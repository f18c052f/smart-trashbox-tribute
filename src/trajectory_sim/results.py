"""シナリオ結果・格子点結果・掃引結果の型、およびモデル除外要因の一覧
（design.md「Results」/ 要件 1.5, 2.8, 4.6, 5.7, 6.5, 6.7, 9.7）。

投擲物理・観測・移動体・キャッチ判定の各段について「初期モデルに含め
なかった要因」を `MODEL_EXCLUSIONS` として保持する。これは本 Spec が
OQ-33 を決着させた内容そのものであり、内容の増減は Revalidation Trigger
に該当する（design.md「Results」Implementation Notes Risks）。

評価対象外（`NotEvaluatedReason`）は不成立とは異なる状態として区別する
（要件 6.7）。「成否が未定なら理由が必ず埋まる」という不変条件
（`catchable is None` と `not_evaluated_reason is None` が同時に成立
しない）を `ScenarioOutcome.__post_init__` で検査する（design.md
「Results」Implementation Notes Validation）。`CellResult` も同じ理由
（中途半端な結果を作らない）から、`status` と `not_evaluated_reason` の
対応関係を同様に検査する。

直列化はここに持たせない（構造と表現を分ける。design.md「Results」
Responsibilities & Constraints）。JSON への変換は `ResultSerializer`
（タスク4.1）の責務である。

`SweepResult.spec` の型 `SweepSpec` は上位層 `trajectory_sim.sweep`
（タスク3.2、design.md「Dependency Direction」で本モジュールより上層）
に属するため、実行時 import を作らない。`from __future__ import
annotations` によりすべての注釈を遅延文字列化した上で、`TYPE_CHECKING`
配下でのみ型検査用に import する（`src/prediction_core/types.py` が
`PredictionConfig` に対して用いているのと同じパターン）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING

from prediction_core import ThrowRecord

from trajectory_sim.errors import ParameterError
from trajectory_sim.params import ScenarioParams

if TYPE_CHECKING:  # pragma: no cover - 型検査時のみ解決される（実行時 import を作らない）
    from trajectory_sim.sweep import SweepSpec

__all__ = [
    "NotEvaluatedReason",
    "CellStatus",
    "MODEL_EXCLUSIONS",
    "ScenarioOutcome",
    "CellResult",
    "SweepResult",
]


class NotEvaluatedReason(StrEnum):
    """評価対象外となった理由（要件 1.6, 6.7）。

    不成立（`CellStatus.NOT_CATCHABLE`）とは区別される状態である。
    """

    NO_FLOOR_CROSSING = "no_floor_crossing"
    """真の軌道と床面 z = 0 との未来側の交点が存在しない（要件1.6）。"""

    NO_SAMPLES = "no_samples"
    """観測サンプルが1件も得られなかった（欠測等により全脱落）。"""

    NO_VALID_PREDICTION = "no_valid_prediction"
    """`prediction-core` による有効な予測が1件も得られなかった。"""


class CellStatus(StrEnum):
    """格子点の状態（要件6.5, 6.7）。

    `NOT_EVALUATED` は `NOT_CATCHABLE`（不成立）と混同してはならない
    （要件6.7）。
    """

    CATCHABLE = "catchable"
    NOT_CATCHABLE = "not_catchable"
    NOT_EVALUATED = "not_evaluated"


MODEL_EXCLUSIONS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "throw_physics": ("air_drag", "spin", "bounce"),
        "observation": (
            "sensor_distortion",
            "field_of_view",
            "occlusion",
            "timestamp_jitter",
        ),
        "drivetrain": (
            "wheel_slip",
            "direction_dependent_performance",
            "inverse_kinematics",
            "speed_control_dynamics",
        ),
        "catch": ("bounce_out",),
    }
)
"""初期モデルに含めなかった要因の一覧（要件1.5, 2.8, 4.6, 9.7 / OQ-33 の
決着内容そのもの）。

- ``throw_physics``: 投擲物理モデル（要件1.5）。
- ``observation``: 観測モデル（要件2.8）。
- ``drivetrain``: 移動体の運動モデル（要件4.6）。
- ``catch``: キャッチ判定（要件5.7: キャッチ後の跳ね返りの有無を評価に
  含めない）。

