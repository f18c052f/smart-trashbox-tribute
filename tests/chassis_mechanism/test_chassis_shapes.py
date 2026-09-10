"""整備スタンドの幾何の導出と形状生成の入口（タスク 3.1 / 要件 5.1-5.6, 5.8）。

本ファイルは**形状ライブラリを必要としない側**を持つ。

- 設計入力の限定（要件 5.2）——スタンドが読む値が「配置半径と現物採寸値」だけで
  あることを、型（`StandInputs`）と静的走査（`ast`）の両方で固定する
- 幾何の導出（`stand_geometry`）が算術だけで完結し、⚠️ **形状ライブラリの無い
  環境でも全数値と成立条件を評価できる**こと
- 形状ライブラリ非導入の環境で `build_parts` が `CadUnavailableError` になり、
  ⚠️ **成功にしない**こと（design.md `#### Shapes` Preconditions）

⚠️ **実形状（ソリッド）に対する不変条件は `test_chassis_invariants.py` が持つ**
（design.md `#### Shapes`「不変条件（`test_chassis_invariants.py` が検査）」が
スタンドの3件——3輪の最下点・ホイール外周との隙間・支持面と駆動ベース下面——を
名指しでそちらへ置いている）。本ファイルはその手前、**形状を作る前に決まる**側で
ある。

ファイル名について: `tests/` に `__init__.py` が無くテストモジュール名が
セッション全体でフラットであるため、`test_chassis_` 接頭辞を付ける
（design.md「Directory Structure」）。
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest
from catch_mechanism import Envelope, check_envelope

from chassis_mechanism import shapes as shapes_module
from chassis_mechanism.config import load_params
from chassis_mechanism.errors import (
    CadUnavailableError,
    ClearanceError,
    GeometryError,
)
from chassis_mechanism.layout import derive_layout
from chassis_mechanism.shapes import (
    CABLE_ROUTE_NAMES,
    MIN_HAND_ACCESS_MM,
    PART_NAMES,
    AdapterGeometry,
    BatteryTrayGeometry,
    BuiltPart,
    CableGuideGeometry,
    DeckStackGeometry,
    DriveBaseGeometry,
    StandGeometry,
    StandInputs,
    adapter_geometry,
    check_before_build,
    battery_tray_geometry,
    cable_guide_geometry,
    build_parts,
    build_service_stand_legs,
    deck_stack_geometry,
    drive_base_geometry,
    measure_part,
    part_masses,
    part_names,
    stand_geometry,
    stand_inputs,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SHAPES_SOURCE = REPO_ROOT / "src" / "chassis_mechanism" / "shapes.py"

# ---------------------------------------------------------------------------
# 形状ライブラリの有無（design.md「Allowed Dependencies」/「Dependency Direction」）。
# ⚠️ **モジュール全体を `pytest.importorskip` で落とさない。** 本ファイルの検査の
# ほとんどは形状ライブラリを必要とせず、非導入環境でも完了しなければならない。
# ---------------------------------------------------------------------------

try:  # pragma: no cover - 環境によって分岐する
    import build123d as _build123d
except ImportError:  # pragma: no cover - `cad` extra 非導入の環境
    _build123d = None

requires_cad = pytest.mark.skipif(
    _build123d is None,
    reason="形状ライブラリ（build123d / `cad` extra）が未導入である。"
    "design.md「Allowed Dependencies」により、形状生成を除く検査は"
    "この環境でも完了する。",
)


@pytest.fixture(scope="module")
def shipped() -> tuple[object, object]:
    """出荷の寸法パラメータと、そこから導いた幾何（⚠️ 読むだけ）。"""
    params = load_params()
    return params, derive_layout(params)


@pytest.fixture(scope="module")
def inputs(shipped: tuple[object, object]) -> StandInputs:
    params, layout = shipped
    return stand_inputs(params, layout)  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def geometry(inputs: StandInputs) -> StandGeometry:
    return stand_geometry(inputs)


# ---------------------------------------------------------------------------
# 1. 設計入力の限定（要件 5.2）
# ---------------------------------------------------------------------------

PERMITTED_INPUT_FIELDS: frozenset[str] = frozenset(
    {
        "base_radius_mm",
        "wheel_angles_deg",
        "wheel_diameter_mm",
        "wheel_width_mm",
        "motor_body_diameter_mm",
        "motor_body_bottom_height_mm",
        "axle_center_height_mm",
        "mount_face_height_mm",
        "support_span_mm",
        "lift_height_mm",
        "wheel_rotation_clearance_mm",
        "leg_count",
    }
)
"""整備スタンドが読んでよい値（要件 5.2: 配置半径と現物採寸値に限る）。

配置半径と取付角は `ChassisLayout`（要件 3.3 の導出）から、ホイール・モータの
寸法と鉛直スタックは現物採寸値から来る。⚠️ **ゴミ箱・トレイ・電源・アダプタの
確定を待たない**（タスク 3.1）。
"""

FORBIDDEN_COMPONENTS: frozenset[str] = frozenset(
    {"adapter", "battery", "board", "power", "trash_can", "rim", "retention"}
)
"""スタンドの構築が触れてはならないコンポーネント（要件 5.2）。"""

STAND_FUNCTIONS: frozenset[str] = frozenset(
    {"stand_inputs", "stand_geometry", "build_service_stand_legs"}
)


def _module_ast() -> ast.Module:
    return ast.parse(SHAPES_SOURCE.read_text(encoding="utf-8"))


def test_stand_inputs_carry_only_the_permitted_design_inputs() -> None:
    """`StandInputs` のフィールドが許された入力ちょうどである（要件 5.2）。

    ⚠️ **これが要件 5.2 の機械的な担保である。** 構築関数は `ResolvedParams` では
    なく本型を受け取るため、ゴミ箱・トレイ・電源の値は**型の上で到達できない**。
    """
    import dataclasses

    assert {field.name for field in dataclasses.fields(StandInputs)} == (
        PERMITTED_INPUT_FIELDS
    )


def test_stand_construction_never_reads_the_forbidden_components() -> None:
    """スタンドの導出・構築が禁止コンポーネントの属性を1つも読まない（要件 5.2）。"""
    offenders: list[str] = []
    for node in ast.walk(_module_ast()):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in STAND_FUNCTIONS:
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Attribute) and child.attr in FORBIDDEN_COMPONENTS:
                offenders.append(f"{node.name} -> .{child.attr} (line {child.lineno})")
    assert offenders == [], f"整備スタンドが禁止入力を読んでいる: {offenders}"


def test_build_service_stand_legs_takes_stand_inputs_not_the_whole_params() -> None:
    """構築関数が受け取るのは `StandInputs` と造形制約だけである（入力の遮断）。

    ⚠️ `ResolvedParams` / `ChassisParams` を受け取らない形にしてあることが要件 5.2
    の担保である——受け取れば、ゴミ箱・トレイ・電源の値へ手が届いてしまう。
    造形制約（`PrintingConstraints`）は設計入力ではなく**造形可能性の関門**であり
    （design.md `#### Shapes`「構築の前に `check_material` / `check_envelope` を
    通す」）、要件 5.2 が限定するのは形を決める入力の側である。
    """
    import inspect

    signature = inspect.signature(build_service_stand_legs)
    annotations = [
        parameter.annotation for parameter in signature.parameters.values()
    ]
    assert annotations == ["StandInputs", "PrintingConstraints"], annotations


def test_stand_inputs_are_read_from_the_layout_and_the_measured_values(
    shipped: tuple[object, object], inputs: StandInputs
) -> None:
    """入力が幾何の導出結果と寸法パラメータの値そのものである。"""
    params, layout = shipped
    chassis = params.chassis  # type: ignore[attr-defined]
    assert inputs.base_radius_mm == layout.base_radius_mm  # type: ignore[attr-defined]
    assert inputs.wheel_angles_deg == layout.wheel_angles_deg  # type: ignore[attr-defined]
    assert inputs.wheel_diameter_mm == chassis.wheel.nominal_diameter_mm
    assert inputs.wheel_width_mm == chassis.wheel.width_mm
    assert inputs.motor_body_diameter_mm == chassis.motor.body_diameter_mm
    assert (
        inputs.motor_body_bottom_height_mm
        == layout.vertical.motor_body_bottom_height_mm  # type: ignore[attr-defined]
    )
    assert inputs.mount_face_height_mm == layout.vertical.mount_face_height_mm  # type: ignore[attr-defined]
    assert inputs.support_span_mm == chassis.stand.support_span_mm
    assert inputs.lift_height_mm == chassis.stand.lift_height_mm
    assert inputs.wheel_rotation_clearance_mm == chassis.stand.wheel_rotation_clearance_mm
    assert inputs.leg_count == chassis.stand.leg_count


# ---------------------------------------------------------------------------
# 2. 幾何の導出（形状ライブラリを要さない）
# ---------------------------------------------------------------------------


def test_the_wheel_hangs_at_the_lift_height_and_the_trough_keeps_the_clearance(
    inputs: StandInputs, geometry: StandGeometry
) -> None:
    """持ち上げ高さと回転の隙間が、そのまま谷の高さを決める（要件 5.4, 5.5）。

    ⚠️ 車軸の高さは鉛直スタックが決める（`axle_center_height_mm`）。実測前の
    出荷値ではそれが公称半径と一致するため、ホイール最下点は持ち上げ高さその
    ものになる——⚠️ **一致は偶然であって定義ではない**。実測で車軸が下がった
    場合は次のテストが固定する。
    """
    radius = inputs.wheel_diameter_mm / 2.0
    assert geometry.wheel_center_height_mm == (
        inputs.lift_height_mm + inputs.axle_center_height_mm
    )
    assert geometry.wheel_bottom_height_mm == geometry.wheel_center_height_mm - radius
    assert geometry.wheel_bottom_height_mm == (
        inputs.lift_height_mm + inputs.axle_center_height_mm - radius
    )
    assert geometry.trough_floor_height_mm == (
        geometry.wheel_bottom_height_mm - inputs.wheel_rotation_clearance_mm
    )
    # ⚠️ 受け面はホイールの**等距離面**である（半径 R + 隙間、車軸と同軸）。
    assert geometry.socket_radius_mm == radius + inputs.wheel_rotation_clearance_mm
    assert geometry.socket_half_width_mm == (
        inputs.wheel_width_mm / 2.0 + inputs.wheel_rotation_clearance_mm
    )


def test_the_trough_follows_the_measured_axle_height_not_the_nominal_radius(
    inputs: StandInputs,
) -> None:
    """車軸が公称半径より下がっても、隙間は寸法パラメータのままである。

    ⚠️ **タスク 2.4 が実効転がり半径を実測へ置き換えると必ず起きる状態である。**
    `layout` の鉛直スタックは車軸中心とモータ胴体下面を同じ δ だけ下げる。谷を
    `lift + 公称半径` で切ると谷だけが δ 高い位置に残り、ホイール外周と台の隙間が
    δ 狭くなる（タスク 3.1 の観測可能な完了状態が出荷値でだけ成り立つ状態）。

    ⚠️ **谷の半径は公称のままである。** 台上のホイールは無荷重であり、縮むのは
    接地側だけである——追随するのは車軸の**高さ**であって樋の**半径**ではない。
    """
    import dataclasses

    radius = inputs.wheel_diameter_mm / 2.0
    clearance = inputs.wheel_rotation_clearance_mm
    drop = 1.0
    lowered = dataclasses.replace(
        inputs,
        axle_center_height_mm=inputs.axle_center_height_mm - drop,
        motor_body_bottom_height_mm=inputs.motor_body_bottom_height_mm - drop,
    )
    lowered_geometry = stand_geometry(lowered)

    assert lowered_geometry.wheel_center_height_mm == (
        lowered.lift_height_mm + lowered.axle_center_height_mm
    )
    assert lowered_geometry.wheel_bottom_height_mm == (
        lowered_geometry.wheel_center_height_mm - radius
    )
    # ⚠️ **これが観測可能な完了状態そのものである。**
    assert (
        lowered_geometry.wheel_bottom_height_mm
        - lowered_geometry.trough_floor_height_mm
    ) == clearance
    # 谷の半径（＝ホイールとの等距離面）は公称のまま動かない。
    assert lowered_geometry.socket_radius_mm == radius + clearance
    # 支持面も同じ δ だけ下がる（機体全体が δ 低く座る）。
    assert lowered_geometry.support_pad_height_mm == (
        lowered.motor_body_bottom_height_mm + lowered.lift_height_mm
    )


def test_the_socket_stops_at_or_below_the_axle_so_the_rotation_stays_visible(
    inputs: StandInputs, geometry: StandGeometry
) -> None:
    """受け面の上端が車軸高さを超えない（要件 5.8: 回転方向が目視できる）。"""
    assert geometry.socket_top_height_mm == (
        geometry.trough_floor_height_mm + inputs.support_span_mm
    )
    assert geometry.socket_top_height_mm <= geometry.wheel_center_height_mm
    assert geometry.top_height_mm == geometry.socket_top_height_mm


def test_the_retention_bearing_area_matches_the_formula_recorded_by_joints(
    inputs: StandInputs, geometry: StandGeometry
) -> None:
    """拘束の当たり面が `joints.CONTACT_BEARING_AREA_FORMULA` と一致する（要件 5.6）。

    ⚠️ **同じ量を2箇所で別々に定義しない。** `joints` は
    `wheel.width_mm * stand.support_span_mm` を接合部の当たり面として記録して
    おり、形状の側が別の値を実現していれば、記録は形状を説明しない。
    """
    from chassis_mechanism.joints import CONTACT_BEARING_AREA_FORMULA, derive_joints

    params = load_params()
    layout = derive_layout(params)
    assert CONTACT_BEARING_AREA_FORMULA == "wheel.width_mm * stand.support_span_mm"
    assert geometry.retention_bearing_area_mm2 == (
        inputs.wheel_width_mm * inputs.support_span_mm
    )
    stand_joints = [
        joint for joint in derive_joints(layout, params) if "service_stand" in joint.name
    ]
    assert len(stand_joints) == inputs.leg_count
    for joint in stand_joints:
        assert joint.bearing_area_mm2 == geometry.retention_bearing_area_mm2


def test_the_support_pad_never_reaches_the_drive_base_underside(
    inputs: StandInputs, geometry: StandGeometry
) -> None:
    """支持面が駆動ベース下面より下にある（要件 5.3 / タスク 3.1 の完了状態）。

    ⚠️ **脚のどの点も駆動ベース下面の高さへ届かない。** 支持は駆動ベース端部の
    駆動ユニット下面で行い、ブラケット・締結の頭・配線が並ぶベース下面には触れない。
    """
    on_stand_underside_mm = inputs.mount_face_height_mm + inputs.lift_height_mm
    assert geometry.support_pad_height_mm == (
        inputs.motor_body_bottom_height_mm + inputs.lift_height_mm
    )
    assert geometry.support_pad_height_mm < on_stand_underside_mm
    assert geometry.top_height_mm < on_stand_underside_mm


def test_the_support_pad_sits_inboard_of_the_wheel_and_under_the_drive_unit(
    inputs: StandInputs, geometry: StandGeometry
) -> None:
    """支持パッドがホイールより内側、駆動ユニットの下にある（要件 5.3）。

    局所座標は「原点＝ホイール中心の真下の床面、+x＝機体外向き」である。
    """
    assert geometry.support_pad_outer_x_mm == -geometry.socket_half_width_mm
    assert geometry.support_pad_inner_x_mm < geometry.support_pad_outer_x_mm
    # パッドはホイールの内側面（局所 -width/2）よりさらに内側にある。
    assert geometry.support_pad_outer_x_mm < -inputs.wheel_width_mm / 2.0
    # パッドの半径方向の広がりはモータ胴体の内側で収まる（駆動ユニットの下）。
    assert geometry.support_pad_outer_x_mm - geometry.support_pad_inner_x_mm <= (
        inputs.motor_body_diameter_mm * 2.0
    )


def test_three_independent_legs_are_named_one_per_wheel(
    shipped: tuple[object, object], inputs: StandInputs, geometry: StandGeometry
) -> None:
    """脚は輪ごとに1つの独立した部品である（要件 5.1 / 決定 5「3脚独立」）。"""
    params, _ = shipped
    assert "service_stand" in PART_NAMES
    assert geometry.leg_count == inputs.leg_count == len(inputs.wheel_angles_deg)
    # ⚠️ タスク 3.2 で駆動ベースが加わったため、脚は一覧の**末尾**にある
    # （`PART_NAMES` の並びがそのまま `part_names` の並びである）。
    assert part_names(params)[-inputs.leg_count :] == tuple(  # type: ignore[arg-type]
        f"service_stand_{index}" for index in range(1, inputs.leg_count + 1)
    )
    assert geometry.leg_angles_deg == inputs.wheel_angles_deg


def test_the_leg_envelope_fits_the_build_volume(
    shipped: tuple[object, object], geometry: StandGeometry
) -> None:
    """1脚の外接箱が造形可能寸法に収まる（要件 2.2 / 決定 5「1体の枠にしない」）。"""
    params, _ = shipped
    assert isinstance(geometry.envelope, Envelope)
    assert check_envelope("service_stand", geometry.envelope, params.printing) == ()  # type: ignore[attr-defined]


def test_the_placed_legs_leave_the_minimum_hand_access(
    inputs: StandInputs, geometry: StandGeometry
) -> None:
    """据え付けた3脚の間の開きが手の幅の下限を満たす（要件 5.8）。

    ⚠️ **開きは配置半径・取付角・脚の外形から算術で決まる**——形状ライブラリを
    要さない。`stand_geometry` が値として持つことで、他のスタンド側の下限と同じく
    **設定の変更が形状生成の前に落ちる**（下限をテストだけで見ていると、間隔を
    詰めるパラメータ変更が黙って造形物になる）。
    """
    assert geometry.leg_separation_mm >= MIN_HAND_ACCESS_MM
    assert geometry.leg_separation_mm == pytest.approx(96.1, abs=0.1)
    assert geometry.leg_count == inputs.leg_count


def test_geometry_rejects_a_placement_that_leaves_no_room_for_a_hand(
    inputs: StandInputs,
) -> None:
    """脚が近づきすぎる配置を、値つきで拒否する（要件 5.8）。

    ⚠️ **他のスタンド側の下限と同じ扱いにする。** 配置半径を詰めれば隣の脚との
    開きは消え、台上で配線・コネクタ・電源の操作部へ手が届かなくなる。
    """
    import dataclasses

    cramped = dataclasses.replace(inputs, base_radius_mm=100.0)
    with pytest.raises(GeometryError) as excinfo:
        stand_geometry(cramped)
    message = str(excinfo.value)
    assert str(MIN_HAND_ACCESS_MM) in message
    assert "100.0" in message
    assert "手" in message


@pytest.mark.parametrize(
    ("field", "value", "fragment"),
    [
        ("wheel_rotation_clearance_mm", 20.0, "持ち上げ高さ"),
        ("support_span_mm", 80.0, "車軸"),
        ("lift_height_mm", 10.5, "谷の底"),
    ],
)
def test_geometry_rejects_combinations_that_do_not_stand_up(
    inputs: StandInputs, field: str, value: float, fragment: str
) -> None:
    """成立しない寸法の組み合わせを、項目名と量つきで拒否する。

    - 隙間 >= 持ち上げ高さ: 谷の底が床より下になる
    - 支持スパンが大きすぎる: 受け面が車軸を越えて回転を隠す（要件 5.8）
    - 持ち上げ高さが小さい: 谷の底に肉が残らない
    """
    import dataclasses

    broken = dataclasses.replace(inputs, **{field: value})
    with pytest.raises(GeometryError) as excinfo:
        stand_geometry(broken)
    assert fragment in str(excinfo.value)
    assert str(value) in str(excinfo.value)


# ---------------------------------------------------------------------------
# 3. 形状ライブラリの遅延 import（design.md「Allowed Dependencies」/「Dependency Direction」）
# ---------------------------------------------------------------------------


def test_shapes_does_not_import_the_shape_library_at_module_level() -> None:
    """`shapes` はモジュール直下で形状ライブラリを import しない。

    ⚠️ **許されていることと「モジュール読み込み時に必要にしてよい」ことは別である**
    （上流 `catch_mechanism.shapes` と同じ規律）。ここへ置くと `stand_geometry` を
    呼ぶだけで CAD が要ることになり、`__init__` が OCCT へ到達しないという
    design.md「Dependency Direction」の性質が壊れる。
    """
    module = _module_ast()
    module_level: list[str] = []
    for node in module.body:
        if isinstance(node, ast.Import):
            module_level.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module_level.append(node.module or "")
    assert [name for name in module_level if name.split(".")[0] in {"build123d", "OCP"}] == []


def test_measure_part_extracts_the_three_metrics_by_duck_typing() -> None:
    """`measure_part` は属性の形だけに依存する（形状ライブラリ非導入でも測れる）。"""

    class _Size:
        X = 1.5
        Y = 2.5
        Z = 3.5

    class _Box:
        volume = 12.25

        def bounding_box(self) -> object:
            return type("_BBox", (), {"size": _Size})()

        def solids(self) -> tuple[object, ...]:
            return (self,)

    metrics = measure_part("service_stand_1", _Box())
    assert metrics.part_name == "service_stand_1"
    assert metrics.volume_mm3 == 12.25
    assert metrics.bbox_mm == (1.5, 2.5, 3.5)
    assert metrics.solid_count == 1


# ---------------------------------------------------------------------------
# 4. ⚠️ 観測可能な完了状態: 形状ライブラリ非導入の環境
# ---------------------------------------------------------------------------

_STUB_MESSAGE = "build123d is blocked by the chassis shapes CAD-absence stub"
_STUB_SOURCE = f"raise ImportError({_STUB_MESSAGE!r})\n"

_PROBE_BODY = """
import json
import sys

