"""`sensing_foundation.obslog` に対するテスト（タスク 2.1）。

観測可能な完了状態（tasks.md 2.1）を固定する:

- 1行1イベントの NDJSON 追記形式で、固定キー（`t_ms` / `session_id` /
  `stage` / `event`）と任意の付随値（`data`）が書き出される
- `emit()` は呼び出し側スレッドでキューへ積むだけで、書き込みは専用スレッドが
  行う（呼び出し側は完了を待たない・ブロックしない）
- 有界キューが満杯のときはログを破棄し、破棄件数を数える（取得を優先する）
- 書き込み失敗が本来の処理を中断させない
- 行単位でフラッシュし、`close()` を呼ばなくても既に書いた行は読める
- 無効化時は `NullLogger` に差し替わり、イベントの生成も文字列化も一切行わない
- 先頭行が `session_start`、正常終了時の最終行が `session_end`
- 非有限値（NaN/Infinity）を含む項目は欠測として落とし、直列化を破綻させない
- `NullLogger.timed()` は単一の共有 no-op コンテキストマネージャを返す

要件: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.9, 8.10
"""

from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path

import pytest

from sensing_foundation.config import LoggingConfig
from sensing_foundation.obslog import (
    LOG_FORMAT_VERSION,
    Logger,
    LoggerStats,
    LogEvent,
    NullLogger,
    StageLogger,
    StructuredLogger,
    get_logger,
)
from sensing_foundation.timebase import SessionClock


def _read_lines(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _read_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in _read_lines(path)]


@pytest.fixture
def clock() -> SessionClock:
    return SessionClock(session_id="20260821T000000Z-testsess")


@pytest.fixture
def log_config(tmp_path: Path) -> LoggingConfig:
    return LoggingConfig(enabled=True, path=tmp_path / "logs", queue_capacity=64, flush_each_line=True)


class TestGetLoggerFactory:
    """`get_logger()` が enabled に応じて実装を切り替えることを固定する。"""

    def test_enabled_config_returns_structured_logger(
        self, log_config: LoggingConfig, clock: SessionClock
    ) -> None:
        logger = get_logger(log_config, clock)
        try:
            assert isinstance(logger, StructuredLogger)
            assert logger.enabled is True
        finally:
            logger.close(timeout_ms=1000.0)

    def test_disabled_config_returns_null_logger(
        self, log_config: LoggingConfig, clock: SessionClock
    ) -> None:
        disabled_config = LoggingConfig(
            enabled=False,
            path=log_config.path,
            queue_capacity=log_config.queue_capacity,
            flush_each_line=log_config.flush_each_line,
        )
        logger = get_logger(disabled_config, clock)
        assert isinstance(logger, NullLogger)
        assert logger.enabled is False


class TestNdjsonFormat:
    """1行1イベントの NDJSON・固定キー・任意 data を固定する。"""

    def test_emit_writes_single_ndjson_line_with_fixed_keys(
        self, log_config: LoggingConfig, clock: SessionClock
    ) -> None:
        logger = StructuredLogger(log_config, clock)
        logger.emit("capture", "frame", index=37, wait_ms=31.2)
        logger.close(timeout_ms=1000.0)

        path = log_config.path / f"{clock.session_id}.ndjson"
        events = _read_events(path)

        frame_events = [e for e in events if e["event"] == "frame"]
        assert len(frame_events) == 1
        event = frame_events[0]
        assert event["stage"] == "capture"
        assert event["event"] == "frame"
        assert event["session_id"] == clock.session_id
        assert isinstance(event["t_ms"], (int, float))
        assert event["data"] == {"index": 37, "wait_ms": 31.2}

    def test_first_line_is_session_start_event(
        self, log_config: LoggingConfig, clock: SessionClock
    ) -> None:
        logger = StructuredLogger(log_config, clock)
        logger.emit("capture", "frame", index=1)
        logger.close(timeout_ms=1000.0)

        path = log_config.path / f"{clock.session_id}.ndjson"
        events = _read_events(path)

        first = events[0]
        assert first["stage"] == "system"
        assert first["event"] == "session_start"
        assert first["data"]["log_format_version"] == LOG_FORMAT_VERSION
        assert "wall_ms" in first["data"]

    def test_last_line_on_clean_close_is_session_end_event(
        self, log_config: LoggingConfig, clock: SessionClock
    ) -> None:
        logger = StructuredLogger(log_config, clock)
        logger.emit("capture", "frame", index=1)
        logger.close(timeout_ms=1000.0)

        path = log_config.path / f"{clock.session_id}.ndjson"
        events = _read_events(path)

        last = events[-1]
        assert last["stage"] == "system"
        assert last["event"] == "session_end"
        assert "dropped" in last["data"]

    def test_free_form_stage_names_are_accepted(
        self, log_config: LoggingConfig, clock: SessionClock
    ) -> None:
        # 予約分（system/capture/record）以外の自由な段階名（下流 Spec 想定）も
        # 拒否されずそのまま書き出せることを固定する（要件 8.9）。
        logger = StructuredLogger(log_config, clock)
        logger.emit("detect", "done", latency_ms=12.3)
        logger.close(timeout_ms=1000.0)

        path = log_config.path / f"{clock.session_id}.ndjson"
        events = _read_events(path)
        detect_events = [e for e in events if e["stage"] == "detect"]
        assert len(detect_events) == 1
        assert detect_events[0]["event"] == "done"


