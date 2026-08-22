"""検出方式の比較と判定規則（design.md「L8-L9: 比較・入口 → DetectorComparison」、
タスク 7.1）。OQ-26（物体検出方式の選定）を実測比較で決着させる。

本モジュールは3つの責務を分離して持つ:

1. **実行**（`compare_detectors()`）: 同一の記録済みセッションに対して3方式
   （またはそのうち呼び出し側が指定した任意個数）を独立に実行し、方式ごとに
   同一の指標（`DetectorRunResult`）を算出する。
2. **判定**（`decide()`）: 実行から独立した**純粋関数**。`DetectorRunResult`
   の列から `DetectorDecision` を導く。実測を経由しないため、人工的な
   `DetectorRunResult` を直接与えて判定規則だけを単体でテストできる
   （タスク本文の観測可能な完了状態「差が四分位範囲以内の人工データで
   処理時間による決着が働くことをテストで固定する」がこの分離を要求する）。
3. **書き出し**（`write_comparison_result()`）: `DetectorComparisonResult` を
   `var/tracking/compare-<session_id>.json` へ JSON 化する。`measurements.md`
   への記録は行わない（そのファイルは実機データが揃う後続タスク（9.x）が
   新規作成する。本タスクの時点では存在せず、埋めるべき実測値も無い）。

## 判定規則（design.md「決着させる未決事項」節。実測前に確定させ、`DECISION_
CRITERION` として結果と同じファイルに記録する）

    1. effective_points_per_window が最大の方式を第一候補とする
    2. 方式間の差が各方式の四分位範囲（detect_ms の IQR）以内なら
       区別がついていないとみなし、detect 区間の処理時間 p95 が
       小さい方を採る
    3. なお並ぶ場合は、設定パラメータが少ない方を採る
    4. 絶対値の目標を置かない。すべて方式間の相対比較とばらつきで定義する
    5. 決めきれない場合（selected=None）を正常な結果として扱う

`DECISION_CRITERION` はモジュールレベルの定数文字列であり、どの実測結果にも
依存しない（＝実測前に確定済み）。`decide()` は常にこの文字列をそのまま
`DetectorDecision.criterion` に格納する。

## ルール2「四分位範囲」の解釈についての設計判断

design.md 本文・タスクブリーフのいずれも「方式間の差が各方式の四分位範囲
以内」という言い回しだけを与え、比較する2方式のうちどちらの IQR を基準に
するかを明記していない。`DetectorRunResult` が IQR を持つのは
`detect_ms_iqr` のみ（`effective_points_per_window` 自体は単一の実行から
出るスカラー値であり、分布を持たないため IQR を定義できない——`research.md`
Decision 7 が「ばらつきの指標として `detect_ms_iqr` を流用する」という
判定規則を選んだ理由もここにある）。本実装は2方式の `detect_ms_iqr` の
**大きい方**をしきい値として採用する（`max(a.detect_ms_iqr, b.detect_ms_iqr)`）。
一方だけの IQR を使うと非対称になり「A から見れば区別つかないが B から見れば
区別がつく」という自己矛盾した状態を許してしまうため、大きい方（より保守的
＝ 区別がついていないと判定しやすい側）を採る。「決めきれない場合を正常な
結果として扱う」（要件 4.9）という設計全体の方針とも整合する。

## パラメータ数の定義（ルール3。設計判断として文書化する）

`DetectorConfig`（`config.py`）は3方式すべてに対して**同一のデータクラス**
であり、`dataclasses.fields(DetectorConfig)` は方式に関わらず同じ件数を返す
——これをそのまま使うとルール3が方式間で常に同点になり機能しない。

本実装は「その方式の `MaskBuilder` 実装（`detection/masks/*.py`）が実際に
コンストラクタで受け取る `DetectorConfig` フィールドの個数」を数える
（各モジュールのソースを確認して確定した対応関係。`_KIND_SPECIFIC_PARAM_
FIELDS` 参照）:

    共通（3方式とも `DefaultDetector.detect()` の共有後段が使う。
    `detection/detector.py` 参照）:
        open_kernel_px, close_kernel_px, max_candidates
    depth_band 固有: なし（`masks/depth_band.py`: `roi` の距離帯と
        `depth_scale_mm` のみを使う。距離帯は `RoiConfig` 側であり
        `DetectorConfig` のフィールドではない）
    frame_diff 固有: diff_threshold_mm（`masks/frame_diff.py`）
    background 固有: diff_threshold_mm, background_frames,
        background_update_rate（`masks/background.py`）

したがって depth_band=3, frame_diff=4, background=6 となり、完全同点の
シナリオでは depth_band が最少パラメータとして選ばれる
（design.md が `depth_band` を「3方式のうち状態を持たない最軽量の
ベースライン」と表現しているのと整合する、独立に導出した結果）。

## 実行時の計測経路についての設計判断

`compare_detectors()` は各方式について新たに `TrackingPipeline` を構築し、
`settings.measurement.enabled` を強制的に `True` にした複製設定で実行する
（比較そのものが計測値を必要とするため）。この上書きは `roi` /
`object_model` / `projection` / `tracker` のいずれにも触れないため
`postprocess_fingerprint()` の対象フィールドには影響せず、検出アルゴリズム
の挙動（`measurement.enabled` は計測の記録有無だけを切り替え、検出結果その
ものには影響しない設計——`metrics.py` docstring参照）も変えない。

`detect_ms` の生の値列は `TrackingMetrics.record_update()` が送出する
`stage="detect", event="frame"` イベントの `detect_ms` フィールドからしか
取り出せない（`TrackingPipeline.counters()` は集計済みカウンタのみを返し、
生の所要時間列を保持しない設計——`metrics.py`「集計を行わない」節）。本
モジュールはこのためだけに `_CapturingLogger`（本モジュール内だけで使う、
`Logger` プロトコルを満たす最小限のインメモリシンク）を実装する。上流の
`StructuredLogger`（ファイル書き出し＋バックグラウンドスレッド）を経由して
`sensing_foundation.summarize_log()` で読み戻す経路も検討したが、
`summarize_log()` が返す `FieldStats` は p50/p95/mean/min/max のみで
四分位範囲（Q1/Q3）を持たないため、判定規則が要求する `detect_ms_iqr` を
そのままでは得られない。したがって生の値列を直接保持する経路を採る。

一方、`effective_points_per_window` の算出に使う各点の `t_ms` はログを経由
しない——`TrackingPipeline.process()` の戻り値（`TrackUpdate.appended.point.
t_ms`）から直接得られるため、`EffectiveWindow` へ直接 `add()` する。

## 独立実行と失敗時の扱い（design.md Batch/Job Contract の設計判断）

`runs` は `DetectorRunResult | None` のタプルとし（design.md 自身が
「`runs` タプルの対応要素を `DetectorRunResult | None` にする」ことを選択肢
として明示する）、失敗した方式のスロットには `None` を置く。加えて
`failures: Mapping[str, str]`（`detector_kind` 文字列 → 例外の型名とメッセ
ージ）を別途持たせることで、失敗の理由を読める形で残す（design.md の
もう一つの選択肢「別途エラー内訳を持つ」も同時に採用する、二重の設計判断
——`runs` の `None` だけでは「なぜ失敗したか」が失われるため）。この
2つの選択は互いに排他ではなく補完し合う: `runs` の `None` は「この位置の
方式は実行できなかった」という事実を型で表明し、`failures` はその理由を
人間可読な形で補う。

各方式の実行は完全に独立した `try/except` で囲む（`TrackingPipeline.
process()` 自身が1フレームの例外を既に飲み込む——要件 10.2——ため、ここで
捕捉するのは「その方式の実行全体が構造的に成立しない」というより重い失敗
——設定不正・入力元が開けない・`TrackingPipeline` の構築自体の失敗など
——である）。`TrackingConfigError` 等の「呼び出し方・設定の誤り」区分も
ここでは**あえて伝播させず**捕捉する: `TrackingPipeline.process()` の
Postconditions（`pipeline.py` docstring）はこれらを「1フレームの処理」の
外側で伝播する例外として位置づけているが、本モジュールの独立性契約
（「失敗した方式があっても他方式の結果を捨てない」）は方式単位でのより
広い耐性を要求しており、`pipeline.py` の1フレーム粒度の方針とは異なる、
本モジュール固有の境界判断である。ただし「後段設定が全方式で同一である
こと」の検証（`postprocess_fingerprint` 不一致）は、どの方式の実行も
始める前に行う事前検証であり、この try/except の対象外——検証失敗は
即座に伝播する。

Requirements: 4.4, 4.5, 4.6, 4.7, 4.8, 4.9, 8.4, 8.10, 11.5

依存方向（design.md「Dependency Direction」表: `Pipeline --> Bench`）:
0〜7層（`errors` / `types` / `config` / `metrics` / `detection/*` /
`projection` / `tracking` / `pipeline`）＋ `sensing_foundation`（公開入口の
`FrameSource` / `Logger` / `SourceKind`）を import してよい。`cli`（L9）は
import しない。
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from types import MappingProxyType

from sensing_foundation import FrameSource, SourceKind

from flying_object_tracking.config import TrackingSettings
from flying_object_tracking.errors import TrackingConfigError
from flying_object_tracking.metrics import EffectiveWindow
from flying_object_tracking.pipeline import TrackingPipeline
from flying_object_tracking.types import DetectorKind

__all__ = [
    "DetectorRunResult",
    "DetectorDecision",
    "DetectorComparisonResult",
    "DECISION_CRITERION",
    "decide",
    "parameter_count",
    "compare_detectors",
    "write_comparison_result",
]


# ==============================================================================
# 判定規則の説明文（実測前に確定させる。DetectorDecision.criterion に常に
# そのまま格納する定数——「実測前に確定させ、説明文を結果と同じファイルに
# 含める」というタスク本文の要求そのもの）
# ==============================================================================

DECISION_CRITERION: str = (
    "1) effective_points_per_window が最大の方式を第一候補とする。 "
    "2) 方式間の差が比較対象2方式の detect_ms_iqr の大きい方以内なら "
    "区別がついていないとみなし、detect_ms_p95 が小さい方を採る。 "
    "3) なお並ぶ場合は、設定パラメータ数（parameter_count()。3方式共通の "
    "後段パラメータ数に、その方式固有のパラメータ数を加えた値）が少ない方を採る。 "
    "4) 絶対値の目標は置かない。すべて方式間の相対比較とばらつきで定義する。 "
    "5) 上記のいずれでも区別がつかない場合は selected=None とし、決めきれない "
    "ことを正常な結果として扱う（無理に1つ選ばない）。"
)


# ==============================================================================
# データモデル（design.md「DetectorComparison」節の契約そのもの。
# DetectorComparisonResult.runs / failures のみ、モジュール docstring
# 「独立実行と失敗時の扱い」に記載の理由で design.md の記載から拡張する）
# ==============================================================================


@dataclass(frozen=True, slots=True)
class DetectorRunResult:
    """1方式・1セッションの実行結果（design.md 記載の契約そのもの。要件 4.4〜4.7）。"""

    detector_kind: str
    session_id: str
    source: SourceKind
    frames_processed: int
    frames_without_candidate: int
    """取りこぼし（要件 4.6）。"""
    points_appended: int
    point_failures: int
    effective_points_per_window: float
    """第一指標（要件 4.5）。"""
    effective_points_peak: int
    detect_ms_p50: float
    detect_ms_p95: float
    detect_ms_iqr: float
    """判定規則で使う（要件 4.7）。"""
    track_ms_p50: float
    track_ms_p95: float
    tracks_started: int
    postprocess_fingerprint: str
    """後段設定の同一性（要件 4.4）。"""


@dataclass(frozen=True, slots=True)
class DetectorDecision:
    """判定規則の適用結果（design.md 記載の契約そのもの。要件 4.8, 4.9, 11.5）。"""

    criterion: str
    """判定規則の説明文（実測前に確定して記録する）。常に `DECISION_CRITERION`。"""
    selected: str | None
    """決めきれない場合は `None`（要件 4.9）。"""
    rationale: str
    provisional: bool
    """実機以外の入力しか無い場合 `True`（要件 11.5 / 4.9）。"""


@dataclass(frozen=True, slots=True)
class DetectorComparisonResult:
    """`compare_detectors()` の戻り値（design.md 記載の契約 + 拡張）。

    `runs` / `failures` の型が design.md の記載（`runs: tuple[
    DetectorRunResult, ...]`）から拡張されている理由はモジュール docstring
    「独立実行と失敗時の扱い」を参照。
    """

    runs: tuple[DetectorRunResult | None, ...]
    """実行順序どおり。失敗した方式の位置は `None`。"""
    failures: Mapping[str, str]
    """失敗した方式（`detector_kind` 文字列）→ 失敗理由（例外の型名とメッセージ）。"""
    decision: DetectorDecision
    note: str
    """simulated / recorded の別と、扱い上の注意（要件 11.5）。"""


# ==============================================================================
# パラメータ数の定義（ルール3。モジュール docstring「パラメータ数の定義」参照）
# ==============================================================================

_COMMON_PARAM_FIELDS: tuple[str, ...] = ("open_kernel_px", "close_kernel_px", "max_candidates")
"""3方式共通で使う `DetectorConfig` フィールド（`DefaultDetector.detect()` の
共有後段——形態処理・候補抽出——が使う。`detection/detector.py` 参照）。"""

_KIND_SPECIFIC_PARAM_FIELDS: Mapping[str, tuple[str, ...]] = {
    str(DetectorKind.DEPTH_BAND): (),
    str(DetectorKind.FRAME_DIFF): ("diff_threshold_mm",),
    str(DetectorKind.BACKGROUND): (
        "diff_threshold_mm",
        "background_frames",
        "background_update_rate",
    ),
}
"""方式固有の `DetectorConfig` フィールド。各 `detection/masks/*.py` の
コンストラクタが実際に受け取る引数を出典として確定した対応関係
（モジュール docstring「パラメータ数の定義」参照）。"""


def parameter_count(detector_kind: str) -> int:
    """`detector_kind` の方式が実際に使う `DetectorConfig` フィールド数を返す。

    判定規則ルール3「設定パラメータが少ない方を採る」の「パラメータ数」の
    定義そのもの。定義の根拠はモジュール docstring「パラメータ数の定義」を
    参照。

    Args:
        detector_kind: `DetectorRunResult.detector_kind`（`str(DetectorKind
            メンバー)` と同じ文字列）。

    Returns:
        共通パラメータ数（3）に、その方式固有のパラメータ数を加えた値。
        depth_band=3, frame_diff=4, background=6。

    Raises:
        KeyError: 未知の `detector_kind`（`DetectorKind` のいずれの値でも
            ない文字列）を渡した場合。
    """
    specific = _KIND_SPECIFIC_PARAM_FIELDS[detector_kind]
    return len(_COMMON_PARAM_FIELDS) + len(specific)


# ==============================================================================
# 分位点・四分位範囲（純粋な数値計算。sensing_foundation.logsummary の線形
# 補間方式——numpy 既定の 'linear' / Excel PERCENTILE.INC と同じ——に揃える。
# 上流は Q1/Q3 を公開していないため、同じ補間式を本モジュールで独立に適用する）
# ==============================================================================


def _percentile(sorted_values: Sequence[float], fraction: float) -> float:
    """昇順ソート済み数値列から線形補間で分位点を求める。空列は 0.0。"""
    n = len(sorted_values)
    if n == 0:
        return 0.0
    if n == 1:
        return sorted_values[0]
    k = (n - 1) * fraction
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_values[int(k)]
    return sorted_values[f] * (c - k) + sorted_values[c] * (k - f)


def _iqr(values: Sequence[float]) -> float:
    """四分位範囲（Q3 - Q1）。空列は 0.0。"""
    if not values:
        return 0.0
    sorted_values = sorted(values)
    q1 = _percentile(sorted_values, 0.25)
    q3 = _percentile(sorted_values, 0.75)
    return q3 - q1


# ==============================================================================
# 判定（純粋関数。実測を経由しない——モジュール docstring「責務2: 判定」参照）
# ==============================================================================


def decide(runs: Sequence[DetectorRunResult]) -> DetectorDecision:
    """判定規則（`DECISION_CRITERION`）を `runs` へ適用する純粋関数。

    実測（`compare_detectors()`）から独立しており、手で組み立てた
    `DetectorRunResult` を直接与えて判定規則だけを単体でテストできる。
    `runs` は既に失敗を除いた（`None` を含まない）実行結果の列を渡すこと
    （`compare_detectors()` はこの契約を守って呼び出す）。

    Args:
        runs: 判定対象の実行結果。空でもよい（要件 4.9: 決めきれない場合を
            正常な結果として扱う。全方式が失敗した場合の呼び出し元
            ——`compare_detectors()`——もこの関数を空列で呼ぶ）。

    Returns:
        `DetectorDecision`。`provisional` は `runs` 内のいずれかの
        `source` が `SourceKind.LIVE` でない場合（`runs` が空の場合を
        含む）に `True` になる（要件 11.5: 実機なしで得られた結果を実機の
        結論として扱わない）。
    """
    provisional = (not runs) or any(r.source != SourceKind.LIVE for r in runs)

    if not runs:
        return DetectorDecision(
            criterion=DECISION_CRITERION,
            selected=None,
            rationale="実行結果が1件も無いため決定できない（全方式が失敗した、または入力が無かった）。",
            provisional=provisional,
        )

    # ルール1: effective_points_per_window が最大の方式を第一候補とする。
    leader_value = max(r.effective_points_per_window for r in runs)
    # 複数方式が厳密に同値で最大の場合も想定し、先頭の1つを基準点として使う
    # （どれを基準にしても、同値の他方式は diff=0 <= threshold で必ず
    # contenders に含まれるため、基準点の選び方自体は結果に影響しない）。
    leader = next(r for r in runs if r.effective_points_per_window == leader_value)

    def _indistinguishable_from_leader(run: DetectorRunResult) -> bool:
        if run is leader:
            return True
        diff = abs(run.effective_points_per_window - leader.effective_points_per_window)
        threshold = max(leader.detect_ms_iqr, run.detect_ms_iqr)
        return diff <= threshold

    contenders = [r for r in runs if _indistinguishable_from_leader(r)]

    if len(contenders) == 1:
        winner = contenders[0]
        return DetectorDecision(
            criterion=DECISION_CRITERION,
            selected=winner.detector_kind,
            rationale=(
                f"{winner.detector_kind} は effective_points_per_window="
                f"{winner.effective_points_per_window:.6g} で最大であり、他方式との差は"
                "いずれも比較対象2方式の detect_ms_iqr の大きい方を超える"
                "（区別がついている。ルール1で決定）。"
            ),
            provisional=provisional,
        )

    # ルール2: 区別がつかない候補間で detect_ms_p95 が小さい方を採る。
    min_p95 = min(r.detect_ms_p95 for r in contenders)
    p95_contenders = [r for r in contenders if r.detect_ms_p95 == min_p95]
    tied_kinds_rule1 = sorted(r.detector_kind for r in contenders)

    if len(p95_contenders) == 1:
        winner = p95_contenders[0]
        return DetectorDecision(
            criterion=DECISION_CRITERION,
            selected=winner.detector_kind,
            rationale=(
                f"effective_points_per_window では {tied_kinds_rule1} の区別がつかず"
                f"（各方式の detect_ms_iqr 以内）、detect_ms_p95 が最小の "
                f"{winner.detector_kind}（{winner.detect_ms_p95:.6g}ms）を採用した"
                "（ルール2で決定）。"
            ),
            provisional=provisional,
        )

    # ルール3: なお並ぶ場合は設定パラメータが少ない方を採る。
    param_counts = {r.detector_kind: parameter_count(r.detector_kind) for r in p95_contenders}
    min_params = min(param_counts.values())
    param_contenders = [r for r in p95_contenders if param_counts[r.detector_kind] == min_params]
    tied_kinds_rule2 = sorted(r.detector_kind for r in p95_contenders)

    if len(param_contenders) == 1:
        winner = param_contenders[0]
        return DetectorDecision(
            criterion=DECISION_CRITERION,
            selected=winner.detector_kind,
            rationale=(
                f"effective_points_per_window・detect_ms_p95 のいずれでも "
                f"{tied_kinds_rule2} の区別がつかず、設定パラメータ数が最小の "
                f"{winner.detector_kind}（{min_params}個）を採用した（ルール3で決定）。"
            ),
            provisional=provisional,
        )

    # ルール5: それでも並ぶ場合は決めない（正常な結果として扱う。要件 4.9）。
    tied_kinds_rule3 = sorted(r.detector_kind for r in param_contenders)
    return DetectorDecision(
        criterion=DECISION_CRITERION,
        selected=None,
        rationale=(
            f"{tied_kinds_rule3} は effective_points_per_window・detect_ms_p95・"
            "設定パラメータ数のいずれでも区別がつかなかった。無理に1つを選ばず、"
            "決めきれない結果として扱う（要件 4.9）。"
        ),
        provisional=provisional,
    )


# ==============================================================================
# 実行（`compare_detectors()`。モジュール docstring「責務1: 実行」参照）
# ==============================================================================


class _CapturingLogger:
    """`Logger` プロトコルを満たす、送出イベントをメモリに保持するだけの内部シンク。

    `bench/compare.py` の外へは公開しない（`__all__` に含めない）。使う理由は
    モジュール docstring「実行時の計測経路についての設計判断」を参照。

    `TrackingMetrics` / `TrackingPipeline` が実際に呼ぶのは `enabled`
    （属性）と `emit()` のみ（`stage()` / `timed()` / `stats()` / `close()`
    はどちらも呼ばない——`Logger` は `Protocol` であり `TrackingPipeline` /
    `TrackingMetrics` の実装コードを確認して確定した事実）。未使用のメソッド
    は `NotImplementedError` にして、想定外の呼び出しがあれば即座に判明する
    ようにする（`close()` だけは実害の無い no-op が自然なため例外的に許す）。
    """

    def __init__(self) -> None:
        self.enabled = True
        self._events: list[tuple[str, str, Mapping[str, object]]] = []

    def emit(self, stage: str, event: str, /, **data: object) -> None:
        self._events.append((stage, event, data))

    def stage(self, stage: str) -> object:
        raise NotImplementedError(
            "_CapturingLogger.stage() は DetectorComparison の計測経路では使用しない"
        )

    def timed(self, stage: str, event: str, /, **data: object) -> AbstractContextManager[None]:
        raise NotImplementedError(
            "_CapturingLogger.timed() は DetectorComparison の計測経路では使用しない"
        )

    def stats(self) -> object:
        raise NotImplementedError(
            "_CapturingLogger.stats() は DetectorComparison の計測経路では使用しない"
        )

    def close(self, timeout_ms: float = 1000.0) -> None:
        return None

    def numeric_values(self, stage: str, field_name: str) -> list[float]:
        """`stage` の `"frame"` イベントから `field_name` の数値を集めて返す。"""
        return [
            float(data[field_name])  # type: ignore[arg-type]
            for (s, e, data) in self._events
            if s == stage and e == "frame" and field_name in data
        ]


_PROVISIONAL_NOTE: str = (
    "入力元に SourceKind.LIVE（実機）以外が含まれるため、本結果を実機の結論として"
    "扱わない。暫定的な比較結果である（要件 11.5）。"
)
_NON_PROVISIONAL_NOTE: str = "入力元はすべて SourceKind.LIVE（実機）由来である。"


def _run_single_detector(
    kind: DetectorKind,
    settings: TrackingSettings,
    open_source: Callable[[], FrameSource],
    session_id: str,
) -> DetectorRunResult:
    """1方式を1セッションぶん実行し、`DetectorRunResult` を組み立てる。"""
    # 計測を強制的に有効化する（モジュール docstring「実行時の計測経路に
    # ついての設計判断」参照）。postprocess_fingerprint の対象フィールド
    # （roi/object_model/projection/tracker/measurement）のうち measurement
    # だけがこの複製で変わり得るが、比較の事前検証（fingerprint 一致確認）
    # は呼び出し元が元の settings 群に対して既に行っている前提であるため、
    # ここでの上書きはその検証結果を無効化しない（全方式に同一に適用する）。
    run_settings = replace(settings, measurement=replace(settings.measurement, enabled=True))

    logger = _CapturingLogger()
    pipeline = TrackingPipeline(run_settings, logger)
    window = EffectiveWindow(run_settings.measurement.window_ms)

    source = open_source()
    with source:
        for frame in source.frames():
            update = pipeline.process(frame)
            if update.appended is not None:
                window.add(update.appended.point.t_ms)

    track = pipeline.finish()
    counters = pipeline.counters()

    detect_values = sorted(
        logger.numeric_values(run_settings.measurement.detect_stage, "detect_ms")
    )
    track_values = sorted(logger.numeric_values(run_settings.measurement.track_stage, "track_ms"))

    return DetectorRunResult(
        detector_kind=str(kind),
        session_id=session_id,
        source=track.source,
        frames_processed=counters.frames_processed,
        frames_without_candidate=counters.frames_without_candidate,
        points_appended=counters.points_appended,
        point_failures=counters.point_failures,
        effective_points_per_window=window.value(),
        effective_points_peak=window.peak(),
        detect_ms_p50=_percentile(detect_values, 0.5),
        detect_ms_p95=_percentile(detect_values, 0.95),
        detect_ms_iqr=_iqr(detect_values),
        track_ms_p50=_percentile(track_values, 0.5),
        track_ms_p95=_percentile(track_values, 0.95),
        tracks_started=counters.tracks_started,
        postprocess_fingerprint=run_settings.postprocess_fingerprint(),
    )


def compare_detectors(
    session_id: str,
    open_source: Callable[[], FrameSource],
    settings_by_kind: Mapping[DetectorKind, TrackingSettings],
) -> DetectorComparisonResult:
    """同一セッションに対して複数方式を独立に実行し、比較結果を組み立てる。

    design.md「DetectorComparison / Batch Job Contract」（タスク 7.1）。
    「1つ以上の記録済みセッション」のうち、本関数は**1セッション**を対象と
    する（複数セッションにわたる集約・警告付与は、design.md Risks が
    「複数セッションでの実行を既定の運用とする」と述べる CLI 側
    ——タスク 7.3——の責務であり、本タスクの `DetectorComparisonResult`
    契約自体が `session_id: str`単数のフィールドしか持たないことからも
    範囲外と判断した）。

    Args:
        session_id: 対象セッションの識別子。各 `DetectorRunResult.
            session_id` にそのまま使う。
        open_source: 呼び出すたびに、同一セッションを最初から辿る**新規**
            `FrameSource` を返すファクトリ。方式ごとに1回ずつ（`len(
            settings_by_kind)` 回）呼ばれる。同一の `FrameSource` インスタ
            ンスを使い回すことは呼び出し側の責任では想定していない
            （`track_source` 相当の消費を各方式が独立に行うため）。
        settings_by_kind: 検出方式 → その方式用の `TrackingSettings`。
            **後段設定（`roi` / `object_model` / `projection` / `tracker`
            / `measurement`）は全方式で同一でなければならない**
            （`postprocess_fingerprint()` の一致で検証する。要件 4.4）。

    Returns:
        `DetectorComparisonResult`。`runs` は `settings_by_kind` の反復順に
        対応する（失敗した方式は `None`）。

    Raises:
        TrackingConfigError: `settings_by_kind` が空、いずれかの `settings.
            detector.kind` がマッピングのキーと一致しない、または後段設定
            の `postprocess_fingerprint` が方式間で一致しない場合。**どの
            方式の実行も始める前に**検証する（要件 4.4「実行前に検証し、
            不一致なら拒否する」）。
    """
    if not settings_by_kind:
        raise TrackingConfigError("settings_by_kind は空であってはならない（比較対象が無い）")

    for kind, settings in settings_by_kind.items():
        if settings.detector.kind != kind:
            raise TrackingConfigError(
                "settings_by_kind のキーと settings.detector.kind が食い違っている: "
                f"key={kind!r}, settings.detector.kind={settings.detector.kind!r}"
            )

    fingerprints = {
        kind: settings.postprocess_fingerprint() for kind, settings in settings_by_kind.items()
    }
    distinct_fingerprints = set(fingerprints.values())
    if len(distinct_fingerprints) > 1:
        raise TrackingConfigError(
            "後段設定（roi/object_model/projection/tracker/measurement）が方式間で"
            f"一致しない（postprocess_fingerprint 不一致）: {fingerprints}"
        )

    runs: list[DetectorRunResult | None] = []
    failures: dict[str, str] = {}

    for kind, settings in settings_by_kind.items():
        try:
            run = _run_single_detector(kind, settings, open_source, session_id)
        except Exception as exc:  # noqa: BLE001 - 意図的な広い捕捉。
            # 各方式の実行は独立とし、失敗した方式があっても他方式の結果を
            # 捨てない（モジュール docstring「独立実行と失敗時の扱い」参照）。
            failures[str(kind)] = f"{type(exc).__name__}: {exc}"
            runs.append(None)
        else:
            runs.append(run)

    successful_runs = [r for r in runs if r is not None]
    decision = decide(successful_runs)

    note_parts = [_PROVISIONAL_NOTE if decision.provisional else _NON_PROVISIONAL_NOTE]
    if failures:
        failed_kinds = sorted(failures)
        note_parts.append(
            f"失敗した方式: {failed_kinds}。他方式の結果は破棄せず保持している"
            "（failures フィールドに理由を記録）。"
        )
    note = " ".join(note_parts)

    return DetectorComparisonResult(
        runs=tuple(runs),
        failures=MappingProxyType(failures),
        decision=decision,
        note=note,
    )


# ==============================================================================
# 書き出し（責務3。measurements.md への記録はここでは行わない——モジュール
# docstring 冒頭参照）
# ==============================================================================


def comparison_output_path(output_root: Path, session_id: str) -> Path:
    """`DetectorComparisonResult` の書き出し先パスを返す（design.md「比較結果」節）。"""
    return output_root / f"compare-{session_id}.json"


def write_comparison_result(
    result: DetectorComparisonResult, output_root: Path, session_id: str
) -> Path:
    """`result` を `var/tracking/compare-<session_id>.json` へ JSON 化する。

    design.md「比較結果」節: 「`DetectorComparisonResult` をそのまま JSON
    化する」「判定規則の説明文（`decision.criterion`）を結果と同じファイル
    に含める」。`decision` は `DetectorComparisonResult` のフィールドである
    ため、この2つは常に同じファイルに書かれる（別ファイルへ分離する余地が
    構造的に無い）。

    `measurements.md`（`.kiro/specs/flying-object-tracking/measurements.md`）
    への記録は行わない。そのファイルは実機データが揃う後続タスク（9.x）が
    新規作成する責務であり、本タスクの時点では存在せず、未実測の数値を
    埋めるべきでもない（`tech.md` 開発標準1）。

    Args:
        result: 書き出す比較結果。
        output_root: 出力先ルート（`TrackingSettings.output_root` を渡す
            想定。design.md「Batch/Job Contract」）。存在しなければ作成する。
        session_id: `comparison_output_path()` に渡すファイル名の一部。

    Returns:
        書き出したファイルのパス。
    """
    output_root.mkdir(parents=True, exist_ok=True)
    path = comparison_output_path(output_root, session_id)
    # `asdict(result)` を丸ごと使わない: `result.failures` は
    # `MappingProxyType` であり（`compare_detectors()` 参照）、
    # `dataclasses.asdict()` は非 dict のマッピング型を `copy.deepcopy()`
    # へ回すため `TypeError: cannot pickle 'mappingproxy' object` になる
    # （dict ではなく Mapping プロトコルだけを満たす型だと再帰対象と
    # 認識されない）。フィールドごとに組み立てることでこれを避ける。
    payload = {
        "runs": [None if r is None else asdict(r) for r in result.runs],
        "failures": dict(result.failures),
        "decision": asdict(result.decision),
        "note": result.note,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return path
