"""実形状に対する不変条件（design.md `#### Shapes`「不変条件」/ 要件 5.3-5.6, 5.8）。

design.md `#### Shapes` は不変条件の検査を**本ファイル**に置くと定めており、
整備スタンドについて次の3件を名指ししている。

- スタンドに載せたとき、3輪の最下点が床面より上にあり、
  かつ**ホイール外周とスタンドの隙間**が確保される（要件 5.4, 5.5）
- スタンドの支持面は駆動ベース端部にぶら下がる駆動ユニット（モータ胴体）の
  下面にあり、⚠️ **駆動ベース下面と接触しない**（要件 5.3）

⚠️ **本ファイルは解析値どうしを突き合わせない。** 検査対象は
`build_parts` が実際に構築したソリッドであり、寸法から再計算した数ではない
（上流 `test_catch_rim_invariants.py` と同じ規律）——寸法から作り直した値どうしを
比べると、形状の側に入った誤りをそのまま「一致」と報告してしまう。

⚠️ **ホイールと機体は本ファイルが立てる代用形状である。** 駆動ベース（タスク 3.2）
はまだ存在せず、整備スタンドは要件 5.1 によりそれに**先立って**検証される。
代用形状はホイールの公称寸法と `ChassisLayout` の鉛直スタックだけから立てており、
⚠️ スタンド側の導出値を使って作らない（使えば「自分の値と自分の値が一致する」と
いう恒真の検査になる）。

ファイル名について: `tests/` に `__init__.py` が無くテストモジュール名が
セッション全体でフラットであるため、`test_chassis_` 接頭辞を付ける。
"""

from __future__ import annotations

import itertools
import math
from typing import Any

import pytest
from catch_mechanism import check_envelope

from chassis_mechanism.config import load_params
from chassis_mechanism.joints import derive_joints
from chassis_mechanism.layout import derive_layout
from chassis_mechanism.errors import GeometryError
from chassis_mechanism.joints import DECK_SEAT_JOINT_NAME
from chassis_mechanism.shapes import (
    ADAPTER_SEGMENT_PART_NAME,
    ASSEMBLY_ORDER,
    BATTERY_TRAY_PART_NAME,
    BuiltPart,
    assembled_interferences,
    assembled_parts,
    part_envelopes,
    part_masses,
    CABLE_GUIDE_PART_NAME,
    BOARD_DECK_PART_NAME,
    CATCH_DECK_PART_NAME,
    HUB_PLATE_PART_NAME,
    MIN_HAND_ACCESS_MM,
    MOTOR_ARM_PART_NAME,
    SERVICE_STAND_PART_NAME,
    TRASH_CAN_PART_NAME,
    StandGeometry,
    adapter_geometry,
    assembly_reach_violations,
    assembly_steps,
    battery_tray_geometry,
    build_parts,
    build_trash_can_shell,
    cable_guide_geometry,
    deck_stack_geometry,
    drive_base_geometry,
    part_names,
    stand_geometry,
    stand_inputs,
)

_BOTH_SIDES_MM = 2
"""量が中心線の**両側**に効くことを表す係数（⚠️ 寸法ではない）。

`shapes._BOTH_SIDES` と同じ趣旨であり、テスト側で改めて置く（テストは実装の
私的な名前を読まない）。
"""

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

_EPS_MM = 0.05
"""隙間の一致を「ちょうど」と言うための刻み（mm）。

⚠️ **片側だけでは「ちょうど」を言えない。** 隙間より内側では触れず、外側では
触れる、という**両側**を見て初めて隙間がその値であると言える。
"""

_PROBE_MM = 1000.0
"""半空間の代わりに置く十分に大きな箱の一辺（mm）。"""


# ---------------------------------------------------------------------------
# 共通の足場
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def shipped() -> tuple[Any, Any]:
    """出荷の寸法パラメータと幾何の導出結果（⚠️ 読むだけ）。"""
    params = load_params()
    return params, derive_layout(params)


@pytest.fixture(scope="module")
def geometry(shipped: tuple[Any, Any]) -> StandGeometry:
    params, layout = shipped
    return stand_geometry(stand_inputs(params, layout))


@pytest.fixture(scope="module")
def legs(shipped: tuple[Any, Any]) -> tuple[Any, ...]:
    """構築済みの脚（⚠️ タスク 3.2 以降、`build_parts` は駆動ベースも返す）。"""
    params, layout = shipped
    return tuple(
        part
        for part in build_parts(params, layout)
        if part.name.startswith("service_stand_")
    )


def _box(
    x_range: tuple[float, float],
    y_range: tuple[float, float],
    z_range: tuple[float, float],
) -> Any:
    """局所座標の範囲で表した直方体（プローブ用）。"""
    from build123d import Align, Box, Location

    x_min, x_max = x_range
    y_min, y_max = y_range
    z_min, z_max = z_range
    return Location(((x_min + x_max) / 2.0, (y_min + y_max) / 2.0, (z_min + z_max) / 2.0)) * Box(
        x_max - x_min,
        y_max - y_min,
        z_max - z_min,
        align=(Align.CENTER, Align.CENTER, Align.CENTER),
    )


def _wheel_cylinder(
    *,
    radius_mm: float,
    width_mm: float,
    center_height_mm: float,
    radius_growth_mm: float = 0.0,
    width_growth_mm: float = 0.0,
    x_offset_mm: float = 0.0,
    y_offset_mm: float = 0.0,
) -> Any:
    """与えられた寸法と車軸高さで立てたホイールの代用形状。

    ⚠️ **スタンドの導出値を一切使わない。** 半径・幅・車軸中心の高さは呼び手が
    与える。原点は「ホイール中心の真下の床面」、+x は機体外向き（車軸方向）、
    +y は接線方向である。

    Args:
        radius_mm: ホイールの公称半径（台上のホイールは無荷重であるため公称）。
        width_mm: ホイールの幅。
        center_height_mm: 台上での車軸中心の高さ。
        radius_growth_mm: 外周を膨らませる量（隙間の検査に使う）。
        width_growth_mm: 幅方向へ膨らませる量。
        x_offset_mm: 半径方向（車軸方向）へずらす量。
        y_offset_mm: 接線方向へずらす量（反力で機体が動いた状態の再現）。
    """
    from build123d import Align, Cylinder, Location, Rotation

    return (
        Location((x_offset_mm, y_offset_mm, center_height_mm))
        * Rotation(0, 90, 0)
        * Cylinder(
            radius_mm + radius_growth_mm,
            width_mm + 2.0 * width_growth_mm,
            align=(Align.CENTER, Align.CENTER, Align.CENTER),
        )
    )


def _wheel_solid(
    shipped: tuple[Any, Any],
    *,
    radius_growth_mm: float = 0.0,
    width_growth_mm: float = 0.0,
    x_offset_mm: float = 0.0,
    y_offset_mm: float = 0.0,
) -> Any:
    """脚の局所座標へ置いた、出荷パラメータでのホイールの代用形状。

    ⚠️ **スタンドの導出値を一切使わない。** ホイールの公称寸法（現物採寸）と、
    `stand.lift_height_mm`（持ち上げ高さ）＋ `ChassisLayout` の鉛直スタックが
    持つ**車軸中心の高さ**だけから立てる。⚠️ **`lift + 公称半径` と書かない**
    ——それは `shapes.stand_geometry` が谷の位置を決めるのに使う式そのものであり、
    写せば「自分の値と自分の値が一致する」恒真の検査になる。台上での車軸の高さは
    鉛直スタック（実測が入る側）が決め、⚠️ 公称半径と一致するとは限らない。
    """
    params, layout = shipped
    wheel = params.chassis.wheel
    stand = params.chassis.stand
    return _wheel_cylinder(
        radius_mm=wheel.nominal_diameter_mm / 2.0,
        width_mm=wheel.width_mm,
        center_height_mm=stand.lift_height_mm + layout.vertical.axle_center_height_mm,
        radius_growth_mm=radius_growth_mm,
        width_growth_mm=width_growth_mm,
        x_offset_mm=x_offset_mm,
        y_offset_mm=y_offset_mm,
    )


def _volume(shape: Any) -> float:
    """交差の結果の体積（空なら 0.0）。"""
    return float(shape.volume)


_INSET_MM = 0.01
"""プローブ箱を実体の境界からわずかに内側へ寄せる量（mm）。

⚠️ **面で接するだけの部位をプローブへ拾わせない。** 支持パッドの外側面には谷の
壁が、幅方向には案内リブがちょうど接しており、境界ぴったりの箱で切り出すと
「接しているだけで重なっていない」部位が結果へ混ざり得る。測りたいのは
**重なっている実体**である。
"""


def _pad_probe(geometry: StandGeometry) -> Any:
    """支持パッドの実体だけを切り出すプローブ（境界からわずかに内側）。"""
    return _box(
        (
            geometry.support_pad_inner_x_mm + _INSET_MM,
            geometry.support_pad_outer_x_mm - _INSET_MM,
        ),
        (
            -geometry.support_pad_half_width_mm + _INSET_MM,
            geometry.support_pad_half_width_mm - _INSET_MM,
        ),
        (0.0, _PROBE_MM),
    )


def _column(x_mm: float, y_mm: float) -> Any:
    """`(x, y)` に立てた細い柱（その位置の材料の高さを測る）。"""
    side_mm = 0.4
    return _box(
        (x_mm - side_mm / 2.0, x_mm + side_mm / 2.0),
        (y_mm - side_mm / 2.0, y_mm + side_mm / 2.0),
        (0.0, _PROBE_MM),
    )


# ---------------------------------------------------------------------------
# 1. 3輪の最下点が床面より上にある（要件 5.4 / タスク 3.1 の完了状態）
# ---------------------------------------------------------------------------


@requires_cad
def test_all_three_wheels_hang_above_the_floor_when_the_machine_sits_on_the_stand(
    shipped: tuple[Any, Any], geometry: StandGeometry, legs: tuple[Any, ...]
) -> None:
    """⚠️ **支持面の高さを実形状から測り**、そこから3輪の最下点を出す。

    機体は脚の支持パッドに座る。パッドが受けるのは駆動ユニットの下面であり、
    その面は接地点から `motor_body_bottom_height_mm` の高さにある（`layout` の
    鉛直スタック）。したがって台上での機体の**公称の接地点**は

        パッド上面の高さ − motor_body_bottom_height_mm  （＝ lift_height_mm）

    にあり、⚠️ **ホイールの最下点はそこではない**——車軸は接地点から
    `axle_center_height_mm` の高さにあり、無荷重のホイールの最下点はさらに
    公称半径ぶん下である。実測で車軸が公称半径より下がれば、床までの余裕は
    その分だけ**減る**。3輪が床に触れないの観測はこの最下点の側で見る。
    """
    params, layout = shipped
    assert len(legs) == params.chassis.stand.leg_count == 3

    nominal_radius_mm = params.chassis.wheel.nominal_diameter_mm / 2.0
    pad_probe = _pad_probe(geometry)
    for leg in legs:
        pad = leg.solid & pad_probe
        assert _volume(pad) > 0.0, f"{leg.name}: 支持パッドの実体が無い"
        pad_top_mm = float(pad.bounding_box().max.Z)
        contact_point_mm = pad_top_mm - layout.vertical.motor_body_bottom_height_mm
        assert contact_point_mm == pytest.approx(
            params.chassis.stand.lift_height_mm, abs=1e-6
        )
        wheel_bottom_mm = (
            contact_point_mm
            + layout.vertical.axle_center_height_mm
            - nominal_radius_mm
        )
        assert wheel_bottom_mm > 0.0, f"{leg.name}: ホイールが床へ接地している"


# ---------------------------------------------------------------------------
# 2. ホイールは台に触れず、外周との隙間が寸法パラメータと一致する（要件 5.4, 5.5）
# ---------------------------------------------------------------------------


@requires_cad
def test_no_wheel_touches_the_stand(
    shipped: tuple[Any, Any], legs: tuple[Any, ...]
) -> None:
    """台上でホイールは台のどこにも接触しない（要件 5.4）。"""
    wheel = _wheel_solid(shipped)
    for leg in legs:
        assert _volume(leg.solid & wheel) == 0.0, f"{leg.name}: ホイールが台に接触する"


@requires_cad
def test_the_gap_to_the_wheel_outer_circumference_equals_the_dimension_parameter(
    shipped: tuple[Any, Any], legs: tuple[Any, ...]
) -> None:
    """ホイール外周と台の隙間が `wheel_rotation_clearance_mm` **ちょうど**である。

    ⚠️ **片側だけでは足りない。** 隙間の内側（`clearance − ε`）まで膨らませても
    触れず、外側（`clearance + ε`）まで膨らませると触れることの両方を見る——
    前者だけなら隙間は「それ以上」としか言えず、後者だけなら「それ以下」としか
    言えない。台がホイールから遠ざかっただけの形も、この対で落ちる。
    """
    params, _ = shipped
    clearance_mm = params.chassis.stand.wheel_rotation_clearance_mm
    inside = _wheel_solid(shipped, radius_growth_mm=clearance_mm - _EPS_MM)
    outside = _wheel_solid(shipped, radius_growth_mm=clearance_mm + _EPS_MM)
    for leg in legs:
        assert _volume(leg.solid & inside) == 0.0, (
            f"{leg.name}: 隙間が {clearance_mm}mm より狭い"
        )
        assert _volume(leg.solid & outside) > 0.0, (
            f"{leg.name}: 隙間が {clearance_mm}mm より広い（谷がホイールを受けていない）"
        )


@requires_cad
def test_the_gap_holds_when_the_measured_axle_sits_below_the_nominal_radius(
    shipped: tuple[Any, Any]
) -> None:
    """⚠️ **車軸の高さは公称半径ではなく鉛直スタックが決める**（要件 1.5, 3.9）。

    タスク 2.4 が荷重下の実効転がり半径を実測へ置き換えると、
    `layout.vertical.axle_center_height_mm` は公称半径 δ ぶん**下がる**
    （`_effective_rolling_radius_mm` が唯一の差し込み点である）。台上でも機体は
    その分だけ低く座るため、車軸は `lift + axle_center_height` にあり
    `lift + 公称半径` にはない。⚠️ **谷を後者で切ると、谷だけが δ ぶん高い位置に
    残り、ホイール外周と台の隙間が寸法パラメータより δ 狭くなる**——タスク 3.1 の
    観測可能な完了状態「ホイール外周と台の隙間が寸法パラメータと一致し」が
    出荷値でだけ成り立つ状態になる。

    ⚠️ 谷の**半径**は公称のままでよい（台上のホイールは無荷重であり、縮むのは
    接地側だけである）。追随するのは車軸の**高さ**である。
    """
    import dataclasses

    from chassis_mechanism.shapes import build_service_stand_legs, stand_inputs

    params, layout = shipped
    clearance_mm = params.chassis.stand.wheel_rotation_clearance_mm
    nominal_radius_mm = params.chassis.wheel.nominal_diameter_mm / 2.0

    # ⚠️ 実測が入った状態の再現。実効転がり半径が δ 縮めば車軸もモータ胴体下面も
    # 同じ δ だけ下がる（`layout.derive_layout` の鉛直スタックの組み上げ方）。
    drop_mm = 1.0
    lowered = dataclasses.replace(
        stand_inputs(params, layout),
        axle_center_height_mm=layout.vertical.axle_center_height_mm - drop_mm,
        motor_body_bottom_height_mm=(
            layout.vertical.motor_body_bottom_height_mm - drop_mm
        ),
    )
    assert lowered.axle_center_height_mm < nominal_radius_mm

    legs = build_service_stand_legs(lowered, params.printing)
    center_height_mm = lowered.lift_height_mm + lowered.axle_center_height_mm

    def wheel(**growth: float) -> Any:
        return _wheel_cylinder(
            radius_mm=nominal_radius_mm,
            width_mm=lowered.wheel_width_mm,
            center_height_mm=center_height_mm,
            **growth,
        )

    inside = wheel(radius_growth_mm=clearance_mm - _EPS_MM)
    outside = wheel(radius_growth_mm=clearance_mm + _EPS_MM)
    for leg in legs:
        assert _volume(leg.solid & wheel()) == 0.0, f"{leg.name}: ホイールが台に接触する"
        assert _volume(leg.solid & inside) == 0.0, (
            f"{leg.name}: 隙間が {clearance_mm}mm より狭い"
            "（谷が車軸の実測高さへ追随していない）"
        )
        assert _volume(leg.solid & outside) > 0.0, (
            f"{leg.name}: 隙間が {clearance_mm}mm より広い（谷がホイールを受けていない）"
        )


@requires_cad
def test_the_gap_beside_the_wheel_faces_also_equals_the_dimension_parameter(
    shipped: tuple[Any, Any], legs: tuple[Any, ...]
) -> None:
    """ホイールの側面と台の隙間も同じ量である（幅方向にも回転の余地を残す）。"""
    params, _ = shipped
    clearance_mm = params.chassis.stand.wheel_rotation_clearance_mm
    inside = _wheel_solid(shipped, width_growth_mm=clearance_mm - _EPS_MM)
    outside = _wheel_solid(shipped, width_growth_mm=clearance_mm + _EPS_MM)
    for leg in legs:
        assert _volume(leg.solid & inside) == 0.0
        assert _volume(leg.solid & outside) > 0.0


# ---------------------------------------------------------------------------
# 3. 支持面は駆動ベース下面と接触しない（要件 5.3 / タスク 3.1 の完了状態）
# ---------------------------------------------------------------------------


@requires_cad
def test_the_stand_never_reaches_the_height_of_the_drive_base_underside(
    shipped: tuple[Any, Any], legs: tuple[Any, ...]
) -> None:
    """⚠️ **脚のどの点も駆動ベース下面へ届かない**（支持面が下面に触れない）。

    台上での駆動ベース下面の高さは `mount_face_height_mm + lift_height_mm` で
    ある。脚の全体がその高さより低ければ、⚠️ ブラケット・締結の頭・配線が並ぶ
    ベース下面へは**触れようがない**（要件 5.3 の理由そのもの）。
    """
    params, layout = shipped
    underside_mm = (
        layout.vertical.mount_face_height_mm + params.chassis.stand.lift_height_mm
    )
    half_space = _box(
        (-_PROBE_MM, _PROBE_MM), (-_PROBE_MM, _PROBE_MM), (underside_mm, _PROBE_MM)
    )
    for leg in legs:
        assert float(leg.solid.bounding_box().max.Z) < underside_mm, leg.name
        assert _volume(leg.solid & half_space) == 0.0, (
            f"{leg.name}: 駆動ベース下面の高さへ材料が届いている"
        )


@requires_cad
def test_the_support_surface_is_a_pad_under_the_drive_unit_inboard_of_the_wheel(
    shipped: tuple[Any, Any], geometry: StandGeometry, legs: tuple[Any, ...]
) -> None:
    """支持面が駆動ベース端部の駆動ユニット下面を受ける水平面である（要件 5.3）。

    パッドはホイールの内側面よりさらに機体側にあり（＝ホイール外周には掛からない）、
    その上面は駆動ユニットの下面の高さ（`motor_body_bottom_height_mm + lift`）に
    ある。
    """
    params, layout = shipped
    wheel_inner_face_x_mm = -params.chassis.wheel.width_mm / 2.0
    expected_pad_top_mm = (
        layout.vertical.motor_body_bottom_height_mm
        + params.chassis.stand.lift_height_mm
    )
    pad_probe = _pad_probe(geometry)
    for leg in legs:
        pad = leg.solid & pad_probe
        bbox = pad.bounding_box()
        assert float(bbox.max.Z) == pytest.approx(expected_pad_top_mm, abs=1e-6)
        # ⚠️ パッドはホイールの内側面より内側にある（ホイールに掛からない）。
        assert float(bbox.max.X) <= wheel_inner_face_x_mm
        # 受け面としての広がりを持つ（線でも点でもない）。
        assert float(bbox.max.Y) - float(bbox.min.Y) > 0.0
        assert float(bbox.max.X) - float(bbox.min.X) > 0.0


# ---------------------------------------------------------------------------
# 4. 反力に対する拘束（要件 5.6）
# ---------------------------------------------------------------------------


@requires_cad
def test_the_leg_catches_the_wheel_from_both_sides_when_the_reaction_moves_the_machine(
    shipped: tuple[Any, Any], geometry: StandGeometry, legs: tuple[Any, ...]
) -> None:
    """機体が接線方向へ動けば、ホイールは**両側とも**谷の壁に受け止められる。

    ⚠️ **これが要件 5.6 の観測である。** `joints` の当たり面の面積は
    「モータ反力を受ける面としての最低限の広さ」を見ているだけであり
    （`CONTACT_BEARING_AREA_FORMULA` の docstring）、外れないことを保証して
    いるのは**谷の形**である。片側だけの脚、あるいは浅すぎる谷はここで落ちる。
    """
    params, _ = shipped
    clearance_mm = params.chassis.stand.wheel_rotation_clearance_mm
    for leg in legs:
        for direction in (+1.0, -1.0):
            moved = _wheel_solid(
                shipped, y_offset_mm=direction * (clearance_mm + 10.0 * _EPS_MM)
            )
            caught = leg.solid & moved
            assert _volume(caught) > 0.0, (
                f"{leg.name}: 接線方向 {direction:+.0f} へ動いた機体を受け止められない"
            )
            bbox = caught.bounding_box()
            # 受け止めはホイールの幅の内側で、谷の帯（床から受け面の上端まで）で起こる。
            assert float(bbox.min.X) >= -geometry.socket_half_width_mm - 1e-6
            assert float(bbox.max.X) <= geometry.socket_half_width_mm + 1e-6
            assert float(bbox.max.Z) <= geometry.socket_top_height_mm + 1e-6


@requires_cad
def test_the_outer_radial_wall_stops_the_machine_from_sliding_off_outboard(
    shipped: tuple[Any, Any], geometry: StandGeometry, legs: tuple[Any, ...]
) -> None:
    """⚠️ **谷の外周側（+x）には壁が残り、機体が外へ抜けない**（要件 5.6）。

    谷を抜く円筒は機体側（−x）へは本体の面を貫くが、外周側は貫かない。
    ⚠️ **貫いてしまっても接線方向の検査は通る**（ホイールを ±y へ動かす検査は
    +x の壁を1点も押さえない）ため、ここで壁そのものを2通りに固定する。

    - ホイールの真横（+x 側、谷の帯の高さ）に材料が残っていること
    - 半径方向外向きへ隙間ぶん動いたホイールが、⚠️ **その壁に受け止められる**こと
      （接線方向の検査の +x 版。受け止めは谷の外側の面より外で起こる）
    """
    params, _ = shipped
    clearance_mm = params.chassis.stand.wheel_rotation_clearance_mm

    # ⚠️ ホイールの真横（y≈0）で、谷の底のすぐ上からホイール最下点までの帯を
    # 見る。⚠️ **谷の円筒の内側に完全に入る帯を選ぶ**——外周側まで貫けば、この帯は
    # まるごと空になる（帯を広く取ると、円筒の外に残る隅の肉を拾ってしまう）。
    wall_probe = _box(
        (geometry.socket_half_width_mm + _INSET_MM, geometry.outer_x_mm - _INSET_MM),
        (-1.0, 1.0),
        (
            geometry.trough_floor_height_mm + 1.0,
            geometry.wheel_bottom_height_mm - _INSET_MM,
        ),
    )
    moved = _wheel_solid(shipped, x_offset_mm=clearance_mm + 10.0 * _EPS_MM)
    for leg in legs:
        assert _volume(leg.solid & wall_probe) > 0.0, (
            f"{leg.name}: 谷の外周側に壁が無い（機体が半径方向外向きへ抜ける）"
        )
        caught = leg.solid & moved
        assert _volume(caught) > 0.0, (
            f"{leg.name}: 半径方向外向きへ動いた機体を受け止められない"
        )
        # 受け止めているのは谷の外側の面より外——すなわち外周側の壁である。
        assert float(caught.bounding_box().min.X) >= (
            geometry.socket_half_width_mm - 1e-6
        )


@requires_cad
def test_the_realised_valley_matches_the_bearing_area_recorded_by_joints(
    shipped: tuple[Any, Any], geometry: StandGeometry, legs: tuple[Any, ...]
) -> None:
    """実形状の谷の寸法が `joints` の当たり面の式と噛み合う（要件 5.6）。

    ⚠️ **谷の高さを実形状から測る。** ホイールの真下に立てた柱で谷の底を、
    受け面の外側（壁の肉の中）に立てた柱で受け面の上端を測り、その差が
    `support_span_mm` と一致することを見る。`joints` が記録する当たり面
    `wheel.width_mm * stand.support_span_mm` は、この帯のうちホイールが実際に
    当たれる範囲（幅はホイール自身の幅）の投影である。式を写しただけの記録は、
    形が変われば実測との差としてここに現れる。
    """
    params, layout = shipped
    stand = params.chassis.stand
    wheel = params.chassis.wheel
    stand_joints = [
        joint for joint in derive_joints(layout, params) if "service_stand" in joint.name
    ]
    assert len(stand_joints) == len(legs)

    for leg in legs:
        # ホイール中心の真下: ここに残る材料の上面が谷の底である。
        floor = leg.solid & _column(0.0, 0.0)
        assert _volume(floor) > 0.0, f"{leg.name}: 谷の底に材料が無い"
        floor_top_mm = float(floor.bounding_box().max.Z)
        # 谷の壁（受け面の外側の肉）: ここに残る材料の上面が受け面の上端である。
        flank = leg.solid & _column(0.0, geometry.socket_radius_mm + 1.0)
        assert _volume(flank) > 0.0, f"{leg.name}: 谷の壁が無い（片側だけの脚）"
        flank_top_mm = float(flank.bounding_box().max.Z)

        # ⚠️ 許容差は柱の太さぶんである。谷の面は曲面（車軸と同軸の円筒）であり、
        # 柱の端（中心から 0.2mm）では底が 0.0005mm だけ高い。**値そのものの
        # ずれではなく測り方の幅**であり、丸めや当て込みではない。
        # ⚠️ 谷の底の高さは**車軸の実測高さ**から決まる（`lift − 隙間` と書かない
        # ——それが成り立つのは車軸が公称半径にある間だけである）。
        wheel_bottom_mm = (
            stand.lift_height_mm
            + layout.vertical.axle_center_height_mm
            - wheel.nominal_diameter_mm / 2.0
        )
        assert floor_top_mm == pytest.approx(
            wheel_bottom_mm - stand.wheel_rotation_clearance_mm, abs=0.01
        )
        assert flank_top_mm - floor_top_mm == pytest.approx(
            stand.support_span_mm, abs=0.01
        )
        # 谷の壁は外側（＋y）だけでなく内側（−y）にも同じ高さで立つ。
        opposite = leg.solid & _column(0.0, -(geometry.socket_radius_mm + 1.0))
        assert _volume(opposite) > 0.0
        assert float(opposite.bounding_box().max.Z) == pytest.approx(
            flank_top_mm, abs=1e-6
        )

    for joint in stand_joints:
        assert joint.bearing_area_mm2 == pytest.approx(
            wheel.width_mm * stand.support_span_mm, abs=1e-9
        )


# ---------------------------------------------------------------------------
# 5. 回転が見え、手が届く（要件 5.8）
# ---------------------------------------------------------------------------


@requires_cad
def test_the_stand_does_not_enclose_the_wheel_above_its_axle(
    shipped: tuple[Any, Any], geometry: StandGeometry, legs: tuple[Any, ...]
) -> None:
    """車軸より上に台の材料が無い（ホイールの回転方向を目視できる。要件 5.8）。"""
    params, _ = shipped
    axle_height_mm = (
        params.chassis.stand.lift_height_mm + params.chassis.wheel.nominal_diameter_mm / 2.0
    )
    above_axle = _box(
        (-_PROBE_MM, _PROBE_MM), (-_PROBE_MM, _PROBE_MM), (axle_height_mm, _PROBE_MM)
    )
    for leg in legs:
        assert _volume(leg.solid & above_axle) == 0.0, (
            f"{leg.name}: 車軸より上を台が覆っており、回転方向が見えない"
        )
        assert float(leg.solid.bounding_box().max.Z) <= axle_height_mm + 1e-6
    assert geometry.socket_top_height_mm <= axle_height_mm


@requires_cad
def test_the_three_legs_are_independent_and_leave_room_for_a_hand(
    shipped: tuple[Any, Any], geometry: StandGeometry, legs: tuple[Any, ...]
) -> None:
    """3脚は互いに離れて置かれ、間に手が入る（要件 5.8 / 決定 5「3脚独立」）。

    ⚠️ 「手が届く」を完全に機械化することはできない。ここで固定するのは
    **隣り合う脚の間に手の幅ぶんの開きがある**ことと、**脚同士が繋がっていない**
    こと——1体の枠にしていないことの形状側の観測である。
    """
    from build123d import Location, Rotation

    placed = [
        Rotation(0, 0, angle) * Location((geometry.placement_radius_mm, 0.0, 0.0)) * leg.solid
        for angle, leg in zip(geometry.leg_angles_deg, legs, strict=True)
    ]
    for index, first in enumerate(placed):
        for second in placed[index + 1 :]:
            assert _volume(first & second) == 0.0, "脚同士が重なっている"
            measured_mm = first.distance_to(second)
            assert measured_mm >= MIN_HAND_ACCESS_MM
            # ⚠️ `stand_geometry` が算術で持つ開きは実形状の開きの**下限**で
            # なければならない。覆いが実形状より狭ければ、「形状生成の前に落とす」
            # 検査が空振りする。
            assert measured_mm >= geometry.leg_separation_mm - 1e-6, (
                "算術の開きが実形状の開きより広い（覆いになっていない）"
            )


