"""可視化——落下地点・時系列・軌道・収束・帰属の5種類の図（開発PC 専用）。

design.md「Components and Interfaces / L8-L9: 出力 / Plotter」、tasks.md タスク
7.2、要件 8.1-8.10。

**本モジュールは何も算出しない。** 既に出た値（記録・真値・実測・収束・帰属・
OQ-05 の材料・レイアウト）を受け取って描くだけの層である（要件 8.10、
`tech.md` 開発標準3「表示レイヤにアルゴリズムを持たせない」）。図の中で数値を
作り直すと、図に出た値と算出側の値が食い違う経路が生まれ、**目で見て気付く**
という可視化の唯一の効能が失われる。推定軌道の点列を引数として受け取るのも
同じ理由である——放物運動モデルをここで解き直せば、それは予測器の再実装で
あって描画ではない。

==============================================================================
なぜ描画ライブラリの import を関数の中に置くのか
==============================================================================

`matplotlib` を import してよいのは**本モジュールだけ**である（要件 8.8）。
さらに、**モジュールのトップレベルでは import しない**。トップレベルへ置くと
`import m1_validation.plot` そのものが未導入環境で失敗し、`plot` を参照する
どの入口も道連れになる。要件 8.9 が求めているのは「可視化**のみ**が利用不可に
なる」ことであって、可視化が無いと集計も判断も動かない状態ではない。

したがって import は `matplotlib_backend()` の中だけで行い、その失敗
（`ImportError`）は**例外ではなく値**として `PlotAvailability` /
`PlotResult` に載せて返す。可視化は開発用であり、本番処理の必須要件では
ない（A-8）。

==============================================================================
なぜ描画を「バックエンド」越しに行うのか
==============================================================================

画像ファイルの中身は後から照合できない。「ファイルが生成された」ことしか
確かめられない検査は、**何をどこへ描いたかを一切固定しない**（タスク 7.1 の
教訓）。そこで描画の語彙を `PlotBackend` の7操作へ絞り、描画関数はその語彙
だけを呼ぶ。値の対応（どの系列にどの点を、どのラベルで、どの注記とともに
描いたか）を、記録用のバックエンドを差し込むことで値として取り出せる。

`matplotlib` に触れるのは `_MatplotlibBackend` ただ1つであり、5つの描画関数は
描画ライブラリを知らない。

==============================================================================
なぜ「暫定目標値である旨」を図の中へ描くのか
==============================================================================

位置精度の許容窓（`ThrowLayout.position_tolerance_mm` に由来し、`Oq05Result`
が実際に使った値を持つ）は**暫定目標値であって合否条件ではない**
（要件 8.3、design.md「ThrowLayout」Risks が「一人歩きしやすい」と名指し
している）。図だけが切り出されて資料に貼られると、窓の円は「これに入れば
成功」という基準に見える。凡例や周辺文書ではなく、**図の一部として**注記を
描くのはそのためである。留保の文面は `Oq05Result.object_scope_note` から
運ぶ——同じ断りを2箇所に書くと、片方だけを直したときに2つの文書が別のことを
言い始める（`report.py` が想定値を `BudgetUpdate` から運ぶのと同じ理由）。

==============================================================================
境界
==============================================================================

本モジュールは評価側（L8）であり、上流3パッケージ
（`sensing_foundation` / `flying_object_tracking` / `world_frame_calibration`）
を import しない（design.md「Allowed Dependencies」）。`numpy` も使わない
——本モジュールは数値計算をしないので、そもそも要らない。
"""

from __future__ import annotations

import warnings
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from m1_validation.attribution import AttributionResult
from m1_validation.judgement.oq05 import Oq05Result
from m1_validation.layout import ThrowLayout
from m1_validation.metrics.accuracy import AccuracyResult
from m1_validation.metrics.aggregate import ThrowAggregate
from m1_validation.metrics.convergence import ConvergenceResult
from m1_validation.types import ThrowTruth, TruthMethod
from prediction_core import InvalidPrediction, Prediction, ThrowRecord

__all__ = [
    "ATTRIBUTION_NOTE_TEMPLATE",
    "AXIS_ERROR_X_MM",
    "AXIS_ERROR_Y_MM",
    "AXIS_HIT_ERROR_MM",
    "AXIS_HIT_TIME_MS",
    "AXIS_OBSERVATION_TIME_MS",
    "AXIS_SAMPLE_COUNT",
    "AXIS_WORLD_X_MM",
    "AXIS_WORLD_Y_MM",
    "AXIS_WORLD_Z_MM",
    "AXIS_X",
    "AXIS_Y",
    "KIND_ATTRIBUTION",
    "KIND_CONVERGENCE",
    "KIND_TIMELINE",
    "KIND_TOP_DOWN",
    "KIND_TRAJECTORY",
    "LABEL_ACTUAL_HIT",
    "LABEL_ACTUAL_HIT_TIME",
    "LABEL_BIAS",
    "LABEL_CAMERA_RAY",
    "LABEL_CONVERGED_AT",
    "LABEL_CONVERGENCE_BAND",
    "LABEL_CONVERGENCE_ERROR",
    "LABEL_ERROR_VECTORS",
    "LABEL_ESTIMATED_TRAJECTORY",
    "LABEL_OBSERVED_SAMPLES",
    "LABEL_PREDICTED_HITS",
    "LABEL_PREDICTED_HIT_TIME",
    "LABEL_STANDBY",
    "LABEL_TIME_RESIDUAL",
    "LABEL_TOLERANCE_WINDOW",
    "MATPLOTLIB_BACKEND",
    "MISSING_CAMERA_RAY_NOTE",
    "MISSING_GLYPH_WARNING_TEMPLATE",
    "MISSING_IMPACT_NOTE",
    "MISSING_IMPACT_TIME_NOTE",
    "MISSING_TRAJECTORY_NOTE",
    "NOT_CONVERGED_NOTE",
    "PLOT_KINDS",
    "PLOT_LIBRARY",
    "SAMPLE_COUNT_ANNOTATION_TEMPLATE",
    "TIME_RESIDUAL_NOTE",
    "TITLE_ATTRIBUTION",
    "TITLE_CONVERGENCE",
    "TITLE_TIMELINE",
    "TITLE_TOP_DOWN",
    "TITLE_TRAJECTORY",
    "TRAJECTORY_SOURCE_NOTE",
    "UNAVAILABLE_REASON",
    "WINDOW_NOTE_TEMPLATE",
    "PlotAvailability",
    "PlotBackend",
    "PlotResult",
    "draw_attribution",
    "draw_convergence",
    "draw_timeline",
    "draw_top_down",
    "draw_trajectory",
    "matplotlib_backend",
    "plot_output_path",
    "render_figures",
    "visualization_availability",
]


