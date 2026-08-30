"""誤差の帰属（★ 本 Spec の核）。

design.md「Components and Interfaces / L6: 帰属 / ErrorAttributor」、
tasks.md タスク 5.1 / 5.2、要件 6.1-6.11, 2.4。

本モジュールは2つの部品からなる。

1. **観測品質に基づく予測のばらつきの見積もり**（タスク 5.1、要件 6.8）。
   観測サンプルを再抽出して予測をやり直す（`bootstrap_prediction_spread`）。
2. **帰属の判定規則**（タスク 5.2、要件 6.1-6.7 / 6.9 / 6.10 / 6.11）。
   投擲群の誤差ベクトルを共通の偏り成分とばらつき成分へ分解し、
   **向きで**帰属先を判別する（`attribute`）。

**判定規則は実測前に固定し、結果と同じ場所に説明文として残す**（要件 6.1）。
規則が結果から離れた場所にあると、あとから規則のほうを結果に合わせて
読み替えられてしまう。`attribution_criterion()` が組み立てた文面が
`AttributionResult.judgement.criterion` に入る。

**帰属は「大きさ」ではなく「向き」で切り分ける**（`research.md` Decision 4）。
座標系のずれは World 座標系に固定されたオフセットとして全投擲へ同じ向きで
現れ、Depth が対象物のカメラ側表面を測ることによる寄りはカメラ視線方向の軸に
沿う。どちらも「共通の偏り」として現れるので、**大きさだけでは区別できない**。

**判別不能は正常な結果である**（要件 6.10）。とくに投擲位置が1箇所だと
World 固定方向とカメラ視線方向が縮退し、どちらに由来するかは原理的に
決められない。無理に一つの原因へ割り当てると、そのあと間違った側を
直しにいくことになる。

**新しい推定器を実装しない。** 再抽出したサンプル列を `prediction_core.predict`
へ渡し直すだけである。ここで自前の軌道当てはめを書くと、見積もったばらつきは
**本番の予測器の性質ではなく自前実装の性質**になる。そのばらつきは要件 6.6 /
6.7 で「ばらつき成分が観測ノイズ由来か、モデル由来か」を分ける**基準**として
使われるので、基準が別物になった時点で帰属そのものが無意味になる
（`research.md` の「集計器・推定器を二重に持たない」と同じ理由）。

**乱数はインスタンスで持ち、グローバルな乱数状態を触らない。**
`random.seed()` / `random.random()` をモジュール大域の乱数器で呼ぶと、同じ
プロセスで走る他の処理の乱数列が本関数の呼び出し回数に依存し、実行順や並列
実行で結果が動く。同一入力・同一種に対して同一結果を返すこと（要件 12.4 /
3.7）は、`random.Random(seed)` を関数内に閉じることで構造的に満たす。

⚠️ **再抽出回数の既定値（`AttributionConfig.bootstrap_iterations`）は暫定の
評価候補であって必須性能ではない**（要件 13.7）。`config.PROVISIONAL_NOTICE`
が `--print-settings` の読み手へ必ずその旨を出す。回数を増減して見積もりが
どれだけ動くかは実測してから決める事項であり、**合否条件として扱わない。**

本モジュールは L6 層の評価側であり、`sensing_foundation` /
`flying_object_tracking` / `world_frame_calibration` を import しない
（design.md「Allowed Dependencies」）。数値計算は標準ライブラリだけで書く。
"""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from m1_validation.config import AttributionConfig, M1Settings
from m1_validation.errors import FailureReason, M1ConfigError, M1ValidationError
from m1_validation.metrics.aggregate import ThrowAggregate
from m1_validation.types import (
    M1_EXTRA_VERSION,
    Attribution,
    Judgement,
    TruthMethod,
)
from prediction_core import (
    InvalidPrediction,
    InvalidReason,
    Prediction,
    Sample,
    ThrowRecord,
    predict,
)


@dataclass(frozen=True, slots=True)
class BootstrapSpread:
    """再抽出で見積もった、観測由来の予測ばらつき（要件 6.8）。

    Attributes:
        rms_mm: 落下地点（World の**水平2成分**）のばらつき（mm）。再予測が
            得た落下地点の**平均まわりの距離の二乗平均平方根**である。
            見積もれないとき（有効な再予測が2件未満）は `None`。
            **0.0 で埋めない**——「ばらつきが無かった」と「見積もれなかった」
            を混ぜると、後者が要件 6.6 の「観測ノイズ由来の範囲」を空にして、
            すべてのばらつきをモデル由来（予測）へ倒す。
        mean_hit_mm: 再予測が得た落下地点の平均（World mm の水平2成分）。
            ばらつきの中心であり、共通偏りとの比較に使える。有効な再予測が
            1件も無ければ `None`。
        iterations: 実施した再抽出の回数（設定 `bootstrap_iterations`）。
        valid_count: そのうち有効な予測が得られた回数。
        invalid_counts: 無効だった再予測の**理由ごとの件数**。理由の語彙は
            `prediction_core.InvalidReason` であり、本 Spec で再定義しない。
            観測されなかった理由の行は作らない（0 の行をでっち上げない）。
            順序は**最初に現れた順**（`accuracy.py` の `invalid_counts` と
            同じ持ち方）。
        seed: 使用した乱数種（設定 `bootstrap_seed`）。**結果と同じ場所に
            残す**——種が分からない見積もりは再現できない（要件 12.4）。

    Invariants:
        `iterations == valid_count + sum(件数)`。有効・無効のどちらかに
        必ず1回ぶん数える（どこへ消えたか分からない反復を作らない）。
    """

    rms_mm: float | None
    mean_hit_mm: tuple[float, float] | None
    iterations: int
    valid_count: int
    invalid_counts: tuple[tuple[InvalidReason, int], ...]
    seed: int


