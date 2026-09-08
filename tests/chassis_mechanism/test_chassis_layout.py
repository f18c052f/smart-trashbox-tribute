"""`chassis_mechanism.layout` の検査（design.md `#### Layout` / 要件 3.1, 3.2, 3.3,
3.5, 3.6, 4.1, 7.8, 7.9）。

固定するのは次の6点である。

1. **ホイール配置半径の式がここにしか無い**こと（要件 3.3 / tasks.md タスク 2.1
   「⚠️ この導出をここ以外に置かない」）。取付面までの距離を動かすと配置半径・
   アーム長・鉛直スタックが同時に動く。⚠️ **「機体中心 → 取付面」は導出値では
   なく寸法パラメータ `base.hub_center_to_mount_face_mm` である**（要件 1.1,
   1.5）——本 Spec が決める設計変数はこの1つだけであり、それを選ぶのはタスク 5.4
   である。したがってアーム長はこの値に 1:1 で追随しなければならない
   （追随しなければ、design.md 決定 1 の条件2「接合の成立」が配置半径の**範囲**を
   縛れなくなる）。
2. **取付角は等配置**であり、基準は機体 +x から反時計回りであること（要件 3.2,
   3.6）。⚠️ 輪番号と符号の**規約そのもの**は上流の制御ロジックが持つ。
3. **鉛直スタックは接地点を原点に積み上がり、逆転する入力を拒否する**
   （要件 4.1 / design.md `#### Layout` Invariants）。
4. **転倒余裕は合否条件ではない**こと（要件 3.5, 7.9）。型と記録の双方で表明され、
   ⚠️ **モジュール内のどこにも閾値との比較が無い**。
5. **出所は入力の最弱を継承し、黙って実測へ格上げされない**こと（要件 1.9 /
   design.md `#### Layout` Risks）。`bracket.mount_face_reference` が未確認である
   間、`base_radius_mm` の出所は仮値である。
6. **導出記録は入力・式・出所・結果を持つ**こと（要件 3.4 / 上流
   `configs/catch_mechanism/catch-opening.json` と同形）。
"""

from __future__ import annotations

import ast
import json
import math
from dataclasses import replace
from pathlib import Path

import pytest

from catch_mechanism import Provenance

from chassis_mechanism import layout as layout_module
from chassis_mechanism.config import (
    DEFAULT_DIMENSIONS_PATH,
    SCHEMA_VERSION,
    ResolvedParams,
    load_params,
)
from chassis_mechanism.errors import GeometryError, ParameterError
from chassis_mechanism.layout import (
    ASSUMPTIONS,
    DEFAULT_LAYOUT_PATH,
    FORMULA,
    GRAVITY_MM_S2,
    REQUIRED_INPUT_NAMES,
    TIPPING_NOTE,
    ChassisLayout,
    ObservedRollingRadius,
    TippingEstimate,
    VerticalStack,
    derive_layout,
    dump_layout,
    load_layout,
)

_LAYOUT_SOURCE: str = Path(layout_module.__file__).read_text(encoding="utf-8")

_COMPRESSION_MM: float = 0.75
"""荷重で転がり半径が縮む量（mm）の例。

research.md「最低地上高は何から決まるか」の「公称 30mm に対し荷重下で 29.25mm」
——その差である。⚠️ **設計値ではない。** 実測は要件 10.3（タスク 6.7）が持つ。
"""


# ---------------------------------------------------------------------------
# 補助
# ---------------------------------------------------------------------------


def _params() -> ResolvedParams:
    """出荷されている寸法設定を読む。"""
    return load_params(DEFAULT_DIMENSIONS_PATH)


def _with_chassis(params: ResolvedParams, **changes: object) -> ResolvedParams:
    """本 Spec 側の寸法だけを差し替えた `ResolvedParams` を作る。"""
    return replace(params, chassis=replace(params.chassis, **changes))


def _with_bracket(params: ResolvedParams, **changes: object) -> ResolvedParams:
    """ブラケットの実測値だけを差し替える。"""
    return _with_chassis(params, bracket=replace(params.chassis.bracket, **changes))


def _with_base(params: ResolvedParams, **changes: object) -> ResolvedParams:
    """駆動ベースの寸法だけを差し替える（設計変数はこの群にある）。"""
    return _with_chassis(params, base=replace(params.chassis.base, **changes))


def _all_measured(params: ResolvedParams) -> ResolvedParams:
    """全パラメータの出所を実測に置き換える（出所の継承規則の確認用）。"""
    provenance = {key: Provenance.MEASURED for key in params.chassis.provenance}
    return _with_chassis(params, provenance=provenance)


# ---------------------------------------------------------------------------
# 1. ホイール配置半径とアーム長（要件 3.1, 3.3）
# ---------------------------------------------------------------------------


def test_base_radius_is_the_sum_of_the_two_documented_terms() -> None:
    """`base_radius_mm` は「機体中心 → 取付面」と「取付面 → ホイール中心」の和である。"""
    params = _params()
    layout = derive_layout(params)
    assert layout.base_radius_mm == pytest.approx(
        layout.hub_center_to_mount_face_mm
        + params.chassis.bracket.mount_face_to_wheel_center_mm
    )


def test_hub_center_to_mount_face_is_read_from_the_dimension_parameter() -> None:
    """「機体中心 → 取付面」は寸法パラメータをそのまま読んだ値である（要件 1.1）。

    ⚠️ **導出値ではない。** これは本 Spec が決める唯一の設計変数であり、値を選ぶ
    のはタスク 5.4（要件 3.4）である。式としてコードへ埋めれば、決めるべき値が
    設定ファイルの外へ移る。
    """
    params = _params()
    layout = derive_layout(params)
    assert layout.hub_center_to_mount_face_mm == pytest.approx(
        params.chassis.base.hub_center_to_mount_face_mm
    )


