"""寸法パラメータの型と構築時検証（タスク 1.3、要件 1.1, 1.2, 1.9, 3.7, 4.3,
7.8, 8.1, 8.2）。

本ファイルが固定するのは design.md `#### Params` の Preconditions /
Postconditions / Invariants と、tasks.md タスク 1.3 の「観測可能な完了状態」である。

1. **基準面の記述が空の構築を拒否する**こと（`bracket.mount_face_reference`）。
   ⚠️ 「別途 105.6mm という測定値があったが基準面が不明のため破棄した」という
   失敗を再発させないための型上の要求である
2. **上流より緩い当たり面下限を拒否する**こと（`joint_local.min_bearing_area_mm2`）。
   ⚠️ 厳しくすることはできるが緩めることはできない
3. **負の寸法を項目名と値つきで拒否する**こと（要件 1.4 と同形の扱い）
4. **「実測＋仮値の導出は仮値」**となること（要件 1.9 / 導出値の出所は入力の最弱を継承）

併せて、design.md「静的検査」が本 Spec に課す2点——`PARAMETER_PATHS` に上流の
コンポーネント名が現れないこと（要件 1.3 の機械的な担保）と、上流の**内部
モジュール**を直接 import していないこと——を `params.py` について先取りして
固定する。`test_chassis_boundaries.py`（タスク 7.x）が全モジュールへ広げる。

ファイル名について: `tests/` 配下には `__init__.py` が無く pytest の import-mode も
既定（prepend）のため、テストモジュール名はセッション全体でフラットである。
`test_chassis_` 接頭辞は design.md「Existing Architecture Analysis」の要求である。
"""

from __future__ import annotations

import ast
import math
import pickle
from copy import deepcopy
from dataclasses import (
    FrozenInstanceError,
    MISSING,
    asdict,
    dataclass,
    fields,
    is_dataclass,
)
from pathlib import Path
from typing import get_type_hints

import pytest

import catch_mechanism
from catch_mechanism import JointPolicy, Provenance
from chassis_mechanism import params as params_module
from chassis_mechanism.errors import ChassisMechanismError, ParameterError
from chassis_mechanism.params import (
    PARAMETER_PATHS,
    AdapterSpec,
    BaseSpec,
    BatterySpec,
    BoardSpec,
    BracketMeasurements,
    ChassisParams,
    ClearanceLimits,
    HubSpec,
    LocalJointLimits,
    MassItem,
    MotorSpec,
    ParameterPath,
    PowerParams,
    StandSpec,
    WheelSpec,
    weakest_provenance,
)

# ---------------------------------------------------------------------------
# 構築ヘルパ
#
# 値は「あり得る形」であることだけを満たす仮値であり、`configs/chassis_mechanism/
# dimensions.json`（タスク 1.4）の正ではない。各ヘルパは1箇所で定義し
# `**overrides` で1項目だけ差し替える形に統一する——テスト側に第2の値表を作ると、
# 型が変わったときに直す場所が増える。
# ---------------------------------------------------------------------------


def make_bracket(**overrides: object) -> BracketMeasurements:
    values: dict[str, object] = {
        "outline_x_mm": 45.0,
        "outline_y_mm": 40.0,
        "mount_face_to_contact_mm": 60.0,
        "mount_face_to_wheel_center_mm": 32.0,
        "mount_face_reference": "モータ取付面の中心から接地点まで鉛直下向きに測る",
        "mount_hole_count": 4,
        "mount_hole_diameter_mm": 4.2,
        "mount_hole_pitch_mm": 30.0,
    }
    values.update(overrides)
    return BracketMeasurements(**values)  # type: ignore[arg-type]


def make_motor(**overrides: object) -> MotorSpec:
    values: dict[str, object] = {
        "body_diameter_mm": 37.0,
        "body_length_mm": 52.0,
        "shaft_diameter_mm": 6.0,
        "shaft_length_mm": 14.0,
        "shaft_flat_present": True,
    }
    values.update(overrides)
    return MotorSpec(**values)  # type: ignore[arg-type]


def make_wheel(**overrides: object) -> WheelSpec:
    values: dict[str, object] = {
        "nominal_diameter_mm": 60.0,
        "width_mm": 26.0,
        "center_bore_diameter_mm": 12.0,
        "bolt_circle_diameter_mm": 25.0,
        "mount_hole_count": 3,
        "mass_g": 48.0,
    }
    values.update(overrides)
    return WheelSpec(**values)  # type: ignore[arg-type]


def make_hub(**overrides: object) -> HubSpec:
    values: dict[str, object] = {
        "bore_diameter_mm": 6.0,
        "boss_diameter_mm": 14.0,
        "boss_length_mm": 12.0,
        "flange_diameter_mm": 30.0,
        "flange_thickness_mm": 5.0,
        "overall_length_mm": 18.0,
        "set_screw_designation": "M4",
        "mass_g": 22.0,
    }
    values.update(overrides)
    return HubSpec(**values)  # type: ignore[arg-type]


