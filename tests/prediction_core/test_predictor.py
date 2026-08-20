"""Predictor の統合検証（タスク 3.1 のスコープ、要件 1.1 / 1.3 / 1.4 / 3.3 / 3.5 / 10.6）。

**このファイルはタスク 3.1 の範囲に限定する。** design.md の Predictor は
6 段階の検証（Sample 型チェック・有限性チェック・min_samples チェック・
縮退・未来側交点・出力有限性チェック）と `elapsed_ms` の実測を要求するが、
タスク 3.1 はそのうち以下だけを実装する。

- 正常系（整形済み・有限・`len(samples) >= config.min_samples` を満たす入力）が
  `fit_trajectory` と `solve_floor_impact` を経て `Prediction` に組み立てられること
- `fit_trajectory` / `solve_floor_impact` がそれぞれネイティブに返す
  `InvalidReason.DEGENERATE_TIME` / `InvalidReason.NO_FUTURE_FLOOR_CROSSING` を
  そのまま `InvalidPrediction` へ伝播すること
- `elapsed_ms` は常に `None`（タスク 3.3 が実測に置き換える）

`MALFORMED_INPUT` / `NON_FINITE_VALUE`（入力側・出力側の両方）/
`INSUFFICIENT_SAMPLES` の検証、検証順序の契約、実測の `elapsed_ms` は
タスク 3.2 / 3.3 でこのファイルに追加される。ここでは意図的にテストしない。
"""

from __future__ import annotations

import inspect
import math
import random

from analytic import KnownTrajectory, analytic_floor_impact, generate_samples

from prediction_core.config import PredictionConfig
from prediction_core.predictor import predict
from prediction_core.types import InvalidPrediction, InvalidReason, Prediction, Sample


def test_predict_returns_prediction_with_eight_fields_for_known_parabola() -> None:
    """既知放物線から、要件 3.3 の 8 フィールドが揃った Prediction が返る。"""
    trajectory = KnownTrajectory(
        x0_mm=100.0,
        vx_mm_s=250.0,
        y0_mm=-50.0,
        vy_mm_s=-30.0,
        z0_mm=800.0,
        vz_mm_s=1500.0,
        gravity_mm_s2=9806.65,
    )
    times_ms = [0.0, 50.0, 120.0, 300.0]
    samples = generate_samples(trajectory, times_ms)
    config = PredictionConfig(gravity_mm_s2=trajectory.gravity_mm_s2)

    outcome = predict(samples, config)

    assert isinstance(outcome, Prediction)
    based_on_time_ms = max(times_ms)
    expected = analytic_floor_impact(trajectory, after_time_ms=based_on_time_ms)
    assert expected is not None

    assert math.isclose(
        outcome.predicted_hit_x_mm, expected.hit_x_mm, rel_tol=1e-6, abs_tol=1e-6
    )
    assert math.isclose(
        outcome.predicted_hit_y_mm, expected.hit_y_mm, rel_tol=1e-6, abs_tol=1e-6
    )
    assert math.isclose(
        outcome.predicted_hit_time_ms, expected.hit_time_ms, rel_tol=1e-9
    )
    assert math.isfinite(outcome.remaining_time_ms)
    assert math.isfinite(outcome.estimated_vx_mm_s)
    assert math.isfinite(outcome.estimated_vy_mm_s)
    assert math.isfinite(outcome.estimated_vz_mm_s)
    assert math.isclose(outcome.residual, 0.0, abs_tol=1e-6)
    # 誤差ゼロの理想放物線であるため丸め誤差の範囲で 0 になる。

    assert outcome.sample_count == len(samples)
    assert outcome.based_on_time_ms == based_on_time_ms
    assert outcome.elapsed_ms is None  # タスク 3.1 時点では常に None（3.3 が置き換える）
    assert outcome.config is config


def test_predict_remaining_time_ms_equals_hit_time_minus_based_on_time() -> None:
    """remaining_time_ms == predicted_hit_time_ms - based_on_time_ms（要件 3.5）。"""
    trajectory = KnownTrajectory(
        x0_mm=-1200.0,
        vx_mm_s=-400.0,
        y0_mm=300.0,
        vy_mm_s=600.0,
        z0_mm=2000.0,
        vz_mm_s=-200.0,
        gravity_mm_s2=1622.0,  # 月面重力相当。test_fitting.py の2番目のフィクスチャと同一。
    )
    times_ms = [1_000.0, 1_030.0, 1_090.0, 1_250.0]
    samples = generate_samples(trajectory, times_ms)
    config = PredictionConfig(gravity_mm_s2=trajectory.gravity_mm_s2)

    outcome = predict(samples, config)

    assert isinstance(outcome, Prediction)
    assert outcome.remaining_time_ms == (
        outcome.predicted_hit_time_ms - outcome.based_on_time_ms
    )


def test_predict_velocity_fields_are_identical_to_trajectory_fields() -> None:
    """予測結果直下の速度成分は trajectory の同名フィールドと厳密に一致する。

    値が同じ計算源からコピーされているだけで、独立に再計算・丸めされて
    いないことをミューテーションに敏感な形で確認する（design.md
    Predictor の Postconditions）。
    """
    trajectory = KnownTrajectory(
        x0_mm=10.0,
        vx_mm_s=333.0,
        y0_mm=-10.0,
        vy_mm_s=-77.0,
        z0_mm=600.0,
        vz_mm_s=900.0,
        gravity_mm_s2=9806.65,
    )
    times_ms = [0.0, 20.0, 55.0, 90.0]
    samples = generate_samples(trajectory, times_ms)
    config = PredictionConfig(gravity_mm_s2=trajectory.gravity_mm_s2)

    outcome = predict(samples, config)

    assert isinstance(outcome, Prediction)
    assert outcome.estimated_vx_mm_s == outcome.trajectory.estimated_vx_mm_s
    assert outcome.estimated_vy_mm_s == outcome.trajectory.estimated_vy_mm_s
    assert outcome.estimated_vz_mm_s == outcome.trajectory.estimated_vz_mm_s


