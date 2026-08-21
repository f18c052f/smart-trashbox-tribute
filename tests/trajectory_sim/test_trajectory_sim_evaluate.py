"""ScenarioEvaluator のテスト（要件5.3, 5.5, 6.2, 6.3, 10.2 /
design.md「ScenarioEvaluator」）。

`trajectory_sim.evaluate` が以下を満たすことを固定する:

- 誤差要因をすべてゼロにした条件で、`evaluate_throw` の成否判定が
  `evaluate_reachability` と一致すること（design.md Validation、本タスクの
  観測可能な完了状態）。到達可能・到達不可能の双方を、境界から十分な
  マージンを取った条件で確認する
- 評価対象外の3経路（`NO_FLOOR_CROSSING` / `NO_SAMPLES` /
  `NO_VALID_PREDICTION`）がそれぞれ正しい理由で区別され、不成立と
  混ざらないこと（要件6.7）
- `keep_record` が真のときのみ `ThrowRecord` を保持すること
- 停止方針では残留速度が判定に使われ、通過方針では記録のみで判定に
  使われないこと（要件5.5）
- `required_distance_mm` / `hold_time_ms` の算出式が、低水準の関数
  （`physics` / `observation` / `prediction_link` / `drivetrain`）を
  直接呼び出した独立計算と一致すること（要件10.2）
- `evaluate_reachability` が乱数を使わず決定的であること

ファイル名は `test_trajectory_sim_evaluate.py`
（`tests/trajectory_sim/test_trajectory_sim_*.py` の命名方針を踏襲する）。
"""

from __future__ import annotations

import math
from random import Random

import pytest

from trajectory_sim import drivetrain, evaluate, observation, physics, prediction_link, units
from trajectory_sim.params import (
    CatchCriteria,
    CatchPolicy,
    DrivetrainParams,
    LayoutParams,
    ObservationParams,
    ScenarioParams,
    ThrowDispersion,
    ThrowParams,
)
from trajectory_sim.results import NotEvaluatedReason

try:
    from prediction_core import PredictionConfig
except ImportError:  # pragma: no cover - prediction_core は依存として常に存在する想定
    PredictionConfig = None  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# 共通フィクスチャ
# ---------------------------------------------------------------------------


def _throw(**overrides: float) -> ThrowParams:
    """既定では release_z_mm=2000, speed_mm_s=4000, elevation=45, azimuth=0
    (x軸方向のみに飛ぶ、y=0) の投擲条件を返す。
    """
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
    """誤差要因ゼロ（sigma/dropout はすべて既定値0）の観測条件を返す。"""
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
    prediction: "PredictionConfig | None" = None,
) -> ScenarioParams:
    assert PredictionConfig is not None
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
    )


# ---------------------------------------------------------------------------
# 完了条件1: 誤差ゼロ条件で evaluate_throw と evaluate_reachability の成否が
# 一致する（到達可能・到達不可能それぞれ十分なマージンを持たせる）
# ---------------------------------------------------------------------------


def test_evaluate_throw_and_reachability_agree_when_solidly_reachable_stop_and_wait() -> None:
    """十分な性能（余裕を持って到達できる機体）を与えたとき、全経路評価と
    到達可否評価の成否が一致する（停止方針、本タスクの観測可能な完了条件）。
    """
    params = _scenario_params(
        drivetrain_params=_drivetrain(
            max_speed_mm_s=6000.0, max_accel_mm_s2=30000.0, max_decel_mm_s2=30000.0
        ),
        catch=CatchCriteria(policy=CatchPolicy.STOP_AND_WAIT),
    )

    outcome = evaluate.evaluate_throw(params, Random(0), "solid-reachable", keep_record=False)

    assert outcome.not_evaluated_reason is None
    assert outcome.catchable is True  # 十分な性能マージンがあるため成立するはず

    reachability_outcome = evaluate.evaluate_reachability(
        outcome.hold_time_ms, outcome.required_distance_mm, params
    )

    assert reachability_outcome.catchable == outcome.catchable


