"""公開 API の確定を検証する（design.md「PublicApi」、タスク 8.1、要件 7.1 / 7.3）。

`flying_object_tracking/__init__.py` は再エクスポートのみを行い、ロジックを
一切持たない契約である（design.md「Dependency Direction」表の `__init__` 行:
「再エクスポートのみ。ロジックを持たない」）。本テストは
`tests/sensing_foundation/test_sensing_public_api.py`（タスク 8.2 相当。同じ
検証の流儀を確立済み）と同じ観点で以下を固定する。

- `flying_object_tracking.__all__` が本タスクで確定した公開シンボル一覧と
  **完全一致**すること（多すぎても少なすぎても失敗）
- `__all__` に列挙された各名前が実際に `flying_object_tracking` から
  import/`getattr` できること
- `dir(flying_object_tracking)` から dunder と内部サブモジュール名を除いた
  ものが `__all__` の集合と一致すること（意図しない漏れ export が無いこと）
- 内部実装（`PointEstimator` / `SingleObjectTracker` / `MaskBuilder` /
  `create_mask_builder` / 3方式の具象マスク実装 / `DefaultDetector` /
  判定規則の内部詳細 / CLI の `main` 等）が一切公開されていないこと
- `__init__.py` のソース構造自体が再エクスポート専用（ロジックを持たない）
  であること
"""

from __future__ import annotations

import ast
from pathlib import Path

import flying_object_tracking

# 10カテゴリ（タスク本文の分類）から導出した公開シンボル一覧。
# 判断根拠は flying_object_tracking/__init__.py のモジュール docstring を正とする。
EXPECTED_PUBLIC_SYMBOLS: frozenset[str] = frozenset(
    {
        # 座標系（types.py）
        "CoordinateFrame",
        # 受け渡し型（types.py）
        "Roi",
        "Candidate",
        "CandidateRejection",
        "CameraPoint",
        "PointFailure",
        "TrackPoint",
        "CameraTrack",
        "TrackUpdate",
        # 追跡状態・終了理由・入力元種別（CameraTrack のフィールド型。types.py）
        "TrackState",
        "TrackEndReason",
        "SourceKind",
        # 候補と失敗の理由（types.py）
        "RejectReason",
        "PointFailureReason",
        # 検出方式と生成口（types.py / detection/detector.py）
        "DetectorKind",
        "Detector",
        "create_detector",
        "DetectResult",
        # 設定（config.py）
        "TrackingSettings",
        "RoiConfig",
        "ObjectModelConfig",
        "DetectorConfig",
        "ProjectionConfig",
        "TrackerConfig",
        "MeasurementConfig",
        # パイプラインと実行関数（pipeline.py）
        "TrackingPipeline",
        "CaughtExceptionCounters",
        "track_source",
        # 計測（metrics.py）
        "TrackingMetrics",
        "TrackingCounters",
        "EffectiveWindow",
        # 比較結果（bench/compare.py / bench/overhead.py）
        "DetectorRunResult",
        "DetectorDecision",
        "DetectorComparisonResult",
        "compare_detectors",
        "OverheadResult",
        "OverheadVerdict",
        "OverheadReport",
        "OverheadBench",
        # 例外（errors.py）
        "TrackingError",
        "TrackingConfigError",
        "IntrinsicsUnavailableError",
        "DetectorUnavailableError",
        # 受け渡し形式の版番号（types.py）
        "HANDOFF_VERSION",
    }
)

#: `flying_object_tracking` パッケージ直下に実在するトップレベルサブモジュール名。
#: `sensing_foundation` の同名テストと同じ理由（Python の import 機構が
#: `import flying_object_tracking.detection` 等を1度でも実行すると、
#: パッケージオブジェクトへ副作用としてサブモジュール属性を束縛するため、
#: `dir()` ベースの「漏れ export」検査ではこれらを除外する必要がある）。
INTERNAL_SUBMODULE_NAMES: frozenset[str] = frozenset(
    {
        "errors",
        "types",
        "config",
        "metrics",
        "detection",
        "projection",
        "tracking",
        "pipeline",
        "bench",
        "cli",
        # `from __future__ import annotations` により `dir()` に現れる
        # 言語機能オブジェクト。公開シンボルではない。
        "annotations",
    }
)

