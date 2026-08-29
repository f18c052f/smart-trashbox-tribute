"""実測項目 1 / 2 / 6（総飛行時間・リリース〜検出開始・狙い誤差）の算出。

design.md「Components and Interfaces / L4-L5: 真値と実測 / FlightMetrics」、
tasks.md タスク 4.2、要件 5.1, 5.2, 5.6（および欠測の扱いとして 4.4 / 4.6）。

3つとも**差または距離**であり、算出そのものは短い。本モジュールの中身は
むしろ「何から何を引くか」「欠測をどう伝えるか」「不確かさをどう運ぶか」の
3点にある。

**項目2 はプロジェクトで最も未検証な量である。** `docs/requirements.md §3`
の時間予算表のうち区間1（リリース〜検出開始）だけが**完全に未検証**であり、
ここが想定より長ければ移動体の持ち時間が直接削られ、プロジェクトの成立性
そのものが変わる（要件の冒頭「誰の問題か」）。したがって本モジュールは
値を返すだけでなく、**その旨の強調文を結果に載せる**（`FlightResult.emphasis`）
——レポート側が強調を付け忘れられない形にするためである。

**不確かさは真値が申告したものだけを運ぶ。** 新しい誤差要因をここで足さない。
`truth.py` は各真値の `source` に**何を含めていないか**を明記している
（内挿: 床面高さ自体のずれ・対象物の寸法／外挿: 放物運動モデルの誤り・
リリース高さの測り方の誤差）。それらをここで足すと、真値側の申告と実測側の
申告が食い違い、**どちらが正しいのか誰にも分からなくなる**。含めていない
ものは、含めていないまま `source` の記述とともに読む。

**欠測は例外ではなく値である**（design.md「Error Categories and Responses」:
真値の欠測 → 値として扱う。当該項目のみ欠測、他項目は継続）。リリース時刻が
欠測なら項目1・2 は `None` になるが、**項目6 の算出は止まらない**。逆に
落下地点が未記入でも項目1・2 は出る。**0 で埋めない**——「0 だった」と
「測っていない」は別である。

本モジュールは L5 層の評価側であり、`sensing_foundation` /
`flying_object_tracking` / `world_frame_calibration` を import しない
（design.md「Allowed Dependencies」。評価側は**記録された値だけ**を読む）。
数値計算は標準ライブラリだけで書く。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from m1_validation.errors import M1ConfigError
from m1_validation.layout import ThrowLayout
from m1_validation.types import ThrowTruth, TruthMethod, TruthValue
from prediction_core import Sample, ThrowRecord

#: `FlightResult.methods` のキー。**実測項目ではなく真値の名前**で引く
#: （`ThrowTruth` のフィールド名と一致させてある）。項目1 は2つの真値に
#: 依存するので、実測項目ごとの1対1にすると求め方が片方へ潰れる。
IMPACT_POINT_KEY: str = "impact_point_world_mm"
IMPACT_TIME_KEY: str = "impact_time_ms"
RELEASE_TIME_KEY: str = "release_time_ms"

#: `FlightResult.emphasis` のキー（実測項目2）。
RELEASE_TO_DETECT_KEY: str = "release_to_detect_ms"

#: 実測項目2 に必ず添える強調文（tasks.md 4.2「レポート向けに強調できる形」）。
RELEASE_TO_DETECT_EMPHASIS: str = (
    "実測項目2（リリース〜検出開始）は docs/requirements.md §3 の区間1 に当たり、"
    "時間予算表のなかで唯一まったく未検証の量である——プロジェクトで最も未検証な"
    "量であり、想定より長ければ移動体の持ち時間が直接削られ、プロジェクトの"
    "成立性そのものが変わる。値はリリース時刻の外挿に全面的に依存するので、"
    "求め方（外挿）と不確かさを必ず併記して読むこと。"
)

@dataclass(frozen=True, slots=True)
class FlightResult:
    """1投擲ぶんの実測項目 1 / 2 / 6（要件 5.1, 5.2, 5.6）。

    Attributes:
        total_flight_ms: 項目1。実際の落下時刻 − リリース時刻。どちらかが
            欠測なら `None`。
        total_flight_uncertainty_ms: 項目1 に伝播した不確かさ（ms）。
        release_to_detect_ms: 項目2。最初の有効サンプルの観測時刻 −
            リリース時刻。**プロジェクトで最も未検証な量**（`emphasis` 参照）。
        release_to_detect_uncertainty_ms: 項目2 に伝播した不確かさ（ms）。
        aim_error_mm: 項目6。待機位置 → 実際の落下地点の**水平**距離。
        aim_error_uncertainty_mm: 項目6 に伝播した不確かさ（mm）。
        methods: 真値ごとの求め方（要件 4.4）。キーは `IMPACT_POINT_KEY` /
            `IMPACT_TIME_KEY` / `RELEASE_TIME_KEY`。欠測は
            `TruthMethod.MISSING` として載る。
        emphasis: 実測項目のキーから強調文へのマッピング。**値が欠測でも
            消えない**——測れなかったこと自体が報告に値するからである。

    **design.md の擬似コードとの差**: design.md は不確かさを
    `uncertainty_ms: float | None`（項目1 / 2 に伝播した不確かさ）という
    1つのフィールドで書いているが、ここでは項目ごとに分けている。
    **項目1 と項目2 では伝播の式が違う**からである。項目1 は落下時刻
    （内挿）とリリース時刻（外挿）の差なので両方の不確かさが効くのに対し、
    項目2 の検出開始は**観測された時刻そのもの**であり推定値ではないので、
    リリース時刻の不確かさだけが効く。1つに畳むと、項目1 を過小に申告するか、
    **項目2 を過大に申告する**かのどちらかになる。項目2 はプロジェクトで
    最も未検証な量であり、その不確かさを実際より大きく見せることは区間1 の
    実測値を読めなくするのと同じなので、ここは分ける。
    """

    total_flight_ms: float | None
    total_flight_uncertainty_ms: float | None
    release_to_detect_ms: float | None
    release_to_detect_uncertainty_ms: float | None
    aim_error_mm: float | None
    aim_error_uncertainty_mm: float | None
    methods: Mapping[str, TruthMethod]
    emphasis: Mapping[str, str]

    def __post_init__(self) -> None:
        # 実測値は「その時点の真値でそう測った」という記録である。呼び出し側が
        # 使い回すマッピングをそのまま抱えると、レポートに出る求め方や強調文が
        # 算出時のものと食い違い得る（`Judgement.evidence` と同じ方針）。
        object.__setattr__(self, "methods", dict(self.methods))
        object.__setattr__(self, "emphasis", dict(self.emphasis))


def measure_flight(
    record: ThrowRecord, truth: ThrowTruth, *, layout: ThrowLayout
) -> FlightResult:
    """実測項目 1 / 2 / 6 を算出する（要件 5.1, 5.2, 5.6）。

    Args:
        record: 対象の投擲記録。**項目2 の「検出開始」**をここから採る
            （最初の有効サンプルの観測時刻）。
        truth: 同じ投擲の真値（`derive_truth()` の戻り値）。
        layout: 投擲レイアウト。`standby_position_world_mm` が
            **狙い誤差の基準**である（要件 5.6）。

    Returns:
        `FlightResult`。求まらなかった項目は `None` であり、**0 で埋めない**。
        求め方の種別と強調文は、値が欠測でも必ず載る。

    Raises:
        M1ConfigError: 記録と真値の `record_id` が違う場合。**取り違えた
            真値で測ると、別の投擲の誤差を本投擲の実測値として報告する
            ことになる**（`truth.attach_truth()` と同じ理由で拒否する）。
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

    release_ms = _finite_time(truth.release_time_ms)
    impact_ms = _finite_time(truth.impact_time_ms)
    detect_ms = _first_valid_sample_time_ms(record.samples)

    # --- 項目1: 総飛行時間（要件 5.1）
    total_flight_ms: float | None = None
    total_flight_uncertainty_ms: float | None = None
    if release_ms is not None and impact_ms is not None:
        total_flight_ms = impact_ms - release_ms
        total_flight_uncertainty_ms = _combined_uncertainty_ms(
            truth.impact_time_ms, truth.release_time_ms
        )

    # --- 項目2: リリース〜検出開始（要件 5.2）★最も未検証な量
    release_to_detect_ms: float | None = None
    release_to_detect_uncertainty_ms: float | None = None
    if release_ms is not None and detect_ms is not None:
        release_to_detect_ms = detect_ms - release_ms
        # 検出開始は観測された時刻そのものであり推定値ではない。落下時刻の
        # 不確かさをここへ足すと**項目2 を水増しする**（`FlightResult` の
        # docstring 参照）。
        release_to_detect_uncertainty_ms = truth.release_time_ms.uncertainty_ms

    # --- 項目6: 狙い誤差（要件 5.6）
    aim_error_mm, aim_error_uncertainty_mm = _aim_error(
        truth.impact_point_world_mm, layout=layout
    )

    return FlightResult(
        total_flight_ms=total_flight_ms,
        total_flight_uncertainty_ms=total_flight_uncertainty_ms,
        release_to_detect_ms=release_to_detect_ms,
        release_to_detect_uncertainty_ms=release_to_detect_uncertainty_ms,
        aim_error_mm=aim_error_mm,
        aim_error_uncertainty_mm=aim_error_uncertainty_mm,
        methods={
            IMPACT_POINT_KEY: truth.impact_point_world_mm.method,
            IMPACT_TIME_KEY: truth.impact_time_ms.method,
            RELEASE_TIME_KEY: truth.release_time_ms.method,
        },
        emphasis={RELEASE_TO_DETECT_KEY: RELEASE_TO_DETECT_EMPHASIS},
    )