def bootstrap_prediction_spread(
    record: ThrowRecord, *, iterations: int, seed: int
) -> BootstrapSpread:
    """観測サンプルを再抽出して予測をやり直し、落下地点のばらつきを見積もる。

    観測サンプル列から**重複を許して**同じ件数だけ引き直し（ブートストラップ
    再抽出）、そのたびに `prediction_core.predict` を**記録の設定のまま**
    呼び直す。得られた落下地点の散らばりが、その投擲の観測品質から導かれる
    予測のばらつきである（要件 6.8）。

    Args:
        record: 対象の投擲記録。使うのは `samples` と `config` であり、
            **記録済みの `predictions` は読まない**——再抽出はサンプルから
            予測をやり直すことに意味がある。
        iterations: 再抽出の回数。**暫定の評価候補**であって必須性能では
            ない（モジュール docstring 参照）。
        seed: 乱数種。同一入力・同一種なら**同一の結果**を返す（要件 12.4）。

    Returns:
        `BootstrapSpread`。有効な再予測が2件未満なら `rms_mm` は `None`
        （**0.0 で埋めない**）。

    Raises:
        M1ConfigError: `iterations` が 1 未満の場合。1回も引き直さない指定は
            設定の誤りであり、**実行前に拒否する**（要件 13.6。
            `config._validate_values` が同じ条件を起動時にも見る）。

    Notes:
        再予測が無効になること自体は失敗ではない（例: サンプル数が
        `min_samples` に満たない投擲では全反復が `INSUFFICIENT_SAMPLES` に
        なる）。**観測の不成立は値として返す**——1投擲の見積もり不能で
        投擲群の集計を止めない（design.md「Error Categories and Responses」）。
    """
    if iterations < 1:
        raise M1ConfigError(
            f"bootstrap_iterations は 1 以上でなければならない: {iterations!r}"
            "（再抽出を1回も行わないと帰属のばらつきが出ない）",
            {"key": "bootstrap_iterations", "value": iterations},
        )

    # 乱数はインスタンスで持つ（グローバルな乱数状態を触らない。モジュール
    # docstring 参照）。暗号用途ではないので Mersenne Twister で足りる。
    rng = random.Random(seed)
    hits: list[tuple[float, float]] = []
    invalid_counts: dict[InvalidReason, int] = {}

    def count_invalid(reason: InvalidReason) -> None:
        invalid_counts[reason] = invalid_counts.get(reason, 0) + 1

    for _ in range(iterations):
        outcome = predict(_resample(record.samples, rng), record.config)
        if isinstance(outcome, InvalidPrediction):
            count_invalid(outcome.reason)
            continue
        hit_mm = _finite_hit_point_mm(outcome)
        if hit_mm is None:
            # `prediction_core` は非有限の算出結果を `InvalidPrediction` に
            # するので本来ここへは来ないが、記録は JSON を経由して復元され
            # うる（`accuracy.py` の同名の判断と同じ理由）。NaN を平均へ
            # 混ぜると、ばらつき全体が NaN になって静かに比較不能になる。
            count_invalid(InvalidReason.NON_FINITE_VALUE)
            continue
        hits.append(hit_mm)

    return BootstrapSpread(
        rms_mm=_rms_about_mean_mm(tuple(hits)),
        mean_hit_mm=_mean_point_mm(tuple(hits)),
        iterations=iterations,
        valid_count=len(hits),
        invalid_counts=tuple(invalid_counts.items()),
        seed=seed,
    )


def _resample(samples: Sequence[Sample], rng: random.Random) -> tuple[Sample, ...]:
    """観測サンプル列から**重複を許して**同じ件数だけ引き直す。

    件数を元と同じにするのがブートストラップの定義であり、件数を変えると
    見積もったばらつきに「サンプル数の違いによる分」が混ざる——それは
    観測品質ではなく標本の大きさの効果であり、要件 6.8 が求める量ではない。

    **サンプルは作り直さず、元の値オブジェクトをそのまま引く。** 値を揺らして
    しまうと、見積もりが観測ノイズではなく自家生成ノイズの性質になる。
    """
    count = len(samples)
    return tuple(samples[rng.randrange(count)] for _ in range(count)) if count else ()


def _finite_hit_point_mm(prediction: Prediction) -> tuple[float, float] | None:
    """予測の落下地点から**水平2成分**を取り出す。非有限なら `None`。

    高さ成分を持たないのは、要件 6.2 / 6.8 が扱う誤差が床平面上の量だから
    である（`accuracy.py` の `hit_error_mm` と同じ規約）。
    """
    x_mm = prediction.predicted_hit_x_mm
    y_mm = prediction.predicted_hit_y_mm
    if not (math.isfinite(x_mm) and math.isfinite(y_mm)):
        return None
    return (x_mm, y_mm)


def _mean_point_mm(
    points: tuple[tuple[float, float], ...],
) -> tuple[float, float] | None:
    """点群の平均（World mm の水平2成分）。1点も無ければ `None`。"""
    if not points:
        return None
    count = len(points)
    return (
        math.fsum(x_mm for x_mm, _ in points) / count,
        math.fsum(y_mm for _, y_mm in points) / count,
    )


def _rms_about_mean_mm(points: tuple[tuple[float, float], ...]) -> float | None:
    """**平均まわり**の距離の二乗平均平方根（mm）。2点未満なら `None`。

    **分母は N（母集団 RMS）である。** タスク 5.2 が投擲群のばらつき成分
    （共通偏りを除いた残りの RMS）と直接比較する量なので、両者の分母を
    そろえておかないと**比較そのものが偏る**——要件 6.6 の「観測由来の範囲に
    収まるか」は2つの RMS の大小だけで決まるため、片方だけ N-1 で割ると
    その分だけ「範囲を超えた」側へ倒れる。⚠️ **タスク 5.2 のばらつき成分も
    同じ分母で組むこと。**

    1点では距離が必ず 0 になる。それは「ばらつきが無い」ではなく
    「ばらつきが定義できない」ので、`0.0` ではなく `None` を返す
    （`BootstrapSpread.rms_mm` の docstring 参照）。
    """
    if len(points) < 2:
        return None
    mean = _mean_point_mm(points)
    assert mean is not None  # 2点以上あるので必ず求まる
    mean_x_mm, mean_y_mm = mean
    squared = math.fsum(
        (x_mm - mean_x_mm) ** 2 + (y_mm - mean_y_mm) ** 2 for x_mm, y_mm in points
    )
    return math.sqrt(squared / len(points))


# ---------------------------------------------------------------------------
# 帰属の判定規則（タスク 5.2、要件 6.1-6.7 / 6.9 / 6.10 / 6.11 / 2.4）
# ---------------------------------------------------------------------------

