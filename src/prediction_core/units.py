"""単位換算の単一定義点（要件 1.2 / 2.5 / 10.4）。

外部契約の単位は **距離 mm・時刻 ms・速度 mm/s・加速度 mm/s^2** である
（`docs/requirements.md` 6.1）。一方、内部計算は入力サンプルと同じ
**mm と ms** に統一するため、内部の速度は mm/ms、内部の重力加速度は
mm/ms^2 になる。この差を埋める換算をここに集約する。

換算係数はこのモジュールにのみ存在する。他モジュールは `1000` や `1e6`
といった係数を直接書かず、本モジュールの関数を経由すること（要件 10.5）。
"""

from __future__ import annotations

MS_PER_S: float = 1000.0
"""1 秒あたりのミリ秒数。本パッケージで唯一の時間単位換算係数。"""


def mm_per_ms_to_mm_per_s(v_mm_ms: float) -> float:
    """内部単位の速度 mm/ms を外部公開単位の mm/s へ換算する。

    Args:
        v_mm_ms: 内部計算で用いる速度（mm/ms）。

    Returns:
        外部公開フィールド（`estimated_v*_mm_s`）で用いる速度（mm/s）。
    """
    return v_mm_ms * MS_PER_S


def mm_per_s2_to_mm_per_ms2(a_mm_s2: float) -> float:
    """外部指定の加速度 mm/s^2 を内部単位の mm/ms^2 へ換算する。

    Args:
        a_mm_s2: 外部から指定される加速度（mm/s^2）。重力加速度など。

    Returns:
        内部計算で用いる加速度（mm/ms^2）。
    """
    return a_mm_s2 / MS_PER_S**2
