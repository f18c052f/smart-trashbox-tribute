"""依存境界の静的検査（要件 5.2, 5.5, 5.7, 9.3、タスク 1.6）。

`src/catch_mechanism/*.py` を `ast` で静的に走査し、design.md
「Allowed Dependencies」と「Dependency Direction」が宣言する境界を固定する。

1. **形状ライブラリ（build123d／OCCT バインディング）の import を
   `shapes` / `export` の2モジュールに限る**（design.md「Allowed Dependencies」
   表の第2行「**`shapes.py` / `export.py` に限る。**」、「Dependency Direction」の
   「`build123d` の import は **`shapes` / `export` の2モジュールに限る**」。要件 5.5）。
2. **パッケージ入口（`__init__`）から形状ライブラリへ到達しない**（design.md
   「Dependency Direction」の「`__init__` は `shapes` / `export` を import しない
   （公開 API が OCCT を要求しないため）」、`#### PublicApi` の「公開するのは
   **中核層の型と関数のみ**」。要件 5.2, 5.7, 10.3）。⚠️ これは1件の import 文の
   有無ではなく**モジュール輸入グラフ上の到達可能性**であるため、モジュール
   トップレベルの import だけを辺として辿る（関数内の遅延 import は
   `import catch_mechanism` の時点では評価されず、到達を作らない）。
3. **上流パッケージ（`prediction_core` / `trajectory_sim` 他 `src/` の兄弟
   パッケージ）を import しない**（design.md「Allowed Dependencies」表の第3行
   「`prediction_core` / `trajectory_sim` **不可**（依存方向が逆になる）」。要件 5.4）。
   ⚠️ **この検査が要件 9.3 の機械的な担保でもある。** 要件 9.3 は「跳ね返りが
   評価対象外である旨を判断の記録に明示し、**シミュレータの出力を保持の根拠と
   して用いない**」と述べる。判断の記録そのものは design.md「受け口形状の決定」
   節（決定2・決定3）が担うが、「シミュレータの出力を根拠に使っていない」ことを
   コード側で示せるのは、`catch_mechanism` が `trajectory_sim` へ一切到達しない
   ことの静的な証明——すなわち **import が存在しないこと**——である
   （`docs/decisions.md` D-9 により `trajectory_sim` は `bounce_out` をモデル外と
   しており、そこから保持の根拠を引くことは原理的にできない）。
4. **依存方向（左の層からのみ import する）に反する内部の辺が無いこと**
   （design.md「Dependency Direction」の
   `errors → params → config → {selection, tolerance, constraints, metrics}
   → shapes → export → cli`）。層表 `LAYER_ORDER` は design.md のこの1行から
   ずれていないことを `test_layer_order_matches_design_document` が突き合わせる。

加えて、実行時サードパーティ依存ゼロ（design.md「Allowed Dependencies」表の
第1行「Python 標準ライブラリ 可」／「Technology Stack」の「中核ロジック:
Python 3.11 標準ライブラリのみ ⚠️ サードパーティ依存なし」。要件 5.2, 5.6）を
`sys.stdlib_module_names` を正として検査する。⚠️ 上流 `prediction_core` /
`trajectory_sim` の境界テストは**手書きの許可リスト**を持つが、本 Spec の
design.md は許可先を「Python 標準ライブラリ」と**全体で**述べているため、
その表明に忠実な形として標準ライブラリ名の集合そのものを用いる（手書きの
リストを二重管理せず、後続タスクが新しい標準ライブラリを使うたびに本ファイルを
編集する必要も生じない）。

**検査関数をテストモジュール側に置く理由**: design.md「Directory Structure」の
`src/catch_mechanism/` は本検査のためのモジュールを挙げておらず、
`#### PublicApi` の `__all__` にも境界検査の API は無い。静的解析の補助は
出荷物の公開契約ではないため、上流2 Spec と同じくテストモジュール内に閉じる。

**本ファイルは `catch_mechanism` を import しない。** `import catch_mechanism`
は `__init__.py` を評価してしまい、「入口から形状ライブラリへ到達しない」ことを
独立に検証できなくなる（上流 `tests/prediction_core/test_boundaries.py` と
同じ原則）。常にソースをテキストとして読み、`ast.parse` で解析するのみとする。
したがって **build123d 非導入の環境でも本ファイルの全件が通る**（要件 5.7）。

**ファイル名について**: design.md「Directory Structure」は
`tests/catch_mechanism/test_boundaries.py` を挙げるが、その名前は
`tests/prediction_core/test_boundaries.py` が既に使っている。`tests/` 配下に
`__init__.py` が無くテストモジュール名がフラットな名前空間を共有するため、
同名ファイルは収集時に衝突する（`test_catch_packaging.py` 冒頭の注記を参照）。
本ディレクトリの既存ファイルと同じく `test_catch_` 接頭辞を付けて回避する。

**観測可能な完了状態（タスク文言どおり）**: 「違反を含む架空のソース文字列を
検査関数へ渡すと失敗し、現状のツリーでは成功する」ことを、実ファイルを
書き換える代わりに、検査ロジックを純関数として切り出し、`test_detects_*` 系
（架空のソース）と現ツリーを渡す系の両方で証明する。
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
SRC_DIR = SRC_ROOT / "catch_mechanism"
DESIGN_PATH = REPO_ROOT / ".kiro" / "specs" / "catch-mechanism" / "design.md"

#: 自パッケージのトップレベル名。
PACKAGE = "catch_mechanism"


def _source_files() -> dict[str, Path]:
    """`src/catch_mechanism/*.py` を `{モジュール名: パス}` で返す。

    ⚠️ **タスク 2.x〜4.x が追加する未作成のモジュールは、当然ここに現れない。**
    走査対象を「現に存在するファイル」から取り、許可表 `LAYER_ORDER` の側は
    design.md が宣言する**全モジュール**を名前で持つ。この非対称性により、
    後続タスクが `shapes.py` などを追加した瞬間から、追加作業なしに本ファイルの
    全検査が新モジュールへ適用される（`test_every_source_file_is_known_to_the_layer_table`
    が、表に無い `.py` の出現を明示的な失敗として知らせる）。
    """
    return {path.stem: path for path in sorted(SRC_DIR.glob("*.py"))}


SOURCE_FILES: dict[str, Path] = _source_files()


def _current_sources() -> dict[str, str]:
    """現ツリーの `{モジュール名: ソース文字列}`。"""
    return {name: path.read_text(encoding="utf-8") for name, path in SOURCE_FILES.items()}


# ---------------------------------------------------------------------------
# 共通: import 文の静的抽出
# ---------------------------------------------------------------------------


def _is_type_checking_test(test: ast.expr) -> bool:
    """`if TYPE_CHECKING:` ガードかどうかを判定する（`typing.TYPE_CHECKING` 形も許容）。"""
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    if isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
        return True
    return False


class _RuntimeImportCollector(ast.NodeVisitor):
    """実行時に評価される import 文を、関数の内側も含めて収集する。

    `if TYPE_CHECKING:` ブロック内の import は型検査時のみ解決され実行時 import を
    作らないため、その `body` は走査対象から外す（`orelse` は通常どおり走査する）。
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


