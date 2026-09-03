"""寸法パラメータの型と構築時検証（タスク 1.3、要件 1.1, 1.2, 1.4, 1.5, 1.8, 2.1,
2.5, 2.6, 8.6, 9.4, 10.1）。

本ファイルが固定するのは design.md「Params」の Preconditions / Postconditions /
Invariants と、tasks.md タスク 1.3 の「観測可能な完了状態」である。

1. **実物の寸法に既定値が無い**こと。省略した構築が `TypeError` で失敗すること
   （要件 1.4 / design.md「Params」Responsibilities: 「既定値を与えるのは
   設計上の選択に限る」）。既定値を持ってよいのは受け口の**決定値**
   （`added_depth_mm = 0.0` / `bottom_modification = "none"`）だけである
2. **出所は実測 / 仮値の2値**であり、導出は「入力の最弱を継承する」こと
   （要件 1.2, 1.5, 9.4）。⚠️ 第3の値を作らないことを値集合の完全一致で固定する
3. **許可材料の一覧に無い材料を構築時に拒否する**こと（要件 2.5）
4. **`PARAMETER_PATHS` がデータクラス木の走査で生成される**こと（要件 1.8）。
   手書きの表であれば、フィールドを増やしたときに黙って表から漏れる
5. **径の大小関係・正値性・角度範囲を構築時に検証し、違反フィールド名と値を示す**
   こと（要件 1.4, 8.6, 10.1）

ファイル名について: `tests/` 配下には `__init__.py` が無く pytest の import-mode も
既定（prepend）のため、テストモジュール名はセッション全体でフラットである。
design.md「Directory Structure」が挙げる `test_params.py` は将来
`tests/trajectory_sim/test_params.py` 等と衝突しうるため使えない。タスク 1.1 の
`test_catch_packaging.py` / タスク 1.2 の `test_catch_errors.py` に倣い
`test_catch_params.py` とする（tasks.md「Implementation Notes」）。
"""

from __future__ import annotations

import ast
import math
import pickle
from copy import deepcopy
from dataclasses import FrozenInstanceError, MISSING, asdict, fields, is_dataclass
from pathlib import Path
from typing import get_type_hints

import pytest

from catch_mechanism import params as params_module
from catch_mechanism.errors import CatchMechanismError, ParameterError
from catch_mechanism.params import (
    ALLOWED_MATERIALS,
    PARAMETER_PATHS,
    JointPolicy,
    MechanismParams,
    ObjectSpec,
    ParameterPath,
    PrintingConstraints,
    Provenance,
    RetentionParams,
    RimParams,
    TrashCanMeasurements,
)

# ---------------------------------------------------------------------------
# 構築ヘルパ
#
# 値は design.md「Logical Data Model」の `configs/catch_mechanism/dimensions.json`
# の例に合わせる（すべて公称・推定の仮値）。テスト側で別の値表を作らないため、
# 各ヘルパは1箇所で定義し `**overrides` で1項目だけ差し替える形に統一する。
# ---------------------------------------------------------------------------

def _trash_can(**overrides: object) -> TrashCanMeasurements:
    values: dict[str, object] = {
        "model_id": "yamada-kagaku-no335",
        "opening_inner_diameter_mm": 220.0,
        "top_outer_diameter_mm": 225.0,
        "bottom_outer_diameter_mm": 158.0,
        "bottom_flat_diameter_mm": 140.0,
        "height_mm": 244.0,
        "mass_g": 228.0,
        "bottom_thickness_mm": 1.5,
        "taper_deg": 7.0,
    }
    values.update(overrides)
    return TrashCanMeasurements(**values)  # type: ignore[arg-type]


def _target_object(**overrides: object) -> ObjectSpec:
    values: dict[str, object] = {"diameter_mm": 65.0, "height_mm": 122.0}
    values.update(overrides)
    return ObjectSpec(**values)  # type: ignore[arg-type]


def _printing(**overrides: object) -> PrintingConstraints:
    values: dict[str, object] = {
        "build_x_mm": 180.0,
        "build_y_mm": 180.0,
        "build_z_mm": 180.0,
        "material": "PETG",
        "material_density_g_cm3": 1.27,
        "segment_margin_mm": 5.0,
    }
    values.update(overrides)
    return PrintingConstraints(**values)  # type: ignore[arg-type]


