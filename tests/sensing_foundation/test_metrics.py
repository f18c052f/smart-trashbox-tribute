"""`sensing_foundation.metrics` に対するテスト（タスク 2.2）。

観測可能な完了状態（tasks.md 2.2）を固定する:

- 合成フレームを `CaptureMetrics.frame()` へ流したとき、区間ごとの計測値
  （`wait_ms` / `drain_ms` / `handoff_ms` / `total_ms`）と5種のカウンタ
  （`frames_dropped` / `frames_missing` / `acquire_errors` / `measured_fps` /
  `frames_yielded`）が、ログ出力（`logger.emit()` へ渡された `data`）と
  `counters()` が返す `CaptureStats` の双方から取得できる
- `capture_latency_ms` は `timestamp_domain == GLOBAL_TIME` のときだけログの
  `data` にキーとして現れ、それ以外のドメインでは**キーごと省かれる**
  （`None` を値として送るのではない）
- CPU・メモリは毎フレームではなく、`resource_interval_ms` で設定した間隔
  でのみサンプルされる（`snapshot_resources()` は間隔未経過なら no-op）

要件: 2.2, 2.3, 9.1, 9.2, 9.3, 9.5
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from synthetic import make_depth_frame

from sensing_foundation.metrics import CaptureMetrics, FrameTiming
from sensing_foundation.sysstat import ResourceSample
from sensing_foundation.types import (
    CameraIntrinsics,
    CaptureFrame,
    CaptureStats,
    SourceKind,
    StreamProfile,
    TimestampDomain,
)

WIDTH_PX = 16
HEIGHT_PX = 12


class FakeLogger:
    """`Logger` プロトコルに構造的に適合する、`emit()` 呼び出しを記録するだけの偽実装。

    `emit()` に渡された `stage` / `event` / `**data` をそのまま記録する
    ことで、テストが「どのキーが実際に渡されたか」を厳密に検証できる
    （`data` に含まれない = キーが省かれた、を区別するため）。
    """

    def __init__(self) -> None:
        self.enabled = True
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def emit(self, stage: str, event: str, /, **data: object) -> None:
        self.calls.append((stage, event, dict(data)))

    def stage(self, stage: str):  # pragma: no cover - 本テストでは未使用
        raise NotImplementedError

    def timed(self, stage: str, event: str, /, **data: object):  # pragma: no cover
        raise NotImplementedError

    def stats(self):  # pragma: no cover - 本テストでは未使用
        raise NotImplementedError

    def close(self, timeout_ms: float = 1000.0) -> None:  # pragma: no cover
        return None


class FakeClock:
    """`SessionClock.now_ms()` と同じ形の、任意に進められる偽時計。"""

    def __init__(self, start_ms: float = 0.0) -> None:
        self._now_ms = start_ms

    def now_ms(self) -> float:
        return self._now_ms

    def advance(self, delta_ms: float) -> None:
        self._now_ms += delta_ms


@dataclass
class FakeSysstat:
    """`Sysstat.sample()` と同じ形の、呼び出し回数を数える偽実装。"""

    samples: list[ResourceSample] = field(default_factory=list)
    call_count: int = 0

    def sample(self) -> ResourceSample:
        self.call_count += 1
        if self.samples:
            return self.samples.pop(0)
        return ResourceSample(
            cpu_percent=12.5,
            process_rss_bytes=1024,
            system_available_bytes=2048,
            system_total_bytes=4096,
        )


def _make_profile() -> StreamProfile:
    intrinsics = CameraIntrinsics(
        width_px=WIDTH_PX,
        height_px=HEIGHT_PX,
        fx_px=100.0,
        fy_px=100.0,
        ppx_px=8.0,
        ppy_px=6.0,
        model="brown_conrady",
        coeffs=(0.0, 0.0, 0.0, 0.0, 0.0),
    )
    return StreamProfile(
        width_px=WIDTH_PX,
        height_px=HEIGHT_PX,
        fps=30,
        depth_scale_mm=1.0,
        color_enabled=False,
        intrinsics=intrinsics,
    )


def _make_frame(
    *,
    index: int = 0,
    seq: int = 0,
    dropped_before: int = 0,
    gap_before: int = 0,
    timestamp_domain: TimestampDomain = TimestampDomain.HARDWARE_CLOCK,
    capture_latency_ms: float | None = None,
    device_timestamp_ms: float | None = 5.0,
) -> CaptureFrame:
    depth = make_depth_frame(WIDTH_PX, HEIGHT_PX, seq)
    return CaptureFrame(
        index=index,
        seq=seq,
        t_capture_ms=float(index),
        device_timestamp_ms=device_timestamp_ms,
        timestamp_domain=timestamp_domain,
        capture_latency_ms=capture_latency_ms,
        depth=depth,
        profile=_make_profile(),
        source=SourceKind.LIVE,
        dropped_before=dropped_before,
        gap_before=gap_before,
    )


def _make_timing(total: float = 10.0) -> FrameTiming:
    return FrameTiming(wait_ms=2.0, drain_ms=3.0, handoff_ms=5.0, total_ms=total)


@pytest.fixture
def logger() -> FakeLogger:
    return FakeLogger()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(start_ms=1000.0)


class TestFrameCounters:
    """`frame()` が `dropped_before` / `gap_before` を累積することを固定する（要件 2.2, 2.3）。"""

    def test_accumulates_dropped_and_missing_across_multiple_calls(
        self, logger: FakeLogger, clock: FakeClock
    ) -> None:
        metrics = CaptureMetrics(logger, clock, sysstat=None)

        metrics.frame(_make_timing(), _make_frame(index=0, seq=0, dropped_before=2, gap_before=0))
        metrics.frame(_make_timing(), _make_frame(index=1, seq=1, dropped_before=0, gap_before=3))
        metrics.frame(_make_timing(), _make_frame(index=2, seq=5, dropped_before=1, gap_before=1))

        stats = metrics.counters()
        assert stats.frames_yielded == 3
        assert stats.frames_dropped == 2 + 0 + 1
        assert stats.frames_missing == 0 + 3 + 1

    def test_acquire_error_increments_dedicated_counter_and_emits(
        self, logger: FakeLogger, clock: FakeClock
    ) -> None:
        metrics = CaptureMetrics(logger, clock, sysstat=None)

        metrics.acquire_error()
        metrics.acquire_error()

        stats = metrics.counters()
        assert stats.acquire_errors == 2
        # frame() を経由しないカウンタなので frames_yielded は増えない。
        assert stats.frames_yielded == 0

        error_events = [c for c in logger.calls if c[1] == "acquire_error"]
        assert len(error_events) == 2
        assert all(c[0] == "capture" for c in error_events)

    def test_counters_returns_capturestats_instance(
        self, logger: FakeLogger, clock: FakeClock
    ) -> None:
        metrics = CaptureMetrics(logger, clock, sysstat=None)
        stats = metrics.counters()
        assert isinstance(stats, CaptureStats)


class TestFrameLogEmission:
    """`frame()` がログへ per-frame の計測値を送ることを固定する（要件 9.1）。"""

    def test_emits_capture_stage_frame_event_with_timing_values(
        self, logger: FakeLogger, clock: FakeClock
    ) -> None:
        metrics = CaptureMetrics(logger, clock, sysstat=None)
        timing = FrameTiming(wait_ms=1.5, drain_ms=2.5, handoff_ms=3.5, total_ms=7.5)

        metrics.frame(timing, _make_frame(index=0, seq=0))

        assert len(logger.calls) == 1
        stage, event, data = logger.calls[0]
        assert stage == "capture"
        assert event == "frame"
        assert data["wait_ms"] == 1.5
        assert data["drain_ms"] == 2.5
        assert data["handoff_ms"] == 3.5
        assert data["total_ms"] == 7.5


class TestCaptureLatencyConditionalPresence:
    """`capture_latency_ms` が GLOBAL_TIME のときだけ現れることを固定する（design.md Validation）。"""

    def test_key_present_when_domain_is_global_time(
        self, logger: FakeLogger, clock: FakeClock
    ) -> None:
        metrics = CaptureMetrics(logger, clock, sysstat=None)
        frame = _make_frame(
            timestamp_domain=TimestampDomain.GLOBAL_TIME,
            capture_latency_ms=42.0,
        )

        metrics.frame(_make_timing(), frame)

        _, _, data = logger.calls[0]
        assert "capture_latency_ms" in data
        assert data["capture_latency_ms"] == 42.0

    @pytest.mark.parametrize(
        "domain",
        [
            TimestampDomain.HARDWARE_CLOCK,
            TimestampDomain.SYSTEM_TIME,
            TimestampDomain.UNKNOWN,
        ],
    )
    def test_key_absent_for_non_global_time_domains(
        self, logger: FakeLogger, clock: FakeClock, domain: TimestampDomain
    ) -> None:
        metrics = CaptureMetrics(logger, clock, sysstat=None)
        # 値そのものは与えても（現実には他ドメインで capture_latency_ms が
        # None であることが多いが、値があってもドメインで判断することを
        # 確認するためあえて値を与える）、キーは絶対に現れてはならない。
        frame = _make_frame(timestamp_domain=domain, capture_latency_ms=99.0)

        metrics.frame(_make_timing(), frame)

        _, _, data = logger.calls[0]
        assert "capture_latency_ms" not in data

    def test_key_absent_when_global_time_but_value_missing(
        self, logger: FakeLogger, clock: FakeClock
    ) -> None:
        metrics = CaptureMetrics(logger, clock, sysstat=None)
        frame = _make_frame(
            timestamp_domain=TimestampDomain.GLOBAL_TIME,
            capture_latency_ms=None,
        )

        metrics.frame(_make_timing(), frame)

        _, _, data = logger.calls[0]
        assert "capture_latency_ms" not in data


class TestResourceSampling:
    """`snapshot_resources()` が間隔でのみサンプルすることを固定する（要件 9.3）。"""

    def test_first_call_samples_unconditionally(
        self, logger: FakeLogger, clock: FakeClock
    ) -> None:
        sysstat = FakeSysstat()
        metrics = CaptureMetrics(logger, clock, sysstat=sysstat, resource_interval_ms=500.0)

        metrics.snapshot_resources()

        assert sysstat.call_count == 1
        resource_events = [c for c in logger.calls if c[1] == "resources"]
        assert len(resource_events) == 1
        stage, event, data = resource_events[0]
        assert stage == "capture"
        assert data["cpu_percent"] == 12.5
        assert data["process_rss_bytes"] == 1024
        assert data["system_available_bytes"] == 2048
        assert data["system_total_bytes"] == 4096

    def test_no_op_before_interval_elapsed(self, logger: FakeLogger, clock: FakeClock) -> None:
        sysstat = FakeSysstat()
        metrics = CaptureMetrics(logger, clock, sysstat=sysstat, resource_interval_ms=500.0)

        metrics.snapshot_resources()  # 1回目: 無条件にサンプル
        clock.advance(499.0)
        metrics.snapshot_resources()  # 2回目: 間隔未経過につき no-op

        assert sysstat.call_count == 1
        resource_events = [c for c in logger.calls if c[1] == "resources"]
        assert len(resource_events) == 1

    def test_samples_again_once_interval_elapsed(
        self, logger: FakeLogger, clock: FakeClock
    ) -> None:
        sysstat = FakeSysstat()
        metrics = CaptureMetrics(logger, clock, sysstat=sysstat, resource_interval_ms=500.0)

        metrics.snapshot_resources()  # 1回目
        clock.advance(500.0)
        metrics.snapshot_resources()  # 2回目: ちょうど間隔経過

        assert sysstat.call_count == 2
        resource_events = [c for c in logger.calls if c[1] == "resources"]
        assert len(resource_events) == 2

    def test_no_op_when_sysstat_is_none(self, logger: FakeLogger, clock: FakeClock) -> None:
        metrics = CaptureMetrics(logger, clock, sysstat=None, resource_interval_ms=500.0)

        metrics.snapshot_resources()

        assert logger.calls == []

    def test_frame_calls_do_not_trigger_resource_sampling(
        self, logger: FakeLogger, clock: FakeClock
    ) -> None:
        """毎フレームではなく間隔でサンプルする（要件 9.3）: frame() 単体では
        リソースはサンプルされない。"""
        sysstat = FakeSysstat()
        metrics = CaptureMetrics(logger, clock, sysstat=sysstat, resource_interval_ms=500.0)

        for i in range(10):
            metrics.frame(_make_timing(), _make_frame(index=i, seq=i))

        assert sysstat.call_count == 0
        assert all(c[1] != "resources" for c in logger.calls)


class TestMeasuredFpsAndDuration:
    """`counters()` の `duration_ms` / `measured_fps` が構築時刻を起点にすることを固定する。"""

    def test_duration_and_fps_reflect_elapsed_time_since_construction(
        self, logger: FakeLogger, clock: FakeClock
    ) -> None:
        metrics = CaptureMetrics(logger, clock, sysstat=None)

        clock.advance(1000.0)  # 構築から1秒経過（frame() 呼び出し前）
        metrics.frame(_make_timing(), _make_frame(index=0, seq=0))
        metrics.frame(_make_timing(), _make_frame(index=1, seq=1))

        stats = metrics.counters()
        assert stats.duration_ms == pytest.approx(1000.0)
        assert stats.measured_fps == pytest.approx(2.0)  # 2 frames / 1s

    def test_zero_duration_yields_zero_fps_without_division_error(
        self, logger: FakeLogger, clock: FakeClock
    ) -> None:
        metrics = CaptureMetrics(logger, clock, sysstat=None)
        stats = metrics.counters()
        assert stats.duration_ms == 0.0
        assert stats.measured_fps == 0.0


class TestNoTargetJudgment:
    """`CaptureStats` の型に「目標充足」を表すブール値フィールドが無いことを固定する（要件 9.5）。"""

    def test_capturestats_fields_are_purely_measured_values(self) -> None:
        field_names = {f for f in CaptureStats.__dataclass_fields__}
        assert field_names == {
            "frames_yielded",
            "frames_dropped",
            "frames_missing",
            "duration_ms",
            "measured_fps",
            "acquire_errors",
        }