def make_base(**overrides: object) -> BaseSpec:
    values: dict[str, object] = {
        "wheel_count": 3,
        "hub_outer_diameter_mm": 120.0,
        "hub_center_to_mount_face_mm": 101.3,
        "plate_thickness_mm": 6.0,
        "arm_width_mm": 30.0,
        "arm_thickness_mm": 8.0,
        "slot_travel_mm": 4.0,
        "first_wheel_angle_deg": 0.0,
    }
    values.update(overrides)
    return BaseSpec(**values)  # type: ignore[arg-type]


def make_clearance(**overrides: object) -> ClearanceLimits:
    values: dict[str, object] = {
        "min_ground_clearance_mm": 8.0,
        "cable_lowest_offset_mm": 12.0,
        "fastener_protrusion_mm": 3.0,
    }
    values.update(overrides)
    return ClearanceLimits(**values)  # type: ignore[arg-type]


def make_adapter(**overrides: object) -> AdapterSpec:
    values: dict[str, object] = {
        "seat_clearance_mm": 1.0,
        "wall_thickness_mm": 4.0,
        "rise_height_mm": 25.0,
        "retention_point_count": 3,
    }
    values.update(overrides)
    return AdapterSpec(**values)  # type: ignore[arg-type]


def make_battery(**overrides: object) -> BatterySpec:
    values: dict[str, object] = {
        "length_mm": 140.0,
        "width_mm": 45.0,
        "height_mm": 25.0,
        "mass_g": 330.0,
        "tray_wall_thickness_mm": 3.0,
        "hold_height_mm": 20.0,
    }
    values.update(overrides)
    return BatterySpec(**values)  # type: ignore[arg-type]


def make_board(**overrides: object) -> BoardSpec:
    values: dict[str, object] = {
        "deck_x_mm": 150.0,
        "deck_y_mm": 120.0,
        "standoff_height_mm": 8.0,
        "driver_count": 3,
        "cooling_gap_mm": 10.0,
        "mass_g": 260.0,
        "hold_height_mm": 90.0,
    }
    values.update(overrides)
    return BoardSpec(**values)  # type: ignore[arg-type]


def make_power(**overrides: object) -> PowerParams:
    """電源系のヘルパ。**既定はすべて未決（`None`）**である。

    ⚠️ 値の決定はタスク 5.6 であり、本タスクは「決まっていないことを正直に
    表せる」ことだけを固定する。ヘルパにもっともらしい既定を置くと、決定の
    済んだ項目と未決の項目がテストの中で区別できなくなる。
    """
    values: dict[str, object] = {
        "main_switch_present": None,
        "main_switch_position": None,
        "main_switch_height_mm": None,
        "terminal_block_present": None,
        "terminal_block_position": None,
        "terminal_block_length_mm": None,
        "terminal_block_width_mm": None,
        "terminal_block_height_mm": None,
        "terminal_block_mass_g": None,
        "terminal_block_hold_height_mm": None,
        "fuse_holder_position": None,
        "estop_provision": None,
    }
    values.update(overrides)
    return PowerParams(**values)  # type: ignore[arg-type]


def make_stand(**overrides: object) -> StandSpec:
    values: dict[str, object] = {
        "support_span_mm": 70.0,
        "lift_height_mm": 30.0,
        "wheel_rotation_clearance_mm": 10.0,
        "leg_count": 3,
    }
    values.update(overrides)
    return StandSpec(**values)  # type: ignore[arg-type]


def make_joint_local(**overrides: object) -> LocalJointLimits:
    values: dict[str, object] = {
        "min_bearing_area_mm2": 120.0,
        "fastener_length_margin_mm": 3.0,
    }
    values.update(overrides)
    return LocalJointLimits(**values)  # type: ignore[arg-type]


def make_upstream_joint_policy(**overrides: object) -> JointPolicy:
    """上流の継手方針。⚠️ **本 Spec の設定ファイルへ複製しない値**である。"""
    values: dict[str, object] = {
        "bolt_designation": "M3",
        "through_hole_diameter_mm": 3.4,
        "insert_outer_diameter_mm": 4.6,
        "insert_length_mm": 5.7,
        "dowel_diameter_mm": 4.0,
        "min_bearing_area_mm2": 100.0,
    }
    values.update(overrides)
    return JointPolicy(**values)  # type: ignore[arg-type]


def full_provenance(**overrides: Provenance) -> dict[str, Provenance]:
    """`PARAMETER_PATHS` の全パスを覆う出所表を作る（既定は仮値）。

    キー集合の一致が `ChassisParams` の不変条件であるため、テストは常に全パスを
    与える。個別の項目だけを実測にしたいときは `overrides` で差し替える。
    """
    mapping = {path: Provenance.ASSUMED for path in PARAMETER_PATHS}
    mapping.update(overrides)
    return mapping


def make_params(**overrides: object) -> ChassisParams:
    values: dict[str, object] = {
        "bracket": make_bracket(),
        "motor": make_motor(),
        "wheel": make_wheel(),
        "hub": make_hub(),
        "base": make_base(),
        "clearance": make_clearance(),
        "adapter": make_adapter(),
        "battery": make_battery(),
        "board": make_board(),
        "power": make_power(),
        "stand": make_stand(),
        "joint_local": make_joint_local(),
        "provenance": full_provenance(),
    }
    values.update(overrides)
    return ChassisParams(**values)  # type: ignore[arg-type]


