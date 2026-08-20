"""解析的サンプル生成ヘルパの検証（要件 7.1）。

`tests/prediction_core/analytic.py` はテスト専用の独立した物理オラクルである。
`docs/requirements.md` §5-B の教科書的な放物運動の式
（`x(t)=x0+vx*t` / `y(t)=y0+vy*t` / `z(t)=z0+vz*t-1/2*g*t^2`）を、
まだ存在しない `src/prediction_core/fitting.py` / `impact.py`
（タスク 2.1 / 2.2）とは独立に、素直な閉形式で実装する。

このヘルパ自体が誤っていると後続タスク（2.1 / 2.2 / 5.2 / 5.3）の
「解析解と一致する」という判定基準そのものが崩れるため、本ファイルは
決定性・物理的正しさ（手計算した1ケース）・交点なしの境界値の3点を固定する。
"""

from __future__ import annotations

import math

from analytic import (
    AnalyticFloorImpact,
    KnownTrajectory,
    add_noise,
    analytic_floor_impact,
    generate_samples,
)


def _sample_trajectory() -> KnownTrajectory:
    """斜方投射に相当する任意の既知軌道（決定性・往復検証に使う）。"""
    return KnownTrajectory(
        x0_mm=100.0,
        vx_mm_s=250.0,
        y0_mm=-50.0,
        vy_mm_s=-30.0,
        z0_mm=800.0,
        vz_mm_s=1500.0,
        gravity_mm_s2=9806.65,
    )


def _free_fall_trajectory() -> KnownTrajectory:
    """鉛直自由落下。手計算できる解析解を持つ（垂直方向のみ、水平成分は 0）。

    z0_mm=1000.0, vz_mm_s=0.0, g=9806.65 mm/s^2 のとき、
    z(t)=0 の解は t = sqrt(2*z0/g) 秒 ≈ 0.4516 秒 ≈ 451.6 ms。
    """
    return KnownTrajectory(
        x0_mm=0.0,
        vx_mm_s=0.0,
        y0_mm=0.0,
        vy_mm_s=0.0,
        z0_mm=1000.0,
        vz_mm_s=0.0,
        gravity_mm_s2=9806.65,
    )


# --- 決定性 -----------------------------------------------------------------


def test_generate_samples_is_deterministic() -> None:
    """同じ引数で2回生成したサンプル列は完全に一致する（要件 7.1）。"""
    trajectory = _sample_trajectory()
    times_ms = [0.0, 10.0, 25.5, 100.0, 250.0]

    first = generate_samples(trajectory, times_ms)
    second = generate_samples(trajectory, times_ms)

    assert first == second


def test_add_noise_is_deterministic_for_the_same_seed() -> None:
    """同じ seed による決定的なノイズ重畳は2回とも完全に一致する（要件 7.1）。"""
    trajectory = _sample_trajectory()
    samples = generate_samples(trajectory, [0.0, 50.0, 120.0, 300.0])

    first = add_noise(samples, seed=1234, stddev_mm=5.0)
    second = add_noise(samples, seed=1234, stddev_mm=5.0)

    assert first == second
    # ノイズが実際に重畳されており、無ノイズの結果と一致しないことも確認する。
    assert first != samples


def test_add_noise_uses_locally_scoped_random_not_global_state() -> None:
    """`random.Random(seed)` によるローカルスコープの乱数器を使う。

    グローバルな `random` モジュールの状態を消費してから呼んでも、
    結果は同じ seed であれば変わらないことを確認する
    （呼び出し順序に依存しないことが要件 7.1 の決定性・順序非依存の前提）。
    """
    import random

    trajectory = _sample_trajectory()
    samples = generate_samples(trajectory, [0.0, 50.0, 120.0])

    baseline = add_noise(samples, seed=777, stddev_mm=3.0)

    # グローバル random の状態を大きく進めてから同じ呼び出しを行う。
    random.seed(999)
    for _ in range(1000):
        random.random()

    after_global_state_consumed = add_noise(samples, seed=777, stddev_mm=3.0)

    assert baseline == after_global_state_consumed


