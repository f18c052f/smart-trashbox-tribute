"""`sensing_foundation.source` に対するテスト（タスク 3.1）。

観測可能な完了状態（tasks.md 3.1）を固定する:

- 遅い下流を模した合成入力に対し、**ドレイン有効時のみ**破棄件数
  （`dropped_before` / `CaptureStats.frames_dropped`）が増え、`seq` が
  跳んでも `CaptureFrame.index` は欠番なく増える
- `CaptureFrame.index` は 0 から始まり、`seq` の欠番とは無関係に必ず1ずつ
  増える
- `seq` の飛び（欠落）は跳んだ直後のフレームの `gap_before` に反映され、
  最終統計の `frames_missing` にも合算される
- ドレインで捨てた枚数は `dropped_before` と最終統計の `frames_dropped` の
  両方に反映される
- `on_acquire_error="continue"` では取得失敗を計数して継続し、`"stop"` では
  例外を送出して停止する
- `with source:` は例外時も含めて必ず `stop()` を呼ぶ
- アダプタは `_acquire()` / `_drain_latest()` の2操作のみを実装すればよい
- 入力元固有の引数はコンストラクタにのみ現れ、共通契約
  （`start()` / `frames()` / `stop()`）の引数には現れない
- `stop()` 後、`stats` は破棄・欠落・取得総数・実測フレームレートを含む
  要約として確定する（以後変化しない）

要件: 2.1, 2.2, 2.3, 2.4, 2.5, 2.7, 2.8, 4.1, 4.6
"""

from __future__ import annotations

import inspect

import pytest
from synthetic import make_depth_frame

from sensing_foundation.config import CaptureConfig
from sensing_foundation.errors import SourceUnavailableError
from sensing_foundation.metrics import CaptureMetrics
from sensing_foundation.source import BaseFrameSource, FrameSource, RawFrame
from sensing_foundation.types import (
    CameraIntrinsics,
    CaptureStats,
    SourceKind,
    StreamProfile,
    TimestampDomain,
)

WIDTH_PX = 8
HEIGHT_PX = 6


class FakeLogger:
    """`Logger` プロトコルに構造的に適合する、`emit()` 呼び出しを記録するだけの偽実装。"""

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