def _finite_time(value: TruthValue) -> float | None:
    """時刻の真値を取り出す。欠測・非有限・型違いなら `None`。

    非有限値を欠測として扱うのは、本 Spec と上流・`prediction_core` に共通の
    方針である（design.md「Data Models」: NaN / Infinity は欠測として表す）。
    NaN のまま差を取ると、**NaN が「測れた値」として集計へ流れ込む**。
    """
    if value.method is TruthMethod.MISSING:
        return None
    if not isinstance(value.value, float | int) or isinstance(value.value, bool):
        return None
    number = float(value.value)
    return number if math.isfinite(number) else None


def _first_valid_sample_time_ms(samples: Sequence[Sample]) -> float | None:
    """最初の有効サンプルの観測時刻（ms）。1件も無ければ `None`。

    **最初**であって最後ではない（要件 5.2。区間1 は「投げてから見え始める
    まで」であり、見えなくなるまでの時間ではない）。継ぎ目が既に無効な点を
    除外しているので `record.samples` は有効なサンプルの列だが、観測時刻が
    非有限な要素だけはここでも読み飛ばす——欠測を値として扱う方針
    （`_finite_time()` 参照）を、真値と観測で食い違わせないためである。
    """
    for sample in samples:
        if math.isfinite(sample.t_ms):
            return sample.t_ms
    return None


