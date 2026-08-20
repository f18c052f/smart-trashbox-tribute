"""ImpactSolver の検証（要件 3.1 / 3.2 / 3.4 / 3.6 / 6.3）。

`tests/prediction_core/analytic.py` は `src/prediction_core/impact.py` を
一切 import しない独立した物理オラクルである（同ファイルの docstring 参照）。
本テストはそのオラクルの `analytic_floor_impact` が返す解と、
`solve_floor_impact` が返す解を、同一の物理的軌道について突き合わせる。
比較が「同じコードを同じコードと比べる」循環にならないことがこの構成の目的である。
"""

from __future__ import annotations

import math

from analytic import KnownTrajectory, analytic_floor_impact

from prediction_core.impact import FloorImpact, solve_floor_impact
from prediction_core.types import InvalidReason, TrajectoryParameters


def _trajectory_params_at_t_ref(
    known: KnownTrajectory, t_ref_ms: float
) -> TrajectoryParameters:
    """`KnownTrajectory`(t=0 基準)を `t_ref_ms` 基準の `TrajectoryParameters` へ変換する。

    `TrajectoryParameters` の位置・速度は `t_ref_ms` を時間原点として表すため
    (design.md「CoreTypes」)、`KnownTrajectory` の t=0 基準の値をそのまま
    詰めると t_ref と t=0 の混同バグになる(tasks.md 2.1 の Implementation Notes
    で指摘された既知の落とし穴)。`position_at_ms(t_ref_ms)` で t_ref 時点の
    厳密な位置を求め、vz も t_ref 時点の値(vz0 - g*t_ref_s)へ換算してから
    詰める。
    """
    x0, y0, z0 = known.position_at_ms(t_ref_ms)
    t_ref_s = t_ref_ms / 1000.0
    vz_at_t_ref = known.vz_mm_s - known.gravity_mm_s2 * t_ref_s
    return TrajectoryParameters(
        t_ref_ms=t_ref_ms,
        x0_mm=x0,
        y0_mm=y0,
        z0_mm=z0,
        estimated_vx_mm_s=known.vx_mm_s,
        estimated_vy_mm_s=known.vy_mm_s,
        estimated_vz_mm_s=vz_at_t_ref,
        gravity_mm_s2=known.gravity_mm_s2,
    )


def test_solve_floor_impact_selects_future_root_when_past_and_future_roots_exist() -> (
    None
):
    """上昇中の軌道で過去側・未来側両方に根がある場合、未来側の最小根を選ぶ(要件3.2)。"""
    known = KnownTrajectory(
        x0_mm=0.0,
        vx_mm_s=100.0,
        y0_mm=0.0,
        vy_mm_s=-50.0,
        z0_mm=500.0,
        vz_mm_s=2000.0,
        gravity_mm_s2=9806.65,
    )
    # z(t) = 500 + 2000*t_s - 0.5*9806.65*t_s^2 = 0
    # 判別式 D = vz^2 + 2*g*z0 = 2000^2 + 2*9806.65*500 > 0 なので2根とも実数。
    # 積 c/a = -z0/(0.5g) < 0 なので2根の符号は異なる(過去側と未来側に1根ずつ)。
    t_ref_ms = 0.0
    latest_time_ms = 0.0
    trajectory = _trajectory_params_at_t_ref(known, t_ref_ms)

    result = solve_floor_impact(trajectory, latest_time_ms)
    expected = analytic_floor_impact(known, latest_time_ms)

    assert expected is not None
    assert isinstance(result, FloorImpact)
    assert result.hit_time_ms > latest_time_ms
    assert math.isclose(result.hit_time_ms, expected.hit_time_ms, rel_tol=1e-9)
    assert math.isclose(result.hit_x_mm, expected.hit_x_mm, rel_tol=1e-9, abs_tol=1e-6)
    assert math.isclose(result.hit_y_mm, expected.hit_y_mm, rel_tol=1e-9, abs_tol=1e-6)


