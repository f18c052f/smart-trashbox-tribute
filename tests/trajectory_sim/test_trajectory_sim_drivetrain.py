"""機体運動性能モデルのうち到達可否の閉形式判定のテスト
（要件5.2, 6.2 / design.md「DrivetrainModel」）。

`trajectory_sim.drivetrain` が以下を満たすことを固定する:

- `max_distance_from_rest` の三角形プロファイルと台形プロファイルが
  `hold_time_ms == t_sat` の境界で連続であること
- `max_decel_mm_s2 == max_accel_mm_s2` のとき、`min_time_to_stop_at` が
  返す所要時間が、加速フェーズ（または減速フェーズ）単体の所要時間の
  ちょうど2倍になること（design.md「DrivetrainModel」Validation）
- 台形プロファイル（最高速度への飽和が起きる場合）の
  `max_distance_from_rest` / `min_time_to_stop_at` が手計算と一致すること
- `is_reachable` が通過方針・停止方針それぞれで正しく到達可否を判定する
  こと
- `distance_mm == 0` / `hold_time_ms == 0` の境界で例外や NaN が生じない
  こと
- これら3関数が `DrivetrainParams` の値のみに依存し、ハードコードされた
  性能定数を持たないこと（design.md「DrivetrainModel」Responsibilities
  & Constraints: 「機体性能値を自前で持たない」）

ファイル名は `test_trajectory_sim_drivetrain.py`
（`tests/prediction_core/` に同名の衝突するテストファイルは存在しない。
`tests/trajectory_sim/test_trajectory_sim_*.py` の命名方針を踏襲する。
tasks.md「Implementation Notes」参照）。

本タスク（2.2）の範囲は `max_distance_from_rest` / `min_time_to_stop_at`
/ `is_reachable` のみである。`TargetUpdate` / `MotionState` / `simulate`
（後続タスク2.3の範囲）はここでは扱わない。
"""

from __future__ import annotations

import math
import random

import pytest

from trajectory_sim import drivetrain, units
from trajectory_sim.params import CatchPolicy, DrivetrainParams


def _drivetrain(**overrides: float) -> DrivetrainParams:
    """検証を通る最小構成の `DrivetrainParams` を返す（明示指定分のみ上書き）。

    既定値は `max_speed_mm_s=1000.0`（内部単位で `v_max=1.0mm/ms`）、
    `max_accel_mm_s2=max_decel_mm_s2=2000.0`（内部単位で `a=0.002mm/ms^2`）
    であり、`t_sat = v_max / a_max = 500.0ms` になるよう選んだ。
    """
    base: dict[str, float] = dict(
        max_speed_mm_s=1000.0,
        max_accel_mm_s2=2000.0,
        max_decel_mm_s2=2000.0,
        control_period_ms=10.0,
        command_latency_ms=5.0,
    )
    base.update(overrides)
    return DrivetrainParams(**base)


# ---------------------------------------------------------------------------
# max_distance_from_rest: 三角形/台形プロファイルの境界連続性
# ---------------------------------------------------------------------------


def test_max_distance_from_rest_is_continuous_at_saturation_boundary() -> None:
    """`hold_time_ms == t_sat` で三角形式と台形式が一致する（観測可能な完了状態）。"""
    params = _drivetrain()
    v_max = units.mm_per_s_to_mm_per_ms(params.max_speed_mm_s)
    a_max = units.mm_per_s2_to_mm_per_ms2(params.max_accel_mm_s2)
    t_sat = v_max / a_max

    triangular_value = 0.5 * a_max * t_sat**2
    trapezoidal_value = v_max * (t_sat - 0.5 * t_sat)

    assert triangular_value == pytest.approx(trapezoidal_value)
    assert drivetrain.max_distance_from_rest(t_sat, params) == pytest.approx(triangular_value)
    assert drivetrain.max_distance_from_rest(t_sat, params) == pytest.approx(trapezoidal_value)


def test_max_distance_from_rest_triangular_branch_matches_hand_calculation() -> None:
    """飽和前（三角形プロファイル）の距離が `0.5 * a_max * T**2` と一致する。"""
    params = _drivetrain()
    hold_time_ms = 100.0  # t_sat=500ms より十分小さい
    a_max = units.mm_per_s2_to_mm_per_ms2(params.max_accel_mm_s2)
    expected = 0.5 * a_max * hold_time_ms**2

    assert drivetrain.max_distance_from_rest(hold_time_ms, params) == pytest.approx(expected)


