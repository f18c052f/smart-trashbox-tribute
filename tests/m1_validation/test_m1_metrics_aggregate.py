"""投擲群への集計の検証（タスク 4.6、要件 2.5, 5.9, 5.10）。

観測可能な完了状態（tasks.md 4.6）を固定する:

- **2つのキャリブレーション識別子が混在する入力が、識別子ごとに分かれて集計される**
- **試行数下限未達の集計に暫定の印が付く**（集計自体は返る）

あわせて design.md「ThrowAggregator」とタスク箇条が定める点も固定する:

- 各項目について**代表値・ばらつき・最小最大・欠測数・試行数**を出す
- **未検証キャリブレーションで得た投擲を、検証済みのものと同じ集計に混ぜない**
- **実機由来の投擲数**を数える（後段の判断が使う）
- 帰属の入力となる**誤差ベクトル群**を、ベクトルのまま束ねる

**参照解は実装の定数から組まない**（tasks.md「Implementation Notes」タスク4.1）。
分位点・IQR・最小最大の期待値は本ファイルのリテラルから手で組んだ値であり、
`Distribution` の他のフィールドや実装の出力を許容差にも期待値にも使わない。
項目キー・暫定理由のラベルも**テスト局所のリテラル**で書き、実装の定数は
`TestLabelInvariants` の独立した不変条件検査にだけ登場させる
（タスク4.5 で見つかった「ラベルを実装の定数と自己比較する」空振りの是正型）。

**検査を2段に分けた理由。** 代数の段（大半のクラス）は `FlightResult` /
`AccuracyResult` / `ConvergenceResult` を**テスト局所のリテラルで直接組む**。
10 個の項目値をすべて違う値にしてあるので、**読み取るフィールドを1つでも
取り違えた実装は違う分布を返す**。合わせ技として `TestRealResultTypes` が
`measure_flight()` / `measure_accuracy()` / `analyze_convergence()` の**本物の
戻り値**を通し、フィールド名の綴りではなく意味の上で正しい値を拾っている
ことを固定する（代数の段はダブルではなく本物の型を使うが、値は手で置くので
上流の算出規則の変化までは覆わない）。

**許容差の根拠。** 分位点はすべて有限桁の四則演算（線形補間）であり、
**厳密に一致するのが正しい**。`EXACT_TOLERANCE`（1e-9）は丸めのためだけの
余裕である。
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Mapping, Sequence

import pytest

from m1_validation.config import ConvergenceConfig, M1Settings, TrialLimits
from m1_validation.errors import M1ConfigError
from m1_validation.layout import ThrowLayout
from m1_validation.metrics.accuracy import (
    AccuracyResult,
    PredictionError,
    measure_accuracy,
)
from m1_validation.metrics.aggregate import (
    ITEM_KEYS,
    MEASUREMENT_ITEM_KEYS,
    PROVISIONAL_INSUFFICIENT_VALID_THROWS,
    PROVISIONAL_NON_LIVE_THROWS,
    Distribution,
    ThrowAggregate,
    ThrowMetrics,
    ThrowRow,
    aggregate,
)
from m1_validation.metrics.convergence import ConvergenceResult, analyze_convergence
from m1_validation.metrics.flight import FlightResult, measure_flight
from m1_validation.metrics.latency import (
    FirstPredictionLatency,
    LatencyResult,
    StageLatency,
)
from m1_validation.types import (
    M1_EXTRA_VERSION,
    Judgement,
    ThrowTruth,
    TruthMethod,
    TruthValue,
)
from prediction_core import (
    InvalidPrediction,
    InvalidReason,
    Prediction,
    PredictionConfig,
    PredictionOutcome,
    Sample,
    SourceKind,
    ThrowRecord,
    TrajectoryParameters,
)

EXACT_TOLERANCE = 1e-9

CONFIG = PredictionConfig()

# --- 項目キーのリテラル（**実装の定数を import して自己比較しない**）--------

KEY_TOTAL_FLIGHT = "total_flight_ms"
KEY_RELEASE_TO_DETECT = "release_to_detect_ms"
KEY_DETECT_TO_FIRST_PREDICTION = "detect_to_first_prediction_ms"
KEY_HIT_ERROR_FIRST = "hit_error_norm_first_mm"
KEY_HIT_ERROR_FINAL = "hit_error_norm_final_mm"
KEY_TIME_ERROR_FIRST = "time_error_first_ms"
KEY_TIME_ERROR_FINAL = "time_error_final_ms"
KEY_AIM_ERROR = "aim_error_mm"
KEY_VALID_SAMPLES = "valid_samples"
KEY_CONVERGED_AT = "converged_at"

ALL_KEYS = (
    KEY_TOTAL_FLIGHT,
    KEY_RELEASE_TO_DETECT,
    KEY_DETECT_TO_FIRST_PREDICTION,
    KEY_HIT_ERROR_FIRST,
    KEY_HIT_ERROR_FINAL,
    KEY_TIME_ERROR_FIRST,
    KEY_TIME_ERROR_FINAL,
    KEY_AIM_ERROR,
    KEY_VALID_SAMPLES,
    KEY_CONVERGED_AT,
)

REASON_INSUFFICIENT = "insufficient_valid_throws"
REASON_NON_LIVE = "non_live_throws"

CAL_A = "cal-aaaa-0001"
CAL_B = "cal-bbbb-0002"

# --- レイアウト（暫定許容窓の期待値はここから手で組む）----------------------

APERTURE_DIAMETER_MM = 200.0
OBJECT_DIAMETER_MM = 65.0


def layout() -> ThrowLayout:
    return ThrowLayout(
        layout_id="layout-aggregate-test",
        release_position_world_mm=(-1700.0, -50.0, 1690.0),
        release_height_mm=1690.0,
        throw_direction_deg=0.0,
        standby_position_world_mm=(1000.0, 1000.0),
        object_diameter_mm=OBJECT_DIAMETER_MM,
        aperture_diameter_mm=APERTURE_DIAMETER_MM,
        camera_position_world_mm=(0.0, -2500.0, 1200.0),
        notes="テスト用の仮値（確定ではない）",
    )


def settings(
    *,
    min_valid_throws: int = 2,
    min_sessions: int = 1,
    require_live_source: bool = False,
) -> M1Settings:
    return M1Settings(
        layout=layout(),
        convergence=ConvergenceConfig(band_mm=50.0),
        trials=TrialLimits(
            min_valid_throws=min_valid_throws,
            min_sessions=min_sessions,
            require_live_source=require_live_source,
        ),
    )


# --- 記録・真値・実測結果を組み立てる小道具 ---------------------------------


def m1_extra(
    *,
    calibration_id: str = CAL_A,
    verified: bool = True,
    failed_reason: str | None = None,
    extra_version: str = M1_EXTRA_VERSION,
) -> dict[str, object]:
    """`runner.py` が書く `extra["m1"]` のうち、集計が読む部分だけ。"""
    return {
        "m1_extra_version": extra_version,
        "calibration": {
            "calibration_id": calibration_id,
            "verification_state": "passed" if verified else "not_verified",
            "verified": verified,
        },
        "verified": verified,
        "failed_reason": failed_reason,
    }


def record(
    record_id: str,
    *,
    calibration_id: str = CAL_A,
    verified: bool = True,
    source: SourceKind = SourceKind.SIMULATED,
    session_id: str | None = "session-0001",
    failed_reason: str | None = None,
    sample_count: int = 4,
    predictions: Sequence[PredictionOutcome] = (),
    extra_version: str = M1_EXTRA_VERSION,
    m1_payload: object | None = None,
) -> ThrowRecord:
    extra: dict[str, object] = {}
    if m1_payload is not None:
        extra["m1"] = m1_payload
    else:
        extra["m1"] = m1_extra(
            calibration_id=calibration_id,
            verified=verified,
            failed_reason=failed_reason,
            extra_version=extra_version,
        )
    if session_id is not None:
        extra["sensing"] = {
            "session_id": session_id,
            "frame_index_from": 0,
            "frame_index_to": 10,
        }
    return ThrowRecord(
        record_id=record_id,
        source=source,
        config=CONFIG,
        samples=tuple(
            Sample(
                t_ms=5100.0 + 20.0 * index,
                x_mm=-1700.0 + 30.0 * index,
                y_mm=-50.0,
                z_mm=1690.0 - 5.0 * index,
            )
            for index in range(sample_count)
        ),
        predictions=tuple(predictions),
        extra=extra,
    )


def truth_value(
    value: float | tuple[float, float, float] | None,
    method: TruthMethod,
    *,
    uncertainty_mm: float | None = None,
    uncertainty_ms: float | None = None,
) -> TruthValue:
    return TruthValue(
        value=value,
        method=method,
        uncertainty_mm=uncertainty_mm,
        uncertainty_ms=uncertainty_ms,
        source="テスト用の真値（測り方の記述）",
    )


def throw_truth(
    record_id: str, *, impact_point_missing: bool = False
) -> ThrowTruth:
    point = (
        truth_value(None, TruthMethod.MISSING)
        if impact_point_missing
        else truth_value((1050.0, 1120.0, 12.0), TruthMethod.MEASURED)
    )
    return ThrowTruth(
        record_id=record_id,
        impact_point_world_mm=point,
        impact_time_ms=truth_value(5793.0, TruthMethod.INTERPOLATED),
        release_time_ms=truth_value(5000.0, TruthMethod.EXTRAPOLATED),
        external_mark_delta_ms=None,
    )


def flight_result(
    *,
    total_flight_ms: float | None,
    release_to_detect_ms: float | None,
    aim_error_mm: float | None,
) -> FlightResult:
    return FlightResult(
        total_flight_ms=total_flight_ms,
        total_flight_uncertainty_ms=1.0,
        release_to_detect_ms=release_to_detect_ms,
        release_to_detect_uncertainty_ms=2.0,
        aim_error_mm=aim_error_mm,
        aim_error_uncertainty_mm=3.0,
        methods={},
        emphasis={},
    )


def prediction_error(
    *, sample_count: int, error_mm: tuple[float, float], time_error_ms: float | None
) -> PredictionError:
    return PredictionError(
        sample_count=sample_count,
        based_on_time_ms=5100.0 + 20.0 * sample_count,
        hit_error_mm=error_mm,
        hit_error_norm_mm=math.hypot(*error_mm),
        time_error_ms=time_error_ms,
        residual_mm=3.0,
        remaining_time_ms=400.0,
    )


def accuracy_result(
    *,
    first: PredictionError | None,
    final: PredictionError | None,
) -> AccuracyResult:
    errors = tuple(item for item in (first, final) if item is not None)
    return AccuracyResult(
        errors=errors,
        first_valid=first,
        final=final,
        invalid_counts=(),
    )


def convergence_result(
    *, valid_samples: int, converged_at: int | None, verdict: str
) -> ConvergenceResult:
    return ConvergenceResult(
        valid_samples=valid_samples,
        converged_at=converged_at,
        band_mm=50.0,
        final_error_mm=None,
        judgement=Judgement(
            question="convergence",
            criterion="テスト用の規則説明文（空にできない）",
            verdict=verdict,
            rationale="テスト用",
            evidence={},
            provisional=False,
        ),
    )


def metrics(
    record_id: str,
    *,
    calibration_id: str = CAL_A,
    verified: bool = True,
    source: SourceKind = SourceKind.SIMULATED,
    session_id: str | None = "session-0001",
    failed_reason: str | None = None,
    total_flight_ms: float | None = 800.0,
    release_to_detect_ms: float | None = 100.0,
    aim_error_mm: float | None = 300.0,
    first_error_mm: tuple[float, float] | None = (30.0, 40.0),
    final_error_mm: tuple[float, float] | None = (60.0, 80.0),
    first_time_error_ms: float | None = 11.0,
    final_time_error_ms: float | None = 7.0,
    valid_samples: int = 9,
    converged_at: int | None = 5,
    verdict: str = "converged",
    impact_point_missing: bool = False,
    extra_version: str = M1_EXTRA_VERSION,
    m1_payload: object | None = None,
    truth_record_id: str | None = None,
) -> ThrowMetrics:
    """1投擲ぶんの束（既定値はどの項目も違う値になるようにしてある）。"""
    first = (
        None
        if first_error_mm is None
        else prediction_error(
            sample_count=3, error_mm=first_error_mm, time_error_ms=first_time_error_ms
        )
    )
    final = (
        None
        if final_error_mm is None
        else prediction_error(
            sample_count=9, error_mm=final_error_mm, time_error_ms=final_time_error_ms
        )
    )
    return ThrowMetrics(
        record=record(
            record_id,
            calibration_id=calibration_id,
            verified=verified,
            source=source,
            session_id=session_id,
            failed_reason=failed_reason,
            extra_version=extra_version,
            m1_payload=m1_payload,
        ),
        truth=throw_truth(
            truth_record_id if truth_record_id is not None else record_id,
            impact_point_missing=impact_point_missing,
        ),
        flight=flight_result(
            total_flight_ms=total_flight_ms,
            release_to_detect_ms=release_to_detect_ms,
            aim_error_mm=aim_error_mm,
        ),
        accuracy=accuracy_result(first=first, final=final),
        convergence=convergence_result(
            valid_samples=valid_samples, converged_at=converged_at, verdict=verdict
        ),
    )


def empty_stage() -> StageLatency:
    return StageLatency(
        stage="predict",
        event="update",
        field="end_to_end_ms",
        source="derived",
        count=0,
        p50_ms=None,
        p95_ms=None,
        mean_ms=None,
        min_ms=None,
        max_ms=None,
    )


def latency_for(values: Mapping[str, float | None]) -> LatencyResult:
    """実測項目3 だけを持つ `LatencyResult`（他は集計が読まない）。"""
    return LatencyResult(
        definition="テスト用",
        first_prediction_basis="テスト用",
        stage_note="テスト用",
        stages=(),
        end_to_end=empty_stage(),
        detect_to_first_prediction=tuple(
            FirstPredictionLatency(
                record_id=record_id,
                detection_start_ms=5100.0,
                first_prediction_at_ms=None if value is None else 5100.0 + value,
                first_prediction_sample_count=3,
                detect_to_first_prediction_ms=value,
            )
            for record_id, value in values.items()
        ),
        capture_fps=None,
        process_fps=None,
        cpu_percent_mean=None,
        rss_bytes_max=None,
        frames_dropped=None,
        frames_missing=None,
        unknown_stages=(),
        foreign_prediction_events=0,
        unusable_prediction_events=0,
        log_lines_dropped=0,
        log_lines_skipped=0,
    )


def by_calibration(
    aggregates: Sequence[ThrowAggregate],
) -> dict[tuple[str, bool], ThrowAggregate]:
    return {(item.calibration_id, item.verified): item for item in aggregates}


# ---------------------------------------------------------------------------
# 観測可能な完了状態 (1): 識別子ごとに分かれて集計される（要件 2.5）
# ---------------------------------------------------------------------------


class TestCalibrationGrouping:
    """**混在したまま平均しない**（要件 2.5）。"""

    def test_two_calibration_ids_produce_two_aggregates(self) -> None:
        results = [
            metrics("t-1", calibration_id=CAL_A, aim_error_mm=100.0),
            metrics("t-2", calibration_id=CAL_A, aim_error_mm=200.0),
            metrics("t-3", calibration_id=CAL_B, aim_error_mm=900.0),
            metrics("t-4", calibration_id=CAL_B, aim_error_mm=1100.0),
        ]
        aggregates = aggregate(results, settings=settings())

        assert len(aggregates) == 2
        grouped = by_calibration(aggregates)
        assert set(grouped) == {(CAL_A, True), (CAL_B, True)}

        # 混ぜて平均すると 4 件・中央値 550.0 になる。**分かれていれば
        # 150.0 と 1000.0 である**（どちらもテスト局所のリテラルから手計算）。
        assert grouped[(CAL_A, True)].items[KEY_AIM_ERROR].count == 2
        assert grouped[(CAL_A, True)].items[KEY_AIM_ERROR].median == pytest.approx(
            150.0, abs=EXACT_TOLERANCE
        )
        assert grouped[(CAL_B, True)].items[KEY_AIM_ERROR].median == pytest.approx(
            1000.0, abs=EXACT_TOLERANCE
        )

    def test_unverified_is_not_mixed_with_verified_of_same_id(self) -> None:
        """**未検証と検証済みを同じ集計に混ぜない**（タスク箇条）。

        識別子が同じでも検証状態が違えば別の集計になる。同じ識別子で
        まとめる実装（`calibration_id` だけを鍵にした実装）はここで落ちる。
        """
        results = [
            metrics("t-1", calibration_id=CAL_A, verified=True, aim_error_mm=100.0),
            metrics("t-2", calibration_id=CAL_A, verified=True, aim_error_mm=200.0),
            metrics("t-3", calibration_id=CAL_A, verified=False, aim_error_mm=900.0),
        ]
        aggregates = aggregate(results, settings=settings())

        grouped = by_calibration(aggregates)
        assert set(grouped) == {(CAL_A, True), (CAL_A, False)}
        assert grouped[(CAL_A, True)].valid_throw_count == 2
        assert grouped[(CAL_A, False)].valid_throw_count == 1
        assert grouped[(CAL_A, False)].items[KEY_AIM_ERROR].median == pytest.approx(
            900.0, abs=EXACT_TOLERANCE
        )

    def test_group_order_is_deterministic(self) -> None:
        forward = aggregate(
            [
                metrics("t-1", calibration_id=CAL_B),
                metrics("t-2", calibration_id=CAL_A),
                metrics("t-3", calibration_id=CAL_A, verified=False),
            ],
            settings=settings(),
        )
        backward = aggregate(
            [
                metrics("t-3", calibration_id=CAL_A, verified=False),
                metrics("t-2", calibration_id=CAL_A),
                metrics("t-1", calibration_id=CAL_B),
            ],
            settings=settings(),
        )
        keys_forward = [(item.calibration_id, item.verified) for item in forward]
        keys_backward = [(item.calibration_id, item.verified) for item in backward]
        assert keys_forward == keys_backward
        # 検証済みが先、識別子は辞書順（要件 12.4 の再現性を形として固定する）。
        assert keys_forward == [(CAL_A, True), (CAL_A, False), (CAL_B, True)]


# ---------------------------------------------------------------------------
# 観測可能な完了状態 (2): 試行数下限未達に暫定の印（要件 5.10）
# ---------------------------------------------------------------------------


class TestProvisional:
    """**集計自体は返す。** 下限未達は例外ではなく印である（要件 5.10）。"""

    def test_below_minimum_is_flagged_but_still_returns_values(self) -> None:
        results = [metrics("t-1", aim_error_mm=100.0)]
        (item,) = aggregate(results, settings=settings(min_valid_throws=3))

        assert item.provisional is True
        assert REASON_INSUFFICIENT in item.provisional_reasons
        # 集計そのものは返る（例外にしない）。
        assert item.items[KEY_AIM_ERROR].count == 1
        assert item.items[KEY_AIM_ERROR].median == pytest.approx(
            100.0, abs=EXACT_TOLERANCE
        )

    def test_at_minimum_is_not_provisional(self) -> None:
        results = [metrics("t-1"), metrics("t-2"), metrics("t-3")]
        (item,) = aggregate(results, settings=settings(min_valid_throws=3))

        assert item.provisional is False
        assert item.provisional_reasons == ()

    def test_boundary_is_inclusive(self) -> None:
        """下限**ちょうど**は未達ではない。境界を取り違えた実装が落ちる。"""
        two = aggregate(
            [metrics("t-1"), metrics("t-2")], settings=settings(min_valid_throws=2)
        )
        one = aggregate([metrics("t-1")], settings=settings(min_valid_throws=2))
        assert two[0].provisional is False
        assert one[0].provisional is True

    def test_non_live_throws_make_the_aggregate_provisional(self) -> None:
        """**投擲単位の暫定規則と食い違わせない**（タスク4.4 からの申し送り）。

        `ConvergenceAnalyzer` は `require_live_source` かつ実機由来でない投擲を
        投擲単位で暫定としている。その投擲だけを集めた集計が「暫定でない」と
        言うと、同じ事実について2つの層が逆のことを言う。
        """
        results = [
            metrics("t-1", source=SourceKind.SIMULATED),
            metrics("t-2", source=SourceKind.LIVE),
        ]
        (item,) = aggregate(
            results, settings=settings(min_valid_throws=1, require_live_source=True)
        )
        assert item.provisional is True
        assert REASON_NON_LIVE in item.provisional_reasons

    def test_all_live_is_not_provisional_for_source(self) -> None:
        results = [
            metrics("t-1", source=SourceKind.LIVE),
            metrics("t-2", source=SourceKind.LIVE),
        ]
        (item,) = aggregate(
            results, settings=settings(min_valid_throws=1, require_live_source=True)
        )
        assert item.provisional is False
        assert REASON_NON_LIVE not in item.provisional_reasons

    def test_require_live_source_off_does_not_flag_simulated(self) -> None:
        results = [metrics("t-1", source=SourceKind.SIMULATED)]
        (item,) = aggregate(
            results, settings=settings(min_valid_throws=1, require_live_source=False)
        )
        assert item.provisional is False

    def test_unverified_is_not_folded_into_provisional(self) -> None:
        """**検証状態と試行数は別の軸である。**

        1つのフラグに畳むと「未検証だが試行数は十分」と「検証済みだが試行数
        不足」が同じ見え方になり、直し方の違う2つの状態が区別できなくなる。
        未検証であることは `verified` が運ぶ。
        """
        results = [metrics("t-1", verified=False), metrics("t-2", verified=False)]
        (item,) = aggregate(results, settings=settings(min_valid_throws=2))
        assert item.verified is False
        assert item.provisional is False
        assert item.provisional_reasons == ()

    def test_reasons_accumulate(self) -> None:
        results = [metrics("t-1", source=SourceKind.SIMULATED)]
        (item,) = aggregate(
            results, settings=settings(min_valid_throws=5, require_live_source=True)
        )
        assert set(item.provisional_reasons) == {REASON_INSUFFICIENT, REASON_NON_LIVE}


# ---------------------------------------------------------------------------
# 分布（代表値・ばらつき・最小最大・欠測数・試行数）
# ---------------------------------------------------------------------------


class TestDistribution:
    """要件 5.9: **代表値とばらつき、および試行数を併記する。**"""

    #: 6 件の値。中央値・p95・IQR・最小最大を手計算できる並びにしてある。
    #: 昇順: 10, 20, 30, 40, 50, 60
    #: median = _pct(0.50) = 補間位置 (6-1)*0.50 = 2.5 → 30 + 0.5*(40-30) = 35
    #: p95    = _pct(0.95) = 補間位置 (6-1)*0.95 = 4.75 → 50 + 0.75*(60-50) = 57.5
    #: p25    = _pct(0.25) = 補間位置 1.25 → 20 + 0.25*(30-20) = 22.5
    #: p75    = _pct(0.75) = 補間位置 3.75 → 40 + 0.75*(50-40) = 47.5
    #: IQR    = 47.5 - 22.5 = 25.0
    VALUES = (30.0, 10.0, 60.0, 20.0, 50.0, 40.0)
    EXPECT_MEDIAN = 35.0
    EXPECT_P95 = 57.5
    EXPECT_IQR = 25.0

    def test_representative_scatter_and_extremes(self) -> None:
        results = [
            metrics(f"t-{index}", aim_error_mm=value)
            for index, value in enumerate(self.VALUES)
        ]
        (item,) = aggregate(results, settings=settings())
        distribution = item.items[KEY_AIM_ERROR]

        assert distribution.count == 6
        assert distribution.missing == 0
        assert distribution.median == pytest.approx(
            self.EXPECT_MEDIAN, abs=EXACT_TOLERANCE
        )
        assert distribution.p95 == pytest.approx(self.EXPECT_P95, abs=EXACT_TOLERANCE)
        assert distribution.iqr == pytest.approx(self.EXPECT_IQR, abs=EXACT_TOLERANCE)
        assert distribution.minimum == pytest.approx(10.0, abs=EXACT_TOLERANCE)
        assert distribution.maximum == pytest.approx(60.0, abs=EXACT_TOLERANCE)

    def test_missing_is_counted_not_zero_filled(self) -> None:
        """**欠測は 0 で埋めない**（「0 だった」と「測っていない」は別）。"""
        results = [
            metrics("t-1", aim_error_mm=100.0),
            metrics("t-2", aim_error_mm=None),
            metrics("t-3", aim_error_mm=300.0),
        ]
        (item,) = aggregate(results, settings=settings())
        distribution = item.items[KEY_AIM_ERROR]

        assert distribution.count == 2
        assert distribution.missing == 1
        # 0 で埋めていれば中央値は 100.0 になる。欠測を外せば 200.0 である。
        assert distribution.median == pytest.approx(200.0, abs=EXACT_TOLERANCE)
        assert distribution.minimum == pytest.approx(100.0, abs=EXACT_TOLERANCE)

    def test_count_plus_missing_equals_throw_count(self) -> None:
        """どの項目も**同じ分母**で読める（欠測数の意味が項目ごとに動かない）。"""
        results = [
            metrics("t-1", total_flight_ms=None, aim_error_mm=100.0),
            metrics("t-2", total_flight_ms=700.0, aim_error_mm=None),
            metrics("t-3", total_flight_ms=900.0, aim_error_mm=300.0),
        ]
        (item,) = aggregate(results, settings=settings())
        for key in ALL_KEYS:
            distribution = item.items[key]
            assert distribution.count + distribution.missing == item.throw_count, key

    def test_empty_distribution_is_missing_not_zero(self) -> None:
        results = [metrics("t-1", aim_error_mm=None)]
        (item,) = aggregate(results, settings=settings())
        distribution = item.items[KEY_AIM_ERROR]
        assert distribution.count == 0
        assert distribution.missing == 1
        assert distribution.median is None
        assert distribution.p95 is None
        assert distribution.iqr is None
        assert distribution.minimum is None
        assert distribution.maximum is None

    def test_single_value_has_zero_iqr_and_equal_extremes(self) -> None:
        results = [metrics("t-1", aim_error_mm=42.0)]
        (item,) = aggregate(results, settings=settings())
        distribution = item.items[KEY_AIM_ERROR]
        assert distribution.median == pytest.approx(42.0, abs=EXACT_TOLERANCE)
        assert distribution.p95 == pytest.approx(42.0, abs=EXACT_TOLERANCE)
        assert distribution.iqr == pytest.approx(0.0, abs=EXACT_TOLERANCE)

    def test_non_finite_values_are_treated_as_missing(self) -> None:
        """NaN を「測れた値」として集計へ流さない（記録は JSON を経由しうる）。"""
        results = [
            metrics("t-1", aim_error_mm=float("nan")),
            metrics("t-2", aim_error_mm=100.0),
        ]
        (item,) = aggregate(results, settings=settings())
        distribution = item.items[KEY_AIM_ERROR]
        assert distribution.count == 1
        assert distribution.missing == 1
        assert distribution.median == pytest.approx(100.0, abs=EXACT_TOLERANCE)

    def test_time_error_missing_alone_is_not_zero_filled(self) -> None:
        """**落下時刻の真値だけが欠測**した投擲を 0 で埋めない。

        この状態は実在する。要件 4.2 は「観測点列が床面高さを跨ぐ区間が
        存在しない場合は欠測として扱う」と定めており、そのとき
        `measure_accuracy()` は**落下地点の誤差は作れるが
        `PredictionError.time_error_ms` は `None`** という行を返す
        （`accuracy.py`: 落下時刻だけが欠測なら時刻差だけが `None`）。
        タスク4.1 の申し送りによれば `SeamConfig.floor_margin_mm` との
        干渉で**実測項目5 は系統的に欠測になりうる**ので、これは稀な
        分岐ではない。

        0 で埋めると「予測落下時刻が実測とぴったり一致した」ことになり、
        代表値を良い方へ引っ張る。有効値を 100.0 と 300.0 の2件にして
        あるので、**欠測を外せば中央値は 200.0、0 で埋めれば 100.0** に
        なる——2つの実装が違う数として現れる。
        """
        results = [
            metrics("t-1", first_time_error_ms=100.0, final_time_error_ms=100.0),
            # 落下地点の誤差は有るが、落下時刻の真値だけが欠測な投擲。
            metrics("t-2", first_time_error_ms=None, final_time_error_ms=None),
            metrics("t-3", first_time_error_ms=300.0, final_time_error_ms=300.0),
        ]
        (item,) = aggregate(results, settings=settings())

        for key in (KEY_TIME_ERROR_FIRST, KEY_TIME_ERROR_FINAL):
            distribution = item.items[key]
            assert distribution.count == 2, key
            assert distribution.missing == 1, key
            assert distribution.median == pytest.approx(
                200.0, abs=EXACT_TOLERANCE
            ), key
            assert distribution.minimum == pytest.approx(
                100.0, abs=EXACT_TOLERANCE
            ), key

        # 落下地点の誤差のほうは3件そろっている（**当該項目のみ欠測**とし、
        # 他の項目の集計を止めない。要件 4.6）。
        assert item.items[KEY_HIT_ERROR_FINAL].count == 3
        assert item.items[KEY_HIT_ERROR_FINAL].missing == 0
        # 落下時刻の真値が無くても有効試行であることは変わらない
        # （有効試行の判定は**落下地点**の真値で行う）。
        assert item.valid_throw_count == 3


class TestPercentileRule:
    """分位点の作り方を上流・`latency.py` と揃える（同じ表に並ぶ数字のため）。"""

    def test_matches_the_latency_aggregator_interpolation(self) -> None:
        from m1_validation.metrics.latency import _percentile

        values = (10.0, 20.0, 30.0, 40.0, 50.0, 60.0)
        results = [
            metrics(f"t-{index}", aim_error_mm=value)
            for index, value in enumerate(values)
        ]
        (item,) = aggregate(results, settings=settings())
        distribution = item.items[KEY_AIM_ERROR]
        assert distribution.median == pytest.approx(
            _percentile(values, 0.5), abs=EXACT_TOLERANCE
        )
        assert distribution.p95 == pytest.approx(
            _percentile(values, 0.95), abs=EXACT_TOLERANCE
        )


# ---------------------------------------------------------------------------
# 実測7項目のすべてが集計されること／読み取り先が正しいこと
# ---------------------------------------------------------------------------


class TestItemWiring:
    """**10 個の項目値をすべて違う値にしてある。**

    読み取るフィールドを1つでも取り違えた実装は、違う分布を返す。
    """

    def test_each_item_reads_its_own_source_field(self) -> None:
        results = [
            metrics(
                "t-1",
                total_flight_ms=801.0,
                release_to_detect_ms=102.0,
                aim_error_mm=303.0,
                first_error_mm=(3.0, 4.0),  # ノルム 5.0
                final_error_mm=(6.0, 8.0),  # ノルム 10.0
                first_time_error_ms=11.0,
                final_time_error_ms=7.0,
                valid_samples=9,
                converged_at=5,
            )
        ]
        (item,) = aggregate(
            results,
            settings=settings(),
            latency=latency_for({"t-1": 23.0}),
        )
        expected = {
            KEY_TOTAL_FLIGHT: 801.0,
            KEY_RELEASE_TO_DETECT: 102.0,
            KEY_DETECT_TO_FIRST_PREDICTION: 23.0,
            KEY_HIT_ERROR_FIRST: 5.0,
            KEY_HIT_ERROR_FINAL: 10.0,
            KEY_TIME_ERROR_FIRST: 11.0,
            KEY_TIME_ERROR_FINAL: 7.0,
            KEY_AIM_ERROR: 303.0,
            KEY_VALID_SAMPLES: 9.0,
            KEY_CONVERGED_AT: 5.0,
        }
        for key, value in expected.items():
            assert item.items[key].median == pytest.approx(
                value, abs=EXACT_TOLERANCE
            ), key

    def test_all_ten_item_keys_are_present(self) -> None:
        (item,) = aggregate([metrics("t-1")], settings=settings())
        assert set(item.items) == set(ALL_KEYS)

    def test_latency_absent_makes_item3_missing_not_zero(self) -> None:
        (item,) = aggregate([metrics("t-1")], settings=settings())
        distribution = item.items[KEY_DETECT_TO_FIRST_PREDICTION]
        assert distribution.count == 0
        assert distribution.missing == 1
        assert distribution.median is None

    def test_latency_row_for_another_throw_is_not_borrowed(self) -> None:
        (item,) = aggregate(
            [metrics("t-1")],
            settings=settings(),
            latency=latency_for({"t-9": 23.0}),
        )
        assert item.items[KEY_DETECT_TO_FIRST_PREDICTION].count == 0


class TestLabelInvariants:
    """ラベルの**独立した不変条件**（自己比較にしないための対の検査）。"""

    def test_item_keys_match_the_literals_and_are_distinct(self) -> None:
        assert tuple(ITEM_KEYS) == ALL_KEYS
        assert len(set(ITEM_KEYS)) == len(ITEM_KEYS)

    def test_every_measurement_item_1_to_7_has_keys(self) -> None:
        """**実測7項目のすべてが集計される**（要件 5.9）。"""
        assert set(MEASUREMENT_ITEM_KEYS) == {1, 2, 3, 4, 5, 6, 7}
        covered: list[str] = []
        for number in sorted(MEASUREMENT_ITEM_KEYS):
            keys = MEASUREMENT_ITEM_KEYS[number]
            assert keys, number
            covered.extend(keys)
        # どの項目キーも、ちょうど1つの実測項目に属する。
        assert sorted(covered) == sorted(ALL_KEYS)

    def test_provisional_reason_labels_match_the_literals(self) -> None:
        assert PROVISIONAL_INSUFFICIENT_VALID_THROWS == REASON_INSUFFICIENT
        assert PROVISIONAL_NON_LIVE_THROWS == REASON_NON_LIVE
        assert PROVISIONAL_INSUFFICIENT_VALID_THROWS != PROVISIONAL_NON_LIVE_THROWS

    def test_reported_reasons_come_from_the_known_vocabulary(self) -> None:
        (item,) = aggregate(
            [metrics("t-1", source=SourceKind.SIMULATED)],
            settings=settings(min_valid_throws=9, require_live_source=True),
        )
        assert set(item.provisional_reasons) <= {REASON_INSUFFICIENT, REASON_NON_LIVE}
        assert item.provisional_reasons


# ---------------------------------------------------------------------------
# 試行数・実機由来の投擲数・真値欠測の扱い
# ---------------------------------------------------------------------------


class TestTrialCounts:
    def test_live_throws_are_counted(self) -> None:
        """**実機由来の投擲数を数える**（後段の判断が使う。要件 9.10 の材料）。"""
        results = [
            metrics("t-1", source=SourceKind.LIVE),
            metrics("t-2", source=SourceKind.LIVE),
            metrics("t-3", source=SourceKind.RECORDED),
            metrics("t-4", source=SourceKind.SIMULATED),
        ]
        (item,) = aggregate(results, settings=settings())
        assert item.throw_count == 4
        assert item.live_throw_count == 2

    def test_truth_missing_throw_is_not_counted_as_a_valid_trial(self) -> None:
        """タスク4.3 からの申し送り: **落下地点の真値が未記入の投擲は試行数に
        数えない**（`AccuracyResult.errors` が空になる理由が2つあるため、
        戻り値ではなく真値の求め方を見て決める）。
        """
        results = [
            metrics("t-1"),
            metrics(
                "t-2",
                impact_point_missing=True,
                first_error_mm=None,
                final_error_mm=None,
            ),
        ]
        (item,) = aggregate(results, settings=settings())
        assert item.throw_count == 2
        assert item.valid_throw_count == 1

    def test_valid_trial_is_decided_by_truth_not_by_the_error_series(self) -> None:
        """**真値はあるが有効な予測が0件**の投擲は、有効試行として数える。

        `AccuracyResult.errors` が空になる理由は2つある（有効予測が0件 /
        落下地点の真値が未記入）。誤差系列の空きだけを見て試行数から外す
        実装は、**予測が1度も成立しなかったという事実を「測っていない」に
        すり替える**。ここはその2つを分ける唯一の入力である。
        """
        results = [
            metrics(
                "t-1",
                impact_point_missing=False,
                first_error_mm=None,
                final_error_mm=None,
            ),
            metrics(
                "t-2",
                impact_point_missing=True,
                first_error_mm=None,
                final_error_mm=None,
            ),
        ]
        (item,) = aggregate(results, settings=settings())
        assert item.throw_count == 2
        assert item.valid_throw_count == 1
        assert [row.truth_available for row in item.per_throw] == [True, False]

    def test_truth_missing_throw_still_contributes_other_items(self) -> None:
        """要件 4.6: **他の項目の集計を止めない。**"""
        results = [
            metrics("t-1", aim_error_mm=100.0),
            metrics(
                "t-2",
                impact_point_missing=True,
                first_error_mm=None,
                final_error_mm=None,
                total_flight_ms=700.0,
                aim_error_mm=None,
            ),
        ]
        (item,) = aggregate(results, settings=settings())
        assert item.items[KEY_TOTAL_FLIGHT].count == 2
        assert item.items[KEY_HIT_ERROR_FINAL].count == 1

    def test_provisional_gate_uses_valid_trials_not_raw_throws(self) -> None:
        results = [
            metrics("t-1"),
            metrics(
                "t-2",
                impact_point_missing=True,
                first_error_mm=None,
                final_error_mm=None,
            ),
        ]
        (item,) = aggregate(results, settings=settings(min_valid_throws=2))
        assert item.throw_count == 2
        assert item.provisional is True

    def test_session_ids_are_collected(self) -> None:
        results = [
            metrics("t-1", session_id="session-b"),
            metrics("t-2", session_id="session-a"),
            metrics("t-3", session_id="session-a"),
            metrics("t-4", session_id=None),
        ]
        (item,) = aggregate(results, settings=settings())
        assert item.session_ids == ("session-a", "session-b")


class TestFailedThrows:
    """失敗投擲は `runner.successful_throws()` の規則で除く（要件 3.8）。"""

    def test_failed_throws_are_excluded_but_counted(self) -> None:
        results = [
            metrics("t-1", aim_error_mm=100.0),
            metrics("t-2", aim_error_mm=900.0, failed_reason="no_valid_sample"),
        ]
        (item,) = aggregate(results, settings=settings())
        assert item.throw_count == 1
        assert item.failed_throw_count == 1
        assert item.items[KEY_AIM_ERROR].count == 1
        assert item.items[KEY_AIM_ERROR].median == pytest.approx(
            100.0, abs=EXACT_TOLERANCE
        )

    def test_group_of_only_failed_throws_is_still_reported(self) -> None:
        """黙って消さない（その識別子で投げた事実そのものが材料である）。"""
        results = [metrics("t-1", failed_reason="no_valid_sample")]
        (item,) = aggregate(results, settings=settings())
        assert item.throw_count == 0
        assert item.failed_throw_count == 1
        assert item.valid_throw_count == 0
        assert item.provisional is True


# ---------------------------------------------------------------------------
# 収束の3値（未収束と測定不能を混ぜない）
# ---------------------------------------------------------------------------


class TestConvergenceCounts:
    """タスク4.4 からの申し送り (2) (3) に答える。"""

    def test_not_measurable_is_not_counted_as_not_converged(self) -> None:
        results = [
            metrics("t-1", converged_at=5, verdict="converged"),
            metrics("t-2", converged_at=None, verdict="not_converged"),
            metrics(
                "t-3",
                converged_at=None,
                verdict="not_measurable",
                impact_point_missing=True,
                first_error_mm=None,
                final_error_mm=None,
            ),
        ]
        (item,) = aggregate(results, settings=settings())
        assert item.converged_count == 1
        assert item.not_converged_count == 1
        assert item.not_measurable_count == 1

    def test_converged_at_distribution_excludes_unconverged_throws(self) -> None:
        results = [
            metrics("t-1", converged_at=4, verdict="converged"),
            metrics("t-2", converged_at=6, verdict="converged"),
            metrics("t-3", converged_at=None, verdict="not_converged"),
        ]
        (item,) = aggregate(results, settings=settings())
        distribution = item.items[KEY_CONVERGED_AT]
        assert distribution.count == 2
        assert distribution.missing == 1
        assert distribution.median == pytest.approx(5.0, abs=EXACT_TOLERANCE)

    def test_single_prediction_throws_are_counted_separately(self) -> None:
        """有効な予測が1件だけの投擲は**規則上つねに未収束**になる。

        未収束の件数をそのまま「予測が落ち着かなかった投擲」と読むと誤る
        ので、その内数を別に数えて読み分けられるようにする。
        """
        results = [
            metrics(
                "t-1",
                converged_at=None,
                verdict="not_converged",
                first_error_mm=None,  # 有効な予測は最終の1件だけ
            ),
            metrics("t-2", converged_at=None, verdict="not_converged"),
            # 有効な予測が **0 件** の投擲。「1件だけ」に数えてはならない。
            metrics(
                "t-3",
                converged_at=None,
                verdict="not_measurable",
                first_error_mm=None,
                final_error_mm=None,
                impact_point_missing=True,
            ),
        ]
        (item,) = aggregate(results, settings=settings())
        assert item.not_converged_count == 2
        assert item.single_prediction_throw_count == 1


# ---------------------------------------------------------------------------
# 帰属の入力（誤差ベクトル群）
# ---------------------------------------------------------------------------


class TestErrorVectors:
    """**ベクトルのまま束ねる**（帰属が向きを使う。タスク4.3 の申し送り）。"""

    def test_error_vectors_are_the_final_vectors_in_row_order(self) -> None:
        results = [
            metrics("t-1", final_error_mm=(10.0, -20.0)),
            metrics("t-2", final_error_mm=(-30.0, 40.0)),
        ]
        (item,) = aggregate(results, settings=settings())
        assert item.error_vectors == ((10.0, -20.0), (-30.0, 40.0))

    def test_error_vectors_are_not_collapsed_to_norms(self) -> None:
        """符号を落とした（ノルム化した）実装はここで落ちる。"""
        results = [metrics("t-1", final_error_mm=(-60.0, -80.0))]
        (item,) = aggregate(results, settings=settings())
        assert item.error_vectors == ((-60.0, -80.0),)

    def test_throws_without_final_error_contribute_no_vector(self) -> None:
        results = [
            metrics("t-1", final_error_mm=(10.0, -20.0)),
            metrics(
                "t-2",
                impact_point_missing=True,
                first_error_mm=None,
                final_error_mm=None,
            ),
        ]
        (item,) = aggregate(results, settings=settings())
        assert item.error_vectors == ((10.0, -20.0),)

    def test_throw_with_truth_but_no_valid_prediction_contributes_no_vector(
        self,
    ) -> None:
        """**真値の有無と誤差ベクトルの有無は独立の軸である。**

        落下地点の真値が記入されていても、有効な予測が1件も無ければ誤差
        ベクトルは作れない。`truth_available` で誤差ベクトルを絞る実装は
        この投擲について `None` を並べてしまい、`error_vectors` の契約
        （`tuple[tuple[float, float], ...]`）が破れる。壊れるのは集計では
        なく**タスク5.2 の帰属**である——共通の偏りの向きを平均ベクトルと
        して求める段で `None` に当たる。

        そこで、真値の欠測とは**別の理由**で誤差ベクトルを持たない投擲を
        混ぜ、`valid_throw_count` と `len(error_vectors)` が一致しない入力
        にしてある（一致させて書くと、2つの軸を取り違えた実装が素通りする）。
        """
        results = [
            metrics("t-1", final_error_mm=(10.0, -20.0)),
            # 真値はある（有効試行として数える）が、有効な予測が0件。
            metrics(
                "t-2",
                impact_point_missing=False,
                first_error_mm=None,
                final_error_mm=None,
            ),
            metrics("t-3", final_error_mm=(-30.0, 40.0)),
        ]
        (item,) = aggregate(results, settings=settings())

        # 3件とも有効試行だが、誤差ベクトルは2件しか無い。
        assert item.valid_throw_count == 3
        assert len(item.error_vectors) == 2
        assert item.error_vectors == ((10.0, -20.0), (-30.0, 40.0))
        assert all(
            isinstance(vector, tuple) and len(vector) == 2
            for vector in item.error_vectors
        )
        assert None not in item.error_vectors

    def test_vectors_are_not_mixed_across_calibration_ids(self) -> None:
        results = [
            metrics("t-1", calibration_id=CAL_A, final_error_mm=(10.0, 0.0)),
            metrics("t-2", calibration_id=CAL_B, final_error_mm=(0.0, 90.0)),
        ]
        grouped = by_calibration(aggregate(results, settings=settings()))
        assert grouped[(CAL_A, True)].error_vectors == ((10.0, 0.0),)
        assert grouped[(CAL_B, True)].error_vectors == ((0.0, 90.0),)


class TestPerThrowRows:
    def test_rows_carry_the_values_the_distributions_are_built_from(self) -> None:
        results = [
            metrics("t-1", aim_error_mm=100.0, source=SourceKind.LIVE),
            metrics("t-2", aim_error_mm=300.0),
        ]
        (item,) = aggregate(results, settings=settings())
        assert [row.record_id for row in item.per_throw] == ["t-1", "t-2"]
        assert item.per_throw[0].live is True
        assert item.per_throw[1].live is False
        assert item.per_throw[0].values[KEY_AIM_ERROR] == pytest.approx(
            100.0, abs=EXACT_TOLERANCE
        )
        assert item.per_throw[0].session_id == "session-0001"
        assert item.per_throw[0].truth_available is True

    def test_rows_are_immutable(self) -> None:
        (item,) = aggregate([metrics("t-1")], settings=settings())
        with pytest.raises(dataclasses.FrozenInstanceError):
            item.per_throw[0].record_id = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 入力の取り違えを拒否する
# ---------------------------------------------------------------------------


class TestRejectedInputs:
    def test_record_without_m1_extra_is_rejected(self) -> None:
        """キャリブレーションの分からない投擲を、どこかの群へ紛れ込ませない。"""
        broken = ThrowMetrics(
            record=dataclasses.replace(record("t-1"), extra={}),
            truth=throw_truth("t-1"),
            flight=flight_result(
                total_flight_ms=1.0, release_to_detect_ms=1.0, aim_error_mm=1.0
            ),
            accuracy=accuracy_result(first=None, final=None),
            convergence=convergence_result(
                valid_samples=1, converged_at=None, verdict="not_measurable"
            ),
        )
        with pytest.raises(M1ConfigError):
            aggregate([broken], settings=settings())

    def test_unknown_m1_extra_version_is_rejected(self) -> None:
        with pytest.raises(M1ConfigError):
            aggregate(
                [metrics("t-1", extra_version="99.0")], settings=settings()
            )

    def test_duplicate_record_id_is_rejected(self) -> None:
        with pytest.raises(M1ConfigError):
            aggregate([metrics("t-1"), metrics("t-1")], settings=settings())

    def test_mismatched_truth_record_id_is_rejected(self) -> None:
        with pytest.raises(M1ConfigError):
            aggregate(
                [metrics("t-1", truth_record_id="t-9")], settings=settings()
            )

    def test_contradictory_verified_flags_are_rejected(self) -> None:
        """要約と最上位で検証状態が食い違う記録は、どちらの群にも入れない。"""
        payload = m1_extra(verified=True)
        payload["verified"] = False
        with pytest.raises(M1ConfigError):
            aggregate([metrics("t-1", m1_payload=payload)], settings=settings())

    def test_missing_calibration_id_is_rejected(self) -> None:
        payload = m1_extra()
        calibration = payload["calibration"]
        assert isinstance(calibration, dict)
        calibration["calibration_id"] = ""
        with pytest.raises(M1ConfigError):
            aggregate([metrics("t-1", m1_payload=payload)], settings=settings())

    def test_empty_input_returns_no_aggregate(self) -> None:
        assert aggregate([], settings=settings()) == ()


# ---------------------------------------------------------------------------
# 再現性（要件 12.4）
# ---------------------------------------------------------------------------


class TestReproducibility:
    def test_same_input_gives_equal_aggregates(self) -> None:
        results = [
            metrics("t-1", calibration_id=CAL_B, aim_error_mm=100.0),
            metrics("t-2", calibration_id=CAL_A, aim_error_mm=200.0),
        ]
        first = aggregate(results, settings=settings())
        second = aggregate(results, settings=settings())
        assert first == second

    def test_aggregate_is_immutable(self) -> None:
        (item,) = aggregate([metrics("t-1")], settings=settings())
        with pytest.raises(dataclasses.FrozenInstanceError):
            item.provisional = False  # type: ignore[misc]

    def test_items_mapping_is_copied(self) -> None:
        (item,) = aggregate([metrics("t-1")], settings=settings())
        assert isinstance(item.items, Mapping)
        assert isinstance(item.items[KEY_AIM_ERROR], Distribution)


# ---------------------------------------------------------------------------
# 本物の実測結果型を通す（フィールドの意味の上での配線）
# ---------------------------------------------------------------------------


def trajectory() -> TrajectoryParameters:
    return TrajectoryParameters(
        t_ref_ms=5100.0,
        x0_mm=-1700.0,
        y0_mm=-50.0,
        z0_mm=1690.0,
        estimated_vx_mm_s=3000.0,
        estimated_vy_mm_s=-500.0,
        estimated_vz_mm_s=1020.0,
        gravity_mm_s2=9806.65,
    )


def real_prediction(
    *, sample_count: int, hit_mm: tuple[float, float], hit_time_ms: float
) -> Prediction:
    return Prediction(
        predicted_hit_x_mm=hit_mm[0],
        predicted_hit_y_mm=hit_mm[1],
        predicted_hit_time_ms=hit_time_ms,
        remaining_time_ms=600.0 - 10.0 * sample_count,
        estimated_vx_mm_s=3000.0,
        estimated_vy_mm_s=-500.0,
        estimated_vz_mm_s=1020.0,
        residual=3.0,
        trajectory=trajectory(),
        sample_count=sample_count,
        based_on_time_ms=5100.0 + 20.0 * sample_count,
        elapsed_ms=1.25,
        config=CONFIG,
    )


class TestRealResultTypes:
    """`measure_flight()` / `measure_accuracy()` / `analyze_convergence()` の
    **本物の戻り値**を集計へ通す。代数の段はフィールド名の綴りしか守らないが、
    ここは「どの実測項目がどこから来るか」を意味の上で固定する。
    """

    #: 真値（テスト局所のリテラル）。落下地点 (1050, 1120)、落下時刻 5793、
    #: リリース時刻 5000。待機位置は (1000, 1000) なので狙い誤差は
    #: hypot(50, 120) = 130.0 mm（3-4-5 系の整数三角形）。
    EXPECT_AIM_ERROR_MM = 130.0
    #: 総飛行時間 = 5793 - 5000 = 793.0 ms
    EXPECT_TOTAL_FLIGHT_MS = 793.0
    #: 検出開始 = 最初のサンプルの t_ms = 5100.0 → 5100 - 5000 = 100.0 ms
    EXPECT_RELEASE_TO_DETECT_MS = 100.0
    #: 最終予測 (1110, 1200) − 実測 (1050, 1120) = (60, 80) → ノルム 100.0 mm
    EXPECT_FINAL_ERROR_MM = 100.0
    #: 初回予測 (1080, 1160) − 実測 = (30, 40) → ノルム 50.0 mm
    EXPECT_FIRST_ERROR_MM = 50.0
    #: 最終予測の落下時刻 5800 − 5793 = 7.0 ms
    EXPECT_FINAL_TIME_ERROR_MS = 7.0

    def build(self) -> ThrowMetrics:
        predictions: tuple[PredictionOutcome, ...] = (
            InvalidPrediction(
                reason=InvalidReason.INSUFFICIENT_SAMPLES,
                detail="テスト用",
                sample_count=1,
                based_on_time_ms=5120.0,
                elapsed_ms=0.5,
                config=CONFIG,
            ),
            real_prediction(
                sample_count=3, hit_mm=(1080.0, 1160.0), hit_time_ms=5804.0
            ),
            real_prediction(
                sample_count=4, hit_mm=(1110.0, 1200.0), hit_time_ms=5800.0
            ),
        )
        throw = record("t-real", sample_count=4, predictions=predictions)
        truth = throw_truth("t-real")
        return ThrowMetrics(
            record=throw,
            truth=truth,
            flight=measure_flight(throw, truth, layout=layout()),
            accuracy=measure_accuracy(throw, truth),
            convergence=analyze_convergence(
                throw, measure_accuracy(throw, truth), settings=settings()
            ),
        )

    def test_items_match_the_hand_computed_values(self) -> None:
        (item,) = aggregate([self.build()], settings=settings())
        expected = {
            KEY_TOTAL_FLIGHT: self.EXPECT_TOTAL_FLIGHT_MS,
            KEY_RELEASE_TO_DETECT: self.EXPECT_RELEASE_TO_DETECT_MS,
            KEY_AIM_ERROR: self.EXPECT_AIM_ERROR_MM,
            KEY_HIT_ERROR_FIRST: self.EXPECT_FIRST_ERROR_MM,
            KEY_HIT_ERROR_FINAL: self.EXPECT_FINAL_ERROR_MM,
            KEY_TIME_ERROR_FINAL: self.EXPECT_FINAL_TIME_ERROR_MS,
            KEY_VALID_SAMPLES: 4.0,
        }
        for key, value in expected.items():
            assert item.items[key].median == pytest.approx(
                value, abs=EXACT_TOLERANCE
            ), key

    def test_error_vector_keeps_the_direction_of_the_final_prediction(self) -> None:
        (item,) = aggregate([self.build()], settings=settings())
        assert item.error_vectors == ((60.0, 80.0),)


class TestThrowRowShape:
    def test_row_type_is_exposed(self) -> None:
        (item,) = aggregate([metrics("t-1")], settings=settings())
        assert all(isinstance(row, ThrowRow) for row in item.per_throw)
