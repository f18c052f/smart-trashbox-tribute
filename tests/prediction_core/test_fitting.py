"""TrajectoryFitter の検証（要件 2.1-2.5 / 6.2 / 10.2）。

`tests/prediction_core/analytic.py` は `src/prediction_core/fitting.py` を
一切 import しない独立した物理オラクルである（同ファイルの docstring 参照）。
本テストはそのオラクルが生成した誤差ゼロのサンプル列を `fit_trajectory` に
通し、返る軌道パラメータが解析解（`KnownTrajectory` の各フィールド）と
丸め誤差の範囲で一致することを確認する。比較が「同じコードを同じコードと
比べる」循環にならないことがこの構成の目的である。
"""

from __future__ import annotations

import math

from analytic import KnownTrajectory, add_noise, generate_samples

from prediction_core.config import PredictionConfig
from prediction_core.fitting import FitResult, fit_trajectory
from prediction_core.types import InvalidReason, Sample


def _assert_matches_known_trajectory(
    result: FitResult, trajectory: KnownTrajectory, t_ref_ms: float
) -> None:
    """フィット結果が解析解と一致することを確認する。

    `TrajectoryParameters` の位置は `t_ref_ms` を基準時刻とする(design.md)ため、
    `KnownTrajectory`(常に絶対時刻 t=0 基準)の初期位置とは、`t_ref_ms` が 0 の
    場合を除き直接比較できない。`trajectory.position_at_ms(t_ref_ms)` で
    `t_ref_ms` 時点の厳密位置を独立に求め、それと比較する。
    """
    traj = result.trajectory
    assert traj.t_ref_ms == t_ref_ms
    expected_x0, expected_y0, expected_z0 = trajectory.position_at_ms(t_ref_ms)
    assert math.isclose(traj.x0_mm, expected_x0, rel_tol=1e-6, abs_tol=1e-6)
    assert math.isclose(traj.y0_mm, expected_y0, rel_tol=1e-6, abs_tol=1e-6)
    assert math.isclose(traj.z0_mm, expected_z0, rel_tol=1e-6, abs_tol=1e-6)
    assert math.isclose(
        traj.estimated_vx_mm_s, trajectory.vx_mm_s, rel_tol=1e-6, abs_tol=1e-6
    )
    assert math.isclose(
        traj.estimated_vy_mm_s, trajectory.vy_mm_s, rel_tol=1e-6, abs_tol=1e-6
    )
    # vz は重力により時間とともに変化する(vz(t) = vz0 - g*t)ため、t_ref 時点の
    # 値と比較する必要がある。t_ref_ms=0 のときのみ trajectory.vz_mm_s と一致する。
    expected_vz = trajectory.vz_mm_s - trajectory.gravity_mm_s2 * (t_ref_ms / 1000.0)
    assert math.isclose(traj.estimated_vz_mm_s, expected_vz, rel_tol=1e-6, abs_tol=1e-6)
    assert math.isclose(
        traj.gravity_mm_s2, trajectory.gravity_mm_s2, rel_tol=1e-9, abs_tol=1e-6
    )
    assert math.isclose(result.residual, 0.0, abs_tol=1e-6)


def test_fit_trajectory_matches_analytic_solution_for_ideal_parabola() -> None:
    """誤差ゼロの理想放物線から、解析解と丸め誤差の範囲で一致する結果を返す(要件2.1/2.3/2.4/2.5)。"""
    trajectory = KnownTrajectory(
        x0_mm=100.0,
        vx_mm_s=250.0,
        y0_mm=-50.0,
        vy_mm_s=-30.0,
        z0_mm=800.0,
        vz_mm_s=1500.0,
        gravity_mm_s2=9806.65,
    )
    times_ms = [0.0, 50.0, 120.0, 300.0, 480.0]
    samples = generate_samples(trajectory, times_ms)
    config = PredictionConfig(gravity_mm_s2=trajectory.gravity_mm_s2)

    result = fit_trajectory(samples, config)

    assert isinstance(result, FitResult)
    _assert_matches_known_trajectory(result, trajectory, t_ref_ms=0.0)


def test_fit_trajectory_matches_analytic_solution_for_a_second_distinct_trajectory() -> (
    None
):
    """別の初期位置・速度・重力加速度でも解析解に一致する(ハードコードでないことの確認)。"""
    trajectory = KnownTrajectory(
        x0_mm=-1200.0,
        vx_mm_s=-400.0,
        y0_mm=300.0,
        vy_mm_s=600.0,
        z0_mm=2000.0,
        vz_mm_s=-200.0,
        gravity_mm_s2=1622.0,  # 月面重力相当。既定値(地球)と明確に異なる値。
    )
    times_ms = [1_000.0, 1_030.0, 1_090.0, 1_250.0, 1_700.0]
    samples = generate_samples(trajectory, times_ms)
    config = PredictionConfig(gravity_mm_s2=trajectory.gravity_mm_s2)

    result = fit_trajectory(samples, config)

    assert isinstance(result, FitResult)
    _assert_matches_known_trajectory(result, trajectory, t_ref_ms=1_000.0)


