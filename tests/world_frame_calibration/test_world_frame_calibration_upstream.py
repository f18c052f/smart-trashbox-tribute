"""world_frame_calibration.upstream のフレーム収集・平均化・型写像の検証
(tasks.md タスク 6.1 / 要件 1.3, 1.6, 1.7, 3.8, 8.1, 8.3, 8.4, 8.5)。

design.md「L0-L2〜L8: 上流接続」節の UpstreamAdapter コンポーネント契約を
固定する。本テストが確認すること:

- `collect_depth` が `sensing_foundation` の公開型を模した最小ダブル
  （`FrameSource` プロトコルを構造的に満たすだけのフェイク。実機・SDK・
  上流の具象アダプタ実装は一切使わない）から、指定枚数のフレームを収集し
  平均化した `DepthImage` を返すこと
- 無効画素（生カウント 0）は平均から除外され、寄与ゼロの画素は `NaN`
  として残ること（0 で埋めない。要件 1.3）
- 生カウントから mm への換算・無効判定が上流 `depth_raw_to_mm` /
  `is_valid_depth` に**実際に委譲されている**こと（`raw * depth_scale_mm`
  や「生値 == 0」を自前で書いていないことの回帰。要件 3.8）
- 平均化が新しい `float64` 配列へ確保され、入力フレームの読み取り専用
  Depth 配列を一切書き換えないこと（要件 1.7）
- 同一のフレーム列から常に同一の `DepthImage` が得られること（要件 8.3）
- `frames_used` が実際に収集したフレーム数を記録すること（要件 1.6）
- カメラ内部パラメータ・ストリーム識別情報が入力フレームの経路（上流
  `StreamProfile`）から取得され、独自の固定値を用いないこと（要件 8.4）
- 収集中にストリーム設定が変化した場合、`CalibrationConfigError` として
  失敗し、異なる設定のフレームを静かに平均へ混ぜないこと
- `frame_count` が不正、または入力元がフレーム列を使い果たした場合に
  `CalibrationConfigError` を送出すること

ファイル名について: `tests/**` に `__init__.py` が無いため、裸の
`test_upstream.py` は使わずプレフィックス付きの
`test_world_frame_calibration_upstream.py` とする（既存タスクの命名規約。
tasks.md 実装ノート・タスク1.6参照）。

タスク 6.2（本ファイルの下半分）で、構造化ロギングへの委譲
（`stage_logger` / `timed` / `timed_apply` / `CALIBRATE_STAGE`）と、
`collect_depth` への `collect` / `failure` イベント送出を追加で検証する
（要件 10.1〜10.5）。
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace

import numpy as np
import pytest
from sensing_foundation import (
    CameraIntrinsics,
    CaptureFrame,
    NullLogger,
    SourceKind,
    StreamProfile,
    TimestampDomain,
)

from world_frame_calibration import upstream
from world_frame_calibration.errors import CalibrationConfigError
from world_frame_calibration.transform import WorldTransform
from world_frame_calibration.types import DepthImage, Intrinsics, StreamSignature

# ---------------------------------------------------------------------------
# 最小ダブル: sensing_foundation の公開型を模しただけの構成要素。
# 実機・SDK・上流の具象アダプタ（SimulatedSource 等）は一切使わない。
# ---------------------------------------------------------------------------


def _camera_intrinsics(**overrides: object) -> CameraIntrinsics:
    kwargs: dict[str, object] = dict(
        width_px=4,
        height_px=2,
        fx_px=500.0,
        fy_px=500.0,
        ppx_px=2.0,
        ppy_px=1.0,
        model="brown_conrady",
        coeffs=(0.0, 0.0, 0.0, 0.0, 0.0),
    )
    kwargs.update(overrides)
    return CameraIntrinsics(**kwargs)  # type: ignore[arg-type]


_UNSET = object()
"""`_stream_profile` の `intrinsics` 引数の「未指定」を表す番人。

