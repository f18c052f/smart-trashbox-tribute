"""sensing-foundation: RealSense 由来のセンシング基盤パッケージ。

本モジュールは公開 API の**再エクスポート専用**であり、ロジックを一切持たない
（design.md「PublicApi / Responsibilities & Constraints」、タスク 8.2）。
下流3 Spec（`world-frame-calibration` / `flying-object-tracking` /
`m1-prediction-validation`）が参照してよいシンボルは、この `__all__` に
**明示列挙されたものだけ**である。

**アダプタ実装クラス（`SimulatedSource` / `RecordedSource` / `RealSenseSource`）
と基底クラス（`BaseFrameSource`）は公開しない。** 入力元の構築は
`open_source(settings, metrics, *, clock, ...)` へ一本化する（要件 4.1 / 4.6）。
同様に `bench` / `doctor` / `cli` の中身も公開入口には出さない
（コマンドとして使う。design.md「PublicApi」節）。

**design.md の `__all__` 擬似コードからの2つの追加**（design.md「Revalidation
Triggers」: `__all__` からの**削除・改名**は下流 Spec の再検証を要求するが、
**追加はこれに該当しない**）:

1. `ThrowRecordFormatError` / `ThrowRecordVersionError`
   （`errors.py`、タスク5で新設）。design.md の Error Categories コード
   ブロックは Throw Record 保存層が生まれる前に書かれたため、これら2例外を
   まだ含んでいない（タスク5・7.1 の Implementation Notes が明記するドキュメント
   同期漏れ）。`ThrowRecordStore` の呼び出し側がこれらを捕捉するには公開入口に
   出す必要があり、内部モジュール（`sensing_foundation.errors`）への直接
   import を強いるのは公開 API を一点集約する目的（要件 12.8）に反する。
2. `link_to_session`（`throw_store.py`、タスク5で新設）。`ThrowRecordStore`
   と対になる、観測フレーム記録（セッション記録）と Throw Record を
   `extra["sensing"]` 経由で対応付ける関数であり（要件 7.7）、
   `throw_store.py` 自身の `__all__` に既に含まれる（タスク5時点で
   モジュールの公開面として扱われている）。Throw Record の検出結果を
   セッション記録と対応付ける相関ワークフローの中核であり、
   `m1-prediction-validation` を含む下流 Spec が呼ぶ必要がある。
   これを公開入口に出さないと、下流は `sensing_foundation.throw_store` へ
   直接 import せざるを得なくなり、依存方向の静的検証が壊れる。

**含めなかったもの**: `bench/*`（`ModeResult` / `ModeSweep` / `OverheadResult` /
`OverheadVerdict` / `LoggingOverheadReport` / `LoggingOverheadBench`）・
`doctor.py`（`Doctor` / `CheckResult`）・`cli.py` の中身はいずれも
design.md が「コマンドとして使う」と明示的に除外している。`RawFrame`
（`source.py`）・`StageLogger` / `LoggerStats` / `RESERVED_STAGES`
（`obslog.py`）・`RecordingStats`（`recording/writer.py`）・
`InProgressSessionWarning`（`recording/reader.py`）・`FrameSupplier`
（`sources/simulated.py`）・`ReplaySpeed`（`sources/recorded.py`）も
design.md の `__all__` 擬似コードに無く、下流の境界テストの起点を必要以上に
広げないため、本タスクでは追加しない（`tests/sensing_foundation/
test_sensing_public_api.py` がこれらの非公開を回帰的に固定する）。
"""

from __future__ import annotations

from sensing_foundation.config import (
    CaptureConfig,
    LoggingConfig,
    RecordingConfig,
    RuntimeSettings,
)
from sensing_foundation.errors import (
    DeviceNotReadyError,
    RecordingFormatError,
    RecordingVersionError,
    RecordingWriteError,
    SensingConfigError,
    SensingFoundationError,
    SourceContractError,
    SourceUnavailableError,
    ThrowRecordFormatError,
    ThrowRecordVersionError,
)
from sensing_foundation.geometry import (
    INVALID_DEPTH_RAW,
    depth_raw_to_mm,
    deproject_pixel,
    is_valid_depth,
)
from sensing_foundation.logsummary import LogSummary, summarize_log
from sensing_foundation.metrics import CaptureMetrics, FrameTiming
from sensing_foundation.obslog import (
    LOG_FORMAT_VERSION,
    LogEvent,
    Logger,
    NullLogger,
    StructuredLogger,
    get_logger,
)
from sensing_foundation.recording.layout import RECORDING_FORMAT_VERSION
from sensing_foundation.recording.reader import SessionReader
from sensing_foundation.recording.ringbuffer import FrameRingBuffer
from sensing_foundation.recording.writer import SessionRecorder
from sensing_foundation.source import FrameSource, open_source
from sensing_foundation.sysstat import ResourceSample, Sysstat
from sensing_foundation.throw_store import (
    ThrowRecordReadIssue,
    ThrowRecordStore,
    link_to_session,
)
from sensing_foundation.timebase import SessionClock
from sensing_foundation.types import (
    CameraIntrinsics,
    CaptureFrame,
    CaptureStats,
    SourceKind,
    StreamProfile,
    TimestampDomain,
)

__all__ = [
    # 型（types）
    "CaptureFrame",
    "StreamProfile",
    "CameraIntrinsics",
    "TimestampDomain",
    "CaptureStats",
    "SourceKind",
    # 幾何（geometry）
    "depth_raw_to_mm",
    "is_valid_depth",
    "deproject_pixel",
    "INVALID_DEPTH_RAW",
    # 計時・資源（timebase / sysstat）
    "SessionClock",
    "Sysstat",
    "ResourceSample",
    # 設定（config）
    "CaptureConfig",
    "RecordingConfig",
    "LoggingConfig",
    "RuntimeSettings",
    # 観測（obslog / metrics）
    "Logger",
    "StructuredLogger",
    "NullLogger",
    "LogEvent",
    "get_logger",
    "LOG_FORMAT_VERSION",
    "CaptureMetrics",
    "FrameTiming",
    # 取得（source）
    "FrameSource",
    "open_source",
    # 記録・再生（recording）
    "SessionRecorder",
    "SessionReader",
    "FrameRingBuffer",
    "RECORDING_FORMAT_VERSION",
    # Throw Record 保存（throw_store）
    "ThrowRecordStore",
    "ThrowRecordReadIssue",
    "link_to_session",
    # 集計（logsummary）— m1 が「集計器を二重に持たない」ために公開入口へ出す
    "summarize_log",
    "LogSummary",
    # 例外（errors）
    "SensingFoundationError",
    "SensingConfigError",
    "SourceUnavailableError",
    "DeviceNotReadyError",
    "SourceContractError",
    "RecordingFormatError",
    "RecordingVersionError",
    "RecordingWriteError",
    "ThrowRecordFormatError",
    "ThrowRecordVersionError",
]