def _joint(**overrides: object) -> JointPolicy:
    values: dict[str, object] = {
        "bolt_designation": "M3",
        "through_hole_diameter_mm": 3.4,
        "insert_outer_diameter_mm": 4.6,
        "insert_length_mm": 5.7,
        "dowel_diameter_mm": 3.0,
        "min_bearing_area_mm2": 60.0,
    }
    values.update(overrides)
    return JointPolicy(**values)  # type: ignore[arg-type]


def _rim(**overrides: object) -> RimParams:
    values: dict[str, object] = {
        "fit_clearance_mm": 1.0,
        "flange_width_mm": 30.0,
        "flange_slope_deg": 15.0,
        "wall_thickness_mm": 4.0,
        "height_mm": 18.0,
    }
    values.update(overrides)
    return RimParams(**values)  # type: ignore[arg-type]


def _retention(**overrides: object) -> RetentionParams:
    values: dict[str, object] = {
        "retrofit_fastener_count": 6,
        "liner_flat_min_diameter_mm": 140.0,
    }
    values.update(overrides)
    return RetentionParams(**values)  # type: ignore[arg-type]


def _params(**overrides: object) -> MechanismParams:
    values: dict[str, object] = {
        "trash_can": _trash_can(),
        "target_object": _target_object(),
        "printing": _printing(),
        "joint": _joint(),
        "rim": _rim(),
        "retention": _retention(),
        "provenance": {"trash_can.opening_inner_diameter_mm": Provenance.ASSUMED},
    }
    values.update(overrides)
    return MechanismParams(**values)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 要件 1.2 / 1.5 / 9.4: 出所は実測 / 仮値の2値であり、導出は最弱を継承する
# ---------------------------------------------------------------------------


def test_provenance_has_exactly_two_values() -> None:
    """出所は `MEASURED` / `ASSUMED` の2値ちょうどである（要件 1.2）。

    ⚠️ 第3の値（"derived" 等）を作らないことがこのテストの主眼である。
    導出量は独自の出所を持たず「入力の最弱」を継承する（要件 1.5）ため、
    第3の値は下流の設定ファイルとの値集合の一致を壊すだけである。
    """
    assert {member.value for member in Provenance} == {"measured", "assumed"}
    assert Provenance.MEASURED.value == "measured"
    assert Provenance.ASSUMED.value == "assumed"


def test_provenance_value_set_matches_trajectory_sim() -> None:
    """出所の値集合が `trajectory_sim.Provenance` と一致する（design.md「Params」）。

    design.md Implementation Notes は「`trajectory_sim.params` の型を import
    しない（依存方向が逆になる）。⚠️ `Provenance` の**値集合だけ**を一致させ、
    `str` として設定ファイルへ書けるようにする」と定める。実装モジュールでは
    import できないため、**テストからのみ**両者を突き合わせて還元時に翻訳が
    要らないことを固定する。
    """
    from trajectory_sim.params import Provenance as UpstreamProvenance

    assert {m.value for m in Provenance} == {m.value for m in UpstreamProvenance}


def test_provenance_is_str_and_serializable() -> None:
    """出所は `str` として設定ファイルへそのまま書ける（design.md「Params」）。"""
    assert isinstance(Provenance.MEASURED, str)
    assert Provenance("measured") is Provenance.MEASURED
    assert f"{Provenance.ASSUMED}" == "assumed"


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ((Provenance.MEASURED,), Provenance.MEASURED),
        ((Provenance.ASSUMED,), Provenance.ASSUMED),
        ((Provenance.MEASURED, Provenance.MEASURED), Provenance.MEASURED),
        ((Provenance.MEASURED, Provenance.ASSUMED), Provenance.ASSUMED),
        ((Provenance.ASSUMED, Provenance.MEASURED), Provenance.ASSUMED),
        ((Provenance.ASSUMED, Provenance.ASSUMED), Provenance.ASSUMED),
        (
            (Provenance.MEASURED, Provenance.MEASURED, Provenance.ASSUMED),
            Provenance.ASSUMED,
        ),
    ],
)
def test_weakest_inherits_the_weakest_input(
    values: tuple[Provenance, ...], expected: Provenance
) -> None:
    """導出量の出所は入力の最弱を継承する（要件 1.5、tasks.md 1.3 の完了状態）。

    「実測＋仮値の導出は仮値」「実測のみの導出は実測」——1つでも仮値を含めば
    仮値であり、未実測の推定が実測を名乗って合否条件へ紛れ込むことを防ぐ。
    """
    assert Provenance.weakest(*values) is expected


