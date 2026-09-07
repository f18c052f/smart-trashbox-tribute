"""OQ-27（Raspberry Pi 4 継続可否）の判定（タスク 6.1、要件 9.1-9.11）。

本ファイルが固定するのは**判断そのもの**である。OQ-27 は「Pi 4 を買い替えるか」
という後戻りできない判断に直結するため、次の3点を特に厚く固定する。

1. **改善項目の未適用が残っている間「不足」を返さない**（GATE 0。要件 9.3）。
   証跡（適用済みの項目と前後の計測値）が無ければ、性能不足を宣言しない。
2. **絶対値の目標を置かない**（要件 9.2）。比較対象は**同一測定から得た量**で
   あり、固定値・カタログ値・過去の記録ではない。したがって
   「同じレイテンシでも、同じ測定の中の比較対象が違えば判定が変わる」ことを
   固定する——比較対象を定数へ潰す実装はここで落ちる。
3. **判定規則の説明文が結果に埋め込まれる**（要件 9.1）。文面は
   **一文まるごとの肯定照合**で固定し、**取り違えた組み合わせの一文が
   含まれないこと**も併せて固定する（タスク 5.2 の教訓: 書式スロットを
   入れ替える変異が、単語を個別に `in` で見るテストを全通過した）。

期待値はすべて**テスト局所のリテラル**から組む。実装の定数を import して
自分自身と比べる検査は置かない（タスク 4.5 の教訓: ラベル定数を全部同じ
文字列へ潰しても通ってしまう）。
"""

from __future__ import annotations

import ast
import inspect
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from m1_validation import config as config_module
from m1_validation.config import M1Settings, Oq27Config, TrialLimits
from m1_validation.errors import M1ConfigError
from m1_validation.judgement import oq27 as oq27_module
from m1_validation.judgement.oq27 import (
    IMPROVEMENT_STEPS,
    ImprovementRecord,
    Oq27Result,
    judge_oq27,
    oq27_criterion,
)
from m1_validation.layout import ThrowLayout
from m1_validation.metrics.aggregate import (
    ITEM_DETECT_TO_FIRST_PREDICTION_MS,
    ITEM_KEYS,
    ITEM_RELEASE_TO_DETECT_MS,
    Distribution,
    ThrowAggregate,
)
from m1_validation.metrics.latency import LatencyResult, StageLatency
from m1_validation.types import Oq27Verdict

# ---------------------------------------------------------------------------
# テスト局所のリテラル（実装の定数を参照しない）
# ---------------------------------------------------------------------------

#: 判断の種類。`Judgement.question` に入る値。
QUESTION = "OQ-27"

#: 取得区間の段階名。規則3 が「取得区間が律速か」を見るときの名前である。
CAPTURE_STAGE = "capture"

#: `docs/development-environment.md §13.2` の改善項目（**順序も内容の一部**）。
STEP_KEYS = (
    "color_stream_reduction",
    "resolution",
    "roi",
    "fps",
    "image_processing_reduction",
    "point_cloud_avoidance",
    "detection_algorithm_simplification",
    "software_optimization",
)

#: 各項目の日本語ラベル（§13.2 の箇条そのもの）。
STEP_LABELS = {
    "color_stream_reduction": "Color stream 削減",
    "resolution": "Resolution 調整",
    "roi": "ROI 縮小",
    "fps": "FPS 調整",
    "image_processing_reduction": "不要な画像処理削減",
    "point_cloud_avoidance": "Point Cloud 全生成を回避",
    "detection_algorithm_simplification": "検出アルゴリズム簡略化",
    "software_optimization": "ソフトウェア最適化",
}

# --- 判定理由（**相互排他**。判定値が同じでも診断は別物である）--------------

R_GATE1_THROWS = "有効試行数が下限に満たない（GATE 1）。"
R_GATE1_SESSIONS = "セッション数が下限に満たない（GATE 1）。"
R_GATE2_NO_LIVE = (
    "実機由来の投擲が1件も無い（GATE 2）。"
    "合成・記録再生の結果を実機の結論として扱わない（要件 9.10）。"
)
R_CONTINUE = (
    "end-to-end の p95 は同一測定から得たオーバーヘッド相当値を超えない（規則1）。"
)
R_INSUFFICIENT = (
    "end-to-end の p95 が同一測定から得た比較対象を超え、"
    "計算資源が飽和している（規則2）。"
)
R_CONSTRAINED = (
    "end-to-end の p95 が同一測定から得た比較対象を超えるが、"
    "計算資源には余裕があり、律速段階は取得区間である（規則3）。"
)
R_NO_MEASUREMENT = (
    "end-to-end の p95 か同一測定から得た比較対象が欠測しており、"
    "規則1〜3を適用できない（規則4）。"
)
R_SATURATION_UNKNOWN = (
    "end-to-end の p95 が比較対象を超えるが、"
    "計算資源の飽和を判定する材料が1つも無い（規則4）。"
)
R_BOTTLENECK_NOT_ACQUISITION = (
    "end-to-end の p95 が比較対象を超え、計算資源には余裕があるが、"
    "律速段階が取得区間ではない（規則4）。"
)
R_GATE0_BLOCKED = (
    "規則2により「不足」と判定される状態だが、"
    "§13.2 の改善項目に未適用のものが残っているため「不足」を出さない（GATE 0）。"
)

#: すべての理由。**どの場面でもちょうど1つだけが現れる**ことを固定するために使う。
ALL_RATIONALES = (
    R_GATE1_THROWS,
    R_GATE1_SESSIONS,
    R_GATE2_NO_LIVE,
    R_CONTINUE,
    R_INSUFFICIENT,
    R_CONSTRAINED,
    R_NO_MEASUREMENT,
    R_SATURATION_UNKNOWN,
    R_BOTTLENECK_NOT_ACQUISITION,
    R_GATE0_BLOCKED,
)

# --- 同一測定の値（0 を既定にしない。タスク 2.2 の教訓）--------------------

#: 実測項目2 の代表値（リリース〜検出開始）。
RELEASE_TO_DETECT_MEDIAN_MS = 70.0
#: 実測項目3 の代表値（検出開始〜初回予測）。
DETECT_TO_FIRST_MEDIAN_MS = 130.0
#: 上2つの和。**同一測定から得たオーバーヘッド相当値**である。
OVERHEAD_REFERENCE_MS = 200.0

#: 余裕のある end-to-end の p95（規則1 が成立する）。
HEALTHY_P95_MS = 25.0
#: 比較対象を超える end-to-end の p95。
EXCEEDING_P95_MS = 260.0


# ---------------------------------------------------------------------------
# 小道具
# ---------------------------------------------------------------------------


def layout() -> ThrowLayout:
    return ThrowLayout(
        layout_id="layout-oq27-test",
        release_position_world_mm=(-1700.0, -50.0, 1690.0),
        release_height_mm=1690.0,
        throw_direction_deg=0.0,
        standby_position_world_mm=(1000.0, 1000.0),
        object_diameter_mm=65.0,
        aperture_diameter_mm=200.0,
        camera_position_world_mm=(0.0, -2500.0, 1200.0),
        notes="テスト用の仮値（確定ではない）",
    )


def settings(
    *,
    min_valid_throws: int = 5,
    min_sessions: int = 2,
    improvements_applied: Sequence[str] = (),
    cpu_saturation_ratio: float = 0.9,
    fps_shortfall_ratio: float = 0.95,
) -> M1Settings:
    return M1Settings(
        layout=layout(),
        trials=TrialLimits(
            min_valid_throws=min_valid_throws,
            min_sessions=min_sessions,
            require_live_source=False,
        ),
        oq27=Oq27Config(
            cpu_saturation_ratio=cpu_saturation_ratio,
            fps_shortfall_ratio=fps_shortfall_ratio,
        ),
        improvements_applied=tuple(improvements_applied),
    )


def stage_row(
    stage: str,
    *,
    p95_ms: float | None,
    event: str = "frame",
    field: str = "total_ms",
    count: int = 40,
) -> StageLatency:
    """段階別レイテンシの1行。**p95 以外は判定に効かない**ことを前提にしない。"""
    return StageLatency(
        stage=stage,
        event=event,
        field=field,
        source="log",
        count=count,
        p50_ms=None if p95_ms is None else p95_ms / 2.0,
        p95_ms=p95_ms,
        mean_ms=None if p95_ms is None else p95_ms / 1.8,
        min_ms=None if p95_ms is None else p95_ms / 4.0,
        max_ms=None if p95_ms is None else p95_ms * 1.1,
    )


#: 取得区間が律速の内訳（capture が最大、track が最小）。
CAPTURE_BOUND_STAGES = (
    stage_row(CAPTURE_STAGE, p95_ms=180.0),
    stage_row("detect", p95_ms=40.0),
    stage_row("track", p95_ms=25.0),
)

#: 処理側が律速の内訳（detect が最大、capture が最小）。
COMPUTE_BOUND_STAGES = (
    stage_row(CAPTURE_STAGE, p95_ms=20.0),
    stage_row("detect", p95_ms=190.0),
    stage_row("track", p95_ms=30.0),
)


def latency(
    *,
    end_to_end_p95_ms: float | None,
    stages: Sequence[StageLatency] = CAPTURE_BOUND_STAGES,
    capture_fps: float | None = 30.0,
    process_fps: float | None = 29.7,
    cpu_percent_mean: float | None = 40.0,
    frames_dropped: int | None = 0,
    frames_missing: int | None = 3,
) -> LatencyResult:
    return LatencyResult(
        definition="テスト局所の end-to-end 定義文",
        first_prediction_basis="テスト局所の初回予測基準文",
        stage_note="テスト局所の段階注記",
        stages=tuple(stages),
        end_to_end=StageLatency(
            stage="predict",
            event="update",
            field="end_to_end_ms",
            source="derived",
            count=0 if end_to_end_p95_ms is None else 57,
            p50_ms=None if end_to_end_p95_ms is None else end_to_end_p95_ms / 2.5,
            p95_ms=end_to_end_p95_ms,
            mean_ms=None if end_to_end_p95_ms is None else end_to_end_p95_ms / 2.2,
            min_ms=None if end_to_end_p95_ms is None else end_to_end_p95_ms / 6.0,
            max_ms=None if end_to_end_p95_ms is None else end_to_end_p95_ms * 1.3,
        ),
        detect_to_first_prediction=(),
        capture_fps=capture_fps,
        process_fps=process_fps,
        cpu_percent_mean=cpu_percent_mean,
        rss_bytes_max=734003200,
        frames_dropped=frames_dropped,
        frames_missing=frames_missing,
        unknown_stages=(),
        foreign_prediction_events=0,
        unusable_prediction_events=0,
        log_lines_dropped=0,
        log_lines_skipped=0,
    )


