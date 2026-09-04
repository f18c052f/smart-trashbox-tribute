"""受け口の幾何導出（タスク 3.1、要件 8.1, 8.2, 8.5, 8.6）。

本ファイルが固定するのは design.md `#### Shapes` の Service Interface /
Postconditions と、tasks.md タスク 3.1 の「観測可能な完了状態」である。

1. **取り付け部の内径が採寸値から導出される**こと。
   `top_outer_diameter_mm + 2 × fit_clearance_mm` であり、採寸値を変えると追随する
   （要件 8.5, 8.6 / design.md「Shapes」Responsibilities）
2. **外径が「取り付け部＋フランジ幅」から導出される**こと。採寸値とフランジ幅の
   双方に追随し、造形制約の導出（`required_segment_count`）へ渡る
3. **通過できる最小径が常に開口内径以上である**こと（要件 8.2 /
   design.md「Shapes」Postconditions
   `clear_opening_diameter_mm >= trash_can.opening_inner_diameter_mm`）。
   ⚠️ これは**検査**であって願望ではない。開口を狭める寸法の組み合わせは
   `GeometryError` で拒否される（要件 8.2「狭めないことを検査する」)
4. **分割数は導出値であり、`constraints` へ委譲される**こと
   （tasks.md「Implementation Notes」タスク 2.1(b)「⚠️ **形状層（タスク 3.2）は
   同じ幾何を再実装せず、この関数を使うこと。**」）
5. **形状を構築せずに評価できる**こと。本ファイルは形状ライブラリを一切
   import せず、CAD 非導入の環境でも全件が通る（design.md「Shapes」
   Implementation Notes の Validation「`rim_geometry` は形状構築の前に評価でき、
   不変条件の検査を軽量に行える」）

⚠️ **通過できる最小径は「取り付け部の内径」ではない。** 取り付け部の内径は
ゴミ箱の縁の**外側**に嵌まる穴であり、開口内径より広い。落ちてくる物は
「受け口の穴」と「ゴミ箱自身の開口」の**両方**を通るため、通過できる最小径は
2つのうち**狭い方**である。取り付け部の内径をそのまま返す実装は出荷値
（内径 227.0 / 開口 220.0）でも Postconditions を通ってしまうが、φ225 の物が
通ると主張することになる。`test_clear_opening_is_the_narrowest_aperture_not_the_fit_bore`
がこの取り違えを落とす。

⚠️ **出荷 `configs/catch_mechanism/dimensions.json` を読まない。** 採寸値は
タスク 5.2 で実測へ更新される予定であり（tasks.md「Implementation Notes」の
購入報告）、出荷ファイルの値に依存したテストは境界の外側から落ちる
（同ノートのタスク 2.3 (b) と同じ理由）。本ファイルは局所のヘルパで
パラメータを組み立て、**関係**だけを固定する。

ファイル名について: `tests/` 配下に `__init__.py` が無く pytest の import-mode も
既定（prepend）のため、テストモジュール名はセッション全体でフラットである。
既存の `test_catch_*.py` に倣う（tasks.md「Implementation Notes」タスク 1.1）。
"""

from __future__ import annotations

import math
from dataclasses import FrozenInstanceError, fields

import pytest

from catch_mechanism import shapes as shapes_module
from catch_mechanism.constraints import (
    MAX_SEGMENT_COUNT,
    check_envelope,
    required_segment_count,
    sector_envelope,
)
from catch_mechanism.errors import CatchMechanismError, GeometryError
from catch_mechanism.params import (
    JointPolicy,
    MechanismParams,
    ObjectSpec,
    PrintingConstraints,
    Provenance,
    RetentionParams,
    RimParams,
    TrashCanMeasurements,
)
from catch_mechanism.shapes import RimGeometry, rim_geometry

# ---------------------------------------------------------------------------
# パラメータ組み立ての補助（`test_catch_params.py` と同形）。
# ⚠️ 出荷 `dimensions.json` を読まず、局所の値から組み立てる。
# ---------------------------------------------------------------------------


def _trash_can(**overrides: object) -> TrashCanMeasurements:
    values: dict[str, object] = {
        "model_id": "yamada-kagaku-no335",
        "opening_inner_diameter_mm": 220.0,
        "top_outer_diameter_mm": 225.0,
        "bottom_outer_diameter_mm": 158.0,
        "bottom_flat_diameter_mm": 140.0,
        "height_mm": 244.0,
        "mass_g": 228.0,
        "bottom_thickness_mm": 1.5,
        "taper_deg": 7.0,
    }
    values.update(overrides)
    return TrashCanMeasurements(**values)  # type: ignore[arg-type]