`intrinsics=None` は「入力元が内部パラメータを提供できない」という
テスト対象の正当な状態（`StreamProfile.intrinsics` の docstring）を
明示的に表すために使うため、デフォルト引数として `None` を使うと
「未指定」と「明示的な None」を区別できなくなる。そのためデフォルトは
専用の番人オブジェクトにする。
"""


def _stream_profile(
    intrinsics: CameraIntrinsics | None = _UNSET, **overrides: object  # type: ignore[assignment]
) -> StreamProfile:
    intr = _camera_intrinsics() if intrinsics is _UNSET else intrinsics
    kwargs: dict[str, object] = dict(
        width_px=intr.width_px if intr is not None else 4,
        height_px=intr.height_px if intr is not None else 2,
        fps=30,
        depth_scale_mm=2.0,
        color_enabled=False,
        intrinsics=intr,
    )
    kwargs.update(overrides)
    return StreamProfile(**kwargs)  # type: ignore[arg-type]


def _capture_frame(
    *,
    index: int,
    depth: np.ndarray,
    profile: StreamProfile,
    source: SourceKind = SourceKind.SIMULATED,
) -> CaptureFrame:
    return CaptureFrame(
        index=index,
        seq=index,
        t_capture_ms=float(index) * 33.0,
        device_timestamp_ms=None,
        timestamp_domain=TimestampDomain.UNKNOWN,
        capture_latency_ms=None,
        depth=depth,
        profile=profile,
        source=source,
        dropped_before=0,
        gap_before=0,
    )


class _FakeFrameSource:
    """`FrameSource` プロトコルを `frames()` の1メソッドだけ満たすフェイク。

    `collect_depth` は `source.frames()` しか呼ばないため、それ以外の
    プロトコルメンバ（`start` / `stop` / `kind` / `profile` / `stats`）は
    持たない（モジュール docstring「`source` のライフサイクル管理は
    呼び出し側の責務」を参照）。
    """

    def __init__(self, frames: list[CaptureFrame]) -> None:
        self._frames = frames

    def frames(self):
        return iter(self._frames)


def _u16(rows: list[list[int]]) -> np.ndarray:
    return np.array(rows, dtype=np.uint16)


# ---------------------------------------------------------------------------
# to_intrinsics / to_signature: 機械的な写像
# ---------------------------------------------------------------------------


def test_to_intrinsics_maps_profile_intrinsics_field_by_field() -> None:
    intr = _camera_intrinsics(
        width_px=640,
        height_px=480,
        fx_px=615.5,
        fy_px=616.25,
        ppx_px=321.1,
        ppy_px=239.9,
        model="brown_conrady",
        coeffs=(0.1, 0.0, 0.0, 0.0, 0.0),
    )
    profile = _stream_profile(intrinsics=intr)

    result = upstream.to_intrinsics(profile)

    assert isinstance(result, Intrinsics)
    assert result.width_px == intr.width_px
    assert result.height_px == intr.height_px
    assert result.fx_px == intr.fx_px
    assert result.fy_px == intr.fy_px
    assert result.ppx_px == intr.ppx_px
    assert result.ppy_px == intr.ppy_px
    assert result.model == intr.model
    assert result.coeffs == intr.coeffs


def test_to_intrinsics_rejects_missing_intrinsics_as_config_error() -> None:
    """入力元が内部パラメータを提供できない場合、独自の固定値で埋めずに
    CalibrationConfigError で失敗する（要件 8.4）。
    """
    profile = _stream_profile(intrinsics=None)

    with pytest.raises(CalibrationConfigError):
        upstream.to_intrinsics(profile)


def test_to_signature_maps_profile_fields_by_field() -> None:
    profile = _stream_profile(width_px=640, height_px=480, fps=15, depth_scale_mm=0.25, color_enabled=True)

    result = upstream.to_signature(profile)

    assert isinstance(result, StreamSignature)
    assert result.width_px == 640
    assert result.height_px == 480
    assert result.fps == 15
    assert result.depth_scale_mm == 0.25
    assert result.color_enabled is True


# ---------------------------------------------------------------------------
# collect_depth: 単一フレーム
# ---------------------------------------------------------------------------


def test_collect_depth_single_frame_converts_via_upstream_scale() -> None:
    profile = _stream_profile(depth_scale_mm=2.0)
    depth_raw = _u16([[100, 200, 0, 400], [500, 0, 700, 800]])
    frame = _capture_frame(index=0, depth=depth_raw, profile=profile)
    source = _FakeFrameSource([frame])

    result = upstream.collect_depth(source, frame_count=1)

    assert isinstance(result, DepthImage)
    assert result.frames_used == 1
    expected = np.array(
        [[200.0, 400.0, np.nan, 800.0], [1000.0, np.nan, 1400.0, 1600.0]],
        dtype=np.float64,
    )
    np.testing.assert_array_equal(result.depth_mm, expected)
    expected_valid_count = np.array([[1, 1, 0, 1], [1, 0, 1, 1]], dtype=np.int32)
    np.testing.assert_array_equal(result.valid_count, expected_valid_count)
    assert result.source_kind == "simulated"
    assert result.intrinsics == upstream.to_intrinsics(profile)
    assert result.signature == upstream.to_signature(profile)


# ---------------------------------------------------------------------------
# collect_depth: 複数フレームの平均化・無効画素の除外
# ---------------------------------------------------------------------------


def test_collect_depth_averages_multiple_frames_excluding_invalid_pixels() -> None:
    """無効画素は平均から除外され、有効に寄与したフレームだけで平均する
    （要件 1.3, 1.6）。
    """
    profile = _stream_profile(depth_scale_mm=2.0, width_px=2, height_px=2)
    frame1 = _capture_frame(
        index=0, depth=_u16([[100, 0], [200, 300]]), profile=profile
    )
    frame2 = _capture_frame(
        index=1, depth=_u16([[110, 120], [0, 310]]), profile=profile
    )
    source = _FakeFrameSource([frame1, frame2])

    result = upstream.collect_depth(source, frame_count=2)

    assert result.frames_used == 2
    # (0,0): 両フレームとも有効 -> (200+220)/2 = 210
    # (0,1): frame1 無効、frame2 のみ -> 240
    # (1,0): frame1 のみ有効 -> 400
    # (1,1): 両フレームとも有効 -> (600+620)/2 = 610
    expected = np.array([[210.0, 240.0], [400.0, 610.0]], dtype=np.float64)
    np.testing.assert_allclose(result.depth_mm, expected)
    expected_valid_count = np.array([[2, 1], [1, 2]], dtype=np.int32)
    np.testing.assert_array_equal(result.valid_count, expected_valid_count)


def test_collect_depth_pixel_invalid_in_all_frames_remains_nan_not_zero() -> None:
    """全フレームで無効な画素は寄与ゼロのまま NaN（欠測）で残り、
    0 で埋めない（`types.DepthImage` の Postconditions）。
    """
    profile = _stream_profile(depth_scale_mm=1.0, width_px=2, height_px=1)
    frame1 = _capture_frame(index=0, depth=_u16([[0, 50]]), profile=profile)
    frame2 = _capture_frame(index=1, depth=_u16([[0, 60]]), profile=profile)
    source = _FakeFrameSource([frame1, frame2])

    result = upstream.collect_depth(source, frame_count=2)

    assert result.frames_used == 2
    assert np.isnan(result.depth_mm[0, 0])
    assert result.valid_count[0, 0] == 0
    assert result.depth_mm[0, 1] == pytest.approx(55.0)
    assert result.valid_count[0, 1] == 2


# ---------------------------------------------------------------------------
# collect_depth: 上流演算への委譲（自前実装の禁止）の回帰
# ---------------------------------------------------------------------------


def test_collect_depth_delegates_to_upstream_is_valid_depth_and_depth_raw_to_mm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`raw * depth_scale_mm` や「生値 == 0」を自前で書かず、上流
    `depth_raw_to_mm` / `is_valid_depth` を実際に呼んでいることを、
    呼び出しをスパイして固定する（要件 3.8）。
    """
    profile = _stream_profile(depth_scale_mm=2.0, width_px=2, height_px=1)
    depth_raw = _u16([[10, 0]])
    frame = _capture_frame(index=0, depth=depth_raw, profile=profile)
    source = _FakeFrameSource([frame])

    valid_calls: list[int] = []
    convert_calls: list[tuple[int, float]] = []
    real_is_valid_depth = upstream.is_valid_depth
    real_depth_raw_to_mm = upstream.depth_raw_to_mm

    def _spy_is_valid_depth(raw: int) -> bool:
        valid_calls.append(raw)
        return real_is_valid_depth(raw)

    def _spy_depth_raw_to_mm(raw: int, depth_scale_mm: float) -> float:
        convert_calls.append((raw, depth_scale_mm))
        return real_depth_raw_to_mm(raw, depth_scale_mm)

    monkeypatch.setattr(upstream, "is_valid_depth", _spy_is_valid_depth)
    monkeypatch.setattr(upstream, "depth_raw_to_mm", _spy_depth_raw_to_mm)

    result = upstream.collect_depth(source, frame_count=1)

    assert sorted(valid_calls) == [0, 10]
    # 無効画素（raw=0）は depth_raw_to_mm へ渡さない。
    assert convert_calls == [(10, 2.0)]
    assert result.depth_mm[0, 0] == pytest.approx(20.0)
    assert np.isnan(result.depth_mm[0, 1])


