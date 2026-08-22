"""パッケージ構成とテスト実行基盤の検証（要件 8.5 / 11.3）。

ハードウェアを接続しない環境で `pytest` が完走し、
`import trajectory_sim` が成立することを固定する。
あわせて、`pyproject.toml` の wheel 対象へ新パッケージが追加されていること、
実行時のサードパーティ依存を追加していないこと（design.md「Allowed
Dependencies」）をパッケージメタデータ上で固定する。
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"


def _load_pyproject() -> dict:
    with PYPROJECT_PATH.open("rb") as fp:
        return tomllib.load(fp)


def test_package_is_importable_without_hardware() -> None:
    """ハードウェア非接続の環境で trajectory_sim を import できる（要件 8.5）。"""
    import trajectory_sim

    package_file = Path(trajectory_sim.__file__).resolve()
    assert package_file.name == "__init__.py"
    assert package_file.parent.name == "trajectory_sim"


def test_runtime_python_is_at_least_3_11() -> None:
    """テストを実行している Python が下限バージョン以上である。"""
    assert sys.version_info >= (3, 11)


def test_wheel_packages_include_trajectory_sim() -> None:
    """wheel の packages に src/trajectory_sim が追加されている（design.md「Modified Files」）。"""
    pyproject = _load_pyproject()
    packages = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    assert "src/trajectory_sim" in packages
    # 既存の prediction_core も引き続き列挙されていること（既存構成を壊さない）
    assert "src/prediction_core" in packages


def test_no_third_party_runtime_dependencies() -> None:
    """実行時のサードパーティ依存を追加していない（要件 11.3 / design.md Allowed Dependencies）。"""
    project = _load_pyproject()["project"]
    assert project.get("dependencies", []) == []
