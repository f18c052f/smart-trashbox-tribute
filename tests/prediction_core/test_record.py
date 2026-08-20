"""ThrowRecord の dict / JSON 往復の検証（タスク 4.1 / 4.2、要件 9.1 / 9.2 / 9.3 / 9.6）。

design.md「ThrowRecordCodec」が定める `to_dict` / `from_dict` /
`to_json` / `from_json` を対象にする。`replay` / `predictions_equivalent` は
タスク 4.3 の担当であり、本ファイルでは検証しない。

**非有限値の等価性検証について**（重要）: Python の `==` は `NaN != NaN`
であるため、dataclass の自動生成 `__eq__` を用いた全体比較
（`from_dict(to_dict(r)) == r`）は非有限値を含むレコードでは必ず失敗する。
そのため:

- 「dict 化して復元したレコードが元と等価になる」という本タスクの主要な
  観測可能完了状態は、**有限値のみのレコード**で検証する。
- 「非有限値を含む場合も dict 化は成功させる」という完了状態は、
  `to_dict()` が例外を出さないこと、および得られた dict の該当フィールドが
  `math.isnan` / `math.isinf` で期待どおりであることを個別に確認する形で
  検証する（全体比較はしない）。
"""

from __future__ import annotations

import json
import math

import pytest

from prediction_core.config import PredictionConfig
from prediction_core.errors import RecordSchemaError, RecordSerializationError
from prediction_core.record import SCHEMA_VERSION, ThrowRecord
from prediction_core.types import (
    InvalidPrediction,
    InvalidReason,
    Prediction,
    Sample,
    SourceKind,
    TrajectoryParameters,
)


def _trajectory(t_ref_ms: float = 100.0) -> TrajectoryParameters:
    return TrajectoryParameters(
        t_ref_ms=t_ref_ms,
        x0_mm=10.0,
        y0_mm=20.0,
        z0_mm=500.0,
        estimated_vx_mm_s=250.0,
        estimated_vy_mm_s=-50.0,
        estimated_vz_mm_s=1200.0,
        gravity_mm_s2=9806.65,
    )


def _prediction(config: PredictionConfig, *, sample_count: int = 3) -> Prediction:
    return Prediction(
        predicted_hit_x_mm=123.4,
        predicted_hit_y_mm=-56.7,
        predicted_hit_time_ms=250.0,
        remaining_time_ms=150.0,
        estimated_vx_mm_s=250.0,
        estimated_vy_mm_s=-50.0,
        estimated_vz_mm_s=1200.0,
        residual=0.5,
        trajectory=_trajectory(),
        sample_count=sample_count,
        based_on_time_ms=100.0,
        elapsed_ms=1.25,
        config=config,
    )


def _invalid_prediction(
    config: PredictionConfig, *, sample_count: int = 1
) -> InvalidPrediction:
    return InvalidPrediction(
        reason=InvalidReason.INSUFFICIENT_SAMPLES,
        detail=f"観測サンプル数が不足しています: {sample_count}件。",
        sample_count=sample_count,
        based_on_time_ms=0.0,
        elapsed_ms=0.75,
        config=config,
    )


def _samples() -> tuple[Sample, ...]:
    return (
        Sample(t_ms=0.0, x_mm=0.0, y_mm=0.0, z_mm=500.0),
        Sample(t_ms=50.0, x_mm=12.5, y_mm=-2.5, z_mm=480.0),
        Sample(t_ms=100.0, x_mm=25.0, y_mm=-5.0, z_mm=440.0),
    )


