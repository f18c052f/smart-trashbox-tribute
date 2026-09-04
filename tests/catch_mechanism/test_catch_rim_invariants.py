"""受け口の不変条件（タスク 4.4 / 要件 8.2, 8.3, 9.4, 9.6, 9.7）。

⚠️⚠️ **本ファイルが検査するしきい値は「設計の自己整合性」の検査であり、
プロジェクトの合否条件ではない。**（要件 9.6）

`docs/requirements.md` の NFR-7 をはじめとする**プロジェクトの合否条件は、実物を
用いた計測（M1〜M3）で判定される**。本ファイルが確かめるのは、そこへ至る前段の
「設計が自分自身と矛盾していないか」だけである——受け口が開口を狭めていないか、
採寸値を変えたときに寸法が追随するか、導出した分割数の断片が造形可能寸法に
収まるか、決定として記録した値が型で固定されているか。

⚠️ **本ファイルの入力は大半が仮値である。** 出荷
`configs/catch_mechanism/dimensions.json` の採寸値はタスク 5.2 で実測へ差し替わる
予定であり、本ファイルが使う数値は（実測の当たりを付けた範囲の）**掃引用の合成値**
にすぎない。したがって本ファイルが全件通ることは「受け口が実物のゴミ箱に嵌まる」
ことも「取りこぼしを防げる」ことも意味しない。design.md「受け口形状の決定」が
述べるとおり、`bounce_out` は `docs/decisions.md` D-9 によりシミュレータのモデル外
であり、決定 1〜5 は**未実測の推定を含む机上の判断**である。要件 9.6「未実測の推定
に基づく判断材料を、合否条件と区別できる形で記録する」に従い、この区別を本ファイル
自身の説明として明記する。

## 本ファイルの立ち位置（`test_catch_shapes.py` との違い）

`test_catch_shapes.py`（タスク 3.1 / 3.2 / 3.3）は `rim_geometry` と
`build_segments` の**契約**を、代表点と境界事例で固定する。本ファイルはその上に
**パラメータ空間全体にわたる性質**を重ねる——1点で成り立つ等式は、その1点の外側で
丸め・クランプ・場合分けが入り込んでも落ちない。

したがって本ファイルの検査は次の形を取る。

1. **決定的な掃引**（`random.Random` を固定種で使う。振られる値は実行ごとに
   変わらない）
2. **二分岐の全域性**（値を返すか、明示的に拒否するか。第三の道が無いこと）
3. **表現不可能性**（決定 2 / 決定 3 は「設定されていない」のではなく「設定
   できない」。`dataclasses.replace` を含むあらゆる経路で構築できないこと）

⚠️ **掃引は `rim_geometry` を中心に置く。** `rim_geometry` は純粋な算術であり
形状ライブラリを要さないため（要件 5.7）、2万件でも1秒未満で回る。ソリッドの構築は
1点あたり約 0.36 秒かかるため、`build_segments` を要する検査は代表的な少数の
組み合わせに限り、同じ組み合わせの再構築を `_cached_build` で避ける。

⚠️ **出荷 `configs/catch_mechanism/dimensions.json` を読まない**
（`test_catch_shapes.py` と同じ理由。タスク 5.2 が採寸値を書き戻したときに、境界の
外側から落ちるテストを作らない）。本ファイルは局所のヘルパで値を組み立て、
**関係**だけを固定する。

ファイル名について: design.md `### Directory Structure` は本ファイルを
`test_rim_invariants.py` と呼ぶが、`tests/` 配下に `__init__.py` が無く pytest の
import-mode も既定（prepend）のためテストモジュール名がセッション全体でフラット
である。既存の `test_catch_*.py` に倣う（tasks.md「Implementation Notes」
タスク 1.1）。
"""

from __future__ import annotations

import dataclasses
import math
import random
from typing import Iterator

import pytest

from catch_mechanism.constraints import check_envelope, required_segment_count
from catch_mechanism.errors import GeometryError, ParameterError
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
    RimSegment,
    build_segments,
    rim_geometry,
    segment_envelope,
)