# ---------------------------------------------------------------------------
# collect_depth: 入力 Depth バッファの非破壊
# ---------------------------------------------------------------------------


def test_collect_depth_never_mutates_input_depth_buffers() -> None:
    """入力フレームの Depth バッファは平均化後も変更されない（要件 1.7）。

    平均化は新しい float64 配列へ行い、上流の読み取り専用 Depth
    （`writeable=False`）を書き換えない。
    """
    profile = _stream_profile(depth_scale_mm=1.0, width_px=2, height_px=2)
    depth1 = _u16([[10, 20], [30, 40]])
    depth2 = _u16([[15, 25], [35, 45]])
    original1 = depth1.copy()
    original2 = depth2.copy()
    frame1 = _capture_frame(index=0, depth=depth1, profile=profile)
    frame2 = _capture_frame(index=1, depth=depth2, profile=profile)

    # sensing_foundation.CaptureFrame は構築時に depth を読み取り専用化する。
    assert frame1.depth.flags.writeable is False
    assert frame2.depth.flags.writeable is False

    source = _FakeFrameSource([frame1, frame2])
    result = upstream.collect_depth(source, frame_count=2)

    np.testing.assert_array_equal(frame1.depth, original1)
    np.testing.assert_array_equal(frame2.depth, original2)
    assert result.depth_mm is not frame1.depth
    assert result.depth_mm is not frame2.depth
    assert result.depth_mm.dtype == np.float64


