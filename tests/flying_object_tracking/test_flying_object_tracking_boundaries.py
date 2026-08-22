"""境界と依存ゼロの回帰テスト（要件 1.5, 5.3, 5.8, 7.4, 7.5, 7.8,
13.1〜13.6、タスク 8.2）。

`src/flying_object_tracking/**/*.py` の import 文とソースを `ast` による
静的解析で走査し、design.md「境界テスト（回帰）」節が列挙する以下の8点を
固定する（`prediction-core` / `sensing-foundation` の先例
`tests/prediction_core/test_boundaries.py` の技法をそのまま踏襲する）。

1. **`prediction_core` を import しているモジュールが1つも無い**こと
   （要件 5.3 / 7.4 / 13.6。World 変換を書きようがない状態の担保）。
2. `sensing_foundation` の**内部モジュール**（`sensing_foundation.types` 等）
   への直接 import が無いこと（要件 1.5。公開入口 `sensing_foundation`
   トップレベルからのみ import する）。
3. **`cv2` を import しているモジュールが1つだけ**（`detection/mask_ops.py`）
   であること。
4. design.md「Dependency Direction」表に反する import が無いこと。
   特に `tracking` が `detection` / `projection` を import しないこと。
5. `projection.py` が逆投影の基本演算（ピンホール式・`depth_scale_mm` の
   直接乗算による mm 換算・無効 Depth の直値比較）を自前で持たないこと
   （要件 5.8）。
6. `pyproject.toml` の `[project].dependencies` が空であり、
   `[project.optional-dependencies].tracking` に numpy と GUI 無し
   OpenCV が宣言されていること（要件 13.3 / 13.4）。
7. `src/prediction_core/**` / `src/sensing_foundation/**` へ抜ける相対
   import が無いこと（要件 13.1 / 13.5）。
8. 予測の入力契約にカメラ固有の型が漏れていないことの、予測コア非依存の
   検証（要件 7.4 / 13.6。1. で `prediction_core` を一切 import しないと
   証明されるため、`prediction_core.Sample` 等の入力型を本パッケージが
   構築しようがないことが構造的に導かれる）。

**本ファイルは `flying_object_tracking` を一切 import しない**（5.1/1.4の
申し送りと同じ理由: `__init__.py` がパッケージ全体を import してしまうと
個別モジュールの実行時依存を独立検証できなくなる）。ソースファイルを常に
テキストとして読み、`ast.parse` で静的解析するのみである。

**観測可能な完了状態（タスク文言どおり）**: 「許可外の import や依存方向の
逆流、逆投影の自前実装を故意に入れると失敗し、現状のコードでは成功する
テストが通る」ことを、本物のプロダクションファイルを書き換える代わりに、
検査ロジックを関数として切り出し、その関数へ「違反を含む架空のソース
文字列」を渡すテストケース群（`test_detects_*` 系）と、「現状の実コード」
を渡すテストケース群の両方で証明する。

**ファイル名の衝突について**: `tests/` 配下には `__init__.py` が無く
（`tests/prediction_core/` 等いずれのテストディレクトリにも存在しない）、
pytest はテストファイルをパッケージ修飾なしの平文モジュール名で
`sys.modules` へ登録する（rootdir 相対のパッケージ名前空間を持たない）。
`tests/prediction_core/test_boundaries.py` が既に存在するため、
本ファイルを同名 `test_boundaries.py` として置くと
`import file mismatch`（モジュール名衝突）で collection が失敗する
（実際に確認済み）。本テストディレクトリの他の全ファイルが既に
`test_flying_object_tracking_*.py` という接頭辞付きの命名規約
（同ディレクトリの `test_flying_object_tracking_tracker_boundaries.py` が
前例）でこの衝突を避けているため、本ファイルも同じ規約に従い
`test_flying_object_tracking_boundaries.py` という名前にする。
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src" / "flying_object_tracking"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"


def _source_files() -> dict[str, Path]:
    """`src/flying_object_tracking/**/*.py` を `{正規化モジュール名: パス}` で返す。

    正規化モジュール名は `flying_object_tracking.` プレフィックスを除いた
    ドット区切りパスであり、パッケージの `__init__.py` は
    `<パッケージ>.__init__`（ルート自身は `__init__`）と表す
    （例: `detection/masks/depth_band.py` -> `detection.masks.depth_band`、
    `detection/masks/__init__.py` -> `detection.masks.__init__`）。
    """
    result: dict[str, Path] = {}
    for path in sorted(SRC_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel_parts = list(path.relative_to(SRC_DIR).parts)
        rel_parts[-1] = "__init__" if rel_parts[-1] == "__init__.py" else Path(rel_parts[-1]).stem
        result[".".join(rel_parts)] = path
    return result


SOURCE_FILES: dict[str, Path] = _source_files()


def test_source_files_enumeration_matches_expected_modules() -> None:
    """`SOURCE_FILES` が想定どおりの20モジュールを走査対象にしている。

    ここがずれると、以降の全チェックが一部モジュールを見落としたまま
    「成功」してしまうため、走査対象自体を固定する。
    """
    assert set(SOURCE_FILES) == {
        "__init__",
        "errors",
        "types",
        "config",
        "metrics",
        "detection.__init__",
        "detection.mask_ops",
        "detection.candidates",
        "detection.detector",
        "detection.masks.__init__",
        "detection.masks.background",
        "detection.masks.depth_band",
        "detection.masks.frame_diff",
        "projection",
        "tracking",
        "pipeline",
        "bench.__init__",
        "bench.compare",
        "bench.overhead",
        "cli",
    }


# ---------------------------------------------------------------------------
# 共通: 実行時 import 文の静的抽出（`tests/prediction_core/test_boundaries.py`
# の `_RuntimeImportCollector` / `collect_runtime_imports` と同じ技法）
# ---------------------------------------------------------------------------


def _is_type_checking_test(test: ast.expr) -> bool:
    """`if TYPE_CHECKING:` ガードかどうかを判定する（`typing.TYPE_CHECKING` 形も許容）。"""
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    if isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
        return True
    return False


class _RuntimeImportCollector(ast.NodeVisitor):
    """実行時に評価される import 文だけを `(モジュール名, 行番号)` として収集する。

    `if TYPE_CHECKING:` ブロック内の import は実行時 import を作らないため、
    誤検知を避けて意図的に走査対象から外す（`orelse` は通常どおり走査する）。
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
    """ソース文字列から実行時 import 文を `(モジュール名, 行番号)` の列として抽出する。"""
    tree = ast.parse(source)
    collector = _RuntimeImportCollector()
    collector.visit(tree)
    return collector.imports


# ---------------------------------------------------------------------------
# 1. `prediction_core` を import しているモジュールが1つも無いこと
#    （要件 5.3 / 7.4 / 13.6）
# ---------------------------------------------------------------------------


def find_prediction_core_imports(source: str) -> list[str]:
    """`prediction_core`（またはそのサブモジュール）への import を検出する。

    違反を `"モジュール名 (line N)"` の列として返す。空列であれば違反なし。
    """
    violations = []
    for module, lineno in collect_runtime_imports(source):
        if module.startswith("."):
            continue
        if module.split(".")[0] == "prediction_core":
            violations.append(f"{module} (line {lineno})")
    return violations


def test_no_module_imports_prediction_core() -> None:
    """`src/flying_object_tracking/**` の全ファイルに `prediction_core` の import が無い。

    World frame への変換（`prediction_core.Sample` の構成）を本 Spec が
    書きようがないことの担保そのものである（requirements.md A-2）。
    """
    for module_name, path in SOURCE_FILES.items():
        source = path.read_text(encoding="utf-8")
        violations = find_prediction_core_imports(source)
        assert violations == [], f"{module_name} に prediction_core の import: {violations}"


def test_detects_prediction_core_import_in_crafted_source() -> None:
    """違反ケース: `from prediction_core import Sample` を含む架空のソースが検出される。"""
    fake_source = "from prediction_core import Sample\n"
    violations = find_prediction_core_imports(fake_source)
    assert violations != []
    assert "prediction_core" in violations[0]


