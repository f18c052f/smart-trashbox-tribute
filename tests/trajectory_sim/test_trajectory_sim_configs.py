"""`configs/trajectory_sim/*.json` の4ファイルが実行可能であることの検証
（tasks.md タスク4.4 / 要件4.8, 5.6, 10.3）。

`configs/trajectory_sim/` には以下の4ファイルが実運用の実例として置かれる:

- `drivetrain-wheel60.json` / `drivetrain-wheel48.json`: `--drivetrain` で
  読み込むホイール由来形式の機体パラメータ（60mm / 48mm、要件4.8）。
- `sweep-reachability.json`: `SweepKind.REACHABILITY` の到達可否掃引
  （持ち時間 × 必要移動量、要件10.1系）。
- `sweep-layout.json`: `SweepKind.THROW` の掃引で、レイアウト候補
  （`layout.home_x_mm` / `throw.azimuth_deg`）とキャッチ方針
  （`catch.policy`）を同一掃引の軸に含める（要件5.6, 10.3）。

本テストは、これら4ファイルが `trajectory_sim.cli.main()` の実経路
（タスク4.3が固定した経路）をそのまま通り、有効な出力ファイルを生成する
ことを確認する。`test_layout_study.py`（タスク5.3が別途実装する、より
踏み込んだ境界検出のE2E検証）が担う内容までは踏み込まない。
"""

from __future__ import annotations

import json
from pathlib import Path

from trajectory_sim import cli

CONFIGS_DIR = Path(__file__).resolve().parents[2] / "configs" / "trajectory_sim"

DRIVETRAIN_WHEEL60 = CONFIGS_DIR / "drivetrain-wheel60.json"
DRIVETRAIN_WHEEL48 = CONFIGS_DIR / "drivetrain-wheel48.json"
SWEEP_REACHABILITY = CONFIGS_DIR / "sweep-reachability.json"
SWEEP_LAYOUT = CONFIGS_DIR / "sweep-layout.json"


def _run_cli(config_path: Path, drivetrain_path: Path, output_path: Path) -> dict[str, object]:
    """`cli.main()` を実行し、成功と出力の基本構造を確認したうえで内容を返す。"""
    exit_code = cli.main(
        [
            "--config",
            str(config_path),
            "--drivetrain",
            str(drivetrain_path),
            "--output",
            str(output_path),
        ]
    )
    assert exit_code == 0, f"{config_path.name} x {drivetrain_path.name} の実行が失敗した"
    assert output_path.exists(), "出力ファイルが生成されていない"

    result = json.loads(output_path.read_text(encoding="utf-8"))

    assert "output_schema_version" in result
    assert isinstance(result["cells"], list)
    assert len(result["cells"]) > 0
    assert result["calibration"]["stage"] == "uncalibrated"
    assert "notice" in result["calibration"]
    assert set(result["model_exclusions"].keys()) == {
        "throw_physics",
        "observation",
        "drivetrain",
        "catch",
    }
    return result


def test_all_four_files_exist() -> None:
    """完了条件: 4本の設定ファイルがすべて committed されている。"""
    for path in (DRIVETRAIN_WHEEL60, DRIVETRAIN_WHEEL48, SWEEP_REACHABILITY, SWEEP_LAYOUT):
        assert path.is_file(), f"{path} が存在しない"


def test_reachability_sweep_runs_with_wheel60(tmp_path: Path) -> None:
    _run_cli(SWEEP_REACHABILITY, DRIVETRAIN_WHEEL60, tmp_path / "out.json")


def test_reachability_sweep_runs_with_wheel48(tmp_path: Path) -> None:
    _run_cli(SWEEP_REACHABILITY, DRIVETRAIN_WHEEL48, tmp_path / "out.json")


def test_layout_sweep_runs_with_wheel60(tmp_path: Path) -> None:
    _run_cli(SWEEP_LAYOUT, DRIVETRAIN_WHEEL60, tmp_path / "out.json")


def test_layout_sweep_runs_with_wheel48(tmp_path: Path) -> None:
    _run_cli(SWEEP_LAYOUT, DRIVETRAIN_WHEEL48, tmp_path / "out.json")


def test_requirement_4_8_wheel_diameter_changes_reachability_outcome(tmp_path: Path) -> None:
    """要件4.8: 同一の掃引を異なるホイール径で実行し、結果を比較できる
    （少なくとも1つの格子点で結果が異なる）ことを確認する。
    """
    result_60 = _run_cli(SWEEP_REACHABILITY, DRIVETRAIN_WHEEL60, tmp_path / "out60.json")
    result_48 = _run_cli(SWEEP_REACHABILITY, DRIVETRAIN_WHEEL48, tmp_path / "out48.json")

    cells_60 = result_60["cells"]
    cells_48 = result_48["cells"]
    assert len(cells_60) == len(cells_48)
    assert cells_60 != cells_48, (
        "60mm/48mm ホイールで到達可否掃引の結果が完全に同一であり、"
        "ホイール径の差が結果に反映されていない"
    )


def test_requirement_5_6_catch_policy_comparison_in_same_sweep(tmp_path: Path) -> None:
    """要件5.6: キャッチ方針（停止方針/通過方針）が同一掃引の格子点として
    両方存在し、比較できることを確認する。
    """
    result = _run_cli(SWEEP_LAYOUT, DRIVETRAIN_WHEEL60, tmp_path / "out.json")

    axis_names = [axis["name"] for axis in result["sweep"]["axes"]]
    policy_axis_index = axis_names.index("catch.policy")

    observed_policies = {
        cell["axis_values"][policy_axis_index] for cell in result["cells"]
    }
    assert observed_policies == {"stop_and_wait", "pass_through"}, (
        f"catch.policy 軸の値が両方針を含んでいない: {observed_policies!r}"
    )


def test_requirement_10_3_layout_candidates_produce_multiple_distinct_points(
    tmp_path: Path,
) -> None:
    """要件10.3: レイアウト候補（layout.home_x_mm / throw.azimuth_deg）の
    複数の組み合わせが、格子点として出力に現れることを確認する。
    """
    result = _run_cli(SWEEP_LAYOUT, DRIVETRAIN_WHEEL60, tmp_path / "out.json")

    axis_names = [axis["name"] for axis in result["sweep"]["axes"]]
    home_x_index = axis_names.index("layout.home_x_mm")
    azimuth_index = axis_names.index("throw.azimuth_deg")

    layout_combinations = {
        (cell["axis_values"][home_x_index], cell["axis_values"][azimuth_index])
        for cell in result["cells"]
    }
    assert len(layout_combinations) > 1, (
        "layout.home_x_mm / throw.azimuth_deg の組み合わせが1通りしか"
        "生成されておらず、複数のレイアウト候補を比較できていない"
    )


def test_provenance_present_and_all_assumed_for_reachability(tmp_path: Path) -> None:
    """要件9.4系: `sweep-reachability.json` の出所が全て『想定値』である。"""
    result = _run_cli(SWEEP_REACHABILITY, DRIVETRAIN_WHEEL60, tmp_path / "out.json")
    provenance = result["parameter_provenance"]
    assert len(provenance) > 0
    assert all(value == "assumed" for value in provenance.values())


def test_provenance_present_and_all_assumed_for_layout(tmp_path: Path) -> None:
    """要件9.4系: `sweep-layout.json` の出所が全て『想定値』である。"""
    result = _run_cli(SWEEP_LAYOUT, DRIVETRAIN_WHEEL60, tmp_path / "out.json")
    provenance = result["parameter_provenance"]
    assert len(provenance) > 0
    assert all(value == "assumed" for value in provenance.values())
