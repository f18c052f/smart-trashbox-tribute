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

import ast
import math
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import pytest

from catch_mechanism import shapes as shapes_module
from catch_mechanism.constraints import (
    MAX_SEGMENT_COUNT,
    Envelope,
    check_envelope,
    check_joint,
    required_segment_count,
    sector_envelope,
)
from catch_mechanism.config import SCHEMA_VERSION
from catch_mechanism.errors import CatchMechanismError, GeometryError, ParameterError
from catch_mechanism.metrics import GeometryBaseline, PartMetrics, compare_metrics
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
from catch_mechanism.shapes import (
    PART_NAMES,
    BuiltPart,
    RimGeometry,
    RimSegment,
    build_parts,
    build_segments,
    measure_part,
    rim_geometry,
    segment_envelope,
    segment_height_mm,
    segment_part_names,
)

# ---------------------------------------------------------------------------
# 形状ライブラリの有無（要件 5.7）。
# ⚠️ **モジュール全体を `pytest.importorskip` で落とさない。** 本ファイルの
# タスク 3.1 側の検査は形状ライブラリを必要とせず、非導入環境でも完了しなければ
# ならない（要件 5.7「形状生成の環境を持たない実行環境でも、形状生成を除く
# すべての検査が完了できる」）。したがって**ソリッドを実際に構築する検査だけ**を
# 個別に skip する。
# ---------------------------------------------------------------------------

try:  # pragma: no cover - 環境によって分岐する
    import build123d as _build123d
except ImportError:  # pragma: no cover - `cad` extra 非導入の環境
    _build123d = None

requires_cad = pytest.mark.skipif(
    _build123d is None,
    reason="形状ライブラリ（build123d / `cad` extra）が未導入である。"
    "要件 5.7 により、形状生成を除く検査はこの環境でも完了する。",
)

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


# ---------------------------------------------------------------------------
# タスク 3.2: ワイドリムのセグメント形状（要件 2.6, 3.1, 8.3, 8.4, 9.7）
#
# 本節が固定するのは tasks.md タスク 3.2 の「観測可能な完了状態」と、
# design.md「受け口形状の決定」決定1 / 決定4 である。
#
# 1. **全セグメントが構築でき、セグメント数が導出値と一致する**こと
# 2. **外周が高く内周が低い**こと。⚠️ 向きを取り違えると内向きの漏斗になり、
#    決定1 に真っ向から反する。パラメータの符号ではなく**実際のソリッドの Z**を
#    内周・外周の2箇所で測って向きを固定する
# 3. **締結座の数が指定どおり**であること（決定4 / 要件 9.7）
# 4. **端面に貫通ボルト穴と金属インサート座があり、ダボは荷重を受けない**こと
#    （要件 2.6, 8.4）。参照解決は**幾何セレクタ**（位置・向きによる選択）で行い、
#    生成名（Face6 等）に依存しない
# 5. **実高さを伴う check_envelope を通る**こと。⚠️ tasks.md「Implementation
#    Notes」タスク 3.1(a)「Z 軸は 3.1 で検査されない。タスク 3.2 / 3.4 は実高さを
#    伴う check_envelope を必ず通すこと」の決着である
# ---------------------------------------------------------------------------


def _sector_half_angle_deg(segment_count: int) -> float:
    """セグメントの半開き角（度）。セグメントは +X 軸を中心に左右対称に置かれる。"""
    return 180.0 / segment_count