# ---------------------------------------------------------------------------
# 6. 造形可能寸法（要件 2.2）
# ---------------------------------------------------------------------------


@requires_cad
def test_each_leg_fits_the_build_volume(
    shipped: tuple[Any, Any], legs: tuple[Any, ...]
) -> None:
    """1脚ずつの外接箱が造形可能寸法に収まる（決定 5「1体の枠にしない」の帰結）。"""
    from catch_mechanism import Envelope

    params, _ = shipped
    for leg in legs:
        x_mm, y_mm, z_mm = leg.metrics.bbox_mm
        violations = check_envelope(
            leg.name, Envelope(x_mm=x_mm, y_mm=y_mm, z_mm=z_mm), params.printing
        )
        assert violations == (), violations


# ---------------------------------------------------------------------------
# 7. 駆動ベース（タスク 3.2 / 要件 2.5, 2.6, 2.11, 3.1, 3.7, 3.8, 3.10）
#
# ⚠️ **本節の中心は「解析値と実形状の突き合わせ」である**（design.md
# `#### Joints` Risks: 当たり面は寸法パラメータから解析的に算出し、実形状との
# 一致は本ファイルが検査する）。`joints` は build123d を import できないため、
# 当たり面が形の上で本当に実現しているかを見られるのはここだけである。
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def drive_base(shipped: tuple[Any, Any]) -> Any:
    params, layout = shipped
    return drive_base_geometry(params, layout)


@pytest.fixture(scope="module")
def parts(shipped: tuple[Any, Any]) -> dict[str, Any]:
    """構築済みの全部品を名前で引ける形にする。"""
    params, layout = shipped
    return {part.name: part for part in build_parts(params, layout)}


def _arm_joint(shipped: tuple[Any, Any]) -> Any:
    params, layout = shipped
    return next(
        joint
        for joint in derive_joints(layout, params)
        if joint.name == "hub_plate__motor_arm_1"
    )


def _boss_region(solid: Any, *, radius_mm: float, centre: tuple[float, float, float]) -> Any:
    """ボルト座の範囲だけを、⚠️ **幾何量（座の半径）で**切り出す。

    上流 `test_catch_shapes.py::_joint_boss_region` と同じ規律である——生成名を
    使わず、ボルトの軸に同軸な円筒との交差で座の範囲を取る。⚠️ **端面全体を
    測って比べる形にしない**：ボルトから離れた材料は座面圧を1Pa も下げない
    （`check_joint` が名指しする破壊モードはボルト座面のめり込みである）。
    """
    from build123d import Align, Cylinder, Location, Rotation

    return solid & (
        Location(centre)
        * Rotation(90, 0, 0)
        * Cylinder(radius_mm, _PROBE_MM, align=(Align.CENTER, Align.CENTER, Align.CENTER))
    )


def _planar_faces_on_plane(solid: Any, normal: tuple[float, float, float], offset_mm: float) -> list[Any]:
    """`normal` を法線に持ち、その方向の座標が `offset_mm` である平面を選ぶ。

    ⚠️ **生成名を使わない。** 選択条件は「法線の向き」と「面の中心がその平面上に
    ある」という幾何量だけである（上流 `_planar_faces_on_end_plane` と同じ）。
    """
    from build123d import GeomType

    selected: list[Any] = []
    for face in solid.faces():
        if face.geom_type != GeomType.PLANE:
            continue
        unit = face.normal_at()
        if (
            abs(unit.X - normal[0]) > 1e-4
            or abs(unit.Y - normal[1]) > 1e-4
            or abs(unit.Z - normal[2]) > 1e-4
        ):
            continue
        centre = face.center()
        along_mm = centre.X * normal[0] + centre.Y * normal[1] + centre.Z * normal[2]
        if abs(along_mm - offset_mm) > 1e-6:
            continue
        selected.append(face)
    return selected


def _measured_bolt_seat_area_mm2(solid: Any, geometry: Any) -> float:
    """構築したソリッドから、ボルト座の当たり面の**実面積**を測る。

    ⚠️ **測る面はボルト頭が当たる側**（貫通穴が開いている側）である。反対側の
    側壁にはインサート座（より大きい穴）が開くため、両者の面積は一致しない。
    `joints.BEARING_AREA_FORMULA` が貫通穴径を引いている以上、突き合わせる相手は
    貫通穴側の面である。
    """
    normal = (0.0, -1.0, 0.0)
    offset_mm = geometry.fork_half_width_mm  # 面の中心 · normal = +half_width
    total_mm2 = 0.0
    for radius_mm in geometry.bolt_radii_mm:
        region = _boss_region(
            solid,
            radius_mm=geometry.boss_diameter_mm / 2.0,
            centre=(radius_mm, 0.0, geometry.bolt_height_mm),
        )
        faces = _planar_faces_on_plane(region, normal, offset_mm)
        assert faces, f"半径 {radius_mm}mm の座に、貫通穴側の平面が無い"
        total_mm2 += sum(float(face.area) for face in faces)
    return total_mm2


@requires_cad
def test_the_measured_bolt_seat_area_matches_the_bearing_area_recorded_by_joints(
    shipped: tuple[Any, Any], drive_base: Any, parts: dict[str, Any]
) -> None:
    """⚠️ **記録された当たり面が、構築したアームの座の実面積と一致する。**

    `joints` は build123d を import できないため当たり面を解析式で持つ
    （design.md `#### Joints` Risks）。⚠️ **解析式が実形状から離れていれば、
    「下限を満たす」という判定は形について何も言っていない。** 突き合わせる
    片側は**寸法から計算し直した数ではなく、ソリッドから測った面積**である。

    ⚠️ **この検査は実際に噛む。** 接合面の厚さが座の外径 9.2mm を下回ると座の環は
    面に載りきらず、測れる面積は解析値を下回る——対になる
    `test_a_joint_face_thinner_than_the_boss_cannot_realise_the_bearing_area` が
    そのことを実形状で示す。
    """
    params, _ = shipped
    joint = _arm_joint(shipped)
    arm = parts["motor_arm_1"]
    measured_mm2 = _measured_bolt_seat_area_mm2(arm.solid, drive_base)

    assert measured_mm2 == pytest.approx(joint.bearing_area_mm2, rel=1e-9)
    # 記録が下限を満たすという主張が、実形状の側でも成り立っている（要件 2.9）。
    assert measured_mm2 >= joint.min_bearing_area_mm2
    assert measured_mm2 >= params.joint.min_bearing_area_mm2


@requires_cad
def test_the_measured_bolt_seat_area_is_insensitive_to_the_arm_width(
    shipped: tuple[Any, Any], drive_base: Any
) -> None:
    """⚠️ ボルトから離れた材料を当たり面に数えていない（要件 2.6 / 8.4 の性質）。

    `check_joint` が名指しする破壊モードはボルト座面のめり込みであり、支配量は
    座面圧である。⚠️ **アーム幅を広げても座面圧は 1Pa も下がらない。** 幅を
    広げて測った面積が動かないことが、「離れた材料を数えていない」ことの
    観測可能な形である。

    ⚠️ **倍率は 1.5 から 1.15 へ下げた（主張は変えていない）。** 中央部は配線の
    通し穴を持つため `build_drive_base` は配線ガイドの幾何を要するが、
    ⚠️ **アーム幅 1.5 倍では3系統を分けて通す弧が消え、機体そのものが拒否される**
    （`tasks.md`「3.5 が残した申し送り」）。ここで見たいのは⚠️ **幅を広げても
    座面積が動かないこと**であり、広げる量は主張に関与しない。
    """
    import dataclasses

    from chassis_mechanism.shapes import build_drive_base

    params, layout = shipped
    wider = dataclasses.replace(
        params,
        chassis=dataclasses.replace(
            params.chassis,
            base=dataclasses.replace(
                params.chassis.base,
                arm_width_mm=params.chassis.base.arm_width_mm * 1.15,
            ),
        ),
    )
    wider_geometry = drive_base_geometry(wider, layout)
    wider_arm = next(
        part for part in build_drive_base(wider, layout) if part.name == "motor_arm_1"
    )
    assert wider_geometry.arm_half_width_mm > drive_base.arm_half_width_mm
    assert _measured_bolt_seat_area_mm2(
        wider_arm.solid, wider_geometry
    ) == pytest.approx(
        _measured_bolt_seat_area_mm2(
            next(
                part
                for part in build_drive_base(params, layout)
                if part.name == "motor_arm_1"
            ).solid,
            drive_base,
        ),
        rel=1e-9,
    )


@requires_cad
def test_a_joint_face_thinner_than_the_boss_cannot_realise_the_bearing_area(
    shipped: tuple[Any, Any], drive_base: Any
) -> None:
    """⚠️ **薄い接合面では記録された当たり面が実現しない**（要件 2.5 / 2.9）。

    これが `base.arm_thickness_mm` を 6.0mm から 15.0mm へ引き上げた根拠である。
    ⚠️ 6.0mm の接合面では座の環（φ9.2）が面に載りきらず、⚠️ **測れる当たり面は
    解析値を大きく下回り、本 Spec の下限 90mm^2 すら満たさない**——にもかかわらず
    解析式は満たしていると述べる。ここではその差を**実形状の上で**固定する。

    ⚠️ `drive_base_geometry` はこの寸法を構築前に拒否するため（形の成立条件）、
    ここでは幾何を直接差し替えて**測るためだけに**構築する。
    """
    import dataclasses
    import math

    from chassis_mechanism.shapes import (
        _build_motor_arm,
        battery_tray_geometry,
        cable_guide_geometry,
    )

    params, layout = shipped
    joint = _arm_joint(shipped)
    thin_thickness_mm = 6.0
    assert thin_thickness_mm < drive_base.boss_diameter_mm

    # ⚠️ ボルトの軸はアームの厚さの中央にある。厚さを差し替えるなら軸の高さも
    # 同じ規則で差し替える——片方だけ動かすと、座が面から外れた形を測ってしまう。
    thin = dataclasses.replace(
        drive_base,
        arm_thickness_mm=thin_thickness_mm,
        bolt_height_mm=drive_base.underside_height_mm + thin_thickness_mm / 2.0,
    )
    # ⚠️ バッテリトレイの穴はアームの外側の帯（半径 114mm 以遠）にあり、
    # ここで測る座（半径 64.6 / 73.8mm）とは重ならない。
    tray = battery_tray_geometry(params, layout)
    # ⚠️ 配線ガイドの座はアームの**上面**（半径 109.6 / 130.6mm）にあり、
    # ここで測る座とは重ならない。
    guide = cable_guide_geometry(params, layout)
    measured_mm2 = _measured_bolt_seat_area_mm2(
        _build_motor_arm(thin, tray, guide), thin
    )

    # 帯へ切り取られた環の面積を、⚠️ **形からではなく初等幾何から**独立に出す。
    boss_radius_mm = drive_base.boss_diameter_mm / 2.0
    half_band_mm = thin_thickness_mm / 2.0
    strip_mm2 = 2.0 * (
        boss_radius_mm**2 * math.asin(half_band_mm / boss_radius_mm)
        + half_band_mm * math.sqrt(boss_radius_mm**2 - half_band_mm**2)
    )
    hole_mm2 = math.pi / 4.0 * params.joint.through_hole_diameter_mm**2
    expected_mm2 = drive_base.bolt_count * (strip_mm2 - hole_mm2)

    assert measured_mm2 == pytest.approx(expected_mm2, rel=1e-6)
    assert measured_mm2 < joint.bearing_area_mm2
    # ⚠️ **解析値は下限を満たすと述べるが、実物は満たさない。**
    assert joint.bearing_area_mm2 >= joint.min_bearing_area_mm2
    assert measured_mm2 < joint.min_bearing_area_mm2


@requires_cad
def test_the_bracket_slots_are_slots_whose_travel_matches_the_parameter(
    shipped: tuple[Any, Any], drive_base: Any, parts: dict[str, Any]
) -> None:
    """⚠️ 長穴の移動量が `base.slot_travel_mm` と一致する（要件 3.8 / タスク 3.2）。

    ⚠️ **実形状から測る。** アームの上面の高さで長穴の位置に細い柱を立て、
    材料が無いこと（＝穴が開いていること）を半径方向へ掃いて、開いている区間の
    長さと幅を出す。移動量は「長さ − 幅」である。
    """
    params, _ = shipped
    _assert_slots_travel(
        parts["motor_arm_1"].solid,
        drive_base,
        expected_travel_mm=params.chassis.base.slot_travel_mm,
        expected_width_mm=params.joint.through_hole_diameter_mm,
    )


_SLOT_MARGIN_MM = 0.5
"""長穴を測る窓を、アームの上下面から内側へ寄せる量（mm）。

⚠️ **アームの外側の空間を穴として拾わないための量である。** 窓を板厚より高く
取ると、上下に残る空きが「穴の一部」として外接箱へ混ざる。
"""


def _assert_slots_travel(
    solid: Any,
    geometry: Any,
    *,
    expected_travel_mm: float,
    expected_width_mm: float,
) -> None:
    """長穴の実形状を測り、幅と移動量を突き合わせる。

    ⚠️ **期待値は寸法パラメータと上流の継手方針から来る**（`shapes` の導出値では
    ない）。移動量は「長穴の長さ − 長穴の幅」であり、幅ぶんは締結要素そのものが
    占めるため動ける量に入らない。
    """
    span_mm = expected_width_mm + expected_travel_mm + 2.0
    for offset_mm in geometry.slot_offsets_mm:
        # ⚠️ **窓はアームの幅の内側に収める。** はみ出すと、アームの外の空間が
        # 「穴の一部」として外接箱へ混ざり、幅を測れなくなる。
        span_y_mm = min(
            span_mm, geometry.arm_half_width_mm - abs(offset_mm) - _SLOT_MARGIN_MM
        )
        assert span_y_mm > expected_width_mm / 2.0, (
            "窓が長穴より狭い（長穴がアームの縁へ寄りすぎている）"
        )
        window = _box(
            (
                geometry.slot_center_radius_mm - span_mm,
                geometry.slot_center_radius_mm + span_mm,
            ),
            (offset_mm - span_y_mm, offset_mm + span_y_mm),
            (
                geometry.underside_height_mm + _SLOT_MARGIN_MM,
                geometry.underside_height_mm
                + geometry.arm_thickness_mm
                - _SLOT_MARGIN_MM,
            ),
        )
        assert _volume(solid & window) > 0.0, "長穴のまわりに材料が無い"
        void = window - solid
        assert _volume(void) > 0.0, f"接線方向 {offset_mm}mm に穴が開いていない"
        bbox = void.bounding_box()
        measured_length_mm = float(bbox.max.X) - float(bbox.min.X)
        measured_width_mm = float(bbox.max.Y) - float(bbox.min.Y)

        assert measured_width_mm == pytest.approx(expected_width_mm, abs=1e-6)
        assert measured_length_mm - measured_width_mm == pytest.approx(
            expected_travel_mm, abs=1e-6
        )
        # 穴は板厚を貫いている（袋穴ではない）——窓の高さいっぱいに空いている。
        assert float(bbox.max.Z) - float(bbox.min.Z) == pytest.approx(
            geometry.arm_thickness_mm - 2.0 * _SLOT_MARGIN_MM, abs=1e-6
        )


@requires_cad
def test_the_measured_slot_travel_follows_the_dimension_parameter(
    shipped: tuple[Any, Any]
) -> None:
    """⚠️ 別の移動量でも実形状が追随する（出荷値での一点合わせではない）。

    ⚠️ `0.0`（差を吸収しない）も設定として成立する——そのとき長穴は丸穴になり、
    測った長さと幅が一致する（要件 3.8 の「0 を許す」の形状側の現れ）。
    """
    import dataclasses

    from chassis_mechanism.shapes import build_drive_base

    params, layout = shipped
    for travel_mm in (0.0, 8.0):
        moved = dataclasses.replace(
            params,
            chassis=dataclasses.replace(
                params.chassis,
                base=dataclasses.replace(
                    params.chassis.base, slot_travel_mm=travel_mm
                ),
            ),
        )
        geometry = drive_base_geometry(moved, layout)
        arm = next(
            part
            for part in build_drive_base(moved, layout)
            if part.name == "motor_arm_1"
        )
        _assert_slots_travel(
            arm.solid,
            geometry,
            expected_travel_mm=travel_mm,
            expected_width_mm=params.joint.through_hole_diameter_mm,
        )


@requires_cad
def test_no_printed_part_touches_the_motor_body(
    shipped: tuple[Any, Any], parts: dict[str, Any]
) -> None:
    """⚠️ **造形部品がモータ本体をクランプしない**（要件 3.7 / タスク 3.2）。

    モータ胴体の代用形状は⚠️ **`shapes` の導出値を使わずに**立てる——胴体の外径と
    全長は現物採寸値、車軸の高さとギヤボックス端面の半径は `ChassisLayout` の
    鉛直スタックと軸方向スタックから来る。取り付けは付属金属ブラケットが担い、
    造形部品はどれも胴体へ触れない。
    """
    from build123d import Align, Cylinder, Location, Rotation

    params, layout = shipped
    motor = params.chassis.motor
    outer_radius_mm = layout.base_radius_mm - layout.axial_stack_mm[1]
    inner_radius_mm = outer_radius_mm - motor.body_length_mm
    centre_radius_mm = (outer_radius_mm + inner_radius_mm) / 2.0

    def body(angle_deg: float) -> Any:
        return (
            Rotation(0, 0, angle_deg)
            * Location((centre_radius_mm, 0.0, layout.vertical.axle_center_height_mm))
            * Rotation(0, 90, 0)
            * Cylinder(
                motor.body_diameter_mm / 2.0,
                motor.body_length_mm,
                align=(Align.CENTER, Align.CENTER, Align.CENTER),
            )
        )

    placed = _placed_drive_base(shipped, parts)
    bodies = [body(angle_deg) for angle_deg in layout.wheel_angles_deg]
    for name, solid in placed.items():
        for index, motor_body in enumerate(bodies, start=1):
            assert _volume(solid & motor_body) == 0.0, (
                f"{name} がモータ {index} の胴体を掴んでいる"
            )

    # ⚠️ **半径方向では重なっている**——高さだけが隔てている（検査が空振りでない）。
    assert inner_radius_mm < params.chassis.base.hub_outer_diameter_mm / 2.0
    assert outer_radius_mm > params.chassis.base.hub_outer_diameter_mm / 2.0


def _placed_drive_base(
    shipped: tuple[Any, Any], parts: dict[str, Any]
) -> dict[str, Any]:
    """駆動ベースの部品を機体座標へ据え付ける（中央部はそのまま、アームは回す）。

    ⚠️ **配線ガイドはここに現れない。** ガイドはアダプタより**後**に据える
    （`ASSEMBLY_ORDER` / design.md 組立手順 13）ため、アダプタを差し込む経路や
    その取付ボルトを回す筋を見る検査では、まだ置かれていない部品である。
    ⚠️ **裏返せば、缶を外すときはガイドを先に緩める**——ガイドの板はアダプタの
    外周のすぐ外（嵌め合い隙間ぶん）に立っており、アダプタ断片はその下を半径
    方向へ通れない。組み上がり状態の干渉は `_placed_cable_guides` を足して見る。
    """
    from build123d import Rotation

    _, layout = shipped
    placed: dict[str, Any] = {"hub_plate": parts["hub_plate"].solid}
    for index, angle_deg in enumerate(layout.wheel_angles_deg, start=1):
        placed[f"motor_arm_{index}"] = (
            Rotation(0, 0, angle_deg) * parts[f"motor_arm_{index}"].solid
        )
    return placed


def _placed_cable_guides(
    shipped: tuple[Any, Any], parts: dict[str, Any]
) -> dict[str, Any]:
    """配線ガイドを機体座標へ据え付ける（⚠️ 輪の角度へ回す）。

    ⚠️ **3点は同一のソリッドである**（`shapes._placed_machine_parts` と同じ扱い）
    ——回さずに並べると自分自身と重なる。
    """
    from build123d import Rotation

    _, layout = shipped
    return {
        f"cable_guide_{index}": Rotation(0, 0, angle_deg)
        * parts[f"cable_guide_{index}"].solid
        for index, angle_deg in enumerate(layout.wheel_angles_deg, start=1)
    }


@requires_cad
def test_the_tongue_enters_the_fork_without_the_parts_interfering(
    shipped: tuple[Any, Any], drive_base: Any, parts: dict[str, Any]
) -> None:
    """⚠️ 舌と二股は**重ならずに**噛み合う（隙間で合わせる。要件 2.11 / 決定 4）。

    切削で合わせる嵌合を設計に含めないため、舌は二股の溝より薄い。組み上がり
    状態でどの2部品も干渉しない（design.md `#### Shapes` 不変条件 / 要件 9.1）
    ことと、⚠️ **舌が二股の区間へ実際に入っている**ことの両方を見る。
    """
    placed = _placed_drive_base(shipped, parts)
    names = list(placed)
    for index, first in enumerate(names):
        for second in names[index + 1 :]:
            assert _volume(placed[first] & placed[second]) == 0.0, (
                f"{first} と {second} が干渉している"
            )

    # 舌は二股の溝の中にある: 重ね代の区間で、中央部の材料が二股の溝の幅の内側に
    # 収まり、かつアームの内側の区間に届いている。
    lap_probe = _box(
        (drive_base.hub_radius_mm + 0.5, drive_base.hub_radius_mm + drive_base.lap_length_mm - 0.5),
        (-_PROBE_MM, _PROBE_MM),
        (drive_base.underside_height_mm + 0.5, drive_base.underside_height_mm + drive_base.plate_thickness_mm - 0.5),
    )
    tongue = placed["hub_plate"] & lap_probe
    assert _volume(tongue) > 0.0, "重ね代の区間に中央部の材料が無い（舌が無い）"
    bbox = tongue.bounding_box()
    assert float(bbox.max.Y) <= drive_base.fork_slot_width_mm / 2.0 + 1e-6
    assert float(bbox.min.Y) >= -drive_base.fork_slot_width_mm / 2.0 - 1e-6
    # 二股の側壁は溝の外側にあり、舌と重ならない。
    fork = placed["motor_arm_1"] & lap_probe
    assert _volume(fork) > 0.0, "重ね代の区間にアームの材料が無い（二股が無い）"
    fork_bbox = fork.bounding_box()
    assert float(fork_bbox.max.Y) == pytest.approx(drive_base.fork_half_width_mm, abs=1e-6)
    assert float(fork_bbox.min.Y) == pytest.approx(-drive_base.fork_half_width_mm, abs=1e-6)


@requires_cad
def test_each_drive_base_fragment_fits_the_build_volume(
    shipped: tuple[Any, Any], drive_base: Any, parts: dict[str, Any]
) -> None:
    """各断片の外接箱が造形可能寸法に収まる（要件 2.2 / タスク 3.2 の完了状態）。

    ⚠️ **点数は `joints.segment_counts()` が正である**（要件 2.1）。ここで数え
    直さない。中央部の宣言外接箱（`joints._check_fragment_envelopes` が見る量）は
    舌の先端が描く円の外接正方形であり、⚠️ **実形状の外接箱を覆う**——覆いで
    なければ「収まっている」という判定が実物について述べたものにならない。
    """
    from catch_mechanism import Envelope

    from chassis_mechanism.joints import segment_counts

    params, _ = shipped
    counts = segment_counts(params)
    for base_name in ("hub_plate", "motor_arm"):
        built = [
            name
            for name in parts
            if name == base_name or name.startswith(f"{base_name}_")
        ]
        assert len(built) == counts[base_name], base_name

    for name, part in parts.items():
        x_mm, y_mm, z_mm = part.metrics.bbox_mm
        violations = check_envelope(
            name, Envelope(x_mm=x_mm, y_mm=y_mm, z_mm=z_mm), params.printing
        )
        assert violations == (), violations

    hub_bbox = parts["hub_plate"].metrics.bbox_mm
    declared = drive_base.hub_plate_envelope
    assert hub_bbox[0] <= declared.x_mm + 1e-6
    assert hub_bbox[1] <= declared.y_mm + 1e-6
    assert hub_bbox[2] == pytest.approx(declared.z_mm, abs=1e-6)
    # 舌があるぶん、実形状は公称外径より大きい（宣言が公称のままでは覆えない）。
    assert hub_bbox[0] > params.chassis.base.hub_outer_diameter_mm

    arm_bbox = parts["motor_arm_1"].metrics.bbox_mm
    arm_declared = drive_base.motor_arm_envelope
    for measured, expected in zip(
        arm_bbox, (arm_declared.x_mm, arm_declared.y_mm, arm_declared.z_mm), strict=True
    ):
        assert measured == pytest.approx(expected, abs=1e-6)


# ---------------------------------------------------------------------------
# 3. ゴミ箱固定アダプタ（タスク 3.3 / 要件 2.2, 6.1, 6.2, 6.5, 6.6, 6.7, 6.10）
#
# ⚠️ **アダプタは座ではなくクランプである**（design.md 決定 4b）。ゴミ箱の底は
# 平面部径（Ø170）まで抜かれ、⚠️ **下から支える平面はもう無い**。残るのは外径
# との差ぶんの縁（片側 5mm）であり、クランプはその**下へ入って掴む**。
#
# ⚠️ **本節の中心は5つである。**
#   - 受け面が**円錐台に沿う**こと（要件 6.2）。⚠️ 円筒断面を持たない
#   - 切り取り径が平面部径を超えず、⚠️ **縁の下に掴み面が実在する**こと（要件 6.10）
#   - ゴミ箱が提供する通過を**狭めない**こと（要件 6.7）。⚠️ **基準は変わった**
#     ——底の内面ではなく、**底を抜いた開口から `taper_deg` で広がる円錐**であり、
#     中央は段（タスク 3.4）が通れるよう開いている
#   - 缶を据えたまま断片を差し込めること（要件 6.6 の着脱手順が成立する条件）
#   - `joints` が記録した当たり面が、⚠️ **実形状の座で実現している**こと
#     （design.md `#### Joints` Risks。アダプタの2家族は本タスクまで未計測だった）
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def adapter(shipped: tuple[Any, Any]) -> Any:
    params, layout = shipped
    return adapter_geometry(params, layout)


@pytest.fixture(scope="module")
def adapter_parts(adapter: Any, parts: dict[str, Any]) -> tuple[Any, ...]:
    """構築済みのアダプタ断片（⚠️ 機体座標。据え付けの回転は要らない）。"""
    return tuple(
        parts[f"adapter_segment_{index}"].solid
        for index in range(1, adapter.segment_count + 1)
    )


def _full_cylinder(radius_mm: float, z_range: tuple[float, float]) -> Any:
    """機体の軸に同軸な円筒プローブ。

    ⚠️ **`align=None` は軸を機体中心に置き、下端を原点へ置く**（中央ではない。
    `shapes._build_adapter_segment` の `ring_tool` と同じ規約）。したがって
    位置は `z_range[0]` である——⚠️ **中点を渡すと筒は上半分ぶんずれ、
    プローブが隣の帯を掴む。**
    """
    from build123d import Cylinder, Location

    z_min, z_max = z_range
    return Location((0.0, 0.0, z_min)) * Cylinder(
        radius_mm, z_max - z_min, align=None
    )


def _cone_probe(
    bottom_radius_mm: float, slope: float, z_range: tuple[float, float]
) -> Any:
    """下端 `z_range[0]` で `bottom_radius_mm` の、勾配 `slope` の円錐プローブ。"""
    from build123d import Cone, Location

    z_min, z_max = z_range
    height_mm = z_max - z_min
    return Location((0.0, 0.0, z_min)) * Cone(
        bottom_radius_mm, bottom_radius_mm + height_mm * slope, height_mm, align=None
    )


def _trash_can_model(params: Any, adapter: Any, *, top_mm: float) -> Any:
    """⚠️ **底を抜いたゴミ箱の代用形状**（アダプタの高さ範囲だけ）。

    ⚠️ **アダプタ側の導出値から作らない**——上流の採寸値（底の外径・平面部径・
    テーパー角・肉厚）と、缶の底が載る高さだけで立てる。使うのは
    「缶を据えたまま断片を差し込めるか」（要件 6.6）を実形状で見るためである。

    - **側壁**: 底の外径から `taper_deg` で広がる円錐の殻（肉厚は
      `bottom_thickness_mm`）
    - **縁**: 切り取り径から外径までの環。⚠️ **実物の縁の下面は角の丸みで上へ
      反っている**が、ここでは平らな環として置く——⚠️ 実物より下へ張り出す
      置き方であり、干渉の判定は保守側に倒れる
    """
    from build123d import Location

    can = params.trash_can
    floor_mm = adapter.floor_top_height_mm
    outer_radius_mm = can.bottom_outer_diameter_mm / 2.0
    wall = _cone_probe(
        outer_radius_mm, adapter.seat_slope, (floor_mm, top_mm)
    ) - _cone_probe(
        outer_radius_mm - can.bottom_thickness_mm,
        adapter.seat_slope,
        (floor_mm - _EPS_MM, top_mm + _EPS_MM),
    )
    lip_range = (floor_mm, floor_mm + can.bottom_thickness_mm)
    lip = _full_cylinder(outer_radius_mm, lip_range) - _full_cylinder(
        can.bottom_flat_diameter_mm / 2.0,
        (lip_range[0] - _EPS_MM, lip_range[1] + _EPS_MM),
    )
    return wall + lip


def _seat_radius_mm(adapter: Any, height_mm: float) -> float:
    """受け面の半径（⚠️ **解析値**。実形状との突き合わせの相手として使う）。"""
    return adapter.seat_bottom_radius_mm + (
        height_mm - adapter.floor_top_height_mm
    ) * adapter.seat_slope


