"""レポート——人が読む要約と、機械可読の出力。

design.md「Components and Interfaces / L8: 出力 / Reporter」、
「Data Models / 集計・判断の出力（`report-<session>.json`）」、tasks.md タスク
7.1、要件 2.2, 5.11, 6.9, 9.4, 10.4, 11.2。

**本モジュールは何も算出せず、何も判定しない。** 既に出た値（集計・帰属・
OQ-27・OQ-05・時間予算表の更新値・計測 ON/OFF 比較）を束ねて表示するだけの層
である（`tech.md` 開発標準3「表示レイヤにアルゴリズムを持たせない」）。数値を
ここで作り直すと、レポートに出た値と算出側の値が食い違う経路が生まれる。

==============================================================================
なぜ「判定規則の説明文」を要約に必ず入れるのか
==============================================================================

本 Spec が出すのは「Pi 4 では不足」「誤差はキャリブレーション由来」といった
**後戻りしにくい判断**である。数値だけが転記されて根拠が消えた状態は、
`structure.md` が最悪と呼ぶ状態であり、あとから規則のほうを結果に合わせて
読み替えられてしまう（要件 9.1）。

そこで本モジュールは、**全判定（帰属・OQ-27・OQ-05・時間予算・計測 ON/OFF）
の `criterion` を、要約にも機械可読出力にも全文のまま載せる**。要約するのは
数値であって規則ではない。

==============================================================================
なぜ「証跡のキー集合と値の型」をここで固定するのか
==============================================================================

`Judgement` を JSON へ写すのは本モジュールだけである（タスク 6.2 の申し送り）。
判断モジュール側は `evidence` のキー集合も値の型も固定していないので、写し方が
各所へ散らばると、**同じ判断が出力ごとに違う形で現れる**。`JUDGEMENT_KEYS` と
`judgement_to_dict()` が唯一の写し方であり、値は JSON が表せる型
（`str` / `bool` / `int` / 有限の `float` / `None` / 配列 / オブジェクト）へ
正規化される。

==============================================================================
なぜ NaN と無限大を欠測にするのか
==============================================================================

`json.dumps(..., allow_nan=False)` は `NaN` / `Infinity` で例外を送出する
（design.md「集計・判断の出力」）。素通しすると出力そのものが作れず、
`allow_nan=True` にすると JSON として壊れた語が残って読み手が数値と誤認する。
**0 で埋めるのはさらに悪い**——「測って 0 だった」と読まれる。したがって
`None`（欠測）へ倒す。これは本 Spec が一貫して採っている「欠測は `None`」の
規律そのものである。

==============================================================================
境界
==============================================================================

本モジュールは評価側（L8）であり、上流3パッケージ
（`sensing_foundation` / `flying_object_tracking` / `world_frame_calibration`）
を import しない（design.md「Allowed Dependencies」）。描画ライブラリ
（`matplotlib`）も import しない——図はタスク 7.2 の `plot.py` が唯一の
持ち主である（要件 8.8）。数値計算は標準ライブラリの `math` だけで足りる。
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from m1_validation.attribution import AttributionResult
from m1_validation.bench import OverheadReport
from m1_validation.bench import report_to_dict as overhead_report_to_dict
from m1_validation.config import PROVISIONAL_NOTICE
from m1_validation.errors import M1ConfigError
from m1_validation.judgement.budget import BudgetRow, BudgetUpdate
from m1_validation.judgement.oq05 import Oq05Result
from m1_validation.judgement.oq27 import Oq27Result
from m1_validation.metrics.aggregate import (
    MEASUREMENT_ITEM_KEYS,
    Distribution,
    ThrowAggregate,
)
from m1_validation.types import Judgement

__all__ = [
    "ASSUMED_ITEM_4",
    "ASSUMED_ITEM_5",
    "ASSUMED_ITEM_6",
    "ASSUMED_ITEM_7",
    "ASSUMED_SOURCES",
    "ATTRIBUTION_READING_NOTES",
    "COMPONENT_BREAKDOWN_NOTE",
    "JUDGEMENT_KEYS",
    "MEASUREMENT_ITEM_LABELS",
    "NOTE_ITEM_5",
    "NOTE_ITEM_7",
    "RANGE_BAND_READING_NOTE",
    "REPORT_VERSION",
    "SCATTER_WITH_BIAS_NOTE",
    "UNIT_NOTE_METERS_VS_MM",
    "UNIT_NOTE_SECONDS_VS_MS",
    "UNVERIFIED_WARNING",
    "M1Report",
    "MeasurementColumn",
    "MeasurementRow",
    "build_report",
    "judgement_to_dict",
    "provisional_warning",
    "render_summary",
    "report_output_path",
    "report_to_dict",
    "write_report",
]

#: 機械可読出力の形の版。**この値の変更は読み手（人・後続 Spec）の再確認を
#: 要求する**（`types.M1_EXTRA_VERSION` と同じ扱い）。
REPORT_VERSION: str = "1.0"


# ---------------------------------------------------------------------------
# 警告（**真のときだけ出す**）
# ---------------------------------------------------------------------------

#: 未検証キャリブレーションで得たデータが含まれるときの警告（要件 2.2）。
#:
#: **常に出してはならない。** 検証を通したデータにまで「帰属できない」と書くと、
#: 検証を通した意味が消え、警告そのものが読み飛ばされるようになる。
UNVERIFIED_WARNING: str = (
    "【警告】未検証のキャリブレーションで得た投擲が含まれている。"
    "この集計から誤差の帰属はできない（要件 2.2）——"
    "座標系が数 cm ずれていても症状は「予測が悪い」にしか見えないため、"
    "検証を通していない誤差は系統誤差と予測誤差に分離できない。"
    "キャリブレーション検証を通したうえで測り直すこと。"
)

_PROVISIONAL_WARNING_TEMPLATE: str = (
    "【警告】集計に暫定の印が付いている。判断に用いてよい状態ではない"
    "（要件 5.10 / 9.9）。理由: {reasons}。"
)


def provisional_warning(reasons: Sequence[str]) -> str:
    """暫定の印が立っているときの警告文（要件 5.10）。

    検証状態とは**別の軸**である（`ThrowAggregate` の docstring）。1つの警告に
    まとめると「未検証だが試行数は十分」と「検証済みだが試行数不足」が同じ
    見え方になり、次にやること（測り直す／投げる回数を増やす）が読めなくなる。
    """
    listed = "、".join(reasons) if reasons else "（理由の記録なし）"
    return _PROVISIONAL_WARNING_TEMPLATE.format(reasons=listed)


# ---------------------------------------------------------------------------
# 実測7項目（`docs/requirements.md §8 M1` の表の写し）
# ---------------------------------------------------------------------------

#: 実測項目の名称。**`docs/requirements.md §8 M1` の「実測項目」列の写し**で
#: あり、本 Spec が言い換えない（要件 5.11 は「対応する想定値と並べて提示する」
#: ことを求めており、対応関係が読めなくなると並置の意味が失われる）。
MEASUREMENT_ITEM_LABELS: Mapping[int, str] = {
    1: "総飛行時間",
    2: "リリース〜検出開始までの時間",
    3: "検出開始〜予測確定までの時間",
    4: "予測落下地点と実際の落下地点の誤差",
    5: "落下時刻の予測誤差",
    6: "狙い誤差（必要横移動量）",
    7: "何サンプル取れたか / 何サンプルで誤差が収束するか",
}

#: 想定値の出どころ。**7つとも別の場所を指す。** どの文書のどこと並べたのかが
#: 読めないと、食い違ったときに何を疑えばよいか決まらない。
ASSUMED_SOURCES: Mapping[int, str] = {
    1: "docs/requirements.md §3 本文（総飛行時間の想定）",
    2: "docs/requirements.md §3 時間予算表 区間1（まったく未検証の区間）",
    3: "docs/requirements.md §3 時間予算表 区間2 / NFR-3",
    4: "docs/requirements.md NFR-5（位置精度・暫定目標）",
    5: "docs/requirements.md NFR-6（到達時の静定）",
    6: "docs/requirements.md §3 本文（横方向の狙い誤差の想定）",
    7: "docs/requirements.md FR-1（最低3サンプルとその根拠）",
}

#: 項目4 の想定値（NFR-5 の本文の写し）。**数値を写さない**のは、窓の値が
#: 対象物の寸法と開口寸法から導かれる量であり、レイアウトによって変わるため
#: である。実際に使った窓の値は `Oq05Result.window_mm` から運ぶ。
ASSUMED_ITEM_4: str = (
    "落下時刻における水平位置誤差 < 開口半径 − ゴミの代表寸法/2"
    "（NFR-5。暫定目標であって合否条件ではない）"
)

#: 項目5 の想定値。**無い想定値をでっち上げない。**
ASSUMED_ITEM_5: str = (
    "並べられる想定値は無い（NFR-6 は到達時の静定を求めるだけで、"
    "落下時刻の予測誤差そのものに許容量を置いていない）"
)

#: 項目6 の想定値（`docs/requirements.md §3` 本文の写し）。
ASSUMED_ITEM_6: str = "0.3〜0.8 m"

#: 項目7 の想定値（`docs/requirements.md` FR-1 の写し）。
ASSUMED_ITEM_7: str = "最低3サンプル"

#: 想定と実測で単位が違う項目に付ける注記。**桁がそのまま3つずれる。**
UNIT_NOTE_SECONDS_VS_MS: str = (
    "想定値は秒（s）で書かれており、実測値はミリ秒（ms）である。"
    "桁を揃えずにそのまま引き比べないこと。"
)

UNIT_NOTE_METERS_VS_MM: str = (
    "想定値はメートル（m）で書かれており、実測値はミリメートル（mm）である。"
    "桁を揃えずにそのまま引き比べないこと。"
)

NOTE_ITEM_5: str = (
    "NFR-6 の方針（停止して待つ / 通過キャッチを許容する）が未確定であり、"
    "落下時刻の予測誤差に対する想定値はどの文書にも無い。"
    "並べる相手が無いので、実測値だけを材料として残す"
    "——ここに仮の目標値を置くと、実測前の数値が合否条件として独り歩きする。"
)

NOTE_ITEM_7: str = (
    "FR-1 の「最低3サンプル」は理論上の下限であり、"
    "同項の根拠が「実用上は3点でも足りない可能性があり、"
    "必要サンプル数は M1 の実測で見直す」と断っている。"
    "本項目はその見直しのための実測である。"
)

_ITEM_4_WINDOW_NOTE_TEMPLATE: str = (
    "本レポートで用いた暫定許容窓は {window:g} mm である"
    "（投擲レイアウトの開口寸法と対象物寸法から導いた暫定値であり、"
    "合否条件ではない）。"
)


# ---------------------------------------------------------------------------
# 帰属の読み分け規則（**成分と一緒に出す**。要件 6.9 / 6.11）
# ---------------------------------------------------------------------------

COMPONENT_BREAKDOWN_NOTE: str = (
    "誤差を合計の単一値へ畳まず、共通の偏り成分とばらつき成分の内訳として"
    "読むこと（要件 6.9）。畳んだ時点で、どちらを直せばよいのかという情報が"
    "消える。"
)

SCATTER_WITH_BIAS_NOTE: str = (
    "ばらつき成分だけを読んで予測器を疑いに行かないこと。"
    "ばらつき側の語彙には「検出由来」が無く（要件 6.6 / 6.7 は"
    "観測ノイズ由来・モデル由来・判別不能の3値のみ）、"
    "カメラ視線方向の偏りは投擲位置ごとに World 上の向きが変わるため"
    "実際にばらつきも生む。それが「モデル由来」と名指しされるのは規則どおりの"
    "動作である。**偏り成分と併せて読むこと。**"
)

RANGE_BAND_READING_NOTE: str = (
    "距離帯ごとの誤差は、遠方の帯でだけ誤差が大きい場合を"
    "奥行き計測の距離特性として読み分けるためのものである（要件 6.11）。"
    "全帯で一様に大きい誤差を距離特性と読まないこと。"
)

#: 帰属の内訳に併記する読み分け規則。**3つとも別の誤読を防いでいる。**
ATTRIBUTION_READING_NOTES: tuple[str, ...] = (
    COMPONENT_BREAKDOWN_NOTE,
    SCATTER_WITH_BIAS_NOTE,
    RANGE_BAND_READING_NOTE,
)


# ---------------------------------------------------------------------------
# 判断の写し方（**唯一の固定点**。タスク 6.2 の申し送り）
# ---------------------------------------------------------------------------

#: `Judgement` を JSON へ写したときのキー集合。**ここが唯一の定義である。**
JUDGEMENT_KEYS: tuple[str, ...] = (
    "question",
    "criterion",
    "verdict",
    "rationale",
    "evidence",
    "provisional",
)


def _json_safe(value: object) -> object:
    """JSON が表せる型へ正規化する。**NaN と無限大は欠測（`None`）にする。**

    `bool` の判定を `int` より先に置いてあるのは、Python では `bool` が `int`
    の部分型であり、順序を逆にすると真偽値が `1` / `0` になって
    **「判定が真だった」と「1 件あった」が同じ見え方になる**からである。

    列挙（`StrEnum`）は `str` の部分型なので文字列として写る。それ以外の型
    （`Path` など）は文字列化する——JSON にできない値で出力全体が作れなく
    なるより、文字列で残るほうが読み手にとって有用である。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def judgement_to_dict(judgement: Judgement) -> dict[str, object]:
    """判断を JSON 化できる形へ写す。**キー集合と値の型はここで固定する。**

    判断モジュール側は `evidence` のキー集合も値の型も固定していない
    （タスク 6.2 の申し送り）。写し方が各所へ散らばると、同じ判断が出力ごとに
    違う形で現れる。本関数が唯一の写し方である。

    `criterion` は必ず**全文のまま**載る。要約もしないし、省略もしない
    ——数値だけが残って根拠が消える状態を避けるためである（要件 9.1）。
    """
    return {
        "question": str(judgement.question),
        "criterion": str(judgement.criterion),
        "verdict": str(judgement.verdict),
        "rationale": str(judgement.rationale),
        "evidence": {
            str(key): _json_safe(value) for key, value in judgement.evidence.items()
        },
        "provisional": bool(judgement.provisional),
    }


