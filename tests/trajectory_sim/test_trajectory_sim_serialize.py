"""ResultSerializer（`sweep_result_to_dict`）のテスト（要件7.1, 7.2, 7.3,
7.6, 7.7, 9.1, 9.3, 9.4, 9.6, 9.7 / design.md「ResultSerializer」）。

`trajectory_sim.serialize` が以下を満たすことを固定する:

- 較正段階が未較正（既定値）のとき `calibration.notice` が必ず含まれ、
  M1/M2 較正済みのときは `stage` のみで `notice` キー自体が存在しない
  こと（要件9.1, 9.3）
- `model_exclusions` / `parameter_provenance` / `sweep.axes`（名前・単位・
  値）/ `output_schema_version` が出力に含まれること（要件7.2, 7.3, 7.6,
  9.4, 9.7）
- `parameters` が `ScenarioParams` 全体（`prediction` を含む）を、
  `prediction_core` を import せずに再帰的に辞書化していること（要件7.2）
- `cells` の各要素が `axis_values` / `status` / `success_ratio` /
  `metrics` / `not_evaluated_reason` の5キーを持ち、enum が `.value` へ
  変換されていること（要件6.5, 6.7, 10.4）
- 代表シナリオが保持されている場合のみ `throw_records` が非空になり、
  中身は `ThrowRecord.to_dict()` の結果そのものであること（要件7.4）
- `sweep.catch_ratio_threshold` が `cells` と同一ドキュメント内に存在し、
  成立割合の判定閾値が常に併記されること（要件9.6）
- 本モジュールが `prediction_core` / `trajectory_sim.errors` のいずれも
  import しないこと（design.md「Dependency Direction」境界）

ファイル名は `test_trajectory_sim_serialize.py`
（`tests/trajectory_sim/test_trajectory_sim_*.py` の命名方針を踏襲する）。
"""

from __future__ import annotations

import inspect
from types import MappingProxyType

import pytest
from prediction_core import PredictionConfig

from trajectory_sim import results as results_module
from trajectory_sim import serialize
from trajectory_sim.params import (
    CalibrationStage,
    CatchCriteria,
    DrivetrainParams,
    LayoutParams,
    ObservationParams,
    Provenance,
    ScenarioParams,
    ThrowDispersion,
    ThrowParams,
)
from trajectory_sim.results import CellResult, CellStatus, NotEvaluatedReason, SweepResult
from trajectory_sim.serialize import OUTPUT_SCHEMA_VERSION, sweep_result_to_dict
from trajectory_sim.sweep import AxisSpec, SweepKind, SweepSpec, run_sweep

# ---------------------------------------------------------------------------
# 共通フィクスチャ（test_trajectory_sim_sweep.py と同じ構成方針）
# ---------------------------------------------------------------------------


def _throw(**overrides: float) -> ThrowParams:
    base: dict[str, float] = dict(
        release_x_mm=0.0,
        release_y_mm=0.0,
        release_z_mm=2000.0,
        speed_mm_s=4000.0,
        elevation_deg=45.0,
        azimuth_deg=0.0,
    )
    base.update(overrides)
    return ThrowParams(**base)


def _observation_params(**overrides: float) -> ObservationParams:
    base: dict[str, float] = dict(
        detection_start_delay_ms=20.0,
        sample_period_ms=20.0,
        sample_latency_ms=5.0,
        prediction_latency_ms=3.0,
    )
    base.update(overrides)
    return ObservationParams(**base)


def _drivetrain(**overrides: float) -> DrivetrainParams:
    base: dict[str, float] = dict(
        max_speed_mm_s=6000.0,
        max_accel_mm_s2=30000.0,
        max_decel_mm_s2=30000.0,
        control_period_ms=1.0,
        command_latency_ms=1.0,
        integration_step_ms=1.0,
    )
    base.update(overrides)
    return DrivetrainParams(**base)


