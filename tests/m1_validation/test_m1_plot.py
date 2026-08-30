"""可視化（タスク 7.2、要件 8.1-8.10）。

本ファイルが固定するのは**描画そのもの**であって、算出でも判定でもない。
図は既に出た値を描くだけの層であり（要件 8.10）、ここで数値を作り直しては
ならない。画像ファイルの中身は直接照合できないので、**描画呼び出しを記録する
偽のバックエンド**を注入し、「何をどこへ描いたか」を値として固定する。
「ファイルが生成された」だけの検査は空振りである（タスク 7.1 の教訓）。

特に厚く固定するのは次の8点である。

1. **描画ライブラリを import するのはこのモジュールだけであり、しかも
   モジュールトップレベルでは import しない**（要件 8.8）。トップレベルへ
   置くと、未導入環境で `m1_validation` 全体が壊れる。
2. **未導入環境では可視化のみが利用不可になり、例外で全体が落ちない**
   （要件 8.9）。**真偽の両方を通す。**
3. **5種類の図がすべて描かれる**（要件 8.1-8.7）。1つ落とせばここで落ちる。
4. **予測落下地点は何サンプル目に基づくかが判別できる**（要件 8.2）。
   注記を落とす変異も、全部同じ注記にする変異も落ちる。
5. **許容窓の値が暫定目標値である旨が図の中に文字として描かれる**
   （要件 8.3）。注記は `Oq05Result` から**運ぶ**ものであって、図が持つ
   定数ではない——フィクスチャの注記は本ファイル局所のリテラルにしてある
   ので、実装へ焼き付ける変異は必ず落ちる。
6. **意味の違う値は取り違えられない**（上面図の x と y、予測と実測、待機位置と
   落下地点、許容窓の半径と直径、予測落下時刻と実際の落下時刻、共通の偏りと
   カメラ視線方向）。フィクスチャ上でもすべて別の値にしてある。
7. **0件側・欠測側の分岐へ入力が届く**（真値の欠測・軌道点列が空・未収束・
   カメラ視線方向が無い・誤差ベクトルが0件）。**必ず対で置く。**
8. **本モジュールは計算しない**（要件 8.10）。誤差のノルムは
   `PredictionError.hit_error_norm_mm` から読むのであって、`hit_error_mm`
   から組み直さない——フィクスチャは**ノルムと成分がわざと整合しない**値に
   してあるので、計算し直す実装は落ちる。
9. **描画呼び出しが実際に図になる経路**（`matplotlib_backend()` が返す
   本物のバックエンド）を、matplotlib のオブジェクト状態で固定する。
   偽のバックエンドが固定するのは「描画関数が**何を呼んだか**」だけであり、
   その呼び出しが図にならなくても `save()` がファイルさえ作れば
   「5種類の図が生成された」は満たされてしまう——**白紙の画像5枚**でも
   通る状態は空振りである（タスク 7.1 の「図と返り値とラベルは別の経路」の
   同型）。**とりわけ要件 8.3 の暫定注記が本物の図から消える変異**は、
   `save()` 後の `figure.texts` を見て初めて落ちる。
10. **`except ImportError` の狭さを対で固定する。** 依存の欠落だけを値へ
   倒し、書き込み失敗（`OSError`）は**伝播させる**——値へ倒すと「図が無い
   こと」に誰も気付けない。
11. **字形の欠落を握り潰さない。** 抑止するなら数えて `PlotResult` へ
   載せる。**真偽の両側**を固定する。

期待値はすべて**テスト局所のリテラル**から組む。実装の定数を import して自分
自身と比べる検査は置かない。ただし「実装の定数が互いに異なること」だけは、
取り違えを露見させるための**対の検査**として残す（タスク 6.3 の教訓）。
"""

from __future__ import annotations

import ast
import dataclasses
import sys
import warnings
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

