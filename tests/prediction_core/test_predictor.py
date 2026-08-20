"""Predictor の統合検証（タスク 3.1-3.3、要件 1.1 / 1.3 / 1.4 / 3.3 / 3.5 / 6.1-6.8 / 8.1-8.5 / 10.6）。

design.md の Predictor は 6 段階の検証（Sample 型チェック・有限性チェック・
min_samples チェック・縮退・未来側交点・出力有限性チェック）と `elapsed_ms`
の実測を要求する。

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

タスク 3.3 が追加したもの（本ファイル末尾のセクション）:

- `config.measure_elapsed` が真のとき、成功・失敗の両方で `elapsed_ms` が
  実測された有限の非負値になること（要件 8.1）
- `config.measure_elapsed` が偽のとき、`time.perf_counter_ns` の呼び出し
  自体が行われず `elapsed_ms` が `None` のまま返ること（要件 8.3）
- 7 箇所ある `InvalidPrediction` / `Prediction` の組み立て地点すべてで
  実測が反映されること（部分的な取りこぼしがないこと）

タスク 3.1 / 3.2 のテストは意図的に `elapsed_ms is None` を固定していたが、
これはタスク 3.2 時点での正しい挙動であり、本タスクの実装後もそれらの
アサーションは（`config.measure_elapsed` を指定していない = 既定値 True の
はずなのに None を期待している点で）矛盾するように見える。しかし実際には
矛盾しない: 3.1/3.2 のテストは `PredictionConfig()`（`measure_elapsed=True`
既定）を使っているため、本タスク実装後は `elapsed_ms is None` が **失敗する**。
そのため、それらのテストの `elapsed_ms is None` アサーションは本タスクの
実装に合わせて `elapsed_ms is not None` 系の検証に更新し、3.3 時点の正しい
契約に合わせる。
"""

from __future__ import annotations

import inspect
import math
import random
import time
from unittest.mock import patch

from analytic import KnownTrajectory, analytic_floor_impact, generate_samples

from prediction_core.config import PredictionConfig
from prediction_core.predictor import predict
from prediction_core.types import InvalidPrediction, InvalidReason, Prediction, Sample


def _assert_measured_elapsed_ms(elapsed_ms: float | None) -> None:
    """`config.measure_elapsed=True` 時の `elapsed_ms` の形を固定するヘルパ（要件 8.1）。

    実測されていること（`float` であり有限・非負）を確認する。加えて、
    インメモリの小さな計算に対して明らかに桁が違う値（例: ns 単位のまま
    ms として返してしまう単位換算バグ）を見逃さないよう、緩い上限
    （1000 ms）も併せて確認する。この上限は実測値のばらつきを許容しつつ、
    `/1e6` を忘れて `/1e3` にする・換算を丸ごと省略するといった数百万倍
    オーダーの誤りを検出できる程度に十分低く設定している。
    """
    assert elapsed_ms is not None
    assert isinstance(elapsed_ms, float)
    assert math.isfinite(elapsed_ms)
    assert elapsed_ms >= 0.0
    assert elapsed_ms < 1000.0


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
    _assert_measured_elapsed_ms(outcome.elapsed_ms)  # config.measure_elapsed 既定 True（要件 8.1）
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

    `elapsed_ms` はタスク 3.3 以降、実測される実行時刻由来の値であり、
    同じ入力でも呼び出しごとに異なり得る（要件 8.1）。そのため、ここでは
    `measure_elapsed=False` を明示して `elapsed_ms` を両呼び出しとも `None`
    に固定し、フィールド単位の完全な等価性比較（`==`）が「処理時間以外の
    全フィールド一致」の検証として成立するようにする。
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
    config = PredictionConfig(gravity_mm_s2=trajectory.gravity_mm_s2, measure_elapsed=False)

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
    _assert_measured_elapsed_ms(outcome.elapsed_ms)
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
    _assert_measured_elapsed_ms(outcome.elapsed_ms)
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
    _assert_measured_elapsed_ms(outcome.elapsed_ms)
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
    _assert_measured_elapsed_ms(outcome.elapsed_ms)
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
    _assert_measured_elapsed_ms(outcome.elapsed_ms)
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
    _assert_measured_elapsed_ms(outcome.elapsed_ms)
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
    _assert_measured_elapsed_ms(outcome.elapsed_ms)
    assert outcome.config is config


