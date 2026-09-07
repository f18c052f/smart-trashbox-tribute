"""実測項目 1 / 2 / 6 の算出の検証（タスク 4.2、要件 5.1, 5.2, 5.6）。

観測可能な完了状態（tasks.md 4.2）を固定する:

- **合成軌道に対して3項目が解析解と一致する**
- **リリース時刻が欠測のとき、項目1・2 だけが欠測になり、項目6 は残る**

あわせて design.md「FlightMetrics」が定める点も固定する:

- 真値の不確かさが結果へ**伝播する**（項目1 と項目2 では伝播の式が違う）
- 求め方の種別（`TruthMethod`）が結果に含まれる
- 項目2 が**プロジェクトで最も未検証な量**である旨を、レポートが拾える形で持つ

**検査を2段に分けた理由。** 3項目はどれも「差」または「距離」であり、
合成軌道だけで検査すると**式の取り違えが軌道の数値に紛れて見えなくなる**。
そこで下の2段に分ける。

1. **代数の段**（`TestItem1` / `TestItem2` / `TestItem6` / `TestUncertainty`）:
   真値をテスト局所のリテラルで組み（`ThrowTruth` を直接構築する）、
   結果を**厳密な期待値と突き合わせる**。ここでは軌道も予測も介在しないので、
   符号の反転・被減数と減数の取り違え・水平距離を3次元距離にする誤り・
   不確かさの合成規則の取り違えが、そのまま差として現れる。
2. **合成軌道の段**（`TestSyntheticTrajectory`）: 既知の放物軌道から
   `ThrowPredictionTracker` に記録を作らせ、`derive_truth()` が返す本物の
   真値を通して、3項目が**解析解と一致する**ことを固定する。

**参照解は実装の定数に触れない。** 床面高さは本ファイルの `FLOOR_Z_MM`
（リテラルの 0.0）から組み、`truth.FLOOR_HEIGHT_MM` を参照しない。実装の
定数で参照解を組むと、**その定数を変えたときに参照解が一緒に動いて差が
消える**（tasks.md「Implementation Notes」タスク4.1。床面定数を 50.0 に
しても全件通っていた事故が実際に起きている）。

**許容差の根拠。** 3項目で性質が違う。

- **項目2（リリース〜検出開始）**: リリース時刻は厳密な放物線上のサンプルを
  重力固定で当てた軌道の外挿なので、**解析解と厳密に一致するのが正しい**。
  許容差 `EXACT_TOLERANCE_MS`（1e-6 ms）はフィットの条件数だけに由来する
  余裕であり、定式化を誤れば数十 ms 規模でずれる。
- **項目1（総飛行時間）**: 落下時刻が隣接2点の**線形**内挿なので、放物線の
  解析解とは原理的に一致しない。弦は放物線の下を通るため**必ず早い側**に
  出る。そこで「たまたま通る許容差」を置かず、**テスト定数だけから組み立てた
  絶対上界**（区間内で弦と放物線が離れる最大量 `g dt^2 / 8` を接地時の降下
  速度で時間へ換算した量）と突き合わせ、**ずれが 0 でなく早い側であること**も
  固定する。**実装が申告した不確かさを許容差に使わない**——それは過小申告
  しか捕まえられず、上限側に対して無力である（同じく tasks.md タスク4.1）。
- **項目6（狙い誤差）**: 距離なので厳密に一致するのが正しい（1e-9 mm）。
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import pytest

from m1_validation.errors import M1ConfigError
from m1_validation.layout import ThrowLayout
from m1_validation.metrics.flight import (
    IMPACT_POINT_KEY,
    IMPACT_TIME_KEY,
    RELEASE_TIME_KEY,
    RELEASE_TO_DETECT_KEY,
    FlightResult,
    measure_flight,
)
from m1_validation.truth import derive_truth
from m1_validation.types import ThrowTruth, TruthMethod, TruthValue
from prediction_core import (
    PredictionConfig,
    Sample,
    SourceKind,
    ThrowPredictionTracker,
    ThrowRecord,
)

# --- 代数の段で使う既知の値（すべてテスト局所のリテラル）-------------------
#
# 基準になる量に 0 を置かない。tasks.md タスク2.2 / 3.1 の Implementation
# Notes が「0 を既定値にしたフィクスチャは、差分・オフセット・基準の
# 取り違えをまるごと素通しさせる」と記録している。3項目はすべて差または
# 距離なので、ここが 0 だと検査が働かない。

RECORD_ID = "throw-0042"

#: 最初の有効サンプルの観測時刻（ms）。
FIRST_SAMPLE_T_MS = 5100.0
#: 2件目以降の観測時刻。**最初の1件だけが項目2 に効く**ことを固定するために置く。
LATER_SAMPLE_T_MS = (5150.0, 5220.0, 5310.0)

#: リリース時刻（ms）と落下時刻（ms）。差 789.0 ms が項目1 の期待値である。
RELEASE_T_MS = 5004.0
IMPACT_T_MS = 5793.0
EXPECTED_TOTAL_FLIGHT_MS = 789.0
EXPECTED_RELEASE_TO_DETECT_MS = 96.0

#: 真値の不確かさ（ms / mm）。**2つを別々の値にする**——同じ値にすると
#: 「項目1 は両者の和」「項目2 はリリース側だけ」の違いが消える。
IMPACT_TIME_UNC_MS = 3.0
RELEASE_TIME_UNC_MS = 20.0
IMPACT_POINT_UNC_MM = 15.0

#: 待機位置（World mm の水平2成分）と落下地点（World mm）。
#: 水平差は (900, 1200) であり、距離は **1500 mm ちょうど**（3-4-5 の 300 倍）。
#: 落下地点の z を **0 でない 12.0 mm** にしてあるのは、水平距離を3次元
#: 距離に取り違えた実装がここで落ちるようにするためである
#: （3次元なら sqrt(1500^2 + 12^2) = 1500.048 mm になる）。
STANDBY_XY_MM = (150.0, -80.0)
IMPACT_POINT_MM = (1050.0, 1120.0, 12.0)
EXPECTED_AIM_ERROR_MM = 1500.0

DISTANCE_TOLERANCE_MM = 1e-9


# --- 合成軌道の段で使う既知の軌道 -------------------------------------------

CONFIG = PredictionConfig()
G_MM_MS2 = CONFIG.gravity_mm_ms2

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

#: 最初の有効サンプルの観測時刻。リリースのちょうど 100 ms 後（= §3 区間1）。
SYN_FIRST_SAMPLE_T_MS = 5100.0
SYN_EXPECTED_RELEASE_TO_DETECT_MS = 100.0

#: サンプル間隔（60 fps 相当）。
SYN_DT_MS = 1000.0 / 60.0

#: 外挿の許容差（モジュール docstring 参照）。
EXACT_TOLERANCE_MS = 1e-6


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
    """線形内挿が解析解より早く出る量の**上界**（ms。テスト定数だけから組む）。

    弦と放物線が区間内で離れる最大量は `g dt^2 / 8`（中点で最大）であり、
    それを接地時の降下速度で時間へ換算する。実装は弦の傾き（区間中点の
    瞬時速度）で割るので厳密には一致しないため、1.1 倍の余裕を持たせる
    ——それでも `dt^2/8` を `dt^2/6` と取り違えた実装（+33%）は落ちる。
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
        "standby_position_world_mm": STANDBY_XY_MM,
        "object_diameter_mm": 65.0,
        "aperture_diameter_mm": 200.0,
        "camera_position_world_mm": (0.0, -1500.0, 1000.0),
        "notes": "仮値。確定ではない。",
    }
    values.update(overrides)
    return ThrowLayout(**values)  # type: ignore[arg-type]


