"""公開 API の確定を検証する（design.md「PublicApi」、タスク 8.2、要件 12.8）。

ファイル名は `test_public_api.py` ではなく `test_sensing_public_api.py` と
した。`tests/prediction_core/test_public_api.py` が既に存在するため、
タスク 1.5 Implementation Notes が申し送る衝突回避方針（`tests/` 配下に
`__init__.py` を置けない制約下での同名ファイル衝突対策）をそのまま踏襲する。

`sensing_foundation/__init__.py` は再エクスポートのみを行い、ロジックを
一切持たない契約である（design.md「PublicApi / Responsibilities &
Constraints」）。本テストは以下を固定する。

- `sensing_foundation.__all__` が本タスクで確定した公開シンボル一覧と
  **完全一致**すること（多すぎても少なすぎても失敗）
- `__all__` に列挙された各名前が実際に `sensing_foundation` から
  import/`getattr` できること
- `dir(sensing_foundation)` から dunder と内部サブモジュール名を除いたものが
  `__all__` の集合と一致すること（意図しない漏れ export が無いこと）
- アダプタ実装クラス・基底クラス・`bench`/`doctor`/`cli` の中身が
  一切公開されていないこと（design.md が明示的に禁止する）
- `__init__.py` のソース構造自体が再エクスポート専用（ロジックを持たない）
  であること

**design.md の `__all__` 擬似コードからの2つの追加**（`__init__.py`
モジュール docstring に詳細な理由を記載済み。要旨のみ再掲する）:

1. `ThrowRecordFormatError` / `ThrowRecordVersionError`
   （タスク5で `errors.py` に新設。design.md の Error Categories コード
   ブロックのドキュメント同期漏れ——tasks.md 5・7.1 の Implementation
   Notes が明記——であり、`ThrowRecordStore` の呼び出し側が捕捉するために
   公開入口へ出す必要がある）。
2. `link_to_session`（タスク5で `throw_store.py` に新設。同モジュール自身の
   `__all__` に既に含まれており、Throw Record とセッション記録を対応付ける
   相関ワークフローの中核として下流 Spec が呼ぶ必要がある）。

design.md「Revalidation Triggers」は `__all__` からの**削除・改名**を
下流 Spec の再検証が必要な変更として扱うが、**追加はこれに該当しない**
（tasks.md 8.2 本文にも明記される原則）ため、この2追加は本タスク単独の
判断で行ってよい。
"""

from __future__ import annotations

import ast
from pathlib import Path

import sensing_foundation

# design.md「PublicApi」の `__all__` 擬似コード43個 + 本タスクで追加した3個
# （`link_to_session` / `ThrowRecordFormatError` / `ThrowRecordVersionError`。
# 理由は本ファイル docstring・各シンボルの行コメントを参照）= 合計46個。
EXPECTED_PUBLIC_SYMBOLS: frozenset[str] = frozenset(
    {
        # types.py
        "CaptureFrame",
        "StreamProfile",
        "CameraIntrinsics",
        "TimestampDomain",
        "CaptureStats",
        "SourceKind",
        # geometry.py
        "depth_raw_to_mm",
        "is_valid_depth",
        "deproject_pixel",
        "INVALID_DEPTH_RAW",
        # timebase.py / sysstat.py
        "SessionClock",
        "Sysstat",
        "ResourceSample",
        # config.py
        "CaptureConfig",
        "RecordingConfig",
        "LoggingConfig",
        "RuntimeSettings",
        # obslog.py / metrics.py
        "Logger",
        "StructuredLogger",
        "NullLogger",
        "LogEvent",
        "get_logger",
        "LOG_FORMAT_VERSION",
        "CaptureMetrics",
        "FrameTiming",
        # source.py
        "FrameSource",
        "open_source",
        # recording/*
        "SessionRecorder",
        "SessionReader",
        "FrameRingBuffer",
        "RECORDING_FORMAT_VERSION",
        # throw_store.py
        "ThrowRecordStore",
        "ThrowRecordReadIssue",
        "link_to_session",  # design.md 擬似コードに無い追加（理由: 本ファイル docstring）
        # logsummary.py
        "summarize_log",
        "LogSummary",
        # errors.py（design.md 記載の8個 + タスク5で新設した2個）
        "SensingFoundationError",
        "SensingConfigError",
        "SourceUnavailableError",
        "DeviceNotReadyError",
        "SourceContractError",
        "RecordingFormatError",
        "RecordingVersionError",
        "RecordingWriteError",
        "ThrowRecordFormatError",  # design.md 擬似コードに無い追加（理由: 本ファイル docstring）
        "ThrowRecordVersionError",  # 同上
    }
)