class TestFlushPerLine:
    """close() を呼ぶ前でも既に書いた行がファイルから読めることを固定する。"""

    def test_lines_are_readable_before_close_when_flush_each_line(
        self, log_config: LoggingConfig, clock: SessionClock
    ) -> None:
        logger = StructuredLogger(log_config, clock)
        try:
            for i in range(10):
                logger.emit("capture", "frame", index=i)

            path = log_config.path / f"{clock.session_id}.ndjson"

            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                events = _read_events(path)
                frame_events = [e for e in events if e["event"] == "frame"]
                if len(frame_events) >= 10:
                    break
                time.sleep(0.01)

            frame_events = [e for e in _read_events(path) if e["event"] == "frame"]
            assert len(frame_events) == 10
        finally:
            logger.close(timeout_ms=1000.0)


class TestBoundedQueueDropOnFull:
    """有界キュー満杯時にログを破棄し、呼び出し側をブロックしないことを固定する。"""

    def test_burst_beyond_capacity_does_not_block_caller(
        self, tmp_path: Path, clock: SessionClock
    ) -> None:
        tiny_config = LoggingConfig(
            enabled=True, path=tmp_path / "logs", queue_capacity=1, flush_each_line=True
        )
        logger = StructuredLogger(tiny_config, clock)
        try:
            # 書き込みを人為的に止め、writer スレッドが追いつけない状況を作る。
            hold = threading.Event()
            original_write_line = logger._write_line

            def blocking_write_line(line: str) -> None:
                hold.wait(timeout=5.0)
                original_write_line(line)

            logger._write_line = blocking_write_line  # type: ignore[method-assign]

            start = time.monotonic()
            for i in range(2000):
                logger.emit("capture", "frame", index=i)
            elapsed = time.monotonic() - start

            # writer スレッドが完全に止まっていても、2000件の emit() が
            # 十分高速に完了する（呼び出し側がブロックしていない証拠）。
            assert elapsed < 1.0

            hold.set()
        finally:
            logger.close(timeout_ms=2000.0)

        stats = logger.stats()
        assert stats.dropped > 0

    def test_dropped_count_increases_and_file_remains_line_readable(
        self, tmp_path: Path, clock: SessionClock
    ) -> None:
        tiny_config = LoggingConfig(
            enabled=True, path=tmp_path / "logs", queue_capacity=2, flush_each_line=True
        )
        logger = StructuredLogger(tiny_config, clock)

        # 環境の I/O 速度に依存させず確実に破棄を発生させるため、writer を
        # 一時的に止めてから burst を送出する（テスト1と同じ手法）。
        hold = threading.Event()
        original_write_line = logger._write_line

        def blocking_write_line(line: str) -> None:
            hold.wait(timeout=5.0)
            original_write_line(line)

        logger._write_line = blocking_write_line  # type: ignore[method-assign]

        for i in range(500):
            logger.emit("capture", "frame", index=i)

        hold.set()
        logger.close(timeout_ms=2000.0)

        stats_after_close = logger.stats()
        assert stats_after_close.dropped > 0

        path = tiny_config.path / f"{clock.session_id}.ndjson"
        # 生成されたファイルは、破棄が発生していても1行ずつ読み取れる
        # （壊れた行・半端な行が無い）ことを固定する。
        lines = _read_lines(path)
        assert len(lines) > 0
        for line in lines:
            parsed = json.loads(line)  # 壊れていれば例外になる
            assert "t_ms" in parsed
            assert "stage" in parsed
            assert "event" in parsed

        seqs = [json.loads(line)["seq"] for line in lines]
        assert seqs == sorted(seqs)