COMPONENT_TYPES: dict[str, type] = {
    "bracket": BracketMeasurements,
    "motor": MotorSpec,
    "wheel": WheelSpec,
    "hub": HubSpec,
    "base": BaseSpec,
    "clearance": ClearanceLimits,
    "adapter": AdapterSpec,
    "battery": BatterySpec,
    "board": BoardSpec,
    "power": PowerParams,
    "stand": StandSpec,
    "joint_local": LocalJointLimits,
}

#: 上流 `catch_mechanism.PARAMETER_PATHS` のコンポーネント名。
#: ⚠️ **本 Spec のパス表にこれらが現れてはならない**（要件 1.3 / design.md
#: 「静的検査」）。上流が持つ値を本 Spec の設定ファイルへ再定義した瞬間に、
#: 同じ値が2箇所で食い違う余地が生まれる。
UPSTREAM_COMPONENTS = frozenset(
    {"trash_can", "target_object", "printing", "joint", "rim", "retention"}
)


# パス表がデータクラス木の走査で生成されることを、**本 Spec の型とは無関係な木**で
# 確かめるための合成データクラス（`test_parameter_paths_are_generated_by_walking_an
# _arbitrary_root`）。⚠️ モジュール直下に置く——`from __future__ import annotations`
# の下では注釈が文字列であり、関数内で定義した型は `get_type_hints` から解決できない。


@dataclass(frozen=True, slots=True)
class _SyntheticLeaf:
    alpha_mm: float
    beta_deg: float


@dataclass(frozen=True, slots=True)
class _SyntheticExtendedLeaf:
    alpha_mm: float
    beta_deg: float
    gamma_g: float


@dataclass(frozen=True, slots=True)
class _SyntheticRoot:
    leaf: _SyntheticLeaf
    provenance: dict


@dataclass(frozen=True, slots=True)
class _SyntheticExtendedRoot:
    leaf: _SyntheticExtendedLeaf
    provenance: dict


# ---------------------------------------------------------------------------
# 型の形（12群 ＋ 集約ルート）
# ---------------------------------------------------------------------------


def test_twelve_component_groups_are_aggregated_by_one_root() -> None:
    """design.md `#### Params` の12群が `ChassisParams` に1つだけ集約される。"""
    root_fields = [field.name for field in fields(ChassisParams)]
    assert root_fields == [
        "bracket",
        "motor",
        "wheel",
        "hub",
        "base",
        "clearance",
        "adapter",
        "battery",
        "board",
        "power",
        "stand",
        "joint_local",
        "provenance",
    ]
    hints = get_type_hints(ChassisParams)
    for name, expected in COMPONENT_TYPES.items():
        assert hints[name] is expected


def test_bracket_field_names_match_design_service_interface() -> None:
    """`BracketMeasurements` のフィールド名は design.md の宣言と一致する。"""
    assert [field.name for field in fields(BracketMeasurements)] == [
        "outline_x_mm",
        "outline_y_mm",
        "mount_face_to_contact_mm",
        "mount_face_to_wheel_center_mm",
        "mount_face_reference",
        "mount_hole_count",
        "mount_hole_diameter_mm",
        "mount_hole_pitch_mm",
    ]


@pytest.mark.parametrize(
    "cls",
    [*COMPONENT_TYPES.values(), ChassisParams, MassItem, ParameterPath],
    ids=lambda cls: cls.__name__,
)
def test_all_types_are_frozen_slotted_dataclasses(cls: type) -> None:
    """すべて `frozen=True, slots=True` のデータクラスである（タスク 1.3）。"""
    assert is_dataclass(cls)
    assert cls.__dataclass_params__.frozen is True  # type: ignore[attr-defined]
    assert "__slots__" in cls.__dict__


@pytest.mark.parametrize(
    "cls", [*COMPONENT_TYPES.values(), ChassisParams], ids=lambda cls: cls.__name__
)
def test_no_field_has_a_default(cls: type) -> None:
    """寸法値に既定値を与えない。⚠️ 書き忘れが「もっともらしい数」で埋まらない。"""
    for field in fields(cls):
        assert field.default is MISSING, field.name
        assert field.default_factory is MISSING, field.name


def test_instances_are_immutable_and_value_equal() -> None:
    params = make_params()
    with pytest.raises(FrozenInstanceError):
        params.bracket = make_bracket()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        params.bracket.outline_x_mm = 1.0  # type: ignore[misc]
    with pytest.raises(AttributeError):
        params.__dict__  # noqa: B018
    assert params == make_params()
    assert params != make_params(base=make_base(plate_thickness_mm=7.0))


def test_aggregate_survives_asdict_deepcopy_and_pickle() -> None:
    """集約は直列化できる（タスク 1.4 の識別子算出が最も自然な経路で通る）。"""
    params = make_params()
    assert asdict(params)["bracket"]["outline_x_mm"] == 45.0
    assert deepcopy(params) == params
    assert pickle.loads(pickle.dumps(params)) == params


# ---------------------------------------------------------------------------
# 出所（上流の型をそのまま使う）と最弱の継承
# ---------------------------------------------------------------------------


def test_provenance_is_the_upstream_type_itself() -> None:
    """⚠️ 出所は上流の型を**そのまま**使う。独自の2値を定義しない。"""
    assert params_module.Provenance is catch_mechanism.Provenance
    assert {member.value for member in Provenance} == {"measured", "assumed"}