この4段すべてを常に持つ（要件9.7: 出力に一覧を含めるための唯一の
情報源）。項目の増減は OQ-33 の決着内容を変更することを意味し、
Revalidation Trigger に該当する（design.md「Results」Implementation
Notes Risks）。
"""


@dataclass(frozen=True, slots=True)
class ScenarioOutcome:
    """1回のシナリオ試行の評価結果（要件1.6, 5.3, 5.5, 6.5, 6.7）。

    Attributes:
        catchable: キャッチ成立の可否。評価対象外の場合は `None`。
        not_evaluated_reason: 評価対象外となった理由。評価済みの場合は
            `None`。
        true_impact_x_mm, true_impact_y_mm: 真の落下地点（mm）。
        true_impact_time_ms: 真の落下時刻（ms）。
        required_distance_mm: 真の落下地点と移動体位置の水平距離（mm、
            要件5.3）。
        hold_time_ms: 真の落下時刻までの持ち時間（ms）。
        first_command_time_ms: 初回予測に基づく指令が発生した時刻（ms）。
        position_error_mm: キャッチ判定時点の水平位置誤差（mm）。
        residual_speed_mm_s: キャッチ判定時点の残留速度（mm/s、要件5.3）。
        prediction_error_mm: 予測落下地点と真の落下地点の誤差（mm）。
        sample_count: 生成された観測サンプル数。
        valid_prediction_count: `prediction-core` が有効と判定した予測数。
        record: 代表シナリオとして記録する場合の Throw Record。記録しない
            場合は `None`（要件7.4）。

    評価対象外の場合、`true_impact_*` 以降の数値フィールドは意味を
    持たないため `None` としてよい（呼び出し側の責務。本クラスは個々の
    数値フィールド間の対応関係までは検証しない）。
    """

    catchable: bool | None
    not_evaluated_reason: NotEvaluatedReason | None
    true_impact_x_mm: float | None
    true_impact_y_mm: float | None
    true_impact_time_ms: float | None
    required_distance_mm: float | None
    hold_time_ms: float | None
    first_command_time_ms: float | None
    position_error_mm: float | None
    residual_speed_mm_s: float | None
    prediction_error_mm: float | None
    sample_count: int
    valid_prediction_count: int
    record: ThrowRecord | None

    def __post_init__(self) -> None:
        """`catchable` と `not_evaluated_reason` が一方のみ指定されていることを検証する。

        「成否が未定なら理由が必ず埋まる」（task 1.6）を、「理由が
        埋まっているなら成否は未定である」まで含めて双方向に検査する。
        片方だけ埋まった中途半端な結果（design.md「Results」
        Implementation Notes Validation）と、両方埋まった矛盾する結果の
        双方を `ParameterError` で拒否する。
        """
        catchable_is_none = self.catchable is None
        reason_is_none = self.not_evaluated_reason is None
        if catchable_is_none == reason_is_none:
            raise ParameterError(
                "catchable と not_evaluated_reason はどちらか一方のみが "
                "指定されていなければならない（成否が未定なら理由が必ず "
                f"埋まる）: catchable={self.catchable!r}, "
                f"not_evaluated_reason={self.not_evaluated_reason!r}"
            )


@dataclass(frozen=True, slots=True)
class CellResult:
    """1格子点の掃引結果（要件6.4, 6.5, 6.7）。

    Attributes:
        axis_values: 掃引の各軸に対応する、この格子点での値の並び。
        status: この格子点の状態（成立 / 不成立 / 評価対象外）。
        trials: この格子点で行われた試行回数。
        evaluated_trials: そのうち評価対象になった試行回数（評価対象外を
            除く）。
        success_count: そのうち成立と判定された試行回数。
        success_ratio: `success_count / evaluated_trials`。
            `evaluated_trials == 0` の場合は `None`（`SweepEngine`、
            タスク3.2の責務）。
        metrics: 評価対象の試行についての指標値の平均（位置誤差・
            持ち時間・必要移動量・予測誤差・残留速度など）。評価対象外の
            試行は含めない（design.md「Results」Implementation Notes）。
        not_evaluated_reason: `status` が `NOT_EVALUATED` の場合の理由。
            それ以外の場合は `None`。
        representative: この格子点の代表試行の詳細結果。
    """

    axis_values: tuple[float | str, ...]
    status: CellStatus
    trials: int
    evaluated_trials: int
    success_count: int
    success_ratio: float | None
    metrics: Mapping[str, float]
    not_evaluated_reason: NotEvaluatedReason | None
    representative: ScenarioOutcome | None

    def __post_init__(self) -> None:
        """`status` と `not_evaluated_reason` の対応関係を検証する。

        `status is CellStatus.NOT_EVALUATED` であることと
        `not_evaluated_reason is not None` であることは同値でなければ
        ならない。`ScenarioOutcome` と同じ「中途半端な結果を作らない」
        という理由による、格子点結果レベルでの対応する不変条件である。
        """
        is_not_evaluated = self.status is CellStatus.NOT_EVALUATED
        reason_is_none = self.not_evaluated_reason is None
        if is_not_evaluated == reason_is_none:
            raise ParameterError(
                "status が NOT_EVALUATED であることと not_evaluated_reason "
                "が指定されていることは一致していなければならない（評価 "
                "対象外なら理由が必ず埋まり、それ以外なら理由は指定できな"
                f"い）: status={self.status!r}, "
                f"not_evaluated_reason={self.not_evaluated_reason!r}"
            )


@dataclass(frozen=True, slots=True)
class SweepResult:
    """掃引全体の結果（要件6.6, 7.2）。

    Attributes:
        spec: この掃引を定義した `SweepSpec`（`trajectory_sim.sweep`、
            タスク3.2）。
        base_params: 掃引の基準となった `ScenarioParams`。
        cells: 全格子点の結果。
    """

    spec: SweepSpec
    base_params: ScenarioParams
    cells: tuple[CellResult, ...]