def dist(median: float | None, *, iqr: float | None = None) -> Distribution:
    if median is None:
        return Distribution(
            count=0, median=None, p95=None, iqr=None, minimum=None, maximum=None,
            missing=8,
        )
    return Distribution(
        count=8,
        median=median,
        p95=median * 1.4,
        iqr=median * 0.3 if iqr is None else iqr,
        minimum=median * 0.7,
        maximum=median * 1.6,
        missing=0,
    )


def items(
    *,
    release_to_detect_ms: float | None = RELEASE_TO_DETECT_MEDIAN_MS,
    detect_to_first_ms: float | None = DETECT_TO_FIRST_MEDIAN_MS,
) -> Mapping[str, Distribution]:
    table = {key: dist(None) for key in ITEM_KEYS}
    table[ITEM_RELEASE_TO_DETECT_MS] = dist(release_to_detect_ms)
    table[ITEM_DETECT_TO_FIRST_PREDICTION_MS] = dist(detect_to_first_ms)
    return table


def aggregate(
    *,
    valid_throw_count: int = 8,
    session_ids: Sequence[str] = ("session-a", "session-b"),
    live_throw_count: int = 3,
    provisional: bool = False,
    verified: bool = True,
    release_to_detect_ms: float | None = RELEASE_TO_DETECT_MEDIAN_MS,
    detect_to_first_ms: float | None = DETECT_TO_FIRST_MEDIAN_MS,
) -> ThrowAggregate:
    return ThrowAggregate(
        calibration_id="cal-oq27-0001",
        verified=verified,
        session_ids=tuple(session_ids),
        throw_count=9,
        failed_throw_count=1,
        valid_throw_count=valid_throw_count,
        live_throw_count=live_throw_count,
        converged_count=6,
        not_converged_count=2,
        not_measurable_count=0,
        single_prediction_throw_count=0,
        provisional=provisional,
        provisional_reasons=("insufficient_valid_throws",) if provisional else (),
        items=items(
            release_to_detect_ms=release_to_detect_ms,
            detect_to_first_ms=detect_to_first_ms,
        ),
        error_vectors=((12.0, -7.0), (9.0, 3.0)),
        per_throw=(),
    )


def full_improvements(
    *,
    omit: str | None = None,
    without_before: str | None = None,
    without_after: str | None = None,
    not_applied: str | None = None,
) -> tuple[ImprovementRecord, ...]:
    """§13.2 の全項目について、前後の計測値を伴う証跡を組む。

    引数で1件だけ欠けさせられるようにしてあるのは、**証跡のどの部分が
    GATE 0 を開けているのか**を1件ずつ確かめるためである。
    """
    rows: list[ImprovementRecord] = []
    for index, step in enumerate(STEP_KEYS):
        if step == omit:
            continue
        rows.append(
            ImprovementRecord(
                step=step,
                applied=step != not_applied,
                before={} if step == without_before else {"end_to_end_p95_ms": 300.0 + index},
                after={} if step == without_after else {"end_to_end_p95_ms": 260.0 + index},
            )
        )
    return tuple(rows)


def rationales_present(result: Oq27Result) -> tuple[str, ...]:
    """結果の説明に現れた理由（**ちょうど1つ**であることを確かめるため）。"""
    return tuple(text for text in ALL_RATIONALES if text in result.judgement.rationale)


# ---------------------------------------------------------------------------
# 4値それぞれへ分岐する
# ---------------------------------------------------------------------------


class TestFourVerdicts:
    """判定値は「継続 / 条件付き継続 / 不足 / 保留」の4値である（要件 9.1）。"""

    def test_continue_when_p95_does_not_exceed_the_same_measurement_reference(
        self,
    ) -> None:
        """規則1: p95 が同一測定のオーバーヘッド相当値を超えない → 継続。"""
        result = judge_oq27(
            latency(end_to_end_p95_ms=HEALTHY_P95_MS),
            aggregate(),
            settings=settings(improvements_applied=STEP_KEYS),
            improvements=full_improvements(),
        )
        assert result.verdict is Oq27Verdict.CONTINUE
        assert result.judgement.verdict == "continue"
        assert rationales_present(result) == (R_CONTINUE,)
        assert result.end_to_end_p95_ms == HEALTHY_P95_MS
        assert result.overhead_reference_ms == OVERHEAD_REFERENCE_MS
        assert result.resource_saturated is False
        assert result.limiting_conditions == ()
        assert result.judgement.provisional is False

    def test_insufficient_when_it_exceeds_and_resources_are_saturated(self) -> None:
        """規則2: 超過 ＋ 資源の飽和 → 不足（GATE 0 が開いているとき）。"""
        result = judge_oq27(
            latency(end_to_end_p95_ms=EXCEEDING_P95_MS, frames_dropped=4),
            aggregate(),
            settings=settings(improvements_applied=STEP_KEYS),
            improvements=full_improvements(),
        )
        assert result.verdict is Oq27Verdict.INSUFFICIENT
        assert result.judgement.verdict == "insufficient"
        assert rationales_present(result) == (R_INSUFFICIENT,)
        assert result.resource_saturated is True
        assert result.end_to_end_p95_ms == EXCEEDING_P95_MS
        assert result.overhead_reference_ms == OVERHEAD_REFERENCE_MS

    def test_continue_with_constraints_when_acquisition_is_the_bottleneck(self) -> None:
        """規則3: 超過 ＋ 資源に余裕 ＋ 取得区間が律速 → 条件付き継続。"""
        result = judge_oq27(
            latency(end_to_end_p95_ms=EXCEEDING_P95_MS, stages=CAPTURE_BOUND_STAGES),
            aggregate(),
            settings=settings(),
        )
        assert result.verdict is Oq27Verdict.CONTINUE_WITH_CONSTRAINTS
        assert result.judgement.verdict == "continue_with_constraints"
        assert rationales_present(result) == (R_CONSTRAINED,)
        assert result.resource_saturated is False
        assert result.bottleneck_stage == CAPTURE_STAGE

    def test_deferred_when_trial_count_is_below_the_limit(self) -> None:
        """GATE 1: 有効試行数の下限未達 → 保留。"""
        result = judge_oq27(
            latency(end_to_end_p95_ms=HEALTHY_P95_MS),
            aggregate(valid_throw_count=4),
            settings=settings(min_valid_throws=5),
        )
        assert result.verdict is Oq27Verdict.DEFERRED
        assert result.judgement.verdict == "deferred"
        assert rationales_present(result) == (R_GATE1_THROWS,)
        assert result.judgement.provisional is True

    def test_the_four_verdicts_are_distinct(self) -> None:
        """4値が互いに異なる（全部が同じ値を返す実装を落とす）。

        個別の期待値だけを見ていると、**判定値のラベルを取り違えた実装**でも
        一部のテストが通ってしまう（タスク 5.3 の教訓）。
        """
        verdicts = {
            judge_oq27(
                latency(end_to_end_p95_ms=HEALTHY_P95_MS),
                aggregate(),
                settings=settings(),
            ).verdict,
            judge_oq27(
                latency(end_to_end_p95_ms=EXCEEDING_P95_MS, frames_dropped=4),
                aggregate(),
                settings=settings(improvements_applied=STEP_KEYS),
                improvements=full_improvements(),
            ).verdict,
            judge_oq27(
                latency(end_to_end_p95_ms=EXCEEDING_P95_MS),
                aggregate(),
                settings=settings(),
            ).verdict,
            judge_oq27(
                latency(end_to_end_p95_ms=HEALTHY_P95_MS),
                aggregate(valid_throw_count=4),
                settings=settings(min_valid_throws=5),
            ).verdict,
        }
        assert verdicts == {
            Oq27Verdict.CONTINUE,
            Oq27Verdict.INSUFFICIENT,
            Oq27Verdict.CONTINUE_WITH_CONSTRAINTS,
            Oq27Verdict.DEFERRED,
        }


# ---------------------------------------------------------------------------
# GATE 0（★ 本タスクの核心）
# ---------------------------------------------------------------------------