def test_add_noise_does_not_perturb_global_random_state() -> None:
    """`add_noise` の呼び出し自体がグローバル `random` の状態を変化させない。

    上のテストは「呼び出し順序に依存しない」ことしか確認しておらず、
    たとえばグローバル乱数器を毎回 seed で再初期化してから呼ぶような
    実装でも通ってしまう。ここでは呼び出し前後でグローバル
    `random.getstate()` そのものが不変であることを直接検証し、
    `add_noise` がローカルスコープの `random.Random(seed)` だけを
    消費して、グローバル状態には一切触れないことを保証する
    （要件 7.1）。
    """
    import random

    trajectory = _sample_trajectory()
    samples = generate_samples(trajectory, [0.0, 50.0, 120.0])

    random.seed(42)
    state_before = random.getstate()
    next_value_before = random.random()

    # 呼び出し前後で比較できるよう、消費前の状態へ復元してから呼ぶ。
    random.setstate(state_before)
    add_noise(samples, seed=777, stddev_mm=3.0)
    state_after = random.getstate()

    assert state_after == state_before
    # グローバル乱数器の「次の値」も呼び出し前後で変わらないことを確認する。
    random.setstate(state_before)
    next_value_after = random.random()
    assert next_value_after == next_value_before


# --- 物理的正しさ（手計算した1ケース） ---------------------------------------


def test_generate_samples_at_t_zero_returns_exact_initial_position() -> None:
    """t=0 でのサンプルは誤差を含まない厳密な初期位置と一致する。"""
    trajectory = _free_fall_trajectory()

    samples = generate_samples(trajectory, [0.0])

    assert samples[0].t_ms == 0.0
    assert samples[0].x_mm == 0.0
    assert samples[0].y_mm == 0.0
    assert samples[0].z_mm == 1000.0


def test_position_at_ms_mid_flight_matches_hand_computed_z() -> None:
    """t>0 かつ vz!=0 の飛行中の位置が、手計算した重力項込みの値と一致する。

    `_sample_trajectory()`（vz_mm_s=1500.0, gravity_mm_s2=9806.65）の
    t_ms=200.0 における位置を、`z(t) = z0 + vz*t - 0.5*g*t^2`
    （t は秒に変換済み、t_s = 0.2）から独立に手計算する。

        x(0.2) = 100.0 + 250.0*0.2       = 150.0
        y(0.2) = -50.0 + (-30.0)*0.2     = -56.0
        z(0.2) = 800.0 + 1500.0*0.2 - 0.5*9806.65*0.2**2 = 903.867

    `t=0` では重力項 `0.5*g*t^2` が消えて検証にならないため、
    この直接テストで `position_at_ms` の z 式（重力項の係数を含む）を
    単独で固定する。free-fall フィクスチャは vx=vy=0 のため x/y の
    確認にもならず、`analytic_floor_impact` は独自の二次方程式を解く
    別経路であるため、この経路（重力項の係数）はここでしか検証できない。
    """
    trajectory = _sample_trajectory()

    x_mm, y_mm, z_mm = trajectory.position_at_ms(200.0)

    assert math.isclose(x_mm, 150.0, rel_tol=0.0, abs_tol=1e-6)
    assert math.isclose(y_mm, -56.0, rel_tol=0.0, abs_tol=1e-6)
    assert math.isclose(z_mm, 903.867, rel_tol=0.0, abs_tol=1e-6)


def test_analytic_floor_impact_matches_hand_computed_free_fall() -> None:
    """鉛直自由落下の落下時刻が手計算値 sqrt(2*z0/g) と一致する。"""
    trajectory = _free_fall_trajectory()
    expected_hit_time_ms = math.sqrt(2.0 * 1000.0 / 9806.65) * 1000.0

    impact = analytic_floor_impact(trajectory, after_time_ms=0.0)

    assert isinstance(impact, AnalyticFloorImpact)
    assert impact.hit_time_ms == expected_hit_time_ms
    assert math.isclose(impact.hit_time_ms, 451.6, rel_tol=1e-3)
    # 水平成分の初速・初期位置が 0 のため、落下地点は原点のまま。
    assert impact.hit_x_mm == 0.0
    assert impact.hit_y_mm == 0.0
    # analytic_floor_impact が独自に解いた hit_time_ms を
    # position_at_ms（z 式）へ戻したとき、z がちょうど 0 になることを確認する。
    # これにより二次方程式の解と position_at_ms の z 式が同じ物理量を
    # 表していることを結びつける（両者が乖離すれば検出できる）。
    _hx, _hy, hit_z_mm = trajectory.position_at_ms(impact.hit_time_ms)
    assert math.isclose(hit_z_mm, 0.0, rel_tol=0.0, abs_tol=1e-6)


# --- 未来側に交点が存在しない境界値 ------------------------------------------