class TestRoundTripFiniteValues:
    """主要な観測可能完了状態: 有限値のみのレコードで dict 往復が等価になる。"""

    def test_round_trip_with_prediction_and_invalid_prediction_mixed(
        self, default_config: PredictionConfig
    ) -> None:
        """`Prediction` と `InvalidPrediction` が混在する複数件の系列で往復が等価。"""
        record = ThrowRecord(
            record_id="throw-0001",
            source=SourceKind.LIVE,
            config=default_config,
            samples=_samples(),
            predictions=(
                _invalid_prediction(default_config, sample_count=1),
                _invalid_prediction(default_config, sample_count=2),
                _prediction(default_config, sample_count=3),
            ),
        )

        restored = ThrowRecord.from_dict(record.to_dict())

        assert restored == record

    def test_round_trip_with_recorded_source_and_extra_values(
        self, default_config: PredictionConfig
    ) -> None:
        """`source=recorded`、`extra` に値がある場合の往復。"""
        record = ThrowRecord(
            record_id="throw-recorded-1",
            source=SourceKind.RECORDED,
            config=default_config,
            samples=_samples(),
            predictions=(_prediction(default_config),),
            extra={"note": "hardware-replay", "batch": 7},
        )

        restored = ThrowRecord.from_dict(record.to_dict())

        assert restored == record
        assert restored.extra == {"note": "hardware-replay", "batch": 7}

    def test_round_trip_with_simulated_source_and_empty_extra(
        self, default_config: PredictionConfig
    ) -> None:
        """`source=simulated`、`extra` が空 dict の場合の往復。"""
        record = ThrowRecord(
            record_id="throw-sim-1",
            source=SourceKind.SIMULATED,
            config=default_config,
            samples=_samples(),
            predictions=(_prediction(default_config),),
        )

        restored = ThrowRecord.from_dict(record.to_dict())

        assert restored == record
        assert restored.extra == {}

    def test_round_trip_preserves_non_default_config_values(self) -> None:
        """既定値と異なる `PredictionConfig`（ネスト含む）でも往復が等価。"""
        config = PredictionConfig(
            gravity_mm_s2=9800.0,
            min_samples=4,
            measure_elapsed=False,
            time_degeneracy_rel_tol=1e-6,
        )
        record = ThrowRecord(
            record_id="throw-custom-config",
            source=SourceKind.LIVE,
            config=config,
            samples=_samples(),
            predictions=(_prediction(config), _invalid_prediction(config)),
        )

        restored = ThrowRecord.from_dict(record.to_dict())

        assert restored == record
        assert restored.config == config
        assert restored.predictions[0].config == config
        assert restored.predictions[1].config == config


class TestSamplesAndPredictionsAreTuples:
    def test_samples_and_predictions_are_tuples_after_from_dict(
        self, default_config: PredictionConfig
    ) -> None:
        """`from_dict` に list が渡されても、内部表現はタプルになる（不変性）。"""
        record = ThrowRecord(
            record_id="throw-tuple-check",
            source=SourceKind.LIVE,
            config=default_config,
            samples=_samples(),
            predictions=(_prediction(default_config),),
        )
        data = record.to_dict()
        # to_dict は list を返す契約であることも併せて確認する。
        assert isinstance(data["samples"], list)
        assert isinstance(data["predictions"], list)

        restored = ThrowRecord.from_dict(data)

        assert isinstance(restored.samples, tuple)
        assert isinstance(restored.predictions, tuple)


