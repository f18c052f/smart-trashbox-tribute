"""造形制約の検査と分割数の導出（タスク 2.1、要件 2.2, 2.3, 2.4, 2.7, 2.8, 8.3）。

本ファイルが固定するのは design.md「Constraints」の Service Interface /
Postconditions / Invariants と、tasks.md タスク 2.1 の「観測可能な完了状態」である。

1. **外接箱の検査が超過している軸と超過量を返す**こと（要件 2.2, 2.3, 2.4）
2. **分割数が導出値である**こと。外径を増やすと導出される分割数が単調に増え、
   導出した分割数での扇形が造形可能寸法に収まること（要件 8.3 / design.md
   Postconditions）
3. ⚠️ **斜め配置（正方形の対角線）を仮定しない**こと（design.md「Constraints」/
   「Testing Strategy」Unit Tests 4）。φ285 を 180mm 機で割るとき、対角線
   （254.6mm）を使えば 4 分割で足りるが、軸並行の外接箱では 5 分割が要る
4. **現実的な上限までに収まる分割数が存在しない場合は例外で拒否する**こと
   （design.md Invariants）。⚠️ 黙って大きな分割数を返さない
5. **材料の許可検査と継手の当たり面の下限検査**（要件 2.5, 2.6, 8.4）
6. **切削加工を要する形状を扱わない方針**が検査の対象範囲として明示されること
   （要件 2.8）

⚠️ 扇形の外接箱は**弦長だけではない**。弦（接線方向の広がり）に直交して半径方向の
広がりが同じ造形面を占めるため、本ファイルは扇形の輪郭を実際に標本化して外接箱を
求める独立した参照実装（`_sampled_sector_extent`）を持ち、実装の
`sector_envelope` がそれと一致することを突き合わせる。実装と同じ式をテストへ
書き写すと、式そのものの誤りを検出できない。

ファイル名について: `tests/` 配下には `__init__.py` が無く pytest の import-mode も
既定（prepend）のため、テストモジュール名はセッション全体でフラットである。
design.md「Directory Structure」が挙げる `test_constraints.py` は将来
`tests/trajectory_sim/test_constraints.py` 等と衝突しうるため使えない。既存の
`test_catch_params.py` / `test_catch_config.py` に倣い `test_catch_constraints.py`
とする（tasks.md「Implementation Notes」）。
"""

from __future__ import annotations

import math
from dataclasses import FrozenInstanceError, fields

import pytest

from catch_mechanism import constraints as constraints_module
from catch_mechanism.constraints import (
    MAX_SEGMENT_COUNT,
    BuildViolation,
    Envelope,
    check_envelope,
    check_joint,
    check_material,
    required_segment_count,
    sector_envelope,
)
from catch_mechanism.errors import CatchMechanismError, GeometryError, ParameterError
from catch_mechanism.params import ALLOWED_MATERIALS, JointPolicy, PrintingConstraints


def _printing(
    *,
    build_x_mm: float = 180.0,
    build_y_mm: float = 180.0,
    build_z_mm: float = 180.0,
    material: str = "PETG",
    segment_margin_mm: float = 5.0,
) -> PrintingConstraints:
    """`configs/catch_mechanism/dimensions.json` 相当の造形制約を作る補助。"""
    return PrintingConstraints(
        build_x_mm=build_x_mm,
        build_y_mm=build_y_mm,
        build_z_mm=build_z_mm,
        material=material,
        material_density_g_cm3=1.27,
        segment_margin_mm=segment_margin_mm,
    )


def _joint(*, min_bearing_area_mm2: float = 60.0) -> JointPolicy:
    """`configs/catch_mechanism/dimensions.json` 相当の継手方針を作る補助。"""
    return JointPolicy(
        bolt_designation="M3",
        through_hole_diameter_mm=3.4,
        insert_outer_diameter_mm=4.6,
        insert_length_mm=5.7,
        dowel_diameter_mm=3.0,
        min_bearing_area_mm2=min_bearing_area_mm2,
    )


