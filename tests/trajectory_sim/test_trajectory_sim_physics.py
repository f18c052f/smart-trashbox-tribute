"""投擲物理モデルのテスト（要件1.1, 1.2, 1.3, 1.6, 1.7 / design.md「ThrowPhysics」）。

`trajectory_sim.physics` が以下を満たすことを固定する:

- `TrueTrajectory.position_at` が任意時刻の真の位置を返す（要件1.7）
- `sample_throw` が `rng.normalvariate` を**固定の順序・固定の回数**
  （速度 → 仰角 → 方位角 → リリース位置 x, y, z）だけ引き、ゼロ分散の
  項目では乱数を消費しない（要件1.3, design.md「ThrowPhysics」Invariants）
- `solve_true_impact` が床面 z=0 との未来側の交点を解析解として返し、
  未来側の根が存在しない場合は `None` を返す（要件1.2, 1.6）

ファイル名は `test_trajectory_sim_physics.py`
（`tests/prediction_core/` に `test_physics.py` は存在しないが、
`tests/trajectory_sim/test_trajectory_sim_*.py` の命名方針を踏襲する。
tasks.md「Implementation Notes」参照）。
"""

from __future__ import annotations

import math
from random import Random

import pytest

from trajectory_sim import physics, units
from trajectory_sim.params import ThrowDispersion, ThrowParams


def _throw(**overrides: float) -> ThrowParams:
    """検証を通る最小構成の `ThrowParams` を返す（明示指定分のみ上書き）。"""
    base: dict[str, float] = dict(
        release_x_mm=0.0,
        release_y_mm=0.0,
        release_z_mm=0.0,
        speed_mm_s=1000.0,
        elevation_deg=45.0,
        azimuth_deg=0.0,
    )
    base.update(overrides)
    return ThrowParams(**base)