class TestToDictTopLevelShape:
    def test_to_dict_has_all_required_top_level_keys(
        self, default_config: PredictionConfig
    ) -> None:
        record = ThrowRecord(
            record_id="throw-shape",
            source=SourceKind.LIVE,
            config=default_config,
            samples=_samples(),
            predictions=(_prediction(default_config),),
        )

        data = record.to_dict()

        for key in (
            "schema_version",
            "record_id",
            "source",
            "config",
            "samples",
            "predictions",
            "extra",
        ):
            assert key in data

    def test_to_dict_source_is_string_value(
        self, default_config: PredictionConfig
    ) -> None:
        record = ThrowRecord(
            record_id="throw-source-str",
            source=SourceKind.RECORDED,
            config=default_config,
            samples=_samples(),
            predictions=(),
        )

        data = record.to_dict()

        assert data["source"] == "recorded"
        # `SourceKind` は `StrEnum`（= `str` のサブクラス）であるため、
        # 単なる `isinstance(..., str)` では「`.value` へ変換せず enum
        # メンバーのまま格納した」欠陥を見逃す。plain `str` への変換を
        # 厳密に検証するため型そのものを比較する。
        assert type(data["source"]) is str

    def test_invalid_prediction_reason_is_plain_string_value(
        self, default_config: PredictionConfig
    ) -> None:
        record = ThrowRecord(
            record_id="throw-reason-str",
            source=SourceKind.LIVE,
            config=default_config,
            samples=(),
            predictions=(_invalid_prediction(default_config),),
        )

        reason_value = record.to_dict()["predictions"][0]["reason"]

        assert reason_value == "insufficient_samples"
        # `InvalidReason` も `StrEnum` であるため、同様に plain `str` への
        # 変換を型そのもので検証する。
        assert type(reason_value) is str

    def test_to_dict_default_schema_version(
        self, default_config: PredictionConfig
    ) -> None:
        record = ThrowRecord(
            record_id="throw-schema",
            source=SourceKind.LIVE,
            config=default_config,
            samples=(),
            predictions=(),
        )

        assert record.to_dict()["schema_version"] == SCHEMA_VERSION

    def test_prediction_element_has_kind_prediction(
        self, default_config: PredictionConfig
    ) -> None:
        record = ThrowRecord(
            record_id="throw-kind-prediction",
            source=SourceKind.LIVE,
            config=default_config,
            samples=_samples(),
            predictions=(_prediction(default_config),),
        )

        assert record.to_dict()["predictions"][0]["kind"] == "prediction"

    def test_invalid_prediction_element_has_kind_invalid(
        self, default_config: PredictionConfig
    ) -> None:
        record = ThrowRecord(
            record_id="throw-kind-invalid",
            source=SourceKind.LIVE,
            config=default_config,
            samples=(),
            predictions=(_invalid_prediction(default_config),),
        )

        assert record.to_dict()["predictions"][0]["kind"] == "invalid"

    def test_invalid_prediction_dict_has_exactly_the_expected_keys(
        self, default_config: PredictionConfig
    ) -> None:
        """`InvalidPrediction` の dict 形に禁止フィールドが混入しないことを固定する。

        `InvalidPrediction`（`types.py`）は「無効な予測から落下地点を読み出す
        経路が存在しないことで、誤った目標座標が駆動系へ流れる事態を型の
        段階で防ぐ」（要件6.7）契約を持つ。ThrowRecord を実際に読む下流
        （`sensing-foundation` / `m1-prediction-validation`）は Python の型
        ではなく dict/JSON 形を読むため、この保証はコーデック境界の
        キー集合でも検証しなければならない。部分一致（余分なキーを見逃す
        `in` 判定の羅列）ではなく完全一致にすることで、`predicted_hit_x_mm`
        などの禁止フィールドの**混入**と、`reason` などの必須フィールドの
        **欠落**の両方を検出する。
        """
        record = ThrowRecord(
            record_id="throw-invalid-keys",
            source=SourceKind.LIVE,
            config=default_config,
            samples=(),
            predictions=(_invalid_prediction(default_config),),
        )

        invalid_dict = record.to_dict()["predictions"][0]

        assert set(invalid_dict) == {
            "kind",
            "reason",
            "detail",
            "sample_count",
            "based_on_time_ms",
            "elapsed_ms",
            "config",
        }
        # 落下地点・落下時刻・残り時間・軌道・残差は `InvalidPrediction` に
        # 存在しないフィールドであり、dict 形にも現れてはならない。
        for forbidden_key in (
            "predicted_hit_x_mm",
            "predicted_hit_y_mm",
            "predicted_hit_time_ms",
            "remaining_time_ms",
            "estimated_vx_mm_s",
            "estimated_vy_mm_s",
            "estimated_vz_mm_s",
            "residual",
            "trajectory",
        ):
            assert forbidden_key not in invalid_dict


class TestNonFiniteValuesDoNotRaiseOnToDict:
    """非有限値を含んでいても `to_dict` は例外にせず、メモリ上の忠実性を保つ。"""

    def test_prediction_with_nan_residual_round_trips_in_dict_form(
        self, default_config: PredictionConfig
    ) -> None:
        prediction = Prediction(
            predicted_hit_x_mm=1.0,
            predicted_hit_y_mm=2.0,
            predicted_hit_time_ms=3.0,
            remaining_time_ms=4.0,
            estimated_vx_mm_s=5.0,
            estimated_vy_mm_s=6.0,
            estimated_vz_mm_s=7.0,
            residual=float("nan"),
            trajectory=_trajectory(),
            sample_count=3,
            based_on_time_ms=0.0,
            elapsed_ms=float("inf"),
            config=default_config,
        )
        record = ThrowRecord(
            record_id="throw-nonfinite",
            source=SourceKind.LIVE,
            config=default_config,
            samples=_samples(),
            predictions=(prediction,),
        )

        data = record.to_dict()  # 例外を出さないこと自体が検証対象

        residual_value = data["predictions"][0]["residual"]
        elapsed_value = data["predictions"][0]["elapsed_ms"]
        assert math.isnan(residual_value)
        assert math.isinf(elapsed_value) and elapsed_value > 0

    def test_invalid_prediction_with_infinite_based_on_time_round_trips_in_dict_form(
        self, default_config: PredictionConfig
    ) -> None:
        invalid = InvalidPrediction(
            reason=InvalidReason.NON_FINITE_VALUE,
            detail="非有限値テスト",
            sample_count=2,
            based_on_time_ms=float("-inf"),
            elapsed_ms=None,
            config=default_config,
        )
        record = ThrowRecord(
            record_id="throw-nonfinite-invalid",
            source=SourceKind.LIVE,
            config=default_config,
            samples=(),
            predictions=(invalid,),
        )

        data = record.to_dict()  # 例外を出さないこと自体が検証対象

        based_on_time_value = data["predictions"][0]["based_on_time_ms"]
        assert math.isinf(based_on_time_value) and based_on_time_value < 0
        assert data["predictions"][0]["elapsed_ms"] is None

    def test_sample_with_nan_position_round_trips_in_dict_form(
        self, default_config: PredictionConfig
    ) -> None:
        samples = (
            Sample(t_ms=0.0, x_mm=float("nan"), y_mm=0.0, z_mm=0.0),
        )
        record = ThrowRecord(
            record_id="throw-nonfinite-sample",
            source=SourceKind.LIVE,
            config=default_config,
            samples=samples,
            predictions=(),
        )

        data = record.to_dict()  # 例外を出さないこと自体が検証対象

        assert math.isnan(data["samples"][0]["x_mm"])