def test_max_distance_from_rest_trapezoidal_branch_matches_hand_calculation() -> None:
    """飽和後（台形プロファイル）の距離が手計算（750mm）と一致する。

    `_drivetrain()` の既定値では `v_max=1.0mm/ms`, `t_sat=500ms` なので、
    `hold_time_ms=1000ms` は台形プロファイルになる:
    `distance = v_max * (hold_time_ms - 0.5 * t_sat) = 1.0 * (1000 - 250) = 750`
    """
    params = _drivetrain()
    hold_time_ms = 1000.0

    assert drivetrain.max_distance_from_rest(hold_time_ms, params) == pytest.approx(750.0)


# ---------------------------------------------------------------------------
# min_time_to_stop_at: a_max == a_decel のときの「2倍」関係
# ---------------------------------------------------------------------------


def test_min_time_to_stop_at_is_double_phase_time_when_accel_equals_decel() -> None:
    """`a_max == a_decel` のとき、停止方針の所要時間が単一フェーズの2倍になる
    （観測可能な完了状態、design.md「DrivetrainModel」Validation）。

    `v_max` を十分大きく取り、三角形プロファイル
    （`v_peak_unsat <= v_max`）が確実に選ばれるようにする。
    `t_half = v_peak_unsat / a = sqrt(distance_mm / a)`（`a_max == a_decel
    == a` のときの加速フェーズ単体の所要時間）と比較し、
    `min_time_to_stop_at(distance_mm, params) == 2 * t_half` を確認する。

    注意: これは「同じ距離を止まらず加速だけで進む場合」との比較
    （`sqrt(2)` 倍になる）ではない。あくまで「加速フェーズ時間 +
    減速フェーズ時間」であり、両者が等しいときに単純に2倍になるという
    対称性による関係である。
    """
    params = _drivetrain(
        max_speed_mm_s=10000.0,  # 十分大きく、確実に三角形プロファイルにする
        max_accel_mm_s2=1000.0,
        max_decel_mm_s2=1000.0,
    )
    distance_mm = 100.0
    a = units.mm_per_s2_to_mm_per_ms2(params.max_accel_mm_s2)
    t_half = math.sqrt(distance_mm / a)

    assert drivetrain.min_time_to_stop_at(distance_mm, params) == pytest.approx(2 * t_half)


def test_min_time_to_stop_at_triangular_branch_matches_hand_calculation() -> None:
    """三角形プロファイルの所要時間が `v_peak_unsat * (1/a_max + 1/a_decel)` と一致する。"""
    params = _drivetrain(
        max_speed_mm_s=10000.0,
        max_accel_mm_s2=1000.0,
        max_decel_mm_s2=1000.0,
    )
    distance_mm = 100.0
    a_max = units.mm_per_s2_to_mm_per_ms2(params.max_accel_mm_s2)
    a_decel = units.mm_per_s2_to_mm_per_ms2(params.max_decel_mm_s2)
    v_peak_unsat = math.sqrt(2.0 * distance_mm * a_max * a_decel / (a_max + a_decel))
    expected = v_peak_unsat * (1.0 / a_max + 1.0 / a_decel)

    assert drivetrain.min_time_to_stop_at(distance_mm, params) == pytest.approx(expected)


def test_min_time_to_stop_at_trapezoidal_branch_matches_hand_calculation() -> None:
    """台形プロファイル（最高速度へ飽和する場合）の所要時間が手計算（2100ms）と一致する。

    `max_speed_mm_s=500.0`（内部 `v_max=0.5mm/ms`）、
    `max_accel_mm_s2=max_decel_mm_s2=5000.0`（内部 `a=0.005mm/ms^2`）、
    `distance_mm=1000.0` のとき:

    - `v_peak_unsat = sqrt(2*1000*0.005*0.005/0.01) = sqrt(5) ≈ 2.236mm/ms`
      は `v_max=0.5mm/ms` を大きく超えるため台形プロファイルになる
    - `t1 = v_max/a_max = 100ms`, `d1 = 0.5*v_max*t1 = 25mm`
    - `t2 = v_max/a_decel = 100ms`, `d2 = 0.5*v_max*t2 = 25mm`
    - `d_cruise = 1000 - 25 - 25 = 950mm`, `t_cruise = 950/0.5 = 1900ms`
    - 合計 `100 + 1900 + 100 = 2100ms`
    """
    params = _drivetrain(
        max_speed_mm_s=500.0,
        max_accel_mm_s2=5000.0,
        max_decel_mm_s2=5000.0,
    )
    distance_mm = 1000.0

    assert drivetrain.min_time_to_stop_at(distance_mm, params) == pytest.approx(2100.0)