def test_fit_trajectory_converts_velocity_to_mm_per_s_not_mm_per_ms() -> None:
    """速度は mm/s で返る(mm/ms のまま、あるいは *1000 忘れなら明らかに不一致になる値で確認、要件2.5)。"""
    trajectory = KnownTrajectory(
        x0_mm=0.0,
        vx_mm_s=250.0,
        y0_mm=0.0,
        vy_mm_s=-30.0,
        z0_mm=800.0,
        vz_mm_s=1500.0,
        gravity_mm_s2=9806.65,
    )
    times_ms = [0.0, 10.0, 40.0]
    samples = generate_samples(trajectory, times_ms)
    config = PredictionConfig(gravity_mm_s2=trajectory.gravity_mm_s2)

    result = fit_trajectory(samples, config)

    assert isinstance(result, FitResult)
    # mm/ms のまま(*1000 忘れ)なら 0.25 になり、250.0 とは明らかに異なる。
    assert math.isclose(
        result.trajectory.estimated_vx_mm_s, 250.0, rel_tol=1e-6, abs_tol=1e-6
    )
    assert math.isclose(
        result.trajectory.estimated_vy_mm_s, -30.0, rel_tol=1e-6, abs_tol=1e-6
    )
    assert math.isclose(
        result.trajectory.estimated_vz_mm_s, 1500.0, rel_tol=1e-6, abs_tol=1e-6
    )


def test_fit_trajectory_returns_degenerate_time_when_all_timestamps_are_identical() -> (
    None
):
    """観測時刻が縮退している場合は例外を送出せず InvalidReason.DEGENERATE_TIME を返す(要件6.2)。"""
    samples = [
        Sample(t_ms=100.0, x_mm=1.0, y_mm=2.0, z_mm=3.0),
        Sample(t_ms=100.0, x_mm=4.0, y_mm=5.0, z_mm=6.0),
        Sample(t_ms=100.0, x_mm=7.0, y_mm=8.0, z_mm=9.0),
    ]
    config = PredictionConfig()

    result = fit_trajectory(samples, config)

    assert result is InvalidReason.DEGENERATE_TIME


def test_fit_trajectory_with_noisy_samples_has_nonzero_residual() -> None:
    """既知の誤差を重畳すると残差が 0 でなくなる(残差計算が実際に機能していることの確認)。"""
    trajectory = KnownTrajectory(
        x0_mm=100.0,
        vx_mm_s=250.0,
        y0_mm=-50.0,
        vy_mm_s=-30.0,
        z0_mm=800.0,
        vz_mm_s=1500.0,
        gravity_mm_s2=9806.65,
    )
    times_ms = [0.0, 50.0, 120.0, 300.0, 480.0, 600.0]
    clean_samples = generate_samples(trajectory, times_ms)
    noisy_samples = add_noise(clean_samples, seed=42, stddev_mm=15.0)
    config = PredictionConfig(gravity_mm_s2=trajectory.gravity_mm_s2)

    result = fit_trajectory(noisy_samples, config)

    assert isinstance(result, FitResult)
    assert result.residual > 1.0

    # 残差の値そのものを、fit_trajectory とは独立にこのテストの中で
    # 再計算した SSE / 自由度 3*(n-2) の平方根と突き合わせる。
    # `result.residual > 1.0` のような閾値検査だけでは、自由度の分母を
    # 例えば 3*(n-1) に取り違えても(SSE が同じ桁数なら)通ってしまうため、
    # 分母の値そのものを固定するにはこの独立再計算が必要。
    n = len(noisy_samples)
    traj = result.trajectory
    t_ref_ms = traj.t_ref_ms
    vx_internal = traj.estimated_vx_mm_s / 1000.0
    vy_internal = traj.estimated_vy_mm_s / 1000.0
    vz_internal = traj.estimated_vz_mm_s / 1000.0
    g_internal = traj.gravity_mm_s2 / 1000.0**2
    sse = 0.0
    for sample in noisy_samples:
        t = sample.t_ms - t_ref_ms
        x_pred = traj.x0_mm + vx_internal * t
        y_pred = traj.y0_mm + vy_internal * t
        z_pred = traj.z0_mm + vz_internal * t - 0.5 * g_internal * t * t
        sse += (
            (sample.x_mm - x_pred) ** 2
            + (sample.y_mm - y_pred) ** 2
            + (sample.z_mm - z_pred) ** 2
        )
    expected_residual = math.sqrt(sse / (3.0 * (n - 2)))
    assert math.isclose(result.residual, expected_residual, rel_tol=1e-9)
