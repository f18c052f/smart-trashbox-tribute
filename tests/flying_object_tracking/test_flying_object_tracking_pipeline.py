"""`pipeline.py`（タスク 6.1 `TrackingPipeline`）の検証。

design.md「L6-L7: 追跡と統合 → TrackingPipeline」と「1フレームの処理」
シーケンス図、要件 1.1, 8.1, 10.2, 10.3 が定める、次の観測可能な完了状態を
固定する（タスク本文 verbatim）:

- 検出段で例外を起こす細工をした入力でも処理が最後まで走り切る
- 捕捉件数がカウンタに現れる
- フレーム欠落が点に記録される

加えて、タスクブリーフが挙げる2つの未決事項の解消を直接検証する:

- `PointEstimator` の単体 `CandidateRejection`（`count=1`）と
  `Detector.detect()` の集約済み `DetectResult.rejections` が、同一理由
  ごとに正しく合算されること（`_merge_rejections` の単体テストと、
  実際の `process()` 経由での統合テストの両方）
- `TrackingMetrics.record_update()` へ追加した `mask_px` キーワード引数が、
  実際に `DetectResult.mask_px` の値を運んでいること

`track_source()`（上流のフレーム供給との結線）はタスク 6.2 の責務であり、
本ファイルは対象にしない。
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass

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

import flying_object_tracking.pipeline as pipeline_module
from flying_object_tracking.config import (
    DetectorConfig,
    MeasurementConfig,
    ObjectModelConfig,
    ProjectionConfig,
    RoiConfig,
    TrackerConfig,
    TrackingSettings,
)
from flying_object_tracking.detection.detector import DetectResult
from flying_object_tracking.errors import IntrinsicsUnavailableError, TrackingConfigError
from flying_object_tracking.pipeline import CaughtExceptionCounters, TrackingPipeline
from flying_object_tracking.types import (
    Candidate,
    CameraTrack,
    CandidateRejection,
    DetectorKind,
    RejectReason,
    TrackEndReason,
    TrackState,
    TrackUpdate,
)

DIAMETER_MM = 65.0
BLOB_Z_MM = 1000.0  # 既定 z 帯 [500, 5000] の内側
BACKGROUND_RAW = 6000  # 既定 z 帯の外側


# --------------------------------------------------------------------------
# テストダブル: 送出内容を記録するだけの Logger フェイク
# （test_flying_object_tracking_metrics.py と同じ形。bare import を避けるため
# 本ファイル内に複製する。conftest の対象は synthetic.py / fakes.py のみ）
# --------------------------------------------------------------------------


@dataclass
class _RecordedEvent:
    stage: str
    event: str
    data: dict[str, object]


class _RecordingLogger:
    def __init__(self) -> None:
        self.enabled = True
        self.events: list[_RecordedEvent] = []

    def emit(self, stage: str, event: str, /, **data: object) -> None:
        self.events.append(_RecordedEvent(stage, event, dict(data)))

    def stage(self, stage: str):  # pragma: no cover - TrackingPipeline は使わない
        raise NotImplementedError

    def timed(self, stage: str, event: str, /, **data: object) -> AbstractContextManager[None]:
        raise NotImplementedError

    def stats(self):  # pragma: no cover
        raise NotImplementedError

    def close(self, timeout_ms: float = 1000.0) -> None:  # pragma: no cover
        return None


# --------------------------------------------------------------------------
# ドメイン値のビルダ
# --------------------------------------------------------------------------


def _intrinsics(*, width_px: int = 64, height_px: int = 64) -> CameraIntrinsics:
    return CameraIntrinsics(
        width_px=width_px,
        height_px=height_px,
        fx_px=200.0,
        fy_px=200.0,
        ppx_px=width_px / 2.0,
        ppy_px=height_px / 2.0,
        model="none",
        coeffs=(0.0, 0.0, 0.0, 0.0, 0.0),
    )


def _profile(
    *,
    width_px: int = 64,
    height_px: int = 64,
    depth_scale_mm: float = 1.0,
    with_intrinsics: bool = True,
) -> StreamProfile:
    return StreamProfile(
        width_px=width_px,
        height_px=height_px,
        fps=30,
        depth_scale_mm=depth_scale_mm,
        color_enabled=False,
        intrinsics=_intrinsics(width_px=width_px, height_px=height_px) if with_intrinsics else None,
    )


def _frame(
    depth: np.ndarray,
    profile: StreamProfile,
    *,
    index: int = 0,
    seq: int = 0,
    t_ms: float = 0.0,
    gap_before: int = 0,
    source: SourceKind = SourceKind.SIMULATED,
) -> CaptureFrame:
    return CaptureFrame(
        index=index,
        seq=seq,
        t_capture_ms=t_ms,
        device_timestamp_ms=None,
        timestamp_domain=TimestampDomain.UNKNOWN,
        capture_latency_ms=None,
        depth=depth,
        profile=profile,
        source=source,
        dropped_before=0,
        gap_before=gap_before,
    )


def _settings(
    *,
    kind: DetectorKind = DetectorKind.DEPTH_BAND,
    measurement_enabled: bool = True,
    z_min_mm: float | None = 500.0,
    z_max_mm: float | None = 5000.0,
    max_missing_frames: int = 3,
    max_step_mm: float = 900.0,
    min_points_to_start: int = 1,
    open_kernel_px: int = 0,
    close_kernel_px: int = 0,
    roi: RoiConfig | None = None,
) -> TrackingSettings:
    return TrackingSettings(
        roi=roi if roi is not None else RoiConfig(z_min_mm=z_min_mm, z_max_mm=z_max_mm),
        object_model=ObjectModelConfig(),
        detector=DetectorConfig(
            kind=kind, open_kernel_px=open_kernel_px, close_kernel_px=close_kernel_px
        ),
        projection=ProjectionConfig(),
        tracker=TrackerConfig(
            max_step_mm=max_step_mm,
            max_missing_frames=max_missing_frames,
            min_points_to_start=min_points_to_start,
        ),
        measurement=MeasurementConfig(enabled=measurement_enabled),
    )


def _blob_depth(
    *,
    width_px: int = 64,
    height_px: int = 64,
    x0: int = 28,
    y0: int = 28,
    w: int = 8,
    h: int = 8,
) -> np.ndarray:
    depth = np.full((height_px, width_px), BACKGROUND_RAW, dtype=np.uint16)
    depth[y0 : y0 + h, x0 : x0 + w] = np.uint16(BLOB_Z_MM)
    return depth


def _trajectory(synthetic_module, n: int, *, step_mm: float = 15.0):
    TrajectoryPoint = synthetic_module.TrajectoryPoint
    return [
        TrajectoryPoint(t_ms=float(i) * 33.0, x_mm=float(i) * step_mm, y_mm=0.0, z_mm=BLOB_Z_MM)
        for i in range(n)
    ]


def _synthesize(synthetic_module, n: int, *, step_mm: float = 15.0, profile: StreamProfile | None = None):
    profile = profile if profile is not None else _profile()
    trajectory = _trajectory(synthetic_module, n, step_mm=step_mm)
    return synthetic_module.synthesize_frames(
        trajectory, profile, DIAMETER_MM, background_raw=BACKGROUND_RAW
    )


# ==========================================================================
# 1. Happy path: 既知の移動する塊の軌道を最後まで処理できる
# ==========================================================================


class TestHappyPath:
    def test_processes_moving_blob_trajectory_and_appends_points(self, synthetic_module) -> None:
        settings = _settings()
        pipeline = TrackingPipeline(settings, NullLogger())
        frames = _synthesize(synthetic_module, 6)

        updates = [pipeline.process(frame) for frame in frames]

        assert all(isinstance(u, TrackUpdate) for u in updates)
        assert all(u.appended is not None for u in updates), [
            (u.candidates, u.appended) for u in updates
        ]

        final_track = pipeline.finish()
        assert isinstance(final_track, CameraTrack)
        assert final_track.state == TrackState.ENDED
        assert final_track.end_reason == TrackEndReason.SOURCE_END
        assert len(final_track.points) == len(frames)
        assert final_track.started_t_ms == frames[0].t_capture_ms


# ==========================================================================
# 2. 例外耐性: 検出段の予期しない例外で処理を止めず、捕捉件数を数える
#    （タスク本文の観測可能な完了状態そのもの）
# ==========================================================================


class TestExceptionResilience:
    def test_unexpected_exception_during_point_estimation_does_not_crash(
        self, synthetic_module
    ) -> None:
        settings = _settings(max_missing_frames=10)
        pipeline = TrackingPipeline(settings, NullLogger())
        frames = _synthesize(synthetic_module, 5)

        broken_index = 2
        original_estimate = pipeline._point_estimator.estimate  # type: ignore[attr-defined]

        def flaky_estimate(frame, candidate):
            if frame.index == broken_index:
                raise ValueError("synthetic estimate failure")
            return original_estimate(frame, candidate)

        pipeline._point_estimator.estimate = flaky_estimate  # type: ignore[method-assign]

        # (a) process() は例外を伝播しない。
        updates = [pipeline.process(frame) for frame in frames]

        # (b) 例外を起こしたフレームは候補ゼロとして扱われる。
        broken_update = updates[broken_index]
        assert broken_update.candidates == 0
        assert broken_update.appended is None

        # (c) 後続フレームの処理は正常に継続する。
        later_updates = updates[broken_index + 1 :]
        assert any(u.appended is not None for u in later_updates), later_updates

        # (d) 捕捉件数がカウンタから取り出せる。
        caught = pipeline.caught_exceptions()
        assert isinstance(caught, CaughtExceptionCounters)
        assert caught.total == 1
        assert dict(caught.by_type) == {"ValueError": 1}

    def test_unexpected_exception_during_detect_does_not_crash(self, synthetic_module) -> None:
        settings = _settings(max_missing_frames=10)
        pipeline = TrackingPipeline(settings, NullLogger())
        frames = _synthesize(synthetic_module, 4)

        broken_index = 1
        original_detect = pipeline._detector.detect  # type: ignore[attr-defined]

        def flaky_detect(frame):
            if frame.index == broken_index:
                raise RuntimeError("synthetic detect failure")
            return original_detect(frame)

        pipeline._detector.detect = flaky_detect  # type: ignore[method-assign]

        updates = [pipeline.process(frame) for frame in frames]

        assert updates[broken_index].candidates == 0
        assert updates[broken_index].appended is None
        assert any(u.appended is not None for u in updates[broken_index + 1 :])

        caught = pipeline.caught_exceptions()
        assert caught.total == 1
        assert dict(caught.by_type) == {"RuntimeError": 1}

    def test_unexpected_exception_during_tracker_update_does_not_crash(
        self, synthetic_module
    ) -> None:
        """レビュー指摘: 初版は検出・逆投影段だけを try/except で囲んでおり、
        `SingleObjectTracker.update()` の予期しない例外がそのまま
        `process()` から伝播していた（タスク本文「1フレームの処理中」の
        範囲は3段すべてに及ぶ）。この特定のフレームだけ `tracker.update()`
        を壊し、(a) 例外が伝播しない、(b) そのフレームは候補ゼロ扱い、
        (c) 後続フレームは実際に点が追加される形で正常継続する、
        (d) 捕捉件数が1件だけカウントされることを確認する。
        """
        settings = _settings(max_missing_frames=10)
        pipeline = TrackingPipeline(settings, NullLogger())
        frames = _synthesize(synthetic_module, 5)

        broken_index = 2

        # tracker は最初の process() 呼び出しで遅延構築される。broken_index
        # より前のフレームを先に処理しておき、その後に限定して
        # tracker.update() を破壊する（破壊対象のフレームだけに限定するため、
        # 元のバインドメソッドをフレーム処理の前後で差し替える）。
        updates: list[TrackUpdate] = []
        for frame in frames:
            if frame.index == broken_index:
                original_bound_update = pipeline._tracker.update  # type: ignore[attr-defined]

                def flaky_update(points, *, frame_index, frame_seq, gap_before):
                    raise ValueError("synthetic tracker failure")

                pipeline._tracker.update = flaky_update  # type: ignore[method-assign]
                updates.append(pipeline.process(frame))
                pipeline._tracker.update = original_bound_update  # type: ignore[method-assign]
            else:
                updates.append(pipeline.process(frame))

        broken_update = updates[broken_index]
        assert broken_update.appended is None
        assert broken_update.candidates == 0
        # フォールバックは直前の成功スナップショットを使い回すため、点数は
        # その直前フレーム時点から変わらない。
        assert broken_update.track.points == updates[broken_index - 1].track.points

        later_updates = updates[broken_index + 1 :]
        assert any(u.appended is not None for u in later_updates), later_updates

        caught = pipeline.caught_exceptions()
        assert caught.total == 1
        assert dict(caught.by_type) == {"ValueError": 1}

    def test_permanently_broken_tracker_update_falls_back_without_double_counting(
        self, synthetic_module
    ) -> None:
        """`tracker.update()` が渡す引数によらず常に例外を送出する最悪ケース
        （候補ゼロでの再試行も失敗する）でも `process()` は例外を伝播せず、
        `caught_exceptions().total` は1フレームにつき1件だけ（再試行の
        失敗を重ねて数えない）であることを確認する。
        """
        settings = _settings(max_missing_frames=10)
        pipeline = TrackingPipeline(settings, NullLogger())
        frames = _synthesize(synthetic_module, 2)

        # 最初のフレームで tracker を構築させてから、以後の update を
        # 引数によらず常に破壊する。
        pipeline.process(frames[0])

        def always_raises(points, *, frame_index, frame_seq, gap_before):
            raise ValueError("tracker permanently broken")

        pipeline._tracker.update = always_raises  # type: ignore[method-assign]

        update = pipeline.process(frames[1])

        assert update.appended is None
        assert update.candidates == 0

        caught = pipeline.caught_exceptions()
        assert caught.total == 1
        assert dict(caught.by_type) == {"ValueError": 1}

    def test_caught_exception_is_logged_as_detect_error_event(self, synthetic_module) -> None:
        settings = _settings(max_missing_frames=10)
        logger = _RecordingLogger()
        pipeline = TrackingPipeline(settings, logger)
        frames = _synthesize(synthetic_module, 2)

        def always_raises(frame):
            raise KeyError("boom")

        pipeline._detector.detect = always_raises  # type: ignore[method-assign]

        pipeline.process(frames[0])

        error_events = [e for e in logger.events if e.stage == "detect" and e.event == "error"]
        assert len(error_events) == 1
        assert error_events[0].data["exception_type"] == "KeyError"


# ==========================================================================
# 3. 設定不正・内部パラメータ欠落は捕捉せず伝播する
# ==========================================================================


class TestNonCatchableExceptionsPropagate:
    def test_intrinsics_unavailable_propagates(self) -> None:
        settings = _settings()
        pipeline = TrackingPipeline(settings, NullLogger())

        profile = _profile(with_intrinsics=False)
        depth = _blob_depth()
        frame = _frame(depth, profile)

        with pytest.raises(IntrinsicsUnavailableError):
            pipeline.process(frame)

    def test_tracking_config_error_propagates_for_out_of_bounds_roi(self) -> None:
        settings = _settings(roi=RoiConfig(x_px=0, y_px=0, width_px=999, height_px=999))
        pipeline = TrackingPipeline(settings, NullLogger())

        profile = _profile()
        frame = _frame(np.full((64, 64), BACKGROUND_RAW, dtype=np.uint16), profile)

        with pytest.raises(TrackingConfigError):
            pipeline.process(frame)

    def test_config_errors_are_not_counted_as_caught_exceptions(self) -> None:
        settings = _settings(roi=RoiConfig(x_px=0, y_px=0, width_px=999, height_px=999))
        pipeline = TrackingPipeline(settings, NullLogger())

        profile = _profile()
        frame = _frame(np.full((64, 64), BACKGROUND_RAW, dtype=np.uint16), profile)

        with pytest.raises(TrackingConfigError):
            pipeline.process(frame)

        assert pipeline.caught_exceptions().total == 0


# ==========================================================================
# 4. フレーム欠落（gap_before）が点へ引き継がれる
# ==========================================================================


class TestGapBeforePropagation:
    def test_gap_before_reaches_appended_track_point(self) -> None:
        settings = _settings()
        pipeline = TrackingPipeline(settings, NullLogger())

        profile = _profile()
        frame = _frame(_blob_depth(), profile, gap_before=3)

        update = pipeline.process(frame)

        assert update.appended is not None
        assert update.appended.gap_before == 3


# ==========================================================================
# 5. 計測の配線: detect()/track()/record_update() がフレームごとに正しく
#    呼ばれる（1フレームにつき正確に1回ずつのイベント）
# ==========================================================================


class TestMetricsWiring:
    def test_emits_exactly_one_detect_and_track_event_per_processed_frame(
        self, synthetic_module
    ) -> None:
        settings = _settings()
        logger = _RecordingLogger()
        pipeline = TrackingPipeline(settings, logger)
        frames = _synthesize(synthetic_module, 4)

        for frame in frames:
            pipeline.process(frame)

        detect_events = [e for e in logger.events if e.stage == "detect" and e.event == "frame"]
        track_events = [e for e in logger.events if e.stage == "track" and e.event == "frame"]
        assert len(detect_events) == len(frames)
        assert len(track_events) == len(frames)

    def test_disabled_measurement_emits_no_frame_events(self, synthetic_module) -> None:
        settings = _settings(measurement_enabled=False)
        logger = _RecordingLogger()
        pipeline = TrackingPipeline(settings, logger)
        frames = _synthesize(synthetic_module, 3)

        for frame in frames:
            pipeline.process(frame)

        frame_events = [e for e in logger.events if e.event == "frame"]
        assert frame_events == []


# ==========================================================================
# 6. mask_px の橋渡し: metrics.py へ足した追加引数が実測値を運ぶ
# ==========================================================================


class TestMaskPxBridging:
    def test_detect_event_mask_px_matches_detect_result(self) -> None:
        settings = _settings()
        logger = _RecordingLogger()
        pipeline = TrackingPipeline(settings, logger)

        profile = _profile(width_px=32, height_px=32)
        depth = np.full((32, 32), BACKGROUND_RAW, dtype=np.uint16)
        depth[5:9, 5:11] = np.uint16(BLOB_Z_MM)  # 4x6=24 画素。open/close=0 で無変化。
        frame = _frame(depth, profile)

        pipeline.process(frame)

        detect_events = [e for e in logger.events if e.stage == "detect" and e.event == "frame"]
        assert len(detect_events) == 1
        assert detect_events[0].data["mask_px"] == 24


# ==========================================================================
# 7. 除外理由の合算（開いていた課題1: gate_rejected 節ではなく、
#    PointEstimator 単体 CandidateRejection と DetectResult 集約済み
#    rejections の合算）
# ==========================================================================


class TestRejectionMerging:
    def test_merge_rejections_sums_counts_for_the_same_reason_across_both_sources(self) -> None:
        aggregated = (
            CandidateRejection(reason=RejectReason.TOO_SMALL_PX, count=2),
            CandidateRejection(reason=RejectReason.OUT_OF_ROI, count=1),
        )
        singles = [
            CandidateRejection(reason=RejectReason.TOO_SMALL_PX, count=1),
            CandidateRejection(reason=RejectReason.SIZE_MISMATCH, count=1),
        ]

        merged = pipeline_module._merge_rejections(aggregated, singles)
        merged_by_reason = {r.reason: r.count for r in merged}

        assert merged_by_reason == {
            RejectReason.TOO_SMALL_PX: 3,
            RejectReason.OUT_OF_ROI: 1,
            RejectReason.SIZE_MISMATCH: 1,
        }
        # 同一理由が複数エントリに分裂していないこと（単純連結ではない証拠）。
        assert len(merged) == len(merged_by_reason)

    def test_merge_rejections_returns_empty_tuple_for_no_input(self) -> None:
        assert pipeline_module._merge_rejections((), []) == ()

    def test_process_merges_detect_level_and_point_level_rejections_of_same_reason(
        self, monkeypatch
    ) -> None:
        """検出段の集約済み rejections（count=2）と、PointEstimator が返す
        単体 rejection（count=1）が同じ理由（TOO_SMALL_PX）を持つ場合に、
        `process()` の戻り値で1つの合算済みエントリ（count=3）になること
        （2つに分裂しないこと）を確認する。
        """
        settings = _settings()
        pipeline = TrackingPipeline(settings, NullLogger())

        fake_candidate = Candidate(
            cx_px=5.0, cy_px=5.0, bbox_px=(0, 0, 4, 4), area_px=16, valid_depth_px=16, mask_score=1.0
        )
        fake_detect_result = DetectResult(
            candidates=(fake_candidate,),
            rejections=(CandidateRejection(reason=RejectReason.TOO_SMALL_PX, count=2),),
            mask_px=16,
        )
        monkeypatch.setattr(pipeline._detector, "detect", lambda frame: fake_detect_result)
        monkeypatch.setattr(
            pipeline._point_estimator,
            "estimate",
            lambda frame, candidate: CandidateRejection(reason=RejectReason.TOO_SMALL_PX, count=1),
        )

        profile = _profile()
        frame = _frame(np.zeros((64, 64), dtype=np.uint16), profile)

        update = pipeline.process(frame)

        assert update.rejections == (CandidateRejection(reason=RejectReason.TOO_SMALL_PX, count=3),)


# ==========================================================================
# 8. finish(): 入力が尽きたときの最終点列確定
# ==========================================================================


class TestFinish:
    def test_finish_after_processing_returns_source_end(self, synthetic_module) -> None:
        settings = _settings()
        pipeline = TrackingPipeline(settings, NullLogger())
        frames = _synthesize(synthetic_module, 3)
        for frame in frames:
            pipeline.process(frame)

        track = pipeline.finish()

        assert isinstance(track, CameraTrack)
        assert track.state == TrackState.ENDED
        assert track.end_reason == TrackEndReason.SOURCE_END

    def test_finish_without_any_process_call_is_safe(self) -> None:
        settings = _settings()
        pipeline = TrackingPipeline(settings, NullLogger())

        track = pipeline.finish()

        assert track.end_reason == TrackEndReason.SOURCE_END
        assert track.points == ()
