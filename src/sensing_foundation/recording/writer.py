"""セッション記録の書き出しを実装する `SessionRecorder`（design.md「SessionRecorder」、
タスク 4.3、要件 5.1-5.4, 5.7, 5.8）。

`SessionRecorder` は `manifest.json` / `frames.ndjson`（索引） / `depth.bin`
（Depth 生バッファの連結） / `summary.json`（終了時サマリ）の4ファイルを
セッションディレクトリへ書き出す、**受動的な**部品である（design.md「記録が
`source` を import しないのは意図的である」）。取得ループの中身を知らず、
`write(frame)` を呼ばれた分だけ書く。呼び出し元は取得ループ本体
（連続記録）でも `FrameRingBuffer.flush_to()`（トリガ保存）でもよい
（design.md Implementation Notes / Integration）。

**索引行はブロブ書き込みの後に書く（Invariant）**: `frames.ndjson` の行数と
`depth.bin` に実在するフレーム数は常に一致させる。1フレーム分の書き込みは
「(1) `depth.bin` へバイト列を追記して flush する」→「(2) 追記できた
オフセット・長さを使って `frames.ndjson` へ1行追記して flush する」の順で
行い、(1) が失敗すれば (2) は実行しない。(1) が成功した後に (2) が失敗した
場合、`depth.bin` には「どの索引行からも参照されない末尾バイト列」が残り
得るが、これは許容する——**索引が実体を超えて指す**（存在しないフレームを
指す索引行がある）ことだけを避ければ、電源断・クラッシュに対して安全である
（design.md「途中で電源が落ちても索引が実体を超えない」）。オフセットは
`depth.bin` のファイルハンドルの `tell()` を真実の情報源として使う
（内部カウンタで手動追跡すると、書き込みが部分的に失敗した場合にずれる
おそれがあるため）。

**書き込み失敗は例外として呼び出し側へ伝播しない（要件 5.8）**。
`write()` は内部で発生したあらゆる例外を捕捉し、`write_errors` を増やして
ログへ送るのみで、`write()` 自体からは常に `None` が返る。**「連続」失敗が
既定 100 回の上限に達した場合に限り**、`RecordingWriteError` を**内部的に
構築してログへ記録するだけ**にとどめ、以後の `write()` 呼び出しは
（引き続き例外を送出せずに）静かに no-op へ切り替わる（`_stopped` フラグ）。
tasks.md 4.3 の記述（「例外を上へ投げずに取得を継続させる」）は
design.md「Error Handling」の「連続失敗が上限に達したときだけ
`RecordingWriteError` を送出して記録を止め、取得は継続する」という一文と
一見矛盾するように読めるが、両者を両立させる唯一の解釈は「送出（止める
対象）はあくまで記録処理自身に対してであり、`write()` の呼び出し元
（取得ループ）へ例外として伝播させることは意味しない」というものである。
本実装はこの解釈を採用する: `RecordingWriteError` のインスタンスは構築され
`stage="record", event="stopped"` としてログへ送出されるが、**Python の
例外として `raise` されることは一度もない**。

**測定点（要件 5.6 / tasks.md「記録経路にも計測点を置く」）**: `write()` は
`Logger.timed("record", "write", ...)` で全体を包む。これにより記録が
有効な場合の追加負荷（要件 5.6）を、`bench.logging_overhead`（タスク 7.3）
が後から ON/OFF 比較できる。測定対象区間には失敗時のフォールバック処理も
含める（成功時だけ計測すると「失敗が多い記録経路」の負荷を過小評価する）。

**`DeviceInfo` / `RuntimeInfo` の表現**: design.md のインタフェース擬似コード
はこれらを型として参照するが、`types.py` を含むどのタスクの境界にも
これらのデータクラス定義は存在しない。本実装は新規のデータクラス型を
発明せず、`manifest.json` の Data Models スキーマ表（`device`:
`serial`/`firmware`/`usb_type`/`product_line`、`runtime`: OS・カーネル・
Python版・SDK版・ホスト名・`global_time_enabled`）にそのまま対応する
**プレーンな `Mapping[str, object]`** として受け取る
（`device: Mapping[str, object] | None`、`runtime: Mapping[str, object]`）。
中身の検証は行わない（`recording.layout.write_manifest()` と同じ
「往復の正しさのみを保証する」方針を踏襲する）。

**`source` / `capture` / `failure_cap` の追加引数（意図的な拡張）**:
design.md の `SessionRecorder.__init__` 擬似コードには現れないが、
`manifest.json` は `source`（入力元種別文字列）と `capture`（取得設定の
サブセット）を必須キーとして持つ（Data Models「manifest.json」表）。
これらの値をどこからも受け取らずに manifest を組み立てることはできないため、
本実装はキーワード専用引数として追加する: `source: SourceKind`
（必須。manifest の `source` へそのまま `str(source)`（= `SourceKind` の
`str` 値）として書く）、`capture: Mapping[str, object] | None = None`
（既定 `None` は空辞書として書く）、`failure_cap: int = 100`（design.md
「連続失敗が上限（既定 100）」）。この拡張は task 3.2（`SimulatedSource`）が
`clock` / `capture_config` を追加した前例と同種であり、Implementation Notes
の節にこの事実を明記する（`tasks.md` の Implementation Notes 節と同様の
運用）。**下流でこのクラスを呼び出す場合（タスク 4.6 の `open_source` や
CLI）はこの拡張シグネチャを把握して呼び出すこと。**

**`write()` に渡す `CaptureFrame.profile` の Precondition**: design.md は
「`write()` に渡す `CaptureFrame` の `profile` は生成時の `profile` と
一致する」ことを Precondition として書いている（Invariant ではなく
Precondition）。`types.py`（タスク 1.5）の「検証しない」方針
（`CaptureFrame` 自身が `depth.shape`/`dtype` を検証しないのと同じ理由——
Precondition の検証は呼び出し側の責務であり、本モジュールの責務ではない）を
踏襲し、本実装は `frame.profile == self._profile` を実行時に検証しない。

**`close()` 後の `write()`（design.md 未規定、task ブリーフの指示に従い
明示的に選択）**: `close()` 後に `write()` を呼んでも例外を送出せず、
静かに no-op として無視する。`obslog.StructuredLogger.emit()` が
「`close()` 後の `emit()` は no-op」という Invariant を採用しているのと
同じ前例に倣う（一貫性のため）。

**`__exit__` と `close(stats)` の関係（design.md 未規定分の補完）**:
`close(self, stats: CaptureStats) -> RecordingStats` は `CaptureStats` を
要求するが、コンテキストマネージャの `__exit__(self, *exc)` は例外情報
以外の引数を受け取れないため、`with` ブロックを抜ける時点で「本物の」
`CaptureStats`（`CaptureMetrics.counters()` が返すもの）を渡すことが
構造的にできない。本実装は次の方針を採る:
    - 通常の使い方は `with` ブロックの内側で `recorder.close(stats)` を
      **明示的に**呼ぶこと（`stats` は `CaptureMetrics.counters()` などの
      本物の値）。`close()` は冪等であるため、その後 `__exit__` に到達しても
      二重書き込みは起きない。
    - `close()` が一度も呼ばれないまま `with` ブロックを抜けた場合
      （例外発生時など）の安全網として、`__exit__` は自前の内部カウンタ
      （`write()` を通じて観測した `dropped_before`/`gap_before` の累計）から
      合成した `CaptureStats` を使って `close()` を呼ぶ。これにより
      `summary.json` が必ず書かれることを保証しつつ、`close(stats)` 自体の
      型シグネチャは design.md の記述通り「`CaptureStats` を要求する」形を
      崩さない。
"""