def _material_inside_radius_mm3(
    solids: tuple[Any, ...], radius_mm: float, height_mm: float
) -> float:
    """高さ `height_mm` の薄い層で、半径 `radius_mm` の内側にある材料の体積。"""
    slab = (height_mm - _SLAB_MM / 2.0, height_mm + _SLAB_MM / 2.0)
    probe = _full_cylinder(radius_mm, slab)
    return sum(_volume(solid & probe) for solid in solids)


_SLAB_MM = 0.2
"""半径を測るための薄い層の厚さ（mm）。

⚠️ **層の中でも受け面の半径はテーパーぶん動く**（0.2mm の層で 0.02mm 弱）。
`_EPS_MM`（0.05mm）はその変化より大きく、テーパーによる広がり（立ち上がりの
全高で 1.7mm）よりはるかに小さい——両側からの判定が意味を持つ範囲である。
"""


def _radial_boss_region(
    solid: Any, *, angle_deg: float, height_mm: float, radius_mm: float
) -> Any:
    """半径方向のボルトの軸に同軸な円筒で、座の範囲だけを切り出す。

    ⚠️ **生成名を使わない**（`_boss_region` と同じ規律）。違いは軸の向きだけで
    あり、アダプタの締結は半径方向（`print_normal_axis == "x"`）である。
    """
    from build123d import Align, Cylinder, Location, Rotation

    return solid & (
        Rotation(0, 0, angle_deg)
        * Location((0.0, 0.0, height_mm))
        * Rotation(0, 90, 0)
        * Cylinder(radius_mm, _PROBE_MM, align=(Align.CENTER, Align.CENTER, Align.CENTER))
    )


def _measured_radial_seat_area_mm2(
    solids: tuple[Any, ...],
    *,
    angles_deg: tuple[float, ...],
    height_mm: float,
    face_radius_mm: float,
    boss_diameter_mm: float,
) -> float:
    """半径方向の締結の当たり面を、⚠️ **構築したソリッドから**測る。

    ボルト頭が当たるのは座ぐりの底（平面）である。⚠️ **切り出す円筒の半径を座の
    半径ぴったりにしない**——座の境界はその円筒そのものであり、同一面での
    ブール演算に結果を委ねることになる。

    ⚠️ **`joints.BEARING_AREA_FORMULA` を再計算した値を返さない。** 面積は
    OCCT の面から採る。
    """
    total_mm2 = 0.0
    for angle_deg in angles_deg:
        radians = math.radians(angle_deg)
        normal = (math.cos(radians), math.sin(radians), 0.0)
        found = False
        for solid in solids:
            region = _radial_boss_region(
                solid,
                angle_deg=angle_deg,
                height_mm=height_mm,
                radius_mm=boss_diameter_mm / 2.0 + _EPS_MM,
            )
            if _volume(region) == 0.0:
                continue
            faces = _planar_faces_on_plane(region, normal, face_radius_mm)
            total_mm2 += sum(float(face.area) for face in faces)
            found = found or bool(faces)
        assert found, f"角度 {angle_deg} の座に、ボルト頭が当たる平面が無い"
    return total_mm2


@requires_cad
def test_the_adapter_seat_has_no_cylindrical_cross_section(
    shipped: Any, adapter: Any, adapter_parts: tuple[Any, ...]
) -> None:
    """⚠️ **受け面は円錐台の側面に沿い、円筒断面を持たない**（要件 6.2）。

    ⚠️ **面の型だけでは足りない**（型を見るだけでは、円錐が「どちらへ」開いて
    いるかも、実際に受け面がそこにあるかも分からない）。実形状の受け面の半径を
    **2つの高さで両側から**測り、テーパー角ぶん広がっていることを固定する
    ——⚠️ 円筒であれば2つの高さで同じ半径になる。
    """
    from build123d import GeomType

    low_mm = adapter.floor_top_height_mm + 1.0
    high_mm = adapter.rise_top_height_mm - 1.0
    for height_mm in (low_mm, high_mm):
        expected_mm = _seat_radius_mm(adapter, height_mm)
        assert (
            _material_inside_radius_mm3(adapter_parts, expected_mm - _EPS_MM, height_mm)
            == 0.0
        ), height_mm
        assert (
            _material_inside_radius_mm3(adapter_parts, expected_mm + _EPS_MM, height_mm)
            > 0.0
        ), height_mm

    # ⚠️ **円筒であればここが破れる**: 下端の半径は上端では受け面の内側に入る。
    assert (
        _material_inside_radius_mm3(
            adapter_parts, adapter.seat_bottom_radius_mm + _EPS_MM, high_mm
        )
        == 0.0
    )
    assert _seat_radius_mm(adapter, high_mm) - _seat_radius_mm(
        adapter, low_mm
    ) == pytest.approx((high_mm - low_mm) * adapter.seat_slope)

    # 面の型でも同じことを言う: 受け面は円錐であり、⚠️ **受け面の帯に軸対称の
    # 円筒面が1つも無い**（外周面 φ188 と裾の面はこの帯の外にある）。
    # ⚠️ **見るのはゴミ箱の底が載る高さより上だけである**——受け面とは
    # 「掴み面より上で缶の側壁と向き合う面」のことであり、その下にあるのは
    # 縁を下から受ける床と裾（缶の側壁と向き合わない）だからである。
    # ⚠️ **かつての理由（角の丸みの逃げを除く）ではない**——逃げは本タスクで
    # 廃止した（決定 4b）。現在この帯の内側で掴み面より下に落ちる面は
    # **1つも無い**（帯にあるのは受け面の円錐と、軸が半径方向の保持の座ぐりの
    # 円筒だけである）。⚠️ **それでも高さで区切る**: 床側に同じ半径の面が
    # 現れる形（縁の下の溝や丸み）へ変えたとき、それを受け面として判定して
    # しまわないためである。
    band = (adapter.seat_bottom_radius_mm - _EPS_MM, adapter.seat_top_radius_mm + _EPS_MM)
    cones = 0
    for solid in adapter_parts:
        for face in solid.faces():
            point = face.position_at(0.5, 0.5)
            radius_mm = math.hypot(float(point.X), float(point.Y))
            if float(point.Z) < adapter.floor_top_height_mm:
                continue
            if face.geom_type == GeomType.CONE and band[0] <= radius_mm <= band[1]:
                cones += 1
            if face.geom_type != GeomType.CYLINDER:
                continue
            unit = face.normal_at(point)
            axis_is_z = abs(float(unit.Z)) < 1e-6 and abs(
                float(unit.X) * float(point.Y) - float(unit.Y) * float(point.X)
            ) < 1e-6
            if not axis_is_z:
                continue  # ボルト穴・座ぐり（軸は半径方向）は受け面ではない
            assert not (band[0] <= radius_mm <= band[1]), (
                f"受け面の帯に円筒面がある（半径 {radius_mm}mm）"
            )
    assert cones == len(adapter_parts), "断片ごとに受け面の円錐が1つある"


@requires_cad
def test_a_cylindrical_trash_can_still_builds_a_seat(shipped: tuple[Any, Any]) -> None:
    """テーパー 0（円筒形のゴミ箱）でも断片が構築できる。

    ⚠️ **上流はテーパー 0 を許している**（円筒形のゴミ箱を排除しないため）。
    そのとき受け面は円筒になり、⚠️ **円錐としては作れない**（上下の径が等しい
    円錐を形状ライブラリは拒む）。要件 6.2 が禁じているのは「円錐台の底に
    円筒の座を当てること」であって、円筒の底に円筒の座を当てることではない。
    """
    import dataclasses

    params, layout = shipped
    straight = dataclasses.replace(
        params, trash_can=dataclasses.replace(params.trash_can, taper_deg=0.0)
    )
    geometry = adapter_geometry(straight, layout)
    assert geometry.seat_slope == 0.0
    for index in range(geometry.segment_count):
        solid = _build_adapter_segment_for_test(geometry, index)
        assert len(solid.solids()) == 1
        assert float(solid.volume) > 0.0


def _build_adapter_segment_for_test(geometry: Any, index: int) -> Any:
    from chassis_mechanism.shapes import _build_adapter_segment

    return _build_adapter_segment(geometry, index)


@requires_cad
def test_the_seat_confines_the_trash_can_all_around_at_the_designed_clearance(
    shipped: Any, adapter: Any, adapter_parts: tuple[Any, ...]
) -> None:
    """⚠️ **水平方向の拘束は「座が全周を囲んでいること」である**（要件 6.5）。

    断片は円環を等分したものであり、組み上がると閉じた環になる。⚠️ **どこか
    1箇所でも欠ければゴミ箱はそちらへ逃げる**——分割の継ぎ目を含む全周で
    材料があることを見る。隙間は設計値（`adapter.seat_clearance_mm`）に一致する。
    """
    params, _ = shipped
    clearance_mm = params.chassis.adapter.seat_clearance_mm
    can_radius_mm = params.trash_can.bottom_outer_diameter_mm / 2.0
    # ⚠️ 保持の締結の高さを避ける（そこには貫通穴が開いている）。
    height_mm = (adapter.retention_bolt_height_mm + adapter.rise_top_height_mm) / 2.0
    seat_mm = _seat_radius_mm(adapter, height_mm)
    assert seat_mm - (
        can_radius_mm + (height_mm - adapter.floor_top_height_mm) * adapter.seat_slope
    ) == pytest.approx(clearance_mm)

    wall_mid_mm = (seat_mm + adapter.outer_radius_mm) / 2.0
    for step in range(36):
        angle_deg = 5.0 + step * 10.0
        radians = math.radians(angle_deg)
        column = _box(
            (
                wall_mid_mm * math.cos(radians) - 0.2,
                wall_mid_mm * math.cos(radians) + 0.2,
            ),
            (
                wall_mid_mm * math.sin(radians) - 0.2,
                wall_mid_mm * math.sin(radians) + 0.2,
            ),
            (height_mm - _SLAB_MM, height_mm + _SLAB_MM),
        )
        assert sum(_volume(solid & column) for solid in adapter_parts) > 0.0, angle_deg


@requires_cad
def test_the_adapter_never_narrows_the_passage_the_trash_can_offers(
    shipped: Any, adapter: Any, adapter_parts: tuple[Any, ...]
) -> None:
    """⚠️ **アダプタはゴミ箱が提供する通過を狭めない**（要件 6.7）。

    ⚠️ **基準は改訂で変わった。** 底が残っていた頃の通過は「底の内面（Ø177）
    から上へ広がる円錐」であったが、底は抜かれた（決定 4b）。⚠️ **いまの通過は
    「切り取り径（＝寸法パラメータ `adapter.bottom_cut_diameter_mm`。出荷値
    Ø160）から `taper_deg` で広がる円錐」であり、開口の平面（＝縁が載る高さ）
    から始まる**。⚠️ **手切りの余裕を採ったぶん禁止領域はさらに小さくなった
    （＝主張はさらに弱い）**——締まりを与えているのは下半分の反証側である
    ——基準が移った理由は
    「厳しくするため」ではなく、⚠️ **そこが**いま**ゴミ箱が提供している通過
    そのものだから**である。

    ⚠️ **この置き換えで、禁止領域そのものは小さくなった（＝主張は弱くなった）。**
    `85 + (z - 78) * slope` は `88.5 + (z - 79.5) * slope` より、どの高さでも
    半径が **3.372mm 小さい**——上端を含めどの高さでも旧い禁止円錐の内側にある。
    新旧の差として増えるのは z ∈ [78, 79.5] の帯だけであり、⚠️ **そこに
    アダプタは `r < 85` の材料をもともと持たない**（床の上面は z = 78 で終わる）。
    ⚠️ **「開口の基準が Ø210 から Ø170 になったから厳しくなった」と読み替えない**
    ——狭い禁止領域は弱い主張である。

    ⚠️ **締まりを与えているのは下半分の反証側である。** 受け面のすぐ内側
    （`_seat_radius_mm(adapter, floor_top) + _EPS_MM`）へ寄せた円錐は必ず当たる
    ——⚠️ **受け面が 0.05mm でも内側へ動けば落ちる**という側が本体であり、
    それは改訂の前後で変わっていない。

    ⚠️ **その円錐の内側にアダプタの材料が1mm^3 も無い**ことが、通過を狭めない
    ことの形の側の意味である——アダプタは縁の**下**から掴み、側壁を**外から**
    抱えるのであって、開口の内側へは入らない。段（タスク 3.4）はこの円錐を
    通って缶の内側へ立ち上がる。

    ⚠️ **`trash_can.opening_inner_diameter_mm` は受け面の内径の下限ではない**
    （design.md `#### Shapes` の不変条件がそう明記している。本ファイル末尾の
    `test_the_opening_inner_diameter_is_not_a_bound_on_the_seat_bore` を参照）。
    """
    params, _ = shipped
    can = params.trash_can
    opening_mm = adapter.floor_top_height_mm
    assert adapter.cut_radius_mm == pytest.approx(
        params.chassis.adapter.bottom_cut_diameter_mm / 2.0
    )
    assert 2.0 * adapter.cut_radius_mm <= can.bottom_flat_diameter_mm
    passage = _cone_probe(
        adapter.cut_radius_mm,
        adapter.seat_slope,
        (opening_mm, adapter.rise_top_height_mm + _PROBE_MM),
    )
    for index, solid in enumerate(adapter_parts, start=1):
        assert _volume(solid & passage) == 0.0, index

    # ⚠️ 空振りでないこと: 通過の円錐は受け面の帯のすぐ内側にあり、受け面を
    # わずかに内側へ寄せれば必ず当たる。
    intruding = _cone_probe(
        _seat_radius_mm(adapter, opening_mm) + _EPS_MM,
        adapter.seat_slope,
        (opening_mm, adapter.rise_top_height_mm),
    )
    assert sum(_volume(solid & intruding) for solid in adapter_parts) > 0.0


@requires_cad
def test_the_grip_face_reaches_under_the_lip_the_cut_leaves(
    shipped: Any, adapter: Any, adapter_parts: tuple[Any, ...]
) -> None:
    """⚠️ **掴み面が縁の下に実在する**（要件 6.10 / 決定 4b）。

    底を抜いた後に残るのは、切り取り径（＝平面部径 Ø170）から外径 Ø180 までの
    縁だけである。⚠️ **アダプタはその下へ入って掴む**——ここが欠ければ缶は
    受け面のテーパーが噛むまで沈む（隙間 1mm ÷ 勾配 0.085 ＝ 十数 mm）。

    見るのは3つである:

    - 縁の帯 `[cut_radius, lip_outer_radius]` の**すぐ下**に、⚠️ **全周で**材料が
      あること。⚠️ **角度を刻んで突くのではなく環の体積で測る**——欠けた扇が
      1つでもあれば体積が足りなくなり、⚠️ 継ぎ目の抜けも同時に捉えられる
    - その材料の上面が、缶の底が載る高さの**平面**であること（面積で見る）
    - 同じ帯の**すぐ上**には材料が無いこと（そこは縁が占める）
    """
    grip_mm = adapter.floor_top_height_mm
    below = (grip_mm - _SLAB_MM, grip_mm)
    above = (grip_mm, grip_mm + _SLAB_MM)

    def _lip_ring(z_range: tuple[float, float]) -> Any:
        return _full_cylinder(adapter.lip_outer_radius_mm, z_range) - _full_cylinder(
            adapter.cut_radius_mm, (z_range[0] - _EPS_MM, z_range[1] + _EPS_MM)
        )

    expected_mm3 = (
        math.pi
        * (adapter.lip_outer_radius_mm**2 - adapter.cut_radius_mm**2)
        * _SLAB_MM
    )
    measured_mm3 = sum(_volume(solid & _lip_ring(below)) for solid in adapter_parts)
    assert measured_mm3 == pytest.approx(expected_mm3, rel=1e-6)
    assert sum(_volume(solid & _lip_ring(above)) for solid in adapter_parts) == 0.0

    # 掴み面は平面であり、裾の内側から受け面の立ち上がりまで続いて縁の帯を
    # 丸ごと覆う。⚠️ **ただし縁がこの面に載るのは「面」ではなく「円」である**
    # ——平面部径ちょうどで切る以上、残る縁の下面は角の丸みそのものであり、
    # 切り口（r = cut_radius_mm）で平面へ接するだけで、外側へ行くほど浮く。
    # ⚠️ **面で受けていると読み替えない。** 接触円の線荷重は缶 228g だけなら
    # 0.004N/mm、内容物を含めて 1kg を見ても 0.02N/mm 程度であり、PP が局所的に
    # 馴染んで幅を持つ範囲である（要件 6.10 の座面としてはこれで足りる）。
    # ⚠️ 面で当てるには角の丸み半径が要るが、上流 `TrashCanMeasurements` に
    # その項目は無い——⚠️ **発明しない**（要件 1.1 / 1.3）。
    face_area_mm2 = sum(
        float(face.area)
        for solid in adapter_parts
        for face in _planar_faces_on_plane(solid, (0.0, 0.0, 1.0), grip_mm)
    )
    assert face_area_mm2 == pytest.approx(
        math.pi
        * (adapter.seat_bottom_radius_mm**2 - adapter.skirt_inner_radius_mm**2),
        rel=1e-6,
    )


@requires_cad
def test_a_grip_face_that_stops_short_of_the_lip_is_caught(
    shipped: Any, adapter: Any
) -> None:
    """⚠️ **上の検査が空振りでない**——縁へ届かない掴み面は捉えられる。

    掴み面を 2mm 下げた幾何を**測るためだけに**構築する。缶の縁はもとの高さに
    あるため、⚠️ **その下の環は空になる**——検査は「面がそこにある」ことを
    本当に見ている。
    """
    import dataclasses

    from chassis_mechanism.shapes import _build_adapter_segment

    _, _ = shipped
    drop_mm = 2.0
    sunken = dataclasses.replace(
        adapter, floor_top_height_mm=adapter.floor_top_height_mm - drop_mm
    )
    solids = tuple(
        _build_adapter_segment(sunken, index) for index in range(sunken.segment_count)
    )
    ring = _full_cylinder(
        adapter.lip_outer_radius_mm,
        (adapter.floor_top_height_mm - _SLAB_MM, adapter.floor_top_height_mm),
    ) - _full_cylinder(
        adapter.cut_radius_mm,
        (
            adapter.floor_top_height_mm - _SLAB_MM - _EPS_MM,
            adapter.floor_top_height_mm + _EPS_MM,
        ),
    )
    assert sum(_volume(solid & ring) for solid in solids) == 0.0


@requires_cad
def test_the_centre_stays_open_for_the_deck_stack_that_rises_inside_the_can(
    shipped: Any, adapter: Any, adapter_parts: tuple[Any, ...]
) -> None:
    """⚠️ **中央は開いている**（要件 6.7 / 7.10 の前提）。

    底を抜いた意味は、缶の内側を段積み土台の空間として使えることである
    （決定 4b）。⚠️ **アダプタが中央に蓋をしていればその意味が消える。**

    ⚠️ **開いている径は高さで2段に分かれる**——ここを曖昧にしない:

    - 缶の底が載る高さ**より上**では、通過は切り取り径 Ø170 から広がる円錐で
      ある（`test_the_adapter_never_narrows_the_passage_the_trash_can_offers`）
    - **その下**（駆動ベースの上面から缶の底まで）は、アダプタの床が環として
      残るため通過は裾の内径 `skirt_inner_radius_mm` に絞られる。
      ⚠️ **段の柱はこの径の内側（＝中央部の真上）から立ち上げる**——
      タスク 3.4 が読む拘束であり、ここで数として固定する
    """
    _, _ = shipped
    column = _full_cylinder(
        adapter.skirt_inner_radius_mm,
        (adapter.skirt_bottom_height_mm - _PROBE_MM, adapter.rise_top_height_mm + _PROBE_MM),
    )
    for index, solid in enumerate(adapter_parts, start=1):
        assert _volume(solid & column) == 0.0, index

    # ⚠️ 空振りでないこと: わずかに太い柱は裾へ当たる。
    thicker = _full_cylinder(
        adapter.skirt_inner_radius_mm + _EPS_MM,
        (adapter.skirt_bottom_height_mm, adapter.floor_bottom_height_mm),
    )
    assert sum(_volume(solid & thicker) for solid in adapter_parts) > 0.0
    # 2段の関係（⚠️ 下の段のほうが細い。段の柱はそちらに従う）。
    assert adapter.skirt_inner_radius_mm < adapter.cut_radius_mm


@requires_cad
def test_a_segment_can_be_installed_with_the_trash_can_already_in_place(
    shipped: Any, adapter: Any, adapter_parts: tuple[Any, ...], parts: dict[str, Any]
) -> None:
    """⚠️ **缶を据えたまま断片を差し込める**（要件 6.6 / design.md 組立手順 16）。

    掴み面は縁の**下**にあるため、⚠️ **缶を上から落とし込んでも掴み面は通れない**
    ——据え付けの順序は「缶を置き、断片を半径方向に差し込んで留める」である。

    断片は円環の一部であり、⚠️ **占める角度が 180 度以下であれば、二等分線の
    向きへ引き抜くとき断片のどの点も軸から遠ざかる**
    （`|R e^{iθ} + d| >= R` が `|θ| <= 90` 度で成り立つ）。したがって座った位置に
    隙間がある限り経路の全域に隙間がある。⚠️ **論証だけで済ませず、経路上の
    位置で実際に測る。**
    """
    from build123d import Location

    params, _ = shipped
    assert adapter.segment_span_deg <= 180.0, "この向きの引き抜きが成立する条件"
    can = _trash_can_model(params, adapter, top_mm=adapter.rise_top_height_mm + 2.0)
    placed = _placed_drive_base(shipped, parts)

    for index, solid in enumerate(adapter_parts):
        radians = math.radians(
            adapter.segment_start_angles_deg[index] + adapter.segment_span_deg / 2.0
        )
        for offset_mm in (2.0, 6.0, 15.0, 35.0):
            moved = solid.moved(
                Location(
                    (
                        offset_mm * math.cos(radians),
                        offset_mm * math.sin(radians),
                        0.0,
                    )
                )
            )
            assert _volume(moved & can) == 0.0, (index, offset_mm)
            if index:
                continue
            # ⚠️ 先に据えた駆動ベースと、先に留めた隣の断片にも当たらない。
            for name, other in placed.items():
                assert _volume(moved & other) == 0.0, (name, offset_mm)
            for other_index in range(1, len(adapter_parts)):
                assert _volume(moved & adapter_parts[other_index]) == 0.0, (
                    other_index,
                    offset_mm,
                )

    # ⚠️ 空振りでないこと: 同じ断片を 2mm 持ち上げれば掴み面が縁の居場所を奪う。
    lifted = adapter_parts[0].moved(Location((0.0, 0.0, 2.0)))
    assert _volume(lifted & can) > 0.0


@requires_cad
def test_the_mount_bolts_can_be_driven_from_outside_with_the_can_in_place(
    shipped: Any, adapter: Any, adapter_parts: tuple[Any, ...], parts: dict[str, Any]
) -> None:
    """⚠️ **駆動ベースを分解せずに着脱できる**（要件 6.6）。

    取付ボルトは半径方向であり、⚠️ **缶より下の帯を通って外から工具が届く**。
    工具の筋（座の外径ぶんの円筒）に、アダプタ・駆動ベースのいずれの材料も
    無いことを見る。缶が筋に入り得ないことは高さの関係で示す
    ——⚠️ **缶は掴み面より上にしか存在しない。**
    """
    from build123d import Align, Cylinder, Location, Rotation

    _, _ = shipped
    boss_radius_mm = adapter.boss_diameter_mm / 2.0
    # 工具の帯は缶（掴み面より上）にも床（裾の上）にも掛からない。
    assert adapter.mount_bolt_height_mm + boss_radius_mm < adapter.floor_bottom_height_mm
    assert adapter.mount_bolt_height_mm - boss_radius_mm > adapter.skirt_bottom_height_mm

    near_mm = adapter.skirt_outer_radius_mm + _EPS_MM
    far_mm = adapter.outer_radius_mm + 60.0
    band = (
        adapter.mount_bolt_height_mm - boss_radius_mm,
        adapter.mount_bolt_height_mm + boss_radius_mm,
    )
    # アダプタ自身は裾より外側のこの帯に材料を持たない（⚠️ 全周で見る）。
    outside = _full_cylinder(far_mm, band) - _full_cylinder(
        near_mm, (band[0] - _EPS_MM, band[1] + _EPS_MM)
    )
    for index, solid in enumerate(adapter_parts, start=1):
        assert _volume(solid & outside) == 0.0, index

    def _tool_path(angle_deg: float) -> Any:
        return (
            Rotation(0, 0, angle_deg)
            * Location(((near_mm + far_mm) / 2.0, 0.0, adapter.mount_bolt_height_mm))
            * Rotation(0, 90, 0)
            * Cylinder(
                boss_radius_mm,
                far_mm - near_mm,
                align=(Align.CENTER, Align.CENTER, Align.CENTER),
            )
        )

    placed = _placed_drive_base(shipped, parts)
    for angles in adapter.mount_bolt_angles_deg:
        for angle_deg in angles:
            path = _tool_path(angle_deg)
            for name, solid in placed.items():
                assert _volume(path & solid) == 0.0, (angle_deg, name)

    # ⚠️ 空振りでないこと: アームの角度では筋が塞がる（座をそこへ置けない理由）。
    blocked = _tool_path(adapter.arm_angles_deg[0])
    assert sum(_volume(blocked & solid) for solid in placed.values()) > 0.0


@requires_cad
def test_the_adapter_stays_below_the_mouth_where_the_upstream_rim_mounts(
    shipped: Any, adapter: Any, adapter_parts: tuple[Any, ...]
) -> None:
    """⚠️ **上流が設計した受け口と干渉しない**（要件 6.7）。

    受け口（ワイドリム）はゴミ箱の**上端**へ被さる部品であり、上流
    `RimParams.height_mm` がその被さる高さを持つ。アダプタは底を受ける座で
    あり、⚠️ **ゴミ箱の側面をその高さまで登らない**。
    """
    from catch_mechanism import load_params as upstream_load_params

    params, _ = shipped
    can = params.trash_can
    rim = upstream_load_params().rim

    reach_mm = adapter.rise_top_height_mm - adapter.floor_top_height_mm
    assert reach_mm < can.height_mm - rim.height_mm

    # 実形状でも同じことを言う（宣言ではなく構築した断片の高さで見る）。
    for solid in adapter_parts:
        assert float(solid.bounding_box().max.Z) <= adapter.rise_top_height_mm + 1e-6
    # 受け口はゴミ箱の上端の外径へ嵌まる。アダプタの外径はそれより小さい。
    assert adapter.outer_radius_mm * 2.0 < can.top_outer_diameter_mm


@requires_cad
def test_the_measured_adapter_mount_seat_area_matches_the_bearing_area_recorded_by_joints(
    shipped: Any, adapter: Any, adapter_parts: tuple[Any, ...]
) -> None:
    """⚠️ **`hub_plate__adapter_segment_i` の当たり面が実形状で実現している。**

    `joints` は build123d を import できないため当たり面を解析式で持つ
    （design.md `#### Joints` Risks）。⚠️ **この家族は本タスクまで一度も
    実形状と突き合わされていない。** 突き合わせる片側は寸法から計算し直した
    数ではなく、⚠️ **ソリッドから測った面積**である。
    """
    params, layout = shipped
    joints = {spec.name: spec for spec in derive_joints(layout, params)}
    for index in range(1, adapter.segment_count + 1):
        spec = joints[f"hub_plate__adapter_segment_{index}"]
        measured_mm2 = _measured_radial_seat_area_mm2(
            (adapter_parts[index - 1],),
            angles_deg=adapter.mount_bolt_angles_deg[index - 1],
            height_mm=adapter.mount_bolt_height_mm,
            face_radius_mm=adapter.skirt_outer_radius_mm - adapter.mount_spotface_depth_mm,
            boss_diameter_mm=adapter.boss_diameter_mm,
        )
        assert measured_mm2 == pytest.approx(spec.bearing_area_mm2, rel=1e-9), index
        assert measured_mm2 >= spec.min_bearing_area_mm2
        assert measured_mm2 >= params.joint.min_bearing_area_mm2


@requires_cad
def test_the_measured_retention_seat_area_matches_the_bearing_area_recorded_by_joints(
    shipped: Any, adapter: Any, adapter_parts: tuple[Any, ...]
) -> None:
    """⚠️ **`adapter__trash_can` の当たり面が実形状で実現している**（要件 6.5, 2.9）。

    保持の締結は円周へ等配置され、断片をまたぐ。⚠️ **記録は1件の接合部として
    全数のボルトを数えている**ため、突き合わせも全断片を合わせて測る。
    """
    params, layout = shipped
    spec = next(
        joint
        for joint in derive_joints(layout, params)
        if joint.name == "adapter__trash_can"
    )
    measured_mm2 = _measured_radial_seat_area_mm2(
        adapter_parts,
        angles_deg=adapter.retention_bolt_angles_deg,
        height_mm=adapter.retention_bolt_height_mm,
        face_radius_mm=adapter.outer_radius_mm - adapter.retention_spotface_depth_mm,
        boss_diameter_mm=adapter.boss_diameter_mm,
    )
    assert measured_mm2 == pytest.approx(spec.bearing_area_mm2, rel=1e-9)
    assert measured_mm2 >= spec.min_bearing_area_mm2
    assert measured_mm2 >= params.joint.min_bearing_area_mm2


