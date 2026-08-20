"""境界と依存ゼロの回帰テスト（要件 1.4 / 1.5 / 7.1 / 8.2 / 9.5、タスク 5.4）。

`src/prediction_core/**/*.py` の import 文を `ast` による静的解析で走査し、
以下の3点を固定する。

1. **サードパーティ実行時依存ゼロ**: 標準ライブラリの許可リスト
   （`ALLOWED_STDLIB_ROOTS`）と `prediction_core` 自身のサブモジュール以外の
   import が無いこと（design.md「Allowed Dependencies」）。
2. **依存方向の逆流が無いこと**: design.md「Dependency Direction」表
   （`DEPENDENCY_ALLOWED_TARGETS`）に無い `prediction_core` 内部辺が無いこと。
   特に `record`（L5）が `tracker`（L6）を import していないこと
   （design.md が意図的に `record` を `tracker` より下層に置いた設計判断の根幹）。
3. **ファイル入出力・ネットワーク・ロギング基盤への依存が無いこと**:
   `logging` / `socket` / `urllib` / `http` / `pathlib` / `os.path` の import、
   および `open(...)` 呼び出しが無いこと（要件 8.2 / 9.5）。

タスク 1.2 からの申し送り（要件 10.5）として、`units.py` 以外の裸の単位換算
係数リテラル（`1000` / `1000.0` / `1e6` / `1e-6`）の禁止も検査する。

**5.1 の申し送りに従い、`import prediction_core` は行わない**（`__init__.py`
がパッケージ全体を import してしまい、個別モジュールの実行時依存を独立検証
できなくなるため）。本ファイルは常にソースファイルをテキストとして読み、
`ast.parse` で静的解析するのみで、`prediction_core` を一切 import しない。

**観測可能な完了状態（タスク文言どおり）**: 「許可外の import や依存方向の
逆流を故意に入れると失敗し、現状のコードでは成功するテストが通る」ことを、
本物のプロダクションファイルを書き換える代わりに、検査ロジックを関数として
切り出し、その関数へ「違反を含む架空のソース文字列」を渡すテストケース群
（`test_detects_*` 系）と、「現状の実コード」を渡すテストケース群の両方で
証明する。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src" / "prediction_core"


def _source_files() -> dict[str, Path]:
    """`src/prediction_core/*.py` を `{モジュール名: パス}` で返す。

    `tests/` 配下は対象に含めない（`tests/prediction_core/analytic.py` は
    `random` を使うなど、プロダクションコードの制約を受けないため）。
    """
    return {path.stem: path for path in sorted(SRC_DIR.glob("*.py"))}


SOURCE_FILES: dict[str, Path] = _source_files()


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
    import を作らない（`types.py` の `PredictionConfig` 参照。design.md
    「`config` フィールドの型注釈」）。誤検知を避けるため、このブロックの
    `body` は意図的に走査対象から外す（`orelse` は通常どおり走査する）。
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

    `ast.parse` による静的解析のみで、`source` を実行しない（5.1 の申し送り
    どおり、実行時 import を伴わない検査）。
    """
    tree = ast.parse(source)
    collector = _RuntimeImportCollector()
    collector.visit(tree)
    return collector.imports


# ---------------------------------------------------------------------------
# 1. サードパーティ実行時依存ゼロ
# ---------------------------------------------------------------------------

ALLOWED_STDLIB_ROOTS: frozenset[str] = frozenset(
    {
        "__future__",
        "dataclasses",
        "enum",
        "math",
        "json",
        "time",
        "sys",
        "typing",
        "collections",
    }
)
"""design.md「Allowed Dependencies」（`dataclasses` / `enum` / `math` / `json` /
`time` / `sys` / `typing`）に加え、実装が実際に使う標準ライブラリの
トップレベルモジュール名。`collections` は `collections.abc.Sequence` /
`Mapping`（`fitting.py` / `predictor.py` / `record.py`）のために追加する。
`__future__` は全モジュールが用いる `from __future__ import annotations` のため。
"""