class _ModuleLevelImportCollector(_RuntimeImportCollector):
    """**モジュールの import 時に評価される** import 文だけを収集する。

    関数（`def` / `async def`）の本体は import 時に実行されないため、走査を打ち切る。
    クラス本体は import 時に実行されるため通常どおり走査する。

    ⚠️ この区別が design.md「Dependency Direction」の「`cli` は `shapes` / `export`
    を**関数内で遅延 import** し、未インストール時に専用の失敗を返す」を意味の
    あるものにする。遅延 import は `import catch_mechanism.cli` を
    `import build123d` へ到達させないからである。
    """

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        return


def collect_runtime_imports(source: str) -> list[tuple[str, int]]:
    """ソース文字列から実行時 import を `(モジュール名, 行番号)` の列として抽出する。

    `ast.parse` による静的解析のみで `source` を実行しない。関数内の遅延 import も
    「実行時 import」として含む（形状ライブラリの参照範囲は、遅延であろうと
    `shapes` / `export` 以外に現れてはならないため）。
    """
    collector = _RuntimeImportCollector()
    collector.visit(ast.parse(source))
    return collector.imports


def collect_module_level_imports(source: str) -> list[tuple[str, int]]:
    """モジュール import 時に評価される import だけを抽出する（関数内は除く）。"""
    collector = _ModuleLevelImportCollector()
    collector.visit(ast.parse(source))
    return collector.imports


def _internal_target(module: str) -> str | None:
    """`catch_mechanism.<target>` 形の import から `<target>` を取り出す。

    自パッケージ内部を指さない import には `None` を返す。
    """
    parts = module.split(".")
    if parts[0] != PACKAGE or len(parts) == 1:
        return None
    return parts[1]


def test_collect_runtime_imports_sees_imports_inside_functions() -> None:
    """遅延 import も `collect_runtime_imports` には現れる（範囲限定の検査に必要）。"""
    fake_source = "def build() -> None:\n    import build123d\n"
    assert collect_runtime_imports(fake_source) == [("build123d", 2)]


def test_collect_module_level_imports_skips_imports_inside_functions() -> None:
    """遅延 import は `collect_module_level_imports` には現れない（到達可能性に必要）。"""
    fake_source = "import json\n\n\ndef build() -> None:\n    import build123d\n"
    assert collect_module_level_imports(fake_source) == [("json", 1)]


def test_collect_module_level_imports_keeps_class_body_imports() -> None:
    """クラス本体の import は import 時に評価されるため除外しない。"""
    fake_source = "class Part:\n    import build123d\n"
    assert collect_module_level_imports(fake_source) == [("build123d", 2)]


def test_type_checking_guarded_imports_are_excluded_everywhere() -> None:
    """`if TYPE_CHECKING:` 内の import はどちらの収集にも現れない。"""
    fake_source = (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    import build123d\n"
        "else:\n"
        "    import json\n"
    )
    assert ("build123d", 3) not in collect_runtime_imports(fake_source)
    assert ("json", 5) in collect_runtime_imports(fake_source)
    assert ("build123d", 3) not in collect_module_level_imports(fake_source)