def _scenario_params(
    *,
    throw: ThrowParams | None = None,
    dispersion: ThrowDispersion | None = None,
    observation_params: ObservationParams | None = None,
    drivetrain_params: DrivetrainParams | None = None,
    catch: CatchCriteria | None = None,
    layout: LayoutParams | None = None,
    prediction: PredictionConfig | None = None,
    calibration_stage: CalibrationStage = CalibrationStage.UNCALIBRATED,
    provenance: dict[str, Provenance] | None = None,
) -> ScenarioParams:
    return ScenarioParams(
        throw=throw if throw is not None else _throw(),
        dispersion=dispersion if dispersion is not None else ThrowDispersion(),
        observation=(
            observation_params if observation_params is not None else _observation_params()
        ),
        drivetrain=drivetrain_params if drivetrain_params is not None else _drivetrain(),
        catch=catch if catch is not None else CatchCriteria(),
        layout=layout if layout is not None else LayoutParams(home_x_mm=0.0, home_y_mm=0.0),
        prediction=prediction if prediction is not None else PredictionConfig(),
        calibration_stage=calibration_stage,
        provenance=provenance if provenance is not None else {},
    )


def _reachability_spec() -> SweepSpec:
    return SweepSpec(
        kind=SweepKind.REACHABILITY,
        axes=(
            AxisSpec(name="hold_time_ms", unit="ms", values=(200.0, 2000.0)),
            AxisSpec(name="required_distance_mm", unit="mm", values=(100.0,)),
        ),
        trials_per_cell=1,
    )


# ---------------------------------------------------------------------------
# 完了条件1（必須）: 既定（未較正）の全必須項目
# ---------------------------------------------------------------------------


def test_sweep_result_to_dict_reflects_uncalibrated_default_and_required_fields() -> None:
    base_params = _scenario_params(
        provenance={"throw.speed_mm_s": Provenance.MEASURED},
    )
    assert base_params.calibration_stage is CalibrationStage.UNCALIBRATED  # 既定値の確認

    spec = _reachability_spec()
    result = run_sweep(spec, base_params)

    output = sweep_result_to_dict(result)

    assert output["output_schema_version"] == "1.0"
    assert output["output_schema_version"] == OUTPUT_SCHEMA_VERSION

    assert output["calibration"]["stage"] == "uncalibrated"
    assert isinstance(output["calibration"]["notice"], str)
    assert len(output["calibration"]["notice"]) > 0

    model_exclusions = output["model_exclusions"]
    assert set(model_exclusions.keys()) == set(results_module.MODEL_EXCLUSIONS.keys())
    for key, expected_values in results_module.MODEL_EXCLUSIONS.items():
        assert tuple(model_exclusions[key]) == expected_values

    assert output["parameter_provenance"] == {"throw.speed_mm_s": "measured"}

    axes = output["sweep"]["axes"]
    assert len(axes) == 2
    assert axes[0] == {"name": "hold_time_ms", "unit": "ms", "values": [200.0, 2000.0]}
    assert axes[1] == {"name": "required_distance_mm", "unit": "mm", "values": [100.0]}
    assert output["sweep"]["kind"] == "reachability"
    assert output["sweep"]["trials_per_cell"] == 1
    assert output["sweep"]["seed"] == 0
    assert "catch_ratio_threshold" in output["sweep"]


# ---------------------------------------------------------------------------
# 完了条件2: 較正済みの場合 notice キー自体が存在しない
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stage", "expected_value"),
    [
        (CalibrationStage.M1_CALIBRATED, "m1_calibrated"),
        (CalibrationStage.M2_CALIBRATED, "m2_calibrated"),
    ],
)
def test_sweep_result_to_dict_omits_notice_when_calibrated(
    stage: CalibrationStage, expected_value: str
) -> None:
    base_params = _scenario_params(calibration_stage=stage)
    result = run_sweep(_reachability_spec(), base_params)

    output = sweep_result_to_dict(result)

    assert output["calibration"]["stage"] == expected_value
    assert "notice" not in output["calibration"]