def find_disallowed_third_party_imports(source: str) -> list[str]:
    """標準ライブラリ許可リスト外・`prediction_core` 自身以外の import を検出する。

    違反を人間可読な文字列（`"モジュール名 (line N)"`）の列として返す。
    空列であれば違反なし。`if TYPE_CHECKING:` 内の import は
    `collect_runtime_imports` の時点で除外済み。
    """
    violations = []
    for module, lineno in collect_runtime_imports(source):
        if module.startswith("."):
            continue  # パッケージ内相対 import（本パッケージには現存しない）
        root = module.split(".")[0]
        if root == "prediction_core" or root in ALLOWED_STDLIB_ROOTS:
            continue
        violations.append(f"{module} (line {lineno})")
    return violations


def test_source_files_enumeration_matches_expected_modules() -> None:
    """`SOURCE_FILES` が想定どおりの10モジュールを走査対象にしている。

    ここがずれると、以降の全チェックが一部モジュールを見落としたまま
    「成功」してしまうため、走査対象自体を固定する。
    """
    assert set(SOURCE_FILES) == {
        "__init__",
        "config",
        "errors",
        "fitting",
        "impact",
        "predictor",
        "record",
        "tracker",
        "types",
        "units",
    }


def test_no_third_party_runtime_imports_in_any_source_file() -> None:
    """`src/prediction_core/**` の全ファイルに許可外の import が無い。"""
    for module_name, path in SOURCE_FILES.items():
        source = path.read_text(encoding="utf-8")
        violations = find_disallowed_third_party_imports(source)
        assert violations == [], f"{module_name}.py に許可外の import: {violations}"


def test_type_checking_guarded_import_is_excluded_from_runtime_check() -> None:
    """`types.py` の `if TYPE_CHECKING: from prediction_core.config import ...` は
    実行時 import ではないため収集結果に現れない（1.4 の申し送り）。
    """
    source = SOURCE_FILES["types"].read_text(encoding="utf-8")
    imports = collect_runtime_imports(source)
    assert not any(module == "prediction_core.config" for module, _ in imports)


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


def test_type_checking_guarded_third_party_import_is_not_flagged() -> None:
    """`TYPE_CHECKING` ガード内のサードパーティ import は実行時に評価されない
    ため誤検知しないことを確認する（誤検知回避）。
    """
    fake_source = (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    import numpy\n"
    )
    assert find_disallowed_third_party_imports(fake_source) == []


# ---------------------------------------------------------------------------
# 2. 依存方向の逆流が無いこと
# ---------------------------------------------------------------------------

DEPENDENCY_ALLOWED_TARGETS: dict[str, frozenset[str]] = {
    # design.md「Dependency Direction」表（層0〜7）をそのまま許可リストとして表現する。
    "units": frozenset(),  # L0: 標準ライブラリのみ、互いに import しない
    "errors": frozenset(),  # L0: 同上
    "types": frozenset({"units"}),  # L1
    "config": frozenset({"units", "types", "errors"}),  # L2
    "fitting": frozenset({"units", "types", "config"}),  # L3
    "impact": frozenset({"units", "types"}),  # L3（config には依存しない）
    "predictor": frozenset({"units", "errors", "types", "config", "fitting", "impact"}),  # L4
    "record": frozenset(
        {"units", "errors", "types", "config", "fitting", "impact", "predictor"}
    ),  # L5（tracker は含まない = 依存方向の逆流禁止）
    "tracker": frozenset(
        {"units", "errors", "types", "config", "fitting", "impact", "predictor", "record"}
    ),  # L6
    "__init__": frozenset(
        {
            "units",
            "errors",
            "types",
            "config",
            "fitting",
            "impact",
            "predictor",
            "record",
            "tracker",
        }
    ),  # L7（再エクスポートのみ）
}


def find_dependency_direction_violations(module_name: str, source: str) -> list[str]:
    """`module_name` のソースから、依存方向表に無い `prediction_core` 内部 import を検出する。

    違反を `"module_name -> target (line N)"` の列として返す。空列であれば違反なし。
    """
    allowed = DEPENDENCY_ALLOWED_TARGETS[module_name]
    violations = []
    for module, lineno in collect_runtime_imports(source):
        if module.startswith("."):
            continue
        parts = module.split(".")
        if parts[0] != "prediction_core" or len(parts) == 1:
            continue  # 標準ライブラリ、または `import prediction_core` 自体
        target = parts[1]
        if target == module_name:
            continue  # 自己参照（実際には起こらないがガード）
        if target not in allowed:
            violations.append(f"{module_name} -> {target} (line {lineno})")
    return violations


