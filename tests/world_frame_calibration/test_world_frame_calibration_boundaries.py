"""境界テスト: 依存方向・上流 import 集中・禁止 import・変更範囲の静的検証
(tasks.md タスク 7.3 / 要件 3.8, 8.2, 8.6, 11.1, 11.2, 11.4, 11.5)。

design.md「Architecture Pattern & Boundary Map」の Allowed Dependencies /
禁止 リストと、「Dependency Direction」の層表（`errors`/`linalg`(L0) →
`types`(L1) → `plan`/`deproject`(L2) → `plane`/`transform`(L3) →
`anchors`(L4) → `frame`(L5) → `result`(L6) → `verify`(L7) →
`report`/`upstream`(L8) → `cli`/`__init__`(L9)）を、`src/world_frame_
calibration/**/*.py` のソースを `ast` で静的解析することにより検証する。
実行時 import・実行時 introspection は一切用いない（design.md の層表は
ソースレベルの契約であり、無関係な理由での実行時 import の推移閉包を
誤検出しないようにするため）。

本テストが固定する7点（tasks.md タスク7.3の箇条書きに1対1対応）:

1. `upstream.py` / `deproject.py` / `cli.py` 以外のモジュールが
   `sensing_foundation` を import していないこと
2. `deproject.py` が `sensing_foundation` から参照するシンボルが
   `deproject_pixel` と `CameraIntrinsics` の2つだけであること（要件 3.8。
   1の狭い特殊形）
3. 本パッケージのどこにもピンホールの式が再実装されていないこと
   （`ppx_px` / `ppy_px` / `fx_px` / `fy_px` を用いた除算がソースに現れない）
4. 全モジュールが `cv2` / `pyrealsense2` / `prediction_core` を
   import していないこと
5. 設計で定めた層をまたぐ逆方向の import が無いこと（依存方向表そのものを
   `DEPENDENCY_ALLOWED_TARGETS` として符号化し、各モジュールの実際の
   import と突き合わせる）
6. 変更対象が自パッケージ・自テスト・自 Spec ディレクトリに閉じており、
   `src/prediction_core/**` / `tests/prediction_core/**` /
   `src/sensing_foundation/**` を変更していないこと
7. 検出・追跡・予測に相当する処理が本パッケージに存在しないこと
   （公開シンボル一覧 [タスク6.3で固定済み] と依存の両面から）

各チェックは「検査ロジックを純粋関数として切り出し、(a) 実際のソースに
対して違反ゼロであることを確認するテストと、(b) 意図的に違反を含む架空の
ソース文字列/リストを渡すと検出できることを確認するテストの両方を書く」
という `tests/prediction_core/test_boundaries.py` の技法を踏襲する。
これにより「意図的に違反 import を足すと失敗することを確認できる」という
タスク7.3の観測可能な完了状態を、本物のプロダクションファイルを書き換える
ことなく証明する。

ファイル名について: `tests/**` に `__init__.py` が無いため、design.md の
File Structure Plan が示す `test_boundaries.py` ではなく、既存タスクの
命名規約（tasks.md タスク1.6 実装ノート）に従いプレフィックス付きの
`test_world_frame_calibration_boundaries.py` とする。
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

import world_frame_calibration

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src" / "world_frame_calibration"


def _source_files() -> dict[str, Path]:
    """`src/world_frame_calibration/*.py` を `{モジュール名: パス}` で返す。"""
    return {path.stem: path for path in sorted(SRC_DIR.glob("*.py"))}


SOURCE_FILES: dict[str, Path] = _source_files()


def test_source_files_enumeration_matches_expected_modules() -> None:
    """`SOURCE_FILES` が想定どおりの15モジュールを走査対象にしている。

    ここがずれると、以降の全チェックが一部モジュールを見落としたまま
    「成功」してしまうため、走査対象自体を固定する。
    """
    assert set(SOURCE_FILES) == {
        "__init__",
        "anchors",
        "cli",
        "deproject",
        "errors",
        "frame",
        "linalg",
        "plan",
        "plane",
        "report",
        "result",
        "transform",
        "types",
        "upstream",
        "verify",
    }


# ---------------------------------------------------------------------------
# 1. `upstream.py` / `deproject.py` / `cli.py` 以外は `sensing_foundation` を
#    import していないこと（design.md「Allowed Dependencies」）
# ---------------------------------------------------------------------------

SENSING_FOUNDATION_ALLOWED_MODULES: frozenset[str] = frozenset({"upstream", "deproject", "cli"})


def find_sensing_foundation_import_lines(source: str) -> list[int]:
    """ソース文字列中の `sensing_foundation` への import 行番号一覧を返す。

    `import sensing_foundation` 形と `from sensing_foundation[.x] import ...`
    形の両方を検出する。空リストであれば参照なし。
    """
    tree = ast.parse(source)
    linenos: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            if node.module == "sensing_foundation" or node.module.startswith(
                "sensing_foundation."
            ):
                linenos.append(node.lineno)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "sensing_foundation" or alias.name.startswith(
                    "sensing_foundation."
                ):
                    linenos.append(node.lineno)
    return linenos


def test_only_upstream_deproject_cli_import_sensing_foundation() -> None:
    """許可された3モジュール以外に `sensing_foundation` 参照が無い。"""
    for module_name, path in SOURCE_FILES.items():
        if module_name in SENSING_FOUNDATION_ALLOWED_MODULES:
            continue
        source = path.read_text(encoding="utf-8")
        linenos = find_sensing_foundation_import_lines(source)
        assert linenos == [], (
            f"{module_name}.py が sensing_foundation を import している（行 {linenos}）"
        )


def test_upstream_deproject_cli_actually_reference_sensing_foundation() -> None:
    """前提の健全性確認: 許可された3モジュールが実際に `sensing_foundation` を
    参照している（さもないと上のテストが空虚に通ってしまう）。
    """
    for module_name in SENSING_FOUNDATION_ALLOWED_MODULES:
        source = SOURCE_FILES[module_name].read_text(encoding="utf-8")
        assert find_sensing_foundation_import_lines(source) != [], (
            f"{module_name}.py は sensing_foundation を参照するはずだが検出されなかった"
        )


def test_detects_sensing_foundation_from_import_in_crafted_source() -> None:
    """違反ケース: `from sensing_foundation import ...` を含む架空のソースが検出される。"""
    fake_source = "from sensing_foundation import CameraIntrinsics\n"
    assert find_sensing_foundation_import_lines(fake_source) != []


def test_detects_bare_sensing_foundation_import_in_crafted_source() -> None:
    """違反ケース: `import sensing_foundation` を含む架空のソースが検出される。"""
    fake_source = "import sensing_foundation\n"
    assert find_sensing_foundation_import_lines(fake_source) != []


def test_does_not_flag_unrelated_import_in_crafted_source() -> None:
    """誤検知回避: 無関係な import は検出されない。"""
    fake_source = "import numpy as np\nfrom world_frame_calibration.types import Plane\n"
    assert find_sensing_foundation_import_lines(fake_source) == []


# ---------------------------------------------------------------------------
# 2. `deproject.py` が参照してよい上流シンボルは `deproject_pixel` と
#    `CameraIntrinsics` の2つだけであること（要件 3.8。1のより狭い特殊形）
# ---------------------------------------------------------------------------

ALLOWED_DEPROJECT_SENSING_FOUNDATION_SYMBOLS: frozenset[str] = frozenset(
    {"deproject_pixel", "CameraIntrinsics"}
)


def find_sensing_foundation_symbol_imports(source: str) -> set[str]:
    """`from sensing_foundation import ...` で取り込まれるシンボル名の集合を返す。"""
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "sensing_foundation":
            for alias in node.names:
                names.add(alias.name)
    return names


def test_deproject_references_exactly_deproject_pixel_and_camera_intrinsics() -> None:
    """`deproject.py` が参照する上流シンボルが厳密に2つ（要件 3.8）。"""
    source = SOURCE_FILES["deproject"].read_text(encoding="utf-8")
    assert (
        find_sensing_foundation_symbol_imports(source)
        == ALLOWED_DEPROJECT_SENSING_FOUNDATION_SYMBOLS
    )


def test_deproject_does_not_use_bare_import_sensing_foundation_form() -> None:
    """`deproject.py` が `import sensing_foundation` 形（from 節以外）を使っていない。

    `import sensing_foundation` 形はモジュール全体への参照を許してしまい、
    2シンボル限定の意図を破るため個別に禁止する。
    """
    source = SOURCE_FILES["deproject"].read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] != "sensing_foundation", (
                    "deproject.py が 'import sensing_foundation' 形式を使用している"
                    "（from 節で2シンボルだけを取り込む契約に反する）"
                )


def test_detects_extra_sensing_foundation_symbol_in_crafted_deproject_source() -> None:
    """違反ケース: 許可2シンボルに加えて別のシンボルを取り込む架空のソースが検出される。"""
    fake_source = (
        "from sensing_foundation import deproject_pixel, CameraIntrinsics, depth_raw_to_mm\n"
    )
    found = find_sensing_foundation_symbol_imports(fake_source)
    assert found != ALLOWED_DEPROJECT_SENSING_FOUNDATION_SYMBOLS
    assert "depth_raw_to_mm" in found


def test_detects_missing_symbol_in_crafted_deproject_source() -> None:
    """違反ケース: 2シンボルのうち1つしか取り込まない架空のソースも不一致として検出される。"""
    fake_source = "from sensing_foundation import deproject_pixel\n"
    found = find_sensing_foundation_symbol_imports(fake_source)
    assert found != ALLOWED_DEPROJECT_SENSING_FOUNDATION_SYMBOLS


# ---------------------------------------------------------------------------
# 3. ピンホールの式が再実装されていないこと（`ppx_px` / `ppy_px` / `fx_px` /
#    `fy_px` を用いた除算がソースに現れない）
# ---------------------------------------------------------------------------

PINHOLE_FIELD_NAMES: frozenset[str] = frozenset({"ppx_px", "ppy_px", "fx_px", "fy_px"})


class _PinholeDivisionFinder(ast.NodeVisitor):
    """`/` または `//` の演算子オペランドに内部パラメータのフィールド名が
    現れる `BinOp` を検出する。
    """

    def __init__(self) -> None:
        self.found: list[tuple[str, int]] = []

    def visit_BinOp(self, node: ast.BinOp) -> None:  # noqa: N802 (ast.NodeVisitor 命名規約)
        if isinstance(node.op, (ast.Div, ast.FloorDiv)):
            names: set[str] = set()
            names |= self._collect_field_names(node.left)
            names |= self._collect_field_names(node.right)
            for name in sorted(names):
                self.found.append((name, node.lineno))
        self.generic_visit(node)

    @staticmethod
    def _collect_field_names(expr: ast.expr) -> set[str]:
        found: set[str] = set()
        for sub in ast.walk(expr):
            if isinstance(sub, ast.Name) and sub.id in PINHOLE_FIELD_NAMES:
                found.add(sub.id)
            elif isinstance(sub, ast.Attribute) and sub.attr in PINHOLE_FIELD_NAMES:
                found.add(sub.attr)
        return found


def find_pinhole_formula_divisions(source: str) -> list[tuple[str, int]]:
    """ソース文字列中の「内部パラメータフィールド名を含む除算」を
    `(フィールド名, 行番号)` の列として返す。空列であれば違反なし。
    """
    tree = ast.parse(source)
    finder = _PinholeDivisionFinder()
    finder.visit(tree)
    return finder.found


def test_no_pinhole_formula_division_in_any_actual_source_file() -> None:
    """`src/world_frame_calibration/**` のどこにもピンホール式の除算が無い。"""
    for module_name, path in SOURCE_FILES.items():
        source = path.read_text(encoding="utf-8")
        found = find_pinhole_formula_divisions(source)
        assert found == [], f"{module_name}.py にピンホール式らしき除算がある: {found}"


def test_detects_pinhole_formula_reimplementation_with_attribute_access_in_crafted_source() -> (
    None
):
    """違反ケース: `(u - intr.ppx_px) / intr.fx_px` 形の再実装が検出される。"""
    fake_source = (
        "def deproject(u, v, z, intr):\n"
        "    x = (u - intr.ppx_px) / intr.fx_px * z\n"
        "    y = (v - intr.ppy_px) / intr.fy_px * z\n"
        "    return x, y, z\n"
    )
    found = find_pinhole_formula_divisions(fake_source)
    assert found != []
    field_names = {name for name, _ in found}
    assert field_names == PINHOLE_FIELD_NAMES


def test_detects_pinhole_formula_with_bare_names_in_crafted_source() -> None:
    """違反ケース: 属性アクセスでなく裸の変数名でも検出される。"""
    fake_source = "def f(fx_px, ppx_px, u):\n    return (u - ppx_px) / fx_px\n"
    found = find_pinhole_formula_divisions(fake_source)
    field_names = {name for name, _ in found}
    assert {"ppx_px", "fx_px"} <= field_names


def test_detects_pinhole_formula_with_floor_division_in_crafted_source() -> None:
    """違反ケース: `//`（floor division）でも検出される。"""
    fake_source = "def f(intr, u):\n    return (u - intr.ppx_px) // intr.fx_px\n"
    assert find_pinhole_formula_divisions(fake_source) != []


def test_does_not_flag_unrelated_division_in_crafted_source() -> None:
    """誤検知回避: 内部パラメータに無関係な除算は検出されない。"""
    fake_source = "def f(a, b):\n    return a / b\n"
    assert find_pinhole_formula_divisions(fake_source) == []


def test_does_not_flag_field_access_without_division_in_crafted_source() -> None:
    """誤検知回避: フィールドへのアクセスだけで除算を伴わなければ検出されない
    （代入・辞書化・比較などの正当な用途を誤検知しない）。
    """
    fake_source = (
        "def f(intr):\n"
        "    return {'fx_px': intr.fx_px, 'ppx_px': intr.ppx_px}\n"
        "def g(intr):\n"
        "    return intr.fx_px == 0.0\n"
    )
    assert find_pinhole_formula_divisions(fake_source) == []


def test_does_not_flag_division_by_unrelated_field_sharing_no_name_in_crafted_source() -> None:
    """誤検知回避: フィールド名を含まない除算（他の値どうしの除算）は検出されない。"""
    fake_source = "def f(width_px, height_px):\n    return width_px / height_px\n"
    assert find_pinhole_formula_divisions(fake_source) == []


# ---------------------------------------------------------------------------
# 4. 全モジュールが `cv2` / `pyrealsense2` / `prediction_core` を
#    import していないこと
# ---------------------------------------------------------------------------

FORBIDDEN_ROOT_MODULES: frozenset[str] = frozenset({"cv2", "pyrealsense2", "prediction_core"})


def find_forbidden_root_imports(source: str) -> list[str]:
    """`cv2` / `pyrealsense2` / `prediction_core` への import を
    `"モジュール名 (line N)"` の列として返す。空列であれば違反なし。
    """
    tree = ast.parse(source)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            root = node.module.split(".")[0]
            if root in FORBIDDEN_ROOT_MODULES:
                violations.append(f"{node.module} (line {node.lineno})")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in FORBIDDEN_ROOT_MODULES:
                    violations.append(f"{alias.name} (line {node.lineno})")
    return violations


def test_no_forbidden_root_imports_in_any_source_file() -> None:
    """`src/world_frame_calibration/**` に `cv2` / `pyrealsense2` /
    `prediction_core` の import が無い。
    """
    for module_name, path in SOURCE_FILES.items():
        source = path.read_text(encoding="utf-8")
        violations = find_forbidden_root_imports(source)
        assert violations == [], f"{module_name}.py に禁止 import: {violations}"


@pytest.mark.parametrize(
    "fake_source",
    [
        "import cv2\n",
        "import pyrealsense2 as rs\n",
        "from cv2 import imread\n",
        "from pyrealsense2 import pipeline\n",
        "import prediction_core\n",
        "from prediction_core import types\n",
        "from prediction_core.record import ThrowRecord\n",
    ],
)
def test_detects_forbidden_root_import_in_crafted_source(fake_source: str) -> None:
    """違反ケース: 禁止3モジュールへの各 import 形が検出される。"""
    assert find_forbidden_root_imports(fake_source) != []


def test_does_not_flag_similarly_named_unrelated_module_in_crafted_source() -> None:
    """誤検知回避: 禁止ルート名を接頭辞に持つだけの無関係なモジュールは検出されない
    （例: `cv2` に対する `cv2_utils` のような別パッケージ）。
    """
    fake_source = "import cv2_utils\n"
    assert find_forbidden_root_imports(fake_source) == []


# ---------------------------------------------------------------------------
# 5. 設計で定めた層をまたぐ逆方向の import が無いこと
# ---------------------------------------------------------------------------

# design.md「Dependency Direction」の層表をそのまま許可リストとして表現する。
# `sensing_foundation`（`deproject` / `upstream` / `cli` のみ）は本パッケージ
# 内部の辺ではないため、ここには含めず 1./2. で別途検証する。
DEPENDENCY_ALLOWED_TARGETS: dict[str, frozenset[str]] = {
    # Layer 0: 標準ライブラリ(+ numpy は linalg のみ)。互いに import しない。
    "errors": frozenset(),
    "linalg": frozenset(),
    # Layer 1
    "types": frozenset({"errors", "linalg"}),
    # Layer 2
    "plan": frozenset({"errors", "linalg", "types"}),
    "deproject": frozenset({"errors", "linalg", "types"}),
    # Layer 3
    "plane": frozenset({"errors", "linalg", "types", "plan", "deproject"}),
    "transform": frozenset({"errors", "linalg", "types", "plan", "deproject"}),
    # Layer 4
    "anchors": frozenset(
        {"errors", "linalg", "types", "plan", "deproject", "plane", "transform"}
    ),
    # Layer 5
    "frame": frozenset(
        {"errors", "linalg", "types", "plan", "deproject", "plane", "transform", "anchors"}
    ),
    # Layer 6
    "result": frozenset(
        {
            "errors",
            "linalg",
            "types",
            "plan",
            "deproject",
            "plane",
            "transform",
            "anchors",
            "frame",
        }
    ),
    # Layer 7
    "verify": frozenset(
        {
            "errors",
            "linalg",
            "types",
            "plan",
            "deproject",
            "plane",
            "transform",
            "anchors",
            "frame",
            "result",
        }
    ),
    # Layer 8
    "report": frozenset(
        {
            "errors",
            "linalg",
            "types",
            "plan",
            "deproject",
            "plane",
            "transform",
            "anchors",
            "frame",
            "result",
            "verify",
        }
    ),
    # `upstream` は同じ層8だが `report`/`verify` を含まない
    # （design.md「`report` が `upstream` を import しないのは意図的」の裏返し
    # として、`upstream` も `verify`/`report` を import してはならない）。
    "upstream": frozenset(
        {
            "errors",
            "linalg",
            "types",
            "plan",
            "deproject",
            "plane",
            "transform",
            "anchors",
            "frame",
            "result",
        }
    ),
    # Layer 9
    "cli": frozenset(
        {
            "errors",
            "linalg",
            "types",
            "plan",
            "deproject",
            "plane",
            "transform",
            "anchors",
            "frame",
            "result",
            "verify",
            "report",
            "upstream",
        }
    ),
    "__init__": frozenset(
        {
            "errors",
            "linalg",
            "types",
            "plan",
            "deproject",
            "plane",
            "transform",
            "anchors",
            "frame",
            "result",
            "verify",
            "report",
            "upstream",
        }
    ),
}


def test_dependency_allowed_targets_covers_exactly_the_source_modules() -> None:
    """`DEPENDENCY_ALLOWED_TARGETS` のキー集合が `SOURCE_FILES` と完全一致する。

    ここがずれると新設/削除されたモジュールが依存方向検査から漏れる。
    """
    assert set(DEPENDENCY_ALLOWED_TARGETS) == set(SOURCE_FILES)


def collect_internal_wfc_targets(source: str) -> list[tuple[str, int]]:
    """`world_frame_calibration` パッケージ内部の他モジュールへの参照を
    `(対象モジュール名, 行番号)` の列として収集する。

    `from world_frame_calibration.X import ...`、
    `from world_frame_calibration import X`（X がサブモジュール名の場合）、
    `import world_frame_calibration.X` の3形式すべてに対応する。
    """
    tree = ast.parse(source)
    targets: list[tuple[str, int]] = []
    known_submodules = set(DEPENDENCY_ALLOWED_TARGETS)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            if node.module == "world_frame_calibration":
                for alias in node.names:
                    if alias.name in known_submodules:
                        targets.append((alias.name, node.lineno))
            elif node.module.startswith("world_frame_calibration."):
                parts = node.module.split(".")
                if len(parts) >= 2:
                    targets.append((parts[1], node.lineno))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("world_frame_calibration."):
                    parts = alias.name.split(".")
                    if len(parts) >= 2:
                        targets.append((parts[1], node.lineno))
    return targets


def find_dependency_direction_violations(module_name: str, source: str) -> list[str]:
    """`module_name` のソースから、依存方向表に無い内部 import を検出する。

    違反を `"module_name -> target (line N)"` の列として返す。空列であれば違反なし。
    """
    allowed = DEPENDENCY_ALLOWED_TARGETS[module_name]
    violations: list[str] = []
    for target, lineno in collect_internal_wfc_targets(source):
        if target == module_name:
            continue  # 自己参照（実際には起こらないがガード）
        if target not in allowed:
            violations.append(f"{module_name} -> {target} (line {lineno})")
    return violations


def test_dependency_direction_respected_by_all_source_modules() -> None:
    """`src/world_frame_calibration/**` の全ファイルが依存方向表に従う。"""
    for module_name, path in SOURCE_FILES.items():
        source = path.read_text(encoding="utf-8")
        violations = find_dependency_direction_violations(module_name, source)
        assert violations == [], f"{module_name}.py の依存方向違反: {violations}"


def test_detects_layer0_modules_importing_each_other_in_crafted_source() -> None:
    """違反ケース: L0 の `errors` が同じ L0 の `linalg` を import する架空のソース
    （L0 相互 import 禁止）。
    """
    fake_source = "from world_frame_calibration.linalg import unit\n"
    violations = find_dependency_direction_violations("errors", fake_source)
    assert violations != []
    assert "linalg" in violations[0]


def test_detects_upward_dependency_from_types_to_plan_in_crafted_source() -> None:
    """違反ケース: `types`（L1）が `plan`（L2）を import する架空のソース（上向き依存）。"""
    fake_source = "from world_frame_calibration.plan import CalibrationPlan\n"
    violations = find_dependency_direction_violations("types", fake_source)
    assert violations != []
    assert "plan" in violations[0]


def test_detects_upward_dependency_from_deproject_to_plane_in_crafted_source() -> None:
    """違反ケース: `deproject`（L2）が `plane`（L3）を import する架空のソース
    （`from world_frame_calibration import X` 形での検出も兼ねる）。
    """
    fake_source = "from world_frame_calibration import plane\n"
    violations = find_dependency_direction_violations("deproject", fake_source)
    assert violations != []
    assert "plane" in violations[0]


def test_detects_upstream_importing_verify_in_crafted_source() -> None:
    """違反ケース: `upstream`（L8、0〜6のみ許可）が `verify`（L7）を import する
    架空のソース。`report`（同じL8だが0〜7許可）との非対称性を突く回帰。
    """
    fake_source = "from world_frame_calibration.verify import VerificationReport\n"
    violations = find_dependency_direction_violations("upstream", fake_source)
    assert violations != []
    assert "verify" in violations[0]


def test_detects_report_importing_upstream_in_crafted_source() -> None:
    """違反ケース: `report` が `upstream` を import する架空のソース。

    design.md「`report` が `upstream` を import しないのは意図的である」を
    直接裏付ける回帰（ログ無効時にレポートが変わらないことを保証する設計判断）。
    """
    fake_source = "from world_frame_calibration import upstream\n"
    violations = find_dependency_direction_violations("report", fake_source)
    assert violations != []
    assert "upstream" in violations[0]


def test_report_importing_verify_is_allowed_as_a_sanity_check() -> None:
    """健全性確認: `report` が `verify`（L7、report の許可範囲 0〜7 内）を
    import することは正当であり誤検知しない（実際に report.py が行っている）。
    """
    fake_source = "from world_frame_calibration.verify import Verdict\n"
    violations = find_dependency_direction_violations("report", fake_source)
    assert violations == []


def test_detects_cli_importing_nonexistent_module_dependency_is_not_applicable() -> None:
    """健全性確認: `cli`（L9）は全内部モジュールを import してよい（依存方向表の頂点）。"""
    fake_source = (
        "from world_frame_calibration import anchors\n"
        "from world_frame_calibration import upstream\n"
        "from world_frame_calibration import report\n"
    )
    violations = find_dependency_direction_violations("cli", fake_source)
    assert violations == []


# ---------------------------------------------------------------------------
# 6. 変更対象が自パッケージ・自テスト・自 Spec ディレクトリに閉じていること
# ---------------------------------------------------------------------------
#
# 実装方針についての判断（tasks.md タスク7.3の申し送り事項）:
# 「git diff ベースで特定のベース参照と比較する」方式は、浅いクローンや
# `main` ブランチが存在しない実行環境では前提が成立せず、テストが
# 「境界違反ではなく環境の問題」で赤くなってしまう。そこで、検査ロジック
# 本体（`find_forbidden_boundary_changes` / `find_out_of_boundary_changes`）
# は変更ファイルパスの**リスト**を受け取る純粋関数として切り出し、
# (a) 架空のファイルリストに対する検出テストで検査ロジック自体の正しさを
# 証明したうえで、(b) 実際の `git` 情報が利用可能な場合にのみ本物の
# working tree の差分（コミット済み: `git diff --name-only <merge-base>...HEAD`、
# 未コミット: `git status --porcelain`）を渡して実行する。(b) は git 情報が
# 取得できない環境では `pytest.skip` し、単独実行・繰り返し実行の頑健性を
# 損なわずに「使える時は本物の変更を確認する」実測を両立させる。

FORBIDDEN_BOUNDARY_PREFIXES: tuple[str, ...] = (
    "src/prediction_core/",
    "tests/prediction_core/",
    "src/sensing_foundation/",
)

ALLOWED_BOUNDARY_PREFIXES: tuple[str, ...] = (
    "src/world_frame_calibration/",
    "tests/world_frame_calibration/",
    ".kiro/specs/world-frame-calibration/",
)

# design.md Boundary Commitments: 「ルート `pyproject.toml` への追記」のみ許可。
ALLOWED_BOUNDARY_EXACT_FILES: frozenset[str] = frozenset({"pyproject.toml"})

#: 「このブランチに本 Spec の作業が載っているか」を判定するための、本 Spec が
#: **所有する**パス接頭辞（タスク 7.5）。現状の値は `ALLOWED_BOUNDARY_PREFIXES`
#: と一致するが、意味は異なる——あちらは「変更してよい場所」、こちらは
#: 「変更されていれば本 Spec の作業だと言える場所」である。
#:
#: `ALLOWED_BOUNDARY_EXACT_FILES`（ルート `pyproject.toml`）は**含めない**。
#: 共有の構成ファイルへ1行足しただけの他 Spec のブランチが、本 Spec の境界検査の
#: 対象になってしまうためである。
OWNED_BOUNDARY_PREFIXES: tuple[str, ...] = (
    "src/world_frame_calibration/",
    "tests/world_frame_calibration/",
    ".kiro/specs/world-frame-calibration/",
)


def _normalize(raw_path: str) -> str:
    return raw_path.strip().replace("\\", "/")


def find_forbidden_boundary_changes(changed_files: list[str]) -> list[str]:
    """変更ファイルパスの列から、明示的に禁止された3ディレクトリ配下のものを返す。

    空列であれば `src/prediction_core/**` / `tests/prediction_core/**` /
    `src/sensing_foundation/**` への変更が無い。
    """
    violations: list[str] = []
    for raw in changed_files:
        path = _normalize(raw)
        if not path:
            continue
        if any(path.startswith(prefix) for prefix in FORBIDDEN_BOUNDARY_PREFIXES):
            violations.append(path)
    return violations


def find_out_of_boundary_changes(changed_files: list[str]) -> list[str]:
    """変更ファイルパスの列から、許可された境界（自パッケージ・自テスト・
    自 Spec ディレクトリ・ルート `pyproject.toml`）の外にあるものを返す。

    空列であれば全変更が境界内に閉じている。
    """
    violations: list[str] = []
    for raw in changed_files:
        path = _normalize(raw)
        if not path:
            continue
        if path in ALLOWED_BOUNDARY_EXACT_FILES:
            continue
        if any(path.startswith(prefix) for prefix in ALLOWED_BOUNDARY_PREFIXES):
            continue
        violations.append(path)
    return violations


def contains_world_frame_calibration_changes(changed_files: list[str]) -> bool:
    """変更ファイルパスの列に、本 Spec が所有するパスが1つでも含まれるか（タスク 7.5）。

    `test_actual_working_tree_changes_since_main_stay_within_boundary` が
    「このブランチには本 Spec の作業しか載っていない」という**暗黙の前提**の上に
    立っていたため、他 Spec のブランチで走ると境界違反として報告されていた。
    この関数はその前提を明示的な判定へ置き換える——本 Spec の変更が1つも無い
    ブランチは、そもそも本 Spec の境界検査の対象ではない。

    `False` を返すのは「違反が無い」という意味ではなく「**検査対象ではない**」
    という意味である。`True` のときは従来どおり全変更が検査される
    （本 Spec の作業と越境が同居する場合は引き続き違反として報告される）。
    """
    for raw in changed_files:
        path = _normalize(raw)
        if not path:
            continue
        if any(path.startswith(prefix) for prefix in OWNED_BOUNDARY_PREFIXES):
            return True
    return False


def test_find_forbidden_boundary_changes_detects_named_directories_in_crafted_list() -> None:
    """違反ケース: 禁止3ディレクトリへの変更を含む架空のファイルリストが検出される。"""
    changed = [
        "src/world_frame_calibration/plan.py",
        "src/prediction_core/types.py",
        "tests/prediction_core/test_types.py",
        "src/sensing_foundation/geometry.py",
    ]
    violations = find_forbidden_boundary_changes(changed)
    assert set(violations) == {
        "src/prediction_core/types.py",
        "tests/prediction_core/test_types.py",
        "src/sensing_foundation/geometry.py",
    }


def test_find_forbidden_boundary_changes_accepts_own_boundary_only_in_crafted_list() -> None:
    """健全性確認: 自パッケージのみの変更リストには禁止パスが検出されない。"""
    changed = [
        "src/world_frame_calibration/plan.py",
        "tests/world_frame_calibration/test_world_frame_calibration_plan.py",
    ]
    assert find_forbidden_boundary_changes(changed) == []


def test_find_out_of_boundary_changes_flags_paths_outside_allowed_prefixes_in_crafted_list() -> (
    None
):
    """違反ケース: 許可プレフィックス外のパス（禁止3ディレクトリに限らない）が検出される。"""
    changed = [
        "src/world_frame_calibration/plan.py",
        "src/prediction_core/types.py",
        "tests/sensing_foundation/test_sensing_boundaries.py",
        "docs/requirements.md",
    ]
    violations = find_out_of_boundary_changes(changed)
    assert "src/prediction_core/types.py" in violations
    assert "tests/sensing_foundation/test_sensing_boundaries.py" in violations
    assert "docs/requirements.md" in violations
    assert "src/world_frame_calibration/plan.py" not in violations


def test_find_out_of_boundary_changes_accepts_all_allowed_paths_in_crafted_list() -> None:
    """健全性確認: 境界内パス（自パッケージ・自テスト・自 Spec・ルート pyproject.toml）
    のみの変更リストは空列を返す。
    """
    changed = [
        "src/world_frame_calibration/plan.py",
        "tests/world_frame_calibration/test_world_frame_calibration_plan.py",
        ".kiro/specs/world-frame-calibration/tasks.md",
        ".kiro/specs/world-frame-calibration/procedure.md",
        "pyproject.toml",
    ]
    assert find_out_of_boundary_changes(changed) == []


def test_find_out_of_boundary_changes_ignores_blank_lines_in_crafted_list() -> None:
    """誤検知回避: 空文字列・空白のみの行（git 出力の末尾等）は無視される。"""
    changed = ["src/world_frame_calibration/plan.py", "", "   "]
    assert find_out_of_boundary_changes(changed) == []


def test_find_out_of_boundary_changes_normalizes_windows_path_separators_in_crafted_list() -> (
    None
):
    """Windows 由来のバックスラッシュ区切りパスも正しく判定される。"""
    changed = ["src\\world_frame_calibration\\plan.py", "src\\prediction_core\\types.py"]
    violations = find_out_of_boundary_changes(changed)
    assert violations == ["src/prediction_core/types.py"]


def test_contains_wfc_changes_true_for_each_owned_prefix_in_crafted_list() -> None:
    """本 Spec が所有する3つの接頭辞は、それぞれ単独で「本 Spec の作業あり」と判定される。"""
    for owned in (
        "src/world_frame_calibration/plan.py",
        "tests/world_frame_calibration/test_world_frame_calibration_plan.py",
        ".kiro/specs/world-frame-calibration/tasks.md",
    ):
        assert contains_world_frame_calibration_changes([owned]) is True, owned


def test_contains_wfc_changes_false_for_other_spec_only_list() -> None:
    """他 Spec の変更しか無いブランチでは「本 Spec の作業なし」と判定される。"""
    changed = [
        "src/sensing_foundation/config.py",
        "src/sensing_foundation/bench/logging_overhead.py",
        "tests/sensing_foundation/test_bench_logging.py",
        ".kiro/specs/sensing-foundation/tasks.md",
    ]
    assert contains_world_frame_calibration_changes(changed) is False


def test_contains_wfc_changes_false_for_shared_pyproject_only() -> None:
    """ルート `pyproject.toml` は共有ファイルであり、本 Spec の作業の証拠にしない。

    `ALLOWED_BOUNDARY_EXACT_FILES` は「変更してよい場所」であって「本 Spec が
    所有する場所」ではない——ここを混同すると、`pyproject.toml` に1行足した
    他 Spec のブランチが本 Spec の境界検査の対象になってしまう。
    """
    assert contains_world_frame_calibration_changes(["pyproject.toml"]) is False


def test_contains_wfc_changes_normalizes_windows_path_separators() -> None:
    """Windows 由来のバックスラッシュ区切りパスでも判定できる。"""
    assert (
        contains_world_frame_calibration_changes(["src\\world_frame_calibration\\plan.py"])
        is True
    )


def test_contains_wfc_changes_ignores_blank_lines() -> None:
    """空文字列・空白のみの行（git 出力の末尾等）は無視される。"""
    assert contains_world_frame_calibration_changes(["", "   "]) is False


def test_mixed_branch_is_still_inspected_and_still_reports_violations() -> None:
    """検出能力を落としていないことの明示: 本 Spec の変更と越境が同居する列は、
    検査対象と判定され、かつ越境が引き続き報告される。

    タスク 7.5 が導入する skip は「本 Spec の変更が1つも無いとき」に限られる。
    1つでもあれば従来どおり全変更が検査される。
    """
    changed = [
        "src/world_frame_calibration/plan.py",
        "src/sensing_foundation/config.py",
    ]
    assert contains_world_frame_calibration_changes(changed) is True
    assert find_forbidden_boundary_changes(changed) == ["src/sensing_foundation/config.py"]


def test_other_spec_only_list_is_exactly_the_false_positive_being_fixed() -> None:
    """是正対象の誤検出を、そのままの形で固定する。

    `spec/sensing-foundation-bringup` で実際に起きていた状態（本 Spec の変更が
    1つも無いのに `find_forbidden_boundary_changes` が非空になる）を再現し、
    新しい判定がそれを検査対象外にすることを示す。
    """
    changed = ["src/sensing_foundation/config.py"]
    # 従来の判定単体では違反として報告される（＝ skip しなければ赤くなる）。
    assert find_forbidden_boundary_changes(changed) != []
    # しかし本 Spec の変更が1つも無いので、そもそも検査対象ではない。
    assert contains_world_frame_calibration_changes(changed) is False


def test_actual_working_tree_changes_since_main_stay_within_boundary() -> None:
    """本 Spec の実際の変更（コミット済み + 未コミット）が境界内に閉じている
    ことを、`main` ブランチとの merge-base からの実差分で確認する
    （tasks.md タスク7.3「変更対象が自パッケージ・自テスト・自 Spec
    ディレクトリに閉じていることを確認する」）。

    **前提（タスク 7.5 で明示化）**: 本検査は「作業ツリーの変更のうち本 Spec に
    属さないものは境界違反である」と読む。これは**このブランチに本 Spec の作業が
    載っている**ときにのみ成り立つ読み方であり、他 Spec のブランチでは
    その Spec の正当な変更をすべて違反として報告してしまう。したがって
    `contains_world_frame_calibration_changes()` が偽のとき、
    すなわち本 Spec の変更が1つも含まれないときは検査対象が無いものとして
    `pytest.skip` する。本 Spec の変更が1つでも含まれる場合は、
    **従来どおり全変更を検査する**（検出能力は落とさない）。

    `git` 自体、または `main` ブランチや共通祖先が見つからない環境では
    前提が成立しないため `pytest.skip` する（境界違反の有無とは無関係な
    環境要因でこのテストが赤くならないようにするため）。Windows 側で作成した
    git worktree を WSL から実行する場合、`.git` ファイルが指す `gitdir` が
    Windows 形式のパスであるため git がリポジトリを解決できず、この経路で
    skip する。
    """
    try:
        merge_base_result = subprocess.run(
            ["git", "merge-base", "main", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if merge_base_result.returncode != 0:
            pytest.skip(
                f"'main' との共通祖先が見つからないため skip: {merge_base_result.stderr.strip()}"
            )
        merge_base = merge_base_result.stdout.strip()

        diff_result = subprocess.run(
            ["git", "diff", "--name-only", f"{merge_base}...HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
    except (FileNotFoundError, OSError, subprocess.CalledProcessError) as exc:
        pytest.skip(f"git 情報が取得できないため skip: {exc}")
        return

    committed_files = [line for line in diff_result.stdout.splitlines() if line.strip()]
    # `git status --porcelain` は `XY path` 形式（XY はステータス2文字）。
    # パスはインデックス3以降。リネームの `old -> new` 形式は右辺のみを使う。
    uncommitted_files = []
    for line in status_result.stdout.splitlines():
        if not line.strip():
            continue
        raw_path = line[3:]
        if " -> " in raw_path:
            raw_path = raw_path.split(" -> ", 1)[1]
        uncommitted_files.append(raw_path)

    changed_files = committed_files + uncommitted_files
    if not changed_files:
        pytest.skip("main との差分が無いため境界検査の対象が無い")
    if not contains_world_frame_calibration_changes(changed_files):
        pytest.skip(
            "本 Spec の変更が1つも含まれないブランチのため境界検査の対象が無い"
            f"（変更 {len(changed_files)} 件はすべて本 Spec の所有外）"
        )

    forbidden = find_forbidden_boundary_changes(changed_files)
    assert forbidden == [], f"禁止ディレクトリへの変更を検出した: {forbidden}"

    out_of_boundary = find_out_of_boundary_changes(changed_files)
    assert out_of_boundary == [], f"境界外への変更を検出した: {out_of_boundary}"


# ---------------------------------------------------------------------------
# 7. 検出・追跡・予測に相当する処理が本パッケージに存在しないこと
#    （公開シンボル一覧と依存の両面から）
# ---------------------------------------------------------------------------

FORBIDDEN_PROCESSING_SUBSTRINGS: tuple[str, ...] = ("detect", "track", "predict")


def find_processing_substring_imports(source: str) -> list[str]:
    """ソース文字列の import 名の中に検出/追跡/予測相当の部分文字列を含む
    ものを検出する（大小文字を無視）。空列であれば違反なし。
    """
    tree = ast.parse(source)
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module:
                names.append(node.module)
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)

    lowered = [name.lower() for name in names]
    hits: list[str] = []
    for forbidden in FORBIDDEN_PROCESSING_SUBSTRINGS:
        hits.extend(name for name in lowered if forbidden in name)
    return hits


def test_no_module_imports_anything_resembling_detection_tracking_or_prediction() -> None:
    """`src/world_frame_calibration/**` のどのモジュールも検出/追跡/予測相当の
    ものを一切 import していない（要件 11.1, 11.2）。
    """
    for module_name, path in SOURCE_FILES.items():
        source = path.read_text(encoding="utf-8")
        hits = find_processing_substring_imports(source)
        assert hits == [], f"{module_name}.py に検出/追跡/予測相当の import: {hits}"


def test_public_symbol_list_has_no_detection_tracking_prediction_names() -> None:
    """公開シンボル一覧（タスク6.3で固定済み）に検出/追跡/予測相当の名前が無い。

    公開面と依存面の両方から「本パッケージが検出・追跡・予測に相当する処理を
    持たない」ことを固定する（タスク7.3の該当項目）。
    """
    lowered_all = [name.lower() for name in world_frame_calibration.__all__]
    for forbidden in FORBIDDEN_PROCESSING_SUBSTRINGS:
        offending = [name for name in lowered_all if forbidden in name]
        assert offending == [], f"__all__ に検出/追跡/予測相当のシンボル: {offending}"


@pytest.mark.parametrize(
    "fake_source",
    [
        "from some_tracker import Tracker\n",
        "import object_detector\n",
        "from prediction_engine import Predictor\n",
        "from flying_object_tracking import Detection\n",
    ],
)
def test_detects_processing_substring_in_crafted_source(fake_source: str) -> None:
    """違反ケース: 検出/追跡/予測相当の部分文字列を含む import 名が検出される。"""
    assert find_processing_substring_imports(fake_source) != []


def test_does_not_flag_unrelated_import_names_in_crafted_source() -> None:
    """誤検知回避: 検出/追跡/予測に無関係な import は検出されない。"""
    fake_source = "import numpy as np\nfrom world_frame_calibration.types import Plane\n"
    assert find_processing_substring_imports(fake_source) == []