# ---------------------------------------------------------------------------
# 層表: design.md「Dependency Direction」
# ---------------------------------------------------------------------------

LAYER_ORDER: tuple[frozenset[str], ...] = (
    frozenset({"errors"}),
    frozenset({"params"}),
    frozenset({"config"}),
    frozenset({"selection", "tolerance", "constraints", "metrics"}),
    frozenset({"shapes"}),
    frozenset({"export"}),
    frozenset({"cli"}),
)
"""design.md「Dependency Direction」の
`errors → params → config → {selection, tolerance, constraints, metrics}
→ shapes → export → cli` をそのままデータにしたもの。

⚠️ **同じ層のモジュール同士の import も許さない。** 設計は「各層は**左側の層から
のみ** import する」と述べており、同層は「左側」ではない（`{selection, tolerance,
constraints, metrics}` は互いに独立な兄弟であり、design.md
「Components and Interfaces」表の Key Dependencies にも兄弟間の辺は無い）。
"""

LAYERED_MODULES: frozenset[str] = frozenset().union(*LAYER_ORDER)
CORE_MODULES: frozenset[str] = frozenset().union(*LAYER_ORDER[:4])
"""標準ライブラリのみで動く中核層（`errors` 〜 `metrics`）。"""

CAD_LAYER_MODULES: frozenset[str] = frozenset({"shapes", "export"})
"""design.md「CAD 層（`cad` extra が必要）」に属するモジュール。"""

ENTRY_MODULES: frozenset[str] = frozenset({"__init__", "__main__"})
"""層の連鎖に載らない入口（design.md「Directory Structure」）。"""

PACKAGE_ENTRY = "__init__"

KNOWN_MODULES: frozenset[str] = LAYERED_MODULES | ENTRY_MODULES


def _layer_index(module_name: str) -> int:
    for index, layer in enumerate(LAYER_ORDER):
        if module_name in layer:
            return index
    raise AssertionError(f"{module_name} は層表 LAYER_ORDER に無い")


def allowed_import_targets(module_name: str) -> frozenset[str]:
    """`module_name` が import してよい自パッケージ内モジュールの集合を返す。

    - 層に属するモジュール: **厳密に左側の層**すべて（同層・右側は不可）
    - `__init__`: 中核層のみ（`shapes` / `export` / `cli` は不可。
      design.md「Dependency Direction」/「PublicApi」）
    - `__main__`: `python -m catch_mechanism` の入口であり `cli` を呼ぶため全モジュール
    """
    if module_name == PACKAGE_ENTRY:
        return CORE_MODULES
    if module_name == "__main__":
        return LAYERED_MODULES
    return frozenset().union(frozenset(), *LAYER_ORDER[: _layer_index(module_name)])


def test_layer_order_matches_design_document() -> None:
    """`LAYER_ORDER` が design.md「Dependency Direction」の1行と一致する。

    表を手で書き写している以上、design.md 側が変わったときに黙ってずれるのが
    最大の危険であるため、原文をその場で解析して突き合わせる。
    """
    lines = [
        line.strip()
        for line in DESIGN_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("errors →")
    ]
    assert len(lines) == 1, f"design.md の依存方向の行が一意でない: {lines}"
    documented = tuple(
        frozenset(name.strip() for name in tier.strip().strip("{}").split(",") if name.strip())
        for tier in lines[0].split("→")
    )
    assert documented == LAYER_ORDER


def test_every_source_file_is_known_to_the_layer_table() -> None:
    """`src/catch_mechanism/` の全 `.py` が層表に載っている。

    ⚠️ **未知の `.py` を黙って見逃さないための番人である。** 後続タスクが
    design.md に無いモジュールを足した場合、あるいは名前を変えた場合、
    ここが落ちて層表の更新を強制する（表に無いモジュールは
    `allowed_import_targets` が扱えず、依存方向の検査が素通りしてしまう）。
    """
    unknown = sorted(set(SOURCE_FILES) - KNOWN_MODULES)
    assert unknown == [], (
        f"層表 LAYER_ORDER / ENTRY_MODULES に無いモジュール: {unknown}。"
        " design.md「Dependency Direction」へ位置づけを追記し、本ファイルの表も更新すること"
    )


DOCUMENTED_MODULES: frozenset[str] = frozenset(
    {
        "__init__",
        "errors",
        "params",
        "config",
        "selection",
        "tolerance",
        "constraints",
        "metrics",
        "shapes",
        "export",
        "cli",
        "__main__",
    }
)
"""design.md「Directory Structure」の `src/catch_mechanism/` が挙げる全12モジュール。

⚠️ **現ツリーに存在するかどうかとは無関係の一覧である。** タスク 2.x〜4.1 が
書く予定のモジュールも最初から名前で持つ。
"""


