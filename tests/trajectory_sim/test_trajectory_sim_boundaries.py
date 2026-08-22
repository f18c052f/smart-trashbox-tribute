"""境界と依存ゼロの回帰テスト（要件 3.2, 3.6, 3.8, 6.8, 11.1-11.6、タスク 5.4）。

`src/trajectory_sim/*.py` の import 文を `ast` による静的解析で走査し、
`tests/prediction_core/test_boundaries.py`（上流 `prediction_core` 自身の
境界検査、design.md「Dependency Direction」冒頭が「本 Spec 側の境界は
本 Spec 側で用意する必要がある」と明示する対）と**同じ方針**で、以下の
6点を固定する。

1. **サードパーティ実行時依存ゼロ**: 標準ライブラリの許可リスト
   （`ALLOWED_STDLIB_ROOTS`）・自パッケージ（`trajectory_sim`）・上流パッケージ
   （`prediction_core`）以外の import が無いこと（design.md「Technology
   Stack」/ 要件 11.3）。ネットワーク・非同期・描画に相当する第三者
   パッケージ（`socket` 系や `matplotlib` 等）の import も、この一般規則の
   対象として検出される（要件 6.8, 11.2, 11.4 の一部）。
2. **`prediction_core` を import してよいモジュールを4つに限定**
   （`params` / `results` / `prediction_link` / `__init__`）し、物理・観測・
   運動・評価・掃引・直列化・CLI の各層が上流に一切触れないことを検査する
   （design.md「Dependency Direction」表、要件 3.2, 3.8）。
3. **上流のサブモジュール直接 import が無く、参照シンボルが公開 API
   （18シンボル）の範囲内であること**を検査する（design.md「PredictionLink」
   Validation: 「`from prediction_core.tracker import ...` のような
   サブモジュール直接 import は失敗させる」/ 要件 3.8）。
4. **ネットワーク・非同期サーバに相当するモジュールの import が無いこと**
   （`socket` / `http` / `urllib` / `asyncio`。design.md「Dependency
   Direction」に付随する `BoundaryCheck` の役割 / 要件 11.2, 11.4）。
5. **モジュール間の依存方向が design.md の表に載っている辺のみであること**
   （design.md「Dependency Direction」Mermaid グラフと層表、要件 11.5）。
6. **単位換算モジュール（`units.py`）以外に裸の換算リテラル
   （`1000` / `1000.0` / `1e6` / `1e-6`）が無いこと**（design.md「Units」/
   要件 2.6, 7.7 の回帰。タスク1.2 が `prediction_core` 側に導入した規律を
   本パッケージにも敷く）。

加えて、`pyproject.toml` の `[project] dependencies` が空のままであることを
検査する（要件 3.6, 11.3、design.md「Modified Files」: 「`[project]
dependencies` は空のまま変更しない」）。`optional-dependencies` の有無は
検査しない（design.md が明示的に対象外としている）。

**本ファイルは `prediction_core` / `trajectory_sim` のいずれも import
しない**（`import trajectory_sim` は `__init__.py` を評価してしまい、
将来タスク5.5が追加する再エクスポートも含めてパッケージ全体を読み込む
ことになり、個別モジュールの実行時依存を独立検証できなくなるため）。
常にソースファイルをテキストとして読み、`ast.parse` で静的解析するのみ
とする（上流 `test_boundaries.py` と同じ原則）。

**観測可能な完了状態（タスク文言どおり）**: 「現状のコードで全検査が通り、
違反ソースを渡した検査が確実に失敗する」ことを、本物のプロダクション
ファイルを書き換える代わりに、検査ロジックを関数として切り出し、その
関数へ「違反を含む架空のソース文字列」を渡すテストケース群
（`test_detects_*` 系）と、「現状の実コード」を渡すテストケース群の両方で
証明する。

ファイル名について: `tests/prediction_core/test_boundaries.py` と
同一のベース名（`test_boundaries.py`）を用いると、`tests/**` 配下に
`__init__.py` が無いため pytest の既定の import mode が `import file
mismatch` で失敗する（既存タスク1.1/1.2で確立した本 Spec の命名規約:
`test_trajectory_sim_*.py`）。そのためこのファイルは
`test_trajectory_sim_boundaries.py` という名前にする。
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src" / "trajectory_sim"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"


def _source_files() -> dict[str, Path]:
    """`src/trajectory_sim/*.py` を `{モジュール名: パス}` で返す。"""
    return {path.stem: path for path in sorted(SRC_DIR.glob("*.py"))}


SOURCE_FILES: dict[str, Path] = _source_files()


def test_source_files_enumeration_matches_expected_modules() -> None:
    """`SOURCE_FILES` が想定どおりの14モジュールを走査対象にしている。

    ここがずれると、以降の全チェックが一部モジュールを見落としたまま
    「成功」してしまうため、走査対象自体を固定する（上流
    `test_source_files_enumeration_matches_expected_modules` と同じ意図）。
    """
    assert set(SOURCE_FILES) == {
        "__init__",
        "__main__",
        "cli",
        "drivetrain",
        "errors",
        "evaluate",
        "observation",
        "params",
        "physics",
        "prediction_link",
        "results",
        "serialize",
        "sweep",
        "units",
    }


# ---------------------------------------------------------------------------
# 共通: 実行時 import 文の静的抽出
# ---------------------------------------------------------------------------


def _is_type_checking_test(test: ast.expr) -> bool:
    """`if TYPE_CHECKING:` ガードかどうかを判定する（`typing.TYPE_CHECKING` 形も許容）。"""
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    if isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
        return True
    return False


class _RuntimeImportCollector(ast.NodeVisitor):
    """実行時に評価される import 文だけを収集する。

    `if TYPE_CHECKING:` ブロック内の import は型検査時のみ解決され実行時
    import を作らない（`results.py` の `SweepSpec` 前方参照。design.md
    「Results」: `trajectory_sim.sweep` は本モジュールより上層のため実行時
    import を作らない）。誤検知を避けるため、このブロックの `body` は
    意図的に走査対象から外す（`orelse` は通常どおり走査する）。
    """

    def __init__(self) -> None:
        self.imports: list[tuple[str, int]] = []

    def visit_If(self, node: ast.If) -> None:  # noqa: N802 (ast.NodeVisitor 命名規約)
        if _is_type_checking_test(node.test):
            for child in node.orelse:
                self.visit(child)
            return
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            self.imports.append((alias.name, node.lineno))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        if node.level and node.level > 0:
            self.imports.append(("." * node.level + (node.module or ""), node.lineno))
        else:
            self.imports.append((node.module or "", node.lineno))


def collect_runtime_imports(source: str) -> list[tuple[str, int]]:
    """ソース文字列から実行時 import 文を `(モジュール名, 行番号)` の列として抽出する。

    `ast.parse` による静的解析のみで、`source` を実行しない。
    """
    tree = ast.parse(source)
    collector = _RuntimeImportCollector()
    collector.visit(tree)
    return collector.imports


class _FromImportDetailCollector(ast.NodeVisitor):
    """`from X import a, b` の形の import 文を `(モジュール名, シンボル名の列, 行番号)`
    として、実行時に評価されるものだけ収集する（`TYPE_CHECKING` 除外は
    `_RuntimeImportCollector` と同じ方針）。

    サブモジュール直接 import・公開シンボル外参照の検出（3節）には
    シンボル名そのものが必要であり、`collect_runtime_imports` の
    フラットな `(モジュール名, 行番号)` だけでは表現できないため、
    専用のコレクタを別途用意する。
    """

    def __init__(self) -> None:
        self.from_imports: list[tuple[str, list[str], int]] = []

    def visit_If(self, node: ast.If) -> None:  # noqa: N802
        if _is_type_checking_test(node.test):
            for child in node.orelse:
                self.visit(child)
            return
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        if node.level and node.level > 0:
            return  # 本パッケージには相対 import が現存しない
        module = node.module or ""
        names = [alias.name for alias in node.names]
        self.from_imports.append((module, names, node.lineno))


def collect_runtime_from_import_details(source: str) -> list[tuple[str, list[str], int]]:
    """ソース文字列から実行時 `from X import ...` 文を詳細付きで抽出する。"""
    tree = ast.parse(source)
    collector = _FromImportDetailCollector()
    collector.visit(tree)
    return collector.from_imports


# ---------------------------------------------------------------------------
# 1. サードパーティ実行時依存ゼロ
# ---------------------------------------------------------------------------

ALLOWED_STDLIB_ROOTS: frozenset[str] = frozenset(
    {
        "__future__",
        "argparse",
        "collections",
        "dataclasses",
        "enum",
        "hashlib",
        "itertools",
        "json",
        "math",
        "os",
        "pathlib",
        "random",
        "sys",
        "tempfile",
        "types",
        "typing",
    }
)
"""design.md「Units」/「Technology Stack」が明示する `dataclasses` / `enum` /
`math` / `json` / `random`（`random.Random`）/ `hashlib`（`blake2b`）/
`argparse` / `pathlib`（`sys` は Cli の終了コード用に必要）に加え、実装が
実際に使う標準ライブラリのトップレベルモジュール名を列挙する。