import m1_validation
from m1_validation import plot as plot_module
from m1_validation.attribution import (
    AttributionResult,
    BiasComponent,
    RangeBand,
    ScatterComponent,
)
from m1_validation.judgement.oq05 import Oq05Result
from m1_validation.layout import ThrowLayout
from m1_validation.metrics.accuracy import AccuracyResult, PredictionError
from m1_validation.metrics.aggregate import ThrowAggregate, ThrowRow
from m1_validation.metrics.convergence import ConvergenceResult
from m1_validation.plot import (
    AXIS_ERROR_X_MM,
    AXIS_ERROR_Y_MM,
    AXIS_HIT_ERROR_MM,
    AXIS_HIT_TIME_MS,
    AXIS_OBSERVATION_TIME_MS,
    AXIS_SAMPLE_COUNT,
    AXIS_WORLD_X_MM,
    AXIS_WORLD_Y_MM,
    AXIS_WORLD_Z_MM,
    KIND_ATTRIBUTION,
    KIND_CONVERGENCE,
    KIND_TIMELINE,
    KIND_TOP_DOWN,
    KIND_TRAJECTORY,
    LABEL_ACTUAL_HIT,
    LABEL_ACTUAL_HIT_TIME,
    LABEL_BIAS,
    LABEL_CAMERA_RAY,
    LABEL_CONVERGED_AT,
    LABEL_CONVERGENCE_BAND,
    LABEL_CONVERGENCE_ERROR,
    LABEL_ERROR_VECTORS,
    LABEL_ESTIMATED_TRAJECTORY,
    LABEL_OBSERVED_SAMPLES,
    LABEL_PREDICTED_HIT_TIME,
    LABEL_PREDICTED_HITS,
    LABEL_STANDBY,
    LABEL_TIME_RESIDUAL,
    LABEL_TOLERANCE_WINDOW,
    MATPLOTLIB_BACKEND,
    MISSING_GLYPH_WARNING_TEMPLATE,
    PLOT_KINDS,
    PLOT_LIBRARY,
    TITLE_ATTRIBUTION,
    TITLE_CONVERGENCE,
    TITLE_TIMELINE,
    TITLE_TOP_DOWN,
    TITLE_TRAJECTORY,
    UNAVAILABLE_REASON,
    PlotAvailability,
    PlotResult,
    draw_attribution,
    draw_convergence,
    draw_timeline,
    draw_top_down,
    draw_trajectory,
    matplotlib_backend,
    plot_output_path,
    render_figures,
    visualization_availability,
)
from m1_validation.types import (
    Attribution,
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

CONFIG = PredictionConfig()

# ---------------------------------------------------------------------------
# テスト局所のリテラル
#
# **意味の違う数はフィクスチャ上でも別の値にする**（タスク 6.3 の教訓）。
# 取り違えは、値が一致していると露見しない。
# ---------------------------------------------------------------------------

RECORD_ID = "throw-plot-0031"
CALIBRATION_ID = "cal-plot-0077"
LAYOUT_ID = "layout-plot-0005"
SESSION_ID = "session-plot-0009"

#: 待機位置（World mm の水平2成分）。**x と y を別値**にしてあるので、
#: 軸を入れ替える変異が露見する。落下地点とも予測とも重ならない。
STANDBY_MM = (1310.0, -240.0)

#: レイアウトの寸法。**ここから導かれる暫定許容窓は 115.0 mm** であり、
#: 下の `OQ05_WINDOW_MM`（95.0）と**わざと違えてある**——窓を
#: `ThrowLayout.position_tolerance_mm` から取り直す実装（＝本モジュールで
#: 計算し直す実装）はここで落ちる。窓は `Oq05Result` から運ぶものである。
APERTURE_DIAMETER_MM = 300.0
OBJECT_DIAMETER_MM = 70.0
RELEASE_HEIGHT_MM = 1450.0

#: OQ-05 が算出済みの暫定許容窓（**半径** mm）。直径（190.0）とも
#: 半分（47.5）とも違う値として固定する。
OQ05_WINDOW_MM = 95.0

#: 実際の落下地点（World mm）。x / y / z をすべて別値にしてあるので、
#: 水平2成分の取り違えも、高さの混入も露見する。
IMPACT_POINT_MM = (1042.0, 118.0, 9.0)
IMPACT_TIME_MS = 5793.0
RELEASE_TIME_MS = 5042.0

#: 予測落下地点系列。**サンプル数 3 / 5 / 8 はすべて別値**であり、
#: 落下地点も落下時刻も観測時刻もそれぞれ相異なる。
PREDICTED_SAMPLE_COUNTS = (3, 5, 8)
PREDICTED_HITS_MM = ((1180.0, 61.0), (1096.0, 145.0), (1057.0, 126.0))
PREDICTED_HIT_TIMES_MS = (5860.0, 5812.0, 5799.0)
BASED_ON_TIMES_MS = (5240.0, 5286.0, 5333.0)
PREDICTION_RESIDUALS_MM = (7.5, 4.25, 2.75)
REMAINING_TIMES_MS = (620.0, 526.0, 466.0)

#: 予測落下地点に添える注記。**3つとも別**である。
EXPECTED_SAMPLE_ANNOTATIONS = ("3 サンプル", "5 サンプル", "8 サンプル")

#: 観測点列（World mm）。x / y / z をすべて別値にしてある。
OBSERVED_POINTS_MM = (
    (-1850.0, 40.0, 1420.0),
    (-1240.0, 22.0, 1610.0),
    (-610.0, 4.0, 1505.0),
    (55.0, -14.0, 1080.0),
)

#: 推定軌道（**算出済みの点列を受け取るだけ**。本モジュールは軌道を推定しない）。
TRAJECTORY_POINTS_MM = (
    (-2000.0, 45.0, 1300.0),
    (-1000.0, 16.0, 1580.0),
    (0.0, -13.0, 1120.0),
    (1042.0, -43.0, 0.0),
)

#: 誤差系列。**`hit_error_norm_mm` は `hit_error_mm` のノルムとわざと
#: 一致させていない**——ノルムを成分から組み直す実装はここで落ちる
#: （要件 8.10「集計済みの値を描画するだけ」）。
HIT_ERRORS_MM = ((138.0, -57.0), (54.0, 27.0), (15.0, 8.0))
HIT_ERROR_NORMS_MM = (201.0, 93.0, 31.0)
TIME_ERRORS_MS = (67.0, 19.0, 6.0)

#: 収束の材料。帯域は窓（95.0）ともレイアウト由来（115.0）とも別値である。
CONVERGENCE_BAND_MM = 42.0
CONVERGED_AT = 5
CONVERGENCE_VALID_SAMPLES = 11
CONVERGENCE_FINAL_ERROR_MM = 33.0
CONVERGENCE_RATIONALE = "＜局所リテラル: 収束の根拠＞"
CONVERGENCE_CRITERION = "＜局所リテラル: 収束の判定規則の全文＞"

#: 帰属の材料。誤差ベクトル3件・共通の偏り・カメラ視線方向は**すべて別**。
ERROR_VECTORS_MM = ((21.0, -34.0), (48.0, -12.0), (-9.0, 63.0))
VECTOR_RECORD_IDS = ("throw-a-0001", "throw-b-0002", "throw-c-0003")
#: 誤差ベクトルを持たない投擲。**注記へ現れてはならない。**
NO_VECTOR_RECORD_ID = "throw-d-0004"
BIAS_VECTOR_MM = (23.0, -41.0)
CAMERA_RAY_HORIZONTAL = (0.6, 0.8)
ATTRIBUTION_CRITERION = "＜局所リテラル: 帰属の判定規則の全文＞"

#: `Oq05Result` から**運ぶ**注記。実装が持つ定数ではないので、焼き付ける
#: 変異が落ちるように本ファイル局所のリテラルにしてある。
OQ05_OBJECT_SCOPE_NOTE = "＜局所リテラル: 許容窓は対象物の寸法に依存し未決である＞"
OQ05_UPPER_BOUND_NOTE = "＜局所リテラル: 予測側から見た成功率の上限である＞"
OQ05_MATERIAL_ONLY_NOTE = "＜局所リテラル: OQ-05 は決着させず材料の提示にとどめる＞"
OQ05_CRITERION = "＜局所リテラル: OQ-05 の材料の作り方の全文＞"


# ---------------------------------------------------------------------------
# 描画呼び出しを記録する偽のバックエンド
#
# **画像ファイルの中身は直接照合できない。** 「何をどこへ描いたか」を値として
# 取り出せるようにするのが本クラスの唯一の役目である。
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class Call:
    """1回の描画呼び出し。"""

    op: str
    payload: Mapping[str, object]


class RecordingBackend:
    """描画呼び出しを順に記録するだけのバックエンド。

    **このバックエンドが固定するのは「描画関数が何を呼んだか」だけである。**
    その呼び出しが実際の図になることは `TestMatplotlibBackend` が別に固定する。
    """

    def __init__(self, *, missing_glyphs: int = 0) -> None:
        self.calls: list[Call] = []
        self.missing_glyphs = missing_glyphs

    def open_figure(
        self, *, kind: str, title: str, x_label: str, y_label: str
    ) -> None:
        self.calls.append(
            Call(
                "open_figure",
                {"kind": kind, "title": title, "x_label": x_label, "y_label": y_label},
            )
        )

    def points(
        self,
        *,
        label: str,
        points: Sequence[tuple[float, float]],
        annotations: Sequence[str],
    ) -> None:
        self.calls.append(
            Call(
                "points",
                {
                    "label": label,
                    "points": tuple(points),
                    "annotations": tuple(annotations),
                },
            )
        )

    def polyline(self, *, label: str, points: Sequence[tuple[float, float]]) -> None:
        self.calls.append(Call("polyline", {"label": label, "points": tuple(points)}))

    def reference_line(self, *, label: str, axis: str, value: float) -> None:
        self.calls.append(
            Call("reference_line", {"label": label, "axis": axis, "value": value})
        )

    def circle(
        self, *, label: str, center: tuple[float, float], radius: float
    ) -> None:
        self.calls.append(
            Call("circle", {"label": label, "center": center, "radius": radius})
        )

    def arrow(
        self,
        *,
        label: str,
        origin: tuple[float, float],
        vector: tuple[float, float],
    ) -> None:
        self.calls.append(
            Call("arrow", {"label": label, "origin": origin, "vector": vector})
        )

    def note(self, *, text: str) -> None:
        self.calls.append(Call("note", {"text": text}))

    def save(self, path: Path) -> None:
        self.calls.append(Call("save", {"path": path}))

    def missing_glyph_count(self) -> int:
        return self.missing_glyphs


class FailingSaveBackend(RecordingBackend):
    """書き出しだけが失敗するバックエンド（**依存の欠落ではない失敗**）。"""

    def save(self, path: Path) -> None:
        raise OSError("テスト用: 書き出し先が使えない")


class ExplodingFactory:
    """描画ライブラリが導入されていない環境を模す。"""

    def __init__(self) -> None:
        self.called = 0

    def __call__(self) -> RecordingBackend:
        self.called += 1
        raise ImportError("テスト用: matplotlib が無い")


class BrokenFactory:
    """依存の欠落**以外**の理由で失敗する生成器。"""

    def __call__(self) -> RecordingBackend:
        raise OSError("テスト用: 出力先を用意できない")


# --- 記録の読み出しヘルパ ---------------------------------------------------


def ops(calls: Sequence[Call], op: str) -> tuple[Call, ...]:
    return tuple(call for call in calls if call.op == op)


def by_label(calls: Sequence[Call], op: str, label: str) -> Mapping[str, object]:
    """指定した図形操作のうち、そのラベルのものを1件だけ取り出す。"""
    matched = [call for call in calls if call.op == op and call.payload["label"] == label]
    assert len(matched) == 1, f"{op}/{label} が {len(matched)} 件（1件であるべき）"
    return matched[0].payload


def has_label(calls: Sequence[Call], op: str, label: str) -> bool:
    return any(call.op == op and call.payload["label"] == label for call in calls)


def labels(calls: Sequence[Call]) -> tuple[str, ...]:
    return tuple(
        str(call.payload["label"]) for call in calls if "label" in call.payload
    )


def notes(calls: Sequence[Call]) -> tuple[str, ...]:
    return tuple(str(call.payload["text"]) for call in ops(calls, "note"))


def figure(calls: Sequence[Call], kind: str) -> tuple[Call, ...]:
    """`open_figure(kind=...)` から対応する `save` までを切り出す。"""
    section: list[Call] = []
    inside = False
    for call in calls:
        if call.op == "open_figure":
            inside = call.payload["kind"] == kind
        if inside:
            section.append(call)
        if inside and call.op == "save":
            break
    assert section, f"図 {kind} が描かれていない"
    return tuple(section)


# ---------------------------------------------------------------------------
# フィクスチャ組み立て
# ---------------------------------------------------------------------------


def trajectory() -> TrajectoryParameters:
    return TrajectoryParameters(
        t_ref_ms=BASED_ON_TIMES_MS[0],
        x0_mm=-1850.0,
        y0_mm=40.0,
        z0_mm=1420.0,
        estimated_vx_mm_s=3000.0,
        estimated_vy_mm_s=-500.0,
        estimated_vz_mm_s=1020.0,
        gravity_mm_s2=CONFIG.gravity_mm_s2,
    )


def valid_prediction(index: int) -> Prediction:
    return Prediction(
        predicted_hit_x_mm=PREDICTED_HITS_MM[index][0],
        predicted_hit_y_mm=PREDICTED_HITS_MM[index][1],
        predicted_hit_time_ms=PREDICTED_HIT_TIMES_MS[index],
        remaining_time_ms=REMAINING_TIMES_MS[index],
        estimated_vx_mm_s=3000.0,
        estimated_vy_mm_s=-500.0,
        estimated_vz_mm_s=1020.0,
        residual=PREDICTION_RESIDUALS_MM[index],
        trajectory=trajectory(),
        sample_count=PREDICTED_SAMPLE_COUNTS[index],
        based_on_time_ms=BASED_ON_TIMES_MS[index],
        elapsed_ms=1.25,
        config=CONFIG,
    )


def invalid_prediction() -> InvalidPrediction:
    return InvalidPrediction(
        reason=InvalidReason.INSUFFICIENT_SAMPLES,
        detail="テスト用の無効予測",
        sample_count=2,
        based_on_time_ms=5200.0,
        elapsed_ms=0.5,
        config=CONFIG,
    )


def outcomes() -> tuple[PredictionOutcome, ...]:
    """**無効予測を先頭と中間の両方へ置く**——読み飛ばし方の誤りを露見させる。"""
    return (
        invalid_prediction(),
        valid_prediction(0),
        valid_prediction(1),
        invalid_prediction(),
        valid_prediction(2),
    )


def record(
    *,
    samples: Sequence[Sample] | None = None,
    predictions: Sequence[PredictionOutcome] | None = None,
) -> ThrowRecord:
    return ThrowRecord(
        record_id=RECORD_ID,
        source=SourceKind.RECORDED,
        config=CONFIG,
        samples=(
            tuple(
                Sample(t_ms=BASED_ON_TIMES_MS[0] + 20.0 * index, x_mm=x, y_mm=y, z_mm=z)
                for index, (x, y, z) in enumerate(OBSERVED_POINTS_MM)
            )
            if samples is None
            else tuple(samples)
        ),
        predictions=outcomes() if predictions is None else tuple(predictions),
    )


def truth(
    *, impact_point: bool = True, impact_time: bool = True
) -> ThrowTruth:
    return ThrowTruth(
        record_id=RECORD_ID,
        impact_point_world_mm=(
            TruthValue(
                value=IMPACT_POINT_MM,
                method=TruthMethod.MEASURED,
                uncertainty_mm=5.0,
                uncertainty_ms=None,
                source="床のマークをメジャーで測った（テスト）",
            )
            if impact_point
            else TruthValue(
                value=None,
                method=TruthMethod.MISSING,
                uncertainty_mm=None,
                uncertainty_ms=None,
                source="未記入（テスト）",
            )
        ),
        impact_time_ms=(
            TruthValue(
                value=IMPACT_TIME_MS,
                method=TruthMethod.INTERPOLATED,
                uncertainty_mm=None,
                uncertainty_ms=8.0,
                source="床面高さを跨ぐ区間の内挿（テスト）",
            )
            if impact_time
            else TruthValue(
                value=None,
                method=TruthMethod.MISSING,
                uncertainty_mm=None,
                uncertainty_ms=None,
                source="跨ぐ区間が無い（テスト）",
            )
        ),
        release_time_ms=TruthValue(
            value=RELEASE_TIME_MS,
            method=TruthMethod.EXTRAPOLATED,
            uncertainty_mm=None,
            uncertainty_ms=12.0,
            source="推定軌道の外挿（テスト）",
        ),
        external_mark_delta_ms=None,
    )


def prediction_error(index: int, *, time_error: bool = True) -> PredictionError:
    return PredictionError(
        sample_count=PREDICTED_SAMPLE_COUNTS[index],
        based_on_time_ms=BASED_ON_TIMES_MS[index],
        hit_error_mm=HIT_ERRORS_MM[index],
        hit_error_norm_mm=HIT_ERROR_NORMS_MM[index],
        time_error_ms=TIME_ERRORS_MS[index] if time_error else None,
        residual_mm=PREDICTION_RESIDUALS_MM[index],
        remaining_time_ms=REMAINING_TIMES_MS[index],
    )


def accuracy(
    *, count: int = 3, time_error: bool = True
) -> AccuracyResult:
    errors = tuple(prediction_error(index, time_error=time_error) for index in range(count))
    return AccuracyResult(
        errors=errors,
        first_valid=errors[0] if errors else None,
        final=errors[-1] if errors else None,
        invalid_counts=((InvalidReason.INSUFFICIENT_SAMPLES, 2),),
    )


def judgement(*, question: str, criterion: str, verdict: str, rationale: str) -> Judgement:
    return Judgement(
        question=question,
        criterion=criterion,
        verdict=verdict,
        rationale=rationale,
        evidence={},
        provisional=False,
    )


def convergence(*, converged_at: int | None = CONVERGED_AT) -> ConvergenceResult:
    return ConvergenceResult(
        valid_samples=CONVERGENCE_VALID_SAMPLES,
        converged_at=converged_at,
        band_mm=CONVERGENCE_BAND_MM,
        final_error_mm=CONVERGENCE_FINAL_ERROR_MM,
        judgement=judgement(
            question="convergence",
            criterion=CONVERGENCE_CRITERION,
            verdict="converged" if converged_at is not None else "not_converged",
            rationale=CONVERGENCE_RATIONALE,
        ),
    )


def layout() -> ThrowLayout:
    return ThrowLayout(
        layout_id=LAYOUT_ID,
        release_position_world_mm=(-2100.0, 60.0, RELEASE_HEIGHT_MM),
        release_height_mm=RELEASE_HEIGHT_MM,
        throw_direction_deg=12.0,
        standby_position_world_mm=STANDBY_MM,
        object_diameter_mm=OBJECT_DIAMETER_MM,
        aperture_diameter_mm=APERTURE_DIAMETER_MM,
        camera_position_world_mm=(0.0, -1800.0, 1200.0),
        notes="テスト用の仮レイアウト",
    )


def oq05() -> Oq05Result:
    return Oq05Result(
        window_mm=OQ05_WINDOW_MM,
        within_window_ratio=0.625,
        within_window_count=5,
        evaluated_throw_count=8,
        confidence_level=0.9,
        required_trials={"0.2": 37},
        upper_bound_note=OQ05_UPPER_BOUND_NOTE,
        object_scope_note=OQ05_OBJECT_SCOPE_NOTE,
        material_only_note=OQ05_MATERIAL_ONLY_NOTE,
        judgement=judgement(
            question="OQ-05",
            criterion=OQ05_CRITERION,
            verdict="material_only",
            rationale="＜局所リテラル: OQ-05 の根拠＞",
        ),
    )


def throw_rows(*, with_vectors: bool = True) -> tuple[ThrowRow, ...]:
    rows: list[ThrowRow] = []
    if with_vectors:
        for record_id, vector in zip(VECTOR_RECORD_IDS, ERROR_VECTORS_MM, strict=True):
            rows.append(
                ThrowRow(
                    record_id=record_id,
                    session_id=SESSION_ID,
                    source="recorded",
                    live=False,
                    truth_available=True,
                    error_vector_mm=vector,
                    values={},
                )
            )
    rows.append(
        ThrowRow(
            record_id=NO_VECTOR_RECORD_ID,
            session_id=SESSION_ID,
            source="recorded",
            live=False,
            truth_available=False,
            error_vector_mm=None,
            values={},
        )
    )
    return tuple(rows)


def aggregate(*, with_vectors: bool = True) -> ThrowAggregate:
    return ThrowAggregate(
        calibration_id=CALIBRATION_ID,
        verified=True,
        session_ids=(SESSION_ID,),
        throw_count=4,
        failed_throw_count=1,
        valid_throw_count=3,
        live_throw_count=0,
        converged_count=2,
        not_converged_count=1,
        not_measurable_count=1,
        single_prediction_throw_count=0,
        provisional=False,
        provisional_reasons=(),
        items={},
        error_vectors=ERROR_VECTORS_MM if with_vectors else (),
        per_throw=throw_rows(with_vectors=with_vectors),
    )


def attribution() -> AttributionResult:
    return AttributionResult(
        bias=BiasComponent(
            vector_mm=BIAS_VECTOR_MM,
            norm_mm=47.02,
            significance_ratio=3.75,
            world_fixed_agreement_deg=12.5,
            camera_ray_agreement_deg=71.25,
            degenerate=False,
            attribution=Attribution.CALIBRATION,
        ),
        scatter=ScatterComponent(
            rms_mm=14.5,
            bootstrap_rms_mm=9.25,
            residual_median_mm=5.125,
            attribution=Attribution.OBSERVATION_NOISE,
        ),
        range_bands=(
            RangeBand(
                range_lo_mm=1000.0,
                range_hi_mm=1500.0,
                throw_count=4,
                mean_error_norm_mm=31.5,
            ),
        ),
        calibration_reference={"bias_state": "recognized"},
        judgement=judgement(
            question="attribution",
            criterion=ATTRIBUTION_CRITERION,
            verdict="bias=calibration/scatter=observation_noise",
            rationale="＜局所リテラル: 帰属の根拠＞",
        ),
    )


def render_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "record": record(),
        "truth": truth(),
        "accuracy": accuracy(),
        "convergence": convergence(),
        "layout": layout(),
        "oq05": oq05(),
        "aggregate": aggregate(),
        "attribution": attribution(),
        "trajectory_points_world_mm": TRAJECTORY_POINTS_MM,
        "camera_ray_horizontal": CAMERA_RAY_HORIZONTAL,
    }
    base.update(overrides)
    return base


