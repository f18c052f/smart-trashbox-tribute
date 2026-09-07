"""`chassis_mechanism.joints` の検査（design.md `#### Joints` / 要件 2.1, 2.2,
2.6, 2.7, 2.8, 2.9, 2.10, 3.10, 5.6, 6.8）。

固定するのは次の8点である。

1. **接合部の一覧は幾何と寸法から導出される**（tasks.md タスク 2.3
   「⚠️ **手書きの一覧を設定ファイルに持たない**」）。⚠️ 輪数・脚数・上流の
   ゴミ箱の採寸値・造形可能寸法を動かすと一覧の件数が追随する。記録
   （`joint-schedule.json`）は導出の**写し**であり入力ではない。
2. **積層方向（`z`）と一致する接合面は形状不正として拒否される**（要件 2.8 /
   design.md `#### Joints` Validation「`print_normal_axis == "z"` の接合部は
   `GeometryError` で拒否する」）。⚠️ メッセージは**接合部の名と軸**を持つ。
3. **位置決め要素（ダボ）は締結部品一覧に現れず、当たり面にも算入されない**
   （要件 2.7 / design.md Invariants「⚠️ **ダボは `lines` に現れない**」）。
4. **締結部品の長さは「積み上がり厚さ ＋ 上流のインサート長 ＋ 余裕」から導出
   される**（要件 2.10 / tasks.md タスク 2.3「⚠️ **上流 `JointPolicy.
   insert_length_mm` を使い、数値を書き写さない**」）。⚠️ **板厚を変えると
   ボルト長が追随する**（観測可能な完了状態）。
5. **当たり面は上流の下限と本 Spec のより厳しい下限の両方を満たす**
   （要件 2.9 / design.md Postconditions）。⚠️ 上流の `check_joint` を実際に
   通す。⚠️ ダボの径を変えても当たり面は動かない。
6. **分割数の導出は部品の種類で分かれる**（要件 2.1 / research.md「Decision:
   分割の導出を部品の種類で分ける」）。⚠️ **円環でない部品を円環として近似
   しない**——造形可能寸法を動かしても位相従属の部品の分割数は動かない。
7. **断片の外接箱は上流の検査（`check_envelope`）を関門とする**（要件 2.2,
   2.3）。⚠️ 超過する軸と超過量がメッセージに現れる。
8. **記録は導出の写しであり、独立に編集してよい自由記述ではない**——未知キー・
   欠損・joints と食い違う lines を拒否し、出荷されている記録が現在の寸法から
   再導出したものと一致する。
"""

from __future__ import annotations

import ast
import json
import math
import re
from dataclasses import replace
from pathlib import Path

import pytest
from catch_mechanism import GeometryError as UpstreamGeometryError
from catch_mechanism import check_joint, required_segment_count

from chassis_mechanism import joints as joints_module
from chassis_mechanism.config import (
    DEFAULT_DIMENSIONS_PATH,
    SCHEMA_VERSION,
    ResolvedParams,
    load_params,
    parameters_digest,
)
from chassis_mechanism.errors import ConsistencyError, GeometryError, ParameterError
from chassis_mechanism.joints import (
    ASSUMPTIONS,
    BATTERY_TRAY_ARM_INDEX,
    BOSS_DIAMETER_FACTOR,
    DECK_SEAT_JOINT_NAME,
    DEFAULT_JOINT_SCHEDULE_PATH,
    FASTENER_KINDS,
    LAYER_NORMAL_AXIS,
    battery_tray_ear_length_mm,
    board_deck_outer_diameter_mm,
    board_deck_rise_mm,
    catch_deck_outer_diameter_mm,
    catch_deck_rise_mm,
    deck_collar_length_mm,
    deck_riser_outer_diameter_mm,
    FastenerLine,
    FastenerSchedule,
    JointSpec,
    derive_fastener_schedule,
    derive_joints,
    dump_fastener_schedule,
    load_fastener_schedule,
    segment_counts,
)
from chassis_mechanism.layout import derive_layout

_JOINTS_SOURCE: str = Path(joints_module.__file__).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 補助
# ---------------------------------------------------------------------------


def _params() -> ResolvedParams:
    """出荷されている寸法設定（本 Spec ＋ 上流）を読む。"""
    return load_params(DEFAULT_DIMENSIONS_PATH)


def _with_base(params: ResolvedParams, **changes: object) -> ResolvedParams:
    """`base` 群だけを差し替えた `ResolvedParams` を作る。"""
    return replace(
        params, chassis=replace(params.chassis, base=replace(params.chassis.base, **changes))
    )


def _with_clearance(params: ResolvedParams, **changes: object) -> ResolvedParams:
    """`clearance` 群だけを差し替えた `ResolvedParams` を作る。"""
    return replace(
        params,
        chassis=replace(
            params.chassis, clearance=replace(params.chassis.clearance, **changes)
        ),
    )


def _with_joint_local(params: ResolvedParams, **changes: object) -> ResolvedParams:
    """`joint_local` 群だけを差し替えた `ResolvedParams` を作る。"""
    return replace(
        params,
        chassis=replace(
            params.chassis, joint_local=replace(params.chassis.joint_local, **changes)
        ),
    )


def _with_wheel_count(params: ResolvedParams, count: int) -> ResolvedParams:
    """輪数と脚数を同時に差し替える（`ChassisParams` は両者の一致を要求する）。"""
    chassis = params.chassis
    return replace(
        params,
        chassis=replace(
            chassis,
            base=replace(chassis.base, wheel_count=count),
            stand=replace(chassis.stand, leg_count=count),
        ),
    )


def _derived(params: ResolvedParams) -> tuple[JointSpec, ...]:
    """出荷寸法から接合部を導出する。"""
    return derive_joints(derive_layout(params), params)


def _named(specs: tuple[JointSpec, ...], name: str) -> JointSpec:
    """名前で1件を取り出す。"""
    return next(spec for spec in specs if spec.name == name)


def _pad_area_mm2(params: ResolvedParams) -> float:
    """ボルト1本あたりの当たり面（インサート座の環）。上流の継手方針だけから決まる。"""
    joint = params.joint
    boss_diameter_mm = joints_module.BOSS_DIAMETER_FACTOR * joint.insert_outer_diameter_mm
    return (
        math.pi
        / 4
        * (boss_diameter_mm**2 - joint.through_hole_diameter_mm**2)
    )


# ---------------------------------------------------------------------------
# 1. 接合部の一覧は導出される（手書きの一覧を持たない）
# ---------------------------------------------------------------------------


def test_joint_names_are_derived_from_the_geometry_and_the_dimensions() -> None:
    """出荷寸法からの一覧が、輪数・分割数・保持箇所から一意に決まる。"""
    params = _params()
    specs = _derived(params)
    counts = segment_counts(params)
    expected_names = (
        tuple(f"hub_plate__motor_arm_{index}" for index in range(1, params.chassis.base.wheel_count + 1))
        + tuple(
            f"hub_plate__adapter_segment_{index}"
            for index in range(1, counts["adapter_segment"] + 1)
        )
        + ("adapter__trash_can",)
        + (
            f"motor_arm_{BATTERY_TRAY_ARM_INDEX}__battery_tray",
            DECK_SEAT_JOINT_NAME,
        )
        + tuple(
            f"board_deck__catch_deck_{index}"
            for index in range(1, counts["catch_deck"] + 1)
        )
        + tuple(
            f"service_stand_{index}__wheel_{index}"
            for index in range(1, params.chassis.stand.leg_count + 1)
        )
    )
    assert tuple(spec.name for spec in specs) == expected_names


def test_joint_count_follows_the_wheel_count() -> None:
    """⚠️ 輪数を増やすと中央部↔アームの接合部とスタンドの拘束が追随する。"""
    four = _with_wheel_count(_params(), 4)
    specs = _derived(four)
    arms = [spec for spec in specs if spec.members[1].startswith("motor_arm_")]
    stands = [spec for spec in specs if spec.members[0].startswith("service_stand_")]
    assert len(arms) == 4
    assert len(stands) == 4