try:
    import build123d  # noqa: F401
except ImportError:
    blocked = True
else:
    blocked = False

from chassis_mechanism.config import load_params
from chassis_mechanism.errors import CadUnavailableError
from chassis_mechanism.layout import derive_layout
from chassis_mechanism.shapes import (
    adapter_geometry,
    battery_tray_geometry,
    build_parts,
    cable_guide_geometry,
    deck_stack_geometry,
    drive_base_geometry,
    part_names,
    stand_geometry,
    stand_inputs,
)

params = load_params()
layout = derive_layout(params)
geometry = stand_geometry(stand_inputs(params, layout))
drive_base = drive_base_geometry(params, layout)
adapter = adapter_geometry(params, layout)
deck = deck_stack_geometry(params, layout)
tray = battery_tray_geometry(params, layout)
guide = cable_guide_geometry(params, layout)

report = {
    "stub_blocked_the_shape_library": blocked,
    "part_names": list(part_names(params)),
    "trough_floor_height_mm": geometry.trough_floor_height_mm,
    "socket_radius_mm": geometry.socket_radius_mm,
    "support_pad_height_mm": geometry.support_pad_height_mm,
    "lap_length_mm": drive_base.lap_length_mm,
    "bolt_count": drive_base.bolt_count,
    "slot_length_mm": drive_base.slot_length_mm,
    "slot_width_mm": drive_base.slot_width_mm,
    "arm_thickness_mm": drive_base.arm_thickness_mm,
    "boss_diameter_mm": drive_base.boss_diameter_mm,
    "adapter_segment_count": adapter.segment_count,
    "adapter_outer_radius_mm": adapter.outer_radius_mm,
    "adapter_seat_bottom_radius_mm": adapter.seat_bottom_radius_mm,
    "adapter_seat_top_radius_mm": adapter.seat_top_radius_mm,
    "adapter_cut_radius_mm": adapter.cut_radius_mm,
    "adapter_lip_width_mm": adapter.lip_width_mm,
    "adapter_retention_bolt_count": adapter.retention_bolt_count,
    "board_deck_radius_mm": deck.board_plate_radius_mm,
    "catch_deck_radius_mm": deck.catch_plate_radius_mm,
    "catch_segment_count": deck.catch_segment_count,
    "deck_usable_area_mm2": deck.usable_area_mm2,
    "liner_flat_min_diameter_mm": deck.liner_flat_min_diameter_mm,
    "switch_provision_band_mm": list(deck.switch_provision_band_mm),
    "tray_floor_bottom_height_mm": tray.floor_bottom_height_mm,
    "tray_extraction_angle_deg": tray.extraction_angle_deg,
    "tray_ear_outer_radius_mm": tray.ear_outer_radius_mm,
    "cable_lowest_height_mm": guide.cable_lowest_height_mm,
    "cable_route_names": [route.name for route in guide.routes],
    "cable_route_bands_mm": [
        [route.inner_y_mm, route.outer_y_mm] for route in guide.routes
    ],
    "cable_guide_top_height_mm": guide.top_height_mm,
    "cable_guide_mount_bolt_radii_mm": list(guide.mount_bolt_radii_mm),
    "cable_guide_estop_lead_out_y_mm": guide.estop_lead_out_y_mm,
    "cable_passage_angles_deg": [route.passage_angle_deg for route in guide.routes],
    "cable_passage_arc_deg": list(guide.passage_arc_deg),
    "cable_passage_spacing_mm": guide.passage_spacing_mm,
    "cable_window_bottom_height_mm": guide.window_bottom_height_mm,
    "build_failed": False,
    "error_type": "",
    "message": "",
    "shape_library_modules": sorted(
        name for name in sys.modules if name.split(".")[0] in ("build123d", "OCP")
    ),
}
try:
    build_parts(params, layout)
except CadUnavailableError as exc:
    report["build_failed"] = True
    report["error_type"] = type(exc).__name__
    report["message"] = str(exc)
