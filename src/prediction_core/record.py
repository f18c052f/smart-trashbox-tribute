"""Throw Record の最小スキーマ・dict / JSON 往復・Replay（要件 9.1-9.4 / 9.6）。

本モジュールは L5 層であり、実行時に `prediction_core.types` /
`prediction_core.config` / `prediction_core.errors` に加え、`replay` の
実装のためだけに `prediction_core.predictor`（L4）を import する
（design.md「Dependency Direction」表: L5 の `record` は 0〜4層を import
可能）。まだ存在しない `tracker.py`（L6、逐次蓄積器）は import しない。
`replay` は記録された観測サンプル系列の前置列に `predict()` を直接適用して
予測系列を再構成するため、逐次蓄積器そのものには依存しない（design.md
「State Management / Risks」: スキーマ層を import しただけで蓄積器まで
引きずり込まれる状態を避けるため）。

**このタスク（4.3）が実装する範囲**: `replay` / `predictions_equivalent`。
`to_dict` / `from_dict` / `to_json` / `from_json`（タスク 4.1 / 4.2）は
変更しない。

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

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace

from prediction_core.config import PredictionConfig
from prediction_core.errors import RecordSchemaError, RecordSerializationError
from prediction_core.predictor import predict
from prediction_core.types import (
    InvalidPrediction,
    InvalidReason,
    Prediction,
    PredictionOutcome,
    Sample,
    SourceKind,
    TrajectoryParameters,
)

__all__ = [
    "SCHEMA_VERSION",
    "ThrowRecord",
    "predictions_equivalent",
    "replay",
]

SCHEMA_VERSION: str = "1.0"
"""Throw Record スキーマの版（design.md「Throw Record JSON スキーマ」）。

互換性を壊す変更（必須フィールドの追加・削除・意味変更）でのみ更新する
（要件 9.6）。
"""

_PREDICTION_KIND = "prediction"
_INVALID_KIND = "invalid"

_REQUIRED_TOP_LEVEL_KEYS: tuple[str, ...] = (
    "record_id",
    "source",
    "config",
    "samples",
    "predictions",
)
"""`from_dict` が必須とするトップレベルキー（要件 9.2 / 9.3）。"""

_KNOWN_TOP_LEVEL_KEYS: frozenset[str] = frozenset(
    {*_REQUIRED_TOP_LEVEL_KEYS, "schema_version", "extra"}
)
"""`ThrowRecord` が意味を持って解釈するトップレベルキー（要件 9.6）。