def test_evaluate_throw_and_reachability_agree_when_solidly_unreachable_stop_and_wait() -> None:
    """性能が明らかに不足する機体を与えたとき、全経路評価と到達可否評価の
    成否がともに不成立で一致する（停止方針）。
    """
    params = _scenario_params(
        drivetrain_params=_drivetrain(
            max_speed_mm_s=500.0, max_accel_mm_s2=500.0, max_decel_mm_s2=500.0
        ),
        catch=CatchCriteria(policy=CatchPolicy.STOP_AND_WAIT),
    )

    outcome = evaluate.evaluate_throw(params, Random(0), "solid-unreachable", keep_record=False)

    assert outcome.not_evaluated_reason is None
    assert outcome.catchable is False  # 性能不足のため不成立のはず

    reachability_outcome = evaluate.evaluate_reachability(
        outcome.hold_time_ms, outcome.required_distance_mm, params
    )

    assert reachability_outcome.catchable == outcome.catchable


# 通過方針(PASS_THROUGH)については、`evaluate_reachability` が判定に
# 用いる `max_distance_from_rest` は「持ち時間内にその距離まで到達
# **できる**能力があるか」だけを表す閉形式であり、`evaluate_throw` が
# 実際に走らせる `drivetrain.simulate` は行き過ぎを抑える制御を持たない
# （design.md「DrivetrainModel」Implementation Notes / Risks）。その
# ため、性能に余裕がある(＝持ち時間に対し到達可能距離が必要移動量を
# 大きく上回る)ほど、実際には目標を通り過ぎて大きく離れてしまい、
# 「到達できる」ことと「その時刻にちょうどそこにいる」ことが一致しなく
# なる。したがって PASS_THROUGH については、本タスクの必須完了条件
# （誤差ゼロ条件での両評価器の一致）の対象を STOP_AND_WAIT に限定する
# （タスク指示: 「STOP_AND_WAIT is probably the more interesting case
# since PASS_THROUGH…is a much looser criterion」）。上の2つの
# STOP_AND_WAIT テスト(到達可能・到達不可能、いずれも十分なマージン付き)
# が本タスクの必須完了条件を満たす。


# ---------------------------------------------------------------------------
# 完了条件2: 評価対象外の3経路
# ---------------------------------------------------------------------------


def test_evaluate_throw_returns_no_floor_crossing_when_thrown_straight_down_from_floor() -> None:
    """床面 (z=0) から真下へ投げた場合、未来側の床面交点が存在しない
    （`physics.solve_true_impact` が `None` を返す）ため、
    `NO_FLOOR_CROSSING` として評価対象外になる。
    """
    throw = _throw(release_z_mm=0.0, elevation_deg=-90.0, speed_mm_s=1000.0)
    trajectory = physics.sample_throw(throw, ThrowDispersion(), Random(0))
    assert physics.solve_true_impact(trajectory) is None  # テスト前提の確認

    params = _scenario_params(throw=throw)

    outcome = evaluate.evaluate_throw(params, Random(0), "no-floor-crossing", keep_record=True)

    assert outcome.catchable is None
    assert outcome.not_evaluated_reason is NotEvaluatedReason.NO_FLOOR_CROSSING
    assert outcome.true_impact_x_mm is None
    assert outcome.true_impact_y_mm is None
    assert outcome.true_impact_time_ms is None
    assert outcome.required_distance_mm is None
    assert outcome.hold_time_ms is None
    assert outcome.first_command_time_ms is None
    assert outcome.position_error_mm is None
    assert outcome.residual_speed_mm_s is None
    assert outcome.prediction_error_mm is None
    assert outcome.sample_count == 0
    assert outcome.valid_prediction_count == 0
    assert outcome.record is None  # 評価対象外の経路では常に None


