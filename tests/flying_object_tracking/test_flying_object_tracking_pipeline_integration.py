"""既知軌道の往復・非破壊・決定性を結合テストで固定する（タスク 6.3）。

design.md「Testing Strategy → Integration Tests」の `test_pipeline.py` /
`test_readonly_depth.py` / `test_determinism.py` が定める役割（design.md の
Directory Structure はこれらを別ファイルに分けるが、本 Spec の命名規約
——タスク 1.5 の Implementation Notes 参照——により本ファイル1つへ集約する。
タスク 6.1 の `test_flying_object_tracking_pipeline.py`（`TrackingPipeline`
の単体レベルの例外・合算・mask_px 挙動）とは対象が異なる、検出 → 逆投影 →
追跡を貫く**結合レベル**のテストである）と、タスク 6.3 の本文（verbatim）
が要求する5項目を固定する:

1. 既知の3D軌道 → 合成フレーム列 → パイプライン → 点列の往復（許容誤差内）
2. 3方式（`DEPTH_BAND` / `FRAME_DIFF` / `BACKGROUND`）すべてで同一の合成
   軌道が復元できること（方式ごとの実装差の検出）
3. 読み取り専用の距離配列を流し、全経路が例外なく完走すること
4. 同一の合成セッションを2回処理して同一の点列が得られること
5. 入力元の種別だけを変えた同一内容の入力でも同一結果になること

Requirements: 1.2, 1.3, 1.4, 11.1, 11.2, 11.3

## 合成軌道の構成（3方式すべてに公平な機会を与える）

`frame_diff` は直前フレームとの差分が必要なため最低2フレーム、
`background` は `background_frames` 回の初期化完了（`ready`）が必要
（tasks.md 2.3 / 2.4）。本ファイルは全ケース共通で:

- **静止ウォームアップ区間**（`WARMUP_LEN` フレーム）: 軌道上の物体を
  意図的に **z 座標を静止背景と同じ raw 値になる距離**（`BACKGROUND_RAW`
  と等しい mm 値）に置く。`depth_scale_mm=1.0` の下ではこれは
  「物体を描画してもその raw 値が背景そのものと完全に一致し、結果として
  フレーム全体が一様な背景depthになる」ことを意味する。したがって
  `depth_band`（z 帯の外なので前景ゼロ）・`frame_diff`（連続フレームが
  ビット単位で同一なので差分ゼロ）・`background`（初期化中は内容に関わらず
  前景ゼロを返す契約。tasks.md 2.4）のいずれにとっても、この区間は
  「候補ゼロ」に確実に一致する、実際に描画された合成フレームでの
  ウォームアップになる。手書きの `CaptureFrame` を別途組み立てる必要が
  無い（`synthetic_module.synthesize_frames` を1回呼ぶだけで済む）
- **移動区間**（`MOVING_LEN` フレーム）: 対象物が z 帯内（`BLOB_Z_MM`）を
  x 方向へ等速で移動する、既知の3D軌道

`SingleObjectTracker` は `IDLE` 状態では欠測カウンタを進めない
（`tracking.py` の設計判断1）。したがってウォームアップ区間の長さは
`max_missing_frames` の値に関わらず追跡の生死に影響しない。

## `frame_diff` の位置バイアスについて（実測に基づく設計判断）

`frame_diff` の前景マスクは「直前フレームとの差分」であるため、対象物が
移動するフレームでは前景領域が「新しく物体に覆われた画素」と「直前まで
物体に覆われていて今は覆われていない画素」の和集合（三日月状の2領域）に
なる（`test_flying_object_tracking_frame_diff_mask.py` が単体で固定する
挙動）。本ファイルは実際に本結合経路（検出 → 候補化 → 逆投影）を通した
実測（開発中にスクリプトで確認済み。本ファイル自体は理論値を計算せず、
観測される性質だけを固定する）により、次の**安定した**性質を確認した:

- ウォームアップ最終フレーム → 移動区間の最初のフレームへの遷移
  （物体が「何もない背景」から出現する）では、前景領域が新しい物体の
  disk 全体そのものになるため、復元位置は**真の位置と厳密に一致**する
- 移動区間の2フレーム目以降（物体が物体自身の直前位置と重なりながら
  移動する）では、三日月状の前景領域の重心は直前フレームと現在フレームの
  中間に系統的に偏る。この偏りは等速直線運動の下では**フレームごとに
  一定**であり（同一の並進量・同一の形状が繰り返されるため）、結果として
  「復元された x 座標のフレーム間の差分」は真の1フレームあたり移動量
  （`STEP_MM`）と一致する

したがって本ファイルは `frame_diff` について、(a) 移動区間の最初の点が
真の位置と一致すること、(b) 全区間で y・z が真値と一致すること、
(c) 定常区間（移動区間の2点目以降。最初の点から2点目への遷移は
「バイアス無し → 定常バイアス」の変わり目そのものであり定常差分と一致
しないため対象から除く）で連続する復元点の x 座標の差分が `STEP_MM` と
一致すること、の3点で「軌道が許容誤差内で復元される」ことを検証する。
これは特定の画素配置に依存する固定オフセット値をハードコードするより
頑健であり、かつ検出・逆投影・追跡のいずれかが壊れれば（符号反転・
スケール誤り・軸入れ替え・
候補の取りこぼし等）確実に失敗する、実質的な回帰検出力を持つ。
"""