def test_joint_count_follows_the_upstream_trash_can_measurement() -> None:
    """⚠️ 上流のゴミ箱の底の外径を広げるとアダプタ断片の接合部が増える。"""
    params = _params()
    wider = replace(
        params, trash_can=replace(params.trash_can, bottom_outer_diameter_mm=200.0)
    )
    before = len([spec for spec in _derived(params) if "adapter_segment" in spec.name])
    after = len([spec for spec in _derived(wider) if "adapter_segment" in spec.name])
    assert after > before


def test_every_derived_joint_names_two_distinct_members() -> None:
    """接合部は必ず2部材を結ぶ（要件 3.10 の中央部↔モータ取付部を含む）。"""
    for spec in _derived(_params()):
        assert len(spec.members) == 2
        assert spec.members[0] != spec.members[1]
        assert all(member.strip() for member in spec.members)


def test_the_stand_retention_and_the_adapter_retention_are_present() -> None:
    """⚠️ 要件 5.6（台上の拘束）と要件 6.8（ゴミ箱の締結箇所）が一覧に現れる。"""
    names = {spec.name for spec in _derived(_params())}
    assert "adapter__trash_can" in names
    assert "service_stand_1__wheel_1" in names


# ---------------------------------------------------------------------------
# 2. 積層方向と一致する接合面は拒否される（要件 2.8）
# ---------------------------------------------------------------------------


def test_a_joint_whose_face_normal_is_the_layer_axis_is_rejected() -> None:
    """⚠️ `print_normal_axis == "z"` は `GeometryError`。名と軸がメッセージに出る。"""
    with pytest.raises(GeometryError) as excinfo:
        JointSpec(
            name="hub_plate__motor_arm_1",
            members=("hub_plate", "motor_arm_1"),
            bolt_count=2,
            bolt_length_mm=20.0,
            insert_count=2,
            dowel_count=2,
            bearing_area_mm2=200.0,
            print_normal_axis="z",
            min_bearing_area_mm2=90.0,
        )
    message = str(excinfo.value)
    assert "hub_plate__motor_arm_1" in message
    assert "z" in message


def test_an_axis_that_is_not_an_axis_at_all_is_rejected() -> None:
    """軸名でない造形姿勢を受け付けない（空文字・別名とも）。"""
    for axis in ("", "Z", "vertical"):
        with pytest.raises(GeometryError):
            JointSpec(
                name="joint",
                members=("a", "b"),
                bolt_count=2,
                bolt_length_mm=20.0,
                insert_count=2,
                dowel_count=0,
                bearing_area_mm2=200.0,
                print_normal_axis=axis,
                min_bearing_area_mm2=90.0,
            )


def test_no_derived_joint_uses_the_layer_axis() -> None:
    """導出された全接合部の造形姿勢が積層方向を避けている（design.md Postconditions）。"""
    for spec in _derived(_params()):
        assert spec.print_normal_axis != joints_module.LAYER_NORMAL_AXIS
        assert spec.print_normal_axis in joints_module.ALLOWED_PRINT_NORMAL_AXES


# ---------------------------------------------------------------------------
# 3. ダボは締結部品一覧に現れず、当たり面にも算入されない（要件 2.7）
# ---------------------------------------------------------------------------


def test_load_bearing_joints_carry_locating_dowels() -> None:
    """造形部品どうしの接合部は位置決めダボを持つ（型の上での区別）。"""
    specs = _derived(_params())
    arm_joint = _named(specs, "hub_plate__motor_arm_1")
    assert arm_joint.dowel_count > 0
    assert arm_joint.insert_count == arm_joint.bolt_count


def test_dowels_never_appear_in_the_fastener_lines() -> None:
    """⚠️ ダボは造形で作る位置決め要素であり、購入する締結部品ではない。"""
    schedule = derive_fastener_schedule(derive_layout(_params()), _params())
    assert {line.kind for line in schedule.lines} <= set(FASTENER_KINDS)
    assert "dowel" not in FASTENER_KINDS
    total_dowels = sum(spec.dowel_count for spec in schedule.joints)
    assert total_dowels > 0
    assert all("dowel" not in line.designation for line in schedule.lines)


def test_the_line_totals_are_determined_by_the_joints_alone() -> None:
    """`lines` の総数は `joints` から一意に決まる（design.md Invariants）。"""
    schedule = derive_fastener_schedule(derive_layout(_params()), _params())
    bolts = sum(line.count for line in schedule.lines if line.kind == "bolt")
    inserts = sum(line.count for line in schedule.lines if line.kind == "insert")
    nuts = sum(line.count for line in schedule.lines if line.kind == "nut")
    assert bolts == sum(spec.bolt_count for spec in schedule.joints)
    assert inserts == sum(spec.insert_count for spec in schedule.joints)
    assert nuts == sum(spec.bolt_count - spec.insert_count for spec in schedule.joints)


def test_changing_the_dowel_diameter_does_not_move_any_bearing_area() -> None:
    """⚠️ 位置決め要素は当たり面の面積に算入されない（上流 `check_joint` の契約）。"""
    params = _params()
    thicker_dowel = replace(params, joint=replace(params.joint, dowel_diameter_mm=8.0))
    before = [spec.bearing_area_mm2 for spec in _derived(params)]
    after = [spec.bearing_area_mm2 for spec in _derived(thicker_dowel)]
    assert before == after


def test_a_record_with_more_dowels_keeps_the_same_lines(tmp_path: Path) -> None:
    """ダボを増やした記録でも `lines` は変わらない（＝ダボは数え上げに入らない）。"""
    schedule = derive_fastener_schedule(derive_layout(_params()), _params())
    doubled = FastenerSchedule(
        schema_version=schedule.schema_version,
        parameters_digest=schedule.parameters_digest,
        joints=tuple(
            replace(spec, dowel_count=spec.dowel_count * 2) for spec in schedule.joints
        ),
        lines=schedule.lines,
    )
    path = tmp_path / "joint-schedule.json"
    dump_fastener_schedule(doubled, path)
    assert load_fastener_schedule(path).lines == schedule.lines


# ---------------------------------------------------------------------------
# 4. 締結部品の長さの導出（要件 2.10）
# ---------------------------------------------------------------------------


def test_bolt_length_is_the_stack_plus_the_upstream_insert_length_plus_margin() -> None:
    """積み上がり厚さ ＋ 上流のインサート長 ＋ 余裕。数値を書き写さない。

    ⚠️ 「余裕」は `joint_local.fastener_length_margin_mm` である——床との隙間の
    ための `clearance.fastener_protrusion_mm` ではない（両者が同じ値のときでも
    参照先を取り違えれば、片方を動かしたときに黙って壊れる）。

    ⚠️ **ナットで受ける `adapter__trash_can` も同じ式である。** 噛み合い代に
    上流のインサート長を使うのは、⚠️ **ナットの高さを持つ寸法パラメータが
    上流にも本 Spec にも無い**ためであり、数値を発明しない（`ASSUMPTIONS`）。
    ナットはインサートより薄いため、この長さは保守側に倒れている。
    """
    params = _params()
    chassis = params.chassis
    margin_mm = chassis.joint_local.fastener_length_margin_mm
    arm_joint = _named(_derived(params), "hub_plate__motor_arm_1")
    assert arm_joint.bolt_length_mm == pytest.approx(
        chassis.base.arm_thickness_mm
        + chassis.base.plate_thickness_mm
        + params.joint.insert_length_mm
        + margin_mm
    )
    adapter_joint = _named(_derived(params), "hub_plate__adapter_segment_1")
    assert adapter_joint.bolt_length_mm == pytest.approx(
        chassis.adapter.wall_thickness_mm + params.joint.insert_length_mm + margin_mm
    )
    retention = _named(_derived(params), "adapter__trash_can")
    assert retention.bolt_length_mm == pytest.approx(
        chassis.adapter.wall_thickness_mm
        + params.trash_can.bottom_thickness_mm
        + params.joint.insert_length_mm
        + margin_mm
    )