# ---------------------------------------------------------------------------
# 描画ライブラリ
# ---------------------------------------------------------------------------

#: 唯一の描画ライブラリ。任意依存 `m1-viz` としてのみ宣言される（要件 13.3）。
PLOT_LIBRARY: str = "matplotlib"

#: **非対話バックエンド。** 画面を要求しない（design.md「Plotter」Integration）。
#: 開発PC が GUI 無しでも、CI でも同じ図が出る。
MATPLOTLIB_BACKEND: str = "Agg"

#: 描画ライブラリが無いときの理由（要件 8.9）。**例外ではなく値として返す。**
UNAVAILABLE_REASON: str = (
    "描画ライブラリ matplotlib が導入されていないため、"
    "可視化のみを利用不可とする（要件 8.9）。"
    "計測・集計・判断の経路は停止しない"
    "——可視化は開発用であり、本番処理の必須要件ではない（A-8）。"
    "図が必要なら任意依存 m1-viz を導入して再実行すること。"
)


# ---------------------------------------------------------------------------
# 図の種類（**5種類。design.md「Plotter」Contracts の表と同じ**）
# ---------------------------------------------------------------------------

KIND_TOP_DOWN: str = "top_down"
KIND_TIMELINE: str = "timeline"
KIND_TRAJECTORY: str = "trajectory"
KIND_CONVERGENCE: str = "convergence"
KIND_ATTRIBUTION: str = "attribution"

#: 出力する図の種類と**その順序**。出力ファイル名にも入る。
PLOT_KINDS: tuple[str, ...] = (
    KIND_TOP_DOWN,
    KIND_TIMELINE,
    KIND_TRAJECTORY,
    KIND_CONVERGENCE,
    KIND_ATTRIBUTION,
)


# ---------------------------------------------------------------------------
# 見出し・軸・凡例（**互いに別物である**。取り違えると読み手が誤読する）
# ---------------------------------------------------------------------------

TITLE_TOP_DOWN: str = (
    "床平面の上面図: 予測落下地点系列・実際の落下地点・待機位置・暫定許容窓"
)
TITLE_TIMELINE: str = "落下時刻の推移: 予測落下時刻・実際の落下時刻・残差"
TITLE_TRAJECTORY: str = "World 座標系の軌道図: 観測点列と推定軌道"
TITLE_CONVERGENCE: str = "収束図: サンプル数と落下地点の誤差"
TITLE_ATTRIBUTION: str = "帰属図: 誤差ベクトルの散布・共通の偏り・カメラ視線方向"

#: 軸名には**必ず単位を入れる**（design.md「Plotter」Risks）。
AXIS_WORLD_X_MM: str = "World X 座標（mm）"
AXIS_WORLD_Y_MM: str = "World Y 座標（mm）"
AXIS_WORLD_Z_MM: str = "World Z 座標（mm、床面 z = 0）"
AXIS_OBSERVATION_TIME_MS: str = "観測時刻（ms）"
AXIS_HIT_TIME_MS: str = "落下時刻・残差（ms）"
AXIS_SAMPLE_COUNT: str = "サンプル数（件）"
AXIS_HIT_ERROR_MM: str = "落下地点の誤差（mm）"
AXIS_ERROR_X_MM: str = "誤差ベクトルの X 成分（mm、予測 − 実測）"
AXIS_ERROR_Y_MM: str = "誤差ベクトルの Y 成分（mm、予測 − 実測）"

#: 基準線を引く軸。`AXIS_X` は縦線（x = 値）、`AXIS_Y` は横線（y = 値）。
AXIS_X: str = "x"
AXIS_Y: str = "y"

LABEL_PREDICTED_HITS: str = "予測落下地点系列"
LABEL_ACTUAL_HIT: str = "実際の落下地点"
LABEL_STANDBY: str = "待機位置"
LABEL_TOLERANCE_WINDOW: str = "暫定許容窓"
LABEL_PREDICTED_HIT_TIME: str = "予測落下時刻"
LABEL_ACTUAL_HIT_TIME: str = "実際の落下時刻"
LABEL_TIME_RESIDUAL: str = "落下時刻の残差"
LABEL_OBSERVED_SAMPLES: str = "観測点列"
LABEL_ESTIMATED_TRAJECTORY: str = "推定軌道"
LABEL_CONVERGENCE_ERROR: str = "サンプル数ごとの誤差"
LABEL_CONVERGENCE_BAND: str = "収束の帯域"
LABEL_CONVERGED_AT: str = "収束サンプル数"
LABEL_ERROR_VECTORS: str = "投擲ごとの誤差ベクトル"
LABEL_BIAS: str = "共通の偏り成分"
LABEL_CAMERA_RAY: str = "カメラ視線方向"