# ---------------------------------------------------------------------------
# タスク 3.3: 処理時間の計測と実行時無効化（要件 8.1-8.5）
# ---------------------------------------------------------------------------


def test_predict_elapsed_ms_is_none_when_measurement_disabled_for_success() -> None:
    """`measure_elapsed=False` のとき、成功結果の `elapsed_ms` は `None`（要件 8.3）。"""
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
    config = PredictionConfig(gravity_mm_s2=trajectory.gravity_mm_s2, measure_elapsed=False)

    outcome = predict(samples, config)

    assert isinstance(outcome, Prediction)
    assert outcome.elapsed_ms is None


def test_predict_elapsed_ms_never_exceeds_externally_measured_wall_clock_duration() -> None:
    """`elapsed_ms` は `predict()` 呼び出しを外側から挟んだ壁時計時間を超えない(単位換算の健全性、要件 8.1)。

    `predict()` 内部で計測する区間は、この呼び出し全体を外側から挟む
    壁時計計測の部分区間である。したがって正しく ms に換算されていれば、
    測定オーバーヘッド分のわずかな余裕を見込んでも
    `elapsed_ms <= wall_elapsed_ms + epsilon` が常に成り立つ。

    `_assert_measured_elapsed_ms` の固定上限(1000 ms)だけでは、この
    テスト環境の実測値が非常に小さい(数百分の1〜数十分の1 ms)ため、
    `/1e6` を `/1e3` にする(1000倍の桁違い)といった単位バグを混入させても
    1000 ms 未満に収まってしまい検出できないことを実機で確認した。
    そのため、マシン速度に依存しない相対的な健全性チェックとして、
    同一呼び出しを外側から独立に計測した壁時計時間との比較を用いる。
    この比較はどんな速度のマシンでも「部分区間は全体区間を超えない」
    という不変条件そのものであり、マジックナンバーのしきい値に頼らない。
    """
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
    config = PredictionConfig(gravity_mm_s2=trajectory.gravity_mm_s2, measure_elapsed=True)

    wall_start_ns = time.perf_counter_ns()
    outcome = predict(samples, config)
    wall_elapsed_ms = (time.perf_counter_ns() - wall_start_ns) / 1e6

    assert isinstance(outcome, Prediction)
    assert outcome.elapsed_ms is not None
    # 呼び出し前後の壁時計計測そのものの誤差用に小さな加算余裕(0.5ms)のみ
    # 許容する。乗算的な緩さを持たせない。
    assert outcome.elapsed_ms <= wall_elapsed_ms + 0.5


def test_predict_elapsed_ms_is_none_when_measurement_disabled_for_failure() -> None:
    """`measure_elapsed=False` のとき、無効判定の `elapsed_ms` も `None`（要件 8.3）。"""
    samples = [
        Sample(t_ms=0.0, x_mm=1.0, y_mm=2.0, z_mm=3.0),
        Sample(t_ms=10.0, x_mm=4.0, y_mm=5.0, z_mm=6.0),
    ]
    config = PredictionConfig(measure_elapsed=False)
    assert len(samples) < config.min_samples  # 前提: INSUFFICIENT_SAMPLES を踏む

    outcome = predict(samples, config)

    assert isinstance(outcome, InvalidPrediction)
    assert outcome.reason is InvalidReason.INSUFFICIENT_SAMPLES
    assert outcome.elapsed_ms is None


