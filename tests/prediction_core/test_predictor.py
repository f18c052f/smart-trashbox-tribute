"""Predictor の統合検証（タスク 3.1 / 3.2 のスコープ、要件 1.1 / 1.3 / 1.4 / 3.3 / 3.5 / 6.1-6.8 / 10.6）。

**このファイルはタスク 3.1 / 3.2 の範囲に限定する。** design.md の Predictor は
6 段階の検証（Sample 型チェック・有限性チェック・min_samples チェック・
縮退・未来側交点・出力有限性チェック）と `elapsed_ms` の実測を要求する。

タスク 3.1 が実装したもの:

- 正常系（整形済み・有限・`len(samples) >= config.min_samples` を満たす入力）が
  `fit_trajectory` と `solve_floor_impact` を経て `Prediction` に組み立てられること
- `fit_trajectory` / `solve_floor_impact` がそれぞれネイティブに返す
  `InvalidReason.DEGENERATE_TIME` / `InvalidReason.NO_FUTURE_FLOOR_CROSSING` を
  そのまま `InvalidPrediction` へ伝播すること

タスク 3.2 が追加したもの:

- 6 段階の検証順序のうち、残る 4 段階
  (`MALFORMED_INPUT` -> `NON_FINITE_VALUE`(入力) -> `INSUFFICIENT_SAMPLES` -> ...
  ... -> `NON_FINITE_VALUE`(出力))
- 複数条件が同時に成立する入力でも、返る理由が検証順序どおり決定的になること
- どの失敗でも例外を送出せず、無効予測を値として返すこと
- 無効理由に人が読める文脈（サンプル数・時刻範囲など）を添えること

`elapsed_ms` の実測はタスク 3.3 でこのファイルに追加される。ここでは意図的に
テストしない（本ファイル中は常に `None` を期待する）。
"""

from __future__ import annotations

import inspect
import math
import random
from unittest.mock import patch

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


# ---------------------------------------------------------------------------
# タスク 3.2: 無効判定5種と判定順序（要件 6.1-6.8）
# ---------------------------------------------------------------------------


def test_predict_returns_malformed_input_for_non_sample_element() -> None:
    """要素に Sample でないものが混ざっている場合、単独条件として MALFORMED_INPUT になる（要件 6.5）。

    他の条件（サンプル数・有限性）は満たしているため、この条件だけが
    原因であることを固定する。件数は既定の min_samples（3）ちょうどに
    そろえ、INSUFFICIENT_SAMPLES が同時に成立しないようにする。
    """
    samples = [
        Sample(t_ms=0.0, x_mm=1.0, y_mm=2.0, z_mm=3.0),
        Sample(t_ms=10.0, x_mm=4.0, y_mm=5.0, z_mm=6.0),
        "not-a-sample",
    ]
    config = PredictionConfig()

    outcome = predict(samples, config)  # type: ignore[arg-type]

    assert isinstance(outcome, InvalidPrediction)
    assert outcome.reason is InvalidReason.MALFORMED_INPUT
    assert str(len(samples)) in outcome.detail
    assert outcome.sample_count == len(samples)
    assert outcome.based_on_time_ms is None
    assert outcome.elapsed_ms is None
    assert outcome.config is config


def test_predict_returns_non_finite_value_for_non_finite_input_field() -> None:
    """入力サンプルのいずれかのフィールドが非有限の場合、単独条件として NON_FINITE_VALUE になる（要件 6.4）。

    全要素が `Sample` 型であり、件数も min_samples ちょうどであるため、
    非有限値のみが原因であることを固定する。
    """
    samples = [
        Sample(t_ms=0.0, x_mm=1.0, y_mm=2.0, z_mm=3.0),
        Sample(t_ms=10.0, x_mm=float("nan"), y_mm=5.0, z_mm=6.0),
        Sample(t_ms=20.0, x_mm=7.0, y_mm=8.0, z_mm=9.0),
    ]
    config = PredictionConfig()

    outcome = predict(samples, config)

    assert isinstance(outcome, InvalidPrediction)
    assert outcome.reason is InvalidReason.NON_FINITE_VALUE
    assert str(len(samples)) in outcome.detail
    assert outcome.sample_count == len(samples)
    assert outcome.based_on_time_ms == max(s.t_ms for s in samples)
    assert outcome.elapsed_ms is None
    assert outcome.config is config


def test_predict_returns_insufficient_samples_for_too_few_samples() -> None:
    """有効な観測サンプルが min_samples 未満の場合、単独条件として INSUFFICIENT_SAMPLES になる（要件 6.1）。

    全要素が `Sample` 型かつ全フィールド有限であるため、サンプル数不足
    のみが原因であることを固定する。
    """
    samples = [
        Sample(t_ms=0.0, x_mm=1.0, y_mm=2.0, z_mm=3.0),
        Sample(t_ms=10.0, x_mm=4.0, y_mm=5.0, z_mm=6.0),
    ]
    config = PredictionConfig()
    assert len(samples) < config.min_samples  # このテストの前提を明示的に固定する

    outcome = predict(samples, config)

    assert isinstance(outcome, InvalidPrediction)
    assert outcome.reason is InvalidReason.INSUFFICIENT_SAMPLES
    assert str(len(samples)) in outcome.detail
    assert str(config.min_samples) in outcome.detail
    assert outcome.sample_count == len(samples)
    assert outcome.based_on_time_ms == max(s.t_ms for s in samples)
    assert outcome.elapsed_ms is None
    assert outcome.config is config