def _sampled_sector_extent(
    outer_diameter_mm: float, segment_count: int, samples: int = 20001
) -> tuple[float, float]:
    """扇形の輪郭を標本化して軸並行の外接箱の辺長を求める独立参照実装。

    円環を `segment_count` 等分した扇形を、対称軸を +X 軸に合わせ、頂点（円の
    中心）を原点に置いた姿勢で考える。輪郭は「原点」と「半径 R の円弧
    （角度 ±π/n）」からなり、2本の直線辺はこれらの凸包の内側にあるため外接箱の
    辺長には影響しない。

    ⚠️ 実装の式（`sector_envelope`）とは独立に、幾何そのものから外接箱を求める。
    """
    radius = outer_diameter_mm / 2.0
    half_angle = math.pi / segment_count
    xs = [0.0]
    ys = [0.0]
    for index in range(samples + 1):
        theta = -half_angle + 2.0 * half_angle * index / samples
        xs.append(radius * math.cos(theta))
        ys.append(radius * math.sin(theta))
    return max(xs) - min(xs), max(ys) - min(ys)


DIAMETER_SWEEP: tuple[float, ...] = tuple(float(value) for value in range(60, 351, 10))


# --- 値型 ---------------------------------------------------------------


def test_envelope_and_violation_have_the_fields_declared_by_the_design() -> None:
    """`Envelope` / `BuildViolation` が design.md の Service Interface と一致する。"""
    assert [field.name for field in fields(Envelope)] == ["x_mm", "y_mm", "z_mm"]
    assert [field.name for field in fields(BuildViolation)] == [
        "part_name",
        "axis",
        "envelope_mm",
        "limit_mm",
        "excess_mm",
    ]


def test_envelope_is_frozen() -> None:
    """`Envelope` は不変である（値型として比較・記録に使える）。"""
    envelope = Envelope(x_mm=10.0, y_mm=20.0, z_mm=30.0)
    with pytest.raises(FrozenInstanceError):
        envelope.x_mm = 1.0  # type: ignore[misc]


@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf")])
def test_envelope_rejects_non_positive_or_non_finite_extents(bad: float) -> None:
    """外接箱の辺長は正の有限値でなければならない。"""
    with pytest.raises(ParameterError):
        Envelope(x_mm=bad, y_mm=10.0, z_mm=10.0)


# --- 外接箱の検査（要件 2.2, 2.3, 2.4）---------------------------------


def test_check_envelope_returns_no_violation_when_it_fits() -> None:
    """造形可能寸法に収まる部品は違反を返さない。"""
    printing = _printing()
    assert check_envelope("rim", Envelope(x_mm=100.0, y_mm=100.0, z_mm=20.0), printing) == ()


def test_check_envelope_treats_exact_limit_as_fitting() -> None:
    """造形可能寸法とちょうど等しい辺は超過ではない。"""
    printing = _printing()
    envelope = Envelope(x_mm=180.0, y_mm=180.0, z_mm=180.0)
    assert check_envelope("rim", envelope, printing) == ()


def test_check_envelope_reports_axis_and_excess() -> None:
    """超過している軸と超過量を返す（要件 2.4）。"""
    printing = _printing()
    violations = check_envelope("rim", Envelope(x_mm=185.0, y_mm=100.0, z_mm=20.0), printing)
    assert len(violations) == 1
    violation = violations[0]
    assert violation.part_name == "rim"
    assert violation.axis == "x"
    assert violation.envelope_mm == pytest.approx(185.0)
    assert violation.limit_mm == pytest.approx(180.0)
    assert violation.excess_mm == pytest.approx(5.0)


def test_check_envelope_reports_every_violated_axis_in_order() -> None:
    """複数の軸が超過していれば、最初の1件で打ち切らずに全件返す。"""
    printing = _printing(build_x_mm=100.0, build_y_mm=110.0, build_z_mm=120.0)
    violations = check_envelope("rim", Envelope(x_mm=150.0, y_mm=160.0, z_mm=170.0), printing)
    assert [violation.axis for violation in violations] == ["x", "y", "z"]
    assert [violation.excess_mm for violation in violations] == pytest.approx([50.0, 50.0, 50.0])