def test_params_module_does_not_define_its_own_provenance() -> None:
    source = ast.parse(Path(params_module.__file__).read_text(encoding="utf-8"))
    defined = {
        node.name
        for node in ast.walk(source)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef))
    }
    assert "Provenance" not in defined


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
            (Provenance.MEASURED, Provenance.MEASURED, Provenance.MEASURED),
            Provenance.MEASURED,
        ),
        (
            (Provenance.MEASURED, Provenance.MEASURED, Provenance.ASSUMED),
            Provenance.ASSUMED,
        ),
    ],
)
def test_weakest_provenance_covers_the_whole_input_space(
    values: tuple[Provenance, ...], expected: Provenance
) -> None:
    """⚠️ **実測＋仮値の導出は仮値**（要件 1.9 / タスク 1.3 の完了状態）。"""
    assert weakest_provenance(*values) is expected


def test_weakest_provenance_rejects_empty_input() -> None:
    """入力が無ければ導出量の出所は定まらない。⚠️ 空を実測と解釈しない。"""
    with pytest.raises(ParameterError) as excinfo:
        weakest_provenance()
    assert "weakest_provenance" in str(excinfo.value)


def test_weakest_provenance_rejects_non_provenance_input() -> None:
    with pytest.raises(ParameterError) as excinfo:
        weakest_provenance(Provenance.MEASURED, "measured")  # type: ignore[arg-type]
    assert "'measured'" in str(excinfo.value)


def test_weakest_provenance_failure_is_this_specs_error_type() -> None:
    """⚠️ 上流の失敗と取り違えない。呼び違えたのは本 Spec の側である。"""
    with pytest.raises(ChassisMechanismError):
        weakest_provenance()
    assert not issubclass(ParameterError, catch_mechanism.ParameterError)


# ---------------------------------------------------------------------------
# 基準面の記述（⚠️ 105.6mm を破棄した経緯の再発防止）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("blank", ["", " ", "\t", "　", "\n "])
def test_bracket_rejects_blank_mount_face_reference(blank: str) -> None:
    """基準面の記述が空（空白のみを含む）の構築を拒否する。"""
    with pytest.raises(ParameterError) as excinfo:
        make_bracket(mount_face_reference=blank)
    message = str(excinfo.value)
    assert "mount_face_reference" in message
    assert repr(blank) in message


def test_bracket_docstring_records_why_the_reference_is_required() -> None:
    """⚠️ 型の要求の**理由**（破棄した 105.6mm）を docstring に残す。"""
    assert "105.6" in (BracketMeasurements.__doc__ or "")


def test_bracket_accepts_a_real_reference_description() -> None:
    bracket = make_bracket(mount_face_reference="ブラケット外側面の穴中心から軸方向")
    assert bracket.mount_face_reference.strip()


# ---------------------------------------------------------------------------
# 当たり面の下限（⚠️ 緩める方向は許さない）
# ---------------------------------------------------------------------------


def test_local_joint_limits_accept_a_stricter_value() -> None:
    policy = make_upstream_joint_policy(min_bearing_area_mm2=100.0)
    make_joint_local(min_bearing_area_mm2=150.0).validate_against_upstream(policy)


def test_local_joint_limits_accept_an_equal_value() -> None:
    """「上流の下限以上」であり、等しい値は受け入れる。"""
    policy = make_upstream_joint_policy(min_bearing_area_mm2=100.0)
    make_joint_local(min_bearing_area_mm2=100.0).validate_against_upstream(policy)


def test_local_joint_limits_reject_a_looser_value_naming_both_numbers() -> None:
    """⚠️ 上流より緩い下限は拒否する（要件 2.9 / design.md Implementation Notes）。"""
    policy = make_upstream_joint_policy(min_bearing_area_mm2=100.0)
    with pytest.raises(ParameterError) as excinfo:
        make_joint_local(min_bearing_area_mm2=99.9).validate_against_upstream(policy)
    message = str(excinfo.value)
    assert "joint_local.min_bearing_area_mm2" in message
    assert "99.9" in message
    assert "100.0" in message


def test_aggregate_delegates_the_upstream_check() -> None:
    """`ChassisParams` からも1呼び出しで検査できる（`config` が呼ぶ唯一の経路）。"""
    policy = make_upstream_joint_policy(min_bearing_area_mm2=100.0)
    make_params().validate_against_upstream(policy)
    loose = make_params(joint_local=make_joint_local(min_bearing_area_mm2=10.0))
    with pytest.raises(ParameterError):
        loose.validate_against_upstream(policy)


def test_upstream_check_rejects_a_non_policy_argument() -> None:
    with pytest.raises(ParameterError):
        make_joint_local().validate_against_upstream(object())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 数値の構築時検証（負・0・非有限を項目名と値つきで拒否）
# ---------------------------------------------------------------------------