def test_layer_table_names_every_module_declared_by_the_design() -> None:
    """層表が design.md「Directory Structure」の全12モジュールをちょうど名前で持つ。

    ⚠️ **この主張は「まだ書かれていない」ことに依存しない。** 現ツリーが4
    モジュールでも、本 Spec が完成して12モジュール揃っても等しく成り立つ
    （`set(SOURCE_FILES)` との真部分集合関係を主張すると、設計どおりの完成形が
    境界違反として報告され、後続タスクの実装者に本ファイルの編集を強いてしまう）。

    表が設計より**先を行っている**ことの実質は、次のテスト
    `test_checks_apply_to_modules_before_their_files_exist` が担う。
    """
    assert KNOWN_MODULES == DOCUMENTED_MODULES


def test_checks_apply_to_modules_before_their_files_exist() -> None:
    """検査関数は、ファイルが未作成のモジュール名に対しても機能する。

    層表が全12モジュールを名前で持つため、`shapes.py` などが追加された瞬間から
    **表の更新を待たずに**依存方向・形状ライブラリ・上流 import の各検査が
    その新モジュールへ及ぶ。ここではその性質を、実ファイルの有無に依らない形で
    固定する（許可集合が全モジュール名について引けること、CAD 層の名前が
    形状ライブラリを許され中核層の名前が許されないこと）。
    """
    for module_name in DOCUMENTED_MODULES:
        allowed_import_targets(module_name)  # 未作成でも例外を出さずに引ける
        assert find_dependency_direction_violations(module_name, "") == []
    for module_name in CAD_LAYER_MODULES:
        assert find_cad_import_violations(module_name, "import build123d\n") == []
    for module_name in DOCUMENTED_MODULES - CAD_LAYER_MODULES:
        assert find_cad_import_violations(module_name, "import build123d\n") != []


def test_allowed_import_targets_encode_the_left_only_rule() -> None:
    """許可集合が「左の層のみ」を表している（同層・右側は含まない）。"""
    assert allowed_import_targets("errors") == frozenset()
    assert allowed_import_targets("params") == frozenset({"errors"})
    assert allowed_import_targets("config") == frozenset({"errors", "params"})
    assert allowed_import_targets("tolerance") == frozenset({"errors", "params", "config"})
    assert "metrics" not in allowed_import_targets("tolerance")  # 同層は不可
    assert "shapes" in allowed_import_targets("export")
    assert "export" not in allowed_import_targets("shapes")
    assert allowed_import_targets(PACKAGE_ENTRY) == CORE_MODULES
    assert CAD_LAYER_MODULES.isdisjoint(allowed_import_targets(PACKAGE_ENTRY))
    assert "cli" not in allowed_import_targets(PACKAGE_ENTRY)


# ---------------------------------------------------------------------------
# 1. 形状ライブラリの参照範囲（要件 5.5）
# ---------------------------------------------------------------------------

CAD_IMPORT_ROOTS: frozenset[str] = frozenset({"build123d", "OCP"})
"""形状ライブラリと、その推移依存である OCCT バインディングのトップレベル名。

design.md「Allowed Dependencies」は「`build123d`（＋推移依存の OCCT
バインディング）」を1件の依存として扱う。`OCP` を直接 import すれば
`build123d` を名乗らずに同じ重い依存を持ち込めてしまうため、両方を対象にする。
"""


def find_cad_import_violations(module_name: str, source: str) -> list[str]:
    """形状ライブラリの import が `shapes` / `export` 以外に現れていないか検査する。

    違反を `"module_name -> root (line N)"` の列として返す。空列であれば違反なし。
    関数内の遅延 import も対象に含める（参照範囲の限定は、評価時期に依らない）。
    """
    if module_name in CAD_LAYER_MODULES:
        return []
    violations: list[str] = []
    for module, lineno in collect_runtime_imports(source):
        if module.split(".")[0] in CAD_IMPORT_ROOTS:
            violations.append(f"{module_name} -> {module} (line {lineno})")
    return violations


def find_module_level_cad_imports(module_name: str, source: str) -> list[str]:
    """CAD 層をモジュールトップレベルで import していないか検査する。

    形状ライブラリ自体のトップレベル import と、CAD 層モジュール
    （`shapes` / `export`）へのトップレベル内部 import の両方を対象にする。
    ⚠️ `shapes` / `export` 自身は CAD 層の内部であり、対象から外す
    （`export` が `shapes` をトップレベル import するのは正しい形である）。
    """
    if module_name in CAD_LAYER_MODULES:
        return []
    violations: list[str] = []
    for module, lineno in collect_module_level_imports(source):
        root = module.split(".")[0]
        target = _internal_target(module)
        if root in CAD_IMPORT_ROOTS or target in CAD_LAYER_MODULES:
            violations.append(f"{module_name} -> {module} (line {lineno})")
    return violations


def test_no_cad_import_outside_shapes_and_export_in_current_tree() -> None:
    """現ツリーの全モジュールに形状ライブラリの import が無い。

    `shapes` / `export` は未作成のため、現時点では「どこにも無い」ことが正しい。
    """
    for module_name, source in _current_sources().items():
        violations = find_cad_import_violations(module_name, source)
        assert violations == [], f"{module_name}.py の形状ライブラリ参照: {violations}"


