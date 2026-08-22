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

本タスク（6.1）のスコープ外: 構造化ロギングへの委譲（`stage_logger` /
`timed` / `timed_apply` / `CALIBRATE_STAGE`）はタスク 6.2 で別ファイルへ
追加されるため、ここでは検証しない（`upstream.py` モジュール docstring
「本タスク（6.1）のスコープ外」を参照）。
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest
from sensing_foundation import (
    CameraIntrinsics,
    CaptureFrame,
    SourceKind,
    StreamProfile,
    TimestampDomain,
)

from world_frame_calibration import upstream
from world_frame_calibration.errors import CalibrationConfigError
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