class TestGateZeroImprovements:
    """改善項目の未適用が残る間「不足」を返さない（要件 9.3 / 9.4）。

    これは「性能不足を宣言する前に、まだ手を打てる余地が無いことを示せ」と
    いう規則である。**証跡（適用済みの項目と前後の計測値）を要求する。**
    """

    def _saturated_case(
        self,
        *,
        applied: Sequence[str],
        improvements: Sequence[ImprovementRecord],
    ) -> Oq27Result:
        """規則2 が成立する（＝ GATE 0 が無ければ「不足」になる）入力。"""
        return judge_oq27(
            latency(end_to_end_p95_ms=EXCEEDING_P95_MS, frames_dropped=4),
            aggregate(),
            settings=settings(improvements_applied=applied),
            improvements=improvements,
        )

    def test_no_improvement_evidence_at_all_never_yields_insufficient(self) -> None:
        result = self._saturated_case(applied=(), improvements=())
        assert result.verdict is Oq27Verdict.DEFERRED
        assert rationales_present(result) == (R_GATE0_BLOCKED,)
        assert result.judgement.provisional is True

    def test_one_missing_step_still_blocks_insufficient(self) -> None:
        """1項目でも未適用なら「不足」を出さない。"""
        remaining = [step for step in STEP_KEYS if step != "roi"]
        result = self._saturated_case(
            applied=remaining, improvements=full_improvements(omit="roi")
        )
        assert result.verdict is Oq27Verdict.DEFERRED
        assert rationales_present(result) == (R_GATE0_BLOCKED,)

    def test_before_measurements_are_required_as_evidence(self) -> None:
        """**前**の計測値が無い項目は適用済みと認めない（要件 9.4）。"""
        result = self._saturated_case(
            applied=STEP_KEYS, improvements=full_improvements(without_before="fps")
        )
        assert result.verdict is Oq27Verdict.DEFERRED
        assert rationales_present(result) == (R_GATE0_BLOCKED,)

    def test_after_measurements_are_required_as_evidence(self) -> None:
        """**後**の計測値が無い項目は適用済みと認めない（要件 9.4）。"""
        result = self._saturated_case(
            applied=STEP_KEYS, improvements=full_improvements(without_after="fps")
        )
        assert result.verdict is Oq27Verdict.DEFERRED
        assert rationales_present(result) == (R_GATE0_BLOCKED,)

    def test_a_record_marked_not_applied_does_not_open_the_gate(self) -> None:
        result = self._saturated_case(
            applied=STEP_KEYS,
            improvements=full_improvements(not_applied="software_optimization"),
        )
        assert result.verdict is Oq27Verdict.DEFERRED
        assert rationales_present(result) == (R_GATE0_BLOCKED,)

    def test_evidence_without_the_declared_setting_does_not_open_the_gate(self) -> None:
        """証跡があっても設定に名前が無ければ適用済みと認めない。

        `improvements_applied` は「実験者が何を適用したと申告しているか」であり、
        証跡は「その申告を裏づける計測値」である。**片方だけでは開かない。**
        """
        result = self._saturated_case(applied=(), improvements=full_improvements())
        assert result.verdict is Oq27Verdict.DEFERRED
        assert rationales_present(result) == (R_GATE0_BLOCKED,)

    def test_full_evidence_opens_the_gate(self) -> None:
        result = self._saturated_case(
            applied=STEP_KEYS, improvements=full_improvements()
        )
        assert result.verdict is Oq27Verdict.INSUFFICIENT
        assert rationales_present(result) == (R_INSUFFICIENT,)

    def test_insufficient_implies_every_step_is_applied(self) -> None:
        """design.md の Postcondition: 不足なら全項目が適用済みである。"""
        result = self._saturated_case(
            applied=STEP_KEYS, improvements=full_improvements()
        )
        assert result.verdict is Oq27Verdict.INSUFFICIENT
        assert tuple(row.step for row in result.improvements) == STEP_KEYS
        assert all(row.applied for row in result.improvements)

    def test_the_gate_only_blocks_insufficient(self) -> None:
        """GATE 0 は「不足」だけを止める。継続まで保留にしない。

        GATE 0 を「未適用なら常に保留」に読み替えると、**改善を1つも適用して
        いない健全な測定まで判断不能になる**。止めるのは購入判断に直結する
        「不足」だけである。
        """
        result = judge_oq27(
            latency(end_to_end_p95_ms=HEALTHY_P95_MS),
            aggregate(),
            settings=settings(improvements_applied=()),
        )
        assert result.verdict is Oq27Verdict.CONTINUE
        assert rationales_present(result) == (R_CONTINUE,)
        # 判断に用いてよい状態ではない旨は暫定の印で示す（要件 9.3）。
        assert result.judgement.provisional is True

    def test_every_step_of_section_13_2_is_covered_in_order(self) -> None:
        """§13.2 の8項目が順序どおりに揃っている。"""
        assert IMPROVEMENT_STEPS == STEP_KEYS

    def test_the_result_lists_all_steps_with_their_measurements(self) -> None:
        """未適用の項目も**行としては残る**（何が残っているかを読むため）。"""
        result = self._saturated_case(
            applied=[step for step in STEP_KEYS if step != "roi"],
            improvements=full_improvements(omit="roi"),
        )
        rows = {row.step: row for row in result.improvements}
        assert set(rows) == set(STEP_KEYS)
        assert rows["roi"].applied is False
        assert rows["roi"].before == {}
        assert rows["roi"].after == {}
        assert rows["fps"].applied is True
        assert rows["fps"].before == {"end_to_end_p95_ms": 303.0}
        assert rows["fps"].after == {"end_to_end_p95_ms": 263.0}

    def test_unknown_step_in_the_settings_is_rejected(self) -> None:
        """§13.2 に無い項目名は拒否する（綴り間違いが黙って通らない）。"""
        with pytest.raises(M1ConfigError):
            judge_oq27(
                latency(end_to_end_p95_ms=HEALTHY_P95_MS),
                aggregate(),
                settings=settings(improvements_applied=("roi", "overclock")),
            )

    def test_unknown_step_in_the_evidence_is_rejected(self) -> None:
        with pytest.raises(M1ConfigError):
            judge_oq27(
                latency(end_to_end_p95_ms=HEALTHY_P95_MS),
                aggregate(),
                settings=settings(),
                improvements=(
                    ImprovementRecord(
                        step="overclock", applied=True, before={"a": 1.0}, after={"a": 2.0}
                    ),
                ),
            )

    def test_duplicated_step_in_the_evidence_is_rejected(self) -> None:
        with pytest.raises(M1ConfigError):
            judge_oq27(
                latency(end_to_end_p95_ms=HEALTHY_P95_MS),
                aggregate(),
                settings=settings(),
                improvements=(
                    ImprovementRecord(
                        step="roi", applied=True, before={"a": 1.0}, after={"a": 2.0}
                    ),
                    ImprovementRecord(
                        step="roi", applied=True, before={"a": 3.0}, after={"a": 4.0}
                    ),
                ),
            )


# ---------------------------------------------------------------------------
# 保留（GATE 1 / GATE 2）
# ---------------------------------------------------------------------------


class TestDeferralGates:
    """保留の3条件（要件 9.9 / 9.10）。**1つずつ落ちること**を確かめる。

    3条件をまとめて1つの入力で見ると、条件を1つ落とす変異が素通りする。
    それぞれ「その条件だけが成立しない入力」を用意する——他の2条件は満たして
    いるので、落とした条件が効いていなければ判定は継続へ倒れる。
    """

    def test_only_the_throw_count_fails(self) -> None:
        result = judge_oq27(
            latency(end_to_end_p95_ms=HEALTHY_P95_MS),
            aggregate(valid_throw_count=4, session_ids=("s-1", "s-2"), live_throw_count=3),
            settings=settings(min_valid_throws=5, min_sessions=2),
        )
        assert result.verdict is Oq27Verdict.DEFERRED
        assert rationales_present(result) == (R_GATE1_THROWS,)

    def test_only_the_session_count_fails(self) -> None:
        result = judge_oq27(
            latency(end_to_end_p95_ms=HEALTHY_P95_MS),
            aggregate(valid_throw_count=8, session_ids=("s-1",), live_throw_count=3),
            settings=settings(min_valid_throws=5, min_sessions=2),
        )
        assert result.verdict is Oq27Verdict.DEFERRED
        assert rationales_present(result) == (R_GATE1_SESSIONS,)

    def test_only_the_live_source_fails(self) -> None:
        """**実機由来の投擲が無い**場合は保留（要件 9.10）。

        合成・記録再生だけで得た数字を実機の結論として扱うと、
        **実機で一度も動かしていないまま Pi 4 の可否を決めることになる。**
        """
        result = judge_oq27(
            latency(end_to_end_p95_ms=HEALTHY_P95_MS),
            aggregate(valid_throw_count=8, session_ids=("s-1", "s-2"), live_throw_count=0),
            settings=settings(min_valid_throws=5, min_sessions=2),
        )
        assert result.verdict is Oq27Verdict.DEFERRED
        assert rationales_present(result) == (R_GATE2_NO_LIVE,)

    def test_the_boundary_of_the_throw_count_is_inclusive(self) -> None:
        """下限**ちょうど**は保留にしない（`<` であって `<=` ではない）。"""
        at_limit = judge_oq27(
            latency(end_to_end_p95_ms=HEALTHY_P95_MS),
            aggregate(valid_throw_count=5),
            settings=settings(min_valid_throws=5),
        )
        below = judge_oq27(
            latency(end_to_end_p95_ms=HEALTHY_P95_MS),
            aggregate(valid_throw_count=4),
            settings=settings(min_valid_throws=5),
        )
        assert at_limit.verdict is Oq27Verdict.CONTINUE
        assert below.verdict is Oq27Verdict.DEFERRED

    def test_the_boundary_of_the_session_count_is_inclusive(self) -> None:
        at_limit = judge_oq27(
            latency(end_to_end_p95_ms=HEALTHY_P95_MS),
            aggregate(session_ids=("s-1", "s-2")),
            settings=settings(min_sessions=2),
        )
        below = judge_oq27(
            latency(end_to_end_p95_ms=HEALTHY_P95_MS),
            aggregate(session_ids=("s-1",)),
            settings=settings(min_sessions=2),
        )
        assert at_limit.verdict is Oq27Verdict.CONTINUE
        assert below.verdict is Oq27Verdict.DEFERRED

    def test_the_limits_come_from_the_settings(self) -> None:
        """**下限を渡すだけでなく、その下限で判定が動く**ことを固定する。

        同じ集計に対して下限だけを変えると判定が変わる。設定値を記録しながら
        別の値で判定する実装（タスク 5.2 で実在した空振り）はここで落ちる。
        """
        counted = aggregate(valid_throw_count=6, session_ids=("s-1", "s-2"))
        strict = judge_oq27(
            latency(end_to_end_p95_ms=HEALTHY_P95_MS),
            counted,
            settings=settings(min_valid_throws=7),
        )
        loose = judge_oq27(
            latency(end_to_end_p95_ms=HEALTHY_P95_MS),
            counted,
            settings=settings(min_valid_throws=6),
        )
        assert strict.verdict is Oq27Verdict.DEFERRED
        assert loose.verdict is Oq27Verdict.CONTINUE

    def test_the_session_limit_comes_from_the_settings(self) -> None:
        counted = aggregate(session_ids=("s-1", "s-2", "s-3"))
        strict = judge_oq27(
            latency(end_to_end_p95_ms=HEALTHY_P95_MS),
            counted,
            settings=settings(min_sessions=4),
        )
        loose = judge_oq27(
            latency(end_to_end_p95_ms=HEALTHY_P95_MS),
            counted,
            settings=settings(min_sessions=3),
        )
        assert strict.verdict is Oq27Verdict.DEFERRED
        assert loose.verdict is Oq27Verdict.CONTINUE