def time_truth(value: float, method: TruthMethod, uncertainty_ms: float) -> TruthValue:
    return TruthValue(
        value=value,
        method=method,
        uncertainty_mm=None,
        uncertainty_ms=uncertainty_ms,
        source="テスト用の既知の真値",
    )


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
    release_time: TruthValue | None = None,
    record_id: str = RECORD_ID,
) -> ThrowTruth:
    """テスト局所のリテラルだけから真値一式を組む。"""
    if impact_point is None:
        impact_point = TruthValue(
            value=IMPACT_POINT_MM,
            method=TruthMethod.MEASURED,
            uncertainty_mm=IMPACT_POINT_UNC_MM,
            uncertainty_ms=None,
            source="メジャー実測（テスト）",
        )
    if impact_time is None:
        impact_time = time_truth(
            IMPACT_T_MS, TruthMethod.INTERPOLATED, IMPACT_TIME_UNC_MS
        )
    if release_time is None:
        release_time = time_truth(
            RELEASE_T_MS, TruthMethod.EXTRAPOLATED, RELEASE_TIME_UNC_MS
        )
    return ThrowTruth(
        record_id=record_id,
        impact_point_world_mm=impact_point,
        impact_time_ms=impact_time,
        release_time_ms=release_time,
        external_mark_delta_ms=None,
    )