@requires_cad
def test_a_bolt_seat_left_on_the_raw_wall_realises_no_bearing_face(
    shipped: Any, adapter: Any
) -> None:
    """⚠️ **座ぐりが無ければ当たり面は1mm^2 も実現しない**（検査が空振りでない）。

    `joints.BEARING_AREA_FORMULA` が数えるのは**平らな座の環**である。円筒面へ
    直接ボルトを当てれば、頭は2本の線で当たるだけであり、記録された環はどこにも
    無い。⚠️ **測る側が「面の型」を見ているからこそ、この差が出る。**
    """
    import dataclasses

    from chassis_mechanism.shapes import _build_adapter_segment

    _, _ = shipped
    raw = dataclasses.replace(
        adapter, mount_spotface_depth_mm=0.0, retention_spotface_depth_mm=0.0
    )
    solids = tuple(
        _build_adapter_segment(raw, index) for index in range(raw.segment_count)
    )
    for index in range(raw.segment_count):
        region = _radial_boss_region(
            solids[index],
            angle_deg=raw.mount_bolt_angles_deg[index][0],
            height_mm=raw.mount_bolt_height_mm,
            radius_mm=raw.boss_diameter_mm / 2.0 + _EPS_MM,
        )
        normal_at = math.radians(raw.mount_bolt_angles_deg[index][0])
        assert (
            _planar_faces_on_plane(
                region,
                (math.cos(normal_at), math.sin(normal_at), 0.0),
                raw.skirt_outer_radius_mm,
            )
            == []
        )


@requires_cad
def test_a_skirt_shorter_than_the_boss_cannot_realise_the_mount_bearing_area(
    shipped: Any, adapter: Any
) -> None:
    """⚠️ **裾が座の外径より低いと、記録された当たり面は実現しない**（要件 2.9）。

    タスク 3.2 が `base.arm_thickness_mm` について実形状で示したのと同じ形で
    ある。⚠️ 解析式は座の環をまるごと数えるため、⚠️ **面からはみ出しても
    解析値は下がらない**——下限を満たすという判定だけが残る。

    ⚠️ `adapter_geometry` はこの寸法を構築の前に拒否するため、ここでは幾何を
    直接差し替えて**測るためだけに**構築する。
    """
    import dataclasses

    from chassis_mechanism.shapes import _build_adapter_segment

    params, layout = shipped
    spec = next(
        joint
        for joint in derive_joints(layout, params)
        if joint.name == "hub_plate__adapter_segment_1"
    )
    boss_radius_mm = adapter.boss_diameter_mm / 2.0
    short_mm = 1.3 * boss_radius_mm  # 座の環（直径 2r）より低い裾
    shallow = dataclasses.replace(
        adapter,
        skirt_bottom_height_mm=adapter.floor_bottom_height_mm - short_mm,
        mount_bolt_height_mm=adapter.floor_bottom_height_mm - short_mm / 2.0,
    )
    measured_mm2 = _measured_radial_seat_area_mm2(
        (_build_adapter_segment(shallow, 0),),
        angles_deg=shallow.mount_bolt_angles_deg[0],
        height_mm=shallow.mount_bolt_height_mm,
        face_radius_mm=shallow.skirt_outer_radius_mm - shallow.mount_spotface_depth_mm,
        boss_diameter_mm=shallow.boss_diameter_mm,
    )

    # ⚠️ 欠ける量を**形からではなく初等幾何から**独立に出す（弓形の面積）。
    half_mm = short_mm / 2.0
    cap_mm2 = boss_radius_mm**2 * math.acos(half_mm / boss_radius_mm) - half_mm * math.sqrt(
        boss_radius_mm**2 - half_mm**2
    )
    hole_mm2 = math.pi / 4.0 * params.joint.through_hole_diameter_mm**2
    expected_mm2 = shallow.mount_bolt_count * (
        math.pi * boss_radius_mm**2 - cap_mm2 - hole_mm2
    )
    assert measured_mm2 == pytest.approx(expected_mm2, rel=1e-6)
    assert measured_mm2 < spec.bearing_area_mm2
    # ⚠️ **解析値は下限を満たすと述べるが、実物の座は小さい。**
    assert spec.bearing_area_mm2 >= spec.min_bearing_area_mm2


@requires_cad
def test_the_retention_bolt_passes_through_the_wall_and_reaches_the_trash_can(
    shipped: Any, adapter: Any, adapter_parts: tuple[Any, ...]
) -> None:
    """⚠️ **保持の締結は貫通穴であり、ゴミ箱のテーパー面へ届く**（要件 6.5）。

    ⚠️ **袋穴では締結にならない。** 半径方向のボルトが受け面まで抜けていること
    （＝穴の軸に材料が残っていないこと）と、その高さがゴミ箱の底の載る面より
    上にあること（＝底ではなく側面を押さえること）の両方を実形状で見る。
    """
    from build123d import Align, Cylinder, Location, Rotation

    seat_mm = _seat_radius_mm(adapter, adapter.retention_bolt_height_mm)
    inner_mm = seat_mm - _EPS_MM
    outer_mm = adapter.outer_radius_mm + _EPS_MM
    for angle_deg in adapter.retention_bolt_angles_deg:
        bore = (
            Rotation(0, 0, angle_deg)
            * Location((( inner_mm + outer_mm) / 2.0, 0.0, adapter.retention_bolt_height_mm))
            * Rotation(0, 90, 0)
            * Cylinder(
                adapter.through_hole_diameter_mm / 2.0 - _EPS_MM,
                outer_mm - inner_mm,
                align=(Align.CENTER, Align.CENTER, Align.CENTER),
            )
        )
        assert sum(_volume(solid & bore) for solid in adapter_parts) == 0.0, angle_deg
    assert adapter.retention_bolt_height_mm > adapter.floor_top_height_mm


@requires_cad
def test_the_hub_plate_carries_the_insert_bores_the_adapter_bolts_into(
    shipped: Any, adapter: Any, parts: dict[str, Any]
) -> None:
    """⚠️ **`hub_plate__adapter_segment_i` のインサートに座がある。**

    記録は断片1つにつき `insert_count` 個のインサートを数える。⚠️ **相手側に
    座が無ければ、そのインサートはどこにも入らない**——中央部の外縁に、
    上流 `JointPolicy.insert_length_mm` ぶんの深さの座があることを実形状で見る。
    """
    from build123d import Align, Cylinder, Location, Rotation

    params, _ = shipped
    plate = parts["hub_plate"].solid
    hub_radius_mm = params.chassis.base.hub_outer_diameter_mm / 2.0
    depth_mm = params.joint.insert_length_mm

    def _axis_probe(angle_deg: float, near_mm: float, far_mm: float, radius_mm: float) -> Any:
        return (
            Rotation(0, 0, angle_deg)
            * Location(((near_mm + far_mm) / 2.0, 0.0, adapter.mount_bolt_height_mm))
            * Rotation(0, 90, 0)
            * Cylinder(
                radius_mm,
                abs(far_mm - near_mm),
                align=(Align.CENTER, Align.CENTER, Align.CENTER),
            )
        )

    for angles in adapter.mount_bolt_angles_deg:
        for angle_deg in angles:
            # 座の中は空である（深さ ＝ インサート長）。
            empty = _axis_probe(
                angle_deg,
                hub_radius_mm - depth_mm + _EPS_MM,
                hub_radius_mm - _EPS_MM,
                adapter.insert_bore_diameter_mm / 2.0 - _EPS_MM,
            )
            assert _volume(plate & empty) == 0.0, angle_deg
            # ⚠️ **袋穴である**（座の先には材料が残っている）。
            beyond = _axis_probe(
                angle_deg,
                hub_radius_mm - depth_mm - 2.0,
                hub_radius_mm - depth_mm - _EPS_MM,
                adapter.insert_bore_diameter_mm / 2.0 - _EPS_MM,
            )
            assert _volume(plate & beyond) > 0.0, angle_deg


@requires_cad
def test_each_adapter_fragment_is_one_solid_that_fits_the_build_volume(
    shipped: Any, adapter: Any, parts: dict[str, Any]
) -> None:
    """断片が生成され、⚠️ **実測の外接箱**が造形可能寸法に収まる（要件 2.2）。

    ⚠️ **点数は `joints.segment_counts()` が正である**（要件 2.1）。宣言した
    外接箱は実形状の外接箱と一致する——覆いでなければ「収まっている」という
    判定が実物について述べたものにならない。
    """
    from catch_mechanism import Envelope

    from chassis_mechanism.joints import segment_counts

    params, _ = shipped
    counts = segment_counts(params)
    built = [name for name in parts if name.startswith("adapter_segment_")]
    assert len(built) == counts["adapter_segment"] == adapter.segment_count

    for index in range(1, adapter.segment_count + 1):
        part = parts[f"adapter_segment_{index}"]
        assert part.metrics.solid_count == 1, index
        x_mm, y_mm, z_mm = part.metrics.bbox_mm
        assert (
            check_envelope(
                part.name, Envelope(x_mm=x_mm, y_mm=y_mm, z_mm=z_mm), params.printing
            )
            == ()
        ), index
        declared = adapter.segment_envelopes[index - 1]
        for measured, expected in zip(
            (x_mm, y_mm, z_mm), (declared.x_mm, declared.y_mm, declared.z_mm), strict=True
        ):
            assert measured == pytest.approx(expected, abs=1e-6), index


@requires_cad
def test_the_adapter_does_not_interfere_with_the_drive_base(
    shipped: Any, adapter: Any, parts: dict[str, Any]
) -> None:
    """組み上がり状態でアダプタが駆動ベースと干渉しない（要件 9.1）。

    ⚠️ **裾はアームと同じ高さの帯を通る。** 逃げが無ければここが噛む。
    """
    placed = _placed_drive_base(shipped, parts)
    for index in range(1, adapter.segment_count + 1):
        segment = parts[f"adapter_segment_{index}"].solid
        for name, solid in placed.items():
            assert _volume(segment & solid) == 0.0, (index, name)
        for other in range(index + 1, adapter.segment_count + 1):
            assert _volume(
                segment & parts[f"adapter_segment_{other}"].solid
            ) == 0.0, (index, other)

    # ⚠️ 空振りでないこと: 断片は中央部とアームの真上に載っている（高さだけが
    # 隔てている）。
    assert adapter.skirt_inner_radius_mm < adapter.outer_radius_mm
    below = _box(
        (-_PROBE_MM, _PROBE_MM),
        (-_PROBE_MM, _PROBE_MM),
        (adapter.floor_bottom_height_mm - 1.0, adapter.floor_bottom_height_mm),
    )
    assert _volume(placed["hub_plate"] & below) > 0.0


@requires_cad
def test_the_opening_inner_diameter_is_not_a_bound_on_the_seat_bore(
    shipped: Any, adapter: Any, adapter_parts: tuple[Any, ...]
) -> None:
    """⚠️ **`opening_inner_diameter_mm` は座の内径の下限にならない。**

    design.md `#### Shapes` の不変条件がそう明記している——⚠️ **底（φ180）を
    受ける座の内径は、必ず底の外径の側にある**。
    `joints.ADAPTER_OUTER_DIAMETER_FORMULA` が定める**外径**（φ188）ですら
    開口（φ210）より小さく、内径がそれを上回る形は存在しない。

    要件 6.7 の意味は「開口の**数値**より大きい穴を開けること」ではなく、
    ⚠️ **ゴミ箱が提供する通過を狭めないこと**である（それは
    `test_the_adapter_never_narrows_the_passage_the_trash_can_offers` が
    実形状で固定している）。本テストは、⚠️ **開口の数値を下限として持ち込む
    読み替えが再び入り込まないよう**、その関係を実形状の頂点から測って
    固定する。
    """
    params, _ = shipped
    opening_mm = params.trash_can.opening_inner_diameter_mm

    inner_radius_mm = min(
        math.hypot(float(vertex.X), float(vertex.Y))
        for solid in adapter_parts
        for vertex in solid.vertices()
    )
    assert inner_radius_mm == pytest.approx(adapter.skirt_inner_radius_mm, abs=1e-6)
    assert 2.0 * adapter.outer_radius_mm < opening_mm
    assert 2.0 * inner_radius_mm < opening_mm


# ---------------------------------------------------------------------------
# 4. バッテリトレイと段積み土台（タスク 3.4 / 要件 7.1-7.5, 7.10-7.13, 8.3, 8.5）
#
# ⚠️ **段は底を抜いた缶の内側を通る**（design.md 決定 4b）。本節が実形状に対して
# 固定するのは6つである。
#
#   - **缶の側壁と交わらない**こと、かつ段の外形が⚠️ **その高さの缶の内径から
#     来ている**こと（要件 7.11）。⚠️ 缶の代用形状は**上流の採寸値だけ**で立てる
#     ——段の導出値から作れば恒真の検査になる
#   - 最上段が⚠️ **上流が定める緩衝材用の平面の最小径以上の平面**を持つこと
#     （要件 7.12）。⚠️ **底を抜いて失われた平面はここが肩代わりする**
#   - バッテリが⚠️ **全部品の中で最も低い搭載物**であること（要件 7.1）
#   - バッテリが⚠️ **駆動ベースも段も分解せずに**抜けること（要件 7.2）。
#     掃引した体積が他のどの部品とも交わらないことで示す
#   - 放熱の隙間（要件 7.5）が⚠️ **実形状の2面の間に実在する**こと
#   - `joints` が記録した当たり面が、⚠️ **実形状の座で実現している**こと
#     （design.md `#### Joints` Risks。本タスクが足した3家族すべてについて）
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def deck(shipped: tuple[Any, Any]) -> Any:
    params, layout = shipped
    return deck_stack_geometry(params, layout)


@pytest.fixture(scope="module")
def tray(shipped: tuple[Any, Any]) -> Any:
    params, layout = shipped
    return battery_tray_geometry(params, layout)


@pytest.fixture(scope="module")
def catch_deck_solids(deck: Any, parts: dict[str, Any]) -> tuple[Any, ...]:
    """構築済みの受け止めデッキの断片（⚠️ 機体座標。据え付けの回転は要らない）。"""
    return tuple(
        parts[
            CATCH_DECK_PART_NAME
            if deck.catch_segment_count == 1
            else f"{CATCH_DECK_PART_NAME}_{index}"
        ].solid
        for index in range(1, deck.catch_segment_count + 1)
    )


