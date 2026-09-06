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
    BuiltPart,
    StandGeometry,
    StandInputs,
    build_parts,
    build_service_stand_legs,
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
    assert PART_NAMES == ("service_stand",)
    assert geometry.leg_count == inputs.leg_count == len(inputs.wheel_angles_deg)
    assert part_names(params) == tuple(  # type: ignore[arg-type]
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
    build_parts,
    part_names,
    stand_geometry,
    stand_inputs,
)

params = load_params()
layout = derive_layout(params)
geometry = stand_geometry(stand_inputs(params, layout))

report = {
    "stub_blocked_the_shape_library": blocked,
    "part_names": list(part_names(params)),
    "trough_floor_height_mm": geometry.trough_floor_height_mm,
    "socket_radius_mm": geometry.socket_radius_mm,
    "support_pad_height_mm": geometry.support_pad_height_mm,
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
    tmp_path: Path, geometry: StandGeometry
) -> None:
    """CAD 非導入の環境で、幾何は導けて**形状生成だけが専用の失敗になる**。

    ⚠️ **成功にしない**（design.md「Error Categories and Responses」/
    「Allowed Dependencies」）。
    """
    import json

    result = _run_blocked(_PROBE_BODY, _nocad_stub(tmp_path))
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])

    assert report["stub_blocked_the_shape_library"] is True
    assert report["shape_library_modules"] == []
    assert report["part_names"] == [
        f"service_stand_{index}" for index in range(1, geometry.leg_count + 1)
    ]
    assert report["trough_floor_height_mm"] == geometry.trough_floor_height_mm
    assert report["socket_radius_mm"] == geometry.socket_radius_mm
    assert report["support_pad_height_mm"] == geometry.support_pad_height_mm
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
    """脚は輪ごとに独立した1個の立体である（決定 5「3脚独立。1体の枠にしない」）。"""
    params, layout = shipped
    parts = build_parts(params, layout)  # type: ignore[arg-type]
    assert len(parts) == geometry.leg_count
    assert [part.name for part in parts] == list(part_names(params))  # type: ignore[arg-type]
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
    """実形状の外接箱が、造形可能寸法の検査へ渡した外接箱と一致する。"""
    params, layout = shipped
    part = build_parts(params, layout)[0]  # type: ignore[arg-type]
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