def _end_face_outward_normals(
    segment_count: int,
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """始端面・終端面の**外向き法線**を返す（幾何セレクタの鍵）。

    セグメントは局所角 `[-half, +half]` を占める。始端面（`-half`）の外向きは
    反時計回りの接線の逆、終端面（`+half`）の外向きは接線そのものである。
    """
    half = math.radians(_sector_half_angle_deg(segment_count))
    start = (-math.sin(half), -math.cos(half), 0.0)
    end = (-math.sin(half), math.cos(half), 0.0)
    return start, end


def _planar_faces_on_end_plane(
    solid: object, normal: tuple[float, float, float]
) -> list[object]:
    """`normal` を法線に持ち、かつ Z 軸を含む平面上にある平面を選ぶ。

    ⚠️ **生成名を使わない。** 選択条件は「法線の向き」と「面の中心が
    その平面上にある（`center · normal == 0`）」という幾何量だけである。
    """
    from build123d import GeomType

    selected: list[object] = []
    for face in solid.faces():  # type: ignore[attr-defined]
        if face.geom_type != GeomType.PLANE:
            continue
        unit = face.normal_at()
        if (
            abs(unit.X - normal[0]) > 1e-4
            or abs(unit.Y - normal[1]) > 1e-4
            or abs(unit.Z - normal[2]) > 1e-4
        ):
            continue
        center = face.center()
        if abs(center.X * normal[0] + center.Y * normal[1]) > 1e-6:
            continue
        selected.append(face)
    return selected


def _end_face_bores(
    solid: object, normal: tuple[float, float, float]
) -> list[tuple[float, float]]:
    """端面から材料側へ掘られた円筒穴を `(半径, 軸の高さ)` で集める。

    条件は「円筒面である」「軸が水平（Z 成分ゼロ）」「軸の向きが端面の外向き
    法線の**逆**（＝材料へ向かって掘られている）」「軸が端面の平面上にある」。
    ⚠️ 生成名を一切使わない、位置と向きだけの選択である。
    """
    from build123d import GeomType

    bores: list[tuple[float, float]] = []
    for face in solid.faces():  # type: ignore[attr-defined]
        if face.geom_type != GeomType.CYLINDER:
            continue
        axis = face.axis_of_rotation
        direction, position = axis.direction, axis.position
        if abs(direction.Z) > 1e-6:
            continue
        if abs(direction.X + normal[0]) > 1e-4 or abs(direction.Y + normal[1]) > 1e-4:
            continue
        if abs(position.X * normal[0] + position.Y * normal[1]) > 1e-6:
            continue
        bores.append((face.radius, position.Z))
    return bores


def _retrofit_bore_count(solid: object, bore_radius_mm: float) -> int:
    """締結座（Z 軸方向の止まり穴）の数を幾何セレクタで数える。

    条件は「円筒面である」「軸が Z 軸に平行」「半径が金属インサート外径の半分」。
    ⚠️ 取り付け部の内周面（半径 `inner_diameter/2`）も座のパッド外周面も同じ
    条件のうち軸の向きだけは満たすため、**半径**で切り分ける。

    ⚠️ パッドの外周面が継手座と融合して切り取られている場合、build123d は
    `Face.radius` に `None` を返す（円筒面ではあるが、周を一周しないため）。
    座の**止まり穴**の側は常に一周しており半径を返すので、`None` は読み飛ばす。
    """
    from build123d import GeomType

    count = 0
    for face in solid.faces():  # type: ignore[attr-defined]
        if face.geom_type != GeomType.CYLINDER:
            continue
        if abs(abs(face.axis_of_rotation.direction.Z) - 1.0) > 1e-9:
            continue
        radius = face.radius
        if radius is None or abs(radius - bore_radius_mm) > 1e-6:
            continue
        count += 1
    return count


def _top_surface_z_at_radius(
    solid: object, radius_mm: float, probe_mm: float = 1.0
) -> float:
    """半径 `radius_mm` の細い柱でソリッドを切り、その位置の上面の Z を返す。

    ⚠️ **パラメータの符号ではなく実形状を測る。** 傾斜の向きを取り違えた実装
    （内周が高い＝内向きの漏斗）は、この関数の内周／外周の値の大小で落ちる。
    """
    from build123d import Align, Box, Location

    column = Location((radius_mm, 0.0, 0.0)) * Box(
        probe_mm,
        probe_mm,
        1000.0,
        align=(Align.CENTER, Align.CENTER, Align.CENTER),
    )
    return (solid & column).bounding_box().max.Z  # type: ignore[operator]


def _built_envelope(solid: object) -> Envelope:
    """**実際に構築されたソリッド**の外接箱から `Envelope` を作る。"""
    size = solid.bounding_box().size  # type: ignore[attr-defined]
    return Envelope(x_mm=size.X, y_mm=size.Y, z_mm=size.Z)


# --- 形状ライブラリを必要としない検査 ---------------------------------------


def test_part_names_declares_the_single_rim_segment_kind() -> None:
    """`PART_NAMES` は design.md の Service Interface どおり1種類だけを挙げる。"""
    assert PART_NAMES == ("rim_segment",)


def test_segment_part_names_are_unique_and_match_the_derived_segment_count() -> None:
    """部品名は導出された分割数だけ在り、互いに重複しない。"""
    params = _params()
    names = segment_part_names(params)
    assert len(names) == rim_geometry(params).segment_count
    assert len(set(names)) == len(names)
    assert all(name.startswith(PART_NAMES[0]) for name in names)


def test_segment_height_includes_the_flange_rise_and_the_wall() -> None:
    """実高さは「取り付け部の高さ ＋ 肉厚 ＋ フランジの立ち上がり」である。

    ⚠️ `RimParams.height_mm` は**取り付け部の高さ**であり（`params.py` の
    docstring）、部品全体の高さではない。外向きに傾いたフランジは
    `flange_width × tan(flange_slope)` だけ高さを足す。
    """
    params = _params()
    rise = 30.0 * math.tan(math.radians(15.0))
    assert segment_height_mm(params) == pytest.approx(18.0 + 4.0 + rise)


def test_segment_height_grows_with_the_flange_slope() -> None:
    """傾斜を強めると実高さが増える（立ち上がりが高さに効いている証拠）。"""
    gentle = segment_height_mm(_params(rim=_rim(flange_slope_deg=5.0)))
    steep = segment_height_mm(_params(rim=_rim(flange_slope_deg=25.0)))
    assert steep > gentle


def test_segment_envelope_is_the_sector_envelope_with_the_real_height() -> None:
    """外接箱は `constraints.sector_envelope` に**実高さ**を渡して得る。

    tasks.md「Implementation Notes」タスク 2.1(b)「⚠️ 形状層（タスク 3.2）は
    同じ幾何を再実装せず、この関数を使うこと。」の固定である。
    """
    params = _params()
    geometry = rim_geometry(params)
    expected = sector_envelope(
        geometry.outer_diameter_mm,
        geometry.segment_count,
        segment_height_mm(params),
    )
    assert segment_envelope(params) == expected


def test_segment_envelope_passes_check_envelope_for_shipping_values() -> None:
    """出荷相当の値では、実高さを伴う `check_envelope` に違反が出ない。"""
    params = _params()
    assert check_envelope(PART_NAMES[0], segment_envelope(params), params.printing) == ()


def test_segment_envelope_detects_the_z_axis_violation_missed_by_task_3_1() -> None:
    """⚠️ タスク 3.1(a) の申し送りの決着: 実高さで Z 軸の超過が検出される。

    `required_segment_count` は高さに `build_z_mm` を置くため、実高さの違反を
    検出できない。分割数の導出は通るのに、実高さでは Z を超える——この状態が
    ここで初めて観測される。
    """
    params = _params(rim=_rim(height_mm=200.0))
    assert rim_geometry(params).segment_count == 5  # 分割数の導出は通ってしまう
    violations = check_envelope(
        PART_NAMES[0], segment_envelope(params), params.printing
    )
    assert [violation.axis for violation in violations] == ["z"]
    assert violations[0].excess_mm == pytest.approx(
        200.0 + 4.0 + 30.0 * math.tan(math.radians(15.0)) - 180.0
    )


def test_build_segments_refuses_a_rim_that_exceeds_the_build_volume_in_z() -> None:
    """実高さが造形可能寸法を超えるなら、構築そのものを拒否する。

    ⚠️ 形状ライブラリの有無に関わらず拒否される（検査はソリッド構築の**前**に
    行う）。要件 5.7 の環境でもこの検査は成立しなければならない。
    """
    params = _params(rim=_rim(height_mm=200.0))
    with pytest.raises(GeometryError) as excinfo:
        build_segments(params)
    message = str(excinfo.value)
    assert "z" in message
    assert "180" in message


def test_shapes_does_not_import_the_shape_library_at_module_level() -> None:
    """⚠️ `shapes` は build123d を**モジュール直下で import しない**（要件 5.7）。

    design.md「Allowed Dependencies」は `shapes` に build123d の import を許すが、
    許されていることと**モジュール読み込み時に必要にしてよい**ことは別である。
    モジュール直下へ置くと `rim_geometry` すら CAD 非導入環境で評価できなくなり、
    design.md「Shapes」Implementation Notes の Validation
    「`rim_geometry` は形状構築の前に評価でき、不変条件の検査を軽量に行える」が
    壊れる。
    """
    source = Path(shapes_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    module_level: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            module_level.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            module_level.append(node.module)
    assert not [
        name for name in module_level if name.split(".")[0] in {"build123d", "OCP"}
    ]


# --- 形状ライブラリを要する検査（要件 5.7 により個別に skip される）-----------


@requires_cad
def test_build_segments_returns_one_solid_per_derived_segment() -> None:
    """全セグメントが構築でき、その数は導出された分割数と一致する。"""
    params = _params()
    segments = build_segments(params)
    assert len(segments) == rim_geometry(params).segment_count == 5
    assert all(isinstance(segment, RimSegment) for segment in segments)
    assert [segment.name for segment in segments] == list(segment_part_names(params))
    assert [segment.index for segment in segments] == [0, 1, 2, 3, 4]
    for segment in segments:
        assert len(segment.solid.solids()) == 1
        assert segment.solid.volume > 0.0


@requires_cad
def test_every_built_segment_fits_the_build_volume_by_its_actual_bounding_box() -> None:
    """⚠️ **解析値ではなく実際に構築したソリッドの外接箱**で造形可能寸法を検査する。

    tasks.md タスク 2.1(c) のとおり `check_envelope` は `segment_margin_mm` を
    引かないため、判定基準は 180×180×180 そのものである。
    """
    params = _params()
    for segment in build_segments(params):
        built = _built_envelope(segment.solid)
        assert check_envelope(segment.name, built, params.printing) == ()
        assert built.x_mm <= 180.0
        assert built.y_mm <= 180.0
        assert built.z_mm <= 180.0


@requires_cad
def test_built_bounding_box_is_within_the_analytic_segment_envelope() -> None:
    """解析的な外接箱は実形状の**上界**である（保守側に倒れている）。

    ⚠️ 許容差 1e-3mm は OCCT の外接箱が持つ数値誤差の吸収であり、判定の緩和では
    ない。半径方向は `sector_envelope` が中心を含む扇形の最悪値 `D/2` を採るため
    実形状より大幅に大きく（実測で約3倍）、接線方向は弦長と一致する。
    """
    params = _params()
    analytic = segment_envelope(params)
    for segment in build_segments(params):
        built = _built_envelope(segment.solid)
        assert built.x_mm <= analytic.x_mm + 1e-3
        assert built.y_mm <= analytic.y_mm + 1e-3
        assert built.z_mm == pytest.approx(analytic.z_mm)
        # 半径方向は円環断片であるため、最悪値 `D/2` よりはるかに小さい。
        assert built.x_mm < analytic.x_mm / 2.0


@requires_cad
def test_flange_rises_outward_and_never_forms_an_inward_funnel() -> None:
    """⚠️ 決定1: 外周が高く内周が低い。**実形状の Z を2箇所で測って**固定する。

    傾斜の符号を取り違えた実装は内向きの漏斗になり、FR-12 に不利な絞りを
    開口の内側へ作る。パラメータの符号を見るテストではこの取り違えを落とせない。
    """
    params = _params()
    geometry = rim_geometry(params)
    inner_r = geometry.inner_diameter_mm / 2.0
    outer_r = geometry.outer_diameter_mm / 2.0
    segment = build_segments(params)[0]
    inner_top = _top_surface_z_at_radius(segment.solid, inner_r + 0.5)
    outer_top = _top_surface_z_at_radius(segment.solid, outer_r - 0.5)
    assert outer_top > inner_top
    rise = 30.0 * math.tan(math.radians(15.0))
    assert outer_top - inner_top == pytest.approx(rise, abs=0.6)


@requires_cad
def test_built_solid_never_reaches_inside_the_fit_bore() -> None:
    """⚠️ 決定1: 受け口は開口内径を一切狭めない。

    ソリッドの全頂点が取り付け部の内径以上の半径にあることを確かめる。内向きの
    リップ・漏斗・締結座のいずれかが内側へせり出せば落ちる。
    """
    params = _params()
    inner_r = rim_geometry(params).inner_diameter_mm / 2.0
    for segment in build_segments(params):
        radii = [math.hypot(vertex.X, vertex.Y) for vertex in segment.solid.vertices()]
        assert min(radii) == pytest.approx(inner_r)


@requires_cad
def test_end_faces_carry_a_through_bolt_hole_and_a_metal_insert_seat() -> None:
    """要件 8.4 / 2.6: 端面の締結は貫通ボルト穴と金属インサート座で行う。

    片方の端面に貫通ボルト穴（バカ穴）、もう片方に金属インサート座を置く。
    隣り合うセグメントを突き合わせると穴と座が同軸で向かい合う。
    参照解決は**幾何セレクタ**（法線・軸の向き・平面上にあること）で行う。
    """
    params = _params()
    joint = params.joint
    start_normal, end_normal = _end_face_outward_normals(
        rim_geometry(params).segment_count
    )
    segment = build_segments(params)[0]

    start_radii = [radius for radius, _ in _end_face_bores(segment.solid, start_normal)]
    end_radii = [radius for radius, _ in _end_face_bores(segment.solid, end_normal)]

    assert any(
        radius == pytest.approx(joint.through_hole_diameter_mm / 2.0)
        for radius in start_radii
    )
    assert not any(
        radius == pytest.approx(joint.insert_outer_diameter_mm / 2.0)
        for radius in start_radii
    )
    assert any(
        radius == pytest.approx(joint.insert_outer_diameter_mm / 2.0)
        for radius in end_radii
    )


@requires_cad
def test_locating_dowel_is_bored_with_clearance_on_both_end_faces() -> None:
    """要件 2.6 / 8.4: ダボは位置決めのみで、荷重を受けない。

    ダボ穴は**呼び径より大きい**（すきま嵌め）。締まり嵌めにすると剪断荷重が
    ダボへ回り、「荷重を受けるのは貫通ボルトと金属インサートだけ」という
    `JointPolicy` の区別が形状の側で破れる。

    ⚠️ ダボの識別は径ではなく**軸の高さ**で行う。ボルト穴・インサート座・ボルト
    先端の逃げは同一の軸上に並び、ダボはそれとは別の高さに置かれる——これが
    「荷重を受ける要素とは別に置く」ことの幾何としての現れである。
    """
    params = _params()
    joint = params.joint
    start_normal, end_normal = _end_face_outward_normals(
        rim_geometry(params).segment_count
    )
    segment = build_segments(params)[0]
    nominal = joint.dowel_diameter_mm / 2.0

    bolt_axes = [
        height
        for radius, height in _end_face_bores(segment.solid, start_normal)
        if radius == pytest.approx(joint.through_hole_diameter_mm / 2.0)
    ]
    assert len(bolt_axes) == 1
    bolt_axis_height = bolt_axes[0]

    for normal in (start_normal, end_normal):
        bores = _end_face_bores(segment.solid, normal)
        dowels = [
            radius
            for radius, height in bores
            if abs(height - bolt_axis_height) > 1e-6
        ]
        assert len(dowels) == 1
        assert nominal < dowels[0] < nominal + 0.5


@requires_cad
def test_joint_bearing_area_is_reported_and_passes_check_joint() -> None:
    """要件 2.6 / 8.4: 支圧面積が下限を満たすことを検査したうえで構築する。"""
    params = _params()
    for segment in build_segments(params):
        assert segment.joint_bearing_area_mm2 is not None
        assert segment.joint_bearing_area_mm2 >= params.joint.min_bearing_area_mm2
        check_joint(params.joint, segment.joint_bearing_area_mm2)


def test_build_segments_refuses_a_joint_that_cannot_reach_the_bearing_area() -> None:
    """支圧面積の下限を満たせない継手方針は拒否される（要件 2.6, 8.4）。

    ⚠️ 拒否はソリッド構築の**前**に起きるため、形状ライブラリを必要としない。
    メッセージには実際の支圧面積と下限の双方が載る（`check_joint` の規約）。
    """
    params = _params(joint=_joint(min_bearing_area_mm2=100_000.0))
    with pytest.raises(ParameterError) as excinfo:
        build_segments(params)
    message = str(excinfo.value)
    assert "bearing_area_mm2=" in message
    assert "min_bearing_area_mm2=100000.0" in message


@requires_cad
def test_joint_faces_are_not_normal_to_the_layer_direction() -> None:
    """⚠️ 造形向き（リム面を寝かせる）の前提を形状の側で固定する。

    リム面を造形面に寝かせると層法線は +Z である。接合面（端面）の法線は
    XY 平面内にあり、層法線と一致しない——ボルトの締結力が層間剥離ではなく
    層内のせん断・支圧で受けられる配置である。
    """
    params = _params()
    normals = _end_face_outward_normals(rim_geometry(params).segment_count)
    segment = build_segments(params)[0]
    for normal in normals:
        faces = _planar_faces_on_end_plane(segment.solid, normal)
        assert faces, "端面が幾何セレクタで選べない"
        for face in faces:
            assert abs(face.normal_at().Z) < 1e-9


@requires_cad
def test_retrofit_seat_total_matches_the_retention_parameter() -> None:
    """決定4 / 要件 9.7: 後付け部品用の締結座を、指定された数だけ設ける。

    ⚠️ 締結座は**受け口全体で**指定数である（design.md「受け口形状の決定」
    決定4「後付け部品用の締結座を6箇所」）。セグメントごとに指定数を設けると
    リング全体では指定数 × 分割数になり、決定の記録から外れる。
    """
    params = _params()
    segments = build_segments(params)
    assert sum(segment.retrofit_seat_count for segment in segments) == 6
    assert params.retention.retrofit_fastener_count == 6


@requires_cad
def test_retrofit_seat_count_is_observable_in_the_built_solid() -> None:
    """締結座の数は**構築したソリッドの側でも**数えられる（幾何セレクタ）。

    戻り値の整数だけを見るテストは、穴の空いていない部品でも通ってしまう。
    """
    params = _params()
    bore_radius = params.joint.insert_outer_diameter_mm / 2.0
    for segment in build_segments(params):
        assert (
            _retrofit_bore_count(segment.solid, bore_radius)
            == segment.retrofit_seat_count
        )


@requires_cad
def test_retrofit_seat_total_follows_the_parameter_when_it_changes() -> None:
    """締結座の数はパラメータに追随する（10 箇所なら 10 箇所）。"""
    params = _params(retention=_retention(retrofit_fastener_count=10))
    segments = build_segments(params)
    assert sum(segment.retrofit_seat_count for segment in segments) == 10
    # 10 は分割数 5 で割り切れるため、全セグメントが同一形状になる。
    assert {segment.retrofit_seat_count for segment in segments} == {2}
    volumes = [segment.solid.volume for segment in segments]
    assert max(volumes) - min(volumes) < 1e-6


@requires_cad
def test_retrofit_seats_do_not_pierce_the_flange_top_surface() -> None:
    """締結座はフランジ**下面**に設け、上面へ貫通させない。

    上面は物が落ちてくる面である。座を上面へ抜くと、受け口の捕捉面に穴が並ぶ。
    """
    params = _params()
    bore_radius = params.joint.insert_outer_diameter_mm / 2.0
    segment = build_segments(params)[0]
    assert _retrofit_bore_count(segment.solid, bore_radius) >= 1
    inner_r = rim_geometry(params).inner_diameter_mm / 2.0
    outer_r = rim_geometry(params).outer_diameter_mm / 2.0
    top_inner = _top_surface_z_at_radius(segment.solid, inner_r + 0.5)
    top_outer = _top_surface_z_at_radius(segment.solid, outer_r - 0.5)
    # 上面は内周から外周まで単調に上がる連続面である（穴で欠けていない）。
    mid = (inner_r + outer_r) / 2.0
    top_mid = _top_surface_z_at_radius(segment.solid, mid)
    assert top_inner < top_mid < top_outer


@requires_cad
def test_undivided_rim_is_built_as_a_single_seamless_ring() -> None:
    """分割不要（`segment_count == 1`）なら継手を持たない1体のリングになる。

    ⚠️ 分割数は導出値であり、1 になりうる（`constraints.required_segment_count`
    の戻り値 1 は「分割しなくても収まる」を意味する）。継手を無条件に作る実装は
    ここで、存在しない合わせ目へ穴を開ける。
    """
    params = _params_for(
        opening_inner_diameter_mm=150.0,
        top_outer_diameter_mm=152.0,
        fit_clearance_mm=0.2,
        flange_width_mm=11.0,
    )
    assert rim_geometry(params).segment_count == 1
    segments = build_segments(params)
    assert len(segments) == 1
    assert segments[0].joint_bearing_area_mm2 is None
    start_normal, end_normal = _end_face_outward_normals(1)
    assert _end_face_bores(segments[0].solid, start_normal) == []
    assert _end_face_bores(segments[0].solid, end_normal) == []
    # 締結座はリング全体に指定数だけ在る（分割されていなくても変わらない）。
    assert segments[0].retrofit_seat_count == params.retention.retrofit_fastener_count


# ---------------------------------------------------------------------------
# 成立しない寸法の拒否（`build_segments` のガード群）
#
# ⚠️ **各テストは「例外が出たこと」ではなく「意図したガードが出したこと」を
# 固定する。** `pytest.raises(GeometryError)` だけを見るテストは、別のガードが
# 先に発火しても緑になり、名指ししたガードを一度も通らないまま「検証済み」に
# 見えてしまう（実際に一度この誤りを踏んだ）。したがって全件で例外メッセージの
# 特徴的な部分文字列を assert する。
#
# ガードの発火順は `build_segments` の実行順であり、前段のガードが後段を覆い隠す。
# そのため各テストは**そのガードだけが発火する**パラメータを選んである。
# ---------------------------------------------------------------------------


def test_build_segments_refuses_a_wall_thicker_than_the_flange() -> None:
    """壁の肉厚がフランジ幅以上なら断面が成立しない（`build_segments` 冒頭）。

    フランジは「取り付け部の壁より外側へ張り出す部分」であり、壁がフランジ幅に
    達すると張り出しが消える。⚠️ 継手座の張り出しの検査（下のテスト）より**前**に
    あるため、両方を同時に破るパラメータでは本ガードが先に出る。
    """
    params = _params(rim=_rim(flange_width_mm=3.0))
    with pytest.raises(GeometryError) as excinfo:
        build_segments(params)
    message = str(excinfo.value)
    assert "wall_thickness_mm=4.0" in message
    assert "flange_width_mm=3.0" in message
    assert "フランジの断面が" in message


def test_build_segments_refuses_a_flange_too_narrow_for_the_joint_boss() -> None:
    """継手座の張り出しがフランジ幅を超える寸法は拒否する。

    継手座は `wall_thickness + 2 × insert_outer_diameter` だけ外向きに張り出す。
    ⚠️ 壁（4mm）に貫通ボルト穴（φ3.4）を通すと残り肉が 0.3mm になるため、座を
    設けずに壁へ直接穴を開ける構成は採れない——この張り出しは削れない量である。

    ⚠️ **境界の両側を固定する。** 10.0mm では拒否され、12.0mm では構築できる
    （後者は `test_a_flange_just_wide_enough_for_the_joint_boss_builds`）。
    片側だけを見るテストは、別のガードが代わりに発火しても緑になる。
    """
    too_narrow = _params(rim=_rim(flange_width_mm=10.0, wall_thickness_mm=1.0))
    with pytest.raises(GeometryError) as excinfo:
        build_segments(too_narrow)
    message = str(excinfo.value)
    assert "継手座の張り出し" in message
    assert "insert_outer_diameter_mm=4.6" in message
    assert "flange_width_mm=10.0" in message


@requires_cad
def test_a_flange_just_wide_enough_for_the_joint_boss_builds() -> None:
    """境界の反対側: フランジ 12.0mm（張り出し 10.2mm）なら構築できる。

    ⚠️ 拒否側だけを固定すると、ガードが**常に**発火する（例: 条件を反転した）
    実装でも緑になる。⚠️ 本テストだけが形状ライブラリを要するため、拒否側とは
    別のテストへ分けてある（要件 5.7: 形状生成を除く検査は非導入環境で完了する）。
    """
    wide_enough = _params(rim=_rim(flange_width_mm=12.0, wall_thickness_mm=1.0))
    segments = build_segments(wide_enough)
    assert len(segments) == rim_geometry(wide_enough).segment_count


def test_build_segments_refuses_a_rim_too_short_to_host_the_joint() -> None:
    """取り付け部が低すぎて継手を置けない寸法は `GeometryError` で拒否する。

    ⚠️ 黙って穴同士を重ねたり、穴を部品の外へはみ出させたりしない。
    """
    params = _params(rim=_rim(height_mm=6.0))
    with pytest.raises(GeometryError) as excinfo:
        build_segments(params)
    message = str(excinfo.value)
    assert "しかなく、下限" in message
    assert "height_mm=6.0" in message


def test_build_segments_refuses_a_joint_boss_that_fills_the_sector_angle() -> None:
    """継手座が分割角を占め尽くす寸法は拒否する。

    座の周方向の厚みは `3 × insert_length` である。両端の座が分割角を埋めると
    セグメントの本体が残らない。⚠️ 座同士が重なったソリッドを黙って返さない。
    """
    params = _params(joint=_joint(insert_length_mm=30.0))
    with pytest.raises(GeometryError) as excinfo:
        build_segments(params)
    assert "占め尽くす" in str(excinfo.value)


def test_build_segments_refuses_a_joint_whose_end_face_is_consumed_by_bores() -> None:
    """端面が穴で埋まって支圧面積が残らない寸法は拒否する。

    ⚠️ 支圧面積が 0 以下でも `check_joint` は `_require_positive_finite` で
    弾くが、そこへ**負の面積を渡してから**弾かせると「面積が正でない」という
    一般的な誤りに化け、原因（端面が穴で埋まった）が失われる。ここで先に落とす。

    到達させるには、荷重を受けない位置決めダボだけが極端に太い継手方針が要る
    （ダボ φ7.2 に対しインサート φ0.5）。⚠️ 現実の部品としては不合理だが、
    `JointPolicy` はこの組み合わせを構築できてしまう。
    """
    params = _params(
        rim=_rim(wall_thickness_mm=0.5, flange_width_mm=10.0, height_mm=21.0),
        joint=_joint(insert_outer_diameter_mm=0.5, dowel_diameter_mm=7.2),
    )
    with pytest.raises(GeometryError) as excinfo:
        build_segments(params)
    message = str(excinfo.value)
    assert "端面の接触面積" in message
    assert "dowel_diameter_mm=7.2" in message


def test_build_segments_refuses_a_retrofit_pad_that_leaves_the_flange_band() -> None:
    """⚠️ 決定1 の最後の砦: 締結座のパッドが取り付け部の内径より内側へ入らない。

    パッド（外径 `2 × insert_outer_diameter`）はフランジ帯の中央に置く。
    フランジ幅が `2 × insert_outer_diameter` を下回るとパッドは帯からはみ出し、
    内側へはみ出せば**受け口が開口内径を狭める**——design.md「受け口形状の決定」
    決定1「開口内径を一切狭めない」に真っ向から反する形になる。

    ⚠️ **分割数 1 でしか到達できない。** 分割数が 2 以上では継手座の張り出しの
    検査（`wall + 2 × insert_od <= flange_width`）が先に発火し、そちらを満たす
    寸法は本条件（`flange_width < 2 × insert_od`）を同時には満たせない。
    したがって合わせ目を持たない小径の受け口が、このガードの唯一の到達経路である。
    """
    params = _params_for(
        opening_inner_diameter_mm=150.0,
        top_outer_diameter_mm=152.0,
        fit_clearance_mm=0.2,
        flange_width_mm=4.0,
        wall_thickness_mm=1.0,
    )
    assert rim_geometry(params).segment_count == 1
    with pytest.raises(GeometryError) as excinfo:
        build_segments(params)
    message = str(excinfo.value)
    assert "フランジの帯" in message
    assert "決定1" in message


def test_build_segments_refuses_a_retrofit_pad_that_hangs_below_the_floor() -> None:
    """締結座のパッドが部品の底面より下へ出る寸法は拒否する。

    パッドはフランジ下面から `insert_length + 余裕` だけ垂れ下がる。底面より下へ
    出ると、⚠️ **受け口がゴミ箱の縁に座らない**（部品が浮く）。
    """
    params = _params(joint=_joint(insert_length_mm=20.0))
    with pytest.raises(GeometryError) as excinfo:
        build_segments(params)
    message = str(excinfo.value)
    assert "パッドの下端が" in message
    assert "insert_length_mm=20.0" in message


def test_build_segments_refuses_retrofit_seats_that_straddle_an_end_face() -> None:
    """⚠️ 締結座の「リング全体で指定数」という配分が破綻する条件を拒否する。

    座はリング全体の等分角に置き、各セグメントは自分の角度範囲に入った座を持つ。
    座の数が増えると、どれかがセグメントの端面にまたがる位置へ来る——そのまま
    構築すると隣のセグメントと干渉し、突き合わせられない部品ができる。

    19 箇所 / 5 分割で到達する（局所角 34.1°、パッド半角 2.05°、分割半角 36°）。
    """
    params = _params(retention=_retention(retrofit_fastener_count=19))
    with pytest.raises(GeometryError) as excinfo:
        build_segments(params)
    message = str(excinfo.value)
    assert "端面をまたぐ" in message
    assert "19" in message


# ---------------------------------------------------------------------------
# 解析値と実形状の突き合わせ
# ---------------------------------------------------------------------------


def _joint_boss_region(solid: object, params: MechanismParams) -> object:
    """ソリッドから**継手座の範囲**（半径で切り出した領域）を取り出す。

    ⚠️ 切り出しは半径という**幾何量**で行う（生成名に依存しない）。座の半径方向の
    張り出しは `wall_thickness + 係数 × insert_outer_diameter` であり、係数は
    実装が持つ定数をそのまま参照する——ここで別の値を書くと、テストが式ではなく
    「テストが思う座の大きさ」を検査することになる。
    """
    from build123d import Align, Cylinder

    boss_radial_mm = (
        params.rim.wall_thickness_mm
        + shapes_module._JOINT_BOSS_INSERT_DIAMETER_MULTIPLE
        * params.joint.insert_outer_diameter_mm
    )
    clip_radius_mm = rim_geometry(params).inner_diameter_mm / 2.0 + boss_radial_mm
    return solid & Cylinder(  # type: ignore[operator]
        clip_radius_mm,
        400.0,
        align=(Align.CENTER, Align.CENTER, Align.CENTER),
    )


@requires_cad
def test_joint_bearing_area_matches_the_measured_boss_face_of_the_built_solid() -> None:
    """⚠️ 支圧面積が**構築したソリッドの継手座の端面**の実面積と一致する。

    `joint_bearing_area_mm2` は `check_joint` をソリッド構築の**前**に通すため
    解析式で持つ（要件 2.7）。解析式が実形状から離れていれば、下限を満たすという
    検査は形状について何も言っていないことになる。ここで両者を突き合わせる。

    ⚠️ **測る範囲は継手座に限る。** 端面全体を測って比べる形にすると、ボルトから
    十数 mm 離れたフランジ外周の断面まで支圧面積に数える式を「実測と一致する」と
    して通してしまう。`check_joint` が名指しする破壊モードはボルト座面のめり込み
    であり、離れた材料は座面圧を下げない（要件 2.6, 8.4）。

    ⚠️ 採るのは**穴の大きい方の端面**（＝接触面積の狭い方）である。始端面には
    貫通ボルト穴（φ3.4）、終端面には金属インサート座（φ4.6）が開くため、両者の
    面積は一致しない。式に定数倍を掛けると本テストが落ちる。
    """
    params = _params()
    start_normal, end_normal = _end_face_outward_normals(
        rim_geometry(params).segment_count
    )
    for segment in build_segments(params):
        boss_region = _joint_boss_region(segment.solid, params)
        measured = [
            sum(
                face.area
                for face in _planar_faces_on_end_plane(boss_region, normal)
            )
            for normal in (start_normal, end_normal)
        ]
        assert all(area > 0.0 for area in measured)
        assert segment.joint_bearing_area_mm2 == pytest.approx(
            min(measured), rel=1e-9
        )
        # 狭い方を採っている（広い方をそのまま報告していない）ことも固定する。
        assert segment.joint_bearing_area_mm2 < max(measured)


@requires_cad
def test_joint_bearing_area_matches_the_measured_boss_face_at_other_dimensions() -> None:
    """別の寸法でも解析値と継手座の実面積が一致する（一点合わせではない）。"""
    params = _params(rim=_rim(flange_width_mm=12.0, wall_thickness_mm=1.0))
    start_normal, end_normal = _end_face_outward_normals(
        rim_geometry(params).segment_count
    )
    segment = build_segments(params)[0]
    boss_region = _joint_boss_region(segment.solid, params)
    measured = min(
        sum(face.area for face in _planar_faces_on_end_plane(boss_region, normal))
        for normal in (start_normal, end_normal)
    )
    assert segment.joint_bearing_area_mm2 == pytest.approx(measured, rel=1e-9)
    # 出荷相当（フランジ 30 / 壁 4）の値とは明確に異なる＝定数ではない。
    assert segment.joint_bearing_area_mm2 < 250.0


@requires_cad
def test_joint_bearing_area_is_insensitive_to_the_flange_width() -> None:
    """⚠️ 要件 2.6 / 8.4 の内容を**性質**として固定する: フランジ幅に非感応。

    `check_joint` が名指しする破壊モードは「ボルトの締め付け力が樹脂へ集中して
    座面がめり込み、締結が緩む」であり、支配量はボルト**座面圧**である。ボルト軸
    から離れたフランジ外周へ材料を足しても座面圧は 1Pa も下がらない。

    ⚠️ **フランジ断面を算入する式は、フランジ幅を広げるだけで支圧面積が増える。**
    そうなると「当たり面が足りない」という判定を、当たり面と無関係な寸法で買える
    ことになる。フランジ幅を 30 → 60 へ倍にしても値が動かないことが、
    「離れた材料を当たり面に数えていない」ことの観測可能な形である。
    """
    narrow = _params(rim=_rim(flange_width_mm=30.0))
    wide = _params(rim=_rim(flange_width_mm=60.0))
    # フランジ幅は外径を通じて分割数を変える。⚠️ 支圧面積はそれにも依らない。
    assert rim_geometry(narrow).segment_count != rim_geometry(wide).segment_count

    narrow_area = build_segments(narrow)[0].joint_bearing_area_mm2
    wide_area = build_segments(wide)[0].joint_bearing_area_mm2
    assert narrow_area is not None and wide_area is not None
    assert wide_area == pytest.approx(narrow_area, rel=1e-12)


def test_bearing_area_rejects_a_joint_that_a_wider_flange_must_not_rescue() -> None:
    """⚠️ 当たり面の不足を、当たり面と無関係な寸法で埋め合わせられない。

    取り付け部が低く（16mm）壁が薄く（1mm）インサートが細い（φ1.0）継手座は、
    フランジ幅が 30mm あっても支圧面積の下限 60mm^2 に届かない。フランジ断面を
    算入する式ではこの寸法が**受理されてしまう**（座の実面積は変わらないのに）。

    ⚠️ 形状ライブラリを必要としない（`check_joint` はソリッド構築の前に走る）。
    """
    params = _params(
        rim=_rim(height_mm=16.0, wall_thickness_mm=1.0, flange_width_mm=30.0),
        joint=_joint(insert_outer_diameter_mm=1.0),
    )
    with pytest.raises(ParameterError) as excinfo:
        build_segments(params)
    message = str(excinfo.value)
    assert "min_bearing_area_mm2=60.0" in message
    assert "下回る" in message


def test_segment_envelope_delegates_to_the_constraints_sector_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """⚠️ 扇形の外接箱を**再実装せず** `constraints.sector_envelope` へ委譲する。

    tasks.md「Implementation Notes」タスク 2.1(b)「⚠️ 形状層（タスク 3.2）は同じ
    幾何を再実装せず、この関数を使うこと。」を固定する。⚠️ **値の一致だけを見る
    テストでは守れない**——同値の式をインラインで書いても通ってしまい、
    `n = 1` の場合分けや半径方向の最悪値の取り方が2箇所へ分岐する余地が残る。
    呼び出しそのものと引数を観測する。
    """
    params = _params()
    geometry = rim_geometry(params)
    calls: list[tuple[float, int, float]] = []

    def spy(outer_diameter_mm: float, segment_count: int, height_mm: float) -> Envelope:
        calls.append((outer_diameter_mm, segment_count, height_mm))
        return sector_envelope(outer_diameter_mm, segment_count, height_mm)

    monkeypatch.setattr(shapes_module, "sector_envelope", spy)
    result = segment_envelope(params)

    assert calls == [
        (
            geometry.outer_diameter_mm,
            geometry.segment_count,
            segment_height_mm(params),
        )
    ]
    assert result == sector_envelope(*calls[0])


# ---------------------------------------------------------------------------
# タスク 3.3: 形状指標の抽出と決定性（要件 3.7, 4.1）
#
# 本節が固定するのは tasks.md タスク 3.3 の「観測可能な完了状態」
# 「同一パラメータから2回構築した部品の指標が**完全に一致**する」と、
# design.md「Shapes」Service Interface の `BuiltPart` / `build_parts` /
# `measure_part`、および Invariants「同一パラメータからの再構築は同一の
# `PartMetrics` を返す」である。
#
# 1. **抽出は形状オブジェクトを中核層へ渡さない**こと（要件 4.1 /
#    design.md「Metrics」Responsibilities）。`PartMetrics` は素の数値だけを持ち、
#    `measure_part` は build123d の型を一切参照しない**ダックタイピング**で測る
# 2. **完全に一致**すること。⚠️ 許容差ではなく `==` である。
#    `metrics.GeometryBaseline` の許容差（`volume_rel_tolerance` /
#    `bbox_abs_tolerance_mm`）は **OCCT の版差**を吸収するために記録側が持つ量で
#    あり（design.md「Shapes」Risks）、同一環境・同一パラメータの再構築がそれに
#    頼ってよい理由にはならない
# 3. **丸めない**こと。⚠️ 抽出時に量子化すると許容差の機構が二重になり、実在する
#    ずれを記録の側から観測できなくなる
#
# ⚠️ **本節は共有フィクスチャを使わない。** 決定性の主張は「同じ入力から
# **独立に**構築しても同じ指標になる」であり、1回だけ構築した結果を2つの名前で
# 見る形は何も観測していない。実測で `build_segments` 1回は約 0.34 秒であり、
# 個別に構築しても収集全体を目立って遅くしない。
# ---------------------------------------------------------------------------


class _FakeSolid:
    """`measure_part` がダックタイピングで測ることを示す偽のソリッド。

    ⚠️ **build123d の型ではない。** `measure_part` が形状ライブラリの型・関数を
    参照していれば、この偽物は測れない。CAD 非導入の環境でも走る検査である
    （要件 5.7）。
    """

    class _Vec:
        def __init__(self, x: float, y: float, z: float) -> None:
            self.X = x
            self.Y = y
            self.Z = z

    class _Box:
        def __init__(self, size: "_FakeSolid._Vec") -> None:
            self.size = size

    def __init__(
        self, volume: float, size: tuple[float, float, float], solid_count: int
    ) -> None:
        self.volume = volume
        self._size = _FakeSolid._Vec(*size)
        self._solid_count = solid_count

    def bounding_box(self) -> "_FakeSolid._Box":
        return _FakeSolid._Box(self._size)

    def solids(self) -> list[object]:
        return [object() for _ in range(self._solid_count)]


# --- 形状ライブラリを必要としない検査 ---------------------------------------


def test_built_part_is_a_frozen_value_with_the_designed_fields() -> None:
    """`BuiltPart` は design.md の Service Interface どおり3項目の不変値である。"""
    assert [field.name for field in fields(BuiltPart)] == ["name", "solid", "metrics"]
    part = BuiltPart(
        name="rim_segment_1",
        solid=object(),
        metrics=PartMetrics(
            part_name="rim_segment_1",
            volume_mm3=1.0,
            bbox_mm=(1.0, 2.0, 3.0),
            solid_count=1,
        ),
    )
    with pytest.raises(FrozenInstanceError):
        part.name = "other"  # type: ignore[misc]


def test_measure_part_extracts_the_three_metrics_by_duck_typing() -> None:
    """⚠️ **形状ライブラリの型を参照せずに**体積・境界箱・立体数を抽出する。

    `measure_part(name, solid)` の `solid` は design.md が `object` と宣言する。
    偽のソリッドで測れることが「build123d の型が署名にも実装にも漏れていない」
    ことの観測である。CAD 非導入の環境でも走る。
    """
    solid = _FakeSolid(volume=1234.5, size=(10.0, 20.0, 30.0), solid_count=2)
    metrics = measure_part("rim_segment_1", solid)
    assert isinstance(metrics, PartMetrics)
    assert metrics.part_name == "rim_segment_1"
    assert metrics.volume_mm3 == 1234.5
    assert metrics.bbox_mm == (10.0, 20.0, 30.0)
    assert metrics.solid_count == 2


def test_measure_part_does_not_round_or_quantize_the_extracted_numbers() -> None:
    """⚠️ **丸めない。** 下位桁までそのまま指標へ渡す。

    量子化すると許容差の機構が二重になる——`metrics.GeometryBaseline` は既に
    `volume_rel_tolerance` / `bbox_abs_tolerance_mm` を持ち、OCCT の版差は
    **記録側**で吸収する設計である（design.md「Shapes」Risks）。抽出側でも丸めると
    記録側の許容差を 0 に置いても観測できないずれが残り、「どれだけ動いたか」を
    記録から読めなくなる。
    """
    volume = 36524.60651339012345
    size = (51.676571238443471, 168.69436760793977, 30.038475772933681)
    metrics = measure_part("rim_segment_1", _FakeSolid(volume, size, 1))
    assert metrics.volume_mm3.hex() == volume.hex()
    assert tuple(value.hex() for value in metrics.bbox_mm) == tuple(
        value.hex() for value in size
    )


def test_measure_part_carries_only_plain_numbers_into_the_core_layer() -> None:
    """指標は**素の数値だけ**であり、形状オブジェクトを中核層へ渡さない（要件 4.1）。

    tasks.md タスク 3.3「抽出は形状オブジェクトを中核層へ渡さない形で行う」の
    固定である。⚠️ `PartMetrics` にソリッドを載せる項目が生えていないこと自体を
    検査する。
    """
    solid = _FakeSolid(volume=1234.5, size=(10.0, 20.0, 30.0), solid_count=1)
    metrics = measure_part("rim_segment_1", solid)
    assert type(metrics.volume_mm3) is float
    assert type(metrics.bbox_mm) is tuple
    assert all(type(extent) is float for extent in metrics.bbox_mm)
    assert type(metrics.solid_count) is int
    for field in fields(PartMetrics):
        assert getattr(metrics, field.name) is not solid


def test_measure_part_rejects_a_shape_that_holds_no_solid() -> None:
    """立体を持たない形状は指標として記録しない（`PartMetrics` の不変条件）。

    ⚠️ 形状生成が空を返した事故を「立体数 0 の部品」として通さない。
    """
    with pytest.raises(ParameterError):
        measure_part("rim_segment_1", _FakeSolid(1.0, (1.0, 1.0, 1.0), 0))


# --- 形状ライブラリを要する検査（要件 5.7 により個別に skip される）-----------


@requires_cad
def test_build_parts_wraps_every_segment_with_its_metrics() -> None:
    """`build_parts` は導出された分割数だけ `BuiltPart` を返す（⚠️ 1点ではない）。

    ⚠️ tasks.md「Implementation Notes」タスク 3.2(b): 締結座はリング全体で
    `retrofit_fastener_count` 箇所であり、出荷値 6箇所 / 5分割では配分が
    `[1,1,2,1,1]` になる。**部品は5点**であって「1個を5回刷る」のではない。
    """
    params = _params()
    parts = build_parts(params)
    assert len(parts) == rim_geometry(params).segment_count == 5
    assert all(isinstance(part, BuiltPart) for part in parts)
    assert [part.name for part in parts] == list(segment_part_names(params))
    assert [part.metrics.part_name for part in parts] == [part.name for part in parts]
    for part in parts:
        assert part.metrics.solid_count == 1
        assert part.metrics.volume_mm3 > 0.0


@requires_cad
def test_built_part_metrics_match_the_solid_it_carries() -> None:
    """指標は `BuiltPart.solid` そのものから抽出した値と厳密に一致する。

    ⚠️ 別のソリッド（例えば常に先頭のセグメント）を測っていれば落ちる。
    """
    for part in build_parts(_params()):
        solid = part.solid
        size = solid.bounding_box().size  # type: ignore[attr-defined]
        assert part.metrics.volume_mm3 == solid.volume  # type: ignore[attr-defined]
        assert part.metrics.bbox_mm == (size.X, size.Y, size.Z)
        assert part.metrics.solid_count == len(solid.solids())  # type: ignore[attr-defined]
        assert part.metrics == measure_part(part.name, solid)


@requires_cad
def test_built_part_bbox_is_the_envelope_used_for_the_build_volume_check() -> None:
    """境界箱は実ソリッドの軸並行外接箱であり、造形可能寸法の検査と同じ量である。

    既存の `_built_envelope`（タスク 3.2 の検査が使う量）と一致することで、指標の
    境界箱が「別の座標系で測った別物」でないことを固定する。⚠️ 部品固有の座標系へ
    移し替えた最小外接箱ではなく、`build_segments` が返す位置のままの軸並行箱で
    ある（リングの中心が原点、セグメントは +X 軸まわりに対称）。
    """
    params = _params()
    for part in build_parts(params):
        built = _built_envelope(part.solid)
        assert part.metrics.bbox_mm == (built.x_mm, built.y_mm, built.z_mm)
        assert check_envelope(part.name, built, params.printing) == ()


@requires_cad
def test_metrics_are_exactly_identical_across_two_independent_builds() -> None:
    """⚠️ **タスク 3.3 の観測可能な完了状態**: 2回構築した指標が**完全に一致**する。

    ⚠️ **共有フィクスチャを使わない。** 1回だけ構築した結果を2つの名前で見る形は
    何も観測していない。`build_parts` を2度呼び、独立に構築した2組を比べる。

    ⚠️ **許容差を使わない**（`pytest.approx` を使わない）。実測では体積・境界箱
    ともに**ビット単位で一致**する（同一プロセス内・別プロセス間の双方で確認済み。
    build123d 0.11.1）。ここを `approx` に緩めると、再構築ごとに下位桁が動く実装が
    緑のまま通り、要件 3.7「同一の形状指標を持つ生成物を出力する」が主張だけに
    なる。
    """
    params = _params()
    first = build_parts(params)
    second = build_parts(params)

    assert [part.name for part in first] == [part.name for part in second]
    assert len(first) == 5
    for left, right in zip(first, second, strict=True):
        assert left.metrics == right.metrics
        # ⚠️ `==` は float の厳密比較だが、ビット列でも重ねて固定する
        # （`-0.0 == 0.0` のような表現の差も許さない）。
        assert left.metrics.volume_mm3.hex() == right.metrics.volume_mm3.hex()
        assert tuple(value.hex() for value in left.metrics.bbox_mm) == tuple(
            value.hex() for value in right.metrics.bbox_mm
        )


@requires_cad
def test_the_regenerated_metrics_match_a_record_at_zero_tolerance() -> None:
    """再構築した指標は、許容差 **0** の記録とも一致する（要件 3.7, 4.4）。

    中核層の照合器（`metrics.compare_metrics`）へ実際に通すことで、抽出した指標が
    そのまま記録・照合の入力になることを固定する。⚠️ 許容差を 0 に置くのは、
    「完全に一致」を照合器の言葉で言い直したものである。

    ⚠️ **記録ファイルを出荷しない。** `configs/catch_mechanism/geometry-baseline.json`
    の作成はタスク **4.2** の担当であり（`parameters_digest` と CLI を要する）、
    本検査は記録を**その場で組み立てる**（tasks.md「Implementation Notes」
    タスク 2.4(a) と同じ規律）。
    """
    params = _params()
    recorded = {part.name: part.metrics for part in build_parts(params)}
    baseline = GeometryBaseline(
        schema_version=SCHEMA_VERSION,
        parameters_digest="sha256:" + "0" * 64,
        volume_rel_tolerance=0.0,
        bbox_abs_tolerance_mm=0.0,
        generator_version="build123d-test",
        parts=recorded,
    )
    regenerated = {part.name: part.metrics for part in build_parts(params)}
    assert compare_metrics(baseline, regenerated) == ()


@requires_cad
def test_the_segment_with_two_retrofit_seats_is_measurably_distinct() -> None:
    """⚠️ **5部品は同一形状ではない**——決定性の検査を空虚にしないための観測。

    tasks.md「Implementation Notes」タスク 3.2(b): 出荷値 6箇所 / 5分割では締結座の
    配分が `[1,1,2,1,1]` となり、座を2つ持つ `rim_segment_3` だけが他より厚い。
    ⚠️ **全セグメントが同一形状になる不具合**（たとえば座の配分をセグメントごとに
    一定にしてしまう誤り）は、決定性の検査だけでは気付けない——どの部品も同じ値なら
    2回の構築も当然一致する。「違いが実際にある」ことをここで観測して初めて、
    決定性の一致が意味を持つ。

    ⚠️ 座を1つ持つ4点は既定精度の `Shape.volume` では下位桁が分かれるが、
    **これは求積誤差であって実形状の差ではない**（`BRepGProp` の `Eps` を 1e-9 まで
    締めると相対 2e-13 で収束する。`retrofit_fastener_count` を 10 や 5 にして
    全セグメントを同一相対角にすればビット単位で一致する）。したがって
    ⚠️ **その差が「存在すること」を assert してはならない**——版が上がって
    正当に一致したときに、何も壊れていないのにテストが落ちる。ここでは
    「4点が互いに近いこと」だけを固定する。版差の吸収は記録側の許容差
    （`metrics.GeometryBaseline.volume_rel_tolerance`）の役割である。
    """
    params = _params()
    parts = build_parts(params)
    volumes = [part.metrics.volume_mm3 for part in parts]
    assert [segment.retrofit_seat_count for segment in build_segments(params)] == [
        1,
        1,
        2,
        1,
        1,
    ]

    two_seat = volumes[2]
    one_seat = volumes[:2] + volumes[3:]
    # 座が2つある部品は、他の4点のいずれより明確に大きい。
    assert all(two_seat - value > 1.0 for value in one_seat)
    # 座が1つの4点は互いに近い。⚠️ 既定精度では下位桁が分かれるが、それは求積誤差で
    # あり実形状の差ではないため、「分かれていること」は assert しない（docstring 参照）。
    assert max(one_seat) - min(one_seat) < 1.0e-2


@requires_cad
def test_build_parts_delegates_the_construction_to_build_segments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`build_parts` は `build_segments` へ委譲し、形状の構築を二重に持たない。

    ⚠️ **値の一致だけを見る検査では守れない**——同じ形状を別経路で組み立てても
    通ってしまい、決定1（内向きに張り出さない）や締結座の配分といった規則が
    2箇所へ分岐する余地が残る。呼び出しそのものと引数を観測する
    （`test_segment_envelope_delegates_to_the_constraints_sector_envelope` と同形）。
    """
    params = _params()
    calls: list[MechanismParams] = []
    real = shapes_module.build_segments

    def spy(argument: MechanismParams) -> tuple[RimSegment, ...]:
        calls.append(argument)
        return real(argument)

    monkeypatch.setattr(shapes_module, "build_segments", spy)
    parts = shapes_module.build_parts(params)

    assert calls == [params]
    assert [part.name for part in parts] == list(segment_part_names(params))