# ---------------------------------------------------------------------------
# 形状ライブラリの有無（要件 5.7）。
# ⚠️ **モジュール全体を `pytest.importorskip` で落とさない。** 本ファイルの掃引は
# `rim_geometry` / `segment_envelope`（純粋な算術）だけで完結し、`cad` extra
# 非導入の環境でも走らなければならない。ソリッドを実際に構築する検査だけを個別に
# skip し、**理由を文字列として残す**（tasks.md タスク 3.2(g) / 要件 5.7）。
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
# パラメータ組み立ての補助（`test_catch_shapes.py` / `test_catch_params.py` と同形）。
# ⚠️ 出荷 `dimensions.json` を読まず、局所の値から組み立てる。
# ---------------------------------------------------------------------------


def _params(
    *,
    opening_inner_diameter_mm: float = 220.0,
    top_outer_diameter_mm: float = 225.0,
    fit_clearance_mm: float = 1.0,
    flange_width_mm: float = 30.0,
    flange_slope_deg: float = 15.0,
    wall_thickness_mm: float = 4.0,
    rim_height_mm: float = 18.0,
    build_x_mm: float = 180.0,
    build_y_mm: float = 180.0,
    build_z_mm: float = 180.0,
    segment_margin_mm: float = 5.0,
    retrofit_fastener_count: int = 6,
) -> MechanismParams:
    """掃引で動かす項目だけを引数に持つパラメータ集約を組み立てる。

    底径は `bottom_flat <= bottom_outer <= opening_inner`（`params` の不変条件）を
    満たすよう開口内径から従属させる。⚠️ 本ファイルの関心は受け口の幾何であり、
    底径そのものではない。
    """
    return MechanismParams(
        trash_can=TrashCanMeasurements(
            model_id="sweep-synthetic",
            opening_inner_diameter_mm=opening_inner_diameter_mm,
            top_outer_diameter_mm=top_outer_diameter_mm,
            bottom_outer_diameter_mm=opening_inner_diameter_mm * 0.7,
            bottom_flat_diameter_mm=opening_inner_diameter_mm * 0.6,
            height_mm=244.0,
            mass_g=228.0,
            bottom_thickness_mm=1.5,
            taper_deg=7.0,
        ),
        target_object=ObjectSpec(diameter_mm=65.0, height_mm=122.0),
        printing=PrintingConstraints(
            build_x_mm=build_x_mm,
            build_y_mm=build_y_mm,
            build_z_mm=build_z_mm,
            material="PETG",
            material_density_g_cm3=1.27,
            segment_margin_mm=segment_margin_mm,
        ),
        joint=JointPolicy(
            bolt_designation="M3",
            through_hole_diameter_mm=3.4,
            insert_outer_diameter_mm=4.6,
            insert_length_mm=5.7,
            dowel_diameter_mm=3.0,
            min_bearing_area_mm2=60.0,
        ),
        rim=RimParams(
            fit_clearance_mm=fit_clearance_mm,
            flange_width_mm=flange_width_mm,
            flange_slope_deg=flange_slope_deg,
            wall_thickness_mm=wall_thickness_mm,
            height_mm=rim_height_mm,
        ),
        retention=RetentionParams(
            retrofit_fastener_count=retrofit_fastener_count,
            liner_flat_min_diameter_mm=140.0,
        ),
        provenance={"trash_can.opening_inner_diameter_mm": Provenance.ASSUMED},
    )


#: 掃引の件数。タスク 3.1 のレビューが敵対的2万件で `clear_opening_diameter_mm` の
#: 事後条件を確かめた（tasks.md「Implementation Notes」タスク 3.1(b)「敵対的2万件で
#: 例外0件」）が、⚠️ **その掃引はレビューの中だけで行われ、固定されていなかった。**
#: 本ファイルはそれを回帰として残す。`rim_geometry` は形状を構築しないため、
#: 2万件でも1秒未満で回る。
SWEEP_SIZE: int = 20_000

#: 掃引の種。⚠️ **固定する。** 実行のたびに違う空間を見るテストは、落ちた回だけ
#: 再現できない事実を報告することになる。
SWEEP_SEED: int = 20260904


def _sweep(
    size: int = SWEEP_SIZE, *, seed: int = SWEEP_SEED
) -> Iterator[tuple[float, float, float, float]]:
    """`(開口内径, 上端外径, 隙間, フランジ幅)` を決定的に掃引する。

    ⚠️ **上端外径は開口内径をまたぐ両側から採る。** 受け口が成立する側だけを掃引
    すると、要件 8.2 の「狭めないことを**検査する**」側（拒否）が一度も実行されない。
    """
    rng = random.Random(seed)
    for _ in range(size):
        opening_inner_mm = rng.uniform(120.0, 300.0)
        yield (
            opening_inner_mm,
            opening_inner_mm + rng.uniform(-12.0, 12.0),
            rng.uniform(0.05, 4.0),
            rng.uniform(5.0, 45.0),
        )