def test_dependency_direction_respected_by_all_source_modules() -> None:
    """`src/prediction_core/**` の全ファイルが依存方向表に従う。"""
    for module_name, path in SOURCE_FILES.items():
        source = path.read_text(encoding="utf-8")
        violations = find_dependency_direction_violations(module_name, source)
        assert violations == [], f"{module_name}.py の依存方向違反: {violations}"


def test_record_does_not_import_tracker() -> None:
    """`record`（L5）が `tracker`（L6）を import していない（依存方向の逆流禁止）。

    design.md が意図的に `record` を `tracker` より下層に置いた設計判断の
    根幹であるため、依存方向表の一部としてだけでなく単独でも固定する。
    """
    source = SOURCE_FILES["record"].read_text(encoding="utf-8")
    imports = collect_runtime_imports(source)
    assert not any(
        module == "prediction_core.tracker" or module.startswith("prediction_core.tracker.")
        for module, _ in imports
    )


def test_detects_reversed_dependency_from_record_to_tracker_in_crafted_source() -> None:
    """違反ケース: `record` が `tracker` を import する架空のソースが検出される。"""
    fake_source = "from prediction_core.tracker import ThrowPredictionTracker\n"
    violations = find_dependency_direction_violations("record", fake_source)
    assert violations != []
    assert "tracker" in violations[0]


def test_detects_layer0_modules_importing_each_other_in_crafted_source() -> None:
    """違反ケース: L0 の `units` が `errors` を import する架空のソース（L0 相互 import 禁止）。"""
    fake_source = "from prediction_core.errors import PredictionCoreError\n"
    violations = find_dependency_direction_violations("units", fake_source)
    assert violations != []


def test_detects_types_importing_upper_layer_module_in_crafted_source() -> None:
    """違反ケース: `types`（L1）が `config`（L2）を実行時 import する架空のソース（上向き依存）。"""
    fake_source = "from prediction_core.config import PredictionConfig\n"
    violations = find_dependency_direction_violations("types", fake_source)
    assert violations != []


def test_detects_impact_importing_config_in_crafted_source() -> None:
    """違反ケース: `impact`（L3）が `config` を import する架空のソース
    （design.md が明示的に禁止する辺）。
    """
    fake_source = "from prediction_core.config import PredictionConfig\n"
    violations = find_dependency_direction_violations("impact", fake_source)
    assert violations != []


# ---------------------------------------------------------------------------
# 3. ファイル入出力・ネットワーク・ロギング基盤への依存が無いこと
# ---------------------------------------------------------------------------

FORBIDDEN_IMPORT_MODULES: tuple[str, ...] = (
    "logging",  # ロギング基盤（要件 8.2）
    "socket",  # ネットワーク
    "urllib",  # ネットワーク
    "http",  # ネットワーク
    "pathlib",  # ファイル入出力
    "os.path",  # ファイル入出力
)


def _matches_forbidden_module(module: str) -> str | None:
    for forbidden in FORBIDDEN_IMPORT_MODULES:
        if module == forbidden or module.startswith(forbidden + "."):
            return forbidden
    return None


def find_forbidden_infrastructure_imports(source: str) -> list[str]:
    """ファイル入出力・ネットワーク・ロギング基盤に関連する import を検出する。

    違反を `"モジュール名 (line N)"` の列として返す。空列であれば違反なし。
    """
    violations = []
    for module, lineno in collect_runtime_imports(source):
        if module.startswith("."):
            continue
        if _matches_forbidden_module(module) is not None:
            violations.append(f"{module} (line {lineno})")
    return violations