def _target_object(**overrides: object) -> ObjectSpec:
    values: dict[str, object] = {"diameter_mm": 65.0, "height_mm": 122.0}
    values.update(overrides)
    return ObjectSpec(**values)  # type: ignore[arg-type]


def _printing(**overrides: object) -> PrintingConstraints:
    values: dict[str, object] = {
        "build_x_mm": 180.0,
        "build_y_mm": 180.0,
        "build_z_mm": 180.0,
        "material": "PETG",
        "material_density_g_cm3": 1.27,
        "segment_margin_mm": 5.0,
    }
    values.update(overrides)
    return PrintingConstraints(**values)  # type: ignore[arg-type]


def _joint(**overrides: object) -> JointPolicy:
    values: dict[str, object] = {
        "bolt_designation": "M3",
        "through_hole_diameter_mm": 3.4,
        "insert_outer_diameter_mm": 4.6,
        "insert_length_mm": 5.7,
        "dowel_diameter_mm": 3.0,
        "min_bearing_area_mm2": 60.0,
    }
    values.update(overrides)
    return JointPolicy(**values)  # type: ignore[arg-type]


def _rim(**overrides: object) -> RimParams:
    values: dict[str, object] = {
        "fit_clearance_mm": 1.0,
        "flange_width_mm": 30.0,
        "flange_slope_deg": 15.0,
        "wall_thickness_mm": 4.0,
        "height_mm": 18.0,
    }
    values.update(overrides)
    return RimParams(**values)  # type: ignore[arg-type]


def _retention(**overrides: object) -> RetentionParams:
    values: dict[str, object] = {
        "retrofit_fastener_count": 6,
        "liner_flat_min_diameter_mm": 140.0,
    }
    values.update(overrides)
    return RetentionParams(**values)  # type: ignore[arg-type]


def _params(**overrides: object) -> MechanismParams:
    values: dict[str, object] = {
        "trash_can": _trash_can(),
        "target_object": _target_object(),
        "printing": _printing(),
        "joint": _joint(),
        "rim": _rim(),
        "retention": _retention(),
        "provenance": {"trash_can.opening_inner_diameter_mm": Provenance.ASSUMED},
    }
    values.update(overrides)
    return MechanismParams(**values)  # type: ignore[arg-type]


def _params_for(
    *,
    opening_inner_diameter_mm: float,
    top_outer_diameter_mm: float,
    fit_clearance_mm: float = 1.0,
    flange_width_mm: float = 30.0,
    wall_thickness_mm: float = 4.0,
    **printing_overrides: object,
) -> MechanismParams:
    """開口内径・上端外径・受け口寸法だけを動かしたパラメータを作る。

    底径は `bottom_flat <= bottom_outer <= opening_inner`（`params` の不変条件）を
    満たすよう開口内径から従属させる。⚠️ 本ファイルの関心は受け口の幾何であり、
    底径そのものではない。
    """
    return _params(
        trash_can=_trash_can(
            opening_inner_diameter_mm=opening_inner_diameter_mm,
            top_outer_diameter_mm=top_outer_diameter_mm,
            bottom_outer_diameter_mm=opening_inner_diameter_mm * 0.7,
            bottom_flat_diameter_mm=opening_inner_diameter_mm * 0.6,
        ),
        rim=_rim(
            fit_clearance_mm=fit_clearance_mm,
            flange_width_mm=flange_width_mm,
            wall_thickness_mm=wall_thickness_mm,
        ),
        printing=_printing(**printing_overrides),
    )


#: 受け口が成立する採寸値の組み合わせ（上端外径 >= 開口内径）。
#: ⚠️ 出荷値だけで確かめると Postconditions が偶然通ってしまうため、開口内径・
#: 上端外径・隙間・フランジ幅をそれぞれ広い範囲で振る。
_VALID_COMBINATIONS: tuple[tuple[float, float, float, float], ...] = (
    # (開口内径, 上端外径, 隙間, フランジ幅)
    (220.0, 225.0, 1.0, 30.0),  # 出荷相当（第一候補 No.335）
    (210.0, 214.0, 1.0, 30.0),  # 実物として購入した個体の開口径帯
    (220.0, 220.0, 0.5, 30.0),  # 上端外径と開口内径が一致する極端な形
    (180.0, 190.0, 2.0, 10.0),  # 小径・細いフランジ
    (150.0, 152.0, 0.2, 5.0),  # 分割不要になる小径
    (250.0, 260.0, 3.0, 20.0),  # 大径
)


