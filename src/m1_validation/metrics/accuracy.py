"""実測項目 4 / 5（落下地点の誤差・落下時刻の誤差）の算出。

design.md「Components and Interfaces / L4-L5: 真値と実測 / AccuracyMetrics」、
tasks.md タスク 4.3、要件 5.4, 5.5（および欠測の扱いとして 4.6）。

**誤差は1つの数ではなく系列である。** 予測は観測サンプルが増えるたびに
更新されるので、「その投擲の誤差」を最終予測1点に代表させると、**何サンプル
の段階でどれだけ当たっていたか**——収束（要件 5.7）とレイテンシ予算
（要件 11）の両方が要求する量——が消える。したがって本モジュールは
**予測が更新されるたびの誤差**を系列として返す。

**誤差はスカラーではなくベクトルとして保持する。** 帰属（要件 6.3）は、
投擲群に共通する偏りの**向き**が World 座標系に固定されているか、カメラ
視線方向に沿っているかを判別する。大きさだけを持たせるとこの判別ができず、
キャリブレーション由来と検出由来を切り分けられない——**「予測が悪い」と
いう一つの症状に潰れる**という、本 Spec が避けようとしている事態そのもので
ある。向きの規約は **`予測 − 実測`**（真値から予測へ向かうベクトル）に
そろえ、落下時刻の差も同じ向き（正なら予測が遅い側）で表す。

**無効な予測は系列から除き、理由ごとに数える**（design.md「Validation」）。
`InvalidPrediction` は落下地点のフィールドを**意図的に持たない**
（`prediction_core` 要件 6.7）ので、そもそも誤差を作れない。0 で埋めて系列に
載せると、誤った目標座標が誤差として集計へ流れる。数えるのは、**無効が多い
投擲を「たまたま予測が悪い投擲」と取り違えない**ためである（継ぎ目が除外
理由ごとの件数を残すのと同じ理由）。**理由の語彙は
`prediction_core.InvalidReason` をそのまま使い、本 Spec で再定義しない。**

**真値が欠測なら誤差も欠測**（design.md「Error Categories and Responses」:
真値の欠測 → 値として扱う。当該項目のみ欠測、他項目は継続）。落下地点が
未記入なら誤差系列は空になり、落下時刻だけが欠測なら時刻差だけが `None` に
なる。**0 で埋めない**——「誤差が 0 だった」と「測っていない」を混ぜると、
前者として集計へ入り代表値を良い方へ引っ張る。

本モジュールは L5 層の評価側であり、`sensing_foundation` /
`flying_object_tracking` / `world_frame_calibration` を import しない
（design.md「Allowed Dependencies」）。数値計算は標準ライブラリだけで書く。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from m1_validation.errors import M1ConfigError
from m1_validation.types import ThrowTruth, TruthMethod, TruthValue
from prediction_core import (
    InvalidPrediction,
    InvalidReason,
    Prediction,
    ThrowRecord,
)


@dataclass(frozen=True, slots=True)
class PredictionError:
    """1回の予測更新に対する誤差1件（要件 5.4 / 5.5）。

    Attributes:
        sample_count: この予測が基づいた観測サンプル数
            （`Prediction.sample_count`）。**何サンプルの段階の誤差か**が
            分からないと、収束（タスク 4.4）にも帰属（タスク 5.2）にも
            使えない。
        based_on_time_ms: 使用サンプルのうち最新の観測時刻（ms、
            `Prediction.based_on_time_ms`）。
        hit_error_mm: 落下地点の誤差（World mm の**水平2成分**）。
            **`予測 − 実測`のベクトル**であり、スカラーに畳まない
            （モジュール docstring 参照）。
        hit_error_norm_mm: `hit_error_mm` の大きさ（mm）。要件 5.4 が言う
            「水平距離の誤差」であり、**高さ成分を含まない**。
        time_error_ms: 落下時刻の誤差（ms）。`予測 − 実測`。正なら予測が
            遅い側。落下時刻の真値が欠測なら `None`（**0 で埋めない**）。
        residual_mm: この予測の残差（mm、`Prediction.residual`）。帰属
            （要件 6.7）が「ばらつきが観測由来の範囲を超え、かつフィットの
            残差が大きい」場合をモデル由来と判定するので、誤差と同じ行に
            置く。
        remaining_time_ms: この予測時点で落下までに残っていた時間（ms、
            `Prediction.remaining_time_ms`）。**移動体に残された時間**であり、
            誤差と併記して初めて「どれだけの持ち時間でどれだけの精度が
            出ているか」が読める。**真値から作り直さない**——予測が申告した
            値でなければ「予測時点で分かっていたはずの持ち時間」にならない。
    """

    sample_count: int
    based_on_time_ms: float
    hit_error_mm: tuple[float, float]
    hit_error_norm_mm: float
    time_error_ms: float | None
    residual_mm: float
    remaining_time_ms: float


@dataclass(frozen=True, slots=True)
class AccuracyResult:
    """1投擲ぶんの実測項目 4 / 5（要件 5.4, 5.5）。

    Attributes:
        errors: 予測が更新されるたびの誤差（**生成順**）。無効な予測と、
            真値が欠測で誤差を作れない場合は載らない。
        first_valid: 最初の有効な予測の誤差。`errors` の先頭と同一物。
            誤差が1件も無ければ `None`。
        final: 最後の有効な予測の誤差。`errors` の末尾と同一物であり、
            **記録の最後の予測ではない**（末尾が無効予測なら1つ前になる）。
        invalid_counts: 無効だった予測の**理由ごとの件数**。理由の語彙は
            `prediction_core.InvalidReason` である。観測されなかった理由の
            行は作らない（0 の行をでっち上げない）。順序は**最初に現れた
            順**である（継ぎ目の `ThrowSamples.rejected` と同じ持ち方）。

    **design.md の擬似コードとの差**: design.md の `AccuracyResult` は
    `errors` / `first_valid` / `final` の3フィールドだが、ここでは
    `invalid_counts` を足している。tasks.md 4.3 と design.md の
    「Validation: 無効な予測（`InvalidPrediction`）は系列から除き、**理由ごと
    に数える**」が件数を要求しており、本コンポーネントの戻り値以外に
    置き場所が無いためである。

    `errors` が空でも、それだけでは「有効な予測が1件も無かった」のか
    「落下地点の真値が未記入なのか」は区別できない。**区別は真値
    （`ThrowTruth.impact_point_world_mm.method`）を見て行う**——集計側は
    真値の欠測した投擲を試行数として数えない（design.md「AccuracyMetrics」
    Risks）。
    """

    errors: tuple[PredictionError, ...]
    first_valid: PredictionError | None
    final: PredictionError | None
    invalid_counts: tuple[tuple[InvalidReason, int], ...]


def measure_accuracy(record: ThrowRecord, truth: ThrowTruth) -> AccuracyResult:
    """実測項目 4 / 5 を、予測更新のたびの系列として算出する（要件 5.4, 5.5）。

    Args:
        record: 対象の投擲記録。`predictions` を**生成順のまま**辿る。
        truth: 同じ投擲の真値（`derive_truth()` の戻り値）。

    Returns:
        `AccuracyResult`。真値が欠測なら当該の誤差は載らず、**0 で埋めない**。
        無効な予測は系列から除かれ、理由ごとに数えられる。

    Raises:
        M1ConfigError: 記録と真値の `record_id` が違う場合。**取り違えた真値で
            測ると、別の投擲の誤差を本投擲の実測値として報告することになる**
            （`measure_flight()` / `attach_truth()` と同じ理由で拒否する）。
            真値の欠測では例外を投げない（要件 4.6）。
    """
    if record.record_id != truth.record_id:
        raise M1ConfigError(
            "記録と真値の record_id が違う: "
            f"{record.record_id!r} ≠ {truth.record_id!r}。"
            "取り違えた真値で測ると、別の投擲の誤差を本投擲の実測値として"
            "報告することになる",
            {"record_id": record.record_id, "truth_record_id": truth.record_id},
        )

    impact_xy_mm = _finite_horizontal_point_mm(truth.impact_point_world_mm)
    impact_time_ms = _finite_time_ms(truth.impact_time_ms)

    errors: list[PredictionError] = []
    invalid_counts: dict[InvalidReason, int] = {}

    def count_invalid(reason: InvalidReason) -> None:
        invalid_counts[reason] = invalid_counts.get(reason, 0) + 1

    for outcome in record.predictions:
        if isinstance(outcome, InvalidPrediction):
            count_invalid(outcome.reason)
            continue
        if not _is_finite_prediction(outcome):
            # `prediction_core` は非有限の算出結果を `InvalidPrediction` に
            # するので本来ここへは来ないが、記録は JSON を経由して復元され
            # うる（`ThrowRecord.from_dict` は非有限値を拒否しない）。NaN を
            # そのまま誤差にすると **NaN が「測れた値」として集計へ流れ込む**
            # （design.md「Data Models」: NaN / Infinity は欠測として表す）。
            # 理由は `prediction_core` の語彙をそのまま使い、本 Spec で新しい
            # 理由を作らない。
            count_invalid(InvalidReason.NON_FINITE_VALUE)
            continue
        if impact_xy_mm is None:
            # 落下地点の真値が無ければ誤差そのものが作れない。**0 で埋めて
            # 系列へ載せない**——「誤差が 0 だった」として集計に入る。
            continue
        errors.append(
            _prediction_error(
                outcome, impact_xy_mm=impact_xy_mm, impact_time_ms=impact_time_ms
            )
        )

    return AccuracyResult(
        errors=tuple(errors),
        first_valid=errors[0] if errors else None,
        final=errors[-1] if errors else None,
        invalid_counts=tuple(invalid_counts.items()),
    )


def _prediction_error(
    prediction: Prediction,
    *,
    impact_xy_mm: tuple[float, float],
    impact_time_ms: float | None,
) -> PredictionError:
    """1件の有効な予測に対する誤差を組み立てる。

    落下地点の誤差は**水平2成分**だけを取る。高さを混ぜないのは、要件 5.4 が
    「水平距離の誤差」を求めており、落下地点の高さ成分（対象物の中心高さ・
    床面の凹凸）は移動体が詰めるべき距離ではないからである（狙い誤差
    `measure_flight()` と同じ扱い）。
    """
    dx_mm = prediction.predicted_hit_x_mm - impact_xy_mm[0]
    dy_mm = prediction.predicted_hit_y_mm - impact_xy_mm[1]
    time_error_ms = (
        None
        if impact_time_ms is None
        else prediction.predicted_hit_time_ms - impact_time_ms
    )
    return PredictionError(
        sample_count=prediction.sample_count,
        based_on_time_ms=prediction.based_on_time_ms,
        hit_error_mm=(dx_mm, dy_mm),
        hit_error_norm_mm=math.hypot(dx_mm, dy_mm),
        time_error_ms=time_error_ms,
        residual_mm=prediction.residual,
        remaining_time_ms=prediction.remaining_time_ms,
    )


def _is_finite_prediction(prediction: Prediction) -> bool:
    """誤差の行を作るのに使う値がすべて有限か。

    判定の範囲は `prediction_core` の `NON_FINITE_VALUE`（算出結果に NaN もしくは
    Infinity が含まれる）と同じにする。1つでも非有限なら行全体を作らない
    ——残差だけが NaN の行を載せても、その行は読めないまま集計へ入る。
    """
    return all(
        math.isfinite(value)
        for value in (
            prediction.predicted_hit_x_mm,
            prediction.predicted_hit_y_mm,
            prediction.predicted_hit_time_ms,
            prediction.remaining_time_ms,
            prediction.residual,
            prediction.based_on_time_ms,
        )
    )


def _finite_time_ms(value: TruthValue) -> float | None:
    """時刻の真値を取り出す。欠測・非有限・型違いなら `None`。

    非有限値を欠測として扱うのは本 Spec と上流・`prediction_core` に共通の
    方針である（`metrics/flight.py` の同名ヘルパと同じ判定）。
    """
    if value.method is TruthMethod.MISSING:
        return None
    if not isinstance(value.value, float | int) or isinstance(value.value, bool):
        return None
    number = float(value.value)
    return number if math.isfinite(number) else None


def _finite_horizontal_point_mm(value: TruthValue) -> tuple[float, float] | None:
    """落下地点の真値から**水平2成分**を取り出す。欠測・非有限なら `None`。"""
    if value.method is TruthMethod.MISSING:
        return None
    point = value.value
    if not isinstance(point, tuple):
        return None
    x_mm, y_mm = float(point[0]), float(point[1])
    if not (math.isfinite(x_mm) and math.isfinite(y_mm)):
        return None
    return (x_mm, y_mm)