これ以外のトップレベルキーは `from_dict` が `extra` へ退避する。"""


def _is_array_like(value: object) -> bool:
    """`samples` / `predictions` に許容する「配列」形かどうかを判定する。

    JSON 配列は `json.loads` で `list` になる。`str` / `bytes` はイテラブル
    だが要素反復の意味が異なるため明示的に除外する。
    """
    return isinstance(value, (list, tuple)) and not isinstance(value, (str, bytes))


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
        extra: 下流が追加した項目の退避先（要件 9.6）。`from_dict` は
            明示的な `extra` キーの中身に加え、未知のトップレベルキーも
            ここへ統合する（往復で情報を失わない）。

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

        必須キー（`record_id` / `source` / `config` / `samples` /
        `predictions`）の欠落、および明らかな型不一致（`source` が
        `SourceKind` の有効な値でない、`config` が dict でない、
        `samples` / `predictions` が配列でない）は `RecordSchemaError` を
        送出する。メッセージには問題のキー名を含める（design.md「Error
        Handling」）。未知のトップレベルキーは `extra` へ退避し、
        明示的な `extra` キーの中身とマージする（要件 9.6）。
        """
        missing = [key for key in _REQUIRED_TOP_LEVEL_KEYS if key not in data]
        if missing:
            raise RecordSchemaError(
                "ThrowRecord の復元に必要なキーが欠落しています: "
                f"{', '.join(sorted(missing))}"
            )

        source_raw = data["source"]
        try:
            source = SourceKind(source_raw)
        except ValueError as e:
            raise RecordSchemaError(
                f"キー 'source' の値が不正です（SourceKind として解釈できません）: "
                f"{source_raw!r}"
            ) from e

        config_raw = data["config"]
        if not isinstance(config_raw, Mapping):
            raise RecordSchemaError(
                f"キー 'config' はオブジェクト(dict)である必要があります: "
                f"{config_raw!r}"
            )

        samples_raw = data["samples"]
        if not _is_array_like(samples_raw):
            raise RecordSchemaError(
                f"キー 'samples' は配列である必要があります: {samples_raw!r}"
            )

        predictions_raw = data["predictions"]
        if not _is_array_like(predictions_raw):
            raise RecordSchemaError(
                f"キー 'predictions' は配列である必要があります: {predictions_raw!r}"
            )

        explicit_extra = data.get("extra", {})
        if not isinstance(explicit_extra, Mapping):
            raise RecordSchemaError(
                f"キー 'extra' はオブジェクト(dict)である必要があります: "
                f"{explicit_extra!r}"
            )

        # 未知のトップレベルキーは失わず extra へ統合する（要件 9.6）。
        # 明示的な extra の中身をベースに、未知キーの値で上書き・追加する
        # （どちらも「情報を失わない」ことが目的であり、キー名の衝突は
        # 実運用では起きない想定。衝突時は未知キー側を優先する）。
        merged_extra: dict[str, object] = dict(explicit_extra)
        for key, value in data.items():
            if key not in _KNOWN_TOP_LEVEL_KEYS:
                merged_extra[key] = value

        return cls(
            record_id=data["record_id"],
            source=source,
            config=_config_from_dict(config_raw),
            samples=tuple(_sample_from_dict(item) for item in samples_raw),
            predictions=tuple(
                _prediction_outcome_from_dict(item) for item in predictions_raw
            ),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
            extra=merged_extra,
        )

    def to_json(self, *, indent: int | None = None) -> str:
        """`ThrowRecord` を JSON 文字列へ直列化する（要件 9.3）。

        `json.dumps(..., allow_nan=False, ensure_ascii=False)` を用いる。
        既定の `NaN` / `Infinity` 出力は RFC 8259 準拠ではなく、
        TypeScript 可視化側の `JSON.parse` が受け付けないため
        （design.md「ThrowRecordCodec / Implementation Notes」）。

        非有限値（NaN / Infinity）を含むレコードは `RecordSerializationError`
        を送出する。`to_dict()` 自体はメモリ上の忠実性を保つため常に成功する
        （非有限値でも例外にしない）が、JSON 化はその時点で拒否する。
        """
        data = self.to_dict()
        try:
            return json.dumps(
                data, allow_nan=False, ensure_ascii=False, indent=indent
            )
        except ValueError as e:
            raise RecordSerializationError(
                "ThrowRecord を JSON へ直列化できません: 非有限値(NaN/Infinity)を"
                "含むフィールドが存在します。RFC 8259 準拠の JSON では表現でき"
                "ません。"
            ) from e

    @classmethod
    def from_json(cls, text: str) -> "ThrowRecord":
        """JSON 文字列から `ThrowRecord` を復元する（要件 9.3）。

        `json.loads` でパースした dict を `from_dict` に委譲する。パース
        結果に対する検証（必須キー欠落・型不一致・未知キー退避）は
        `from_dict` と共通である。
        """
        return cls.from_dict(json.loads(text))


def replay(record: ThrowRecord) -> tuple[PredictionOutcome, ...]:
    """記録された観測サンプル系列から予測系列を再構成する（要件 9.4）。

    `record.samples` を記録順の**前置列**（1点目、1〜2点目、…、全点）に
    分け、それぞれへ `record.config` で `predict()` を適用する。
    `ThrowPredictionTracker`（逐次蓄積器、まだ存在しない）には一切依存
    しない（design.md「Batch / Job Contract」）。

    `predict()` は入力を `t_ms` 昇順に安定ソートしてから処理するため、
    ここで渡す前置列は `record.samples` のインデックス順のままでよい
    （事前に時刻でソートする必要はない）。

    副作用を持たず、何度呼んでも同じ結果を返す（design.md
    「Idempotency & recovery」）。
    """
    return tuple(
        predict(record.samples[: prefix_length + 1], record.config)
        for prefix_length in range(len(record.samples))
    )


def predictions_equivalent(
    left: Sequence[PredictionOutcome],
    right: Sequence[PredictionOutcome],
) -> bool:
    """予測系列2つが同値かどうかを判定する（要件 9.4）。

    `elapsed_ms` は処理時間の実測値であり、呼び出しのたびに変動しうる
    ため比較対象から**除外する**（design.md「Invariants」）。それ以外の
    全フィールドは厳密に比較する。

    - 長さが異なれば偽
    - 同じ位置の要素の型が異なれば（一方が `Prediction`、他方が
      `InvalidPrediction`）偽
    - 上記のいずれにも該当しなければ、各要素を `elapsed_ms` を揃えた上で
      比較し、全ペアが一致すれば真
    """
    if len(left) != len(right):
        return False

    for left_outcome, right_outcome in zip(left, right):
        if type(left_outcome) is not type(right_outcome):
            return False
        # ここまでで同じ型（Prediction 同士、または InvalidPrediction
        # 同士）であることが確定している。両型とも `elapsed_ms` フィールド
        # を持つため、同一の値へ揃えてから残り全フィールドを比較する。
        if replace(left_outcome, elapsed_ms=None) != replace(
            right_outcome, elapsed_ms=None
        ):
            return False

    return True
