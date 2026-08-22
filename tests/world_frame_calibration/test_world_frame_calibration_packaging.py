"""パッケージ構成とテスト実行基盤の検証（要件 11.5）。

ハードウェアを接続しない環境で `pytest` が完走し、
`import world_frame_calibration` が成立することを固定する。
あわせて、`pyproject.toml` の wheel 対象へ新パッケージが追加されていること、
`calibration` extras が `numpy>=1.24` を宣言していること、
`[project].dependencies` が空のまま変更されていないこと
（design.md「Allowed Dependencies」/「Modified Files」）をパッケージ
メタデータ上で固定する。

`tests/prediction_core/test_packaging.py` はこのファイルの境界外であり、
一切変更しない（design.md「Modified Files」の前提）。
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
    """ハードウェア非接続の環境で world_frame_calibration を import できる（要件 11.5）。"""
    import world_frame_calibration

    package_file = Path(world_frame_calibration.__file__).resolve()
    assert package_file.name == "__init__.py"
    assert package_file.parent.name == "world_frame_calibration"


def test_runtime_python_is_at_least_3_11() -> None:
    """テストを実行している Python が下限バージョン以上である。"""
    assert sys.version_info >= (3, 11)


def test_wheel_packages_include_world_frame_calibration() -> None:
    """wheel の packages に src/world_frame_calibration が追記されている（design.md「Modified Files」）。"""
    pyproject = _load_pyproject()
    packages = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    assert "src/world_frame_calibration" in packages
    # 既存の prediction_core / sensing_foundation / trajectory_sim も引き続き
    # 列挙されていること（追記のみで既存構成を壊さない）
    assert "src/prediction_core" in packages
    assert "src/sensing_foundation" in packages
    assert "src/trajectory_sim" in packages


def test_calibration_extra_declares_numpy_only() -> None:
    """`calibration` extras が numpy>=1.24 のみを宣言している（design.md「Modified Files」）。"""
    pyproject = _load_pyproject()
    optional_dependencies = pyproject["project"]["optional-dependencies"]
    assert optional_dependencies["calibration"] == ["numpy>=1.24"]


def test_no_third_party_runtime_dependencies() -> None:
    """`[project].dependencies` は空のまま変更されていない（要件 11.5 / design.md Allowed Dependencies）。

    `prediction_core` の実行時依存ゼロという不変条件を、本 Spec のパッケージ
    追記が壊していないことを固定する。extras の許可リスト自体の検証は
    `tests/prediction_core/test_packaging.py` が所有するため、ここでは
    再改訂しない。
    """
    project = _load_pyproject()["project"]
    assert project.get("dependencies", []) == []
