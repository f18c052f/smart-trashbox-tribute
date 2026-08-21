"""`sensing_foundation.logsummary` に対するテスト（タスク 7.1）。

観測可能な完了状態（tasks.md 7.1）を固定する:

- 構造化ログを行単位でストリーム処理し、段階×イベントごとの件数・代表値・
  分位点（p50/p95）・欠測数を集計する
- **未知の段階名も、集計側の改修なしに集計できる**（下流 Spec が足した段階を
  読める）
- `stages` フィルタで対象段階を絞り込める
- 実機・ハードウェア依存なしに（`StructuredLogger` が書いた NDJSON ファイルを
  読むだけで）実行できる
- 巨大なログでも行単位でストリーム処理する（`f.read()` で全読みしない）
- 空・ほぼ空のログでもクラッシュしない

要件: 8.8, 9.4, 9.7, 12.3
"""

from __future__ import annotations

import ast
import json
import math
import time
from pathlib import Path

import pytest

from sensing_foundation.config import LoggingConfig
from sensing_foundation.logsummary import EventSummary, FieldStats, LogSummary, summarize_log
from sensing_foundation.obslog import StructuredLogger
from sensing_foundation.timebase import SessionClock


@pytest.fixture
def clock() -> SessionClock:
    return SessionClock(session_id="20260821T000000Z-testsess")


@pytest.fixture
def log_config(tmp_path: Path) -> LoggingConfig:
    return LoggingConfig(enabled=True, path=tmp_path / "logs", queue_capacity=256, flush_each_line=True)


def _percentile_reference(values: list[float], fraction: float) -> float:
    """テスト側の独立した手計算用リファレンス実装（線形補間、numpy 'linear' 相当）。"""
    values = sorted(values)
    n = len(values)
    if n == 1:
        return values[0]
    k = (n - 1) * fraction
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return values[int(k)]
    return values[f] * (c - k) + values[c] * (k - f)


class TestRealStructuredLoggerCaptureFrame:
    """実際の `StructuredLogger` が書いた NDJSON を読み、フィールドごとの分位点を計算できることを固定する。"""

    def test_capture_frame_quantiles_are_correct(
        self, log_config: LoggingConfig, clock: SessionClock
    ) -> None:
        logger = StructuredLogger(log_config, clock)
        wait_values = [1.0, 2.0, 3.0, 4.0, 5.0]
        drain_values = [10.0, 20.0, 30.0, 40.0, 50.0]
        handoff_values = [0.5, 1.5, 2.5, 3.5, 4.5]
        for wait, drain, handoff in zip(wait_values, drain_values, handoff_values):
            logger.emit(
                "capture",
                "frame",
                wait_ms=wait,
                drain_ms=drain,
                handoff_ms=handoff,
                total_ms=wait + drain + handoff,
            )
        logger.close(timeout_ms=2000.0)

        path = log_config.path / f"{clock.session_id}.ndjson"
        summary = summarize_log(path)

        key = ("capture", "frame")
        assert key in summary.events
        event_summary = summary.events[key]
        assert isinstance(event_summary, EventSummary)
        assert event_summary.count == 5

        wait_stats = event_summary.fields["wait_ms"]
        assert isinstance(wait_stats, FieldStats)
        assert wait_stats.present == 5
        assert wait_stats.missing == 0
        assert wait_stats.numeric_count == 5

        # 手計算: [1,2,3,4,5] の p50 (線形補間, k=(5-1)*0.5=2.0) -> sorted[2] = 3.0
        assert wait_stats.p50 == pytest.approx(3.0)
        # p95: k=(5-1)*0.95=3.8 -> sorted[3]*(4-3.8) + sorted[4]*(3.8-3) = 4*0.2 + 5*0.8 = 4.8
        assert wait_stats.p95 == pytest.approx(4.8)
        assert wait_stats.minimum == pytest.approx(1.0)
        assert wait_stats.maximum == pytest.approx(5.0)
        assert wait_stats.mean == pytest.approx(3.0)

        # 他のフィールドは独立したリファレンス実装で照合する。
        drain_stats = event_summary.fields["drain_ms"]
        assert drain_stats.p50 == pytest.approx(_percentile_reference(drain_values, 0.5))
        assert drain_stats.p95 == pytest.approx(_percentile_reference(drain_values, 0.95))

        handoff_stats = event_summary.fields["handoff_ms"]
        assert handoff_stats.p50 == pytest.approx(_percentile_reference(handoff_values, 0.5))
        assert handoff_stats.p95 == pytest.approx(_percentile_reference(handoff_values, 0.95))


