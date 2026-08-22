"""誤差ゼロ条件のE2E検証（要件2.7, 8.5 / design.md「ScenarioEvaluator」）。

tasks.md 5.1 の観測可能な完了状態「誤差ゼロ E2E のテストが通り、片方の
評価器の判定条件を意図的にずらすと失敗する」を満たすため、本ファイルは
以下を固定する:

- 観測ノイズ・欠測・ばらつきをすべてゼロにした条件で、`evaluate_throw` の
  フル経路（物理 → 観測 → 予測 → 運動 → キャッチ判定）を通した予測結果が、
  `physics.sample_throw` + `physics.solve_true_impact` による解析解と
  一致すること（要件2.7を `observation.observe` 単体ではなく `evaluate_throw`
  のフル経路で再確認する）
- 同条件・STOP_AND_WAIT方針で、`evaluate_throw` の成否が `evaluate_reachability`
  と一致すること（PASS_THROUGH は tasks.md への申し送り事項により構造的に
  この一致を保証できないため対象外。`test_trajectory_sim_evaluate.py` の
  既存の指摘と同じ理由）
- 上記の一致検証が、一方の評価器へ渡す機体性能だけを意図的に食い違わせると
  実際に失敗する（＝チェックに実効性がある）こと
- 本テストがハードウェア・ネットワーク非接続のサンドボックス環境で例外なく
  完走すること自体が、要件8.5の実行時の証拠であること

`tests/trajectory_sim/test_trajectory_sim_evaluate.py`（タスク3.1、
`evaluate_throw`/`evaluate_reachability` の単体レベルのクロスチェックを
含む）とは別の、E2E/受入レベルの新規ファイルである。既存ファイルの内容
には一切手を加えず、そこから import もしない。
"""

from __future__ import annotations

from random import Random

import pytest
from prediction_core import PredictionConfig

from trajectory_sim import evaluate, physics
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

# ---------------------------------------------------------------------------
# 共通フィクスチャ
# ---------------------------------------------------------------------------


def _zero_error_throw() -> ThrowParams:
    """境界（三角形/台形プロファイル切替など）から十分に離れた、素直な投擲条件。

    重力は既定値（9806.65mm/s^2）のまま変更しない。`PredictionConfig` の
    既定の重力も同じ値であるため、予測モデルと真の物理モデルの重力が
    一致し、放物線フィットが真の軌道を厳密に復元できる前提が成り立つ。
    """
    return ThrowParams(
        release_x_mm=0.0,
        release_y_mm=0.0,
        release_z_mm=2000.0,
        speed_mm_s=4000.0,
        elevation_deg=45.0,
        azimuth_deg=0.0,
    )


def _zero_error_observation(**overrides: float) -> ObservationParams:
    """観測ノイズ・欠測をすべてゼロにし、タイミングのみ現実的な小さい値を持つ観測条件。

    `detection_start_delay_ms` / `sample_period_ms` / `sample_latency_ms` /
    `prediction_latency_ms` は誤差要因ではなく純粋なタイミングであるため
    ゼロにはせず、`test_trajectory_sim_evaluate.py`（タスク3.1）のフィクス
    チャと同じ現実的な小さい値を用いる。
    """
    base: dict[str, float] = dict(
        detection_start_delay_ms=20.0,
        sample_period_ms=20.0,
        sample_latency_ms=5.0,
        prediction_latency_ms=3.0,
        sigma_x_mm=0.0,
        sigma_y_mm=0.0,
        sigma_z_mm=0.0,
        distance_sigma_rel_per_m2=0.0,
        dropout_ratio=0.0,
    )
    base.update(overrides)
    return ObservationParams(**base)


def _reachable_drivetrain() -> DrivetrainParams:
    """三角形/台形プロファイルの境界から十分なマージンを持つ、余裕のある機体性能。

    十分な性能マージンにより、誤差ゼロ条件では全経路評価が成立
    （`catchable=True`）するはずの機体性能（`test_trajectory_sim_evaluate.py`
    の「solidly reachable」フィクスチャと同じ値）。
    """
    return DrivetrainParams(
        max_speed_mm_s=6000.0,
        max_accel_mm_s2=30000.0,
        max_decel_mm_s2=30000.0,
        control_period_ms=1.0,
        command_latency_ms=1.0,
        integration_step_ms=1.0,
    )


def _unreachable_drivetrain() -> DrivetrainParams:
    """性能不足が明らかな機体性能（「solidly unreachable」フィクスチャと同じ値）。"""
    return DrivetrainParams(
        max_speed_mm_s=500.0,
        max_accel_mm_s2=500.0,
        max_decel_mm_s2=500.0,
        control_period_ms=1.0,
        command_latency_ms=1.0,
        integration_step_ms=1.0,
    )