def test_layout_never_builds_the_mount_face_distance_from_other_dimensions() -> None:
    """⚠️ 「機体中心 → 取付面」を他の寸法から組み立てる式がモジュール内に無い。

    かつてこの距離は `base.hub_outer_diameter_mm / 2 + bracket.outline_x_mm` と
    して**導出されていた**。その形では `hub_outer_diameter_mm` がアーム長から
    相殺され、アーム長が定数になってしまう——つまり design.md 決定 1 の条件2
    （接合の成立）が配置半径の**範囲**を縛れなくなる。⚠️ **同じ形へ戻らない
    ことを、ブラケット外形がこのモジュールに現れないことで固定する**
    （`bracket.outline_x_mm` は幾何の導出の入力ではない）。
    """
    assert "outline_x" not in _LAYOUT_SOURCE


def test_arm_length_spans_from_the_central_hub_to_the_wheel_center() -> None:
    """アーム長は中央部の外縁からホイール中心面までの半径方向の張り出しである。"""
    params = _params()
    layout = derive_layout(params)
    assert layout.arm_length_mm == pytest.approx(
        layout.base_radius_mm - params.chassis.base.hub_outer_diameter_mm / 2.0
    )
    assert layout.arm_length_mm > 0.0


def test_arm_that_cannot_hold_the_joint_bearing_area_is_rejected() -> None:
    """当たり面を確保できない配置半径は形状不正として拒否される（要件 3.10 の前提）。"""
    params = _params()
    layout = derive_layout(params)
    # ⚠️ **接合面は「アーム長 × アーム厚」である**（接線方向を法線に持つ面。
    # 要件 2.8 が積層方向 `z` を法線に持つ面を禁じる）。⚠️ `arm_width_mm` を
    # 掛けない——それはボルトの軸方向であり、その積は要件 2.8 が禁じる**水平面**
    # の面積になる。
    required = layout.arm_length_mm * params.chassis.base.arm_thickness_mm
    broken = _with_chassis(
        params,
        joint_local=replace(
            params.chassis.joint_local, min_bearing_area_mm2=required * 2.0
        ),
    )
    with pytest.raises(GeometryError) as excinfo:
        derive_layout(broken)
    message = str(excinfo.value)
    assert "arm_length_mm" in message
    assert "min_bearing_area_mm2" in message


def test_arm_that_is_thinner_than_the_bearing_area_requires_is_rejected() -> None:
    """アーム**厚**を薄くして当たり面が足りなくなる入力も、同じ検査で拒否される。

    ⚠️ **接合面の面内2軸はアーム長と厚さである。** 中央部↔モータ取付部の接合面は
    接線方向を法線に持ち（`joints` が `print_normal_axis="y"` として記録する。
    要件 2.8 / A-5 が積層方向 `z` を法線に持つ面を禁じる）、幅 `arm_width_mm` は
    **ボルトの軸そのもの**である。したがって当たり面を痩せさせるのは厚さであって
    幅ではない。
    """
    params = _params()
    layout = derive_layout(params)
    thickness = params.chassis.joint_local.min_bearing_area_mm2 / layout.arm_length_mm
    broken = _with_chassis(
        params, base=replace(params.chassis.base, arm_thickness_mm=thickness / 2.0)
    )
    with pytest.raises(GeometryError) as excinfo:
        derive_layout(broken)
    assert "arm_thickness_mm" in str(excinfo.value)


def test_the_arm_width_is_not_an_input_to_the_bearing_area_limit() -> None:
    """⚠️ **アーム幅を痩せさせても当たり面の検査は動かない**（軸の取り違えの再発防止）。

    かつてこの検査は `min_bearing_area_mm2 / arm_width_mm` を最小アーム長として
    いた——それは `arm_length × arm_width` という**水平（`z` 法線）な当たり面**を
    前提とする式であり、要件 2.8 と `joints` の `print_normal_axis="y"` の双方に
    反する。⚠️ **幅は締結の軸方向であり、当たり面の面内の量ではない。**
    幅をどれだけ細くしても幾何の導出が成立し続けることで、その取り違えが戻って
    いないことを固定する。
    """
    params = _params()
    before = derive_layout(params)
    for factor in (0.5, 0.1, 0.01):
        narrowed = derive_layout(
            _with_chassis(
                params,
                base=replace(
                    params.chassis.base,
                    arm_width_mm=params.chassis.base.arm_width_mm * factor,
                ),
            )
        )
        assert narrowed.arm_length_mm == pytest.approx(before.arm_length_mm)


def test_arm_length_grows_one_to_one_with_the_mount_face_distance() -> None:
    """⚠️ アーム長は「機体中心 → 取付面」に 1:1 で追随する（中央部の外径は固定）。

    これは design.md 決定 1 の条件2（接合の成立）が配置半径の**範囲**を縛るため
    の前提である。設計変数を動かしてもアーム長が変わらないなら、当たり面の下限は
    どの配置半径も等しく許すか等しく拒むかのどちらかになり、タスク 5.4 が
    「4条件を同時に満たす範囲」から選ぶという作業そのものが成り立たない。
    """
    params = _params()
    base = params.chassis.base
    before = derive_layout(params)
    delta_mm = 12.5
    after = derive_layout(
        _with_base(
            params,
            hub_center_to_mount_face_mm=base.hub_center_to_mount_face_mm + delta_mm,
        )
    )
    assert after.hub_center_to_mount_face_mm - before.hub_center_to_mount_face_mm == (
        pytest.approx(delta_mm)
    )
    assert after.base_radius_mm - before.base_radius_mm == pytest.approx(delta_mm)
    assert after.arm_length_mm - before.arm_length_mm == pytest.approx(delta_mm)


