"""位置許容誤差の導出と導出記録の直列化（タスク 2.3、要件 7.1, 7.2, 7.3, 7.4, 7.9）。

本ファイルが固定するのは design.md「Tolerance」の Responsibilities /
Preconditions / Postconditions / Invariants と、tasks.md タスク 2.3 の
「観測可能な完了状態」である。

1. **導出は「開口内半径 − 対象物の代表寸法の半分」だけ**であること（要件 7.1）。
   公称 φ220・φ65 に対して 77.5mm になり、その出所が**仮値**であること
2. ⚠️ **外向きに張り出す部分の寸法を算入しない**こと（要件 7.2）。
   `rim.flange_width_mm` を変えても導出結果が1バイトも動かないこと。
   「入った」と「キャッチできた」は別問題であり、許容誤差は**保持まで成立する
   内径**で決まる——フランジを広げれば許容誤差が増える実装は、缶が受け口を
   すり抜けても成功と数える
3. **入力の値と出所・導出式・前提が導出結果に併記される**こと（要件 7.3, 7.9）
4. **出所は入力の最弱を継承する**こと（要件 7.4）。⚠️ すべての入力が実測の
   ときに**限り**実測を名乗る
5. **記録の書き出しと読み戻しで内容が保存される**こと。記録は導出の写しで
   あって独立に編集してよい自由記述ではないため、読み戻しは値と入力の整合・
   出所の継承・前提の明示を**そのつど検査する**

⚠️ **本ファイルは `dimensions.json` の値を固定しない。** 公称 φ220・φ65 は
テスト側で明示的に組み立てた入力であり、出荷される寸法設定を読み取った値では
ない。出荷値そのもののピン留めはタスク 1.4（`_Boundary: Config_`、
`test_catch_config.py`）が持つ。同様に、**出荷される導出記録が出荷される寸法と
食い違っていないか**の照合はここに置かない——tasks.md タスク 5.3
（`_Boundary: Tolerance_`, `_Depends: 5.2_`）の完了状態が「再導出した値と記録が
一致することをテストで固定する」と明示的に要求しており、そこの成果物である。
ここへ前倒しすると、採寸値を書き戻すタスク 5.2（`_Boundary: Config_`）が
**自らの境界の外にある赤**を踏むことになり、「全テストが通る」という 5.2 の
完了状態を 5.2 自身の権限では満たせなくなる。

ファイル名について: design.md「Directory Structure」の名からは離れるが、
`tests/` に `__init__.py` が無くテストモジュール名がセッション全体でフラット
であるため、`test_catch_*.py` の接頭辞に倣う（tasks.md「Implementation Notes」）。
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from catch_mechanism.config import SCHEMA_VERSION, load_params
from catch_mechanism.errors import CatchMechanismError, ParameterError
from catch_mechanism.params import PARAMETER_PATHS, MechanismParams, Provenance
from catch_mechanism.tolerance import (
    ASSUMPTIONS,
    DEFAULT_DERIVATION_PATH,
    FORMULA,
    OBJECT_DIAMETER_PATH,
    OPENING_DIAMETER_PATH,
    ToleranceDerivation,
    ToleranceInput,
    derive_position_tolerance,
    dump_derivation,
    load_derivation,
)

# ---------------------------------------------------------------------------
# ヘルパ
#
# 寸法表をテスト側で手書きしない。土台は出荷される `dimensions.json` を読み、
# 導出に効く項目だけを明示的に差し替える（値の二重管理を避けるため。要件 1.8）。
# ⚠️ **導出に効く項目は必ず明示する。** 出荷値をそのまま期待値に使うと、採寸で
# 値が動いたとき（タスク 5.2、`_Boundary: Config_`）に Tolerance 側のテストが
# 落ち、5.2 が自らの境界内で直せない赤になる。
# ---------------------------------------------------------------------------

NOMINAL_OPENING_MM = 220.0
"""公称の開口内径（mm）。第一候補の公称値であり**仮値**である。"""

NOMINAL_OBJECT_MM = 65.0
"""公称の対象物径（mm）。M1 の実験条件である空き缶（350ml 缶相当）の仮値。"""

NOMINAL_TOLERANCE_MM = 77.5
"""公称値に対する位置許容誤差（mm）＝ 220/2 − 65/2（タスク 2.3 観測可能な完了状態）。"""


def _shipped() -> MechanismParams:
    """出荷される寸法設定から `MechanismParams` を組み立てる（差し替えの土台）。"""
    return load_params()


def _with_rim(params: MechanismParams, **overrides: float) -> MechanismParams:
    """受け口（外向きに張り出す側）の寸法だけを差し替える。"""
    return replace(params, rim=replace(params.rim, **overrides))


def _with_diameters(
    params: MechanismParams,
    *,
    opening_mm: float | None = None,
    object_mm: float | None = None,
) -> MechanismParams:
    """導出に効く2つの径だけを差し替える。"""
    trash_can = params.trash_can
    if opening_mm is not None:
        trash_can = replace(trash_can, opening_inner_diameter_mm=opening_mm)
    target = params.target_object
    if object_mm is not None:
        target = replace(target, diameter_mm=object_mm)
    return replace(params, trash_can=trash_can, target_object=target)


def _with_provenance(
    params: MechanismParams, table: dict[str, Provenance]
) -> MechanismParams:
    """出所表そのものを差し替える。"""
    return replace(params, provenance=table)


def _nominal() -> MechanismParams:
    """公称 φ220・φ65 を**明示的に**与え、出所表を空にした寸法パラメータ。

    出所表が空であることは「1件も実測を明示していない」——すなわち導出に効く
    2値がいずれも仮値である——という状態そのものである（design.md「Logical Data
    Model」: 表に現れないパスは `ASSUMED`）。⚠️ 出荷される `dimensions.json` は
    土台としてのみ用い、**導出に効く値と出所はここで上書きする**。
    """
    params = _with_diameters(
        _shipped(), opening_mm=NOMINAL_OPENING_MM, object_mm=NOMINAL_OBJECT_MM
    )
    return _with_provenance(params, {})


def _document(tmp_path: Path) -> dict[str, Any]:
    """公称値からの導出記録を素の辞書として組み立てる（改変用の土台）。

    ⚠️ 土台を**出荷される記録**から取らないのは、拒否系テストが
    `configs/catch_mechanism/catch-opening.json` の中身に依存すると、記録が
    更新された（タスク 5.3）ときに拒否の検査まで巻き添えで落ちるためである。
    ここでは書き出しの実装そのものを土台にする。
    """
    source = tmp_path / "source.json"
    dump_derivation(derive_position_tolerance(_nominal()), source)
    return json.loads(source.read_text(encoding="utf-8"))


def _write(tmp_path: Path, document: object, *, name: str = "catch-opening.json") -> Path:
    """`document` を JSON として書き出し、そのパスを返す。"""
    target = tmp_path / name
    text = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)
    target.write_text(f"{text}\n", encoding="utf-8", newline="\n")
    return target


# ---------------------------------------------------------------------------
# 導出そのもの（要件 7.1）
# ---------------------------------------------------------------------------


def test_nominal_derivation_is_half_the_diameter_difference() -> None:
    """公称 φ220・φ65 に対する導出値が 77.5mm である（タスク 2.3 観測可能な完了状態）。

    ⚠️ この値は `trajectory_sim` の暫定値 67.5 とは異なる。暫定値は開口寸法が
    確定していない時期の仮置きであり、置き換えはタスク 5.4 が担う。
    """
    derivation = derive_position_tolerance(_nominal())

    assert derivation.position_tolerance_mm == NOMINAL_TOLERANCE_MM
    assert derivation.position_tolerance_mm == 77.5


def test_nominal_derivation_provenance_is_assumed() -> None:
    """公称値は実測を明示していないため導出の出所は仮値である（要件 7.4）。

    タスク 2.3 の観測可能な完了状態「その出所が仮値になること」。
    """
    assert derive_position_tolerance(_nominal()).provenance is Provenance.ASSUMED


@pytest.mark.parametrize(
    ("opening_mm", "object_mm", "expected_mm"),
    [
        (220.0, 65.0, 77.5),
        (240.0, 65.0, 87.5),
        (220.0, 100.0, 60.0),
        (200.0, 66.0, 67.0),
    ],
)
def test_derivation_is_the_half_difference_for_any_input(
    opening_mm: float, object_mm: float, expected_mm: float
) -> None:
    """導出は「開口内半径 − 対象物半径」以外の何物でもない（要件 7.1）。"""
    params = _with_diameters(_shipped(), opening_mm=opening_mm, object_mm=object_mm)

    assert derive_position_tolerance(params).position_tolerance_mm == expected_mm


def test_derived_value_is_positive() -> None:
    """導出値は正である（design.md「Tolerance」Postconditions）。"""
    assert derive_position_tolerance(_nominal()).position_tolerance_mm > 0.0


# ---------------------------------------------------------------------------
# ⚠️ 外向きに張り出す部分を算入しない（要件 7.2 / タスク 2.3 観測可能な完了状態）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("flange_width_mm", [0.5, 30.0, 120.0])
def test_flange_width_does_not_move_the_derivation(flange_width_mm: float) -> None:
    """⚠️ フランジ幅を変えても導出結果が**まったく**変わらない（要件 7.2）。

    値だけでなく出所・入力・式・前提まで一致することを見る。フランジ幅が入力の
    一覧へ現れるだけでも、「外向きの張り出しが許容誤差の根拠に入っている」と
    記録が主張してしまう。
    """
    baseline = _shipped()
    widened = _with_rim(baseline, flange_width_mm=flange_width_mm)

    assert derive_position_tolerance(widened) == derive_position_tolerance(baseline)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("fit_clearance_mm", 5.0),
        ("wall_thickness_mm", 12.0),
        ("flange_slope_deg", 45.0),
        ("height_mm", 60.0),
    ],
)
def test_no_rim_parameter_moves_the_derivation(field_name: str, value: float) -> None:
    """受け口の寸法はどれ1つとして導出に参加しない（要件 7.2）。

    ⚠️ `fit_clearance_mm` と `wall_thickness_mm` は**取り付け部**の寸法であり、
    ゴミ箱本体の外側に被さる。要件 8.2 が「受け口はゴミ箱本体の開口内径を
    狭めない」ことを別途要求しているため、保持が成立する内径は本体の開口内径
    そのものである。`flange_slope_deg` / `height_mm` も同様に内径を作らない。
    """
    baseline = _shipped()
    changed = _with_rim(baseline, **{field_name: value})

    assert derive_position_tolerance(changed) == derive_position_tolerance(baseline)


def test_inputs_do_not_mention_any_rim_parameter() -> None:
    """入力の一覧にも導出式にも受け口の寸法が現れない（要件 7.2, 7.3）。"""
    derivation = derive_position_tolerance(_shipped())

    names = {entry.name for entry in derivation.inputs}
    assert names == {OPENING_DIAMETER_PATH, OBJECT_DIAMETER_PATH}
    assert not any(name.startswith("rim.") for name in names)
    assert "rim." not in derivation.formula
    assert "flange" not in derivation.formula


# ---------------------------------------------------------------------------
# 入力の値と出所・式・前提の併記（要件 7.3, 7.9）
# ---------------------------------------------------------------------------


def test_inputs_carry_value_and_provenance_of_both_diameters() -> None:
    """導出に用いた各入力の値と出所が併記される（要件 7.3）。

    並びは式の順（開口内径 → 対象物径）である。
    """
    derivation = derive_position_tolerance(_nominal())

    assert derivation.inputs == (
        ToleranceInput(
            name=OPENING_DIAMETER_PATH,
            value_mm=NOMINAL_OPENING_MM,
            provenance=Provenance.ASSUMED,
        ),
        ToleranceInput(
            name=OBJECT_DIAMETER_PATH,
            value_mm=NOMINAL_OBJECT_MM,
            provenance=Provenance.ASSUMED,
        ),
    )


def test_input_names_are_parameter_paths() -> None:
    """入力名は `PARAMETER_PATHS` のパスであり、単一の正へ辿れる（要件 7.3）。"""
    for entry in derive_position_tolerance(_shipped()).inputs:
        assert entry.name in PARAMETER_PATHS


def test_formula_is_recorded_and_names_only_its_two_inputs() -> None:
    """導出式が文字列として記録される（要件 7.3）。"""
    derivation = derive_position_tolerance(_shipped())

    assert derivation.formula == FORMULA
    assert OPENING_DIAMETER_PATH in derivation.formula
    assert OBJECT_DIAMETER_PATH in derivation.formula


def test_assumptions_record_the_can_and_the_non_inclusion() -> None:
    """前提（M1 の空き缶・外向き部分の非算入）が記録される（要件 7.2, 7.9）。"""
    derivation = derive_position_tolerance(_shipped())

    assert derivation.assumptions
    assert set(ASSUMPTIONS) <= set(derivation.assumptions)
    joined = "".join(derivation.assumptions)
    assert "空き缶" in joined
    assert "外向き" in joined


# ---------------------------------------------------------------------------
# 出所の継承（要件 7.4 / design.md「Tolerance」Invariants）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("opening", "target", "expected"),
    [
        (Provenance.MEASURED, Provenance.MEASURED, Provenance.MEASURED),
        (Provenance.MEASURED, Provenance.ASSUMED, Provenance.ASSUMED),
        (Provenance.ASSUMED, Provenance.MEASURED, Provenance.ASSUMED),
        (Provenance.ASSUMED, Provenance.ASSUMED, Provenance.ASSUMED),
    ],
)
def test_provenance_inherits_the_weakest_input(
    opening: Provenance, target: Provenance, expected: Provenance
) -> None:
    """すべての入力が実測のときに**限り**実測を名乗る（要件 7.4）。"""
    params = _with_provenance(
        _shipped(),
        {OPENING_DIAMETER_PATH: opening, OBJECT_DIAMETER_PATH: target},
    )
    derivation = derive_position_tolerance(params)

    assert derivation.provenance is expected
    assert derivation.inputs[0].provenance is opening
    assert derivation.inputs[1].provenance is target


def test_paths_absent_from_the_table_are_assumed() -> None:
    """出所表に現れないパスは仮値として扱う（design.md「Logical Data Model」）。

    ⚠️ 開口内径だけを実測として明示しても、対象物径が表から漏れていれば
    導出は実測を名乗らない——「実測を名乗るには明示が要る」。
    """
    params = _with_provenance(_shipped(), {OPENING_DIAMETER_PATH: Provenance.MEASURED})

    assert derive_position_tolerance(params).provenance is Provenance.ASSUMED


def test_measured_provenance_requires_both_inputs_measured() -> None:
    """双方を実測として明示したときに実測が名乗れる（要件 7.4）。"""
    params = _with_provenance(
        _shipped(),
        {
            OPENING_DIAMETER_PATH: Provenance.MEASURED,
            OBJECT_DIAMETER_PATH: Provenance.MEASURED,
        },
    )

    assert derive_position_tolerance(params).provenance is Provenance.MEASURED


# ---------------------------------------------------------------------------
# 前提条件（design.md「Tolerance」Preconditions）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("object_mm", [220.0, 300.0])
def test_object_not_smaller_than_the_opening_is_rejected(object_mm: float) -> None:
    """対象物が開口内径以上であれば導出そのものが成立しない。

    許容誤差 0 以下は「入る余地が無い」という状態であり、値として返せば
    下流はそれを「厳しい合否条件」として受け取ってしまう。
    """
    params = _with_diameters(_nominal(), object_mm=object_mm)

    with pytest.raises(ParameterError) as excinfo:
        derive_position_tolerance(params)

    message = str(excinfo.value)
    assert OPENING_DIAMETER_PATH in message
    assert OBJECT_DIAMETER_PATH in message
    assert repr(object_mm) in message


def test_precondition_failure_is_catchable_as_value_error() -> None:
    """例外階層は基底例外としても `ValueError` としても捕捉できる（タスク 1.2）。"""
    params = _with_diameters(_nominal(), object_mm=300.0)

    with pytest.raises(CatchMechanismError):
        derive_position_tolerance(params)
    with pytest.raises(ValueError):
        derive_position_tolerance(params)


# ---------------------------------------------------------------------------
# 記録の書き出しと読み戻し
# ---------------------------------------------------------------------------


def test_dump_then_load_round_trips(tmp_path: Path) -> None:
    """書き出して読み戻すと、値・出所・入力・式・前提が保存される。"""
    derivation = derive_position_tolerance(_shipped())
    target = tmp_path / "catch-opening.json"

    dump_derivation(derivation, target)

    assert load_derivation(target) == derivation


def test_round_trip_preserves_assumptions_verbatim(tmp_path: Path) -> None:
    """⚠️ 前提が書き出し・読み戻しで落ちない（要件 7.9）。"""
    derivation = derive_position_tolerance(_shipped())
    target = tmp_path / "catch-opening.json"
    dump_derivation(derivation, target)

    restored = load_derivation(target)

    assert restored.assumptions == derivation.assumptions
    assert restored.formula == derivation.formula


def test_dump_writes_lf_sorted_and_indented(tmp_path: Path) -> None:
    """整形は LF・インデント2・キー整列・末尾改行に固定する（要件 1.7 と同じ規律）。"""
    target = tmp_path / "catch-opening.json"
    dump_derivation(derive_position_tolerance(_shipped()), target)

    content = target.read_bytes()
    assert b"\r" not in content
    assert content.endswith(b"\n")

    text = content.decode("utf-8")
    document = json.loads(text)
    assert text == json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def test_dump_is_idempotent(tmp_path: Path) -> None:
    """同じ導出を2度書いても同じバイト列になる（差分が行単位で読める）。"""
    derivation = derive_position_tolerance(_shipped())
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    dump_derivation(derivation, first)
    dump_derivation(derivation, second)

    assert first.read_bytes() == second.read_bytes()


def test_record_document_has_the_designed_fields(tmp_path: Path) -> None:
    """記録の形が design.md「Data Models」の表どおりである。"""
    target = tmp_path / "catch-opening.json"
    dump_derivation(derive_position_tolerance(_nominal()), target)

    document = json.loads(target.read_text(encoding="utf-8"))

    assert set(document) == {
        "schema_version",
        "position_tolerance_mm",
        "provenance",
        "inputs",
        "formula",
        "assumptions",
    }
    assert document["schema_version"] == SCHEMA_VERSION
    assert document["position_tolerance_mm"] == NOMINAL_TOLERANCE_MM
    assert document["provenance"] == "assumed"
    assert document["inputs"] == [
        {
            "name": OPENING_DIAMETER_PATH,
            "provenance": "assumed",
            "value_mm": NOMINAL_OPENING_MM,
        },
        {"name": OBJECT_DIAMETER_PATH, "provenance": "assumed", "value_mm": NOMINAL_OBJECT_MM},
    ]
    assert isinstance(document["assumptions"], list)


# ---------------------------------------------------------------------------
# 読み戻しの検証（記録は導出の写しであり、自由記述ではない）
# ---------------------------------------------------------------------------


def test_load_rejects_missing_file(tmp_path: Path) -> None:
    """ファイルが無いことも `ParameterError` に統一する（`config` と同形）。"""
    with pytest.raises(ParameterError):
        load_derivation(tmp_path / "absent.json")


def test_load_rejects_broken_json(tmp_path: Path) -> None:
    """JSON として解析できない内容を拒否する。"""
    target = tmp_path / "catch-opening.json"
    target.write_text("{", encoding="utf-8", newline="\n")

    with pytest.raises(ParameterError):
        load_derivation(target)


def test_load_rejects_unknown_top_level_key(tmp_path: Path) -> None:
    """⚠️ あらゆる階層で未知キーを拒否する（`config` と同じ規律）。"""
    document = _document(tmp_path)
    document["positon_tolerance_mm"] = 77.5

    with pytest.raises(ParameterError) as excinfo:
        load_derivation(_write(tmp_path, document))

    assert "positon_tolerance_mm" in str(excinfo.value)


def test_load_rejects_unknown_key_inside_an_input(tmp_path: Path) -> None:
    """入力の各項目でも未知キーを拒否する。"""
    document = _document(tmp_path)
    document["inputs"][0]["unit"] = "mm"

    with pytest.raises(ParameterError) as excinfo:
        load_derivation(_write(tmp_path, document))

    assert "unit" in str(excinfo.value)


def test_load_rejects_missing_key(tmp_path: Path) -> None:
    """必須キーの欠損を項目名つきで拒否する（既定値で埋めない）。"""
    document = _document(tmp_path)
    del document["assumptions"]

    with pytest.raises(ParameterError) as excinfo:
        load_derivation(_write(tmp_path, document))

    assert "assumptions" in str(excinfo.value)


def test_load_rejects_wrong_type(tmp_path: Path) -> None:
    """値の型違いを拒否する（`true` が 1mm として通らない）。"""
    document = _document(tmp_path)
    document["position_tolerance_mm"] = True

    with pytest.raises(ParameterError):
        load_derivation(_write(tmp_path, document))


def test_load_rejects_unsupported_schema_version(tmp_path: Path) -> None:
    """未対応の記録形式の版を拒否する。"""
    document = _document(tmp_path)
    document["schema_version"] = "9.9"

    with pytest.raises(ParameterError) as excinfo:
        load_derivation(_write(tmp_path, document))

    assert "9.9" in str(excinfo.value)


def test_load_rejects_a_value_that_does_not_follow_from_its_inputs(tmp_path: Path) -> None:
    """⚠️ 記録された値が記録された入力から導けなければ拒否する（要件 7.1）。

    導出箇所が1つであることは、記録側が別の値を主張できないことまで含めて
    初めて成立する。
    """
    document = _document(tmp_path)
    document["position_tolerance_mm"] = 67.5

    with pytest.raises(ParameterError) as excinfo:
        load_derivation(_write(tmp_path, document))

    message = str(excinfo.value)
    assert "67.5" in message
    assert "77.5" in message


def test_load_rejects_measured_claimed_from_assumed_inputs(tmp_path: Path) -> None:
    """⚠️ 未実測の入力から実測を名乗る記録を拒否する（要件 7.4）。"""
    document = _document(tmp_path)
    document["provenance"] = "measured"

    with pytest.raises(ParameterError) as excinfo:
        load_derivation(_write(tmp_path, document))

    message = str(excinfo.value)
    assert "measured" in message
    assert "assumed" in message


def test_load_accepts_measured_when_every_input_is_measured(tmp_path: Path) -> None:
    """入力がすべて実測であれば実測の記録を受け付ける（要件 7.4）。"""
    document = _document(tmp_path)
    document["provenance"] = "measured"
    for entry in document["inputs"]:
        entry["provenance"] = "measured"

    restored = load_derivation(_write(tmp_path, document))

    assert restored.provenance is Provenance.MEASURED


def test_load_rejects_unknown_provenance_word(tmp_path: Path) -> None:
    """出所の語彙は実測 / 仮値の2値ちょうどである（第3の値を作らない）。"""
    document = _document(tmp_path)
    document["provenance"] = "derived"

    with pytest.raises(ParameterError) as excinfo:
        load_derivation(_write(tmp_path, document))

    assert "derived" in str(excinfo.value)


def test_load_rejects_a_missing_required_input(tmp_path: Path) -> None:
    """入力は開口内径と対象物径の2件を必ず含む（design.md「Tolerance」Postconditions）。"""
    document = _document(tmp_path)
    document["inputs"] = [document["inputs"][0]]

    with pytest.raises(ParameterError) as excinfo:
        load_derivation(_write(tmp_path, document))

    assert OBJECT_DIAMETER_PATH in str(excinfo.value)


def test_load_rejects_an_extra_input_that_the_formula_does_not_use(tmp_path: Path) -> None:
    """⚠️ 式に現れない入力を記録へ足せない（要件 7.2）。

    `rim.flange_width_mm` を入力として書き足せるなら、記録の上では外向きの
    張り出しが根拠に含まれてしまう。
    """
    document = _document(tmp_path)
    document["inputs"].append(
        {"name": "rim.flange_width_mm", "provenance": "assumed", "value_mm": 30.0}
    )

    with pytest.raises(ParameterError) as excinfo:
        load_derivation(_write(tmp_path, document))

    assert "rim.flange_width_mm" in str(excinfo.value)


def test_load_rejects_a_record_without_the_mandated_assumptions(tmp_path: Path) -> None:
    """前提が落ちた記録を拒否する（要件 7.9）。"""
    document = _document(tmp_path)
    document["assumptions"] = ["特になし"]

    with pytest.raises(ParameterError) as excinfo:
        load_derivation(_write(tmp_path, document))

    assert "assumptions" in str(excinfo.value)


def test_load_rejects_a_rewritten_formula(tmp_path: Path) -> None:
    """記録された式が実装の式と食い違えば拒否する（要件 7.1: 唯一の導出箇所）。"""
    document = _document(tmp_path)
    document["formula"] = (
        f"{OPENING_DIAMETER_PATH} - {OBJECT_DIAMETER_PATH} + rim.flange_width_mm"
    )

    with pytest.raises(ParameterError) as excinfo:
        load_derivation(_write(tmp_path, document))

    assert "formula" in str(excinfo.value)


def test_load_rejects_inputs_that_is_not_an_array(tmp_path: Path) -> None:
    """`inputs` は配列でなければならない。"""
    document = _document(tmp_path)
    document["inputs"] = {"name": OPENING_DIAMETER_PATH}

    with pytest.raises(ParameterError):
        load_derivation(_write(tmp_path, document))


# ---------------------------------------------------------------------------
# 出荷される導出記録（`configs/catch_mechanism/catch-opening.json`）
#
# ⚠️ **出荷される寸法との突き合わせはここに置かない。** 「再導出した値と記録が
# 一致すること」を固定するのは tasks.md タスク 5.3（`_Boundary: Tolerance_`,
# `_Depends: 5.2_`）の完了状態であり、その成果物である。ここへ前倒しすると、
# 採寸値を書き戻すタスク 5.2（`_Boundary: Config_`）が自らの境界の外にある赤を
# 踏み、「全テストが通る」という 5.2 の完了状態を自力で満たせなくなる。
# 記録**単体**の妥当性（値が入力から導けること・出所が入力の最弱であること・
# 式・前提）は `load_derivation` → `ToleranceDerivation.__post_init__` が
# 読み込みのたびに強制するため、下の2件で足りる。
# ---------------------------------------------------------------------------


def test_shipped_record_is_a_valid_derivation_and_still_provisional() -> None:
    """出荷される記録が導出として整合し、その出所が仮値である（要件 6.7 と同じ扱い）。

    `load_derivation` を通ること自体が、値・入力・出所・式・前提の整合の検査で
    ある（記録は導出の写しであり自由記述ではない）。
    """
    derivation = load_derivation(DEFAULT_DERIVATION_PATH)

    assert derivation.provenance is Provenance.ASSUMED
    assert all(item.provenance is Provenance.ASSUMED for item in derivation.inputs)


def test_shipped_record_has_no_cr_bytes() -> None:
    """出荷される記録が LF で置かれている（`.gitattributes` の個別則と一致）。"""
    assert b"\r" not in DEFAULT_DERIVATION_PATH.read_bytes()


# ---------------------------------------------------------------------------
# 値型としての性質
# ---------------------------------------------------------------------------


def test_derivation_types_are_frozen_value_objects() -> None:
    """導出結果は不変であり、値等価である。"""
    first = derive_position_tolerance(_shipped())
    second = derive_position_tolerance(_shipped())

    assert first == second
    with pytest.raises(AttributeError):
        first.position_tolerance_mm = 1.0  # type: ignore[misc]
    with pytest.raises(AttributeError):
        first.inputs[0].value_mm = 1.0  # type: ignore[misc]


def test_derivation_rejects_inconsistent_construction() -> None:
    """⚠️ 不整合な導出結果は**そもそも構築できない**（唯一の導出箇所の担保）。"""
    inputs = (
        ToleranceInput(OPENING_DIAMETER_PATH, 220.0, Provenance.ASSUMED),
        ToleranceInput(OBJECT_DIAMETER_PATH, 65.0, Provenance.ASSUMED),
    )
    with pytest.raises(ParameterError):
        ToleranceDerivation(
            position_tolerance_mm=67.5,
            provenance=Provenance.ASSUMED,
            inputs=inputs,
            formula=FORMULA,
            assumptions=ASSUMPTIONS,
        )
