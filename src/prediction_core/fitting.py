"""閉形式最小二乗による軌道推定と残差算出（要件 2.1-2.5 / 6.2 / 10.2）。

TrajectoryFitter コンポーネント。L3 層であり、実行時には `prediction_core.units` /
`prediction_core.types` / `prediction_core.config` と標準ライブラリのみを import する
（design.md「Dependency Direction」）。

**アルゴリズム概要**（design.md「#### TrajectoryFitter」）:

- 重力加速度 `g` が既知であることを利用し、`z' = z + 1/2 * g * t~^2` と置くことで
  x・y・z の3軸すべてを「2パラメータの単回帰」に還元する（要件 2.1 / 2.2）。
- 時刻は `t~ = t - t_ref`（`t_ref = min(t_ms)`）へ原点シフトしてから累積する。
  絶対時刻のまま `t^2` を扱うと桁落ちするため（要件 2.1）。
- 累積はサンプル列に与えられた昇順の順序でそのまま行う。呼び出し側
  （将来の Predictor）が `t_ms` 昇順で整列済みであることを保証しており、
  本関数はここで再ソートしない。演算順序を固定することが決定性の根拠になる。
- 縮退判定は `Δ = n*Σt~^2 - (Σt~)^2` を `config.time_degeneracy_rel_tol` による
  **相対閾値**と比較して行う（要件 6.2）。厳密な `Δ == 0` 比較は使わない。
- 残差は「3軸合計の残差二乗和を残差自由度 `3*(n-2)` で割った平方根」として
  算出する（要件 2.4 / 10.2）。単位は mm。
- 推定速度は内部単位（mm/ms）で計算し、`prediction_core.units.mm_per_ms_to_mm_per_s`
  で mm/s に換算してから `TrajectoryParameters` へ格納する（要件 2.5）。

副作用を持たない純関数であり、例外は送出しない。縮退時は
`InvalidReason.DEGENERATE_TIME` を値として返す。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from prediction_core.config import PredictionConfig
from prediction_core.types import InvalidReason, Sample, TrajectoryParameters
from prediction_core.units import mm_per_ms_to_mm_per_s

__all__ = ["FitResult", "fit_trajectory"]


@dataclass(frozen=True, slots=True)
class FitResult:
    """軌道推定に成功した場合の結果。

    Attributes:
        trajectory: 推定された軌道パラメータ（速度は mm/s 単位）。
        residual: 推定軌道と観測サンプルの乖離（mm）。
    """

    trajectory: TrajectoryParameters
    residual: float  # 単位 mm


def fit_trajectory(
    samples: Sequence[Sample],
    config: PredictionConfig,
) -> FitResult | InvalidReason:
    """整列済みサンプル列から軌道パラメータと残差を閉形式で推定する。

    Preconditions（呼び出し側が保証する。ここでは検証しない）:
        - `samples` は `t_ms` 昇順に整列済みであり、全要素の全フィールドが有限である。
        - `len(samples) >= config.min_samples`（`config.min_samples >= 3` は
          `PredictionConfig` 自身の構築時検証が保証する）。

    Returns:
        成功時は `FitResult`。観測時刻が縮退している場合は
        `InvalidReason.DEGENERATE_TIME`（例外は送出しない）。
    """
    n = len(samples)

    # 時刻を最小値で原点シフトしてから、サンプル列に与えられた昇順の
    # 固定順序で累積する（絶対時刻のまま t^2 を扱う桁落ちを避け、
    # 演算順序を決定的にするため）。
    t_ref_ms = min(sample.t_ms for sample in samples)
    t_tilde = [sample.t_ms - t_ref_ms for sample in samples]

    st = 0.0
    stt = 0.0
    for t in t_tilde:
        st += t
        stt += t * t

    delta = n * stt - st * st

    # 縮退判定は相対閾値で行う。Δ == 0 の厳密比較は使わない（要件 6.2）。
    if delta <= config.time_degeneracy_rel_tol * n * stt:
        return InvalidReason.DEGENERATE_TIME

    g_tilde = config.gravity_mm_ms2  # 内部単位（mm/ms^2）。units 経由で既に換算済み。

    # z を z' = z + 1/2*g_tilde*t~^2 と線形化し、x・y・z' の3軸を
    # 同じ形の単回帰として解く（要件 2.1 / 2.2）。
    sx = sy = sz = 0.0
    stx = sty = stz = 0.0
    for sample, t in zip(samples, t_tilde):
        z_prime = sample.z_mm + 0.5 * g_tilde * t * t

        sx += sample.x_mm
        stx += t * sample.x_mm

        sy += sample.y_mm
        sty += t * sample.y_mm

        sz += z_prime
        stz += t * z_prime

    vx = (n * stx - st * sx) / delta
    x0 = (sx - vx * st) / n

    vy = (n * sty - st * sy) / delta
    y0 = (sy - vy * st) / n

    vz = (n * stz - st * sz) / delta
    z0 = (sz - vz * st) / n

    # 残差は線形化前の元の物理モデル（z' ではなく z）に対して算出する
    # （design.md「Implementation Notes」。1/2*g_tilde*t~^2 の項は相殺され
    # 数学的には同値だが、物理的な解釈に合わせてこの形で計算する）。
    sse = 0.0
    for sample, t in zip(samples, t_tilde):
        x_pred = x0 + vx * t
        y_pred = y0 + vy * t
        z_pred = z0 + vz * t - 0.5 * g_tilde * t * t

        dx = sample.x_mm - x_pred
        dy = sample.y_mm - y_pred
        dz = sample.z_mm - z_pred
        sse += dx * dx + dy * dy + dz * dz

    residual = math.sqrt(sse / (3.0 * (n - 2)))

    trajectory = TrajectoryParameters(
        t_ref_ms=t_ref_ms,
        x0_mm=x0,
        y0_mm=y0,
        z0_mm=z0,
        estimated_vx_mm_s=mm_per_ms_to_mm_per_s(vx),
        estimated_vy_mm_s=mm_per_ms_to_mm_per_s(vy),
        estimated_vz_mm_s=mm_per_ms_to_mm_per_s(vz),
        gravity_mm_s2=config.gravity_mm_s2,
    )

    return FitResult(trajectory=trajectory, residual=residual)