def test_arm_length_shrinks_one_to_one_with_the_mount_face_distance() -> None:
    """内側へ寄せればアームは同じだけ短くなる（縛りが片側だけでないことを固定する）。"""
    params = _params()
    base = params.chassis.base
    before = derive_layout(params)
    delta_mm = 12.5
    after = derive_layout(
        _with_base(
            params,
            hub_center_to_mount_face_mm=base.hub_center_to_mount_face_mm - delta_mm,
        )
    )
    assert before.arm_length_mm - after.arm_length_mm == pytest.approx(delta_mm)
    assert before.base_radius_mm - after.base_radius_mm == pytest.approx(delta_mm)


def test_the_bearing_area_limit_bounds_the_mount_face_distance_from_below() -> None:
    """⚠️ 当たり面の下限が「機体中心 → 取付面」の**下限**として効く（決定 1 条件2）。

    設計変数を内側へ寄せていくとアームが短くなり、ハブとの接合部の当たり面を
    確保できなくなった時点で `GeometryError` になる。この境界が存在することが、
    タスク 5.4 が配置半径を「範囲から選ぶ」ということの中身である。
    """
    params = _params()
    chassis = params.chassis
    layout = derive_layout(params)
    # ⚠️ 割るのはアーム**厚**である（接合面の面内2軸はアーム長と厚さ）。
    minimum_arm_mm = (
        chassis.joint_local.min_bearing_area_mm2 / chassis.base.arm_thickness_mm
    )
    assert layout.arm_length_mm > minimum_arm_mm
    slack_mm = layout.arm_length_mm - minimum_arm_mm

    # 余裕のぶんだけ内側へ寄せてもまだ成立する（境界のすぐ手前）。
    still_valid = derive_layout(
        _with_base(
            params,
            hub_center_to_mount_face_mm=(
                chassis.base.hub_center_to_mount_face_mm - slack_mm / 2.0
            ),
        )
    )
    assert still_valid.arm_length_mm > minimum_arm_mm

    # 余裕を超えて寄せると、同じ検査が対と量を示して拒否する。
    with pytest.raises(GeometryError) as excinfo:
        derive_layout(
            _with_base(
                params,
                hub_center_to_mount_face_mm=(
                    chassis.base.hub_center_to_mount_face_mm - slack_mm - 1.0
                ),
            )
        )
    message = str(excinfo.value)
    assert "arm_length_mm" in message
    assert "min_bearing_area_mm2" in message


def test_changing_the_central_hub_diameter_moves_the_arm_but_not_the_radius() -> None:
    """中央部を太くすると配置半径は動かず、アームだけが同じだけ短くなる。

    ⚠️ 配置半径は「機体中心 → 取付面」＋「取付面 → ホイール中心」だけで決まり、
    中央部の外径はそこに現れない。中央部が食い込む分はアームの張り出しから引かれる。
    """
    params = _params()
    base = params.chassis.base
    before = derive_layout(params)
    growth_mm = 20.0
    after = derive_layout(
        _with_base(
            params,
            hub_outer_diameter_mm=base.hub_outer_diameter_mm + 2.0 * growth_mm,
        )
    )
    assert after.base_radius_mm == pytest.approx(before.base_radius_mm)
    assert before.arm_length_mm - after.arm_length_mm == pytest.approx(growth_mm)


# ---------------------------------------------------------------------------
# 2. 取付角（要件 3.2, 3.6）
# ---------------------------------------------------------------------------


def test_wheel_angles_are_equally_spaced_from_the_first_wheel_angle() -> None:
    """取付角は第1輪の取付角から等間隔で生成される。"""
    params = _params()
    layout = derive_layout(params)
    count = params.chassis.base.wheel_count
    assert len(layout.wheel_angles_deg) == count
    assert layout.wheel_angles_deg[0] == pytest.approx(
        params.chassis.base.first_wheel_angle_deg
    )
    steps = [
        layout.wheel_angles_deg[index + 1] - layout.wheel_angles_deg[index]
        for index in range(count - 1)
    ]
    assert steps == pytest.approx([360.0 / count] * (count - 1))


def test_wheel_angles_are_not_renormalised_when_the_first_angle_is_negative() -> None:
    """負の第1輪角でも等間隔性が壊れない（⚠️ 折り返して規約を作り直さない）。"""
    params = _params()
    shifted = _with_chassis(
        params, base=replace(params.chassis.base, first_wheel_angle_deg=-30.0)
    )
    layout = derive_layout(shifted)
    assert layout.wheel_angles_deg == pytest.approx((-30.0, 90.0, 210.0))