@pytest.mark.parametrize(
    ("module_name", "fake_source"),
    [
        ("params", "import build123d\n"),
        ("config", "from build123d import Cylinder\n"),
        ("__init__", "from catch_mechanism.shapes import build_parts\nimport build123d\n"),
        ("metrics", "import build123d.topology as topo\n"),
        ("cli", "import OCP\n"),
        ("tolerance", "def derive() -> None:\n    import build123d\n"),
    ],
)
def test_detects_cad_import_outside_allowed_modules_in_crafted_source(
    module_name: str, fake_source: str
) -> None:
    """違反ケース: `shapes` / `export` 以外が形状ライブラリを import する架空のソース。"""
    violations = find_cad_import_violations(module_name, fake_source)
    assert violations != [], f"{module_name} の形状ライブラリ import を検出できていない"
    assert module_name in violations[0]


@pytest.mark.parametrize("module_name", sorted(CAD_LAYER_MODULES))
def test_cad_import_inside_shapes_and_export_is_not_flagged(module_name: str) -> None:
    """`shapes` / `export` の形状ライブラリ import は違反ではない（誤検知回避）。"""
    fake_source = "from build123d import Cylinder, Mesher\nimport OCP\n"
    assert find_cad_import_violations(module_name, fake_source) == []


def test_type_checking_guarded_cad_import_is_not_flagged() -> None:
    """`TYPE_CHECKING` ガード内の形状ライブラリ参照は実行時 import を作らない。"""
    fake_source = (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from build123d import Part\n"
    )
    assert find_cad_import_violations("metrics", fake_source) == []


def test_detects_module_level_cad_layer_import_from_cli_in_crafted_source() -> None:
    """違反ケース: `cli` が `shapes` をトップレベル import する架空のソース。

    design.md「Dependency Direction」:「`cli` は `shapes` / `export` を**関数内で
    遅延 import** し、未インストール時に専用の失敗を返す」。
    """
    fake_source = "from catch_mechanism.shapes import build_parts\n"
    violations = find_module_level_cad_imports("cli", fake_source)
    assert violations != []
    assert "shapes" in violations[0]


def test_lazy_cad_layer_import_from_cli_is_not_flagged() -> None:
    """`cli` の関数内 `shapes` import は違反ではない（設計が要求する形）。"""
    fake_source = (
        "def cmd_build() -> int:\n"
        "    from catch_mechanism.shapes import build_parts\n"
        "    from catch_mechanism.export import export_all\n"
        "    return 0\n"
    )
    assert find_module_level_cad_imports("cli", fake_source) == []


def test_no_module_level_cad_layer_import_in_current_tree() -> None:
    """現ツリーのどのモジュールも `shapes` / `export` をトップレベル import しない。"""
    for module_name, source in _current_sources().items():
        violations = find_module_level_cad_imports(module_name, source)
        assert violations == [], f"{module_name}.py の形状レイヤ即時 import: {violations}"


# ---------------------------------------------------------------------------
# 2. パッケージ入口から形状ライブラリへ到達しない（要件 5.2, 5.7）
# ---------------------------------------------------------------------------


def find_cad_reachable_from_entry(
    sources: Mapping[str, str], entry: str = PACKAGE_ENTRY
) -> list[str]:
    """`entry` を import したときに形状ライブラリへ到達するかを検査する。

    モジュールトップレベルの内部 import だけを辺として幅優先で辿り、
    到達した先が (a) 形状ライブラリをトップレベル import している、または
    (b) CAD 層（`shapes` / `export`）そのものである場合に、到達経路を
    `"__init__ -> cli -> shapes"` の形の文字列として返す。空列であれば到達なし。

    `sources` に無いモジュールへの辺は辿れないため無視する（未作成モジュール）。
    """
    if entry not in sources:
        return []
    violations: list[str] = []
    seen = {entry}
    queue: list[tuple[str, tuple[str, ...]]] = [(entry, (entry,))]
    while queue:
        module_name, path = queue.pop(0)
        imports = collect_module_level_imports(sources[module_name])
        for module, lineno in imports:
            root = module.split(".")[0]
            if root in CAD_IMPORT_ROOTS:
                trail = " -> ".join(path)
                violations.append(f"{trail} -> {module} (line {lineno} of {module_name}.py)")
        for module, lineno in imports:
            target = _internal_target(module)
            if target is None:
                continue
            if target in CAD_LAYER_MODULES:
                trail = " -> ".join((*path, target))
                violations.append(f"{trail} (line {lineno} of {module_name}.py)")
            if target in seen or target not in sources:
                continue
            seen.add(target)
            queue.append((target, (*path, target)))
    return violations


def test_package_entry_does_not_reach_cad_in_current_tree() -> None:
    """現ツリーで `import catch_mechanism` が形状ライブラリへ到達しない（要件 5.2, 5.7）。"""
    paths = find_cad_reachable_from_entry(_current_sources())
    assert paths == [], f"入口から形状ライブラリへ到達している: {paths}"


