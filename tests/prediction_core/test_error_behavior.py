"""サンプル数と予測誤差の評価出力を検証する(タスク 5.3、要件 7.3 / 7.4)。

design.md「Testing Strategy / E2E / 評価テスト」節が定める方針を対象にする。

- 既知の放物線(`analytic.py` のオラクル)へ決定的な擬似乱数(`random.Random(seed)`)
  でノイズを重畳し、`config.min_samples` 以上へサンプル数を増やした予測系列を
  `ThrowPredictionTracker` で取得する。
- 各予測(`Prediction`)から `sample_count` / `residual` / 落下地点誤差が
  同一系列から一貫して取り出せ、サンプル数の異なる予測どうしを相互比較できる
  形(タプルのリスト)へ組み立てられることを確認する。
- **誤差が単調に減少することは合否条件にしない**(design.md が明示的に禁止。
  決定的シードであっても偶然そうならない場合があるため、`tech.md` 開発標準1)。
  検証するのは「評価に必要な出力が揃っていること」に限る。
"""

from __future__ import annotations

import math

from prediction_core.config import PredictionConfig
from prediction_core.tracker import ThrowPredictionTracker
from prediction_core.types import Prediction, SourceKind

from analytic import (
    AnalyticFloorImpact,
    KnownTrajectory,
    add_noise,
    analytic_floor_impact,
    generate_samples,
)


def _trajectory() -> KnownTrajectory:
    """斜方投射軌道。既定 config の重力(9806.65 mm/s^2)と一致させ、
    フィッティングが既知の物理と整合する状況を作る。
    """
    return KnownTrajectory(
        x0_mm=0.0,
        vx_mm_s=300.0,
        y0_mm=0.0,
        vy_mm_s=-100.0,
        z0_mm=800.0,
        vz_mm_s=1500.0,
        gravity_mm_s2=9806.65,
    )


def _noisy_samples(*, seed: int, stddev_mm: float, count: int):
    """`min_samples` 以上のサンプル数へ増やせる、決定的ノイズ付きサンプル列。

    落下(解析解 約585ms)より十分手前の 0〜(count-1)*30 ms を等間隔サンプリング
    してから、`add_noise` で決定的なガウスノイズを重畳する。
    """
    times_ms = [30.0 * i for i in range(count)]
    exact_samples = generate_samples(_trajectory(), times_ms)
    return add_noise(exact_samples, seed=seed, stddev_mm=stddev_mm)