#: 予測落下地点に添える注記（要件 8.2）。**どの予測が何サンプル目に基づくかを
#: 図の上で判別できるようにする。** 色や形だけでは、白黒印刷でも図の縮小でも
#: 判別が消える。
SAMPLE_COUNT_ANNOTATION_TEMPLATE: str = "{count} サンプル"


# ---------------------------------------------------------------------------
# 注記（**図の一部として描く**。凡例や周辺文書に逃がさない）
# ---------------------------------------------------------------------------

#: 許容窓の注記（要件 8.3）。`{scope}` には `Oq05Result.object_scope_note` を
#: **運ぶ**（本モジュールは同じ断りを持ち直さない）。
WINDOW_NOTE_TEMPLATE: str = (
    "暫定許容窓の半径 {window:g} mm は暫定目標値であって合否条件ではない"
    "（要件 8.3）。この円の外に出た予測を失敗と判定してはならない。{scope}"
)

MISSING_IMPACT_NOTE: str = (
    "実際の落下地点が欠測のため、実測点と暫定許容窓を描いていない"
    "（要件 4.6）。0 mm の点として描かない。"
)

TIME_RESIDUAL_NOTE: str = (
    "残差は 予測落下時刻 − 実際の落下時刻（ms）であり、正なら予測が遅い側である。"
)

MISSING_IMPACT_TIME_NOTE: str = (
    "実際の落下時刻が欠測のため、基準線と残差を描いていない（要件 4.6）。"
    "0 ms として描かない。"
)

TRAJECTORY_SOURCE_NOTE: str = (
    "推定軌道は算出済みの点列をそのまま描いたものである。"
    "本モジュールは軌道を推定しない（要件 8.10）。"
)

MISSING_TRAJECTORY_NOTE: str = (
    "推定軌道の点列が与えられていないため、観測点列だけを描いている。"
)

NOT_CONVERGED_NOTE: str = (
    "収束サンプル数が得られていないため、収束位置の基準線を描いていない。"
    "未収束は正常な結果である。"
)

#: 帰属の内訳（要件 6.9）。**合計の単一値へ畳まない。**
ATTRIBUTION_NOTE_TEMPLATE: str = (
    "共通の偏り成分の帰属先: {bias} ／ ばらつき成分の帰属先: {scatter}"
)

MISSING_CAMERA_RAY_NOTE: str = (
    "カメラ視線方向が与えられていないため、視線方向を重ねていない。"
    "向きの帰属は判別不能になりうる（要件 6.10）。"
)


# ---------------------------------------------------------------------------
# 字形の欠落（**握り潰さない**）
# ---------------------------------------------------------------------------

#: matplotlib が「この文字はフォントに無い」と報せてくるときの目印。
#: 1文字につき1件出るので、そのまま流すと1回の書き出しで数百件になり、
#: 本当に見るべき警告が埋もれる。
_MISSING_GLYPH_MARKER: str = "missing from font"

#: 字形が欠けたまま書き出したときに `PlotResult` へ載せる警告。
#:
#: **抑止するなら記録して報告する。** 本 Spec の見出し・凡例・注記はすべて
#: 日本語であり、matplotlib の既定フォント（DejaVu Sans）は CJK の字形を
#: 持たない。字形が無ければ画像の上では豆腐になり、**要件 8.3 が求める
#: 「暫定目標値である旨を図に明示する」は成立しない**——読めない字形は明示
#: ではない。警告だけ消して図を返すと、その事実が誰にも届かない。
MISSING_GLYPH_WARNING_TEMPLATE: str = (
    "描画に用いたフォントに字形が無く、{count} 種類の文字が図の上で表示できていない。"
    "本 Spec の見出し・凡例・注記は日本語であり、"
    "とりわけ暫定許容窓が暫定目標値である旨の注記（要件 8.3）が読めない。"
    "読めない字形は明示ではないので、この図を根拠として引用しないこと。"
    "CJK 対応フォントを導入して描き直すこと。"
)


# ---------------------------------------------------------------------------
# 描画の語彙
# ---------------------------------------------------------------------------