#: `Judgement.question`。判断の種類を1語で表す（`types.Judgement` 参照）。
ATTRIBUTION_QUESTION: str = "attribution"

_CRITERION_TEMPLATE: str = (
    "誤差帰属の判定規則（実測前に固定。design.md「ErrorAttributor」判定規則、"
    "research.md Decision 4、要件 6.1）: "
    "投擲群の誤差ベクトルを、共通の偏り成分（成分ごとの平均ベクトル）と"
    "ばらつき成分（偏りを除いた平均まわり・分母 N の RMS）へ分解する。"
    "【規則1】共通偏りの大きさがばらつきの {bias_ratio:g} 倍に満たないなら"
    "偏り成分なしとする（大きさが 0 の偏りは有意としない。ばらつきを算出"
    "できない――有効な誤差ベクトルが2件未満――ときは有意性を判定せず"
    "判別不能とする）。"
    "【規則2】有意な偏りの向きが、検証レポートの平均オフセット方向と"
    "整合するなら（角度差 {direction:g} 度以内）キャリブレーション由来とする。"
    "【規則3】有意な偏りの向きが、投擲ごとの"
    "カメラ視線方向の軸に沿い、かつ検証レポートで偏りが認められないなら"
    "検出由来の候補とする（Depth が対象物のカメラ側表面を測ることによる"
    "系統的な寄り。候補にとどめ、断定しない）。"
    "【規則4】どちらとも整合しない、または"
    "両者が縮退して区別できないなら判別不能とする"
    "（投擲位置が1箇所で、検証レポートの平均オフセット方向とカメラ視線方向が"
    "同じ軸に乗ってしまう場合を含む。判別可能性を上げるには投擲位置を増やす）。"
    "【規則5】ばらつき成分が、観測サンプルの"
    "再抽出で見積もった予測ばらつきの範囲内なら観測ノイズ由来とする"
    "（境界はちょうどの値を範囲内に含む）。"
    "【規則6】範囲を超え、かつフィットの残差代表値が"
    "その見積もりの {residual_ratio:g} 倍以上ならモデル由来（予測）とする。"
    "【規則7】範囲を超えるが残差が大きくないなら判別不能とする。"
    "向きの比べ方: "
    "検証レポートの平均オフセットとは符号付きの向きで比べ、"
    "カメラ視線方向とは軸（同じ向きと逆向きを区別しない）として比べる"
    "——較正のずれは World 上で符号を保つが、対象物のカメラ側表面を測ること"
    "による寄りはカメラ側へ向くため、視線方向とは逆を向くからである。"
    "検証レポートの平均オフセットが「認められる」とは、その水平成分の"
    "大きさがレポートのばらつきの {bias_ratio:g} 倍以上であることをいう"
    "（レポートにばらつきが無ければ、大きさが 0 でないことをもって認める）。"
    "平均オフセットが記録されていない（検証を実施していない）場合は"
    "「認められない」とも言えないので、規則3は適用しない。"
    "距離帯はカメラから落下地点の真値までの距離を {band:g} mm 幅で区切り、"
    "帯ごとの平均誤差を提示する（遠方でのみ誤差が大きい場合を奥行き計測の"
    "距離特性として読み分けるため。要件 6.11）。"
    "偏り成分とばらつき成分は独立に判定し、"
    "合計誤差の単一値へ畳まない（要件 6.9）。"
    "判別不能は異常ではなく正常な結果である"
    "（無理に一つの原因へ割り当てると、そのあと間違った側を直しにいくことに"
    "なる。要件 6.10）。"
    "すべての閾値は絶対値の目標を置かず、同一測定内の量どうしの相対比較で"
    "定義する。ここに出ている既定値は暫定の評価候補であって必須性能では"
    "ない（要件 13.7）。"
)

_BIAS_RATIONALE_NO_SCATTER: str = (
    "ばらつきが見積もれず、共通の偏りの有意性を判定できない（規則1の適用不能）。"
)
_BIAS_RATIONALE_NONE: str = "共通の偏りは有意でない（規則1）。"
_BIAS_RATIONALE_CALIBRATION: str = (
    "共通の偏りは検証レポートの平均オフセット方向と整合する（規則2）。"
)
_BIAS_RATIONALE_DETECTION: str = (
    "共通の偏りはカメラ視線方向の軸に沿い、"
    "検証レポートでは偏りが認められない（規則3）。"
)
_BIAS_RATIONALE_DEGENERATE: str = (
    "検証レポートの平均オフセット方向とカメラ視線方向が縮退しており、"
    "向きで区別できない（規則4）。"
)
_BIAS_RATIONALE_MISMATCH: str = "共通の偏りはどちらの向きとも整合しない（規則4）。"

_SCATTER_RATIONALE_NO_SCATTER: str = (
    "投擲群のばらつきを算出できない（有効な誤差ベクトルが2件未満）。"
)
_SCATTER_RATIONALE_NO_BOOTSTRAP: str = (
    "観測由来のばらつきを見積もれず、ばらつき成分を判定できない"
    "（規則5〜7の適用不能）。"
)
_SCATTER_RATIONALE_NO_RESIDUAL: str = (
    "フィットの残差が記録されておらず、ばらつき成分を判定できない"
    "（規則6・7の適用不能）。"
)
_SCATTER_RATIONALE_OBSERVATION_NOISE: str = (
    "ばらつきは再抽出で見積もった観測由来の範囲に収まる（規則5）。"
)
_SCATTER_RATIONALE_PREDICTION: str = (
    "ばらつきが観測由来の範囲を超え、フィットの残差も大きい（規則6）。"
)
_SCATTER_RATIONALE_SMALL_RESIDUAL: str = (
    "ばらつきは観測由来の範囲を超えるが、フィットの残差は大きくない（規則7）。"
)

#: 検証レポートの平均オフセットの状態（`_report_bias_state`）。
#: **「認められない」と「測っていない」を同じ値にしない**（要件 6.5）。
_REPORT_BIAS_RECOGNIZED: str = "recognized"
_REPORT_BIAS_NEGLIGIBLE: str = "negligible"
_REPORT_BIAS_UNMEASURED: str = "unmeasured"

_UNVERIFIED_NOTICE: str = (
    "未検証のキャリブレーションで得た投擲群であり、"
    "誤差の帰属ができない（要件 2.2）。"
)