print(json.dumps(report))
"""


def _nocad_stub(tmp_path: Path) -> Path:
    """形状ライブラリの import が `ImportError` になるスタブ置き場を作って返す。"""
    stub_dir = tmp_path / "nocad"
    stub_dir.mkdir(parents=True, exist_ok=True)
    (stub_dir / "build123d.py").write_text(_STUB_SOURCE, encoding="utf-8")
    return stub_dir


def _run_blocked(code: str, stub_dir: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(stub_dir) + (os.pathsep + existing if existing else "")
    return subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        capture_output=True,
        encoding="utf-8",
        env=env,
        timeout=300.0,
        check=False,
    )


def test_the_cad_blocking_stub_actually_blocks_the_shape_library(tmp_path: Path) -> None:
    """遮断スタブが効いていることを先に確かめる（後続を空振りにしないため）。

    ⚠️ `"ImportError" in stderr` だけでは「そもそも導入されていない」場合と
    区別できない。**スタブ固有のメッセージ**が出ることまで見る。
    """
    result = _run_blocked("import build123d\n", _nocad_stub(tmp_path))
    assert result.returncode != 0
    assert _STUB_MESSAGE in result.stderr


def test_geometry_is_available_and_building_fails_loudly_without_the_shape_library(
    tmp_path: Path,
    geometry: StandGeometry,
    drive_base: DriveBaseGeometry,
    adapter: AdapterGeometry,
) -> None:
    """CAD 非導入の環境で、幾何は導けて**形状生成だけが専用の失敗になる**。

    ⚠️ **成功にしない**（design.md「Error Categories and Responses」/
    「Allowed Dependencies」）。⚠️ 駆動ベース（タスク 3.2）についても同じである
    ——重ね代・座の本数・長穴の寸法・接合面の厚さは算術だけで決まるため、
    形状ライブラリの無い環境でも**成立条件まで**評価できる。
    """
    import json

    params = load_params()
    result = _run_blocked(_PROBE_BODY, _nocad_stub(tmp_path))
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])

    assert report["stub_blocked_the_shape_library"] is True
    assert report["shape_library_modules"] == []
    assert report["part_names"] == list(part_names(params))
    assert report["part_names"][-geometry.leg_count :] == [
        f"service_stand_{index}" for index in range(1, geometry.leg_count + 1)
    ]
    assert report["trough_floor_height_mm"] == geometry.trough_floor_height_mm
    assert report["socket_radius_mm"] == geometry.socket_radius_mm
    assert report["support_pad_height_mm"] == geometry.support_pad_height_mm
    assert report["lap_length_mm"] == drive_base.lap_length_mm
    assert report["bolt_count"] == drive_base.bolt_count
    assert report["slot_length_mm"] == drive_base.slot_length_mm
    assert report["slot_width_mm"] == drive_base.slot_width_mm
    assert report["arm_thickness_mm"] == drive_base.arm_thickness_mm
    assert report["boss_diameter_mm"] == drive_base.boss_diameter_mm
    # ⚠️ 配線ガイドも算術だけで決まる（通路の分離も保持の最下点も CAD を要さない）。
    guide = cable_guide_geometry(params, derive_layout(params))
    assert report["cable_lowest_height_mm"] == guide.cable_lowest_height_mm
    assert report["cable_route_names"] == list(CABLE_ROUTE_NAMES)
    assert report["cable_route_bands_mm"] == [
        [route.inner_y_mm, route.outer_y_mm] for route in guide.routes
    ]
    assert report["cable_guide_top_height_mm"] == guide.top_height_mm
    assert report["cable_guide_mount_bolt_radii_mm"] == list(guide.mount_bolt_radii_mm)
    assert report["cable_guide_estop_lead_out_y_mm"] == guide.estop_lead_out_y_mm
    # ⚠️ 経路（通し穴と窓）も算術だけで決まる——⚠️ **繋がっているかどうかを
    # 知るには CAD が要る**が、どこを通るのかは CAD 無しで読める。
    assert report["cable_passage_angles_deg"] == [
        route.passage_angle_deg for route in guide.routes
    ]
    assert report["cable_passage_arc_deg"] == list(guide.passage_arc_deg)
    assert report["cable_passage_spacing_mm"] == guide.passage_spacing_mm
    assert report["cable_window_bottom_height_mm"] == guide.window_bottom_height_mm
    # ⚠️ アダプタの座も算術だけで決まる（上流の採寸値・テーパー角・分割数導出）。
    assert report["adapter_segment_count"] == adapter.segment_count
    assert report["adapter_outer_radius_mm"] == adapter.outer_radius_mm
    assert report["adapter_seat_bottom_radius_mm"] == adapter.seat_bottom_radius_mm
    assert report["adapter_seat_top_radius_mm"] == adapter.seat_top_radius_mm
    assert report["adapter_cut_radius_mm"] == adapter.cut_radius_mm
    assert report["adapter_lip_width_mm"] == adapter.lip_width_mm
    assert report["adapter_retention_bolt_count"] == adapter.retention_bolt_count
    # ⚠️ **段とトレイも算術だけで決まる**（上流の採寸値・テーパー角・分割数導出）。
    # 成立条件——取付面が足りるか、放熱の隙間に座が収まるか、缶の口より下か——は
    # すべてこの環境で評価済みであり、⚠️ CAD が無いことは「分からない」ではない。
    deck = deck_stack_geometry(params, derive_layout(params))
    tray = battery_tray_geometry(params, derive_layout(params))
    assert report["board_deck_radius_mm"] == deck.board_plate_radius_mm
    assert report["catch_deck_radius_mm"] == deck.catch_plate_radius_mm
    assert report["catch_segment_count"] == deck.catch_segment_count
    assert report["deck_usable_area_mm2"] == deck.usable_area_mm2
    assert report["liner_flat_min_diameter_mm"] == deck.liner_flat_min_diameter_mm
    assert tuple(report["switch_provision_band_mm"]) == deck.switch_provision_band_mm
    assert report["tray_floor_bottom_height_mm"] == tray.floor_bottom_height_mm
    assert report["tray_extraction_angle_deg"] == tray.extraction_angle_deg
    assert report["tray_ear_outer_radius_mm"] == tray.ear_outer_radius_mm
    assert report["build_failed"] is True
    assert report["error_type"] == "CadUnavailableError"
    assert "cad" in report["message"]


# ---------------------------------------------------------------------------
# 5. 構築（形状ライブラリを要する）
# ---------------------------------------------------------------------------


@requires_cad
def test_build_parts_returns_one_independent_solid_per_leg(
    shipped: tuple[object, object], geometry: StandGeometry
) -> None:
    """脚は輪ごとに独立した1個の立体である（決定 5「3脚独立。1体の枠にしない」）。

    ⚠️ タスク 3.2 で駆動ベースが加わったため、`build_parts` の戻り値は脚だけでは
    ない。脚が**輪の数ちょうど**であることと、そのどれもが単一の立体であることが
    決定 5 の主張であり、そこは変わっていない。
    """
    params, layout = shipped
    parts = build_parts(params, layout)  # type: ignore[arg-type]
    assert [part.name for part in parts] == list(part_names(params))  # type: ignore[arg-type]
    legs = [part for part in parts if part.name.startswith("service_stand_")]
    assert len(legs) == geometry.leg_count
    for part in parts:
        assert isinstance(part, BuiltPart)
        # ⚠️ 立体が1個であることが「1体の枠にしない」の形状側の主張である。
        assert part.metrics.solid_count == 1
        assert part.metrics.volume_mm3 > 0.0


@requires_cad
def test_metrics_are_identical_across_two_independent_builds(
    shipped: tuple[object, object]
) -> None:
    """同一パラメータからの2回の構築が同一の指標を返す（要件 1.12）。"""
    params, layout = shipped
    first = build_parts(params, layout)  # type: ignore[arg-type]
    second = build_parts(params, layout)  # type: ignore[arg-type]
    assert [part.metrics for part in first] == [part.metrics for part in second]


@requires_cad
def test_the_measured_bounding_box_is_the_envelope_used_for_the_build_volume_check(
    shipped: tuple[object, object], geometry: StandGeometry
) -> None:
    """実形状の外接箱が、造形可能寸法の検査へ渡した外接箱と一致する（脚）。"""
    params, layout = shipped
    part = next(
        candidate
        for candidate in build_parts(params, layout)  # type: ignore[arg-type]
        if candidate.name.startswith("service_stand_")
    )
    bbox = part.metrics.bbox_mm
    expected = (
        geometry.envelope.x_mm,
        geometry.envelope.y_mm,
        geometry.envelope.z_mm,
    )
    for measured, declared in zip(bbox, expected, strict=True):
        assert measured == pytest.approx(declared, abs=1e-6)


@requires_cad
def test_building_fails_when_the_leg_does_not_fit_the_build_volume(
    inputs: StandInputs, shipped: tuple[object, object]
) -> None:
    """造形可能寸法を超える脚は構築しない（要件 2.3: 軸と超過量を示す）。

    ⚠️ **成立する幾何のまま外接箱だけを超えさせる。** 公称外径を大きくすると
    車軸の高さ（鉛直スタックが持つ値）との辻褄が合わず、床へ抜ける谷として
    `stand_geometry` の側で先に落ちてしまう——見たいのはその手前ではなく
    `check_envelope` の側である。ここではホイール幅を広げて半径方向の外接箱だけを
    超えさせ、脚が離れたままになるよう配置半径も併せて広げる。
    """
    import dataclasses

    params, _ = shipped
    huge = dataclasses.replace(inputs, wheel_width_mm=300.0, base_radius_mm=600.0)
    with pytest.raises(GeometryError) as excinfo:
        build_service_stand_legs(huge, params.printing)  # type: ignore[attr-defined]
    message = str(excinfo.value)
    assert "service_stand" in message
    assert "超過" in message


@requires_cad
def test_cad_unavailable_error_is_not_raised_when_the_library_is_present(
    shipped: tuple[object, object]
) -> None:
    """導入済みの環境では `CadUnavailableError` にならない（遮断検査の対）。"""
    params, layout = shipped
    try:
        build_parts(params, layout)  # type: ignore[arg-type]
    except CadUnavailableError as exc:  # pragma: no cover - 導入済み環境では通らない
        pytest.fail(f"形状ライブラリが導入されているのに失敗した: {exc}")


# ---------------------------------------------------------------------------
# 6. 駆動ベース: 中央部と3つのモータ取付部（タスク 3.2 / 要件 2.5, 2.6, 2.11,
#    3.1, 3.7, 3.8, 3.10）
#
# ⚠️ **本節は形状ライブラリを要さない側である。** 実形状に対する不変条件
# （当たり面の実測・長穴の実測・モータ胴体との非接触）は
# `test_chassis_invariants.py` が持つ（design.md `#### Shapes`「不変条件」）。
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def drive_base(shipped: tuple[object, object]) -> DriveBaseGeometry:
    params, layout = shipped
    return drive_base_geometry(params, layout)  # type: ignore[arg-type]


def _replace_base(params: object, **changes: object) -> object:
    """`base` 群だけを差し替えた `ResolvedParams` を作る。"""
    import dataclasses

    chassis = params.chassis  # type: ignore[attr-defined]
    return dataclasses.replace(
        params,  # type: ignore[type-var]
        chassis=dataclasses.replace(
            chassis, base=dataclasses.replace(chassis.base, **changes)
        ),
    )


def test_the_drive_base_is_a_central_plate_with_three_radial_arms(
    shipped: tuple[object, object], drive_base: DriveBaseGeometry
) -> None:
    """中央部1つと、輪数ぶんの放射状アームからなる（要件 3.1, 3.2）。

    ⚠️ **部品の点数は `joints.segment_counts()` が唯一の正である**（要件 2.1:
    分割数は導出であって設定値ではない）。ここで別に数え直さない。
    """
    from chassis_mechanism.joints import segment_counts

    params, layout = shipped
    counts = segment_counts(params)  # type: ignore[arg-type]
    assert counts["hub_plate"] == 1
    assert counts["motor_arm"] == params.chassis.base.wheel_count  # type: ignore[attr-defined]
    assert drive_base.wheel_angles_deg == layout.wheel_angles_deg  # type: ignore[attr-defined]
    assert drive_base.wheel_count == counts["motor_arm"]
    assert PART_NAMES == (
        "hub_plate",
        "motor_arm",
        "adapter_segment",
        "battery_tray",
        "board_deck",
        "catch_deck",
        "cable_guide",
        "service_stand",
    )
    assert part_names(params) == (  # type: ignore[arg-type]
        "hub_plate",
        "motor_arm_1",
        "motor_arm_2",
        "motor_arm_3",
        "adapter_segment_1",
        "adapter_segment_2",
        "adapter_segment_3",
        "battery_tray",
        # ⚠️ **基板デッキだけ番号を持たない。** 分割数は缶の内径から従属し
        # （要件 7.13）、出荷の寸法では 1 である——`part_names` の規約では
        # 分割しない部品は番号を持たない。番号の有無を手で決めていない。
        "board_deck",
        "catch_deck_1",
        "catch_deck_2",
        "catch_deck_3",
        "cable_guide_1",
        "cable_guide_2",
        "cable_guide_3",
        "service_stand_1",
        "service_stand_2",
        "service_stand_3",
    )


def test_the_arm_spans_from_the_plate_edge_to_the_wheel_centre_plane(
    shipped: tuple[object, object], drive_base: DriveBaseGeometry
) -> None:
    """アームの半径方向の区間は `ARM_LENGTH_FORMULA` そのものである（要件 3.3）。"""
    params, layout = shipped
    base = params.chassis.base  # type: ignore[attr-defined]
    assert drive_base.hub_radius_mm == pytest.approx(base.hub_outer_diameter_mm / 2.0)
    assert drive_base.arm_outer_radius_mm == pytest.approx(layout.base_radius_mm)  # type: ignore[attr-defined]
    assert (
        drive_base.arm_outer_radius_mm - drive_base.hub_radius_mm
    ) == pytest.approx(layout.arm_length_mm)  # type: ignore[attr-defined]
    # ⚠️ 板の下面は取付面（鉛直スタック）である。ここで定数を置かない。
    assert drive_base.underside_height_mm == pytest.approx(
        layout.vertical.mount_face_height_mm  # type: ignore[attr-defined]
    )


def test_the_lap_joint_reads_the_bolt_count_from_the_joint_derivation(
    shipped: tuple[object, object], drive_base: DriveBaseGeometry
) -> None:
    """⚠️ **座の本数と重ね代は `joints` が唯一の正である**（要件 2.6, 3.10）。

    形の側が本数を数え直すと、下限を上げたときに座だけが増えて舌が伸びない、
    といった食い違いが黙って残る。
    """
    from chassis_mechanism.joints import arm_joint_lap_length_mm, derive_joints

    params, layout = shipped
    arm_joint = next(
        joint
        for joint in derive_joints(layout, params)  # type: ignore[arg-type]
        if joint.name == "hub_plate__motor_arm_1"
    )
    assert drive_base.bolt_count == arm_joint.bolt_count
    assert len(drive_base.bolt_radii_mm) == arm_joint.bolt_count
    assert drive_base.lap_length_mm == pytest.approx(
        arm_joint_lap_length_mm(layout, params)  # type: ignore[arg-type]
    )
    # 座はすべて重ね代の内側にある。
    for radius_mm in drive_base.bolt_radii_mm:
        assert drive_base.hub_radius_mm < radius_mm
        assert radius_mm < drive_base.hub_radius_mm + drive_base.lap_length_mm


def test_a_joint_face_thinner_than_the_boss_is_rejected(
    shipped: tuple[object, object]
) -> None:
    """⚠️ **座の外径を下回る接合面の厚さは形状不正である**（要件 2.5, 2.6, 2.9）。

    中央部↔モータ取付部の接合面は接線方向を法線に持ち、面内2軸は
    **アーム長と厚さ**である。`joints.BEARING_AREA_FORMULA` が数えるのは
    座の**環まるごと**であるため、厚さが座の外径 `BOSS_DIAMETER_FACTOR ×
    insert_outer_diameter_mm` を下回ると、⚠️ **記録された当たり面が面に載らない**
    ——解析値だけが下限を満たし、実物は満たさない状態になる。

    ⚠️ **これは CAD 非導入の環境でも観測できる**（`drive_base_geometry` は算術
    のみである）。実形状との一致は `test_chassis_invariants.py` が別に検査する。
    """
    params, layout = shipped
    boss_diameter_mm = 2.0 * params.joint.insert_outer_diameter_mm  # type: ignore[attr-defined]
    thin = _replace_base(params, arm_thickness_mm=boss_diameter_mm - 0.1)
    with pytest.raises(GeometryError) as excinfo:
        drive_base_geometry(thin, layout)  # type: ignore[arg-type]
    message = str(excinfo.value)
    assert "arm_thickness_mm" in message
    assert repr(boss_diameter_mm) in message


def test_the_shipped_joint_face_is_thick_enough_for_the_whole_boss(
    shipped: tuple[object, object], drive_base: DriveBaseGeometry
) -> None:
    """出荷値では接合面の厚さが座の外径以上である（要件 2.5 の「薄い当たり面」禁止）。"""
    params, _ = shipped
    assert drive_base.boss_diameter_mm == pytest.approx(
        2.0 * params.joint.insert_outer_diameter_mm  # type: ignore[attr-defined]
    )
    assert drive_base.arm_thickness_mm >= drive_base.boss_diameter_mm


def test_the_bracket_slot_travel_equals_the_dimension_parameter(
    shipped: tuple[object, object], drive_base: DriveBaseGeometry
) -> None:
    """⚠️ 長穴の移動量が `base.slot_travel_mm` と一致する（要件 3.8 / タスク 3.2）。

    移動量は「長穴の長さ − 長穴の幅」である（幅ぶんは締結要素そのものが占める）。
    ⚠️ **穴の径は上流の貫通穴径であり、ブラケット側の呼び径ではない**
    （要件 2.11: 切削で合わせる嵌合を設計に含めない）。
    """
    params, _ = shipped
    base = params.chassis.base  # type: ignore[attr-defined]
    bracket = params.chassis.bracket  # type: ignore[attr-defined]
    assert drive_base.slot_width_mm == pytest.approx(
        params.joint.through_hole_diameter_mm  # type: ignore[attr-defined]
    )
    assert drive_base.slot_length_mm - drive_base.slot_width_mm == pytest.approx(
        base.slot_travel_mm
    )
    assert drive_base.slot_travel_mm == pytest.approx(base.slot_travel_mm)
    assert len(drive_base.slot_offsets_mm) == bracket.mount_hole_count
    assert drive_base.slot_offsets_mm == pytest.approx(
        (-bracket.mount_hole_pitch_mm / 2.0, bracket.mount_hole_pitch_mm / 2.0)
    )
    # 長穴の中心は取付面の半径（本 Spec の唯一の設計変数）にある。
    assert drive_base.slot_center_radius_mm == pytest.approx(
        base.hub_center_to_mount_face_mm
    )


def test_the_slot_travel_follows_the_dimension_parameter(
    shipped: tuple[object, object]
) -> None:
    """⚠️ 長穴の移動量を書き換えると長穴が 1:1 で伸びる（値を焼き込んでいない）。

    ⚠️ `0.0`（差を吸収しない）も設定として成立する——そのとき長穴は丸穴になる。
    """
    params, layout = shipped
    for travel_mm in (0.0, 2.5, 8.0):
        moved = drive_base_geometry(
            _replace_base(params, slot_travel_mm=travel_mm),  # type: ignore[arg-type]
            layout,  # type: ignore[arg-type]
        )
        assert moved.slot_length_mm - moved.slot_width_mm == pytest.approx(travel_mm)


def test_no_printed_bore_is_a_machined_fit_to_a_mating_part(
    shipped: tuple[object, object], drive_base: DriveBaseGeometry
) -> None:
    """⚠️ **切削加工を前提とする嵌合を設計に含めない**（要件 2.11 / 決定 4）。

    観測可能な形にするために2つを固定する。

    - 造形する穴はどれも上流の**貫通穴径以上**である。貫通穴径は締結要素の呼びに
      対してすでに隙間を持つ値であり（M3 に対し φ3.4）、それを下回る穴は
      「あとで揉んで合わせる」ことを前提にしなければ成立しない
    - 造形する穴の径が、相手部品の**呼び寸法そのもの**と一致しない。一致していれば
      それは締まり嵌め——寸法差を切削で吸収する設計である。寸法差は長穴と隙間で
      吸収する（決定 4）
    """
    params, _ = shipped
    chassis = params.chassis  # type: ignore[attr-defined]
    through_hole_mm = params.joint.through_hole_diameter_mm  # type: ignore[attr-defined]
    assert drive_base.bore_diameters_mm, "造形する穴が1つも無い記述は検査にならない"
    for diameter_mm in drive_base.bore_diameters_mm:
        assert diameter_mm >= through_hole_mm, diameter_mm

    # ⚠️ **一覧は「機械的に組み付ける相手」である。** 熱圧入インサートはここに
    # 現れない——その下穴が呼び外径ちょうどであることは正しい（インサートは周囲の
    # 樹脂を**溶かして**食い込むのであって、削って合わせるのではない。上流
    # `catch_mechanism.shapes` も下穴を `insert_outer_diameter_mm` に採っている）。
    mating_nominals_mm = (
        chassis.bracket.mount_hole_diameter_mm,
        chassis.motor.shaft_diameter_mm,
        chassis.hub.boss_diameter_mm,
        chassis.hub.bore_diameter_mm,
        chassis.wheel.center_bore_diameter_mm,
    )
    for diameter_mm in drive_base.bore_diameters_mm:
        for nominal_mm in mating_nominals_mm:
            assert diameter_mm != pytest.approx(nominal_mm), (
                f"造形穴 {diameter_mm}mm が相手部品の呼び {nominal_mm}mm と一致する"
                "（切削で合わせる嵌合である）"
            )
    # ブラケットの取付穴は丸穴ではなく長穴である（要件 3.8）。
    assert drive_base.slot_length_mm > drive_base.slot_width_mm


def test_the_motor_body_hangs_below_the_drive_base(
    shipped: tuple[object, object], drive_base: DriveBaseGeometry
) -> None:
    """⚠️ **造形部品でモータ本体をクランプしない**（要件 3.7）。

    モータ胴体は付属金属ブラケットにぶら下がり、その最上点は駆動ベースの下面より
    低い。⚠️ ここが崩れる寸法は「造形部品が胴体を掴む」設計であるため拒否する。
    """
    params, layout = shipped
    motor = params.chassis.motor  # type: ignore[attr-defined]
    assert drive_base.motor_axis_height_mm == pytest.approx(
        layout.vertical.axle_center_height_mm  # type: ignore[attr-defined]
    )
    assert drive_base.motor_body_diameter_mm == pytest.approx(motor.body_diameter_mm)
    assert (
        drive_base.motor_outer_radius_mm - drive_base.motor_inner_radius_mm
    ) == pytest.approx(motor.body_length_mm)
    # ギヤボックス端面はホイール中心面からハブのフランジ厚と半幅ぶん内側にある。
    assert drive_base.motor_outer_radius_mm == pytest.approx(
        layout.base_radius_mm - layout.axial_stack_mm[1]  # type: ignore[attr-defined]
    )
    top_mm = drive_base.motor_axis_height_mm + motor.body_diameter_mm / 2.0
    assert top_mm <= drive_base.underside_height_mm
    # ⚠️ 半径方向では胴体が中央部ともアームとも重なる——高さだけが隔てている。
    assert drive_base.motor_inner_radius_mm < drive_base.hub_radius_mm
    assert drive_base.motor_outer_radius_mm > drive_base.hub_radius_mm


def test_a_drive_base_that_would_clamp_the_motor_body_is_rejected(
    shipped: tuple[object, object]
) -> None:
    """胴体の上端がベース下面へ届く寸法は拒否される（要件 3.7）。"""
    import dataclasses

    params, layout = shipped
    chassis = params.chassis  # type: ignore[attr-defined]
    clamping = dataclasses.replace(
        params,  # type: ignore[type-var]
        chassis=dataclasses.replace(
            chassis,
            motor=dataclasses.replace(chassis.motor, body_diameter_mm=90.0),
        ),
    )
    with pytest.raises(GeometryError) as excinfo:
        drive_base_geometry(clamping, layout)  # type: ignore[arg-type]
    assert "body_diameter_mm" in str(excinfo.value)


def test_the_drive_base_fragments_fit_the_build_volume(
    shipped: tuple[object, object], drive_base: DriveBaseGeometry
) -> None:
    """中央部とアームの外接箱が造形可能寸法に収まる（要件 2.2）。"""
    params, _ = shipped
    for name, envelope in (
        ("hub_plate", drive_base.hub_plate_envelope),
        ("motor_arm", drive_base.motor_arm_envelope),
    ):
        assert isinstance(envelope, Envelope)
        assert check_envelope(name, envelope, params.printing) == (), name  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# 7. ゴミ箱固定アダプタ（タスク 3.3 / 要件 2.2, 6.1, 6.2, 6.5, 6.7, 6.10）
#
# ⚠️ **アダプタは座ではなくクランプである**（design.md 決定 4b）。ゴミ箱の底は
# 平面部径まで抜かれ、残るのは外径との差ぶんの縁（片側 `lip_width_mm`）だけで
# ある。⚠️ **下から支える平面はもう無い**——クランプは縁の下へ入って掴み、
# 側壁を円錐の受け面で外から抱える。
#
# ⚠️ **本節は形状ライブラリを要さない側である。** 受け面の径・テーパー・切り
# 取り径・縁の幅・分割数・締結箇所は算術だけで決まり、実形状に対する不変条件
# （受け面が円筒断面を持たない、縁の下に掴み面がある、通過を狭めない、当たり面が
# 実現している）は `test_chassis_invariants.py` が持つ。
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def adapter(shipped: tuple[object, object]) -> AdapterGeometry:
    params, layout = shipped
    return adapter_geometry(params, layout)  # type: ignore[arg-type]


def _replace_can(params: object, **changes: object) -> object:
    """上流のゴミ箱の採寸値だけを差し替えた `ResolvedParams` を作る。"""
    import dataclasses

    return dataclasses.replace(
        params,  # type: ignore[type-var]
        trash_can=dataclasses.replace(params.trash_can, **changes),  # type: ignore[attr-defined]
    )


def test_the_adapter_dimensions_come_from_the_upstream_trash_can_measurements(
    shipped: tuple[object, object], adapter: AdapterGeometry
) -> None:
    """⚠️ アダプタの寸法は上流の採寸値から導かれる（要件 6.1 / 1.3）。

    底の外径・底の平面部径・テーパー角・底の肉厚のすべてが形に現れる。
    ⚠️ **本 Spec 側が持つのは受けるための量（隙間・肉厚・立ち上がり・保持箇所）
    だけである**——同じ値を再定義しない。
    """
    from chassis_mechanism.joints import _adapter_outer_diameter_mm, derive_joints

    params, layout = shipped
    can = params.trash_can  # type: ignore[attr-defined]
    spec = params.chassis.adapter  # type: ignore[attr-defined]

    # 座の内側は「底の外半径 ＋ 隙間」から始まる。
    assert adapter.seat_bottom_radius_mm == pytest.approx(
        can.bottom_outer_diameter_mm / 2.0 + spec.seat_clearance_mm
    )
    # 外径の正は `joints` である（⚠️ 形の側で数え直さない）。
    assert adapter.outer_radius_mm == pytest.approx(
        _adapter_outer_diameter_mm(params) / 2.0  # type: ignore[arg-type]
    )
    # ⚠️ **切り取り径は本 Spec の寸法パラメータである**（要件 6.10 / 決定 4b）。
    # 上流の平面部径は**上限**であって値そのものではない——手で切る以上、
    # 上限をそのまま採らない。
    assert adapter.cut_radius_mm == pytest.approx(spec.bottom_cut_diameter_mm / 2.0)
    assert 2.0 * adapter.cut_radius_mm < can.bottom_flat_diameter_mm
    assert adapter.cut_radius_mm < adapter.seat_bottom_radius_mm
    # 掴み代は「底の外径 − 切り取り径」として残る縁である（要件 6.10, 6.11）。
    assert adapter.lip_outer_radius_mm == pytest.approx(
        can.bottom_outer_diameter_mm / 2.0
    )
    assert adapter.lip_width_mm == pytest.approx(
        (can.bottom_outer_diameter_mm - spec.bottom_cut_diameter_mm) / 2.0
    )
    assert adapter.taper_deg == can.taper_deg
    # ⚠️ **底の肉厚は形の側から消えたのではなく、締結の積み上がりへ移った。**
    # 底が残っていた頃はここが「通過径」を決めていたが、底は抜かれた（決定 4b）
    # ——いま肉厚が効くのは、クランプの肉と缶の側壁を貫くボルトの長さである。
    retention = next(
        joint
        for joint in derive_joints(layout, params)  # type: ignore[arg-type]
        if joint.name == "adapter__trash_can"
    )
    assert retention.bolt_length_mm == pytest.approx(
        spec.wall_thickness_mm
        + can.bottom_thickness_mm
        + params.joint.insert_length_mm  # type: ignore[attr-defined]
        + params.chassis.joint_local.fastener_length_margin_mm  # type: ignore[attr-defined]
    )


def test_the_cut_never_exceeds_the_upstream_bottom_flat_diameter(
    shipped: tuple[object, object], adapter: AdapterGeometry
) -> None:
    """⚠️ **切り取り径は上流の底の平面部径以下である**（要件 6.10）。

    ⚠️ **切断は不可逆である**（要件 6.11 / design.md 決定 4b）。平面部径を超えて
    切れば、外径との差として残るはずの縁が消え、⚠️ **缶の重量を受ける座面が
    どこにも無くなる**——缶は受け面のテーパーが噛むまで沈む。⚠️ **縁が担うのは
    その座面であって、持ち上げ方向の拘束ではない**（要件 6.10 の改訂どおり
    テーパーは持ち上げでは緩む側であり、上方向は受入基準 6.5 の締結が止める）。

    ⚠️ **切り取り径は本 Spec の寸法パラメータであり、上流の平面部径は上限で
    ある**（要件 6.10 / 決定 4b「手作業の余裕」）。⚠️ **上限をそのまま採らない**
    ——切断は工作機械ではなく手で行い、⚠️ **誤差の効き方は片側である**（小さく
    切れば縁が広がるだけだが、大きく切れば縁が消える）。
    """
    params, _ = shipped
    can = params.trash_can  # type: ignore[attr-defined]

    assert 2.0 * adapter.cut_radius_mm <= can.bottom_flat_diameter_mm
    # ⚠️ 上限そのものではない（手切りの誤差の余裕が残っている）。
    assert 2.0 * adapter.cut_radius_mm < can.bottom_flat_diameter_mm
    assert adapter.lip_width_mm > 0.0
    assert adapter.lip_width_mm == pytest.approx(
        adapter.lip_outer_radius_mm - adapter.cut_radius_mm
    )
    # 縁は受け面の内側にある（クランプは縁の下へ入り、側壁を外から抱える）。
    assert (
        adapter.cut_radius_mm
        < adapter.lip_outer_radius_mm
        < adapter.seat_bottom_radius_mm
    )
    # 掴み面はゴミ箱の底が載る高さ（`floor_top_height_mm`）の環であり、
    # 縁の帯 [cut, lip_outer] を丸ごと覆う（実形状は invariants が測る）。
    assert adapter.seat_bottom_radius_mm > adapter.lip_outer_radius_mm


def _replace_adapter(params: object, **changes: object) -> object:
    """本 Spec 側のアダプタの寸法だけを差し替えた `ResolvedParams` を作る。"""
    import dataclasses

    chassis = params.chassis  # type: ignore[attr-defined]
    return dataclasses.replace(
        params,  # type: ignore[type-var]
        chassis=dataclasses.replace(
            chassis, adapter=dataclasses.replace(chassis.adapter, **changes)
        ),
    )


def test_a_cut_diameter_that_leaves_no_lip_is_rejected(
    shipped: tuple[object, object],
) -> None:
    """⚠️ 縁が残らない切り取り径を**黙って受けない**（要件 6.10, 6.11）。

    ⚠️ **形の成立条件は読み込みの検査に頼らない。** `config` は切り取り径が
    上流の平面部径を超えることを拒否するが、形の側は「縁が残るか」だけを見る
    ——⚠️ 平面部径が底の外径と等しいゴミ箱（角の丸みが無い底。上流はそれを
    許す）では、上限まで切った瞬間に掴み代が消える。
    """
    params, layout = shipped
    can = params.trash_can  # type: ignore[attr-defined]
    with pytest.raises(GeometryError) as excinfo:
        adapter_geometry(
            _replace_adapter(
                params, bottom_cut_diameter_mm=can.bottom_outer_diameter_mm
            ),  # type: ignore[arg-type]
            layout,  # type: ignore[arg-type]
        )
    message = str(excinfo.value)
    assert "bottom_cut_diameter_mm" in message
    assert "bottom_outer_diameter_mm" in message


def test_the_cut_is_read_from_the_dimension_file_without_touching_code(
    shipped: tuple[object, object], adapter: AdapterGeometry
) -> None:
    """⚠️ **切り取り径は設定値であり、変えれば縁と通過がまとめて追随する**
    （要件 1.5 / 6.10）。

    ⚠️ **小さく切れば縁が広がるだけである**——誤差の効き方が片側であることを、
    導出の側でも固定する（design.md 決定 4b「手作業の余裕」）。
    """
    params, layout = shipped
    tighter_mm = params.chassis.adapter.bottom_cut_diameter_mm - 8.0  # type: ignore[attr-defined]
    tighter = adapter_geometry(
        _replace_adapter(params, bottom_cut_diameter_mm=tighter_mm),  # type: ignore[arg-type]
        layout,  # type: ignore[arg-type]
    )
    assert tighter.cut_radius_mm == pytest.approx(tighter_mm / 2.0)
    assert tighter.lip_width_mm == pytest.approx(adapter.lip_width_mm + 4.0)
    # ⚠️ 縁の外半径は缶の外径のままである（切り取りは内側だけを動かす）。
    assert tighter.lip_outer_radius_mm == pytest.approx(adapter.lip_outer_radius_mm)


def test_the_seat_follows_the_frustum_and_is_not_a_cylinder(
    shipped: tuple[object, object], adapter: AdapterGeometry
) -> None:
    """⚠️ **受け面は円錐台の側面に沿う**（要件 6.2）。

    ⚠️ **円筒であれば上端と下端の径が等しい。** ここで固定するのは
    「テーパー角ぶん広がっている」ことであり、実形状の側の検査
    （円筒断面を持たないこと）は `test_chassis_invariants.py` が持つ。
    """
    import math

    params, _ = shipped
    can = params.trash_can  # type: ignore[attr-defined]

    assert adapter.seat_slope == pytest.approx(math.tan(math.radians(can.taper_deg)))
    assert adapter.seat_top_radius_mm > adapter.seat_bottom_radius_mm
    assert adapter.seat_top_radius_mm - adapter.seat_bottom_radius_mm == pytest.approx(
        adapter.rise_height_mm * adapter.seat_slope
    )


def test_a_cylindrical_bottom_still_yields_a_seat(shipped: tuple[object, object]) -> None:
    """テーパー 0（円筒形のゴミ箱）でも座は成立する（上流が許す入力）。

    ⚠️ **円錐台を前提にすることと、テーパーが 0 の入力を拒むことは別である。**
    上流 `TrashCanMeasurements` はテーパー 0 を「円筒形のゴミ箱を排除しない」ために
    許しており、本 Spec が導出でそれを弾いてはならない。
    """
    params, layout = shipped
    straight = adapter_geometry(_replace_can(params, taper_deg=0.0), layout)  # type: ignore[arg-type]
    assert straight.seat_slope == pytest.approx(0.0)
    assert straight.seat_top_radius_mm == pytest.approx(straight.seat_bottom_radius_mm)


def test_the_seat_is_re_derived_when_the_upstream_measurement_is_updated(
    shipped: tuple[object, object], adapter: AdapterGeometry, tmp_path: Path
) -> None:
    """⚠️ **上流の採寸値を測り直すと座の寸法が追随する**（要件 6.9 / 1.5）。

    タスク 5.3 が `trash_can` の仮値を実測へ置き換える。そのとき**実装コードを
    変えずに**座が動くことをここで固定する。⚠️ **動くのは上流が正である量だけで
    ある**——切り取り径は本 Spec の寸法パラメータであり、平面部径は上限として
    しか効かない（決定 4b）。

    ⚠️ **書き戻しは `tmp_path` の複製に対して行い、実物の
    `configs/catch_mechanism/dimensions.json` へは触れない。** 値は上流の
    書き出し形式を通して往復させる——メモリ上の差し替えだけでは
    「設定ファイルを読み直したら追随する」ことを示せない。
    """
    from catch_mechanism import load_params as upstream_load_params

    from chassis_mechanism.config import (
        UPSTREAM_DIMENSIONS_PATH,
        update_upstream_measurement,
    )

    original = UPSTREAM_DIMENSIONS_PATH.read_bytes()
    target = tmp_path / "upstream-dimensions.json"
    target.write_bytes(original.replace(b"\r\n", b"\n"))

    params, layout = shipped
    measured_outer_mm = params.trash_can.bottom_outer_diameter_mm - 4.6  # type: ignore[attr-defined]
    update_upstream_measurement(
        "trash_can.bottom_outer_diameter_mm", measured_outer_mm, path=target
    )
    reloaded = upstream_load_params(target).trash_can
    remeasured = adapter_geometry(
        _replace_can(params, bottom_outer_diameter_mm=reloaded.bottom_outer_diameter_mm),  # type: ignore[arg-type]
        layout,
    )

    assert remeasured.lip_outer_radius_mm == pytest.approx(measured_outer_mm / 2.0)
    assert remeasured.seat_bottom_radius_mm == pytest.approx(
        adapter.seat_bottom_radius_mm - 2.3
    )
    # ⚠️ **底の外径が小さくなれば掴み代はその半分ぶん狭まる**（要件 6.10）。
    # ⚠️ **切り取り径は動かない**——それは本 Spec の寸法パラメータであり、
    # 上流の平面部径は**上限**としてしか効かない（決定 4b「手作業の余裕」）。
    assert remeasured.cut_radius_mm == pytest.approx(adapter.cut_radius_mm)
    assert remeasured.lip_width_mm == pytest.approx(adapter.lip_width_mm - 2.3)
    # ⚠️ 実物は書き換わっていない。
    assert UPSTREAM_DIMENSIONS_PATH.read_bytes() == original


def test_the_seat_follows_a_changed_bottom_outer_diameter(
    shipped: tuple[object, object], adapter: AdapterGeometry
) -> None:
    """底の外径が変われば座の内径と外径がまとめて動く（要件 6.9）。"""
    params, layout = shipped
    wider = adapter_geometry(
        _replace_can(
            params,
            bottom_outer_diameter_mm=params.trash_can.bottom_outer_diameter_mm + 10.0,  # type: ignore[attr-defined]
        ),
        layout,
    )
    assert wider.seat_bottom_radius_mm == pytest.approx(
        adapter.seat_bottom_radius_mm + 5.0
    )
    assert wider.outer_radius_mm == pytest.approx(adapter.outer_radius_mm + 5.0)


def test_the_fragment_count_is_the_upstream_annular_derivation(
    shipped: tuple[object, object], adapter: AdapterGeometry
) -> None:
    """⚠️ **分割数は上流の円環の導出が決める**（要件 2.1, 2.2 / research.md）。

    アダプタは円環そのものであり、`required_segment_count` がそのまま使える
    （駆動ベースのように輪数から従属させない）。⚠️ **形の側で数え直さない。**
    """
    from chassis_mechanism.joints import segment_counts

    params, _ = shipped
    counts = segment_counts(params)  # type: ignore[arg-type]
    assert adapter.segment_count == counts["adapter_segment"]
    assert adapter.segment_count > 1, "分割しないなら分割の導出を検査できない"
    assert len(adapter.segment_start_angles_deg) == adapter.segment_count
    assert adapter.segment_span_deg == pytest.approx(360.0 / adapter.segment_count)
    assert len(adapter.segment_envelopes) == adapter.segment_count


def test_a_bottom_no_split_can_solve_fails_with_the_upstream_error(
    shipped: tuple[object, object],
) -> None:
    """⚠️ どの分割数でも収まらない外径は**上流の失敗のまま**伝播する。

    ⚠️ **包み直さない**（design.md「Error Strategy」: どちらの設定が壊れて
    いるかをメッセージから消さない）。半径方向の広がりは分割数を増やしても
    縮まないため、分割では解決しない（上流 `required_segment_count`）。
    """
    from catch_mechanism import GeometryError as UpstreamGeometryError

    params, layout = shipped
    huge_mm = params.trash_can.opening_inner_diameter_mm * 4.0  # type: ignore[attr-defined]
    huge = _replace_can(
        params,
        bottom_outer_diameter_mm=huge_mm,
        bottom_flat_diameter_mm=huge_mm,
        opening_inner_diameter_mm=huge_mm,
    )
    with pytest.raises(UpstreamGeometryError) as excinfo:
        adapter_geometry(huge, layout)  # type: ignore[arg-type]
    assert "分割" in str(excinfo.value)


def test_the_retention_points_come_from_the_joint_derivation(
    shipped: tuple[object, object], adapter: AdapterGeometry
) -> None:
    """⚠️ 締結箇所の数は `joints` が正である（要件 6.5 / 2.10）。

    保持箇所の数（`adapter.retention_point_count`）は下限であり、当たり面の
    下限を満たすためにそれ以上になりうる。⚠️ **形の側で数え直さない。**
    """
    from chassis_mechanism.joints import derive_joints

    params, layout = shipped
    joints = {spec.name: spec for spec in derive_joints(layout, params)}  # type: ignore[arg-type]
    retention = joints["adapter__trash_can"]
    assert adapter.retention_bolt_count == retention.bolt_count
    assert adapter.retention_bolt_count >= params.chassis.adapter.retention_point_count  # type: ignore[attr-defined]
    assert len(adapter.retention_bolt_angles_deg) == adapter.retention_bolt_count

    for index in range(1, adapter.segment_count + 1):
        mount = joints[f"hub_plate__adapter_segment_{index}"]
        assert adapter.mount_bolt_count == mount.bolt_count
        assert len(adapter.mount_bolt_angles_deg[index - 1]) == mount.bolt_count


def test_the_retention_bolts_press_the_tapered_wall_not_the_floor(
    adapter: AdapterGeometry,
) -> None:
    """⚠️ 保持の締結はゴミ箱の**側面（テーパー面）**を押さえる（要件 6.5）。

    ⚠️ **底へ穴を開けて点で引かない**——底はもう無い（決定 4b）。残っているのは
    幅 `lip_width_mm` の縁だけであり、⚠️ **そこへ穴を開ければ掴み代を自分で
    削ることになる**（`joints.ASSUMPTIONS` の要件 6.8 の根拠は、底を抜いた後は
    「縁が薄く狭い」という形でいっそう強く効く）。ボルトの軸は縁の載る高さより
    上にあり、座の環が丸ごと立ち上がりに載る——これは駆動ベースの「接合面が
    座の環を載せられる厚さ」と同じ成立条件である。

    ⚠️ **拘束の向きを取り違えない。** 水平方向はテーパーの受け面が全周で与え、
    鉛直方向は縁の下へ入った掴み面が受ける（缶は落ちない）。⚠️ **持ち上げ方向を
    止めているのは受け面ではなくこの貫通ボルトである**——テーパーは上へ抜ける
    向きには緩む側であり、くさびとして効くのは沈む向きだけである。
    """
    boss_radius_mm = adapter.boss_diameter_mm / 2.0
    assert adapter.retention_bolt_height_mm - boss_radius_mm >= adapter.floor_top_height_mm
    assert adapter.retention_bolt_height_mm + boss_radius_mm <= adapter.rise_top_height_mm
    # 締結は水平（半径方向）であり、⚠️ 縁ではなく側壁を貫く。
    assert adapter.seat_top_radius_mm > adapter.seat_bottom_radius_mm
    assert adapter.retention_bolt_height_mm > adapter.floor_top_height_mm


def test_a_rise_shorter_than_the_boss_is_rejected(
    shipped: tuple[object, object], adapter: AdapterGeometry
) -> None:
    """⚠️ 立ち上がりが座の外径を下回る寸法を拒否する（要件 2.5, 2.9）。

    ⚠️ **座の環が立ち上がりに載らなければ、締結はゴミ箱の側面ではなく座の底を
    押さえることになる。** 駆動ベースが `arm_thickness_mm` に課したのと同じ
    成立条件である。
    """
    import dataclasses

    params, layout = shipped
    chassis = params.chassis  # type: ignore[attr-defined]
    thin = dataclasses.replace(
        params,  # type: ignore[type-var]
        chassis=dataclasses.replace(
            chassis,
            adapter=dataclasses.replace(
                chassis.adapter, rise_height_mm=adapter.boss_diameter_mm / 2.0
            ),
        ),
    )
    with pytest.raises(GeometryError) as excinfo:
        adapter_geometry(thin, layout)  # type: ignore[arg-type]
    assert "rise_height_mm" in str(excinfo.value)


def test_a_plate_thinner_than_the_boss_leaves_no_mount_seat(
    shipped: tuple[object, object], adapter: AdapterGeometry
) -> None:
    """⚠️ 中央部の厚さが座の外径を下回ると、取付の座の環が裾に載らない。"""
    import dataclasses

    params, layout = shipped
    chassis = params.chassis  # type: ignore[attr-defined]
    thin = dataclasses.replace(
        params,  # type: ignore[type-var]
        chassis=dataclasses.replace(
            chassis,
            base=dataclasses.replace(
                chassis.base, plate_thickness_mm=adapter.boss_diameter_mm / 2.0
            ),
        ),
    )
    with pytest.raises(GeometryError) as excinfo:
        adapter_geometry(thin, layout)  # type: ignore[arg-type]
    assert "plate_thickness_mm" in str(excinfo.value)


def test_the_mount_bolts_clear_the_motor_arms_and_the_split_planes(
    adapter: AdapterGeometry,
) -> None:
    """⚠️ 取付の座は**アームの間**に置く（アームは裾の高さを占めている）。

    裾は中央部の外縁を掴むため、アームと同じ高さの帯を通る。⚠️ **座がアームに
    重なる配置は締結できない**——導出はアームを避けた空きの弧へ座を並べ、
    並ばない場合は失敗する。
    """
    import math

    boss_half_deg = math.degrees(
        math.asin(adapter.boss_diameter_mm / 2.0 / adapter.skirt_outer_radius_mm)
    )
    arm_half_deg = math.degrees(
        math.asin(adapter.arm_void_half_width_mm / adapter.skirt_inner_radius_mm)
    )
    for index, angles in enumerate(adapter.mount_bolt_angles_deg):
        start_deg = adapter.segment_start_angles_deg[index]
        for angle_deg in angles:
            local_deg = (angle_deg - start_deg) % 360.0
            assert local_deg >= boss_half_deg
            assert local_deg <= adapter.segment_span_deg - boss_half_deg
            for arm_deg in adapter.arm_angles_deg:
                gap_deg = abs((angle_deg - arm_deg + 180.0) % 360.0 - 180.0)
                assert gap_deg > arm_half_deg + boss_half_deg, (angle_deg, arm_deg)


def test_the_mount_bolts_cannot_be_placed_when_the_arms_take_the_whole_arc(
    shipped: tuple[object, object],
) -> None:
    """⚠️ 空きの弧に座が並ばない配置は**黙って重ねずに**失敗する（要件 2.9）。"""
    import dataclasses

    params, layout = shipped
    chassis = params.chassis  # type: ignore[attr-defined]
    fat = dataclasses.replace(
        params,  # type: ignore[type-var]
        chassis=dataclasses.replace(
            chassis, base=dataclasses.replace(chassis.base, arm_width_mm=118.0)
        ),
    )
    with pytest.raises(GeometryError) as excinfo:
        adapter_geometry(fat, layout)  # type: ignore[arg-type]
    assert "adapter_segment" in str(excinfo.value)


def test_the_adapter_fragments_fit_the_build_volume(
    shipped: tuple[object, object], adapter: AdapterGeometry
) -> None:
    """各断片の外接箱が造形可能寸法に収まる（要件 2.2）。"""
    params, _ = shipped
    for index, envelope in enumerate(adapter.segment_envelopes, start=1):
        assert isinstance(envelope, Envelope)
        assert (
            check_envelope(
                f"adapter_segment_{index}",
                envelope,
                params.printing,  # type: ignore[attr-defined]
            )
            == ()
        ), index


def test_no_printed_adapter_bore_is_a_machined_fit_to_a_mating_part(
    shipped: tuple[object, object], adapter: AdapterGeometry
) -> None:
    """⚠️ アダプタにも切削前提の嵌合を含めない（要件 2.11 / 決定 4）。"""
    params, _ = shipped
    chassis = params.chassis  # type: ignore[attr-defined]
    through_hole_mm = params.joint.through_hole_diameter_mm  # type: ignore[attr-defined]
    assert adapter.bore_diameters_mm
    for diameter_mm in adapter.bore_diameters_mm:
        assert diameter_mm >= through_hole_mm, diameter_mm
    mating_nominals_mm = (
        chassis.bracket.mount_hole_diameter_mm,
        chassis.motor.shaft_diameter_mm,
        chassis.hub.boss_diameter_mm,
        chassis.hub.bore_diameter_mm,
        chassis.wheel.center_bore_diameter_mm,
    )
    for diameter_mm in adapter.bore_diameters_mm:
        for nominal_mm in mating_nominals_mm:
            assert diameter_mm != pytest.approx(nominal_mm), diameter_mm


def test_the_adapter_sits_on_the_drive_base_without_taking_its_place(
    shipped: tuple[object, object],
    adapter: AdapterGeometry,
    drive_base: DriveBaseGeometry,
) -> None:
    """アダプタは中央部とアームの**上に載り**、外側へ張り出す（決定 1）。

    ⚠️ **底を受けるのはハブではなくアダプタ断片である**——ゴミ箱の底 φ180 は
    造形可能寸法を超えるため中央部では受けられない。
    """
    params, _ = shipped
    can = params.trash_can  # type: ignore[attr-defined]
    plate_top_mm = drive_base.underside_height_mm + drive_base.plate_thickness_mm
    assert adapter.floor_bottom_height_mm == pytest.approx(plate_top_mm)
    # 座は底の外半径まで届く（決定 1 の条件 (c)）。
    assert adapter.outer_radius_mm >= can.bottom_outer_diameter_mm / 2.0
    # 裾は中央部の外縁を掴む（⚠️ 掴まなければ半径方向の締結が成立しない）。
    assert adapter.skirt_inner_radius_mm > drive_base.hub_radius_mm
    assert (
        adapter.skirt_inner_radius_mm
        < drive_base.hub_radius_mm + adapter.boss_diameter_mm
    )
    # 長穴（ブラケット取付）へは掛からない。
    assert (
        adapter.outer_radius_mm
        < drive_base.slot_center_radius_mm - drive_base.slot_length_mm / 2.0
    )


def test_the_retention_joint_needs_no_insert_in_the_seat_wall(
    shipped: tuple[object, object], adapter: AdapterGeometry
) -> None:
    """⚠️ **保持の締結は貫通ボルトとナットで受ける**（インサートを要さない）。

    金属インサートは「モータ反力を樹脂へ渡す接合部で、樹脂にねじを立てない」
    ための要素である（要件 2.6 / A-5 / `joints.ASSUMPTIONS`）。この家族が挟むのは
    **購入部品**（ゴミ箱）であり、⚠️ **相手側は樹脂ではないためインサートの
    居場所が無い**。

    ⚠️ **形の側がその通りになっていることを、寸法の関係として固定する。**
    締結の軸（半径方向）に沿ってアダプタが持つ肉は立ち上がりの肉厚
    `adapter.wall_thickness_mm` だけであり、それは上流
    `JointPolicy.insert_length_mm` より薄い——インサートを要求する記録に戻れば、
    ⚠️ **入るはずの座がどこにも作れない**。座ぐり（当たり面）はそのまま残る
    （`test_chassis_invariants.py` が実面積を測っている）。
    """
    from chassis_mechanism.joints import derive_joints

    params, layout = shipped
    wall_mm = params.chassis.adapter.wall_thickness_mm  # type: ignore[attr-defined]
    insert_mm = params.joint.insert_length_mm  # type: ignore[attr-defined]

    # 締結の軸に沿った肉は立ち上がりの肉厚そのものである。
    assert adapter.outer_radius_mm - adapter.seat_bottom_radius_mm == pytest.approx(
        wall_mm
    )
    assert wall_mm < insert_mm, (
        "肉厚がインサート長を超えたなら、この家族の受け方を見直してよい"
        "（それでも相手は購入部品であり、インサートが要る理由は生じない）"
    )
    # 座ぐりは肉を貫かない（当たり面は実現し、インサートの座は無い）。
    assert adapter.retention_spotface_depth_mm < wall_mm

    retention = next(
        spec
        for spec in derive_joints(layout, params)  # type: ignore[arg-type]
        if spec.name == "adapter__trash_can"
    )
    assert retention.insert_count == 0
    assert retention.bolt_count == adapter.retention_bolt_count


# ---------------------------------------------------------------------------
# バッテリトレイと段積み土台（タスク 3.4 / 要件 7.1-7.5, 7.10-7.13, 8.3, 8.5）
#
# ⚠️ **本節は形状ライブラリを要さない側である。** 実形状に対する不変条件は
# `test_chassis_invariants.py` が持つ（design.md `#### Shapes`）。ここが固定
# するのは、**形を作る前に決まる**数と成立条件である。
# ---------------------------------------------------------------------------


def _with_board(shipped: tuple[object, object], **changes: object) -> object:
    """基板デッキの寸法パラメータだけを差し替えた `ResolvedParams` を作る。"""
    import dataclasses

    params, _ = shipped
    return dataclasses.replace(
        params,
        chassis=dataclasses.replace(
            params.chassis,  # type: ignore[attr-defined]
            board=dataclasses.replace(params.chassis.board, **changes),  # type: ignore[attr-defined]
        ),
    )


def _with_battery(shipped: tuple[object, object], **changes: object) -> object:
    import dataclasses

    params, _ = shipped
    return dataclasses.replace(
        params,
        chassis=dataclasses.replace(
            params.chassis,  # type: ignore[attr-defined]
            battery=dataclasses.replace(params.chassis.battery, **changes),  # type: ignore[attr-defined]
        ),
    )


@pytest.fixture(scope="module")
def deck(shipped: tuple[object, object]) -> DeckStackGeometry:
    params, layout = shipped
    return deck_stack_geometry(params, layout)  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def tray(shipped: tuple[object, object]) -> BatteryTrayGeometry:
    params, layout = shipped
    return battery_tray_geometry(params, layout)  # type: ignore[arg-type]


def test_the_deck_geometry_needs_no_shape_library(
    shipped: tuple[object, object], deck: DeckStackGeometry, tray: BatteryTrayGeometry
) -> None:
    """⚠️ 段とトレイの**全数値と成立条件**は形状ライブラリ無しで評価できる。

    `stand_geometry` / `drive_base_geometry` / `adapter_geometry` と同じ規律で
    ある（design.md「Allowed Dependencies」）。形が成立するかを知るために CAD を
    要求しない。
    """
    source = ast.parse(SHAPES_SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(source):
        if isinstance(node, ast.FunctionDef) and node.name in {
            "deck_stack_geometry",
            "battery_tray_geometry",
        }:
            for inner in ast.walk(node):
                assert not isinstance(inner, (ast.Import, ast.ImportFrom)), node.name
    assert deck.board_plate_radius_mm > 0.0
    assert tray.envelope.x_mm > 0.0


def test_each_deck_height_is_derived_from_the_can_bottom_and_upstream_values(
    shipped: tuple[object, object], deck: DeckStackGeometry
) -> None:
    """⚠️ **段の高さは缶の底の面から積み上げて決まる**（要件 7.10, 7.11）。

    ⚠️ **どの段が何を担うかを数の並びとして固定する**（要件 7.10 が「各段の
    高さと担当する搭載物を記録する」ことを求めている）。

      - 基板デッキ: 缶の底 ＋ 缶の肉厚（残る縁）＋ 隙間 の高さに下面がある
      - 受け止めデッキ: 基板面 ＋ 部品の高さ ＋ 放熱の隙間 の上に下面がある
    """
    params, _ = shipped
    board = params.chassis.board  # type: ignore[attr-defined]
    can = params.trash_can  # type: ignore[attr-defined]
    adapter = adapter_geometry(params, derive_layout(params))  # type: ignore[arg-type]

    assert deck.can_bottom_height_mm == pytest.approx(adapter.floor_top_height_mm)
    assert deck.board_plate_bottom_height_mm == pytest.approx(
        deck.can_bottom_height_mm + can.bottom_thickness_mm + board.can_clearance_mm
    )
    assert deck.board_plate_top_height_mm == pytest.approx(
        deck.board_plate_bottom_height_mm + board.deck_thickness_mm
    )
    assert deck.board_plane_height_mm == pytest.approx(
        deck.board_plate_top_height_mm + board.standoff_height_mm
    )
    assert deck.component_top_height_mm == pytest.approx(
        deck.board_plane_height_mm + board.component_height_mm
    )
    # ⚠️ **「部品の頭 ＋ 放熱の隙間」を空けるのは板ではなく筒の下端である。**
    # 板だけで高さを決めると、筒が重ね代ぶん下へ垂れて搭載部品の居場所を奪う。
    assert deck.catch_tube_bottom_height_mm == pytest.approx(
        deck.component_top_height_mm + board.cooling_gap_mm
    )
    assert deck.catch_plate_bottom_height_mm == pytest.approx(
        deck.catch_tube_bottom_height_mm + deck.collar_length_mm
    )
    assert deck.riser_top_height_mm == pytest.approx(deck.catch_plate_bottom_height_mm)
    # ⚠️ 段は缶の口より下に収まる（受け止め面が缶の外へ出ない）。
    assert deck.catch_plate_top_height_mm < deck.can_mouth_height_mm


def test_each_deck_outline_follows_the_can_diameter_at_its_own_height(
    shipped: tuple[object, object], deck: DeckStackGeometry
) -> None:
    """⚠️ **段の外形はその高さの缶の内径から導かれる**（要件 7.11）。

    ⚠️ **缶はテーパーで上へ広がるため、2つの段の径は等しくない。** 期待値は
    上流の採寸値だけから独立に組み立てる（`joints` の式を再実行しない）。
    """
    import math

    params, _ = shipped
    can = params.trash_can  # type: ignore[attr-defined]
    clearance_mm = params.chassis.board.can_clearance_mm  # type: ignore[attr-defined]
    slope = math.tan(math.radians(can.taper_deg))
    inner_at_bottom_mm = can.bottom_outer_diameter_mm / 2.0 - can.bottom_thickness_mm

    for radius_mm, bottom_mm in (
        (deck.board_plate_radius_mm, deck.board_plate_bottom_height_mm),
        (deck.catch_plate_radius_mm, deck.catch_plate_bottom_height_mm),
    ):
        expected_mm = (
            inner_at_bottom_mm
            + (bottom_mm - deck.can_bottom_height_mm) * slope
            - clearance_mm
        )
        assert radius_mm == pytest.approx(expected_mm, abs=1e-9)
        assert deck.can_inner_radius_mm(bottom_mm) == pytest.approx(
            radius_mm + clearance_mm, abs=1e-9
        )
    assert deck.catch_plate_radius_mm > deck.board_plate_radius_mm


def test_the_deck_split_comes_from_the_upstream_segment_derivation(
    shipped: tuple[object, object], deck: DeckStackGeometry
) -> None:
    """⚠️ 段が造形可能寸法を超えれば上流の分割数導出に従って分割する（要件 7.13）。"""
    from catch_mechanism import required_segment_count

    from chassis_mechanism.joints import (
        board_deck_outer_diameter_mm,
        catch_deck_outer_diameter_mm,
        segment_counts,
    )

    params, _ = shipped
    counts = segment_counts(params)  # type: ignore[arg-type]
    assert deck.board_segment_count == counts["board_deck"]
    assert deck.catch_segment_count == counts["catch_deck"]
    assert counts["board_deck"] == required_segment_count(
        board_deck_outer_diameter_mm(params), params.printing  # type: ignore[arg-type]
    )
    assert counts["catch_deck"] == required_segment_count(
        catch_deck_outer_diameter_mm(params), params.printing  # type: ignore[arg-type]
    )
    # ⚠️ 出荷の寸法では、上の段だけが分割される（分割が名ばかりでないこと）。
    assert deck.board_segment_count == 1
    assert deck.catch_segment_count > 1


def test_a_narrower_printer_splits_the_lower_deck_too(
    shipped: tuple[object, object]
) -> None:
    """⚠️ 造形面を狭めれば下の段も分割される（分割数が導出であること。要件 2.1）。"""
    import dataclasses

    from chassis_mechanism.joints import segment_counts

    params, layout = shipped
    # ⚠️ **駆動ベースが成立する範囲で狭める。** 120mm まで狭めると中央部が
    # 先に造形可能寸法を超え、⚠️ 段の分割について何も言えなくなる。
    narrow = dataclasses.replace(
        params,
        printing=dataclasses.replace(
            params.printing, build_x_mm=160.0, build_y_mm=160.0  # type: ignore[attr-defined]
        ),
    )
    counts = segment_counts(narrow)
    assert counts["board_deck"] > 1
    assert deck_stack_geometry(narrow, layout).board_segment_count == counts[  # type: ignore[arg-type]
        "board_deck"
    ]


def test_the_riser_outer_diameter_is_the_hub_plate_diameter(
    shipped: tuple[object, object], deck: DeckStackGeometry
) -> None:
    """⚠️ **立ち上がりの外径は中央部の外径そのものである**（要件 7.10）。

    段が下から上へ抜けられる道はアダプタの床の内縁の内側しかなく、その内縁は
    中央部の外径に嵌め合い隙間を足したものである。⚠️ **別の数を置けば、掴む面が
    消えるか床と食い合う。**
    """
    params, layout = shipped
    adapter = adapter_geometry(params, layout)  # type: ignore[arg-type]
    assert deck.riser_outer_radius_mm == pytest.approx(
        params.chassis.base.hub_outer_diameter_mm / 2.0  # type: ignore[attr-defined]
    )
    assert adapter.skirt_inner_radius_mm > deck.riser_outer_radius_mm
    assert deck.riser_inner_radius_mm == pytest.approx(
        deck.riser_outer_radius_mm - deck.deck_thickness_mm
    )


def test_the_board_deck_offers_at_least_the_mounting_area_the_boards_need(
    shipped: tuple[object, object], deck: DeckStackGeometry
) -> None:
    """⚠️ **取付面が要る面積以上ある**（要件 7.4 / design.md 決定 4b）。

    決定 4b は「必要な 19,600mm^2 に対して1段で足りる」と**面積で**述べている。
    ⚠️ **内接する長方形として読まない**——Ø182 に一辺 140 の正方形は入らず、
    決定 4b はその読み方を採っていない。
    """
    params, _ = shipped
    board = params.chassis.board  # type: ignore[attr-defined]
    assert deck.required_area_mm2 == pytest.approx(board.mount_area_mm2)
    assert deck.usable_area_mm2 >= deck.required_area_mm2


def test_a_deck_too_small_for_the_boards_is_rejected(
    shipped: tuple[object, object]
) -> None:
    """⚠️ 取付面が足りない寸法は拒否される（黙って狭い段を作らない）。"""
    _, layout = shipped
    bigger = _with_board(shipped, mount_area_mm2=90000.0)
    with pytest.raises(GeometryError) as excinfo:
        deck_stack_geometry(bigger, layout)  # type: ignore[arg-type]
    assert "board.mount_area_mm2" in str(excinfo.value)


def test_the_bolt_between_the_decks_sits_in_the_middle_of_the_overlap(
    shipped: tuple[object, object], deck: DeckStackGeometry
) -> None:
    """⚠️ **段どうしのボルトは重ね代の中央にある**（要件 2.9）。

    重ね代は座の外径の2倍であり（`joints.DECK_COLLAR_LENGTH_FORMULA`）、中央に
    置けば座の環の上下に半径ぶんずつの肉が残る。⚠️ **部品の頭との関係で置かない**
    ——重ね代の外へ出た座は、どちらの筒にも載らない。
    """
    _, _ = shipped
    assert deck.deck_bolt_height_mm == pytest.approx(
        deck.catch_plate_bottom_height_mm - deck.collar_length_mm / 2.0
    )
    assert (
        deck.catch_tube_bottom_height_mm + deck.boss_diameter_mm / 2.0
        <= deck.deck_bolt_height_mm
        <= deck.catch_plate_bottom_height_mm - deck.boss_diameter_mm / 2.0
    )
    # ⚠️ 搭載部品の頭より十分に上にある（工具が水平に入る）。
    assert deck.deck_bolt_height_mm > deck.component_top_height_mm


def test_the_catch_deck_tube_may_not_reach_into_the_component_envelope(
    shipped: tuple[object, object], deck: DeckStackGeometry
) -> None:
    """⚠️ **筒の下端が「部品の頭 ＋ 放熱の隙間」を空ける**（要件 7.4, 7.5）。

    ⚠️ **これは一度落とした落とし穴である。** 板の下面だけを「部品の頭 ＋
    隙間」に置くと、筒が重ね代ぶん下へ垂れて搭載部品の居場所へ入り込み、
    ⚠️ **それでも取付面の見積もりは塞がれた面積を数えたまま「足りている」と
    述べる**。ここが発火するのは `joints.catch_deck_rise_mm` が重ね代を
    落としたときである。
    """
    import dataclasses

    params, layout = shipped
    assert deck.catch_tube_bottom_height_mm == pytest.approx(
        deck.component_top_height_mm + deck.cooling_gap_mm
    )

    # ⚠️ 空振りでないこと: 重ね代を伸ばせば（＝板の高さを据え置いたまま筒だけ
    # 下げれば）筒は部品の居場所へ落ちてくる。段の板厚を通じて重ね代を動かせない
    # ため、上流のインサート外径を太らせて重ね代を伸ばす。
    thicker = dataclasses.replace(
        params,
        joint=dataclasses.replace(
            params.joint,  # type: ignore[attr-defined]
            insert_outer_diameter_mm=params.joint.insert_outer_diameter_mm * 1.4,  # type: ignore[attr-defined]
        ),
    )
    # 重ね代が伸びれば板も一緒に上がるため、正しい式のままでは発火しない。
    assert deck_stack_geometry(thicker, layout).catch_tube_bottom_height_mm == (  # type: ignore[arg-type]
        pytest.approx(deck.component_top_height_mm + deck.cooling_gap_mm)
    )


def test_the_recorded_board_hold_height_must_match_what_the_deck_gives(
    shipped: tuple[object, object], deck: DeckStackGeometry
) -> None:
    """⚠️ 記録された保持高さが段の与える収まりの中にある（要件 7.8）。

    ⚠️ **記録と実物の置き場所が食い違えば、合成重心の見積もりは実機と別の機体に
    ついて述べる。**
    """
    params, layout = shipped
    hold_mm = params.chassis.board.hold_height_mm  # type: ignore[attr-defined]
    assert deck.board_plane_height_mm <= hold_mm <= deck.component_top_height_mm

    for bad_mm in (deck.board_plane_height_mm - 1.0, deck.component_top_height_mm + 1.0):
        with pytest.raises(GeometryError) as excinfo:
            deck_stack_geometry(_with_board(shipped, hold_height_mm=bad_mm), layout)  # type: ignore[arg-type]
        assert "hold_height_mm" in str(excinfo.value)


def test_the_top_deck_must_clear_the_upstream_liner_flat_minimum(
    shipped: tuple[object, object], deck: DeckStackGeometry
) -> None:
    """⚠️ **最上段の平面の下限は上流が正である**（要件 7.12）。

    ⚠️ **本 Spec は読むだけである**——数を書き写せば、上流が緩衝材の方針を
    変えたときに黙って古い下限を主張し続ける。
    """
    from catch_mechanism import load_params as upstream_load_params

    params, layout = shipped
    upstream = upstream_load_params().retention
    assert deck.liner_flat_min_diameter_mm == upstream.liner_flat_min_diameter_mm
    assert params.retention.liner_flat_min_diameter_mm == (  # type: ignore[attr-defined]
        upstream.liner_flat_min_diameter_mm
    )
    assert 2.0 * deck.catch_plate_radius_mm >= deck.liner_flat_min_diameter_mm

    # ⚠️ 下限が上がれば形の側が拒否する（下限が飾りでないこと）。
    import dataclasses

    demanding = dataclasses.replace(
        params,
        retention=dataclasses.replace(
            params.retention,  # type: ignore[attr-defined]
            liner_flat_min_diameter_mm=4.0 * deck.catch_plate_radius_mm,
        ),
    )
    with pytest.raises(GeometryError) as excinfo:
        deck_stack_geometry(demanding, layout)  # type: ignore[arg-type]
    assert "liner_flat_min_diameter_mm" in str(excinfo.value)


def test_the_deck_stack_carries_a_mounting_point_for_every_board(
    shipped: tuple[object, object], deck: DeckStackGeometry
) -> None:
    """⚠️ **モータドライバ3台＋制御基板＋電圧監視＋5V 生成**（要件 7.4）。

    ⚠️ 台数が設定値なのはドライバだけである。残り3枚は要件が名指しで挙げて
    おり、⚠️ **設定で減らせる形にしない**。
    """
    params, _ = shipped
    assert deck.module_count == params.chassis.board.driver_count + 3  # type: ignore[attr-defined]
    assert len(deck.mount_boss_angles_deg) == 2 * deck.module_count
    assert len(set(deck.mount_boss_angles_deg)) == len(deck.mount_boss_angles_deg)
    assert deck.insert_bore_diameter_mm == params.joint.insert_outer_diameter_mm  # type: ignore[attr-defined]
    assert deck.insert_bore_depth_mm == params.joint.insert_length_mm  # type: ignore[attr-defined]
    assert deck.insert_bore_depth_mm < deck.deck_thickness_mm


def test_more_drivers_add_more_mounting_points(shipped: tuple[object, object]) -> None:
    """⚠️ ドライバを増やせば取付箇所が増える（設定から導出であること）。"""
    _, layout = shipped
    more = _with_board(shipped, driver_count=4)
    assert (
        deck_stack_geometry(more, layout).module_count  # type: ignore[arg-type]
        == deck_stack_geometry(*shipped).module_count + 1  # type: ignore[arg-type]
    )


def test_the_battery_sits_below_the_drive_base_and_comes_out_sideways(
    shipped: tuple[object, object], tray: BatteryTrayGeometry
) -> None:
    """⚠️ **バッテリは中央部の下に吊られ、半径方向へ引き抜ける**（要件 7.1, 7.2）。"""
    params, layout = shipped
    battery = params.chassis.battery  # type: ignore[attr-defined]
    base = drive_base_geometry(params, layout)  # type: ignore[arg-type]

    assert tray.tray_top_height_mm == pytest.approx(base.underside_height_mm)
    assert tray.battery_top_height_mm < tray.tray_top_height_mm
    assert tray.battery_top_height_mm - tray.battery_bottom_height_mm == pytest.approx(
        battery.height_mm
    )
    assert tray.floor_bottom_height_mm < tray.battery_bottom_height_mm
    assert (
        tray.floor_bottom_height_mm
        > params.chassis.clearance.min_ground_clearance_mm  # type: ignore[attr-defined]
    )
    # ⚠️ 引き抜く向きにはモータ取付部が無い。
    for angle_deg in layout.wheel_angles_deg:  # type: ignore[attr-defined]
        assert abs(
            (tray.extraction_angle_deg - angle_deg + 180.0) % 360.0 - 180.0
        ) > 1e-6
    # ⚠️ 記録された保持高さがポケットの中にある（要件 7.8）。
    assert (
        tray.battery_bottom_height_mm
        <= battery.hold_height_mm
        <= tray.battery_top_height_mm
    )


def test_an_even_wheel_count_leaves_no_way_to_pull_the_battery_out(
    shipped: tuple[object, object]
) -> None:
    """⚠️ 真後ろが別のアームになる配置は拒否される（要件 7.2）。

    ⚠️ **黙って作らない**——駆動ベースを分解せずに着脱できる経路が無い機体を
    「作れた」と報告しないための対である。
    """
    import dataclasses

    params, _ = shipped
    four = dataclasses.replace(
        params,
        chassis=dataclasses.replace(
            params.chassis,  # type: ignore[attr-defined]
            base=dataclasses.replace(params.chassis.base, wheel_count=4),  # type: ignore[attr-defined]
            stand=dataclasses.replace(params.chassis.stand, leg_count=4),  # type: ignore[attr-defined]
        ),
    )
    layout = derive_layout(four)
    with pytest.raises(GeometryError) as excinfo:
        battery_tray_geometry(four, layout)
    assert "wheel_count" in str(excinfo.value)


def test_the_tray_ears_sit_on_the_solid_band_of_the_arm(
    shipped: tuple[object, object], tray: BatteryTrayGeometry
) -> None:
    """⚠️ **耳は二股の外・長穴の外の中実の帯にしか載らない**（要件 2.9, 3.8）。

    二股の中は中央部の舌と `hub_plate__motor_arm_*` のボルトが占めており、
    ⚠️ 長穴の縁に掛かった座は当たり面にならない。
    """
    from chassis_mechanism.joints import battery_tray_ear_length_mm

    params, layout = shipped
    base = drive_base_geometry(params, layout)  # type: ignore[arg-type]
    assert tray.ear_inner_radius_mm >= base.fork_root_radius_mm
    assert tray.ear_inner_radius_mm >= base.slot_center_radius_mm + base.slot_length_mm / 2.0
    assert tray.ear_outer_radius_mm <= base.arm_outer_radius_mm
    assert tray.ear_outer_radius_mm - tray.ear_inner_radius_mm == pytest.approx(
        battery_tray_ear_length_mm(layout, params), abs=1e-9  # type: ignore[arg-type]
    )
    # ⚠️ ボルトの本数と並びは `joints` が正である（形の側で数え直さない）。
    assert len(tray.bolt_radii_mm) == tray.bolt_count
    for near_mm, far_mm in zip(tray.bolt_radii_mm, tray.bolt_radii_mm[1:], strict=False):
        assert far_mm - near_mm == pytest.approx(tray.boss_diameter_mm, abs=1e-9)


def test_the_tray_holds_the_main_fuse_beside_the_battery(
    shipped: tuple[object, object], tray: BatteryTrayGeometry
) -> None:
    """⚠️ 主ヒューズをバッテリ直近へ置ける保持箇所がある（要件 8.5）。"""
    params, layout = shipped
    battery = params.chassis.battery  # type: ignore[attr-defined]
    assert tray.fuse_bay_outer_y_mm - tray.fuse_bay_inner_y_mm == pytest.approx(
        battery.fuse_holder_width_mm
    )
    assert 2.0 * tray.fuse_bay_half_length_mm == pytest.approx(
        battery.fuse_holder_length_mm
    )
    assert tray.fuse_bay_inner_y_mm == pytest.approx(tray.outer_half_width_mm)

    huge = _with_battery(shipped, fuse_holder_length_mm=400.0)
    with pytest.raises(GeometryError) as excinfo:
        battery_tray_geometry(huge, layout)  # type: ignore[arg-type]
    assert "fuse_holder_length_mm" in str(excinfo.value)


def test_the_main_switch_provision_is_a_band_and_not_a_position(
    shipped: tuple[object, object], deck: DeckStackGeometry
) -> None:
    """⚠️ **位置は決めない。決まっていないことを未決のまま持つ**（要件 8.1, 8.3）。

    ⚠️ タスク 5.6 が `power.main_switch_position` と
    `power.main_switch_height_mm` を決める。本タスクが与えるのは「缶を載せても
    外から届く高さの帯」だけであり、⚠️ **もっともらしい既定値を置かない**。
    """
    params, layout = shipped
    power = params.chassis.power  # type: ignore[attr-defined]
    assert power.main_switch_present is True
    assert power.main_switch_position is None
    assert power.main_switch_height_mm is None

    base = drive_base_geometry(params, layout)  # type: ignore[arg-type]
    low_mm, high_mm = deck.switch_provision_band_mm
    assert low_mm == pytest.approx(base.underside_height_mm)
    assert high_mm == pytest.approx(deck.can_bottom_height_mm)
    assert low_mm < high_mm


def test_the_deck_geometry_is_deterministic(shipped: tuple[object, object]) -> None:
    """同一の寸法パラメータからの再導出は同一の値を返す（要件 1.12）。"""
    params, layout = shipped
    assert deck_stack_geometry(params, layout) == deck_stack_geometry(params, layout)  # type: ignore[arg-type]
    assert battery_tray_geometry(params, layout) == battery_tray_geometry(  # type: ignore[arg-type]
        params, layout
    )


@requires_cad
def test_the_deck_and_tray_solids_are_deterministic(
    shipped: tuple[object, object]
) -> None:
    """同一の寸法パラメータからの再構築は同一の形状指標を返す（要件 1.12）。"""
    from chassis_mechanism.shapes import build_battery_tray, build_deck_stack

    params, layout = shipped
    for builder in (build_battery_tray, build_deck_stack):
        first = builder(params, layout)  # type: ignore[arg-type]
        second = builder(params, layout)  # type: ignore[arg-type]
        assert tuple(part.metrics for part in first) == tuple(
            part.metrics for part in second
        )


# ---------------------------------------------------------------------------
# 8. 配線ガイド（タスク 3.5 / 要件 4.6, 7.6, 7.7, 8.4, 8.7 / design.md 決定 8）
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def guide(shipped: tuple[object, object]) -> CableGuideGeometry:
    params, layout = shipped
    return cable_guide_geometry(params, layout)  # type: ignore[arg-type]


def _replace_cable(params: object, **changes: object) -> object:
    """`cable` 群だけを差し替えた `ResolvedParams` を作る。"""
    from dataclasses import replace

    return replace(
        params,  # type: ignore[type-var]
        chassis=replace(
            params.chassis, cable=replace(params.chassis.cable, **changes)  # type: ignore[attr-defined]
        ),
    )


def _replace_power(params: object, **changes: object) -> object:
    """`power` 群だけを差し替えた `ResolvedParams` を作る。"""
    from dataclasses import replace

    return replace(
        params,  # type: ignore[type-var]
        chassis=replace(
            params.chassis, power=replace(params.chassis.power, **changes)  # type: ignore[attr-defined]
        ),
    )


def test_the_three_systems_are_three_separate_routes(
    guide: CableGuideGeometry,
) -> None:
    """⚠️ **色ではなく経路そのものを分ける**（要件 7.7 / design.md 決定 8）。

    ⚠️ **取り違えるとエンコーダが飛ぶ。** 3系統は独立した通路を持ち、通路の間には
    壁の厚さぶんの肉が残る——⚠️ **重なる通路や、壁の無い隣り合わせを作らない。**
    """
    assert tuple(route.name for route in guide.routes) == CABLE_ROUTE_NAMES
    assert len(guide.routes) == 3
    for route in guide.routes:
        assert route.outer_y_mm - route.inner_y_mm == pytest.approx(
            guide.channel_width_mm
        )
    for left, right in zip(guide.routes, guide.routes[1:], strict=False):
        gap_mm = right.inner_y_mm - left.outer_y_mm
        assert gap_mm == pytest.approx(guide.wall_thickness_mm)
        assert gap_mm > 0.0
    # 内側と外側にも壁が残る（通路が部品の外へ開いていない）。
    assert guide.routes[0].inner_y_mm - guide.skirt_inner_y_mm == pytest.approx(
        guide.wall_thickness_mm
    )
    assert guide.routes_outer_y_mm - guide.routes[-1].outer_y_mm == pytest.approx(
        guide.wall_thickness_mm
    )


def test_the_route_lookup_rejects_an_unknown_system(
    guide: CableGuideGeometry,
) -> None:
    """⚠️ 引けなかった系統について検査が黙らないよう、未知の名は拒否する。"""
    assert guide.route("supply") is guide.routes[-1]
    with pytest.raises(GeometryError) as excinfo:
        guide.route("hydraulic")
    assert "hydraulic" in str(excinfo.value)


def test_the_cable_lowest_point_is_the_height_the_clearance_calculation_uses(
    shipped: tuple[object, object], guide: CableGuideGeometry
) -> None:
    """⚠️ **配線の最下点は隙間の算出対象そのものである**（要件 4.2, 4.6）。

    ⚠️ **形の側で別の高さを決めない。** ガイドが保持する最下点と `clearance` が
    `cable` として返す高さが食い違えば、「隙間は足りている」という判定は形について
    何も言っていない。
    """
    from chassis_mechanism.clearance import clearance_items

    params, layout = shipped
    heights = {
        item.name: item.height_mm
        for item in clearance_items(layout, params.chassis)  # type: ignore[arg-type,attr-defined]
    }
    assert guide.cable_lowest_height_mm == pytest.approx(heights["cable"])


def test_lowering_the_cable_offset_lowers_the_guide_with_it(
    shipped: tuple[object, object]
) -> None:
    """⚠️ **空振りでないこと**: オフセットを下げると保持箇所も同じだけ下がる。"""
    from dataclasses import replace

    from chassis_mechanism.clearance import clearance_items

    params, layout = shipped
    lowered = replace(
        params,  # type: ignore[type-var]
        chassis=replace(
            params.chassis,  # type: ignore[attr-defined]
            clearance=replace(
                params.chassis.clearance, cable_lowest_offset_mm=15.0  # type: ignore[attr-defined]
            ),
        ),
    )
    guide = cable_guide_geometry(lowered, layout)  # type: ignore[arg-type]
    heights = {
        item.name: item.height_mm
        for item in clearance_items(layout, lowered.chassis)  # type: ignore[arg-type,attr-defined]
    }
    assert guide.cable_lowest_height_mm == pytest.approx(heights["cable"])
    shipped_mm = cable_guide_geometry(params, layout).cable_lowest_height_mm  # type: ignore[arg-type]
    assert guide.cable_lowest_height_mm == pytest.approx(shipped_mm - 4.0)
    # ⚠️ 渡りの通路の内高もオフセットに追随する（床の側の値で高さを決めない）。
    assert guide.crossing_channel_bottom_mm == pytest.approx(
        guide.cable_lowest_height_mm + params.chassis.cable.wall_thickness_mm  # type: ignore[attr-defined]
    )


def test_an_offset_too_small_for_the_crossing_channel_is_rejected(
    shipped: tuple[object, object]
) -> None:
    """⚠️ **空振りでないこと**: 渡りの通路は「床の側の値」では成立しない。

    ⚠️ **配線の最下点は隙間の算出対象そのものである**（要件 4.2）。その値が
    通路の内寸と壁の厚さを収めなければ、⚠️ **経路は繋がらないまま「隙間は
    足りている」という判定だけが残る**。
    """
    from dataclasses import replace

    params, layout = shipped
    shallow = replace(
        params,  # type: ignore[type-var]
        chassis=replace(
            params.chassis,  # type: ignore[attr-defined]
            clearance=replace(
                params.chassis.clearance, cable_lowest_offset_mm=6.0  # type: ignore[attr-defined]
            ),
        ),
    )
    with pytest.raises(GeometryError) as excinfo:
        cable_guide_geometry(shallow, layout)  # type: ignore[arg-type]
    message = str(excinfo.value)
    assert "cable_lowest_offset_mm" in message
    assert "wall_thickness_mm" in message


def test_a_holding_point_below_the_ground_floor_is_rejected(
    shipped: tuple[object, object]
) -> None:
    """⚠️ 保持箇所が床の下限を割る設定を黙って作らない（要件 4.4, 4.6）。"""
    from dataclasses import replace

    params, layout = shipped
    sagging = replace(
        params,  # type: ignore[type-var]
        chassis=replace(
            params.chassis,  # type: ignore[attr-defined]
            clearance=replace(
                params.chassis.clearance, cable_lowest_offset_mm=500.0  # type: ignore[attr-defined]
            ),
        ),
    )
    with pytest.raises(GeometryError) as excinfo:
        cable_guide_geometry(sagging, layout)  # type: ignore[arg-type]
    message = str(excinfo.value)
    assert "cable_lowest_offset_mm" in message
    assert repr(500.0) in message


def test_the_guide_keeps_clear_of_the_adapter_the_ear_and_the_wheel(
    shipped: tuple[object, object], guide: CableGuideGeometry
) -> None:
    """⚠️ **回転部と既に置かれている部品を、半径と接線の両方で避ける**（要件 7.6）。

    ⚠️ **「外側だから当たらない」ではない。** 板はアダプタの外周より外、裾はホイール
    より内側（トレイの耳の内側）に留まり、耳の帯そのものは通らない。
    """
    params, layout = shipped
    adapter = adapter_geometry(params, layout)  # type: ignore[arg-type]
    tray = battery_tray_geometry(params, layout)  # type: ignore[arg-type]
    base = drive_base_geometry(params, layout)  # type: ignore[arg-type]

    assert guide.plate_inner_radius_mm > adapter.outer_radius_mm
    assert guide.skirt_outer_radius_mm < tray.ear_inner_radius_mm
    assert guide.skirt_inner_y_mm > tray.web_outer_y_mm
    assert guide.skirt_inner_y_mm > base.arm_half_width_mm
    # ⚠️ **渡りはベース板の下を通る**（要件 7.6 の経路の残り半分）。ホイールは
    # 車軸まわりの円筒であり、⚠️ **隔てているのは高さではなく半径である**
    # ——渡りはホイールの内側面よりずっと内側を通る。
    assert guide.cable_lowest_height_mm < base.underside_height_mm
    assert guide.crossing_inner_radius_mm > tray.outer_half_length_mm
    assert guide.plate_inner_radius_mm > adapter.outer_radius_mm
    # ⚠️ ホイールの内側面より内側に留まる（半径でも隔てる）。
    assert guide.skirt_outer_radius_mm < layout.base_radius_mm  # type: ignore[attr-defined]


def test_the_guide_top_is_the_band_that_stays_reachable_with_the_can_on(
    shipped: tuple[object, object], guide: CableGuideGeometry
) -> None:
    """⚠️ 端子台と非常停止の余地は**缶を載せても外から届く帯**に収まる（要件 8.3）。"""
    params, layout = shipped
    deck = deck_stack_geometry(params, layout)  # type: ignore[arg-type]
    low_mm, high_mm = deck.switch_provision_band_mm
    assert guide.top_height_mm == pytest.approx(high_mm)
    # ⚠️ **帯に収まるのは「後から触る余地」だけである。** 渡りは帯より下（ベース
    # 板の下）を通るが、そこへ手を入れることは求められていない（要件 8.3 が
    # 求めるのは操作部が缶を載せても届くことである）。
    assert guide.crossing_top_height_mm == pytest.approx(low_mm)
    assert low_mm <= guide.estop_bolt_height_mm <= high_mm
    assert low_mm <= guide.estop_lead_out_height_mm <= high_mm
    assert low_mm <= guide.terminal_pad_bottom_height_mm <= high_mm


def test_the_terminal_block_provision_is_a_seat_and_its_screw_holes(
    guide: CableGuideGeometry,
) -> None:
    """⚠️ **端子台は保持箇所と経路であって、位置の決定ではない**（要件 8.4）。

    寸法も方式もタスク 5.6 が決める（`PowerParams` は未決）。ここが持つのは座と
    ねじ穴、そして⚠️ **そこへ至る経路**（`supply` の通路が座の隣に開く）だけである。
    """
    assert len(guide.terminal_bolt_y_mm) == 2
    # ⚠️ 2つのねじ穴は接線方向に並ぶ（半径方向の帯は狭く、長穴の移動量で動く）。
    assert abs(guide.terminal_bolt_y_mm[1] - guide.terminal_bolt_y_mm[0]) == (
        pytest.approx(guide.boss_diameter_mm)
    )
    assert guide.terminal_pad_outer_y_mm > guide.terminal_pad_inner_y_mm
    # ⚠️ 座は `supply` の通路と壁1枚で隣り合う（そこへ至る経路である）。
    supply = guide.route("supply")
    assert guide.terminal_pad_inner_y_mm == pytest.approx(supply.outer_y_mm)
    # 穴は座の肉に収まる（袋穴が板を突き抜けない）。
    assert guide.terminal_pad_bottom_height_mm < (
        guide.top_height_mm - guide.insert_bore_depth_mm
    )


def test_a_decided_terminal_block_that_does_not_fit_the_seat_is_rejected(
    shipped: tuple[object, object]
) -> None:
    """⚠️ **空振りでないこと**: 座を作ったことと、決まった端子台が載ることは別である。

    ⚠️ 未決（`None`）のあいだは何も主張しない——タスク 5.6 が寸法を入れた瞬間に
    この検査が働く。
    """
    params, layout = shipped
    assert params.chassis.power.terminal_block_length_mm is None  # type: ignore[attr-defined]
    oversized = _replace_power(params, terminal_block_length_mm=500.0)
    with pytest.raises(GeometryError) as excinfo:
        cable_guide_geometry(oversized, layout)  # type: ignore[arg-type]
    assert "terminal_block_length_mm" in str(excinfo.value)


def test_the_estop_provision_is_screw_holes_and_a_lead_out(
    shipped: tuple[object, object], guide: CableGuideGeometry
) -> None:
    """⚠️ **非常停止は「後から追加できる余地」だけを残す**（要件 8.6, 8.7）。

    方式の決着は本 Spec の対象外であり（要件 8.6）、`power.estop_provision` は
    未決のままである。⚠️ **ここで方式を決めない**——ねじ穴と配線の引き出しだけを残す。
    """
    params, _ = shipped
    assert params.chassis.power.estop_provision is None  # type: ignore[attr-defined]
    assert len(guide.estop_bolt_y_mm) == 2
    # ⚠️ 座はちょうど1つぶん離れて並ぶ（環が重ならない最小の間隔である）。
    assert abs(guide.estop_bolt_y_mm[1] - guide.estop_bolt_y_mm[0]) == pytest.approx(
        guide.boss_diameter_mm
    )
    # ⚠️ 引き出しは `supply` の通路へ開く（電源系の器物がそこにある）。
    assert guide.estop_lead_out_y_mm == pytest.approx(guide.route("supply").center_y_mm)
    # ⚠️ 取付ねじの座は袋穴であり、通路を貫かない。
    assert (
        guide.skirt_outer_radius_mm - guide.channel_outer_radius_mm
        > guide.insert_bore_depth_mm
    )


def test_a_channel_too_wide_for_the_skirt_is_rejected(
    shipped: tuple[object, object]
) -> None:
    """⚠️ **空振りでないこと**: 通路が太れば裾に肉が残らず、座ぐりが通路を貫く。

    ⚠️ **要求する取付面も併せて下げる。** 通し穴は⚠️ **輪ごと・系統ごとに1つ**
    （9箇所）であり、通路を太らせると⚠️ **先に基板デッキの取付面の見積もりが
    尽きる**——下げずに測ると、この検査は裾の肉ではなく取付面の関門を測って
    しまう（`mount_area_mm2` を下げるのは⚠️ **測る対象を裾へ戻すため**であって、
    裾の条件を緩めるためではない）。
    """
    from dataclasses import replace

    params, layout = shipped
    fat = _replace_cable(params, channel_width_mm=16.0)
    fat = replace(
        fat,  # type: ignore[type-var]
        chassis=replace(
            fat.chassis,  # type: ignore[attr-defined]
            board=replace(
                fat.chassis.board,  # type: ignore[attr-defined]
                mount_area_mm2=10000.0,
            ),
        ),
    )
    with pytest.raises(GeometryError) as excinfo:
        cable_guide_geometry(fat, layout)  # type: ignore[arg-type]
    message = str(excinfo.value)
    assert "channel_width_mm" in message
    assert "裾の外側" in message


def test_the_guide_does_not_include_machined_fits(
    shipped: tuple[object, object], guide: CableGuideGeometry
) -> None:
    """⚠️ 配線ガイドにも切削前提の嵌合を含めない（要件 2.11 / 決定 4）。"""
    params, _ = shipped
    chassis = params.chassis  # type: ignore[attr-defined]
    through_hole_mm = params.joint.through_hole_diameter_mm  # type: ignore[attr-defined]
    assert guide.bore_diameters_mm
    for diameter_mm in guide.bore_diameters_mm:
        assert diameter_mm >= through_hole_mm, diameter_mm
    mating_nominals_mm = (
        chassis.bracket.mount_hole_diameter_mm,
        chassis.motor.shaft_diameter_mm,
        chassis.hub.boss_diameter_mm,
        chassis.hub.bore_diameter_mm,
        chassis.wheel.center_bore_diameter_mm,
    )
    for diameter_mm in guide.bore_diameters_mm:
        for nominal_mm in mating_nominals_mm:
            assert diameter_mm != pytest.approx(nominal_mm), diameter_mm


def test_the_mount_screws_land_outboard_of_the_bracket_slots(
    shipped: tuple[object, object], guide: CableGuideGeometry
) -> None:
    """⚠️ 留めねじはブラケット取付長穴を避け、アームの上面の中実の帯へ入る。

    ⚠️ **長穴に掛かったねじはアームに何も掴んでいない。**
    """
    params, layout = shipped
    base = drive_base_geometry(params, layout)  # type: ignore[arg-type]
    slot_outer_mm = base.slot_center_radius_mm + base.slot_length_mm / 2.0
    assert len(guide.mount_bolt_radii_mm) == 2
    for radius_mm in guide.mount_bolt_radii_mm:
        assert radius_mm - guide.boss_diameter_mm / 2.0 >= slot_outer_mm
        assert radius_mm + guide.boss_diameter_mm / 2.0 <= base.arm_outer_radius_mm
    assert (
        guide.mount_bolt_radii_mm[1] - guide.mount_bolt_radii_mm[0]
        >= guide.boss_diameter_mm
    )
    # ⚠️ 座はアームの上面の上にあり、インサートはアームの肉に収まる。
    assert guide.insert_bore_depth_mm < base.arm_thickness_mm


def test_the_tray_ear_demands_more_of_the_arm_end_than_the_mount_screws(
    shipped: tuple[object, object], guide: CableGuideGeometry
) -> None:
    """⚠️ **留めねじの帯を先に拒むのはバッテリトレイの耳である**（順序を数で固定）。

    ガイドは「長穴の外端 ＋ 座半分」から「アームの外端 − 座半分」までに座2つが
    並ぶことを要求する。⚠️ **同じ帯へ耳がより厳しい条件を課している**——耳は
    長穴の外端から座の外径ぶん**外に始まり**、さらに座2つぶんの長さを要する。
    したがってガイド側の拒否は出荷の寸法からは届かない。⚠️ **その順序が変わった
    とき（耳の式や受け持つアームが変わったとき）に気付けるよう、ここで固定する**
    ——ガイド側の拒否はそのときのための保険である。
    """
    params, layout = shipped
    base = drive_base_geometry(params, layout)  # type: ignore[arg-type]
    tray = battery_tray_geometry(params, layout)  # type: ignore[arg-type]
    slot_outer_mm = base.slot_center_radius_mm + base.slot_length_mm / 2.0

    screws_need_mm = 2.0 * guide.boss_diameter_mm
    ear_needs_mm = tray.ear_outer_radius_mm - slot_outer_mm
    assert ear_needs_mm > screws_need_mm
    # 出荷の寸法では耳もガイドも収まっている（どちらの拒否も鳴っていない）。
    assert tray.ear_outer_radius_mm <= base.arm_outer_radius_mm
    assert (
        guide.mount_bolt_radii_mm[1] - guide.mount_bolt_radii_mm[0]
        >= guide.boss_diameter_mm
    )


def test_a_can_that_pushes_the_plate_outward_leaves_no_room_for_the_routes(
    shipped: tuple[object, object]
) -> None:
    """⚠️ **空振りでないこと**: 缶が太れば板の内縁が外へ動き、通路が裾に収まらない。

    ⚠️ **上流の採寸値が変わればガイドも追随する**（要件 6.9 と同じ向きの依存で
    ある）——板はアダプタの外周より外にしか置けず、裾の外端はトレイの耳が決める。
    """
    from dataclasses import replace

    params, layout = shipped
    wide_can = replace(
        params,  # type: ignore[type-var]
        trash_can=replace(
            params.trash_can,  # type: ignore[attr-defined]
            bottom_outer_diameter_mm=200.0,
            bottom_flat_diameter_mm=190.0,
        ),
    )
    with pytest.raises(GeometryError) as excinfo:
        cable_guide_geometry(wide_can, layout)  # type: ignore[arg-type]
    assert "裾の外側" in str(excinfo.value)


def test_the_passage_clears_the_battery_tray_in_every_mounting_angle(
    shipped: tuple[object, object], guide: CableGuideGeometry
) -> None:
    """⚠️ **通し穴の下は3つの取付角すべてで自由空間である**（要件 7.6）。

    トレイは1本のアームにしか無いが、⚠️ **配線ガイドは3点とも同一形状**である
    ——回した位置でトレイの壁の上へ来れば、そこだけ配線の降りる先が無い機体になる。
    ⚠️ **1点だけ測って済ませない。** 穴は⚠️ **系統ごとに1つ**あり、3系統 × 3取付角の
    9通りすべてで測る。

    ⚠️ **見るのは実際の隙間である。** かつてここには「穴の `y` はヒューズホルダの
    置き場の `y` より外」という**座標軸ごとの粗い上界**も置いていたが、
    ⚠️ **置き場は `x` の広がりを持つ箱**であり、その外側を回れば `y` が小さくても
    当たらない——粗い上界は自由な弧の外側半分を丸ごと禁じ、3系統を分けて通す
    余地をそこで失っていた。⚠️ **実際の隙間（`_tray_footprint_gap_mm`）のほうが
    強い主張である**（2次元の距離であり、3つの取付角すべてで測る）。
    """
    import math

    from chassis_mechanism.shapes import (
        _JOINT_FIT_CLEARANCE_MM,
        _tray_footprint_gap_mm,
    )

    params, layout = shipped
    tray = battery_tray_geometry(params, layout)  # type: ignore[arg-type]
    deck = deck_stack_geometry(params, layout)  # type: ignore[arg-type]
    radius_mm = guide.passage_diameter_mm / 2.0

    # 穴は段の立ち上がりの内側にある（出た配線はそのまま筒の中を昇る）。
    assert guide.passage_center_radius_mm + radius_mm <= deck.riser_inner_radius_mm

    for route in guide.routes:
        # 穴は耳へつながる腕の外（ガイドの側）——内側では渡りが届かない。
        assert route.passage_y_mm - radius_mm >= tray.web_outer_y_mm, route.name
        assert guide.passage_center_radius_mm == pytest.approx(
            math.hypot(route.passage_x_mm, route.passage_y_mm)
        ), route.name
        for angle_deg in layout.wheel_angles_deg:  # type: ignore[attr-defined]
            radians = math.radians(angle_deg)
            x_mm = route.passage_x_mm * math.cos(radians) - route.passage_y_mm * (
                math.sin(radians)
            )
            y_mm = route.passage_x_mm * math.sin(radians) + route.passage_y_mm * (
                math.cos(radians)
            )
            gap_mm = _tray_footprint_gap_mm(tray, x_mm, y_mm)
            assert gap_mm >= radius_mm + _JOINT_FIT_CLEARANCE_MM, (
                route.name,
                angle_deg,
                gap_mm,
            )


def test_a_battery_that_reaches_under_the_passage_is_rejected(
    shipped: tuple[object, object]
) -> None:
    """⚠️ **空振りでないこと**: トレイが伸びれば通し穴の下は塞がる。

    ⚠️ **穴そのものはトレイと交わらない**（穴は中央部の肉の中、トレイはその下）
    ——塞がるのは**降りる先**である。⚠️ 交差だけを見ていると、この欠陥は通る。
    """
    from dataclasses import replace

    params, layout = shipped
    long_battery = replace(
        params,  # type: ignore[type-var]
        chassis=replace(
            params.chassis,  # type: ignore[attr-defined]
            battery=replace(params.chassis.battery, length_mm=92.0),  # type: ignore[attr-defined]
        ),
    )
    with pytest.raises(GeometryError) as excinfo:
        cable_guide_geometry(long_battery, layout)  # type: ignore[arg-type]
    message = str(excinfo.value)
    assert "battery.length_mm" in message
    assert "配線が降りる先が無い" in message


def _with_arm_width(params: object, factor: float) -> object:
    """アームの幅だけを倍率で動かした寸法パラメータを返す。"""
    from dataclasses import replace

    return replace(
        params,  # type: ignore[type-var]
        chassis=replace(
            params.chassis,  # type: ignore[attr-defined]
            base=replace(
                params.chassis.base,  # type: ignore[attr-defined]
                arm_width_mm=params.chassis.base.arm_width_mm * factor,  # type: ignore[attr-defined]
            ),
        ),
    )


def test_the_three_passages_are_spread_across_the_free_arc(
    shipped: tuple[object, object], guide: CableGuideGeometry
) -> None:
    """⚠️ **通し穴の方位角は自由な弧から導出する**（要件 7.7 / 決定 8）。

    ⚠️ **角度を発明しない。** 3系統は弧の両端まで広げて据えられ、⚠️ **穴どうしの
    間隔は通路の内寸と壁の和以上**である——それを下回れば、中央部で3系統は1つの
    穴へ合流する。
    """
    import math

    arc_start_deg, arc_end_deg = guide.passage_arc_deg
    angles_deg = [route.passage_angle_deg for route in guide.routes]
    assert angles_deg[0] == pytest.approx(arc_start_deg)
    assert angles_deg[-1] == pytest.approx(arc_end_deg)
    # 等間隔である（弧を3系統で分け合う）。
    pitch_deg = (arc_end_deg - arc_start_deg) / (len(angles_deg) - 1)
    for index, angle_deg in enumerate(angles_deg):
        assert angle_deg == pytest.approx(arc_start_deg + index * pitch_deg)
    # 間隔は弦で測る（角度ではなく距離が壁である）。
    assert guide.passage_spacing_mm == pytest.approx(
        2.0
        * guide.passage_center_radius_mm
        * math.sin(math.radians(pitch_deg) / 2.0)
    )
    assert guide.passage_spacing_mm >= guide.channel_width_mm + guide.wall_thickness_mm


def test_a_wider_arm_narrows_the_free_arc_until_the_routes_merge(
    shipped: tuple[object, object]
) -> None:
    """⚠️ **空振りでないこと**: アームの幅は3系統の分離を条件づけている。

    ⚠️ **これは 3.2 が記録したアーム幅の knife-edge と同じつまみの帰結である。**
    耳へつながる腕はアームの幅から動き、腕が太るほど⚠️ **穴を置ける弧は両側から
    削られる**。1.3 倍で穴どうしの壁が残らなくなり、1.9 倍で穴の居場所そのものが
    立ち上がりの内側から消える。⚠️ **どちらも黙って合流させず、寸法パラメータを
    名指しして拒否する。**

    ⚠️ **倍率は「落ちる値」を探して決めたのではなく、叩く関門を名指しして
    決めてある。** 出荷寸法の全域を 0.01 刻みで走査すると、アームの幅だけを
    太らせて到達できる関門は
    ⚠️ **合流（1.27〜1.81 倍）→ 穴の居場所が無い（1.82 倍〜）の2つだけ**で
    ある——自由な弧そのものが消える関門（「配線が降りる先が無い」）へは
    ⚠️ **アームの幅からは届かない**。そちらはバッテリトレイの外周が決めており、
    `test_no_free_arc_for_the_passages_is_rejected_by_naming_the_tray` が
    `battery.length_mm` で叩く。⚠️ **倍率を上げて別の関門を鳴らしたものを
    「同じ反例」と言わない**（Ø60.0 の頃は 1.5 倍が弧の消滅へ届いていた）。
    """
    params, layout = shipped

    with pytest.raises(GeometryError) as merged:
        cable_guide_geometry(_with_arm_width(params, 1.3), layout)  # type: ignore[arg-type]
    message = str(merged.value)
    assert "1つの穴へ合流する" in message
    assert "base.arm_width_mm" in message
    assert "cable.channel_width_mm" in message
    assert "cable.wall_thickness_mm" in message
    # ⚠️ **叩いた関門を名指しする**（別の関門が先に鳴っていれば反例ではない）。
    assert "立ち上がりの内側に穴の居場所が無い" not in message
    assert "配線が降りる先が無い" not in message

    with pytest.raises(GeometryError) as no_room:
        cable_guide_geometry(_with_arm_width(params, 1.9), layout)  # type: ignore[arg-type]
    message = str(no_room.value)
    assert "立ち上がりの内側に穴の居場所が無い" in message
    assert "base.arm_width_mm" in message
    assert "1つの穴へ合流する" not in message

    # ⚠️ 出荷の幅では通る（この検査が幅そのものを疑っていない証拠である）。
    assert cable_guide_geometry(_with_arm_width(params, 1.0), layout).routes  # type: ignore[arg-type]


def test_no_free_arc_for_the_passages_is_rejected_by_naming_the_tray(
    shipped: tuple[object, object]
) -> None:
    """⚠️ **穴を置ける弧が1つも残らない入力は、塞いでいる寸法を名指して拒否される。**

    ⚠️ **弧を消しているのはバッテリトレイの外周である。** 通し穴の下が3つの
    取付角のどれかでトレイの影に入れば、⚠️ **そこには配線が降りる先が無い**
    ——アームの幅を太らせても、その手前で「穴の居場所が無い」関門が先に鳴る
    ため、この枝はトレイ側のつまみでしか踏めない
    （`test_a_wider_arm_narrows_the_free_arc_until_the_routes_merge` の申し送り）。

    ⚠️ **叩いた関門を名指しする。** 「配線が降りる先が無い」は2箇所にある
    ——弧が1つも無い枝と、走査が跨いだ塞がりを後から見つける枝である。ここが
    見たいのは前者であり、⚠️ **後者へ流れ着いたものを同じ反例と言わない**。
    """
    params, _layout = shipped
    layout = derive_layout(params)  # type: ignore[arg-type]

    with pytest.raises(GeometryError) as gone:
        cable_guide_geometry(_with_battery(shipped, length_mm=90.0), layout)  # type: ignore[arg-type]
    message = str(gone.value)
    assert "配線が降りる先が無い" in message
    # ⚠️ 弧が1つも無い枝である（走査が跨いだ塞がりの枝ではない）。
    assert "どの角でもいずれかの取付角で" in message
    assert "battery.length_mm" in message
    assert "battery.fuse_holder_width_mm" in message
    assert "base.arm_width_mm" in message
    assert "1つの穴へ合流する" not in message

    # ⚠️ 出荷のバッテリでは通る（この検査がトレイそのものを疑っていない証拠）。
    assert cable_guide_geometry(params, layout).routes  # type: ignore[arg-type]


def _free_air_leg_distances_mm(
    guide: CableGuideGeometry,
) -> dict[tuple[str, str], float]:
    """自由空間を渡る脚どうしの最小距離を、⚠️ **実装とは別に組み立てて測る**。

    ⚠️ **中心どうしでも端点どうしでもない。** 平面上の線分では、交差していない
    限り最小は必ずどちらかの端点で取る——ただし相手の側は⚠️ **線分の内部**であり、
    出荷の寸法でも `motor` と `supply` の最小は `supply` の脚の途中に落ちる。
    """
    import math

    def point_segment_mm(
        point: tuple[float, float],
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> float:
        span = (end[0] - start[0], end[1] - start[1])
        length_squared = span[0] ** 2 + span[1] ** 2
        ratio = (
            (point[0] - start[0]) * span[0] + (point[1] - start[1]) * span[1]
        ) / length_squared
        ratio = min(max(ratio, 0.0), 1.0)
        return math.hypot(
            point[0] - (start[0] + ratio * span[0]),
            point[1] - (start[1] + ratio * span[1]),
        )

    legs = {
        route.name: (
            (guide.crossing_inner_radius_mm, route.center_y_mm),
            (route.passage_x_mm, route.passage_y_mm),
        )
        for route in guide.routes
    }
    distances: dict[tuple[str, str], float] = {}
    names = [route.name for route in guide.routes]
    for first in range(len(names)):
        for second in range(first + 1, len(names)):
            left = legs[names[first]]
            right = legs[names[second]]
            distances[names[first], names[second]] = min(
                point_segment_mm(left[0], *right),
                point_segment_mm(left[1], *right),
                point_segment_mm(right[0], *left),
                point_segment_mm(right[1], *left),
            )
    return distances


def test_a_thin_wall_brings_the_free_air_legs_together(
    shipped: tuple[object, object], guide: CableGuideGeometry
) -> None:
    """⚠️ **壁が終わったあとも3系統は離れている**（要件 7.7 / 決定 8）。

    ⚠️ **穴の間隔だけでは足りない。** 渡りの口から中央部の通し穴までは壁の無い
    自由空間であり、⚠️ **口の並びを決めているのは `cable.wall_thickness_mm`**
    ——弧も穴の間隔も壁の厚さでは動かないため、⚠️ **壁を薄くすると口だけが内側へ
    寄り、脚が近づく**。壁 0.5mm では穴の間隔 14.87mm はそのままに `motor` と
    `supply` の脚が 4.68mm まで詰まる（要る間隔は通路の内寸 7.0mm）——
    ⚠️ **束が同じ空間を共有すれば、取り違えは配線を挿す直前に起きる。**
    """
    params, layout = shipped

    thinned = _replace_cable(params, wall_thickness_mm=0.5)
    with pytest.raises(GeometryError) as excinfo:
        cable_guide_geometry(thinned, layout)  # type: ignore[arg-type]
    message = str(excinfo.value)
    assert "cable.wall_thickness_mm" in message
    assert "cable.channel_width_mm" in message
    assert "motor" in message
    assert "supply" in message
    # ⚠️ 穴の間隔の関門では止まっていない（間隔は壁の厚さで動かない）。
    assert "1つの穴へ合流する" not in message

    # ⚠️ **出荷の寸法では通る**（この検査が壁の厚さそのものを疑っていない証拠）。
    # ⚠️ この 7.5121mm は**実装が持っていない量**である（`cable_guide_geometry`
    # は脚の距離を記録に残さず、関門の中で捨てる）。`guide` から導き直せば
    # `_free_air_leg_distances_mm` を2度書くだけの循環になるため、⚠️ **独立に
    # 組み立てた値の直書きのまま**にしてある——寸法が動いたらここも読み直す。
    distances_mm = _free_air_leg_distances_mm(guide)
    assert min(distances_mm.values()) == pytest.approx(7.5121, abs=1e-4)
    assert distances_mm["motor", "supply"] == pytest.approx(7.5121, abs=1e-4)
    assert min(distances_mm.values()) >= guide.channel_width_mm
    # ⚠️ **余裕は薄い**（要件 1.9 の実測と同時に見直す申し送り）。⚠️ その薄さを
    # 「0.5mm 未満」のような絶対値で書かない——実測が入るたびに値が動き
    # （0.3798mm → 0.5121mm）、⚠️ **薄さの主張ではなく数字の更新作業になる**。
    # ⚠️ **壁をわずか 0.5mm 削るだけで関門が鳴る**ことで薄さを示す。
    with pytest.raises(GeometryError) as barely:
        cable_guide_geometry(  # type: ignore[arg-type]
            _replace_cable(params, wall_thickness_mm=guide.wall_thickness_mm - 0.5),
            layout,
        )
    assert "自由空間を渡る脚どうしが" in str(barely.value)
    assert "cable.wall_thickness_mm" in str(barely.value)


def test_the_window_lets_the_wiring_out_above_the_board_plane(
    shipped: tuple[object, object], guide: CableGuideGeometry
) -> None:
    """⚠️ **窓は基板面より上にある**（要件 7.4, 7.6）。

    低く開ければ配線は段の下へ回り込み、⚠️ 取付面と放熱の隙間を奪う。上は段
    どうしの重ね代（受け止めデッキの筒）より下でなければならない。
    """
    params, layout = shipped
    deck = deck_stack_geometry(params, layout)  # type: ignore[arg-type]
    assert guide.window_bottom_height_mm > deck.board_plane_height_mm
    assert guide.window_top_height_mm < deck.catch_tube_bottom_height_mm
    assert guide.window_top_height_mm - guide.window_bottom_height_mm == (
        pytest.approx(guide.channel_width_mm)
    )
    # 窓は通し穴と同じ方位角にある（配線は筒の中をまっすぐ昇る）。
    # ⚠️ **系統ごとに別の角**であり、内側の系統ほど小さい角である。
    angles_deg = [route.passage_angle_deg for route in guide.routes]
    assert angles_deg == sorted(angles_deg)
    assert angles_deg[0] > 0.0
    assert guide.window_width_mm == pytest.approx(guide.passage_diameter_mm)


def test_the_cable_guide_geometry_is_deterministic(
    shipped: tuple[object, object]
) -> None:
    """同一の寸法パラメータからの再導出は同一の値を返す（要件 1.12）。"""
    params, layout = shipped
    assert cable_guide_geometry(params, layout) == cable_guide_geometry(  # type: ignore[arg-type]
        params, layout
    )


def test_the_guide_count_comes_from_the_joint_module(
    shipped: tuple[object, object], guide: CableGuideGeometry
) -> None:
    """⚠️ 点数を形の側で数え直さない（要件 2.1）。"""
    from chassis_mechanism.joints import segment_counts

    params, _ = shipped
    assert guide.guide_count == segment_counts(params)["cable_guide"]  # type: ignore[arg-type]
    assert guide.guide_count == params.chassis.base.wheel_count  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# 11. 検査を通らない形状の生成物を出さない関門（タスク 3.6 / 要件 1.12, 2.3,
#     2.4, 4.4, 9.1）
#
# ⚠️ **関門は生成の「前」に立つ。** 作ってから捨てる実装は、書き出し（タスク
# 3.7）が検査を飛ばした瞬間に無検査の生成物を出す。したがって本節の検査は
# ⚠️ **形状ライブラリを要さない**——関門が落ちる設定では、CAD 非導入の環境でも
# 同じ例外が同じ内容で出る。
# ---------------------------------------------------------------------------


def _too_low_for_the_floor(params: object) -> object:
    """床との隙間が3部位で足りなくなる寸法パラメータを作る。

    ⚠️ **成立する幾何のまま隙間だけを落とす。** 下限を上げるだけではバッテリ
    トレイの下面がその下限を割り、`battery_tray_geometry` の側で先に落ちる
    ——見たいのはその手前ではなく隙間の関門である。バッテリを薄くしてトレイの
    下面を上げ、締結の突出量を（鉛直スタックが許す範囲で）伸ばす。
    """
    import dataclasses

    chassis = params.chassis  # type: ignore[attr-defined]
    return dataclasses.replace(
        params,  # type: ignore[arg-type]
        chassis=dataclasses.replace(
            chassis,
            clearance=dataclasses.replace(
                chassis.clearance,
                min_ground_clearance_mm=32.0,
                fastener_protrusion_mm=29.0,
            ),
            battery=dataclasses.replace(
                chassis.battery, height_mm=10.0, hold_height_mm=50.0
            ),
        ),
    )


def _reported_clearance_shortfalls(
    message: str,
) -> dict[str, tuple[float, float, float]]:
    """関門のメッセージから「部位 → (隙間, 下限, 不足量)」を読み取る。

    ⚠️ **不足量を数字列の含有で見ない。** `"20.5mm 下回る"` のような直書きは
    ホイール径やバッテリの実測で機体が上下した瞬間に嘘になり（Ø60.0 → Ø57.9
    で実際に落ちた）、⚠️ 部分一致は `"1.0mm 下回る"` が `"21.0mm 下回る"` に
    当たるなど**別の部位の値**でも通ってしまう。値で照合し、⚠️ **不足量が下限
    と隙間の差であること**まで見る。
    """
    import re

    return {
        name: (float(gap_mm), float(minimum_mm), float(shortfall_mm))
        for name, gap_mm, minimum_mm, shortfall_mm in re.findall(
            r"(\w+) の隙間 (\S+?)mm が下限 (\S+?)mm を (\S+?)mm 下回る", message
        )
    }


def _reported_envelope_excesses(
    message: str,
) -> dict[tuple[str, str], tuple[float, float, float]]:
    """関門のメッセージから「(断片, 軸) → (外接箱, 上限, 超過量)」を読み取る。

    ⚠️ `_reported_clearance_shortfalls` と同じ理由で、超過量は直書きせず
    **値**で照合する（外接箱はバッテリの実測でそのまま動く）。
    """
    import re

    return {
        (part_name, axis): (float(envelope_mm), float(limit_mm), float(excess_mm))
        for part_name, axis, envelope_mm, limit_mm, excess_mm in re.findall(
            r"(\w+) の 軸 (\w+) が (\S+?)mm で上限 (\S+?)mm を (\S+?)mm 超過",
            message,
        )
    }


def test_the_shipped_parameters_pass_the_gate(shipped: tuple[object, object]) -> None:
    """⚠️ **出荷の寸法は関門を通る**（反例の対。関門そのものが空振りでない証拠）。"""
    params, layout = shipped
    assert check_before_build(params, layout) is None  # type: ignore[arg-type]


def test_the_gate_lists_every_floor_clearance_shortfall(
    shipped: tuple[object, object]
) -> None:
    """隙間の不足を**部位と不足量つきで全件**示して生成を止める（要件 4.4）。

    ⚠️ **1件で打ち切らない。** 締結の突出量を伸ばして3部位（モータ胴体・
    ブラケット・締結の下端）を同時に下限へ落とし、⚠️ **3件すべてが1回の失敗に
    現れる**ことを固定する。下限を上回る2部位（ベース板下面・配線）は現れない。
    """
    params, _ = shipped
    tripped = _too_low_for_the_floor(params)
    # ⚠️ **幾何も引き直す。** 隙間の高さは鉛直スタック（`layout`）が持っており、
    # 古い幾何のまま渡せば、変えたはずの締結の突出量が効かない。
    layout = derive_layout(tripped)  # type: ignore[arg-type]
    with pytest.raises(ClearanceError) as excinfo:
        check_before_build(tripped, layout)  # type: ignore[arg-type]
    message = str(excinfo.value)
    for name in ("motor_body", "bracket", "fastener"):
        assert name in message, name
    # ⚠️ 下限を上回る2部位は現れない（見ていない部位と余裕のある部位を混同しない）。
    assert "base_underside" not in message
    assert "cable" not in message
    # ⚠️ 不足量が部位ごとに出る。⚠️ **値は下限と鉛直スタックから導く**
    # （直書きは車軸高さが実測で動いた瞬間に嘘になる）。
    reported = _reported_clearance_shortfalls(message)
    assert set(reported) == {"motor_body", "bracket", "fastener"}, message
    minimum_mm = tripped.chassis.clearance.min_ground_clearance_mm  # type: ignore[attr-defined]
    for name, (gap_mm, limit_mm, shortfall_mm) in reported.items():
        assert limit_mm == pytest.approx(minimum_mm), name
        assert shortfall_mm == pytest.approx(limit_mm - gap_mm), name
        assert shortfall_mm > 0.0, name
    # ⚠️ **報告された隙間は鉛直スタックそのもの**である（別の量を不足量欄へ
    # 詰めても通る検査にしない）。
    assert reported["motor_body"][0] == pytest.approx(
        layout.vertical.motor_body_bottom_height_mm
    ), message
    assert reported["fastener"][0] == pytest.approx(
        layout.vertical.fastener_bottom_height_mm
    ), message


def test_the_gate_stops_the_build_before_a_single_solid_is_made(
    shipped: tuple[object, object]
) -> None:
    """⚠️ **関門は形状ライブラリより手前にある**（要件 2.3, 4.4）。

    `build_parts` は隙間の不足を、⚠️ **ソリッドを1つも作らずに**拒否する
    ——CAD 非導入の環境でも同じ失敗になることが、関門が生成の前に立っている
    ことの証拠である（`CadUnavailableError` にならない）。
    """
    params, layout = shipped
    tripped = _too_low_for_the_floor(params)
    layout = derive_layout(tripped)  # type: ignore[arg-type]
    with pytest.raises(ClearanceError):
        build_parts(tripped, layout)  # type: ignore[arg-type]


def test_the_gate_lists_every_build_volume_excess_of_every_part(
    shipped: tuple[object, object]
) -> None:
    """造形可能寸法の超過を**部品・軸・超過量つきで全件**示す（要件 2.2, 2.3）。

    ⚠️ **部品ごとに1件ずつ直す往復にしない。** 造形面の低い造形機を仮定すると、
    ⚠️ **分割で逃げられない部品**（バッテリトレイ・整備スタンドの脚）と、
    分割しても高さが縮まない段が同時に超過する——⚠️ **その全件が1回の失敗に
    現れる**ことを固定する。

    ⚠️ **一律に小さくしない。** 造形面を全軸で縮めると、上流の円環の分割数導出
    （`required_segment_count`）が「どの分割数でも収まらない」として先に落ちる
    ——見たいのはその手前ではなく本 Spec の関門である。
    """
    import dataclasses

    params, layout = shipped
    # ⚠️ **造形面の上限はトレイの外接箱から導く。** 「45.0」のような直書きは、
    # ⚠️ **バッテリの実測でトレイが縮んだ瞬間に z が上限へ届かなくなり**、
    # 「同じ部品の複数の軸が全件出る」という本件の主眼を測らないまま緑になる
    # （高さ 26.0 → 23.2 で実際にそうなった）。x も z も⚠️ **トレイが必ず超える
    # 側**へ、同じだけ内側に置く。
    trip_margin_mm = 1.0
    tray_envelope = battery_tray_geometry(params, layout).envelope  # type: ignore[arg-type]
    small = dataclasses.replace(
        params,  # type: ignore[arg-type]
        printing=dataclasses.replace(
            params.printing,  # type: ignore[attr-defined]
            build_x_mm=tray_envelope.x_mm - trip_margin_mm,
            build_z_mm=tray_envelope.z_mm - trip_margin_mm,
        ),
    )
    with pytest.raises(GeometryError) as excinfo:
        check_before_build(small, layout)  # type: ignore[arg-type]
    message = str(excinfo.value)
    # ⚠️ **断片ごとに1件**である（同じ部品の種類でも番号ごとに出る）。
    for name in (
        "battery_tray",
        "board_deck_1",
        "board_deck_2",
        "board_deck_3",
        "service_stand_1",
        "service_stand_2",
        "service_stand_3",
    ):
        assert name in message, name
    # ⚠️ **同じ部品の複数の軸も全件**である（バッテリトレイは x と z の両方）。
    assert "軸 x" in message
    assert "軸 z" in message
    # ⚠️ 超過量は**造形面の上限と外接箱から導く**（直書きは、バッテリの実測で
    # トレイの外接箱が動いた瞬間に嘘になる）。
    reported = _reported_envelope_excesses(message)
    assert ("battery_tray", "x") in reported, message
    assert ("battery_tray", "z") in reported, message
    assert ("board_deck_1", "z") in reported, message
    for key, (envelope_mm, limit_mm, excess_mm) in reported.items():
        assert excess_mm == pytest.approx(envelope_mm - limit_mm), key
        assert excess_mm > 0.0, key
    assert reported["battery_tray", "x"][1] == pytest.approx(small.printing.build_x_mm)
    assert reported["battery_tray", "z"][1] == pytest.approx(small.printing.build_z_mm)
    # ⚠️ **報告された外接箱はトレイの実際の外接箱である**（超過量の欄へ別の量を
    # 詰めても通る検査にしない）。
    tray_envelope = battery_tray_geometry(small, layout).envelope  # type: ignore[arg-type]
    assert reported["battery_tray", "x"][0] == pytest.approx(tray_envelope.x_mm)
    assert reported["battery_tray", "z"][0] == pytest.approx(tray_envelope.z_mm)
    # ⚠️ 見直す先が部品ごとに示される（家族ごとの例外を1つへまとめた代償を払わない）。
    assert "バッテリの寸法か配置半径を見直すこと" in message


def test_the_gate_refuses_a_material_outside_the_upstream_list(
    shipped: tuple[object, object]
) -> None:
    """材料が上流の許可一覧に無ければ生成しない（要件 2.4）。"""
    import dataclasses

    from catch_mechanism import ParameterError as UpstreamParameterError

    params, layout = shipped
    # ⚠️ **上流の構築時検証を迂回した個体を作る**（`PrintingConstraints` は
    # 許可外の材料では構築できない。`test_chassis_upstream_contract.py` と
    # 同じ手口である）——⚠️ **関門が実際に `check_material` を通していること**を
    # 見たいのであって、上流の型の検証を見たいのではない。
    printing = dataclasses.replace(params.printing)  # type: ignore[attr-defined]
    object.__setattr__(printing, "material", "ABS")
    bypassed = dataclasses.replace(params, printing=printing)  # type: ignore[arg-type]
    with pytest.raises(UpstreamParameterError) as excinfo:
        check_before_build(bypassed, layout)  # type: ignore[arg-type]
    assert "ABS" in str(excinfo.value)


def test_the_gate_refuses_a_positioning_element_no_part_realises(
    shipped: tuple[object, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ **数え上げた位置決め要素をどの部品も実現していない一覧を拒否する。**

    タスク 3.2 が残した申し送りそのものである——`joints` がダボを2本記録して
    いるのに、⚠️ **どの部品にもダボ穴が無い**状態は、緑のまま通り抜けていた。
    穴の一覧（`bore_diameters_mm`）に位置決め要素の径が現れない以上、
    数えた要素は形の上のどこにも無い。
    """
    import dataclasses

    from chassis_mechanism import joints as joints_module

    params, layout = shipped
    derived = joints_module.derive_joints(layout, params)  # type: ignore[arg-type]
    with_dowels = tuple(
        dataclasses.replace(joint, dowel_count=2)
        if joint.name.startswith("hub_plate__motor_arm_")
        else joint
        for joint in derived
    )
    monkeypatch.setattr(
        "chassis_mechanism.shapes.derive_joints",
        lambda *_args, **_kwargs: with_dowels,
    )
    with pytest.raises(GeometryError) as excinfo:
        check_before_build(params, layout)  # type: ignore[arg-type]
    message = str(excinfo.value)
    assert "hub_plate__motor_arm_1" in message
    assert "dowel" in message