@pytest.fixture(scope="module")
def board_deck_solids(deck: Any, parts: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(
        parts[
            BOARD_DECK_PART_NAME
            if deck.board_segment_count == 1
            else f"{BOARD_DECK_PART_NAME}_{index}"
        ].solid
        for index in range(1, deck.board_segment_count + 1)
    )


def _union(solids: tuple[Any, ...]) -> Any:
    """断片を1つの立体へ合わせる（⚠️ 段としての性質は組み上がりのものである）。"""
    merged = solids[0]
    for solid in solids[1:]:
        merged = merged + solid
    return merged


def _bottomless_can(params: Any, adapter: Any, *, top_mm: float) -> Any:
    """⚠️ **上流の採寸値だけで立てた、底を抜いたゴミ箱の代用形状。**

    使うのは `trash_can` の底の外径・底の平面部径・肉厚・テーパー角と、
    缶の底が載る高さ（アダプタの床の上面）だけである。⚠️ **段の導出値を1つも
    使わない**——使えば「自分の値と自分の値が一致する」恒真の検査になる。

    `_trash_can_model` との違いは高さの範囲だけであり、⚠️ **こちらは段の全高を
    覆う**（段は缶の深いところまで登る）。
    """
    can = params.trash_can
    floor_mm = adapter.floor_top_height_mm
    outer_radius_mm = can.bottom_outer_diameter_mm / 2.0
    slope = math.tan(math.radians(can.taper_deg))
    wall = _cone_probe(outer_radius_mm, slope, (floor_mm, top_mm)) - _cone_probe(
        outer_radius_mm - can.bottom_thickness_mm,
        slope,
        (floor_mm - _EPS_MM, top_mm + _EPS_MM),
    )
    lip_range = (floor_mm, floor_mm + can.bottom_thickness_mm)
    lip = _full_cylinder(outer_radius_mm, lip_range) - _full_cylinder(
        can.bottom_flat_diameter_mm / 2.0,
        (lip_range[0] - _EPS_MM, lip_range[1] + _EPS_MM),
    )
    return wall + lip


@requires_cad
def test_no_deck_intersects_the_side_wall_of_the_can(
    shipped: tuple[Any, Any],
    adapter: Any,
    deck: Any,
    board_deck_solids: tuple[Any, ...],
    catch_deck_solids: tuple[Any, ...],
) -> None:
    """⚠️ **どの段も缶の側壁と交わらない**（要件 7.11）。

    缶の代用形状は**上流の採寸値だけ**で立てる。⚠️ **段の側の値から作らない**
    ——作れば「自分の値と自分の値が一致する」恒真の検査になる。
    """
    params, _ = shipped
    can = _bottomless_can(
        params, adapter, top_mm=deck.catch_plate_top_height_mm + _EPS_MM
    )
    for index, solid in enumerate(board_deck_solids + catch_deck_solids):
        assert _volume(solid & can) == 0.0, index

    # ⚠️ 空振りでないこと: 隙間ぶんだけ太らせた段は側壁へ食い込む。
    for solid, radius_mm, z_range in (
        (
            board_deck_solids[0],
            deck.board_plate_radius_mm + deck.can_clearance_mm + _EPS_MM,
            (deck.board_plate_bottom_height_mm, deck.board_plate_top_height_mm),
        ),
        (
            catch_deck_solids[0],
            deck.catch_plate_radius_mm + deck.can_clearance_mm + _EPS_MM,
            (deck.catch_plate_bottom_height_mm, deck.catch_plate_top_height_mm),
        ),
    ):
        grown = _full_cylinder(radius_mm, z_range)
        assert _volume(grown & can) > 0.0, radius_mm


@requires_cad
def test_each_deck_outline_comes_from_the_can_diameter_at_its_own_height(
    shipped: tuple[Any, Any],
    adapter: Any,
    deck: Any,
    board_deck_solids: tuple[Any, ...],
    catch_deck_solids: tuple[Any, ...],
) -> None:
    """⚠️ **段の外形はその段の下面の高さの缶の内径から来ている**（要件 7.11）。

    缶はテーパーで上へ広がるため、段ごとに使える径が違う。⚠️ **両側から測る**
    ——隙間より内側では材料があり、外側では無い。片側だけでは「ちょうど」を
    言えない。⚠️ 期待値は**上流の採寸値だけ**から独立に組み立てる。
    """
    params, _ = shipped
    can = params.trash_can
    slope = math.tan(math.radians(can.taper_deg))
    inner_at_bottom_mm = can.bottom_outer_diameter_mm / 2.0 - can.bottom_thickness_mm

    for solids, bottom_mm, top_mm in (
        (
            board_deck_solids,
            deck.board_plate_bottom_height_mm,
            deck.board_plate_top_height_mm,
        ),
        (
            catch_deck_solids,
            deck.catch_plate_bottom_height_mm,
            deck.catch_plate_top_height_mm,
        ),
    ):
        expected_mm = (
            inner_at_bottom_mm
            + (bottom_mm - adapter.floor_top_height_mm) * slope
            - params.chassis.board.can_clearance_mm
        )
        probe_height_mm = (bottom_mm + top_mm) / 2.0
        total_mm3 = _material_inside_radius_mm3(solids, _PROBE_MM, probe_height_mm)
        inside_mm3 = _material_inside_radius_mm3(
            solids, expected_mm - _EPS_MM, probe_height_mm
        )
        outside_mm3 = _material_inside_radius_mm3(
            solids, expected_mm + _EPS_MM, probe_height_mm
        )
        assert total_mm3 > 0.0
        # ⚠️ 期待した半径の**外側**には材料が1つも無い。
        assert outside_mm3 == pytest.approx(total_mm3, rel=1e-9), (
            "段が期待した半径より外へ出ている（缶の内径からの導出と食い違う）"
        )
        # ⚠️ **外へ出ていないことだけでは足りない**——細すぎる段もそれを満たす。
        # 期待した半径のすぐ内側には縁の材料があり、そこで欠けが出る。
        assert inside_mm3 < total_mm3, (
            "段が期待した半径に届いていない（縁がその手前で終わっている）"
        )

    # ⚠️ 2つの段の径は等しくない（缶が上へ広がることを検査が実際に見ている）。
    assert deck.catch_plate_radius_mm > deck.board_plate_radius_mm


@requires_cad
def test_the_top_deck_carries_the_flat_the_upstream_liner_needs(
    shipped: tuple[Any, Any], deck: Any, catch_deck_solids: tuple[Any, ...]
) -> None:
    """⚠️ **最上段は上流が定める最小径以上の平面を持つ**（要件 7.12）。

    ⚠️ **底を抜いたことで失われた緩衝材の貼り付け面は、ここが肩代わりする**
    （design.md 決定 4b）。⚠️ **最小径は上流 `retention` が正であり、本 Spec は
    読むだけである**——数を書き写せば、上流が緩衝材の方針を変えたときに黙って
    古い下限を主張し続ける。

    平面であることは3つで示す。⚠️ **面積だけでは足りない**——穴だらけの環でも
    面積は足りうる。

      1. 上面より上に、その円の内側では材料が1つも無い（出っ張りが無い）
      2. 上面のすぐ下は、その円の内側が**隙間なく**材料である（穴が無い）
      3. 上面の高さに、法線が上を向く平面が実在し、面積が下限以上である
    """
    params, _ = shipped
    top_mm = deck.catch_plate_top_height_mm
    flat_radius_mm = params.retention.liner_flat_min_diameter_mm / 2.0
    # ⚠️ 上流から読んでいる（本 Spec の設定ファイルの値ではない）。
    assert flat_radius_mm == deck.liner_flat_min_diameter_mm / 2.0
    assert deck.catch_plate_radius_mm >= flat_radius_mm

    union = _union(catch_deck_solids)
    above = union & _full_cylinder(flat_radius_mm, (top_mm + _EPS_MM, top_mm + _PROBE_MM))
    assert _volume(above) == 0.0, "受け止め面より上に出っ張りがある"

    slab = union & _full_cylinder(flat_radius_mm, (top_mm - _SLAB_MM, top_mm))
    assert _volume(slab) == pytest.approx(
        math.pi * flat_radius_mm**2 * _SLAB_MM, rel=1e-9
    ), "受け止め面の直下に穴がある"

    faces = _planar_faces_on_plane(union, (0.0, 0.0, 1.0), top_mm)
    assert faces, "受け止め面に法線が上を向く平面が無い"
    assert sum(float(face.area) for face in faces) >= math.pi * flat_radius_mm**2


@requires_cad
def test_a_hole_in_the_top_deck_breaks_the_liner_flat(
    shipped: tuple[Any, Any], deck: Any, catch_deck_solids: tuple[Any, ...]
) -> None:
    """⚠️ **上の検査は実際に噛む。** 受け止め面に穴を開けると3つとも落ちる。

    ⚠️ **空振りの検査を「満たしている」と読まないための対である。**
    """
    params, _ = shipped
    top_mm = deck.catch_plate_top_height_mm
    flat_radius_mm = params.retention.liner_flat_min_diameter_mm / 2.0
    hole_radius_mm = flat_radius_mm / 4.0

    holed = _union(catch_deck_solids) - _full_cylinder(
        hole_radius_mm, (top_mm - _PROBE_MM, top_mm + _PROBE_MM)
    )
    slab = holed & _full_cylinder(flat_radius_mm, (top_mm - _SLAB_MM, top_mm))
    assert _volume(slab) < math.pi * flat_radius_mm**2 * _SLAB_MM
    faces = _planar_faces_on_plane(holed, (0.0, 0.0, 1.0), top_mm)
    assert sum(float(face.area) for face in faces) < math.pi * deck.catch_plate_radius_mm**2


@requires_cad
def test_the_battery_is_the_lowest_mounted_item_on_the_machine(
    shipped: tuple[Any, Any], tray: Any, parts: dict[str, Any]
) -> None:
    """⚠️ **バッテリは機体の最下部にある**（要件 7.1）。

    ⚠️ **`build_parts` が返すすべてを見る。** 一部だけを見れば、後から足した
    部品がバッテリより下へ潜っても気付けない。

    ⚠️ **整備スタンドの脚だけは対象外である**——脚は機体が**載る**台であり、
    機体が担ぐ搭載物ではない（要件 5.3）。⚠️ しかも脚は**脚の局所座標**で
    構築されており（`shapes` モジュール docstring）、機体座標の高さを比べる
    こと自体に意味が無い。除外した名前の集合を**その場で固定する**——
    黙って増えれば、除外が抜け道になる。
    """
    _, _ = shipped
    excluded = {name for name in parts if name.startswith(f"{SERVICE_STAND_PART_NAME}_")}
    assert excluded == {f"{SERVICE_STAND_PART_NAME}_{index}" for index in (1, 2, 3)}

    machine = {name: part for name, part in parts.items() if name not in excluded}
    assert BATTERY_TRAY_PART_NAME in machine
    tray_bottom_mm = float(
        machine[BATTERY_TRAY_PART_NAME].solid.bounding_box().min.Z
    )
    assert tray_bottom_mm == pytest.approx(tray.floor_bottom_height_mm, abs=1e-6)

    for name, part in machine.items():
        if name == BATTERY_TRAY_PART_NAME:
            continue
        other_bottom_mm = float(part.solid.bounding_box().min.Z)
        assert tray_bottom_mm < other_bottom_mm, name
        # ⚠️ **バッテリそのもの**（トレイではなく中身）も他のどの部品より低い。
        assert tray.battery_bottom_height_mm < other_bottom_mm, name

    # ⚠️ 段へ上げていない（決定 4b が禁じた形をここで固定する）。
    assert tray.battery_top_height_mm < min(
        float(part.solid.bounding_box().min.Z)
        for name, part in machine.items()
        if name.startswith(BOARD_DECK_PART_NAME) or name.startswith(CATCH_DECK_PART_NAME)
    )


def _battery_box(tray: Any, *, lift_mm: float, reach_mm: float | None) -> Any:
    """バッテリの外形（`reach_mm` を与えると引き抜く向きへ掃引した体積）。

    ⚠️ **トレイの形から作らない。** 使うのはバッテリの寸法と座った高さだけで
    あり、トレイの壁・縁・腕はどれも入らない。
    """
    from build123d import Rotation

    near_mm = -tray.pocket_half_length_mm if reach_mm is None else -reach_mm
    return Rotation(0, 0, tray.arm_angle_deg) * _box(
        (near_mm, tray.pocket_half_length_mm),
        (-tray.pocket_half_width_mm, tray.pocket_half_width_mm),
        (
            tray.battery_bottom_height_mm + lift_mm,
            tray.battery_top_height_mm + lift_mm,
        ),
    )


@requires_cad
def test_the_battery_comes_out_without_taking_anything_apart(
    shipped: tuple[Any, Any], tray: Any, parts: dict[str, Any]
) -> None:
    """⚠️ **駆動ベースも段も分解せずにバッテリを外せる**（要件 7.2）。

    ⚠️ **経路を掃引した体積で示す。** 「向きが空いている」という論証では、
    抜けている途中で当たる形を見落とす。掃引は「座った位置」と「抜け止めの縁を
    越えて引き抜く帯」の和であり、⚠️ **他のどの部品とも交わってはならない**。

    ⚠️ **整備スタンドの脚は対象外**（`test_the_battery_is_the_lowest_...` と
    同じ理由——脚は機体の部品ではなく、脚の局所座標で構築されている）。
    """
    _, _ = shipped
    sweep = _battery_box(tray, lift_mm=0.0, reach_mm=None) + _battery_box(
        tray, lift_mm=tray.lift_height_mm, reach_mm=_PROBE_MM
    )
    for name, part in parts.items():
        if name.startswith(f"{SERVICE_STAND_PART_NAME}_"):
            continue
        assert _volume(sweep & part.solid) == 0.0, name

    # ⚠️ 空振りでないこと: 持ち上げずに引けば抜け止めの縁が止める（要件 7.3）。
    blocked = _battery_box(tray, lift_mm=0.0, reach_mm=_PROBE_MM)
    assert _volume(blocked & parts[BATTERY_TRAY_PART_NAME].solid) > 0.0


@requires_cad
def test_the_tray_holds_a_place_for_the_main_fuse_beside_the_battery(
    shipped: tuple[Any, Any], tray: Any, parts: dict[str, Any]
) -> None:
    """⚠️ **主ヒューズをバッテリ直近へ置ける保持箇所がある**（要件 8.5）。

    置き場は**バッテリのポケットと壁を共有する**位置にあり、⚠️ その内側は
    空である（材料で埋まっていれば置き場ではない）。
    """
    from build123d import Rotation

    params, _ = shipped
    battery = params.chassis.battery
    solid = parts[BATTERY_TRAY_PART_NAME].solid
    bay = Rotation(0, 0, tray.arm_angle_deg) * _box(
        (-tray.fuse_bay_half_length_mm, tray.fuse_bay_half_length_mm),
        (tray.fuse_bay_inner_y_mm, tray.fuse_bay_outer_y_mm),
        (tray.battery_bottom_height_mm, tray.fuse_bay_top_height_mm),
    )
    assert _volume(solid & bay) == 0.0, "ヒューズホルダの置き場が材料で埋まっている"
    assert tray.fuse_bay_outer_y_mm - tray.fuse_bay_inner_y_mm == pytest.approx(
        battery.fuse_holder_width_mm, abs=1e-9
    )
    assert _BOTH_SIDES_MM * tray.fuse_bay_half_length_mm == pytest.approx(
        battery.fuse_holder_length_mm, abs=1e-9
    )
    # ⚠️ **バッテリ直近である**——ポケットの外壁1枚を隔てているだけ。
    assert tray.fuse_bay_inner_y_mm == pytest.approx(
        tray.outer_half_width_mm, abs=1e-9
    )
    # ⚠️ 空振りでないこと: 置き場のまわりには材料がある（空中に浮いていない）。
    around = Rotation(0, 0, tray.arm_angle_deg) * _box(
        (-tray.fuse_bay_wall_x_mm, tray.fuse_bay_wall_x_mm),
        (tray.fuse_bay_outer_y_mm, tray.fuse_bay_wall_y_mm),
        (tray.battery_bottom_height_mm, tray.fuse_bay_top_height_mm),
    )
    assert _volume(solid & around) > 0.0


@requires_cad
def test_the_cooling_gap_is_real_material_free_space_above_the_boards(
    shipped: tuple[Any, Any],
    deck: Any,
    board_deck_solids: tuple[Any, ...],
    catch_deck_solids: tuple[Any, ...],
) -> None:
    """⚠️ **放熱の隙間が実形状の2面の間に実在する**（要件 7.5）。

    段の間の空きのうち、⚠️ **部品の頭より上の帯**が空気の道である。その帯には
    どちらの段の材料も無く、⚠️ **厚みは `board.cooling_gap_mm` ちょうど**である。
    """
    params, _ = shipped
    # ⚠️ **帯の上端は板の下面ではなく筒の下端である**——筒は板から重ね代ぶん
    # 下へ垂れており、板で測れば空気の道を実際より広く述べることになる。
    gap_range = (deck.component_top_height_mm, deck.catch_tube_bottom_height_mm)
    assert gap_range[1] - gap_range[0] == pytest.approx(
        params.chassis.board.cooling_gap_mm, abs=1e-9
    )
    # ⚠️ **立ち上がりの肉だけを除く。** 立ち上がりの内側も取付面であり、
    # ⚠️ そこを最初から除くと、そこへ垂れてくる筒を見落とす（一度見落とした）。
    band = _usable_region(deck, gap_range)
    for solid in board_deck_solids + catch_deck_solids:
        assert _volume(solid & band) == 0.0

    # ⚠️ 空振りでないこと: 帯を上へ伸ばせば受け止めデッキの筒に当たる。
    wider = _usable_region(
        deck, (gap_range[0], gap_range[1] + deck.collar_length_mm)
    )
    assert sum(_volume(solid & wider) for solid in catch_deck_solids) > 0.0


@requires_cad
def test_the_board_deck_carries_a_mounting_point_for_every_board(
    shipped: tuple[Any, Any], deck: Any, board_deck_solids: tuple[Any, ...]
) -> None:
    """⚠️ **モータドライバ3台・制御基板・電圧監視・5V 生成の取付箇所がある**
    （要件 7.4）。

    ⚠️ **座は袋穴である**——板を貫けば座が残らず、インサートが抜ける。
    """
    params, _ = shipped
    board = params.chassis.board
    assert deck.module_count == board.driver_count + 3
    assert len(deck.mount_boss_angles_deg) == _BOTH_SIDES_MM * deck.module_count

    top_mm = deck.board_plate_top_height_mm
    bottom_mm = deck.board_plate_bottom_height_mm
    for angle_deg in deck.mount_boss_angles_deg:
        radians = math.radians(angle_deg)
        centre = (
            deck.mount_circle_radius_mm * math.cos(radians),
            deck.mount_circle_radius_mm * math.sin(radians),
        )
        # 座の中は空である（穴が開いている）。
        bore = _column(*centre) & _full_cylinder(
            _PROBE_MM, (top_mm - deck.insert_bore_depth_mm + _EPS_MM, top_mm)
        )
        assert (
            sum(_volume(solid & bore) for solid in board_deck_solids) == 0.0
        ), angle_deg
        # ⚠️ **袋穴である**——座の底より下には材料が残っている。
        under = _column(*centre) & _full_cylinder(
            _PROBE_MM,
            (bottom_mm, top_mm - deck.insert_bore_depth_mm - _EPS_MM),
        )
        assert sum(_volume(solid & under) for solid in board_deck_solids) > 0.0, angle_deg


@requires_cad
def test_the_deck_stack_rises_through_the_opening_the_adapter_leaves(
    shipped: tuple[Any, Any],
    adapter: Any,
    deck: Any,
    board_deck_solids: tuple[Any, ...],
    adapter_parts: tuple[Any, ...],
) -> None:
    """⚠️ **段は中央部の真上から立ち上がり、アダプタの床の内縁に掴まれる**
    （要件 7.10 / `joints.DECK_SEAT_BEARING_AREA_FORMULA`）。

    立ち上がりの外径は⚠️ **中央部の外径そのもの**であり、アダプタが中央部を
    掴むのと同じ嵌め合い隙間で段が掴まれる。
    """
    params, _ = shipped
    assert deck.riser_outer_radius_mm == pytest.approx(
        params.chassis.base.hub_outer_diameter_mm / 2.0, abs=1e-9
    )
    assert deck.riser_bottom_height_mm == pytest.approx(
        adapter.floor_bottom_height_mm, abs=1e-9
    )
    # 段はアダプタと触れ合わない（嵌め合い隙間ぶん離れている）。
    for solid in board_deck_solids:
        for other in adapter_parts:
            assert _volume(solid & other) == 0.0

    # ⚠️ 空振りでないこと: 隙間ぶん太らせた立ち上がりはアダプタの床へ当たる。
    grown = _full_cylinder(
        adapter.skirt_inner_radius_mm + _EPS_MM,
        (adapter.floor_bottom_height_mm, adapter.floor_top_height_mm),
    )
    assert sum(_volume(grown & other) for other in adapter_parts) > 0.0


@requires_cad
def test_no_two_assembled_parts_interfere(
    shipped: tuple[Any, Any], parts: dict[str, Any]
) -> None:
    """組み上がり状態でどの2部品も干渉しない（要件 9.1）。

    ⚠️ **アームは据え付けの角度へ回してから比べる**——`build_drive_base` は
    3本に**同一のソリッド**を返すため、回さずに比べると自分自身と重なる。
    ⚠️ 整備スタンドの脚は機体の部品ではなく、脚の局所座標で構築されている
    （`test_the_battery_is_the_lowest_...` と同じ理由で対象外）。
    """
    import itertools

    placed = dict(_placed_drive_base(shipped, parts))
    placed.update(_placed_cable_guides(shipped, parts))
    for name, part in parts.items():
        if name.startswith(f"{SERVICE_STAND_PART_NAME}_") or name in placed:
            continue
        placed[name] = part.solid

    for left, right in itertools.combinations(sorted(placed), 2):
        assert _volume(placed[left] & placed[right]) == 0.0, (left, right)

    # ⚠️ 空振りでないこと: 段を缶の底の高さまで下げれば、アダプタの床と当たる。
    lowered = placed[BOARD_DECK_PART_NAME].moved(
        __import__("build123d").Location((0.0, 0.0, -20.0))
    )
    assert _volume(lowered & placed["adapter_segment_1"]) > 0.0


@requires_cad
def test_each_new_fragment_fits_the_build_volume_and_matches_its_declared_envelope(
    shipped: tuple[Any, Any], deck: Any, tray: Any, parts: dict[str, Any]
) -> None:
    """段とトレイの外接箱が造形可能寸法に収まり、宣言と一致する（要件 2.2, 7.13）。

    ⚠️ **点数は `joints.segment_counts()` が正である**（要件 2.1）。ここで数え
    直さない。⚠️ **宣言した外接箱と実形状が一致すること**が、「収まっている」と
    いう判定が実物について述べたものである条件である。
    """
    from catch_mechanism import Envelope

    from chassis_mechanism.joints import segment_counts

    params, _ = shipped
    counts = segment_counts(params)
    assert deck.board_segment_count == counts[BOARD_DECK_PART_NAME]
    assert deck.catch_segment_count == counts[CATCH_DECK_PART_NAME]
    for base_name in (BATTERY_TRAY_PART_NAME, BOARD_DECK_PART_NAME, CATCH_DECK_PART_NAME):
        built = [
            name
            for name in parts
            if name == base_name or name.startswith(f"{base_name}_")
        ]
        assert len(built) == counts[base_name], base_name

    declared = {
        BATTERY_TRAY_PART_NAME: tray.envelope,
        **{
            (
                BOARD_DECK_PART_NAME
                if deck.board_segment_count == 1
                else f"{BOARD_DECK_PART_NAME}_{index + 1}"
            ): envelope
            for index, envelope in enumerate(deck.board_envelopes)
        },
        **{
            (
                CATCH_DECK_PART_NAME
                if deck.catch_segment_count == 1
                else f"{CATCH_DECK_PART_NAME}_{index + 1}"
            ): envelope
            for index, envelope in enumerate(deck.catch_envelopes)
        },
    }
    for name, envelope in declared.items():
        measured = parts[name].metrics.bbox_mm
        for value, expected in zip(
            measured, (envelope.x_mm, envelope.y_mm, envelope.z_mm), strict=True
        ):
            assert value == pytest.approx(expected, abs=1e-6), name
        assert (
            check_envelope(
                name,
                Envelope(x_mm=measured[0], y_mm=measured[1], z_mm=measured[2]),
                params.printing,
            )
            == ()
        ), name
        assert parts[name].metrics.solid_count == 1, name


# ---------------------------------------------------------------------------
# 4b. 記録された当たり面が実形状で実現している（design.md `#### Joints` Risks）
#
# ⚠️ **本タスクが足した3家族すべてについて測る。** 解析式が実形状から離れて
# いれば、「下限を満たす」という判定は形について何も言っていない。
# ---------------------------------------------------------------------------


def _tangential_boss_region(
    solid: Any, *, angle_deg: float, radius_mm: float, height_mm: float, probe_mm: float
) -> Any:
    """接線方向のボルトの軸に同軸な円筒で、座の範囲だけを切り出す。

    ⚠️ **生成名を使わない**（`_boss_region` と同じ規律）。違いは、据え付けの
    角度へ回してから切り出すことだけである。
    """
    from build123d import Align, Cylinder, Location, Rotation

    return solid & (
        Rotation(0, 0, angle_deg)
        * Location((radius_mm, 0.0, height_mm))
        * Rotation(90, 0, 0)
        * Cylinder(probe_mm, _PROBE_MM, align=(Align.CENTER, Align.CENTER, Align.CENTER))
    )


def _measured_tray_seat_area_mm2(solid: Any, tray: Any) -> float:
    """トレイの耳の当たり面を、⚠️ **構築したソリッドから**測る。

    ⚠️ **測る面はボルト頭が当たる側**（`-y` の耳の外面）である。向こう側は
    ナットが当たる面であり、両者を足すと二重に数える。
    """
    radians = math.radians(tray.arm_angle_deg)
    normal = (math.sin(radians), -math.cos(radians), 0.0)
    total_mm2 = 0.0
    for radius_mm in tray.bolt_radii_mm:
        region = _tangential_boss_region(
            solid,
            angle_deg=tray.arm_angle_deg,
            radius_mm=radius_mm,
            height_mm=tray.bolt_height_mm,
            probe_mm=tray.boss_diameter_mm / 2.0,
        )
        faces = _planar_faces_on_plane(region, normal, tray.web_outer_y_mm)
        assert faces, f"半径 {radius_mm}mm の座に、ボルト頭が当たる平面が無い"
        total_mm2 += sum(float(face.area) for face in faces)
    return total_mm2


@requires_cad
def test_the_measured_tray_seat_area_matches_the_bearing_area_recorded_by_joints(
    shipped: tuple[Any, Any], tray: Any, parts: dict[str, Any]
) -> None:
    """⚠️ 記録された当たり面が、構築したトレイの耳の実面積と一致する（要件 2.9）。"""
    params, layout = shipped
    joint = next(
        spec
        for spec in derive_joints(layout, params)
        if spec.name == f"motor_arm_{tray.arm_index}__battery_tray"
    )
    measured_mm2 = _measured_tray_seat_area_mm2(
        parts[BATTERY_TRAY_PART_NAME].solid, tray
    )
    assert measured_mm2 == pytest.approx(joint.bearing_area_mm2, rel=1e-9)
    assert measured_mm2 >= joint.min_bearing_area_mm2
    assert measured_mm2 >= params.joint.min_bearing_area_mm2
    # ⚠️ **ナットで受ける**（インサートの居場所が無い）。
    assert joint.insert_count == 0
    assert joint.bolt_count > 0


@requires_cad
def test_an_ear_thinner_than_the_boss_cannot_realise_the_tray_bearing_area(
    shipped: tuple[Any, Any], tray: Any, parts: dict[str, Any]
) -> None:
    """⚠️ **この検査は実際に噛む。** 耳が座の外径より低ければ環は載りきらない。

    ⚠️ `battery_tray_geometry` は座の外径を下回るアームの厚さを構築前に拒否
    するため、薄い耳は「作れない」——それでも⚠️ **測る手口が薄さを見抜くこと**は
    示せなければならない。ここでは構築済みのソリッドを帯へ切り取り、
    **測るためだけに**薄い耳を作る。
    """
    _, _ = shipped
    thin_mm = tray.boss_diameter_mm / 2.0
    assert thin_mm < tray.boss_diameter_mm
    thinned = parts[BATTERY_TRAY_PART_NAME].solid & _box(
        (-_PROBE_MM, _PROBE_MM),
        (-_PROBE_MM, _PROBE_MM),
        (tray.bolt_height_mm - thin_mm / 2.0, tray.bolt_height_mm + thin_mm / 2.0),
    )
    measured_mm2 = _measured_tray_seat_area_mm2(thinned, tray)
    full_mm2 = (
        tray.bolt_count
        * math.pi
        / 4.0
        * (tray.boss_diameter_mm**2 - tray.through_hole_diameter_mm**2)
    )
    assert measured_mm2 < full_mm2
    assert measured_mm2 > 0.0


def _cylindrical_faces_at_radius(solid: Any, radius_mm: float) -> list[Any]:
    """機体の軸に同軸で、半径が `radius_mm` の円筒面を選ぶ。

    ⚠️ **生成名を使わない**（`_planar_faces_on_plane` と同じ規律）。条件は
    「円筒であること」と「面の中心が軸からその距離にあること」だけである。
    """
    from build123d import GeomType

    selected: list[Any] = []
    for face in solid.faces():
        if face.geom_type != GeomType.CYLINDER:
            continue
        centre = face.center()
        if abs(math.hypot(float(centre.X), float(centre.Y)) - radius_mm) > 1e-6:
            continue
        selected.append(face)
    return selected


def _measured_deck_seat_area_mm2(
    solids: tuple[Any, ...], adapter: Any, deck: Any
) -> float:
    """段がアダプタの床に掴まれる帯の面積を、⚠️ **構築したソリッドから**測る。

    ⚠️ **`joints.DECK_SEAT_BEARING_AREA_FORMULA` を再計算した値を返さない。**
    面積は OCCT の面から採る。
    """
    band = _full_cylinder(
        _PROBE_MM,
        (adapter.floor_bottom_height_mm, adapter.floor_top_height_mm),
    )
    total_mm2 = 0.0
    for solid in solids:
        region = solid & band
        if _volume(region) == 0.0:
            continue
        total_mm2 += sum(
            float(face.area)
            for face in _cylindrical_faces_at_radius(region, deck.riser_outer_radius_mm)
        )
    return total_mm2


@requires_cad
def test_the_measured_deck_seat_band_matches_the_bearing_area_recorded_by_joints(
    shipped: tuple[Any, Any],
    adapter: Any,
    deck: Any,
    board_deck_solids: tuple[Any, ...],
) -> None:
    """⚠️ 段↔アダプタの拘束の当たり面が、実形状の円筒帯と一致する（要件 2.9）。

    ⚠️ **締結部品を持たない拘束である**（整備スタンドの谷と同じ分類）。数える
    のはボルト座の環ではなく、立ち上がりの外周がアダプタの床の厚さぶん掴まれて
    いる帯である。
    """
    params, layout = shipped
    joint = next(
        spec
        for spec in derive_joints(layout, params)
        if spec.name == DECK_SEAT_JOINT_NAME
    )
    measured_mm2 = _measured_deck_seat_area_mm2(board_deck_solids, adapter, deck)
    assert measured_mm2 == pytest.approx(joint.bearing_area_mm2, rel=1e-9)
    assert measured_mm2 >= joint.min_bearing_area_mm2
    assert joint.bolt_count == 0
    assert joint.insert_count == 0


@requires_cad
def test_a_riser_that_misses_the_adapter_floor_realises_no_seat_band(
    shipped: tuple[Any, Any], adapter: Any, deck: Any, board_deck_solids: tuple[Any, ...]
) -> None:
    """⚠️ **この検査は実際に噛む。** 立ち上がりが細ければ帯は消える。

    ⚠️ 掴む面は「その半径にある円筒面」であり、半径が変われば**面積が減るのでは
    なく無くなる**——`deck_stack_geometry` が嵌め合い隙間との一致を構築前に
    拒否するのはそのためである（面積の検査だけでは、段が中心を失ったことを
    「面積が足りない」としか言えない）。
    """
    _, _ = shipped
    shrunk = tuple(
        solid
        - _full_cylinder(
            deck.riser_outer_radius_mm,
            (deck.riser_bottom_height_mm - _EPS_MM, deck.riser_top_height_mm + _EPS_MM),
        )
        + _full_cylinder(
            deck.riser_outer_radius_mm - 1.0,
            (deck.riser_bottom_height_mm, deck.riser_top_height_mm),
        )
        for solid in board_deck_solids
    )
    assert _measured_deck_seat_area_mm2(shrunk, adapter, deck) == 0.0


@requires_cad
def test_the_measured_deck_to_deck_seat_area_matches_the_bearing_area_recorded_by_joints(
    shipped: tuple[Any, Any], deck: Any, board_deck_solids: tuple[Any, ...]
) -> None:
    """⚠️ 段どうしの締結の当たり面が、実形状の座ぐりの実面積と一致する（要件 2.9）。

    ⚠️ **測るのは基板デッキ側**（ボルト頭が当たる座ぐりの底）である。相手側は
    インサート座であり、面積は一致しない。
    """
    params, layout = shipped
    joints = {spec.name: spec for spec in derive_joints(layout, params)}
    for index, angles_deg in enumerate(deck.deck_bolt_angles_deg, start=1):
        name = f"{BOARD_DECK_PART_NAME}__{CATCH_DECK_PART_NAME}_{index}"
        joint = joints[name]
        measured_mm2 = _measured_radial_seat_area_mm2(
            board_deck_solids,
            angles_deg=angles_deg,
            height_mm=deck.deck_bolt_height_mm,
            face_radius_mm=(
                deck.riser_outer_radius_mm - deck.deck_spotface_depth_mm
            ),
            boss_diameter_mm=deck.boss_diameter_mm,
        )
        assert measured_mm2 == pytest.approx(joint.bearing_area_mm2, rel=1e-9), name
        assert measured_mm2 >= joint.min_bearing_area_mm2
        assert measured_mm2 >= params.joint.min_bearing_area_mm2


@requires_cad
def test_a_deck_bolt_seat_left_on_the_raw_wall_realises_no_bearing_face(
    shipped: tuple[Any, Any], deck: Any, catch_deck_solids: tuple[Any, ...]
) -> None:
    """⚠️ **この検査は実際に噛む。** 座ぐりを削らなければ平面は実現しない。

    受け止めデッキの筒には座ぐりが無い（インサート座だけである）。⚠️ **同じ
    手口でその面を測ると、ボルト頭が当たる平面は1つも出てこない**——立ち上がり
    側で面が出るのは、座ぐりを実際に削っているからである。
    """
    _, _ = shipped
    for index, angles_deg in enumerate(deck.deck_bolt_angles_deg):
        for angle_deg in angles_deg:
            radians = math.radians(angle_deg)
            normal = (math.cos(radians), math.sin(radians), 0.0)
            region = _radial_boss_region(
                catch_deck_solids[index],
                angle_deg=angle_deg,
                height_mm=deck.deck_bolt_height_mm,
                radius_mm=deck.boss_diameter_mm / 2.0 + _EPS_MM,
            )
            assert _volume(region) > 0.0, "筒に材料が無い（検査が空振りしている）"
            assert (
                _planar_faces_on_plane(
                    region, normal, deck.catch_tube_outer_radius_mm
                )
                == []
            )


@requires_cad
def test_the_deck_bolt_reaches_an_insert_seat_in_the_catch_deck_tube(
    shipped: tuple[Any, Any], deck: Any, catch_deck_solids: tuple[Any, ...]
) -> None:
    """⚠️ **記録されたインサートは実形状の袋穴へ入る**（要件 2.6）。

    ⚠️ **袋穴である**——筒を突き抜けていれば、インサートは反対側へ抜ける。
    """
    _, _ = shipped
    for index, angles_deg in enumerate(deck.deck_bolt_angles_deg):
        for angle_deg in angles_deg:
            seat = _radial_bore_probe(
                angle_deg=angle_deg,
                height_mm=deck.deck_bolt_height_mm,
                radius_range_mm=(
                    deck.catch_tube_outer_radius_mm - deck.insert_bore_depth_mm + _EPS_MM,
                    deck.catch_tube_outer_radius_mm - _EPS_MM,
                ),
                diameter_mm=deck.insert_bore_diameter_mm - _EPS_MM,
            )
            assert _volume(catch_deck_solids[index] & seat) == 0.0, angle_deg
            beyond = _radial_bore_probe(
                angle_deg=angle_deg,
                height_mm=deck.deck_bolt_height_mm,
                radius_range_mm=(
                    deck.catch_tube_inner_radius_mm + _EPS_MM,
                    deck.catch_tube_outer_radius_mm - deck.insert_bore_depth_mm - _EPS_MM,
                ),
                diameter_mm=deck.insert_bore_diameter_mm - _EPS_MM,
            )
            assert _volume(catch_deck_solids[index] & beyond) > 0.0, angle_deg


def _radial_bore_probe(
    *,
    angle_deg: float,
    height_mm: float,
    radius_range_mm: tuple[float, float],
    diameter_mm: float,
) -> Any:
    """半径方向の穴の**中身**を表すプローブ（材料が無いはずの体積）。"""
    from build123d import Align, Cylinder, Location, Rotation

    near_mm, far_mm = radius_range_mm
    return (
        Rotation(0, 0, angle_deg)
        * Location(((near_mm + far_mm) / 2.0, 0.0, height_mm))
        * Rotation(0, 90, 0)
        * Cylinder(
            diameter_mm / 2.0,
            abs(far_mm - near_mm),
            align=(Align.CENTER, Align.CENTER, Align.CENTER),
        )
    )


@requires_cad
def test_the_arm_carries_the_through_bore_the_tray_bolts_into(
    shipped: tuple[Any, Any], tray: Any, parts: dict[str, Any]
) -> None:
    """⚠️ **記録された締結の相手側の穴がアームに実在する**（要件 2.10）。

    ⚠️ **穴が無ければ、記録された締結はどこも通らない**（中央部がアダプタの
    インサート座を持つのと同じ理由）。⚠️ **3本すべてに開いている**——1本だけに
    開けるとアームが別部品になり、取り違えても気付けない。
    """
    _, _ = shipped
    for index in (1, 2, 3):
        arm = parts[f"motor_arm_{index}"].solid
        for radius_mm in tray.bolt_radii_mm:
            bore = _radial_bore_probe(
                angle_deg=0.0,
                height_mm=tray.bolt_height_mm,
                radius_range_mm=(radius_mm - _EPS_MM, radius_mm + _EPS_MM),
                diameter_mm=tray.through_hole_diameter_mm - _EPS_MM,
            )
            # ⚠️ **空振りでないこと**: 穴のすぐ脇（同じ半径で、穴の径の
            # 外の高さ）にはアームの材料が残っている。穴の中だけを見て
            # 「材料が無い」と言っても、⚠️ そもそもアームがそこに無い場合と
            # 区別できない。
            beside = _box(
                (radius_mm - _EPS_MM, radius_mm + _EPS_MM),
                (-_PROBE_MM, _PROBE_MM),
                (
                    tray.bolt_height_mm + tray.through_hole_diameter_mm,
                    tray.bolt_height_mm + tray.through_hole_diameter_mm + _EPS_MM,
                ),
            )
            assert _volume(arm & bore) == 0.0, (index, radius_mm)
            assert _volume(arm & beside) > 0.0, "アームに材料が無い（空振り）"


@requires_cad
def test_the_can_never_covers_the_band_left_for_the_main_switch(
    shipped: tuple[Any, Any], adapter: Any, deck: Any, parts: dict[str, Any]
) -> None:
    """⚠️ **メインスイッチを置ける帯が缶に覆われない**（要件 8.3）。

    ⚠️ **位置を決めない**——`PowerParams` はタスク 5.6 まで未決であり、値が
    入るまで形は何も作らない。ここが固定するのは「置ける場所が残っている」
    ことだけである。

      1. 帯は缶の底より下にある（缶はそこまで降りてこない）
      2. 帯のなかで、アームの間・アダプタの外に**空いた体積**が実在する
      3. 未決（`None`）なら形は何も足さない
    """
    params, layout = shipped
    low_mm, high_mm = deck.switch_provision_band_mm
    assert low_mm < high_mm
    assert high_mm == pytest.approx(adapter.floor_top_height_mm, abs=1e-9)
    assert params.chassis.power.main_switch_height_mm is None

    can = _bottomless_can(
        params, adapter, top_mm=deck.catch_plate_top_height_mm + _EPS_MM
    )
    assert _volume(can & _full_cylinder(_PROBE_MM, (0.0, high_mm))) == 0.0

    # ⚠️ アームの間の空きは実在する（帯が名前だけの存在でないこと）。
    from build123d import Rotation

    free_angle_deg = layout.wheel_angles_deg[0] + 360.0 / (
        _BOTH_SIDES_MM * len(layout.wheel_angles_deg)
    )
    pocket = Rotation(0, 0, free_angle_deg) * _box(
        (adapter.outer_radius_mm + _EPS_MM, adapter.outer_radius_mm + 20.0),
        (-10.0, 10.0),
        (low_mm, high_mm),
    )
    assert _volume(pocket) > 0.0
    for name, part in parts.items():
        if name.startswith(f"{SERVICE_STAND_PART_NAME}_"):
            continue
        assert _volume(pocket & part.solid) == 0.0, name


@requires_cad
def test_a_switch_height_outside_the_band_is_rejected(
    shipped: tuple[Any, Any], deck: Any
) -> None:
    """⚠️ **帯の外の高さは拒否される**（要件 8.3）。

    ⚠️ 未決を未決のまま持てることと、決まった値を検査できることは別である
    ——タスク 5.6 が高さを決めたとき、缶に覆われる高さを黙って通さない。
    """
    import dataclasses

    params, layout = shipped
    for height_mm in (
        deck.switch_provision_band_mm[0] - 1.0,
        deck.switch_provision_band_mm[1] + 1.0,
    ):
        bad = dataclasses.replace(
            params,
            chassis=dataclasses.replace(
                params.chassis,
                power=dataclasses.replace(
                    params.chassis.power, main_switch_height_mm=height_mm
                ),
            ),
        )
        with pytest.raises(GeometryError) as excinfo:
            deck_stack_geometry(bad, layout)
        assert "main_switch_height_mm" in str(excinfo.value)

    # ⚠️ 帯の中なら通る（拒否が高さそのものではなく帯を見ていること）。
    good = dataclasses.replace(
        params,
        chassis=dataclasses.replace(
            params.chassis,
            power=dataclasses.replace(
                params.chassis.power,
                main_switch_height_mm=sum(deck.switch_provision_band_mm) / 2.0,
            ),
        ),
    )
    assert deck_stack_geometry(good, layout).switch_provision_band_mm == (
        deck.switch_provision_band_mm
    )


def _usable_region(deck: object, z_range: tuple[float, float]) -> object:
    """基板デッキの**取付に使える領域**を、その高さの帯で切り出したプローブ。

    ⚠️ **立ち上がりの肉だけを除く。** 立ち上がりの内側（筒の中）も取付面で
    あり、⚠️ **そこを最初から除いてしまうと、そこへ落ちてくる材料を見落とす**
    ——実際に一度見落とした（受け止めデッキの筒は立ち上がりの内側にある）。
    """
    z_min, z_max = z_range
    outer = _full_cylinder(deck.board_plate_radius_mm, (z_min, z_max))  # type: ignore[attr-defined]
    riser_wall = _full_cylinder(
        deck.riser_outer_radius_mm, (z_min - _EPS_MM, z_max + _EPS_MM)  # type: ignore[attr-defined]
    ) - _full_cylinder(
        deck.riser_inner_radius_mm, (z_min - _PROBE_MM, z_max + _PROBE_MM)  # type: ignore[attr-defined]
    )
    return outer - riser_wall


@requires_cad
def test_nothing_intrudes_into_the_component_envelope_above_the_board_deck(
    shipped: tuple[Any, Any],
    deck: Any,
    board_deck_solids: tuple[Any, ...],
    catch_deck_solids: tuple[Any, ...],
) -> None:
    """⚠️ **約束した搭載部品の居場所に、どの部品も入り込まない**（要件 7.4, 7.5）。

    `usable_area_mm2` は「基板デッキの上面のうち取付に使える面積」であり、
    ⚠️ **その面の上に `standoff + component_height` の高さが空いていて初めて
    意味を持つ**。⚠️ **塞がれた面積を数えれば、要る面積に足りない段が
    「足りている」という顔で通る**——決定 4b の「1段で足りる」の唯一の根拠が
    その数である以上、ここは実形状で見なければならない。

    ⚠️ **立ち上がりの内側も取付面である**ため、プローブは立ち上がりの**肉**
    だけを除く（内側を最初から除くと、そこへ垂れてくる筒を見落とす）。
    """
    _, _ = shipped
    envelope = _usable_region(
        deck, (deck.board_plate_top_height_mm, deck.component_top_height_mm)
    )
    for solid in board_deck_solids + catch_deck_solids:
        assert _volume(solid & envelope) == 0.0

    # ⚠️ 空振りでないこと: 筒を重ね代ぶん下へ伸ばした形は、⚠️ **ちょうど
    # 環の体積ぶん**居場所を奪う（板の下面で高さを決めた設計がこれである）。
    dropped = _full_cylinder(
        deck.catch_tube_outer_radius_mm,
        (
            deck.catch_tube_bottom_height_mm - deck.collar_length_mm,
            deck.catch_tube_bottom_height_mm,
        ),
    ) - _full_cylinder(
        deck.catch_tube_inner_radius_mm,
        (
            deck.catch_tube_bottom_height_mm - deck.collar_length_mm - _EPS_MM,
            deck.catch_tube_bottom_height_mm + _EPS_MM,
        ),
    )
    intrusion_mm3 = _volume(dropped & envelope)
    overlap_mm = deck.component_top_height_mm - (
        deck.catch_tube_bottom_height_mm - deck.collar_length_mm
    )
    assert intrusion_mm3 == pytest.approx(
        math.pi
        * (deck.catch_tube_outer_radius_mm**2 - deck.catch_tube_inner_radius_mm**2)
        * overlap_mm,
        rel=1e-9,
    )
    assert intrusion_mm3 > 0.0

    # ⚠️ **奪われる取付面は要る面積との差より大きい**——だからこの落とし穴は
    # 「余裕のうち」では済まない（塞がれた環を引くと 19,600 を下回る）。
    shadow_area_mm2 = math.pi * (
        deck.catch_tube_outer_radius_mm**2 - deck.catch_tube_inner_radius_mm**2
    )
    assert deck.usable_area_mm2 - shadow_area_mm2 < deck.required_area_mm2


# ---------------------------------------------------------------------------
# 8. 手切りの誤差の帯と、組立の順序（要件 6.12 / 7.14 / design.md 決定 4b）
#
# ⚠️ **切断は工作機械ではなく手で行う。** 要件 6.12 は掴み面を「切り取り径が
# 上限まで振れても縁が載る範囲」に渡って連続させることを求めており、⚠️ **誤差の
# 効き方は片側である**——小さく切れば縁が広がるだけだが、大きく切れば縁が消える。
#
# 要件 7.14 は⚠️ **組み上げられること**を求める。⚠️ **「組み上がった状態で
# 干渉しない」ことは組み上げられることを意味しない**——缶は上へ広がる円錐台で
# あり、切り取った開口より大きい段はそこを通れない。
# ---------------------------------------------------------------------------


def _lip_ring_below_the_grip_face(adapter: Any, cut_diameter_mm: float) -> Any:
    """切り取り径 `cut_diameter_mm` で切ったときに残る縁の、**すぐ下**の環。"""
    grip_mm = adapter.floor_top_height_mm
    z_range = (grip_mm - _SLAB_MM, grip_mm)
    return _full_cylinder(adapter.lip_outer_radius_mm, z_range) - _full_cylinder(
        cut_diameter_mm / 2.0, (z_range[0] - _EPS_MM, z_range[1] + _EPS_MM)
    )


@requires_cad
def test_the_grip_face_covers_every_cut_the_hand_can_land(
    shipped: tuple[Any, Any], adapter: Any, adapter_parts: tuple[Any, ...]
) -> None:
    """⚠️ **切り取り径が上限まで振れても縁は掴み面に載る**（要件 6.12）。

    ⚠️ **出荷の切り取り径1点だけを見ない。** 手で切る以上、実際の径は帯の中の
    どこかに落ちる——⚠️ **上限（上流の平面部径）まで振れても縁が掴み面へ載る**
    ことが要件 6.12 の意味である。ここでは帯の中の複数の径について、⚠️ **残る
    縁の帯 `[切り取り径/2, 縁の外半径]` のすぐ下が全周で材料である**ことを
    実形状に対して測る（欠けた扇が1つでもあれば体積が足りなくなる）。
    """
    params, _ = shipped
    can = params.trash_can
    shipped_cut_mm = params.chassis.adapter.bottom_cut_diameter_mm
    upper_bound_mm = can.bottom_flat_diameter_mm
    assert shipped_cut_mm < upper_bound_mm

    # ⚠️ **帯の内端は掴み面の内縁（裾の内側）である**——そこより小さく切ると
    # 縁の内側が中央の開口へはみ出す。⚠️ 外端は縁の外半径（＝底の外半径）で
    # あり、掴み面はその外側（受け面の内径）まで続いている。
    band_low_mm = _BOTH_SIDES_MM * adapter.skirt_inner_radius_mm
    assert band_low_mm < shipped_cut_mm
    assert adapter.seat_bottom_radius_mm > adapter.lip_outer_radius_mm

    for cut_diameter_mm in (121.0, 140.0, shipped_cut_mm, 165.0, upper_bound_mm):
        expected_mm3 = (
            math.pi
            * (adapter.lip_outer_radius_mm**2 - (cut_diameter_mm / 2.0) ** 2)
            * _SLAB_MM
        )
        measured_mm3 = sum(
            _volume(solid & _lip_ring_below_the_grip_face(adapter, cut_diameter_mm))
            for solid in adapter_parts
        )
        assert measured_mm3 == pytest.approx(expected_mm3, rel=1e-6), cut_diameter_mm

    # ⚠️ 空振りでないこと: 帯の内端より小さく切れば、縁の内側は掴み面から外れる。
    too_small_mm = band_low_mm - 1.0
    expected_mm3 = (
        math.pi
        * (adapter.lip_outer_radius_mm**2 - (too_small_mm / 2.0) ** 2)
        * _SLAB_MM
    )
    measured_mm3 = sum(
        _volume(solid & _lip_ring_below_the_grip_face(adapter, too_small_mm))
        for solid in adapter_parts
    )
    assert measured_mm3 < expected_mm3


@requires_cad
def test_the_recorded_assembly_order_is_geometrically_reachable(
    shipped: tuple[Any, Any]
) -> None:
    """⚠️ **組立手順の順序で、各部品が所定の位置へ到達できる**（要件 7.14）。

    ⚠️ **組み上がり状態の無干渉（要件 9.1）とは別の主張である。** ここが見るのは
    「その時点で既に置かれている部品と干渉せずに到達できる経路があるか」であり、
    ⚠️ **缶を段の上から被せることはできない**という種類の欠陥はここでしか出ない。
    """
    params, layout = shipped
    assert assembly_reach_violations(params, layout) == ()


@requires_cad
def test_placing_the_decks_before_the_can_is_caught(
    shipped: tuple[Any, Any]
) -> None:
    """⚠️ **空振りでないこと: 段を缶より先に置く順序は落ちる**（要件 7.14）。

    ⚠️ **これが逃げた欠陥そのものである。** 段（基板デッキ Ø173.6 / 受け止め
    デッキ Ø185.4）は切り取った開口（Ø160）より大きく、⚠️ **缶を段の上から
    降ろせない**。組み上がった状態はどちらの順序でも同じであるため、
    組み上がりの干渉検査はこれを1つも捉えない。
    """
    params, layout = shipped
    wrong_order = (
        HUB_PLATE_PART_NAME,
        MOTOR_ARM_PART_NAME,
        BATTERY_TRAY_PART_NAME,
        BOARD_DECK_PART_NAME,
        CATCH_DECK_PART_NAME,
        TRASH_CAN_PART_NAME,
        ADAPTER_SEGMENT_PART_NAME,
        CABLE_GUIDE_PART_NAME,
    )
    assert sorted(wrong_order) == sorted(ASSEMBLY_ORDER), "順序だけが違う"
    violations = assembly_reach_violations(params, layout, order=wrong_order)
    assert violations != ()
    blocked = {violation.part_name for violation in violations}
    blockers = {violation.blocked_by for violation in violations}
    assert blocked == {TRASH_CAN_PART_NAME}
    assert any(name.startswith(BOARD_DECK_PART_NAME) for name in blockers)
    assert any(name.startswith(CATCH_DECK_PART_NAME) for name in blockers)
    assert all(violation.overlap_mm3 > 0.0 for violation in violations)


def test_the_recorded_assembly_order_names_every_part_once() -> None:
    """⚠️ **順序は部品を1つも落とさない**（落とせば検査は黙って弱くなる）。

    ⚠️ **形状ライブラリを要さない**——順序と据え付けの向きは算術である。
    """
    params = load_params()
    layout = derive_layout(params)
    steps = assembly_steps(params, layout)
    names = [step.part_name for step in steps]
    assert len(names) == len(set(names))
    assert set(names) == set(part_names(params)) - {
        name for name in part_names(params) if name.startswith(f"{SERVICE_STAND_PART_NAME}_")
    } | {TRASH_CAN_PART_NAME}
    # ⚠️ 缶は造形物ではない（購入部品である）。
    assert TRASH_CAN_PART_NAME not in part_names(params)
    # 缶は段より先、アダプタ断片より先である（design.md 組立手順 10 → 11）。
    assert names.index(TRASH_CAN_PART_NAME) < names.index(BOARD_DECK_PART_NAME)
    assert names.index(TRASH_CAN_PART_NAME) < names.index("adapter_segment_1")


def test_an_order_that_drops_a_part_is_rejected() -> None:
    """⚠️ **部品を落とした順序を黙って通さない**（検査の抜け道を塞ぐ）。"""
    params = load_params()
    layout = derive_layout(params)
    with pytest.raises(GeometryError) as excinfo:
        assembly_steps(params, layout, order=(HUB_PLATE_PART_NAME,))
    assert TRASH_CAN_PART_NAME in str(excinfo.value)


def test_an_order_that_names_a_part_twice_is_rejected() -> None:
    """⚠️ **同じ部品を2度置く順序を黙って通さない**（2度目は自分自身と当たる）。"""
    params = load_params()
    layout = derive_layout(params)
    with pytest.raises(GeometryError) as excinfo:
        assembly_steps(params, layout, order=ASSEMBLY_ORDER + (HUB_PLATE_PART_NAME,))
    assert HUB_PLATE_PART_NAME in str(excinfo.value)


def test_an_order_that_names_an_unknown_part_is_rejected() -> None:
    """⚠️ 未知の名を含む順序を、その名を示して拒否する。"""
    params = load_params()
    layout = derive_layout(params)
    with pytest.raises(GeometryError) as excinfo:
        assembly_steps(params, layout, order=ASSEMBLY_ORDER + ("wide_rim",))
    assert "wide_rim" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 8. 配線ガイド（タスク 3.5 / 要件 4.6, 7.6, 7.7, 8.4, 8.7 / design.md 決定 8）
#
# ⚠️ **ここが見るのは実形状である。** 「3系統に分けた」という主張は、⚠️ 通路の
# 間に材料が残っていて初めて形についての主張になる。
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def guide(shipped: tuple[Any, Any]) -> Any:
    params, layout = shipped
    return cable_guide_geometry(params, layout)


@pytest.fixture(scope="module")
def guide_solids(shipped: tuple[Any, Any], parts: dict[str, Any]) -> dict[str, Any]:
    return _placed_cable_guides(shipped, parts)


def _channel_box(guide: Any, route: Any, *, inset_mm: float = 0.0) -> Any:
    """1系統の通路の空洞（鉛直の区間。局所座標＝第1輪の向き）。"""
    return _box(
        (
            guide.channel_inner_radius_mm + inset_mm,
            guide.channel_outer_radius_mm - inset_mm,
        ),
        (route.inner_y_mm + inset_mm, route.outer_y_mm - inset_mm),
        (
            guide.crossing_channel_bottom_mm + inset_mm,
            guide.top_height_mm - inset_mm,
        ),
    )


def _crossing_box(guide: Any, route: Any, *, inset_mm: float = 0.0) -> Any:
    """1系統の通路の空洞（渡りの区間＝ベース板の下を内側へ運ぶ部分）。"""
    return _box(
        (
            guide.crossing_inner_radius_mm + inset_mm,
            guide.channel_outer_radius_mm - inset_mm,
        ),
        (route.inner_y_mm + inset_mm, route.outer_y_mm - inset_mm),
        (
            guide.crossing_channel_bottom_mm + inset_mm,
            guide.crossing_top_height_mm - inset_mm,
        ),
    )


def _guides_union(guide_solids: dict[str, Any]) -> Any:
    """据え付けた配線ガイドを1つの形へまとめる（測るためだけの和）。"""
    solids = list(guide_solids.values())
    combined = solids[0]
    for solid in solids[1:]:
        combined = combined + solid
    return combined


@requires_cad
def test_each_route_is_its_own_channel_with_material_between(
    guide: Any, parts: dict[str, Any]
) -> None:
    """⚠️ **3系統は交差せず、間に材料が残っている**（要件 7.7 / 決定 8）。

    ⚠️ **色だけに頼らず経路そのものを分ける**——取り違えるとエンコーダが飛ぶ。
    通路が空洞であること、通路どうしが交わらないこと、⚠️ **その間の壁が実体で
    あること**の3つを実形状に対して測る。
    """
    solid = parts["cable_guide_1"].solid
    boxes = [_channel_box(guide, route) for route in guide.routes]
    boxes += [_crossing_box(guide, route) for route in guide.routes]

    for box in boxes:
        assert _volume(solid & box) == 0.0
    for left, right in itertools.combinations(boxes, 2):
        # ⚠️ 同じ系統の鉛直と渡りは繋がっている（重なる）。別の系統とは交わらない。
        if boxes.index(left) % len(guide.routes) == boxes.index(right) % len(
            guide.routes
        ):
            continue
        assert _volume(left & right) == 0.0

    # ⚠️ 通路どうしの間は**全域が材料**である（壁が消えれば3系統は1つの空洞になる）。
    # ⚠️ **渡りの区間でも壁は残る**——そして⚠️ **通し穴も窓も系統ごとに1つ**で
    # あり、経路はどこでも合流しない（要件 7.7 / 決定 8）。
    for left, right in zip(guide.routes, guide.routes[1:], strict=False):
        for radius_range_mm, z_range_mm in (
            (
                (guide.channel_inner_radius_mm, guide.channel_outer_radius_mm),
                (guide.crossing_channel_bottom_mm, guide.top_height_mm),
            ),
            (
                (guide.crossing_inner_radius_mm, guide.channel_outer_radius_mm),
                (guide.crossing_channel_bottom_mm, guide.crossing_top_height_mm),
            ),
        ):
            wall = _box(radius_range_mm, (left.outer_y_mm, right.inner_y_mm), z_range_mm)
            assert _volume(solid & wall) == pytest.approx(_volume(wall), rel=1e-9)

    # ⚠️ 空振りでないこと: 壁の厚さぶん太らせた通路は材料へ食い込む。
    grown = _box(
        (guide.channel_inner_radius_mm, guide.channel_outer_radius_mm),
        (
            guide.routes[0].inner_y_mm - guide.wall_thickness_mm,
            guide.routes[0].outer_y_mm + guide.wall_thickness_mm,
        ),
        (guide.cable_lowest_height_mm, guide.top_height_mm),
    )
    assert _volume(solid & grown) > 0.0


@requires_cad
def test_the_lowest_material_of_the_guide_is_the_height_the_clearance_uses(
    shipped: tuple[Any, Any], guide: Any, guide_solids: dict[str, Any]
) -> None:
    """⚠️ **配線の最下点が隙間の算出対象に現れる**（要件 4.2, 4.6）。

    ⚠️ **保持箇所は実体である**——通路の下の口の高さが `clearance` の `cable` と
    一致し、⚠️ **そこより下にガイドの材料は無い**。
    """
    from chassis_mechanism.clearance import clearance_items

    params, layout = shipped
    heights = {
        item.name: item.height_mm for item in clearance_items(layout, params.chassis)
    }
    assert guide.cable_lowest_height_mm == pytest.approx(heights["cable"])
    for name, solid in guide_solids.items():
        measured_mm = float(solid.bounding_box().min.Z)
        assert measured_mm == pytest.approx(heights["cable"], abs=1e-6), name
        assert measured_mm > params.chassis.clearance.min_ground_clearance_mm

    # ⚠️ **最下点は渡りの床である**（配線はその上に載る）。床の下に材料は無い。
    solid = parts_union = _guides_union(guide_solids)
    below = _box(
        (-_PROBE_MM, _PROBE_MM),
        (-_PROBE_MM, _PROBE_MM),
        (guide.cable_lowest_height_mm - 5.0, guide.cable_lowest_height_mm - _EPS_MM),
    )
    assert _volume(parts_union & below) == 0.0

    # ⚠️ 渡りの内端は3つとも実際に開いている（そこから通し穴へ渡る）。
    for route in guide.routes:
        mouth = _box(
            (
                guide.crossing_inner_radius_mm,
                guide.crossing_inner_radius_mm + _SLAB_MM,
            ),
            (route.inner_y_mm, route.outer_y_mm),
            (guide.crossing_channel_bottom_mm, guide.crossing_top_height_mm),
        )
        assert _volume(solid & mouth) == 0.0, route.name
        # まわりには材料がある（口が部品の外へ抜けていない）。
        ring = _box(
            (
                guide.crossing_inner_radius_mm,
                guide.crossing_inner_radius_mm + _SLAB_MM,
            ),
            (
                route.inner_y_mm - guide.wall_thickness_mm,
                route.outer_y_mm + guide.wall_thickness_mm,
            ),
            (
                guide.cable_lowest_height_mm,
                guide.crossing_top_height_mm,
            ),
        )
        assert _volume(solid & ring) > 0.0, route.name


@requires_cad
def test_no_cable_guide_reaches_a_wheel_or_the_floor(
    shipped: tuple[Any, Any], guide: Any, guide_solids: dict[str, Any]
) -> None:
    """⚠️ **回転部にも床にも触れない**（要件 4.6, 7.6）。

    ⚠️ **「外側だから当たらない」ではない。** ホイールは車軸まわりの円筒であり、
    その頂点はちょうどベース板の下面の高さにある——ガイドはその高さより下へ出ず、
    かつホイールの内側面より内側の半径に留まる。⚠️ **両方を実形状で測る。**
    """
    from build123d import Rotation

    params, layout = shipped
    wheel = params.chassis.wheel

    def _wheels(*, radius_growth_mm: float = 0.0, width_growth_mm: float = 0.0) -> Any:
        combined = None
        for angle_deg in layout.wheel_angles_deg:
            solid = Rotation(0, 0, angle_deg) * _wheel_cylinder(
                radius_mm=wheel.nominal_diameter_mm / 2.0,
                width_mm=wheel.width_mm,
                center_height_mm=layout.vertical.axle_center_height_mm,
                radius_growth_mm=radius_growth_mm,
                width_growth_mm=width_growth_mm,
                x_offset_mm=layout.base_radius_mm,
            )
            combined = solid if combined is None else combined + solid
        return combined

    wheels = _wheels()
    for name, solid in guide_solids.items():
        assert _volume(solid & wheels) == 0.0, name
        assert float(solid.bounding_box().min.Z) > (
            params.chassis.clearance.min_ground_clearance_mm
        )

    # ⚠️ 空振りでないこと: 半径にも幅にも膨らませたホイールは裾へ届く
    #（隔てているのが半径と高さの**両方**であることの現れである）。
    grown = _wheels(radius_growth_mm=20.0, width_growth_mm=12.0)
    assert sum(_volume(solid & grown) for solid in guide_solids.values()) > 0.0


@requires_cad
def test_the_terminal_seat_is_a_flat_face_with_blind_screw_holes(
    guide: Any, parts: dict[str, Any]
) -> None:
    """⚠️ **端子台の保持箇所は座とねじ穴として実在する**（要件 8.4）。

    ⚠️ **寸法も方式も未決である**（タスク 5.6）。ここが見るのは「後から載せられる
    平面と、そこへ入るねじの座があること」だけである。⚠️ **ねじ穴は袋穴であり、
    座の肉を突き抜けない**——突き抜ければ、そこは座ではなく穴である。
    """
    solid = parts["cable_guide_1"].solid
    seat = _box(
        (guide.terminal_pad_inner_radius_mm, guide.terminal_pad_outer_radius_mm),
        (guide.terminal_pad_inner_y_mm, guide.terminal_pad_outer_y_mm),
        (guide.top_height_mm - _SLAB_MM, guide.top_height_mm),
    )
    faces = _planar_faces_on_plane(solid & seat, (0.0, 0.0, 1.0), guide.top_height_mm)
    assert faces, "端子台の座に平面が無い"
    assert sum(float(face.area) for face in faces) > 0.0

    for y_mm in guide.terminal_bolt_y_mm:
        column = _box(
            (
                guide.terminal_bolt_radius_mm - _INSET_MM,
                guide.terminal_bolt_radius_mm + _INSET_MM,
            ),
            (y_mm - _INSET_MM, y_mm + _INSET_MM),
            (
                guide.top_height_mm - guide.insert_bore_depth_mm + _EPS_MM,
                guide.top_height_mm - _EPS_MM,
            ),
        )
        assert _volume(solid & column) == 0.0, y_mm
        # ⚠️ **袋穴である**——座ぐりの底より下には材料が残る。
        below = _box(
            (
                guide.terminal_bolt_radius_mm - _INSET_MM,
                guide.terminal_bolt_radius_mm + _INSET_MM,
            ),
            (y_mm - _INSET_MM, y_mm + _INSET_MM),
            (
                guide.terminal_pad_bottom_height_mm + _EPS_MM,
                guide.top_height_mm - guide.insert_bore_depth_mm - _EPS_MM,
            ),
        )
        assert _volume(solid & below) == pytest.approx(_volume(below), rel=1e-6), y_mm


@requires_cad
def test_the_estop_lead_out_opens_only_into_the_supply_route(
    guide: Any, parts: dict[str, Any]
) -> None:
    """⚠️ **非常停止の引き出しは電源の経路へだけ開く**（要件 8.7 / 決定 8）。

    ⚠️ **他の系統へ抜ける穴は「経路を分けた」ことを台無しにする。** 同じ高さ・
    同じ深さの筋を他の系統の位置で引けば、そこは全域が材料でなければならない。
    """
    solid = parts["cable_guide_1"].solid
    supply = guide.route("supply")

    def _lead_out_path(y_mm: float) -> Any:
        return _box(
            (
                guide.channel_outer_radius_mm + _EPS_MM,
                guide.skirt_outer_radius_mm - _EPS_MM,
            ),
            (y_mm - _INSET_MM, y_mm + _INSET_MM),
            (
                guide.estop_lead_out_height_mm - _INSET_MM,
                guide.estop_lead_out_height_mm + _INSET_MM,
            ),
        )

    opened = _lead_out_path(supply.center_y_mm)
    assert _volume(solid & opened) == 0.0
    for route in guide.routes:
        if route.name == "supply":
            continue
        blocked = _lead_out_path(route.center_y_mm)
        assert _volume(solid & blocked) == pytest.approx(
            _volume(blocked), rel=1e-6
        ), route.name

    # ⚠️ 引き出しは実際に `supply` の通路へ抜けている（外面から通路まで繋がる）。
    outside = _box(
        (
            guide.skirt_outer_radius_mm - _EPS_MM,
            guide.skirt_outer_radius_mm + _EPS_MM,
        ),
        (supply.center_y_mm - _INSET_MM, supply.center_y_mm + _INSET_MM),
        (
            guide.estop_lead_out_height_mm - _INSET_MM,
            guide.estop_lead_out_height_mm + _INSET_MM,
        ),
    )
    assert _volume(solid & outside) == 0.0

    # ⚠️ 取付ねじの座は**袋穴**であり、通路へは抜けない。
    for y_mm in guide.estop_bolt_y_mm:
        beyond = _box(
            (
                guide.channel_outer_radius_mm + _EPS_MM,
                guide.skirt_outer_radius_mm - guide.insert_bore_depth_mm - _EPS_MM,
            ),
            (y_mm - _INSET_MM, y_mm + _INSET_MM),
            (
                guide.estop_bolt_height_mm - _INSET_MM,
                guide.estop_bolt_height_mm + _INSET_MM,
            ),
        )
        assert _volume(solid & beyond) == pytest.approx(_volume(beyond), rel=1e-6)


@requires_cad
def test_the_arm_carries_the_insert_bores_the_cable_guide_screws_into(
    guide: Any, drive_base: Any, parts: dict[str, Any]
) -> None:
    """⚠️ **記録された取付ねじが実形状のどこかを通る**（`joints.ASSUMPTIONS`）。

    ガイドはアームの上面へ座る。⚠️ **アーム側に座が無ければ、ねじはどこへも入らない**
    ——ハブ板がアダプタの座を持つのと同じ関係である。
    """
    arm = parts["motor_arm_1"].solid
    arm_top_mm = drive_base.underside_height_mm + drive_base.arm_thickness_mm

    for radius_mm in guide.mount_bolt_radii_mm:
        bore = _box(
            (radius_mm - _INSET_MM, radius_mm + _INSET_MM),
            (guide.mount_bolt_y_mm - _INSET_MM, guide.mount_bolt_y_mm + _INSET_MM),
            (arm_top_mm - guide.insert_bore_depth_mm + _EPS_MM, arm_top_mm - _EPS_MM),
        )
        assert _volume(arm & bore) == 0.0, radius_mm
        # ⚠️ **袋穴である**——アームの下面へ抜けない（抜ければ床側の隙間の話になる）。
        below = _box(
            (radius_mm - _INSET_MM, radius_mm + _INSET_MM),
            (guide.mount_bolt_y_mm - _INSET_MM, guide.mount_bolt_y_mm + _INSET_MM),
            (
                drive_base.underside_height_mm + _EPS_MM,
                arm_top_mm - guide.insert_bore_depth_mm - _EPS_MM,
            ),
        )
        assert _volume(arm & below) == pytest.approx(_volume(below), rel=1e-6)

    # ⚠️ 空振りでないこと: 座の無い半径では柱いっぱいに材料がある。
    between_mm = sum(guide.mount_bolt_radii_mm) / 2.0
    solid_column = _box(
        (between_mm - _INSET_MM, between_mm + _INSET_MM),
        (guide.mount_bolt_y_mm - _INSET_MM, guide.mount_bolt_y_mm + _INSET_MM),
        (arm_top_mm - guide.insert_bore_depth_mm + _EPS_MM, arm_top_mm - _EPS_MM),
    )
    assert _volume(arm & solid_column) == pytest.approx(
        _volume(solid_column), rel=1e-6
    )


@requires_cad
def test_each_cable_guide_is_one_solid_that_fits_the_build_volume(
    shipped: tuple[Any, Any], guide: Any, parts: dict[str, Any]
) -> None:
    """⚠️ 宣言した外接箱と実形状が一致し、造形可能寸法に収まる（要件 2.2, 2.3）。

    ⚠️ **点数は `joints.segment_counts()` が正である**（要件 2.1）。
    """
    from catch_mechanism import Envelope

    from chassis_mechanism.joints import segment_counts

    params, _ = shipped
    names = [name for name in parts if name.startswith("cable_guide")]
    assert len(names) == segment_counts(params)["cable_guide"]
    for name in names:
        metrics = parts[name].metrics
        assert metrics.solid_count == 1, name
        for value, expected in zip(
            metrics.bbox_mm,
            (guide.envelope.x_mm, guide.envelope.y_mm, guide.envelope.z_mm),
            strict=True,
        ):
            assert value == pytest.approx(expected, abs=1e-6), name
        assert (
            check_envelope(
                name,
                Envelope(
                    x_mm=metrics.bbox_mm[0],
                    y_mm=metrics.bbox_mm[1],
                    z_mm=metrics.bbox_mm[2],
                ),
                params.printing,
            )
            == ()
        ), name


@requires_cad
def test_dropping_the_cable_guide_from_above_is_caught(
    shipped: tuple[Any, Any]
) -> None:
    """⚠️ **空振りでないこと: ガイドを真上から降ろす経路は缶に塞がれる**（要件 7.14）。

    缶の側壁は上へ広がっており、⚠️ **ガイドの真上をいずれ横切る**——組み上がった
    状態はどちらの向きでも同じであるため、⚠️ 最終形の干渉検査はこれを1つも
    捉えない。ガイドはアームの上面へ**半径方向へ差し込んで**据える。
    """
    from chassis_mechanism.shapes import (
        _AXIAL_APPROACH,
        _approach_overlap_mm3,
        _placed_machine_parts,
        _radial_approach,
    )

    params, layout = shipped
    placed = _placed_machine_parts(params, layout)
    can = placed[TRASH_CAN_PART_NAME]
    solid = placed["cable_guide_1"]

    assert _approach_overlap_mm3(solid, can, _AXIAL_APPROACH) > 0.0
    # ⚠️ 記録された向き（半径方向）なら缶に当たらない。
    radial = _radial_approach(layout.wheel_angles_deg[0])
    assert _approach_overlap_mm3(solid, can, radial) == 0.0


# ---------------------------------------------------------------------------
# 8b. ⚠️ 経路が**繋がっている**こと（要件 7.6 / タスク 3.5）
#
# ⚠️ **これが欠けていた検査である。** 3系統が分かれていることも、最下点が隙間の
# 算出対象に現れることも、⚠️ **経路がどこへも通じていなくても成り立ってしまう**
# ——通路の分離だけを測っていると、配線が基板へ届かない機体を全数通してしまう。
# ここでは通路と同じ断面のプローブを経路に沿って掃引し、⚠️ **どの部品にも当たら
# ないこと**を実形状に対して測る。
# ---------------------------------------------------------------------------


def _harness_path_solids(guide: Any, route: Any, deck: Any) -> list[Any]:
    """1系統ぶんの経路（第1輪の向きの局所座標＝機体座標）を区間ごとに並べる。

    ⚠️ **経路は幾何が宣言している値だけで組み立てる**（`CableGuideGeometry` の
    渡りと窓、`CableRoute` の通し穴）。⚠️ 測るためにここで新しい寸法を発明しない。

    ⚠️ **系統ごとに別の通し穴・別の窓を通る**（要件 7.7 / 決定 8）。渡りの内端から
    穴までは⚠️ **壁の無い自由空間**であり、そこを1本の傾いた区間として掃引する
    ——3系統ぶんのこの区間が互いに交わらないことが「経路で分けた」ことの実体で
    ある（`test_the_harness_has_a_free_path_from_the_guide_to_the_board_plane`）。
    """
    from build123d import Align, Box, Cylinder, Location, Rotation

    half_mm = guide.channel_width_mm / 2.0
    crossing_z = (guide.crossing_channel_bottom_mm, guide.crossing_top_height_mm)
    reach_angle_deg = math.degrees(
        math.atan2(
            route.passage_y_mm - route.center_y_mm,
            route.passage_x_mm - guide.crossing_inner_radius_mm,
        )
    )
    # ⚠️ **束は口から真っ直ぐ出てから曲がる。** 口の幅は通路の内寸ちょうどしか
    # 無く、⚠️ **傾いた同じ幅の帯を口の面から直接生やすと角が壁へ食い込む**
    # ——曲がりは口の外（自由空間）で起きる。真っ直ぐ出る長さは、傾いた帯の
    # 端面が口の面を越えない最小の長さである。
    # ⚠️ 角が口の面にちょうど接したままではブール演算の結果が面の扱いに委ねられる
    # ため、測る余裕（`_EPS_MM`）ぶん外で曲げる。
    bend_mm = half_mm * abs(math.sin(math.radians(reach_angle_deg))) + _EPS_MM
    mouth_x_mm = guide.crossing_inner_radius_mm - bend_mm
    mouth_y_mm = route.center_y_mm
    reach_mm = math.hypot(
        route.passage_x_mm - mouth_x_mm, route.passage_y_mm - mouth_y_mm
    )

    def shaft(z_range: tuple[float, float]) -> Any:
        z_min, z_max = z_range
        return Location(
            (route.passage_x_mm, route.passage_y_mm, (z_min + z_max) / 2.0)
        ) * Cylinder(
            guide.passage_diameter_mm / 2.0,
            z_max - z_min,
            align=(Align.CENTER, Align.CENTER, Align.CENTER),
        )

    return [
        # 1. ガイドの鉛直の通路（端子台の座と非常停止の引き出しが接する区間）。
        _channel_box(guide, route),
        # 2. ガイドの渡り（ベース板の下を内側へ運ぶ区間）。
        _crossing_box(guide, route),
        # 3. 渡りの口を真っ直ぐ出る区間（⚠️ ここまでは口と同じ向きである）。
        _box(
            (mouth_x_mm, guide.crossing_inner_radius_mm),
            (route.inner_y_mm, route.outer_y_mm),
            crossing_z,
        ),
        # 4. 口から通し穴まで（⚠️ **壁の無い自由空間を斜めに渡る**）。
        Location(
            (
                (mouth_x_mm + route.passage_x_mm) / 2.0,
                (mouth_y_mm + route.passage_y_mm) / 2.0,
                (crossing_z[0] + crossing_z[1]) / 2.0,
            )
        )
        * Rotation(0, 0, reach_angle_deg)
        * Box(
            reach_mm,
            guide.channel_width_mm,
            crossing_z[1] - crossing_z[0],
            align=(Align.CENTER, Align.CENTER, Align.CENTER),
        ),
        # 5. 中央部の通し穴（⚠️ **ここだけが缶の内側へ通じている**）。
        shaft((guide.crossing_top_height_mm, guide.window_bottom_height_mm)),
        # 6. 立ち上がりの窓（⚠️ **基板面より上で、系統ごとに別の口から外へ出る**）。
        Rotation(0, 0, route.passage_angle_deg)
        * _box(
            (
                guide.passage_center_radius_mm - half_mm,
                _window_exit_mm(guide, deck),
            ),
            (-guide.window_width_mm / 2.0, guide.window_width_mm / 2.0),
            (guide.window_bottom_height_mm, guide.window_top_height_mm),
        ),
    ]


def _window_exit_mm(guide: Any, deck: Any) -> float:
    """窓を出たプローブを止める半径（mm）。⚠️ **寸法ではなく測る範囲である。**

    ⚠️ **立ち上がりの外径から導出する**——定数で置くと、外径がその値を越えた
    瞬間にプローブは窓の内側で止まり、⚠️ **筒から出ていないのに「経路は空いて
    いる」と言い続ける**。壁を出たことが分かればよいので、外径に壁の厚さぶんを
    足した半径で止める（取付箇所の円より内側である）。
    """
    return deck.riser_outer_radius_mm + guide.wall_thickness_mm


@requires_cad
def test_the_harness_has_a_free_path_from_the_guide_to_the_board_plane(
    shipped: tuple[Any, Any], guide: Any, parts: dict[str, Any]
) -> None:
    """⚠️ **経路は繋がって初めて経路である**（要件 7.6）。そして⚠️ **3系統は
    最後まで別の経路である**（要件 7.7 / 決定 8）。

    モータ側からガイドの通路へ入った配線が、渡り → 中央部の通し穴 →
    立ち上がりの中 → 窓、と辿って⚠️ **基板面の高さで缶の内側へ出られる**ことを、
    通路と同じ断面のプローブで測る。⚠️ **どの部品にも当たらないこと**が1つ目の
    主張であり、⚠️ **3本の経路が互いに1点も共有しないこと**が2つ目である
    ——⚠️ **後者が欠けていると、3系統が同じ穴を通る形が黙って通る。**
    """
    params, layout = shipped
    deck = deck_stack_geometry(params, layout)
    placed = dict(_placed_drive_base(shipped, parts))
    placed.update(_placed_cable_guides(shipped, parts))
    for name, part in parts.items():
        if name.startswith(f"{SERVICE_STAND_PART_NAME}_") or name in placed:
            continue
        placed[name] = part.solid
    placed[TRASH_CAN_PART_NAME] = build_trash_can_shell(params, layout)

    chains = {
        route.name: _harness_path_solids(guide, route, deck) for route in guide.routes
    }
    for name, probes in chains.items():
        for index, probe in enumerate(probes):
            for part_name, solid in placed.items():
                assert _volume(probe & solid) == 0.0, (name, index, part_name)

    # ⚠️ **3系統の経路は全区間で互いに交わらない。** ガイドの中では壁が隔てて
    # いるが、⚠️ **渡りの口から先は壁が無い**——そこで交わる形は「経路で分けた」
    # という主張を端子の手前で失う。
    for left, right in itertools.combinations(chains, 2):
        for left_index, left_probe in enumerate(chains[left]):
            for right_index, right_probe in enumerate(chains[right]):
                assert _volume(left_probe & right_probe) == 0.0, (
                    left,
                    left_index,
                    right,
                    right_index,
                )

    # ⚠️ 経路は基板面より上で外へ出る（低く出れば段の下へ回り込む）。
    assert guide.window_bottom_height_mm > deck.board_plane_height_mm
    assert guide.window_top_height_mm < deck.catch_tube_bottom_height_mm
    # ⚠️ プローブは筒の外まで届いている（窓の内側で止まっていない）。
    assert _window_exit_mm(guide, deck) > deck.riser_outer_radius_mm


@requires_cad
def test_material_separates_the_three_passages_in_every_plate_they_cross(
    shipped: tuple[Any, Any], guide: Any, parts: dict[str, Any]
) -> None:
    """⚠️ **穴どうしの間に材料が残っている**（要件 7.7 / 決定 8）。

    ⚠️ **「系統ごとに穴を開けた」は、間に肉が残って初めて形についての主張になる。**
    中央部の板と基板デッキの板の両方で、隣り合う通し穴の中心の間に立てた円柱が
    ⚠️ **全域材料である**ことを測る（穴が1つに融合していれば空洞が出る）。
    """
    from build123d import Align, Cylinder, Location

    params, layout = shipped
    drive_base = drive_base_geometry(params, layout)
    deck = deck_stack_geometry(params, layout)
    hub = parts[HUB_PLATE_PART_NAME].solid
    board = parts[BOARD_DECK_PART_NAME].solid

    # ⚠️ 壁の太さぶん（穴の間隔 − 通路の内寸）の円柱を、穴と穴の真ん中に立てる。
    wall_mm = guide.passage_spacing_mm - guide.channel_width_mm
    assert wall_mm >= guide.wall_thickness_mm

    def wall_probe(left: Any, right: Any, z_range: tuple[float, float]) -> Any:
        z_min, z_max = z_range
        return Location(
            (
                (left.passage_x_mm + right.passage_x_mm) / 2.0,
                (left.passage_y_mm + right.passage_y_mm) / 2.0,
                (z_min + z_max) / 2.0,
            )
        ) * Cylinder(
            wall_mm / 2.0,
            z_max - z_min,
            align=(Align.CENTER, Align.CENTER, Align.CENTER),
        )

    hub_z = (
        drive_base.underside_height_mm + _EPS_MM,
        drive_base.underside_height_mm + drive_base.plate_thickness_mm - _EPS_MM,
    )
    board_z = (
        deck.board_plate_bottom_height_mm + _EPS_MM,
        deck.board_plate_top_height_mm - _EPS_MM,
    )
    for left, right in zip(guide.routes, guide.routes[1:], strict=False):
        for solid, z_range, label in (
            (hub, hub_z, HUB_PLATE_PART_NAME),
            (board, board_z, BOARD_DECK_PART_NAME),
        ):
            probe = wall_probe(left, right, z_range)
            assert _volume(solid & probe) == pytest.approx(
                _volume(probe), rel=1e-9
            ), (left.name, right.name, label)

    # ⚠️ 空振りでないこと: 穴そのものの位置に立てた同じ円柱は材料に当たらない。
    for route in guide.routes:
        bore = wall_probe(route, route, hub_z)
        assert _volume(hub & bore) == 0.0, route.name


@requires_cad
def test_merging_the_three_passages_makes_the_routes_share_one_path(
    shipped: tuple[Any, Any], guide: Any
) -> None:
    """⚠️ **空振りでないこと**: 3系統を1つの通し穴へ束ねると経路は交わる。

    ⚠️ **これが前の版の形である。** 通し穴を輪ごとに1つだけ開ける設計では、
    3本の経路は中央部で同じ空間を通る——⚠️ **経路の分離は、配線を挿す直前で
    失われていた。** 交差を測る検査が無ければ、その形は黙って通る。
    """
    import dataclasses

    params, layout = shipped
    deck = deck_stack_geometry(params, layout)
    merged = dataclasses.replace(
        guide,
        routes=tuple(
            dataclasses.replace(
                route,
                passage_angle_deg=guide.routes[1].passage_angle_deg,
                passage_x_mm=guide.routes[1].passage_x_mm,
                passage_y_mm=guide.routes[1].passage_y_mm,
            )
            for route in guide.routes
        ),
    )
    merged_chains = [
        _harness_path_solids(merged, route, deck) for route in merged.routes
    ]
    overlap_mm3 = sum(
        _volume(left & right)
        for left_chain, right_chain in itertools.combinations(merged_chains, 2)
        for left in left_chain
        for right in right_chain
    )
    assert overlap_mm3 > 0.0

    # ⚠️ 束ねていない本来の経路は1点も共有しない（この検査が形そのものを
    # 疑っていない証拠である）。
    chains = [_harness_path_solids(guide, route, deck) for route in guide.routes]
    assert (
        sum(
            _volume(left & right)
            for left_chain, right_chain in itertools.combinations(chains, 2)
            for left in left_chain
            for right in right_chain
        )
        == 0.0
    )


@requires_cad
def test_closing_either_new_opening_blocks_the_harness_path(
    shipped: tuple[Any, Any], guide: Any
) -> None:
    """⚠️ **空振りでないこと**: 2つの開口のどちらを塞いでも経路は通らなくなる。

    ⚠️ **経路を通しているのは中央部の通し穴と立ち上がりの窓の2つだけである。**
    どちらか一方でも塞げばプローブは材料へ当たる——⚠️ この2つが無い設計では、
    3系統の分離が完璧でも配線は基板へ届かない。
    """
    import dataclasses

    from chassis_mechanism.shapes import _build_board_deck, _build_hub_plate

    params, layout = shipped
    drive_base = drive_base_geometry(params, layout)
    adapter = adapter_geometry(params, layout)
    deck = deck_stack_geometry(params, layout)
    route = guide.route("supply")
    probes = _harness_path_solids(guide, route, deck)

    # (a) 通し穴を余所へ移した中央部は、経路の鉛直区間を塞ぐ。
    moved = dataclasses.replace(
        guide,
        routes=tuple(
            dataclasses.replace(
                item,
                passage_x_mm=-item.passage_x_mm,
                passage_y_mm=-item.passage_y_mm,
            )
            for item in guide.routes
        ),
    )
    blocked_plate = _build_hub_plate(drive_base, adapter, moved)
    assert sum(_volume(probe & blocked_plate) for probe in probes) > 0.0
    # 本来の中央部は塞がない（この検査が中央部そのものを疑っていない証拠）。
    plate = _build_hub_plate(drive_base, adapter, guide)
    assert sum(_volume(probe & plate) for probe in probes) == 0.0

    # (b) 窓を受け止めデッキの筒の高さへ移した基板デッキは、経路の出口を塞ぐ。
    raised = dataclasses.replace(
        guide,
        window_bottom_height_mm=deck.catch_tube_bottom_height_mm,
        window_top_height_mm=deck.catch_tube_bottom_height_mm + guide.window_width_mm,
    )
    blocked_deck = _build_board_deck(deck, 0, raised)
    assert sum(_volume(probe & blocked_deck) for probe in probes) > 0.0
    board_deck = _build_board_deck(deck, 0, guide)
    assert sum(_volume(probe & board_deck) for probe in probes) == 0.0


# ---------------------------------------------------------------------------
# 12. 関門が実形状について述べていること（タスク 3.6 / 要件 1.12, 2.2, 2.3,
#     2.9, 4.4, 7.9, 9.1）
#
# ⚠️ **関門は形状ライブラリを要さない側にある**（`shapes.check_before_build`）。
# それが正しいためには、⚠️ **関門が読む宣言と実形状が一致していなければならない**
# ——本節はその突き合わせを実ソリッドに対して行う。
# ---------------------------------------------------------------------------


def _tray_seat_probe(tray: Any) -> Any:
    """回り止めの座として数える範囲（ポケットの足跡とヒューズ置き場）を切り出す箱。

    ⚠️ **解析式が数える範囲と同じ2つの箱である**（`battery_tray_geometry` の
    `seat_bearing_area_mm2`）。腕と耳の帯は含めない——中央部の下面は半径
    `hub_radius_mm` で切れており、⚠️ **一部しか当たらない帯を面積へ足さない**。
    """
    pocket = _box(
        (-tray.outer_half_length_mm, tray.outer_half_length_mm),
        (-tray.outer_half_width_mm, tray.outer_half_width_mm),
        (tray.tray_top_height_mm - _PROBE_MM, tray.tray_top_height_mm + _PROBE_MM),
    )
    fuse_bay = _box(
        (-tray.fuse_bay_wall_x_mm, tray.fuse_bay_wall_x_mm),
        (tray.outer_half_width_mm, tray.fuse_bay_wall_y_mm),
        (tray.tray_top_height_mm - _PROBE_MM, tray.tray_top_height_mm + _PROBE_MM),
    )
    return pocket + fuse_bay


def _measured_tray_anti_rotation_seat_mm2(solid: Any, tray: Any) -> float:
    """トレイの上端が中央部の下面へ当たる面の**実面積**（mm^2）。"""
    clipped = solid & _tray_seat_probe(tray)
    faces = _planar_faces_on_plane(clipped, (0.0, 0.0, 1.0), tray.tray_top_height_mm)
    assert faces, "ポケットの縁の上端に、上を向いた平面が1つも無い"
    return sum(float(face.area) for face in faces)


@requires_cad
def test_the_measured_anti_rotation_seat_matches_the_area_the_geometry_records(
    shipped: tuple[Any, Any], tray: Any, parts: dict[str, Any]
) -> None:
    """⚠️ 回り止めの座の当たり面が、実形状の面積と一致する（要件 2.9）。

    ポケットの縁の上端は中央部（ハブ板）の下面へ**圧縮で**当たり、トレイが
    締結のボルトを軸に回ろうとするのを止めている。⚠️ **他のどの接合部の家族にも
    当たり面の検査があるのに、ここだけ無かった**（タスク 3.2 が 3.6 へ残した
    申し送り）。

    ⚠️ **`joints` の `JointSpec` としては持てない**——この面の法線はトレイの
    造形姿勢で積層方向（`z`）を向いており、`JointSpec` は要件 2.8 に従って
    その軸を拒否する。⚠️ **姿勢を偽らずに**、当たり面の下限だけを課している。
    """
    params, _ = shipped
    measured_mm2 = _measured_tray_anti_rotation_seat_mm2(
        parts[BATTERY_TRAY_PART_NAME].solid, tray
    )
    assert measured_mm2 == pytest.approx(tray.seat_bearing_area_mm2, rel=1e-9)
    assert measured_mm2 >= params.joint.min_bearing_area_mm2
    # ⚠️ **座は中央部の下面の内側にある**（外へはみ出した縁は蓋を持たない）。
    drive_base = drive_base_geometry(*shipped)
    corner_mm = math.hypot(tray.outer_half_length_mm, tray.fuse_bay_wall_y_mm)
    assert corner_mm <= drive_base.hub_radius_mm


@requires_cad
def test_a_tray_wall_too_thin_cannot_realise_the_anti_rotation_seat(
    shipped: tuple[Any, Any], tray: Any, parts: dict[str, Any]
) -> None:
    """⚠️ **空振りでないこと**: 壁が薄ければ縁の上端は当たり面にならない。

    ⚠️ 出荷の壁厚（3.0mm）で 756mm^2 ある座は、⚠️ **測る手口が薄さを見抜く**
    ことを示せなければ「壁がどれだけ薄くても通る検査」と区別できない。ここでは
    構築済みのソリッドから縁の外側の帯だけを削り、**測るためだけに**薄い縁を
    作って、同じ手口が下限を割ることを見る。
    """
    params, _ = shipped
    thin_mm = 0.2
    assert thin_mm < tray.wall_thickness_mm
    shaved = parts[BATTERY_TRAY_PART_NAME].solid - _box(
        (-tray.outer_half_length_mm + thin_mm, tray.outer_half_length_mm - thin_mm),
        (-tray.outer_half_width_mm + thin_mm, tray.fuse_bay_wall_y_mm - thin_mm),
        (tray.tray_top_height_mm - _INSET_MM, tray.tray_top_height_mm + _PROBE_MM),
    )
    measured_mm2 = _measured_tray_anti_rotation_seat_mm2(shaved, tray)
    assert measured_mm2 < params.joint.min_bearing_area_mm2
    # ⚠️ **削っていない側は通る**（この反例が下限そのものを疑っていない証拠）。
    assert (
        _measured_tray_anti_rotation_seat_mm2(
            parts[BATTERY_TRAY_PART_NAME].solid, tray
        )
        >= params.joint.min_bearing_area_mm2
    )


@requires_cad
def test_every_declared_envelope_matches_the_solid_the_gate_lets_through(
    shipped: tuple[Any, Any], parts: dict[str, Any]
) -> None:
    """⚠️ 関門が読む外接箱が、**実形状の外接箱と一致する**（要件 2.2, 2.3）。

    `check_before_build` は形状ライブラリを要さずに造形可能寸法を判定する。
    ⚠️ **その判定が実物について述べたものであるためには、宣言と実形状が一致して
    いなければならない**——一致しなければ、関門は「収まっている」と言いながら
    造形できない断片を通す。
    """
    params, layout = shipped
    declared = dict(part_envelopes(params, layout))
    assert set(declared) == set(parts)
    for name, envelope in declared.items():
        size = parts[name].solid.bounding_box().size
        for axis, measured_mm, declared_mm in (
            ("x", size.X, envelope.x_mm),
            ("y", size.Y, envelope.y_mm),
            ("z", size.Z, envelope.z_mm),
        ):
            # ⚠️ **宣言は実形状を覆う**（下回れば、関門は造形できない断片を通す）。
            assert measured_mm <= declared_mm + 1e-6, (name, axis)
            if name != HUB_PLATE_PART_NAME:
                # ⚠️ 中央部以外は**ぴったり一致**する（緩い宣言を黙って許さない）。
                assert measured_mm == pytest.approx(declared_mm, abs=1e-6), (name, axis)
        assert check_envelope(name, envelope, params.printing) == (), name

    # ⚠️ **中央部だけは保守側に広く宣言している**——舌の先端が描く円をそのまま
    # 採っており、3方向の舌の間では実形状がその円の内側に退く（`build_drive_base`
    # の「中央部の外接箱は舌の張り出しを含む」）。⚠️ **緩さを数値で固定する**：
    # 黙って広がれば、いつか「収まらないのに収まっている」と読める側へ倒れる。
    hub_size = parts[HUB_PLATE_PART_NAME].solid.bounding_box().size
    hub_declared = declared[HUB_PLATE_PART_NAME]
    assert hub_declared.x_mm == pytest.approx(156.8, abs=1e-6)
    assert hub_size.X == pytest.approx(138.4, abs=1e-3)
    assert hub_size.Y == pytest.approx(142.893, abs=1e-3)
    assert hub_declared.x_mm - hub_size.X < 20.0


@requires_cad
def test_the_assembled_machine_has_no_interference_and_the_check_can_see_one(
    shipped: tuple[Any, Any]
) -> None:
    """組み上がり状態でどの2部品も干渉しない（要件 9.1）。

    ⚠️ **`assembled_parts` が据え付けを持つ**——アームと配線ガイドは点数ぶん
    同一のソリッドであり、回さずに比べれば自分自身と重なる。⚠️ **ゴミ箱も
    含まれる**（要件 9.1 の「搭載物との干渉」）。

    ⚠️ **空振りでないこと**: 段を缶の底の高さまで下げれば、アダプタの床と当たる
    ——⚠️ **同じ facility がそれを全件の値として返す**（例外にしない）。
    """
    from build123d import Location

    params, layout = shipped
    placed = assembled_parts(params, layout)
    assert TRASH_CAN_PART_NAME in {part.name for part in placed}
    assert not any(
        part.name.startswith(f"{SERVICE_STAND_PART_NAME}_") for part in placed
    )
    assert assembled_interferences(placed) == ()

    lowered = tuple(
        part
        if part.name != BOARD_DECK_PART_NAME
        else BuiltPart(
            name=part.name,
            solid=part.solid.moved(Location((0.0, 0.0, -20.0))),
            metrics=part.metrics,
        )
        for part in placed
    )
    violations = assembled_interferences(lowered)
    assert violations, "段を 20mm 下げても干渉が出ないなら、この検査は何も見ていない"
    assert all(violation.overlap_mm3 > 0.0 for violation in violations)
    assert any(
        BOARD_DECK_PART_NAME in (violation.left, violation.right)
        for violation in violations
    )


@requires_cad
def test_the_part_masses_come_from_the_measured_volumes_and_the_upstream_density(
    shipped: tuple[Any, Any], parts: dict[str, Any]
) -> None:
    """質量の目安が**実形状の体積**と上流の材料密度から出る（要件 7.9）。

    ⚠️ **寸法からの再計算ではない。** 体積は構築したソリッドから抽出した値で
    あり、質量はそれを上流 `estimate_mass_g` へ渡した結果である。
    """
    params, layout = shipped
    built = build_parts(params, layout)
    masses = {mass.part_name: mass for mass in part_masses(built, params.printing)}
    assert set(masses) == set(parts)
    for name, part in parts.items():
        measured_mm3 = float(part.solid.volume)
        assert masses[name].volume_mm3 == pytest.approx(measured_mm3, rel=1e-12)
        assert masses[name].mass_g == pytest.approx(
            measured_mm3 / 1000.0 * params.printing.material_density_g_cm3, rel=1e-12
        )
    # ⚠️ **目安であって合否条件ではない**——それでも「0 グラムの部品」は形の破綻である。
    assert min(mass.mass_g for mass in masses.values()) > 0.0


# ---------------------------------------------------------------------------
# 13. 接合面の法線と積層方向、搭載物の最下面（タスク 4.4 / 要件 2.8, 2.9, 7.1）
#
# ⚠️ **`joints` の宣言は形状を見ていない。** `joints` は build123d を import
# できない層であり（モジュール docstring「実形状との一致は
# `test_chassis_invariants.py`（`cad` extra、タスク 4.4）が検査する」）、
# `print_normal_axis` は**設計の決定を表明した文字列**にすぎない。上の 1〜12 節は
# 接合部の家族ごとに**当たり面の面積**を実形状と突き合わせたが、⚠️ **面積は
# 向きについて何も言わない**——法線が積層方向を向いていても面積は変わらない。
#
# 本節が足すのは3つである。
#
#   - **全数であること**: 記録された接合部の**すべて**に実形状の面が対応する。
#     ⚠️ **家族ごとの検査は、家族が増えたときに黙って素通りする。**
#   - **向きが宣言どおりであること**: 面の法線を**面から読み出して**分類し、
#     宣言した軸（`x` は半径方向、`y` は接線方向。`ALLOWED_PRINT_NORMAL_AXES`）と
#     突き合わせる。⚠️ **法線を検索条件に与えて「見つかった」と言わない**
#     ——それでは向きは仮定であって観測ではない（1〜12 節の `_planar_faces_on_plane`
#     は面積を測るために法線を条件に使っている。ここでは条件に使わない）。
#   - **積層方向が存在すること**: 部品ごとに、⚠️ **その部品の接合面のどれとも
#     一致しない向き**が1つ以上あること。要件 2.8 が禁じるのは
#     「接合面の法線が積層方向と一致する配置」であり、⚠️ そのような向きが
#     1つも無ければ、どう寝かせて造形しても禁忌を避けられない。
#
# ⚠️ **対象は `JointSpec` として記録された接合部だけである**（design.md 決定 4b
# 「⚠️ **要件 2.8 が禁じるのは層間剥離で荷重を受ける継手であり、圧縮の座では
# ない**」）。面で押し合う圧縮の座——アダプタの床の上面（缶の縁が載る座）、
# トレイのポケットの縁の上端（回り止め。12 節）、立ち上がりの下端の環——は
# 接合部ではなく、法線が積層方向を向いていてよい。⚠️ **その線引きを注釈では
# なく形で固定する**のが
# `test_counting_the_compression_seat_as_a_joint_leaves_no_layer_direction`
# である。
# ---------------------------------------------------------------------------

_RADIAL_CLASS = "radial"
"""法線が機体の半径方向を向く面（`joints` の `print_normal_axis == "x"`）。"""

_TANGENTIAL_CLASS = "tangential"
"""法線が機体の接線方向を向く面（`joints` の `print_normal_axis == "y"`）。"""

_LAYER_CLASS = "layer"
"""法線が鉛直（機体座標の `z`）を向く面。⚠️ **平置きで造形すれば積層方向である。**"""

_SKEW_CLASS = "skew"
"""上のどれでもない向き。⚠️ **「その他」を黙って通さないために名前を与える。**"""

_DIRECTION_TOL = 1e-6
"""単位ベクトルの向きが「一致する」と言える内積の許容差。"""

_SPAN_TOL = 1e-9
"""法線の並びが張る空間の次元を数えるときの、直交化残差の下限。"""


def _axis_class(angle_deg: float, unit: tuple[float, float, float]) -> str:
    """法線を、その接合部の**station（角度）**の円筒座標系で分類する。

    ⚠️ **面の中心の角度ではなく接合部の角度で見る。** 座は station から半径
    方向・接線方向へ広がっており、面の中心はその角度から外れている——面の中心の
    角度で組んだ基底で測れば、半径方向の面が「斜め」に見える。
    """
    radians = math.radians(angle_deg)
    radial = (math.cos(radians), math.sin(radians), 0.0)
    tangential = (-math.sin(radians), math.cos(radians), 0.0)
    if abs(abs(unit[2]) - 1.0) < _DIRECTION_TOL:
        return _LAYER_CLASS
    if abs(unit[2]) > _DIRECTION_TOL:
        return _SKEW_CLASS
    if abs(sum(a * b for a, b in zip(unit, radial))) > 1.0 - _DIRECTION_TOL:
        return _RADIAL_CLASS
    if abs(sum(a * b for a, b in zip(unit, tangential))) > 1.0 - _DIRECTION_TOL:
        return _TANGENTIAL_CLASS
    return _SKEW_CLASS


def _outward_radial_boss_region(
    solid: Any, *, angle_deg: float, height_mm: float, radius_mm: float
) -> Any:
    """半径方向のボルトの軸に同軸な円筒のうち、⚠️ **機体の軸から外向きの半分**。

    ⚠️ **`_radial_boss_region` は両側へ伸びる。** 面積を測るときは法線と半径で
    絞るため問題にならないが、⚠️ **法線を読み出す側では反対側（角度 +180°）に
    ある別の断片の割り面が混ざる**——出荷の配置では保持ボルトの角度 60° の
    反対側 240° がちょうど断片の割り面であり、接線方向の面が2枚拾われる。
    それらはどの接合部の当たり面でもない。
    """
    from build123d import Align, Cylinder, Location, Rotation

    return solid & (
        Rotation(0, 0, angle_deg)
        * Location((0.0, 0.0, height_mm))
        * Rotation(0, 90, 0)
        * Cylinder(radius_mm, _PROBE_MM, align=(Align.CENTER, Align.CENTER, Align.MIN))
    )


def _planar_face_normals(region: Any) -> list[Any]:
    """切り出した範囲に残る**平面**の法線を、⚠️ **面から読み出して**返す。

    ⚠️ **円筒面は返さない**——切り出しに使う円筒そのものの側面が混ざるためで
    ある（切り出しの道具を測ってしまう）。
    """
    from build123d import GeomType

    return [
        face.normal_at()
        for face in region.faces()
        if face.geom_type == GeomType.PLANE
    ]


def _unit(normal: Any) -> tuple[float, float, float]:
    """`build123d` の法線を素のタプルへ落とす（⚠️ 正規化はしない。既に単位である）。"""
    return (float(normal.X), float(normal.Y), float(normal.Z))


def _trough_faces(leg: Any, geometry: StandGeometry) -> list[Any]:
    """脚の谷（ホイールの等距離面）の面を、⚠️ **幾何量だけで**選ぶ。

    条件は「円筒面であること」と「その面のどの点も車軸の線から
    `socket_radius_mm` の距離にあること」だけである（生成名も面の数も使わない）。
    車軸の線は `y = 0`・`z = wheel_center_height_mm` を通り、脚の局所座標の
    `x` 方向へ伸びる——⚠️ **その向きは `build_service_stand_legs` が決めており、
    ここで仮定しているのではない**（下の `test_the_stand_trough_...` が実形状から
    その向きを取り出す）。
    """
    from build123d import GeomType

    selected: list[Any] = []
    for face in leg.faces():
        if face.geom_type != GeomType.CYLINDER:
            continue
        points = [face.position_at(u, 0.5) for u in (0.02, 0.25, 0.5, 0.75, 0.98)]
        if all(
            abs(
                math.hypot(
                    float(point.Y), float(point.Z) - geometry.wheel_center_height_mm
                )
                - geometry.socket_radius_mm
            )
            < 1e-6
            for point in points
        ):
            selected.append(face)
    return selected


@pytest.fixture(scope="module")
def realised_joint_faces(
    shipped: tuple[Any, Any],
    parts: dict[str, Any],
    drive_base: Any,
    adapter: Any,
    adapter_parts: tuple[Any, ...],
    tray: Any,
    deck: Any,
    board_deck_solids: tuple[Any, ...],
    geometry: StandGeometry,
    legs: tuple[Any, ...],
) -> dict[str, tuple[tuple[str, float, tuple[float, float, float]], ...]]:
    """記録された接合部ごとに、⚠️ **実形状の接合面の法線**を測って返す。

    値は `(部品名, station の角度, 法線)` の並びである。⚠️ **法線は面から読み
    出す**——検索条件には与えない。

    ⚠️ **接合部の名前をここで書き下している**のは、名前の一覧が `derive_joints`
    の側にあり、⚠️ **突き合わせが「全数か」を言えるようにするため**である
    （`test_every_recorded_joint_shows_a_face_...` が集合の一致を見る）。
    家族が増えれば、ここに面を測る手口が無い接合部として現れる。
    """
    _, layout = shipped
    faces: dict[str, tuple[tuple[str, float, tuple[float, float, float]], ...]] = {}

    # 中央部↔モータ取付部: 二股の壁（ボルト頭とナットが当たる面）。据え付け前の
    # 局所座標であり、station は 0°（`build_drive_base` はアームを回さず返す）。
    for index in range(1, len(layout.wheel_angles_deg) + 1):
        entries: list[tuple[str, float, tuple[float, float, float]]] = []
        for radius_mm in drive_base.bolt_radii_mm:
            region = _tangential_boss_region(
                parts[f"{MOTOR_ARM_PART_NAME}_{index}"].solid,
                angle_deg=0.0,
                radius_mm=radius_mm,
                height_mm=drive_base.bolt_height_mm,
                probe_mm=drive_base.boss_diameter_mm / 2.0,
            )
            entries += [
                (f"{MOTOR_ARM_PART_NAME}_{index}", 0.0, _unit(normal))
                for normal in _planar_face_normals(region)
            ]
        faces[f"{HUB_PLATE_PART_NAME}__{MOTOR_ARM_PART_NAME}_{index}"] = tuple(entries)

    # 中央部↔アダプタ断片: 裾の座ぐり（半径方向）。
    for index in range(1, adapter.segment_count + 1):
        entries = []
        for angle_deg in adapter.mount_bolt_angles_deg[index - 1]:
            region = _outward_radial_boss_region(
                adapter_parts[index - 1],
                angle_deg=angle_deg,
                height_mm=adapter.mount_bolt_height_mm,
                radius_mm=adapter.boss_diameter_mm / 2.0 + _EPS_MM,
            )
            entries += [
                (f"{ADAPTER_SEGMENT_PART_NAME}_{index}", angle_deg, _unit(normal))
                for normal in _planar_face_normals(region)
            ]
        faces[f"{HUB_PLATE_PART_NAME}__{ADAPTER_SEGMENT_PART_NAME}_{index}"] = tuple(
            entries
        )

    # アダプタ↔ゴミ箱: 保持ボルトの座ぐり（半径方向）。⚠️ 断片をまたぐ1件の接合部。
    entries = []
    for angle_deg in adapter.retention_bolt_angles_deg:
        for index, solid in enumerate(adapter_parts, start=1):
            region = _outward_radial_boss_region(
                solid,
                angle_deg=angle_deg,
                height_mm=adapter.retention_bolt_height_mm,
                radius_mm=adapter.boss_diameter_mm / 2.0 + _EPS_MM,
            )
            if _volume(region) == 0.0:
                continue
            entries += [
                (f"{ADAPTER_SEGMENT_PART_NAME}_{index}", angle_deg, _unit(normal))
                for normal in _planar_face_normals(region)
            ]
    faces[f"adapter__{TRASH_CAN_PART_NAME}"] = tuple(entries)

    # モータ取付部↔バッテリトレイ: 耳の外面（接線方向）。
    entries = []
    for radius_mm in tray.bolt_radii_mm:
        region = _tangential_boss_region(
            parts[BATTERY_TRAY_PART_NAME].solid,
            angle_deg=tray.arm_angle_deg,
            radius_mm=radius_mm,
            height_mm=tray.bolt_height_mm,
            probe_mm=tray.boss_diameter_mm / 2.0,
        )
        entries += [
            (BATTERY_TRAY_PART_NAME, tray.arm_angle_deg, _unit(normal))
            for normal in _planar_face_normals(region)
        ]
    faces[f"{MOTOR_ARM_PART_NAME}_{tray.arm_index}__{BATTERY_TRAY_PART_NAME}"] = tuple(
        entries
    )

    # アダプタ↔段積み土台: 立ち上がりの外周が床の内縁に掴まれる帯（円筒面）。
    # ⚠️ **平面ではない**——法線は帯の上を掃くため、複数の母線で読み出す。
    entries = []
    band = _full_cylinder(
        _PROBE_MM, (adapter.floor_bottom_height_mm, adapter.floor_top_height_mm)
    )
    for index, solid in enumerate(board_deck_solids, start=1):
        name = (
            BOARD_DECK_PART_NAME
            if deck.board_segment_count == 1
            else f"{BOARD_DECK_PART_NAME}_{index}"
        )
        region = solid & band
        for face in _cylindrical_faces_at_radius(region, deck.riser_outer_radius_mm):
            for u in (0.02, 0.25, 0.5, 0.75, 0.98):
                point = face.position_at(u, 0.5)
                entries.append(
                    (
                        name,
                        math.degrees(math.atan2(float(point.Y), float(point.X))),
                        _unit(face.normal_at(point)),
                    )
                )
    faces[DECK_SEAT_JOINT_NAME] = tuple(entries)

    # 段どうし: 立ち上がり側の座ぐり（半径方向）。
    for index, angles_deg in enumerate(deck.deck_bolt_angles_deg, start=1):
        entries = []
        for angle_deg in angles_deg:
            for solid_index, solid in enumerate(board_deck_solids, start=1):
                name = (
                    BOARD_DECK_PART_NAME
                    if deck.board_segment_count == 1
                    else f"{BOARD_DECK_PART_NAME}_{solid_index}"
                )
                region = _outward_radial_boss_region(
                    solid,
                    angle_deg=angle_deg,
                    height_mm=deck.deck_bolt_height_mm,
                    radius_mm=deck.boss_diameter_mm / 2.0 + _EPS_MM,
                )
                if _volume(region) == 0.0:
                    continue
                entries += [
                    (name, angle_deg, _unit(normal))
                    for normal in _planar_face_normals(region)
                ]
        faces[f"{BOARD_DECK_PART_NAME}__{CATCH_DECK_PART_NAME}_{index}"] = tuple(entries)

    # 整備スタンド↔ホイール: 谷（円筒面）。⚠️ **station の概念が無い**——法線は
    # 谷の上を掃くため、角度は 0° を置く（分類には使わない。下の専用の検査が見る）。
    for index, leg in enumerate(legs, start=1):
        entries = []
        for face in _trough_faces(leg.solid, geometry):
            for u in (0.02, 0.25, 0.5, 0.75, 0.98):
                entries.append(
                    (
                        f"{SERVICE_STAND_PART_NAME}_{index}",
                        0.0,
                        _unit(face.normal_at(face.position_at(u, 0.5))),
                    )
                )
        faces[f"{SERVICE_STAND_PART_NAME}_{index}__wheel_{index}"] = tuple(entries)

    return faces


_DECLARED_AXIS_CLASSES = {
    "x": _RADIAL_CLASS,
    "y": _TANGENTIAL_CLASS,
}
"""`print_normal_axis` が名指す軸と、機体座標での面の向きの対応。

⚠️ **`joints.ALLOWED_PRINT_NORMAL_AXES` の docstring がこの対応の正である**
（「`x` は半径方向、`y` は接線方向の面である」）。⚠️ **軸が増えたら気付ける
ようにする**——下の検査が鍵の集合を `ALLOWED_PRINT_NORMAL_AXES` と突き合わせる。
"""


def _curved_cradle_joint_names(shipped: tuple[Any, Any]) -> frozenset[str]:
    """接合面が**平面ではない**接合部（整備スタンドの谷）の名前。

    ⚠️ **谷は円筒面であり、法線は面の上を掃く。** 半径方向・接線方向の
    どちらか一方に分類できる面ではないため、軸の突き合わせの対象にしない
    ——代わりに `test_the_stand_trough_leaves_only_the_axle_free_for_the_layer_direction`
    が「掃いた先に何が無いか」を見る。⚠️ **件数は脚数から導く**（ここで数え直さない）。
    """
    params, _ = shipped
    return frozenset(
        f"{SERVICE_STAND_PART_NAME}_{index}__wheel_{index}"
        for index in range(1, params.chassis.stand.leg_count + 1)
    )


@requires_cad
def test_every_recorded_joint_shows_a_face_whose_normal_is_the_axis_it_declares(
    shipped: tuple[Any, Any],
    realised_joint_faces: dict[str, tuple[tuple[str, float, tuple[float, float, float]], ...]],
) -> None:
    """⚠️ **記録された接合部が全数、宣言どおりの向きの面を実形状に持つ**（要件 2.8, 2.9）。

    ⚠️ **本検査の主題は面積ではなく向きである。** 1〜12 節は当たり面の**面積**を
    家族ごとに突き合わせたが、面積は法線について何も言わない——座を寝かせても
    面積は変わる必要が無い。ここでは法線を**面から読み出して**分類し、
    `print_normal_axis` と突き合わせる。

    ⚠️ **全数であることが主張の半分である。** 家族ごとの検査は、接合部の家族が
    増えたときに黙って素通りする（本 Spec は「宣言された機構を1つも観測できない
    テスト」を既に一度出している）。ここでは `derive_joints` が返す名前の集合と、
    面を測れた接合部の集合が**一致する**ことを見る。
    """
    from chassis_mechanism.joints import ALLOWED_PRINT_NORMAL_AXES, LAYER_NORMAL_AXIS

    params, layout = shipped
    assert set(_DECLARED_AXIS_CLASSES) == set(ALLOWED_PRINT_NORMAL_AXES)
    assert LAYER_NORMAL_AXIS not in _DECLARED_AXIS_CLASSES

    specs = {spec.name: spec for spec in derive_joints(layout, params)}
    assert set(realised_joint_faces) == set(specs)

    curved = _curved_cradle_joint_names(shipped)
    assert curved <= set(specs)
    assert len(curved) == params.chassis.stand.leg_count

    for name, spec in specs.items():
        entries = realised_joint_faces[name]
        assert entries, f"{name}: 実形状に接合面が1つも無い"
        if name in curved:
            continue
        observed = {
            _axis_class(angle_deg, normal) for _, angle_deg, normal in entries
        }
        assert observed == {_DECLARED_AXIS_CLASSES[spec.print_normal_axis]}, (
            name,
            spec.print_normal_axis,
            sorted(observed),
        )
        assert _LAYER_CLASS not in observed, name


@requires_cad
def test_a_seat_cut_across_the_layer_direction_is_caught(
    shipped: tuple[Any, Any], drive_base: Any, parts: dict[str, Any]
) -> None:
    """⚠️ **空振りでないこと**: 座を寝かせれば、同じ手口が積層方向を向いた面を拾う。

    ⚠️ **上の検査は「見つかった面が宣言どおりの向きである」と述べている。**
    それが空振りでないためには、⚠️ **向きの違う面が置かれたときに拾えること**を
    示さなければならない——法線を検索条件に与える測り方では、寝かせた座は
    「面が無い」ではなく「見えない」になる。

    ここでは構築済みのアームの座の範囲へ**測るためだけに**水平な座ぐりを削り、
    同じ手口が `layer` を返すことを見る。⚠️ **削っていないアームは
    `tangential` だけを返す**（この反例が向きの判定そのものを疑っていない証拠）。
    """
    _, _ = shipped
    arm = parts[f"{MOTOR_ARM_PART_NAME}_1"].solid
    radius_mm = drive_base.bolt_radii_mm[0]
    boss_radius_mm = drive_base.boss_diameter_mm / 2.0

    def classes(solid: Any) -> set[str]:
        region = _tangential_boss_region(
            solid,
            angle_deg=0.0,
            radius_mm=radius_mm,
            height_mm=drive_base.bolt_height_mm,
            probe_mm=boss_radius_mm,
        )
        return {
            _axis_class(0.0, _unit(normal)) for normal in _planar_face_normals(region)
        }

    assert classes(arm) == {_TANGENTIAL_CLASS}

    # 座の範囲に、法線が上を向く平面（＝平置きなら積層方向を向く面）を作る。
    pocket_top_mm = drive_base.bolt_height_mm + boss_radius_mm / 2.0
    lying_seat = arm - _box(
        (radius_mm - boss_radius_mm / 2.0, radius_mm + boss_radius_mm / 2.0),
        (-_PROBE_MM, _PROBE_MM),
        (pocket_top_mm, _PROBE_MM),
    )
    # ⚠️ 削った跡には半径方向の側面も現れる——見るのは **`layer` が現れること**である。
    assert _LAYER_CLASS in classes(lying_seat)


def _orthonormal_span(
    normals: tuple[tuple[float, float, float], ...]
) -> list[tuple[float, float, float]]:
    """法線の並びが張る空間の正規直交基底（⚠️ 3 次元のグラム・シュミット）。"""
    basis: list[tuple[float, float, float]] = []
    for normal in normals:
        residual = list(normal)
        for vector in basis:
            projection = sum(a * b for a, b in zip(residual, vector))
            residual = [a - projection * b for a, b in zip(residual, vector)]
        length = math.sqrt(sum(a * a for a in residual))
        if length > _SPAN_TOL:
            basis.append(tuple(a / length for a in residual))  # type: ignore[arg-type]
    return basis


def _layer_directions(
    normals: tuple[tuple[float, float, float], ...]
) -> list[tuple[float, float, float]]:
    """どの法線とも直交する向きの正規直交基底＝**積層方向として採れる向き**。

    ⚠️ **空であることが要件 2.8 の破れである**——接合面の法線が3次元を張って
    しまえば、どう寝かせて造形しても、どれかの面の法線が積層方向と一致する。
    """
    basis = _orthonormal_span(normals)
    free: list[tuple[float, float, float]] = []
    for axis in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)):
        residual = list(axis)
        for vector in basis + free:
            projection = sum(a * b for a, b in zip(residual, vector))
            residual = [a - projection * b for a, b in zip(residual, vector)]
        length = math.sqrt(sum(a * a for a in residual))
        if length > _SPAN_TOL:
            free.append(tuple(a / length for a in residual))  # type: ignore[arg-type]
    return free


