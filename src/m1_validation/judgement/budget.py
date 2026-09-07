"""時間予算表（`docs/requirements.md §3`）の更新値の算出（ゲート付き）。

design.md「Components and Interfaces / L7: 判断 / BudgetUpdater」、
tasks.md タスク 6.3、要件 11.1-11.8。

**本モジュールは値を算出するだけであり、文書の書き換えは行わない**
（design.md「BudgetUpdater」Risks）。`docs/requirements.md` を実際に書き換える
のは人であり、差分を目で確認するためにその手順を機械へ渡さない。更新してよい
対象も §3 の時間予算表とその導出値（NFR-3 の暫定目標）に限られ、他節へは及ば
ない（要件 11.8）。

本モジュールが守っているのは4つの規律である。

**第一に、ゲートが先である**（要件 11.1）。M1 実測7項目のいずれかが欠測して
いる間は、更新値を一切出さない。代わりに**欠測している列を列挙して返す**
——「何が足りないのか」が読めなければ、次に何を測ればよいのか決められない。
揃わないうちに部分的な更新値を出すと、まだ測っていない区間の数字が「実測で
更新された表」の顔をして後続のマイルストーンへ流れる。

**第二に、区間3（予測確定〜移動体が動き出す）を勝手に埋めない。** この区間は
本 Spec の範囲外である——M1 に移動体は存在せず、送信・受信・指令反映のいずれ
も測れない。**行は残し、実測値を欠測のままとし、備考に「M3 で実測する」と
注記する。** 0 で埋めれば「瞬時に動き出す」と言ったことになり、想定値で埋め
れば「実測した」と言ったことになる。どちらも測っていない。同じ規律を
`judgement.oq27` が比較対象（オーバーヘッド相当値＝区間1＋区間2）に対して
採っている。

**第三に、区間2 を「検出開始〜初回予測」と読み替える**（要件 11.3）。予測コア
は最小サンプル数に達した時点で初回予測を出し、以降サンプルが増えるたびに更新
する逐次予測であり、元の表が前提にしている「予測が1回確定して終わり」という
単発予測モデルとは別物である。**読み替えたことを行の見出しに反映する**
——見出しが元のままだと、読み手は別物の量を同じ量として比べる。逐次予測が
前提である旨の本文は、実測項目3 を作った測定自身が申告した
`LatencyResult.first_prediction_basis` をそのまま運ぶ（焼き付けると、表の注記
と測定の基準が黙って食い違い得る）。

**第四に、実測が想定と食い違っても、数値を想定へ合わせない**（要件 11.6）。
想定値の列と実測の列は別であり、片方が他方を上書きすると「食い違ったこと」
自体が読めなくなる。既存の想定値・導出根拠・注記は削除せず、更新後もこれらの
数値が**合否条件ではなく暫定目標値**である旨を維持する（要件 11.5 / 11.7）。

本モジュールは L7 層であり、`errors` / `types` / `config` と `metrics` の
結果型、そして標準ライブラリだけを参照する。上流3パッケージ
（`sensing_foundation` / `flying_object_tracking` / `world_frame_calibration`）
を直接 import しない（要件 13.1）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from m1_validation.config import M1Settings
from m1_validation.metrics.aggregate import (
    ITEM_DETECT_TO_FIRST_PREDICTION_MS,
    ITEM_RELEASE_TO_DETECT_MS,
    ITEM_TOTAL_FLIGHT_MS,
    MEASUREMENT_ITEM_KEYS,
    Distribution,
    ThrowAggregate,
    ThrowRow,
    # **分位点の式を書き直さない。** 同じレポートに並ぶ数字の作り方が2つあると、
    # 「投擲群の p95」と「導出した分布の p95」が別の意味の数になる
    # （research.md Decision 7「集計器を二重に持たない」）。`aggregate.py` は
    # 本タスクの境界外で公開ヘルパへ昇格できないため、私有関数を参照している
    # ——公開化はフィーチャレベル検証で扱う。
    _percentile,
)
from m1_validation.metrics.latency import LatencyResult
from m1_validation.types import Judgement

# ---------------------------------------------------------------------------
# 語彙
# ---------------------------------------------------------------------------

#: `Judgement.question`。判断の種類を1語で表す（`types.Judgement` 参照）。
BUDGET_QUESTION: str = "time-budget"

#: 実測値が揃い、更新値を算出した。
VERDICT_UPDATABLE: str = "updatable"

#: 実測値が揃っておらず、更新しない（要件 11.1）。**失敗ではない**——
#: 何が足りないのかを `missing_items` として返すことが本判定の仕事である。
VERDICT_NOT_UPDATABLE: str = "not_updatable"

#: 区間の識別子（`BudgetRow.segment`）。`total` は §3 の表の
#: 「オーバーヘッド合計」行に当たる。
SEGMENT_RELEASE_TO_DETECT: str = "1"
SEGMENT_DETECT_TO_FIRST_PREDICTION: str = "2"
SEGMENT_PREDICTION_TO_MOVE: str = "3"
SEGMENT_OVERHEAD_TOTAL: str = "total"


def missing_item_key(item: int, column: str) -> str:
    """欠測した列の名前を組む（要件 11.1）。

    **番号と列名の両方を入れる。** 列名だけでは `docs/requirements.md §8 M1` の
    実測7項目のどれに当たるのかが読めず、番号だけでは項目4 / 5 / 7 が持つ
    2列のどちらなのかが決まらない。
    """
    return f"item{item}:{column}"


# ---------------------------------------------------------------------------
# `docs/requirements.md §3` の写し（**勝手な数値を発明しない**）
# ---------------------------------------------------------------------------
#
# 見出し・想定値・備考は §3 の時間予算表そのものである。実測で更新するのは
# 「実測」「ばらつき」「試行数」の列であって、想定値の列ではない（要件 11.6）。

_LABEL_SEGMENT_1: str = "リリース〜検出開始"

#: **区間2 の見出しは読み替えを反映する**（要件 11.3）。元の見出しを併記するの
#: は、読み手が「元の表もそう書いていた」と誤解しないようにするためである。
_LABEL_SEGMENT_2: str = "検出開始〜初回予測（元の表の「検出開始〜予測確定」からの読み替え）"

_LABEL_SEGMENT_3: str = "予測確定〜移動体が動き出す"
_LABEL_OVERHEAD_TOTAL: str = "オーバーヘッド合計（区間1＋区間2。区間3 を含まない）"

_ASSUMED_SEGMENT_1: str = "0.05〜0.10 s"
_ASSUMED_SEGMENT_2: str = "0.10〜0.15 s"
_ASSUMED_SEGMENT_3: str = "0.05 s"
_ASSUMED_OVERHEAD_TOTAL: str = "0.2〜0.3 s"

#: §3 本文の「総飛行時間 ≈ 0.6〜1.2s」。
_ASSUMED_TOTAL_FLIGHT: str = "0.6〜1.2 s"

#: §3 本文の「移動体に残された時間 ≈ 0.3〜1.0 s」。
_ASSUMED_REMAINING_TIME: str = "0.3〜1.0 s"

_NOTE_SEGMENT_1: str = (
    "手・腕による遮蔽、検出の立ち上がり（§3 の備考）。実測項目2 から算出する。"
    "§3 が「まったく未検証」と書いていた区間であり、M1 で最優先に実測する対象で"
    "ある。"
)

_NOTE_SEGMENT_2_PREFIX: str = (
    "サンプル取得（FR-1）＋フィッティング（FR-2）（§3 の備考）。"
    "実測項目3 から算出する。終点は「予測確定」ではなく初回予測である。"
)

_NOTE_SEGMENT_3: str = (
    "送信・受信・指令反映（NFR-3 に含む）（§3 の備考）。"
    "本区間は本 Spec の範囲外である——M1 に移動体は存在せず、"
    "送信・受信・指令反映のいずれも測れない。M3 で実測する。"
    "実測値は欠測のまま残し、0 でも想定値でも埋めない。"
)

_NOTE_OVERHEAD_TOTAL: str = (
    "区間1と区間2 を投擲ごとに足し合わせた分布である。"
    "区間3 を含まないため、実際のオーバーヘッドの下側である。"
    "区間3 が実測される（M3）まで、合計は下側のままとして読むこと。"
    # **想定列と実測列で区間3 の扱いが違う。** これを書かないと、要件 11.2 の
    # 「想定値と実測を並べる」がそのまま比較可能に見えてしまう。
    "なお §3 の想定値 0.2〜0.3 s の側は区間1＋区間2＋区間3 の和であり、"
    "区間3 を含む。"
    "想定と実測は同じ範囲を指していないので、そのまま引き比べないこと。"
)

# --- 注記（**出力に残す**。要件 11.5 / 11.7 / 11.8）------------------------
#
# 3つとも「読み手が更新値を誤読しないため」の警告であり、互いに別物である。
# 残り時間の留保を落とすと移動体の持ち時間が過大に見積もられ、暫定目標の断りを
# 落とすと更新後の数値が合否条件として読まれ、算出のみである旨を落とすと本
# コンポーネントが文書を書き換えたことにされる。

REMAINING_TIME_NOTE: str = (
    "移動体に残された時間は、投擲ごとに「総飛行時間 −（区間1＋区間2）」として"
    "算出した分布である。区間3（予測確定〜移動体が動き出す）を差し引いていない"
    "ため、実際に移動体へ残る時間の上側である。区間3 が M3 で実測されるまで、"
    "この値を移動体の持ち時間としてそのまま使わないこと。"
    # **想定側は区間3 を差し引いた後の値である**（合計行とは扱いが逆向きに
    # 見えるが、どちらも「想定は区間3 を計上している」という同じ事実である）。
    "なお §3 の想定値 0.3〜1.0 s の側は区間3 を差し引いた後の値である。"
    "想定と実測は同じ範囲を指していないので、そのまま引き比べないこと。"
)

PROVISIONAL_TARGET_NOTE: str = (
    "更新後の予測レイテンシ（NFR-3）の値も暫定目標値であって合否条件ではない"
    "（要件 11.7、docs/requirements.md NFR-3 の但し書き）。"
    "最終的な合否は NFR-7（キャッチ成功率）で判定する。"
)

COMPUTATION_ONLY_NOTE: str = (
    "本コンポーネントは値を算出するだけであり、docs/requirements.md を"
    "書き換えない（要件 11.8、design.md「BudgetUpdater」Risks）。"
    "文書の更新は差分を目で確認するために人が行う。"
    "更新してよい対象は §3 の時間予算表とその導出値（NFR-3 の暫定目標）に"
    "限られ、他節には及ばない。"
    "既存の想定値・導出根拠・注記は削除しない（要件 11.5）。"
)

# --- 理由（**相互排他**。次にやることが違う）-------------------------------

_RATIONALE_READY: str = (
    "M1 実測7項目がすべて揃っているので、時間予算表の更新値を算出した"
    "（投擲数 {throws}）。"
    "区間3 は本 Spec の範囲外であり、行を残して未実測のままにしてある。"
)

_RATIONALE_BLOCKED: str = (
    "M1 実測7項目のうち {count} 列が欠測しているので、"
    "時間予算表の更新値を出さない（要件 11.1）。欠測している列: {items}。"
)


# ---------------------------------------------------------------------------
# 判定規則の説明文（**結果と同じ場所に置く**）
# ---------------------------------------------------------------------------

_CRITERION_TEMPLATE: str = (
    "時間予算表（docs/requirements.md §3）の更新値の算出規則"
    "（実測前に固定。design.md「BudgetUpdater」、要件 11.1-11.8）: "
    "判定値は「updatable（更新値を算出した）」と"
    "「not_updatable（実測値が揃わず更新しない）」の2値である。"
    "【ゲート】M1 実測7項目のいずれかが欠測している間は、"
    "時間予算表の更新値を一切出さない（要件 11.1）。"
    "欠測している列を item<実測項目の番号>:<項目キー> の形で列挙して返し、"
    "各行の実測値は欠測のままにする。"
    "【行の構成】各区間の行に、既存の想定値・実測の代表値（中央値）・"
    "ばらつき（四分位範囲）・試行数を並べる（要件 11.2）。"
    "代表値に平均を採らない——投擲はばらつきが大きく外れ値が出やすい。"
    "試行数はその区間の値が得られた投擲数であり、投擲数でも有効試行数でもない。"
    "【区間2 の読み替え】区間2 は「検出開始〜初回予測」と読み替える"
    "（要件 11.3）。"
    "予測コアは最小サンプル数に達した時点で初回予測を出し、以降サンプルが"
    "増えるたびに更新する逐次予測であり、元の表が前提にしている"
    "「予測が1回確定して終わり」という単発予測モデルとは別物である。"
    "読み替えたことを行の見出しと備考の両方に残す。"
    "【区間3】区間3（予測確定〜移動体が動き出す）は本 Spec の範囲外である。"
    "M1 に移動体は存在せず、送信・受信・指令反映のいずれも測れないので、"
    "行は残したうえで実測値を欠測のままとし、備考に M3 で実測すると注記する。"
    "0 でも想定値でも埋めない。"
    "【オーバーヘッド合計】オーバーヘッド合計は、"
    "投擲ごとに区間1と区間2の実測値を足した分布とする"
    "（区間ごとの代表値を足すのではない——代表値が同じ投擲から来るとは"
    "限らない）。"
    "区間3 を含まないため、実際のオーバーヘッドの下側である。"
    "【移動体に残された時間】移動体に残された時間は、"
    "投擲ごとに総飛行時間から同じ投擲の区間1と区間2の実測値を引いた分布と"
    "する。3つの実測値が揃った投擲だけが分布に入る。"
    "区間3 を差し引いていないため、実際に移動体へ残る時間の上側である。"
    "【想定側との比較】§3 の想定値の側は、"
    "オーバーヘッド合計（0.2〜0.3 s）が区間1＋区間2＋区間3 の和であり、"
    "移動体に残された時間（0.3〜1.0 s）は区間3 を差し引いた後の値である。"
    "本 Spec の実測側は区間3 を含まないので、"
    "この2行は想定と実測をそのまま引き比べられない。"
    "【導出値】時間予算表から導出されている予測レイテンシの暫定目標"
    "（NFR-3）を、更新後の表と食い違わない値へ揃える（要件 11.4）。"
    "区間2 の実測の上側（p95）に、区間3 の据え置きの想定値 {seg3:g} ms を"
    "足した値とする。"
    "中央値ではなく上側を採るのは、元の目標が各区間の想定範囲の上端から"
    "導かれているためである。丸めない。"
    "【据え置き】この {seg3:g} ms は区間3 の想定値であって実測値ではない。"
    "区間3 が M3 で実測されるまでの据え置きであり、"
    "表の区間3 の行を埋めるものではない。"
    "【想定と食い違った場合】実測値が想定と食い違っても、"
    "数値を想定へ合わせない。表そのものを実測値で更新する（要件 11.6）。"
    "【残すもの】既存の想定値・導出根拠・注記を削除しない（要件 11.5）。"
    "更新後も、これらの数値が合否条件ではなく暫定目標値である旨を維持する"
    "（要件 11.7）。"
    "【更新対象】更新してよいのは §3 の時間予算表とその導出値だけであり、"
    "他節を書き換えない（要件 11.8）。"
    "本コンポーネントは値を算出するだけであり、文書の書き換えは行わない"
    "（design.md「BudgetUpdater」Risks）。"
)


def budget_criterion(*, segment3_assumed_ms: float) -> str:
    """実際に適用する算出規則の説明文を組み立てる。

    **実際に使った据え置き値を文面へ入れる。** 規則の文だけを残して数値を伏せる
    と、同じ文で違う導出値が正当化できてしまう（`oq27.oq27_criterion` /
    `oq05.oq05_criterion` と同じ理由）。文面は実測の中身によって変わらない
    ——結果に合わせて動く規則は規則ではない。
    """
    return _CRITERION_TEMPLATE.format(seg3=segment3_assumed_ms)


# ---------------------------------------------------------------------------
# 結果
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BudgetRow:
    """時間予算表の1行（要件 11.2）。

    Attributes:
        segment: 区間の識別子（`1` / `2` / `3` / `total`）。
        label: 行の見出し。**区間2 は読み替えを反映する**（要件 11.3）。
        assumed: 既存の想定値（`docs/requirements.md §3` の文字列をそのまま
            残す）。**実測値で置き換えない**——想定と実測が同じ列に入ると、
            食い違ったこと自体が読めなくなる（要件 11.6）。
        measured: 実測の分布（代表値・ばらつき・試行数を持つ）。ゲートが
            閉じている間、および**区間3** では `None`。**0 で埋めない。**
        trials: その区間の値が得られた投擲数。`measured` が無ければ 0。
        note: 備考。§3 の備考を残したうえで、本 Spec が足した読み替え・
            範囲外・下側であることの注記を続ける（要件 11.5）。
    """

    segment: str
    label: str
    assumed: str
    measured: Distribution | None
    trials: int
    note: str


@dataclass(frozen=True, slots=True)
class BudgetUpdate:
    """時間予算表の更新値（要件 11.1-11.8）。

    Attributes:
        ready: 実測値が揃っていて更新値を出せるか（要件 11.1）。
        missing_items: 欠測している列（`missing_item_key()` の形）。
            `ready` が真なら空である。
        rows: 区間ごとの行。**ゲートが閉じていても行と想定値は残る。**
        total_flight_ms: 総飛行時間（実測項目1）の分布。ゲートが閉じていれば
            `None`。
        remaining_time_ms: 移動体に残された時間の分布。ゲートが閉じていれば
            `None`。**区間3 を差し引いていないので上側である**
            （`remaining_time_note`）。
        derived_latency_target_ms: 更新後の表から導出した NFR-3 の暫定目標
            （ms。要件 11.4）。ゲートが閉じていれば `None`。
        segment3_assumed_ms: 導出で据え置いた区間3 の想定値（ms）。
            **実測値ではない**——何を足したのかを読めるようにするために公開
            する。
        total_flight_assumed: 総飛行時間の既存の想定値（§3 本文）。
        remaining_time_assumed: 移動体に残された時間の既存の想定値（§3 本文）。
        remaining_time_note: 残り時間が上側である旨。
        provisional_target_note: 更新後も暫定目標値であって合否条件ではない旨
            （要件 11.7）。
        computation_only_note: 本コンポーネントが値を算出するだけで文書を
            書き換えない旨、および更新対象が §3 の表とその導出値に限られる旨
            （要件 11.8）。
        judgement: 判断の共通の形。`verdict` は `VERDICT_UPDATABLE` /
            `VERDICT_NOT_UPDATABLE` の2値。

    Postconditions:
        `ready is False` のとき `rows` の `measured` はすべて `None` であり、
        `total_flight_ms` / `remaining_time_ms` / `derived_latency_target_ms`
        も `None` である（design.md「BudgetUpdater」Postconditions）。

    **design.md の擬似コードとの差**（フィーチャレベル検証で同期すること）:

    - `segment3_assumed_ms` / `total_flight_assumed` / `remaining_time_assumed`
      と3つの注記を足した。要件 11.2 は想定値を実測と並べることを求めており、
      総飛行時間と残り時間にも §3 本文の想定値がある。要件 11.7 の「暫定目標値
      である旨の維持」と要件 11.8 の「更新対象の限定」は、判定値からは読めない
      独立の注記である（`oq05.Oq05Result` の3注記と同型）。
    - `BudgetRow.measured` を区間3 では常に `None` にした。擬似コードは
      `Distribution | None` を許しているだけだが、**区間3 は入力によらず欠測**
      である（M1 に移動体が無い）。
    """

    ready: bool
    missing_items: tuple[str, ...]
    rows: tuple[BudgetRow, ...]
    total_flight_ms: Distribution | None
    remaining_time_ms: Distribution | None
    derived_latency_target_ms: float | None
    segment3_assumed_ms: float
    total_flight_assumed: str
    remaining_time_assumed: str
    remaining_time_note: str
    provisional_target_note: str
    computation_only_note: str
    judgement: Judgement


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def compute_budget_update(
    aggregate: ThrowAggregate,
    latency: LatencyResult,
    *,
    settings: M1Settings,
) -> BudgetUpdate:
    """時間予算表の更新値を算出する（要件 11.1-11.8）。

    **本関数は値を算出するだけであり、`docs/requirements.md` を書き換えない**
    （要件 11.8、design.md「BudgetUpdater」Risks）。文書の更新は差分を目で
    確認するために人が行う。更新してよい対象も §3 の時間予算表とその導出値
    （NFR-3 の暫定目標）に限られ、他節には及ばない。

    **実測値が揃っていない間は更新値を出さない**（要件 11.1）。M1 実測7項目の
    いずれかが欠測していれば `ready=False` を返し、欠測している列を
    `missing_items` に列挙する。このとき各行の実測値はすべて欠測のままである。

    **区間3（予測確定〜移動体が動き出す）は本 Spec の範囲外である。** 行は残す
    が実測値は常に欠測であり、備考に「M3 で実測する」と注記する。**勝手に
    埋めない。**

    Args:
        aggregate: 1つのキャリブレーション（識別子 × 検証状態）に属する投擲群
            の集計。区間ごとの分布・投擲ごとの値・試行数をここから読む。
        latency: 段階別レイテンシの集計。**区間2 の読み替えの根拠**
            （`first_prediction_basis`）と、集計の入力になったログの健全性を
            ここから読む。区間ごとの所要時間は `aggregate` 側にしか無いので、
            同じ量を2つの出所から二重に載せない。
        settings: 解決済みの設定。`budget.segment3_assumed_ms`（区間3 の
            据え置きの想定値）を読む。

    Returns:
        `BudgetUpdate`。**更新しない場合でも結果は返る**——行・想定値・注記は
        ゲートの状態によらず残り、欠測は `None` として表れる。

    Notes:
        同一入力・同一設定に対して同一の更新値を返す（要件 12.4）。乱数を
        使わず、行の並びも欠測列の並びも入力順に依存しない。
    """
    segment3_assumed_ms = settings.budget.segment3_assumed_ms
    missing = _missing_items(aggregate.items)
    ready = not missing

    rows = _rows(
        aggregate,
        latency,
        ready=ready,
    )
    total_flight = aggregate.items.get(ITEM_TOTAL_FLIGHT_MS) if ready else None
    remaining = _remaining_time(aggregate.per_throw) if ready else None
    target = (
        _derived_latency_target(
            aggregate.items.get(ITEM_DETECT_TO_FIRST_PREDICTION_MS),
            segment3_assumed_ms=segment3_assumed_ms,
        )
        if ready
        else None
    )

    overhead_row = _row_of(rows, SEGMENT_OVERHEAD_TOTAL)
    segment1_row = _row_of(rows, SEGMENT_RELEASE_TO_DETECT)
    segment2_row = _row_of(rows, SEGMENT_DETECT_TO_FIRST_PREDICTION)

    rationale = (
        _RATIONALE_READY.format(throws=aggregate.throw_count)
        if ready
        else _RATIONALE_BLOCKED.format(
            count=len(missing), items=" / ".join(missing)
        )
    )

    return BudgetUpdate(
        ready=ready,
        missing_items=missing,
        rows=rows,
        total_flight_ms=total_flight,
        remaining_time_ms=remaining,
        derived_latency_target_ms=target,
        segment3_assumed_ms=segment3_assumed_ms,
        total_flight_assumed=_ASSUMED_TOTAL_FLIGHT,
        remaining_time_assumed=_ASSUMED_REMAINING_TIME,
        remaining_time_note=REMAINING_TIME_NOTE,
        provisional_target_note=PROVISIONAL_TARGET_NOTE,
        computation_only_note=COMPUTATION_ONLY_NOTE,
        judgement=Judgement(
            question=BUDGET_QUESTION,
            criterion=budget_criterion(segment3_assumed_ms=segment3_assumed_ms),
            verdict=VERDICT_UPDATABLE if ready else VERDICT_NOT_UPDATABLE,
            rationale=rationale,
            evidence={
                "calibration_id": aggregate.calibration_id,
                "verified": aggregate.verified,
                "throw_count": aggregate.throw_count,
                "valid_throw_count": aggregate.valid_throw_count,
                "session_count": len(aggregate.session_ids),
                "ready": ready,
                "missing_items": list(missing),
                "segment2_label": segment2_row.label,
                "segment1_median_ms": _median(segment1_row.measured),
                "segment1_iqr_ms": _iqr(segment1_row.measured),
                "segment1_trials": segment1_row.trials,
                "segment2_median_ms": _median(segment2_row.measured),
                "segment2_iqr_ms": _iqr(segment2_row.measured),
                "segment2_p95_ms": _p95(segment2_row.measured),
                "segment2_trials": segment2_row.trials,
                # 区間3 は入力によらず欠測である（**0 で埋めない**）。
                "segment3_measured_ms": None,
                "segment3_assumed_ms": segment3_assumed_ms,
                "segment3_note": _NOTE_SEGMENT_3,
                "overhead_total_median_ms": _median(overhead_row.measured),
                "overhead_total_iqr_ms": _iqr(overhead_row.measured),
                "overhead_total_trials": overhead_row.trials,
                "total_flight_median_ms": _median(total_flight),
                "total_flight_iqr_ms": _iqr(total_flight),
                "total_flight_trials": 0 if total_flight is None else total_flight.count,
                "remaining_time_median_ms": _median(remaining),
                "remaining_time_iqr_ms": _iqr(remaining),
                "remaining_time_trials": 0 if remaining is None else remaining.count,
                "derived_latency_target_ms": target,
                "assumed_segment1": _ASSUMED_SEGMENT_1,
                "assumed_segment2": _ASSUMED_SEGMENT_2,
                "assumed_segment3": _ASSUMED_SEGMENT_3,
                "assumed_overhead_total": _ASSUMED_OVERHEAD_TOTAL,
                "assumed_total_flight": _ASSUMED_TOTAL_FLIGHT,
                "assumed_remaining_time": _ASSUMED_REMAINING_TIME,
                # 区間2 の読み替えの根拠は、実測項目3 を作った測定自身の申告を
                # 運ぶ（表の注記と測定の基準を食い違わせない）。
                "first_prediction_basis": latency.first_prediction_basis,
                # 集計値ではなく**集計の入力の健全性**である。ここが 0 でなけ
                # れば、上のどの数字も「取りこぼしたログの上での値」として読む
                # 必要がある。
                "log_lines_dropped": latency.log_lines_dropped,
                "log_lines_skipped": latency.log_lines_skipped,
                "aggregate_provisional": aggregate.provisional,
                "remaining_time_note": REMAINING_TIME_NOTE,
                "provisional_target_note": PROVISIONAL_TARGET_NOTE,
                "computation_only_note": COMPUTATION_ONLY_NOTE,
            },
            # 暫定の印は「判断に用いてよい状態ではない」ことを示す
            # （要件 5.10）。ゲートが閉じていても、集計が暫定（試行数下限未達
            # など）でも立つ。**2項はそれぞれ単独で効く**——片方に畳むと、
            # 実測が揃っているのに試行数が足りない更新値に印が付かなくなる。
            provisional=(not ready or aggregate.provisional),
        ),
    )


# ---------------------------------------------------------------------------
# ゲート（要件 11.1）
# ---------------------------------------------------------------------------


def _missing_items(items: Mapping[str, Distribution]) -> tuple[str, ...]:
    """M1 実測7項目のうち、値が1件も得られていない列を並べる。

    **「行が無い」と「行はあるが値が無い」を同じ欠測として扱う。** 前者だけを
    見落とすと、集計の行そのものが落ちた項目が「揃っている」ことになり、
    更新値が測っていない量の上に立つ。

    並びは実測項目の番号順であり、入力のマッピングの順序に依存しない
    （要件 12.4）。
    """
    missing: list[str] = []
    for item in sorted(MEASUREMENT_ITEM_KEYS):
        for column in MEASUREMENT_ITEM_KEYS[item]:
            distribution = items.get(column)
            if distribution is None or distribution.median is None:
                missing.append(missing_item_key(item, column))
    return tuple(missing)


# ---------------------------------------------------------------------------
# 行（要件 11.2 / 11.3）
# ---------------------------------------------------------------------------


def _rows(
    aggregate: ThrowAggregate, latency: LatencyResult, *, ready: bool
) -> tuple[BudgetRow, ...]:
    """時間予算表の4行を組む。

    ゲートが閉じている間も**行・見出し・想定値・備考は残す**（要件 11.5）。
    残さないと、何を更新しようとしていたのか自体が読めなくなる。
    """
    segment1 = aggregate.items.get(ITEM_RELEASE_TO_DETECT_MS) if ready else None
    segment2 = (
        aggregate.items.get(ITEM_DETECT_TO_FIRST_PREDICTION_MS) if ready else None
    )
    overhead = _overhead_total(aggregate.per_throw) if ready else None
    return (
        BudgetRow(
            segment=SEGMENT_RELEASE_TO_DETECT,
            label=_LABEL_SEGMENT_1,
            assumed=_ASSUMED_SEGMENT_1,
            measured=segment1,
            trials=_trials(segment1),
            note=_NOTE_SEGMENT_1,
        ),
        BudgetRow(
            segment=SEGMENT_DETECT_TO_FIRST_PREDICTION,
            label=_LABEL_SEGMENT_2,
            assumed=_ASSUMED_SEGMENT_2,
            measured=segment2,
            trials=_trials(segment2),
            # 逐次予測が前提である旨は、実測項目3 を作った測定自身の申告を
            # そのまま運ぶ。焼き付けると、表の注記と測定の基準が黙って食い違う。
            note=f"{_NOTE_SEGMENT_2_PREFIX}{latency.first_prediction_basis}",
        ),
        BudgetRow(
            segment=SEGMENT_PREDICTION_TO_MOVE,
            label=_LABEL_SEGMENT_3,
            assumed=_ASSUMED_SEGMENT_3,
            # **入力によらず欠測。** 本 Spec に移動体は無い（要件 11.1 の
            # ゲートとは別の理由で埋まらない）。
            measured=None,
            trials=0,
            note=_NOTE_SEGMENT_3,
        ),
        BudgetRow(
            segment=SEGMENT_OVERHEAD_TOTAL,
            label=_LABEL_OVERHEAD_TOTAL,
            assumed=_ASSUMED_OVERHEAD_TOTAL,
            measured=overhead,
            trials=_trials(overhead),
            note=_NOTE_OVERHEAD_TOTAL,
        ),
    )


def _row_of(rows: Sequence[BudgetRow], segment: str) -> BudgetRow:
    for row in rows:
        if row.segment == segment:
            return row
    raise AssertionError(f"区間 {segment} の行が組み立てられていない")


def _trials(distribution: Distribution | None) -> int:
    """その区間の値が得られた投擲数。**投擲数でも有効試行数でもない。**"""
    return 0 if distribution is None else distribution.count


# ---------------------------------------------------------------------------
# 導出値
# ---------------------------------------------------------------------------


def _overhead_total(rows: Sequence[ThrowRow]) -> Distribution:
    """オーバーヘッド合計（区間1＋区間2）を**投擲ごとに**足した分布。

    **区間ごとの代表値を足さない。** 区間1 の中央値と区間2 の中央値が同じ投擲
    から来るとは限らず、足した値がどの投擲でも実現していない量になりうる。

    **区間3 を含めない**（`docs/requirements.md §3` の合計は区間1＋区間2＋
    区間3 である）。したがってここで得る値は実際のオーバーヘッドの**下側**で
    ある。`judgement.oq27` の比較対象と同じ規律であり、同じ理由で「勝手に
    埋めない」。
    """
    return _distribution([_sum(row) for row in rows])


def _remaining_time(rows: Sequence[ThrowRow]) -> Distribution:
    """移動体に残された時間（総飛行時間 − オーバーヘッド）を投擲ごとに算出する。

    **引く向きは「総飛行時間から引く」である。** 逆にすると負の量になり、
    「持ち時間」という意味を失う。

    3つの実測値（総飛行時間・区間1・区間2）が揃った投擲だけが分布に入る。
    片方が欠けた投擲を 0 で補って残すと、測っていない投擲が「持ち時間が長い
    投擲」として代表値を引き上げる。

    **区間3 を差し引いていないので上側である**（`REMAINING_TIME_NOTE`）。
    """
    values: list[float | None] = []
    for row in rows:
        flight = row.values.get(ITEM_TOTAL_FLIGHT_MS)
        overhead = _sum(row)
        values.append(None if flight is None or overhead is None else flight - overhead)
    return _distribution(values)


def _sum(row: ThrowRow) -> float | None:
    """1投擲ぶんの区間1＋区間2。どちらかが欠測なら `None`（**0 で埋めない**）。"""
    segment1 = row.values.get(ITEM_RELEASE_TO_DETECT_MS)
    segment2 = row.values.get(ITEM_DETECT_TO_FIRST_PREDICTION_MS)
    if segment1 is None or segment2 is None:
        return None
    return segment1 + segment2


def _derived_latency_target(
    segment2: Distribution | None, *, segment3_assumed_ms: float
) -> float | None:
    """NFR-3 の暫定目標を、**更新後の表と食い違わない値**として算出する（要件 11.4）。

    区間2 の実測の**上側**（p95）に、区間3 の**据え置きの想定値**を足す。

    - 上側を採るのは、元の 200 ms が各区間の想定範囲の**上端**（0.15 s ＋
      0.05 s）から導かれているためである。中央値を採ると、同じ「≤ X ms」と
      いう形の目標が別の意味になる。
    - 区間3 は本 Spec が測れないので、据え置きの想定値を足す。**これは表の
      区間3 の行を埋めることではない**——行は欠測のまま残り、据え置きである
      ことは `criterion` と `segment3_assumed_ms` に明記される。
    - **丸めない。** 丸めると、更新後の表から導けない値になる。
    """
    if segment2 is None or segment2.p95 is None:
        return None
    return segment2.p95 + segment3_assumed_ms


# ---------------------------------------------------------------------------
# 分布（**分位点の式は `aggregate.py` と同じものを使う**）
# ---------------------------------------------------------------------------


def _distribution(values: Sequence[float | None]) -> Distribution:
    """投擲ごとの値の並びから分布を組む。

    **`count + missing` は常に投擲数である**（`aggregate._distribution` と同じ
    不変条件）。欠測を分母から外すと、項目ごとに分母が動いて欠測の多さが
    読めなくなる。
    """
    present = sorted(value for value in values if value is not None)
    missing = len(values) - len(present)
    if not present:
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
        count=len(present),
        median=_percentile(present, 0.5),
        p95=_percentile(present, 0.95),
        iqr=_percentile(present, 0.75) - _percentile(present, 0.25),
        minimum=present[0],
        maximum=present[-1],
        missing=missing,
    )


def _median(distribution: Distribution | None) -> float | None:
    return None if distribution is None else distribution.median


def _p95(distribution: Distribution | None) -> float | None:
    return None if distribution is None else distribution.p95


def _iqr(distribution: Distribution | None) -> float | None:
    return None if distribution is None else distribution.iqr