# ---------------------------------------------------------------------------
# 出力の形
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MeasurementColumn:
    """実測項目1つぶんの列（`ThrowAggregate.items` の1項目の写し）。

    Attributes:
        key: 項目キー。
        present: 集計に当該項目の行が**あったか**。偽なら以下はすべて `None`
            である。**「行が無い」と「行はあるが値が1件も無い」は別**であり、
            前者を件数 0 として写すと、集計に載っていない項目が
            「測ったが1件も取れなかった」ように見える（タスク 6.2 の教訓）。
        count: 値が得られた試行数。
        median: 代表値（中央値）。
        p95: 95 パーセンタイル。
        iqr: 四分位範囲（ばらつき）。
        minimum: 最小。
        maximum: 最大。
        missing: 欠測した試行数。

    Invariants:
        `median` / `p95` / `iqr` / `minimum` / `maximum` は有限値か `None` で
        ある。NaN と無限大は `None`（欠測）へ倒れる。
    """

    key: str
    present: bool
    count: int | None
    median: float | None
    p95: float | None
    iqr: float | None
    minimum: float | None
    maximum: float | None
    missing: int | None


@dataclass(frozen=True, slots=True)
class MeasurementRow:
    """実測7項目の1行。**想定値と実測値を並べる**（要件 5.11 / 11.2）。

    Attributes:
        item: `docs/requirements.md §8 M1` の項目番号（1〜7）。
        label: 同表の実測項目名。
        assumed: **対応する既存ドキュメントの想定値。** 区間1・区間2・総飛行
            時間は `BudgetUpdate` から運ぶ（再発明しない）。
        assumed_source: 想定値の出どころ。
        notes: 並べて読むときの注記（単位の違い・§3 由来の備考・読み替えなど）。
        columns: 実測の列。項目4 / 5 / 7 は**初回予測と最終予測の2列**を持つ
            ——1投擲の値が1つに決まらないためである
            （`MEASUREMENT_ITEM_KEYS` の注記）。

    **想定値と実測値を同じ列に入れない。** 入れた時点で、食い違ったこと自体が
    読めなくなる（要件 11.6 が「数値を想定へ合わせない」と定めているのと同じ
    理由である）。
    """

    item: int
    label: str
    assumed: str
    assumed_source: str
    notes: tuple[str, ...]
    columns: tuple[MeasurementColumn, ...]