def _is_free_direction(
    normals: tuple[tuple[float, float, float], ...],
    direction: tuple[float, float, float],
) -> bool:
    """`direction` がどの法線とも一致していない（＝直交している）こと。"""
    return all(
        abs(sum(a * b for a, b in zip(normal, direction))) < _DIRECTION_TOL
        for normal in normals
    )


def _normals_by_part(
    realised: dict[str, tuple[tuple[str, float, tuple[float, float, float]], ...]]
) -> dict[str, tuple[tuple[float, float, float], ...]]:
    """接合面の法線を、⚠️ **それを持つ部品ごとに**まとめ直す。

    ⚠️ **造形姿勢は部品の性質であり、接合部の性質ではない。** 1つの部品が複数の
    接合部に加わるとき、積層方向はそのすべてを同時に避けなければならない。
    """
    by_part: dict[str, list[tuple[float, float, float]]] = {}
    for entries in realised.values():
        for part_name, _, normal in entries:
            by_part.setdefault(part_name, []).append(normal)
    return {name: tuple(values) for name, values in by_part.items()}


@requires_cad
def test_every_part_admits_a_layer_direction_that_no_joint_face_of_it_takes(
    shipped: tuple[Any, Any],
    realised_joint_faces: dict[str, tuple[tuple[str, float, tuple[float, float, float]], ...]],
) -> None:
    """⚠️ **接合面の法線が積層方向と一致する部品が無い**（要件 2.8 / A-5）。

    ⚠️ **`print_normal_axis` は接合部ごとの宣言であり、部品については何も言わない。**
    造形姿勢は部品の性質である——1つの部品が複数の接合部に加わるとき、積層方向は
    そのすべての法線を同時に避けなければならず、⚠️ **避けられる向きが1つも
    無ければ、宣言がどれも `z` でなくても要件 2.8 は満たせない。**

    ここでは部品ごとに、⚠️ **実形状の接合面の法線が張る空間**を求め、その直交
    補空間（＝積層方向として採れる向き）が空でないことを見る。⚠️ 出荷の機体では
    駆動ベース・アダプタ・段・トレイが鉛直（平置き）を採れ、⚠️ **整備スタンドの
    脚だけは車軸方向しか採れない**——谷の法線が鉛直を含むためである
    （下の `test_the_stand_trough_...` がその向きを実形状から取り出す）。
    """
    params, _ = shipped
    by_part = _normals_by_part(realised_joint_faces)
    upright = (0.0, 0.0, 1.0)

    stand_names = {
        f"{SERVICE_STAND_PART_NAME}_{index}"
        for index in range(1, params.chassis.stand.leg_count + 1)
    }
    assert stand_names <= set(by_part)

    # ⚠️ **覆えている部品をその場で固定する。** 接合部の当たり面は**片側の部材**に
    # 実現しており（ボルト頭が当たる側。1〜12 節が測っているのと同じ面）、
    # ⚠️ **相手側の部材はこの並びに現れない**——中央部（相手はアームとアダプタ
    # 断片）、受け止めデッキ（相手は基板デッキ）がそれである。配線ガイドは
    # そもそも `JointSpec` を持たない（`joints.ASSUMPTIONS`: 取付ねじは構造の
    # 接合部ではない）。⚠️ **黙って減れば抜け道になる**ため集合で押さえる。
    assert set(by_part) == (
        {f"{MOTOR_ARM_PART_NAME}_{index}" for index in range(1, 4)}
        | {f"{ADAPTER_SEGMENT_PART_NAME}_{index}" for index in range(1, 4)}
        | {BATTERY_TRAY_PART_NAME, BOARD_DECK_PART_NAME}
        | stand_names
    ), sorted(by_part)
    assert HUB_PLATE_PART_NAME not in by_part

    for name, normals in sorted(by_part.items()):
        free = _layer_directions(normals)
        assert free, f"{name}: 接合面の法線が3次元を張っており、積層方向が採れない"
        if name in stand_names:
            # 谷は鉛直を向く面を含む。⚠️ **平置きでは造形できない脚である。**
            assert not _is_free_direction(normals, upright), name
            assert len(free) == 1, name
        else:
            assert _is_free_direction(normals, upright), name


