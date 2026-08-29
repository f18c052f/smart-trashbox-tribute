"""段階別レイテンシ・end-to-end・資源使用の集計（`development-environment.md §13.1`）。

design.md「Components and Interfaces / L4-L5: 真値と実測 / LatencyAggregator」、
research.md Decision 7、tasks.md タスク 4.5、要件 5.3, 7.1, 7.2, 7.3, 7.9。

**集計器を二重に持たない**（research.md Decision 7）。段階×イベントの集計は
上流の集計器（`UpstreamGateway.summarize_stages()` → `summarize_log()`）へ
委譲し、本モジュールが足すのは**上流が返せない量だけ**である:

- **end-to-end**（要件 7.2）。1行の中の2つの値の差なので、フィールドごとに
  独立して集計する上流の出力からは作れない
- **投擲単位への束ね直し**（実測項目3。要件 5.3）
- **fps**（イベントの送出時刻の幅。上流の集計は行の `t_ms` を見ない）
- **予測そのものの所要時間**（`Prediction.elapsed_ms`。ログではなく記録にある）

上流が既に返す量（段階ごとの件数・分位点・平均・最小・最大）は**一切計算し
直さない**。同じ数字が2つの計算式から出ると、食い違ったときにどちらが正しい
のか決められなくなる。

**所要時間の意味を解釈しない。** 段階別レイテンシは `stage` / `event` /
`field` の3つ組と出所（`source`）で識別できる形にして並べるだけであり、
「どの段階のどの値が本当のレイテンシか」を本モジュールは決めない。これが
**上流が段階を足しても集計側の改修が要らない**（要件 7.3）ということの中身で
あり、**本 Spec が知らない段階名も捨てない**（`unknown_stages` に名前も残す）。
既知の段階名の一覧（`KNOWN_STAGES`）は**ラベル付けにしか使わない。絞り込みに
使ってはならない**——使った瞬間、上流が段階を足すと黙って消える。

**集計を実機上で常時実行する前提を持たない**（要件 7.9）。入力は書き終えた
ログファイルと記録だけであり、取得中の状態には触れない。取得の最中に集計が
乗ると、計測対象そのものを歪める。

**欠測は `None` であって 0 ではない**（tasks.md「Implementation Notes」
タスク1.3）。資源値が取れない環境（`/proc` の無い環境）・取得を測っていない
ログ・初回予測が成立しなかった投擲は、いずれも欠測として返す。0 で埋めると
「0 だった」と「測っていない」が同じ値になる。

本モジュールは L5 層の評価側であり、`sensing_foundation` /
`flying_object_tracking` / `world_frame_calibration` を**直接 import しない**
（取得基盤への接点は `upstream.py` ただ1つ）。段階名の定数だけを
`m1_validation.upstream` から借り、集計器は呼び出し側から注入してもらう。
数値計算は標準ライブラリだけで書く。
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from m1_validation.errors import M1ConfigError
from m1_validation.upstream import (
    M1_STAGES,
    STAGE_PREDICT,
    UPSTREAM_RESERVED_STAGES,
)
from prediction_core import Prediction, ThrowRecord

# ---------------------------------------------------------------------------
# 読み取る段階とイベント
# ---------------------------------------------------------------------------

#: 上流3 Spec が使う段階名の写し。
#:
#: ⚠️ **上流はいずれもこの名前を公開入口に出していない**ため写しを持つ
#: （`upstream.UPSTREAM_RESERVED_STAGES` と同じ事情）。ただし本モジュールは
#: この一覧を**ラベル付けにしか使わない**ので、上流が段階を増やしても集計は
#: 壊れない——増えた段階は `unknown_stages` に名前が出るだけである。
UPSTREAM_STAGES: frozenset[str] = frozenset({"detect", "track", "calibrate"})

#: 本 Spec が名前を知っている段階（`unknown_stages` の裏返し）。
KNOWN_STAGES: frozenset[str] = (
    UPSTREAM_RESERVED_STAGES | UPSTREAM_STAGES | frozenset(M1_STAGES)
)

#: 取得 fps を数えるイベント。上流の `CaptureMetrics.frame()` が
#: **下流へ渡した1フレームにつきちょうど1回**送出する。
CAPTURE_FRAME_EVENT: tuple[str, str] = ("capture", "frame")

#: 実処理 fps を数えるイベント。追跡まで通った1フレームにつき1回送出される
#: （上流の `TrackingMetrics`）。**取得 fps と一致するなら「取得した分を
#: 取りこぼさず処理できている」ことを意味する**——律速が取得側か処理側かを
#: 読み分けるための対（`development-environment.md §13.1`）。
PROCESS_FRAME_EVENT: tuple[str, str] = ("track", "frame")

#: 資源計測のイベント（上流の `CaptureMetrics.snapshot_resources()`）。
RESOURCE_EVENT: tuple[str, str] = ("capture", "resources")

#: 予測更新のイベント（本 Spec の `runner.py` が送出する）。
PREDICT_UPDATE_EVENT: tuple[str, str] = (STAGE_PREDICT, "update")

#: 資源計測のフィールド名（上流の `ResourceSample`）。
CPU_PERCENT_FIELD: str = "cpu_percent"
RSS_BYTES_FIELD: str = "process_rss_bytes"

#: 取りこぼし・欠落のフィールド名（上流の `CaptureFrame`）。
DROPPED_BEFORE_FIELD: str = "dropped_before"
GAP_BEFORE_FIELD: str = "gap_before"

#: end-to-end の `StageLatency.field`。**ログにこの名前のフィールドは無い**
#: （本モジュールが2つの時刻から算出する）。
END_TO_END_FIELD: str = "end_to_end_ms"

#: 記録から読んだ予測所要時間の `StageLatency.event`。ログのイベントではなく
#: Throw Record が出所であることを名前で分かるようにしてある。
RECORD_PREDICT_EVENT: str = "throw_record"

#: `StageLatency.source`。**同じ量を別の出所から二重に載せない**ための札。
LATENCY_SOURCE_LOG: str = "log"
LATENCY_SOURCE_RECORD: str = "record"
LATENCY_SOURCE_DERIVED: str = "derived"

#: 所要時間ではなく**時刻**を表す命名（この接尾辞のフィールドは段階別
#: レイテンシとして読まない）。`sample_t_ms` / `predicted_at_ms` /
#: `based_on_time_ms` を所要時間として並べると、`predict` 段の「レイテンシ」が
#: 観測時刻そのもの（数千 ms）になって表が壊れる。
TIMESTAMP_SUFFIXES: tuple[str, ...] = ("_at_ms", "_time_ms", "_t_ms")

# ---------------------------------------------------------------------------
# 出力へ載せる説明文（**結果と同じ場所に置く**）
# ---------------------------------------------------------------------------

#: end-to-end の定義文（要件 7.2）。**定義を結果に明示する**ための文であり、
#: `LatencyResult.definition` として出力に含まれる。
END_TO_END_DEFINITION: str = (
    "end-to-end レイテンシの定義（要件 7.2）: "
    "**ある観測の取得時刻から、その観測を含めた予測が得られるまでの"
    "経過時間**である。"
    "取得時刻は `predict`/`update` イベントの `sample_t_ms`"
    "（上流の `CaptureFrame.t_capture_ms` をそのまま引き継いだ観測時刻）、"
    "予測が得られた時刻は同じイベントの `predicted_at_ms` であり、"
    "1件ごとに `predicted_at_ms - sample_t_ms` として算出する"
    "（どちらもセッション単調時計の値なので差が取れる）。"
    "予測が成立しなかった更新（`valid` が偽）は「予測が得られた」に"
    "当たらないので算入しない。"
    "**この値は段階別レイテンシの合計とは一致しない。** "
    "段階の所要時間には現れない待ち時間・キューイング・スケジューリングを"
    "含むためであり、一致しないことは異常ではない——"
    "差を埋めようとして段階の値と足し引きしないこと。"
)

#: 実測項目3 の基準を明示する文（要件 5.3）。**単発予測ではなく初回予測**を
#: 基準としている旨を、値と同じ場所に置く。
FIRST_PREDICTION_BASIS: str = (
    "実測項目3（検出開始〜予測確定）の基準（要件 5.3、prediction-core D-1）: "
    "**本 Spec は単発予測ではなく初回予測を基準としている。** "
    "`prediction_core` は最小サンプル数に達した時点で初回予測を出し、"
    "以降サンプルが増えるたびに更新する逐次予測であり、"
    "docs/requirements.md §3 の時間予算表が前提にしている"
    "「予測が1回確定して終わり」という単発予測モデルとは別物である。"
    "したがって区間2 は「検出開始（最初の有効サンプルの観測時刻）から"
    "初回予測が得られた時刻まで」と読み替えて算出してある。"
    "**単発予測の値として読まないこと。**"
)

#: 段階別レイテンシの読み取り方（要件 7.3）。
STAGE_READING_NOTE: str = (
    "段階別レイテンシの読み取り方（要件 7.3）: "
    "段階×イベントの集計は上流の集計器へ委譲しており、本モジュールは"
    "**所要時間の意味を解釈しない**——`stage` / `event` / `field` の3つ組と"
    "出所（`source`）で識別できる形にして残すだけである。したがって"
    "**上流が段階を足しても本モジュールの改修は要らず、本 Spec が知らない"
    "段階名も捨てずに残す**（知らない段階は `unknown_stages` にも名前が出る）。"
    "所要時間とみなすのは名前が `_ms` で終わるフィールドのうち、時刻を表す"
    "命名（`_at_ms` / `_time_ms` / `_t_ms`）でないものである。"
    "`source` が `record` の行はログではなく Throw Record が出所であり、"
    "同じ量を2つの出所から二重に載せないための札である。"
)


# ---------------------------------------------------------------------------
# 上流の集計出力の形（構造だけを借りる。上流の型は import しない）
# ---------------------------------------------------------------------------


class FieldStatsLike(Protocol):
    """上流 `FieldStats` の、本モジュールが読む部分だけの形。"""

    numeric_count: int
    p50: float | None
    p95: float | None
    mean: float | None
    minimum: float | None
    maximum: float | None


class EventSummaryLike(Protocol):
    """上流 `EventSummary` の、本モジュールが読む部分だけの形。"""

    fields: Mapping[str, FieldStatsLike]


class LogSummaryLike(Protocol):
    """上流 `LogSummary` の、本モジュールが読む部分だけの形。"""

    events: Mapping[tuple[str, str], EventSummaryLike]
    skipped_lines: int
    dropped_total: int


#: 集計器の注入口。実運用では `UpstreamGateway.summarize_stages` を渡す。
#:
#: **なぜ注入なのか。** design.md の擬似コードは
#: `aggregate_latency(log_path, records)` だが、集計器は
#: `UpstreamGateway` のメソッドであり、窓口を開くにはセッション時計と
#: ログ器（＝書き込み先のログファイル）が要る。**集計のためだけに新しい
#: セッションを開くのは要件 7.9（集計は実機上の常時実行を前提としない）に
#: 逆行する**ので、集計器そのものを受け取る形にした。本モジュールが
#: `sensing_foundation` を直接 import しない（接点は `upstream.py` だけ）
#: という規則とも噛み合う。
StageSummarizer = Callable[[Path], LogSummaryLike]


# ---------------------------------------------------------------------------
# 結果
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StageLatency:
    """1つの所要時間の分布（design.md「LatencyAggregator」Contracts）。

    Attributes:
        stage: 段階名。上流が記録した段階も、本 Spec が知らない段階も入る。
        event: イベント名。同じ段階でも別のイベントの所要時間は混ぜない。
        field: 所要時間のフィールド名。`capture` のように1つの段階が複数の
            区間（`wait_ms` / `drain_ms` / `handoff_ms` / `total_ms`）を
            記録する場合、**足し合わせずに別々の行として残す**——足すと
            内訳と合計を二重に数える。
        source: 出所（`log` / `record` / `derived`）。
        count: 数値として得られた件数。
        p50_ms: 中央値。数値が1件も無ければ `None`。
        p95_ms: 95 パーセンタイル。同上。
        mean_ms: 平均。同上。
        min_ms: 最小。同上。
        max_ms: 最大。同上。

    **design.md の擬似コードとの差**: 擬似コードは
    `(stage, count, p50_ms, p95_ms, iqr_ms)` である。

    - `event` / `field` / `source` を足した。1つの段階が複数の所要時間を
      記録するため、段階名だけでは**どの区間の数字か決まらない**
      （`capture` の `wait_ms` と `total_ms` が同じ行に潰れる）。
    - `iqr_ms` を落とし、`mean_ms` / `min_ms` / `max_ms` に替えた。
      **上流の集計器は p25 / p75 を返さない**ため IQR は導出できず、
      算出するには生ログを読み直して集計器を二重に持つことになる
      （research.md Decision 7 が禁じ、その Trade-offs に「上流の集計出力の
      形に縛られる。許容できる」と明記されている）。ばらつきは
      `p95_ms - p50_ms` と `min_ms` / `max_ms` から読むこと。
    """

    stage: str
    event: str
    field: str
    source: str
    count: int
    p50_ms: float | None
    p95_ms: float | None
    mean_ms: float | None
    min_ms: float | None
    max_ms: float | None


@dataclass(frozen=True, slots=True)
class FirstPredictionLatency:
    """1投擲ぶんの実測項目3（要件 5.3）。

    Attributes:
        record_id: 投擲の識別子。
        detection_start_ms: 検出開始（**最初の有効サンプルの観測時刻**）。
            サンプルが1件も無ければ `None`。
        first_prediction_at_ms: **初回予測**が得られた時刻。最後まで予測が
            成立しなければ `None`。
        first_prediction_sample_count: 初回予測が基づいたサンプル数。
        detect_to_first_prediction_ms: 上2つの差。どちらかが欠測なら `None`
            ——**0 で埋めない**（「0 ms で予測が出た」と「出なかった」は別）。

    **design.md の擬似コードとの差**: 擬似コードは
    `detect_to_first_prediction_ms: tuple[float, ...]`（投擲ごとの値の並び）
    である。ここでは投擲ごとのレコードにした。平坦な `float` の並びでは
    **欠測を表せず**、詰めると値と投擲の対応が崩れる。
    """

    record_id: str
    detection_start_ms: float | None
    first_prediction_at_ms: float | None
    first_prediction_sample_count: int | None
    detect_to_first_prediction_ms: float | None


@dataclass(frozen=True, slots=True)
class LatencyResult:
    """`aggregate_latency()` の戻り値（要件 7.1, 7.2, 7.3, 5.3）。

    Attributes:
        definition: end-to-end の定義文（要件 7.2）。
        first_prediction_basis: 実測項目3 が初回予測基準である旨（要件 5.3）。
        stage_note: 段階の読み取り方（要件 7.3）。
        stages: 段階別レイテンシ。`(stage, event, field)` の辞書順。
        end_to_end: end-to-end の分布。
        detect_to_first_prediction: 実測項目3。**引数 `records` と同じ順**。
        capture_fps: 取得 fps。測れなければ `None`（欠測）。
        process_fps: 実処理 fps。同上。
        cpu_percent_mean: CPU 使用率の平均。取得できない環境では `None`。
        rss_bytes_max: 常駐メモリの最大。同上。
        frames_dropped: 取りこぼし件数の合計。取得を測っていなければ `None`。
        frames_missing: フレーム欠落件数の合計。同上。
        unknown_stages: 読めたが本 Spec が名前を知らない段階（要件 7.3）。
            **所要時間を持たない段階も名前は残す。**
        foreign_prediction_events: 集計対象の投擲に属さない予測更新の件数。
            混ぜずに除いたうえで**件数は残す**（黙って捨てない）。
        unusable_prediction_events: 必要なフィールドを欠く予測更新の件数。
        log_lines_dropped: ログ器自身が捨てた行数（`session_end` の `dropped`）。
        log_lines_skipped: 読み取れなかった行数。

    後2者は**集計値ではなく集計の入力の健全性**である。ここが 0 でなければ、
    上のどの数字も「取りこぼしたログの上での値」として読む必要がある。

    **design.md の擬似コードとの差**: `frames_dropped` / `frames_missing` を
    `int | None` にした（取得を測っていないログで 0 を返すと「取りこぼしが
    無かった」と読めてしまう）。`definition` 以外の説明文と、混入・欠損の
    件数を足してある。
    """

    definition: str
    first_prediction_basis: str
    stage_note: str
    stages: tuple[StageLatency, ...]
    end_to_end: StageLatency
    detect_to_first_prediction: tuple[FirstPredictionLatency, ...]
    capture_fps: float | None
    process_fps: float | None
    cpu_percent_mean: float | None
    rss_bytes_max: int | None
    frames_dropped: int | None
    frames_missing: int | None
    unknown_stages: tuple[str, ...]
    foreign_prediction_events: int
    unusable_prediction_events: int
    log_lines_dropped: int
    log_lines_skipped: int


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def aggregate_latency(
    log_path: Path,
    records: Sequence[ThrowRecord],
    *,
    summarize: StageSummarizer,
) -> LatencyResult:
    """構造化ログと投擲記録から §13.1 の項目を集計する（要件 7.1）。

    Args:
        log_path: 構造化ログ（NDJSON）のパス。**書き終えたファイルを読むだけ**
            であり、取得中の状態には触れない（要件 7.9）。
        records: 集計対象の投擲。実測項目3 の検出開始と、予測所要時間の出所。
            **失敗投擲を除くのは呼び出し側の仕事**である
            （`runner.successful_throws()`）。
        summarize: 段階×イベントの集計器。実運用では
            `UpstreamGateway.summarize_stages` を渡す（`StageSummarizer` の
            docstring に注入にした理由がある）。

    Returns:
        `LatencyResult`。欠測はすべて `None` であり、0 では埋めない。

    Raises:
        M1ConfigError: `records` に同じ `record_id` が2つ以上ある場合。
            黙って束ねると、別々の投擲の初回予測が1つの行に潰れる。
        FileNotFoundError: `log_path` が無い場合（上流の集計器と同じ挙動）。
    """
    ordered_records = tuple(records)
    record_ids = _unique_record_ids(ordered_records)

    summary = summarize(Path(log_path))
    scan = _scan_log(Path(log_path), record_ids)

    stages = tuple(
        sorted(
            _log_stage_latencies(summary) + _record_prediction_latency(ordered_records),
            key=lambda entry: (entry.stage, entry.event, entry.field),
        )
    )

    return LatencyResult(
        definition=END_TO_END_DEFINITION,
        first_prediction_basis=FIRST_PREDICTION_BASIS,
        stage_note=STAGE_READING_NOTE,
        stages=stages,
        end_to_end=_derived_latency(
            stage=PREDICT_UPDATE_EVENT[0],
            event=PREDICT_UPDATE_EVENT[1],
            field=END_TO_END_FIELD,
            values=scan.end_to_end_ms,
        ),
        detect_to_first_prediction=tuple(
            _first_prediction(record, scan) for record in ordered_records
        ),
        capture_fps=scan.capture.fps(),
        process_fps=scan.process.fps(),
        cpu_percent_mean=_resource_mean(summary, CPU_PERCENT_FIELD),
        rss_bytes_max=_resource_max_int(summary, RSS_BYTES_FIELD),
        frames_dropped=_counter_total(summary, DROPPED_BEFORE_FIELD),
        frames_missing=_counter_total(summary, GAP_BEFORE_FIELD),
        unknown_stages=_unknown_stages(summary),
        foreign_prediction_events=scan.foreign_prediction_events,
        unusable_prediction_events=scan.unusable_prediction_events,
        log_lines_dropped=int(summary.dropped_total),
        log_lines_skipped=int(summary.skipped_lines),
    )


# ---------------------------------------------------------------------------
# 段階別レイテンシ（上流の集計出力の並べ替え）
# ---------------------------------------------------------------------------


def is_duration_field(name: str) -> bool:
    """そのフィールド名を**所要時間**として読んでよいか（要件 7.3）。

    段階ごとの分岐を持たず、**命名だけで決める**。だから上流が段階を足しても
    集計側の改修が要らない。時刻を表す命名（`TIMESTAMP_SUFFIXES`）は除く
    ——時刻を所要時間として並べると表が壊れる。
    """
    if not name.endswith("_ms"):
        return False
    if name == "t_ms":
        return False
    return not name.endswith(TIMESTAMP_SUFFIXES)


def _log_stage_latencies(summary: LogSummaryLike) -> list[StageLatency]:
    """上流の集計結果を段階別レイテンシの行へ写す。**計算し直さない。**

    数値が1件も無いフィールド（値がすべて `None` など）は行を作らない。
    欠測を 0 の分布として並べると、測っていない段階が「常に 0 ms の段階」に
    見える。
    """
    entries: list[StageLatency] = []
    for (stage, event), event_summary in summary.events.items():
        for name, stats in event_summary.fields.items():
            if not is_duration_field(name) or stats.numeric_count <= 0:
                continue
            entries.append(
                StageLatency(
                    stage=stage,
                    event=event,
                    field=name,
                    source=LATENCY_SOURCE_LOG,
                    count=stats.numeric_count,
                    p50_ms=stats.p50,
                    p95_ms=stats.p95,
                    mean_ms=stats.mean,
                    min_ms=stats.minimum,
                    max_ms=stats.maximum,
                )
            )
    return entries


def _record_prediction_latency(
    records: Sequence[ThrowRecord],
) -> list[StageLatency]:
    """予測そのものの所要時間（§13.1「Trajectory prediction latency」）。

    **出所はログではなく記録**である。`runner.py` が送出する
    `predict`/`update` は両端の時刻しか載せておらず、予測1回あたりの処理時間は
    `Prediction.elapsed_ms` にしかない。計測が無効なら `None` であり
    （`prediction-core` 要件 8.3）、その場合は行を作らない（欠測）。
    """
    values = [
        float(outcome.elapsed_ms)
        for record in records
        for outcome in record.predictions
        if isinstance(outcome, Prediction)
        and outcome.elapsed_ms is not None
        and math.isfinite(outcome.elapsed_ms)
    ]
    if not values:
        return []
    return [
        _latency_from_values(
            stage=STAGE_PREDICT,
            event=RECORD_PREDICT_EVENT,
            field="elapsed_ms",
            source=LATENCY_SOURCE_RECORD,
            values=values,
        )
    ]


def _unknown_stages(summary: LogSummaryLike) -> tuple[str, ...]:
    """読めたが本 Spec が名前を知らない段階（要件 7.3）。

    **所要時間を持つかどうかで絞らない。** 段階が現れた事実そのものを残す
    ——名前が消えると、上流が何を記録していたのかが後から分からない。
    """
    seen = {stage for stage, _ in summary.events}
    return tuple(sorted(seen - KNOWN_STAGES))


# ---------------------------------------------------------------------------
# 資源使用と取りこぼし（上流の集計出力から読む）
# ---------------------------------------------------------------------------


def _field_stats(
    summary: LogSummaryLike, key: tuple[str, str], name: str
) -> FieldStatsLike | None:
    """`(stage, event)` の `name` フィールドの集計。無ければ `None`（欠測）。"""
    event_summary = summary.events.get(key)
    if event_summary is None:
        return None
    stats = event_summary.fields.get(name)
    if stats is None or stats.numeric_count <= 0:
        return None
    return stats


def _resource_mean(summary: LogSummaryLike, name: str) -> float | None:
    """資源値の平均。`/proc` が読めない環境では欠測（要件 7.1）。

    上流の `Sysstat` は読めない環境で `None` を送る。`None` は数値として
    数えられないので、ここは自然に欠測になる（**0 で埋めない**）。
    """
    stats = _field_stats(summary, RESOURCE_EVENT, name)
    return None if stats is None else stats.mean


def _resource_max_int(summary: LogSummaryLike, name: str) -> int | None:
    """資源値の最大（バイト数など整数の量）。欠測なら `None`。"""
    stats = _field_stats(summary, RESOURCE_EVENT, name)
    if stats is None or stats.maximum is None:
        return None
    return round(stats.maximum)


def _counter_total(summary: LogSummaryLike, name: str) -> int | None:
    """フレーム単位カウンタの合計。取得を測っていなければ `None`。

    上流の集計器は合計を返さないので、**平均 × 件数**で戻す。上流が持って
    いる値から復元しているだけであり、生ログを読み直してはいない
    （research.md Decision 7）。
    """
    stats = _field_stats(summary, CAPTURE_FRAME_EVENT, name)
    if stats is None or stats.mean is None:
        return None
    return round(stats.mean * stats.numeric_count)


# ---------------------------------------------------------------------------
# ログの走査（上流の集計器が返せない量だけを取る）
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _EventTimes:
    """あるイベントの件数と送出時刻の幅（fps の材料）。"""

    count: int = 0
    first_ms: float | None = None
    last_ms: float | None = None

    def add(self, t_ms: float) -> None:
        self.count += 1
        if self.first_ms is None or t_ms < self.first_ms:
            self.first_ms = t_ms
        if self.last_ms is None or t_ms > self.last_ms:
            self.last_ms = t_ms

    def fps(self) -> float | None:
        """イベント間隔から求めた fps。測れなければ `None`（**0 で埋めない**）。

        `(件数 - 1) ÷ 幅` である（`件数 ÷ 幅` ではない）。最初のイベントの
        前には間隔が無いので、等間隔のとき区間の数は件数より1つ少ない。
        1件しか無い、または幅が 0 のときは**測れていない**ので欠測にする。
        """
        if self.count < 2 or self.first_ms is None or self.last_ms is None:
            return None
        span_s = (self.last_ms - self.first_ms) / 1000.0
        if span_s <= 0.0:
            return None
        return (self.count - 1) / span_s


@dataclass(slots=True)
class _Scan:
    """ログ1回の走査で得たもの。**段階×イベントの集計はここでは行わない。**"""

    end_to_end_ms: list[float]
    first_valid: dict[str, tuple[float, int | None]]
    capture: _EventTimes
    process: _EventTimes
    foreign_prediction_events: int
    unusable_prediction_events: int


def _scan_log(log_path: Path, record_ids: frozenset[str]) -> _Scan:
    """ログを1行ずつ読み、行の中でしか作れない量を集める。

    **上流の集計器と重ならない**（research.md Decision 7）。ここで取るのは

    - `predicted_at_ms - sample_t_ms`（1行の中の2つの値の差）
    - 投擲ごとの初回予測の時刻
    - イベントの送出時刻（行の `t_ms`。上流の集計は `data` しか見ない）

    のみであり、段階ごとの件数も分位点もここでは数えない。

    行単位でストリーム処理する（上流の `summarize_log()` と同じ理由。
    巨大なログを丸ごとメモリへ載せない）。
    """
    scan = _Scan(
        end_to_end_ms=[],
        first_valid={},
        capture=_EventTimes(),
        process=_EventTimes(),
        foreign_prediction_events=0,
        unusable_prediction_events=0,
    )
    with Path(log_path).open("r", encoding="utf-8") as log_file:
        for raw_line in log_file:
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            stage = payload.get("stage")
            event = payload.get("event")
            if not isinstance(stage, str) or not isinstance(event, str):
                continue
            raw_data = payload.get("data")
            data: Mapping[str, object] = raw_data if isinstance(raw_data, dict) else {}

            key = (stage, event)
            emitted_at_ms = _finite(payload.get("t_ms"))
            if key == CAPTURE_FRAME_EVENT and emitted_at_ms is not None:
                scan.capture.add(emitted_at_ms)
            elif key == PROCESS_FRAME_EVENT and emitted_at_ms is not None:
                scan.process.add(emitted_at_ms)
            elif key == PREDICT_UPDATE_EVENT:
                _take_prediction_update(scan, data, record_ids)
    return scan


def _take_prediction_update(
    scan: _Scan, data: Mapping[str, object], record_ids: frozenset[str]
) -> None:
    """`predict`/`update` 1件から end-to-end と初回予測を取り込む。

    **集計対象の投擲に属さない更新は混ぜない**（要件 2.5 と同じ理由。別の
    集計単位や失敗投擲の予測が紛れ込むと、end-to-end の分布が動く）。
    ただし件数は残す——黙って捨てると、混入していた事実が消える。
    """
    record_id = data.get("record_id")
    if not isinstance(record_id, str) or record_id not in record_ids:
        scan.foreign_prediction_events += 1
        return

    sample_t_ms = _finite(data.get("sample_t_ms"))
    predicted_at_ms = _finite(data.get("predicted_at_ms"))
    valid = data.get("valid")
    if sample_t_ms is None or predicted_at_ms is None or not isinstance(valid, bool):
        scan.unusable_prediction_events += 1
        return
    if not valid:
        # 無効な更新は「予測が得られた」に当たらない（定義文のとおり）。
        # 欠測ではないので数え直しもしない。
        return

    scan.end_to_end_ms.append(predicted_at_ms - sample_t_ms)

    raw_count = data.get("sample_count")
    is_count = isinstance(raw_count, int) and not isinstance(raw_count, bool)
    sample_count = raw_count if is_count else None
    known = scan.first_valid.get(record_id)
    if known is None or predicted_at_ms < known[0]:
        # **最も早く得られた有効な予測**を初回とする。行の並びに頼らない
        # ——ログ器は複数スレッドから送出され、並びが前後し得る。
        scan.first_valid[record_id] = (predicted_at_ms, sample_count)


def _first_prediction(record: ThrowRecord, scan: _Scan) -> FirstPredictionLatency:
    """1投擲ぶんの実測項目3（要件 5.3）。

    検出開始は**記録の最初の有効サンプルの観測時刻**である（要件 5.2 が
    実測項目2 で使うのと同じ起点）。初回予測が基づいた観測を起点にすると、
    サンプルが溜まるまでの待ち時間——区間2 の中身そのもの——が丸ごと落ちる。

    ⚠️ 検出開始の求め方は `flight.py` の `_first_valid_sample_time_ms()` と
    **同じ規則**である。あちらが非公開名なので写しを持っている。片方だけを
    変えると実測項目2 と項目3 が食い違う（`test_m1_metrics_latency.py` が
    両者の一致を固定している）。
    """
    detection_start_ms = _first_valid_sample_time_ms(record)
    found = scan.first_valid.get(record.record_id)
    first_prediction_at_ms = None if found is None else found[0]
    sample_count = None if found is None else found[1]

    elapsed_ms: float | None = None
    if detection_start_ms is not None and first_prediction_at_ms is not None:
        elapsed_ms = first_prediction_at_ms - detection_start_ms

    return FirstPredictionLatency(
        record_id=record.record_id,
        detection_start_ms=detection_start_ms,
        first_prediction_at_ms=first_prediction_at_ms,
        first_prediction_sample_count=sample_count,
        detect_to_first_prediction_ms=elapsed_ms,
    )


def _first_valid_sample_time_ms(record: ThrowRecord) -> float | None:
    """最初の有効サンプルの観測時刻（ms）。1件も無ければ `None`。"""
    for sample in record.samples:
        if math.isfinite(sample.t_ms):
            return sample.t_ms
    return None


# ---------------------------------------------------------------------------
# 分布（**上流が返さない量にだけ使う**）
# ---------------------------------------------------------------------------


def _derived_latency(
    *, stage: str, event: str, field: str, values: Sequence[float]
) -> StageLatency:
    """本モジュールが算出した値の分布（出所は `derived`）。"""
    return _latency_from_values(
        stage=stage,
        event=event,
        field=field,
        source=LATENCY_SOURCE_DERIVED,
        values=values,
    )


def _latency_from_values(
    *, stage: str, event: str, field: str, source: str, values: Sequence[float]
) -> StageLatency:
    """値の並びから分布を組む。空なら件数 0・統計は欠測（`None`）。"""
    ordered = sorted(values)
    if not ordered:
        return StageLatency(
            stage=stage,
            event=event,
            field=field,
            source=source,
            count=0,
            p50_ms=None,
            p95_ms=None,
            mean_ms=None,
            min_ms=None,
            max_ms=None,
        )
    return StageLatency(
        stage=stage,
        event=event,
        field=field,
        source=source,
        count=len(ordered),
        p50_ms=_percentile(ordered, 0.5),
        p95_ms=_percentile(ordered, 0.95),
        mean_ms=sum(ordered) / len(ordered),
        min_ms=ordered[0],
        max_ms=ordered[-1],
    )


def _percentile(sorted_values: Sequence[float], fraction: float) -> float:
    """昇順の値から線形補間で分位点を求める。

    **上流の集計器と同じ式**（`(n-1)*q` の線形補間）である。同じ表に並ぶ
    数字の作り方を揃えるためであり、上流が集計する量をここで計算し直して
    いるわけではない（この関数を通るのは end-to-end と予測所要時間だけ）。
    """
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    k = (n - 1) * fraction
    low = math.floor(k)
    high = math.ceil(k)
    if low == high:
        return sorted_values[int(k)]
    return sorted_values[low] * (high - k) + sorted_values[high] * (k - low)


# ---------------------------------------------------------------------------
# 小物
# ---------------------------------------------------------------------------


def _unique_record_ids(records: Sequence[ThrowRecord]) -> frozenset[str]:
    """`records` の識別子。重複していれば**設定の誤り**として拒否する。

    同じ識別子の投擲を2つ渡されると、初回予測が1つの行に潰れ、どちらの投擲の
    値なのか決められない。黙って束ねると、その取り違えが集計へ流れる。
    """
    seen: set[str] = set()
    duplicated: list[str] = []
    for record in records:
        if record.record_id in seen:
            duplicated.append(record.record_id)
        seen.add(record.record_id)
    if duplicated:
        raise M1ConfigError(
            "集計対象に同じ識別子の投擲が複数ある: "
            f"{sorted(set(duplicated))}。"
            "識別子ごとに1件でないと、初回予測がどちらの投擲のものか決まらない",
            {"duplicated_record_ids": sorted(set(duplicated))},
        )
    return frozenset(seen)


def _finite(value: object) -> float | None:
    """有限の数値なら `float` に、そうでなければ `None`（欠測）。

    `bool` は数値として扱わない（`valid` が真のとき 1.0 と読まれる事故を
    防ぐ。上流の集計器も同じ扱いをしている）。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None