def _combined_uncertainty_ms(*values: TruthValue) -> float | None:
    """時刻の真値の不確かさを合成する（項目1 用）。

    **二乗和ではなく単純和**（上界）を採る。2つの不確かさは独立ではない
    ——`truth.py` は落下時刻（内挿）とリリース時刻（外挿）の不確かさを
    **どちらも同じ最終予測の残差**から導いており、共通の成分を持つ。
    独立を仮定した二乗和はこの場合に過小評価になり、**総飛行時間を実際より
    確からしく見せる**。ここは時間予算表の更新に直接入る量なので、
    確からしさを盛る側の誤りを避ける。

    1つでも不確かさが申告されていなければ `None` を返す。**足りない項を 0 と
    みなして合成しない**——「不確かさが 0」と「不確かさが分からない」は別で
    あり、前者として報告すると誤差の意味を読み違える。
    """
    total = 0.0
    for value in values:
        if value.uncertainty_ms is None or not math.isfinite(value.uncertainty_ms):
            return None
        total += abs(value.uncertainty_ms)
    return total


def _aim_error(
    impact_point: TruthValue, *, layout: ThrowLayout
) -> tuple[float | None, float | None]:
    """狙い誤差（項目6。要件 5.6）と、それに伝播した不確かさ。

    待機位置から実際の落下地点までの**水平距離**である。高さを混ぜない
    ——狙い誤差は移動体に要求される横移動量であり、落下地点の高さ成分
    （対象物の中心高さ・床面の凹凸）は移動量ではないからである。

    不確かさは落下地点の実測の不確かさをそのまま伝播させる。**レイアウトの
    待機位置の測り方の誤差は含まない**——`ThrowLayout` は不確かさを持たない
    （待機位置は設計値であって実測値ではない）。含んでいない量をここで
    でっち上げないのは、真値側の申告と食い違わせないためである
    （モジュール docstring 参照）。
    """
    if impact_point.method is TruthMethod.MISSING:
        return (None, None)
    point = impact_point.value
    if not isinstance(point, tuple):
        return (None, None)

    standby = layout.standby_position_world_mm
    dx_mm = point[0] - standby[0]
    dy_mm = point[1] - standby[1]
    if not (math.isfinite(dx_mm) and math.isfinite(dy_mm)):
        return (None, None)

    uncertainty_mm = impact_point.uncertainty_mm
    if uncertainty_mm is not None and not math.isfinite(uncertainty_mm):
        uncertainty_mm = None
    return (math.hypot(dx_mm, dy_mm), uncertainty_mm)