POSITIVE_LENGTH_CASES = [
    (make_bracket, "outline_x_mm"),
    (make_bracket, "mount_face_to_contact_mm"),
    (make_bracket, "mount_face_to_wheel_center_mm"),
    (make_bracket, "mount_hole_diameter_mm"),
    (make_bracket, "mount_hole_pitch_mm"),
    (make_motor, "body_diameter_mm"),
    (make_motor, "shaft_diameter_mm"),
    (make_wheel, "nominal_diameter_mm"),
    (make_wheel, "width_mm"),
    (make_wheel, "mass_g"),
    (make_hub, "bore_diameter_mm"),
    (make_hub, "flange_thickness_mm"),
    (make_hub, "mass_g"),
    (make_base, "hub_outer_diameter_mm"),
    (make_base, "plate_thickness_mm"),
    (make_base, "arm_width_mm"),
    (make_base, "arm_thickness_mm"),
    (make_clearance, "min_ground_clearance_mm"),
    (make_adapter, "wall_thickness_mm"),
    (make_adapter, "rise_height_mm"),
    (make_battery, "length_mm"),
    (make_battery, "mass_g"),
    (make_battery, "hold_height_mm"),
    (make_board, "deck_x_mm"),
    (make_board, "standoff_height_mm"),
    (make_board, "mass_g"),
    (make_board, "hold_height_mm"),
    (make_stand, "support_span_mm"),
    (make_stand, "lift_height_mm"),
    (make_joint_local, "min_bearing_area_mm2"),
]


@pytest.mark.parametrize(
    ("factory", "field_name"),
    POSITIVE_LENGTH_CASES,
    ids=[f"{factory.__name__}-{name}" for factory, name in POSITIVE_LENGTH_CASES],
)
@pytest.mark.parametrize("bad", [-1.0, -0.5, 0.0, math.nan, math.inf, -math.inf])
def test_non_positive_values_are_rejected_with_name_and_value(
    factory: object, field_name: str, bad: float
) -> None:
    """負の寸法・0・非有限は**項目名と値つきで**拒否される（タスク 1.3 完了状態）。"""
    with pytest.raises(ParameterError) as excinfo:
        factory(**{field_name: bad})  # type: ignore[operator]
    message = str(excinfo.value)
    assert field_name in message
    assert repr(bad) in message


@pytest.mark.parametrize(
    ("factory", "field_name"),
    [
        (make_base, "slot_travel_mm"),
        (make_adapter, "seat_clearance_mm"),
        (make_clearance, "cable_lowest_offset_mm"),
        (make_clearance, "fastener_protrusion_mm"),
        (make_board, "cooling_gap_mm"),
        (make_stand, "wheel_rotation_clearance_mm"),
        (make_joint_local, "fastener_length_margin_mm"),
    ],
)
def test_zero_is_allowed_where_zero_is_a_decision(
    factory: object, field_name: str
) -> None:
    """0 が「余裕を取らない」という意味を持つ項目は 0 を受け入れる。"""
    factory(**{field_name: 0.0})  # type: ignore[operator]


@pytest.mark.parametrize(
    ("factory", "field_name"),
    [
        (make_base, "slot_travel_mm"),
        (make_adapter, "seat_clearance_mm"),
        (make_clearance, "cable_lowest_offset_mm"),
        (make_board, "cooling_gap_mm"),
        (make_joint_local, "fastener_length_margin_mm"),
    ],
)
@pytest.mark.parametrize("bad", [-0.1, math.nan, math.inf])
def test_negative_values_are_rejected_even_where_zero_is_allowed(
    factory: object, field_name: str, bad: float
) -> None:
    with pytest.raises(ParameterError) as excinfo:
        factory(**{field_name: bad})  # type: ignore[operator]
    assert field_name in str(excinfo.value)
    assert repr(bad) in str(excinfo.value)


@pytest.mark.parametrize(
    ("factory", "field_name", "bad"),
    [
        (make_bracket, "mount_hole_count", 0),
        (make_bracket, "mount_hole_count", -1),
        (make_bracket, "mount_hole_count", True),
        (make_bracket, "mount_hole_count", 4.0),
        (make_wheel, "mount_hole_count", 0),
        (make_base, "wheel_count", 2),
        (make_base, "wheel_count", True),
        (make_adapter, "retention_point_count", 0),
        (make_board, "driver_count", 0),
        (make_stand, "leg_count", 0),
    ],
)
def test_counts_are_rejected_with_name_and_value(
    factory: object, field_name: str, bad: object
) -> None:
    """個数は整数であり、`bool` を黙って個数として通さない。"""
    with pytest.raises(ParameterError) as excinfo:
        factory(**{field_name: bad})  # type: ignore[operator]
    message = str(excinfo.value)
    assert field_name in message
    assert repr(bad) in message


def test_wheel_count_below_three_is_rejected() -> None:
    """⚠️ 3輪オムニの前提（design.md Layout Preconditions: `wheel_count >= 3`）。"""
    with pytest.raises(ParameterError) as excinfo:
        make_base(wheel_count=2)
    assert "wheel_count" in str(excinfo.value)


@pytest.mark.parametrize("bad", [360.0, -360.0, 720.0, math.nan, math.inf])
def test_angle_outside_the_allowed_range_is_rejected(bad: float) -> None:
    """角度の値域は `-360 < x < 360`（design.md `#### Params` Preconditions）。"""
    with pytest.raises(ParameterError) as excinfo:
        make_base(first_wheel_angle_deg=bad)
    assert "first_wheel_angle_deg" in str(excinfo.value)
    assert repr(bad) in str(excinfo.value)