def test_the_gate_carries_every_kind_of_violation_it_has_already_computed(
    shipped: tuple[object, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ **見えている違反を捨てない**——例外の型は1つでも、内容は捨てない。

    関門は外接箱・床との隙間・要素の実現の3種を**先に全部**計算する。⚠️ 送出
    できる例外の型は1つだけだが、⚠️ **既に手元にある他の種類の違反まで捨てれば、
    1種類ずつ直しては再実行する往復**になる（関門が全件を1回で示す約束と矛盾する）。

    ⚠️ **隙間の不足と要素の未実現を同時に起こす。** 送出されるのは
    `ClearanceError` だが、⚠️ **ダボの未実現も同じメッセージに載る**。
    """
    import dataclasses

    from chassis_mechanism import joints as joints_module

    params, _ = shipped
    tripped = _too_low_for_the_floor(params)
    layout = derive_layout(tripped)  # type: ignore[arg-type]
    derived = joints_module.derive_joints(layout, tripped)  # type: ignore[arg-type]
    with_dowels = tuple(
        dataclasses.replace(joint, dowel_count=2)
        if joint.name.startswith("hub_plate__motor_arm_")
        else joint
        for joint in derived
    )
    monkeypatch.setattr(
        "chassis_mechanism.shapes.derive_joints",
        lambda *_args, **_kwargs: with_dowels,
    )
    with pytest.raises(ClearanceError) as excinfo:
        check_before_build(tripped, layout)  # type: ignore[arg-type]
    message = str(excinfo.value)
    # ⚠️ 送出された型そのものの内容（隙間の不足）は全件出る。
    # ⚠️ **値は下限と鉛直スタックから導く**（直書きは実測で機体が上下すると嘘に）。
    reported = _reported_clearance_shortfalls(message)
    assert set(reported) == {"motor_body", "bracket", "fastener"}, message
    minimum_mm = tripped.chassis.clearance.min_ground_clearance_mm  # type: ignore[attr-defined]
    for name, (gap_mm, limit_mm, shortfall_mm) in reported.items():
        assert limit_mm == pytest.approx(minimum_mm), name
        assert shortfall_mm == pytest.approx(limit_mm - gap_mm), name
        assert shortfall_mm > 0.0, name
    assert reported["motor_body"][0] == pytest.approx(
        layout.vertical.motor_body_bottom_height_mm
    ), message
    # ⚠️ **捨てられていた側**——同じ失敗に併せて載る。
    assert "hub_plate__motor_arm_1" in message
    assert "dowel" in message


def test_the_envelope_failure_carries_both_of_the_other_two_kinds(
    shipped: tuple[object, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ **3種が同時に起きたとき、外接箱の失敗が残り2種を**両方**載せる。**

    上の検査（`test_the_gate_carries_every_kind_of_violation_it_has_already_computed`）
    が通るのは隙間＋実現の2種であり、送出されるのは `ClearanceError` である。
    ⚠️ **外接箱の枝は別の連結を組み立てている**（`clearance_also + realisation_also`）
    ——そこを一度も踏まなければ、片方を落とす書き間違いが緑のまま残る
    （タスク 3.6 のレビュー指摘3）。

    ⚠️ **3種を同時に起こす**: 床との隙間を落とし、造形面を小さくし、実現されない
    ダボを数えさせる。送出されるのは `GeometryError`（外接箱）であり、⚠️ **隙間も
    ダボも同じメッセージに載る**。
    """
    import dataclasses

    from chassis_mechanism import joints as joints_module

    params, _ = shipped
    tripped = _too_low_for_the_floor(params)
    # ⚠️ 造形面を小さくして外接箱の枝を踏ませる（一律には縮めない。上流の分割数
    # 導出が先に落ちる。`test_the_gate_lists_every_build_volume_excess_of_every_part`）。
    tripped = dataclasses.replace(
        tripped,
        printing=dataclasses.replace(
            tripped.printing, build_x_mm=160.0, build_z_mm=45.0
        ),
    )
    layout = derive_layout(tripped)  # type: ignore[arg-type]
    derived = joints_module.derive_joints(layout, tripped)  # type: ignore[arg-type]
    with_dowels = tuple(
        dataclasses.replace(joint, dowel_count=2)
        if joint.name.startswith("hub_plate__motor_arm_")
        else joint
        for joint in derived
    )
    monkeypatch.setattr(
        "chassis_mechanism.shapes.derive_joints",
        lambda *_args, **_kwargs: with_dowels,
    )
    with pytest.raises(GeometryError) as excinfo:
        check_before_build(tripped, layout)  # type: ignore[arg-type]
    message = str(excinfo.value)
    # 1. 送出された型そのものの内容（外接箱の超過）。
    #    ⚠️ 超過量・不足量とも**上限と幾何から導く**（直書きは実測で動くと嘘に）。
    assert "battery_tray" in message
    excesses = _reported_envelope_excesses(message)
    assert ("battery_tray", "x") in excesses, message
    for key, (envelope_mm, limit_mm, excess_mm) in excesses.items():
        assert excess_mm == pytest.approx(envelope_mm - limit_mm), key
        assert excess_mm > 0.0, key
    assert excesses["battery_tray", "x"][1] == pytest.approx(tripped.printing.build_x_mm)
    assert excesses["battery_tray", "x"][0] == pytest.approx(
        battery_tray_geometry(tripped, layout).envelope.x_mm  # type: ignore[arg-type]
    )
    # 2. ⚠️ 併せて載る床との隙間（`clearance_also`）。
    shortfalls = _reported_clearance_shortfalls(message)
    assert set(shortfalls) == {"motor_body", "bracket", "fastener"}, message
    minimum_mm = tripped.chassis.clearance.min_ground_clearance_mm  # type: ignore[attr-defined]
    for name, (gap_mm, limit_mm, shortfall_mm) in shortfalls.items():
        assert limit_mm == pytest.approx(minimum_mm), name
        assert shortfall_mm == pytest.approx(limit_mm - gap_mm), name
        assert shortfall_mm > 0.0, name
    assert shortfalls["motor_body"][0] == pytest.approx(
        layout.vertical.motor_body_bottom_height_mm
    ), message
    # 3. ⚠️ 併せて載る要素の未実現（`realisation_also`）。
    #    ⚠️ **2 と 3 の両方**が同時に出ることが本件の主眼である。
    assert "hub_plate__motor_arm_1" in message
    assert "dowel" in message


GATE_NAMES: frozenset[str] = frozenset(
    {"check_before_build", "check_before_building_stand"}
)


def _builders_and_what_they_call_first(source: str) -> dict[str, set[str]]:
    """`build_*` 関数ごとに、⚠️ **本体の先頭**で呼んでいる名前の集合を返す。

    docstring を1文目として飛ばし、その次の文で呼ばれている名前だけを見る
    ——⚠️ **「どこかで呼んでいる」では足りない**。関門は形を作る前に立つ。
    """
    tree = ast.parse(source)
    found: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if not node.name.startswith("build_"):
            continue
        first = node.body[1] if len(node.body) > 1 else node.body[0]
        found[node.name] = {
            child.func.id
            for child in ast.walk(first)
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
        }
    return found


def test_every_public_builder_passes_through_the_gate(shipped: tuple[object, object]) -> None:
    """⚠️ **関門を迂回できる入口が無い**（要件 2.3, 4.4 / タスク 3.7 の前提）。

    公開されている構築関数の**すべて**が、本体の先頭で関門を呼ぶ。⚠️ 呼ばない
    入口が1つでもあれば、そこから無検査の生成物が出る。整備スタンドだけは
    `StandInputs` しか受け取らないため（要件 5.2）、材料と外接箱に閉じた
    スタンド用の関門を通る。

    ⚠️ **走査が何件見つけたかを固定する**（タスク 3.6 のレビュー指摘 / タスク 3.7
    がこの固定に寄りかかるため）。「見つけた全件が関門を通る」だけでは、命名規約が
    変わったりソースの取得先がずれたりして⚠️ **1件も見つからなかった場合に緑の
    まま通る**。下限は `shapes.__all__` が公開している `build_*` の集合であり、
    ⚠️ **手で数えた定数ではない**——構築の入口が増えれば下限も自動で上がる。
    """
    inspected = _builders_and_what_they_call_first(
        SHAPES_SOURCE.read_text(encoding="utf-8")
    )
    offenders = sorted(
        name for name, called in inspected.items() if not called & GATE_NAMES
    )
    assert offenders == [], f"関門を通らない構築の入口がある: {offenders}"

    exported = {name for name in shapes_module.__all__ if name.startswith("build_")}
    assert exported, "⚠️ `shapes.__all__` に構築の入口が1つも無い（走査の前提が崩れた）"
    missing = sorted(exported - set(inspected))
    assert missing == [], f"公開されているのに走査が見つけていない入口: {missing}"
    assert len(inspected) >= len(exported) >= 8


def test_the_builder_scan_would_notice_an_ungated_builder_or_an_empty_scan() -> None:
    """⚠️ 上の走査が**空振りではない**ことの反例（タスク 3.6 のレビュー指摘）。

    3つを示す。(1) 関門を呼ばない構築関数は検出される、(2) ⚠️ **同じ形の関数が
    関門を呼んでいれば検出されない**（反例が下限そのものを疑っていない証拠）、
    (3) 構築関数が1つも無いソースでは走査が空になる——⚠️ **その空を捉えるのが
    件数の下限である。**
    """
    ungated = 'def build_thing(params):\n    """doc"""\n    return 1\n'
    assert _builders_and_what_they_call_first(ungated) == {"build_thing": set()}

    gated = (
        'def build_thing(params):\n'
        '    """doc"""\n'
        "    check_before_build(params, layout)\n"
        "    return 1\n"
    )
    assert _builders_and_what_they_call_first(gated)["build_thing"] & GATE_NAMES

    assert _builders_and_what_they_call_first("def helper():\n    return 1\n") == {}


@requires_cad
def test_two_builds_from_the_same_parameters_return_the_same_metrics(
    shipped: tuple[object, object]
) -> None:
    """同一入力からの2回の生成が同一の指標を返す（要件 1.12）。

    ⚠️ **指標は実形状から抽出した値である**（`measure_part`）。寸法からの
    再計算ではないため、ブール演算の結果が版や実行ごとに揺れれば一致しない。
    """
    params, layout = shipped
    first = build_parts(params, layout)  # type: ignore[arg-type]
    second = build_parts(params, layout)  # type: ignore[arg-type]
    assert [part.name for part in first] == list(part_names(params))  # type: ignore[arg-type]
    assert [part.metrics for part in first] == [part.metrics for part in second]


@requires_cad
def test_every_part_carries_a_mass_estimate_from_the_upstream_density(
    shipped: tuple[object, object]
) -> None:
    """体積と上流の材料密度から各部品の質量の目安を出す（要件 7.9 / タスク 3.6）。

    ⚠️ **密度は上流の公開契約から来る**（`PrintingConstraints.
    material_density_g_cm3`）——本 Spec は同じ値を持たない（要件 1.3）。
    """
    from catch_mechanism import estimate_mass_g

    params, layout = shipped
    parts = build_parts(params, layout)  # type: ignore[arg-type]
    masses = part_masses(parts, params.printing)  # type: ignore[attr-defined]
    assert [mass.part_name for mass in masses] == [part.name for part in parts]
    for part, mass in zip(parts, masses, strict=True):
        assert mass.volume_mm3 == part.metrics.volume_mm3
        assert mass.mass_g == estimate_mass_g(
            part.metrics.volume_mm3, params.printing.material_density_g_cm3  # type: ignore[attr-defined]
        )
        assert mass.mass_g > 0.0


_METRICS_PROBE = """
import json

from chassis_mechanism.config import load_params
from chassis_mechanism.layout import derive_layout
from chassis_mechanism.shapes import build_parts

params = load_params()
report = [
    {
        "name": part.metrics.part_name,
        "volume_mm3": part.metrics.volume_mm3.hex(),
        "bbox_mm": [extent_mm.hex() for extent_mm in part.metrics.bbox_mm],
        "solid_count": part.metrics.solid_count,
    }
    for part in build_parts(params, derive_layout(params))
]
print(json.dumps(report))
"""
"""別のプロセスで形状指標を抽出して JSON で返す小片（要件 1.12）。

⚠️ **浮動小数を `hex()` で運ぶ。** 十進の丸めで一致させると、⚠️ **最後の 1bit が
違う指標を「同じ」と読んでしまう**——指標の一致は記録の照合（タスク 4.2）が
許容差で見る話であって、ここで見たいのは⚠️ **同じ入力から同じ数が出ること**
そのものである。
"""


@requires_cad
def test_a_fresh_process_extracts_the_same_metrics_bit_for_bit(
    shipped: tuple[object, object]
) -> None:
    """⚠️ **別プロセスの生成も同一の指標を返す**（要件 1.12）。

    ⚠️ **同一プロセスでの2回では足りない。** 辞書や集合の反復順が
    `PYTHONHASHSEED` で変われば、ブール演算の順序が変わって最下位ビットが動き
    うる——⚠️ **その揺れは同じプロセスの中では決して現れない。** 新しい
    インタプリタを別の hash seed で起こし、⚠️ **ビット単位で**突き合わせる。
    """
    import json

    params, layout = shipped
    env = os.environ.copy()
    env["PYTHONHASHSEED"] = "12345"
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", _METRICS_PROBE],
        cwd=str(REPO_ROOT),
        capture_output=True,
        encoding="utf-8",
        env=env,
        timeout=600.0,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    reported = json.loads(result.stdout)

    here = build_parts(params, layout)  # type: ignore[arg-type]
    assert [entry["name"] for entry in reported] == [part.name for part in here]
    for entry, part in zip(reported, here, strict=True):
        assert float.fromhex(entry["volume_mm3"]) == part.metrics.volume_mm3
        assert tuple(
            float.fromhex(extent) for extent in entry["bbox_mm"]
        ) == part.metrics.bbox_mm
        assert entry["solid_count"] == part.metrics.solid_count