def test_the_ground_clearance_protrusion_does_not_move_any_bolt_length() -> None:
    """⚠️ **床との隙間の量でボルト長が動いてはならない**（別の物理量である）。

    `clearance.fastener_protrusion_mm` の意味は締結部品の**下方**への突出量だけ
    であり（`params.ClearanceLimits` の docstring）、その唯一の消費者は
    `layout` の床との隙間（要件 4.2, 4.3）である。⚠️ 導出されるボルトは
    すべて半径方向 `x` か接線方向 `y` を向いており、**下方を向くものは1本も
    無い**。

    ⚠️ **`0.0` は「皿頭で突出が無い」を表す正当な値である**——これを設定した
    だけで全ボルトの余裕が消えるなら、床についての判断が調達するボルトの長さを
    黙って縮めていることになる（タスク 5.5 の調達数量が壊れる）。
    """
    params = _params()
    before = [(spec.name, spec.bolt_length_mm) for spec in _derived(params)]
    for protrusion_mm in (0.0, 1.0, 2.5):
        moved = _with_clearance(params, fastener_protrusion_mm=protrusion_mm)
        after = [(spec.name, spec.bolt_length_mm) for spec in _derived(moved)]
        assert after == before, (
            f"clearance.fastener_protrusion_mm={protrusion_mm} でボルト長が動いた: "
            f"{before!r} -> {after!r}"
        )


def test_the_fastener_length_margin_moves_every_bolt_length_one_for_one() -> None:
    """締結長の余裕は `joint_local.fastener_length_margin_mm` が唯一の入口である。

    ⚠️ **この量を動かしたら全ボルトが 1:1 で追随しなければならない**——追随
    しなければ、余裕がどこか別の場所（書き写した数値や別の趣旨の量）から来て
    いることになる。⚠️ `0.0`（余裕を取らない）も設定として成立する。
    """
    params = _params()
    base_margin_mm = params.chassis.joint_local.fastener_length_margin_mm
    before = [spec.bolt_length_mm for spec in _derived(params) if spec.bolt_count > 0]
    for margin_mm in (0.0, base_margin_mm + 2.5):
        moved = _with_joint_local(params, fastener_length_margin_mm=margin_mm)
        after = [spec.bolt_length_mm for spec in _derived(moved) if spec.bolt_count > 0]
        delta_mm = margin_mm - base_margin_mm
        assert after == [pytest.approx(length + delta_mm) for length in before]


def test_changing_the_plate_thickness_moves_the_bolt_length() -> None:
    """⚠️ 板厚を変えると締結部品の長さが追随する（観測可能な完了状態）。"""
    params = _params()
    thicker = _with_base(params, plate_thickness_mm=params.chassis.base.plate_thickness_mm + 4.0)
    before = _named(_derived(params), "hub_plate__motor_arm_1").bolt_length_mm
    after = _named(_derived(thicker), "hub_plate__motor_arm_1").bolt_length_mm
    assert after == pytest.approx(before + 4.0)


def test_changing_the_upstream_insert_length_moves_every_bolt_length() -> None:
    """⚠️ インサート長は上流が定める。数値を書き写していれば追随しない。"""
    params = _params()
    longer = replace(params, joint=replace(params.joint, insert_length_mm=9.7))
    before = [spec.bolt_length_mm for spec in _derived(params) if spec.bolt_count > 0]
    after = [spec.bolt_length_mm for spec in _derived(longer) if spec.bolt_count > 0]
    assert all(
        later == pytest.approx(earlier + 4.0) for earlier, later in zip(before, after)
    )


def test_the_schedule_lines_are_procurable_rows() -> None:
    """種別・呼び・長さ・数量の一覧である（要件 2.10）。"""
    params = _params()
    schedule = derive_fastener_schedule(derive_layout(params), params)
    assert schedule.lines
    for line in schedule.lines:
        assert line.designation == params.joint.bolt_designation
        assert line.kind in FASTENER_KINDS
        assert line.count > 0
        if line.kind == "nut":
            assert line.length_mm is None
        else:
            assert line.length_mm is not None and line.length_mm > 0.0
    insert_line = next(line for line in schedule.lines if line.kind == "insert")
    assert insert_line.length_mm == pytest.approx(params.joint.insert_length_mm)


def test_a_fastener_free_joint_has_no_bolt_length() -> None:
    """⚠️ 締結部品を持たない拘束（台上の保持）は長さ 0 であり、一覧へ何も足さない。"""
    stand = _named(_derived(_params()), "service_stand_1__wheel_1")
    assert stand.bolt_count == 0
    assert stand.insert_count == 0
    assert stand.bolt_length_mm == 0.0


def test_a_bolt_count_without_a_length_is_rejected() -> None:
    """ボルトを持つ接合部の長さは正でなければならない。"""
    with pytest.raises(GeometryError):
        JointSpec(
            name="joint",
            members=("a", "b"),
            bolt_count=2,
            bolt_length_mm=0.0,
            insert_count=2,
            dowel_count=0,
            bearing_area_mm2=200.0,
            print_normal_axis="x",
            min_bearing_area_mm2=90.0,
        )


def test_more_inserts_than_bolts_is_rejected() -> None:
    """インサートはボルトを受ける要素であり、ボルトより多くは現れない。"""
    with pytest.raises(GeometryError):
        JointSpec(
            name="joint",
            members=("a", "b"),
            bolt_count=1,
            bolt_length_mm=20.0,
            insert_count=2,
            dowel_count=0,
            bearing_area_mm2=200.0,
            print_normal_axis="x",
            min_bearing_area_mm2=90.0,
        )


# ---------------------------------------------------------------------------
# 5. 当たり面は上流の下限と本 Spec の下限の両方を満たす（要件 2.9）
# ---------------------------------------------------------------------------


def test_every_joint_passes_the_upstream_check_and_the_local_floor() -> None:
    """⚠️ 上流の `check_joint` と本 Spec のより厳しい下限の**両方**を満たす。"""
    params = _params()
    local_floor_mm2 = params.chassis.joint_local.min_bearing_area_mm2
    for spec in _derived(params):
        check_joint(params.joint, spec.bearing_area_mm2)  # 上流の検査を実際に通す
        assert spec.bearing_area_mm2 >= spec.min_bearing_area_mm2
        assert spec.min_bearing_area_mm2 >= params.joint.min_bearing_area_mm2
        if spec.name.startswith(("hub_plate__motor_arm", "service_stand")):
            assert spec.min_bearing_area_mm2 == local_floor_mm2


def test_the_bearing_area_of_a_bolted_joint_is_the_analytic_boss_area() -> None:
    """当たり面は寸法パラメータから解析的に算出する（design.md Risks）。"""
    params = _params()
    pad_mm2 = _pad_area_mm2(params)
    arm_joint = _named(_derived(params), "hub_plate__motor_arm_1")
    assert arm_joint.bearing_area_mm2 == pytest.approx(arm_joint.bolt_count * pad_mm2)


