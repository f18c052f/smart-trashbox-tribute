"""構造化ログの後処理集計を実装する（design.md「L8-L9: 比較・集計・入口 / LogSummarizer」）。

`StructuredLogger`（タスク 2.1）が書き出す NDJSON 形式の構造化ログを、行単位で
ストリーム処理しながら段階（stage）×イベント（event）ごとに件数・代表値・
分位点（p50/p95）・欠測数を集計する（要件 9.4）。集計そのものは
`StructuredLogger` の責務に含めない（要件 8.8: ロガーは記録するだけに留め、
集計・可視化は本モジュールが別プロセス／別環境で行う）。

**取得ループには一切依存しない。** 本モジュールが読むのは、既にディスクへ
書き終わった NDJSON ファイルだけである。`FrameSource` / `CaptureMetrics` の
ライブ配線を必要とせず、RealSense SDK が存在しない環境（CI・開発機）でも
実行できる（要件 9.7, 12.3）。CLI の `summarize` サブコマンド（タスク 8.1）
がこの `summarize_log()` を呼ぶ入口になる想定だが、本モジュール自体は CLI に
依存しない。

**未知の段階名も、本モジュールを改修せずに集計できる。** `obslog.py` の
`RESERVED_STAGES`（`system` / `capture` / `record`）は予約枠に過ぎず、
下流 Spec（`world-frame-calibration` / `flying-object-tracking` /
`m1-prediction-validation`）が `detect` / `track` / `predict` のような自由な
段階名を追加してよい（要件 8.9 の裏返し）。本モジュールは stage / event を
常に「任意の文字列」として扱い、既知の段階名をハードコードした分岐を一切
持たない（`system`/`session_start`/`session_end` の特別扱いも、後述の
「便宜フィールド」の抽出のみであり、通常の (stage, event) 集計からは
除外しない）。

**行単位でストリーム処理する**（design.md Implementation Notes「Risks:
巨大なログの全読み込みを避け、行ごとにストリーム処理する」）。
`Path.open()` が返すファイルオブジェクトを `for line in f:` で反復するのみで、
`f.read()` / `f.readlines()` のようにファイル全体を一度にメモリへ載せる
呼び出しは行わない。ただし正確な分位点の計算には、各 (stage, event, field)
の組について観測された数値をすべて保持する必要があるため、**集計後の
数値配列自体はメモリに残る**（生ログのテキスト全体をメモリへ載せることは
避けるが、集計結果のサイズは載せない）。この点は design.md の Risk 記述
（「巨大ログの全読み込みを避ける」）が指す対象——生の行データ——とは別の
話であることを明記しておく。

**区間ごとに分解した集計結果を出力する**（要件 9.4）。ここでの「区間」とは、
(stage, event) の組ごとに区切られる集計単位を指す —— 例えば `capture`/`frame`
イベントであれば、そのイベントの `data` に含まれる `wait_ms` / `drain_ms` /
`handoff_ms` / `total_ms` という各区間ごとに、独立した件数・分位点・欠測数を
`EventSummary.fields` として保持する。

**Boundary の解決**: design.md の依存方向表は本モジュールに層 0〜6
（`recording/reader.py` を含む）からの import を許可しているが、
`summarize_log()` は生の NDJSON 行を直接パースするだけで足り、
`obslog.StructuredLogger` の内部実装や `recording/reader.SessionReader`
（セッション記録＝フレーム層の再生）を呼び出す必要はない。design.md
「PublicApi」節が列挙する本モジュールの公開シンボルも `summarize_log` /
`LogSummary` の2つのみであり、セッション記録側の集計は本タスクの
スコープではないと判断した（`recording/reader` へのアクセス許可は、将来
本モジュールへ記録集計を足す余地としての「未使用の余地」であり、本タスクの
実装はそれを使わない）。したがって本モジュールは `obslog` / `source` /
`sources.*` / `metrics` / `recording.*` のいずれも import しない —— 依存する
のは stdlib（`json` / `math` / `pathlib` など）のみである。

Public Interface（design.md より）::

    def summarize_log(path: Path, *, stages: Sequence[str] | None = None
                      ) -> "LogSummary": ...   # stage×event ごとの件数・p50・p95・欠測数

**分位点の計算方法**: 線形補間（numpy のデフォルト `'linear'` および
Excel の `PERCENTILE.INC` と同じ方式）を用いる。値を昇順にソートした配列を
``v`` （長さ ``n``）とし、目標分位を ``q``（``p50`` なら 0.5、``p95`` なら
0.95）とすると、``k = (n - 1) * q`` の整数部 ``f`` と切り上げ ``c`` を取り、
``f == c`` ならそのまま ``v[f]``、そうでなければ
``v[f] * (c - k) + v[c] * (k - f)`` で補間する。要素数が1件の場合はその値を
そのまま返す。外部ツールとの厳密な一致は要求しないが、この方式で内部的に
一貫して計算する。

**欠測（missing）の定義**: ある (stage, event) の組について、同じフィールド
名がイベントによって存在したりしなかったりする場合（例: `metrics.py` の
`capture_latency_ms` は `GLOBAL_TIME` ドメインかつ値がある場合のみ `data` に
現れる、条件付きキー）、そのフィールドが `data` に存在しないイベント数を
`missing` として数える。`present`（`data` にキーが存在したイベント数）とは
独立に数え、欠測を 0 や NaN として分位点計算に混ぜることはしない —— 分位点は
実際に数値として存在した値だけから計算する。
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["FieldStats", "EventSummary", "LogSummary", "summarize_log"]


@dataclass(frozen=True, slots=True)
class FieldStats:
    """(stage, event) 内の1フィールドについての集計結果。

    Attributes:
        present: このフィールドがイベントの `data` に存在した件数
            （値が数値かどうかを問わない）。
        missing: 同じ (stage, event) の他のイベントにはこのフィールドが
            存在したが、このフィールド自身は存在しなかった件数
            （`present` の裏返し。`present + missing == event_count`）。
        numeric_count: `present` のうち、値が数値（`bool` を除く
            `int` / `float`、かつ有限値）だったため分位点計算に使えた件数。
            `present` と一致しないのは、値が数値でない場合（稀）のみ。
        p50: 中央値（線形補間）。数値が1件も無ければ `None`。
        p95: 95パーセンタイル（線形補間）。数値が1件も無ければ `None`。
        mean: 算術平均。数値が1件も無ければ `None`。
        minimum: 最小値。数値が1件も無ければ `None`。
        maximum: 最大値。数値が1件も無ければ `None`。
    """

    present: int
    missing: int
    numeric_count: int
    p50: float | None
    p95: float | None
    mean: float | None
    minimum: float | None
    maximum: float | None


@dataclass(frozen=True, slots=True)
class EventSummary:
    """1つの (stage, event) の組についての集計結果（design.md「区間ごとに分解」）。"""

    stage: str
    event: str
    count: int
    fields: Mapping[str, FieldStats]


@dataclass(frozen=True, slots=True)
class LogSummary:
    """`summarize_log()` の戻り値。ログ全体の集計結果。

    Attributes:
        events: (stage, event) をキーとする集計結果のマッピング。未知の
            段階名も、このマッピングのキーとして自然に現れる。
        total_lines: 空行を除いて読み取った有効な NDJSON 行数
            （`stages` フィルタの影響を受けない、ファイル全体の行数）。
        skipped_lines: JSON として解釈できなかった、または
            `stage` / `event` を欠いていたために読み飛ばした行数。
        log_format_version: `session_start` イベントの `data` から拾った
            ログ形式版（`obslog.LOG_FORMAT_VERSION`）。`session_start` が
            無ければ `None`。
        dropped_total: `session_end` イベントの `data.dropped`
            （`LoggerStats.dropped`）の合計。複数セッションが同一ファイルに
            混在することは通常無いが、念のため合算する。`session_end` が
            無ければ 0。
    """

    events: Mapping[tuple[str, str], EventSummary]
    total_lines: int
    skipped_lines: int
    log_format_version: int | None
    dropped_total: int


def _percentile(sorted_values: Sequence[float], fraction: float) -> float:
    """昇順ソート済み数値列から、線形補間で分位点を求める（モジュール docstring参照）。"""
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    k = (n - 1) * fraction
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_values[int(k)]
    return sorted_values[f] * (c - k) + sorted_values[c] * (k - f)


@dataclass
class _FieldAccumulator:
    """1フィールド分の集計状態（ストリーム処理中の可変な中間表現）。"""

    present: int = 0
    numeric_values: list[float] = field(default_factory=list)

    def add(self, value: object) -> None:
        self.present += 1
        if isinstance(value, bool):
            return
        if isinstance(value, (int, float)) and math.isfinite(value):
            self.numeric_values.append(float(value))

    def finalize(self, event_count: int) -> FieldStats:
        missing = event_count - self.present
        values = sorted(self.numeric_values)
        numeric_count = len(values)
        if numeric_count == 0:
            p50 = p95 = mean = minimum = maximum = None
        else:
            p50 = _percentile(values, 0.5)
            p95 = _percentile(values, 0.95)
            mean = sum(values) / numeric_count
            minimum = values[0]
            maximum = values[-1]
        return FieldStats(
            present=self.present,
            missing=missing,
            numeric_count=numeric_count,
            p50=p50,
            p95=p95,
            mean=mean,
            minimum=minimum,
            maximum=maximum,
        )


class _EventAccumulator:
    """1つの (stage, event) 分の集計状態（ストリーム処理中の可変な中間表現）。"""

    __slots__ = ("stage", "event", "count", "fields")

    def __init__(self, stage: str, event: str) -> None:
        self.stage = stage
        self.event = event
        self.count = 0
        self.fields: dict[str, _FieldAccumulator] = {}

    def add(self, data: Mapping[str, object]) -> None:
        self.count += 1
        for key, value in data.items():
            accumulator = self.fields.get(key)
            if accumulator is None:
                accumulator = _FieldAccumulator()
                self.fields[key] = accumulator
            accumulator.add(value)

    def finalize(self) -> EventSummary:
        fields = {name: accumulator.finalize(self.count) for name, accumulator in self.fields.items()}
        return EventSummary(stage=self.stage, event=self.event, count=self.count, fields=fields)


def summarize_log(path: Path, *, stages: Sequence[str] | None = None) -> "LogSummary":
    """構造化ログ（NDJSON）を行単位で読み、stage×event ごとの件数・p50・p95・欠測数を集計する。

    `path` は `StructuredLogger` が書き出した NDJSON ファイル（またはその
    Event Contract と互換の形式のファイル）を指す。ファイルは行単位で
    ストリーム処理し、一度に全体をメモリへ読み込むことはしない
    （モジュール docstring「行単位でストリーム処理する」を参照）。

    Args:
        path: NDJSON ログファイルへのパス。
        stages: 指定した場合、これらの段階名に一致するイベントのみを
            `events` へ集計する（`system`/`session_start`/`session_end`
            由来の `log_format_version` / `dropped_total` 抽出には影響しない）。
            `None`（既定）の場合はファイル中の全段階を対象にする —— 未知の
            段階名を読むためにこの引数を指定する必要はない。

    Returns:
        `LogSummary`。ファイルが空、または有効な行が1つも無い場合は
        `events` が空のマッピングになる（クラッシュしない）。

    Raises:
        FileNotFoundError: `path` が存在しない場合（標準の `open()` の挙動）。
    """
    stage_filter = set(stages) if stages is not None else None
    accumulators: dict[tuple[str, str], _EventAccumulator] = {}
    total_lines = 0
    skipped_lines = 0
    log_format_version: int | None = None
    dropped_total = 0

    with Path(path).open("r", encoding="utf-8") as log_file:
        for raw_line in log_file:
            line = raw_line.strip()
            if not line:
                continue
            total_lines += 1

            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                skipped_lines += 1
                continue

            if not isinstance(payload, dict):
                skipped_lines += 1
                continue

            stage = payload.get("stage")
            event = payload.get("event")
            if not isinstance(stage, str) or not isinstance(event, str):
                skipped_lines += 1
                continue

            raw_data = payload.get("data")
            data: Mapping[str, object] = raw_data if isinstance(raw_data, dict) else {}

            # system/session_start・session_end からの便宜フィールド抽出。
            # `stages` フィルタの影響を受けない（ドキュメントメタデータのため）。
            if stage == "system" and event == "session_start":
                candidate_version = data.get("log_format_version")
                if isinstance(candidate_version, int) and not isinstance(candidate_version, bool):
                    log_format_version = candidate_version
            elif stage == "system" and event == "session_end":
                candidate_dropped = data.get("dropped")
                if isinstance(candidate_dropped, int) and not isinstance(candidate_dropped, bool):
                    dropped_total += candidate_dropped

            if stage_filter is not None and stage not in stage_filter:
                continue

            key = (stage, event)
            accumulator = accumulators.get(key)
            if accumulator is None:
                accumulator = _EventAccumulator(stage, event)
                accumulators[key] = accumulator
            accumulator.add(data)

    events = {key: accumulator.finalize() for key, accumulator in accumulators.items()}
    return LogSummary(
        events=events,
        total_lines=total_lines,
        skipped_lines=skipped_lines,
        log_format_version=log_format_version,
        dropped_total=dropped_total,
    )