class TestGateOrdering:
    """判定の順序（保留ゲート → 本判定 → GATE 0 の veto）。

    **順序が実際に働く入力で確かめる。** 分岐がそもそも成立しない入力では
    順序の取り違えが素通りする（タスク 5.2 の教訓）。
    """

    def test_deferral_gates_run_before_the_main_rules(self) -> None:
        """本判定なら「継続」になる入力でも、ゲート未達なら保留である。

        順序を入れ替えて本判定を先に置くと、この入力は「継続」を返す。
        """
        result = judge_oq27(
            latency(end_to_end_p95_ms=HEALTHY_P95_MS),
            aggregate(valid_throw_count=2),
            settings=settings(min_valid_throws=5),
        )
        assert result.verdict is Oq27Verdict.DEFERRED
        assert rationales_present(result) == (R_GATE1_THROWS,)

    def test_live_gate_runs_before_the_main_rules(self) -> None:
        result = judge_oq27(
            latency(end_to_end_p95_ms=HEALTHY_P95_MS),
            aggregate(live_throw_count=0),
            settings=settings(),
        )
        assert result.verdict is Oq27Verdict.DEFERRED
        assert rationales_present(result) == (R_GATE2_NO_LIVE,)

    def test_deferral_gates_are_reported_before_gate_zero(self) -> None:
        """試行数不足と改善未適用が同時に成立する場合、**診断は試行数側**。

        どちらも判定値は保留なので `verdict` では区別が付かない。しかし
        「投げる回数を増やす」と「改善項目を適用する」は**やることが違う**
        ので、どちらを報告するかは読み手にとって別物である。
        """
        result = judge_oq27(
            latency(end_to_end_p95_ms=EXCEEDING_P95_MS, frames_dropped=4),
            aggregate(valid_throw_count=2),
            settings=settings(min_valid_throws=5, improvements_applied=()),
        )
        assert result.verdict is Oq27Verdict.DEFERRED
        assert rationales_present(result) == (R_GATE1_THROWS,)

    def test_the_throw_gate_is_reported_before_the_session_gate(self) -> None:
        result = judge_oq27(
            latency(end_to_end_p95_ms=HEALTHY_P95_MS),
            aggregate(valid_throw_count=2, session_ids=("s-1",)),
            settings=settings(min_valid_throws=5, min_sessions=2),
        )
        assert rationales_present(result) == (R_GATE1_THROWS,)

    def test_the_session_gate_is_reported_before_the_live_gate(self) -> None:
        result = judge_oq27(
            latency(end_to_end_p95_ms=HEALTHY_P95_MS),
            aggregate(session_ids=("s-1",), live_throw_count=0),
            settings=settings(min_sessions=2),
        )
        assert rationales_present(result) == (R_GATE1_SESSIONS,)


# ---------------------------------------------------------------------------
# 比較対象は同一測定から得る（★ 絶対値の目標を置かない）
# ---------------------------------------------------------------------------


class TestOverheadReference:
    """比較対象は**同一測定から得た量**である（要件 9.2 / 9.6）。

    別の測定・カタログ値・過去の記録と比べない。したがって
    **同じレイテンシでも、同じ測定の中の比較対象が違えば判定が変わる。**
    """

    def test_the_reference_is_the_sum_of_two_measured_items(self) -> None:
        result = judge_oq27(
            latency(end_to_end_p95_ms=HEALTHY_P95_MS),
            aggregate(release_to_detect_ms=90.0, detect_to_first_ms=160.0),
            settings=settings(),
        )
        assert result.overhead_reference_ms == 250.0

    def test_a_larger_reference_from_the_same_measurement_flips_the_verdict(self) -> None:
        """比較対象を固定値へ潰す実装はここで落ちる。

        end-to-end はどちらも同じ 260 ms である。違うのは**同じ測定から得た
        比較対象**だけであり、それだけで判定が変わらなければならない。
        """
        small = judge_oq27(
            latency(end_to_end_p95_ms=EXCEEDING_P95_MS, frames_dropped=4),
            aggregate(release_to_detect_ms=70.0, detect_to_first_ms=130.0),
            settings=settings(improvements_applied=STEP_KEYS),
            improvements=full_improvements(),
        )
        large = judge_oq27(
            latency(end_to_end_p95_ms=EXCEEDING_P95_MS, frames_dropped=4),
            aggregate(release_to_detect_ms=300.0, detect_to_first_ms=500.0),
            settings=settings(improvements_applied=STEP_KEYS),
            improvements=full_improvements(),
        )
        assert small.verdict is Oq27Verdict.INSUFFICIENT
        assert large.verdict is Oq27Verdict.CONTINUE
        assert small.overhead_reference_ms == 200.0
        assert large.overhead_reference_ms == 800.0

    def test_both_items_enter_the_reference(self) -> None:
        """**2項目の和**である（片方だけを使う実装を落とす）。

        p95 = 150 ms は 70 + 130 = 200 を超えないが、70 だけ・130 だけの
        どちらとも比べても超える。
        """
        result = judge_oq27(
            latency(end_to_end_p95_ms=150.0, frames_dropped=4),
            aggregate(release_to_detect_ms=70.0, detect_to_first_ms=130.0),
            settings=settings(improvements_applied=STEP_KEYS),
            improvements=full_improvements(),
        )
        assert result.verdict is Oq27Verdict.CONTINUE
        assert result.overhead_reference_ms == 200.0

    def test_equal_values_do_not_count_as_exceeding(self) -> None:
        """境界: **ちょうど等しい値は「超えない」に含む**（要件 9.6）。"""
        equal = judge_oq27(
            latency(end_to_end_p95_ms=200.0, frames_dropped=4),
            aggregate(),
            settings=settings(improvements_applied=STEP_KEYS),
            improvements=full_improvements(),
        )
        just_above = judge_oq27(
            latency(end_to_end_p95_ms=200.5, frames_dropped=4),
            aggregate(),
            settings=settings(improvements_applied=STEP_KEYS),
            improvements=full_improvements(),
        )
        assert equal.verdict is Oq27Verdict.CONTINUE
        assert just_above.verdict is Oq27Verdict.INSUFFICIENT

    def test_missing_release_to_detect_makes_the_reference_unavailable(self) -> None:
        result = judge_oq27(
            latency(end_to_end_p95_ms=HEALTHY_P95_MS),
            aggregate(release_to_detect_ms=None),
            settings=settings(),
        )
        assert result.verdict is Oq27Verdict.DEFERRED
        assert rationales_present(result) == (R_NO_MEASUREMENT,)
        assert result.overhead_reference_ms is None

    def test_missing_detect_to_first_prediction_makes_it_unavailable(self) -> None:
        result = judge_oq27(
            latency(end_to_end_p95_ms=HEALTHY_P95_MS),
            aggregate(detect_to_first_ms=None),
            settings=settings(),
        )
        assert result.verdict is Oq27Verdict.DEFERRED
        assert rationales_present(result) == (R_NO_MEASUREMENT,)
        assert result.overhead_reference_ms is None

    def test_missing_end_to_end_p95_is_reported_as_unmeasured(self) -> None:
        result = judge_oq27(
            latency(end_to_end_p95_ms=None),
            aggregate(),
            settings=settings(),
        )
        assert result.verdict is Oq27Verdict.DEFERRED
        assert rationales_present(result) == (R_NO_MEASUREMENT,)
        assert result.end_to_end_p95_ms is None
        # **0 で埋めない**（「0 ms で予測が出た」と「測っていない」は別）。
        assert result.overhead_reference_ms == OVERHEAD_REFERENCE_MS


# ---------------------------------------------------------------------------
# 律速段階
# ---------------------------------------------------------------------------