def test_solve_floor_impact_returns_invalid_when_discriminant_is_negative() -> None:
    """判別式 D = vz^2 + 2*g*z0 が負の場合、無効理由を返す(要件6.3)。"""
    # z0 < 0 (床下)かつ |vz| が小さいと D = vz^2 + 2*g*z0 < 0 になり得る。
    # vz=10, g=9806.65, z0=-1.0 -> D = 100 + 2*9806.65*(-1.0) = 100 - 19613.3 < 0。
    known = KnownTrajectory(
        x0_mm=0.0,
        vx_mm_s=0.0,
        y0_mm=0.0,
        vy_mm_s=0.0,
        z0_mm=-1.0,
        vz_mm_s=10.0,
        gravity_mm_s2=9806.65,
    )
    discriminant = known.vz_mm_s**2 + 2.0 * known.gravity_mm_s2 * known.z0_mm
    assert discriminant < 0.0  # このテストの前提を明示的に固定する

    trajectory = _trajectory_params_at_t_ref(known, t_ref_ms=0.0)

    result = solve_floor_impact(trajectory, latest_time_ms=0.0)

    assert result is InvalidReason.NO_FUTURE_FLOOR_CROSSING


def test_solve_floor_impact_returns_invalid_when_all_real_roots_are_in_the_past() -> (
    None
):
    """実根は存在するが、全根が latest_time_ms 以下の場合は無効理由を返す(要件6.3)。

    D<0 のケースとは区別される別の分岐(タスクの受け入れ基準が明示的に
    別ケースとして要求している)。
    """
    known = KnownTrajectory(
        x0_mm=0.0,
        vx_mm_s=0.0,
        y0_mm=0.0,
        vy_mm_s=0.0,
        z0_mm=500.0,
        vz_mm_s=2000.0,
        gravity_mm_s2=9806.65,
    )
    # 未来側の根の時刻を求め、それより後を latest_time_ms とすることで
    # 両根とも latest_time_ms 以下になるようにする。
    reference_impact = analytic_floor_impact(known, after_time_ms=0.0)
    assert reference_impact is not None
    latest_time_ms = reference_impact.hit_time_ms + 1000.0

    trajectory = _trajectory_params_at_t_ref(known, t_ref_ms=0.0)

    result = solve_floor_impact(trajectory, latest_time_ms=latest_time_ms)

    assert result is InvalidReason.NO_FUTURE_FLOOR_CROSSING


def test_solve_floor_impact_matches_analytic_oracle_with_nonzero_t_ref() -> None:
    """t_ref_ms が 0 でない場合でも、時間基準を復元して解析解と一致する(要件3.4)。"""
    known = KnownTrajectory(
        x0_mm=-200.0,
        vx_mm_s=150.0,
        y0_mm=80.0,
        vy_mm_s=-40.0,
        z0_mm=5000.0,
        vz_mm_s=800.0,
        gravity_mm_s2=9806.65,
    )
    t_ref_ms = 1_000.0
    latest_time_ms = 1_030.0
    trajectory = _trajectory_params_at_t_ref(known, t_ref_ms)

    result = solve_floor_impact(trajectory, latest_time_ms)
    expected = analytic_floor_impact(known, latest_time_ms)

    assert expected is not None
    assert isinstance(result, FloorImpact)
    # 落下時刻は入力(t=0基準の絶対時刻)と同一の時間基準で返る(要件3.4)。
    assert math.isclose(result.hit_time_ms, expected.hit_time_ms, rel_tol=1e-9)
    assert math.isclose(result.hit_x_mm, expected.hit_x_mm, rel_tol=1e-9, abs_tol=1e-6)
    assert math.isclose(result.hit_y_mm, expected.hit_y_mm, rel_tol=1e-9, abs_tol=1e-6)


