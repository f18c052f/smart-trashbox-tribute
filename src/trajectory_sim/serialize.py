"""掃引結果を JSON 化可能な辞書へ変換する（design.md「ResultSerializer」/
要件 7.1, 7.2, 7.3, 7.6, 7.7, 9.1, 9.3, 9.4, 9.6, 9.7）。

**本タスク（4.1）の範囲**: `sweep_result_to_dict` という、純粋でメモリ上
だけの辞書組み立て関数のみを実装する。ファイルへの書き出し
（`write_sweep_result`）、出力キーの許可リスト検査、非有限値・未知の
enum 値の拒否（`OutputError`）、`json.dump` の設定（`ensure_ascii=False`
等）は、いずれも後続タスク（4.2）の責務であり、ここには含めない
（design.md「ResultSerializer」Batch / Job Contract は 4.2 が担う）。
したがって本モジュールは非有限値や未知の enum 値を拒否せず、与えられた
`SweepResult` の内容をそのまま忠実に辞書へ写像する。

`OUTPUT_SCHEMA_VERSION`（本 Spec 独自の出力形式の版）は、
`prediction_core.ThrowRecord` の `schema_version`（Throw Record 自身の
スキーマ版）とは異なるキー名（`output_schema_version`）で表す
（要件7.3）。両者は別の関心事であり、値の一致・不一致に意味を持たせない。

**`prediction_core` を一切 import しない**（design.md「Dependency
Direction」表: `prediction_core` の import を許すモジュールは
`params` / `results` / `prediction_link` / `__init__` の4つのみであり、
本モジュールはそこに含まれない）。`parameters` キーの組み立てでは、
`ScenarioParams.prediction`（上流 `prediction_core.PredictionConfig`）を
含む全パラメータ木を、`dataclasses` の型検査のみに基づく汎用的な
再帰関数 `_to_jsonable` で走査する。Python のデータクラス検査は完全に
duck-typed であるため、`PredictionConfig` の具体的な型を知らなくても
正しく辞書化できる（`task 2.4` の `observation.py` が確立した
「上流型を import せず素通しする」方針を、辞書化についても踏襲する）。

`ThrowRecord` だけは `_to_jsonable` の対象にしない。`ThrowRecord.to_dict()`
自身が `Prediction` / `InvalidPrediction` の直和型を判別する専用の変換を
持つため（`src/prediction_core/record.py`）、これを再定義せず、代表
シナリオが保持されている場合はその `to_dict()` の結果をそのまま埋め込む
（design.md「ResultSerializer」Responsibilities: 「再定義しない」）。

較正段階が未較正（`CalibrationStage.UNCALIBRATED`、既定値）の場合のみ
`calibration.notice` を出力する。M1 / M2 較正済みの場合は `stage` のみを
出力し、`notice` キー自体を（`None` にするのではなく）省略する
（design.md「ResultSerializer」Implementation Notes）。

成立割合（各格子点の `success_ratio`）と、それを判定するのに使った
閾値（`sweep.catch_ratio_threshold`）を同一の出力ドキュメント内に
共存させることで、「成立割合は最終的な合否条件の達成度ではなく、
与えられた前提での算出値である」（要件9.6）ことを、閾値を読み手が
必ず参照できる形で示す。
"""

from __future__ import annotations

import dataclasses
import enum
from collections.abc import Mapping

from trajectory_sim.params import CalibrationStage, ScenarioParams
from trajectory_sim.results import MODEL_EXCLUSIONS, CellResult, SweepResult

__all__ = ["OUTPUT_SCHEMA_VERSION", "sweep_result_to_dict"]

OUTPUT_SCHEMA_VERSION: str = "1.0"
"""本 Spec 独自の出力形式の版（要件7.3）。

`prediction_core.ThrowRecord.SCHEMA_VERSION`（Throw Record 自身のスキーマ
版）とは無関係かつ別のキー名（`output_schema_version`）で出力される。
互換性を壊す出力構造の変更（キーの追加・削除・意味変更）でのみ更新する。
"""

