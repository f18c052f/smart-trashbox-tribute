"""Throw Record の最小スキーマと dict 往復（要件 9.1 / 9.2 / 9.3）。

本モジュールは L5 層であり、実行時に `prediction_core.types` /
`prediction_core.config` / `prediction_core.errors` のみを import する
（design.md「Dependency Direction」）。`predictor.py`（L4）や、まだ存在しない
`tracker.py`（L6）を import しない。1投擲の記録に `predict()` そのものは
不要であり（Replay はタスク 4.3 の担当）、`predictor` への依存は生じない。

**このタスク（4.1）が実装する範囲**: `ThrowRecord` データクラス本体と
`to_dict` / `from_dict` のみ。`to_json` / `from_json` / `replay` /
`predictions_equivalent` はタスク 4.2 / 4.3 の担当であり、ここでは実装しない
（スタブも置かない）。

**手動での dict 変換**（`dataclasses.asdict()` を使わない理由）:

- `predictions` の要素は `Prediction` / `InvalidPrediction` の直和型であり、
  復元時に判別するための `kind` キー（`"prediction"` / `"invalid"`）を
  人為的に付与する必要がある（design.md「`predictions` 要素の判別」）。
- `StrEnum` メンバー（`SourceKind` / `InvalidReason`）は `.value` で明示的に
  文字列化する契約になっている。
- ネストした `PredictionConfig`（`ThrowRecord.config` 直下と、各
  `Prediction` / `InvalidPrediction.config` の両方）と `TrajectoryParameters`
  （`Prediction.trajectory` の中）もそれぞれ辞書化・復元する必要がある。

**非有限値の扱い**（要件 9.3、design.md「Validation」）: `to_dict` は非有限
値（NaN / Infinity）を含む場合でも例外にしない。メモリ上の忠実性を保ち、
拒否は JSON 化の時点（タスク 4.2）に限定する。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from prediction_core.config import PredictionConfig
from prediction_core.errors import RecordSchemaError
from prediction_core.types import (
    InvalidPrediction,
    InvalidReason,
    Prediction,
    PredictionOutcome,
    Sample,
    SourceKind,
    TrajectoryParameters,
)

__all__ = ["SCHEMA_VERSION", "ThrowRecord"]

SCHEMA_VERSION: str = "1.0"
"""Throw Record スキーマの版（design.md「Throw Record JSON スキーマ」）。