# ---------------------------------------------------------------------------
# collect_depth: 決定性（同一入力に同一結果）
# ---------------------------------------------------------------------------


def test_collect_depth_is_deterministic_for_the_same_frame_sequence() -> None:
    """同一内容のフレーム列から常に同一の DepthImage が得られる（要件 8.3）。"""

    def _build_source() -> _FakeFrameSource:
        profile = _stream_profile(depth_scale_mm=1.5, width_px=2, height_px=2)
        frames = [
            _capture_frame(index=0, depth=_u16([[10, 0], [30, 40]]), profile=profile),
            _capture_frame(index=1, depth=_u16([[12, 22], [0, 42]]), profile=profile),
        ]
        return _FakeFrameSource(frames)

    result_a = upstream.collect_depth(_build_source(), frame_count=2)
    result_b = upstream.collect_depth(_build_source(), frame_count=2)

    np.testing.assert_array_equal(result_a.depth_mm, result_b.depth_mm)
    np.testing.assert_array_equal(result_a.valid_count, result_b.valid_count)
    assert result_a.frames_used == result_b.frames_used
    assert result_a.intrinsics == result_b.intrinsics
    assert result_a.signature == result_b.signature
    assert result_a.source_kind == result_b.source_kind


# ---------------------------------------------------------------------------
# collect_depth: 縮退・誤り入力
# ---------------------------------------------------------------------------


