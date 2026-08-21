"""解像度・fps の掃引と実効サンプル数の算出を実装する（design.md「L8-L9: 比較・集計・入口 / ModeSweep」）。

候補となる `CaptureConfig`（解像度・fps・Color 有無）を**同一条件・同一時間**で
順に実行し、実測フレームレート・破棄件数・欠落件数・取得レイテンシの代表値と
分位点・CPU・メモリを記録する（要件 11.2, 11.3）。

**評価軸は「フレームレート単体」ではなく「一定時間窓あたりの有効フレーム数」
である**（要件 11.4）。この指標を `ModeResult.effective_samples_per_window`
（**フレーム層の実効サンプル数**）と呼ぶ。窓長（`window_ms`）は
`ModeSweep` のコンストラクタ引数として**必ず**受け取り、算出ロジック内部に
固定値として埋め込まない。

============================================================================
指標名の対応関係（tasks.md の Revalidation Trigger、必読）
============================================================================

`effective_samples_per_window`（本モジュール、**フレーム層**の実効サンプル数）
は、`flying-object-tracking` Spec が将来定義する **`effective_points_per_window`**
（**点層**の実効点数）とは**別物の指標**である。両者は層が異なる（本 Spec は
capture 区間のフレーム列を数え、`flying-object-tracking` は検出・追跡後の
点列を数える）が、**対になる指標**として比較・報告される想定であり、
**片方だけ定義（窓の切り方・カウント方法）を変えると、フレーム層とサンプル層の
比較が成立しなくなる**。どちらかの定義を変更する場合は、もう一方の Spec への
再検証を伴わせること（tasks.md 冒頭の Revalidation Trigger）。結果を JSON へ
直列化する際（CLI・タスク 8.1 の責務）も、キー説明にこの対応関係を明記する
こと。

============================================================================
`effective_samples_per_window` の算出式（手計算で検証可能な形にする）
============================================================================

単純な代数的近似（`measured_fps * (window_ms / 1000)`）は fps がほぼ均一な
場合にのみ正しく、フレーム到着のバースト性・不均一性を隠しうる。本モジュールは
より頑健な**スライディングでない固定窓の実カウント**を採用する:

1. ウォームアップ終了時刻（`warmup_s * 1000` ms、計測開始からの相対値）を
   窓の原点 `origin_ms` とする。
2. 計測区間の長さ（`duration_s * 1000` ms）を `window_ms` で割った**完全な
   窓の個数** `n = floor(duration_s*1000 / window_ms)` を求める。
3. 各フレームの `t_capture_ms` から `origin_ms` を引いた経過時間を
   `window_ms` で割った商（floor）を窓番号とし、`0 <= 窓番号 < n` のフレーム
   だけを窓ごとに数える（末尾の不完全な窓に落ちるフレームは数えない —
   `n` 個ぶんの**完全な**窓だけを評価対象にすることで、末尾半端窓による
   過小評価バイアスを避ける）。
4. `effective_samples_per_window = (窓ごとのフレーム数の合計) / n`
   （＝窓あたりの平均有効フレーム数）。

`n == 0`（`window_ms` が `duration_s` より長い設定ミス的なケース）のときのみ、
代数近似 `measured_fps * (window_ms/1000)` にフォールバックする（本モジュール
docstring 内、`_effective_samples_per_window()` を参照）。

============================================================================
ウォームアップ
============================================================================

各モードの実行は必ず `warmup_s` 秒のウォームアップ区間を先に走らせる。
ウォームアップ区間中に取得したフレーム・計測点（`wait_ms`/`total_ms`/
CPU/メモリのサンプル）は**すべて**最終結果の算出対象から除外する
（要件 11.3 の「同一条件」を、ウォームアップ由来の立ち上がりノイズで
汚染しないため）。実装は「ウォームアップ専用のフェーズを別に走らせて
捨てる」のではなく、**単一の `frames()` ループを最初から最後まで流し、
`t_capture_ms >= warmup_end_ms` のフレームだけを集計対象へ追加する**
という形を取る（`_run_mode()` 参照）。

============================================================================
モード順の入れ替え再実行（実装判断）
============================================================================

tasks.md は「モード順を入れ替えた再実行ができるようにする」ことを求める
（熱の影響の切り分けのため）。design.md の `ModeSweep.__init__` は
`modes: Sequence[CaptureConfig]` を素直に受け取るのみで、内部にシャッフル・
2巡目実行の機構は持たない。本モジュールはこれを「**呼び出し側が `modes`
の順序を制御する**」という設計で満たす: `ModeSweep(modes=order_a, ...)` と
`ModeSweep(modes=tuple(reversed(order_a)), ...)`（または任意にシャッフルした
順序）をそれぞれ構築して `.run()` を2回呼べば、「モード順を入れ替えた
再実行」がそのまま可能になる。`run()` 自身は `self._modes` の**与えられた
順序のまま**を実行するだけであり、内部でシャッフルや2巡目実行を暗黙に
行わない（暗黙に行うと、呼び出し側が「順序を変えていないつもりの1巡目」と
「本モジュールが勝手に足した2巡目」を区別できなくなる）。各 `ModeResult`
は `width_px`/`height_px`/`fps`/`color_enabled` を保持するため、2回の
`run()` 結果はこれらのフィールドで対応付けて比較できる（`ModeResult` に
実行順序自体を記録するフィールドは無い——「同じ設定を2巡目に回したときの
差」が関心の対象であり、巡回番号そのものではないため）。

============================================================================
無効な回の表現（実装判断。design.md との差分を明記する）
============================================================================

design.md の `ModeResult` 擬似コード（14フィールド）には `valid` に相当する
フィールドが無い一方、次の2箇所が異なる粒度で「無効」を扱う:

- Batch/Job Contract: 「途中で失敗した**モード**は `null` として残り、他の
  モードの結果を捨てない」——モードの**構築・開始そのものが失敗**した
  （`open_source()` が例外を送出した、`start()` が例外を送出した等）場合を
  指すと読める。この場合、そのモードについては測定値が一切存在しない。
- Implementation Notes: 「USB2 接続や `stream_open` の失敗が検出された場合、
  その回の結果を**無効として記録する**」——「記録する」という言い回しは、
  結果オブジェクト自体は**存在し**、無効の旨が付随情報として残ることを
  示唆する（USB2 の場合、フレームの取得自体は一応進行しうる。
  `sources/realsense.py` の `usb2_warning` も「fps 計測結果を有効なものとして
  扱わない」という運用上の注記であり、フレームの取得を止めるものではない）。

この2つの記述は**両立しない粒度**（片方は結果を消す、片方は結果を残す）で
あるため、本モジュールは両方を字義通り実装することでどちらの記述も裏切らない
形を取る:

1. **モードの構築・実行が測定値ゼロのまま失敗した場合**（`open_source()`・
   `start()`・最初の `_acquire()` などが例外を送出し、1件も測定用フレームを
   集められなかった場合）: `ModeSweep.run()` が返す `tuple` の該当位置は
   **`None`** になる（design.md の Batch/Job Contract の「`null` として残る」
   という文言をそのまま実装する）。したがって本モジュールの `run()` の
   戻り型は design.md の擬似コード `tuple[ModeResult, ...]` を
   `tuple[ModeResult | None, ...]` へ拡張する。これは design.md の
   Batch/Job Contract 自体が要求する挙動であり、擬似コードの型注釈が
   これを反映し忘れているだけと解釈する。
2. **一部でも測定値を集められた場合**（USB2 警告が検出された、または
   測定の途中で例外が発生したが計測開始後に何らかのフレームは得られていた
   場合）: `ModeResult` を実際に構築して返すが、**追加フィールド**
   `valid: bool = True` を `False` にし、`invalid_reason: str | None = None`
   に理由の説明文を入れる。この2フィールドの追加は、design.md の擬似コードに
   無い最小限の加算的拡張であり、本 Spec の他タスク（例: タスク 3.2 の
   `SimulatedSource.__init__` へのキーワード専用引数追加、タスク 4.3 の
   `SessionRecorder.__init__` への引数追加）と同種の、コンストラクタ／
   データクラスをキーワード専用または既定値つきで拡張する前例に倣う
   （既存フィールドの意味・型は一切変更しない）。

**`valid`/`invalid_reason` は「目標値を満たしたか」の判定ではない**
（要件 9.5, 11.6 が禁じる合否判定とは無関係）。これらは「この回の計測値を
そのまま信用してよいか」という**計測条件の健全性**を表すフラグであり、
`measured_fps` や `effective_samples_per_window` の値そのものに対する
良否判断は一切含まない（`valid=False` でも、集められた範囲の実測値は
そのまま `ModeResult` に残る——「計測結果を無条件に有効なものとして扱わない」
という要件 10.4 と同じ考え方を、フィールド名を変えてこちらにも適用したもの）。

============================================================================
USB2 接続・モード開閉失敗の検出（3種のアダプタ共通の扱い）
============================================================================

`RealSenseSource`（タスク 6.1）は `usb2_warning: bool | None` プロパティを
公開する（`True`=USB2 検出、`False`=USB2 でないと判定できた、`None`=判定
不能）。`SimulatedSource`/`RecordedSource` にはこの概念自体が無い
（USB という物理層を持たない）。本モジュールはこれを `getattr(source,
"usb2_warning", None)` で**汎用的に**問い合わせる（`hasattr` チェックを
明示的に書く代わりに `getattr` の既定値を使うことで、属性が無いアダプタでは
常に `None` が返り、無効化トリガが一切発火しない——simulated/recorded に
対する誤検出を構造的に防ぐ）。`True` が返った場合のみ `invalid_reason` に
USB2 検出を記録する（`_detect_usb2_invalid()` 参照）。「モード開閉失敗」
（`open_source()`/`start()`/`_acquire()` の例外）は「無効な回の表現」節の
経路1（測定値ゼロなら `None`）または経路2（部分的な測定値があれば
`valid=False`）のいずれかとして扱う。

============================================================================
目標値の充足判定を行わない（要件 9.6, 11.6）
============================================================================

本モジュールは `measured_fps` / `effective_samples_per_window` /
CPU・メモリのいずれについても、「これは十分か」を判定する `bool` を
一切算出・保持しない。`ModeSweep` は Pi 4 を継続するかの判断も行わない
（材料の提供にとどまる。判断は `measurements.md` へ人手で記録する、design.md
の Batch/Job Contract の想定）。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from sensing_foundation.config import CaptureConfig, RuntimeSettings
from sensing_foundation.errors import SensingConfigError
from sensing_foundation.metrics import CaptureMetrics
from sensing_foundation.source import FrameSource, open_source
from sensing_foundation.sysstat import Sysstat
from sensing_foundation.timebase import SessionClock
from sensing_foundation.types import CaptureFrame

if TYPE_CHECKING:
    # `open_source()` と同じ流儀（`source.py` モジュール docstring
    # 「レイヤー逸脱の扱い」参照）: 型注釈のためだけの参照であり、
    # `TYPE_CHECKING` の外では実行時に import しない。
    from sensing_foundation.sources.recorded import ReplaySpeed
    from sensing_foundation.sources.simulated import FrameSupplier

__all__ = ["ModeResult", "ModeSweep"]


@dataclass(frozen=True, slots=True)
class ModeResult:
    """1候補モード分の掃引結果（design.md「ModeSweep」の `ModeResult`）。

    design.md の擬似コードが定める14フィールドに加え、`valid` /
    `invalid_reason` の2フィールドを加算的に拡張する（モジュール docstring
    「無効な回の表現」参照）。

    Attributes:
        width_px: このモードの Depth ストリーム幅（px）。
        height_px: このモードの Depth ストリーム高さ（px）。
        fps: このモードの要求フレームレート。
        color_enabled: このモードで Color ストリームが有効だったか。
        measured_fps: ウォームアップ除外後の実測フレームレート。
        frames_yielded: ウォームアップ除外後に取得できたフレーム数。
        frames_dropped: 同、ドレインによる破棄件数の合計。
        frames_missing: 同、`seq` の欠落件数の合計。
        effective_samples_per_window: **フレーム層の実効サンプル数**
            （評価窓あたりの有効フレーム数）。`flying-object-tracking` の
            **点層**の実効点数（`effective_points_per_window`、将来定義）
            とは**別物の、対になる指標**である。片方だけ定義を変えると
            比較が成立しなくなる（モジュール docstring 冒頭を参照）。
        wait_ms_p50: 取得待機時間（`FrameTiming.wait_ms`）の中央値。
        wait_ms_p95: 同、95パーセンタイル。
        total_ms_p50: 1フレームあたりの総所要時間（`FrameTiming.total_ms`）
            の中央値。
        total_ms_p95: 同、95パーセンタイル。
        cpu_percent_mean: プロセスの CPU 使用率サンプルの平均（0-100）。
            サンプルが1件も取れなかった場合（`Sysstat` 非対応環境、または
            計測区間が短すぎてサンプル間隔に達しなかった場合）は `None`。
        rss_bytes_max: プロセスの常駐メモリ量サンプルの最大値。同上の理由で
            `None` になり得る。
        valid: この回の計測値をそのまま信用してよいかを表す（**目標値の
            充足判定ではない**。モジュール docstring 参照）。既定 `True`。
        invalid_reason: `valid=False` の理由を人間可読な文字列で説明する。
            `valid=True` のときは `None`。
    """

    width_px: int
    height_px: int
    fps: int
    color_enabled: bool
    measured_fps: float
    frames_yielded: int
    frames_dropped: int
    frames_missing: int
    effective_samples_per_window: float
    wait_ms_p50: float
    wait_ms_p95: float
    total_ms_p50: float
    total_ms_p95: float
    cpu_percent_mean: float | None
    rss_bytes_max: int | None
    valid: bool = True
    invalid_reason: str | None = None


# ----------------------------------------------------------------------------
# 分位点計算（`logsummary.py` の `_percentile()` と同じ規約: k=(n-1)*q の
# 線形補間。`logsummary._percentile` は非公開のため、本モジュールは重複実装
# する——両モジュールとも同じ規約に揃えることを、この docstring とテストで
# 明示する）。
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
# effective_samples_per_window: 固定窓の実カウント（モジュール docstring参照）
# ----------------------------------------------------------------------------


def effective_samples_per_window(
    frame_times_ms: Sequence[float],
    *,
    origin_ms: float,
    span_ms: float,
    window_ms: float,
) -> float:
    """`frame_times_ms`（ウォームアップ除外後の `t_capture_ms` 列）から
    「窓あたりの平均有効フレーム数」を算出する、純粋関数（副作用なし）。

    `ModeSweep` の内部状態から切り離した独立関数として公開する（`window_ms`
    を変えて同一の `frame_times_ms` に対する結果を比較するテストや、実行を
    伴わない手計算検証をしやすくするため）。算出式はモジュール docstring
    「`effective_samples_per_window` の算出式」を参照。

    Args:
        frame_times_ms: フレームの `t_capture_ms`（`origin_ms` と同じ時間
            基準）。順不同でよい。
        origin_ms: 窓の原点（通常はウォームアップ終了時刻）。
        span_ms: 評価対象の区間長（通常は `duration_s * 1000`）。
        window_ms: 窓長（ms）。**固定値をここに埋め込まず、必ず呼び出し側が
            指定する**（要件 11.4）。

    Returns:
        窓あたりの平均有効フレーム数。`span_ms < window_ms`（完全な窓が
        1つも取れない設定ミス的なケース）の場合のみ、代数近似
        `(frame_times_ms の実測 fps) * (window_ms / 1000)` へフォールバックする。

    Raises:
        SensingConfigError: `window_ms <= 0`（呼び出し方の誤り）。
    """
    if window_ms <= 0:
        raise SensingConfigError(f"window_ms は正でなければならない: {window_ms}")

    num_complete_windows = math.floor(span_ms / window_ms)

    if num_complete_windows <= 0:
        if not frame_times_ms:
            return 0.0
        last_ms = max(frame_times_ms) - origin_ms
        if last_ms <= 0:
            return 0.0
        approx_fps = len(frame_times_ms) / (last_ms / 1000.0)
        return approx_fps * (window_ms / 1000.0)

    counts = [0] * num_complete_windows
    for t_ms in frame_times_ms:
        offset_ms = t_ms - origin_ms
        if offset_ms < 0:
            continue
        bin_index = math.floor(offset_ms / window_ms)
        if 0 <= bin_index < num_complete_windows:
            counts[bin_index] += 1

    return sum(counts) / num_complete_windows


# ----------------------------------------------------------------------------
# 測定専用の内部 Logger 実装（`Logger` プロトコルへ構造的に適合する）
# ----------------------------------------------------------------------------


@dataclass(slots=True)
class _RecordedEvent:
    """`_RecordingLogger.emit()` が1件記録するイベント（内部表現）。"""

    t_ms: float
    stage: str
    event: str
    data: dict[str, object]


class _RecordingLogger:
    """`emit()` された内容をファイルへ書かず、プロセス内のメモリへ貯めるだけの `Logger`。

    `CaptureMetrics`（要件 9.1, 9.3）は計測点を `Logger.emit()` 経由でしか
    公開しないため、`wait_ms`/`total_ms`/CPU/メモリのサンプルを本モジュールが
    後から分位点計算・平均計算するには、この受け皿が必要になる。
    `obslog.StructuredLogger` は非同期 writer スレッドとディスク上の NDJSON
    ファイルを持つが、`ModeSweep` は掃引するモードの数だけ永続ファイルを
    増やす必要が無い（プロセス内で完結した集計で十分）ため、本クラスは
    同期的にメモリへ追記するだけの最小実装とする（`obslog.Logger`
    Protocol への構造的適合——`obslog.py` モジュール docstring が明記する
    「`Protocol` を構造的部分型として扱う」流儀を踏襲する）。

    `t_ms` は `emit()` 呼び出し時点で `clock.now_ms()` を読み、
    `StructuredLogger` が内部で行っているのと同じタイミングづけを行う。
    """

    def __init__(self, clock: SessionClock) -> None:
        self.enabled = True
        self._clock = clock
        self.events: list[_RecordedEvent] = []

    def emit(self, stage: str, event: str, /, **data: object) -> None:
        self.events.append(
            _RecordedEvent(t_ms=self._clock.now_ms(), stage=stage, event=event, data=dict(data))
        )

    def stage(self, stage: str):  # pragma: no cover - ModeSweep は未使用
        raise NotImplementedError

    def timed(self, stage: str, event: str, /, **data: object):  # pragma: no cover
        raise NotImplementedError

    def stats(self):  # pragma: no cover - ModeSweep は未使用
        raise NotImplementedError

    def close(self, timeout_ms: float = 1000.0) -> None:
        return None


def _detect_usb2_invalid(source: object) -> str | None:
    """`source` が USB2 接続を検出していれば、無効理由の文字列を返す（要件 1.5）。

    `getattr(source, "usb2_warning", None)` で汎用的に問い合わせる。
    `RealSenseSource`（タスク 6.1）のみがこの属性を公開し、`True`/`False`/
    `None`（判定不能）を返す。`SimulatedSource`/`RecordedSource` はこの属性を
    持たないため、`getattr` の既定値 `None` が返り、常に無効化しない
    （USB という物理層を持たない入力元に対する誤検出を構造的に防ぐ）。

    `True` のときのみ無効理由を返す。`False`・`None`（判定不能）は、いずれも
    「USB2 だと確定できなかった」ことを意味し、無効化のトリガにはしない
    （`RealSenseSource.usb2_warning` の docstring 自身が「`False` を既定として
    誤って安全側に倒さない」——`None` は欠測であり `False` と同一視しない、
    という設計を明記している。本関数は `True` のみを唯一のトリガにすることで
    この区別と整合させる）。
    """
    usb2_warning = getattr(source, "usb2_warning", None)
    if usb2_warning is True:
        return (
            "USB2 接続が検出された（RealSenseSource.usb2_warning=True）。"
            "fps 計測結果を有効なものとして扱わない（要件 1.5）。"
        )
    return None


class ModeSweep:
    """候補 `CaptureConfig` を同一条件・同一時間で順に掃引する（design.md「ModeSweep」）。

    Preconditions: `modes` は空でない。`duration_s` / `window_ms` は正。
        `warmup_s` は0以上。
    Postconditions: `run()` が返す `tuple` の長さは `len(modes)` と一致する。
        各要素は成功時 `ModeResult`（測定値ゼロのまま失敗した場合のみ `None`。
        モジュール docstring「無効な回の表現」参照）。1つのモードの失敗は
        他のモードの結果に影響しない。
    Invariants: 目標値の充足判定・Pi 4 継続可否の判断を一切行わない
        （要件 9.6, 11.6）。
    """

    def __init__(
        self,
        modes: Sequence[CaptureConfig],
        duration_s: float,
        window_ms: float,
        warmup_s: float,
    ) -> None:
        """`ModeSweep` を組み立てる。

        Args:
            modes: 掃引する候補 `CaptureConfig` の列。`run()` は**この順序の
                まま**実行する（内部でシャッフルしない。モジュール docstring
                「モード順の入れ替え再実行」参照）。空であってはならない。
            duration_s: ウォームアップ後、各モードにつき測定を継続する秒数
                （同一条件・同一時間で比較するための共通パラメータ。
                要件 11.2）。
            window_ms: `effective_samples_per_window` の評価窓長（ms）。
                固定値ではなく、呼び出し側が明示的に選ぶ（要件 11.4）。
                design.md Implementation Notes の目安は「既定窓 600ms
                （総飛行時間の下限側の目安）」だが、本クラス自体は既定値を
                持たず、呼び出し側に必須指定させる。
            warmup_s: 各モードで測定開始前に走らせるウォームアップの秒数
                （0以上。0ならウォームアップなし）。

        Raises:
            SensingConfigError: `modes` が空、`duration_s<=0`、
                `window_ms<=0`、`warmup_s<0` のいずれか（呼び出し方の誤り）。
        """
        if len(modes) == 0:
            raise SensingConfigError("modes は少なくとも1件指定する必要がある")
        if duration_s <= 0:
            raise SensingConfigError(f"duration_s は正でなければならない: {duration_s}")
        if window_ms <= 0:
            raise SensingConfigError(f"window_ms は正でなければならない: {window_ms}")
        if warmup_s < 0:
            raise SensingConfigError(f"warmup_s は0以上でなければならない: {warmup_s}")

        self._modes = tuple(modes)
        self._duration_s = duration_s
        self._window_ms = window_ms
        self._warmup_s = warmup_s

    def run(
        self,
        settings: RuntimeSettings,
        *,
        supplier: "FrameSupplier | None" = None,
        speed: "ReplaySpeed" = "fast",
    ) -> tuple[ModeResult | None, ...]:
        """全モードを順に実行し、結果を返す。

        design.md の擬似コードは `run(self, settings: RuntimeSettings) ->
        tuple[ModeResult, ...]` という1引数シグネチャを示すが、`settings.source
        == SourceKind.SIMULATED` のとき `open_source()`（タスク 4.6）は
        `supplier` を必須で要求する（`ModeSweep` 自身は投擲物理・ノイズ生成を
        持たない——`SimulatedSource` と同じ理由、要件 4.5）。本メソッドは
        `open_source()` 自身が採用した拡張と同じ形（キーワード専用引数の
        追加）でこれに対応する。戻り値の型も `tuple[ModeResult | None, ...]`
        へ拡張する（モジュール docstring「無効な回の表現」参照）。

        `settings.capture` はここでは使われない——各モードの `CaptureConfig`
        は `self._modes` の値で `settings` を置き換えて使う（`settings` は
        `source`/`recording`/`logging`/`session_path` の共通部分の供給元）。

        `settings.source == SourceKind.RECORDED` の場合の注意: 再生される
        セッションの実際の解像度・fps は記録時点で確定しており、`modes` の
        各 `CaptureConfig.width_px`/`height_px`/`fps` を変えても再生内容
        そのものは変わらない（`RecordedSource` は記録された Depth 配列を
        そのまま返す）。したがって RECORDED での掃引は「同一の記録済み
        セッションに対し、`ModeResult` のメタデータとしてどの候補値を
        記録するか」を確認する以上の意味を持たない場合がある——実機
        （live）または simulated での掃引が本来の比較手段である。

        Args:
            settings: `source`/`recording`/`logging`/`session_path` の
                共通設定。`capture` はモードごとに上書きされる。
            supplier: `settings.source == SourceKind.SIMULATED` のときのみ
                必須。全モードで共有される（`open_source()` と同じ意味論。
                `run()` docstring 参照）。
            speed: `settings.source == SourceKind.RECORDED` のときのみ意味を
                持つ再生速度。

        Returns:
            `self._modes` と同じ順序・同じ長さの `tuple`。各要素は
            `ModeResult`（測定値ゼロのまま失敗した場合のみ `None`）。
        """
        results: list[ModeResult | None] = []
        for index, mode in enumerate(self._modes):
            results.append(self._run_mode(mode, settings, index, supplier=supplier, speed=speed))
        return tuple(results)

    # ------------------------------------------------------------------
    # 内部実装
    # ------------------------------------------------------------------

    def _run_mode(
        self,
        mode: CaptureConfig,
        settings: RuntimeSettings,
        index: int,
        *,
        supplier: "FrameSupplier | None",
        speed: "ReplaySpeed",
    ) -> ModeResult | None:
        """1候補モード分を実行する。1つのモードの失敗は他のモードに波及させない。

        `Exception`（`BaseException` ではない——`KeyboardInterrupt` 等は
        素通しする）を広く捕捉する。これは実装の不備を隠すためではなく、
        design.md Batch/Job Contract 自身が要求する「1つのモードの失敗が
        他のモードの結果を道連れにしない」を成立させるための意図的な設計
        判断である（`doctor.py` の「各項目は独立して失敗できる」という
        既存の設計判断と同じ思想を、モード単位に適用したもの）。捕捉した
        例外の種類・メッセージは `invalid_reason` へそのまま記録するため、
        原因の切り分けを妨げない。
        """
        session_id = (
            f"bench-modes-{index}-{mode.width_px}x{mode.height_px}"
            f"-{mode.fps}fps{'-color' if mode.color_enabled else ''}"
        )
        clock = SessionClock(session_id=session_id)
        sysstat = Sysstat()
        recorder = _RecordingLogger(clock)
        metrics = CaptureMetrics(recorder, clock, sysstat)
        mode_settings = replace(settings, capture=mode)

        warmup_end_ms = self._warmup_s * 1000.0
        measure_end_ms = warmup_end_ms + self._duration_s * 1000.0

        collected: list[CaptureFrame] = []
        invalid_reason: str | None = None

        try:
            source: FrameSource = open_source(
                mode_settings, metrics, clock=clock, supplier=supplier, speed=speed
            )
            with source:
                for frame in source.frames():
                    metrics.snapshot_resources()
                    if frame.t_capture_ms >= warmup_end_ms:
                        collected.append(frame)
                    if frame.t_capture_ms >= measure_end_ms:
                        break
                # ループがここへ抜ける場合、`break` によるものか、供給が
                # 測定終了時刻に達する前に尽きた（simulated/recorded の
                # 有限系列が StopIteration で終端した）ものかのいずれか。
                # 後者でも得られた分だけで結果を組み立てる（例外ではない）。
                usb2_reason = _detect_usb2_invalid(source)
                if usb2_reason is not None:
                    invalid_reason = usb2_reason
        except Exception as exc:  # noqa: BLE001 - 意図的な広い捕捉（docstring参照）
            invalid_reason = f"{type(exc).__name__}: {exc}"
            if not collected:
                # 経路1: 測定値ゼロのまま失敗 → null として残す
                # （design.md Batch/Job Contract）。
                return None
            # 経路2: 部分的な測定値がある → valid=False で記録する
            # （design.md Implementation Notes「無効として記録する」）。

        return self._build_result(mode, collected, recorder, warmup_end_ms, invalid_reason)

    def _build_result(
        self,
        mode: CaptureConfig,
        collected: list[CaptureFrame],
        recorder: _RecordingLogger,
        warmup_end_ms: float,
        invalid_reason: str | None,
    ) -> ModeResult:
        frames_yielded = len(collected)
        frames_dropped = sum(frame.dropped_before for frame in collected)
        frames_missing = sum(frame.gap_before for frame in collected)

        measured_fps = self._measured_fps(collected, warmup_end_ms)

        effective = effective_samples_per_window(
            [frame.t_capture_ms for frame in collected],
            origin_ms=warmup_end_ms,
            span_ms=self._duration_s * 1000.0,
            window_ms=self._window_ms,
        )

        wait_values = sorted(
            float(evt.data["wait_ms"])
            for evt in recorder.events
            if evt.t_ms >= warmup_end_ms
            and evt.stage == "capture"
            and evt.event == "frame"
            and "wait_ms" in evt.data
        )
        total_values = sorted(
            float(evt.data["total_ms"])
            for evt in recorder.events
            if evt.t_ms >= warmup_end_ms
            and evt.stage == "capture"
            and evt.event == "frame"
            and "total_ms" in evt.data
        )
        cpu_values = [
            float(evt.data["cpu_percent"])
            for evt in recorder.events
            if evt.t_ms >= warmup_end_ms
            and evt.stage == "capture"
            and evt.event == "resources"
            and evt.data.get("cpu_percent") is not None
        ]
        rss_values = [
            int(evt.data["process_rss_bytes"])
            for evt in recorder.events
            if evt.t_ms >= warmup_end_ms
            and evt.stage == "capture"
            and evt.event == "resources"
            and evt.data.get("process_rss_bytes") is not None
        ]

        return ModeResult(
            width_px=mode.width_px,
            height_px=mode.height_px,
            fps=mode.fps,
            color_enabled=mode.color_enabled,
            measured_fps=measured_fps,
            frames_yielded=frames_yielded,
            frames_dropped=frames_dropped,
            frames_missing=frames_missing,
            effective_samples_per_window=effective,
            wait_ms_p50=_percentile(wait_values, 0.5),
            wait_ms_p95=_percentile(wait_values, 0.95),
            total_ms_p50=_percentile(total_values, 0.5),
            total_ms_p95=_percentile(total_values, 0.95),
            cpu_percent_mean=(sum(cpu_values) / len(cpu_values)) if cpu_values else None,
            rss_bytes_max=max(rss_values) if rss_values else None,
            valid=invalid_reason is None,
            invalid_reason=invalid_reason,
        )

    @staticmethod
    def _measured_fps(collected: list[CaptureFrame], warmup_end_ms: float) -> float:
        """ウォームアップ除外後の実測フレームレートを算出する。

        `CaptureMetrics.counters()` の `measured_fps`（構築時刻からの経過時間
        を分母にする）とは異なり、本メソッドは**ウォームアップ終了時刻から
        最後に集計したフレームまでの経過時間**を分母にする——ウォームアップ
        区間を計測から除外する、というこのモジュールの前提と整合させるため
        （モジュール docstring「ウォームアップ」参照）。
        """
        if not collected:
            return 0.0
        elapsed_ms = collected[-1].t_capture_ms - warmup_end_ms
        if elapsed_ms <= 0:
            return 0.0
        return len(collected) / (elapsed_ms / 1000.0)