def test_analytic_floor_impact_returns_none_when_already_past_the_crossing() -> None:
    """交点が `after_time_ms` より後に存在しない場合は None を返す。

    自由落下の解析解は約 451.6 ms で床に到達する。その後の時刻を
    `after_time_ms` として渡すと、未来側の交点は存在しない。
    """
    trajectory = _free_fall_trajectory()

    impact = analytic_floor_impact(trajectory, after_time_ms=500.0)

    assert impact is None


# --- 未来側に交点が2つ存在する場合の「最も早い解」選択 ------------------------


def _two_future_roots_trajectory() -> KnownTrajectory:
    """z=0 より下から上向きに投げ上げ、未来側に2つの正の実根を持つ軌道。

    z0_mm=-500.0（床面より下から開始）、vz_mm_s=5000.0（上向き）とすると、
    `0.5*g*t_s^2 - vz*t_s - z0 = 0` の根の積は `-2*z0/g > 0`、根の和は
    `2*vz/g > 0` となり、判別式が正である限り2根とも正になる
    （物理的な開始点が床の上下どちらかは、このオラクルの式評価には無関係。
    このフィクスチャの目的は根の選択ロジックの検証であり、"投げ上げ→
    上昇中に z=0 を通過→再び z=0 へ落下" という2交点の存在を作ることにある）。

    手計算（秒単位、a=0.5*g=4903.325, b=-vz=-5000.0, c=-z0=500.0）:
        discriminant = vz^2 + 2*g*z0
                     = 5000.0^2 + 2*9806.65*(-500.0)
                     = 25_000_000.0 - 9_806_650.0 = 15_193_350.0  (> 0、2実根)
        sqrt(discriminant) ≈ 3897.8648
        root_a_s = (vz - sqrt_d) / g ≈ (5000.0 - 3897.8648) / 9806.65 ≈ 0.112387 s
        root_b_s = (vz + sqrt_d) / g ≈ (5000.0 + 3897.8648) / 9806.65 ≈ 0.907330 s

    両根とも正（≈112.39 ms と ≈907.33 ms）であり、`after_time_ms=0.0` に対して
    どちらも「未来」である。早い方（≈112.39 ms）が正しい解。
    """
    return KnownTrajectory(
        x0_mm=0.0,
        vx_mm_s=0.0,
        y0_mm=0.0,
        vy_mm_s=0.0,
        z0_mm=-500.0,
        vz_mm_s=5000.0,
        gravity_mm_s2=9806.65,
    )


def test_analytic_floor_impact_selects_earliest_of_two_future_roots() -> None:
    """未来側に2つの正の実根がある場合、早い方（最小根）を返す。

    `_two_future_roots_trajectory()` は判別式が正で、かつ根の積・和がともに
    正になるよう選んだフィクスチャであり、`after_time_ms=0.0` に対して
    2つの実根（≈112.39 ms, ≈907.33 ms）が両方とも未来側に存在する。
    既存の全フィクスチャは `z0_mm > 0` であるため常に根の積が負
    （=未来根が高々1個）になっており、この「2根から最小を選ぶ」経路は
    このテストでしか検証できない（`min(future_roots_s)` を
    `max(future_roots_s)` に取り違える実装でも他のテストは通ってしまう）。
    """
    trajectory = _two_future_roots_trajectory()
    g = trajectory.gravity_mm_s2
    vz = trajectory.vz_mm_s
    z0 = trajectory.z0_mm
    discriminant = vz * vz + 2.0 * g * z0
    sqrt_d = math.sqrt(discriminant)
    expected_smaller_root_ms = (vz - sqrt_d) / g * 1000.0
    expected_larger_root_ms = (vz + sqrt_d) / g * 1000.0

    # フィクスチャが意図通り2つの正の未来根を持つことを確認する。
    assert expected_smaller_root_ms > 0.0
    assert expected_larger_root_ms > 0.0
    assert expected_smaller_root_ms < expected_larger_root_ms

    impact = analytic_floor_impact(trajectory, after_time_ms=0.0)

    assert isinstance(impact, AnalyticFloorImpact)
    assert math.isclose(
        impact.hit_time_ms, expected_smaller_root_ms, rel_tol=0.0, abs_tol=1e-6
    )
    # より遅い方の根では断じてないことも明示する。
    assert not math.isclose(
        impact.hit_time_ms, expected_larger_root_ms, rel_tol=1e-3
    )