class PlotBackend(Protocol):
    """描画の語彙。**この7操作しか使わない。**

    描画関数を描画ライブラリから切り離すための境界である。語彙を絞ってある
    ので、記録用のバックエンドを差し込めば「何をどこへ描いたか」を値として
    取り出せる（モジュール docstring 参照）。
    """

    def open_figure(
        self, *, kind: str, title: str, x_label: str, y_label: str
    ) -> None:
        """新しい図を開く。以降の描画はこの図に対して行われる。"""

    def points(
        self,
        *,
        label: str,
        points: Sequence[tuple[float, float]],
        annotations: Sequence[str],
    ) -> None:
        """点の集合を描く。`annotations` は空か、`points` と同じ長さである。"""

    def polyline(self, *, label: str, points: Sequence[tuple[float, float]]) -> None:
        """点を順につないだ折れ線を描く。"""

    def reference_line(self, *, label: str, axis: str, value: float) -> None:
        """基準線を引く。`axis` が `AXIS_X` なら縦線、`AXIS_Y` なら横線。"""

    def circle(self, *, label: str, center: tuple[float, float], radius: float) -> None:
        """中心と**半径**で円を描く（直径ではない）。"""

    def arrow(
        self, *, label: str, origin: tuple[float, float], vector: tuple[float, float]
    ) -> None:
        """`origin` から `vector` の向きと大きさで矢印を描く。"""

    def note(self, *, text: str) -> None:
        """図の一部として注記を描く。凡例でも軸名でもない。"""

    def save(self, path: Path) -> None:
        """開いている図を画像ファイルとして書き出し、閉じる。"""

    def missing_glyph_count(self) -> int:
        """これまでの書き出しで、フォントに字形が無く描けなかった**文字の種類数**。

        **0 は「調べていない」ではなく「1種類も欠けていない」である。**
        描画バックエンドしか知り得ない事実なので、ここで問い合わせる。

        **出現回数ではなく種類数である。** 同じ文字が見出しにも注記にも出れば
        警告は何度も出るし、書き出しは複数回の描画を伴う（`bbox_inches` の
        採寸で描き直す）。回数を報告すると「539 文字が表示できていない」と
        いった、読み手が意味を取れない数になる。
        """


# ---------------------------------------------------------------------------
# 結果の形
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlotAvailability:
    """可視化が使えるか（要件 8.9）。

    Attributes:
        available: 描画ライブラリが導入されているか。
        library: 描画ライブラリの名前。
        backend: 使用する非対話バックエンドの名前。
        reason: 使えない理由。使えるなら `None`。

    **`available` が偽でも失敗ではない。** 可視化は開発用であり、実機側の
    実行経路には入らない（要件 8.8、A-8）。
    """

    available: bool
    library: str
    backend: str
    reason: str | None


@dataclass(frozen=True, slots=True)
class PlotResult:
    """図の出力結果（要件 8.9）。

    Attributes:
        available: 図を出せたか。偽なら `kinds` も `paths` も空である。
        reason: 出せなかった理由。出せたなら `None`。
        kinds: 出力した図の種類（`PLOT_KINDS` の順）。
        paths: 出力した画像ファイル（`kinds` と同順・同数）。
        missing_glyph_count: 書き出しの際にフォントへ字形が無く、図の上で
            表示できなかった**文字の種類数**（出現回数ではない）。
            **0 は「1種類も欠けていない」である。**
        font_warning: 字形が欠けていたときの警告。欠けていなければ `None`
            ——`missing_glyph_count == 0` と必ず対応する。

    **利用不可を例外にしない。** 呼び出し側（CLI）は `available` を見て終了
    コードを分ければよく、集計・判断の経路は止まらない。
    """

    available: bool
    reason: str | None
    kinds: tuple[str, ...]
    paths: tuple[Path, ...]
    missing_glyph_count: int
    font_warning: str | None


# ---------------------------------------------------------------------------
# 利用可否と出力先
# ---------------------------------------------------------------------------


def visualization_availability(
    *, backend_factory: Callable[[], PlotBackend] | None = None
) -> PlotAvailability:
    """可視化が使えるかを**値として**返す（要件 8.9）。

    Args:
        backend_factory: バックエンドの生成器。既定は `matplotlib` を遅延
            import する生成器である。導入されていなければ `ImportError` を
            送出し、本関数はそれを利用不可として返す。

    Returns:
        `PlotAvailability`。

    Raises:
        Exception: **依存の欠落（`ImportError`）以外の失敗はそのまま伝播する。**
            要件 8.9 が値へ倒すことを求めているのは「可視化に必要な依存が
            導入されていない場合」だけである。何でも利用不可へ畳むと、
            「任意依存を入れれば直る」と「入れても直らない」が同じ見え方に
            なる。
    """
    factory = matplotlib_backend if backend_factory is None else backend_factory
    try:
        factory()
    except ImportError:
        return PlotAvailability(
            available=False,
            library=PLOT_LIBRARY,
            backend=MATPLOTLIB_BACKEND,
            reason=UNAVAILABLE_REASON,
        )
    return PlotAvailability(
        available=True,
        library=PLOT_LIBRARY,
        backend=MATPLOTLIB_BACKEND,
        reason=None,
    )


def plot_output_path(output_root: Path | str, stem: str, kind: str) -> Path:
    """図の書き出し先（design.md「Plotter」Output: `var/m1/plots/<stem>-<kind>.png`）。

    図だけをまとめて `plots/` へ置くのは、レポート JSON（`report-<session>.json`）
    と混ざらないようにするためである。
    """
    return Path(output_root) / "plots" / f"{stem}-{kind}.png"


# ---------------------------------------------------------------------------
# 上面図（要件 8.1, 8.2, 8.3）
# ---------------------------------------------------------------------------