def test_evaluate_throw_returns_no_samples_when_detection_delay_exceeds_flight_time() -> None:
    """検出開始遅れが真の落下時刻を超える場合、観測サンプルが1件も
    生成されず `NO_SAMPLES` として評価対象外になる。
    """
    throw = _throw()
    observation_params = _observation_params(detection_start_delay_ms=100_000.0)
    params = _scenario_params(throw=throw, observation_params=observation_params)

    # テスト前提の確認: 実際に observe がゼロ件を返すこと。
    trajectory = physics.sample_throw(throw, ThrowDispersion(), Random(0))
    impact = physics.solve_true_impact(trajectory)
    assert impact is not None
    samples = observation.observe(trajectory, impact, observation_params, Random(0))
    assert samples == ()

    outcome = evaluate.evaluate_throw(params, Random(0), "no-samples", keep_record=True)

    assert outcome.catchable is None
    assert outcome.not_evaluated_reason is NotEvaluatedReason.NO_SAMPLES
    assert outcome.true_impact_x_mm == pytest.approx(impact.x_mm)
    assert outcome.true_impact_y_mm == pytest.approx(impact.y_mm)
    assert outcome.true_impact_time_ms == pytest.approx(impact.time_ms)
    assert outcome.required_distance_mm is not None
    assert outcome.hold_time_ms is None
    assert outcome.first_command_time_ms is None
    assert outcome.position_error_mm is None
    assert outcome.residual_speed_mm_s is None
    assert outcome.prediction_error_mm is None
    assert outcome.sample_count == 0
    assert outcome.valid_prediction_count == 0
    assert outcome.record is None  # 評価対象外の経路では常に None


def test_evaluate_throw_returns_no_valid_prediction_when_fewer_than_min_samples() -> None:
    """実際に得られる観測サンプル数が `PredictionConfig.min_samples`
    未満の場合、有効な予測が1件も得られず `NO_VALID_PREDICTION` として
    評価対象外になる。
    """
    throw = _throw()
    # sample_period_ms を粗くし、真の落下時刻までに2点しか標本化されない
    # ようにする（min_samples の既定値3未満）。
    observation_params = _observation_params(
        detection_start_delay_ms=100.0, sample_period_ms=500.0
    )
    params = _scenario_params(throw=throw, observation_params=observation_params)

    trajectory = physics.sample_throw(throw, ThrowDispersion(), Random(0))
    impact = physics.solve_true_impact(trajectory)
    assert impact is not None
    samples = observation.observe(trajectory, impact, observation_params, Random(0))
    assert 0 < len(samples) < params.prediction.min_samples  # テスト前提の確認

    outcome = evaluate.evaluate_throw(params, Random(0), "no-valid-prediction", keep_record=True)

    assert outcome.catchable is None
    assert outcome.not_evaluated_reason is NotEvaluatedReason.NO_VALID_PREDICTION
    assert outcome.true_impact_x_mm == pytest.approx(impact.x_mm)
    assert outcome.true_impact_y_mm == pytest.approx(impact.y_mm)
    assert outcome.true_impact_time_ms == pytest.approx(impact.time_ms)
    assert outcome.required_distance_mm is not None
    assert outcome.hold_time_ms is None
    assert outcome.first_command_time_ms is None
    assert outcome.position_error_mm is None
    assert outcome.residual_speed_mm_s is None
    assert outcome.prediction_error_mm is None
    assert outcome.sample_count == len(samples)
    assert outcome.valid_prediction_count == 0
    # NO_VALID_PREDICTION は record を保持しうる経路である
    # (keep_record=True かつ record が None でないこと)。
    assert outcome.record is not None


# ---------------------------------------------------------------------------
# 完了条件3: keep_record の挙動
# ---------------------------------------------------------------------------