def test_detects_prediction_core_submodule_import_in_crafted_source() -> None:
    """違反ケース: `import prediction_core.record` を含む架空のソースが検出される。"""
    fake_source = "import prediction_core.record\n"
    violations = find_prediction_core_imports(fake_source)
    assert violations != []


# ---------------------------------------------------------------------------
# 2. `sensing_foundation` の内部モジュールへの直接 import が無いこと（要件 1.5）
# ---------------------------------------------------------------------------


def find_sensing_foundation_internal_imports(source: str) -> list[str]:
    """`sensing_foundation` の公開入口（トップレベル）以外への import を検出する。

    `from sensing_foundation import X`（または `import sensing_foundation`）は
    公開入口として許可する。`sensing_foundation.geometry` 等のサブモジュールへ
    直接触れる import を違反として `"モジュール名 (line N)"` の列で返す。
    """
    violations = []
    for module, lineno in collect_runtime_imports(source):
        if module.startswith("."):
            continue
        if module == "sensing_foundation":
            continue
        if module.startswith("sensing_foundation."):
            violations.append(f"{module} (line {lineno})")
    return violations


def test_no_module_imports_sensing_foundation_internal_submodule() -> None:
    """`src/flying_object_tracking/**` の全ファイルが `sensing_foundation` の
    公開入口（トップレベル）だけを import している（要件 1.5）。
    """
    for module_name, path in SOURCE_FILES.items():
        source = path.read_text(encoding="utf-8")
        violations = find_sensing_foundation_internal_imports(source)
        assert violations == [], f"{module_name} に sensing_foundation 内部 import: {violations}"


def test_detects_sensing_foundation_internal_from_import_in_crafted_source() -> None:
    """違反ケース: `from sensing_foundation.geometry import is_valid_depth` が検出される。"""
    fake_source = "from sensing_foundation.geometry import is_valid_depth\n"
    violations = find_sensing_foundation_internal_imports(fake_source)
    assert violations != []
    assert "sensing_foundation.geometry" in violations[0]


def test_detects_sensing_foundation_internal_plain_import_in_crafted_source() -> None:
    """違反ケース: `import sensing_foundation.types` が検出される。"""
    fake_source = "import sensing_foundation.types\n"
    violations = find_sensing_foundation_internal_imports(fake_source)
    assert violations != []


def test_public_entry_point_import_is_not_flagged_in_crafted_source() -> None:
    """誤検知回避: `from sensing_foundation import CaptureFrame` は違反ではない。"""
    fake_source = "from sensing_foundation import CaptureFrame, INVALID_DEPTH_RAW\n"
    assert find_sensing_foundation_internal_imports(fake_source) == []


# ---------------------------------------------------------------------------
# 3. `cv2` を import しているモジュールが1つだけであること
# ---------------------------------------------------------------------------


def find_cv2_imports(source: str) -> list[str]:
    """`cv2`（またはそのサブモジュール）への import を検出する。"""
    violations = []
    for module, lineno in collect_runtime_imports(source):
        if module.startswith("."):
            continue
        if module == "cv2" or module.startswith("cv2."):
            violations.append(f"{module} (line {lineno})")
    return violations


def test_exactly_one_module_imports_cv2() -> None:
    """`src/flying_object_tracking/**` のうち `cv2` を import しているのは
    `detection/mask_ops.py` だけである（design.md「Dependency Direction」表:
    layer 3 `detection/mask_ops` のみが `cv2` を持つ）。
    """
    modules_with_cv2 = []
    for module_name, path in SOURCE_FILES.items():
        source = path.read_text(encoding="utf-8")
        if find_cv2_imports(source):
            modules_with_cv2.append(module_name)
    assert modules_with_cv2 == ["detection.mask_ops"]


def test_detects_cv2_import_in_crafted_source() -> None:
    """違反ケース: `import cv2` を含む架空のソース（`mask_ops.py` 以外を想定）が検出される。"""
    fake_source = "import cv2\n"
    violations = find_cv2_imports(fake_source)
    assert violations != []


def test_detects_cv2_from_import_in_crafted_source() -> None:
    """違反ケース: `from cv2 import morphologyEx` を含む架空のソースが検出される。"""
    fake_source = "from cv2 import morphologyEx\n"
    assert find_cv2_imports(fake_source) != []


# ---------------------------------------------------------------------------
# 4. Dependency Direction 表に反する import が無いこと
#    （特に `tracking` が `detection` / `projection` を import しないこと）
# ---------------------------------------------------------------------------


def _canonical_internal_target(module: str, alias_name: str | None) -> str:
    """`flying_object_tracking.` 配下の import 文が指す正規化モジュール名を返す。

    `from flying_object_tracking.detection import mask_ops` のように
    パッケージから直接サブモジュールを import する形と、
    `from flying_object_tracking.config import ObjectModelConfig` のように
    モジュール内のシンボルを import する形を区別する必要がある。
    `モジュール名 + "." + インポートされた名前` が既知のサブモジュール名
    （`SOURCE_FILES` のキー）と一致する場合はそちらを対象とみなし、
    一致しなければ `モジュール名` 自体（一致しなければそのパッケージの
    `__init__`）を対象とみなす。
    """
    stripped = module[len("flying_object_tracking") :].lstrip(".")
    base = stripped if stripped else "__init__"
    if alias_name is not None:
        candidate = f"{base}.{alias_name}" if base != "__init__" else alias_name
        if candidate in SOURCE_FILES:
            return candidate
    if base in SOURCE_FILES:
        return base
    if f"{base}.__init__" in SOURCE_FILES:
        return f"{base}.__init__"
    return base


class _InternalImportCollector(ast.NodeVisitor):
    """`flying_object_tracking` 配下への import 文だけを
    `(モジュール名, インポートされた名前 or None, 行番号)` として収集する。

    `_RuntimeImportCollector` と同じ `TYPE_CHECKING` ガード除外を行う。
    相対 import（`node.level > 0`）はここでは対象にしない（チェック7が扱う）。
    """

    def __init__(self) -> None:
        self.entries: list[tuple[str, str | None, int]] = []

    def visit_If(self, node: ast.If) -> None:  # noqa: N802
        if _is_type_checking_test(node.test):
            for child in node.orelse:
                self.visit(child)
            return
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            if alias.name == "flying_object_tracking" or alias.name.startswith(
                "flying_object_tracking."
            ):
                self.entries.append((alias.name, None, node.lineno))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        if node.level and node.level > 0:
            return
        module = node.module or ""
        if module == "flying_object_tracking" or module.startswith("flying_object_tracking."):
            for alias in node.names:
                self.entries.append((module, alias.name, node.lineno))


def collect_internal_import_targets(source: str) -> list[tuple[str, int]]:
    """ソース文字列から `flying_object_tracking` 内部 import を
    `(正規化モジュール名, 行番号)` の列として抽出する。
    """
    tree = ast.parse(source)
    collector = _InternalImportCollector()
    collector.visit(tree)
    return [
        (_canonical_internal_target(module, alias_name), lineno)
        for module, alias_name, lineno in collector.entries
    ]


_L0: frozenset[str] = frozenset()  # errors
_L1: frozenset[str] = _L0 | {"errors"}  # types
_L2: frozenset[str] = _L1 | {"types"}  # config
_L3_BASE: frozenset[str] = _L2 | {"config"}  # metrics / detection.mask_ops / projection / tracking
_MASK_FAMILY_BASE: frozenset[str] = _L3_BASE | {"metrics", "detection.mask_ops"}
_L5_BASE: frozenset[str] = _MASK_FAMILY_BASE | {
    "detection.masks.__init__",
    "detection.masks.background",
    "detection.masks.depth_band",
    "detection.masks.frame_diff",
    "detection.candidates",
}
_L7_BASE: frozenset[str] = _L5_BASE | {"detection.detector", "projection", "tracking"}
_L8_BASE: frozenset[str] = _L7_BASE | {"pipeline"}
_L9_BASE: frozenset[str] = _L8_BASE | {"bench.__init__", "bench.compare", "bench.overhead"}

