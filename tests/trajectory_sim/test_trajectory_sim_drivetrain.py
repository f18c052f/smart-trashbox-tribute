"""機体運動性能モデルのうち到達可否の閉形式判定と実運動の数値積分のテスト
（要件4.4, 4.5, 4.7, 5.2, 6.2 / design.md「DrivetrainModel」）。

`trajectory_sim.drivetrain` が以下を満たすことを固定する:

タスク2.2（閉形式）:

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

タスク2.3（数値積分、`TargetUpdate` / `MotionState` / `simulate`）:

- 単一目標を時刻0に与えた条件で、`simulate` の到達距離が2.2の閉形式と
  積分刻み以内の誤差で一致すること（design.md「DrivetrainModel」
  Invariants、本タスクの観測可能な完了状態）
- `updates` が空の場合は始点に静止したままであること
- まだ利用可能になっていない目標更新は無視されること
- 目標の切り替えが制御周期の境界でのみ行われ、指令反映遅れを経た
  「生の」利用可能時刻では切り替わらないこと（要件4.5）
- `MotionState.speed_mm_s` が mm/ms から mm/s への正しい換算であること
- 停止方針で時間経過とともに「加速→減速→停止」という定性的に妥当な
  挙動を示すこと

ファイル名は `test_trajectory_sim_drivetrain.py`
（`tests/prediction_core/` に同名の衝突するテストファイルは存在しない。
`tests/trajectory_sim/test_trajectory_sim_*.py` の命名方針を踏襲する。
tasks.md「Implementation Notes」参照）。
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


# ---------------------------------------------------------------------------
# simulate: 閉形式との一致（タスク2.3の「観測可能な完了状態」）
#
# `_fast_drivetrain` は積分刻み・制御周期・指令反映遅れを、シミュレーション
# 時間スケール（数百ms）に対して無視できるほど小さくした構成を返す。
# `command_latency_ms` / `control_period_ms` は構築時検証（タスク1.4）に
# より厳密に正の値でなければならないため、`0.1ms` という最小限の値を
# 用いる。
# ---------------------------------------------------------------------------


def _fast_drivetrain(**overrides: float) -> DrivetrainParams:
    """`simulate` の閉形式一致テスト用の `DrivetrainParams` を返す。"""
    base: dict[str, float] = dict(
        max_speed_mm_s=1000.0,
        max_accel_mm_s2=2000.0,
        max_decel_mm_s2=2000.0,
        control_period_ms=0.1,
        command_latency_ms=0.1,
        integration_step_ms=0.1,
    )
    base.update(overrides)
    return DrivetrainParams(**base)


def test_simulate_pass_through_matches_closed_form_max_distance() -> None:
    """通過方針: 単一目標を時刻0に与えた条件で、積分結果の到達距離が
    2.2の閉形式（`max_distance_from_rest`）と積分刻み以内の誤差で一致
    する（タスク2.3の観測可能な完了状態、design.md「DrivetrainModel」
    Invariants）。

    目標を到達可能距離の10倍遠くに置き、シミュレーション時間内は常に
    目標方向（+x）が変わらないようにする。これにより通過方針の制御則
    （常に目標方向へ最大加速し、行き過ぎを抑える制御を持たない）が、
    2.2の閉形式が前提とする「常に加速し続け、最高速度で飽和する」
    bang-bang プロファイルと完全に一致する条件を作る。

    許容誤差の根拠（レビュー指摘の再発防止のため、素の閉形式に対する
    ゆるい許容誤差（旧 `abs=0.5mm`）を、系統誤差を打ち消した「補正後の
    期待値」に対する狭い許容誤差に置き換える）:

    一定加速度の区間における semi-implicit Euler 積分（速度を先に
    更新してから、その更新後の速度で位置を更新する方式）は、真の解析解
    `0.5*a*t^2` に対して系統的に `+0.5*a*t*dt` だけ大きい値を返す
    （各ステップの位置更新に「その区間の終端速度」を使う右リーマン和
    のため）。加速フェーズの継続時間は飽和時間 `t_sat = v_max/a_max`
    なので、この加速バイアスは `+0.5*a_max*t_sat*dt` になる。

    これに加え、`command_latency_ms > 0`（構築時検証により必須）である
    ため、目標は時刻0の制御周期境界では有効にならず、最初の1積分刻み
    （`dt`）は静止したまま経過してから加速が始まる。これは
    `hold_time_ms` 全体のうち最高速度で走行できる巡航ステップ数を
    ちょうど1ステップ分減らす効果を持ち、`-v_max*dt` の追加バイアスと
    なる。

    本テストのパラメータ（`t_sat=500ms`, `a_max=0.002mm/ms^2`,
    `v_max=1.0mm/ms`, `dt=0.1ms`、いずれも内部単位）では
    `t_sat` と `hold_time_ms - t_sat` がともに `dt` のちょうど整数倍
    （5000ステップずつ）であるため、上記2つの系統誤差の合計
    `net_bias = 0.5*a_max*t_sat*dt - v_max*dt = 0.05 - 0.1 = -0.05mm`
    が実測値と厳密に一致する（`-0.05000000006mm`、差はステップ数
    10000回分の浮動小数点丸め誤差のみ）。

    「素の閉形式 + net_bias」を補正後の期待値とし、残差の許容誤差は
    浮動小数点丸め誤差の想定（1e-9mm オーダー）に対し十分な安全マージン
    を持たせて `abs=0.01mm` とする。これは「位置更新に更新前の速度を
    使う」バグ（このバイアスの符号を反転させ、net_bias が
    `-0.05-0.1=-0.15mm` になる）と「速度クランプを更新前の速度で判定
    する」バグ（巡航フェーズで飽和速度がわずかに超過し続ける定常状態に
    収束し、net_bias が `+0.05mm` 付近になる）の双方を、意図的な変異
    テストで実際に検出できることを確認済み（いずれも補正後の期待値から
    `abs=0.01mm` を大きく超えて外れる）。
    """
    params = _fast_drivetrain()
    hold_time_ms = 1000.0  # 台形プロファイル（t_sat=500ms を超える）
    expected_distance_mm = drivetrain.max_distance_from_rest(hold_time_ms, params)
    far_target_x_mm = 10.0 * expected_distance_mm
    update = drivetrain.TargetUpdate(
        available_time_ms=0.0, x_mm=far_target_x_mm, y_mm=0.0, impact_time_ms=0.0
    )

    result = drivetrain.simulate(
        0.0, 0.0, [update], params, CatchPolicy.PASS_THROUGH, hold_time_ms
    )

    v_max = units.mm_per_s_to_mm_per_ms(params.max_speed_mm_s)
    a_max = units.mm_per_s2_to_mm_per_ms2(params.max_accel_mm_s2)
    t_sat = v_max / a_max
    dt_ms = params.integration_step_ms
    net_bias_mm = 0.5 * a_max * t_sat * dt_ms - v_max * dt_ms
    expected_with_bias_mm = expected_distance_mm + net_bias_mm

    assert result.time_ms == hold_time_ms
    assert result.y_mm == pytest.approx(0.0, abs=1e-9)
    assert result.x_mm == pytest.approx(expected_with_bias_mm, abs=0.01)


def test_simulate_stop_and_wait_matches_closed_form_min_time_to_stop_at() -> None:
    """停止方針: 単一目標を時刻0に与え、閉形式の所要時間
    （`min_time_to_stop_at`）ちょうどでシミュレーションを終了させると、
    到達距離が目標距離と、速度がほぼ0と一致する（タスク2.3の観測可能な
    完了状態、design.md「DrivetrainModel」Invariants）。

    許容誤差の根拠（レビュー指摘の再発防止のため、旧 `abs=1.0mm` /
    `abs=0.01mm/ms` という広い許容誤差を、根拠付きの狭い値へ置き換える）:

    `test_simulate_pass_through_matches_closed_form_max_distance` と同様の
    系統誤差（加速フェーズの右リーマン和による `+0.5*a_max*t_accel*dt`
    のオーダーの過大評価）が、ここでは減速フェーズにも生じる。減速
    フェーズは速度が単調減少するため、同じ右リーマン和が今度は
    `-0.5*a_decel*t_decel*dt` のオーダーで過小評価側に働き、
    `a_max == a_decel` かつ `t_accel ≈ t_decel`（三角形プロファイルの
    対称性）である本フィクスチャでは、加速フェーズと減速フェーズの
    バイアスがほぼ打ち消し合う。そのため、単一フェーズのみを扱う
    上記テストよりも実際の残差ははるかに小さくなることが期待できる
    （残差は主に、制動距離判定によるフェーズ切替が積分刻みの境界に
    しか起こらないことに起因する、より高次の離散化誤差である）。

    この打ち消し合いにより解析的に厳密な閉形式を手計算するのは煩雑
    なため、ここでは「実装が正しいことをレビューで確認済み」の
    `drivetrain.simulate` を実行して実測したベースライン残差
    （`+0.00258mm`、`test_min_time_to_stop_at_*` 系のテストで固定
    されている `min_time_to_stop_at` 自体の正しさを前提にした残差）
    を基準に、加速フェーズ単体のバイアス見積もり
    `0.5*a_max*(stop_time_ms/2)*dt ≈ 0.039mm` の約2.5倍にあたる
    `abs=0.1mm` を許容誤差とする。旧許容誤差（`abs=1.0mm`）の10分の1
    まで狭めつつ、打ち消し合い前の単相バイアス見積もりにも十分な
    マージンを残す値である。

    速度の許容誤差も同様に、実測残留速度（`≈0.00021mm/ms`）に対し
    十分なマージンを残しつつ、旧許容誤差（`abs=0.01mm/ms`）の10分の1
    にあたる `abs=0.001mm/ms`（=1mm/s）へ狭める。

    このテストのフィクスチャ（`distance_mm=300`）は三角形プロファイル
    （`v_peak_unsat < v_max`）であり、速度クランプが一度も発動しない
    よう意図的に選んである（ドキュメント参照）。そのため「速度クランプ
    を更新前の速度で判定する」バグ（レビュー指摘のバグ(b)）は、この
    フィクスチャでは構造的にまったく発現せず（変異テストで確認済み:
    実測値が変異前後でビット単位まで一致）、本テストはこのバグを検出
    できない。バグ(b)は
    `test_simulate_speed_clamp_applied_using_post_update_speed`
    （速度クランプが実際に発動するよう専用に設計したテスト）が確実に
    検出することを意図的な変異テストで確認済みである。一方、「位置更新
    に更新前の速度を使う」バグ（バグ(a)）は本テストでも検出できる
    ことを変異テストで確認済み（残差が `+0.00258mm` から `+0.157mm`
    へ変化し、`abs=0.1mm` を明確に超える）。
    """
    params = _fast_drivetrain()
    distance_mm = 300.0  # v_peak_unsat = sqrt(0.6) ≈ 0.7746mm/ms < v_max=1.0mm/ms（三角形）
    stop_time_ms = drivetrain.min_time_to_stop_at(distance_mm, params)
    update = drivetrain.TargetUpdate(
        available_time_ms=0.0, x_mm=distance_mm, y_mm=0.0, impact_time_ms=0.0
    )

    result = drivetrain.simulate(
        0.0, 0.0, [update], params, CatchPolicy.STOP_AND_WAIT, stop_time_ms
    )

    assert result.time_ms == stop_time_ms
    assert result.x_mm == pytest.approx(distance_mm, abs=0.1)
    assert result.y_mm == pytest.approx(0.0, abs=1e-9)
    residual_speed_mm_ms = math.hypot(result.vx_mm_ms, result.vy_mm_ms)
    assert residual_speed_mm_ms == pytest.approx(0.0, abs=0.001)


# ---------------------------------------------------------------------------
# simulate: 半陰的(symplectic)Eulerのステップ順序を狭い許容誤差で検証する
#
# 上の2つの閉形式一致テストは許容誤差が広く（±0.5mm/±1.0mm）、レビューで
# 発見された2つの具体的な実装ミス
#   (a) 位置更新に更新前の速度を用いる（速度→位置の更新順序を誤る）
#   (b) 速度クランプの判定を更新前の速度で行う（更新後に上限を超えた
#       まさにそのステップでクランプが漏れる）
# のいずれも検出できないことが変異テストで確認された（意図的にどちらの
# バグも導入して確認済み。理由: 速度→位置の更新順序を入れ替えるバグは
# 「右リーマン和」（正しい実装、閉形式に対し系統的に `+0.5*a*T*dt` だけ
# 大きい値を返す）を「左リーマン和」（`-0.5*a*T*dt` 側にずれる）に変える
# だけであり、素の閉形式との差の絶対値は両者でほぼ同じ大きさになるため、
# 閉形式を中心とした対称な許容誤差では原理的に区別できない）。
#
# 以下の2テストは、ステップ数を数ステップに絞り、定数加速度下の閉形式
# 総和公式から厳密な期待値を手計算し、極めて狭い許容誤差(abs=1e-9)で
# 比較することで、これらの順序バグを確実に検出する。
# ---------------------------------------------------------------------------


def test_simulate_pure_acceleration_matches_hand_computed_step_order() -> None:
    """速度クランプに達しない純粋な等加速度区間で、速度→位置の更新順序
    （semi-implicit Euler）が正しいことを、数ステップの手計算値と
    厳密な許容誤差(abs=1e-9)で照合する。

    目標は始点から遠方の +x 方向（`(1000, 0)`）に固定し、通過方針
    （常に目標方向へ最大加速）を用いることで、制御則の分岐（速度クランプ・
    停止判定）を一切経由しない「純粋な定数加速度の積分」だけを取り出す。

    `control_period_ms=1.0`, `command_latency_ms=0.5`
    （`integration_step_ms=1.0` より小さい値）を用いる。構築時検証
    （タスク1.4）により `command_latency_ms > 0` が必須なため、目標が
    厳密に時刻0から有効になることはできない: `_active_target` は
    `elapsed_ms=0` の時点の制御周期境界 `floor(0/1.0)*1.0=0.0` に対し
    有効反映時刻 `0+0.5=0.5` がこれを上回るため、最初の1ステップ
    （`elapsed_ms=0..1`）は目標が無効のまま（加速度0）で経過し、
    2ステップ目（`elapsed_ms=1.0`、制御周期境界 `1.0` に整列）から
    有効になる。したがって `end_time_ms=4.0`（積分刻み4ステップ）の
    うち、最初の1ステップは静止したままで、残り3ステップ（`n=3`）
    だけが定数加速度 `a_max` を受ける。

    速度0から始まる定数加速度の semi-implicit Euler（速度を先に更新し、
    その更新後の速度で位置を更新する）の厳密な閉形式総和は:

        v_n = a_max * n * dt
        x_n = a_max * dt^2 * n*(n+1)/2

    （`x_n = dt * sum_{k=1}^{n} v_k`, `v_k = a_max*k*dt` の総和）。
    """
    params = _fast_drivetrain(
        max_speed_mm_s=1000.0,  # 十分大きく、この3ステップでは飽和しない
        max_accel_mm_s2=1000.0,  # a_max_internal = 1000/1e6 = 0.001mm/ms^2
        max_decel_mm_s2=1000.0,  # PASS_THROUGH では未使用
        control_period_ms=1.0,
        command_latency_ms=0.5,
        integration_step_ms=1.0,
    )
    a_max = units.mm_per_s2_to_mm_per_ms2(params.max_accel_mm_s2)
    dt_ms = params.integration_step_ms
    n = 3  # 有効な加速ステップ数（1ステップの起動遅延の後）
    end_time_ms = (n + 1) * dt_ms  # 起動遅延1ステップ + 加速3ステップ
    expected_vx_mm_ms = a_max * n * dt_ms
    expected_x_mm = a_max * dt_ms**2 * n * (n + 1) / 2.0

    update = drivetrain.TargetUpdate(
        available_time_ms=0.0, x_mm=1000.0, y_mm=0.0, impact_time_ms=0.0
    )

    result = drivetrain.simulate(
        0.0, 0.0, [update], params, CatchPolicy.PASS_THROUGH, end_time_ms
    )

    assert result.time_ms == end_time_ms
    assert result.y_mm == pytest.approx(0.0, abs=1e-9)
    assert result.vy_mm_ms == pytest.approx(0.0, abs=1e-9)
    assert result.vx_mm_ms == pytest.approx(expected_vx_mm_ms, abs=1e-9)
    assert result.x_mm == pytest.approx(expected_x_mm, abs=1e-9)


def test_simulate_speed_clamp_applied_using_post_update_speed() -> None:
    """速度クランプが「更新後」の速度で判定・適用されることを、クランプが
    実際に発動する数ステップの手計算値と厳密な許容誤差(abs=1e-9)で照合する。

    上のテストと同じ起動遅延の構造（`n=3` の有効な加速ステップ）だが、
    `max_speed_mm_s` を意図的に低く設定し、3ステップ目でちょうど
    最高速度を超えるようにする:

        step1: v: 0 -> a*dt          (< v_max、クランプなし)
        step2: v: a*dt -> 2*a*dt     (< v_max、クランプなし)
        step3: v: 2*a*dt -> 3*a*dt   (> v_max、クランプ発動)
               クランプ後の速度は厳密に v_max になる

    速度クランプが「更新前」の速度で判定されるバグ（レビュー指摘の
    バグ(b)）の下では、判定に使う速度が3ステップ目の直前でもまだ
    `2*a*dt < v_max` であるため、このバグはクランプを一度も発動させず、
    3ステップ目の速度が `3*a*dt`（v_max超過）のまま残る。これは以下の
    期待値と明確に異なる。
    """
    max_accel_mm_s2 = 1000.0
    a_max = units.mm_per_s2_to_mm_per_ms2(max_accel_mm_s2)
    dt_ms = 1.0
    n = 3
    # 3ステップ目でちょうど v_max を超えるよう、2ステップ目までの速度
    # (2*a*dt) と3ステップ目の速度 (3*a*dt) の間に v_max を置く。
    v_max_internal = 2.5 * a_max * dt_ms
    max_speed_mm_s = units.mm_per_ms_to_mm_per_s(v_max_internal)
    params = _fast_drivetrain(
        max_speed_mm_s=max_speed_mm_s,
        max_accel_mm_s2=max_accel_mm_s2,
        max_decel_mm_s2=max_accel_mm_s2,  # PASS_THROUGH では未使用
        control_period_ms=1.0,
        command_latency_ms=0.5,
        integration_step_ms=dt_ms,
    )
    end_time_ms = (n + 1) * dt_ms

    step1_v = a_max * dt_ms
    step1_x = step1_v * dt_ms
    step2_v = step1_v + a_max * dt_ms
    step2_x = step1_x + step2_v * dt_ms
    step3_v_unclamped = step2_v + a_max * dt_ms
    assert step3_v_unclamped > v_max_internal  # 本テストが実際にクランプを発動させる前提の保証
    step3_v = v_max_internal  # クランプ後
    step3_x = step2_x + step3_v * dt_ms

    update = drivetrain.TargetUpdate(
        available_time_ms=0.0, x_mm=1000.0, y_mm=0.0, impact_time_ms=0.0
    )

    result = drivetrain.simulate(
        0.0, 0.0, [update], params, CatchPolicy.PASS_THROUGH, end_time_ms
    )

    assert result.time_ms == end_time_ms
    assert result.y_mm == pytest.approx(0.0, abs=1e-9)
    assert result.vy_mm_ms == pytest.approx(0.0, abs=1e-9)
    assert result.vx_mm_ms == pytest.approx(step3_v, abs=1e-9)
    assert result.x_mm == pytest.approx(step3_x, abs=1e-9)


# ---------------------------------------------------------------------------
# simulate: 目標更新が無い/まだ利用可能でない場合は始点で静止し続ける
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("policy", [CatchPolicy.PASS_THROUGH, CatchPolicy.STOP_AND_WAIT])
def test_simulate_with_no_updates_stays_at_rest(policy: CatchPolicy) -> None:
    """`updates` が空の場合、両方針とも始点に静止したままの状態を返す
    （design.md「DrivetrainModel」Postconditions、タスク2.3の完了条件）。
    """
    params = _drivetrain()

    result = drivetrain.simulate(10.0, -20.0, [], params, policy, 5000.0)

    assert result == drivetrain.MotionState(
        time_ms=5000.0, x_mm=10.0, y_mm=-20.0, vx_mm_ms=0.0, vy_mm_ms=0.0
    )


def test_simulate_ignores_update_not_yet_available_by_end_time() -> None:
    """目標更新の反映時刻（指令反映遅れを含み、制御周期に整列させた後）が
    `end_time_ms` より後になる場合、その更新は一度も有効にならず、
    移動体は始点で静止したままになる（要件4.5）。

    `_drivetrain()` の既定値（`control_period_ms=10.0`,
    `command_latency_ms=5.0`）で `available_time_ms=100.0` の更新は、
    反映時刻 `100+5=105ms` を制御周期10msの境界に整列させると
    `110ms`（105以上で最初の10の倍数）になる。`end_time_ms=100.0` は
    これより前なので、この更新は一度も有効にならない。
    """
    params = _drivetrain()
    update = drivetrain.TargetUpdate(
        available_time_ms=100.0, x_mm=999.0, y_mm=999.0, impact_time_ms=100.0
    )

    result = drivetrain.simulate(
        0.0, 0.0, [update], params, CatchPolicy.PASS_THROUGH, 100.0
    )

    assert result == drivetrain.MotionState(
        time_ms=100.0, x_mm=0.0, y_mm=0.0, vx_mm_ms=0.0, vy_mm_ms=0.0
    )


# ---------------------------------------------------------------------------
# simulate: 目標の切り替えは制御周期の境界でのみ行われる（要件4.5）
# ---------------------------------------------------------------------------


def test_simulate_switches_target_only_at_control_period_boundary() -> None:
    """2件目の目標更新は、その「生の」反映時刻（指令反映遅れを含むが
    制御周期には未整列）ではなく、その時刻以降で最初に訪れる制御周期の
    境界でのみ有効になる（要件4.5、design.md「DrivetrainModel」
    Responsibilities & Constraints: 「目標の切り替えは制御周期の境界での
    み行う」）。

    `_drivetrain()` の既定値（`control_period_ms=10.0`,
    `command_latency_ms=5.0`, `integration_step_ms=1.0`）を用いる。

    - 1件目の更新（`available_time_ms=0.0`, 目標 `(1000, 0)`）の反映
      時刻は `0+5=5ms` であり、制御周期10msの境界に整列すると `10ms`
      から有効になる
    - 2件目の更新（`available_time_ms=20.0`, 目標 `(0, 1000)`）の反映
      時刻は `20+5=25ms` であり、制御周期の境界に整列すると `30ms`
      （25以上で最初の10の倍数）から有効になる。もし整列を行わず
      「生の」反映時刻25msでそのまま切り替わってしまう実装だと、
      `end_time_ms=29.0` の時点で既に y 方向への加速が始まってしまう
      （このテストはその誤りを検出する）

    したがって、`end_time_ms=29.0` まででは y 方向の速度・位置は
    厳密に0のままであり、`end_time_ms=31.0` まで進めると初めて y 方向
    の速度が正になる。
    """
    params = _drivetrain()
    update1 = drivetrain.TargetUpdate(
        available_time_ms=0.0, x_mm=1000.0, y_mm=0.0, impact_time_ms=0.0
    )
    update2 = drivetrain.TargetUpdate(
        available_time_ms=20.0, x_mm=0.0, y_mm=1000.0, impact_time_ms=0.0
    )
    updates = [update1, update2]

    before_switch = drivetrain.simulate(
        0.0, 0.0, updates, params, CatchPolicy.PASS_THROUGH, 29.0
    )
    at_raw_available_time = drivetrain.simulate(
        0.0, 0.0, updates, params, CatchPolicy.PASS_THROUGH, 25.0
    )
    after_switch = drivetrain.simulate(
        0.0, 0.0, updates, params, CatchPolicy.PASS_THROUGH, 31.0
    )

    # 生の反映時刻25ms・その後29msまでは、2件目の目標(y方向)へは一切
    # 切り替わっていない(y方向の速度・位置が厳密に0のまま)。
    assert at_raw_available_time.y_mm == 0.0
    assert at_raw_available_time.vy_mm_ms == 0.0
    assert before_switch.y_mm == 0.0
    assert before_switch.vy_mm_ms == 0.0
    # 1件目の目標(x方向)へは既に加速している。
    assert before_switch.vx_mm_ms > 0.0

    # 制御周期の境界(30ms)を過ぎた31msでは、2件目の目標(y方向)へ
    # 切り替わり、y方向の速度が正になっている。
    assert after_switch.vy_mm_ms > 0.0


# ---------------------------------------------------------------------------
# MotionState.speed_mm_s: mm/ms から mm/s への換算
# ---------------------------------------------------------------------------


def test_motion_state_speed_mm_s_converts_from_internal_mm_per_ms() -> None:
    """`speed_mm_s` は `sqrt(vx^2+vy^2)`（mm/ms）を mm/s に換算した値になる
    （design.md「DrivetrainModel」Service Interface、要件4.7）。

    `vx_mm_ms=0.3`, `vy_mm_ms=0.4` のとき、大きさは `0.5mm/ms` であり、
    `units.mm_per_ms_to_mm_per_s(0.5) == 500.0mm/s` になる。
    """
    state = drivetrain.MotionState(
        time_ms=0.0, x_mm=0.0, y_mm=0.0, vx_mm_ms=0.3, vy_mm_ms=0.4
    )

    assert state.speed_mm_s == pytest.approx(units.mm_per_ms_to_mm_per_s(0.5))
    assert state.speed_mm_s == pytest.approx(500.0)


# ---------------------------------------------------------------------------
# simulate: 停止方針における定性的な整合性（任意時刻の位置・速度の自己整合）
# ---------------------------------------------------------------------------


def test_simulate_stop_and_wait_shows_accelerate_then_decelerate_pattern() -> None:
    """同一シナリオに対し複数の `end_time_ms` で `simulate` を呼び、
    停止方針が「加速→減速→ほぼ停止」という時間最適追従として妥当な
    定性的パターンを示すことを確認する（design.md「DrivetrainModel」
    Responsibilities: 「時間最適(bang-bang)な運動」、要件4.7:
    「任意の時刻における…位置と速度を算出できる」の自己整合性チェック）。
    """
    params = _fast_drivetrain()
    distance_mm = 300.0
    stop_time_ms = drivetrain.min_time_to_stop_at(distance_mm, params)
    update = drivetrain.TargetUpdate(
        available_time_ms=0.0, x_mm=distance_mm, y_mm=0.0, impact_time_ms=0.0
    )
    sample_times_ms = [
        stop_time_ms * 0.1,
        stop_time_ms * 0.25,
        stop_time_ms * 0.5,
        stop_time_ms * 0.75,
        stop_time_ms,
    ]

    states = [
        drivetrain.simulate(0.0, 0.0, [update], params, CatchPolicy.STOP_AND_WAIT, t)
        for t in sample_times_ms
    ]
    distances_mm = [state.x_mm for state in states]
    speeds_mm_ms = [math.hypot(state.vx_mm_ms, state.vy_mm_ms) for state in states]

    # 目標へ向けて進むにつれ、始点からの距離は単調非減少である。
    assert distances_mm == sorted(distances_mm)
    # 序盤(10%地点)は加速中で、中盤(50%地点)より遅い。
    assert speeds_mm_ms[0] < speeds_mm_ms[2]
    # 終盤(100%地点、ちょうど停止予定時刻)は中盤より遅く、ほぼ0である
    # (減速フェーズで確実に減速していることの確認)。
    assert speeds_mm_ms[-1] < speeds_mm_ms[2]
    assert speeds_mm_ms[-1] == pytest.approx(0.0, abs=0.01)
    assert distances_mm[-1] == pytest.approx(distance_mm, abs=1.0)