`collections` は `collections.abc.Mapping` / `Sequence`（`params.py` /
`results.py` / `drivetrain.py` / `prediction_link.py` / `serialize.py` /
`cli.py`）のために必要。`os` / `tempfile` は `serialize.py` の
アトミックなファイル書き出し（一時ファイル + `os.replace`）のために必要
（`prediction_core` とは異なり、本パッケージは design.md 上ファイル入出力
を明示的に担う。要件7.5）。`types.MappingProxyType` は不変な既定値・
集計結果のために params / results / evaluate / sweep が用いる。
`__future__` は全モジュールが用いる `from __future__ import annotations`
のため。`itertools`（`itertools.product`）は掃引の直積走査（`sweep.py`）
のため。`typing`（`get_type_hints` / `TYPE_CHECKING` / `get_args` /
`get_origin`）は params / results / cli が用いる。

`prediction_core` とは異なり、本パッケージは `pathlib` / `os.path` /
`open(...)` によるファイル入出力を design.md 上明示的に許されている
（`cli.py` の設定読み込み、`serialize.py` の結果書き出し。要件7.5,
11.2）ため、ここでは禁止しない。ネットワーク・非同期サーバへの依存
（`socket` / `http` / `urllib` / `asyncio`）だけを別途「4. 禁止された
インフラ import」として検査する。
"""


def find_disallowed_third_party_imports(source: str) -> list[str]:
    """標準ライブラリ許可リスト外・自パッケージ・上流パッケージ以外の import を検出する。

    違反を人間可読な文字列（`"モジュール名 (line N)"`）の列として返す。
    空列であれば違反なし。`if TYPE_CHECKING:` 内の import は
    `collect_runtime_imports` の時点で除外済み。

    `matplotlib` のような描画ライブラリ（要件6.8: 掃引結果の描画を自身の
    責務に含めない）も、この一般規則で自動的に検出される。専用の
    描画禁止チェックを別途持たない。
    """
    violations = []
    for module, lineno in collect_runtime_imports(source):
        if module.startswith("."):
            continue  # パッケージ内相対 import（本パッケージには現存しない）
        root = module.split(".")[0]
        if root in ("trajectory_sim", "prediction_core") or root in ALLOWED_STDLIB_ROOTS:
            continue
        violations.append(f"{module} (line {lineno})")
    return violations


def test_no_third_party_runtime_imports_in_any_source_file() -> None:
    """`src/trajectory_sim/**` の全ファイルに許可外の import が無い。"""
    for module_name, path in SOURCE_FILES.items():
        source = path.read_text(encoding="utf-8")
        violations = find_disallowed_third_party_imports(source)
        assert violations == [], f"{module_name}.py に許可外の import: {violations}"


def test_type_checking_guarded_import_is_excluded_from_runtime_check() -> None:
    """`results.py` の `if TYPE_CHECKING: from trajectory_sim.sweep import SweepSpec`
    は実行時 import ではないため収集結果に現れない。
    """
    source = SOURCE_FILES["results"].read_text(encoding="utf-8")
    imports = collect_runtime_imports(source)
    assert not any(module == "trajectory_sim.sweep" for module, _ in imports)


def test_detects_disallowed_third_party_import_in_crafted_source() -> None:
    """違反ケース: `import numpy` を含む架空のソースを検査関数へ渡すと検出される。"""
    fake_source = "import numpy as np\n"
    violations = find_disallowed_third_party_imports(fake_source)
    assert violations != []
    assert "numpy" in violations[0]


def test_detects_disallowed_third_party_from_import_in_crafted_source() -> None:
    """違反ケース: `from requests import get` を含む架空のソースが検出される。"""
    fake_source = "from requests import get\n"
    violations = find_disallowed_third_party_imports(fake_source)
    assert violations != []
    assert "requests" in violations[0]


def test_detects_plotting_library_import_in_crafted_source() -> None:
    """違反ケース: `import matplotlib.pyplot` を含む架空のソースが検出される（要件6.8）。

    掃引結果の描画専用チェックを別途持たない代わりに、一般的な
    サードパーティ許可リストの検査がこれを自動的に捕捉することを示す。
    """
    fake_source = "import matplotlib.pyplot as plt\n"
    violations = find_disallowed_third_party_imports(fake_source)
    assert violations != []
    assert "matplotlib" in violations[0]


def test_type_checking_guarded_third_party_import_is_not_flagged() -> None:
    """`TYPE_CHECKING` ガード内のサードパーティ import は実行時に評価されない
    ため誤検知しないことを確認する（誤検知回避）。
    """
    fake_source = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import numpy\n"
    assert find_disallowed_third_party_imports(fake_source) == []


# ---------------------------------------------------------------------------
# 2. `prediction_core` を import してよいモジュールを4つに限定する
# ---------------------------------------------------------------------------

ALLOWED_PREDICTION_CORE_IMPORTING_MODULES: frozenset[str] = frozenset(
    {"params", "results", "prediction_link", "__init__"}
)
"""design.md「Dependency Direction」表の `prediction_core` 列で「可」と
されている4モジュール（`params` / `results` / `prediction_link`）に加え、
`__init__`（再エクスポート層。design.md: 「可（再エクスポートしない。
型注釈のみ）」。タスク5.5で型注釈用途の import が追加され得るが、本タスク
時点では空であり、既にこの4モジュール限定に違反していない）。

