"""パッケージ骨組みと依存境界の宣言を検証する（タスク 1.1 / 要件 1.1）。

本ファイルが固定するのは2点である。

1. **`chassis_mechanism` パッケージが存在し、形状ライブラリ（build123d）を
   導入していない環境でも import できる**こと。design.md「Dependency Direction」が
   ⚠️ **`__init__` は `shapes` / `export` を import しない**と定めているため、
   公開入口の import が OCCT へ到達しないことは骨組みの段階から成立する。
2. **任意依存の一覧が増えていない**こと。本 Spec は形状ライブラリを
   ⚠️ **上流 `catch-mechanism` が導入済みの `cad` extras のまま使う**（design.md
   「Allowed Dependencies」）。新しい extras を宣言しないため、許可リストが複製されている
   `tests/prediction_core/test_packaging.py` と
   `tests/sensing_foundation/test_sensing_boundaries.py` には**触れる理由が無い**
   （tasks.md「触れてはいけない場所」）。触れていないことを、この2ファイルの許可リストと
   `pyproject.toml` の extras が**一致したままである**という形で外側から固定する。

ファイル名について: `tests/` 配下には `__init__.py` を置かないため、テストモジュール名は
pytest セッション全体でフラットな名前空間を共有する。同一ベース名のファイルが2つあると
収集時に落ちるため、本 Spec のテストは全て `test_chassis_` 接頭辞を付ける
（design.md「Directory Structure」の注記）。`test_packaging.py` は
`tests/prediction_core/` が既に使っている。
"""

from __future__ import annotations

import ast
import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"

#: 本 Spec 着手時点で宣言されている任意依存の全て。
#: ⚠️ **本 Spec はここへ1つも足さない**（design.md「Allowed Dependencies」）。
EXPECTED_OPTIONAL_EXTRAS = frozenset({"sensing", "calibration", "tracking", "m1-viz", "cad"})

#: `ALLOWED_OPTIONAL_EXTRAS` が定義されている上流の2ファイル。
#: ⚠️ **本 Spec はどちらにも触れない。** 触れる必要が生じたなら、それは
#: 新しい extras を宣言してしまったということである。
ALLOWLIST_SOURCES = (
    REPO_ROOT / "tests" / "prediction_core" / "test_packaging.py",
    REPO_ROOT / "tests" / "sensing_foundation" / "test_sensing_boundaries.py",
)

#: 本 Spec の追記より前から wheel の対象に入っているパッケージ。
EXISTING_WHEEL_PACKAGES = (
    "src/prediction_core",
    "src/sensing_foundation",
    "src/trajectory_sim",
    "src/world_frame_calibration",
    "src/flying_object_tracking",
    "src/m1_validation",
    "src/catch_mechanism",
)


def _load_pyproject() -> dict:
    with PYPROJECT_PATH.open("rb") as fp:
        return tomllib.load(fp)