def draw_top_down(
    backend: PlotBackend,
    *,
    record: ThrowRecord,
    truth: ThrowTruth,
    layout: ThrowLayout,
    oq05: Oq05Result,
) -> None:
    """床平面の上面図を描く（要件 8.1, 8.2, 8.3）。

    Args:
        backend: 描画先。
        record: 投擲記録。**予測落下地点は `predictions` の有効な予測から
            そのまま読む。** 無効な予測は落下地点のフィールドを持たないので
            系列に載らない（0 で埋めない）。
        truth: 同じ投擲の真値。実際の落下地点が欠測なら実測点も許容窓も
            描かず、欠測である旨を注記する。
        layout: 投擲レイアウト。**待機位置**をここから読む。
        oq05: OQ-05 の判断材料。**許容窓の値と留保の文面をここから運ぶ**
            ——窓をレイアウトから計算し直すと、実際に材料の算出に使われた
            値と図の円が食い違いうる（要件 8.10）。
    """
    backend.open_figure(
        kind=KIND_TOP_DOWN,
        title=TITLE_TOP_DOWN,
        x_label=AXIS_WORLD_X_MM,
        y_label=AXIS_WORLD_Y_MM,
    )
    predictions = _valid_predictions(record)
    backend.points(
        label=LABEL_PREDICTED_HITS,
        points=tuple(
            (item.predicted_hit_x_mm, item.predicted_hit_y_mm) for item in predictions
        ),
        annotations=tuple(
            SAMPLE_COUNT_ANNOTATION_TEMPLATE.format(count=item.sample_count)
            for item in predictions
        ),
    )
    impact_xy_mm = _impact_xy_mm(truth)
    backend.points(
        label=LABEL_ACTUAL_HIT,
        points=() if impact_xy_mm is None else (impact_xy_mm,),
        annotations=(),
    )
    backend.points(
        label=LABEL_STANDBY,
        points=(layout.standby_position_world_mm,),
        annotations=(),
    )
    if impact_xy_mm is not None:
        backend.circle(
            label=LABEL_TOLERANCE_WINDOW,
            center=impact_xy_mm,
            radius=oq05.window_mm,
        )
    backend.note(
        text=WINDOW_NOTE_TEMPLATE.format(
            window=oq05.window_mm, scope=oq05.object_scope_note
        )
    )
    if impact_xy_mm is None:
        backend.note(text=MISSING_IMPACT_NOTE)


# ---------------------------------------------------------------------------
# 時系列図（要件 8.4）
# ---------------------------------------------------------------------------


def draw_timeline(
    backend: PlotBackend,
    *,
    record: ThrowRecord,
    truth: ThrowTruth,
    accuracy: AccuracyResult,
) -> None:
    """予測落下時刻の推移・実際の落下時刻・残差の推移を描く（要件 8.4）。

    Args:
        backend: 描画先。
        record: 投擲記録。予測落下時刻は `Prediction.predicted_hit_time_ms`
            をそのまま読む。
        truth: 同じ投擲の真値。実際の落下時刻は基準線になる。
        accuracy: 実測項目 4 / 5 の算出結果。**残差は
            `PredictionError.time_error_ms` を読むだけ**であり、ここで
            引き算し直さない（要件 8.10）。
    """
    backend.open_figure(
        kind=KIND_TIMELINE,
        title=TITLE_TIMELINE,
        x_label=AXIS_OBSERVATION_TIME_MS,
        y_label=AXIS_HIT_TIME_MS,
    )
    backend.polyline(
        label=LABEL_PREDICTED_HIT_TIME,
        points=tuple(
            (item.based_on_time_ms, item.predicted_hit_time_ms)
            for item in _valid_predictions(record)
        ),
    )
    impact_time_ms = _impact_time_ms(truth)
    if impact_time_ms is not None:
        backend.reference_line(
            label=LABEL_ACTUAL_HIT_TIME, axis=AXIS_Y, value=impact_time_ms
        )
    backend.polyline(
        label=LABEL_TIME_RESIDUAL,
        points=tuple(
            (error.based_on_time_ms, error.time_error_ms)
            for error in accuracy.errors
            if error.time_error_ms is not None
        ),
    )
    backend.note(text=TIME_RESIDUAL_NOTE)
    if impact_time_ms is None:
        backend.note(text=MISSING_IMPACT_TIME_NOTE)


# ---------------------------------------------------------------------------
# 軌道図（要件 8.5）
# ---------------------------------------------------------------------------


def draw_trajectory(
    backend: PlotBackend,
    *,
    record: ThrowRecord,
    trajectory_points_world_mm: Sequence[tuple[float, float, float]],
) -> None:
    """観測点列と推定軌道を World 座標系の軌道図として描く（要件 8.5）。

    上面図（X-Y）と重複しないよう、**鉛直面（X-Z）** に落とす。落下は高さの
    変化なので、床面 z = 0 との交わりが図の上で読めることに意味がある。

    Args:
        backend: 描画先。
        record: 投擲記録。観測点列は `samples` をそのまま読む。
        trajectory_points_world_mm: **算出済みの**推定軌道の点列（World mm の
            3成分）。空でもよい。**本モジュールは軌道を推定しない**
            （要件 8.10）——放物運動モデルをここで解き直せば予測器の再実装に
            なる。
    """
    backend.open_figure(
        kind=KIND_TRAJECTORY,
        title=TITLE_TRAJECTORY,
        x_label=AXIS_WORLD_X_MM,
        y_label=AXIS_WORLD_Z_MM,
    )
    backend.points(
        label=LABEL_OBSERVED_SAMPLES,
        points=tuple((sample.x_mm, sample.z_mm) for sample in record.samples),
        annotations=(),
    )
    trajectory = tuple(trajectory_points_world_mm)
    backend.polyline(
        label=LABEL_ESTIMATED_TRAJECTORY,
        points=tuple((point[0], point[2]) for point in trajectory),
    )
    backend.note(text=TRAJECTORY_SOURCE_NOTE)
    if not trajectory:
        backend.note(text=MISSING_TRAJECTORY_NOTE)


# ---------------------------------------------------------------------------
# 収束図（要件 8.6）
# ---------------------------------------------------------------------------