def test_detects_entry_reaching_cad_layer_through_module_chain() -> None:
    """違反ケース: `__init__ → cli → shapes` と辿れる架空のツリー。"""
    fake_tree = {
        "__init__": "from catch_mechanism.cli import main\n",
        "cli": "from catch_mechanism.shapes import build_parts\n",
        "shapes": "import build123d\n",
    }
    paths = find_cad_reachable_from_entry(fake_tree)
    assert paths != []
    assert "shapes" in paths[0]


def test_detects_entry_reaching_cad_library_directly() -> None:
    """違反ケース: `__init__` が形状ライブラリを直接 import する架空のツリー。"""
    fake_tree = {"__init__": "import build123d\n"}
    paths = find_cad_reachable_from_entry(fake_tree)
    assert paths != []


def test_detects_entry_reaching_cad_library_through_core_module() -> None:
    """違反ケース: 中核層のモジュールが形状ライブラリを持ち込む架空のツリー。"""
    fake_tree = {
        "__init__": "from catch_mechanism.metrics import PartMetrics\n",
        "metrics": "from build123d import Part\n",
    }
    paths = find_cad_reachable_from_entry(fake_tree)
    assert paths != []
    assert "metrics" in paths[0]


def test_lazy_import_chain_does_not_count_as_reaching_cad() -> None:
    """`cli` が関数内で `shapes` を import する形なら入口からは到達しない（誤検知回避）。"""
    fake_tree = {
        "__init__": "from catch_mechanism.cli import main\n",
        "cli": "def cmd_build() -> int:\n    from catch_mechanism.shapes import build\n    return 0\n",
        "shapes": "import build123d\n",
    }
    assert find_cad_reachable_from_entry(fake_tree) == []


def test_entry_reaching_full_core_layer_is_not_flagged() -> None:
    """中核層を全部再エクスポートする入口は違反ではない（タスク 6.1 の想定形）。"""
    fake_tree = {
        "__init__": (
            "from catch_mechanism.errors import CatchMechanismError\n"
            "from catch_mechanism.params import MechanismParams\n"
            "from catch_mechanism.config import load_params\n"
            "from catch_mechanism.metrics import PartMetrics\n"
        ),
        "errors": "",
        "params": "from catch_mechanism.errors import ParameterError\n",
        "config": "import json\n",
        "metrics": "import json\n",
    }
    assert find_cad_reachable_from_entry(fake_tree) == []


# ---------------------------------------------------------------------------
# 3. 上流パッケージを import しない（要件 5.4、および要件 9.3 の機械的担保）
# ---------------------------------------------------------------------------


def _sibling_packages() -> frozenset[str]:
    """`src/` 配下の自パッケージ以外のパッケージ名。

    design.md「Allowed Dependencies」は `prediction_core` / `trajectory_sim` を
    名指しで不可としている。⚠️ **名指しの2つだけを列挙すると、後から増えた
    兄弟パッケージが素通りする**ため、`src/` の実際の内容から導く。
    """
    return frozenset(
        path.name
        for path in SRC_ROOT.iterdir()
        if path.is_dir() and path.name != PACKAGE and (path / "__init__.py").exists()
    )


FORBIDDEN_UPSTREAM_PACKAGES: frozenset[str] = _sibling_packages()


def test_forbidden_upstream_set_contains_the_packages_named_by_design() -> None:
    """導出した禁止集合が design.md の名指し2件を確かに含む。"""
    assert {"prediction_core", "trajectory_sim"} <= FORBIDDEN_UPSTREAM_PACKAGES
    assert PACKAGE not in FORBIDDEN_UPSTREAM_PACKAGES


def find_upstream_import_violations(source: str) -> list[str]:
    """他パッケージ（`prediction_core` / `trajectory_sim` 等）の import を検出する。

    違反を `"root (line N)"` の列として返す。空列であれば違反なし。
    """
    violations: list[str] = []
    for module, lineno in collect_runtime_imports(source):
        root = module.split(".")[0]
        if root in FORBIDDEN_UPSTREAM_PACKAGES:
            violations.append(f"{module} (line {lineno})")
    return violations


def test_no_upstream_package_import_in_current_tree() -> None:
    """現ツリーのどのモジュールも上流パッケージを import しない（要件 5.4, 9.3）。

    ⚠️ **`trajectory_sim` への辺が1本も無いことが、要件 9.3 の
    「シミュレータの出力を保持の根拠として用いない」の機械的な証明である。**
    保持（FR-12）の判断は design.md「受け口形状の決定」節の机上の根拠のみに
    依っており、`docs/decisions.md` D-9 でモデル外とされた跳ね返りの出力を
    参照する経路は、コード上に存在しない。
    """
    for module_name, source in _current_sources().items():
        violations = find_upstream_import_violations(source)
        assert violations == [], f"{module_name}.py の上流 import: {violations}"


@pytest.mark.parametrize(
    "fake_source",
    [
        "import trajectory_sim\n",
        "from trajectory_sim.params import DrivetrainParams\n",
        "import prediction_core.units as units\n",
        "from prediction_core import Provenance\n",
        "from sensing_foundation.geometry import Frame\n",
        "def load() -> None:\n    from trajectory_sim.results import SweepResult\n",
    ],
)
def test_detects_upstream_package_import_in_crafted_source(fake_source: str) -> None:
    """違反ケース: 上流パッケージを import する架空のソース。"""
    assert find_upstream_import_violations(fake_source) != []


