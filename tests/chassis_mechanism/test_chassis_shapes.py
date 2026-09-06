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

from chassis_mechanism.config import load_params
from chassis_mechanism.errors import CadUnavailableError, GeometryError
from chassis_mechanism.layout import derive_layout
from chassis_mechanism.shapes import (
    MIN_HAND_ACCESS_MM,
    PART_NAMES,
    AdapterGeometry,
    BuiltPart,
    DriveBaseGeometry,
    StandGeometry,
    StandInputs,
    adapter_geometry,
    build_parts,
    build_service_stand_legs,
    drive_base_geometry,
    measure_part,
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
    build_parts,
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
    "adapter_contact_radius_mm": adapter.contact_radius_mm,
    "adapter_retention_bolt_count": adapter.retention_bolt_count,
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
    # ⚠️ アダプタの座も算術だけで決まる（上流の採寸値・テーパー角・分割数導出）。
    assert report["adapter_segment_count"] == adapter.segment_count
    assert report["adapter_outer_radius_mm"] == adapter.outer_radius_mm
    assert report["adapter_seat_bottom_radius_mm"] == adapter.seat_bottom_radius_mm
    assert report["adapter_seat_top_radius_mm"] == adapter.seat_top_radius_mm
    assert report["adapter_contact_radius_mm"] == adapter.contact_radius_mm
    assert report["adapter_retention_bolt_count"] == adapter.retention_bolt_count
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
    assert PART_NAMES == ("hub_plate", "motor_arm", "adapter_segment", "service_stand")
    assert part_names(params) == (  # type: ignore[arg-type]
        "hub_plate",
        "motor_arm_1",
        "motor_arm_2",
        "motor_arm_3",
        "adapter_segment_1",
        "adapter_segment_2",
        "adapter_segment_3",
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
# 7. ゴミ箱固定アダプタ（タスク 3.3 / 要件 2.2, 6.1, 6.2, 6.5, 6.7）
#
# ⚠️ **本節は形状ライブラリを要さない側である。** 座の径・テーパー・分割数・
# 締結箇所は算術だけで決まり、実形状に対する不変条件（座が円筒断面を持たない、
# 開口を狭めない、当たり面が実現している）は `test_chassis_invariants.py` が持つ。
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
    from chassis_mechanism.joints import _adapter_outer_diameter_mm

    params, _ = shipped
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
    # 接触するのは底の**平面部**だけである（角の丸みは逃がす）。
    assert adapter.contact_radius_mm == pytest.approx(can.bottom_flat_diameter_mm / 2.0)
    assert adapter.contact_radius_mm < adapter.seat_bottom_radius_mm
    # 底の肉厚は「ゴミ箱が提供する通過径」を決める（要件 6.7 の判定に効く）。
    assert adapter.can_clear_radius_mm == pytest.approx(
        can.bottom_outer_diameter_mm / 2.0 - can.bottom_thickness_mm
    )
    assert adapter.taper_deg == can.taper_deg


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

    タスク 5.3 が `trash_can.bottom_flat_diameter_mm` の仮値を実測へ置き換える。
    そのとき**実装コードを変えずに**座が動くことをここで固定する。

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
    measured_flat_mm = params.trash_can.bottom_flat_diameter_mm - 4.6  # type: ignore[attr-defined]
    update_upstream_measurement(
        "trash_can.bottom_flat_diameter_mm", measured_flat_mm, path=target
    )
    reloaded = upstream_load_params(target).trash_can
    remeasured = adapter_geometry(
        _replace_can(params, bottom_flat_diameter_mm=reloaded.bottom_flat_diameter_mm),  # type: ignore[arg-type]
        layout,
    )

    assert remeasured.contact_radius_mm == pytest.approx(measured_flat_mm / 2.0)
    assert remeasured.contact_radius_mm < adapter.contact_radius_mm
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

    ⚠️ **底へ穴を開けて点で引かない**（`joints.ASSUMPTIONS` の要件 6.8 の根拠）。
    ボルトの軸は底の載る高さより上にあり、座の環が丸ごと立ち上がりに載る
    ——これは駆動ベースの「接合面が座の環を載せられる厚さ」と同じ成立条件である。
    """
    boss_radius_mm = adapter.boss_diameter_mm / 2.0
    assert adapter.retention_bolt_height_mm - boss_radius_mm >= adapter.floor_top_height_mm
    assert adapter.retention_bolt_height_mm + boss_radius_mm <= adapter.rise_top_height_mm
    # 締結は水平（半径方向）である。テーパー面を半径方向に押さえることが、
    # 上方向の拘束（くさび）にもなる。
    assert adapter.seat_top_radius_mm > adapter.seat_bottom_radius_mm


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
