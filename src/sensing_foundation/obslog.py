"""構造化ロギング基盤（design.md「StructuredLogger」）。

1行1イベントの NDJSON 追記形式で、固定キー（`t_ms` / `session_id` / `stage` /
`event`）と任意の付随値（`data`）を書き出す（要件 8.1, 8.2）。

**送出は fire-and-forget である。** 呼び出し側スレッド（`emit()` を呼ぶ側）が
行うのは「イベントを構築し、有界キューへ積む」ことだけであり、実際のファイル
I/O（直列化・書き込み・フラッシュ）は本モジュールが起動する専用の writer
スレッドが行う。呼び出し側は書き込みの完了を一切待たない（要件 8.3）。これは
`tech.md` 開発標準5「計測が計測対象を歪めないこと」を成立させるための中核
設計であり、取得ループのフレームレートを一切左右してはならない。

**キューは有界であり、満杯時はログを破棄して取得を優先する**（要件 8.6）。
破棄件数は `LoggerStats.dropped` として `stats()` から取得でき、正常終了時は
`session_end` イベントの `data` にも含まれる。順序は保証されるが（同一プロセス
内では投入順）、破棄によって欠番が生じ得る。各イベントに単調増加する `seq` を
付与するため、ファイル中の `seq` の欠番から破棄の発生箇所を後から推定できる。

**書き込み失敗は本来の処理を中断させない**（要件 8.7）。writer スレッド内の
例外はすべて捕捉し、`LoggerStats.write_errors` を増やして継続する。呼び出し側
スレッドで例外が発生することはない（I/O 自体を行わないため）。

**行単位でフラッシュする**（`LoggingConfig.flush_each_line`、既定 True）。
電源断などで最終行が壊れても、先行する行はそこまで読める状態を保つ
（`close()` を呼ばなくても、書き込み済みの行はファイルから読める）。

**実行時に無効化でき、無効時は `NullLogger` に完全に差し替わる**（要件 8.4,
8.5）。`NullLogger` の `emit()` / `timed()` は引数（`stage` / `event` / `data`
の値）に一切触れない —— 文字列化も属性アクセスも行わない、真の no-op である。
呼び出し側は `if logger.enabled: ...` を挟まずに `logger.emit(..., value=heavy())`
と書けるが、Python の呼び出し規約上、キーワード引数の**式そのもの**は呼び出し
時点で評価される（これは言語仕様上避けられない）。`NullLogger` が保証するのは
「渡された値をこちら側が二次的に触れない（＝ JSON 直列化やイベントオブジェクト
構築を一切行わない）」ことである。

**予約 stage 名**は `RESERVED_STAGES`（`system` / `capture` / `record`）。
下流 Spec（`world-frame-calibration` / `flying-object-tracking` /
`m1-prediction-validation`）は `detect` / `track` / `predict` のような自由な
stage 名を、本モジュールを改修せずに追加してよい（要件 8.9）。この制約は
ドキュメントの区分であり、実行時に stage 名を検証・拒否することはしない。

**非有限値（NaN / Infinity）はその項目だけ欠測として落とす**（要件 8.6 の
直列化破綻回避）。`json.dumps(..., allow_nan=False, ensure_ascii=False)` を
用いる方針は `prediction_core.record` と揃えている。`data` の値が
`dict` / `list` / `tuple` にネストしていても再帰的に走査し、非有限値を含む
キー・要素のみを取り除く（他の項目は生き残る）。

**集計は持たない**（要件 8.8）。集計は `logsummary.py`（タスク 7.1）が別に
行う。

Service Interface（design.md より）::

    class Logger(Protocol):
        enabled: bool
        def emit(self, stage: str, event: str, /, **data: object) -> None: ...
        def stage(self, stage: str) -> "StageLogger": ...
        def timed(self, stage: str, event: str, /, **data: object) -> AbstractContextManager[None]: ...
        def stats(self) -> "LoggerStats": ...
        def close(self, timeout_ms: float = 1000.0) -> None: ...

`StageLogger` / `LoggerStats` は design.md が名前のみを参照し、フィールドまで
規定していない（タスク 2.1 の実装判断に委ねられている）。本モジュールでの
設計は次の通り:

- `StageLogger`: `logger.stage(name)` が返す、特定の stage 名を束縛した薄い
  ラッパ。`emit(event, **data)` / `timed(event, **data)` を、内部で保持する
  `Logger`（`StructuredLogger` でも `NullLogger` でも良い）へそのまま委譲する
  だけであり、独自の状態・キュー・スレッドは持たない。
- `LoggerStats`: `emitted`（`emit()` 呼び出し総数）・`dropped`（キュー満杯に
  よる破棄件数）・`written`（実際にファイルへ書けた行数）・`write_errors`
  （直列化・書き込みで例外になった件数）の4カウンタを持つ、不変のスナップ
  ショット。`session_end` イベントの `data` はこの4カウンタをそのまま含む。

Invariants:
    - `close()` 後の `emit()` は no-op（Invariant, design.md）。
    - `close()` の二重呼び出しは安全（Invariant, design.md）。
    - `emit()` は呼び出し側スレッドで I/O を行わない。返るまでの処理は
      イベント構築とキュー投入のみ（Postconditions, design.md）。
"""