def attribution_criterion(
    *,
    direction_agreement_deg: float,
    bias_significance_ratio: float,
    residual_significance_ratio: float,
    range_band_mm: float,
) -> str:
    """実際に適用する規則の説明文を組み立てる（要件 6.1）。

    **実際に使った閾値を文面へ入れる。** 規則の文だけを残して閾値を伏せると、
    同じ文で違う判定が正当化できてしまう（`convergence.convergence_criterion`
    と同じ理由）。文面は判定値によって変わらない——結果に合わせて動く規則は
    規則ではない。
    """
    return _CRITERION_TEMPLATE.format(
        bias_ratio=bias_significance_ratio,
        direction=direction_agreement_deg,
        residual_ratio=residual_significance_ratio,
        band=range_band_mm,
    )


@dataclass(frozen=True, slots=True)
class BiasComponent:
    """投擲群に共通する偏り成分（要件 6.2-6.5, 6.10）。

    Attributes:
        vector_mm: 共通偏りのベクトル（World mm の水平2成分）。
            **スカラーへ畳まない**——向きが帰属の判別そのものである。
        norm_mm: その大きさ（mm）。
        significance_ratio: 「偏りの大きさ ÷ ばらつき」。ばらつきが算出
            できない（有効な誤差ベクトルが2件未満）か 0 のときは `None`。
            **0.0 で埋めない**——比が定義できないことと比が 0 であることは
            別である。
        world_fixed_agreement_deg: 検証レポートの平均オフセット方向との
            角度差（deg、0〜180 の**符号付きの向き**として測る）。
            レポートに偏りが記録されていない、または向きが定まらないときは
            `None`（**測っていないことを「整合した」の根拠にしない**）。
        camera_ray_agreement_deg: カメラ視線方向との角度差（deg）。
            **軸として**測る（0〜90）。投擲ごとに向きが違うので、
            **すべての投擲に対する角度差の最大値**を採る——要件 6.5 の
            「一貫して整合」は最も外れた投擲でも閾値内、という意味である。
        degenerate: 検証レポートの平均オフセット方向とカメラ視線方向が
            縮退して区別できないか（要件 6.10。research.md Decision 4）。
        attribution: 帰属先。`NONE` は「有意な偏りが無い」、
            `UNDETERMINED` は「決めきれない」であり、**別物**である。
    """

    vector_mm: tuple[float, float]
    norm_mm: float
    significance_ratio: float | None
    world_fixed_agreement_deg: float | None
    camera_ray_agreement_deg: float | None
    degenerate: bool
    attribution: Attribution


@dataclass(frozen=True, slots=True)
class ScatterComponent:
    """共通の偏りを除いた投擲ごとのばらつき成分（要件 6.6-6.8）。

    Attributes:
        rms_mm: ばらつき（mm）。**平均まわり・分母 N** の母集団 RMS であり、
            `BootstrapSpread.rms_mm` と同じ分母である（要件 6.6 は2つの RMS
            の大小だけで決まるので、片方だけ N-1 にすると比較が偏る）。
            誤差ベクトルが2件未満なら `None`。
        bootstrap_rms_mm: 再抽出で見積もった観測由来の予測ばらつきの代表値
            （mm、投擲ごとの見積もりの中央値）。1投擲も見積もれなければ
            `None`。**0.0 で埋めない**——埋めると観測ノイズ由来の範囲が
            空になり、すべてのばらつきがモデル由来へ倒れる。
        residual_median_mm: フィットの残差の代表値（mm、投擲ごとの最終予測の
            残差の中央値）。残差が1件も記録されていなければ `None`。
        attribution: 帰属先（`OBSERVATION_NOISE` / `PREDICTION` /
            `UNDETERMINED`）。
    """

    rms_mm: float | None
    bootstrap_rms_mm: float | None
    residual_median_mm: float | None
    attribution: Attribution


@dataclass(frozen=True, slots=True)
class RangeBand:
    """距離帯ごとの誤差（要件 6.11）。

    Attributes:
        range_lo_mm: 帯の下限（mm、含む）。カメラから落下地点の真値までの距離。
        range_hi_mm: 帯の上限（mm、含まない）。
        throw_count: この帯に入った投擲数。
        mean_error_norm_mm: この帯の誤差の大きさの平均（mm）。

    **投擲が1件も無い帯は作らない。** 0 件の行をでっち上げると、
    「その距離では誤差が 0 だった」と読まれ得る。
    """

    range_lo_mm: float
    range_hi_mm: float
    throw_count: int
    mean_error_norm_mm: float


@dataclass(frozen=True, slots=True)
class AttributionResult:
    """帰属の結果（要件 6.9: **常に成分ごとの内訳**）。

    Attributes:
        bias: 共通の偏り成分。
        scatter: ばらつき成分。
        range_bands: 距離帯ごとの誤差（近い順）。
        calibration_reference: 記録に埋め込まれた検証レポートの要約
            （要件 2.4）。**上流パッケージを import せずに取り込んだ値**で
            あり、あとから「何と突き合わせたのか」を辿れるように残す。
        judgement: 判断（判定規則の説明文・判定値・根拠・暫定の印）。

    **合計誤差の単一値を持たない。** 畳んだ時点で帰属の情報が消える。
    """

    bias: BiasComponent
    scatter: ScatterComponent
    range_bands: tuple[RangeBand, ...]
    calibration_reference: Mapping[str, object]
    judgement: Judgement

    def __post_init__(self) -> None:
        # 取り込んだ要約は複製して持つ（`Judgement.evidence` と同じ方針）。
        # 記録側のマッピングを抱えたままだと、レポートに出る「突き合わせた値」
        # が算出時のものと食い違い得る。
        object.__setattr__(
            self, "calibration_reference", dict(self.calibration_reference)
        )


@dataclass(frozen=True, slots=True)
class _ThrowView:
    """帰属が1投擲から読む材料（本モジュール内部の作業用）。"""

    record_id: str
    error_vector_mm: tuple[float, float]
    camera_ray_horizontal: tuple[float, float] | None
    residual_mm: float | None
    range_mm: float | None
    bootstrap_rms_mm: float | None
    calibration_summary: Mapping[str, object]


