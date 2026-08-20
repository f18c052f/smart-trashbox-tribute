"""平面 z=0 との未来側最早交点の算出（要件 3.1 / 3.2 / 3.4 / 3.6 / 6.3）。

ImpactSolver コンポーネント。L3 層であり、`fitting.py`（TrajectoryFitter）と
同一階層で互いに独立である（design.md「Dependency Direction」）。実行時には
標準ライブラリと `prediction_core.units` / `prediction_core.types` のみを
import する。`prediction_core.config` には依存しない。重力加速度は
`trajectory.gravity_mm_s2` から得るため、本モジュールは `PredictionConfig`
を引数に取らない（design.md「ImpactSolver / Responsibilities & Constraints」）。

**床面は z = 0 固定**であり、床平面パラメータを引数に取らない。床平面の推定・
キャリブレーションは `world-frame-calibration` の責務であり、本モジュールの
責務に含めない（要件 3.1 / 3.6）。

**解く式**（design.md「ImpactSolver」）: `½·g·t̃² − vz·t̃ − z0 = 0`
（`a·t̃² + b·t̃ + c = 0` 形式で `a = ½g`, `b = −vz`, `c = −z0`）。
判別式 `D = b² − 4ac = vz² + 2·g·z0`。

**単位戦略**: `trajectory` の速度・重力加速度フィールドは既に外部公開単位
（mm/s, mm/s^2）であるため、本モジュールの計算は**秒単位**で行う。
`vz_mm_s * t_s = mm` および `gravity_mm_s2 * t_s^2 = mm` は単位変換なしで
次元が整合するため、`prediction_core.units.mm_per_ms_to_mm_per_s` や
`mm_per_s2_to_mm_per_ms2` は使わない（それらは `fitting.py` が用いる
mm/ms 内部単位規約向けであり、本モジュールには不要）。ms<->s の境界変換にのみ
`prediction_core.units.MS_PER_S` を用いる。

**桁落ち対策**（design.md「ImpactSolver」）: 素朴な根の公式ではなく
`q = −½(b + sign(b)·√D)` 形式を用い、2根を `q/a` と `c/q` で得る。
`a = ½g` であり `g > 0`（呼び出し側が保証、Preconditions）なので `a` は
常に非ゼロ。`q` は `b=0` かつ `D=0`（`vz=0` かつ `z0=0`）の場合にのみ
厳密に 0 になり得るため、`c/q` の算出はこの場合をガードする。

`t̃` の解のうち `t̃_latest` より真に大きいもののうち最小を採用する
（要件 3.2）。該当する解が存在しない場合（`D < 0`、または全解が
`t̃_latest` 以下）は `InvalidReason.NO_FUTURE_FLOOR_CROSSING` を値として
返す（例外は送出しない、要件 6.3）。

副作用を持たない純関数である。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from prediction_core.types import InvalidReason, TrajectoryParameters
from prediction_core.units import MS_PER_S

__all__ = ["FloorImpact", "solve_floor_impact"]


@dataclass(frozen=True, slots=True)
class FloorImpact:
    """平面 z=0 との未来側最早交点。

    Attributes:
        hit_x_mm: 落下地点の X 座標（mm）。
        hit_y_mm: 落下地点の Y 座標（mm）。
        hit_time_ms: 落下時刻（ms）。入力サンプルと同一の時間基準（絶対、要件 3.4）。
    """

    hit_x_mm: float
    hit_y_mm: float
    hit_time_ms: float


def solve_floor_impact(
    trajectory: TrajectoryParameters,
    latest_time_ms: float,
) -> FloorImpact | InvalidReason:
    """推定軌道と平面 z=0 の、`latest_time_ms` より真に後にある最も早い交点を返す。

    Preconditions（呼び出し側が保証する。ここでは検証しない）:
        - `trajectory` の全フィールドが有限である。
        - `trajectory.gravity_mm_s2 > 0`。
        - `latest_time_ms >= trajectory.t_ref_ms`。

    Returns:
        成功時は `FloorImpact`（`hit_time_ms > latest_time_ms`）。
        該当する未来側交点が存在しない場合（判別式が負、または全ての実根が
        `latest_time_ms` 以下）は `InvalidReason.NO_FUTURE_FLOOR_CROSSING`
        （例外は送出しない）。
    """
    g = trajectory.gravity_mm_s2
    vz = trajectory.estimated_vz_mm_s
    z0 = trajectory.z0_mm

    a = 0.5 * g
    b = -vz
    c = -z0

    discriminant = b * b - 4.0 * a * c

    if discriminant < 0.0:
        return InvalidReason.NO_FUTURE_FLOOR_CROSSING

    sqrt_d = math.sqrt(discriminant)
    if b >= 0.0:
        q = -0.5 * (b + sqrt_d)
    else:
        q = -0.5 * (b - sqrt_d)

    root1 = q / a
    root2 = c / q if q != 0.0 else None

    t_tilde_latest_s = (latest_time_ms - trajectory.t_ref_ms) / MS_PER_S

    candidate_roots_s = [
        root_s
        for root_s in (root1, root2)
        if root_s is not None and root_s > t_tilde_latest_s
    ]

    if not candidate_roots_s:
        return InvalidReason.NO_FUTURE_FLOOR_CROSSING

    t_tilde_hit_s = min(candidate_roots_s)

    hit_x_mm = trajectory.x0_mm + trajectory.estimated_vx_mm_s * t_tilde_hit_s
    hit_y_mm = trajectory.y0_mm + trajectory.estimated_vy_mm_s * t_tilde_hit_s
    hit_time_ms = trajectory.t_ref_ms + t_tilde_hit_s * MS_PER_S

    return FloorImpact(hit_x_mm=hit_x_mm, hit_y_mm=hit_y_mm, hit_time_ms=hit_time_ms)