def test_collect_depth_rejects_frame_count_below_one() -> None:
    source = _FakeFrameSource([])

    with pytest.raises(CalibrationConfigError):
        upstream.collect_depth(source, frame_count=0)


def test_collect_depth_rejects_exhausted_frame_source() -> None:
    profile = _stream_profile()
    frame = _capture_frame(index=0, depth=_u16([[10, 20]]), profile=profile)
    source = _FakeFrameSource([frame])  # 1枚しか供給できない

    with pytest.raises(CalibrationConfigError):
        upstream.collect_depth(source, frame_count=3)


def test_collect_depth_rejects_stream_configuration_change_mid_collection() -> None:
    """収集中にストリーム設定（ここでは fps）が変化した場合、異なる設定の
    フレームを静かに平均へ混ぜず CalibrationConfigError で失敗する。
    """
    profile_a = _stream_profile(fps=30)
    profile_b = replace(profile_a, fps=15)
    frame1 = _capture_frame(index=0, depth=_u16([[10, 20]]), profile=profile_a)
    frame2 = _capture_frame(index=1, depth=_u16([[30, 40]]), profile=profile_b)
    source = _FakeFrameSource([frame1, frame2])

    with pytest.raises(CalibrationConfigError):
        upstream.collect_depth(source, frame_count=2)


def test_collect_depth_rejects_stream_configuration_change_in_intrinsics() -> None:
    """内部パラメータ（焦点距離）が変化した場合も設定変化として拒否する。"""
    intr_a = _camera_intrinsics(fx_px=500.0)
    intr_b = _camera_intrinsics(fx_px=550.0)
    profile_a = _stream_profile(intrinsics=intr_a)
    profile_b = _stream_profile(intrinsics=intr_b)
    frame1 = _capture_frame(index=0, depth=_u16([[10, 20]]), profile=profile_a)
    frame2 = _capture_frame(index=1, depth=_u16([[30, 40]]), profile=profile_b)
    source = _FakeFrameSource([frame1, frame2])

    with pytest.raises(CalibrationConfigError):
        upstream.collect_depth(source, frame_count=2)


# =============================================================================
# タスク 6.2: 計測点とロギング委譲
# (tasks.md タスク 6.2 / 要件 10.1, 10.2, 10.3, 10.4, 10.5)
#
# 本節が確認すること:
# - `CALIBRATE_STAGE` が `"calibrate"` であり、上流の実際の
#   `RESERVED_STAGES`（`system` / `capture` / `record`）と衝突しないこと
#   （ハードコードした推測ではなく実際の定数に対して確認する）
# - `stage_logger` / `timed` が上流 `Logger.stage()` / `Logger.timed()` へ
#   段階名 `calibrate` を添えて薄く委譲するだけであること（`logger=None` も
#   安全に扱う）
# - `timed_apply` が `WorldTransform.apply` へ実際に委譲し（自前で行列積を
#   書き直さない）、その適用区間を計測委譲すること。`transform.py` 自体は
#   一切変更しない
# - `collect_depth` がロギング有効時に `collect` イベント（設計どおりの
#   キー）を送出し、失敗時も `failure` イベントを送出してから例外を
#   送出すること（成功時しかログが残らない状態を作らない）
# - ロギングが無効（`logger=None` または上流の無効な `NullLogger`）な
#   場合、`valid_ratio` の算出という計測値の生成そのものを行わないこと
#   （出力の抑制ではなく計算自体のスキップであることを、算出関数への
#   呼び出し回数をスパイして固定する）
#
# ここでも task 6.1 と同じ方針（実機・SDK・上流の具象ロガー実装
# `StructuredLogger` は使わず、`Logger` プロトコルを構造的に満たすだけの
# 記録専用ダブルを用いる）を踏襲する。
# =============================================================================