# ---------------------------------------------------------------------------
# ゼロ距離 / ゼロ持ち時間の境界
# ---------------------------------------------------------------------------


def test_max_distance_from_rest_at_zero_hold_time_is_zero() -> None:
    """`hold_time_ms=0` では到達可能距離は 0（三角形式の自然な境界）。"""
    params = _drivetrain()

    assert drivetrain.max_distance_from_rest(0.0, params) == 0.0


def test_min_time_to_stop_at_zero_distance_is_zero() -> None:
    """`distance_mm=0` では所要時間は 0（`v_peak_unsat=sqrt(0)=0` の自然な境界）。"""
    params = _drivetrain()

    assert drivetrain.min_time_to_stop_at(0.0, params) == 0.0


# ---------------------------------------------------------------------------
# min_time_to_stop_at: 三角形/台形の境界での浮動小数点丸め誤差に対する頑健性
#
# `v_peak_unsat` は独立した式で計算されるため、三角形/台形の境界
# （`v_peak_unsat` が `v_max` にごくわずかに近い場合）付近では、
# `d1 + d2`（台形式側の計算）が IEEE 754 の丸め誤差により `distance_mm`
# をわずかに上回ることがあり、`d_cruise = distance_mm - d1 - d2` が
# 極小の負値になり得る。`min_time_to_stop_at` は正当な
# `DrivetrainParams` と `distance_mm >= 0` に対して例外を送出して
# はならない（レビュー指摘の再発防止）。
# ---------------------------------------------------------------------------


def test_min_time_to_stop_at_does_not_raise_at_boundary_floating_point_edge_case() -> None:
    """レビューで発見された具体的な再現ケース: 境界付近の丸め誤差で
    `AssertionError` を送出していた（修正前）。修正後は有限かつ非負の
    値を例外なく返すこと。
    """
    params = DrivetrainParams(
        max_speed_mm_s=1752.119543015265,
        max_accel_mm_s2=902.4091897469095,
        max_decel_mm_s2=2518.6788895442264,
        control_period_ms=10.0,
        command_latency_ms=5.0,
    )
    distance_mm = 2310.3906377742014

    result = drivetrain.min_time_to_stop_at(distance_mm, params)

    assert math.isfinite(result)
    assert result >= 0.0


def test_min_time_to_stop_at_is_continuous_at_triangular_trapezoidal_boundary() -> None:
    """三角形/台形の分岐境界（`v_peak_unsat == v_max` となる
    `distance_mm`）で、`min_time_to_stop_at` が三角形式の値と
    連続であること（境界での丸め誤差クランプが結果を歪めていないことの
    確認、`test_max_distance_from_rest_is_continuous_at_saturation_boundary`
    の精神を踏襲）。

    境界距離は `v_peak_unsat = v_max` を
    `distance_mm = v_peak**2/(2*a_max) + v_peak**2/(2*a_decel)` に
    代入して解いた
    `distance_mm = 0.5 * v_max**2 * (a_max + a_decel) / (a_max * a_decel)`
    （内部 mm/ms 単位）で計算する。
    """
    params = _drivetrain(
        max_speed_mm_s=1752.119543015265,
        max_accel_mm_s2=902.4091897469095,
        max_decel_mm_s2=2518.6788895442264,
    )
    v_max = units.mm_per_s_to_mm_per_ms(params.max_speed_mm_s)
    a_max = units.mm_per_s2_to_mm_per_ms2(params.max_accel_mm_s2)
    a_decel = units.mm_per_s2_to_mm_per_ms2(params.max_decel_mm_s2)
    boundary_distance_mm = 0.5 * v_max**2 * (a_max + a_decel) / (a_max * a_decel)

    triangular_value = v_max * (1.0 / a_max + 1.0 / a_decel)

    assert drivetrain.min_time_to_stop_at(boundary_distance_mm, params) == pytest.approx(
        triangular_value
    )


def test_min_time_to_stop_at_never_raises_near_boundary_for_random_params() -> None:
    """境界距離付近でのランダムな `DrivetrainParams` の組み合わせに対し、
    `min_time_to_stop_at` が例外を送出しないことを軽量な確率的テストで
    確認する（CI での実行時間を抑えるため試行数は小さく保つ）。
    """
    rng = random.Random(0)

    for _ in range(500):
        max_speed_mm_s = rng.uniform(100.0, 5000.0)
        max_accel_mm_s2 = rng.uniform(100.0, 5000.0)
        max_decel_mm_s2 = rng.uniform(100.0, 5000.0)
        params = _drivetrain(
            max_speed_mm_s=max_speed_mm_s,
            max_accel_mm_s2=max_accel_mm_s2,
            max_decel_mm_s2=max_decel_mm_s2,
        )
        v_max = units.mm_per_s_to_mm_per_ms(max_speed_mm_s)
        a_max = units.mm_per_s2_to_mm_per_ms2(max_accel_mm_s2)
        a_decel = units.mm_per_s2_to_mm_per_ms2(max_decel_mm_s2)
        boundary_distance_mm = 0.5 * v_max**2 * (a_max + a_decel) / (a_max * a_decel)

        result = drivetrain.min_time_to_stop_at(boundary_distance_mm, params)

        assert math.isfinite(result)
        assert result >= 0.0


