"""移動体の運動性能モデル: 到達可否の閉形式判定
（design.md「DrivetrainModel」/ 要件 5.2, 6.2）。

タスク 2.2 の範囲は以下の3関数のみである:

- `max_distance_from_rest`: 静止状態から一定時間で bang-bang 加速により
  到達できる最大距離（通過方針の到達可能距離）
- `min_time_to_stop_at`: 静止状態から加速し、目標点でちょうど速度 0 に
  なるよう減速するために必要な最短所要時間（停止方針の所要時間）
- `is_reachable`: 上記2関数を用いて、与えられた持ち時間で必要移動量に
  到達できるかを方針ごとに判定する

`TargetUpdate` / `MotionState` / `simulate`（design.md「DrivetrainModel」
Service Interface の残り）は、実運動を固定刻みの数値積分で解く後続タスク
（2.3）の範囲であり、本タスクでは定義しない。

並進のみの質点として1次元（持ち時間・距離という2軸）の閉形式のみを扱う。
x/y ベクトルや回転・逆運動学は持たない（design.md「DrivetrainModel」
Responsibilities & Constraints: 「並進のみの質点」「等方」）。

**機体性能値を自前で持たない。** 最高速度・加速度上限・減速度上限は
すべて引数で受け取る `DrivetrainParams` から取得し、本モジュールが
独自の性能定数を持つことはない（design.md「DrivetrainModel」
Responsibilities & Constraints）。

内部計算は `trajectory_sim.units` を介して mm/ms・mm/ms^2 に換算した
値のみで行う。裸の `1000` や `1e6` はここに書かない（要件2.6, 7.7）。

`hold_time_ms` / `distance_mm` は、パッケージの外部公開単位でもある
ミリ秒・ミリメートルのままで受け取り、換算を必要としない。

`DrivetrainParams` はタスク 1.4 の構築時検証（`__post_init__` の
`_require_positive_finite`）により、`max_speed_mm_s` /
`max_accel_mm_s2` / `max_decel_mm_s2` が常に正の有限値であることが
既に保証されている。本モジュールはその前提を信頼し、
`trajectory_sim.errors` による再検証は行わない
（design.md「DrivetrainModel」は本モジュールに検証責務を割り当てて
いない。`physics.py` と同じ方針）。
"""

from __future__ import annotations

import math

from trajectory_sim import units
from trajectory_sim.params import CatchPolicy, DrivetrainParams

__all__ = ["max_distance_from_rest", "min_time_to_stop_at", "is_reachable"]


def max_distance_from_rest(hold_time_ms: float, params: DrivetrainParams) -> float:
    """静止状態から `hold_time_ms` の間に bang-bang 加速で到達できる最大距離を返す。

    通過方針（`CatchPolicy.PASS_THROUGH`）の到達可能距離
    （design.md「DrivetrainModel」Implementation Notes / 要件6.2）。

    加速度 `a_max` で加速し続け、最高速度 `v_max` に達したのちはそれ以上
    加速しない（`v_max` で飽和する）bang-bang プロファイルを、
    `hold_time_ms` 以内に最高速度へ到達するかどうかで場合分けする:

    - 三角形プロファイル（`hold_time_ms <= t_sat`、`t_sat = v_max / a_max`
      は最高速度へ到達するまでの時間）: `distance = 0.5 * a_max *
      hold_time_ms ** 2`
    - 台形プロファイル（`hold_time_ms > t_sat`）: 最高速度到達までの距離
      `0.5 * v_max * t_sat` に、残り時間を最高速度で走行した距離
      `v_max * (hold_time_ms - t_sat)` を加えたもの。整理すると
      `distance = v_max * (hold_time_ms - 0.5 * t_sat)`

    両プロファイルは `hold_time_ms == t_sat` において
    `0.5 * v_max * t_sat` に一致し、連続である。

    Preconditions:
        `hold_time_ms >= 0`。
    """
    v_max = units.mm_per_s_to_mm_per_ms(params.max_speed_mm_s)
    a_max = units.mm_per_s2_to_mm_per_ms2(params.max_accel_mm_s2)
    t_sat = v_max / a_max

    if hold_time_ms <= t_sat:
        return 0.5 * a_max * hold_time_ms**2
    return v_max * (hold_time_ms - 0.5 * t_sat)