class _OpenCallFinder(ast.NodeVisitor):
    """`open(...)` 呼び出し（`ast.Call` で関数名が `open`）を検出する。"""

    def __init__(self) -> None:
        self.linenos: list[int] = []

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        if isinstance(node.func, ast.Name) and node.func.id == "open":
            self.linenos.append(node.lineno)
        self.generic_visit(node)


def find_open_calls(source: str) -> list[int]:
    """ソース文字列中の `open(...)` 呼び出し箇所の行番号一覧を返す。"""
    tree = ast.parse(source)
    finder = _OpenCallFinder()
    finder.visit(tree)
    return finder.linenos


def test_no_forbidden_infrastructure_imports_in_any_source_file() -> None:
    """`src/prediction_core/**` にファイル入出力・ネットワーク・ロギング基盤への import が無い。"""
    for module_name, path in SOURCE_FILES.items():
        source = path.read_text(encoding="utf-8")
        violations = find_forbidden_infrastructure_imports(source)
        assert violations == [], (
            f"{module_name}.py にファイルI/O・ネットワーク・ロギング基盤への import: {violations}"
        )


def test_no_open_calls_in_any_source_file() -> None:
    """`src/prediction_core/**` に `open(...)` 呼び出しが無い（ファイル入出力の実行時経路）。"""
    for module_name, path in SOURCE_FILES.items():
        source = path.read_text(encoding="utf-8")
        linenos = find_open_calls(source)
        assert linenos == [], f"{module_name}.py に open() 呼び出しがある: 行 {linenos}"


@pytest.mark.parametrize(
    "fake_source",
    [
        "import logging\n",
        "import socket\n",
        "from urllib import request\n",
        "import http.client\n",
        "from pathlib import Path\n",
        "import os.path\n",
    ],
)
def test_detects_forbidden_infrastructure_import_in_crafted_source(fake_source: str) -> None:
    """違反ケース: 禁止対象6種の import を含む架空のソースがそれぞれ検出される。"""
    violations = find_forbidden_infrastructure_imports(fake_source)
    assert violations != []


def test_detects_open_call_in_crafted_source() -> None:
    """違反ケース: `open(...)` 呼び出しを含む架空のソースが検出される。"""
    fake_source = "def f():\n    with open('x.txt') as fp:\n        return fp.read()\n"
    assert find_open_calls(fake_source) != []


def test_does_not_flag_unrelated_builtin_calls_in_crafted_source() -> None:
    """誤検知回避: `open` という名前を含まない無関係な呼び出しは検出されない。"""
    fake_source = "def f(x):\n    return len(str(x))\n"
    assert find_open_calls(fake_source) == []
    assert find_forbidden_infrastructure_imports(fake_source) == []


# ---------------------------------------------------------------------------
# 4. 裸の単位換算係数リテラルの禁止（タスク1.2からの申し送り、要件10.5）
# ---------------------------------------------------------------------------

BANNED_UNIT_CONVERSION_LITERALS: frozenset[float] = frozenset({1000, 1000.0, 1e6, 1e-6})
"""`units.py` にのみ存在してよい単位換算係数。ミリ秒<->秒の換算係数 `1000`
（`MS_PER_S`）と、その2乗である `1e6`（`1e-6`）を禁止する。int/float どちらの
形で書かれても Python では同値（`1000 == 1000.0`）なため区別しない。"""

# `predictor.py` の `_elapsed_ms` が持つ `time.perf_counter_ns() の戻り値
# （**ナノ秒**）を ms へ換算する `/ 1e6` は、design.md Predictor
# Implementation Notes が `(end - start) / 1e6` をそのまま用いると明示する
# 意図的な実装であり、predictor.py のモジュール docstring にも同じ記述がある。
# units.py が管轄するのは「距離 mm・時刻 ms」ドメインの換算
# （mm/ms<->mm/s, mm/s^2<->mm/ms^2）であり、perf_counter の時間分解能（ns）を
# ms へ変換するこの箇所は別ドメインの関心事のため、要件10.5の「他モジュールは
# 1000/1e6 を直接書かない」規律の対象外として明示的に許可する
# （プロダクションコードは変更しないため、検査側にこの例外を持たせる）。
KNOWN_NON_UNIT_DOMAIN_LITERALS: frozenset[tuple[str, int]] = frozenset({("predictor", 63)})


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
    """`units.py` 以外に裸の `1000` / `1e6` 等の換算係数が無い（要件10.5）。

    `predictor.py` の `perf_counter_ns()` 由来の `/ 1e6`（ns->ms 換算、
    units.py が管轄する mm/ms ドメインとは別の関心事）のみ、狭く明示的な
    例外として許可する。
    """
    for module_name, path in SOURCE_FILES.items():
        if module_name == "units":
            continue
        source = path.read_text(encoding="utf-8")
        found = find_banned_unit_conversion_literals(source)
        unexpected = [
            (value, lineno)
            for value, lineno in found
            if (module_name, lineno) not in KNOWN_NON_UNIT_DOMAIN_LITERALS
        ]
        assert unexpected == [], f"{module_name}.py に裸の単位換算係数リテラル: {unexpected}"


