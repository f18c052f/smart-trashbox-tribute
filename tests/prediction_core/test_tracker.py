"""ThrowPredictionTracker の逐次予測契約の検証(タスク 4.4、要件 4.1-4.3 / 5.1-5.4)。

design.md「ThrowPredictionTracker」節が定める契約を対象にする。

- `add_sample` は常に `PredictionOutcome` を返し、常に予測系列へ追加する
  (最小サンプル数未満の間は `InsufficientSamples`)。
- `len(predictions) == len(samples)` が常に成立する(要件 5.2)。
- `add_sample` の戻り値は `predictions[-1]` と同一である。
- `first_valid` は系列中で最初の `Prediction`、無ければ `None`(要件 4.1)。
- `sample_count` は `Prediction` の系列上で狭義単調増加する(要件 4.3)。
- `samples` / `predictions` は `tuple` として公開され、外部から内部状態を
  変更できない。
- `to_record()` はトラッカーの状態と一致する `ThrowRecord` を返す。
- 駆動制御・目標座標送信のメソッドを持たない(要件 5.4)。
"""

from __future__ import annotations

import pytest

from prediction_core.config import PredictionConfig
from prediction_core.record import ThrowRecord
from prediction_core.tracker import ThrowPredictionTracker
from prediction_core.types import (
    InvalidPrediction,
    InvalidReason,
    Prediction,
    Sample,
    SourceKind,
)

from analytic import KnownTrajectory, generate_samples


def _trajectory() -> KnownTrajectory:
    """普通の斜方投射軌道(analytic.py の教科書式オラクル)。"""
    return KnownTrajectory(
        x0_mm=0.0,
        vx_mm_s=300.0,
        y0_mm=0.0,
        vy_mm_s=-100.0,
        z0_mm=800.0,
        vz_mm_s=1500.0,
        gravity_mm_s2=9806.65,
    )


def _samples(count: int) -> list[Sample]:
    times_ms = [20.0 * i for i in range(count)]
    return generate_samples(_trajectory(), times_ms)


def _new_tracker(
    config: PredictionConfig, *, record_id: str = "throw-tracker"
) -> ThrowPredictionTracker:
    return ThrowPredictionTracker(
        record_id=record_id, source=SourceKind.SIMULATED, config=config
    )


class TestFirstValidPrediction:
    """主要な観測可能完了状態: min_samples 点で最初の有効予測が返る(要件 4.1)。"""

    def test_first_valid_prediction_at_min_samples_default(
        self, default_config: PredictionConfig
    ) -> None:
        """既定の min_samples=3 で、1・2点目は無効、3点目で初めて有効になる。"""
        tracker = _new_tracker(default_config)
        samples = _samples(5)

        outcomes = [tracker.add_sample(sample) for sample in samples]

        assert isinstance(outcomes[0], InvalidPrediction)
        assert outcomes[0].reason == InvalidReason.INSUFFICIENT_SAMPLES
        assert isinstance(outcomes[1], InvalidPrediction)
        assert outcomes[1].reason == InvalidReason.INSUFFICIENT_SAMPLES
        assert isinstance(outcomes[2], Prediction)
        assert isinstance(outcomes[3], Prediction)
        assert isinstance(outcomes[4], Prediction)

    @pytest.mark.parametrize("min_samples", [3, 4, 5])
    def test_first_valid_prediction_at_configured_min_samples(
        self, min_samples: int
    ) -> None:
        """min_samples を変えても、ちょうどその点数目で最初の Prediction が返る。"""
        config = PredictionConfig(min_samples=min_samples)
        tracker = _new_tracker(config)
        samples = _samples(min_samples + 2)

        outcomes = [tracker.add_sample(sample) for sample in samples]

        for outcome in outcomes[: min_samples - 1]:
            assert isinstance(outcome, InvalidPrediction)
            assert outcome.reason == InvalidReason.INSUFFICIENT_SAMPLES
        assert isinstance(outcomes[min_samples - 1], Prediction)
        assert isinstance(outcomes[min_samples], Prediction)
        assert isinstance(outcomes[min_samples + 1], Prediction)


class TestAddSampleReturnValue:
    """`add_sample` の戻り値が常に `predictions[-1]` と同一であること。"""

    def test_return_value_matches_predictions_last(
        self, default_config: PredictionConfig
    ) -> None:
        tracker = _new_tracker(default_config)

        for sample in _samples(5):
            outcome = tracker.add_sample(sample)
            assert outcome is tracker.predictions[-1]


