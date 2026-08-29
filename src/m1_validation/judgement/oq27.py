"""OQ-27（Raspberry Pi 4 を継続するか）の判定。

design.md「Components and Interfaces / L7: 判断 / Oq27Judge」および
「決着させる未決事項」、research.md Decision 5、tasks.md タスク 6.1、
要件 9.1-9.11。

**本モジュールはハードウェアの置き換えを実行せず、判断材料と結論の提示までにとどめる**
（要件 9.11）。買うべき部品も、発注も、構成の差し替えも扱わない。
出すのは「継続 / 条件付き継続 / 不足 / 保留」という4値の判定と、その根拠に
なった同一測定内の量、律速段階、改善の適用履歴だけである。

本モジュールが守っているのは3つの規律である。

**第一に、絶対値の目標を置かない**（要件 9.2、`tech.md` 開発標準1）。
「end-to-end が 300 ms 以内なら継続」のような閾値を持たない。比較対象は
**同一測定から得た量**——実測項目2（リリース〜検出開始）と実測項目3
（検出開始〜初回予測）の代表値の和、すなわち `docs/requirements.md §3` の
時間予算表がいう**システム側オーバーヘッドを、その測定自身で測り直した値**
——であり、別の測定・カタログ値・過去の記録とは比べない。未実測の数値を
合否条件にすると、根拠のない基準から逆算した判断が独り歩きする。

**第二に、性能不足を宣言する前に、まだ手を打てる余地が無いことを示させる**
（GATE 0。要件 9.3 / 9.4、`docs/development-environment.md §13.2`、
`tech.md` 開発標準4）。§13.2 の改善項目に未適用のものが残っている間、
「不足」を出さない。しかも「適用した」という申告だけでは足りず、
**前後の計測値を証跡として要求する**。「不足」の判定は購入判断に直結する
——証跡の無い判定を出せない構造にしておく。

**第三に、単発の投擲では判断しない**（GATE 1 / 2。要件 9.9 / 9.10）。
投擲はばらつきが大きく再現性が低い。試行数・セッション数が下限に満たない
場合、および**実機由来の投擲が1件も無い場合**は「保留」を返す。保留は失敗
ではなく正常な結果である——判断を急いで暫定値でハードを替えないための値で
ある。

⚠️ **試行数の下限（`TrialLimits`）と飽和の割合（`Oq27Config`）は暫定の評価
候補であって必須性能ではない**（要件 13.7）。合否条件として扱わない。

本モジュールは L7 層であり、`errors` / `types` / `config` と `metrics` の
結果型、そして標準ライブラリだけを参照する。上流3パッケージ
（`sensing_foundation` / `flying_object_tracking` / `world_frame_calibration`）
を直接 import しない（要件 13.1）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from m1_validation.config import M1Settings, Oq27Config
from m1_validation.errors import M1ConfigError
from m1_validation.metrics.aggregate import (
    ITEM_DETECT_TO_FIRST_PREDICTION_MS,
    ITEM_RELEASE_TO_DETECT_MS,
    ThrowAggregate,
)
from m1_validation.metrics.latency import LatencyResult, StageLatency
from m1_validation.types import Judgement, Oq27Verdict

# ---------------------------------------------------------------------------
# 語彙
# ---------------------------------------------------------------------------

#: `Judgement.question`。判断の種類を1語で表す（`types.Judgement` 参照）。
OQ27_QUESTION: str = "OQ-27"

#: 取得区間の段階名。規則3（条件付き継続）が「取得側が律速か」を見るときに
#: 使う。上流 `sensing_foundation` が取得に用いる段階名の写しであり、
#: **本モジュールは上流を import しない**（`latency.py` と同じ事情）。
ACQUISITION_STAGE: str = "capture"

#: CPU 使用率という測定量そのものの上限（全負荷）。
#:
#: **これは性能目標ではない。** 「使用率」は定義上 0〜100% の目盛りを持ち、
#: 飽和とはその目盛りの端に張り付いていることを指す。したがってこの 100.0 は
#: 「100% を達成せよ」でも「100% 未満なら合格」でもなく、**同一測定内で
#: 相対比較を行うための、その量自身の満量**である（要件 9.2）。
CPU_FULL_PERCENT: float = 100.0

#: `docs/development-environment.md §13.2`「性能不足だった場合の検討順序」。
#:
#: **順序も内容の一部である**——安いものから順に試すという規律であり、
#: 8番目まで到達して初めてハードウェアの検討に進んでよい。設定
#: `improvements_applied` と証跡（`ImprovementRecord`）は、この名前で指す。
IMPROVEMENT_STEPS: tuple[str, ...] = (
    "color_stream_reduction",
    "resolution",
    "roi",
    "fps",
    "image_processing_reduction",
    "point_cloud_avoidance",
    "detection_algorithm_simplification",
    "software_optimization",
)

#: 改善項目の日本語ラベル（§13.2 の箇条そのもの）。判定規則の説明文に、
#: 設定へ書くキーと対にして載せる——キーだけでは何の項目か読めず、ラベル
#: だけでは何と書けば通るのかが分からない。
IMPROVEMENT_STEP_LABELS: Mapping[str, str] = {
    "color_stream_reduction": "Color stream 削減",
    "resolution": "Resolution 調整",
    "roi": "ROI 縮小",
    "fps": "FPS 調整",
    "image_processing_reduction": "不要な画像処理削減",
    "point_cloud_avoidance": "Point Cloud 全生成を回避",
    "detection_algorithm_simplification": "検出アルゴリズム簡略化",
    "software_optimization": "ソフトウェア最適化",
}


# ---------------------------------------------------------------------------
# 判定規則の説明文（**結果と同じ場所に置く**。要件 9.1）
# ---------------------------------------------------------------------------

_CRITERION_TEMPLATE: str = (
    "OQ-27（Raspberry Pi 4 継続可否）の判定規則"
    "（実測前に固定。design.md「決着させる未決事項」/「Oq27Judge」、"
    "research.md Decision 5、要件 9.1）: "
    "判定値は「継続 / 条件付き継続 / 不足 / 保留」の4値である。"
    "【GATE 1】有効試行数が {min_throws:g} 未満、"
    "またはセッション数が {min_sessions:g} 未満なら保留とする"
    "（投擲はばらつきが大きく再現性が低いため、単発では判断しない。要件 9.9）。"
    "【GATE 2】実機由来の投擲が1件も無いなら保留とし、"
    "合成・記録再生の結果を実機の結論として扱わない（要件 9.10）。"
    "【規則1】end-to-end レイテンシの p95 が、"
    "同一測定から得たオーバーヘッド相当値"
    "（実測項目2「リリース〜検出開始」の代表値と"
    "実測項目3「検出開始〜初回予測」の代表値の和）を超えないなら継続とする"
    "（ちょうど等しい値は「超えない」に含む。要件 9.6）。"
    "【規則2】超え、かつ計算資源が飽和しているなら不足とする（要件 9.7）。"
    "【規則3】超えるが計算資源に余裕があり、"
    "律速段階が取得区間（段階名 capture）であるなら条件付き継続とし、"
    "律速している条件を明示する（要件 9.8）。"
    "【規則4】上記で決まらないなら保留とする"
    "（p95 か比較対象が欠測、飽和を判定する材料が1つも無い、"
    "律速段階が取得区間でない、のいずれか）。"
    "【GATE 0】docs/development-environment.md §13.2 の改善項目"
    "（{steps}）に未適用のものが残っている間は「不足」を出さず保留へ落とす"
    "（要件 9.3）。適用済みと認めるのは、設定 improvements_applied に名前があり、"
    "かつ前後の計測値が証跡として揃っている項目だけである（要件 9.4）。"
    "律速段階は段階別レイテンシの内訳のうち p95 が最大の行とし、"
    "同値なら段階名・イベント名・フィールド名の辞書順で先の行を採る（要件 9.5）。"
    "計算資源の飽和は同一測定内の量だけで判定する: "
    "取りこぼしが1件以上あるか、"
    "実処理 fps が取得 fps の {fps_ratio:g} 倍を下回るか、"
    "CPU 使用率の平均が全負荷（測定量そのものの上限 100%）の "
    "{cpu_ratio:g} 倍以上であること。"
    "3つとも欠測なら飽和は判定不能とする"
    "（「測っていない」を「余裕がある」と読み替えない）。"
    "絶対値の目標を置かず、同一測定内の量どうしの相対比較とばらつきで判定する"
    "（要件 9.2、tech.md 開発標準1）。"
    "ここに出ている下限と割合は暫定の評価候補であって必須性能ではない"
    "（要件 13.7）。"
    "本判定はハードウェアの置き換えを実行せず、"
    "判断材料と結論の提示までにとどめる（要件 9.11）。"
)

# --- 判定理由（**相互排他**。判定値が同じでも診断は別物である）--------------
#
# 保留は4通りの理由で立つ。判定値ではどれなのか区別が付かないが、
# 「投げる回数を増やす」「実機で撮る」「改善項目を適用する」「資源を測る」は
# **やることがまったく違う**。取り違えて記録すると、存在しない問題を追いかけ
# ることになる（タスク 5.2 の教訓）。

_RATIONALE_GATE1_THROWS: str = "有効試行数が下限に満たない（GATE 1）。"
_RATIONALE_GATE1_SESSIONS: str = "セッション数が下限に満たない（GATE 1）。"
_RATIONALE_GATE2_NO_LIVE: str = (
    "実機由来の投擲が1件も無い（GATE 2）。"
    "合成・記録再生の結果を実機の結論として扱わない（要件 9.10）。"
)
_RATIONALE_CONTINUE: str = (
    "end-to-end の p95 は同一測定から得たオーバーヘッド相当値を超えない（規則1）。"
)
_RATIONALE_INSUFFICIENT: str = (
    "end-to-end の p95 が同一測定から得た比較対象を超え、"
    "計算資源が飽和している（規則2）。"
)
_RATIONALE_CONSTRAINED: str = (
    "end-to-end の p95 が同一測定から得た比較対象を超えるが、"
    "計算資源には余裕があり、律速段階は取得区間である（規則3）。"
)
_RATIONALE_NO_MEASUREMENT: str = (
    "end-to-end の p95 か同一測定から得た比較対象が欠測しており、"
    "規則1〜3を適用できない（規則4）。"
)
_RATIONALE_SATURATION_UNKNOWN: str = (
    "end-to-end の p95 が比較対象を超えるが、"
    "計算資源の飽和を判定する材料が1つも無い（規則4）。"
)
_RATIONALE_BOTTLENECK_NOT_ACQUISITION: str = (
    "end-to-end の p95 が比較対象を超え、計算資源には余裕があるが、"
    "律速段階が取得区間ではない（規則4）。"
)
_RATIONALE_GATE0_BLOCKED: str = (
    "規則2により「不足」と判定される状態だが、"
    "§13.2 の改善項目に未適用のものが残っているため「不足」を出さない（GATE 0）。"
)


def oq27_criterion(
    *,
    min_valid_throws: int,
    min_sessions: int,
    fps_shortfall_ratio: float,
    cpu_saturation_ratio: float,
) -> str:
    """実際に適用する規則の説明文を組み立てる（要件 9.1）。

    **実際に使った下限と割合を文面へ入れる。** 規則の文だけを残して数値を
    伏せると、同じ文で違う判定が正当化できてしまう
    （`attribution.attribution_criterion` / `convergence.convergence_criterion`
    と同じ理由）。文面は判定値によって変わらない——結果に合わせて動く規則は
    規則ではない。
    """
    steps = " / ".join(
        f"{step}（{IMPROVEMENT_STEP_LABELS[step]}）" for step in IMPROVEMENT_STEPS
    )
    return _CRITERION_TEMPLATE.format(
        min_throws=min_valid_throws,
        min_sessions=min_sessions,
        steps=steps,
        fps_ratio=fps_shortfall_ratio,
        cpu_ratio=cpu_saturation_ratio,
    )


# ---------------------------------------------------------------------------
# 結果
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ImprovementRecord:
    """`§13.2` の改善項目1件と、その前後の計測値（要件 9.4）。

    Attributes:
        step: 項目名。`IMPROVEMENT_STEPS` のいずれか。
        applied: 適用したか。
        before: 適用**前**の計測値（項目名から値へ）。
        after: 適用**後**の計測値。

    **前後の計測値は証跡である。** 「適用した」という申告だけを受け取ると、
    GATE 0 は名前を並べるだけで開いてしまう。`judge_oq27()` は
    `before` と `after` の**どちらも空でない**項目だけを適用済みとして数える
    ——何がどう変わったのかを示せない改善は、`tech.md` 開発標準4 が求める
    「何を適用して何が変わったかの記録」になっていない。

    Notes:
        渡されたマッピングは**複製して**保持する。呼び出し側が使い回す辞書を
        あとから書き換えても、証跡は判定時の事実のまま残る
        （`Judgement.evidence` と同じ方針）。
    """

    step: str
    applied: bool
    before: Mapping[str, float]
    after: Mapping[str, float]

    def __post_init__(self) -> None:
        object.__setattr__(self, "before", dict(self.before))
        object.__setattr__(self, "after", dict(self.after))


@dataclass(frozen=True, slots=True)
class Oq27Result:
    """OQ-27 の判定結果（要件 9.1-9.11）。

    Attributes:
        verdict: 判定値（継続 / 条件付き継続 / 不足 / 保留）。
        bottleneck_stage: 律速段階の段階名（要件 9.5）。段階別レイテンシに
            p95 を持つ行が1つも無ければ `None`。
        bottleneck_label: 律速段階を一意に指す `段階/イベント/フィールド`。
            1つの段階が複数の区間を記録するため、**段階名だけでは
            どの区間が律速なのか決まらない**（`StageLatency` の docstring）。
        bottleneck_p95_ms: 律速段階の p95（ms）。
        end_to_end_p95_ms: end-to-end レイテンシの p95（ms）。測れていなければ
            `None`（**0 で埋めない**——「0 ms だった」と「測っていない」は別）。
        overhead_reference_ms: **同一測定から得た比較対象**（ms。要件 9.2 /
            9.6）。実測項目2 と実測項目3 の代表値の和であり、設計時の想定値
            （§3 の 0.2〜0.3 s）を持ち込まない。どちらかが欠測なら `None`。
        resource_saturated: 計算資源が飽和していたか（要件 9.7）。材料が1つも
            無ければ `None`——**「測っていない」を「余裕がある」と読み替え
            ない**。
        limiting_conditions: 律速している条件（要件 9.8）。条件付き継続の
            ときだけ内容を持つ。
        improvements: `§13.2` の全項目の適用状況と前後の計測値（要件 9.4）。
            **未適用の項目も行として残る**——何が残っているかが読めなければ、
            次に何をすればよいか分からない。
        judgement: 判断（判定規則の説明文・判定値・根拠・証跡・暫定の印）。

    Postconditions:
        `verdict` が `INSUFFICIENT` のとき、`improvements` の全項目が
        `applied=True` である（GATE 0。design.md「Oq27Judge」）。

    **design.md の擬似コードとの差**（フィーチャレベル検証で同期すること）:

    - `end_to_end_p95_ms` / `overhead_reference_ms` を `float | None`、
      `resource_saturated` を `bool | None` にした。擬似コードは非 optional
      だが、`LatencyResult` はどの項目も欠測しうる（資源値は Linux の
      `/proc` から取れない環境で `None`、end-to-end は予測更新が1件も
      無ければ `None`）。0 や `False` で埋めると、**測っていない測定が
      「余裕がある」測定として判断へ入る**（タスク 4.6 / 5.1 と同じ判断）。
    - `bottleneck_label` / `bottleneck_p95_ms` / `limiting_conditions` を
      足した。要件 9.8 が「律速している条件を明示する」と求めており、
      段階名1つでは置き場所が無い。
    """

    verdict: Oq27Verdict
    bottleneck_stage: str | None
    bottleneck_label: str | None
    bottleneck_p95_ms: float | None
    end_to_end_p95_ms: float | None
    overhead_reference_ms: float | None
    resource_saturated: bool | None
    limiting_conditions: tuple[str, ...]
    improvements: tuple[ImprovementRecord, ...]
    judgement: Judgement


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def judge_oq27(
    latency: LatencyResult,
    aggregate: ThrowAggregate,
    *,
    settings: M1Settings,
    improvements: Sequence[ImprovementRecord] = (),
) -> Oq27Result:
    """Pi 4 を継続するかを、実測前に固定した規則で判断する（要件 9）。

    判定規則は `oq27_criterion()` が返す説明文のとおりであり、**実測前に
    固定されている**。結果の `judgement.criterion` に同じ文面を埋め込むので、
    あとから規則のほうを結果に合わせて読み替えられない（要件 9.1）。

    **本関数はハードウェアの置き換えを実行せず、判断材料と結論の提示までにとどめる**
    （要件 9.11）。返すのは判定値と、その根拠になった同一測定内の量・律速段階・
    改善の適用履歴だけである。「不足」という判定は次に何を検討してよいかを
    示すにすぎず、購入も構成変更も本 Spec の範囲外である
    （Boundary Context「Out of scope」）。

    Args:
        latency: 段階別レイテンシと資源使用の集計（`aggregate_latency()` の
            戻り値）。end-to-end の p95・律速段階・飽和の材料をここから読む。
        aggregate: 1つのキャリブレーション（識別子 × 検証状態）に属する投擲群
            の集計。試行数・セッション数・実機由来の投擲数と、**同一測定から
            得た比較対象**の材料（実測項目2 / 3 の代表値）をここから読む。
        settings: 解決済みの設定。`trials`（試行数の下限）・`oq27`（飽和の
            割合）・`improvements_applied`（適用したと申告された改善項目）を
            読む。
        improvements: 改善項目の証跡。**適用済みと認めるには、`settings` に
            名前があり、かつここに前後の計測値が揃っている必要がある**
            （要件 9.4）。省略すると GATE 0 は開かない。

    Returns:
        `Oq27Result`。**保留は正常な結果である**——判断を急いで暫定値で
        ハードを替えないための値であり、失敗ではない。

    Raises:
        M1ConfigError: `settings.improvements_applied` または `improvements`
            に `§13.2` に無い項目名がある、あるいは `improvements` に同じ
            項目が2度現れる場合。**綴り間違いを黙って無視しない**
            ——見過ごすと「適用したはずの改善」が数えられないまま
            GATE 0 が閉じ続ける、あるいは知らない名前で開いてしまう。

    Notes:
        同一入力・同一設定に対して同一の判定を返す（要件 12.4）。

        **`improvements` が design.md の擬似コード署名に無いのは意図した差で
        ある。** 擬似コードは `judge_oq27(latency, aggregate, *, settings)`
        だが、`M1Settings.improvements_applied` は**項目名の並びしか持たない**
        ので、design.md 自身が `Oq27Result.improvements` に要求している
        `before` / `after` の計測値をどこからも取れない。証跡を要求せよという
        タスク箇条（要件 9.4）を満たすには引数が要る。
    """
    trials = settings.trials
    rows = _improvement_records(settings.improvements_applied, improvements)
    missing_steps = tuple(row.step for row in rows if not row.applied)
    gate0_covered = not missing_steps

    bottleneck = _bottleneck(latency.stages)
    end_to_end_p95_ms = latency.end_to_end.p95_ms
    reference = _overhead_reference(aggregate)
    saturated, signals = _resource_saturation(latency, settings.oq27)

    verdict, rationale, limiting = _decide(
        aggregate=aggregate,
        settings=settings,
        end_to_end_p95_ms=end_to_end_p95_ms,
        reference=reference,
        saturated=saturated,
        bottleneck=bottleneck,
        latency=latency,
    )

    # GATE 0 は判定値を作らない。**「不足」だけを止める**（要件 9.3）。
    # 「未適用なら常に保留」に読み替えると、改善を1つも適用していない健全な
    # 測定まで判断不能になる。止めるのは購入判断に直結する「不足」だけである。
    if verdict is Oq27Verdict.INSUFFICIENT and not gate0_covered:
        verdict = Oq27Verdict.DEFERRED
        rationale = _RATIONALE_GATE0_BLOCKED
        limiting = ()

    return Oq27Result(
        verdict=verdict,
        bottleneck_stage=None if bottleneck is None else bottleneck.stage,
        bottleneck_label=None if bottleneck is None else _stage_label(bottleneck),
        bottleneck_p95_ms=None if bottleneck is None else bottleneck.p95_ms,
        end_to_end_p95_ms=end_to_end_p95_ms,
        overhead_reference_ms=reference.total_ms,
        resource_saturated=saturated,
        limiting_conditions=limiting,
        improvements=rows,
        judgement=Judgement(
            question=OQ27_QUESTION,
            criterion=oq27_criterion(
                min_valid_throws=trials.min_valid_throws,
                min_sessions=trials.min_sessions,
                fps_shortfall_ratio=settings.oq27.fps_shortfall_ratio,
                cpu_saturation_ratio=settings.oq27.cpu_saturation_ratio,
            ),
            verdict=str(verdict),
            rationale=rationale,
            evidence={
                "calibration_id": aggregate.calibration_id,
                "verified": aggregate.verified,
                "throw_count": aggregate.throw_count,
                "valid_throw_count": aggregate.valid_throw_count,
                "live_throw_count": aggregate.live_throw_count,
                "session_count": len(aggregate.session_ids),
                "min_valid_throws": trials.min_valid_throws,
                "min_sessions": trials.min_sessions,
                "end_to_end_p95_ms": end_to_end_p95_ms,
                "end_to_end_p50_ms": latency.end_to_end.p50_ms,
                "end_to_end_min_ms": latency.end_to_end.min_ms,
                "end_to_end_max_ms": latency.end_to_end.max_ms,
                "end_to_end_count": latency.end_to_end.count,
                "overhead_reference_ms": reference.total_ms,
                "release_to_detect_median_ms": reference.release_to_detect_ms,
                "release_to_detect_iqr_ms": reference.release_to_detect_iqr_ms,
                "detect_to_first_prediction_median_ms": reference.detect_to_first_ms,
                "detect_to_first_prediction_iqr_ms": reference.detect_to_first_iqr_ms,
                "bottleneck_stage": None if bottleneck is None else bottleneck.stage,
                "bottleneck_label": (
                    None if bottleneck is None else _stage_label(bottleneck)
                ),
                "bottleneck_p95_ms": None if bottleneck is None else bottleneck.p95_ms,
                "resource_saturated": saturated,
                "frames_dropped": signals.frames_dropped,
                "frames_missing": latency.frames_missing,
                "capture_fps": signals.capture_fps,
                "process_fps": signals.process_fps,
                "cpu_percent_mean": signals.cpu_percent_mean,
                "cpu_saturation_ratio": settings.oq27.cpu_saturation_ratio,
                "fps_shortfall_ratio": settings.oq27.fps_shortfall_ratio,
                "improvements_covered": gate0_covered,
                "improvements_missing": list(missing_steps),
                "limiting_conditions": list(limiting),
            },
            # 暫定の印は「判断に用いてよい状態ではない」ことを示す
            # （要件 5.10 / 9.3 / 9.9）。保留・集計の暫定・改善の未適用の
            # いずれでも立つ。
            provisional=(
                verdict is Oq27Verdict.DEFERRED
                or aggregate.provisional
                or not gate0_covered
            ),
        ),
    )


# ---------------------------------------------------------------------------
# 判定の本体
# ---------------------------------------------------------------------------


def _decide(
    *,
    aggregate: ThrowAggregate,
    settings: M1Settings,
    end_to_end_p95_ms: float | None,
    reference: _OverheadReference,
    saturated: bool | None,
    bottleneck: StageLatency | None,
    latency: LatencyResult,
) -> tuple[Oq27Verdict, str, tuple[str, ...]]:
    """GATE 1 / 2 を通したうえで規則1〜4 を当てる。

    **順序は load-bearing である。** 保留ゲートを本判定より後ろへ移すと、
    試行数が2件しか無い測定に対して「継続」という結論が出る——投擲は
    ばらつきが大きく再現性が低いのだから、それは根拠になっていない
    （要件 9.9、A-6「単発の投擲では判断しない」）。
    """
    trials = settings.trials
    if aggregate.valid_throw_count < trials.min_valid_throws:
        return Oq27Verdict.DEFERRED, _RATIONALE_GATE1_THROWS, ()
    if len(aggregate.session_ids) < trials.min_sessions:
        return Oq27Verdict.DEFERRED, _RATIONALE_GATE1_SESSIONS, ()
    if aggregate.live_throw_count <= 0:
        return Oq27Verdict.DEFERRED, _RATIONALE_GATE2_NO_LIVE, ()

    if end_to_end_p95_ms is None or reference.total_ms is None:
        return Oq27Verdict.DEFERRED, _RATIONALE_NO_MEASUREMENT, ()

    # 規則1: **ちょうど等しい値は「超えない」に含む**（要件 9.6 の文面どおり）。
    if end_to_end_p95_ms <= reference.total_ms:
        return Oq27Verdict.CONTINUE, _RATIONALE_CONTINUE, ()

    if saturated is None:
        return Oq27Verdict.DEFERRED, _RATIONALE_SATURATION_UNKNOWN, ()
    if saturated:
        return Oq27Verdict.INSUFFICIENT, _RATIONALE_INSUFFICIENT, ()
    if bottleneck is not None and bottleneck.stage == ACQUISITION_STAGE:
        return (
            Oq27Verdict.CONTINUE_WITH_CONSTRAINTS,
            _RATIONALE_CONSTRAINED,
            _limiting_conditions(
                bottleneck=bottleneck,
                latency=latency,
                end_to_end_p95_ms=end_to_end_p95_ms,
                reference_ms=reference.total_ms,
            ),
        )
    return Oq27Verdict.DEFERRED, _RATIONALE_BOTTLENECK_NOT_ACQUISITION, ()


# ---------------------------------------------------------------------------
# 同一測定から得た比較対象（要件 9.2 / 9.6）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _OverheadReference:
    """比較対象と、その内訳・ばらつき（判定はばらつきと併記する）。"""

    total_ms: float | None
    release_to_detect_ms: float | None
    release_to_detect_iqr_ms: float | None
    detect_to_first_ms: float | None
    detect_to_first_iqr_ms: float | None


def _overhead_reference(aggregate: ThrowAggregate) -> _OverheadReference:
    """同一測定から**オーバーヘッド相当値**を組む（要件 9.2 / 9.6）。

    `docs/requirements.md §3` の時間予算表がいうシステム側オーバーヘッドは
    区間1（リリース〜検出開始）＋区間2（検出開始〜予測確定）＋区間3
    （予測確定〜移動体が動き出す）である。本 Spec が測れるのは前2つ
    ——実測項目2 と実測項目3——であり、**区間3 は本 Spec の範囲外**
    （M3 で実測する。tasks.md 6.3）なので含めない。

    したがってここで得る値は実際のオーバーヘッドの**下側**であり、
    比較は保守的な（「超えた」と言いやすい）側に倒れる。それでよい
    ——超えたときに何が起きるかは規則2〜4 が場合分けし、購入判断に直結する
    「不足」には GATE 0 の証跡がさらに要るからである。

    **想定値 0.2〜0.3 s を持ち込まない**（要件 9.2、`tech.md` 開発標準1）。
    比較対象は必ずこの測定自身から取る。
    """
    release = aggregate.items.get(ITEM_RELEASE_TO_DETECT_MS)
    detect = aggregate.items.get(ITEM_DETECT_TO_FIRST_PREDICTION_MS)
    release_ms = None if release is None else release.median
    detect_ms = None if detect is None else detect.median
    total_ms = (
        None if release_ms is None or detect_ms is None else release_ms + detect_ms
    )
    return _OverheadReference(
        total_ms=total_ms,
        release_to_detect_ms=release_ms,
        release_to_detect_iqr_ms=None if release is None else release.iqr,
        detect_to_first_ms=detect_ms,
        detect_to_first_iqr_ms=None if detect is None else detect.iqr,
    )


# ---------------------------------------------------------------------------
# 律速段階（要件 9.5）
# ---------------------------------------------------------------------------


def _stage_label(stage: StageLatency) -> str:
    """段階を一意に指す名前。**段階名だけでは足りない。**

    1つの段階が複数の区間を記録する（`capture` の `wait_ms` / `drain_ms` /
    `handoff_ms` / `total_ms`）ため、段階名だけを律速として提示すると、
    読み手が「取得のどこが遅いのか」を絞り込めない。
    """
    return f"{stage.stage}/{stage.event}/{stage.field}"


def _bottleneck(stages: Sequence[StageLatency]) -> StageLatency | None:
    """段階別レイテンシの内訳から律速段階を選ぶ（要件 9.5）。

    p95 が**最大**の行を採る。同値なら段階名・イベント名・フィールド名の
    辞書順で先の行を採る——並びが入力順に依存すると、同じ測定を別の順で
    読んだだけで律速段階が変わり、要件 12.4 の決定性が壊れる。

    p95 を持たない行（数値が1件も無かった段階）は候補にしない。欠測を
    0 とみなして並べると、測っていない段階が「常に速い段階」に見える。
    """
    candidates = [stage for stage in stages if stage.p95_ms is not None]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda stage: (
            -(stage.p95_ms or 0.0),
            stage.stage,
            stage.event,
            stage.field,
        ),
    )


def _limiting_conditions(
    *,
    bottleneck: StageLatency,
    latency: LatencyResult,
    end_to_end_p95_ms: float,
    reference_ms: float,
) -> tuple[str, ...]:
    """条件付き継続のときに**律速している条件を明示する**（要件 9.8）。

    「条件付き」とだけ言われても、どの条件を動かせば継続できるのかが
    分からなければ次の行動が決まらない。取得側の設定（解像度・fps・ROI）は
    `§13.2` の検討順序そのものであり、ここで名指しした量がその入口になる。
    """
    return (
        (
            f"律速段階: {_stage_label(bottleneck)}"
            f"（p95 = {_number(bottleneck.p95_ms)} ms、件数 {bottleneck.count}）"
        ),
        (
            f"取得 fps = {_number(latency.capture_fps)} / "
            f"実処理 fps = {_number(latency.process_fps)}"
        ),
        (
            f"end-to-end p95 = {_number(end_to_end_p95_ms)} ms が"
            f"同一測定のオーバーヘッド相当値 {_number(reference_ms)} ms を超えている"
        ),
    )


def _number(value: float | None) -> str:
    """欠測を「欠測」と書く。**0 と書かない。**"""
    return "欠測" if value is None else f"{value:g}"


# ---------------------------------------------------------------------------
# 計算資源の飽和（要件 9.7）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _SaturationSignals:
    """飽和の判定に使った同一測定内の量（欠測は `None` のまま残す）。"""

    frames_dropped: int | None
    capture_fps: float | None
    process_fps: float | None
    cpu_percent_mean: float | None


def _resource_saturation(
    latency: LatencyResult, config: Oq27Config
) -> tuple[bool | None, _SaturationSignals]:
    """計算資源が飽和しているかを**同一測定内の量だけ**で判定する（要件 9.7）。

    3つの signal を見る。いずれも絶対値の性能目標を置かない。

    1. **取りこぼし**（`frames_dropped`）が1件以上あるか。取得した frame を
       下流へ渡せずに捨てているなら、処理が追いついていない。
    2. **実処理 fps が取得 fps の `fps_shortfall_ratio` 倍を下回る**か。
       同一測定内の2量の比であり、「何 fps 出れば十分か」を決めずに済む
       （`latency.py` が取得 fps と実処理 fps を対で持つのはこのためである）。
    3. **CPU 使用率の平均が全負荷の `cpu_saturation_ratio` 倍以上**か。
       全負荷（100%）は性能目標ではなく、使用率という量そのものの目盛りの端
       である（`CPU_FULL_PERCENT` の注記）。

    1つでも成立すれば飽和、**3つとも欠測なら判定不能（`None`）**とする。
    「測っていない」を「余裕がある」と読み替えると、資源を一切測っていない
    測定が条件付き継続まで進んでしまう。

    Note:
        **フレーム欠落（`frames_missing`）は材料にしない。** 欠落はデバイス側
        連番の飛びであり、取りこぼし（下流へ渡せず捨てた件数）とは別の量で
        ある。混ぜると、取りこぼしが1件も無いのに飽和と判定される。
    """
    signals = _SaturationSignals(
        frames_dropped=latency.frames_dropped,
        capture_fps=latency.capture_fps,
        process_fps=latency.process_fps,
        cpu_percent_mean=latency.cpu_percent_mean,
    )

    dropped: bool | None = (
        None if signals.frames_dropped is None else signals.frames_dropped > 0
    )

    shortfall: bool | None
    if (
        signals.capture_fps is None
        or signals.process_fps is None
        or signals.capture_fps <= 0.0
    ):
        shortfall = None
    else:
        shortfall = signals.process_fps < signals.capture_fps * config.fps_shortfall_ratio

    cpu: bool | None
    if signals.cpu_percent_mean is None:
        cpu = None
    else:
        cpu = signals.cpu_percent_mean >= CPU_FULL_PERCENT * config.cpu_saturation_ratio

    verdicts = (dropped, shortfall, cpu)
    if any(value is True for value in verdicts):
        return True, signals
    if all(value is None for value in verdicts):
        return None, signals
    return False, signals


# ---------------------------------------------------------------------------
# GATE 0（改善項目の証跡。要件 9.3 / 9.4）
# ---------------------------------------------------------------------------


def _improvement_records(
    declared: Sequence[str], improvements: Sequence[ImprovementRecord]
) -> tuple[ImprovementRecord, ...]:
    """`§13.2` の全項目について、適用済みかを**証跡込みで**判定する。

    適用済みと認めるのは、次の**両方**が揃った項目だけである（要件 9.4）。

    - 設定 `improvements_applied` に名前がある（実験者の申告）
    - `improvements` に `applied=True` かつ `before` と `after` の
      どちらも空でない証跡がある（申告を裏づける前後の計測値）

    **片方だけでは開かない。** 申告だけなら名前を並べるだけで
    「Pi 4 では不足」と言えてしまうし、証跡だけなら実験者が適用したと
    考えていない変更まで数えてしまう。

    戻り値は `IMPROVEMENT_STEPS` の順で全項目を含む。**未適用の項目も行として
    残す**——何が残っているかが読めなければ、次に何をすればよいか分からない。

    Raises:
        M1ConfigError: 未知の項目名、または `improvements` の項目名の重複。
    """
    known = set(IMPROVEMENT_STEPS)

    unknown_declared = sorted(set(declared) - known)
    if unknown_declared:
        raise M1ConfigError(
            "improvements_applied に §13.2 に無い項目名がある: "
            f"{unknown_declared}。綴り間違いを黙って無視すると、"
            "適用したはずの改善が数えられないまま GATE 0 が閉じ続ける",
            {"unknown": unknown_declared, "known_steps": list(IMPROVEMENT_STEPS)},
        )

    by_step: dict[str, ImprovementRecord] = {}
    for record in improvements:
        if record.step not in known:
            raise M1ConfigError(
                f"改善項目の証跡に §13.2 に無い項目名がある: {record.step!r}",
                {"step": record.step, "known_steps": list(IMPROVEMENT_STEPS)},
            )
        if record.step in by_step:
            raise M1ConfigError(
                f"改善項目の証跡が重複している: {record.step!r}。"
                "どちらの前後の計測値を採るのかを本モジュールが決めてはならない",
                {"step": record.step},
            )
        by_step[record.step] = record

    declared_steps = set(declared)
    rows: list[ImprovementRecord] = []
    for step in IMPROVEMENT_STEPS:
        record = by_step.get(step)
        evidenced = (
            record is not None
            and record.applied
            and bool(record.before)
            and bool(record.after)
        )
        rows.append(
            ImprovementRecord(
                step=step,
                applied=step in declared_steps and evidenced,
                before={} if record is None else record.before,
                after={} if record is None else record.after,
            )
        )
    return tuple(rows)
