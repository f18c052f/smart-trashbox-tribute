"""レポート（タスク 7.1、要件 2.2, 5.11, 6.9, 9.4, 10.4, 11.2）。

本ファイルが固定するのは**表示**であって、算出でも判定でもない。レポートは
既に出た値を束ねて出すだけの層であり、ここで数値を作り直したり判定をやり直し
たりしてはならない。したがって次の7点を特に厚く固定する。

1. **判定規則の説明文が要約と機械可読出力の両方に必ず残る**（本タスクの中心
   規律）。数値だけが残って根拠が消える状態を避けるためであり、**全判定
   （帰属・OQ-27・OQ-05・時間予算・計測 ON/OFF）の `criterion`** が対象で
   ある。1つでも落ちればここで落ちる。
2. **未検証キャリブレーションの警告は、真のときだけ出る**（要件 2.2）。常に
   出す実装も、常に出さない実装もここで落ちる。**真偽の両方を通す。**
3. **帰属は成分ごとの内訳として出る**（要件 6.9）。合計誤差の単一値へ畳んだ
   実装、成分の一部だけを出す実装、上流の読み分け規則を落とした実装は落ちる。
4. **OQ-05 が材料であって決着ではない旨が残る**（要件 10.4）。
5. **想定値と実測値は別の列であり、取り違えられない**（要件 5.11 / 11.2）。
   想定値のうち区間1・区間2・総飛行時間の3つは `BudgetUpdate` から**運ぶ**
   ものであって、レポートが持つ定数ではない。**フィクスチャの想定値は本
   ファイル局所のリテラル**にしてあるので、実装へ焼き付ける変異は必ず落ちる
   （タスク 6.1 の教訓「テストの設定値を実装の既定値と重ならない値にする」）。
6. **「想定側は区間3 を計上し、実測側は含まない」の一文が落ちない**
   （タスク 6.3 からの申し送り）。落とすと2列が直接比較可能に見え、楽観側へ
   倒れた読みが独り歩きする。
7. **NaN と無限大は欠測として表れる**。0 で埋めた実装、そのまま出して JSON を
   壊す実装はここで落ちる。

期待値はすべて**テスト局所のリテラル**から組む。実装の定数を import して自分
自身と比べる検査は置かない（タスク 4.5 / 6.1 の教訓）。ただし「実装の定数が
互いに異なること」だけは、取り違えを露見させるための**対の検査**として残す
（タスク 6.3 の教訓: `distinct` 検査は値の固定と対でのみ意味を持つ）。
"""

from __future__ import annotations

import dataclasses
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from m1_validation.attribution import (
    AttributionResult,
    BiasComponent,
    RangeBand,
    ScatterComponent,
)
from m1_validation.bench import (
    ConditionStats,
    OverheadReport,
    OverheadVerdict,
)
from m1_validation.errors import M1ConfigError
from m1_validation.judgement.budget import (
    BudgetRow,
    BudgetUpdate,
    budget_criterion,
)
from m1_validation.judgement.oq05 import Oq05Result
from m1_validation.judgement.oq27 import ImprovementRecord, Oq27Result
from m1_validation.metrics.aggregate import (
    ITEM_AIM_ERROR_MM,
    ITEM_CONVERGED_AT,
    ITEM_DETECT_TO_FIRST_PREDICTION_MS,
    ITEM_HIT_ERROR_FINAL_MM,
    ITEM_HIT_ERROR_FIRST_MM,
    ITEM_KEYS,
    ITEM_RELEASE_TO_DETECT_MS,
    ITEM_TIME_ERROR_FINAL_MS,
    ITEM_TIME_ERROR_FIRST_MS,
    ITEM_TOTAL_FLIGHT_MS,
    ITEM_VALID_SAMPLES,
    Distribution,
    ThrowAggregate,
    ThrowRow,
)
from m1_validation.report import (
    ASSUMED_ITEM_4,
    ASSUMED_ITEM_5,
    ASSUMED_ITEM_6,
    ASSUMED_ITEM_7,
    ASSUMED_SOURCES,
    ATTRIBUTION_READING_NOTES,
    COMPONENT_BREAKDOWN_NOTE,
    JUDGEMENT_KEYS,
    MEASUREMENT_ITEM_LABELS,
    NOTE_ITEM_5,
    NOTE_ITEM_7,
    RANGE_BAND_READING_NOTE,
    REPORT_VERSION,
    SCATTER_WITH_BIAS_NOTE,
    UNIT_NOTE_METERS_VS_MM,
    UNIT_NOTE_SECONDS_VS_MS,
    UNVERIFIED_WARNING,
    M1Report,
    build_report,
    judgement_to_dict,
    provisional_warning,
    render_summary,
    report_output_path,
    report_to_dict,
    write_report,
)
from m1_validation.types import Attribution, Judgement, Oq27Verdict

# ---------------------------------------------------------------------------
# テスト局所のリテラル
#
# **意味の違う数はフィクスチャ上でも別の値にする**（タスク 6.3 の教訓）。
# 列の取り違えは、値が一致していると露見しない。
# ---------------------------------------------------------------------------

SESSION_ID = "session-report-0007"
CALIBRATION_ID = "cal-report-0042"
THROW_COUNT = 12

#: 項目キーごとの中央値。**10 個すべて相異なる。**
ITEM_MEDIANS: Mapping[str, float] = {
    ITEM_TOTAL_FLIGHT_MS: 812.0,
    ITEM_RELEASE_TO_DETECT_MS: 63.0,
    ITEM_DETECT_TO_FIRST_PREDICTION_MS: 128.0,
    ITEM_HIT_ERROR_FIRST_MM: 141.0,
    ITEM_HIT_ERROR_FINAL_MM: 72.0,
    ITEM_TIME_ERROR_FIRST_MS: 19.0,
    ITEM_TIME_ERROR_FINAL_MS: 8.0,
    ITEM_AIM_ERROR_MM: 437.0,
    ITEM_VALID_SAMPLES: 11.0,
    ITEM_CONVERGED_AT: 6.0,
}

#: 項目キーごとの試行数。**10 個すべて相異なり、欠測数との和は `THROW_COUNT`。**
ITEM_COUNTS: Mapping[str, int] = {
    key: THROW_COUNT - index for index, key in enumerate(ITEM_KEYS)
}

# --- `BudgetUpdate` から**運ぶ**想定値・備考 -------------------------------
#
# レポートが持つ定数ではないので、実装の文面と重ならないリテラルにしてある。
# 「0.05〜0.10 s」等を焼き付ける変異はここで落ちる。

BUDGET_SEG1_ASSUMED = "＜局所リテラル: 区間1 の想定値＞"
BUDGET_SEG1_NOTE = "＜局所リテラル: 区間1 の備考＞"
BUDGET_SEG2_ASSUMED = "＜局所リテラル: 区間2 の想定値＞"
BUDGET_SEG2_NOTE = "＜局所リテラル: 区間2 の備考。終点は初回予測である＞"
BUDGET_SEG2_LABEL = "＜局所リテラル: 区間2 の見出し（初回予測への読み替え）＞"
BUDGET_SEG3_ASSUMED = "＜局所リテラル: 区間3 の想定値＞"
BUDGET_SEG3_NOTE = "＜局所リテラル: 区間3 の備考。M3 で実測する＞"
BUDGET_TOTAL_ASSUMED = "＜局所リテラル: オーバーヘッド合計の想定値＞"
BUDGET_TOTAL_NOTE = "＜局所リテラル: 合計は区間3 を含まない下側である＞"
BUDGET_TOTAL_FLIGHT_ASSUMED = "＜局所リテラル: 総飛行時間の想定値＞"
BUDGET_REMAINING_ASSUMED = "＜局所リテラル: 移動体に残された時間の想定値＞"
BUDGET_REMAINING_NOTE = "＜局所リテラル: 残り時間は区間3 を引いていない上側である＞"
BUDGET_PROVISIONAL_NOTE = "＜局所リテラル: 更新後も暫定目標値である＞"
BUDGET_COMPUTATION_NOTE = "＜局所リテラル: 値を算出するだけで文書は書き換えない＞"

# --- 判定規則の説明文（**5つとも相異なる**）--------------------------------

CRITERION_ATTRIBUTION = "＜局所リテラル: 帰属の判定規則の全文＞"
CRITERION_OQ27 = "＜局所リテラル: OQ-27 の判定規則の全文＞"
CRITERION_OQ05 = "＜局所リテラル: OQ-05 の材料の作り方の全文＞"
CRITERION_BUDGET = "＜局所リテラル: 時間予算表の更新値の算出規則の全文＞"
CRITERION_OVERHEAD = "＜局所リテラル: 計測 ON/OFF 比較の判定基準の全文＞"

QUESTION_ATTRIBUTION = "attribution"
QUESTION_OQ27 = "OQ-27"
QUESTION_OQ05 = "OQ-05"
QUESTION_BUDGET = "time-budget"
QUESTION_OVERHEAD = "measurement_overhead"

OQ05_UPPER_BOUND_NOTE = "＜局所リテラル: 予測側から見た成功率の上限である＞"
OQ05_OBJECT_SCOPE_NOTE = "＜局所リテラル: 許容窓は対象物の寸法に依存し未決である＞"
OQ05_MATERIAL_ONLY_NOTE = "＜局所リテラル: OQ-05 は決着させず材料の提示にとどめる＞"

OQ05_WINDOW_MM = 67.5

# ---------------------------------------------------------------------------
# フィクスチャ組み立て
# ---------------------------------------------------------------------------


def dist(key: str) -> Distribution:
    """項目キーごとに**すべて異なる**分布を作る。"""
    median = ITEM_MEDIANS[key]
    count = ITEM_COUNTS[key]
    return Distribution(
        count=count,
        median=median,
        p95=median * 1.5,
        iqr=median * 0.25,
        minimum=median - 5.0,
        maximum=median + 7.0,
        missing=THROW_COUNT - count,
    )


def items(*, absent: Sequence[str] = ()) -> dict[str, Distribution]:
    table = {key: dist(key) for key in ITEM_KEYS}
    for key in absent:
        del table[key]
    return table


def throw_rows() -> tuple[ThrowRow, ...]:
    return (
        ThrowRow(
            record_id="throw-0001",
            session_id=SESSION_ID,
            source="live",
            live=True,
            truth_available=True,
            error_vector_mm=(21.0, -34.0),
            values=dict.fromkeys(ITEM_KEYS, None),
        ),
    )


def aggregate(
    *,
    verified: bool = True,
    provisional: bool = False,
    reasons: Sequence[str] = (),
    absent: Sequence[str] = (),
) -> ThrowAggregate:
    return ThrowAggregate(
        calibration_id=CALIBRATION_ID,
        verified=verified,
        session_ids=(SESSION_ID,),
        throw_count=THROW_COUNT,
        failed_throw_count=2,
        valid_throw_count=9,
        live_throw_count=7,
        converged_count=5,
        not_converged_count=3,
        not_measurable_count=1,
        single_prediction_throw_count=4,
        provisional=provisional,
        provisional_reasons=tuple(reasons),
        items=items(absent=absent),
        error_vectors=((21.0, -34.0),),
        per_throw=throw_rows(),
    )


def judgement(
    *,
    question: str,
    criterion: str,
    verdict: str,
    rationale: str = "＜局所リテラル: 根拠＞",
    evidence: Mapping[str, object] | None = None,
    provisional: bool = False,
) -> Judgement:
    return Judgement(
        question=question,
        criterion=criterion,
        verdict=verdict,
        rationale=rationale,
        evidence={} if evidence is None else evidence,
        provisional=provisional,
    )


