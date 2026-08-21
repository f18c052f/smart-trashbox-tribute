"""trajectory_sim のテスト共通フィクスチャ。

「検証を通る最小構成のパラメータ一式」を返す共通フィクスチャの器をここに置く。
`ScenarioParams` 一式（投擲・ばらつき・観測・機体・キャッチ判定・レイアウト・
予測設定・較正段階・出所）はタスク 1.4 で導入されるため、パラメータ型が
出そろった後の別タスクで本フィクスチャの中身を実データへ差し替える。
今回のタスクではフィクスチャ関数の骨組みのみを用意し、中身はプレースホルダとする。
"""

from __future__ import annotations

import pytest


@pytest.fixture
def minimal_scenario_params():
    """検証を通る最小構成の `ScenarioParams` を返す共通フィクスチャ（骨組みのみ）。

    現時点では `trajectory_sim.params` が未実装のため、
    利用しようとしたテストは明示的にスキップされる。
    `trajectory_sim.params` の実装後も、本フィクスチャの中身（実際の
    最小構成パラメータ値）は別タスクで埋めるまでプレースホルダのままである。
    """
    pytest.importorskip(
        "trajectory_sim.params",
        reason="ScenarioParams はタスク 1.4 で実装される",
    )
    raise NotImplementedError(
        "minimal_scenario_params の中身は、ScenarioParams 一式が出そろった"
        "後続タスクで実データを用いて実装する（今回はフィクスチャの骨組みのみ）。"
    )