def build_record(
    *,
    times: Sequence[float] = (FIRST_SAMPLE_T_MS, *LATER_SAMPLE_T_MS),
    record_id: str = RECORD_ID,
) -> ThrowRecord:
    """観測時刻だけが意味を持つ最小の記録（代数の段で使う）。"""
    return ThrowRecord(
        record_id=record_id,
        source=SourceKind.SIMULATED,
        config=CONFIG,
        samples=tuple(
            Sample(t_ms=t_ms, x_mm=-100.0 + index, y_mm=20.0, z_mm=900.0 - index)
            for index, t_ms in enumerate(times)
        ),
        predictions=(),
    )


def build_synthetic_record(record_id: str = RECORD_ID) -> ThrowRecord:
    """既知の放物軌道から `ThrowRecord` を組み立てる（合成軌道の段）。

    予測系列は**本物の `ThrowPredictionTracker`** に作らせる。真値の外挿は
    最終予測の軌道パラメータを使うので、手で書いた軌道パラメータを渡すと
    実際に通る経路を検査しないことになる（`test_m1_truth.py` と同じ方針）。
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


def synthetic_entry() -> dict[str, object]:
    """合成軌道の解析解を「実測した落下地点」として渡す記入内容。"""
    return {
        "impact_point_world_mm": list(syn_impact_point_mm()),
        "impact_point_source": "メジャー実測（合成軌道の解析解を実測値とみなす）",
        "impact_point_uncertainty_mm": IMPACT_POINT_UNC_MM,
    }


# ---------------------------------------------------------------------------
# 項目1: 総飛行時間（要件 5.1）
# ---------------------------------------------------------------------------


class TestItem1TotalFlight:
    """総飛行時間 = 実際の落下時刻 − リリース時刻（要件 5.1）。"""

    def test_is_impact_time_minus_release_time(self) -> None:
        """厳密な期待値と一致する。

        被減数と減数を取り違えた実装（`release − impact`）は符号ごと落ちる。
        """
        result = measure_flight(build_record(), build_truth(), layout=build_layout())

        assert result.total_flight_ms == pytest.approx(
            EXPECTED_TOTAL_FLIGHT_MS, abs=EXACT_TOLERANCE_MS
        )

    def test_is_not_measured_from_the_first_sample(self) -> None:
        """総飛行時間の起点は**リリース時刻**であり、最初のサンプルではない。

        起点を取り違えると、項目1 が項目2 のぶんだけ短く出る。両者を別の
        値にしてあるので、取り違えは差として現れる。
        """
        result = measure_flight(build_record(), build_truth(), layout=build_layout())

        assert result.total_flight_ms != pytest.approx(
            IMPACT_T_MS - FIRST_SAMPLE_T_MS, abs=EXACT_TOLERANCE_MS
        )

    def test_missing_when_release_time_is_missing(self) -> None:
        """リリース時刻が欠測なら項目1 は欠測（要件 4.6 / 5.1）。"""
        truth = build_truth(release_time=missing_truth())

        result = measure_flight(build_record(), truth, layout=build_layout())

        assert result.total_flight_ms is None
        assert result.total_flight_uncertainty_ms is None

    def test_missing_when_impact_time_is_missing(self) -> None:
        """落下時刻が欠測でも**項目2 は残る**（欠測は項目ごとに閉じる）。"""
        truth = build_truth(impact_time=missing_truth())

        result = measure_flight(build_record(), truth, layout=build_layout())

        assert result.total_flight_ms is None
        assert result.total_flight_uncertainty_ms is None
        assert result.release_to_detect_ms == pytest.approx(
            EXPECTED_RELEASE_TO_DETECT_MS, abs=EXACT_TOLERANCE_MS
        )


# ---------------------------------------------------------------------------
# 項目2: リリース〜検出開始（要件 5.2）★プロジェクトで最も未検証な量
# ---------------------------------------------------------------------------


class TestItem2ReleaseToDetect:
    """リリース〜検出開始 = 最初の有効サンプルの観測時刻 − リリース時刻。"""

    def test_is_first_sample_time_minus_release_time(self) -> None:
        result = measure_flight(build_record(), build_truth(), layout=build_layout())

        assert result.release_to_detect_ms == pytest.approx(
            EXPECTED_RELEASE_TO_DETECT_MS, abs=EXACT_TOLERANCE_MS
        )

    def test_uses_the_first_sample_not_the_last(self) -> None:
        """**最初の**サンプルを使う。最後のサンプルを使う実装は落ちる。

        §3 区間1 は「投げてから見え始めるまで」であり、見えなくなるまでの
        時間ではない。取り違えると区間1 が飛行時間ぶん水増しされ、
        「Pi 4 では不足」という後戻りできない判断の根拠が壊れる。
        """
        result = measure_flight(build_record(), build_truth(), layout=build_layout())

        assert result.release_to_detect_ms != pytest.approx(
            LATER_SAMPLE_T_MS[-1] - RELEASE_T_MS, abs=EXACT_TOLERANCE_MS
        )

    def test_missing_when_release_time_is_missing(self) -> None:
        truth = build_truth(release_time=missing_truth())

        result = measure_flight(build_record(), truth, layout=build_layout())

        assert result.release_to_detect_ms is None
        assert result.release_to_detect_uncertainty_ms is None

    def test_missing_when_no_sample_was_observed(self) -> None:
        """有効サンプルが1件も無ければ検出開始が存在しないので欠測。"""
        result = measure_flight(
            build_record(times=()), build_truth(), layout=build_layout()
        )

        assert result.release_to_detect_ms is None
        assert result.release_to_detect_uncertainty_ms is None
        # 項目1 はサンプルに依らないので残る。
        assert result.total_flight_ms == pytest.approx(
            EXPECTED_TOTAL_FLIGHT_MS, abs=EXACT_TOLERANCE_MS
        )

    def test_skips_a_non_finite_sample_time(self) -> None:
        """観測時刻が非有限な点は「有効サンプル」ではないので読み飛ばす。

        要件 5.2 が言う基準は「最初の**有効**サンプル」である。NaN をそのまま
        被減数にすると **NaN が「測れた値」として集計へ流れ込む**。読み飛ばして
        次の有効な点を検出開始とするのは、`truth.py` の内挿が非有限の対を
        読み飛ばすのと同じ方針である。
        """
        result = measure_flight(
            build_record(times=(math.nan, *LATER_SAMPLE_T_MS)),
            build_truth(),
            layout=build_layout(),
        )

        assert result.release_to_detect_ms == pytest.approx(
            LATER_SAMPLE_T_MS[0] - RELEASE_T_MS, abs=EXACT_TOLERANCE_MS
        )

    def test_is_emphasised_as_the_most_unvalidated_quantity(self) -> None:
        """項目2 の強調文が結果に載る（tasks.md 4.2「レポート向けに強調」）。

        `docs/requirements.md §3` 区間1 は**完全に未検証**であり、本 Spec の
        存在理由の中心にある。レポートが強調を付け忘れられない形にする。
        """
        result = measure_flight(build_record(), build_truth(), layout=build_layout())

        assert RELEASE_TO_DETECT_KEY in result.emphasis
        assert "未検証" in result.emphasis[RELEASE_TO_DETECT_KEY]

    def test_emphasis_survives_when_the_item_is_missing(self) -> None:
        """欠測でも強調は消えない——測れなかったこと自体が報告に値する。"""
        truth = build_truth(release_time=missing_truth())

        result = measure_flight(build_record(), truth, layout=build_layout())

        assert result.release_to_detect_ms is None
        assert RELEASE_TO_DETECT_KEY in result.emphasis


# ---------------------------------------------------------------------------
# 項目6: 狙い誤差（要件 5.6）
# ---------------------------------------------------------------------------


class TestItem6AimError:
    """狙い誤差 = 待機位置 → 実際の落下地点の**水平**距離（要件 5.6）。"""

    def test_is_the_horizontal_distance_from_the_standby_position(self) -> None:
        """厳密な期待値（3-4-5 の 300 倍 = 1500 mm）と一致する。

        落下地点の z を 12.0 mm にしてあるので、**3次元距離を返す実装は
        1500.048 mm になり落ちる**。狙い誤差は移動体の必要横移動量であり、
        高さを混ぜた量ではない。
        """
        result = measure_flight(build_record(), build_truth(), layout=build_layout())

        assert result.aim_error_mm == pytest.approx(
            EXPECTED_AIM_ERROR_MM, abs=DISTANCE_TOLERANCE_MM
        )

    def test_ignores_the_vertical_component(self) -> None:
        """落下地点の高さだけを変えても狙い誤差は変わらない。"""
        raised = TruthValue(
            value=(IMPACT_POINT_MM[0], IMPACT_POINT_MM[1], 400.0),
            method=TruthMethod.MEASURED,
            uncertainty_mm=IMPACT_POINT_UNC_MM,
            uncertainty_ms=None,
            source="メジャー実測（テスト）",
        )

        result = measure_flight(
            build_record(), build_truth(impact_point=raised), layout=build_layout()
        )

        assert result.aim_error_mm == pytest.approx(
            EXPECTED_AIM_ERROR_MM, abs=DISTANCE_TOLERANCE_MM
        )

    def test_is_measured_from_the_standby_position_not_the_origin(self) -> None:
        """基準は**レイアウトの待機位置**であり、World 原点ではない。

        待機位置を動かせば狙い誤差も動く。原点固定の実装はここで落ちる。
        """
        moved = build_layout(standby_position_world_mm=(0.0, 0.0))

        result = measure_flight(build_record(), build_truth(), layout=moved)

        assert result.aim_error_mm == pytest.approx(
            math.hypot(IMPACT_POINT_MM[0], IMPACT_POINT_MM[1]),
            abs=DISTANCE_TOLERANCE_MM,
        )
        assert result.aim_error_mm != pytest.approx(
            EXPECTED_AIM_ERROR_MM, abs=DISTANCE_TOLERANCE_MM
        )

    def test_missing_when_the_impact_point_is_missing(self) -> None:
        truth = build_truth(impact_point=missing_truth())

        result = measure_flight(build_record(), truth, layout=build_layout())

        assert result.aim_error_mm is None
        assert result.aim_error_uncertainty_mm is None

    def test_survives_when_the_release_time_is_missing(self) -> None:
        """**タスク 4.2 の完了状態**: リリース時刻が欠測でも項目6 は残る。

        要件 4.6 / 5.6。1件の欠測で他項目の集計を止めない。
        """
        truth = build_truth(release_time=missing_truth())

        result = measure_flight(build_record(), truth, layout=build_layout())

        assert result.total_flight_ms is None
        assert result.release_to_detect_ms is None
        assert result.aim_error_mm == pytest.approx(
            EXPECTED_AIM_ERROR_MM, abs=DISTANCE_TOLERANCE_MM
        )
        assert result.aim_error_uncertainty_mm == pytest.approx(
            IMPACT_POINT_UNC_MM, abs=DISTANCE_TOLERANCE_MM
        )


# ---------------------------------------------------------------------------
# 不確かさの伝播と求め方の種別（要件 4.4 / 5.1 / 5.2、tasks.md 4.2）
# ---------------------------------------------------------------------------


class TestUncertaintyPropagation:
    """真値の不確かさが結果へ伝播する。**項目1 と項目2 で式が違う**。"""

    def test_total_flight_combines_both_time_truths(self) -> None:
        """項目1 は2つの真値の差なので、両方の不確かさが効く。

        期待値 23.0 ms はテスト局所のリテラル（3.0 + 20.0）から組む。
        二乗和（20.22）・最大値（20.0）・片側だけ（20.0 / 3.0）のいずれの
        実装もここで落ちる。
        """
        result = measure_flight(build_record(), build_truth(), layout=build_layout())

        assert result.total_flight_uncertainty_ms == pytest.approx(
            IMPACT_TIME_UNC_MS + RELEASE_TIME_UNC_MS, abs=EXACT_TOLERANCE_MS
        )

    def test_release_to_detect_carries_only_the_release_uncertainty(self) -> None:
        """項目2 の不確かさはリリース時刻のぶんだけである。

        検出開始は**観測された時刻そのもの**であって推定値ではないので、
        落下時刻（内挿）の不確かさをここへ足すと**項目2 を水増しする**。
        項目2 はプロジェクトで最も未検証な量であり、その不確かさを実際より
        大きく申告することは、区間1 の実測値を読めなくするのと同じである。
        """
        result = measure_flight(build_record(), build_truth(), layout=build_layout())

        assert result.release_to_detect_uncertainty_ms == pytest.approx(
            RELEASE_TIME_UNC_MS, abs=EXACT_TOLERANCE_MS
        )
        # 項目1 の合成値を流用した実装はここで落ちる。
        assert result.release_to_detect_uncertainty_ms != pytest.approx(
            IMPACT_TIME_UNC_MS + RELEASE_TIME_UNC_MS, abs=EXACT_TOLERANCE_MS
        )

    def test_aim_error_carries_the_impact_point_uncertainty(self) -> None:
        result = measure_flight(build_record(), build_truth(), layout=build_layout())

        assert result.aim_error_uncertainty_mm == pytest.approx(
            IMPACT_POINT_UNC_MM, abs=DISTANCE_TOLERANCE_MM
        )

    def test_does_not_invent_uncertainty_that_the_truth_did_not_report(self) -> None:
        """真値が不確かさを申告していなければ、結果でも申告しない。

        値を捏造して「不確かさが分かっている」ように見せない。
        """
        bare = TruthValue(
            value=RELEASE_T_MS,
            method=TruthMethod.EXTRAPOLATED,
            uncertainty_mm=None,
            uncertainty_ms=None,
            source="不確かさを申告しない真値（テスト）",
        )

        result = measure_flight(
            build_record(), build_truth(release_time=bare), layout=build_layout()
        )

        assert result.release_to_detect_ms == pytest.approx(
            EXPECTED_RELEASE_TO_DETECT_MS, abs=EXACT_TOLERANCE_MS
        )
        assert result.release_to_detect_uncertainty_ms is None
        assert result.total_flight_uncertainty_ms is None


class TestMethodsAreReported:
    """求め方の種別を結果に含める（要件 4.4、tasks.md 4.2）。"""

    def test_reports_the_method_of_every_truth(self) -> None:
        """3つの真値それぞれの求め方が載る。

        真値ごとに載せるのは、**項目1 が2つの真値に依存する**からである。
        実測項目ごとの1対1の対応にすると、項目1 の求め方が「内挿」と
        「外挿」のどちらか一方に潰れて落ちる。
        """
        result = measure_flight(build_record(), build_truth(), layout=build_layout())

        assert result.methods[IMPACT_POINT_KEY] is TruthMethod.MEASURED
        assert result.methods[IMPACT_TIME_KEY] is TruthMethod.INTERPOLATED
        assert result.methods[RELEASE_TIME_KEY] is TruthMethod.EXTRAPOLATED

    def test_reports_missing_as_a_method(self) -> None:
        """欠測も求め方の1つとして載る（値だけを見て欠測に気付けるように）。"""
        truth = build_truth(release_time=missing_truth())

        result = measure_flight(build_record(), truth, layout=build_layout())

        assert result.methods[RELEASE_TIME_KEY] is TruthMethod.MISSING


class TestResultIsSelfContained:
    """結果が後から書き換わらないこと。"""

    def test_mappings_are_copied_at_construction(self) -> None:
        """`methods` / `emphasis` は複製して保持する（`Judgement` と同じ方針）。

        呼び出し側が使い回す辞書をそのまま抱えると、レポートに出る求め方が
        算出時のものと食い違い得る。
        """
        methods = {RELEASE_TIME_KEY: TruthMethod.EXTRAPOLATED}
        emphasis = {RELEASE_TO_DETECT_KEY: "強調文"}
        result = FlightResult(
            total_flight_ms=None,
            total_flight_uncertainty_ms=None,
            release_to_detect_ms=None,
            release_to_detect_uncertainty_ms=None,
            aim_error_mm=None,
            aim_error_uncertainty_mm=None,
            methods=methods,
            emphasis=emphasis,
        )

        methods[RELEASE_TIME_KEY] = TruthMethod.MISSING
        emphasis[RELEASE_TO_DETECT_KEY] = "書き換えた"

        assert result.methods[RELEASE_TIME_KEY] is TruthMethod.EXTRAPOLATED
        assert result.emphasis[RELEASE_TO_DETECT_KEY] == "強調文"


class TestMismatchedTruthIsRejected:
    """別の投擲の真値で測らない。"""

    def test_rejects_a_truth_for_another_record(self) -> None:
        """記録と真値の `record_id` が違えば拒否する（`attach_truth` と同じ）。

        取り違えた真値で測ると、**別の投擲の誤差**を本投擲の実測値として
        報告することになる。黙って数値を返してはならない。
        """
        truth = build_truth(record_id="throw-9999")

        with pytest.raises(M1ConfigError):
            measure_flight(build_record(), truth, layout=build_layout())


# ---------------------------------------------------------------------------
# 合成軌道（tasks.md 4.2 の観測可能な完了状態）
# ---------------------------------------------------------------------------


class TestSyntheticTrajectory:
    """既知の放物軌道に対して3項目が解析解と一致する。"""

    def test_all_three_items_match_the_analytic_solution(self) -> None:
        record = build_synthetic_record()
        layout = build_layout()
        truth = derive_truth(record, synthetic_entry(), layout=layout)

        result = measure_flight(record, truth, layout=layout)

        # --- 項目2: 外挿は厳密に一致するのが正しい（モジュール docstring）。
        assert result.release_to_detect_ms == pytest.approx(
            SYN_EXPECTED_RELEASE_TO_DETECT_MS, abs=EXACT_TOLERANCE_MS
        )

        # --- 項目1: 落下時刻が線形内挿なので、**必ず早い側**へ有限量ずれる。
        assert result.total_flight_ms is not None
        error_ms = result.total_flight_ms - (
            syn_impact_time_ms() - SYN_RELEASE_T_MS
        )
        assert -syn_interpolation_bound_ms() <= error_ms < 0.0

        # --- 項目6: 距離なので厳密に一致する。
        impact_x_mm, impact_y_mm, _ = syn_impact_point_mm()
        assert result.aim_error_mm == pytest.approx(
            math.hypot(
                impact_x_mm - STANDBY_XY_MM[0], impact_y_mm - STANDBY_XY_MM[1]
            ),
            abs=DISTANCE_TOLERANCE_MM,
        )

        # --- 求め方と不確かさ。真値が申告した値がそのまま伝播している。
        assert result.methods[IMPACT_TIME_KEY] is TruthMethod.INTERPOLATED
        assert result.methods[RELEASE_TIME_KEY] is TruthMethod.EXTRAPOLATED
        assert result.methods[IMPACT_POINT_KEY] is TruthMethod.MEASURED

        impact_unc_ms = truth.impact_time_ms.uncertainty_ms
        release_unc_ms = truth.release_time_ms.uncertainty_ms
        assert impact_unc_ms is not None
        assert release_unc_ms is not None
        assert result.total_flight_uncertainty_ms == pytest.approx(
            impact_unc_ms + release_unc_ms, abs=EXACT_TOLERANCE_MS
        )
        assert result.release_to_detect_uncertainty_ms == pytest.approx(
            release_unc_ms, abs=EXACT_TOLERANCE_MS
        )

    def test_only_the_aim_error_remains_when_the_release_is_missing(self) -> None:
        """**タスク 4.2 の完了状態**: リリース時刻欠測時に項目6 だけが残る。

        欠測は手で作らず、**実際に外挿が成立しない条件**（推定軌道が届かない
        リリース高さ）で作る。落下時刻の内挿は成立したままなので、
        「落下時刻はあるのに項目1 が出ない」ことも同時に固定できる。
        """
        record = build_synthetic_record()
        layout = build_layout(release_height_mm=9000.0)
        truth = derive_truth(record, synthetic_entry(), layout=layout)
        assert truth.release_time_ms.method is TruthMethod.MISSING
        assert truth.impact_time_ms.method is TruthMethod.INTERPOLATED

        result = measure_flight(record, truth, layout=layout)

        assert result.total_flight_ms is None
        assert result.release_to_detect_ms is None
        assert result.aim_error_mm is not None
        assert result.aim_error_uncertainty_mm == pytest.approx(
            IMPACT_POINT_UNC_MM, abs=DISTANCE_TOLERANCE_MM
        )