class TestFromDictRejectsUnknownKind:
    def test_from_dict_raises_record_schema_error_for_unknown_kind(
        self, default_config: PredictionConfig
    ) -> None:
        data = {
            "schema_version": SCHEMA_VERSION,
            "record_id": "throw-bad-kind",
            "source": "live",
            "config": {
                "gravity_mm_s2": default_config.gravity_mm_s2,
                "min_samples": default_config.min_samples,
                "measure_elapsed": default_config.measure_elapsed,
                "time_degeneracy_rel_tol": default_config.time_degeneracy_rel_tol,
            },
            "samples": [],
            "predictions": [{"kind": "not-a-real-kind"}],
            "extra": {},
        }

        with pytest.raises(RecordSchemaError):
            ThrowRecord.from_dict(data)


class TestFromDictDoesNotAliasExtra:
    def test_mutating_input_extra_after_from_dict_does_not_affect_record(
        self, default_config: PredictionConfig
    ) -> None:
        """`from_dict` は入力 dict の `extra` サブ辞書をコピーして保持する。

        `ThrowRecord` は `frozen=True` で不変性を契約している。`extra` が
        呼び出し元の dict への参照のままだと、呼び出し元がその後に元の
        dict を変更した際、`ThrowRecord.extra` の中身が意図せず変化して
        しまい不変性の契約が事実上破られる。`from_dict` 呼び出し**後**に
        入力側の `extra` サブ辞書を変更し、生成済みの `ThrowRecord.extra`
        が影響を受けないことを固定する。
        """
        input_extra = {"note": "original"}
        data = {
            "schema_version": SCHEMA_VERSION,
            "record_id": "throw-extra-alias-check",
            "source": "live",
            "config": {
                "gravity_mm_s2": default_config.gravity_mm_s2,
                "min_samples": default_config.min_samples,
                "measure_elapsed": default_config.measure_elapsed,
                "time_degeneracy_rel_tol": default_config.time_degeneracy_rel_tol,
            },
            "samples": [],
            "predictions": [],
            "extra": input_extra,
        }

        record = ThrowRecord.from_dict(data)

        # 復元後に呼び出し元の入力 dict を変更(mutate)する。
        input_extra["note"] = "mutated-after-from-dict"
        input_extra["injected"] = "should-not-leak-into-record"

        assert record.extra == {"note": "original"}


class TestJsonRoundTripFiniteValues:
    """タスク 4.2 の主要な観測可能完了状態: 有限値のみのレコードで JSON 往復が等価。"""

    def test_from_json_of_to_json_equals_original_record(
        self, default_config: PredictionConfig
    ) -> None:
        record = ThrowRecord(
            record_id="throw-json-roundtrip",
            source=SourceKind.LIVE,
            config=default_config,
            samples=_samples(),
            predictions=(
                _invalid_prediction(default_config, sample_count=1),
                _prediction(default_config, sample_count=2),
            ),
            extra={"note": "json-roundtrip", "batch": 3},
        )

        restored = ThrowRecord.from_json(record.to_json())

        assert restored == record

    def test_to_json_returns_str(self, default_config: PredictionConfig) -> None:
        record = ThrowRecord(
            record_id="throw-json-type",
            source=SourceKind.SIMULATED,
            config=default_config,
            samples=(),
            predictions=(),
        )

        assert isinstance(record.to_json(), str)