from __future__ import annotations

import itertools
import json
import math
import os
import platform
import queue
import socket
import threading
from collections.abc import Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from sensing_foundation.config import LoggingConfig
from sensing_foundation.timebase import SessionClock

__all__ = [
    "LOG_FORMAT_VERSION",
    "RESERVED_STAGES",
    "Logger",
    "LogEvent",
    "LoggerStats",
    "StageLogger",
    "StructuredLogger",
    "NullLogger",
    "get_logger",
]

#: NDJSON ファイルの形式版。記録形式版・ログ形式版の変更は tasks.md 冒頭の
#: Revalidation Trigger（④）に該当するため、変更時は下流 Spec の再検証を
#: 要求すること。
LOG_FORMAT_VERSION = 1

#: 予約 stage 名（design.md「予約 stage: system / capture / record」）。
#: 下流 Spec はこれ以外の任意の stage 名を自由に追加してよい（要件 8.9）。
#: 実行時の検証には用いない（ドキュメント上の区分）。
RESERVED_STAGES = frozenset({"system", "capture", "record"})

# writer スレッドが closing シグナル後に空のキューをポーリングする間隔。
_POLL_INTERVAL_S = 0.05

# NaN/Infinity 検出時に「この値は落とす」ことを示す内部センチネル。
_DROP = object()


@dataclass(frozen=True, slots=True)
class LogEvent:
    """1件のログイベント（design.md Event Contract の内部表現）。

    `emit()` はこのオブジェクトを構築して有界キューへ積む。`t_ms` /
    `session_id` / `stage` / `event` が固定キー、`seq` / `data` が任意キー
    である（design.md「Event Contract」）。`data` は直列化前の生の値
    （NaN/Infinity を含み得る）を保持し、非有限値の除去は直列化時
    （`_serialize_event`）に行う。
    """

    t_ms: float
    session_id: str
    stage: str
    event: str
    seq: int
    data: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LoggerStats:
    """ロガーの内部カウンタのスナップショット（要件 8.6）。

    Attributes:
        emitted: `emit()` が呼ばれた回数（キュー投入の成否を問わない）。
        dropped: 有界キューが満杯だったために破棄された件数。
        written: writer スレッドが実際にファイルへ書き込めた行数
            （`session_start` / `session_end` を含む）。
        write_errors: 直列化またはファイル書き込みで例外になった件数
            （要件 8.7: この件数が増えても呼び出し側の処理は中断しない）。
    """

    emitted: int
    dropped: int
    written: int
    write_errors: int


class Logger(Protocol):
    """`StructuredLogger` / `NullLogger` が実装する共通プロトコル（design.md）。"""

    enabled: bool

    def emit(self, stage: str, event: str, /, **data: object) -> None: ...

    def stage(self, stage: str) -> "StageLogger": ...

    def timed(
        self, stage: str, event: str, /, **data: object
    ) -> AbstractContextManager[None]: ...

    def stats(self) -> LoggerStats: ...

    def close(self, timeout_ms: float = 1000.0) -> None: ...


class StageLogger:
    """特定の stage 名を束縛した薄いラッパ（`Logger.stage(name)` の戻り値）。

    下流 Spec（`detect` / `track` / `predict` などの自由な stage）が、毎回
    stage 名を書かずに済むようにするための糖衣であり、内部で保持する
    `Logger` へ `emit()` / `timed()` をそのまま委譲するだけである。独自の
    キュー・スレッド・カウンタは持たない（`NullLogger` を包んだ場合は
    そのまま no-op になる）。

    例::

        stage_logger = logger.stage("detect")
        stage_logger.emit("done", latency_ms=12.3)
        with stage_logger.timed("infer"):
            ...
    """

    __slots__ = ("_logger", "_stage")

    def __init__(self, logger: Logger, stage: str) -> None:
        self._logger = logger
        self._stage = stage

    def emit(self, event: str, /, **data: object) -> None:
        self._logger.emit(self._stage, event, **data)

    def timed(self, event: str, /, **data: object) -> AbstractContextManager[None]:
        return self._logger.timed(self._stage, event, **data)