def test_predict_malformed_input_wins_over_insufficient_samples_when_both_hold() -> None:
    """MALFORMED_INPUT と INSUFFICIENT_SAMPLES が同時に成立する場合、検証順序どおり MALFORMED_INPUT が勝つ（要件 6.5 > 6.1）。

    件数は min_samples（3）未満であり、かつ Sample でない要素を含む。
    契約上の判定順序（1: 型 -> 3: 件数）から MALFORMED_INPUT が返るべきで
    あり、INSUFFICIENT_SAMPLES ではないことを固定する。
    """
    samples = [
        Sample(t_ms=0.0, x_mm=1.0, y_mm=2.0, z_mm=3.0),
        "not-a-sample",
    ]
    config = PredictionConfig()
    assert len(samples) < config.min_samples  # このテストの前提を明示的に固定する

    outcome = predict(samples, config)  # type: ignore[arg-type]

    assert isinstance(outcome, InvalidPrediction)
    assert outcome.reason is InvalidReason.MALFORMED_INPUT


def test_predict_non_finite_value_wins_over_insufficient_samples_when_both_hold() -> None:
    """NON_FINITE_VALUE と INSUFFICIENT_SAMPLES が同時に成立する場合、検証順序どおり NON_FINITE_VALUE が勝つ（要件 6.4 > 6.1）。

    件数は min_samples（3）未満であり、かつ非有限フィールドを含む。
    契約上の判定順序（2: 有限性 -> 3: 件数）から NON_FINITE_VALUE が返る
    べきであり、INSUFFICIENT_SAMPLES ではないことを固定する。
    """
    samples = [
        Sample(t_ms=0.0, x_mm=1.0, y_mm=2.0, z_mm=3.0),
        Sample(t_ms=10.0, x_mm=float("inf"), y_mm=5.0, z_mm=6.0),
    ]
    config = PredictionConfig()
    assert len(samples) < config.min_samples  # このテストの前提を明示的に固定する

    outcome = predict(samples, config)

    assert isinstance(outcome, InvalidPrediction)
    assert outcome.reason is InvalidReason.NON_FINITE_VALUE


def test_predict_insufficient_samples_short_circuits_before_fit_trajectory() -> None:
    """サンプル数不足の場合、`fit_trajectory` は一度も呼ばれない（判定順序が真に短絡することの証明）。

    reason が正しいだけでは「たまたま正しい出力になった」可能性を排除
    できないため、`fit_trajectory` をスパイして未呼び出しを直接確認する。
    """
    samples = [
        Sample(t_ms=0.0, x_mm=1.0, y_mm=2.0, z_mm=3.0),
        Sample(t_ms=10.0, x_mm=4.0, y_mm=5.0, z_mm=6.0),
    ]
    config = PredictionConfig()
    assert len(samples) < config.min_samples  # このテストの前提を明示的に固定する

    with patch("prediction_core.predictor.fit_trajectory") as spy_fit_trajectory:
        outcome = predict(samples, config)

    assert isinstance(outcome, InvalidPrediction)
    assert outcome.reason is InvalidReason.INSUFFICIENT_SAMPLES
    spy_fit_trajectory.assert_not_called()


def test_predict_empty_samples_returns_insufficient_samples() -> None:
    """空列は MALFORMED_INPUT や NON_FINITE_VALUE ではなく INSUFFICIENT_SAMPLES になる（要件 6.1）。

    空列は「全要素が Sample である」「全フィールドが有限である」を
    空虚な真として満たすため、判定順序上そのまま素通りして
    件数不足のみで無効になることを固定する。また `based_on_time_ms` の
    算出が空列で例外にならないことも確認する。
    """
    samples: list[Sample] = []
    config = PredictionConfig()

    outcome = predict(samples, config)

    assert isinstance(outcome, InvalidPrediction)
    assert outcome.reason is InvalidReason.INSUFFICIENT_SAMPLES
    assert outcome.sample_count == 0
    assert outcome.based_on_time_ms is None
    assert outcome.elapsed_ms is None
    assert outcome.config is config


def test_predict_returns_non_finite_value_for_non_finite_output() -> None:
    """有限な入力でも算出結果が非有限になり得る現実的なケースで NON_FINITE_VALUE になる（要件 6.4、出力側）。

    z 方向の初速を float64 の実用上限近く（1e160 mm/s）まで大きくすると、
    入力サンプル自体は有限のまま、`fit_trajectory` の残差計算は有限に
    収まる一方、`solve_floor_impact` 内の判別式 `b*b`（b = -vz）が
    `1e160**2 ~ 1e320` で float64 の最大値（約1.8e308）を超えて `inf` に
    桁あふれし、`FloorImpact.hit_time_ms` が `inf` に、`hit_x_mm` /
    `hit_y_mm` が `0.0 * inf` で `nan` になる（下位2モジュールをモック
    せずに実際に踏むことを事前に手計算で確認済み）。
    """
    trajectory = KnownTrajectory(
        x0_mm=100.0,
        vx_mm_s=0.0,
        y0_mm=0.0,
        vy_mm_s=0.0,
        z0_mm=800.0,
        vz_mm_s=1e160,
        gravity_mm_s2=9806.65,
    )
    times_ms = [0.0, 10.0, 20.0]
    samples = generate_samples(trajectory, times_ms)
    assert all(
        math.isfinite(v) for s in samples for v in (s.t_ms, s.x_mm, s.y_mm, s.z_mm)
    )  # このテストの前提: 入力自体は有限のまま
    config = PredictionConfig(gravity_mm_s2=trajectory.gravity_mm_s2)

    outcome = predict(samples, config)

    assert isinstance(outcome, InvalidPrediction)
    assert outcome.reason is InvalidReason.NON_FINITE_VALUE
    assert outcome.detail
    assert outcome.sample_count == len(samples)
    assert outcome.based_on_time_ms == max(times_ms)
    assert outcome.elapsed_ms is None
    assert outcome.config is config
