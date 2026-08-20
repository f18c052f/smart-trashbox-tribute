"""解析的サンプル生成ヘルパ（要件 7.1）。テスト専用。

**このモジュールは `prediction_core` パッケージ本体から import しない。**
`src/prediction_core/**` のいずれのモジュールもここに依存してはならない
（design.md「Boundary Commitments / Out of Boundary」、tasks.md 1.6）。

## このモジュールの位置づけ

本モジュールは**独立した物理オラクル**である。まだ存在しない
`src/prediction_core/fitting.py`（TrajectoryFitter, タスク 2.1）や
`src/prediction_core/impact.py`（ImpactSolver, タスク 2.2）の実装を
一切 import・再利用しない。`docs/requirements.md` §5-B が確定している
教科書的な放物運動の式

    x(t) = x0 + vx * t
    y(t) = y0 + vy * t
    z(t) = z0 + vz * t - 1/2 * g * t^2

を、このモジュールだけで素直な閉形式として計算する。タスク 2.1 / 2.2 の
実装をこのオラクルと比較することで、比較が循環（同じコードを同じコードと
比べる）にならないようにするのが目的である。

そのため `analytic_floor_impact` は design.md が ImpactSolver に要求する
「桁落ちに強い根の公式」を採用しない。ここでのテスト入力は通常の値に
限られ、数値安定性の作り込みはこのオラクルの責務ではない
（本 Spec の責務は `src/prediction_core/impact.py` 側にある）。

## 公開 API

- `KnownTrajectory`: 既知の軌道パラメータ（放物運動の6自由度 + 重力加速度）。
  `position_at_ms(t_ms)` で任意時刻の厳密位置 `(x_mm, y_mm, z_mm)` を返す。
- `generate_samples(trajectory, times_ms) -> list[Sample]`:
  誤差を含まない厳密なサンプル列を返す。
- `add_noise(samples, *, seed, stddev_mm) -> list[Sample]`:
  `random.Random(seed)` によるローカルスコープの決定的ガウスノイズを
  各サンプルの x_mm / y_mm / z_mm へ独立に重畳する（t_ms は変更しない。
  観測時刻はセンサのクロックに由来し、位置測定と異なり本ヘルパが
  モデル化する誤差要因ではないため）。
- `AnalyticFloorImpact`: 解析的に求めた落下地点・落下時刻。
- `analytic_floor_impact(trajectory, after_time_ms) -> AnalyticFloorImpact | None`:
  平面 z=0 との交点のうち、`after_time_ms` より真に後にある最も早い解を返す。
  存在しなければ `None`。

## 決定性の契約（要件 7.1）

- `generate_samples` は純関数。同一引数で2回呼んでも完全一致する結果を返す。
- `add_noise` は**呼び出しのたびに新しい `random.Random(seed)` インスタンスを
  生成する**。グローバルな `random` モジュールの状態を一切読み書きしないため、
  他のコードが先にグローバル乱数を消費していても結果は変わらない。

## 単位換算

内部の物理計算は秒単位で行う（教科書の式がそう定義されているため）。
`t_ms` から秒への変換には、このプロジェクトの ms<->s 換算の単一定義点である
`prediction_core.units.MS_PER_S` を再利用する。本ファイルは `src/` 配下の
静的依存ガード（タスク 5.4）の対象外だが、換算係数を二重に定義しないという
同じ規律を適用する。
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Sequence

from prediction_core.types import Sample
from prediction_core.units import MS_PER_S

__all__ = [
    "AnalyticFloorImpact",
    "KnownTrajectory",
    "add_noise",
    "analytic_floor_impact",
    "generate_samples",
]


@dataclass(frozen=True, slots=True)
class KnownTrajectory:
    """既知の放物運動パラメータ（教科書の式そのもの、`docs/requirements.md` §5-B）。

    すべての位置・速度は時刻 `t_ms=0` を基準に定義される
    （`src/prediction_core` の `TrajectoryParameters.t_ref_ms` のような
    可変の基準時刻は持たない。テスト側でサンプル時刻に絶対オフセットを
    与えたい場合は `times_ms` の値そのものをずらせばよい）。

    Attributes:
        x0_mm: t=0 における X 座標（mm）。
        vx_mm_s: X 方向速度（mm/s、一定）。
        y0_mm: t=0 における Y 座標（mm）。
        vy_mm_s: Y 方向速度（mm/s、一定）。
        z0_mm: t=0 における Z 座標（mm）。
        vz_mm_s: t=0 における Z 方向速度（mm/s）。
        gravity_mm_s2: 重力加速度の大きさ（mm/s^2、正値を想定）。
    """

    x0_mm: float
    vx_mm_s: float
    y0_mm: float
    vy_mm_s: float
    z0_mm: float
    vz_mm_s: float
    gravity_mm_s2: float

    def position_at_ms(self, t_ms: float) -> tuple[float, float, float]:
        """時刻 `t_ms` における厳密な位置 `(x_mm, y_mm, z_mm)` を返す。

        `docs/requirements.md` §5-B の式をそのまま評価する。空気抵抗は
        考慮しない。
        """
        t_s = t_ms / MS_PER_S
        x_mm = self.x0_mm + self.vx_mm_s * t_s
        y_mm = self.y0_mm + self.vy_mm_s * t_s
        z_mm = self.z0_mm + self.vz_mm_s * t_s - 0.5 * self.gravity_mm_s2 * t_s * t_s
        return (x_mm, y_mm, z_mm)


def generate_samples(
    trajectory: KnownTrajectory, times_ms: Sequence[float]
) -> list[Sample]:
    """誤差を含まない厳密なサンプル列を返す。

    `times_ms` の各時刻について `trajectory.position_at_ms` を評価し、
    `Sample(t_ms, x_mm, y_mm, z_mm)` の列として返す。純関数であり、
    同一引数を渡せば何度呼んでも完全に一致する結果を返す（要件 7.1）。
    """
    samples: list[Sample] = []
    for t_ms in times_ms:
        x_mm, y_mm, z_mm = trajectory.position_at_ms(t_ms)
        samples.append(Sample(t_ms=t_ms, x_mm=x_mm, y_mm=y_mm, z_mm=z_mm))
    return samples


def add_noise(
    samples: Sequence[Sample], *, seed: int, stddev_mm: float
) -> list[Sample]:
    """`random.Random(seed)` による決定的なガウスノイズを各軸へ重畳する。

    `t_ms` は変更しない。x_mm / y_mm / z_mm それぞれに独立な
    `rng.gauss(0.0, stddev_mm)` を加える。ノイズはサンプルの先頭から
    順に、各サンプルにつき x, y, z の順で1回ずつ乱数を消費するため、
    同一の `samples` / `seed` / `stddev_mm` を渡す限り結果は完全に
    再現する（要件 7.1）。

    グローバルな `random` モジュールの状態には一切触れない。呼び出しの
    たびにこの関数専用の `random.Random(seed)` インスタンスを新規に
    生成するため、他のコードの乱数消費履歴や呼び出し順序の影響を受けない。
    """
    rng = random.Random(seed)
    noisy: list[Sample] = []
    for sample in samples:
        dx = rng.gauss(0.0, stddev_mm)
        dy = rng.gauss(0.0, stddev_mm)
        dz = rng.gauss(0.0, stddev_mm)
        noisy.append(
            Sample(
                t_ms=sample.t_ms,
                x_mm=sample.x_mm + dx,
                y_mm=sample.y_mm + dy,
                z_mm=sample.z_mm + dz,
            )
        )
    return noisy


@dataclass(frozen=True, slots=True)
class AnalyticFloorImpact:
    """平面 z=0 との解析的な交点（既知軌道から直接算出した厳密解）。

    Attributes:
        hit_x_mm: 落下地点の X 座標（mm）。
        hit_y_mm: 落下地点の Y 座標（mm）。
        hit_time_ms: 落下時刻（ms）。`KnownTrajectory` と同じ絶対時間基準。
    """

    hit_x_mm: float
    hit_y_mm: float
    hit_time_ms: float


def analytic_floor_impact(
    trajectory: KnownTrajectory, after_time_ms: float
) -> AnalyticFloorImpact | None:
    """`z(t)=0` の、`after_time_ms` より真に後にある最も早い解を返す。

    解く式は `0.5*g*t_s^2 - vz*t_s - z0 = 0`（秒単位）。判別式
    `D = vz^2 + 2*g*z0` が負、または実根がすべて `after_time_ms` 以下の
    場合は `None` を返す。

    ここでは design.md が `src/prediction_core/impact.py`（ImpactSolver）
    に要求する桁落ち対策形式の根の公式は使わない。このオラクルは通常の
    値のみを扱うテスト専用ヘルパであり、数値安定性の作り込みは対象外
    （本モジュールの docstring 冒頭を参照）。
    """
    g = trajectory.gravity_mm_s2
    vz = trajectory.vz_mm_s
    z0 = trajectory.z0_mm

    discriminant = vz * vz + 2.0 * g * z0
    if discriminant < 0.0:
        return None

    sqrt_d = discriminant**0.5
    root_a_s = (vz - sqrt_d) / g
    root_b_s = (vz + sqrt_d) / g

    after_time_s = after_time_ms / MS_PER_S
    future_roots_s = [
        root_s for root_s in (root_a_s, root_b_s) if root_s > after_time_s
    ]
    if not future_roots_s:
        return None

    hit_time_s = min(future_roots_s)
    hit_time_ms = hit_time_s * MS_PER_S
    hit_x_mm, hit_y_mm, _hit_z_mm = trajectory.position_at_ms(hit_time_ms)

    return AnalyticFloorImpact(
        hit_x_mm=hit_x_mm,
        hit_y_mm=hit_y_mm,
        hit_time_ms=hit_time_ms,
    )