def test_raising_the_local_floor_adds_bolts() -> None:
    """⚠️ 本 Spec の下限を上げると、下限を満たすまでボルト本数が増える。

    ⚠️ **下限は 150mm^2 を採る（かつては 200mm^2 だった）。** 本数が増えると
    重ね代（`ARM_JOINT_LAP_LENGTH_FORMULA` = 本数 × 座径）も伸び、中央部の
    外接箱がその2倍だけ広がる——200mm^2 は4本 ＝ 重ね代 36.8mm となり、
    中央部が 193.6mm で造形可能寸法 180mm を超える。⚠️ **緩めたのではない**：
    見たいのは「下限を上げると本数が増える」ことであり、造形可能寸法の関門は
    `test_a_central_plate_that_exceeds_the_build_volume_is_rejected` が別に持つ。
    """
    floor_mm2 = 150.0
    params = _params()
    stricter = _with_joint_local(params, min_bearing_area_mm2=floor_mm2)
    before = _named(_derived(params), "hub_plate__motor_arm_1")
    after = _named(_derived(stricter), "hub_plate__motor_arm_1")
    assert after.bolt_count > before.bolt_count
    assert after.bearing_area_mm2 >= floor_mm2


def test_the_lap_length_is_the_bolt_row_and_widens_the_central_plate() -> None:
    """⚠️ 重ね代は「本数 × 座径」であり、中央部の外接箱をその2倍だけ広げる。

    ハブ板の舌はアームの二股へ差し込まれるため、中央部の外縁から重ね代のぶん
    **外へ張り出す**（要件 2.6 の重ね継手）。⚠️ **この量を無視した外接箱は、
    実際より小さい部品について「収まっている」と述べる**——それは検査が無い
    ことより悪い。本数が増えれば舌も伸び、外接箱も追随する。
    """
    params = _params()
    layout = derive_layout(params)
    boss_diameter_mm = (
        joints_module.BOSS_DIAMETER_FACTOR * params.joint.insert_outer_diameter_mm
    )
    arm_joint = _named(_derived(params), "hub_plate__motor_arm_1")
    lap_mm = joints_module.arm_joint_lap_length_mm(layout, params)
    assert lap_mm == pytest.approx(arm_joint.bolt_count * boss_diameter_mm)

    # 下限を上げて本数が増えると、重ね代も 1:1 で伸びる。
    stricter = _with_joint_local(params, min_bearing_area_mm2=150.0)
    stricter_layout = derive_layout(stricter)
    stricter_lap_mm = joints_module.arm_joint_lap_length_mm(stricter_layout, stricter)
    assert stricter_lap_mm > lap_mm

    # 舌のぶんだけ広げた外接箱で造形可能寸法を見ている（公称外径のままではない）。
    outer_diameter_mm = params.chassis.base.hub_outer_diameter_mm
    too_big = _with_base(
        params, hub_outer_diameter_mm=params.printing.build_x_mm - lap_mm
    )
    assert too_big.chassis.base.hub_outer_diameter_mm < params.printing.build_x_mm
    with pytest.raises(GeometryError) as excinfo:
        _derived(too_big)
    message = str(excinfo.value)
    assert "hub_plate" in message
    # 公称外径そのままで判定していたら、この寸法は「収まっている」と通っていた。
    assert outer_diameter_mm < params.printing.build_x_mm


def test_a_floor_that_no_joint_face_can_carry_is_rejected() -> None:
    """⚠️ 当たり面が接合面に収まらない下限は形状不正である（接合部の名が出る）。

    ⚠️ アーム長そのものが足りなくなる値は `derive_layout` が先に拒否する
    （要件 3.10）。ここで見たいのは**接合面の幅にボルト座が並ばない**という
    `joints` 側の関門であるため、アーム長の下限は満たす値を選ぶ。
    """
    params = _params()
    impossible = replace(
        params,
        chassis=replace(
            params.chassis,
            joint_local=replace(
                params.chassis.joint_local, min_bearing_area_mm2=1000.0
            ),
        ),
    )
    with pytest.raises(GeometryError) as excinfo:
        _derived(impossible)
    assert "hub_plate__motor_arm_1" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 5b. ボルト座を並べる方向は接合面の**面内**である（軸の取り違えの再発防止）
# ---------------------------------------------------------------------------


def test_the_seat_row_is_measured_along_the_arm_length_not_the_arm_width() -> None:
    """⚠️ **座を並べる幅はアーム長（半径方向）であり、アーム幅ではない。**

    中央部↔モータ取付部の接合面は接線方向 `y` を法線に持つ
    （`print_normal_axis`。要件 2.8 / A-5 が積層方向 `z` を法線に持つ面を禁じる
    ため、この向き以外を採れない）。したがって面内2軸は**半径方向（アーム長）と
    厚さ方向**であり、⚠️ `arm_width_mm` は `_check_fragment_envelopes` が `y` へ
    写している量——**ボルトの軸そのもの**である。座をその方向へ並べることは
    できない（並べれば座がボルトの軸上に重なる）。

    かつて `face_width_mm` にはこの `arm_width_mm` が渡されていた。出荷値では
    2 本 × 座径 9.2mm = 18.4mm ≦ 45mm で通ってしまうため、⚠️ **値の一致では
    捉えられない**。ここでは「不足している」とメッセージが述べる**使える幅**が
    アーム長であることを見る。
    """
    params = _params()
    layout = derive_layout(params)
    impossible = _with_joint_local(params, min_bearing_area_mm2=1000.0)
    with pytest.raises(GeometryError) as excinfo:
        _derived(impossible)
    message = str(excinfo.value)
    assert repr(layout.arm_length_mm) in message, message
    assert repr(params.chassis.base.arm_width_mm) not in message, message


def test_narrowing_the_arm_width_never_moves_the_bolt_row(
) -> None:
    """⚠️ アーム幅を座の並びより細くしても導出は成立する（幅は軸方向である）。

    出荷値の 2 本 × 座径 9.2mm = 18.4mm より細い 12mm のアーム幅でも、座は
    半径方向へ並ぶため接合部は成立する。⚠️ **旧実装ではここが `GeometryError`
    になっていた**——座の並びを締結の軸方向で測っていたためである。
    """
    params = _params()
    boss_diameter_mm = (
        joints_module.BOSS_DIAMETER_FACTOR * params.joint.insert_outer_diameter_mm
    )
    before = _named(_derived(params), "hub_plate__motor_arm_1")
    assert before.bolt_count * boss_diameter_mm > 12.0  # 旧実装ならここで落ちる幅
    narrow = _named(_derived(_with_base(params, arm_width_mm=12.0)), "hub_plate__motor_arm_1")
    assert narrow.bolt_count == before.bolt_count
    assert narrow.bearing_area_mm2 == pytest.approx(before.bearing_area_mm2)


def test_shortening_the_arm_makes_the_seat_row_stop_fitting() -> None:
    """⚠️ **アームを短くすると座が並ばなくなる**（正しい軸で測っている証拠）。

    設計変数「機体中心 → 取付面」を内側へ寄せるとアーム長が 1:1 で縮む。
    座の並びに要る `bolt_count × 座径` を下回った時点で `joints` が拒否する。
    ⚠️ **旧実装（幅で測る）ではアームをいくら短くしてもこの関門は動かなかった**
    ——アーム幅は設計変数と無関係だからである。ここが `joints` 側の関門であり、
    `derive_layout` の最小アーム長（当たり面 ÷ アーム厚 = 6.0mm）はまだ満たす
    値を選んでいる。
    """
    params = _params()
    base = params.chassis.base
    boss_diameter_mm = (
        joints_module.BOSS_DIAMETER_FACTOR * params.joint.insert_outer_diameter_mm
    )
    required_mm = joints_module.MIN_BOLTS_PER_FASTENED_JOINT * boss_diameter_mm

    # 座の並びにあと 0.4mm 足りないアーム長へ寄せる。
    target_arm_length_mm = required_mm - 0.4
    shortened = _with_base(
        params,
        hub_center_to_mount_face_mm=(
            target_arm_length_mm
            + base.hub_outer_diameter_mm / 2.0
            - params.chassis.bracket.mount_face_to_wheel_center_mm
        ),
    )
    layout = derive_layout(shortened)
    assert layout.arm_length_mm == pytest.approx(target_arm_length_mm)
    # ⚠️ アーム長の下限（当たり面 ÷ アーム厚）はまだ満たしている＝落ちるのは
    # `layout` ではなく `joints` の関門である。
    assert layout.arm_length_mm > (
        shortened.chassis.joint_local.min_bearing_area_mm2 / base.arm_thickness_mm
    )

    with pytest.raises(GeometryError) as excinfo:
        derive_joints(layout, shortened)
    message = str(excinfo.value)
    assert "hub_plate__motor_arm_1" in message
    assert repr(required_mm) in message, message


