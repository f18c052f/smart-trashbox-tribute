"""実測項目 4 / 5 の算出の検証（タスク 4.3、要件 5.4, 5.5）。

観測可能な完了状態（tasks.md 4.3）を固定する:

- **既知の真値に対して誤差系列が解析解と一致する**
- **無効予測が理由ごとに計数される**

あわせて design.md「AccuracyMetrics」が定める点も固定する:

- 誤差を**スカラーではなくベクトルとして保持する**（帰属が向きを使う。要件 6.3）
- 各要素に**基づいたサンプル数・観測時刻・残差・残り時間**が併記される
- 真値が欠測なら誤差も欠測（**0 で埋めない**）

**検査を2段に分けた理由。** タスク 4.2 と同じである。誤差は「差」であり、
合成軌道だけで検査すると**式の取り違えが軌道の数値に紛れて見えなくなる**。
とくに本モジュールでは、合成軌道の予測が真値とほぼ一致してしまうため
`予測 − 実測` と `実測 − 予測` の**区別がつかなくなる**（誤差がどちらも
ほぼ 0 になる）。tasks.md「Implementation Notes」タスク4.2 が記録している
とおり、「解析解と一致」の検査は**区別すべき差が実際に現れる入力**が要る。

1. **代数の段**（`TestHitError` / `TestTimeError` / `TestCompanionValues` /
   `TestInvalidPredictions` / `TestMissingTruth`）: 真値と予測を**テスト局所の
   リテラル**で組み、結果を厳密な期待値と突き合わせる。符号の反転・x と y の
   取り違え・水平誤差を3次元にする誤り・被減数と減数の取り違え・無効予測の
   数え落としが、そのまま差として現れる。
2. **合成軌道の段**（`TestSyntheticTrajectory`）: 既知の放物軌道から
   `ThrowPredictionTracker` に記録を作らせ、`derive_truth()` が返す本物の
   真値を通して解析解と突き合わせる。**向きの検査には既知のオフセットを
   注入した真値を使う**——真値を解析解ちょうどに置いた検査は誤差が 0 に
   なるので、符号を反転した実装でも通ってしまうからである。

**参照解は実装の定数に触れない。** 床面高さは本ファイルの `FLOOR_Z_MM`
（リテラルの 0.0）から組み、`truth.FLOOR_HEIGHT_MM` を参照しない。実装の
定数で参照解を組むと、**その定数を変えたときに参照解が一緒に動いて差が
消える**（tasks.md「Implementation Notes」タスク4.1）。

**許容差の根拠。**

- **代数の段**: すべて有限桁の四則演算なので**厳密に一致するのが正しい**。
  `EXACT_TOLERANCE`（1e-9）は丸めのためだけの余裕であり、式を誤れば
  数十〜数百 mm 規模でずれる。
- **合成軌道の段（落下地点）**: サンプルが厳密に放物線上にあり重力を固定して
  当てているので、フィットは**厳密解**である。残るのは浮動小数の条件数だけで
  あり、`SYN_FIT_TOLERANCE_MM`（1e-3 mm）はその余裕である。**実装が申告した
  不確かさを許容差に使わない**——それは過小申告しか捕まえられず、上限側に
  対して無力である（tasks.md タスク4.1）。注入するオフセットは 37 mm / 21 mm
  であり、許容差より4桁大きい。
- **合成軌道の段（落下時刻）**: 真値の落下時刻は隣接2点の**線形**内挿なので、
  予測（解析解）とは原理的に一致せず、弦は放物線の下を通るため真値が**必ず
  早い側**に出る。したがって時刻誤差は**必ず正**であり、その上界は
  **テスト定数だけから組み立てた絶対量**（`syn_interpolation_bound_ms()`）で
  ある。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import FrozenInstanceError
from itertools import pairwise

import pytest

from m1_validation.errors import M1ConfigError
from m1_validation.layout import ThrowLayout
from m1_validation.metrics.accuracy import (
    AccuracyResult,
    PredictionError,
    measure_accuracy,
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

RECORD_ID = "throw-0042"


# --- 代数の段で使う既知の値（すべてテスト局所のリテラル）-------------------
#
# 基準になる量に 0 を置かない（tasks.md タスク2.2 / 3.1 の Implementation
# Notes）。誤差は差なので、真値が 0 だと「予測をそのまま返す実装」と
# 区別できなくなる。

#: 実際の落下地点（World mm）。**z を 0 でない 12.0 mm にしてある**——
#: 水平誤差を3次元距離に取り違えた実装がここで落ちるようにするためである。
IMPACT_POINT_MM = (1050.0, 1120.0, 12.0)
#: 実際の落下時刻（ms）。
IMPACT_T_MS = 5793.0

#: 3件の有効予測それぞれの誤差ベクトル（**予測 − 実測**）と時刻差。
#: x と y で大きさも符号も変え、**成分の取り違えと符号の反転が差として
#: 現れる**ようにしてある。ノルムは 500 / 100 / 15 の厳密値になる
#: （3-4-5 の 100 倍・20 倍・3-4-5 の 3 倍）。
EXPECTED_HIT_ERRORS_MM = ((300.0, -400.0), (-60.0, 80.0), (9.0, 12.0))
EXPECTED_HIT_ERROR_NORMS_MM = (500.0, 100.0, 15.0)
EXPECTED_TIME_ERRORS_MS = (18.0, -7.0, 2.0)

#: 各予測に併記される値（サンプル数・観測時刻・残差・残り時間）。
#: **要素ごとにすべて別の値**にしてある——1つでも取り違えた実装（前の要素の
#: 値を運ぶ・添字をずらす）が落ちるようにするためである。
EXPECTED_SAMPLE_COUNTS = (3, 4, 5)
EXPECTED_BASED_ON_TIMES_MS = (5150.0, 5220.0, 5310.0)
EXPECTED_RESIDUALS_MM = (4.5, 2.25, 1.5)
EXPECTED_REMAINING_TIMES_MS = (661.0, 566.0, 485.0)


# --- 合成軌道の段で使う既知の軌道 -------------------------------------------

#: 床面の高さ（mm）。**実装の `truth.FLOOR_HEIGHT_MM` を参照せず、ここに
#: リテラルで置く**（モジュール docstring 参照）。
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

#: フィットの条件数だけに由来する許容差（モジュール docstring 参照）。
SYN_FIT_TOLERANCE_MM = 1e-3

#: 合成軌道の段で真値へ注入する既知のオフセット（World mm の水平2成分）。
#: **許容差より4桁大きく、x と y で大きさも符号も違う**——これによって
#: 「誤差ベクトルの向き」が合成軌道の段でも実際に差として現れる。
SYN_TRUTH_OFFSET_MM = (37.0, -21.0)


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


def syn_interpolation_bound_ms() -> float:
    """線形内挿の落下時刻が解析解より早く出る量の**上界**（ms）。

    テスト定数だけから組む。弦と放物線が区間内で離れる最大量は `g dt^2 / 8`
    （中点で最大）であり、それを接地時の降下速度で時間へ換算する。実装は弦の
    傾きで割るので厳密には一致しないため 1.1 倍の余裕を持たせる——それでも
    `dt^2/8` を `dt^2/6` と取り違えた実装（+33%）は落ちる
    （`test_m1_metrics_flight.py` と同じ組み立て）。
    """
    descent_speed_mm_ms = abs(
        SYN_VZ_MM_MS - G_MM_MS2 * (syn_impact_time_ms() - SYN_RELEASE_T_MS)
    )
    return 1.1 * (G_MM_MS2 * SYN_DT_MS**2 / 8.0) / descent_speed_mm_ms


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
        "object_diameter_mm": 65.0,
        "aperture_diameter_mm": 200.0,
        "camera_position_world_mm": (0.0, -1500.0, 1000.0),
        "notes": "仮値。確定ではない。",
    }
    values.update(overrides)
    return ThrowLayout(**values)  # type: ignore[arg-type]


def missing_truth() -> TruthValue:
    return TruthValue(
        value=None,
        method=TruthMethod.MISSING,
        uncertainty_mm=None,
        uncertainty_ms=None,
        source="テスト用の欠測",
    )


def build_truth(
    *,
    impact_point: TruthValue | None = None,
    impact_time: TruthValue | None = None,
    record_id: str = RECORD_ID,
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
    if impact_time is None:
        impact_time = TruthValue(
            value=IMPACT_T_MS,
            method=TruthMethod.INTERPOLATED,
            uncertainty_mm=None,
            uncertainty_ms=3.0,
            source="観測点の内挿（テスト）",
        )
    return ThrowTruth(
        record_id=record_id,
        impact_point_world_mm=impact_point,
        impact_time_ms=impact_time,
        release_time_ms=TruthValue(
            value=5004.0,
            method=TruthMethod.EXTRAPOLATED,
            uncertainty_mm=None,
            uncertainty_ms=20.0,
            source="推定軌道の外挿（テスト）",
        ),
        external_mark_delta_ms=None,
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


def valid_prediction(index: int) -> Prediction:
    """`index` 番目（0 起点）の有効予測を、期待値のリテラルから逆算して組む。

    落下地点は「真値 + 期待する誤差ベクトル」、落下時刻は「真値 + 期待する
    時刻差」である。**実装の出力ではなくテスト局所のリテラルから組む**ので、
    実装が式を変えても期待値は動かない。
    """
    dx_mm, dy_mm = EXPECTED_HIT_ERRORS_MM[index]
    return Prediction(
        predicted_hit_x_mm=IMPACT_POINT_MM[0] + dx_mm,
        predicted_hit_y_mm=IMPACT_POINT_MM[1] + dy_mm,
        predicted_hit_time_ms=IMPACT_T_MS + EXPECTED_TIME_ERRORS_MS[index],
        remaining_time_ms=EXPECTED_REMAINING_TIMES_MS[index],
        estimated_vx_mm_s=3000.0,
        estimated_vy_mm_s=-500.0,
        estimated_vz_mm_s=1020.0,
        residual=EXPECTED_RESIDUALS_MM[index],
        trajectory=trajectory(),
        sample_count=EXPECTED_SAMPLE_COUNTS[index],
        based_on_time_ms=EXPECTED_BASED_ON_TIMES_MS[index],
        elapsed_ms=1.25,
        config=CONFIG,
    )


def invalid_prediction(
    reason: InvalidReason, *, sample_count: int, based_on_time_ms: float | None
) -> InvalidPrediction:
    return InvalidPrediction(
        reason=reason,
        detail="テスト用の無効予測",
        sample_count=sample_count,
        based_on_time_ms=based_on_time_ms,
        elapsed_ms=0.5,
        config=CONFIG,
    )


#: 代数の段の予測系列。**無効予測を先頭・中間の両方へ置く**——先頭だけを
#: 読み飛ばす実装（無効が続く間だけスキップする）が落ちるようにするため。
def build_outcomes() -> tuple[PredictionOutcome, ...]:
    return (
        invalid_prediction(
            InvalidReason.INSUFFICIENT_SAMPLES, sample_count=1, based_on_time_ms=5100.0
        ),
        invalid_prediction(
            InvalidReason.INSUFFICIENT_SAMPLES, sample_count=2, based_on_time_ms=5120.0
        ),
        valid_prediction(0),
        valid_prediction(1),
        invalid_prediction(
            InvalidReason.NO_FUTURE_FLOOR_CROSSING,
            sample_count=6,
            based_on_time_ms=5400.0,
        ),
        valid_prediction(2),
    )


def build_record(
    *,
    predictions: Sequence[PredictionOutcome] | None = None,
    record_id: str = RECORD_ID,
) -> ThrowRecord:
    """予測系列だけが意味を持つ最小の記録（代数の段で使う）。"""
    if predictions is None:
        predictions = build_outcomes()
    return ThrowRecord(
        record_id=record_id,
        source=SourceKind.SIMULATED,
        config=CONFIG,
        samples=tuple(
            Sample(t_ms=t_ms, x_mm=-100.0 + index, y_mm=20.0, z_mm=900.0 - index)
            for index, t_ms in enumerate((5100.0, 5150.0, 5220.0, 5310.0))
        ),
        predictions=tuple(predictions),
    )


def build_synthetic_record(record_id: str = RECORD_ID) -> ThrowRecord:
    """既知の放物軌道から `ThrowRecord` を組み立てる（合成軌道の段）。

    予測系列は**本物の `ThrowPredictionTracker`** に作らせる。手で書いた予測を
    渡すと、実際に通る経路（`predict()` のフィットと外挿）を検査しないことに
    なる（`test_m1_metrics_flight.py` と同じ方針）。最小サンプル数に満たない
    間の呼び出しは `INSUFFICIENT_SAMPLES` の無効予測として系列に載るので、
    **無効予測の計数も本物の値で検査できる**。
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