class _RecordingRandom(Random):
    """`normalvariate` の呼び出し引数を記録しつつ実体は基底実装へ委譲するスパイ。

    決定性を壊さないよう、記録後は必ず `super().normalvariate` の返り値を
    そのまま返す。
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.calls: list[tuple[float, float]] = []

    def normalvariate(self, mu: float = 0.0, sigma: float = 1.0) -> float:
        self.calls.append((mu, sigma))
        return super().normalvariate(mu, sigma)


# ---------------------------------------------------------------------------
# TrueTrajectory.position_at（要件1.7）
# ---------------------------------------------------------------------------


def test_position_at_release_time_equals_release_point() -> None:
    """`t0_ms` そのものを渡すとリリース位置と厳密に一致する。"""
    trajectory = physics.TrueTrajectory(
        t0_ms=100.0,
        x0_mm=10.0,
        y0_mm=20.0,
        z0_mm=30.0,
        vx_mm_ms=1.0,
        vy_mm_ms=2.0,
        vz_mm_ms=3.0,
        gravity_mm_ms2=0.01,
    )
    assert trajectory.position_at(100.0) == pytest.approx((10.0, 20.0, 30.0))


def test_position_at_future_time_matches_hand_calculation() -> None:
    """水平投射（vz=0）から 500ms 後の位置を、閉形式の運動方程式で手計算し照合する。

    x = x0 + vx*dt, y = y0（vy=0）, z = z0 - 0.5*g*dt^2（vz=0 のため）。
    """
    g_mm_ms2 = units.mm_per_s2_to_mm_per_ms2(9806.65)
    trajectory = physics.TrueTrajectory(
        t0_ms=0.0,
        x0_mm=0.0,
        y0_mm=0.0,
        z0_mm=4903.325,
        vx_mm_ms=2.0,
        vy_mm_ms=0.0,
        vz_mm_ms=0.0,
        gravity_mm_ms2=g_mm_ms2,
    )
    x, y, z = trajectory.position_at(500.0)
    assert x == pytest.approx(1000.0, rel=1e-9)
    assert y == pytest.approx(0.0, abs=1e-9)
    assert z == pytest.approx(4903.325 - 0.5 * g_mm_ms2 * 500.0**2, rel=1e-9)


# ---------------------------------------------------------------------------
# solve_true_impact: 解析解の一致（要件1.2）
# ---------------------------------------------------------------------------


def test_solve_true_impact_horizontal_throw_from_height_matches_hand_derivation() -> None:
    """水平投射（elevation=0）で高さ z0 から床面へ落下する時刻は t = sqrt(2*z0/g)。

    手計算: g=9806.65mm/s^2, z0=g/2=4903.325mm とすると
    t = sqrt(2*4903.325/9806.65) = sqrt(1.0) = 1.0s = 1000ms。
    azimuth=0 のため y は変化せず、x は vx*t だけ進む
    （vx = speed_mm_s/1000 = 2.0mm/ms, x = 0 + 2.0*1000 = 2000mm）。
    """
    throw = _throw(
        release_z_mm=4903.325, elevation_deg=0.0, azimuth_deg=0.0, speed_mm_s=2000.0
    )
    trajectory = physics.sample_throw(throw, ThrowDispersion(), Random(0))
    impact = physics.solve_true_impact(trajectory)
    assert impact is not None
    assert impact.time_ms == pytest.approx(1000.0, rel=1e-9)
    assert impact.x_mm == pytest.approx(2000.0, rel=1e-9)
    assert impact.y_mm == pytest.approx(0.0, abs=1e-9)


def test_solve_true_impact_vertical_throw_matches_hand_derivation() -> None:
    """鉛直投げ上げ（elevation=90, z0=0）の落下時刻は t = 2*v/g。

    手計算: v=g=9806.65（mm/s と mm/s^2 で同数値）のとき
    t = 2*9806.65/9806.65 = 2.0s = 2000ms。
    elevation=90 では cos(90°)=0 のため水平成分の速度は0で、
    x・y はリリース位置（0, 0）から変化しない。
    """
    throw = _throw(
        release_z_mm=0.0, elevation_deg=90.0, azimuth_deg=30.0, speed_mm_s=9806.65
    )
    trajectory = physics.sample_throw(throw, ThrowDispersion(), Random(1))
    impact = physics.solve_true_impact(trajectory)
    assert impact is not None
    assert impact.time_ms == pytest.approx(2000.0, rel=1e-9)
    assert impact.x_mm == pytest.approx(0.0, abs=1e-9)
    assert impact.y_mm == pytest.approx(0.0, abs=1e-9)


def test_solve_true_impact_time_is_strictly_greater_than_t0() -> None:
    """postcondition: 返る `time_ms` は必ず `trajectory.t0_ms` より大きい。"""
    throw = _throw(release_z_mm=1000.0, elevation_deg=10.0)
    trajectory = physics.sample_throw(throw, ThrowDispersion(), Random(2))
    impact = physics.solve_true_impact(trajectory)
    assert impact is not None
    assert impact.time_ms > trajectory.t0_ms


# ---------------------------------------------------------------------------
# solve_true_impact: 縮退した入力で「無し」を返す（要件1.6）
# ---------------------------------------------------------------------------


def test_solve_true_impact_returns_none_when_thrown_straight_down_from_floor() -> None:
    """z0=0 かつ elevation=-90（まっすぐ下）のとき、根は {0, 負} のみで未来側の根が無い。

    手計算: a=0.5*g>0, b=-vz=speed_mm_ms>0（vz<0のため）, c=-z0=0。
    disc = b^2 - 4ac = b^2（c=0のため）なので sqrt(disc)=b。
    root1 = (-b+b)/(2a) = 0、root2 = (-b-b)/(2a) = -b/a < 0。
    どちらも 0 以下であり、`dt > 0` を満たす未来側の根は存在しない。
    """
    throw = _throw(release_z_mm=0.0, elevation_deg=-90.0)
    trajectory = physics.sample_throw(throw, ThrowDispersion(), Random(3))
    assert physics.solve_true_impact(trajectory) is None


def test_solve_true_impact_returns_none_when_released_below_floor_without_enough_lift() -> None:
    """z0<0（床下でリリース）かつ vz=0 のとき判別式が負になり、実根が存在しない。

    手計算: a=0.5*g>0（gravity_mm_s2は常に正のため）, b=-vz=0, c=-z0=100
    （z0=-100 のため）。disc = b^2 - 4ac = 0 - 4*a*100 = -400a < 0。
    判別式が負のため実根が存在せず、軌道は常に z<0（床の下）のままである
    （z(t) の頂点 z0=-100 自体が既に0未満であるため、放物線全体が
    z=0 に到達しない）。
    """
    throw = _throw(release_z_mm=-100.0, elevation_deg=0.0)
    trajectory = physics.sample_throw(throw, ThrowDispersion(), Random(4))
    assert physics.solve_true_impact(trajectory) is None


# ---------------------------------------------------------------------------
# sample_throw: t0_ms はローカル時刻原点として常に 0.0
# ---------------------------------------------------------------------------


def test_sample_throw_uses_local_time_origin_zero() -> None:
    trajectory = physics.sample_throw(_throw(), ThrowDispersion(), Random(5))
    assert trajectory.t0_ms == 0.0


def test_sample_throw_converts_gravity_via_units_module() -> None:
    throw = _throw(release_z_mm=1.0)
    trajectory = physics.sample_throw(throw, ThrowDispersion(), Random(6))
    assert trajectory.gravity_mm_ms2 == pytest.approx(
        units.mm_per_s2_to_mm_per_ms2(throw.gravity_mm_s2)
    )


# ---------------------------------------------------------------------------
# sample_throw: 固定の乱数消費順序・回数（要件1.3, design.md「ThrowPhysics」Invariants）
# ---------------------------------------------------------------------------


def test_sample_throw_draws_zero_times_when_dispersion_is_zero() -> None:
    """ばらつきが全てゼロ（既定値）のとき、`rng.normalvariate` を一切呼ばない。"""
    throw = _throw()
    dispersion = ThrowDispersion()
    rng = _RecordingRandom(42)
    trajectory = physics.sample_throw(throw, dispersion, rng)

    assert rng.calls == []
    assert trajectory.x0_mm == pytest.approx(throw.release_x_mm)
    assert trajectory.y0_mm == pytest.approx(throw.release_y_mm)
    assert trajectory.z0_mm == pytest.approx(throw.release_z_mm)

    expected_vx = (
        units.mm_per_s_to_mm_per_ms(throw.speed_mm_s)
        * math.cos(units.deg_to_rad(throw.elevation_deg))
        * math.cos(units.deg_to_rad(throw.azimuth_deg))
    )
    expected_vy = (
        units.mm_per_s_to_mm_per_ms(throw.speed_mm_s)
        * math.cos(units.deg_to_rad(throw.elevation_deg))
        * math.sin(units.deg_to_rad(throw.azimuth_deg))
    )
    expected_vz = units.mm_per_s_to_mm_per_ms(throw.speed_mm_s) * math.sin(
        units.deg_to_rad(throw.elevation_deg)
    )
    assert trajectory.vx_mm_ms == pytest.approx(expected_vx)
    assert trajectory.vy_mm_ms == pytest.approx(expected_vy)
    assert trajectory.vz_mm_ms == pytest.approx(expected_vz)


def test_sample_throw_draws_in_fixed_order_with_full_dispersion() -> None:
    """全項目にばらつきがあるとき、速度→仰角→方位角→x→y→z の順で厳密に6回引く。"""
    throw = _throw()
    dispersion = ThrowDispersion(
        speed_sigma_mm_s=10.0,
        elevation_sigma_deg=1.0,
        azimuth_sigma_deg=2.0,
        release_sigma_mm=5.0,
    )
    rng = _RecordingRandom(42)
    physics.sample_throw(throw, dispersion, rng)

    assert rng.calls == [
        (throw.speed_mm_s, dispersion.speed_sigma_mm_s),
        (throw.elevation_deg, dispersion.elevation_sigma_deg),
        (throw.azimuth_deg, dispersion.azimuth_sigma_deg),
        (throw.release_x_mm, dispersion.release_sigma_mm),
        (throw.release_y_mm, dispersion.release_sigma_mm),
        (throw.release_z_mm, dispersion.release_sigma_mm),
    ]


def test_sample_throw_skips_draw_for_individually_zero_sigma_fields() -> None:
    """一部のみゼロ分散のとき、その項目だけ乱数を消費しない。"""
    throw = _throw()
    dispersion = ThrowDispersion(azimuth_sigma_deg=3.0)  # 他は既定の 0.0
    rng = _RecordingRandom(7)
    physics.sample_throw(throw, dispersion, rng)

    assert rng.calls == [(throw.azimuth_deg, dispersion.azimuth_sigma_deg)]


def test_sample_throw_does_not_use_gauss(monkeypatch: pytest.MonkeyPatch) -> None:
    """`rng.gauss` はキャッシュにより呼び出し順に状態が残るため使用してはならない
    （design.md「Technology Stack」）。
    """

    def _forbidden(*_args: object, **_kwargs: object) -> float:
        raise AssertionError("sample_throw は rng.gauss を呼び出してはならない")

    monkeypatch.setattr(Random, "gauss", _forbidden, raising=True)
    throw = _throw()
    dispersion = ThrowDispersion(speed_sigma_mm_s=10.0)
    physics.sample_throw(throw, dispersion, Random(8))  # 例外が出なければ成功


# ---------------------------------------------------------------------------
# sample_throw: 決定性（同一種・同一入力なら同一結果）
# ---------------------------------------------------------------------------


def test_sample_throw_is_deterministic_for_same_seed_and_inputs() -> None:
    throw = _throw()
    dispersion = ThrowDispersion(
        speed_sigma_mm_s=10.0,
        elevation_sigma_deg=1.0,
        azimuth_sigma_deg=2.0,
        release_sigma_mm=5.0,
    )
    trajectory_a = physics.sample_throw(throw, dispersion, Random(123))
    trajectory_b = physics.sample_throw(throw, dispersion, Random(123))
    assert trajectory_a == trajectory_b