def test_weakest_rejects_empty_input() -> None:
    """入力が無ければ「最弱」は定義できないため拒否する（要件 1.5）。

    空を `MEASURED` と解釈すると、入力を1つも持たない値が実測を名乗る。
    """
    with pytest.raises(ParameterError):
        Provenance.weakest()


def test_weakest_rejects_non_provenance_input() -> None:
    """`Provenance` 以外を最弱の計算へ混ぜない（要件 1.2）。"""
    with pytest.raises(ParameterError) as excinfo:
        Provenance.weakest(Provenance.MEASURED, "measured")  # type: ignore[arg-type]
    assert "measured" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 要件 1.4: 実物の寸法に既定値を与えない（省略した構築を失敗させる）
# ---------------------------------------------------------------------------

#: 実物の寸法・実機の制約・調達品の諸元を持つ型。全フィールドが必須である。
NO_DEFAULT_CLASSES = (
    TrashCanMeasurements,
    ObjectSpec,
    PrintingConstraints,
    JointPolicy,
    RimParams,
    MechanismParams,
)


@pytest.mark.parametrize("cls", NO_DEFAULT_CLASSES, ids=lambda c: c.__name__)
def test_real_world_dimensions_have_no_defaults(cls: type) -> None:
    """実物の寸法に既定値を与えない（要件 1.4 / design.md「Params」）。

    既定値があると、設定ファイルに書き忘れた項目が「もっともらしい数」で
    黙って埋まり、未実測の値が実測のふりをする。`trajectory_sim.
    DrivetrainParams` と同じ扱いとする。
    """
    for field_info in fields(cls):
        assert field_info.default is MISSING, f"{cls.__name__}.{field_info.name} に既定値がある"
        assert field_info.default_factory is MISSING, (
            f"{cls.__name__}.{field_info.name} に既定値ファクトリがある"
        )


@pytest.mark.parametrize("cls", NO_DEFAULT_CLASSES, ids=lambda c: c.__name__)
def test_omitted_construction_fails(cls: type) -> None:
    """省略した構築は `TypeError` として失敗する（要件 1.4）。"""
    with pytest.raises(TypeError):
        cls()  # type: ignore[call-arg]


def test_partially_omitted_construction_fails() -> None:
    """一部だけ省略した構築も失敗する（要件 1.4）。"""
    with pytest.raises(TypeError):
        TrashCanMeasurements(  # type: ignore[call-arg]
            opening_inner_diameter_mm=220.0,
            top_outer_diameter_mm=225.0,
        )
    with pytest.raises(TypeError):
        MechanismParams(  # type: ignore[call-arg]
            trash_can=_trash_can(),
            target_object=_target_object(),
            printing=_printing(),
            joint=_joint(),
        )


def test_retention_decisions_are_the_only_defaults() -> None:
    """既定値を持つのは受け口の決定値だけである（design.md「Params」Invariants）。

    `added_depth_mm = 0.0`（受け口は本体に深さを足さない）と
    `bottom_modification = "none"`（底へ加工を行わない）は実測ではなく
    **設計上の決定**であり、型として固定してよい唯一の値である。
    """
    defaults = {f.name: f.default for f in fields(RetentionParams)}
    assert defaults["retrofit_fastener_count"] is MISSING
    assert defaults["liner_flat_min_diameter_mm"] is MISSING
    assert defaults["added_depth_mm"] == 0.0
    assert defaults["bottom_modification"] == "none"


# ---------------------------------------------------------------------------
# 要件 1.4 / 8.6 / 10.1: 正値性・角度範囲・径の大小関係の構築時検証
# ---------------------------------------------------------------------------

#: 正の有限値であることを要求する（型, フィールド名）の並び。
POSITIVE_FIELDS = [
    (TrashCanMeasurements, "opening_inner_diameter_mm"),
    (TrashCanMeasurements, "top_outer_diameter_mm"),
    (TrashCanMeasurements, "bottom_outer_diameter_mm"),
    (TrashCanMeasurements, "bottom_flat_diameter_mm"),
    (TrashCanMeasurements, "height_mm"),
    (TrashCanMeasurements, "mass_g"),
    (TrashCanMeasurements, "bottom_thickness_mm"),
    (ObjectSpec, "diameter_mm"),
    (ObjectSpec, "height_mm"),
    (PrintingConstraints, "build_x_mm"),
    (PrintingConstraints, "build_y_mm"),
    (PrintingConstraints, "build_z_mm"),
    (PrintingConstraints, "material_density_g_cm3"),
    (JointPolicy, "through_hole_diameter_mm"),
    (JointPolicy, "insert_outer_diameter_mm"),
    (JointPolicy, "insert_length_mm"),
    (JointPolicy, "dowel_diameter_mm"),
    (JointPolicy, "min_bearing_area_mm2"),
    (RimParams, "fit_clearance_mm"),
    (RimParams, "flange_width_mm"),
    (RimParams, "wall_thickness_mm"),
    (RimParams, "height_mm"),
    (RetentionParams, "liner_flat_min_diameter_mm"),
]

