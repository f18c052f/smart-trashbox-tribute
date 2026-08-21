"""`sensing_foundation.bench.logging_overhead` に対するテスト（タスク 7.3）。

ファイル名は design.md の File Structure Plan が示す `test_bench.py` から
意図的に外れ、`test_bench_logging.py` とする——`bench/modes.py`（タスク 7.2）が
既に `test_bench_modes.py` を使っており、design.md 想定の共通ファイルへ両者を
まとめると `test_bench_modes.py` を上書きしてしまう（tasks.md Implementation
Notes「タスク7.2」が本タスクへ明示的に指示する回避方針）。

観測可能な完了状態（tasks.md 7.3）を固定する:

- 合成入力で実行すると、3条件（`logging_off`/`logging_on`/`recording_on`）の
  実測値・判定基準文字列・判定結果を含む結果が得られる
- 判定基準は実測前に固定された文字列であり（`_CRITERION_TEXT`）、2つの判定
  （`logging_on_vs_logging_off` / `recording_on_vs_logging_off`）で同一
- 判定ロジック（`_compute_verdict`）は合成した `OverheadResult` を直接渡して
  単体テストできる（合格・不合格・境界値の3ケース）
- A/B/A/B（3条件版: A/B/C/A/B/C/...）の交互実行が構造的に確認できる
  （`segment_order`）
- 生サンプルから p50/p95/iqr を再計算すると `OverheadResult` の値と一致する
- 非実機（simulated）入力での実行結果には、実機の結論として扱わない旨の
  断り書きが明記される
- 結果は `dataclasses.asdict()` + `json.dumps()` でそのまま直列化できる

要件: 5.6, 10.1, 10.2, 10.3, 10.4, 10.5
"""

from __future__ import annotations

import dataclasses
import json

import pytest
from synthetic import make_supplier

from sensing_foundation.bench.logging_overhead import (
    _CRITERION_TEXT,
    LoggingOverheadBench,
    LoggingOverheadReport,
    OverheadResult,
    _compute_verdict,
    _percentile,
)
from sensing_foundation.config import CaptureConfig, RuntimeSettings
from sensing_foundation.errors import SensingConfigError
from sensing_foundation.types import SourceKind

WIDTH_PX = 8
HEIGHT_PX = 6


def _settings() -> RuntimeSettings:
    return RuntimeSettings(
        source=SourceKind.SIMULATED,
        capture=CaptureConfig(width_px=WIDTH_PX, height_px=HEIGHT_PX, fps=30),
    )


def _make_result(
    condition: str,
    *,
    p50: float,
    p95: float | None = None,
    iqr: float,
    frames_dropped: int = 0,
    samples: int = 100,
    measured_fps: float = 30.0,
) -> OverheadResult:
    return OverheadResult(
        condition=condition,  # type: ignore[arg-type]
        samples=samples,
        total_ms_p50=p50,
        total_ms_p95=p95 if p95 is not None else p50,
        total_ms_iqr=iqr,
        measured_fps=measured_fps,
        frames_dropped=frames_dropped,
    )


# ----------------------------------------------------------------------------
# _compute_verdict(): 純粋関数の単体テスト（フル実行を伴わない検証）
# ----------------------------------------------------------------------------


