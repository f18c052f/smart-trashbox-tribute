"""OQ-05 の判断材料（タスク 6.2、要件 10.1-10.5）。

本ファイルが固定するのは**判断ではなく材料**である。OQ-05（NFR-7 の目標成功率と
試行回数 N）は「M1・M2 実測後」に決めると `open-questions.md` が定めており、
M1 単独では移動体の性能が入らないため成功率を決められない。したがって次の4点を
特に厚く固定する。

1. **判定値が常に「材料のみ」である**（要件 10.4）。結論を出す形にした実装は
   ここで落ちる。
2. **割合が「予測側から見た成功率の上限」であって、キャッチ成功率そのものでは
   ない旨が出力に残る**（要件 10.2）。この一文が消えると、移動体性能を無視した
   期待値が独り歩きする。
3. **許容窓が対象物の寸法に依存し、最終スコープが未決である旨が出力に残る**
   （要件 10.5）。
4. **判定規則の説明文が結果に埋め込まれ、渡した設定から組まれている**
   （タスク 6.1 の教訓: テストが渡す設定値を実装の既定値と一致させると、
   「設定を無視して既定値を記録する」実装と区別が付かない）。

期待値はすべて**テスト局所のリテラル**から組む。実装の定数を import して自分
自身と比べる検査は置かない（タスク 4.5 の教訓）。必要試行回数の期待値も、式を
書き写すのではなく**整数のリテラル**で置いてある——式を写すと、実装と同じ
取り違えをテストも一緒に起こす。
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from m1_validation.config import M1Settings, Oq05Config
from m1_validation.errors import M1ConfigError
from m1_validation.judgement.oq05 import (
    Oq05Result,
    oq05_criterion,
    oq05_material,
)
from m1_validation.layout import LAYOUT_FORMAT_VERSION, ThrowLayout
from m1_validation.metrics.aggregate import (
    ITEM_HIT_ERROR_FINAL_MM,
    ITEM_KEYS,
    Distribution,
    ThrowAggregate,
)

# ---------------------------------------------------------------------------
# テスト局所のリテラル（実装の定数を参照しない）
# ---------------------------------------------------------------------------

#: 判断の種類。`Judgement.question` に入る値。
QUESTION = "OQ-05"

#: 判定値。**常にこの1値**であり、OQ-05 を決着させない（要件 10.4）。
MATERIAL_ONLY = "material_only"

# --- レイアウト（許容窓は開口半径 − 対象半径。**67.5 mm を使わない**）--------
#
# `docs/requirements.md` NFR-5 の導出値（開口 φ200・対象 φ65 → 67.5 mm）を
# そのままテストへ持ち込むと、**レイアウトを無視して 67.5 を焼き付けた実装**と
# 区別が付かない。3つの寸法をすべて別の値にしてある。

APERTURE_MM = 300.0
OBJECT_MM = 80.0
#: 300/2 − 80/2 = 110.0。
WINDOW_MM = 110.0

#: 第2のレイアウト（240/2 − 40/2 = 100.0）。窓が設定から来ていることの対照。
APERTURE2_MM = 240.0
OBJECT2_MM = 40.0
WINDOW2_MM = 100.0

# --- 設定（**既定値と重ならない値**にする。タスク 6.1 の教訓）---------------

#: 第1の設定点。信頼水準も区間幅も既定値（0.95 / 0.2・0.1・0.05）と重ならない。
LEVEL_1 = 0.8
WIDTHS_1: tuple[float, ...] = (0.3, 0.12)

#: 第2の設定点。
LEVEL_2 = 0.9
WIDTHS_2: tuple[float, ...] = (0.25,)

#: 既定値。**期待値としては使わない**——「結果に現れてはならない値」として
#: 否定照合にだけ使う。
DEFAULT_LEVEL_TEXT = "0.95"
DEFAULT_CONFIDENCE_LEVEL = 0.95
DEFAULT_WIDTH_KEYS = ("width_0.2", "width_0.1", "width_0.05")
#: 既定の設定点で p = 0.75 のときの必要試行回数（禁止値）。
DEFAULT_REQUIRED_TRIALS = (73, 289, 1153)
#: NFR-5 の導出値。**レイアウトから来ていない窓**として禁止値に使う。
CANONICAL_WINDOW_TEXT = "67.5"

# --- 誤差ベクトル群 --------------------------------------------------------
#
# **等方にしない。** x 成分だけ・y 成分だけで窓を判定する実装を落とすため、
# 「どちらの成分も窓以下だがノルムは窓を超える」ベクトルを必ず入れる
# （タスク 5.1 の教訓: 等方なフィクスチャは成分の取り違えを一切捕まえない）。

#: ノルム 50。窓の内側。
V_IN_SMALL = (30.0, -40.0)
#: ノルム **ちょうど 110.0**（66² + 88² = 110²）。境界の包含性を見る。
V_ON_WINDOW = (66.0, 88.0)
#: ノルム 25。
V_IN_TINY = (-20.0, 15.0)
#: ノルム 105（y 成分だけが大きい）。
V_IN_Y = (0.0, -105.0)
#: ノルム 75（72² + 21² = 75²）。
V_IN_MIXED = (72.0, -21.0)
#: ノルム 75（45² + 60² = 75²）。
V_IN_MIXED2 = (-45.0, 60.0)
#: ノルム 127.28…。**x も y も 110 以下**なので、片成分で判定する実装では
#: 内側になってしまう。ノルムで判定していることの決定的な対照である。
V_OUT_DIAGONAL = (90.0, 90.0)
#: ノルム 151.3…（x 成分が大きい）。
V_OUT_X = (-150.0, 20.0)

#: 8件中6件が窓の内側 → 割合 0.75。
VECTORS_075: tuple[tuple[float, float], ...] = (
    V_IN_SMALL,
    V_ON_WINDOW,
    V_IN_TINY,
    V_IN_Y,
    V_IN_MIXED,
    V_IN_MIXED2,
    V_OUT_DIAGONAL,
    V_OUT_X,
)
RATIO_075 = 0.75
WITHIN_COUNT_075 = 6
EVALUATED_075 = 8

#: 必要試行回数（p = 0.75、信頼水準 0.8、全幅 0.3 / 0.12）。
#:
#: **整数のリテラルで置く。** 式（n = 4 z² p (1 − p) / W²）をテストへ書き写すと、
#: 実装が係数 4 を落としても、ばらつきを 1 乗にしても、幅の 2 乗を落としても、
#: テストが同じ取り違えを起こして通ってしまう。
#: 参考: 4 · 1.28155…² · 0.1875 / 0.3² = 13.686… → 切り上げて 14。
#:       4 · 1.28155…² · 0.1875 / 0.12² = 85.540… → 切り上げて 86。
REQUIRED_1 = {"width_0.3": 14, "width_0.12": 86}
#: 同じ p・第2の設定点（信頼水準 0.9、全幅 0.25）: 32.466… → 33。
REQUIRED_2 = {"width_0.25": 33}

#: 切り捨てた場合の値。**禁止値**として使う（13.686… の切り捨ては 13）。
FLOOR_1 = {"width_0.3": 13, "width_0.12": 85}


# ---------------------------------------------------------------------------
# 小道具
# ---------------------------------------------------------------------------


def layout(
    *,
    aperture_diameter_mm: float = APERTURE_MM,
    object_diameter_mm: float = OBJECT_MM,
) -> ThrowLayout:
    return ThrowLayout(
        layout_id="layout-oq05-test",
        release_position_world_mm=(-1700.0, -50.0, 1690.0),
        release_height_mm=1690.0,
        throw_direction_deg=0.0,
        standby_position_world_mm=(1000.0, 1000.0),
        object_diameter_mm=object_diameter_mm,
        aperture_diameter_mm=aperture_diameter_mm,
        camera_position_world_mm=(0.0, -2500.0, 1200.0),
        notes="テスト用の仮値（確定ではない）",
    )


def settings(
    *,
    confidence_level: float = LEVEL_1,
    interval_widths: Sequence[float] = WIDTHS_1,
    aperture_diameter_mm: float = APERTURE_MM,
    object_diameter_mm: float = OBJECT_MM,
) -> M1Settings:
    return M1Settings(
        layout=layout(
            aperture_diameter_mm=aperture_diameter_mm,
            object_diameter_mm=object_diameter_mm,
        ),
        oq05=Oq05Config(
            confidence_level=confidence_level,
            interval_widths=tuple(interval_widths),
        ),
    )


def dist(median: float | None) -> Distribution:
    if median is None:
        return Distribution(
            count=0,
            median=None,
            p95=None,
            iqr=None,
            minimum=None,
            maximum=None,
            missing=8,
        )
    return Distribution(
        count=8,
        median=median,
        p95=median * 1.4,
        iqr=median * 0.3,
        minimum=median * 0.7,
        maximum=median * 1.6,
        missing=0,
    )


#: 最終予測の落下地点誤差の代表値（証跡に載る）。
FINAL_ERROR_MEDIAN_MM = 75.0
FINAL_ERROR_P95_MM = 105.0


def items(*, final_error_median_mm: float | None = FINAL_ERROR_MEDIAN_MM):
    table = {key: dist(None) for key in ITEM_KEYS}
    table[ITEM_HIT_ERROR_FINAL_MM] = dist(final_error_median_mm)
    return table


def aggregate(
    *,
    error_vectors: Sequence[tuple[float, float]] = VECTORS_075,
    throw_count: int = 11,
    valid_throw_count: int = 9,
    provisional: bool = False,
    verified: bool = True,
    final_error_median_mm: float | None = FINAL_ERROR_MEDIAN_MM,
) -> ThrowAggregate:
    """集計1件。

    **`throw_count` / `valid_throw_count` / `len(error_vectors)` をすべて別の
    値にしてある**（11 / 9 / 8）。3つが一致していると、割合の分母を「近いが別の
    軸」へ差し替えた実装が素通りする（タスク 4.6 の教訓）。
    """
    return ThrowAggregate(
        calibration_id="cal-oq05-0001",
        verified=verified,
        session_ids=("session-a", "session-b"),
        throw_count=throw_count,
        failed_throw_count=1,
        valid_throw_count=valid_throw_count,
        live_throw_count=3,
        converged_count=6,
        not_converged_count=2,
        not_measurable_count=0,
        single_prediction_throw_count=0,
        provisional=provisional,
        provisional_reasons=("insufficient_valid_throws",) if provisional else (),
        items=items(final_error_median_mm=final_error_median_mm),
        error_vectors=tuple(error_vectors),
        per_throw=(),
    )


def material(**kwargs: object) -> Oq05Result:
    """既定の集計・第1の設定点で材料を作る。"""
    return oq05_material(aggregate(**kwargs), settings=settings())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 材料1: 許容窓に収まる割合（要件 10.1）
# ---------------------------------------------------------------------------


class TestWithinWindowRatio:
    """窓に収まる割合は、**レイアウトの窓**と**誤差ベクトルのノルム**で決まる。"""

    def test_the_window_comes_from_the_layout(self) -> None:
        """窓は投擲レイアウトの寸法から導く（NFR-5 の 67.5 mm を焼き付けない）。"""
        assert material().window_mm == pytest.approx(WINDOW_MM)

    def test_a_second_layout_moves_the_window(self) -> None:
        """**寸法が変われば窓が変わる。** 定数化した実装はここで落ちる。"""
        result = oq05_material(
            aggregate(),
            settings=settings(
                aperture_diameter_mm=APERTURE2_MM, object_diameter_mm=OBJECT2_MM
            ),
        )
        assert result.window_mm == pytest.approx(WINDOW2_MM)

    def test_the_ratio_is_the_share_of_throws_inside_the_window(self) -> None:
        result = material()
        assert result.within_window_ratio == pytest.approx(RATIO_075)
        assert result.within_window_count == WITHIN_COUNT_075
        assert result.evaluated_throw_count == EVALUATED_075

    def test_the_denominator_is_the_number_of_error_vectors(self) -> None:
        """分母は**誤差ベクトルが得られた投擲数**であり、試行数でも投擲数でもない。

        3つが一致する入力しか置かないと、分母を「近いが別の軸」へ差し替えた
        実装が素通りする（タスク 4.6 の教訓）。ここでは 11 / 9 / 8 が全部違う。
        """
        result = material()
        assert result.evaluated_throw_count == 8
        assert result.within_window_ratio == pytest.approx(6 / 8)
        assert result.within_window_ratio != pytest.approx(6 / 9)
        assert result.within_window_ratio != pytest.approx(6 / 11)

    def test_a_vector_exactly_on_the_window_counts_as_inside(self) -> None:
        """窓の値**ちょうど**は「収まる」に含む（境界の包含性）。"""
        result = oq05_material(
            aggregate(error_vectors=(V_ON_WINDOW,)), settings=settings()
        )
        assert result.within_window_count == 1
        assert result.within_window_ratio == pytest.approx(1.0)

    def test_a_vector_just_outside_the_window_does_not_count(self) -> None:
        """境界のすぐ外は「収まらない」。包含性が片側だけであることを固定する。"""
        just_outside = (66.0, 88.1)
        assert math.hypot(*just_outside) > WINDOW_MM
        result = oq05_material(
            aggregate(error_vectors=(just_outside,)), settings=settings()
        )
        assert result.within_window_count == 0
        assert result.within_window_ratio == pytest.approx(0.0)

    def test_the_window_test_uses_the_vector_norm_not_one_component(self) -> None:
        """**ノルムで判定する。** 片成分で判定する実装をここで落とす。

        `(90, 90)` は x も y も窓（110 mm）以下だが、ノルムは 127.3 mm で窓の
        外である。誤差ベクトルの向きは帰属（要件 6.3）が使う情報であり、
        大きさを片成分に代表させると、斜めに外した投擲が「収まった」ことに
        なる。
        """
        assert abs(V_OUT_DIAGONAL[0]) <= WINDOW_MM
        assert abs(V_OUT_DIAGONAL[1]) <= WINDOW_MM
        assert math.hypot(*V_OUT_DIAGONAL) > WINDOW_MM
        result = oq05_material(
            aggregate(error_vectors=(V_OUT_DIAGONAL,)), settings=settings()
        )
        assert result.within_window_count == 0
        assert result.within_window_ratio == pytest.approx(0.0)

    def test_a_wider_window_admits_more_throws(self) -> None:
        """窓を広げれば割合が上がる（窓が判定に効いていることの対照）。"""
        wide = oq05_material(
            aggregate(),
            settings=settings(aperture_diameter_mm=500.0, object_diameter_mm=80.0),
        )
        assert wide.window_mm == pytest.approx(210.0)
        assert wide.within_window_ratio == pytest.approx(1.0)

    def test_no_error_vectors_leaves_the_ratio_missing(self) -> None:
        """誤差ベクトルが1件も無ければ**欠測**である。**0 で埋めない。**

        「窓に収まった投擲が1件も無かった」と「そもそも測っていない」は
        別である。0.0 を返すと、前者として読まれて材料が悪い方へ倒れる。
        """
        result = oq05_material(aggregate(error_vectors=()), settings=settings())
        assert result.within_window_ratio is None
        assert result.within_window_count == 0
        assert result.evaluated_throw_count == 0


# ---------------------------------------------------------------------------
# 材料2: 必要試行回数（要件 10.3）
# ---------------------------------------------------------------------------


class TestRequiredTrials:
    """観測されたばらつきから、所望の信頼区間幅を得るための試行回数を出す。"""

    def test_the_required_trials_match_the_fixed_numbers(self) -> None:
        """期待値は**整数のリテラル**。式を書き写すと同じ取り違えを共有する。"""
        assert dict(material().required_trials) == REQUIRED_1

    def test_the_second_settings_point_gives_different_numbers(self) -> None:
        result = oq05_material(
            aggregate(),
            settings=settings(confidence_level=LEVEL_2, interval_widths=WIDTHS_2),
        )
        assert dict(result.required_trials) == REQUIRED_2

    def test_the_result_is_rounded_up_not_down(self) -> None:
        """**切り上げる。** 切り捨てると、要求した区間幅に届かない試行数が出る。"""
        table = dict(material().required_trials)
        for key, floored in FLOOR_1.items():
            assert table[key] != floored
        assert table["width_0.3"] == FLOOR_1["width_0.3"] + 1
        assert table["width_0.12"] == FLOOR_1["width_0.12"] + 1

    def test_a_narrower_interval_needs_more_trials(self) -> None:
        table = dict(material().required_trials)
        assert table["width_0.12"] > table["width_0.3"]

    def test_a_higher_confidence_level_needs_more_trials(self) -> None:
        """信頼水準が判定に効く（係数を無視する実装をここで落とす）。"""
        low = oq05_material(
            aggregate(),
            settings=settings(confidence_level=0.7, interval_widths=(0.3,)),
        )
        high = oq05_material(
            aggregate(),
            settings=settings(confidence_level=0.99, interval_widths=(0.3,)),
        )
        assert dict(low.required_trials)["width_0.3"] < (
            dict(high.required_trials)["width_0.3"]
        )

    def test_the_observed_spread_enters_the_count(self) -> None:
        """**観測されたばらつき**が効く。p = 0.5 のほうが多くの試行を要する。

        二項比率のばらつき p(1 − p) は p = 0.5 で最大になる。割合を無視して
        最悪値だけを使う実装、あるいはばらつきを定数へ潰した実装は、
        0.75 の群と 0.5 の群で同じ数を返すのでここで落ちる。
        """
        half = oq05_material(
            aggregate(
                error_vectors=(
                    V_IN_SMALL,
                    V_IN_TINY,
                    V_OUT_DIAGONAL,
                    V_OUT_X,
                )
            ),
            settings=settings(confidence_level=LEVEL_1, interval_widths=(0.3,)),
        )
        assert half.within_window_ratio == pytest.approx(0.5)
        # 4 · 1.28155…² · 0.25 / 0.09 = 18.248… → 切り上げて 19。
        assert dict(half.required_trials)["width_0.3"] == 19
        assert dict(material().required_trials)["width_0.3"] == 14

    def test_the_keys_follow_the_configured_widths(self) -> None:
        result = oq05_material(
            aggregate(),
            settings=settings(confidence_level=LEVEL_1, interval_widths=(0.4, 0.15)),
        )
        assert tuple(result.required_trials) == ("width_0.4", "width_0.15")

    def test_the_defaults_are_not_used_when_settings_are_given(self) -> None:
        """**設定を無視して既定値を使う実装**をここで落とす（要件 13.5）。

        既定値（信頼水準 0.95、全幅 0.2 / 0.1 / 0.05）は期待値ではなく
        **禁止値**として使う。
        """
        table = dict(material().required_trials)
        for forbidden_key in DEFAULT_WIDTH_KEYS:
            assert forbidden_key not in table
        for forbidden_value in DEFAULT_REQUIRED_TRIALS:
            assert forbidden_value not in table.values()

    def test_the_confidence_level_is_reported_on_the_result(self) -> None:
        """**公開フィールドは値で固定する**（「`None` でない」では足りない）。

        `Oq05Result` は design.md「集計・判断の出力（`report-<session>.json`）」
        でそのまま JSON 化されてレポートに載る。ここが既定値へ焼き付いていると、
        レポートは「信頼水準 0.95 で見積もった」と出る一方、**同じ結果の
        criterion と evidence は 0.8 と言う**——同一の結果の中で食い違う
        （タスク 6.1 の空振り形4 の変形が、criterion から公開フィールドへ
        場所を移して再発する形であり、タスク 5.1 の `mean_hit_mm` と同型の
        空振り形7 でもある）。

        既定値はここでも**禁止値**として使う。
        """
        assert material().confidence_level == pytest.approx(LEVEL_1)
        assert material().confidence_level != pytest.approx(DEFAULT_CONFIDENCE_LEVEL)

    def test_the_confidence_level_follows_a_second_settings_point(self) -> None:
        """**第2の設定点**でも公開フィールドが設定どおりに動く。

        設定点が1つだけだと、その値を焼き付けた実装と区別できない。
        """
        result = oq05_material(
            aggregate(),
            settings=settings(confidence_level=LEVEL_2, interval_widths=WIDTHS_2),
        )
        assert result.confidence_level == pytest.approx(LEVEL_2)
        assert result.confidence_level != pytest.approx(DEFAULT_CONFIDENCE_LEVEL)

    def test_the_result_field_and_the_criterion_agree(self) -> None:
        """公開フィールドと記録された規則が**同じ設定から来ている**。

        片方だけを固定すると、レポートの見出しと規則の文面が食い違ったまま
        残る（要件 10.3 の材料がどの信頼水準のものか決められなくなる）。
        """
        result = material()
        assert result.confidence_level == pytest.approx(LEVEL_1)
        assert "z は信頼水準 0.8 に対する標準正規分布の両側分位点である。" in (
            result.judgement.criterion
        )
        assert result.judgement.evidence["confidence_level"] == pytest.approx(
            LEVEL_1
        )

    def test_the_configured_widths_are_not_the_implementation_defaults(self) -> None:
        """将来 `Oq05Config` の既定値がテスト側の値へ動いたら必ず落ちる番人。

        禁止値による否定照合はテスト局所のリテラルなので、既定値が動くと
        **古い値を見続けて黙って通る**（タスク 6.1 の申し送り）。参照解を
        実装から組むのではなく、**非一致だけ**を見る。
        """
        default = Oq05Config()
        assert LEVEL_1 != default.confidence_level
        assert LEVEL_2 != default.confidence_level
        assert WIDTHS_1 != default.interval_widths
        assert WIDTHS_2 != default.interval_widths

    def test_a_missing_ratio_leaves_the_required_trials_missing(self) -> None:
        """割合が欠測なら必要試行回数も欠測。**0 で埋めない。**"""
        result = oq05_material(aggregate(error_vectors=()), settings=settings())
        assert dict(result.required_trials) == {"width_0.3": None, "width_0.12": None}

    def test_a_degenerate_ratio_leaves_the_required_trials_missing(self) -> None:
        """割合が 1 に振り切れると観測されたばらつきが 0 になり、見積もれない。

        正規近似の分散 p(1 − p) が 0 になるので、素直に計算すると「0 回で
        よい」という明らかに誤った材料が出る。**欠測として返す**。
        """
        result = oq05_material(
            aggregate(error_vectors=(V_IN_SMALL, V_IN_TINY)), settings=settings()
        )
        assert result.within_window_ratio == pytest.approx(1.0)
        assert dict(result.required_trials) == {"width_0.3": None, "width_0.12": None}

    def test_a_zero_ratio_also_leaves_the_required_trials_missing(self) -> None:
        result = oq05_material(
            aggregate(error_vectors=(V_OUT_DIAGONAL, V_OUT_X)), settings=settings()
        )
        assert result.within_window_ratio == pytest.approx(0.0)
        assert dict(result.required_trials) == {"width_0.3": None, "width_0.12": None}


# ---------------------------------------------------------------------------
# 判定値は常に「材料のみ」（要件 10.4）★
# ---------------------------------------------------------------------------


class TestVerdictIsAlwaysMaterialOnly:
    """**OQ-05 を決着させない。** 判定値は入力によらず1値である。

    M1 単独では移動体の性能が入らないため、キャッチ成功率も試行回数 N も
    決められない。ここで結論を出す形にすると、`open-questions.md` が
    「M1・M2 実測後」と定めた順序が壊れる。
    """

    @pytest.mark.parametrize(
        "vectors",
        [
            VECTORS_075,
            (V_IN_SMALL, V_IN_TINY),
            (V_OUT_DIAGONAL, V_OUT_X),
            (),
        ],
    )
    def test_the_verdict_never_changes(
        self, vectors: tuple[tuple[float, float], ...]
    ) -> None:
        result = oq05_material(
            aggregate(error_vectors=vectors), settings=settings()
        )
        assert result.judgement.verdict == MATERIAL_ONLY

    def test_the_verdict_does_not_depend_on_the_settings(self) -> None:
        verdicts = {
            oq05_material(
                aggregate(),
                settings=settings(
                    confidence_level=level, interval_widths=widths
                ),
            ).judgement.verdict
            for level, widths in ((LEVEL_1, WIDTHS_1), (LEVEL_2, WIDTHS_2))
        }
        assert verdicts == {MATERIAL_ONLY}

    def test_the_verdict_is_not_a_conclusion_vocabulary(self) -> None:
        """OQ-27 のような可否の語彙を借りていない（結論を出す形にしない）。"""
        verdict = material().judgement.verdict
        for conclusive in ("continue", "insufficient", "deferred", "ok", "ng"):
            assert verdict != conclusive


# ---------------------------------------------------------------------------
# 注記（要件 10.2 / 10.4 / 10.5）★
# ---------------------------------------------------------------------------

UPPER_BOUND_NOTE = (
    "この割合は予測側から見た成功率の上限であって、"
    "キャッチ成功率そのものではない（要件 10.2）。"
    "予測が許容窓に収まっても、移動体が間に合わなければ、"
    "あるいは対象物が開口から跳ね出せば、キャッチは成立しない。"
)

OBJECT_SCOPE_NOTE = (
    "許容窓の値は対象物の寸法に依存し、対象物の最終スコープは未決である"
    "（要件 10.5 / 13.9）。窓が変われば割合も必要試行回数も変わる。"
)

MATERIAL_ONLY_NOTE = (
    "本結果は OQ-05（NFR-7 の目標成功率と試行回数 N）を決着させない"
    "（要件 10.4）。M1 単独では移動体の性能が入らないため成功率を決められず、"
    "判定値は常に material_only である。"
)


class TestNotes:
    """3つの注記が**全文で**出力に残る（要件 10.2 / 10.4 / 10.5）。"""

    def test_the_upper_bound_note_is_the_fixed_text(self) -> None:
        assert material().upper_bound_note == UPPER_BOUND_NOTE

    def test_the_object_scope_note_is_the_fixed_text(self) -> None:
        assert material().object_scope_note == OBJECT_SCOPE_NOTE

    def test_the_material_only_note_is_the_fixed_text(self) -> None:
        assert material().material_only_note == MATERIAL_ONLY_NOTE

    def test_the_three_notes_are_distinct(self) -> None:
        """3つが**互いに別物**である（同じ文字列へ潰す実装を落とす）。"""
        result = material()
        notes = {
            result.upper_bound_note,
            result.object_scope_note,
            result.material_only_note,
        }
        assert len(notes) == 3

    def test_the_notes_are_not_swapped(self) -> None:
        """注記の**取り違え**を落とす。3つは読み手にとって別の警告である。"""
        result = material()
        assert result.upper_bound_note != OBJECT_SCOPE_NOTE
        assert result.upper_bound_note != MATERIAL_ONLY_NOTE
        assert result.object_scope_note != UPPER_BOUND_NOTE
        assert result.object_scope_note != MATERIAL_ONLY_NOTE
        assert result.material_only_note != UPPER_BOUND_NOTE
        assert result.material_only_note != OBJECT_SCOPE_NOTE

    def test_the_notes_do_not_depend_on_the_data(self) -> None:
        """材料が欠測でも注記は消えない（**警告が消える条件を作らない**）。"""
        empty = oq05_material(aggregate(error_vectors=()), settings=settings())
        assert empty.upper_bound_note == UPPER_BOUND_NOTE
        assert empty.object_scope_note == OBJECT_SCOPE_NOTE
        assert empty.material_only_note == MATERIAL_ONLY_NOTE


# ---------------------------------------------------------------------------
# 判定規則の説明文（★ 記録される文面を固定する。要件 10.1-10.5）
# ---------------------------------------------------------------------------


def expected_criterion_sentences(
    *,
    window: str,
    aperture: str,
    obj: str,
    level: str,
    widths: str,
) -> tuple[str, ...]:
    """規則の説明文の**期待される全文**を、一文ずつテスト局所のリテラルで書く。

    ここが本ファイルで最も load-bearing な定数である。タスク 6.1 で分かった
    とおり、**覆い忘れた文は削っても取り違えても誰も気づかない領域**になる。
    したがって全文を覆い、**連結が criterion と厳密に一致する**ことまで
    固定する（順序の入れ替えも、文の追加も、これで落ちる）。

    **実装の私有定数を import して自分自身と比べてはならない**（空振り形3）。
    ここに並ぶのはすべてテスト局所のリテラルである。
    """
    return (
        # 見出し
        (
            "OQ-05（NFR-7 の目標成功率と試行回数 N）の判断材料の作り方"
            "（実測前に固定。design.md「Oq05Material」、要件 10.1-10.5）: "
        ),
        # 判定値の語彙（1値であること自体が規則の一部である）
        "判定値は「material_only」の1値だけであり、OQ-05 を決着させない（要件 10.4）。",
        # なぜ決着させないのか
        (
            "M1 単独では移動体の性能が入らないため成功率そのものを決められない。"
            "本材料は目標成功率も試行回数 N も確定させず、提示にとどめる（A-7）。"
        ),
        # 材料1: 窓の出どころ
        (
            f"【材料1】位置精度の暫定許容窓は {window} mm とする"
            f"（投擲レイアウトの開口寸法 {aperture} mm の半径から"
            f"対象物の寸法 {obj} mm の半径を引いた値。要件 10.1）。"
        ),
        # 材料1: 割合の作り方（ノルム・分母・境界の包含性）
        (
            "窓に収まる割合は、投擲ごとの最終予測の落下地点誤差ベクトルについて、"
            "水平2成分の大きさ（ノルム）が窓以下である投擲の数を、"
            "誤差ベクトルが得られた投擲の数で割った値とする"
            "（片成分ではなくノルムで判定し、窓の値ちょうどは「収まる」に含む）。"
        ),
        # 材料1 の読み方（**ここが消えると期待値が独り歩きする**）
        (
            "【材料1の読み方】当該割合は予測側から見た成功率の上限であって、"
            "キャッチ成功率そのものではない（要件 10.2）。"
        ),
        (
            "予測が窓に収まっても、移動体が間に合わなければ、"
            "あるいは対象物が開口から跳ね出せば、キャッチは成立しない。"
        ),
        # 材料2: 式（切り上げ）
        (
            "【材料2】所望の信頼区間幅を得るために必要な試行回数は、"
            "二項比率の正規近似による信頼区間の全幅 W から "
            "n = 4 z^2 p (1 - p) / W^2 として求め、小数点以下を切り上げる"
            "（要件 10.3）。"
        ),
        # 材料2: 記号の定義（**ばらつきがどこに入るか**）
        (
            "ここで p は材料1 の割合（観測されたばらつきは p (1 - p) として入る）、"
            f"z は信頼水準 {level} に対する標準正規分布の両側分位点である。"
        ),
        # 材料2: 幅の定義（全幅であって片側ではない）
        (
            f"求める信頼区間幅は {widths} であり、"
            "いずれも割合の全幅であって片側の幅ではない。"
        ),
        # 欠測の扱い（0 で埋めない）
        (
            "材料1 の割合が得られない場合、および割合が 0 または 1 に振り切れて"
            "観測されたばらつきが 0 になる場合は、必要試行回数を欠測とし"
            "0 で埋めない。"
        ),
        # 留保（要件 10.5）
        (
            "【留保】許容窓の値は対象物の寸法に依存し、"
            "対象物の最終スコープは未決である（要件 10.5、要件 13.9）。"
            "窓が変われば割合も必要試行回数も変わる。"
        ),
        # 要件 13.7 の宣言
        (
            "ここに出ている許容窓・信頼区間幅・信頼水準は暫定の評価候補であって"
            "必須性能ではない（要件 13.7）。"
        ),
        # 要件 10.4 の宣言
        "本材料は OQ-05 を決着させず、判断材料の提示までにとどめる（要件 10.4）。",
    )


CRITERION_1 = {
    "window": "110",
    "aperture": "300",
    "obj": "80",
    "level": "0.8",
    "widths": "0.3 / 0.12",
}
CRITERION_2 = {
    "window": "100",
    "aperture": "240",
    "obj": "40",
    "level": "0.9",
    "widths": "0.25",
}


class TestCriterionText:
    """**判定規則の説明文を結果に埋め込む**（要件 10.1-10.5）。

    数字を個別に `in` で見る検査は、**書式スロットを入れ替える変異**を素通り
    させる（タスク 5.2 の実例）。スロットの数だけ**一文まるごとの肯定照合**を
    置き、**取り違えた一文が含まれないこと**も併せて固定する。
    """

    def criterion(self) -> str:
        return material().judgement.criterion

    def test_every_sentence_of_the_rule_is_present(self) -> None:
        """**全文が一文まるごと**含まれる（覆い漏れた文は削っても通る）。"""
        text = self.criterion()
        for sentence in expected_criterion_sentences(**CRITERION_1):
            assert sentence in text, sentence

    def test_the_criterion_is_exactly_the_fixed_text(self) -> None:
        """連結が criterion と**厳密に一致**する。

        一致まで固定すると、**文の削除・追加・順序の入れ替え**がすべて落ちる。
        """
        assert self.criterion() == "".join(
            expected_criterion_sentences(**CRITERION_1)
        )

    def test_the_criterion_matches_a_second_settings_point(self) -> None:
        """**別の設定点でも全文が一致する**（規則が設定から組まれている固定）。

        設定点が1つだけだと、その値を実装へ焼き付けた（あるいは既定値へ
        差し替えた）実装と区別できない。
        """
        result = oq05_material(
            aggregate(),
            settings=settings(
                confidence_level=LEVEL_2,
                interval_widths=WIDTHS_2,
                aperture_diameter_mm=APERTURE2_MM,
                object_diameter_mm=OBJECT2_MM,
            ),
        )
        assert result.judgement.criterion == "".join(
            expected_criterion_sentences(**CRITERION_2)
        )

    def test_the_criterion_builder_reflects_its_arguments(self) -> None:
        """公開の組み立て関数も、渡した値をそのまま文面へ入れる。"""
        text = oq05_criterion(
            window_mm=WINDOW2_MM,
            aperture_diameter_mm=APERTURE2_MM,
            object_diameter_mm=OBJECT2_MM,
            confidence_level=LEVEL_2,
            interval_widths=WIDTHS_2,
        )
        assert text == "".join(expected_criterion_sentences(**CRITERION_2))

    def test_the_window_slots_are_not_swapped(self) -> None:
        """窓・開口・対象寸法の**取り違え**を落とす。"""
        text = self.criterion()
        assert "位置精度の暫定許容窓は 300 mm とする" not in text
        assert "位置精度の暫定許容窓は 80 mm とする" not in text
        assert "投擲レイアウトの開口寸法 110 mm の半径から" not in text
        assert "対象物の寸法 300 mm の半径を引いた値" not in text

    def test_the_window_is_not_the_canonical_nfr5_value(self) -> None:
        """NFR-5 の導出値（67.5 mm）を焼き付けた実装をここで落とす。"""
        assert CANONICAL_WINDOW_TEXT not in self.criterion()

    def test_the_level_and_the_widths_are_not_swapped(self) -> None:
        text = self.criterion()
        assert "z は信頼水準 0.3 に対する標準正規分布の両側分位点である。" not in text
        assert "求める信頼区間幅は 0.8 であり、" not in text

    def test_the_criterion_does_not_fall_back_to_the_defaults(self) -> None:
        """記録される規則は**設定した値**であって、実装の既定値ではない。

        設定を無視して既定値を規則へ書き込む実装は、**材料そのものは設定
        どおりに出したまま**、記録だけが嘘になる。同一の `Judgement` の中で
        criterion が「信頼水準 0.95」と言い、evidence が「0.8」と言う状態で
        あり、値を見ているテストでは一切気づけない。

        ここは既定値を**期待値ではなく禁止値**として使う唯一の場所である。
        """
        text = self.criterion()
        assert (
            f"z は信頼水準 {DEFAULT_LEVEL_TEXT} に対する標準正規分布の両側分位点"
            not in text
        )
        assert "求める信頼区間幅は 0.2 / 0.1 / 0.05 であり、" not in text

    def test_the_upper_bound_sentence_is_present(self) -> None:
        """**「成功率そのものではない」旨**の一文（要件 10.2）。"""
        assert (
            "【材料1の読み方】当該割合は予測側から見た成功率の上限であって、"
            "キャッチ成功率そのものではない（要件 10.2）。" in self.criterion()
        )

    def test_the_upper_bound_sentence_is_not_inverted(self) -> None:
        """**上限とキャッチ成功率を取り違えた一文**が含まれない。

        ⚠️ これが本材料で最も重い取り違えである。記録される規則が
        「当該割合はキャッチ成功率そのものである」と書かれると、
        移動体性能を無視した期待値がそのまま NFR-7 の目標として置かれる。
        """
        text = self.criterion()
        assert (
            "当該割合はキャッチ成功率そのものであって、"
            "予測側から見た成功率の上限ではない" not in text
        )
        assert "当該割合はキャッチ成功率そのものである" not in text

    def test_the_material_only_sentences_are_present(self) -> None:
        """**OQ-05 を決着させない**旨（要件 10.4）が2箇所に残る。"""
        text = self.criterion()
        assert (
            "判定値は「material_only」の1値だけであり、"
            "OQ-05 を決着させない（要件 10.4）。" in text
        )
        assert (
            "本材料は OQ-05 を決着させず、判断材料の提示までにとどめる（要件 10.4）。"
            in text
        )

    def test_the_criterion_does_not_claim_to_settle_oq05(self) -> None:
        text = self.criterion()
        assert "OQ-05 を決着させる" not in text
        assert "本材料は OQ-05 を決着させ、" not in text

    def test_the_object_scope_sentence_is_present(self) -> None:
        """**対象物の寸法に依存し最終スコープが未決**である旨（要件 10.5）。"""
        assert (
            "【留保】許容窓の値は対象物の寸法に依存し、"
            "対象物の最終スコープは未決である（要件 10.5、要件 13.9）。"
            in self.criterion()
        )

    def test_the_norm_rule_is_not_replaced_by_a_component_rule(self) -> None:
        """窓の判定を**片成分**と書き換えた一文が含まれない。"""
        text = self.criterion()
        assert "水平2成分のうち片方の成分が窓以下である投擲の数" not in text
        assert "ノルムではなく片成分で判定し" not in text

    def test_the_boundary_rule_is_not_inverted(self) -> None:
        assert "窓の値ちょうどは「収まる」に含まない" not in self.criterion()

    def test_the_rounding_rule_is_not_inverted(self) -> None:
        assert "小数点以下を切り捨てる" not in self.criterion()

    def test_the_missing_rule_is_not_inverted(self) -> None:
        assert "必要試行回数を 0 とする" not in self.criterion()
        assert "0 で埋める。" not in self.criterion()

    def test_the_criterion_does_not_change_with_the_data(self) -> None:
        """**結果に合わせて動く規則は規則ではない。** 材料の中身によらず同一。"""
        texts = {
            oq05_material(
                aggregate(error_vectors=vectors), settings=settings()
            ).judgement.criterion
            for vectors in (
                VECTORS_075,
                (V_IN_SMALL,),
                (V_OUT_X,),
                (),
            )
        }
        assert len(texts) == 1


# ---------------------------------------------------------------------------
# 判断の共通の形・証跡
# ---------------------------------------------------------------------------


class TestJudgementShape:
    """材料も `Judgement` の共通の形に載る（要件 10.4）。"""

    def test_the_question_is_oq05(self) -> None:
        assert material().judgement.question == QUESTION

    def test_the_recorded_rule_and_the_evidence_agree(self) -> None:
        """**記録した規則と、材料に使った値が同じ設定から来ている。**

        criterion（規則）と evidence（根拠の数値）が同一の `Judgement` の中で
        食い違うと、どちらが本当に適用されたのかを後から決められない。
        """
        result = material()
        evidence = result.judgement.evidence
        assert evidence["window_mm"] == pytest.approx(WINDOW_MM)
        assert evidence["aperture_diameter_mm"] == pytest.approx(APERTURE_MM)
        assert evidence["object_diameter_mm"] == pytest.approx(OBJECT_MM)
        assert evidence["confidence_level"] == pytest.approx(LEVEL_1)
        assert evidence["interval_widths"] == [0.3, 0.12]

        text = result.judgement.criterion
        assert "位置精度の暫定許容窓は 110 mm とする" in text
        assert "投擲レイアウトの開口寸法 300 mm の半径から" in text
        assert "対象物の寸法 80 mm の半径を引いた値" in text
        assert "z は信頼水準 0.8 に対する標準正規分布の両側分位点である。" in text
        assert "求める信頼区間幅は 0.3 / 0.12 であり、" in text

    def test_the_evidence_carries_the_layout_identity(self) -> None:
        """**どのレイアウトから窓が導かれたか**を後から辿れる（要件 10.5）。

        許容窓は対象物の寸法に依存し、その寸法は投擲レイアウトが持つ。
        レイアウトの識別子が証跡に残らなければ、窓が変わったときに材料の
        どれがどの寸法のものだったかを突き合わせられない。

        キャリブレーション識別子と**別の値であること**まで見るのは、
        近いが別の軸のキーへ差し替える変異を落とすためである（空振り形3）。
        """
        evidence = material().judgement.evidence
        assert evidence["layout_id"] == "layout-oq05-test"
        assert evidence["calibration_id"] == "cal-oq05-0001"
        assert evidence["layout_id"] != evidence["calibration_id"]

    def test_the_evidence_carries_the_three_notes(self) -> None:
        """3つの注記が**証跡の段にも全文で**残る（要件 10.2 / 10.4 / 10.5）。

        ⚠️ **変異は経路の各段に置く**（タスク 5.1 / 本タスクの M36 と同型）。
        公開フィールドを固定しても、`Judgement.evidence` の写しは別の経路で
        ある。design.md「集計・判断の出力」はレポートを `report-<session>.json`
        として出すと定めており、**要件 10.2 の「上限であってキャッチ成功率
        ではない」旨が最も届くべき場所がここ**である。空文字へ潰しても
        取り違えても気づけない状態にしておくと、移動体性能を無視した期待値が
        そのまま NFR-7 の目標として置かれる。
        """
        evidence = material().judgement.evidence
        assert evidence["upper_bound_note"] == UPPER_BOUND_NOTE
        assert evidence["object_scope_note"] == OBJECT_SCOPE_NOTE
        assert evidence["material_only_note"] == MATERIAL_ONLY_NOTE

    def test_the_evidence_notes_are_not_swapped(self) -> None:
        """証跡の3注記の**取り違え**を落とす（3つは別の警告である）。"""
        evidence = material().judgement.evidence
        assert evidence["upper_bound_note"] != OBJECT_SCOPE_NOTE
        assert evidence["upper_bound_note"] != MATERIAL_ONLY_NOTE
        assert evidence["object_scope_note"] != UPPER_BOUND_NOTE
        assert evidence["object_scope_note"] != MATERIAL_ONLY_NOTE
        assert evidence["material_only_note"] != UPPER_BOUND_NOTE
        assert evidence["material_only_note"] != OBJECT_SCOPE_NOTE
        assert (
            len(
                {
                    evidence["upper_bound_note"],
                    evidence["object_scope_note"],
                    evidence["material_only_note"],
                }
            )
            == 3
        )

    def test_the_public_notes_and_the_evidence_notes_agree(self) -> None:
        """**両経路が同一の文面**である（片方だけ直す変異を落とす）。"""
        result = material()
        evidence = result.judgement.evidence
        assert evidence["upper_bound_note"] == result.upper_bound_note
        assert evidence["object_scope_note"] == result.object_scope_note
        assert evidence["material_only_note"] == result.material_only_note

    def test_the_evidence_notes_do_not_depend_on_the_data(self) -> None:
        """材料が欠測でも証跡の注記は消えない（**警告が消える条件を作らない**）。"""
        evidence = oq05_material(
            aggregate(error_vectors=()), settings=settings()
        ).judgement.evidence
        assert evidence["upper_bound_note"] == UPPER_BOUND_NOTE
        assert evidence["object_scope_note"] == OBJECT_SCOPE_NOTE
        assert evidence["material_only_note"] == MATERIAL_ONLY_NOTE

    def test_the_evidence_reports_a_provisional_aggregate(self) -> None:
        """証跡の暫定印は**両方の値**で固定する（False 側だけでは足りない）。"""
        assert material().judgement.evidence["aggregate_provisional"] is False
        marked = oq05_material(aggregate(provisional=True), settings=settings())
        assert marked.judgement.evidence["aggregate_provisional"] is True

    def test_the_evidence_reports_an_unverified_calibration(self) -> None:
        """検証状態も**両方の値**で固定する（要件 2.2）。

        真側のフィクスチャしか置かないと、`True` を焼き付けた実装が素通りする
        ——**未検証のキャリブレーションで得た材料が「検証済み」として
        レポートに載る**状態であり、そこから誤差の帰属ができないという事実が
        消える（要件 2.2）。`aggregate_provisional` と同じ形で閉じる。
        """
        assert material().judgement.evidence["verified"] is True
        unverified = oq05_material(aggregate(verified=False), settings=settings())
        assert unverified.judgement.evidence["verified"] is False

    def test_the_evidence_carries_the_material(self) -> None:
        result = material()
        evidence = result.judgement.evidence
        assert evidence["within_window_ratio"] == pytest.approx(RATIO_075)
        assert evidence["within_window_count"] == WITHIN_COUNT_075
        assert evidence["evaluated_throw_count"] == EVALUATED_075
        assert evidence["required_trials"] == REQUIRED_1
        assert evidence["calibration_id"] == "cal-oq05-0001"
        assert evidence["verified"] is True
        assert evidence["throw_count"] == 11
        assert evidence["valid_throw_count"] == 9

    def test_the_evidence_carries_the_spread_of_the_final_error(self) -> None:
        """**ばらつきを併記する**（代表値だけでは材料の質が読めない）。"""
        evidence = material().judgement.evidence
        assert evidence["hit_error_norm_final_median_mm"] == pytest.approx(
            FINAL_ERROR_MEDIAN_MM
        )
        assert evidence["hit_error_norm_final_p95_mm"] == pytest.approx(
            FINAL_ERROR_P95_MM
        )

    def test_the_missing_spread_is_not_filled_with_zero(self) -> None:
        result = oq05_material(
            aggregate(final_error_median_mm=None), settings=settings()
        )
        evidence = result.judgement.evidence
        assert evidence["hit_error_norm_final_median_mm"] is None
        assert evidence["hit_error_norm_final_p95_mm"] is None

    def test_a_missing_final_error_row_is_also_reported_as_missing(self) -> None:
        """項目の**行そのもの**が無い集計でも 0 で埋めない。

        「値が1件も無かった分布」と「行が無い」は別の欠け方であり、経路も別で
        ある（前者は `Distribution.median is None`、後者は `items` にキーが
        無い）。**変異は経路の各段に置く**（タスク 5.1 の教訓）。
        """
        base = aggregate()
        stripped = replace(
            base,
            items={
                key: value
                for key, value in base.items.items()
                if key != ITEM_HIT_ERROR_FINAL_MM
            },
        )
        evidence = oq05_material(stripped, settings=settings()).judgement.evidence
        assert evidence["hit_error_norm_final_median_mm"] is None
        assert evidence["hit_error_norm_final_p95_mm"] is None

    def test_the_required_trials_are_copied(self) -> None:
        """材料は算出時の事実のまま残る（`Judgement.evidence` と同じ方針）。

        公開経路（`oq05_material()`）はマッピングをその場で組むので、
        **呼び出し側が使い回す辞書を渡せるのは公開コンストラクタだけ**である。
        コピーしていないと、レポートに出る必要試行回数が算出時と食い違い得る。
        """
        source: dict[str, int | None] = {"width_0.3": 14}
        result = Oq05Result(
            window_mm=WINDOW_MM,
            within_window_ratio=RATIO_075,
            within_window_count=WITHIN_COUNT_075,
            evaluated_throw_count=EVALUATED_075,
            confidence_level=LEVEL_1,
            required_trials=source,
            upper_bound_note=UPPER_BOUND_NOTE,
            object_scope_note=OBJECT_SCOPE_NOTE,
            material_only_note=MATERIAL_ONLY_NOTE,
            judgement=material().judgement,
        )
        source["width_0.3"] = 999
        assert dict(result.required_trials) == {"width_0.3": 14}

    def test_the_result_is_frozen(self) -> None:
        result = material()
        with pytest.raises(FrozenInstanceError):
            result.window_mm = 1.0  # type: ignore[misc]


class TestRationale:
    """理由は**相互排他**である（値が同じでも診断は別物）。"""

    R_MEASURED = (
        "誤差ベクトルが得られた 8 件の投擲のうち 6 件が暫定許容窓に収まった"
        "（材料1）。必要試行回数は信頼区間幅ごとに算出した（材料2）。"
    )
    R_NO_VECTORS = (
        "誤差ベクトルが1件も無く、暫定許容窓に収まる割合を算出できない"
        "（材料1・材料2とも欠測）。"
    )
    R_DEGENERATE = (
        "暫定許容窓に収まる割合が 0 または 1 に振り切れており、"
        "観測されたばらつきが 0 になるため必要試行回数を見積もれない"
        "（材料2 のみ欠測）。"
    )

    def test_the_measured_case_reports_the_counts(self) -> None:
        assert material().judgement.rationale == self.R_MEASURED

    def test_the_empty_case_says_the_ratio_is_missing(self) -> None:
        result = oq05_material(aggregate(error_vectors=()), settings=settings())
        assert result.judgement.rationale == self.R_NO_VECTORS

    def test_the_degenerate_case_says_the_spread_is_zero(self) -> None:
        result = oq05_material(
            aggregate(error_vectors=(V_IN_SMALL, V_IN_TINY)), settings=settings()
        )
        assert result.judgement.rationale == self.R_DEGENERATE

    def test_the_three_rationales_are_mutually_exclusive(self) -> None:
        """3つの診断は**やることが違う**（投げる／窓を見直す／そのまま使う）。"""
        seen = {
            material().judgement.rationale,
            oq05_material(
                aggregate(error_vectors=()), settings=settings()
            ).judgement.rationale,
            oq05_material(
                aggregate(error_vectors=(V_IN_SMALL, V_IN_TINY)),
                settings=settings(),
            ).judgement.rationale,
        }
        assert seen == {self.R_MEASURED, self.R_NO_VECTORS, self.R_DEGENERATE}


class TestProvisionalFlag:
    """暫定の印は**各項が単独で効く**（タスク 6.1 の教訓）。"""

    def test_a_healthy_material_is_not_provisional(self) -> None:
        """非暫定の対照。これが無いと「常に真」の実装と区別が付かない。"""
        assert material().judgement.provisional is False

    def test_a_provisional_aggregate_raises_the_flag(self) -> None:
        """第1項: 集計が暫定（試行数下限未達。要件 5.10）。"""
        result = oq05_material(aggregate(provisional=True), settings=settings())
        assert result.judgement.provisional is True

    def test_a_missing_ratio_raises_the_flag(self) -> None:
        """第2項: 材料1 が欠測（誤差ベクトルが1件も無い）。"""
        result = oq05_material(aggregate(error_vectors=()), settings=settings())
        assert result.judgement.provisional is True

    def test_the_two_terms_are_independent(self) -> None:
        """2項が**別々に**立つ（片方を落とす変異を落とす）。"""
        only_aggregate = oq05_material(
            aggregate(provisional=True), settings=settings()
        )
        only_missing = oq05_material(
            aggregate(error_vectors=()), settings=settings()
        )
        assert only_aggregate.judgement.provisional is True
        assert only_missing.judgement.provisional is True
        assert only_aggregate.within_window_ratio is not None
        assert only_missing.judgement.evidence["aggregate_provisional"] is False


class TestDeterminism:
    """同一入力・同一設定に同一の材料を返す（要件 12.4）。"""

    def test_repeated_calls_agree(self) -> None:
        first = material()
        second = material()
        assert first == second

    def test_the_input_order_of_the_widths_is_preserved(self) -> None:
        """幅の並びが設定どおりであり、実装側で並べ替えられない。"""
        result = oq05_material(
            aggregate(),
            settings=settings(interval_widths=(0.12, 0.3)),
        )
        assert tuple(result.required_trials) == ("width_0.12", "width_0.3")


# ---------------------------------------------------------------------------
# 設定（要件 13.5 / 13.6 / 13.7）
# ---------------------------------------------------------------------------


@pytest.fixture()
def layout_file(tmp_path: Path) -> Path:
    """設定解決の入力となるレイアウトファイル（`test_m1_config.py` と同じ形）。"""
    path = tmp_path / "layout.json"
    path.write_text(
        json.dumps(
            {
                "format_version": LAYOUT_FORMAT_VERSION,
                "layouts": [
                    {
                        "layout_id": "layout-oq05-file",
                        "release_position_world_mm": [-1700.0, -50.0, 1690.0],
                        "release_height_mm": 1690.0,
                        "throw_direction_deg": 0.0,
                        "standby_position_world_mm": [1000.0, 1000.0],
                        "object_diameter_mm": OBJECT_MM,
                        "aperture_diameter_mm": APERTURE_MM,
                        "camera_position_world_mm": [0.0, -2500.0, 1200.0],
                        "notes": "テスト用の仮値（確定ではない）",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _resolve(layout_file: Path, **overrides: object) -> M1Settings:
    return M1Settings.resolve(
        file=None,
        env={},
        overrides={"layout_file": str(layout_file), **overrides},
    )


class TestOq05Settings:
    """OQ-05 の設定が4段の解決順序に乗る（要件 13.5）。"""

    def test_the_defaults_are_resolved(self, layout_file: Path) -> None:
        resolved = _resolve(layout_file)
        assert resolved.oq05.confidence_level == pytest.approx(0.95)
        assert resolved.oq05.interval_widths == (0.2, 0.1, 0.05)

    def test_the_runtime_override_wins(self, layout_file: Path) -> None:
        resolved = _resolve(
            layout_file, confidence_level=LEVEL_1, interval_widths=[0.3, 0.12]
        )
        assert resolved.oq05.confidence_level == pytest.approx(LEVEL_1)
        assert resolved.oq05.interval_widths == WIDTHS_1

    def test_the_environment_layer_is_read(self, layout_file: Path) -> None:
        resolved = M1Settings.resolve(
            file=None,
            env={
                "STB_M1_CONFIDENCE_LEVEL": "0.9",
                "STB_M1_INTERVAL_WIDTHS": "0.25,0.4",
            },
            overrides={"layout_file": str(layout_file)},
        )
        assert resolved.oq05.confidence_level == pytest.approx(0.9)
        assert resolved.oq05.interval_widths == (0.25, 0.4)

    @pytest.mark.parametrize("bad", [0.0, 1.0, -0.1, 1.5])
    def test_an_out_of_range_confidence_level_is_rejected(
        self, layout_file: Path, bad: float
    ) -> None:
        """**実行開始前に**拒否する（要件 13.6）。"""
        with pytest.raises(M1ConfigError):
            _resolve(layout_file, confidence_level=bad)

    @pytest.mark.parametrize("bad", [[], [0.0], [-0.2], [1.5], [0.3, 0.0]])
    def test_an_out_of_range_interval_width_is_rejected(
        self, layout_file: Path, bad: list[float]
    ) -> None:
        with pytest.raises(M1ConfigError):
            _resolve(layout_file, interval_widths=bad)

    def test_describe_reports_the_oq05_group(self, layout_file: Path) -> None:
        described = _resolve(
            layout_file, confidence_level=LEVEL_1, interval_widths=[0.3, 0.12]
        ).describe()
        payload = described["oq05"]
        assert payload["confidence_level"] == pytest.approx(LEVEL_1)  # type: ignore[index]
        assert payload["interval_widths"] == [0.3, 0.12]  # type: ignore[index]

    def test_the_provisional_notice_names_the_new_settings(
        self, layout_file: Path
    ) -> None:
        """既定値が**必須性能と取り違えられない**ようにする（要件 13.7）。"""
        notice = str(_resolve(layout_file).describe()["provisional_notice"])
        assert "confidence_level" in notice
        assert "interval_widths" in notice