_FACTORIES = {
    TrashCanMeasurements: _trash_can,
    ObjectSpec: _target_object,
    PrintingConstraints: _printing,
    JointPolicy: _joint,
    RimParams: _rim,
    RetentionParams: _retention,
}


@pytest.mark.parametrize(
    "bad_value", [0.0, -1.0, -0.0001, float("nan"), math.inf, -math.inf]
)
@pytest.mark.parametrize(
    ("cls", "field_name"), POSITIVE_FIELDS, ids=lambda x: x if isinstance(x, str) else x.__name__
)
def test_non_positive_or_non_finite_is_rejected(
    cls: type, field_name: str, bad_value: float
) -> None:
    """長さ・直径・質量は有限かつ正でなければならない（design.md Preconditions）。

    メッセージには**違反フィールド名と値**を含める（要件 1.4:「該当する項目名と
    値を示す」／ design.md Validation）。
    """
    with pytest.raises(ParameterError) as excinfo:
        _FACTORIES[cls](**{field_name: bad_value})
    message = str(excinfo.value)
    assert field_name in message
    assert repr(bad_value) in message


@pytest.mark.parametrize("bad_angle", [-0.1, -1.0, 90.0, 90.1, 180.0, float("nan"), math.inf])
@pytest.mark.parametrize(
    ("cls", "field_name"),
    [(TrashCanMeasurements, "taper_deg"), (RimParams, "flange_slope_deg")],
    ids=["taper_deg", "flange_slope_deg"],
)
def test_angle_out_of_range_is_rejected(cls: type, field_name: str, bad_angle: float) -> None:
    """角度は 0 以上 90 度未満である（design.md「Params」Preconditions）。"""
    with pytest.raises(ParameterError) as excinfo:
        _FACTORIES[cls](**{field_name: bad_angle})
    message = str(excinfo.value)
    assert field_name in message
    assert repr(bad_angle) in message


@pytest.mark.parametrize("good_angle", [0.0, 0.5, 7.0, 45.0, 89.999])
def test_angle_in_range_is_accepted(good_angle: float) -> None:
    """0 以上 90 度未満の角度は受け付ける（テーパー 0 = 円筒も有効な形状）。"""
    assert _trash_can(taper_deg=good_angle).taper_deg == good_angle
    assert _rim(flange_slope_deg=good_angle).flange_slope_deg == good_angle


def test_bottom_flat_larger_than_bottom_outer_is_rejected() -> None:
    """底の平面部径は底の外径を超えられない（design.md「Params」Invariants）。"""
    with pytest.raises(ParameterError) as excinfo:
        _trash_can(bottom_flat_diameter_mm=160.0, bottom_outer_diameter_mm=158.0)
    message = str(excinfo.value)
    assert "bottom_flat_diameter_mm" in message
    assert "bottom_outer_diameter_mm" in message
    assert "160.0" in message
    assert "158.0" in message


def test_bottom_outer_larger_than_opening_inner_is_rejected() -> None:
    """底の外径は開口内径を超えられない（design.md「Params」Invariants）。"""
    with pytest.raises(ParameterError) as excinfo:
        _trash_can(bottom_outer_diameter_mm=230.0, opening_inner_diameter_mm=220.0)
    message = str(excinfo.value)
    assert "bottom_outer_diameter_mm" in message
    assert "opening_inner_diameter_mm" in message
    assert "230.0" in message
    assert "220.0" in message


def test_equal_diameters_are_accepted() -> None:
    """大小関係は等号を含む（`<=`）。円筒形のゴミ箱を排除しない。"""
    measurements = _trash_can(
        bottom_flat_diameter_mm=200.0,
        bottom_outer_diameter_mm=200.0,
        opening_inner_diameter_mm=200.0,
        taper_deg=0.0,
    )
    assert measurements.bottom_flat_diameter_mm == 200.0