#: `sensing_foundation` パッケージ直下に実在する、あらゆるトップレベル
#: サブモジュール名（`errors.py` 〜 `logsummary.py` の12個の葉モジュールに
#: 加え、`doctor` / `cli` / `bench` / `sources` / `recording` の5つの
#: パッケージ/モジュール名）。Python の import 機構は `import
#: sensing_foundation.doctor`（`test_doctor.py` など、本 Spec の**他の**
#: テストファイルが行う）を1度でも実行すると、`sensing_foundation`
#: モジュールオブジェクトへ `doctor` 属性を副作用として束縛する——
#: これは `sensing_foundation/__init__.py` が何を import するかとは
#: **無関係**に発生する CPython の仕様であり、同一プロセス内で他のテスト
#: ファイルが先に実行されたかどうかにテストの成否が依存しないよう、
#: `dir()` ベースの「漏れ export」検査ではサブモジュール名を**すべて**
#: 除外する。`__init__.py` 自身が `doctor`/`cli`/`bench`/`sources` の
#: いずれのシンボルも re-export しないことは、`dir()` に頼らない
#: `test_doctor_cli_bench_sources_not_imported_by_init_module()`
#: （ソースの静的解析）が別途固定する。
INTERNAL_SUBMODULE_NAMES: frozenset[str] = frozenset(
    {
        "errors",
        "types",
        "geometry",
        "timebase",
        "sysstat",
        "config",
        "obslog",
        "metrics",
        "source",
        "recording",
        "throw_store",
        "logsummary",
        "doctor",
        "cli",
        "bench",
        "sources",
        # `from __future__ import annotations` により `dir()` に現れる
        # 言語機能オブジェクト。公開シンボルではない。
        "annotations",
    }
)

#: 公開してはならないことを明示的に固定する内部シンボル群
#: （アダプタ実装クラス・基底クラス・`bench`/`doctor`/`cli` の中身、
#: および design.md 擬似コードに無く本タスクでも追加しないと判断した
#: モジュール固有の補助シンボル）。
NOT_PUBLIC_SYMBOLS: frozenset[str] = frozenset(
    {
        # アダプタ実装クラス・基底クラス（design.md が明示的に禁止）
        "SimulatedSource",
        "RecordedSource",
        "RealSenseSource",
        "BaseFrameSource",
        "RawFrame",
        # bench / doctor / cli の中身（design.md「コマンドとして使う」）
        "Doctor",
        "CheckResult",
        "ModeSweep",
        "ModeResult",
        "LoggingOverheadBench",
        "OverheadResult",
        "OverheadVerdict",
        "LoggingOverheadReport",
        "main",
        # design.md 擬似コードに無く、本タスクでも追加しないと判断した補助シンボル
        "StageLogger",
        "LoggerStats",
        "RESERVED_STAGES",
        "RecordingStats",
        "InProgressSessionWarning",
        "FrameSupplier",
        "ReplaySpeed",
        "probe_sdk",
        "probe_devices",
    }
)


def test_all_matches_expected_public_symbols_exactly() -> None:
    """`__all__` が本タスクで確定した公開シンボル一覧と完全一致する（多くも少なくもない）。"""
    assert set(sensing_foundation.__all__) == EXPECTED_PUBLIC_SYMBOLS


def test_all_has_no_duplicate_entries() -> None:
    """`__all__` に重複が無い（完全一致だけでは重複+別の欠落の相殺を見逃すため）。"""
    assert len(sensing_foundation.__all__) == len(set(sensing_foundation.__all__))


def test_every_symbol_in_all_is_importable_via_getattr() -> None:
    """`__all__` に列挙された各名前が `sensing_foundation` から実際に取得できる。"""
    for name in sensing_foundation.__all__:
        assert hasattr(sensing_foundation, name), f"{name} が sensing_foundation に存在しない"


def test_every_symbol_in_all_is_importable_via_from_import() -> None:
    """`from sensing_foundation import <name>` の形で全シンボルが import できる。"""
    import importlib

    module = importlib.import_module("sensing_foundation")
    for name in sensing_foundation.__all__:
        getattr(module, name)


def test_namespace_has_no_unintended_public_leaks() -> None:
    """`dir(sensing_foundation)` のうち非 dunder・非内部サブモジュール名が `__all__` と一致する。

    ここに `__all__` に無い名前が余分に現れる場合、意図せず内部実装を
    re-export してしまっている（漏れ export）ことを意味する。
    """
    visible_names = {
        name
        for name in dir(sensing_foundation)
        if not name.startswith("_") and name not in INTERNAL_SUBMODULE_NAMES
    }
    assert visible_names == EXPECTED_PUBLIC_SYMBOLS


