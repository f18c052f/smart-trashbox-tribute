"""`chassis_mechanism.assembly` の検査（design.md `#### Assembly` / 要件 4.5,
9.4, 9.5, 9.6, 9.7, 9.8, 10.1, 10.2, 10.3, 10.4, 10.5, 10.9）。

固定するのは次の8点である。

1. **出荷される観測記録は全項目が未記入のまま読み込める**こと（tasks.md タスク 2.4
   「⚠️ **6群の実測が始まる前から読み込めるようにする**（読み込み自体が失敗すると、
   組立前に他の検査を回せなくなる）」）。⚠️ **読み込みの検証と完了の判定を混同しない**
   ——空白の記録は「読める」が「完了ではない」。未記入の必須項目は**全件**が
   `missing_observations()` に現れる。
2. **代表値は3個の平均**であり、平均と一致しない記録は `ConsistencyError` で拒否される
   こと（design.md `#### Assembly` Invariants /「Revalidation Triggers」項目3）。
   ⚠️ 許容差の**境界の両側**を固定する。
3. **転動を伴わない測定は、その測定が捉えられない範囲の記述を必須とする**こと
   （要件 10.5 / design.md「記述が空なら `MeasurementError`」）。値だけでは測定にならない
   ——静的なノギス当てが転動時の実効半径を捉えないという限界は、値と同じだけ重要である。
4. **隙間の観測の部位名は算出側の5部位と一致する**こと（design.md `#### Assembly`
   Integration「⚠️ **部位が1つでも欠けた記録を「完了」と呼ばない**」）。
   ⚠️ 一覧を**書き写していない**ことを、部位名の文字列リテラルが `assembly.py` に
   1つも現れないという形で固定する（`clearance.CLEARANCE_ITEM_NAMES` が唯一の正）。
5. **組立完了の判定にモータへ通電する項目が1つも無い**こと（要件 9.8 /
   design.md「⚠️ **判定にモータへの通電を含む項目を1つも置かない**」）。
   ⚠️ 必須項目の識別子と `missing_observations()` の出力を語彙で検査し、
   `is_assembly_complete` の本体が `missing_observations(record) == ()` **そのもの**で
   あることを `ast` で固定する——余分な条件を後から足せる場所を残さない。
6. **未記入と仮値を区別できる**こと（要件 10.9 / design.md Risks「⚠️ **出所が
   `assumed` のままの必須項目を `missing_observations` が拾う**」）。概算のまま
   「完了」と呼ぶ経路を塞ぐ。
7. **パラメータ識別子が観測で動かない**こと（design.md `#### Baseline`
   「⚠️ **観測（`measurements.json`）は識別子に含めない**」/ タスク 1.4）。
   観測のたびに形状の再生成が要求される事態を、観測を書く側からも固定する。
8. **記録の読み書きが往復する**こと。未知キー・欠損・型不正はあらゆる階層で拒否する
   （`config` / `layout` / `joints` と同形）。

⚠️ **本ファイルは `catch_mechanism` を直接 import しない。** `assembly` は上流を
import してよいモジュールではなく（`test_chassis_boundaries.py`
`UPSTREAM_IMPORT_ALLOWED_MODULES`）、出所の型は `chassis_mechanism.params` 経由で
到達する。テストも同じ経路を使う。
"""

from __future__ import annotations

import ast
import inspect
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from chassis_mechanism import assembly as assembly_module
from chassis_mechanism.assembly import (
    DEFAULT_MEASUREMENTS_PATH,
    DERIVED_ABS_TOLERANCE_MM,
    MEASUREMENT_METHODS,
    NON_ROLLING_METHODS,
    OBSERVATION_PATHS,
    REQUIRED_CHECK_NAMES,
    ROLLING_METHOD,
    STATIC_LOADED_METHOD,
    WHEEL_OBSERVATION_COUNT,
    AssemblyCheck,
    AssemblyRecord,
    ClearanceObservation,
    FitDeviation,
    WheelObservation,
    dump_assembly_record,
    is_assembly_complete,
    load_assembly_record,
    missing_observations,
    representative_rolling_radius_mm,
)
from chassis_mechanism.clearance import CLEARANCE_ITEM_NAMES
from chassis_mechanism.config import (
    DEFAULT_DIMENSIONS_PATH,
    SCHEMA_VERSION,
    load_params,
    parameters_digest,
)
from chassis_mechanism.errors import (
    ConsistencyError,
    MeasurementError,
    ParameterError,
)
from chassis_mechanism.params import Provenance

_ASSEMBLY_SOURCE: str = Path(assembly_module.__file__).read_text(encoding="utf-8")
_ASSEMBLY_TREE: ast.Module = ast.parse(_ASSEMBLY_SOURCE)

#: 出荷されている寸法設定の識別子。
#: ⚠️ **`test_chassis_config.py::test_digest_is_pinned_to_a_stable_literal` と
#: 同じ文字列である。** 観測記録を足したことでこの値が動けば、観測のたびに形状の
#: 再生成が要求される（design.md `#### Baseline`）。二重に書いているのは、
#: 観測を**書き込む側**からも同じ錨を打つためである。
PINNED_PARAMETERS_DIGEST = (
    "sha256:d7d505da40e5965c5d06b7e3918328addae0cb3950fb0702c57e6dc948924fdd"
)