@dataclass(frozen=True, slots=True)
class M1Report:
    """1つのキャリブレーション群についてのレポート。

    Attributes:
        report_version: 出力の形の版。
        session_id: セッション識別子（出力ファイル名にも入る）。
        calibration_id: 集計の対象となったキャリブレーション識別子。
        verified: そのキャリブレーションが検証を通過していたか。
        provisional: 集計に暫定の印が付いていたか。
        warnings: **真のときだけ立つ**警告。何も無ければ空である。
        measurements: 実測7項目（想定値との並置）。
        attribution: 誤差の帰属（成分ごとの内訳）。
        attribution_reading_notes: 内訳に併記する読み分け規則。
        oq27: OQ-27 の判定結果（改善適用履歴を含む）。
        oq05: OQ-05 の判断材料。
        budget: 時間予算表の更新値。
        overhead: 計測 ON/OFF 比較。実施していなければ `None`。
        judgements: **全判定**（帰属・OQ-27・OQ-05・時間予算・計測 ON/OFF）。
            順序は固定であり、`criterion` を全文のまま持つ。
        provisional_notice: 既定値が暫定の評価候補である旨（要件 13.7）。

    **本型は算出しない。** 与えられた結果を束ねて表示可能にするだけであり、
    ここで数値を作り直すと、レポートに出た値と算出側の値が食い違う。
    """

    report_version: str
    session_id: str
    calibration_id: str
    verified: bool
    provisional: bool
    warnings: tuple[str, ...]
    measurements: tuple[MeasurementRow, ...]
    attribution: AttributionResult
    attribution_reading_notes: tuple[str, ...]
    oq27: Oq27Result
    oq05: Oq05Result
    budget: BudgetUpdate
    overhead: OverheadReport | None
    judgements: tuple[Judgement, ...]
    provisional_notice: str


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def build_report(
    *,
    session_id: str,
    aggregate: ThrowAggregate,
    attribution: AttributionResult,
    oq27: Oq27Result,
    oq05: Oq05Result,
    budget: BudgetUpdate,
    overhead: OverheadReport | None = None,
) -> M1Report:
    """算出済みの結果を束ねてレポートを組み立てる。

    **1つのキャリブレーション群（識別子 × 検証状態）につき1つのレポート**を
    作る。集計は既に識別子ごとに分かれており（要件 2.5）、帰属・OQ-27・OQ-05・
    時間予算はいずれも1つの `ThrowAggregate` から出るためである。複数の
    キャリブレーションを扱う入口（タスク 8.1 の CLI）は、群ごとに本関数を
    呼ぶ。

    Args:
        session_id: セッション識別子。**空にできない**（出力ファイル名になる）。
        aggregate: 投擲群の集計。実測7項目の実測列と、検証状態・暫定の印を
            ここから読む。
        attribution: 誤差の帰属（成分ごとの内訳）。
        oq27: OQ-27 の判定結果。
        oq05: OQ-05 の判断材料。
        budget: 時間予算表の更新値。**想定値もここから運ぶ。**
        overhead: 計測 ON/OFF 比較。実施していなければ `None`。

    Returns:
        `M1Report`。

    Raises:
        M1ConfigError: `session_id` が空、または `budget` に区間1 / 区間2 の
            行が無い（呼び出し方の誤り）。
    """
    if not session_id.strip():
        raise M1ConfigError(
            "session_id を空にはできない: 出力ファイル名と対応付けの鍵になる",
            {"field": "session_id", "value": session_id},
        )
    warnings: list[str] = []
    if not aggregate.verified:
        warnings.append(UNVERIFIED_WARNING)
    if aggregate.provisional:
        warnings.append(provisional_warning(aggregate.provisional_reasons))
    judgements: list[Judgement] = [
        attribution.judgement,
        oq27.judgement,
        oq05.judgement,
        budget.judgement,
    ]
    if overhead is not None:
        judgements.append(overhead.judgement)
    return M1Report(
        report_version=REPORT_VERSION,
        session_id=session_id,
        calibration_id=aggregate.calibration_id,
        verified=aggregate.verified,
        provisional=aggregate.provisional,
        warnings=tuple(warnings),
        measurements=_measurement_rows(
            aggregate=aggregate, budget=budget, oq05=oq05
        ),
        attribution=attribution,
        attribution_reading_notes=ATTRIBUTION_READING_NOTES,
        oq27=oq27,
        oq05=oq05,
        budget=budget,
        overhead=overhead,
        judgements=tuple(judgements),
        provisional_notice=PROVISIONAL_NOTICE,
    )