# ---------------------------------------------------------------------------
# 完了条件3: parameters の完全な再帰的ダンプ（prediction を含む）
# ---------------------------------------------------------------------------


def test_sweep_result_to_dict_parameters_full_recursive_dump() -> None:
    base_params = _scenario_params()
    result = run_sweep(_reachability_spec(), base_params)

    output = sweep_result_to_dict(result)
    parameters = output["parameters"]

    assert parameters["throw"]["speed_mm_s"] == base_params.throw.speed_mm_s
    assert parameters["drivetrain"]["max_speed_mm_s"] == base_params.drivetrain.max_speed_mm_s
    assert parameters["catch"]["policy"] == "stop_and_wait"
    assert parameters["prediction"]["gravity_mm_s2"] == base_params.prediction.gravity_mm_s2

    # 同じ provenance データが parameters.provenance にも parameter_provenance
    # にも重複して現れることは仕様どおりの意図的な重複である。
    assert parameters["provenance"] == output["parameter_provenance"]


def test_serialize_module_does_not_import_prediction_core() -> None:
    """本モジュールが `prediction_core` を import しないこと
    （design.md「Dependency Direction」境界）。

    `trajectory_sim.errors`（`OutputError`）は、タスク4.2で本モジュール
    自身が出力契約の検証・拒否を担うようになったため、正当に import する
    （design.md「ResultSerializer」Batch / Job Contract）。この点は
    `prediction_core` の禁輸とは独立した別の境界であり、
    `test_write_sweep_result_uses_output_error` 等が
    `trajectory_sim.errors.OutputError` の使用を別途固定する。
    """
    source = inspect.getsource(serialize)
    assert "import prediction_core" not in source
    assert "from prediction_core" not in source


# ---------------------------------------------------------------------------
# 完了条件4: cells 構造（catchable / not_catchable / not_evaluated 混在）
# ---------------------------------------------------------------------------


def test_sweep_result_to_dict_cells_structure_with_mixed_statuses() -> None:
    spec = SweepSpec(
        kind=SweepKind.THROW,
        axes=(AxisSpec(name="throw.speed_mm_s", unit="mm/s", values=(1000.0, 2000.0, 3000.0)),),
        trials_per_cell=1,
    )
    cell_catchable = CellResult(
        axis_values=(1000.0,),
        status=CellStatus.CATCHABLE,
        trials=1,
        evaluated_trials=1,
        success_count=1,
        success_ratio=1.0,
        metrics=MappingProxyType({"position_error_mm": 10.0}),
        not_evaluated_reason=None,
        representative=None,
    )
    cell_not_catchable = CellResult(
        axis_values=(2000.0,),
        status=CellStatus.NOT_CATCHABLE,
        trials=1,
        evaluated_trials=1,
        success_count=0,
        success_ratio=0.0,
        metrics=MappingProxyType({"position_error_mm": 500.0}),
        not_evaluated_reason=None,
        representative=None,
    )
    cell_not_evaluated = CellResult(
        axis_values=(3000.0,),
        status=CellStatus.NOT_EVALUATED,
        trials=1,
        evaluated_trials=0,
        success_count=0,
        success_ratio=None,
        metrics=MappingProxyType({}),
        not_evaluated_reason=NotEvaluatedReason.NO_SAMPLES,
        representative=None,
    )
    result = SweepResult(
        spec=spec,
        base_params=_scenario_params(),
        cells=(cell_catchable, cell_not_catchable, cell_not_evaluated),
    )

    output = sweep_result_to_dict(result)
    cells = output["cells"]
    assert len(cells) == 3

    assert cells[0] == {
        "axis_values": [1000.0],
        "status": "catchable",
        "success_ratio": 1.0,
        "metrics": {"position_error_mm": 10.0},
        "not_evaluated_reason": None,
    }
    assert cells[1] == {
        "axis_values": [2000.0],
        "status": "not_catchable",
        "success_ratio": 0.0,
        "metrics": {"position_error_mm": 500.0},
        "not_evaluated_reason": None,
    }
    assert cells[2] == {
        "axis_values": [3000.0],
        "status": "not_evaluated",
        "success_ratio": None,
        "metrics": {},
        "not_evaluated_reason": "no_samples",
    }


