"""実測項目 7（有効サンプル数・収束サンプル数）と、収束の判定規則。

design.md「Components and Interfaces / L4-L5: 真値と実測 /
ConvergenceAnalyzer」、research.md Decision 8、tasks.md タスク 4.4、
要件 5.7, 5.8。

**収束は真値との誤差では測らない。** 基準は**その投擲の最終予測**であり、
「サンプル数 N 以降のすべての予測落下地点が最終予測から一定の帯域内に
収まり続ける最小の N」を収束サンプル数とする（research.md Decision 8）。
真値との比較で定義すると、真値が欠測した投擲では収束そのものが測れなく
なる——**最終予測との比較なら投擲内で完結する**。真値との誤差は別項目
（要件 5.4、`accuracy.py`）が担う。

**その代償として、最終予測自体がずれていれば収束は速く見える。** 予測が
早々に「同じ場所」を指し続けても、その場所が実際の落下地点から外れていれば
何の役にも立たない。したがって本モジュールは**収束サンプル数と最終誤差を
必ず併記する**（要件 5.7）——`ConvergenceResult` が両方を持つだけでなく、
判断（`Judgement`）の説明文と証跡にも両方を載せる。片方だけを取り出せる
戻り値は用意しない。

**判定規則は実測前に固定し、算出結果と同じ場所に記録する**（要件 5.8）。
`ConvergenceResult.judgement.criterion` に、実際に使った帯域と規則の別
（末尾条件の有無）まで含めた説明文が入る。判定値だけが残って規則が失われる
と、**あとから規則のほうを結果に合わせて読み替えられる**。

**未収束は正常な結果である**（design.md「Error Categories and Responses」)。
例外にも欠測にもしない。ただし「真値が無くて誤差系列が作れなかった」ことと
「予測が最後まで落ち着かなかった」ことは**別の結果**として返す——混ぜると、
真値を書き忘れた投擲が「収束しなかった投擲」として収束サンプル数の分布を
悪い方へ引っ張る。

本モジュールは L5 層の評価側であり、`sensing_foundation` /
`flying_object_tracking` / `world_frame_calibration` を import しない
（design.md「Allowed Dependencies」）。数値計算は標準ライブラリだけで書く。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from m1_validation.config import M1Settings
from m1_validation.errors import M1ConfigError
from m1_validation.metrics.accuracy import AccuracyResult
from m1_validation.types import Judgement
from prediction_core import SourceKind, ThrowRecord

#: `Judgement.question`。判断の種類を引く語であり、`research.md` の
#: 「すべての判断を『説明文 ＋ 判定値 ＋ 根拠』の共通の形に載せる」に従う。
CONVERGENCE_QUESTION: str = "convergence"

#: 判定値の語彙（`Judgement.verdict`）。**3値である**。「測れなかった」を
#: 「収束しなかった」に畳まない（モジュール docstring 参照）。
VERDICT_CONVERGED: str = "converged"
VERDICT_NOT_CONVERGED: str = "not_converged"
VERDICT_NOT_MEASURABLE: str = "not_measurable"

#: `judgement.evidence` の `band_source`。帯域がどこから来たかを残す。
BAND_SOURCE_SETTINGS: str = "settings.convergence.band_mm"
BAND_SOURCE_LAYOUT: str = "layout.position_tolerance_mm（暫定許容窓）"

_CRITERION_HEAD: str = (
    "収束サンプル数の判定規則（実測前に固定。research.md Decision 8、要件 5.8）: "
)

_CRITERION_TAIL_RULE: str = (
    "サンプル数 N 以降の**すべての**有効な予測落下地点が、その投擲の"
    "最終予測から {band:g} mm 以内に収まり続ける最小の N を収束サンプル数と"
    "する。"
)

_CRITERION_FIRST_TOUCH_RULE: str = (
    "有効な予測落下地点が、その投擲の最終予測から {band:g} mm 以内へ"
    "**最初に**入ったときのサンプル数を収束サンプル数とする"
    "（require_monotonic_tail=false のため、以降も帯域内に留まり続けることは"
    "要求しない。いったん帯域へ入って出た列でも早い値が出る）。"
)

_CRITERION_COMMON: str = (
    "帯域は**最終予測との距離**に対する閾値であり、真値との誤差ではない"
    "（真値が欠測した投擲でも収束を測れるようにするため）。境界はちょうどの値を"
    "帯域内に含む。最終予測は自分自身との距離が 0 なので必ず条件を満たすので、"
    "**最終予測より前のどの予測も帯域に留まらなかった投擲は「未収束」とする**"
    "——そうしないと「投擲が終わったから収束した」と言うのと同じになる。"
    "未収束は異常ではなく正常な結果である。有効な予測が1件も無い（真値の欠測を"
    "含む）投擲は「測定不能」であり、未収束とは区別する。"
    "帯域の既定はレイアウトの暫定許容窓に揃えるが、**これは暫定の評価候補で"
    "あって合否条件ではない**。収束サンプル数は最終予測を基準にしているため、"
    "**最終予測自体がずれていれば収束は速く見える**。したがって収束サンプル数と"
    "最終誤差は必ず併記して読むこと。"
)


def convergence_criterion(*, band_mm: float, require_monotonic_tail: bool) -> str:
    """実際に適用する規則の説明文を組み立てる（要件 5.8）。

    **実際に使った帯域と規則の別を文面へ入れる。** 規則の文だけを残して帯域を
    伏せると、同じ文で違う判定が正当化できてしまう。
    """
    rule = (
        _CRITERION_TAIL_RULE
        if require_monotonic_tail
        else _CRITERION_FIRST_TOUCH_RULE
    )
    return _CRITERION_HEAD + rule.format(band=band_mm) + _CRITERION_COMMON


@dataclass(frozen=True, slots=True)
class ConvergenceResult:
    """1投擲ぶんの実測項目 7 と、その判断（要件 5.7, 5.8）。

    Attributes:
        valid_samples: **1投擲で得られた有効サンプル数**（`record.samples` の
            件数）。継ぎ目の除外規則を通ったあとに残った観測の件数であり、
            **最終予測のサンプル数ではない**——末尾の予測が無効になった投擲
            では両者がずれる。
        converged_at: 収束サンプル数。未収束・測定不能なら `None`
            （**0 で埋めない**。「1サンプルで収束した」と読まれる）。
        band_mm: 実際に使った帯域（mm）。`M1Settings.effective_convergence_band_mm`
            の値であり、既定はレイアウトの暫定許容窓である。
        final_error_mm: **最終予測の落下地点誤差**（mm）。真値が欠測なら
            `None`。`converged_at` と**必ず併記する**ための同居フィールドで
            ある（モジュール docstring 参照）。
        judgement: 判断。`criterion` に実測前に固定した規則の説明文、
            `rationale` と `evidence` に**収束サンプル数と最終誤差の両方**が
            載る。

    **design.md の擬似コードとの差**: 無い（`valid_samples` / `converged_at` /
    `band_mm` / `final_error_mm` / `judgement` の5フィールドのまま）。差が
    あるのは `analyze_convergence()` の引数のほうである（同関数の docstring
    を参照）。
    """

    valid_samples: int
    converged_at: int | None
    band_mm: float
    final_error_mm: float | None
    judgement: Judgement


def analyze_convergence(
    record: ThrowRecord, accuracy: AccuracyResult, *, settings: M1Settings
) -> ConvergenceResult:
    """実測項目 7 を算出し、固定した規則で収束を判定する（要件 5.7, 5.8）。

    Args:
        record: 対象の投擲記録。ここから読むのは**有効サンプル数**
            （`samples` の件数）と**入力元**（`source`。暫定の印に使う）
            だけである。予測系列は `accuracy` 側が既に整理している。
        accuracy: 同じ投擲の誤差系列（`measure_accuracy()` の戻り値）。
            収束は `errors` の**誤差ベクトルの差**として測る——誤差は
            `予測 − 実測` なので、2つの誤差ベクトルの差を取ると真値が消え、
            **予測どうしの距離**になる。したがって基準は最終予測であり、
            真値の値そのものは収束判定に影響しない。
        settings: 解決済みの設定。帯域は
            `M1Settings.effective_convergence_band_mm` から取る——
            `band_mm or layout.position_tolerance_mm` を利用側で書くと、
            **設定した値と実際に使う値が食い違う経路が増える**（導出は
            `config.py` に1箇所だけ置いてある）。

    Returns:
        `ConvergenceResult`。未収束・測定不能は**正常な結果**として返り、
        例外にはならない。

    Raises:
        M1ConfigError: 帯域が正の有限値でない場合、または `accuracy` が
            `record` の投擲のものではありえない場合（記録のサンプル数を
            超えるサンプル数の誤差が含まれる）。**取り違えたまま算出すると
            「11 サンプルのうち 14 サンプル目で収束」という読めない結果が
            集計へ流れる**（`measure_accuracy()` が record_id の取り違えを
            拒否するのと同じ理由）。

    **design.md の擬似コードとの差**: design.md の署名は
    `analyze_convergence(accuracy, *, settings, layout)` である。ここでは
    `record` を足し、`layout` を落とした。

    - `record` を足したのは、要件 5.7 が求める「**1投擲で得られた有効
      サンプル数**」が `AccuracyResult` から出せないからである。誤差系列は
      有効な予測しか持たないので、末尾の予測が無効になった投擲では最終予測の
      サンプル数が実サンプル数より小さく、真値が欠測した投擲では誤差系列が
      空になる。どちらでも「得られたサンプル数」を取り違える。
    - `layout` を落としたのは、`settings.layout` が同じものであり、2経路
      から渡せる形にすると**帯域の導出に食い違う入力を与えられる**からで
      ある（タスク1.5 が導出を1箇所へ集めた理由と同じ）。
    """
    band_mm = _require_usable_band_mm(settings)
    valid_samples = len(record.samples)
    _require_same_throw(record, accuracy, valid_samples=valid_samples)

    errors = accuracy.errors
    final = accuracy.final
    require_monotonic_tail = settings.convergence.require_monotonic_tail

    deviations_mm = _deviations_from_final_mm(accuracy)
    converged_index = _converged_index(
        deviations_mm, band_mm=band_mm, require_monotonic_tail=require_monotonic_tail
    )
    converged_at = (
        None if converged_index is None else errors[converged_index].sample_count
    )
    final_error_mm = None if final is None else final.hit_error_norm_mm

    if not errors:
        verdict = VERDICT_NOT_MEASURABLE
    elif converged_at is None:
        verdict = VERDICT_NOT_CONVERGED
    else:
        verdict = VERDICT_CONVERGED

    band_source = (
        BAND_SOURCE_LAYOUT
        if settings.convergence.band_mm is None
        else BAND_SOURCE_SETTINGS
    )
    max_deviation_mm = max(deviations_mm) if deviations_mm else None
    provisional = settings.trials.require_live_source and record.source is not (
        SourceKind.LIVE
    )

    judgement = Judgement(
        question=CONVERGENCE_QUESTION,
        criterion=convergence_criterion(
            band_mm=band_mm, require_monotonic_tail=require_monotonic_tail
        ),
        verdict=verdict,
        rationale=_rationale(
            verdict=verdict,
            valid_samples=valid_samples,
            prediction_count=len(errors),
            converged_at=converged_at,
            band_mm=band_mm,
            final_error_mm=final_error_mm,
        ),
        evidence={
            "record_id": record.record_id,
            "source": str(record.source),
            "valid_samples": valid_samples,
            "prediction_count": len(errors),
            "converged_at": converged_at,
            "band_mm": band_mm,
            "band_source": band_source,
            "require_monotonic_tail": require_monotonic_tail,
            "final_error_mm": final_error_mm,
            "final_sample_count": None if final is None else final.sample_count,
            "max_deviation_from_final_mm": max_deviation_mm,
        },
        provisional=provisional,
    )
    return ConvergenceResult(
        valid_samples=valid_samples,
        converged_at=converged_at,
        band_mm=band_mm,
        final_error_mm=final_error_mm,
        judgement=judgement,
    )


def _deviations_from_final_mm(accuracy: AccuracyResult) -> tuple[float, ...]:
    """各予測の落下地点と**最終予測**の落下地点との水平距離（mm）。

    誤差は `予測 − 実測` なので、差を取ると実測（真値）が消えて予測どうしの
    距離になる。**真値の値が収束判定に影響しない**のはこのためであり、
    research.md Decision 8 が「投擲内で完結する」と言っているのがこの性質で
    ある。高さは扱わない（要件 5.4 と同じく水平2成分）。
    """
    final = accuracy.final
    if final is None:
        return ()
    final_x_mm, final_y_mm = final.hit_error_mm
    return tuple(
        math.hypot(
            error.hit_error_mm[0] - final_x_mm, error.hit_error_mm[1] - final_y_mm
        )
        for error in accuracy.errors
    )


def _converged_index(
    deviations_mm: tuple[float, ...],
    *,
    band_mm: float,
    require_monotonic_tail: bool,
) -> int | None:
    """収束したとみなす**系列上の添字**。未収束なら `None`。

    最終予測（末尾）は自分自身との距離が 0 なので必ず帯域内である。それだけ
    が帯域内である列を「収束」と呼ぶと**「投擲が終わったから収束した」**と
    言っているのと同じなので、末尾しか該当しない場合は未収束とする
    （説明文にも同じことを書いてある）。有効な予測が1件だけの投擲も、
    この規則によって未収束になる——1点では落ち着いたかどうかを観測できない。
    """
    last = len(deviations_mm) - 1
    if last < 1:
        return None

    within = [deviation_mm <= band_mm for deviation_mm in deviations_mm]
    if require_monotonic_tail:
        index = last
        while index >= 1 and within[index - 1]:
            index -= 1
    else:
        index = next(
            (position for position, inside in enumerate(within) if inside), last
        )
    return None if index >= last else index


def _rationale(
    *,
    verdict: str,
    valid_samples: int,
    prediction_count: int,
    converged_at: int | None,
    band_mm: float,
    final_error_mm: float | None,
) -> str:
    """判定の説明文。**収束サンプル数と最終誤差を必ず同じ文へ載せる。**

    レポートが判定値だけを転記しても、説明文を運べば併記が保たれる
    （要件 5.7 が両者の併記を求める理由はモジュール docstring 参照）。
    """
    final_error_text = (
        "最終誤差は欠測（落下地点の真値が無い）"
        if final_error_mm is None
        else f"最終予測の落下地点誤差は {final_error_mm:g} mm"
    )
    head = (
        f"有効サンプル {valid_samples} 件・有効な予測 {prediction_count} 件。"
    )
    if verdict == VERDICT_NOT_MEASURABLE:
        return (
            head
            + "誤差系列が空のため収束を測れない（測定不能。未収束とは別である）。"
            + f"{final_error_text}。"
        )
    if verdict == VERDICT_NOT_CONVERGED:
        return (
            head
            + f"最終予測より前のどの予測も最終予測から {band_mm:g} mm 以内に"
            "留まらなかったため未収束（正常な結果である）。"
            + f"{final_error_text}。"
        )
    return (
        head
        + f"{converged_at} サンプル目以降の予測が最終予測から {band_mm:g} mm 以内に"
        f"留まり続けたため、収束サンプル数は {converged_at} である。"
        + f"{final_error_text}。"
        + "**収束の速さは最終予測からの距離で測っており、最終予測自体のずれは"
        "含まない。両者を必ず併せて読むこと。**"
    )


def _require_usable_band_mm(settings: M1Settings) -> float:
    """実効の帯域を取り出す。正の有限値でなければ**設定の誤り**として拒否する。

    黙って受けると、どの投擲も「未収束」という**もっともらしい正常値**に
    なり、設定の誤りが結果の読み違いとして表に出る（design.md「Error
    Categories and Responses」の「設定の誤り」）。`M1Settings.resolve()` は
    同じ条件を起動時に拒否するが、設定を直接組み立てる経路もあるため境界で
    確かめる。
    """
    band_mm = settings.effective_convergence_band_mm
    if not math.isfinite(band_mm) or band_mm <= 0.0:
        raise M1ConfigError(
            f"収束の帯域が正の有限値でない: {band_mm!r}。"
            "この値ではどの投擲も「未収束」というもっともらしい結果になり、"
            "設定の誤りが結果の読み違いとして表に出る",
            {"band_mm": band_mm},
        )
    return band_mm


def _require_same_throw(
    record: ThrowRecord, accuracy: AccuracyResult, *, valid_samples: int
) -> None:
    """誤差系列がその記録の投擲のものでありえるかを確かめる。

    予測は蓄積済みの全観測から作られるので、どの誤差の `sample_count` も
    記録のサンプル数を超えない。超えていれば**別の投擲の誤差系列**である。
    `AccuracyResult` は投擲の識別子を持たない（`accuracy.py` の設計）ため
    完全な照合はできないが、この不変条件だけでも取り違えの大半は落ちる。
    """
    for error in accuracy.errors:
        if error.sample_count > valid_samples:
            raise M1ConfigError(
                "誤差系列が記録と対応していない: サンプル数 "
                f"{error.sample_count} の予測誤差があるが、記録の有効サンプルは "
                f"{valid_samples} 件しかない。別の投擲の誤差系列で収束を測ると、"
                "読めない収束サンプル数が集計へ流れる",
                {
                    "record_id": record.record_id,
                    "valid_samples": valid_samples,
                    "error_sample_count": error.sample_count,
                },
            )
