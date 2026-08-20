"""既知放物線との end-to-end 一致の検証(要件 2.3 / 3.4 / 7.1 / 7.2)。

`tests/prediction_core/analytic.py` は `predict` が内部で使う
`fitting.fit_trajectory` / `impact.solve_floor_impact` のいずれも import
しない独立した物理オラクルである(同ファイルの docstring 参照)。本テストは
そのオラクルが生成した誤差ゼロのサンプル列を `predict` に通し、返る
`Prediction` の落下地点・落下時刻が解析解(`analytic_floor_impact`)と
丸め誤差の範囲で一致することを確認する。比較が「同じコードを同じコードと
比べる」循環にならないことがこの構成の目的である(design.md「Boundary
Commitments」、tasks.md 1.6)。

**残差の閾値は使わない**(design.md Testing Strategy の non-goal)。ここで
検証するのはあくまで `predict` の出力が解析解と一致することであり、
`Prediction.residual` の大小そのものは判定基準にしない。

許容誤差は `tests/prediction_core/test_fitting.py` /
`test_impact.py` が採用している基準(`rel_tol=1e-6, abs_tol=1e-6` 前後)を
踏襲する。入力サンプルは `generate_samples` が返す誤差ゼロの厳密値であり、
かつ放物運動モデルの未知数(各軸2個、計6個)に対しサンプル数(5〜8点)は
過剰決定系なので、`fit_trajectory` の最小二乗解は理論上、厳密解(解析解)に
一致する。したがって許容差として残るのは浮動小数点演算の丸め誤差のみで
よい。

大きな絶対時刻オフセットのテスト(要件 3.4)のみ、`t_ref_ms` 自体が
1e9 スケールになるため、float64 がその大きさの数を表現できる精度の床
(machine epsilon 相当、およそ 1e9 * 2^-52 ≈ 2.2e-7 ms)を踏まえて
時刻の許容誤差をわずかに広げている(該当テストの docstring 参照)。
"""

from __future__ import annotations

import math

from analytic import KnownTrajectory, analytic_floor_impact, generate_samples

from prediction_core import PredictionConfig, Prediction, Sample, predict

# 位置(mm)の許容誤差。test_fitting.py / test_impact.py と同じ基準。
_POS_REL_TOL = 1e-6
_POS_ABS_TOL = 1e-6

# 時刻(ms)の許容誤差。test_impact.py と同じ基準。
_TIME_REL_TOL = 1e-9
_TIME_ABS_TOL = 1e-6


def _assert_prediction_matches_analytic(
    prediction: Prediction,
    expected_hit_x_mm: float,
    expected_hit_y_mm: float,
    expected_hit_time_ms: float,
) -> None:
    assert math.isclose(
        prediction.predicted_hit_x_mm,
        expected_hit_x_mm,
        rel_tol=_POS_REL_TOL,
        abs_tol=_POS_ABS_TOL,
    )
    assert math.isclose(
        prediction.predicted_hit_y_mm,
        expected_hit_y_mm,
        rel_tol=_POS_REL_TOL,
        abs_tol=_POS_ABS_TOL,
    )
    assert math.isclose(
        prediction.predicted_hit_time_ms,
        expected_hit_time_ms,
        rel_tol=_TIME_REL_TOL,
        abs_tol=_TIME_ABS_TOL,
    )


def test_predict_matches_analytic_solution_for_horizontal_launch() -> None:
    """水平投射(vz0=0)の落下地点・落下時刻が解析解と一致する(要件 2.3 / 3.4 / 7.2)。"""
    trajectory = KnownTrajectory(
        x0_mm=100.0,
        vx_mm_s=250.0,
        y0_mm=-50.0,
        vy_mm_s=-30.0,
        z0_mm=800.0,
        vz_mm_s=0.0,
        gravity_mm_s2=9806.65,
    )
    # 解析的な落下時刻は約 403.9ms。サンプルはすべてそれより前。
    times_ms = [0.0, 50.0, 100.0, 150.0, 200.0, 250.0, 300.0]
    samples = generate_samples(trajectory, times_ms)
    config = PredictionConfig(gravity_mm_s2=trajectory.gravity_mm_s2)

    expected = analytic_floor_impact(trajectory, after_time_ms=times_ms[-1])
    assert expected is not None

    outcome = predict(samples, config)

    assert isinstance(outcome, Prediction)
    _assert_prediction_matches_analytic(
        outcome,
        expected.hit_x_mm,
        expected.hit_y_mm,
        expected.hit_time_ms,
    )