def test_check_envelope_does_not_apply_the_segment_margin() -> None:
    """外接箱の検査は造形可能寸法そのものと比べる（余裕は分割数の導出側の量）。"""
    printing = _printing(segment_margin_mm=50.0)
    assert check_envelope("rim", Envelope(x_mm=179.0, y_mm=179.0, z_mm=1.0), printing) == ()


# --- 分割数の導出（要件 8.3 / design.md Postconditions）---------------


def test_sector_envelope_matches_an_independent_geometric_sampling() -> None:
    """`sector_envelope` が扇形の実際の軸並行外接箱と一致する。

    ⚠️ 弦長は外接箱の**片方の辺**でしかない。半径方向の広がりが直交する軸を
    占めることを、標本化した輪郭との突き合わせで固定する。
    """
    for segment_count in range(1, MAX_SEGMENT_COUNT + 1):
        envelope = sector_envelope(285.0, segment_count, 18.0)
        width, height = _sampled_sector_extent(285.0, segment_count)
        assert envelope.x_mm == pytest.approx(width, abs=1e-3)
        assert envelope.y_mm == pytest.approx(height, abs=1e-3)
        assert envelope.z_mm == pytest.approx(18.0)


def test_single_segment_envelope_is_the_whole_ring() -> None:
    """1分割（分割しない）の外接箱は外径そのものである。"""
    envelope = sector_envelope(120.0, 1, 10.0)
    assert envelope.x_mm == pytest.approx(120.0)
    assert envelope.y_mm == pytest.approx(120.0)


def test_required_segment_count_is_one_when_the_ring_already_fits() -> None:
    """造形可能寸法（余裕を引いた値）に収まる外径は分割を要さない。"""
    assert required_segment_count(170.0, _printing()) == 1


def test_required_segment_count_does_not_assume_diagonal_placement() -> None:
    """φ285 / 180mm 機で 5 分割が返る（design.md「Testing Strategy」Unit Tests 4）。

    ⚠️ 正方形の対角線（180√2 = 254.6mm）を使ってよいなら 4 分割（弦長 201.5mm）で
    足りてしまう。軸並行の外接箱で判定する限り 4 分割は収まらない。
    """
    printing = _printing()
    assert required_segment_count(285.0, printing) == 5
    assert required_segment_count(285.0, printing) != 4


def test_required_segment_count_increases_monotonically_with_diameter() -> None:
    """外径を増やすと導出される分割数が単調に増える（tasks.md 観測可能な完了状態）。"""
    printing = _printing()
    counts = [required_segment_count(diameter, printing) for diameter in DIAMETER_SWEEP]
    assert counts == sorted(counts)
    assert counts[0] == 1
    assert counts[-1] > counts[0]


def test_derived_segment_fits_the_build_envelope() -> None:
    """導出した分割数での扇形が造形可能寸法に収まる（design.md Postconditions）。"""
    printing = _printing()
    for diameter in DIAMETER_SWEEP:
        count = required_segment_count(diameter, printing)
        envelope = sector_envelope(diameter, count, 18.0)
        assert check_envelope("rim", envelope, printing) == ()
        width, height = _sampled_sector_extent(diameter, count)
        limit = min(printing.build_x_mm, printing.build_y_mm) - printing.segment_margin_mm
        assert max(width, height) <= limit + 1e-6


def test_derived_segment_count_is_minimal() -> None:
    """1つ少ない分割数では収まらない（最小性）。"""
    printing = _printing()
    limit = min(printing.build_x_mm, printing.build_y_mm) - printing.segment_margin_mm
    for diameter in DIAMETER_SWEEP:
        count = required_segment_count(diameter, printing)
        if count == 1:
            continue
        width, height = _sampled_sector_extent(diameter, count - 1)
        assert max(width, height) > limit


def test_required_segment_count_honours_the_segment_margin() -> None:
    """余裕の量が導出に効く。⚠️ 余裕 0 でも破綻しない。"""
    assert required_segment_count(180.0, _printing(segment_margin_mm=0.0)) == 1
    assert required_segment_count(180.0, _printing(segment_margin_mm=5.0)) == 3