def test_own_package_and_stdlib_imports_are_not_flagged_as_upstream() -> None:
    """自パッケージ・標準ライブラリの import は上流違反ではない（誤検知回避）。"""
    fake_source = "import json\nfrom catch_mechanism.params import MechanismParams\n"
    assert find_upstream_import_violations(fake_source) == []


# ---------------------------------------------------------------------------
# 4. 実行時サードパーティ依存ゼロ（要件 5.2, 5.6）
# ---------------------------------------------------------------------------


def find_third_party_import_violations(module_name: str, source: str) -> list[str]:
    """標準ライブラリ・自パッケージ以外の import を検出する。

    形状ライブラリ（`CAD_IMPORT_ROOTS`）は `shapes` / `export` に限り許可する
    （design.md「Allowed Dependencies」）。違反は `"root (line N)"` の列。
    """
    allowed = set(sys.stdlib_module_names) | {PACKAGE}
    if module_name in CAD_LAYER_MODULES:
        allowed |= CAD_IMPORT_ROOTS
    violations: list[str] = []
    for module, lineno in collect_runtime_imports(source):
        if module.startswith("."):
            continue  # 相対 import は find_relative_imports が別途禁じる
        root = module.split(".")[0]
        if root not in allowed:
            violations.append(f"{module} (line {lineno})")
    return violations


def test_no_third_party_import_in_current_tree() -> None:
    """現ツリーの全モジュールが標準ライブラリと自パッケージだけを import する。"""
    for module_name, source in _current_sources().items():
        violations = find_third_party_import_violations(module_name, source)
        assert violations == [], f"{module_name}.py の許可外 import: {violations}"


@pytest.mark.parametrize(
    "fake_source",
    ["import numpy as np\n", "from requests import get\n", "import matplotlib.pyplot as plt\n"],
)
def test_detects_third_party_import_in_crafted_source(fake_source: str) -> None:
    """違反ケース: サードパーティを import する架空のソース。"""
    assert find_third_party_import_violations("params", fake_source) != []


def test_stdlib_and_own_package_imports_are_not_flagged() -> None:
    """標準ライブラリと自パッケージは許可される（誤検知回避）。"""
    fake_source = (
        "from __future__ import annotations\n"
        "import hashlib\n"
        "import json\n"
        "from collections.abc import Mapping\n"
        "from dataclasses import dataclass\n"
        "from pathlib import Path\n"
        "from catch_mechanism.errors import ParameterError\n"
    )
    assert find_third_party_import_violations("config", fake_source) == []


def test_cad_library_is_third_party_outside_the_cad_layer() -> None:
    """形状ライブラリは CAD 層の外ではサードパーティとして扱われる。"""
    assert find_third_party_import_violations("params", "import build123d\n") != []
    assert find_third_party_import_violations("shapes", "import build123d\n") == []


# ---------------------------------------------------------------------------
# 5. 依存方向（左の層からのみ import する）
# ---------------------------------------------------------------------------


def find_dependency_direction_violations(module_name: str, source: str) -> list[str]:
    """層表に無い自パッケージ内部の辺を検出する。

    違反を `"module_name -> target (line N)"` の列として返す。空列であれば違反なし。
    関数内の遅延 import も辺として数える（遅延であっても依存方向は変わらない。
    ⚠️ `cli` は最右の層であり、`shapes` / `export` への辺は方向としては合法である。
    遅延であるべきという別の要求は `find_module_level_cad_imports` が担う）。
    """
    allowed = allowed_import_targets(module_name)
    violations: list[str] = []
    for module, lineno in collect_runtime_imports(source):
        target = _internal_target(module)
        if target is None or target == module_name:
            continue
        if target not in KNOWN_MODULES:
            violations.append(f"{module_name} -> {target} (line {lineno}; 層表に無いモジュール)")
        elif target not in allowed:
            violations.append(f"{module_name} -> {target} (line {lineno})")
    return violations


def test_dependency_direction_respected_by_current_tree() -> None:
    """現ツリーの全モジュールが依存方向に従う。"""
    for module_name, source in _current_sources().items():
        violations = find_dependency_direction_violations(module_name, source)
        assert violations == [], f"{module_name}.py の依存方向違反: {violations}"


def test_current_tree_actually_contains_internal_edges() -> None:
    """⚠️ 検査が空振りしていないこと（現ツリーに実際に内部の辺がある）。

    `params -> errors` と `config -> {errors, params}` が現に存在することを
    確かめ、「辺が1本も無いから全部通っている」状態と区別する。
    """
    sources = _current_sources()
    params_targets = {
        _internal_target(module) for module, _ in collect_runtime_imports(sources["params"])
    }
    config_targets = {
        _internal_target(module) for module, _ in collect_runtime_imports(sources["config"])
    }
    assert "errors" in params_targets
    assert {"errors", "params"} <= config_targets