@pytest.mark.parametrize("good", [0.0, -359.9, 359.9, 120.0])
def test_angle_inside_the_allowed_range_is_accepted(good: float) -> None:
    assert make_base(first_wheel_angle_deg=good).first_wheel_angle_deg == good


@pytest.mark.parametrize(
    ("factory", "field_name"),
    [
        (make_hub, "set_screw_designation"),
    ],
)
@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_designations_are_rejected(
    factory: object, field_name: str, blank: str
) -> None:
    with pytest.raises(ParameterError) as excinfo:
        factory(**{field_name: blank})  # type: ignore[operator]
    assert field_name in str(excinfo.value)


# ---------------------------------------------------------------------------
# 形が成立するための大小関係
# ---------------------------------------------------------------------------


def test_wheel_bolt_circle_must_fit_inside_the_wheel() -> None:
    with pytest.raises(ParameterError) as excinfo:
        make_wheel(bolt_circle_diameter_mm=61.0)
    message = str(excinfo.value)
    assert "bolt_circle_diameter_mm" in message
    assert "nominal_diameter_mm" in message


def test_wheel_center_bore_must_fit_inside_the_bolt_circle() -> None:
    with pytest.raises(ParameterError) as excinfo:
        make_wheel(center_bore_diameter_mm=26.0)
    assert "center_bore_diameter_mm" in str(excinfo.value)


def test_hub_bore_must_be_smaller_than_the_boss() -> None:
    with pytest.raises(ParameterError) as excinfo:
        make_hub(bore_diameter_mm=14.0)
    assert "bore_diameter_mm" in str(excinfo.value)


def test_hub_flange_must_not_be_smaller_than_the_boss() -> None:
    with pytest.raises(ParameterError) as excinfo:
        make_hub(flange_diameter_mm=13.0)
    assert "flange_diameter_mm" in str(excinfo.value)


def test_hub_overall_length_must_cover_the_flange() -> None:
    with pytest.raises(ParameterError) as excinfo:
        make_hub(overall_length_mm=4.0)
    assert "overall_length_mm" in str(excinfo.value)


def test_motor_shaft_must_be_thinner_than_the_body() -> None:
    with pytest.raises(ParameterError) as excinfo:
        make_motor(shaft_diameter_mm=40.0)
    assert "shaft_diameter_mm" in str(excinfo.value)


def test_stand_leg_count_must_match_the_wheel_count() -> None:
    """⚠️ 3脚独立（決定 5）。輪ごとに載せ降ろしできることが前提である。"""
    with pytest.raises(ParameterError) as excinfo:
        make_params(stand=make_stand(leg_count=4))
    message = str(excinfo.value)
    assert "stand.leg_count" in message
    assert "base.wheel_count" in message
    assert "4" in message and "3" in message


# ---------------------------------------------------------------------------
# 電源系（8.1, 8.2, 8.5, 8.7）— 未決を未決として表せること
# ---------------------------------------------------------------------------


def test_power_params_can_represent_undecided_values() -> None:
    """⚠️ 値の決定はタスク 5.6。既定で決定に見える値を持たせない。"""
    power = make_power()
    assert power.main_switch_present is None
    assert power.terminal_block_present is None
    assert power.fuse_holder_position is None
    assert power.estop_provision is None


def test_power_params_can_represent_the_recorded_decisions() -> None:
    """決定 6 / 決定 7 の内容を値として保持できる（要件 8.1, 8.2, 8.5, 8.7）。"""
    power = make_power(
        main_switch_present=True,
        main_switch_position="基板トレイ側面",
        main_switch_height_mm=120.0,
        terminal_block_present=True,
        terminal_block_position="基板トレイ最下段",
        terminal_block_length_mm=60.0,
        terminal_block_width_mm=20.0,
        terminal_block_height_mm=18.0,
        terminal_block_mass_g=35.0,
        terminal_block_hold_height_mm=60.0,
        fuse_holder_position="バッテリ直近（端子台より上流）",
        estop_provision="基板トレイにねじ穴と配線引き出しを残す",
    )
    assert power.main_switch_present is True
    assert power.estop_provision


@pytest.mark.parametrize(
    "field_name",
    [
        "main_switch_height_mm",
        "terminal_block_length_mm",
        "terminal_block_mass_g",
        "terminal_block_hold_height_mm",
    ],
)
@pytest.mark.parametrize("bad", [0.0, -1.0, math.nan])
def test_power_numeric_values_when_given_must_be_positive(
    field_name: str, bad: float
) -> None:
    with pytest.raises(ParameterError) as excinfo:
        make_power(**{field_name: bad})
    assert field_name in str(excinfo.value)
    assert repr(bad) in str(excinfo.value)


@pytest.mark.parametrize(
    "field_name",
    ["main_switch_position", "terminal_block_position", "fuse_holder_position", "estop_provision"],
)
def test_power_descriptions_when_given_must_not_be_blank(field_name: str) -> None:
    """⚠️ 空白だけの記述は決定ではない。未決なら `None` と書く。"""
    with pytest.raises(ParameterError) as excinfo:
        make_power(**{field_name: "   "})
    assert field_name in str(excinfo.value)


