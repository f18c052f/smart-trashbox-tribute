"""PredictionLink のテスト（要件3.1, 3.3, 3.4, 3.5, 3.7, 3.9, 7.4 /
design.md「PredictionLink」）。

`trajectory_sim.prediction_link.run_prediction` が以下を満たすことを
固定する:

- 誤差ゼロのサンプル列に対する最終予測が真の落下地点・落下時刻と丸め
  誤差の範囲で一致する（要件3.1, 3.3, 3.7）
- 最小サンプル数未満のみの入力では有効な予測が0件になる（要件3.5）
- 構築された `ThrowRecord.extra` のトップレベルが `"sim"` の1キーのみで
  あり、渡した内容がそのまま素通しされる（要件3.9、design.md
  Preconditions の解釈は `prediction_link.py` docstring 参照）
- 目標更新の `available_time_ms` が
  `based_on_time_ms + sample_latency_ms + prediction_latency_ms` と一致
  する（design.md「PredictionLink」Responsibilities & Constraints）
- `updates` が `available_time_ms` の昇順であること（design.md
  Postconditions）
- `record.source == SourceKind.SIMULATED`、`record.samples` が入力
  サンプル列と一致し、`record.predictions` が `InvalidPrediction` の
  理由を含めてトラッカーの予測系列とそのまま一致すること
- 本モジュールが `prediction_core` のサブモジュールを直接 import して
  いないこと（境界の簡易チェック）

ファイル名は `test_trajectory_sim_prediction_link.py`
（`tests/trajectory_sim/test_trajectory_sim_*.py` の命名方針を踏襲する）。
"""

from __future__ import annotations

import inspect
from pathlib import Path
from random import Random

import pytest
from prediction_core import (
    InvalidPrediction,
    Prediction,
    PredictionConfig,
    Sample,
    SourceKind,
    ThrowPredictionTracker,
)

from trajectory_sim import observation, physics, prediction_link
from trajectory_sim.params import ObservationParams, ThrowDispersion, ThrowParams


def _throw(**overrides: float) -> ThrowParams:
    base: dict[str, float] = dict(
        release_x_mm=0.0,
        release_y_mm=0.0,
        release_z_mm=2000.0,
        speed_mm_s=3000.0,
        elevation_deg=45.0,
        azimuth_deg=30.0,
    )
    base.update(overrides)
    return ThrowParams(**base)


def _observation_params(**overrides: float) -> ObservationParams:
    base: dict[str, float] = dict(
        detection_start_delay_ms=20.0,
        sample_period_ms=20.0,
        sample_latency_ms=5.0,
        prediction_latency_ms=3.0,
    )
    base.update(overrides)
    return ObservationParams(**base)


def _error_free_samples(
    throw: ThrowParams, observation_params: ObservationParams
) -> tuple[tuple[Sample, ...], physics.ImpactPoint]:
    """誤差要因ゼロの `ObservationParams` から、既知の投擲に対するサンプル列を生成する。"""
    trajectory = physics.sample_throw(throw, ThrowDispersion(), Random(0))
    impact = physics.solve_true_impact(trajectory)
    assert impact is not None
    samples = observation.observe(trajectory, impact, observation_params, Random(0))
    return samples, impact


# ---------------------------------------------------------------------------
# 完了条件1: 誤差ゼロのサンプル列 -> 最終予測が真の落下地点・時刻に一致
# ---------------------------------------------------------------------------


def test_run_prediction_final_prediction_matches_true_impact_when_error_free() -> None:
    throw = _throw()
    observation_params = _observation_params()
    samples, impact = _error_free_samples(throw, observation_params)
    config = PredictionConfig()
    assert len(samples) >= config.min_samples

    timeline = prediction_link.run_prediction(
        samples=samples,
        observation=observation_params,
        config=config,
        record_id="cell-0-trial-0",
        extra={"sim": {"sim_extra_version": "1.0", "cell_index": 0, "trial_index": 0}},
    )

    assert timeline.final_prediction is not None
    assert timeline.final_prediction.predicted_hit_x_mm == pytest.approx(impact.x_mm, abs=1e-6)
    assert timeline.final_prediction.predicted_hit_y_mm == pytest.approx(impact.y_mm, abs=1e-6)
    assert timeline.final_prediction.predicted_hit_time_ms == pytest.approx(
        impact.time_ms, abs=1e-6
    )


# ---------------------------------------------------------------------------
# 完了条件2: 最小サンプル数未満 -> 有効な予測0件
# ---------------------------------------------------------------------------


def test_run_prediction_returns_zero_valid_predictions_when_below_min_samples() -> None:
    config = PredictionConfig()
    assert config.min_samples == 3
    samples = (
        Sample(t_ms=0.0, x_mm=0.0, y_mm=0.0, z_mm=2000.0),
        Sample(t_ms=20.0, x_mm=10.0, y_mm=5.0, z_mm=1900.0),
    )
    observation_params = _observation_params()

    timeline = prediction_link.run_prediction(
        samples=samples,
        observation=observation_params,
        config=config,
        record_id="cell-0-trial-1",
        extra={"sim": {"sim_extra_version": "1.0", "cell_index": 0, "trial_index": 1}},
    )

    assert timeline.valid_prediction_count == 0
    assert timeline.updates == ()
    assert timeline.final_prediction is None


# ---------------------------------------------------------------------------
# 完了条件3: extra のトップレベルが "sim" の1キーのみで版フィールドを含む
# ---------------------------------------------------------------------------