# ---------------------------------------------------------------------------
# 型そのもの（design.md「Shapes」Service Interface）
# ---------------------------------------------------------------------------


def test_rim_geometry_is_a_frozen_value_with_the_designed_fields() -> None:
    """`RimGeometry` は design.md が宣言する4項目を持つ不変値である。"""
    assert [field.name for field in fields(RimGeometry)] == [
        "segment_count",
        "inner_diameter_mm",
        "clear_opening_diameter_mm",
        "outer_diameter_mm",
    ]
    geometry = rim_geometry(_params())
    with pytest.raises(FrozenInstanceError):
        geometry.inner_diameter_mm = 1.0  # type: ignore[misc]
    assert isinstance(geometry.segment_count, int)


def test_rim_geometry_is_determined_by_the_parameters_alone() -> None:
    """同じパラメータからは同じ幾何が返る（値等価）。"""
    assert rim_geometry(_params()) == rim_geometry(_params())


# ---------------------------------------------------------------------------
# 要件 8.5 / 8.6: 取り付け部の内径は採寸値と隙間から導出され、採寸値に追随する
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("opening_inner_mm", "top_outer_mm", "fit_clearance_mm", "flange_width_mm"),
    _VALID_COMBINATIONS,
)
def test_inner_diameter_is_the_measured_top_plus_twice_the_clearance(
    opening_inner_mm: float,
    top_outer_mm: float,
    fit_clearance_mm: float,
    flange_width_mm: float,
) -> None:
    """取り付け部の内径 = 上端外径 + 2 × 隙間（要件 8.5, 8.6）。

    ⚠️ 隙間は**半径方向**の量であるため直径には2倍で効く。片側分しか足さない
    実装は、個体差を吸収する量を半分にしてしまう。
    """
    geometry = rim_geometry(
        _params_for(
            opening_inner_diameter_mm=opening_inner_mm,
            top_outer_diameter_mm=top_outer_mm,
            fit_clearance_mm=fit_clearance_mm,
            flange_width_mm=flange_width_mm,
        )
    )
    assert geometry.inner_diameter_mm == pytest.approx(
        top_outer_mm + 2.0 * fit_clearance_mm
    )


def test_inner_diameter_follows_a_changed_measurement() -> None:
    """採寸値を変えると取り付け部の内径が同じ量だけ追随する（要件 8.5）。"""
    base = rim_geometry(
        _params_for(opening_inner_diameter_mm=220.0, top_outer_diameter_mm=225.0)
    )
    remeasured = rim_geometry(
        _params_for(opening_inner_diameter_mm=210.0, top_outer_diameter_mm=214.0)
    )
    assert remeasured.inner_diameter_mm - base.inner_diameter_mm == pytest.approx(
        214.0 - 225.0
    )


def test_inner_diameter_follows_a_changed_clearance() -> None:
    """隙間を変えると取り付け部の内径が2倍で追随する（要件 8.6）。"""
    tight = rim_geometry(
        _params_for(
            opening_inner_diameter_mm=220.0,
            top_outer_diameter_mm=225.0,
            fit_clearance_mm=0.5,
        )
    )
    loose = rim_geometry(
        _params_for(
            opening_inner_diameter_mm=220.0,
            top_outer_diameter_mm=225.0,
            fit_clearance_mm=2.5,
        )
    )
    assert loose.inner_diameter_mm - tight.inner_diameter_mm == pytest.approx(
        2.0 * (2.5 - 0.5)
    )


