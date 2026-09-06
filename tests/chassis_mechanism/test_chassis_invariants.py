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

from typing import Any

import pytest
from catch_mechanism import check_envelope

from chassis_mechanism.config import load_params
from chassis_mechanism.joints import derive_joints
from chassis_mechanism.layout import derive_layout
from chassis_mechanism.shapes import (
    MIN_HAND_ACCESS_MM,
    StandGeometry,
    build_parts,
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
    """構築済みの脚（`build_parts` の戻り値そのもの）。"""
    params, layout = shipped
    return build_parts(params, layout)


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
