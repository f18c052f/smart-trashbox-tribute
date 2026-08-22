"""`trajectory_sim.params` のデータクラス構築と検証を固定するテスト
（task 1.4 / design.md「Params」/ 要件 1.4, 2.6, 4.1, 4.3, 5.1, 5.4, 7.7, 9.2, 10.1）。

命名は `tests/trajectory_sim/` の既存規約（`test_trajectory_sim_*.py`）に
合わせ、`tests/prediction_core/` とのベース名衝突（pytest の prepend
インポートモード由来）を避ける。
"""

from __future__ import annotations

import dataclasses
import math

import pytest
from prediction_core import PredictionConfig, Sample

from trajectory_sim.errors import ParameterError, SweepDefinitionError
from trajectory_sim.params import (
    PARAMETER_PATHS,
    CalibrationStage,
    CatchCriteria,
    CatchPolicy,
    DrivetrainParams,
    LayoutParams,
    ObservationParams,
    ParameterPath,
    Provenance,
    ScenarioParams,
    ThrowDispersion,
    ThrowParams,
    make_sample,
    replace_by_path,
)


def _valid_throw() -> ThrowParams:
    return ThrowParams(
        release_x_mm=0.0,
        release_y_mm=0.0,
        release_z_mm=1000.0,
        speed_mm_s=3000.0,
        elevation_deg=45.0,
        azimuth_deg=0.0,
    )


def _valid_observation() -> ObservationParams:
    return ObservationParams(
        detection_start_delay_ms=50.0,
        sample_period_ms=16.0,
        sample_latency_ms=5.0,
        prediction_latency_ms=5.0,
    )


def _valid_drivetrain() -> DrivetrainParams:
    return DrivetrainParams(
        max_speed_mm_s=1500.0,
        max_accel_mm_s2=2000.0,
        max_decel_mm_s2=2500.0,
        control_period_ms=10.0,
        command_latency_ms=20.0,
    )


def _valid_scenario_params() -> ScenarioParams:
    return ScenarioParams(
        throw=_valid_throw(),
        dispersion=ThrowDispersion(),
        observation=_valid_observation(),
        drivetrain=_valid_drivetrain(),
        catch=CatchCriteria(),
        layout=LayoutParams(home_x_mm=500.0, home_y_mm=500.0),
        prediction=PredictionConfig(),
    )


# ---------------------------------------------------------------------------
# 正常系: 最小構成で構築でき、frozen/slots であること
# ---------------------------------------------------------------------------


class TestConstructionHappyPath:
    def test_scenario_params_constructs_with_valid_values(self) -> None:
        params = _valid_scenario_params()
        assert isinstance(params, ScenarioParams)
        assert params.calibration_stage is CalibrationStage.UNCALIBRATED
        assert params.provenance == {}

    def test_throw_params_is_frozen(self) -> None:
        throw = _valid_throw()
        with pytest.raises(dataclasses.FrozenInstanceError):
            throw.speed_mm_s = 1.0  # type: ignore[misc]

    def test_throw_params_has_no_dict_slots_only(self) -> None:
        throw = _valid_throw()
        assert not hasattr(throw, "__dict__")

    def test_drivetrain_params_optional_wheel_fields_default_none(self) -> None:
        drivetrain = _valid_drivetrain()
        assert drivetrain.wheel_diameter_mm is None
        assert drivetrain.motor_rpm is None
        assert drivetrain.speed_efficiency is None

    def test_drivetrain_params_accepts_optional_wheel_fields(self) -> None:
        drivetrain = DrivetrainParams(
            max_speed_mm_s=1500.0,
            max_accel_mm_s2=2000.0,
            max_decel_mm_s2=2500.0,
            control_period_ms=10.0,
            command_latency_ms=20.0,
            wheel_diameter_mm=60.0,
            motor_rpm=3000.0,
            speed_efficiency=0.9,
        )
        assert drivetrain.wheel_diameter_mm == 60.0
        assert drivetrain.motor_rpm == 3000.0
        assert drivetrain.speed_efficiency == 0.9


