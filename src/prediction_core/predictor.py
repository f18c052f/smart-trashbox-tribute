"""入力検証・推定・交点算出・時間計測を統合する Predictor(要件 1.3 / 1.4 / 3.3 / 3.5 / 6 / 8 / 10.6)。

Predictor コンポーネント。L4 層であり、`fitting` と `impact`(L3、互いに独立な
兄弟モジュール)の両方を import してよい唯一のモジュールである
(design.md「Dependency Direction」)。

**このファイルはタスク 3.1 の範囲に限定する。** design.md の Predictor は
以下をすべて要求するが、タスク 3.1 が実装するのはそのうち正常系の統合と、
`fit_trajectory` / `solve_floor_impact` がネイティブに返す無効理由の伝播のみである。

- 6 段階の検証順序(`MALFORMED_INPUT` -> `NON_FINITE_VALUE`(入力) ->
  `INSUFFICIENT_SAMPLES` -> `DEGENERATE_TIME` -> `NO_FUTURE_FLOOR_CROSSING` ->
  `NON_FINITE_VALUE`(出力))のうち、本タスクが行うのは `DEGENERATE_TIME` と
  `NO_FUTURE_FLOOR_CROSSING` の 2 段階のみである。他 3 段階の検証は
  タスク 3.2 が追加する
- `elapsed_ms` の実測(`time.perf_counter_ns` と `config.measure_elapsed`)は
  タスク 3.3 が追加する。本タスクでは常に `None` を設定する

このタスクの前提(呼び出し側が保証する。ここでは検証しない):
    - `samples` の全要素は `Sample` インスタンスであり、全フィールドが有限である
    - `len(samples) >= config.min_samples`
"""

from __future__ import annotations

from collections.abc import Sequence

from prediction_core.config import PredictionConfig
from prediction_core.fitting import fit_trajectory
from prediction_core.impact import solve_floor_impact
from prediction_core.types import InvalidPrediction, InvalidReason, Prediction, PredictionOutcome, Sample

__all__ = ["predict"]


def predict(
    samples: Sequence[Sample],
    config: PredictionConfig,
) -> PredictionOutcome:
    """観測サンプル列から落下地点・落下時刻を推定する。

    公開シグネチャは `samples` と `config` のみであり、デバイス固有の型・
    カメラパラメータ・ファイル入出力を一切受け取らない(要件 1.4)。

    入力は `t_ms` 昇順に安定ソートしてから下位(`fit_trajectory` /
    `solve_floor_impact`)へ渡す。Python の `sorted()` は安定ソートである
    ため、同一サンプル集合であれば入力の並び順が異なっても結果が一致する
    (要件 1.1 / 1.3)。

    Returns:
        成功時は `Prediction`。`fit_trajectory` が観測時刻の縮退を検出した
        場合、または `solve_floor_impact` が未来側の床面交点なしと判定した
        場合は、その理由をそのまま伝播した `InvalidPrediction`。
        タスク 3.1 の時点では `elapsed_ms` は常に `None`(タスク 3.3 が実測に
        置き換える)。
    """
    sorted_samples = sorted(samples, key=lambda sample: sample.t_ms)
    sample_count = len(samples)
    based_on_time_ms = max(sample.t_ms for sample in samples)

    fit_outcome = fit_trajectory(sorted_samples, config)
    if isinstance(fit_outcome, InvalidReason):
        return InvalidPrediction(
            reason=fit_outcome,
            detail=f"軌道推定に失敗しました: {fit_outcome.value}",
            sample_count=sample_count,
            based_on_time_ms=based_on_time_ms,
            elapsed_ms=None,
            config=config,
        )
    fit_result = fit_outcome

    impact_outcome = solve_floor_impact(fit_result.trajectory, latest_time_ms=based_on_time_ms)
    if isinstance(impact_outcome, InvalidReason):
        return InvalidPrediction(
            reason=impact_outcome,
            detail=f"落下点の算出に失敗しました: {impact_outcome.value}",
            sample_count=sample_count,
            based_on_time_ms=based_on_time_ms,
            elapsed_ms=None,
            config=config,
        )
    impact = impact_outcome

    return Prediction(
        predicted_hit_x_mm=impact.hit_x_mm,
        predicted_hit_y_mm=impact.hit_y_mm,
        predicted_hit_time_ms=impact.hit_time_ms,
        remaining_time_ms=impact.hit_time_ms - based_on_time_ms,
        estimated_vx_mm_s=fit_result.trajectory.estimated_vx_mm_s,
        estimated_vy_mm_s=fit_result.trajectory.estimated_vy_mm_s,
        estimated_vz_mm_s=fit_result.trajectory.estimated_vz_mm_s,
        residual=fit_result.residual,
        trajectory=fit_result.trajectory,
        sample_count=sample_count,
        based_on_time_ms=based_on_time_ms,
        elapsed_ms=None,
        config=config,
    )
