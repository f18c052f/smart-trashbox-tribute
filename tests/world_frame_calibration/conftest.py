"""world_frame_calibration のテスト共通フィクスチャ。

一時ディレクトリと既定のキャリブレーション計画（`CalibrationPlan`）に関する
共通フィクスチャの器をここに置く（tasks.md タスク 1.1）。`CalibrationPlan`
はタスク 1.5（`world_frame_calibration.plan`）で実装されるため、現時点では
一時ディレクトリの払い出しのみを実データで提供し、既定計画フィクスチャは
骨組みのみを用意する。実装が進んだ時点で、本フィクスチャの中身を実データへ
差し替える。
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_calibration_dir(tmp_path: Path) -> Path:
    """キャリブレーション結果・検証レポートの出力先として使う一時ディレクトリを返す。

    実運用の既定出力先は `var/calibration/`（design.md「Modified Files」）
    だが、テストでは `tmp_path` 配下に同名のディレクトリを払い出し、
    実ファイルシステムへの読み書きを実際のパスで検証できるようにする。
    """
    calibration_dir = tmp_path / "calibration"
    calibration_dir.mkdir()
    return calibration_dir


@pytest.fixture
def default_calibration_plan():
    """検証を通る最小構成の `CalibrationPlan` を返す共通フィクスチャ（骨組みのみ）。

    現時点では `world_frame_calibration.plan` が未実装のため、
    利用しようとしたテストは明示的にスキップされる。
    `world_frame_calibration.plan` の実装後も、本フィクスチャの中身
    （実際の既定計画の値）は別タスク（タスク 1.5 以降）で埋めるまで
    プレースホルダのままである。
    """
    pytest.importorskip(
        "world_frame_calibration.plan",
        reason="CalibrationPlan はタスク 1.5 で実装される",
    )
    raise NotImplementedError(
        "default_calibration_plan の中身は、CalibrationPlan が実装された"
        "後続タスクで実データを用いて実装する（今回はフィクスチャの骨組みのみ）。"
    )