def _read_allowlist(path: Path) -> set[str]:
    """`ALLOWED_OPTIONAL_EXTRAS = {...}` の集合リテラルを静的に読み取る。

    当該モジュールを import せずに `ast` で読むのは、テストツリーが
    パッケージ化されておらず import できないためである（本ファイル冒頭の注記を参照）。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if "ALLOWED_OPTIONAL_EXTRAS" in names:
            return set(ast.literal_eval(node.value))
    raise AssertionError(f"{path} に ALLOWED_OPTIONAL_EXTRAS が見つからない")


def test_package_is_importable_without_the_shape_library() -> None:
    """形状ライブラリを入れていない環境でも `chassis_mechanism` を import できる。

    タスク 1.1 の観測可能な完了状態そのもの。`src` レイアウトの入口ファイルが
    実在し、名前空間パッケージへ退化していないことを併せて確かめる。
    """
    import chassis_mechanism

    package_file = Path(chassis_mechanism.__file__).resolve()
    assert package_file.name == "__init__.py"
    assert package_file.parent.name == "chassis_mechanism"
    assert package_file.parent.parent.name == "src"


def test_importing_the_package_does_not_pull_in_the_shape_library() -> None:
    """公開入口の import が形状ライブラリを引き込まない（design.md「Dependency Direction」）。

    形状ライブラリが導入済みか否かに依らず成立させるため、まっさらな子プロセスで
    `import chassis_mechanism` した直後の `sys.modules` を見る。自プロセスの
    `sys.modules` を見る形にすると、他のテストが先に import していた場合に結果が変わる。
    ⚠️ 上流 `catch_mechanism.__init__` も OCCT へ到達しないため、この性質は
    上流の公開 API を再エクスポートし始めた後も推移的に保たれる。
    """
    probe = (
        "import chassis_mechanism, sys; "
        'assert chassis_mechanism.__file__ is not None, "名前空間パッケージになっている"; '
        "print('build123d' in sys.modules)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )
    assert completed.stdout.strip() == "False"


def test_public_entry_point_declares_an_explicit_public_surface() -> None:
    """公開入口が `__all__` を明示的に宣言する（design.md「Revalidation Triggers」項目4）。

    `__all__` が存在すること自体が契約である。⚠️ **中身の正は本ファイルではなく
    7群の下流契約テストが持つ。** ここで固定するのは骨組みの側——入口が暗黙の
    公開面（`__all__` 不在）に退化していないことだけである。
    """
    import chassis_mechanism

    assert isinstance(chassis_mechanism.__all__, list)
    assert all(isinstance(name, str) for name in chassis_mechanism.__all__)


def test_wheel_packages_include_chassis_mechanism() -> None:
    """wheel の対象パッケージに `src/chassis_mechanism` が含まれる（design.md「Modified Files」）。"""
    packages = _load_pyproject()["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    assert "src/chassis_mechanism" in packages
    # 既存パッケージへの追記のみであり、既存エントリを壊していないことも確認する。
    for existing in EXISTING_WHEEL_PACKAGES:
        assert existing in packages, f"既存の wheel 対象 {existing} が失われている"


def test_base_dependencies_remain_empty() -> None:
    """`[project].dependencies` は本 Spec の追記後も空のままである（design.md「Modified Files」）。"""
    project = _load_pyproject()["project"]
    assert project.get("dependencies", []) == []


def test_optional_dependencies_are_not_extended() -> None:
    """任意依存の一覧が増えていない（tasks.md 1.1 / design.md「Allowed Dependencies」）。

    本 Spec が使う形状ライブラリは上流が宣言済みの `cad` であり、
    ⚠️ **新しい extras を1つも足さない**。集合が増えていないことを厳密な一致で固定する。
    """
    optional_deps = _load_pyproject()["project"]["optional-dependencies"]
    assert set(optional_deps) == set(EXPECTED_OPTIONAL_EXTRAS)
    assert optional_deps["cad"] == ["build123d>=0.9,<1.0"]


def test_upstream_extras_allowlists_are_left_untouched() -> None:
    """複製された2つの許可リストが、宣言済み extras と一致したままである。

    ⚠️ **本 Spec はこの2ファイルに触れない**（tasks.md「触れてはいけない場所」）。
    新しい extras を足せば、上流のテストを通すためにこの2ファイルを編集せざるを
    得なくなる。したがって「許可リスト == 宣言済み extras」が保たれていること自体が、
    触れていないことの外側からの証拠になる。
    """
    allowlists = {path: _read_allowlist(path) for path in ALLOWLIST_SOURCES}
    for path, allowlist in allowlists.items():
        assert allowlist == set(EXPECTED_OPTIONAL_EXTRAS), (
            f"{path} の ALLOWED_OPTIONAL_EXTRAS が変化している: {sorted(allowlist)}"
        )
    values = list(allowlists.values())
    assert values[0] == values[1], "複製された ALLOWED_OPTIONAL_EXTRAS が食い違っている"