def _measurement_rows(
    *, aggregate: ThrowAggregate, budget: BudgetUpdate, oq05: Oq05Result
) -> tuple[MeasurementRow, ...]:
    """実測7項目の行を組み立てる（要件 5.11）。

    **想定値のうち項目1 / 2 / 3 は `BudgetUpdate` から運ぶ。** 同じ想定値を
    2箇所に書くと、片方だけを直したときに2つの文書が別のことを言い始める
    （`research.md` Decision 7「集計器を二重に持たない」と同じ理由）。
    """
    segment1 = _budget_row(budget, "1")
    segment2 = _budget_row(budget, "2")
    window_note = _ITEM_4_WINDOW_NOTE_TEMPLATE.format(window=oq05.window_mm)
    specs: tuple[tuple[int, str, tuple[str, ...]], ...] = (
        (1, budget.total_flight_assumed, (UNIT_NOTE_SECONDS_VS_MS,)),
        (2, segment1.assumed, (segment1.note, UNIT_NOTE_SECONDS_VS_MS)),
        (3, segment2.assumed, (segment2.note, UNIT_NOTE_SECONDS_VS_MS)),
        (4, ASSUMED_ITEM_4, (window_note,)),
        (5, ASSUMED_ITEM_5, (NOTE_ITEM_5,)),
        (6, ASSUMED_ITEM_6, (UNIT_NOTE_METERS_VS_MM,)),
        (7, ASSUMED_ITEM_7, (NOTE_ITEM_7,)),
    )
    return tuple(
        MeasurementRow(
            item=item,
            label=MEASUREMENT_ITEM_LABELS[item],
            assumed=assumed,
            assumed_source=ASSUMED_SOURCES[item],
            notes=notes,
            columns=tuple(
                _column(key, aggregate.items.get(key))
                for key in MEASUREMENT_ITEM_KEYS[item]
            ),
        )
        for item, assumed, notes in specs
    )