@requires_cad
def test_counting_the_compression_seat_as_a_joint_leaves_no_layer_direction(
    shipped: tuple[Any, Any],
    adapter: Any,
    adapter_parts: tuple[Any, ...],
    realised_joint_faces: dict[str, tuple[tuple[str, float, tuple[float, float, float]], ...]],
) -> None:
    """⚠️ **空振りでないこと**、かつ ⚠️ **要件 2.8 の範囲そのものの固定である。**

    design.md 決定 4b は「⚠️ **要件 2.8 が禁じるのは層間剥離で荷重を受ける継手で
    あり、圧縮の座ではない**」と範囲を定めている。⚠️ **その線引きは注釈のままでは
    観測できない。**

    アダプタの床の上面は、底を抜いた缶の縁が載る**圧縮の座**である（決定 4b:
    「外径と切り取り径の差として残る縁が、そのまま缶の重量を受ける座面になる」）。
    その面の法線は鉛直であり、⚠️ **これを接合面として数えた瞬間、アダプタ断片の
    法線は3次元を張って積層方向が1つも採れなくなる**——上の検査は本当に噛む。

    ⚠️ **数えない側（＝`JointSpec` として記録された接合部だけ）では鉛直が採れる**
    ことを同時に固定する（この反例が判定そのものを疑っていない証拠）。
    """
    _, _ = shipped
    by_part = _normals_by_part(realised_joint_faces)
    name = f"{ADAPTER_SEGMENT_PART_NAME}_1"
    joint_normals = by_part[name]
    free = _layer_directions(joint_normals)
    assert len(free) == 1, free
    assert abs(abs(free[0][2]) - 1.0) < 1e-6, free

    # 缶の縁が載る座（床の上面）を**実形状から**取り出す。
    seats = _planar_faces_on_plane(
        adapter_parts[0], (0.0, 0.0, 1.0), adapter.floor_top_height_mm
    )
    assert seats, "缶の縁が載る座が実形状に無い"
    assert sum(float(face.area) for face in seats) > 0.0
    seat_normals = tuple(_unit(face.normal_at()) for face in seats)
    assert all(abs(abs(normal[2]) - 1.0) < 1e-6 for normal in seat_normals)

    assert _layer_directions(joint_normals + seat_normals) == []