# ===========================================================================
# 1. 依存の隔離（要件 8.8）
# ===========================================================================


class TestDependencyIsolation:
    """**描画ライブラリを import するのはこのモジュールだけ**（要件 8.8）。"""

    def test_no_other_module_imports_the_drawing_library(self) -> None:
        """`plot.py` 以外の `m1_validation` は matplotlib を import しない。

        1つでも他モジュールが import すると、依存が実機側の実行経路へ入り、
        未導入環境で集計・判断まで巻き添えで落ちる。
        """
        package_root = Path(m1_validation.__file__).parent
        offenders: list[str] = []
        for path in sorted(package_root.rglob("*.py")):
            if path.name == "plot.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.split(".")[0] == "matplotlib":
                            offenders.append(f"{path.name}:{node.lineno}")
                elif (
                    isinstance(node, ast.ImportFrom)
                    and (node.module or "").split(".")[0] == "matplotlib"
                ):
                    offenders.append(f"{path.name}:{node.lineno}")
        assert offenders == []

    def test_the_plot_module_never_imports_the_library_at_module_level(self) -> None:
        """トップレベル import は禁止。**未導入環境でも import できること**が要る。

        トップレベルへ置くと `m1_validation.plot` の import 自体が失敗し、
        「可視化のみ利用不可」（要件 8.9）が成立しなくなる。
        """
        tree = ast.parse(
            Path(plot_module.__file__).read_text(encoding="utf-8")
        )
        top_level: list[str] = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                top_level += [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                top_level.append((node.module or "").split(".")[0])
        assert "matplotlib" not in top_level

    def test_the_plot_module_does_import_the_library_somewhere(self) -> None:
        """**対の検査**: 遅延 import が実際に書かれている（描画の実体がある）。"""
        tree = ast.parse(
            Path(plot_module.__file__).read_text(encoding="utf-8")
        )
        found = any(
            (isinstance(node, ast.Import)
             and any(alias.name.split(".")[0] == "matplotlib" for alias in node.names))
            or (isinstance(node, ast.ImportFrom)
                and (node.module or "").split(".")[0] == "matplotlib")
            for node in ast.walk(tree)
        )
        assert found

    def test_library_and_backend_names_are_fixed(self) -> None:
        """**非対話バックエンド**を使う（画面を要求しない。要件 8.8）。"""
        assert PLOT_LIBRARY == "matplotlib"
        assert MATPLOTLIB_BACKEND == "Agg"

    def test_the_non_interactive_backend_is_actually_selected(self) -> None:
        """**定数を持つだけでは「その設定で動いている」ことは固定できない。**

        別のバックエンドへ倒した状態から入ると、実装が実際に `Agg` へ切り
        替えているかどうかで結果が変わる。`matplotlib.use()` の呼び出しを
        落とす変異はここで落ちる（タスク 6.1 の教訓の同型）。
        """
        matplotlib = pytest.importorskip("matplotlib")
        previous = matplotlib.get_backend()
        try:
            matplotlib.use("pdf")
            assert matplotlib.get_backend().lower() == "pdf"
            visualization_availability()
            assert matplotlib.get_backend().lower() == "agg"
        finally:
            matplotlib.use(previous)


# ===========================================================================
# 2. 利用可否（要件 8.9）——**真偽の両方を通す**
# ===========================================================================


class TestAvailability:
    def test_available_when_the_library_is_installed(self) -> None:
        result = visualization_availability()
        assert isinstance(result, PlotAvailability)
        assert result.available is True
        assert result.reason is None
        assert result.library == "matplotlib"
        assert result.backend == "Agg"

    def test_unavailable_when_the_library_is_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`sys.modules` を汚して**本物の遅延 import を失敗させる**。"""
        monkeypatch.setitem(sys.modules, "matplotlib", None)
        result = visualization_availability()
        assert result.available is False
        assert result.library == "matplotlib"
        assert result.reason == UNAVAILABLE_REASON

    def test_unavailable_reason_states_only_visualization_is_lost(self) -> None:
        """**一文ずつ**固定する（要件 8.9 の内容がここに書かれている）。"""
        assert UNAVAILABLE_REASON == (
            "描画ライブラリ matplotlib が導入されていないため、"
            "可視化のみを利用不可とする（要件 8.9）。"
            "計測・集計・判断の経路は停止しない"
            "——可視化は開発用であり、本番処理の必須要件ではない（A-8）。"
            "図が必要なら任意依存 m1-viz を導入して再実行すること。"
        )

    def test_render_reports_unavailable_instead_of_raising(
        self, tmp_output_dir: Path
    ) -> None:
        """**例外で落とさず、利用不可を値として返す**（要件 8.9）。"""
        factory = ExplodingFactory()
        result = render_figures(
            output_root=tmp_output_dir,
            backend_factory=factory,
            **render_kwargs(),
        )
        assert isinstance(result, PlotResult)
        assert result.available is False
        assert result.reason == UNAVAILABLE_REASON
        assert result.paths == ()
        assert result.kinds == ()
        assert result.missing_glyph_count == 0
        assert result.font_warning is None
        assert factory.called == 1
        assert list(tmp_output_dir.rglob("*.png")) == []

    def test_render_reports_available_with_a_working_backend(
        self, tmp_output_dir: Path
    ) -> None:
        """**対の検査**: 使える環境では利用不可にならない。"""
        result = render_figures(
            output_root=tmp_output_dir,
            backend_factory=RecordingBackend,
            **render_kwargs(),
        )
        assert result.available is True
        assert result.reason is None

    def test_render_with_the_real_library_writes_five_image_files(
        self, tmp_output_dir: Path
    ) -> None:
        """既定のバックエンド（本物の matplotlib）で**5種類の画像が出る**。"""
        result = render_figures(output_root=tmp_output_dir, **render_kwargs())
        assert result.available is True
        assert result.kinds == (
            "top_down",
            "timeline",
            "trajectory",
            "convergence",
            "attribution",
        )
        assert result.paths == (
            tmp_output_dir / "plots" / f"{RECORD_ID}-top_down.png",
            tmp_output_dir / "plots" / f"{RECORD_ID}-timeline.png",
            tmp_output_dir / "plots" / f"{RECORD_ID}-trajectory.png",
            tmp_output_dir / "plots" / f"{RECORD_ID}-convergence.png",
            tmp_output_dir / "plots" / f"{CALIBRATION_ID}-attribution.png",
        )
        for path in result.paths:
            assert path.is_file()
            assert path.stat().st_size > 0

    def test_render_with_the_real_library_survives_missing_truth(
        self, tmp_output_dir: Path
    ) -> None:
        """**欠測側でも本物のバックエンドで描き切る**（0 で埋めない経路）。"""
        result = render_figures(
            output_root=tmp_output_dir,
            **render_kwargs(
                truth=truth(impact_point=False, impact_time=False),
                convergence=convergence(converged_at=None),
                aggregate=aggregate(with_vectors=False),
                trajectory_points_world_mm=(),
                camera_ray_horizontal=None,
            ),
        )
        assert result.available is True
        assert len(result.paths) == 5
        for path in result.paths:
            assert path.is_file()


# ===========================================================================
# 3. 出力先と図の種類
# ===========================================================================


class TestOutputLayout:
    def test_output_path_is_stem_and_kind_under_plots(self, tmp_path: Path) -> None:
        assert plot_output_path(tmp_path, "throw-0007", "convergence") == (
            tmp_path / "plots" / "throw-0007-convergence.png"
        )

    def test_five_kinds_are_declared_in_a_fixed_order(self) -> None:
        assert PLOT_KINDS == (
            "top_down",
            "timeline",
            "trajectory",
            "convergence",
            "attribution",
        )
        assert (
            KIND_TOP_DOWN,
            KIND_TIMELINE,
            KIND_TRAJECTORY,
            KIND_CONVERGENCE,
            KIND_ATTRIBUTION,
        ) == PLOT_KINDS

    def test_render_opens_and_saves_each_kind_exactly_once(
        self, tmp_output_dir: Path
    ) -> None:
        backend = RecordingBackend()
        render_figures(
            output_root=tmp_output_dir,
            backend_factory=lambda: backend,
            **render_kwargs(),
        )
        opened = tuple(
            str(call.payload["kind"]) for call in ops(backend.calls, "open_figure")
        )
        assert opened == (
            "top_down",
            "timeline",
            "trajectory",
            "convergence",
            "attribution",
        )
        saved = tuple(call.payload["path"] for call in ops(backend.calls, "save"))
        assert saved == (
            tmp_output_dir / "plots" / f"{RECORD_ID}-top_down.png",
            tmp_output_dir / "plots" / f"{RECORD_ID}-timeline.png",
            tmp_output_dir / "plots" / f"{RECORD_ID}-trajectory.png",
            tmp_output_dir / "plots" / f"{RECORD_ID}-convergence.png",
            tmp_output_dir / "plots" / f"{CALIBRATION_ID}-attribution.png",
        )

    def test_titles_are_fixed_per_kind(self) -> None:
        """**見出しは読み手が最初に見る**（タスク 6.3 の教訓）。全文一致で固定する。"""
        assert TITLE_TOP_DOWN == (
            "床平面の上面図: 予測落下地点系列・実際の落下地点・待機位置・暫定許容窓"
        )
        assert TITLE_TIMELINE == "落下時刻の推移: 予測落下時刻・実際の落下時刻・残差"
        assert TITLE_TRAJECTORY == "World 座標系の軌道図: 観測点列と推定軌道"
        assert TITLE_CONVERGENCE == "収束図: サンプル数と落下地点の誤差"
        assert TITLE_ATTRIBUTION == (
            "帰属図: 誤差ベクトルの散布・共通の偏り・カメラ視線方向"
        )

    def test_each_figure_carries_its_own_title(self, tmp_output_dir: Path) -> None:
        """**対の検査**: 見出しが図と正しく対応している（取り違えが落ちる）。"""
        backend = RecordingBackend()
        render_figures(
            output_root=tmp_output_dir,
            backend_factory=lambda: backend,
            **render_kwargs(),
        )
        titles = {
            str(call.payload["kind"]): str(call.payload["title"])
            for call in ops(backend.calls, "open_figure")
        }
        assert titles == {
            "top_down": TITLE_TOP_DOWN,
            "timeline": TITLE_TIMELINE,
            "trajectory": TITLE_TRAJECTORY,
            "convergence": TITLE_CONVERGENCE,
            "attribution": TITLE_ATTRIBUTION,
        }

    def test_labels_and_titles_are_mutually_distinct(self) -> None:
        """**対の検査**: 取り違えが `!=` で露見する土台を作る（値の固定は各節）。"""
        constants = (
            LABEL_PREDICTED_HITS,
            LABEL_ACTUAL_HIT,
            LABEL_STANDBY,
            LABEL_TOLERANCE_WINDOW,
            LABEL_PREDICTED_HIT_TIME,
            LABEL_ACTUAL_HIT_TIME,
            LABEL_TIME_RESIDUAL,
            LABEL_OBSERVED_SAMPLES,
            LABEL_ESTIMATED_TRAJECTORY,
            LABEL_CONVERGENCE_ERROR,
            LABEL_CONVERGENCE_BAND,
            LABEL_CONVERGED_AT,
            LABEL_ERROR_VECTORS,
            LABEL_BIAS,
            LABEL_CAMERA_RAY,
            TITLE_TOP_DOWN,
            TITLE_TIMELINE,
            TITLE_TRAJECTORY,
            TITLE_CONVERGENCE,
            TITLE_ATTRIBUTION,
            AXIS_WORLD_X_MM,
            AXIS_WORLD_Y_MM,
            AXIS_WORLD_Z_MM,
            AXIS_OBSERVATION_TIME_MS,
            AXIS_HIT_TIME_MS,
            AXIS_SAMPLE_COUNT,
            AXIS_HIT_ERROR_MM,
            AXIS_ERROR_X_MM,
            AXIS_ERROR_Y_MM,
        )
        assert len(set(constants)) == len(constants)


# ===========================================================================
# 4. 上面図（要件 8.1, 8.2, 8.3）
# ===========================================================================


class TestTopDown:
    def top_down(self, **overrides: object) -> tuple[Call, ...]:
        backend = RecordingBackend()
        draw_top_down(
            backend,
            record=overrides.get("record", record()),
            truth=overrides.get("truth", truth()),
            layout=overrides.get("layout", layout()),
            oq05=overrides.get("oq05", oq05()),
        )
        return tuple(backend.calls)

    def test_axes_are_the_floor_plane(self) -> None:
        opened = self.top_down()[0]
        assert opened.payload["kind"] == "top_down"
        assert opened.payload["x_label"] == AXIS_WORLD_X_MM
        assert opened.payload["y_label"] == AXIS_WORLD_Y_MM
        assert AXIS_WORLD_X_MM == "World X 座標（mm）"
        assert AXIS_WORLD_Y_MM == "World Y 座標（mm）"

    def test_predicted_hit_series_is_drawn_in_world_xy_order(self) -> None:
        """**x と y を入れ替える変異が落ちる**（フィクスチャは x ≠ y）。"""
        payload = by_label(self.top_down(), "points", LABEL_PREDICTED_HITS)
        assert payload["points"] == (
            (1180.0, 61.0),
            (1096.0, 145.0),
            (1057.0, 126.0),
        )
        assert LABEL_PREDICTED_HITS == "予測落下地点系列"

    def test_predicted_hit_series_skips_invalid_predictions(self) -> None:
        """無効予測は落下地点を持たない。**0 で埋めて系列へ載せない。**"""
        payload = by_label(
            self.top_down(record=record(predictions=(invalid_prediction(),))),
            "points",
            LABEL_PREDICTED_HITS,
        )
        assert payload["points"] == ()
        assert payload["annotations"] == ()

    def test_each_predicted_hit_is_annotated_with_its_sample_count(self) -> None:
        """**何サンプル目に基づくかが判別できる**（要件 8.2）。"""
        payload = by_label(self.top_down(), "points", LABEL_PREDICTED_HITS)
        assert payload["annotations"] == ("3 サンプル", "5 サンプル", "8 サンプル")
        assert len(set(EXPECTED_SAMPLE_ANNOTATIONS)) == 3

    def test_actual_hit_is_a_separate_series_from_the_predictions(self) -> None:
        """**予測と実測を入れ替える変異が落ちる。**"""
        payload = by_label(self.top_down(), "points", LABEL_ACTUAL_HIT)
        assert payload["points"] == ((1042.0, 118.0),)
        assert payload["annotations"] == ()
        assert LABEL_ACTUAL_HIT == "実際の落下地点"

    def test_standby_position_is_a_separate_series_from_the_actual_hit(self) -> None:
        """**待機位置と落下地点を入れ替える変異が落ちる。**"""
        payload = by_label(self.top_down(), "points", LABEL_STANDBY)
        assert payload["points"] == ((1310.0, -240.0),)
        assert LABEL_STANDBY == "待機位置"

    def test_tolerance_window_is_a_circle_of_the_oq05_window_radius(self) -> None:
        """**半径と直径の取り違え・レイアウトから計算し直す変異が落ちる。**

        レイアウト由来の窓は 115.0 mm であり、`Oq05Result` の 95.0 mm とは
        別値にしてある。本モジュールは計算しない（要件 8.10）。
        """
        payload = by_label(self.top_down(), "circle", LABEL_TOLERANCE_WINDOW)
        assert payload["radius"] == 95.0
        assert payload["center"] == (1042.0, 118.0)
        assert LABEL_TOLERANCE_WINDOW == "暫定許容窓"

    def test_window_note_states_it_is_a_provisional_target(self) -> None:
        """**許容窓の値が暫定目標値である旨を図に描く**（要件 8.3）。

        窓の値・暫定目標値である旨・合否条件ではない旨・`Oq05Result` から
        運んだ留保の4つが、**1つの注記として全文で**現れる。
        """
        assert notes(self.top_down())[0] == (
            "暫定許容窓の半径 95 mm は暫定目標値であって合否条件ではない"
            "（要件 8.3）。この円の外に出た予測を失敗と判定してはならない。"
            f"{OQ05_OBJECT_SCOPE_NOTE}"
        )

    def test_window_note_carries_the_scope_note_from_oq05(self) -> None:
        """**注記は運ぶものであって、この図が持つ定数ではない。**"""
        altered = dataclasses.replace(
            oq05(), object_scope_note="＜別のリテラル: 差し替えた留保＞"
        )
        assert notes(self.top_down(oq05=altered))[0].endswith(
            "＜別のリテラル: 差し替えた留保＞"
        )

    def test_window_note_is_drawn_even_when_the_actual_hit_is_missing(self) -> None:
        """欠測側でも**暫定である旨は消えない**（窓の値そのものは材料である）。"""
        drawn = notes(self.top_down(truth=truth(impact_point=False)))
        assert drawn[0].startswith("暫定許容窓の半径 95 mm は暫定目標値であって")

    def test_missing_actual_hit_omits_the_point_and_the_window(self) -> None:
        """**0 mm の点として描かない**（要件 4.6。欠測は欠測として出す）。"""
        calls = self.top_down(truth=truth(impact_point=False))
        assert by_label(calls, "points", LABEL_ACTUAL_HIT)["points"] == ()
        assert not has_label(calls, "circle", LABEL_TOLERANCE_WINDOW)
        assert notes(calls)[1] == (
            "実際の落下地点が欠測のため、実測点と暫定許容窓を描いていない"
            "（要件 4.6）。0 mm の点として描かない。"
        )

    def test_missing_note_is_absent_when_the_actual_hit_is_present(self) -> None:
        """**対の検査**: 欠測でないときに欠測の注記を出さない。"""
        drawn = notes(self.top_down())
        assert not any("欠測のため" in text for text in drawn)

    def test_the_figure_is_saved_by_the_caller_not_by_the_draw_function(self) -> None:
        """描画関数は保存しない（保存先は `render_figures` が決める）。"""
        assert ops(self.top_down(), "save") == ()


# ===========================================================================
# 5. 時系列図（要件 8.4）
# ===========================================================================


class TestTimeline:
    def timeline(self, **overrides: object) -> tuple[Call, ...]:
        backend = RecordingBackend()
        draw_timeline(
            backend,
            record=overrides.get("record", record()),
            truth=overrides.get("truth", truth()),
            accuracy=overrides.get("accuracy", accuracy()),
        )
        return tuple(backend.calls)

    def test_axes_are_observation_time_and_hit_time(self) -> None:
        opened = self.timeline()[0]
        assert opened.payload["kind"] == "timeline"
        assert opened.payload["x_label"] == AXIS_OBSERVATION_TIME_MS
        assert opened.payload["y_label"] == AXIS_HIT_TIME_MS
        assert AXIS_OBSERVATION_TIME_MS == "観測時刻（ms）"
        assert AXIS_HIT_TIME_MS == "落下時刻・残差（ms）"

    def test_predicted_hit_time_series_uses_the_prediction_values(self) -> None:
        payload = by_label(self.timeline(), "polyline", LABEL_PREDICTED_HIT_TIME)
        assert payload["points"] == (
            (5240.0, 5860.0),
            (5286.0, 5812.0),
            (5333.0, 5799.0),
        )
        assert LABEL_PREDICTED_HIT_TIME == "予測落下時刻"

    def test_actual_hit_time_is_a_horizontal_reference_line(self) -> None:
        """**予測落下時刻と実際の落下時刻を入れ替える変異が落ちる。**"""
        payload = by_label(self.timeline(), "reference_line", LABEL_ACTUAL_HIT_TIME)
        assert payload["value"] == 5793.0
        assert payload["axis"] == "y"
        assert LABEL_ACTUAL_HIT_TIME == "実際の落下時刻"

    def test_residual_series_comes_from_the_measured_time_errors(self) -> None:
        """残差は算出済みの `time_error_ms` を描くだけ。**引き算し直さない。**"""
        payload = by_label(self.timeline(), "polyline", LABEL_TIME_RESIDUAL)
        assert payload["points"] == ((5240.0, 67.0), (5286.0, 19.0), (5333.0, 6.0))
        assert LABEL_TIME_RESIDUAL == "落下時刻の残差"

    def test_residual_note_states_the_sign_convention(self) -> None:
        assert notes(self.timeline())[0] == (
            "残差は 予測落下時刻 − 実際の落下時刻（ms）であり、"
            "正なら予測が遅い側である。"
        )

    def test_missing_actual_hit_time_omits_the_reference_line(self) -> None:
        calls = self.timeline(
            truth=truth(impact_time=False), accuracy=accuracy(time_error=False)
        )
        assert not has_label(calls, "reference_line", LABEL_ACTUAL_HIT_TIME)
        assert by_label(calls, "polyline", LABEL_TIME_RESIDUAL)["points"] == ()
        assert notes(calls)[1] == (
            "実際の落下時刻が欠測のため、基準線と残差を描いていない（要件 4.6）。"
            "0 ms として描かない。"
        )

    def test_missing_note_is_absent_when_the_actual_hit_time_is_present(self) -> None:
        assert not any("欠測のため" in text for text in notes(self.timeline()))

    def test_predicted_hit_time_series_skips_invalid_predictions(self) -> None:
        payload = by_label(
            self.timeline(record=record(predictions=(invalid_prediction(),))),
            "polyline",
            LABEL_PREDICTED_HIT_TIME,
        )
        assert payload["points"] == ()


# ===========================================================================
# 6. 軌道図（要件 8.5）
# ===========================================================================


class TestTrajectory:
    def trajectory_calls(self, **overrides: object) -> tuple[Call, ...]:
        backend = RecordingBackend()
        draw_trajectory(
            backend,
            record=overrides.get("record", record()),
            trajectory_points_world_mm=overrides.get(
                "trajectory_points_world_mm", TRAJECTORY_POINTS_MM
            ),
        )
        return tuple(backend.calls)

    def test_axes_are_the_world_vertical_plane(self) -> None:
        opened = self.trajectory_calls()[0]
        assert opened.payload["kind"] == "trajectory"
        assert opened.payload["x_label"] == AXIS_WORLD_X_MM
        assert opened.payload["y_label"] == AXIS_WORLD_Z_MM
        assert AXIS_WORLD_Z_MM == "World Z 座標（mm、床面 z = 0）"

    def test_observed_points_are_drawn_as_world_x_and_z(self) -> None:
        """**x と z の取り違え・y の混入が落ちる**（3成分すべて別値）。"""
        payload = by_label(self.trajectory_calls(), "points", LABEL_OBSERVED_SAMPLES)
        assert payload["points"] == (
            (-1850.0, 1420.0),
            (-1240.0, 1610.0),
            (-610.0, 1505.0),
            (55.0, 1080.0),
        )
        assert payload["annotations"] == ()
        assert LABEL_OBSERVED_SAMPLES == "観測点列"

    def test_estimated_trajectory_is_the_supplied_polyline(self) -> None:
        """**観測点列と推定軌道を入れ替える変異が落ちる。**"""
        payload = by_label(
            self.trajectory_calls(), "polyline", LABEL_ESTIMATED_TRAJECTORY
        )
        assert payload["points"] == (
            (-2000.0, 1300.0),
            (-1000.0, 1580.0),
            (0.0, 1120.0),
            (1042.0, 0.0),
        )
        assert LABEL_ESTIMATED_TRAJECTORY == "推定軌道"

    def test_note_states_the_trajectory_is_not_computed_here(self) -> None:
        assert notes(self.trajectory_calls())[0] == (
            "推定軌道は算出済みの点列をそのまま描いたものである。"
            "本モジュールは軌道を推定しない（要件 8.10）。"
        )

    def test_empty_trajectory_still_draws_the_observations(self) -> None:
        calls = self.trajectory_calls(trajectory_points_world_mm=())
        assert by_label(calls, "polyline", LABEL_ESTIMATED_TRAJECTORY)["points"] == ()
        assert len(by_label(calls, "points", LABEL_OBSERVED_SAMPLES)["points"]) == 4
        assert notes(calls)[1] == (
            "推定軌道の点列が与えられていないため、観測点列だけを描いている。"
        )

    def test_single_observation_is_still_drawn(self) -> None:
        """観測点が1点しか無くても描く（**0 件・1 件の境界**）。"""
        one = (Sample(t_ms=5240.0, x_mm=-1850.0, y_mm=40.0, z_mm=1420.0),)
        payload = by_label(
            self.trajectory_calls(record=record(samples=one)),
            "points",
            LABEL_OBSERVED_SAMPLES,
        )
        assert payload["points"] == ((-1850.0, 1420.0),)

    def test_no_observations_draws_an_empty_series(self) -> None:
        calls = self.trajectory_calls(record=record(samples=()))
        assert by_label(calls, "points", LABEL_OBSERVED_SAMPLES)["points"] == ()

    def test_empty_trajectory_note_is_absent_when_points_are_supplied(self) -> None:
        assert not any(
            "与えられていない" in text for text in notes(self.trajectory_calls())
        )


# ===========================================================================
# 7. 収束図（要件 8.6）
# ===========================================================================


class TestConvergence:
    def convergence_calls(self, **overrides: object) -> tuple[Call, ...]:
        backend = RecordingBackend()
        draw_convergence(
            backend,
            accuracy=overrides.get("accuracy", accuracy()),
            convergence=overrides.get("convergence", convergence()),
        )
        return tuple(backend.calls)

    def test_axes_are_sample_count_and_hit_error(self) -> None:
        opened = self.convergence_calls()[0]
        assert opened.payload["kind"] == "convergence"
        assert opened.payload["x_label"] == AXIS_SAMPLE_COUNT
        assert opened.payload["y_label"] == AXIS_HIT_ERROR_MM
        assert AXIS_SAMPLE_COUNT == "サンプル数（件）"
        assert AXIS_HIT_ERROR_MM == "落下地点の誤差（mm）"

    def test_error_series_uses_the_stored_norm_not_a_recomputed_one(self) -> None:
        """**本モジュールで計算する変異が落ちる**（要件 8.10）。

        フィクスチャの `hit_error_norm_mm`（201 / 93 / 31）は
        `hit_error_mm` のノルム（149.3… / 60.4… / 17.0…）と**一致しない**。
        """
        payload = by_label(
            self.convergence_calls(), "polyline", LABEL_CONVERGENCE_ERROR
        )
        assert payload["points"] == ((3, 201.0), (5, 93.0), (8, 31.0))
        assert LABEL_CONVERGENCE_ERROR == "サンプル数ごとの誤差"

    def test_band_is_a_horizontal_reference_line(self) -> None:
        payload = by_label(
            self.convergence_calls(), "reference_line", LABEL_CONVERGENCE_BAND
        )
        assert payload["value"] == 42.0
        assert payload["axis"] == "y"
        assert LABEL_CONVERGENCE_BAND == "収束の帯域"

    def test_converged_at_is_a_vertical_reference_line(self) -> None:
        """**帯域と収束サンプル数を入れ替える変異が落ちる**（軸も値も別）。"""
        payload = by_label(
            self.convergence_calls(), "reference_line", LABEL_CONVERGED_AT
        )
        assert payload["value"] == 5
        assert payload["axis"] == "x"
        assert LABEL_CONVERGED_AT == "収束サンプル数"

    def test_rationale_is_carried_from_the_judgement(self) -> None:
        """判定の根拠は**運ぶ**。図がこの文面を持たない。"""
        assert notes(self.convergence_calls())[0] == CONVERGENCE_RATIONALE

    def test_not_converged_omits_the_vertical_line_and_says_so(self) -> None:
        calls = self.convergence_calls(convergence=convergence(converged_at=None))
        assert not has_label(calls, "reference_line", LABEL_CONVERGED_AT)
        assert notes(calls)[1] == (
            "収束サンプル数が得られていないため、収束位置の基準線を描いていない。"
            "未収束は正常な結果である。"
        )

    def test_converged_case_has_no_not_converged_note(self) -> None:
        assert not any(
            "得られていないため" in text for text in notes(self.convergence_calls())
        )

    def test_no_errors_draws_an_empty_series(self) -> None:
        calls = self.convergence_calls(accuracy=accuracy(count=0))
        assert by_label(calls, "polyline", LABEL_CONVERGENCE_ERROR)["points"] == ()


# ===========================================================================
# 8. 帰属図（要件 8.7）
# ===========================================================================


class TestAttributionPlot:
    def attribution_calls(self, **overrides: object) -> tuple[Call, ...]:
        backend = RecordingBackend()
        draw_attribution(
            backend,
            aggregate=overrides.get("aggregate", aggregate()),
            attribution=overrides.get("attribution", attribution()),
            camera_ray_horizontal=overrides.get(
                "camera_ray_horizontal", CAMERA_RAY_HORIZONTAL
            ),
        )
        return tuple(backend.calls)

    def test_axes_are_the_error_vector_components(self) -> None:
        opened = self.attribution_calls()[0]
        assert opened.payload["kind"] == "attribution"
        assert opened.payload["x_label"] == AXIS_ERROR_X_MM
        assert opened.payload["y_label"] == AXIS_ERROR_Y_MM
        assert AXIS_ERROR_X_MM == "誤差ベクトルの X 成分（mm、予測 − 実測）"
        assert AXIS_ERROR_Y_MM == "誤差ベクトルの Y 成分（mm、予測 − 実測）"

    def test_error_vectors_are_scattered_with_their_record_ids(self) -> None:
        payload = by_label(self.attribution_calls(), "points", LABEL_ERROR_VECTORS)
        assert payload["points"] == ((21.0, -34.0), (48.0, -12.0), (-9.0, 63.0))
        assert payload["annotations"] == (
            "throw-a-0001",
            "throw-b-0002",
            "throw-c-0003",
        )
        assert LABEL_ERROR_VECTORS == "投擲ごとの誤差ベクトル"

    def test_throws_without_an_error_vector_are_not_annotated(self) -> None:
        """**注記と点の対応が崩れる変異が落ちる**（件数も並びも合わなくなる）。"""
        payload = by_label(self.attribution_calls(), "points", LABEL_ERROR_VECTORS)
        assert NO_VECTOR_RECORD_ID not in payload["annotations"]

    def test_bias_is_an_arrow_from_the_origin(self) -> None:
        payload = by_label(self.attribution_calls(), "arrow", LABEL_BIAS)
        assert payload["origin"] == (0.0, 0.0)
        assert payload["vector"] == (23.0, -41.0)
        assert LABEL_BIAS == "共通の偏り成分"

    def test_camera_ray_is_a_separate_arrow_from_the_bias(self) -> None:
        """**カメラ視線方向と共通の偏りを入れ替える変異が落ちる。**"""
        payload = by_label(self.attribution_calls(), "arrow", LABEL_CAMERA_RAY)
        assert payload["origin"] == (0.0, 0.0)
        assert payload["vector"] == (0.6, 0.8)
        assert LABEL_CAMERA_RAY == "カメラ視線方向"

    def test_attribution_note_names_both_components(self) -> None:
        """**偏りとばらつきの帰属先を入れ替える変異が落ちる**（別の値である）。"""
        assert notes(self.attribution_calls())[0] == (
            "共通の偏り成分の帰属先: calibration ／ "
            "ばらつき成分の帰属先: observation_noise"
        )

    def test_missing_camera_ray_omits_the_arrow_and_says_so(self) -> None:
        calls = self.attribution_calls(camera_ray_horizontal=None)
        assert not has_label(calls, "arrow", LABEL_CAMERA_RAY)
        assert has_label(calls, "arrow", LABEL_BIAS)
        assert notes(calls)[1] == (
            "カメラ視線方向が与えられていないため、視線方向を重ねていない。"
            "向きの帰属は判別不能になりうる（要件 6.10）。"
        )

    def test_camera_ray_note_is_absent_when_the_ray_is_supplied(self) -> None:
        assert not any(
            "与えられていないため" in text for text in notes(self.attribution_calls())
        )

    def test_no_error_vectors_draws_an_empty_scatter(self) -> None:
        payload = by_label(
            self.attribution_calls(aggregate=aggregate(with_vectors=False)),
            "points",
            LABEL_ERROR_VECTORS,
        )
        assert payload["points"] == ()
        assert payload["annotations"] == ()


# ===========================================================================
# 9. 全体（`render_figures` が5つを揃えて描く）
# ===========================================================================


class TestRenderFigures:
    def calls(self, **overrides: object) -> tuple[Call, ...]:
        backend = RecordingBackend()
        render_figures(
            output_root=overrides.pop("output_root"),
            backend_factory=lambda: backend,
            **render_kwargs(**overrides),
        )
        return tuple(backend.calls)

    def test_every_figure_draws_its_own_content(self, tmp_output_dir: Path) -> None:
        """**5種類のうち1つを描かない変異が落ちる**（中身まで見る）。"""
        calls = self.calls(output_root=tmp_output_dir)
        assert labels(figure(calls, "top_down")) == (
            LABEL_PREDICTED_HITS,
            LABEL_ACTUAL_HIT,
            LABEL_STANDBY,
            LABEL_TOLERANCE_WINDOW,
        )
        assert labels(figure(calls, "timeline")) == (
            LABEL_PREDICTED_HIT_TIME,
            LABEL_ACTUAL_HIT_TIME,
            LABEL_TIME_RESIDUAL,
        )
        assert labels(figure(calls, "trajectory")) == (
            LABEL_OBSERVED_SAMPLES,
            LABEL_ESTIMATED_TRAJECTORY,
        )
        assert labels(figure(calls, "convergence")) == (
            LABEL_CONVERGENCE_ERROR,
            LABEL_CONVERGENCE_BAND,
            LABEL_CONVERGED_AT,
        )
        assert labels(figure(calls, "attribution")) == (
            LABEL_ERROR_VECTORS,
            LABEL_BIAS,
            LABEL_CAMERA_RAY,
        )

    def test_the_provisional_window_note_reaches_the_top_down_figure(
        self, tmp_output_dir: Path
    ) -> None:
        """**暫定である旨を図から落とす変異が落ちる**（全体経路でも固定する）。"""
        drawn = notes(figure(self.calls(output_root=tmp_output_dir), "top_down"))
        assert drawn[0] == (
            "暫定許容窓の半径 95 mm は暫定目標値であって合否条件ではない"
            "（要件 8.3）。この円の外に出た予測を失敗と判定してはならない。"
            f"{OQ05_OBJECT_SCOPE_NOTE}"
        )

    def test_sample_count_annotations_reach_the_top_down_figure(
        self, tmp_output_dir: Path
    ) -> None:
        """**サンプル数の判別を落とす／全部同じにする変異が落ちる。**"""
        payload = by_label(
            figure(self.calls(output_root=tmp_output_dir), "top_down"),
            "points",
            LABEL_PREDICTED_HITS,
        )
        assert payload["annotations"] == ("3 サンプル", "5 サンプル", "8 サンプル")

    def test_each_figure_is_saved_once_after_its_own_content(
        self, tmp_output_dir: Path
    ) -> None:
        for kind in ("top_down", "timeline", "trajectory", "convergence", "attribution"):
            section = figure(self.calls(output_root=tmp_output_dir), kind)
            assert section[0].op == "open_figure"
            assert section[-1].op == "save"
            assert len(ops(section, "save")) == 1

    def test_render_accepts_a_string_output_root(self, tmp_output_dir: Path) -> None:
        backend = RecordingBackend()
        result = render_figures(
            output_root=str(tmp_output_dir),
            backend_factory=lambda: backend,
            **render_kwargs(),
        )
        assert result.paths[0] == tmp_output_dir / "plots" / f"{RECORD_ID}-top_down.png"


# ===========================================================================
# 10. 値の不変性
# ===========================================================================


class TestValueSemantics:
    def test_results_are_frozen(self) -> None:
        availability = PlotAvailability(
            available=True, library="matplotlib", backend="Agg", reason=None
        )
        result = PlotResult(
            available=True,
            reason=None,
            kinds=(),
            paths=(),
            missing_glyph_count=0,
            font_warning=None,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            availability.available = False  # type: ignore[misc]
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.available = False  # type: ignore[misc]

    def test_results_are_value_equal(self) -> None:
        left = PlotResult(
            available=False,
            reason=UNAVAILABLE_REASON,
            kinds=(),
            paths=(),
            missing_glyph_count=0,
            font_warning=None,
        )
        right = PlotResult(
            available=False,
            reason=UNAVAILABLE_REASON,
            kinds=(),
            paths=(),
            missing_glyph_count=0,
            font_warning=None,
        )
        assert left == right
# ===========================================================================
# 11. 依存の欠落**以外**の失敗は伝播する（要件 8.9 の範囲を狭く保つ）
# ===========================================================================


class TestFailurePropagation:
    """**片側だけ値へ倒す。** 何でも値へ倒すと「図が無いこと」に気付けない。

    要件 8.9 が名指しするのは「可視化に必要な依存が導入されていない場合」
    だけである。書き込み権限・ディスク・出力先の失敗まで `PlotResult` へ
    畳むと、**利用不可の報告と「依存はあるが書けなかった」が同じ見え方に
    なる**——前者は任意依存を入れれば直り、後者は入れても直らない。
    """

    def test_availability_propagates_a_failure_that_is_not_a_missing_dependency(
        self,
    ) -> None:
        with pytest.raises(OSError):
            visualization_availability(backend_factory=BrokenFactory())

    def test_availability_still_turns_a_missing_dependency_into_a_value(self) -> None:
        """**対の検査**: 依存の欠落側は例外にしない。"""
        result = visualization_availability(backend_factory=ExplodingFactory())
        assert result.available is False
        assert result.reason == UNAVAILABLE_REASON

    def test_render_propagates_a_failure_that_is_not_a_missing_dependency(
        self, tmp_output_dir: Path
    ) -> None:
        with pytest.raises(OSError):
            render_figures(
                output_root=tmp_output_dir,
                backend_factory=BrokenFactory(),
                **render_kwargs(),
            )

    def test_render_propagates_a_failing_save(self, tmp_output_dir: Path) -> None:
        """**書き出しの失敗を握らない。** 依存はあるのに図が無い状態である。"""
        with pytest.raises(OSError):
            render_figures(
                output_root=tmp_output_dir,
                backend_factory=FailingSaveBackend,
                **render_kwargs(),
            )

    def test_render_still_turns_a_missing_dependency_into_a_value(
        self, tmp_output_dir: Path
    ) -> None:
        """**対の検査**: 依存の欠落側は例外にしない。"""
        result = render_figures(
            output_root=tmp_output_dir,
            backend_factory=ExplodingFactory(),
            **render_kwargs(),
        )
        assert result.available is False


# ===========================================================================
# 12. 字形の欠落は握り潰さずに報告する（要件 8.3）
# ===========================================================================


class TestFontReporting:
    """**読めない字形は「明示」ではない。**

    本 Spec の見出し・凡例・注記はすべて日本語である。描画に使うフォントが
    CJK の字形を持たなければ、要件 8.3 が求める「許容窓の値が暫定目標値で
    ある旨を図に明示する」は画像の上で成立しない。警告だけ消して図を返すと、
    その事実がどこにも残らない。
    """

    def test_missing_glyphs_are_reported_on_the_result(
        self, tmp_output_dir: Path
    ) -> None:
        result = render_figures(
            output_root=tmp_output_dir,
            backend_factory=lambda: RecordingBackend(missing_glyphs=7),
            **render_kwargs(),
        )
        assert result.available is True
        assert result.missing_glyph_count == 7
        assert result.font_warning == (
            "描画に用いたフォントに字形が無く、7 種類の文字が図の上で表示できていない。"
            "本 Spec の見出し・凡例・注記は日本語であり、"
            "とりわけ暫定許容窓が暫定目標値である旨の注記（要件 8.3）が読めない。"
            "読めない字形は明示ではないので、この図を根拠として引用しないこと。"
            "CJK 対応フォントを導入して描き直すこと。"
        )

    def test_no_missing_glyph_leaves_no_font_warning(
        self, tmp_output_dir: Path
    ) -> None:
        """**対の検査**: 0 件のときに警告を出さない（常に出す実装が落ちる）。"""
        result = render_figures(
            output_root=tmp_output_dir,
            backend_factory=lambda: RecordingBackend(missing_glyphs=0),
            **render_kwargs(),
        )
        assert result.missing_glyph_count == 0
        assert result.font_warning is None

    def test_the_count_reaches_the_warning_text(self, tmp_output_dir: Path) -> None:
        """件数が文面へ入る（定数を焼き付ける実装が落ちる）。"""
        result = render_figures(
            output_root=tmp_output_dir,
            backend_factory=lambda: RecordingBackend(missing_glyphs=41),
            **render_kwargs(),
        )
        assert result.font_warning is not None
        assert result.font_warning.startswith(
            "描画に用いたフォントに字形が無く、41 種類の文字が図の上で表示できていない。"
        )

    def test_the_real_backend_agrees_with_itself(self, tmp_output_dir: Path) -> None:
        """本物の経路でも件数と警告が必ず対応する。"""
        result = render_figures(output_root=tmp_output_dir, **render_kwargs())
        assert (result.font_warning is None) == (result.missing_glyph_count == 0)
        if result.font_warning is not None:
            assert result.font_warning == MISSING_GLYPH_WARNING_TEMPLATE.format(
                count=result.missing_glyph_count
            )


# ===========================================================================
# 13. 本物のバックエンド——**描画呼び出しが実際に図になる経路**
#
# 偽のバックエンドが固定するのは「描画関数が何を呼んだか」だけである。
# `_MatplotlibBackend` の7操作を全部 `pass` にしても、`save` がファイルさえ
# 作れば「5種類の図が生成された」は**白紙の画像5枚**で満たされてしまう。
# ここで matplotlib のオブジェクト状態を直接照合して、その経路を塞ぐ。
# ===========================================================================

#: 本節の局所リテラル。**4つとも別値**なので、見出しと軸名の取り違えが露見する。
BACKEND_KIND = "＜局所リテラル: 図の種類＞"
BACKEND_TITLE = "＜局所リテラル: 図の見出し＞"
BACKEND_X_LABEL = "＜局所リテラル: 横軸の名前＞"
BACKEND_Y_LABEL = "＜局所リテラル: 縦軸の名前＞"
BACKEND_LABEL = "＜局所リテラル: 系列の名前＞"

#: 散布する点。**x と y をすべて別値**にしてあるので、軸の入れ替えが露見する。
BACKEND_POINTS = ((11.0, 22.0), (33.0, 44.0))
BACKEND_ANNOTATIONS = ("＜局所リテラル: 注記A＞", "＜局所リテラル: 注記B＞")
BACKEND_LINE = ((5.0, 60.0), (7.0, 80.0), (9.0, 100.0))
BACKEND_VERTICAL_VALUE = 13.0
BACKEND_HORIZONTAL_VALUE = 17.0
BACKEND_CENTER = (25.0, -35.0)
#: 半径。**直径（19.0）とも半分（4.75）とも別値**である。
BACKEND_RADIUS = 9.5
BACKEND_ORIGIN = (2.0, 3.0)
BACKEND_VECTOR = (12.0, -20.0)
BACKEND_NOTE_A = "＜局所リテラル: 図へ描く注記1件目＞"
BACKEND_NOTE_B = "＜局所リテラル: 図へ描く注記2件目＞"

#: 字形が確実に揃っている見出し（matplotlib に同梱される DejaVu Sans の範囲）。
ASCII_TITLE = "ascii title"
ASCII_X_LABEL = "ascii x"
ASCII_Y_LABEL = "ascii y"
ASCII_NOTE = "ascii note"

#: 字形が確実に**無い**見出しと注記。互いに1文字も共有しないので、
#: 欠ける字形の下限が `len(set(...))` として素直に決まる（**16 文字**）。
CJK_TITLE = "日本語の見出し"
CJK_NOTE = "暫定目標値である旨"


def xy_pairs(rows: object) -> list[tuple[float, float]]:
    """matplotlib が返す座標列を素の tuple へ落とす（numpy を import しない）。"""
    return [(float(row[0]), float(row[1])) for row in rows]  # type: ignore[union-attr]


def numbers(values: object) -> list[float]:
    return [float(value) for value in values]  # type: ignore[union-attr]


class TestMatplotlibBackend:
    @pytest.fixture
    def pyplot(self) -> object:
        module = pytest.importorskip("matplotlib.pyplot")
        yield module
        module.close("all")

    def opened(self, pyplot: object) -> tuple[object, object, object]:
        backend = matplotlib_backend()
        backend.open_figure(
            kind=BACKEND_KIND,
            title=BACKEND_TITLE,
            x_label=BACKEND_X_LABEL,
            y_label=BACKEND_Y_LABEL,
        )
        figure = pyplot.gcf()
        return backend, figure, figure.axes[0]

    # --- open_figure --------------------------------------------------------

    def test_open_figure_puts_the_title_and_axis_names_on_the_axes(
        self, pyplot: object
    ) -> None:
        """**見出しを描かない変異・軸名を入れ替える変異が落ちる。**"""
        _backend, _figure, axes = self.opened(pyplot)
        assert axes.get_title() == "＜局所リテラル: 図の見出し＞"
        assert axes.get_xlabel() == "＜局所リテラル: 横軸の名前＞"
        assert axes.get_ylabel() == "＜局所リテラル: 縦軸の名前＞"

    def test_open_figure_records_the_kind_on_the_axes(self, pyplot: object) -> None:
        assert self.opened(pyplot)[2].get_label() == "＜局所リテラル: 図の種類＞"

    # --- points -------------------------------------------------------------

    def test_points_are_scattered_in_xy_order(self, pyplot: object) -> None:
        """**散布の x と y を入れ替える変異が落ちる。**"""
        backend, _figure, axes = self.opened(pyplot)
        backend.points(
            label=BACKEND_LABEL,
            points=BACKEND_POINTS,
            annotations=BACKEND_ANNOTATIONS,
        )
        assert xy_pairs(axes.collections[0].get_offsets()) == [
            (11.0, 22.0),
            (33.0, 44.0),
        ]
        assert axes.collections[0].get_label() == "＜局所リテラル: 系列の名前＞"

    def test_point_annotations_are_anchored_to_their_own_points(
        self, pyplot: object
    ) -> None:
        """**要件 8.2 の判別が本物の図に載る。**

        文字列だけを見ると、**注記を全部先頭の点へ寄せる変異**が素通りする
        ——そのとき図は「先頭の点が同時に 3・5・8 サンプルである」と読め、
        他の点が何サンプル目に基づくかは図から失われる。**どの点に付いたか
        まで**照合する。`BACKEND_POINTS` は2点とも相異なるので、寄せる変異も
        並びを入れ替える変異も落ちる。

        ⚠️ `Annotation.get_position()` は `xytext`（オフセット）を返す。
        アンカーは `.xy` である。
        """
        backend, _figure, axes = self.opened(pyplot)
        backend.points(
            label=BACKEND_LABEL,
            points=BACKEND_POINTS,
            annotations=BACKEND_ANNOTATIONS,
        )
        assert [(text.get_text(), tuple(text.xy)) for text in axes.texts] == [
            ("＜局所リテラル: 注記A＞", (11.0, 22.0)),
            ("＜局所リテラル: 注記B＞", (33.0, 44.0)),
        ]

    def test_points_without_annotations_write_nothing(self, pyplot: object) -> None:
        """**対の検査**: 注記が無ければ文字を置かない。"""
        backend, _figure, axes = self.opened(pyplot)
        backend.points(label=BACKEND_LABEL, points=BACKEND_POINTS, annotations=())
        assert [text.get_text() for text in axes.texts] == []

    # --- polyline -----------------------------------------------------------

    def test_polyline_puts_the_points_on_a_line(self, pyplot: object) -> None:
        """**線を一切描かない変異が落ちる。**"""
        backend, _figure, axes = self.opened(pyplot)
        backend.polyline(label=BACKEND_LABEL, points=BACKEND_LINE)
        assert len(axes.lines) == 1
        assert numbers(axes.lines[0].get_xdata()) == [5.0, 7.0, 9.0]
        assert numbers(axes.lines[0].get_ydata()) == [60.0, 80.0, 100.0]
        assert axes.lines[0].get_label() == "＜局所リテラル: 系列の名前＞"

    # --- reference_line（**両方の軸で固定する**）-----------------------------

    def test_reference_line_on_the_x_axis_is_vertical(self, pyplot: object) -> None:
        backend, _figure, axes = self.opened(pyplot)
        backend.reference_line(
            label=BACKEND_LABEL, axis="x", value=BACKEND_VERTICAL_VALUE
        )
        assert numbers(axes.lines[0].get_xdata()) == [13.0, 13.0]
        assert numbers(axes.lines[0].get_ydata()) == [0.0, 1.0]

    def test_reference_line_on_the_y_axis_is_horizontal(self, pyplot: object) -> None:
        """**縦横を取り違える変異が落ちる**（上と対で置いてある）。"""
        backend, _figure, axes = self.opened(pyplot)
        backend.reference_line(
            label=BACKEND_LABEL, axis="y", value=BACKEND_HORIZONTAL_VALUE
        )
        assert numbers(axes.lines[0].get_ydata()) == [17.0, 17.0]
        assert numbers(axes.lines[0].get_xdata()) == [0.0, 1.0]

    # --- circle -------------------------------------------------------------

    def test_circle_uses_the_radius_not_the_diameter(self, pyplot: object) -> None:
        """**許容窓が2倍の大きさで描かれる変異が落ちる**（要件 8.3）。"""
        backend, _figure, axes = self.opened(pyplot)
        backend.circle(
            label=BACKEND_LABEL, center=BACKEND_CENTER, radius=BACKEND_RADIUS
        )
        assert len(axes.patches) == 1
        assert float(axes.patches[0].get_radius()) == 9.5
        assert float(axes.patches[0].get_radius()) != 19.0
        assert xy_pairs([axes.patches[0].get_center()]) == [(25.0, -35.0)]
        assert axes.patches[0].get_label() == "＜局所リテラル: 系列の名前＞"

    # --- arrow --------------------------------------------------------------

    def test_arrow_runs_from_the_origin_to_origin_plus_vector(
        self, pyplot: object
    ) -> None:
        backend, _figure, axes = self.opened(pyplot)
        backend.arrow(
            label=BACKEND_LABEL, origin=BACKEND_ORIGIN, vector=BACKEND_VECTOR
        )
        assert xy_pairs(axes.lines[0].get_xydata()) == [(2.0, 3.0), (14.0, -17.0)]
        assert axes.lines[0].get_label() == "＜局所リテラル: 系列の名前＞"
        # 矢じり（`annotate`）の**アンカーも先端**であること。線だけを見ると
        # 矢じりが別の場所を指す変異が素通りし、向きが図から読めなくなる。
        assert [(text.get_text(), tuple(text.xy)) for text in axes.texts] == [
            ("", (14.0, -17.0))
        ]

    # --- save（注記と凡例）--------------------------------------------------

    def test_notes_become_part_of_the_saved_figure(
        self, pyplot: object, tmp_output_dir: Path
    ) -> None:
        """**注記を捨てる変異・保存時に書かない変異が落ちる**（要件 8.3）。"""
        backend, figure, _axes = self.opened(pyplot)
        backend.polyline(label=BACKEND_LABEL, points=BACKEND_LINE)
        backend.note(text=BACKEND_NOTE_A)
        backend.note(text=BACKEND_NOTE_B)
        backend.save(tmp_output_dir / "notes.png")
        assert [text.get_text() for text in figure.texts] == [
            "＜局所リテラル: 図へ描く注記1件目＞",
            "＜局所リテラル: 図へ描く注記2件目＞",
        ]

    def test_a_figure_without_notes_carries_no_text(
        self, pyplot: object, tmp_output_dir: Path
    ) -> None:
        """**対の検査**: 注記が無ければ文字を足さない。"""
        backend, figure, _axes = self.opened(pyplot)
        backend.polyline(label=BACKEND_LABEL, points=BACKEND_LINE)
        backend.save(tmp_output_dir / "no-notes.png")
        assert [text.get_text() for text in figure.texts] == []

    def test_the_provisional_window_note_becomes_part_of_the_real_image(
        self, pyplot: object, tmp_output_dir: Path
    ) -> None:
        """**要件 8.3 の一文が、偽ではなく本物の図に載っていること。**

        これが落ちるとき、`PlotResult` も `RecordingBackend` も正しいのに
        **画像だけが暫定である旨を失っている**。
        """
        backend = matplotlib_backend()
        draw_top_down(
            backend, record=record(), truth=truth(), layout=layout(), oq05=oq05()
        )
        figure = pyplot.gcf()
        backend.save(tmp_output_dir / "top_down.png")
        expected = (
            "暫定許容窓の半径 95 mm は暫定目標値であって合否条件ではない"
            "（要件 8.3）。この円の外に出た予測を失敗と判定してはならない。"
            f"{OQ05_OBJECT_SCOPE_NOTE}"
        )
        assert [text.get_text() for text in figure.texts] == [expected]

    def test_the_real_image_carries_the_tolerance_window_and_its_sample_labels(
        self, pyplot: object, tmp_output_dir: Path
    ) -> None:
        """上面図の中身が本物の図に載る（許容窓・サンプル数の判別・実測点）。

        **注記は「どの予測落下地点に付いたか」まで固定する**（要件 8.2）。
        フィクスチャの予測落下地点は3点とも別値なので、寄せる変異・並びを
        入れ替える変異の両方が落ちる。
        """
        backend = matplotlib_backend()
        draw_top_down(
            backend, record=record(), truth=truth(), layout=layout(), oq05=oq05()
        )
        axes = pyplot.gcf().axes[0]
        assert float(axes.patches[0].get_radius()) == 95.0
        assert xy_pairs([axes.patches[0].get_center()]) == [(1042.0, 118.0)]
        assert [(text.get_text(), tuple(text.xy)) for text in axes.texts] == [
            ("3 サンプル", (1180.0, 61.0)),
            ("5 サンプル", (1096.0, 145.0)),
            ("8 サンプル", (1057.0, 126.0)),
        ]
        backend.save(tmp_output_dir / "top_down.png")

    def test_a_legend_is_drawn_when_something_is_labelled(
        self, pyplot: object, tmp_output_dir: Path
    ) -> None:
        """design.md「Plotter」Risks が名指しした凡例が実際に付くこと。"""
        backend, _figure, axes = self.opened(pyplot)
        backend.polyline(label=BACKEND_LABEL, points=BACKEND_LINE)
        backend.save(tmp_output_dir / "legend.png")
        legend = axes.get_legend()
        assert legend is not None
        assert [entry.get_text() for entry in legend.get_texts()] == [
            "＜局所リテラル: 系列の名前＞"
        ]

    def test_no_legend_is_drawn_when_nothing_is_labelled(
        self, pyplot: object, tmp_output_dir: Path
    ) -> None:
        """**対の検査**: 描くものが無ければ空の凡例を付けない。"""
        backend, _figure, axes = self.opened(pyplot)
        backend.note(text=BACKEND_NOTE_A)
        backend.save(tmp_output_dir / "no-legend.png")
        assert axes.get_legend() is None

    def test_saving_writes_a_non_empty_file(
        self, pyplot: object, tmp_output_dir: Path
    ) -> None:
        backend, _figure, _axes = self.opened(pyplot)
        backend.polyline(label=BACKEND_LABEL, points=BACKEND_LINE)
        path = tmp_output_dir / "written.png"
        backend.save(path)
        assert path.is_file()
        assert path.stat().st_size > 0

    # --- 呼び出し方の誤り ---------------------------------------------------

    def test_drawing_before_opening_a_figure_is_rejected(
        self, pyplot: object
    ) -> None:
        """**どちらのガードが働いたかまで固定する。**

        `save()` は図と座標軸の両方を要求するので、片方のガードを外しても
        もう片方が `RuntimeError` を送出してしまう。**例外の型だけを見ると
        2つのガードが区別できず、片方を消す変異が素通りする**（本リポジトリ
        の「相互排他の説明文を取り違える変異」と同型）。文面まで見る。
        """
        backend = matplotlib_backend()
        with pytest.raises(RuntimeError, match="描画しようとしている"):
            backend.polyline(label=BACKEND_LABEL, points=BACKEND_LINE)

    def test_saving_before_opening_a_figure_is_rejected(
        self, pyplot: object, tmp_output_dir: Path
    ) -> None:
        """**保存側のガードが先に働く**（描画側のガードに肩代わりさせない）。"""
        backend = matplotlib_backend()
        with pytest.raises(RuntimeError, match="保存しようとしている"):
            backend.save(tmp_output_dir / "never.png")

    # --- 字形の欠落（**本物の経路で両側を通す**）-----------------------------

    def test_the_real_backend_counts_glyphs_the_font_cannot_draw(
        self, pyplot: object, tmp_output_dir: Path
    ) -> None:
        """**DejaVu Sans は matplotlib に同梱され、CJK を持たない**——決定的。"""
        matplotlib = pytest.importorskip("matplotlib")
        backend = matplotlib_backend()
        previous = matplotlib.rcParams["font.family"]
        try:
            matplotlib.rcParams["font.family"] = ["DejaVu Sans"]
            backend.open_figure(
                kind=BACKEND_KIND,
                title=CJK_TITLE,
                x_label=ASCII_X_LABEL,
                y_label=ASCII_Y_LABEL,
            )
            backend.note(text=CJK_NOTE)
            backend.save(tmp_output_dir / "cjk.png")
        finally:
            matplotlib.rcParams["font.family"] = previous
        # **件数の大きさまで見る。** 真偽（> 0）だけでは、539 文字が欠けて
        # いるのに「1 文字が表示できていない」と報告する実装（`+= 1` を
        # `= 1` にする変異）も、警告の重複除去で件数が黙って過少になる実装
        # （`simplefilter("always")` を落とす変異）も素通りする。読み手へ
        # 提示される数が嘘になると、「1 文字なら大丈夫だろう」と読まれる。
        # **件数の大きさまで厳密に見る。** 真偽（> 0）だけでは、16 種類が
        # 欠けているのに「1 種類が表示できていない」と報告する実装
        # （`+= 1` を `= 1` にする変異）が素通りする。読み手へ提示される数が
        # 嘘になると、「1 文字なら大丈夫だろう」と読まれる。
        # 見出しと注記は1文字も共有しないので、期待値は素直に決まる。
        distinct_missing = len(set(CJK_TITLE + CJK_NOTE))
        assert distinct_missing == 16
        assert backend.missing_glyph_count() == distinct_missing

    def test_the_real_backend_reports_zero_when_every_glyph_exists(
        self, pyplot: object, tmp_output_dir: Path
    ) -> None:
        """**対の検査**: 字形が揃っていれば 0 件（常に数える実装が落ちる）。"""
        matplotlib = pytest.importorskip("matplotlib")
        backend = matplotlib_backend()
        previous = matplotlib.rcParams["font.family"]
        try:
            matplotlib.rcParams["font.family"] = ["DejaVu Sans"]
            backend.open_figure(
                kind=BACKEND_KIND,
                title=ASCII_TITLE,
                x_label=ASCII_X_LABEL,
                y_label=ASCII_Y_LABEL,
            )
            backend.note(text=ASCII_NOTE)
            backend.save(tmp_output_dir / "ascii.png")
        finally:
            matplotlib.rcParams["font.family"] = previous
        assert backend.missing_glyph_count() == 0

    def test_saving_does_not_swallow_warnings_that_are_not_about_glyphs(
        self, pyplot: object, tmp_output_dir: Path
    ) -> None:
        """**抑止装置にしない。** 字形以外の警告はそのまま出し直される。

        見出しと軸名を ASCII だけにして**字形の欠落を 0 件に固定**してある
        ので、「字形の警告だけを数え、それ以外は数えずに出し直す」という
        振る舞いの両側がここで見える。
        """
        backend = matplotlib_backend()
        backend.open_figure(
            kind=BACKEND_KIND,
            title=ASCII_TITLE,
            x_label=ASCII_X_LABEL,
            y_label=ASCII_Y_LABEL,
        )
        figure = pyplot.gcf()
        original = figure.savefig

        def noisy(*args: object, **kwargs: object) -> object:
            warnings.warn(
                "＜局所リテラル: 字形とは無関係の警告＞", UserWarning, stacklevel=2
            )
            return original(*args, **kwargs)

        figure.savefig = noisy
        with pytest.warns(UserWarning, match="字形とは無関係の警告"):
            backend.save(tmp_output_dir / "warned.png")
        assert backend.missing_glyph_count() == 0
