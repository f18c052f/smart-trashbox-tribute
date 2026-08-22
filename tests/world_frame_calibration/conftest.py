"""world_frame_calibration のテスト共通フィクスチャ。

一時ディレクトリと既定のキャリブレーション計画（`CalibrationPlan`）に関する
共通フィクスチャをここに置く（tasks.md タスク 1.1 / 1.5）。`CalibrationPlan`
はタスク 1.5（`world_frame_calibration.plan`）で実装済みであるため、
`default_calibration_plan` は他のテストがそのまま使い回せる、検証を通る
最小構成の実データを返す。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from world_frame_calibration.plan import AnchorSpec, CalibrationPlan, PlanLimits
from world_frame_calibration.types import PixelRegion


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
def default_calibration_plan() -> CalibrationPlan:
    """検証を通る最小構成の `CalibrationPlan` を返す共通フィクスチャ。

    典型的な Depth ストリーム解像度（640x480）を想定し、原点マーカー・
    方向マーカーの探索範囲を互いに重ならない位置に置いた、自己整合的で
    妥当な既定計画を返す（`plan.py` の起動時検証4条件をすべて満たす）。
    `verification_points` は空のタプル、`tolerance` / `expected_baseline_mm`
    は未指定（`None`）とし、必要なテストは返り値を
    `dataclasses.replace` 等で上書きして使う。
    """
    return CalibrationPlan(
        plan_format_version="1.0",
        floor_region=PixelRegion(x0_px=0, y0_px=0, x1_px=640, y1_px=480),
        origin_anchor=AnchorSpec(
            label="origin",
            region=PixelRegion(x0_px=50, y0_px=380, x1_px=110, y1_px=440),
            height_band_mm=(5.0, 60.0),
        ),
        x_axis_anchor=AnchorSpec(
            label="x_axis",
            region=PixelRegion(x0_px=530, y0_px=380, x1_px=590, y1_px=440),
            height_band_mm=(5.0, 60.0),
        ),
        verification_points=(),
        limits=PlanLimits(),
        tolerance=None,
        expected_baseline_mm=None,
        notes="default_calibration_plan フィクスチャによる暫定の既定値。",
    )