def test_retrofit_fastener_count_must_be_positive_integer() -> None:
    """後付け締結箇所は 1 以上の整数である（要件 9.7）。"""
    for bad_count in (0, -1):
        with pytest.raises(ParameterError) as excinfo:
            _retention(retrofit_fastener_count=bad_count)
        assert "retrofit_fastener_count" in str(excinfo.value)


def test_added_depth_must_be_zero() -> None:
    """受け口は本体に深さを足さない決定である（design.md「Params」Invariants）。"""
    with pytest.raises(ParameterError) as excinfo:
        _retention(added_depth_mm=5.0)
    message = str(excinfo.value)
    assert "added_depth_mm" in message
    assert "5.0" in message


def test_bottom_modification_must_be_none() -> None:
    """底へ加工を行わない決定を型で表す（design.md「Params」Invariants）。"""
    with pytest.raises(ParameterError) as excinfo:
        _retention(bottom_modification="cut")
    message = str(excinfo.value)
    assert "bottom_modification" in message
    assert "cut" in message


def test_model_id_must_not_be_empty() -> None:
    """採寸した実物の機種識別子は空文字を許さない（要件 6.8）。

    どの実物を測った値なのかが設定ファイルから失われると、選定結果と寸法設定の
    突き合わせ（タスク 5.1「選定結果が寸法設定ファイルの機種識別情報と一致する」）
    が成立しない。
    """
    with pytest.raises(ParameterError) as excinfo:
        _trash_can(model_id="")
    assert "model_id" in str(excinfo.value)


def test_model_id_is_carried_by_the_measurements_type() -> None:
    """機種識別子は採寸値と同じ型に載る（要件 1.8: 定義を単一の箇所に限る）。

    design.md `## Data Models` の `dimensions.json` 例は `trash_can.model_id` を
    持つ。⚠️ 同じ design.md の `#### Params` Service Interface には現れないが、
    `## Data Models` 側・要件 6.8・タスク 5.1 に合わせてこちらを採る（タスク 1.4
    の Config は `params.py` を変更できないため、ここで決着させる必要がある）。
    """
    assert _trash_can().model_id == "yamada-kagaku-no335"
    assert PARAMETER_PATHS["trash_can.model_id"].value_type is str
    assert PARAMETER_PATHS["trash_can.model_id"].unit == ""
    assert PARAMETER_PATHS["trash_can.model_id"].component == "trash_can"
    assert PARAMETER_PATHS["trash_can.model_id"].field_name == "model_id"


def test_bolt_designation_must_not_be_empty() -> None:
    """締結要素の呼びは空文字を許さない（要件 2.6: 荷重を受ける要素の識別）。"""
    with pytest.raises(ParameterError) as excinfo:
        _joint(bolt_designation="")
    assert "bolt_designation" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 要件 2.1 / 2.5: 造形制約と許可材料
# ---------------------------------------------------------------------------


def test_allowed_materials_is_exactly_petg_and_pla() -> None:
    """許可材料の一覧は PETG / PLA である（design.md「Params」/「Constraints」）。

    ⚠️ ASA / ABS / PC / PA / CF・GF は一覧に含めない（design.md「Constraints」）。
    """
    assert ALLOWED_MATERIALS == frozenset({"PETG", "PLA"})
    assert isinstance(ALLOWED_MATERIALS, frozenset)


@pytest.mark.parametrize("material", ["PETG", "PLA"])
def test_allowed_material_is_accepted(material: str) -> None:
    """一覧にある材料は受け付ける（要件 2.5）。"""
    assert _printing(material=material).material == material


@pytest.mark.parametrize("material", ["ABS", "ASA", "PC", "PA-CF", "petg", "", "PETG "])
def test_disallowed_material_is_rejected(material: str) -> None:
    """一覧に無い材料の指定を構築時に拒否する（要件 2.5、tasks.md 1.3 の完了状態）。

    大文字小文字違い・前後の空白も**黙って正規化しない**。設定ファイルの
    表記揺れを許すと、許可一覧が実質的に曖昧になる。
    """
    with pytest.raises(ParameterError) as excinfo:
        _printing(material=material)
    message = str(excinfo.value)
    assert "material" in message
    assert repr(material) in message


def test_material_rejection_lists_allowed_materials() -> None:
    """拒否メッセージに許可一覧を示す（何を書けばよいかが分からなければ直せない）。"""
    with pytest.raises(ParameterError) as excinfo:
        _printing(material="ABS")
    message = str(excinfo.value)
    assert "PETG" in message
    assert "PLA" in message