class TestComputeVerdict:
    def test_criterion_is_the_fixed_precommitted_string(self):
        """判定基準は実測値に関わらず常に同一の固定文字列である（要件 10.3）。"""
        off = _make_result("logging_off", p50=10.0, iqr=2.0)
        on = _make_result("logging_on", p50=10.5, iqr=2.0)
        verdict = _compute_verdict(on, off)
        assert verdict.criterion == _CRITERION_TEXT
        assert "中央値" in verdict.criterion
        assert "四分位範囲" in verdict.criterion
        assert "IQR" in verdict.criterion
        assert "frames_dropped" in verdict.criterion

    def test_passes_when_delta_within_iqr_and_drops_not_increased(self):
        """median_delta_ms(=0.5) < baseline_iqr_ms(=2.0)、drops 増加なし → 合格。"""
        off = _make_result("logging_off", p50=10.0, iqr=2.0, frames_dropped=0)
        on = _make_result("logging_on", p50=10.5, iqr=1.0, frames_dropped=0)
        verdict = _compute_verdict(on, off)
        assert verdict.passed is True
        assert verdict.median_delta_ms == pytest.approx(0.5)
        assert verdict.baseline_iqr_ms == pytest.approx(2.0)

    def test_fails_when_delta_exceeds_iqr(self):
        """median_delta_ms(=5.0) > baseline_iqr_ms(=2.0) → 不合格、要件10.4の文言を含む。"""
        off = _make_result("logging_off", p50=10.0, iqr=2.0, frames_dropped=0)
        on = _make_result("logging_on", p50=15.0, iqr=1.0, frames_dropped=0)
        verdict = _compute_verdict(on, off)
        assert verdict.passed is False
        assert verdict.median_delta_ms == pytest.approx(5.0)
        assert "無条件に有効なものとして扱わない" in verdict.detail

    def test_fails_when_frames_dropped_increases_even_if_delta_within_iqr(self):
        """中央値の差は IQR 以内でも、frames_dropped が増えていれば不合格。"""
        off = _make_result("logging_off", p50=10.0, iqr=2.0, frames_dropped=0)
        on = _make_result("recording_on", p50=10.1, iqr=1.0, frames_dropped=3)
        verdict = _compute_verdict(on, off)
        assert verdict.passed is False
        assert "無条件に有効なものとして扱わない" in verdict.detail

    def test_boundary_delta_exactly_equal_to_iqr_passes(self):
        """「以内」は境界を含む: median_delta_ms == baseline_iqr_ms は合格（`<=`）。"""
        off = _make_result("logging_off", p50=10.0, iqr=2.0, frames_dropped=0)
        on = _make_result("logging_on", p50=12.0, iqr=1.0, frames_dropped=0)  # delta == 2.0
        verdict = _compute_verdict(on, off)
        assert verdict.median_delta_ms == pytest.approx(verdict.baseline_iqr_ms)
        assert verdict.passed is True

    def test_delta_is_absolute_value_on_faster_than_off_still_passes_check(self):
        """ON が OFF より速い（負の差）場合でも絶対値で比較し、歪みとして扱わない。"""
        off = _make_result("logging_off", p50=10.0, iqr=2.0, frames_dropped=0)
        on = _make_result("logging_on", p50=9.5, iqr=1.0, frames_dropped=0)
        verdict = _compute_verdict(on, off)
        assert verdict.median_delta_ms == pytest.approx(0.5)
        assert verdict.passed is True


# ----------------------------------------------------------------------------
# LoggingOverheadBench.__init__(): 構築時検証
# ----------------------------------------------------------------------------


class TestConstruction:
    def test_rejects_non_positive_segment_s(self):
        with pytest.raises(SensingConfigError):
            LoggingOverheadBench(segment_s=0.0, cycles=2)

    def test_rejects_non_positive_cycles(self):
        with pytest.raises(SensingConfigError):
            LoggingOverheadBench(segment_s=0.02, cycles=0)


# ----------------------------------------------------------------------------
# run(): 合成入力での完全実行（実機不要）
# ----------------------------------------------------------------------------


