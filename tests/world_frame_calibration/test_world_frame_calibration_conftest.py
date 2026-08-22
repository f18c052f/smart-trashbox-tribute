"""共通フィクスチャの器（`conftest.py`）そのものを固定するテスト（tasks.md タスク 1.1 / 1.5）。

`tmp_calibration_dir` は実データとして機能することを、`default_calibration_plan`
は `CalibrationPlan`（タスク 1.5 で実装済み）の検証を通る実データを返すことを、
それぞれ固定する。
"""

from __future__ import annotations

from pathlib import Path

from world_frame_calibration.plan import (
    AnchorSpec,
    CalibrationPlan,
    PlanLimits,
    load_plan,
    save_plan,
)


def test_tmp_calibration_dir_is_a_real_writable_directory(tmp_calibration_dir: Path) -> None:
    """`tmp_calibration_dir` は実在し、書き込み可能なディレクトリを返す。"""
    assert tmp_calibration_dir.is_dir()
    assert tmp_calibration_dir.name == "calibration"

    marker = tmp_calibration_dir / "result.json"
    marker.write_text("{}", encoding="utf-8")
    assert marker.read_text(encoding="utf-8") == "{}"


def test_default_calibration_plan_returns_a_real_calibration_plan(
    default_calibration_plan: CalibrationPlan,
) -> None:
    """`default_calibration_plan` は本物の `CalibrationPlan` インスタンスを返す。

    タスク 1.5 で `world_frame_calibration.plan` が実装されたことで、
    このフィクスチャはもはやスキップ／`NotImplementedError` のプレースホルダ
    ではなく、そのまま他のテストが使い回せる妥当な値を持つ実データを返す。
    """
    plan = default_calibration_plan

    assert isinstance(plan, CalibrationPlan)
    assert plan.plan_format_version == "1.0"

    assert isinstance(plan.origin_anchor, AnchorSpec)
    assert isinstance(plan.x_axis_anchor, AnchorSpec)
    assert plan.origin_anchor.label != plan.x_axis_anchor.label

    lower, upper = plan.origin_anchor.height_band_mm
    assert lower < upper
    lower, upper = plan.x_axis_anchor.height_band_mm
    assert lower < upper

    assert plan.verification_points == ()
    assert isinstance(plan.limits, PlanLimits)
    assert plan.tolerance is None
    assert plan.expected_baseline_mm is None
    assert isinstance(plan.notes, str)


def test_default_calibration_plan_round_trips_through_save_and_load(
    default_calibration_plan: CalibrationPlan, tmp_calibration_dir: Path
) -> None:
    """`default_calibration_plan` は `save_plan` / `load_plan` の起動時検証を通過する実データである。"""
    plan_path = tmp_calibration_dir / "plan.json"

    save_plan(default_calibration_plan, plan_path)
    loaded = load_plan(plan_path)

    assert loaded == default_calibration_plan