_BUILD_CACHE: dict[str, tuple[RimSegment, ...]] = {}


def _cached_build(label: str, params: MechanismParams) -> tuple[RimSegment, ...]:
    """同一パラメータでの再構築を避ける（1点あたり約 0.36 秒かかる）。

    ⚠️ 本ファイルの CAD 検査は「代表的な少数の組み合わせ」に限る取り決めである
    （本モジュール docstring）。同じ組み合わせを2つの検査が必要とする場合に、構築を
    2回走らせない。
    """
    if label not in _BUILD_CACHE:
        _BUILD_CACHE[label] = build_segments(params)
    return _BUILD_CACHE[label]


# ---------------------------------------------------------------------------
# 不変条件 1: 通過できる最小径が常に開口内径以上である（要件 8.2 / 決定1）
#
# ⚠️ **`assert clear >= opening` は単体ではほぼ空検査である。**
# `clear_opening_diameter_mm` は `min(取り付け部内径, 開口内径)` であり、
# tasks.md「Implementation Notes」タスク 3.1(b) が言うとおり**有効値域では常に
# 開口内径と一致する**。したがって不等式そのものには情報が無く、情報は
# 「`rim_geometry` が例外を投げなかった」事実の側にある。本節が固定するのは
# **二分岐の全域性**——取り付け部の内径が開口内径を下回る組み合わせは必ず拒否され、
# 下回らない組み合わせでは通過径が開口内径と**厳密に一致**する、という双条件。
# ---------------------------------------------------------------------------


def test_the_rim_either_leaves_the_opening_intact_or_is_refused() -> None:
    """⚠️ **設計の自己整合性の検査であり、合否条件ではない**（要件 9.6）。

    2万件の敵対的な組み合わせに対し、受け口には2つの道しか無いことを固定する
    （要件 8.2 / design.md「受け口形状の決定」決定1）。

    1. 取り付け部の内径が開口内径を**下回る**なら、`GeometryError` で拒否される。
       ⚠️ 拒否の**理由**まで確かめる——分割数が見つからずに落ちたのを「狭めない
       検査が効いた」と読み違えないため、メッセージが開口を狭めた旨を述べている
       ことを併せて見る
    2. 下回らないなら、通過できる最小径は開口内径と**厳密に一致**する
       （`>=` ではなく `==`。フランジは外向きにのみ張り出し、受け口側に絞りが
       存在しないという決定1 の内容そのもの）

    `test_catch_shapes.py::test_clear_opening_is_never_narrower_than_the_bin_opening`
    が6点で見ている不等式を、空間全体の**双条件**へ強めたものである。
    """
    narrowing = 0
    intact = 0
    for opening_mm, top_mm, clearance_mm, flange_mm in _sweep():
        params = _params(
            opening_inner_diameter_mm=opening_mm,
            top_outer_diameter_mm=top_mm,
            fit_clearance_mm=clearance_mm,
            flange_width_mm=flange_mm,
        )
        # ⚠️ 実装と同じ式で予測する（浮動小数の丸めまで一致させるため）。
        expected_bore_mm = top_mm + 2.0 * clearance_mm
        if expected_bore_mm < opening_mm:
            with pytest.raises(GeometryError) as excinfo:
                rim_geometry(params)
            assert "狭める" in str(excinfo.value)
            narrowing += 1
            continue
        try:
            geometry = rim_geometry(params)
        except GeometryError as error:
            # 分割数が見つからない外径。⚠️ 「狭める」経路と混ざっていないこと。
            assert "狭める" not in str(error)
            continue
        assert geometry.clear_opening_diameter_mm == opening_mm
        assert geometry.inner_diameter_mm >= opening_mm
        intact += 1
    # ⚠️ 掃引が両方の枝を実際に踏んだこと（空検査でないこと）を固定する。
    assert narrowing > 0
    assert intact > 0
    assert narrowing + intact > SWEEP_SIZE // 2