def synthetic_entry(offset_mm: tuple[float, float] = (0.0, 0.0)) -> dict[str, object]:
    """合成軌道の解析解（＋既知のオフセット）を「実測した落下地点」として渡す。"""
    x_mm, y_mm, z_mm = syn_impact_point_mm()
    return {
        "impact_point_world_mm": [x_mm + offset_mm[0], y_mm + offset_mm[1], z_mm],
        "impact_point_source": "メジャー実測（合成軌道の解析解を実測値とみなす）",
        "impact_point_uncertainty_mm": 15.0,
    }


# ---------------------------------------------------------------------------
# 項目4: 落下地点の誤差（要件 5.4）
# ---------------------------------------------------------------------------


class TestHitError:
    """予測落下地点 − 実際の落下地点を、**ベクトル**の系列として返す。"""

    def test_series_has_one_element_per_valid_prediction(self) -> None:
        """系列は**予測が更新されるたび**の要素を持つ（要件 5.4）。

        無効予測は除かれるので、6件の予測のうち有効な3件だけが載る。
        最終値だけを返す実装（系列にしない実装）はここで落ちる。
        """
        result = measure_accuracy(build_record(), build_truth())

        assert len(result.errors) == len(EXPECTED_HIT_ERRORS_MM)

    def test_error_is_a_vector_not_a_scalar(self) -> None:
        """誤差を**ベクトルとして保持する**（design.md「AccuracyMetrics」）。

        帰属（要件 6.3）は偏りの**向き**が World 座標系に固定されているか
        カメラ視線方向に沿っているかを判別する。スカラーの大きさだけを
        持つ設計にすると、その判別ができなくなり、**「予測が悪い」という
        一つの症状に潰れる**——本 Spec が避けようとしている事態そのもので
        ある。
        """
        result = measure_accuracy(build_record(), build_truth())

        for error, expected in zip(
            result.errors, EXPECTED_HIT_ERRORS_MM, strict=True
        ):
            assert error.hit_error_mm == pytest.approx(
                expected, abs=EXACT_TOLERANCE
            )

    def test_error_points_from_the_truth_to_the_prediction(self) -> None:
        """向きは **予測 − 実測** である（design.md のコメント）。

        符号を反転した実装（実測 − 予測）は、帰属で偏りの向きを**逆に**
        報告する。x と y で符号が違う値を置いてあるので、成分の取り違えも
        同時に落ちる。
        """
        result = measure_accuracy(build_record(), build_truth())
        first = result.errors[0]

        assert first.hit_error_mm[0] == pytest.approx(300.0, abs=EXACT_TOLERANCE)
        assert first.hit_error_mm[1] == pytest.approx(-400.0, abs=EXACT_TOLERANCE)

    def test_norm_is_the_horizontal_distance(self) -> None:
        """ノルムは**水平**距離である（要件 5.4「水平距離の誤差」）。

        真値の z を 12.0 mm にしてあるので、3次元距離を返す実装は
        sqrt(500^2 + 12^2) = 500.144 mm になり落ちる。
        """
        result = measure_accuracy(build_record(), build_truth())

        for error, expected in zip(
            result.errors, EXPECTED_HIT_ERROR_NORMS_MM, strict=True
        ):
            assert error.hit_error_norm_mm == pytest.approx(
                expected, abs=EXACT_TOLERANCE
            )

    def test_norm_is_consistent_with_the_vector(self) -> None:
        """ノルムとベクトルが食い違わない（片方だけ壊れた実装を捕まえる）。"""
        result = measure_accuracy(build_record(), build_truth())

        for error in result.errors:
            assert error.hit_error_norm_mm == pytest.approx(
                math.hypot(*error.hit_error_mm), abs=EXACT_TOLERANCE
            )

    def test_series_keeps_the_prediction_order(self) -> None:
        """系列の順序は予測の生成順である。

        誤差が 500 → 100 → 15 と減る列を置いてあるので、逆順に組み立てた
        実装は「収束していない」ように見える形で落ちる。
        """
        result = measure_accuracy(build_record(), build_truth())

        assert [error.hit_error_norm_mm for error in result.errors] == pytest.approx(
            list(EXPECTED_HIT_ERROR_NORMS_MM), abs=EXACT_TOLERANCE
        )

    def test_first_valid_and_final_are_the_ends_of_the_series(self) -> None:
        """`first_valid` / `final` は系列の両端である。

        **`final` は最後の予測ではなく最後の「有効な」予測**である。系列の
        最後の要素の後ろに無効予測を置いた列でも成り立つ必要があるが、
        ここでは無効を中間に置いて「無効を final にしない」ことを固定する。
        """
        result = measure_accuracy(build_record(), build_truth())

        assert result.first_valid is result.errors[0]
        assert result.final is result.errors[-1]
        assert result.first_valid is not None
        assert result.final is not None
        assert result.first_valid.hit_error_norm_mm == pytest.approx(
            EXPECTED_HIT_ERROR_NORMS_MM[0], abs=EXACT_TOLERANCE
        )
        assert result.final.hit_error_norm_mm == pytest.approx(
            EXPECTED_HIT_ERROR_NORMS_MM[-1], abs=EXACT_TOLERANCE
        )

    def test_final_is_the_last_valid_prediction_even_when_an_invalid_follows(
        self,
    ) -> None:
        """末尾が無効予測でも `final` は**最後の有効予測**である。"""
        outcomes = (
            *build_outcomes(),
            invalid_prediction(
                InvalidReason.NO_FUTURE_FLOOR_CROSSING,
                sample_count=7,
                based_on_time_ms=5500.0,
            ),
        )

        result = measure_accuracy(
            build_record(predictions=outcomes), build_truth()
        )

        assert result.final is not None
        assert result.final.sample_count == EXPECTED_SAMPLE_COUNTS[-1]