DEPENDENCY_ALLOWED_TARGETS: dict[str, frozenset[str]] = {
    # design.md「Dependency Direction」表（層0〜9）をそのまま許可リストとして表現する。
    "errors": _L0,  # L0: 標準ライブラリのみ
    "types": _L1,  # L1
    "config": _L2,  # L2
    "metrics": _L3_BASE,  # L3
    "detection.__init__": frozenset(),  # サブパッケージマーカー。現状 import なし
    "detection.mask_ops": _L3_BASE,  # L3（cv2 を持つ唯一のモジュール。内部 import は0〜2のみ）
    "detection.candidates": _MASK_FAMILY_BASE,  # L4 相当（candidates.py 自身の docstring が明言）
    "detection.masks.__init__": _MASK_FAMILY_BASE
    | {
        # レジストリ集約（`create_mask_builder`）としての意図的な兄弟 import
        "detection.masks.background",
        "detection.masks.depth_band",
        "detection.masks.frame_diff",
    },
    "detection.masks.background": _MASK_FAMILY_BASE,  # L4
    "detection.masks.depth_band": _MASK_FAMILY_BASE,  # L4
    "detection.masks.frame_diff": _MASK_FAMILY_BASE,  # L4
    "detection.detector": _L5_BASE,  # L5（0〜4）
    "projection": _L3_BASE,  # L5（0〜2のみ。`detection` を import しない。要件 5.8）
    "tracking": _L3_BASE,  # L6（0〜2のみ。`detection` / `projection` を import しない）
    "pipeline": _L7_BASE,  # L7（0〜6）
    "bench.__init__": frozenset(),  # サブパッケージマーカー。現状 import なし
    "bench.compare": _L8_BASE,  # L8（0〜7）
    "bench.overhead": _L8_BASE,  # L8（0〜7）
    "cli": _L9_BASE,  # L9（0〜8）
    "__init__": _L9_BASE,  # L9（0〜8。再エクスポートのみ）
}


def find_dependency_direction_violations(module_name: str, source: str) -> list[str]:
    """`module_name` のソースから、依存方向表に無い `flying_object_tracking` 内部 import を検出する。

    違反を `"module_name -> target (line N)"` の列として返す。空列であれば違反なし。
    """
    allowed = DEPENDENCY_ALLOWED_TARGETS[module_name]
    violations = []
    for target, lineno in collect_internal_import_targets(source):
        if target == module_name:
            continue  # 自己参照（実際には起こらないがガード）
        if target not in allowed:
            violations.append(f"{module_name} -> {target} (line {lineno})")
    return violations


def test_dependency_direction_respected_by_all_source_modules() -> None:
    """`src/flying_object_tracking/**` の全ファイルが依存方向表に従う。"""
    for module_name, path in SOURCE_FILES.items():
        source = path.read_text(encoding="utf-8")
        violations = find_dependency_direction_violations(module_name, source)
        assert violations == [], f"{module_name} の依存方向違反: {violations}"


def test_tracking_does_not_import_detection_or_projection() -> None:
    """`tracking`（L6）が `detection` / `projection` を import していない。

    design.md が「追跡は時刻付きカメラ座標点の列だけを扱い、その点がどの
    検出方式・逆投影経路から来たかを知らない」と明記する設計判断の根幹
    であるため、依存方向表の一部としてだけでなく単独でも固定する
    （要件 11.2 の合成軌道テストが検出を経由せずに追跡を検証できることの前提）。
    """
    source = SOURCE_FILES["tracking"].read_text(encoding="utf-8")
    targets = {target for target, _ in collect_internal_import_targets(source)}
    assert not any(target == "projection" or target.startswith("detection") for target in targets)


def test_projection_does_not_import_detection() -> None:
    """`projection`（L5）が `detection` を import していない（design.md `projection` 行）。"""
    source = SOURCE_FILES["projection"].read_text(encoding="utf-8")
    targets = {target for target, _ in collect_internal_import_targets(source)}
    assert not any(target.startswith("detection") for target in targets)


def test_detects_tracking_importing_detection_in_crafted_source() -> None:
    """違反ケース: `tracking` が `detection.detector` を import する架空のソースが検出される。"""
    fake_source = "from flying_object_tracking.detection.detector import Detector\n"
    violations = find_dependency_direction_violations("tracking", fake_source)
    assert violations != []
    assert "detection.detector" in violations[0]


def test_detects_tracking_importing_projection_in_crafted_source() -> None:
    """違反ケース: `tracking` が `projection` を import する架空のソースが検出される。"""
    fake_source = "from flying_object_tracking.projection import PointEstimator\n"
    violations = find_dependency_direction_violations("tracking", fake_source)
    assert violations != []
    assert "projection" in violations[0]


def test_detects_projection_importing_detection_in_crafted_source() -> None:
    """違反ケース: `projection` が `detection.detector` を import する架空のソースが検出される。"""
    fake_source = "from flying_object_tracking.detection.detector import Detector\n"
    violations = find_dependency_direction_violations("projection", fake_source)
    assert violations != []


def test_detects_layer0_module_importing_upper_layer_in_crafted_source() -> None:
    """違反ケース: `errors`（L0）が `types`（L1）を import する架空のソース（上向き依存）。"""
    fake_source = "from flying_object_tracking.types import CoordinateFrame\n"
    violations = find_dependency_direction_violations("errors", fake_source)
    assert violations != []


def test_detects_config_importing_metrics_in_crafted_source() -> None:
    """違反ケース: `config`（L2）が `metrics`（L3）を import する架空のソース（上向き依存）。"""
    fake_source = "from flying_object_tracking.metrics import TrackingMetrics\n"
    violations = find_dependency_direction_violations("config", fake_source)
    assert violations != []


def test_submodule_import_form_resolves_to_correct_canonical_target() -> None:
    """`from flying_object_tracking.detection import mask_ops` の形が
    `detection.mask_ops` へ正しく解決される（サブモジュール import の曖昧さ回避）。
    """
    fake_source = "from flying_object_tracking.detection import mask_ops\n"
    targets = [target for target, _ in collect_internal_import_targets(fake_source)]
    assert targets == ["detection.mask_ops"]


def test_package_symbol_import_form_resolves_to_package_init() -> None:
    """`from flying_object_tracking.detection.masks import MaskBuilder` の形が
    `detection.masks.__init__`（パッケージ自身）へ解決される。
    """
    fake_source = "from flying_object_tracking.detection.masks import MaskBuilder\n"
    targets = [target for target, _ in collect_internal_import_targets(fake_source)]
    assert targets == ["detection.masks.__init__"]


# ---------------------------------------------------------------------------
# 5. `projection.py` が逆投影の基本演算を自前で持たないこと（要件 5.8）
# ---------------------------------------------------------------------------
#
# design.md が名指しする3パターンを検査する。実コード（`projection.py`）は
# 集約用途で `is_valid_depth` / `depth_raw_to_mm` と**同一の式**をネイティブ
# NumPy 配列演算としてベクトル化して適用する箇所を持つ（`!= INVALID_DEPTH_RAW`、
# 配列全体への `* depth_scale_mm`）。これは task 2.2 以降のマスク生成モジュール
# （`depth_band.py` / `frame_diff.py` / `background.py`）と同一の、既にレビュー
# 済みの「上流の基準・式を配列に適用する」パターンであり「上流の判定基準・
# 換算式を再定義する」ものではないため、以下の検査は**これを違反として
# 扱わない**よう設計する（タスク文書がこの区別を明示的に要求している）。
#
# 一方、**候補1つにつき1回だけ生じる代表値の mm 換算**（`z_mm`。実際に
# `deproject_pixel()` へ渡される値）は、design.md アルゴリズム上の約束4が
# 名指しする「`depth_raw_to_mm()` でのみ行う」対象そのものであり、この
# スカラー値がベクトル化アグリゲーションを経由せず直接
# `raw * depth_scale_mm` で作られていたら違反とみなす。