#: 公開してはならないことを明示的に固定する内部シンボル群
#: （`flying_object_tracking/__init__.py` モジュール docstring
#: 「含めなかったもの」の判断根拠と対応する）。
NOT_PUBLIC_SYMBOLS: frozenset[str] = frozenset(
    {
        # Detector の唯一の具象実装（生成口 create_detector に一本化）
        "DefaultDetector",
        # 検出方式の内部構成（マスク生成戦略とその3実装、生成口、cv2 窓口、
        # 候補化ヘルパー）
        "MaskBuilder",
        "create_mask_builder",
        "DepthBandMask",
        "FrameDiffMask",
        "BackgroundMask",
        "crop_roi",
        "clean_mask",
        "components",
        "Component",
        "CandidateExtractor",
        "CandidateExtractionResult",
        # 逆投影・追跡の内部実装（TrackingPipeline に統合されており単体では公開しない）
        "PointEstimator",
        "SingleObjectTracker",
        # 比較（compare_detectors）の内部詳細
        "DECISION_CRITERION",
        "decide",
        "parameter_count",
        "write_comparison_result",
        "comparison_output_path",
        # 計測 ON/OFF 比較（OverheadBench）の内部詳細
        "CRITERION_TEXT",
        "compute_verdict",
        "overhead_output_path",
        "write_overhead_result",
        # CLI（コマンドとして使う。公開入口には出さない）
        "main",
    }
)


def test_all_matches_expected_public_symbols_exactly() -> None:
    """`__all__` が本タスクで確定した公開シンボル一覧と完全一致する（多くも少なくもない）。"""
    assert set(flying_object_tracking.__all__) == EXPECTED_PUBLIC_SYMBOLS


def test_all_has_no_duplicate_entries() -> None:
    """`__all__` に重複が無い（完全一致だけでは重複+別の欠落の相殺を見逃すため）。"""
    assert len(flying_object_tracking.__all__) == len(set(flying_object_tracking.__all__))


def test_every_symbol_in_all_is_importable_via_getattr() -> None:
    """`__all__` に列挙された各名前が `flying_object_tracking` から実際に取得できる。"""
    for name in flying_object_tracking.__all__:
        assert hasattr(flying_object_tracking, name), (
            f"{name} が flying_object_tracking に存在しない"
        )


def test_every_symbol_in_all_is_importable_via_from_import() -> None:
    """`from flying_object_tracking import <name>` の形で全シンボルが import できる。"""
    import importlib

    module = importlib.import_module("flying_object_tracking")
    for name in flying_object_tracking.__all__:
        getattr(module, name)


def test_namespace_has_no_unintended_public_leaks() -> None:
    """`dir(flying_object_tracking)` のうち非 dunder・非内部サブモジュール名が `__all__` と一致する。

    ここに `__all__` に無い名前が余分に現れる場合、意図せず内部実装を
    re-export してしまっている（漏れ export）ことを意味する。
    """
    visible_names = {
        name
        for name in dir(flying_object_tracking)
        if not name.startswith("_") and name not in INTERNAL_SUBMODULE_NAMES
    }
    assert visible_names == EXPECTED_PUBLIC_SYMBOLS


def test_internal_implementation_symbols_are_not_public() -> None:
    """内部実装（検出方式の内部構成・PointEstimator・SingleObjectTracker・
    判定規則の内部詳細・CLI の中身等）が、いずれも `flying_object_tracking`
    の属性として一切現れない（`__all__` に無いだけでなく `hasattr` でも
    取得できないことまで固定する）。
    """
    assert NOT_PUBLIC_SYMBOLS.isdisjoint(set(flying_object_tracking.__all__))
    for name in NOT_PUBLIC_SYMBOLS:
        assert not hasattr(flying_object_tracking, name), (
            f"{name} は公開してはならないが flying_object_tracking から到達できる"
        )