def _zero_error_scenario_params(
    *,
    drivetrain: DrivetrainParams,
    policy: CatchPolicy = CatchPolicy.STOP_AND_WAIT,
) -> ScenarioParams:
    """誤差要因をすべてゼロにした `ScenarioParams` を組み立てる。

    `PredictionConfig(measure_elapsed=False)` により、タスク2.5/3.2で
    見つかった実処理時間計測由来のフレーキネスを避ける。
    """
    return ScenarioParams(
        throw=_zero_error_throw(),
        dispersion=ThrowDispersion(),
        observation=_zero_error_observation(),
        drivetrain=drivetrain,
        catch=CatchCriteria(policy=policy),
        layout=LayoutParams(home_x_mm=0.0, home_y_mm=0.0),
        prediction=PredictionConfig(measure_elapsed=False),
    )


# ---------------------------------------------------------------------------
# 1. 誤差ゼロ・フル経路の解析解一致（要件2.7）
# ---------------------------------------------------------------------------


def test_zero_error_full_pipeline_matches_analytic_ground_truth() -> None:
    """誤差要因ゼロ条件で、`evaluate_throw` のフル経路の予測結果が
    解析解（`physics.sample_throw` + `physics.solve_true_impact`）と
    一致すること（要件2.7を `observation.observe` 単体ではなくフル経路で
    再確認する、タスク5.1の必須完了条件）。
    """
    throw = _zero_error_throw()
    params = _zero_error_scenario_params(drivetrain=_reachable_drivetrain())

    # 解析的な真値: `sample_throw` は `ThrowDispersion()` が全項目0.0のため
    # 乱数器から一切値を引かない（design.md「ThrowPhysics」Invariants:
    # 「標準偏差が0の項目は rng.normalvariate を呼ばない」）。したがって
    # これは純粋に決定的な計算であり、`evaluate_throw` 内部が呼ぶのと
    # 同じ低水準関数を呼び出した「独立」な参照値として使える。
    trajectory = physics.sample_throw(throw, ThrowDispersion(), Random(12345))
    analytic_impact = physics.solve_true_impact(trajectory)
    assert analytic_impact is not None  # テスト前提の確認（床面と交わる投擲であること）

    outcome = evaluate.evaluate_throw(
        params, Random(0), record_id="e2e-zero-error", keep_record=True
    )

    assert outcome.not_evaluated_reason is None
    assert outcome.catchable is not None
    assert outcome.sample_count > 0
    assert outcome.valid_prediction_count > 0

    assert outcome.true_impact_x_mm == pytest.approx(analytic_impact.x_mm)
    assert outcome.true_impact_y_mm == pytest.approx(analytic_impact.y_mm)
    assert outcome.true_impact_time_ms == pytest.approx(analytic_impact.time_ms)

    # prediction_error_mm の許容誤差について: 観測ノイズ・欠測をすべて
    # ゼロにし、かつ `PredictionConfig` の重力既定値が `ThrowParams` の
    # 重力既定値と同じ(9806.65mm/s^2)であるため、予測が用いる放物運動
    # モデルと真の物理モデルは完全に一致する。したがって放物線フィット
    # （`prediction_core`）は真の落下地点を IEEE754 浮動小数点精度の
    # 範囲内で厳密に復元できるはずであり、ここでの許容誤差は「観測モデル
    # に起因する意味のある誤差」を許すものではなく、純粋な浮動小数点の
    # 丸め誤差だけを許す極小値（abs=1e-6mm）とする。
    assert outcome.prediction_error_mm == pytest.approx(0.0, abs=1e-6)


# ---------------------------------------------------------------------------
# 2. 誤差ゼロ条件での2評価器の一致（STOP_AND_WAIT、到達可能/不可能の両方）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("drivetrain_factory", "expected_catchable"),
    [
        (_reachable_drivetrain, True),
        (_unreachable_drivetrain, False),
    ],
    ids=["solidly-reachable", "solidly-unreachable"],
)
def test_zero_error_evaluators_agree_stop_and_wait(
    drivetrain_factory: object, expected_catchable: bool
) -> None:
    """誤差ゼロ条件・STOP_AND_WAIT方針で、全経路評価(`evaluate_throw`)の
    成否が到達可否評価(`evaluate_reachability`)と一致すること（境界から
    十分なマージンを取った、到達可能・到達不可能の両条件で確認する。
    タスク5.1の必須完了条件、要件2.7の枠組みの上での「2つの評価器が食い
    違わない」ことの固定）。
    """
    assert callable(drivetrain_factory)
    params = _zero_error_scenario_params(
        drivetrain=drivetrain_factory(), policy=CatchPolicy.STOP_AND_WAIT
    )

    outcome_throw = evaluate.evaluate_throw(
        params, Random(0), record_id="e2e-agree", keep_record=False
    )
    assert outcome_throw.not_evaluated_reason is None
    assert outcome_throw.catchable is expected_catchable

    outcome_reach = evaluate.evaluate_reachability(
        outcome_throw.hold_time_ms, outcome_throw.required_distance_mm, params
    )

    assert outcome_reach.catchable == outcome_throw.catchable


