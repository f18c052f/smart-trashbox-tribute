"""投擲物理モデル（design.md「ThrowPhysics」/ 要件 1.1, 1.2, 1.3, 1.6, 1.7）。

空気抵抗を無視した放物運動のみを扱う: `x`・`y` は等速、`z` は等加速度
（design.md「ThrowPhysics」Responsibilities）。

内部は速度を mm/ms、重力を mm/ms^2 で保持し、外部公開単位（mm/s・mm/s^2・
度）との換算はすべて `trajectory_sim.units` を経由する（design.md
「ThrowPhysics」Implementation Notes: 「内部の速度は mm/ms、重力は
mm/ms² で保持する」/ 要件 2.6, 7.7）。裸の `1000` や `math.pi/180`
はここに書かない。

`sample_throw` はばらつきを、引数として受け取った乱数器 `rng` から
**固定の順序・固定の回数**（速度 → 仰角 → 方位角 → リリース位置
x, y, z）だけ引いて適用する。順序を変えると同一種でも結果が変わるため、
順序自体が契約である（design.md「ThrowPhysics」Invariants）。標準偏差が
0 の項目は `rng.normalvariate` を呼ばず、平均値をそのまま使う
（乱数消費を発生させない。design.md「ThrowPhysics」Implementation
Notes）。`rng.gauss` は生成値をキャッシュし呼び出し順に状態が残るため
使わず、`rng.normalvariate` のみを用いる。

`solve_true_impact` は真の落下時刻を二次方程式の未来側の根として解析的
に求める。数値探索は行わない。未来側の根が存在しない場合は例外ではなく
`None` を返す（design.md「ThrowPhysics」Responsibilities / 要件1.6）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from random import Random

from trajectory_sim import units
from trajectory_sim.params import ThrowDispersion, ThrowParams

__all__ = ["TrueTrajectory", "ImpactPoint", "sample_throw", "solve_true_impact"]


@dataclass(frozen=True, slots=True)
class TrueTrajectory:
    """空気抵抗を無視した放物運動の真の軌道（design.md「ThrowPhysics」）。

    `t0_ms` はこの軌道自身のローカルな時刻原点（リリース時刻）である。
    `ThrowParams` は絶対的なリリース時刻を持たないため、`sample_throw`
    は常に `t0_ms = 0.0` を採用する。絶対時刻へのオフセットは呼び出し側
    （観測モデル・シナリオ評価器、いずれも別タスク）の責務であり、
    本モジュールの関心事ではない。

    Attributes:
        t0_ms: 軌道のローカル時刻原点（リリース時刻）。
        x0_mm, y0_mm, z0_mm: リリース位置（mm）。
        vx_mm_ms, vy_mm_ms, vz_mm_ms: リリース時の速度成分（mm/ms）。
        gravity_mm_ms2: 重力加速度の大きさ（mm/ms^2）。
    """

    t0_ms: float
    x0_mm: float
    y0_mm: float
    z0_mm: float
    vx_mm_ms: float
    vy_mm_ms: float
    vz_mm_ms: float
    gravity_mm_ms2: float

    def position_at(self, t_ms: float) -> tuple[float, float, float]:
        """任意時刻 `t_ms` における真の位置を返す（要件1.7）。

        `t_ms` は `t0_ms` に対して過去・現在・未来のいずれでもよい。
        軌道自身のパラメータのみから閉形式で算出する純関数であり、
        床面との交差判定は行わない（`solve_true_impact` の責務）。
        """
        dt = t_ms - self.t0_ms
        x = self.x0_mm + self.vx_mm_ms * dt
        y = self.y0_mm + self.vy_mm_ms * dt
        z = self.z0_mm + self.vz_mm_ms * dt - 0.5 * self.gravity_mm_ms2 * dt * dt
        return (x, y, z)


@dataclass(frozen=True, slots=True)
class ImpactPoint:
    """真の落下地点・真の落下時刻（design.md「ThrowPhysics」/ 要件1.2）。"""

    x_mm: float
    y_mm: float
    time_ms: float


def sample_throw(throw: ThrowParams, dispersion: ThrowDispersion, rng: Random) -> TrueTrajectory:
    """投擲条件にばらつきを適用し、真の軌道を生成する（要件1.1, 1.3）。

    `rng` は呼び出し側が試行ごとに用意した独立な乱数器であり
    （design.md「ThrowPhysics」Preconditions）、本関数はそれを消費する
    だけで自前の乱数器を生成・シードしない。

    乱数消費は速度 → 仰角 → 方位角 → リリース位置 x, y, z の順に固定
    する。対応する分散（シグマ）が 0 の項目は `rng.normalvariate` を
    呼ばずに `throw` の値をそのまま使う。
    """
    if dispersion.speed_sigma_mm_s > 0.0:
        speed_mm_s = rng.normalvariate(throw.speed_mm_s, dispersion.speed_sigma_mm_s)
    else:
        speed_mm_s = throw.speed_mm_s

    if dispersion.elevation_sigma_deg > 0.0:
        elevation_deg = rng.normalvariate(throw.elevation_deg, dispersion.elevation_sigma_deg)
    else:
        elevation_deg = throw.elevation_deg

    if dispersion.azimuth_sigma_deg > 0.0:
        azimuth_deg = rng.normalvariate(throw.azimuth_deg, dispersion.azimuth_sigma_deg)
    else:
        azimuth_deg = throw.azimuth_deg

    if dispersion.release_sigma_mm > 0.0:
        release_x_mm = rng.normalvariate(throw.release_x_mm, dispersion.release_sigma_mm)
    else:
        release_x_mm = throw.release_x_mm

    if dispersion.release_sigma_mm > 0.0:
        release_y_mm = rng.normalvariate(throw.release_y_mm, dispersion.release_sigma_mm)
    else:
        release_y_mm = throw.release_y_mm

    if dispersion.release_sigma_mm > 0.0:
        release_z_mm = rng.normalvariate(throw.release_z_mm, dispersion.release_sigma_mm)
    else:
        release_z_mm = throw.release_z_mm

    speed_mm_ms = units.mm_per_s_to_mm_per_ms(speed_mm_s)
    elevation_rad = units.deg_to_rad(elevation_deg)
    azimuth_rad = units.deg_to_rad(azimuth_deg)
    vx_mm_ms = speed_mm_ms * math.cos(elevation_rad) * math.cos(azimuth_rad)
    vy_mm_ms = speed_mm_ms * math.cos(elevation_rad) * math.sin(azimuth_rad)
    vz_mm_ms = speed_mm_ms * math.sin(elevation_rad)
    gravity_mm_ms2 = units.mm_per_s2_to_mm_per_ms2(throw.gravity_mm_s2)

    return TrueTrajectory(
        t0_ms=0.0,
        x0_mm=release_x_mm,
        y0_mm=release_y_mm,
        z0_mm=release_z_mm,
        vx_mm_ms=vx_mm_ms,
        vy_mm_ms=vy_mm_ms,
        vz_mm_ms=vz_mm_ms,
        gravity_mm_ms2=gravity_mm_ms2,
    )


def solve_true_impact(trajectory: TrueTrajectory) -> ImpactPoint | None:
    """床面 z = 0 との未来側の交点を解析的に求める（要件1.2, 1.6）。

    `z(t0+dt) = z0 + vz*dt - 0.5*g*dt^2 = 0` を `dt` について解く二次
    方程式（`a = 0.5*g`, `b = -vz`, `c = -z0`）を、二次方程式の解の公式
    のみで解く（数値探索は行わない）。

    `ThrowParams.gravity_mm_s2` は構築時検証により常に正であるため
    （`params.py` の `_require_positive_finite`）、`a = 0.5*gravity_mm_ms2`
    が厳密に 0 になる経路は通常の構築を経る限り存在しない。ここでは
    その前提を利用し、`a == 0` の特別扱いは行わない。

    判別式が負の場合（実根が存在しない: 例えば床下でリリースされ、
    かつ上向きの初速が不足している場合）は `None` を返す。

    実根が存在する場合、`dt > 0`（`trajectory.t0_ms` より厳密に未来）
    を満たす根を採用する:
    - 一方のみが正なら、その根を採用する
    - 両方が正の場合（`z0 < 0` で、床の下からリリースされ、かつ床を
      上向きに一度通過してから再び下向きに横切るなど）は、床面を
      最初に横切る時刻である**小さい方の正根**を採用する（軌道が
      物理的に最初に床へ到達する時刻が真の落下時刻であるという解釈）
    - どちらも 0 以下なら `None` を返す
    """
    a = 0.5 * trajectory.gravity_mm_ms2
    b = -trajectory.vz_mm_ms
    c = -trajectory.z0_mm

    discriminant = b * b - 4.0 * a * c
    if discriminant < 0.0:
        return None

    sqrt_discriminant = math.sqrt(discriminant)
    root_lo = (-b - sqrt_discriminant) / (2.0 * a)
    root_hi = (-b + sqrt_discriminant) / (2.0 * a)
    if root_lo > root_hi:
        root_lo, root_hi = root_hi, root_lo

    if root_hi <= 0.0:
        return None
    dt = root_lo if root_lo > 0.0 else root_hi

    time_ms = trajectory.t0_ms + dt
    x_mm = trajectory.x0_mm + trajectory.vx_mm_ms * dt
    y_mm = trajectory.y0_mm + trajectory.vy_mm_ms * dt
    return ImpactPoint(x_mm=x_mm, y_mm=y_mm, time_ms=time_ms)