def test_predict_does_not_call_perf_counter_ns_when_measurement_disabled_success() -> None:
    """`measure_elapsed=False` のとき、成功経路でも `time.perf_counter_ns` は一度も呼ばれない（要件 8.3）。

    「計測はしたが結果を捨てた」（誤り）ではなく「そもそも計測していない」
    （正しい）ことをタスク文言どおり機械的に証明する。`elapsed_ms is None`
    という結果だけでは両者を区別できないため、呼び出し回数を直接検証する。
    """
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
    config = PredictionConfig(gravity_mm_s2=trajectory.gravity_mm_s2, measure_elapsed=False)

    with patch("prediction_core.predictor.time.perf_counter_ns") as spy_perf_counter_ns:
        outcome = predict(samples, config)

    assert isinstance(outcome, Prediction)
    spy_perf_counter_ns.assert_not_called()


def test_predict_does_not_call_perf_counter_ns_when_measurement_disabled_failure() -> None:
    """`measure_elapsed=False` のとき、無効判定の経路でも `time.perf_counter_ns` は一度も呼ばれない（要件 8.3）。"""
    samples = [
        Sample(t_ms=0.0, x_mm=1.0, y_mm=2.0, z_mm=3.0),
        Sample(t_ms=10.0, x_mm=4.0, y_mm=5.0, z_mm=6.0),
    ]
    config = PredictionConfig(measure_elapsed=False)
    assert len(samples) < config.min_samples  # 前提: INSUFFICIENT_SAMPLES を踏む

    with patch("prediction_core.predictor.time.perf_counter_ns") as spy_perf_counter_ns:
        outcome = predict(samples, config)

    assert isinstance(outcome, InvalidPrediction)
    assert outcome.reason is InvalidReason.INSUFFICIENT_SAMPLES
    spy_perf_counter_ns.assert_not_called()


def test_predict_calls_perf_counter_ns_exactly_twice_when_measurement_enabled_success() -> None:
    """`measure_elapsed=True` のとき、成功経路で `time.perf_counter_ns` はちょうど2回呼ばれる（入口・出口、要件 8.1）。

    実際の計測値は本物の `time.perf_counter_ns` に委譲しつつ（`side_effect`）、
    呼び出し回数だけを数える。これにより `elapsed_ms` が実測に基づく値の
    まま、呼び出し回数という別の側面（相互作用）を検証できる。
    """
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
    config = PredictionConfig(gravity_mm_s2=trajectory.gravity_mm_s2, measure_elapsed=True)

    with patch(
        "prediction_core.predictor.time.perf_counter_ns", side_effect=time.perf_counter_ns
    ) as spy_perf_counter_ns:
        outcome = predict(samples, config)

    assert isinstance(outcome, Prediction)
    assert spy_perf_counter_ns.call_count == 2
    _assert_measured_elapsed_ms(outcome.elapsed_ms)


def test_predict_calls_perf_counter_ns_exactly_twice_when_measurement_enabled_failure() -> None:
    """`measure_elapsed=True` のとき、無効判定の経路でも `time.perf_counter_ns` はちょうど2回呼ばれる（要件 8.1）。

    早期リターンの経路ごとに出口の呼び出し箇所が異なるため、経路によらず
    「入口1回 + 実際に通った出口1回 = 常に2回」であることを固定する。
    """
    samples = [
        Sample(t_ms=0.0, x_mm=1.0, y_mm=2.0, z_mm=3.0),
        Sample(t_ms=10.0, x_mm=4.0, y_mm=5.0, z_mm=6.0),
    ]
    config = PredictionConfig(measure_elapsed=True)
    assert len(samples) < config.min_samples  # 前提: INSUFFICIENT_SAMPLES を踏む

    with patch(
        "prediction_core.predictor.time.perf_counter_ns", side_effect=time.perf_counter_ns
    ) as spy_perf_counter_ns:
        outcome = predict(samples, config)

    assert isinstance(outcome, InvalidPrediction)
    assert outcome.reason is InvalidReason.INSUFFICIENT_SAMPLES
    assert spy_perf_counter_ns.call_count == 2
    _assert_measured_elapsed_ms(outcome.elapsed_ms)