# ----------------------------------------------------------------------------
# NullLogger: 実行時無効化の実装。イベント生成も文字列化も一切行わない。
# ----------------------------------------------------------------------------


class _NullTimedContextManager(AbstractContextManager[None]):
    """`NullLogger.timed()` が返す、単一で共有される no-op コンテキストマネージャ。

    毎回新しいオブジェクトを割り当てるとロギング無効時にもアロケーション
    コストが残る（design.md Implementation Notes「Risks」）ため、モジュール
    読み込み時に1つだけ生成し、以後すべての `NullLogger.timed()` 呼び出しが
    同一インスタンスを返す（`is` で同一性を確認できる）。
    """

    __slots__ = ()

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False


_NULL_TIMED_CM = _NullTimedContextManager()
_NULL_STATS = LoggerStats(emitted=0, dropped=0, written=0, write_errors=0)


class NullLogger:
    """ロギング無効時に使う真の no-op 実装（design.md「NullLogger」）。

    `emit()` / `timed()` は `stage` / `event` / `data` のいずれにも触れない
    （文字列化・属性アクセス・JSON 直列化のいずれも行わない）。呼び出し側は
    値の構築コストを気にせず `logger.emit("x", "y", value=heavy())` と
    書けるが、`heavy()` の呼び出し自体（引数式の評価）は Python の呼び出し
    規約上避けられない。`NullLogger` が保証するのは「渡された後、こちら側が
    その値を一切使わない」ことである。
    """

    __slots__ = ()

    enabled = False

    def emit(self, stage: str, event: str, /, **data: object) -> None:
        return None

    def stage(self, stage: str) -> StageLogger:
        return StageLogger(self, stage)

    def timed(
        self, stage: str, event: str, /, **data: object
    ) -> AbstractContextManager[None]:
        return _NULL_TIMED_CM

    def stats(self) -> LoggerStats:
        return _NULL_STATS

    def close(self, timeout_ms: float = 1000.0) -> None:
        return None


# ----------------------------------------------------------------------------
# 非有限値のサニタイズ（要件 8.6 の直列化破綻回避）
# ----------------------------------------------------------------------------


