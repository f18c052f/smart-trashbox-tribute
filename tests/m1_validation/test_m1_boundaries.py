"""境界テスト: 依存方向・上流 import 集中・禁止 import・変更範囲の静的検証
(tasks.md タスク 8.4、要件 13.1, 13.2, 13.3, 13.4)。

design.md「Boundary Commitments / Allowed Dependencies」と
「Architecture / Dependency Direction」を、`src/m1_validation/**/*.py` の
ソースを `ast` で静的解析することにより検証する。実行時 import は使わない
（無関係な理由での推移的 import を誤検出しないため。`world_frame_calibration`
の境界テストと同じ方針）。

本テストが固定する点（tasks.md タスク8.4の箇条書きに対応）:

1. `sensing_foundation` を import するのが `upstream.py` 1モジュールに限ること
2. `flying_object_tracking` / `world_frame_calibration` を import するのが
   `seam.py` 1モジュールに限ること
3. 入口（`cli.py`）が上流3パッケージを直接 import していないこと
4. ピンホール逆投影の基本演算（`deproject_pixel` 等）をどのモジュールも
   直接 import していないこと（本 Spec はこの演算そのものを持たない）
5. 評価側（`truth` / `metrics.*` / `attribution` / `judgement.*` / `report` /
   `plot`）が上流3パッケージを import していないこと
6. 収集側（`upstream` / `seam` / `runner`）が `plot` を import していないこと、
   および `matplotlib` を import するのが `plot.py` だけであること
7. `cv2` / `pyrealsense2` / `numpy` を直接 import していないこと
8. 設計で定めた層をまたぐ逆方向の import が無いこと
   （`DEPENDENCY_ALLOWED_TARGETS` として符号化し、実際の import と突き合わせる）
9. 配布物の必須依存が空のままであること、および `m1-viz` extra が定義されて
   いること（上流の許可リスト側の検証は `tests/prediction_core/test_packaging.py`
   に委ねる。**同テストは改変しない**——本ファイルは自分が追記した内容だけを見る）
10. 変更対象が自パッケージ・自テスト・自 Spec ディレクトリ・
    ルート `pyproject.toml` への追記・`docs/requirements.md` §3 に閉じており、
    上流3パッケージのソースを変更していないこと

各チェックは「検査ロジックを純粋関数として切り出し、(a) 実際のソースに対して
違反ゼロであることを確認するテストと、(b) 意図的に違反を含む架空のソース
文字列/リストを渡すと検出できることを確認するテストの両方を書く」という
`tests/prediction_core/test_boundaries.py` の技法を踏襲する。

ファイル名について: `tests/prediction_core/test_boundaries.py` と衝突する
ため（`tests/` に `__init__.py` が無く、pytest の既定 import mode では
同名ファイルがツリー全体で一意でなければならない）、既存の回避規約
（`test_m1_determinism.py` と同じ理由）に従い `test_m1_boundaries.py` とする。

**既知の意図的な例外（design.md のテキストと実装の食い違い）**: design.md
「Dependency Direction」の ASCII 図は「評価側から収集側への import は禁止
（`truth` 以降が `upstream`/`seam`/`runner` を import しない）」と書いているが、
`metrics/latency.py`（タスク4.5・実装済み）は段階名の**定数だけ**を
`m1_validation.upstream` から借りている（同モジュール docstring 参照）。
これは「上流3パッケージ（`sensing_foundation` 等）を import しない」という
tasks.md タスク8.4 の**実際の文言**には反しない——`m1_validation.upstream` は
本 Spec 自身のモジュールであり、上流パッケージそのものではない。
`trajectory-simulator` の Implementation Notes が記録した前例
（design.md の記載不備を実装が正しく踏まえていた場合、境界検査側を実態に
合わせる）に倣い、`DEPENDENCY_ALLOWED_TARGETS["metrics.latency"]` にこの
1エッジを明示的に許可し、ここに理由を残す。
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - requires-python >= 3.11 なので到達しない
    import tomli as tomllib  # type: ignore[no-redef]

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src" / "m1_validation"


# ---------------------------------------------------------------------------
# ソースファイルの列挙
# ---------------------------------------------------------------------------


def _source_files() -> dict[str, Path]:
    """`src/m1_validation/**/*.py` を `{ドット区切りモジュール名: パス}` で返す。

    サブパッケージの `__init__.py` はパッケージ名そのもの
    （例: `metrics/__init__.py` → `"metrics"`）、トップレベルの
    `__init__.py` は `"__init__"` とする。
    """
    files: dict[str, Path] = {}
    for path in sorted(SRC_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel_parts = path.relative_to(SRC_DIR).with_suffix("").parts
        if rel_parts[-1] == "__init__":
            dotted = ".".join(rel_parts[:-1]) if len(rel_parts) > 1 else "__init__"
        else:
            dotted = ".".join(rel_parts)
        files[dotted] = path
    return files


SOURCE_FILES: dict[str, Path] = _source_files()


def test_source_files_enumeration_matches_expected_modules() -> None:
    """走査対象そのものを固定する。ここがずれると以降の全チェックが
    一部モジュールを見落としたまま「成功」してしまう。
    """
    assert set(SOURCE_FILES) == {
        "__init__",
        "attribution",
        "bench",
        "cli",
        "config",
        "errors",
        "layout",
        "plot",
        "report",
        "runner",
        "seam",
        "truth",
        "types",
        "upstream",
        "metrics",
        "metrics.accuracy",
        "metrics.aggregate",
        "metrics.convergence",
        "metrics.flight",
        "metrics.latency",
        "judgement",
        "judgement.budget",
        "judgement.oq05",
        "judgement.oq27",
    }


# ---------------------------------------------------------------------------
# 汎用ヘルパ（実ファイル・架空ソースの両方から使う）
# ---------------------------------------------------------------------------


def _imported_root_packages_from_tree(tree: ast.Module) -> set[str]:
    """ツリー全体（関数内も含む）を歩き、import されたトップレベル
    パッケージ名の集合を返す。`plot.py` の matplotlib のような**意図的な
    遅延 import**（関数内）も見逃さないため、モジュールレベルに限定しない。
    """
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                roots.add(node.module.split(".")[0])
    return roots


def _imported_root_packages(path: Path) -> set[str]:
    return _imported_root_packages_from_tree(ast.parse(path.read_text(encoding="utf-8")))


def _module_level_internal_imports_from_tree(tree: ast.Module) -> set[str]:
    """**モジュールレベルの import だけ**を対象に、`m1_validation.*` への
    参照先（ドット区切りの相対名）を返す。

    内部依存の層方向チェックには関数内 import を含めない
    （`sensing_foundation` タスク4.6 の先例と同じ理由: 正当な理由での
    関数内 import まで誤検出しないため）。本パッケージには現状そのような
    関数内の内部 import は無い（`grep` で確認済み）ので、この限定は
    実害なく安全側へ倒す。
    """
    targets: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            if node.module == "m1_validation":
                for alias in node.names:
                    targets.add(alias.name)
            elif node.module.startswith("m1_validation."):
                targets.add(node.module[len("m1_validation.") :])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("m1_validation."):
                    targets.add(alias.name[len("m1_validation.") :])
    return targets


def _module_level_internal_imports(path: Path) -> set[str]:
    return _module_level_internal_imports_from_tree(
        ast.parse(path.read_text(encoding="utf-8"))
    )


UPSTREAM_PACKAGES: frozenset[str] = frozenset(
    {"sensing_foundation", "flying_object_tracking", "world_frame_calibration"}
)
FORBIDDEN_THIRD_PARTY: frozenset[str] = frozenset({"numpy", "cv2", "pyrealsense2"})
DEPROJECTION_SYMBOLS: frozenset[str] = frozenset(
    {"deproject_pixel", "depth_raw_to_mm", "is_valid_depth", "CameraIntrinsics"}
)


# ---------------------------------------------------------------------------
# 1. sensing_foundation の接点は upstream.py だけ
# ---------------------------------------------------------------------------


def test_only_upstream_module_imports_sensing_foundation() -> None:
    offenders = {
        name: path
        for name, path in SOURCE_FILES.items()
        if name != "upstream" and "sensing_foundation" in _imported_root_packages(path)
    }
    assert offenders == {}
    assert "sensing_foundation" in _imported_root_packages(SOURCE_FILES["upstream"])


def test_detects_sensing_foundation_import_in_crafted_source() -> None:
    fake = ast.parse("from sensing_foundation import RuntimeSettings\n")
    assert "sensing_foundation" in _imported_root_packages_from_tree(fake)


def test_does_not_flag_unrelated_import_in_crafted_source() -> None:
    fake = ast.parse("from sensing_foundation_helpers import unrelated\n")
    # トップレベル名で判定するため、似た名前のパッケージは別物として扱われる。
    assert "sensing_foundation" not in _imported_root_packages_from_tree(fake)
    assert "sensing_foundation_helpers" in _imported_root_packages_from_tree(fake)


# ---------------------------------------------------------------------------
# 2. flying_object_tracking / world_frame_calibration の接点は seam.py だけ
# ---------------------------------------------------------------------------


def test_only_seam_module_imports_flying_object_tracking_and_world_frame_calibration() -> None:
    for package in ("flying_object_tracking", "world_frame_calibration"):
        offenders = {
            name: path
            for name, path in SOURCE_FILES.items()
            if name != "seam" and package in _imported_root_packages(path)
        }
        assert offenders == {}, (package, offenders)
    seam_roots = _imported_root_packages(SOURCE_FILES["seam"])
    assert {"flying_object_tracking", "world_frame_calibration"} <= seam_roots


def test_detects_world_frame_calibration_import_in_crafted_source() -> None:
    fake = ast.parse("import world_frame_calibration\n")
    assert "world_frame_calibration" in _imported_root_packages_from_tree(fake)


# ---------------------------------------------------------------------------
# 3. 入口（cli.py）は上流3パッケージを直接 import しない
# ---------------------------------------------------------------------------


def test_entrypoint_does_not_import_upstream_packages_directly() -> None:
    cli_roots = _imported_root_packages(SOURCE_FILES["cli"])
    assert cli_roots.isdisjoint(UPSTREAM_PACKAGES), cli_roots & UPSTREAM_PACKAGES


# ---------------------------------------------------------------------------
# 4. ピンホール逆投影の基本演算をどのモジュールも直接 import しない
# ---------------------------------------------------------------------------


def test_no_module_imports_pinhole_deprojection_symbols_directly() -> None:
    for name, path in SOURCE_FILES.items():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module is not None and node.module.endswith(".geometry"):
                    pytest.fail(f"{name} が geometry モジュールを直接 import している")
                for alias in node.names:
                    assert alias.name not in DEPROJECTION_SYMBOLS, (name, alias.name)


def test_detects_deproject_pixel_symbol_in_crafted_source() -> None:
    fake = ast.parse("from sensing_foundation import deproject_pixel\n")
    names = {
        alias.name
        for node in ast.walk(fake)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "deproject_pixel" in names & DEPROJECTION_SYMBOLS


# ---------------------------------------------------------------------------
# 5. 評価側は上流3パッケージを import しない
# ---------------------------------------------------------------------------

EVALUATION_SIDE_MODULES: tuple[str, ...] = (
    "truth",
    "metrics.accuracy",
    "metrics.aggregate",
    "metrics.convergence",
    "metrics.flight",
    "metrics.latency",
    "attribution",
    "judgement.budget",
    "judgement.oq05",
    "judgement.oq27",
    "report",
    "plot",
)


def test_evaluation_side_modules_do_not_import_upstream_packages() -> None:
    for name in EVALUATION_SIDE_MODULES:
        roots = _imported_root_packages(SOURCE_FILES[name])
        offending = roots & UPSTREAM_PACKAGES
        assert not offending, (name, offending)


def test_evaluation_side_module_list_matches_design_layer_map() -> None:
    """走査対象そのものが design.md の評価側の並び
    （`truth → metrics/* → attribution → judgement/* → report → plot`）と
    一致することを固定する。ここがずれると5番の検査が一部モジュールを
    見落としたまま「成功」する。
    """
    assert set(EVALUATION_SIDE_MODULES) == {
        name
        for name in SOURCE_FILES
        if name.split(".")[0]
        in {"truth", "metrics", "attribution", "judgement", "report", "plot"}
        and name not in {"metrics", "judgement"}
    }


# ---------------------------------------------------------------------------
# 6. 収集側は plot を import しない。matplotlib は plot.py だけ
# ---------------------------------------------------------------------------

COLLECTION_SIDE_MODULES: tuple[str, ...] = ("upstream", "seam", "runner")


def test_collection_side_modules_do_not_import_plot() -> None:
    for name in COLLECTION_SIDE_MODULES:
        internal = _module_level_internal_imports(SOURCE_FILES[name])
        assert "plot" not in internal, name


def test_matplotlib_is_imported_only_by_plot() -> None:
    offenders = {
        name: path
        for name, path in SOURCE_FILES.items()
        if name != "plot" and "matplotlib" in _imported_root_packages(path)
    }
    assert offenders == {}
    # `plot.py` 自身は要件 8.9（未導入環境での縮退）のためモジュールレベルでは
    # import しない（関数内の遅延 import のみ）。`ast.walk` はそれも拾う。
    assert "matplotlib" in _imported_root_packages(SOURCE_FILES["plot"])


def test_detects_matplotlib_import_inside_a_function_in_crafted_source() -> None:
    """遅延 import（関数内）も見逃さないことを確かめる——`plot.py` 自身が
    採用しているパターンそのものを架空ソースで再現する。
    """
    fake = ast.parse(
        "def matplotlib_backend():\n"
        "    import matplotlib.pyplot as plt\n"
        "    return plt\n"
    )
    assert "matplotlib" in _imported_root_packages_from_tree(fake)


# ---------------------------------------------------------------------------
# 7. 画像処理ライブラリ・カメラ SDK・数値計算ライブラリを直接 import しない
# ---------------------------------------------------------------------------


def test_no_module_imports_forbidden_third_party_packages_directly() -> None:
    for name, path in SOURCE_FILES.items():
        offending = _imported_root_packages(path) & FORBIDDEN_THIRD_PARTY
        assert not offending, (name, offending)


def test_detects_numpy_import_in_crafted_source() -> None:
    fake = ast.parse("import numpy as np\n")
    assert "numpy" in _imported_root_packages_from_tree(fake) & FORBIDDEN_THIRD_PARTY


# ---------------------------------------------------------------------------
# 8. 依存方向: 層をまたぐ逆方向の import が無い
# ---------------------------------------------------------------------------

#: design.md「Dependency Direction」を符号化した許可リスト。
#: `metrics.latency` → `upstream` は本ファイル冒頭の docstring
#: 「既知の意図的な例外」で説明した、実装済み・意図的な1エッジである。
DEPENDENCY_ALLOWED_TARGETS: dict[str, frozenset[str]] = {
    "__init__": frozenset(),
    "errors": frozenset(),
    "types": frozenset({"errors"}),
    "layout": frozenset({"errors"}),
    "config": frozenset({"errors", "layout"}),
    "upstream": frozenset({"errors"}),
    "seam": frozenset({"config", "errors", "types"}),
    "runner": frozenset({"config", "errors", "seam", "types", "upstream"}),
    "bench": frozenset({"config", "metrics.latency", "runner", "types"}),
    "truth": frozenset({"errors", "layout", "types"}),
    "metrics": frozenset(),
    "metrics.accuracy": frozenset({"errors", "types"}),
    "metrics.aggregate": frozenset(
        {
            "config",
            "errors",
            "metrics.accuracy",
            "metrics.convergence",
            "metrics.flight",
            "metrics.latency",
            "runner",
            "types",
        }
    ),
    "metrics.convergence": frozenset({"config", "errors", "metrics.accuracy", "types"}),
    "metrics.flight": frozenset({"errors", "layout", "types"}),
    "metrics.latency": frozenset({"errors", "upstream"}),
    "attribution": frozenset({"config", "errors", "metrics.aggregate", "types"}),
    "judgement": frozenset(),
    "judgement.budget": frozenset(
        {"config", "metrics.aggregate", "metrics.latency", "types"}
    ),
    "judgement.oq05": frozenset({"config", "metrics.aggregate", "types"}),
    "judgement.oq27": frozenset(
        {"config", "errors", "metrics.aggregate", "metrics.latency", "types"}
    ),
    "report": frozenset(
        {
            "attribution",
            "bench",
            "config",
            "errors",
            "judgement.budget",
            "judgement.oq05",
            "judgement.oq27",
            "metrics.aggregate",
            "types",
        }
    ),
    "plot": frozenset(
        {
            "attribution",
            "judgement.oq05",
            "layout",
            "metrics.accuracy",
            "metrics.aggregate",
            "metrics.convergence",
            "types",
        }
    ),
    "cli": frozenset(
        {
            "attribution",
            "bench",
            "config",
            "errors",
            "judgement.budget",
            "judgement.oq05",
            "judgement.oq27",
            "metrics.accuracy",
            "metrics.aggregate",
            "metrics.convergence",
            "metrics.flight",
            "metrics.latency",
            "plot",
            "report",
            "runner",
            "seam",
            "truth",
            "upstream",
        }
    ),
}


def test_dependency_allowed_targets_covers_exactly_the_source_modules() -> None:
    assert set(DEPENDENCY_ALLOWED_TARGETS) == set(SOURCE_FILES)


def test_dependency_direction_respected_by_all_source_modules() -> None:
    for name, path in SOURCE_FILES.items():
        actual = _module_level_internal_imports(path)
        allowed = DEPENDENCY_ALLOWED_TARGETS[name]
        unexpected = actual - allowed
        assert not unexpected, f"{name} は許可されていない内部依存を持つ: {unexpected}"


def test_detects_upward_dependency_from_errors_to_types_in_crafted_source() -> None:
    """違反ケース: 最下層（`errors`）が上位層（`types`）を import する。"""
    fake = ast.parse("from m1_validation.types import ThrowTruth\n")
    imports = _module_level_internal_imports_from_tree(fake)
    assert imports == {"types"}
    assert "types" not in DEPENDENCY_ALLOWED_TARGETS["errors"]


def test_detects_evaluation_side_importing_seam_in_crafted_source() -> None:
    """違反ケース: 評価側（`truth`）が収集側（`seam`）を import する。"""
    fake = ast.parse("from m1_validation.seam import build_seam\n")
    imports = _module_level_internal_imports_from_tree(fake)
    assert imports == {"seam"}
    assert "seam" not in DEPENDENCY_ALLOWED_TARGETS["truth"]


def test_multi_name_import_from_package_root_is_parsed_in_crafted_source() -> None:
    """`from m1_validation import bench, plot, report, seam, upstream` の
    ような、`cli.py` が使う複数名 import の形が正しく分解されることを確かめる。
    """
    fake = ast.parse("from m1_validation import bench, plot, seam\n")
    assert _module_level_internal_imports_from_tree(fake) == {"bench", "plot", "seam"}


def test_report_importing_judgement_oq27_is_allowed_as_a_sanity_check() -> None:
    """健全性確認: 許可されている辺（`report` → `judgement.oq27`）が
    誤って弾かれないこと。
    """
    assert "judgement.oq27" in DEPENDENCY_ALLOWED_TARGETS["report"]
    actual = _module_level_internal_imports(SOURCE_FILES["report"])
    assert "judgement.oq27" in actual


# ---------------------------------------------------------------------------
# 9. 配布物の必須依存は空のまま。m1-viz extra が定義されている
# ---------------------------------------------------------------------------

PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"


def test_pyproject_dependencies_stay_empty_and_m1_viz_extra_is_declared() -> None:
    """本 Spec が追記した内容だけを検証する。

    `set(optional-dependencies) <= ALLOWED_OPTIONAL_EXTRAS` の側は
    `tests/prediction_core/test_packaging.py::test_no_third_party_runtime_dependencies`
    が既に守っている——**同テストを改変せず、ここでも複製しない**
    （design.md「Allowed Dependencies」の Prerequisite 節どおり）。
    """
    data = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    project = data["project"]
    assert project.get("dependencies") == []
    optional = project.get("optional-dependencies", {})
    assert "m1-viz" in optional
    assert any(dep.startswith("matplotlib") for dep in optional["m1-viz"])


# ---------------------------------------------------------------------------
# 10. 変更対象が境界内に閉じている（git 差分の静的検証）
# ---------------------------------------------------------------------------
#
# 実装方針: `world_frame_calibration` の境界テストと同じ理由で、検査ロジック
# 本体を「変更ファイルパスの列を受け取る純粋関数」として切り出す。(a) 架空の
# ファイルリストに対する検出テストで検査ロジック自体を証明し、(b) git 情報が
# 利用可能な場合にのみ実際の working tree の差分を渡して実行する（利用不可な
# 環境では skip）。
#
# 未コミット側とコミット側を**対称に**扱う（world_frame_calibration タスク9.2
# の教訓）: 両方をまとめた1つの変更ファイル列に対して「本 Spec の所有パスを
# 1つでも含むか」を判定し、含まない場合のみ検査対象から外す。

FORBIDDEN_BOUNDARY_PREFIXES: tuple[str, ...] = (
    "src/prediction_core/",
    "tests/prediction_core/",
    "src/sensing_foundation/",
    "tests/sensing_foundation/",
    "src/flying_object_tracking/",
    "tests/flying_object_tracking/",
    "src/world_frame_calibration/",
    "tests/world_frame_calibration/",
)

ALLOWED_BOUNDARY_PREFIXES: tuple[str, ...] = (
    "src/m1_validation/",
    "tests/m1_validation/",
    ".kiro/specs/m1-prediction-validation/",
)

#: design.md「Boundary Commitments」: ルート `pyproject.toml` への追記と
#: `docs/requirements.md`（§3 のみ。内容の検証は別関数で行う）。
ALLOWED_BOUNDARY_EXACT_FILES: frozenset[str] = frozenset(
    {"pyproject.toml", "docs/requirements.md"}
)

#: 「このブランチに本 Spec の作業が載っているか」の判定に使う所有パス
#: （`ALLOWED_BOUNDARY_EXACT_FILES` は含めない——共有ファイルへ1行足しただけの
#: 他 Spec の変更を、本 Spec の作業の証拠にしないため）。
OWNED_BOUNDARY_PREFIXES: tuple[str, ...] = ALLOWED_BOUNDARY_PREFIXES

REQUIREMENTS_MD_PATH_STR = "docs/requirements.md"


def _normalize(raw_path: str) -> str:
    return raw_path.strip().replace("\\", "/")


def find_forbidden_boundary_changes(changed_files: list[str]) -> list[str]:
    violations: list[str] = []
    for raw in changed_files:
        path = _normalize(raw)
        if not path:
            continue
        if any(path.startswith(prefix) for prefix in FORBIDDEN_BOUNDARY_PREFIXES):
            violations.append(path)
    return violations


def find_out_of_boundary_changes(changed_files: list[str]) -> list[str]:
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


def contains_m1_validation_changes(changed_files: list[str]) -> bool:
    for raw in changed_files:
        path = _normalize(raw)
        if not path:
            continue
        if any(path.startswith(prefix) for prefix in OWNED_BOUNDARY_PREFIXES):
            return True
    return False


def test_find_forbidden_boundary_changes_detects_named_directories_in_crafted_list() -> None:
    changed = [
        "src/m1_validation/report.py",
        "src/prediction_core/types.py",
        "tests/flying_object_tracking/test_x.py",
    ]
    violations = find_forbidden_boundary_changes(changed)
    assert set(violations) == {
        "src/prediction_core/types.py",
        "tests/flying_object_tracking/test_x.py",
    }


def test_find_forbidden_boundary_changes_accepts_own_boundary_only_in_crafted_list() -> None:
    changed = ["src/m1_validation/report.py", "tests/m1_validation/test_m1_report.py"]
    assert find_forbidden_boundary_changes(changed) == []


def test_find_out_of_boundary_changes_flags_paths_outside_allowed_prefixes_in_crafted_list() -> (
    None
):
    changed = [
        "src/m1_validation/report.py",
        "src/prediction_core/types.py",
        "docs/decisions.md",
    ]
    violations = find_out_of_boundary_changes(changed)
    assert "src/prediction_core/types.py" in violations
    assert "docs/decisions.md" in violations
    assert "src/m1_validation/report.py" not in violations


def test_find_out_of_boundary_changes_accepts_all_allowed_paths_in_crafted_list() -> None:
    changed = [
        "src/m1_validation/report.py",
        "tests/m1_validation/test_m1_report.py",
        ".kiro/specs/m1-prediction-validation/tasks.md",
        "pyproject.toml",
        "docs/requirements.md",
    ]
    assert find_out_of_boundary_changes(changed) == []


def test_find_out_of_boundary_changes_ignores_blank_lines_in_crafted_list() -> None:
    assert find_out_of_boundary_changes(["src/m1_validation/report.py", "", "   "]) == []


def test_find_out_of_boundary_changes_normalizes_windows_path_separators_in_crafted_list() -> (
    None
):
    changed = ["src\\m1_validation\\report.py", "src\\prediction_core\\types.py"]
    assert find_out_of_boundary_changes(changed) == ["src/prediction_core/types.py"]


def test_contains_m1_validation_changes_true_for_each_owned_prefix_in_crafted_list() -> None:
    for owned in (
        "src/m1_validation/report.py",
        "tests/m1_validation/test_m1_report.py",
        ".kiro/specs/m1-prediction-validation/tasks.md",
    ):
        assert contains_m1_validation_changes([owned]) is True, owned


def test_contains_m1_validation_changes_false_for_other_spec_only_list() -> None:
    changed = [
        "src/world_frame_calibration/frame.py",
        ".kiro/specs/world-frame-calibration/tasks.md",
    ]
    assert contains_m1_validation_changes(changed) is False


def test_contains_m1_validation_changes_false_for_shared_files_only() -> None:
    """`pyproject.toml` / `docs/requirements.md` は共有ファイルであり、
    本 Spec の作業の証拠にしない（他 Spec がここへ1行足しただけのブランチを
    誤って検査対象にしないため）。
    """
    assert contains_m1_validation_changes(["pyproject.toml", "docs/requirements.md"]) is False


def test_mixed_changes_are_still_inspected_and_still_reported() -> None:
    """本 Spec の変更と越境が同居する場合は、検査対象と判定され、かつ
    越境が引き続き報告される（検出能力を落とさないことの明示）。
    """
    changed = ["src/m1_validation/report.py", "src/world_frame_calibration/frame.py"]
    assert contains_m1_validation_changes(changed) is True
    assert find_forbidden_boundary_changes(changed) == ["src/world_frame_calibration/frame.py"]


def _find_section_3_bounds(lines: Sequence[str]) -> tuple[int, int]:
    """`docs/requirements.md` の `## 3. ...` 見出しの行範囲（1始まり、両端含む）
    を返す。次の `## ` 見出しの直前まで、無ければファイル末尾までとする。
    """
    headings = [
        (index + 1, line) for index, line in enumerate(lines) if line.startswith("## ")
    ]
    for position, (lineno, text) in enumerate(headings):
        if text.startswith("## 3."):
            end = headings[position + 1][0] - 1 if position + 1 < len(headings) else len(lines)
            return lineno, end
    raise AssertionError("docs/requirements.md に `## 3.` 見出しが見つからない")


_HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)


def requirements_md_changes_stay_within_section_3(
    diff_text: str, *, file_lines: Sequence[str]
) -> bool:
    """`git diff -U0 -- docs/requirements.md` の出力を受け取り、変更行が
    すべて §3 の行範囲に収まっているかを判定する（要件 13.4 の「対象
    ドキュメントの1節に閉じている」の内容チェック）。
    """
    section_start, section_end = _find_section_3_bounds(file_lines)
    for match in _HUNK_HEADER_RE.finditer(diff_text):
        start = int(match.group(1))
        count = int(match.group(2)) if match.group(2) else 1
        if count == 0:
            # 純削除の hunk。`start` は削除位置の直前行を指す規約なので、
            # 削除箇所の実際の行は `start + 1` 相当だが、安全側に倒して
            # `start` そのものが範囲内であることを要求する。
            end = start
        else:
            end = start + count - 1
        if not (section_start <= start and end <= section_end):
            return False
    return True


def test_requirements_md_section_3_bounds_are_found_in_the_actual_file() -> None:
    lines = (REPO_ROOT / "docs" / "requirements.md").read_text(encoding="utf-8").splitlines()
    start, end = _find_section_3_bounds(lines)
    assert lines[start - 1].startswith("## 3.")
    assert start < end


def test_requirements_md_changes_within_section_3_are_accepted_in_crafted_diff() -> None:
    file_lines = ["# doc", "## 2. x", "body", "## 3. y", "body", "body", "## 4. z", "body"]
    diff_text = "@@ -4,2 +4,2 @@\n-old\n+new\n"
    assert requirements_md_changes_stay_within_section_3(diff_text, file_lines=file_lines)


def test_requirements_md_changes_outside_section_3_are_detected_in_crafted_diff() -> None:
    file_lines = ["# doc", "## 2. x", "body", "## 3. y", "body", "body", "## 4. z", "body"]
    # 2行目（§2 側）を触る架空の hunk。
    diff_text = "@@ -2,1 +2,1 @@\n-old\n+new\n"
    assert not requirements_md_changes_stay_within_section_3(diff_text, file_lines=file_lines)


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    return result.stdout


def test_actual_working_tree_changes_stay_within_boundary() -> None:
    """実際の working tree（コミット済み + 未コミット）を検査する。
    git 情報が使えない環境では skip する（純粋関数側は上のテストで
    既に検出できることを証明済みなので、頑健性を損なわない）。
    """
    try:
        subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        _git("rev-parse", "main")
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("git、または main ブランチが利用できない環境")

    committed = [
        line for line in _git("diff", "--name-only", "main...HEAD").splitlines() if line.strip()
    ]
    status_lines = [line for line in _git("status", "--porcelain").splitlines() if line.strip()]
    uncommitted = [line[3:] for line in status_lines]

    changed = committed + uncommitted
    if not contains_m1_validation_changes(changed):
        pytest.skip("このブランチに m1-prediction-validation の変更が無い")

    assert find_forbidden_boundary_changes(changed) == []
    assert find_out_of_boundary_changes(changed) == []

    if REQUIREMENTS_MD_PATH_STR in {_normalize(path) for path in changed}:
        diff_text = _git("diff", "-U0", "main...HEAD", "--", REQUIREMENTS_MD_PATH_STR)
        file_lines = (REPO_ROOT / REQUIREMENTS_MD_PATH_STR).read_text(
            encoding="utf-8"
        ).splitlines()
        assert requirements_md_changes_stay_within_section_3(diff_text, file_lines=file_lines)