class TestBottleneckStage:
    """段階別レイテンシの内訳から律速段階を特定する（要件 9.5）。"""

    def test_the_largest_p95_is_the_bottleneck(self) -> None:
        result = judge_oq27(
            latency(end_to_end_p95_ms=HEALTHY_P95_MS, stages=COMPUTE_BOUND_STAGES),
            aggregate(),
            settings=settings(),
        )
        assert result.bottleneck_stage == "detect"
        assert result.bottleneck_label == "detect/frame/total_ms"
        assert result.bottleneck_p95_ms == 190.0

    def test_the_bottleneck_is_not_the_smallest_stage(self) -> None:
        """最大でなく最小を採る変異を落とす（内訳の最小は track である）。"""
        result = judge_oq27(
            latency(end_to_end_p95_ms=HEALTHY_P95_MS, stages=CAPTURE_BOUND_STAGES),
            aggregate(),
            settings=settings(),
        )
        assert result.bottleneck_stage == CAPTURE_STAGE
        assert result.bottleneck_p95_ms == 180.0

    def test_ties_are_broken_by_the_lexicographic_order_of_the_row(self) -> None:
        """同値なら段階名・イベント名・フィールド名の辞書順で先の行を採る。

        並びが入力順に依存すると、同じ測定を別の順で読んだだけで律速段階が
        変わる（要件 12.4 の決定性が壊れる）。
        """
        rows = (
            stage_row("track", p95_ms=120.0),
            stage_row("detect", p95_ms=120.0),
        )
        result = judge_oq27(
            latency(end_to_end_p95_ms=HEALTHY_P95_MS, stages=rows),
            aggregate(),
            settings=settings(),
        )
        assert result.bottleneck_stage == "detect"

    def test_rows_without_a_p95_are_not_candidates(self) -> None:
        rows = (
            stage_row("calibrate", p95_ms=None),
            stage_row("track", p95_ms=45.0),
        )
        result = judge_oq27(
            latency(end_to_end_p95_ms=HEALTHY_P95_MS, stages=rows),
            aggregate(),
            settings=settings(),
        )
        assert result.bottleneck_stage == "track"

    def test_stages_without_any_p95_leave_the_bottleneck_unknown(self) -> None:
        """p95 を持つ行が1つも無ければ律速段階は不明である。

        欠測を 0 とみなして並べると、**測っていない段階が「常に速い段階」に
        見える**——そして候補が全部欠測なら、辞書順で先の段階が律速として
        名指しされてしまう。取得区間の名前がたまたま先に来れば、
        資源を測っていない測定が「条件付き継続」まで進む。
        """
        rows = (
            stage_row(CAPTURE_STAGE, p95_ms=None),
            stage_row("detect", p95_ms=None),
        )
        result = judge_oq27(
            latency(end_to_end_p95_ms=EXCEEDING_P95_MS, stages=rows),
            aggregate(),
            settings=settings(),
        )
        assert result.bottleneck_stage is None
        assert result.bottleneck_p95_ms is None
        assert result.verdict is Oq27Verdict.DEFERRED
        assert rationales_present(result) == (R_BOTTLENECK_NOT_ACQUISITION,)

    def test_no_stage_rows_leaves_the_bottleneck_unknown(self) -> None:
        result = judge_oq27(
            latency(end_to_end_p95_ms=HEALTHY_P95_MS, stages=()),
            aggregate(),
            settings=settings(),
        )
        assert result.bottleneck_stage is None
        assert result.bottleneck_label is None
        assert result.bottleneck_p95_ms is None

    def test_a_compute_bottleneck_does_not_yield_conditional_continuation(self) -> None:
        """超過 ＋ 資源に余裕でも、律速が取得区間でなければ条件付き継続にしない。

        取得条件を絞れば直るのかどうかが分からないまま「条件付き継続」と
        言うと、**存在しない改善余地を約束する**ことになる。
        """
        result = judge_oq27(
            latency(end_to_end_p95_ms=EXCEEDING_P95_MS, stages=COMPUTE_BOUND_STAGES),
            aggregate(),
            settings=settings(),
        )
        assert result.verdict is Oq27Verdict.DEFERRED
        assert rationales_present(result) == (R_BOTTLENECK_NOT_ACQUISITION,)

    def test_an_unknown_bottleneck_does_not_yield_conditional_continuation(self) -> None:
        result = judge_oq27(
            latency(end_to_end_p95_ms=EXCEEDING_P95_MS, stages=()),
            aggregate(),
            settings=settings(),
        )
        assert result.verdict is Oq27Verdict.DEFERRED
        assert rationales_present(result) == (R_BOTTLENECK_NOT_ACQUISITION,)

    def test_the_limiting_conditions_are_stated(self) -> None:
        """条件付き継続では**律速している条件を明示する**（要件 9.8）。"""
        result = judge_oq27(
            latency(
                end_to_end_p95_ms=EXCEEDING_P95_MS,
                stages=CAPTURE_BOUND_STAGES,
                capture_fps=30.0,
                process_fps=29.7,
            ),
            aggregate(),
            settings=settings(),
        )
        assert result.verdict is Oq27Verdict.CONTINUE_WITH_CONSTRAINTS
        joined = "".join(result.limiting_conditions)
        assert "capture/frame/total_ms" in joined
        assert "180" in joined
        assert "取得 fps = 30" in joined
        assert "実処理 fps = 29.7" in joined
        assert "同一測定のオーバーヘッド相当値 200 ms を超えている" in joined

    def test_unmeasured_conditions_are_written_as_missing_not_zero(self) -> None:
        """律速条件の欠測は「欠測」と書く。**0 と書かない。**

        「取得 fps = 0」と書かれた条件は、読み手に「取得が止まっていた」と
        読ませる。測っていないだけの測定に、起きていない現象を記録すること
        になる。
        """
        result = judge_oq27(
            latency(
                end_to_end_p95_ms=EXCEEDING_P95_MS,
                stages=CAPTURE_BOUND_STAGES,
                capture_fps=None,
                process_fps=None,
                cpu_percent_mean=45.0,
                frames_dropped=0,
            ),
            aggregate(),
            settings=settings(),
        )
        assert result.verdict is Oq27Verdict.CONTINUE_WITH_CONSTRAINTS
        joined = "".join(result.limiting_conditions)
        assert "取得 fps = 欠測 / 実処理 fps = 欠測" in joined
        assert "取得 fps = 0" not in joined


# ---------------------------------------------------------------------------
# 資源の飽和
# ---------------------------------------------------------------------------


class TestResourceSaturation:
    """飽和は**同一測定内の量だけ**で判定する（要件 9.2 / 9.7）。"""

    def _exceeding(self, **kwargs: object) -> Oq27Result:
        return judge_oq27(
            latency(end_to_end_p95_ms=EXCEEDING_P95_MS, **kwargs),  # type: ignore[arg-type]
            aggregate(),
            settings=settings(improvements_applied=STEP_KEYS),
            improvements=full_improvements(),
        )

    def test_dropped_frames_mean_saturation(self) -> None:
        result = self._exceeding(frames_dropped=1)
        assert result.resource_saturated is True
        assert result.verdict is Oq27Verdict.INSUFFICIENT

    def test_zero_dropped_frames_do_not_mean_saturation(self) -> None:
        result = self._exceeding(frames_dropped=0)
        assert result.resource_saturated is False

    def test_process_fps_falling_behind_capture_fps_means_saturation(self) -> None:
        result = self._exceeding(capture_fps=30.0, process_fps=28.0)
        assert result.resource_saturated is True
        assert result.verdict is Oq27Verdict.INSUFFICIENT

    def test_the_fps_ratio_actually_drives_the_decision(self) -> None:
        """**倍率を渡すだけでなく、その倍率で判定が動く**ことを固定する。

        同じ 30.0 / 29.0 fps の対に対し、倍率だけを変えると飽和判定が変わる。
        """
        common = {
            "capture_fps": 30.0,
            "process_fps": 29.0,
            "frames_dropped": 0,
            "cpu_percent_mean": 40.0,
        }
        lenient = judge_oq27(
            latency(end_to_end_p95_ms=EXCEEDING_P95_MS, **common),  # type: ignore[arg-type]
            aggregate(),
            settings=settings(
                improvements_applied=STEP_KEYS, fps_shortfall_ratio=0.95
            ),
            improvements=full_improvements(),
        )
        strict = judge_oq27(
            latency(end_to_end_p95_ms=EXCEEDING_P95_MS, **common),  # type: ignore[arg-type]
            aggregate(),
            settings=settings(
                improvements_applied=STEP_KEYS, fps_shortfall_ratio=0.99
            ),
            improvements=full_improvements(),
        )
        assert lenient.resource_saturated is False
        assert strict.resource_saturated is True

    def test_the_fps_boundary_is_exclusive(self) -> None:
        """取得 fps × 倍率**ちょうど**は飽和としない（`<` であって `<=` ではない）。"""

        def saturated(process_fps: float) -> bool | None:
            return judge_oq27(
                latency(
                    end_to_end_p95_ms=EXCEEDING_P95_MS,
                    capture_fps=30.0,
                    process_fps=process_fps,
                    frames_dropped=0,
                    cpu_percent_mean=40.0,
                ),
                aggregate(),
                settings=settings(
                    improvements_applied=STEP_KEYS, fps_shortfall_ratio=0.9
                ),
                improvements=full_improvements(),
            ).resource_saturated

        # 取得 30.0 fps × 0.9 = 27.0 fps がちょうどの境界である。
        assert saturated(27.0) is False
        assert saturated(26.9) is True

    def test_cpu_at_the_saturation_ratio_counts_as_saturated(self) -> None:
        """CPU 使用率は全負荷（測定量そのものの上限 100%）との比で見る。"""
        at_boundary = self._exceeding(cpu_percent_mean=90.0, frames_dropped=0)
        below = self._exceeding(cpu_percent_mean=89.9, frames_dropped=0)
        assert at_boundary.resource_saturated is True
        assert below.resource_saturated is False

    def test_the_cpu_ratio_actually_drives_the_decision(self) -> None:
        common = {
            "cpu_percent_mean": 85.0,
            "frames_dropped": 0,
            "capture_fps": 30.0,
            "process_fps": 29.7,
        }
        lenient = judge_oq27(
            latency(end_to_end_p95_ms=EXCEEDING_P95_MS, **common),  # type: ignore[arg-type]
            aggregate(),
            settings=settings(improvements_applied=STEP_KEYS, cpu_saturation_ratio=0.9),
            improvements=full_improvements(),
        )
        strict = judge_oq27(
            latency(end_to_end_p95_ms=EXCEEDING_P95_MS, **common),  # type: ignore[arg-type]
            aggregate(),
            settings=settings(improvements_applied=STEP_KEYS, cpu_saturation_ratio=0.8),
            improvements=full_improvements(),
        )
        assert lenient.resource_saturated is False
        assert strict.resource_saturated is True

    def test_all_three_signals_missing_leaves_saturation_undecided(self) -> None:
        """3つとも欠測なら飽和は判定不能であり、**飽和していない**とは言わない。

        「測っていない」を「余裕がある」と読み替えると、資源を一切測って
        いない測定が条件付き継続まで進んでしまう。
        """
        result = self._exceeding(
            frames_dropped=None,
            capture_fps=None,
            process_fps=None,
            cpu_percent_mean=None,
        )
        assert result.resource_saturated is None
        assert result.verdict is Oq27Verdict.DEFERRED
        assert rationales_present(result) == (R_SATURATION_UNKNOWN,)

    def test_one_measured_signal_is_enough_to_decide(self) -> None:
        result = self._exceeding(
            frames_dropped=None,
            capture_fps=None,
            process_fps=None,
            cpu_percent_mean=45.0,
        )
        assert result.resource_saturated is False

    def test_saturation_is_not_evaluated_from_frames_missing(self) -> None:
        """フレーム欠落（`frames_missing`）は飽和の材料にしない。

        欠落は取得の取りこぼし（`frames_dropped`）とは別の量である
        （前者はデバイス側連番の飛び、後者は下流へ渡せず捨てた件数）。
        混ぜると、取りこぼしが無いのに飽和と判定される。
        """
        result = self._exceeding(frames_dropped=0, frames_missing=99)
        assert result.resource_saturated is False


# ---------------------------------------------------------------------------
# 判定規則の説明文（★ 記録される文面を固定する）
# ---------------------------------------------------------------------------


