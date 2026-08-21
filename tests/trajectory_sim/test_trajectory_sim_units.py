"""単位換算モジュールの往復検証（要件 2.6 / 7.7）。

`trajectory_sim.units` が速度（mm/s ⇄ mm/ms）・加速度（mm/s^2 ⇄ mm/ms^2）・
角度（度 ⇄ ラジアン）の相互換算を定義し、往復させた値が元の値と一致する
ことを固定する。また、換算関数が純関数であり非有限値（inf/nan）を
そのまま透過させること（検証は `params` の境界で行う方針）も確認する。

ファイル名は `test_trajectory_sim_units.py`（`test_units.py` ではない）。
`tests/prediction_core/test_units.py` が既に存在し、両ディレクトリに
`__init__.py` がないため、pytest の既定 import-mode ではベース名の衝突が
発生する（`tests/trajectory_sim/test_trajectory_sim_packaging.py` と同じ
命名方針を踏襲）。
"""

from __future__ import annotations

import math

import pytest

from trajectory_sim import units


# ---------------------------------------------------------------------------
# 速度: mm/s <-> mm/ms
# ---------------------------------------------------------------------------


def test_ms_per_s_constant_is_1000() -> None:
    """`MS_PER_S` が唯一の時間単位換算係数として 1000 を表す。"""
    assert units.MS_PER_S == 1000.0


def test_mm_per_s_to_mm_per_ms_known_value() -> None:
    assert units.mm_per_s_to_mm_per_ms(1000.0) == pytest.approx(1.0)


def test_mm_per_ms_to_mm_per_s_known_value() -> None:
    assert units.mm_per_ms_to_mm_per_s(1.0) == pytest.approx(1000.0)


@pytest.mark.parametrize("value_mm_s", [0.0, 1.0, -500.0, 3200.0, 0.0001])
def test_speed_round_trip_mm_s_to_mm_ms_to_mm_s(value_mm_s: float) -> None:
    """mm/s -> mm/ms -> mm/s の往復が元の値に一致する。"""
    roundtrip = units.mm_per_ms_to_mm_per_s(units.mm_per_s_to_mm_per_ms(value_mm_s))
    assert roundtrip == pytest.approx(value_mm_s)


@pytest.mark.parametrize("value_mm_ms", [0.0, 1.0, -0.5, 3.2, 1e-6])
def test_speed_round_trip_mm_ms_to_mm_s_to_mm_ms(value_mm_ms: float) -> None:
    """mm/ms -> mm/s -> mm/ms の往復が元の値に一致する。"""
    roundtrip = units.mm_per_s_to_mm_per_ms(units.mm_per_ms_to_mm_per_s(value_mm_ms))
    assert roundtrip == pytest.approx(value_mm_ms)


# ---------------------------------------------------------------------------
# 加速度: mm/s^2 <-> mm/ms^2
# ---------------------------------------------------------------------------


def test_mm_per_s2_to_mm_per_ms2_known_value() -> None:
    assert units.mm_per_s2_to_mm_per_ms2(1_000_000.0) == pytest.approx(1.0)


def test_mm_per_ms2_to_mm_per_s2_known_value() -> None:
    assert units.mm_per_ms2_to_mm_per_s2(1.0) == pytest.approx(1_000_000.0)


@pytest.mark.parametrize("value_mm_s2", [0.0, 9810.0, -300.0, 1.0])
def test_accel_round_trip_mm_s2_to_mm_ms2_to_mm_s2(value_mm_s2: float) -> None:
    """mm/s^2 -> mm/ms^2 -> mm/s^2 の往復が元の値に一致する。"""
    roundtrip = units.mm_per_ms2_to_mm_per_s2(units.mm_per_s2_to_mm_per_ms2(value_mm_s2))
    assert roundtrip == pytest.approx(value_mm_s2)


@pytest.mark.parametrize("value_mm_ms2", [0.0, 9.81e-3, -3e-4, 1.0])
def test_accel_round_trip_mm_ms2_to_mm_s2_to_mm_ms2(value_mm_ms2: float) -> None:
    """mm/ms^2 -> mm/s^2 -> mm/ms^2 の往復が元の値に一致する。"""
    roundtrip = units.mm_per_s2_to_mm_per_ms2(units.mm_per_ms2_to_mm_per_s2(value_mm_ms2))
    assert roundtrip == pytest.approx(value_mm_ms2)


# ---------------------------------------------------------------------------
# 角度: 度 <-> ラジアン
# ---------------------------------------------------------------------------


def test_deg_to_rad_known_value() -> None:
    assert units.deg_to_rad(180.0) == pytest.approx(math.pi)


def test_rad_to_deg_known_value() -> None:
    assert units.rad_to_deg(math.pi) == pytest.approx(180.0)


@pytest.mark.parametrize("value_deg", [0.0, 45.0, -90.0, 360.0, 12.5])
def test_angle_round_trip_deg_to_rad_to_deg(value_deg: float) -> None:
    """度 -> ラジアン -> 度 の往復が元の値に一致する。"""
    roundtrip = units.rad_to_deg(units.deg_to_rad(value_deg))
    assert roundtrip == pytest.approx(value_deg)


@pytest.mark.parametrize("value_rad", [0.0, math.pi / 4, -math.pi / 2, 2 * math.pi])
def test_angle_round_trip_rad_to_deg_to_rad(value_rad: float) -> None:
    """ラジアン -> 度 -> ラジアン の往復が元の値に一致する。"""
    roundtrip = units.deg_to_rad(units.rad_to_deg(value_rad))
    assert roundtrip == pytest.approx(value_rad)


# ---------------------------------------------------------------------------
# 非有限値の透過（検証は params の境界で行うため、ここでは加工しない）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "func",
    [
        units.mm_per_s_to_mm_per_ms,
        units.mm_per_ms_to_mm_per_s,
        units.mm_per_s2_to_mm_per_ms2,
        units.mm_per_ms2_to_mm_per_s2,
        units.deg_to_rad,
        units.rad_to_deg,
    ],
)
def test_infinite_values_pass_through(func) -> None:
    assert func(math.inf) == math.inf
    assert func(-math.inf) == -math.inf


@pytest.mark.parametrize(
    "func",
    [
        units.mm_per_s_to_mm_per_ms,
        units.mm_per_ms_to_mm_per_s,
        units.mm_per_s2_to_mm_per_ms2,
        units.mm_per_ms2_to_mm_per_s2,
        units.deg_to_rad,
        units.rad_to_deg,
    ],
)
def test_nan_passes_through(func) -> None:
    assert math.isnan(func(math.nan))