def test_predict_matches_analytic_solution_for_oblique_launch_upward() -> None:
    """斜方投射(上向き、vz0>0)の落下地点・落下時刻が解析解と一致する(要件 2.3 / 3.4 / 7.2)。"""
    trajectory = KnownTrajectory(
        x0_mm=-1200.0,
        vx_mm_s=-400.0,
        y0_mm=300.0,
        vy_mm_s=600.0,
        z0_mm=500.0,
        vz_mm_s=3000.0,
        gravity_mm_s2=9806.65,
    )
    # 解析的な落下時刻は約 748.1ms。サンプルはすべてそれより前。
    times_ms = [0.0, 80.0, 160.0, 240.0, 320.0, 400.0, 480.0]
    samples = generate_samples(trajectory, times_ms)
    config = PredictionConfig(gravity_mm_s2=trajectory.gravity_mm_s2)

    expected = analytic_floor_impact(trajectory, after_time_ms=times_ms[-1])
    assert expected is not None

    outcome = predict(samples, config)

    assert isinstance(outcome, Prediction)
    _assert_prediction_matches_analytic(
        outcome,
        expected.hit_x_mm,
        expected.hit_y_mm,
        expected.hit_time_ms,
    )


def test_predict_matches_analytic_solution_for_oblique_launch_downward() -> None:
    """斜方投射(下向き、vz0<0)の落下地点・落下時刻が解析解と一致する(要件 2.3 / 3.4 / 7.2)。"""
    trajectory = KnownTrajectory(
        x0_mm=50.0,
        vx_mm_s=120.0,
        y0_mm=200.0,
        vy_mm_s=-80.0,
        z0_mm=1500.0,
        vz_mm_s=-800.0,
        gravity_mm_s2=9806.65,
    )
    # 解析的な落下時刻は約 477.5ms。サンプルはすべてそれより前。
    times_ms = [0.0, 50.0, 100.0, 150.0, 200.0, 250.0, 300.0]
    samples = generate_samples(trajectory, times_ms)
    config = PredictionConfig(gravity_mm_s2=trajectory.gravity_mm_s2)

    expected = analytic_floor_impact(trajectory, after_time_ms=times_ms[-1])
    assert expected is not None

    outcome = predict(samples, config)

    assert isinstance(outcome, Prediction)
    _assert_prediction_matches_analytic(
        outcome,
        expected.hit_x_mm,
        expected.hit_y_mm,
        expected.hit_time_ms,
    )


def test_predict_matches_analytic_solution_for_high_initial_altitude() -> None:
    """初期高度が高いケースの落下地点・落下時刻が解析解と一致する(要件 2.3 / 3.4 / 7.2)。"""
    trajectory = KnownTrajectory(
        x0_mm=0.0,
        vx_mm_s=50.0,
        y0_mm=0.0,
        vy_mm_s=50.0,
        z0_mm=10_000.0,
        vz_mm_s=0.0,
        gravity_mm_s2=9806.65,
    )
    # 解析的な落下時刻は約 1428.1ms。サンプルはすべてそれより前。
    times_ms = [0.0, 150.0, 300.0, 450.0, 600.0, 750.0, 900.0]
    samples = generate_samples(trajectory, times_ms)
    config = PredictionConfig(gravity_mm_s2=trajectory.gravity_mm_s2)

    expected = analytic_floor_impact(trajectory, after_time_ms=times_ms[-1])
    assert expected is not None

    outcome = predict(samples, config)

    assert isinstance(outcome, Prediction)
    _assert_prediction_matches_analytic(
        outcome,
        expected.hit_x_mm,
        expected.hit_y_mm,
        expected.hit_time_ms,
    )


