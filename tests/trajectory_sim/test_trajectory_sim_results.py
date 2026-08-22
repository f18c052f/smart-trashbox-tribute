"""`trajectory_sim.results` の型と不変条件を固定するテスト
（task 1.6 / design.md「Results」/ 要件 1.5, 2.8, 4.6, 5.7, 6.5, 6.7, 9.7）。

命名は `tests/trajectory_sim/` の既存規約（`test_trajectory_sim_*.py`）に
合わせ、`tests/prediction_core/` とのベース名衝突（pytest の prepend
インポートモード由来）を避ける。
"""

from __future__ import annotations

import dataclasses

import pytest
from prediction_core import PredictionConfig, SourceKind, ThrowRecord

from trajectory_sim.errors import ParameterError
from trajectory_sim.params import (
    CatchCriteria,
    DrivetrainParams,
    LayoutParams,
    ObservationParams,
    ScenarioParams,
    ThrowDispersion,
    ThrowParams,
)
from trajectory_sim.results import (
    MODEL_EXCLUSIONS,
    CellResult,
    CellStatus,
    NotEvaluatedReason,
    ScenarioOutcome,
    SweepResult,
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


def _evaluated_outcome(*, catchable: bool = True) -> ScenarioOutcome:
    return ScenarioOutcome(
        catchable=catchable,
        not_evaluated_reason=None,
        true_impact_x_mm=100.0,
        true_impact_y_mm=200.0,
        true_impact_time_ms=500.0,
        required_distance_mm=30.0,
        hold_time_ms=450.0,
        first_command_time_ms=80.0,
        position_error_mm=20.0,
        residual_speed_mm_s=50.0,
        prediction_error_mm=10.0,
        sample_count=8,
        valid_prediction_count=6,
        record=None,
    )


def _not_evaluated_outcome(
    reason: NotEvaluatedReason = NotEvaluatedReason.NO_SAMPLES,
) -> ScenarioOutcome:
    return ScenarioOutcome(
        catchable=None,
        not_evaluated_reason=reason,
        true_impact_x_mm=None,
        true_impact_y_mm=None,
        true_impact_time_ms=None,
        required_distance_mm=None,
        hold_time_ms=None,
        first_command_time_ms=None,
        position_error_mm=None,
        residual_speed_mm_s=None,
        prediction_error_mm=None,
        sample_count=0,
        valid_prediction_count=0,
        record=None,
    )


def _make_throw_record() -> ThrowRecord:
    return ThrowRecord(
        record_id="scenario-1",
        source=SourceKind.SIMULATED,
        config=PredictionConfig(),
        samples=(),
        predictions=(),
    )


# ---------------------------------------------------------------------------
# MODEL_EXCLUSIONS: OQ-33 の決着内容そのものを固定する
# ---------------------------------------------------------------------------


class TestModelExclusions:
    def test_has_all_four_stages(self) -> None:
        assert set(MODEL_EXCLUSIONS.keys()) == {
            "throw_physics",
            "observation",
            "drivetrain",
            "catch",
        }

    def test_throw_physics_exclusions_are_exact(self) -> None:
        assert MODEL_EXCLUSIONS["throw_physics"] == ("air_drag", "spin", "bounce")

    def test_observation_exclusions_are_exact(self) -> None:
        assert MODEL_EXCLUSIONS["observation"] == (
            "sensor_distortion",
            "field_of_view",
            "occlusion",
            "timestamp_jitter",
        )

    def test_drivetrain_exclusions_are_exact(self) -> None:
        assert MODEL_EXCLUSIONS["drivetrain"] == (
            "wheel_slip",
            "direction_dependent_performance",
            "inverse_kinematics",
            "speed_control_dynamics",
        )

    def test_catch_exclusions_are_exact(self) -> None:
        assert MODEL_EXCLUSIONS["catch"] == ("bounce_out",)

    def test_is_immutable_mapping(self) -> None:
        with pytest.raises(TypeError):
            MODEL_EXCLUSIONS["throw_physics"] = ()  # type: ignore[index]


# ---------------------------------------------------------------------------
# NotEvaluatedReason / CellStatus: 列挙値の固定
# ---------------------------------------------------------------------------


class TestEnums:
    def test_not_evaluated_reason_values(self) -> None:
        assert {member.value for member in NotEvaluatedReason} == {
            "no_floor_crossing",
            "no_samples",
            "no_valid_prediction",
        }

    def test_cell_status_values(self) -> None:
        assert {member.value for member in CellStatus} == {
            "catchable",
            "not_catchable",
            "not_evaluated",
        }


# ---------------------------------------------------------------------------
# ScenarioOutcome: 正常系
# ---------------------------------------------------------------------------


class TestScenarioOutcomeHappyPath:
    def test_constructs_when_evaluated_and_catchable(self) -> None:
        outcome = _evaluated_outcome(catchable=True)
        assert outcome.catchable is True
        assert outcome.not_evaluated_reason is None

    def test_constructs_when_evaluated_and_not_catchable(self) -> None:
        outcome = _evaluated_outcome(catchable=False)
        assert outcome.catchable is False
        assert outcome.not_evaluated_reason is None

    def test_constructs_when_not_evaluated(self) -> None:
        outcome = _not_evaluated_outcome(NotEvaluatedReason.NO_FLOOR_CROSSING)
        assert outcome.catchable is None
        assert outcome.not_evaluated_reason is NotEvaluatedReason.NO_FLOOR_CROSSING

    def test_is_frozen(self) -> None:
        outcome = _evaluated_outcome()
        with pytest.raises(dataclasses.FrozenInstanceError):
            outcome.catchable = False  # type: ignore[misc]

    def test_has_no_dict_slots_only(self) -> None:
        outcome = _evaluated_outcome()
        assert not hasattr(outcome, "__dict__")

    def test_holds_real_throw_record(self) -> None:
        record = _make_throw_record()
        outcome = dataclasses.replace(_evaluated_outcome(), record=record)
        assert outcome.record is record
        assert isinstance(outcome.record, ThrowRecord)


# ---------------------------------------------------------------------------
# ScenarioOutcome: 不変条件 -- 「成否が未定なら理由が必ず埋まる」
# ---------------------------------------------------------------------------


class TestScenarioOutcomeInvariant:
    def test_rejects_both_none(self) -> None:
        with pytest.raises(ParameterError):
            dataclasses.replace(
                _not_evaluated_outcome(), catchable=None, not_evaluated_reason=None
            )

    def test_rejects_both_set(self) -> None:
        with pytest.raises(ParameterError):
            dataclasses.replace(
                _evaluated_outcome(),
                catchable=True,
                not_evaluated_reason=NotEvaluatedReason.NO_SAMPLES,
            )

    def test_error_message_mentions_field_names(self) -> None:
        with pytest.raises(ParameterError, match="catchable"):
            dataclasses.replace(
                _not_evaluated_outcome(), catchable=None, not_evaluated_reason=None
            )


# ---------------------------------------------------------------------------
# CellResult: 正常系
# ---------------------------------------------------------------------------


class TestCellResultHappyPath:
    def _catchable_cell(self) -> CellResult:
        return CellResult(
            axis_values=(500.0, "stop_and_wait"),
            status=CellStatus.CATCHABLE,
            trials=4,
            evaluated_trials=4,
            success_count=4,
            success_ratio=1.0,
            metrics={"position_error_mm": 12.0},
            not_evaluated_reason=None,
            representative=_evaluated_outcome(catchable=True),
        )

    def test_constructs_when_catchable(self) -> None:
        cell = self._catchable_cell()
        assert cell.status is CellStatus.CATCHABLE
        assert cell.not_evaluated_reason is None

    def test_constructs_when_not_catchable(self) -> None:
        cell = dataclasses.replace(
            self._catchable_cell(),
            status=CellStatus.NOT_CATCHABLE,
            success_count=0,
            success_ratio=0.0,
            representative=_evaluated_outcome(catchable=False),
        )
        assert cell.status is CellStatus.NOT_CATCHABLE
        assert cell.not_evaluated_reason is None

    def test_constructs_when_not_evaluated(self) -> None:
        cell = CellResult(
            axis_values=(500.0, "stop_and_wait"),
            status=CellStatus.NOT_EVALUATED,
            trials=4,
            evaluated_trials=0,
            success_count=0,
            success_ratio=None,
            metrics={},
            not_evaluated_reason=NotEvaluatedReason.NO_SAMPLES,
            representative=_not_evaluated_outcome(),
        )
        assert cell.status is CellStatus.NOT_EVALUATED
        assert cell.not_evaluated_reason is NotEvaluatedReason.NO_SAMPLES
        assert cell.success_ratio is None

    def test_is_frozen(self) -> None:
        cell = self._catchable_cell()
        with pytest.raises(dataclasses.FrozenInstanceError):
            cell.status = CellStatus.NOT_CATCHABLE  # type: ignore[misc]

    def test_has_no_dict_slots_only(self) -> None:
        cell = self._catchable_cell()
        assert not hasattr(cell, "__dict__")


# ---------------------------------------------------------------------------
# CellResult: 不変条件 -- status と not_evaluated_reason の対応関係
# ---------------------------------------------------------------------------


class TestCellResultInvariant:
    def _catchable_cell(self) -> CellResult:
        return CellResult(
            axis_values=(500.0,),
            status=CellStatus.CATCHABLE,
            trials=4,
            evaluated_trials=4,
            success_count=4,
            success_ratio=1.0,
            metrics={},
            not_evaluated_reason=None,
            representative=None,
        )

    def test_rejects_not_evaluated_without_reason(self) -> None:
        with pytest.raises(ParameterError):
            dataclasses.replace(
                self._catchable_cell(),
                status=CellStatus.NOT_EVALUATED,
                not_evaluated_reason=None,
            )

    def test_rejects_catchable_status_with_reason(self) -> None:
        with pytest.raises(ParameterError):
            dataclasses.replace(
                self._catchable_cell(),
                not_evaluated_reason=NotEvaluatedReason.NO_SAMPLES,
            )

    def test_rejects_not_catchable_status_with_reason(self) -> None:
        with pytest.raises(ParameterError):
            dataclasses.replace(
                self._catchable_cell(),
                status=CellStatus.NOT_CATCHABLE,
                not_evaluated_reason=NotEvaluatedReason.NO_VALID_PREDICTION,
            )


# ---------------------------------------------------------------------------
# SweepResult: 単なる入れ物であり、独自の不変条件を持たない
# ---------------------------------------------------------------------------


class _DummySweepSpec:
    """`SweepSpec`（タスク3.2、未実装）の代わりとなる最小限のダミー。

    `results.py` は `SweepSpec` を実行時 import しない（design.md
    「Dependency Direction」で `results` は `sweep` より下位層のため）。
    `SweepResult.spec` の注釈は `from __future__ import annotations` に
    より遅延文字列化されているため、dataclass はこの値を実行時に検証
    しない。
    """


class TestSweepResult:
    def test_constructs_and_holds_fields(self) -> None:
        spec = _DummySweepSpec()
        params = _valid_scenario_params()
        cell = CellResult(
            axis_values=(1.0,),
            status=CellStatus.CATCHABLE,
            trials=1,
            evaluated_trials=1,
            success_count=1,
            success_ratio=1.0,
            metrics={},
            not_evaluated_reason=None,
            representative=None,
        )
        result = SweepResult(spec=spec, base_params=params, cells=(cell,))
        assert result.spec is spec
        assert result.base_params is params
        assert result.cells == (cell,)

    def test_is_frozen(self) -> None:
        result = SweepResult(
            spec=_DummySweepSpec(), base_params=_valid_scenario_params(), cells=()
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.cells = ()  # type: ignore[misc]

    def test_has_no_dict_slots_only(self) -> None:
        result = SweepResult(
            spec=_DummySweepSpec(), base_params=_valid_scenario_params(), cells=()
        )
        assert not hasattr(result, "__dict__")