def test_run_prediction_extra_passthrough_has_single_sim_namespace_key() -> None:
    throw = _throw()
    observation_params = _observation_params()
    samples, _impact = _error_free_samples(throw, observation_params)
    config = PredictionConfig()
    extra_in = {"sim": {"sim_extra_version": "1.0", "cell_index": 7, "trial_index": 2}}

    timeline = prediction_link.run_prediction(
        samples=samples,
        observation=observation_params,
        config=config,
        record_id="cell-7-trial-2",
        extra=extra_in,
    )

    assert list(timeline.record.extra.keys()) == ["sim"]
    assert timeline.record.extra["sim"] == extra_in["sim"]
    assert "sim_extra_version" in timeline.record.extra["sim"]


# ---------------------------------------------------------------------------
# available_time_ms の算出式
# ---------------------------------------------------------------------------


def test_run_prediction_available_time_ms_matches_formula() -> None:
    throw = _throw()
    observation_params = _observation_params(sample_latency_ms=12.0, prediction_latency_ms=8.0)
    samples, _impact = _error_free_samples(throw, observation_params)
    config = PredictionConfig()

    # 参照用に別のトラッカーを同一入力で駆動し、based_on_time_ms を独立に得る。
    reference_tracker = ThrowPredictionTracker(
        record_id="reference", source=SourceKind.SIMULATED, config=config
    )
    reference_outcomes = [reference_tracker.add_sample(s) for s in samples]
    reference_valid = [o for o in reference_outcomes if isinstance(o, Prediction)]
    assert reference_valid, "テスト前提: 少なくとも1件の有効な予測が必要"

    timeline = prediction_link.run_prediction(
        samples=samples,
        observation=observation_params,
        config=config,
        record_id="cell-0-trial-0",
        extra={"sim": {"sim_extra_version": "1.0", "cell_index": 0, "trial_index": 0}},
    )

    assert len(timeline.updates) == len(reference_valid)
    for update, prediction in zip(timeline.updates, reference_valid):
        expected = (
            prediction.based_on_time_ms
            + observation_params.sample_latency_ms
            + observation_params.prediction_latency_ms
        )
        assert update.available_time_ms == pytest.approx(expected)


# ---------------------------------------------------------------------------
# updates の順序（available_time_ms 昇順）
# ---------------------------------------------------------------------------


def test_run_prediction_updates_are_sorted_ascending_by_available_time_ms() -> None:
    throw = _throw()
    observation_params = _observation_params(sample_period_ms=15.0)
    samples, _impact = _error_free_samples(throw, observation_params)
    config = PredictionConfig()
    assert len(samples) > config.min_samples + 2  # 複数の有効予測が積み上がる構成であること

    timeline = prediction_link.run_prediction(
        samples=samples,
        observation=observation_params,
        config=config,
        record_id="cell-0-trial-0",
        extra={"sim": {"sim_extra_version": "1.0", "cell_index": 0, "trial_index": 0}},
    )

    assert len(timeline.updates) >= 2
    times = [u.available_time_ms for u in timeline.updates]
    assert times == sorted(times)


# ---------------------------------------------------------------------------
# record の中身: source / samples / predictions（InvalidPrediction の理由を含む）
# ---------------------------------------------------------------------------


def test_run_prediction_record_source_and_samples_and_predictions_preserved() -> None:
    # elapsed_ms は計測ごとに変動しうるため無効化し、決定的な比較を可能にする。
    config = PredictionConfig(measure_elapsed=False)
    # 最初の2件は min_samples(3) 未満で InvalidPrediction、3件目以降で Prediction になる構成。
    samples = (
        Sample(t_ms=0.0, x_mm=0.0, y_mm=0.0, z_mm=2000.0),
        Sample(t_ms=20.0, x_mm=42.4, y_mm=24.4, z_mm=1980.0),
        Sample(t_ms=40.0, x_mm=84.8, y_mm=48.8, z_mm=1940.4),
        Sample(t_ms=60.0, x_mm=127.2, y_mm=73.2, z_mm=1880.9),
    )
    observation_params = _observation_params()

    timeline = prediction_link.run_prediction(
        samples=samples,
        observation=observation_params,
        config=config,
        record_id="cell-0-trial-0",
        extra={"sim": {"sim_extra_version": "1.0", "cell_index": 0, "trial_index": 0}},
    )

    assert timeline.record.source == SourceKind.SIMULATED
    assert timeline.record.samples == samples

    # 独立した参照トラッカーで同じ入力を駆動し、予測系列全体が一致することを確認する。
    reference_tracker = ThrowPredictionTracker(
        record_id="reference", source=SourceKind.SIMULATED, config=config
    )
    for s in samples:
        reference_tracker.add_sample(s)
    assert timeline.record.predictions == reference_tracker.predictions

    # 少なくとも1件は InvalidPrediction（サンプル数不足）であり、理由が保持されていること。
    invalid_outcomes = [o for o in timeline.record.predictions if isinstance(o, InvalidPrediction)]
    assert len(invalid_outcomes) >= 1
    for invalid in invalid_outcomes:
        assert invalid.reason is not None


# ---------------------------------------------------------------------------
# 境界: prediction_core のサブモジュール直接 import をしていないこと
# ---------------------------------------------------------------------------


def test_prediction_link_does_not_import_prediction_core_submodules() -> None:
    source_path = Path(inspect.getfile(prediction_link))
    source = source_path.read_text(encoding="utf-8")
    assert "from prediction_core." not in source
    assert "import prediction_core." not in source