class _SpyStageLogger:
    """`Logger.stage(name)` の戻り値（`StageLogger`）を模した記録専用ダブル。

    `sensing_foundation.obslog.StageLogger` は公開入口（`__all__`）に
    含まれないため型として import しない。`upstream.stage_logger` の
    戻り値がこのダブルと構造的に同じ使い方（`emit` / `timed`）をする
    ことをテストの側から検証する。
    """

    def __init__(self, parent: "_SpyLogger", stage: str) -> None:
        self._parent = parent
        self._stage = stage

    def emit(self, event: str, /, **data: object) -> None:
        self._parent.emit(self._stage, event, **data)

    @contextmanager
    def timed(self, event: str, /, **data: object):
        self._parent.timed_calls.append((self._stage, event, dict(data)))
        yield


class _SpyLogger:
    """`sensing_foundation.Logger` プロトコルを満たす記録専用ダブル。

    実際の I/O・ファイル書き込み・スレッドは一切行わない
    （`StructuredLogger` は使わない）。`enabled` を切り替えて「ロギング
    無効」を模擬できる。
    """

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self.emitted: list[tuple[str, str, dict[str, object]]] = []
        self.timed_calls: list[tuple[str, str, dict[str, object]]] = []

    def emit(self, stage: str, event: str, /, **data: object) -> None:
        self.emitted.append((stage, event, dict(data)))

    def stage(self, stage: str) -> _SpyStageLogger:
        return _SpyStageLogger(self, stage)

    @contextmanager
    def timed(self, stage: str, event: str, /, **data: object):
        self.timed_calls.append((stage, event, dict(data)))
        yield

    def stats(self):  # pragma: no cover - collect_depth/timed_apply は呼ばない
        raise NotImplementedError

    def close(self, timeout_ms: float = 1000.0) -> None:  # pragma: no cover
        return None


# ---------------------------------------------------------------------------
# CALIBRATE_STAGE: 予約段階名との非衝突
# ---------------------------------------------------------------------------


def test_calibrate_stage_is_the_literal_string_calibrate() -> None:
    assert upstream.CALIBRATE_STAGE == "calibrate"


def test_calibrate_stage_does_not_collide_with_upstream_reserved_stages() -> None:
    """予約段階名（`system` / `capture` / `record`）と衝突しないことを、
    ハードコードした推測ではなく `sensing_foundation` の実際の
    `RESERVED_STAGES` 定数に対して確認する（tasks.md タスク 6.2）。

    `RESERVED_STAGES` は `sensing_foundation` の公開入口（`__all__`）には
    含まれない（`sensing_foundation/__init__.py` docstring
    「含めなかったもの」を参照）。本番コード（`upstream.py`）はこれを
    import しないが、本テストは実際の定数に対して固定するために内部
    モジュールから直接読む。
    """
    from sensing_foundation.obslog import RESERVED_STAGES

    assert upstream.CALIBRATE_STAGE not in RESERVED_STAGES


# ---------------------------------------------------------------------------
# stage_logger / timed: 上流 Logger への薄い委譲
# ---------------------------------------------------------------------------


def test_stage_logger_binds_calibrate_stage_and_delegates_emit() -> None:
    spy = _SpyLogger()

    bound = upstream.stage_logger(spy)
    bound.emit("something", key=1)

    assert spy.emitted == [("calibrate", "something", {"key": 1})]


def test_stage_logger_with_none_logger_is_a_safe_noop() -> None:
    """`logger=None` は上流の `NullLogger` へ正規化され、例外にならない。"""
    bound = upstream.stage_logger(None)

    bound.emit("something", key=1)
    with bound.timed("something_else"):
        pass


def test_timed_delegates_to_logger_timed_with_calibrate_stage() -> None:
    spy = _SpyLogger()

    with upstream.timed(spy, "apply", key="value"):
        pass

    assert spy.timed_calls == [("calibrate", "apply", {"key": "value"})]