これ以外のモジュール（`units` / `errors` / `physics` / `drivetrain` /
`observation` / `evaluate` / `sweep` / `serialize` / `cli` / `__main__`）は
`prediction_core` への参照を一切持ってはならない（design.md 「本設計の
最も重要な制約」/ 要件3.2: フィッティング・床面交点・残差の再実装を
防ぐための機械的な代理指標。上流を import できなければ、複製する
すらできない）。
"""


def find_prediction_core_import_violations(module_name: str, source: str) -> list[str]:
    """`module_name` が `prediction_core` を参照してよいモジュールでないのに、
    ソース中に `prediction_core` への実行時参照があれば検出する。

    違反を `"module_name: モジュール名 (line N)"` の列として返す。
    空列であれば違反なし（許可モジュールの場合は常に空列を返す）。
    """
    if module_name in ALLOWED_PREDICTION_CORE_IMPORTING_MODULES:
        return []
    violations = []
    for module, lineno in collect_runtime_imports(source):
        if module.startswith("."):
            continue
        root = module.split(".")[0]
        if root == "prediction_core":
            violations.append(f"{module_name}: {module} (line {lineno})")
    return violations


def test_prediction_core_import_confined_to_allowed_modules_in_all_source_files() -> None:
    """`src/trajectory_sim/**` の全ファイルで、`prediction_core` への参照が
    許可された4モジュールに限られている。
    """
    for module_name, path in SOURCE_FILES.items():
        source = path.read_text(encoding="utf-8")
        violations = find_prediction_core_import_violations(module_name, source)
        assert violations == [], f"{module_name}.py の prediction_core 参照違反: {violations}"


def test_allowed_modules_do_reference_prediction_core() -> None:
    """許可4モジュールのうち `params` / `results` / `prediction_link` は、
    実際に `prediction_core` を参照している（許可リストが空振りでないことの確認）。
    """
    for module_name in ("params", "results", "prediction_link"):
        source = SOURCE_FILES[module_name].read_text(encoding="utf-8")
        imports = collect_runtime_imports(source)
        assert any(module.split(".")[0] == "prediction_core" for module, _ in imports), (
            f"{module_name}.py は prediction_core を参照しているはずである"
        )


def test_detects_prediction_core_import_in_disallowed_module_crafted_source() -> None:
    """違反ケース: `physics` が `prediction_core` を import する架空のソースが検出される。"""
    fake_source = "from prediction_core import PredictionConfig\n"
    violations = find_prediction_core_import_violations("physics", fake_source)
    assert violations != []
    assert "prediction_core" in violations[0]


def test_detects_bare_prediction_core_import_in_disallowed_module_crafted_source() -> None:
    """違反ケース: `sweep` が `import prediction_core` する架空のソースが検出される。"""
    fake_source = "import prediction_core\n"
    violations = find_prediction_core_import_violations("sweep", fake_source)
    assert violations != []


# ---------------------------------------------------------------------------
# 3. 上流のサブモジュール直接 import が無く、参照シンボルが公開 API の範囲内
# ---------------------------------------------------------------------------

PUBLIC_PREDICTION_CORE_API: frozenset[str] = frozenset(
    {
        "InvalidPrediction",
        "InvalidReason",
        "Prediction",
        "PredictionConfig",
        "PredictionConfigError",
        "PredictionCoreError",
        "PredictionOutcome",
        "RecordSchemaError",
        "RecordSerializationError",
        "SCHEMA_VERSION",
        "Sample",
        "SourceKind",
        "ThrowPredictionTracker",
        "ThrowRecord",
        "TrajectoryParameters",
        "predict",
        "predictions_equivalent",
        "replay",
    }
)
"""`src/prediction_core/__init__.py` の `__all__` と一致する、公開 API の
18シンボル。ここに無い名前は内部実装として扱う（`prediction_core`
モジュール docstring: 「`__all__` に列挙されたシンボルのみが公開契約」）。
"""


def find_prediction_core_submodule_or_unknown_symbol_violations(source: str) -> list[str]:
    """`prediction_core` への参照のうち、以下の2種類の違反を検出する。

    (a) `from prediction_core.XXX import ...` や `import prediction_core.XXX`
        のようなサブモジュール直接 import（design.md「PredictionLink」
        Validation: 「サブモジュール直接 import は失敗させる」/ 要件3.8）。
    (b) `from prediction_core import ...`（トップレベルの正しい形）で
        importされたシンボルが `PUBLIC_PREDICTION_CORE_API` に含まれない
        （内部実装名の漏洩・将来のタイポ検出）。

    違反を人間可読な文字列の列として返す。空列であれば違反なし。
    `import prediction_core`（サブモジュールを伴わない裸の import）自体は
    サブモジュール参照ではないため違反にしない。
    """
    violations: list[str] = []

    for module, lineno in collect_runtime_imports(source):
        if module.startswith("."):
            continue
        if module.startswith("prediction_core."):
            violations.append(f"{module} (line {lineno})")

    for module, names, lineno in collect_runtime_from_import_details(source):
        if module == "prediction_core":
            for name in names:
                if name not in PUBLIC_PREDICTION_CORE_API:
                    violations.append(f"prediction_core.{name} (line {lineno})")
        elif module.startswith("prediction_core."):
            violations.append(f"{module} (line {lineno})")

    return violations


def test_no_prediction_core_submodule_or_unknown_symbol_in_current_source() -> None:
    """`params.py` / `results.py` / `prediction_link.py`（`prediction_core` を
    参照する唯一の3モジュール）の実際の内容に違反が無い。
    """
    for module_name in ("params", "results", "prediction_link"):
        source = SOURCE_FILES[module_name].read_text(encoding="utf-8")
        violations = find_prediction_core_submodule_or_unknown_symbol_violations(source)
        assert violations == [], f"{module_name}.py の公開API限定違反: {violations}"


def test_detects_prediction_core_submodule_from_import_in_crafted_source() -> None:
    """違反ケース(a): `from prediction_core.record import ThrowRecord` が検出される。"""
    fake_source = "from prediction_core.record import ThrowRecord\n"
    violations = find_prediction_core_submodule_or_unknown_symbol_violations(fake_source)
    assert violations != []
    assert "prediction_core.record" in violations[0]


def test_detects_prediction_core_submodule_bare_import_in_crafted_source() -> None:
    """違反ケース(a): `import prediction_core.tracker` が検出される。"""
    fake_source = "import prediction_core.tracker\n"
    violations = find_prediction_core_submodule_or_unknown_symbol_violations(fake_source)
    assert violations != []
    assert "prediction_core.tracker" in violations[0]


def test_detects_unknown_symbol_from_top_level_import_in_crafted_source() -> None:
    """違反ケース(b): `from prediction_core import _internal_helper` が検出される。"""
    fake_source = "from prediction_core import _internal_helper\n"
    violations = find_prediction_core_submodule_or_unknown_symbol_violations(fake_source)
    assert violations != []
    assert "_internal_helper" in violations[0]


def test_bare_top_level_import_of_prediction_core_itself_is_not_flagged() -> None:
    """誤検知回避: `import prediction_core`（サブモジュールを伴わない裸の import）
    自体はサブモジュール参照ではないため違反にしない。
    """
    fake_source = "import prediction_core\n"
    assert find_prediction_core_submodule_or_unknown_symbol_violations(fake_source) == []


def test_known_public_symbols_from_top_level_import_are_not_flagged() -> None:
    """誤検知回避: 公開18シンボルすべてを1つの `from prediction_core import (...)`
    でまとめて import しても違反にならない。
    """
    fake_source = "from prediction_core import (\n" + ",\n".join(
        sorted(PUBLIC_PREDICTION_CORE_API)
    ) + ",\n)\n"
    assert find_prediction_core_submodule_or_unknown_symbol_violations(fake_source) == []


# ---------------------------------------------------------------------------
# 4. ネットワーク・非同期サーバに相当するモジュールの import が無いこと
# ---------------------------------------------------------------------------

FORBIDDEN_IMPORT_MODULES: tuple[str, ...] = (
    "socket",  # ネットワーク（要件11.2, 11.4）
    "http",  # ネットワーク
    "urllib",  # ネットワーク
    "asyncio",  # 常駐・非同期サーバ（要件11.2）
)
"""design.md「Dependency Direction」に付随する `BoundaryCheck` が
`trajectory_sim` に対して禁じる4項目。`prediction_core` 自身の
`FORBIDDEN_IMPORT_MODULES`（6項目: 上記4つに加え `logging` / `pathlib` /
`os.path`）とは意図的に異なる、より短いリストである。