@pytest.mark.parametrize("opening_mm", [120.0, 180.0, 210.0, 220.0, 265.0])
def test_the_refusal_threshold_sits_exactly_at_the_opening_diameter(
    opening_mm: float,
) -> None:
    """⚠️ **設計の自己整合性の検査であり、合否条件ではない**（要件 9.6）。

    「開口を狭める値へ変更すると失敗する」（tasks.md タスク 4.4 の観測可能な完了
    状態）を、**しきい値の位置ごと**固定する。

    取り付け部の内径がちょうど開口内径のとき受理され、そこから 1µm 縮めるだけで
    拒否される。⚠️ 既存の
    `test_catch_shapes.py::test_a_rim_that_would_narrow_the_opening_is_rejected` は
    開口 220 に対し上端外径 200（20mm の食い込み）という**離れた1点**でしか拒否を
    見ていないため、「数 mm までは狭めてよい」と緩めた実装を通してしまう。本検査は
    しきい値が厳密に開口内径にあることを、複数の開口径で押さえる。
    """
    clearance_mm = 1.0
    # 取り付け部の内径がちょうど開口内径に一致する上端外径。
    exact_top_mm = opening_mm - 2.0 * clearance_mm

    geometry = rim_geometry(
        _params(
            opening_inner_diameter_mm=opening_mm,
            top_outer_diameter_mm=exact_top_mm,
            fit_clearance_mm=clearance_mm,
        )
    )
    assert geometry.inner_diameter_mm == pytest.approx(opening_mm)
    assert geometry.clear_opening_diameter_mm == pytest.approx(opening_mm)

    with pytest.raises(GeometryError) as excinfo:
        rim_geometry(
            _params(
                opening_inner_diameter_mm=opening_mm,
                top_outer_diameter_mm=exact_top_mm - 0.001,
                fit_clearance_mm=clearance_mm,
            )
        )
    assert "狭める" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 不変条件 2: 採寸値の変更に取り付け部の寸法が追随する（要件 8.5 / 1.6）
#
# ⚠️ 代表点での等式は、その点の外側で丸め・クランプ・既定値へのフォールバックが
# 入り込んでも落ちない。本節は**空間全体での恒等式**と**量子化の不在**を見る。
# ---------------------------------------------------------------------------


def test_the_mount_dimensions_are_an_exact_identity_over_the_whole_sweep() -> None:
    """⚠️ **設計の自己整合性の検査であり、合否条件ではない**（要件 9.6）。

    2万件すべてで、取り付け部の内径と受け口の外径が採寸値からの恒等式である
    （要件 8.5「採寸値が更新された場合、取り付け部の寸法をその値から再導出する」/
    8.6「個体差を吸収する隙間を寸法パラメータとして保持する」）。

    ⚠️ **`pytest.approx` を使わない。** 実装と同じ式を同じ順序で評価するためビット
    単位で一致するはずであり、許容差を置くと下位桁の丸めを見逃す。
    """
    checked = 0
    for opening_mm, top_mm, clearance_mm, flange_mm in _sweep():
        params = _params(
            opening_inner_diameter_mm=opening_mm,
            top_outer_diameter_mm=top_mm,
            fit_clearance_mm=clearance_mm,
            flange_width_mm=flange_mm,
        )
        try:
            geometry = rim_geometry(params)
        except GeometryError:
            continue
        assert geometry.inner_diameter_mm == top_mm + 2.0 * clearance_mm
        assert (
            geometry.outer_diameter_mm == geometry.inner_diameter_mm + 2.0 * flange_mm
        )
        # 分割数も導出値のままである（`constraints` への委譲。要件 8.3）。
        assert geometry.segment_count == required_segment_count(
            geometry.outer_diameter_mm, params.printing
        )
        checked += 1
    assert checked > SWEEP_SIZE // 2


@pytest.mark.parametrize("delta_mm", [0.001, 0.01, 0.1, 0.5, 1.0, 3.0, 10.0, 25.0])
def test_the_mount_bore_moves_by_exactly_the_measurement_change(
    delta_mm: float,
) -> None:
    """⚠️ **設計の自己整合性の検査であり、合否条件ではない**（要件 9.6）。

    採寸値を `delta_mm` 動かすと、取り付け部の内径も**ちょうど同じだけ**動く。

    ⚠️ 1µm 刻みを含めるのは、実装が採寸値を丸め・量子化・整数化していないことを
    見るためである。既存の
    `test_catch_shapes.py::test_inner_diameter_follows_a_changed_measurement` は
    225→230 の 5mm 差だけを見ており、0.1mm 単位へ丸める実装を通してしまう。採寸
    （要件 6.5）はノギスの読みであり、丸めれば取り付けの嵌め合いが変わる。
    """
    base_top_mm = 200.0

    def bore(top_mm: float) -> float:
        return rim_geometry(
            _params(
                opening_inner_diameter_mm=150.0,
                top_outer_diameter_mm=top_mm,
                fit_clearance_mm=1.0,
                flange_width_mm=10.0,
            )
        ).inner_diameter_mm

    moved = bore(base_top_mm + delta_mm) - bore(base_top_mm)
    assert moved == pytest.approx(delta_mm, rel=1e-9)
    assert moved > 0.0