# ---------------------------------------------------------------------------
# 要件 1.8: PARAMETER_PATHS はデータクラス木の走査で生成する
# ---------------------------------------------------------------------------


def _walk_expected_paths() -> dict[str, tuple[str, str, type]]:
    """`MechanismParams` の木を独立に走査し、期待されるパス表を作る。

    実装が手書きの表を持っていれば、この独立な走査と食い違う。
    """
    expected: dict[str, tuple[str, str, type]] = {}
    hints = get_type_hints(MechanismParams)
    for component in fields(MechanismParams):
        if component.name == "provenance":
            continue
        component_type = hints[component.name]
        assert is_dataclass(component_type)
        leaf_hints = get_type_hints(component_type)
        for leaf in fields(component_type):
            path = f"{component.name}.{leaf.name}"
            expected[path] = (component.name, leaf.name, leaf_hints[leaf.name])
    return expected


def test_parameter_paths_cover_every_leaf_field() -> None:
    """パス表はデータクラス木の全リーフを漏れなく含む（要件 1.8）。"""
    expected = _walk_expected_paths()
    assert set(PARAMETER_PATHS) == set(expected)


def test_parameter_paths_entries_match_the_tree() -> None:
    """各エントリのパス・所属・フィールド名・値の型が木と一致する（要件 1.8）。"""
    expected = _walk_expected_paths()
    for path, (component, field_name, value_type) in expected.items():
        entry = PARAMETER_PATHS[path]
        assert isinstance(entry, ParameterPath)
        assert entry.path == path
        assert entry.component == component
        assert entry.field_name == field_name
        assert entry.value_type is value_type


def test_parameter_paths_resolve_against_a_real_instance() -> None:
    """表のパスは実インスタンスから実際に値を辿れる（要件 1.8 / 10.1）。"""
    params = _params()
    for path, entry in PARAMETER_PATHS.items():
        value = getattr(getattr(params, entry.component), entry.field_name)
        assert isinstance(value, entry.value_type), path


def test_parameter_paths_excludes_provenance() -> None:
    """`provenance` はパス表に含めない（寸法ではなく出所の対応表そのもの）。"""
    assert "provenance" not in PARAMETER_PATHS
    assert not any(path.startswith("provenance") for path in PARAMETER_PATHS)


def test_parameter_paths_contains_the_documented_paths() -> None:
    """design.md の `dimensions.json` 例が挙げる出所のパスが実在する（要件 1.1, 1.2）。"""
    for path in (
        "trash_can.model_id",
        "trash_can.opening_inner_diameter_mm",
        "trash_can.bottom_outer_diameter_mm",
        "target_object.diameter_mm",
        "printing.material",
        "joint.bolt_designation",
        "rim.fit_clearance_mm",
        "retention.bottom_modification",
    ):
        assert path in PARAMETER_PATHS


def test_parameter_path_value_types() -> None:
    """値の型は下流（Config, タスク 1.4）が JSON を検証するために要る。"""
    assert PARAMETER_PATHS["trash_can.mass_g"].value_type is float
    assert PARAMETER_PATHS["printing.material"].value_type is str
    assert PARAMETER_PATHS["retention.retrofit_fastener_count"].value_type is int


def test_parameter_path_units_are_derived_from_field_names() -> None:
    """単位はフィールド名の接尾辞から導き、手書きの表を二重管理しない（要件 1.8）。"""
    assert PARAMETER_PATHS["trash_can.opening_inner_diameter_mm"].unit == "mm"
    assert PARAMETER_PATHS["trash_can.taper_deg"].unit == "deg"
    assert PARAMETER_PATHS["trash_can.mass_g"].unit == "g"
    assert PARAMETER_PATHS["joint.min_bearing_area_mm2"].unit == "mm^2"
    assert PARAMETER_PATHS["printing.material_density_g_cm3"].unit == "g/cm^3"
    assert PARAMETER_PATHS["printing.material"].unit == ""
    assert PARAMETER_PATHS["retention.retrofit_fastener_count"].unit == ""
    assert PARAMETER_PATHS["trash_can.model_id"].unit == ""