`trajectory_sim` は `cli.py` / `serialize.py` が design.md 上正当に
ファイル入出力を行う（要件7.5）ため、`pathlib` / `os.path` /
`open(...)` をここでは禁止しない（`prediction_core` のようにファイル
入出力そのものを一切許さない層ではない）。
"""


def _matches_forbidden_module(module: str) -> str | None:
    for forbidden in FORBIDDEN_IMPORT_MODULES:
        if module == forbidden or module.startswith(forbidden + "."):
            return forbidden
    return None


def find_forbidden_infrastructure_imports(source: str) -> list[str]:
    """ネットワーク・非同期サーバに関連する import を検出する。

    違反を `"モジュール名 (line N)"` の列として返す。空列であれば違反なし。
    """
    violations = []
    for module, lineno in collect_runtime_imports(source):
        if module.startswith("."):
            continue
        if _matches_forbidden_module(module) is not None:
            violations.append(f"{module} (line {lineno})")
    return violations


def test_no_forbidden_infrastructure_imports_in_any_source_file() -> None:
    """`src/trajectory_sim/**` にネットワーク・非同期サーバへの import が無い。"""
    for module_name, path in SOURCE_FILES.items():
        source = path.read_text(encoding="utf-8")
        violations = find_forbidden_infrastructure_imports(source)
        assert violations == [], f"{module_name}.py にネットワーク・非同期基盤への import: {violations}"


@pytest.mark.parametrize(
    "fake_source",
    [
        "import socket\n",
        "from urllib import request\n",
        "import http.client\n",
        "import asyncio\n",
    ],
)
def test_detects_forbidden_infrastructure_import_in_crafted_source(fake_source: str) -> None:
    """違反ケース: 禁止対象4種の import を含む架空のソースがそれぞれ検出される。"""
    violations = find_forbidden_infrastructure_imports(fake_source)
    assert violations != []


def test_pathlib_and_os_are_not_forbidden_for_trajectory_sim() -> None:
    """誤検知回避: `pathlib` / `os` は `trajectory_sim` では禁止されない
    （`cli.py` / `serialize.py` が正当に使う。`prediction_core` とは異なる方針）。
    """
    fake_source = "from pathlib import Path\nimport os\n"
    assert find_forbidden_infrastructure_imports(fake_source) == []


# ---------------------------------------------------------------------------
# 5. モジュール間の依存方向が design.md の表に載っている辺のみであること
# ---------------------------------------------------------------------------

DEPENDENCY_ALLOWED_TARGETS: dict[str, frozenset[str]] = {
    # design.md「Dependency Direction」表（層0〜9）をそのまま許可リストとして表現する。
    "units": frozenset(),  # L0: 標準ライブラリのみ、互いに import しない
    "errors": frozenset(),  # L0: 同上
    "params": frozenset({"units", "errors"}),  # L1
    "results": frozenset({"units", "errors", "params"}),  # L2
    "physics": frozenset({"units", "errors", "params"}),  # L3
    "drivetrain": frozenset({"units", "errors", "params"}),  # L3
    "observation": frozenset(
        {"units", "errors", "params", "results", "physics", "drivetrain"}
    ),  # L4（0〜3。design.md 表: 「0〜3（physics を含む）」）
    "prediction_link": frozenset(
        {"units", "errors", "params", "results", "drivetrain"}
    ),  # L4
    "evaluate": frozenset(
        {
            "units",
            "errors",
            "params",
            "results",
            "physics",
            "drivetrain",
            "observation",
            "prediction_link",
        }
    ),  # L5（0〜4）
    "sweep": frozenset(
        {
            "units",
            "errors",
            "params",
            "results",
            "physics",
            "drivetrain",
            "observation",
            "prediction_link",
            "evaluate",
        }
    ),  # L6（0〜5）
    "serialize": frozenset(
        {
            "units",
            "errors",
            "params",
            "results",
            "physics",
            "drivetrain",
            "observation",
            "prediction_link",
            "evaluate",
            "sweep",
        }
    ),  # L7（0〜6）
    "cli": frozenset(
        {
            "units",
            "errors",
            "params",
            "results",
            "physics",
            "drivetrain",
            "observation",
            "prediction_link",
            "evaluate",
            "sweep",
            "serialize",
        }
    ),  # L8（0〜7）
    "__main__": frozenset({"cli"}),
    "__init__": frozenset(
        {
            "units",
            "errors",
            "params",
            "results",
            "physics",
            "drivetrain",
            "observation",
            "prediction_link",
            "evaluate",
            "sweep",
            "serialize",
            "cli",
        }
    ),  # L9（0〜8。再エクスポートのみ）
}
"""design.md「Dependency Direction」の Mermaid グラフ・層表（151-194行）を
そのまま許可リストへ写した表。あるモジュールが実際に import していなくても
「許可されている」ことは問題ではない（許可リストにあるが未使用の辺は
違反ではない）。この表に無い辺だけを違反として検出する。