class TestToJsonIndent:
    def test_to_json_with_indent_produces_multiline_output(
        self, default_config: PredictionConfig
    ) -> None:
        record = ThrowRecord(
            record_id="throw-json-indent",
            source=SourceKind.LIVE,
            config=default_config,
            samples=_samples(),
            predictions=(_prediction(default_config),),
        )

        compact = record.to_json()
        indented = record.to_json(indent=2)

        assert "\n" not in compact
        assert "\n" in indented
        # インデント付きでも同じ内容を表現している(往復すれば元と等価)。
        assert ThrowRecord.from_json(indented) == record


class TestToJsonRejectsNonFiniteValues:
    """非有限値を含むレコードは `to_json` で `RecordSerializationError`。

    `to_dict()` はメモリ上の忠実性を保つため引き続き成功すること
    (タスク 4.1 の既存契約と矛盾しないこと)も併せて確認する。
    """

    def test_to_json_raises_for_nan_residual(
        self, default_config: PredictionConfig
    ) -> None:
        prediction = Prediction(
            predicted_hit_x_mm=1.0,
            predicted_hit_y_mm=2.0,
            predicted_hit_time_ms=3.0,
            remaining_time_ms=4.0,
            estimated_vx_mm_s=5.0,
            estimated_vy_mm_s=6.0,
            estimated_vz_mm_s=7.0,
            residual=float("nan"),
            trajectory=_trajectory(),
            sample_count=3,
            based_on_time_ms=0.0,
            elapsed_ms=1.0,
            config=default_config,
        )
        record = ThrowRecord(
            record_id="throw-json-nonfinite",
            source=SourceKind.LIVE,
            config=default_config,
            samples=_samples(),
            predictions=(prediction,),
        )

        # to_dict() 自体は成功する(既存契約)。
        data = record.to_dict()
        assert math.isnan(data["predictions"][0]["residual"])

        with pytest.raises(RecordSerializationError):
            record.to_json()

    def test_to_json_raises_for_infinite_elapsed_ms(
        self, default_config: PredictionConfig
    ) -> None:
        invalid = InvalidPrediction(
            reason=InvalidReason.NON_FINITE_VALUE,
            detail="非有限値テスト",
            sample_count=1,
            based_on_time_ms=0.0,
            elapsed_ms=float("inf"),
            config=default_config,
        )
        record = ThrowRecord(
            record_id="throw-json-nonfinite-invalid",
            source=SourceKind.LIVE,
            config=default_config,
            samples=(),
            predictions=(invalid,),
        )

        with pytest.raises(RecordSerializationError):
            record.to_json()

    def test_to_json_raises_for_nan_sample_position(
        self, default_config: PredictionConfig
    ) -> None:
        record = ThrowRecord(
            record_id="throw-json-nonfinite-sample",
            source=SourceKind.LIVE,
            config=default_config,
            samples=(Sample(t_ms=0.0, x_mm=float("nan"), y_mm=0.0, z_mm=0.0),),
            predictions=(),
        )

        with pytest.raises(RecordSerializationError):
            record.to_json()


class TestToJsonOutputHasNoNonFiniteLiterals:
    def test_to_json_output_does_not_contain_nan_or_infinity_literals(
        self, default_config: PredictionConfig
    ) -> None:
        record = ThrowRecord(
            record_id="throw-json-literal-check",
            source=SourceKind.LIVE,
            config=default_config,
            samples=_samples(),
            predictions=(_prediction(default_config), _invalid_prediction(default_config)),
        )

        text = record.to_json()

        assert "NaN" not in text
        assert "Infinity" not in text
        # 生成された文字列は標準ライブラリで問題なくパースできる。
        json.loads(text)