def min_time_to_stop_at(distance_mm: float, params: DrivetrainParams) -> float:
    """静止状態から `distance_mm` の距離でちょうど速度 0 に到達する最短所要時間を返す。

    停止方針（`CatchPolicy.STOP_AND_WAIT`）の所要時間
    （design.md「DrivetrainModel」Implementation Notes / 要件5.2）。

    加速度 `a_max` で加速し、減速度 `a_decel` で減速して目標点でちょうど
    速度 0 になる bang-bang プロファイルを想定する。飽和（最高速度
    `v_max` への到達）が起きるかどうかで場合分けする。

    まず、最高速度への飽和が起きない場合のピーク速度を
    `distance_mm = v_peak**2 / (2*a_max) + v_peak**2 / (2*a_decel)` を
    `v_peak` について解いて求める:

        v_peak_unsat = sqrt(2 * distance_mm * a_max * a_decel / (a_max + a_decel))

    - 三角形プロファイル（`v_peak_unsat <= v_max`）: 加速フェーズ時間
      `v_peak_unsat / a_max` と減速フェーズ時間 `v_peak_unsat / a_decel`
      の和
    - 台形プロファイル（`v_peak_unsat > v_max`）: 最高速度への加速フェーズ
      （時間 `t1 = v_max / a_max`、距離 `d1 = 0.5 * v_max * t1`）、
      最高速度からの減速フェーズ（時間 `t2 = v_max / a_decel`、距離
      `d2 = 0.5 * v_max * t2`）、その間を最高速度で走行する巡航フェーズ
      （距離 `d_cruise = distance_mm - d1 - d2`、時間
      `d_cruise / v_max`）の合計

    `v_peak_unsat > v_max` はこの分岐が選ばれる条件そのものであり、これは
    「加減速だけで `distance_mm` に到達しようとすると `v_max` を超える
    速度が必要になる」ことを意味する。言い換えると、`v_max` までの
    加減速だけで到達できる距離 `d1 + d2` は `distance_mm`以下でなければ
    ならない（そうでなければ飽和せずに到達でき、`v_peak_unsat <= v_max`
    の分岐と矛盾する）。したがって `d_cruise = distance_mm - d1 - d2` は
    この分岐では実数演算上必ず 0 以上になる。ただし `v_peak_unsat` と
    `d1 + d2` は独立した式で計算されるため、境界付近では IEEE 754
    浮動小数点演算の丸め誤差により `d_cruise` が極小の負値になり得る。
    これを `max(0.0, ...)` でクランプして吸収する。

    Preconditions:
        `distance_mm >= 0`。
    """
    v_max = units.mm_per_s_to_mm_per_ms(params.max_speed_mm_s)
    a_max = units.mm_per_s2_to_mm_per_ms2(params.max_accel_mm_s2)
    a_decel = units.mm_per_s2_to_mm_per_ms2(params.max_decel_mm_s2)

    v_peak_unsat = math.sqrt(2.0 * distance_mm * a_max * a_decel / (a_max + a_decel))

    if v_peak_unsat <= v_max:
        return v_peak_unsat * (1.0 / a_max + 1.0 / a_decel)

    t1 = v_max / a_max
    d1 = 0.5 * v_max * t1
    t2 = v_max / a_decel
    d2 = 0.5 * v_max * t2
    # d_cruise >= 0 はこの分岐 (v_peak_unsat > v_max) では実数演算上
    # 常に成り立つ不変条件である（上記の通り、そうでなければ
    # v_peak_unsat <= v_max の分岐と矛盾する）。しかし v_peak_unsat と
    # d1 + d2 は独立した式で計算されるため、境界付近
    # （v_peak_unsat が v_max にごくわずかに近い場合）では IEEE 754
    # 浮動小数点演算の丸め誤差により d1 + d2 が distance_mm をわずかに
    # （最終ビット単位で）上回ることがあり、d_cruise が極小の負値
    # （例: -1.1e-13）になり得る。これは不変条件そのものの破れではなく
    # 浮動小数点の丸めによる桁落ちであるため、`max(0.0, ...)` で
    # クランプして吸収する（標準的な浮動小数点対策であり、
    # ドメイン上の不変条件を無視しているわけではない）。
    d_cruise = max(0.0, distance_mm - d1 - d2)
    t_cruise = d_cruise / v_max
    return t1 + t_cruise + t2


def is_reachable(
    hold_time_ms: float,
    distance_mm: float,
    params: DrivetrainParams,
    policy: CatchPolicy,
) -> bool:
    """`hold_time_ms` の持ち時間で `distance_mm` の移動量に到達できるかを判定する。

    （design.md「DrivetrainModel」Service Interface / 要件5.2, 6.2）

    - `CatchPolicy.PASS_THROUGH`（通過方針）: 目標点で停止する必要はなく、
      持ち時間内に到達できればよい。`max_distance_from_rest(hold_time_ms,
      params) >= distance_mm` で判定する
    - `CatchPolicy.STOP_AND_WAIT`（停止方針）: 目標点で速度が 0 になる
      減速まで含めて評価する（要件5.2）。
      `min_time_to_stop_at(distance_mm, params) <= hold_time_ms` で判定する

    `distance_mm == 0` は両方針で自明に到達可能である
    （`max_distance_from_rest` は `hold_time_ms >= 0` に対し常に
    `>= 0` を返し、`min_time_to_stop_at(0, params)` は `0.0` を返す
    ため、`hold_time_ms >= 0` の前提のもとでは常に真になる）。

    Preconditions:
        `hold_time_ms >= 0`、`distance_mm >= 0`。
    """
    if policy is CatchPolicy.PASS_THROUGH:
        return max_distance_from_rest(hold_time_ms, params) >= distance_mm
    return min_time_to_stop_at(distance_mm, params) <= hold_time_ms
