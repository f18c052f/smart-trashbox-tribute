"""OQ-05（NFR-7 の目標成功率と試行回数 N）の**判断材料**。

design.md「Components and Interfaces / L7: 判断 / Oq05Material」、
requirements.md「A-7」、tasks.md タスク 6.2、要件 10.1-10.5。

**本モジュールは OQ-05 を決着させない**（要件 10.4）。`open-questions.md` は
OQ-05 を「M1・M2 実測後」に決めると定めており、M1 単独では移動体の性能が
入らないため成功率を決められない。したがって判定値は入力によらず常に
`material_only` の1値であり、**結論を出す形にしていない**。ここで数値を
確定させると、移動体の実測（M2）が入る前に目標成功率が既成事実化する。

出せる材料は2つである。

**材料1: 位置精度の暫定許容窓に予測が収まる割合**（要件 10.1）。投擲ごとの
最終予測の落下地点誤差ベクトルについて、水平2成分の大きさ（ノルム）が窓以下で
ある投擲の割合を出す。**この割合はキャッチ成功率そのものではない**（要件
10.2）——予測が窓に収まっても、移動体が間に合わなければ、あるいは対象物が
開口から跳ね出せば、キャッチは成立しない。したがって当該割合は
**予測側から見た成功率の上限**である。この読み方を出力から落とすと、移動体
性能を無視した期待値がそのまま NFR-7 の目標として置かれる。

**材料2: 所望の信頼区間幅を得るために必要な試行回数**（要件 10.3）。二項比率の
正規近似による信頼区間の全幅から逆算する。**新しい統計手法を導入しない**
（design.md「Oq05Material」Integration）——本 Spec は測定であって改善では
なく（A-1）、推定器を二重に持たない（`research.md` Decision 7）。計算は
`math` と `statistics` の範囲で閉じている。

⚠️ **許容窓の値は対象物の寸法に依存し、対象物の最終スコープは未決である**
（要件 10.5 / 13.9）。窓は `ThrowLayout` の開口寸法と対象物寸法から導く値で
あって、コードに埋め込んだ性能目標ではない。窓が変われば材料1 の割合も
材料2 の必要試行回数も変わる。

⚠️ **信頼水準と信頼区間幅（`Oq05Config`）は暫定の評価候補であって必須性能では
ない**（要件 13.7）。合否条件として扱わない。

**欠測を 0 で埋めない。** 誤差ベクトルが1件も無ければ割合は `None` であり、
割合が 0 または 1 に振り切れた群では正規近似の分散 p(1 − p) が 0 になるため
必要試行回数は `None` である。0 を返すと「1件も収まらなかった」「0 回の試行で
決まる」という、測っていないことと区別の付かない材料が判断へ入る。

本モジュールは L7 層であり、`errors` / `types` / `config` / `layout` と
`metrics` の結果型、そして標準ライブラリだけを参照する。上流3パッケージ
（`sensing_foundation` / `flying_object_tracking` / `world_frame_calibration`）
を直接 import しない（要件 13.1）。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import NormalDist

from m1_validation.config import M1Settings
from m1_validation.metrics.aggregate import ITEM_HIT_ERROR_FINAL_MM, ThrowAggregate
from m1_validation.types import Judgement

# ---------------------------------------------------------------------------
# 語彙
# ---------------------------------------------------------------------------

#: `Judgement.question`。判断の種類を1語で表す（`types.Judgement` 参照）。
OQ05_QUESTION: str = "OQ-05"

#: `Judgement.verdict`。**入力によらず常にこの1値**である（要件 10.4）。
#:
#: OQ-05 は M1 単独では決着しない。可否の語彙（`Oq27Verdict` のような）を
#: 借りると、材料が結論として読まれる——移動体の実測が入る前に目標成功率が
#: 固まってしまう。1値しか無いこと自体が「決着させない」という規律である。
VERDICT_MATERIAL_ONLY: str = "material_only"


def required_trials_key(width: float) -> str:
    """信頼区間幅から `required_trials` のキーを組む。

    幅そのものをキーにするのは、**どの幅に対する試行数なのかが読めなければ
    材料にならない**からである（「N = 86」だけでは何も決められない）。
    """
    return f"width_{width:g}"


# ---------------------------------------------------------------------------
# 判定規則の説明文（**結果と同じ場所に置く**）
# ---------------------------------------------------------------------------

_CRITERION_TEMPLATE: str = (
    "OQ-05（NFR-7 の目標成功率と試行回数 N）の判断材料の作り方"
    "（実測前に固定。design.md「Oq05Material」、要件 10.1-10.5）: "
    "判定値は「material_only」の1値だけであり、OQ-05 を決着させない（要件 10.4）。"
    "M1 単独では移動体の性能が入らないため成功率そのものを決められない。"
    "本材料は目標成功率も試行回数 N も確定させず、提示にとどめる（A-7）。"
    "【材料1】位置精度の暫定許容窓は {window:g} mm とする"
    "（投擲レイアウトの開口寸法 {aperture:g} mm の半径から"
    "対象物の寸法 {object_mm:g} mm の半径を引いた値。要件 10.1）。"
    "窓に収まる割合は、投擲ごとの最終予測の落下地点誤差ベクトルについて、"
    "水平2成分の大きさ（ノルム）が窓以下である投擲の数を、"
    "誤差ベクトルが得られた投擲の数で割った値とする"
    "（片成分ではなくノルムで判定し、窓の値ちょうどは「収まる」に含む）。"
    "【材料1の読み方】当該割合は予測側から見た成功率の上限であって、"
    "キャッチ成功率そのものではない（要件 10.2）。"
    "予測が窓に収まっても、移動体が間に合わなければ、"
    "あるいは対象物が開口から跳ね出せば、キャッチは成立しない。"
    "【材料2】所望の信頼区間幅を得るために必要な試行回数は、"
    "二項比率の正規近似による信頼区間の全幅 W から "
    "n = 4 z^2 p (1 - p) / W^2 として求め、小数点以下を切り上げる"
    "（要件 10.3）。"
    "ここで p は材料1 の割合（観測されたばらつきは p (1 - p) として入る）、"
    "z は信頼水準 {level:g} に対する標準正規分布の両側分位点である。"
    "求める信頼区間幅は {widths} であり、"
    "いずれも割合の全幅であって片側の幅ではない。"
    "材料1 の割合が得られない場合、および割合が 0 または 1 に振り切れて"
    "観測されたばらつきが 0 になる場合は、必要試行回数を欠測とし"
    "0 で埋めない。"
    "【留保】許容窓の値は対象物の寸法に依存し、"
    "対象物の最終スコープは未決である（要件 10.5、要件 13.9）。"
    "窓が変われば割合も必要試行回数も変わる。"
    "ここに出ている許容窓・信頼区間幅・信頼水準は暫定の評価候補であって"
    "必須性能ではない（要件 13.7）。"
    "本材料は OQ-05 を決着させず、判断材料の提示までにとどめる（要件 10.4）。"
)

# --- 注記（**出力に残す**。要件 10.2 / 10.4 / 10.5）------------------------
#
# 3つとも「読み手が材料を誤読しないため」の警告であり、互いに別物である。
# 上限の読み方を落とすと期待値が独り歩きし、窓の留保を落とすと未決の寸法が
# 確定値として読まれ、決着させない旨を落とすと材料が結論として読まれる。

_UPPER_BOUND_NOTE: str = (
    "この割合は予測側から見た成功率の上限であって、"
    "キャッチ成功率そのものではない（要件 10.2）。"
    "予測が許容窓に収まっても、移動体が間に合わなければ、"
    "あるいは対象物が開口から跳ね出せば、キャッチは成立しない。"
)

_OBJECT_SCOPE_NOTE: str = (
    "許容窓の値は対象物の寸法に依存し、対象物の最終スコープは未決である"
    "（要件 10.5 / 13.9）。窓が変われば割合も必要試行回数も変わる。"
)

_MATERIAL_ONLY_NOTE: str = (
    "本結果は OQ-05（NFR-7 の目標成功率と試行回数 N）を決着させない"
    "（要件 10.4）。M1 単独では移動体の性能が入らないため成功率を決められず、"
    "判定値は常に material_only である。"
)

# --- 理由（**相互排他**。材料の欠け方によって次にやることが違う）------------
#
# 「投げる回数を増やす」「窓か対象物の寸法を見直す」「そのまま材料として使う」
# は別の行動である。取り違えて記録すると、次にやるべきことを間違える
# （タスク 5.2 / 6.1 の教訓）。

_RATIONALE_MEASURED: str = (
    "誤差ベクトルが得られた {evaluated} 件の投擲のうち {within} 件が"
    "暫定許容窓に収まった（材料1）。"
    "必要試行回数は信頼区間幅ごとに算出した（材料2）。"
)
_RATIONALE_NO_VECTORS: str = (
    "誤差ベクトルが1件も無く、暫定許容窓に収まる割合を算出できない"
    "（材料1・材料2とも欠測）。"
)
_RATIONALE_DEGENERATE: str = (
    "暫定許容窓に収まる割合が 0 または 1 に振り切れており、"
    "観測されたばらつきが 0 になるため必要試行回数を見積もれない"
    "（材料2 のみ欠測）。"
)


def oq05_criterion(
    *,
    window_mm: float,
    aperture_diameter_mm: float,
    object_diameter_mm: float,
    confidence_level: float,
    interval_widths: Sequence[float],
) -> str:
    """実際に適用する材料の作り方の説明文を組み立てる。

    **実際に使った窓・寸法・信頼水準・区間幅を文面へ入れる。** 規則の文だけを
    残して数値を伏せると、同じ文で違う材料が正当化できてしまう
    （`oq27.oq27_criterion` / `attribution.attribution_criterion` と同じ理由）。
    文面は材料の中身によって変わらない——結果に合わせて動く規則は規則ではない。
    """
    widths = " / ".join(f"{width:g}" for width in interval_widths)
    return _CRITERION_TEMPLATE.format(
        window=window_mm,
        aperture=aperture_diameter_mm,
        object_mm=object_diameter_mm,
        level=confidence_level,
        widths=widths,
    )


# ---------------------------------------------------------------------------
# 結果
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Oq05Result:
    """OQ-05 の判断材料（要件 10.1-10.5）。

    Attributes:
        window_mm: 位置精度の暫定許容窓（mm）。`ThrowLayout` の開口寸法と
            対象物寸法から導いた値であり、**合否条件ではない**
            （`ThrowLayout.position_tolerance_mm` の注記）。
        within_window_ratio: 予測が窓に収まる割合（要件 10.1）。誤差ベクトルが
            1件も無ければ `None`（**0 で埋めない**——「1件も収まらなかった」
            と「測っていない」は別である）。
        within_window_count: 窓に収まった投擲数。
        evaluated_throw_count: 割合の**分母**。誤差ベクトルが得られた投擲数で
            あり、`ThrowAggregate.throw_count` でも `valid_throw_count` でも
            ない（真値はあるが有効な予測が0件の投擲は誤差ベクトルを持たない）。
        confidence_level: 材料2 に用いた信頼水準（`Oq05Config`）。
        required_trials: 信頼区間幅ごとの必要試行回数（要件 10.3）。キーは
            `required_trials_key()` が組む幅の表示。見積もれない場合は `None`。
        upper_bound_note: **予測側から見た上限**である旨（要件 10.2）。
        object_scope_note: 許容窓が対象物の寸法に依存し、対象物の最終スコープが
            未決である旨（要件 10.5）。
        material_only_note: OQ-05 を決着させず材料の提示にとどめる旨
            （要件 10.4）。
        judgement: 判断の共通の形。`verdict` は常に `VERDICT_MATERIAL_ONLY`。

    Postconditions:
        `judgement.verdict == VERDICT_MATERIAL_ONLY`（要件 10.4）。入力にも
        設定にも依存しない。

    **design.md の擬似コードとの差**（フィーチャレベル検証で同期すること）:

    - `within_window_ratio` を `float | None`、`required_trials` の値を
      `int | None` にした。擬似コードは非 optional だが、`ThrowAggregate` の
      誤差ベクトルは 0 件になりうるし、割合が 0 / 1 に振り切れた群では正規
      近似の分散が 0 になって必要試行回数を見積もれない。0 で埋めると
      **「測っていない」が「材料が出た」として判断へ入る**（タスク 4.6 /
      5.1 / 6.1 と同じ判断）。
    - `within_window_count` / `evaluated_throw_count` / `confidence_level` /
      `material_only_note` を足した。割合だけでは分母が読めず（8 件中 6 件と
      80 件中 60 件は材料としての重みが違う）、要件 10.4 の「材料の提示に
      とどめる旨」は `upper_bound_note` / `object_scope_note` と並ぶ独立の
      注記だからである。
    """

    window_mm: float
    within_window_ratio: float | None
    within_window_count: int
    evaluated_throw_count: int
    confidence_level: float
    required_trials: Mapping[str, int | None]
    upper_bound_note: str
    object_scope_note: str
    material_only_note: str
    judgement: Judgement

    def __post_init__(self) -> None:
        # 材料は「その時点の集計からこう出した」という記録である。呼び出し側が
        # 使い回すマッピングを抱えると、レポートの数値が算出時と食い違い得る
        # （`Judgement.evidence` / `ThrowAggregate.items` と同じ方針）。
        object.__setattr__(self, "required_trials", dict(self.required_trials))


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def oq05_material(aggregate: ThrowAggregate, *, settings: M1Settings) -> Oq05Result:
    """OQ-05 の判断材料を作る（要件 10.1-10.5）。**決着させない。**

    材料の作り方は `oq05_criterion()` が返す説明文のとおりであり、**実測前に
    固定されている**。結果の `judgement.criterion` に同じ文面を埋め込むので、
    あとから作り方のほうを結果に合わせて読み替えられない。

    **本関数は OQ-05 を決着させず、判断材料の提示までにとどめる**（要件
    10.4）。判定値は入力によらず `VERDICT_MATERIAL_ONLY` の1値である。
    目標成功率も試行回数 N も、移動体の実測（M2）が入ってから決める。

    Args:
        aggregate: 1つのキャリブレーション（識別子 × 検証状態）に属する投擲群
            の集計。**誤差ベクトル群**（`error_vectors`）と最終誤差の分布を
            ここから読む。
        settings: 解決済みの設定。`layout`（暫定許容窓の出どころ）と
            `oq05`（信頼水準・信頼区間幅）を読む。

    Returns:
        `Oq05Result`。**材料が欠測でも結果は返る**——注記と作り方の説明文は
        材料の有無によらず残り、欠測は `None` として表れる。

    Notes:
        同一入力・同一設定に対して同一の材料を返す（要件 12.4）。乱数を使わず、
        信頼区間幅の並びも設定の順序をそのまま保つ。

        **`settings` を受け取るのは design.md の擬似コード署名からの意図した
        差である。** 擬似コードは `oq05_material(aggregate, *, layout)` だが、
        信頼水準と信頼区間幅は `M1Settings.oq05` にあり、`layout` は
        `settings.layout` と同一物である。レイアウトへの経路を2つ持つと、
        タスク 1.5 が設定を1箇所へ集めた意味が消える（タスク 4.4 が
        `analyze_convergence` に対して採った処置と同型）。
    """
    layout = settings.layout
    config = settings.oq05
    window_mm = layout.position_tolerance_mm

    vectors = aggregate.error_vectors
    evaluated = len(vectors)
    within = sum(1 for vector in vectors if _within_window(vector, window_mm))
    ratio = None if evaluated == 0 else within / evaluated

    required = {
        required_trials_key(width): _required_trials(
            ratio=ratio, width=width, confidence_level=config.confidence_level
        )
        for width in config.interval_widths
    }

    if ratio is None:
        rationale = _RATIONALE_NO_VECTORS
    elif _spread_is_degenerate(ratio):
        rationale = _RATIONALE_DEGENERATE
    else:
        rationale = _RATIONALE_MEASURED.format(evaluated=evaluated, within=within)

    final_error = aggregate.items.get(ITEM_HIT_ERROR_FINAL_MM)

    return Oq05Result(
        window_mm=window_mm,
        within_window_ratio=ratio,
        within_window_count=within,
        evaluated_throw_count=evaluated,
        confidence_level=config.confidence_level,
        required_trials=required,
        upper_bound_note=_UPPER_BOUND_NOTE,
        object_scope_note=_OBJECT_SCOPE_NOTE,
        material_only_note=_MATERIAL_ONLY_NOTE,
        judgement=Judgement(
            question=OQ05_QUESTION,
            criterion=oq05_criterion(
                window_mm=window_mm,
                aperture_diameter_mm=layout.aperture_diameter_mm,
                object_diameter_mm=layout.object_diameter_mm,
                confidence_level=config.confidence_level,
                interval_widths=config.interval_widths,
            ),
            verdict=VERDICT_MATERIAL_ONLY,
            rationale=rationale,
            evidence={
                "calibration_id": aggregate.calibration_id,
                "verified": aggregate.verified,
                "throw_count": aggregate.throw_count,
                "valid_throw_count": aggregate.valid_throw_count,
                "layout_id": layout.layout_id,
                "window_mm": window_mm,
                "aperture_diameter_mm": layout.aperture_diameter_mm,
                "object_diameter_mm": layout.object_diameter_mm,
                "within_window_ratio": ratio,
                "within_window_count": within,
                "evaluated_throw_count": evaluated,
                "confidence_level": config.confidence_level,
                "interval_widths": list(config.interval_widths),
                "required_trials": dict(required),
                # ばらつきを併記する（要件 5.9 の方針）。代表値だけでは
                # 「窓に収まらなかった投擲がどれだけ外れたか」が読めない。
                "hit_error_norm_final_median_mm": (
                    None if final_error is None else final_error.median
                ),
                "hit_error_norm_final_p95_mm": (
                    None if final_error is None else final_error.p95
                ),
                "aggregate_provisional": aggregate.provisional,
                "upper_bound_note": _UPPER_BOUND_NOTE,
                "object_scope_note": _OBJECT_SCOPE_NOTE,
                "material_only_note": _MATERIAL_ONLY_NOTE,
            },
            # 暫定の印は「判断に用いてよい状態ではない」ことを示す
            # （要件 5.10）。集計が暫定（試行数下限未達など）でも、材料1 が
            # 欠測でも立つ。**2項はそれぞれ単独で効く**——片方に畳むと、
            # 試行数が足りているのに材料が空の集計に印が付かなくなる。
            provisional=(aggregate.provisional or ratio is None),
        ),
    )


# ---------------------------------------------------------------------------
# 材料1（要件 10.1 / 10.2）
# ---------------------------------------------------------------------------


def _within_window(vector: tuple[float, float], window_mm: float) -> bool:
    """誤差ベクトルが暫定許容窓に収まるか。**ノルムで判定する。**

    片成分（x だけ・y だけ）で判定すると、**斜めに外した投擲が「収まった」
    ことになる**——窓 110 mm に対して誤差 `(90, 90)` は各成分こそ窓以下だが、
    落下地点は 127 mm 離れている。要件 10.1 が言う「位置精度の許容窓」は
    水平面上の距離であり、`ThrowLayout.position_tolerance_mm` も開口と対象物の
    半径差、すなわち**距離**として導かれている。

    窓の値**ちょうど**は「収まる」に含む（`<=`）。境界の扱いは規則の一部で
    あり、`criterion` に明記してある。
    """
    return math.hypot(vector[0], vector[1]) <= window_mm


# ---------------------------------------------------------------------------
# 材料2（要件 10.3）
# ---------------------------------------------------------------------------


def _spread_is_degenerate(ratio: float) -> bool:
    """観測されたばらつき p (1 − p) が 0 になる割合か。

    割合が 0 または 1 に振り切れた群では正規近似の分散が 0 になり、素直に
    式へ入れると「0 回の試行でよい」という明らかに誤った材料が出る。
    **見積もれないことを欠測として返す**——0 で埋めない。
    """
    return ratio <= 0.0 or ratio >= 1.0


def _required_trials(
    *, ratio: float | None, width: float, confidence_level: float
) -> int | None:
    """所望の信頼区間幅を得るために必要な試行回数（要件 10.3）。

    二項比率の正規近似による信頼区間の**全幅** W は
    `W = 2 z sqrt(p (1 - p) / n)` である。これを n について解くと

        n = 4 z^2 p (1 - p) / W^2

    となり、小数点以下を**切り上げる**（切り捨てると、要求した区間幅に届かない
    試行数を材料として出すことになる）。

    - `p` は材料1 の割合。**観測されたばらつき**は `p (1 - p)` として入る。
    - `z` は信頼水準に対する標準正規分布の**両側**分位点
      （`NormalDist().inv_cdf(0.5 + confidence_level / 2)`）。

    **新しい統計手法を導入しない**（design.md「Oq05Material」Integration）。
    正規近似は割合が 0 / 1 の近傍で破綻することが知られているが、本 Spec は
    測定であって改善ではなく（A-1）、破綻する場合は欠測として返す方針を
    採っている（`_spread_is_degenerate`）。より適切な区間（Wilson 等）へ
    差し替えるかどうかは、材料が揃ってから OQ-05 の場で決める事項である。

    Returns:
        必要試行回数。割合が得られていない、または観測されたばらつきが 0 に
        なる場合は `None`（**0 で埋めない**）。
    """
    if ratio is None or _spread_is_degenerate(ratio):
        return None
    z = NormalDist().inv_cdf(0.5 + confidence_level / 2.0)
    return math.ceil(4.0 * z * z * ratio * (1.0 - ratio) / (width * width))