def test_timed_with_none_logger_returns_a_working_context_manager() -> None:
    entered = False
    with upstream.timed(None, "apply"):
        entered = True

    assert entered is True


# ---------------------------------------------------------------------------
# timed_apply: WorldTransform.apply への外部からの薄い計測委譲。
# transform.py 自体は変更しない（tasks.md タスク 2.2 Critical constraint）。
# ---------------------------------------------------------------------------


def _identity_transform() -> WorldTransform:
    return WorldTransform(
        rotation=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        translation_mm=(10.0, 20.0, 30.0),
    )


def test_timed_apply_returns_the_same_result_as_calling_apply_directly() -> None:
    transform = _identity_transform()
    points = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]], dtype=np.float64)

    result = upstream.timed_apply(None, transform, points)

    np.testing.assert_array_equal(result, transform.apply(points))


def test_timed_apply_emits_a_timed_apply_event_under_calibrate_stage() -> None:
    spy = _SpyLogger()
    transform = _identity_transform()
    points = np.array([[1.0, 2.0, 3.0]], dtype=np.float64)

    upstream.timed_apply(spy, transform, points)

    assert len(spy.timed_calls) == 1
    stage, event, _data = spy.timed_calls[0]
    assert stage == "calibrate"
    assert event == "apply"


def test_timed_apply_delegates_to_worldtransform_apply_without_reimplementing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`timed_apply` が行列演算を自前で書き直さず、`WorldTransform.apply` へ
    実際に委譲していることをスパイで固定する（`transform.py` 未変更の裏付け）。
    """
    transform = _identity_transform()
    points = np.array([[1.0, 2.0, 3.0]], dtype=np.float64)
    calls: list[object] = []
    real_apply = WorldTransform.apply

    def _spy_apply(self: WorldTransform, points_camera_mm: np.ndarray) -> np.ndarray:
        calls.append(points_camera_mm)
        return real_apply(self, points_camera_mm)

    monkeypatch.setattr(WorldTransform, "apply", _spy_apply)

    upstream.timed_apply(None, transform, points)

    assert len(calls) == 1


# ---------------------------------------------------------------------------
# collect_depth へのロギング統合: collect イベントと failure イベント
# ---------------------------------------------------------------------------


def test_collect_depth_emits_collect_event_with_designed_keys_when_logger_enabled() -> None:
    profile = _stream_profile(depth_scale_mm=2.0, width_px=2, height_px=1)
    frame = _capture_frame(index=0, depth=_u16([[10, 0]]), profile=profile)
    source = _FakeFrameSource([frame])
    spy = _SpyLogger(enabled=True)

    upstream.collect_depth(source, frame_count=1, logger=spy)

    assert len(spy.emitted) == 1
    stage, event, data = spy.emitted[0]
    assert stage == "calibrate"
    assert event == "collect"
    assert data["frames_requested"] == 1
    assert data["frames_used"] == 1
    assert data["valid_ratio"] == pytest.approx(0.5)
    assert "duration_ms" in data
    assert data["duration_ms"] >= 0.0


def test_collect_depth_with_none_logger_does_not_compute_valid_ratio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ロギングが無効（`logger=None`）な場合、計測値（`valid_ratio`）の
    生成そのものを行わない（要件 10.5）。「渡された値を使わない」だけで
    なく、値を計算する関数自体が呼ばれないことを固定する。
    """
    calls: list[object] = []

    def _spy_valid_ratio(valid_count: np.ndarray) -> float:
        calls.append(valid_count)
        return 0.0

    monkeypatch.setattr(upstream, "_valid_ratio", _spy_valid_ratio)

    profile = _stream_profile(depth_scale_mm=1.0, width_px=2, height_px=1)
    frame = _capture_frame(index=0, depth=_u16([[10, 20]]), profile=profile)
    source = _FakeFrameSource([frame])

    upstream.collect_depth(source, frame_count=1, logger=None)

    assert calls == []