def attribution(
    *, criterion: str = CRITERION_ATTRIBUTION, evidence: Mapping[str, object] | None = None
) -> AttributionResult:
    return AttributionResult(
        bias=BiasComponent(
            vector_mm=(23.0, -41.0),
            norm_mm=47.02,
            significance_ratio=3.75,
            world_fixed_agreement_deg=12.5,
            camera_ray_agreement_deg=71.25,
            degenerate=False,
            attribution=Attribution.CALIBRATION,
        ),
        scatter=ScatterComponent(
            rms_mm=14.5,
            bootstrap_rms_mm=9.25,
            residual_median_mm=5.125,
            attribution=Attribution.OBSERVATION_NOISE,
        ),
        range_bands=(
            RangeBand(
                range_lo_mm=1000.0,
                range_hi_mm=1500.0,
                throw_count=4,
                mean_error_norm_mm=31.5,
            ),
            RangeBand(
                range_lo_mm=1500.0,
                range_hi_mm=2000.0,
                throw_count=6,
                mean_error_norm_mm=88.25,
            ),
        ),
        calibration_reference={"bias_state": "recognized", "offset_mm": 17.5},
        judgement=judgement(
            question=QUESTION_ATTRIBUTION,
            criterion=criterion,
            verdict="bias=calibration/scatter=observation_noise",
            evidence={} if evidence is None else evidence,
        ),
    )


IMPROVEMENT_APPLIED = ImprovementRecord(
    step="＜局所リテラル: 適用済みの改善項目＞",
    applied=True,
    before={"process_fps": 12.5},
    after={"process_fps": 21.75},
)

IMPROVEMENT_PENDING = ImprovementRecord(
    step="＜局所リテラル: 未適用の改善項目＞",
    applied=False,
    before={},
    after={},
)


def oq27(*, criterion: str = CRITERION_OQ27) -> Oq27Result:
    return Oq27Result(
        verdict=Oq27Verdict.CONTINUE_WITH_CONSTRAINTS,
        bottleneck_stage="capture",
        bottleneck_label="capture/frame/total_ms",
        bottleneck_p95_ms=54.25,
        end_to_end_p95_ms=163.75,
        overhead_reference_ms=191.5,
        resource_saturated=False,
        limiting_conditions=("＜局所リテラル: 律速している条件＞",),
        improvements=(IMPROVEMENT_APPLIED, IMPROVEMENT_PENDING),
        judgement=judgement(
            question=QUESTION_OQ27,
            criterion=criterion,
            verdict=str(Oq27Verdict.CONTINUE_WITH_CONSTRAINTS),
        ),
    )


def oq05(*, criterion: str = CRITERION_OQ05) -> Oq05Result:
    return Oq05Result(
        window_mm=OQ05_WINDOW_MM,
        within_window_ratio=0.625,
        within_window_count=5,
        evaluated_throw_count=8,
        confidence_level=0.85,
        required_trials={"0.20": 41, "0.10": None},
        upper_bound_note=OQ05_UPPER_BOUND_NOTE,
        object_scope_note=OQ05_OBJECT_SCOPE_NOTE,
        material_only_note=OQ05_MATERIAL_ONLY_NOTE,
        judgement=judgement(
            question=QUESTION_OQ05,
            criterion=criterion,
            verdict="material_only",
        ),
    )


def budget_rows(*, ready: bool = True) -> tuple[BudgetRow, ...]:
    return (
        BudgetRow(
            segment="1",
            label="＜局所リテラル: 区間1 の見出し＞",
            assumed=BUDGET_SEG1_ASSUMED,
            measured=dist(ITEM_RELEASE_TO_DETECT_MS) if ready else None,
            trials=ITEM_COUNTS[ITEM_RELEASE_TO_DETECT_MS] if ready else 0,
            note=BUDGET_SEG1_NOTE,
        ),
        BudgetRow(
            segment="2",
            label=BUDGET_SEG2_LABEL,
            assumed=BUDGET_SEG2_ASSUMED,
            measured=dist(ITEM_DETECT_TO_FIRST_PREDICTION_MS) if ready else None,
            trials=ITEM_COUNTS[ITEM_DETECT_TO_FIRST_PREDICTION_MS] if ready else 0,
            note=BUDGET_SEG2_NOTE,
        ),
        BudgetRow(
            segment="3",
            label="＜局所リテラル: 区間3 の見出し＞",
            assumed=BUDGET_SEG3_ASSUMED,
            measured=None,
            trials=0,
            note=BUDGET_SEG3_NOTE,
        ),
        BudgetRow(
            segment="total",
            label="＜局所リテラル: 合計の見出し＞",
            assumed=BUDGET_TOTAL_ASSUMED,
            measured=dist(ITEM_TOTAL_FLIGHT_MS) if ready else None,
            trials=3 if ready else 0,
            note=BUDGET_TOTAL_NOTE,
        ),
    )


def budget(
    *, criterion: str = CRITERION_BUDGET, ready: bool = True
) -> BudgetUpdate:
    return BudgetUpdate(
        ready=ready,
        missing_items=() if ready else ("item4:hit_error_norm_first_mm",),
        rows=budget_rows(ready=ready),
        total_flight_ms=dist(ITEM_TOTAL_FLIGHT_MS) if ready else None,
        remaining_time_ms=dist(ITEM_AIM_ERROR_MM) if ready else None,
        derived_latency_target_ms=237.5 if ready else None,
        segment3_assumed_ms=37.0,
        total_flight_assumed=BUDGET_TOTAL_FLIGHT_ASSUMED,
        remaining_time_assumed=BUDGET_REMAINING_ASSUMED,
        remaining_time_note=BUDGET_REMAINING_NOTE,
        provisional_target_note=BUDGET_PROVISIONAL_NOTE,
        computation_only_note=BUDGET_COMPUTATION_NOTE,
        judgement=judgement(
            question=QUESTION_BUDGET,
            criterion=criterion,
            verdict="updatable" if ready else "not_updatable",
        ),
    )


def overhead(*, criterion: str = CRITERION_OVERHEAD) -> OverheadReport:
    stats = tuple(
        ConditionStats(
            condition=condition,
            target="predict",
            target_label="＜局所リテラル: 予測区間＞",
            samples=samples,
            p50_ms=p50,
            p95_ms=p50 * 1.4,
            iqr_ms=p50 * 0.2,
        )
        for condition, samples, p50 in (
            ("measurement_off", 31, 4.5),
            ("measurement_on", 29, 4.75),
        )
    )
    return OverheadReport(
        criterion=criterion,
        stats=stats,
        verdicts=(
            OverheadVerdict(
                target="predict",
                target_label="＜局所リテラル: 予測区間＞",
                passed=True,
                median_delta_ms=0.25,
                baseline_iqr_ms=0.9,
                within_iqr=True,
                dropped_not_increased=True,
                on_frames_dropped=2,
                off_frames_dropped=3,
                detail="＜局所リテラル: 判定の根拠＞",
                unconditional_validity_note=None,
            ),
        ),
        raw_samples={"measurement_off": {"predict": (4.5, 4.75)}},
        segment_order=("measurement_off", "measurement_on"),
        frames_dropped={"measurement_off": 3, "measurement_on": 2},
        target_labels={"predict": "＜局所リテラル: 予測区間＞"},
        upstream_segment_note="＜局所リテラル: 上流の区間と混同させない＞",
        end_to_end_definition="＜局所リテラル: end-to-end の定義文＞",
        unconditional_validity_note=None,
        judgement=judgement(
            question=QUESTION_OVERHEAD,
            criterion=criterion,
            verdict="not_significantly_changed",
        ),
    )


def report(
    *,
    verified: bool = True,
    provisional: bool = False,
    reasons: Sequence[str] = (),
    absent: Sequence[str] = (),
    with_overhead: bool = True,
    budget_criterion_text: str = CRITERION_BUDGET,
    budget_ready: bool = True,
    attribution_evidence: Mapping[str, object] | None = None,
) -> M1Report:
    return build_report(
        session_id=SESSION_ID,
        aggregate=aggregate(
            verified=verified,
            provisional=provisional,
            reasons=reasons,
            absent=absent,
        ),
        attribution=attribution(evidence=attribution_evidence),
        oq27=oq27(),
        oq05=oq05(),
        budget=budget(criterion=budget_criterion_text, ready=budget_ready),
        overhead=overhead() if with_overhead else None,
    )


def column_of(built: M1Report, item: int, key: str) -> object:
    row = next(row for row in built.measurements if row.item == item)
    return next(column for column in row.columns if column.key == key)


def row_of(built: M1Report, item: int) -> object:
    return next(row for row in built.measurements if row.item == item)


# ---------------------------------------------------------------------------
# 1. 判定規則の説明文が両方の出力に残る（本タスクの中心規律）
# ---------------------------------------------------------------------------


class TestEveryCriterionSurvivesIntoBothOutputs:
    """**全判定の `criterion` が、要約にも機械可読出力にも残る。**

    数値だけが残って根拠が消える状態を避けるための検査である
    （design.md「Reporter」Risks、タスク 7.1 の観測可能な完了状態）。
    1つでも落とす変異、要約からだけ落とす変異、機械可読出力からだけ落とす
    変異は、すべてここで落ちる。
    """

    ALL = (
        CRITERION_ATTRIBUTION,
        CRITERION_OQ27,
        CRITERION_OQ05,
        CRITERION_BUDGET,
        CRITERION_OVERHEAD,
    )

    def test_the_summary_contains_every_criterion_in_full(self) -> None:
        summary = render_summary(report())
        for criterion in self.ALL:
            assert criterion in summary

    def test_the_machine_readable_output_contains_every_criterion_in_full(
        self,
    ) -> None:
        payload = report_to_dict(report())
        criteria = [entry["criterion"] for entry in payload["judgements"]]
        assert criteria == list(self.ALL)

    def test_the_judgement_list_covers_the_five_questions(self) -> None:
        payload = report_to_dict(report())
        questions = [entry["question"] for entry in payload["judgements"]]
        assert questions == [
            QUESTION_ATTRIBUTION,
            QUESTION_OQ27,
            QUESTION_OQ05,
            QUESTION_BUDGET,
            QUESTION_OVERHEAD,
        ]

    def test_without_the_overhead_bench_the_other_four_still_appear(self) -> None:
        """計測 ON/OFF 比較は任意入力である。**残り4件を落とさない。**"""
        built = report(with_overhead=False)
        summary = render_summary(built)
        payload = report_to_dict(built)
        assert [entry["criterion"] for entry in payload["judgements"]] == [
            CRITERION_ATTRIBUTION,
            CRITERION_OQ27,
            CRITERION_OQ05,
            CRITERION_BUDGET,
        ]
        assert CRITERION_OVERHEAD not in summary
        for criterion in self.ALL[:4]:
            assert criterion in summary
        assert payload["overhead"] is None

    def test_the_five_criteria_are_distinct_in_the_fixture(self) -> None:
        """**取り違えを露見させるための対の検査**（タスク 6.3 の教訓）。

        5つが同じ文字列だと、どれを取り違えても上の検査が通ってしまう。
        """
        assert len(set(self.ALL)) == 5

    def test_the_budget_criterion_carries_the_segment3_comparison_sentence(
        self,
    ) -> None:
        """**タスク6.3 からの申し送り。落とすと楽観側の読みが独り歩きする。**

        「§3 の想定値の側は区間3 を計上しており、実測の側は含まない」という
        一文は、想定列と実測列を並べる本レポートで**必ず一緒に出す**。
        本検査だけは実際の `budget_criterion()` の文面を通す——申し送りの
        対象がまさにこの文だからである。
        """
        real = budget_criterion(segment3_assumed_ms=37.0)
        sentence = (
            "【想定側との比較】§3 の想定値の側は、"
            "オーバーヘッド合計（0.2〜0.3 s）が区間1＋区間2＋区間3 の和であり、"
            "移動体に残された時間（0.3〜1.0 s）は区間3 を差し引いた後の値である。"
            "本 Spec の実測側は区間3 を含まないので、"
            "この2行は想定と実測をそのまま引き比べられない。"
        )
        assert sentence in real
        built = report(budget_criterion_text=real)
        assert sentence in render_summary(built)
        payload = report_to_dict(built)
        assert sentence in payload["judgements"][3]["criterion"]


# ---------------------------------------------------------------------------
# 2. 未検証キャリブレーションの警告（要件 2.2）
# ---------------------------------------------------------------------------