def attribute(
    aggregate: ThrowAggregate,
    records: Sequence[ThrowRecord],
    *,
    settings: M1Settings,
) -> AttributionResult:
    """投擲群の誤差を、キャリブレーション・検出・予測へ帰属させる（要件 6）。

    判定規則は `attribution_criterion()` が返す説明文のとおりであり、
    **実測前に固定されている**。結果の `judgement.criterion` に同じ文面を
    埋め込むので、あとから規則のほうを結果に合わせて読み替えられない
    （要件 6.1）。

    Args:
        aggregate: 1つのキャリブレーション（識別子 × 検証状態）に属する
            投擲群の集計。誤差ベクトル群と、投擲ごとの行（`record_id` との
            対応付け）をここから読む。
        records: 同じ群の投擲記録。**カメラ視線方向・フィットの残差・
            落下地点の真値・検証レポートの要約**はここから読む。群に属さない
            記録が混ざっていてもよい（`record_id` で引く）。
        settings: 解決済みの設定。`attribution`（閾値・再抽出）と
            `layout.camera_position_world_mm`（距離帯）を読む。

    Returns:
        `AttributionResult`。**合計誤差の単一値を返さない**（要件 6.9）。

    Raises:
        M1ValidationError: 誤差ベクトルが0件の場合
            （`context["reason"]` は `FailureReason.INSUFFICIENT_TRIALS`）。
            **帰属すべき材料が無いことを「偏りが無い」と言い換えない。**
        M1ConfigError: 行と誤差ベクトル群の件数が食い違う、行に対応する記録
            が無い、記録の `record_id` が重複する、`extra["m1"]` が無い／
            形式版が未知、同じ群の記録が違う検証レポートを載せている、の
            いずれか。

    Notes:
        同一入力・同一設定に対して同一結果を返す（要件 12.4）。再抽出の
        乱数は `bootstrap_prediction_spread()` の中でインスタンス化される。
    """
    config = settings.attribution
    if not aggregate.error_vectors:
        raise M1ValidationError(
            "誤差ベクトルが1件も無い投擲群には帰属を出せない: "
            f"calibration_id={aggregate.calibration_id!r}, "
            f"throw_count={aggregate.throw_count}。"
            "0件で「偏り成分なし」を返すと、材料が無いことが"
            "「偏りが無い」という積極的な結論に化ける",
            {
                "reason": str(FailureReason.INSUFFICIENT_TRIALS),
                "calibration_id": aggregate.calibration_id,
                "throw_count": aggregate.throw_count,
                "error_vector_count": 0,
            },
        )

    views = _build_views(aggregate, records, settings=settings)
    reference = _calibration_reference(views)

    bias, bias_reason = _judge_bias(views, reference=reference, config=config)
    scatter, scatter_reason = _judge_scatter(views, config=config)
    bands = _range_bands(views, band_mm=config.range_band_mm)

    parts = [bias_reason, scatter_reason]
    if not aggregate.verified:
        parts.append(_UNVERIFIED_NOTICE)

    return AttributionResult(
        bias=bias,
        scatter=scatter,
        range_bands=bands,
        calibration_reference=reference,
        judgement=Judgement(
            question=ATTRIBUTION_QUESTION,
            criterion=attribution_criterion(
                direction_agreement_deg=config.direction_agreement_deg,
                bias_significance_ratio=config.bias_significance_ratio,
                residual_significance_ratio=config.residual_significance_ratio,
                range_band_mm=config.range_band_mm,
            ),
            verdict=f"bias={bias.attribution}/scatter={scatter.attribution}",
            rationale="".join(parts),
            evidence={
                "calibration_id": aggregate.calibration_id,
                "verified": aggregate.verified,
                "throw_count": len(views),
                "bias_vector_mm": list(bias.vector_mm),
                "bias_norm_mm": bias.norm_mm,
                "significance_ratio": bias.significance_ratio,
                "world_fixed_agreement_deg": bias.world_fixed_agreement_deg,
                "camera_ray_agreement_deg": bias.camera_ray_agreement_deg,
                "degenerate": bias.degenerate,
                "scatter_rms_mm": scatter.rms_mm,
                "bootstrap_rms_mm": scatter.bootstrap_rms_mm,
                "residual_median_mm": scatter.residual_median_mm,
                "bootstrap_iterations": config.bootstrap_iterations,
                "bootstrap_seed": config.bootstrap_seed,
                "range_band_count": len(bands),
            },
            provisional=aggregate.provisional or not aggregate.verified,
        ),
    )


# ---------------------------------------------------------------------------
# 入力の取り出し（記録に埋め込まれた要約からしか読まない。要件 13.1）
# ---------------------------------------------------------------------------


def _build_views(
    aggregate: ThrowAggregate,
    records: Sequence[ThrowRecord],
    *,
    settings: M1Settings,
) -> tuple[_ThrowView, ...]:
    """行と記録を突き合わせ、投擲ごとの材料を組む。

    **行を黙って飛ばさない。** 飛ばすと、カメラ視線方向も残差も距離も欠けた
    まま判定が進み、「どちらとも整合しない → 判別不能」という結論が
    **記録の欠落によって**作られる。
    """
    rows = tuple(
        row for row in aggregate.per_throw if row.error_vector_mm is not None
    )
    if len(rows) != len(aggregate.error_vectors):
        raise M1ConfigError(
            "投擲群の行と誤差ベクトル群の件数が食い違う: "
            f"rows={len(rows)}, error_vectors={len(aggregate.error_vectors)}"
            "（対応付けがずれると、別の投擲の誤差に別の投擲の視線方向を"
            "当てることになる）",
            {
                "calibration_id": aggregate.calibration_id,
                "row_count": len(rows),
                "error_vector_count": len(aggregate.error_vectors),
            },
        )

    by_id: dict[str, ThrowRecord] = {}
    for record in records:
        if record.record_id in by_id:
            raise M1ConfigError(
                f"投擲記録の record_id が重複している: {record.record_id!r}"
                "（どちらの記録を帰属の材料にしたのか決められない）",
                {"record_id": record.record_id},
            )
        by_id[record.record_id] = record

    camera_world_mm = settings.layout.camera_position_world_mm
    config = settings.attribution
    views: list[_ThrowView] = []
    for row, vector in zip(rows, aggregate.error_vectors, strict=True):
        record = by_id.get(row.record_id)
        if record is None:
            raise M1ConfigError(
                f"誤差ベクトルを持つ投擲 {row.record_id!r} の記録が無い。"
                "記録の欠落を「材料が無い投擲」として黙って飛ばすと、"
                "判別不能が欠落によって作られる",
                {"record_id": row.record_id},
            )
        payload = _m1_payload(record)
        summary = payload.get("calibration")
        if not isinstance(summary, Mapping):
            raise M1ConfigError(
                f"投擲 {record.record_id!r} の extra['m1'] に"
                "キャリブレーション要約が無い。突き合わせる相手が分からない",
                {"record_id": record.record_id},
            )
        impact_mm = _impact_point_world_mm(payload)
        spread = bootstrap_prediction_spread(
            record,
            iterations=config.bootstrap_iterations,
            seed=config.bootstrap_seed,
        )
        views.append(
            _ThrowView(
                record_id=row.record_id,
                error_vector_mm=vector,
                camera_ray_horizontal=_camera_ray_horizontal(payload),
                residual_mm=_final_residual_mm(record),
                range_mm=(
                    None
                    if impact_mm is None
                    else _distance_mm(camera_world_mm, impact_mm)
                ),
                bootstrap_rms_mm=spread.rms_mm,
                calibration_summary=summary,
            )
        )
    return tuple(views)