from __future__ import annotations

import json
import sys
import time
import zlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from sensing_foundation.errors import RecordingWriteError, SensingConfigError
from sensing_foundation.obslog import Logger, NullLogger
from sensing_foundation.recording import layout
from sensing_foundation.types import CaptureFrame, CaptureStats, SourceKind, StreamProfile

__all__ = ["RecordingStats", "SessionRecorder"]

#: design.md「連続失敗が上限（既定 100）に達したときだけ」の既定値。
DEFAULT_FAILURE_CAP: int = 100

_VALID_COMPRESSIONS = ("none", "zlib")


@dataclass(frozen=True, slots=True)
class RecordingStats:
    """`SessionRecorder.close()` が返す終了時サマリ（design.md「summary.json」）。

    design.md には専用のデータクラス定義が無いため、`summary.json` の
    フィールド一覧（`frames_written` / `frames_dropped` / `frames_missing` /
    `measured_fps` / `duration_ms` / `bytes_written` / `write_errors` /
    `closed_wall_ms`）にそのまま対応する `frozen=True, slots=True` の
    dataclass として本モジュールで定義する（他のタスクが定義した既存の値
    オブジェクト、例えば `CaptureStats`（タスク 1.5）と同じ流儀）。

    Attributes:
        frames_written: `frames.ndjson`/`depth.bin` の両方へ実際に書けた
            フレーム数（索引行数 = ブロブ内フレーム数、Invariant）。
        frames_dropped: 取得側のドレイン破棄総数（`CaptureStats` から転記）。
        frames_missing: 取得側の欠落総数（`CaptureStats` から転記）。
        measured_fps: 取得側の実測フレームレート（`CaptureStats` から転記）。
        duration_ms: 取得を継続した時間（`CaptureStats` から転記）。
        bytes_written: `depth.bin` へ実際に書き込めたバイト総数
            （圧縮後のバイト数。索引の `len` の総和と一致する）。
        write_errors: 記録の書き込み（ブロブ or 索引のいずれか）で失敗した
            `write()` 呼び出しの総数。
        closed_wall_ms: `close()` を呼んだ時点の壁時計（epoch ms）。
    """

    frames_written: int
    frames_dropped: int
    frames_missing: int
    measured_fps: float
    duration_ms: float
    bytes_written: int
    write_errors: int
    closed_wall_ms: float