class TestTheUnverifiedWarning:
    """**警告は真のときだけ出る。**

    常に出す実装は「検証済みのデータまで帰属できないことにする」——検証を
    通した意味が消える。常に出さない実装は要件 2.2 そのものを落とす。
    **真偽の両方を通す**（タスク 6.2 の教訓）。
    """

    def test_an_unverified_aggregate_produces_the_warning(self) -> None:
        built = report(verified=False)
        assert UNVERIFIED_WARNING in built.warnings
        assert UNVERIFIED_WARNING in render_summary(built)
        assert UNVERIFIED_WARNING in report_to_dict(built)["warnings"]

    def test_a_verified_aggregate_produces_no_such_warning(self) -> None:
        built = report(verified=True)
        assert UNVERIFIED_WARNING not in built.warnings
        assert UNVERIFIED_WARNING not in render_summary(built)

    def test_the_warning_says_that_errors_cannot_be_attributed(self) -> None:
        """**警告の中身が「誤差の帰属ができない」旨であること**（要件 2.2）。

        文面を空にしても、別の話題に差し替えても、上の2件だけでは落ちない。
        """
        assert "帰属" in UNVERIFIED_WARNING
        assert "できない" in UNVERIFIED_WARNING
        assert UNVERIFIED_WARNING.strip() != ""

    def test_the_verified_flag_is_carried_to_both_outputs(self) -> None:
        assert report(verified=False).verified is False
        assert report(verified=True).verified is True
        assert report_to_dict(report(verified=False))["verified"] is False
        assert report_to_dict(report(verified=True))["verified"] is True

    def test_a_provisional_aggregate_produces_its_own_warning(self) -> None:
        """暫定の印は検証状態とは**別の軸**である（要件 5.10）。"""
        reasons = ("insufficient_valid_throws", "non_live_throws")
        built = report(provisional=True, reasons=reasons)
        expected = provisional_warning(reasons)
        assert expected in built.warnings
        assert expected in render_summary(built)
        for reason in reasons:
            assert reason in expected

    def test_a_non_provisional_aggregate_produces_no_provisional_warning(
        self,
    ) -> None:
        built = report(provisional=False)
        assert built.warnings == ()
        assert built.provisional is False

    def test_both_warnings_can_be_present_at_once(self) -> None:
        """2つの警告は独立に立つ。片方が他方を隠さない。"""
        reasons = ("insufficient_valid_throws",)
        built = report(verified=False, provisional=True, reasons=reasons)
        assert UNVERIFIED_WARNING in built.warnings
        assert provisional_warning(reasons) in built.warnings
        assert len(built.warnings) == 2

    def test_the_two_warnings_are_distinct(self) -> None:
        """**対の検査。** 同じ文面だと上の独立性が固定できない。"""
        assert UNVERIFIED_WARNING != provisional_warning(("x",))


# ---------------------------------------------------------------------------
# 3. 実測7項目と想定値の並置（要件 5.11 / 11.2）
# ---------------------------------------------------------------------------


class TestMeasurementsBesideTheAssumedValues:
    """**実測7項目を、対応する既存ドキュメントの想定値と並べて出す。**

    想定列と実測列の**取り違え**、想定値を実装へ焼き付ける変異、項目を落とす
    変異、単位の違いの注記を落とす変異が、ここで落ちる。
    """

    def test_there_are_exactly_seven_items_in_order(self) -> None:
        built = report()
        assert [row.item for row in built.measurements] == [1, 2, 3, 4, 5, 6, 7]

    def test_each_item_carries_the_label_from_the_source_table(self) -> None:
        built = report()
        for row in built.measurements:
            assert row.label == MEASUREMENT_ITEM_LABELS[row.item]
            assert row.label.strip() != ""

    def test_the_seven_labels_are_distinct(self) -> None:
        """**対の検査。** 全部同じ見出しに潰す変異を殺す。"""
        assert len(set(MEASUREMENT_ITEM_LABELS.values())) == 7

    def test_the_seven_assumed_sources_are_distinct(self) -> None:
        assert len(set(ASSUMED_SOURCES.values())) == 7

    def test_items_1_2_3_carry_the_assumed_values_from_the_budget_update(
        self,
    ) -> None:
        """**想定値は `BudgetUpdate` から運ぶ。再発明しない。**

        フィクスチャの想定値は本ファイル局所のリテラルなので、`0.05〜0.10 s`
        などを実装へ焼き付ける変異は必ず落ちる。
        """
        built = report()
        assert row_of(built, 1).assumed == BUDGET_TOTAL_FLIGHT_ASSUMED
        assert row_of(built, 2).assumed == BUDGET_SEG1_ASSUMED
        assert row_of(built, 3).assumed == BUDGET_SEG2_ASSUMED

    def test_items_1_2_3_do_not_take_each_others_assumed_values(self) -> None:
        """**列の取り違え専用の否定照合。**"""
        built = report()
        assert row_of(built, 2).assumed != BUDGET_SEG2_ASSUMED
        assert row_of(built, 3).assumed != BUDGET_SEG1_ASSUMED
        assert row_of(built, 1).assumed != BUDGET_SEG1_ASSUMED
        assert BUDGET_SEG3_ASSUMED not in {
            row.assumed for row in built.measurements
        }

    def test_items_4_to_7_carry_the_assumed_values_from_the_source_documents(
        self,
    ) -> None:
        built = report()
        assert row_of(built, 4).assumed == ASSUMED_ITEM_4
        assert row_of(built, 5).assumed == ASSUMED_ITEM_5
        assert row_of(built, 6).assumed == ASSUMED_ITEM_6
        assert row_of(built, 7).assumed == ASSUMED_ITEM_7
        assert len({ASSUMED_ITEM_4, ASSUMED_ITEM_5, ASSUMED_ITEM_6, ASSUMED_ITEM_7}) == 4

    def test_item_6_states_the_assumed_range_from_section_3(self) -> None:
        """§3 本文の「狙い誤差（想定 0.3〜0.8m）」を写していること。"""
        assert "0.3〜0.8" in ASSUMED_ITEM_6

    def test_item_7_states_the_three_sample_floor_from_fr1(self) -> None:
        assert "3" in ASSUMED_ITEM_7
        assert "FR-1" in ASSUMED_SOURCES[7]

    def test_the_columns_follow_the_measurement_item_mapping(self) -> None:
        built = report()
        assert [column.key for column in row_of(built, 1).columns] == [
            ITEM_TOTAL_FLIGHT_MS
        ]
        assert [column.key for column in row_of(built, 4).columns] == [
            ITEM_HIT_ERROR_FIRST_MM,
            ITEM_HIT_ERROR_FINAL_MM,
        ]
        assert [column.key for column in row_of(built, 5).columns] == [
            ITEM_TIME_ERROR_FIRST_MS,
            ITEM_TIME_ERROR_FINAL_MS,
        ]
        assert [column.key for column in row_of(built, 7).columns] == [
            ITEM_VALID_SAMPLES,
            ITEM_CONVERGED_AT,
        ]

    def test_a_column_carries_the_distribution_of_its_own_item(self) -> None:
        """**項目キーの取り違えを殺す。** 中央値は10 個すべて相異なる。"""
        built = report()
        column = column_of(built, 2, ITEM_RELEASE_TO_DETECT_MS)
        assert column.present is True
        assert column.median == 63.0
        assert column.p95 == 63.0 * 1.5
        assert column.iqr == 63.0 * 0.25
        assert column.minimum == 58.0
        assert column.maximum == 70.0
        assert column.count == ITEM_COUNTS[ITEM_RELEASE_TO_DETECT_MS]
        assert column.missing == THROW_COUNT - ITEM_COUNTS[ITEM_RELEASE_TO_DETECT_MS]

    def test_every_item_key_appears_exactly_once_across_the_rows(self) -> None:
        built = report()
        keys = [
            column.key for row in built.measurements for column in row.columns
        ]
        assert sorted(keys) == sorted(ITEM_KEYS)

    def test_an_absent_item_is_marked_absent_rather_than_zero_filled(self) -> None:
        """**行そのものが無い項目を 0 で埋めない**（タスク 6.2 の教訓）。"""
        built = report(absent=(ITEM_AIM_ERROR_MM,))
        column = column_of(built, 6, ITEM_AIM_ERROR_MM)
        assert column.present is False
        assert column.count is None
        assert column.median is None
        assert column.missing is None
        assert column.count != 0
        assert column.median != 0.0

    def test_the_unit_notes_are_attached_where_the_units_differ(self) -> None:
        """秒とミリ秒、メートルとミリメートルを並べていることを明示する。"""
        built = report()
        for item in (1, 2, 3):
            assert UNIT_NOTE_SECONDS_VS_MS in row_of(built, item).notes
        assert UNIT_NOTE_METERS_VS_MM in row_of(built, 6).notes
        assert UNIT_NOTE_METERS_VS_MM not in row_of(built, 1).notes
        assert UNIT_NOTE_SECONDS_VS_MS not in row_of(built, 6).notes
        assert UNIT_NOTE_SECONDS_VS_MS != UNIT_NOTE_METERS_VS_MM

    def test_items_2_and_3_carry_the_budget_row_notes(self) -> None:
        """§3 由来の備考と読み替えの注記を**運ぶ**（要件 11.5）。"""
        built = report()
        assert BUDGET_SEG1_NOTE in row_of(built, 2).notes
        assert BUDGET_SEG2_NOTE in row_of(built, 3).notes
        assert BUDGET_SEG1_NOTE not in row_of(built, 3).notes
        assert BUDGET_SEG2_NOTE not in row_of(built, 2).notes

    def test_item_4_carries_the_provisional_window_from_the_oq05_material(
        self,
    ) -> None:
        """許容窓の数値は `Oq05Result` から運ぶ。レポートで作り直さない。"""
        built = report()
        notes = " ".join(row_of(built, 4).notes)
        assert "67.5" in notes

    def test_item_5_states_that_no_assumed_value_exists(self) -> None:
        """**無い想定値をでっち上げない。**"""
        built = report()
        assert NOTE_ITEM_5 in row_of(built, 5).notes
        assert NOTE_ITEM_5 != NOTE_ITEM_7

    def test_item_7_carries_its_own_note(self) -> None:
        built = report()
        assert NOTE_ITEM_7 in row_of(built, 7).notes
        assert NOTE_ITEM_7 not in row_of(built, 5).notes

    def test_the_summary_shows_assumed_and_measured_side_by_side(self) -> None:
        summary = render_summary(report())
        assert MEASUREMENT_ITEM_LABELS[2] in summary
        assert BUDGET_SEG1_ASSUMED in summary
        assert "63" in summary
        assert ASSUMED_SOURCES[2] in summary
        assert BUDGET_SEG1_NOTE in summary
        assert UNIT_NOTE_SECONDS_VS_MS in summary

    def test_the_machine_readable_output_keeps_the_two_columns_apart(self) -> None:
        payload = report_to_dict(report())
        row = next(
            entry for entry in payload["measurements"] if entry["item"] == 2
        )
        assert row["assumed"] == BUDGET_SEG1_ASSUMED
        assert row["assumed_source"] == ASSUMED_SOURCES[2]
        assert row["columns"][0]["median"] == 63.0
        assert row["columns"][0]["key"] == ITEM_RELEASE_TO_DETECT_MS


# ---------------------------------------------------------------------------
# 4. 帰属の内訳（要件 6.9）
# ---------------------------------------------------------------------------


