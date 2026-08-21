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


def test_serialize_module_does_not_import_prediction_core_or_errors() -> None:
    """本モジュールが `prediction_core` / `trajectory_sim.errors` の
    いずれも import しないこと（design.md「Dependency Direction」境界）。
    """
    source = inspect.getsource(serialize)
    assert "import prediction_core" not in source
    assert "from prediction_core" not in source
    assert "trajectory_sim.errors" not in source
    assert "from trajectory_sim import errors" not in source


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