class TestSequenceLengthInvariant:
    """`len(predictions) == len(samples)` が常に成立する(要件 5.2)。"""

    def test_lengths_match_after_each_addition(
        self, default_config: PredictionConfig
    ) -> None:
        tracker = _new_tracker(default_config)

        for i, sample in enumerate(_samples(6), start=1):
            tracker.add_sample(sample)
            assert len(tracker.predictions) == len(tracker.samples) == i


class TestSampleCountMonotonicity:
    """各 `Prediction` の `sample_count` が系列上で狭義単調増加する(要件 4.3)。"""

    def test_sample_count_strictly_increasing_across_valid_predictions(
        self, default_config: PredictionConfig
    ) -> None:
        tracker = _new_tracker(default_config)

        for sample in _samples(6):
            tracker.add_sample(sample)

        valid_counts = [
            outcome.sample_count
            for outcome in tracker.predictions
            if isinstance(outcome, Prediction)
        ]
        assert len(valid_counts) >= 2
        assert valid_counts == sorted(valid_counts)
        assert len(set(valid_counts)) == len(valid_counts)

    def test_sample_count_strictly_increasing_across_all_outcomes(
        self, default_config: PredictionConfig
    ) -> None:
        """無効な予測も含め、`sample_count` は系列全体で狭義単調増加する。"""
        tracker = _new_tracker(default_config)

        for sample in _samples(6):
            tracker.add_sample(sample)

        all_counts = [outcome.sample_count for outcome in tracker.predictions]
        assert all_counts == list(range(1, 7))


class TestFirstValidProperty:
    """`first_valid` プロパティの契約。"""

    def test_first_valid_returns_none_before_any_valid_prediction(
        self, default_config: PredictionConfig
    ) -> None:
        tracker = _new_tracker(default_config)
        tracker.add_sample(_samples(1)[0])

        assert tracker.first_valid is None

    def test_first_valid_returns_first_prediction_in_sequence(
        self, default_config: PredictionConfig
    ) -> None:
        tracker = _new_tracker(default_config)
        samples = _samples(5)

        outcomes = [tracker.add_sample(sample) for sample in samples]
        first_prediction = next(o for o in outcomes if isinstance(o, Prediction))

        assert tracker.first_valid is first_prediction

    def test_first_valid_does_not_change_once_set(
        self, default_config: PredictionConfig
    ) -> None:
        """最初の有効予測が確定した後にサンプルを追加しても、`first_valid` は
        その最初の `Prediction` を指し続ける(以降の予測で上書きされない)。
        """
        tracker = _new_tracker(default_config)
        samples = _samples(5)

        for sample in samples[:3]:
            tracker.add_sample(sample)
        first_after_third = tracker.first_valid
        assert first_after_third is not None

        for sample in samples[3:]:
            tracker.add_sample(sample)

        assert tracker.first_valid is first_after_third


class TestLatestProperty:
    """`latest` プロパティの契約。"""

    def test_latest_is_none_before_any_sample(
        self, default_config: PredictionConfig
    ) -> None:
        tracker = _new_tracker(default_config)

        assert tracker.latest is None

    def test_latest_returns_last_outcome_valid_or_invalid(
        self, default_config: PredictionConfig
    ) -> None:
        tracker = _new_tracker(default_config)
        samples = _samples(5)

        for sample in samples[:2]:
            tracker.add_sample(sample)
        assert isinstance(tracker.latest, InvalidPrediction)

        for sample in samples[2:]:
            last_outcome = tracker.add_sample(sample)
            assert tracker.latest is last_outcome