class TestAttributionIsShownAsComponents:
    """**合計誤差の単一値ではなく、成分ごとの内訳として出す**（要件 6.9）。

    成分を1つに畳む変異、成分の一部だけを出す変異、読み分け規則を落とす変異が
    ここで落ちる。
    """

    def test_the_bias_component_is_shown_with_its_direction(self) -> None:
        payload = report_to_dict(report())["attribution"]["bias"]
        assert payload["vector_mm"] == [23.0, -41.0]
        assert payload["norm_mm"] == 47.02
        assert payload["significance_ratio"] == 3.75
        assert payload["world_fixed_agreement_deg"] == 12.5
        assert payload["camera_ray_agreement_deg"] == 71.25
        assert payload["degenerate"] is False
        assert payload["attribution"] == "calibration"

    def test_the_scatter_component_is_shown_separately(self) -> None:
        payload = report_to_dict(report())["attribution"]["scatter"]
        assert payload["rms_mm"] == 14.5
        assert payload["bootstrap_rms_mm"] == 9.25
        assert payload["residual_median_mm"] == 5.125
        assert payload["attribution"] == "observation_noise"

    def test_the_two_components_are_not_collapsed_into_one_total(self) -> None:
        """**単一の合計誤差フィールドを持たない。**"""
        payload = report_to_dict(report())["attribution"]
        assert "total_error_mm" not in payload
        assert set(payload) == {
            "bias",
            "scatter",
            "range_bands",
            "calibration_reference",
            "reading_notes",
            "judgement",
        }

    def test_the_range_bands_are_shown(self) -> None:
        bands = report_to_dict(report())["attribution"]["range_bands"]
        assert [band["range_lo_mm"] for band in bands] == [1000.0, 1500.0]
        assert [band["mean_error_norm_mm"] for band in bands] == [31.5, 88.25]
        assert [band["throw_count"] for band in bands] == [4, 6]

    def test_the_calibration_reference_is_carried(self) -> None:
        payload = report_to_dict(report())["attribution"]["calibration_reference"]
        assert payload == {"bias_state": "recognized", "offset_mm": 17.5}

    def test_the_reading_rules_accompany_the_components(self) -> None:
        """**上流の読み分け規則を併記する**（タスク 7.1 の箇条）。"""
        built = report()
        assert built.attribution_reading_notes == ATTRIBUTION_READING_NOTES
        summary = render_summary(built)
        for note in (
            COMPONENT_BREAKDOWN_NOTE,
            SCATTER_WITH_BIAS_NOTE,
            RANGE_BAND_READING_NOTE,
        ):
            assert note in summary
            assert note in built.attribution_reading_notes

    def test_the_three_reading_rules_are_distinct(self) -> None:
        """**相互排他。** 同じ文へ潰す変異を殺す。"""
        assert len(set(ATTRIBUTION_READING_NOTES)) == 3

    def test_the_scatter_note_warns_against_reading_scatter_alone(self) -> None:
        """タスク5.3 の申し送り: ばらつきだけを読むと予測器を疑いに行く。"""
        assert "偏り" in SCATTER_WITH_BIAS_NOTE
        assert "検出由来" in SCATTER_WITH_BIAS_NOTE

    def test_the_range_band_note_explains_the_depth_range_reading(self) -> None:
        assert "遠方" in RANGE_BAND_READING_NOTE
        assert "奥行き" in RANGE_BAND_READING_NOTE

    def test_the_summary_shows_both_components_with_their_attributions(
        self,
    ) -> None:
        summary = render_summary(report())
        assert "calibration" in summary
        assert "observation_noise" in summary
        assert "47.02" in summary
        assert "14.5" in summary


# ---------------------------------------------------------------------------
# 5. OQ-27（要件 9.4）
# ---------------------------------------------------------------------------


class TestOq27Section:
    """**判定値・判定規則の説明文・改善適用履歴**を出す（要件 9.4）。"""

    def test_the_verdict_is_shown(self) -> None:
        built = report()
        assert report_to_dict(built)["oq27"]["verdict"] == "continue_with_constraints"
        assert "continue_with_constraints" in render_summary(built)

    def test_the_improvement_history_is_shown_for_applied_and_pending(
        self,
    ) -> None:
        """**真偽の両方を通す。** 未適用の行も残る（要件 9.4）。"""
        payload = report_to_dict(report())["oq27"]["improvements"]
        assert [entry["step"] for entry in payload] == [
            IMPROVEMENT_APPLIED.step,
            IMPROVEMENT_PENDING.step,
        ]
        assert [entry["applied"] for entry in payload] == [True, False]
        assert payload[0]["before"] == {"process_fps": 12.5}
        assert payload[0]["after"] == {"process_fps": 21.75}
        assert payload[1]["before"] == {}
        assert payload[1]["after"] == {}

    def test_the_before_and_after_values_are_not_swapped(self) -> None:
        payload = report_to_dict(report())["oq27"]["improvements"][0]
        assert payload["before"] != payload["after"]
        assert payload["before"]["process_fps"] < payload["after"]["process_fps"]

    def test_the_summary_lists_the_improvement_history(self) -> None:
        summary = render_summary(report())
        assert IMPROVEMENT_APPLIED.step in summary
        assert IMPROVEMENT_PENDING.step in summary
        assert "12.5" in summary
        assert "21.75" in summary

    def test_the_bottleneck_and_limiting_conditions_are_shown(self) -> None:
        payload = report_to_dict(report())["oq27"]
        assert payload["bottleneck_stage"] == "capture"
        assert payload["bottleneck_label"] == "capture/frame/total_ms"
        assert payload["bottleneck_p95_ms"] == 54.25
        assert payload["end_to_end_p95_ms"] == 163.75
        assert payload["overhead_reference_ms"] == 191.5
        assert payload["resource_saturated"] is False
        assert payload["limiting_conditions"] == [
            "＜局所リテラル: 律速している条件＞"
        ]
        assert "＜局所リテラル: 律速している条件＞" in render_summary(report())


# ---------------------------------------------------------------------------
# 6. OQ-05（要件 10.4）
# ---------------------------------------------------------------------------


class TestOq05Section:
    """**材料であって決着ではない旨**を出す（要件 10.4）。"""

    def test_the_three_notes_are_all_present(self) -> None:
        built = report()
        summary = render_summary(built)
        payload = report_to_dict(built)["oq05"]
        for note in (
            OQ05_MATERIAL_ONLY_NOTE,
            OQ05_UPPER_BOUND_NOTE,
            OQ05_OBJECT_SCOPE_NOTE,
        ):
            assert note in summary
        assert payload["material_only_note"] == OQ05_MATERIAL_ONLY_NOTE
        assert payload["upper_bound_note"] == OQ05_UPPER_BOUND_NOTE
        assert payload["object_scope_note"] == OQ05_OBJECT_SCOPE_NOTE

    def test_the_three_notes_are_not_swapped(self) -> None:
        """**相互排他。** 3つは別の警告である。"""
        payload = report_to_dict(report())["oq05"]
        assert payload["material_only_note"] != payload["upper_bound_note"]
        assert payload["material_only_note"] != payload["object_scope_note"]
        assert payload["upper_bound_note"] != payload["object_scope_note"]

    def test_the_material_values_are_shown(self) -> None:
        payload = report_to_dict(report())["oq05"]
        assert payload["window_mm"] == 67.5
        assert payload["within_window_ratio"] == 0.625
        assert payload["within_window_count"] == 5
        assert payload["evaluated_throw_count"] == 8
        assert payload["confidence_level"] == 0.85
        assert payload["required_trials"] == {"0.20": 41, "0.10": None}

    def test_an_unestimable_required_trial_stays_missing(self) -> None:
        """**欠測を 0 で埋めない。**"""
        payload = report_to_dict(report())["oq05"]
        assert payload["required_trials"]["0.10"] is None
        assert payload["required_trials"]["0.10"] != 0

    def test_the_verdict_is_material_only(self) -> None:
        assert report_to_dict(report())["oq05"]["verdict"] == "material_only"


# ---------------------------------------------------------------------------
# 7. 時間予算表（要件 11.2）
# ---------------------------------------------------------------------------


class TestBudgetSection:
    """行と注記を**運ぶ**。レポートで作り直さない。"""

    def test_every_row_is_carried_with_its_label_assumed_and_note(self) -> None:
        rows = report_to_dict(report())["budget"]["rows"]
        assert [row["segment"] for row in rows] == ["1", "2", "3", "total"]
        assert [row["assumed"] for row in rows] == [
            BUDGET_SEG1_ASSUMED,
            BUDGET_SEG2_ASSUMED,
            BUDGET_SEG3_ASSUMED,
            BUDGET_TOTAL_ASSUMED,
        ]
        assert [row["note"] for row in rows] == [
            BUDGET_SEG1_NOTE,
            BUDGET_SEG2_NOTE,
            BUDGET_SEG3_NOTE,
            BUDGET_TOTAL_NOTE,
        ]
        assert rows[1]["label"] == BUDGET_SEG2_LABEL

    def test_segment_three_stays_missing(self) -> None:
        """**区間3 を勝手に埋めない。**"""
        rows = report_to_dict(report())["budget"]["rows"]
        assert rows[2]["measured"] is None
        assert rows[2]["trials"] == 0
        assert BUDGET_SEG3_NOTE in render_summary(report())

    def test_the_three_budget_notes_are_carried(self) -> None:
        payload = report_to_dict(report())["budget"]
        summary = render_summary(report())
        for note in (
            BUDGET_REMAINING_NOTE,
            BUDGET_PROVISIONAL_NOTE,
            BUDGET_COMPUTATION_NOTE,
        ):
            assert note in summary
        assert payload["remaining_time_note"] == BUDGET_REMAINING_NOTE
        assert payload["provisional_target_note"] == BUDGET_PROVISIONAL_NOTE
        assert payload["computation_only_note"] == BUDGET_COMPUTATION_NOTE

    def test_the_derived_target_and_the_kept_segment3_assumption_are_shown(
        self,
    ) -> None:
        payload = report_to_dict(report())["budget"]
        assert payload["derived_latency_target_ms"] == 237.5
        assert payload["segment3_assumed_ms"] == 37.0
        assert payload["ready"] is True
        assert payload["missing_items"] == []

    def test_a_closed_gate_is_reported_with_its_missing_items(self) -> None:
        """ゲートが閉じていても行と想定値は残る（要件 11.1）。"""
        built = report(budget_ready=False)
        payload = report_to_dict(built)["budget"]
        assert payload["ready"] is False
        assert payload["missing_items"] == ["item4:hit_error_norm_first_mm"]
        assert payload["rows"][0]["measured"] is None
        assert payload["rows"][0]["assumed"] == BUDGET_SEG1_ASSUMED
        assert "item4:hit_error_norm_first_mm" in render_summary(built)


# ---------------------------------------------------------------------------
# 8. 計測 ON/OFF 比較
# ---------------------------------------------------------------------------


class TestOverheadSection:
    def test_the_overhead_report_is_carried_verbatim(self) -> None:
        payload = report_to_dict(report())["overhead"]
        assert payload["criterion"] == CRITERION_OVERHEAD
        assert payload["segment_order"] == ["measurement_off", "measurement_on"]
        assert payload["verdicts"][0]["passed"] is True
        assert payload["raw_samples"] == {"measurement_off": {"predict": [4.5, 4.75]}}

    def test_the_summary_has_its_own_overhead_section(self) -> None:
        """**節そのものを落とす変異を殺す。**

        判定値だけを見ると、判定規則の一覧にも同じ語が出るので節を丸ごと
        削っても気付けない（実際に変異が生き延びた）。この節にしか現れない
        値——交互実行の並び・上流の区間との切り分け・end-to-end の定義文・
        判定の内訳——で固定する。
        """
        summary = render_summary(report())
        assert "measurement_off→measurement_on" in summary
        assert "＜局所リテラル: 上流の区間と混同させない＞" in summary
        assert "＜局所リテラル: end-to-end の定義文＞" in summary
        assert "＜局所リテラル: 判定の根拠＞" in summary
        assert "0.25" in summary
        assert "0.9" in summary

    def test_without_the_bench_the_summary_says_so(self) -> None:
        """**「実施していない」と「差が無かった」は別である。**"""
        summary = render_summary(report(with_overhead=False))
        assert "実施していない" in summary
        assert "＜局所リテラル: end-to-end の定義文＞" not in summary


# ---------------------------------------------------------------------------
# 9. NaN と無限大を欠測として表す
# ---------------------------------------------------------------------------