def _profile_to_manifest(profile: StreamProfile) -> dict[str, object]:
    """`StreamProfile` を manifest の `profile` オブジェクトへ変換する。

    design.md の manifest スキーマ表は `profile` に `pixel_format` を含むが、
    `StreamProfile`（タスク 1.5）自体はこのフィールドを持たない
    （Depth ストリームのみを扱う本 Spec では常に `uint16`/`"z16"` 相当で
    あるため）。`blob.dtype` と重複する情報だが、design.md のキー構成を
    崩さないため固定文字列 `"z16"`（RealSense SDK の Depth ストリーム形式名）
    として書く。
    """
    return {
        "width_px": profile.width_px,
        "height_px": profile.height_px,
        "fps": profile.fps,
        "depth_scale_mm": profile.depth_scale_mm,
        "color_enabled": profile.color_enabled,
        "pixel_format": "z16",
    }


def _intrinsics_to_manifest(profile: StreamProfile) -> dict[str, object] | None:
    intrinsics = profile.intrinsics
    if intrinsics is None:
        return None
    return {
        "fx_px": intrinsics.fx_px,
        "fy_px": intrinsics.fy_px,
        "ppx_px": intrinsics.ppx_px,
        "ppy_px": intrinsics.ppy_px,
        "model": intrinsics.model,
        "coeffs": list(intrinsics.coeffs),
    }