def test_a_joint_below_its_own_floor_cannot_be_constructed() -> None:
    """下限を下回る当たり面を持つ接合部は構築できない（Postconditions）。"""
    with pytest.raises(GeometryError):
        JointSpec(
            name="joint",
            members=("a", "b"),
            bolt_count=2,
            bolt_length_mm=20.0,
            insert_count=2,
            dowel_count=0,
            bearing_area_mm2=10.0,
            print_normal_axis="x",
            min_bearing_area_mm2=90.0,
        )


# ---------------------------------------------------------------------------
# 6. 分割数の導出は部品の種類で分かれる（要件 2.1）
# ---------------------------------------------------------------------------


def test_the_annular_part_uses_the_upstream_segment_derivation() -> None:
    """円環部品（アダプタ）は上流 `required_segment_count` を用いる。"""
    params = _params()
    counts = segment_counts(params)
    adapter = params.chassis.adapter
    outer_diameter_mm = params.trash_can.bottom_outer_diameter_mm + 2.0 * (
        adapter.seat_clearance_mm + adapter.wall_thickness_mm
    )
    assert counts["adapter_segment"] == required_segment_count(
        outer_diameter_mm, params.printing
    )


def test_the_phase_determined_parts_follow_the_wheel_count() -> None:
    """位相が決まっている部品は輪数から従属する（円環の等分に載せない）。"""
    counts = segment_counts(_with_wheel_count(_params(), 4))
    assert counts["motor_arm"] == 4
    assert counts["cable_guide"] == 4
    assert counts["service_stand"] == 4
    assert counts["hub_plate"] == 1


def test_a_non_annular_part_is_not_approximated_as_an_annulus() -> None:
    """⚠️ 造形可能寸法を動かしても位相従属の部品の分割数は動かない。

    円環として近似していれば、造形面を狭めた瞬間に**過大な分割数**が
    「正しい導出」の顔をして返る（research.md「Decision: 分割の導出を部品の
    種類で分ける」の Rationale）。
    """
    params = _params()
    narrow = replace(
        params, printing=replace(params.printing, build_x_mm=120.0, build_y_mm=120.0)
    )
    assert segment_counts(narrow)["motor_arm"] == params.chassis.base.wheel_count
    assert segment_counts(narrow)["adapter_segment"] > segment_counts(params)["adapter_segment"]


def test_a_diameter_no_split_can_solve_propagates_the_upstream_failure() -> None:
    """⚠️ 収まる分割数が存在しない径は、上流の失敗がそのまま伝播する。

    半径方向の広がりは分割数を増やしても縮まないため、造形面が座の半径より
    狭ければどの分割数でも収まらない（上流 `required_segment_count` の
    Invariants）。⚠️ **上流の失敗を包み直さない**（design.md「Error Strategy」）。
    """
    params = _params()
    tiny_printer = replace(
        params, printing=replace(params.printing, build_x_mm=20.0, build_y_mm=20.0)
    )
    with pytest.raises(UpstreamGeometryError):
        segment_counts(tiny_printer)


# ---------------------------------------------------------------------------
# 7. 断片の外接箱は上流の検査を関門とする（要件 2.2, 2.3）
# ---------------------------------------------------------------------------


def test_a_fragment_that_exceeds_the_build_volume_is_rejected() -> None:
    """超過する軸と超過量を示して拒否する（アームが造形面を超える配置半径）。"""
    params = _with_base(_params(), hub_center_to_mount_face_mm=300.0)
    with pytest.raises(GeometryError) as excinfo:
        _derived(params)
    message = str(excinfo.value)
    assert "motor_arm" in message
    assert "x" in message


def test_a_central_plate_that_exceeds_the_build_volume_is_rejected() -> None:
    """中央部は分割しないため、外接箱が関門になる（design.md Shapes 部品表）。"""
    params = _with_base(_params(), hub_outer_diameter_mm=200.0)
    with pytest.raises(GeometryError) as excinfo:
        _derived(params)
    assert "hub_plate" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 8. 記録は導出の写しである
# ---------------------------------------------------------------------------


def test_dump_writes_lf_sorted_keys_and_a_trailing_newline(tmp_path: Path) -> None:
    """整形は `config.dump_params` に揃える（LF・インデント2・キー整列・末尾改行）。"""
    path = tmp_path / "joint-schedule.json"
    dump_fastener_schedule(derive_fastener_schedule(derive_layout(_params()), _params()), path)
    raw = path.read_bytes()
    assert b"\r\n" not in raw
    assert raw.endswith(b"\n")
    document = json.loads(raw.decode("utf-8"))
    assert list(document) == sorted(document)
    assert document["schema_version"] == SCHEMA_VERSION


def test_the_record_carries_the_parameters_digest() -> None:
    """記録は寸法パラメータの識別子を持つ（design.md「Data Models」）。"""
    params = _params()
    schedule = derive_fastener_schedule(derive_layout(params), params)
    assert schedule.parameters_digest == parameters_digest(params.chassis)


def test_load_round_trips_dump(tmp_path: Path) -> None:
    """書き出して読み戻し、再度書き出すと同じバイト列になる（記録は写しである）。

    ⚠️ 記録は `_ROUND_DIGITS` で丸めた値を持つため、読み戻した値は導出値と
    ビット単位では一致しない（`layout` と同じ扱い）。往復で保たれるのは
    **記録そのもの**である。
    """
    params = _params()
    original = derive_fastener_schedule(derive_layout(params), params)
    path = tmp_path / "joint-schedule.json"
    dump_fastener_schedule(original, path)
    restored = load_fastener_schedule(path)
    assert [spec.name for spec in restored.joints] == [
        spec.name for spec in original.joints
    ]
    assert restored.parameters_digest == original.parameters_digest
    assert [line.count for line in restored.lines] == [
        line.count for line in original.lines
    ]
    again = tmp_path / "again.json"
    dump_fastener_schedule(restored, again)
    assert again.read_bytes() == path.read_bytes()


def test_load_rejects_an_unknown_key(tmp_path: Path) -> None:
    """あらゆる階層で未知キーを拒否する（項目名を示す）。"""
    params = _params()
    path = tmp_path / "joint-schedule.json"
    dump_fastener_schedule(derive_fastener_schedule(derive_layout(params), params), path)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["note"] = "手で足した項目"
    path.write_text(json.dumps(document), encoding="utf-8", newline="\n")
    with pytest.raises(ParameterError) as excinfo:
        load_fastener_schedule(path)
    assert "note" in str(excinfo.value)


def test_load_rejects_a_missing_key(tmp_path: Path) -> None:
    """欠けている項目を既定値で埋めない。"""
    params = _params()
    path = tmp_path / "joint-schedule.json"
    dump_fastener_schedule(derive_fastener_schedule(derive_layout(params), params), path)
    document = json.loads(path.read_text(encoding="utf-8"))
    del document["lines"]
    path.write_text(json.dumps(document), encoding="utf-8", newline="\n")
    with pytest.raises(ParameterError) as excinfo:
        load_fastener_schedule(path)
    assert "lines" in str(excinfo.value)


def test_load_rejects_lines_that_contradict_the_joints(tmp_path: Path) -> None:
    """⚠️ `lines` は `joints` から一意に決まる。食い違う記録を受け付けない。"""
    params = _params()
    path = tmp_path / "joint-schedule.json"
    dump_fastener_schedule(derive_fastener_schedule(derive_layout(params), params), path)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["lines"][0]["count"] += 1
    path.write_text(json.dumps(document), encoding="utf-8", newline="\n")
    with pytest.raises(ConsistencyError):
        load_fastener_schedule(path)