class TestNonFiniteValuesBecomeMissing:
    """**NaN / 無限大は欠測（`None`）として表す。**

    そのまま出すと JSON が壊れ、0 で埋めると読み手が「測って 0 だった」と
    受け取る。**どちらも別の事故である。**
    """

    def build(self, value: float) -> M1Report:
        base = aggregate()
        broken = dict(base.items)
        broken[ITEM_AIM_ERROR_MM] = Distribution(
            count=4,
            median=value,
            p95=value,
            iqr=value,
            minimum=value,
            maximum=value,
            missing=THROW_COUNT - 4,
        )
        replaced = ThrowAggregate(
            calibration_id=base.calibration_id,
            verified=base.verified,
            session_ids=base.session_ids,
            throw_count=base.throw_count,
            failed_throw_count=base.failed_throw_count,
            valid_throw_count=base.valid_throw_count,
            live_throw_count=base.live_throw_count,
            converged_count=base.converged_count,
            not_converged_count=base.not_converged_count,
            not_measurable_count=base.not_measurable_count,
            single_prediction_throw_count=base.single_prediction_throw_count,
            provisional=base.provisional,
            provisional_reasons=base.provisional_reasons,
            items=broken,
            error_vectors=base.error_vectors,
            per_throw=base.per_throw,
        )
        return build_report(
            session_id=SESSION_ID,
            aggregate=replaced,
            attribution=attribution(
                evidence={"nan": math.nan, "inf": math.inf, "ninf": -math.inf}
            ),
            oq27=oq27(),
            oq05=oq05(),
            budget=budget(),
            overhead=overhead(),
        )

    @pytest.mark.parametrize(
        "value", [math.nan, math.inf, -math.inf], ids=["nan", "inf", "-inf"]
    )
    def test_a_non_finite_distribution_value_is_reported_as_missing(
        self, value: float
    ) -> None:
        built = self.build(value)
        column = column_of(built, 6, ITEM_AIM_ERROR_MM)
        assert column.median is None
        assert column.p95 is None
        assert column.iqr is None
        assert column.minimum is None
        assert column.maximum is None

    @pytest.mark.parametrize(
        "value", [math.nan, math.inf, -math.inf], ids=["nan", "inf", "-inf"]
    )
    def test_the_missing_value_is_not_zero_filled(self, value: float) -> None:
        column = column_of(self.build(value), 6, ITEM_AIM_ERROR_MM)
        assert column.median != 0.0
        assert column.present is True
        assert column.count == 4

    def test_non_finite_evidence_values_become_missing(self) -> None:
        payload = report_to_dict(self.build(math.nan))
        evidence = payload["judgements"][0]["evidence"]
        assert evidence["nan"] is None
        assert evidence["inf"] is None
        assert evidence["ninf"] is None

    @pytest.mark.parametrize(
        "value", [math.nan, math.inf, -math.inf], ids=["nan", "inf", "-inf"]
    )
    def test_the_machine_readable_output_is_valid_json(self, value: float) -> None:
        """`allow_nan=False` は NaN / Infinity で例外を送出する。"""
        text = json.dumps(
            report_to_dict(self.build(value)), ensure_ascii=False, allow_nan=False
        )
        assert "NaN" not in text
        assert "Infinity" not in text

    def test_a_finite_value_survives_untouched(self) -> None:
        """**対の検査。** すべてを `None` に潰す変異を殺す。"""
        column = column_of(self.build(1.5), 6, ITEM_AIM_ERROR_MM)
        assert column.median == 1.5


# ---------------------------------------------------------------------------
# 10. 証跡のキー集合と値の型（タスク 6.2 からの申し送り）
# ---------------------------------------------------------------------------


class TestJudgementSerialisationFixesKeysAndTypes:
    """**証跡のキー集合と値の型を1箇所で固定する**（タスク 6.2 の申し送り）。

    `Judgement` を JSON 化するのは本モジュールであり、ここが唯一の固定点で
    ある。キーを足す変異・落とす変異・値の型を変える変異がここで落ちる。
    """

    def test_the_key_set_is_exactly_the_six_declared_keys(self) -> None:
        payload = judgement_to_dict(
            judgement(
                question="q",
                criterion="c",
                verdict="v",
                rationale="r",
                evidence={"a": 1},
                provisional=True,
            )
        )
        assert tuple(payload) == JUDGEMENT_KEYS
        assert JUDGEMENT_KEYS == (
            "question",
            "criterion",
            "verdict",
            "rationale",
            "evidence",
            "provisional",
        )

    def test_the_values_keep_their_declared_types(self) -> None:
        payload = judgement_to_dict(
            judgement(
                question="q",
                criterion="c",
                verdict=str(Oq27Verdict.CONTINUE),
                rationale="r",
                evidence={"n": 3, "f": 1.25, "b": False, "s": "x", "none": None},
                provisional=False,
            )
        )
        assert payload["question"] == "q"
        assert payload["criterion"] == "c"
        assert payload["verdict"] == "continue"
        assert payload["rationale"] == "r"
        assert payload["provisional"] is False
        assert payload["evidence"] == {
            "n": 3,
            "f": 1.25,
            "b": False,
            "s": "x",
            "none": None,
        }
        assert isinstance(payload["evidence"]["n"], int)
        assert isinstance(payload["evidence"]["b"], bool)
        assert isinstance(payload["evidence"]["f"], float)

    def test_nested_sequences_and_mappings_are_converted(self) -> None:
        payload = judgement_to_dict(
            judgement(
                question="q",
                criterion="c",
                verdict="v",
                evidence={
                    "seq": (1.0, math.nan),
                    "map": {"inner": math.inf},
                    "enum": Attribution.DETECTION,
                    "path": Path("var/m1"),
                },
            )
        )
        assert payload["evidence"]["seq"] == [1.0, None]
        assert payload["evidence"]["map"] == {"inner": None}
        assert payload["evidence"]["enum"] == "detection"
        assert isinstance(payload["evidence"]["path"], str)

    def test_the_provisional_flag_is_carried_both_ways(self) -> None:
        for flag in (True, False):
            payload = judgement_to_dict(
                judgement(
                    question="q", criterion="c", verdict="v", provisional=flag
                )
            )
            assert payload["provisional"] is flag

    def test_the_evidence_is_a_copy(self) -> None:
        source = {"a": 1}
        built = judgement(
            question="q", criterion="c", verdict="v", evidence=source
        )
        payload = judgement_to_dict(built)
        payload["evidence"]["a"] = 999
        assert built.evidence["a"] == 1


# ---------------------------------------------------------------------------
# 11. 書き出しと形
# ---------------------------------------------------------------------------


class TestWritingTheReport:
    def test_the_output_path_follows_the_design(self, tmp_output_dir: Path) -> None:
        assert report_output_path(tmp_output_dir, SESSION_ID) == (
            tmp_output_dir / f"report-{SESSION_ID}.json"
        )

    def test_the_written_file_round_trips_through_json(
        self, tmp_output_dir: Path
    ) -> None:
        path = write_report(report(), tmp_output_dir, SESSION_ID)
        assert path == tmp_output_dir / f"report-{SESSION_ID}.json"
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert [entry["criterion"] for entry in loaded["judgements"]] == [
            CRITERION_ATTRIBUTION,
            CRITERION_OQ27,
            CRITERION_OQ05,
            CRITERION_BUDGET,
            CRITERION_OVERHEAD,
        ]
        assert loaded["session_id"] == SESSION_ID
        assert loaded["calibration_id"] == CALIBRATION_ID

    def test_the_directory_is_created_when_missing(self, tmp_path: Path) -> None:
        target = tmp_path / "does" / "not" / "exist"
        path = write_report(report(), target, SESSION_ID)
        assert path.exists()

    def test_the_report_is_immutable(self) -> None:
        built = report()
        with pytest.raises(FrozenInstanceError):
            built.session_id = "other"  # type: ignore[misc]

    def test_the_report_version_and_provisional_notice_are_present(self) -> None:
        built = report()
        payload = report_to_dict(built)
        assert built.report_version == REPORT_VERSION
        assert payload["report_version"] == REPORT_VERSION
        assert payload["provisional_notice"] == built.provisional_notice
        assert built.provisional_notice.strip() != ""
        assert built.provisional_notice in render_summary(built)

    def test_an_empty_session_id_is_rejected(self) -> None:
        with pytest.raises(M1ConfigError):
            build_report(
                session_id="   ",
                aggregate=aggregate(),
                attribution=attribution(),
                oq27=oq27(),
                oq05=oq05(),
                budget=budget(),
                overhead=None,
            )

    def test_the_same_input_produces_the_same_output(self) -> None:
        """要件 12.4: 同一入力に対して同一の出力を返す。"""
        first = report_to_dict(report())
        second = report_to_dict(report())
        assert first == second
        assert render_summary(report()) == render_summary(report())


# ---------------------------------------------------------------------------
# 12. 本モジュールが所有する文字列を、値として固定する
#
# レビュー差し戻し（ラウンド1）の是正。ここより上の検査は、見出し・出どころ・
# 注記について「実装の定数と自分自身を比べる」形と「互いに異なる」形しか
# 持っていなかった。**それは値の固定ではない**（既知の空振り形3 / 6 / 7）。
# 実際に、見出しの入れ替え・出どころの入れ替え・注記を無内容へ潰す変異が
# 85件すべてを素通りした。
#
# 以下はすべて**テスト局所リテラル**である。実装から import した定数どうしを
# 比べる検査は1つも置かない。
# ---------------------------------------------------------------------------


#: `docs/requirements.md §8 M1`（L392-400）の表の「実測項目」列の写し。
SOURCE_TABLE_LABELS: Mapping[int, str] = {
    1: "総飛行時間",
    2: "リリース〜検出開始までの時間",
    3: "検出開始〜予測確定までの時間",
    4: "予測落下地点と実際の落下地点の誤差",
    5: "落下時刻の予測誤差",
    6: "狙い誤差（必要横移動量）",
    7: "何サンプル取れたか / 何サンプルで誤差が収束するか",
}

#: 同表の「対応」列が指している文書上の場所。
SOURCE_TABLE_SOURCES: Mapping[int, str] = {
    1: "docs/requirements.md §3 本文（総飛行時間の想定）",
    2: "docs/requirements.md §3 時間予算表 区間1（まったく未検証の区間）",
    3: "docs/requirements.md §3 時間予算表 区間2 / NFR-3",
    4: "docs/requirements.md NFR-5（位置精度・暫定目標）",
    5: "docs/requirements.md NFR-6（到達時の静定）",
    6: "docs/requirements.md §3 本文（横方向の狙い誤差の想定）",
    7: "docs/requirements.md FR-1（最低3サンプルとその根拠）",
}


class TestTheItemLabelsAndSourcesAreFixedByValue:
    """**見出しと出どころを値で固定する**（要件 5.11）。

    要件 5.11 が求めるのは値の併記ではなく**対応関係**である。見出しと想定値の
    対応が壊れると、レポートは「検出開始〜予測確定」という見出しの下に区間1 の
    想定値と区間1 の備考を並べて出す——**誤読を生む形でしか読まれない出力**に
    なる。

    タスク6.3 の最重要教訓（「行の見出しについて `distinct` だけを置いていた
    ため、別区間の終点へ取り違えても110件全通過した。**読み手が最初に見るのは
    見出しである**」）の再発を防ぐための検査である。
    """

    def test_every_label_matches_the_source_table(self) -> None:
        for item, label in SOURCE_TABLE_LABELS.items():
            assert MEASUREMENT_ITEM_LABELS[item] == label

    def test_every_row_carries_its_own_label(self) -> None:
        built = report()
        for item, label in SOURCE_TABLE_LABELS.items():
            assert row_of(built, item).label == label

    def test_no_row_carries_the_label_of_another_row(self) -> None:
        """**取り違え専用の否定照合。** 隣接する行ほど入れ替えても気付きにくい。"""
        built = report()
        for item in SOURCE_TABLE_LABELS:
            others = {
                label
                for other, label in SOURCE_TABLE_LABELS.items()
                if other != item
            }
            assert row_of(built, item).label not in others

    def test_every_assumed_source_matches_the_document_it_points_at(self) -> None:
        for item, source in SOURCE_TABLE_SOURCES.items():
            assert ASSUMED_SOURCES[item] == source

    def test_every_row_carries_its_own_assumed_source(self) -> None:
        built = report()
        for item, source in SOURCE_TABLE_SOURCES.items():
            assert row_of(built, item).assumed_source == source

    def test_no_row_carries_the_assumed_source_of_another_row(self) -> None:
        built = report()
        for item in SOURCE_TABLE_SOURCES:
            others = {
                source
                for other, source in SOURCE_TABLE_SOURCES.items()
                if other != item
            }
            assert row_of(built, item).assumed_source not in others

    def test_the_two_time_budget_segments_are_not_swapped(self) -> None:
        """**区間1 と区間2 の取り違えは本 Spec で最も起こりやすい取り違えである。**

        項目2 は §3 区間1（まったく未検証の区間）、項目3 は §3 区間2 / NFR-3 で
        ある。入れ替わると、プロジェクトで最も未検証な量の想定値が別の区間の
        見出しの下に出る。
        """
        built = report()
        assert row_of(built, 2).label == "リリース〜検出開始までの時間"
        assert row_of(built, 3).label == "検出開始〜予測確定までの時間"
        assert "区間1" in row_of(built, 2).assumed_source
        assert "区間1" not in row_of(built, 3).assumed_source
        assert "区間2" in row_of(built, 3).assumed_source
        assert "区間2" not in row_of(built, 2).assumed_source

    def test_the_summary_puts_each_label_above_its_own_source(self) -> None:
        """要約の中でも見出しと出どころが同じ節に並ぶ。"""
        summary = render_summary(report())
        for item, label in SOURCE_TABLE_LABELS.items():
            heading = f"### 項目{item} {label}"
            assert heading in summary
            section = summary.split(heading, 1)[1].split("### ", 1)[0]
            assert SOURCE_TABLE_SOURCES[item] in section


