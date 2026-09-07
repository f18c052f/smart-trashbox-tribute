"""依存境界の静的検査（要件 1.3、タスク 1.5）。

`src/chassis_mechanism/*.py` を `ast` で静的に走査し、design.md
「Allowed Dependencies」と「Dependency Direction」が宣言する境界を固定する。

1. **形状ライブラリ（build123d／OCCT バインディング）の import を
   `shapes` / `export` の2モジュールに限る**（design.md「Allowed Dependencies」
   表の第3行「**`shapes.py` / `export.py` に限る。**」、「Dependency Direction」の
   「`build123d` の import は **`shapes` / `export` の2モジュールに限る**」）。
2. **パッケージ入口（`__init__`）から形状ライブラリへ到達しない**（design.md
   「Dependency Direction」の「`__init__` は `shapes` / `export` を import しない
   （公開 API が OCCT を要求しないため）」、`#### PublicApi` の
   「⚠️ `__init__` は `build123d` を import しない」）。⚠️ これは1件の import 文の
   有無ではなく**モジュール輸入グラフ上の到達可能性**であるため、モジュール
   トップレベルの import だけを辺として辿る（関数内の遅延 import は
   `import chassis_mechanism` の時点では評価されず、到達を作らない）。
   design.md は続けて「上流 `catch_mechanism.__init__` も OCCT へ到達しないため、
   この性質は推移的に保たれる」と述べる。⚠️ **この推移の前提も、同じ検査関数を
   上流のソース木へ当てて確かめる**（前提が崩れれば本 Spec の主張も崩れるため、
   上流側の変更を黙って受け入れない）。
3. **上流 `catch_mechanism` へは公開入口からのみ到達する**（design.md
   「Allowed Dependencies」表の第2行「⚠️ `import catch_mechanism` /
   `from catch_mechanism import X` のみ。内部モジュール（`catch_mechanism.params`
   等）へ直接 import しない」、「Dependency Direction」の同旨）。さらに、上流の
   公開 API を import してよいモジュールは design.md の Components 節が
   `External: catch_mechanism...` を宣言した5件に限る。⚠️ **この5件は「Dependency
   Direction」の散文が挙げる4件と食い違う**——事情と扱いは
   `test_upstream_importer_set_matches_the_component_sections` に記した。
4. **兄弟パッケージ（`prediction_core` / `trajectory_sim` / `sensing_foundation`
   ほか）を import しない**（design.md「Allowed Dependencies」表の第4行
   「**不可**（依存方向が逆になる、または無関係）」、および
   「⚠️ **`chassis_mechanism` は `trajectory_sim` を import しない。** 還元は
   `configs/trajectory_sim/drivetrain-wheel60.json` の値と、それを読むだけの
   一致検査を通じて行う」）。
5. **依存方向（左の層からのみ import する）に反する内部の辺が無いこと**
   （design.md「Dependency Direction」の
   `errors → params → config → layout → {clearance, joints}
   → {assembly, baseline} → shapes → export → cli`）。層表 `LAYER_ORDER` は
   design.md のこの1行からずれていないことを
   `test_layer_order_matches_design_document` が突き合わせる。
6. **本 Spec のパラメータパスに上流のコンポーネント名が現れないこと**
   （要件 1.3「上流が公開している寸法値・造形制約・継手方針を参照して用い、
   同じ値を自身の設定ファイルへ再定義しない」、design.md `#### Params` Risks
   「⚠️ **上流が持つ値（造形可能寸法・材料・継手方針・ゴミ箱の採寸値）を1つでも
   ここへ書いたら要件 1.3 違反である**。`test_chassis_boundaries.py` が項目名の
   重複を検出する」）。⚠️ **両側とも導出する**——上流のコンポーネント名は上流の
   公開 `PARAMETER_PATHS` から、本 Spec 側は `ChassisParams` の定義（`ast`）から
   取る。どちらかを手で書き写せば、その写しが古びた時点で検査が嘘になる。

加えて、実行時サードパーティ依存ゼロ（design.md「Allowed Dependencies」表の
第1行「Python 標準ライブラリ 可」／「Technology Stack」の「中核ロジック:
Python 3.11 標準ライブラリ ＋ `catch_mechanism` の公開 API ⚠️ サードパーティ
依存なし」）を `sys.stdlib_module_names` を正として検査し、相対 import が無いこと
（上の各検査が絶対 import の形から依存先を読むため）も固定する。

**検査関数をテストモジュール側に置く理由**: design.md「Directory Structure」の
`src/chassis_mechanism/` は本検査のためのモジュールを挙げておらず、
`#### PublicApi` の公開一覧にも境界検査の API は無い。静的解析の補助は出荷物の
公開契約ではないため、上流と同じくテストモジュール内に閉じる。

**本ファイルは `chassis_mechanism` を import しない**（design.md
「Dependency Direction」の「この方向と import 制限は
`tests/chassis_mechanism/test_chassis_boundaries.py` が `ast` で静的に検査する
（上流と同じく `chassis_mechanism` を import しない）」）。`import
chassis_mechanism` は `__init__.py` を評価してしまい、「入口から形状ライブラリへ
到達しない」ことを独立に検証できなくなる。常にソースをテキストとして読み、
`ast.parse` で解析するのみとする。⚠️ **本 Spec 側のパラメータパスも例外ではない**
——`chassis_mechanism.params` を import する代わりに `ChassisParams` の定義を
`ast` で読む。

**唯一の実行時 import は上流の公開入口 `catch_mechanism` である。** これは検査の
対象ではなく検査の**入力**（上流のコンポーネント名の正）であり、上流の
`__init__` は OCCT へ到達しない（`test_upstream_entry_does_not_reach_cad` が
本ファイル内で確かめる）。したがって **build123d 非導入の環境でも本ファイルの
全件が通る**。
"""

from __future__ import annotations

import ast
import re
import sys
from collections.abc import Collection, Mapping
from pathlib import Path

import pytest

from catch_mechanism import PARAMETER_PATHS as UPSTREAM_PARAMETER_PATHS

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
DESIGN_PATH = REPO_ROOT / ".kiro" / "specs" / "chassis-mechanism" / "design.md"

