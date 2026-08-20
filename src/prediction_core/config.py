"""挙動を左右する数値を導出根拠つきで公開する設定オブジェクト（要件8.3・10.1・10.2・10.3・10.5）。

本モジュールは L2 層であり、実行時に `prediction_core.units` と
`prediction_core.errors` のみを import する（design.md「Dependency Direction」）。
`prediction_core.types` の実行時 import は現時点で不要なため行っていないが、
design.md の依存表は `config` が `types` を import してよい方向を許可している
（禁止は逆方向。`types.py` は `PredictionConfig` を `TYPE_CHECKING` 下でのみ
参照しており、`types` が `config` を実行時 import することはない）。

**根拠のない固定値を埋め込まない**（要件10.5）: 重力加速度・最小サンプル数・
時刻縮退の相対閾値はすべてここでパラメータ化され、既定値には導出根拠を併記する。
不正な値は構築時（`__post_init__`）に `PredictionConfigError` を送出して拒否する
（Fail Fast）。予測の無効判定（`InvalidPrediction`）とは異なるカテゴリであり、
設定不正は例外として扱う（`errors.py` 参照）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from prediction_core.errors import PredictionConfigError
from prediction_core.units import mm_per_s2_to_mm_per_ms2

__all__ = ["PredictionConfig"]


@dataclass(frozen=True, slots=True)
class PredictionConfig:
    """予測の挙動を左右するパラメータ（要件10.1 / 10.6）。

    不変（`frozen=True`）かつ値等価であり、予測結果（`Prediction` /
    `InvalidPrediction`）にそのまま同梱して使用パラメータを追跡できる
    （要件10.6）。

    Attributes:
        gravity_mm_s2: 重力加速度の大きさ（mm/s^2）。既定値 9806.65 は標準重力
            加速度 9.80665 m/s^2 を mm/s^2 に換算した値である。定数として
            運動モデルに埋め込まず、ここでパラメータ化する（要件10.1）。
        min_samples: 予測に必要な最小観測サンプル数。既定値 3 の根拠は、
            放物運動モデルの各軸の未知数が2個（g は既知）であることによる。
            n = 2 では厳密解となり残差の自由度 `3(n-2)` が 0 になるため、
            残差を信頼度として使える最小値である n = 3 を既定とする
            （要件10.2、A-1）。M1 の実測結果に応じて見直す前提であり、
            3 未満は構築時に拒否する（要件10.3）。
        measure_elapsed: 予測1回あたりの処理時間計測を行うかどうか。既定値
            True。構造化ロギング基盤の完成を待たずに計測できる一方、
            実行時に無効化できる必要がある（要件8.3、`tech.md` 開発標準5）。
        time_degeneracy_rel_tol: 観測時刻の縮退判定に用いる相対閾値。既定値
            `sqrt(float64 machine epsilon)` ≈ 1.4901161193847656e-08。
            縮退判定は正規方程式の行列式に対する相対比較であり、
            正規方程式は条件数を2乗するため、machine epsilon の平方根を
            相対閾値の尺度に用いるのが慣行である。
    """

    gravity_mm_s2: float = 9806.65
    min_samples: int = 3
    measure_elapsed: bool = True
    time_degeneracy_rel_tol: float = 1.4901161193847656e-08

    def __post_init__(self) -> None:
        """全不変条件を検証し、違反時は違反フィールド名と値を添えて拒否する。"""
        if self.min_samples < 3:
            raise PredictionConfigError(
                f"min_samples={self.min_samples!r} は 3 未満である。"
                "放物運動モデルの各軸は未知数2個であり、n=2 では残差自由度が"
                "0 になり信頼度として使えないため、最小サンプル数は 3 以上"
                "でなければならない（要件10.3）。"
            )

        if not (math.isfinite(self.gravity_mm_s2) and self.gravity_mm_s2 > 0):
            raise PredictionConfigError(
                f"gravity_mm_s2={self.gravity_mm_s2!r} は正の有限値でなければ"
                "ならない。0 以下または非有限では、平面 z=0 との「落下側」の"
                "交点が定義できない。"
            )

        if not (
            math.isfinite(self.time_degeneracy_rel_tol)
            and 0.0 < self.time_degeneracy_rel_tol < 1.0
        ):
            raise PredictionConfigError(
                f"time_degeneracy_rel_tol={self.time_degeneracy_rel_tol!r} は "
                "0 と 1 の間の有限値でなければならない。"
            )

    @property
    def gravity_mm_ms2(self) -> float:
        """内部計算用の重力加速度（mm/ms^2）。

        `prediction_core.units.mm_per_s2_to_mm_per_ms2` を経由して換算する。
        `1e6` 相当の換算係数を本モジュールに直書きしないのは、係数の定義箇所を
        `units.py` の一箇所に保つため（要件10.5）。
        """
        return mm_per_s2_to_mm_per_ms2(self.gravity_mm_s2)