def _resolve_call_name(node: ast.expr) -> str | None:
    """Call の `func` から呼び出された関数・メソッド名を取り出す（属性アクセスは属性名）。"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def find_hand_rolled_pinhole_formula(source: str) -> list[int]:
    """ピンホール式の自前実装（主点 `ppx_px` / `ppy_px` の減算）を検出する。

    `ppx_px` / `ppy_px`（主点）は `sensing_foundation.deproject_pixel()` の
    内部にのみ存在してよい値であり、本パッケージの計算式にこれらを
    **減算**する形で登場する時点で、逆投影を自前実装している強い兆候になる
    （本パッケージには現状これらの属性名自体が一切登場しない。`fx_px` は
    `expected_diameter_px` 算出のため単独で乗除に登場するが、`ppx_px` /
    `ppy_px` の減算とは別物であり誤検知しない）。行番号の列を返す。
    """
    tree = ast.parse(source)
    linenos = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Sub):
            for operand in (node.left, node.right):
                if isinstance(operand, ast.Attribute) and operand.attr in {"ppx_px", "ppy_px"}:
                    linenos.append(node.lineno)
    return linenos


def _is_safe_depth_raw_to_mm_call(expr: ast.expr) -> bool:
    return isinstance(expr, ast.Call) and _resolve_call_name(expr.func) == "depth_raw_to_mm"


def _resolve_name_chain(
    expr: ast.expr, assigns: dict[str, ast.expr], _seen: set[str] | None = None
) -> ast.expr:
    """`Name` を、単純な `名前 = 式` 代入の連鎖を通じて**多段に**遡って解決する。

    `intermediate = raw * scale; z_mm = intermediate` のような、変数を
    分割・リネームしただけの評価が同じ多段の代入チェーンを追跡する
    （1段しか遡らないと `z_mm` -> `intermediate` で止まってしまい、
    `intermediate` 自体が違反を含むことを見落とす）。`assigns` に無い名前、
    または循環参照（`_seen` で検出）に到達した時点で、その時点の式を返す。
    """
    seen = _seen if _seen is not None else set()
    while isinstance(expr, ast.Name):
        if expr.id in seen or expr.id not in assigns:
            break
        seen.add(expr.id)
        expr = assigns[expr.id]
    return expr


def _is_depth_scale_mm_reference(expr: ast.expr, assigns: dict[str, ast.expr]) -> bool:
    """`expr` が（代入チェーンを遡った先で）`depth_scale_mm` を指す参照かどうかを返す。

    `scale = frame.profile.depth_scale_mm` のように、乗算に使う直前で
    `depth_scale_mm` を別名の変数へ抽出するリファクタも遡って判定する
    （オペランドがそのままの `Attribute`/`Name` でなくても検出する）。
    """
    resolved = _resolve_name_chain(expr, assigns)
    if isinstance(resolved, ast.Attribute) and resolved.attr == "depth_scale_mm":
        return True
    if isinstance(resolved, ast.Name) and "depth_scale_mm" in resolved.id:
        return True
    return False


def _contains_bare_depth_scale_multiplication(expr: ast.expr, assigns: dict[str, ast.expr]) -> bool:
    for sub in ast.walk(expr):
        if isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.Mult):
            for operand in (sub.left, sub.right):
                if _is_depth_scale_mm_reference(operand, assigns):
                    return True
    return False


def _collect_simple_assigns(func: ast.AST) -> dict[str, ast.expr]:
    """関数本体内の単純な代入を `{変数名: 代入された式}` として収集する。

    以下の3形を扱う（`_resolve_name_chain` がこの辞書を多段に遡れるように、
    最終的にはすべて「名前 -> 式」の1段分のエントリへ正規化する）:

    1. 単純な `名前 = 式`（`ast.Assign` かつターゲットが単一の `ast.Name`）。
    2. **タプル/リストのアンパック代入**（`other, z_mm = 0, raw * scale`）:
       `ast.Assign` のターゲットが `ast.Tuple` / `ast.List` で、右辺も同じ長さの
       `ast.Tuple` / `ast.List` の場合、対応する位置ごとに名前 -> 式を割り当てる
       （右辺が呼び出し等で位置ごとの対応が取れない場合は無視する。
       `x_mm, y_mm, z_mm_out = deproject_pixel(...)` のような、呼び出し結果を
       アンパックする形はこの限りではない——それはバイパスの検査対象では
       ない、`deproject_pixel` の**戻り値**の受け取り方であるため）。
    3. **累算代入**（`z_mm = raw; z_mm *= scale`）: `ast.AugAssign` かつ
       演算子が `ast.Mult` の場合、その時点までに分かっている左辺の値
       （無ければ代入前の名前そのもの）を左オペランドとする等価な `ast.BinOp`
       を合成し、`* depth_scale_mm` の検出ロジックへそのまま流し込む。

    設計上のスコープ外（意図的に非対応。レビューで「相応の停止点」と判断済み）:
    ヘルパー関数への委譲（`z_mm = some_helper(raw, scale)` のように乗算が
    別関数の内部に隠れる形。関数間解析が必要になるため対象外）、
    `**kwargs` によるディクショナリ展開、属性ターゲットへの代入
    （`self.z_mm = ...`）。
    """
    assigns: dict[str, ast.expr] = {}
    for node in ast.walk(func):
        if isinstance(node, ast.Assign):
            if len(node.targets) != 1:
                continue
            target = node.targets[0]
            if isinstance(target, ast.Name):
                assigns[target.id] = node.value
            elif isinstance(target, (ast.Tuple, ast.List)) and isinstance(
                node.value, (ast.Tuple, ast.List)
            ):
                if len(target.elts) == len(node.value.elts):
                    for target_elt, value_elt in zip(target.elts, node.value.elts):
                        if isinstance(target_elt, ast.Name):
                            assigns[target_elt.id] = value_elt
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            if isinstance(node.op, ast.Mult):
                prior = assigns.get(node.target.id, ast.Name(id=node.target.id, ctx=ast.Load()))
                synthesized = ast.BinOp(left=prior, op=ast.Mult(), right=node.value)
                ast.copy_location(synthesized, node)
                assigns[node.target.id] = synthesized
    return assigns


def find_bypassed_depth_scale_conversions(source: str) -> list[str]:
    """`deproject_pixel(...)` へ渡す実引数が `depth_raw_to_mm()` の実呼び出しを
    経由せず、`raw * depth_scale_mm` を自前で書いて作られていないかを検出する。

    関数本体内の単純な代入（`名前 = 式`、タプル/リストのアンパック代入、
    `*=` の累算代入）を**多段の連鎖として**（`a = b; b = c; z_mm = c` の
    ような複数回の変数分割・リネームであっても）名前解決し、その式が
    `depth_raw_to_mm(...)` の呼び出し**そのもの**でない、かつ
    `* depth_scale_mm`（乗算のもう一方が別名変数へ抽出されている場合を含む）
    を含む場合に違反として `"... (line N)"` の列を返す（`_collect_simple_assigns`
    の docstring 参照）。

    `z_mm=` という特定のキーワード名の実引数だけでなく、`deproject_pixel`
    呼び出しの**位置引数・キーワード引数のすべて**を検査する
    （将来この呼び出しがキーワードから位置引数の形へ変わっても、
    位置引数経由でのバイパスを見落とさないため）。
    """
    tree = ast.parse(source)
    violations = []
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        assigns = _collect_simple_assigns(func)
        for node in ast.walk(func):
            if isinstance(node, ast.Call) and _resolve_call_name(node.func) == "deproject_pixel":
                arg_descriptions: list[tuple[str, ast.expr]] = [
                    (f"位置引数{i}", arg) for i, arg in enumerate(node.args)
                ] + [(f"キーワード引数 {kw.arg}", kw.value) for kw in node.keywords]
                for description, raw_expr in arg_descriptions:
                    resolved = _resolve_name_chain(raw_expr, assigns)
                    if _is_safe_depth_raw_to_mm_call(resolved):
                        continue
                    if _contains_bare_depth_scale_multiplication(resolved, assigns):
                        violations.append(
                            f"deproject_pixel() の{description}が depth_raw_to_mm() を経由しない "
                            f"mm 換算を使っている (line {node.lineno})"
                        )
    return violations


def _identifier_signature(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return _identifier_signature(node.value)
    return None


def find_raw_depth_zero_comparisons(source: str) -> list[str]:
    """無効 Depth の直値比較（`== 0` / `!= 0`）を検出する。

    比較対象の識別子名（属性名・変数名）に `depth` または `raw` を含む
    場合のみを対象とする（`n == 0` / `candidates == 0` のような無関係な
    カウンタ比較を誤検知しないため）。`!= INVALID_DEPTH_RAW` /
    `== INVALID_DEPTH_RAW`（`Name` 比較であり `Constant(0)` を使わない）は
    構造的にこの検査の対象にならず、誤検知しない
    （`depth_band.py` / `frame_diff.py` / `background.py` / `projection.py`
    自身が使う、task 2.2 以降で確立済みのベクトル化された正当な同値式）。
    """
    tree = ast.parse(source)
    violations = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Compare) and len(node.ops) == 1):
            continue
        op = node.ops[0]
        if not isinstance(op, (ast.Eq, ast.NotEq)):
            continue
        sides = (node.left, node.comparators[0])
        for value_side, other_side in (sides, sides[::-1]):
            if (
                isinstance(value_side, ast.Constant)
                and not isinstance(value_side.value, bool)
                and value_side.value == 0
            ):
                sig = _identifier_signature(other_side)
                if sig is not None and ("depth" in sig.lower() or "raw" in sig.lower()):
                    op_text = "==" if isinstance(op, ast.Eq) else "!="
                    violations.append(f"{sig} {op_text} 0 (line {node.lineno})")
    return violations


def test_projection_calls_upstream_deprojection_functions() -> None:
    """`projection.py` が `depth_raw_to_mm` / `deproject_pixel` を実際に呼び出している
    （import しているだけでなく `ast.Call` として使っていること。要件 5.8）。
    """
    source = SOURCE_FILES["projection"].read_text(encoding="utf-8")
    tree = ast.parse(source)
    called_names = {
        _resolve_call_name(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)
    }
    assert "depth_raw_to_mm" in called_names
    assert "deproject_pixel" in called_names


def test_projection_has_no_hand_rolled_pinhole_formula() -> None:
    """`projection.py` にピンホール式（主点減算）の自前実装が無い（要件 5.8）。"""
    source = SOURCE_FILES["projection"].read_text(encoding="utf-8")
    assert find_hand_rolled_pinhole_formula(source) == []


def test_projection_does_not_bypass_depth_raw_to_mm_for_deprojection() -> None:
    """`projection.py` の `deproject_pixel(z_mm=...)` が `depth_raw_to_mm()` の
    実呼び出しを経由している（`* depth_scale_mm` を自分で書いていない。要件 5.8）。
    """
    source = SOURCE_FILES["projection"].read_text(encoding="utf-8")
    assert find_bypassed_depth_scale_conversions(source) == []


def test_projection_has_no_raw_depth_zero_comparison() -> None:
    """`projection.py` に無効 Depth の直値比較（`== 0` / `!= 0`）が無い（要件 5.8）。

    `projection.py` 自身が使う `bbox_depth != INVALID_DEPTH_RAW`（`Name` 比較）
    は `Constant(0)` を使わないため、この検査には引っかからない。
    """
    source = SOURCE_FILES["projection"].read_text(encoding="utf-8")
    assert find_raw_depth_zero_comparisons(source) == []


def test_no_source_file_has_hand_rolled_pinhole_formula() -> None:
    """防御的チェック: `src/flying_object_tracking/**` のどこにもピンホール式の
    自前実装（主点減算）が無い（`projection.py` 以外に紛れ込む余地も塞ぐ）。
    """
    for module_name, path in SOURCE_FILES.items():
        source = path.read_text(encoding="utf-8")
        linenos = find_hand_rolled_pinhole_formula(source)
        assert linenos == [], f"{module_name} にピンホール式の自前実装: 行 {linenos}"


def test_no_source_file_has_raw_depth_zero_comparison() -> None:
    """防御的チェック: `src/flying_object_tracking/**` のどこにも無効 Depth の
    直値比較（`== 0` / `!= 0`）が無い。
    """
    for module_name, path in SOURCE_FILES.items():
        source = path.read_text(encoding="utf-8")
        violations = find_raw_depth_zero_comparisons(source)
        assert violations == [], f"{module_name} に無効 Depth の直値比較: {violations}"


def test_detects_hand_rolled_pinhole_formula_in_crafted_source() -> None:
    """違反ケース: `(u_px - intrinsics.ppx_px) / intrinsics.fx_px` 形の架空ソースが検出される。"""
    fake_source = (
        "def f(u_px, intrinsics, z_mm):\n"
        "    x_mm = (u_px - intrinsics.ppx_px) / intrinsics.fx_px * z_mm\n"
        "    return x_mm\n"
    )
    assert find_hand_rolled_pinhole_formula(fake_source) != []


def test_does_not_flag_expected_diameter_calculation_in_crafted_source() -> None:
    """誤検知回避: `intrinsics.fx_px * diameter_mm / z_mm`（期待直径の算出。
    ピンホール逆投影そのものではない）は検出されない。
    """
    fake_source = (
        "def f(intrinsics, diameter_mm, z_mm):\n"
        "    return intrinsics.fx_px * diameter_mm / z_mm\n"
    )
    assert find_hand_rolled_pinhole_formula(fake_source) == []


def test_detects_bypassed_depth_scale_conversion_via_intermediate_variable_in_crafted_source() -> (
    None
):
    """違反ケース: `z_mm` を `depth_raw_to_mm()` を経由せず `raw * depth_scale_mm` で
    直接作って `deproject_pixel` へ渡す架空のソースが検出される。
    """
    fake_source = (
        "def estimate(self, frame, candidate):\n"
        "    representative_raw = 123.0\n"
        "    z_mm = representative_raw * frame.profile.depth_scale_mm\n"
        "    return deproject_pixel(\n"
        "        intrinsics, u_px=candidate.cx_px, v_px=candidate.cy_px, z_mm=z_mm\n"
        "    )\n"
    )
    assert find_bypassed_depth_scale_conversions(fake_source) != []


def test_detects_bypassed_depth_scale_conversion_inline_in_crafted_source() -> None:
    """違反ケース: 中間変数を介さず `z_mm=raw * depth_scale_mm` を直接渡す架空のソースが検出される。"""
    fake_source = (
        "def estimate(self, frame, candidate, representative_raw):\n"
        "    return deproject_pixel(\n"
        "        intrinsics,\n"
        "        u_px=candidate.cx_px,\n"
        "        v_px=candidate.cy_px,\n"
        "        z_mm=representative_raw * frame.profile.depth_scale_mm,\n"
        "    )\n"
    )
    assert find_bypassed_depth_scale_conversions(fake_source) != []


def test_detects_multi_hop_assignment_chain_bypass_in_crafted_source() -> None:
    """違反ケース: `intermediate = raw * scale; z_mm = intermediate`（1段では
    `intermediate` 止まりになる評価を、多段の代入チェーンとして遡って検出する）。

    レビューで指摘された回避手法(a): 中間変数を1つ挟むだけの、悪意のない
    リファクタでも境界が破られずに検出できることを固定する。
    """
    fake_source = (
        "def estimate(self, frame, candidate, representative_raw):\n"
        "    intermediate = representative_raw * frame.profile.depth_scale_mm\n"
        "    z_mm = intermediate\n"
        "    return deproject_pixel(\n"
        "        intrinsics, u_px=candidate.cx_px, v_px=candidate.cy_px, z_mm=z_mm\n"
        "    )\n"
    )
    assert find_bypassed_depth_scale_conversions(fake_source) != []


def test_detects_three_hop_assignment_chain_bypass_in_crafted_source() -> None:
    """違反ケース: 3段の代入チェーン（`step1 -> step2 -> z_mm`）でも検出する。

    レビューで指摘された回避手法(d): 変数分割の段数がいくつであっても
    多段追跡が機能することを固定する。
    """
    fake_source = (
        "def estimate(self, frame, candidate, representative_raw):\n"
        "    step1 = representative_raw * frame.profile.depth_scale_mm\n"
        "    step2 = step1\n"
        "    z_mm = step2\n"
        "    return deproject_pixel(\n"
        "        intrinsics, u_px=candidate.cx_px, v_px=candidate.cy_px, z_mm=z_mm\n"
        "    )\n"
    )
    assert find_bypassed_depth_scale_conversions(fake_source) != []


def test_detects_extracted_depth_scale_variable_bypass_in_crafted_source() -> None:
    """違反ケース: `depth_scale_mm` を先に別名変数へ抽出してから乗算する形も検出する
    （`scale = frame.profile.depth_scale_mm; z_mm = raw * scale`）。

    レビューで指摘された回避手法(c): 乗算のオペランド自体がリネームされていても、
    その代入元を遡って `depth_scale_mm` 参照だと判定できることを固定する。
    """
    fake_source = (
        "def estimate(self, frame, candidate, representative_raw):\n"
        "    scale = frame.profile.depth_scale_mm\n"
        "    z_mm = representative_raw * scale\n"
        "    return deproject_pixel(\n"
        "        intrinsics, u_px=candidate.cx_px, v_px=candidate.cy_px, z_mm=z_mm\n"
        "    )\n"
    )
    assert find_bypassed_depth_scale_conversions(fake_source) != []


def test_detects_positional_argument_bypass_in_crafted_source() -> None:
    """違反ケース: `z_mm` をキーワードではなく**位置引数**として渡す形でも検出する
    （`deproject_pixel(intrinsics, candidate.cx_px, candidate.cy_px, z_mm_value)`）。

    レビューで指摘された回避手法(b): 現在の `projection.py` はキーワード引数
    だけを使うが、将来これが位置引数の呼び出しへ変わっても見落とさないことを固定する。
    """
    fake_source = (
        "def estimate(self, frame, candidate, representative_raw):\n"
        "    z_mm = representative_raw * frame.profile.depth_scale_mm\n"
        "    return deproject_pixel(intrinsics, candidate.cx_px, candidate.cy_px, z_mm)\n"
    )
    assert find_bypassed_depth_scale_conversions(fake_source) != []


def test_detects_positional_multi_hop_bypass_in_crafted_source() -> None:
    """違反ケース: 位置引数と多段代入チェーンの組み合わせでも検出する
    （回避手法(a)/(b)/(d)の複合形）。
    """
    fake_source = (
        "def estimate(self, frame, candidate, representative_raw):\n"
        "    intermediate = representative_raw * frame.profile.depth_scale_mm\n"
        "    z_mm = intermediate\n"
        "    return deproject_pixel(intrinsics, candidate.cx_px, candidate.cy_px, z_mm)\n"
    )
    assert find_bypassed_depth_scale_conversions(fake_source) != []


def test_detects_tuple_unpacking_assignment_bypass_in_crafted_source() -> None:
    """違反ケース: タプルアンパック代入（`other, z_mm = 0, raw * scale`）で
    作られた `z_mm` を渡す形も検出する。

    `deproject_pixel` の戻り値を `x_mm, y_mm, z_mm_out = deproject_pixel(...)`
    という形で受け取る本物の `projection.py` の書き方自体が、この
    アンパック代入というイディオムがこのファイルにとって特別なものではない
    ことを裏付ける（レビュー指摘: 「このファイル自身の実コードが既にタプル
    アンパックを使っている」）。
    """
    fake_source = (
        "def estimate(self, frame, candidate, representative_raw):\n"
        "    other, z_mm = 0, representative_raw * frame.profile.depth_scale_mm\n"
        "    return deproject_pixel(\n"
        "        intrinsics, u_px=candidate.cx_px, v_px=candidate.cy_px, z_mm=z_mm\n"
        "    )\n"
    )
    assert find_bypassed_depth_scale_conversions(fake_source) != []


def test_does_not_flag_tuple_unpacking_safe_call_in_crafted_source() -> None:
    """誤検知回避: タプルアンパック代入経由でも `depth_raw_to_mm()` を実際に
    経由していれば検出されない。
    """
    fake_source = (
        "def estimate(self, frame, candidate, representative_raw):\n"
        "    other, z_mm = 0, depth_raw_to_mm(representative_raw, frame.profile.depth_scale_mm)\n"
        "    return deproject_pixel(\n"
        "        intrinsics, u_px=candidate.cx_px, v_px=candidate.cy_px, z_mm=z_mm\n"
        "    )\n"
    )
    assert find_bypassed_depth_scale_conversions(fake_source) == []


def test_detects_augmented_assignment_bypass_in_crafted_source() -> None:
    """違反ケース: 累算代入（`z_mm = raw` の後に `z_mm *= depth_scale_mm`）で
    作られた `z_mm` を渡す形も検出する。
    """
    fake_source = (
        "def estimate(self, frame, candidate, representative_raw):\n"
        "    z_mm = representative_raw\n"
        "    z_mm *= frame.profile.depth_scale_mm\n"
        "    return deproject_pixel(\n"
        "        intrinsics, u_px=candidate.cx_px, v_px=candidate.cy_px, z_mm=z_mm\n"
        "    )\n"
    )
    assert find_bypassed_depth_scale_conversions(fake_source) != []


def test_does_not_flag_augmented_assignment_with_unrelated_factor_in_crafted_source() -> None:
    """誤検知回避: `depth_raw_to_mm()` を経由した後の、`depth_scale_mm` と
    無関係な係数による累算代入（`z_mm *= 1.0`）は検出されない。
    """
    fake_source = (
        "def estimate(self, frame, candidate, representative_raw):\n"
        "    z_mm = depth_raw_to_mm(representative_raw, frame.profile.depth_scale_mm)\n"
        "    z_mm *= 1.0\n"
        "    return deproject_pixel(\n"
        "        intrinsics, u_px=candidate.cx_px, v_px=candidate.cy_px, z_mm=z_mm\n"
        "    )\n"
    )
    assert find_bypassed_depth_scale_conversions(fake_source) == []


def test_does_not_flag_positional_safe_depth_raw_to_mm_call_in_crafted_source() -> None:
    """誤検知回避: 位置引数として渡していても `depth_raw_to_mm()` を実際に
    経由していれば検出されない。
    """
    fake_source = (
        "def estimate(self, frame, candidate, representative_raw):\n"
        "    z_mm = depth_raw_to_mm(representative_raw, frame.profile.depth_scale_mm)\n"
        "    return deproject_pixel(intrinsics, candidate.cx_px, candidate.cy_px, z_mm)\n"
    )
    assert find_bypassed_depth_scale_conversions(fake_source) == []


def test_does_not_flag_multi_hop_safe_depth_raw_to_mm_call_in_crafted_source() -> None:
    """誤検知回避: 多段の代入チェーンを経由していても、最終的に
    `depth_raw_to_mm()` の呼び出しへ到達すれば検出されない
    （多段追跡そのものが誤検知を増やさないことを確認する）。
    """
    fake_source = (
        "def estimate(self, frame, candidate, representative_raw):\n"
        "    computed = depth_raw_to_mm(representative_raw, frame.profile.depth_scale_mm)\n"
        "    z_mm = computed\n"
        "    return deproject_pixel(\n"
        "        intrinsics, u_px=candidate.cx_px, v_px=candidate.cy_px, z_mm=z_mm\n"
        "    )\n"
    )
    assert find_bypassed_depth_scale_conversions(fake_source) == []


def test_does_not_flag_real_depth_raw_to_mm_call_in_crafted_source() -> None:
    """誤検知回避: `z_mm = depth_raw_to_mm(raw, scale)` を経由する正しい形は検出されない。"""
    fake_source = (
        "def estimate(self, frame, candidate, representative_raw):\n"
        "    z_mm = depth_raw_to_mm(representative_raw, frame.profile.depth_scale_mm)\n"
        "    return deproject_pixel(\n"
        "        intrinsics, u_px=candidate.cx_px, v_px=candidate.cy_px, z_mm=z_mm\n"
        "    )\n"
    )
    assert find_bypassed_depth_scale_conversions(fake_source) == []


def test_does_not_flag_vectorized_array_depth_scale_multiplication_in_crafted_source() -> None:
    """誤検知回避: 配列全体への `* depth_scale_mm`（`depth_band.py` 等と同一の
    ベクトル化パターン）は `deproject_pixel` へ渡らない限り検出されない
    （集約用途のベクトル化と、代表値の mm 換算は別の関心事であるため）。
    """
    fake_source = (
        "def build(self, roi_depth):\n"
        "    depth_mm = roi_depth.astype(np.float64) * self._depth_scale_mm\n"
        "    return depth_mm\n"
    )
    assert find_bypassed_depth_scale_conversions(fake_source) == []


def test_detects_raw_depth_zero_comparison_in_crafted_source() -> None:
    """違反ケース: `if raw == 0:` を含む架空のソースが検出される。"""
    fake_source = "def f(raw):\n    if raw == 0:\n        return None\n"
    violations = find_raw_depth_zero_comparisons(fake_source)
    assert violations != []
    assert "raw" in violations[0]


def test_detects_raw_depth_not_equal_zero_comparison_in_crafted_source() -> None:
    """違反ケース: `bbox_depth != 0` を含む架空のソースが検出される。"""
    fake_source = "def f(bbox_depth):\n    return bbox_depth != 0\n"
    assert find_raw_depth_zero_comparisons(fake_source) != []


def test_does_not_flag_unrelated_zero_comparison_in_crafted_source() -> None:
    """誤検知回避: `n == 0` / `candidates == 0` のような Depth と無関係な
    カウンタ比較は検出されない。
    """
    fake_source = "def f(n, candidates):\n    return n == 0 or candidates == 0\n"
    assert find_raw_depth_zero_comparisons(fake_source) == []


def test_does_not_flag_invalid_depth_raw_named_comparison_in_crafted_source() -> None:
    """誤検知回避: `raw != INVALID_DEPTH_RAW`（`Constant(0)` を使わない）は検出されない。"""
    fake_source = "def f(raw):\n    return raw != INVALID_DEPTH_RAW\n"
    assert find_raw_depth_zero_comparisons(fake_source) == []


# ---------------------------------------------------------------------------
# 6. `pyproject.toml`: 基本の依存一覧が空、任意依存グループに numpy と
#    GUI 無し OpenCV（要件 13.3 / 13.4）
# ---------------------------------------------------------------------------
#
# `tests/prediction_core/test_packaging.py` は extras 表明そのもの
# （空であること）の緩和を `sensing-foundation` の所有物として検証しており、
# 本ファイルはそれを変更しない。ここでは本 Spec 自身の観点から、
# `pyproject.toml` の実際の内容について独立に belt-and-suspenders の
# 回帰を張るだけである。


def _load_pyproject() -> dict:
    return tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))


def test_project_dependencies_is_empty() -> None:
    """`[project].dependencies` が空である（要件 13.3 / 13.4）。"""
    data = _load_pyproject()
    assert data["project"]["dependencies"] == []


def test_tracking_extra_declares_numpy_and_headless_opencv() -> None:
    """`[project.optional-dependencies].tracking` に numpy と GUI 無し OpenCV が宣言されている。"""
    data = _load_pyproject()
    tracking_extra = data["project"]["optional-dependencies"]["tracking"]
    assert any(dep.startswith("numpy") for dep in tracking_extra)
    opencv_deps = [dep for dep in tracking_extra if dep.startswith("opencv")]
    assert opencv_deps != [], "tracking extras に opencv 系パッケージが宣言されていない"
    assert all("headless" in dep for dep in opencv_deps), (
        f"GUI 無し（headless）でない OpenCV パッケージが宣言されている: {opencv_deps}"
    )


def test_detects_empty_tracking_extra_in_crafted_toml() -> None:
    """違反ケース: `tracking` extras が空の架空 TOML では numpy/opencv の表明が失敗する。"""
    fake_toml = (
        '[project]\ndependencies = []\n\n'
        '[project.optional-dependencies]\ntracking = []\n'
    )
    data = tomllib.loads(fake_toml)
    tracking_extra = data["project"]["optional-dependencies"]["tracking"]
    assert not any(dep.startswith("numpy") for dep in tracking_extra)


def test_detects_non_empty_base_dependencies_in_crafted_toml() -> None:
    """違反ケース: `[project].dependencies` が空でない架空 TOML は要件13.3/13.4違反として検出できる。"""
    fake_toml = '[project]\ndependencies = ["numpy>=1.24"]\n'
    data = tomllib.loads(fake_toml)
    assert data["project"]["dependencies"] != []


def test_detects_gui_opencv_package_in_crafted_toml() -> None:
    """違反ケース: `opencv-python`（GUI 有り）が紛れ込んだ架空 TOML は headless 判定で検出できる。"""
    fake_toml = (
        '[project.optional-dependencies]\n'
        'tracking = ["numpy>=1.24", "opencv-python>=4.8"]\n'
    )
    data = tomllib.loads(fake_toml)
    opencv_deps = [
        dep for dep in data["project"]["optional-dependencies"]["tracking"] if dep.startswith("opencv")
    ]
    assert not all("headless" in dep for dep in opencv_deps)


# ---------------------------------------------------------------------------
# 7. `prediction_core` / `sensing_foundation` へ抜ける相対 import が無いこと
#    （要件 13.1 / 13.5）
# ---------------------------------------------------------------------------
#
# `flying_object_tracking` は `src/` 直下の独立したトップレベルパッケージ
# であるため、相対 import（`from . import x` 等）は構造上
# `flying_object_tracking` 自身の内部（または、ドット数が多すぎる場合は
# `flying_object_tracking` の**外**＝兄弟パッケージの名前空間）にしか
# 解決されない。「`prediction_core` / `sensing_foundation` だけを狙い撃ちで
# 参照する」相対 import は原理的に構成できない一方、
# 「`flying_object_tracking` の外へ抜けるドット数の相対 import」は
# 構造的に可能であり、それが `prediction_core` / `sensing_foundation` の
# 名前空間と衝突する形の唯一の経路であるため、この検査はそれを検出する。


def find_relative_imports(source: str) -> list[tuple[int, int]]:
    """ソース文字列中の相対 import を `(level, lineno)` の列として返す。"""
    tree = ast.parse(source)
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level and node.level > 0:
            violations.append((node.level, node.lineno))
    return violations


def relative_import_escapes_top_level_package(canonical_name: str, level: int) -> bool:
    """`canonical_name` のファイル内の相対 import が `level` を持つとき、
    `flying_object_tracking` パッケージの外（`prediction_core` /
    `sensing_foundation` 等の兄弟パッケージの名前空間）へ出るかどうかを返す。

    `canonical_name` のドット区切り部品数（`flying_object_tracking` 自身を
    除いた深さ + 1）が、その場所から `flying_object_tracking` 自身の
    `__init__` に達するまでに必要なレベル数と一致する。それを超えるレベルの
    相対 import は `flying_object_tracking` の外（兄弟パッケージの名前空間）
    へ抜ける。
    """
    package_depth = len(canonical_name.split("."))
    return level > package_depth


def find_package_escaping_relative_imports(canonical_name: str, source: str) -> list[str]:
    """`canonical_name` のソースに含まれる、パッケージ外へ抜ける相対 import を検出する。"""
    violations = []
    for level, lineno in find_relative_imports(source):
        if relative_import_escapes_top_level_package(canonical_name, level):
            violations.append(f"{canonical_name}: level={level} (line {lineno})")
    return violations


def test_no_source_file_uses_relative_imports() -> None:
    """`src/flying_object_tracking/**` に相対 import が現状1つも無い。

    本 Spec の全モジュールは `from flying_object_tracking.xxx import yyy`
    という絶対 import の形だけを使う（`prediction_core` の先例と同じ方針）。
    このテストが「相対 import が構造的に発生し得ない」ことの根拠にはならない
    ため、以降のテストで検査ロジック自体（`relative_import_escapes_top_level_
    package`）を架空の入力で固定する。
    """
    for module_name, path in SOURCE_FILES.items():
        source = path.read_text(encoding="utf-8")
        assert find_relative_imports(source) == [], f"{module_name} に相対 import がある"


def test_shallow_relative_import_stays_within_package_in_crafted_source() -> None:
    """`detection.masks.depth_band`（深さ3）からの浅い相対 import（level=1, 2, 3）は
    いずれも `flying_object_tracking` パッケージ内に収まる（違反ではない）。
    """
    for level in (1, 2, 3):
        assert not relative_import_escapes_top_level_package("detection.masks.depth_band", level)


def test_deep_relative_import_escapes_package_in_crafted_source() -> None:
    """`detection.masks.depth_band`（深さ3）からの深すぎる相対 import（level=4）は
    `flying_object_tracking` パッケージの外（`prediction_core` /
    `sensing_foundation` の名前空間）へ抜ける（違反）。
    """
    assert relative_import_escapes_top_level_package("detection.masks.depth_band", 4)


def test_detects_package_escaping_relative_import_in_crafted_source() -> None:
    """違反ケース: 架空のソースに深すぎる相対 import（`from .... import x`）が
    あれば `find_package_escaping_relative_imports` が検出する。
    """
    fake_source = "from .... import Sample\n"
    violations = find_package_escaping_relative_imports("detection.masks.depth_band", fake_source)
    assert violations != []


def test_does_not_flag_within_package_relative_import_in_crafted_source() -> None:
    """誤検知回避: パッケージ内に収まる相対 import（`from . import x`）は検出されない。"""
    fake_source = "from . import background\n"
    violations = find_package_escaping_relative_imports("detection.masks.depth_band", fake_source)
    assert violations == []


def test_root_init_relative_import_boundary_in_crafted_source() -> None:
    """ルート `__init__`（深さ1）では level=1 が境界であり、level=2 から違反になる。"""
    assert not relative_import_escapes_top_level_package("__init__", 1)
    assert relative_import_escapes_top_level_package("__init__", 2)


# ---------------------------------------------------------------------------
# 8. 予測の入力契約にカメラ固有の型が漏れていないこと（予測コア非依存の検証）
# ---------------------------------------------------------------------------
#
# design.md「境界テスト（回帰）」の文言（「予測の入力契約にカメラ固有の型が
# 漏れていないことを、予測コア非依存の検証として位置付ける」）どおり、
# `prediction_core` 自体を一切参照せずに検証する。本 Spec は
# `prediction_core.Sample`（またはそれに類する予測コアの入力型）を
# **一切 import しない**（チェック1）ため、そのような型を構築すること自体が
# 構造的に不可能である——「import していない名前は参照できない」という
# Python の言語仕様そのものが担保になる。加えて、本 Spec が実際に外へ渡す
# 唯一の受け渡し型 `CameraTrack`（および構成要素 `CameraPoint`）が、
# 上流のカメラ固有型（`sensing_foundation.CaptureFrame` /
# `CameraIntrinsics` / `StreamProfile` 等）をフィールドとして持たないことを
# 型定義の静的検査で固定する。


_FORBIDDEN_CAMERA_SPECIFIC_ANNOTATION_NAMES: frozenset[str] = frozenset(
    {"CaptureFrame", "CameraIntrinsics", "StreamProfile", "FrameSource"}
)


def _annotation_identifier_names(annotation: ast.expr) -> set[str]:
    """型注釈式に現れる識別子名（`Name.id` / `Attribute.attr`）の集合を返す。

    `CameraPoint | PointFailure` のような `BinOp`（`|` 演算子）や
    `tuple[TrackPoint, ...]` のような `Subscript` も辿る。
    """
    names: set[str] = set()
    for node in ast.walk(annotation):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def find_camera_specific_types_in_dataclass_fields(source: str, class_name: str) -> list[str]:
    """指定した dataclass のフィールド型注釈に、上流のカメラ固有型が
    含まれていないかを検出する。違反を `"フィールド名: 型名 (line N)"` の列で返す。
    """
    tree = ast.parse(source)
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    found = _annotation_identifier_names(
                        stmt.annotation
                    ) & _FORBIDDEN_CAMERA_SPECIFIC_ANNOTATION_NAMES
                    for name in found:
                        violations.append(f"{stmt.target.id}: {name} (line {stmt.lineno})")
    return violations


def test_no_module_imports_prediction_core_reaffirms_input_contract_isolation() -> None:
    """予測コア非依存の検証: `prediction_core` を一切 import しないこと
    （チェック1の再掲）自体が、`prediction_core.Sample` 等の入力型を本 Spec
    が構築しようがないことの構造的根拠になる（design.md「境界テスト（回帰）」
    の該当項目）。
    """
    for module_name, path in SOURCE_FILES.items():
        source = path.read_text(encoding="utf-8")
        assert find_prediction_core_imports(source) == [], (
            f"{module_name} が prediction_core を import しており、"
            "予測の入力契約分離の前提が崩れている"
        )


def test_camera_track_has_no_camera_specific_type_fields() -> None:
    """`CameraTrack`（本 Spec の唯一の受け渡し型）が上流のカメラ固有型
    （`CaptureFrame` / `CameraIntrinsics` / `StreamProfile` 等）を
    フィールドとして持たない。
    """
    source = SOURCE_FILES["types"].read_text(encoding="utf-8")
    assert find_camera_specific_types_in_dataclass_fields(source, "CameraTrack") == []


def test_camera_point_has_no_camera_specific_type_fields() -> None:
    """`CameraPoint` が上流のカメラ固有型をフィールドとして持たない。"""
    source = SOURCE_FILES["types"].read_text(encoding="utf-8")
    assert find_camera_specific_types_in_dataclass_fields(source, "CameraPoint") == []


def test_camera_track_frame_field_is_fixed_to_camera_coordinate_frame() -> None:
    """`CameraTrack.frame` の型注釈が `CoordinateFrame` である
    （`CoordinateFrame` はメンバーが `CAMERA` 一つだけの列挙型。要件 5.2/5.3/7.3）。
    """
    source = SOURCE_FILES["types"].read_text(encoding="utf-8")
    tree = ast.parse(source)
    field_annotations: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "CameraTrack":
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    if isinstance(stmt.annotation, ast.Name):
                        field_annotations[stmt.target.id] = stmt.annotation.id
    assert field_annotations.get("frame") == "CoordinateFrame"


def test_coordinate_frame_enum_has_only_camera_member() -> None:
    """`CoordinateFrame` 列挙型のメンバーが `CAMERA` 一つだけである
    （World frame を表現するメンバーが存在し得ない、という型による保証の前提）。
    """
    source = SOURCE_FILES["types"].read_text(encoding="utf-8")
    tree = ast.parse(source)
    members: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "CoordinateFrame":
            for stmt in node.body:
                if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                    target = stmt.targets[0]
                    if isinstance(target, ast.Name):
                        members.append(target.id)
    assert members == ["CAMERA"]


def test_detects_camera_specific_type_field_in_crafted_source() -> None:
    """違反ケース: `CaptureFrame` をフィールドに持つ架空の dataclass が検出される。"""
    fake_source = (
        "from dataclasses import dataclass\n\n"
        "@dataclass(frozen=True)\n"
        "class CameraTrack:\n"
        "    frame: CoordinateFrame\n"
        "    source_frame: CaptureFrame\n"
    )
    violations = find_camera_specific_types_in_dataclass_fields(fake_source, "CameraTrack")
    assert violations != []
    assert "CaptureFrame" in violations[0]


def test_does_not_flag_unrelated_field_types_in_crafted_source() -> None:
    """誤検知回避: `CoordinateFrame` / `float` / `int` 等の無関係な型注釈は検出されない。"""
    fake_source = (
        "from dataclasses import dataclass\n\n"
        "@dataclass(frozen=True)\n"
        "class CameraTrack:\n"
        "    frame: CoordinateFrame\n"
        "    t_ms: float\n"
        "    track_id: int\n"
    )
    assert find_camera_specific_types_in_dataclass_fields(fake_source, "CameraTrack") == []