# ---------------------------------------------------------------------------
# is_reachable: 通過方針・停止方針それぞれの到達可能/不可能
# ---------------------------------------------------------------------------


def test_is_reachable_pass_through_reachable_case() -> None:
    """通過方針: 持ち時間内の到達可能距離以下なら到達可能。

    `_drivetrain()` の既定値で `hold_time_ms=1000` の到達可能距離は
    750mm（台形プロファイル、手計算済み）。`distance_mm=500` はこれ以下。
    """
    params = _drivetrain()

    assert (
        drivetrain.is_reachable(1000.0, 500.0, params, CatchPolicy.PASS_THROUGH) is True
    )


def test_is_reachable_pass_through_unreachable_case() -> None:
    """通過方針: 到達可能距離を超える距離は到達不可能。

    `hold_time_ms=1000` の到達可能距離は 750mm。`distance_mm=800` は
    これを超える。
    """
    params = _drivetrain()

    assert (
        drivetrain.is_reachable(1000.0, 800.0, params, CatchPolicy.PASS_THROUGH) is False
    )


def test_is_reachable_stop_and_wait_reachable_case() -> None:
    """停止方針: 停止所要時間以上の持ち時間があれば到達可能。

    `_drivetrain()` の既定値（`a_max=a_decel=0.002mm/ms^2` 内部単位、
    `v_max=1.0mm/ms` 内部単位）で `distance_mm=100` のときの
    `v_peak_unsat = sqrt(100*0.002) ≈ 0.4472mm/ms` は `v_max` 以下
    （三角形プロファイル）。所要時間は `v_peak_unsat*2/a ≈ 447.2ms`。
    `hold_time_ms=500` はこれ以上。
    """
    params = _drivetrain()

    assert (
        drivetrain.is_reachable(500.0, 100.0, params, CatchPolicy.STOP_AND_WAIT) is True
    )


def test_is_reachable_stop_and_wait_unreachable_case() -> None:
    """停止方針: 停止所要時間に満たない持ち時間では到達不可能。

    上記と同じ `distance_mm=100` の停止所要時間 ≈447.2ms に対し、
    `hold_time_ms=400` はこれ未満。
    """
    params = _drivetrain()

    assert (
        drivetrain.is_reachable(400.0, 100.0, params, CatchPolicy.STOP_AND_WAIT) is False
    )


# ---------------------------------------------------------------------------
# パラメータ化されていること（ハードコードされた性能定数を持たない）
# ---------------------------------------------------------------------------


def test_max_distance_from_rest_depends_only_on_params() -> None:
    """異なる `DrivetrainParams` を与えると異なる結果になる
    （ハードコードされた性能定数を持たないことの証拠）。
    """
    slow_params = _drivetrain(max_speed_mm_s=1000.0, max_accel_mm_s2=2000.0)
    fast_params = _drivetrain(max_speed_mm_s=5000.0, max_accel_mm_s2=8000.0)
    hold_time_ms = 200.0

    slow_distance = drivetrain.max_distance_from_rest(hold_time_ms, slow_params)
    fast_distance = drivetrain.max_distance_from_rest(hold_time_ms, fast_params)

    assert slow_distance != pytest.approx(fast_distance)


def test_min_time_to_stop_at_depends_only_on_params() -> None:
    """異なる `DrivetrainParams` を与えると異なる結果になる
    （ハードコードされた性能定数を持たないことの証拠）。
    """
    slow_params = _drivetrain(max_accel_mm_s2=1000.0, max_decel_mm_s2=1000.0)
    fast_params = _drivetrain(max_accel_mm_s2=9000.0, max_decel_mm_s2=9000.0)
    distance_mm = 50.0

    slow_time = drivetrain.min_time_to_stop_at(distance_mm, slow_params)
    fast_time = drivetrain.min_time_to_stop_at(distance_mm, fast_params)

    assert slow_time != pytest.approx(fast_time)