#: 完了判定に現れてはならない語彙（要件 9.8 / 9.9）。
#: ⚠️ **通電・走行・エンコーダ校正を必要とする項目を1つも置かない。** 語彙で縛るのは、
#: 「台上で少しだけ回してみる」程度の項目が必須項目として紛れ込む経路を塞ぐためである。
#: `motor` を挙げないのは、隙間の部位名 `motor_body`（静止した胴体下面の高さ）が
#: 正当な観測だからである——禁じたいのは部品の名ではなく**回す操作**である。
FORBIDDEN_COMPLETION_TOKENS = (
    "energi",
    "power_on",
    "powered",
    "encoder",
    "calibrat",
    "spin",
    "drive_test",
    "通電",
    "走行",
    "エンコーダ",
    "校正",
)


# ---------------------------------------------------------------------------
# 補助（記録の組み立て）
# ---------------------------------------------------------------------------


_WHEEL_DIAMETERS_MM = (59.1, 59.4, 59.3)


def _mean(values: Sequence[float]) -> float:
    return math.fsum(values) / len(values)


def _wheels(
    diameters: Sequence[float] | None = _WHEEL_DIAMETERS_MM,
    *,
    method: str = ROLLING_METHOD,
    limitation_note: str = "",
) -> tuple[WheelObservation, ...]:
    """3個のホイール観測を作る。`diameters` が `None` なら全件未記入。"""
    if diameters is None:
        return tuple(
            WheelObservation(
                index=index,
                effective_rolling_diameter_mm=None,
                method="",
                limitation_note="",
            )
            for index in range(WHEEL_OBSERVATION_COUNT)
        )
    return tuple(
        WheelObservation(
            index=index,
            effective_rolling_diameter_mm=diameter,
            method=method,
            limitation_note=limitation_note,
        )
        for index, diameter in enumerate(diameters)
    )


def _clearances(
    measured: Mapping[str, float] | None = None,
) -> tuple[ClearanceObservation, ...]:
    """5部位の隙間観測を `CLEARANCE_ITEM_NAMES` の順に作る。"""
    observations = []
    for index, name in enumerate(CLEARANCE_ITEM_NAMES):
        if measured is None:
            observations.append(
                ClearanceObservation(
                    name=name, measured_mm=None, design_mm=None, difference_mm=None
                )
            )
            continue
        design_mm = 10.0 + index
        measured_mm = measured[name]
        observations.append(
            ClearanceObservation(
                name=name,
                measured_mm=measured_mm,
                design_mm=design_mm,
                difference_mm=measured_mm - design_mm,
            )
        )
    return tuple(observations)


def _filled_clearances() -> tuple[ClearanceObservation, ...]:
    return _clearances(
        {name: 10.5 + index for index, name in enumerate(CLEARANCE_ITEM_NAMES)}
    )


def _checks(result: str = "pass", note: str = "手で確認した") -> tuple[AssemblyCheck, ...]:
    return tuple(
        AssemblyCheck(name=name, result=result, note=note)
        for name in REQUIRED_CHECK_NAMES
    )


def _provenance(default: Provenance = Provenance.MEASURED) -> dict[str, Provenance]:
    return {path: default for path in OBSERVATION_PATHS}


def _complete_record(**overrides: object) -> AssemblyRecord:
    """必須項目がすべて実測で埋まった記録（`is_assembly_complete` が真）。"""
    fields: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "mass_g": 2450.0,
        "cog_height_mm": 72.5,
        "cog_radial_offset_mm": 3.2,
        "cog_method": "3点支持で各脚の反力を測り、モーメントの釣り合いから求めた",
        "wheels": _wheels(),
        "representative_wheel_diameter_mm": _mean(_WHEEL_DIAMETERS_MM),
        "clearances": _filled_clearances(),
        "fit_deviations": (),
        "checks": _checks(),
        "provenance": _provenance(),
    }
    fields.update(overrides)
    return AssemblyRecord(**fields)  # type: ignore[arg-type]


def _blank_record(**overrides: object) -> AssemblyRecord:
    """全項目が未記入の記録（出荷される `measurements.json` と同じ形）。"""
    fields: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "mass_g": None,
        "cog_height_mm": None,
        "cog_radial_offset_mm": None,
        "cog_method": "",
        "wheels": _wheels(None),
        "representative_wheel_diameter_mm": None,
        "clearances": _clearances(),
        "fit_deviations": (),
        "checks": _checks(result="pending", note=""),
        "provenance": _provenance(Provenance.ASSUMED),
    }
    fields.update(overrides)
    return AssemblyRecord(**fields)  # type: ignore[arg-type]


def _attribute_docstring_targets(tree: ast.Module) -> set[int]:
    """モジュール直下の「代入の直後に置かれた文字列」（属性 docstring）を集める。"""
    targets: set[int] = set()
    previous_is_assign = False
    for statement in tree.body:
        if (
            previous_is_assign
            and isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
        ):
            targets.add(id(statement.value))
        previous_is_assign = isinstance(statement, (ast.Assign, ast.AnnAssign))
    return targets


def _executable_string_constants() -> list[str]:
    """docstring（モジュール・クラス・関数・属性）を除く文字列定数。"""
    attribute_docs = _attribute_docstring_targets(_ASSEMBLY_TREE)
    docstrings: set[int] = set()
    for node in ast.walk(_ASSEMBLY_TREE):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    return [
        node.value
        for node in ast.walk(_ASSEMBLY_TREE)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
        and id(node) not in attribute_docs
    ]