def test_error_behavior_outputs_are_available_across_growing_sample_counts() -> None:
    """サンプル数を増やした予測系列から sample_count / residual / 落下地点誤差
    が一貫して取り出せ、サンプル数の異なる予測どうしを相互比較できる形の
    データ構造を組み立てられること(観測可能な完了状態)。
    """
    config = PredictionConfig()
    trajectory = _trajectory()
    noisy_samples = _noisy_samples(seed=20240521, stddev_mm=3.0, count=10)

    tracker = ThrowPredictionTracker(
        record_id="error-behavior-throw",
        source=SourceKind.SIMULATED,
        config=config,
    )
    outcomes = [tracker.add_sample(sample) for sample in noisy_samples]

    valid_predictions = [o for o in outcomes if isinstance(o, Prediction)]
    # min_samples=3(既定)未満の予測は除外されているはずであり、
    # 10 点中 3〜10 点目の 8 件が有効であることを前提として以降を検証する。
    assert len(valid_predictions) == len(noisy_samples) - (config.min_samples - 1)

    # 落下地点誤差の基準となる解析解は、系列全体で単一の値を使う
    # (同一の既知軌道・同一の落下事象を指すため、観測開始前の時刻を
    # after_time_ms に渡せば足りる)。
    analytic_impact = analytic_floor_impact(trajectory, after_time_ms=0.0)
    assert isinstance(analytic_impact, AnalyticFloorImpact)

    # 「評価に必要な出力が揃っていること」の主眼: sample_count / residual /
    # 落下地点誤差を同一系列から取り出し、相互比較できるタプルの列に組み立てる。
    evaluation_series: list[tuple[int, float, float]] = []
    for prediction in valid_predictions:
        sample_count = prediction.sample_count
        residual = prediction.residual
        hit_error_mm = math.hypot(
            prediction.predicted_hit_x_mm - analytic_impact.hit_x_mm,
            prediction.predicted_hit_y_mm - analytic_impact.hit_y_mm,
        )
        evaluation_series.append((sample_count, residual, hit_error_mm))

    # データ構造そのものの契約: 長さがサンプル数の異なる予測の件数と一致し、
    # 各要素が (sample_count, residual, hit_error_mm) の3値であること。
    assert len(evaluation_series) == len(valid_predictions)
    for sample_count, residual, hit_error_mm in evaluation_series:
        assert isinstance(sample_count, int)
        assert isinstance(residual, float)
        assert isinstance(hit_error_mm, float)
        assert math.isfinite(residual)
        assert math.isfinite(hit_error_mm)
        assert residual >= 0.0
        assert hit_error_mm >= 0.0

    # サンプル数の異なる予測どうしを相互比較できること: sample_count が
    # 予測系列上で狭義単調増加しており、各予測を一意に識別・整列できる
    # (要件 4.3 と同型の性質を、この評価用データ構造の上でも確認する)。
    sample_counts = [item[0] for item in evaluation_series]
    assert sample_counts == sorted(sample_counts)
    assert len(set(sample_counts)) == len(sample_counts)
    assert sample_counts == list(range(config.min_samples, len(noisy_samples) + 1))

    # residual / hit_error_mm が実際に各予測から個別に取り出されていること
    # (固定値を使い回す実装ではないこと)の弱い傍証として、値が全て同一に
    # なっていないことだけを確認する。トレンド(増加・減少)は主張しない。
    residuals = [item[1] for item in evaluation_series]
    hit_errors_mm = [item[2] for item in evaluation_series]
    assert len(set(residuals)) > 1
    assert len(set(hit_errors_mm)) > 1

    # 誤差の単調減少はアサートしない(design.md「Testing Strategy」が明示的に
    # 禁止。決定的シードでも偶然そうならない場合があるため、tech.md 開発標準1)。


def test_error_behavior_series_is_reproducible_for_the_same_seed() -> None:
    """同一の投擲・同一のノイズシードから得た予測系列は、sample_count /
    residual / 落下地点誤差のいずれも完全に再現する。

    `analytic.py` 自体の決定性契約は `test_analytic.py` が既に固定して
    いるため、ここでは「本テストが組み立てる評価用データ構造」が
    再現可能であることに限定して軽く確認する(重複を避ける)。
    """
    config = PredictionConfig()
    trajectory = _trajectory()

    def _evaluation_series() -> list[tuple[int, float, float]]:
        noisy_samples = _noisy_samples(seed=13579, stddev_mm=2.0, count=6)
        tracker = ThrowPredictionTracker(
            record_id="error-behavior-reproducible",
            source=SourceKind.SIMULATED,
            config=config,
        )
        outcomes = [tracker.add_sample(sample) for sample in noisy_samples]
        analytic_impact = analytic_floor_impact(trajectory, after_time_ms=0.0)
        assert isinstance(analytic_impact, AnalyticFloorImpact)

        series: list[tuple[int, float, float]] = []
        for outcome in outcomes:
            if not isinstance(outcome, Prediction):
                continue
            hit_error_mm = math.hypot(
                outcome.predicted_hit_x_mm - analytic_impact.hit_x_mm,
                outcome.predicted_hit_y_mm - analytic_impact.hit_y_mm,
            )
            series.append((outcome.sample_count, outcome.residual, hit_error_mm))
        return series

    first = _evaluation_series()
    second = _evaluation_series()

    assert first == second
    assert len(first) >= 2