class TestUnknownStageAggregation:
    """未知の段階名（下流 Spec が足す想定）も改修なしに集計できることを固定する（headline observable-completion）。"""

    def test_unknown_stage_is_aggregated_generically(
        self, log_config: LoggingConfig, clock: SessionClock
    ) -> None:
        logger = StructuredLogger(log_config, clock)
        # "detect" は obslog の RESERVED_STAGES に含まれない、下流 Spec が足す想定の段階名。
        latencies = [12.0, 8.0, 20.0, 15.0]
        for value in latencies:
            logger.emit("detect", "infer", latency_ms=value)
        logger.close(timeout_ms=2000.0)

        path = log_config.path / f"{clock.session_id}.ndjson"
        summary = summarize_log(path)

        key = ("detect", "infer")
        assert key in summary.events
        event_summary = summary.events[key]
        assert event_summary.stage == "detect"
        assert event_summary.event == "infer"
        assert event_summary.count == 4

        latency_stats = event_summary.fields["latency_ms"]
        assert latency_stats.numeric_count == 4
        assert latency_stats.p50 == pytest.approx(_percentile_reference(latencies, 0.5))
        assert latency_stats.p95 == pytest.approx(_percentile_reference(latencies, 0.95))


class TestMissingFieldHandling:
    """同一 (stage, event) 内でフィールドの有無が混在する場合、欠測数が正しく分離して数えられることを固定する。"""

    def test_partial_field_presence_tracks_missing_separately(
        self, log_config: LoggingConfig, clock: SessionClock
    ) -> None:
        logger = StructuredLogger(log_config, clock)
        # capture_latency_ms は metrics.py の設計同様、条件付きでのみ現れる想定。
        logger.emit("capture", "frame", wait_ms=1.0, capture_latency_ms=100.0)
        logger.emit("capture", "frame", wait_ms=2.0, capture_latency_ms=200.0)
        logger.emit("capture", "frame", wait_ms=3.0)
        logger.emit("capture", "frame", wait_ms=4.0)
        logger.close(timeout_ms=2000.0)

        path = log_config.path / f"{clock.session_id}.ndjson"
        summary = summarize_log(path)

        event_summary = summary.events[("capture", "frame")]
        assert event_summary.count == 4

        wait_stats = event_summary.fields["wait_ms"]
        assert wait_stats.present == 4
        assert wait_stats.missing == 0

        latency_stats = event_summary.fields["capture_latency_ms"]
        assert latency_stats.present == 2
        assert latency_stats.missing == 2
        assert latency_stats.numeric_count == 2
        # 欠測は 0 扱いにせず、存在した2値だけで分位点を計算する。
        assert latency_stats.p50 == pytest.approx(_percentile_reference([100.0, 200.0], 0.5))


class TestStagesFilter:
    """`stages` フィルタで対象段階のみを集計できることを固定する。"""

    def test_stages_filter_restricts_to_given_stages(
        self, log_config: LoggingConfig, clock: SessionClock
    ) -> None:
        logger = StructuredLogger(log_config, clock)
        logger.emit("capture", "frame", wait_ms=1.0)
        logger.emit("detect", "infer", latency_ms=5.0)
        logger.close(timeout_ms=2000.0)

        path = log_config.path / f"{clock.session_id}.ndjson"
        summary = summarize_log(path, stages=["capture"])

        assert ("capture", "frame") in summary.events
        assert ("detect", "infer") not in summary.events
        # system (session_start/session_end) も対象外のはず。
        assert all(stage == "capture" for stage, _event in summary.events)

    def test_stages_filter_none_includes_all_stages(
        self, log_config: LoggingConfig, clock: SessionClock
    ) -> None:
        logger = StructuredLogger(log_config, clock)
        logger.emit("capture", "frame", wait_ms=1.0)
        logger.emit("detect", "infer", latency_ms=5.0)
        logger.close(timeout_ms=2000.0)

        path = log_config.path / f"{clock.session_id}.ndjson"
        summary = summarize_log(path, stages=None)

        assert ("capture", "frame") in summary.events
        assert ("detect", "infer") in summary.events
        assert ("system", "session_start") in summary.events
        assert ("system", "session_end") in summary.events


