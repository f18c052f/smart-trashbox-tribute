"""掃引結果を JSON 化可能な辞書へ変換し、ファイルへ書き出す
（design.md「ResultSerializer」/ 要件 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7,
8.2, 9.1, 9.3, 9.4, 9.5, 9.6, 9.7, 10.5, 5.8）。

**タスク4.1**: `sweep_result_to_dict` という、純粋でメモリ上だけの辞書
組み立て関数を実装した。与えられた `SweepResult` の内容を、非有限値や
未知の enum 値を拒否せず、そのまま忠実に辞書へ写像する。

**タスク4.2（本追加分）**: 書き出し前の検証（`_validate_output_dict` /
`_find_non_finite` / `_find_unknown_enum_value`）と、実際のファイル書き出し
（`write_sweep_result`）を追加する（design.md「ResultSerializer」
Batch / Job Contract）。検証は純粋関数として I/O から切り離し、
ファイルへ触れる前に完了させることで「部分的なファイルを残さない」
（要件7.5 相当の運用要件、および設計上の耐障害性）を実現する。
出力キーの許可リスト検査により、「合否」「達成」「NFR-7」に相当する
断定的なキーが混入しないことをテストで固定する（要件9.5）。

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
import json
import math
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path

from trajectory_sim.errors import OutputError
from trajectory_sim.params import CalibrationStage, CatchPolicy, Provenance, ScenarioParams
from trajectory_sim.results import (
    MODEL_EXCLUSIONS,
    CellResult,
    CellStatus,
    NotEvaluatedReason,
    SweepResult,
)
from trajectory_sim.sweep import SweepKind

__all__ = ["OUTPUT_SCHEMA_VERSION", "sweep_result_to_dict", "write_sweep_result"]

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


_ALLOWED_TOP_LEVEL_KEYS: frozenset[str] = frozenset(
    {
        "output_schema_version",
        "calibration",
        "model_exclusions",
        "sweep",
        "parameters",
        "parameter_provenance",
        "cells",
        "throw_records",
    }
)
"""`sweep_result_to_dict` が出力する最上位キーの許可リスト（要件9.5）。

このリストに無いキーが最上位に混入した場合は `OutputError` とする。
「合否」「達成」「NFR-7」に相当する断定的な項目が後から静かに紛れ込む
ことを防ぐ回帰ガードである。ネストした構造の内部キーはそれぞれの
モジュール（`results` / `params`）が定義するフィールド名で決まるため、
ここでは検査しない。
"""

_ALLOWED_CALIBRATION_KEYS: frozenset[str] = frozenset({"stage", "notice"})
"""`calibration` サブ辞書に許されるキー（要件9.1, 9.3, 9.5）。