def test_collect_depth_with_disabled_null_logger_does_not_compute_valid_ratio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`logger=None` だけでなく、上流の無効な `NullLogger`（`enabled=False`）
    を明示的に渡した場合も同様に計測値の生成そのものを行わないことを固定する。
    """
    calls: list[object] = []

    def _spy_valid_ratio(valid_count: np.ndarray) -> float:
        calls.append(valid_count)
        return 0.0

    monkeypatch.setattr(upstream, "_valid_ratio", _spy_valid_ratio)

    profile = _stream_profile(depth_scale_mm=1.0, width_px=2, height_px=1)
    frame = _capture_frame(index=0, depth=_u16([[10, 20]]), profile=profile)
    source = _FakeFrameSource([frame])

    upstream.collect_depth(source, frame_count=1, logger=NullLogger())

    assert calls == []


def test_collect_depth_with_enabled_logger_does_compute_valid_ratio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """対照テスト: ロギングが有効なら `_valid_ratio` が実際に呼ばれる。"""
    calls: list[object] = []
    real_valid_ratio = upstream._valid_ratio

    def _spy_valid_ratio(valid_count: np.ndarray) -> float:
        calls.append(valid_count)
        return real_valid_ratio(valid_count)

    monkeypatch.setattr(upstream, "_valid_ratio", _spy_valid_ratio)

    profile = _stream_profile(depth_scale_mm=1.0, width_px=2, height_px=1)
    frame = _capture_frame(index=0, depth=_u16([[10, 20]]), profile=profile)
    source = _FakeFrameSource([frame])

    upstream.collect_depth(source, frame_count=1, logger=_SpyLogger(enabled=True))

    assert len(calls) == 1


def test_collect_depth_emits_failure_event_before_raising_on_exhausted_source() -> None:
    """成功時しかログが残らない状態を作らない: フレーム収集が失敗しても
    `failure` イベントを送出してから例外を送出する。
    """
    profile = _stream_profile()
    frame = _capture_frame(index=0, depth=_u16([[10, 20]]), profile=profile)
    source = _FakeFrameSource([frame])  # 1枚しか供給できない
    spy = _SpyLogger(enabled=True)

    with pytest.raises(CalibrationConfigError):
        upstream.collect_depth(source, frame_count=3, logger=spy)

    assert len(spy.emitted) == 1
    stage, event, data = spy.emitted[0]
    assert stage == "calibrate"
    assert event == "failure"
    assert "exhausted" in data["detail"]


def test_collect_depth_emits_failure_event_on_stream_configuration_change() -> None:
    profile_a = _stream_profile(fps=30)
    profile_b = replace(profile_a, fps=15)
    frame1 = _capture_frame(index=0, depth=_u16([[10, 20]]), profile=profile_a)
    frame2 = _capture_frame(index=1, depth=_u16([[30, 40]]), profile=profile_b)
    source = _FakeFrameSource([frame1, frame2])
    spy = _SpyLogger(enabled=True)

    with pytest.raises(CalibrationConfigError):
        upstream.collect_depth(source, frame_count=2, logger=spy)

    assert len(spy.emitted) == 1
    assert spy.emitted[0][1] == "failure"


def test_collect_depth_with_none_logger_still_raises_on_failure() -> None:
    """`logger=None` でも例外送出そのものは変わらない
    （ロギングの有無が処理そのものの成否を左右しない）。
    """
    source = _FakeFrameSource([])

    with pytest.raises(CalibrationConfigError):
        upstream.collect_depth(source, frame_count=0, logger=None)


def test_collect_depth_without_logger_argument_still_works_backward_compatibly() -> None:
    """`logger` はキーワード専用の既定 `None` であり、既存呼び出し
    （タスク 6.1 の全テスト）が引き続き動作すること（後方互換）。
    """
    profile = _stream_profile(depth_scale_mm=2.0)
    depth_raw = _u16([[100, 200, 0, 400], [500, 0, 700, 800]])
    frame = _capture_frame(index=0, depth=depth_raw, profile=profile)
    source = _FakeFrameSource([frame])

    result = upstream.collect_depth(source, frame_count=1)

    assert result.frames_used == 1