def _budget_row(budget: BudgetUpdate, segment: str) -> BudgetRow:
    """時間予算表の行を区間識別子で引く。

    見つからないのは呼び出し方の誤りである——`compute_budget_update()` は
    ゲートの開閉によらず全区間の行を返す（要件 11.1 の「行と想定値は残る」）。
    """
    for row in budget.rows:
        if row.segment == segment:
            return row
    raise M1ConfigError(
        f"時間予算表に区間 {segment} の行が無い: 想定値を運べない",
        {"segment": segment, "segments": [row.segment for row in budget.rows]},
    )


def _column(key: str, distribution: Distribution | None) -> MeasurementColumn:
    """1項目の分布を列へ写す。**行が無い項目を 0 で埋めない。**"""
    if distribution is None:
        return MeasurementColumn(
            key=key,
            present=False,
            count=None,
            median=None,
            p95=None,
            iqr=None,
            minimum=None,
            maximum=None,
            missing=None,
        )
    return MeasurementColumn(
        key=key,
        present=True,
        count=distribution.count,
        median=_finite(distribution.median),
        p95=_finite(distribution.p95),
        iqr=_finite(distribution.iqr),
        minimum=_finite(distribution.minimum),
        maximum=_finite(distribution.maximum),
        missing=distribution.missing,
    )


def _finite(value: float | None) -> float | None:
    """NaN と無限大を欠測（`None`）へ倒す。**0 で埋めない。**"""
    if value is None:
        return None
    return value if math.isfinite(value) else None


# ---------------------------------------------------------------------------
# 機械可読の出力
# ---------------------------------------------------------------------------


def report_to_dict(report: M1Report) -> dict[str, object]:
    """レポートを JSON 化できる形へ写す（design.md「集計・判断の出力」）。

    **全判定の `criterion` が `judgements` に全文のまま入る。** 数値だけを
    書き出すと、規則を見直したときに何を根拠にそう判定したのかを再構成できない。
    """
    return {
        "report_version": report.report_version,
        "session_id": report.session_id,
        "calibration_id": report.calibration_id,
        "verified": report.verified,
        "provisional": report.provisional,
        "warnings": list(report.warnings),
        "measurements": [
            {
                "item": row.item,
                "label": row.label,
                "assumed": row.assumed,
                "assumed_source": row.assumed_source,
                "notes": list(row.notes),
                "columns": [
                    {
                        "key": column.key,
                        "present": column.present,
                        "count": column.count,
                        "median": column.median,
                        "p95": column.p95,
                        "iqr": column.iqr,
                        "minimum": column.minimum,
                        "maximum": column.maximum,
                        "missing": column.missing,
                    }
                    for column in row.columns
                ],
            }
            for row in report.measurements
        ],
        "attribution": _attribution_to_dict(report),
        "oq27": _oq27_to_dict(report.oq27),
        "oq05": _oq05_to_dict(report.oq05),
        "budget": _budget_to_dict(report.budget),
        "overhead": (
            None
            if report.overhead is None
            else _json_safe(overhead_report_to_dict(report.overhead))
        ),
        "judgements": [judgement_to_dict(item) for item in report.judgements],
        "provisional_notice": report.provisional_notice,
    }


def _attribution_to_dict(report: M1Report) -> dict[str, object]:
    """帰属を写す。**合計誤差の単一値を作らない**（要件 6.9）。"""
    result = report.attribution
    return {
        "bias": {
            "vector_mm": [_finite(value) for value in result.bias.vector_mm],
            "norm_mm": _finite(result.bias.norm_mm),
            "significance_ratio": _finite(result.bias.significance_ratio),
            "world_fixed_agreement_deg": _finite(
                result.bias.world_fixed_agreement_deg
            ),
            "camera_ray_agreement_deg": _finite(
                result.bias.camera_ray_agreement_deg
            ),
            "degenerate": result.bias.degenerate,
            "attribution": str(result.bias.attribution),
        },
        "scatter": {
            "rms_mm": _finite(result.scatter.rms_mm),
            "bootstrap_rms_mm": _finite(result.scatter.bootstrap_rms_mm),
            "residual_median_mm": _finite(result.scatter.residual_median_mm),
            "attribution": str(result.scatter.attribution),
        },
        "range_bands": [
            {
                "range_lo_mm": _finite(band.range_lo_mm),
                "range_hi_mm": _finite(band.range_hi_mm),
                "throw_count": band.throw_count,
                "mean_error_norm_mm": _finite(band.mean_error_norm_mm),
            }
            for band in result.range_bands
        ],
        "calibration_reference": _json_safe(result.calibration_reference),
        "reading_notes": list(report.attribution_reading_notes),
        "judgement": judgement_to_dict(result.judgement),
    }