from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path

import pytest
from sensing_foundation import (
    CameraIntrinsics,
    CaptureMetrics,
    CaptureStats,
    FrameSource,
    NullLogger,
    RuntimeSettings,
    SessionClock,
    SessionRecorder,
    SourceKind,
    StreamProfile,
    open_source,
)

from flying_object_tracking.config import (
    DetectorConfig,
    MeasurementConfig,
    ObjectModelConfig,
    ProjectionConfig,
    RoiConfig,
    TrackerConfig,
    TrackingSettings,
)
from flying_object_tracking.pipeline import TrackingPipeline, track_source
from flying_object_tracking.types import (
    CameraPoint,
    CameraTrack,
    DetectorKind,
    TrackEndReason,
    TrackPoint,
    TrackState,
    TrackUpdate,
)

DIAMETER_MM = 65.0
BLOB_Z_MM = 1000.0  # 既定 z 帯 [500, 5000] の内側
BACKGROUND_RAW = 6000  # 既定 z 帯の外側（他ファイルと同じ規約）
WIDTH_PX = 64
HEIGHT_PX = 64
FX_PX = 200.0
FY_PX = 200.0

STEP_MM = 15.0
WARMUP_LEN = 4
MOVING_LEN = 8

TOL_MM = 0.5
"""往復で許容する誤差（mm）。本ファイルの幾何条件では実測値が真値と
厳密に一致する（浮動小数点誤差のみ）ため、小さく閉じたタイトな値で
十分（deprojection_roundtrip テストの `TOL_MM=1.0` と同系統の考え方）。"""

_DETECTOR_KINDS = (DetectorKind.DEPTH_BAND, DetectorKind.FRAME_DIFF, DetectorKind.BACKGROUND)


# ==============================================================================
# ドメイン値のビルダ（他テストファイルとの bare import 衝突を避けるため
# このファイル内に自己完結させる。既存の task 6.1/6.2 テストファイルと
# 同じ流儀）
# ==============================================================================


def _intrinsics() -> CameraIntrinsics:
    return CameraIntrinsics(
        width_px=WIDTH_PX,
        height_px=HEIGHT_PX,
        fx_px=FX_PX,
        fy_px=FY_PX,
        ppx_px=WIDTH_PX / 2.0,
        ppy_px=HEIGHT_PX / 2.0,
        model="none",
        coeffs=(0.0, 0.0, 0.0, 0.0, 0.0),
    )


def _profile() -> StreamProfile:
    return StreamProfile(
        width_px=WIDTH_PX,
        height_px=HEIGHT_PX,
        fps=30,
        depth_scale_mm=1.0,
        color_enabled=False,
        intrinsics=_intrinsics(),
    )


def _settings(kind: DetectorKind) -> TrackingSettings:
    """`kind` だけが異なる、それ以外は全方式で同一の後段設定（要件 4.4 の精神）。

    `open_kernel_px=0` / `close_kernel_px=0` で形態処理を無効化する
    （既存テストと同じ理由: 候補形状を合成器の描画そのものに保ち、往復の
    許容誤差を形態処理由来のノイズで汚さないため）。
    """
    return TrackingSettings(
        roi=RoiConfig(z_min_mm=500.0, z_max_mm=5000.0),
        object_model=ObjectModelConfig(diameter_mm=DIAMETER_MM),
        detector=DetectorConfig(
            kind=kind,
            diff_threshold_mm=80.0,
            background_frames=WARMUP_LEN,
            background_update_rate=0.0,
            open_kernel_px=0,
            close_kernel_px=0,
        ),
        projection=ProjectionConfig(),
        tracker=TrackerConfig(max_step_mm=900.0, max_missing_frames=3, min_points_to_start=1),
        measurement=MeasurementConfig(enabled=False),
    )