def test_solve_floor_impact_picks_smaller_of_two_future_roots() -> None:
    """未来側に2根ある場合、より早い方(最小)を落下点として選ぶ(要件3.2)。

    観測基準時刻の時点で床下(z0<0)から上昇中の軌道は、上向きに床面を
    通過する根と、その後の頂点を経て再び下向きに通過する根の、2つとも
    未来側に持ち得る。この場合でも「最も早い」交点を選ぶことを固定する
    (min と max を取り違えると誤って遅い方の根を選んでしまう)。
    """
    known = KnownTrajectory(
        x0_mm=0.0,
        vx_mm_s=100.0,
        y0_mm=0.0,
        vy_mm_s=0.0,
        z0_mm=-100.0,
        vz_mm_s=2000.0,
        gravity_mm_s2=9806.65,
    )
    discriminant = known.vz_mm_s**2 + 2.0 * known.gravity_mm_s2 * known.z0_mm
    assert discriminant > 0.0  # このテストの前提: 判別式は正で2実根を持つ

    t_ref_ms = 0.0
    latest_time_ms = 0.0
    trajectory = _trajectory_params_at_t_ref(known, t_ref_ms)

    result = solve_floor_impact(trajectory, latest_time_ms)
    expected = analytic_floor_impact(known, latest_time_ms)

    assert expected is not None
    assert isinstance(result, FloorImpact)
    assert math.isclose(result.hit_time_ms, expected.hit_time_ms, rel_tol=1e-9)
    # 2根とも未来側にあることを明示的に確認する(このテストの前提)。
    # より遅い根(頂点を経た後の再通過)ではないことを、解析解と突き合わせて保証する。
    later_root_time_s = (known.vz_mm_s + math.sqrt(discriminant)) / known.gravity_mm_s2
    assert (result.hit_time_ms / 1000.0) < later_root_time_s - 1e-9


def test_solve_floor_impact_applies_velocity_term_to_hit_position() -> None:
    """hit_x_mm / hit_y_mm が単なる初期位置のコピーでなく、速度項が反映されている。"""
    known = KnownTrajectory(
        x0_mm=100.0,
        vx_mm_s=300.0,
        y0_mm=-60.0,
        vy_mm_s=250.0,
        z0_mm=900.0,
        vz_mm_s=500.0,
        gravity_mm_s2=9806.65,
    )
    t_ref_ms = 0.0
    latest_time_ms = 0.0
    trajectory = _trajectory_params_at_t_ref(known, t_ref_ms)

    result = solve_floor_impact(trajectory, latest_time_ms)

    assert isinstance(result, FloorImpact)
    # 落下までに有限の時間がかかり、vx/vy が非ゼロなので、位置は x0/y0 から動く。
    assert not math.isclose(result.hit_x_mm, trajectory.x0_mm, abs_tol=1e-6)
    assert not math.isclose(result.hit_y_mm, trajectory.y0_mm, abs_tol=1e-6)


def test_solve_floor_impact_rejects_a_root_exactly_at_latest_time_ms() -> None:
    """落下点は latest_time_ms より真に後でなければならず、等しい場合は無効(要件3.2)。

    `hit_time_ms` を新たな `latest_time_ms` として同じ軌道に再度問い合わせると、
    その根はもはや「真に後」ではないため、無効理由が返らなければならない
    (`>` を `>=` に取り違えると、この境界で誤って有効な結果を返してしまう)。
    自前の再計算ではなく `solve_floor_impact` 自身が最初に返した
    `hit_time_ms` をそのまま再利用することで、独立計算との浮動小数点誤差の
    混入を避け、境界を厳密に固定する。
    """
    known = KnownTrajectory(
        x0_mm=0.0,
        vx_mm_s=100.0,
        y0_mm=0.0,
        vy_mm_s=-50.0,
        z0_mm=500.0,
        vz_mm_s=2000.0,
        gravity_mm_s2=9806.65,
    )
    trajectory = _trajectory_params_at_t_ref(known, t_ref_ms=0.0)

    first = solve_floor_impact(trajectory, latest_time_ms=0.0)
    assert isinstance(first, FloorImpact)

    second = solve_floor_impact(trajectory, latest_time_ms=first.hit_time_ms)

    assert second is InvalidReason.NO_FUTURE_FLOOR_CROSSING