# ---------------------------------------------------------------------------
# 要件 8.1: 外径は「取り付け部＋フランジ幅」から導出される
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("opening_inner_mm", "top_outer_mm", "fit_clearance_mm", "flange_width_mm"),
    _VALID_COMBINATIONS,
)
def test_outer_diameter_is_the_mount_plus_the_flange_on_both_sides(
    opening_inner_mm: float,
    top_outer_mm: float,
    fit_clearance_mm: float,
    flange_width_mm: float,
) -> None:
    """外径 = 取り付け部の内径 + 2 × フランジ幅（tasks.md タスク 3.1）。

    フランジは**外向きにのみ**張り出すため（design.md「受け口形状の決定」決定1）、
    片側の幅が直径には2倍で効く。tasks.md「Implementation Notes」タスク 2.1(a) が
    記録する実部品のリム外径 287.0mm = 225 + 2×1.0 + 2×30 と一致する。
    """
    geometry = rim_geometry(
        _params_for(
            opening_inner_diameter_mm=opening_inner_mm,
            top_outer_diameter_mm=top_outer_mm,
            fit_clearance_mm=fit_clearance_mm,
            flange_width_mm=flange_width_mm,
        )
    )
    assert geometry.outer_diameter_mm == pytest.approx(
        geometry.inner_diameter_mm + 2.0 * flange_width_mm
    )
    assert geometry.outer_diameter_mm > geometry.inner_diameter_mm


def test_outer_diameter_follows_both_the_measurement_and_the_flange_width() -> None:
    """採寸値とフランジ幅のどちらを変えても外径が追随する（要件 8.1, 8.5）。"""
    base = rim_geometry(
        _params_for(opening_inner_diameter_mm=220.0, top_outer_diameter_mm=225.0)
    )
    remeasured = rim_geometry(
        _params_for(opening_inner_diameter_mm=210.0, top_outer_diameter_mm=214.0)
    )
    widened = rim_geometry(
        _params_for(
            opening_inner_diameter_mm=220.0,
            top_outer_diameter_mm=225.0,
            flange_width_mm=40.0,
        )
    )
    assert remeasured.outer_diameter_mm == pytest.approx(base.outer_diameter_mm - 11.0)
    assert widened.outer_diameter_mm == pytest.approx(base.outer_diameter_mm + 20.0)


def test_outer_diameter_does_not_include_the_wall_thickness() -> None:
    """壁の肉厚は外径に算入しない。

    フランジの内周は取り付け部の内径そのものであり（design.md「Shapes」
    Responsibilities の「フランジの内周は開口内径以上」がこの内径に対する条件で
    ある）、フランジ幅はそこから外向きに測る。肉厚を足す実装は、同じフランジ幅の
    指定に対して外径を黙って広げ、分割数の導出を狂わせる。
    """
    thin = rim_geometry(
        _params_for(
            opening_inner_diameter_mm=220.0,
            top_outer_diameter_mm=225.0,
            wall_thickness_mm=2.0,
        )
    )
    thick = rim_geometry(
        _params_for(
            opening_inner_diameter_mm=220.0,
            top_outer_diameter_mm=225.0,
            wall_thickness_mm=8.0,
        )
    )
    assert thin.outer_diameter_mm == pytest.approx(thick.outer_diameter_mm)
    assert thin.inner_diameter_mm == pytest.approx(thick.inner_diameter_mm)


# ---------------------------------------------------------------------------
# 要件 8.2: 通過できる最小径は常に開口内径以上（design.md Postconditions）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("opening_inner_mm", "top_outer_mm", "fit_clearance_mm", "flange_width_mm"),
    _VALID_COMBINATIONS,
)
def test_clear_opening_is_never_narrower_than_the_bin_opening(
    opening_inner_mm: float,
    top_outer_mm: float,
    fit_clearance_mm: float,
    flange_width_mm: float,
) -> None:
    """`clear_opening_diameter_mm >= opening_inner_diameter_mm`（要件 8.2）。

    design.md「受け口形状の決定」決定1「フランジは外向きにのみ張り出し、開口内径を
    一切狭めない」を数値として固定する。
    """
    params = _params_for(
        opening_inner_diameter_mm=opening_inner_mm,
        top_outer_diameter_mm=top_outer_mm,
        fit_clearance_mm=fit_clearance_mm,
        flange_width_mm=flange_width_mm,
    )
    geometry = rim_geometry(params)
    assert (
        geometry.clear_opening_diameter_mm
        >= params.trash_can.opening_inner_diameter_mm
    )


def test_clear_opening_is_the_narrowest_aperture_not_the_fit_bore() -> None:
    """通過できる最小径は取り付け部の内径ではなく、2つの穴の狭い方である。

    ⚠️ 落ちてくる物は受け口の穴とゴミ箱自身の開口の**両方**を通る。取り付け部の
    内径（開口内径より広い）を返す実装は、実際には通らない径を「通る」と主張する。
    """
    params = _params_for(
        opening_inner_diameter_mm=220.0, top_outer_diameter_mm=225.0
    )
    geometry = rim_geometry(params)
    assert geometry.inner_diameter_mm == pytest.approx(227.0)
    assert geometry.clear_opening_diameter_mm == pytest.approx(220.0)
    assert geometry.clear_opening_diameter_mm < geometry.inner_diameter_mm