互換性を壊す変更（必須フィールドの追加・削除・意味変更）でのみ更新する
（要件 9.6）。
"""

_PREDICTION_KIND = "prediction"
_INVALID_KIND = "invalid"


def _sample_to_dict(sample: Sample) -> dict[str, object]:
    """`Sample` を JSON スキーマ表の `samples` 要素へ写像する。"""
    return {
        "t_ms": sample.t_ms,
        "x_mm": sample.x_mm,
        "y_mm": sample.y_mm,
        "z_mm": sample.z_mm,
    }


def _sample_from_dict(data: Mapping[str, object]) -> Sample:
    """`samples` 要素から `Sample` を復元する。"""
    return Sample(
        t_ms=data["t_ms"],
        x_mm=data["x_mm"],
        y_mm=data["y_mm"],
        z_mm=data["z_mm"],
    )


def _config_to_dict(config: PredictionConfig) -> dict[str, object]:
    """`PredictionConfig` を dict へ写像する（`config` キー、ネストにも使用）。"""
    return {
        "gravity_mm_s2": config.gravity_mm_s2,
        "min_samples": config.min_samples,
        "measure_elapsed": config.measure_elapsed,
        "time_degeneracy_rel_tol": config.time_degeneracy_rel_tol,
    }


def _config_from_dict(data: Mapping[str, object]) -> PredictionConfig:
    """dict から `PredictionConfig` を復元する。"""
    return PredictionConfig(
        gravity_mm_s2=data["gravity_mm_s2"],
        min_samples=data["min_samples"],
        measure_elapsed=data["measure_elapsed"],
        time_degeneracy_rel_tol=data["time_degeneracy_rel_tol"],
    )


def _trajectory_to_dict(trajectory: TrajectoryParameters) -> dict[str, object]:
    """`TrajectoryParameters` を dict へ写像する（`Prediction.trajectory` 用）。"""
    return {
        "t_ref_ms": trajectory.t_ref_ms,
        "x0_mm": trajectory.x0_mm,
        "y0_mm": trajectory.y0_mm,
        "z0_mm": trajectory.z0_mm,
        "estimated_vx_mm_s": trajectory.estimated_vx_mm_s,
        "estimated_vy_mm_s": trajectory.estimated_vy_mm_s,
        "estimated_vz_mm_s": trajectory.estimated_vz_mm_s,
        "gravity_mm_s2": trajectory.gravity_mm_s2,
    }


def _trajectory_from_dict(data: Mapping[str, object]) -> TrajectoryParameters:
    """dict から `TrajectoryParameters` を復元する。"""
    return TrajectoryParameters(
        t_ref_ms=data["t_ref_ms"],
        x0_mm=data["x0_mm"],
        y0_mm=data["y0_mm"],
        z0_mm=data["z0_mm"],
        estimated_vx_mm_s=data["estimated_vx_mm_s"],
        estimated_vy_mm_s=data["estimated_vy_mm_s"],
        estimated_vz_mm_s=data["estimated_vz_mm_s"],
        gravity_mm_s2=data["gravity_mm_s2"],
    )


def _prediction_outcome_to_dict(outcome: PredictionOutcome) -> dict[str, object]:
    """`PredictionOutcome`（直和型）を `kind` キー付きの dict へ写像する。

    復元時に直和型を判別できるよう、`isinstance` 分岐で `kind` を
    明示的に付与する（design.md「Invariants」）。
    """
    if isinstance(outcome, Prediction):
        return {
            "kind": _PREDICTION_KIND,
            "predicted_hit_x_mm": outcome.predicted_hit_x_mm,
            "predicted_hit_y_mm": outcome.predicted_hit_y_mm,
            "predicted_hit_time_ms": outcome.predicted_hit_time_ms,
            "remaining_time_ms": outcome.remaining_time_ms,
            "estimated_vx_mm_s": outcome.estimated_vx_mm_s,
            "estimated_vy_mm_s": outcome.estimated_vy_mm_s,
            "estimated_vz_mm_s": outcome.estimated_vz_mm_s,
            "residual": outcome.residual,
            "trajectory": _trajectory_to_dict(outcome.trajectory),
            "sample_count": outcome.sample_count,
            "based_on_time_ms": outcome.based_on_time_ms,
            "elapsed_ms": outcome.elapsed_ms,
            "config": _config_to_dict(outcome.config),
        }

    # この時点で `isinstance(outcome, InvalidPrediction)` である
    # （`PredictionOutcome = Prediction | InvalidPrediction` の直和のため）。
    return {
        "kind": _INVALID_KIND,
        "reason": outcome.reason.value,
        "detail": outcome.detail,
        "sample_count": outcome.sample_count,
        "based_on_time_ms": outcome.based_on_time_ms,
        "elapsed_ms": outcome.elapsed_ms,
        "config": _config_to_dict(outcome.config),
    }


def _prediction_outcome_from_dict(data: Mapping[str, object]) -> PredictionOutcome:
    """`kind` キーで判別しながら dict から `PredictionOutcome` を復元する。

    `kind` が `"prediction"` / `"invalid"` のいずれでもない場合は
    `RecordSchemaError` を送出する（最低限の防御。厳密な必須キー検証は
    タスク 4.2 の担当）。
    """
    kind = data.get("kind")
    if kind == _PREDICTION_KIND:
        return Prediction(
            predicted_hit_x_mm=data["predicted_hit_x_mm"],
            predicted_hit_y_mm=data["predicted_hit_y_mm"],
            predicted_hit_time_ms=data["predicted_hit_time_ms"],
            remaining_time_ms=data["remaining_time_ms"],
            estimated_vx_mm_s=data["estimated_vx_mm_s"],
            estimated_vy_mm_s=data["estimated_vy_mm_s"],
            estimated_vz_mm_s=data["estimated_vz_mm_s"],
            residual=data["residual"],
            trajectory=_trajectory_from_dict(data["trajectory"]),
            sample_count=data["sample_count"],
            based_on_time_ms=data["based_on_time_ms"],
            elapsed_ms=data["elapsed_ms"],
            config=_config_from_dict(data["config"]),
        )

    if kind == _INVALID_KIND:
        return InvalidPrediction(
            reason=InvalidReason(data["reason"]),
            detail=data["detail"],
            sample_count=data["sample_count"],
            based_on_time_ms=data["based_on_time_ms"],
            elapsed_ms=data["elapsed_ms"],
            config=_config_from_dict(data["config"]),
        )

    raise RecordSchemaError(
        f"predictions 要素の kind={kind!r} が不正です。"
        f'"{_PREDICTION_KIND}" または "{_INVALID_KIND}" のいずれかでなければ'
        "なりません。"
    )


@dataclass(frozen=True, slots=True)
class ThrowRecord:
    """1回の投擲を表す最小スキーマ（要件 9.1 / 9.2）。

    集約ルートであり、`record_id` により同一性を持つ（design.md「Domain
    Model」）。予測処理時間はレコード専用フィールドを持たず、`predictions`
    の各要素（`Prediction.elapsed_ms` / `InvalidPrediction.elapsed_ms`）が
    個別に保持する（要件 9.2 の明示的な制約）。

    Attributes:
        record_id: 投擲の識別子。
        source: 観測サンプル列の入力元（`live` / `recorded` / `simulated`）。
        config: この投擲の予測に使用したパラメータ。
        samples: 観測サンプル系列（記録順）。
        predictions: 予測結果系列（生成順）。`Prediction` と
            `InvalidPrediction` の直和が混在してよい。
        schema_version: 本スキーマの版。既定値は `SCHEMA_VERSION`。
        extra: 下流が追加した項目の退避先（要件 9.6）。本タスクでは
            単純に往復させるのみで、未知トップレベルキーの退避ロジック
            自体はタスク 4.2 の担当。

    `record_id` の空文字列チェックなど、値の妥当性検証は本タスクの範囲外
    （design.md: `ThrowRecord` 自体は値オブジェクトとして検証を持たず、
    検証は `ThrowPredictionTracker` 側の責務）。
    """

    record_id: str
    source: SourceKind
    config: PredictionConfig
    samples: tuple[Sample, ...]
    predictions: tuple[PredictionOutcome, ...]
    schema_version: str = SCHEMA_VERSION
    extra: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        """Throw Record JSON スキーマ（最小形）に従う dict を返す。

        非有限値（NaN / Infinity）を含んでいても例外にしない。メモリ上の
        忠実性を保つ（design.md「Validation」）。
        """
        return {
            "schema_version": self.schema_version,
            "record_id": self.record_id,
            "source": self.source.value,
            "config": _config_to_dict(self.config),
            "samples": [_sample_to_dict(sample) for sample in self.samples],
            "predictions": [
                _prediction_outcome_to_dict(outcome) for outcome in self.predictions
            ],
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "ThrowRecord":
        """dict から `ThrowRecord` を復元する。

        `samples` / `predictions` は入力が `list` でも内部表現である
        `tuple` へ変換する（不変性のため）。`predictions` の各要素は
        `kind` キーで直和型を判別して復元する。
        """
        return cls(
            record_id=data["record_id"],
            source=SourceKind(data["source"]),
            config=_config_from_dict(data["config"]),
            samples=tuple(_sample_from_dict(item) for item in data["samples"]),
            predictions=tuple(
                _prediction_outcome_from_dict(item) for item in data["predictions"]
            ),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
            extra=dict(data.get("extra", {})),
        )
