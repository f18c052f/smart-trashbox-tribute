"""flying_object_tracking のテスト共通フィクスチャ。

一時ディレクトリ・既定設定・計測無効のロガーに関する共通フィクスチャの器を
ここに置く。`TrackingSettings` 等の設定型はタスク 1.4 で導入されるため、
既定設定フィクスチャはその時点で中身を実装する。ロガーは上流
`sensing_foundation` の `NullLogger` が既に公開されているため、
本タスクの時点で実データとして使える。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sensing_foundation import NullLogger


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
