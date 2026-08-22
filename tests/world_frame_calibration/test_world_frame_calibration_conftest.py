"""共通フィクスチャの器（`conftest.py`）そのものを固定するテスト（tasks.md タスク 1.1）。

`tmp_calibration_dir` は実データとして機能することを、`default_calibration_plan`
は `CalibrationPlan`（タスク 1.5）が未実装の間は明示的にスキップされることを、
それぞれ固定する。
"""

from __future__ import annotations

from pathlib import Path


def test_tmp_calibration_dir_is_a_real_writable_directory(tmp_calibration_dir: Path) -> None:
    """`tmp_calibration_dir` は実在し、書き込み可能なディレクトリを返す。"""
    assert tmp_calibration_dir.is_dir()
    assert tmp_calibration_dir.name == "calibration"

    marker = tmp_calibration_dir / "result.json"
    marker.write_text("{}", encoding="utf-8")
    assert marker.read_text(encoding="utf-8") == "{}"


def test_default_calibration_plan_skips_until_plan_module_exists(request) -> None:
    """`world_frame_calibration.plan` が無い間、`default_calibration_plan` は明示的にスキップする。

    タスク 1.5 で `world_frame_calibration.plan` が実装された後は、本テストは
    `pytest.importorskip` を通過して `NotImplementedError` で失敗するようになる
    （フィクスチャの中身がまだプレースホルダのままであることの合図）。
    その時点で本テストとフィクスチャの中身を実データへ差し替える。
    """
    import pytest as _pytest

    with _pytest.raises(_pytest.skip.Exception) as exc_info:
        request.getfixturevalue("default_calibration_plan")

    assert "CalibrationPlan" in str(exc_info.value)