def draw_convergence(
    backend: PlotBackend,
    *,
    accuracy: AccuracyResult,
    convergence: ConvergenceResult,
) -> None:
    """サンプル数と誤差の関係を収束図として描く（要件 8.6）。

    Args:
        backend: 描画先。
        accuracy: 実測項目 4 / 5 の算出結果。**誤差の大きさは
            `PredictionError.hit_error_norm_mm` を読むだけ**であり、成分から
            組み直さない（要件 8.10）。
        convergence: 収束の判定結果。帯域・収束サンプル数・根拠を運ぶ。
            収束サンプル数が `None`（未収束・測定不能）なら基準線を描かず、
            その旨を注記する——**未収束は正常な結果である。**
    """
    backend.open_figure(
        kind=KIND_CONVERGENCE,
        title=TITLE_CONVERGENCE,
        x_label=AXIS_SAMPLE_COUNT,
        y_label=AXIS_HIT_ERROR_MM,
    )
    backend.polyline(
        label=LABEL_CONVERGENCE_ERROR,
        points=tuple(
            (error.sample_count, error.hit_error_norm_mm) for error in accuracy.errors
        ),
    )
    backend.reference_line(
        label=LABEL_CONVERGENCE_BAND, axis=AXIS_Y, value=convergence.band_mm
    )
    if convergence.converged_at is not None:
        backend.reference_line(
            label=LABEL_CONVERGED_AT, axis=AXIS_X, value=convergence.converged_at
        )
    backend.note(text=convergence.judgement.rationale)
    if convergence.converged_at is None:
        backend.note(text=NOT_CONVERGED_NOTE)


# ---------------------------------------------------------------------------
# 帰属図（要件 8.7）
# ---------------------------------------------------------------------------


def draw_attribution(
    backend: PlotBackend,
    *,
    aggregate: ThrowAggregate,
    attribution: AttributionResult,
    camera_ray_horizontal: tuple[float, float] | None,
) -> None:
    """誤差ベクトルの散布・共通の偏り・カメラ視線方向を重ねて描く（要件 8.7）。

    共通の偏りが World 座標系に固定された向きなのかカメラ視線方向に沿うのかを
    **目で確かめられる**ようにするための図である（要件 6.3）。数値の角度差
    （`BiasComponent.*_agreement_deg`）だけでは、縮退している場合の見分けが
    つかない。

    Args:
        backend: 描画先。
        aggregate: 投擲群の集計。誤差ベクトルと、その出どころの投擲識別子を
            読む。**誤差ベクトルを持たない投擲は点にも注記にも現れない。**
        attribution: 帰属の判定結果。共通の偏りベクトルと、成分ごとの帰属先を
            運ぶ。
        camera_ray_horizontal: World 座標系で表したカメラ視線方向の水平成分。
            **算出済みの値を受け取る**（要件 8.10）。無ければ `None` を渡す
            ——重ねずに、その旨を注記する。
    """
    backend.open_figure(
        kind=KIND_ATTRIBUTION,
        title=TITLE_ATTRIBUTION,
        x_label=AXIS_ERROR_X_MM,
        y_label=AXIS_ERROR_Y_MM,
    )
    backend.points(
        label=LABEL_ERROR_VECTORS,
        points=tuple(aggregate.error_vectors),
        annotations=tuple(
            row.record_id
            for row in aggregate.per_throw
            if row.error_vector_mm is not None
        ),
    )
    backend.arrow(
        label=LABEL_BIAS, origin=(0.0, 0.0), vector=attribution.bias.vector_mm
    )
    if camera_ray_horizontal is not None:
        backend.arrow(
            label=LABEL_CAMERA_RAY, origin=(0.0, 0.0), vector=camera_ray_horizontal
        )
    backend.note(
        text=ATTRIBUTION_NOTE_TEMPLATE.format(
            bias=attribution.bias.attribution,
            scatter=attribution.scatter.attribution,
        )
    )
    if camera_ray_horizontal is None:
        backend.note(text=MISSING_CAMERA_RAY_NOTE)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def render_figures(
    *,
    output_root: Path | str,
    record: ThrowRecord,
    truth: ThrowTruth,
    accuracy: AccuracyResult,
    convergence: ConvergenceResult,
    layout: ThrowLayout,
    oq05: Oq05Result,
    aggregate: ThrowAggregate,
    attribution: AttributionResult,
    trajectory_points_world_mm: Sequence[tuple[float, float, float]] = (),
    camera_ray_horizontal: tuple[float, float] | None = None,
    backend_factory: Callable[[], PlotBackend] | None = None,
) -> PlotResult:
    """5種類の図を画像ファイルとして書き出す（要件 8.1-8.10）。

    **描画ライブラリが無ければ利用不可を返すだけで、例外を投げない**
    （要件 8.9）。呼び出し側の集計・判断は止まらない。**握るのは依存の欠落
    だけであり、書き出しの失敗（`OSError` など）はそのまま伝播する**
    ——値へ倒すと「図が無いこと」に誰も気付けない。

    Args:
        output_root: 出力先の根（図は `plots/` の下へ入る）。
        record: 投擲記録。上面図・時系列図・軌道図の材料。
        truth: 同じ投擲の真値。
        accuracy: 実測項目 4 / 5 の算出結果。
        convergence: 収束の判定結果。
        layout: 投擲レイアウト（待機位置）。
        oq05: OQ-05 の判断材料（暫定許容窓と留保）。
        aggregate: 投擲群の集計（誤差ベクトル）。
        attribution: 帰属の判定結果。
        trajectory_points_world_mm: 算出済みの推定軌道の点列。
        camera_ray_horizontal: 算出済みのカメラ視線方向（水平成分）。
        backend_factory: バックエンドの生成器。既定は `matplotlib`。

    Returns:
        `PlotResult`。利用不可なら `kinds` も `paths` も空である。
        字形が欠けたまま書き出した場合は `font_warning` が立つ
        ——**読めない字形は「図に明示した」ことにならない**（要件 8.3）。

    **投擲単位の図（上面図・時系列図・軌道図・収束図）は `record_id`、
    投擲群の図（帰属図）は `calibration_id` をファイル名の幹にする。**
    帰属は群に対する判断であり、1投擲の名前を付けると別の投擲の図と取り違え
    られる。
    """
    factory = matplotlib_backend if backend_factory is None else backend_factory
    try:
        backend = factory()
    except ImportError:
        # **ここで握るのは `ImportError` だけである。** 要件 8.9 が名指しする
        # のは「可視化に必要な依存が導入されていない場合」であり、書き込み権限
        # やディスクの失敗まで値へ倒すと、**図が無いことに誰も気付けない**。
        return PlotResult(
            available=False,
            reason=UNAVAILABLE_REASON,
            kinds=(),
            paths=(),
            missing_glyph_count=0,
            font_warning=None,
        )

    stems = {
        KIND_TOP_DOWN: record.record_id,
        KIND_TIMELINE: record.record_id,
        KIND_TRAJECTORY: record.record_id,
        KIND_CONVERGENCE: record.record_id,
        KIND_ATTRIBUTION: aggregate.calibration_id,
    }
    paths: list[Path] = []
    for kind in PLOT_KINDS:
        if kind == KIND_TOP_DOWN:
            draw_top_down(
                backend, record=record, truth=truth, layout=layout, oq05=oq05
            )
        elif kind == KIND_TIMELINE:
            draw_timeline(backend, record=record, truth=truth, accuracy=accuracy)
        elif kind == KIND_TRAJECTORY:
            draw_trajectory(
                backend,
                record=record,
                trajectory_points_world_mm=trajectory_points_world_mm,
            )
        elif kind == KIND_CONVERGENCE:
            draw_convergence(backend, accuracy=accuracy, convergence=convergence)
        else:
            draw_attribution(
                backend,
                aggregate=aggregate,
                attribution=attribution,
                camera_ray_horizontal=camera_ray_horizontal,
            )
        path = plot_output_path(output_root, stems[kind], kind)
        path.parent.mkdir(parents=True, exist_ok=True)
        backend.save(path)
        paths.append(path)
    missing_glyphs = backend.missing_glyph_count()
    return PlotResult(
        available=True,
        reason=None,
        kinds=PLOT_KINDS,
        paths=tuple(paths),
        missing_glyph_count=missing_glyphs,
        font_warning=(
            None
            if missing_glyphs == 0
            else MISSING_GLYPH_WARNING_TEMPLATE.format(count=missing_glyphs)
        ),
    )