def test_known_exemption_hides_no_other_violations() -> None:
    """`predictor.py` の例外が想定した1箇所のみに限定されている（例外の悪用防止）。

    例外リストと実際に検出される違反行の集合が完全一致することを確認し、
    「例外を口実に別の違反を見逃していないか」を固定する。
    """
    source = SOURCE_FILES["predictor"].read_text(encoding="utf-8")
    found_linenos = {lineno for _, lineno in find_banned_unit_conversion_literals(source)}
    exempted_linenos = {
        lineno for module_name, lineno in KNOWN_NON_UNIT_DOMAIN_LITERALS if module_name == "predictor"
    }
    assert found_linenos == exempted_linenos


def test_known_exemption_line_is_the_perf_counter_ns_conversion() -> None:
    """例外対象の行が実際に `perf_counter_ns()` の ns->ms 換算であることを確認する。

    行番号だけに頼った例外だと、将来その行の意味がすり替わっても気づけない
    ため、行の内容そのものも固定する。
    """
    source = SOURCE_FILES["predictor"].read_text(encoding="utf-8")
    lines = source.splitlines()
    for module_name, lineno in KNOWN_NON_UNIT_DOMAIN_LITERALS:
        if module_name != "predictor":
            continue
        line_text = lines[lineno - 1]
        assert "perf_counter_ns" in line_text
        assert "1e6" in line_text


def test_units_module_itself_contains_the_conversion_literal() -> None:
    """`units.py` 自体には `MS_PER_S = 1000.0` があり検出される（除外ロジックの前提を確認）。

    これはあくまで「なぜ units.py だけを走査から除外するのか」という前提の
    正しさを固定するためのテストであり、units.py 自身は換算係数を持ってよい。
    """
    source = SOURCE_FILES["units"].read_text(encoding="utf-8")
    found = find_banned_unit_conversion_literals(source)
    assert found != []


def test_config_default_gravity_and_tolerance_are_not_false_positives() -> None:
    """既定値 9806.65（重力加速度）・1.4901161193847656e-08（相対閾値）など
    単位換算とは無関係な定数は誤検知しない（誤検知回避）。
    """
    source = SOURCE_FILES["config"].read_text(encoding="utf-8")
    assert find_banned_unit_conversion_literals(source) == []


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


def test_does_not_flag_unrelated_numeric_literals_in_crafted_source() -> None:
    """誤検知回避: 禁止対象外の数値（重力加速度・サンプル数・相対閾値相当）は検出されない。"""
    fake_source = (
        "GRAVITY_MM_S2 = 9806.65\n"
        "MIN_SAMPLES = 3\n"
        "TOL = 1.4901161193847656e-08\n"
    )
    assert find_banned_unit_conversion_literals(fake_source) == []


def test_does_not_flag_numeric_literal_mentioned_only_in_docstring() -> None:
    """誤検知回避: docstring 内に偶然 `1000` という文字列が含まれても検出されない。

    docstring は `ast.Constant` としては `str` 型であり、数値リテラルではない
    ため、テキストマッチと異なり本検査は反応しない。
    """
    fake_source = '"""この関数は1000回まで安全に呼べる。"""\ndef f() -> None:\n    return None\n'
    assert find_banned_unit_conversion_literals(fake_source) == []