def test_load_rejects_a_dowel_line(tmp_path: Path) -> None:
    """⚠️ ダボは購入する締結部品ではない。種別として受け付けない。"""
    params = _params()
    path = tmp_path / "joint-schedule.json"
    dump_fastener_schedule(derive_fastener_schedule(derive_layout(params), params), path)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["lines"].append(
        {"designation": "M3", "kind": "dowel", "length_mm": 12.0, "count": 6}
    )
    path.write_text(json.dumps(document), encoding="utf-8", newline="\n")
    with pytest.raises((ParameterError, ConsistencyError)):
        load_fastener_schedule(path)


def test_the_shipped_record_matches_the_current_derivation(tmp_path: Path) -> None:
    """出荷されている記録が、現在の寸法からの導出と**バイト単位で**一致する。

    ⚠️ 記録は手で編集する対象ではない。寸法を変えたら書き出し直す。
    """
    params = _params()
    expected = tmp_path / "joint-schedule.json"
    dump_fastener_schedule(derive_fastener_schedule(derive_layout(params), params), expected)
    assert DEFAULT_JOINT_SCHEDULE_PATH.exists()
    assert DEFAULT_JOINT_SCHEDULE_PATH.read_bytes() == expected.read_bytes()
    assert load_fastener_schedule().parameters_digest == parameters_digest(params.chassis)


def test_a_fastener_line_is_an_immutable_value() -> None:
    """`FastenerLine` は凍結された値である。"""
    line = FastenerLine(designation="M3", kind="bolt", length_mm=20.0, count=6)
    with pytest.raises(Exception):
        line.count = 7  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 静的検査（数値リテラル・形状ライブラリ・上流の内部モジュール）
# ---------------------------------------------------------------------------


#: 文言のなかに紛れ込んだ寸法を捕まえる正規表現（`"12.5mm"` / `"1.5 mm"` /
#: `"90mm^2"`）。⚠️ **数値リテラルだけを見る検査では文字列に書いた寸法が素通り
#: する**——docstring や `ASSUMPTIONS` の根拠文は、上流の値を書き写すと上流が
#: 測り直したときに黙って偽の数を述べ続ける（要件 6.3）。
#: ⚠️ 数字が **mm の直前にある**ことを要求するため、`要件 2.10` のような条項番号や
#: `insert_length_mm` / `bearing_area_mm2` のような項目名（mm の前が英字）には
#: 一致しない。
_DIMENSION_IN_TEXT = re.compile(r"\d(?:[\d,]*\.?\d*)\s*mm")

#: 整数リテラルのうち、寸法ではありえないものとして無条件に許す値。
#: ⚠️ **narrow に保つこと。** 0 / 1 / 2 は本モジュールでは個数・添字・
#: 「2部材」「両側」「べき乗の2」としてのみ現れる構造的な値であり、
#: ミリメートルの寸法をこの3値で表す箇所は無い（寸法はすべて設定ファイルと
#: 上流の継手方針から来る）。3 以上の整数は下の文脈判定を通らなければ違反である。
_STRUCTURAL_INTS = frozenset({0, 1, 2})

#: 値だけでは許せないが、**現れる文脈**で寸法でないと判定できる整数。
#: - `math.pi / 4`: 円の面積式の定数（直径から面積を出す `/4`）。
#: - `_ROUND_DIGITS = 9`: 記録の丸め桁数であって長さではない。
#: ⚠️ どちらも「その1箇所に現れたときだけ」許す。同じ 4 や 9 を別の場所へ書けば
#: 違反として現れる。
_ROUND_DIGITS_NAME = "_ROUND_DIGITS"


def _contextually_allowed_int_nodes(tree: ast.AST) -> set[int]:
    """寸法でないと文脈から言い切れる整数リテラルの `id()` を集める。"""
    allowed: set[int] = set()
    for node in ast.walk(tree):
        # `math.pi / 4` の 4（円の面積式の定数）。
        if (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Div)
            and isinstance(node.left, ast.Attribute)
            and node.left.attr == "pi"
            and isinstance(node.right, ast.Constant)
        ):
            allowed.add(id(node.right))
        # `_ROUND_DIGITS: Final[int] = 9`（丸め桁数であって長さではない）。
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == _ROUND_DIGITS_NAME
            and isinstance(node.value, ast.Constant)
        ):
            allowed.add(id(node.value))
    return allowed


def test_module_embeds_no_dimensional_literal() -> None:
    """⚠️ 寸法・長さ・面積の数値をコードへ埋め込まない（0.0 を除く）。

    当たり面も下限もインサート長も、寸法パラメータと上流の継手方針が正である。
    数を1つでも書けば、設定ファイルを書き換えても導出が動かない箇所が生まれる。

    ⚠️ **`float` のリテラルだけを見ても足りない。** 寸法は `int`（`_X = 12`）
    としても、文字列のなか（`"12.5mm"`）としても書ける。前者は型が違うだけで
    同じ埋め込みであり、後者は根拠の文言が上流の値を写したまま古びる経路である。
    本検査は3つとも捕まえる。
    """
    tree = ast.parse(_JOINTS_SOURCE)
    contextually_allowed = _contextually_allowed_int_nodes(tree)
    float_literals: list[float] = []
    int_literals: list[tuple[int, int]] = []
    text_dimensions: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant):
            continue
        value = node.value
        if isinstance(value, bool):
            # ⚠️ `bool` は `int` の派生である。真偽値は寸法ではない。
            continue
        if isinstance(value, float):
            if value != 0.0:
                float_literals.append(value)
        elif isinstance(value, int):
            if value not in _STRUCTURAL_INTS and id(node) not in contextually_allowed:
                int_literals.append((node.lineno, value))
        elif isinstance(value, str):
            found = _DIMENSION_IN_TEXT.search(value)
            if found is not None:
                text_dimensions.append((node.lineno, found.group(0)))
    assert float_literals == [], f"寸法の数値リテラルが埋め込まれている: {float_literals}"
    assert int_literals == [], f"寸法の整数リテラルが埋め込まれている: {int_literals}"
    assert text_dimensions == [], (
        f"文言のなかに寸法が書き写されている: {text_dimensions}"
        "（上流が測り直したときに黙って偽の数になる。項目名で指すこと）"
    )


@pytest.mark.parametrize(
    "injected",
    [
        "_X: Final[float] = 12.5\n",
        "_X: Final[int] = 12\n",
        "_X: Final[int] = 4\n",
        '_X: Final[str] = "底の肉厚は 1.5mm 相当である"\n',
        '_X: Final[str] = "座の面積は 90mm^2 である"\n',
        '_X: Final[str] = "ボルト長は 20.7 mm である"\n',
    ],
    ids=["float", "int", "int-that-is-allowed-elsewhere", "text-mm", "text-mm2", "text-spaced-mm"],
)
def test_the_literal_guard_catches_an_injected_dimension(injected: str) -> None:
    """⚠️ **検査そのものが効いていることを確かめる。**

    数値リテラルだけを見る検査は `int` と文字列を素通りさせた（実際に素通り
    した）。⚠️ 文脈で許した 4（`math.pi / 4`）も、**別の場所へ書けば違反**で
    なければならない——許可は値ではなく現れ方に付いている。
    """
    tree = ast.parse(_JOINTS_SOURCE + injected)
    contextually_allowed = _contextually_allowed_int_nodes(tree)
    violations = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and not isinstance(node.value, bool)
        and (
            (isinstance(node.value, float) and node.value != 0.0)
            or (
                isinstance(node.value, int)
                and node.value not in _STRUCTURAL_INTS
                and id(node) not in contextually_allowed
            )
            or (
                isinstance(node.value, str)
                and _DIMENSION_IN_TEXT.search(node.value) is not None
            )
        )
    ]
    assert violations, f"注入した寸法 {injected!r} を検査が見逃した"