class TestTheAssumedValuesAreFixedByValue:
    """項目4〜7 の想定値を**文書の文言として**固定する。

    これらは `BudgetUpdate` から運べない（時間予算表に載っていない）ため本
    モジュールが所有している定数であり、**所有しているものは値で固定する**。
    """

    def test_item_4_transcribes_nfr5(self) -> None:
        assert ASSUMED_ITEM_4 == (
            "落下時刻における水平位置誤差 < 開口半径 − ゴミの代表寸法/2"
            "（NFR-5。暫定目標であって合否条件ではない）"
        )

    def test_item_4_does_not_harden_the_window_into_a_pass_condition(self) -> None:
        assert "合否条件ではない" in ASSUMED_ITEM_4
        assert "暫定目標" in ASSUMED_ITEM_4

    def test_item_6_transcribes_the_section_3_range(self) -> None:
        assert ASSUMED_ITEM_6 == "0.3〜0.8 m"

    def test_item_7_transcribes_the_fr1_floor(self) -> None:
        assert ASSUMED_ITEM_7 == "最低3サンプル"

    def test_item_7_note_says_the_floor_is_under_review(self) -> None:
        assert "FR-1 の「最低3サンプル」は理論上の下限" in NOTE_ITEM_7
        assert "実用上は3点でも足りない可能性があり" in NOTE_ITEM_7
        assert "必要サンプル数は M1 の実測で見直す" in NOTE_ITEM_7
        assert "本項目はその見直しのための実測である" in NOTE_ITEM_7

    def test_the_window_note_names_the_window_and_its_status(self) -> None:
        notes = " ".join(row_of(report(), 4).notes)
        assert "本レポートで用いた暫定許容窓は 67.5 mm である" in notes
        assert "投擲レイアウトの開口寸法と対象物寸法から導いた暫定値" in notes
        assert "合否条件ではない" in notes


class TestItemFiveRefusesToFabricateATarget:
    """**項目5 には並べる相手が無い。仮の目標値を置かない。**

    `docs/requirements.md` NFR-6 が求めるのは「落下時刻において位置誤差だけで
    なく残留速度も許容範囲であること」であって、**落下時刻の予測誤差そのものに
    許容量を置いていない**。方針（停止して待つ / 通過キャッチを許容する）も
    OQ-04 で未確定である。

    ここに仮の数値を置くと、**実測前の数値が合否条件として独り歩きする**
    （`tech.md` 開発標準1「未実測の数値を合否条件にしない」）。この立場は
    レポートの文面としてしか残らないので、文面で固定する。
    """

    def test_the_assumed_column_says_there_is_no_assumed_value(self) -> None:
        assert ASSUMED_ITEM_5 == (
            "並べられる想定値は無い（NFR-6 は到達時の静定を求めるだけで、"
            "落下時刻の予測誤差そのものに許容量を置いていない）"
        )

    def test_no_numeric_target_is_smuggled_into_the_assumed_column(self) -> None:
        """**否定照合。** 「±30 ms 以内」のような捏造を殺す。"""
        without_reference = ASSUMED_ITEM_5.replace("NFR-6", "")
        assert not any(char.isdigit() for char in without_reference)
        assert "±" not in ASSUMED_ITEM_5
        assert "ms" not in without_reference
        assert "mm" not in without_reference

    def test_the_note_explains_why_no_target_is_placed(self) -> None:
        assert "NFR-6 の方針" in NOTE_ITEM_5
        assert "未確定" in NOTE_ITEM_5
        assert "想定値はどの文書にも無い" in NOTE_ITEM_5
        assert "実測値だけを材料として残す" in NOTE_ITEM_5
        assert "仮の目標値を置くと" in NOTE_ITEM_5
        assert "合否条件として独り歩きする" in NOTE_ITEM_5

    def test_the_summary_carries_both_statements(self) -> None:
        summary = render_summary(report())
        assert ASSUMED_ITEM_5 in summary
        assert NOTE_ITEM_5 in summary


class TestTheUnitNotesStateTheUnits:
    """**単位の食い違いは桁が3つずれる。** 注記の中身を語で固定する。

    想定値は §3 が秒で、§3 本文の狙い誤差はメートルで書かれている。実測値は
    どちらもミリ単位である。この注記が無内容へ潰れると、437（mm）を
    0.3〜0.8（m）と並べた読み手が「3桁近い超過」と読む。
    """

    def test_the_seconds_note_names_both_units_and_the_warning(self) -> None:
        assert "想定値は秒（s）" in UNIT_NOTE_SECONDS_VS_MS
        assert "実測値はミリ秒（ms）" in UNIT_NOTE_SECONDS_VS_MS
        assert "引き比べないこと" in UNIT_NOTE_SECONDS_VS_MS

    def test_the_metres_note_names_both_units_and_the_warning(self) -> None:
        assert "想定値はメートル（m）" in UNIT_NOTE_METERS_VS_MM
        assert "実測値はミリメートル（mm）" in UNIT_NOTE_METERS_VS_MM
        assert "引き比べないこと" in UNIT_NOTE_METERS_VS_MM

    def test_neither_note_borrows_the_units_of_the_other(self) -> None:
        assert "ミリ秒" not in UNIT_NOTE_METERS_VS_MM
        assert "ミリメートル" not in UNIT_NOTE_SECONDS_VS_MS

    def test_both_notes_reach_the_summary_with_their_content(self) -> None:
        summary = render_summary(report())
        assert "想定値は秒（s）" in summary
        assert "実測値はミリメートル（mm）" in summary


class TestTheReadingRulesStateTheirRules:
    """読み分け規則3件を**同じ厚さで**固定する（要件 6.9 / 6.11）。

    構造側（合計フィールドを持たないこと）は別の検査で固定済みである。ここで
    固定するのは**読み手に渡る文面**であり、構造が正しくても文面が無内容だと
    「単一値へ畳んで読む」誤読は止まらない。
    """

    def test_the_breakdown_note_forbids_collapsing_into_a_total(self) -> None:
        """**文の数だけ肯定照合を置く**（タスク 6.1 の是正の型）。

        語を1つ2つ見るだけだと、一文まるごと落とす変異が生き延びる。
        """
        assert "合計の単一値へ畳まず" in COMPONENT_BREAKDOWN_NOTE
        assert "偏り成分とばらつき成分の内訳として" in COMPONENT_BREAKDOWN_NOTE
        assert "どちらを直せばよいのかという情報が消える" in COMPONENT_BREAKDOWN_NOTE

    def test_the_scatter_note_forbids_reading_scatter_alone(self) -> None:
        """**先頭の一文（規則そのもの）まで覆う。**

        末尾の「偏り成分と併せて読むこと」だけを見ていたため、先頭の
        「ばらつき成分だけを読んで予測器を疑いに行かないこと」を無内容へ
        差し替える変異が生き延びた。**規則を述べている文は先頭である。**
        """
        assert "ばらつき成分だけを読んで予測器を疑いに行かないこと" in (
            SCATTER_WITH_BIAS_NOTE
        )
        assert "ばらつき側の語彙には「検出由来」が無く" in SCATTER_WITH_BIAS_NOTE
        assert "カメラ視線方向の偏り" in SCATTER_WITH_BIAS_NOTE
        assert "実際にばらつきも生む" in SCATTER_WITH_BIAS_NOTE
        assert "規則どおりの動作である" in SCATTER_WITH_BIAS_NOTE
        assert "偏り成分と併せて読むこと" in SCATTER_WITH_BIAS_NOTE

    def test_the_range_band_note_states_the_depth_range_reading(self) -> None:
        assert "遠方の帯でだけ誤差が大きい場合" in RANGE_BAND_READING_NOTE
        assert "奥行き計測の距離特性" in RANGE_BAND_READING_NOTE
        assert "全帯で一様に大きい誤差を距離特性と読まないこと" in (
            RANGE_BAND_READING_NOTE
        )

    def test_the_three_notes_do_not_borrow_the_wording_of_the_others(self) -> None:
        assert "奥行き" not in COMPONENT_BREAKDOWN_NOTE
        assert "検出由来" not in COMPONENT_BREAKDOWN_NOTE
        assert "合計" not in RANGE_BAND_READING_NOTE


class TestTheWarningsStateTheirContent:
    """2つの警告の**中身**を語で固定する（要件 2.2 / 5.10）。"""

    def test_the_unverified_warning_states_what_cannot_be_done(self) -> None:
        assert "未検証のキャリブレーション" in UNVERIFIED_WARNING
        assert "誤差の帰属はできない" in UNVERIFIED_WARNING
        assert "座標系が数 cm ずれていても" in UNVERIFIED_WARNING
        assert "系統誤差と予測誤差に分離できない" in UNVERIFIED_WARNING
        assert "測り直すこと" in UNVERIFIED_WARNING

    def test_the_provisional_warning_states_that_it_is_not_usable(self) -> None:
        text = provisional_warning(("insufficient_valid_throws",))
        assert "暫定の印" in text
        assert "判断に用いてよい状態ではない" in text
        assert "insufficient_valid_throws" in text

    def test_the_provisional_warning_says_so_when_no_reason_was_recorded(
        self,
    ) -> None:
        """**理由が無いことを黙って伏せない。**"""
        assert "理由の記録なし" in provisional_warning(())


# ---------------------------------------------------------------------------
# 13. 要約における欠測の書かれ方
# ---------------------------------------------------------------------------


def broken_item_aggregate(value: float) -> ThrowAggregate:
    """狙い誤差の分布だけを与えられた値に差し替えた集計。"""
    base = aggregate()
    broken = dict(base.items)
    broken[ITEM_AIM_ERROR_MM] = Distribution(
        count=4,
        median=value,
        p95=value,
        iqr=value,
        minimum=value,
        maximum=value,
        missing=THROW_COUNT - 4,
    )
    return dataclasses.replace(base, items=broken)


def summary_of(
    *,
    agg: ThrowAggregate | None = None,
    oq27_result: Oq27Result | None = None,
    budget_ready: bool = True,
) -> str:
    return render_summary(
        build_report(
            session_id=SESSION_ID,
            aggregate=aggregate() if agg is None else agg,
            attribution=attribution(),
            oq27=oq27() if oq27_result is None else oq27_result,
            oq05=oq05(),
            budget=budget(ready=budget_ready),
            overhead=overhead(),
        )
    )