def _oq27_to_dict(result: Oq27Result) -> dict[str, object]:
    """OQ-27 を写す。**未適用の改善項目も行として残す**（要件 9.4）。"""
    return {
        "verdict": str(result.verdict),
        "bottleneck_stage": result.bottleneck_stage,
        "bottleneck_label": result.bottleneck_label,
        "bottleneck_p95_ms": _finite(result.bottleneck_p95_ms),
        "end_to_end_p95_ms": _finite(result.end_to_end_p95_ms),
        "overhead_reference_ms": _finite(result.overhead_reference_ms),
        "resource_saturated": result.resource_saturated,
        "limiting_conditions": list(result.limiting_conditions),
        "improvements": [
            {
                "step": record.step,
                "applied": record.applied,
                "before": _json_safe(record.before),
                "after": _json_safe(record.after),
            }
            for record in result.improvements
        ],
        "judgement": judgement_to_dict(result.judgement),
    }


def _oq05_to_dict(result: Oq05Result) -> dict[str, object]:
    """OQ-05 を写す。**3つの注記は互いに別の警告である**（要件 10.2/10.4/10.5）。"""
    return {
        "window_mm": _finite(result.window_mm),
        "within_window_ratio": _finite(result.within_window_ratio),
        "within_window_count": result.within_window_count,
        "evaluated_throw_count": result.evaluated_throw_count,
        "confidence_level": _finite(result.confidence_level),
        "required_trials": dict(result.required_trials),
        "upper_bound_note": result.upper_bound_note,
        "object_scope_note": result.object_scope_note,
        "material_only_note": result.material_only_note,
        "verdict": str(result.judgement.verdict),
        "judgement": judgement_to_dict(result.judgement),
    }


def _budget_to_dict(result: BudgetUpdate) -> dict[str, object]:
    """時間予算表の更新値を写す。**区間3 の行は欠測のまま残る。**"""
    return {
        "ready": result.ready,
        "missing_items": list(result.missing_items),
        "rows": [
            {
                "segment": row.segment,
                "label": row.label,
                "assumed": row.assumed,
                "measured": _distribution_to_dict(row.measured),
                "trials": row.trials,
                "note": row.note,
            }
            for row in result.rows
        ],
        "total_flight_ms": _distribution_to_dict(result.total_flight_ms),
        "remaining_time_ms": _distribution_to_dict(result.remaining_time_ms),
        "derived_latency_target_ms": _finite(result.derived_latency_target_ms),
        "segment3_assumed_ms": _finite(result.segment3_assumed_ms),
        "total_flight_assumed": result.total_flight_assumed,
        "remaining_time_assumed": result.remaining_time_assumed,
        "remaining_time_note": result.remaining_time_note,
        "provisional_target_note": result.provisional_target_note,
        "computation_only_note": result.computation_only_note,
        "judgement": judgement_to_dict(result.judgement),
    }


def _distribution_to_dict(
    distribution: Distribution | None,
) -> dict[str, object] | None:
    """分布を写す。**欠測は `None` のまま**（0 で埋めない）。"""
    if distribution is None:
        return None
    return {
        "count": distribution.count,
        "median": _finite(distribution.median),
        "p95": _finite(distribution.p95),
        "iqr": _finite(distribution.iqr),
        "minimum": _finite(distribution.minimum),
        "maximum": _finite(distribution.maximum),
        "missing": distribution.missing,
    }


# ---------------------------------------------------------------------------
# 人が読む要約
# ---------------------------------------------------------------------------

_MISSING_TEXT = "欠測"


def _num(value: float | None) -> str:
    """数値を表示用の文字列へ。**欠測は「欠測」と書く**（0 と書かない）。"""
    finite = _finite(value)
    return _MISSING_TEXT if finite is None else f"{finite:g}"


def _count(value: int | None) -> str:
    return _MISSING_TEXT if value is None else str(value)


def _pairs(mapping: Mapping[str, float]) -> str:
    if not mapping:
        return "記録なし"
    return "、".join(f"{key}={value:g}" for key, value in mapping.items())


def render_summary(report: M1Report) -> str:
    """人が読む要約を組み立てる。

    **判定規則の説明文を必ず含める**（design.md「Reporter」Risks、タスク 7.1
    の観測可能な完了状態）。要約するのは数値であって規則ではない——規則を
    削った要約は、数値だけが残って根拠が消えた状態そのものである。

    要約と機械可読出力は**同じ材料から作る**。片方だけに載る値があると、
    どちらを引用したかで議論の前提が変わる。
    """
    lines: list[str] = [
        f"# M1 レポート（セッション {report.session_id}）",
        "",
        f"- 出力の形の版: {report.report_version}",
        f"- キャリブレーション識別子: {report.calibration_id}",
        f"- 検証を通過しているか: {'はい' if report.verified else 'いいえ'}",
        f"- 暫定の印: {'あり' if report.provisional else 'なし'}",
    ]
    if report.warnings:
        lines += ["", "## 警告"]
        lines += [f"- {warning}" for warning in report.warnings]
    lines += _summary_measurements(report)
    lines += _summary_attribution(report)
    lines += _summary_oq27(report)
    lines += _summary_oq05(report)
    lines += _summary_budget(report)
    lines += _summary_overhead(report)
    lines += _summary_criteria(report)
    lines += ["", "## 設定の断り", f"- {report.provisional_notice}", ""]
    return "\n".join(lines)