def test_parameter_paths_is_immutable() -> None:
    """パス表は書き換えられない（唯一の正が実行時に差し替わらない）。"""
    with pytest.raises(TypeError):
        PARAMETER_PATHS["trash_can.height_mm"] = PARAMETER_PATHS[  # type: ignore[index]
            "trash_can.height_mm"
        ]


# ---------------------------------------------------------------------------
# 要件 9.4 / design.md Risks: provenance の未知キーを拒否する
# ---------------------------------------------------------------------------


def test_known_provenance_keys_are_accepted() -> None:
    """`PARAMETER_PATHS` にあるキーは受け付ける（要件 1.2）。"""
    params = _params(
        provenance={
            "trash_can.opening_inner_diameter_mm": Provenance.MEASURED,
            "target_object.diameter_mm": Provenance.ASSUMED,
        }
    )
    assert params.provenance["trash_can.opening_inner_diameter_mm"] is Provenance.MEASURED


def test_unknown_provenance_key_is_rejected() -> None:
    """未知のキーは拒否する（design.md「Params」Risks: 出所が黙って無視されない）。"""
    with pytest.raises(ParameterError) as excinfo:
        _params(provenance={"trash_can.no_such_field_mm": Provenance.MEASURED})
    assert "trash_can.no_such_field_mm" in str(excinfo.value)


def test_provenance_key_for_a_component_alone_is_rejected() -> None:
    """コンポーネント名だけのキーはリーフを指さないため拒否する（要件 9.4）。"""
    with pytest.raises(ParameterError) as excinfo:
        _params(provenance={"trash_can": Provenance.MEASURED})
    assert "trash_can" in str(excinfo.value)


def test_provenance_value_must_be_a_provenance() -> None:
    """出所の値は `Provenance` の2値に限る（要件 1.2）。"""
    with pytest.raises(ParameterError) as excinfo:
        _params(provenance={"trash_can.height_mm": "derived"})  # type: ignore[dict-item]
    message = str(excinfo.value)
    assert "trash_can.height_mm" in message
    assert "derived" in message


def test_empty_provenance_is_accepted() -> None:
    """出所の記載が無いことは許す。design.md は「表に無いパスは ASSUMED」と定める。"""
    assert _params(provenance={}).provenance == {}


def test_provenance_is_not_aliased_to_the_caller_dict() -> None:
    """構築後に呼び出し側の辞書を書き換えても値は変わらない（Postconditions）。

    検証を通った `MechanismParams` が以降の層で再検証を要さないためには、
    検証時に見た対応表がそのまま残ることが要る。呼び出し側の辞書との参照の
    共有（エイリアス）を切ることでこれを満たす。
    """
    source: dict[str, Provenance] = {"trash_can.height_mm": Provenance.MEASURED}
    params = _params(provenance=source)
    source["trash_can.no_such_field_mm"] = Provenance.MEASURED
    assert "trash_can.no_such_field_mm" not in params.provenance
    assert params.provenance == {"trash_can.height_mm": Provenance.MEASURED}


def test_provenance_is_a_plain_dict() -> None:
    """`provenance` は素の `dict` である（`MappingProxyType` で包まない）。

    ⚠️ **これは直列化の生死を分ける。** `dataclasses.asdict()` は dict では
    ないマッピング型を再帰対象と認識せず `copy.deepcopy()` へ回すため、
    `MappingProxyType` を保持すると `TypeError: cannot pickle 'mappingproxy'
    object` になる（同じ罠の記録が
    `src/flying_object_tracking/bench/compare.py` にある）。上流の
    `trajectory_sim.ScenarioParams.provenance` も素の `dict` を保持する。
    """
    assert type(_params().provenance) is dict


def test_params_survive_asdict_deepcopy_and_pickle() -> None:
    """集約ルートが直列化の3経路を通る（タスク 1.4 の `dump_params` / digest の前提）。

    `config.dump_params` / `parameters_digest`（タスク 1.4）は集約の直列化その
    ものであり、最も自然な経路が `dataclasses.asdict()` である。ここが通らない
    型は、設定ファイルの書き出しと識別子の算出をどちらも塞ぐ。
    """
    params = _params(
        provenance={
            "trash_can.opening_inner_diameter_mm": Provenance.MEASURED,
            "target_object.diameter_mm": Provenance.ASSUMED,
        }
    )

    payload = asdict(params)
    assert payload["trash_can"]["model_id"] == "yamada-kagaku-no335"
    assert payload["trash_can"]["opening_inner_diameter_mm"] == 220.0
    assert payload["retention"]["bottom_modification"] == "none"
    assert payload["provenance"] == {
        "trash_can.opening_inner_diameter_mm": Provenance.MEASURED,
        "target_object.diameter_mm": Provenance.ASSUMED,
    }

    assert deepcopy(params) == params
    assert pickle.loads(pickle.dumps(params)) == params