def _make_profile() -> StreamProfile:
    intrinsics = CameraIntrinsics(
        width_px=WIDTH_PX,
        height_px=HEIGHT_PX,
        fx_px=50.0,
        fy_px=50.0,
        ppx_px=4.0,
        ppy_px=3.0,
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


def _raw(seq: int) -> RawFrame:
    return RawFrame(seq=seq, depth=make_depth_frame(WIDTH_PX, HEIGHT_PX, seq))


class _Stop:
    """`_acquire()` に「供給が正常に尽きた」ことを表す `StopIteration` を送出させるための番兵。"""


class _FakeSource(BaseFrameSource):
    """`_acquire()` / `_drain_latest()` のみを実装する、テスト専用の最小アダプタ。

    `acquire_script` は `_acquire()` の呼び出しごとに1つずつ消費される。
    要素は `RawFrame`（取得成功）、`None`（取得失敗）、`_Stop`
    （供給の正常な終端 = `StopIteration` を送出）のいずれか。

    `drain_script` は `_drain_latest()` が呼ばれるたびに1つずつ消費される
    `(RawFrame | None, int)` のタプルのリスト。尽きた場合は `(None, 0)`
    （滞留なし）を返す。
    """

    def __init__(
        self,
        *,
        acquire_script: list[object],
        drain_script: list[tuple[RawFrame | None, int]] | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._acquire_script = list(acquire_script)
        self._drain_script = list(drain_script or [])
        self.acquire_calls = 0
        self.drain_calls = 0

    def _acquire(self, timeout_ms: int) -> RawFrame | None:
        self.acquire_calls += 1
        item = self._acquire_script.pop(0)
        if item is _Stop:
            raise StopIteration
        return item  # type: ignore[return-value]

    def _drain_latest(self) -> tuple[RawFrame | None, int]:
        self.drain_calls += 1
        if self._drain_script:
            return self._drain_script.pop(0)
        return (None, 0)


def _make_source(
    *,
    acquire_script: list[object],
    drain_script: list[tuple[RawFrame | None, int]] | None = None,
    drain_enabled: bool = True,
    on_acquire_error: str = "continue",
    logger: FakeLogger | None = None,
    clock: FakeClock | None = None,
) -> tuple[_FakeSource, FakeLogger, FakeClock, CaptureMetrics]:
    logger = logger if logger is not None else FakeLogger()
    clock = clock if clock is not None else FakeClock(start_ms=0.0)
    metrics = CaptureMetrics(logger, clock, sysstat=None)
    capture_config = CaptureConfig(
        drain_enabled=drain_enabled,
        acquire_timeout_ms=1000,
        on_acquire_error=on_acquire_error,  # type: ignore[arg-type]
    )
    source = _FakeSource(
        acquire_script=acquire_script,
        drain_script=drain_script,
        kind=SourceKind.LIVE,
        profile=_make_profile(),
        metrics=metrics,
        clock=clock,
        capture_config=capture_config,
    )
    return source, logger, clock, metrics


class TestSlowConsumerDrainBehavior:
    """観測可能な完了状態: ドレイン有効時のみ破棄件数が増え、index は欠番なく増える。"""

    def test_drain_enabled_increases_discard_count_and_index_stays_gapless(self) -> None:
        # 1回目: acquire で seq=0 を受け取った直後に、滞留していた4枚を捨てて
        # 最新の seq=5 へ追いつく（下流が遅く、その間に供給側が5枚進んだ想定）。
        # 2回目: 滞留なし。3回目: 滞留2枚を捨てて seq=7 -> seq=10 へ追いつく。
        source, _logger, _clock, _metrics = _make_source(
            acquire_script=[_raw(0), _raw(6), _raw(7)],
            drain_script=[(_raw(5), 4), (None, 0), (_raw(10), 2)],
            drain_enabled=True,
        )
        with source:
            it = source.frames()
            f0 = next(it)
            f1 = next(it)
            f2 = next(it)

        # index は 0, 1, 2 と欠番なく増える（seq が 5, 6, 10 と飛んでいても）。
        assert (f0.index, f1.index, f2.index) == (0, 1, 2)
        assert (f0.seq, f1.seq, f2.seq) == (5, 6, 10)

        assert f0.dropped_before == 4
        assert f1.dropped_before == 0
        assert f2.dropped_before == 2

        stats = source.stats
        assert stats.frames_dropped == 4 + 0 + 2
        assert stats.frames_yielded == 3
        assert source.drain_calls == 3

    def test_drain_disabled_never_calls_drain_latest_and_discard_stays_zero(self) -> None:
        # drain_script に滞留データを用意しても、drain_enabled=False なら
        # _drain_latest() 自体が一切呼ばれず、破棄件数は増えない。
        source, _logger, _clock, _metrics = _make_source(
            acquire_script=[_raw(0), _raw(6), _raw(7)],
            drain_script=[(_raw(5), 4), (None, 0), (_raw(10), 2)],
            drain_enabled=False,
        )
        with source:
            it = source.frames()
            f0 = next(it)
            f1 = next(it)
            f2 = next(it)

        assert source.drain_calls == 0
        assert (f0.dropped_before, f1.dropped_before, f2.dropped_before) == (0, 0, 0)
        # ドレインを行わないため raw の seq がそのまま使われる。
        assert (f0.seq, f1.seq, f2.seq) == (0, 6, 7)
        # index は引き続き欠番なく増える。
        assert (f0.index, f1.index, f2.index) == (0, 1, 2)

        stats = source.stats
        assert stats.frames_dropped == 0
        # seq が 0 -> 6 -> 7 と飛ぶため欠落は検出される（破棄とは別カウンタ）。
        assert stats.frames_missing == 5 + 0


class TestIndexAndGapDetection:
    """`index` の欠番なし増加と、`seq` 欠落検出を独立に固定する。"""

    def test_index_starts_at_zero_and_increments_by_one_regardless_of_seq_gaps(self) -> None:
        source, _logger, _clock, _metrics = _make_source(
            acquire_script=[_raw(0), _raw(1), _raw(3), _raw(4)],
            drain_enabled=False,
        )
        with source:
            it = source.frames()
            frames = [next(it) for _ in range(4)]

        assert [f.index for f in frames] == [0, 1, 2, 3]
        assert [f.seq for f in frames] == [0, 1, 3, 4]
        assert [f.gap_before for f in frames] == [0, 0, 1, 0]

        stats = source.stats
        assert stats.frames_missing == 1

    def test_gap_is_zero_for_first_frame_regardless_of_starting_seq(self) -> None:
        source, _logger, _clock, _metrics = _make_source(
            acquire_script=[_raw(42)],
            drain_enabled=False,
        )
        with source:
            frame = next(source.frames())
        assert frame.gap_before == 0
        assert frame.index == 0


class TestDiscardCounting:
    """ドレインで捨てた枚数が per-frame と最終統計の両方に反映されることを固定する。"""

    def test_dropped_before_reflected_in_frame_and_final_stats(self) -> None:
        source, _logger, _clock, _metrics = _make_source(
            acquire_script=[_raw(0)],
            drain_script=[(_raw(3), 3)],
            drain_enabled=True,
        )
        with source:
            frame = next(source.frames())
            assert frame.dropped_before == 3
        assert source.stats.frames_dropped == 3


class TestAcquireErrorHandling:
    """`on_acquire_error` による継続/停止の切り替えを固定する（要件 2.7）。"""

    def test_continue_mode_counts_failure_and_keeps_iterating(self) -> None:
        source, _logger, _clock, metrics = _make_source(
            acquire_script=[None, _raw(0)],
            on_acquire_error="continue",
        )
        with source:
            frame = next(source.frames())

        assert frame.index == 0
        assert frame.seq == 0
        assert source.acquire_calls == 2  # 1回失敗 + 1回成功
        assert metrics.counters().acquire_errors == 1
        assert metrics.counters().frames_yielded == 1

    def test_stop_mode_raises_source_unavailable_error(self) -> None:
        source, _logger, _clock, metrics = _make_source(
            acquire_script=[None],
            on_acquire_error="stop",
        )
        with source:
            with pytest.raises(SourceUnavailableError):
                next(source.frames())

        assert metrics.counters().acquire_errors == 1

    def test_acquire_error_emits_via_metrics(self) -> None:
        source, logger, _clock, _metrics = _make_source(
            acquire_script=[None, _raw(0)],
            on_acquire_error="continue",
        )
        with source:
            next(source.frames())

        error_events = [c for c in logger.calls if c[1] == "acquire_error"]
        assert len(error_events) == 1


class TestContextManagerLifecycle:
    """`with source:` が例外時も含めて `stop()` を呼ぶことを固定する。"""

    def test_enter_returns_self_and_exit_finalizes_stats(self) -> None:
        source, _logger, clock, _metrics = _make_source(acquire_script=[_raw(0)])
        with source as ctx:
            assert ctx is source
            it = source.frames()
            next(it)

        stats_immediately_after = source.stats
        clock.advance(1000.0)
        stats_later = source.stats
        # stop() 後は stats が確定し、時間が進んでも変化しない。
        assert stats_immediately_after == stats_later

    def test_stop_called_even_when_with_block_raises(self) -> None:
        class _StopTracking(_FakeSource):
            def __init__(self, **kwargs: object) -> None:
                super().__init__(**kwargs)  # type: ignore[arg-type]
                self.stop_calls = 0

            def stop(self) -> None:
                self.stop_calls += 1
                super().stop()

        logger = FakeLogger()
        clock = FakeClock()
        metrics = CaptureMetrics(logger, clock, sysstat=None)
        source = _StopTracking(
            acquire_script=[_raw(0)],
            kind=SourceKind.LIVE,
            profile=_make_profile(),
            metrics=metrics,
            clock=clock,
            capture_config=CaptureConfig(),
        )

        class _Boom(Exception):
            pass

        with pytest.raises(_Boom):
            with source:
                raise _Boom("failure inside with block")

        assert source.stop_calls == 1


class TestTwoMethodAdapterContract:
    """アダプタが実装するのは `_acquire()` / `_drain_latest()` の2操作のみでよいことを固定する。"""

    def test_minimal_adapter_overriding_only_the_two_methods_works_end_to_end(self) -> None:
        # _FakeSource はこの2メソッドしかオーバーライドしていない
        # （start/stop/__enter__/__exit__/frames/kind/profile/stats はすべて
        # BaseFrameSource のものをそのまま使う）。
        source, _logger, _clock, _metrics = _make_source(
            acquire_script=[_raw(0), _raw(1)],
            drain_enabled=False,
        )
        with source:
            it = source.frames()
            f0 = next(it)
            f1 = next(it)

        assert f0.index == 0
        assert f1.index == 1
        assert isinstance(source.stats, CaptureStats)

    def test_shared_contract_methods_take_no_extra_arguments(self) -> None:
        # 入力元固有の設定はコンストラクタでのみ受け、共通契約
        # （start / frames / stop）の引数には一切現れないことを確認する。
        for name in ("start", "frames", "stop"):
            sig = inspect.signature(getattr(BaseFrameSource, name))
            params = [p for p in sig.parameters.values() if p.name != "self"]
            assert params == [], f"{name}() は self 以外の引数を持ってはならない: {params}"


class TestSummaryAfterStop:
    """`stop()` 後、`stats` が破棄・欠落・取得総数・実測フレームレートを含む要約として確定する。"""

    def test_stats_includes_all_summary_fields(self) -> None:
        source, _logger, clock, _metrics = _make_source(
            acquire_script=[_raw(0), _raw(2)],
            drain_enabled=False,
        )
        clock.advance(100.0)
        with source:
            it = source.frames()
            next(it)
            clock.advance(50.0)
            next(it)

        stats = source.stats
        assert stats.frames_yielded == 2
        assert stats.frames_missing == 1  # seq 0 -> 2
        assert stats.frames_dropped == 0
        assert stats.acquire_errors == 0
        assert stats.measured_fps >= 0.0
        assert stats.duration_ms >= 0.0

    def test_stats_is_frozen_after_stop_even_if_metrics_would_report_growth(self) -> None:
        source, _logger, clock, metrics = _make_source(
            acquire_script=[_raw(0)],
            drain_enabled=False,
        )
        with source:
            next(source.frames())
        frozen = source.stats

        clock.advance(10_000.0)
        # メトリクス自身の counters() は時間経過で measured_fps / duration_ms
        # が変わり得るが、source.stats は stop() 時点の値のまま。
        live_counters = metrics.counters()
        assert live_counters.duration_ms != frozen.duration_ms or live_counters.duration_ms == 0.0
        assert source.stats == frozen


class TestFrameSourceProtocolShape:
    """`FrameSource` が意図した構造的部分型として `BaseFrameSource` を受理することを固定する。"""

    def test_base_frame_source_satisfies_protocol_members(self) -> None:
        source, _logger, _clock, _metrics = _make_source(acquire_script=[_raw(0)])
        # 構造的部分型: 明示的な継承なしに FrameSource の全メンバを持つ。
        assert hasattr(source, "kind")
        assert hasattr(source, "profile")
        assert hasattr(source, "stats")
        assert callable(source.start)
        assert callable(source.frames)
        assert callable(source.stop)
        assert callable(source.__enter__)
        assert callable(source.__exit__)
        assert source.kind == SourceKind.LIVE

    def test_frame_source_is_defined_as_a_protocol(self) -> None:
        from typing import Protocol

        assert Protocol in FrameSource.__mro__
        for member in ("kind", "profile", "stats", "start", "frames", "stop", "__enter__", "__exit__"):
            assert hasattr(FrameSource, member)


class TestRawFrameShape:
    """本タスクが定義した `RawFrame` の最小形を固定する。"""

    def test_raw_frame_defaults(self) -> None:
        depth = make_depth_frame(WIDTH_PX, HEIGHT_PX, 0)
        raw = RawFrame(seq=0, depth=depth)
        assert raw.seq == 0
        assert raw.device_timestamp_ms is None
        assert raw.timestamp_domain == TimestampDomain.UNKNOWN
        assert raw.capture_latency_ms is None

    def test_captured_frame_carries_raw_timestamp_fields_through(self) -> None:
        depth = make_depth_frame(WIDTH_PX, HEIGHT_PX, 0)
        raw = RawFrame(
            seq=0,
            depth=depth,
            device_timestamp_ms=123.0,
            timestamp_domain=TimestampDomain.GLOBAL_TIME,
            capture_latency_ms=4.5,
        )
        source, _logger, _clock, _metrics = _make_source(acquire_script=[raw], drain_enabled=False)
        with source:
            frame = next(source.frames())

        assert frame.device_timestamp_ms == 123.0
        assert frame.timestamp_domain == TimestampDomain.GLOBAL_TIME
        assert frame.capture_latency_ms == 4.5


class TestEndOfSupplySignal:
    """`_acquire()` が `StopIteration` を送出すると `frames()` が正常終了することを固定する。

    本タスクの境界は `SimulatedSource`（3.2）を含まないため最小限の確認に
    留めるが、拡張点として動作することは本タスクの責務で固定しておく。
    """

    def test_stop_iteration_from_acquire_ends_generator_cleanly(self) -> None:
        source, _logger, _clock, _metrics = _make_source(
            acquire_script=[_raw(0), _raw(1), _Stop],
            drain_enabled=False,
        )
        with source:
            frames = list(source.frames())

        assert [f.index for f in frames] == [0, 1]
        assert source.stats.frames_yielded == 2