# ---------------------------------------------------------------------------
# 値の取り出し（**選ぶだけ。算出しない**）
# ---------------------------------------------------------------------------


def _valid_predictions(record: ThrowRecord) -> tuple[Prediction, ...]:
    """有効な予測だけを生成順のまま取り出す。

    `InvalidPrediction` は落下地点・落下時刻のフィールドを**意図的に持たない**
    （`prediction_core` 要件 6.7）ので、そもそも描けない。0 で埋めて系列へ
    載せると、誤った目標座標が図の上で「予測」として読まれる。
    """
    return tuple(
        outcome
        for outcome in record.predictions
        if not isinstance(outcome, InvalidPrediction)
    )


def _impact_xy_mm(truth: ThrowTruth) -> tuple[float, float] | None:
    """実際の落下地点から**水平2成分**を取り出す。欠測なら `None`。

    高さ成分を混ぜないのは、上面図が床平面の図であり、要件 5.4 が扱うのも
    水平距離だからである（`metrics/accuracy.py` と同じ扱い）。
    """
    value = truth.impact_point_world_mm
    if value.method is TruthMethod.MISSING:
        return None
    point = value.value
    if not isinstance(point, tuple):
        return None
    return (point[0], point[1])


def _impact_time_ms(truth: ThrowTruth) -> float | None:
    """実際の落下時刻を取り出す。欠測なら `None`（**0 で埋めない**）。"""
    value = truth.impact_time_ms
    if value.method is TruthMethod.MISSING:
        return None
    if not isinstance(value.value, float | int) or isinstance(value.value, bool):
        return None
    return float(value.value)


# ---------------------------------------------------------------------------
# matplotlib バックエンド（**描画ライブラリに触れる唯一の場所**）
# ---------------------------------------------------------------------------


def matplotlib_backend() -> PlotBackend:
    """`matplotlib` を**遅延 import** して非対話バックエンドを組み立てる。

    トップレベルで import しないのはモジュール docstring のとおりである。
    導入されていなければ `ImportError` がそのまま伝播し、呼び出し側
    （`visualization_availability()` / `render_figures()`）が**値**へ倒す。

    Raises:
        ImportError: `matplotlib` が導入されていない場合。
    """
    import matplotlib

    # 画面を要求しないバックエンドを明示的に選ぶ（design.md「Plotter」
    # Integration）。GUI の無い開発機・CI でも同じ図が出る。
    matplotlib.use(MATPLOTLIB_BACKEND)
    from matplotlib import pyplot
    from matplotlib.patches import Circle

    return _MatplotlibBackend(pyplot, Circle)