# ---------------------------------------------------------------------------
# 完了条件5: throw_records（代表シナリオの有無）
# ---------------------------------------------------------------------------


def test_sweep_result_to_dict_throw_records_present_when_keep_representative_record_true() -> (
    None
):
    spec = SweepSpec(
        kind=SweepKind.THROW,
        axes=(AxisSpec(name="throw.speed_mm_s", unit="mm/s", values=(4000.0,)),),
        trials_per_cell=1,
        keep_representative_record=True,
    )
    result = run_sweep(spec, _scenario_params())
    assert result.cells[0].representative is not None
    record = result.cells[0].representative.record
    assert record is not None

    output = sweep_result_to_dict(result)

    assert isinstance(output["throw_records"], list)
    assert len(output["throw_records"]) == 1
    embedded = output["throw_records"][0]
    assert embedded == record.to_dict()
    assert embedded["record_id"] == record.record_id
    assert embedded["source"] == record.source.value
    assert embedded["schema_version"] == record.schema_version


def test_sweep_result_to_dict_throw_records_empty_when_disabled() -> None:
    spec = SweepSpec(
        kind=SweepKind.THROW,
        axes=(AxisSpec(name="throw.speed_mm_s", unit="mm/s", values=(4000.0,)),),
        trials_per_cell=1,
        keep_representative_record=False,
    )
    result = run_sweep(spec, _scenario_params())

    output = sweep_result_to_dict(result)

    assert output["throw_records"] == []


# ---------------------------------------------------------------------------
# 完了条件6: catch_ratio_threshold の併記（要件9.6）
# ---------------------------------------------------------------------------


def test_sweep_result_to_dict_catch_ratio_threshold_colocated_with_cells() -> None:
    spec = SweepSpec(
        kind=SweepKind.THROW,
        axes=(AxisSpec(name="throw.speed_mm_s", unit="mm/s", values=(1000.0,)),),
        trials_per_cell=2,
        catch_ratio_threshold=0.5,
    )
    cell = CellResult(
        axis_values=(1000.0,),
        status=CellStatus.CATCHABLE,
        trials=2,
        evaluated_trials=2,
        success_count=1,
        success_ratio=0.5,
        metrics=MappingProxyType({}),
        not_evaluated_reason=None,
        representative=None,
    )
    result = SweepResult(spec=spec, base_params=_scenario_params(), cells=(cell,))

    output = sweep_result_to_dict(result)

    assert output["sweep"]["catch_ratio_threshold"] == 0.5
    assert output["cells"][0]["success_ratio"] == 0.5


# ---------------------------------------------------------------------------
# タスク4.2: write_sweep_result / _validate_output_dict
# （要件7.4, 7.5, 8.2, 9.5, 10.5, 5.8）
# ---------------------------------------------------------------------------

import json

from trajectory_sim.errors import OutputError
from trajectory_sim.serialize import (
    _validate_output_dict,
    write_sweep_result,
)


def _small_throw_result() -> SweepResult:
    """小さく決定的な THROW 掃引結果を作る（代表シナリオ保持あり）。"""
    spec = SweepSpec(
        kind=SweepKind.THROW,
        axes=(AxisSpec(name="throw.speed_mm_s", unit="mm/s", values=(4000.0,)),),
        trials_per_cell=1,
        keep_representative_record=True,
    )
    return run_sweep(spec, _scenario_params())