class TestWriteFailureDoesNotInterruptCaller:
    """書き込み失敗時に本来の処理（emit の呼び出し側）を中断させないことを固定する。"""

    def test_writer_exception_does_not_propagate_to_caller_or_kill_thread(
        self, log_config: LoggingConfig, clock: SessionClock
    ) -> None:
        logger = StructuredLogger(log_config, clock)

        def failing_write_line(line: str) -> None:
            raise OSError("simulated disk failure")

        logger._write_line = failing_write_line  # type: ignore[method-assign]

        # 書き込みが必ず失敗する状態でも emit() 自体は例外を出さない。
        for i in range(5):
            logger.emit("capture", "frame", index=i)

        # writer スレッドが例外で落ちていないことを close() が正常に返ることで確認する。
        logger.close(timeout_ms=1000.0)

        stats = logger.stats()
        assert stats.write_errors >= 5


class TestNonFiniteValuesAreDroppedNotBroken:
    """NaN / Infinity を含む付随値がその項目だけ欠測になることを固定する。"""

    def test_nan_field_is_dropped_but_others_survive_and_no_exception(
        self, log_config: LoggingConfig, clock: SessionClock
    ) -> None:
        logger = StructuredLogger(log_config, clock)
        # emit() 自体が例外を投げないことも合わせて確認する。
        logger.emit("test", "event", weird=float("nan"), fine=1.0, inf_val=float("inf"))
        logger.close(timeout_ms=1000.0)

        path = log_config.path / f"{clock.session_id}.ndjson"
        events = _read_events(path)
        target = next(e for e in events if e["event"] == "event")

        assert "weird" not in target["data"]
        assert "inf_val" not in target["data"]
        assert target["data"]["fine"] == 1.0

    def test_nan_inside_nested_dict_is_dropped(
        self, log_config: LoggingConfig, clock: SessionClock
    ) -> None:
        logger = StructuredLogger(log_config, clock)
        logger.emit("test", "nested", payload={"a": float("nan"), "b": 2.0})
        logger.close(timeout_ms=1000.0)

        path = log_config.path / f"{clock.session_id}.ndjson"
        events = _read_events(path)
        target = next(e for e in events if e["event"] == "nested")

        assert "a" not in target["data"]["payload"]
        assert target["data"]["payload"]["b"] == 2.0


class _PoisonObject:
    """属性アクセス・文字列化のいずれでも例外を送出するテスト用オブジェクト。

    無効化時 (`NullLogger`) の `emit()` / `timed()` が引数に一切触れないことの
    最も強い証拠として使う。
    """

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"poison object attribute {name!r} was touched")

    def __str__(self) -> str:
        raise AssertionError("poison object was stringified")

    def __repr__(self) -> str:
        raise AssertionError("poison object was repr()'d")


class TestDisabledLoggerIsTrueNoOp:
    """無効時にイベント生成も文字列化も行わない（真の no-op）ことを固定する。"""

    def test_null_logger_emit_never_touches_poison_kwarg(self) -> None:
        logger: Logger = NullLogger()
        # 例外が出ないこと自体が「一切触れていない」ことの証拠。
        logger.emit("capture", "frame", poison=_PoisonObject())

    def test_null_logger_timed_never_touches_poison_kwarg(self) -> None:
        logger: Logger = NullLogger()
        with logger.timed("capture", "frame", poison=_PoisonObject()):
            pass

    def test_null_logger_enabled_is_false(self) -> None:
        logger = NullLogger()
        assert logger.enabled is False

    def test_null_logger_timed_returns_single_shared_instance(self) -> None:
        logger = NullLogger()
        cm1 = logger.timed("capture", "frame")
        cm2 = logger.timed("record", "other", some=1)
        assert cm1 is cm2

    def test_null_logger_stats_returns_zeroed_stats(self) -> None:
        logger = NullLogger()
        stats = logger.stats()
        assert stats.dropped == 0
        assert stats.emitted == 0

    def test_null_logger_close_is_safe_and_idempotent(self) -> None:
        logger = NullLogger()
        logger.close()
        logger.close(timeout_ms=10.0)


class TestCloseInvariants:
    """`close()` 後の `emit()` は no-op、二重 close は安全であることを固定する。"""

    def test_emit_after_close_is_noop(
        self, log_config: LoggingConfig, clock: SessionClock
    ) -> None:
        logger = StructuredLogger(log_config, clock)
        logger.close(timeout_ms=1000.0)

        before = logger.stats()
        logger.emit("capture", "frame", index=1)  # クローズ後は no-op
        after = logger.stats()

        assert before == after

    def test_double_close_is_safe(self, log_config: LoggingConfig, clock: SessionClock) -> None:
        logger = StructuredLogger(log_config, clock)
        logger.close(timeout_ms=1000.0)
        logger.close(timeout_ms=1000.0)  # 2回目も例外を出さない


