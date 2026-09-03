"""パッケージ構成とテスト実行基盤の検証（要件 1.5 / 7.1）。

ハードウェアを接続しない環境で `python -m pytest` が完走し、
`import prediction_core` が成立することを固定する。
あわせて、実行時のサードパーティ依存がゼロであること
（design.md「Allowed Dependencies」）をパッケージメタデータ上で固定する。
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
    """ハードウェア非接続の環境で prediction_core を import できる（要件 1.5 / 7.1）。"""
    import prediction_core

    package_file = Path(prediction_core.__file__).resolve()
    assert package_file.name == "__init__.py"
    assert package_file.parent.name == "prediction_core"


def test_runtime_python_is_at_least_3_11() -> None:
    """テストを実行している Python が下限バージョン以上である。"""
    assert sys.version_info >= (3, 11)


def test_requires_python_lower_bound_is_3_11() -> None:
    """requires-python の下限が 3.11（Raspberry Pi OS Bookworm）である。"""
    pyproject = _load_pyproject()
    assert pyproject["project"]["requires-python"] == ">=3.11"


# 許可リストは sensing-foundation が4 Spec 分（sensing / tracking / calibration /
# m1-viz）をまとめて登録する。各 Spec が自分の extras 名を個別にここへ追記する形に
# すると、後続4 Spec が同じ赤いテストへ個別に衝突する構図がそのまま再現してしまう
# ため、最初に着地する Spec（sensing-foundation）が不変条件の表現だけを一度に是正
# する（sensing-foundation design.md「test_packaging.py の改訂」）。
ALLOWED_OPTIONAL_EXTRAS = {"sensing", "tracking", "calibration", "m1-viz", "cad"}


def test_no_third_party_runtime_dependencies() -> None:
    """実行時のサードパーティ依存がゼロである（design.md Allowed Dependencies）。

    `[project].dependencies == []` は `prediction_core` の import に第三者
    パッケージが要らないという本来守りたい不変条件であり、そのまま残す。
    extras（optional-dependencies）は「存在しないこと」ではなく「許可された
    名前しか無いこと」を表明する形へ緩める。extras は明示的に指定しない限り
    インストールされず `prediction_core` の import 経路には現れないため、
    許可リストへ広げても実行時依存ゼロ性は損なわれない
    （sensing-foundation design.md「test_packaging.py の改訂」／唯一の認可された例外）。
    """
    project = _load_pyproject()["project"]
    assert project.get("dependencies", []) == []
    assert set(project.get("optional-dependencies", {})) <= ALLOWED_OPTIONAL_EXTRAS


def test_dev_dependency_is_pytest_only() -> None:
    """開発依存は pytest のみである。"""
    pyproject = _load_pyproject()
    dev_group = pyproject["dependency-groups"]["dev"]
    assert [spec.split(">=")[0].split("==")[0].strip() for spec in dev_group] == ["pytest"]
