"""計測 ON / OFF の比較を実装する（design.md「L8-L9: 比較・集計・入口 / LoggingOverheadBench」）。

`tech.md` 開発標準5「計測が計測対象を歪めないこと」を実測で確認する、本 Spec で
**唯一の** 合否判定を行うコンポーネントである（要件 10.1-10.5, 5.6）。他の
ベンチ（`bench/modes.py`、タスク 7.2）が「目標値の充足判定を行わない」のとは
対照的に、本モジュールは判定を**行う**——ただしその判定は「Pi 4 は十分速いか」
ではなく「**計測を有効にすることで処理時間が計測できないほど歪むか**」という、
計測の妥当性そのものについての判定である（モジュール docstring 冒頭でこの
違いを明記する。task brief 参照）。

============================================================================
3条件と、比較の構造（実装判断・design.md との差分の明記）
============================================================================

tasks.md 7.3 は「ログ無効・ログ有効・記録有効の各条件を...交互に実行する」と
書く。design.md の `OverheadResult.condition` は
`Literal["logging_off", "logging_on", "recording_on"]` の3値を定めるが、
`OverheadVerdict` は**単一の**（`ON` 条件・`OFF` 条件という単数形の）frozen
dataclass であり、3条件のうちどの2つを比較して1つの `OverheadVerdict` を
作るのかを明示しない。本モジュールはこれを次のように解決する:

- **`OverheadVerdict` 自体には比較対象を識別するフィールドを追加しない**
  （design.md の擬似コード5フィールドをそのまま保つ——task brief の指示
  「individual verdict is the single-dataclass shape design.md shows」に従う）。
- 代わりに、`OFF` 条件（`logging_off`）と `ON` 条件の1組を受け取って
  `OverheadVerdict` を1つ返す純粋関数 `_compute_verdict(on, off)` を用意し、
  `LoggingOverheadBench.run()` がこれを **2回** 呼ぶ:
    1. `logging_on` vs `logging_off`（ロギングそのものの追加負荷）
    2. `recording_on` vs `logging_off`（記録の追加負荷。要件 5.6）
  返る2つの `OverheadVerdict` は `LoggingOverheadReport.verdicts`
  （`Mapping[str, OverheadVerdict]`、キー
  `"logging_on_vs_logging_off"` / `"recording_on_vs_logging_off"`）に収める。
  どちらの比較も**同一の** `logging_off` 条件をベースラインにする——3条件を
  総当たりで比較するのではなく、「何も計測しない状態」という1つの基準点に
  対して、ロギングと記録それぞれの追加負荷を独立に問う設計である。
- `recording_on` 条件は**ロギングを無効**（`NullLogger`）にして実行する
  （設計判断。「記録の追加負荷」だけを測るため、ロギングの追加負荷と
  混ぜない——単一の要因を1回の比較につき1つだけ変える、という実験計画の
  原則をそのまま適用したもの）。`SessionRecorder.write()` は自身の内部で
  `logger.timed("record", "write", ...)` を呼ぶが（`recording/writer.py`
  参照）、`NullLogger` を渡すことでこの内部呼び出し自体は真の no-op になる
  ——それでも `write()` が行う実際のファイル I/O（open済みの blob/index
  ファイルへの追記と `flush()`）は本物であり、その所要時間は下記の「本モジュール
  独自の `total_ms`」測定に確実に現れる。

============================================================================
本モジュール独自の `total_ms` 測定（design.md との差分・重要な実装判断）
============================================================================

`metrics.py`（タスク 2.2）の `CaptureMetrics.frame()` は、`BaseFrameSource`
（タスク 3.1）が**既に測り終えた** `FrameTiming`（`wait_ms`/`drain_ms`/
`handoff_ms`/`total_ms`）を受け取ってからログへ送出する。すなわち
`FrameTiming.total_ms` の測定区間は `logger.emit()` 呼び出しそのものより
**前に確定しており**、`emit()` 自体のコスト（キュー投入・オブジェクト構築）は
`FrameTiming.total_ms` に一切含まれない（`source.py` の取得ループ、
`t_handoff_end = clock.now_ms(); ...; metrics.frame(timing, frame)` の順序を
参照——`timing` は `metrics.frame()` を呼ぶ前に組み立て終わっている）。

したがって、本モジュールが `FrameTiming.total_ms`（＝ `ModeSweep` が
`total_ms_p50`/`total_ms_p95` として使っているのと同じ値）をそのまま流用すると、
**測ろうとしている当のオーバーヘッド（`logger.emit()` のコスト）に構造的に
盲目になる**——ロギングを ON にしても OFF にしても `FrameTiming.total_ms` の
値自体は変わらないはずであり、それでは「有意に変化しない」という判定が常に
自明に真になってしまい、判定として無意味になる。

本モジュールはこの問題を避けるため、**自前で外側から** 1フレームぶんの
所要時間を測る: 3条件で共有する1本の `FrameSource.frames()` イテレータに対して
`next(iterator)` を呼ぶ直前・直後の `SessionClock.now_ms()` の差を
`total_ms` として採用し（`recording_on` 条件では、`next()` で得たフレームを
`SessionRecorder.write()` へ渡す処理も同じ計測区間に含める）、この値を
`OverheadResult.total_ms_p50`/`total_ms_p95`/`total_ms_iqr` の元データとする。
これにより `total_ms` は「1枚取得する（＋記録有効なら書き込む）」という
**利用者から見える1サイクル全体**の所要時間になり、`logger.emit()` の
キュー投入コストや `SessionRecorder.write()` の実ファイル I/O コストが
確実に測定対象へ含まれる。`OverheadResult.total_ms_p50` は
`ModeSweep.ModeResult.total_ms_p50`（`FrameTiming.total_ms` 由来）とは
**測定区間の定義が異なる**別の値であることに注意——フィールド名が同じでも
出典が異なる。

============================================================================
生サンプルの置き場（design.md との差分の明記）
============================================================================

design.md の `OverheadResult` 擬似コードは集計値（`p50`/`p95`/`iqr`/
`measured_fps`/`frames_dropped`）のみを持ち、生サンプルの列を持たない。
一方 Batch/Job Contract は「各条件の生サンプルを残し、判定を後から再計算
できるようにする」ことを求める（要件 10.2 の裏付けとしても必要）。本モジュールは
`OverheadResult` 自体を集計専用のまま**変更せず**（design.md の擬似コード通り
`frozen=True, slots=True` の5+2フィールド）、生サンプルは1段上の
`LoggingOverheadReport.raw_samples`（`Mapping[str, tuple[float, ...]]`、
条件名をキーにした `total_ms` の生の列）として別に持たせる
（task brief の推奨する解決と同じ形。`ModeSweep` の `valid`/`invalid_reason`
のような `OverheadResult` 自体への加算的拡張ではなく、コンテナ側に置くことで
「集計専用の型」という design.md の意図を保つ）。

============================================================================
A/B/A/B 交互実行（3条件版への一般化）
============================================================================

要件 10.1 の「同一条件で比較する」ことと、Batch/Job Contract の
「順序効果を打ち消すため A/B/A/B で回す」ことを、3条件へ素直に一般化し、
`(logging_off, logging_on, recording_on)` という固定順の1巡を「セグメント」
とし、これを `cycles` 回繰り返す（`logging_off, logging_on, recording_on,
logging_off, logging_on, recording_on, ...`）。1セグメントの長さは
`segment_s` 秒（各条件で共通、`__init__` 引数）であり、3条件を合計した
`cycles * segment_s` 秒がそれぞれの条件の総稼働時間になる（同一時間で
比較する、という要件 10.1 の「同一時間」を満たす）。

実際に交互実行が起きたことをテスト側が確認できるよう、実行したセグメントの
条件名の列を `LoggingOverheadReport.segment_order`（例:
`("logging_off","logging_on","recording_on") * cycles`）としてそのまま
記録する。タイミングに依存する脆いテストを書かずに「単純な逐次実行
（OFF を全部やってから ON を全部やる）ではなく、本当に交互に実行された」
ことを構造的に固定できる。

============================================================================
3条件は1本の入力元を共有する（タスク 7.4。design.md「3条件が入力元を共有する理由」）
============================================================================

`run()` は `FrameSource`・`SessionClock`・`CaptureMetrics` を**1つずつだけ**
構築し、3条件で共有する。`open_source()` の呼び出しは1回である。条件の
切り替えでセグメント境界ごとに変えるのは**ロガーの向き先だけ**であり
（`logging_off` → `NullLogger`、`logging_on` → `StructuredLogger`、
`recording_on` → `NullLogger` ＋ `SessionRecorder.write()`）、その差し替えは
`obslog.Logger` が `Protocol`（構造的部分型）であることを利用した本モジュール
私有の `_RoutingLogger` が担う。`metrics.py` も `source.py` も変更しない。

**なぜ条件ごとに開かないか**（design.md の根拠1〜3の要約）:

1. RealSense は1デバイスにつき1パイプラインしか開けない。3本を同時に開く
   構造は live で2本目のオープンが失敗する（タスク9.5 で実測）。
2. 毎セグメントで開き直す案は、パイプラインのウォームアップ（タスク9.4 の
   実測: 30fps 要求時にウォームアップ無しで 17〜20fps、2秒後で 30.08fps）に
   よって**全セグメントがウォームアップ区間**になり、測っているものが
   ロギング負荷ではなく起動の過渡応答になる。
3. 交互実行を諦める案は Batch/Job Contract の A/B/A/B を放棄することになり、
   順序効果と熱ドリフトを打ち消せなくなる。

**帰結（許容する副作用）**: 3条件は1本の列の**互いに素な区間**を処理する。
`simulated` / `recorded` であっても3条件が同一内容を見ることはもう無い
（`recording_on` が書き出すセッション記録の `index` には、他条件が引いた
ぶんの欠番が空く）。これは A/B/A/B の交互実行と複数サイクルが打ち消すべき
非定常性であり、交互実行が存在する理由そのものである。

**帰結（供給が尽きたときの挙動の変化）**: 入力を共有するため、`StopIteration`
は3条件同時に効く。旧構造では条件ごとに別々の供給を持っていたので「1条件
だけ尽きる」ことがあり得たが、共有した1本を分け合う以上それは起こらない。

**条件別の `frames_dropped`**: `CaptureMetrics` が1つしか無いため、
`counters().frames_dropped` の累計をそのまま読むと3条件とも同じ総数になる。
**セグメント境界の前後で読んだ差分**を条件ごとに積算する
（`_run_segment()`）。

============================================================================
実機以外での実行と、非実機の断り書き（要件 10.5）
============================================================================

`settings.source != SourceKind.LIVE` のとき、`LoggingOverheadReport.
hardware_disclaimer` に「この結果を実機の結論として扱わない」旨の文字列を
入れる（`SourceKind.LIVE` のときは `None`）。`design.md` Implementation Notes
「Risks: simulated の結果を実機の結論として扱わない旨を出力に明記する」を
そのまま実装する。

============================================================================
判定基準（要件 10.3。実測前に確定させ、結果とともに記録する）
============================================================================

判定基準の文字列 `_CRITERION_TEXT` はモジュールレベルの定数として固定し、
実測値から動的に組み立てない（「実測前に確定している」ことを、文字列が
実行時のデータに一切依存しないという形でコードレベルに落とし込む）。

    ON 条件の total_ms 中央値と OFF 条件の中央値の差が、OFF 条件の
    四分位範囲（IQR）以内であり、かつ frames_dropped が増えていないとき
    「有意に変化しない」と判定する。

`median_delta_ms` は ON とOFF の中央値の**差の絶対値**として計算する
（ON が OFF より速くなるケース——症状としては「歪み」ではない——を誤って
不合格にしないため。design.md の文言「差」は符号を明示しないが、比較対象の
IQR が非負であることから絶対値の比較を意図していると解釈する）。
「以内」（"以内" = at-or-below）を文字通り実装し、`median_delta_ms ==
baseline_iqr_ms` の境界は**合格**とする（`<=` を使う。`<` ではない）。
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from sensing_foundation.config import RuntimeSettings
from sensing_foundation.errors import SensingConfigError
from sensing_foundation.metrics import CaptureMetrics
from sensing_foundation.obslog import (
    Logger,
    LoggerStats,
    NullLogger,
    StageLogger,
    get_logger,
)
from sensing_foundation.recording import layout
from sensing_foundation.recording.writer import SessionRecorder
from sensing_foundation.source import open_source
from sensing_foundation.timebase import SessionClock
from sensing_foundation.types import CaptureFrame, SourceKind

if TYPE_CHECKING:
    # `open_source()` と同じ流儀（`bench/modes.py` 参照）: 型注釈のためだけの
    # 参照であり、`TYPE_CHECKING` の外では実行時に import しない。
    from sensing_foundation.sources.recorded import ReplaySpeed
    from sensing_foundation.sources.simulated import FrameSupplier

__all__ = [
    "OverheadResult",
    "OverheadVerdict",
    "LoggingOverheadReport",
    "LoggingOverheadBench",
]

_CONDITIONS: tuple[str, str, str] = ("logging_off", "logging_on", "recording_on")

#: 判定基準の説明文（要件 10.3）。実測前に固定し、実行時のデータに一切依存
#: させない——動的に組み立てると「実測前に確定させる」という要件の趣旨に反する。
_CRITERION_TEXT = (
    "ON 条件の total_ms 中央値と OFF 条件の中央値の差が、OFF 条件の"
    "四分位範囲（IQR）以内であり、かつ frames_dropped が増えていないとき"
    "「有意に変化しない」と判定する。"
)

_HARDWARE_DISCLAIMER_TEMPLATE = (
    "この結果は入力元 {source!s} で得られたものであり、実機"
    "（RealSense on Raspberry Pi 4）の結論として扱わない（要件 10.5）。"
    "実機での再実行はタスク 9.5 で行う。"
)


@dataclass(frozen=True, slots=True)
class OverheadResult:
    """1条件分の集計結果（design.md「LoggingOverheadBench」の `OverheadResult`）。

    design.md の擬似コード通り、集計値のみを持つ（生サンプルは
    `LoggingOverheadReport.raw_samples` 側に置く。モジュール docstring
    「生サンプルの置き場」参照）。

    Attributes:
        condition: この結果がどの条件のものか。
        samples: `total_ms` の生サンプル数。
        total_ms_p50: `total_ms` の中央値（モジュール docstring「本モジュール
            独自の `total_ms` 測定」参照——`ModeSweep.ModeResult.total_ms_p50`
            とは測定区間の定義が異なる）。
        total_ms_p95: 同、95パーセンタイル。
        total_ms_iqr: 同、四分位範囲（p75 - p25）。
        measured_fps: この条件の稼働時間（アイドル区間を除く、各セグメントの
            実測経過時間の合計）に対する実測フレームレート。
        frames_dropped: この条件でのドレイン破棄件数の累計
            （`CaptureMetrics.counters().frames_dropped`）。simulated 入力は
            常にドレイン無効なので通常 0（`SimulatedSource` の Invariant）。
    """

    condition: Literal["logging_off", "logging_on", "recording_on"]
    samples: int
    total_ms_p50: float
    total_ms_p95: float
    total_ms_iqr: float
    measured_fps: float
    frames_dropped: int


@dataclass(frozen=True, slots=True)
class OverheadVerdict:
    """ON/OFF 1組の比較判定（design.md「LoggingOverheadBench」の `OverheadVerdict`）。

    design.md の擬似コード通り、比較対象の条件を識別するフィールドは持たない
    （どの2条件を比較したかは `LoggingOverheadReport.verdicts` のキーで
    識別する。モジュール docstring「3条件と、比較の構造」参照）。

    Attributes:
        criterion: 判定基準の説明文（`_CRITERION_TEXT` そのもの。実測前に
            確定済みで、実行時のデータに依存しない）。
        passed: 「有意に変化しない」と判定されたら `True`。
        median_delta_ms: ON/OFF の `total_ms_p50` の差の絶対値。
        baseline_iqr_ms: OFF 条件の `total_ms_iqr`（判定の基準）。
        detail: 判定の根拠と、`False` の場合は「計測結果を無条件に有効な
            ものとして扱わない」旨（要件 10.4）を明記した説明文。
    """

    criterion: str
    passed: bool
    median_delta_ms: float
    baseline_iqr_ms: float
    detail: str


@dataclass(frozen=True, slots=True)
class LoggingOverheadReport:
    """`LoggingOverheadBench.run()` の戻り値。3条件・2判定・生サンプルを束ねる。

    design.md には専用の戻り値型が示されていないため（Batch/Job Contract は
    出力先ファイルのみを規定する）、本モジュールが定義する。

    Attributes:
        results: 3条件分の `OverheadResult`。`("logging_off", "logging_on",
            "recording_on")` の順で固定。
        verdicts: `"logging_on_vs_logging_off"` / `"recording_on_vs_logging_off"`
            をキーとする2つの `OverheadVerdict`（モジュール docstring
            「3条件と、比較の構造」参照）。
        raw_samples: 条件名をキーにした `total_ms` の生サンプル列
            （収集順。判定を後から再計算できるようにする。要件 10.2）。
        segment_order: 実際に実行したセグメントの条件名の列
            （`("logging_off","logging_on","recording_on") * cycles` の
            はずである——交互実行の構造的な証跡）。
        hardware_disclaimer: `settings.source != SourceKind.LIVE` のときのみ
            非 `None`。実機の結論として扱わない旨の文字列（要件 10.5）。
    """

    results: tuple[OverheadResult, OverheadResult, OverheadResult]
    verdicts: Mapping[str, OverheadVerdict]
    raw_samples: Mapping[str, tuple[float, ...]]
    segment_order: tuple[str, ...]
    hardware_disclaimer: str | None


# ----------------------------------------------------------------------------
# 分位点計算（`logsummary.py` / `bench/modes.py` の `_percentile()` と同じ
# 規約: k=(n-1)*q の線形補間。両モジュールとも非公開のため、本モジュールも
# 重複実装する——3箇所とも同じ規約に揃えることを、この docstring とテストで
# 明示する（`bench/modes.py` のコメントが既に指摘する将来のリファクタ対象）。
# ----------------------------------------------------------------------------


def _percentile(sorted_values: Sequence[float], fraction: float) -> float:
    """昇順ソート済み数値列から、線形補間で分位点を求める。値が無ければ 0.0。"""
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


# ----------------------------------------------------------------------------
# 判定ロジック（純粋関数。合成した OverheadResult を直接渡してテストできる）
# ----------------------------------------------------------------------------


def _compute_verdict(on: OverheadResult, off: OverheadResult) -> OverheadVerdict:
    """`on` 条件と `off` 条件（ベースライン）の1組から `OverheadVerdict` を1つ作る。

    `LoggingOverheadBench.run()` はこの関数を2回呼ぶ（モジュール docstring
    「3条件と、比較の構造」参照）。純粋関数であり、`OverheadResult` を
    直接構築してこの関数だけを単体テストできる（`ModeSweep` の
    `effective_samples_per_window()` と同じ、フル実行を伴わない検証のしやすさ
    を意図した切り出し）。

    Args:
        on: 比較したい側（`logging_on` または `recording_on`）の結果。
        off: ベースライン（`logging_off`）の結果。

    Returns:
        `criterion` は常に `_CRITERION_TEXT`（固定・実測前に確定済み）。
        `passed` は `median_delta_ms <= baseline_iqr_ms` かつ
        `on.frames_dropped <= off.frames_dropped` のとき `True`
        （境界値 `median_delta_ms == baseline_iqr_ms` は合格。「以内」の
        文字通りの実装。モジュール docstring 参照）。
    """
    median_delta_ms = abs(on.total_ms_p50 - off.total_ms_p50)
    baseline_iqr_ms = off.total_ms_iqr
    dropped_not_increased = on.frames_dropped <= off.frames_dropped
    within_iqr = median_delta_ms <= baseline_iqr_ms
    passed = within_iqr and dropped_not_increased

    if passed:
        detail = (
            f"{on.condition!r} は {off.condition!r} 比較で有意な差が観測されなかった"
            f"（median_delta_ms={median_delta_ms:.4f} <= "
            f"baseline_iqr_ms={baseline_iqr_ms:.4f}、frames_dropped "
            f"{on.frames_dropped} <= {off.frames_dropped}）。"
        )
    else:
        detail = (
            f"{on.condition!r} は {off.condition!r} 比較で有意な差が観測された"
            f"（median_delta_ms={median_delta_ms:.4f}, "
            f"baseline_iqr_ms={baseline_iqr_ms:.4f}、frames_dropped "
            f"{on.frames_dropped} vs {off.frames_dropped}）。"
            "計測結果を無条件に有効なものとして扱わない（要件 10.4）。"
        )

    return OverheadVerdict(
        criterion=_CRITERION_TEXT,
        passed=passed,
        median_delta_ms=median_delta_ms,
        baseline_iqr_ms=baseline_iqr_ms,
        detail=detail,
    )


# ----------------------------------------------------------------------------
# 向き先を差し替えられる転送ロガー（タスク 7.4）
# ----------------------------------------------------------------------------


class _RoutingLogger:
    """`Logger` プロトコルを満たし、委譲先を実行中に差し替えられる転送ロガー。

    3条件が1つの `CaptureMetrics` を共有する構造（design.md「3条件が入力元を
    共有する理由」）を成立させるための、**本モジュール私有**のクラスである。
    `CaptureMetrics` は構築時にロガーを束ねてしまうため、条件の切り替えを
    「別の `CaptureMetrics` を作る」ことで表現すると計測点が3つに割れてしまう。
    `obslog.Logger` が `Protocol`（構造的部分型）であることを利用し、
    **転送先だけを差し替えられるロガー**を1つ握らせることで、計測点を1つに
    保ったまま条件を切り替える。`metrics.py` も `source.py` も変更しない。

    Preconditions: `route_to()` はセグメント境界でのみ呼ぶ（セグメントの
        途中で向き先が変わると、その条件の測定値に別条件のコストが混ざる）。
    Postconditions: `enabled` は常に現在の委譲先の `enabled` と一致する。
    Invariants: 自身は状態（キュー・スレッド・カウンタ）を一切持たない。
        `stats()` / `close()` も現在の委譲先へそのまま渡す。

    **測定上の注意**: 本クラスを挟むことで、`emit()` 1回につき Python の
    関数呼び出しと `**data` の再パックが1段ぶん増える。このコストは
    **3条件すべてに等しく乗る**ため、条件間の差（`median_delta_ms`）には
    現れない——`logging_off` を「素の `NullLogger`」ではなく「転送を1段
    挟んだ `NullLogger`」を基準点として測る、という意味である。
    """

    __slots__ = ("_target", "enabled")

    def __init__(self, target: Logger) -> None:
        self._target = target
        self.enabled = target.enabled

    def route_to(self, target: Logger) -> None:
        """委譲先を差し替える（セグメント境界でのみ呼ぶこと）。"""
        self._target = target
        self.enabled = target.enabled

    def emit(self, stage: str, event: str, /, **data: object) -> None:
        self._target.emit(stage, event, **data)

    def stage(self, stage: str) -> StageLogger:
        return self._target.stage(stage)

    def timed(
        self, stage: str, event: str, /, **data: object
    ) -> AbstractContextManager[None]:
        return self._target.timed(stage, event, **data)

    def stats(self) -> LoggerStats:
        return self._target.stats()

    def close(self, timeout_ms: float = 1000.0) -> None:
        self._target.close(timeout_ms)


# ----------------------------------------------------------------------------
# LoggingOverheadBench
# ----------------------------------------------------------------------------


class LoggingOverheadBench:
    """計測 ON/OFF の3条件を A/B/A/B（一般化して A/B/C/A/B/C/...）で交互実行する。

    Preconditions: `segment_s` は正、`cycles` は1以上。
    Postconditions: `run()` が返す `LoggingOverheadReport.results` は常に
        3件（`logging_off`/`logging_on`/`recording_on`）そろっている。
        `segment_order` の長さは `3 * cycles`。
    Invariants:
        - 判定基準文字列（`_CRITERION_TEXT`）は実測値に一切依存しない固定値
          である。
        - `run()` 1回につき `open_source()` の呼び出しは**1回だけ**であり、
          `FrameSource`・`SessionClock`・`CaptureMetrics` は3条件で共有される
          （モジュール docstring「3条件は1本の入力元を共有する」）。
    """

    def __init__(self, segment_s: float, cycles: int) -> None:
        """`LoggingOverheadBench` を組み立てる。

        Args:
            segment_s: 1セグメント（1条件を連続して走らせる時間）の秒数。
                3条件 × `cycles` 回ぶんのセグメントを合計すると、各条件の
                総稼働時間は `segment_s * cycles` 秒になる（同一時間での
                比較。要件 10.1）。
            cycles: `(logging_off, logging_on, recording_on)` の1巡を
                繰り返す回数。2以上でなければ「交互」にならないが、最小の
                動作確認用に1も許容する。

        Raises:
            SensingConfigError: `segment_s<=0` または `cycles<1`
                （呼び出し方の誤り）。
        """
        if segment_s <= 0:
            raise SensingConfigError(f"segment_s は正でなければならない: {segment_s}")
        if cycles < 1:
            raise SensingConfigError(f"cycles は1以上でなければならない: {cycles}")

        self._segment_s = segment_s
        self._cycles = cycles

    def run(
        self,
        settings: RuntimeSettings,
        *,
        supplier: "FrameSupplier",
        work_dir: Path,
        speed: "ReplaySpeed" = "fast",
    ) -> LoggingOverheadReport:
        """3条件を交互実行し、`LoggingOverheadReport` を返す。

        `settings.capture` は3条件で共通に使う——そもそも `open_source()` は
        1回しか呼ばれず、3条件は同じ `FrameSource` から引くため、取得設定が
        条件間で食い違う余地が構造的に無い。`settings.logging`/
        `settings.recording` は「ロギング/記録を有効にした条件」で使う
        `queue_capacity`/`compression` 等の**テンプレート**としてのみ使い、
        `enabled` と出力先（`path`/`root`）は本メソッドが `work_dir` を
        基準に上書きする。

        Args:
            settings: `capture`（3条件共通）と `logging`/`recording`
                （`queue_capacity`/`compression` 等のテンプレート）の供給元。
                `source` は `hardware_disclaimer` の判定にも使う。
            supplier: `settings.source == SourceKind.SIMULATED` のとき必須
                （`open_source()` と同じ意味論）。`SimulatedSource` は1つ
                しか作られないため、3条件は**この供給関数が返す1本の列**を
                前から順に分け合う。したがって供給は3条件ぶんの合計
                （おおむね `3 * cycles * segment_s` 秒ぶん）を賄える必要が
                ある——尽きた時点で3条件とも打ち切られる。
            work_dir: `logging_on` 用の NDJSON ログと `recording_on` 用の
                セッション記録を書き出す作業ディレクトリ
                （`work_dir/"logs"` と `work_dir/"sessions"` を使う）。
            speed: `settings.source == SourceKind.RECORDED` のときのみ意味を
                持つ再生速度。

        Returns:
            3条件の `OverheadResult`・2つの `OverheadVerdict`・条件別の
            生サンプル・実行したセグメント順・非実機の断り書きを束ねた
            `LoggingOverheadReport`。
        """
        work_dir = Path(work_dir)
        logs_dir = work_dir / "logs"
        sessions_root = work_dir / "sessions"

        # 3条件で1つずつ共有する（design.md「3条件が入力元を共有する理由」）。
        clock = SessionClock(session_id="bench-logging")
        null_logger: Logger = NullLogger()
        logging_cfg = replace(settings.logging, enabled=True, path=logs_dir)
        structured_logger: Logger = get_logger(logging_cfg, clock)
        # 取得ループが握るのは転送ロガー1本だけ。向き先の差し替えが条件の切り替えになる。
        router = _RoutingLogger(null_logger)
        metrics = CaptureMetrics(router, clock, sysstat=None)
        source = open_source(
            settings, metrics, clock=clock, supplier=supplier, speed=speed
        )

        # 条件ごとの向き先。`recording_on` は `NullLogger` のまま
        # `SessionRecorder.write()` を足す（記録単独の追加負荷を測るため。
        # モジュール docstring「3条件と、比較の構造」参照）。
        logger_for_condition: Mapping[str, Logger] = {
            "logging_off": null_logger,
            "logging_on": structured_logger,
            "recording_on": null_logger,
        }

        recording_cfg = replace(settings.recording, enabled=True, root=sessions_root)
        recorder = SessionRecorder(
            root=recording_cfg.root,
            session_id=layout.new_session_id(clock.started_wall_ms),
            profile=source.profile,
            device=None,
            runtime={"origin": "bench_logging_overhead"},
            compression=recording_cfg.compression,
            logger=NullLogger(),
            source=settings.source,
            capture=None,
        )

        raw_samples: dict[str, list[float]] = {name: [] for name in _CONDITIONS}
        active_elapsed_ms: dict[str, float] = dict.fromkeys(_CONDITIONS, 0.0)
        frames_dropped: dict[str, int] = dict.fromkeys(_CONDITIONS, 0)
        segment_order: list[str] = []
        exhausted = False

        with source:
            iterator = iter(source.frames())

            for _ in range(self._cycles):
                for condition in _CONDITIONS:
                    segment_order.append(condition)
                    router.route_to(logger_for_condition[condition])
                    exhausted = self._run_segment(
                        condition,
                        clock=clock,
                        iterator=iterator,
                        metrics=metrics,
                        recorder=recorder if condition == "recording_on" else None,
                        samples=raw_samples[condition],
                        exhausted=exhausted,
                        active_elapsed_ms=active_elapsed_ms,
                        frames_dropped=frames_dropped,
                    )

            # 停止処理（`source.__exit__`）はどの条件にも属さない。向き先を
            # 無効側へ戻してから抜ける。
            router.route_to(null_logger)

        # summary.json への書き込み（副作用）のために呼ぶ。戻り値は使わない
        # ——`OverheadResult.frames_dropped` はセグメント境界の差分から
        # 条件ごとに積算しており、`RecordingStats` を経由しない。渡す
        # `CaptureStats` は**共有した計測点の累計**であり、`recording_on`
        # 条件だけの値ではない（summary.json は記録側の付随成果物であって、
        # 本ベンチの判定材料ではない）。
        recorder.close(metrics.counters())
        structured_logger.close()

        results = tuple(
            self._build_result(
                condition,
                raw_samples[condition],
                active_elapsed_ms[condition],
                frames_dropped[condition],
            )
            for condition in _CONDITIONS
        )
        by_condition = {r.condition: r for r in results}

        verdicts = {
            "logging_on_vs_logging_off": _compute_verdict(
                by_condition["logging_on"], by_condition["logging_off"]
            ),
            "recording_on_vs_logging_off": _compute_verdict(
                by_condition["recording_on"], by_condition["logging_off"]
            ),
        }

        hardware_disclaimer: str | None = None
        if settings.source != SourceKind.LIVE:
            hardware_disclaimer = _HARDWARE_DISCLAIMER_TEMPLATE.format(source=settings.source)

        return LoggingOverheadReport(
            results=results,  # type: ignore[arg-type]
            verdicts=verdicts,
            raw_samples={name: tuple(values) for name, values in raw_samples.items()},
            segment_order=tuple(segment_order),
            hardware_disclaimer=hardware_disclaimer,
        )

    # ------------------------------------------------------------------
    # 内部実装
    # ------------------------------------------------------------------

    def _run_segment(
        self,
        condition: str,
        *,
        clock: SessionClock,
        iterator: Iterator[CaptureFrame],
        metrics: CaptureMetrics,
        recorder: SessionRecorder | None,
        samples: list[float],
        exhausted: bool,
        active_elapsed_ms: dict[str, float],
        frames_dropped: dict[str, int],
    ) -> bool:
        """1セグメント（`segment_s` 秒ぶん）を実行し、新しい `exhausted` を返す。

        `total_ms` は `next(iterator)` の直前・直後の `clock.now_ms()` の差
        として測る（モジュール docstring「本モジュール独自の `total_ms` 測定」
        参照）。`recorder` が渡された場合（`recording_on` 条件）は、取得した
        フレームを `recorder.write()` へ渡す処理も同じ計測区間に含める。

        `frames_dropped[condition]` には、**このセグメントの前後で読んだ
        `metrics.counters().frames_dropped` の差分**を足し込む（3条件が
        1つの `CaptureMetrics` を共有するため、累計値をそのまま読むと
        どの条件も同じ総数になってしまう）。

        供給が尽きた（`StopIteration`）場合は `True` を返してこのセグメントを
        打ち切る。入力元は3条件で共有しているため、尽きた時点で以後の
        すべてのセグメントが即座にスキップされる（条件ごとに別々の供給を
        持っていた旧構造との差分。共有した1本の列を分け合う以上、
        「1条件だけ尽きる」ことは起こり得ない）。
        """
        if exhausted:
            return True

        dropped_before = metrics.counters().frames_dropped
        segment_start_ms = clock.now_ms()
        deadline_ms = segment_start_ms + self._segment_s * 1000.0

        while clock.now_ms() < deadline_ms:
            t0 = clock.now_ms()
            try:
                frame = next(iterator)
            except StopIteration:
                exhausted = True
                break
            if recorder is not None:
                recorder.write(frame)
            t1 = clock.now_ms()
            samples.append(t1 - t0)

        active_elapsed_ms[condition] += clock.now_ms() - segment_start_ms
        frames_dropped[condition] += metrics.counters().frames_dropped - dropped_before
        return exhausted

    @staticmethod
    def _build_result(
        condition: str,
        samples: list[float],
        active_elapsed_ms: float,
        frames_dropped: int,
    ) -> OverheadResult:
        sorted_samples = sorted(samples)
        n = len(sorted_samples)
        p50 = _percentile(sorted_samples, 0.5)
        p95 = _percentile(sorted_samples, 0.95)
        p25 = _percentile(sorted_samples, 0.25)
        p75 = _percentile(sorted_samples, 0.75)

        measured_fps = n / (active_elapsed_ms / 1000.0) if active_elapsed_ms > 0.0 else 0.0

        return OverheadResult(
            condition=condition,  # type: ignore[arg-type]
            samples=n,
            total_ms_p50=p50,
            total_ms_p95=p95,
            total_ms_iqr=p75 - p25,
            measured_fps=measured_fps,
            frames_dropped=frames_dropped,
        )