def _valid_result_dict() -> dict[str, object]:
    return sweep_result_to_dict(_small_throw_result())


# --- 完了条件1（必須）: 同一結果を2回書き出すとバイト単位で一致する ------


def test_write_sweep_result_is_byte_identical_across_two_writes(tmp_path) -> None:
    result = _small_throw_result()
    path1 = tmp_path / "out1.json"
    path2 = tmp_path / "out2.json"

    write_sweep_result(result, path1)
    write_sweep_result(result, path2)

    assert path1.read_bytes() == path2.read_bytes()


def test_write_sweep_result_is_byte_identical_for_independently_computed_results(
    tmp_path,
) -> None:
    """同一の設定と同一の種で掃引を2回実行した場合の同一出力（要件8.2）。"""
    spec = _reachability_spec()
    base_params = _scenario_params()
    result_a = run_sweep(spec, base_params)
    result_b = run_sweep(spec, base_params)

    path_a = tmp_path / "a.json"
    path_b = tmp_path / "b.json"
    write_sweep_result(result_a, path_a)
    write_sweep_result(result_b, path_b)

    assert path_a.read_bytes() == path_b.read_bytes()


# --- 完了条件2（必須）: 断定的な最上位キーの検出と拒否（要件9.5） --------


def test_validate_output_dict_accepts_clean_dict() -> None:
    result_dict = _valid_result_dict()
    _validate_output_dict(result_dict)  # 例外を送出しないことを確認する


@pytest.mark.parametrize("assertive_key", ["nfr7_achieved", "passed", "success"])
def test_validate_output_dict_rejects_assertive_top_level_key(assertive_key: str) -> None:
    result_dict = _valid_result_dict()
    result_dict[assertive_key] = True

    with pytest.raises(OutputError):
        _validate_output_dict(result_dict)


def test_validate_output_dict_rejects_assertive_calibration_key() -> None:
    result_dict = _valid_result_dict()
    result_dict["calibration"]["nfr7_achieved"] = True

    with pytest.raises(OutputError):
        _validate_output_dict(result_dict)


# --- 非有限値の拒否 --------------------------------------------------------


def test_validate_output_dict_rejects_non_finite_value_in_parameters() -> None:
    result_dict = _valid_result_dict()
    result_dict["parameters"]["throw"]["speed_mm_s"] = float("nan")

    with pytest.raises(OutputError):
        _validate_output_dict(result_dict)


def test_validate_output_dict_rejects_non_finite_value_in_cells_metrics() -> None:
    result_dict = _valid_result_dict()
    result_dict["cells"][0]["metrics"]["position_error_mm"] = float("inf")

    with pytest.raises(OutputError):
        _validate_output_dict(result_dict)


def test_write_sweep_result_leaves_no_file_when_validation_fails(tmp_path) -> None:
    """検証が失敗した場合、対象パスにファイルが一切作られないこと
    （「部分的なファイルを残さない」、アトミック性の裏付け）。

    `ThrowParams` 等は構築時点で非有限値を `ParameterError` で拒否する
    ため、`base_params` 経由では非有限値を持つ `SweepResult` を作れない。
    `CellResult.metrics`（`Mapping[str, float]`）は構築時点で値の中身を
    検証しないため、ここに非有限値を混入させて検証失敗パスを再現する。
    """
    spec = SweepSpec(
        kind=SweepKind.THROW,
        axes=(AxisSpec(name="throw.speed_mm_s", unit="mm/s", values=(1000.0,)),),
        trials_per_cell=1,
    )
    broken_cell = CellResult(
        axis_values=(1000.0,),
        status=CellStatus.CATCHABLE,
        trials=1,
        evaluated_trials=1,
        success_count=1,
        success_ratio=1.0,
        metrics=MappingProxyType({"position_error_mm": float("nan")}),
        not_evaluated_reason=None,
        representative=None,
    )
    broken_result = SweepResult(spec=spec, base_params=_scenario_params(), cells=(broken_cell,))

    target_path = tmp_path / "should_not_exist.json"
    with pytest.raises(OutputError):
        write_sweep_result(broken_result, target_path)

    assert not target_path.exists()


