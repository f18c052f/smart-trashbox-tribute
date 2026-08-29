"""誤差の帰属（★ 本 Spec の核）。

design.md「Components and Interfaces / L6: 帰属 / ErrorAttributor」、
tasks.md タスク 5.1、要件 6.8。

本タスク（5.1）が置くのは、**観測品質に基づく予測のばらつきを、観測サンプルを
再抽出して予測をやり直す方法で見積もる**部分だけである（要件 6.8）。
判定規則そのもの（共通偏り／ばらつき成分への分解と帰属先の決定）はタスク 5.2
が同じモジュールへ足す。

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
from collections.abc import Sequence
from dataclasses import dataclass

from m1_validation.errors import M1ConfigError
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
