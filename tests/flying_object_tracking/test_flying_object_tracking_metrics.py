"""`TrackingMetrics` の観測可能な完了状態を固定する（タスク 5.1）。

design.md「L3: 計測と画像処理の基礎 → TrackingMetrics」・要件 8.1, 8.2, 8.3,
8.5, 8.6, 8.7, 8.9, 9.3 を対象にする。

検証する観測可能な完了状態（tasks.md 5.1 verbatim）:

- 計測有効時に検出・追跡の両段階でイベントが送出される
- 計測無効時に1件もイベントが送出されない（`NullLogger` へ真に委譲する）
- 取りこぼし（`frames_without_candidate`）と3D化失敗（`point_failures`）が
  別々に数えられる
- `gate_rejected` の累積が task 4.1 の教訓（事後合計ではなく per-call 累積）
  どおりであること

`Logger` プロトコルの本物（`sensing_foundation.StructuredLogger`）はキュー・
スレッドを持ち決定的なアサーションを書きにくいため、本ファイルは
`Logger` プロトコルを構造的に満たす最小のインメモリ・フェイクを使う
（タスク指示: 「tests/sensing_foundation/ が使う test double を確認し、
簡単な方でよい」。`sensing_foundation` 側は `Logger` 専用の共有フェイクを
公開していないため、本ファイル内に最小限のものを定義する）。
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass, field

import pytest
from sensing_foundation import (
    LoggingConfig,
    NullLogger,
    SessionClock,
    StructuredLogger,
    summarize_log,
)

from flying_object_tracking.config import MeasurementConfig, TrackerConfig
from flying_object_tracking.metrics import TrackingCounters, TrackingMetrics
from flying_object_tracking.tracking import SingleObjectTracker
from flying_object_tracking.types import (
    CameraPoint,
    CameraTrack,
    CandidateRejection,
    CoordinateFrame,
    HANDOFF_VERSION,
    PointFailure,
    PointFailureReason,
    RejectReason,
    SourceKind,
    TrackEndReason,
    TrackPoint,
    TrackState,
    TrackUpdate,
)

# --------------------------------------------------------------------------
# テストダブル
# --------------------------------------------------------------------------


@dataclass
class _RecordedEvent:
    stage: str
    event: str
    data: dict[str, object]


class _RecordingLogger:
    """`Logger` プロトコルを構造的に満たす、送出内容を記録するだけのフェイク。

    `sensing_foundation.StructuredLogger` はキュー・writer スレッドを持ち
    非同期に書き込むため、決定的なアサーションを書くにはこちらの方が適する。
    """

    def __init__(self) -> None:
        self.enabled = True
        self.events: list[_RecordedEvent] = []

    def emit(self, stage: str, event: str, /, **data: object) -> None:
        self.events.append(_RecordedEvent(stage, event, dict(data)))

    def stage(self, stage: str):  # pragma: no cover - TrackingMetrics は使わない
        raise NotImplementedError

    def timed(self, stage: str, event: str, /, **data: object) -> AbstractContextManager[None]:
        raise NotImplementedError

    def stats(self):  # pragma: no cover - TrackingMetrics は使わない
        raise NotImplementedError

    def close(self, timeout_ms: float = 1000.0) -> None:  # pragma: no cover
        return None


class _ForbiddenLogger:
    """`emit()` / `timed()` が一度でも呼ばれたら即座に失敗するフェイク。

    計測無効時に `TrackingMetrics` が本当に `NullLogger` へ委譲しており、
    コンストラクタに渡された（有効な）ロガーへ一切触れないことを証明する
    ために使う。`enabled = True` にしておくことで、`TrackingMetrics` が
    もし内部で `self._logger.enabled` の判定だけに頼り、渡された実体を
    そのまま使い回していた場合に検出できる。
    """

    def __init__(self) -> None:
        self.enabled = True

    def emit(self, stage: str, event: str, /, **data: object) -> None:
        raise AssertionError(
            f"計測無効時に emit が呼ばれた: stage={stage!r} event={event!r} data={data!r}"
        )

    def stage(self, stage: str):
        raise AssertionError(f"計測無効時に stage({stage!r}) が呼ばれた")

    def timed(self, stage: str, event: str, /, **data: object) -> AbstractContextManager[None]:
        raise AssertionError(
            f"計測無効時に timed が呼ばれた: stage={stage!r} event={event!r} data={data!r}"
        )

    def stats(self):
        raise AssertionError("計測無効時に stats が呼ばれた")

    def close(self, timeout_ms: float = 1000.0) -> None:
        raise AssertionError("計測無効時に close が呼ばれた")


@dataclass(frozen=True, slots=True)
class _FrameStub:
    """`CaptureFrame` のうち `record_update` が実際に使う `index` だけを持つ最小の代役。

    `sensing_foundation.CaptureFrame` は Depth 配列・`StreamProfile` 等を
    要求する重い構築が必要だが、`TrackingMetrics.record_update` は
    `frame.index` しか参照しない（`frame_index` として送出するため）。
    Python は実行時に型注釈を強制しないため、属性だけを満たすこの代役で
    `TrackingMetrics` の振る舞いを直接検証できる。
    """

    index: int


# --------------------------------------------------------------------------
# ドメイン値のビルダ（test_flying_object_tracking_tracker_boundaries.py と同じ形）
# --------------------------------------------------------------------------


def _point(
    t_ms: float,
    x_mm: float,
    y_mm: float,
    z_mm: float,
    *,
    valid_depth_px: int = 50,
    depth_spread_mm: float = 5.0,
    apparent_diameter_px: float = 20.0,
    expected_diameter_px: float = 20.0,
    intrinsics_source: str = "stream_profile",
) -> CameraPoint:
    return CameraPoint(
        frame=CoordinateFrame.CAMERA,
        t_ms=t_ms,
        x_mm=x_mm,
        y_mm=y_mm,
        z_mm=z_mm,
        valid_depth_px=valid_depth_px,
        depth_spread_mm=depth_spread_mm,
        apparent_diameter_px=apparent_diameter_px,
        expected_diameter_px=expected_diameter_px,
        intrinsics_source=intrinsics_source,
    )


def _track_point(point: CameraPoint, *, frame_index: int = 0, rivals: int = 0) -> TrackPoint:
    return TrackPoint(
        point=point,
        frame_index=frame_index,
        frame_seq=frame_index,
        gap_before=0,
        rivals=rivals,
    )


def _camera_track(
    *,
    points: tuple[TrackPoint, ...] = (),
    state: TrackState = TrackState.TRACKING,
    end_reason: TrackEndReason | None = None,
    started_t_ms: float = 0.0,
    track_id: int = 0,
) -> CameraTrack:
    return CameraTrack(
        handoff_version=HANDOFF_VERSION,
        frame=CoordinateFrame.CAMERA,
        track_id=track_id,
        started_t_ms=started_t_ms,
        points=points,
        state=state,
        end_reason=end_reason,
        source=SourceKind.SIMULATED,
        detector_kind="frame_diff",
    )


def _track_update(
    *,
    track: CameraTrack,
    appended: TrackPoint | None = None,
    candidates: int = 0,
    rejections: tuple[CandidateRejection, ...] = (),
    point_failures: tuple[PointFailure, ...] = (),
) -> TrackUpdate:
    return TrackUpdate(
        track=track,
        appended=appended,
        candidates=candidates,
        rejections=rejections,
        point_failures=point_failures,
    )


def _measurement_config(*, enabled: bool = True) -> MeasurementConfig:
    return MeasurementConfig(
        enabled=enabled,
        window_ms=600.0,
        detect_stage="detect",
        track_stage="track",
    )


def _tracker_config(
    *, max_step_mm: float = 900.0, max_missing_frames: int = 3
) -> TrackerConfig:
    return TrackerConfig(
        max_step_mm=max_step_mm,
        max_missing_frames=max_missing_frames,
        min_points_to_start=1,
        max_points=120,
    )


# ==========================================================================
# 1. stage 名が正しく、上流の予約 stage と衝突しない
# ==========================================================================


class TestStageNames:
    def test_detect_and_track_stage_names_do_not_collide_with_reserved_stages(self) -> None:
        from sensing_foundation.obslog import RESERVED_STAGES

        config = _measurement_config()
        assert config.detect_stage == "detect"
        assert config.track_stage == "track"
        assert config.detect_stage not in RESERVED_STAGES
        assert config.track_stage not in RESERVED_STAGES


# ==========================================================================
# 2. 計測有効時: detect() / track() が区間ごとに独立したイベントを送出する
# ==========================================================================


class TestEnabledEmitsDetectAndTrackEvents:
    def test_detect_and_track_context_managers_alone_emit_nothing(self) -> None:
        """`detect()` / `track()` は純粋なタイマーであり、単独では何も送出
        しない（レビュー指摘の是正後の振る舞い）。実際の送出は
        `record_update()` が1フレームにつき1回だけ行う。
        """
        logger = _RecordingLogger()
        metrics = TrackingMetrics(logger, _measurement_config(enabled=True))

        with metrics.detect(frame_index=7):
            pass
        with metrics.track(frame_index=7):
            pass

        assert logger.events == []

    def test_full_frame_sequence_emits_exactly_one_merged_event_per_stage(self) -> None:
        """`detect()` → `track()` → `record_update()` という実際の呼び出し
        順序で、`stage="detect"`/`"track"` それぞれについて `event="frame"`
        がちょうど1回だけ送出され、そのイベントに区間の所要時間とドメイン
        データの両方が揃っていることを確認する（レビュー指摘: 二重送出に
        よる `missing` の見せかけ増加を防ぐ）。
        """
        logger = _RecordingLogger()
        metrics = TrackingMetrics(logger, _measurement_config(enabled=True))

        with metrics.detect(frame_index=1):
            pass
        with metrics.track(frame_index=1):
            pass

        point = _point(10.0, 0.0, 0.0, 1000.0)
        tp = _track_point(point, frame_index=1, rivals=2)
        track = _camera_track(points=(tp,), state=TrackState.TRACKING)
        rejections = (CandidateRejection(reason=RejectReason.TOO_SMALL_PX, count=1),)
        update = _track_update(track=track, appended=tp, candidates=3, rejections=rejections)
        metrics.record_update(update, _FrameStub(index=1))

        detect_events = [e for e in logger.events if e.stage == "detect" and e.event == "frame"]
        track_events = [e for e in logger.events if e.stage == "track" and e.event == "frame"]
        assert len(detect_events) == 1, "detect/frame はフレームにつき正確に1回だけ送出されるはず"
        assert len(track_events) == 1, "track/frame はフレームにつき正確に1回だけ送出されるはず"

        detect_data = detect_events[0].data
        assert detect_data["frame_index"] == 1
        assert isinstance(detect_data["detect_ms"], float)
        assert detect_data["detect_ms"] >= 0.0
        assert detect_data["candidates"] == 3
        assert detect_data["rejected"] == {RejectReason.TOO_SMALL_PX: 1}

        track_data = track_events[0].data
        assert track_data["frame_index"] == 1
        assert isinstance(track_data["track_ms"], float)
        assert track_data["track_ms"] >= 0.0
        assert track_data["appended"] is True
        assert track_data["points"] == 1
        assert track_data["rivals"] == 2
        assert "gate_rejected" in track_data

    def test_pending_timing_does_not_leak_into_the_next_frame(self) -> None:
        """`detect()` / `track()` を呼ばなかったフレームでは、前フレームの
        所要時間を使い回さない（消費後に確実にリセットされる）。
        """
        logger = _RecordingLogger()
        metrics = TrackingMetrics(logger, _measurement_config(enabled=True))

        with metrics.detect(frame_index=0):
            pass
        with metrics.track(frame_index=0):
            pass
        track0 = _camera_track(points=(), state=TrackState.IDLE)
        metrics.record_update(
            _track_update(track=track0, appended=None, candidates=0), _FrameStub(index=0)
        )

        # フレーム1: detect()/track() を一切呼ばない。
        track1 = _camera_track(points=(), state=TrackState.IDLE)
        metrics.record_update(
            _track_update(track=track1, appended=None, candidates=0), _FrameStub(index=1)
        )

        frame1_detect = [
            e
            for e in logger.events
            if e.stage == "detect" and e.event == "frame" and e.data["frame_index"] == 1
        ]
        frame1_track = [
            e
            for e in logger.events
            if e.stage == "track" and e.event == "frame" and e.data["frame_index"] == 1
        ]
        assert len(frame1_detect) == 1
        assert len(frame1_track) == 1
        assert "detect_ms" not in frame1_detect[0].data
        assert "track_ms" not in frame1_track[0].data

    def test_record_update_emits_track_end_event_on_transition_to_ended(self) -> None:
        logger = _RecordingLogger()
        metrics = TrackingMetrics(logger, _measurement_config(enabled=True))

        start_point = _point(0.0, 0.0, 0.0, 1000.0)
        start_tp = _track_point(start_point, frame_index=0)
        tracking_track = _camera_track(points=(start_tp,), state=TrackState.TRACKING)
        metrics.record_update(
            _track_update(track=tracking_track, appended=start_tp, candidates=1),
            _FrameStub(index=0),
        )

        ended_track = _camera_track(
            points=(start_tp,),
            state=TrackState.ENDED,
            end_reason=TrackEndReason.LOST,
        )
        metrics.record_update(
            _track_update(track=ended_track, appended=None, candidates=0),
            _FrameStub(index=1),
        )

        end_events = [e for e in logger.events if e.stage == "track" and e.event == "track_end"]
        assert len(end_events) == 1
        data = end_events[0].data
        assert data["track_id"] == 0
        assert data["points"] == 1
        assert "duration_ms" in data
        assert data["end_reason"] is TrackEndReason.LOST


# ==========================================================================
# 3. 計測無効時: 1件もイベントを送出せず、渡されたロガーに一切触れない
# ==========================================================================


class TestDisabledMeasurementEmitsNothing:
    def test_disabled_config_never_touches_the_constructor_logger(self) -> None:
        """config.enabled=False のとき、渡した実体が有効なロガーでも一切呼ばれない。

        `TrackingMetrics` が内部で `NullLogger` へ委譲していることの直接証拠。
        `_ForbiddenLogger` は呼ばれた瞬間に AssertionError を送出するため、
        以下がすべて例外なく完了すること自体が「一度も呼ばれなかった」ことの
        証明になる。
        """
        forbidden = _ForbiddenLogger()
        metrics = TrackingMetrics(forbidden, _measurement_config(enabled=False))

        with metrics.detect(frame_index=0):
            pass
        with metrics.track(frame_index=0):
            pass

        point = _point(0.0, 0.0, 0.0, 1000.0)
        tp = _track_point(point)
        track = _camera_track(points=(tp,), state=TrackState.TRACKING)
        update = _track_update(track=track, appended=tp, candidates=1)
        metrics.record_update(update, _FrameStub(index=0))

        # ここまで例外が飛ばなければ emit/timed/stage/close は一度も
        # 呼ばれていない。

    def test_disabled_config_swaps_to_null_logger_internally(self) -> None:
        """`NullLogger` 相当へ差し替わっていることを型で確認する。"""
        metrics = TrackingMetrics(_ForbiddenLogger(), _measurement_config(enabled=False))
        assert isinstance(metrics._logger, NullLogger)  # type: ignore[attr-defined]

    def test_disabled_measurement_counters_still_advance(self) -> None:
        """イベント送出は行わなくても、カウンタ自体は無効時も更新される。

        カウンタの更新は「計測値の生成（ログイベント）」ではなく、
        呼び出し側が後で `counters()` を読める軽量な整数演算であるため、
        計測 ON/OFF に関わらず一貫した値を返す（要件 8.8 の ON/OFF 比較を
        阻害しない）。
        """
        forbidden = _ForbiddenLogger()
        metrics = TrackingMetrics(forbidden, _measurement_config(enabled=False))

        point = _point(0.0, 0.0, 0.0, 1000.0)
        tp = _track_point(point)
        track = _camera_track(points=(tp,), state=TrackState.TRACKING)
        update = _track_update(track=track, appended=tp, candidates=1)
        metrics.record_update(update, _FrameStub(index=0))

        counters = metrics.counters()
        assert counters.frames_processed == 1
        assert counters.candidates_total == 1


# ==========================================================================
# 4. frames_without_candidate と point_failures は別々のカウンタである
# ==========================================================================


class TestSeparateCounters:
    def test_frames_without_candidate_increments_independently_of_point_failures(self) -> None:
        logger = _RecordingLogger()
        metrics = TrackingMetrics(logger, _measurement_config(enabled=True))

        # フレーム0: 候補ゼロ（取りこぼし）、3D化失敗は無い。
        empty_track = _camera_track(points=(), state=TrackState.IDLE)
        metrics.record_update(
            _track_update(track=empty_track, appended=None, candidates=0),
            _FrameStub(index=0),
        )
        after_frame0 = metrics.counters()
        assert after_frame0.frames_without_candidate == 1
        assert after_frame0.point_failures == 0

        # フレーム1: 候補はあるが3D化に失敗（取りこぼしではない）。
        failure = PointFailure(
            reason=PointFailureReason.INSUFFICIENT_VALID_PIXELS, valid_depth_px=1
        )
        metrics.record_update(
            _track_update(
                track=empty_track,
                appended=None,
                candidates=1,
                point_failures=(failure,),
            ),
            _FrameStub(index=1),
        )
        after_frame1 = metrics.counters()
        assert after_frame1.frames_without_candidate == 1  # 変化しない
        assert after_frame1.point_failures == 1  # こちらだけ増える

    def test_point_failures_can_increment_without_affecting_frames_without_candidate(self) -> None:
        logger = _RecordingLogger()
        metrics = TrackingMetrics(logger, _measurement_config(enabled=True))

        failure = PointFailure(
            reason=PointFailureReason.DEPTH_SPREAD_TOO_LARGE, valid_depth_px=10
        )
        track = _camera_track(points=(), state=TrackState.IDLE)
        # candidates=2 なので取りこぼしではないが、うち1つが3D化に失敗。
        metrics.record_update(
            _track_update(
                track=track, appended=None, candidates=2, point_failures=(failure,)
            ),
            _FrameStub(index=0),
        )

        counters = metrics.counters()
        assert counters.frames_without_candidate == 0
        assert counters.point_failures == 1


# ==========================================================================
# 5. gate_rejected: task 4.1 の教訓（事後合計ではなく per-call 累積）
# ==========================================================================


class TestGateRejectedAccumulation:
    def test_gate_rejected_uses_per_call_formula_not_post_hoc_rivals_sum(self) -> None:
        """task 4.1 が実証した誤り（`sum(p.rivals for p in track.points)`）を
        再現するシナリオ: 複数候補フレームで全候補がゲート外（`appended is
        None`）になると、`TrackPoint` が1つも作られないため `rivals` には
        一切残らない。`TrackingMetrics` が正しく `TrackUpdate.candidates` /
        `appended` から per-call 累積していれば、この場合でも
        `gate_rejected` は正しく増える。
        """
        logger = _RecordingLogger()
        metrics = TrackingMetrics(logger, _measurement_config(enabled=True))

        tracker_config = _tracker_config(max_step_mm=10.0, max_missing_frames=10)
        tracker = SingleObjectTracker(tracker_config, SourceKind.SIMULATED, "frame_diff")

        # フレーム0: 開始点1つ（IDLE -> TRACKING）。このフレームは
        # 「まだ IDLE だった」呼び出しであり、gate_rejected には含まれない。
        start = _point(0.0, 0.0, 0.0, 1000.0)
        update0 = tracker.update([start], frame_index=0, frame_seq=0, gap_before=0)
        metrics.record_update(update0, _FrameStub(index=0))
        assert metrics.counters().gate_rejected == 0

        # フレーム1: TRACKING 状態で、3候補すべてがゲート外
        # （max_step_mm=10.0 に対し、全候補が500mm以上離れている）。
        # appended は None になり、TrackPoint が1つも作られないため、
        # 事後の rivals 合計方式では 0 のまま検出できない過小評価になる。
        far_candidates = [
            _point(33.0, 500.0, 0.0, 1000.0),
            _point(33.0, 0.0, 500.0, 1000.0),
            _point(33.0, 0.0, 0.0, 1500.0),
        ]
        update1 = tracker.update(
            far_candidates, frame_index=1, frame_seq=1, gap_before=0
        )
        assert update1.appended is None  # 全候補がゲート外
        assert update1.candidates == 3

        metrics.record_update(update1, _FrameStub(index=1))

        # 正しい per-call 累積: TRACKING 状態で appended is None なので
        # candidates(3) をそのまま加算する。
        assert metrics.counters().gate_rejected == 3

        # 事後の rivals 合計方式が過小評価になることの直接証拠
        # （このテストが再現しようとしている task 4.1 の誤り）。
        post_hoc_sum = sum(p.rivals for p in update1.track.points)
        assert post_hoc_sum == 0
        assert metrics.counters().gate_rejected != post_hoc_sum

    def test_gate_rejected_accumulates_across_multiple_tracking_calls(self) -> None:
        """採用ありのフレーム（candidates - 1）と採用なしのフレーム
        （candidates）が正しく累積されることを、複数フレームにわたって
        確認する。
        """
        logger = _RecordingLogger()
        metrics = TrackingMetrics(logger, _measurement_config(enabled=True))

        tracker_config = _tracker_config(max_step_mm=200.0, max_missing_frames=10)
        tracker = SingleObjectTracker(tracker_config, SourceKind.SIMULATED, "frame_diff")

        start = _point(0.0, 0.0, 0.0, 1000.0)
        update0 = tracker.update([start], frame_index=0, frame_seq=0, gap_before=0)
        metrics.record_update(update0, _FrameStub(index=0))
        assert metrics.counters().gate_rejected == 0  # IDLE 呼び出しは対象外

        # フレーム1: 2候補がゲート内、1つが採用される -> candidates(2) - 1 = 1
        near_candidates = [
            _point(33.0, 50.0, 0.0, 1000.0),
            _point(33.0, 100.0, 0.0, 1000.0),
        ]
        update1 = tracker.update(
            near_candidates, frame_index=1, frame_seq=1, gap_before=0
        )
        assert update1.appended is not None
        assert update1.candidates == 2
        metrics.record_update(update1, _FrameStub(index=1))
        assert metrics.counters().gate_rejected == 1

        # フレーム2: 候補ゼロ -> gate_rejected は変化しない（0 を加算）。
        update2 = tracker.update([], frame_index=2, frame_seq=2, gap_before=0)
        metrics.record_update(update2, _FrameStub(index=2))
        assert metrics.counters().gate_rejected == 1

        # フレーム3: 3候補すべてゲート外 -> +3。
        far_candidates = [
            _point(66.0, 900.0, 0.0, 1000.0),
            _point(66.0, 0.0, 900.0, 1000.0),
            _point(66.0, 900.0, 900.0, 1000.0),
        ]
        update3 = tracker.update(
            far_candidates, frame_index=3, frame_seq=3, gap_before=0
        )
        assert update3.appended is None
        metrics.record_update(update3, _FrameStub(index=3))
        assert metrics.counters().gate_rejected == 1 + 3


# ==========================================================================
# 6. counters() はスナップショットであり、生成後の内部状態変化を反映しない
# ==========================================================================


class TestCountersSnapshotSemantics:
    def test_counters_snapshot_does_not_mutate_after_further_updates(self) -> None:
        logger = _RecordingLogger()
        metrics = TrackingMetrics(logger, _measurement_config(enabled=True))

        point = _point(0.0, 0.0, 0.0, 1000.0)
        tp = _track_point(point)
        track = _camera_track(points=(tp,), state=TrackState.TRACKING)
        metrics.record_update(
            _track_update(track=track, appended=tp, candidates=1), _FrameStub(index=0)
        )

        snapshot_1 = metrics.counters()
        assert snapshot_1.frames_processed == 1

        metrics.record_update(
            _track_update(track=track, appended=tp, candidates=1), _FrameStub(index=1)
        )
        snapshot_2 = metrics.counters()

        # 以前に取得したスナップショットは変化しない（frozen dataclass）。
        assert snapshot_1.frames_processed == 1
        assert snapshot_2.frames_processed == 2
        assert snapshot_1 != snapshot_2

    def test_counters_returns_tracking_counters_instance(self) -> None:
        logger = _RecordingLogger()
        metrics = TrackingMetrics(logger, _measurement_config(enabled=True))
        assert isinstance(metrics.counters(), TrackingCounters)


# ==========================================================================
# 7. 集計（代表値・分位点）を実行中に行わない
# ==========================================================================


class TestNoAggregation:
    def test_module_does_not_import_statistics_or_numpy(self) -> None:
        """`metrics.py` が集計ライブラリを一切 import していないことを
        ソースの静的走査で確認する（要件 8.9 の消極的な裏付け）。
        """
        import flying_object_tracking.metrics as metrics_module

        source_path = metrics_module.__file__
        assert source_path is not None
        with open(source_path, encoding="utf-8") as fh:
            source = fh.read()

        forbidden_tokens = (
            "import statistics",
            "import numpy",
            "percentile",
            "median(",
            "mean(",
            "quantile",
        )
        for token in forbidden_tokens:
            assert token not in source, f"集計を思わせるトークンが見つかった: {token!r}"


# ==========================================================================
# 8. detect() / track() は共有の no-op コンテキストを返す（無効時）
# ==========================================================================


class TestDisabledContextManagersAreNoOps:
    def test_detect_and_track_context_managers_do_not_raise_when_disabled(self) -> None:
        metrics = TrackingMetrics(_ForbiddenLogger(), _measurement_config(enabled=False))

        with metrics.detect(frame_index=0):
            pass
        with metrics.track(frame_index=0):
            pass

    def test_detect_context_manager_propagates_exceptions_from_the_body(self) -> None:
        """タイマーであって、本体の例外を握りつぶさないことを確認する。"""
        logger = _RecordingLogger()
        metrics = TrackingMetrics(logger, _measurement_config(enabled=True))

        with pytest.raises(ValueError, match="boom"):
            with metrics.detect(frame_index=0):
                raise ValueError("boom")


# ==========================================================================
# 9. 契約テスト: 実際の StructuredLogger + summarize_log() で二重送出が
#    起きていないことを直接検証する（レビュー指摘への対応）
# ==========================================================================
#
# レビューは、初版の実装（detect()/track() が即座に部分イベントを送出し、
# record_update() がさらに別の部分イベントを送出する構成）を
# sensing_foundation.logsummary.summarize_log() に通した結果、
# EventSummary.count が実フレーム数の2倍になり、本来存在したはずの値が
# 「たまたま別の行に書かれていた」ために missing として数えられることを
# 直接再現して指摘した。本クラスはその再発防止のための契約テストであり、
# 手動確認ではなく実際の StructuredLogger（キュー・writer スレッドを含む
# 本物の NDJSON 書き出し経路）を通して summarize_log() を実行する。


class TestLogSummaryContract:
    def test_summarize_log_reports_one_event_per_frame_with_no_spurious_missing(
        self, tmp_path
    ) -> None:
        logging_config = LoggingConfig(
            enabled=True, path=tmp_path, queue_capacity=64, flush_each_line=True
        )
        clock = SessionClock("metrics-contract-test")
        logger = StructuredLogger(logging_config, clock)
        metrics = TrackingMetrics(logger, _measurement_config(enabled=True))

        num_frames = 5
        point = _point(0.0, 0.0, 0.0, 1000.0)
        try:
            for i in range(num_frames):
                with metrics.detect(frame_index=i):
                    pass
                with metrics.track(frame_index=i):
                    pass

                tp = _track_point(point, frame_index=i, rivals=1)
                track = _camera_track(points=(tp,), state=TrackState.TRACKING)
                update = _track_update(track=track, appended=tp, candidates=2)
                metrics.record_update(update, _FrameStub(index=i))
        finally:
            logger.close(timeout_ms=2000.0)

        log_path = tmp_path / f"{clock.session_id}.ndjson"
        summary = summarize_log(log_path)

        detect_summary = summary.events[("detect", "frame")]
        track_summary = summary.events[("track", "frame")]

        # 実フレーム数と一致すること（二重送出なら num_frames の2倍になる）。
        assert detect_summary.count == num_frames, (
            "detect/frame の件数が実フレーム数と食い違った"
            "（二重送出が再発している可能性がある）"
        )
        assert track_summary.count == num_frames, (
            "track/frame の件数が実フレーム数と食い違った"
            "（二重送出が再発している可能性がある）"
        )

        # detect()/track() を毎フレーム呼んでいるので、実際に計測できた
        # 値については missing が一切無いはず（見せかけの欠測が無いこと）。
        for field_name in ("frame_index", "detect_ms", "candidates", "rejected"):
            stats = detect_summary.fields[field_name]
            assert stats.present == num_frames, f"detect.{field_name}: present={stats.present}"
            assert stats.missing == 0, f"detect.{field_name} が見かけ上欠測している: {stats}"

        for field_name in (
            "frame_index",
            "track_ms",
            "appended",
            "points",
            "rivals",
            "gate_rejected",
        ):
            stats = track_summary.fields[field_name]
            assert stats.present == num_frames, f"track.{field_name}: present={stats.present}"
            assert stats.missing == 0, f"track.{field_name} が見かけ上欠測している: {stats}"

        # detect_ms / track_ms は実測された数値であること。
        assert detect_summary.fields["detect_ms"].numeric_count == num_frames
        assert track_summary.fields["track_ms"].numeric_count == num_frames