def test_detection_bench_cli_not_imported_by_init_module_source() -> None:
    """`__init__.py` 自身のソースは検出の内部構成（`detection.masks` /
    `detection.mask_ops` / `detection.candidates`）・`projection` /
    `tracking`（`TrackingPipeline` に統合済みの内部実装）・`cli` へ直接
    import しないことを、実行順序に依存しない静的解析で固定する
    （`sensing_foundation` の同名テストと同じ流儀）。`flying_object_tracking.
    detection.detector`（`Detector` / `DetectResult` / `create_detector` の
    出所。検出方式の生成口そのものであり公開契約の一部）は対象外とする。
    """
    init_path = Path(flying_object_tracking.__file__).resolve()
    tree = ast.parse(init_path.read_text(encoding="utf-8"))

    forbidden_prefixes = (
        "flying_object_tracking.detection.masks",
        "flying_object_tracking.detection.mask_ops",
        "flying_object_tracking.detection.candidates",
        "flying_object_tracking.projection",
        "flying_object_tracking.tracking",
        "flying_object_tracking.cli",
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


def test_create_detector_is_the_only_public_construction_entry_point() -> None:
    """`create_detector` が公開されている一方、`Detector` の唯一の具象実装
    `DefaultDetector` は公開されない（検出器の生成が生成口へ一本化されている
    ことの直接確認。要件 4.2）。
    """
    assert "create_detector" in flying_object_tracking.__all__
    assert "DefaultDetector" not in flying_object_tracking.__all__
    assert not hasattr(flying_object_tracking, "DefaultDetector")


def test_compare_and_overhead_result_types_are_public_without_internal_helpers() -> None:
    """比較結果型（`DetectorComparisonResult` / `OverheadReport`）と実行口
    （`compare_detectors` / `OverheadBench`）は公開される一方、判定規則の
    純粋関数・パラメータ数計算・JSON 書き出しヘルパーは公開されない
    （結果型自身が判定基準の説明文を内包するため、下流はそれらを別途
    呼ぶ必要が無い。design.md「DetectorComparison / 比較結果」節）。
    """
    for name in (
        "DetectorRunResult",
        "DetectorDecision",
        "DetectorComparisonResult",
        "compare_detectors",
        "OverheadResult",
        "OverheadVerdict",
        "OverheadReport",
        "OverheadBench",
    ):
        assert name in flying_object_tracking.__all__

    for name in (
        "decide",
        "parameter_count",
        "DECISION_CRITERION",
        "write_comparison_result",
        "comparison_output_path",
        "compute_verdict",
        "CRITERION_TEXT",
        "overhead_output_path",
        "write_overhead_result",
    ):
        assert name not in flying_object_tracking.__all__
        assert not hasattr(flying_object_tracking, name)


def test_caught_exception_counters_is_public_as_pipeline_return_type() -> None:
    """`CaughtExceptionCounters` は `TrackingPipeline.caught_exceptions()` の
    戻り値型であり、これを呼ぶ下流コードが型を import できるよう公開される。
    """
    assert "CaughtExceptionCounters" in flying_object_tracking.__all__


def test_handoff_version_and_coordinate_frame_are_public() -> None:
    """`HANDOFF_VERSION` / `CoordinateFrame` は下流の結合再検証トリガーの対象
    であり（design.md「Revalidation Triggers」）、必ず公開される。
    """
    assert "HANDOFF_VERSION" in flying_object_tracking.__all__
    assert "CoordinateFrame" in flying_object_tracking.__all__


def test_init_module_source_contains_no_logic() -> None:
    """`__init__.py` が import 文と `__all__` 定義とモジュール docstring のみで構成される。

    再エクスポート専用という設計契約（design.md「Dependency Direction」表
    `__init__` 行:「再エクスポートのみ。ロジックを持たない」）を、ソース構造
    そのものから機械的に確認する（`tests/sensing_foundation/
    test_sensing_public_api.py::test_init_module_source_contains_no_logic` と
    同じ流儀）。
    """
    init_path = Path(flying_object_tracking.__file__).resolve()
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
