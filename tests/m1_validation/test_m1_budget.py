"""時間予算表の更新値（タスク 6.3、要件 11.1-11.8）。

本ファイルが固定するのは**更新値の算出**であって、文書の書き換えではない。
`docs/requirements.md §3` を書き換えるのは人であり、本コンポーネントは値を出す
までで止まる（design.md「BudgetUpdater」Risks）。したがって次の5点を特に厚く
固定する。

1. **ゲートが先である**（要件 11.1）。M1 実測7項目が揃っていない間は更新値を
   一切出さず、**欠測している項目を列挙して**返す。ゲートを外した実装、欠測の
   列挙を空にした実装、別の項目を挙げた実装はここで落ちる。
2. **区間3（予測確定〜移動体が動き出す）を勝手に埋めない。** 行は残し、実測値
   は欠測のまま、備考に「M3 で実測する」と注記する。0 や想定値で埋めた実装、
   注記を削った実装はここで落ちる。
3. **区間2 の読み替えが行の見出しに現れる**（要件 11.3）。「検出開始〜初回
   予測」であることと、元の表の「検出開始〜予測確定」からの読み替えであることの
   両方が読めなければならない。
4. **引き算の向きと対象**。移動体に残された時間は「総飛行時間 − （区間1＋
   区間2）」であり、投擲ごとに引く。向きを反転した実装、代表値どうしを引いた
   実装、引く区間を取り違えた実装はここで落ちる。
5. **公開フィールド・`evidence`・`criterion` は別の経路である**（タスク 6.2 の
   ★★ 教訓）。3経路すべてを値で固定する。

期待値はすべて**テスト局所のリテラル**から組む。実装の定数を import して自分
自身と比べる検査は置かない（タスク 4.5 の教訓）。分位点も式を書き写さず、
**数値のリテラル**で置いてある——式を写すと、実装と同じ取り違えをテストも
一緒に起こす。

設定値は**実装の既定値と重ならない値**にしてある（タスク 6.1 の教訓）。既定値
（区間3 の据え置き想定値 50 ms）は**禁止値**として否定照合にだけ使う。
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping, Sequence
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from m1_validation.config import BudgetConfig, M1Settings
from m1_validation.errors import M1ConfigError
from m1_validation.judgement.budget import (
    BudgetRow,
    BudgetUpdate,
    budget_criterion,
    compute_budget_update,
    missing_item_key,
)
from m1_validation.layout import ThrowLayout
from m1_validation.metrics.aggregate import (
    ITEM_AIM_ERROR_MM,
    ITEM_CONVERGED_AT,
    ITEM_DETECT_TO_FIRST_PREDICTION_MS,
    ITEM_HIT_ERROR_FINAL_MM,
    ITEM_HIT_ERROR_FIRST_MM,
    ITEM_KEYS,
    ITEM_RELEASE_TO_DETECT_MS,
    ITEM_TIME_ERROR_FINAL_MS,
    ITEM_TIME_ERROR_FIRST_MS,
    ITEM_TOTAL_FLIGHT_MS,
    ITEM_VALID_SAMPLES,
    Distribution,
    ThrowAggregate,
    ThrowRow,
)
from m1_validation.metrics.latency import LatencyResult, StageLatency

# ---------------------------------------------------------------------------
# テスト局所のリテラル（実装の定数を参照しない）
# ---------------------------------------------------------------------------

#: 判断の種類。`Judgement.question` に入る値。
QUESTION = "time-budget"

#: 判定値の2値。**両方を通す入力を必ず置く**（タスク 6.2 の教訓）。
UPDATABLE = "updatable"
NOT_UPDATABLE = "not_updatable"

# --- 設定（**既定値 50.0 と重ならない値**にする）---------------------------

#: 第1の設定点。区間3 の据え置き想定値。
SEG3_ASSUMED_1 = 70.0
#: 第2の設定点。
SEG3_ASSUMED_2 = 35.0
#: 実装の既定値。**期待値としては使わない**——禁止値として否定照合にだけ使う。
DEFAULT_SEG3_ASSUMED = 50.0

# --- 区間の識別子・見出し・想定値（docs/requirements.md §3 のまま）----------

SEGMENT_1 = "1"
SEGMENT_2 = "2"
SEGMENT_3 = "3"
SEGMENT_TOTAL = "total"

ASSUMED_1 = "0.05〜0.10 s"
ASSUMED_2 = "0.10〜0.15 s"
ASSUMED_3 = "0.05 s"
ASSUMED_TOTAL = "0.2〜0.3 s"
ASSUMED_FLIGHT = "0.6〜1.2 s"
ASSUMED_REMAINING = "0.3〜1.0 s"

# --- 見出し（**4行すべてを値で固定する**）----------------------------------
#
# 「互いに違う」ことしか見ない検査は値の固定ではない（空振り形2・形3）。
# 見出しを空文字へ潰す変異も、終点を別区間と取り違える変異も、合計行が
# 「区間1＋区間2＋区間3」と偽る変異も、distinct 検査だけでは全部生き残る。

LABEL_1 = "リリース〜検出開始"
LABEL_2 = "検出開始〜初回予測（元の表の「検出開始〜予測確定」からの読み替え）"
LABEL_3 = "予測確定〜移動体が動き出す"
LABEL_TOTAL = "オーバーヘッド合計（区間1＋区間2。区間3 を含まない）"

#: **合計行の見出しが偽ってはならない文面。** 読み手が最初に見るのは見出しで
#: あり、ここが「区間3 を含む」と言うと本タスクの中心規律が見出しの段で崩れる。
FORBIDDEN_TOTAL_LABEL_TEXT = "区間1＋区間2＋区間3"

# --- 備考（§3 の既存注記の写し。**要件 11.5 の唯一の固定**）----------------

#: §3 の備考「手・腕による遮蔽、検出の立ち上がり」を運んでいること。
NOTE_1 = (
    "手・腕による遮蔽、検出の立ち上がり（§3 の備考）。実測項目2 から算出する。"
    "§3 が「まったく未検証」と書いていた区間であり、M1 で最優先に実測する対象で"
    "ある。"
)

#: §3 の備考「サンプル取得（FR-1）＋フィッティング（FR-2）」を運んでいること。
#: 実測の基準文（`latency.first_prediction_basis`）は**この接尾に付く**。
NOTE_2_PREFIX = (
    "サンプル取得（FR-1）＋フィッティング（FR-2）（§3 の備考）。"
    "実測項目3 から算出する。終点は「予測確定」ではなく初回予測である。"
)

NOTE_3 = (
    "送信・受信・指令反映（NFR-3 に含む）（§3 の備考）。"
    "本区間は本 Spec の範囲外である——M1 に移動体は存在せず、"
    "送信・受信・指令反映のいずれも測れない。M3 で実測する。"
    "実測値は欠測のまま残し、0 でも想定値でも埋めない。"
)

NOTE_TOTAL = (
    "区間1と区間2 を投擲ごとに足し合わせた分布である。"
    "区間3 を含まないため、実際のオーバーヘッドの下側である。"
    "区間3 が実測される（M3）まで、合計は下側のままとして読むこと。"
    "なお §3 の想定値 0.2〜0.3 s の側は区間1＋区間2＋区間3 の和であり、"
    "区間3 を含む。"
    "想定と実測は同じ範囲を指していないので、そのまま引き比べないこと。"
)

# --- 出力に残す3注記（**全文を固定する**）----------------------------------

NOTE_REMAINING_TIME = (
    "移動体に残された時間は、投擲ごとに「総飛行時間 −（区間1＋区間2）」として"
    "算出した分布である。区間3（予測確定〜移動体が動き出す）を差し引いていない"
    "ため、実際に移動体へ残る時間の上側である。区間3 が M3 で実測されるまで、"
    "この値を移動体の持ち時間としてそのまま使わないこと。"
    "なお §3 の想定値 0.3〜1.0 s の側は区間3 を差し引いた後の値である。"
    "想定と実測は同じ範囲を指していないので、そのまま引き比べないこと。"
)

NOTE_PROVISIONAL_TARGET = (
    "更新後の予測レイテンシ（NFR-3）の値も暫定目標値であって合否条件ではない"
    "（要件 11.7、docs/requirements.md NFR-3 の但し書き）。"
    "最終的な合否は NFR-7（キャッチ成功率）で判定する。"
)

NOTE_COMPUTATION_ONLY = (
    "本コンポーネントは値を算出するだけであり、docs/requirements.md を"
    "書き換えない（要件 11.8、design.md「BudgetUpdater」Risks）。"
    "文書の更新は差分を目で確認するために人が行う。"
    "更新してよい対象は §3 の時間予算表とその導出値（NFR-3 の暫定目標）に"
    "限られ、他節には及ばない。"
    "既存の想定値・導出根拠・注記は削除しない（要件 11.5）。"
)

#: `docs/requirements.md` NFR-3 の現行値。**更新後の値がこれと同じになっては
#: ならない**（更新前の表から導出した実装を落とすための禁止値）。
OLD_LATENCY_TARGET_MS = 200.0

# --- 投擲ごとの実測値 -------------------------------------------------------
#
# **欠測の組み合わせを意図的にばらしてある。** 「試行数」の列が5つ（区間1 /
# 区間2 / オーバーヘッド合計 / 総飛行時間 / 残り時間）あり、**5つすべてが
# 別の値になる**ようにしている。どれか2つが同数だと、その2列を取り違えた実装が
# 素通りする（タスク 4.6「近いが別の軸」の教訓）。
#
#   区間1 = 6 / 区間2 = 7 / オーバーヘッド合計 = 5 / 総飛行時間 = 4 /
#   残り時間 = 3 / 投擲数 = 9 / 有効試行数 = 8

#: `(record_id, 区間1, 区間2, 総飛行時間)`。欠測は `None`。
THROWS: tuple[tuple[str, float | None, float | None, float | None], ...] = (
    # 3項目とも揃った投擲（残り時間の分布に入るのはこの3件だけ）
    ("throw-1", 60.0, 120.0, 800.0),
    ("throw-2", 80.0, 170.0, 1000.0),
    ("throw-3", 100.0, 110.0, 1300.0),
    # 区間1・区間2 は揃うが総飛行時間が無い（オーバーヘッド合計にだけ入る）
    ("throw-4", 70.0, 145.0, None),
    ("throw-5", 90.0, 150.0, None),
    # 区間1 だけ
    ("throw-6", 200.0, None, None),
    # 区間2 だけ
    ("throw-7", None, 160.0, None),
    ("throw-8", None, 190.0, None),
    # 総飛行時間だけ
    ("throw-9", None, None, 1500.0),
)

THROW_COUNT = 9
#: 有効試行数。**投擲数とも5つの試行数とも別の値**にしてある。
VALID_THROW_COUNT = 8

# --- 分布（**数値のリテラル**で置く。分位点の式を書き写さない）--------------
#
# 参考: 昇順の値に対する `(n-1)*q` の線形補間である。
#   区間1  [60, 70, 80, 90, 100, 200]        → 中央値 85、四分位 97.5-72.5、p95 175
#   区間2  [110, 120, 145, 150, 160, 170, 190] → 中央値 150、四分位 165-132.5、p95 184
#   総飛行 [800, 1000, 1300, 1500]           → 中央値 1150、四分位 1350-950、p95 1470

SEG1_COUNT = 6
SEG1_MISSING = 3
SEG1_MEDIAN = 85.0
SEG1_IQR = 25.0
SEG1_P95 = 175.0
SEG1_MIN = 60.0
SEG1_MAX = 200.0

SEG2_COUNT = 7
SEG2_MISSING = 2
SEG2_MEDIAN = 150.0
SEG2_IQR = 32.5
SEG2_P95 = 184.0
SEG2_MIN = 110.0
SEG2_MAX = 190.0

FLIGHT_COUNT = 4
FLIGHT_MISSING = 5
FLIGHT_MEDIAN = 1150.0
FLIGHT_IQR = 400.0
FLIGHT_P95 = 1470.0
FLIGHT_MIN = 800.0
FLIGHT_MAX = 1500.0

# 投擲ごとのオーバーヘッド（区間1＋区間2）: 180 / 250 / 210 / 215 / 240
#   昇順 [180, 210, 215, 240, 250] → 中央値 215、四分位範囲 240-210、p95 248
OVERHEAD_COUNT = 5
OVERHEAD_MISSING = 4
OVERHEAD_MEDIAN = 215.0
OVERHEAD_IQR = 30.0
OVERHEAD_P95 = 248.0
OVERHEAD_MIN = 180.0
OVERHEAD_MAX = 250.0

# 投擲ごとの残り時間（総飛行時間 − オーバーヘッド）: 620 / 750 / 1090
#   昇順 [620, 750, 1090] → 中央値 750、四分位範囲 920-685、p95 1056
REMAINING_COUNT = 3
REMAINING_MISSING = 6
REMAINING_MEDIAN = 750.0
REMAINING_IQR = 235.0
REMAINING_P95 = 1056.0
REMAINING_MIN = 620.0
REMAINING_MAX = 1090.0

#: **代表値どうしを引いた場合の値**（1150 − 215）。禁止値である。投擲ごとに
#: 引いた 750 と区別できる入力になっていることが、この定数の存在理由である。
MEDIAN_MINUS_MEDIAN = 935.0

# --- 導出値（要件 11.4）----------------------------------------------------

#: 区間2 の実測の上側（p95 = 184）＋ 区間3 の据え置き想定値（70）。
DERIVED_TARGET_1 = 254.0
#: 第2の設定点（184 + 35）。
DERIVED_TARGET_2 = 219.0

#: 禁止値の並び。左から順に
#: 「中央値を使った」「既定値 50 を使った」「区間1 を使った」
#: 「オーバーヘッド合計を使った」「更新前の表から導出した」。
FORBIDDEN_TARGETS = (
    SEG2_MEDIAN + SEG3_ASSUMED_1,  # 220.0
    SEG2_P95 + DEFAULT_SEG3_ASSUMED,  # 234.0
    SEG1_P95 + SEG3_ASSUMED_1,  # 245.0
    OVERHEAD_P95 + SEG3_ASSUMED_1,  # 318.0
    OLD_LATENCY_TARGET_MS,  # 200.0
)

# --- 欠測項目のキー（要件 11.1）--------------------------------------------

MISSING_KEY_FLIGHT = "item1:total_flight_ms"
MISSING_KEY_SEG1 = "item2:release_to_detect_ms"
MISSING_KEY_SEG2 = "item3:detect_to_first_prediction_ms"
MISSING_KEY_HIT_FIRST = "item4:hit_error_norm_first_mm"
MISSING_KEY_HIT_FINAL = "item4:hit_error_norm_final_mm"
MISSING_KEY_TIME_FIRST = "item5:time_error_first_ms"
MISSING_KEY_TIME_FINAL = "item5:time_error_final_ms"
MISSING_KEY_AIM = "item6:aim_error_mm"
MISSING_KEY_SAMPLES = "item7:valid_samples"
MISSING_KEY_CONVERGED = "item7:converged_at"

ALL_MISSING_KEYS = (
    MISSING_KEY_FLIGHT,
    MISSING_KEY_SEG1,
    MISSING_KEY_SEG2,
    MISSING_KEY_HIT_FIRST,
    MISSING_KEY_HIT_FINAL,
    MISSING_KEY_TIME_FIRST,
    MISSING_KEY_TIME_FINAL,
    MISSING_KEY_AIM,
    MISSING_KEY_SAMPLES,
    MISSING_KEY_CONVERGED,
)

# --- 逐次予測の基準文（**LatencyResult から運ばれる**）---------------------
#
# 実装の定数（`latency.FIRST_PREDICTION_BASIS`）を焼き付けた実装を落とすため、
# フィクスチャは**本物と違う文面**を渡す。

BASIS_1 = "＜テスト局所の基準文1: 区間2 は初回予測を終点とする＞"
BASIS_2 = "＜テスト局所の基準文2: 別の設定で撮った測定の基準文＞"

LOG_LINES_DROPPED = 3
LOG_LINES_SKIPPED = 7


# ---------------------------------------------------------------------------
# 小道具
# ---------------------------------------------------------------------------


def _layout_file(tmp_path: Path) -> Path:
    """設定解決に必要な最小のレイアウトファイル（要件 13.8）。"""
    path = tmp_path / "layout.json"
    path.write_text(
        json.dumps(
            {
                "format_version": "1.0",
                "layouts": [
                    {
                        "layout_id": "layout-budget-test",
                        "release_position_world_mm": [-1700.0, -50.0, 1690.0],
                        "release_height_mm": 1690.0,
                        "throw_direction_deg": 0.0,
                        "standby_position_world_mm": [1000.0, 1000.0],
                        "object_diameter_mm": 80.0,
                        "aperture_diameter_mm": 300.0,
                        "camera_position_world_mm": [0.0, -2500.0, 1200.0],
                        "notes": "テスト用の仮値（確定ではない）",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def layout() -> ThrowLayout:
    return ThrowLayout(
        layout_id="layout-budget-test",
        release_position_world_mm=(-1700.0, -50.0, 1690.0),
        release_height_mm=1690.0,
        throw_direction_deg=0.0,
        standby_position_world_mm=(1000.0, 1000.0),
        object_diameter_mm=80.0,
        aperture_diameter_mm=300.0,
        camera_position_world_mm=(0.0, -2500.0, 1200.0),
        notes="テスト用の仮値（確定ではない）",
    )


def settings(*, segment3_assumed_ms: float = SEG3_ASSUMED_1) -> M1Settings:
    return M1Settings(
        layout=layout(),
        budget=BudgetConfig(segment3_assumed_ms=segment3_assumed_ms),
    )


def dist(
    values: Sequence[float],
    *,
    median: float | None,
    p95: float | None,
    iqr: float | None,
    minimum: float | None,
    maximum: float | None,
    missing: int,
) -> Distribution:
    """分布1件。**期待値はリテラルで渡す**（式を写さない）。"""
    return Distribution(
        count=len(values),
        median=median,
        p95=p95,
        iqr=iqr,
        minimum=minimum,
        maximum=maximum,
        missing=missing,
    )


def seg1_dist() -> Distribution:
    return dist(
        [60.0, 70.0, 80.0, 90.0, 100.0, 200.0],
        median=SEG1_MEDIAN,
        p95=SEG1_P95,
        iqr=SEG1_IQR,
        minimum=SEG1_MIN,
        maximum=SEG1_MAX,
        missing=SEG1_MISSING,
    )


def seg2_dist() -> Distribution:
    return dist(
        [110.0, 120.0, 145.0, 150.0, 160.0, 170.0, 190.0],
        median=SEG2_MEDIAN,
        p95=SEG2_P95,
        iqr=SEG2_IQR,
        minimum=SEG2_MIN,
        maximum=SEG2_MAX,
        missing=SEG2_MISSING,
    )


def flight_dist() -> Distribution:
    return dist(
        [800.0, 1000.0, 1300.0, 1500.0],
        median=FLIGHT_MEDIAN,
        p95=FLIGHT_P95,
        iqr=FLIGHT_IQR,
        minimum=FLIGHT_MIN,
        maximum=FLIGHT_MAX,
        missing=FLIGHT_MISSING,
    )


def filler_dist(median: float) -> Distribution:
    """ゲートを通すためだけの分布（区間の行には現れない項目）。"""
    return Distribution(
        count=5,
        median=median,
        p95=median * 1.4,
        iqr=median * 0.3,
        minimum=median * 0.7,
        maximum=median * 1.6,
        missing=4,
    )


def empty_dist() -> Distribution:
    """1件も値が得られなかった項目。**0 で埋めない。**"""
    return Distribution(
        count=0,
        median=None,
        p95=None,
        iqr=None,
        minimum=None,
        maximum=None,
        missing=THROW_COUNT,
    )


def items(
    *, empty: Sequence[str] = (), absent: Sequence[str] = ()
) -> dict[str, Distribution]:
    """項目キーから分布へのマッピング。

    `empty` は「行はあるが値が1件も無い」項目、`absent` は「行そのものが無い」
    項目である。**2つは別の経路**であり、後者で 0 埋めする実装が生き残った
    前例がある（タスク 6.2 の教訓）。
    """
    table: dict[str, Distribution] = {
        ITEM_TOTAL_FLIGHT_MS: flight_dist(),
        ITEM_RELEASE_TO_DETECT_MS: seg1_dist(),
        ITEM_DETECT_TO_FIRST_PREDICTION_MS: seg2_dist(),
        ITEM_HIT_ERROR_FIRST_MM: filler_dist(120.0),
        ITEM_HIT_ERROR_FINAL_MM: filler_dist(75.0),
        ITEM_TIME_ERROR_FIRST_MS: filler_dist(18.0),
        ITEM_TIME_ERROR_FINAL_MS: filler_dist(9.0),
        ITEM_AIM_ERROR_MM: filler_dist(430.0),
        ITEM_VALID_SAMPLES: filler_dist(11.0),
        ITEM_CONVERGED_AT: filler_dist(6.0),
    }
    for key in empty:
        table[key] = empty_dist()
    for key in absent:
        del table[key]
    return table


def rows() -> tuple[ThrowRow, ...]:
    built: list[ThrowRow] = []
    for record_id, seg1, seg2, flight in THROWS:
        values: dict[str, float | None] = dict.fromkeys(ITEM_KEYS, None)
        values[ITEM_RELEASE_TO_DETECT_MS] = seg1
        values[ITEM_DETECT_TO_FIRST_PREDICTION_MS] = seg2
        values[ITEM_TOTAL_FLIGHT_MS] = flight
        built.append(
            ThrowRow(
                record_id=record_id,
                session_id="session-a",
                source="live",
                live=True,
                truth_available=flight is not None,
                error_vector_mm=None if flight is None else (12.0, -34.0),
                values=values,
            )
        )
    return tuple(built)


def aggregate(
    *,
    empty: Sequence[str] = (),
    absent: Sequence[str] = (),
    per_throw: Sequence[ThrowRow] | None = None,
    provisional: bool = False,
    verified: bool = True,
) -> ThrowAggregate:
    return ThrowAggregate(
        calibration_id="cal-budget-0001",
        verified=verified,
        session_ids=("session-a", "session-b", "session-c"),
        throw_count=THROW_COUNT,
        failed_throw_count=1,
        valid_throw_count=VALID_THROW_COUNT,
        live_throw_count=4,
        converged_count=5,
        not_converged_count=2,
        not_measurable_count=0,
        single_prediction_throw_count=0,
        provisional=provisional,
        provisional_reasons=("insufficient_valid_throws",) if provisional else (),
        items=items(empty=empty, absent=absent),
        error_vectors=((12.0, -34.0),),
        per_throw=rows() if per_throw is None else tuple(per_throw),
    )


def stage(name: str) -> StageLatency:
    return StageLatency(
        stage=name,
        event="update",
        field="total_ms",
        source="log",
        count=5,
        p50_ms=44.0,
        p95_ms=66.0,
        mean_ms=48.0,
        min_ms=30.0,
        max_ms=70.0,
    )


def latency(*, basis: str = BASIS_1) -> LatencyResult:
    return LatencyResult(
        definition="＜テスト局所の end-to-end 定義文＞",
        first_prediction_basis=basis,
        stage_note="＜テスト局所の段階の読み取り方＞",
        stages=(stage("predict"),),
        end_to_end=stage("predict"),
        detect_to_first_prediction=(),
        capture_fps=30.0,
        process_fps=28.0,
        cpu_percent_mean=61.0,
        rss_bytes_max=1234,
        frames_dropped=2,
        frames_missing=1,
        unknown_stages=(),
        foreign_prediction_events=0,
        unusable_prediction_events=0,
        log_lines_dropped=LOG_LINES_DROPPED,
        log_lines_skipped=LOG_LINES_SKIPPED,
    )


def update(**kwargs: object) -> BudgetUpdate:
    """既定の集計・第1の設定点で更新値を算出する。"""
    return compute_budget_update(
        aggregate(**kwargs),  # type: ignore[arg-type]
        latency(),
        settings=settings(),
    )


def row_of(result: BudgetUpdate, segment: str) -> BudgetRow:
    matches = [row for row in result.rows if row.segment == segment]
    assert len(matches) == 1, f"区間 {segment} の行がちょうど1つ無い"
    return matches[0]


def evidence(result: BudgetUpdate) -> Mapping[str, object]:
    return result.judgement.evidence


# ---------------------------------------------------------------------------
# ゲート（要件 11.1）
# ---------------------------------------------------------------------------


class TestGate:
    """**実測値が揃っていない間は更新値を出さない。**

    ゲートを外した実装（欠測があっても更新値を出す）と、欠測の列挙を空にした
    実装は、ここで落ちる。
    """

    def test_all_seven_items_present_opens_the_gate(self) -> None:
        result = update()
        assert result.ready is True
        assert result.missing_items == ()
        assert result.judgement.verdict == UPDATABLE

    def test_one_empty_item_closes_the_gate(self) -> None:
        """**行はあるが値が1件も無い**項目でも更新しない。"""
        result = update(empty=[ITEM_AIM_ERROR_MM])
        assert result.ready is False
        assert result.missing_items == (MISSING_KEY_AIM,)
        assert result.judgement.verdict == NOT_UPDATABLE

    def test_an_absent_row_also_closes_the_gate(self) -> None:
        """**行そのものが無い**項目も欠測である（0 で埋めない）。

        「値が `None`」と「キーが無い」は別の経路であり、後者だけを 0 で
        埋める実装が生き残った前例がある（タスク 6.2 の教訓）。
        """
        result = update(absent=[ITEM_CONVERGED_AT])
        assert result.ready is False
        assert result.missing_items == (MISSING_KEY_CONVERGED,)

    def test_missing_items_are_listed_in_item_order(self) -> None:
        """欠測項目は**すべて**列挙され、実測項目の番号順に並ぶ。"""
        result = update(
            empty=[ITEM_TIME_ERROR_FINAL_MS, ITEM_TOTAL_FLIGHT_MS],
            absent=[ITEM_VALID_SAMPLES],
        )
        assert result.missing_items == (
            MISSING_KEY_FLIGHT,
            MISSING_KEY_TIME_FINAL,
            MISSING_KEY_SAMPLES,
        )

    def test_every_measurement_column_can_be_reported_missing(self) -> None:
        """**10列すべてに欠測の名前がある。**

        覆い忘れた列は、欠測しても誰も気づかない領域になる。
        """
        result = compute_budget_update(
            aggregate(empty=list(ITEM_KEYS)), latency(), settings=settings()
        )
        assert result.missing_items == ALL_MISSING_KEYS

    def test_missing_item_names_are_all_distinct(self) -> None:
        """列ごとに別の名前が付く（同じ名前へ潰した実装を落とす）。"""
        assert len(set(ALL_MISSING_KEYS)) == len(ALL_MISSING_KEYS)

    def test_the_missing_item_key_names_both_the_item_and_the_column(self) -> None:
        """公開の組み立て関数も、番号と列名の両方を入れる。"""
        assert missing_item_key(4, "hit_error_norm_first_mm") == MISSING_KEY_HIT_FIRST
        assert missing_item_key(7, "converged_at") == MISSING_KEY_CONVERGED

    def test_the_item_numbers_are_not_swapped(self) -> None:
        """項目番号の取り違えを落とす。"""
        result = compute_budget_update(
            aggregate(empty=list(ITEM_KEYS)), latency(), settings=settings()
        )
        assert "item2:total_flight_ms" not in result.missing_items
        assert "item1:release_to_detect_ms" not in result.missing_items
        assert "item3:aim_error_mm" not in result.missing_items


class TestGateSuppressesTheUpdate:
    """ゲートが閉じている間、**更新値は一切出ない**（要件 11.1）。"""

    def result(self) -> BudgetUpdate:
        return update(empty=[ITEM_AIM_ERROR_MM])

    def test_no_row_carries_a_measured_value(self) -> None:
        """design.md の Postconditions: `ready is False` なら `measured` は全て `None`。"""
        result = self.result()
        assert [row.measured for row in result.rows] == [None, None, None, None]

    def test_no_row_carries_a_trial_count(self) -> None:
        result = self.result()
        assert [row.trials for row in result.rows] == [0, 0, 0, 0]

    def test_the_derived_values_are_missing_not_zero(self) -> None:
        """**0 で埋めない。** 「測っていない」と「0 だった」は別である。"""
        result = self.result()
        assert result.total_flight_ms is None
        assert result.remaining_time_ms is None
        assert result.derived_latency_target_ms is None

    def test_the_table_shape_survives_the_gate(self) -> None:
        """更新しなくても**行と想定値は残る**（要件 11.5: 注記を削除しない）。"""
        result = self.result()
        assert [row.segment for row in result.rows] == [
            SEGMENT_1,
            SEGMENT_2,
            SEGMENT_3,
            SEGMENT_TOTAL,
        ]
        assert [row.assumed for row in result.rows] == [
            ASSUMED_1,
            ASSUMED_2,
            ASSUMED_3,
            ASSUMED_TOTAL,
        ]

    def test_the_evidence_reports_missing_not_zero(self) -> None:
        payload = evidence(self.result())
        assert payload["ready"] is False
        assert payload["missing_items"] == [MISSING_KEY_AIM]
        assert payload["segment1_median_ms"] is None
        assert payload["segment2_median_ms"] is None
        assert payload["overhead_total_median_ms"] is None
        assert payload["total_flight_median_ms"] is None
        assert payload["remaining_time_median_ms"] is None
        assert payload["derived_latency_target_ms"] is None


# ---------------------------------------------------------------------------
# 行の構成（要件 11.2）
# ---------------------------------------------------------------------------


class TestRows:
    """各行に**想定値・実測の代表値・ばらつき・試行数**を並べる。"""

    def test_the_table_has_one_row_per_segment_plus_the_total(self) -> None:
        result = update()
        assert [row.segment for row in result.rows] == [
            SEGMENT_1,
            SEGMENT_2,
            SEGMENT_3,
            SEGMENT_TOTAL,
        ]

    def test_segment1_carries_its_own_measured_column(self) -> None:
        row = row_of(update(), SEGMENT_1)
        assert row.assumed == ASSUMED_1
        assert row.measured is not None
        assert row.measured.median == pytest.approx(SEG1_MEDIAN)
        assert row.measured.iqr == pytest.approx(SEG1_IQR)
        assert row.measured.count == SEG1_COUNT
        assert row.measured.missing == SEG1_MISSING
        assert row.trials == SEG1_COUNT

    def test_segment2_carries_its_own_measured_column(self) -> None:
        row = row_of(update(), SEGMENT_2)
        assert row.assumed == ASSUMED_2
        assert row.measured is not None
        assert row.measured.median == pytest.approx(SEG2_MEDIAN)
        assert row.measured.iqr == pytest.approx(SEG2_IQR)
        assert row.measured.count == SEG2_COUNT
        assert row.measured.missing == SEG2_MISSING
        assert row.trials == SEG2_COUNT

    def test_the_measured_columns_are_not_swapped(self) -> None:
        """**区間1 と区間2 の実測列の取り違え**を落とす。"""
        result = update()
        first = row_of(result, SEGMENT_1).measured
        second = row_of(result, SEGMENT_2).measured
        assert first is not None
        assert second is not None
        assert first.median != pytest.approx(SEG2_MEDIAN)
        assert second.median != pytest.approx(SEG1_MEDIAN)

    def test_the_trial_counts_are_not_swapped(self) -> None:
        """**試行数の列**も区間ごとに別である（6 と 5）。"""
        result = update()
        assert row_of(result, SEGMENT_1).trials != row_of(result, SEGMENT_2).trials

    def test_the_trial_count_is_not_the_throw_count(self) -> None:
        """試行数は**値が得られた投擲数**であり、投擲数でも有効試行数でもない。"""
        row = row_of(update(), SEGMENT_2)
        assert row.trials != THROW_COUNT
        assert row.trials != VALID_THROW_COUNT

    def test_the_spread_column_is_the_iqr_not_the_p95(self) -> None:
        """ばらつきの列を p95 へ差し替えた実装を落とす。"""
        row = row_of(update(), SEGMENT_2)
        assert row.measured is not None
        assert row.measured.iqr != pytest.approx(SEG2_P95)

    def test_the_assumed_column_is_not_replaced_by_the_measured_value(self) -> None:
        """**既存の想定値を実測値で置き換えない**（要件 11.5 / 11.6）。

        想定と実測は別の列であり、片方が他方を上書きすると
        「想定と食い違ったこと」自体が読めなくなる。
        """
        result = update()
        assert [row.assumed for row in result.rows] == [
            ASSUMED_1,
            ASSUMED_2,
            ASSUMED_3,
            ASSUMED_TOTAL,
        ]

    def test_the_assumed_values_are_all_distinct(self) -> None:
        """4つの想定値が互いに違う（同じ文字列へ潰した実装を落とす）。"""
        assumed = [row.assumed for row in update().rows]
        assert len(set(assumed)) == len(assumed)

    def test_the_notes_are_all_distinct(self) -> None:
        notes = [row.note for row in update().rows]
        assert len(set(notes)) == len(notes)

    def test_the_labels_are_all_distinct(self) -> None:
        labels = [row.label for row in update().rows]
        assert len(set(labels)) == len(labels)


class TestRowHeadingsAndNotes:
    """**見出しと備考を値で固定する。**

    「互いに違う」ことしか見ない検査は値の固定ではない（空振り形2・形3）。
    見出しを空文字へ潰す変異・終点を別区間と取り違える変異・合計行が
    「区間3 を含む」と偽る変異・§3 の既存備考を無内容へ潰す変異は、
    distinct 検査だけでは全部生き残る。
    """

    def test_every_label_is_fixed_by_value(self) -> None:
        assert [row.label for row in update().rows] == [
            LABEL_1,
            LABEL_2,
            LABEL_3,
            LABEL_TOTAL,
        ]

    def test_no_label_is_empty(self) -> None:
        for row in update().rows:
            assert row.label.strip(), row.segment

    def test_the_labels_survive_a_closed_gate(self) -> None:
        """更新しなくても見出しは残る（要件 11.5）。"""
        assert [row.label for row in update(empty=[ITEM_AIM_ERROR_MM]).rows] == [
            LABEL_1,
            LABEL_2,
            LABEL_3,
            LABEL_TOTAL,
        ]

    def test_the_total_label_does_not_claim_to_include_segment3(self) -> None:
        """**読み手が最初に見るのは見出しである。**

        備考の「下側」だけを固定しても、同じ行の見出しが
        「区間1＋区間2＋区間3」と言えば規律は見出しの段で崩れる。
        """
        label = row_of(update(), SEGMENT_TOTAL).label
        assert FORBIDDEN_TOTAL_LABEL_TEXT not in label
        assert "区間3 を含まない" in label

    def test_the_segment1_label_does_not_borrow_another_end_point(self) -> None:
        """区間1 の終点は「検出開始」であり「予測確定」ではない。"""
        label = row_of(update(), SEGMENT_1).label
        assert "検出開始" in label
        assert "予測確定" not in label

    def test_every_note_is_fixed_by_value(self) -> None:
        """4行の備考を**全文で**固定する。"""
        result = update()
        assert row_of(result, SEGMENT_1).note == NOTE_1
        assert row_of(result, SEGMENT_2).note == NOTE_2_PREFIX + BASIS_1
        assert row_of(result, SEGMENT_3).note == NOTE_3
        assert row_of(result, SEGMENT_TOTAL).note == NOTE_TOTAL

    def test_the_segment1_note_carries_the_original_remark(self) -> None:
        """§3 の備考を運んでいる（要件 11.5: 既存の注記を削除しない）。"""
        note = row_of(update(), SEGMENT_1).note
        assert "手・腕による遮蔽" in note
        assert "検出の立ち上がり" in note

    def test_the_segment2_note_carries_the_original_remark(self) -> None:
        """§3 の備考は基準文の**接頭**として残る。"""
        note = row_of(update(), SEGMENT_2).note
        assert "サンプル取得（FR-1）＋フィッティング（FR-2）" in note
        assert note.startswith(NOTE_2_PREFIX)

    def test_the_segment3_note_carries_the_original_remark(self) -> None:
        note = row_of(update(), SEGMENT_3).note
        assert "送信・受信・指令反映（NFR-3 に含む）" in note

    def test_the_original_remarks_do_not_leak_between_rows(self) -> None:
        """**備考の取り違え**を落とす。"""
        result = update()
        assert "手・腕による遮蔽" not in row_of(result, SEGMENT_2).note
        assert "手・腕による遮蔽" not in row_of(result, SEGMENT_3).note
        assert "手・腕による遮蔽" not in row_of(result, SEGMENT_TOTAL).note
        assert "サンプル取得（FR-1）" not in row_of(result, SEGMENT_1).note
        assert "サンプル取得（FR-1）" not in row_of(result, SEGMENT_TOTAL).note
        assert "送信・受信・指令反映" not in row_of(result, SEGMENT_1).note

    def test_the_notes_survive_a_closed_gate(self) -> None:
        result = update(empty=[ITEM_AIM_ERROR_MM])
        assert row_of(result, SEGMENT_1).note == NOTE_1
        assert row_of(result, SEGMENT_TOTAL).note == NOTE_TOTAL


# ---------------------------------------------------------------------------
# 区間2 の読み替え（要件 11.3）
# ---------------------------------------------------------------------------


class TestSegment2IsRelabelled:
    """区間2 は「検出開始〜**初回予測**」と読み替える。**見出しに反映する。**"""

    def test_the_label_names_the_first_prediction_as_the_end_point(self) -> None:
        assert "検出開始〜初回予測" in row_of(update(), SEGMENT_2).label

    def test_the_label_says_it_is_a_re_reading_of_the_original(self) -> None:
        """元の定義と違うことが**読み手に分かる形**であること。

        「検出開始〜初回予測」とだけ書くと、元の表がそう書いていたのか
        読み替えたのかが分からない。
        """
        label = row_of(update(), SEGMENT_2).label
        assert "検出開始〜予測確定" in label
        assert "読み替え" in label

    def test_the_relabelling_does_not_leak_into_other_rows(self) -> None:
        """**別の区間の見出しと取り違えない。**"""
        result = update()
        assert "検出開始〜初回予測" not in row_of(result, SEGMENT_1).label
        assert "検出開始〜初回予測" not in row_of(result, SEGMENT_3).label
        assert row_of(result, SEGMENT_3).label == "予測確定〜移動体が動き出す"

    def test_the_note_carries_the_sequential_prediction_basis(self) -> None:
        """逐次予測が前提である旨は、**同じ測定が申告した基準文**を運ぶ。

        基準文を実装へ焼き付けると、実測項目3 の基準と表の注記が食い違い得る。
        """
        assert BASIS_1 in row_of(update(), SEGMENT_2).note

    def test_a_second_measurement_changes_the_basis_text(self) -> None:
        """**焼き付けた実装を落とす。**"""
        result = compute_budget_update(
            aggregate(), latency(basis=BASIS_2), settings=settings()
        )
        note = row_of(result, SEGMENT_2).note
        assert BASIS_2 in note
        assert BASIS_1 not in note

    def test_the_basis_does_not_leak_into_other_rows(self) -> None:
        result = update()
        for segment in (SEGMENT_1, SEGMENT_3, SEGMENT_TOTAL):
            assert BASIS_1 not in row_of(result, segment).note

    def test_the_basis_is_also_in_the_evidence(self) -> None:
        assert evidence(update())["first_prediction_basis"] == BASIS_1


# ---------------------------------------------------------------------------
# 区間3 は本 Spec の範囲外（勝手に埋めない）
# ---------------------------------------------------------------------------


class TestSegment3StaysUnmeasured:
    """区間3 は**行を残して未実測のまま**にし、「M3 で実測する」と注記する。"""

    def test_the_row_exists(self) -> None:
        row = row_of(update(), SEGMENT_3)
        assert row.label == "予測確定〜移動体が動き出す"
        assert row.assumed == ASSUMED_3

    def test_the_measured_value_stays_missing_even_when_the_gate_is_open(self) -> None:
        """**0 でも想定値でも埋めない。** ゲートが開いていても欠測のままである。"""
        result = update()
        assert result.ready is True
        assert row_of(result, SEGMENT_3).measured is None

    def test_the_trial_count_is_zero_because_nothing_was_measured(self) -> None:
        assert row_of(update(), SEGMENT_3).trials == 0

    def test_the_note_says_it_is_measured_in_m3(self) -> None:
        note = row_of(update(), SEGMENT_3).note
        assert "M3 で実測する" in note
        assert "本 Spec の範囲外" in note

    def test_the_note_forbids_filling_it_in(self) -> None:
        assert "埋めない" in row_of(update(), SEGMENT_3).note

    def test_the_note_is_also_in_the_evidence(self) -> None:
        payload = evidence(update())
        assert payload["segment3_measured_ms"] is None
        assert "M3 で実測する" in str(payload["segment3_note"])

    def test_the_m3_note_does_not_leak_into_other_rows(self) -> None:
        result = update()
        for segment in (SEGMENT_1, SEGMENT_2, SEGMENT_TOTAL):
            assert "M3 で実測する" not in row_of(result, segment).note


# ---------------------------------------------------------------------------
# オーバーヘッド合計と移動体に残された時間
# ---------------------------------------------------------------------------


class TestOverheadTotal:
    """オーバーヘッド合計は**区間1＋区間2 を投擲ごとに足した**分布である。"""

    def test_the_total_is_computed_per_throw(self) -> None:
        row = row_of(update(), SEGMENT_TOTAL)
        assert row.measured is not None
        assert row.measured.median == pytest.approx(OVERHEAD_MEDIAN)
        assert row.measured.iqr == pytest.approx(OVERHEAD_IQR)
        assert row.measured.p95 == pytest.approx(OVERHEAD_P95)
        assert row.measured.minimum == pytest.approx(OVERHEAD_MIN)
        assert row.measured.maximum == pytest.approx(OVERHEAD_MAX)
        assert row.measured.count == OVERHEAD_COUNT
        assert row.measured.missing == OVERHEAD_MISSING
        assert row.trials == OVERHEAD_COUNT

    def test_the_total_is_not_the_sum_of_the_medians(self) -> None:
        """代表値どうしを足した値（85 + 140 = 225）とは違う。"""
        row = row_of(update(), SEGMENT_TOTAL)
        assert row.measured is not None
        assert row.measured.median != pytest.approx(SEG1_MEDIAN + SEG2_MEDIAN)

    def test_the_total_does_not_include_segment3(self) -> None:
        """**区間3 を足し込まない**（据え置きの想定値でも埋めない）。"""
        row = row_of(update(), SEGMENT_TOTAL)
        assert row.measured is not None
        assert row.measured.median != pytest.approx(OVERHEAD_MEDIAN + SEG3_ASSUMED_1)

    def test_the_note_says_the_total_is_a_lower_bound(self) -> None:
        """区間3 を含まないので**実際のオーバーヘッドの下側**である。"""
        assert "下側" in row_of(update(), SEGMENT_TOTAL).note

    def test_the_note_says_the_assumed_side_includes_segment3(self) -> None:
        """**想定列と実測列で区間3 の扱いが違う**ことを出力に残す。

        §3 の想定値 0.2〜0.3 s は区間1＋区間2＋区間3 の和である。実測側が
        区間3 を含まないことだけを書くと、要件 11.2 の「想定値と実測を並べる」が
        **そのまま比較可能に見えてしまう**。
        """
        note = row_of(update(), SEGMENT_TOTAL).note
        assert "§3 の想定値 0.2〜0.3 s の側は区間1＋区間2＋区間3 の和であり" in note
        assert "そのまま引き比べないこと" in note


class TestTotalFlightAndRemainingTime:
    """総飛行時間と、そこからオーバーヘッドを引いた**移動体に残された時間**。"""

    def test_the_total_flight_time_comes_from_measurement_item_one(self) -> None:
        result = update()
        assert result.total_flight_ms is not None
        assert result.total_flight_ms.median == pytest.approx(FLIGHT_MEDIAN)
        assert result.total_flight_ms.iqr == pytest.approx(FLIGHT_IQR)
        assert result.total_flight_ms.count == FLIGHT_COUNT

    def test_the_remaining_time_is_the_flight_minus_the_overhead(self) -> None:
        result = update()
        assert result.remaining_time_ms is not None
        assert result.remaining_time_ms.median == pytest.approx(REMAINING_MEDIAN)
        assert result.remaining_time_ms.iqr == pytest.approx(REMAINING_IQR)
        assert result.remaining_time_ms.p95 == pytest.approx(REMAINING_P95)
        assert result.remaining_time_ms.minimum == pytest.approx(REMAINING_MIN)
        assert result.remaining_time_ms.maximum == pytest.approx(REMAINING_MAX)
        assert result.remaining_time_ms.count == REMAINING_COUNT
        assert result.remaining_time_ms.missing == REMAINING_MISSING

    def test_the_subtraction_runs_per_throw_not_on_the_medians(self) -> None:
        """**代表値どうしの引き算（1000 − 210 = 790）とは違う値になる入力。**

        投擲ごとに引くのは、区間ごとの代表値が同じ投擲から来るとは限らない
        ためである。この対照が無いと、どちらの実装も区別が付かない。
        """
        result = update()
        assert result.remaining_time_ms is not None
        assert result.remaining_time_ms.median != pytest.approx(MEDIAN_MINUS_MEDIAN)

    def test_the_subtraction_is_not_reversed(self) -> None:
        """**引く向き**。オーバーヘッドから総飛行時間を引くと負になる。"""
        result = update()
        assert result.remaining_time_ms is not None
        assert result.remaining_time_ms.minimum is not None
        assert result.remaining_time_ms.minimum > 0.0
        assert result.remaining_time_ms.median != pytest.approx(-REMAINING_MEDIAN)

    def test_the_remaining_time_is_smaller_than_the_flight_time(self) -> None:
        result = update()
        assert result.total_flight_ms is not None
        assert result.remaining_time_ms is not None
        assert result.total_flight_ms.median is not None
        assert result.remaining_time_ms.median is not None
        assert result.remaining_time_ms.median < result.total_flight_ms.median

    def test_only_throws_with_all_three_values_enter(self) -> None:
        """3項目が揃った投擲だけが分布へ入る。

        **5つの試行数はすべて別の値である**（区間1=6 / 区間2=7 /
        オーバーヘッド合計=5 / 総飛行時間=4 / 残り時間=3）。どれか2つが同数だと、
        その2列を取り違えた実装が素通りする。
        """
        result = update()
        assert result.remaining_time_ms is not None
        assert result.remaining_time_ms.count == REMAINING_COUNT
        assert result.remaining_time_ms.count != SEG1_COUNT
        assert result.remaining_time_ms.count != SEG2_COUNT
        assert result.remaining_time_ms.count != OVERHEAD_COUNT
        assert result.remaining_time_ms.count != FLIGHT_COUNT

    def test_the_five_trial_counts_are_pairwise_distinct(self) -> None:
        """フィクスチャが取り違えを露見させる形になっていることの自己検査。"""
        counts = (
            SEG1_COUNT,
            SEG2_COUNT,
            OVERHEAD_COUNT,
            FLIGHT_COUNT,
            REMAINING_COUNT,
            THROW_COUNT,
            VALID_THROW_COUNT,
        )
        assert len(set(counts)) == len(counts)

    def test_an_empty_per_throw_table_yields_missing_not_zero(self) -> None:
        """投擲ごとの行が無ければ、導出値は**欠測**である（0 で埋めない）。"""
        result = compute_budget_update(
            aggregate(per_throw=()), latency(), settings=settings()
        )
        assert result.remaining_time_ms is not None
        assert result.remaining_time_ms.count == 0
        assert result.remaining_time_ms.median is None
        total = row_of(result, SEGMENT_TOTAL)
        assert total.measured is not None
        assert total.measured.median is None

    def test_the_note_says_the_remaining_time_is_an_upper_bound(self) -> None:
        """区間3 を引いていないので**実際に残る時間の上側**である。"""
        note = update().remaining_time_note
        assert "上側" in note
        assert "区間3" in note

    def test_the_note_says_the_assumed_side_already_subtracts_segment3(self) -> None:
        """§3 の想定値 0.3〜1.0 s は**区間3 を差し引いた後**の値である。"""
        note = update().remaining_time_note
        assert "§3 の想定値 0.3〜1.0 s の側は区間3 を差し引いた後の値である" in note
        assert "そのまま引き比べないこと" in note

    def test_the_assumed_ranges_are_kept_beside_the_derived_values(self) -> None:
        """総飛行時間と残り時間にも**既存の想定値**を併記する（要件 11.2）。"""
        result = update()
        assert result.total_flight_assumed == ASSUMED_FLIGHT
        assert result.remaining_time_assumed == ASSUMED_REMAINING
        assert result.total_flight_assumed != result.remaining_time_assumed


# ---------------------------------------------------------------------------
# 導出値（要件 11.4）
# ---------------------------------------------------------------------------


class TestDerivedLatencyTarget:
    """予測レイテンシの暫定目標を、**更新後の表と整合する値**として算出する。"""

    def test_it_is_the_upper_side_of_segment2_plus_the_carried_segment3(self) -> None:
        assert update().derived_latency_target_ms == pytest.approx(DERIVED_TARGET_1)

    @pytest.mark.parametrize("forbidden", FORBIDDEN_TARGETS)
    def test_it_is_not_derived_from_the_wrong_column(self, forbidden: float) -> None:
        """中央値・既定値・区間1・合計・更新前の表のいずれからも来ていない。"""
        value = update().derived_latency_target_ms
        assert value is not None
        assert value != pytest.approx(forbidden)

    def test_a_second_settings_point_moves_the_target(self) -> None:
        """**設定を無視して既定値を使う実装**をここで落とす。"""
        result = compute_budget_update(
            aggregate(),
            latency(),
            settings=settings(segment3_assumed_ms=SEG3_ASSUMED_2),
        )
        assert result.derived_latency_target_ms == pytest.approx(DERIVED_TARGET_2)
        assert result.segment3_assumed_ms == pytest.approx(SEG3_ASSUMED_2)

    def test_the_carried_segment3_value_is_exposed(self) -> None:
        """据え置いた想定値そのものも公開する（何を足したのか読めるように）。"""
        result = update()
        assert result.segment3_assumed_ms == pytest.approx(SEG3_ASSUMED_1)
        assert result.segment3_assumed_ms != pytest.approx(DEFAULT_SEG3_ASSUMED)

    def test_the_target_is_not_rounded(self) -> None:
        """丸めると「更新後の表と食い違わない」が崩れる。"""
        value = update().derived_latency_target_ms
        assert value is not None
        assert value != pytest.approx(250.0)
        assert value != pytest.approx(260.0)

    def test_the_evidence_carries_the_target_and_its_parts(self) -> None:
        payload = evidence(update())
        assert payload["derived_latency_target_ms"] == pytest.approx(DERIVED_TARGET_1)
        assert payload["segment2_p95_ms"] == pytest.approx(SEG2_P95)
        assert payload["segment3_assumed_ms"] == pytest.approx(SEG3_ASSUMED_1)


# ---------------------------------------------------------------------------
# 判断（公開フィールド・evidence・criterion の3経路）
# ---------------------------------------------------------------------------


class TestJudgement:
    def test_the_question_is_the_time_budget(self) -> None:
        assert update().judgement.question == QUESTION

    def test_both_verdicts_are_reachable(self) -> None:
        """**真偽値・列挙は両方の値で固定する**（タスク 6.2 の教訓）。"""
        assert update().judgement.verdict == UPDATABLE
        assert update(empty=[ITEM_AIM_ERROR_MM]).judgement.verdict == NOT_UPDATABLE

    def test_the_two_verdicts_differ(self) -> None:
        assert UPDATABLE != NOT_UPDATABLE

    def test_the_rationale_is_mutually_exclusive(self) -> None:
        """更新した理由と更新しなかった理由は**取り違えられない**文面である。"""
        ready = update().judgement.rationale
        blocked = update(empty=[ITEM_AIM_ERROR_MM]).judgement.rationale
        assert "更新値を算出した" in ready
        assert "更新値を出さない" not in ready
        assert "更新値を出さない" in blocked
        assert "更新値を算出した" not in blocked

    def test_the_blocked_rationale_names_the_missing_items(self) -> None:
        rationale = update(empty=[ITEM_AIM_ERROR_MM]).judgement.rationale
        assert MISSING_KEY_AIM in rationale

    def test_the_evidence_carries_the_measured_columns(self) -> None:
        payload = evidence(update())
        assert payload["segment1_median_ms"] == pytest.approx(SEG1_MEDIAN)
        assert payload["segment1_iqr_ms"] == pytest.approx(SEG1_IQR)
        assert payload["segment1_trials"] == SEG1_COUNT
        assert payload["segment2_median_ms"] == pytest.approx(SEG2_MEDIAN)
        assert payload["segment2_iqr_ms"] == pytest.approx(SEG2_IQR)
        assert payload["segment2_trials"] == SEG2_COUNT
        assert payload["overhead_total_median_ms"] == pytest.approx(OVERHEAD_MEDIAN)
        assert payload["overhead_total_iqr_ms"] == pytest.approx(OVERHEAD_IQR)
        assert payload["overhead_total_trials"] == OVERHEAD_COUNT
        assert payload["total_flight_median_ms"] == pytest.approx(FLIGHT_MEDIAN)
        assert payload["total_flight_iqr_ms"] == pytest.approx(FLIGHT_IQR)
        assert payload["remaining_time_median_ms"] == pytest.approx(REMAINING_MEDIAN)
        assert payload["remaining_time_iqr_ms"] == pytest.approx(REMAINING_IQR)

    def test_the_evidence_columns_are_not_swapped(self) -> None:
        payload = evidence(update())
        assert payload["segment1_median_ms"] != pytest.approx(SEG2_MEDIAN)
        assert payload["segment2_median_ms"] != pytest.approx(SEG1_MEDIAN)
        assert payload["total_flight_median_ms"] != pytest.approx(REMAINING_MEDIAN)

    def test_the_evidence_carries_the_assumed_column(self) -> None:
        """**想定値は証跡にも残る**（要件 11.5）。"""
        payload = evidence(update())
        assert payload["assumed_segment1"] == ASSUMED_1
        assert payload["assumed_segment2"] == ASSUMED_2
        assert payload["assumed_segment3"] == ASSUMED_3
        assert payload["assumed_overhead_total"] == ASSUMED_TOTAL
        assert payload["assumed_total_flight"] == ASSUMED_FLIGHT
        assert payload["assumed_remaining_time"] == ASSUMED_REMAINING

    def test_the_evidence_carries_the_relabelled_segment2_heading(self) -> None:
        label = str(evidence(update())["segment2_label"])
        assert label == LABEL_2
        assert "検出開始〜初回予測" in label
        assert "読み替え" in label

    def test_the_evidence_carries_the_segment3_note_in_full(self) -> None:
        assert evidence(update())["segment3_note"] == NOTE_3

    def test_the_evidence_carries_the_derived_trial_counts(self) -> None:
        """5つの試行数を**それぞれ独立に**固定する。"""
        payload = evidence(update())
        assert payload["segment1_trials"] == SEG1_COUNT
        assert payload["segment2_trials"] == SEG2_COUNT
        assert payload["overhead_total_trials"] == OVERHEAD_COUNT
        assert payload["total_flight_trials"] == FLIGHT_COUNT
        assert payload["remaining_time_trials"] == REMAINING_COUNT
        assert payload["overhead_total_trials"] != THROW_COUNT

    def test_the_evidence_identifies_the_calibration(self) -> None:
        payload = evidence(update())
        assert payload["calibration_id"] == "cal-budget-0001"
        assert payload["throw_count"] == THROW_COUNT
        assert payload["valid_throw_count"] == VALID_THROW_COUNT
        assert payload["session_count"] == 3

    @pytest.mark.parametrize("verified", [True, False])
    def test_the_verification_state_is_carried_both_ways(self, verified: bool) -> None:
        """**真偽値は両方の値を通す入力を置く**（タスク 6.2 の教訓）。

        未検証キャリブレーションで得た値で表を更新したという事実が、
        レポートから消えてはならない（要件 2.2）。
        """
        result = compute_budget_update(
            aggregate(verified=verified), latency(), settings=settings()
        )
        assert evidence(result)["verified"] is verified

    @pytest.mark.parametrize("provisional", [True, False])
    def test_the_aggregate_provisional_flag_is_carried_both_ways(
        self, provisional: bool
    ) -> None:
        result = compute_budget_update(
            aggregate(provisional=provisional), latency(), settings=settings()
        )
        assert evidence(result)["aggregate_provisional"] is provisional

    def test_the_evidence_carries_the_log_health(self) -> None:
        payload = evidence(update())
        assert payload["log_lines_dropped"] == LOG_LINES_DROPPED
        assert payload["log_lines_skipped"] == LOG_LINES_SKIPPED

    def test_the_log_health_fields_are_not_swapped(self) -> None:
        payload = evidence(update())
        assert payload["log_lines_dropped"] != LOG_LINES_SKIPPED

    def test_the_evidence_is_json_serialisable(self) -> None:
        assert json.loads(json.dumps(evidence(update()), ensure_ascii=False))


class TestProvisionalFlag:
    """暫定の印は**2項がそれぞれ単独で効く**（タスク 6.1 の教訓）。"""

    def test_the_baseline_is_not_provisional(self) -> None:
        assert update().judgement.provisional is False

    def test_a_closed_gate_alone_raises_the_flag(self) -> None:
        result = update(empty=[ITEM_AIM_ERROR_MM])
        assert result.ready is False
        assert result.judgement.provisional is True

    def test_a_provisional_aggregate_alone_raises_the_flag(self) -> None:
        result = update(provisional=True)
        assert result.ready is True
        assert result.judgement.provisional is True


# ---------------------------------------------------------------------------
# 注記（要件 11.7 / 11.8。**公開フィールドと証跡の両方**）
# ---------------------------------------------------------------------------


class TestNotes:
    def test_the_target_stays_a_provisional_goal(self) -> None:
        """更新後も**合否条件ではなく暫定目標値**である旨を維持する（要件 11.7）。"""
        note = update().provisional_target_note
        assert "暫定目標値" in note
        assert "合否条件ではない" in note

    def test_the_component_only_computes_values(self) -> None:
        """**文書の書き換えは行わない**（要件 11.8、design.md Risks）。"""
        note = update().computation_only_note
        assert "算出するだけ" in note
        assert "書き換えない" in note

    def test_the_update_scope_is_limited_to_the_budget_table(self) -> None:
        """更新対象は時間予算表とその導出値に限る（要件 11.8）。"""
        assert "他節" in update().computation_only_note

    def test_the_three_notes_are_fixed_by_value(self) -> None:
        """**全文で固定する。** 部分照合だけでは別の文面へ差し替えても通る。"""
        result = update()
        assert result.remaining_time_note == NOTE_REMAINING_TIME
        assert result.provisional_target_note == NOTE_PROVISIONAL_TARGET
        assert result.computation_only_note == NOTE_COMPUTATION_ONLY

    def test_the_three_notes_are_distinct(self) -> None:
        result = update()
        notes = {
            result.remaining_time_note,
            result.provisional_target_note,
            result.computation_only_note,
        }
        assert len(notes) == 3

    def test_the_notes_are_also_in_the_evidence(self) -> None:
        """**公開フィールドと証跡は別の経路である**（タスク 6.2 の教訓）。"""
        result = update()
        payload = evidence(result)
        assert payload["remaining_time_note"] == result.remaining_time_note
        assert payload["provisional_target_note"] == result.provisional_target_note
        assert payload["computation_only_note"] == result.computation_only_note

    def test_the_notes_are_not_empty(self) -> None:
        result = update()
        for note in (
            result.remaining_time_note,
            result.provisional_target_note,
            result.computation_only_note,
        ):
            assert note.strip()

    def test_the_notes_survive_a_closed_gate(self) -> None:
        """更新しなくても注記は残る（要件 11.5）。"""
        result = update(empty=[ITEM_AIM_ERROR_MM])
        assert result.provisional_target_note
        assert result.computation_only_note

    def test_the_docstring_says_the_component_does_not_rewrite_the_document(
        self,
    ) -> None:
        """**docstring に明記する**ことが本タスクの成果物の一部である。

        文書化の要求に対して打てる唯一の検査である（タスク 1.4 と同型）。
        """
        doc = compute_budget_update.__doc__ or ""
        assert "算出するだけ" in doc
        assert "書き換えない" in doc


# ---------------------------------------------------------------------------
# 判定規則の説明文（**全文を固定する**。タスク 6.1 の教訓）
# ---------------------------------------------------------------------------


def expected_criterion_sentences(*, seg3: str) -> tuple[str, ...]:
    """規則の説明文の**期待される全文**を、一文ずつテスト局所のリテラルで書く。

    ここが本ファイルで最も load-bearing な定数である。覆い忘れた文は、削っても
    取り違えても誰も気づかない領域になる。したがって全文を覆い、**連結が
    criterion と厳密に一致する**ことまで固定する（順序の入れ替えも、文の追加も、
    これで落ちる）。

    **実装の私有定数を import して自分自身と比べてはならない**（空振り形3）。
    """
    return (
        # 見出し
        (
            "時間予算表（docs/requirements.md §3）の更新値の算出規則"
            "（実測前に固定。design.md「BudgetUpdater」、要件 11.1-11.8）: "
        ),
        # 判定値の語彙
        (
            "判定値は「updatable（更新値を算出した）」と"
            "「not_updatable（実測値が揃わず更新しない）」の2値である。"
        ),
        # ゲート（要件 11.1）
        (
            "【ゲート】M1 実測7項目のいずれかが欠測している間は、"
            "時間予算表の更新値を一切出さない（要件 11.1）。"
            "欠測している列を item<実測項目の番号>:<項目キー> の形で列挙して返し、"
            "各行の実測値は欠測のままにする。"
        ),
        # 行の構成（要件 11.2）
        (
            "【行の構成】各区間の行に、既存の想定値・実測の代表値（中央値）・"
            "ばらつき（四分位範囲）・試行数を並べる（要件 11.2）。"
            "代表値に平均を採らない——投擲はばらつきが大きく外れ値が出やすい。"
            "試行数はその区間の値が得られた投擲数であり、投擲数でも有効試行数でもない。"
        ),
        # 区間2 の読み替え（要件 11.3）
        (
            "【区間2 の読み替え】区間2 は「検出開始〜初回予測」と読み替える"
            "（要件 11.3）。"
            "予測コアは最小サンプル数に達した時点で初回予測を出し、以降サンプルが"
            "増えるたびに更新する逐次予測であり、元の表が前提にしている"
            "「予測が1回確定して終わり」という単発予測モデルとは別物である。"
            "読み替えたことを行の見出しと備考の両方に残す。"
        ),
        # 区間3（本 Spec の範囲外）
        (
            "【区間3】区間3（予測確定〜移動体が動き出す）は本 Spec の範囲外である。"
            "M1 に移動体は存在せず、送信・受信・指令反映のいずれも測れないので、"
            "行は残したうえで実測値を欠測のままとし、備考に M3 で実測すると注記する。"
            "0 でも想定値でも埋めない。"
        ),
        # オーバーヘッド合計
        (
            "【オーバーヘッド合計】オーバーヘッド合計は、"
            "投擲ごとに区間1と区間2の実測値を足した分布とする"
            "（区間ごとの代表値を足すのではない——代表値が同じ投擲から来るとは"
            "限らない）。"
            "区間3 を含まないため、実際のオーバーヘッドの下側である。"
        ),
        # 移動体に残された時間
        (
            "【移動体に残された時間】移動体に残された時間は、"
            "投擲ごとに総飛行時間から同じ投擲の区間1と区間2の実測値を引いた分布と"
            "する。3つの実測値が揃った投擲だけが分布に入る。"
            "区間3 を差し引いていないため、実際に移動体へ残る時間の上側である。"
        ),
        # 想定側との比較（想定列と実測列で区間3 の扱いが違う）
        (
            "【想定側との比較】§3 の想定値の側は、"
            "オーバーヘッド合計（0.2〜0.3 s）が区間1＋区間2＋区間3 の和であり、"
            "移動体に残された時間（0.3〜1.0 s）は区間3 を差し引いた後の値である。"
            "本 Spec の実測側は区間3 を含まないので、"
            "この2行は想定と実測をそのまま引き比べられない。"
        ),
        # 導出値（要件 11.4）
        (
            "【導出値】時間予算表から導出されている予測レイテンシの暫定目標"
            "（NFR-3）を、更新後の表と食い違わない値へ揃える（要件 11.4）。"
            f"区間2 の実測の上側（p95）に、区間3 の据え置きの想定値 {seg3} ms を"
            "足した値とする。"
            "中央値ではなく上側を採るのは、元の目標が各区間の想定範囲の上端から"
            "導かれているためである。丸めない。"
        ),
        # 据え置きの意味
        (
            f"【据え置き】この {seg3} ms は区間3 の想定値であって実測値ではない。"
            "区間3 が M3 で実測されるまでの据え置きであり、"
            "表の区間3 の行を埋めるものではない。"
        ),
        # 要件 11.6
        (
            "【想定と食い違った場合】実測値が想定と食い違っても、"
            "数値を想定へ合わせない。表そのものを実測値で更新する（要件 11.6）。"
        ),
        # 要件 11.5 / 11.7
        (
            "【残すもの】既存の想定値・導出根拠・注記を削除しない（要件 11.5）。"
            "更新後も、これらの数値が合否条件ではなく暫定目標値である旨を維持する"
            "（要件 11.7）。"
        ),
        # 要件 11.8
        (
            "【更新対象】更新してよいのは §3 の時間予算表とその導出値だけであり、"
            "他節を書き換えない（要件 11.8）。"
        ),
        # 本コンポーネントの立ち位置
        (
            "本コンポーネントは値を算出するだけであり、文書の書き換えは行わない"
            "（design.md「BudgetUpdater」Risks）。"
        ),
    )


class TestCriterion:
    """**規則の文面は全文を一文ずつ覆う。**"""

    def criterion(self) -> str:
        return update().judgement.criterion

    def test_every_sentence_of_the_rule_is_recorded(self) -> None:
        text = self.criterion()
        for sentence in expected_criterion_sentences(seg3="70"):
            assert sentence in text, sentence

    def test_the_criterion_is_exactly_the_fixed_text(self) -> None:
        """連結が criterion と**厳密に一致**する（削除・追加・順序の入れ替えが落ちる）。"""
        assert self.criterion() == "".join(expected_criterion_sentences(seg3="70"))

    def test_the_criterion_matches_a_second_settings_point(self) -> None:
        """**別の設定点でも全文が一致する**（規則が設定から組まれている固定）。"""
        result = compute_budget_update(
            aggregate(),
            latency(),
            settings=settings(segment3_assumed_ms=SEG3_ASSUMED_2),
        )
        assert result.judgement.criterion == "".join(
            expected_criterion_sentences(seg3="35")
        )

    def test_the_criterion_builder_reflects_its_argument(self) -> None:
        text = budget_criterion(segment3_assumed_ms=SEG3_ASSUMED_2)
        assert text == "".join(expected_criterion_sentences(seg3="35"))

    def test_the_criterion_does_not_use_the_default_setting(self) -> None:
        """**設定を無視して既定値を書き込む実装**を落とす。"""
        text = self.criterion()
        assert "50 ms を" not in text
        assert "この 50 ms は" not in text

    def test_the_criterion_does_not_change_with_the_data(self) -> None:
        """規則は**結果によって動かない**（結果に合わせて動く規則は規則ではない）。"""
        assert update().judgement.criterion == update(
            empty=[ITEM_AIM_ERROR_MM]
        ).judgement.criterion

    def test_the_gate_and_the_segment3_rule_are_not_swapped(self) -> None:
        """取り違え専用の否定照合。"""
        text = self.criterion()
        assert "【ゲート】区間3" not in text
        assert "【区間3】M1 実測7項目" not in text
        assert "区間2 は「検出開始〜予測確定」と読み替える" not in text
        assert "区間1 の実測の上側（p95）に" not in text

    def test_the_two_bounds_are_not_swapped(self) -> None:
        """合計は「下側」、残り時間は「上側」。**逆に書いた実装を落とす。**"""
        text = self.criterion()
        assert "実際のオーバーヘッドの下側である。" in text
        assert "実際のオーバーヘッドの上側である。" not in text
        assert "実際に移動体へ残る時間の上側である。" in text
        assert "実際に移動体へ残る時間の下側である。" not in text

    def test_the_assumed_side_of_the_two_rows_is_not_swapped(self) -> None:
        """想定側の区間3 の扱いを2行で取り違えた実装を落とす。"""
        text = self.criterion()
        assert "オーバーヘッド合計（0.2〜0.3 s）が区間1＋区間2＋区間3 の和" in text
        assert "移動体に残された時間（0.3〜1.0 s）は区間3 を差し引いた後の値" in text
        assert "オーバーヘッド合計（0.2〜0.3 s）は区間3 を差し引いた後の値" not in text


# ---------------------------------------------------------------------------
# 決定性・不変性・境界
# ---------------------------------------------------------------------------


class TestDeterminismAndImmutability:
    def test_the_same_input_gives_the_same_update(self) -> None:
        first = update()
        second = update()
        assert first == second

    def test_a_different_settings_point_gives_a_different_update(self) -> None:
        """「2回呼ぶと同じ」だけでは空振りになる（タスク 5.1 の教訓）。"""
        other = compute_budget_update(
            aggregate(),
            latency(),
            settings=settings(segment3_assumed_ms=SEG3_ASSUMED_2),
        )
        assert update() != other

    @pytest.mark.parametrize("cls", [BudgetRow, BudgetUpdate])
    def test_the_results_are_frozen_dataclasses(self, cls: type) -> None:
        assert dataclasses.is_dataclass(cls)
        assert cls.__dataclass_params__.frozen  # type: ignore[attr-defined]

    def test_the_update_cannot_be_modified(self) -> None:
        result = update()
        with pytest.raises(FrozenInstanceError):
            result.ready = False  # type: ignore[misc]

    def test_the_evidence_is_copied(self) -> None:
        """証跡は**複製して**保持する（`Judgement` の方針）。"""
        result = update()
        payload = dict(result.judgement.evidence)
        payload["ready"] = "tampered"
        assert result.judgement.evidence["ready"] is True


class TestBudgetConfig:
    """区間3 の据え置き想定値は**設定として外に出す**。"""

    def test_the_default_is_the_documented_assumption(self) -> None:
        """既定値は `docs/requirements.md §3` の区間3 の想定値（0.05 s）である。"""
        assert BudgetConfig().segment3_assumed_ms == pytest.approx(
            DEFAULT_SEG3_ASSUMED
        )

    def test_the_test_settings_do_not_coincide_with_the_default(self) -> None:
        """**テストの設定値が既定値と一致すると空振りになる**（タスク 6.1 の教訓）。

        既定値が将来テスト側の値へ動いたとき、このガードが必ず落ちる。
        """
        assert SEG3_ASSUMED_1 != BudgetConfig().segment3_assumed_ms
        assert SEG3_ASSUMED_2 != BudgetConfig().segment3_assumed_ms

    def test_it_is_resolved_from_the_settings_layers(self, tmp_path: Path) -> None:
        """設定として**外から与えられる**（`ENV_PREFIX` の層まで通る）。"""
        resolved = M1Settings.resolve(
            file=None,
            env={"STB_M1_SEGMENT3_ASSUMED_MS": str(SEG3_ASSUMED_2)},
            overrides={"layout_file": _layout_file(tmp_path)},
        )
        assert resolved.budget.segment3_assumed_ms == pytest.approx(SEG3_ASSUMED_2)

    def test_an_override_beats_the_environment(self, tmp_path: Path) -> None:
        resolved = M1Settings.resolve(
            file=None,
            env={"STB_M1_SEGMENT3_ASSUMED_MS": str(SEG3_ASSUMED_2)},
            overrides={
                "layout_file": _layout_file(tmp_path),
                "segment3_assumed_ms": SEG3_ASSUMED_1,
            },
        )
        assert resolved.budget.segment3_assumed_ms == pytest.approx(SEG3_ASSUMED_1)

    def test_it_appears_in_the_resolved_description(self, tmp_path: Path) -> None:
        """`--print-settings` から読める（要件 13.5）。"""
        resolved = M1Settings.resolve(
            file=None,
            env={},
            overrides={
                "layout_file": _layout_file(tmp_path),
                "segment3_assumed_ms": SEG3_ASSUMED_1,
            },
        )
        described = resolved.describe()["budget"]
        assert described["segment3_assumed_ms"] == pytest.approx(SEG3_ASSUMED_1)  # type: ignore[index]

    def test_the_provisional_notice_names_the_carried_assumption(
        self, tmp_path: Path
    ) -> None:
        """既定値が必須性能と読まれないようにする（要件 13.7）。"""
        resolved = M1Settings.resolve(
            file=None, env={}, overrides={"layout_file": _layout_file(tmp_path)}
        )
        assert "segment3_assumed_ms" in str(resolved.describe()["provisional_notice"])

    def test_a_non_positive_carried_assumption_is_rejected(
        self, tmp_path: Path
    ) -> None:
        """**0 で埋めた設定を実行開始前に拒否する**（要件 13.6）。

        0 を許すと「区間3 は瞬時である」という未実測の主張が、設定の顔をして
        導出値へ入る。
        """
        with pytest.raises(M1ConfigError):
            M1Settings.resolve(
                file=None,
                env={},
                overrides={
                    "layout_file": _layout_file(tmp_path),
                    "segment3_assumed_ms": 0.0,
                },
            )
