"""`prediction_core` への唯一の接続層（design.md「PredictionLink」/ 要件3.1,
3.3, 3.4, 3.5, 3.7, 3.9, 7.4）。

`ThrowPredictionTracker` にサンプルを1点ずつ追加し、返ってきた
`PredictionOutcome` を走査して `Prediction` のみを目標更新
（`trajectory_sim.drivetrain.TargetUpdate`）へ変換する。`InvalidPrediction`
は目標更新へ変換しない（成功として扱わない）が、その理由（`reason`）は
`ThrowRecord.predictions` にそのまま保持される（要件3.5）。

**予測の数式に一切触れない**（design.md「PredictionLink」Responsibilities
& Constraints）: フィッティング・床面交点・残差の計算は行わず、上流
`prediction_core` の公開 API（`ThrowPredictionTracker` / `Prediction` の
フィールド）から得られる値をそのまま用いる。

`extra` は掃引の格子点番号・試行番号などを名前空間キー `"sim"` の1キーに
収めた、呼び出し側（`ScenarioEvaluator` 等、後続タスク）が組み立て済みの
マッピングとして受け取り、本層はこれを一切加工せず `ThrowRecord.extra`
へそのまま渡す（要件3.9。design.md Preconditions:
`extra` は `{"sim": {...}}` の形で渡す、の解釈は本モジュール docstring
末尾の補足を参照）。

**常に `from prediction_core import ...` の公開 API のみを参照する**
（`prediction_core.tracker` 等のサブモジュール直接 import は行わない。
design.md「PredictionLink」Validation: `BoundaryCheck` が静的に検査する）。

補足（設計上のあいまいさの解消）: design.md 本文の「収める」という表現
だけを読むと、本層が `sim` 名前空間の構造自体を組み立てるようにも読める。
しかし Service Interface の Precondition は「`extra` は
`{"sim": {"sim_extra_version": "1.0", "cell_index": ..., "trial_index":
...}}` の形で**渡す**」と、既に組み立てられた引数の形として記述して
いる。`run_prediction` のシグネチャも `cell_index` / `trial_index` を
個別の引数として受け取らず、汎用の `extra: Mapping[str, object]` を
そのまま受け取る形になっている（`record_id` も同様に、掃引の格子点番号・
試行番号から**呼び出し側が**決定的に組み立てた文字列をそのまま受け取る）。
したがって本モジュールは `extra` の中身を検証・変換せず、素通しする
方針を採用する。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from prediction_core import (
    Prediction,
    PredictionConfig,
    Sample,
    SourceKind,
    ThrowPredictionTracker,
    ThrowRecord,
)

from trajectory_sim.drivetrain import TargetUpdate
from trajectory_sim.params import ObservationParams

__all__ = ["PredictionTimeline", "run_prediction"]


@dataclass(frozen=True, slots=True)
class PredictionTimeline:
    """1投擲分の予測系列を、目標更新系列と Throw Record にまとめたもの
    （design.md「PredictionLink」Service Interface）。

    Attributes:
        record: `prediction_core.ThrowRecord`。simulated 由来として構築
            されており、`predictions` は `Prediction` / `InvalidPrediction`
            の直和が生成順に混在する。
        updates: 有効な予測から得られた目標更新の系列。
            `available_time_ms` の昇順（design.md Postconditions）。
        valid_prediction_count: 有効な予測（`Prediction`）の件数。
            `len(updates)` と常に一致する。
        final_prediction: 最後に得られた有効な `Prediction`。有効な予測が
            1件も無い場合は `None`（要件3.5: 呼び出し側へその旨を返す）。
    """

    record: ThrowRecord
    updates: tuple[TargetUpdate, ...]
    valid_prediction_count: int
    final_prediction: Prediction | None


def run_prediction(
    samples: Sequence[Sample],
    observation: ObservationParams,
    config: PredictionConfig,
    record_id: str,
    extra: Mapping[str, object],
) -> PredictionTimeline:
    """観測サンプル列を1点ずつ `ThrowPredictionTracker` へ追加し、予測タイムラインを返す。

    （design.md「PredictionLink」Service Interface / 要件3.1, 3.3, 3.4,
    3.5, 3.7, 3.9, 7.4）

    Preconditions:
        `samples` は時刻昇順であること（`ObservationModel` の
        Postconditions が保証する。本関数は再ソート・再検証を行わない）。
        `extra` は `{"sim": {"sim_extra_version": "1.0", "cell_index":
        ..., "trial_index": ...}}` の形で渡されていること（呼び出し側の
        責務。本関数はこれを検証せず、そのまま `ThrowRecord.extra` へ渡す）。

    Postconditions:
        `updates` は `available_time_ms` の昇順。
        `valid_prediction_count == len(updates)`。
        `record.samples` は入力 `samples` 列と一致する。
    """
    tracker = ThrowPredictionTracker(
        record_id=record_id, source=SourceKind.SIMULATED, config=config
    )

    updates: list[TargetUpdate] = []
    final_prediction: Prediction | None = None

    for sample in samples:
        outcome = tracker.add_sample(sample)
        if isinstance(outcome, Prediction):
            updates.append(
                TargetUpdate(
                    available_time_ms=(
                        outcome.based_on_time_ms
                        + observation.sample_latency_ms
                        + observation.prediction_latency_ms
                    ),
                    x_mm=outcome.predicted_hit_x_mm,
                    y_mm=outcome.predicted_hit_y_mm,
                    impact_time_ms=outcome.predicted_hit_time_ms,
                )
            )
            final_prediction = outcome

    record = ThrowRecord(
        record_id=record_id,
        source=SourceKind.SIMULATED,
        config=config,
        samples=tracker.samples,
        predictions=tracker.predictions,
        extra=extra,
    )

    return PredictionTimeline(
        record=record,
        updates=tuple(updates),
        valid_prediction_count=len(updates),
        final_prediction=final_prediction,
    )
