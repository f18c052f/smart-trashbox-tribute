"""単位換算モジュールの検証（要件 1.2 / 2.5 / 10.4）。

design.md「L0-L2 基盤層 / Units」が定める公開面
（`MS_PER_S` / `mm_per_ms_to_mm_per_s` / `mm_per_s2_to_mm_per_ms2`）が
定義どおりに振る舞うことを固定する。

外部契約の単位は 距離 mm / 時刻 ms / 速度 mm/s / 加速度 mm/s^2 であり、
内部計算は mm と ms に統一する。したがって内部の速度は mm/ms、
内部の重力加速度は mm/ms^2 になる。
"""

from __future__ import annotations

import inspect

import pytest

from prediction_core.units import (
    MS_PER_S,
    mm_per_ms_to_mm_per_s,
    mm_per_s2_to_mm_per_ms2,
)


def test_ms_per_s_is_the_single_conversion_factor() -> None:
    """換算係数 MS_PER_S は 1 秒あたりのミリ秒数（1000.0）である（要件 1.2）。"""
    assert MS_PER_S == 1000.0
    assert isinstance(MS_PER_S, float)


@pytest.mark.parametrize(
    ("v_mm_ms", "expected_mm_s"),
    [
        (0.0, 0.0),
        (1.0, 1000.0),
        (0.001, 1.0),
        (-2.5, -2500.0),
        (12.34, 12340.0),
    ],
)
def test_mm_per_ms_to_mm_per_s_scales_by_ms_per_s(
    v_mm_ms: float, expected_mm_s: float
) -> None:
    """内部速度 mm/ms を外部公開の mm/s へ換算する（要件 2.5 / 10.4）。"""
    assert mm_per_ms_to_mm_per_s(v_mm_ms) == pytest.approx(expected_mm_s, rel=1e-12)


@pytest.mark.parametrize("v_mm_ms", [0.0, 1.0, -3.75, 0.0125, 1234.5])
def test_mm_per_ms_to_mm_per_s_is_defined_as_multiplication(v_mm_ms: float) -> None:
    """速度換算は MS_PER_S の乗算として定義される。"""
    assert mm_per_ms_to_mm_per_s(v_mm_ms) == v_mm_ms * MS_PER_S


@pytest.mark.parametrize(
    ("a_mm_s2", "expected_mm_ms2"),
    [
        (0.0, 0.0),
        (1_000_000.0, 1.0),
        (-1_000_000.0, -1.0),
        (9806.65, 9.80665e-3),
        (9800.0, 9.8e-3),
    ],
)
def test_mm_per_s2_to_mm_per_ms2_scales_by_ms_per_s_squared(
    a_mm_s2: float, expected_mm_ms2: float
) -> None:
    """外部指定の加速度 mm/s^2 を内部計算用の mm/ms^2 へ換算する（要件 1.2）。"""
    assert mm_per_s2_to_mm_per_ms2(a_mm_s2) == pytest.approx(expected_mm_ms2, rel=1e-12)


@pytest.mark.parametrize("a_mm_s2", [0.0, 9806.65, -4903.325, 1.0, 250_000.0])
def test_mm_per_s2_to_mm_per_ms2_is_defined_as_division(a_mm_s2: float) -> None:
    """加速度換算は MS_PER_S の 2 乗による除算として定義される。"""
    assert mm_per_s2_to_mm_per_ms2(a_mm_s2) == a_mm_s2 / MS_PER_S**2


@pytest.mark.parametrize("value", [0.0, 1.0, -2.5, 9.80665e-6, 0.0125])
def test_conversion_round_trip_mm_per_ms_to_mm_per_s_to_mm_per_ms2(
    value: float,
) -> None:
    """mm/ms -> mm/s -> mm/ms^2 の往復が元の値へ戻る。

    速度換算を 2 回適用すると係数は MS_PER_S**2 になり、
    加速度換算の除数と一致する。両者が単一の換算係数を共有していることを固定する。
    """
    scaled_once = mm_per_ms_to_mm_per_s(value)
    scaled_twice = mm_per_ms_to_mm_per_s(scaled_once)

    assert mm_per_s2_to_mm_per_ms2(scaled_twice) == pytest.approx(value, rel=1e-12)


def test_gravity_round_trip_keeps_physical_meaning() -> None:
    """重力加速度 9806.65 mm/s^2 が内部単位を経て元の値へ戻る。"""
    g_mm_s2 = 9806.65
    g_mm_ms2 = mm_per_s2_to_mm_per_ms2(g_mm_s2)

    assert g_mm_ms2 == pytest.approx(9.80665e-3, rel=1e-12)
    assert mm_per_ms_to_mm_per_s(
        mm_per_ms_to_mm_per_s(g_mm_ms2)
    ) == pytest.approx(g_mm_s2, rel=1e-12)


def test_conversions_accept_int_and_return_float() -> None:
    """整数入力でも float を返す。"""
    assert isinstance(mm_per_ms_to_mm_per_s(2), float)
    assert isinstance(mm_per_s2_to_mm_per_ms2(2), float)


def test_units_module_depends_on_nothing_in_the_package() -> None:
    """units は L0 層であり、パッケージ内の他モジュールを import しない。"""
    import prediction_core.units as units_module

    source = inspect.getsource(units_module)
    import_lines = [
        line.strip()
        for line in source.splitlines()
        if line.strip().startswith(("import ", "from "))
    ]

    assert not [line for line in import_lines if "prediction_core" in line]