# ---------------------------------------------------------------------------
# 3. ミューテーション感度の証明: 上記の一致検証が空虚でないことの証明
# ---------------------------------------------------------------------------


def test_agreement_check_has_teeth_when_drivetrain_is_skewed_between_evaluators() -> None:
    """上の一致検証（完了条件2）が実効性のある回帰検知であり、常に一致を
    返す空虚な比較ではないことを証明する（tasks.md 5.1 観測可能な完了状態:
    「片方の評価器の判定条件を意図的にずらすと失敗する」）。

    production コードは一切変更しない。代わりに、`evaluate_reachability`
    へ渡す `params.drivetrain` だけを、`evaluate_throw` が実際に使った
    機体性能とは著しく劣る値へ意図的にすり替える。これは「一方の評価器の
    参照する判定条件が食い違った」という回帰そのものを模擬している。

    このすり替えにより2評価器の `catchable` が実際に食い違うことを確認
    できて初めて、完了条件2の一致アサーションが「両者が本当に同じ判定
    条件を見ているか」を検出できる、実効性のあるチェックであることが
    証明される（もし完了条件2のアサーションが常に真になる空虚な比較
    だったなら、このテストの食い違いの検出にも失敗していたはずである）。
    """
    reachable_drivetrain = _reachable_drivetrain()
    skewed_drivetrain = _unreachable_drivetrain()

    params_for_throw = _zero_error_scenario_params(
        drivetrain=reachable_drivetrain, policy=CatchPolicy.STOP_AND_WAIT
    )
    outcome_throw = evaluate.evaluate_throw(
        params_for_throw, Random(0), record_id="e2e-mutation", keep_record=False
    )
    assert outcome_throw.not_evaluated_reason is None
    # 完了条件2の「solidly-reachable」ケースと同じ条件のため成立するはず。
    assert outcome_throw.catchable is True

    # `evaluate_throw` が実際に使った params_for_throw とは
    # `drivetrain` だけが異なる params を、`evaluate_reachability` 側にだけ
    # 与える（＝一方の評価器の判定条件だけを意図的にずらす）。
    params_with_skewed_drivetrain = ScenarioParams(
        throw=params_for_throw.throw,
        dispersion=params_for_throw.dispersion,
        observation=params_for_throw.observation,
        drivetrain=skewed_drivetrain,
        catch=params_for_throw.catch,
        layout=params_for_throw.layout,
        prediction=params_for_throw.prediction,
    )

    outcome_reach_skewed = evaluate.evaluate_reachability(
        outcome_throw.hold_time_ms,
        outcome_throw.required_distance_mm,
        params_with_skewed_drivetrain,
    )

    # 機体性能を意図的に食い違わせたことで、2つの評価器の catchable 判定が
    # 実際に分かれることを確認する。分かれなければ、完了条件2の一致
    # アサーションはこの種の回帰を検出できない空虚なチェックだったことに
    # なる。
    assert outcome_reach_skewed.catchable is False
    assert outcome_reach_skewed.catchable != outcome_throw.catchable


# ---------------------------------------------------------------------------
# 4. ハードウェア・ネットワーク非接続でのフル経路完走（要件8.5）
# ---------------------------------------------------------------------------


def test_full_pipeline_completes_without_hardware_or_network_access() -> None:
    """フル経路評価と到達可否評価が、カメラ・ネットワークいずれにも接続
    していない本サンドボックス環境で例外を投げずに完走すること。

    本テスト自身がこの環境で実行され成功することそのものが、要件8.5
    （すべての機能をハードウェア非接続の環境で実行・検証できること）の
    フル経路に対する実行時の証拠である。特別なモックや環境変数チェックは
    不要で、本パッケージの依存関係（実行時サードパーティ依存ゼロ）と
    設計上、カメラ・ネットワークへの依存経路自体が存在しない。
    """
    params = _zero_error_scenario_params(drivetrain=_reachable_drivetrain())

    outcome = evaluate.evaluate_throw(
        params, Random(0), record_id="e2e-no-hardware", keep_record=True
    )

    assert outcome.not_evaluated_reason is None
    assert outcome.catchable is not None

    reachability_outcome = evaluate.evaluate_reachability(
        outcome.hold_time_ms, outcome.required_distance_mm, params
    )
    assert reachability_outcome.catchable is not None
