"""コマンドライン入口を実装する（design.md「L8-L9: 比較・集計・入口 / CLI」、
タスク 8.1、要件 12.4）。

`doctor` / `capture` / `record` / `replay-session` / `bench-modes` /
`bench-logging` / `summarize` の7サブコマンドを提供する、本 Spec の最上位の
統合点である（依存方向表「9 | cli | 0〜8」——このモジュールはパッケージ内の
どのレイヤも import してよい）。人間が最初に叩くコマンド、およびタスク9の
ブリングアップ手順の入口になる。

============================================================================
`replay-session` という名前（`replay` ではない。design.md「Naming」）
============================================================================

**再生のサブコマンド名は `replay` ではなく `replay-session` である。**
`prediction_core.replay(record)` は Throw Record（サンプル層、検出後の点列
から予測を再実行する）という**別の操作**であり、本 Spec の再生は
セッション記録（フレーム層、`var/sessions/<id>/` 配下の生 Depth フレーム列）
を流し直す操作である。層が異なるものへ同じ語を使うと、計測メモや `docs/`
側の記述がどちらを指すか判別できなくなる（tasks.md 8.1 が明示的に警告する
点）。この区別はトップレベルの `--help`（本モジュールの `description`）と
`replay-session` サブコマンド自身の `description` の両方に明記する。

============================================================================
設定解決順序（要件 12.4）
============================================================================

**CLI 引数 > 環境変数 > 設定ファイル > 既定値**の順で解決する
（`RuntimeSettings.resolve()`、タスク 1.6、をそのまま呼ぶ——本モジュールは
解決順序ロジックを再実装しない）。すべてのサブコマンドが共通の設定フラグ
群（`--source` / `--session` / `--width-px` / ... / `--config`）を持ち、
`--print-settings` を指定すると解決済みの `RuntimeSettings` を JSON で
表示してサブコマンドの本処理を実行せずに終了する。

`--source recorded` なのに `--session` が未指定、といった不整合は
`RuntimeSettings.resolve()` 自身が起動前（`_validate()`）に `SensingConfigError`
として拒否する。本モジュールはこれを独自に再実装せず、`main()` で捕捉して
Python の生トレースバックの代わりに読める1行のエラーメッセージへ変換する
だけである。

`replay-session` サブコマンドは**常に `source=recorded` を強制する**
（`--source` に何を指定していても上書きする）——このサブコマンドを選ぶこと
自体が「セッション記録を再生したい」という意図を表すため。

タスク 1.6 の Implementation Notes が指示する通り、`RuntimeSettings.resolve()`
の `installed_ram_bytes` には `Sysstat.sample().system_total_bytes` を必ず
渡す（さもないとリングバッファの RAM 上限チェックが実運用で常に無効の
ままになる）。

既定の解像度・fps（`config.py` の `CaptureConfig` 既定値、640×480/30fps）は
**「初期評価候補であり必須性能ではない」**（`config.py` モジュール docstring
の表現をそのまま踏襲する。`tech.md` 開発標準1）。この一文は `--width-px` /
`--height-px` / `--fps` / `--modes` の `--help` 文にも明記する（design.md
「Risks: 既定値が既成事実化しないよう `--help` に明記する」）。

============================================================================
`--source simulated` の内部供給関数（設計判断。task brief の指示に従い
ここに理由を厚く書く）
============================================================================

`open_source()`（タスク 4.6）の `SourceKind.SIMULATED` 分岐は `supplier:
FrameSupplier`（`index -> depth 配列 または None`）を必須で要求する。これは
`SimulatedSource` 自身が投擲物理・ノイズ生成を持たない設計（要件 4.5）の
帰結であり、`open_source()` はこれを合成できない。

テスト側には `tests/sensing_foundation/synthetic.py`（タスク 1.7）という
決定的な供給関数生成ヘルパが既にあるが、**`src/` は `tests/` を import
してはならない**という境界（タスク 3.2 以降で確立された規約）により、
`cli.py`（`src/` 配下）からこれを再利用することはできない。

したがって、実際のエンドユーザーが `--source simulated` を CLI から選んだ
場合、外部から供給関数を配線する手段が無い限り、本来は「合成する元データが
無い」——`simulated` モードは本来 Python API 経由（`SimulatedSource` を
スクリプトで直接構築し、独自の供給関数を渡す）で使うか、将来の
`trajectory-simulator` Spec が実装する物理ベースの供給関数と組み合わせて
使うことを想定した、テスト・開発ツールである。

本実装はこれを次のように解決する: `--source simulated` が CLI から選ばれ、
かつ外部供給関数の配線手段が無い場合にのみ、`_smoke_supplier()`（本モジュール
下部）という**最小限の内部生成器**を使う。これは:

- 投擲物理・ノイズモデルを一切持たない、決定的な単純パターン（`index` から
  一意に決まる一定値の配列）を返すだけの「スモークテスト用」生成器である。
- `trajectory-simulator`（将来の Spec）が提供する物理ベースの供給関数とは
  明確に別物であり、精度・リアリズムを一切主張しない。
- CLI がハードウェアも外部供給関数も無い状態で `record` → `replay-session`
  → `summarize` という一連の動作を実演できるようにする、という一点のみを
  目的とする。

一方、`run_capture()` / `run_record()` / `run_bench_modes()` /
`run_bench_logging()`（本モジュールの各サブコマンド本体、`main()` からも
テストからも呼べる）は `supplier: FrameSupplier | None = None` という
キーワード引数を持ち、`None` かつ `settings.source == SourceKind.SIMULATED`
のときにのみ `_smoke_supplier()` へフォールバックする。呼び出し側（将来の
`trajectory-simulator` 連携、または本 Spec のテストスイート自身）が明示的に
`supplier=...` を渡せば、その供給関数がそのまま使われる——`open_source()`
自身が持つ「supplier を合成しない」という原則を、CLI 層でも壊さない。

本 Spec のテストスイートは、`tests/sensing_foundation/synthetic.py`
（テストツリー側なので import してよい）が提供する `make_supplier()` を
`run_record()` などへ直接注入し、契約違反・欠番供給などのケースを検証する
（argv 経由の `main()` では表現できない注入である点に注意）。

============================================================================
入力元は必ず文脈管理で開閉する
============================================================================

`FrameSource` に触れるすべてのサブコマンド（`capture` / `record` /
`replay-session`）は `with source:` を用いる。`bench-modes` /
`bench-logging` は `ModeSweep.run()` / `LoggingOverheadBench.run()` が
それぞれ内部で `with source:` を行うため（`bench/modes.py` /
`bench/logging_overhead.py` の実装済みコード参照）、本モジュールが重ねて
文脈管理をする必要はない。これにより、途中終了（例外・`KeyboardInterrupt`）
でも `source.stop()`（`__exit__`）が必ず走る。

============================================================================
サブコマンドの実体は `run_*()` 関数として切り出す
============================================================================

`main(argv)` は「argv を解析し、`RuntimeSettings` を解決し、`run_*()` を
呼び、結果を JSON で表示する」という薄い配線のみを行う。各サブコマンドの
本体は `run_doctor()` / `run_capture()` / `run_record()` /
`run_replay_session()` / `run_bench_modes()` / `run_bench_logging()` /
`run_summarize()` という独立した関数として実装し、`main()` からもテストからも
（`supplier` の注入を含め）直接呼べるようにする。これにより、subprocess を
起動せずに `argparse` の標準的なテスト手法（`argv` を組み立てて呼び出し、
戻り値・stdout/stderr・例外を確認する）が使える。
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import platform
import re
import socket
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from sensing_foundation.bench.logging_overhead import LoggingOverheadBench
from sensing_foundation.bench.modes import ModeSweep
from sensing_foundation.config import CaptureConfig, RuntimeSettings
from sensing_foundation.doctor import Doctor
from sensing_foundation.errors import SensingConfigError, SensingFoundationError
from sensing_foundation.logsummary import summarize_log
from sensing_foundation.metrics import CaptureMetrics
from sensing_foundation.obslog import get_logger
from sensing_foundation.recording import layout
from sensing_foundation.recording.reader import SessionReader
from sensing_foundation.recording.ringbuffer import FrameRingBuffer
from sensing_foundation.recording.writer import SessionRecorder
from sensing_foundation.source import FrameSource, open_source
from sensing_foundation.sources.recorded import ReplaySpeed
from sensing_foundation.sources.simulated import FrameSupplier
from sensing_foundation.sysstat import Sysstat
from sensing_foundation.timebase import SessionClock
from sensing_foundation.types import SourceKind

__all__ = ["main"]

#: `config.py`（タスク 1.6）の `CaptureConfig`/`RuntimeSettings` docstring が
#: 既に確立している表現をそのまま踏襲する（`--help` に既成事実化を防ぐ注記を
#: 入れるための固定文字列）。
_INITIAL_CANDIDATE_NOTE = "初期評価候補であり必須性能ではない"

#: `_collect_overrides()` が `RuntimeSettings.resolve(overrides=...)` へ渡す
#: キー集合。`config.py._FIELD_SPECS` のキー名と1対1で一致させる（値は
#: argparse の dest 名としてもそのまま使う——ハイフン区切りの CLI フラグ名は
#: argparse がアンダースコアの dest へ自動変換するため、フラグ名とキー名を
#: 素直に揃えれば変換テーブルが不要になる）。
_SETTINGS_KEYS: tuple[str, ...] = (
    "source",
    "session_path",
    "width_px",
    "height_px",
    "fps",
    "color_enabled",
    "queue_capacity",
    "drain_enabled",
    "acquire_timeout_ms",
    "on_acquire_error",
    "recording_enabled",
    "recording_mode",
    "ring_seconds",
    "compression",
    "recording_root",
    "max_ring_bytes",
    "logging_enabled",
    "logging_path",
    "logging_queue_capacity",
    "flush_each_line",
)

_MODE_TOKEN_RE = re.compile(r"^(\d+)x(\d+)@(\d+)(c)?$")

_TOP_LEVEL_DESCRIPTION = (
    "smart-trashbox-tribute sensing-foundation の CLI 入口。\n"
    "診断（doctor）・取得（capture）・記録（record）・再生（replay-session）・"
    "モード掃引（bench-modes）・計測比較（bench-logging）・集計（summarize）"
    "を提供する。\n\n"
    "注意: replay-session は『セッション記録（フレーム層、var/sessions/ 配下の"
    "生 Depth フレーム列）の再生』であり、prediction_core.replay(record) が"
    "行う『Throw Record（サンプル層）からの予測の再実行』とは別の操作である。"
    "層が異なるため、意図的に replay ではなく replay-session という名前に"
    "してある。\n\n"
    "実行時設定は CLI 引数 > 環境変数（STB_SF_*） > 設定ファイル（--config）"
    "> 既定値の順で解決する。--print-settings で解決結果のみを表示できる。"
)


# ----------------------------------------------------------------------------
# `--source simulated` 用の内部スモークテスト生成器
# ----------------------------------------------------------------------------


def _smoke_supplier(width_px: int, height_px: int) -> FrameSupplier:
    """`--source simulated` に外部供給関数が配線されていない場合にのみ使う、
    最小限の内部生成器（モジュール docstring「`--source simulated` の内部
    供給関数」を参照）。

    投擲物理・ノイズモデルを一切持たない、`index` から一意に決まる決定的な
    一定値パターンを返すだけの「スモークテスト用」生成器であり、
    `trajectory-simulator`（将来の Spec）の物理ベースの供給関数とは別物
    である。有限系列ではなく常に値を返し続ける（`None` を返さない）——
    呼び出し側（`run_capture()`/`run_record()` 等）が `duration_s` 等の
    時間で打ち切る前提。
    """

    def supplier(index: int) -> np.ndarray | None:
        value = 500 + (index % 50)
        return np.full((height_px, width_px), value, dtype=np.uint16)

    return supplier


def _resolve_supplier(
    settings: RuntimeSettings, supplier: FrameSupplier | None
) -> FrameSupplier | None:
    """`supplier` が未指定かつ `source=SIMULATED` のときのみ内部生成器へ委ねる。"""
    if supplier is not None:
        return supplier
    if settings.source == SourceKind.SIMULATED:
        return _smoke_supplier(settings.capture.width_px, settings.capture.height_px)
    return None


# ----------------------------------------------------------------------------
# 共通ヘルパ
# ----------------------------------------------------------------------------


def _live_only(source: FrameSource, attr_name: str) -> Any:
    """live の入力元だけが公開する属性を、無ければ `None`（欠測）として読む。

    `getattr()` で問い合わせるのは、`cli` が `RealSenseSource` を名指しで
    import しないためである（入力元の構築は `open_source()` へ一本化されて
    おり、CLI はアダプタの実体を知らない。`bench/modes.py` の
    `_detect_usb2_invalid()` が採る問い合わせ方と同じ）。

    `kind` で live を先に絞るのは、**物理デバイスを持たない入力元について
    値を捏造しないため**である（要件 3.5）。recorded / simulated では
    `None`（欠測）を返す。

    戻り値が `Any` なのは、CLI がアダプタの型を知らないためである
    （`sources/realsense.py` が SDK オブジェクトへ `Any` を使うのと同じ理由）。
    """
    if source.kind is not SourceKind.LIVE:
        return None
    return getattr(source, attr_name, None)


def _build_device_info(source: FrameSource) -> dict[str, object] | None:
    """`manifest.json` の `device` オブジェクトを組み立てる（要件 5.2。タスク 8.3）。

    live のときは `RealSenseSource.device_identity`（タスク 6.3）——
    **パイプラインが実際に開いた個体**の識別情報——を写す。`probe_devices()`
    の列挙結果ではないので、複数台つながっていても記録に残るのは撮った個体
    である。

    キー名は `manifest.json` のスキーマ（design.md「Data Models」）の語彙で
    あり、`DeviceIdentity` のフィールド名（`rs.camera_info` の列挙値名）とは
    異なる。**この対応付けは記録側の責務**である——アダプタが保存形式の語彙を
    知ると、記録形式を変えるたびに SDK アダプタを触ることになる。

    Returns:
        live のとき、取得できた項目を写した dict（取得できなかった項目は
        `None`。要件 3.5「取れないものは欠測として残す」）。live 以外、または
        入力元が識別情報を公開しない場合は `None`。

        **全項目が `None` の dict と `None` は別の意味である**: 前者は
        「live で撮ったが SDK が何も答えなかった」、後者は「そもそも
        物理デバイスで撮っていない」。
    """
    identity = _live_only(source, "device_identity")
    if identity is None:
        return None
    return {
        "name": identity.name,
        "serial": identity.serial_number,
        "firmware": identity.firmware_version,
        "usb_type": identity.usb_type_descriptor,
        "product_line": identity.product_line,
    }


def _build_runtime_info(source: FrameSource) -> dict[str, object]:
    """`manifest.json` の `runtime` オブジェクトを組み立てる（CLI からの最小実装）。

    `global_time_enabled` は live のとき `RealSenseSource.global_time_enabled`
    （有効化を試みた結果。タスク 6.1）を写す。**入力元を開いた後に呼ぶこと**
    ——開く前には有効化の結果が存在しない（タスク 8.3 で是正した欠陥は、
    まさに開く前に組み立てていたために `None` 固定になっていたことだった）。

    `True` / `False` は「試みて成功した」「試みて失敗した」、`None` は
    「そもそも試みていない」（live 以外）を意味する。
    """
    return {
        "os": platform.system(),
        "os_release": platform.release(),
        "python_version": platform.python_version(),
        "hostname": socket.gethostname(),
        "global_time_enabled": _live_only(source, "global_time_enabled"),
    }


def _drain_frames(
    source: FrameSource,
    duration_s: float | None,
    *,
    on_frame,
    metrics: CaptureMetrics,
) -> int:
    """先頭フレームの `t_capture_ms` を起点に `duration_s` 秒ぶんフレームを取り出す。

    `duration_s` が `None` の場合は供給が尽きるまで取り出す。取り出した各
    フレームは `on_frame(frame)` へ渡され、`metrics.snapshot_resources()` を
    毎フレーム呼ぶ（間隔制御は `CaptureMetrics` 自身の責務。design.md 参照）。

    Returns:
        取り出したフレーム数。
    """
    count = 0
    start_ms: float | None = None
    for frame in source.frames():
        if start_ms is None:
            start_ms = frame.t_capture_ms
        on_frame(frame)
        metrics.snapshot_resources()
        count += 1
        if duration_s is not None and (frame.t_capture_ms - start_ms) >= duration_s * 1000.0:
            break
    return count


def _log_path_for(settings: RuntimeSettings, session_id: str) -> str | None:
    """`StructuredLogger` が使うファイル名規約（`obslog.py` 参照）から、ログの
    パスを予測する。`settings.logging.enabled` が偽なら `None`。
    """
    if not settings.logging.enabled:
        return None
    return str(Path(settings.logging.path) / f"{session_id}.ndjson")


def _json_default(obj: object) -> object:
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"JSON化できない値: {obj!r}")


def _dump_json(payload: object) -> str:
    return json.dumps(payload, default=_json_default, ensure_ascii=False, indent=2)


# ----------------------------------------------------------------------------
# サブコマンド本体（`main()` からも、テストからも `supplier` 注入つきで呼べる）
# ----------------------------------------------------------------------------


def run_doctor(settings: RuntimeSettings) -> list[dict[str, object]]:
    """`doctor` サブコマンドの本体。9項目の診断結果を JSON 互換の list で返す。"""
    doctor = Doctor(capture_config=settings.capture, destination=settings.recording.root)
    return json.loads(doctor.report_json())


def run_capture(
    settings: RuntimeSettings,
    *,
    duration_s: float,
    supplier: FrameSupplier | None = None,
    speed: ReplaySpeed = "fast",
) -> dict[str, object]:
    """`capture` サブコマンドの本体。取得のみ行い、統計を返す（記録しない）。"""
    resolved_supplier = _resolve_supplier(settings, supplier)

    session_id = layout.new_session_id(time.time() * 1000.0)
    clock = SessionClock(session_id=session_id)
    sysstat = Sysstat()
    logger = get_logger(settings.logging, clock)
    metrics = CaptureMetrics(logger, clock, sysstat)

    try:
        source = open_source(
            settings, metrics, clock=clock, supplier=resolved_supplier, speed=speed
        )
        with source:
            frames_seen = _drain_frames(
                source, duration_s, on_frame=lambda _frame: None, metrics=metrics
            )
            stats = source.stats
    finally:
        logger.close()

    return {
        "session_id": session_id,
        "log_path": _log_path_for(settings, session_id),
        "frames_seen": frames_seen,
        "stats": dataclasses.asdict(stats),
    }


def run_record(
    settings: RuntimeSettings,
    *,
    duration_s: float,
    supplier: FrameSupplier | None = None,
    speed: ReplaySpeed = "fast",
) -> dict[str, object]:
    """`record` サブコマンドの本体。取得＋記録（`recording.mode` の ring/continuous）。

    `mode == "ring"` の場合、`FrameRingBuffer` へ蓄積し、`duration_s` 経過
    （または供給の終端）で1回だけ `flush_to()` する（design.md「記録
    （リングバッファ＋トリガ保存）」のトリガの一種。実運用の投擲検出トリガ
    はこの Spec の範囲外であり、CLI は「区間終了」を唯一のトリガとして扱う
    ——このシンプルなトリガ方針を `--help` にも明記する）。
    """
    resolved_supplier = _resolve_supplier(settings, supplier)

    session_id = layout.new_session_id(time.time() * 1000.0)
    clock = SessionClock(session_id=session_id)
    sysstat = Sysstat()
    logger = get_logger(settings.logging, clock)
    metrics = CaptureMetrics(logger, clock, sysstat)

    try:
        source = open_source(
            settings, metrics, clock=clock, supplier=resolved_supplier, speed=speed
        )
        # 入力元は必ず文脈管理で開閉する（要件・task brief）。途中終了・
        # 例外時でも source.stop() が走る。
        with source:
            recorder_cm = SessionRecorder(
                root=settings.recording.root,
                session_id=session_id,
                profile=source.profile,
                device=_build_device_info(source),
                runtime=_build_runtime_info(source),
                compression=settings.recording.compression,
                logger=logger,
                source=settings.source,
                capture=dataclasses.asdict(settings.capture),
            )
            with recorder_cm as recorder:
                if settings.recording.mode == "ring":
                    ring = FrameRingBuffer(
                        settings.recording.ring_seconds,
                        source.profile,
                        max_bytes=settings.recording.max_ring_bytes,
                    )
                    frames_seen = _drain_frames(
                        source, duration_s, on_frame=ring.append, metrics=metrics
                    )
                    frames_written = ring.flush_to(recorder)
                else:
                    frames_seen = _drain_frames(
                        source, duration_s, on_frame=recorder.write, metrics=metrics
                    )
                    frames_written = frames_seen
                stats = source.stats
                recording_stats = recorder.close(stats)
    finally:
        logger.close()

    return {
        "session_id": session_id,
        "session_dir": str(layout.session_dir(settings.recording.root, session_id)),
        "log_path": _log_path_for(settings, session_id),
        "mode": settings.recording.mode,
        "frames_seen": frames_seen,
        "frames_written": frames_written,
        "capture_stats": dataclasses.asdict(stats),
        "recording_stats": dataclasses.asdict(recording_stats),
    }


def run_replay_session(
    settings: RuntimeSettings,
    *,
    speed: ReplaySpeed = "fast",
) -> dict[str, object]:
    """`replay-session` サブコマンドの本体。

    セッション記録（フレーム層）を再生し、統計と一致性
    （再生できた枚数が索引の枚数と一致するか）を確認する。
    `prediction_core.replay(record)`（サンプル層の予測再実行）とは別の操作
    である（モジュール docstring 参照）。
    """
    if settings.session_path is None:
        raise SensingConfigError(
            "replay-session には --session（再生対象のセッションディレクトリ）が"
            "必須である（起動前検証）。"
        )

    # 一致性確認のための独立した参照読み出し（`open_source()` が内部で構築
    # する `SessionReader` とは別インスタンス。セッションディレクトリを2回
    # 開く軽いコストと引き換えに、再生結果と索引の期待値を独立に比較できる）。
    reference_reader = SessionReader(settings.session_path)
    expected_frames = len(reference_reader)
    summary_present = reference_reader.summary is not None

    session_id = layout.new_session_id(time.time() * 1000.0)
    clock = SessionClock(session_id=session_id)
    logger = get_logger(settings.logging, clock)
    metrics = CaptureMetrics(logger, clock, sysstat=None)

    try:
        source = open_source(settings, metrics, clock=clock, speed=speed)
        with source:
            frames_replayed = 0
            for _frame in source.frames():
                frames_replayed += 1
            stats = source.stats
    finally:
        logger.close()

    return {
        "session_path": str(settings.session_path),
        "log_path": _log_path_for(settings, session_id),
        "expected_frames": expected_frames,
        "frames_replayed": frames_replayed,
        "consistent": frames_replayed == expected_frames,
        "summary_present": summary_present,
        "stats": dataclasses.asdict(stats),
    }


def _parse_modes(spec: str) -> list[CaptureConfig]:
    """`--modes` のカンマ区切り仕様（例 `"640x480@30,640x480@60c"`）を解析する。

    トークンは `WIDTHxHEIGHT@FPS`（Color 無効）または `WIDTHxHEIGHT@FPSc`
    （Color 有効、末尾 `c`）の形式。
    """
    modes: list[CaptureConfig] = []
    for token in (t.strip() for t in spec.split(",")):
        if not token:
            continue
        match = _MODE_TOKEN_RE.match(token)
        if match is None:
            raise SensingConfigError(
                f"--modes のトークンを解釈できない: {token!r}"
                "（'WIDTHxHEIGHT@FPS' または 'WIDTHxHEIGHT@FPSc'（Color有効）"
                "の形式で指定せよ）"
            )
        width, height, fps, color_flag = match.groups()
        modes.append(
            CaptureConfig(
                width_px=int(width),
                height_px=int(height),
                fps=int(fps),
                color_enabled=color_flag is not None,
            )
        )
    if not modes:
        raise SensingConfigError("--modes に少なくとも1つの候補モードを指定する必要がある")
    return modes


def run_bench_modes(
    settings: RuntimeSettings,
    *,
    modes: Sequence[CaptureConfig],
    duration_s: float,
    window_ms: float,
    warmup_s: float,
    supplier: FrameSupplier | None = None,
    speed: ReplaySpeed = "fast",
) -> dict[str, object]:
    """`bench-modes` サブコマンドの本体。解像度・fps を掃引する。

    入力元は `ModeSweep.run()` が内部で `with source:` する（本関数では
    重ねて文脈管理しない。モジュール docstring 参照）。
    """
    resolved_supplier = _resolve_supplier(settings, supplier)
    sweep = ModeSweep(modes=modes, duration_s=duration_s, window_ms=window_ms, warmup_s=warmup_s)
    results = sweep.run(settings, supplier=resolved_supplier, speed=speed)
    return {
        "effective_samples_per_window_note": (
            "ModeResult.effective_samples_per_window は『フレーム層』の実効"
            "サンプル数である。flying-object-tracking が定義する『点層』の"
            "実効点数（effective_points_per_window）とは別物の、対になる指標"
            "であり、片方だけ定義を変えると比較が成立しなくなる。"
        ),
        "results": [dataclasses.asdict(r) if r is not None else None for r in results],
    }


def run_bench_logging(
    settings: RuntimeSettings,
    *,
    segment_s: float,
    cycles: int,
    work_dir: Path,
    supplier: FrameSupplier | None = None,
    speed: ReplaySpeed = "fast",
) -> dict[str, object]:
    """`bench-logging` サブコマンドの本体。計測 ON/OFF の3条件を比較する。

    入力元は `LoggingOverheadBench.run()` が内部で `with source_off,
    source_on, source_rec:` する（本関数では重ねて文脈管理しない）。
    """
    resolved_supplier = _resolve_supplier(settings, supplier)
    bench = LoggingOverheadBench(segment_s=segment_s, cycles=cycles)
    report = bench.run(settings, supplier=resolved_supplier, work_dir=work_dir, speed=speed)
    return dataclasses.asdict(report)


def run_summarize(log_path: Path, *, stages: Sequence[str] | None = None) -> dict[str, object]:
    """`summarize` サブコマンドの本体。ログを stage×event ごとに集計する。"""
    summary = summarize_log(Path(log_path), stages=stages)
    return {
        "events": [dataclasses.asdict(event_summary) for event_summary in summary.events.values()],
        "total_lines": summary.total_lines,
        "skipped_lines": summary.skipped_lines,
        "log_format_version": summary.log_format_version,
        "dropped_total": summary.dropped_total,
    }


# ----------------------------------------------------------------------------
# argparse 構築
# ----------------------------------------------------------------------------


def _build_common_parser() -> argparse.ArgumentParser:
    """全サブコマンドが継承する、実行時設定フラグ群（`RuntimeSettings` へ対応）。"""
    parser = argparse.ArgumentParser(add_help=False)
    group = parser.add_argument_group(
        "実行時設定（解決順序: CLI引数 > 環境変数(STB_SF_*) > 設定ファイル > 既定値）"
    )
    group.add_argument(
        "--config",
        metavar="PATH",
        default=None,
        help="JSON 設定ファイルのパス（省略時は設定ファイル層を使わない）。",
    )
    group.add_argument(
        "--source",
        choices=["live", "recorded", "simulated"],
        default=None,
        help=(
            "入力元の種別（既定 live）。replay-session はこのフラグに関わらず"
            "常に recorded を使う。"
        ),
    )
    group.add_argument(
        "--session",
        dest="session_path",
        metavar="PATH",
        default=None,
        help="再生対象のセッションディレクトリ（--source recorded / replay-session で必須）。",
    )
    group.add_argument(
        "--width-px",
        type=int,
        default=None,
        help=f"Depth ストリームの幅（px）。既定 640 は{_INITIAL_CANDIDATE_NOTE}（config.py 参照）。",
    )
    group.add_argument(
        "--height-px",
        type=int,
        default=None,
        help=f"Depth ストリームの高さ（px）。既定 480 は{_INITIAL_CANDIDATE_NOTE}。",
    )
    group.add_argument(
        "--fps",
        type=int,
        default=None,
        help=f"要求フレームレート（fps）。既定 30 は{_INITIAL_CANDIDATE_NOTE}。",
    )
    group.add_argument(
        "--color-enabled",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Color ストリームを有効にするか（既定: 無効）。",
    )
    group.add_argument("--queue-capacity", type=int, default=None, help="取得キューの容量。")
    group.add_argument(
        "--drain-enabled",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="ドレイン（最新追従）を行うか。live のみで意味を持つ。",
    )
    group.add_argument(
        "--acquire-timeout-ms", type=int, default=None, help="1フレーム取得の最大待機時間（ms）。"
    )
    group.add_argument(
        "--on-acquire-error",
        choices=["continue", "stop"],
        default=None,
        help="取得失敗時の挙動。",
    )
    group.add_argument(
        "--recording-enabled",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="記録設定の enabled 値（record サブコマンドはこの値に関わらず常に記録する）。",
    )
    group.add_argument(
        "--recording-mode",
        choices=["ring", "continuous"],
        default=None,
        help="記録方式（record サブコマンドが使う）。",
    )
    group.add_argument(
        "--ring-seconds", type=float, default=None, help="ring モードで保持する秒数。"
    )
    group.add_argument(
        "--compression", choices=["none", "zlib"], default=None, help="記録ブロブの圧縮方式。"
    )
    group.add_argument(
        "--recording-root", metavar="PATH", default=None, help="セッション記録の出力先ルート。"
    )
    group.add_argument(
        "--max-ring-bytes", type=int, default=None, help="リングバッファの許容バイト数の上限。"
    )
    group.add_argument(
        "--logging-enabled",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="構造化ロギングを有効にするか（既定: 有効）。",
    )
    group.add_argument(
        "--logging-path", metavar="PATH", default=None, help="ログ出力先ディレクトリ。"
    )
    group.add_argument(
        "--logging-queue-capacity", type=int, default=None, help="ログ送出キューの容量。"
    )
    group.add_argument(
        "--flush-each-line",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="行単位でフラッシュするか。",
    )
    group.add_argument(
        "--print-settings",
        action="store_true",
        default=False,
        help="解決済みの実行時設定を JSON で表示し、サブコマンドの本処理を実行せずに終了する。",
    )
    return parser


def _build_parser() -> argparse.ArgumentParser:
    common = _build_common_parser()
    parser = argparse.ArgumentParser(
        prog="sensing-foundation",
        description=_TOP_LEVEL_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "doctor",
        parents=[common],
        help="環境診断を実行し JSON で報告する。",
        description="環境診断（development-environment.md §16 相当）を実行し、9項目の結果を JSON で報告する。",
    )

    capture_parser = subparsers.add_parser(
        "capture",
        parents=[common],
        help="取得のみ行い統計を表示する（記録は行わない）。",
    )
    capture_parser.add_argument(
        "--duration-s", type=float, default=1.0, help="取得を継続する秒数（既定 1.0 秒）。"
    )
    capture_parser.add_argument(
        "--speed",
        choices=["fast", "realtime"],
        default="fast",
        help="--source recorded のときのみ意味を持つ再生速度。",
    )

    record_parser = subparsers.add_parser(
        "record",
        parents=[common],
        help="取得＋記録（ring / continuous）。",
    )
    record_parser.add_argument(
        "--duration-s", type=float, default=1.0, help="記録を継続する秒数（既定 1.0 秒）。"
    )
    record_parser.add_argument(
        "--speed",
        choices=["fast", "realtime"],
        default="fast",
        help="--source recorded のときのみ意味を持つ再生速度。",
    )

    replay_parser = subparsers.add_parser(
        "replay-session",
        parents=[common],
        help="セッション記録（フレーム層）を再生し、統計と一致性を確認する。",
        description=(
            "セッション記録（フレーム層、var/sessions/<id>/ 配下の生 Depth フレーム列）"
            "を再生する。prediction_core.replay(record)（Throw Record／サンプル層からの"
            "予測の再実行）とは別の操作であるため、意図的に replay ではなく"
            "replay-session という名前にしてある。--session でセッションディレクトリを"
            "指定すること（未指定は起動前に失敗する）。--source に何を指定していても"
            "常に recorded として扱う。"
        ),
    )
    replay_parser.add_argument(
        "--speed",
        choices=["fast", "realtime"],
        default="fast",
        help="実時間再生（realtime）か最速再生（fast、既定）か。",
    )

    bench_modes_parser = subparsers.add_parser(
        "bench-modes",
        parents=[common],
        help="解像度・fps の掃引を実行する。",
    )
    bench_modes_parser.add_argument(
        "--modes",
        default="640x480@30,640x480@60",
        help=(
            "掃引する候補モードのカンマ区切りリスト（例 '640x480@30,640x480@60c'。"
            "末尾 'c' で Color 有効）。既定の 640x480@30/60fps は"
            f"{_INITIAL_CANDIDATE_NOTE}。"
        ),
    )
    bench_modes_parser.add_argument(
        "--duration-s",
        type=float,
        default=1.0,
        help="モードごとの測定継続秒数（ウォームアップ除く）。",
    )
    bench_modes_parser.add_argument(
        "--window-ms", type=float, default=200.0, help="実効サンプル数の評価窓長（ms）。"
    )
    bench_modes_parser.add_argument(
        "--warmup-s", type=float, default=0.0, help="モードごとのウォームアップ秒数。"
    )
    bench_modes_parser.add_argument(
        "--speed",
        choices=["fast", "realtime"],
        default="fast",
        help="--source recorded のときのみ意味を持つ再生速度。",
    )

    bench_logging_parser = subparsers.add_parser(
        "bench-logging",
        parents=[common],
        help="計測 ON/OFF の3条件（logging_off/logging_on/recording_on）を比較する。",
    )
    bench_logging_parser.add_argument(
        "--segment-s", type=float, default=0.05, help="1セグメント（1条件連続実行）の秒数。"
    )
    bench_logging_parser.add_argument(
        "--cycles", type=int, default=3, help="A/B/C 1巡の繰り返し回数。"
    )
    bench_logging_parser.add_argument(
        "--work-dir",
        default=None,
        help="ログ・記録の作業ディレクトリ（既定: <recording-rootの親>/bench-logging）。",
    )
    bench_logging_parser.add_argument(
        "--speed",
        choices=["fast", "realtime"],
        default="fast",
        help="--source recorded のときのみ意味を持つ再生速度。",
    )

    summarize_parser = subparsers.add_parser(
        "summarize",
        parents=[common],
        help="ログ・記録の集計を行う。",
    )
    summarize_parser.add_argument(
        "--log",
        dest="log_path",
        required=True,
        metavar="PATH",
        help="集計対象の NDJSON ログファイル。",
    )
    summarize_parser.add_argument(
        "--stages",
        default=None,
        help="集計対象を絞る段階名のカンマ区切りリスト（省略時は全段階を対象にする）。",
    )

    return parser


def _collect_overrides(args: argparse.Namespace) -> dict[str, object]:
    overrides: dict[str, object] = {}
    for key in _SETTINGS_KEYS:
        value = getattr(args, key, None)
        if value is not None:
            overrides[key] = value
    return overrides


def _resolve_settings(args: argparse.Namespace) -> RuntimeSettings:
    overrides = _collect_overrides(args)
    if args.command == "replay-session":
        # replay-session を選ぶこと自体が「セッション記録を再生したい」という
        # 意図であるため、--source に何を指定していても recorded を強制する
        # （モジュール docstring「設定解決順序」参照）。
        overrides["source"] = "recorded"

    file_path = Path(args.config) if args.config else None
    installed_ram_bytes = Sysstat().sample().system_total_bytes
    return RuntimeSettings.resolve(
        file=file_path,
        env=os.environ,
        overrides=overrides,
        installed_ram_bytes=installed_ram_bytes,
    )


def _dispatch(args: argparse.Namespace, settings: RuntimeSettings) -> object:
    if args.command == "doctor":
        return run_doctor(settings)
    if args.command == "capture":
        return run_capture(settings, duration_s=args.duration_s, speed=args.speed)
    if args.command == "record":
        return run_record(settings, duration_s=args.duration_s, speed=args.speed)
    if args.command == "replay-session":
        return run_replay_session(settings, speed=args.speed)
    if args.command == "bench-modes":
        modes = _parse_modes(args.modes)
        return run_bench_modes(
            settings,
            modes=modes,
            duration_s=args.duration_s,
            window_ms=args.window_ms,
            warmup_s=args.warmup_s,
            speed=args.speed,
        )
    if args.command == "bench-logging":
        if args.work_dir:
            work_dir = Path(args.work_dir)
        else:
            work_dir = Path(settings.recording.root).parent / "bench-logging"
        return run_bench_logging(
            settings,
            segment_s=args.segment_s,
            cycles=args.cycles,
            work_dir=work_dir,
            speed=args.speed,
        )
    if args.command == "summarize":
        stages = [s.strip() for s in args.stages.split(",") if s.strip()] if args.stages else None
        return run_summarize(Path(args.log_path), stages=stages)
    raise AssertionError(f"未知のサブコマンド: {args.command}")  # argparse の choices で到達しないはず


def main(argv: Sequence[str] | None = None) -> int:
    """CLI エントリポイント。`python -m sensing_foundation.cli` からも呼ばれる。"""
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        settings = _resolve_settings(args)
    except SensingConfigError as exc:
        print(f"設定エラー: {exc}", file=sys.stderr)
        return 2

    if args.print_settings:
        print(_dump_json(dataclasses.asdict(settings)))
        return 0

    try:
        result = _dispatch(args, settings)
    except SensingConfigError as exc:
        print(f"設定エラー: {exc}", file=sys.stderr)
        return 2
    except SensingFoundationError as exc:
        print(f"実行エラー: {exc}", file=sys.stderr)
        return 1

    print(_dump_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
