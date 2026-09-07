"""投擲群への集計（代表値・ばらつき・試行数・暫定の印）。

design.md「Components and Interfaces / L4-L5: 真値と実測 / ThrowAggregator」、
tasks.md タスク 4.6、要件 2.5, 5.9, 5.10。

**本モジュールの核心は「混ぜない」ことである。** 要件 2.5 が
「投擲を識別子ごとに分けて集計し、混在したまま平均しない」と定めているのは、
キャリブレーション結果が入れ替わると**系統誤差の違う母集団**になるからである。
混ぜて平均すると、座標系の入れ替わり（数 cm の平行移動でありうる）が
**ばらつきとして紛れ込む**。そうなると誤差の帰属（要件 6）は成立しない
——共通の偏りが消え、代わりにばらつきが膨らむので、「観測ノイズが大きい」と
いう誤った結論が出る。`docs/requirements.md §6.2` が警告する
「座標系が数 cm ずれていても症状は『予測が悪い』にしか見えない」が、
集計の段でも同じ形で現れる。

**未検証キャリブレーションで得た投擲も、検証済みのものと混ぜない。** 理由は
同じである。検証を通っていない結果の誤差は系統誤差か予測誤差か分離できない
（要件 2.1 / 2.2）ので、検証済みの投擲と同じ平均に入れると**分離できる側の
データまで分離できなくする**。したがって群の鍵は
`(calibration_id, verified)` の対であり、識別子だけではない。

**暫定の印は「集計自体は返す」ための仕組みである**（要件 5.10）。試行数が
下限に満たなくても数値は出す。出さないと、下限に届くまで何も見えないまま
実験を続けることになる。返したうえで**判断に用いてよい状態ではない**旨を
印として付ける。`min_valid_throws` などの下限は**暫定の評価候補であって
必須性能ではない**（`config.py` の注記）。

**欠測は `None` であって 0 ではない。** 「0 だった」と「測っていない」を
混ぜると、前者として代表値を良い方へ引っ張る。分布は `count`（値が得られた
試行数）と `missing`（欠測数）を必ず併記し、両者の和は常にその群の
`throw_count` に等しい——項目ごとに分母が動くと、欠測の多さが読めなくなる。

**帰属の入力はベクトルのまま束ねる**（`error_vectors`）。スカラーに畳んだ
時点で帰属（要件 6.3）が成立しない——共通の偏りが World 座標系に固定された
向きなのか、カメラ視線方向に沿っているのかを、向きでしか判別できないため
である（`accuracy.py` モジュール docstring）。

本モジュールは L5 層の評価側であり、`sensing_foundation` /
`flying_object_tracking` / `world_frame_calibration` を**直接 import しない**。
失敗投擲の除外は `runner.successful_throws()` に委ね、規則を書き直さない
（置き場所を各所で覚え直すと、変わったときに黙って全件が「成功」に見える）。
数値計算は標準ライブラリだけで書く。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from m1_validation.config import M1Settings
from m1_validation.errors import M1ConfigError
from m1_validation.metrics.accuracy import AccuracyResult, PredictionError
from m1_validation.metrics.convergence import (
    VERDICT_CONVERGED,
    VERDICT_NOT_CONVERGED,
    VERDICT_NOT_MEASURABLE,
    ConvergenceResult,
)
from m1_validation.metrics.flight import FlightResult
from m1_validation.metrics.latency import LatencyResult
from m1_validation.runner import successful_throws
from m1_validation.types import M1_EXTRA_VERSION, ThrowTruth, TruthMethod
from prediction_core import SourceKind, ThrowRecord

# ---------------------------------------------------------------------------
# 項目キー
# ---------------------------------------------------------------------------

#: 実測項目1（総飛行時間）。
ITEM_TOTAL_FLIGHT_MS: str = "total_flight_ms"
#: 実測項目2（リリース〜検出開始）。★プロジェクトで最も未検証な量。
ITEM_RELEASE_TO_DETECT_MS: str = "release_to_detect_ms"
#: 実測項目3（検出開始〜**初回予測**）。`LatencyAggregator` の結果から取る。
ITEM_DETECT_TO_FIRST_PREDICTION_MS: str = "detect_to_first_prediction_ms"
#: 実測項目4（落下地点誤差）の**初回予測**時点の大きさ。
ITEM_HIT_ERROR_FIRST_MM: str = "hit_error_norm_first_mm"
#: 実測項目4 の**最終予測**時点の大きさ。
ITEM_HIT_ERROR_FINAL_MM: str = "hit_error_norm_final_mm"
#: 実測項目5（落下時刻誤差）の初回予測時点の値。
ITEM_TIME_ERROR_FIRST_MS: str = "time_error_first_ms"
#: 実測項目5 の最終予測時点の値。
ITEM_TIME_ERROR_FINAL_MS: str = "time_error_final_ms"
#: 実測項目6（狙い誤差）。
ITEM_AIM_ERROR_MM: str = "aim_error_mm"
#: 実測項目7 のうち「何サンプル取れたか」。
ITEM_VALID_SAMPLES: str = "valid_samples"
#: 実測項目7 のうち「何サンプルで収束したか」。
ITEM_CONVERGED_AT: str = "converged_at"

#: 分布を作る項目キーの並び（出力の順序でもある）。
ITEM_KEYS: tuple[str, ...] = (
    ITEM_TOTAL_FLIGHT_MS,
    ITEM_RELEASE_TO_DETECT_MS,
    ITEM_DETECT_TO_FIRST_PREDICTION_MS,
    ITEM_HIT_ERROR_FIRST_MM,
    ITEM_HIT_ERROR_FINAL_MM,
    ITEM_TIME_ERROR_FIRST_MS,
    ITEM_TIME_ERROR_FINAL_MS,
    ITEM_AIM_ERROR_MM,
    ITEM_VALID_SAMPLES,
    ITEM_CONVERGED_AT,
)

#: `docs/requirements.md §8 M1` の実測7項目から項目キーへの対応。
#:
#: 項目4 / 5 / 7 が2つの列を持つのは、**1投擲の値が1つに決まらない**ためで
#: ある。誤差は予測が更新されるたびの系列（要件 5.4 / 5.5）なので、投擲群へ
#: 束ねるには系列のどこを代表に採るかを決めなければならない。初回予測と
#: 最終予測の両方を残すのは、**両者の差が「サンプルを増やして良くなったか」
#: そのもの**であり、片方に潰すとその問いに答えられなくなるからである
#: （FR-1 の「3」の妥当性を見る材料でもある）。
MEASUREMENT_ITEM_KEYS: Mapping[int, tuple[str, ...]] = {
    1: (ITEM_TOTAL_FLIGHT_MS,),
    2: (ITEM_RELEASE_TO_DETECT_MS,),
    3: (ITEM_DETECT_TO_FIRST_PREDICTION_MS,),
    4: (ITEM_HIT_ERROR_FIRST_MM, ITEM_HIT_ERROR_FINAL_MM),
    5: (ITEM_TIME_ERROR_FIRST_MS, ITEM_TIME_ERROR_FINAL_MS),
    6: (ITEM_AIM_ERROR_MM,),
    7: (ITEM_VALID_SAMPLES, ITEM_CONVERGED_AT),
}

# ---------------------------------------------------------------------------
# 暫定の印の理由
# ---------------------------------------------------------------------------

#: 有効試行数が `TrialLimits.min_valid_throws` に満たない（要件 5.10）。
PROVISIONAL_INSUFFICIENT_VALID_THROWS: str = "insufficient_valid_throws"

#: `require_live_source` が有効なのに実機由来でない投擲を含む。
#:
#: `ConvergenceAnalyzer` が**投擲単位**で立てている暫定の印
#: （`require_live_source` かつ実機由来でない）と食い違わせないための理由で
#: ある。投擲が1つでも暫定なら、その値が入った集計も暫定である——同じ事実に
#: ついて2つの層が逆のことを言う状態を作らない。
PROVISIONAL_NON_LIVE_THROWS: str = "non_live_throws"


# ---------------------------------------------------------------------------
# 入力
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ThrowMetrics:
    """1投擲ぶんの実測結果一式（集計の入力）。

    Attributes:
        record: 投擲記録。キャリブレーション識別子・検証状態・入力元・
            セッション対応付け・失敗理由をここから読む。
        truth: 同じ投擲の真値。**落下地点の求め方**が
            `TruthMethod.MISSING` かどうかで有効試行かを決める。
        flight: 実測項目 1 / 2 / 6。
        accuracy: 実測項目 4 / 5。
        convergence: 実測項目 7。

    **design.md の擬似コードとの差**: 擬似コードの `aggregate()` は
    `Sequence[Mapping[str, object]]` を受け取る。ここでは型付きの束にした。
    素のマッピングにすると、**キーの綴りを間違えても静かに欠測になる**
    ——本モジュールが防ごうとしているのは「値が黙って集計から落ちること」
    そのものなので、入口でそれを許す形は採らない。
    """

    record: ThrowRecord
    truth: ThrowTruth
    flight: FlightResult
    accuracy: AccuracyResult
    convergence: ConvergenceResult


# ---------------------------------------------------------------------------
# 出力
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Distribution:
    """1項目の分布（要件 5.9: 代表値・ばらつき・試行数を併記する）。

    Attributes:
        count: 値が得られた試行数。
        median: 代表値（中央値）。値が1件も無ければ `None`。
        p95: 95 パーセンタイル。同上。
        iqr: 四分位範囲（p75 − p25）。**ばらつき**であり、同上。
        minimum: 最小。同上。
        maximum: 最大。同上。
        missing: 欠測した試行数。**0 で埋めた値ではない。**

    Invariants:
        `count + missing` は、その群の `throw_count` に等しい。項目ごとに
        分母が動くと欠測の多さが読めなくなるため、分母を1つに固定してある。

    平均を持たないのは意図である。投擲はばらつきが大きく外れ値が出やすい
    （要件の A-6「単発の投擲では判断しない」）ので、代表値には中央値を採る。
    """

    count: int
    median: float | None
    p95: float | None
    iqr: float | None
    minimum: float | None
    maximum: float | None
    missing: int


@dataclass(frozen=True, slots=True)
class ThrowRow:
    """集計に入った1投擲ぶんの行（`ThrowAggregate.per_throw` の要素）。

    Attributes:
        record_id: 投擲の識別子。
        session_id: セッション記録の識別子（`extra["sensing"]`）。対応付けが
            無ければ `None`。
        source: 入力元（`live` / `recorded` / `simulated`）。
        live: 実機由来か。`source` から導いた値であり、判断側が文字列比較を
            書き散らさずに済むように置いてある。
        truth_available: 落下地点の真値が記入されているか。偽なら
            **有効試行として数えない**（`valid_throw_count`）。
        error_vector_mm: 最終予測の落下地点誤差（`予測 − 実測` の水平
            2成分）。作れなければ `None`。
        values: 項目キーから値へのマッピング。**分布はこの行から組む**ので、
            分布と行が食い違うことはない。欠測は `None`。

    **design.md の擬似コードとの差**: 擬似コードの `per_throw` は
    `tuple[Mapping[str, object], ...]` である。型付きの行にしたのは
    `ThrowMetrics` と同じ理由による。
    """

    record_id: str
    session_id: str | None
    source: str
    live: bool
    truth_available: bool
    error_vector_mm: tuple[float, float] | None
    values: Mapping[str, float | None]

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", dict(self.values))


@dataclass(frozen=True, slots=True)
class ThrowAggregate:
    """1つのキャリブレーション（識別子 × 検証状態）に属する投擲群の集計。

    Attributes:
        calibration_id: キャリブレーション結果の識別子。**別の識別子の投擲を
            この集計へ混ぜない**（要件 2.5）。
        verified: 当該キャリブレーションが検証を通過しているか。
            **検証済みと未検証を同じ集計に混ぜない**ため、識別子と対で群の
            鍵になっている。偽なら、この集計から誤差の帰属はできない
            （要件 2.2）。
        session_ids: 含まれる投擲のセッション識別子（重複を除き辞書順）。
            対応付けの無い投擲は載らない。
        throw_count: 集計に入った投擲数（**失敗投擲を除いたあと**）。
            すべての分布の `count + missing` がこの値になる。
        failed_throw_count: 失敗として記録され、集計から除かれた投擲数
            （要件 3.8）。**除外は「捨てる」ことではない**ので数を残す。
        valid_throw_count: 有効試行数。`throw_count` のうち**落下地点の真値
            が記入されている**ものの数であり、暫定の印の判定に使う。
            真値の無い投擲は誤差を持てないので、試行数として数えない
            （`accuracy.py` の docstring が集計側へ求めている扱い）。
        live_throw_count: 実機由来の投擲数（要件 9.10 の材料）。
        converged_count: 収束した投擲数。
        not_converged_count: 未収束の投擲数。**正常な結果である。**
        not_measurable_count: 収束を測れなかった投擲数（有効な予測が0件。
            真値の欠測を含む）。**未収束として数えない**——混ぜると、真値を
            書き忘れた投擲が収束サンプル数の分布を悪い方へ引っ張る。
        single_prediction_throw_count: 有効な予測が1件だけだった投擲数。
            収束の規則上つねに「未収束」になるので、`not_converged_count`
            をそのまま「予測が落ち着かなかった投擲」と読まないための内数で
            ある。
        provisional: 暫定の印（要件 5.10）。**判断に用いてよい状態ではない。**
        provisional_reasons: 印が立った理由。複数立つ。空なら `provisional`
            は偽である。
        items: 項目キーから分布へのマッピング（`ITEM_KEYS` の全キーが必ず
            揃う。値が1件も無い項目は件数 0 の分布になる）。
        error_vectors: 帰属（タスク5.2）の入力となる誤差ベクトル群。
            **ベクトルのまま**であり、順序は `per_throw` の並びに従う。
        per_throw: 投擲ごとの行。分布の出どころであり、後段が再計算できる。

    **`verified` を `provisional` に畳んでいないのは意図である。** 検証状態と
    試行数は別の軸であり、直し方も違う（前者はキャリブレーションをやり直す、
    後者は投げる回数を増やす）。1つのフラグにまとめると「未検証だが試行数は
    十分」と「検証済みだが試行数不足」が同じ見え方になり、どちらなのか
    区別できなくなる。
    """

    calibration_id: str
    verified: bool
    session_ids: tuple[str, ...]
    throw_count: int
    failed_throw_count: int
    valid_throw_count: int
    live_throw_count: int
    converged_count: int
    not_converged_count: int
    not_measurable_count: int
    single_prediction_throw_count: int
    provisional: bool
    provisional_reasons: tuple[str, ...]
    items: Mapping[str, Distribution]
    error_vectors: tuple[tuple[float, float], ...]
    per_throw: tuple[ThrowRow, ...]

    def __post_init__(self) -> None:
        # 集計は「その時点の記録からこう束ねた」という結果である。呼び出し側が
        # 使い回すマッピングをそのまま抱えると、レポートに出る分布が算出時の
        # ものと食い違い得る（`Judgement.evidence` と同じ方針）。
        object.__setattr__(self, "items", dict(self.items))


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def aggregate(
    results: Sequence[ThrowMetrics],
    *,
    settings: M1Settings,
    latency: LatencyResult | None = None,
) -> tuple[ThrowAggregate, ...]:
    """投擲群を**キャリブレーションごとに分けて**集計する（要件 2.5, 5.9, 5.10）。

    Args:
        results: 投擲ごとの実測結果一式。順序は保存され、群の内部では
            この順に `per_throw` が並ぶ。
        settings: 解決済みの設定。`trials`（試行数の下限と実機要求）だけを
            読む。
        latency: 実測項目3 の出所（`aggregate_latency()` の戻り値）。
            **投擲ごとの初回予測レイテンシは記録にもログにも1箇所ずつしか
            無く、本モジュールが再計算すると集計器を二重に持つことになる**
            ので、算出済みの結果を受け取る。省略すると当該項目は全件欠測に
            なる（**0 で埋めない**）。

    Returns:
        `(calibration_id, verified)` ごとの `ThrowAggregate`。並びは
        識別子の辞書順、同じ識別子なら検証済みが先である（同一入力に対して
        同一の出力を返すため。要件 12.4）。入力が空なら空のタプル。

    Raises:
        M1ConfigError: 投擲の識別子が重複する場合、記録と真値の識別子が
            違う場合、記録が本 Spec の拡張領域（`extra["m1"]`）を持たない
            場合、その形式版が既知でない場合、キャリブレーション識別子が
            空の場合、または検証状態が要約と最上位で食い違う場合。
            **どれも「どの群に入るのか決められない」記録**であり、黙って
            どこかへ入れると要件 2.5 が防ごうとしている混在そのものになる。

    試行数が下限に満たなくても**例外にせず集計を返し、暫定の印を付ける**
    （要件 5.10）。失敗投擲は `runner.successful_throws()` の規則で除いた
    うえで、除いた数を残す（要件 3.8）。
    """
    _require_unique_record_ids(results)
    first_prediction_ms = _first_prediction_index(latency)

    groups: dict[tuple[str, bool], list[ThrowMetrics]] = {}
    for metrics in results:
        _require_matching_record_id(metrics)
        key = _calibration_key(metrics.record)
        groups.setdefault(key, []).append(metrics)

    return tuple(
        _aggregate_group(
            calibration_id=calibration_id,
            verified=verified,
            members=groups[(calibration_id, verified)],
            settings=settings,
            first_prediction_ms=first_prediction_ms,
        )
        # 検証済みを先に置く（`not verified` の昇順）。
        for calibration_id, verified in sorted(
            groups, key=lambda key: (key[0], not key[1])
        )
    )


# ---------------------------------------------------------------------------
# 群ごとの集計
# ---------------------------------------------------------------------------


def _aggregate_group(
    *,
    calibration_id: str,
    verified: bool,
    members: Sequence[ThrowMetrics],
    settings: M1Settings,
    first_prediction_ms: Mapping[str, float | None],
) -> ThrowAggregate:
    """1つの群を集計する。**この関数の外へ群をまたぐ値は出ない。**"""
    kept_records = frozenset(
        record.record_id
        for record in successful_throws(metrics.record for metrics in members)
    )
    kept = [
        metrics for metrics in members if metrics.record.record_id in kept_records
    ]
    failed_throw_count = len(members) - len(kept)

    rows = tuple(
        _row(metrics, first_prediction_ms=first_prediction_ms) for metrics in kept
    )
    throw_count = len(rows)
    valid_throw_count = sum(1 for row in rows if row.truth_available)
    live_throw_count = sum(1 for row in rows if row.live)
    session_ids = tuple(
        sorted({row.session_id for row in rows if row.session_id is not None})
    )

    verdicts = [metrics.convergence.judgement.verdict for metrics in kept]
    error_vectors = tuple(
        row.error_vector_mm for row in rows if row.error_vector_mm is not None
    )

    reasons: list[str] = []
    if valid_throw_count < settings.trials.min_valid_throws:
        reasons.append(PROVISIONAL_INSUFFICIENT_VALID_THROWS)
    if settings.trials.require_live_source and live_throw_count < throw_count:
        reasons.append(PROVISIONAL_NON_LIVE_THROWS)

    return ThrowAggregate(
        calibration_id=calibration_id,
        verified=verified,
        session_ids=session_ids,
        throw_count=throw_count,
        failed_throw_count=failed_throw_count,
        valid_throw_count=valid_throw_count,
        live_throw_count=live_throw_count,
        converged_count=verdicts.count(VERDICT_CONVERGED),
        not_converged_count=verdicts.count(VERDICT_NOT_CONVERGED),
        not_measurable_count=verdicts.count(VERDICT_NOT_MEASURABLE),
        single_prediction_throw_count=sum(
            1 for metrics in kept if len(metrics.accuracy.errors) == 1
        ),
        provisional=bool(reasons),
        provisional_reasons=tuple(reasons),
        items={key: _distribution(rows, key) for key in ITEM_KEYS},
        error_vectors=error_vectors,
        per_throw=rows,
    )


def _row(
    metrics: ThrowMetrics, *, first_prediction_ms: Mapping[str, float | None]
) -> ThrowRow:
    """1投擲ぶんの行を組む。**分布はこの行からしか作らない。**"""
    record = metrics.record
    accuracy = metrics.accuracy
    flight = metrics.flight
    convergence = metrics.convergence

    first = accuracy.first_valid
    final = accuracy.final
    values: dict[str, float | None] = {
        ITEM_TOTAL_FLIGHT_MS: _finite(flight.total_flight_ms),
        ITEM_RELEASE_TO_DETECT_MS: _finite(flight.release_to_detect_ms),
        ITEM_DETECT_TO_FIRST_PREDICTION_MS: _finite(
            first_prediction_ms.get(record.record_id)
        ),
        ITEM_HIT_ERROR_FIRST_MM: _error_norm_mm(first),
        ITEM_HIT_ERROR_FINAL_MM: _error_norm_mm(final),
        ITEM_TIME_ERROR_FIRST_MS: _time_error_ms(first),
        ITEM_TIME_ERROR_FINAL_MS: _time_error_ms(final),
        ITEM_AIM_ERROR_MM: _finite(flight.aim_error_mm),
        ITEM_VALID_SAMPLES: float(convergence.valid_samples),
        ITEM_CONVERGED_AT: _optional_count(convergence.converged_at),
    }

    session_id = _session_id(record)
    return ThrowRow(
        record_id=record.record_id,
        session_id=session_id,
        source=str(record.source),
        live=record.source is SourceKind.LIVE,
        # 「有効な予測が0件」と「落下地点の真値が未記入」は `AccuracyResult`
        # の戻り値だけでは区別できない（`accuracy.py` の docstring）。
        # **真値の求め方を見て決める。**
        truth_available=(
            metrics.truth.impact_point_world_mm.method is not TruthMethod.MISSING
        ),
        error_vector_mm=_error_vector_mm(final),
        values=values,
    )


def _distribution(rows: Sequence[ThrowRow], key: str) -> Distribution:
    """行の並びから1項目の分布を組む。

    **`count + missing` は常に行数**である。欠測を分母から外すと、項目ごとに
    分母が動いて欠測の多さが読めなくなる。
    """
    values = sorted(
        value for value in (row.values[key] for row in rows) if value is not None
    )
    missing = len(rows) - len(values)
    if not values:
        return Distribution(
            count=0,
            median=None,
            p95=None,
            iqr=None,
            minimum=None,
            maximum=None,
            missing=missing,
        )
    return Distribution(
        count=len(values),
        median=_percentile(values, 0.5),
        p95=_percentile(values, 0.95),
        iqr=_percentile(values, 0.75) - _percentile(values, 0.25),
        minimum=values[0],
        maximum=values[-1],
        missing=missing,
    )


def _percentile(sorted_values: Sequence[float], fraction: float) -> float:
    """昇順の値から線形補間で分位点を求める。

    **上流の集計器および `latency.py` と同じ式**（`(n-1)*q` の線形補間）で
    ある。同じレポートに並ぶ数字の作り方を揃えるためであり、式が違うと
    「段階別レイテンシの p95」と「投擲群の p95」が別の意味の数になる。
    """
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    k = (n - 1) * fraction
    low = math.floor(k)
    high = math.ceil(k)
    if low == high:
        return sorted_values[int(k)]
    return sorted_values[low] * (high - k) + sorted_values[high] * (k - low)


# ---------------------------------------------------------------------------
# 記録から群の鍵と対応付けを読む
# ---------------------------------------------------------------------------


def _calibration_key(record: ThrowRecord) -> tuple[str, bool]:
    """群の鍵 `(calibration_id, verified)` を記録から読む。

    **識別子だけを鍵にしない。** 同じキャリブレーション結果でも、検証を
    通す前に投げた分と通したあとに投げた分は別の母集団である（要件 2.2:
    未検証で得た生成物には印が付き、誤差の帰属ができない）。
    """
    payload = _m1_payload(record)
    summary = payload.get("calibration")
    if not isinstance(summary, Mapping):
        raise M1ConfigError(
            f"投擲 {record.record_id!r} の extra['m1'] に "
            "キャリブレーション要約が無い。どの群に入れるか決められない記録を"
            "黙ってどこかへ入れると、要件 2.5 が防ごうとしている混在になる",
            {"record_id": record.record_id},
        )

    calibration_id = summary.get("calibration_id")
    if not isinstance(calibration_id, str) or not calibration_id.strip():
        raise M1ConfigError(
            f"投擲 {record.record_id!r} のキャリブレーション識別子が空である。"
            "識別子ごとに分けて集計できない",
            {"record_id": record.record_id, "calibration_id": calibration_id},
        )

    verified = summary.get("verified")
    top_level = payload.get("verified")
    if not isinstance(verified, bool) or not isinstance(top_level, bool):
        raise M1ConfigError(
            f"投擲 {record.record_id!r} の検証状態が真偽値でない。"
            "検証済みと未検証を分けて集計できない",
            {"record_id": record.record_id, "verified": verified},
        )
    if verified != top_level:
        # どちらが正しいのか決められない。検証済みの群へ入れれば未検証の
        # データを混ぜることになり、未検証の群へ入れれば検証済みのデータを
        # 帰属不能な集計へ落とす。どちらも黙ってやってよい選択ではない。
        raise M1ConfigError(
            f"投擲 {record.record_id!r} の検証状態が食い違っている: "
            f"要約 {verified!r} / 最上位 {top_level!r}。"
            "どちらの群へ入れても誤りになるため、記録を直すまで集計しない",
            {
                "record_id": record.record_id,
                "calibration_verified": verified,
                "extra_verified": top_level,
            },
        )
    return (calibration_id, verified)


def _m1_payload(record: ThrowRecord) -> Mapping[str, object]:
    """本 Spec の拡張領域を取り出し、形式版を照合する。

    形式版が未知なら**内容を推測して読まない**（要件 1.5 と同じ方針）。
    キーの意味が変わっていれば、読めた値のほうが危ない——静かに違う群へ
    入る。
    """
    payload = record.extra.get("m1")
    if not isinstance(payload, Mapping):
        raise M1ConfigError(
            f"投擲 {record.record_id!r} に本 Spec の拡張領域 extra['m1'] が無い。"
            "キャリブレーション識別子が分からない投擲を集計へ入れると、"
            "識別子ごとに分ける意味が失われる",
            {"record_id": record.record_id},
        )
    version = payload.get("m1_extra_version")
    if version != M1_EXTRA_VERSION:
        raise M1ConfigError(
            f"投擲 {record.record_id!r} の extra['m1'] の形式版が既知でない: "
            f"{version!r}（既知は {M1_EXTRA_VERSION!r}）。"
            "内容を推測して読まない",
            {
                "record_id": record.record_id,
                "m1_extra_version": version,
                "known_version": M1_EXTRA_VERSION,
            },
        )
    return payload


def _session_id(record: ThrowRecord) -> str | None:
    """セッション記録への対応付け（`extra["sensing"]`）。無ければ `None`。

    対応付けは上流（`sensing_foundation.link_to_session()`）が書く。無いこと
    は異常ではない（合成入力では対応付ける先が無い）ので、欠測として扱う。
    """
    payload = record.extra.get("sensing")
    if not isinstance(payload, Mapping):
        return None
    session_id = payload.get("session_id")
    return session_id if isinstance(session_id, str) and session_id else None


def _first_prediction_index(
    latency: LatencyResult | None,
) -> Mapping[str, float | None]:
    """実測項目3 を投擲の識別子で引ける形にする。"""
    if latency is None:
        return {}
    return {
        item.record_id: item.detect_to_first_prediction_ms
        for item in latency.detect_to_first_prediction
    }


# ---------------------------------------------------------------------------
# 小物
# ---------------------------------------------------------------------------


def _error_norm_mm(error: PredictionError | None) -> float | None:
    """落下地点誤差の大きさ（mm）。誤差が無ければ欠測。"""
    return None if error is None else _finite(error.hit_error_norm_mm)


def _time_error_ms(error: PredictionError | None) -> float | None:
    """落下時刻誤差（ms）。誤差そのものが無い場合と、落下時刻の真値だけが
    欠測な場合の**どちらでも**欠測になる（`0` で埋めない）。
    """
    return None if error is None else _finite(error.time_error_ms)


def _optional_count(value: int | None) -> float | None:
    """件数の真値を分布へ入る形へ写す。**未確定は `None` のまま。**

    収束サンプル数を `0` で埋めると「1サンプル目で収束した」より速い値として
    分布へ入り、未収束の投擲が収束の速さを良い方へ引っ張る。
    """
    return None if value is None else float(value)


def _error_vector_mm(error: PredictionError | None) -> tuple[float, float] | None:
    """誤差ベクトル（帰属の入力）。**スカラーに畳まない。**"""
    if error is None:
        return None
    x_mm, y_mm = error.hit_error_mm
    if not (math.isfinite(x_mm) and math.isfinite(y_mm)):
        return None
    return (x_mm, y_mm)


def _finite(value: float | None) -> float | None:
    """有限の数値ならそのまま、そうでなければ `None`（欠測）。

    記録は JSON を経由して復元されうる（`ThrowRecord.from_dict` は非有限値を
    拒否しない）。NaN をそのまま集計へ流すと、**「測れた値」として代表値と
    ばらつきを壊す**（design.md「Data Models」: NaN / Infinity は欠測）。
    """
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _require_unique_record_ids(results: Sequence[ThrowMetrics]) -> None:
    """識別子の重複を拒否する。

    同じ投擲を2度数えると、試行数も分布も静かに歪む。`latency.py` が同じ
    理由で同じ検査を持っている。
    """
    seen: set[str] = set()
    duplicated: list[str] = []
    for metrics in results:
        record_id = metrics.record.record_id
        if record_id in seen:
            duplicated.append(record_id)
        seen.add(record_id)
    if duplicated:
        raise M1ConfigError(
            f"集計対象に同じ識別子の投擲が複数ある: {sorted(set(duplicated))}。"
            "同じ投擲を2度数えると試行数も分布も歪む",
            {"duplicated_record_ids": sorted(set(duplicated))},
        )


def _require_matching_record_id(metrics: ThrowMetrics) -> None:
    """記録と真値の取り違えを拒否する。

    `measure_flight()` / `measure_accuracy()` は同じ検査を持つが、集計は
    算出済みの結果を受け取るので**ここを通らずに束ねられてしまう**。
    取り違えたまま束ねると、別の投擲の誤差が本投擲の実測値として集計へ入る。
    """
    if metrics.record.record_id != metrics.truth.record_id:
        raise M1ConfigError(
            "記録と真値の record_id が違う: "
            f"{metrics.record.record_id!r} ≠ {metrics.truth.record_id!r}。"
            "取り違えた真値の投擲を束ねると、別の投擲の誤差が本投擲の実測値"
            "として集計へ入る",
            {
                "record_id": metrics.record.record_id,
                "truth_record_id": metrics.truth.record_id,
            },
        )