def _full_trajectory(synthetic_module) -> list:
    """ウォームアップ区間 + 移動区間からなる、既知の3D軌道全体を返す。

    モジュール docstring「合成軌道の構成」参照。ウォームアップ区間の
    z 座標は `BACKGROUND_RAW`（raw カウント）と数値的に等しい mm 値
    ——`depth_scale_mm=1.0` なので `raw_from_z_mm(BACKGROUND_RAW_MM, 1.0)
    == BACKGROUND_RAW`——にすることで、描画された「物体」が静止背景と
    完全に同値になり、実質的に「物体の無いフレーム」として振る舞う。
    """
    TrajectoryPoint = synthetic_module.TrajectoryPoint
    points = []
    t_ms = 0.0
    for _ in range(WARMUP_LEN):
        points.append(TrajectoryPoint(t_ms=t_ms, x_mm=0.0, y_mm=0.0, z_mm=float(BACKGROUND_RAW)))
        t_ms += 33.0
    for i in range(MOVING_LEN):
        points.append(TrajectoryPoint(t_ms=t_ms, x_mm=float(i) * STEP_MM, y_mm=0.0, z_mm=BLOB_Z_MM))
        t_ms += 33.0
    return points


def _moving_trajectory_xyz() -> list[tuple[float, float, float]]:
    """移動区間だけの真の (x_mm, y_mm, z_mm) 列（ウォームアップを含まない）。"""
    return [(float(i) * STEP_MM, 0.0, BLOB_Z_MM) for i in range(MOVING_LEN)]


def _synthesize(synthetic_module) -> list:
    trajectory = _full_trajectory(synthetic_module)
    return synthetic_module.synthesize_frames(
        trajectory, _profile(), DIAMETER_MM, background_raw=BACKGROUND_RAW
    )


def _record_session(root: Path, frames: list) -> Path:
    """`frames` を `SessionRecorder` でそのまま書き出し、セッションディレクトリを返す。

    `test_flying_object_tracking_track_source.py`（タスク 6.2）と同じ手順
    （bare import を避けるため本ファイル内に自己完結させる）。
    """
    session_id = "pipeline-integration-session"
    recorder = SessionRecorder(
        root,
        session_id,
        _profile(),
        device=None,
        runtime={},
        source=SourceKind.SIMULATED,
    )
    with recorder:
        for frame in frames:
            recorder.write(frame)
        recorder.close(
            CaptureStats(
                frames_yielded=len(frames),
                frames_dropped=0,
                frames_missing=0,
                duration_ms=0.0,
                measured_fps=0.0,
                acquire_errors=0,
            )
        )
    return root / session_id


def _open_recorded_source(session_dir: Path) -> FrameSource:
    runtime_settings = RuntimeSettings(source=SourceKind.RECORDED, session_path=session_dir)
    clock = SessionClock(session_id="pipeline-integration-recorded-replay")
    metrics = CaptureMetrics(NullLogger(), clock, sysstat=None)
    return open_source(runtime_settings, metrics, clock=clock)


def _normalize_point(point: CameraPoint) -> CameraPoint:
    """`t_ms` を比較対象から除外する（`SessionRecorder` 往復で `t_capture_ms`
    が再生セッション独自の `SessionClock` から算出し直されるため。
    `test_flying_object_tracking_track_source.py` と同じ理由）。"""
    return replace(point, t_ms=0.0)


def _normalize_track_point(track_point: TrackPoint | None) -> TrackPoint | None:
    if track_point is None:
        return None
    return replace(track_point, point=_normalize_point(track_point.point))


def _normalize_track(track: CameraTrack) -> CameraTrack:
    return replace(
        track,
        started_t_ms=0.0,
        source=SourceKind.SIMULATED,
        points=tuple(_normalize_track_point(p) for p in track.points),
    )


def _normalize_update(update: TrackUpdate) -> TrackUpdate:
    return replace(
        update,
        track=_normalize_track(update.track),
        appended=_normalize_track_point(update.appended),
    )


def _kind_id(kind: DetectorKind) -> str:
    return kind.value


# ==============================================================================
# 1. 既知軌道の往復（3方式すべて）
# ==============================================================================


