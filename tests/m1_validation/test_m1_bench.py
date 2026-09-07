"""計測 ON/OFF 比較（タスク 6.4、要件 7.5-7.8）。

本ファイルが厚く固定するのは次の6点である。

1. **交互に実行する**（要件 7.6）。`(無効, 有効)` の1巡を繰り返す A/B/A/B で
   あり、片方をまとめて連続実行しない。**実行順そのもの**（偽の実行器が
   受け取った要求の並び）と、結果に残る `segment_order` の**両方**を固定する
   ——片方だけだと、順序を偽って記録する実装と区別が付かない。
2. **判定基準が上流 Spec と同一の形**であること（要件 7.7）。
   「有効条件の中央値と無効条件の中央値の**差の絶対値**が、**無効条件の**
   四分位範囲（p75 − p25）**以内**であり、かつ取りこぼしが増えていない」。
   分母・引く向き・どちらの条件の四分位範囲か・境界の包含性を、
   **それぞれ単独で落ちる入力**で固定する。
3. **判定が偽のときだけ**「無条件に有効として扱わない」旨が出ること
   （要件 7.8）。真のときに出す実装もここで落ちる。
4. **対象区間（予測区間と end-to-end）が出力に明示され、上流の区間と
   混同されない**こと。2区間の数値をすべて別の値にしてあるので、
   ラベルの取り違えは必ず落ちる。
5. **各条件の生の計測値が残り、判定を後から再計算できる**こと。
   書き出した JSON から生値だけを読み直して判定を組み直し、
   同じ判定になることまで見る。
6. **公開フィールド・`evidence`・`criterion` の3経路すべて**を値で固定する
   （タスク 6.2 の教訓）。

期待値はすべて**テスト局所のリテラル**から組む。実装の定数を import して
自分自身と比べる検査は置かない（タスク 4.5 の教訓）。テストが渡す設定値は
**実装の既定値と重ならない値**にし、既定値は禁止値として否定照合にだけ使う
（タスク 6.1 の教訓）。**意味の違う数はフィクスチャ上でも別の値**にしてある
（タスク 6.3 の教訓）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from m1fixtures import verification_summary, write_calibration, write_layout

from flying_object_tracking import (
    HANDOFF_VERSION,
    CameraPoint,
    CameraTrack,
    CoordinateFrame,
    SourceKind,
    TrackPoint,
    TrackState,
    TrackUpdate,
)
from m1_validation import runner as runner_module
from m1_validation.bench import (
    ConditionStats,
    OverheadReport,
    OverheadVerdict,
    SegmentObservation,
    SegmentRequest,
    ThrowSegmentRunner,
    build_condition_stats,
    compute_verdict,
    overhead_criterion,
    overhead_output_path,
    report_to_dict,
    run_overhead_bench,
    write_overhead_report,
)
from m1_validation.config import M1Settings, OverheadConfig
from m1_validation.metrics.latency import END_TO_END_DEFINITION

# ---------------------------------------------------------------------------
# テスト局所のリテラル（実装の定数を参照しない）
# ---------------------------------------------------------------------------

QUESTION = "measurement_overhead"

CONDITION_OFF_NAME = "measurement_off"
CONDITION_ON_NAME = "measurement_on"

TARGET_PREDICT_NAME = "predict"
TARGET_E2E_NAME = "end_to_end"

PREDICT_LABEL = "予測区間（predict 段の1予測あたり総処理時間）"
E2E_LABEL = "end-to-end（観測時刻からその観測を含めた予測が得られるまで）"

VERDICT_UNCHANGED = "not_significantly_changed"
VERDICT_CHANGED = "significantly_changed"
VERDICT_UNDETERMINED = "undetermined"

# --- 設定（**既定値と重ならない値**にする。タスク 6.1 の教訓）---------------

CYCLES = 3
MIN_SAMPLES = 7

#: 第2の設定点。規則が設定から組まれていることの対照。
CYCLES_2 = 2
MIN_SAMPLES_2 = 20

#: 実装の既定値。**期待値としては使わない**——禁止値として否定照合にだけ使う。
DEFAULT_CYCLES_TEXT = "5"
DEFAULT_MIN_SAMPLES_TEXT = "30"

# --- 生の計測値 ------------------------------------------------------------
#
# 1セグメントあたり5件。**同じ多重集合を何巡積んでも p25 / p50 / p75 が
# 元の2番目・3番目・4番目の値に一致する**ので、期待値を補間なしのリテラルで
# 置ける（線形補間の規約: k = (n − 1) q）。
#
# 2つの対象区間の数値は**すべて別の値**にしてある。ラベルを取り違えた実装は
# 必ずどこかの値で落ちる。

#: 無効条件・予測区間。p25=12 / p50=14 / p75=20 / IQR=8 / p95=30。
OFF_PREDICT = (10.0, 12.0, 14.0, 20.0, 30.0)
OFF_PREDICT_P50 = 14.0
OFF_PREDICT_P95 = 30.0
OFF_PREDICT_IQR = 8.0
#: 無効条件の最大 − 最小。**四分位範囲ではない**（禁止値）。
OFF_PREDICT_RANGE = 20.0

#: 有効条件・予測区間（負荷差なし）。p25=16 / p50=19 / p75=20 / IQR=4。
ON_PREDICT_SAME = (13.0, 16.0, 19.0, 20.0, 26.0)
ON_PREDICT_SAME_P50 = 19.0
ON_PREDICT_SAME_P95 = 26.0
ON_PREDICT_SAME_IQR = 4.0
#: |19 − 14| = 5.0。**無効条件の IQR 8.0 以内**だが、
#: 有効条件の IQR 4.0 は超える——どちらの四分位範囲を使うかで判定が動く。
DELTA_PREDICT_SAME = 5.0

#: 有効条件・予測区間（**人工的な負荷差**）。p25=24 / p50=31 / p75=33 / IQR=9。
ON_PREDICT_LOAD = (21.0, 24.0, 31.0, 33.0, 41.0)
ON_PREDICT_LOAD_P50 = 31.0
ON_PREDICT_LOAD_IQR = 9.0
#: |31 − 14| = 17.0。無効条件の IQR 8.0 を超えるが、
#: 最大 − 最小（20.0）には収まる——四分位範囲を範囲に取り違えると通ってしまう。
DELTA_PREDICT_LOAD = 17.0

#: 無効条件・end-to-end。p25=120 / p50=150 / p75=200 / IQR=80 / p95=260。
OFF_E2E = (100.0, 120.0, 150.0, 200.0, 260.0)
OFF_E2E_P50 = 150.0
OFF_E2E_P95 = 260.0
OFF_E2E_IQR = 80.0

#: 有効条件・end-to-end。p25=151 / p50=161 / p75=163 / IQR=12 / p95=170。
ON_E2E_SAME = (149.0, 151.0, 161.0, 163.0, 170.0)
ON_E2E_SAME_P50 = 161.0
ON_E2E_SAME_P95 = 170.0
ON_E2E_SAME_IQR = 12.0
#: |161 − 150| = 11.0。
DELTA_E2E_SAME = 11.0

#: 1条件・1区間あたりの生の計測値の件数（5件 × 3巡）。
SAMPLES_PER_CONDITION = 15

# --- 行ごとに件数が違う入力（**暫定の印は行ごとに独立して効く**）------------
#
# end-to-end は「予測が成立した更新のみ」を算入するので、**実運用では常に
# 予測区間より件数が少ない**（`ThrowSegmentRunner` でも predict 6 / e2e 4）。
# 4行すべてが同じ件数の入力しか置かないと、`any` を `all` に変えた実装
# ——薄い end-to-end 系列を無印で「確定した結果」に昇格させる実装——が
# 素通りする。

#: 薄い end-to-end（3件）。p25=205 / p50=210 / p75=220 / IQR=15。
E2E_THIN_OFF = (200.0, 210.0, 230.0)
#: 同（3件）。p50=212 → 差 2.0 は 15 以内なので判定は真のまま。
E2E_THIN_ON = (204.0, 212.0, 216.0)
THIN_E2E_COUNT = 3
#: 1巡だけ回したときの予測区間の件数。
PREDICT_COUNT_ONE_CYCLE = 5
CYCLES_ONE = 1
#: 予測区間（5件）は満たすが end-to-end（3件）は満たさない下限。
MIN_SAMPLES_PARTIAL = 4
#: 4行すべてが満たす下限。**3件ちょうどは満たす**（`<` で比較する）。
MIN_SAMPLES_ALL_MET = 3

# --- 件数 0 / 1 / 2 の境界 -------------------------------------------------

#: 1件しか無い条件の値。**四分位範囲は算出できない**（上流は 0.0 を返すが、
#: 本実装は欠測とする——ここが上流との唯一の意図的差分である）。
SINGLE_VALUE_MS = 37.0
#: 2件ある条件。p25=28 / p50=33 / p75=38 → IQR=10（**算出できる**）。
PAIR_VALUES = (23.0, 43.0)
PAIR_P50 = 33.0
PAIR_IQR = 10.0

# --- 取りこぼし（**セグメントあたり**の件数。条件ごとの合計は × CYCLES）----

OFF_DROPPED_PER_SEGMENT = 7
OFF_DROPPED_TOTAL = 21
ON_DROPPED_PER_SEGMENT = 2
ON_DROPPED_TOTAL = 6
#: 増えた場合。21 < 27。
ON_DROPPED_INCREASED_PER_SEGMENT = 9
ON_DROPPED_INCREASED_TOTAL = 27


# ---------------------------------------------------------------------------
# フィクスチャの自己検査（タスク 6.3 の教訓）
# ---------------------------------------------------------------------------


class TestFixtureDiscipline:
    """**意味の違う数はフィクスチャ上でも別の値にする。**

    どれかが重なると、その2つを取り違える変異が生き延びる。
    """

    def test_all_headline_numbers_are_distinct(self) -> None:
        numbers = (
            OFF_PREDICT_P50,
            OFF_PREDICT_P95,
            OFF_PREDICT_IQR,
            OFF_PREDICT_RANGE,
            ON_PREDICT_SAME_P50,
            ON_PREDICT_SAME_P95,
            ON_PREDICT_SAME_IQR,
            DELTA_PREDICT_SAME,
            ON_PREDICT_LOAD_P50,
            ON_PREDICT_LOAD_IQR,
            DELTA_PREDICT_LOAD,
            OFF_E2E_P50,
            OFF_E2E_P95,
            OFF_E2E_IQR,
            ON_E2E_SAME_P50,
            ON_E2E_SAME_P95,
            ON_E2E_SAME_IQR,
            DELTA_E2E_SAME,
            float(SAMPLES_PER_CONDITION),
            float(OFF_DROPPED_TOTAL),
            float(ON_DROPPED_TOTAL),
            float(ON_DROPPED_INCREASED_TOTAL),
            float(CYCLES),
            float(MIN_SAMPLES),
        )
        assert len(set(numbers)) == len(numbers)

    def test_the_settings_do_not_coincide_with_the_defaults(self) -> None:
        """**テストが渡す設定値を実装の既定値と一致させない。**

        一致していると、「設定を無視して既定値を使う」実装が期待値と
        区別できない（タスク 6.1 の教訓）。将来 `OverheadConfig` の既定値が
        テスト側の値へ動いたら、このガードが落ちる。
        """
        defaults = OverheadConfig()
        assert CYCLES != defaults.cycles
        assert MIN_SAMPLES != defaults.min_samples
        assert CYCLES_2 != defaults.cycles
        assert MIN_SAMPLES_2 != defaults.min_samples


# ---------------------------------------------------------------------------
# 判定規則の説明文（**全文を一文ずつ**テスト局所のリテラルで置く）
# ---------------------------------------------------------------------------


def expected_criterion_sentences(*, cycles: str, min_samples: str) -> tuple[str, ...]:
    """規則の説明文の**期待される全文**を一文ずつ書き並べる。

    ここが本ファイルで最も load-bearing な定数である。覆い忘れた文は、
    削っても取り違えても誰も気づかない領域になる（タスク 6.1 の教訓）。
    したがって全文を覆い、**連結が `criterion` と厳密に一致する**ことまで
    固定する（削除・追加・**順序の入れ替え**がすべて落ちる）。
    """
    return (
        (
            "計測 ON/OFF 比較の判定基準"
            "（実測前に固定。design.md「OverheadBench」、要件 7.5-7.8）: "
        ),
        (
            "同一入力元・同一設定・同一時間で、計測の有無だけを変えて交互に実行する"
            f"（{CONDITION_OFF_NAME} → {CONDITION_ON_NAME} の1巡を "
            f"{cycles} 回繰り返す A/B/A/B。"
            "順序効果と、時間とともに変化する要因を打ち消すためであり、"
            "片方の条件をまとめて連続実行しない。要件 7.6）。"
        ),
        (
            f"対象区間は「{PREDICT_LABEL}」と「{E2E_LABEL}」の2つである（要件 7.4）。"
        ),
        (
            "本比較の対象区間は m1_validation が追加した predict 段と end-to-end であり、"
            "上流 Spec の区間（sensing_foundation の capture 区間、"
            "flying_object_tracking の detect / track 区間）とは別である。"
            "上流の計測 ON/OFF 比較の結果と混同しないこと。"
        ),
        # 残余コストの向き（**出力に残す**。無効条件にも乗る分は差に現れない）
        (
            "なお、計測を無効にした条件でも、送出の引数となる予測時刻の取得"
            "（session_clock_ms）と引数の組み立ては行われる"
            "（本比較が無効化できるのは構造化ログへの送出だけである）。"
            "この残余は両条件に共通で乗って差に現れないため、"
            "本比較は計測有効側のオーバーヘッドを過小評価する側、"
            "すなわち「有意に変化しない」へ倒れる側に偏る（要件 7.5）。"
        ),
        (
            f"判定は対象区間ごとに行い、{CONDITION_ON_NAME} 条件の中央値と "
            f"{CONDITION_OFF_NAME} 条件の中央値の差の絶対値が、"
            f"{CONDITION_OFF_NAME} 条件の四分位範囲（p75 − p25）以内であり、"
            "かつ取りこぼしが増えていない"
            f"（{CONDITION_ON_NAME} 条件の取りこぼし件数が "
            f"{CONDITION_OFF_NAME} 条件の件数以下である）とき"
            "「有意に変化しない」と判定する"
            "（median_delta_ms <= baseline_iqr_ms。境界値ちょうどは合格）。"
        ),
        (
            f"四分位範囲は無効条件（{CONDITION_OFF_NAME}）自身のばらつきであって、"
            "有効条件のばらつきでも、最大と最小の差でもない。"
        ),
        (
            "本判定基準の形は上流 Spec と同一である"
            "（sensing_foundation.bench.logging_overhead.LoggingOverheadBench および "
            "flying_object_tracking.bench.overhead。"
            "design.md「OverheadBench / Implementation Notes」: "
            "『同じ問いに違う基準を使わない』）。"
        ),
        (
            "判定に必要な値（中央値・四分位範囲・取りこぼし件数）が欠けている場合は"
            "「有意に変化しない」と判定せず、欠測として区別する（0 で埋めない）。"
        ),
        (
            "判定が偽の場合は、当該条件で得た計測結果を"
            "無条件に有効なものとして扱わない旨を結果に明示する（要件 7.8）。"
        ),
        "各条件の生の計測値を結果に残し、判定を後から再計算できるようにする。",
        (
            f"各条件・各対象区間の生の計測値が {min_samples} 件に満たない場合、"
            "結果に暫定の印を付ける"
            f"（{cycles} 回・{min_samples} 件は暫定の評価候補であって"
            "必須性能ではない。要件 13.7）。"
        ),
    )


CRITERION_1 = {"cycles": "3", "min_samples": "7"}
CRITERION_2 = {"cycles": "2", "min_samples": "20"}

#: 判定が偽のときにだけ出る注記（要件 7.8）。
UNCONDITIONAL_NOTE = (
    "本比較は「有意に変化しない」と判定されなかった。"
    "したがって当該条件で得た計測結果を無条件に有効なものとして扱わない（要件 7.8）。"
)

#: 上流の区間と混同させないための注記。
UPSTREAM_NOTE = (
    "本比較の対象区間は m1_validation が追加した predict 段と end-to-end であり、"
    "上流 Spec の区間（sensing_foundation の capture 区間、"
    "flying_object_tracking の detect / track 区間）とは別である。"
    "上流の計測 ON/OFF 比較の結果と混同しないこと。"
)

# --- 判定の理由（**相互排他**。取り違えると次にやることを間違える）----------

REASON_MEDIAN_EXCEEDED = (
    f"{CONDITION_ON_NAME} 条件と {CONDITION_OFF_NAME} 条件の中央値の差 "
    f"{DELTA_PREDICT_LOAD:.4f} ms が、{CONDITION_OFF_NAME} 条件の四分位範囲 "
    f"{OFF_PREDICT_IQR:.4f} ms を超えた"
)
REASON_MEDIAN_MISSING = (
    f"中央値または {CONDITION_OFF_NAME} 条件の四分位範囲が得られず、"
    "中央値の差を評価できなかった"
)
REASON_DROPPED_INCREASED = (
    f"取りこぼしが増えた（{CONDITION_ON_NAME}={ON_DROPPED_INCREASED_TOTAL} 件 > "
    f"{CONDITION_OFF_NAME}={OFF_DROPPED_TOTAL} 件）"
)
REASON_DROPPED_MISSING = (
    "取りこぼしの件数が得られず、増えていないことを確かめられなかった"
)


# ---------------------------------------------------------------------------
# 偽のセグメント実行器
# ---------------------------------------------------------------------------


class ScriptedSegments:
    """条件ごとに決め打ちの観測値を返す実行器。**受け取った要求を全部残す。**

    実行順そのものをここで観測できるようにしてある——`segment_order` だけを
    見ていると、順序を偽って記録する実装と区別が付かない。
    """

    def __init__(self, *, off: SegmentObservation, on: SegmentObservation) -> None:
        self._by_condition = {CONDITION_OFF_NAME: off, CONDITION_ON_NAME: on}
        self.requests: list[SegmentRequest] = []

    def __call__(self, request: SegmentRequest) -> SegmentObservation:
        self.requests.append(request)
        return self._by_condition[request.condition]


class PerCycleSegments:
    """**巡ごとに違う観測値**を返す実行器。

    条件ごとに固定の観測値しか返さない実行器では、「1セグメントだけ欠測」
    という形が作れない——取りこぼしの累積が `None` を飲み込む変異
    （一度欠測になっても後続のセグメントで整数へ戻る）が素通りする。
    """

    def __init__(
        self, *, off: list[SegmentObservation], on: list[SegmentObservation]
    ) -> None:
        self._by_condition = {CONDITION_OFF_NAME: off, CONDITION_ON_NAME: on}
        self.requests: list[SegmentRequest] = []

    def __call__(self, request: SegmentRequest) -> SegmentObservation:
        self.requests.append(request)
        return self._by_condition[request.condition][request.cycle - 1]


def observation(
    *,
    predict: tuple[float, ...],
    end_to_end: tuple[float, ...],
    frames_dropped: int | None,
) -> SegmentObservation:
    return SegmentObservation(
        predict_ms=predict, end_to_end_ms=end_to_end, frames_dropped=frames_dropped
    )


def off_observation(frames_dropped: int | None = OFF_DROPPED_PER_SEGMENT):
    return observation(
        predict=OFF_PREDICT, end_to_end=OFF_E2E, frames_dropped=frames_dropped
    )


def off_observation_with(
    *,
    predict: tuple[float, ...] = OFF_PREDICT,
    end_to_end: tuple[float, ...] = OFF_E2E,
    frames_dropped: int | None = OFF_DROPPED_PER_SEGMENT,
):
    """無効条件（**ベースライン**）側を痩せさせるための観測値。"""
    return observation(
        predict=predict, end_to_end=end_to_end, frames_dropped=frames_dropped
    )


def on_observation(
    *,
    predict: tuple[float, ...] = ON_PREDICT_SAME,
    end_to_end: tuple[float, ...] = ON_E2E_SAME,
    frames_dropped: int | None = ON_DROPPED_PER_SEGMENT,
):
    return observation(
        predict=predict, end_to_end=end_to_end, frames_dropped=frames_dropped
    )


@pytest.fixture
def layout_file(tmp_path: Path) -> Path:
    return write_layout(tmp_path)


def settings(
    layout_file: Path, *, cycles: int = CYCLES, min_samples: int = MIN_SAMPLES
) -> M1Settings:
    return M1Settings.resolve(
        file=None,
        env={},
        overrides={
            "layout_file": str(layout_file),
            "overhead_cycles": cycles,
            "overhead_min_samples": min_samples,
        },
    )


def run(
    layout_file: Path,
    *,
    off: SegmentObservation | None = None,
    on: SegmentObservation | None = None,
    cycles: int = CYCLES,
    min_samples: int = MIN_SAMPLES,
) -> tuple[OverheadReport, ScriptedSegments]:
    segments = ScriptedSegments(
        off=off_observation() if off is None else off,
        on=on_observation() if on is None else on,
    )
    report = run_overhead_bench(
        segments=segments,
        settings=settings(layout_file, cycles=cycles, min_samples=min_samples),
    )
    return report, segments


def verdict_of(report: OverheadReport, target: str) -> OverheadVerdict:
    for item in report.verdicts:
        if item.target == target:
            return item
    raise AssertionError(f"対象区間 {target!r} の判定が無い")


def stats_of(report: OverheadReport, *, condition: str, target: str):
    for item in report.stats:
        if item.condition == condition and item.target == target:
            return item
    raise AssertionError(f"{condition!r} / {target!r} の集計が無い")


# ---------------------------------------------------------------------------
# 1. 交互実行（要件 7.6）
# ---------------------------------------------------------------------------


class TestConditionsAlternate:
    """**交互に実行する。** まとめ実行はここで落ちる。"""

    def test_the_runner_is_called_in_alternating_order(self, layout_file: Path) -> None:
        """実行器が実際に受け取った条件の並びが A/B/A/B である。

        `segment_order` だけを見ていると、**まとめて実行しておいて順序だけ
        交互だと記録する**実装が素通りする。
        """
        _, segments = run(layout_file)
        assert [request.condition for request in segments.requests] == [
            CONDITION_OFF_NAME,
            CONDITION_ON_NAME,
            CONDITION_OFF_NAME,
            CONDITION_ON_NAME,
            CONDITION_OFF_NAME,
            CONDITION_ON_NAME,
        ]

    def test_the_recorded_segment_order_matches(self, layout_file: Path) -> None:
        report, segments = run(layout_file)
        assert report.segment_order == tuple(
            request.condition for request in segments.requests
        )
        assert report.segment_order == (
            CONDITION_OFF_NAME,
            CONDITION_ON_NAME,
        ) * CYCLES

    def test_each_request_carries_its_cycle_and_position(
        self, layout_file: Path
    ) -> None:
        """巡回番号と通し位置が実行器へ渡る（後から順序を再現できる）。"""
        _, segments = run(layout_file)
        assert [request.cycle for request in segments.requests] == [1, 1, 2, 2, 3, 3]
        assert [request.index for request in segments.requests] == [0, 1, 2, 3, 4, 5]

    def test_the_measurement_flag_matches_the_condition(
        self, layout_file: Path
    ) -> None:
        """条件名と計測の有無が食い違わない（**両方の値を通す**）。"""
        _, segments = run(layout_file)
        assert [request.measurement_enabled for request in segments.requests] == [
            False,
            True,
            False,
            True,
            False,
            True,
        ]

    def test_both_conditions_get_the_same_number_of_segments(
        self, layout_file: Path
    ) -> None:
        """**同一時間**（両条件が同じ回数だけ回る。要件 7.6）。"""
        _, segments = run(layout_file)
        conditions = [request.condition for request in segments.requests]
        assert conditions.count(CONDITION_OFF_NAME) == CYCLES
        assert conditions.count(CONDITION_ON_NAME) == CYCLES

    def test_the_cycle_count_comes_from_the_settings(self, layout_file: Path) -> None:
        """巡回数は設定から来る（実装の既定値を焼き付けていない）。"""
        _, segments = run(layout_file, cycles=CYCLES_2)
        assert len(segments.requests) == CYCLES_2 * 2
        assert [request.cycle for request in segments.requests] == [1, 1, 2, 2]


# ---------------------------------------------------------------------------
# 2. 集計（生値 → 中央値・四分位範囲）
# ---------------------------------------------------------------------------


class TestConditionStats:
    """条件 × 対象区間ごとの集計。**2区間の値はすべて別**である。"""

    def test_predict_statistics(self, layout_file: Path) -> None:
        report, _ = run(layout_file)
        off = stats_of(
            report, condition=CONDITION_OFF_NAME, target=TARGET_PREDICT_NAME
        )
        on = stats_of(report, condition=CONDITION_ON_NAME, target=TARGET_PREDICT_NAME)

        assert off.samples == SAMPLES_PER_CONDITION
        assert off.p50_ms == OFF_PREDICT_P50
        assert off.p95_ms == OFF_PREDICT_P95
        assert off.iqr_ms == OFF_PREDICT_IQR
        assert on.p50_ms == ON_PREDICT_SAME_P50
        assert on.p95_ms == ON_PREDICT_SAME_P95
        assert on.iqr_ms == ON_PREDICT_SAME_IQR

    def test_end_to_end_statistics(self, layout_file: Path) -> None:
        report, _ = run(layout_file)
        off = stats_of(report, condition=CONDITION_OFF_NAME, target=TARGET_E2E_NAME)
        on = stats_of(report, condition=CONDITION_ON_NAME, target=TARGET_E2E_NAME)

        assert off.p50_ms == OFF_E2E_P50
        assert off.p95_ms == OFF_E2E_P95
        assert off.iqr_ms == OFF_E2E_IQR
        assert on.p50_ms == ON_E2E_SAME_P50
        assert on.p95_ms == ON_E2E_SAME_P95
        assert on.iqr_ms == ON_E2E_SAME_IQR

    def test_every_condition_and_target_pair_is_present(
        self, layout_file: Path
    ) -> None:
        report, _ = run(layout_file)
        assert {(item.condition, item.target) for item in report.stats} == {
            (CONDITION_OFF_NAME, TARGET_PREDICT_NAME),
            (CONDITION_ON_NAME, TARGET_PREDICT_NAME),
            (CONDITION_OFF_NAME, TARGET_E2E_NAME),
            (CONDITION_ON_NAME, TARGET_E2E_NAME),
        }

    def test_each_stats_row_carries_its_target_label(self, layout_file: Path) -> None:
        report, _ = run(layout_file)
        assert (
            stats_of(
                report, condition=CONDITION_OFF_NAME, target=TARGET_PREDICT_NAME
            ).target_label
            == PREDICT_LABEL
        )
        assert (
            stats_of(
                report, condition=CONDITION_ON_NAME, target=TARGET_E2E_NAME
            ).target_label
            == E2E_LABEL
        )

    def test_an_empty_condition_yields_missing_values(self) -> None:
        """生値が1件も無ければ**欠測（`None`）であって 0 ではない**。"""
        empty = build_condition_stats(
            condition=CONDITION_ON_NAME, target=TARGET_PREDICT_NAME, values=()
        )
        assert empty.samples == 0
        assert empty.p50_ms is None
        assert empty.p95_ms is None
        assert empty.iqr_ms is None

    def test_a_single_sample_has_no_interquartile_range(self) -> None:
        """**1件から四分位範囲は作れない。** ここが上流との意図的な差分である。

        上流2実装の `_percentile` は n==1 で先頭の値を返すため、素直に
        `p75 - p25` を採ると **0.0** になる。0.0 を判定の基準に据えると、
        中央値の差がどれだけ小さくても `delta <= 0.0` が成り立たず、
        **あらゆる比較が「有意に変化した」へ倒れる**。したがって本実装は
        「ばらつきを測れていない」＝欠測として返す。

        `ConditionStats.iqr_ms` の docstring がこの不変条件を名指ししている
        ——**謳っただけでは固定されない**ので、ここで値として固定する。
        """
        single = build_condition_stats(
            condition=CONDITION_OFF_NAME,
            target=TARGET_PREDICT_NAME,
            values=(SINGLE_VALUE_MS,),
        )
        assert single.samples == 1
        assert single.p50_ms == SINGLE_VALUE_MS
        assert single.p95_ms == SINGLE_VALUE_MS
        assert single.iqr_ms is None
        assert single.iqr_ms != 0.0

    def test_two_samples_do_have_an_interquartile_range(self) -> None:
        """**2件からは算出する**（1件の欠測と対で置く境界の裏側）。

        片側だけを見ていると、「2件でも欠測にする」実装（`count < 3`）が
        素通りする。
        """
        pair = build_condition_stats(
            condition=CONDITION_OFF_NAME,
            target=TARGET_PREDICT_NAME,
            values=PAIR_VALUES,
        )
        assert pair.samples == 2
        assert pair.p50_ms == PAIR_P50
        assert pair.iqr_ms == PAIR_IQR


# ---------------------------------------------------------------------------
# 3. 判定基準（**上流 Spec と同一の形**。要件 7.7）
# ---------------------------------------------------------------------------


def stats(
    condition: str, *, p50: float | None, iqr: float | None, samples: int = 15
) -> ConditionStats:
    """判定だけを単体で見るための、手で組んだ集計。"""
    return ConditionStats(
        condition=condition,
        target=TARGET_PREDICT_NAME,
        target_label=PREDICT_LABEL,
        samples=samples,
        p50_ms=p50,
        p95_ms=None,
        iqr_ms=iqr,
    )


class TestCriterionShape:
    """中央値の差 vs **無効条件の**四分位範囲、かつ取りこぼしが増えていない。"""

    def test_the_difference_is_taken_against_the_baseline_iqr(self) -> None:
        """**無効条件の**四分位範囲が基準である（有効条件のものではない）。

        差 5.0 は無効条件の 8.0 以内だが有効条件の 4.0 は超える——
        どちらの条件の四分位範囲を使うかで判定が反転する入力である。
        """
        result = compute_verdict(
            on=stats(CONDITION_ON_NAME, p50=19.0, iqr=4.0),
            off=stats(CONDITION_OFF_NAME, p50=14.0, iqr=8.0),
            on_frames_dropped=ON_DROPPED_TOTAL,
            off_frames_dropped=OFF_DROPPED_TOTAL,
        )
        assert result.passed is True
        assert result.median_delta_ms == 5.0
        assert result.baseline_iqr_ms == 8.0

    def test_the_boundary_value_passes(self) -> None:
        """境界ちょうど（差 == 四分位範囲）は**合格**（「以内」）。"""
        result = compute_verdict(
            on=stats(CONDITION_ON_NAME, p50=22.0, iqr=4.0),
            off=stats(CONDITION_OFF_NAME, p50=14.0, iqr=8.0),
            on_frames_dropped=ON_DROPPED_TOTAL,
            off_frames_dropped=OFF_DROPPED_TOTAL,
        )
        assert result.median_delta_ms == 8.0
        assert result.passed is True

    def test_just_past_the_boundary_fails(self) -> None:
        result = compute_verdict(
            on=stats(CONDITION_ON_NAME, p50=22.5, iqr=4.0),
            off=stats(CONDITION_OFF_NAME, p50=14.0, iqr=8.0),
            on_frames_dropped=ON_DROPPED_TOTAL,
            off_frames_dropped=OFF_DROPPED_TOTAL,
        )
        assert result.median_delta_ms == 8.5
        assert result.passed is False

    def test_the_difference_is_absolute(self) -> None:
        """差は**絶対値**である。有効条件が速くなった場合も差として扱う。

        引く向きを固定しただけの実装（`on − off`）は、有効条件が大きく
        速くなった入力で負の値を返し、**どんな四分位範囲にも収まってしまう**。
        """
        result = compute_verdict(
            on=stats(CONDITION_ON_NAME, p50=2.0, iqr=4.0),
            off=stats(CONDITION_OFF_NAME, p50=14.0, iqr=8.0),
            on_frames_dropped=ON_DROPPED_TOTAL,
            off_frames_dropped=OFF_DROPPED_TOTAL,
        )
        assert result.median_delta_ms == 12.0
        assert result.passed is False

    def test_increased_drops_fail_even_when_the_median_agrees(self) -> None:
        """取りこぼしが増えたら、中央値が揃っていても偽である。"""
        result = compute_verdict(
            on=stats(CONDITION_ON_NAME, p50=14.0, iqr=4.0),
            off=stats(CONDITION_OFF_NAME, p50=14.0, iqr=8.0),
            on_frames_dropped=ON_DROPPED_INCREASED_TOTAL,
            off_frames_dropped=OFF_DROPPED_TOTAL,
        )
        assert result.median_delta_ms == 0.0
        assert result.dropped_not_increased is False
        assert result.passed is False

    def test_equal_drop_counts_pass(self) -> None:
        """「増えていない」は等しい場合を含む。"""
        result = compute_verdict(
            on=stats(CONDITION_ON_NAME, p50=14.0, iqr=4.0),
            off=stats(CONDITION_OFF_NAME, p50=14.0, iqr=8.0),
            on_frames_dropped=OFF_DROPPED_TOTAL,
            off_frames_dropped=OFF_DROPPED_TOTAL,
        )
        assert result.dropped_not_increased is True
        assert result.passed is True

    def test_missing_medians_are_undetermined_not_a_pass(self) -> None:
        """欠測は「有意に変化しない」ではない（**0 で埋めない**）。"""
        result = compute_verdict(
            on=stats(CONDITION_ON_NAME, p50=None, iqr=None, samples=0),
            off=stats(CONDITION_OFF_NAME, p50=14.0, iqr=8.0),
            on_frames_dropped=ON_DROPPED_TOTAL,
            off_frames_dropped=OFF_DROPPED_TOTAL,
        )
        assert result.median_delta_ms is None
        assert result.within_iqr is None
        assert result.passed is False

    def test_missing_drop_counts_are_undetermined_not_a_pass(self) -> None:
        result = compute_verdict(
            on=stats(CONDITION_ON_NAME, p50=19.0, iqr=4.0),
            off=stats(CONDITION_OFF_NAME, p50=14.0, iqr=8.0),
            on_frames_dropped=None,
            off_frames_dropped=OFF_DROPPED_TOTAL,
        )
        assert result.within_iqr is True
        assert result.dropped_not_increased is None
        assert result.passed is False

    def test_a_missing_baseline_iqr_is_undetermined_not_an_exception(self) -> None:
        """**無効条件側**の四分位範囲が欠けている場合（要件 7.8 の前提）。

        基準となるのは常に無効条件側なので、欠測のガードも無効条件側を見て
        いなければならない。有効条件側だけを見る実装は、この入力で
        `float <= None` を評価して**例外で落ちる**——落ちてしまえば
        「当該条件の計測結果を無条件に有効として扱わない」旨を出力に含める
        こともできない。**有効条件側だけの欠測では届かない経路である。**
        """
        result = compute_verdict(
            on=stats(CONDITION_ON_NAME, p50=19.0, iqr=4.0),
            off=build_condition_stats(
                condition=CONDITION_OFF_NAME,
                target=TARGET_PREDICT_NAME,
                values=(SINGLE_VALUE_MS,),
            ),
            on_frames_dropped=ON_DROPPED_TOTAL,
            off_frames_dropped=OFF_DROPPED_TOTAL,
        )
        assert result.baseline_iqr_ms is None
        assert result.median_delta_ms is None
        assert result.within_iqr is None
        assert result.passed is False
        assert REASON_MEDIAN_MISSING in result.detail
        assert result.unconditional_validity_note == UNCONDITIONAL_NOTE

    def test_a_missing_baseline_median_is_undetermined(self) -> None:
        """無効条件側の中央値が欠けている場合（欠測ガードの第2項）。"""
        result = compute_verdict(
            on=stats(CONDITION_ON_NAME, p50=19.0, iqr=4.0),
            off=stats(CONDITION_OFF_NAME, p50=None, iqr=None, samples=0),
            on_frames_dropped=ON_DROPPED_TOTAL,
            off_frames_dropped=OFF_DROPPED_TOTAL,
        )
        assert result.within_iqr is None
        assert result.passed is False

    def test_a_missing_baseline_drop_count_is_undetermined(self) -> None:
        """**無効条件側**の取りこぼし件数が欠けている場合。

        有効条件側だけを見るガードは、この入力で `int <= None` を評価する。
        """
        result = compute_verdict(
            on=stats(CONDITION_ON_NAME, p50=19.0, iqr=4.0),
            off=stats(CONDITION_OFF_NAME, p50=14.0, iqr=8.0),
            on_frames_dropped=ON_DROPPED_TOTAL,
            off_frames_dropped=None,
        )
        assert result.dropped_not_increased is None
        assert result.passed is False
        assert REASON_DROPPED_MISSING in result.detail


# ---------------------------------------------------------------------------
# 4. 判定が偽のときの注記（要件 7.8）
# ---------------------------------------------------------------------------


class TestUnconditionalValidityNote:
    """**偽のときだけ**「無条件に有効として扱わない」旨を出す。"""

    def test_absent_when_the_verdict_is_true(self, layout_file: Path) -> None:
        report, _ = run(layout_file)
        assert report.unconditional_validity_note is None
        for item in report.verdicts:
            assert item.passed is True
            assert item.unconditional_validity_note is None
            assert UNCONDITIONAL_NOTE not in item.detail
        assert report.judgement.evidence["unconditional_validity_note"] is None
        assert UNCONDITIONAL_NOTE not in report.judgement.rationale

    def test_present_when_a_load_difference_is_injected(
        self, layout_file: Path
    ) -> None:
        report, _ = run(layout_file, on=on_observation(predict=ON_PREDICT_LOAD))
        assert report.unconditional_validity_note == UNCONDITIONAL_NOTE
        failed = verdict_of(report, TARGET_PREDICT_NAME)
        assert failed.passed is False
        assert failed.unconditional_validity_note == UNCONDITIONAL_NOTE
        assert UNCONDITIONAL_NOTE in failed.detail
        assert report.judgement.evidence["unconditional_validity_note"] == (
            UNCONDITIONAL_NOTE
        )

    def test_the_passing_target_does_not_carry_the_note(
        self, layout_file: Path
    ) -> None:
        """区間ごとに判定するので、通った区間には注記が付かない。"""
        report, _ = run(layout_file, on=on_observation(predict=ON_PREDICT_LOAD))
        passed = verdict_of(report, TARGET_E2E_NAME)
        assert passed.passed is True
        assert passed.unconditional_validity_note is None
        assert UNCONDITIONAL_NOTE not in passed.detail

    def test_present_when_the_verdict_is_undetermined(
        self, layout_file: Path
    ) -> None:
        """判定できなかった場合も、結果を無条件に有効として扱わない。"""
        report, _ = run(layout_file, on=on_observation(frames_dropped=None))
        assert report.unconditional_validity_note == UNCONDITIONAL_NOTE

    def test_present_when_the_baseline_side_is_missing(
        self, layout_file: Path
    ) -> None:
        """**無効条件側**が欠けた場合も同じ（有効条件側だけの経路ではない）。"""
        report, _ = run(layout_file, off=off_observation_with(end_to_end=()))
        assert report.judgement.verdict == VERDICT_UNDETERMINED
        assert report.unconditional_validity_note == UNCONDITIONAL_NOTE
        assert REASON_MEDIAN_MISSING in verdict_of(report, TARGET_E2E_NAME).detail

    def test_present_when_the_baseline_drop_count_is_missing(
        self, layout_file: Path
    ) -> None:
        report, _ = run(layout_file, off=off_observation(frames_dropped=None))
        assert report.frames_dropped[CONDITION_OFF_NAME] is None
        assert report.frames_dropped[CONDITION_ON_NAME] == ON_DROPPED_TOTAL
        assert report.judgement.verdict == VERDICT_UNDETERMINED


# ---------------------------------------------------------------------------
# 5. 判定の理由（**相互排他**）
# ---------------------------------------------------------------------------


class TestVerdictDetail:
    """理由の文面を取り違えると、次にやるべきことを間違える。"""

    def test_median_exceeded(self, layout_file: Path) -> None:
        report, _ = run(layout_file, on=on_observation(predict=ON_PREDICT_LOAD))
        detail = verdict_of(report, TARGET_PREDICT_NAME).detail
        assert REASON_MEDIAN_EXCEEDED in detail
        assert REASON_MEDIAN_MISSING not in detail
        assert REASON_DROPPED_INCREASED not in detail
        assert REASON_DROPPED_MISSING not in detail

    def test_dropped_increased(self, layout_file: Path) -> None:
        report, _ = run(
            layout_file,
            on=on_observation(frames_dropped=ON_DROPPED_INCREASED_PER_SEGMENT),
        )
        detail = verdict_of(report, TARGET_PREDICT_NAME).detail
        assert REASON_DROPPED_INCREASED in detail
        assert REASON_MEDIAN_EXCEEDED not in detail
        assert REASON_MEDIAN_MISSING not in detail
        assert REASON_DROPPED_MISSING not in detail

    def test_dropped_missing(self, layout_file: Path) -> None:
        report, _ = run(layout_file, on=on_observation(frames_dropped=None))
        detail = verdict_of(report, TARGET_PREDICT_NAME).detail
        assert REASON_DROPPED_MISSING in detail
        assert REASON_DROPPED_INCREASED not in detail
        assert REASON_MEDIAN_EXCEEDED not in detail
        assert REASON_MEDIAN_MISSING not in detail

    def test_median_missing(self, layout_file: Path) -> None:
        report, _ = run(layout_file, on=on_observation(end_to_end=()))
        detail = verdict_of(report, TARGET_E2E_NAME).detail
        assert REASON_MEDIAN_MISSING in detail
        assert REASON_MEDIAN_EXCEEDED not in detail
        assert REASON_DROPPED_INCREASED not in detail
        assert REASON_DROPPED_MISSING not in detail

    def test_the_detail_names_its_own_target(self, layout_file: Path) -> None:
        """どの区間についての判定かが本文から読める（区間の取り違え防止）。"""
        report, _ = run(layout_file)
        assert PREDICT_LABEL in verdict_of(report, TARGET_PREDICT_NAME).detail
        assert E2E_LABEL not in verdict_of(report, TARGET_PREDICT_NAME).detail
        assert E2E_LABEL in verdict_of(report, TARGET_E2E_NAME).detail
        assert PREDICT_LABEL not in verdict_of(report, TARGET_E2E_NAME).detail


# ---------------------------------------------------------------------------
# 6. 判定値（3値を**すべて**通す）
# ---------------------------------------------------------------------------


class TestJudgementVerdict:
    def test_no_difference_is_unchanged(self, layout_file: Path) -> None:
        report, _ = run(layout_file)
        assert report.judgement.verdict == VERDICT_UNCHANGED
        assert report.judgement.question == QUESTION

    def test_a_load_difference_is_changed(self, layout_file: Path) -> None:
        """**人工的に負荷差を与えた入力で判定が偽になる**（完了状態）。"""
        report, _ = run(layout_file, on=on_observation(predict=ON_PREDICT_LOAD))
        assert report.judgement.verdict == VERDICT_CHANGED

    def test_increased_drops_are_changed(self, layout_file: Path) -> None:
        report, _ = run(
            layout_file,
            on=on_observation(frames_dropped=ON_DROPPED_INCREASED_PER_SEGMENT),
        )
        assert report.judgement.verdict == VERDICT_CHANGED

    def test_missing_values_are_undetermined(self, layout_file: Path) -> None:
        report, _ = run(layout_file, on=on_observation(end_to_end=()))
        assert report.judgement.verdict == VERDICT_UNDETERMINED

    def test_a_recognised_difference_outranks_a_missing_value(
        self, layout_file: Path
    ) -> None:
        """差が認められた事実は、欠測に埋もれさせない。"""
        report, _ = run(
            layout_file,
            on=on_observation(predict=ON_PREDICT_LOAD, frames_dropped=None),
        )
        assert report.judgement.verdict == VERDICT_CHANGED

    def test_the_rationale_is_mutually_exclusive(self, layout_file: Path) -> None:
        unchanged = run(layout_file)[0].judgement.rationale
        changed = run(layout_file, on=on_observation(predict=ON_PREDICT_LOAD))[
            0
        ].judgement.rationale
        undetermined = run(layout_file, on=on_observation(end_to_end=()))[
            0
        ].judgement.rationale

        assert "有意な差は観測されなかった" in unchanged
        assert "有意な差が観測された" in changed
        assert "確かめられなかった" in undetermined
        assert "有意な差が観測された" not in unchanged
        assert "有意な差は観測されなかった" not in changed
        assert "確かめられなかった" not in changed

    def test_the_rationale_names_the_failing_target(self, layout_file: Path) -> None:
        rationale = run(layout_file, on=on_observation(predict=ON_PREDICT_LOAD))[
            0
        ].judgement.rationale
        assert PREDICT_LABEL in rationale
        assert E2E_LABEL not in rationale


# ---------------------------------------------------------------------------
# 7. 対象区間の明示（上流の区間と混同させない）
# ---------------------------------------------------------------------------


class TestTargetSegmentsAreExplicit:
    def test_the_report_lists_both_target_labels(self, layout_file: Path) -> None:
        report, _ = run(layout_file)
        assert dict(report.target_labels) == {
            TARGET_PREDICT_NAME: PREDICT_LABEL,
            TARGET_E2E_NAME: E2E_LABEL,
        }

    def test_the_two_labels_are_different(self, layout_file: Path) -> None:
        """ラベルが互いに異なること**だけ**では値の固定にならないので、

        上の厳密一致と対で置く（タスク 6.3 の教訓）。
        """
        report, _ = run(layout_file)
        assert len(set(report.target_labels.values())) == 2

    def test_the_upstream_note_is_carried(self, layout_file: Path) -> None:
        report, _ = run(layout_file)
        assert report.upstream_segment_note == UPSTREAM_NOTE
        assert report.judgement.evidence["upstream_segment_note"] == UPSTREAM_NOTE

    def test_the_end_to_end_definition_comes_from_the_aggregator(
        self, layout_file: Path
    ) -> None:
        """end-to-end の定義は集計側（タスク 4.5）から**運ぶ**。書き直さない。"""
        report, _ = run(layout_file)
        assert report.end_to_end_definition == END_TO_END_DEFINITION
        assert (
            "ある観測の取得時刻から、その観測を含めた予測が得られるまで"
            in report.end_to_end_definition
        )

    def test_upstream_segment_names_are_not_claimed_as_targets(
        self, layout_file: Path
    ) -> None:
        """対象区間を上流の区間名にすり替える変異を落とす。"""
        report, _ = run(layout_file)
        assert set(report.target_labels) == {TARGET_PREDICT_NAME, TARGET_E2E_NAME}
        assert "capture" not in report.target_labels
        assert "detect" not in report.target_labels
        assert "track" not in report.target_labels


# ---------------------------------------------------------------------------
# 8. 生の計測値（判定を後から再計算できる）
# ---------------------------------------------------------------------------


class TestRawSamplesAreKept:
    def test_raw_samples_are_kept_per_condition_and_target(
        self, layout_file: Path
    ) -> None:
        report, _ = run(layout_file)
        assert report.raw_samples[CONDITION_OFF_NAME][TARGET_PREDICT_NAME] == (
            OFF_PREDICT * CYCLES
        )
        assert report.raw_samples[CONDITION_ON_NAME][TARGET_PREDICT_NAME] == (
            ON_PREDICT_SAME * CYCLES
        )
        assert report.raw_samples[CONDITION_OFF_NAME][TARGET_E2E_NAME] == (
            OFF_E2E * CYCLES
        )
        assert report.raw_samples[CONDITION_ON_NAME][TARGET_E2E_NAME] == (
            ON_E2E_SAME * CYCLES
        )

    def test_frames_dropped_are_summed_per_condition(self, layout_file: Path) -> None:
        report, _ = run(layout_file)
        assert report.frames_dropped[CONDITION_OFF_NAME] == OFF_DROPPED_TOTAL
        assert report.frames_dropped[CONDITION_ON_NAME] == ON_DROPPED_TOTAL

    def test_a_missing_segment_count_makes_the_condition_missing(
        self, layout_file: Path
    ) -> None:
        """1セグメントでも欠測なら条件の合計も欠測（**0 で埋めない**）。"""
        report, _ = run(layout_file, on=on_observation(frames_dropped=None))
        assert report.frames_dropped[CONDITION_ON_NAME] is None
        assert report.frames_dropped[CONDITION_OFF_NAME] == OFF_DROPPED_TOTAL

    def test_one_missing_segment_out_of_many_still_makes_it_missing(
        self, layout_file: Path
    ) -> None:
        """**欠測は後続のセグメントで埋め戻されない。**

        全セグメントが欠測の入力しか置かないと、累積が一度 `None` になっても
        次のセグメントで整数へ戻る実装（`(total or 0) + ...`）が素通りする。
        3巡のうち**真ん中の1巡だけ**を欠測にして、合計が欠測のままである
        ことを固定する。数えられなかった区間がある以上、合計は「増えていない
        ことを確かめられる値」ではない。
        """
        segments = PerCycleSegments(
            off=[off_observation() for _ in range(CYCLES)],
            on=[
                on_observation(),
                on_observation(frames_dropped=None),
                on_observation(),
            ],
        )
        report = run_overhead_bench(
            segments=segments, settings=settings(layout_file)
        )
        assert [request.cycle for request in segments.requests] == [1, 1, 2, 2, 3, 3]
        assert report.frames_dropped[CONDITION_ON_NAME] is None
        assert report.frames_dropped[CONDITION_OFF_NAME] == OFF_DROPPED_TOTAL
        assert report.judgement.verdict == VERDICT_UNDETERMINED

    def test_the_verdict_can_be_recomputed_from_the_written_raw_values(
        self, layout_file: Path, tmp_path: Path
    ) -> None:
        """**書き出した生値だけから判定を組み直せる。**

        集計値で置き換えた実装はここで落ちる。
        """
        report, _ = run(layout_file, on=on_observation(predict=ON_PREDICT_LOAD))
        path = write_overhead_report(report, tmp_path, "sess-6-4")
        payload = json.loads(path.read_text(encoding="utf-8"))

        raw = payload["raw_samples"]
        dropped = payload["frames_dropped"]
        recomputed = compute_verdict(
            on=build_condition_stats(
                condition=CONDITION_ON_NAME,
                target=TARGET_PREDICT_NAME,
                values=tuple(raw[CONDITION_ON_NAME][TARGET_PREDICT_NAME]),
            ),
            off=build_condition_stats(
                condition=CONDITION_OFF_NAME,
                target=TARGET_PREDICT_NAME,
                values=tuple(raw[CONDITION_OFF_NAME][TARGET_PREDICT_NAME]),
            ),
            on_frames_dropped=dropped[CONDITION_ON_NAME],
            off_frames_dropped=dropped[CONDITION_OFF_NAME],
        )
        original = verdict_of(report, TARGET_PREDICT_NAME)
        assert recomputed.passed == original.passed is False
        assert recomputed.median_delta_ms == original.median_delta_ms
        assert recomputed.baseline_iqr_ms == original.baseline_iqr_ms

    def test_the_output_path_is_named_after_the_session(self, tmp_path: Path) -> None:
        assert overhead_output_path(tmp_path, "sess-6-4") == (
            tmp_path / "overhead-sess-6-4.json"
        )

    def test_the_written_report_is_json_serialisable(
        self, layout_file: Path
    ) -> None:
        report, _ = run(layout_file)
        assert json.loads(json.dumps(report_to_dict(report), ensure_ascii=False))


# ---------------------------------------------------------------------------
# 9. 判定規則の説明文（3経路のうち `criterion`）
# ---------------------------------------------------------------------------


class TestCriterionText:
    def test_every_sentence_is_present(self, layout_file: Path) -> None:
        text = run(layout_file)[0].judgement.criterion
        for sentence in expected_criterion_sentences(**CRITERION_1):
            assert sentence in text, sentence

    def test_the_criterion_is_exactly_the_fixed_text(self, layout_file: Path) -> None:
        """連結が `criterion` と**厳密に一致**する（順序の入れ替えも落ちる）。"""
        text = run(layout_file)[0].judgement.criterion
        assert text == "".join(expected_criterion_sentences(**CRITERION_1))

    def test_the_criterion_matches_a_second_settings_point(
        self, layout_file: Path
    ) -> None:
        """**別の設定点でも全文が一致する**（規則が設定から組まれている固定）。"""
        text = run(
            layout_file, cycles=CYCLES_2, min_samples=MIN_SAMPLES_2
        )[0].judgement.criterion
        assert text == "".join(expected_criterion_sentences(**CRITERION_2))

    def test_the_default_settings_do_not_leak_into_the_criterion(
        self, layout_file: Path
    ) -> None:
        """既定値を焼き付けた実装を落とす（既定値は**禁止値**）。"""
        text = run(layout_file)[0].judgement.criterion
        assert f"{DEFAULT_CYCLES_TEXT} 回繰り返す" not in text
        assert f"{DEFAULT_MIN_SAMPLES_TEXT} 件に満たない" not in text

    def test_the_criterion_does_not_move_with_the_data(self, layout_file: Path) -> None:
        """規則の文面は結果によって変わらない（結果に合わせて動く規則は規則ではない）。"""
        passed = run(layout_file)[0].judgement.criterion
        failed = run(layout_file, on=on_observation(predict=ON_PREDICT_LOAD))[
            0
        ].judgement.criterion
        assert passed == failed

    def test_the_builder_reflects_its_arguments(self) -> None:
        assert overhead_criterion(cycles=CYCLES_2, min_samples=MIN_SAMPLES_2) == (
            "".join(expected_criterion_sentences(**CRITERION_2))
        )

    def test_the_report_and_the_judgement_carry_the_same_criterion(
        self, layout_file: Path
    ) -> None:
        report, _ = run(layout_file)
        assert report.criterion == report.judgement.criterion


# ---------------------------------------------------------------------------
# 10. 証跡（3経路のうち `evidence`）
# ---------------------------------------------------------------------------


class TestEvidence:
    """**公開フィールド・`evidence`・`criterion` は別の経路である。**"""

    def test_evidence_carries_the_settings_actually_used(
        self, layout_file: Path
    ) -> None:
        report, _ = run(layout_file)
        evidence = report.judgement.evidence
        assert evidence["cycles"] == CYCLES
        assert evidence["min_samples"] == MIN_SAMPLES
        assert evidence["cycles"] != OverheadConfig().cycles
        assert evidence["min_samples"] != OverheadConfig().min_samples

    def test_evidence_carries_the_segment_order(self, layout_file: Path) -> None:
        report, _ = run(layout_file)
        assert report.judgement.evidence["segment_order"] == list(
            (CONDITION_OFF_NAME, CONDITION_ON_NAME) * CYCLES
        )

    def test_evidence_carries_the_per_target_numbers(self, layout_file: Path) -> None:
        report, _ = run(layout_file)
        targets = report.judgement.evidence["targets"]
        assert targets[TARGET_PREDICT_NAME] == {
            "label": PREDICT_LABEL,
            "passed": True,
            "median_delta_ms": DELTA_PREDICT_SAME,
            "baseline_iqr_ms": OFF_PREDICT_IQR,
            "measurement_off_p50_ms": OFF_PREDICT_P50,
            "measurement_on_p50_ms": ON_PREDICT_SAME_P50,
            "measurement_off_samples": SAMPLES_PER_CONDITION,
            "measurement_on_samples": SAMPLES_PER_CONDITION,
        }
        assert targets[TARGET_E2E_NAME] == {
            "label": E2E_LABEL,
            "passed": True,
            "median_delta_ms": DELTA_E2E_SAME,
            "baseline_iqr_ms": OFF_E2E_IQR,
            "measurement_off_p50_ms": OFF_E2E_P50,
            "measurement_on_p50_ms": ON_E2E_SAME_P50,
            "measurement_off_samples": SAMPLES_PER_CONDITION,
            "measurement_on_samples": SAMPLES_PER_CONDITION,
        }

    def test_evidence_carries_the_drop_counts(self, layout_file: Path) -> None:
        report, _ = run(layout_file)
        assert report.judgement.evidence["frames_dropped"] == {
            CONDITION_OFF_NAME: OFF_DROPPED_TOTAL,
            CONDITION_ON_NAME: ON_DROPPED_TOTAL,
        }

    def test_evidence_carries_the_provisional_flag_both_ways(
        self, layout_file: Path
    ) -> None:
        """**真偽値は両方の値を通す入力を置く。**"""
        assert run(layout_file)[0].judgement.evidence["provisional"] is False
        assert (
            run(layout_file, min_samples=MIN_SAMPLES_2)[0].judgement.evidence[
                "provisional"
            ]
            is True
        )


# ---------------------------------------------------------------------------
# 11. 暫定の印（各項が単独で効く）
# ---------------------------------------------------------------------------


class TestProvisional:
    def test_not_provisional_when_everything_is_present(
        self, layout_file: Path
    ) -> None:
        report, _ = run(layout_file)
        assert report.judgement.provisional is False

    def test_too_few_samples_alone_sets_the_flag(self, layout_file: Path) -> None:
        """件数が下限に満たない項が**単独で**旗を立てる（判定は真のまま）。"""
        report, _ = run(layout_file, min_samples=MIN_SAMPLES_2)
        assert report.judgement.verdict == VERDICT_UNCHANGED
        assert report.judgement.provisional is True

    def test_one_thin_row_alone_sets_the_flag(self, layout_file: Path) -> None:
        """**4行のうち1行でも下限を割れば旗が立つ**（`any` であって `all` ではない）。

        end-to-end は「予測が成立した更新のみ」算入するので、**実運用では常に
        予測区間より件数が少ない**。全行が下限を割ったときだけ旗を立てる実装
        （`all`）は、薄い end-to-end 系列を**無印で「確定した結果」に昇格
        させる**——要件 13.7 と暫定の印の目的に正面から反する。

        判定は真のまま（欠測による判定不能の項は立っていない）なので、
        旗を立てているのは**件数の項だけ**である。
        """
        report, _ = run(
            layout_file,
            off=off_observation_with(end_to_end=E2E_THIN_OFF),
            on=on_observation(end_to_end=E2E_THIN_ON),
            cycles=CYCLES_ONE,
            min_samples=MIN_SAMPLES_PARTIAL,
        )
        assert (
            stats_of(
                report, condition=CONDITION_OFF_NAME, target=TARGET_PREDICT_NAME
            ).samples
            == PREDICT_COUNT_ONE_CYCLE
        )
        assert (
            stats_of(
                report, condition=CONDITION_OFF_NAME, target=TARGET_E2E_NAME
            ).samples
            == THIN_E2E_COUNT
        )
        assert report.judgement.verdict == VERDICT_UNCHANGED
        assert report.judgement.provisional is True

    def test_the_same_input_is_not_provisional_when_every_row_meets_the_floor(
        self, layout_file: Path
    ) -> None:
        """同じ入力でも、**全行が下限を満たせば**旗は立たない（対で置く）。

        下限ちょうど（3件 = 下限 3）は満たすとみなす。
        """
        report, _ = run(
            layout_file,
            off=off_observation_with(end_to_end=E2E_THIN_OFF),
            on=on_observation(end_to_end=E2E_THIN_ON),
            cycles=CYCLES_ONE,
            min_samples=MIN_SAMPLES_ALL_MET,
        )
        assert report.judgement.verdict == VERDICT_UNCHANGED
        assert report.judgement.provisional is False

    def test_a_recognised_difference_is_not_provisional(
        self, layout_file: Path
    ) -> None:
        """**差が認められた結果は暫定ではない。**

        暫定の印は「材料が足りず判断に用いてよい状態ではない」ことを示す
        （要件 5.10 / 13.7）。差が観測されたという事実そのものは確定した
        所見であり、警告は `unconditional_validity_note` が運ぶ。`provisional`
        を「判定が真でない」に広げると、2つの別の意味が1つに畳まれる。
        """
        report, _ = run(layout_file, on=on_observation(predict=ON_PREDICT_LOAD))
        assert report.judgement.verdict == VERDICT_CHANGED
        assert report.judgement.provisional is False

    def test_an_undetermined_verdict_alone_sets_the_flag(
        self, layout_file: Path
    ) -> None:
        """欠測による判定不能が**単独で**旗を立てる（件数は下限を満たす）。"""
        report, _ = run(layout_file, on=on_observation(frames_dropped=None))
        assert report.judgement.verdict == VERDICT_UNDETERMINED
        assert (
            stats_of(
                report, condition=CONDITION_ON_NAME, target=TARGET_PREDICT_NAME
            ).samples
            >= MIN_SAMPLES
        )
        assert report.judgement.provisional is True


# ---------------------------------------------------------------------------
# 12. 実行条件の検証
# ---------------------------------------------------------------------------


class TestSettingsAreValidated:
    def test_non_positive_cycles_are_rejected_at_startup(
        self, layout_file: Path
    ) -> None:
        with pytest.raises(Exception, match="overhead_cycles"):
            settings(layout_file, cycles=0)

    def test_non_positive_min_samples_are_rejected_at_startup(
        self, layout_file: Path
    ) -> None:
        with pytest.raises(Exception, match="overhead_min_samples"):
            settings(layout_file, min_samples=0)

    def test_the_settings_are_shown_with_a_provisional_notice(
        self, layout_file: Path
    ) -> None:
        described = settings(layout_file).describe()
        assert described["overhead"] == {
            "cycles": CYCLES,
            "min_samples": MIN_SAMPLES,
        }
        notice = str(described["provisional_notice"])
        assert "overhead_cycles" in notice
        assert "overhead_min_samples" in notice


# ---------------------------------------------------------------------------
# 13. 実際の投擲を1セグメントとして回す実行器
# ---------------------------------------------------------------------------

WIDTH_PX = 8
HEIGHT_PX = 6

#: 観測時刻（**0 を基準にしない**。タスク 2.2 の教訓）。
SAMPLE_T_MS = (5000.0, 5033.0, 5066.0, 5099.0, 5132.0, 5165.0)
#: 更新1件ごとの end-to-end。**観測時刻とも予測区間とも別の値**にしてある。
EXPECTED_E2E_MS = (100.0, 110.0, 120.0, 130.0, 140.0, 150.0)
#: 予測が成立しない先頭の更新数（`prediction_core` の `min_samples` は 3 なので、
#: 1件目と2件目の更新には予測が付かない）。
INVALID_UPDATE_COUNT = 2
#: end-to-end に算入されるのは**予測が成立した更新だけ**である
#: （`END_TO_END_DEFINITION`: 「予測が成立しなかった更新は算入しない」）。
EXPECTED_VALID_E2E_MS = EXPECTED_E2E_MS[INVALID_UPDATE_COUNT:]
#: 予測区間の期待値（注入した単調時計の差分）。
EXPECTED_PREDICT_MS = (2.0, 3.0, 4.0, 5.0, 6.0, 7.0)
MONOTONIC_START_MS = 1000.0

PROBE_BEFORE = 40
PROBE_AFTER = 53
PROBE_DELTA = 13


class FakePipeline:
    """フレーム1枚につき1点を追加する追跡パイプライン（`process()` だけ）。"""

    def __init__(self, points: list[CameraPoint]) -> None:
        self._points = points
        self._appended: list[TrackPoint] = []

    def process(self, frame: object) -> TrackUpdate:
        del frame
        index = len(self._appended)
        appended: TrackPoint | None = None
        if index < len(self._points):
            appended = TrackPoint(
                point=self._points[index],
                frame_index=index,
                frame_seq=1000 + index,
                gap_before=0,
                rivals=0,
            )
            self._appended.append(appended)
        return TrackUpdate(
            track=CameraTrack(
                handoff_version=HANDOFF_VERSION,
                frame=CoordinateFrame.CAMERA,
                track_id=7,
                started_t_ms=SAMPLE_T_MS[0],
                points=tuple(self._appended),
                state=TrackState.TRACKING if self._appended else TrackState.IDLE,
                end_reason=None,
                source=SourceKind.SIMULATED,
                detector_kind="depth_band",
            ),
            appended=appended,
            candidates=1 if appended is not None else 0,
            rejections=(),
            point_failures=(),
        )


class FakeGateway:
    """`run_throw` が実際に使う入口だけを持つ窓口のダブル。

    上流パッケージを一切要さないので、**実機どころか合成入力の供給元すら
    無しに**セグメント実行器を検証できる（要件 12.1）。
    """

    source_kind = "simulated"

    def __init__(self, *, frame_count: int, clock_values: list[float]) -> None:
        self._frame_count = frame_count
        self._clock_values = list(clock_values)
        self.emitted: list[tuple[str, str, dict[str, object]]] = []

    def get_logger_handle(self) -> object:
        return self

    def open_frames(self, *, supplier: object = None):
        del supplier
        return iter(range(self._frame_count))

    def session_clock_ms(self) -> float:
        return self._clock_values.pop(0)

    def emit(self, stage: str, event: str, data) -> None:
        self.emitted.append((stage, event, dict(data)))


def camera_points() -> list[CameraPoint]:
    return [
        CameraPoint(
            frame=CoordinateFrame.CAMERA,
            t_ms=t_ms,
            x_mm=0.0,
            y_mm=0.0,
            z_mm=2500.0 - 60.0 * index,
            valid_depth_px=40,
            depth_spread_mm=10.0,
            apparent_diameter_px=9.0,
            expected_diameter_px=8.5,
            intrinsics_source="stream_profile",
        )
        for index, t_ms in enumerate(SAMPLE_T_MS)
    ]


def make_runner(
    layout_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    dropped_probe=None,
) -> tuple[ThrowSegmentRunner, FakeGateway]:
    from world_frame_calibration import load_calibration

    calibration_path = write_calibration(
        tmp_path, verification=verification_summary()
    )
    loaded = load_calibration(calibration_path)
    monkeypatch.setattr(
        runner_module, "open_tracking", lambda *a, **k: FakePipeline(camera_points())
    )

    gateway = FakeGateway(
        frame_count=len(SAMPLE_T_MS),
        clock_values=[
            t_ms + e2e for t_ms, e2e in zip(SAMPLE_T_MS, EXPECTED_E2E_MS, strict=True)
        ],
    )
    ticks = [MONOTONIC_START_MS]
    for delta in EXPECTED_PREDICT_MS:
        ticks.append(ticks[-1] + delta)

    runner = ThrowSegmentRunner(
        settings=settings(layout_file),
        gateway=gateway,
        calibration_path=calibration_path,
        tracking_settings=object(),
        signature=loaded.signature,
        intrinsics=loaded.intrinsics,
        monotonic_ms=lambda: ticks.pop(0),
        dropped_probe=dropped_probe,
    )
    return runner, gateway


def runner_for(
    layout_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> SegmentObservation:
    runner, _ = make_runner(layout_file, tmp_path, monkeypatch)
    return runner(request_for(enabled=False))


def request_for(*, enabled: bool) -> SegmentRequest:
    return SegmentRequest(
        condition=CONDITION_ON_NAME if enabled else CONDITION_OFF_NAME,
        measurement_enabled=enabled,
        cycle=1,
        index=0 if not enabled else 1,
    )


class TestThrowSegmentRunner:
    """1投擲を1セグメントとして回し、生の計測値を取り出す。"""

    def test_measurement_off_suppresses_the_emission(
        self, layout_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**計測を無効にすると送出しない**（要件 7.5）。"""
        runner, gateway = make_runner(layout_file, tmp_path, monkeypatch)
        runner(request_for(enabled=False))
        assert gateway.emitted == []

    def test_measurement_on_forwards_the_emission(
        self, layout_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner, gateway = make_runner(layout_file, tmp_path, monkeypatch)
        runner(request_for(enabled=True))
        assert [stage for stage, _, _ in gateway.emitted] == ["predict"] * len(
            SAMPLE_T_MS
        )

    def test_both_conditions_observe_the_same_number_of_samples(
        self, layout_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """計測の有無で**観測できる件数が変わらない**（比較が成立する条件）。"""
        runner, _ = make_runner(layout_file, tmp_path, monkeypatch)
        off = runner(request_for(enabled=False))
        runner2, _ = make_runner(layout_file, tmp_path, monkeypatch)
        on = runner2(request_for(enabled=True))
        assert len(off.predict_ms) == len(on.predict_ms) == len(SAMPLE_T_MS)
        assert len(off.end_to_end_ms) == len(on.end_to_end_ms) == len(
            EXPECTED_VALID_E2E_MS
        )

    def test_the_predict_samples_come_from_the_injected_clock(
        self, layout_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner, _ = make_runner(layout_file, tmp_path, monkeypatch)
        assert runner(request_for(enabled=False)).predict_ms == EXPECTED_PREDICT_MS

    def test_the_end_to_end_samples_follow_the_aggregator_definition(
        self, layout_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`predicted_at_ms − sample_t_ms`（タスク 4.5 の定義そのもの）。

        **予測が成立しなかった更新は算入しない。** 先頭2件を算入する実装は
        「予測が得られるまで」ではない量を end-to-end として出すことになる。
        """
        observed = runner_for(layout_file, tmp_path, monkeypatch)
        assert observed.end_to_end_ms == EXPECTED_VALID_E2E_MS
        assert EXPECTED_E2E_MS[0] not in observed.end_to_end_ms
        assert EXPECTED_E2E_MS[1] not in observed.end_to_end_ms

    def test_frames_dropped_is_the_difference_of_the_probe(
        self, layout_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        readings = [PROBE_BEFORE, PROBE_AFTER]
        runner, _ = make_runner(
            layout_file, tmp_path, monkeypatch, dropped_probe=lambda: readings.pop(0)
        )
        assert runner(request_for(enabled=False)).frames_dropped == PROBE_DELTA

    def test_frames_dropped_is_missing_without_a_probe(
        self, layout_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**測っていない**を 0 で埋めない。"""
        runner, _ = make_runner(layout_file, tmp_path, monkeypatch)
        assert runner(request_for(enabled=False)).frames_dropped is None