#: 自パッケージのトップレベル名。
PACKAGE = "chassis_mechanism"

#: 上流パッケージのトップレベル名（公開入口経由に限り import 可）。
UPSTREAM_PACKAGE = "catch_mechanism"

SRC_DIR = SRC_ROOT / PACKAGE
UPSTREAM_SRC_DIR = SRC_ROOT / UPSTREAM_PACKAGE

#: 本 Spec の寸法パラメータの集約ルート（design.md `#### Params`）。
ROOT_PARAMS_CLASS = "ChassisParams"


def _sources_in(directory: Path) -> dict[str, str]:
    """`directory/*.py` を `{モジュール名: ソース文字列}` で返す。

    ⚠️ **タスク 2.x〜4.x が追加する未作成のモジュールは、当然ここに現れない。**
    走査対象を「現に存在するファイル」から取り、許可表 `LAYER_ORDER` の側は
    design.md が宣言する**全モジュール**を名前で持つ。この非対称性により、
    後続タスクが `shapes.py` などを追加した瞬間から、追加作業なしに本ファイルの
    全検査が新モジュールへ適用される
    （`test_every_source_file_is_known_to_the_layer_table` が、表に無い `.py` の
    出現を明示的な失敗として知らせる）。
    """
    return {path.stem: path.read_text(encoding="utf-8") for path in sorted(directory.glob("*.py"))}


def _current_sources() -> dict[str, str]:
    """現ツリーの `src/chassis_mechanism/` の `{モジュール名: ソース文字列}`。"""
    return _sources_in(SRC_DIR)


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
    を**関数内で遅延 import** し、未導入時に専用の失敗を返す」を意味のあるものに
    する。遅延 import は `import chassis_mechanism.cli` を `import build123d` へ
    到達させないからである。
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
    「実行時 import」として含む（形状ライブラリや兄弟パッケージの参照範囲は、
    遅延であろうと現れてはならないため）。
    """
    collector = _RuntimeImportCollector()
    collector.visit(ast.parse(source))
    return collector.imports


def collect_module_level_imports(source: str) -> list[tuple[str, int]]:
    """モジュール import 時に評価される import だけを抽出する（関数内は除く）。"""
    collector = _ModuleLevelImportCollector()
    collector.visit(ast.parse(source))
    return collector.imports


def _internal_target(module: str, package: str = PACKAGE) -> str | None:
    """`<package>.<target>` 形の import から `<target>` を取り出す。

    `package` の内部を指さない import には `None` を返す。
    """
    parts = module.split(".")
    if parts[0] != package or len(parts) == 1:
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
    frozenset({"layout"}),
    frozenset({"clearance", "joints"}),
    frozenset({"assembly", "baseline"}),
    frozenset({"shapes"}),
    frozenset({"export"}),
    frozenset({"cli"}),
)
"""design.md「Dependency Direction」の
`errors → params → config → layout → {clearance, joints} → {assembly, baseline}
→ shapes → export → cli` をそのままデータにしたもの。

⚠️ **同じ層のモジュール同士の import も許さない。** 設計は「各層は**左側の層から
のみ** import する」と述べており、同層は「左側」ではない（`{clearance, joints}` と
`{assembly, baseline}` は互いに独立な兄弟である）。
"""

LAYERED_MODULES: frozenset[str] = frozenset().union(*LAYER_ORDER)
CORE_MODULES: frozenset[str] = frozenset().union(*LAYER_ORDER[:6])
"""標準ライブラリと上流の公開 API だけで動く中核層（`errors` 〜 `baseline`）。

design.md「Allowed Dependencies」表の第1行「中核8モジュールはこれと下記の上流のみ」
の8モジュールである。
"""

CAD_LAYER_MODULES: frozenset[str] = frozenset({"shapes", "export"})
"""design.md が `build123d` の import を許す2モジュール。"""

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
      design.md「Dependency Direction」/ `#### PublicApi` の「**公開しないもの**:
      形状生成と書き出しの手段（`shapes` / `export`）」）
    - `__main__`: `python -m chassis_mechanism` の入口であり `cli` を呼ぶため全モジュール
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
    """`src/chassis_mechanism/` の全 `.py` が層表に載っている。

    ⚠️ **未知の `.py` を黙って見逃さないための番人である。** 後続タスクが
    design.md に無いモジュールを足した場合、あるいは名前を変えた場合、ここが落ちて
    層表の更新を強制する（表に無いモジュールは `allowed_import_targets` が扱えず、
    依存方向の検査が素通りしてしまう）。
    """
    unknown = sorted(set(_current_sources()) - KNOWN_MODULES)
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
        "layout",
        "clearance",
        "joints",
        "assembly",
        "baseline",
        "shapes",
        "export",
        "cli",
        "__main__",
    }
)
"""design.md「Directory Structure」の `src/chassis_mechanism/` が挙げる全13モジュール。

⚠️ **現ツリーに存在するかどうかとは無関係の一覧である。** タスク 2.x〜4.x が書く
予定のモジュールも最初から名前で持つ。
"""


def test_layer_table_names_every_module_declared_by_the_design() -> None:
    """層表が design.md「Directory Structure」の全13モジュールをちょうど名前で持つ。

    ⚠️ **この主張は「まだ書かれていない」ことに依存しない。** 現ツリーが4モジュール
    でも、本 Spec が完成して13モジュール揃っても等しく成り立つ（`set(現ツリー)` との
    真部分集合関係を主張すると、設計どおりの完成形が境界違反として報告され、後続
    タスクの実装者に本ファイルの編集を強いてしまう）。

    表が設計より**先を行っている**ことの実質は、次のテスト
    `test_checks_apply_to_modules_before_their_files_exist` が担う。
    """
    assert KNOWN_MODULES == DOCUMENTED_MODULES


def test_checks_apply_to_modules_before_their_files_exist() -> None:
    """検査関数は、ファイルが未作成のモジュール名に対しても機能する。

    層表が全13モジュールを名前で持つため、`shapes.py` などが追加された瞬間から
    **表の更新を待たずに**依存方向・形状ライブラリ・上流 import の各検査がその新
    モジュールへ及ぶ。ここではその性質を、実ファイルの有無に依らない形で固定する。
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
    assert allowed_import_targets("layout") == frozenset({"errors", "params", "config"})
    assert "joints" not in allowed_import_targets("clearance")  # 同層は不可
    assert "baseline" not in allowed_import_targets("assembly")  # 同層は不可
    assert {"clearance", "joints"} <= allowed_import_targets("assembly")
    assert "shapes" in allowed_import_targets("export")
    assert "export" not in allowed_import_targets("shapes")
    assert allowed_import_targets(PACKAGE_ENTRY) == CORE_MODULES
    assert CAD_LAYER_MODULES.isdisjoint(allowed_import_targets(PACKAGE_ENTRY))
    assert "cli" not in allowed_import_targets(PACKAGE_ENTRY)