def test_adapter_and_internal_tool_symbols_are_not_public() -> None:
    """アダプタ実装クラス・基底クラス・bench/doctor/cli の中身・design.md 未記載の
    補助シンボルが、いずれも `sensing_foundation` の属性として一切現れない
    （`__all__` に無いだけでなく `hasattr` でも取得できないことまで固定する）。
    """
    assert NOT_PUBLIC_SYMBOLS.isdisjoint(set(sensing_foundation.__all__))
    for name in NOT_PUBLIC_SYMBOLS:
        assert not hasattr(sensing_foundation, name), (
            f"{name} は公開してはならないが sensing_foundation から到達できる"
        )


def test_doctor_cli_bench_sources_not_imported_by_init_module() -> None:
    """`__init__.py` 自身のソースは `doctor` / `cli` / `bench` / `sources` の
    いずれからも一切 import しない（design.md「bench/doctor/cli も公開入口には
    出さない」を静的に固定する）。

    ランタイムの `dir(sensing_foundation)` / `hasattr(...)` は、同一プロセス内で
    他のテストファイル（`test_doctor.py` 等）が `import
    sensing_foundation.doctor` を実行した副作用を受けてしまい、テスト実行順序に
    よって結果が変わる（Python の import 機構の仕様であり `__init__.py` の
    実装とは無関係）。そのため本テストは `__init__.py` の**ソースコード**を
    `ast` で静的解析し、実行順序に依存しない形で同じ不変条件を固定する。
    """
    init_path = Path(sensing_foundation.__file__).resolve()
    tree = ast.parse(init_path.read_text(encoding="utf-8"))

    forbidden_prefixes = tuple(
        f"sensing_foundation.{name}" for name in ("doctor", "cli", "bench", "sources")
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            assert not node.module.startswith(forbidden_prefixes), (
                f"__init__.py が {node.module} を import している（行 {node.lineno}）"
            )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith(forbidden_prefixes), (
                    f"__init__.py が {alias.name} を import している（行 {node.lineno}）"
                )


def test_source_kind_re_exported_is_prediction_core_source_kind() -> None:
    """`sensing_foundation.SourceKind` が `prediction_core.SourceKind` と同一オブジェクトである
    （要件 4.3。`test_sensing_boundaries.py` の同名趣旨のテストと重複するが、
    公開 API テストとしても独立に固定する）。
    """
    import prediction_core

    assert sensing_foundation.SourceKind is prediction_core.SourceKind


def test_log_summary_and_summarize_log_are_reexported_from_logsummary_module() -> None:
    """`summarize_log` / `LogSummary` が `logsummary.py` からの再エクスポートである
    （同一オブジェクトであることを確認し、m1 が委譲する集計器が単一であることを裏付ける）。
    """
    from sensing_foundation import logsummary as logsummary_module

    assert sensing_foundation.summarize_log is logsummary_module.summarize_log
    assert sensing_foundation.LogSummary is logsummary_module.LogSummary


def test_capture_metrics_session_clock_logger_get_logger_are_public() -> None:
    """`CaptureMetrics` / `SessionClock` / `Logger` / `get_logger` が必ず `__all__` に含まれる
    （下流3 Spec が自分の段階の計測点を同一の時間基準へ足すために必須。design.md
    「PublicApi / Responsibilities & Constraints」）。
    """
    for name in ("CaptureMetrics", "SessionClock", "Logger", "get_logger"):
        assert name in sensing_foundation.__all__


def test_open_source_is_the_only_public_construction_entry_point() -> None:
    """`open_source` が公開されている一方、3アダプタの具象クラスは一切公開されない
    （入力元の生成が生成口へ一本化されていることの直接確認。要件 4.1 / 4.6）。
    """
    assert "open_source" in sensing_foundation.__all__
    for adapter_name in ("SimulatedSource", "RecordedSource", "RealSenseSource"):
        assert adapter_name not in sensing_foundation.__all__
        assert not hasattr(sensing_foundation, adapter_name)


def test_init_module_source_contains_no_logic() -> None:
    """`__init__.py` が import 文と `__all__` 定義とモジュール docstring のみで構成される。

    再エクスポート専用という設計契約（design.md「ロジックを一切持たない」）を、
    ソース構造そのものから機械的に確認する（`tests/prediction_core/
    test_public_api.py::test_init_module_source_contains_no_logic` と同じ流儀）。
    """
    init_path = Path(sensing_foundation.__file__).resolve()
    tree = ast.parse(init_path.read_text(encoding="utf-8"))

    allowed_node_types = (
        ast.Import,
        ast.ImportFrom,
        ast.Assign,
        ast.AnnAssign,
        ast.Expr,  # モジュール docstring
    )
    for node in tree.body:
        assert isinstance(node, allowed_node_types), (
            f"__init__.py に許可されないトップレベル構文が存在する: {ast.dump(node)}"
        )
        if isinstance(node, ast.Expr):
            assert isinstance(node.value, ast.Constant), (
                "__init__.py の式文は docstring のみ許可される"
            )