# ---------------------------------------------------------------------------
# 項目5: 落下時刻の誤差（要件 5.5）
# ---------------------------------------------------------------------------


class TestTimeError:
    """予測落下時刻 − 実際の落下時刻を、同じ系列として返す（要件 5.5）。"""

    def test_is_predicted_time_minus_actual_time(self) -> None:
        """厳密な期待値と一致する。

        被減数と減数を取り違えた実装は符号ごと落ちる。**落下地点の誤差と
        同じ向きの規約**（予測 − 実測）にそろえてある——片方だけ逆にすると、
        「予測が手前かつ遅い」といった読み方が成り立たなくなる。
        """
        result = measure_accuracy(build_record(), build_truth())

        assert [error.time_error_ms for error in result.errors] == pytest.approx(
            list(EXPECTED_TIME_ERRORS_MS), abs=EXACT_TOLERANCE
        )

    def test_keeps_the_sign(self) -> None:
        """絶対値にしない。**早いか遅いか**は帰属と時間予算の両方で意味を持つ。"""
        result = measure_accuracy(build_record(), build_truth())

        assert result.errors[0].time_error_ms == pytest.approx(
            18.0, abs=EXACT_TOLERANCE
        )
        assert result.errors[1].time_error_ms == pytest.approx(
            -7.0, abs=EXACT_TOLERANCE
        )