def test_predict_is_order_independent() -> None:
    """同一サンプル集合を並び順を変えて渡しても結果が一致する（要件 1.1 / 1.3）。

    `elapsed_ms` はタスク 3.1 時点で常に `None` であるため両者で自明に
    一致し、フィールド単位の完全な等価性比較（`==`）がそのまま
    「処理時間以外の全フィールド一致」の検証として成立する。
    """
    trajectory = KnownTrajectory(
        x0_mm=-200.0,
        vx_mm_s=150.0,
        y0_mm=80.0,
        vy_mm_s=-40.0,
        z0_mm=900.0,
        vz_mm_s=700.0,
        gravity_mm_s2=9806.65,
    )
    times_ms = [0.0, 15.0, 42.0, 88.0, 130.0]
    samples = generate_samples(trajectory, times_ms)
    config = PredictionConfig(gravity_mm_s2=trajectory.gravity_mm_s2)

    shuffled = list(samples)
    random.Random(7).shuffle(shuffled)
    assert [s.t_ms for s in shuffled] != [s.t_ms for s in samples]  # 前提: 実際に並びが変わっている

    outcome_in_order = predict(samples, config)
    outcome_shuffled = predict(shuffled, config)

    assert isinstance(outcome_in_order, Prediction)
    assert isinstance(outcome_shuffled, Prediction)
    assert outcome_in_order == outcome_shuffled


def test_predict_signature_has_only_samples_and_config() -> None:
    """公開シグネチャは samples と config のみ（要件 1.4）。デバイス固有引数を含めない。"""
    params = inspect.signature(predict).parameters
    assert list(params.keys()) == ["samples", "config"]


def test_predict_returns_same_config_object_not_a_copy() -> None:
    """結果に同梱される config は引数と同一オブジェクトである（要件 10.6）。"""
    trajectory = KnownTrajectory(
        x0_mm=0.0,
        vx_mm_s=0.0,
        y0_mm=0.0,
        vy_mm_s=0.0,
        z0_mm=800.0,
        vz_mm_s=1500.0,
        gravity_mm_s2=9806.65,
    )
    times_ms = [0.0, 10.0, 40.0]
    samples = generate_samples(trajectory, times_ms)
    config = PredictionConfig(gravity_mm_s2=trajectory.gravity_mm_s2)

    outcome = predict(samples, config)

    assert isinstance(outcome, Prediction)
    assert outcome.config is config


def test_predict_propagates_degenerate_time_from_fit_trajectory() -> None:
    """観測時刻が縮退している場合、fit_trajectory の DEGENERATE_TIME をそのまま伝播する（要件 6.2）。"""
    samples = [
        Sample(t_ms=100.0, x_mm=1.0, y_mm=2.0, z_mm=3.0),
        Sample(t_ms=100.0, x_mm=4.0, y_mm=5.0, z_mm=6.0),
        Sample(t_ms=100.0, x_mm=7.0, y_mm=8.0, z_mm=9.0),
    ]
    config = PredictionConfig()

    outcome = predict(samples, config)

    assert isinstance(outcome, InvalidPrediction)
    assert outcome.reason is InvalidReason.DEGENERATE_TIME
    assert outcome.detail  # 短くても空でない人間可読な文字列
    assert outcome.sample_count == len(samples)
    assert outcome.based_on_time_ms == 100.0
    assert outcome.elapsed_ms is None
    assert outcome.config is config


def test_predict_propagates_no_future_floor_crossing_from_solve_floor_impact() -> None:
    """未来側に床面交点が無い場合、solve_floor_impact の NO_FUTURE_FLOOR_CROSSING を伝播する（要件 6.3）。

    観測時点ですでに床下（z0 < 0）にあり、上向きの初速も小さいため
    判別式が負になり、二度と z=0 と交わらない軌道を用いる
    （`tests/prediction_core/test_impact.py` の判別式負のケースと同じ構成）。
    """
    trajectory = KnownTrajectory(
        x0_mm=0.0,
        vx_mm_s=0.0,
        y0_mm=0.0,
        vy_mm_s=0.0,
        z0_mm=-1.0,
        vz_mm_s=10.0,
        gravity_mm_s2=9806.65,
    )
    discriminant = trajectory.vz_mm_s**2 + 2.0 * trajectory.gravity_mm_s2 * trajectory.z0_mm
    assert discriminant < 0.0  # このテストの前提を明示的に固定する

    times_ms = [0.0, 10.0, 20.0]
    samples = generate_samples(trajectory, times_ms)
    config = PredictionConfig(gravity_mm_s2=trajectory.gravity_mm_s2)

    outcome = predict(samples, config)

    assert isinstance(outcome, InvalidPrediction)
    assert outcome.reason is InvalidReason.NO_FUTURE_FLOOR_CROSSING
    assert outcome.detail
    assert outcome.sample_count == len(samples)
    assert outcome.based_on_time_ms == max(times_ms)
    assert outcome.elapsed_ms is None
    assert outcome.config is config