def test_module_does_not_import_the_shape_library() -> None:
    """⚠️ `joints` は build123d を import しない（当たり面は解析的に算出する）。

    design.md `#### Joints` Risks が述べるとおり、当たり面は本来なら形状から
    採る量である。⚠️ **それができない層であることが、解析的な算出と
    `test_chassis_invariants.py`（`cad` extra）での突き合わせの理由である。**
    """
    imported: set[str] = set()
    for node in ast.walk(ast.parse(_JOINTS_SOURCE)):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert "build123d" not in imported


def test_module_imports_the_upstream_only_through_its_public_entry() -> None:
    """上流の内部モジュールへ直接 import しない（design.md Allowed Dependencies）。"""
    tree = ast.parse(_JOINTS_SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            "catch_mechanism"
        ):
            assert node.module == "catch_mechanism"


# ---------------------------------------------------------------------------
# 9. ⚠️ 購入部品を挟む接合部はナットで受ける（要件 2.6 / A-5 の適用範囲）
# ---------------------------------------------------------------------------


def test_the_trash_can_retention_is_backed_by_nuts_not_inserts() -> None:
    """⚠️ **`adapter__trash_can` はインサートを持たない**（要件 2.6 / A-5）。

    要件 2.6 と A-5 が「貫通ボルト＋金属インサート＋広い当たり面」を課している
    のは⚠️ **モータ反力を受ける接合部**であり、インサートが解いている問題は
    「樹脂へねじを立てると層間で抜ける」ことである。この家族が挟むのは購入部品
    （ゴミ箱）であり、⚠️ **相手側は樹脂ではないためインサートの居場所が無い**
    ——貫通ボルトとナットで挟むのが正しい受け方である。
    """
    specs = _derived(_params())
    retention = _named(specs, "adapter__trash_can")
    assert retention.bolt_count > 0
    assert retention.insert_count == 0

    # ⚠️ 樹脂どうしの接合部は従来どおりインサートで受ける（適用範囲の区別）。
    assert _named(specs, "hub_plate__motor_arm_1").insert_count == 2
    assert _named(specs, "hub_plate__adapter_segment_1").insert_count == 2


def test_the_schedule_carries_nut_rows_for_the_insert_free_joint() -> None:
    """⚠️ **インサートで受けないボルトはナットの行として現れる**（要件 2.10）。

    「調達できる形の一覧」であるため、⚠️ **受け方の違いが一覧に出ないと、
    ナットを買い忘れて組立が止まる**。行の数量は接合部から一意に決まる。
    """
    params = _params()
    schedule = derive_fastener_schedule(derive_layout(params), params)
    retention = _named(schedule.joints, "adapter__trash_can")

    tray = _named(
        schedule.joints, f"motor_arm_{BATTERY_TRAY_ARM_INDEX}__battery_tray"
    )
    nut_lines = [line for line in schedule.lines if line.kind == "nut"]
    assert len(nut_lines) == 1
    # ⚠️ **インサートで受けない接合部は1つとは限らない。** ゴミ箱のクランプ
    # （相手が購入部品）とバッテリトレイの締結（向こう側の耳の肉が上流の
    # インサート長より薄い）の2つがあり、⚠️ **ナットの行はその総和でなければ
    # ならない**——片方だけを数えると、買い忘れが「一覧が正しい」という顔をして
    # 残る。
    assert nut_lines[0].count == retention.bolt_count + tray.bolt_count
    assert retention.insert_count == 0
    assert tray.insert_count == 0
    assert nut_lines[0].length_mm is None
    assert nut_lines[0].designation == params.joint.bolt_designation

    insert_lines = [line for line in schedule.lines if line.kind == "insert"]
    assert len(insert_lines) == 1
    assert insert_lines[0].count == sum(spec.insert_count for spec in schedule.joints)
    # ⚠️ **インサートで受けない接合部のボルトは、インサートの合計に含まれない。**
    # 数えるのは「ボルトの総数 − ナットで受けるボルトの総数」であり、
    # ⚠️ 名指しした1つを引くのではない（家族が増えたときに黙って合わなくなる）。
    assert insert_lines[0].count == sum(
        spec.bolt_count for spec in schedule.joints
    ) - nut_lines[0].count


def test_more_retention_points_move_both_the_bolts_and_the_nuts() -> None:
    """保持箇所を増やすと、ボルトとナットが**同じだけ**増える（要件 2.10）。"""
    params = _params()
    chassis = params.chassis
    more = replace(
        params,
        chassis=replace(
            chassis, adapter=replace(chassis.adapter, retention_point_count=5)
        ),
    )
    before = derive_fastener_schedule(derive_layout(params), params)
    after = derive_fastener_schedule(derive_layout(more), more)

    def _nuts(schedule: object) -> int:
        return sum(line.count for line in schedule.lines if line.kind == "nut")  # type: ignore[attr-defined]

    assert _named(after.joints, "adapter__trash_can").bolt_count == 5
    assert _nuts(after) - _nuts(before) == 2
    assert _named(after.joints, "adapter__trash_can").insert_count == 0


# ---------------------------------------------------------------------------
# 9. バッテリトレイと段積み土台の家族（タスク 3.4 / 要件 7.1, 7.2, 7.10, 7.13）
# ---------------------------------------------------------------------------


def test_the_deck_rises_are_measured_from_the_can_bottom() -> None:
    """⚠️ **段の高さは缶の底からの立ち上がりとして導かれる**（要件 7.11）。

    ⚠️ **駆動ベースの高さに依存しない**——だからこそ `segment_counts` が
    `ChassisLayout` 無しで段の分割数を導ける。
    """
    params = _params()
    board = params.chassis.board
    can = params.trash_can
    assert board_deck_rise_mm(params) == pytest.approx(
        can.bottom_thickness_mm + board.can_clearance_mm
    )
    # ⚠️ **重ね代を含む。** 受け止めデッキは板から重ね代ぶん下へ垂れる筒を
    # 持ち、⚠️ **「部品の頭 ＋ 放熱の隙間」を空けるのは板ではなく筒の下端**で
    # ある。重ね代を落とすと筒が搭載部品の居場所へ入り込む。
    assert catch_deck_rise_mm(params) == pytest.approx(
        board_deck_rise_mm(params)
        + board.deck_thickness_mm
        + board.standoff_height_mm
        + board.component_height_mm
        + board.cooling_gap_mm
        + deck_collar_length_mm(params)
    )
    # ⚠️ 板だけを「部品の頭 ＋ 隙間」に置く式との差は、ちょうど重ね代である。
    assert catch_deck_rise_mm(params) - deck_collar_length_mm(params) == pytest.approx(
        board_deck_rise_mm(params)
        + board.deck_thickness_mm
        + board.standoff_height_mm
        + board.component_height_mm
        + board.cooling_gap_mm
    )


def test_the_deck_outer_diameters_come_from_the_can_at_those_heights() -> None:
    """⚠️ **段の外径はその高さの缶の内径から導かれる**（要件 7.11）。"""
    params = _params()
    can = params.trash_can
    clearance_mm = params.chassis.board.can_clearance_mm
    slope = math.tan(math.radians(can.taper_deg))
    for diameter_mm, rise_mm in (
        (board_deck_outer_diameter_mm(params), board_deck_rise_mm(params)),
        (catch_deck_outer_diameter_mm(params), catch_deck_rise_mm(params)),
    ):
        expected_mm = 2.0 * (
            can.bottom_outer_diameter_mm / 2.0
            - can.bottom_thickness_mm
            + rise_mm * slope
            - clearance_mm
        )
        assert diameter_mm == pytest.approx(expected_mm, abs=1e-9)
    # ⚠️ 上の段のほうが太い（缶が上へ広がることを式が実際に見ている）。
    assert catch_deck_outer_diameter_mm(params) > board_deck_outer_diameter_mm(params)


