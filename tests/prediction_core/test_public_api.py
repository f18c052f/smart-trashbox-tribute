"""公開 API の確定を検証する（要件 9.7、design.md「PublicApi」）。

`prediction_core/__init__.py` は再エクスポートのみを行い、ロジックを
一切持たない契約である。本テストは以下を固定する。

- `prediction_core.__all__` が design.md「PublicApi」節に列挙された
  18 個の公開シンボル名と**完全一致**すること（多すぎても少なすぎても失敗）
- `__all__` に列挙された各名前が実際に `prediction_core` から
  import/`getattr` できること
- `dir(prediction_core)` から dunder とサブモジュール名を除いたものが
  `__all__` の集合と一致すること（意図しない漏れ export が無いこと）
- 主要シンボルが元の定義モジュールと**同一オブジェクト**であること
  （再エクスポートであり、コピーや再定義ではないことの確認）

本ファイルは `__init__.py` / `__all__` 専用のテストであり、
`test_packaging.py`（依存メタデータのみを検査、
`.kiro/specs/prediction-core/tasks.md` Implementation Notes 1.1 の申し送り
どおり `__all__` の中身までは検証しない）とは役割が異なる。
"""

from __future__ import annotations

import prediction_core

# design.md「PublicApi / Responsibilities & Constraints」に列挙された18個の
# 公開シンボル名。型チェッカーの都合上、`PredictionOutcome` は
# `Prediction | InvalidPrediction` の型エイリアスである点に注意。
EXPECTED_PUBLIC_SYMBOLS: frozenset[str] = frozenset(
    {
        # types.py
        "Sample",
        "SourceKind",
        "TrajectoryParameters",
        "Prediction",
        "InvalidPrediction",
        "InvalidReason",
        "PredictionOutcome",
        # config.py
        "PredictionConfig",
        # predictor.py
        "predict",
        # tracker.py
        "ThrowPredictionTracker",
        # record.py
        "ThrowRecord",
        "SCHEMA_VERSION",
        "replay",
        "predictions_equivalent",
        # errors.py（例外4種）
        "PredictionCoreError",
        "PredictionConfigError",
        "RecordSchemaError",
        "RecordSerializationError",
    }
)

# `prediction_core` パッケージ配下に実在する内部実装モジュール名。
# サブパッケージを import すると Python の import 機構上 `dir()` に
# 自然に現れるが、これらは公開シンボルではなく `__all__` に含めない。
INTERNAL_SUBMODULE_NAMES: frozenset[str] = frozenset(
    {
        "types",
        "config",
        "predictor",
        "tracker",
        "record",
        "errors",
        "units",
        "fitting",
        "impact",
        # `from __future__ import annotations` により `dir()` に現れる
        # 言語機能オブジェクト。公開シンボルではない。
        "annotations",
    }
)


def test_all_has_exactly_18_symbols() -> None:
    """`__all__` の要素数が 18 個ちょうどである。"""
    assert len(prediction_core.__all__) == 18


def test_all_matches_expected_public_symbols_exactly() -> None:
    """`__all__` が design.md 列挙の18シンボルと完全一致する（多くも少なくもない）。"""
    assert set(prediction_core.__all__) == EXPECTED_PUBLIC_SYMBOLS


def test_all_has_no_duplicate_entries() -> None:
    """`__all__` に重複が無い（完全一致だけでは重複+別の欠落の相殺を見逃すため）。"""
    assert len(prediction_core.__all__) == len(set(prediction_core.__all__))


def test_every_symbol_in_all_is_importable_via_getattr() -> None:
    """`__all__` に列挙された各名前が `prediction_core` から実際に取得できる。"""
    for name in prediction_core.__all__:
        assert hasattr(prediction_core, name), f"{name} が prediction_core に存在しない"


def test_every_symbol_in_all_is_importable_via_from_import() -> None:
    """`from prediction_core import <name>` の形で全シンボルが import できる。"""
    import importlib

    module = importlib.import_module("prediction_core")
    for name in prediction_core.__all__:
        # `getattr` が例外を送出しないこと自体が「import できる」ことの
        # 確認である（存在しなければ AttributeError で本テストは失敗する）。
        getattr(module, name)


