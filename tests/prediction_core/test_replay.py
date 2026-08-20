"""Replay と予測系列の同値判定の検証（タスク 4.3、要件 9.4）。

design.md「ThrowRecordCodec」が定める `replay` / `predictions_equivalent` を
対象にする。`replay` は `ThrowPredictionTracker`（まだ存在しない、タスク
4.4）に依存せず、`record.samples` の**前置列**（1点目、1〜2点目、…、全点）
それぞれに `predict()` を適用して再構成する契約である。

`predictions_equivalent` は `elapsed_ms`（実測値であり呼び出しのたびに
変動しうる）を比較対象から除外し、それ以外の全フィールドを厳密比較する
契約である。
"""

from __future__ import annotations

from dataclasses import replace

from prediction_core.config import PredictionConfig
from prediction_core.predictor import predict
from prediction_core.record import ThrowRecord, predictions_equivalent, replay
from prediction_core.types import (
    InvalidPrediction,
    InvalidReason,
    Prediction,
    Sample,
    SourceKind,
)

from analytic import KnownTrajectory, generate_samples


def _trajectory() -> KnownTrajectory:
    """min_samples=3 未満のサンプル数でも InsufficientSamples を含む
    前置列を作れるよう、5点分のサンプルを生成できる普通の斜方投射軌道。
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


def _samples() -> tuple[Sample, ...]:
    return tuple(generate_samples(_trajectory(), [0.0, 20.0, 40.0, 60.0, 80.0]))


def _build_record(
    config: PredictionConfig, *, record_id: str = "throw-replay"
) -> ThrowRecord:
    """`predict()` を前置列へ手動で適用し、`ThrowRecord` を直接組み立てる。

    `ThrowPredictionTracker`（タスク 4.4、まだ存在しない）を使わずに
    予測系列を再現するため、レコード組み立て自体もそれに依存しない。
    """
    samples = _samples()
    predictions = tuple(
        predict(samples[: i + 1], config) for i in range(len(samples))
    )
    return ThrowRecord(
        record_id=record_id,
        source=SourceKind.SIMULATED,
        config=config,
        samples=samples,
        predictions=predictions,
    )


class TestReplayReconstructsRecordedPredictions:
    """主要な観測可能完了状態: 記録済みの予測系列と Replay 結果が同値。"""

    def test_replay_matches_recorded_predictions(
        self, default_config: PredictionConfig
    ) -> None:
        """既定設定（min_samples=3）では、先頭2つの前置列は
        InsufficientSamples になり、残り3つは有効な予測になる。
        Replay はこの混在をそのまま再現できなければならない。
        """
        record = _build_record(default_config)

        # 前提: 先頭2件が無効、残り3件が有効であることを確認しておく
        # （このテストが InsufficientSamples を含む前置列を実際に
        # 経由していることを保証するため）。
        assert isinstance(record.predictions[0], InvalidPrediction)
        assert record.predictions[0].reason == InvalidReason.INSUFFICIENT_SAMPLES
        assert isinstance(record.predictions[1], InvalidPrediction)
        assert record.predictions[1].reason == InvalidReason.INSUFFICIENT_SAMPLES
        assert isinstance(record.predictions[2], Prediction)
        assert isinstance(record.predictions[3], Prediction)
        assert isinstance(record.predictions[4], Prediction)

        replayed = replay(record)

        assert len(replayed) == len(record.predictions)
        assert predictions_equivalent(replayed, record.predictions)

    def test_replay_matches_recorded_predictions_after_json_round_trip(
        self, default_config: PredictionConfig
    ) -> None:
        """要件 9.4 の観測可能完了状態: JSON 往復を挟んだレコードでも成立する。"""
        record = _build_record(default_config, record_id="throw-replay-json")

        round_tripped = ThrowRecord.from_json(record.to_json())

        replayed = replay(round_tripped)

        assert predictions_equivalent(replayed, round_tripped.predictions)
        # 元のレコードに対しても成立すること(JSON 往復自体が意味を保つ)。
        assert predictions_equivalent(replayed, record.predictions)

    def test_replay_with_measure_elapsed_disabled_still_matches(self) -> None:
        """`measure_elapsed=False` でも(全 elapsed_ms が None でも)同値判定は成立する。"""
        config = PredictionConfig(measure_elapsed=False)
        record = _build_record(config, record_id="throw-replay-no-elapsed")

        replayed = replay(record)

        assert predictions_equivalent(replayed, record.predictions)
        # 実測なしなら elapsed_ms は全件 None のはず(前提の再確認)。
        assert all(p.elapsed_ms is None for p in record.predictions)

    def test_replay_returns_tuple(self, default_config: PredictionConfig) -> None:
        record = _build_record(default_config, record_id="throw-replay-type")

        replayed = replay(record)

        assert isinstance(replayed, tuple)

    def test_replay_is_idempotent(self, default_config: PredictionConfig) -> None:
        """`replay` は副作用を持たず、何度呼んでも同じ結果を返す。"""
        record = _build_record(default_config, record_id="throw-replay-idempotent")

        first = replay(record)
        second = replay(record)

        assert predictions_equivalent(first, second)

    def test_replay_does_not_use_full_sample_set_for_early_prefixes(
        self, default_config: PredictionConfig
    ) -> None:
        """Replay が前置列ではなく毎回全サンプルを使うと、先頭2件が
        InsufficientSamples ではなく有効な予測になってしまう
        (ミューテーション検知の直接的な固定)。
        """
        record = _build_record(default_config, record_id="throw-replay-prefix")

        replayed = replay(record)

        assert isinstance(replayed[0], InvalidPrediction)
        assert replayed[0].reason == InvalidReason.INSUFFICIENT_SAMPLES
        assert replayed[0].sample_count == 1
        assert isinstance(replayed[1], InvalidPrediction)
        assert replayed[1].sample_count == 2


class TestPredictionsEquivalent:
    """`predictions_equivalent` 単体の契約を固定する。"""

    def test_identical_sequences_are_equivalent(
        self, default_config: PredictionConfig
    ) -> None:
        record = _build_record(default_config, record_id="throw-eq-identical")

        assert predictions_equivalent(record.predictions, record.predictions)

    def test_sequences_differing_only_in_elapsed_ms_are_equivalent(
        self, default_config: PredictionConfig
    ) -> None:
        """`elapsed_ms` は実測値であり比較対象から除外される。"""
        record = _build_record(default_config, record_id="throw-eq-elapsed")

        shifted = tuple(
            replace(outcome, elapsed_ms=(outcome.elapsed_ms or 0.0) + 999.0)
            for outcome in record.predictions
        )

        assert predictions_equivalent(record.predictions, shifted)
        # 対称性も確認しておく。
        assert predictions_equivalent(shifted, record.predictions)

    def test_sequences_differing_in_other_prediction_field_are_not_equivalent(
        self, default_config: PredictionConfig
    ) -> None:
        record = _build_record(default_config, record_id="throw-eq-diff-pred")
        # 系列中、有効な Prediction のインデックス(2番目以降)を1つ変える。
        prediction_index = next(
            i
            for i, outcome in enumerate(record.predictions)
            if isinstance(outcome, Prediction)
        )
        mutated = list(record.predictions)
        mutated[prediction_index] = replace(
            mutated[prediction_index],
            residual=mutated[prediction_index].residual + 1.0,
        )

        assert not predictions_equivalent(record.predictions, tuple(mutated))

    def test_sequences_differing_in_other_invalid_field_are_not_equivalent(
        self, default_config: PredictionConfig
    ) -> None:
        record = _build_record(default_config, record_id="throw-eq-diff-invalid")
        invalid_index = next(
            i
            for i, outcome in enumerate(record.predictions)
            if isinstance(outcome, InvalidPrediction)
        )
        mutated = list(record.predictions)
        mutated[invalid_index] = replace(
            mutated[invalid_index], detail="意図的に変えた detail"
        )

        assert not predictions_equivalent(record.predictions, tuple(mutated))

    def test_sequences_with_different_lengths_are_not_equivalent(
        self, default_config: PredictionConfig
    ) -> None:
        record = _build_record(default_config, record_id="throw-eq-len")

        assert not predictions_equivalent(
            record.predictions, record.predictions[:-1]
        )

    def test_sequences_with_mismatched_kind_at_same_position_are_not_equivalent(
        self, default_config: PredictionConfig
    ) -> None:
        """同じ位置で `Prediction` と `InvalidPrediction` が入れ替わっていれば偽。"""
        record = _build_record(default_config, record_id="throw-eq-kind")
        prediction_index = next(
            i
            for i, outcome in enumerate(record.predictions)
            if isinstance(outcome, Prediction)
        )
        invalid_index = next(
            i
            for i, outcome in enumerate(record.predictions)
            if isinstance(outcome, InvalidPrediction)
        )

        swapped = list(record.predictions)
        swapped[prediction_index], swapped[invalid_index] = (
            swapped[invalid_index],
            swapped[prediction_index],
        )

        assert not predictions_equivalent(record.predictions, tuple(swapped))

    def test_empty_sequences_are_equivalent(self) -> None:
        assert predictions_equivalent((), ())