class TestSamplesAndPredictionsAreImmutableTuples:
    """`samples` / `predictions` が実際に `tuple` であり、外部から内部状態を
    変更できないこと(design.md Implementation Notes「Integration」)。
    """

    def test_samples_is_tuple(self, default_config: PredictionConfig) -> None:
        tracker = _new_tracker(default_config)
        tracker.add_sample(_samples(1)[0])

        assert isinstance(tracker.samples, tuple)

    def test_predictions_is_tuple(self, default_config: PredictionConfig) -> None:
        tracker = _new_tracker(default_config)
        tracker.add_sample(_samples(1)[0])

        assert isinstance(tracker.predictions, tuple)

    def test_mutating_returned_samples_tuple_does_not_affect_tracker(
        self, default_config: PredictionConfig
    ) -> None:
        tracker = _new_tracker(default_config)
        tracker.add_sample(_samples(1)[0])

        returned = tracker.samples
        with pytest.raises((AttributeError, TypeError)):
            returned.append(_samples(2)[1])  # type: ignore[attr-defined]

        assert len(tracker.samples) == 1

    def test_mutating_returned_predictions_tuple_does_not_affect_tracker(
        self, default_config: PredictionConfig
    ) -> None:
        tracker = _new_tracker(default_config)
        tracker.add_sample(_samples(1)[0])

        returned = tracker.predictions
        with pytest.raises((AttributeError, TypeError)):
            returned.append(returned[0])  # type: ignore[attr-defined]

        assert len(tracker.predictions) == 1

    def test_repeated_property_access_reflects_independent_snapshots(
        self, default_config: PredictionConfig
    ) -> None:
        """`samples` プロパティが同一の list を使い回して公開しているのでは
        なく、呼び出しごとに独立したスナップショットを返すことを確認する
        (内部リストへの参照そのものを漏らしていないことの傍証)。
        """
        tracker = _new_tracker(default_config)
        tracker.add_sample(_samples(1)[0])

        first_call = tracker.samples
        tracker.add_sample(_samples(2)[1])
        second_call = tracker.samples

        assert len(first_call) == 1
        assert len(second_call) == 2


class TestToRecord:
    """`to_record()` がトラッカーの状態と一致する `ThrowRecord` を返す。"""

    def test_to_record_reflects_tracker_state(
        self, default_config: PredictionConfig
    ) -> None:
        tracker = _new_tracker(default_config, record_id="throw-to-record")
        for sample in _samples(5):
            tracker.add_sample(sample)

        record = tracker.to_record()

        assert isinstance(record, ThrowRecord)
        assert record.record_id == "throw-to-record"
        assert record.source == SourceKind.SIMULATED
        assert record.config == default_config
        assert record.samples == tracker.samples
        assert record.predictions == tracker.predictions

    def test_to_record_snapshot_is_not_affected_by_later_additions(
        self, default_config: PredictionConfig
    ) -> None:
        tracker = _new_tracker(default_config, record_id="throw-to-record-snapshot")
        samples = _samples(5)

        for sample in samples[:2]:
            tracker.add_sample(sample)
        snapshot = tracker.to_record()

        for sample in samples[2:]:
            tracker.add_sample(sample)

        assert len(snapshot.samples) == 2
        assert len(snapshot.predictions) == 2
        assert len(tracker.samples) == 5


class TestNoActuationInterface:
    """駆動制御・目標座標の送信を行うメソッドを持たないこと(要件 5.4)。

    クラスの公開インターフェースを確認する程度に留める(過度に作り込まない)。
    """

    def test_public_interface_is_limited_to_documented_members(self) -> None:
        expected_public_members = {
            "add_sample",
            "samples",
            "predictions",
            "latest",
            "first_valid",
            "to_record",
        }
        actual_public_members = {
            name
            for name in dir(ThrowPredictionTracker)
            if not name.startswith("_")
        }

        assert actual_public_members == expected_public_members


class TestRecordIdValidation:
    """`record_id` が空文字列の場合の挙動(本タスクで決めた仕様)。

    design.md は `record_id` は空文字列でないことを Precondition として
    明記するのみで、違反時に例外を送出するかどうかは規定していない。
    本実装は「呼び出し方の誤り」カテゴリ(design.md errors.py の3分類)
    として即座に `ValueError` を送出する方針を採った(モジュール
    docstring 参照)。新しい例外クラスは新設していない。
    """

    def test_empty_record_id_raises_value_error(
        self, default_config: PredictionConfig
    ) -> None:
        with pytest.raises(ValueError):
            ThrowPredictionTracker(
                record_id="", source=SourceKind.SIMULATED, config=default_config
            )

    def test_non_empty_record_id_is_accepted(
        self, default_config: PredictionConfig
    ) -> None:
        tracker = ThrowPredictionTracker(
            record_id="throw-1", source=SourceKind.SIMULATED, config=default_config
        )
        assert tracker.to_record().record_id == "throw-1"


class TestConfigDefaultsToPredictionConfig:
    """`config` 省略時は既定の `PredictionConfig()` を用いる(Precondition)。"""

    def test_default_config_used_when_omitted(self) -> None:
        tracker = ThrowPredictionTracker(
            record_id="throw-default-config", source=SourceKind.SIMULATED
        )

        assert tracker.to_record().config == PredictionConfig()