class SessionRecorder:
    """セッション記録の4ファイル（manifest / frames.ndjson / depth.bin /
    summary.json）を書き出す（design.md「SessionRecorder」）。

    Preconditions:
        `write()` に渡す `CaptureFrame.profile` は構築時の `profile` と
        一致すること（検証しない。モジュール docstring 参照）。

    Postconditions:
        `close()` を呼ぶと `summary.json` が書かれ、以後の `write()` は
        no-op になる（構築時と同様、検証はしないが状態フラグで拒否する）。

    Invariants:
        `frames.ndjson` の行数と `depth.bin` に実在するフレーム数は常に
        一致する（索引行はブロブ書き込みの後にのみ書く。モジュール
        docstring 参照）。
    """

    def __init__(
        self,
        root: Path,
        session_id: str,
        profile: StreamProfile,
        device: Mapping[str, object] | None,
        runtime: Mapping[str, object],
        compression: str = "none",
        logger: Logger = NullLogger(),
        *,
        source: SourceKind,
        capture: Mapping[str, object] | None = None,
        failure_cap: int = DEFAULT_FAILURE_CAP,
    ) -> None:
        """セッションディレクトリを新規作成し、manifest を書いてから記録を開始する。

        Args:
            root: セッション記録の出力先ルート（`recording.layout` 参照）。
            session_id: `recording.layout.new_session_id()` などで生成した
                識別子。ログファイル名・`SessionClock.session_id` と
                同じ値を渡すこと（要件 7.7）。
            profile: このセッションで使う取得設定。以後 `write()` に渡す
                すべての `CaptureFrame.profile` はこれと一致すること
                （Precondition。検証しない）。
            device: `manifest.json` の `device` オブジェクト
                （`serial`/`firmware`/`usb_type`/`product_line`）。
                live 以外の入力元では `None`。中身は検証しない。
            runtime: `manifest.json` の `runtime` オブジェクト（OS・カーネル・
                Python 版・SDK 版・ホスト名・`global_time_enabled`）。
                中身は検証しない。
            compression: `"none"`（既定）または `"zlib"`。
            logger: 記録経路の計測点を送る `Logger`。既定は `NullLogger()`
                （測定を一切行わない no-op）。
            source: `manifest.json` の `source`（入力元種別）。モジュール
                docstring「`source` / `capture` / `failure_cap` の追加引数」
                を参照（design.md 未記載のキーワード専用拡張、必須）。
            capture: `manifest.json` の `capture` オブジェクト
                （`queue_capacity`/`drain_enabled`/`acquire_timeout_ms` 相当）。
                省略時は空辞書として書く。
            failure_cap: 連続書き込み失敗の上限（既定 100。design.md
                「連続失敗が上限（既定 100）に達したときだけ」）。

        Raises:
            SensingConfigError: `compression` が `"none"`/`"zlib"` 以外の
                場合（呼び出し方の誤り）。
        """
        if compression not in _VALID_COMPRESSIONS:
            raise SensingConfigError(
                f"compression は {_VALID_COMPRESSIONS} のいずれかである必要がある: "
                f"{compression!r}"
            )

        self._profile = profile
        self._compression = compression
        self._logger = logger
        self._failure_cap = failure_cap

        self._closed = False
        self._stopped = False
        self._consecutive_failures = 0
        self._write_errors = 0
        self._frames_written = 0
        self._bytes_written = 0
        self._shadow_frames_dropped = 0
        self._shadow_frames_missing = 0
        self._last_recording_stats: RecordingStats | None = None

        self._started_wall_ms = time.time() * 1000.0

        self._session_dir = layout.create_session_dir(root, session_id)

        manifest: dict[str, object] = {
            "format_version": layout.RECORDING_FORMAT_VERSION,
            "session_id": session_id,
            "source": str(source),
            "started_wall_ms": self._started_wall_ms,
            "profile": _profile_to_manifest(profile),
            "intrinsics": _intrinsics_to_manifest(profile),
            "device": dict(device) if device is not None else None,
            "runtime": dict(runtime),
            "capture": dict(capture) if capture is not None else {},
            "blob": {
                "file": layout.BLOB_NAME,
                "dtype": "uint16",
                "little_endian": sys.byteorder == "little",
                "frame_bytes": profile.width_px * profile.height_px * 2,
                "compression": compression,
            },
        }
        layout.write_manifest(self._session_dir, manifest)

        self._blob_path = self._session_dir / layout.BLOB_NAME
        self._index_path = self._session_dir / layout.INDEX_NAME
        self._blob_file = self._blob_path.open("wb")
        self._index_file = self._index_path.open("w", encoding="utf-8")

    # -- 公開インタフェース ---------------------------------------------------

    def write(self, frame: CaptureFrame) -> None:
        """1フレームを記録する。例外を一切送出しない（要件 5.8）。

        `close()` 後、または連続失敗が上限へ達し記録が内部的に停止した後は
        no-op（測定点も送らない——記録経路そのものが動いていないため）。
        """
        if self._closed or self._stopped:
            return

        self._shadow_frames_dropped += frame.dropped_before
        self._shadow_frames_missing += frame.gap_before

        with self._logger.timed(
            "record", "write", index=frame.index, seq=frame.seq
        ):
            try:
                self._write_frame(frame)
            except Exception as exc:  # noqa: BLE001 - 意図的に全例外を捕捉する
                self._on_write_failure(exc)
                return
            self._consecutive_failures = 0

    def close(self, stats: CaptureStats) -> RecordingStats:
        """記録を終了し、`summary.json` を書く（冪等。design.md Postconditions）。

        2回目以降の呼び出しは新たな書き込みを行わず、初回の結果
        （`RecordingStats`）をそのまま返す。
        """
        if self._closed:
            assert self._last_recording_stats is not None
            return self._last_recording_stats

        self._closed = True
        closed_wall_ms = time.time() * 1000.0

        self._blob_file.flush()
        self._blob_file.close()
        self._index_file.flush()
        self._index_file.close()

        recording_stats = RecordingStats(
            frames_written=self._frames_written,
            frames_dropped=stats.frames_dropped,
            frames_missing=stats.frames_missing,
            measured_fps=stats.measured_fps,
            duration_ms=stats.duration_ms,
            bytes_written=self._bytes_written,
            write_errors=self._write_errors,
            closed_wall_ms=closed_wall_ms,
        )

        summary_path = self._session_dir / layout.SUMMARY_NAME
        summary = {
            "frames_written": recording_stats.frames_written,
            "frames_dropped": recording_stats.frames_dropped,
            "frames_missing": recording_stats.frames_missing,
            "measured_fps": recording_stats.measured_fps,
            "duration_ms": recording_stats.duration_ms,
            "bytes_written": recording_stats.bytes_written,
            "write_errors": recording_stats.write_errors,
            "closed_wall_ms": recording_stats.closed_wall_ms,
        }
        with summary_path.open("w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, allow_nan=False)

        self._last_recording_stats = recording_stats
        return recording_stats

    def __enter__(self) -> "SessionRecorder":
        return self

    def __exit__(self, *exc: object) -> None:
        """`close()` が未呼び出しのまま `with` を抜けた場合の安全網。

        モジュール docstring「`__exit__` と `close(stats)` の関係」を参照。
        既に `close()` 済みであれば何もしない（冪等）。
        """
        if not self._closed:
            self.close(self._synthesize_stats())
        return None

    # -- 内部実装 ---------------------------------------------------------

    def _synthesize_stats(self) -> CaptureStats:
        """`__exit__` の安全網が使う、内部カウンタのみから作る `CaptureStats`。

        本物の取得統計（`CaptureMetrics.counters()`）を持たない状況
        （`close(stats)` を明示的に呼ばずに `with` を抜けた場合）向けの
        フォールバックであり、`write()` が観測した `dropped_before`/
        `gap_before` の累計から合成する。
        """
        now_ms = time.time() * 1000.0
        duration_ms = now_ms - self._started_wall_ms
        measured_fps = (
            self._frames_written / (duration_ms / 1000.0) if duration_ms > 0.0 else 0.0
        )
        return CaptureStats(
            frames_yielded=self._frames_written,
            frames_dropped=self._shadow_frames_dropped,
            frames_missing=self._shadow_frames_missing,
            duration_ms=duration_ms,
            measured_fps=measured_fps,
            acquire_errors=0,
        )

    def _write_frame(self, frame: CaptureFrame) -> None:
        """ブロブへ追記してから索引行を追記する（この順序が Invariant）。"""
        raw_bytes = frame.depth.tobytes()
        raw_len = len(raw_bytes)
        if self._compression == "zlib":
            payload = zlib.compress(raw_bytes)
        else:
            payload = raw_bytes

        offset = self._append_blob_chunk(payload)
        length = len(payload)
        self._bytes_written += length

        index_line = self._build_index_line(frame, offset, length, raw_len)
        self._append_index_line(index_line)

        self._frames_written += 1

    def _build_index_line(
        self, frame: CaptureFrame, offset: int, length: int, raw_len: int
    ) -> str:
        record = {
            "i": frame.index,
            "seq": frame.seq,
            "t_capture_ms": frame.t_capture_ms,
            "device_ts_ms": frame.device_timestamp_ms,
            "ts_domain": str(frame.timestamp_domain),
            "capture_latency_ms": frame.capture_latency_ms,
            "off": offset,
            "len": length,
            "raw_len": raw_len,
            "dropped_before": frame.dropped_before,
            "gap_before": frame.gap_before,
        }
        return json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n"

    def _append_blob_chunk(self, payload: bytes) -> int:
        """`depth.bin` へ追記し、書き込み先頭のオフセットを返す（テスト用の差し替え口）。

        オフセットはファイルハンドルの `tell()`（書き込み前の位置）を
        真実の情報源として使う。テストはこのメソッドを差し替えて `OSError`
        を注入できる（`obslog.StructuredLogger._write_line` と同じ前例）。
        """
        offset = self._blob_file.tell()
        self._blob_file.write(payload)
        self._blob_file.flush()
        return offset

    def _append_index_line(self, line: str) -> None:
        """`frames.ndjson` へ1行追記する（テスト用の差し替え口）。"""
        self._index_file.write(line)
        self._index_file.flush()

    def _on_write_failure(self, exc: Exception) -> None:
        self._write_errors += 1
        self._consecutive_failures += 1
        self._logger.emit("record", "write_error", error=str(exc))

        if self._consecutive_failures >= self._failure_cap and not self._stopped:
            self._stopped = True
            # design.md「連続失敗が上限に達したときだけ RecordingWriteError を
            # 送出して記録を止め、取得は継続する」を、モジュール docstring
            # 「書き込み失敗は例外として呼び出し側へ伝播しない」の解釈に
            # 従って実装する: RecordingWriteError は構築してログへ残すのみで
            # 一度も raise しない。取得ループ（write() の呼び出し元）は
            # 影響を受けない。
            error = RecordingWriteError(
                f"記録の書き込みが連続失敗の上限（{self._failure_cap}）に達したため、"
                "記録処理のみを停止した（取得は継続する）"
            )
            self._logger.emit(
                "record",
                "stopped",
                reason=str(error),
                write_errors=self._write_errors,
            )
