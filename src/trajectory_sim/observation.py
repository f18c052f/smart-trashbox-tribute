"""観測モデル（design.md「ObservationModel」/ 要件2.1, 2.2, 2.3, 2.4, 2.5, 2.7）。

真の軌道 `TrueTrajectory` から、検出開始遅れ・標本化周期・軸別ノイズ・
距離依存ノイズ・欠測を模擬した観測サンプル列を生成する。

標本化時刻は `trajectory.t0_ms + 検出開始遅れ + k × 標本化周期`
（`k = 0, 1, ...`）で、真の落下時刻 `impact.time_ms` を超えない範囲に
限る（要件2.1, 2.2）。`trajectory.t0_ms` を起点として用い、`0.0` を
直接の起点とはしない（`sample_throw` が現状常に `t0_ms=0.0` の軌道を
生成するとしても、本関数はその前提に依存しない）。

ノイズは World frame の軸ごとに与え、カメラ姿勢に依存する depth 方向の
モデルは持たない（design.md「ObservationModel」Responsibilities & Constraints）。
距離依存項は「観測原点からの距離の2乗に比例する係数」として与え、実効
標準偏差は `sigma_axis + distance_sigma_rel_per_m2 * (観測原点からの
距離[m])^2` である。距離の単位換算は `trajectory_sim.units.mm_to_m` を
経由し、裸の `/1000.0` は書かない（design.md「ObservationModel」
Implementation Notes）。

乱数の消費順序は、候補となる標本化時刻ごとに「欠測判定 → x ノイズ →
y ノイズ → z ノイズ」で固定する（design.md「ObservationModel」
Invariants）。欠測判定の `rng.random()` は標準偏差の値によらず毎回引く。
一方、各軸のノイズ標準偏差が 0 の場合はその軸の `rng.normalvariate` を
呼ばない（乱数消費を発生させない）。欠測と判定されたサンプルは x/y/z の
乱数も一切消費しない。`rng.gauss` はキャッシュにより呼び出し順に状態が
残るため使わず、`rng.normalvariate` のみを用いる（`sample_throw` と同じ
方針。design.md「Technology Stack」）。

生成物は `prediction_core.Sample` だが、本モジュールは `prediction_core`
を直接 import しない。`trajectory_sim.params.make_sample` を経由して
構築する（design.md「Params」Integration）。
"""

from __future__ import annotations

import math
from random import Random

from trajectory_sim import units
from trajectory_sim.params import ObservationParams, make_sample
from trajectory_sim.physics import ImpactPoint, TrueTrajectory

__all__ = ["observe"]

# 本モジュールは `prediction_core` を一切 import しない（`Sample` の構築は
# `trajectory_sim.params.make_sample` に委ねる。design.md「Params」
# Integration）。戻り値注釈の `Sample` は `from __future__ import
# annotations`（PEP 563）により文字列として保持されるだけで評価されない
# ため、`Sample` という名前を import しなくても定義時エラーにはならない
# （ローカル変数注釈についても同様。局所変数注釈は Python の仕様上、
# 評価も `__annotations__` への格納もされない）。


def _sampling_times(
    trajectory: TrueTrajectory, impact: ImpactPoint, observation: ObservationParams
) -> list[float]:
    """`trajectory.t0_ms + detection_start_delay_ms + k * sample_period_ms` の候補時刻列を返す。

    `impact.time_ms` を超える時刻は含めない（要件2.1, 2.2）。
    """
    times: list[float] = []
    k = 0
    while True:
        t_k = (
            trajectory.t0_ms
            + observation.detection_start_delay_ms
            + k * observation.sample_period_ms
        )
        if t_k > impact.time_ms:
            break
        times.append(t_k)
        k += 1
    return times


def _effective_sigma(
    sigma_axis_mm: float,
    distance_sigma_rel_per_m2: float,
    distance_from_observer_m: float,
) -> float:
    """軸ごとの実効標準偏差を算出する（design.md「ObservationModel」Implementation Notes）。"""
    return sigma_axis_mm + distance_sigma_rel_per_m2 * distance_from_observer_m**2


def observe(
    trajectory: TrueTrajectory,
    impact: ImpactPoint,
    observation: ObservationParams,
    rng: Random,
) -> tuple[Sample, ...]:
    """真の軌道から観測サンプル列を生成する（要件2.1-2.5, 2.7）。

    Preconditions:
        `impact.time_ms > trajectory.t0_ms`（呼び出し側が保証する）。

    Postconditions:
        戻り値の `t_ms` は狭義単調増加。全要素の `t_ms <= impact.time_ms`。
    """
    samples: list[Sample] = []

    for t_k in _sampling_times(trajectory, impact, observation):
        u = rng.random()
        if u < observation.dropout_ratio:
            continue

        true_x, true_y, true_z = trajectory.position_at(t_k)

        distance_from_observer_mm = math.sqrt(
            (true_x - observation.observer_x_mm) ** 2
            + (true_y - observation.observer_y_mm) ** 2
            + (true_z - observation.observer_z_mm) ** 2
        )
        distance_from_observer_m = units.mm_to_m(distance_from_observer_mm)

        sigma_eff_x = _effective_sigma(
            observation.sigma_x_mm,
            observation.distance_sigma_rel_per_m2,
            distance_from_observer_m,
        )
        sigma_eff_y = _effective_sigma(
            observation.sigma_y_mm,
            observation.distance_sigma_rel_per_m2,
            distance_from_observer_m,
        )
        sigma_eff_z = _effective_sigma(
            observation.sigma_z_mm,
            observation.distance_sigma_rel_per_m2,
            distance_from_observer_m,
        )

        noise_x = rng.normalvariate(0.0, sigma_eff_x) if sigma_eff_x > 0.0 else 0.0
        noise_y = rng.normalvariate(0.0, sigma_eff_y) if sigma_eff_y > 0.0 else 0.0
        noise_z = rng.normalvariate(0.0, sigma_eff_z) if sigma_eff_z > 0.0 else 0.0

        samples.append(
            make_sample(
                t_ms=t_k,
                x_mm=true_x + noise_x,
                y_mm=true_y + noise_y,
                z_mm=true_z + noise_z,
            )
        )

    return tuple(samples)
