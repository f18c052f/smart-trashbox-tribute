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

import math
from typing import Any

import pytest
from catch_mechanism import check_envelope

from chassis_mechanism.config import load_params
from chassis_mechanism.joints import derive_joints
from chassis_mechanism.layout import derive_layout
from chassis_mechanism.shapes import (
    MIN_HAND_ACCESS_MM,
    StandGeometry,
    adapter_geometry,
    build_parts,
    drive_base_geometry,
    stand_geometry,
    stand_inputs,
)

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
    1.5 倍にして測った面積が動かないことが、「離れた材料を数えていない」ことの
    観測可能な形である。
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
                arm_width_mm=params.chassis.base.arm_width_mm * 1.5,
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

    from chassis_mechanism.shapes import _build_motor_arm

    params, _ = shipped
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
    measured_mm2 = _measured_bolt_seat_area_mm2(_build_motor_arm(thin), thin)

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
    """駆動ベースの部品を機体座標へ据え付ける（中央部はそのまま、アームは回す）。"""
    from build123d import Rotation

    _, layout = shipped
    placed: dict[str, Any] = {"hub_plate": parts["hub_plate"].solid}
    for index, angle_deg in enumerate(layout.wheel_angles_deg, start=1):
        placed[f"motor_arm_{index}"] = (
            Rotation(0, 0, angle_deg) * parts[f"motor_arm_{index}"].solid
        )
    return placed


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
    「切り取り径 Ø170（＝上流の平面部径）から `taper_deg` で広がる円錐」であり、
    開口の平面（＝縁が載る高さ）から始まる**——基準が移った理由は
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
    assert adapter.cut_radius_mm == pytest.approx(can.bottom_flat_diameter_mm / 2.0)
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
