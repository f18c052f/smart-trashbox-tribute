"""単位換算の単一定義点（要件 2.6 / 7.7）。

外部公開単位（距離 mm・時刻 ms・速度 mm/s・加速度 mm/s^2）と内部計算単位
（mm/ms・mm/ms^2）の相互換算、および角度（度 ⇄ ラジアン）の相互換算を
ここに集約する。

換算係数はこのモジュールにのみ存在する。他モジュールは `1000` や `1e6`、
`math.pi / 180` といった係数を直接書かず、本モジュールの関数を経由する
こと（design.md「Units」/ 要件 2.6, 7.7）。この方針は `BoundaryCheck`
コンポーネントが静的に検査する。

換算はすべて純関数であり、値の妥当性検証は行わない。非有限値（`inf` /
`nan`）もそのまま透過させる。値域の検証は `params` モジュールの境界で
行う（design.md「Units」Invariants）。
"""

from __future__ import annotations

import math

MS_PER_S: float = 1000.0
"""1 秒あたりのミリ秒数。本パッケージで唯一の時間単位換算係数。"""


def mm_per_s_to_mm_per_ms(value: float) -> float:
    """外部公開単位の速度 mm/s を内部計算単位の mm/ms へ換算する。

    Args:
        value: 外部から指定される速度（mm/s）。

    Returns:
        内部計算で用いる速度（mm/ms）。
    """
    return value / MS_PER_S


def mm_per_ms_to_mm_per_s(value: float) -> float:
    """内部計算単位の速度 mm/ms を外部公開単位の mm/s へ換算する。

    Args:
        value: 内部計算で用いる速度（mm/ms）。

    Returns:
        外部公開フィールド（例: `speed_mm_s`）で用いる速度（mm/s）。
    """
    return value * MS_PER_S


def mm_per_s2_to_mm_per_ms2(value: float) -> float:
    """外部公開単位の加速度 mm/s^2 を内部計算単位の mm/ms^2 へ換算する。

    Args:
        value: 外部から指定される加速度（mm/s^2）。重力加速度・
            加速度上限など。

    Returns:
        内部計算で用いる加速度（mm/ms^2）。
    """
    return value / MS_PER_S**2


def mm_per_ms2_to_mm_per_s2(value: float) -> float:
    """内部計算単位の加速度 mm/ms^2 を外部公開単位の mm/s^2 へ換算する。

    Args:
        value: 内部計算で用いる加速度（mm/ms^2）。

    Returns:
        外部公開フィールドで用いる加速度（mm/s^2）。
    """
    return value * MS_PER_S**2


def deg_to_rad(value: float) -> float:
    """角度の度をラジアンへ換算する。

    Args:
        value: 度単位の角度。

    Returns:
        ラジアン単位の角度。
    """
    return math.radians(value)


def rad_to_deg(value: float) -> float:
    """角度のラジアンを度へ換算する。

    Args:
        value: ラジアン単位の角度。

    Returns:
        度単位の角度。
    """
    return math.degrees(value)