class TestTimedContextManager:
    """`timed()` が区間の所要時間を計測してイベント送出することを固定する。"""

    def test_timed_emits_event_with_duration_ms_on_normal_exit(
        self, log_config: LoggingConfig, clock: SessionClock
    ) -> None:
        logger = StructuredLogger(log_config, clock)
        with logger.timed("capture", "wait", extra=1):
            time.sleep(0.01)
        logger.close(timeout_ms=1000.0)

        path = log_config.path / f"{clock.session_id}.ndjson"
        events = _read_events(path)
        target = next(e for e in events if e["event"] == "wait")

        assert target["data"]["duration_ms"] >= 0.0
        assert target["data"]["extra"] == 1

    def test_timed_still_emits_when_exception_raised_inside_block(
        self, log_config: LoggingConfig, clock: SessionClock
    ) -> None:
        logger = StructuredLogger(log_config, clock)
        with pytest.raises(ValueError):
            with logger.timed("capture", "will_fail"):
                raise ValueError("boom")
        logger.close(timeout_ms=1000.0)

        path = log_config.path / f"{clock.session_id}.ndjson"
        events = _read_events(path)
        assert any(e["event"] == "will_fail" for e in events)


class TestStageLogger:
    """`Logger.stage()` が段階名を束縛した薄いラッパを返すことを固定する。"""

    def test_stage_emit_binds_stage_name(
        self, log_config: LoggingConfig, clock: SessionClock
    ) -> None:
        logger = StructuredLogger(log_config, clock)
        stage_logger: StageLogger = logger.stage("detect")
        stage_logger.emit("done", latency_ms=12.3)
        logger.close(timeout_ms=1000.0)

        path = log_config.path / f"{clock.session_id}.ndjson"
        events = _read_events(path)
        target = next(e for e in events if e["event"] == "done")
        assert target["stage"] == "detect"
        assert target["data"]["latency_ms"] == 12.3

    def test_stage_timed_binds_stage_name(
        self, log_config: LoggingConfig, clock: SessionClock
    ) -> None:
        logger = StructuredLogger(log_config, clock)
        stage_logger = logger.stage("track")
        with stage_logger.timed("update"):
            pass
        logger.close(timeout_ms=1000.0)

        path = log_config.path / f"{clock.session_id}.ndjson"
        events = _read_events(path)
        target = next(e for e in events if e["event"] == "update")
        assert target["stage"] == "track"

    def test_null_logger_stage_is_also_noop(self) -> None:
        logger: Logger = NullLogger()
        stage_logger = logger.stage("detect")
        stage_logger.emit("done", poison=_PoisonObject())


class TestSameTimeBaseAsSessionClock:
    """ログの時刻がフレーム取得時刻と同一の時間基準（SessionClock）で表されることを固定する（要件 8.10）。"""

    def test_t_ms_uses_same_clock_as_session_clock(
        self, log_config: LoggingConfig, clock: SessionClock
    ) -> None:
        logger = StructuredLogger(log_config, clock)
        before = clock.now_ms()
        logger.emit("capture", "frame", index=1)
        after = clock.now_ms()
        logger.close(timeout_ms=1000.0)

        path = log_config.path / f"{clock.session_id}.ndjson"
        events = _read_events(path)
        target = next(e for e in events if e["event"] == "frame")

        assert before <= target["t_ms"] <= after + 50.0  # 余裕を持たせる


class TestLogEventDataclass:
    """公開シンボル `LogEvent` が想定どおりのフィールドを持つことを固定する。"""

    def test_log_event_has_expected_fields(self) -> None:
        event = LogEvent(
            t_ms=1.0, session_id="s", stage="capture", event="frame", seq=1, data={"a": 1}
        )
        assert event.t_ms == 1.0
        assert event.session_id == "s"
        assert event.stage == "capture"
        assert event.event == "frame"
        assert event.seq == 1
        assert event.data == {"a": 1}


class TestLoggerStatsDataclass:
    """`LoggerStats` が破棄件数を必ず持つことを固定する（要件 8.6）。"""

    def test_logger_stats_has_dropped_field(self) -> None:
        stats = LoggerStats(emitted=1, dropped=0, written=1, write_errors=0)
        assert stats.dropped == 0
