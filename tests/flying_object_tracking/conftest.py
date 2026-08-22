"""flying_object_tracking のテスト共通フィクスチャ。

一時ディレクトリ・既定設定・計測無効のロガーに関する共通フィクスチャの器を
ここに置く。`TrackingSettings` 等の設定型はタスク 1.4 で導入されるため、
既定設定フィクスチャはその時点で中身を実装する。ロガーは上流
`sensing_foundation` の `NullLogger` が既に公開されているため、
本タスクの時点で実データとして使える。

## `synthetic_module` / `fakes_module` フィクスチャ（タスク 1.5）

`tests/flying_object_tracking/synthetic.py` と `tests/flying_object_tracking/
fakes.py`（design.md「File Structure Plan」が定める配置。既知の3D軌道から
Depth フレーム列を合成する器と、それを反復する最小の `FrameSource` フェイク）
は、**`tests/sensing_foundation/synthetic.py` と同じベース名**を持つ。
`tests/` 配下はパッケージ化されていない（`__init__.py` を置かない）ため、
モジュール名は pytest セッション全体でフラットな `sys.modules` 名前空間を
共有する。したがって素朴な `from synthetic import ...`（bare import）は、
pytest のコレクション順序次第でどちらのファイルが `sys.modules["synthetic"]`
に入るかが決まってしまい、他方の Spec のテスト（`tests/sensing_foundation/
test_bench_logging.py` 等、`from synthetic import make_supplier` を使う
複数ファイル）を実際に壊す（タスク 1.5 の開発中にフルスイート実行で踏んだ
実例であり、仮定の懸念ではない）。`tests/sensing_foundation/**` は本 Spec の
変更対象外（Boundary: `tests/flying_object_tracking`）であるため、衝突を
避ける側は常に本 Spec が持つ。

本モジュールはこの回避策を一箇所に集約する。`importlib.util.
spec_from_file_location` でファイルパスから直接、衝突しない一意な名前
（`sys.modules` のキーを `_flying_object_tracking_tests.<basename>` とする）
でロードし、`synthetic_module` / `fakes_module` という2つの pytest
フィクスチャとして提供する。フィクスチャはモジュールオブジェクトそのものを
返すため、必要な関数・クラスへは属性アクセス（例:
`synthetic_module.synthesize_frames`、`fakes_module.SyntheticFrameSource`）
で直接届く。

**後続タスク（3.1 の `test_projection.py`、3.2、6.3 の `test_pipeline.py` など、
design.md「File Structure Plan」が本ディレクトリに定める他のテストファイル）
が `synthetic.py` / `fakes.py` の中身を必要とする場合も、必ずこの2つの
フィクスチャ経由でアクセスすること。** `from synthetic import ...` /
`from fakes import ...` のような bare import を書くと、上記と同じ衝突を
再発させる（fixture は pytest 自身の依存注入機構を使うため、Python の
`sys.modules`/`sys.path` によるモジュール解決を経由せず、この種の衝突が
原理的に起こらない——同じ理由で、この回避策自体を `from conftest import
...` のような bare import で共有することもしない。`conftest.py` は
`tests/prediction_core` / `tests/sensing_foundation` / `tests/trajectory_sim`
にも同名で存在するため、同じ危険を repo 全体に広げてしまう）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from sensing_foundation import NullLogger

_THIS_DIR = Path(__file__).parent


def _load_sibling_test_module(basename: str):
    """`tests/flying_object_tracking/{basename}.py` を一意な名前で読み込む。

    衝突回避の理由と方針はモジュール docstring「`synthetic_module` /
    `fakes_module` フィクスチャ」を参照。同一プロセス内で2回目以降は
    `sys.modules` に登録済みの一意な名前からそのまま返す（再読み込みしない）。
    """
    unique_name = f"_flying_object_tracking_tests.{basename}"
    cached = sys.modules.get(unique_name)
    if cached is not None:
        return cached

    path = _THIS_DIR / f"{basename}.py"
    spec = importlib.util.spec_from_file_location(unique_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"{path} を読み込めなかった（spec/loader が None）")

    module = importlib.util.module_from_spec(spec)
    sys.modules[unique_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def synthetic_module():
    """`tests/flying_object_tracking/synthetic.py` をモジュールオブジェクトとして返す。

    `synthetic_module.TrajectoryPoint` / `synthetic_module.synthesize_frames`
    のように属性アクセスで各シンボルへ届く（bare import を使わない理由は
    モジュール docstring 参照）。
    """
    return _load_sibling_test_module("synthetic")


@pytest.fixture(scope="session")
def fakes_module():
    """`tests/flying_object_tracking/fakes.py` をモジュールオブジェクトとして返す。

    `fakes_module.SyntheticFrameSource` のように属性アクセスで届く
    （bare import を使わない理由はモジュール docstring 参照）。
    """
    return _load_sibling_test_module("fakes")


@pytest.fixture
def tmp_output_dir(tmp_path: Path) -> Path:
    """比較結果・エクスポートの出力先として使う一時ディレクトリを返す。

    実際の出力レイアウトは後続タスク（`bench` / `cli`）で定まるため、
    現時点では単純な一時ディレクトリの払い出しのみを行う。
    """
    output_dir = tmp_path / "var"
    output_dir.mkdir()
    return output_dir


@pytest.fixture
def null_logger() -> NullLogger:
    """計測を無効化した状態で使う共通ロガー。

    `sensing_foundation.NullLogger` をそのまま用いる
    （design.md「Allowed Dependencies」: 公開入口のみを参照する）。
    """
    return NullLogger()


@pytest.fixture
def default_tracking_settings():
    """既定の `TrackingSettings` を返す共通フィクスチャ（骨組みのみ）。

    現時点では `flying_object_tracking.config` が未実装のため、
    利用しようとしたテストは明示的にスキップされる。
    """
    config_module = pytest.importorskip(
        "flying_object_tracking.config",
        reason="TrackingSettings はタスク 1.4 で実装される",
    )
    return config_module.TrackingSettings()