@pytest.mark.parametrize("field_name", ["main_switch_present", "terminal_block_present"])
def test_power_presence_must_be_bool_or_none(field_name: str) -> None:
    with pytest.raises(ParameterError) as excinfo:
        make_power(**{field_name: "yes"})
    assert field_name in str(excinfo.value)


def test_power_rejects_a_position_for_an_absent_switch() -> None:
    """⚠️ 「設けない」と「その位置」は同時に成立しない。"""
    with pytest.raises(ParameterError) as excinfo:
        make_power(main_switch_present=False, main_switch_position="基板トレイ側面")
    message = str(excinfo.value)
    assert "main_switch_present" in message
    assert "main_switch_position" in message


def test_power_rejects_dimensions_for_an_absent_terminal_block() -> None:
    with pytest.raises(ParameterError) as excinfo:
        make_power(terminal_block_present=False, terminal_block_length_mm=60.0)
    assert "terminal_block_present" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 搭載物の質量と保持高さ（要件 7.8）
# ---------------------------------------------------------------------------


def test_mass_items_carry_mass_and_hold_height() -> None:
    """合成重心の見積もり（タスク 2.1）の**入力側**をここで確定させる。"""
    items = make_params().mass_items()
    by_name = {item.name: item for item in items}
    assert by_name["battery"].mass_g == 330.0
    assert by_name["battery"].hold_height_mm == 20.0
    assert by_name["board"].mass_g == 260.0
    assert by_name["board"].hold_height_mm == 90.0
    assert all(isinstance(item, MassItem) for item in items)


def test_mass_items_include_the_terminal_block_once_decided() -> None:
    """決定 7: 端子台の質量と保持高さは合成重心の見積もりへ算入する。"""
    params = make_params(
        power=make_power(
            terminal_block_present=True,
            terminal_block_mass_g=35.0,
            terminal_block_hold_height_mm=60.0,
        )
    )
    names = [item.name for item in params.mass_items()]
    assert "power_terminal_block" in names


def test_mass_items_omit_the_terminal_block_while_undecided() -> None:
    """⚠️ 未決の値を 0 として算入しない（0 は「質量が無い」という主張になる）。"""
    names = [item.name for item in make_params().mass_items()]
    assert "power_terminal_block" not in names


def test_mass_item_rejects_non_positive_mass_and_blank_name() -> None:
    with pytest.raises(ParameterError) as excinfo:
        MassItem(name="battery", mass_g=0.0, hold_height_mm=20.0)
    assert "mass_g" in str(excinfo.value)
    with pytest.raises(ParameterError):
        MassItem(name=" ", mass_g=1.0, hold_height_mm=20.0)


# ---------------------------------------------------------------------------
# PARAMETER_PATHS（⚠️ データクラス木の走査で生成する。手書きの表を持たない）
# ---------------------------------------------------------------------------


def test_parameter_paths_cover_every_leaf_of_the_dataclass_tree() -> None:
    expected = {
        f"{component}.{field.name}"
        for component, cls in COMPONENT_TYPES.items()
        for field in fields(cls)
    }
    assert set(PARAMETER_PATHS) == expected


def test_parameter_paths_exclude_the_provenance_table_itself() -> None:
    assert not any(path.startswith("provenance") for path in PARAMETER_PATHS)


def test_parameter_paths_are_generated_by_walking_an_arbitrary_root() -> None:
    """⚠️ 手書きの一覧ではないことを、**別の木を渡して**確かめる。

    フィールドを1つ足したときに表が追随することが、この表を生成する唯一の理由で
    ある。列挙リテラルを持つ実装はこのテストを通らない。
    """
    before = params_module._build_parameter_paths(_SyntheticRoot)
    after = params_module._build_parameter_paths(_SyntheticExtendedRoot)
    assert set(before) == {"leaf.alpha_mm", "leaf.beta_deg"}
    assert set(after) == {"leaf.alpha_mm", "leaf.beta_deg", "leaf.gamma_g"}
    assert after["leaf.gamma_g"].unit == "g"


def test_parameter_paths_entries_describe_their_leaf() -> None:
    entry = PARAMETER_PATHS["bracket.mount_face_to_contact_mm"]
    assert isinstance(entry, ParameterPath)
    assert entry.path == "bracket.mount_face_to_contact_mm"
    assert entry.component == "bracket"
    assert entry.field_name == "mount_face_to_contact_mm"
    assert entry.unit == "mm"
    assert entry.value_types == (float,)
    assert entry.optional is False


@pytest.mark.parametrize(
    ("path", "unit"),
    [
        ("bracket.outline_x_mm", "mm"),
        ("base.first_wheel_angle_deg", "deg"),
        ("joint_local.min_bearing_area_mm2", "mm^2"),
        ("battery.mass_g", "g"),
        ("base.wheel_count", ""),
        ("bracket.mount_face_reference", ""),
        ("motor.shaft_flat_present", ""),
    ],
)
def test_units_are_derived_from_the_field_name_suffix(path: str, unit: str) -> None:
    assert PARAMETER_PATHS[path].unit == unit


def test_undecided_power_entries_are_marked_optional() -> None:
    """未決を許す項目だけが `optional`。⚠️ 寸法は未決を許さない。"""
    optional = {path for path, entry in PARAMETER_PATHS.items() if entry.optional}
    assert optional == {
        path for path in PARAMETER_PATHS if path.startswith("power.")
    }
    assert PARAMETER_PATHS["power.main_switch_present"].value_types == (bool,)
    assert PARAMETER_PATHS["power.main_switch_position"].value_types == (str,)