def test_layout_does_not_take_the_wheel_convention_from_the_firmware_tree() -> None:
    """⚠️ 輪番号と符号の規約は上流の制御ロジックが持つ（本モジュールは受け取るだけ）。"""
    tree = ast.parse(_LAYOUT_SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("drivetrain")
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            assert not node.module.startswith("drivetrain")


# ---------------------------------------------------------------------------
# 3. 鉛直スタック（要件 4.1）
# ---------------------------------------------------------------------------


def test_vertical_stack_is_built_from_the_ground_contact_point() -> None:
    """接地点を原点として5つの高さが積み上がる。"""
    params = _params()
    chassis = params.chassis
    vertical = derive_layout(params).vertical
    radius = chassis.wheel.nominal_diameter_mm / 2.0
    assert vertical.effective_rolling_radius_mm == pytest.approx(radius)
    assert vertical.axle_center_height_mm == pytest.approx(radius)
    assert vertical.motor_body_bottom_height_mm == pytest.approx(
        radius - chassis.motor.body_diameter_mm / 2.0
    )
    assert vertical.mount_face_height_mm == pytest.approx(
        chassis.bracket.mount_face_to_contact_mm
    )
    assert vertical.fastener_bottom_height_mm == pytest.approx(
        chassis.bracket.mount_face_to_contact_mm
        - chassis.clearance.fastener_protrusion_mm
    )


def test_vertical_stack_increases_from_the_ground_up() -> None:
    """床 → モータ胴体下面 → 車軸中心 → 締結の下端 → 取付面の順に高くなる。"""
    vertical = derive_layout(_params()).vertical
    assert 0.0 < vertical.motor_body_bottom_height_mm
    assert vertical.motor_body_bottom_height_mm < vertical.axle_center_height_mm
    assert vertical.axle_center_height_mm < vertical.fastener_bottom_height_mm
    assert vertical.fastener_bottom_height_mm <= vertical.mount_face_height_mm


def test_motor_body_reaching_the_floor_is_rejected() -> None:
    """モータ胴体下面が床以下へ来る入力は拒否される。"""
    params = _params()
    broken = _with_chassis(
        params, motor=replace(params.chassis.motor, body_diameter_mm=70.0)
    )
    with pytest.raises(GeometryError) as excinfo:
        derive_layout(broken)
    assert "motor_body_bottom_height_mm" in str(excinfo.value)


def test_base_plate_below_the_axle_center_is_rejected() -> None:
    """取付面が車軸中心より低い入力は逆転として拒否される。"""
    params = _params()
    broken = _with_bracket(params, mount_face_to_contact_mm=20.0)
    with pytest.raises(GeometryError) as excinfo:
        derive_layout(broken)
    message = str(excinfo.value)
    assert "axle_center_height_mm" in message
    assert "height_mm" in message


def test_fastener_below_the_axle_center_is_rejected_with_the_pair_and_amount() -> None:
    """締結の下端が車軸中心より低い入力は、逆転した対と量を示して拒否される。"""
    params = _params()
    broken = _with_chassis(
        params,
        clearance=replace(params.chassis.clearance, fastener_protrusion_mm=40.0),
    )
    with pytest.raises(GeometryError) as excinfo:
        derive_layout(broken)
    message = str(excinfo.value)
    assert "fastener_bottom_height_mm" in message
    assert "axle_center_height_mm" in message
    assert "10" in message


def test_motor_body_bottom_level_with_the_axle_center_is_rejected() -> None:
    """⚠️ 胴体下面が車軸中心と**同じ高さ**の入力も拒否される（等号の境界）。

    モータはホイールと同軸に吊り下がるため、胴体下面は必ず車軸中心より低い
    （`docs/drivetrain-spec.md §6.3`）。同じ高さは「胴体に厚みが無い」ことを
    意味し、成立しない。design.md `#### Layout` Invariants がこの対を厳密な
    不等号で書いていることを、ここで境界として固定する。
    """
    with pytest.raises(GeometryError) as excinfo:
        VerticalStack(
            effective_rolling_radius_mm=30.0,
            nominal_rolling_radius_mm=30.0,
            axle_center_height_mm=30.0,
            motor_body_bottom_height_mm=30.0,
            mount_face_height_mm=60.0,
            fastener_bottom_height_mm=57.0,
        )
    message = str(excinfo.value)
    assert "motor_body_bottom_height_mm" in message
    assert "axle_center_height_mm" in message


def test_fastener_bottom_level_with_the_axle_center_is_rejected() -> None:
    """⚠️ 締結の下端が車軸中心と**同じ高さ**の入力も拒否される（等号の境界）。

    締結部品はベース板の下面から下へ突き出るだけであり、車軸の高さまで降りて
    くることは無い。この対も design.md `#### Layout` Invariants では厳密である。
    """
    with pytest.raises(GeometryError) as excinfo:
        VerticalStack(
            effective_rolling_radius_mm=30.0,
            nominal_rolling_radius_mm=30.0,
            axle_center_height_mm=30.0,
            motor_body_bottom_height_mm=11.5,
            mount_face_height_mm=60.0,
            fastener_bottom_height_mm=30.0,
        )
    message = str(excinfo.value)
    assert "fastener_bottom_height_mm" in message
    assert "axle_center_height_mm" in message


def test_fastener_bottom_level_with_the_mount_face_is_accepted() -> None:
    """⚠️ 締結の下端と取付面が**同一平面**であることは成立する（唯一の非厳密な対）。

    `clearance.fastener_protrusion_mm == 0`（皿頭などで突出が無い状態）のとき、
    締結の下端は取付面と一致する。design.md `#### Layout` Invariants が最後の対
    だけを `<=` で書いているのはこのためであり、ここを厳密にすると**成立する
    設計を拒む**ことになる。
    """
    vertical = VerticalStack(
        effective_rolling_radius_mm=30.0,
        nominal_rolling_radius_mm=30.0,
        axle_center_height_mm=30.0,
        motor_body_bottom_height_mm=11.5,
        mount_face_height_mm=60.0,
        fastener_bottom_height_mm=60.0,
    )
    assert vertical.fastener_bottom_height_mm == vertical.mount_face_height_mm


def test_zero_fastener_protrusion_derives_a_flush_mount_face() -> None:
    """突出量 0 の寸法設定は、導出の経路でも同一平面として受け入れられる。"""
    params = _params()
    flush = _with_chassis(
        params,
        clearance=replace(params.chassis.clearance, fastener_protrusion_mm=0.0),
    )
    vertical = derive_layout(flush).vertical
    assert vertical.fastener_bottom_height_mm == pytest.approx(
        vertical.mount_face_height_mm
    )


def test_vertical_stack_rejects_a_non_positive_nominal_rolling_radius() -> None:
    """公称の転がり半径も正でなければならない。

    ⚠️ **記録の読み戻しがこの型を通る**（`load_layout`）。0 や負の公称値を
    受け付けると、「公称と実効の一致／不一致」という**どちらの入力を使ったかの
    印**が意味を失った記録が読めてしまう。
    """
    with pytest.raises(GeometryError) as excinfo:
        VerticalStack(
            effective_rolling_radius_mm=30.0,
            nominal_rolling_radius_mm=0.0,
            axle_center_height_mm=30.0,
            motor_body_bottom_height_mm=11.5,
            mount_face_height_mm=60.0,
            fastener_bottom_height_mm=57.0,
        )
    assert "nominal_rolling_radius_mm" in str(excinfo.value)


def test_vertical_stack_rejects_an_axle_center_that_is_not_the_rolling_radius() -> None:
    """車軸中心高さは実効転がり半径と一致する（定義であり、記録でも崩せない）。"""
    with pytest.raises(GeometryError):
        VerticalStack(
            effective_rolling_radius_mm=30.0,
            nominal_rolling_radius_mm=30.0,
            axle_center_height_mm=31.0,
            motor_body_bottom_height_mm=11.5,
            mount_face_height_mm=60.0,
            fastener_bottom_height_mm=57.0,
        )


# ---------------------------------------------------------------------------
# 4. 軸方向スタック（要件 1.7）
# ---------------------------------------------------------------------------


def test_axial_stack_matches_the_value_derived_in_bom() -> None:
    """軸方向スタックは `docs/bom.md` が導出した 16.8 / 29.6mm と照合できる形で持つ。"""
    params = _params()
    layout = derive_layout(params)
    flange = params.chassis.hub.flange_thickness_mm
    width = params.chassis.wheel.width_mm
    assert layout.axial_stack_mm == pytest.approx(
        (flange, flange + width / 2.0, flange + width)
    )
    assert layout.axial_stack_mm[1] == pytest.approx(16.8)
    assert layout.axial_stack_mm[2] == pytest.approx(29.6)


# ---------------------------------------------------------------------------
# 5. 合成重心と転倒余裕（要件 3.5, 7.8, 7.9）
# ---------------------------------------------------------------------------


def test_cog_estimate_is_the_mass_weighted_mean_of_the_payload_items() -> None:
    """合成重心は搭載物の質量で重み付けした保持高さの平均である。"""
    params = _params()
    items = params.chassis.mass_items()
    expected = sum(item.mass_g * item.hold_height_mm for item in items) / sum(
        item.mass_g for item in items
    )
    assert derive_layout(params).tipping.cog_height_mm == pytest.approx(expected)


def test_tipping_estimate_uses_the_documented_formula() -> None:
    """`a_limit = g * R / (2 * h_cog)`。"""
    layout = derive_layout(_params())
    assert layout.tipping.accel_limit_mm_s2 == pytest.approx(
        GRAVITY_MM_S2 * layout.base_radius_mm / (2.0 * layout.tipping.cog_height_mm)
    )


def test_tipping_estimate_is_never_a_pass_criterion() -> None:
    """⚠️ 転倒余裕は合否条件ではない（要件 3.5）。型がそれを表明する。"""
    tipping = derive_layout(_params()).tipping
    assert tipping.is_pass_criterion is False
    assert tipping.note == TIPPING_NOTE
    assert "合否条件" in tipping.note


def test_tipping_estimate_cannot_declare_itself_a_pass_criterion() -> None:
    """⚠️ `is_pass_criterion=True` の推定は構築できない。"""
    with pytest.raises(ParameterError):
        TippingEstimate(
            accel_limit_mm_s2=1000.0,
            cog_height_mm=70.0,
            is_pass_criterion=True,
            note=TIPPING_NOTE,
        )


def test_layout_module_never_compares_the_tipping_estimate_against_anything() -> None:
    """⚠️ 閾値との比較がモジュール内に存在しないことを構造として固定する。"""
    tree = ast.parse(_LAYOUT_SOURCE)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        names = {
            child.attr if isinstance(child, ast.Attribute) else child.id
            for child in ast.walk(node)
            if isinstance(child, (ast.Attribute, ast.Name))
        }
        assert "accel_limit_mm_s2" not in names, ast.dump(node)


# ---------------------------------------------------------------------------
# 6. 出所の継承（要件 1.9 / design.md `#### Layout` Risks）
# ---------------------------------------------------------------------------


def test_base_radius_is_assumed_while_the_mount_face_reference_is_unconfirmed() -> None:
    """⚠️ 基準面が未確認である間、配置半径の出所は仮値である（黙って格上げしない）。"""
    params = _params()
    assert params.chassis.provenance["bracket.mount_face_reference"] is (
        Provenance.ASSUMED
    )
    assert derive_layout(params).provenance is Provenance.ASSUMED


def test_confirming_the_mount_face_reference_alone_does_not_upgrade_the_layout() -> None:
    """他に仮値が残る限り、基準面だけを実測にしても導出は仮値のままである。"""
    params = _params()
    provenance = dict(params.chassis.provenance)
    provenance["bracket.mount_face_reference"] = Provenance.MEASURED
    assert derive_layout(_with_chassis(params, provenance=provenance)).provenance is (
        Provenance.ASSUMED
    )


def test_the_unconfirmed_mount_face_reference_alone_keeps_the_layout_assumed() -> None:
    """⚠️ 基準面の記述**だけ**が仮値でも、導出値は仮値のままである。

    design.md `#### Layout` Risks の取り決め——「`bracket.mount_face_reference`
    の確認が済むまで `base_radius_mm` の出所は仮値である」——は、この記述項目を
    導出の入力一覧に**残しておくこと**で機械的に成立している。記述には数値と
    しての出番が無いため、一覧から外しても数値はどれも変わらない。⚠️ **だから
    こそ、外れたことを検出できるのはこのテストだけである。**
    """
    params = _all_measured(_params())
    provenance = dict(params.chassis.provenance)
    provenance["bracket.mount_face_reference"] = Provenance.ASSUMED
    assert derive_layout(_with_chassis(params, provenance=provenance)).provenance is (
        Provenance.ASSUMED
    )


def test_layout_provenance_is_measured_only_when_every_input_is_measured() -> None:
    """全入力が実測になったときに限り導出値は実測を名乗る。"""
    assert derive_layout(_all_measured(_params())).provenance is Provenance.MEASURED


def test_effective_rolling_radius_comes_from_the_nominal_value_for_now() -> None:
    """⚠️ 観測記録はまだ無い。公称値の半分を用い、観測の読み手を持ち込まない。"""
    params = _params()
    layout = derive_layout(params)
    assert layout.vertical.effective_rolling_radius_mm == pytest.approx(
        params.chassis.wheel.nominal_diameter_mm / 2.0
    )
    tree = ast.parse(_LAYOUT_SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            assert "assembly" not in node.module


def test_a_record_derived_from_an_observation_never_claims_the_nominal_half(
    tmp_path: Path,
) -> None:
    """⚠️ **観測で導いた記録が「公称値の半分を仮定した」と名乗らない**（要件 11.3）。

    本 Spec が繰り返し作ってきた事故は「実測を使ったのに仮値を名乗る」であり、
    ⚠️ **今回それは数ではなく記録の散文で起きうる**。`ASSUMPTIONS` は無条件の
    定数であり、`load_layout` は記録がこの定数と**一字一句一致する**ことを要求
    する。したがって「観測記録がまだ存在しないため公称値の半分を用いる」と
    書いてある限り、代表値を記入して導いた記録も同じ主張を載せて出てくる
    ——`effective_rolling_radius_mm` が 29.25 でありながら「公称の半分を用いた」と
    述べる記録が、再測せずこれを読む下流（要件 11.3）へ流れる。

    固定するのは2点である。

    1. **前提文はどちらの場合にも真な規則である**こと（条件節を持ち、両方の
       入力元を名指しする）。
    2. ⚠️ **どちらを使ったかは記録そのものが示す**こと——`vertical` が公称と
       実効を**並べて**持ち、観測を使った記録では両者が食い違う。前提文が
       規則になっただけでは、記録を読んだ人はどちらが起きたのか分からない。
    """
    params = _params()
    nominal_radius_mm = params.chassis.wheel.nominal_diameter_mm / 2.0
    observed_radius_mm = nominal_radius_mm - _COMPRESSION_MM
    path = tmp_path / "layout.json"
    dump_layout(
        derive_layout(
            params,
            ObservedRollingRadius(
                radius_mm=observed_radius_mm, provenance=Provenance.MEASURED
            ),
        ),
        path,
    )
    document = json.loads(path.read_text(encoding="utf-8"))

    # 2. 記録が入力元を自分で示す。
    vertical = document["vertical"]
    assert vertical["effective_rolling_radius_mm"] == pytest.approx(observed_radius_mm)
    assert vertical["nominal_rolling_radius_mm"] == pytest.approx(nominal_radius_mm)
    assert (
        vertical["nominal_rolling_radius_mm"] != vertical["effective_rolling_radius_mm"]
    ), "観測を使った記録が公称値のままの記録と同じ形になっている"

    # 1. 前提文は規則であり、無条件の主張ではない。
    (sentence,) = [
        text
        for text in document["assumptions"]
        if layout_module._EFFECTIVE_ROLLING_RADIUS_KEY.removesuffix("_mm") in text
        or "実効転がり半径は" in text
    ]
    assert "まだ存在しない" not in sentence, (
        f"前提文が「観測はまだ無い」と無条件に述べている: {sentence}"
    )
    assert "記入されていれば" in sentence and "記入されていなければ" in sentence, (
        f"前提文が条件節を持たない（どちらの場合にも真な規則になっていない）: {sentence}"
    )
    for key in (
        layout_module._NOMINAL_ROLLING_RADIUS_KEY,
        layout_module._EFFECTIVE_ROLLING_RADIUS_KEY,
    ):
        assert key in sentence, (
            f"前提文が記録のどの項目を見れば分かるかを示していない（{key} が無い）"
        )

    # 3. 読み戻した記録からも同じことが読める（記録は往復して同じ主張を保つ）。
    reloaded = load_layout(path)
    assert reloaded.vertical.nominal_rolling_radius_mm == pytest.approx(
        nominal_radius_mm
    )
    assert reloaded.vertical.effective_rolling_radius_mm == pytest.approx(
        observed_radius_mm
    )


def test_a_record_derived_without_an_observation_records_the_two_radii_as_equal(
    tmp_path: Path,
) -> None:
    """⚠️ **逆向きも固定する。** 観測が無ければ公称と実効は記録の上で一致する。

    一致／不一致が「どちらの入力を使ったか」の印である以上、観測の無い記録が
    食い違いを見せてはならない——見せれば、組立前の記録が「観測を使った」と
    読まれる。
    """
    path = tmp_path / "layout.json"
    dump_layout(derive_layout(_params()), path)
    vertical = json.loads(path.read_text(encoding="utf-8"))["vertical"]
    assert vertical["nominal_rolling_radius_mm"] == pytest.approx(
        _params().chassis.wheel.nominal_diameter_mm / 2.0
    )
    assert (
        vertical["nominal_rolling_radius_mm"] == vertical["effective_rolling_radius_mm"]
    )


# ---------------------------------------------------------------------------
# 7. 観測可能な完了状態（tasks.md タスク 2.1）
# ---------------------------------------------------------------------------


def test_changing_the_mount_face_distances_moves_radius_arm_and_stack() -> None:
    """2つの取付面までの距離を変えると配置半径・アーム長・鉛直スタックが追随する。"""
    params = _params()
    bracket = params.chassis.bracket
    before = derive_layout(params)
    after = derive_layout(
        _with_bracket(
            params,
            mount_face_to_wheel_center_mm=bracket.mount_face_to_wheel_center_mm + 5.0,
            mount_face_to_contact_mm=bracket.mount_face_to_contact_mm + 7.0,
        )
    )
    assert after.base_radius_mm != before.base_radius_mm
    assert after.arm_length_mm != before.arm_length_mm
    assert after.vertical.mount_face_height_mm != before.vertical.mount_face_height_mm
    assert (
        after.vertical.fastener_bottom_height_mm
        != before.vertical.fastener_bottom_height_mm
    )


def test_changing_the_axial_mount_face_distance_moves_radius_and_arm_together() -> None:
    """取付面 → ホイール中心を1点変えるだけで配置半径とアーム長が同時に動く（要件 3.9）。"""
    params = _params()
    bracket = params.chassis.bracket
    before = derive_layout(params)
    after = derive_layout(
        _with_bracket(
            params,
            mount_face_to_wheel_center_mm=bracket.mount_face_to_wheel_center_mm + 5.0,
        )
    )
    assert after.base_radius_mm - before.base_radius_mm == pytest.approx(5.0)
    assert after.arm_length_mm - before.arm_length_mm == pytest.approx(5.0)


def test_changing_the_vertical_mount_face_distance_moves_the_whole_stack() -> None:
    """取付面 → 接地点を変えると取付面と締結の下端が同じだけ動く（要件 4.1）。"""
    params = _params()
    bracket = params.chassis.bracket
    before = derive_layout(params).vertical
    after = derive_layout(
        _with_bracket(params, mount_face_to_contact_mm=bracket.mount_face_to_contact_mm + 7.0)
    ).vertical
    assert after.mount_face_height_mm - before.mount_face_height_mm == pytest.approx(7.0)
    assert after.fastener_bottom_height_mm - before.fastener_bottom_height_mm == (
        pytest.approx(7.0)
    )


def test_the_mount_face_is_the_measured_distance_while_nothing_is_compressed() -> None:
    """実効転がり半径が公称値と一致する間、取付面高さは**ちょうど**実測距離である。

    要件 4.1（「駆動ベースの下面高さを、モータ取付面から接地点までの実測距離から
    導出する」）が定めるのは高さの**出所**であって、荷重下でも動かないことでは
    ない（動かないことまで求めていると読むと要件 4.7 と両立しない）。補正項は
    「公称の転がり半径 − 実効転がり半径」であり、観測記録が入るまでは 0 である
    ため、実測距離がそのまま取付面高さになる。⚠️ **この等式が崩れたら、それは
    出所が変わったということである。**
    """
    params = _params()
    vertical = derive_layout(params).vertical
    assert vertical.effective_rolling_radius_mm == pytest.approx(
        params.chassis.wheel.nominal_diameter_mm / 2.0
    )
    assert (
        vertical.mount_face_height_mm
        == params.chassis.bracket.mount_face_to_contact_mm
    )


def test_a_compressed_rolling_radius_lowers_every_height_in_the_stack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """実効転がり半径が δ 縮むと、鉛直スタックの高さが**すべて** δ 下がる（要件 4.7）。

    ⚠️ **`bracket.mount_face_to_contact_mm` は「取付面の高さ」という定数ではない。**
    これはホイールを付けた状態で測った「取付面 → 接地点」であり、公称の転がり半径を
    **すでに含んでいる**（`docs/bom.md §B`:「垂直方向では 60.0 − 30 ＝ 30.0mm が
    取付面から車軸までの高さになり整合する」）。荷重で転がり半径が δ 縮めば機体
    全体が δ 低く座るため、取付面も締結の下端も δ 下がる（要件 10.3 /
    research.md「公称 30mm に対し荷重下で 29.25mm なら、モータ胴体下面は 10.75mm
    へ下がる」）。⚠️ ここを実測距離そのままの定数にすると、床との隙間の5部位の
    うち3部位が実効転がり半径に追随せず、「隙間は足りている」という誤った判定が
    残る（design.md `#### Clearance`「隙間はすべて `VerticalStack` から算出される
    ため、実効転がり半径が変われば自動で追随する」が成り立たなくなる）。

    ⚠️ **差し替えるのは実効転がり半径の1点だけである。** 寸法パラメータは一切
    動かしていない——`wheel.nominal_diameter_mm` を書き換えると公称値と実効値が
    同時に動いてしまい、「荷重で縮んだ」状態を表せない（公称値と実測距離は同じ
    現物を同時に測った対である）。
    """
    params = _params()
    before = derive_layout(params).vertical
    monkeypatch.setattr(
        layout_module,
        "_effective_rolling_radius_mm",
        # ⚠️ 第2引数は観測（`ObservedRollingRadius`。タスク 4.1 が配線した）。
        # 本件は観測**無し**で縮んだ状態を作るため受け取って捨てる——差し替え点が
        # 1箇所であることは変わっていない。
        lambda nominal_mm, observed=None: nominal_mm - _COMPRESSION_MM,
    )
    after = derive_layout(params).vertical
    for name in (
        "effective_rolling_radius_mm",
        "axle_center_height_mm",
        "motor_body_bottom_height_mm",
        "fastener_bottom_height_mm",
        "mount_face_height_mm",
    ):
        assert getattr(after, name) - getattr(before, name) == pytest.approx(
            -_COMPRESSION_MM
        ), f"{name} が実効転がり半径に追随していない"


# ---------------------------------------------------------------------------
# 8. 導出記録（要件 3.4 / design.md「Data Models」）
# ---------------------------------------------------------------------------


def test_dump_layout_writes_inputs_formula_provenance_and_results(tmp_path: Path) -> None:
    """導出記録は入力・式・出所・結果を持つ（上流 `catch-opening.json` と同形）。"""
    params = _params()
    layout = derive_layout(params)
    path = tmp_path / "layout.json"
    dump_layout(layout, path)
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["schema_version"] == SCHEMA_VERSION
    assert document["formula"] == FORMULA
    assert document["provenance"] == Provenance.ASSUMED.value
    assert tuple(item["name"] for item in document["inputs"]) == REQUIRED_INPUT_NAMES
    assert tuple(document["assumptions"]) == ASSUMPTIONS
    assert document["base_radius_mm"] == pytest.approx(layout.base_radius_mm)
    assert document["arm_length_mm"] == pytest.approx(layout.arm_length_mm)
    assert document["wheel_angles_deg"] == pytest.approx(list(layout.wheel_angles_deg))
    assert document["axial_stack_mm"] == pytest.approx(list(layout.axial_stack_mm))
    assert set(document["vertical"]) == {
        "effective_rolling_radius_mm",
        # ⚠️ 公称の転がり半径を実効値と**並べて**記録する（タスク 4.1 の是正）。
        # これが無いと、観測で置き換えた記録と公称値のままの記録が同じ形になり、
        # 前提文の「公称値の半分を用いる」という主張を記録の側から反証できない。
        "nominal_rolling_radius_mm",
        "axle_center_height_mm",
        "motor_body_bottom_height_mm",
        "mount_face_height_mm",
        "fastener_bottom_height_mm",
    }


def test_record_states_that_the_tipping_estimate_is_not_a_pass_criterion(
    tmp_path: Path,
) -> None:
    """⚠️ 記録の側にも「合否条件ではない」ことが現れる（要件 3.5）。"""
    path = tmp_path / "layout.json"
    dump_layout(derive_layout(_params()), path)
    tipping = json.loads(path.read_text(encoding="utf-8"))["tipping"]
    assert tipping["is_pass_criterion"] is False
    assert tipping["note"] == TIPPING_NOTE


def test_dump_layout_writes_lf_sorted_keys_and_a_trailing_newline(tmp_path: Path) -> None:
    """行単位の差分が読める形で書き出す（要件 1.10 と同じ規律）。"""
    path = tmp_path / "layout.json"
    dump_layout(derive_layout(_params()), path)
    raw = path.read_bytes()
    assert b"\r\n" not in raw
    assert raw.endswith(b"\n")
    text = raw.decode("utf-8")
    keys = [line.split('"')[1] for line in text.splitlines() if line.startswith('  "')]
    assert keys == sorted(keys)


def test_load_layout_round_trips_dump_layout(tmp_path: Path) -> None:
    """書いた記録を読み戻すと同じ導出結果になる。"""
    path = tmp_path / "layout.json"
    original = derive_layout(_params())
    dump_layout(original, path)
    restored = load_layout(path)
    again = tmp_path / "again.json"
    dump_layout(restored, again)
    assert again.read_bytes() == path.read_bytes()
    assert restored.provenance is original.provenance
    assert restored.base_radius_mm == pytest.approx(original.base_radius_mm)


def test_load_layout_rejects_an_unknown_key(tmp_path: Path) -> None:
    """未知キーは項目名を示して拒否する。"""
    path = tmp_path / "layout.json"
    dump_layout(derive_layout(_params()), path)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["wheel_track_mm"] = 1.0
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ParameterError) as excinfo:
        load_layout(path)
    assert "wheel_track_mm" in str(excinfo.value)


def test_load_layout_rejects_a_missing_key(tmp_path: Path) -> None:
    """欠損キーは項目名を示して拒否する（既定値で埋めない）。"""
    path = tmp_path / "layout.json"
    dump_layout(derive_layout(_params()), path)
    document = json.loads(path.read_text(encoding="utf-8"))
    del document["arm_length_mm"]
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ParameterError) as excinfo:
        load_layout(path)
    assert "arm_length_mm" in str(excinfo.value)