def test_predict_calls_perf_counter_ns_exactly_twice_for_non_finite_output_path() -> None:
    """`measure_elapsed=True` のとき、出力側 NON_FINITE_VALUE の経路でも `time.perf_counter_ns` はちょうど2回(要件 8.1)。

    ステップ6は下位パイプライン成功後に組み立て直前で判定するため、
    「暫定の `Prediction` を組み立てて `elapsed_ms` を実測し、判定に落ちて
    `InvalidPrediction` を再度組み立てて `elapsed_ms` をもう一度実測する」
    という誤った実装だと、入口1回+暫定出口1回+最終出口1回の計3回になって
    しまう(このバグは成功経路や INSUFFICIENT_SAMPLES 経路の
    「ちょうど2回」テストでは検出できない。どちらも「一度も組み立て直さ
    ない」経路だからである)。ここでは出力側 NON_FINITE_VALUE を実際に踏む
    オーバーフロー入力(`test_predict_returns_non_finite_value_for_non_finite_output`
    と同一の構成)を用い、この退行クラスを直接固定する。
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
    config = PredictionConfig(gravity_mm_s2=trajectory.gravity_mm_s2, measure_elapsed=True)

    with patch(
        "prediction_core.predictor.time.perf_counter_ns", side_effect=time.perf_counter_ns
    ) as spy_perf_counter_ns:
        outcome = predict(samples, config)

    assert isinstance(outcome, InvalidPrediction)
    assert outcome.reason is InvalidReason.NON_FINITE_VALUE
    assert spy_perf_counter_ns.call_count == 2
    _assert_measured_elapsed_ms(outcome.elapsed_ms)


def test_predict_measures_elapsed_ms_at_every_return_site_when_enabled() -> None:
    """`measure_elapsed=True` のとき、7箇所ある組み立て地点すべてで `elapsed_ms` が実測される（要件 8.1）。

    正常系1種 + 無効理由5種すべてを個別に踏み、どの `elapsed_ms=None` の
    ハードコードも取りこぼしていないことを直接固定する。部分的な修正
    （例: 成功経路だけ直して失敗経路を1つ忘れる）を検出するための唯一の
    テストであるため、6経路すべてを網羅する。
    """
    default_gravity = PredictionConfig().gravity_mm_s2

    # 1) 成功（正常な組み立て地点）。
    success_trajectory = KnownTrajectory(
        x0_mm=100.0,
        vx_mm_s=250.0,
        y0_mm=-50.0,
        vy_mm_s=-30.0,
        z0_mm=800.0,
        vz_mm_s=1500.0,
        gravity_mm_s2=default_gravity,
    )
    success_samples = generate_samples(success_trajectory, [0.0, 50.0, 120.0, 300.0])
    success_config = PredictionConfig(gravity_mm_s2=default_gravity)
    success_outcome = predict(success_samples, success_config)
    assert isinstance(success_outcome, Prediction)
    _assert_measured_elapsed_ms(success_outcome.elapsed_ms)

    # 2) MALFORMED_INPUT。
    malformed_config = PredictionConfig()
    malformed_outcome = predict(
        [
            Sample(t_ms=0.0, x_mm=1.0, y_mm=2.0, z_mm=3.0),
            Sample(t_ms=10.0, x_mm=4.0, y_mm=5.0, z_mm=6.0),
            "not-a-sample",  # type: ignore[list-item]
        ],
        malformed_config,
    )
    assert isinstance(malformed_outcome, InvalidPrediction)
    assert malformed_outcome.reason is InvalidReason.MALFORMED_INPUT
    _assert_measured_elapsed_ms(malformed_outcome.elapsed_ms)

    # 3) NON_FINITE_VALUE（入力側）。
    non_finite_input_config = PredictionConfig()
    non_finite_input_outcome = predict(
        [
            Sample(t_ms=0.0, x_mm=1.0, y_mm=2.0, z_mm=3.0),
            Sample(t_ms=10.0, x_mm=float("nan"), y_mm=5.0, z_mm=6.0),
            Sample(t_ms=20.0, x_mm=7.0, y_mm=8.0, z_mm=9.0),
        ],
        non_finite_input_config,
    )
    assert isinstance(non_finite_input_outcome, InvalidPrediction)
    assert non_finite_input_outcome.reason is InvalidReason.NON_FINITE_VALUE
    _assert_measured_elapsed_ms(non_finite_input_outcome.elapsed_ms)

    # 4) INSUFFICIENT_SAMPLES。
    insufficient_config = PredictionConfig()
    insufficient_outcome = predict(
        [
            Sample(t_ms=0.0, x_mm=1.0, y_mm=2.0, z_mm=3.0),
            Sample(t_ms=10.0, x_mm=4.0, y_mm=5.0, z_mm=6.0),
        ],
        insufficient_config,
    )
    assert isinstance(insufficient_outcome, InvalidPrediction)
    assert insufficient_outcome.reason is InvalidReason.INSUFFICIENT_SAMPLES
    _assert_measured_elapsed_ms(insufficient_outcome.elapsed_ms)

    # 5) DEGENERATE_TIME。
    degenerate_config = PredictionConfig()
    degenerate_outcome = predict(
        [
            Sample(t_ms=100.0, x_mm=1.0, y_mm=2.0, z_mm=3.0),
            Sample(t_ms=100.0, x_mm=4.0, y_mm=5.0, z_mm=6.0),
            Sample(t_ms=100.0, x_mm=7.0, y_mm=8.0, z_mm=9.0),
        ],
        degenerate_config,
    )
    assert isinstance(degenerate_outcome, InvalidPrediction)
    assert degenerate_outcome.reason is InvalidReason.DEGENERATE_TIME
    _assert_measured_elapsed_ms(degenerate_outcome.elapsed_ms)

    # 6) NO_FUTURE_FLOOR_CROSSING。
    no_crossing_trajectory = KnownTrajectory(
        x0_mm=0.0,
        vx_mm_s=0.0,
        y0_mm=0.0,
        vy_mm_s=0.0,
        z0_mm=-1.0,
        vz_mm_s=10.0,
        gravity_mm_s2=9806.65,
    )
    no_crossing_samples = generate_samples(no_crossing_trajectory, [0.0, 10.0, 20.0])
    no_crossing_config = PredictionConfig(gravity_mm_s2=no_crossing_trajectory.gravity_mm_s2)
    no_crossing_outcome = predict(no_crossing_samples, no_crossing_config)
    assert isinstance(no_crossing_outcome, InvalidPrediction)
    assert no_crossing_outcome.reason is InvalidReason.NO_FUTURE_FLOOR_CROSSING
    _assert_measured_elapsed_ms(no_crossing_outcome.elapsed_ms)

    # 7) NON_FINITE_VALUE（出力側）。
    overflow_trajectory = KnownTrajectory(
        x0_mm=100.0,
        vx_mm_s=0.0,
        y0_mm=0.0,
        vy_mm_s=0.0,
        z0_mm=800.0,
        vz_mm_s=1e160,
        gravity_mm_s2=9806.65,
    )
    overflow_samples = generate_samples(overflow_trajectory, [0.0, 10.0, 20.0])
    overflow_config = PredictionConfig(gravity_mm_s2=overflow_trajectory.gravity_mm_s2)
    overflow_outcome = predict(overflow_samples, overflow_config)
    assert isinstance(overflow_outcome, InvalidPrediction)
    assert overflow_outcome.reason is InvalidReason.NON_FINITE_VALUE
    _assert_measured_elapsed_ms(overflow_outcome.elapsed_ms)