def test_the_mount_bore_is_strictly_increasing_in_the_measurement() -> None:
    """⚠️ **設計の自己整合性の検査であり、合否条件ではない**（要件 9.6）。

    採寸値の並びに対し取り付け部の内径が**狭義単調増加**する（単射である）。

    ⚠️ 量子化された実装は、隣り合う採寸値を同じ内径へ潰す。単調性だけを見る検査
    （`sorted(values) == values`）は等しい値を許すため、この潰れを検出できない。
    """
    tops_mm = [200.0 + step * 0.05 for step in range(200)]
    bores = [
        rim_geometry(
            _params(
                opening_inner_diameter_mm=150.0,
                top_outer_diameter_mm=top_mm,
                fit_clearance_mm=1.0,
                flange_width_mm=10.0,
            )
        ).inner_diameter_mm
        for top_mm in tops_mm
    ]
    assert len(set(bores)) == len(bores)
    assert all(later > earlier for earlier, later in zip(bores, bores[1:]))


# ---------------------------------------------------------------------------
# 不変条件 3: 各セグメントの外接箱が造形可能寸法に収まる（要件 8.3 / 2.3）
# ---------------------------------------------------------------------------


def test_the_derived_split_never_leaves_the_build_plane_over_the_sweep() -> None:
    """⚠️ **設計の自己整合性の検査であり、合否条件ではない**（要件 9.6）。

    `rim_geometry` が値を返したなら、その分割数の扇形は造形面（XY）に必ず収まる
    （要件 8.3「外形が造形可能寸法を超える場合は複数の断片へ分割した形で設計する」/
    `constraints.required_segment_count` の Postconditions）。

    ⚠️ **高さは `segment_height_mm` を含む実高さで評価する**（`segment_envelope`）。
    既存の `test_catch_shapes.py::test_derived_segments_fit_the_build_volume` は
    `rim.height_mm`（取り付け部の高さ）を `sector_envelope` へ直接渡しており、肉厚と
    フランジの立ち上がりを含まない。tasks.md タスク 3.1(a) が言うとおり Z 軸は分割数
    の導出では検査されず、**実高さを渡せるのは `segment_envelope` が初めて**である。

    ⚠️ **Z 軸の違反は許す**（`rim_geometry` の責務ではない）。ただしその場合は
    `build_segments` が拒否しなければならない——これも併せて固定する。⚠️ この拒否は
    形状ライブラリの遅延 import より**前**に起きるため、CAD 非導入の環境でも観測
    できる（tasks.md タスク 3.2(g)）。
    """
    fitting = 0
    z_violating = 0
    for index, (opening_mm, top_mm, clearance_mm, flange_mm) in enumerate(_sweep()):
        # 高さを掃引に乗せて Z の違反側も踏む（種は固定なので配分も不変）。
        rim_height_mm = 5.0 + (index % 40) * 5.0
        params = _params(
            opening_inner_diameter_mm=opening_mm,
            top_outer_diameter_mm=top_mm,
            fit_clearance_mm=clearance_mm,
            flange_width_mm=flange_mm,
            rim_height_mm=rim_height_mm,
        )
        try:
            envelope = segment_envelope(params)
        except GeometryError:
            continue
        violations = check_envelope(PART_NAMES[0], envelope, params.printing)
        axes = {violation.axis for violation in violations}
        # 造形面（XY）の違反は起きえない——分割数がそれを避けるために導出される。
        assert axes <= {"z"}, f"XY 軸が違反した: {violations}"
        if axes:
            with pytest.raises(GeometryError):
                build_segments(params)
            z_violating += 1
        else:
            fitting += 1
    assert fitting > 0
    assert z_violating > 0