_UNCALIBRATED_NOTICE: str = (
    "本結果は較正段階が未較正のため感度分析用であり、絶対値を信用しては"
    "ならない。"
)
"""較正段階が未較正のときに必ず添える注意書き（要件9.3）。"""


def _to_jsonable(value: object) -> object:
    """`ScenarioParams` の木を含む任意の値を JSON 安全な構造へ変換する。

    `dataclasses` の型検査のみに基づく汎用的な再帰であり、`prediction_core`
    を import せずに、埋め込まれた `PredictionConfig` も含めて再帰できる
    （duck-typed）。非有限値の拒否や未知の enum 値の検査は行わない
    （それは書き出し時点、タスク4.2の責務）。

    `ThrowRecord` はここでは扱わない（呼び出し側が `.to_dict()` を直接
    使う。モジュール docstring 参照）。
    """
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _to_jsonable(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_to_jsonable(item) for item in value]
    return value


def _calibration_to_dict(base_params: ScenarioParams) -> dict[str, object]:
    """`calibration` キーを組み立てる（要件9.1, 9.3）。

    `stage` は較正段階に関わらず常に出力する。`notice` は未較正のときのみ
    キー自体を含める（`None` を入れるのではなく、省略する）。
    """
    calibration: dict[str, object] = {"stage": base_params.calibration_stage.value}
    if base_params.calibration_stage is CalibrationStage.UNCALIBRATED:
        calibration["notice"] = _UNCALIBRATED_NOTICE
    return calibration


def _sweep_to_dict(result: SweepResult) -> dict[str, object]:
    """`sweep` キーを組み立てる（要件6.6, 7.6, 8.1, 9.6）。

    `catch_ratio_threshold` を `cells`（各格子点の `success_ratio`）と
    同一ドキュメント内に共存させることで、成立割合が判定に使った閾値と
    常に併記される（要件9.6）。
    """
    spec = result.spec
    return {
        "kind": spec.kind.value,
        "axes": [
            {"name": axis.name, "unit": axis.unit, "values": list(axis.values)}
            for axis in spec.axes
        ],
        "trials_per_cell": spec.trials_per_cell,
        "seed": spec.seed,
        "catch_ratio_threshold": spec.catch_ratio_threshold,
    }


def _cell_to_dict(cell: CellResult) -> dict[str, object]:
    """1格子点分の `cells` 要素を組み立てる（要件6.5, 6.7, 10.4）。"""
    not_evaluated_reason = cell.not_evaluated_reason
    return {
        "axis_values": list(cell.axis_values),
        "status": cell.status.value,
        "success_ratio": cell.success_ratio,
        "metrics": dict(cell.metrics),
        "not_evaluated_reason": (
            not_evaluated_reason.value if not_evaluated_reason is not None else None
        ),
    }


def sweep_result_to_dict(result: SweepResult) -> dict[str, object]:
    """`SweepResult` を、前提と限界を必ず伴った辞書へ変換する。

    （design.md「ResultSerializer」出力の最上位構造 / 要件7.1, 7.2, 7.3,
    7.6, 7.7, 9.1, 9.3, 9.4, 9.6, 9.7）

    ファイルへの書き出しは行わない（純粋な変換のみ）。非有限値・未知の
    enum 値の検査、出力キーの許可リスト検査は行わない（タスク4.2の
    責務）。

    最上位キーは以下の8つのみ:
    ``output_schema_version`` / ``calibration`` / ``model_exclusions`` /
    ``sweep`` / ``parameters`` / ``parameter_provenance`` / ``cells`` /
    ``throw_records``。
    """
    base_params = result.base_params

    throw_records: list[dict[str, object]] = [
        cell.representative.record.to_dict()
        for cell in result.cells
        if cell.representative is not None and cell.representative.record is not None
    ]

    return {
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "calibration": _calibration_to_dict(base_params),
        "model_exclusions": dict(MODEL_EXCLUSIONS),
        "sweep": _sweep_to_dict(result),
        "parameters": _to_jsonable(base_params),
        "parameter_provenance": {
            path: provenance.value for path, provenance in base_params.provenance.items()
        },
        "cells": [_cell_to_dict(cell) for cell in result.cells],
        "throw_records": throw_records,
    }