**design.md の文字どおりの層表からの意図的な1点の乖離**:
`prediction_link` の許可対象に `drivetrain` を加えている。design.md
151-194行の層表・Mermaid グラフはいずれも `prediction_link` の import 先を
`units` / `errors` / `params` / `results` の4つに限定しているが、同じ
design.md の PredictionLink Service Interface（920-934行）は
`PredictionTimeline.updates: tuple[TargetUpdate, ...]` を明示的に要求して
おり、`TargetUpdate` は `DrivetrainModel` コンポーネント
（`drivetrain.py`）が定義する型である（design.md 797行、要件3.7:
「逐次更新を目標へ反映」対応表: `PredictionLink, DrivetrainModel |
TargetUpdate`）。実装（`src/trajectory_sim/prediction_link.py`
`from trajectory_sim.drivetrain import TargetUpdate`、タスク2.5で導入・
既にレビュー済み・コミット済み）はこの Service Interface の要求を満たす
ためにこの edge を必要としており、`drivetrain`（L3）は `prediction_link`
（L4）より下層であるため「左から右へのみ」という依存方向の大原則
（153行）自体には違反しない。この discrepancy は design.md 自身の
層表と Service Interface 記述の不一致（設計文書側の記述ゆれ）であり、
既存の実装コードを書き換える対象ではないため、本タスクの検査はこの
既存の正当な辺を許可リストに含める（design.md 側の記述をどちらかに
統一する要否は本タスクの範囲外であり、CONCERNS として報告する）。
"""


def find_dependency_direction_violations(module_name: str, source: str) -> list[str]:
    """`module_name` のソースから、依存方向表に無い `trajectory_sim` 内部 import を検出する。

    違反を `"module_name -> target (line N)"` の列として返す。空列であれば違反なし。
    """
    allowed = DEPENDENCY_ALLOWED_TARGETS[module_name]
    violations = []
    for module, lineno in collect_runtime_imports(source):
        if module.startswith("."):
            continue
        parts = module.split(".")
        if parts[0] != "trajectory_sim" or len(parts) == 1:
            continue  # 標準ライブラリ・prediction_core、または `import trajectory_sim` 自体
        target = parts[1]
        if target == module_name:
            continue  # 自己参照（実際には起こらないがガード）
        if target not in allowed:
            violations.append(f"{module_name} -> {target} (line {lineno})")
    return violations


def test_dependency_direction_respected_by_all_source_modules() -> None:
    """`src/trajectory_sim/**` の全ファイルが依存方向表に従う。"""
    for module_name, path in SOURCE_FILES.items():
        source = path.read_text(encoding="utf-8")
        violations = find_dependency_direction_violations(module_name, source)
        assert violations == [], f"{module_name}.py の依存方向違反: {violations}"


def test_detects_reversed_dependency_from_units_to_params_in_crafted_source() -> None:
    """違反ケース: `units`（L0）が `params`（L1）を import する架空のソース（依存方向の逆流）。"""
    fake_source = "from trajectory_sim.params import ScenarioParams\n"
    violations = find_dependency_direction_violations("units", fake_source)
    assert violations != []
    assert "params" in violations[0]


def test_detects_layer0_modules_importing_each_other_in_crafted_source() -> None:
    """違反ケース: L0 の `errors` が `units` を import する架空のソース（L0 相互 import 禁止）。"""
    fake_source = "from trajectory_sim.units import mm_to_m\n"
    violations = find_dependency_direction_violations("errors", fake_source)
    assert violations != []


def test_detects_physics_importing_evaluate_in_crafted_source() -> None:
    """違反ケース: `physics`（L3）が `evaluate`（L5）を import する架空のソース
    （design.md が明示的に禁じる、上位層への飛び越し依存）。
    """
    fake_source = "from trajectory_sim.evaluate import evaluate_throw\n"
    violations = find_dependency_direction_violations("physics", fake_source)
    assert violations != []
    assert "evaluate" in violations[0]


def test_detects_serialize_importing_cli_in_crafted_source() -> None:
    """違反ケース: `serialize`（L7）が `cli`（L8）を import する架空のソース
    （出力層が入口層へ依存する逆流）。
    """
    fake_source = "from trajectory_sim.cli import main\n"
    violations = find_dependency_direction_violations("serialize", fake_source)
    assert violations != []


# ---------------------------------------------------------------------------
# 6. 単位換算モジュール以外に裸の換算リテラルが無いこと
# ---------------------------------------------------------------------------

BANNED_UNIT_CONVERSION_LITERALS: frozenset[float] = frozenset({1000, 1000.0, 1e6, 1e-6})
"""`units.py` にのみ存在してよい単位換算係数。ミリ秒<->秒・ミリメートル<->
メートルの換算係数 `1000`（`MS_PER_S` / `MM_PER_M`）と、その2乗である
`1e6`（`1e-6`）を禁止する。int/float どちらの形で書かれても Python では
同値（`1000 == 1000.0`）なため区別しない。"""


class _NumericLiteralFinder(ast.NodeVisitor):
    """禁止された裸の数値リテラル（`ast.Constant`）を検出する。"""

    def __init__(self) -> None:
        self.found: list[tuple[object, int]] = []

    def visit_Constant(self, node: ast.Constant) -> None:  # noqa: N802
        value = node.value
        if isinstance(value, bool):
            return  # bool は int のサブクラスだが対象外
        if isinstance(value, (int, float)) and value in BANNED_UNIT_CONVERSION_LITERALS:
            self.found.append((value, node.lineno))
        self.generic_visit(node)


def find_banned_unit_conversion_literals(source: str) -> list[tuple[object, int]]:
    """ソース文字列中の裸の単位換算係数リテラルを `(値, 行番号)` の列として返す。

    文字列（docstring 等）の中身は `ast.Constant` としては `str` 型になり
    `isinstance(value, (int, float))` を満たさないため、テキストマッチと違い
    docstring 内の偶然の数値文字列を誤検知しない。
    """
    tree = ast.parse(source)
    finder = _NumericLiteralFinder()
    finder.visit(tree)
    return finder.found


def test_no_bare_unit_conversion_literals_outside_units_module() -> None:
    """`units.py` 以外に裸の `1000` / `1e6` 等の換算係数が無い（要件2.6, 7.7）。"""
    for module_name, path in SOURCE_FILES.items():
        if module_name == "units":
            continue
        source = path.read_text(encoding="utf-8")
        found = find_banned_unit_conversion_literals(source)
        assert found == [], f"{module_name}.py に裸の単位換算係数リテラル: {found}"


def test_units_module_itself_contains_the_conversion_literal() -> None:
    """`units.py` 自体には `MS_PER_S = 1000.0` / `MM_PER_M = 1000.0` があり検出される
    （除外ロジックの前提を確認する。これはあくまで「なぜ units.py だけを
    走査から除外するのか」という前提の正しさを固定するためのテストであり、
    units.py 自身は換算係数を持ってよい）。
    """
    source = SOURCE_FILES["units"].read_text(encoding="utf-8")
    found = find_banned_unit_conversion_literals(source)
    assert found != []


def test_known_non_unit_domain_literals_are_not_false_positives() -> None:
    """既知の非換算リテラル（重力加速度 9806.65・許容誤差 67.5/200.0・
    ホイール式の60.0・種の導出の8バイト等）は誤検知しない（誤検知回避）。

    これらは実際に `params.py` / `sweep.py` に存在する値である
    （タスク作業者向け事前調査で確認済み）が、`BANNED_UNIT_CONVERSION_
    LITERALS` に含まれないため、上の「全ファイル走査」テストで既に
    間接的に確認されている。ここでは架空のソースで直接その意図を固定する。
    """
    fake_source = (
        "GRAVITY_MM_S2 = 9806.65\n"
        "POSITION_TOLERANCE_MM = 67.5\n"
        "RESIDUAL_SPEED_TOLERANCE_MM_S = 200.0\n"
        "WHEEL_MINUTES_PER_HOUR = 60.0\n"
        "SEED_BYTE_WIDTH = 8\n"
    )
    assert find_banned_unit_conversion_literals(fake_source) == []


def test_does_not_flag_numeric_literal_mentioned_only_in_docstring() -> None:
    """誤検知回避: docstring 内に偶然 `1000` という文字列が含まれても検出されない。

    docstring は `ast.Constant` としては `str` 型であり、数値リテラルでは
    ないため、テキストマッチと異なり本検査は反応しない。
    """
    fake_source = '"""この関数は1000回まで安全に呼べる。"""\ndef f() -> None:\n    return None\n'
    assert find_banned_unit_conversion_literals(fake_source) == []


@pytest.mark.parametrize(
    "fake_source",
    [
        "FACTOR = 1000\n",
        "FACTOR = 1000.0\n",
        "FACTOR = 1e6\n",
        "FACTOR = 1e-6\n",
        "def f(x):\n    return x * 1000\n",
    ],
)
def test_detects_bare_unit_conversion_literal_in_crafted_source(fake_source: str) -> None:
    """違反ケース: 禁止対象の数値リテラル5パターンがそれぞれ検出される。"""
    assert find_banned_unit_conversion_literals(fake_source) != []


# ---------------------------------------------------------------------------
# 7. `pyproject.toml` の `[project] dependencies` が空のままであること
# ---------------------------------------------------------------------------


def test_pyproject_dependencies_are_empty() -> None:
    """`[project] dependencies` が空のままである（要件3.6, 11.3、design.md
    「Modified Files」: 「`[project] dependencies` は空のまま変更しない」）。

    `[project.optional-dependencies]` の有無は検査しない（design.md が
    明示的に対象外としている。将来の姉妹 Spec が extras を追加してもよい）。
    """
    with PYPROJECT_PATH.open("rb") as fp:
        data = tomllib.load(fp)
    assert data["project"]["dependencies"] == []
