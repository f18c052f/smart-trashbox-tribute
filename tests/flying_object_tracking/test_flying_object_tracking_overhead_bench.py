"""計測 ON/OFF の比較（`bench/overhead.py`、タスク 7.2）の検証。

design.md「L8-L9: 比較・入口 → OverheadBench」と要件 8.6, 8.7, 8.8 が定める、
タスク本文（verbatim）の観測可能な完了状態を固定する:

- 合成入力で有効・無効の比較が完走する
- 判定基準の説明文と判定結果が結果ファイルに含まれる

## テスト構成の設計判断

`bench/compare.py`（タスク 7.1）の `decide()` と同様、判定基準
（`compute_verdict()`）は実行を経由しない**純粋関数**として切り出してある。
したがって:

- 判定基準そのもの（IQR 以内 / 以内でない / 境界）は、手で組み立てた人工的な
  `OverheadResult` を `compute_verdict()` へ直接与えて単体で検証する
  （`TestComputeVerdictJudgmentRule` 群）。
- 実際の A/B/A/B 交互実行（`OverheadBench.run()`）は、完走することと、
  実際に交互実行されたこと・対象段階名の明示・完了状態（結果ファイルへの
  判定基準説明文＋判定結果の同居）を検証する（他のクラス群）。
- 上流 `sensing_foundation.bench.logging_overhead` の判定基準（`_compute_verdict()`）
  と本モジュールの判定基準が同一の式の形であることを、両方の関数へ同じ
  数値を与えて直接突き合わせるテストで固定する（`TestCriterionShapeMatchesUpstream`）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sensing_foundation import CameraIntrinsics, SourceKind, StreamProfile

import flying_object_tracking.bench.overhead as overhead_module
from flying_object_tracking.bench.overhead import (
    CRITERION_TEXT,
    OverheadBench,
    OverheadReport,
    OverheadResult,
    compute_verdict,
    overhead_output_path,
    write_overhead_result,
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
from flying_object_tracking.errors import TrackingConfigError
from flying_object_tracking.pipeline import TrackingPipeline as RealTrackingPipeline
from flying_object_tracking.types import DetectorKind

DIAMETER_MM = 65.0
BLOB_Z_MM = 1000.0
BACKGROUND_RAW = 6000
WIDTH_PX = 64
HEIGHT_PX = 64

STEP_MM = 15.0
WARMUP_LEN = 3
MOVING_LEN = 4


# ==============================================================================
# ドメイン値のビルダ（他テストファイルとの bare import 衝突を避けるため
# このファイル内に自己完結させる。既存タスクファイルと同じ流儀
# ——`test_flying_object_tracking_detector_comparison.py` 参照）
# ==============================================================================


def _intrinsics() -> CameraIntrinsics:
    return CameraIntrinsics(
        width_px=WIDTH_PX,
        height_px=HEIGHT_PX,
        fx_px=200.0,
        fy_px=200.0,
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


def _settings(*, measurement_enabled: bool = True) -> TrackingSettings:
    return TrackingSettings(
        roi=RoiConfig(z_min_mm=500.0, z_max_mm=5000.0),
        object_model=ObjectModelConfig(diameter_mm=DIAMETER_MM),
        detector=DetectorConfig(
            kind=DetectorKind.DEPTH_BAND,
            open_kernel_px=1,
            close_kernel_px=1,
        ),
        projection=ProjectionConfig(),
        tracker=TrackerConfig(max_step_mm=900.0, max_missing_frames=3, min_points_to_start=1),
        measurement=MeasurementConfig(enabled=measurement_enabled),
    )


def _trajectory(synthetic_module) -> list:
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


def _synthesize(synthetic_module) -> list:
    trajectory = _trajectory(synthetic_module)
    return synthetic_module.synthesize_frames(
        trajectory, _profile(), DIAMETER_MM, background_raw=BACKGROUND_RAW
    )


def _result(
    *,
    condition: str = "measurement_on",
    total_ms_p50: float,
    total_ms_p95: float | None = None,
    total_ms_iqr: float,
    samples: int = 50,
) -> OverheadResult:
    return OverheadResult(
        condition=condition,  # type: ignore[arg-type]
        samples=samples,
        total_ms_p50=total_ms_p50,
        total_ms_p95=total_ms_p95 if total_ms_p95 is not None else total_ms_p50 * 1.5,
        total_ms_iqr=total_ms_iqr,
    )


# ==============================================================================
# 1. 判定規則（純粋関数 compute_verdict()）: タスクの観測可能な完了状態そのもの
# ==============================================================================


class TestComputeVerdictJudgmentRule:
    def test_within_iqr_is_not_significantly_different(self) -> None:
        """ON/OFF 中央値の差が OFF の IQR 以内 → 「有意に変化しない」（passed=True）。"""
        off = _result(condition="measurement_off", total_ms_p50=10.0, total_ms_iqr=2.0)
        on = _result(condition="measurement_on", total_ms_p50=10.5, total_ms_iqr=0.3)  # 差0.5 <= IQR2.0

        verdict = compute_verdict(on, off)

        assert verdict.passed is True
        assert verdict.criterion == CRITERION_TEXT
        assert verdict.median_delta_ms == pytest.approx(0.5)
        assert verdict.baseline_iqr_ms == pytest.approx(2.0)

    def test_outside_iqr_is_flagged_as_significantly_different(self) -> None:
        """差が OFF の IQR を明確に超える → 「有意に変化した」（passed=False）。"""
        off = _result(condition="measurement_off", total_ms_p50=10.0, total_ms_iqr=1.0)
        on = _result(condition="measurement_on", total_ms_p50=25.0, total_ms_iqr=0.3)  # 差15 > IQR1.0

        verdict = compute_verdict(on, off)

        assert verdict.passed is False
        assert verdict.median_delta_ms == pytest.approx(15.0)
        assert verdict.baseline_iqr_ms == pytest.approx(1.0)
        assert "無条件に有効なものとして扱わない" in verdict.detail

    def test_boundary_equal_to_iqr_passes(self) -> None:
        """「以内」は境界を含む（`<=`）。差が IQR とちょうど等しい場合は合格。"""
        off = _result(condition="measurement_off", total_ms_p50=10.0, total_ms_iqr=5.0)
        on = _result(condition="measurement_on", total_ms_p50=15.0, total_ms_iqr=0.1)  # 差5 == IQR5

        verdict = compute_verdict(on, off)

        assert verdict.passed is True

    def test_delta_is_absolute_value_on_faster_than_off_still_passes_when_within_iqr(
        self,
    ) -> None:
        """ON が OFF より速くなるケースも、差の絶対値で判定する（符号を問わない）。"""
        off = _result(condition="measurement_off", total_ms_p50=10.0, total_ms_iqr=2.0)
        on = _result(condition="measurement_on", total_ms_p50=9.0, total_ms_iqr=0.1)  # ON の方が速い

        verdict = compute_verdict(on, off)

        assert verdict.median_delta_ms == pytest.approx(1.0)
        assert verdict.passed is True

    def test_criterion_text_is_fixed_and_independent_of_measured_values(self) -> None:
        """判定基準の説明文は実測値に依存しない固定文字列である（実測前に確定）。"""
        off_a = _result(condition="measurement_off", total_ms_p50=1.0, total_ms_iqr=1.0)
        on_a = _result(condition="measurement_on", total_ms_p50=1.0, total_ms_iqr=1.0)
        off_b = _result(condition="measurement_off", total_ms_p50=999.0, total_ms_iqr=0.001)
        on_b = _result(condition="measurement_on", total_ms_p50=1.0, total_ms_iqr=1.0)

        verdict_a = compute_verdict(on_a, off_a)
        verdict_b = compute_verdict(on_b, off_b)

        assert verdict_a.criterion == verdict_b.criterion == CRITERION_TEXT

    def test_verdict_is_recomputable_from_raw_results(self) -> None:
        """`compute_verdict()` は純粋関数であり、同一入力から同一の判定を再現できる。"""
        off = _result(condition="measurement_off", total_ms_p50=10.0, total_ms_iqr=2.0)
        on = _result(condition="measurement_on", total_ms_p50=10.5, total_ms_iqr=0.3)

        first = compute_verdict(on, off)
        second = compute_verdict(on, off)

        assert first == second


# ==============================================================================
# 2. 上流との判定基準の形の一致（design.md「同じ問いに違う基準を使わない」）
# ==============================================================================


class TestCriterionShapeMatchesUpstream:
    def test_same_numeric_verdict_as_upstream_logging_overhead_bench(self) -> None:
        """上流 sensing_foundation.bench.logging_overhead の `_compute_verdict()`

        （実際にソースを読んで確認した判定式: `median_delta_ms =
        abs(on.total_ms_p50 - off.total_ms_p50)`、`baseline_iqr_ms =
        off.total_ms_iqr`、`passed = median_delta_ms <= baseline_iqr_ms`）と、
        本モジュールの `compute_verdict()` へ同じ数値を与えると、同一の
        `median_delta_ms` / `baseline_iqr_ms` / `passed` が得られることを
        直接突き合わせて確認する（モジュール docstring「判定基準の形を上流と
        揃える」の実装が主張どおりであることの回帰テスト）。

        上流側の `frames_dropped` 条件は両条件で同値にし、判定へ影響しない
        ようにする（本モジュールにはこの capture 層固有の概念が無いことは
        意図的な差分——モジュール docstring 参照）。
        """
        upstream = pytest.importorskip(
            "sensing_foundation.bench.logging_overhead",
            reason="上流 sensing_foundation.bench.logging_overhead が無い",
        )

        scenarios = [
            # (off_p50, off_iqr, on_p50) の3つ組。
            (10.0, 2.0, 10.5),  # 差0.5 <= IQR2.0 → 両方 passed=True のはず
            (10.0, 1.0, 25.0),  # 差15 > IQR1.0 → 両方 passed=False のはず
            (10.0, 5.0, 15.0),  # 差5 == IQR5 境界 → 両方 passed=True のはず
        ]

        for off_p50, off_iqr, on_p50 in scenarios:
            upstream_off = upstream.OverheadResult(
                condition="logging_off",
                samples=10,
                total_ms_p50=off_p50,
                total_ms_p95=off_p50 * 1.5,
                total_ms_iqr=off_iqr,
                measured_fps=30.0,
                frames_dropped=0,
            )
            upstream_on = upstream.OverheadResult(
                condition="logging_on",
                samples=10,
                total_ms_p50=on_p50,
                total_ms_p95=on_p50 * 1.5,
                total_ms_iqr=0.1,
                measured_fps=30.0,
                frames_dropped=0,  # 上流条件と同値。frames_dropped 条件を無効化する
            )
            upstream_verdict = upstream._compute_verdict(upstream_on, upstream_off)

            own_off = _result(condition="measurement_off", total_ms_p50=off_p50, total_ms_iqr=off_iqr)
            own_on = _result(condition="measurement_on", total_ms_p50=on_p50, total_ms_iqr=0.1)
            own_verdict = compute_verdict(own_on, own_off)

            assert own_verdict.passed == upstream_verdict.passed
            assert own_verdict.median_delta_ms == pytest.approx(upstream_verdict.median_delta_ms)
            assert own_verdict.baseline_iqr_ms == pytest.approx(upstream_verdict.baseline_iqr_ms)


# ==============================================================================
# 3. A/B/A/B 交互実行: タスクの観測可能な完了状態そのもの
# ==============================================================================


class TestAlternatingExecution:
    def test_segments_genuinely_alternate_not_block_then_block(
        self, monkeypatch, synthetic_module, tmp_path: Path
    ) -> None:
        """実行順が本当に交互（OFF, ON, OFF, ON, ...）であり、「OFF を全部やって

        から ON を全部やる」のような一括実行になっていないことを、
        `TrackingPipeline` の構築順を監視するスパイで確認する（report 自身が
        主張する `segment_order` だけに頼らない独立した検証）。
        """
        construction_log: list[str] = []

        class _SpyPipeline(RealTrackingPipeline):
            def __init__(self, settings, logger):  # noqa: D401 - スパイ用オーバーライド
                construction_log.append(
                    "measurement_on" if settings.measurement.enabled else "measurement_off"
                )
                super().__init__(settings, logger)

        monkeypatch.setattr(overhead_module, "TrackingPipeline", _SpyPipeline)

        frames = _synthesize(synthetic_module)
        cycles = 3
        bench = OverheadBench(cycles=cycles)

        report = bench.run(frames, _settings(), work_dir=tmp_path / "work")

        expected_order = ("measurement_off", "measurement_on") * cycles
        assert report.segment_order == expected_order
        assert tuple(construction_log) == expected_order

        # 明示的に「交互」であることを確認する（連続する2件ごとに OFF, ON）。
        for i in range(0, len(report.segment_order), 2):
            assert report.segment_order[i] == "measurement_off"
            assert report.segment_order[i + 1] == "measurement_on"

    def test_each_condition_processes_the_same_input_the_same_number_of_times(
        self, synthetic_module, tmp_path: Path
    ) -> None:
        """同一入力・同一時間: 両条件とも `cycles * len(frames)` 件のサンプルを持つ。"""
        frames = _synthesize(synthetic_module)
        cycles = 2
        bench = OverheadBench(cycles=cycles)

        report = bench.run(frames, _settings(), work_dir=tmp_path / "work")

        assert report.results[0].condition == "measurement_off"
        assert report.results[1].condition == "measurement_on"
        assert report.results[0].samples == cycles * len(frames)
        assert report.results[1].samples == cycles * len(frames)
        assert len(report.raw_samples["measurement_off"]) == cycles * len(frames)
        assert len(report.raw_samples["measurement_on"]) == cycles * len(frames)

    def test_cycles_less_than_one_is_rejected(self) -> None:
        with pytest.raises(TrackingConfigError):
            OverheadBench(cycles=0)

    def test_empty_frames_is_rejected(self) -> None:
        bench = OverheadBench(cycles=1)
        with pytest.raises(TrackingConfigError):
            bench.run([], _settings(), work_dir=Path("unused"))


# ==============================================================================
# 4. 対象段階名の明示: 上流の取得区間比較との混同防止（design.md「Risks」）
# ==============================================================================


class TestStageNameClarity:
    def test_report_explicitly_names_detect_and_track_stages(
        self, synthetic_module, tmp_path: Path
    ) -> None:
        frames = _synthesize(synthetic_module)
        settings = _settings()
        bench = OverheadBench(cycles=1)

        report = bench.run(frames, settings, work_dir=tmp_path / "work")

        assert report.stage_names == (settings.measurement.detect_stage, settings.measurement.track_stage)
        assert report.stage_names == ("detect", "track")
        assert "detect" in report.note
        assert "track" in report.note
        # 上流の capture 区間比較と混同しない旨が明示されている。
        assert "capture" in report.note

    def test_stage_names_reflect_custom_configured_stage_names(
        self, synthetic_module, tmp_path: Path
    ) -> None:
        """既定でない stage 名を設定した場合も、その値がそのまま反映される。"""
        frames = _synthesize(synthetic_module)
        settings = TrackingSettings(
            roi=RoiConfig(z_min_mm=500.0, z_max_mm=5000.0),
            object_model=ObjectModelConfig(diameter_mm=DIAMETER_MM),
            detector=DetectorConfig(kind=DetectorKind.DEPTH_BAND, open_kernel_px=1, close_kernel_px=1),
            projection=ProjectionConfig(),
            tracker=TrackerConfig(max_step_mm=900.0, max_missing_frames=3, min_points_to_start=1),
            measurement=MeasurementConfig(
                enabled=True, detect_stage="custom_detect", track_stage="custom_track"
            ),
        )
        bench = OverheadBench(cycles=1)

        report = bench.run(frames, settings, work_dir=tmp_path / "work")

        assert report.stage_names == ("custom_detect", "custom_track")
        assert "custom_detect" in report.note
        assert "custom_track" in report.note


# ==============================================================================
# 5. 結合レベルの完走テスト（合成入力での比較の完走と、結果ファイルへの
#    判定基準説明文＋判定結果の同居。タスクの観測可能な完了状態そのもの）
# ==============================================================================


class TestEndToEndSmoke:
    def test_synthetic_ab_comparison_completes_and_result_file_has_criterion_and_verdict(
        self, synthetic_module, tmp_path: Path
    ) -> None:
        frames = _synthesize(synthetic_module)
        bench = OverheadBench(cycles=2)

        report = bench.run(frames, _settings(), work_dir=tmp_path / "work")

        assert isinstance(report, OverheadReport)
        assert report.verdict.criterion == CRITERION_TEXT
        assert isinstance(report.verdict.passed, bool)
        # 判定基準の説明文と判定結果は同一オブジェクト（report.verdict）に同居する。
        assert report.verdict.criterion and report.verdict.detail

        output_root = tmp_path / "var" / "tracking"
        session_id = "overhead-smoke-session"
        written_path = write_overhead_result(report, output_root, session_id)

        assert written_path == overhead_output_path(output_root, session_id)
        assert written_path.exists()

        payload = json.loads(written_path.read_text(encoding="utf-8"))
        assert payload["verdict"]["criterion"] == CRITERION_TEXT
        assert "passed" in payload["verdict"]
        assert payload["stage_names"] == ["detect", "track"]
        assert len(payload["results"]) == 2
        assert payload["segment_order"] == ["measurement_off", "measurement_on"] * 2

    def test_source_kind_of_synthetic_frames_is_not_live(self, synthetic_module) -> None:
        """合成入力で得た比較を実機の結論として扱わない旨は、上位（compare.py

        の `provisional` 相当）の関心であり本ベンチ自体の直接の責務では
        ないが、少なくとも合成入力のフレームが `SourceKind.LIVE` でない
        ことを確認しておく（実機なしで検証している事実の裏付け。要件 11.5
        の精神）。
        """
        frames = _synthesize(synthetic_module)
        assert frames[0].source != SourceKind.LIVE