# ---------------------------------------------------------------------------
# 併記される値（tasks.md 4.3「サンプル数・観測時刻・残差・残り時間」）
# ---------------------------------------------------------------------------


class TestCompanionValues:
    """各要素に、基づいたサンプル数・観測時刻・残差・残り時間を併記する。

    誤差だけでは「何サンプルの段階の誤差か」が分からず、収束の判定
    （タスク 4.4）にも帰属（タスク 5.2）にも使えない。
    """

    def test_carries_the_sample_count_of_each_prediction(self) -> None:
        result = measure_accuracy(build_record(), build_truth())

        assert [error.sample_count for error in result.errors] == list(
            EXPECTED_SAMPLE_COUNTS
        )

    def test_carries_the_observation_time_of_each_prediction(self) -> None:
        """観測時刻は `Prediction.based_on_time_ms`（使用サンプルの最新時刻）。"""
        result = measure_accuracy(build_record(), build_truth())

        assert [
            error.based_on_time_ms for error in result.errors
        ] == pytest.approx(list(EXPECTED_BASED_ON_TIMES_MS), abs=EXACT_TOLERANCE)

    def test_carries_the_residual_of_each_prediction(self) -> None:
        """残差は `Prediction.residual`（単位は **mm**）。

        帰属（要件 6.7）は「ばらつきが観測由来の範囲を超え、かつフィットの
        残差が大きい」場合をモデル由来と判定する。残差を落とすと、その分岐が
        誤差の系列と突き合わせられなくなる。
        """
        result = measure_accuracy(build_record(), build_truth())

        assert [error.residual_mm for error in result.errors] == pytest.approx(
            list(EXPECTED_RESIDUALS_MM), abs=EXACT_TOLERANCE
        )

    def test_carries_the_remaining_time_of_each_prediction(self) -> None:
        """残り時間は `Prediction.remaining_time_ms`（落下時刻 − 観測時刻）。

        **移動体に残された時間**（要件の冒頭「M2 / M3」）そのものであり、
        予測が早い段階で成立するほど大きい。誤差と併記して初めて
        「どれだけの持ち時間でどれだけの精度が出ているか」が読める。
        """
        result = measure_accuracy(build_record(), build_truth())

        assert [
            error.remaining_time_ms for error in result.errors
        ] == pytest.approx(list(EXPECTED_REMAINING_TIMES_MS), abs=EXACT_TOLERANCE)

    def test_does_not_recompute_the_remaining_time_from_the_truth(self) -> None:
        """残り時間は**予測が申告した値**であり、真値から作り直さない。

        真値の落下時刻から作ると「予測時点で分かっていたはずの持ち時間」
        ではなくなり、レイテンシ予算の材料として意味が変わる。真値の落下
        時刻を動かしても残り時間は動かない。
        """
        shifted = TruthValue(
            value=IMPACT_T_MS + 250.0,
            method=TruthMethod.INTERPOLATED,
            uncertainty_mm=None,
            uncertainty_ms=3.0,
            source="観測点の内挿（テスト・ずらした落下時刻）",
        )

        result = measure_accuracy(build_record(), build_truth(impact_time=shifted))

        assert [
            error.remaining_time_ms for error in result.errors
        ] == pytest.approx(list(EXPECTED_REMAINING_TIMES_MS), abs=EXACT_TOLERANCE)