class TestMissingValuesAreWrittenAsMissingInTheSummary:
    """**要約でも欠測は「欠測」と書く。** 0 とも空欄とも書かない。

    機械可読出力側は `None` で固定済みだが、**人が読むのは要約のほうである**。
    要約が欠測を 0 と書けば、機械可読側が正しくても誤読は止まらない。
    """

    def test_an_item_absent_from_the_aggregate_says_the_row_is_missing(
        self,
    ) -> None:
        summary = summary_of(agg=aggregate(absent=(ITEM_AIM_ERROR_MM,)))
        assert "集計に当該項目の行が無い" in summary
        assert "0 件ではない" in summary

    @pytest.mark.parametrize(
        "value", [math.nan, math.inf, -math.inf], ids=["nan", "inf", "-inf"]
    )
    def test_a_non_finite_value_is_written_as_missing(self, value: float) -> None:
        summary = summary_of(agg=broken_item_aggregate(value))
        section = summary.split("### 項目6 ", 1)[1].split("### ", 1)[0]
        assert "代表値 欠測" in section
        assert "代表値 0" not in section
        assert "nan" not in section.lower()
        assert "inf" not in section.lower()

    def test_a_finite_value_is_still_written_as_a_number(self) -> None:
        """**対の検査。** すべてを「欠測」と書く実装を殺す。"""
        section = summary_of(agg=broken_item_aggregate(1.5)).split(
            "### 項目6 ", 1
        )[1]
        assert "代表値 1.5" in section

    def test_unmeasured_resource_saturation_is_not_read_as_headroom(self) -> None:
        """要件 9.7: **「測っていない」を「余裕がある」と読み替えない。**"""
        summary = summary_of(
            oq27_result=dataclasses.replace(oq27(), resource_saturated=None)
        )
        assert "余裕があるとは読まない" in summary
        assert "- 資源の飽和: なし" not in summary

    def test_measured_resource_saturation_is_written_plainly(self) -> None:
        """**対の検査。** 真偽の両方を通す。"""
        assert "- 資源の飽和: なし" in summary_of()
        assert "- 資源の飽和: あり" in summary_of(
            oq27_result=dataclasses.replace(oq27(), resource_saturated=True)
        )

    def test_an_improvement_without_before_and_after_says_no_record(self) -> None:
        """**申告だけの改善を「変化なし」と書かない。**"""
        assert "記録なし" in summary_of()

    def test_an_unestimable_required_trial_count_is_written_as_missing(
        self,
    ) -> None:
        summary = summary_of()
        assert "幅 0.10: 欠測" in summary
        assert "幅 0.20: 41" in summary

    def test_a_closed_gate_writes_the_measured_column_as_missing(self) -> None:
        summary = summary_of(budget_ready=False)
        assert "/ 実測 欠測" in summary


class TestTheSummaryKeepsTheStandingReservations:
    """判定値からは読めない**独立の断り**が要約に残る。"""

    def test_the_oq05_heading_states_material_only(self) -> None:
        """要件 10.4 は注記だけでなく節の位置づけでもある。"""
        assert "材料であって決着ではない" in render_summary(report())

    def test_the_summary_states_that_hardware_is_not_replaced(self) -> None:
        """要件 9.11: 判断材料と結論の提示までにとどめる。"""
        summary = render_summary(report())
        assert "ハードウェアの置き換えは実行しない" in summary
        assert "判断材料と結論の提示にとどめる" in summary


# ---------------------------------------------------------------------------
# 14. 要約の数値を「見出しに結び付いた形」で固定する
#
# レビュー差し戻し（ラウンド2）の是正。機械可読側は値で固定済みだったが、
# **人が実際に読む要約側では「どの数がどの見出しの下に出るか」が一切検査されて
# いなかった**。列の入れ替え（試行数↔欠測、律速 p95↔end-to-end p95、分子↔分母）
# も、予算表の行から想定値の列を丸ごと落とす変異も素通りしていた。
#
# 既存の `test_a_non_finite_value_is_written_as_missing` が使っている「節を
# 切り出してその中を照合する」形をすべての節へ展開する。**節で切り出すのが
# 要点**である——`BUDGET_SEG1_ASSUMED` は実測7項目の節にも出るため、要約全体
# への `in` では予算表の節が素通しになる（空振り形5 の変種）。
# ---------------------------------------------------------------------------


def section_of(summary: str, heading: str) -> str:
    """見出しから次の見出しまでを切り出す。

    要約の見出しはすべて行頭の `#` で始まるので、`"\\n#"` で切ればレベルに
    よらず「その見出しが支配する範囲」が取れる。
    """
    assert heading in summary, heading
    return summary.split(heading, 1)[1].split("\n#", 1)[0]


def line_starting_with(body: str, prefix: str) -> str:
    for line in body.splitlines():
        if line.startswith(prefix):
            return line
    raise AssertionError(f"{prefix!r} で始まる行が無い:\n{body}")


ATTRIBUTION_HEADING = "## 誤差の帰属（成分ごとの内訳。合計の単一値へ畳まない）"
OQ27_HEADING = "## OQ-27（Raspberry Pi 4 の継続可否）"
OQ05_HEADING = "## OQ-05（NFR-7 の目標成功率と試行回数）"
BUDGET_HEADING = "## 時間予算表の更新値"
OVERHEAD_HEADING = "## 計測 ON/OFF 比較（計測の非侵襲性）"


class TestTheSummaryBindsEachNumberToItsOwnHeading:
    """**要約の数値を、それが属する見出しの下で行ごと厳密一致させる。**

    フィクスチャの数はすべて相異なるので、行まるごとの一致は**同じ行の中の
    列の入れ替え**（試行数↔欠測、p95↔四分位範囲、分子↔分母）と**行から列を
    落とす変異**の両方を同時に殺す。
    """

    def test_the_measured_columns_of_item_2_are_bound_in_order(self) -> None:
        body = section_of(
            render_summary(report()), "### 項目2 リリース〜検出開始までの時間"
        )
        assert line_starting_with(body, "- 実測 release_to_detect_ms:") == (
            "- 実測 release_to_detect_ms: 代表値 63"
            " / ばらつき（四分位範囲） 15.75 / p95 94.5"
            " / 最小 58 / 最大 70 / 試行数 11 / 欠測 1"
        )

    def test_the_two_columns_of_item_4_are_bound_to_their_own_keys(self) -> None:
        """項目4 は初回予測と最終予測の**2列**を持つ。入れ替えを殺す。"""
        body = section_of(
            render_summary(report()), "### 項目4 予測落下地点と実際の落下地点の誤差"
        )
        assert line_starting_with(body, "- 実測 hit_error_norm_first_mm:") == (
            "- 実測 hit_error_norm_first_mm: 代表値 141"
            " / ばらつき（四分位範囲） 35.25 / p95 211.5"
            " / 最小 136 / 最大 148 / 試行数 9 / 欠測 3"
        )
        assert line_starting_with(body, "- 実測 hit_error_norm_final_mm:") == (
            "- 実測 hit_error_norm_final_mm: 代表値 72"
            " / ばらつき（四分位範囲） 18 / p95 108"
            " / 最小 67 / 最大 79 / 試行数 8 / 欠測 4"
        )

    def test_the_bias_component_lines_are_bound(self) -> None:
        body = section_of(render_summary(report()), "### 共通の偏り成分")
        assert line_starting_with(body, "- ベクトル:") == "- ベクトル: (23, -41) mm"
        assert line_starting_with(body, "- 大きさ:") == (
            "- 大きさ: 47.02 mm / 有意比（偏り÷ばらつき）: 3.75"
        )
        assert line_starting_with(body, "- World 固定方向") == (
            "- World 固定方向との角度差: 12.5 deg"
            " / カメラ視線方向との角度差: 71.25 deg"
        )
        assert line_starting_with(body, "- 2方向の縮退:") == "- 2方向の縮退: なし"
        assert line_starting_with(body, "- 帰属先:") == "- 帰属先: calibration"

    def test_the_scatter_component_lines_are_bound(self) -> None:
        body = section_of(render_summary(report()), "### ばらつき成分")
        assert line_starting_with(body, "- ばらつき（RMS）:") == (
            "- ばらつき（RMS）: 14.5 mm / 再抽出による見積もり: 9.25 mm"
        )
        assert line_starting_with(body, "- フィット残差") == (
            "- フィット残差の代表値: 5.125 mm"
        )
        assert line_starting_with(body, "- 帰属先:") == "- 帰属先: observation_noise"

    def test_each_range_band_keeps_its_own_bounds_and_error(self) -> None:
        body = section_of(render_summary(report()), "### 距離帯ごとの誤差")
        assert line_starting_with(body, "- 1000〜1500 mm:") == (
            "- 1000〜1500 mm: 平均誤差 31.5 mm / 投擲数 4"
        )
        assert line_starting_with(body, "- 1500〜2000 mm:") == (
            "- 1500〜2000 mm: 平均誤差 88.25 mm / 投擲数 6"
        )

    def test_the_oq27_latency_lines_do_not_swap_their_percentiles(self) -> None:
        """**律速段階の p95 と end-to-end の p95 は別の量である。**

        OQ-27 は Pi 4 を買い替えるかどうかという後戻りしにくい判断であり、
        その根拠として提示する律速段階の p95 を取り違えて表示すると、
        読み手は別の段階を疑いに行く。
        """
        body = section_of(render_summary(report()), OQ27_HEADING)
        assert line_starting_with(body, "- 律速段階:") == (
            "- 律速段階: capture（capture/frame/total_ms） p95 54.25 ms"
        )
        assert line_starting_with(body, "- end-to-end p95:") == (
            "- end-to-end p95: 163.75 ms / 同一測定から得た比較対象: 191.5 ms"
        )

    def test_the_improvement_history_binds_before_to_after(self) -> None:
        body = section_of(render_summary(report()), OQ27_HEADING)
        assert line_starting_with(body, "  - ＜局所リテラル: 適用済み") == (
            "  - ＜局所リテラル: 適用済みの改善項目＞: 適用=済"
            " / 適用前 process_fps=12.5 / 適用後 process_fps=21.75"
        )
        assert line_starting_with(body, "  - ＜局所リテラル: 未適用") == (
            "  - ＜局所リテラル: 未適用の改善項目＞: 適用=未"
            " / 適用前 記録なし / 適用後 記録なし"
        )

    def test_the_oq05_ratio_keeps_the_numerator_before_the_denominator(
        self,
    ) -> None:
        """**分子と分母を入れ替えると割合が読めなくなる。**"""
        body = section_of(render_summary(report()), OQ05_HEADING)
        assert line_starting_with(body, "- 窓に収まった割合:") == (
            "- 窓に収まった割合: 0.625（5 / 8）"
        )
        assert "（8 / 5）" not in body

    def test_the_oq05_window_and_required_trials_are_bound(self) -> None:
        body = section_of(render_summary(report()), OQ05_HEADING)
        assert line_starting_with(body, "- 暫定許容窓:") == "- 暫定許容窓: 67.5 mm"
        assert line_starting_with(body, "- 信頼水準") == (
            "- 信頼水準 0.85 での必要試行回数: 幅 0.20: 41、幅 0.10: 欠測"
        )

    def test_every_budget_row_shows_its_assumed_value_beside_the_measured_one(
        self,
    ) -> None:
        """**要件 11.2 の要約側そのもの。**

        「各区間について、想定値・実測の代表値・ばらつき・試行数を並べた形」
        を求めているのは要件 11.2 であり、タスク 7.1 の `_Requirements:` に
        挙がっている。**節で切り出すのが要点**である——想定値のリテラルは
        実測7項目の節にも出るので、要約全体への `in` では予算表の節が
        素通しになる。
        """
        body = section_of(render_summary(report()), BUDGET_HEADING)
        assert line_starting_with(body, "- 区間1 ") == (
            "- 区間1 ＜局所リテラル: 区間1 の見出し＞:"
            " 想定 ＜局所リテラル: 区間1 の想定値＞"
            " / 実測 代表値 63 / ばらつき 15.75 / 試行数 11"
        )
        assert line_starting_with(body, "- 区間2 ") == (
            "- 区間2 ＜局所リテラル: 区間2 の見出し（初回予測への読み替え）＞:"
            " 想定 ＜局所リテラル: 区間2 の想定値＞"
            " / 実測 代表値 128 / ばらつき 32 / 試行数 10"
        )
        assert line_starting_with(body, "- 区間3 ") == (
            "- 区間3 ＜局所リテラル: 区間3 の見出し＞:"
            " 想定 ＜局所リテラル: 区間3 の想定値＞ / 実測 欠測 / 試行数 0"
        )
        assert line_starting_with(body, "- 区間total ") == (
            "- 区間total ＜局所リテラル: 合計の見出し＞:"
            " 想定 ＜局所リテラル: オーバーヘッド合計の想定値＞"
            " / 実測 代表値 812 / ばらつき 203 / 試行数 3"
        )

    def test_each_budget_row_shows_one_trial_count_taken_from_the_row(
        self,
    ) -> None:
        """**「試行数」の見出しは1行に1つだけである。**

        `Distribution.count` と `BudgetRow.trials` はどちらも試行数だが別の
        フィールドであり、合計行では実際に別の数（12 と 3）になる。両方を
        同じ見出しで並べると、読み手はどちらがその区間の試行数なのか決め
        られない。行が持つ値（`BudgetRow.trials`）が正である。
        """
        body = section_of(render_summary(report()), BUDGET_HEADING)
        total = line_starting_with(body, "- 区間total ")
        assert total.count("試行数") == 1
        assert "試行数 3" in total
        assert "試行数 12" not in total
        for segment in ("1", "2", "3"):
            assert line_starting_with(body, f"- 区間{segment} ").count("試行数") == 1

    def test_the_derived_rows_of_the_budget_are_bound(self) -> None:
        body = section_of(render_summary(report()), BUDGET_HEADING)
        assert line_starting_with(body, "- 総飛行時間:") == (
            "- 総飛行時間: 想定 ＜局所リテラル: 総飛行時間の想定値＞"
            " / 実測 代表値 812 / ばらつき 203 / 試行数 12"
        )
        assert line_starting_with(body, "- 移動体に残された時間:") == (
            "- 移動体に残された時間:"
            " 想定 ＜局所リテラル: 移動体に残された時間の想定値＞"
            " / 実測 代表値 437 / ばらつき 109.25 / 試行数 5"
        )
        assert line_starting_with(body, "- 導出した予測レイテンシ") == (
            "- 導出した予測レイテンシの暫定目標: 237.5 ms"
            "（据え置いた区間3 の想定値 37 ms を含む）"
        )

    def test_the_overhead_verdict_line_is_bound(self) -> None:
        body = section_of(render_summary(report()), OVERHEAD_HEADING)
        assert line_starting_with(body, "- ＜局所リテラル: 予測区間＞:") == (
            "- ＜局所リテラル: 予測区間＞: 有意に変化しない=True"
            " / 中央値の差 0.25 ms"
            " / 基準（無効条件の四分位範囲） 0.9 ms"
            " / ＜局所リテラル: 判定の根拠＞"
        )