def _summary_measurements(report: M1Report) -> list[str]:
    lines = [
        "",
        "## 実測7項目（`docs/requirements.md §8 M1`。想定値と並べて示す）",
    ]
    for row in report.measurements:
        lines += [
            "",
            f"### 項目{row.item} {row.label}",
            f"- 想定: {row.assumed}（出典: {row.assumed_source}）",
        ]
        for column in row.columns:
            if not column.present:
                lines.append(
                    f"- 実測 {column.key}: 集計に当該項目の行が無い"
                    f"（{_MISSING_TEXT}。0 件ではない）"
                )
                continue
            lines.append(
                f"- 実測 {column.key}: 代表値 {_num(column.median)}"
                f" / ばらつき（四分位範囲） {_num(column.iqr)}"
                f" / p95 {_num(column.p95)}"
                f" / 最小 {_num(column.minimum)} / 最大 {_num(column.maximum)}"
                f" / 試行数 {_count(column.count)}"
                f" / 欠測 {_count(column.missing)}"
            )
        lines += [f"- 注記: {note}" for note in row.notes]
    return lines


def _summary_attribution(report: M1Report) -> list[str]:
    result = report.attribution
    bias = result.bias
    scatter = result.scatter
    lines = [
        "",
        "## 誤差の帰属（成分ごとの内訳。合計の単一値へ畳まない）",
        "",
        "### 共通の偏り成分",
        f"- ベクトル: ({_num(bias.vector_mm[0])}, {_num(bias.vector_mm[1])}) mm",
        (
            f"- 大きさ: {_num(bias.norm_mm)} mm"
            f" / 有意比（偏り÷ばらつき）: {_num(bias.significance_ratio)}"
        ),
        (
            f"- World 固定方向との角度差:"
            f" {_num(bias.world_fixed_agreement_deg)} deg"
            f" / カメラ視線方向との角度差:"
            f" {_num(bias.camera_ray_agreement_deg)} deg"
        ),
        f"- 2方向の縮退: {'あり' if bias.degenerate else 'なし'}",
        f"- 帰属先: {bias.attribution}",
        "",
        "### ばらつき成分",
        (
            f"- ばらつき（RMS）: {_num(scatter.rms_mm)} mm"
            f" / 再抽出による見積もり: {_num(scatter.bootstrap_rms_mm)} mm"
        ),
        f"- フィット残差の代表値: {_num(scatter.residual_median_mm)} mm",
        f"- 帰属先: {scatter.attribution}",
        "",
        "### 距離帯ごとの誤差",
    ]
    if result.range_bands:
        lines += [
            f"- {_num(band.range_lo_mm)}〜{_num(band.range_hi_mm)} mm:"
            f" 平均誤差 {_num(band.mean_error_norm_mm)} mm"
            f" / 投擲数 {band.throw_count}"
            for band in result.range_bands
        ]
    else:
        lines.append(f"- 帯に入った投擲が無い（{_MISSING_TEXT}）")
    lines += ["", "### 読み分け規則"]
    lines += [f"- {note}" for note in report.attribution_reading_notes]
    lines.append(f"- 突き合わせた検証レポートの要約: {dict(result.calibration_reference)}")
    return lines


def _summary_oq27(report: M1Report) -> list[str]:
    result = report.oq27
    lines = [
        "",
        "## OQ-27（Raspberry Pi 4 の継続可否）",
        f"- 判定値: {result.verdict}",
        f"- 根拠: {result.judgement.rationale}",
        (
            f"- 律速段階: {result.bottleneck_stage or _MISSING_TEXT}"
            f"（{result.bottleneck_label or _MISSING_TEXT}）"
            f" p95 {_num(result.bottleneck_p95_ms)} ms"
        ),
        (
            f"- end-to-end p95: {_num(result.end_to_end_p95_ms)} ms"
            f" / 同一測定から得た比較対象:"
            f" {_num(result.overhead_reference_ms)} ms"
        ),
        f"- 資源の飽和: {_saturation_text(result.resource_saturated)}",
    ]
    if result.limiting_conditions:
        lines += [
            f"- 律速している条件: {condition}"
            for condition in result.limiting_conditions
        ]
    lines.append("- 改善適用履歴（`development-environment.md §13.2`）:")
    if result.improvements:
        lines += [
            f"  - {record.step}: 適用={'済' if record.applied else '未'}"
            f" / 適用前 {_pairs(record.before)}"
            f" / 適用後 {_pairs(record.after)}"
            for record in result.improvements
        ]
    else:
        lines.append("  - 記録なし")
    lines.append("- ハードウェアの置き換えは実行しない。判断材料と結論の提示にとどめる。")
    return lines


def _saturation_text(value: bool | None) -> str:
    if value is None:
        return f"{_MISSING_TEXT}（判定できる材料が無い。余裕があるとは読まない）"
    return "あり" if value else "なし"


def _summary_oq05(report: M1Report) -> list[str]:
    result = report.oq05
    return [
        "",
        "## OQ-05（NFR-7 の目標成功率と試行回数）——**材料であって決着ではない**",
        f"- {result.material_only_note}",
        f"- 暫定許容窓: {_num(result.window_mm)} mm",
        (
            f"- 窓に収まった割合: {_num(result.within_window_ratio)}"
            f"（{result.within_window_count} / {result.evaluated_throw_count}）"
        ),
        f"- {result.upper_bound_note}",
        (
            f"- 信頼水準 {_num(result.confidence_level)} での必要試行回数:"
            f" {_required_trials_text(result.required_trials)}"
        ),
        f"- {result.object_scope_note}",
    ]