#: 判定が動く閾値は**すべて相異なる値**にする（入れ替えが必ず見える）。
#:
#: ⚠️ **4値のいずれも `Oq27Config` / `TrialLimits` の既定値と一致させてはならない。**
#: 既定値（min_valid_throws=20 / min_sessions=2 / fps_shortfall_ratio=0.95 /
#: cpu_saturation_ratio=0.9）を期待値に採ると、**設定を無視して既定値を規則へ
#: 書き込む実装**と正しい実装が区別できなくなる——記録された規則が
#: 「fps=0.95」と言いながら実際の判定は 0.85 で行われる、という
#: **同一の `Judgement` の中で criterion と evidence が矛盾する状態**が素通りする
#: （空振り形4「参照解を実装の定数から組む」の変形であり、タスク 5.2 の
#: 「設定値で記録しながら別の値で判定する」の鏡像である）。
#: OQ-27 では**記録こそが契約**（要件 9.1）なので、これは実害のある空振りだった。
CRITERION_SETTINGS: dict[str, object] = {
    "min_valid_throws": 7,
    "min_sessions": 3,
    "fps_shortfall_ratio": 0.85,
    "cpu_saturation_ratio": 0.55,
}

#: 既定値。**期待値としては使わない**——「規則に現れてはならない値」として
#: 否定照合にだけ使う（`test_the_criterion_does_not_fall_back_to_the_defaults`）。
DEFAULT_FPS_SHORTFALL_RATIO = "0.95"
DEFAULT_CPU_SATURATION_RATIO = "0.9"
DEFAULT_MIN_VALID_THROWS = "20"
DEFAULT_MIN_SESSIONS = "2"


def expected_criterion_sentences(
    *,
    min_throws: str,
    min_sessions: str,
    fps_ratio: str,
    cpu_ratio: str,
) -> tuple[str, ...]:
    """規則の説明文の**期待される全文**を、一文ずつテスト局所のリテラルで書く。

    ここが本ファイルで最も load-bearing な定数である。要件 9.1 が防ごうと
    しているのは「**実装は正しいまま criterion だけが嘘になる**」状態であり、
    それは文面のうち検査されていない部分にちょうど生まれる。文を1つでも
    覆い忘れると、そこは**削っても取り違えても誰も気づかない領域**になる
    ——実際、規則2 を「計算資源に余裕があるなら不足とする」と書き換えて
    実際の判定と**逆**にする変異が、覆い漏れによって生き延びていた。

    したがって**全文を覆い、連結が criterion と厳密に一致すること**まで
    固定する。順序の入れ替えも、文の追加も、これで落ちる。

    **実装の私有定数を import して自分自身と比べてはならない**（空振り形3。
    定数を全部同じ文字列へ潰しても通ってしまう）。ここに並ぶのはすべて
    テスト局所のリテラルである。
    """
    steps = " / ".join(f"{step}（{STEP_LABELS[step]}）" for step in STEP_KEYS)
    return (
        # 見出し
        (
            "OQ-27（Raspberry Pi 4 継続可否）の判定規則"
            "（実測前に固定。design.md「決着させる未決事項」/「Oq27Judge」、"
            "research.md Decision 5、要件 9.1）: "
        ),
        # 判定値の語彙（4値であること自体が規則の一部である）
        "判定値は「継続 / 条件付き継続 / 不足 / 保留」の4値である。",
        # GATE 1
        (
            f"【GATE 1】有効試行数が {min_throws} 未満、"
            f"またはセッション数が {min_sessions} 未満なら保留とする"
            "（投擲はばらつきが大きく再現性が低いため、単発では判断しない。要件 9.9）。"
        ),
        # GATE 2
        (
            "【GATE 2】実機由来の投擲が1件も無いなら保留とし、"
            "合成・記録再生の結果を実機の結論として扱わない（要件 9.10）。"
        ),
        # 規則1
        (
            "【規則1】end-to-end レイテンシの p95 が、"
            "同一測定から得たオーバーヘッド相当値"
            "（実測項目2「リリース〜検出開始」の代表値と"
            "実測項目3「検出開始〜初回予測」の代表値の和）を超えないなら継続とする"
            "（ちょうど等しい値は「超えない」に含む。要件 9.6）。"
        ),
        # 規則2（**飽和しているなら不足**。ここが逆になると実際の判定と食い違う）
        "【規則2】超え、かつ計算資源が飽和しているなら不足とする（要件 9.7）。",
        # 規則3（**余裕があり、かつ取得区間が律速なら条件付き継続**）
        (
            "【規則3】超えるが計算資源に余裕があり、"
            "律速段階が取得区間（段階名 capture）であるなら条件付き継続とし、"
            "律速している条件を明示する（要件 9.8）。"
        ),
        # 規則4
        (
            "【規則4】上記で決まらないなら保留とする"
            "（p95 か比較対象が欠測、飽和を判定する材料が1つも無い、"
            "律速段階が取得区間でない、のいずれか）。"
        ),
        # GATE 0
        (
            "【GATE 0】docs/development-environment.md §13.2 の改善項目"
            f"（{steps}）に未適用のものが残っている間は「不足」を出さず保留へ落とす"
            "（要件 9.3）。適用済みと認めるのは、設定 improvements_applied に名前があり、"
            "かつ前後の計測値が証跡として揃っている項目だけである（要件 9.4）。"
        ),
        # 律速段階の選び方
        (
            "律速段階は段階別レイテンシの内訳のうち p95 が最大の行とし、"
            "同値なら段階名・イベント名・フィールド名の辞書順で先の行を採る（要件 9.5）。"
        ),
        # 飽和の 3 signal（**3件すべてが規則である。1つ削ると判定と食い違う**）
        (
            "計算資源の飽和は同一測定内の量だけで判定する: "
            "取りこぼしが1件以上あるか、"
            f"実処理 fps が取得 fps の {fps_ratio} 倍を下回るか、"
            "CPU 使用率の平均が全負荷（測定量そのものの上限 100%）の "
            f"{cpu_ratio} 倍以上であること。"
        ),
        # 飽和が判定不能になる場合
        (
            "3つとも欠測なら飽和は判定不能とする"
            "（「測っていない」を「余裕がある」と読み替えない）。"
        ),
        # 要件 9.2 の宣言
        (
            "絶対値の目標を置かず、同一測定内の量どうしの相対比較とばらつきで判定する"
            "（要件 9.2、tech.md 開発標準1）。"
        ),
        # 要件 13.7 の宣言（既定値を必須性能と取り違えさせない）
        (
            "ここに出ている下限と割合は暫定の評価候補であって必須性能ではない"
            "（要件 13.7）。"
        ),
        # 要件 9.11 の宣言
        (
            "本判定はハードウェアの置き換えを実行せず、"
            "判断材料と結論の提示までにとどめる（要件 9.11）。"
        ),
    )