# --- 未知の enum 値の拒否 --------------------------------------------------


def test_validate_output_dict_rejects_unknown_cell_status() -> None:
    result_dict = _valid_result_dict()
    result_dict["cells"][0]["status"] = "bogus_status"

    with pytest.raises(OutputError):
        _validate_output_dict(result_dict)


def test_validate_output_dict_rejects_unknown_calibration_stage() -> None:
    result_dict = _valid_result_dict()
    result_dict["calibration"]["stage"] = "bogus_stage"

    with pytest.raises(OutputError):
        _validate_output_dict(result_dict)


def test_validate_output_dict_rejects_unknown_sweep_kind() -> None:
    result_dict = _valid_result_dict()
    result_dict["sweep"]["kind"] = "bogus_kind"

    with pytest.raises(OutputError):
        _validate_output_dict(result_dict)


def test_validate_output_dict_rejects_unknown_catch_policy() -> None:
    result_dict = _valid_result_dict()
    result_dict["parameters"]["catch"]["policy"] = "bogus_policy"

    with pytest.raises(OutputError):
        _validate_output_dict(result_dict)


def test_validate_output_dict_rejects_unknown_provenance_value() -> None:
    result_dict = _valid_result_dict()
    result_dict["parameter_provenance"] = {"throw.speed_mm_s": "bogus_provenance"}

    with pytest.raises(OutputError):
        _validate_output_dict(result_dict)


def test_validate_output_dict_rejects_unknown_not_evaluated_reason() -> None:
    result_dict = _valid_result_dict()
    result_dict["cells"][0]["not_evaluated_reason"] = "bogus_reason"

    with pytest.raises(OutputError):
        _validate_output_dict(result_dict)


# --- write_sweep_result が有効な JSON を書き出す ---------------------------


def test_write_sweep_result_writes_valid_json_that_round_trips(tmp_path) -> None:
    result = _small_throw_result()
    path = tmp_path / "result.json"

    write_sweep_result(result, path)

    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["output_schema_version"] == OUTPUT_SCHEMA_VERSION
    assert loaded["calibration"]["stage"] == "uncalibrated"
    assert loaded["sweep"]["kind"] == "throw"
    assert isinstance(loaded["cells"], list)


def test_write_sweep_result_preserves_non_ascii_literally(tmp_path) -> None:
    """`ensure_ascii=False` により日本語がエスケープされずそのまま出る
    こと（未較正時の `calibration.notice` を使って確認する）。
    """
    result = _small_throw_result()
    path = tmp_path / "result.json"

    write_sweep_result(result, path)

    raw_text = path.read_text(encoding="utf-8")
    assert "\\u" not in raw_text
    assert "本結果は較正段階が未較正のため" in raw_text


def test_write_sweep_result_output_is_indented_multiline(tmp_path) -> None:
    result = _small_throw_result()
    path = tmp_path / "result.json"

    write_sweep_result(result, path)

    raw_text = path.read_text(encoding="utf-8")
    assert "\n" in raw_text
    assert raw_text.count("\n") > 5


# --- 既存ファイルの上書き（要件相当: design.md「既存ファイルは上書きする」）


def test_write_sweep_result_overwrites_existing_file(tmp_path) -> None:
    path = tmp_path / "result.json"
    path.write_text("古い内容", encoding="utf-8")

    result = _small_throw_result()
    write_sweep_result(result, path)

    new_text = path.read_text(encoding="utf-8")
    assert new_text != "古い内容"
    loaded = json.loads(new_text)
    assert loaded["output_schema_version"] == OUTPUT_SCHEMA_VERSION