# ---------------------------------------------------------------------------
# 既定値の固定
# ---------------------------------------------------------------------------


class TestDefaults:
    def test_calibration_stage_default_is_uncalibrated(self) -> None:
        params = _valid_scenario_params()
        assert params.calibration_stage is CalibrationStage.UNCALIBRATED
        assert CalibrationStage.UNCALIBRATED == "uncalibrated"

    def test_throw_object_diameter_default_is_can_placeholder(self) -> None:
        throw = _valid_throw()
        assert throw.object_diameter_mm == 65.0

    def test_throw_gravity_default(self) -> None:
        throw = _valid_throw()
        assert throw.gravity_mm_s2 == pytest.approx(9806.65)

    def test_dispersion_defaults_are_zero(self) -> None:
        dispersion = ThrowDispersion()
        assert dispersion.speed_sigma_mm_s == 0.0
        assert dispersion.elevation_sigma_deg == 0.0
        assert dispersion.azimuth_sigma_deg == 0.0
        assert dispersion.release_sigma_mm == 0.0

    def test_catch_criteria_defaults(self) -> None:
        catch = CatchCriteria()
        assert catch.policy is CatchPolicy.STOP_AND_WAIT
        assert catch.position_tolerance_mm == 67.5
        assert catch.residual_speed_tolerance_mm_s == 200.0

    def test_drivetrain_integration_step_default(self) -> None:
        drivetrain = _valid_drivetrain()
        assert drivetrain.integration_step_ms == 1.0

    def test_provenance_default_is_empty_dict(self) -> None:
        params = _valid_scenario_params()
        assert params.provenance == {}


# ---------------------------------------------------------------------------
# 機体性能に既定値を与えない: 省略した構築は失敗する（要件4.3）
# ---------------------------------------------------------------------------