def _sanitize_value(value: object) -> object:
    """非有限 float を検出したら `_DROP` を返す（呼び出し元がキー/要素を除く）。

    dict は再帰的にサニタイズしてキーを落とし、list/tuple も再帰的に
    サニタイズして非有限要素を除く。それ以外の値はそのまま返す。
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else _DROP
    if isinstance(value, dict):
        sanitized: dict[str, object] = {}
        for key, inner in value.items():
            sanitized_inner = _sanitize_value(inner)
            if sanitized_inner is not _DROP:
                sanitized[key] = sanitized_inner
        return sanitized
    if isinstance(value, (list, tuple)):
        sanitized_list = []
        for inner in value:
            sanitized_inner = _sanitize_value(inner)
            if sanitized_inner is not _DROP:
                sanitized_list.append(sanitized_inner)
        return sanitized_list
    return value


def _sanitize_data(data: Mapping[str, object]) -> dict[str, object]:
    """`data` マッピングの各値をサニタイズし、非有限値を持つキーを除いた dict を返す。"""
    result: dict[str, object] = {}
    for key, value in data.items():
        sanitized = _sanitize_value(value)
        if sanitized is not _DROP:
            result[key] = sanitized
    return result


def _serialize_event(event: LogEvent) -> str:
    """`LogEvent` を1行の NDJSON 文字列（末尾改行つき）へ直列化する。

    キー順は design.md の Event Contract の例に合わせる: `t_ms` /
    `session_id` / `stage` / `event` / `seq` / `data`。`data` はサニタイズ後に
    空であれば省略する（design.md「任意キー」）。

    `json.dumps(..., allow_nan=False, ensure_ascii=False)` を用いる方針は
    `prediction_core.record` と揃えている。事前にサニタイズしているため
    `allow_nan=False` によって `ValueError` になることはない想定だが、万一
    サニタイズが漏れた非有限値が残っていた場合は `ValueError` を送出させ、
    直列化を静かに破綻させない（呼び出し元の writer ループがこれを捕捉し、
    `write_errors` として計数する）。
    """
    payload: dict[str, object] = {
        "t_ms": event.t_ms,
        "session_id": event.session_id,
        "stage": event.stage,
        "event": event.event,
        "seq": event.seq,
    }
    sanitized_data = _sanitize_data(event.data)
    if sanitized_data:
        payload["data"] = sanitized_data
    return json.dumps(payload, allow_nan=False, ensure_ascii=False) + "\n"


# ----------------------------------------------------------------------------
# StructuredLogger: fire-and-forget な NDJSON 送出の実装。
# ----------------------------------------------------------------------------


class StructuredLogger:
    """有界キュー経由で NDJSON へ fire-and-forget に追記するロガー。

    ファイル名は `<config.path>/<clock.session_id>.ndjson`（design.md
    Implementation Notes）。構築時に `session_start` イベントを**同期的に**
    （呼び出し側スレッドで、writer スレッド起動前に）1行書き、以後の
    `emit()` は有界キューへ積むだけで即座に返る。実際の直列化・書き込みは
    専用の daemon スレッド（writer スレッド）が行う。

    `close()` は writer スレッドへ終了を通知し、指定タイムアウト内に
    ドレインが完了すれば `session_end` イベントを書いてファイルを閉じる。
    タイムアウト内にドレインが完了しなかった場合、writer スレッドと競合
    しないよう `session_end` の書き込みとファイルクローズは行わない
    （daemon スレッドなのでプロセス終了は妨げない）。

    スレッド安全性: `self._file`（ファイルオブジェクト）へ書き込むのは
    常に1スレッドのみである —— 構築時は呼び出し側スレッド（`session_start`
    のみ）、以後は writer スレッド、`close()` 完了後は呼び出し側スレッド
    （`session_end` のみ、writer スレッドの `join()` 成功を確認した後）。
    カウンタ（`_emitted` / `_dropped` / `_written` / `_write_errors`）は
    複数スレッドから更新されるため `_counters_lock` で保護する。`seq` の
    採番は `itertools.count()`（CPython で1呼び出しがアトミック）を用い、
    追加のロックを取らない。
    """

    def __init__(self, config: LoggingConfig, clock: SessionClock) -> None:
        self.enabled = True
        self._config = config
        self._clock = clock

        self._queue: "queue.Queue[LogEvent]" = queue.Queue(maxsize=config.queue_capacity)
        self._closing = threading.Event()
        self._closed = False
        self._close_lock = threading.Lock()

        self._counters_lock = threading.Lock()
        self._emitted = 0
        self._dropped = 0
        self._written = 0
        self._write_errors = 0

        self._seq_counter = itertools.count(1)

        log_dir = Path(config.path)
        log_dir.mkdir(parents=True, exist_ok=True)
        self._path = log_dir / f"{clock.session_id}.ndjson"
        self._file = open(self._path, "a", encoding="utf-8")

        # session_start は writer スレッド起動前に、呼び出し側スレッドで
        # 同期的に書く。これによりファイルの先頭行が必ず session_start に
        # なることを、キューの投入順に頼らずに保証する。
        self._write_and_count(self._build_start_event())

        self._writer_thread = threading.Thread(
            target=self._writer_loop,
            name=f"sensing-foundation-obslog-{clock.session_id}",
            daemon=True,
        )
        self._writer_thread.start()

    # -- Logger プロトコル ---------------------------------------------------

    def emit(self, stage: str, event: str, /, **data: object) -> None:
        """イベントをキューへ積むだけで即座に返る（呼び出し側で I/O を行わない）。

        `close()` 後は no-op（Invariant）。キューが満杯であれば破棄し、
        `LoggerStats.dropped` を増やす（要件 8.6）。
        """
        if self._closed:
            return

        seq = next(self._seq_counter)
        log_event = LogEvent(
            t_ms=self._clock.now_ms(),
            session_id=self._clock.session_id,
            stage=stage,
            event=event,
            seq=seq,
            data=data,
        )

        with self._counters_lock:
            self._emitted += 1

        try:
            self._queue.put_nowait(log_event)
        except queue.Full:
            with self._counters_lock:
                self._dropped += 1

    def stage(self, stage: str) -> StageLogger:
        return StageLogger(self, stage)

    @contextmanager
    def timed(self, stage: str, event: str, /, **data: object):
        """区間の所要時間を計測し、退出時（例外時も含む）に1件送出する。

        送出イベントの `data` には `duration_ms`（区間の所要 ms）が
        自動的に付与される。呼び出し側が既に `duration_ms` を渡していた
        場合はその値を優先する（上書きしない）。
        """
        start_ms = self._clock.now_ms()
        try:
            yield
        finally:
            payload = dict(data)
            payload.setdefault("duration_ms", self._clock.now_ms() - start_ms)
            self.emit(stage, event, **payload)

    def stats(self) -> LoggerStats:
        with self._counters_lock:
            return LoggerStats(
                emitted=self._emitted,
                dropped=self._dropped,
                written=self._written,
                write_errors=self._write_errors,
            )

    def close(self, timeout_ms: float = 1000.0) -> None:
        """writer スレッドへドレインを指示し、可能であれば `session_end` を書いて閉じる。

        二重呼び出しは安全（Invariant）。`timeout_ms` 以内に writer スレッドが
        キューを空にして終了しなければ、`self._file` への同時書き込みを
        避けるため `session_end` の書き込みとファイルクローズを行わない
        （daemon スレッドなのでプロセスの終了は妨げない。テストでは十分な
        `timeout_ms` を指定すること）。
        """
        with self._close_lock:
            if self._closed:
                return
            self._closed = True

        self._closing.set()
        self._writer_thread.join(timeout_ms / 1000.0)

        if not self._writer_thread.is_alive():
            self._write_and_count(self._build_end_event())
            try:
                self._file.flush()
            finally:
                self._file.close()

    # -- 内部実装 -------------------------------------------------------------

    def _write_line(self, line: str) -> None:
        """1行を書き込む（テストがこのメソッドを差し替えて遅延/失敗を模擬できる）。"""
        self._file.write(line)
        if self._config.flush_each_line:
            self._file.flush()

    def _write_and_count(self, log_event: LogEvent) -> None:
        """イベントを直列化して書き込み、成否をカウンタへ反映する。

        直列化・書き込みのいずれで例外が起きても捕捉し、`write_errors` を
        増やして戻る（要件 8.7: 本来の処理を中断させない）。
        """
        try:
            line = _serialize_event(log_event)
            self._write_line(line)
        except Exception:
            with self._counters_lock:
                self._write_errors += 1
            return
        with self._counters_lock:
            self._written += 1

    def _writer_loop(self) -> None:
        """専用 writer スレッドの本体。キューから取り出して書き込むだけを繰り返す。"""
        while True:
            try:
                item = self._queue.get(timeout=_POLL_INTERVAL_S)
            except queue.Empty:
                if self._closing.is_set():
                    return
                continue
            self._write_and_count(item)

    def _build_start_event(self) -> LogEvent:
        payload: dict[str, object] = {
            "log_format_version": LOG_FORMAT_VERSION,
            "wall_ms": self._clock.to_wall_ms(0.0),
            "config": {
                "enabled": self._config.enabled,
                "path": str(self._config.path),
                "queue_capacity": self._config.queue_capacity,
                "flush_each_line": self._config.flush_each_line,
            },
            "host": {
                "hostname": socket.gethostname(),
                "pid": os.getpid(),
                "python_version": platform.python_version(),
            },
        }
        return LogEvent(
            t_ms=self._clock.now_ms(),
            session_id=self._clock.session_id,
            stage="system",
            event="session_start",
            seq=next(self._seq_counter),
            data=payload,
        )

    def _build_end_event(self) -> LogEvent:
        with self._counters_lock:
            stats = LoggerStats(
                emitted=self._emitted,
                dropped=self._dropped,
                written=self._written,
                write_errors=self._write_errors,
            )
        payload: dict[str, object] = {
            "emitted": stats.emitted,
            "dropped": stats.dropped,
            "written": stats.written,
            "write_errors": stats.write_errors,
        }
        return LogEvent(
            t_ms=self._clock.now_ms(),
            session_id=self._clock.session_id,
            stage="system",
            event="session_end",
            seq=next(self._seq_counter),
            data=payload,
        )


def get_logger(config: LoggingConfig, clock: SessionClock) -> Logger:
    """`config.enabled` に応じて `StructuredLogger` / `NullLogger` を返す唯一の入口。

    無効時に `NullLogger` を返すことで、以後のイベント生成・文字列化を一切
    行わないようにする（要件 8.4, 8.5）。
    """
    if not config.enabled:
        return NullLogger()
    return StructuredLogger(config, clock)
