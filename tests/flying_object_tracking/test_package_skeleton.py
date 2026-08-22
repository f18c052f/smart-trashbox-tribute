"""パッケージ骨組みと依存宣言の検証（タスク 1.1 / 要件 11.4, 12.6, 13.1-13.3, 13.5）。

ハードウェアを接続しない環境で `import flying_object_tracking` が成立し、
`pyproject.toml` への追記が「基本の依存一覧は空のまま」「追加依存は
`tracking` extras のみ」という制約を守っていることを固定する。
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"


def _load_pyproject() -> dict:
    with PYPROJECT_PATH.open("rb") as fp:
        return tomllib.load(fp)


def test_package_is_importable_without_hardware() -> None:
    """ハードウェア非接続の環境で flying_object_tracking を import できる（要件 11.1 / 11.4）。"""
    import flying_object_tracking

    package_file = Path(flying_object_tracking.__file__).resolve()
    assert package_file.name == "__init__.py"
    assert package_file.parent.name == "flying_object_tracking"


def test_base_dependencies_remain_empty() -> None:
    """`[project].dependencies` は本 Spec の追記後も空のままである（要件 13.1-13.3）。"""
    project = _load_pyproject()["project"]
    assert project.get("dependencies", []) == []


def test_tracking_extra_declares_numpy_and_headless_opencv() -> None:
    """`tracking` extras に配列ライブラリと GUI 無し画像処理ライブラリを宣言する（要件 13.3）。"""
    optional_deps = _load_pyproject()["project"]["optional-dependencies"]
    assert "tracking" in optional_deps

    tracking_specs = optional_deps["tracking"]
    names = {spec.split(">=")[0].strip() for spec in tracking_specs}
    assert "numpy" in names
    assert "opencv-python-headless" in names

    for spec in tracking_specs:
        if spec.startswith("numpy"):
            assert spec == "numpy>=1.24"
        if spec.startswith("opencv-python-headless"):
            assert spec == "opencv-python-headless>=4.8"


def test_wheel_packages_include_flying_object_tracking() -> None:
    """wheel の対象パッケージに `src/flying_object_tracking` が含まれる。"""
    pyproject = _load_pyproject()
    packages = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    assert "src/flying_object_tracking" in packages
    # 既存パッケージへの追記のみであり、既存エントリを壊していないことも確認する。
    assert "src/prediction_core" in packages
    assert "src/sensing_foundation" in packages
    assert "src/trajectory_sim" in packages