def test_parameter_paths_are_deterministic_and_sorted_by_component_order() -> None:
    """パス表の並びは定義順であり、再構築しても一致する（識別子の安定性の前提）。"""
    assert list(PARAMETER_PATHS) == list(
        params_module._build_parameter_paths(ChassisParams)
    )


def test_parameter_paths_are_immutable() -> None:
    with pytest.raises(TypeError):
        PARAMETER_PATHS["bracket.outline_x_mm"] = None  # type: ignore[index]


def test_parameter_paths_contain_no_upstream_component_name() -> None:
    """⚠️ 上流が持つ値を本 Spec が再定義していないことの機械的な担保（要件 1.3）。"""
    components = {entry.component for entry in PARAMETER_PATHS.values()}
    assert components & UPSTREAM_COMPONENTS == set()


# ---------------------------------------------------------------------------
# provenance（要件 1.2 / design.md Invariants: キー集合 == PARAMETER_PATHS）
# ---------------------------------------------------------------------------


def test_every_parameter_has_a_provenance() -> None:
    params = make_params()
    assert set(params.provenance) == set(PARAMETER_PATHS)


def test_unknown_provenance_key_is_rejected_by_name() -> None:
    bad = full_provenance()
    bad["bracket.no_such_field"] = Provenance.MEASURED
    with pytest.raises(ParameterError) as excinfo:
        make_params(provenance=bad)
    assert "bracket.no_such_field" in str(excinfo.value)


def test_missing_provenance_key_is_rejected_by_name() -> None:
    bad = full_provenance()
    del bad["bracket.outline_x_mm"]
    with pytest.raises(ParameterError) as excinfo:
        make_params(provenance=bad)
    assert "bracket.outline_x_mm" in str(excinfo.value)


def test_non_provenance_value_is_rejected() -> None:
    bad = full_provenance()
    bad["bracket.outline_x_mm"] = "measured"  # type: ignore[assignment]
    with pytest.raises(ParameterError) as excinfo:
        make_params(provenance=bad)
    assert "bracket.outline_x_mm" in str(excinfo.value)


def test_provenance_is_copied_so_later_mutation_cannot_leak_in() -> None:
    """構築を通った集約は以降の層で再検証を要さない（design.md Postconditions）。"""
    source = full_provenance()
    params = make_params(provenance=source)
    source["bracket.outline_x_mm"] = Provenance.MEASURED
    assert params.provenance["bracket.outline_x_mm"] is Provenance.ASSUMED
    assert type(params.provenance) is dict


def test_provenance_records_which_values_are_measured() -> None:
    """要件 1.9: 実測で置き換えられていない公称寸法は仮値のまま残る。"""
    params = make_params(
        provenance=full_provenance(
            **{"bracket.mount_face_to_contact_mm": Provenance.MEASURED}
        )
    )
    assert params.provenance["bracket.mount_face_to_contact_mm"] is Provenance.MEASURED
    assert params.provenance["wheel.nominal_diameter_mm"] is Provenance.ASSUMED
    derived = weakest_provenance(
        params.provenance["bracket.mount_face_to_contact_mm"],
        params.provenance["wheel.nominal_diameter_mm"],
    )
    assert derived is Provenance.ASSUMED


def test_provenance_must_be_a_mapping() -> None:
    with pytest.raises(ParameterError):
        make_params(provenance=[("bracket.outline_x_mm", Provenance.MEASURED)])


# ---------------------------------------------------------------------------
# 依存の制約（design.md「Allowed Dependencies」/「静的検査」）
# ---------------------------------------------------------------------------


def test_params_module_reaches_upstream_only_through_the_package_root() -> None:
    """⚠️ `catch_mechanism.params` 等の**内部モジュール**へ直接 import しない。"""
    source = ast.parse(Path(params_module.__file__).read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(source):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)
    upstream = {name for name in modules if name.split(".")[0] == "catch_mechanism"}
    assert upstream == {"catch_mechanism"}


def test_params_module_imports_no_sibling_package() -> None:
    source = ast.parse(Path(params_module.__file__).read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(source):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    forbidden = {
        "prediction_core",
        "trajectory_sim",
        "sensing_foundation",
        "world_frame_calibration",
        "flying_object_tracking",
        "m1_validation",
        "build123d",
    }
    assert roots & forbidden == set()

    # 依存方向（design.md「Dependency Direction」: errors → params → config …）。
    # ⚠️ `params` が自パッケージから読んでよいのは左隣の `errors` だけである。
    # `config` 以降を読むと依存が上位方向へ逆流する。
    own_submodules = {
        node.module.split(".", 1)[1].split(".")[0]
        for node in ast.walk(source)
        if isinstance(node, ast.ImportFrom)
        and node.level == 0
        and node.module
        and node.module.startswith("chassis_mechanism.")
    }
    assert own_submodules == {"errors"}


def test_params_module_leaves_no_unfinished_marker() -> None:
    text = Path(params_module.__file__).read_text(encoding="utf-8")
    for marker in ("TODO", "FIXME", "TBD", "XXX"):
        assert marker not in text