class TestCriterionText:
    """**判定規則の説明文を結果に埋め込む**（要件 9.1）。

    数字を個別に `in` で見る検査は、**書式スロットを入れ替える変異**を
    素通しさせる（タスク 5.2 の実例）。スロットの数だけ**一文まるごとの
    肯定照合**を置き、**取り違えた一文が含まれないこと**も併せて固定する。
    """

    def criterion(self) -> str:
        return judge_oq27(
            latency(end_to_end_p95_ms=HEALTHY_P95_MS),
            aggregate(valid_throw_count=8, session_ids=("s-1", "s-2", "s-3")),
            settings=settings(**CRITERION_SETTINGS),  # type: ignore[arg-type]
        ).judgement.criterion

    def test_the_trial_limits_appear_as_one_sentence(self) -> None:
        assert (
            "有効試行数が 7 未満、またはセッション数が 3 未満なら保留とする"
            in self.criterion()
        )

    def test_the_trial_limits_are_not_swapped(self) -> None:
        assert (
            "有効試行数が 3 未満、またはセッション数が 7 未満なら保留とする"
            not in self.criterion()
        )

    def test_the_fps_ratio_appears_as_one_sentence(self) -> None:
        assert "実処理 fps が取得 fps の 0.85 倍を下回るか、" in self.criterion()

    def test_the_fps_ratio_is_not_the_cpu_ratio(self) -> None:
        assert "実処理 fps が取得 fps の 0.55 倍を下回るか、" not in self.criterion()

    def test_the_cpu_ratio_appears_as_one_sentence(self) -> None:
        assert (
            "CPU 使用率の平均が全負荷（測定量そのものの上限 100%）の 0.55 倍以上であること。"
            in self.criterion()
        )

    def test_the_cpu_ratio_is_not_the_fps_ratio(self) -> None:
        assert (
            "CPU 使用率の平均が全負荷（測定量そのものの上限 100%）の 0.85 倍以上であること。"
            not in self.criterion()
        )

    def test_the_criterion_does_not_fall_back_to_the_defaults(self) -> None:
        """記録される規則は**設定した値**であって、実装の既定値ではない（要件 9.1）。

        設定を無視して既定値を規則へ書き込む実装は、**判定そのものは設定どおりに
        動いたまま**、記録だけが嘘になる。同一の `Judgement` の中で criterion が
        「fps=0.95」と言い、evidence が「fps=0.85」と言う状態であり、
        判定値を見ているテストでは一切気づけない。

        ここは既定値を**期待値ではなく禁止値**として使う唯一の場所である。
        """
        text = self.criterion()
        assert (
            f"実処理 fps が取得 fps の {DEFAULT_FPS_SHORTFALL_RATIO} 倍を下回るか、"
            not in text
        )
        assert (
            "CPU 使用率の平均が全負荷（測定量そのものの上限 100%）の "
            f"{DEFAULT_CPU_SATURATION_RATIO} 倍以上であること。" not in text
        )
        assert (
            f"有効試行数が {DEFAULT_MIN_VALID_THROWS} 未満、"
            f"またはセッション数が {DEFAULT_MIN_SESSIONS} 未満なら保留とする"
            not in text
        )

    def test_every_improvement_step_appears_with_its_label(self) -> None:
        """8項目が**キーとラベルの対**として現れる（対応の取り違えを落とす）。"""
        text = self.criterion()
        for step in STEP_KEYS:
            assert f"{step}（{STEP_LABELS[step]}）" in text

    def test_the_reference_is_described_as_coming_from_the_same_measurement(
        self,
    ) -> None:
        """比較対象の**出どころ**を文面に固定する（要件 9.2）。"""
        assert (
            "同一測定から得たオーバーヘッド相当値"
            "（実測項目2「リリース〜検出開始」の代表値と"
            "実測項目3「検出開始〜初回予測」の代表値の和）を超えないなら継続とする"
            in self.criterion()
        )

    def test_the_gate_zero_sentence_is_present(self) -> None:
        assert (
            "に未適用のものが残っている間は「不足」を出さず保留へ落とす" in self.criterion()
        )
        assert (
            "適用済みと認めるのは、設定 improvements_applied に名前があり、"
            "かつ前後の計測値が証跡として揃っている項目だけである" in self.criterion()
        )

    def test_the_bottleneck_rule_is_present(self) -> None:
        assert (
            "律速段階は段階別レイテンシの内訳のうち p95 が最大の行とし、"
            "同値なら段階名・イベント名・フィールド名の辞書順で先の行を採る"
            in self.criterion()
        )

    def test_the_criterion_states_that_no_absolute_target_is_used(self) -> None:
        assert (
            "絶対値の目標を置かず、同一測定内の量どうしの相対比較とばらつきで判定する"
            in self.criterion()
        )

    def test_the_criterion_states_that_no_hardware_is_replaced(self) -> None:
        assert (
            "本判定はハードウェアの置き換えを実行せず、"
            "判断材料と結論の提示までにとどめる" in self.criterion()
        )

    def test_the_criterion_does_not_change_with_the_verdict(self) -> None:
        """**結果に合わせて動く規則は規則ではない。** 4値すべてで同一である。"""
        common = {
            "min_valid_throws": 5,
            "min_sessions": 2,
            "improvements_applied": STEP_KEYS,
        }
        texts = {
            judge_oq27(
                latency(end_to_end_p95_ms=HEALTHY_P95_MS),
                aggregate(),
                settings=settings(**common),  # type: ignore[arg-type]
                improvements=full_improvements(),
            ).judgement.criterion,
            judge_oq27(
                latency(end_to_end_p95_ms=EXCEEDING_P95_MS, frames_dropped=4),
                aggregate(),
                settings=settings(**common),  # type: ignore[arg-type]
                improvements=full_improvements(),
            ).judgement.criterion,
            judge_oq27(
                latency(end_to_end_p95_ms=EXCEEDING_P95_MS),
                aggregate(),
                settings=settings(**common),  # type: ignore[arg-type]
                improvements=full_improvements(),
            ).judgement.criterion,
            judge_oq27(
                latency(end_to_end_p95_ms=HEALTHY_P95_MS),
                aggregate(valid_throw_count=1),
                settings=settings(**common),  # type: ignore[arg-type]
                improvements=full_improvements(),
            ).judgement.criterion,
        }
        assert len(texts) == 1

    def test_every_sentence_of_the_rule_is_present(self) -> None:
        """**全文が一文まるごと**含まれる（覆い漏れた文は削っても取り違えても通る）。

        肯定照合を文の数だけ置くのがタスク 5.2 の是正の型である。抜けた文が
        1つでもあると、そこは「実装は正しいまま criterion だけが嘘になる」
        領域になる。
        """
        text = self.criterion()
        for sentence in expected_criterion_sentences(
            min_throws="7", min_sessions="3", fps_ratio="0.85", cpu_ratio="0.55"
        ):
            assert sentence in text, sentence

    def test_the_criterion_is_exactly_the_fixed_text(self) -> None:
        """連結が criterion と**厳密に一致**する。

        一致まで固定すると、**文の削除・追加・順序の入れ替え**がすべて落ちる。
        規則の説明文は「実測前に固定した規則」そのものとして記録に残る
        （要件 9.1）ので、勝手に増減してよい文は1つも無い。
        """
        expected = "".join(
            expected_criterion_sentences(
                min_throws="7", min_sessions="3", fps_ratio="0.85", cpu_ratio="0.55"
            )
        )
        assert self.criterion() == expected

    def test_rule_two_and_rule_three_are_not_swapped(self) -> None:
        """規則2 と規則3 の**取り違え**を明示的に落とす。

        ⚠️ **これが最も重い取り違えである。** 記録される規則が
        「資源に余裕があるなら不足」と書かれると、**実際の判定
        （要件 9.7 / 9.8）と逆**になる。判定値は正しいまま規則だけが嘘になる
        ので、判定値を見ているテストでは一切気づけない。
        """
        text = self.criterion()
        assert "超え、かつ計算資源に余裕があるなら不足とする" not in text
        assert "計算資源が飽和しているなら条件付き継続" not in text
        assert "【規則2】超えるが計算資源に余裕があり" not in text
        assert "【規則3】超え、かつ計算資源が飽和しているなら不足" not in text

    def test_the_gates_are_not_swapped(self) -> None:
        """GATE 1 と GATE 2 の**取り違え**を落とす。

        「投げる回数を増やす」と「実機で撮る」は**やることが違う**。
        取り違えた規則を記録すると、次にやるべきことを間違える。
        """
        text = self.criterion()
        assert "【GATE 2】有効試行数が" not in text
        assert "【GATE 1】実機由来の投擲が1件も無いなら" not in text

    def test_the_saturation_signals_are_not_swapped(self) -> None:
        """飽和 3 signal のうち2つを入れ替えた文が含まれない。"""
        text = self.criterion()
        assert "実処理 fps が取得 fps の 0.85 倍以上であること" not in text
        assert (
            "CPU 使用率の平均が全負荷（測定量そのものの上限 100%）の 0.55 倍を下回るか"
            not in text
        )
        assert "取りこぼしが1件も無いか、" not in text

    def test_the_criterion_matches_a_second_settings_point(self) -> None:
        """**別の設定点でも全文が一致する**（規則が設定から組まれていることの固定）。

        設定点が1つだけだと、その4値を実装へ焼き付けた（あるいは既定値へ
        差し替えた）実装と区別できない。`oq27_criterion()` に対して既に採って
        いる形を `judge_oq27()` 経由にも適用し、**記録側の配線**を固定する。
        """
        result = judge_oq27(
            latency(end_to_end_p95_ms=HEALTHY_P95_MS),
            aggregate(
                valid_throw_count=12,
                session_ids=("s-1", "s-2", "s-3", "s-4"),
            ),
            settings=settings(
                min_valid_throws=11,
                min_sessions=4,
                fps_shortfall_ratio=0.75,
                cpu_saturation_ratio=0.6,
            ),
        )
        assert result.judgement.criterion == "".join(
            expected_criterion_sentences(
                min_throws="11", min_sessions="4", fps_ratio="0.75", cpu_ratio="0.6"
            )
        )

    def test_the_recorded_rule_and_the_evidence_agree(self) -> None:
        """**記録した規則と、判定に使った値が同じ設定から来ている**（要件 9.1）。

        criterion（規則）と evidence（根拠の数値）が同一の `Judgement` の中で
        食い違うと、**どちらが本当に適用されたのかを後から決められない**。
        両方を同じ設定点に対して同時に固定する。
        """
        result = judge_oq27(
            latency(end_to_end_p95_ms=HEALTHY_P95_MS),
            aggregate(
                valid_throw_count=12,
                session_ids=("s-1", "s-2", "s-3", "s-4"),
            ),
            settings=settings(
                min_valid_throws=11,
                min_sessions=4,
                fps_shortfall_ratio=0.75,
                cpu_saturation_ratio=0.6,
            ),
        )
        evidence = result.judgement.evidence
        assert evidence["min_valid_throws"] == 11
        assert evidence["min_sessions"] == 4
        assert evidence["fps_shortfall_ratio"] == 0.75
        assert evidence["cpu_saturation_ratio"] == 0.6

        text = result.judgement.criterion
        assert "有効試行数が 11 未満、またはセッション数が 4 未満なら保留とする" in text
        assert "実処理 fps が取得 fps の 0.75 倍を下回るか、" in text
        assert (
            "CPU 使用率の平均が全負荷（測定量そのものの上限 100%）の 0.6 倍以上であること。"
            in text
        )

    def test_the_criterion_builder_reflects_its_arguments(self) -> None:
        """公開の組み立て関数も、渡した値をそのまま文面へ入れる。"""
        text = oq27_criterion(
            min_valid_throws=11,
            min_sessions=4,
            fps_shortfall_ratio=0.75,
            cpu_saturation_ratio=0.6,
        )
        assert "有効試行数が 11 未満、またはセッション数が 4 未満なら保留とする" in text
        assert "実処理 fps が取得 fps の 0.75 倍を下回るか、" in text
        assert (
            "CPU 使用率の平均が全負荷（測定量そのものの上限 100%）の 0.6 倍以上であること。"
            in text
        )
        # 組み立て関数も**全文**を返す（`judge_oq27()` 経由と同じ文面である）。
        assert text == "".join(
            expected_criterion_sentences(
                min_throws="11", min_sessions="4", fps_ratio="0.75", cpu_ratio="0.6"
            )
        )


# ---------------------------------------------------------------------------
# 判断の共通の形・証跡
# ---------------------------------------------------------------------------