#: CAD で実測する代表的な採寸値。⚠️ **1点あたり約 0.36 秒かかる**ため、分割数の
#: 異なる3例（1 / 3 / 5 分割、計9点）に絞る。出荷値の φ220（5分割）だけを見ていた
#: 既存検査に対し、「採寸値が変わっても収まる」側を足すのが本節の役割。
_MEASURED_BBOX_CASES: tuple[tuple[str, dict[str, float]], ...] = (
    (
        "undivided-small-bin",
        {
            "opening_inner_diameter_mm": 150.0,
            "top_outer_diameter_mm": 152.0,
            "fit_clearance_mm": 0.2,
            "flange_width_mm": 11.0,
        },
    ),
    (
        "three-way-split",
        {
            "opening_inner_diameter_mm": 150.0,
            "top_outer_diameter_mm": 152.0,
            "fit_clearance_mm": 1.0,
            "flange_width_mm": 22.0,
        },
    ),
    (
        "purchased-bin-class",
        {
            "opening_inner_diameter_mm": 210.0,
            "top_outer_diameter_mm": 214.0,
            "fit_clearance_mm": 1.0,
            "flange_width_mm": 30.0,
        },
    ),
)


@requires_cad
@pytest.mark.parametrize(
    ("label", "overrides"),
    _MEASURED_BBOX_CASES,
    ids=[case[0] for case in _MEASURED_BBOX_CASES],
)
def test_every_built_segment_fits_the_build_volume_at_other_measurements(
    label: str, overrides: dict[str, float]
) -> None:
    """⚠️ **設計の自己整合性の検査であり、合否条件ではない**（要件 9.6）。

    ⚠️ **解析値ではなく実際に構築したソリッドの外接箱**で造形可能寸法を検査する
    （要件 8.3 / 2.3）。既存の
    `test_catch_shapes.py::test_every_built_segment_fits_the_build_volume_by_its_actual_bounding_box`
    は出荷値の φ220（5分割）**1点だけ**を見ている。採寸はタスク 5.2 で差し替わるの
    だから、収まることが**採寸値の帯**にわたって成り立たなければ意味が無い。

    ⚠️ 判定基準は 180×180×180 そのものである——`check_envelope` は
    `segment_margin_mm` を差し引かない（tasks.md タスク 2.1(c)）。
    """
    params = _params(**overrides)  # type: ignore[arg-type]
    segments = _cached_build(label, params)
    assert len(segments) == rim_geometry(params).segment_count
    analytic = segment_envelope(params)
    for segment in segments:
        size = segment.solid.bounding_box().size  # type: ignore[attr-defined]
        built = (float(size.X), float(size.Y), float(size.Z))
        assert check_envelope(segment.name, analytic, params.printing) == ()
        assert built[0] <= params.printing.build_x_mm
        assert built[1] <= params.printing.build_y_mm
        assert built[2] <= params.printing.build_z_mm
        # 解析的な外接箱は実形状の上界である（保守側に倒れている）。許容差 1e-3mm は
        # OCCT の外接箱が持つ数値誤差の吸収であり、判定の緩和ではない。
        assert built[0] <= analytic.x_mm + 1e-3
        assert built[1] <= analytic.y_mm + 1e-3
        assert built[2] <= analytic.z_mm + 1e-3


# ---------------------------------------------------------------------------
# 不変条件 4: 後付け用の締結座はリング全体で指定数（要件 9.7 / 決定4）
# ---------------------------------------------------------------------------


@requires_cad
@pytest.mark.parametrize("fastener_count", [1, 7])
def test_the_retrofit_seats_are_a_ring_total_spread_as_evenly_as_possible(
    fastener_count: int,
) -> None:
    """⚠️ **設計の自己整合性の検査であり、合否条件ではない**（要件 9.6）。

    座はリング全体で `retrofit_fastener_count` 箇所であり（design.md 決定4 /
    要件 9.7）、分割数で割り切れない数でも**セグメント間の差は 1 を超えない**。

    ⚠️ **この配分の性質はどこにも固定されていなかった。** 既存検査は合計 6 と合計 10
    を見るが、tasks.md タスク 3.2(b) が記録する配分 `[1,1,2,1,1]` は「同一形状に
    ならない」根拠として文章にあるだけである。座を先頭のセグメントへまとめて置く
    実装でも合計は一致してしまい、⚠️ **偏った配分は後付け部品の取り付け剛性を一方向
    へ寄せる**。

    ⚠️ 座 1 箇所（要件 9.7 が許す最小）では**座を持たないセグメントが生じる**。
    セグメントごとに最低1つ置く実装はここで落ちる（合計が分割数になる）。
    """
    params = _params(retrofit_fastener_count=fastener_count)
    segments = _cached_build(f"retrofit-{fastener_count}", params)
    counts = [segment.retrofit_seat_count for segment in segments]

    assert sum(counts) == fastener_count == params.retention.retrofit_fastener_count
    assert max(counts) - min(counts) <= 1
    # 等配分の下限・上限（座は等角に置かれ、セクタも等角である）。
    segment_count = len(segments)
    assert min(counts) == fastener_count // segment_count
    assert max(counts) == math.ceil(fastener_count / segment_count)


