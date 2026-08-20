"""値オブジェクトと予測結果の直和型（要件 1.1 / 3.3 / 4.3 / 5.3 / 6.6 / 6.7 / 10.4 / 10.6）。

本モジュールは L1 層であり、実行時には標準ライブラリと `prediction_core.units`
以外を import しない（design.md「Dependency Direction」）。

**単位規約**（要件 10.4）: 距離は mm、時刻は ms、速度は mm/s、加速度は mm/s^2 で表し、
これらを表すフィールド名には単位を含める。唯一の例外が `Prediction.residual` であり、
名前に単位を持たないが**値は mm** である。内部計算単位（mm/ms など）との換算は
`prediction_core.units` に集約されており、本モジュールは換算を行わない。
ここに現れる値はすべて外部公開単位である。

**直和型**（要件 6.7）: 予測の結果は `Prediction`（正常）と `InvalidPrediction`（無効）
のいずれかであり、`PredictionOutcome` はその直和である。`InvalidPrediction` は
落下地点・落下時刻・残り時間のフィールドを**持たない**。無効な予測を正常な予測値として
読み出す経路を、型の構造そのもので塞ぐためである。

**検証しない**: 値オブジェクト自体は不変条件を検査しない。設定値の検証は
`PredictionConfig` の構築時に、入力サンプルの有限性検証は `predict()` の境界で行う
（design.md CoreTypes「Implementation Notes / Validation」）。

**`config` フィールドの型注釈**: `PredictionConfig` は L2（`prediction_core.config`）に
属し、本モジュールより上層にある。実行時 import は上向き依存かつ循環になるため、
`from __future__ import annotations` と `TYPE_CHECKING` により型検査時のみ解決する。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - 型検査時のみ解決される（実行時 import を作らない）
    from prediction_core.config import PredictionConfig

__all__ = [
    "InvalidPrediction",
    "InvalidReason",
    "Prediction",
    "PredictionOutcome",
    "Sample",
    "SourceKind",
    "TrajectoryParameters",
]


class SourceKind(StrEnum):
    """観測サンプル列の入力元（要件 9.2）。

    Throw Record の**記録用メタ情報としてのみ**用いる。予測の結果は入力元に
    依存してはならない（要件 1.3）ため、予測経路の引数や値オブジェクトの
    フィールドにこの型が現れることはない（要件 1.4）。
    """

    LIVE = "live"
    RECORDED = "recorded"
    SIMULATED = "simulated"


class InvalidReason(StrEnum):
    """予測が構造的に成立しない理由（要件 6.1-6.6）。

    残差の大きさや収束度に基づく品質判断はここに含めない。採否の判定は
    利用側の責務であり、本パッケージは判断材料としての残差を返すにとどまる
    （要件 6.8）。
    """

    INSUFFICIENT_SAMPLES = "insufficient_samples"
    """有効な観測サンプルが最小サンプル数に満たない（要件 6.1）。"""

    DEGENERATE_TIME = "degenerate_time"
    """観測時刻が縮退しており軌道パラメータを一意に定められない（要件 6.2）。"""

    NO_FUTURE_FLOOR_CROSSING = "no_future_floor_crossing"
    """最新の観測時刻より後に平面 z = 0 との交点が存在しない（要件 6.3）。"""

    NON_FINITE_VALUE = "non_finite_value"
    """入力サンプルまたは算出結果に NaN もしくは Infinity が含まれる（要件 6.4）。"""

    MALFORMED_INPUT = "malformed_input"
    """入力が要件 1 の入力契約を満たさない（要件 6.5）。"""


@dataclass(frozen=True, slots=True)
class Sample:
    """時刻付き3次元位置サンプル（要件 1.1 / 1.2）。

    予測の入力はこの列のみに限定される。デバイス固有の型・カメラパラメータ・
    ファイル入出力は入力契約に含めない（要件 1.4）。座標は World frame へ
    変換済みであることを前提とする（要件 3.1）。

    Attributes:
        t_ms: 観測時刻（ms）。基準は入力側が定め、本パッケージは相対差のみを用いる。
        x_mm: X 座標（mm）。
        y_mm: Y 座標（mm）。
        z_mm: Z 座標（mm）。床面は z = 0 と仮定する。
    """

    t_ms: float
    x_mm: float
    y_mm: float
    z_mm: float


@dataclass(frozen=True, slots=True)
class TrajectoryParameters:
    """放物運動モデルの軌道パラメータ（要件 2.1 / 2.5）。

    x・y を等速、z を重力加速度による等加速度とするモデルのパラメータである。
    位置と速度は `t_ref_ms` を時間原点として表す。

    Attributes:
        t_ref_ms: 位置・速度がこの時刻（ms）を原点とすることを示す基準時刻。
        x0_mm: `t_ref_ms` における X 座標（mm）。
        y0_mm: `t_ref_ms` における Y 座標（mm）。
        z0_mm: `t_ref_ms` における Z 座標（mm）。
        estimated_vx_mm_s: 推定した X 方向速度（mm/s）。
        estimated_vy_mm_s: 推定した Y 方向速度（mm/s）。
        estimated_vz_mm_s: `t_ref_ms` における推定 Z 方向速度（mm/s）。
        gravity_mm_s2: 推定に用いた重力加速度の大きさ（mm/s^2、正値）。
    """

    t_ref_ms: float
    x0_mm: float
    y0_mm: float
    z0_mm: float
    estimated_vx_mm_s: float
    estimated_vy_mm_s: float
    estimated_vz_mm_s: float
    gravity_mm_s2: float


@dataclass(frozen=True, slots=True)
class Prediction:
    """落下地点・落下時刻が算出できた予測結果（要件 3.3）。

    `residual` は本パッケージで唯一、名前に単位を含まないフィールドである
    （要件 10.4 が名指しする例外）。**その値の単位は mm** であり、推定軌道と
    各観測サンプルとの乖離の大きさを表す。定義は `fitting`（TrajectoryFitter）
    を参照すること。利用側が閾値を持つ場合も mm を前提とする。

    Attributes:
        predicted_hit_x_mm: 落下地点の X 座標（mm）。
        predicted_hit_y_mm: 落下地点の Y 座標（mm）。
        predicted_hit_time_ms: 落下時刻（ms）。入力サンプルと同一の時間基準で表す（要件 3.4）。
        remaining_time_ms: `predicted_hit_time_ms` から `based_on_time_ms` を引いた
            残り時間（ms、要件 3.5）。
        estimated_vx_mm_s: 推定した X 方向速度（mm/s）。
        estimated_vy_mm_s: 推定した Y 方向速度（mm/s）。
        estimated_vz_mm_s: 推定した Z 方向速度（mm/s）。
        residual: 推定軌道と観測サンプルの乖離（**mm**、要件 2.4 / 6.8）。
        trajectory: この予測の基になった軌道パラメータ。
        sample_count: 何サンプル目の観測に基づく予測かを示す件数（要件 4.3）。
        based_on_time_ms: 使用サンプルのうち最新の観測時刻（ms、要件 5.3）。
        elapsed_ms: この予測に要した処理時間（ms、要件 8.1）。
            計測が無効な場合は None（要件 8.3）。
        config: この予測に使用したパラメータ（要件 10.6）。
    """

    predicted_hit_x_mm: float
    predicted_hit_y_mm: float
    predicted_hit_time_ms: float
    remaining_time_ms: float
    estimated_vx_mm_s: float
    estimated_vy_mm_s: float
    estimated_vz_mm_s: float
    residual: float
    trajectory: TrajectoryParameters
    sample_count: int
    based_on_time_ms: float
    elapsed_ms: float | None
    config: PredictionConfig


@dataclass(frozen=True, slots=True)
class InvalidPrediction:
    """予測が構造的に成立しなかったことを表す結果（要件 6.6 / 6.7）。

    落下地点（`predicted_hit_x_mm` / `predicted_hit_y_mm`）・落下時刻
    （`predicted_hit_time_ms`）・残り時間（`remaining_time_ms`）のフィールドを
    **意図的に持たない**。無効な予測から落下地点を読み出す経路が存在しないことで、
    誤った目標座標が駆動系へ流れる事態を型の段階で防ぐ（要件 6.7）。

    無効は例外ではなく値として返される。例外は「呼び出し方の誤り」に限り、
    `errors` モジュールが定義する（design.md「Error Handling / Error Strategy」）。

    Attributes:
        reason: どの条件で無効と判定されたか（要件 6.6）。
        detail: 判定の根拠を人が読める形で補足する文字列。分岐条件には用いない。
        sample_count: 判定時点で対象となった観測サンプル件数。
        based_on_time_ms: 対象サンプルのうち最新の観測時刻（ms）。
            サンプルが 1 件も無い場合は None。
        elapsed_ms: この判定に要した処理時間（ms、要件 8.1）。
            計測が無効な場合は None（要件 8.3）。
        config: この判定に使用したパラメータ（要件 10.6）。
    """

    reason: InvalidReason
    detail: str
    sample_count: int
    based_on_time_ms: float | None
    elapsed_ms: float | None
    config: PredictionConfig


PredictionOutcome = Prediction | InvalidPrediction
"""1 回の予測要求に対する結果の直和型（要件 6.7）。

利用側は `isinstance(outcome, Prediction)` により分岐する。この 2 つ以外の
状態は存在せず、無効は例外ではなく本型の一方の枝として返る。
"""