class TestJudgementShape:
    """判断は `Judgement` の共通の形に載る（要件 9.1 / 9.4）。"""

    def test_the_question_is_oq27(self) -> None:
        result = judge_oq27(
            latency(end_to_end_p95_ms=HEALTHY_P95_MS), aggregate(), settings=settings()
        )
        assert result.judgement.question == QUESTION

    def test_the_evidence_carries_the_numbers_and_their_spread(self) -> None:
        """**ばらつきを併記する**（代表値だけでは単発の投擲と区別できない）。"""
        result = judge_oq27(
            latency(end_to_end_p95_ms=HEALTHY_P95_MS),
            aggregate(release_to_detect_ms=70.0, detect_to_first_ms=130.0),
            settings=settings(),
        )
        evidence = result.judgement.evidence
        assert evidence["end_to_end_p95_ms"] == HEALTHY_P95_MS
        assert evidence["overhead_reference_ms"] == OVERHEAD_REFERENCE_MS
        assert evidence["release_to_detect_median_ms"] == 70.0
        assert evidence["release_to_detect_iqr_ms"] == pytest.approx(21.0)
        assert evidence["detect_to_first_prediction_median_ms"] == 130.0
        assert evidence["detect_to_first_prediction_iqr_ms"] == pytest.approx(39.0)
        assert evidence["valid_throw_count"] == 8
        assert evidence["session_count"] == 2
        assert evidence["live_throw_count"] == 3
        assert evidence["bottleneck_stage"] == CAPTURE_STAGE
        assert evidence["resource_saturated"] is False

    def test_the_evidence_lists_the_missing_improvement_steps(self) -> None:
        """**何が残っているか**が読めなければ、次に何をすればよいか分からない。"""
        result = judge_oq27(
            latency(end_to_end_p95_ms=EXCEEDING_P95_MS, frames_dropped=4),
            aggregate(),
            settings=settings(
                improvements_applied=[s for s in STEP_KEYS if s not in ("roi", "fps")]
            ),
            improvements=full_improvements(omit="roi"),
        )
        assert result.judgement.evidence["improvements_missing"] == ["roi", "fps"]
        assert result.judgement.evidence["improvements_covered"] is False

    def test_provisional_reflects_the_aggregate(self) -> None:
        result = judge_oq27(
            latency(end_to_end_p95_ms=HEALTHY_P95_MS),
            aggregate(provisional=True),
            settings=settings(improvements_applied=STEP_KEYS),
            improvements=full_improvements(),
        )
        assert result.verdict is Oq27Verdict.CONTINUE
        assert result.judgement.provisional is True

    def test_a_settled_continuation_is_not_provisional(self) -> None:
        result = judge_oq27(
            latency(end_to_end_p95_ms=HEALTHY_P95_MS),
            aggregate(provisional=False),
            settings=settings(improvements_applied=STEP_KEYS),
            improvements=full_improvements(),
        )
        assert result.judgement.provisional is False

    def test_a_deferred_verdict_is_provisional_on_its_own(self) -> None:
        """**保留は、それだけで暫定である**（design.md「Oq27Judge」Invariants）。

        暫定の印は3つの項の論理和である（保留 / 集計が暫定 / 改善が未適用）。
        **保留の項だけが効いている入力**で固定しないと、その項を削っても
        他の2項が旗を立ててしまい、テストは通り続ける（空振り形7・10）。
        ここは集計を非暫定にし、改善項目を全件証跡込みで適用済みにしてある
        ——旗を立てられるのは保留の項だけである。

        OQ-27 は購入判断に直結するので、**保留に暫定印が付かないこと**には
        実害がある。「まだ判断できない」が「判断してよい結論」として
        レポートへ載る。
        """
        # GATE 2 による保留（実機由来の投擲が1件も無い）。
        gate2 = judge_oq27(
            latency(end_to_end_p95_ms=HEALTHY_P95_MS),
            aggregate(provisional=False, live_throw_count=0),
            settings=settings(improvements_applied=STEP_KEYS),
            improvements=full_improvements(),
        )
        assert gate2.verdict is Oq27Verdict.DEFERRED
        assert gate2.judgement.evidence["improvements_covered"] is True
        assert gate2.judgement.provisional is True

        # 規則4 による保留（end-to-end の p95 が欠測）。
        rule4 = judge_oq27(
            latency(end_to_end_p95_ms=None),
            aggregate(provisional=False),
            settings=settings(improvements_applied=STEP_KEYS),
            improvements=full_improvements(),
        )
        assert rule4.verdict is Oq27Verdict.DEFERRED
        assert rule4.judgement.evidence["improvements_covered"] is True
        assert rule4.judgement.provisional is True

    def test_each_provisional_term_is_identifiable_on_its_own(self) -> None:
        """暫定の3項が**それぞれ単独で**旗を立てる（どの項が効いたか判別できる）。

        3項をまとめて1つの入力で見ると、項を1つ落とす変異が素通りする。
        非暫定の対照（継続 ＋ 集計が非暫定 ＋ 改善が全件適用済み）を基準に、
        1項ずつだけを成立させる。
        """

        def provisional(
            *,
            deferred: bool = False,
            aggregate_provisional: bool = False,
            improvements_covered: bool = True,
        ) -> bool:
            applied = STEP_KEYS if improvements_covered else ()
            return judge_oq27(
                latency(end_to_end_p95_ms=HEALTHY_P95_MS),
                aggregate(
                    provisional=aggregate_provisional,
                    live_throw_count=0 if deferred else 3,
                ),
                settings=settings(improvements_applied=applied),
                improvements=full_improvements(),
            ).judgement.provisional

        assert provisional() is False  # 3項とも成立しない対照
        assert provisional(deferred=True) is True  # 第1項だけ
        assert provisional(aggregate_provisional=True) is True  # 第2項だけ
        assert provisional(improvements_covered=False) is True  # 第3項だけ

    def test_the_same_input_yields_the_same_result(self) -> None:
        """同一入力に同一の判定（要件 12.4）。"""
        args = (
            latency(end_to_end_p95_ms=EXCEEDING_P95_MS),
            aggregate(),
        )
        first = judge_oq27(*args, settings=settings())
        second = judge_oq27(*args, settings=settings())
        assert first == second

    def test_the_improvement_records_are_copied(self) -> None:
        """渡されたマッピングを抱え込まない（証跡が後から書き換わらない）。"""
        before = {"end_to_end_p95_ms": 300.0}
        record = ImprovementRecord(
            step="roi", applied=True, before=before, after={"end_to_end_p95_ms": 250.0}
        )
        before["end_to_end_p95_ms"] = 999.0
        assert record.before == {"end_to_end_p95_ms": 300.0}


# ---------------------------------------------------------------------------
# 境界（依存と責務）
# ---------------------------------------------------------------------------


ALLOWED_IMPORT_ROOTS = {
    "__future__",
    "collections",
    "dataclasses",
    "m1_validation",
    "typing",
}


class TestBoundary:
    """本モジュールが越えてはならない線（要件 13.1、design.md Allowed Dependencies）。"""

    def test_module_imports_only_allowed_roots(self) -> None:
        source = Path(inspect.getfile(oq27_module)).read_text(encoding="utf-8")
        roots = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
        assert roots <= ALLOWED_IMPORT_ROOTS, sorted(roots - ALLOWED_IMPORT_ROOTS)

    def test_module_does_not_import_upstream_packages(self) -> None:
        """上流3パッケージを直接 import しない（接点は `upstream.py` だけ）。"""
        source = Path(inspect.getfile(oq27_module)).read_text(encoding="utf-8")
        roots = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
        forbidden = {
            "numpy",
            "cv2",
            "pyrealsense2",
            "sensing_foundation",
            "flying_object_tracking",
            "world_frame_calibration",
        }
        assert roots.isdisjoint(forbidden), sorted(roots & forbidden)

    def test_the_docstring_says_no_hardware_is_replaced(self) -> None:
        """**ハードウェアの置き換えを実行しない**旨を docstring に明記する（要件 9.11）。"""
        doc = oq27_module.__doc__ or ""
        assert "ハードウェアの置き換えを実行せず" in doc
        assert "判断材料と結論の提示までにとどめる" in doc

    def test_the_judge_docstring_says_no_hardware_is_replaced(self) -> None:
        doc = judge_oq27.__doc__ or ""
        assert "ハードウェアの置き換えを実行せず" in doc


# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------


class TestSettings:
    """OQ-27 の倍率は設定として解決する（要件 13.5 / 13.7）。"""

    @pytest.fixture
    def layout_file(self, tmp_path: Path) -> Path:
        import json

        path = tmp_path / "layout.json"
        path.write_text(
            json.dumps(
                {
                    "format_version": "1.0",
                    "layouts": [
                        {
                            "layout_id": "throw-a",
                            "release_position_world_mm": [-1700.0, -50.0, 1690.0],
                            "release_height_mm": 1690.0,
                            "throw_direction_deg": 0.0,
                            "standby_position_world_mm": [1000.0, 1000.0],
                            "object_diameter_mm": 65.0,
                            "aperture_diameter_mm": 200.0,
                            "camera_position_world_mm": [0.0, -2500.0, 1200.0],
                            "notes": "テスト用の仮値",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return path

    def test_defaults_are_resolved(self, layout_file: Path) -> None:
        resolved = M1Settings.resolve(
            file=None, env={}, overrides={"layout_file": str(layout_file)}
        )
        assert resolved.oq27.cpu_saturation_ratio == 0.9
        assert resolved.oq27.fps_shortfall_ratio == 0.95

    def test_the_env_layer_reaches_the_oq27_group(self, layout_file: Path) -> None:
        resolved = M1Settings.resolve(
            file=None,
            env={f"{config_module.ENV_PREFIX}CPU_SATURATION_RATIO": "0.75"},
            overrides={"layout_file": str(layout_file)},
        )
        assert resolved.oq27.cpu_saturation_ratio == 0.75

    def test_describe_shows_the_oq27_group(self, layout_file: Path) -> None:
        described = M1Settings.resolve(
            file=None, env={}, overrides={"layout_file": str(layout_file)}
        ).describe()["oq27"]
        assert described["cpu_saturation_ratio"] == 0.9  # type: ignore[index]
        assert described["fps_shortfall_ratio"] == 0.95  # type: ignore[index]

    def test_non_positive_ratios_are_rejected_at_startup(self, layout_file: Path) -> None:
        with pytest.raises(M1ConfigError):
            M1Settings.resolve(
                file=None,
                env={},
                overrides={
                    "layout_file": str(layout_file),
                    "cpu_saturation_ratio": 0.0,
                },
            )
        with pytest.raises(M1ConfigError):
            M1Settings.resolve(
                file=None,
                env={},
                overrides={
                    "layout_file": str(layout_file),
                    "fps_shortfall_ratio": -1.0,
                },
            )

    def test_the_provisional_notice_names_the_new_ratios(self, layout_file: Path) -> None:
        """既定の倍率が**必須性能と取り違えられない**ようにする（要件 13.7）。"""
        notice = str(
            M1Settings.resolve(
                file=None, env={}, overrides={"layout_file": str(layout_file)}
            ).describe()["provisional_notice"]
        )
        assert "cpu_saturation_ratio" in notice
        assert "fps_shortfall_ratio" in notice