# ---------------------------------------------------------------------------
# 不変条件 5: 決定2 / 決定3 は「未設定」ではなく「表現不可能」である（要件 9.4）
#
# ⚠️ 既存の `test_catch_params.py::test_added_depth_must_be_zero` /
# `test_bottom_modification_must_be_none` は **1つの悪い値**（5.0 / "cut"）を拒否
# することを見る。本節が足すのは「他の道が無い」側——広い候補集合、しきい値をすり
# 抜けうる値（`1e-12` / `nan`）、綴りの揺れ、そして**既に妥当な集約からの
# `dataclasses.replace`**。決定は既定値として置かれているだけでなく、構築のどの
# 経路からも覆せない。
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "added_depth_mm",
    [5.0, 0.001, -0.001, 1e-12, -3.0, 244.0, float("inf"), float("nan"), 1],
)
def test_a_rim_that_adds_depth_cannot_be_represented(added_depth_mm: float) -> None:
    """⚠️ **設計の自己整合性の検査であり、合否条件ではない**（要件 9.6）。

    決定2「受け口は深さを足さない（`added_depth_mm = 0`）」を、**どの値からも
    覆せない**ことで固定する（要件 9.4 / design.md「受け口形状の決定」決定2）。

    ⚠️ `1e-12` や `nan` を含めるのは、`!= 0.0` の検査を「小さければ許す」「非数は
    素通し」へ緩めた実装を落とすためである。決定2 の根拠は「深さを足すと重心が
    上がり転倒余裕を削る」という**未実測の判断**であり（要件 9.6）、だからこそ黙って
    覆るのではなく明示的に覆されねばならない。
    """
    with pytest.raises(ParameterError) as excinfo:
        RetentionParams(
            retrofit_fastener_count=6,
            liner_flat_min_diameter_mm=140.0,
            added_depth_mm=added_depth_mm,
        )
    assert "added_depth_mm" in str(excinfo.value)


@pytest.mark.parametrize("added_depth_mm", [0.0, -0.0, 0])
def test_only_a_zero_depth_is_accepted(added_depth_mm: float) -> None:
    """⚠️ **設計の自己整合性の検査であり、合否条件ではない**（要件 9.6）。

    受理される値が「0 と等しい値だけ」であることの対側。⚠️ `-0.0` を拒否する実装は、
    書式の差（JSON の `-0.0`）を値の差として扱うことになる
    （`test_catch_config.py::test_digest_treats_negative_zero_as_zero` と同じ立場）。
    """
    retention = RetentionParams(
        retrofit_fastener_count=6,
        liner_flat_min_diameter_mm=140.0,
        added_depth_mm=added_depth_mm,
    )
    assert retention.added_depth_mm == 0.0


@pytest.mark.parametrize(
    "bottom_modification",
    ["cut", "None", "NONE", "none ", " none", "", "hole", "cutout", "no"],
)
def test_a_bottom_modification_cannot_be_represented(bottom_modification: str) -> None:
    """⚠️ **設計の自己整合性の検査であり、合否条件ではない**（要件 9.6）。

    決定3「底に加工を行わない（`bottom_modification = "none"`）」を、**綴りの揺れを
    含めて**固定する（要件 9.4 / design.md 決定3）。

    ⚠️ `"None"` / `"NONE"` / 前後の空白は、設定ファイルの手書きで最も起きやすい
    揺れである。大小同一視や `strip()` を足した実装は「底に加工しない」の綴りを
    増やすことになり、単一の正が2つになる。決定3 の根拠には ⚠️ **不可逆な加工で
    ある**ことが含まれる——覆すなら明示的に覆すべきである。
    """
    with pytest.raises(ParameterError) as excinfo:
        RetentionParams(
            retrofit_fastener_count=6,
            liner_flat_min_diameter_mm=140.0,
            bottom_modification=bottom_modification,
        )
    assert "bottom_modification" in str(excinfo.value)