# ---------------------------------------------------------------------------
# 無効予測（tasks.md 4.3「系列から除き、理由ごとに数える」）
# ---------------------------------------------------------------------------


class TestInvalidPredictions:
    """無効な予測は系列から除き、**理由ごとに数える**。

    数えるのは、**無効が多い投擲を「たまたま予測が悪い投擲」と取り違えない**
    ためである（`SampleReject` の件数を残すのと同じ理由）。理由の語彙は
    `prediction_core.InvalidReason` をそのまま使い、**本 Spec で再定義しない**。
    """

    def test_invalid_predictions_are_excluded_from_the_series(self) -> None:
        """無効予測は誤差系列に載らない。

        `InvalidPrediction` は落下地点のフィールドを**意図的に持たない**
        （prediction_core 要件 6.7）。0 で埋めて系列に載せると、**誤った
        目標座標が誤差として集計へ流れる**。
        """
        result = measure_accuracy(build_record(), build_truth())

        assert len(result.errors) == 3
        assert all(error.sample_count in EXPECTED_SAMPLE_COUNTS for error in result.errors)

    def test_counts_invalid_predictions_per_reason(self) -> None:
        """理由ごとの件数が合う（同じ理由が複数回でも積み上がる）。"""
        result = measure_accuracy(build_record(), build_truth())

        assert dict(result.invalid_counts) == {
            InvalidReason.INSUFFICIENT_SAMPLES: 2,
            InvalidReason.NO_FUTURE_FLOOR_CROSSING: 1,
        }

    def test_each_reason_appears_at_most_once(self) -> None:
        """理由は重複しない（同じ理由が2行に分かれない）。"""
        result = measure_accuracy(build_record(), build_truth())

        reasons = [reason for reason, _ in result.invalid_counts]
        assert len(reasons) == len(set(reasons))

    def test_reports_no_counts_when_every_prediction_is_valid(self) -> None:
        """無効が無ければ件数は空である（0 の行をでっち上げない）。"""
        outcomes = (valid_prediction(0), valid_prediction(1), valid_prediction(2))

        result = measure_accuracy(
            build_record(predictions=outcomes), build_truth()
        )

        assert result.invalid_counts == ()
        assert len(result.errors) == 3

    def test_excludes_and_counts_a_non_finite_prediction(self) -> None:
        """非有限値を含む予測は系列から除き、`NON_FINITE_VALUE` として数える。

        `prediction_core` は非有限の算出結果を `InvalidPrediction` にするので
        本来ここへ来ないが、**記録は JSON を経由して復元されうる**
        （`ThrowRecord.from_dict` は非有限値を拒否しない）。NaN をそのまま
        誤差にすると **NaN が「測れた値」として集計へ流れ込む**——
        design.md「Data Models」の「NaN / Infinity は欠測として表す」に反する。
        理由は `prediction_core` の語彙をそのまま使い、本 Spec で新しい理由を
        作らない。
        """
        broken = Prediction(
            predicted_hit_x_mm=math.nan,
            predicted_hit_y_mm=IMPACT_POINT_MM[1],
            predicted_hit_time_ms=IMPACT_T_MS,
            remaining_time_ms=500.0,
            estimated_vx_mm_s=3000.0,
            estimated_vy_mm_s=-500.0,
            estimated_vz_mm_s=1020.0,
            residual=1.0,
            trajectory=trajectory(),
            sample_count=9,
            based_on_time_ms=5350.0,
            elapsed_ms=1.0,
            config=CONFIG,
        )

        result = measure_accuracy(
            build_record(predictions=(*build_outcomes(), broken)), build_truth()
        )

        assert len(result.errors) == 3
        assert dict(result.invalid_counts)[InvalidReason.NON_FINITE_VALUE] == 1