class TestSessionStartEndAggregation:
    """`session_start` / `session_end` が (stage="system", event=...) として自然に集計され、
    かつ `dropped_total` / `log_format_version` の便宜フィールドが取れることを固定する。"""

    def test_session_events_included_and_convenience_fields_populated(
        self, log_config: LoggingConfig, clock: SessionClock
    ) -> None:
        logger = StructuredLogger(log_config, clock)
        logger.emit("capture", "frame", wait_ms=1.0)
        logger.close(timeout_ms=2000.0)

        path = log_config.path / f"{clock.session_id}.ndjson"
        summary = summarize_log(path)

        assert ("system", "session_start") in summary.events
        assert ("system", "session_end") in summary.events
        assert summary.events[("system", "session_start")].count == 1
        assert summary.events[("system", "session_end")].count == 1

        assert summary.log_format_version == 1
        # ドロップは発生していないはず。
        assert summary.dropped_total == 0


class TestEmptyAndNearEmptyLog:
    """空・ほぼ空のログファイルでもクラッシュせず、空の集計結果を返すことを固定する。"""

    def test_empty_file_returns_empty_summary(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.ndjson"
        path.write_text("", encoding="utf-8")

        summary = summarize_log(path)

        assert summary.events == {}
        assert summary.total_lines == 0
        assert summary.dropped_total == 0
        assert summary.log_format_version is None

    def test_whitespace_only_file_returns_empty_summary(self, tmp_path: Path) -> None:
        path = tmp_path / "blank.ndjson"
        path.write_text("\n\n   \n\n", encoding="utf-8")

        summary = summarize_log(path)

        assert summary.events == {}
        assert summary.total_lines == 0

    def test_malformed_lines_are_skipped_not_fatal(self, tmp_path: Path) -> None:
        path = tmp_path / "malformed.ndjson"
        lines = [
            json.dumps({"t_ms": 0.0, "session_id": "s", "stage": "capture", "event": "frame", "seq": 1}),
            "{not valid json",
            json.dumps({"t_ms": 1.0, "session_id": "s", "stage": "capture", "event": "frame", "seq": 2}),
        ]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        summary = summarize_log(path)

        assert summary.events[("capture", "frame")].count == 2
        assert summary.skipped_lines == 1


class TestStreamingBehavior:
    """行単位でストリーム処理し、ファイル全体を一度に読み込まないことを固定する。"""

    def test_module_source_does_not_slurp_whole_file(self) -> None:
        import sensing_foundation.logsummary as logsummary_module

        source = Path(logsummary_module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)

        slurp_calls: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in ("read", "readlines"):
                    slurp_calls.append(node.func.attr)

        assert slurp_calls == [], (
            "summarize_log は行単位でストリーム処理する設計であり、"
            f"f.read()/f.readlines() のような全読み込みを含めてはならない（検出: {slurp_calls}）"
        )

    def test_large_synthetic_log_completes_in_reasonable_time(self, tmp_path: Path) -> None:
        path = tmp_path / "large.ndjson"
        line_count = 20_000
        with path.open("w", encoding="utf-8") as f:
            for i in range(line_count):
                f.write(
                    json.dumps(
                        {
                            "t_ms": float(i),
                            "session_id": "s",
                            "stage": "capture",
                            "event": "frame",
                            "seq": i,
                            "data": {"wait_ms": float(i % 100)},
                        }
                    )
                    + "\n"
                )

        started = time.monotonic()
        summary = summarize_log(path)
        elapsed_s = time.monotonic() - started

        assert summary.events[("capture", "frame")].count == line_count
        # ストリーム処理であれば数万行でも数秒以内に終わるはず（全読み込みでの破綻を検知する粗いガード）。
        assert elapsed_s < 10.0


class TestBoundaryImports:
    """`logsummary.py` が層境界（design.md Boundary: layer 0-6）を守り、
    生の NDJSON をパースするだけで足りるため obslog / source / sources / metrics の
    書き込み機構を import していないことを固定する。"""

    def test_does_not_import_writer_machinery(self) -> None:
        import sensing_foundation.logsummary as logsummary_module

        source = Path(logsummary_module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)

        imported_modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imported_modules.add(alias.name)

        forbidden = {
            "sensing_foundation.obslog",
            "sensing_foundation.source",
            "sensing_foundation.metrics",
        }
        forbidden |= {m for m in imported_modules if m.startswith("sensing_foundation.sources")}

        assert not (imported_modules & forbidden), (
            "logsummary は生 NDJSON を直接パースするログ専用集計器であり、"
            f"書き込み機構への依存を持ってはならない（検出: {imported_modules & forbidden}）"
        )