class TestRunAgainstSimulated:
    def _run(self, tmp_path, *, cycles=3, segment_s=0.02) -> LoggingOverheadReport:
        supplier = make_supplier(WIDTH_PX, HEIGHT_PX, list(range(20000)), delay_s=0.0)
        bench = LoggingOverheadBench(segment_s=segment_s, cycles=cycles)
        return bench.run(_settings(), supplier=supplier, work_dir=tmp_path / "work")

    def test_all_three_conditions_present_with_positive_samples(self, tmp_path):
        report = self._run(tmp_path)

        assert len(report.results) == 3
        conditions = {r.condition for r in report.results}
        assert conditions == {"logging_off", "logging_on", "recording_on"}
        for result in report.results:
            assert result.samples > 0, result.condition
            assert result.total_ms_p50 >= 0.0
            assert result.total_ms_p95 >= result.total_ms_p50
            assert result.total_ms_iqr >= 0.0
            assert result.measured_fps > 0.0
            # simulated はドレイン無効に固定される（SimulatedSource の Invariant）。
            assert result.frames_dropped == 0

    def test_two_verdicts_with_shared_fixed_criterion(self, tmp_path):
        report = self._run(tmp_path)

        assert set(report.verdicts) == {
            "logging_on_vs_logging_off",
            "recording_on_vs_logging_off",
        }
        for verdict in report.verdicts.values():
            assert verdict.criterion == _CRITERION_TEXT
            assert isinstance(verdict.passed, bool)

    def test_segment_order_shows_genuine_interleaving_not_sequential_blocks(self, tmp_path):
        """3条件が A/B/C/A/B/C/... の順で実行された構造的証跡（タイミング非依存）。"""
        report = self._run(tmp_path, cycles=4, segment_s=0.01)

        expected = ("logging_off", "logging_on", "recording_on") * 4
        assert report.segment_order == expected
        # 逐次実行（OFF を全部→ON を全部→...）ではないことも明示的に確認する。
        assert report.segment_order != (
            ("logging_off",) * 4 + ("logging_on",) * 4 + ("recording_on",) * 4
        )

    def test_raw_samples_recompute_to_same_aggregates(self, tmp_path):
        """生サンプルから p50/p95/iqr を再計算すると `OverheadResult` の値と一致する（要件10.2）。"""
        report = self._run(tmp_path)

        for result in report.results:
            samples = sorted(report.raw_samples[result.condition])
            assert len(samples) == result.samples
            recomputed_p50 = _percentile(samples, 0.5)
            recomputed_p95 = _percentile(samples, 0.95)
            recomputed_iqr = _percentile(samples, 0.75) - _percentile(samples, 0.25)
            assert recomputed_p50 == pytest.approx(result.total_ms_p50)
            assert recomputed_p95 == pytest.approx(result.total_ms_p95)
            assert recomputed_iqr == pytest.approx(result.total_ms_iqr)

    def test_hardware_disclaimer_present_for_simulated_source(self, tmp_path):
        report = self._run(tmp_path)

        assert report.hardware_disclaimer is not None
        assert "実機" in report.hardware_disclaimer
        assert "simulated" in report.hardware_disclaimer

    def test_recording_on_writes_session_files(self, tmp_path):
        """記録有効条件は実際にセッション記録の4ファイルを書き出す。"""
        report = self._run(tmp_path)
        assert report.results  # サニティ

        sessions_root = tmp_path / "work" / "sessions"
        session_dirs = list(sessions_root.iterdir())
        assert len(session_dirs) == 1
        session_dir = session_dirs[0]
        assert (session_dir / "manifest.json").exists()
        assert (session_dir / "frames.ndjson").exists()
        assert (session_dir / "depth.bin").exists()
        assert (session_dir / "summary.json").exists()

    def test_logging_on_writes_ndjson_log(self, tmp_path):
        report = self._run(tmp_path)
        assert report.results  # サニティ

        logs_dir = tmp_path / "work" / "logs"
        log_files = list(logs_dir.glob("*.ndjson"))
        assert len(log_files) == 1
        assert log_files[0].stat().st_size > 0

    def test_results_are_json_serializable(self, tmp_path):
        report = self._run(tmp_path, cycles=2, segment_s=0.01)

        payload = dataclasses.asdict(report)
        text = json.dumps(payload, allow_nan=False, ensure_ascii=False)
        round_tripped = json.loads(text)
        assert set(round_tripped["verdicts"]) == {
            "logging_on_vs_logging_off",
            "recording_on_vs_logging_off",
        }
        assert round_tripped["hardware_disclaimer"] is not None
        assert len(round_tripped["results"]) == 3