class TestRoundTripAccuracy:
    """既知の3D軌道 → 合成フレーム列 → パイプライン → 点列の往復（タスク本文1・2）。"""

    @pytest.mark.parametrize("kind", _DETECTOR_KINDS, ids=_kind_id)
    def test_known_trajectory_is_recovered_within_tolerance(self, synthetic_module, kind) -> None:
        frames = _synthesize(synthetic_module)
        settings = _settings(kind)
        pipeline = TrackingPipeline(settings, NullLogger())

        updates = [pipeline.process(frame) for frame in frames]
        track = pipeline.finish()

        # ウォームアップ区間では候補が一切出ない（モジュール docstring
        # 「合成軌道の構成」参照。3方式共通で成立するはず）。
        warmup_updates = updates[:WARMUP_LEN]
        assert all(u.appended is None for u in warmup_updates), [
            (u.candidates, u.appended) for u in warmup_updates
        ]
        assert all(u.candidates == 0 for u in warmup_updates)

        assert track.state == TrackState.ENDED
        assert track.end_reason == TrackEndReason.SOURCE_END
        assert len(track.points) == MOVING_LEN, [
            (u.candidates, u.appended) for u in updates
        ]

        recovered = [tp.point for tp in track.points]
        true_moving = _moving_trajectory_xyz()

        if kind in (DetectorKind.DEPTH_BAND, DetectorKind.BACKGROUND):
            # この2方式は各フレームの候補領域がそのまま対象物の disk その
            # ものであり、位置バイアスが生じない（モジュール docstring
            # 「frame_diff の位置バイアスについて」の対比参照）。
            for point, (x_mm, y_mm, z_mm) in zip(recovered, true_moving, strict=True):
                assert math.isclose(point.x_mm, x_mm, abs_tol=TOL_MM)
                assert math.isclose(point.y_mm, y_mm, abs_tol=TOL_MM)
                assert math.isclose(point.z_mm, z_mm, abs_tol=TOL_MM)
        else:
            assert kind is DetectorKind.FRAME_DIFF
            # (a) 移動区間の最初の点は真の位置と一致する（背景から物体が
            #     新規出現するフレームであり、三日月バイアスが生じない）。
            first_x, first_y, first_z = true_moving[0]
            assert math.isclose(recovered[0].x_mm, first_x, abs_tol=TOL_MM)
            assert math.isclose(recovered[0].y_mm, first_y, abs_tol=TOL_MM)
            assert math.isclose(recovered[0].z_mm, first_z, abs_tol=TOL_MM)

            # (b) 全区間で y・z は真値と一致する（バイアスは移動方向 x に
            #     のみ生じる。モジュール docstring 参照）。
            for point, (_, y_mm, z_mm) in zip(recovered, true_moving, strict=True):
                assert math.isclose(point.y_mm, y_mm, abs_tol=TOL_MM)
                assert math.isclose(point.z_mm, z_mm, abs_tol=TOL_MM)

            # (c) 連続する復元点の x 座標の差分は真の1フレームあたり
            #     移動量と一致する（等速運動が復元されている）。ただし
            #     recovered[0]→recovered[1] の差分は「バイアス無し(a) →
            #     定常バイアス」への遷移そのものであり定常値と一致しない
            #     （モジュール docstring 参照）。定常区間（recovered[1:]）
            #     の差分だけを検証する。
            deltas = [
                recovered[i + 1].x_mm - recovered[i].x_mm for i in range(1, len(recovered) - 1)
            ]
            for delta in deltas:
                assert math.isclose(delta, STEP_MM, abs_tol=TOL_MM), deltas


# ==============================================================================
# 2. 読み取り専用の距離配列に対する非破壊完走（タスク本文3）
# ==============================================================================


class TestReadOnlyDepthNonDestructive:
    """`CaptureFrame.depth` が読み取り専用でも全経路が例外なく完走することを固定する。

    `CaptureFrame.__post_init__` は常に `depth` を読み取り専用化する
    （タスクブリーフの前提）ため、本テストの役割は「実際に読み取り専用
    である前提を明示的に確認したうえで」検出 → 逆投影 → 追跡の全段が
    書き込みを一切試みないことを完走そのもので証明する回帰ガードである。
    破壊的な in-place 変更が実装へ混入すれば `ValueError`（NumPy の
    read-only 配列への書き込み例外）でテストが即座に失敗する。
    """

    @pytest.mark.parametrize("kind", _DETECTOR_KINDS, ids=_kind_id)
    def test_full_pipeline_completes_without_writing_to_readonly_depth(
        self, synthetic_module, kind
    ) -> None:
        frames = _synthesize(synthetic_module)
        for frame in frames:
            assert frame.depth.flags.writeable is False, (
                "前提が崩れている: 合成フレームの depth が読み取り専用でない"
                "（このテストの意味が成立しない）"
            )

        settings = _settings(kind)
        pipeline = TrackingPipeline(settings, NullLogger())

        # 書き込みを試みる実装が混入していれば、ここで ValueError が発生する。
        # ただし TrackingPipeline.process() は要件10.2により1フレームの
        # 予期しない例外を内部で捕捉するため、ValueError はここでは伝播せず、
        # そのフレームが候補ゼロとして扱われる形で下の点数アサーションに現れる
        # （レビューで実測確認済み。試しに depth_band.py へ破壊的書き込みを
        # 注入すると本テストは実際に失敗する）。
        updates = [pipeline.process(frame) for frame in frames]
        track = pipeline.finish()

        assert len(updates) == len(frames)
        assert track.state == TrackState.ENDED
        # 非破壊であることに加え、実際に検出・追跡が機能したことも確認する
        # （自明に何もしていないだけで「例外が出ない」わけではないことの
        # 証拠）。
        assert len(track.points) == MOVING_LEN