def test_namespace_has_no_unintended_public_leaks() -> None:
    """`dir(prediction_core)` のうち非 dunder・非内部モジュール名が `__all__` と一致する。

    ここに `__all__` に無い名前が余分に現れる場合、意図せず内部実装を
    re-export してしまっている（漏れ export）ことを意味する。
    """
    visible_names = {
        name
        for name in dir(prediction_core)
        if not name.startswith("_") and name not in INTERNAL_SUBMODULE_NAMES
    }
    assert visible_names == EXPECTED_PUBLIC_SYMBOLS


def test_internal_submodule_names_are_not_in_all() -> None:
    """内部実装モジュール名そのものは `__all__` に含まれない。"""
    assert set(prediction_core.__all__).isdisjoint(INTERNAL_SUBMODULE_NAMES)


def test_reexported_symbols_are_identical_objects_not_copies() -> None:
    """公開シンボルが元の定義モジュールと同一オブジェクトである（再エクスポート）。"""
    from prediction_core import (
        InvalidPrediction,
        InvalidReason,
        Prediction,
        PredictionConfig,
        PredictionConfigError,
        PredictionCoreError,
        Sample,
        SourceKind,
        ThrowPredictionTracker,
        ThrowRecord,
        TrajectoryParameters,
        predict,
        predictions_equivalent,
        replay,
    )
    from prediction_core import config as config_module
    from prediction_core import errors as errors_module
    from prediction_core import predictor as predictor_module
    from prediction_core import record as record_module
    from prediction_core import tracker as tracker_module
    from prediction_core import types as types_module

    assert Sample is types_module.Sample
    assert SourceKind is types_module.SourceKind
    assert TrajectoryParameters is types_module.TrajectoryParameters
    assert Prediction is types_module.Prediction
    assert InvalidPrediction is types_module.InvalidPrediction
    assert InvalidReason is types_module.InvalidReason

    assert PredictionConfig is config_module.PredictionConfig
    assert predict is predictor_module.predict
    assert ThrowPredictionTracker is tracker_module.ThrowPredictionTracker

    assert ThrowRecord is record_module.ThrowRecord
    assert replay is record_module.replay
    assert predictions_equivalent is record_module.predictions_equivalent

    assert PredictionCoreError is errors_module.PredictionCoreError
    assert PredictionConfigError is errors_module.PredictionConfigError


def test_schema_version_reexported_with_same_value() -> None:
    """`SCHEMA_VERSION` が `record.py` と同一の値で再エクスポートされる。"""
    from prediction_core import record as record_module

    assert prediction_core.SCHEMA_VERSION == record_module.SCHEMA_VERSION
    assert prediction_core.SCHEMA_VERSION == "1.0"


def test_prediction_outcome_is_the_prediction_invalid_prediction_union() -> None:
    """`PredictionOutcome` が `Prediction | InvalidPrediction` の型エイリアスである。"""
    assert prediction_core.PredictionOutcome == (
        prediction_core.Prediction | prediction_core.InvalidPrediction
    )


def test_all_four_exceptions_are_reexported_and_subclass_prediction_core_error() -> None:
    """例外4種すべてが再エクスポートされ、`PredictionCoreError` の階層に属する。"""
    from prediction_core import (
        PredictionConfigError,
        PredictionCoreError,
        RecordSchemaError,
        RecordSerializationError,
    )

    for exc_type in (PredictionConfigError, RecordSchemaError, RecordSerializationError):
        assert issubclass(exc_type, PredictionCoreError)
        assert issubclass(exc_type, ValueError)


def test_init_module_source_contains_no_logic() -> None:
    """`__init__.py` が import 文と `__all__` 定義とモジュール docstring のみで構成される。

    再エクスポート専用という設計契約（design.md「ロジックを一切持たない」）を、
    ソース構造そのものから機械的に確認する。関数定義・クラス定義・if/for等の
    制御構文が無いことを AST で検査する。
    """
    import ast
    from pathlib import Path

    init_path = Path(prediction_core.__file__).resolve()
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
            # docstring（文字列定数）以外の式文は許可しない。
            assert isinstance(node.value, ast.Constant), (
                "__init__.py の式文は docstring のみ許可される"
            )
