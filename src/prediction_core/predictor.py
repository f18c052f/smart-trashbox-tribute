"""入力検証・推定・交点算出・時間計測を統合する Predictor(要件 1.3 / 1.4 / 3.3 / 3.5 / 6 / 8 / 10.6)。

Predictor コンポーネント。L4 層であり、`fitting` と `impact`(L3、互いに独立な
兄弟モジュール)の両方を import してよい唯一のモジュールである
(design.md「Dependency Direction」)。

**このファイルはタスク 3.1 / 3.2 の範囲に限定する。** design.md の Predictor は
以下をすべて要求するが、タスク 3.1 / 3.2 が実装するのは 6 段階の検証順序と
正常系の統合のみである。

- 6 段階の検証順序(要件 6.1-6.5、design.md「Predictor / Responsibilities &
  Constraints」)を、タスク 3.1 / 3.2 の2タスクに分けて実装した。

  1. 要素が `Sample` であること -> 違反時 `MALFORMED_INPUT`(6.5、タスク 3.2)
  2. 全フィールドが有限であること -> 違反時 `NON_FINITE_VALUE`(6.4、入力側、タスク 3.2)
  3. サンプル数が `min_samples` 以上であること -> 違反時 `INSUFFICIENT_SAMPLES`(6.1、タスク 3.2)
  4. 時刻が縮退していないこと -> 違反時 `DEGENERATE_TIME`(6.2、タスク 3.1。
     `fit_trajectory` がネイティブに返す理由をそのまま伝播する)
  5. 未来側に床面交点があること -> 違反時 `NO_FUTURE_FLOOR_CROSSING`(6.3、タスク 3.1。
     `solve_floor_impact` がネイティブに返す理由をそのまま伝播する)
  6. 算出結果が全て有限であること -> 違反時 `NON_FINITE_VALUE`(6.4、出力側、タスク 3.2)

  1-3 は下位(`fit_trajectory` / `solve_floor_impact`)へ渡す前に、この順序で
  短絡評価する。複数条件が同時に成立する入力でも、返る `reason` が判定順序
  どおり決定的になることが契約である(design.md「Risks」)。6 は下位の
  パイプラインが成功した後、`Prediction` を組み立てて返す直前に行う。

  どの段階の失敗も例外を送出しない。無効な予測は値として `InvalidPrediction`
  で返す。`detail` には判定の根拠となる文脈(サンプル数・時刻範囲など)を
  人が読める形で添えるが、`detail` の内容そのものを分岐条件には使わない
  (`types.py` の `InvalidPrediction.detail` docstring 参照)。

- `elapsed_ms` の実測(`time.perf_counter_ns` と `config.measure_elapsed`)は
  タスク 3.3 が追加する。本タスクまでは常に `None` を設定する。
"""

from __future__ import annotations