# ---------------------------------------------------------------------------
# 1. 形状ライブラリの参照範囲
# ---------------------------------------------------------------------------

CAD_IMPORT_ROOTS: frozenset[str] = frozenset({"build123d", "OCP"})
"""形状ライブラリと、その推移依存である OCCT バインディングのトップレベル名。

design.md「Allowed Dependencies」は「`build123d`（＋推移依存の OCCT
バインディング）」を1件の依存として扱う。`OCP` を直接 import すれば `build123d`
を名乗らずに同じ重い依存を持ち込めてしまうため、両方を対象にする。
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

    ⚠️ **`shapes` / `export` は走査の対象外である**（`find_cad_import_violations`
    は `CAD_LAYER_MODULES` を素通しする）。この2つは design.md「Allowed
    Dependencies」が形状ライブラリの import を**許す**唯一の場所であり、実際に
    関数内で import している。したがって本件が述べているのは「⚠️ **許された2つ
    以外のどこにも無い**」ことである。
    """
    for module_name, source in _current_sources().items():
        violations = find_cad_import_violations(module_name, source)
        assert violations == [], f"{module_name}.py の形状ライブラリ参照: {violations}"


@pytest.mark.parametrize(
    ("module_name", "fake_source"),
    [
        ("params", "import build123d\n"),
        ("config", "from build123d import Cylinder\n"),
        ("__init__", "from chassis_mechanism.shapes import build_parts\nimport build123d\n"),
        ("layout", "import build123d.topology as topo\n"),
        ("cli", "import OCP\n"),
        ("clearance", "def derive() -> None:\n    import build123d\n"),
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
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from build123d import Part\n"
    )
    assert find_cad_import_violations("baseline", fake_source) == []


def test_detects_module_level_cad_layer_import_from_cli_in_crafted_source() -> None:
    """違反ケース: `cli` が `shapes` をトップレベル import する架空のソース。

    design.md「Dependency Direction」:「`cli` は `shapes` / `export` を**関数内で
    遅延 import** し、未導入時に専用の失敗を返す」。
    """
    fake_source = "from chassis_mechanism.shapes import build_parts\n"
    violations = find_module_level_cad_imports("cli", fake_source)
    assert violations != []
    assert "shapes" in violations[0]


def test_lazy_cad_layer_import_from_cli_is_not_flagged() -> None:
    """`cli` の関数内 `shapes` import は違反ではない（設計が要求する形）。"""
    fake_source = (
        "def cmd_build() -> int:\n"
        "    from chassis_mechanism.shapes import build_parts\n"
        "    from chassis_mechanism.export import export_all\n"
        "    return 0\n"
    )
    assert find_module_level_cad_imports("cli", fake_source) == []


def test_no_module_level_cad_layer_import_in_current_tree() -> None:
    """現ツリーのどのモジュールも `shapes` / `export` をトップレベル import しない。"""
    for module_name, source in _current_sources().items():
        violations = find_module_level_cad_imports(module_name, source)
        assert violations == [], f"{module_name}.py の形状レイヤ即時 import: {violations}"


# ---------------------------------------------------------------------------
# 2. パッケージ入口から形状ライブラリへ到達しない
# ---------------------------------------------------------------------------


def find_cad_reachable_from_entry(
    sources: Mapping[str, str], entry: str = PACKAGE_ENTRY, package: str = PACKAGE
) -> list[str]:
    """`entry` を import したときに形状ライブラリへ到達するかを検査する。

    モジュールトップレベルの内部 import だけを辺として幅優先で辿り、到達した先が
    (a) 形状ライブラリをトップレベル import している、または (b) CAD 層
    （`shapes` / `export`）そのものである場合に、到達経路を
    `"__init__ -> cli -> shapes"` の形の文字列として返す。空列であれば到達なし。

    `sources` に無いモジュールへの辺は辿れないため無視する（未作成モジュール）。

    ⚠️ `package` を引数に取るのは、design.md「Dependency Direction」が
    「上流 `catch_mechanism.__init__` も OCCT へ到達しない」という**前提**の上に
    本 Spec の性質を主張しているためである。同じ関数を上流のソース木へ当てて
    その前提も確かめる。
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
            if module.split(".")[0] in CAD_IMPORT_ROOTS:
                trail = " -> ".join(path)
                violations.append(f"{trail} -> {module} (line {lineno} of {module_name}.py)")
        for module, lineno in imports:
            target = _internal_target(module, package)
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
    """現ツリーで `import chassis_mechanism` が形状ライブラリへ到達しない。"""
    paths = find_cad_reachable_from_entry(_current_sources())
    assert paths == [], f"入口から形状ライブラリへ到達している: {paths}"


def test_upstream_entry_does_not_reach_cad() -> None:
    """`import catch_mechanism` も形状ライブラリへ到達しない（推移の前提）。

    design.md「Dependency Direction」:「⚠️ 上流 `catch_mechanism.__init__` も
    OCCT へ到達しないため、この性質は推移的に保たれる」。⚠️ **この前提が崩れれば、
    本ファイルが唯一 import している `catch_mechanism` を通じて OCCT が要求され、
    「形状ライブラリ非導入の環境でも本ファイルが通る」という性質も同時に崩れる。**

    ⚠️ 空振り（`package` の渡し違いで入口から1歩も辿れず、辺が無いから通る状態）を
    排するため、上流の木に CAD 層が現に存在することと、入口から辿れる内部の辺が
    現に存在することを先に確かめる。
    """
    upstream_sources = _sources_in(UPSTREAM_SRC_DIR)
    assert CAD_LAYER_MODULES <= set(upstream_sources), "上流に CAD 層のモジュールが無い"
    entry_edges = {
        _internal_target(module, UPSTREAM_PACKAGE)
        for module, _ in collect_module_level_imports(upstream_sources[PACKAGE_ENTRY])
    } - {None}
    assert entry_edges, "上流の入口から辿れる内部の辺が無い（検査が空振りしている）"

    paths = find_cad_reachable_from_entry(upstream_sources, package=UPSTREAM_PACKAGE)
    assert paths == [], f"上流の入口から形状ライブラリへ到達している: {paths}"


def test_detects_entry_reaching_cad_layer_through_module_chain() -> None:
    """違反ケース: `__init__ → cli → shapes` と辿れる架空のツリー。"""
    fake_tree = {
        "__init__": "from chassis_mechanism.cli import main\n",
        "cli": "from chassis_mechanism.shapes import build_parts\n",
        "shapes": "import build123d\n",
    }
    paths = find_cad_reachable_from_entry(fake_tree)
    assert paths != []
    assert "shapes" in paths[0]


def test_detects_entry_reaching_cad_library_directly() -> None:
    """違反ケース: `__init__` が形状ライブラリを直接 import する架空のツリー。"""
    fake_tree = {"__init__": "import build123d\n"}
    assert find_cad_reachable_from_entry(fake_tree) != []


def test_detects_entry_reaching_cad_library_through_core_module() -> None:
    """違反ケース: 中核層のモジュールが形状ライブラリを持ち込む架空のツリー。"""
    fake_tree = {
        "__init__": "from chassis_mechanism.baseline import GeometryBaseline\n",
        "baseline": "from build123d import Part\n",
    }
    paths = find_cad_reachable_from_entry(fake_tree)
    assert paths != []
    assert "baseline" in paths[0]


def test_lazy_import_chain_does_not_count_as_reaching_cad() -> None:
    """`cli` が関数内で `shapes` を import する形なら入口からは到達しない（誤検知回避）。"""
    fake_tree = {
        "__init__": "from chassis_mechanism.cli import main\n",
        "cli": (
            "def cmd_build() -> int:\n    from chassis_mechanism.shapes import build\n    return 0\n"
        ),
        "shapes": "import build123d\n",
    }
    assert find_cad_reachable_from_entry(fake_tree) == []


def test_entry_reaching_full_core_layer_is_not_flagged() -> None:
    """中核層を全部再エクスポートする入口は違反ではない（design.md `#### PublicApi`）。"""
    fake_tree = {
        "__init__": (
            "from chassis_mechanism.errors import ChassisMechanismError\n"
            "from chassis_mechanism.params import PARAMETER_PATHS\n"
            "from chassis_mechanism.config import load_params\n"
            "from chassis_mechanism.layout import derive_layout\n"
            "from chassis_mechanism.clearance import evaluate_clearance\n"
            "from chassis_mechanism.joints import load_fastener_schedule\n"
            "from chassis_mechanism.assembly import is_assembly_complete\n"
        ),
        "errors": "",
        "params": "from chassis_mechanism.errors import ParameterError\n",
        "config": "import json\nfrom catch_mechanism import load_params\n",
        "layout": "import math\n",
        "clearance": "import math\n",
        "joints": "import json\n",
        "assembly": "import json\n",
    }
    assert find_cad_reachable_from_entry(fake_tree) == []


# ---------------------------------------------------------------------------
# 3. 上流へは公開入口からのみ到達する
# ---------------------------------------------------------------------------


def find_upstream_internal_import_violations(source: str) -> list[str]:
    """上流の**内部モジュール**への直接 import を検出する。

    design.md「Allowed Dependencies」が許すのは `import catch_mechanism` と
    `from catch_mechanism import X` の2形だけであり、`catch_mechanism.params` の
    ような内部モジュールへ手を伸ばす形は許さない。⚠️ 上流の内部は公開契約では
    ないため、そこへ依存すると上流の内部変更で本 Spec が黙って壊れる。

    違反を `"module (line N)"` の列として返す。空列であれば違反なし。
    """
    return [
        f"{module} (line {lineno})"
        for module, lineno in collect_runtime_imports(source)
        if _internal_target(module, UPSTREAM_PACKAGE) is not None
    ]


def test_no_upstream_internal_import_in_current_tree() -> None:
    """現ツリーのどのモジュールも上流の内部モジュールへ手を伸ばさない。"""
    for module_name, source in _current_sources().items():
        violations = find_upstream_internal_import_violations(source)
        assert violations == [], f"{module_name}.py の上流内部 import: {violations}"


def test_current_tree_actually_imports_the_upstream_public_entry() -> None:
    """⚠️ 検査が空振りしていないこと（現ツリーに実際に上流への辺がある）。

    `params` と `config` が現に `catch_mechanism` の公開入口を import している
    ことを確かめ、「上流への辺が1本も無いから全部通っている」状態と区別する。
    """
    sources = _current_sources()
    for module_name in ("params", "config"):
        roots = {module for module, _ in collect_runtime_imports(sources[module_name])}
        assert UPSTREAM_PACKAGE in roots, f"{module_name}.py が上流の公開入口を使っていない"


@pytest.mark.parametrize(
    "fake_source",
    [
        "from catch_mechanism.params import MechanismParams\n",
        "import catch_mechanism.params\n",
        "import catch_mechanism.config as upstream_config\n",
        "from catch_mechanism.errors import CatchMechanismError\n",
        "def load() -> None:\n    from catch_mechanism.params import PARAMETER_PATHS\n",
    ],
)
def test_detects_upstream_internal_import_in_crafted_source(fake_source: str) -> None:
    """違反ケース: 上流の内部モジュールを直接 import する架空のソース。"""
    assert find_upstream_internal_import_violations(fake_source) != []


@pytest.mark.parametrize(
    "fake_source",
    [
        "import catch_mechanism\n",
        "from catch_mechanism import Provenance\n",
        "from catch_mechanism import PARAMETER_PATHS as UPSTREAM_PARAMETER_PATHS\n",
        "from chassis_mechanism.params import PARAMETER_PATHS\n",
        "import json\n",
    ],
)
def test_upstream_public_entry_import_is_not_flagged(fake_source: str) -> None:
    """公開入口経由の import と自パッケージ・標準ライブラリは違反ではない（誤検知回避）。"""
    assert find_upstream_internal_import_violations(fake_source) == []


#: `#### PublicApi` の見出し語からモジュール名への例外的な対応。
#: 他の節は見出し語を小文字にすればモジュール名になる（`Params` → `params`）。
_COMPONENT_HEADING_TO_MODULE: Mapping[str, str] = {"publicapi": PACKAGE_ENTRY}


def _upstream_importers_declared_by_design() -> frozenset[str]:
    """design.md の各コンポーネント節の `External:` 行から、上流へ依存してよい
    モジュールの集合を導く。

    ⚠️ **手書きの一覧を持たない。** `#### <Component>` 見出しを追いながら
    「`- External:` かつ `catch_mechanism` を含む」行を拾い、その節のモジュール名を
    集める。design.md の Components 節が唯一の情報源になる。
    """
    importers: set[str] = set()
    current: str | None = None
    for line in DESIGN_PATH.read_text(encoding="utf-8").splitlines():
        heading = re.match(r"^#### ([A-Za-z]+)", line)
        if line.startswith("#### "):
            current = None
            if heading:
                name = heading.group(1).lower()
                current = _COMPONENT_HEADING_TO_MODULE.get(name, name)
            continue
        stripped = line.strip()
        if current and stripped.startswith("- External:") and UPSTREAM_PACKAGE in stripped:
            importers.add(current)
    return frozenset(importers)


def _upstream_importers_named_by_the_dependency_direction_prose() -> frozenset[str]:
    """design.md「Dependency Direction」の1行が名指しするモジュールを取り出す。"""
    for line in DESIGN_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith(f"- `{UPSTREAM_PACKAGE}` の公開 API は"):
            return frozenset(re.findall(r"`([^`]+)`", stripped)) - {UPSTREAM_PACKAGE}
    raise AssertionError("design.md「Dependency Direction」に上流 API の許可行が無い")


UPSTREAM_IMPORT_ALLOWED_MODULES: frozenset[str] = _upstream_importers_declared_by_design()
"""上流の公開 API を import してよいモジュール（design.md の Components 節から導出）。"""


def test_upstream_importer_set_matches_the_component_sections() -> None:
    """導出した許可集合が design.md の Components 節が宣言する5モジュールと一致する。

    ⚠️ **design.md には既知の食い違いがある。**「Dependency Direction」の散文は
    「`catch_mechanism` の公開 API は `config` / `joints` / `baseline` / `shapes` から
    import してよい」と4件しか挙げないが、`#### Params` の **Dependencies** は
    `External: catch_mechanism.Provenance — 出所の型 (P0)` を宣言しており、
    出荷済みの `src/chassis_mechanism/params.py` も
    `from catch_mechanism import JointPolicy, Provenance` と書いている
    （design.md `#### Params` は「`Provenance` は**上流の型をそのまま使う**。
    ⚠️ 独自に定義しない」とも述べる——上流の型を使う以外の選択肢が無い）。

    **コンポーネント節の方が特定的な記述であり、コードもそれに従っている**ため、
    許可集合は Components 節から導いて `params` を含める。⚠️ 散文の側を黙って
    広げているのではなく、散文が挙げる4件が導出集合の**部分集合**であることを
    次のテストで固定し、どちらかが動けば落ちるようにしてある。
    """
    assert UPSTREAM_IMPORT_ALLOWED_MODULES == frozenset(
        {"params", "config", "joints", "baseline", "shapes"}
    )


def test_dependency_direction_prose_agrees_with_the_component_sections() -> None:
    """散文の名指しと Components 節からの導出集合が一致する。

    ⚠️ かつて散文は `params` を落としており、Components 節（`#### Params` の
    `External: catch_mechanism.Provenance (P0)`）とずれていた。より特定的な
    コンポーネント節が正であり、散文の側を揃えた。どちらかが再び動けば
    ここが落ちて本ファイルの再検討を促す。
    """
    prose = _upstream_importers_named_by_the_dependency_direction_prose()
    assert prose == UPSTREAM_IMPORT_ALLOWED_MODULES


def find_unexpected_upstream_importers(
    module_name: str,
    source: str,
    allowed: Collection[str] = UPSTREAM_IMPORT_ALLOWED_MODULES,
) -> list[str]:
    """上流の公開 API を、設計が許していないモジュールが import していないか検査する。

    違反を `"module_name -> module (line N)"` の列として返す。空列であれば違反なし。
    ⚠️ 上流の**内部**モジュールはどのモジュールからも不可であり、そちらは
    `find_upstream_internal_import_violations` が別途禁じる。
    """
    if module_name in set(allowed):
        return []
    return [
        f"{module_name} -> {module} (line {lineno})"
        for module, lineno in collect_runtime_imports(source)
        if module.split(".")[0] == UPSTREAM_PACKAGE
    ]


def test_only_designated_modules_import_upstream_in_current_tree() -> None:
    """現ツリーで上流を import しているのは設計が許したモジュールだけである。"""
    for module_name, source in _current_sources().items():
        violations = find_unexpected_upstream_importers(module_name, source)
        assert violations == [], f"{module_name}.py の想定外の上流 import: {violations}"


@pytest.mark.parametrize("module_name", ["errors", "__init__", "layout", "clearance", "cli"])
def test_detects_upstream_import_from_undesignated_module(module_name: str) -> None:
    """違反ケース: 設計が上流依存を宣言していないモジュールが上流を import する。"""
    fake_source = "from catch_mechanism import Provenance\n"
    violations = find_unexpected_upstream_importers(module_name, fake_source)
    assert violations != [], f"{module_name} の想定外の上流 import を検出できていない"


@pytest.mark.parametrize("module_name", sorted({"params", "config", "joints", "baseline", "shapes"}))
def test_designated_modules_may_import_upstream(module_name: str) -> None:
    """設計が上流依存を宣言したモジュールの上流 import は違反ではない（誤検知回避）。

    ⚠️ `params` がここに含まれるのは
    `test_upstream_importer_set_matches_the_component_sections` の理由による。
    """
    fake_source = "from catch_mechanism import JointPolicy, Provenance\n"
    assert find_unexpected_upstream_importers(module_name, fake_source) == []


# ---------------------------------------------------------------------------
# 4. 兄弟パッケージを import しない
# ---------------------------------------------------------------------------


def _sibling_packages() -> frozenset[str]:
    """`src/` 配下の、自パッケージでも上流でもないパッケージ名。

    design.md「Allowed Dependencies」は `prediction_core` / `trajectory_sim` /
    `sensing_foundation` を名指しで不可としつつ「その他の兄弟パッケージ」も
    まとめて不可としている。⚠️ **名指しの3つだけを列挙すると、後から増えた兄弟
    パッケージが素通りする**ため、`src/` の実際の内容から導く。
    """
    return frozenset(
        path.name
        for path in SRC_ROOT.iterdir()
        if path.is_dir()
        and path.name not in {PACKAGE, UPSTREAM_PACKAGE}
        and (path / "__init__.py").exists()
    )


FORBIDDEN_SIBLING_PACKAGES: frozenset[str] = _sibling_packages()


def test_forbidden_sibling_set_contains_the_packages_named_by_design() -> None:
    """導出した禁止集合が design.md の名指し3件を確かに含み、許可2件を含まない。"""
    assert {
        "prediction_core",
        "trajectory_sim",
        "sensing_foundation",
    } <= FORBIDDEN_SIBLING_PACKAGES
    assert PACKAGE not in FORBIDDEN_SIBLING_PACKAGES
    assert UPSTREAM_PACKAGE not in FORBIDDEN_SIBLING_PACKAGES


def find_sibling_package_import_violations(source: str) -> list[str]:
    """兄弟パッケージ（`prediction_core` / `trajectory_sim` 等）の import を検出する。

    違反を `"module (line N)"` の列として返す。空列であれば違反なし。

    ⚠️ **`trajectory_sim` への辺が1本も無いことは、design.md
    「⚠️ `chassis_mechanism` は `trajectory_sim` を import しない。還元は
    `configs/trajectory_sim/drivetrain-wheel60.json` の値と、それを読むだけの
    一致検査を通じて行う」の機械的な担保である。**
    """
    return [
        f"{module} (line {lineno})"
        for module, lineno in collect_runtime_imports(source)
        if module.split(".")[0] in FORBIDDEN_SIBLING_PACKAGES
    ]


def test_no_sibling_package_import_in_current_tree() -> None:
    """現ツリーのどのモジュールも兄弟パッケージを import しない。"""
    for module_name, source in _current_sources().items():
        violations = find_sibling_package_import_violations(source)
        assert violations == [], f"{module_name}.py の兄弟パッケージ import: {violations}"


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
def test_detects_sibling_package_import_in_crafted_source(fake_source: str) -> None:
    """違反ケース: 兄弟パッケージを import する架空のソース。"""
    assert find_sibling_package_import_violations(fake_source) != []


def test_own_upstream_and_stdlib_imports_are_not_flagged_as_siblings() -> None:
    """自パッケージ・上流・標準ライブラリの import は兄弟違反ではない（誤検知回避）。"""
    fake_source = (
        "import json\n"
        "from catch_mechanism import Provenance\n"
        "from chassis_mechanism.params import ChassisParams\n"
    )
    assert find_sibling_package_import_violations(fake_source) == []


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

    `params -> errors` と `config -> {errors, params}` が現に存在することを確かめ、
    「辺が1本も無いから全部通っている」状態と区別する。
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
        ("errors", "from chassis_mechanism.params import ChassisParams\n", "params"),
        ("params", "from chassis_mechanism.config import load_params\n", "config"),
        ("config", "from chassis_mechanism.layout import derive_layout\n", "layout"),
        ("layout", "from chassis_mechanism.clearance import evaluate_clearance\n", "clearance"),
        ("clearance", "from chassis_mechanism.joints import JointSpec\n", "joints"),
        ("assembly", "from chassis_mechanism.baseline import GeometryBaseline\n", "baseline"),
        ("baseline", "from chassis_mechanism.shapes import build_parts\n", "shapes"),
        ("shapes", "from chassis_mechanism.export import export_all\n", "export"),
        ("export", "import chassis_mechanism.cli as cli\n", "cli"),
        ("layout", "def load() -> None:\n    from chassis_mechanism.cli import main\n", "cli"),
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
        ("params", "from chassis_mechanism.errors import ParameterError\n"),
        ("config", "from chassis_mechanism.params import ChassisParams\n"),
        ("clearance", "from chassis_mechanism.layout import ChassisLayout\n"),
        ("baseline", "from chassis_mechanism.joints import FastenerSchedule\n"),
        ("shapes", "from chassis_mechanism.assembly import AssemblyRecord\n"),
        ("cli", "from chassis_mechanism.export import export_all\n"),
        ("__init__", "from chassis_mechanism.assembly import is_assembly_complete\n"),
        ("__main__", "from chassis_mechanism.cli import main\n"),
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
        "__init__", "from chassis_mechanism.shapes import build_parts\n"
    )
    assert violations != []
    assert "shapes" in violations[0]


def test_detects_import_of_unknown_internal_module() -> None:
    """違反ケース: 層表に無い内部モジュールへの辺（表の更新漏れを検出する）。"""
    violations = find_dependency_direction_violations(
        "config", "from chassis_mechanism.geometry_helpers import thing\n"
    )
    assert violations != []


# ---------------------------------------------------------------------------
# 6. 本 Spec のパラメータパスに上流のコンポーネント名が現れないこと（要件 1.3）
# ---------------------------------------------------------------------------


def component_names(paths: Collection[str]) -> frozenset[str]:
    """パス文字列の集合から、先頭のコンポーネント名の集合を取り出す。

    パスは `"<component>.<field>"` の形である（上流・本 Spec 共通）。
    """
    return frozenset(path.split(".")[0] for path in paths)


UPSTREAM_COMPONENTS: frozenset[str] = component_names(tuple(UPSTREAM_PARAMETER_PATHS))
"""上流の `PARAMETER_PATHS` が持つコンポーネント名（`trash_can` / `printing` / `joint` 等）。

⚠️ **手書きの一覧ではない。** 上流の公開 API から導くため、上流が群を増やせば
本検査の対象も自動的に増える。
"""


def collect_root_parameter_components(
    source: str, root_class: str = ROOT_PARAMS_CLASS
) -> list[tuple[str, int]]:
    """`root_class` の直下フィールド名を `(名前, 行番号)` の列として抽出する。

    本 Spec の `PARAMETER_PATHS` は `ChassisParams` のデータクラス木を走査して
    生成される（design.md `#### Params`「`PARAMETER_PATHS` はデータクラス木から
    生成し …… ⚠️ **一覧を手書きしない**」）。したがってパスのコンポーネント部分は
    `ChassisParams` の直下フィールド名そのものであり、ここを見れば
    `chassis_mechanism` を import せずに同じ集合が得られる。

    ⚠️ 除外は行わない。`provenance` のような寸法でないフィールドも含めるが、
    上流のコンポーネント名と衝突しない限り無害であり、**含める方が検査は厳しくなる**
    （除外表を持てば、その表が古びたときに検査へ穴が開く）。

    `root_class` が見つからないソース（`config.py` など）には空列を返す。
    ⚠️ 集約ルートが改名されて検査が空振りに落ちる事態は
    `test_collect_root_parameter_components_reads_the_real_dataclass` が
    現ツリーの13フィールドを名指しで突き合わせることで防ぐ。
    """
    components: list[tuple[str, int]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ClassDef) or node.name != root_class:
            continue
        for statement in node.body:
            if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
                components.append((statement.target.id, statement.lineno))
    return components


def find_upstream_component_name_collisions(
    source: str,
    upstream_components: Collection[str] = UPSTREAM_COMPONENTS,
    root_class: str = ROOT_PARAMS_CLASS,
) -> list[str]:
    """本 Spec のパラメータのコンポーネント名が上流の名前と重なっていないか検査する。

    要件 1.3 は「上流が公開している寸法値・造形制約・継手方針を参照して用い、同じ値
    を自身の設定ファイルへ再定義しない」と述べる。同じ値を2箇所に持たないことの
    **機械的な担保**として、上流が持つ群の名前が本 Spec のパラメータ木へ現れない
    ことを見る。

    ⚠️ **一致は「コンポーネント名としての完全一致」で見る。** 部分一致で見ると、
    上流の `joint`（継手方針）に対して本 Spec が持つ `joint_local`（本 Spec が課す
    接合部の下限。design.md `#### Params`）まで違反になってしまう。`joint_local` は
    上流の値の複製ではなく、上流の下限を**厳しくする**別の値である。

    違反を `"component (line N)"` の列として返す。空列であれば違反なし。
    """
    forbidden = frozenset(upstream_components)
    return [
        f"{name} (line {lineno})"
        for name, lineno in collect_root_parameter_components(source, root_class)
        if name in forbidden
    ]


def test_upstream_components_are_derived_and_non_empty() -> None:
    """上流のコンポーネント名が公開 API から実際に取れている（空振りでない）。"""
    assert UPSTREAM_COMPONENTS != frozenset()
    assert {"trash_can", "printing", "joint"} <= UPSTREAM_COMPONENTS


def test_component_names_splits_on_the_first_separator() -> None:
    """`component_names` がパスの先頭要素を返す。"""
    assert component_names(("trash_can.height_mm", "trash_can.mass_g", "joint.kind")) == frozenset(
        {"trash_can", "joint"}
    )


def test_collect_root_parameter_components_reads_the_real_dataclass() -> None:
    """現ツリーの `ChassisParams` から各群 ＋ `provenance` が読める。

    ⚠️ タスク 3.5 が配線ガイドの寸法（`cable`）を足したため13群である。
    """
    source = _current_sources()["params"]
    names = {name for name, _ in collect_root_parameter_components(source)}
    assert names == {
        "bracket",
        "motor",
        "wheel",
        "hub",
        "base",
        "clearance",
        "adapter",
        "battery",
        "board",
        "cable",
        "power",
        "stand",
        "joint_local",
        "provenance",
    }


def test_no_upstream_component_name_in_current_parameter_paths() -> None:
    """現ツリーの `ChassisParams` に上流のコンポーネント名が現れない（要件 1.3）。"""
    violations = find_upstream_component_name_collisions(_current_sources()["params"])
    assert violations == [], f"上流のコンポーネント名の再定義: {violations}"


@pytest.mark.parametrize(
    ("fake_field", "expected"),
    [
        ("    trash_can: TrashCanMeasurements\n", "trash_can"),
        ("    printing: PrintingConstraints\n", "printing"),
        ("    joint: JointPolicy\n", "joint"),
        ("    rim: RimSpec\n", "rim"),
    ],
)
def test_detects_upstream_component_name_in_crafted_source(fake_field: str, expected: str) -> None:
    """違反ケース: 上流の群を本 Spec のパラメータ木へ持ち込む架空のソース。"""
    fake_source = (
        "class ChassisParams:\n"
        '    """架空の集約ルート。"""\n'
        "\n"
        "    bracket: BracketMeasurements\n"
        f"{fake_field}"
        "    provenance: Mapping[str, Provenance]\n"
    )
    violations = find_upstream_component_name_collisions(fake_source)
    assert violations != [], f"{expected} の再定義を検出できていない"
    assert expected in violations[0]


def test_locally_scoped_joint_limits_are_not_flagged() -> None:
    """`joint_local` は上流 `joint` の複製ではない（誤検知回避）。

    design.md `#### Params`:「`LocalJointLimits.min_bearing_area_mm2` は**上流の
    下限以上でなければならない**。⚠️ 本 Spec は上流の下限を厳しくすることはできるが
    緩めることはできない」——上流の値を写したのではなく、上流の値を参照して課す
    別の値である。
    """
    fake_source = (
        "class ChassisParams:\n"
        "    joint_local: LocalJointLimits\n"
        "    provenance: Mapping[str, Provenance]\n"
    )
    assert find_upstream_component_name_collisions(fake_source) == []


def test_upstream_file_path_constant_is_not_a_duplicated_value() -> None:
    """⚠️ 上流ファイルの**所在**を持つことは値の複製ではない（誤検知回避）。

    `config.UPSTREAM_DIMENSIONS_PATH` は `configs/catch_mechanism/dimensions.json`
    というパスであり、要件 1.3 が禁じる「同じ値の再定義」ではない。本検査は
    `ChassisParams` の**パラメータ木**だけを見るため、`config` の定数は対象外である
    ——その事実をここで固定する（対象を広げて文字列一致で探すと、この定数を誤って
    違反として報告してしまう）。
    """
    config_source = _current_sources()["config"]
    assert "UPSTREAM_DIMENSIONS_PATH" in config_source
    assert find_upstream_component_name_collisions(config_source) == []


# ---------------------------------------------------------------------------
# 7. 実行時サードパーティ依存ゼロ
# ---------------------------------------------------------------------------


def find_third_party_import_violations(module_name: str, source: str) -> list[str]:
    """標準ライブラリ・自パッケージ・上流以外の import を検出する。

    形状ライブラリ（`CAD_IMPORT_ROOTS`）は `shapes` / `export` に限り許可する
    （design.md「Allowed Dependencies」）。違反は `"module (line N)"` の列。

    ⚠️ 許可リストを手書きせず `sys.stdlib_module_names` を正とする（design.md
    「Technology Stack」は許可先を「Python 3.11 標準ライブラリ ＋ `catch_mechanism`
    の公開 API」と**全体で**述べており、個別の列挙ではないため）。
    """
    allowed = set(sys.stdlib_module_names) | {PACKAGE, UPSTREAM_PACKAGE}
    if module_name in CAD_LAYER_MODULES:
        allowed |= CAD_IMPORT_ROOTS
    violations: list[str] = []
    for module, lineno in collect_runtime_imports(source):
        if module.startswith("."):
            continue  # 相対 import は find_relative_imports が別途禁じる
        if module.split(".")[0] not in allowed:
            violations.append(f"{module} (line {lineno})")
    return violations


def test_no_third_party_import_in_current_tree() -> None:
    """現ツリーの全モジュールが標準ライブラリ・自パッケージ・上流だけを import する。"""
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


def test_stdlib_upstream_and_own_package_imports_are_not_flagged() -> None:
    """標準ライブラリ・上流・自パッケージは許可される（誤検知回避）。"""
    fake_source = (
        "from __future__ import annotations\n"
        "import hashlib\n"
        "import json\n"
        "from collections.abc import Mapping\n"
        "from dataclasses import dataclass\n"
        "from pathlib import Path\n"
        "from catch_mechanism import Provenance\n"
        "from chassis_mechanism.errors import ParameterError\n"
    )
    assert find_third_party_import_violations("config", fake_source) == []


def test_cad_library_is_third_party_outside_the_cad_layer() -> None:
    """形状ライブラリは CAD 層の外ではサードパーティとして扱われる。"""
    assert find_third_party_import_violations("params", "import build123d\n") != []
    assert find_third_party_import_violations("shapes", "import build123d\n") == []


# ---------------------------------------------------------------------------
# 8. 相対 import が無いこと（上の各検査が絶対 import 前提であるため）
# ---------------------------------------------------------------------------


def find_relative_imports(source: str) -> list[str]:
    """パッケージ内相対 import（`from . import x`）を検出する。

    ⚠️ 上の各検査は `chassis_mechanism.<module>` という**絶対 import の形**から
    依存先を読み取る。相対 import を許すと、その形を経由せずに辺を作れてしまい、
    検査が素通りする。既存の4モジュールは絶対 import で書かれており、その規律を
    ここで固定する（上流3 Spec も同じ書き方である）。
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
    ["from . import errors\n", "from .params import ChassisParams\n", "from ..pkg import x\n"],
)
def test_detects_relative_import_in_crafted_source(fake_source: str) -> None:
    """違反ケース: 相対 import を含む架空のソース。"""
    assert find_relative_imports(fake_source) != []