@requires_cad
def test_the_stand_trough_leaves_only_the_axle_free_for_the_layer_direction(
    shipped: tuple[Any, Any],
    geometry: StandGeometry,
    legs: tuple[Any, ...],
    realised_joint_faces: dict[str, tuple[tuple[str, float, tuple[float, float, float]], ...]],
) -> None:
    """⚠️ **谷は圧縮の受け皿であり、造形姿勢を1つに決めてしまう**（要件 2.8, 5.6）。

    谷はホイールの等距離面（車軸と同軸の円筒）であり、⚠️ **法線は面の上を掃く**
    ——谷の底では鉛直、両側の壁では接線方向へ倒れる。したがって
    ⚠️ **法線と一致しない向きは車軸方向ただ1つ**であり、脚の造形姿勢はそれで
    決まる（`joints` モジュール docstring:「締結の向きが鉛直になる接合部が
    あるが、それは『その部品を横倒しで造形する』ことで避ける」）。

    ⚠️ **本検査は車軸の向きを仮定しない。** 掃いた法線から直交補空間を求め、
    その向きが**谷の円筒の軸**——`build_service_stand_legs` が抜いた円筒の
    向き——と一致することを、⚠️ **谷の面の点が車軸の線から等距離であること**で
    確かめる。

    ⚠️ **空振りでないこと**: 同じ判定に鉛直を渡せば「採れない」と答える
    （谷の底の法線がそれだからである）。
    """
    params, _ = shipped
    for index, leg in enumerate(legs, start=1):
        name = f"{SERVICE_STAND_PART_NAME}_{index}__wheel_{index}"
        normals = tuple(normal for _, _, normal in realised_joint_faces[name])
        assert normals, name

        free = _layer_directions(normals)
        assert len(free) == 1, (name, free)
        axle = free[0]
        # ⚠️ 谷の面のどの点も、この向きの線から `socket_radius_mm` だけ離れている
        # ——すなわちこの向きが円筒の軸（＝車軸）である。
        assert abs(abs(axle[0]) - 1.0) < 1e-6, (name, axle)
        for face in _trough_faces(leg.solid, geometry):
            for u in (0.02, 0.5, 0.98):
                point = face.position_at(u, 0.5)
                assert math.hypot(
                    float(point.Y), float(point.Z) - geometry.wheel_center_height_mm
                ) == pytest.approx(geometry.socket_radius_mm, abs=1e-6)

        # ⚠️ 鉛直は採れない（谷の底の法線がそれである）。
        assert not _is_free_direction(normals, (0.0, 0.0, 1.0)), name
        assert any(abs(normal[2] - 1.0) < 1e-6 for normal in normals), name
        # ⚠️ 壁は接線方向へ倒れており、モータ反力を受ける向きの面が実在する。
        assert any(abs(normal[1]) > 0.9 for normal in normals), name

    assert len(legs) == params.chassis.stand.leg_count


@requires_cad
def test_the_battery_tray_floor_is_below_every_recorded_mounted_item(
    shipped: tuple[Any, Any], tray: Any, parts: dict[str, Any]
) -> None:
    """⚠️ **バッテリが「全搭載物の中で最も低い」ことを、搭載物の記録に対して見る**（要件 7.1）。

    ⚠️ **`test_the_battery_is_the_lowest_mounted_item_on_the_machine` とは
    見ている集合が違う。** あちらは `build_parts` が返す**造形部品**を比べており、
    ⚠️ **搭載物そのもの（バッテリ・基板・端子台）は1件も入っていない**——
    部品の底面を比べても、その部品が何をどの高さで保持しているかは分からない。

    ⚠️ **「全搭載物」の正は `ChassisParams.mass_items()` である**（要件 7.8:
    「各搭載物の質量と保持高さを記録し」）。合成重心の見積もりが読むのと同じ
    並びであり、⚠️ **要否が未決の搭載物は現れない**（端子台）。ここでは
    トレイの**実形状の最下面**を、その並びのすべての保持高さと比べる。

    ⚠️ **床との隙間の一覧（`clearance.CLEARANCE_ITEM_NAMES`）とは別の集合である。**
    あちらにはモータ胴体・ブラケットが入っており、⚠️ **それらはトレイより低い**
    ——駆動ユニットは機体が**担ぐ**搭載物ではなく、機体の端部にぶら下がる駆動系
    そのものである（要件 5.3 が整備スタンドの支持面として名指しする面である）。
    要件 7.1 が言う「最下部」は搭載物の中での順序であり、駆動ユニットを含めた
    機体全体の最下点ではない。
    """
    params, _ = shipped
    items = params.chassis.mass_items()
    assert items, "搭載物の記録が空である（要件 7.8）"
    assert "battery" in {item.name for item in items}

    tray_bottom_mm = float(parts[BATTERY_TRAY_PART_NAME].solid.bounding_box().min.Z)
    assert tray_bottom_mm == pytest.approx(tray.floor_bottom_height_mm, abs=1e-6)

    by_name = {item.name: item for item in items}
    battery_hold_mm = by_name["battery"].hold_height_mm
    # ⚠️ 記録された保持高さは、実形状のポケットの中にある。
    assert tray_bottom_mm < battery_hold_mm
    assert tray.battery_bottom_height_mm <= battery_hold_mm <= tray.battery_top_height_mm

    for item in items:
        assert tray_bottom_mm < item.hold_height_mm, item.name
        if item.name == "battery":
            continue
        assert battery_hold_mm < item.hold_height_mm, item.name

    # ⚠️ **駆動ユニットは搭載物ではない**（この検査の範囲を、比べてはならない
    # 相手を名指しすることで固定する）。
    _, layout = shipped
    assert layout.vertical.motor_body_bottom_height_mm < tray_bottom_mm


@requires_cad
def test_a_mounted_item_held_below_the_battery_is_caught(
    shipped: tuple[Any, Any], tray: Any, parts: dict[str, Any]
) -> None:
    """⚠️ **空振りでないこと**: 基板の保持高さをトレイの下へ落とせば検査は落ちる。

    ⚠️ 出荷の値では基板は 100mm、バッテリは 44mm、トレイの下面は 28mm である
    ——⚠️ **差が大きいほど「通って当たり前」に見える**ため、通らない配置を
    実際に作って、同じ比べ方が捉えることを示す。⚠️ **触っていない側は通る**。
    """
    import dataclasses

    params, _ = shipped
    tray_bottom_mm = float(parts[BATTERY_TRAY_PART_NAME].solid.bounding_box().min.Z)
    items = params.chassis.mass_items()
    battery_hold_mm = {item.name: item for item in items}["battery"].hold_height_mm
    assert all(
        tray_bottom_mm < item.hold_height_mm for item in items
    )  # ⚠️ 触っていない側は通る

    sunk = dataclasses.replace(
        params.chassis,
        board=dataclasses.replace(
            params.chassis.board, hold_height_mm=tray_bottom_mm - 1.0
        ),
    )
    sunk_items = {item.name: item for item in sunk.mass_items()}
    assert sunk_items["board"].hold_height_mm < tray_bottom_mm
    assert sunk_items["board"].hold_height_mm < battery_hold_mm
    assert not all(
        tray_bottom_mm < item.hold_height_mm for item in sunk.mass_items()
    )
    assert tray.floor_bottom_height_mm == pytest.approx(tray_bottom_mm, abs=1e-6)