def test_a_can_whose_taper_leaves_no_deck_is_rejected() -> None:
    """⚠️ 隙間を引いて段が残らない採寸値は拒否される（黙って負の径を返さない）。"""
    params = _params()
    huge = replace(
        params,
        chassis=replace(
            params.chassis,
            board=replace(params.chassis.board, can_clearance_mm=1000.0),
        ),
    )
    with pytest.raises(GeometryError) as excinfo:
        board_deck_outer_diameter_mm(huge)
    assert "can_clearance_mm" in str(excinfo.value)


def test_the_riser_outer_diameter_is_the_hub_plate_outer_diameter() -> None:
    """⚠️ 立ち上がりの外径は中央部の外径そのものである（要件 7.10）。"""
    params = _params()
    assert deck_riser_outer_diameter_mm(params) == params.chassis.base.hub_outer_diameter_mm


def test_the_deck_collar_length_holds_the_bolt_seat_ring() -> None:
    """⚠️ 重ね代は座の外径の2倍である（`DECK_COLLAR_LENGTH_FORMULA`）。"""
    params = _params()
    boss_mm = BOSS_DIAMETER_FACTOR * params.joint.insert_outer_diameter_mm
    assert deck_collar_length_mm(params) == pytest.approx(
        BOSS_DIAMETER_FACTOR * boss_mm
    )
    assert deck_collar_length_mm(params) > boss_mm


def test_the_tray_ear_length_follows_the_bolt_count() -> None:
    """⚠️ 耳の長さは本数 × 座の外径である（`BATTERY_TRAY_EAR_LENGTH_FORMULA`）。"""
    params = _params()
    layout = derive_layout(params)
    tray_joint = _named(
        derive_joints(layout, params),
        f"motor_arm_{BATTERY_TRAY_ARM_INDEX}__battery_tray",
    )
    boss_mm = BOSS_DIAMETER_FACTOR * params.joint.insert_outer_diameter_mm
    assert battery_tray_ear_length_mm(layout, params) == pytest.approx(
        tray_joint.bolt_count * boss_mm
    )


def test_the_tray_joint_is_tangential_and_backed_by_nuts() -> None:
    """⚠️ トレイの締結は接線方向であり、ナットで受ける（要件 2.6, 2.8）。"""
    params = _params()
    spec = _named(
        derive_joints(derive_layout(params), params),
        f"motor_arm_{BATTERY_TRAY_ARM_INDEX}__battery_tray",
    )
    assert spec.print_normal_axis != LAYER_NORMAL_AXIS
    assert spec.insert_count == 0
    assert spec.dowel_count == 0
    assert spec.members == (f"motor_arm_{BATTERY_TRAY_ARM_INDEX}", "battery_tray")
    # ⚠️ 積み上がりは「手前の耳 ＋ アームの幅 ＋ 向こうの耳」である。
    assert spec.bolt_length_mm == pytest.approx(
        2.0 * params.chassis.battery.tray_wall_thickness_mm
        + params.chassis.base.arm_width_mm
        + params.joint.insert_length_mm
        + params.chassis.joint_local.fastener_length_margin_mm
    )


def test_the_deck_seat_joint_carries_no_fasteners() -> None:
    """⚠️ 段↔アダプタは締結部品を持たない拘束である（要件 7.10）。"""
    params = _params()
    schedule = derive_fastener_schedule(derive_layout(params), params)
    spec = _named(schedule.joints, DECK_SEAT_JOINT_NAME)
    assert spec.bolt_count == 0
    assert spec.insert_count == 0
    assert spec.dowel_count == 0
    assert spec.bolt_length_mm == 0.0
    assert spec.print_normal_axis != LAYER_NORMAL_AXIS
    assert spec.bearing_area_mm2 == pytest.approx(
        math.pi
        * deck_riser_outer_diameter_mm(params)
        * params.chassis.adapter.wall_thickness_mm
    )
    assert spec.bearing_area_mm2 >= params.joint.min_bearing_area_mm2


def test_the_deck_to_deck_joints_follow_the_upper_deck_split() -> None:
    """⚠️ 段どうしの接合部の件数は上の段の分割数そのものである（要件 7.13）。"""
    params = _params()
    counts = segment_counts(params)
    specs = derive_joints(derive_layout(params), params)
    names = [
        spec.name for spec in specs if spec.name.startswith("board_deck__catch_deck")
    ]
    assert len(names) == counts["catch_deck"]
    for name in names:
        spec = _named(specs, name)
        assert spec.print_normal_axis != LAYER_NORMAL_AXIS
        assert spec.insert_count == spec.bolt_count
        assert spec.dowel_count == 0
        assert spec.bolt_length_mm == pytest.approx(
            params.chassis.board.deck_thickness_mm
            + params.joint.insert_length_mm
            + params.chassis.joint_local.fastener_length_margin_mm
        )


def test_the_assumptions_argue_the_retention_layout_from_the_lip_the_cut_leaves() -> None:
    """⚠️ **要件 6.8（改訂版）の根拠は「底を抜いた後に残る縁の幅」である。**

    底が残っていた頃の根拠——「底は薄く変形しやすい」「底の外周を押さえる」——は
    ⚠️ **もう成立しない**（design.md 決定 4b で底は抜かれ、保持ボルトは**側壁**を
    貫く）。根拠の記録が古い設計を述べたままだと、⚠️ **配置を見直す人が、
    もう存在しない底を根拠に読む**。
    """
    entry = next(record for record in ASSUMPTIONS if "6.8" in record)
    # 縁の幅は「底の外径 − 切り取り径」であり、両方の出どころが記録に現れる。
    assert "TrashCanMeasurements.bottom_outer_diameter_mm" in entry
    assert "adapter.bottom_cut_diameter_mm" in entry
    assert "縁の幅" in entry
    # 締結が貫くのは側壁であり、側壁の変形しやすさが根拠に現れる。
    assert "側壁" in entry
    # ⚠️ 底を押さえる旧設計の主張が残っていないこと。
    assert "底の外周" not in entry
    assert "ゴミ箱の底は薄く" not in entry
    # ⚠️ 上流の値そのものは書き写さない（要件 6.3）。
    assert "bottom_thickness_mm が正である" in entry


def test_the_assumptions_record_the_cable_guide_hardware_gap() -> None:
    """⚠️ **配線ガイドの取付ねじが締結部品一覧に現れないことを記録に残す。**

    ガイドが受けるのは配線と搭載物の重さだけであり、締結の軸は積層方向である
    ——スタンドオフと同じ理由で接合部としては記録できない。⚠️ **そのぶん要件 2.10 の
    調達一覧に欠けが生まれる**ため、タスク 5.5 への申し送りを残す（調達で
    気付くのでは遅い）。
    """
    entry = next(record for record in ASSUMPTIONS if "配線ガイド" in record)
    assert "mount_bolt_radii_mm" in entry
    assert "5.5" in entry
    # ⚠️ 側面へ留めない理由（重ね代が伸びると掴める帯が消える）を残す。
    assert "ARM_JOINT_LAP_LENGTH_FORMULA" in entry


def test_the_assumptions_record_the_standoff_hardware_gap() -> None:
    """⚠️ **要件 2.10 の欠けを記録として残す**（要件 11.7 / タスク 5.5 への申し送り）。

    基板を留めるスタンドオフとインサートは接合部として記録できない（締結の軸が
    積層方向である）。⚠️ **そのぶん締結部品一覧に現れない**ことを、
    ⚠️ **一覧そのものではなく前提の記録に残す**——調達で気付くのでは遅い。
    """
    joined = "".join(ASSUMPTIONS)
    assert "mount_boss_angles_deg" in joined
    assert "5.5" in joined