def test_required_segment_count_rejects_a_diameter_that_never_fits() -> None:
    """収まる分割数が存在しない外径を例外で拒否する（design.md Invariants）。

    ⚠️ 半径方向の広がりは分割数を増やしても縮まない。弦長だけを見る実装は
    大きな分割数を黙って返してしまう。
    """
    printing = _printing()
    with pytest.raises(GeometryError) as excinfo:
        required_segment_count(360.0, printing)
    message = str(excinfo.value)
    assert "x" in message
    assert "5.0" in message
    assert "360.0" in message


def test_rejection_is_a_catch_mechanism_error() -> None:
    """拒否は本パッケージの例外階層に属する（`cli` の終了コード 2）。"""
    with pytest.raises(CatchMechanismError):
        required_segment_count(5000.0, _printing())


def test_required_segment_count_never_exceeds_the_practical_upper_bound() -> None:
    """返る分割数は現実的な上限を超えない（黙って大きな分割数を返さない）。"""
    printing = _printing()
    for diameter in DIAMETER_SWEEP:
        assert 1 <= required_segment_count(diameter, printing) <= MAX_SEGMENT_COUNT


@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf")])
def test_required_segment_count_rejects_invalid_diameter(bad: float) -> None:
    """外径は正の有限値でなければならない。"""
    with pytest.raises(ParameterError):
        required_segment_count(bad, _printing())


# --- 材料の許可検査（要件 2.5, 2.7）------------------------------------


def test_check_material_accepts_an_allowed_material() -> None:
    """許可一覧の材料は通る。"""
    assert check_material(_printing(material="PETG")) is None


def test_check_material_rejects_a_material_outside_the_allowlist() -> None:
    """構築時検証を迂回した造形制約でも、許可外の材料を拒否する。

    ⚠️ `PrintingConstraints.__post_init__` は `__init__` 経由の構築しか通らない。
    pickle / `object.__setattr__` はこれを迂回するため、生成物を書き出す直前の
    `check_material` が二重の関門として要る（要件 2.7）。
    """
    printing = _printing()
    object.__setattr__(printing, "material", "ABS")
    with pytest.raises(ParameterError) as excinfo:
        check_material(printing)
    message = str(excinfo.value)
    assert "ABS" in message
    assert "material" in message
    for allowed in ALLOWED_MATERIALS:
        assert allowed in message


# --- 継手の当たり面（要件 2.6, 8.4）------------------------------------


def test_check_joint_accepts_area_at_or_above_the_minimum() -> None:
    """下限ちょうど・下限超の支圧面積は通る。"""
    joint = _joint(min_bearing_area_mm2=60.0)
    assert check_joint(joint, 60.0) is None
    assert check_joint(joint, 61.0) is None


def test_check_joint_rejects_area_below_the_minimum() -> None:
    """下限未満の支圧面積を、双方の値を示して拒否する。"""
    joint = _joint(min_bearing_area_mm2=60.0)
    with pytest.raises(ParameterError) as excinfo:
        check_joint(joint, 45.0)
    message = str(excinfo.value)
    assert "45.0" in message
    assert "60.0" in message
    assert "min_bearing_area_mm2" in message


@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf")])
def test_check_joint_rejects_invalid_area(bad: float) -> None:
    """支圧面積は正の有限値でなければならない。"""
    with pytest.raises(ParameterError):
        check_joint(_joint(), bad)


# --- 検査の対象範囲（要件 2.8）-----------------------------------------


def test_module_documents_that_milling_dependent_shapes_are_out_of_scope() -> None:
    """切削加工を要する形状を扱わない方針が、検査の対象範囲として明示される。

    要件 2.8 は「切削加工を前提とする形状を設計に含めない」という**設計上の方針**
    であり、実行時の検査で表せる性質ではない。したがって本モジュールの対象範囲の
    記述として明示されていることを固定する（tasks.md タスク 2.1）。
    """
    doc = constraints_module.__doc__
    assert doc is not None
    assert "切削" in doc
    assert "2.8" in doc
