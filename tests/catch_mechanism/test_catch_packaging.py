"""パッケージ骨組みと依存境界の宣言を検証する（タスク 1.1 / 要件 5.1, 5.4, 5.6, 5.7）。

本ファイルが固定するのは「形状ライブラリ（build123d）を**既定では入らない**
任意依存として隔離したうえで、`catch_mechanism` パッケージが存在し import
できる」ことである。形状ライブラリを導入していない環境でも全件が通ること
自体が要件 5.7 の観測点である。

ファイル名について: `tests/` 配下には `__init__.py` を置かないため、テスト
モジュール名は pytest セッション全体でフラットな名前空間を共有する。同一
ベース名のファイルが2つあると収集時に落ちるため、本ディレクトリのファイル名
は既存 Spec のどれとも衝突させないこと（`test_packaging.py` は
`tests/prediction_core/` が、`test_boundaries.py` `test_config.py`
`test_metrics.py` `test_errors.py` は他 Spec が既に使っている。design.md
「Directory Structure」が挙げる名前をそのまま使うと衝突するものがあるため、
後続タスクは接頭辞を付けて回避すること）。

⚠️ **`ALLOWED_OPTIONAL_EXTRAS` は上流に2箇所複製されている。** 定義元は
`tests/prediction_core/test_packaging.py`、複製は
`tests/sensing_foundation/test_sensing_boundaries.py`。複製の理由は上記の
`__init__.py` 不在（テストモジュール間で定数を import できない）であり、
**その解消は本 Spec の境界外**（`sensing-foundation` の所有物）である。
本 Spec は両方へ `"cad"` を1行足すだけに留め、不変条件の表現・主張には
手を入れない（design.md「依存境界の扱い（`cad` extra の導入）」決定2）。
"""

from __future__ import annotations

import ast
import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"

#: `ALLOWED_OPTIONAL_EXTRAS` が定義されている上流の2ファイル。
#: 片方だけ更新すると
#: `test_pyproject_dependencies_stay_empty_and_extras_stay_within_allowlist`
#: が落ちるため、両者が一致していること自体をここで固定する。
ALLOWLIST_SOURCES = (
    REPO_ROOT / "tests" / "prediction_core" / "test_packaging.py",
    REPO_ROOT / "tests" / "sensing_foundation" / "test_sensing_boundaries.py",
)


def _load_pyproject() -> dict:
    with PYPROJECT_PATH.open("rb") as fp:
        return tomllib.load(fp)


def _read_allowlist(path: Path) -> set[str]:
    """`ALLOWED_OPTIONAL_EXTRAS = {...}` の集合リテラルを静的に読み取る。

    当該モジュールを import せずに `ast` で読むのは、テストツリーが
    パッケージ化されておらず import できないためである（本ファイル冒頭の
    注記を参照）。
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
    """形状ライブラリを入れていない環境でも `catch_mechanism` を import できる（要件 5.7）。"""
    import catch_mechanism

    package_file = Path(catch_mechanism.__file__).resolve()
    assert package_file.name == "__init__.py"
    assert package_file.parent.name == "catch_mechanism"


def test_importing_the_package_does_not_pull_in_the_shape_library() -> None:
    """公開入口の import が形状ライブラリを引き込まない（要件 5.1, 5.7）。

    形状ライブラリが導入済みか否かに依らず成立させるため、まっさらな子
    プロセスで `import catch_mechanism` した直後の `sys.modules` を見る。
    自プロセスの `sys.modules` を見る形にすると、他のテストが先に import
    していた場合に結果が変わってしまう。
    """
    probe = (
        "import catch_mechanism, sys; "
        "assert catch_mechanism.__file__ is not None, \"名前空間パッケージになっている\"; "
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


def test_public_entry_point_exposes_nothing_yet() -> None:
    """公開入口は再エクスポート専用であり、この時点では何も公開していない。

    `__all__` が存在すること自体が契約である（公開契約を決めるのは後続
    タスク 6.1 であり、空であることは「まだ何も公開していない」という
    現状の正確な表明である）。
    """
    import catch_mechanism

    assert catch_mechanism.__all__ == []


def test_base_dependencies_remain_empty() -> None:
    """`[project].dependencies` は本 Spec の追記後も空のままである（要件 5.4, 5.6）。"""
    project = _load_pyproject()["project"]
    assert project.get("dependencies", []) == []


def test_cad_extra_declares_the_shape_library() -> None:
    """形状ライブラリは `cad` extras としてのみ宣言される（要件 5.1）。

    extras は明示的に指定しない限りインストールされないため、既定の実行
    環境（`uv run pytest`）には現れない。
    """
    optional_deps = _load_pyproject()["project"]["optional-dependencies"]
    assert "cad" in optional_deps
    assert optional_deps["cad"] == ["build123d>=0.9,<1.0"]


def test_existing_extras_are_left_untouched() -> None:
    """上流 Spec の extras を壊していない（追記のみである。要件 5.4, 5.6）。"""
    optional_deps = _load_pyproject()["project"]["optional-dependencies"]
    assert optional_deps["sensing"] == ["numpy>=1.24"]
    assert optional_deps["calibration"] == ["numpy>=1.24"]
    assert optional_deps["tracking"] == ["numpy>=1.24", "opencv-python-headless>=4.8"]
    assert optional_deps["m1-viz"] == ["matplotlib>=3.8"]


def test_wheel_packages_include_catch_mechanism() -> None:
    """wheel の対象パッケージに `src/catch_mechanism` が含まれる。"""
    packages = _load_pyproject()["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    assert "src/catch_mechanism" in packages
    # 既存パッケージへの追記のみであり、既存エントリを壊していないことも確認する。
    for existing in (
        "src/prediction_core",
        "src/sensing_foundation",
        "src/trajectory_sim",
        "src/world_frame_calibration",
        "src/flying_object_tracking",
        "src/m1_validation",
    ):
        assert existing in packages


def test_cad_extra_is_registered_in_both_upstream_allowlists() -> None:
    """複製された2つの `ALLOWED_OPTIONAL_EXTRAS` の**両方**に `cad` がある（要件 5.6）。

    片方だけ更新すると上流のテストが落ちる。落ちてから気付くのではなく、
    複製が同期していること自体をここで固定する（design.md「依存境界の
    扱い（`cad` extra の導入）」決定2）。
    """
    allowlists = {path: _read_allowlist(path) for path in ALLOWLIST_SOURCES}
    for path, allowlist in allowlists.items():
        assert "cad" in allowlist, f"{path} の ALLOWED_OPTIONAL_EXTRAS に 'cad' が無い"
    values = list(allowlists.values())
    assert values[0] == values[1], (
        "複製された ALLOWED_OPTIONAL_EXTRAS が食い違っている: "
        f"{ {str(p): sorted(v) for p, v in allowlists.items()} }"
    )


def test_upstream_allowlists_keep_the_existing_extras() -> None:
    """許可リストへの追記のみであり、既存の extras 名を落としていない（要件 5.6）。

    不変条件の表現（`extras ⊆ 許可リスト`）を弱める変更を検出するための
    足場である。⚠️ 主張そのものを本 Spec が書き換えてはならない。
    """
    for path in ALLOWLIST_SOURCES:
        allowlist = _read_allowlist(path)
        assert {"sensing", "tracking", "calibration", "m1-viz"} <= allowlist