# ---------------------------------------------------------------------------
# 真値の欠測（design.md「Error Categories and Responses」: 値として扱う）
# ---------------------------------------------------------------------------


class TestMissingTruth:
    """真値が欠測なら誤差も欠測。**0 で埋めない**。"""

    def test_no_series_when_the_impact_point_is_missing(self) -> None:
        """落下地点が未記入なら誤差系列は空である（要件 4.6 / 5.4）。

        「誤差 0」として載せると、**誤差が無かった投擲**として集計へ入り、
        代表値を良い方へ引っ張る。
        """
        result = measure_accuracy(
            build_record(), build_truth(impact_point=missing_truth())
        )

        assert result.errors == ()
        assert result.first_valid is None
        assert result.final is None

    def test_still_counts_invalid_predictions_when_the_truth_is_missing(self) -> None:
        """真値が欠測でも無効予測の計数は止まらない。

        無効の理由は真値と無関係に決まっており、真値の記入待ちの投擲でも
        「予測が成立しなかった理由」は報告に値する。
        """
        result = measure_accuracy(
            build_record(), build_truth(impact_point=missing_truth())
        )

        assert dict(result.invalid_counts) == {
            InvalidReason.INSUFFICIENT_SAMPLES: 2,
            InvalidReason.NO_FUTURE_FLOOR_CROSSING: 1,
        }

    def test_hit_error_survives_when_only_the_impact_time_is_missing(self) -> None:
        """落下時刻が欠測でも**項目4 は残る**（欠測は項目ごとに閉じる）。

        要件 4.6。1件の欠測で他項目の算出を止めない。
        """
        result = measure_accuracy(
            build_record(), build_truth(impact_time=missing_truth())
        )

        assert len(result.errors) == 3
        assert [error.time_error_ms for error in result.errors] == [None, None, None]
        assert result.errors[0].hit_error_mm == pytest.approx(
            EXPECTED_HIT_ERRORS_MM[0], abs=EXACT_TOLERANCE
        )

    def test_treats_a_non_finite_truth_time_as_missing(self) -> None:
        """真値の落下時刻が非有限なら欠測として扱う（NaN を差にしない）。"""
        nan_time = TruthValue(
            value=math.nan,
            method=TruthMethod.INTERPOLATED,
            uncertainty_mm=None,
            uncertainty_ms=3.0,
            source="観測点の内挿（テスト・非有限）",
        )

        result = measure_accuracy(
            build_record(), build_truth(impact_time=nan_time)
        )

        assert [error.time_error_ms for error in result.errors] == [None, None, None]

    def test_treats_a_non_finite_truth_point_as_missing(self) -> None:
        """真値の落下地点が非有限なら欠測として扱う。"""
        nan_point = TruthValue(
            value=(math.nan, IMPACT_POINT_MM[1], IMPACT_POINT_MM[2]),
            method=TruthMethod.MEASURED,
            uncertainty_mm=15.0,
            uncertainty_ms=None,
            source="メジャー実測（テスト・非有限）",
        )

        result = measure_accuracy(
            build_record(), build_truth(impact_point=nan_point)
        )

        assert result.errors == ()

    def test_reports_an_empty_result_for_a_record_without_predictions(self) -> None:
        """予測が1件も無い記録でも例外にしない（観測の不成立は値である）。"""
        result = measure_accuracy(build_record(predictions=()), build_truth())

        assert result.errors == ()
        assert result.invalid_counts == ()
        assert result.first_valid is None
        assert result.final is None