def test_the_retention_decisions_survive_dataclasses_replace() -> None:
    """⚠️ **設計の自己整合性の検査であり、合否条件ではない**（要件 9.6）。

    **既に妥当な集約からの派生**でも決定2 / 決定3 は覆せない（要件 9.4）。

    ⚠️ 構築時検証だけを見るテストは、`dataclasses.replace` のように「検証済みの
    インスタンスから作る」経路が `__post_init__` を通らない設計に退行したときに
    沈黙する。決定を型で表す（design.md「Params」Invariants）とは**すべての構築
    経路が塞がっている**ことであって、素朴な `__init__` だけが塞がっていることでは
    ない。
    """
    valid = _params().retention
    assert valid.added_depth_mm == 0.0
    assert valid.bottom_modification == "none"

    with pytest.raises(ParameterError):
        dataclasses.replace(valid, added_depth_mm=12.0)
    with pytest.raises(ParameterError):
        dataclasses.replace(valid, bottom_modification="cut")

    # 決定に触れない差し替えは通る（塞いだのは決定だけであること）。
    widened = dataclasses.replace(valid, retrofit_fastener_count=8)
    assert widened.retrofit_fastener_count == 8
    assert widened.added_depth_mm == 0.0
    assert widened.bottom_modification == "none"


@pytest.mark.parametrize("fastener_count", [0, -1, -6])
def test_a_rim_without_a_retrofit_seat_cannot_be_represented(
    fastener_count: int,
) -> None:
    """⚠️ **設計の自己整合性の検査であり、合否条件ではない**（要件 9.6）。

    要件 9.7「既存の受け口を作り直さずに取り付けられる締結箇所を備える」は、座が
    **1 箇所以上**あることを要求する。⚠️ 決定4 が内向きリップを見送った代償が座で
    あり、座を 0 にできる型は決定4 の帰結を無効にする。
    """
    with pytest.raises(ParameterError) as excinfo:
        RetentionParams(
            retrofit_fastener_count=fastener_count,
            liner_flat_min_diameter_mm=140.0,
        )
    assert "retrofit_fastener_count" in str(excinfo.value)


def test_the_liner_plane_constraint_is_carried_alongside_the_decisions() -> None:
    """⚠️ **設計の自己整合性の検査であり、合否条件ではない**（要件 9.6）。

    決定3 の帰結として、底面に残す平面の最小径が**設計上の制約として保持される**
    （要件 9.4）。⚠️ 緩衝材の材質選定と調達は本 Spec の決着対象ではない（要件 9.5 /
    決定5）——保持しているのは「後から貼れる平面が残る」という制約だけであり、
    ⚠️ **この値が合否条件でないことは要件 9.6 の対象そのもの**である（OQ-10 は M3 の
    実投擲後に判断する）。

    ⚠️ **「実物の底がこの径を満たすか」をここで判定しない。** それは採寸（タスク
    5.2）と選定（5.1）の担当であり、本ファイルの合成値に対して主張できることでは
    ない。ここで固定するのは、制約が**既定値を持たない必須の正値として運ばれる**
    ——つまり黙って 0 や欠損へ退化しない——ことだけである。
    """
    params = _params()
    assert params.retention.liner_flat_min_diameter_mm > 0.0
    # ⚠️ 決定2 / 決定3 と違い、この項目には既定値が無い（記録すべき制約であって
    # 決定値ではないため。`test_catch_params.py::test_retention_decisions_are_the_only_defaults`
    # が既定値の側を固定している）。省略も 0 も通らないことをここで押さえる。
    with pytest.raises(TypeError):
        RetentionParams(retrofit_fastener_count=6)  # type: ignore[call-arg]
    for bad_value in (0.0, -1.0, float("nan"), float("inf")):
        with pytest.raises(ParameterError) as excinfo:
            RetentionParams(
                retrofit_fastener_count=6, liner_flat_min_diameter_mm=bad_value
            )
        assert "liner_flat_min_diameter_mm" in str(excinfo.value)
