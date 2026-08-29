"""パッケージ骨組みと依存宣言の検証（タスク 1.1 / 要件 13.2, 13.3）。

ファイル名に `m1_` を冠しているのは、`tests/flying_object_tracking/
test_package_skeleton.py` と**同じベース名になると pytest の収集が
落ちる**ためである（`tests/` 配下は `__init__.py` を置かないので、
モジュール名はセッション全体でフラットな名前空間を共有する）。
本ディレクトリのファイル名は既存4 Spec のどれとも衝突させないこと
（`conftest.py` の申し送りを参照）。

ハードウェアを接続しない環境で `import m1_validation` が成立し、
`pyproject.toml` への**追記**が「必須依存は空のまま」「本 Spec が足す
サードパーティ依存は可視化用の任意指定 1 つだけ」という制約を守っている
ことを固定する。

⚠️ **`m1-viz` という extras 名は上流の許可リストに載っていることが前提**
である。`tests/prediction_core/test_packaging.py` の
`ALLOWED_OPTIONAL_EXTRAS`（`sensing-foundation` が所有し、4 Spec 分を
まとめて登録した）に `m1-viz` が含まれているため本 Spec の追記が成立する。
**本 Spec は同テストを改変しない**（design.md「Prerequisites」）。
許可リストが戻された場合に赤くなるのは上流側の当該テストであり、
その失敗はここではなく `tests/prediction_core/test_packaging.py` に出る。
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
GITIGNORE_PATH = REPO_ROOT / ".gitignore"


def _load_pyproject() -> dict:
    with PYPROJECT_PATH.open("rb") as fp:
        return tomllib.load(fp)


def test_package_is_importable_without_hardware() -> None:
    """ハードウェア非接続の環境で `m1_validation` を import できる（要件 13.2）。"""
    import m1_validation

    package_file = Path(m1_validation.__file__).resolve()
    assert package_file.name == "__init__.py"
    assert package_file.parent.name == "m1_validation"


def test_public_entry_point_exposes_nothing_yet() -> None:
    """公開入口は再エクスポート専用であり、この時点では何も公開していない。

    `__all__` が存在すること自体が契約である（後続タスクがここへ足す）。
    空であることを固定するのは、**中身が無いのに import できてしまう状態を
    「完成」と見誤らないため**である。
    """
    import m1_validation

    assert m1_validation.__all__ == []


def test_base_dependencies_remain_empty() -> None:
    """`[project].dependencies` は本 Spec の追記後も空のままである（要件 13.2, 13.3）。

    予測コアの「実行時のサードパーティ依存ゼロ」を本 Spec が壊さないことの表明。
    """
    project = _load_pyproject()["project"]
    assert project.get("dependencies", []) == []


def test_m1_viz_extra_declares_matplotlib() -> None:
    """可視化用の依存は `m1-viz` extras としてのみ宣言される（要件 13.3）。"""
    optional_deps = _load_pyproject()["project"]["optional-dependencies"]
    assert "m1-viz" in optional_deps
    assert optional_deps["m1-viz"] == ["matplotlib>=3.8"]


def test_existing_extras_are_left_untouched() -> None:
    """上流3 Spec の extras を壊していない（追記のみである）。

    同じファイルを複数 Spec が追記するため、**既存行を書き換えていない**
    ことを明示的に固定する（design.md「Modified Files」）。
    """
    optional_deps = _load_pyproject()["project"]["optional-dependencies"]
    assert optional_deps["sensing"] == ["numpy>=1.24"]
    assert optional_deps["calibration"] == ["numpy>=1.24"]
    assert optional_deps["tracking"] == ["numpy>=1.24", "opencv-python-headless>=4.8"]


def test_wheel_packages_include_m1_validation() -> None:
    """wheel の対象パッケージに `src/m1_validation` が含まれる。"""
    packages = _load_pyproject()["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    assert "src/m1_validation" in packages
    # 既存パッケージへの追記のみであり、既存エントリを壊していないことも確認する。
    for existing in (
        "src/prediction_core",
        "src/sensing_foundation",
        "src/trajectory_sim",
        "src/world_frame_calibration",
        "src/flying_object_tracking",
    ):
        assert existing in packages


def test_output_directory_is_covered_by_the_existing_ignore_rule() -> None:
    """本 Spec の出力先 `var/m1/` は既存の `var/` 規則の傘に入る。

    タスク 1.1 は「`.gitignore` は編集しない。出力先は上流が追加する出力
    ディレクトリの傘に入れる」と定める。**規則を足さなかったことが正しい**
    ——`var/` が既にあるからである、という根拠をここで固定する
    （`var/` が消えると本 Spec の出力が版管理へ入り込む）。
    """
    rules = {
        line.strip()
        for line in GITIGNORE_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert "var/" in rules
