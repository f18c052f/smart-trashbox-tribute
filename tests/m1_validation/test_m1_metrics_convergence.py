"""実測項目 7 と収束判定規則の検証（タスク 4.4、要件 5.7, 5.8）。

観測可能な完了状態（tasks.md 4.4）を固定する:

- **既知の収束列・未収束列に対して規則どおりの結果が返る**
- **規則の説明文が結果に含まれる**

あわせて design.md「ConvergenceAnalyzer」と research.md Decision 8 が定める
点も固定する:

- 収束は**その投擲の最終予測**を基準に測る（真値との誤差ではない）
- 帯域の既定は**レイアウトの暫定許容窓**に揃える（合否条件ではない）
- 収束しない投擲は「未収束」を**正常な結果**として返す
- **収束サンプル数と最終誤差を必ず併記する**

**検査を2段に分けた理由。** タスク 4.2 / 4.3 と同じである。合成軌道だけで
検査すると、**区別すべき差が入力に現れない**。とくに本モジュールでは、
合成軌道の予測が3サンプル目から厳密解に一致してしまうため「最終予測からの
距離」がどの実装でもほぼ 0 になり、**帯域の比較を間違えた実装（真値との
誤差で測る・帯域の内外を逆にする・末尾条件を落とす）でも通ってしまう**。
tasks.md「Implementation Notes」タスク4.3 が記録している「その差が現れない
入力でだけ検査している空振り」そのものである。

1. **代数の段**（`TestValidSamples` / `TestConvergenceRule` / `TestBand` /
   `TestCriterion` / `TestCoReporting` / `TestNotMeasurable` /
   `TestMismatchedInputs` / `TestProvisional`）: 予測落下地点を**テスト局所の
   リテラル**で組み、最終予測からのずれを 300 / 150 / 50 / 60 / 20 / 20 / 0 mm
   と**帯域をまたいで上下させた列**を置く。末尾条件を落とした実装・帯域の
   境界を取り違えた実装・真値との距離で測った実装・系列を逆順に見た実装が、
   そのまま**違う収束サンプル数**として現れる。
2. **合成軌道の段**（`TestSyntheticTrajectory`）: 既知の放物軌道から
   `ThrowPredictionTracker` に記録を作らせ、`derive_truth()` の本物の真値を
   通して端から端まで通す。**真値には帯域より大きい既知のオフセットを注入
   する**——最終予測自体が 180 mm ずれていても収束は速く出るという
   research.md Decision 8 の Trade-off をそのまま検査にしてある。真値との
   距離で測る実装はここで「未収束」を返して落ちる。

**参照解は実装の定数に触れない。** 帯域の既定値の期待値は本ファイルの
`APERTURE_DIAMETER_MM` / `OBJECT_DIAMETER_MM`（リテラル）から組み、
`ThrowLayout.position_tolerance_mm` や `M1Settings.effective_convergence_band_mm`
の戻り値を期待値に使わない（tasks.md「Implementation Notes」タスク4.1）。

**許容差の根拠。** 代数の段の距離はすべて 3-4-5 系の整数三角形から作った
有限桁の四則演算であり、**厳密に一致するのが正しい**。`EXACT_TOLERANCE`
（1e-9）は丸めのためだけの余裕であり、帯域の判定を誤れば 10 mm 以上ずれる。
合成軌道の段のフィットは厳密解なので、収束サンプル数は整数として厳密に、
最終誤差は注入したオフセット（180 mm 規模）に対して `SYN_TOLERANCE_MM`
（1e-3 mm）で突き合わせる——**実装が申告した値を許容差に使わない**。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import FrozenInstanceError

import pytest

from m1_validation.config import ConvergenceConfig, M1Settings, TrialLimits
from m1_validation.errors import M1ConfigError
from m1_validation.layout import ThrowLayout
from m1_validation.metrics.accuracy import (
    AccuracyResult,
    PredictionError,
    measure_accuracy,
)
from m1_validation.metrics.convergence import (
    CONVERGENCE_QUESTION,
    VERDICT_CONVERGED,
    VERDICT_NOT_CONVERGED,
    VERDICT_NOT_MEASURABLE,
    ConvergenceResult,
    analyze_convergence,
)
from m1_validation.truth import derive_truth
from m1_validation.types import ThrowTruth, TruthMethod, TruthValue
from prediction_core import (
    InvalidPrediction,
    InvalidReason,
    Prediction,
    PredictionConfig,
    PredictionOutcome,
    Sample,
    SourceKind,
    ThrowPredictionTracker,
    ThrowRecord,
    TrajectoryParameters,
)

CONFIG = PredictionConfig()
G_MM_MS2 = CONFIG.gravity_mm_ms2

EXACT_TOLERANCE = 1e-9

RECORD_ID = "throw-0044"


# --- 代数の段で使う既知の値（すべてテスト局所のリテラル）-------------------

#: 実際の落下地点（World mm）。z を 0 でない値にして、水平2成分だけを見て
#: いることが崩れたら差が出るようにしてある。
IMPACT_POINT_MM = (1050.0, 1120.0, 12.0)
IMPACT_T_MS = 5793.0

#: レイアウトの寸法（暫定許容窓の期待値をここから組む）。
APERTURE_DIAMETER_MM = 200.0
OBJECT_DIAMETER_MM = 65.0
#: 帯域の既定の期待値 = 開口半径 − 対象寸法/2 = 67.5 mm。**実装の
#: `position_tolerance_mm` を呼ばずにリテラルから組む**（モジュール docstring）。
DEFAULT_BAND_MM = APERTURE_DIAMETER_MM / 2.0 - OBJECT_DIAMETER_MM / 2.0

#: 代数の段で明示的に与える帯域（mm）。既定（67.5）と**違う答えになる**値を
#: 選んである——既定を素通しする実装と、指定を無視する実装の両方が落ちる。
BAND_MM = 50.0

#: すべての予測に共通して乗せる、真値からのずれ（World mm の水平2成分）。
#: ノルムは 500 mm であり**帯域の 10 倍**である。収束を「真値との誤差」で
#: 測る実装は、この列を丸ごと「未収束」と報告して落ちる。
COMMON_OFFSET_MM = (400.0, -300.0)
COMMON_OFFSET_NORM_MM = 500.0

#: 収束列。`(サンプル数, 最終予測からのずれ)`。ずれのノルムは順に
#: 300 / 150 / **50（帯域ちょうど）** / 60 / 20 / 20 / 0 mm である。
#: **一度帯域へ入ってから出る**列にしてあるので、末尾条件（帯域内に留まり
#: 続けること）を落とした実装は違う答えを返す。
CONVERGING_RELATIVE_MM: tuple[tuple[int, tuple[float, float]], ...] = (
    (3, (300.0, 0.0)),
    (4, (-120.0, 90.0)),
    (5, (30.0, 40.0)),
    (6, (60.0, 0.0)),
    (7, (0.0, -20.0)),
    (8, (12.0, -16.0)),
    (9, (0.0, 0.0)),
)

#: 帯域 50 mm・末尾条件ありのときの収束サンプル数。
#: 7 サンプル目以降（20 / 20 / 0 mm）だけが帯域内に留まり続ける。
CONVERGED_AT_TAIL = 7
#: 帯域 50 mm・末尾条件なしのときの収束サンプル数。
#: **最初に**帯域へ触れるのは 5 サンプル目（ちょうど 50 mm）である。
CONVERGED_AT_FIRST_TOUCH = 5
#: 帯域 67.5 mm（既定）・末尾条件ありのときの収束サンプル数。
#: 60 mm のずれが帯域内に入るので 5 サンプル目まで前倒しになる。
CONVERGED_AT_DEFAULT_BAND = 5

#: 未収束列。最終予測の1つ前が 60 mm（帯域外）であり、**最終予測より前の
#: どの予測も帯域に留まらない**。最終予測は自分自身との距離が 0 なので必ず
#: 帯域内であり、それを収束と呼ぶ実装はここで落ちる。
NON_CONVERGING_RELATIVE_MM: tuple[tuple[int, tuple[float, float]], ...] = (
    (3, (300.0, 0.0)),
    (4, (-120.0, 90.0)),
    (5, (36.0, 48.0)),
    (6, (0.0, 0.0)),
)

#: 最初の有効予測から動かない列（3サンプル目で収束する）。
IMMEDIATE_RELATIVE_MM: tuple[tuple[int, tuple[float, float]], ...] = (
    (3, (0.0, 0.0)),
    (4, (0.0, 0.0)),
    (5, (0.0, 0.0)),
)

#: 記録に入っている**有効サンプル数**。有効予測の最終サンプル数（9）と
#: **わざと違えてある**——末尾の予測が無効になった投擲では両者がずれるので、
#: 「最終予測のサンプル数」を有効サンプル数として報告する実装が落ちる。
VALID_SAMPLES = 11


# --- 合成軌道の段で使う既知の軌道 -------------------------------------------

#: 床面の高さ（mm）。実装の `truth.FLOOR_HEIGHT_MM` を参照しない。
FLOOR_Z_MM = 0.0

SYN_RELEASE_T_MS = 5000.0
SYN_RELEASE_HEIGHT_MM = 1500.0
SYN_RELEASE_X_MM = -2000.0
SYN_RELEASE_Y_MM = 0.0
SYN_VX_MM_MS = 3.0
SYN_VY_MM_MS = -0.5
SYN_VZ_MM_MS = 2.0

SYN_FIRST_SAMPLE_T_MS = 5100.0
SYN_DT_MS = 1000.0 / 60.0

SYN_TOLERANCE_MM = 1e-3

#: 合成軌道の段で真値へ注入する既知のオフセット（World mm の水平2成分）。
#: ノルムは 180.27… mm であり、**帯域の既定（67.5 mm）より大きい**。
#: 最終予測が帯域より大きくずれていても収束は速く出る、という
#: research.md Decision 8 の Trade-off をそのまま検査にするための値である。
SYN_TRUTH_OFFSET_MM = (150.0, -100.0)
SYN_TRUTH_OFFSET_NORM_MM = math.hypot(150.0, 100.0)

#: 予測が成立し始めるサンプル数。`docs/requirements.md` FR-1 の「3」であり、
#: 実装の `PredictionConfig.min_samples` を参照せずにリテラルで置く。
SYN_FIRST_PREDICTION_SAMPLES = 3


def syn_position_at(t_ms: float) -> tuple[float, float, float]:
    """既知の軌道上の位置（World mm）。"""
    s = t_ms - SYN_RELEASE_T_MS
    return (
        SYN_RELEASE_X_MM + SYN_VX_MM_MS * s,
        SYN_RELEASE_Y_MM + SYN_VY_MM_MS * s,
        SYN_RELEASE_HEIGHT_MM + SYN_VZ_MM_MS * s - 0.5 * G_MM_MS2 * s * s,
    )


def syn_impact_time_ms() -> float:
    """解析解の落下時刻（`z = FLOOR_Z_MM` を満たす遅い方の根）。"""
    disc = SYN_VZ_MM_MS * SYN_VZ_MM_MS + 2.0 * G_MM_MS2 * (
        SYN_RELEASE_HEIGHT_MM - FLOOR_Z_MM
    )
    return SYN_RELEASE_T_MS + (SYN_VZ_MM_MS + math.sqrt(disc)) / G_MM_MS2


def syn_impact_point_mm() -> tuple[float, float, float]:
    """解析解の落下地点（World mm）。z は床面高さちょうど。"""
    x_mm, y_mm, _ = syn_position_at(syn_impact_time_ms())
    return (x_mm, y_mm, FLOOR_Z_MM)


# --- 組み立てヘルパ ---------------------------------------------------------


def build_layout(**overrides: object) -> ThrowLayout:
    values: dict[str, object] = {
        "layout_id": "throw-a",
        "release_position_world_mm": (
            SYN_RELEASE_X_MM,
            SYN_RELEASE_Y_MM,
            SYN_RELEASE_HEIGHT_MM,
        ),
        "release_height_mm": SYN_RELEASE_HEIGHT_MM,
        "throw_direction_deg": 0.0,
        "standby_position_world_mm": (150.0, -80.0),
        "object_diameter_mm": OBJECT_DIAMETER_MM,
        "aperture_diameter_mm": APERTURE_DIAMETER_MM,
        "camera_position_world_mm": (0.0, -1500.0, 1000.0),
        "notes": "仮値。確定ではない。",
    }
    values.update(overrides)
    return ThrowLayout(**values)  # type: ignore[arg-type]


def build_settings(
    *,
    band_mm: float | None = BAND_MM,
    require_monotonic_tail: bool = True,
    require_live_source: bool = True,
    layout: ThrowLayout | None = None,
) -> M1Settings:
    """テスト局所のリテラルから設定一式を組む。"""
    return M1Settings(
        layout=build_layout() if layout is None else layout,
        convergence=ConvergenceConfig(
            band_mm=band_mm, require_monotonic_tail=require_monotonic_tail
        ),
        trials=TrialLimits(require_live_source=require_live_source),
    )


def trajectory() -> TrajectoryParameters:
    """代数の段では中身が結果に効かない軌道パラメータ（形を満たすだけ）。"""
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


def valid_prediction(
    sample_count: int, relative_mm: tuple[float, float]
) -> Prediction:
    """落下地点を「真値 + 共通オフセット + 最終予測からのずれ」から組む。

    **実装の出力ではなくテスト局所のリテラルから組む**ので、実装が式を変えて
    も期待値は動かない。共通オフセットを乗せてあるため、最終予測との距離
    （収束の基準）と真値との距離（最終誤差）が**別の量として現れる**。
    """
    return Prediction(
        predicted_hit_x_mm=(
            IMPACT_POINT_MM[0] + COMMON_OFFSET_MM[0] + relative_mm[0]
        ),
        predicted_hit_y_mm=(
            IMPACT_POINT_MM[1] + COMMON_OFFSET_MM[1] + relative_mm[1]
        ),
        predicted_hit_time_ms=IMPACT_T_MS + 4.0,
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


def invalid_prediction(
    reason: InvalidReason, *, sample_count: int
) -> InvalidPrediction:
    return InvalidPrediction(
        reason=reason,
        detail="テスト用の無効予測",
        sample_count=sample_count,
        based_on_time_ms=5100.0 + 20.0 * sample_count,
        elapsed_ms=0.5,
        config=CONFIG,
    )


def build_outcomes(
    relative_mm: Sequence[tuple[int, tuple[float, float]]],
) -> tuple[PredictionOutcome, ...]:
    """**先頭と末尾に無効予測を置いた**予測系列を組む。

    末尾の無効予測は絵空事ではない: 対象が落下したあとのサンプルが混ざると
    フィットが成立しなくなる。この形にしてあるので、有効サンプル数を
    「最終予測のサンプル数」で代用した実装が落ちる。
    """
    head = (
        invalid_prediction(InvalidReason.INSUFFICIENT_SAMPLES, sample_count=1),
        invalid_prediction(InvalidReason.INSUFFICIENT_SAMPLES, sample_count=2),
    )
    body = tuple(
        valid_prediction(sample_count, relative)
        for sample_count, relative in relative_mm
    )
    last_sample_count = relative_mm[-1][0]
    tail = tuple(
        invalid_prediction(
            InvalidReason.NO_FUTURE_FLOOR_CROSSING, sample_count=count
        )
        for count in range(last_sample_count + 1, VALID_SAMPLES + 1)
    )
    return head + body + tail


def build_record(
    *,
    relative_mm: Sequence[tuple[int, tuple[float, float]]] = CONVERGING_RELATIVE_MM,
    source: SourceKind = SourceKind.SIMULATED,
    sample_count: int = VALID_SAMPLES,
    record_id: str = RECORD_ID,
) -> ThrowRecord:
    """有効サンプル `sample_count` 件ぶんの記録を組む。"""
    return ThrowRecord(
        record_id=record_id,
        source=source,
        config=CONFIG,
        samples=tuple(
            Sample(
                t_ms=5100.0 + 20.0 * index,
                x_mm=-100.0 + index,
                y_mm=20.0,
                z_mm=900.0 - index,
            )
            for index in range(sample_count)
        ),
        predictions=build_outcomes(relative_mm),
    )


def missing_truth_value() -> TruthValue:
    return TruthValue(
        value=None,
        method=TruthMethod.MISSING,
        uncertainty_mm=None,
        uncertainty_ms=None,
        source="テスト用の欠測",
    )


def build_truth(
    *, impact_point: TruthValue | None = None, record_id: str = RECORD_ID
) -> ThrowTruth:
    """テスト局所のリテラルだけから真値一式を組む。"""
    if impact_point is None:
        impact_point = TruthValue(
            value=IMPACT_POINT_MM,
            method=TruthMethod.MEASURED,
            uncertainty_mm=15.0,
            uncertainty_ms=None,
            source="メジャー実測（テスト）",
        )
    return ThrowTruth(
        record_id=record_id,
        impact_point_world_mm=impact_point,
        impact_time_ms=TruthValue(
            value=IMPACT_T_MS,
            method=TruthMethod.INTERPOLATED,
            uncertainty_mm=None,
            uncertainty_ms=3.0,
            source="観測点の内挿（テスト）",
        ),
        release_time_ms=TruthValue(
            value=5004.0,
            method=TruthMethod.EXTRAPOLATED,
            uncertainty_mm=None,
            uncertainty_ms=20.0,
            source="推定軌道の外挿（テスト）",
        ),
        external_mark_delta_ms=None,
    )


def analyze(
    *,
    relative_mm: Sequence[tuple[int, tuple[float, float]]] = CONVERGING_RELATIVE_MM,
    band_mm: float | None = BAND_MM,
    require_monotonic_tail: bool = True,
    require_live_source: bool = True,
    source: SourceKind = SourceKind.SIMULATED,
    impact_point: TruthValue | None = None,
) -> ConvergenceResult:
    """記録 → 誤差系列 → 収束、という**実際に通る経路**で1件算出する。"""
    record = build_record(relative_mm=relative_mm, source=source)
    accuracy = measure_accuracy(record, build_truth(impact_point=impact_point))
    return analyze_convergence(
        record,
        accuracy,
        settings=build_settings(
            band_mm=band_mm,
            require_monotonic_tail=require_monotonic_tail,
            require_live_source=require_live_source,
        ),
    )


def build_synthetic_record(record_id: str = RECORD_ID) -> ThrowRecord:
    """既知の放物軌道から `ThrowRecord` を組み立てる（合成軌道の段）。

    予測系列は**本物の `ThrowPredictionTracker`** に作らせる。手で書いた
    予測を渡すと、実際に通る経路（`predict()` のフィットと外挿）を検査しない
    ことになる。
    """
    tracker = ThrowPredictionTracker(
        record_id=record_id, source=SourceKind.SIMULATED, config=CONFIG
    )
    t_ms = SYN_FIRST_SAMPLE_T_MS
    until_ms = syn_impact_time_ms() + SYN_DT_MS
    while t_ms <= until_ms:
        x_mm, y_mm, z_mm = syn_position_at(t_ms)
        tracker.add_sample(Sample(t_ms=t_ms, x_mm=x_mm, y_mm=y_mm, z_mm=z_mm))
        t_ms += SYN_DT_MS
    return tracker.to_record()


def synthetic_entry(
    offset_mm: tuple[float, float] = SYN_TRUTH_OFFSET_MM,
) -> dict[str, object]:
    """合成軌道の解析解（＋既知のオフセット）を「実測した落下地点」として渡す。"""
    x_mm, y_mm, z_mm = syn_impact_point_mm()
    return {
        "impact_point_world_mm": [x_mm + offset_mm[0], y_mm + offset_mm[1], z_mm],
        "impact_point_source": "メジャー実測（合成軌道の解析解を実測値とみなす）",
        "impact_point_uncertainty_mm": 15.0,
    }


# ---------------------------------------------------------------------------
# 有効サンプル数（要件 5.7 前半）
# ---------------------------------------------------------------------------


class TestValidSamples:
    """1投擲で得られた**有効サンプル数**を算出する（要件 5.7）。"""

    def test_counts_the_samples_of_the_throw(self) -> None:
        """有効サンプル数は記録に載った観測サンプルの件数である。

        継ぎ目（要件 1.7）が除外規則を通したあとの列が `record.samples` で
        あり、そこに残った件数がそのまま「1投擲で得られた有効サンプル数」で
        ある。
        """
        result = analyze()

        assert result.valid_samples == VALID_SAMPLES

    def test_is_not_the_sample_count_of_the_last_valid_prediction(self) -> None:
        """**最終予測のサンプル数で代用しない。**

        末尾の予測が無効になった投擲では、最終予測のサンプル数（9）は実際に
        得られたサンプル数（11）より小さい。代用すると「取れたサンプルが
        少なかった投擲」に見え、`min_samples` の見直し材料（design.md
        「ConvergenceAnalyzer」Integration）を誤らせる。
        """
        result = analyze()
        last_valid_sample_count = CONVERGING_RELATIVE_MM[-1][0]

        assert last_valid_sample_count != VALID_SAMPLES
        assert result.valid_samples != last_valid_sample_count

    def test_zero_samples_is_a_value_not_an_exception(self) -> None:
        """有効サンプル 0 件（観測の不成立）は**値として**返る。

        design.md「Error Categories and Responses」の「観測の不成立 →
        値として扱う」に従う。例外にすると1投擲の失敗が集計全体を止める。
        """
        record = ThrowRecord(
            record_id=RECORD_ID,
            source=SourceKind.SIMULATED,
            config=CONFIG,
            samples=(),
            predictions=(),
        )
        accuracy = measure_accuracy(record, build_truth())

        result = analyze_convergence(record, accuracy, settings=build_settings())

        assert result.valid_samples == 0
        assert result.converged_at is None
        assert result.judgement.verdict == VERDICT_NOT_MEASURABLE


# ---------------------------------------------------------------------------
# 収束サンプル数（要件 5.7 後半）
# ---------------------------------------------------------------------------


class TestConvergenceRule:
    """収束サンプル数は「最終予測から帯域内に**収まり続ける**最小の N」。"""

    def test_converged_at_is_the_start_of_the_in_band_tail(self) -> None:
        """一度帯域へ入っても出れば収束ではない（末尾条件）。

        置いてある列のずれは 300 / 150 / **50** / 60 / 20 / 20 / 0 mm であり、
        帯域は 50 mm である。5 サンプル目でいったん帯域に触れるが 6 サンプル
        目で 60 mm へ出るので、**帯域内に留まり続けるのは 7 サンプル目以降**
        である。末尾条件を落とした実装は 5 を返して落ちる。
        """
        result = analyze()

        assert result.converged_at == CONVERGED_AT_TAIL

    def test_verdict_is_converged(self) -> None:
        """収束した投擲の判定値は「収束」である。"""
        result = analyze()

        assert result.judgement.verdict == VERDICT_CONVERGED
        assert result.judgement.question == CONVERGENCE_QUESTION

    def test_band_boundary_is_inclusive(self) -> None:
        """帯域**ちょうど**（50.0 mm）は帯域内である。

        末尾条件を外した規則では、最初に帯域へ触れる 5 サンプル目が答えに
        なる。`<` と `<=` を取り違えた実装は 7 を返して落ちる。
        """
        result = analyze(require_monotonic_tail=False)

        assert result.converged_at == CONVERGED_AT_FIRST_TOUCH

    def test_immediately_stable_series_converges_at_the_first_prediction(
        self,
    ) -> None:
        """最初の有効予測から動かない列は、そのサンプル数で収束する。"""
        result = analyze(relative_mm=IMMEDIATE_RELATIVE_MM)

        assert result.converged_at == IMMEDIATE_RELATIVE_MM[0][0]
        assert result.judgement.verdict == VERDICT_CONVERGED

    def test_non_converging_series_returns_not_converged(self) -> None:
        """未収束は**正常な結果**として返る（例外でも欠測でもない）。

        最終予測は自分自身との距離が 0 なので必ず帯域内であり、**それだけを
        根拠に「収束した」と答える実装**は「投擲が終わったから収束した」と
        言っているに等しい。最終予測より前のどの予測も帯域に留まらないこの
        列で、そうした実装が落ちる。
        """
        result = analyze(relative_mm=NON_CONVERGING_RELATIVE_MM)

        assert result.converged_at is None
        assert result.judgement.verdict == VERDICT_NOT_CONVERGED
        # 未収束でも算出は止まらない。有効サンプル数と最終誤差は残る。
        assert result.valid_samples == VALID_SAMPLES
        assert result.final_error_mm == pytest.approx(
            COMMON_OFFSET_NORM_MM, abs=EXACT_TOLERANCE
        )

    def test_non_converging_series_is_not_converged_without_the_tail_rule(
        self,
    ) -> None:
        """末尾条件を外しても、帯域へ一度も入らない列は未収束である。"""
        result = analyze(
            relative_mm=NON_CONVERGING_RELATIVE_MM, require_monotonic_tail=False
        )

        assert result.converged_at is None
        assert result.judgement.verdict == VERDICT_NOT_CONVERGED

    def test_distance_is_measured_from_the_final_prediction_not_the_truth(
        self,
    ) -> None:
        """基準は**その投擲の最終予測**であって真値ではない。

        置いてある列は真値から一律 500 mm ずれている（帯域の 10 倍）。
        真値との誤差で収束を測る実装は、収束列を丸ごと「未収束」と報告して
        落ちる。**投擲内で完結する定義にしてある**理由は research.md
        Decision 8 のとおりで、真値が欠測した投擲でも収束は測れるべきだから
        である。
        """
        result = analyze()

        assert result.final_error_mm == pytest.approx(
            COMMON_OFFSET_NORM_MM, abs=EXACT_TOLERANCE
        )
        assert result.final_error_mm > result.band_mm
        assert result.converged_at == CONVERGED_AT_TAIL


# ---------------------------------------------------------------------------
# 帯域（要件 5.8）
# ---------------------------------------------------------------------------


class TestBand:
    """帯域の既定はレイアウトの暫定許容窓に揃える（要件 5.8）。"""

    def test_default_band_is_the_layout_position_tolerance(self) -> None:
        """`band_mm` 未指定なら開口半径 − 対象寸法/2（= 67.5 mm）を使う。

        期待値はテスト局所のリテラル（開口 200・対象 65）から組む。
        """
        result = analyze(band_mm=None)

        assert result.band_mm == pytest.approx(
            DEFAULT_BAND_MM, abs=EXACT_TOLERANCE
        )

    def test_default_band_changes_the_answer(self) -> None:
        """帯域は**結果に効いている**（申告するだけの飾りではない）。

        既定の 67.5 mm では 60 mm のずれも帯域内に入るので、収束サンプル数は
        7 から 5 へ前倒しになる。帯域を無視して固定値で判定する実装はここで
        落ちる。
        """
        default_band = analyze(band_mm=None)
        explicit_band = analyze(band_mm=BAND_MM)

        assert default_band.converged_at == CONVERGED_AT_DEFAULT_BAND
        assert explicit_band.converged_at == CONVERGED_AT_TAIL

    def test_explicit_band_is_reported_as_used(self) -> None:
        """使った帯域は結果に載る（あとから規則を読み替えられないように）。"""
        result = analyze(band_mm=BAND_MM)

        assert result.band_mm == pytest.approx(BAND_MM, abs=EXACT_TOLERANCE)

    @pytest.mark.parametrize("band_mm", [0.0, -1.0, math.inf, math.nan])
    def test_non_positive_band_is_rejected(self, band_mm: float) -> None:
        """帯域として成立しない値は**設定の誤り**として拒否する。

        黙って受けると、どの投擲も「未収束」という**もっともらしい正常値**に
        なり、設定の誤りが結果の読み違いとして表に出る（design.md
        「Error Categories and Responses」の「設定の誤り」）。
        """
        record = build_record()
        accuracy = measure_accuracy(record, build_truth())

        with pytest.raises(M1ConfigError):
            analyze_convergence(
                record, accuracy, settings=build_settings(band_mm=band_mm)
            )


# ---------------------------------------------------------------------------
# 判定規則の説明文（要件 5.8）
# ---------------------------------------------------------------------------


class TestCriterion:
    """規則の説明文を**結果と同じ場所に**埋め込む（要件 5.8）。"""

    def test_judgement_carries_a_non_empty_criterion(self) -> None:
        """判定値だけを返さない。規則の説明文が必ず伴う。"""
        result = analyze()

        assert result.judgement.criterion.strip() != ""

    def test_criterion_states_the_rule(self) -> None:
        """説明文は規則そのものを述べる（キーワードで固定する）。

        「最終予測を基準にする」「帯域」「未収束が正常な結果である」の3点が
        欠けると、あとから規則を都合よく読み替えられる。
        """
        criterion = analyze().judgement.criterion

        assert "最終予測" in criterion
        assert "未収束" in criterion
        assert "合否条件ではない" in criterion

    def test_criterion_records_the_band_actually_used(self) -> None:
        """説明文には**実際に使った帯域**が入る。

        規則の文だけ残して帯域を伏せると、同じ文で違う判定が正当化できる。
        """
        criterion = analyze(band_mm=BAND_MM).judgement.criterion

        assert f"{BAND_MM:g}" in criterion

    def test_criterion_states_the_degenerate_case_interpretation(self) -> None:
        """縮退ケースの解釈が**説明文そのもの**として載る（要件 5.8）。

        「N 以降のすべての予測が最終予測から帯域内」という規則は、最終予測が
        自分自身との距離 0 で必ず満たすため、**字義どおりでは「未収束」が
        到達不能**になる。そこを「最終予測より前のどの予測も帯域に留まらな
        かった投擲は未収束とする」と定めたのが本タスクで確定した解釈であり、
        **これ自体が「実測前に固定した規則」の一部**である（要件 5.8）。

        振る舞い側は `TestConvergenceRule` の未収束列が固定しているが、記録
        側がこの一文を失うと、**実装は正しいまま criterion だけが嘘になる**
        ——判定値だけが残って規則が失われ、あとから規則のほうを結果に
        合わせて読み替えられる、という要件 5.8 が名指しする壊れ方そのもの
        である。したがって語の断片ではなく、**同じ一文として引ける形**で
        前提と結論の両方を固定する。
        """
        criterion = analyze().judgement.criterion

        assert "最終予測は自分自身との距離が 0 なので必ず条件を満たす" in criterion
        assert (
            "最終予測より前のどの予測も帯域に留まらなかった投擲は「未収束」とする"
            in criterion
        )

    def test_criterion_distinguishes_not_measurable_from_not_converged(self) -> None:
        """判定値の3値の**区別**が記録側にも残る（要件 5.8）。

        「測れなかった」を「収束しなかった」に畳まないことは
        `TestNotMeasurable` が振る舞いとして固定しているが、その区別が規則の
        説明文から落ちると、記録だけを読む人には**2値の規則に見える**——
        真値を書き忘れた投擲を「収束しなかった投擲」として読み替える余地が
        そのまま残る。
        """
        criterion = analyze().judgement.criterion

        assert "「測定不能」であり、未収束とは区別する" in criterion

    def test_criterion_records_which_rule_variant_applied(self) -> None:
        """末尾条件の有無で説明文が変わる（記録と算出が食い違わない）。

        **違うことだけを見ても足りない。** first-touch 側の文面が空同然でも
        `!=` は通ってしまう。実際に適用した規則がどちらなのかを、**互いの
        文面にしか現れない語**で固定する: 末尾条件ありは「収まり続ける」を
        求める規則であり、末尾条件なしは「最初に」帯域へ入った時点を採る
        規則である。取り違えた文面（片方の規則をもう片方に書く）もここで
        落ちる。
        """
        with_tail = analyze(require_monotonic_tail=True).judgement.criterion
        without_tail = analyze(require_monotonic_tail=False).judgement.criterion

        assert with_tail != without_tail

        assert "収まり続ける最小の N" in with_tail
        assert "最初に" not in with_tail

        assert "**最初に**入ったときのサンプル数" in without_tail
        assert "留まり続けることは要求しない" in without_tail
        assert "収まり続ける最小の N" not in without_tail


# ---------------------------------------------------------------------------
# 収束サンプル数と最終誤差の併記（要件 5.7 / 5.8、Decision 8 の Trade-off）
# ---------------------------------------------------------------------------


class TestCoReporting:
    """**収束サンプル数と最終誤差を必ず併記する。**

    最終予測自体がずれていると収束は速く見える（research.md Decision 8 の
    Trade-off）。片方だけを持ち出せる形にすると、収束の速さが予測の正しさ
    として読まれる。
    """

    def test_result_carries_both_numbers(self) -> None:
        result = analyze()

        assert result.converged_at is not None
        assert result.final_error_mm is not None

    def test_rationale_mentions_both_numbers(self) -> None:
        """説明文（`rationale`）に両方が載る。

        レポートが判定値だけを転記しても、**説明文を運べば併記が保たれる**。
        """
        result = analyze()
        rationale = result.judgement.rationale

        assert str(CONVERGED_AT_TAIL) in rationale
        assert f"{COMMON_OFFSET_NORM_MM:g}" in rationale

    def test_evidence_carries_both_numbers(self) -> None:
        """証跡にも両方が載る（数値として後段が引ける形で）。"""
        evidence = analyze().judgement.evidence

        assert evidence["converged_at"] == CONVERGED_AT_TAIL
        assert evidence["final_error_mm"] == pytest.approx(
            COMMON_OFFSET_NORM_MM, abs=EXACT_TOLERANCE
        )
        assert evidence["valid_samples"] == VALID_SAMPLES

    def test_fast_convergence_does_not_imply_a_small_final_error(self) -> None:
        """速い収束と小さい最終誤差は**別の量**である。

        置いてある列は 7 サンプル目で収束するが、最終誤差は帯域の 10 倍
        （500 mm）ある。両者を同じ結果から読めることが要件 5.7 の求める形で
        ある。
        """
        result = analyze()

        assert result.converged_at == CONVERGED_AT_TAIL
        assert result.final_error_mm == pytest.approx(
            COMMON_OFFSET_NORM_MM, abs=EXACT_TOLERANCE
        )


# ---------------------------------------------------------------------------
# 測れない場合（真値の欠測・有効予測なし）
# ---------------------------------------------------------------------------


class TestNotMeasurable:
    """誤差系列が空なら「測れなかった」と返す（未収束と混ぜない）。"""

    def test_missing_impact_point_is_not_reported_as_not_converged(self) -> None:
        """真値の欠測は**未収束ではない**。

        混ぜると、真値を書き忘れた投擲が「収束しなかった投擲」として集計へ
        入り、収束サンプル数の分布を悪い方へ引っ張る。
        """
        result = analyze(impact_point=missing_truth_value())

        assert result.judgement.verdict == VERDICT_NOT_MEASURABLE
        assert result.converged_at is None
        assert result.final_error_mm is None

    def test_valid_samples_survive_a_missing_truth(self) -> None:
        """真値が欠測でも**有効サンプル数は測れている**（0 で埋めない）。"""
        result = analyze(impact_point=missing_truth_value())

        assert result.valid_samples == VALID_SAMPLES


# ---------------------------------------------------------------------------
# 記録と誤差系列の取り違え
# ---------------------------------------------------------------------------


class TestMismatchedInputs:
    """別の投擲の誤差系列を渡されたら拒否する。"""

    def test_sample_count_beyond_the_record_is_rejected(self) -> None:
        """記録のサンプル数を超える誤差は、その記録のものではありえない。

        取り違えたまま算出すると「11 サンプルのうち 14 サンプル目で収束」と
        いう読めない結果が集計へ流れる（`measure_accuracy()` が record_id の
        取り違えを拒否するのと同じ理由）。
        """
        record = build_record()
        stray = PredictionError(
            sample_count=VALID_SAMPLES + 3,
            based_on_time_ms=5400.0,
            hit_error_mm=(1.0, 2.0),
            hit_error_norm_mm=math.hypot(1.0, 2.0),
            time_error_ms=1.0,
            residual_mm=2.0,
            remaining_time_ms=100.0,
        )
        accuracy = AccuracyResult(
            errors=(stray,), first_valid=stray, final=stray, invalid_counts=()
        )

        with pytest.raises(M1ConfigError):
            analyze_convergence(record, accuracy, settings=build_settings())


# ---------------------------------------------------------------------------
# 暫定の印
# ---------------------------------------------------------------------------


class TestProvisional:
    """実機由来でない投擲の判断には暫定の印が付く（要件 9.10 の趣旨）。"""

    def test_simulated_throw_is_provisional(self) -> None:
        result = analyze(source=SourceKind.SIMULATED)

        assert result.judgement.provisional is True

    def test_live_throw_is_not_provisional(self) -> None:
        result = analyze(source=SourceKind.LIVE)

        assert result.judgement.provisional is False

    def test_provisional_follows_the_setting(self) -> None:
        """実機を要求しない設定なら、合成入力でも印は付かない。"""
        result = analyze(source=SourceKind.SIMULATED, require_live_source=False)

        assert result.judgement.provisional is False


# ---------------------------------------------------------------------------
# 結果の形
# ---------------------------------------------------------------------------


class TestResultShape:
    def test_result_is_immutable(self) -> None:
        """結果は不変である（算出後に書き換えられない）。"""
        result = analyze()

        with pytest.raises(FrozenInstanceError):
            result.converged_at = 1  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 合成軌道の段
# ---------------------------------------------------------------------------


class TestSyntheticTrajectory:
    """既知の放物軌道を端から端まで通す。"""

    def test_exact_parabola_converges_at_the_first_prediction(self) -> None:
        """厳密な放物線ではフィットが厳密解になり、予測が動かない。

        3 サンプル目で予測が確定するので、収束サンプル数は 3 である。
        """
        record = build_synthetic_record()
        truth = derive_truth(record, synthetic_entry(), layout=build_layout())
        accuracy = measure_accuracy(record, truth)

        result = analyze_convergence(
            record, accuracy, settings=build_settings(band_mm=None)
        )

        assert result.converged_at == SYN_FIRST_PREDICTION_SAMPLES
        assert result.judgement.verdict == VERDICT_CONVERGED

    def test_valid_samples_match_the_record(self) -> None:
        record = build_synthetic_record()
        truth = derive_truth(record, synthetic_entry(), layout=build_layout())
        accuracy = measure_accuracy(record, truth)

        result = analyze_convergence(
            record, accuracy, settings=build_settings(band_mm=None)
        )

        assert result.valid_samples == len(record.samples)
        assert result.valid_samples > SYN_FIRST_PREDICTION_SAMPLES

    def test_offset_truth_keeps_convergence_fast_but_error_large(self) -> None:
        """**最終予測が帯域より大きくずれていても収束は速く出る。**

        真値へ 180 mm のオフセットを注入してある（帯域の既定は 67.5 mm）。
        収束は 3 サンプル目のまま、最終誤差は 180 mm として出る——これが
        research.md Decision 8 の Trade-off であり、**併記が要る理由**その
        ものである。真値との距離で収束を測る実装は、ここで「未収束」を返して
        落ちる。
        """
        record = build_synthetic_record()
        truth = derive_truth(record, synthetic_entry(), layout=build_layout())
        accuracy = measure_accuracy(record, truth)

        result = analyze_convergence(
            record, accuracy, settings=build_settings(band_mm=None)
        )

        assert result.converged_at == SYN_FIRST_PREDICTION_SAMPLES
        assert result.final_error_mm == pytest.approx(
            SYN_TRUTH_OFFSET_NORM_MM, abs=SYN_TOLERANCE_MM
        )
        assert result.final_error_mm > result.band_mm
