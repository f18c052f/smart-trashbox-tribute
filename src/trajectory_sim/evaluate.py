"""1シナリオの評価器（design.md「ScenarioEvaluator」/ 要件 5.3, 5.5, 6.2, 6.3,
10.2）。

全経路評価（`evaluate_throw`: 物理 → 観測 → 予測 → 運動 → キャッチ判定）と、
運動モデルのみによる到達可否評価（`evaluate_reachability`）の2つを提供する。
**両者は同じ `ScenarioOutcome` を返す**（design.md「ScenarioEvaluator」
Responsibilities & Constraints）。誤差要因をすべてゼロにした条件では、
両者の成否判定が一致することがテストで固定されている
（`tests/trajectory_sim/test_trajectory_sim_evaluate.py`）。

評価対象外は3種類の理由（`NotEvaluatedReason.NO_FLOOR_CROSSING` /
`NO_SAMPLES` / `NO_VALID_PREDICTION`）で区別し、不成立（`catchable=False`）
とは混ぜない（要件6.7、design.md フロー図「1シナリオ評価のフロー」）。

必要移動量は待機位置と真の落下地点の水平距離として（要件10.2）、持ち時間は
真の落下時刻と最初の指令反映時刻の差として算出する（design.md
「ScenarioEvaluator」Responsibilities: 「持ち時間 ＝ 真の落下時刻 − 最初の
目標更新が指令へ反映された時刻」）。最初の指令反映時刻は
`drivetrain.align_to_control_tick_ms` を用いて、`drivetrain.simulate` /
`_active_target` が実際に採用する反映時刻と一致させる（design.md「時間軸と
目標更新」フロー）。これにより、全経路評価の成否が到達可否評価の結果と
一致するという本タスクの完了条件が満たされる。

真の落下時刻における位置誤差と残留速度は常に算出し、`ScenarioOutcome` に
記録する。キャッチ成立の判定に残留速度を用いるかどうかは方針で分かれる
（要件5.5, design.md「ScenarioEvaluator」Responsibilities: 「停止方針では
残留速度 ≤ 許容速度、通過方針では残留速度を記録するが判定に使わない」）。

`keep_record` が偽のときは `ThrowRecord` を保持しない。代表シナリオの
みこれを真にする想定である（design.md「ScenarioEvaluator」Implementation
Notes: 「掃引全体でメモリを持ち続けないため」）。

design.md からの意図的な乖離（シグネチャ拡張）:
    design.md の Service Interface は `evaluate_throw(params, rng,
    record_id, keep_record)` という4引数のみを記載しているが、本関数は
    内部で `prediction_link.run_prediction(samples, observation, config,
    record_id, extra)` を呼び出す必要があり（タスク2.5で確定した契約。
    `run_prediction` は `extra` を不透明なマッピングとしてそのまま
    `ThrowRecord.extra` へ渡すだけで、`"sim"` 名前空間を自ら組み立てない）、
    その `extra` は `record_id` と同じ2値（`cell_index` / `trial_index`）
    から**呼び出し側**が組み立てるものである（design.md「PredictionLink」
    Implementation Notes: 「`record_id` を解析し直さずに引けるようにする」
    ため、`record_id` を構築した側が `extra` も直接組み立てる）。
    `evaluate_throw` は `record_id: str` という不透明な文字列しか受け取らず、
    そこから `cell_index` / `trial_index` を逆算する手段を持たない（design.md
    がまさにそれを禁じている）。したがって、`extra` を意味のある値として
    `run_prediction` へ渡す唯一のクリーンな方法は、`evaluate_throw` に
    5番目の引数 `extra: Mapping[str, object]` を追加し（既定値は空の
    不変マッピング）、これを `run_prediction` へそのまま素通しすることで
    ある（`evaluate_throw` 自身は `extra` の中身を検証・合成しない。
    タスク2.5の「不透明な素通し」の方針をそのまま踏襲する）。この
    シグネチャ拡張は、後続タスク3.2（SweepEngine）が `record_id` と
    `extra` を同じ `cell_index` / `trial_index` から組み立てて、この新しい
    引数経由で渡す必要があることを意味する。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from random import Random
from types import MappingProxyType

from trajectory_sim import drivetrain, observation, physics, prediction_link
from trajectory_sim.params import CatchPolicy, ScenarioParams
from trajectory_sim.results import NotEvaluatedReason, ScenarioOutcome

__all__ = ["evaluate_throw", "evaluate_reachability"]

_EMPTY_EXTRA: Mapping[str, object] = MappingProxyType({})


def _horizontal_distance_mm(x1_mm: float, y1_mm: float, x2_mm: float, y2_mm: float) -> float:
    """2点間の水平距離を算出する（要件5.3, 10.2）。"""
    return math.sqrt((x1_mm - x2_mm) ** 2 + (y1_mm - y2_mm) ** 2)


def evaluate_throw(
    params: ScenarioParams,
    rng: Random,
    record_id: str,
    keep_record: bool,
    extra: Mapping[str, object] = _EMPTY_EXTRA,
) -> ScenarioOutcome:
    """1回の投擲を、物理 → 観測 → 予測 → 運動 → キャッチ判定の全経路で評価する。

    （design.md「ScenarioEvaluator」Service Interface / フロー図
    「1シナリオ評価のフロー」/ 要件5.3, 5.5, 6.3, 10.2）

    `extra` はモジュール docstring に記した理由により追加した5番目の
    引数であり、design.md の4引数シグネチャからの意図的な乖離である。
    `run_prediction` へ不透明なまま素通しするだけで、本関数自身は中身を
    検証・加工しない。

    Preconditions:
        `params` は構築時検証を通過済みであること。

    Postconditions:
        `catchable is None` のとき `not_evaluated_reason` が必ず埋まる。
        逆も成立する（`ScenarioOutcome.__post_init__` が検証する）。
    """
    trajectory = physics.sample_throw(params.throw, params.dispersion, rng)
    impact = physics.solve_true_impact(trajectory)
    if impact is None:
        return ScenarioOutcome(
            catchable=None,
            not_evaluated_reason=NotEvaluatedReason.NO_FLOOR_CROSSING,
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

    samples = observation.observe(trajectory, impact, params.observation, rng)

    required_distance_mm = _horizontal_distance_mm(
        impact.x_mm, impact.y_mm, params.layout.home_x_mm, params.layout.home_y_mm
    )

    if len(samples) == 0:
        return ScenarioOutcome(
            catchable=None,
            not_evaluated_reason=NotEvaluatedReason.NO_SAMPLES,
            true_impact_x_mm=impact.x_mm,
            true_impact_y_mm=impact.y_mm,
            true_impact_time_ms=impact.time_ms,
            required_distance_mm=required_distance_mm,
            hold_time_ms=None,
            first_command_time_ms=None,
            position_error_mm=None,
            residual_speed_mm_s=None,
            prediction_error_mm=None,
            sample_count=0,
            valid_prediction_count=0,
            record=None,
        )

    timeline = prediction_link.run_prediction(
        samples, params.observation, params.prediction, record_id, extra
    )

    if timeline.valid_prediction_count == 0:
        return ScenarioOutcome(
            catchable=None,
            not_evaluated_reason=NotEvaluatedReason.NO_VALID_PREDICTION,
            true_impact_x_mm=impact.x_mm,
            true_impact_y_mm=impact.y_mm,
            true_impact_time_ms=impact.time_ms,
            required_distance_mm=required_distance_mm,
            hold_time_ms=None,
            first_command_time_ms=None,
            position_error_mm=None,
            residual_speed_mm_s=None,
            prediction_error_mm=None,
            sample_count=len(samples),
            valid_prediction_count=0,
            record=(timeline.record if keep_record else None),
        )

    first_command_time_ms = drivetrain.align_to_control_tick_ms(
        timeline.updates[0].available_time_ms + params.drivetrain.command_latency_ms,
        params.drivetrain.control_period_ms,
    )
    hold_time_ms = impact.time_ms - first_command_time_ms

    final_state = drivetrain.simulate(
        start_x_mm=params.layout.home_x_mm,
        start_y_mm=params.layout.home_y_mm,
        updates=timeline.updates,
        params=params.drivetrain,
        policy=params.catch.policy,
        end_time_ms=impact.time_ms,
    )

    position_error_mm = _horizontal_distance_mm(
        final_state.x_mm, final_state.y_mm, impact.x_mm, impact.y_mm
    )
    residual_speed_mm_s = final_state.speed_mm_s

    assert timeline.final_prediction is not None  # valid_prediction_count > 0 が保証する
    prediction_error_mm = _horizontal_distance_mm(
        timeline.final_prediction.predicted_hit_x_mm,
        timeline.final_prediction.predicted_hit_y_mm,
        impact.x_mm,
        impact.y_mm,
    )

    position_ok = position_error_mm <= params.catch.position_tolerance_mm
    if params.catch.policy is CatchPolicy.STOP_AND_WAIT:
        speed_ok = residual_speed_mm_s <= params.catch.residual_speed_tolerance_mm_s
        catchable = position_ok and speed_ok
    else:
        catchable = position_ok

    return ScenarioOutcome(
        catchable=catchable,
        not_evaluated_reason=None,
        true_impact_x_mm=impact.x_mm,
        true_impact_y_mm=impact.y_mm,
        true_impact_time_ms=impact.time_ms,
        required_distance_mm=required_distance_mm,
        hold_time_ms=hold_time_ms,
        first_command_time_ms=first_command_time_ms,
        position_error_mm=position_error_mm,
        residual_speed_mm_s=residual_speed_mm_s,
        prediction_error_mm=prediction_error_mm,
        sample_count=len(samples),
        valid_prediction_count=timeline.valid_prediction_count,
        record=(timeline.record if keep_record else None),
    )


def evaluate_reachability(
    hold_time_ms: float,
    required_distance_mm: float,
    params: ScenarioParams,
) -> ScenarioOutcome:
    """移動体の運動モデルのみで到達可否を判定する（design.md「ScenarioEvaluator」
    Service Interface / 要件6.2, 10.2）。

    `drivetrain.is_reachable` の閉形式判定をそのまま `ScenarioOutcome` へ
    包む。物理・観測・予測の各段を一切経由しないため、失敗モードが
    存在せず、常に確定した `catchable` を返す（`not_evaluated_reason` は
    常に `None`）。

    Invariants:
        乱数を一切使わない（`rng` 引数を持たない）。同一入力に対して
        常に同一結果を返す（design.md「ScenarioEvaluator」Invariants）。
    """
    catchable = drivetrain.is_reachable(
        hold_time_ms, required_distance_mm, params.drivetrain, params.catch.policy
    )
    return ScenarioOutcome(
        catchable=catchable,
        not_evaluated_reason=None,
        true_impact_x_mm=None,
        true_impact_y_mm=None,
        true_impact_time_ms=None,
        required_distance_mm=required_distance_mm,
        hold_time_ms=hold_time_ms,
        first_command_time_ms=None,
        position_error_mm=None,
        residual_speed_mm_s=None,
        prediction_error_mm=None,
        sample_count=0,
        valid_prediction_count=0,
        record=None,
    )