class TestTheMachineReadableReadingNotesAreFixedByValue:
    """**機械可読側の読み分け規則を値で固定する**（空振り形9）。

    既存の検査は公開フィールドと要約しか見ておらず、**JSON の
    `reading_notes` だけを空配列にする変異**が素通ししていた。要件 6.9 /
    6.11 の読み分け規則が機械可読側からだけ消える経路である。キー集合を見る
    検査は空配列を止められない。
    """

    def test_the_three_notes_appear_in_order_with_their_content(self) -> None:
        notes = report_to_dict(report())["attribution"]["reading_notes"]
        assert len(notes) == 3
        assert "合計の単一値へ畳まず" in notes[0]
        assert "ばらつき成分だけを読んで予測器を疑いに行かないこと" in notes[1]
        assert "遠方の帯でだけ誤差が大きい場合" in notes[2]

    def test_the_notes_are_not_emptied_or_blanked(self) -> None:
        notes = report_to_dict(report())["attribution"]["reading_notes"]
        assert notes != []
        assert all(note.strip() != "" for note in notes)

    def test_the_written_file_keeps_them_too(self, tmp_output_dir: Path) -> None:
        path = write_report(report(), tmp_output_dir, SESSION_ID)
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert len(loaded["attribution"]["reading_notes"]) == 3
        assert "合計の単一値へ畳まず" in loaded["attribution"]["reading_notes"][0]


# ---------------------------------------------------------------------------
# 15. 0件側の分岐へ入力を届ける
#
# `resource_saturated=None` の発見は単発ではなかった。`render_summary()` の
# 「0件側」「偽側」の分岐へ入力が1つも届いておらず、**0件を 0 や「なし」と
# 書く**変異が5件生き延びた（空振り形16「件数 0 / 1 / 2 の境界を全部通す」・
# 形17「非対称なガードは両側から通す」）。
# ---------------------------------------------------------------------------


def variant_report(
    *,
    attribution_result: AttributionResult | None = None,
    oq27_result: Oq27Result | None = None,
    oq05_result: Oq05Result | None = None,
    overhead_report: OverheadReport | None = None,
) -> M1Report:
    return build_report(
        session_id=SESSION_ID,
        aggregate=aggregate(),
        attribution=(
            attribution() if attribution_result is None else attribution_result
        ),
        oq27=oq27() if oq27_result is None else oq27_result,
        oq05=oq05() if oq05_result is None else oq05_result,
        budget=budget(),
        overhead=overhead() if overhead_report is None else overhead_report,
    )


class TestTheEmptyBranchesAreReached:
    """**0件側・偽側の分岐を、対照と対で通す。**

    「0件だった」と「0 だった」は別である。前者を後者として書くと、
    測っていないものが測った結果として読まれる——本 Spec が一貫して避けて
    いる誤読そのものである。
    """

    def test_no_range_band_is_written_as_missing_not_as_zero(self) -> None:
        summary = render_summary(
            variant_report(
                attribution_result=dataclasses.replace(attribution(), range_bands=())
            )
        )
        body = section_of(summary, "### 距離帯ごとの誤差")
        assert "- 帯に入った投擲が無い（欠測）" in body
        assert "0 mm" not in body
        assert "平均誤差" not in body

    def test_range_bands_are_still_listed_when_present(self) -> None:
        """**対の検査。** 常に「投擲が無い」と書く実装を殺す。"""
        body = section_of(render_summary(report()), "### 距離帯ごとの誤差")
        assert "帯に入った投擲が無い" not in body
        assert "平均誤差 31.5 mm" in body

    def test_no_improvement_history_is_written_as_no_record(self) -> None:
        """**履歴そのものが0件の分岐。**

        `_pairs({})`（前後の値が空）とは**別の分岐**である。前者は「適用した
        と言うが何が変わったか示せない項目」、後者は「§13.2 の項目を1件も
        記録していない」であり、次にやることが違う。
        """
        summary = render_summary(
            variant_report(oq27_result=dataclasses.replace(oq27(), improvements=()))
        )
        body = section_of(summary, OQ27_HEADING)
        assert "  - 記録なし" in body
        assert "改善は不要" not in body
        assert "適用=" not in body

    def test_an_improvement_without_before_and_after_says_no_record(self) -> None:
        """**対の検査（別の分岐）。** 履歴はあるが前後の値が空の場合。"""
        body = section_of(render_summary(report()), OQ27_HEADING)
        assert "適用前 記録なし / 適用後 記録なし" in body
        assert "  - 記録なし" not in body

    def test_no_limiting_condition_is_not_written_as_none_found(self) -> None:
        """**条件が挙がっていないことを「律速なし」と断定しない。**

        律速条件は「条件付き継続」のときにだけ内容を持つ。空であることは
        「律速が無かった」ではなく「この判定値では条件を挙げない」である。
        """
        summary = render_summary(
            variant_report(
                oq27_result=dataclasses.replace(oq27(), limiting_conditions=())
            )
        )
        body = section_of(summary, OQ27_HEADING)
        assert "- 律速している条件:" not in body
        assert "律速なし" not in summary

    def test_limiting_conditions_are_listed_when_present(self) -> None:
        """**対の検査。**"""
        body = section_of(render_summary(report()), OQ27_HEADING)
        assert "- 律速している条件: ＜局所リテラル: 律速している条件＞" in body

    def test_an_empty_required_trials_table_is_written_as_missing(self) -> None:
        summary = render_summary(
            variant_report(oq05_result=dataclasses.replace(oq05(), required_trials={}))
        )
        body = section_of(summary, OQ05_HEADING)
        assert "- 信頼水準 0.85 での必要試行回数: 欠測" in body
        assert "必要試行回数: 0" not in body

    def test_a_missing_bottleneck_is_written_as_missing(self) -> None:
        """律速段階が1つも測れていない場合。**段階名を捏造しない。**"""
        summary = render_summary(
            variant_report(
                oq27_result=dataclasses.replace(
                    oq27(),
                    bottleneck_stage=None,
                    bottleneck_label=None,
                    bottleneck_p95_ms=None,
                    end_to_end_p95_ms=None,
                )
            )
        )
        body = section_of(summary, OQ27_HEADING)
        assert "- 律速段階: 欠測（欠測） p95 欠測 ms" in body
        assert "- end-to-end p95: 欠測 ms" in body
        assert "p95 0 ms" not in body

    def test_no_missing_items_line_appears_when_the_gate_is_open(self) -> None:
        """**対の検査。** ゲートが開いているのに欠測列を出す実装を殺す。"""
        body = section_of(render_summary(report()), BUDGET_HEADING)
        assert "- 欠測している列:" not in body

    def test_no_warning_section_appears_when_there_is_nothing_to_warn_about(
        self,
    ) -> None:
        """**空の警告節を出さない。** 出すと警告そのものが読み飛ばされる。"""
        assert "## 警告" not in render_summary(report())
        assert "## 警告" in render_summary(report(verified=False))


class TestTheUnconditionalValidityNoteSurvives:
    """**判定が偽のときの「無条件に有効として扱わない」旨を落とさない**（要件 7.8）。

    `tasks.md` Implementation Notes のタスク6.4 が「タスク箇条『判定が偽の
    場合、当該条件の計測結果を無条件に有効として扱わない旨を出力に含める』は
    **落ちてしまえば満たせない**」と名指しで load-bearing だと宣言している当の
    一文である。既存のフィクスチャは常に判定が真（注記が `None`）だったため、
    要約の append を丸ごと削っても誰も気付かなかった。
    """

    NOTE = "＜局所リテラル: 当該条件の計測結果を無条件に有効として扱わない＞"

    def failed_overhead(self) -> OverheadReport:
        base = overhead()
        failed_verdict = dataclasses.replace(
            base.verdicts[0],
            passed=False,
            within_iqr=False,
            detail="＜局所リテラル: 判定が偽になった理由＞",
            unconditional_validity_note=self.NOTE,
        )
        return dataclasses.replace(
            base,
            verdicts=(failed_verdict,),
            unconditional_validity_note=self.NOTE,
            judgement=judgement(
                question=QUESTION_OVERHEAD,
                criterion=CRITERION_OVERHEAD,
                verdict="significantly_changed",
            ),
        )

    def test_the_note_reaches_the_summary_when_a_verdict_failed(self) -> None:
        summary = render_summary(
            variant_report(overhead_report=self.failed_overhead())
        )
        body = section_of(summary, OVERHEAD_HEADING)
        assert self.NOTE in body
        assert "有意に変化しない=False" in body
        assert "＜局所リテラル: 判定が偽になった理由＞" in body

    def test_the_note_is_absent_when_every_verdict_passed(self) -> None:
        """**対の検査。** 常に注記を出す実装は、真の判定にまで留保を付ける。"""
        body = section_of(render_summary(report()), OVERHEAD_HEADING)
        assert self.NOTE not in body
        assert "有意に変化しない=True" in body

    def test_the_note_reaches_the_machine_readable_output_too(self) -> None:
        payload = report_to_dict(
            variant_report(overhead_report=self.failed_overhead())
        )["overhead"]
        assert payload["unconditional_validity_note"] == self.NOTE
        assert payload["verdicts"][0]["unconditional_validity_note"] == self.NOTE
        assert payload["verdicts"][0]["passed"] is False
