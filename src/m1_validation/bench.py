"""計測 ON/OFF 比較（計測が計測対象を歪めないことの確認）。

design.md「Components and Interfaces / L7: 判断 / OverheadBench」、
tasks.md タスク 6.4、要件 7.5, 7.6, 7.7, 7.8。

`tech.md` 開発標準5「計測が計測対象を歪めないこと」を、**本 Spec が足した
区間**——予測区間（`predict` 段）と end-to-end——について実測で確認する。
問いは「どちらの実装が速いか」ではなく、**「計測を有効にすることで処理時間が
計測できないほど歪むか」**である。

==============================================================================
判定基準は上流 Spec と同一の形にする（要件 7.7。**最重要**）
==============================================================================

design.md「OverheadBench / Implementation Notes」は明記する: 「判定基準の形は
上流（`sensing-foundation` の `LoggingOverheadBench`、`flying-object-tracking`
の `bench-overhead`）と**同一の形**にする。同じ問いに違う基準を使わない」。

上流2実装を実際に読んで確認した判定式は次のとおりである
（`src/sensing_foundation/bench/logging_overhead.py` `_compute_verdict()` と
`src/flying_object_tracking/bench/overhead.py` `compute_verdict()`）:

    median_delta_ms = abs(on.total_ms_p50 - off.total_ms_p50)
    baseline_iqr_ms = off.total_ms_iqr            # ← **OFF 条件**の IQR
    passed = median_delta_ms <= baseline_iqr_ms   # ← 境界値は合格（「以内」）
    #  sensing-foundation はさらに on.frames_dropped <= off.frames_dropped

本モジュールの `compute_verdict()` はこの式をそのまま踏襲する。

- 差は **ON と OFF の中央値の差の絶対値**である（引く向きを固定した実装は、
  ON が大きく速くなった入力で負の値を返し、どんな四分位範囲にも収まる）
- 基準は **OFF 条件（＝計測無効側）自身の四分位範囲**である。有効条件の
  ばらつきでも、最大と最小の差でもない。**要件 7.7 が言う「無効条件自身の
  ばらつきに対する相対量」とはこれのことである**
- 比較は `<=`。「以内」を文字どおり実装し、境界値ちょうどを合格とする
- **取りこぼしが増えていないこと**を第2項として要求する（`sensing-foundation`
  と同じ。`flying-object-tracking` は `detect`/`track` 層に対応する計数が
  無いため落としているが、本 Spec は取得側の取りこぼしを
  `LatencyAggregator`（タスク 4.5）が読めるので落とさない。
  **タスク 6.4 の箇条も「かつ取りこぼしが増えていない」を明示している**）

**この基準は絶対値の目標を置かない**（`tech.md` 開発標準1「未実測の数値を
合否条件にしない」）。比べているのは同一測定内で得た量どうしだけである。

==============================================================================
対象区間（要件 7.4。**上流の区間と混同させない**）
==============================================================================

design.md「OverheadBench / Risks」: 「本 Spec の計測対象は `predict` 区間と
end-to-end であり、上流の区間とは別である。出力に対象区間を明示する」。

- **予測区間**: `runner.py` が `predict` 段へ送出する更新1件あたりの総処理
  時間。1つの予測更新の境界から次の境界までを外側から計った壁時計時間である
- **end-to-end**: `predicted_at_ms - sample_t_ms`。定義そのものは
  `LatencyAggregator`（タスク 4.5）が持つ `END_TO_END_DEFINITION` を
  **運ぶだけ**であり、本モジュールで書き直さない（2つの定義文が並ぶと、
  食い違ったときにどちらが正しいのか決められなくなる）

上流の区間（`sensing_foundation` の `capture`、`flying_object_tracking` の
`detect` / `track`）とは**別の量**である。上流の計測 ON/OFF 比較の結果と
並べて読むと、同じ「計測オーバーヘッド」という語で違う区間の数字を比べる
ことになる。`OverheadReport.upstream_segment_note` がこの旨を結果に残す。

==============================================================================
交互実行（要件 7.6）
==============================================================================

`(measurement_off, measurement_on)` の1巡を `OverheadConfig.cycles` 回
繰り返す **A/B/A/B** である。片方の条件をまとめて連続実行すると、時間と
ともに変化する要因（熱・他プロセスの負荷）が条件の差として現れる——
**交互実行はそれを打ち消すためにある**（上流 `sensing-foundation` design.md
「根拠3」が同じことを書いている）。

実行した順序は `OverheadReport.segment_order` に残す。**順序は結果から
検証できなければならない**——「交互に回した」という主張が本文にしか無いと、
まとめ実行へ退化しても誰も気づかない。

==============================================================================
実機を要さない（要件 12.1）
==============================================================================

1セグメントの実行は `SegmentRunner`（呼び出し側から渡す）に委ねる。本
モジュールが持つのは**交互実行の駆動・集計・判定・出力の形**だけである。
`ThrowSegmentRunner` が実体を1つ提供し、これは `runner.run_throw()` を
1投擲＝1セグメントとして回す——入力元は記録済みでも合成でもよく、**実機を
必要としない**（実機での再実行はタスク 9.x）。

**時間の計測を注入できる形にしてある**（`monotonic_ms`）。壁時計に依存した
判定はテストで固定できず、「人工的に負荷差を与えた入力で判定が偽になる」
という完了状態を確かめられない。

本モジュールは L7 層であり、`errors` / `types` / `config` / `metrics` /
`runner` と標準ライブラリだけを参照する。上流3パッケージ
（`sensing_foundation` / `flying_object_tracking` / `world_frame_calibration`）
を直接 import しない（要件 13.1。接点は `upstream.py` / `seam.py`）。
数値計算は標準ライブラリだけで書く。
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from m1_validation.config import M1Settings
from m1_validation.metrics.latency import (
    END_TO_END_DEFINITION,
    PREDICT_UPDATE_EVENT,
)
from m1_validation.runner import run_throw
from m1_validation.types import Judgement

__all__ = [
    "CONDITIONS",
    "CONDITION_OFF",
    "CONDITION_ON",
    "OVERHEAD_QUESTION",
    "TARGETS",
    "TARGET_END_TO_END",
    "TARGET_LABELS",
    "TARGET_PREDICT",
    "UNCONDITIONAL_VALIDITY_NOTE",
    "UPSTREAM_SEGMENT_NOTE",
    "VERDICT_CHANGED",
    "VERDICT_UNCHANGED",
    "VERDICT_UNDETERMINED",
    "ConditionStats",
    "OverheadReport",
    "OverheadVerdict",
    "SegmentObservation",
    "SegmentRequest",
    "SegmentRunner",
    "ThrowSegmentRunner",
    "build_condition_stats",
    "compute_verdict",
    "overhead_criterion",
    "overhead_output_path",
    "report_to_dict",
    "run_overhead_bench",
    "write_overhead_report",
]


# ---------------------------------------------------------------------------
# 語彙
# ---------------------------------------------------------------------------

#: `Judgement.question`。
OVERHEAD_QUESTION: str = "measurement_overhead"

#: 計測を無効にした条件（**ベースライン**。判定の基準となる四分位範囲は
#: 常にこちら側から採る）。
CONDITION_OFF: str = "measurement_off"

#: 計測を有効にした条件。
CONDITION_ON: str = "measurement_on"

#: 交互実行の1巡。**この並び自体が「無効が先」という規約**である。
CONDITIONS: tuple[str, str] = (CONDITION_OFF, CONDITION_ON)

#: 対象区間: 予測区間（本 Spec が足した `predict` 段）。
TARGET_PREDICT: str = "predict"

#: 対象区間: end-to-end。
TARGET_END_TO_END: str = "end_to_end"

#: 対象区間の並び（出力の順序でもある）。
TARGETS: tuple[str, str] = (TARGET_PREDICT, TARGET_END_TO_END)

#: 対象区間の表示名。**上流の区間名にしない**（要件 7.4 / design.md Risks）。
TARGET_LABELS: Mapping[str, str] = {
    TARGET_PREDICT: "予測区間（predict 段の1予測あたり総処理時間）",
    TARGET_END_TO_END: "end-to-end（観測時刻からその観測を含めた予測が得られるまで）",
}

#: 判定値。「有意に変化しない」。
VERDICT_UNCHANGED: str = "not_significantly_changed"

#: 判定値。差が認められた。
VERDICT_CHANGED: str = "significantly_changed"

#: 判定値。判定に必要な値が欠けており、確かめられなかった。
#:
#: **「差が無かった」に丸めない。** 測っていないことを「歪んでいない」と
#: 記録すると、その計測結果がそのまま判断（OQ-27）へ入る。
VERDICT_UNDETERMINED: str = "undetermined"


# ---------------------------------------------------------------------------
# 出力へ載せる説明文（**結果と同じ場所に置く**）
# ---------------------------------------------------------------------------

#: 上流の区間と混同させないための注記（design.md「OverheadBench」Risks）。
UPSTREAM_SEGMENT_NOTE: str = (
    "本比較の対象区間は m1_validation が追加した predict 段と end-to-end であり、"
    "上流 Spec の区間（sensing_foundation の capture 区間、"
    "flying_object_tracking の detect / track 区間）とは別である。"
    "上流の計測 ON/OFF 比較の結果と混同しないこと。"
)

#: **判定が偽のときにだけ**出す注記（要件 7.8）。
#:
#: 真のときに出してはならない——常に出ていると、読み手はこの一文を
#: 「決まり文句」として読み飛ばすようになり、本当に歪んでいた回の警告が
#: 埋もれる。
UNCONDITIONAL_VALIDITY_NOTE: str = (
    "本比較は「有意に変化しない」と判定されなかった。"
    "したがって当該条件で得た計測結果を無条件に有効なものとして扱わない（要件 7.8）。"
)

_CRITERION_TEMPLATE: str = (
    "計測 ON/OFF 比較の判定基準"
    "（実測前に固定。design.md「OverheadBench」、要件 7.5-7.8）: "
    "同一入力元・同一設定・同一時間で、計測の有無だけを変えて交互に実行する"
    "（{off} → {on} の1巡を {cycles} 回繰り返す A/B/A/B。"
    "順序効果と、時間とともに変化する要因を打ち消すためであり、"
    "片方の条件をまとめて連続実行しない。要件 7.6）。"
    "対象区間は「{predict_label}」と「{end_to_end_label}」の2つである（要件 7.4）。"
    "{upstream_note}"
    "なお、計測を無効にした条件でも、送出の引数となる予測時刻の取得"
    "（session_clock_ms）と引数の組み立ては行われる"
    "（本比較が無効化できるのは構造化ログへの送出だけである）。"
    "この残余は両条件に共通で乗って差に現れないため、"
    "本比較は計測有効側のオーバーヘッドを過小評価する側、"
    "すなわち「有意に変化しない」へ倒れる側に偏る（要件 7.5）。"
    "判定は対象区間ごとに行い、{on} 条件の中央値と "
    "{off} 条件の中央値の差の絶対値が、"
    "{off} 条件の四分位範囲（p75 − p25）以内であり、"
    "かつ取りこぼしが増えていない"
    "（{on} 条件の取りこぼし件数が {off} 条件の件数以下である）とき"
    "「有意に変化しない」と判定する"
    "（median_delta_ms <= baseline_iqr_ms。境界値ちょうどは合格）。"
    "四分位範囲は無効条件（{off}）自身のばらつきであって、"
    "有効条件のばらつきでも、最大と最小の差でもない。"
    "本判定基準の形は上流 Spec と同一である"
    "（sensing_foundation.bench.logging_overhead.LoggingOverheadBench および "
    "flying_object_tracking.bench.overhead。"
    "design.md「OverheadBench / Implementation Notes」: "
    "『同じ問いに違う基準を使わない』）。"
    "判定に必要な値（中央値・四分位範囲・取りこぼし件数）が欠けている場合は"
    "「有意に変化しない」と判定せず、欠測として区別する（0 で埋めない）。"
    "判定が偽の場合は、当該条件で得た計測結果を"
    "無条件に有効なものとして扱わない旨を結果に明示する（要件 7.8）。"
    "各条件の生の計測値を結果に残し、判定を後から再計算できるようにする。"
    "各条件・各対象区間の生の計測値が {min_samples} 件に満たない場合、"
    "結果に暫定の印を付ける"
    "（{cycles} 回・{min_samples} 件は暫定の評価候補であって"
    "必須性能ではない。要件 13.7）。"
)


def overhead_criterion(*, cycles: int, min_samples: int) -> str:
    """実際に適用する判定基準の説明文を組み立てる（要件 7.7）。

    **実際に使った実行条件（巡回数・件数の下限）を文面へ入れる。** 規則の文
    だけを残して数値を伏せると、同じ文で違う実行が正当化できてしまう
    （`oq27.oq27_criterion` / `oq05.oq05_criterion` と同じ理由）。

    文面は**計測結果によって変わらない**。結果に合わせて動く規則は規則では
    ない（要件 7.7 が「実測前に確定した基準」を求めているのはそのためである）。
    """
    return _CRITERION_TEMPLATE.format(
        off=CONDITION_OFF,
        on=CONDITION_ON,
        cycles=cycles,
        min_samples=min_samples,
        predict_label=TARGET_LABELS[TARGET_PREDICT],
        end_to_end_label=TARGET_LABELS[TARGET_END_TO_END],
        upstream_note=UPSTREAM_SEGMENT_NOTE,
    )


# --- 判定の理由（**相互排他**。取り違えると次にやることを間違える）----------
#
# 「差が出た」「そもそも測れていない」「取りこぼしが増えた」「取りこぼしを
# 数えていない」は、次に打つ手がそれぞれ違う。判定値はどれも偽なので
# verdict では区別が付かず、文面だけがその区別を運ぶ（タスク 5.2 の教訓）。

_REASON_MEDIAN_EXCEEDED: str = (
    "{on} 条件と {off} 条件の中央値の差 {delta:.4f} ms が、"
    "{off} 条件の四分位範囲 {iqr:.4f} ms を超えた"
)
_REASON_MEDIAN_MISSING: str = (
    "中央値または {off} 条件の四分位範囲が得られず、中央値の差を評価できなかった"
)
_REASON_DROPPED_INCREASED: str = (
    "取りこぼしが増えた（{on}={on_dropped} 件 > {off}={off_dropped} 件）"
)
_REASON_DROPPED_MISSING: str = (
    "取りこぼしの件数が得られず、増えていないことを確かめられなかった"
)

_DETAIL_PASSED: str = (
    "{label}: {on} は {off} との比較で有意な差が観測されなかった"
    "（median_delta_ms={delta:.4f} <= baseline_iqr_ms={iqr:.4f}、"
    "取りこぼし {on_dropped} <= {off_dropped}）。"
)
_DETAIL_FAILED: str = "{label}: 「有意に変化しない」とは判定しなかった。理由: {reasons}。"

# --- 判断の理由（**相互排他**。3値それぞれに別の文を持たせる）--------------

_RATIONALE_UNCHANGED: str = (
    "対象区間 {labels} のいずれについても、計測の有無による有意な差は観測されなかった。"
)
_RATIONALE_CHANGED: str = (
    "対象区間 {labels} で計測の有無による有意な差が観測された。"
    "当該条件で得た計測結果を無条件に有効なものとして扱わない。"
)
_RATIONALE_UNDETERMINED: str = (
    "対象区間 {labels} について、判定に必要な値が欠けており"
    "「有意に変化しない」ことを確かめられなかった。"
)


# ---------------------------------------------------------------------------
# 1セグメントの実行（呼び出し側から注入する）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SegmentRequest:
    """1セグメント分の実行要求（design.md「OverheadBench」Batch Contract）。

    Attributes:
        condition: `CONDITION_OFF` または `CONDITION_ON`。
        measurement_enabled: 計測を有効にするか（要件 7.5）。`condition` と
            必ず対応する——2つを別々に持つのは、実行器が条件名の綴りを
            解釈せずに済むようにするためである。
        cycle: 何巡目か（1 起点）。
        index: 通し位置（0 起点）。**交互実行の並びを実行器側からも
            観測できる**ようにしてある。
    """

    condition: str
    measurement_enabled: bool
    cycle: int
    index: int


@dataclass(frozen=True, slots=True)
class SegmentObservation:
    """1セグメントで得た**生の計測値**（集計値ではない）。

    Attributes:
        predict_ms: 予測区間の生値（1予測につき1件）。
        end_to_end_ms: end-to-end の生値（`END_TO_END_DEFINITION` のとおり、
            **予測が成立した更新のみ**）。
        frames_dropped: このセグメント中に増えた取りこぼし件数。数えられない
            場合は `None`。**0 で埋めない**——「取りこぼしが無かった」と
            「取りこぼしを数えていない」は別である。
    """

    predict_ms: tuple[float, ...]
    end_to_end_ms: tuple[float, ...]
    frames_dropped: int | None


class SegmentRunner(Protocol):
    """1セグメントを実行して生の計測値を返すもの。

    **本モジュールは実行の中身を知らない。** 交互実行の駆動と判定だけを
    持ち、何を1セグメントとするか（1投擲か、記録の1回通しか）は実行器が
    決める。実機の有無もここで吸収される（要件 12.1）。
    """

    def __call__(self, request: SegmentRequest, /) -> SegmentObservation: ...


# ---------------------------------------------------------------------------
# 集計
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConditionStats:
    """1条件 × 1対象区間の集計。

    Attributes:
        condition: 条件名。
        target: 対象区間。
        target_label: 対象区間の表示名（**上流の区間と混同させない**）。
        samples: 生の計測値の件数。
        p50_ms: 中央値。1件も無ければ `None`。
        p95_ms: 95 パーセンタイル。同上。
        iqr_ms: 四分位範囲（p75 − p25）。**2件未満なら `None`**——1件から
            算出した 0.0 は「ばらつきが無い」ではなく「ばらつきを測れて
            いない」であり、判定の基準に据えると差が必ず有意になる。
    """

    condition: str
    target: str
    target_label: str
    samples: int
    p50_ms: float | None
    p95_ms: float | None
    iqr_ms: float | None


def _percentile(sorted_values: Sequence[float], fraction: float) -> float:
    """昇順ソート済み数値列から線形補間で分位点を求める。

    規約 `k = (n - 1) q` は上流2実装（`sensing_foundation.bench.
    logging_overhead._percentile` / `flying_object_tracking.bench.overhead.
    _percentile`）および `metrics/latency.py` と同一である。**同じ問いに違う
    分位点規約を使わない。**
    """
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    k = (n - 1) * fraction
    floor = math.floor(k)
    ceil = math.ceil(k)
    if floor == ceil:
        return sorted_values[int(k)]
    return sorted_values[floor] * (ceil - k) + sorted_values[ceil] * (k - floor)


def build_condition_stats(
    *, condition: str, target: str, values: Sequence[float]
) -> ConditionStats:
    """生の計測値から1条件 × 1区間の集計を作る。

    **公開しているのは、書き出した生値から判定を後から再計算できるように
    するためである**（タスク 6.4 の箇条）。集計値だけを保存すると、規則を
    見直したときに測り直す以外の手が無くなる。
    """
    ordered = sorted(values)
    count = len(ordered)
    if count == 0:
        return ConditionStats(
            condition=condition,
            target=target,
            target_label=TARGET_LABELS[target],
            samples=0,
            p50_ms=None,
            p95_ms=None,
            iqr_ms=None,
        )
    return ConditionStats(
        condition=condition,
        target=target,
        target_label=TARGET_LABELS[target],
        samples=count,
        p50_ms=_percentile(ordered, 0.5),
        p95_ms=_percentile(ordered, 0.95),
        iqr_ms=(
            None
            if count < 2
            else _percentile(ordered, 0.75) - _percentile(ordered, 0.25)
        ),
    )


# ---------------------------------------------------------------------------
# 判定（純粋関数。実行を経由せずに規則だけを検証できる）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OverheadVerdict:
    """1対象区間についての判定（要件 7.7, 7.8）。

    Attributes:
        target: 対象区間。
        target_label: 対象区間の表示名。
        passed: 「有意に変化しない」と判定されたら `True`。
            **2項（中央値の差・取りこぼし）が両方とも成立したときだけ真**で
            あり、どちらかが欠測でも真にならない。
        median_delta_ms: ON/OFF の中央値の**差の絶対値**。欠測なら `None`。
        baseline_iqr_ms: **OFF 条件**の四分位範囲（判定の基準）。同上。
        within_iqr: 中央値の差が基準以内か。評価できなければ `None`。
        dropped_not_increased: 取りこぼしが増えていないか。同上。
        on_frames_dropped: 有効条件の取りこぼし件数。
        off_frames_dropped: 無効条件の取りこぼし件数。
        detail: 判定の根拠。偽の場合は理由（相互排他）を含む。
        unconditional_validity_note: **判定が偽のときだけ**入る注記
            （要件 7.8）。真なら `None`。
    """

    target: str
    target_label: str
    passed: bool
    median_delta_ms: float | None
    baseline_iqr_ms: float | None
    within_iqr: bool | None
    dropped_not_increased: bool | None
    on_frames_dropped: int | None
    off_frames_dropped: int | None
    detail: str
    unconditional_validity_note: str | None


def compute_verdict(
    *,
    on: ConditionStats,
    off: ConditionStats,
    on_frames_dropped: int | None,
    off_frames_dropped: int | None,
) -> OverheadVerdict:
    """1対象区間について ON/OFF 1組から判定を作る（**上流と同一の判定式**）。

    Args:
        on: 計測有効側の集計。
        off: 計測無効側（**ベースライン**）の集計。四分位範囲はこちらから採る。
        on_frames_dropped: 有効条件の取りこぼし件数。数えていなければ `None`。
        off_frames_dropped: 無効条件の取りこぼし件数。同上。

    Returns:
        `passed` は
        `abs(on.p50 - off.p50) <= off.iqr` **かつ**
        `on_frames_dropped <= off_frames_dropped` のとき `True`
        （境界値ちょうどは合格。モジュール docstring「判定基準は上流 Spec と
        同一の形にする」参照）。判定に必要な値が欠けている場合は `False` で
        あり、`within_iqr` / `dropped_not_increased` に `None` が入って
        「差があった」と「確かめられなかった」を区別できるようにしてある。
    """
    label = on.target_label
    if on.p50_ms is None or off.p50_ms is None or off.iqr_ms is None:
        median_delta_ms: float | None = None
        within_iqr: bool | None = None
    else:
        median_delta_ms = abs(on.p50_ms - off.p50_ms)
        within_iqr = median_delta_ms <= off.iqr_ms

    if on_frames_dropped is None or off_frames_dropped is None:
        dropped_not_increased: bool | None = None
    else:
        dropped_not_increased = on_frames_dropped <= off_frames_dropped

    passed = within_iqr is True and dropped_not_increased is True

    if passed:
        detail = _DETAIL_PASSED.format(
            label=label,
            on=CONDITION_ON,
            off=CONDITION_OFF,
            delta=median_delta_ms,
            iqr=off.iqr_ms,
            on_dropped=on_frames_dropped,
            off_dropped=off_frames_dropped,
        )
        note: str | None = None
    else:
        reasons: list[str] = []
        if within_iqr is None:
            reasons.append(_REASON_MEDIAN_MISSING.format(off=CONDITION_OFF))
        elif not within_iqr:
            reasons.append(
                _REASON_MEDIAN_EXCEEDED.format(
                    on=CONDITION_ON,
                    off=CONDITION_OFF,
                    delta=median_delta_ms,
                    iqr=off.iqr_ms,
                )
            )
        if dropped_not_increased is None:
            reasons.append(_REASON_DROPPED_MISSING)
        elif not dropped_not_increased:
            reasons.append(
                _REASON_DROPPED_INCREASED.format(
                    on=CONDITION_ON,
                    off=CONDITION_OFF,
                    on_dropped=on_frames_dropped,
                    off_dropped=off_frames_dropped,
                )
            )
        note = UNCONDITIONAL_VALIDITY_NOTE
        detail = (
            _DETAIL_FAILED.format(label=label, reasons="／".join(reasons)) + note
        )

    return OverheadVerdict(
        target=on.target,
        target_label=label,
        passed=passed,
        median_delta_ms=median_delta_ms,
        baseline_iqr_ms=off.iqr_ms,
        within_iqr=within_iqr,
        dropped_not_increased=dropped_not_increased,
        on_frames_dropped=on_frames_dropped,
        off_frames_dropped=off_frames_dropped,
        detail=detail,
        unconditional_validity_note=note,
    )


# ---------------------------------------------------------------------------
# 結果
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OverheadReport:
    """`run_overhead_bench()` の戻り値（要件 7.5-7.8）。

    Attributes:
        criterion: 判定基準の説明文（`judgement.criterion` と同一）。
        stats: 条件 × 対象区間の集計（4件）。
        verdicts: 対象区間ごとの判定（2件）。
        raw_samples: 条件 → 対象区間 → **生の計測値**（収集順）。
            集計値ではないので、判定を後から再計算できる。
        segment_order: 実際に実行したセグメントの条件名の並び。
            **交互実行の構造的な証跡**である。
        frames_dropped: 条件ごとの取りこぼし件数の合計。欠測は `None`。
        target_labels: 対象区間の表示名。
        upstream_segment_note: 上流の区間と混同させないための注記。
        end_to_end_definition: end-to-end の定義文。**タスク 4.5 が持つ文を
            運ぶだけ**であり、本モジュールで書き直さない。
        unconditional_validity_note: **どれか1つでも判定が偽のときだけ**入る
            注記（要件 7.8）。すべて真なら `None`。
        judgement: 判断の共通の形。
    """

    criterion: str
    stats: tuple[ConditionStats, ...]
    verdicts: tuple[OverheadVerdict, ...]
    raw_samples: Mapping[str, Mapping[str, tuple[float, ...]]]
    segment_order: tuple[str, ...]
    frames_dropped: Mapping[str, int | None]
    target_labels: Mapping[str, str]
    upstream_segment_note: str
    end_to_end_definition: str
    unconditional_validity_note: str | None
    judgement: Judgement


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def run_overhead_bench(
    *, segments: SegmentRunner, settings: M1Settings
) -> OverheadReport:
    """計測の有無だけを変えて**交互に**実行し、有意差の有無を判定する。

    Args:
        segments: 1セグメントを実行して生の計測値を返すもの。
            **同一入力元・同一設定で呼ばれることを前提とする**——本関数が
            変えるのは `SegmentRequest.measurement_enabled` だけである
            （要件 7.6）。
        settings: 解決済みの設定。`overhead`（巡回数・件数の下限）を読む。

    Returns:
        `OverheadReport`。判定が偽でも例外にしない——**計測が歪んでいたと
        いう事実自体が結果**であり、止めると記録が残らない。

    Notes:
        同一の実行器・同一の設定に対して同一の結果を返す（要件 12.4）。
        乱数を使わず、条件の並びも `CONDITIONS` の順序をそのまま保つ。
    """
    cycles = settings.overhead.cycles
    min_samples = settings.overhead.min_samples

    raw: dict[str, dict[str, list[float]]] = {
        condition: {target: [] for target in TARGETS} for condition in CONDITIONS
    }
    dropped: dict[str, int | None] = dict.fromkeys(CONDITIONS, 0)
    segment_order: list[str] = []

    index = 0
    for cycle in range(1, cycles + 1):
        # **1巡の中で条件を入れ替える。** 条件ごとにまとめて回すと、時間と
        # ともに変化する要因が条件の差として現れる（要件 7.6）。
        for condition in CONDITIONS:
            observation = segments(
                SegmentRequest(
                    condition=condition,
                    measurement_enabled=condition == CONDITION_ON,
                    cycle=cycle,
                    index=index,
                )
            )
            segment_order.append(condition)
            index += 1
            raw[condition][TARGET_PREDICT].extend(observation.predict_ms)
            raw[condition][TARGET_END_TO_END].extend(observation.end_to_end_ms)
            total = dropped[condition]
            dropped[condition] = (
                None
                if total is None or observation.frames_dropped is None
                else total + observation.frames_dropped
            )

    stats_by_key = {
        (condition, target): build_condition_stats(
            condition=condition, target=target, values=raw[condition][target]
        )
        for condition in CONDITIONS
        for target in TARGETS
    }
    verdicts = tuple(
        compute_verdict(
            on=stats_by_key[(CONDITION_ON, target)],
            off=stats_by_key[(CONDITION_OFF, target)],
            on_frames_dropped=dropped[CONDITION_ON],
            off_frames_dropped=dropped[CONDITION_OFF],
        )
        for target in TARGETS
    )

    # **差が認められた事実を欠測に埋もれさせない。** 片方の区間で差が出て
    # いるのに、もう片方が測れていないという理由で「判定不能」に丸めると、
    # 実際に観測された歪みが記録から消える。
    recognised = tuple(
        verdict
        for verdict in verdicts
        if verdict.within_iqr is False or verdict.dropped_not_increased is False
    )
    unresolved = tuple(verdict for verdict in verdicts if not verdict.passed)

    if recognised:
        verdict_value = VERDICT_CHANGED
        rationale = _RATIONALE_CHANGED.format(labels=_labels_of(recognised))
    elif unresolved:
        verdict_value = VERDICT_UNDETERMINED
        rationale = _RATIONALE_UNDETERMINED.format(labels=_labels_of(unresolved))
    else:
        verdict_value = VERDICT_UNCHANGED
        rationale = _RATIONALE_UNCHANGED.format(
            labels="／".join(TARGET_LABELS[target] for target in TARGETS)
        )

    note = None if not unresolved else UNCONDITIONAL_VALIDITY_NOTE
    stats = tuple(
        stats_by_key[(condition, target)]
        for target in TARGETS
        for condition in CONDITIONS
    )

    # **2項はそれぞれ単独で効く。** 件数が足りているのに判定できなかった
    # 場合も、判定が付いたが件数が足りない場合も、どちらも「判断に用いて
    # よい状態ではない」（要件 5.10 と同じ扱い）。
    too_few_samples = any(item.samples < min_samples for item in stats)
    provisional = too_few_samples or verdict_value == VERDICT_UNDETERMINED

    criterion = overhead_criterion(cycles=cycles, min_samples=min_samples)
    return OverheadReport(
        criterion=criterion,
        stats=stats,
        verdicts=verdicts,
        raw_samples={
            condition: {
                target: tuple(raw[condition][target]) for target in TARGETS
            }
            for condition in CONDITIONS
        },
        segment_order=tuple(segment_order),
        frames_dropped=dict(dropped),
        target_labels=dict(TARGET_LABELS),
        upstream_segment_note=UPSTREAM_SEGMENT_NOTE,
        end_to_end_definition=END_TO_END_DEFINITION,
        unconditional_validity_note=note,
        judgement=Judgement(
            question=OVERHEAD_QUESTION,
            criterion=criterion,
            verdict=verdict_value,
            rationale=rationale,
            evidence={
                "cycles": cycles,
                "min_samples": min_samples,
                "conditions": list(CONDITIONS),
                "segment_order": list(segment_order),
                "targets": {
                    verdict.target: {
                        "label": verdict.target_label,
                        "passed": verdict.passed,
                        "median_delta_ms": verdict.median_delta_ms,
                        "baseline_iqr_ms": verdict.baseline_iqr_ms,
                        "measurement_off_p50_ms": stats_by_key[
                            (CONDITION_OFF, verdict.target)
                        ].p50_ms,
                        "measurement_on_p50_ms": stats_by_key[
                            (CONDITION_ON, verdict.target)
                        ].p50_ms,
                        "measurement_off_samples": stats_by_key[
                            (CONDITION_OFF, verdict.target)
                        ].samples,
                        "measurement_on_samples": stats_by_key[
                            (CONDITION_ON, verdict.target)
                        ].samples,
                    }
                    for verdict in verdicts
                },
                "frames_dropped": dict(dropped),
                "target_labels": dict(TARGET_LABELS),
                "upstream_segment_note": UPSTREAM_SEGMENT_NOTE,
                "end_to_end_definition": END_TO_END_DEFINITION,
                "unconditional_validity_note": note,
                "provisional": provisional,
            },
            provisional=provisional,
        ),
    )


def _labels_of(verdicts: Sequence[OverheadVerdict]) -> str:
    return "／".join(verdict.target_label for verdict in verdicts)


# ---------------------------------------------------------------------------
# 書き出し（上流 `write_overhead_result()` と同じ流儀）
# ---------------------------------------------------------------------------


def report_to_dict(report: OverheadReport) -> dict[str, object]:
    """`OverheadReport` を JSON 化できる形へ写す。

    **生の計測値をそのまま入れる**（要件 7.8 の前提）。集計値だけを書き出すと、
    判定規則を見直したときに測り直す以外の手が無くなる。
    """
    return {
        "criterion": report.criterion,
        "stats": [
            {
                "condition": item.condition,
                "target": item.target,
                "target_label": item.target_label,
                "samples": item.samples,
                "p50_ms": item.p50_ms,
                "p95_ms": item.p95_ms,
                "iqr_ms": item.iqr_ms,
            }
            for item in report.stats
        ],
        "verdicts": [
            {
                "target": item.target,
                "target_label": item.target_label,
                "passed": item.passed,
                "median_delta_ms": item.median_delta_ms,
                "baseline_iqr_ms": item.baseline_iqr_ms,
                "within_iqr": item.within_iqr,
                "dropped_not_increased": item.dropped_not_increased,
                "on_frames_dropped": item.on_frames_dropped,
                "off_frames_dropped": item.off_frames_dropped,
                "detail": item.detail,
                "unconditional_validity_note": item.unconditional_validity_note,
            }
            for item in report.verdicts
        ],
        "raw_samples": {
            condition: {target: list(values) for target, values in targets.items()}
            for condition, targets in report.raw_samples.items()
        },
        "segment_order": list(report.segment_order),
        "frames_dropped": dict(report.frames_dropped),
        "target_labels": dict(report.target_labels),
        "upstream_segment_note": report.upstream_segment_note,
        "end_to_end_definition": report.end_to_end_definition,
        "unconditional_validity_note": report.unconditional_validity_note,
        "judgement": {
            "question": report.judgement.question,
            "criterion": report.judgement.criterion,
            "verdict": report.judgement.verdict,
            "rationale": report.judgement.rationale,
            "evidence": dict(report.judgement.evidence),
            "provisional": report.judgement.provisional,
        },
    }


def overhead_output_path(output_root: Path, session_id: str) -> Path:
    """書き出し先のパス（design.md「OverheadBench」Output）。"""
    return Path(output_root) / f"overhead-{session_id}.json"


def write_overhead_report(
    report: OverheadReport, output_root: Path, session_id: str
) -> Path:
    """比較結果を1つの JSON として書き出す。

    **判定基準の説明文・判定結果・生の計測値が同じファイルに入る**——3つが
    別々のファイルへ分かれると、数値だけが残って根拠が消える状態になる。
    """
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    path = overhead_output_path(root, session_id)
    path.write_text(
        json.dumps(
            report_to_dict(report),
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# 1投擲を1セグメントとして回す実行器
# ---------------------------------------------------------------------------


def _perf_counter_ms() -> float:
    return time.perf_counter() * 1000.0


class _MeasurementGate:
    """計測の有無を切り替える窓口の被せもの（要件 7.5）。

    `UpstreamGateway` と同じ入口を持ち、`emit()` だけを条件で切り替える。
    **計測を無効にした条件では送出しない**——本 Spec が `predict` 段へ足した
    計測はこの送出であり、これを止めることが「計測を無効にする」ことである。

    **観測そのものは両条件で同じように行う。** セグメントの外側から
    `monotonic_ms` で更新の境界を刻み、`predicted_at_ms` / `sample_t_ms` を
    読む。上流 `flying_object_tracking.bench.overhead` が採ったのと同じ解決で
    ある——ログ経由の値だけを比べると、**無効条件には比較対象そのものが
    存在せず比較が成立しない**し、送出そのものの費用にも構造的に盲目になる。

    ⚠️ **本 Spec が無効にできるのは送出だけである。** `runner.py` は計測の
    有無によらず `session_clock_ms()` を呼んで `predicted_at_ms` を作る。
    計測値の生成そのものを止めるには `runner.py` 側に切り替えが要る
    （タスク 6.4 の境界外。tasks.md「Implementation Notes」へ申し送る）。
    """

    __slots__ = (
        "_enabled",
        "_end_to_end_ms",
        "_inner",
        "_last_ms",
        "_monotonic_ms",
        "_predict_ms",
    )

    def __init__(
        self,
        inner: object,
        *,
        enabled: bool,
        monotonic_ms: Callable[[], float],
    ) -> None:
        self._inner = inner
        self._enabled = enabled
        self._monotonic_ms = monotonic_ms
        self._last_ms = monotonic_ms()
        self._predict_ms: list[float] = []
        self._end_to_end_ms: list[float] = []

    # -- 素通しする入口 -------------------------------------------------
    @property
    def source_kind(self) -> str:
        return self._inner.source_kind  # type: ignore[attr-defined]

    def get_logger_handle(self) -> object:
        return self._inner.get_logger_handle()  # type: ignore[attr-defined]

    def open_frames(self, **kwargs: object) -> object:
        return self._inner.open_frames(**kwargs)  # type: ignore[attr-defined]

    def session_clock_ms(self) -> float:
        return self._inner.session_clock_ms()  # type: ignore[attr-defined]

    # -- 切り替える入口 -------------------------------------------------
    def emit(self, stage: str, event: str, data: Mapping[str, object]) -> None:
        now = self._monotonic_ms()
        self._predict_ms.append(now - self._last_ms)
        self._last_ms = now
        if (stage, event) == PREDICT_UPDATE_EVENT and data.get("valid"):
            predicted_at_ms = data.get("predicted_at_ms")
            sample_t_ms = data.get("sample_t_ms")
            if isinstance(predicted_at_ms, int | float) and isinstance(
                sample_t_ms, int | float
            ):
                self._end_to_end_ms.append(float(predicted_at_ms) - float(sample_t_ms))
        if self._enabled:
            self._inner.emit(stage, event, data)  # type: ignore[attr-defined]

    # -- 取り出し -------------------------------------------------------
    def predict_ms(self) -> tuple[float, ...]:
        return tuple(self._predict_ms)

    def end_to_end_ms(self) -> tuple[float, ...]:
        return tuple(self._end_to_end_ms)


class ThrowSegmentRunner:
    """1投擲を1セグメントとして回す `SegmentRunner` の実体。

    **実機を要さない**（要件 12.1）。入力元は `UpstreamGateway` が開いたもの
    であれば記録再生でも合成でもよく、実機での再実行はタスク 9.x が行う。

    Preconditions:
        `gateway` は `UpstreamGateway` と同じ入口
        （`source_kind` / `get_logger_handle` / `open_frames` /
        `session_clock_ms` / `emit`）を持つこと。
    Postconditions:
        両条件で**同じ入力元・同じ設定**を使う。条件によって変わるのは
        計測の送出だけである（要件 7.6）。
    """

    __slots__ = (
        "_allow_unverified",
        "_calibration_path",
        "_dropped_probe",
        "_gateway",
        "_intrinsics",
        "_monotonic_ms",
        "_record_id_prefix",
        "_settings",
        "_signature",
        "_supplier",
        "_tracking_settings",
    )

    def __init__(
        self,
        *,
        settings: M1Settings,
        gateway: object,
        calibration_path: Path,
        tracking_settings: object,
        signature: object,
        intrinsics: object,
        supplier: object = None,
        record_id_prefix: str = "overhead",
        monotonic_ms: Callable[[], float] | None = None,
        dropped_probe: Callable[[], int | None] | None = None,
        allow_unverified: bool = False,
    ) -> None:
        """実行器を組み立てる。

        Args:
            settings: 本 Spec の設定。**両条件で同一のものを使う。**
            gateway: 上流基盤への窓口（不透明値として素通しする）。
            calibration_path: キャリブレーション結果ファイル。
            tracking_settings: 上流の追跡設定（不透明値）。
            signature: 入力元のストリーム識別情報（不透明値）。
            intrinsics: 入力元のカメラ内部パラメータ（不透明値）。
            supplier: 合成入力のときの供給関数。**両条件で同一のものを渡す**
                （「同一入力元」の実体）。
            record_id_prefix: 記録識別子の接頭辞。
            monotonic_ms: 単調時計（ms）。既定は `time.perf_counter`。
                **注入できるのは、判定を実測の揺らぎに依存させずに固定できる
                ようにするためである**（要件 12.4）。
            dropped_probe: 取りこぼし件数の読み取り。セグメントの前後で読んだ
                差分を条件ごとに積む（上流 `LoggingOverheadBench` と同じ手）。
                `None` なら取りこぼしは**欠測**であり、0 では埋めない。
            allow_unverified: 未検証キャリブレーションでの実行を許可するか。
        """
        self._settings = settings
        self._gateway = gateway
        self._calibration_path = Path(calibration_path)
        self._tracking_settings = tracking_settings
        self._signature = signature
        self._intrinsics = intrinsics
        self._supplier = supplier
        self._record_id_prefix = record_id_prefix
        self._monotonic_ms = monotonic_ms or _perf_counter_ms
        self._dropped_probe = dropped_probe
        self._allow_unverified = allow_unverified

    def __call__(self, request: SegmentRequest, /) -> SegmentObservation:
        """1セグメント（＝1投擲）を実行して生の計測値を返す。"""
        gate = _MeasurementGate(
            self._gateway,
            enabled=request.measurement_enabled,
            monotonic_ms=self._monotonic_ms,
        )
        before = None if self._dropped_probe is None else self._dropped_probe()
        run_throw(
            settings=self._settings,
            gateway=gate,  # type: ignore[arg-type]
            calibration_path=self._calibration_path,
            record_id=f"{self._record_id_prefix}-{request.condition}-{request.cycle}",
            tracking_settings=self._tracking_settings,
            signature=self._signature,
            intrinsics=self._intrinsics,
            supplier=self._supplier,
            allow_unverified=self._allow_unverified,
        )
        after = None if self._dropped_probe is None else self._dropped_probe()
        return SegmentObservation(
            predict_ms=gate.predict_ms(),
            end_to_end_ms=gate.end_to_end_ms(),
            frames_dropped=(
                None if before is None or after is None else after - before
            ),
        )