def test_absolute_import_is_not_flagged_as_relative() -> None:
    """絶対 import は誤検知しない。"""
    assert find_relative_imports("from chassis_mechanism.errors import ParameterError\n") == []


# ---------------------------------------------------------------------------
# 9. 形状ライブラリ非導入の環境で完結すること
# ---------------------------------------------------------------------------


def test_this_module_never_imports_the_package_under_test_or_the_cad_library() -> None:
    """本ファイル自身が `chassis_mechanism` も形状ライブラリも import しない。

    「形状生成の環境を持たない実行環境でも検査が完了する」ことの担保は、本ファイルが
    静的解析だけで完結していることに依る。自分自身を同じ物差しで測っておく。
    ⚠️ 唯一の非標準ライブラリ import は上流の公開入口 `catch_mechanism` であり、
    それが OCCT へ到達しないことは `test_upstream_entry_does_not_reach_cad` が
    別途確かめている。
    """
    own_source = Path(__file__).read_text(encoding="utf-8")
    roots = {module.split(".")[0] for module, _ in collect_runtime_imports(own_source)}
    assert roots.isdisjoint(CAD_IMPORT_ROOTS)
    assert PACKAGE not in roots
    assert roots <= (set(sys.stdlib_module_names) | {"pytest", UPSTREAM_PACKAGE})