class _MatplotlibBackend:
    """`PlotBackend` の唯一の実体。**描画以外の判断を持たない。**"""

    def __init__(self, pyplot: Any, circle: Any) -> None:
        self._pyplot = pyplot
        self._circle = circle
        self._figure: Any | None = None
        self._axes: Any | None = None
        self._notes: list[str] = []
        self._labelled = 0
        # **種類で数える**ので集合で持つ。警告文には欠けた文字とフォント名が
        # 入っているので、同じ文字の再出現は自然に畳まれる。
        self._missing_glyphs: set[str] = set()

    def open_figure(
        self, *, kind: str, title: str, x_label: str, y_label: str
    ) -> None:
        figure, axes = self._pyplot.subplots(figsize=(8.0, 6.0))
        axes.set_title(title)
        axes.set_label(kind)
        axes.set_xlabel(x_label)
        axes.set_ylabel(y_label)
        axes.grid(visible=True, linewidth=0.3)
        self._figure = figure
        self._axes = axes
        self._notes = []
        self._labelled = 0

    def points(
        self,
        *,
        label: str,
        points: Sequence[tuple[float, float]],
        annotations: Sequence[str],
    ) -> None:
        axes = self._require_axes()
        axes.scatter(
            [point[0] for point in points],
            [point[1] for point in points],
            label=label,
            s=28,
        )
        self._labelled += 1
        for point, text in zip(points, annotations, strict=False):
            axes.annotate(text, point, fontsize=7, xytext=(4, 4), textcoords="offset points")

    def polyline(self, *, label: str, points: Sequence[tuple[float, float]]) -> None:
        axes = self._require_axes()
        axes.plot(
            [point[0] for point in points],
            [point[1] for point in points],
            label=label,
            marker="o",
            markersize=3,
            linewidth=1.0,
        )
        self._labelled += 1

    def reference_line(self, *, label: str, axis: str, value: float) -> None:
        axes = self._require_axes()
        if axis == AXIS_X:
            axes.axvline(value, label=label, linestyle="--", linewidth=1.0)
        else:
            axes.axhline(value, label=label, linestyle="--", linewidth=1.0)
        self._labelled += 1

    def circle(self, *, label: str, center: tuple[float, float], radius: float) -> None:
        axes = self._require_axes()
        axes.add_patch(
            self._circle(center, radius, fill=False, label=label, linestyle=":")
        )
        self._labelled += 1

    def arrow(
        self, *, label: str, origin: tuple[float, float], vector: tuple[float, float]
    ) -> None:
        axes = self._require_axes()
        tip = (origin[0] + vector[0], origin[1] + vector[1])
        axes.annotate("", xy=tip, xytext=origin, arrowprops={"arrowstyle": "->"})
        axes.plot(
            [origin[0], tip[0]], [origin[1], tip[1]], label=label, linewidth=1.5
        )
        self._labelled += 1

    def note(self, *, text: str) -> None:
        self._notes.append(text)

    def save(self, path: Path) -> None:
        figure = self._require_figure()
        axes = self._require_axes()
        if self._labelled:
            axes.legend(loc="best", fontsize="x-small")
        for index, text in enumerate(self._notes):
            figure.text(
                0.01,
                0.02 + 0.03 * (len(self._notes) - 1 - index),
                text,
                fontsize=6,
                wrap=True,
            )
        with warnings.catch_warnings(record=True) as caught:
            # **既定のフィルタへ委ねない。** 既定は「一度出した警告文は以降
            # 抑止する」ので、プロセス内で既に出ている警告がここで記録されず、
            # **出し直しの対象からも漏れて黙って消える**。件数のほうは種類で
            # 数えているため既定フィルタでも同じ数になる（＝この行を落とす
            # 変異は件数からは見えない）が、落として良い行ではない。
            warnings.simplefilter("always")
            figure.savefig(path, dpi=120, bbox_inches="tight")
        # 字形が無い旨の警告は**1文字につき1件**出るので、そのまま流すと1回の
        # 書き出しで数百件が積み上がり、本当に見るべき警告が埋もれる。だからと
        # いって黙って捨てると、**「この図の日本語は表示できていない」という
        # 既知の欠陥が誰にも届かない**（要件 8.3）。したがって数えて残し、
        # `PlotResult.font_warning` として報告する。それ以外の警告は**握らず
        # そのまま出し直す**——ここは警告の抑止装置ではない。
        for entry in caught:
            if _MISSING_GLYPH_MARKER in str(entry.message):
                self._missing_glyphs.add(str(entry.message))
                continue
            warnings.warn_explicit(
                entry.message, entry.category, entry.filename, entry.lineno
            )
        self._pyplot.close(figure)
        self._figure = None
        self._axes = None
        self._notes = []
        self._labelled = 0

    def missing_glyph_count(self) -> int:
        """書き出した図すべてを通算した、字形が欠けた文字数。

        **図をまたいで積み上げる**（`save()` で空へ戻さない）。1回の
        `render_figures()` が出す5枚は同じフォントで描かれるので、
        「どれか1枚でも読めない」ことが分かればよい。
        """
        return len(self._missing_glyphs)

    def _require_axes(self) -> Any:
        if self._axes is None:
            raise RuntimeError("open_figure() を呼ぶ前に描画しようとしている")
        return self._axes

    def _require_figure(self) -> Any:
        if self._figure is None:
            raise RuntimeError("open_figure() を呼ぶ前に保存しようとしている")
        return self._figure