def test_clear_opening_follows_a_changed_opening_measurement() -> None:
    """開口内径の採寸値を変えると通過できる最小径が追随する（要件 8.5）。"""
    narrow = rim_geometry(
        _params_for(opening_inner_diameter_mm=210.0, top_outer_diameter_mm=225.0)
    )
    wide = rim_geometry(
        _params_for(opening_inner_diameter_mm=220.0, top_outer_diameter_mm=225.0)
    )
    assert narrow.clear_opening_diameter_mm == pytest.approx(210.0)
    assert wide.clear_opening_diameter_mm == pytest.approx(220.0)


def test_a_fit_bore_equal_to_the_opening_is_accepted() -> None:
    """取り付け部の内径がちょうど開口内径に等しい場合は成立する（等号を許す）。"""
    geometry = rim_geometry(
        _params_for(
            opening_inner_diameter_mm=220.0,
            top_outer_diameter_mm=218.0,
            fit_clearance_mm=1.0,
        )
    )
    assert geometry.inner_diameter_mm == pytest.approx(220.0)
    assert geometry.clear_opening_diameter_mm == pytest.approx(220.0)


def test_a_rim_that_would_narrow_the_opening_is_rejected() -> None:
    """開口を狭める寸法の組み合わせは `GeometryError` で拒否される（要件 8.2）。

    `params` は `bottom_flat <= bottom_outer <= opening_inner` しか強制せず、
    上端外径と開口内径の関係を縛らない。したがって「上端外径が開口内径より
    小さいゴミ箱」を表す `MechanismParams` は**構築できてしまう**。その場合
    取り付け部の内径は開口内径を下回り、受け口が開口を狭める——
    ⚠️ **これを黙って通してはならない。** 要件 8.2 は「狭めないことを検査する」と
    述べており、Postconditions を満たせない以上、値を返す道は無い。
    """
    params = _params_for(
        opening_inner_diameter_mm=220.0,
        top_outer_diameter_mm=200.0,
        fit_clearance_mm=1.0,
    )
    with pytest.raises(GeometryError) as excinfo:
        rim_geometry(params)
    message = str(excinfo.value)
    assert "202" in message  # 取り付け部の内径
    assert "220" in message  # 開口内径
    # `cli` の終了コード分岐と、本パッケージを知らない呼び出し側の防御の双方で
    # 捕捉できる（`errors.py` の階層）。
    assert isinstance(excinfo.value, CatchMechanismError)
    assert isinstance(excinfo.value, ValueError)


# ---------------------------------------------------------------------------
# 要件 8.1 / 8.3: 外径は造形制約の導出へ渡る（分割数は再実装しない）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("opening_inner_mm", "top_outer_mm", "fit_clearance_mm", "flange_width_mm"),
    _VALID_COMBINATIONS,
)
def test_segment_count_matches_the_constraints_derivation(
    opening_inner_mm: float,
    top_outer_mm: float,
    fit_clearance_mm: float,
    flange_width_mm: float,
) -> None:
    """分割数は外径と造形制約から `constraints` が導く値と一致する（要件 8.3）。"""
    params = _params_for(
        opening_inner_diameter_mm=opening_inner_mm,
        top_outer_diameter_mm=top_outer_mm,
        fit_clearance_mm=fit_clearance_mm,
        flange_width_mm=flange_width_mm,
    )
    geometry = rim_geometry(params)
    assert geometry.segment_count == required_segment_count(
        geometry.outer_diameter_mm, params.printing
    )
    assert 1 <= geometry.segment_count <= MAX_SEGMENT_COUNT