@pytest.mark.parametrize(
    ("module_name", "fake_source", "expected"),
    [
        ("errors", "from catch_mechanism.params import MechanismParams\n", "params"),
        ("params", "from catch_mechanism.config import load_params\n", "config"),
        ("config", "from catch_mechanism.selection import evaluate_candidate\n", "selection"),
        ("tolerance", "from catch_mechanism.metrics import PartMetrics\n", "metrics"),
        ("constraints", "from catch_mechanism.shapes import build_parts\n", "shapes"),
        ("shapes", "from catch_mechanism.export import export_all\n", "export"),
        ("export", "import catch_mechanism.cli as cli\n", "cli"),
        ("metrics", "def load() -> None:\n    from catch_mechanism.cli import main\n", "cli"),
    ],
)
def test_detects_dependency_direction_violation_in_crafted_source(
    module_name: str, fake_source: str, expected: str
) -> None:
    """違反ケース: 同層または右側の層を import する架空のソース。"""
    violations = find_dependency_direction_violations(module_name, fake_source)
    assert violations != [], f"{module_name} -> {expected} を検出できていない"
    assert expected in violations[0]


@pytest.mark.parametrize(
    ("module_name", "fake_source"),
    [
        ("params", "from catch_mechanism.errors import ParameterError\n"),
        ("config", "from catch_mechanism.params import MechanismParams\n"),
        ("shapes", "from catch_mechanism.constraints import required_segment_count\n"),
        ("cli", "from catch_mechanism.export import export_all\n"),
        ("__init__", "from catch_mechanism.metrics import PartMetrics\n"),
        ("__main__", "from catch_mechanism.cli import main\n"),
    ],
)
def test_left_ward_imports_are_not_flagged(module_name: str, fake_source: str) -> None:
    """左側の層への import は違反ではない（誤検知回避）。"""
    assert find_dependency_direction_violations(module_name, fake_source) == []


def test_detects_public_api_importing_the_cad_layer() -> None:
    """違反ケース: `__init__` が `shapes` を import する架空のソース。

    design.md「Dependency Direction」:「`__init__` は `shapes` / `export` を
    import しない（公開 API が OCCT を要求しないため）」。
    """
    violations = find_dependency_direction_violations(
        "__init__", "from catch_mechanism.shapes import build_parts\n"
    )
    assert violations != []
    assert "shapes" in violations[0]


def test_detects_import_of_unknown_internal_module() -> None:
    """違反ケース: 層表に無い内部モジュールへの辺（表の更新漏れを検出する）。"""
    violations = find_dependency_direction_violations(
        "config", "from catch_mechanism.geometry_helpers import thing\n"
    )
    assert violations != []


# ---------------------------------------------------------------------------
# 6. 相対 import が無いこと（上の各検査が絶対 import 前提であるため）
# ---------------------------------------------------------------------------


def find_relative_imports(source: str) -> list[str]:
    """パッケージ内相対 import（`from . import x`）を検出する。

    ⚠️ 上の各検査は `catch_mechanism.<module>` という**絶対 import の形**から
    依存先を読み取る。相対 import を許すと、その形を経由せずに辺を作れてしまい、
    検査が素通りする。既存の4モジュールは絶対 import で書かれており、その規律を
    ここで固定する（`prediction_core` / `trajectory_sim` も同じ書き方である）。
    """
    return [
        f"{module} (line {lineno})"
        for module, lineno in collect_runtime_imports(source)
        if module.startswith(".")
    ]


def test_no_relative_imports_in_current_tree() -> None:
    """現ツリーに相対 import が無い（依存方向の検査に穴が開かない）。"""
    for module_name, source in _current_sources().items():
        violations = find_relative_imports(source)
        assert violations == [], f"{module_name}.py の相対 import: {violations}"


@pytest.mark.parametrize(
    "fake_source",
    ["from . import errors\n", "from .params import MechanismParams\n", "from ..pkg import x\n"],
)
def test_detects_relative_import_in_crafted_source(fake_source: str) -> None:
    """違反ケース: 相対 import を含む架空のソース。"""
    assert find_relative_imports(fake_source) != []


def test_absolute_import_is_not_flagged_as_relative() -> None:
    """絶対 import は誤検知しない。"""
    assert find_relative_imports("from catch_mechanism.errors import ParameterError\n") == []


# ---------------------------------------------------------------------------
# 7. 形状ライブラリ非導入の環境で完結すること（要件 5.7）
# ---------------------------------------------------------------------------


def test_this_module_never_imports_the_package_or_the_cad_library() -> None:
    """本ファイル自身が `catch_mechanism` も形状ライブラリも import しない。

    要件 5.7（形状生成の環境を持たない実行環境でも検査が完了する）の担保は、
    本ファイルが静的解析だけで完結していることに依る。自分自身を同じ物差しで
    測っておく。
    """
    own_source = Path(__file__).read_text(encoding="utf-8")
    roots = {module.split(".")[0] for module, _ in collect_runtime_imports(own_source)}
    assert roots.isdisjoint(CAD_IMPORT_ROOTS)
    assert PACKAGE not in roots
    assert roots <= (set(sys.stdlib_module_names) | {"pytest"})