def _m1_payload(record: ThrowRecord) -> Mapping[str, object]:
    """本 Spec の拡張領域を取り出し、形式版を照合する。

    形式版が未知なら**内容を推測して読まない**（`aggregate.py` と同じ方針。
    キーの意味が変わっていれば、読めた値のほうが危ない）。
    """
    payload = record.extra.get("m1")
    if not isinstance(payload, Mapping):
        raise M1ConfigError(
            f"投擲 {record.record_id!r} に本 Spec の拡張領域 extra['m1'] が無い。"
            "検証レポートの要約もカメラ視線方向も読めない",
            {"record_id": record.record_id},
        )
    version = payload.get("m1_extra_version")
    if version != M1_EXTRA_VERSION:
        raise M1ConfigError(
            f"投擲 {record.record_id!r} の extra['m1'] の形式版が既知でない: "
            f"{version!r}（既知は {M1_EXTRA_VERSION!r}）。内容を推測して読まない",
            {
                "record_id": record.record_id,
                "m1_extra_version": version,
                "known_version": M1_EXTRA_VERSION,
            },
        )
    return payload


def _camera_ray_horizontal(
    payload: Mapping[str, object],
) -> tuple[float, float] | None:
    """その投擲のカメラ視線方向（World の水平2成分、単位ベクトル）。

    観測点ごとの視線方向（`provenance[*].camera_ray_unit`）の平均を採って
    正規化する。1投擲のあいだに対象は動くので視線方向もわずかに動くが、
    帰属が見るのは**投擲群に共通する偏りがどちらの向きを向くか**であり、
    投擲を代表する1方向で足りる。

    水平成分が 0（カメラの真下・真上）なら向きが定まらないので `None`。
    **0 ベクトルを方向として扱わない**（`seam.camera_ray_unit()` と同じ判断）。
    """
    provenance = payload.get("provenance")
    if not isinstance(provenance, Sequence) or isinstance(provenance, str | bytes):
        return None
    sum_x = 0.0
    sum_y = 0.0
    count = 0
    for item in provenance:
        if not isinstance(item, Mapping):
            continue
        pair = _leading_pair(item.get("camera_ray_unit"))
        if pair is None:
            continue
        sum_x += pair[0]
        sum_y += pair[1]
        count += 1
    if count == 0:
        return None
    return _unit((sum_x / count, sum_y / count))


def _leading_pair(value: object) -> tuple[float, float] | None:
    """並びの先頭2成分を有限の float として取り出す。読めなければ `None`。"""
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return None
    items = list(value)
    if len(items) < 2:
        return None
    x_mm, y_mm = items[0], items[1]
    if not (isinstance(x_mm, int | float) and isinstance(y_mm, int | float)):
        return None
    if not (math.isfinite(x_mm) and math.isfinite(y_mm)):
        return None
    return (float(x_mm), float(y_mm))


def _impact_point_world_mm(
    payload: Mapping[str, object],
) -> tuple[float, float, float] | None:
    """落下地点の真値（World mm）。未記入・欠測なら `None`。

    **欠測を原点で埋めない。** 埋めるとカメラから原点までの距離帯へ入り、
    「その距離では誤差が大きい」という読みが真値の書き忘れによって作られる。
    """
    truth = payload.get("truth")
    if not isinstance(truth, Mapping):
        return None
    entry = truth.get("impact_point_world_mm")
    if not isinstance(entry, Mapping):
        return None
    if entry.get("method") == str(TruthMethod.MISSING):
        return None
    value = entry.get("value")
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return None
    items = list(value)
    if len(items) != 3 or not all(isinstance(item, int | float) for item in items):
        return None
    point = (float(items[0]), float(items[1]), float(items[2]))
    return point if all(math.isfinite(item) for item in point) else None


def _final_residual_mm(record: ThrowRecord) -> float | None:
    """最終の有効な予測のフィット残差（mm）。無ければ `None`。

    `truth.py` の同名の私有関数は残差が無いとき `0.0` を返すが、あちらは
    「不確かさに足す材料が無い」ことを 0 で表してよい文脈である。**ここでは
    `None` にする**——0 を入れると「残差が小さい」と読まれ、要件 6.7 の
    モデル由来（予測）を見落とす。
    """
    for outcome in reversed(record.predictions):
        if isinstance(outcome, Prediction):
            if not math.isfinite(outcome.residual):
                return None
            return abs(outcome.residual)
    return None