# ==============================================================================
# 3. 決定性: 同一の合成セッションを2回処理して同一の点列（タスク本文4）
# ==============================================================================


class TestDeterminism:
    """同一の合成セッションを2回処理して同一の点列が得られることを固定する（要件 11.3）。"""

    @pytest.mark.parametrize("kind", _DETECTOR_KINDS, ids=_kind_id)
    def test_processing_same_synthetic_session_twice_yields_identical_track_updates(
        self, synthetic_module, fakes_module, kind
    ) -> None:
        frames = _synthesize(synthetic_module)
        settings = _settings(kind)

        # 2つの独立した TrackingPipeline / SyntheticFrameSource インスタンス
        # で、同一内容のフレーム列を処理する（タスク本文「2回処理して」を
        # 文字通り2つの独立した実行として実装する）。
        source1 = fakes_module.SyntheticFrameSource(frames, kind=SourceKind.SIMULATED)
        updates1 = list(track_source(source1, settings, NullLogger()))

        source2 = fakes_module.SyntheticFrameSource(frames, kind=SourceKind.SIMULATED)
        updates2 = list(track_source(source2, settings, NullLogger()))

        assert updates1 == updates2

        final_track1 = updates1[-1].track
        final_track2 = updates2[-1].track
        assert final_track1 == final_track2
        # 自明な「空同士の一致」ではないことを確認する。
        assert len(final_track1.points) == MOVING_LEN


# ==============================================================================
# 4. 入力元の種別だけを変えた同一内容の入力での結果一致（タスク本文5）
# ==============================================================================


class TestSourceKindEquivalence:
    """入力元の種別だけを変えた同一内容の入力でも同一結果になることを固定する（要件 1.2）。

    `sensing_foundation.source._build_simulated_profile()` は
    `SourceKind.SIMULATED` 用の `StreamProfile.intrinsics` を常に `None`
    にするため、`open_source(SourceKind.SIMULATED)` を経由すると候補検出時
    に `IntrinsicsUnavailableError` が伝播し、意味のある点列比較ができない
    （tasks.md タスク6.2 Implementation Notes、および
    `test_flying_object_tracking_track_source.py`
    `TestCrossSourceEquivalence` が既に固定している制約）。本テストも
    同じ読み替えを踏襲する: 「テスト用のフレーム供給」=
    `fakes_module.SyntheticFrameSource`、「上流の記録再生に相当する供給」=
    `SessionRecorder` → `open_source(SourceKind.RECORDED)`。
    """

    def test_synthetic_and_recorded_sources_yield_identical_tracks(
        self, synthetic_module, fakes_module, tmp_path: Path
    ) -> None:
        frames = _synthesize(synthetic_module)
        settings = _settings(DetectorKind.DEPTH_BAND)

        synthetic_source = fakes_module.SyntheticFrameSource(frames, kind=SourceKind.SIMULATED)
        synthetic_updates = list(track_source(synthetic_source, settings, NullLogger()))

        session_dir = _record_session(tmp_path, frames)
        recorded_source = _open_recorded_source(session_dir)
        recorded_updates = list(track_source(recorded_source, settings, NullLogger()))

        assert len(synthetic_updates) == len(frames)
        assert len(recorded_updates) == len(frames)

        normalized_synthetic = [_normalize_update(u) for u in synthetic_updates]
        normalized_recorded = [_normalize_update(u) for u in recorded_updates]
        assert normalized_synthetic == normalized_recorded

        final_synthetic = _normalize_track(synthetic_updates[-1].track)
        final_recorded = _normalize_track(recorded_updates[-1].track)
        assert final_synthetic == final_recorded
        assert len(final_synthetic.points) == MOVING_LEN