class TestDrivetrainRequiredFieldsHaveNoDefault:
    def test_missing_all_required_fields_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            DrivetrainParams()  # type: ignore[call-arg]

    @pytest.mark.parametrize(
        "missing_field",
        [
            "max_speed_mm_s",
            "max_accel_mm_s2",
            "max_decel_mm_s2",
            "control_period_ms",
            "command_latency_ms",
        ],
    )
    def test_missing_single_required_field_raises_type_error(
        self, missing_field: str
    ) -> None:
        kwargs = {
            "max_speed_mm_s": 1500.0,
            "max_accel_mm_s2": 2000.0,
            "max_decel_mm_s2": 2500.0,
            "control_period_ms": 10.0,
            "command_latency_ms": 20.0,
        }
        del kwargs[missing_field]
        with pytest.raises(TypeError):
            DrivetrainParams(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 構築時検証: 違反フィールド名と値を含むメッセージで ParameterError を送出する
# ---------------------------------------------------------------------------


class TestObservationParamsValidation:
    @pytest.mark.parametrize(
        "field_name",
        [
            "detection_start_delay_ms",
            "sample_period_ms",
            "sample_latency_ms",
            "prediction_latency_ms",
            "sigma_x_mm",
            "sigma_y_mm",
            "sigma_z_mm",
            "distance_sigma_rel_per_m2",
        ],
    )
    def test_negative_nonneg_field_rejected(self, field_name: str) -> None:
        kwargs = dict(
            detection_start_delay_ms=50.0,
            sample_period_ms=16.0,
            sample_latency_ms=5.0,
            prediction_latency_ms=5.0,
        )
        kwargs[field_name] = -1.0
        with pytest.raises(ParameterError) as excinfo:
            ObservationParams(**kwargs)
        assert field_name in str(excinfo.value)
        assert "-1.0" in str(excinfo.value)

    @pytest.mark.parametrize("bad_value", [-0.1, 1.0, 1.5, math.inf, math.nan])
    def test_dropout_ratio_out_of_range_rejected(self, bad_value: float) -> None:
        with pytest.raises(ParameterError) as excinfo:
            ObservationParams(
                detection_start_delay_ms=0.0,
                sample_period_ms=1.0,
                sample_latency_ms=0.0,
                prediction_latency_ms=0.0,
                dropout_ratio=bad_value,
            )
        assert "dropout_ratio" in str(excinfo.value)

    def test_dropout_ratio_boundary_zero_is_accepted(self) -> None:
        obs = ObservationParams(
            detection_start_delay_ms=0.0,
            sample_period_ms=1.0,
            sample_latency_ms=0.0,
            prediction_latency_ms=0.0,
            dropout_ratio=0.0,
        )
        assert obs.dropout_ratio == 0.0

    def test_dropout_ratio_just_below_one_is_accepted(self) -> None:
        obs = ObservationParams(
            detection_start_delay_ms=0.0,
            sample_period_ms=1.0,
            sample_latency_ms=0.0,
            prediction_latency_ms=0.0,
            dropout_ratio=0.999,
        )
        assert obs.dropout_ratio == 0.999

    def test_non_finite_delay_rejected(self) -> None:
        with pytest.raises(ParameterError) as excinfo:
            ObservationParams(
                detection_start_delay_ms=math.inf,
                sample_period_ms=1.0,
                sample_latency_ms=0.0,
                prediction_latency_ms=0.0,
            )
        assert "detection_start_delay_ms" in str(excinfo.value)

    def test_observer_position_must_be_finite(self) -> None:
        with pytest.raises(ParameterError) as excinfo:
            ObservationParams(
                detection_start_delay_ms=0.0,
                sample_period_ms=1.0,
                sample_latency_ms=0.0,
                prediction_latency_ms=0.0,
                observer_x_mm=math.nan,
            )
        assert "observer_x_mm" in str(excinfo.value)


class TestThrowDispersionValidation:
    @pytest.mark.parametrize(
        "field_name",
        [
            "speed_sigma_mm_s",
            "elevation_sigma_deg",
            "azimuth_sigma_deg",
            "release_sigma_mm",
        ],
    )
    def test_negative_sigma_rejected(self, field_name: str) -> None:
        with pytest.raises(ParameterError) as excinfo:
            ThrowDispersion(**{field_name: -0.5})
        assert field_name in str(excinfo.value)
        assert "-0.5" in str(excinfo.value)


class TestDrivetrainParamsValidation:
    @pytest.mark.parametrize(
        "field_name",
        [
            "max_speed_mm_s",
            "max_accel_mm_s2",
            "max_decel_mm_s2",
            "control_period_ms",
            "command_latency_ms",
            "integration_step_ms",
        ],
    )
    @pytest.mark.parametrize("bad_value", [0.0, -1.0, math.inf, math.nan])
    def test_non_positive_or_non_finite_rejected(
        self, field_name: str, bad_value: float
    ) -> None:
        kwargs = dict(
            max_speed_mm_s=1500.0,
            max_accel_mm_s2=2000.0,
            max_decel_mm_s2=2500.0,
            control_period_ms=10.0,
            command_latency_ms=20.0,
        )
        kwargs[field_name] = bad_value
        with pytest.raises(ParameterError) as excinfo:
            DrivetrainParams(**kwargs)
        assert field_name in str(excinfo.value)

    @pytest.mark.parametrize(
        "field_name", ["wheel_diameter_mm", "motor_rpm", "speed_efficiency"]
    )
    def test_optional_field_validated_when_provided(self, field_name: str) -> None:
        kwargs = dict(
            max_speed_mm_s=1500.0,
            max_accel_mm_s2=2000.0,
            max_decel_mm_s2=2500.0,
            control_period_ms=10.0,
            command_latency_ms=20.0,
        )
        kwargs[field_name] = -1.0
        with pytest.raises(ParameterError) as excinfo:
            DrivetrainParams(**kwargs)
        assert field_name in str(excinfo.value)

    def test_optional_fields_left_none_do_not_raise(self) -> None:
        drivetrain = _valid_drivetrain()
        assert drivetrain.wheel_diameter_mm is None


class TestThrowParamsValidation:
    def test_non_positive_speed_rejected(self) -> None:
        with pytest.raises(ParameterError) as excinfo:
            ThrowParams(
                release_x_mm=0.0,
                release_y_mm=0.0,
                release_z_mm=0.0,
                speed_mm_s=0.0,
                elevation_deg=45.0,
                azimuth_deg=0.0,
            )
        assert "speed_mm_s" in str(excinfo.value)

    def test_non_positive_gravity_rejected(self) -> None:
        with pytest.raises(ParameterError) as excinfo:
            ThrowParams(
                release_x_mm=0.0,
                release_y_mm=0.0,
                release_z_mm=0.0,
                speed_mm_s=1.0,
                elevation_deg=45.0,
                azimuth_deg=0.0,
                gravity_mm_s2=-9806.65,
            )
        assert "gravity_mm_s2" in str(excinfo.value)

    def test_non_positive_object_diameter_rejected(self) -> None:
        with pytest.raises(ParameterError) as excinfo:
            ThrowParams(
                release_x_mm=0.0,
                release_y_mm=0.0,
                release_z_mm=0.0,
                speed_mm_s=1.0,
                elevation_deg=45.0,
                azimuth_deg=0.0,
                object_diameter_mm=0.0,
            )
        assert "object_diameter_mm" in str(excinfo.value)

    @pytest.mark.parametrize("bad_elevation", [90.1, -90.1, math.inf, math.nan])
    def test_elevation_out_of_range_rejected(self, bad_elevation: float) -> None:
        with pytest.raises(ParameterError) as excinfo:
            ThrowParams(
                release_x_mm=0.0,
                release_y_mm=0.0,
                release_z_mm=0.0,
                speed_mm_s=1.0,
                elevation_deg=bad_elevation,
                azimuth_deg=0.0,
            )
        assert "elevation_deg" in str(excinfo.value)

    def test_elevation_boundary_values_accepted(self) -> None:
        for elevation in (-90.0, 90.0):
            throw = ThrowParams(
                release_x_mm=0.0,
                release_y_mm=0.0,
                release_z_mm=0.0,
                speed_mm_s=1.0,
                elevation_deg=elevation,
                azimuth_deg=0.0,
            )
            assert throw.elevation_deg == elevation

    def test_non_finite_release_position_rejected(self) -> None:
        with pytest.raises(ParameterError) as excinfo:
            ThrowParams(
                release_x_mm=math.nan,
                release_y_mm=0.0,
                release_z_mm=0.0,
                speed_mm_s=1.0,
                elevation_deg=45.0,
                azimuth_deg=0.0,
            )
        assert "release_x_mm" in str(excinfo.value)


class TestCatchCriteriaValidation:
    def test_negative_position_tolerance_rejected(self) -> None:
        with pytest.raises(ParameterError) as excinfo:
            CatchCriteria(position_tolerance_mm=-1.0)
        assert "position_tolerance_mm" in str(excinfo.value)

    def test_negative_residual_speed_tolerance_rejected(self) -> None:
        with pytest.raises(ParameterError) as excinfo:
            CatchCriteria(residual_speed_tolerance_mm_s=-1.0)
        assert "residual_speed_tolerance_mm_s" in str(excinfo.value)

    def test_zero_tolerances_are_accepted(self) -> None:
        catch = CatchCriteria(position_tolerance_mm=0.0, residual_speed_tolerance_mm_s=0.0)
        assert catch.position_tolerance_mm == 0.0
        assert catch.residual_speed_tolerance_mm_s == 0.0


class TestLayoutParamsValidation:
    def test_non_finite_home_position_rejected(self) -> None:
        with pytest.raises(ParameterError) as excinfo:
            LayoutParams(home_x_mm=math.inf, home_y_mm=0.0)
        assert "home_x_mm" in str(excinfo.value)

    def test_negative_home_position_is_allowed(self) -> None:
        layout = LayoutParams(home_x_mm=-100.0, home_y_mm=-200.0)
        assert layout.home_x_mm == -100.0
        assert layout.home_y_mm == -200.0


# ---------------------------------------------------------------------------
# ScenarioParams: 上流予測設定をそのまま保持する
# ---------------------------------------------------------------------------


class TestScenarioParamsPredictionReuse:
    def test_prediction_field_holds_prediction_core_config_as_is(self) -> None:
        config = PredictionConfig(min_samples=5)
        params = dataclasses.replace(_valid_scenario_params(), prediction=config)
        assert params.prediction is config
        assert isinstance(params.prediction, PredictionConfig)


# ---------------------------------------------------------------------------
# make_sample: prediction_core.Sample を構築する
# ---------------------------------------------------------------------------


class TestMakeSample:
    def test_make_sample_returns_prediction_core_sample(self) -> None:
        sample = make_sample(t_ms=10.0, x_mm=1.0, y_mm=2.0, z_mm=3.0)
        assert isinstance(sample, Sample)
        assert sample.t_ms == 10.0
        assert sample.x_mm == 1.0
        assert sample.y_mm == 2.0
        assert sample.z_mm == 3.0


# ---------------------------------------------------------------------------
# Provenance の値そのものの確認
# ---------------------------------------------------------------------------


class TestProvenanceEnum:
    def test_provenance_values(self) -> None:
        assert Provenance.MEASURED == "measured"
        assert Provenance.ASSUMED == "assumed"


# ---------------------------------------------------------------------------
# タスク1.5: DrivetrainParams.from_wheel によるホイール径からの最高速度導出
# （要件4.2）
# ---------------------------------------------------------------------------


class TestDrivetrainFromWheel:
    def _from_wheel(self, wheel_diameter_mm: float) -> DrivetrainParams:
        return DrivetrainParams.from_wheel(
            wheel_diameter_mm=wheel_diameter_mm,
            motor_rpm=530.0,
            speed_efficiency=1.0,
            max_accel_mm_s2=2000.0,
            max_decel_mm_s2=2500.0,
            control_period_ms=10.0,
            command_latency_ms=20.0,
        )

    def test_from_wheel_matches_drivetrain_spec_theoretical_speed(self) -> None:
        # docs/drivetrain-spec.md §3.1: 60mm・無負荷530RPM の理論周速は
        # 約1.66 m/s = 1660 mm/s 前後と引用されている。
        drivetrain = self._from_wheel(wheel_diameter_mm=60.0)
        assert drivetrain.max_speed_mm_s == pytest.approx(1665.9, abs=1.0)
        assert drivetrain.max_speed_mm_s / 1000.0 == pytest.approx(1.66, abs=0.01)

    def test_from_wheel_retains_values_used_for_derivation(self) -> None:
        drivetrain = self._from_wheel(wheel_diameter_mm=60.0)
        assert drivetrain.wheel_diameter_mm == 60.0
        assert drivetrain.motor_rpm == 530.0
        assert drivetrain.speed_efficiency == 1.0

    def test_from_wheel_different_wheel_diameters_yield_different_speeds(self) -> None:
        drivetrain_60mm = self._from_wheel(wheel_diameter_mm=60.0)
        drivetrain_48mm = self._from_wheel(wheel_diameter_mm=48.0)
        assert drivetrain_60mm.max_speed_mm_s != drivetrain_48mm.max_speed_mm_s
        assert drivetrain_48mm.max_speed_mm_s == pytest.approx(
            drivetrain_60mm.max_speed_mm_s * (48.0 / 60.0)
        )

    def test_from_wheel_goes_through_post_init_validation(self) -> None:
        with pytest.raises(ParameterError) as excinfo:
            DrivetrainParams.from_wheel(
                wheel_diameter_mm=60.0,
                motor_rpm=530.0,
                speed_efficiency=1.0,
                max_accel_mm_s2=2000.0,
                max_decel_mm_s2=2500.0,
                control_period_ms=-1.0,
                command_latency_ms=20.0,
            )
        assert "control_period_ms" in str(excinfo.value)

    def test_from_wheel_returns_real_drivetrain_params_instance(self) -> None:
        drivetrain = self._from_wheel(wheel_diameter_mm=60.0)
        assert isinstance(drivetrain, DrivetrainParams)
        assert drivetrain.max_accel_mm_s2 == 2000.0
        assert drivetrain.max_decel_mm_s2 == 2500.0
        assert drivetrain.control_period_ms == 10.0
        assert drivetrain.command_latency_ms == 20.0


# ---------------------------------------------------------------------------
# タスク1.5: PARAMETER_PATHS -- パラメータ木を走査して生成するパス表
# （要件6.1, 9.4）
# ---------------------------------------------------------------------------


class TestParameterPaths:
    @pytest.mark.parametrize(
        "path",
        [
            "throw.speed_mm_s",
            "throw.release_x_mm",
            "throw.elevation_deg",
            "throw.gravity_mm_s2",
            "dispersion.speed_sigma_mm_s",
            "observation.dropout_ratio",
            "observation.detection_start_delay_ms",
            "observation.distance_sigma_rel_per_m2",
            "drivetrain.max_speed_mm_s",
            "drivetrain.motor_rpm",
            "catch.policy",
            "catch.position_tolerance_mm",
            "layout.home_x_mm",
            "calibration_stage",
        ],
    )
    def test_expected_paths_are_present(self, path: str) -> None:
        assert path in PARAMETER_PATHS
        entry = PARAMETER_PATHS[path]
        assert isinstance(entry, ParameterPath)
        assert entry.path == path

    def test_prediction_field_is_excluded(self) -> None:
        assert not any(p.startswith("prediction.") for p in PARAMETER_PATHS)
        assert "prediction" not in PARAMETER_PATHS

    def test_provenance_field_is_excluded(self) -> None:
        assert not any(p.startswith("provenance.") for p in PARAMETER_PATHS)
        assert "provenance" not in PARAMETER_PATHS

    @pytest.mark.parametrize(
        ("path", "expected_unit"),
        [
            ("throw.release_x_mm", "mm"),
            ("throw.speed_mm_s", "mm/s"),
            ("throw.elevation_deg", "deg"),
            ("throw.gravity_mm_s2", "mm/s^2"),
            ("observation.detection_start_delay_ms", "ms"),
            ("observation.dropout_ratio", ""),
            ("observation.distance_sigma_rel_per_m2", "1/m^2"),
            ("drivetrain.max_speed_mm_s", "mm/s"),
            ("drivetrain.max_accel_mm_s2", "mm/s^2"),
            ("drivetrain.motor_rpm", "rpm"),
            ("catch.policy", ""),
            ("calibration_stage", ""),
        ],
    )
    def test_unit_is_derived_from_field_name_suffix(
        self, path: str, expected_unit: str
    ) -> None:
        assert PARAMETER_PATHS[path].unit == expected_unit

    def test_component_and_field_name_are_recorded_for_nested_path(self) -> None:
        entry = PARAMETER_PATHS["throw.speed_mm_s"]
        assert entry.component == "throw"
        assert entry.field_name == "speed_mm_s"

    def test_component_is_none_for_top_level_path(self) -> None:
        entry = PARAMETER_PATHS["calibration_stage"]
        assert entry.component is None
        assert entry.field_name == "calibration_stage"

    def test_parameter_paths_is_immutable_mapping(self) -> None:
        with pytest.raises(TypeError):
            PARAMETER_PATHS["throw.speed_mm_s"] = PARAMETER_PATHS["throw.speed_mm_s"]  # type: ignore[index]


# ---------------------------------------------------------------------------
# タスク1.5: replace_by_path -- パス指定によるパラメータ置換
# ---------------------------------------------------------------------------


class TestReplaceByPath:
    def test_replace_numeric_nested_path(self) -> None:
        params = _valid_scenario_params()
        updated = replace_by_path(params, "throw.speed_mm_s", 4000.0)
        assert updated.throw.speed_mm_s == 4000.0
        # 他のフィールドは変化しない
        assert updated.throw.release_x_mm == params.throw.release_x_mm
        assert updated.observation is params.observation
        # 元のインスタンスは変更されない（不変）
        assert params.throw.speed_mm_s == 3000.0

    def test_replace_numeric_top_level_scalar_like_path_round_trips(self) -> None:
        params = _valid_scenario_params()
        updated = replace_by_path(params, "drivetrain.motor_rpm", 3000.0)
        assert updated.drivetrain.motor_rpm == 3000.0

    def test_replace_catch_policy_enum_path_with_string_value(self) -> None:
        params = _valid_scenario_params()
        updated = replace_by_path(params, "catch.policy", "pass_through")
        assert updated.catch.policy is CatchPolicy.PASS_THROUGH

    def test_replace_calibration_stage_enum_path_with_string_value(self) -> None:
        params = _valid_scenario_params()
        updated = replace_by_path(params, "calibration_stage", "m1_calibrated")
        assert updated.calibration_stage is CalibrationStage.M1_CALIBRATED

    def test_replace_by_path_returns_new_scenario_params_instance(self) -> None:
        params = _valid_scenario_params()
        updated = replace_by_path(params, "throw.speed_mm_s", 4000.0)
        assert isinstance(updated, ScenarioParams)
        assert updated is not params

    def test_unknown_dotted_path_raises_sweep_definition_error(self) -> None:
        params = _valid_scenario_params()
        with pytest.raises(SweepDefinitionError) as excinfo:
            replace_by_path(params, "throw.nonexistent_field", 1.0)
        assert "throw.nonexistent_field" in str(excinfo.value)

    def test_unknown_bogus_path_raises_sweep_definition_error(self) -> None:
        params = _valid_scenario_params()
        with pytest.raises(SweepDefinitionError) as excinfo:
            replace_by_path(params, "totally.bogus", 1.0)
        assert "totally.bogus" in str(excinfo.value)

    def test_replace_by_path_still_enforces_field_validation(self) -> None:
        params = _valid_scenario_params()
        with pytest.raises(ParameterError):
            replace_by_path(params, "throw.speed_mm_s", -1.0)


# ---------------------------------------------------------------------------
# タスク1.5: ScenarioParams.provenance のキー検証（要件9.4）
# ---------------------------------------------------------------------------


class TestProvenanceKeyValidation:
    def test_valid_provenance_keys_are_accepted(self) -> None:
        params = dataclasses.replace(
            _valid_scenario_params(),
            provenance={
                "throw.speed_mm_s": Provenance.MEASURED,
                "drivetrain.max_speed_mm_s": Provenance.ASSUMED,
                "calibration_stage": Provenance.ASSUMED,
            },
        )
        assert params.provenance["throw.speed_mm_s"] is Provenance.MEASURED
        assert params.provenance["drivetrain.max_speed_mm_s"] is Provenance.ASSUMED
        assert params.provenance["calibration_stage"] is Provenance.ASSUMED

    def test_invalid_provenance_key_is_rejected(self) -> None:
        with pytest.raises(ParameterError) as excinfo:
            dataclasses.replace(
                _valid_scenario_params(),
                provenance={"anything.not.validated": Provenance.ASSUMED},
            )
        assert "anything.not.validated" in str(excinfo.value)

    def test_invalid_provenance_key_rejected_on_direct_construction(self) -> None:
        with pytest.raises(ParameterError) as excinfo:
            ScenarioParams(
                throw=_valid_throw(),
                dispersion=ThrowDispersion(),
                observation=_valid_observation(),
                drivetrain=_valid_drivetrain(),
                catch=CatchCriteria(),
                layout=LayoutParams(home_x_mm=500.0, home_y_mm=500.0),
                prediction=PredictionConfig(),
                provenance={"bogus.path": Provenance.MEASURED},
            )
        assert "bogus.path" in str(excinfo.value)