def _required_trials_text(required: Mapping[str, int | None]) -> str:
    if not required:
        return _MISSING_TEXT
    return "、".join(
        f"幅 {width}: {_MISSING_TEXT if value is None else value}"
        for width, value in required.items()
    )


def _summary_budget(report: M1Report) -> list[str]:
    result = report.budget
    lines = [
        "",
        "## 時間予算表の更新値（`docs/requirements.md §3`）",
        f"- 更新できる状態か: {'はい' if result.ready else 'いいえ'}",
    ]
    if result.missing_items:
        lines += [f"- 欠測している列: {item}" for item in result.missing_items]
    for row in result.rows:
        lines.append(
            f"- 区間{row.segment} {row.label}: 想定 {row.assumed}"
            f" / 実測 {_row_measured_text(row)}"
        )
        lines.append(f"  - 備考: {row.note}")
    lines += [
        (
            f"- 総飛行時間: 想定 {result.total_flight_assumed}"
            f" / 実測 {_measured_text(result.total_flight_ms)}"
        ),
        (
            f"- 移動体に残された時間: 想定 {result.remaining_time_assumed}"
            f" / 実測 {_measured_text(result.remaining_time_ms)}"
        ),
        (
            f"- 導出した予測レイテンシの暫定目標:"
            f" {_num(result.derived_latency_target_ms)} ms"
            f"（据え置いた区間3 の想定値"
            f" {_num(result.segment3_assumed_ms)} ms を含む）"
        ),
        f"- 注記: {result.remaining_time_note}",
        f"- 注記: {result.provisional_target_note}",
        f"- 注記: {result.computation_only_note}",
    ]
    return lines


def _row_measured_text(row: BudgetRow) -> str:
    """時間予算表の1行の実測列。

    **試行数は行が持つ値を1つだけ出す**（要件 11.2）。`Distribution.count`
    と `BudgetRow.trials` はどちらも「試行数」だが**別のフィールド**であり、
    両方を同じ見出しで並べると、読み手はどちらがその区間の試行数なのか
    決められない（合計行では実際に別の数になりうる）。行の試行数は
    `BudgetRow.trials` が正である。
    """
    if row.measured is None:
        return f"{_MISSING_TEXT} / 試行数 {row.trials}"
    return (
        f"代表値 {_num(row.measured.median)}"
        f" / ばらつき {_num(row.measured.iqr)}"
        f" / 試行数 {row.trials}"
    )


def _measured_text(distribution: Distribution | None) -> str:
    """行を持たない分布（総飛行時間・移動体に残された時間）の実測列。"""
    if distribution is None:
        return _MISSING_TEXT
    return (
        f"代表値 {_num(distribution.median)}"
        f" / ばらつき {_num(distribution.iqr)}"
        f" / 試行数 {distribution.count}"
    )


def _summary_overhead(report: M1Report) -> list[str]:
    result = report.overhead
    if result is None:
        return [
            "",
            "## 計測 ON/OFF 比較",
            "- 実施していない（比較結果が無い）。",
        ]
    lines = [
        "",
        "## 計測 ON/OFF 比較（計測の非侵襲性）",
        f"- 判定値: {result.judgement.verdict}",
        f"- 交互実行の並び: {'→'.join(result.segment_order)}",
        f"- {result.upstream_segment_note}",
        f"- end-to-end の定義: {result.end_to_end_definition}",
    ]
    lines += [
        f"- {verdict.target_label}: 有意に変化しない={verdict.passed}"
        f" / 中央値の差 {_num(verdict.median_delta_ms)} ms"
        f" / 基準（無効条件の四分位範囲） {_num(verdict.baseline_iqr_ms)} ms"
        f" / {verdict.detail}"
        for verdict in result.verdicts
    ]
    if result.unconditional_validity_note is not None:
        lines.append(f"- {result.unconditional_validity_note}")
    return lines


def _summary_criteria(report: M1Report) -> list[str]:
    """**全判定の規則の説明文を全文のまま並べる。**

    数値だけが残って根拠が消える状態を避けるための節であり、**本レポートで
    最も削ってはならない部分**である（design.md「Reporter」Risks）。
    """
    lines = [
        "",
        "## 判定規則（実測前に固定した規則。**全文を残す**）",
    ]
    for judgement in report.judgements:
        lines += [
            "",
            f"### {judgement.question}",
            (
                f"- 判定値: {judgement.verdict}"
                f" / 暫定: {'あり' if judgement.provisional else 'なし'}"
            ),
            f"- 規則: {judgement.criterion}",
        ]
    return lines


# ---------------------------------------------------------------------------
# 書き出し
# ---------------------------------------------------------------------------


def report_output_path(output_root: Path, session_id: str) -> Path:
    """書き出し先のパス（design.md「Reporter」Output）。"""
    return Path(output_root) / f"report-{session_id}.json"


def write_report(report: M1Report, output_root: Path, session_id: str) -> Path:
    """レポートを1つの JSON として書き出す。

    **実測値・判定値・判定規則の説明文が同じファイルに入る。** 別々のファイル
    へ分かれると、数値だけが引用されて根拠が失われる（`bench.write_overhead_
    report()` と同じ理由）。

    `allow_nan=False` を渡すのは、NaN / Infinity が**残っていたら書き出しを
    失敗させる**ためである（design.md「集計・判断の出力」）。本モジュールは
    非有限値を欠測へ倒してから写しているので、ここで例外が出ることは
    「写し漏れがある」という意味になる。
    """
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    path = report_output_path(root, session_id)
    path.write_text(
        json.dumps(
            report_to_dict(report),
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path