# ---------------------------------------------------------------------------
# 要件 10.1: 値としての不変性と公開に足る形
# ---------------------------------------------------------------------------

ALL_PARAM_CLASSES = (
    TrashCanMeasurements,
    ObjectSpec,
    PrintingConstraints,
    JointPolicy,
    RimParams,
    RetentionParams,
    MechanismParams,
    ParameterPath,
)


@pytest.mark.parametrize("cls", ALL_PARAM_CLASSES, ids=lambda c: c.__name__)
def test_types_are_frozen_dataclasses_with_slots(cls: type) -> None:
    """すべて frozen かつ slots のデータクラスである（design.md「Params」）。"""
    assert is_dataclass(cls)
    assert cls.__dataclass_params__.frozen  # type: ignore[attr-defined]
    assert "__slots__" in cls.__dict__


def test_instances_reject_mutation() -> None:
    """構築後の書き換えを拒む（値としての不変性）。"""
    params = _params()
    with pytest.raises(FrozenInstanceError):
        params.trash_can = _trash_can()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        params.trash_can.height_mm = 1.0  # type: ignore[misc]
    with pytest.raises(AttributeError):
        params.__dict__  # noqa: B018


def test_params_are_value_equal() -> None:
    """同じ値から作った集約は等しい（設定の識別子計算が値に依存できる）。"""
    assert _params() == _params()
    assert _params() != _params(trash_can=_trash_can(height_mm=245.0))


def test_valid_params_construct_and_expose_downstream_fields() -> None:
    """下流が参照する底寸法・造形制約・継手方針が集約から辿れる（要件 10.1, 10.2）。"""
    params = _params()
    assert params.trash_can.bottom_outer_diameter_mm == 158.0
    assert params.trash_can.bottom_flat_diameter_mm == 140.0
    assert params.trash_can.taper_deg == 7.0
    assert params.trash_can.height_mm == 244.0
    assert params.trash_can.mass_g == 228.0
    assert params.printing.material == "PETG"
    assert params.joint.bolt_designation == "M3"


def test_parameter_error_is_a_catch_mechanism_error() -> None:
    """検証違反は `catch_mechanism.errors` の階層で送出する（要件 1.4 / タスク 1.2）。"""
    with pytest.raises(CatchMechanismError):
        _printing(material="ABS")
    with pytest.raises(ValueError):
        _printing(material="ABS")


def test_module_exports_the_designed_names() -> None:
    """design.md「Params」Service Interface の名前をそのまま公開する。"""
    assert set(params_module.__all__) == {
        "Provenance",
        "TrashCanMeasurements",
        "ObjectSpec",
        "PrintingConstraints",
        "JointPolicy",
        "RimParams",
        "RetentionParams",
        "MechanismParams",
        "ParameterPath",
        "PARAMETER_PATHS",
        "ALLOWED_MATERIALS",
    }


# ---------------------------------------------------------------------------
# 要件 5.2 / 5.4 / design.md Implementation Notes: 依存方向を静的に固定する
# ---------------------------------------------------------------------------

#: `params.py` が import してよいトップレベルモジュール。標準ライブラリと自パッケージのみ。
ALLOWED_IMPORT_ROOTS = frozenset(
    {
        "__future__",
        "math",
        "dataclasses",
        "enum",
        "types",
        "typing",
        "collections",
        "catch_mechanism",
    }
)


def _imported_roots(source_path: Path) -> set[str]:
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_params_module_does_not_reverse_the_dependency_direction() -> None:
    """`trajectory_sim` / `prediction_core` / `build123d` を import しない。

    design.md「Params」Implementation Notes: 「`trajectory_sim.params` の型を
    import しない（依存方向が逆になる）」。形状ライブラリを import しないことは
    要件 5.2（形状ライブラリ無しで寸法を扱える）の前提でもある。
    """
    roots = _imported_roots(Path(params_module.__file__))
    assert "trajectory_sim" not in roots
    assert "prediction_core" not in roots
    assert "build123d" not in roots
    assert roots <= ALLOWED_IMPORT_ROOTS, f"想定外の import: {sorted(roots - ALLOWED_IMPORT_ROOTS)}"
