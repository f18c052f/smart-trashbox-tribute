"""コマンドラインエントリポイントの検証（design.md「Cli」/ 要件 7.5, 11.2）。

`python -m trajectory_sim --config <path> --output <path> [--drivetrain <path>]`
という1回きりのバッチ実行経路が、
  - 設定ファイルを指定した実行で出力ファイルを生成すること
  - 不正な設定では出力ファイルを生成せずにエラー終了すること
  - `--drivetrain` による機体パラメータの差し替え（ホイール由来形式 /
    直接形式）と、その優先順位（常に `--drivetrain` が勝つ）
  - 設定ファイルの未知のキーが、トップレベルだけでなくネストの内側でも
    拒否されること
  - enum 値（文字列）が正しく coerce されること
  - 標準出力への要約が1行のみであること
  - `python -m trajectory_sim` の実際の起動経路（`__main__.py`）が動作すること
  - サーバ・ソケット・監視ループに相当する import が無いこと（軽い静的検査）
をテストで固定する（tasks.md タスク4.3）。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from trajectory_sim import cli
from trajectory_sim.params import DrivetrainParams

REPO_ROOT = Path(__file__).resolve().parents[2]


def _drivetrain_dict(max_speed_mm_s: float = 2000.0) -> dict[str, object]:
    """検証を通る最小構成の `DrivetrainParams` フィールド辞書（直接形式）。"""
    return {
        "max_speed_mm_s": max_speed_mm_s,
        "max_accel_mm_s2": 3000.0,
        "max_decel_mm_s2": 3000.0,
        "control_period_ms": 20.0,
        "command_latency_ms": 50.0,
    }


def _wheel_drivetrain_dict() -> dict[str, object]:
    """検証を通る最小構成の `DrivetrainParams` フィールド辞書（ホイール由来形式）。"""
    return {
        "wheel_diameter_mm": 60.0,
        "motor_rpm": 530.0,
        "speed_efficiency": 1.0,
        "max_accel_mm_s2": 3000.0,
        "max_decel_mm_s2": 3000.0,
        "control_period_ms": 20.0,
        "command_latency_ms": 50.0,
    }


def _parameters_dict(
    *,
    include_drivetrain: bool = True,
    drivetrain_max_speed_mm_s: float = 2000.0,
    catch_policy: str | None = None,
    throw_unknown_key: bool = False,
) -> dict[str, object]:
    """検証を通る最小構成の `parameters` 辞書（`ScenarioParams` に対応）。"""
    throw: dict[str, object] = {
        "release_x_mm": 0.0,
        "release_y_mm": 0.0,
        "release_z_mm": 1500.0,
        "speed_mm_s": 3000.0,
        "elevation_deg": 30.0,
        "azimuth_deg": 0.0,
    }
    if throw_unknown_key:
        throw["bogus_key"] = 1

    parameters: dict[str, object] = {
        "throw": throw,
        "dispersion": {},
        "observation": {
            "detection_start_delay_ms": 0.0,
            "sample_period_ms": 10.0,
            "sample_latency_ms": 5.0,
            "prediction_latency_ms": 5.0,
        },
        "catch": {} if catch_policy is None else {"policy": catch_policy},
        "layout": {"home_x_mm": 0.0, "home_y_mm": 0.0},
        "prediction": {},
    }
    if include_drivetrain:
        parameters["drivetrain"] = _drivetrain_dict(drivetrain_max_speed_mm_s)
    return parameters


def _sweep_dict() -> dict[str, object]:
    """検証を通る最小構成の `sweep` 辞書（到達可否掃引、格子点1つ）。

    `evaluate_reachability` は `params.drivetrain` / `params.catch.policy` の
    みを参照するため（`src/trajectory_sim/evaluate.py`）、他パラメータの
    値に依存せずテストを単純にできる。
    """
    return {
        "kind": "reachability",
        "axes": [
            {"name": "hold_time_ms", "unit": "ms", "values": [500.0]},
            {"name": "required_distance_mm", "unit": "mm", "values": [300.0]},
        ],
    }


def _config_dict(**parameters_kwargs: object) -> dict[str, object]:
    return {"parameters": _parameters_dict(**parameters_kwargs), "sweep": _sweep_dict()}


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def test_main_writes_output_file_on_valid_config(tmp_path: Path) -> None:
    """完了条件1: 設定ファイルを指定した実行で出力ファイルが生成される。"""
    config_path = tmp_path / "config.json"
    output_path = tmp_path / "output.json"
    _write_json(config_path, _config_dict())

    exit_code = cli.main(["--config", str(config_path), "--output", str(output_path)])

    assert exit_code == 0
    assert output_path.exists()
    result = json.loads(output_path.read_text(encoding="utf-8"))
    assert result["output_schema_version"] == "1.0"
    assert len(result["cells"]) == 1


def test_main_rejects_unknown_top_level_key_without_writing_output(tmp_path: Path) -> None:
    """完了条件2: 不正な設定（未知のトップレベルキー）では出力ファイルが生成されない。"""
    config = _config_dict()
    config["bogus_top_level_key"] = 1
    config_path = tmp_path / "config.json"
    output_path = tmp_path / "output.json"
    _write_json(config_path, config)

    exit_code = cli.main(["--config", str(config_path), "--output", str(output_path)])

    assert exit_code != 0
    assert not output_path.exists()


def test_main_rejects_missing_required_field_without_writing_output(tmp_path: Path) -> None:
    """完了条件2の別ケース: 必須フィールド欠落（drivetrain 省略・--drivetrain 未指定）。"""
    config = _config_dict(include_drivetrain=False)
    config_path = tmp_path / "config.json"
    output_path = tmp_path / "output.json"
    _write_json(config_path, config)

    exit_code = cli.main(["--config", str(config_path), "--output", str(output_path)])

    assert exit_code != 0
    assert not output_path.exists()


def test_main_drivetrain_override_wheel_shape(tmp_path: Path) -> None:
    """`--drivetrain` のホイール由来形式が `DrivetrainParams.from_wheel` と一致する。"""
    config = _config_dict(include_drivetrain=False)
    config_path = tmp_path / "config.json"
    output_path = tmp_path / "output.json"
    drivetrain_path = tmp_path / "drivetrain.json"
    wheel_data = _wheel_drivetrain_dict()
    _write_json(config_path, config)
    _write_json(drivetrain_path, wheel_data)

    exit_code = cli.main(
        [
            "--config",
            str(config_path),
            "--output",
            str(output_path),
            "--drivetrain",
            str(drivetrain_path),
        ]
    )

    assert exit_code == 0
    result = json.loads(output_path.read_text(encoding="utf-8"))
    expected = DrivetrainParams.from_wheel(
        wheel_diameter_mm=60.0,
        motor_rpm=530.0,
        speed_efficiency=1.0,
        max_accel_mm_s2=3000.0,
        max_decel_mm_s2=3000.0,
        control_period_ms=20.0,
        command_latency_ms=50.0,
    )
    assert result["parameters"]["drivetrain"]["max_speed_mm_s"] == expected.max_speed_mm_s


def test_main_drivetrain_override_direct_shape(tmp_path: Path) -> None:
    """`--drivetrain` の直接形式（フラットな DrivetrainParams フィールド集合）。"""
    config = _config_dict(include_drivetrain=False)
    config_path = tmp_path / "config.json"
    output_path = tmp_path / "output.json"
    drivetrain_path = tmp_path / "drivetrain.json"
    _write_json(config_path, config)
    _write_json(drivetrain_path, _drivetrain_dict(max_speed_mm_s=1234.5))

    exit_code = cli.main(
        [
            "--config",
            str(config_path),
            "--output",
            str(output_path),
            "--drivetrain",
            str(drivetrain_path),
        ]
    )

    assert exit_code == 0
    result = json.loads(output_path.read_text(encoding="utf-8"))
    assert result["parameters"]["drivetrain"]["max_speed_mm_s"] == 1234.5


def test_main_drivetrain_override_takes_precedence_over_config(tmp_path: Path) -> None:
    """`--config` 側に parameters.drivetrain があっても `--drivetrain` が常に勝つ。"""
    config = _config_dict(include_drivetrain=True, drivetrain_max_speed_mm_s=999.0)
    config_path = tmp_path / "config.json"
    output_path = tmp_path / "output.json"
    drivetrain_path = tmp_path / "drivetrain.json"
    _write_json(config_path, config)
    _write_json(drivetrain_path, _drivetrain_dict(max_speed_mm_s=1234.5))

    exit_code = cli.main(
        [
            "--config",
            str(config_path),
            "--output",
            str(output_path),
            "--drivetrain",
            str(drivetrain_path),
        ]
    )

    assert exit_code == 0
    result = json.loads(output_path.read_text(encoding="utf-8"))
    assert result["parameters"]["drivetrain"]["max_speed_mm_s"] == 1234.5


def test_main_drivetrain_rejects_wheel_and_direct_fields_together(tmp_path: Path) -> None:
    """`--drivetrain` にホイール由来と直接指定の両方があれば `ParameterError` で拒否する。"""
    config = _config_dict(include_drivetrain=False)
    config_path = tmp_path / "config.json"
    output_path = tmp_path / "output.json"
    drivetrain_path = tmp_path / "drivetrain.json"
    conflicting = _wheel_drivetrain_dict()
    conflicting["max_speed_mm_s"] = 1234.5
    _write_json(config_path, config)
    _write_json(drivetrain_path, conflicting)

    exit_code = cli.main(
        [
            "--config",
            str(config_path),
            "--output",
            str(output_path),
            "--drivetrain",
            str(drivetrain_path),
        ]
    )

    assert exit_code != 0
    assert not output_path.exists()


def test_main_rejects_invalid_config_drivetrain_block_even_when_overridden_by_drivetrain_flag(
    tmp_path: Path,
) -> None:
    """`--config` 側の `parameters.drivetrain` が不正なら、`--drivetrain` で

    上書きされて最終的に使われなくなるとしても検証だけは必ず行い、
    パラメータ不正として拒否する（`--drivetrain` の優先順位は「使う値」の
    話であって「検証を省略してよい」という話ではない）。
    """
    config = _config_dict(include_drivetrain=True)
    config["parameters"]["drivetrain"] = {"totally_bogus_key": 123}
    config_path = tmp_path / "config.json"
    output_path = tmp_path / "output.json"
    drivetrain_path = tmp_path / "drivetrain.json"
    _write_json(config_path, config)
    _write_json(drivetrain_path, _drivetrain_dict(max_speed_mm_s=1234.5))

    exit_code = cli.main(
        [
            "--config",
            str(config_path),
            "--output",
            str(output_path),
            "--drivetrain",
            str(drivetrain_path),
        ]
    )

    assert exit_code != 0
    assert not output_path.exists()


def test_main_rejects_unknown_key_nested_inside_parameters_throw(tmp_path: Path) -> None:
    """未知のキーは `parameters.throw` のようなネストの内側でも拒否される。"""
    config = _config_dict(throw_unknown_key=True)
    config_path = tmp_path / "config.json"
    output_path = tmp_path / "output.json"
    _write_json(config_path, config)

    exit_code = cli.main(["--config", str(config_path), "--output", str(output_path)])

    assert exit_code != 0
    assert not output_path.exists()


def test_main_coerces_catch_policy_enum_string(tmp_path: Path) -> None:
    """`parameters.catch.policy` の文字列が `CatchPolicy` へ正しく coerce される。"""
    config = _config_dict(catch_policy="pass_through")
    config_path = tmp_path / "config.json"
    output_path = tmp_path / "output.json"
    _write_json(config_path, config)

    exit_code = cli.main(["--config", str(config_path), "--output", str(output_path)])

    assert exit_code == 0
    result = json.loads(output_path.read_text(encoding="utf-8"))
    assert result["parameters"]["catch"]["policy"] == "pass_through"


def test_main_stdout_summary_is_exactly_one_line(tmp_path: Path, capsys) -> None:
    """成功時、標準出力への要約は1行のみである（design.md「Cli」Batch/Job Contract）。"""
    config_path = tmp_path / "config.json"
    output_path = tmp_path / "output.json"
    _write_json(config_path, _config_dict())

    exit_code = cli.main(["--config", str(config_path), "--output", str(output_path)])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert len(captured.out.strip().splitlines()) == 1


def test_python_dash_m_trajectory_sim_end_to_end(tmp_path: Path) -> None:
    """`python -m trajectory_sim ...` が実際のサブプロセスとして動作する（`__main__.py` の配線を検証）。"""
    config_path = tmp_path / "config.json"
    output_path = tmp_path / "output.json"
    _write_json(config_path, _config_dict())

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "trajectory_sim",
            "--config",
            str(config_path),
            "--output",
            str(output_path),
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert output_path.exists()


def test_cli_module_has_no_server_or_network_imports() -> None:
    """`cli.py` / `__main__.py` に常駐サーバ・ソケット相当の import が無いことの軽い静的検査。

    本タスクの範囲での回帰ガードであり、網羅的な静的検査は別タスク（5.4
    `BoundaryCheck`）が担う。
    """
    forbidden_modules = ("socket", "http", "asyncio", "urllib")
    for filename in ("cli.py", "__main__.py"):
        source = (REPO_ROOT / "src" / "trajectory_sim" / filename).read_text(encoding="utf-8")
        for module_name in forbidden_modules:
            assert f"import {module_name}" not in source
            assert f"from {module_name}" not in source