def _document(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_document(path: Path, document: object) -> Path:
    target = path / "measurements.json"
    target.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return target


def _shipped_document() -> dict:
    return _document(DEFAULT_MEASUREMENTS_PATH)


# ---------------------------------------------------------------------------
# 1. 出荷される空白の記録（tasks.md タスク 2.4 の ⚠️ 注記）
# ---------------------------------------------------------------------------


def test_blank_record_is_shipped_at_the_default_path() -> None:
    """観測記録の初期状態がリポジトリに存在する。"""
    assert DEFAULT_MEASUREMENTS_PATH.is_file(), (
        f"{DEFAULT_MEASUREMENTS_PATH} が無い。"
        "6群の実測が始まる前から読み込める初期状態を出荷すること"
    )


def test_blank_record_is_beside_but_not_part_of_the_design_input() -> None:
    """観測は設計入力と**別のファイル**である（design.md「Key Decisions」）。"""
    assert DEFAULT_MEASUREMENTS_PATH != DEFAULT_DIMENSIONS_PATH
    assert DEFAULT_MEASUREMENTS_PATH.parent == DEFAULT_DIMENSIONS_PATH.parent
    assert DEFAULT_MEASUREMENTS_PATH.name == "measurements.json"


def test_blank_record_loads() -> None:
    """⚠️ **全項目が未記入でも読み込みは成功する。**

    読み込み自体が失敗すると、組立前に他の検査を回せなくなる（tasks.md タスク 2.4）。
    """
    record = load_assembly_record()
    assert record.schema_version == SCHEMA_VERSION
    assert record.mass_g is None
    assert record.cog_method == ""


def test_blank_record_holds_every_part_and_wheel_slot() -> None:
    """空白でも5部位と3輪の枠は揃っている（枠の欠落と未記入は別物である）。"""
    record = load_assembly_record()
    assert tuple(item.name for item in record.clearances) == CLEARANCE_ITEM_NAMES
    assert tuple(wheel.index for wheel in record.wheels) == tuple(
        range(WHEEL_OBSERVATION_COUNT)
    )
    assert tuple(check.name for check in record.checks) == REQUIRED_CHECK_NAMES


def test_blank_record_provenance_covers_every_observation_path_as_assumed() -> None:
    """出所表は観測パスを過不足なく持ち、初期状態では全件が仮値である（要件 10.9）。"""
    record = load_assembly_record()
    assert set(record.provenance) == set(OBSERVATION_PATHS)
    assert set(record.provenance.values()) == {Provenance.ASSUMED}


def test_blank_record_lists_every_required_item_as_outstanding() -> None:
    """⚠️ **未記入の必須項目が全件列挙される**（tasks.md タスク 2.4 の完了状態）。"""
    outstanding = missing_observations(load_assembly_record())
    expected = (
        "mass_g: 未記入",
        "cog_height_mm: 未記入",
        "cog_radial_offset_mm: 未記入",
        "cog_method: 未記入",
        "wheels.0.effective_rolling_diameter_mm: 未記入",
        "wheels.0.method: 未記入",
        "wheels.1.effective_rolling_diameter_mm: 未記入",
        "wheels.1.method: 未記入",
        "wheels.2.effective_rolling_diameter_mm: 未記入",
        "wheels.2.method: 未記入",
        "representative_wheel_diameter_mm: 未記入",
        "clearances.motor_body.measured_mm: 未記入",
        "clearances.bracket.measured_mm: 未記入",
        "clearances.fastener.measured_mm: 未記入",
        "clearances.base_underside.measured_mm: 未記入",
        "clearances.cable.measured_mm: 未記入",
        "checks.hub_setscrew_no_slip: 未実施",
        "checks.wheel_bolt_circle: 未実施",
        "checks.stand_retention: 未実施",
    )
    assert outstanding == expected


def test_blank_record_is_not_complete() -> None:
    """空白の記録は完了ではない（読み込めることと完了であることは別である）。"""
    assert is_assembly_complete(load_assembly_record()) is False


def test_blank_record_round_trips_byte_for_byte(tmp_path: Path) -> None:
    """出荷される記録は `dump_assembly_record` の出力と1バイトも違わない。"""
    written = tmp_path / "measurements.json"
    dump_assembly_record(load_assembly_record(), written)
    assert written.read_bytes() == DEFAULT_MEASUREMENTS_PATH.read_bytes()


def test_shipped_record_uses_lf_line_endings() -> None:
    """記録の改行は LF である（`.gitattributes` の例外行と対で成立する）。"""
    raw = DEFAULT_MEASUREMENTS_PATH.read_bytes()
    assert b"\r" not in raw
    assert raw.endswith(b"\n")


def test_shipped_record_holds_exactly_the_designed_keys() -> None:
    """記録の最上位キーが design.md「Logical Data Model」の11件と一致する。"""
    assert sorted(_shipped_document()) == sorted(
        [
            "schema_version",
            "mass_g",
            "cog_height_mm",
            "cog_radial_offset_mm",
            "cog_method",
            "wheels",
            "representative_wheel_diameter_mm",
            "clearances",
            "fit_deviations",
            "checks",
            "provenance",
        ]
    )


# ---------------------------------------------------------------------------
# 2. 完了の判定（要件 9.8）
# ---------------------------------------------------------------------------


def test_complete_record_has_no_outstanding_item() -> None:
    """必須項目がすべて実測で埋まった記録は完了である。"""
    record = _complete_record()
    assert missing_observations(record) == ()
    assert is_assembly_complete(record) is True


def test_completion_is_exactly_the_empty_outstanding_list() -> None:
    """`is_assembly_complete(r)` と `missing_observations(r) == ()` は同値である。"""
    for record in (
        _blank_record(),
        _complete_record(),
        _complete_record(mass_g=None),
        _complete_record(checks=_checks(result="pending", note="")),
        _complete_record(provenance=_provenance(Provenance.ASSUMED)),
    ):
        assert is_assembly_complete(record) == (missing_observations(record) == ())


def test_is_assembly_complete_body_holds_no_extra_condition() -> None:
    """⚠️ `is_assembly_complete` の本体が判定式そのものであることを `ast` で固定する。

    余分な条件を足せる場所を残さないための構造的な錠である（要件 9.8:
    ⚠️ **判定にモータへ通電する項目を1つも置かない**）。
    """
    function = next(
        node
        for node in _ASSEMBLY_TREE.body
        if isinstance(node, ast.FunctionDef) and node.name == "is_assembly_complete"
    )
    body = [
        statement
        for statement in function.body
        if not (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
        )
    ]
    assert len(body) == 1, f"本体に判定式以外の文がある: {ast.dump(function)}"
    statement = body[0]
    assert isinstance(statement, ast.Return)
    compare = statement.value
    assert isinstance(compare, ast.Compare)
    assert isinstance(compare.left, ast.Call)
    assert isinstance(compare.left.func, ast.Name)
    assert compare.left.func.id == "missing_observations"
    assert len(compare.ops) == 1 and isinstance(compare.ops[0], ast.Eq)
    right = compare.comparators[0]
    assert isinstance(right, ast.Tuple) and right.elts == []


def test_no_required_item_mentions_energising_a_motor() -> None:
    """必須項目の識別子に通電・走行・エンコーダ校正の語が現れない（要件 9.8, 9.9）。"""
    identifiers = (
        *OBSERVATION_PATHS,
        *REQUIRED_CHECK_NAMES,
        *MEASUREMENT_METHODS,
        *CLEARANCE_ITEM_NAMES,
    )
    for identifier in identifiers:
        lowered = identifier.lower()
        for token in FORBIDDEN_COMPLETION_TOKENS:
            assert token not in lowered, (
                f"必須項目 {identifier!r} が {token!r} を含む。"
                "組立完了の判定はモータへ通電することなく行う（要件 9.8）"
            )


def test_outstanding_entries_never_mention_energising_a_motor() -> None:
    """列挙される未了項目のどれもモータを回す操作を要求しない。"""
    records = (
        _blank_record(),
        _complete_record(mass_g=None, provenance=_provenance(Provenance.ASSUMED)),
        _complete_record(checks=_checks(result="fail", note="滑った")),
    )
    for record in records:
        for entry in missing_observations(record):
            lowered = entry.lower()
            for token in FORBIDDEN_COMPLETION_TOKENS:
                assert token not in lowered, f"未了項目 {entry!r} が {token!r} を含む"


# ---------------------------------------------------------------------------
# 3. 代表値は3個の平均（design.md Invariants /「Revalidation Triggers」項目3）
# ---------------------------------------------------------------------------


def test_representative_equals_the_mean_of_three_wheels() -> None:
    """代表値は3個の平均である。"""
    record = _complete_record()
    assert record.representative_wheel_diameter_mm == pytest.approx(
        _mean(_WHEEL_DIAMETERS_MM)
    )


def test_representative_that_disagrees_with_the_mean_is_rejected() -> None:
    """⚠️ 平均と一致しない代表値は `ConsistencyError` で拒否する（二重管理の防止）。"""
    with pytest.raises(ConsistencyError) as excinfo:
        _complete_record(representative_wheel_diameter_mm=59.0)
    message = str(excinfo.value)
    assert "representative_wheel_diameter_mm" in message
    assert "59.0" in message


def test_representative_within_the_tolerance_is_accepted() -> None:
    """許容差の内側は受け入れる（丸め由来の差で正しい記録を落とさない）。"""
    mean = _mean(_WHEEL_DIAMETERS_MM)
    record = _complete_record(
        representative_wheel_diameter_mm=mean + DERIVED_ABS_TOLERANCE_MM * 0.9
    )
    assert is_assembly_complete(record) is True


def test_representative_just_outside_the_tolerance_is_rejected() -> None:
    """許容差の外側は拒否する（境界の両側を固定する）。"""
    mean = _mean(_WHEEL_DIAMETERS_MM)
    with pytest.raises(ConsistencyError):
        _complete_record(
            representative_wheel_diameter_mm=mean + DERIVED_ABS_TOLERANCE_MM * 10.0
        )


def test_representative_without_all_three_wheels_is_rejected() -> None:
    """3個そろわないうちに代表値だけを名乗る記録を拒否する（要件 10.3）。"""
    wheels = list(_wheels())
    wheels[2] = WheelObservation(
        index=2, effective_rolling_diameter_mm=None, method="", limitation_note=""
    )
    with pytest.raises(MeasurementError) as excinfo:
        _complete_record(wheels=tuple(wheels))
    assert "wheels.2" in str(excinfo.value)


def test_blank_representative_with_three_wheels_is_outstanding_not_rejected() -> None:
    """3個そろって代表値だけ未記入の途中状態は、拒否ではなく未了として扱う。"""
    record = _complete_record(representative_wheel_diameter_mm=None)
    assert "representative_wheel_diameter_mm: 未記入" in missing_observations(record)


def test_representative_rolling_radius_is_half_the_diameter() -> None:
    """実効転がり**半径**の唯一の出どころ（design.md `#### Layout` Implementation Notes）。"""
    record = _complete_record()
    assert representative_rolling_radius_mm(record) == pytest.approx(
        _mean(_WHEEL_DIAMETERS_MM) / 2.0
    )
    assert representative_rolling_radius_mm(_blank_record()) is None


def test_wheel_count_matches_the_shipped_wheel_count() -> None:
    """観測の輪数が寸法設定の輪数と食い違わない（片方だけ動けばここで落ちる）。"""
    assert WHEEL_OBSERVATION_COUNT == load_params().chassis.base.wheel_count


# ---------------------------------------------------------------------------
# 4. 転動を伴わない測定の限界（要件 10.5）
# ---------------------------------------------------------------------------


def test_static_loaded_measurement_requires_a_limitation_note() -> None:
    """⚠️ 転動を伴わない測定で限界の注記が空なら `MeasurementError`。"""
    with pytest.raises(MeasurementError) as excinfo:
        WheelObservation(
            index=0,
            effective_rolling_diameter_mm=59.2,
            method=STATIC_LOADED_METHOD,
            limitation_note="",
        )
    message = str(excinfo.value)
    assert "limitation_note" in message
    assert STATIC_LOADED_METHOD in message


def test_whitespace_only_limitation_note_is_not_a_limitation() -> None:
    """空白だけの注記は記述ではない。"""
    with pytest.raises(MeasurementError):
        WheelObservation(
            index=0,
            effective_rolling_diameter_mm=59.2,
            method=STATIC_LOADED_METHOD,
            limitation_note="   ",
        )


def test_static_loaded_measurement_with_a_limitation_note_is_accepted() -> None:
    """限界が書かれていれば静的測定も受け入れる。"""
    wheel = WheelObservation(
        index=0,
        effective_rolling_diameter_mm=59.2,
        method=STATIC_LOADED_METHOD,
        limitation_note="ノギスによる静止時の測定であり、転動時の実効半径は捉えない",
    )
    assert wheel.method in NON_ROLLING_METHODS


def test_rolling_measurement_needs_no_limitation_note() -> None:
    """転動を伴う測定に注記は要らない。"""
    wheel = WheelObservation(
        index=0,
        effective_rolling_diameter_mm=59.2,
        method=ROLLING_METHOD,
        limitation_note="",
    )
    assert wheel.method not in NON_ROLLING_METHODS


def test_unknown_measurement_method_is_rejected() -> None:
    """測定方法は既知の2種類（と未記入）に限る。"""
    with pytest.raises(ParameterError) as excinfo:
        WheelObservation(
            index=0,
            effective_rolling_diameter_mm=59.2,
            method="guessed",
            limitation_note="",
        )
    assert "guessed" in str(excinfo.value)


def test_diameter_and_method_must_be_filled_together() -> None:
    """値だけ・方法だけの半端な記録を拒否する（値は手順とともにしか意味を持たない）。"""
    with pytest.raises(ParameterError):
        WheelObservation(
            index=0,
            effective_rolling_diameter_mm=59.2,
            method="",
            limitation_note="",
        )
    with pytest.raises(ParameterError):
        WheelObservation(
            index=0,
            effective_rolling_diameter_mm=None,
            method=ROLLING_METHOD,
            limitation_note="",
        )


def test_load_rejects_a_static_measurement_without_a_limitation_note(
    tmp_path: Path,
) -> None:
    """ファイル経由でも同じ規則が働く（型の検証を読み込みが素通ししない）。"""
    document = _shipped_document()
    document["wheels"][0]["effective_rolling_diameter_mm"] = 59.2
    document["wheels"][0]["method"] = STATIC_LOADED_METHOD
    target = _write_document(tmp_path, document)
    with pytest.raises(MeasurementError):
        load_assembly_record(target)


# ---------------------------------------------------------------------------
# 5. 隙間の部位は算出側と一致する（design.md `#### Assembly` Integration）
# ---------------------------------------------------------------------------


def test_clearance_observation_names_match_the_calculation_side() -> None:
    """5部位の名と並びが `clearance.CLEARANCE_ITEM_NAMES` と一致する。"""
    record = _complete_record()
    assert tuple(item.name for item in record.clearances) == CLEARANCE_ITEM_NAMES


def test_a_record_missing_one_clearance_part_is_rejected() -> None:
    """⚠️ 部位が1つ欠けた記録を拒否する（そして完了とも呼ばない）。"""
    with pytest.raises(MeasurementError) as excinfo:
        _complete_record(clearances=_filled_clearances()[:-1])
    message = str(excinfo.value)
    assert CLEARANCE_ITEM_NAMES[-1] in message


def test_a_record_naming_an_unknown_clearance_part_is_rejected() -> None:
    """算出側に無い部位名を拒否する。"""
    with pytest.raises(MeasurementError) as excinfo:
        ClearanceObservation(
            name="chassis_edge", measured_mm=10.0, design_mm=10.0, difference_mm=0.0
        )
    assert "chassis_edge" in str(excinfo.value)


def test_clearance_parts_out_of_order_are_rejected() -> None:
    """並びも固定する（一覧の順序が入れ替われば差分として読めない）。"""
    filled = _filled_clearances()
    shuffled = (filled[1], filled[0], *filled[2:])
    with pytest.raises(MeasurementError):
        _complete_record(clearances=shuffled)


def test_clearance_difference_must_equal_measured_minus_design() -> None:
    """差は実測と設計値から一意に決まる（同じ量を2箇所で管理しない、要件 4.5）。"""
    with pytest.raises(ConsistencyError) as excinfo:
        ClearanceObservation(
            name=CLEARANCE_ITEM_NAMES[0],
            measured_mm=10.5,
            design_mm=10.0,
            difference_mm=1.0,
        )
    assert "difference_mm" in str(excinfo.value)


def test_clearance_entry_is_all_blank_or_all_filled() -> None:
    """半端に埋まった隙間観測を拒否する。"""
    with pytest.raises(ParameterError):
        ClearanceObservation(
            name=CLEARANCE_ITEM_NAMES[0],
            measured_mm=10.5,
            design_mm=None,
            difference_mm=None,
        )


def test_the_module_never_spells_out_a_clearance_part_name() -> None:
    """⚠️ **部位名を書き写していない**ことを固定する（`clearance` が唯一の正）。

    `clearance.py` へ部位を1つ足したときに、観測側の一覧が黙って古いままになる
    経路を塞ぐ。書き写しがあれば、その文字列がここで見つかる。
    """
    literals = _executable_string_constants()
    for name in CLEARANCE_ITEM_NAMES:
        assert name not in literals, (
            f"assembly.py が部位名 {name!r} を文字列として持っている。"
            "5部位は clearance.CLEARANCE_ITEM_NAMES から導くこと"
        )


def test_observation_paths_are_derived_from_the_clearance_item_names() -> None:
    """観測パスが5部位と3輪から導かれている。"""
    for name in CLEARANCE_ITEM_NAMES:
        assert f"clearances.{name}.measured_mm" in OBSERVATION_PATHS
    for index in range(WHEEL_OBSERVATION_COUNT):
        assert f"wheels.{index}.effective_rolling_diameter_mm" in OBSERVATION_PATHS
    assert len(OBSERVATION_PATHS) == len(set(OBSERVATION_PATHS))


# ---------------------------------------------------------------------------
# 6. 未記入と仮値（要件 10.9 / design.md Risks）
# ---------------------------------------------------------------------------


def test_a_value_left_at_an_estimate_is_listed_as_provisional() -> None:
    """⚠️ 出所が `assumed` のままの必須項目を拾う（概算のまま完了と呼ばせない）。"""
    provenance = _provenance()
    provenance["mass_g"] = Provenance.ASSUMED
    record = _complete_record(provenance=provenance)
    outstanding = missing_observations(record)
    assert outstanding == ("mass_g: 仮値（出所が assumed のまま）",)
    assert is_assembly_complete(record) is False


def test_blank_and_provisional_are_distinguishable_in_the_listing() -> None:
    """未記入と仮値を利用側が読み分けられる。"""
    provenance = _provenance()
    provenance["cog_height_mm"] = Provenance.ASSUMED
    record = _complete_record(mass_g=None, provenance=provenance)
    outstanding = missing_observations(record)
    assert "mass_g: 未記入" in outstanding
    assert "cog_height_mm: 仮値（出所が assumed のまま）" in outstanding


def test_a_blank_value_is_reported_once_not_twice() -> None:
    """未記入の項目を「未記入」と「仮値」で二重に数えない。"""
    record = _blank_record()
    paths = [entry.split(":")[0] for entry in missing_observations(record)]
    assert len(paths) == len(set(paths))


# ---------------------------------------------------------------------------
# 7. 手による確認と組立時の差分（要件 9.5, 9.6, 9.7, 5.7）
# ---------------------------------------------------------------------------


def test_required_checks_are_the_three_named_by_the_design() -> None:
    """手による確認は design.md「組立手順」が名指しする3件である。"""
    assert REQUIRED_CHECK_NAMES == (
        "hub_setscrew_no_slip",
        "wheel_bolt_circle",
        "stand_retention",
    )


def test_a_pending_check_blocks_completion() -> None:
    """未実施の確認があれば完了ではない。"""
    checks = (
        AssemblyCheck(name=REQUIRED_CHECK_NAMES[0], result="pending", note=""),
        *_checks()[1:],
    )
    record = _complete_record(checks=checks)
    assert missing_observations(record) == ("checks.hub_setscrew_no_slip: 未実施",)


def test_a_failed_check_blocks_completion() -> None:
    """不合格の確認があれば完了ではない（合否を黙って読み飛ばさない）。"""
    checks = (
        AssemblyCheck(name=REQUIRED_CHECK_NAMES[0], result="fail", note="ハブが滑った"),
        *_checks()[1:],
    )
    record = _complete_record(checks=checks)
    assert missing_observations(record) == ("checks.hub_setscrew_no_slip: 不合格",)


def test_a_failed_check_must_say_what_failed() -> None:
    """不合格に注記が無ければ現物へ戻れない。"""
    with pytest.raises(MeasurementError):
        AssemblyCheck(name=REQUIRED_CHECK_NAMES[0], result="fail", note="")


def test_unknown_check_name_or_result_is_rejected() -> None:
    """確認の名と結果は既知の集合に限る。"""
    with pytest.raises(MeasurementError):
        AssemblyCheck(name="powered_spin", result="pass", note="")
    with pytest.raises(ParameterError):
        AssemblyCheck(name=REQUIRED_CHECK_NAMES[0], result="maybe", note="")


def test_a_missing_check_is_rejected() -> None:
    """確認が1件でも欠けた記録を拒否する。"""
    with pytest.raises(MeasurementError):
        _complete_record(checks=_checks()[:-1])


def test_an_unreflected_fit_deviation_blocks_completion() -> None:
    """⚠️ 差分を記録しただけで寸法パラメータへ反映していない状態は完了ではない（要件 9.7）。"""
    deviation = FitDeviation(
        location="arm_bolt_hole",
        design_mm=3.2,
        actual_mm=3.05,
        reflected_in_parameters=False,
    )
    record = _complete_record(fit_deviations=(deviation,))
    assert missing_observations(record) == (
        "fit_deviations.0（arm_bolt_hole）: 寸法パラメータへ未反映",
    )


def test_a_reflected_fit_deviation_does_not_block_completion() -> None:
    """反映済みの差分は完了を妨げない。"""
    deviation = FitDeviation(
        location="arm_bolt_hole",
        design_mm=3.2,
        actual_mm=3.05,
        reflected_in_parameters=True,
    )
    assert is_assembly_complete(_complete_record(fit_deviations=(deviation,))) is True


def test_no_fit_deviation_is_a_legitimate_state() -> None:
    """差分が1件も無いことは正当な状態である（見つかったものを記録する項目である）。"""
    assert is_assembly_complete(_complete_record(fit_deviations=())) is True


# ---------------------------------------------------------------------------
# 8. パラメータ識別子は観測で動かない（design.md `#### Baseline` / タスク 1.4）
# ---------------------------------------------------------------------------


def test_parameters_digest_is_pinned_to_the_same_literal_as_the_config_test() -> None:
    """⚠️ 観測記録を足しても識別子は動かない（`test_chassis_config.py` と同じ錨）。"""
    assert parameters_digest(load_params().chassis) == PINNED_PARAMETERS_DIGEST


def test_parameters_digest_does_not_move_when_an_observation_is_written(
    tmp_path: Path,
) -> None:
    """観測を書き込んでも識別子は動かない（観測が形状の再生成を要求しない）。"""
    dimensions = tmp_path / "dimensions.json"
    dimensions.write_text(
        DEFAULT_DIMENSIONS_PATH.read_text(encoding="utf-8"),
        encoding="utf-8",
        newline="\n",
    )
    before = parameters_digest(load_params(dimensions).chassis)
    dump_assembly_record(_complete_record(), tmp_path / "measurements.json")
    after = parameters_digest(load_params(dimensions).chassis)
    assert before == after == PINNED_PARAMETERS_DIGEST


def test_parameters_digest_still_takes_only_the_design_input() -> None:
    """識別子は `ChassisParams` だけの純関数のままである（観測を入力にしない）。"""
    assert list(inspect.signature(parameters_digest).parameters) == ["params"]


# ---------------------------------------------------------------------------
# 9. 読み書きの往復と、あらゆる階層での拒否
# ---------------------------------------------------------------------------


def test_round_trip_preserves_a_complete_record(tmp_path: Path) -> None:
    """書き出して読み戻すと同じ記録になる。"""
    target = tmp_path / "measurements.json"
    record = _complete_record(
        fit_deviations=(
            FitDeviation(
                location="arm_bolt_hole",
                design_mm=3.2,
                actual_mm=3.05,
                reflected_in_parameters=True,
            ),
        ),
        wheels=_wheels(
            method=STATIC_LOADED_METHOD,
            limitation_note="静止時の測定であり転動時の実効半径は捉えない",
        ),
    )
    dump_assembly_record(record, target)
    assert load_assembly_record(target) == record


def test_dump_writes_lf_and_sorted_keys(tmp_path: Path) -> None:
    """整形は `config.dump_params` に揃える（LF・キー整列・末尾改行）。"""
    target = tmp_path / "measurements.json"
    dump_assembly_record(_complete_record(), target)
    raw = target.read_bytes()
    assert b"\r" not in raw
    assert raw.endswith(b"\n")
    text = target.read_text(encoding="utf-8")
    assert list(json.loads(text)) == sorted(json.loads(text))


def test_missing_file_raises_measurement_error(tmp_path: Path) -> None:
    """記録が存在しない場合は `MeasurementError`（design.md Preconditions）。"""
    with pytest.raises(MeasurementError) as excinfo:
        load_assembly_record(tmp_path / "absent.json")
    assert "absent.json" in str(excinfo.value)


def test_unknown_top_level_key_is_rejected(tmp_path: Path) -> None:
    """未知キーを最上位で拒否する。"""
    document = _shipped_document()
    document["torque_nm"] = 1.0
    with pytest.raises(ParameterError) as excinfo:
        load_assembly_record(_write_document(tmp_path, document))
    assert "torque_nm" in str(excinfo.value)


def test_missing_top_level_key_is_rejected(tmp_path: Path) -> None:
    """⚠️ 欠けている項目を既定値で埋めない。"""
    document = _shipped_document()
    del document["mass_g"]
    with pytest.raises(ParameterError) as excinfo:
        load_assembly_record(_write_document(tmp_path, document))
    assert "mass_g" in str(excinfo.value)


def test_unknown_nested_key_is_rejected(tmp_path: Path) -> None:
    """未知キーを入れ子の階層でも拒否する。"""
    document = _shipped_document()
    document["wheels"][0]["tread_depth_mm"] = 1.0
    with pytest.raises(ParameterError) as excinfo:
        load_assembly_record(_write_document(tmp_path, document))
    assert "tread_depth_mm" in str(excinfo.value)


def test_unknown_provenance_path_is_rejected(tmp_path: Path) -> None:
    """出所表のキーは観測パスと一致する。"""
    document = _shipped_document()
    document["provenance"]["mass_kg"] = "assumed"
    with pytest.raises(ParameterError) as excinfo:
        load_assembly_record(_write_document(tmp_path, document))
    assert "mass_kg" in str(excinfo.value)


def test_missing_provenance_path_is_rejected(tmp_path: Path) -> None:
    """出所の無い観測項目を許さない（要件 10.9）。"""
    document = _shipped_document()
    del document["provenance"]["mass_g"]
    with pytest.raises(ParameterError) as excinfo:
        load_assembly_record(_write_document(tmp_path, document))
    assert "mass_g" in str(excinfo.value)


def test_unknown_provenance_value_is_rejected(tmp_path: Path) -> None:
    """出所は実測と仮値の2値ちょうどである。"""
    document = _shipped_document()
    document["provenance"]["mass_g"] = "derived"
    with pytest.raises(ParameterError) as excinfo:
        load_assembly_record(_write_document(tmp_path, document))
    assert "derived" in str(excinfo.value)


def test_wrong_schema_version_is_rejected(tmp_path: Path) -> None:
    """版が違う記録を黙って読まない。"""
    document = _shipped_document()
    document["schema_version"] = "0.9"
    with pytest.raises(ParameterError) as excinfo:
        load_assembly_record(_write_document(tmp_path, document))
    assert "0.9" in str(excinfo.value)


def test_non_numeric_observation_is_rejected(tmp_path: Path) -> None:
    """型不正を項目名付きで拒否する。"""
    document = _shipped_document()
    document["mass_g"] = "2450"
    with pytest.raises(ParameterError) as excinfo:
        load_assembly_record(_write_document(tmp_path, document))
    assert "mass_g" in str(excinfo.value)


def test_non_positive_mass_is_rejected(tmp_path: Path) -> None:
    """あり得ない値を拒否する。"""
    document = _shipped_document()
    document["mass_g"] = 0.0
    with pytest.raises(ParameterError):
        load_assembly_record(_write_document(tmp_path, document))


def test_json_that_is_not_an_object_is_rejected(tmp_path: Path) -> None:
    """記録はオブジェクトである。"""
    target = tmp_path / "measurements.json"
    target.write_text("[]", encoding="utf-8", newline="\n")
    with pytest.raises(ParameterError):
        load_assembly_record(target)


def test_dump_overwrites_an_existing_file(tmp_path: Path) -> None:
    """既存ファイルへの書き出しが残骸を残さない。"""
    target = tmp_path / "measurements.json"
    target.write_text("x" * 50_000, encoding="utf-8", newline="\n")
    dump_assembly_record(_complete_record(), target)
    assert load_assembly_record(target) == _complete_record()


def test_record_is_frozen() -> None:
    """記録は不変である（読み込み後に中身が差し替わらない）。"""
    record = _complete_record()
    with pytest.raises(Exception):
        record.mass_g = 1.0  # type: ignore[misc]


def test_provenance_mapping_is_copied() -> None:
    """出所表は呼び出し側の辞書とエイリアスを切る。"""
    provenance = _provenance()
    record = _complete_record(provenance=provenance)
    provenance["mass_g"] = Provenance.ASSUMED
    assert record.provenance["mass_g"] is Provenance.MEASURED


def test_replace_keeps_the_invariants() -> None:
    """`dataclasses.replace` でも不変条件が働く（構築の経路を1つに保つ）。"""
    record = _complete_record()
    with pytest.raises(ConsistencyError):
        replace(record, representative_wheel_diameter_mm=1.0)


# ---------------------------------------------------------------------------
# 10. 境界（design.md「Dependency Direction」）
# ---------------------------------------------------------------------------


def test_assembly_reaches_provenance_through_this_package_not_upstream() -> None:
    """⚠️ `assembly` は上流を import してよいモジュールではない。

    出所の型は `chassis_mechanism.params` 経由で到達する
    （`test_chassis_boundaries.py` の `UPSTREAM_IMPORT_ALLOWED_MODULES` は
    `assembly` を含まない）。
    """
    imported = {
        node.module
        for node in ast.walk(_ASSEMBLY_TREE)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not any(module.split(".")[0] == "catch_mechanism" for module in imported)
    assert "chassis_mechanism.params" in imported
    assert assembly_module.Provenance is Provenance


def test_assembly_does_not_import_rightward_modules() -> None:
    """`assembly` は自身より右の層を import しない（`baseline` / `shapes` / `cli`）。"""
    imported = {
        node.module
        for node in ast.walk(_ASSEMBLY_TREE)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    forbidden = {
        "chassis_mechanism.baseline",
        "chassis_mechanism.shapes",
        "chassis_mechanism.export",
        "chassis_mechanism.cli",
    }
    assert imported & forbidden == set()
