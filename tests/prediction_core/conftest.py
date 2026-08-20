"""prediction_core のテスト共通フィクスチャ。

既定設定を返す共通フィクスチャの器をここに置く。
`PredictionConfig` はタスク 1.5 で導入されるため、
その時点で本フィクスチャが既定設定インスタンスを返すよう実装する。
"""

from __future__ import annotations

import pytest


@pytest.fixture
def default_config():
    """既定の PredictionConfig を返す共通フィクスチャ。

    現時点では `prediction_core.config` が未実装のため、
    利用しようとしたテストは明示的にスキップされる。
    """
    config_module = pytest.importorskip(
        "prediction_core.config",
        reason="PredictionConfig はタスク 1.5 で実装される",
    )
    return config_module.PredictionConfig()