class TestMismatchedTruthIsRejected:
    """別の投擲の真値で測らない。"""

    def test_rejects_a_truth_for_another_record(self) -> None:
        """記録と真値の `record_id` が違えば拒否する（`measure_flight` と同じ）。

        取り違えた真値で測ると、**別の投擲の誤差**を本投擲の実測値として
        報告することになる。黙って数値を返してはならない。
        """
        with pytest.raises(M1ConfigError):
            measure_accuracy(build_record(), build_truth(record_id="throw-9999"))


class TestResultIsImmutable:
    """結果が後から書き換わらないこと。"""

    def test_result_types_are_frozen(self) -> None:
        result = measure_accuracy(build_record(), build_truth())

        with pytest.raises(FrozenInstanceError):
            result.errors[0].hit_error_norm_mm = 0.0  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            result.first_valid = None  # type: ignore[misc]

    def test_types_are_exported_from_the_module(self) -> None:
        """後続タスク（4.4 収束 / 5.2 帰属）が型で受けられること。"""
        result = measure_accuracy(build_record(), build_truth())

        assert isinstance(result, AccuracyResult)
        assert all(isinstance(error, PredictionError) for error in result.errors)


# ---------------------------------------------------------------------------
# 合成軌道（tasks.md 4.3 の観測可能な完了状態）
# ---------------------------------------------------------------------------