`calibration` は `stage` と（未較正時のみの）`notice` 以外のキーを
持ち得ない。ここに断定的なキーが紛れ込む余地を防ぐ第二の防衛層である。
"""


def _find_non_finite(value: object, path: str = "$") -> str | None:
    """`value` の中に非有限の `float`（`inf` / `-inf` / `nan`）があれば、
    その場所を示すパス文字列を返す（要件9.5 に隣接する出力契約の検査、
    design.md「ResultSerializer」Batch / Job Contract）。

    `dict` は `.items()` を、`list` / `tuple` は添字付きで再帰する。
    それ以外の葉（`str` / `int` / `bool` / `None` 等）は再帰しない。
    """
    if isinstance(value, float):
        if not math.isfinite(value):
            return path
        return None
    if isinstance(value, Mapping):
        for key, item in value.items():
            found = _find_non_finite(item, f"{path}.{key}")
            if found is not None:
                return found
        return None
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found = _find_non_finite(item, f"{path}[{index}]")
            if found is not None:
                return found
        return None
    return None


def _find_unknown_enum_value(result_dict: Mapping[str, object]) -> str | None:
    """既知の enum の集合に含まれない文字列値があれば、その説明を返す
    （design.md「ResultSerializer」Batch / Job Contract:
    「非有限値・未知の列挙値を検出したら書き出さずに `OutputError`」）。

    `throw_records` の中身（`ThrowRecord.to_dict()` の結果）は、ここでの
    enum 知識を持たずに素通しする（モジュール docstring 参照）。
    """
    calibration_stages = {stage.value for stage in CalibrationStage}
    sweep_kinds = {kind.value for kind in SweepKind}
    catch_policies = {policy.value for policy in CatchPolicy}
    provenances = {provenance.value for provenance in Provenance}
    cell_statuses = {status.value for status in CellStatus}
    not_evaluated_reasons = {reason.value for reason in NotEvaluatedReason}

    calibration = result_dict.get("calibration", {})
    stage_value = calibration.get("stage") if isinstance(calibration, Mapping) else None
    if stage_value not in calibration_stages:
        return f"calibration.stage の値 {stage_value!r} は既知の CalibrationStage ではない。"

    sweep = result_dict.get("sweep", {})
    kind_value = sweep.get("kind") if isinstance(sweep, Mapping) else None
    if kind_value not in sweep_kinds:
        return f"sweep.kind の値 {kind_value!r} は既知の SweepKind ではない。"

    parameters = result_dict.get("parameters", {})
    if not isinstance(parameters, Mapping):
        parameters = {}

    params_stage_value = parameters.get("calibration_stage")
    if params_stage_value not in calibration_stages:
        return (
            f"parameters.calibration_stage の値 {params_stage_value!r} は"
            "既知の CalibrationStage ではない。"
        )

    catch = parameters.get("catch", {})
    policy_value = catch.get("policy") if isinstance(catch, Mapping) else None
    if policy_value not in catch_policies:
        return f"parameters.catch.policy の値 {policy_value!r} は既知の CatchPolicy ではない。"

    params_provenance = parameters.get("provenance", {})
    if isinstance(params_provenance, Mapping):
        for path_key, provenance_value in params_provenance.items():
            if provenance_value not in provenances:
                return (
                    f"parameters.provenance[{path_key!r}] の値 {provenance_value!r} は"
                    "既知の Provenance ではない。"
                )

    top_level_provenance = result_dict.get("parameter_provenance", {})
    if isinstance(top_level_provenance, Mapping):
        for path_key, provenance_value in top_level_provenance.items():
            if provenance_value not in provenances:
                return (
                    f"parameter_provenance[{path_key!r}] の値 {provenance_value!r} は"
                    "既知の Provenance ではない。"
                )

    cells = result_dict.get("cells", [])
    if isinstance(cells, (list, tuple)):
        for index, cell in enumerate(cells):
            if not isinstance(cell, Mapping):
                continue
            status_value = cell.get("status")
            if status_value not in cell_statuses:
                return (
                    f"cells[{index}].status の値 {status_value!r} は既知の CellStatus ではない。"
                )
            reason_value = cell.get("not_evaluated_reason")
            if reason_value is not None and reason_value not in not_evaluated_reasons:
                return (
                    f"cells[{index}].not_evaluated_reason の値 {reason_value!r} は"
                    "既知の NotEvaluatedReason ではない。"
                )

    return None


def _validate_output_dict(result_dict: Mapping[str, object]) -> None:
    """書き出し前の出力辞書を検証する（design.md「ResultSerializer」
    Batch / Job Contract、要件7.5, 8.2, 9.5）。

    I/O を一切行わない純粋関数である。ファイルシステムへ触れる前に
    すべての検証を終えることで、「部分的なファイルを残さない」ことを
    保証する（`write_sweep_result` はこの関数が例外を送出しないことを
    確認してから初めて一時ファイルへ書き込む）。

    以下のいずれかに該当する場合 `OutputError` を送出する:

    - 最上位キーが許可リスト（`_ALLOWED_TOP_LEVEL_KEYS`）を超えている
    - `calibration` サブ辞書のキーが許可リスト
      （`_ALLOWED_CALIBRATION_KEYS`）を超えている
    - 非有限の `float` が構造のどこかに含まれている
    - 既知の enum の集合に含まれない文字列値がある
    """
    extra_top_level_keys = set(result_dict.keys()) - _ALLOWED_TOP_LEVEL_KEYS
    if extra_top_level_keys:
        raise OutputError(
            "出力に許可されていない最上位キーが含まれている（合否・達成・"
            f"NFR-7 相当の断定的なキーの混入防止, 要件9.5）: {sorted(extra_top_level_keys)!r}"
        )

    calibration = result_dict.get("calibration")
    if isinstance(calibration, Mapping):
        extra_calibration_keys = set(calibration.keys()) - _ALLOWED_CALIBRATION_KEYS
        if extra_calibration_keys:
            raise OutputError(
                "calibration に許可されていないキーが含まれている（要件9.5）: "
                f"{sorted(extra_calibration_keys)!r}"
            )

    non_finite_path = _find_non_finite(dict(result_dict))
    if non_finite_path is not None:
        raise OutputError(f"非有限値が {non_finite_path} に含まれている。")

    unknown_enum_message = _find_unknown_enum_value(result_dict)
    if unknown_enum_message is not None:
        raise OutputError(unknown_enum_message)


def write_sweep_result(result: SweepResult, path: str | Path) -> None:
    """`SweepResult` を検証したうえで、指定パスへ UTF-8 JSON として
    アトミックに書き出す（design.md「ResultSerializer」Batch / Job
    Contract、要件7.4, 7.5, 8.2, 9.5, 10.5, 5.8）。

    - 検証（`_validate_output_dict`）はファイルシステムへ触れる前に
      完了させる。検証が失敗した場合は何も書き出さない
      （「部分的なファイルを残さない」）。
    - `json.dumps` は `ensure_ascii=False` / `sort_keys=True` / `indent=2`
      / `allow_nan=False` で行う。
    - 一時ファイルへ書き込んでから `os.replace` でアトミックに置き換える
      ことで、中断時に不完全なファイルが最終パスに残らないようにする。
      一時ファイルは最終パスと同じディレクトリに作る（同一ファイル
      システム上でのみ `os.replace` がアトミックであるため）。
    - 同一の `SweepResult`（内容が同一な限り、別インスタンスでもよい）を
      書き出すと、バイト単位で同一のファイルが得られる（要件8.2）。
    - 既存ファイルは上書きする。
    """
    target_path = Path(path)

    result_dict = sweep_result_to_dict(result)
    _validate_output_dict(result_dict)

    json_text = json.dumps(
        result_dict,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    )

    fd, tmp_path_str = tempfile.mkstemp(
        dir=target_path.parent,
        prefix=f".{target_path.name}.",
        suffix=".tmp",
    )
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(json_text)
        os.replace(tmp_path, target_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