def test_evaluate_throw_keeps_record_only_when_keep_record_is_true() -> None:
    """評価済み（評価対象外でない）シナリオで、`keep_record` の値に応じて
    `record` が保持されるかどうかが切り替わること。
    """
    params = _scenario_params()

    with_record = evaluate.evaluate_throw(params, Random(0), "kept", keep_record=True)
    without_record = evaluate.evaluate_throw(params, Random(0), "not-kept", keep_record=False)

    assert with_record.not_evaluated_reason is None  # テスト前提: 評価対象外ではない
    assert with_record.record is not None
    assert without_record.record is None


# ---------------------------------------------------------------------------
# 完了条件4: 方針ごとのキャッチ判定ロジック（残留速度の扱いの違い）
# ---------------------------------------------------------------------------


def test_stop_and_wait_rejects_high_residual_speed_while_pass_through_ignores_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """位置誤差が許容範囲内でも、残留速度が許容値を超える場合、停止方針は
    不成立、通過方針は成立と判定する（要件5.5: 通過方針は残留速度を記録
    するが判定に使わない）。

    `evaluate_throw` の物理・観測・予測の各段はそのまま実行しつつ、
    `drivetrain.simulate` の戻り値だけを既知の手計算値
    （位置誤差=30.625mm、許容誤差67.5mm以内。残留速度=350mm/s、
    許容値200mm/s超過。いずれも境界から十分なマージンを持つ）に固定する
    （`monkeypatch`）。これにより `evaluate_throw` 自身の
    `position_ok`/`speed_ok`/方針別の組み合わせロジックを、離散化誤差や
    予測タイミングの微妙な数値合わせに依存せず直接検証できる。
    """
    throw = _throw()
    observation_params = _observation_params()
    trajectory = physics.sample_throw(throw, ThrowDispersion(), Random(0))
    impact = physics.solve_true_impact(trajectory)
    assert impact is not None  # テスト前提の確認

    position_error_mm = 30.625  # 許容誤差67.5mm以内(マージンあり)
    residual_speed_mm_s = 350.0  # 許容値200.0mm/sを超過(マージンあり)
    residual_speed_mm_ms = units.mm_per_s_to_mm_per_ms(residual_speed_mm_s)

    def fake_simulate(
        *,
        start_x_mm: float,
        start_y_mm: float,
        updates: object,
        params: object,
        policy: object,
        end_time_ms: float,
    ) -> drivetrain.MotionState:
        return drivetrain.MotionState(
            time_ms=end_time_ms,
            x_mm=impact.x_mm + position_error_mm,
            y_mm=impact.y_mm,
            vx_mm_ms=residual_speed_mm_ms,
            vy_mm_ms=0.0,
        )

    monkeypatch.setattr(evaluate.drivetrain, "simulate", fake_simulate)

    params_stop_and_wait = _scenario_params(
        throw=throw,
        observation_params=observation_params,
        catch=CatchCriteria(policy=CatchPolicy.STOP_AND_WAIT),
    )
    params_pass_through = _scenario_params(
        throw=throw,
        observation_params=observation_params,
        catch=CatchCriteria(policy=CatchPolicy.PASS_THROUGH),
    )

    outcome_stop_and_wait = evaluate.evaluate_throw(
        params_stop_and_wait, Random(0), "policy-stop-and-wait", keep_record=False
    )
    outcome_pass_through = evaluate.evaluate_throw(
        params_pass_through, Random(0), "policy-pass-through", keep_record=False
    )

    assert outcome_stop_and_wait.not_evaluated_reason is None  # テスト前提の確認
    assert outcome_pass_through.not_evaluated_reason is None

    assert outcome_stop_and_wait.position_error_mm == pytest.approx(position_error_mm)
    assert outcome_stop_and_wait.residual_speed_mm_s == pytest.approx(residual_speed_mm_s)
    assert outcome_stop_and_wait.catchable is False

    assert outcome_pass_through.position_error_mm == pytest.approx(position_error_mm)
    assert outcome_pass_through.residual_speed_mm_s == pytest.approx(residual_speed_mm_s)
    assert outcome_pass_through.catchable is True