def _calibration_reference(views: Sequence[_ThrowView]) -> Mapping[str, object]:
    """群が突き合わせる検証レポートの要約（要件 2.4）。

    同じ群（同じ識別子・同じ検証状態）の記録が違うレポートを載せていたら
    **混ぜない**（要件 2.5 と同じ理由）。平均オフセットが違えば、それは別の
    母集団であり、突き合わせる相手が1つに決まらない。

    ここでは記録側のマッピングをそのまま返す。複製は `AttributionResult` の
    `__post_init__` が1箇所で行う——複製の場所が2つあると、片方を外しても
    もう片方が効いてしまい、**複製しているつもりの経路**が検査から漏れる。
    """
    first = views[0].calibration_summary
    for view in views[1:]:
        summary = view.calibration_summary
        for key in ("bias_mm", "scatter_rms_mm"):
            if summary.get(key) != first.get(key):
                raise M1ConfigError(
                    "同じ群の投擲が違う検証レポートを載せている: "
                    f"{view.record_id!r} の {key} は {summary.get(key)!r}、"
                    f"{views[0].record_id!r} の {key} は {first.get(key)!r}"
                    "（突き合わせる相手が1つに決まらない）",
                    {
                        "record_id": view.record_id,
                        "key": key,
                        "value": summary.get(key),
                        "expected": first.get(key),
                    },
                )
    return first


# ---------------------------------------------------------------------------
# 判定（規則1〜7。適用した規則をそのまま説明文として返す）
# ---------------------------------------------------------------------------


def _judge_bias(
    views: Sequence[_ThrowView],
    *,
    reference: Mapping[str, object],
    config: AttributionConfig,
) -> tuple[BiasComponent, str]:
    """共通の偏り成分を求め、向きで帰属先を判別する（規則1〜4）。

    帰属先と**適用した規則の説明文を同時に**返す。あとから判定値を見て説明文
    を組み立て直す形にすると、判定と説明文が食い違う経路ができる
    （tasks.md タスク4.4: 適用した規則と記録した規則の取り違え）。
    """
    vectors = tuple(view.error_vector_mm for view in views)
    mean = _mean_point_mm(vectors)
    assert mean is not None  # 呼び出し元が1件以上を保証している
    bias_norm_mm = math.hypot(mean[0], mean[1])
    scatter_rms_mm = _rms_about_mean_mm(vectors)
    ratio = (
        None
        if scatter_rms_mm is None or scatter_rms_mm == 0.0
        else bias_norm_mm / scatter_rms_mm
    )

    bias_dir = _unit(mean)
    report_dir = _unit_or_none(_leading_pair(reference.get("bias_mm")))
    report_state = _report_bias_state(
        reference, bias_significance_ratio=config.bias_significance_ratio
    )
    camera_dirs = tuple(
        view.camera_ray_horizontal
        for view in views
        if view.camera_ray_horizontal is not None
    )

    world_deg = (
        None
        if bias_dir is None or report_dir is None
        else _angle_deg(bias_dir, report_dir)
    )
    camera_deg = (
        None
        if bias_dir is None or not camera_dirs
        else max(_axis_angle_deg(bias_dir, ray) for ray in camera_dirs)
    )
    degenerate = bool(
        report_dir is not None
        and report_state == _REPORT_BIAS_RECOGNIZED
        and camera_dirs
        and max(_axis_angle_deg(report_dir, ray) for ray in camera_dirs)
        <= config.direction_agreement_deg
    )

    attribution, reason = _bias_attribution(
        scatter_rms_mm=scatter_rms_mm,
        bias_norm_mm=bias_norm_mm,
        world_deg=world_deg,
        camera_deg=camera_deg,
        degenerate=degenerate,
        report_state=report_state,
        config=config,
    )
    return (
        BiasComponent(
            vector_mm=mean,
            norm_mm=bias_norm_mm,
            significance_ratio=ratio,
            world_fixed_agreement_deg=world_deg,
            camera_ray_agreement_deg=camera_deg,
            degenerate=degenerate,
            attribution=attribution,
        ),
        reason,
    )


def _bias_significant(
    *, bias_norm_mm: float, scatter_rms_mm: float, ratio: float
) -> bool:
    """偏りが有意か（規則1）。

    **割り算にしない。** ばらつきが 0（全投擲が同じ誤差ベクトル）の群では比が
    発散するので、`偏りの大きさ >= 倍率 × ばらつき` という掛け算で判定する。
    大きさ 0 の偏りを有意としないのは、`0 >= 倍率 × 0` が真になってしまう
    ためである。境界（ちょうど倍率どおり）は有意側に含める。
    """
    return bias_norm_mm > 0.0 and bias_norm_mm >= ratio * scatter_rms_mm


def _bias_attribution(
    *,
    scatter_rms_mm: float | None,
    bias_norm_mm: float,
    world_deg: float | None,
    camera_deg: float | None,
    degenerate: bool,
    report_state: str,
    config: AttributionConfig,
) -> tuple[Attribution, str]:
    """規則1〜4を**この順で**当てる（相互排他）。"""
    if scatter_rms_mm is None:
        return (Attribution.UNDETERMINED, _BIAS_RATIONALE_NO_SCATTER)
    if not _bias_significant(
        bias_norm_mm=bias_norm_mm,
        scatter_rms_mm=scatter_rms_mm,
        ratio=config.bias_significance_ratio,
    ):
        return (Attribution.NONE, _BIAS_RATIONALE_NONE)
    if degenerate:
        return (Attribution.UNDETERMINED, _BIAS_RATIONALE_DEGENERATE)
    if (
        report_state == _REPORT_BIAS_RECOGNIZED
        and world_deg is not None
        and world_deg <= config.direction_agreement_deg
    ):
        return (Attribution.CALIBRATION, _BIAS_RATIONALE_CALIBRATION)
    if (
        report_state == _REPORT_BIAS_NEGLIGIBLE
        and camera_deg is not None
        and camera_deg <= config.direction_agreement_deg
    ):
        return (Attribution.DETECTION, _BIAS_RATIONALE_DETECTION)
    return (Attribution.UNDETERMINED, _BIAS_RATIONALE_MISMATCH)