import math
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
        成功時は `Prediction`。6 段階の検証順序(モジュール docstring 参照)
        のいずれかに違反した場合は、その理由を持つ `InvalidPrediction`。
        タスク 3.2 の時点では `elapsed_ms` は常に `None`(タスク 3.3 が実測に
        置き換える)。
    """
    # ステップ1: 要素が Sample であること(要件 6.5)。
    # 違反時は `sample.t_ms` などへのアクセス自体が安全でないため、
    # `based_on_time_ms` は求めず None のまま返す。
    sample_count = len(samples)
    if not all(isinstance(sample, Sample) for sample in samples):
        return InvalidPrediction(
            reason=InvalidReason.MALFORMED_INPUT,
            detail=(
                "入力サンプル列に Sample 型でない要素が含まれています"
                f"(サンプル数: {sample_count}件)。"
            ),
            sample_count=sample_count,
            based_on_time_ms=None,
            elapsed_ms=None,
            config=config,
        )

    # この時点で全要素が Sample であることが確定しているため、
    # `.t_ms` / `.x_mm` / `.y_mm` / `.z_mm` への安全なアクセスが可能。

    # ステップ2: 全フィールドが有限であること(要件 6.4、入力側)。
    if not all(
        math.isfinite(value)
        for sample in samples
        for value in (sample.t_ms, sample.x_mm, sample.y_mm, sample.z_mm)
    ):
        return InvalidPrediction(
            reason=InvalidReason.NON_FINITE_VALUE,
            detail=(
                "入力サンプルに非有限の値(NaN または Infinity)が含まれています"
                f"(サンプル数: {sample_count}件)。"
            ),
            sample_count=sample_count,
            based_on_time_ms=max((sample.t_ms for sample in samples), default=None),
            elapsed_ms=None,
            config=config,
        )

    # ここまでで全要素が Sample かつ全フィールド有限であることが確定した
    # ため、以降はこの based_on_time_ms を使い回す。`default=None` は
    # samples が空列の場合(全称量化が空虚に真となり得るため)への防御。
    based_on_time_ms = max((sample.t_ms for sample in samples), default=None)

    # ステップ3: サンプル数が min_samples 以上であること(要件 6.1)。
    if sample_count < config.min_samples:
        return InvalidPrediction(
            reason=InvalidReason.INSUFFICIENT_SAMPLES,
            detail=(
                f"観測サンプル数が不足しています: {sample_count}件"
                f"(最小 {config.min_samples}件必要)。"
            ),
            sample_count=sample_count,
            based_on_time_ms=based_on_time_ms,
            elapsed_ms=None,
            config=config,
        )

    sorted_samples = sorted(samples, key=lambda sample: sample.t_ms)

    # ステップ4: 時刻が縮退していないこと(要件 6.2)。
    # `fit_trajectory` がネイティブに返す DEGENERATE_TIME をそのまま伝播する。
    fit_outcome = fit_trajectory(sorted_samples, config)
    if isinstance(fit_outcome, InvalidReason):
        return InvalidPrediction(
            reason=fit_outcome,
            detail=(
                f"軌道推定に失敗しました: {fit_outcome.value}"
                f"(サンプル数: {sample_count}件、最新観測時刻: {based_on_time_ms}ms)。"
            ),
            sample_count=sample_count,
            based_on_time_ms=based_on_time_ms,
            elapsed_ms=None,
            config=config,
        )
    fit_result = fit_outcome

    # ステップ5: 未来側に床面交点があること(要件 6.3)。
    # `solve_floor_impact` がネイティブに返す NO_FUTURE_FLOOR_CROSSING を
    # そのまま伝播する。
    impact_outcome = solve_floor_impact(fit_result.trajectory, latest_time_ms=based_on_time_ms)
    if isinstance(impact_outcome, InvalidReason):
        return InvalidPrediction(
            reason=impact_outcome,
            detail=(
                f"落下点の算出に失敗しました: {impact_outcome.value}"
                f"(サンプル数: {sample_count}件、最新観測時刻: {based_on_time_ms}ms)。"
            ),
            sample_count=sample_count,
            based_on_time_ms=based_on_time_ms,
            elapsed_ms=None,
            config=config,
        )
    impact = impact_outcome

    candidate = Prediction(
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

    # ステップ6: 算出結果が全て有限であること(要件 6.4、出力側)。
    # 結果直下の8フィールドに加え、要件 6.4 の「算出結果」が結果全体を
    # 指す広い表現であることを踏まえ、同梱する trajectory の8フィールドも
    # 併せて検査する(防御的な網羅性のため)。
    output_values = (
        candidate.predicted_hit_x_mm,
        candidate.predicted_hit_y_mm,
        candidate.predicted_hit_time_ms,
        candidate.remaining_time_ms,
        candidate.estimated_vx_mm_s,
        candidate.estimated_vy_mm_s,
        candidate.estimated_vz_mm_s,
        candidate.residual,
        candidate.trajectory.t_ref_ms,
        candidate.trajectory.x0_mm,
        candidate.trajectory.y0_mm,
        candidate.trajectory.z0_mm,
        candidate.trajectory.estimated_vx_mm_s,
        candidate.trajectory.estimated_vy_mm_s,
        candidate.trajectory.estimated_vz_mm_s,
        candidate.trajectory.gravity_mm_s2,
    )
    if not all(math.isfinite(value) for value in output_values):
        return InvalidPrediction(
            reason=InvalidReason.NON_FINITE_VALUE,
            detail=(
                "算出結果に非有限の値(NaN または Infinity)が含まれています"
                f"(サンプル数: {sample_count}件、最新観測時刻: {based_on_time_ms}ms)。"
            ),
            sample_count=sample_count,
            based_on_time_ms=based_on_time_ms,
            elapsed_ms=None,
            config=config,
        )

    return candidate