# ---------------------------------------------------------------------------
# 完了条件5: required_distance_mm / hold_time_ms の算出式を、低水準の
# 関数を直接呼び出した独立計算と突き合わせる（要件10.2）
# ---------------------------------------------------------------------------


def test_required_distance_and_hold_time_match_independent_low_level_computation() -> None:
    """`evaluate_throw` が返す `required_distance_mm` / `hold_time_ms` が、
    `physics` / `observation` / `prediction_link` / `drivetrain` を直接
    呼び出して独立に計算した値と一致すること。

    `required_distance_mm` は「待機位置と真の落下地点の水平距離」
    （要件10.2）、`hold_time_ms` は「真の落下時刻 − 最初の目標更新が
    指令へ反映された時刻」（design.md「ScenarioEvaluator」
    Responsibilities）として、`evaluate_throw` を経由せず個別に計算する。
    """
    throw = _throw()
    observation_params = _observation_params()
    drivetrain_params = _drivetrain()
    layout = LayoutParams(home_x_mm=100.0, home_y_mm=-50.0)
    params = _scenario_params(
        throw=throw,
        observation_params=observation_params,
        drivetrain_params=drivetrain_params,
        layout=layout,
    )

    # --- 独立計算(evaluate_throw の内部と同じ低水準関数を直接呼ぶ) ---
    trajectory = physics.sample_throw(throw, ThrowDispersion(), Random(0))
    impact = physics.solve_true_impact(trajectory)
    assert impact is not None
    expected_required_distance_mm = math.sqrt(
        (impact.x_mm - layout.home_x_mm) ** 2 + (impact.y_mm - layout.home_y_mm) ** 2
    )

    samples = observation.observe(trajectory, impact, observation_params, Random(0))
    timeline = prediction_link.run_prediction(
        samples, observation_params, params.prediction, "record-id", {}
    )
    assert timeline.updates  # テスト前提: 少なくとも1件の有効な目標更新がある

    expected_first_command_time_ms = drivetrain.align_to_control_tick_ms(
        timeline.updates[0].available_time_ms + drivetrain_params.command_latency_ms,
        drivetrain_params.control_period_ms,
    )
    expected_hold_time_ms = impact.time_ms - expected_first_command_time_ms

    # --- evaluate_throw を通した実際の値 ---
    outcome = evaluate.evaluate_throw(params, Random(0), "hand-verify", keep_record=False)

    assert outcome.not_evaluated_reason is None
    assert outcome.required_distance_mm == pytest.approx(expected_required_distance_mm)
    assert outcome.first_command_time_ms == pytest.approx(expected_first_command_time_ms)
    assert outcome.hold_time_ms == pytest.approx(expected_hold_time_ms)


# ---------------------------------------------------------------------------
# 完了条件6: evaluate_reachability の決定性(乱数を使わない)
# ---------------------------------------------------------------------------


def test_evaluate_reachability_is_deterministic_across_repeated_calls() -> None:
    """同一引数で2回呼び出しても、`evaluate_reachability` はビット単位で
    同一の結果を返す（design.md「ScenarioEvaluator」Invariants: 乱数を
    使わず、同一入力に対して常に同一結果を返す）。`rng` 引数を持たない
    シグネチャそのものが、乱数源を持たないことの直接的な証拠でもある。
    """
    params = _scenario_params(catch=CatchCriteria(policy=CatchPolicy.STOP_AND_WAIT))
    hold_time_ms = 500.0
    required_distance_mm = 200.0

    first = evaluate.evaluate_reachability(hold_time_ms, required_distance_mm, params)
    second = evaluate.evaluate_reachability(hold_time_ms, required_distance_mm, params)

    assert first == second
    assert first.catchable is not None
    assert first.not_evaluated_reason is None
    assert first.required_distance_mm == required_distance_mm
    assert first.hold_time_ms == hold_time_ms