def _judge_scatter(
    views: Sequence[_ThrowView], *, config: AttributionConfig
) -> tuple[ScatterComponent, str]:
    """ばらつき成分を求め、観測由来かモデル由来かを分ける（規則5〜7）。

    ばらつきは**平均まわり**の RMS であり、平均を引くこと自体が共通偏りの
    除去である（要件 6.2 の「共通の偏りを除いた投擲ごとの残り」）。
    """
    vectors = tuple(view.error_vector_mm for view in views)
    scatter_rms_mm = _rms_about_mean_mm(vectors)

    bootstrap_values = [
        view.bootstrap_rms_mm for view in views if view.bootstrap_rms_mm is not None
    ]
    residual_values = [
        view.residual_mm for view in views if view.residual_mm is not None
    ]
    bootstrap_rms_mm = (
        statistics.median(bootstrap_values) if bootstrap_values else None
    )
    residual_median_mm = (
        statistics.median(residual_values) if residual_values else None
    )

    attribution, reason = _scatter_attribution(
        scatter_rms_mm=scatter_rms_mm,
        bootstrap_rms_mm=bootstrap_rms_mm,
        residual_median_mm=residual_median_mm,
        config=config,
    )
    return (
        ScatterComponent(
            rms_mm=scatter_rms_mm,
            bootstrap_rms_mm=bootstrap_rms_mm,
            residual_median_mm=residual_median_mm,
            attribution=attribution,
        ),
        reason,
    )


def _scatter_attribution(
    *,
    scatter_rms_mm: float | None,
    bootstrap_rms_mm: float | None,
    residual_median_mm: float | None,
    config: AttributionConfig,
) -> tuple[Attribution, str]:
    """規則5〜7を**この順で**当てる（相互排他）。"""
    if scatter_rms_mm is None:
        return (Attribution.UNDETERMINED, _SCATTER_RATIONALE_NO_SCATTER)
    if bootstrap_rms_mm is None:
        return (Attribution.UNDETERMINED, _SCATTER_RATIONALE_NO_BOOTSTRAP)
    if scatter_rms_mm <= bootstrap_rms_mm:
        return (Attribution.OBSERVATION_NOISE, _SCATTER_RATIONALE_OBSERVATION_NOISE)
    if residual_median_mm is None:
        return (Attribution.UNDETERMINED, _SCATTER_RATIONALE_NO_RESIDUAL)
    if residual_median_mm >= config.residual_significance_ratio * bootstrap_rms_mm:
        return (Attribution.PREDICTION, _SCATTER_RATIONALE_PREDICTION)
    return (Attribution.UNDETERMINED, _SCATTER_RATIONALE_SMALL_RESIDUAL)


def _range_bands(
    views: Sequence[_ThrowView], *, band_mm: float
) -> tuple[RangeBand, ...]:
    """距離帯ごとの平均誤差（要件 6.11）。

    帯は `[k × 幅, (k+1) × 幅)` であり、**データに依存しない**（分位点で切ると、
    投擲を1つ足しただけで帯の境界が動いて前回の結果と比べられなくなる）。
    距離が分からない投擲はどの帯にも入れない。
    """
    buckets: dict[int, list[float]] = {}
    for view in views:
        if view.range_mm is None:
            continue
        index = math.floor(view.range_mm / band_mm)
        buckets.setdefault(index, []).append(
            math.hypot(view.error_vector_mm[0], view.error_vector_mm[1])
        )
    return tuple(
        RangeBand(
            range_lo_mm=index * band_mm,
            range_hi_mm=(index + 1) * band_mm,
            throw_count=len(norms),
            mean_error_norm_mm=math.fsum(norms) / len(norms),
        )
        for index, norms in sorted(buckets.items())
    )


# ---------------------------------------------------------------------------
# 幾何（標準ライブラリだけで書く）
# ---------------------------------------------------------------------------


def _unit(vector_mm: tuple[float, float]) -> tuple[float, float] | None:
    """水平2成分の単位ベクトル。大きさが 0 か非有限なら `None`。"""
    norm = math.hypot(vector_mm[0], vector_mm[1])
    if norm == 0.0 or not math.isfinite(norm):
        return None
    return (vector_mm[0] / norm, vector_mm[1] / norm)


def _unit_or_none(
    vector_mm: tuple[float, float] | None,
) -> tuple[float, float] | None:
    return None if vector_mm is None else _unit(vector_mm)


def _angle_deg(a: tuple[float, float], b: tuple[float, float]) -> float:
    """2つの単位ベクトルのなす角（deg、0〜180）。**符号付きの向きで比べる。**"""
    dot = min(1.0, max(-1.0, a[0] * b[0] + a[1] * b[1]))
    return math.degrees(math.acos(dot))


def _axis_angle_deg(a: tuple[float, float], b: tuple[float, float]) -> float:
    """2つの単位ベクトルが張る**軸**のなす角（deg、0〜90）。

    同じ向きと逆向きを区別しない。Depth が対象物のカメラ側表面を測ることに
    よる寄りは**カメラ側へ**向くので、視線方向（カメラ→対象）とは逆を向く。
    符号付きで比べると、物理的に正しい向きのほうが「整合しない」と判定される。
    """
    angle = _angle_deg(a, b)
    return min(angle, 180.0 - angle)


def _distance_mm(
    a: tuple[float, float, float], b: tuple[float, float, float]
) -> float:
    """3次元の距離（mm）。"""
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def _report_bias_state(
    reference: Mapping[str, object], *, bias_significance_ratio: float
) -> str:
    """検証レポートの平均オフセットの状態（要件 6.4 / 6.5 の分かれ目）。

    **3値にするのは意図である。** 「偏りが認められる」「偏りが認められない」
    「そもそも測っていない」は別物であり、要件 6.5 が規則3（検出由来の候補）
    に要求するのは**2つめ**である。測っていないことを「認められない」の
    根拠に使うと、**検証を実施していないだけの群が検出由来へ倒れる**
    （`world_fixed_agreement_deg` を欠測にする理由と同じ）。

    「認められる」かどうかは**絶対値の目標を置かず**、レポート自身のばらつき
    （`scatter_rms_mm`）との相対比較で決める。ばらつきが記録されていなければ、
    大きさが 0 でないことをもって認める。
    """
    horizontal = _leading_pair(reference.get("bias_mm"))
    if horizontal is None:
        return _REPORT_BIAS_UNMEASURED
    norm_mm = math.hypot(horizontal[0], horizontal[1])
    if norm_mm == 0.0:
        return _REPORT_BIAS_NEGLIGIBLE
    scatter = reference.get("scatter_rms_mm")
    if not isinstance(scatter, int | float) or not math.isfinite(scatter):
        return _REPORT_BIAS_RECOGNIZED
    if norm_mm >= bias_significance_ratio * float(scatter):
        return _REPORT_BIAS_RECOGNIZED
    return _REPORT_BIAS_NEGLIGIBLE