class TestSyntheticTrajectory:
    """既知の放物軌道に対して誤差系列が解析解と一致する。"""

    def test_hit_errors_vanish_against_the_analytic_impact_point(self) -> None:
        """真値が解析解ちょうどなら、どの予測の誤差もほぼ 0 である。

        サンプルが厳密に放物線上にあり重力を固定して当てているので、フィットは
        厳密解であり、予測落下地点は解析解に一致する。

        **この検査だけでは足りない。** 誤差が 0 なので、符号を反転した実装
        （実測 − 予測）でも通ってしまう。向きは
        `test_hit_errors_equal_the_injected_offset` が固定する。
        """
        record = build_synthetic_record()
        truth = derive_truth(record, synthetic_entry(), layout=build_layout())

        result = measure_accuracy(record, truth)

        assert len(result.errors) > 0
        for error in result.errors:
            assert error.hit_error_norm_mm == pytest.approx(
                0.0, abs=SYN_FIT_TOLERANCE_MM
            )

    def test_hit_errors_equal_the_injected_offset(self) -> None:
        """真値を既知のオフセットだけずらすと、誤差はその**逆ベクトル**になる。

        誤差は `予測 − 実測` であり、予測は解析解のままなので
        `解析解 − (解析解 + offset) = -offset` が解析解である。x と y で
        大きさも符号も違うオフセットを注入してあるので、**符号の反転も成分の
        取り違えも 40 mm 規模の差として現れる**（許容差は 1e-3 mm）。
        """
        record = build_synthetic_record()
        truth = derive_truth(
            record, synthetic_entry(SYN_TRUTH_OFFSET_MM), layout=build_layout()
        )

        result = measure_accuracy(record, truth)

        assert len(result.errors) > 0
        for error in result.errors:
            assert error.hit_error_mm[0] == pytest.approx(
                -SYN_TRUTH_OFFSET_MM[0], abs=SYN_FIT_TOLERANCE_MM
            )
            assert error.hit_error_mm[1] == pytest.approx(
                -SYN_TRUTH_OFFSET_MM[1], abs=SYN_FIT_TOLERANCE_MM
            )
            assert error.hit_error_norm_mm == pytest.approx(
                math.hypot(*SYN_TRUTH_OFFSET_MM), abs=SYN_FIT_TOLERANCE_MM
            )

    def test_time_errors_are_bounded_by_the_interpolation_bias(self) -> None:
        """時刻誤差は**必ず正**であり、線形内挿の偏りの上界に収まる。

        真値の落下時刻は隣接2点の線形内挿であり、弦は放物線の下を通るので
        **真値が必ず早い側**に出る。予測は解析解なので
        `予測 − 実測 > 0` になる。上界はテスト定数だけから組み立てた絶対量で
        あり、**実装が申告した不確かさを使わない**。

        符号を反転した実装はここでも落ちる（すべて負になる）。
        """
        record = build_synthetic_record()
        truth = derive_truth(record, synthetic_entry(), layout=build_layout())
        bound_ms = syn_interpolation_bound_ms()

        result = measure_accuracy(record, truth)

        assert len(result.errors) > 0
        for error in result.errors:
            assert error.time_error_ms is not None
            assert 0.0 < error.time_error_ms <= bound_ms

    def test_counts_the_insufficient_sample_predictions(self) -> None:
        """最小サンプル数に満たない間の予測が理由ごとに数えられる。

        `ThrowPredictionTracker` は 1 点目・2 点目の追加でも
        `PredictionOutcome` を返し、系列に載せる（`min_samples` は 3）。
        **本物の無効予測**が理由ごとに数えられることを、ここで固定する。
        """
        record = build_synthetic_record()
        truth = derive_truth(record, synthetic_entry(), layout=build_layout())

        result = measure_accuracy(record, truth)

        counts = dict(result.invalid_counts)
        assert counts[InvalidReason.INSUFFICIENT_SAMPLES] == CONFIG.min_samples - 1
        assert len(result.errors) == len(record.predictions) - sum(counts.values())

    def test_companion_values_come_from_the_real_predictions(self) -> None:
        """併記される値が**本物の予測**の値と一致する。

        サンプル数は 3 から 1 ずつ増え、観測時刻はサンプルの時刻に一致し、
        残り時間は正で単調に減る（落下へ近づくため）。手で作った予測では
        なく、実際に `predict()` が返した値であることをここで確かめる。
        """
        record = build_synthetic_record()
        truth = derive_truth(record, synthetic_entry(), layout=build_layout())

        result = measure_accuracy(record, truth)

        sample_counts = [error.sample_count for error in result.errors]
        assert sample_counts == list(
            range(CONFIG.min_samples, CONFIG.min_samples + len(sample_counts))
        )
        remaining = [error.remaining_time_ms for error in result.errors]
        assert all(later < earlier for earlier, later in pairwise(remaining))
        assert remaining[-1] > 0.0