def test_load_layout_rejects_a_result_that_contradicts_the_inputs(tmp_path: Path) -> None:
    """⚠️ 記録が独自の値を主張できるなら、導出は2箇所にあるのと変わらない。"""
    path = tmp_path / "layout.json"
    dump_layout(derive_layout(_params()), path)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["base_radius_mm"] = document["base_radius_mm"] + 10.0
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ParameterError) as excinfo:
        load_layout(path)
    assert "base_radius_mm" in str(excinfo.value)


def test_shipped_layout_record_matches_the_current_dimensions() -> None:
    """出荷される導出記録は、現在の寸法設定から導かれる値と一致する。"""
    assert DEFAULT_LAYOUT_PATH.exists()
    recorded = load_layout()
    derived = derive_layout(_params())
    assert recorded.base_radius_mm == pytest.approx(derived.base_radius_mm)
    assert recorded.arm_length_mm == pytest.approx(derived.arm_length_mm)
    assert recorded.wheel_angles_deg == pytest.approx(derived.wheel_angles_deg)
    assert recorded.provenance is derived.provenance
    assert recorded.tipping.is_pass_criterion is False


def test_default_layout_path_sits_beside_the_dimensions_file() -> None:
    """記録は本 Spec の設定ディレクトリに置かれる。"""
    assert DEFAULT_LAYOUT_PATH.parent == DEFAULT_DIMENSIONS_PATH.parent
    assert DEFAULT_LAYOUT_PATH.name == "layout.json"


def test_chassis_layout_rejects_a_non_positive_arm_length() -> None:
    """アームが成立しない記録は構築できない（design.md `#### Layout` Postconditions）。"""
    derived = derive_layout(_params())
    with pytest.raises(GeometryError):
        ChassisLayout(
            base_radius_mm=derived.base_radius_mm,
            wheel_angles_deg=derived.wheel_angles_deg,
            hub_center_to_mount_face_mm=derived.hub_center_to_mount_face_mm,
            arm_length_mm=0.0,
            vertical=derived.vertical,
            axial_stack_mm=derived.axial_stack_mm,
            tipping=derived.tipping,
            provenance=derived.provenance,
        )


def test_gravity_constant_is_standard_gravity_in_mm_per_second_squared() -> None:
    """`g` は標準重力加速度である（単位は mm/s^2）。"""
    assert math.isclose(GRAVITY_MM_S2, 9806.65)
