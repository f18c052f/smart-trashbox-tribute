"""境界と依存ゼロの回帰テスト（design.md「境界テスト（回帰）」、タスク 8.2）。

ファイル名は `test_boundaries.py` ではなく `test_sensing_boundaries.py` と
した。`tests/prediction_core/test_boundaries.py` が既に存在し、`tests/`
配下のどのディレクトリにも `__init__.py` が無いため pytest の既定 import
mode（prepend）は同名テストファイルをツリー全体で一意にしか扱えない
（タスク 1.5 Implementation Notes の申し送り、タスク 1.6/8.1 が同じ理由で
`test_sensing_config.py` / `test_sensing_cli.py` へ改名済み）。本ファイルも
同じ回避方針を踏襲する。

design.md「境界テスト（回帰）」節が挙げる7項目を、`src/prediction_core/**` を
対象にした既存の `tests/prediction_core/test_boundaries.py` と同じ流儀
（`ast` による静的解析。プロダクションコードを実行しない）で固定する。

**モジュールレベル import と関数ローカル import の区別（タスク 8.2 の核心）**:
依存方向の静的検証は `ast.Module.body` の**直下**にある `Import`/`ImportFrom`
文だけを対象にする。`source.py` の `open_source()` はレイヤー6/7の
アダプタ（`sources.simulated` / `sources.recorded` / `recording.reader`）を
関数本体の中でだけ遅延 import する（タスク 4.6 Implementation Notes が
明記する、design.md 自身の内部矛盾に対する意図的な解決策）。同様に
`sources/realsense.py` は `pyrealsense2` を関数本体の中でだけ遅延 import
する（タスク 6.1）。素朴に全 AST を走査する実装（`ast.walk` で関数本体の
中まで辿る）だとこれらの正当な遅延 import を誤って違反判定してしまうため、
依存方向チェックは**モジュールレベルのみ**を見る `collect_module_level_
internal_targets()` を使う。一方、`pyrealsense2` / `prediction_core` の
排他性チェックは関数ローカルの import も含めて検出する必要があるため、
`collect_all_imports_recursive()`（`ast.walk` によるフル走査）を使う——
この2つの収集関数の使い分けが本ファイルの設計の要点である。
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SF_SRC_DIR = REPO_ROOT / "src" / "sensing_foundation"
PC_SRC_DIR = REPO_ROOT / "src" / "prediction_core"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"


def _source_files() -> dict[str, Path]:
    """`src/sensing_foundation/**/*.py` を `{ドット区切りモジュールキー: パス}` で返す。

    サブパッケージのマーカー（`sources/__init__.py` 等）は親ディレクトリ名
    そのもの（`"sources"`）をキーにする。トップレベルの `__init__.py` は
    `"__init__"`。`sources/simulated.py` は `"sources.simulated"`。
    """
    result: dict[str, Path] = {}
    for path in sorted(SF_SRC_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel_parts = path.relative_to(SF_SRC_DIR).with_suffix("").parts
        if rel_parts[-1] == "__init__":
            key = "__init__" if len(rel_parts) == 1 else ".".join(rel_parts[:-1])
        else:
            key = ".".join(rel_parts)
        result[key] = path
    return result


SOURCE_FILES: dict[str, Path] = _source_files()


def test_source_files_enumeration_matches_expected_modules() -> None:
    """`SOURCE_FILES` が想定どおりの26モジュールを走査対象にしている。

    ここがずれると、以降の全チェックが一部モジュールを見落としたまま
    「成功」してしまうため、走査対象自体を固定する。
    """
    assert set(SOURCE_FILES) == {
        "__init__",
        "errors",
        "timebase",
        "sysstat",
        "types",
        "geometry",
        "config",
        "obslog",
        "metrics",
        "source",
        "throw_store",
        "logsummary",
        "doctor",
        "cli",
        "sources",
        "sources.simulated",
        "sources.realsense",
        "sources.recorded",
        "recording",
        "recording.layout",
        "recording.ringbuffer",
        "recording.writer",
        "recording.reader",
        "bench",
        "bench.modes",
        "bench.logging_overhead",
    }


# ---------------------------------------------------------------------------
# 1. 依存方向の静的検証（モジュールレベルの import のみを対象にする）
# ---------------------------------------------------------------------------

#: design.md「Dependency Direction」表そのままの層番号。数値が小さいほど下位。
LAYER: dict[str, int] = {
    "errors": 0,
    "timebase": 0,
    "sysstat": 0,
    "types": 1,
    "geometry": 2,
    "config": 2,
    "obslog": 3,
    "metrics": 4,
    "source": 5,
    "recording": 5,
    "recording.layout": 5,
    "recording.ringbuffer": 5,
    "recording.writer": 5,
    "recording.reader": 5,
    "sources": 6,
    "sources.simulated": 6,
    "sources.realsense": 6,
    "throw_store": 6,
    "sources.recorded": 7,
    "logsummary": 7,
    "doctor": 7,
    "bench": 8,
    "bench.modes": 8,
    "bench.logging_overhead": 8,
    "cli": 9,
    "__init__": 9,
}

assert set(LAYER) == set(SOURCE_FILES), "LAYER が SOURCE_FILES と同期していない"

#: 同一層内でも明示的に許可する辺（design.md 依存方向表の「recording/* は
#: 0〜3（source を import しない）」という1行が `recording/*` 4ファイルを
#: 束ねて書いていることの解釈: `writer.py`/`reader.py` が同じ層5内の兄弟
#: モジュール `recording/layout.py`（ファイル名規約の定数群）を参照するのは
#: 許可されるが、層5内の別モジュール `source.py` を参照するのは禁止される
#: （「記録は取得の下流でも上流でもない」という design.md の明言）。
#: 下の `LAYER` に基づく「厳密に下位の層のみ許可」という基本規則は
#: `source` <-> `recording.*` の相互禁止を自然に導出するため、この
#: 許可リストは `recording.layout` への参照という1種類の例外だけを追加する。
SAME_LAYER_EXCEPTIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("recording.writer", "recording.layout"),
        ("recording.reader", "recording.layout"),
    }
)


def collect_module_level_internal_targets(source: str) -> list[tuple[str, int]]:
    """`ast.Module.body` 直下の import 文だけから、参照している
    `sensing_foundation` 内部モジュールキーの一覧を `(キー, 行番号)` で返す。

    関数・クラス・`if`/`try` の本体の中に書かれた import 文（`open_source()`
    の遅延 import、`if TYPE_CHECKING:` ブロックなど）は対象にしない
    （モジュール docstring 参照）。標準ライブラリ・サードパーティ・
    `prediction_core` への import は対象外（別関数で扱う）。
    """
    tree = ast.parse(source)
    targets: list[tuple[str, int]] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                if name == "sensing_foundation" or name.startswith("sensing_foundation."):
                    suffix = name[len("sensing_foundation") :].lstrip(".")
                    if suffix in SOURCE_FILES:
                        targets.append((suffix, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue  # 本パッケージにパッケージ内相対 import は現存しない
            module = node.module or ""
            if module != "sensing_foundation" and not module.startswith("sensing_foundation."):
                continue
            suffix = module[len("sensing_foundation") :].lstrip(".")
            for alias in node.names:
                candidate = f"{suffix}.{alias.name}" if suffix else alias.name
                if candidate in SOURCE_FILES:
                    # 例: `from sensing_foundation.recording import layout`
                    targets.append((candidate, node.lineno))
                elif suffix in SOURCE_FILES:
                    # 例: `from sensing_foundation.config import CaptureConfig`
                    targets.append((suffix, node.lineno))
    return targets


def find_dependency_direction_violations(module_key: str, source: str) -> list[str]:
    """`module_key` のソースから、依存方向表に反するモジュールレベル import を検出する。

    違反を `"module_key -> target (line N)"` の列として返す。空列であれば違反なし。
    """
    own_layer = LAYER[module_key]
    violations: list[str] = []
    for target, lineno in collect_module_level_internal_targets(source):
        if target == module_key:
            continue
        if (module_key, target) in SAME_LAYER_EXCEPTIONS:
            continue
        target_layer = LAYER.get(target)
        if target_layer is None or target_layer >= own_layer:
            violations.append(f"{module_key} -> {target} (line {lineno})")
    return violations


def test_dependency_direction_respected_by_all_source_modules() -> None:
    """`src/sensing_foundation/**` の全ファイルが依存方向表に従う（モジュールレベルのみ）。"""
    for module_key, path in SOURCE_FILES.items():
        source = path.read_text(encoding="utf-8")
        violations = find_dependency_direction_violations(module_key, source)
        assert violations == [], f"{module_key} の依存方向違反: {violations}"


def test_detects_upward_dependency_in_crafted_source() -> None:
    """違反ケース: 層2の `geometry` が層5の `source` を import する架空のソース。"""
    fake_source = "from sensing_foundation.source import FrameSource\n"
    violations = find_dependency_direction_violations("geometry", fake_source)
    assert violations != []
    assert "source" in violations[0]


def test_detects_recording_importing_source_in_crafted_source() -> None:
    """違反ケース: `recording.writer`（層5）が同じ層5の `source` を import する。

    design.md「記録が `source` を import しないのは意図的である」という
    明言そのものを固定する（`SessionRecorder` は受動的な部品であるべき）。
    """
    fake_source = "from sensing_foundation.source import open_source\n"
    violations = find_dependency_direction_violations("recording.writer", fake_source)
    assert violations != []
    assert "source" in violations[0]


def test_recording_sibling_layout_reference_is_allowed() -> None:
    """許可ケース: `recording.writer` が同じ層5の兄弟モジュール `recording.layout` を import する。

    `SAME_LAYER_EXCEPTIONS` が正しく機能していることを固定する
    （実際の `writer.py` / `reader.py` が `from sensing_foundation.recording
    import layout` を行っている実装と対応する）。
    """
    fake_source = "from sensing_foundation.recording import layout\n"
    assert find_dependency_direction_violations("recording.writer", fake_source) == []
    assert find_dependency_direction_violations("recording.reader", fake_source) == []


def test_detects_bench_importing_cli_in_crafted_source() -> None:
    """違反ケース: 層8の `bench.modes` が層9の `cli` を import する架空のソース。"""
    fake_source = "from sensing_foundation.cli import main\n"
    violations = find_dependency_direction_violations("bench.modes", fake_source)
    assert violations != []


def test_package_marker_files_have_no_internal_imports() -> None:
    """`sources/__init__.py` / `recording/__init__.py` / `bench/__init__.py` は
    ロジックを持たないマーカーであり、`sensing_foundation` 内部モジュールを
    一切 import しない。
    """
    for key in ("sources", "recording", "bench"):
        source = SOURCE_FILES[key].read_text(encoding="utf-8")
        assert collect_module_level_internal_targets(source) == []


# ---------------------------------------------------------------------------
# 2. モジュールレベル遅延 import の除外を直接固定する回帰テスト
#    （タスク 4.6 / 6.1 の Implementation Notes が名指しするリスク）
# ---------------------------------------------------------------------------


def collect_all_imports_recursive(source: str) -> list[tuple[str, int]]:
    """`ast.walk` でソース全体（関数・クラスの本体を含む）を走査し、
    全ての `Import`/`ImportFrom` を `(モジュール文字列, 行番号)` の列として返す。

    `pyrealsense2` / `prediction_core` の排他性チェックは関数ローカルの
    import も見逃してはならないため、こちらは意図的にモジュールレベルへの
    限定を行わない（`collect_module_level_internal_targets()` とは
    正反対の網羅方針を取る）。
    """
    tree = ast.parse(source)
    collected: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                collected.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                collected.append(("." * node.level + (node.module or ""), node.lineno))
            else:
                collected.append((node.module or "", node.lineno))
    return collected


def test_open_source_lazy_adapter_imports_are_excluded_from_module_level_check() -> None:
    """`source.py` の `open_source()` が関数ローカルで行う遅延 import
    （`sources.simulated` / `sources.recorded` / `recording.reader`）は、
    モジュールレベル収集には一切現れない（タスク 4.6 Implementation Notes
    が名指しする「素朴な全 AST 走査だと誤検知する」リスクへの回帰テスト）。
    """
    source = SOURCE_FILES["source"].read_text(encoding="utf-8")
    module_level_targets = {t for t, _ in collect_module_level_internal_targets(source)}
    assert "sources.simulated" not in module_level_targets
    assert "sources.recorded" not in module_level_targets
    assert "recording.reader" not in module_level_targets

    # 対照: フル AST 走査であれば実際にこれらの import が存在することを検出できる
    # （収集関数の違いが意図した挙動差を生んでいることを固定する）。
    all_modules = {m for m, _ in collect_all_imports_recursive(source)}
    assert "sensing_foundation.sources.simulated" in all_modules
    assert "sensing_foundation.sources.recorded" in all_modules
    assert "sensing_foundation.recording.reader" in all_modules


def test_realsense_pyrealsense2_lazy_import_is_excluded_from_module_level_check() -> None:
    """`sources/realsense.py` の `pyrealsense2` 遅延 import は
    `ast.Module.body` 直下には一切現れない（タスク 6.1 の遅延 import 方針の回帰テスト）。
    """
    source = SOURCE_FILES["sources.realsense"].read_text(encoding="utf-8")
    tree = ast.parse(source)
    top_level_import_names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top_level_import_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            top_level_import_names.add(node.module or "")
    assert "pyrealsense2" not in top_level_import_names

    # 対照: フル AST 走査であれば実際に検出できる。
    assert find_pyrealsense2_imports(source) != []


# ---------------------------------------------------------------------------
# 3. `sensing_foundation`（`sources.realsense` を除く）が `pyrealsense2` を
#    import しないこと（要件 4.4 / 12.2）。フル AST 走査（関数ローカルも含む）。
# ---------------------------------------------------------------------------


def find_pyrealsense2_imports(source: str) -> list[int]:
    """ソース中の `pyrealsense2` import（モジュールレベル・関数ローカル問わず）の行番号一覧。"""
    linenos = []
    for module, lineno in collect_all_imports_recursive(source):
        if module.split(".")[0] == "pyrealsense2":
            linenos.append(lineno)
    return linenos


def test_only_sources_realsense_imports_pyrealsense2() -> None:
    """`sources/realsense.py` だけが `pyrealsense2` を import する（関数ローカル含めて）。

    `doctor.py` は SDK への問い合わせを `sources.realsense` の `probe_*()`
    関数に委ね、自身は `pyrealsense2` を import しないこと（タスク 6.2 の
    契約）もここで一緒に固定される。
    """
    for module_key, path in SOURCE_FILES.items():
        source = path.read_text(encoding="utf-8")
        linenos = find_pyrealsense2_imports(source)
        if module_key == "sources.realsense":
            assert linenos != [], "sources/realsense.py は pyrealsense2 を遅延importするはず"
        else:
            assert linenos == [], f"{module_key} が pyrealsense2 を import している: 行 {linenos}"


def test_detects_pyrealsense2_import_inside_function_body_in_crafted_source() -> None:
    """違反ケース: 関数本体の中の `import pyrealsense2` も検出される（module-level限定では見逃す種類の違反）。"""
    fake_source = "def f():\n    import pyrealsense2 as rs\n    return rs\n"
    assert find_pyrealsense2_imports(fake_source) != []


def test_import_sensing_foundation_succeeds_without_sdk() -> None:
    """SDK 非導入のこの環境で `import sensing_foundation` が成功し、
    かつ `pyrealsense2` が副作用として import されていないことを確認する
    （要件 4.4 / 12.2 の直接証拠。`sys.modules` を見て確認する）。
    """
    import importlib
    import sys

    importlib.import_module("sensing_foundation")
    assert "pyrealsense2" not in sys.modules


# ---------------------------------------------------------------------------
# 4. `types` と `throw_store` 以外が `prediction_core` を import しないこと。
#    両者とも内部モジュール（`prediction_core.record` 等）を import せず、
#    公開入口のみを参照すること。`types` は `SourceKind` のみ、`throw_store`
#    はスキーマ関連シンボルのみを参照すること（要件 7.8, 4.3）。
# ---------------------------------------------------------------------------


def find_prediction_core_imports(source: str) -> list[tuple[str, list[str], int]]:
    """ソース中の `prediction_core` 関連 import を `(モジュール文字列, 取り込み名の列, 行番号)` で返す。

    フル AST 走査（関数ローカルも含む）。`import prediction_core` 形式は
    取り込み名の列を空にする。
    """
    tree = ast.parse(source)
    results: list[tuple[str, list[str], int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "prediction_core" or alias.name.startswith("prediction_core."):
                    results.append((alias.name, [], node.lineno))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "prediction_core" or module.startswith("prediction_core."):
                results.append((module, [a.name for a in node.names], node.lineno))
    return results


def test_only_types_and_throw_store_import_prediction_core() -> None:
    """`types` と `throw_store` の2モジュール以外は `prediction_core` を一切 import しない。"""
    for module_key, path in SOURCE_FILES.items():
        source = path.read_text(encoding="utf-8")
        hits = find_prediction_core_imports(source)
        if module_key in ("types", "throw_store"):
            assert hits != [], f"{module_key} は prediction_core を import しているはず"
        else:
            assert hits == [], f"{module_key} が prediction_core を import している: {hits}"


def test_types_and_throw_store_do_not_import_prediction_core_internal_modules() -> None:
    """`types` / `throw_store` はいずれも `prediction_core` の**公開入口のみ**を参照し、
    `prediction_core.record` のような内部モジュールを直接 import しない。
    """
    for module_key in ("types", "throw_store"):
        source = SOURCE_FILES[module_key].read_text(encoding="utf-8")
        hits = find_prediction_core_imports(source)
        modules = {module for module, _, _ in hits}
        assert modules == {"prediction_core"}, (
            f"{module_key} が prediction_core の内部モジュールを import している: {modules}"
        )


def test_types_references_only_source_kind_from_prediction_core() -> None:
    """`types.py` の `prediction_core` 参照は `SourceKind` の再エクスポートのみ。"""
    source = SOURCE_FILES["types"].read_text(encoding="utf-8")
    hits = find_prediction_core_imports(source)
    names = {name for _, imported_names, _ in hits for name in imported_names}
    assert names == {"SourceKind"}


def test_throw_store_references_only_schema_symbols_from_prediction_core() -> None:
    """`throw_store.py` の `prediction_core` 参照はスキーマ関連シンボルのみであり、
    `SourceKind`（種別の再エクスポート、`types.py` の責務）には触れない。
    """
    source = SOURCE_FILES["throw_store"].read_text(encoding="utf-8")
    hits = find_prediction_core_imports(source)
    names = {name for _, imported_names, _ in hits for name in imported_names}
    assert names == {"SCHEMA_VERSION", "RecordSchemaError", "ThrowRecord"}
    assert "SourceKind" not in names


def test_detects_prediction_core_import_in_crafted_source_for_disallowed_module() -> None:
    """違反ケース: `metrics`（許可されない）が `prediction_core` を import する架空のソース。"""
    fake_source = "from prediction_core import SourceKind\n"
    hits = find_prediction_core_imports(fake_source)
    assert hits != []


def test_source_kind_is_identical_object_across_packages() -> None:
    """`sensing_foundation.SourceKind` が `prediction_core.SourceKind` と**同一オブジェクト**である
    （再定義による別オブジェクト化の回帰検出。要件 4.3）。
    """
    import prediction_core
    import sensing_foundation

    assert sensing_foundation.SourceKind is prediction_core.SourceKind


# ---------------------------------------------------------------------------
# 5. `set(sensing_foundation.__all__)` が期待リストと一致すること
#    （詳細な固定は `test_sensing_public_api.py` の責務。ここでは境界
#    テストの一部として「存在し、非空である」ことだけを軽く確認する）。
# ---------------------------------------------------------------------------


def test_sensing_foundation_all_is_a_non_empty_list() -> None:
    import sensing_foundation

    assert isinstance(sensing_foundation.__all__, list)
    assert len(sensing_foundation.__all__) > 0


# ---------------------------------------------------------------------------
# 6. `prediction_core` が `sensing_foundation` を import しないこと（要件 12.5）。
# ---------------------------------------------------------------------------


def test_prediction_core_does_not_import_sensing_foundation() -> None:
    """`src/prediction_core/**` のどのファイルも `sensing_foundation` を import しない。

    タスク 1.1 は逆方向（`sensing_foundation` が `prediction_core` を
    import してよい2モジュールに限定される）だけを扱っており、この
    逆流禁止方向のチェックは本タスクで新設する（tasks.md 8.2 の
    観測可能な完了状態が要求する検証項目）。
    """
    for path in sorted(PC_SRC_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for module, lineno in collect_all_imports_recursive(source):
            assert not module.startswith("sensing_foundation"), (
                f"{path.name} が sensing_foundation を import している"
                f"（行 {lineno}）。prediction_core は sensing_foundation に"
                "依存してはならない（要件 12.5）。"
            )


# ---------------------------------------------------------------------------
# 7. `[project].dependencies` が空のままであること、extras が許可リストに
#    収まっていること（タスク 1.1 が改訂した `tests/prediction_core/
#    test_packaging.py` の不変条件を、本 Spec の境界テストとしても固定する）。
# ---------------------------------------------------------------------------

#: タスク 1.1 で新設した許可リストと同じ値。定義元は
#: `tests/prediction_core/test_packaging.py`。同一リストを複製する理由は、
#: `tests/` 配下にテストサブパッケージ用の `__init__.py` を置けない
#: （タスク 1.5 の申し送り）ため、テストモジュール間で定数を import
#: できないからである。
ALLOWED_OPTIONAL_EXTRAS = {"sensing", "tracking", "calibration", "m1-viz"}


def _load_pyproject() -> dict:
    with PYPROJECT_PATH.open("rb") as fp:
        return tomllib.load(fp)


def test_pyproject_dependencies_stay_empty_and_extras_stay_within_allowlist() -> None:
    """`[project].dependencies == []` と `extras ⊆ ALLOWED_OPTIONAL_EXTRAS` を、
    本 Spec の境界テストとしても再確認する（`tests/prediction_core/
    test_packaging.py` が既に固定している不変条件の再検証。design.md
    「境界テスト（回帰）」項目7）。
    """
    project = _load_pyproject()["project"]
    assert project.get("dependencies", []) == []
    assert set(project.get("optional-dependencies", {})) <= ALLOWED_OPTIONAL_EXTRAS


def test_prediction_core_test_packaging_module_still_exists() -> None:
    """タスク 1.1 が改訂した `tests/prediction_core/test_packaging.py` が
    削除・改名されずに存在し続けていることを確認する（同ファイル自体の
    アサーションはこのテストの責務ではなく、`uv run pytest` のフル実行で
    別途検証される）。
    """
    path = REPO_ROOT / "tests" / "prediction_core" / "test_packaging.py"
    assert path.is_file()