class TestFromDictMissingRequiredKeys:
    """必須キーの欠落は `RecordSchemaError` になり、欠落キー名がメッセージに含まれる。"""

    def _valid_data(self, default_config: PredictionConfig) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "record_id": "throw-missing-key",
            "source": "live",
            "config": {
                "gravity_mm_s2": default_config.gravity_mm_s2,
                "min_samples": default_config.min_samples,
                "measure_elapsed": default_config.measure_elapsed,
                "time_degeneracy_rel_tol": default_config.time_degeneracy_rel_tol,
            },
            "samples": [],
            "predictions": [],
            "extra": {},
        }

    @pytest.mark.parametrize(
        "missing_key",
        ["record_id", "source", "config", "samples", "predictions"],
    )
    def test_missing_required_key_raises_record_schema_error_with_key_name(
        self, default_config: PredictionConfig, missing_key: str
    ) -> None:
        data = self._valid_data(default_config)
        del data[missing_key]

        with pytest.raises(RecordSchemaError, match=missing_key):
            ThrowRecord.from_dict(data)

    def test_missing_required_key_raises_via_from_json_too(
        self, default_config: PredictionConfig
    ) -> None:
        data = self._valid_data(default_config)
        del data["config"]
        text = json.dumps(data)

        with pytest.raises(RecordSchemaError, match="config"):
            ThrowRecord.from_json(text)


class TestFromDictTypeMismatches:
    """明らかな型不一致は `RecordSchemaError` になり、問題のキー名がメッセージに含まれる。"""

    def _valid_data(self, default_config: PredictionConfig) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "record_id": "throw-type-mismatch",
            "source": "live",
            "config": {
                "gravity_mm_s2": default_config.gravity_mm_s2,
                "min_samples": default_config.min_samples,
                "measure_elapsed": default_config.measure_elapsed,
                "time_degeneracy_rel_tol": default_config.time_degeneracy_rel_tol,
            },
            "samples": [],
            "predictions": [],
            "extra": {},
        }

    def test_samples_as_string_raises_record_schema_error_with_key_name(
        self, default_config: PredictionConfig
    ) -> None:
        data = self._valid_data(default_config)
        data["samples"] = "not-a-list"

        with pytest.raises(RecordSchemaError, match="samples"):
            ThrowRecord.from_dict(data)

    def test_predictions_as_number_raises_record_schema_error_with_key_name(
        self, default_config: PredictionConfig
    ) -> None:
        data = self._valid_data(default_config)
        data["predictions"] = 42

        with pytest.raises(RecordSchemaError, match="predictions"):
            ThrowRecord.from_dict(data)

    def test_source_as_invalid_string_raises_record_schema_error_with_key_name(
        self, default_config: PredictionConfig
    ) -> None:
        data = self._valid_data(default_config)
        data["source"] = "not-a-real-source"

        with pytest.raises(RecordSchemaError, match="source"):
            ThrowRecord.from_dict(data)

    def test_config_as_string_raises_record_schema_error_with_key_name(
        self, default_config: PredictionConfig
    ) -> None:
        data = self._valid_data(default_config)
        data["config"] = "not-a-dict"

        with pytest.raises(RecordSchemaError, match="config"):
            ThrowRecord.from_dict(data)


class TestUnknownTopLevelKeysPreservedInExtra:
    """未知のトップレベルキーは情報を失わず `extra` へ退避され、往復後も取得できる。"""

    def _valid_data(self, default_config: PredictionConfig) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "record_id": "throw-unknown-key",
            "source": "live",
            "config": {
                "gravity_mm_s2": default_config.gravity_mm_s2,
                "min_samples": default_config.min_samples,
                "measure_elapsed": default_config.measure_elapsed,
                "time_degeneracy_rel_tol": default_config.time_degeneracy_rel_tol,
            },
            "samples": [],
            "predictions": [],
            "extra": {},
        }

    def test_unknown_top_level_key_is_moved_into_extra(
        self, default_config: PredictionConfig
    ) -> None:
        data = self._valid_data(default_config)
        data["custom_field"] = 123

        record = ThrowRecord.from_dict(data)

        assert record.extra == {"custom_field": 123}

    def test_unknown_top_level_key_survives_json_round_trip(
        self, default_config: PredictionConfig
    ) -> None:
        data = self._valid_data(default_config)
        data["custom_field"] = 123
        text = json.dumps(data)

        record = ThrowRecord.from_json(text)

        assert record.extra == {"custom_field": 123}
        # to_dict() で再出力しても情報が失われない(extra 経由で往復する)。
        assert record.to_dict()["extra"] == {"custom_field": 123}
        # to_json() で再度往復しても保持される。
        round_tripped = ThrowRecord.from_json(record.to_json())
        assert round_tripped.extra == {"custom_field": 123}

    def test_unknown_top_level_key_merges_with_explicit_extra(
        self, default_config: PredictionConfig
    ) -> None:
        data = self._valid_data(default_config)
        data["extra"] = {"note": "explicit"}
        data["custom_field"] = 123

        record = ThrowRecord.from_dict(data)

        assert record.extra == {"note": "explicit", "custom_field": 123}