def test_predict_matches_analytic_solution_for_low_initial_altitude() -> None:
    """初期高度が低いケースの落下地点・落下時刻が解析解と一致する(要件 2.3 / 3.4 / 7.2)。"""
    trajectory = KnownTrajectory(
        x0_mm=0.0,
        vx_mm_s=80.0,
        y0_mm=0.0,
        vy_mm_s=-40.0,
        z0_mm=60.0,
        vz_mm_s=200.0,
        gravity_mm_s2=9806.65,
    )
    # 解析的な落下時刻は約 132.9ms。サンプルはすべてそれより前。
    times_ms = [0.0, 15.0, 30.0, 45.0, 60.0, 75.0, 90.0]
    samples = generate_samples(trajectory, times_ms)
    config = PredictionConfig(gravity_mm_s2=trajectory.gravity_mm_s2)

    expected = analytic_floor_impact(trajectory, after_time_ms=times_ms[-1])
    assert expected is not None

    outcome = predict(samples, config)

    assert isinstance(outcome, Prediction)
    _assert_prediction_matches_analytic(
        outcome,
        expected.hit_x_mm,
        expected.hit_y_mm,
        expected.hit_time_ms,
    )


def test_predict_returns_hit_time_in_same_absolute_time_basis_as_input() -> None:
    """大きな絶対時刻オフセットを与えても、落下時刻が入力と同一の時間基準で返る(要件 3.4)。

    `KnownTrajectory` / `analytic_floor_impact` は常に `t=0` を基準に定義
    される(analytic.py の docstring 参照)。絶対時刻オフセット不変性を
    検証するため、まず `t=0` 基準のローカル時刻でサンプルを生成し、その
    `Sample.t_ms` にのみ大きな定数オフセット `_LARGE_OFFSET_MS`(1e9、
    約11.6日相当)を加算する(x_mm/y_mm/z_mm は変更しない)。これは
    「センサのクロックが大きな絶対値を刻んでいるが、観測された物理位置は
    変わらない」状況を模している。

    `predict` は入力サンプルの絶対時刻をそのまま基準に用いる
    (`predictor.py` docstring: 「時刻の相対差のみを用いる」)ため、出力の
    `predicted_hit_time_ms` は「ローカル時刻基準の解析解 + オフセット」と
    一致するはずである。一方 `predicted_hit_x_mm` / `predicted_hit_y_mm`
    は時刻オフセットの影響を受けず、ローカル時刻基準の解析解とそのまま
    一致するはずである。

    理論上は `t_ref_ms`(=サンプル最小時刻)自体が 1e9 スケールになるため、
    float64 がその大きさの数を表現できる精度の床(machine epsilon 相当、
    およそ 1e9 * 2^-52 ≈ 2.2e-7 ms)が乗りうる。しかし `fit_trajectory` は
    時刻を `t_ref_ms`(=サンプル最小値)でシフトしてから2乗する設計
    (design.md「桁落ちを避ける」、fitting.py docstring)のため、この床より
    大きな誤差混入は防がれる。実測でもオフセット無しのケースと同等の
    精度(丸め誤差のみ)だったため、他のテストと同じデフォルトの許容誤差
    (`_TIME_REL_TOL` / `_TIME_ABS_TOL`)をそのまま用いる。
    """
    _LARGE_OFFSET_MS = 1.0e9

    trajectory = KnownTrajectory(
        x0_mm=100.0,
        vx_mm_s=250.0,
        y0_mm=-50.0,
        vy_mm_s=-30.0,
        z0_mm=800.0,
        vz_mm_s=0.0,
        gravity_mm_s2=9806.65,
    )
    local_times_ms = [0.0, 50.0, 100.0, 150.0, 200.0, 250.0, 300.0]
    local_samples = generate_samples(trajectory, local_times_ms)
    shifted_samples = [
        Sample(t_ms=s.t_ms + _LARGE_OFFSET_MS, x_mm=s.x_mm, y_mm=s.y_mm, z_mm=s.z_mm)
        for s in local_samples
    ]
    config = PredictionConfig(gravity_mm_s2=trajectory.gravity_mm_s2)

    expected_local = analytic_floor_impact(trajectory, after_time_ms=local_times_ms[-1])
    assert expected_local is not None

    outcome = predict(shifted_samples, config)

    assert isinstance(outcome, Prediction)
    # 入力サンプルの絶対時刻がすべて 1e9 オフセットされているので、
    # 返る落下時刻もローカル時刻基準の解析解に同じオフセットを足した値になる。
    assert outcome.predicted_hit_time_ms > _LARGE_OFFSET_MS
    _assert_prediction_matches_analytic(
        outcome,
        expected_local.hit_x_mm,
        expected_local.hit_y_mm,
        expected_local.hit_time_ms + _LARGE_OFFSET_MS,
    )