def test_segment_count_is_delegated_to_the_constraints_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """分割数の導出は `constraints.required_segment_count` に委譲される。

    tasks.md「Implementation Notes」タスク 2.1(b):「⚠️ **形状層（タスク 3.2）は
    同じ幾何を再実装せず、この関数を使うこと。**」同じ式を書き写した実装は、
    半径方向の広がりを見落とした版へ静かに分岐しうる。差し替えた関数の戻り値が
    そのまま現れることで、委譲を機械的に固定する。
    """
    seen: list[tuple[float, PrintingConstraints]] = []

    def _fake(outer_diameter_mm: float, printing: PrintingConstraints) -> int:
        seen.append((outer_diameter_mm, printing))
        return 7

    monkeypatch.setattr(shapes_module, "required_segment_count", _fake)
    params = _params()
    geometry = rim_geometry(params)
    assert geometry.segment_count == 7
    # 「外径を造形制約の検査へ渡す」（tasks.md タスク 3.1）——渡るのは導出した外径
    # そのものであり、採寸値でも取り付け部の内径でもない。
    assert seen == [(geometry.outer_diameter_mm, params.printing)]


@pytest.mark.parametrize(
    ("opening_inner_mm", "top_outer_mm", "fit_clearance_mm", "flange_width_mm"),
    _VALID_COMBINATIONS,
)
def test_derived_segments_fit_the_build_volume(
    opening_inner_mm: float,
    top_outer_mm: float,
    fit_clearance_mm: float,
    flange_width_mm: float,
) -> None:
    """導出した外径と分割数の扇形は造形可能寸法に収まる（design.md Postconditions）。

    ⚠️ 形状を構築せずに検査できることそのものの実証でもある（design.md「Shapes」
    Implementation Notes の Validation）。ここで用いる `sector_envelope` /
    `check_envelope` は `constraints` の関数であり、形状ライブラリを要さない。
    """
    params = _params_for(
        opening_inner_diameter_mm=opening_inner_mm,
        top_outer_diameter_mm=top_outer_mm,
        fit_clearance_mm=fit_clearance_mm,
        flange_width_mm=flange_width_mm,
    )
    geometry = rim_geometry(params)
    envelope = sector_envelope(
        geometry.outer_diameter_mm, geometry.segment_count, params.rim.height_mm
    )
    assert check_envelope("rim_segment", envelope, params.printing) == ()


def test_segment_count_grows_with_the_measured_diameter() -> None:
    """採寸値が大きくなると分割数は減らない（外径に追随する導出値である）。"""
    counts = [
        rim_geometry(
            _params_for(
                opening_inner_diameter_mm=diameter_mm - 5.0,
                top_outer_diameter_mm=diameter_mm,
            )
        ).segment_count
        for diameter_mm in (120.0, 180.0, 240.0, 280.0)
    ]
    assert counts == sorted(counts)
    assert counts[0] < counts[-1]


def test_geometry_error_propagates_when_no_segment_count_fits() -> None:
    """どの分割数でも収まらない外径は `GeometryError` として伝播する。

    ⚠️ 例外を握り潰して大きな分割数や無検査の外径を返さない
    （`constraints.required_segment_count` の Invariants）。半径方向の広がりは
    分割数を増やしても縮まないため、外径が造形面の約2倍を超えた時点で分割では
    解決しない。
    """
    params = _params_for(
        opening_inner_diameter_mm=395.0,
        top_outer_diameter_mm=400.0,
        flange_width_mm=30.0,
    )
    # 外径 462.0mm、造形面の上限 175mm の 2 倍を超える。
    assert 400.0 + 2.0 * 1.0 + 2.0 * 30.0 > 2.0 * (180.0 - 5.0)
    with pytest.raises(GeometryError):
        rim_geometry(params)


def test_a_larger_build_volume_reduces_the_segment_count() -> None:
    """造形制約が変われば分割数が追随する（分割数はパラメータではない）。"""
    small = rim_geometry(
        _params_for(
            opening_inner_diameter_mm=220.0,
            top_outer_diameter_mm=225.0,
            build_x_mm=180.0,
            build_y_mm=180.0,
        )
    )
    large = rim_geometry(
        _params_for(
            opening_inner_diameter_mm=220.0,
            top_outer_diameter_mm=225.0,
            build_x_mm=300.0,
            build_y_mm=300.0,
            build_z_mm=300.0,
        )
    )
    assert large.segment_count < small.segment_count
    # 参考: 出荷相当の外径 287.0mm は 180mm 機で 5 分割になる
    # （tasks.md「Implementation Notes」タスク 2.1(a) の記録と一致する）。
    assert small.outer_diameter_mm == pytest.approx(287.0)
    assert small.segment_count == 5
    assert small.segment_count == math.ceil(
        math.pi / math.asin(min(1.0, 175.0 / 287.0))
    )
