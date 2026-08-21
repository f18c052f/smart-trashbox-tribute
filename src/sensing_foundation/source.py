"""入力元の契約と共通挙動を実装する（design.md「L5: 取得と永続化 / FrameSource / BaseFrameSource」）。

`FrameSource` は入力元（live / recorded / simulated）を1つの契約に揃える
イテレータ形状の `Protocol` である。`for frame in source.frames(): ...` が
下流のすべての入口になる（要件 4.1）。

`BaseFrameSource` はこの契約を満たす**共有実装**であり、次を一元管理する
（要件 2.1〜2.5, 2.7, 2.8, 4.6）:

- **ドレイン**: 待機で1枚受け取った直後に、滞留分を捨てて最新へ追いつく
  （要件 2.1）。ドレインを行うかどうかは `CaptureConfig.drain_enabled` で
  制御し、判断そのものを本クラスに集約する（下記「ドレインの ON/OFF を
  誰が決めるか」参照）。
- **欠落検出**: 入力元が付けた `seq` の飛びを検出し、`gap_before` として
  次のフレームに付与する（要件 2.3）。
- **統計更新・計測点送出**: `wait_ms` / `drain_ms` / `handoff_ms` /
  `total_ms` を自前の `SessionClock` で測り、`CaptureMetrics.frame()` へ
  1フレームにつき1回だけ渡す（要件 2.2, 2.3, 9.1）。
- **取得失敗時の継続/停止**: `CaptureConfig.on_acquire_error` に従い、
  `"continue"` なら `CaptureMetrics.acquire_error()` で計数して次の取得へ
  進み、`"stop"` なら `SourceUnavailableError` を送出して取得を止める
  （要件 2.7）。

アダプタ（`SimulatedSource` / `RecordedSource` / `RealSenseSource`）が実装
するのは `_acquire()`（1枚取る）と `_drain_latest()`（滞留分から最新を取る）
の**2操作のみ**である（要件 4.6）。入力元固有の設定（供給関数・SDK 接続情報・
記録セッションパスなど）は各アダプタの**コンストラクタ**で受け、
`start()` / `frames()` / `stop()` という共通契約の引数には一切現れない。

**取得ループの流れ**（design.md「取得ループ（古いフレームを溜めない）」
シーケンス図）:

    wait start を記録 → _acquire() で1枚待つ → wait end を記録
    → （drain_enabled のときのみ）_drain_latest() で滞留分を捨てて最新へ
      追いつく → 破棄件数を記録 → 欠落検出 → CaptureFrame を組み立てる
      （セッション時刻を付与） → handoff end を記録
    → CaptureMetrics.frame(timing, frame) を1回だけ呼ぶ → 呼び出し側へ yield

`metrics.py` のモジュール docstring が明記する通り、シーケンス図の
「mark wait start」「mark wait end」「add discarded count」「mark handoff
end」は `BaseFrameSource` と `CaptureMetrics` の間の**概念的な**会話であり、
実際に `CaptureMetrics` へ届く呼び出しは `frame(timing, frame)`（成功時）と
`acquire_error()`（失敗時）の2つだけである。時刻の計測（wait/drain/handoff
の各区間）は本クラスが自前で保持する `SessionClock` を使って行う——
`CaptureMetrics` は自身の時計を外部へ公開しないため、`BaseFrameSource` は
構築時に `CaptureMetrics` と**同一の** `SessionClock` インスタンスを別途
受け取る（`timebase.py` の「`SessionClock` は取得・記録・ロギングの間で
同一インスタンスを共有することを前提とする」という方針に従う）。

**ドレインの ON/OFF を誰が決めるか（実装判断）**: design.md の Invariant は
「`drain_enabled` が真でも、recorded / simulated では常にドレインを行わない」
と書く。本タスク（3.1）はこの一文を「`BaseFrameSource` が `drain_enabled`
（コンストラクタ引数、`CaptureConfig.drain_enabled` に由来）を見て
`_drain_latest()` を呼ぶかどうか自体を決める」という設計で実現する。
これにより:

1. 「ドレイン有効時のみ破棄件数が増える」という本タスクの観測可能な完了
   状態を、`BaseFrameSource` 単体（フェイクアダプタ）に対して直接検証できる
   （アダプタ側の協力を前提にしない）。
2. 将来の `SimulatedSource` / `RecordedSource`（タスク 3.2 / 4.5）は、
   自分の `super().__init__(...)` 呼び出しで `drain_enabled=False` を
   **固定で** 渡せばよい（利用者が渡した `CaptureConfig.drain_enabled` の値に
   関わらず）。これにより「recorded / simulated は常にドレインを行わない」
   という Invariant を、各アダプタが自分の意思で明示的に満たす形になる。
   （代替案として「アダプタの `_drain_latest()` 自身が常に `(None, 0)` を
   返すことで無効化する」という方式も設計として妥当だが、その場合
   `drain_enabled` フラグの ON/OFF 判断がアダプタと基底クラスの両方に
   分散し、「基底実装に一元化する」という本タスクの要求と整合しにくい。
   本実装は前者を採用する。）

**`RawFrame` の形（実装判断）**: design.md はアダプタが返す生フレームの型を
`"RawFrame | None"` という型ヒントの置き場としてのみ示し、具体的な形は
規定していない。本タスクはこれを最小限のデータクラスとして次の形に定める:

    RawFrame(seq, depth, device_timestamp_ms=None,
              timestamp_domain=TimestampDomain.UNKNOWN,
              capture_latency_ms=None)

`CaptureFrame` の構築に必要な「入力元固有の生の値」をそのまま運ぶだけの
器であり、`index`（`BaseFrameSource` 側の通し番号）・`t_capture_ms`
（`SessionClock` から得るセッション時刻）・`profile` / `source`
（`BaseFrameSource` が既に知っている値）・`dropped_before` / `gap_before`
（本クラスが計算する値）は含めない。`capture_latency_ms` の算出（デバイス
時刻とホスト時刻の突き合わせ）はアダプタ固有の責務であり、`RawFrame` は
既に算出済みの値をそのまま運ぶだけで、本モジュールはこれを計算しない。
タスク 3.2 / 4.5 / 6.1 の各アダプタはこの形に合わせて `RawFrame` を組み立てる
こと。

**`_acquire()` が `None` を返す意味と、終端シグナルとの違い**: `_acquire()`
が `None` を返すことは「取得の失敗（タイムアウト等）」を表し、
`on_acquire_error` に従って処理する。これに対し、有限の供給源（simulated /
recorded）が「もう供給するフレームがない」ことを示す**正常な終端**は、
`_acquire()` が `StopIteration` を送出することで表す（`None` とは意味が
異なる別のシグナルとして区別する）。`frames()` はこの `StopIteration` を
明示的に捕捉してジェネレータを正常終了させる（PEP 479 が問題にする
「捕捉されずに漏れ出る `StopIteration`」ではなく、本クラスの内部で完結する
通常の `try/except` である）。この終端シグナルはタスク 3.1 の境界
（`SimulatedSource` を含まない）の外で使われる想定の拡張点であり、本タスクの
テストでは境界を明示するための最小限の確認に留める。

**`stats` は `CaptureMetrics.counters()` を素通しする（実装判断）**:
`BaseFrameSource` は破棄数・欠落数・実測フレームレートなどの2つ目の
並行したカウンタ集合を持たない。`CaptureMetrics`（タスク 2.2）が既に
`frame()` / `acquire_error()` の呼び出しからこれらを集計しているため、
`stats` プロパティは単に `self._metrics.counters()` を返す。ただし
design.md の Postcondition「`stop()` 後は `stats` が確定する」を満たすため、
`stop()` 呼び出し時点の `counters()` を1度だけ凍結して保持し、以後の
`stats` アクセスはその凍結値を返す（`CaptureMetrics.counters()` は
`duration_ms` / `measured_fps` を「現在時刻」から算出するため、凍結しないと
`stop()` 後も値が変わり続けてしまう）。

**`FrameSource` を構造的部分型として扱う（実装判断）**: design.md の
pseudocode は `class BaseFrameSource(FrameSource):` と書くが、本パッケージの
既存実装（`obslog.StructuredLogger` / `obslog.NullLogger` はいずれも
`Logger(Protocol)` を明示的には継承しない）は `Protocol` を構造的部分型
（duck typing）として扱う流儀を採っている。本モジュールもこれに揃え、
`BaseFrameSource` は `FrameSource` を明示的に継承せず、同じメンバ集合を
実装することで構造的に満たす。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import numpy as np

from sensing_foundation.config import CaptureConfig, RuntimeSettings
from sensing_foundation.errors import SensingConfigError, SourceUnavailableError
from sensing_foundation.metrics import CaptureMetrics, FrameTiming
from sensing_foundation.timebase import SessionClock
from sensing_foundation.types import (
    CaptureFrame,
    CaptureStats,
    SourceKind,
    StreamProfile,
    TimestampDomain,
)

if TYPE_CHECKING:
    # `open_source()`（本モジュール下部）の型注釈のためだけに、レイヤー6/7
    # （`sources.simulated` / `sources.recorded`）の型を参照する。
    # `TYPE_CHECKING` ブロックは実行時には評価されないため、これらの
    # import は `source.py`（レイヤー5）の**実行時**依存グラフには現れない
    # ——`open_source()` 関数docstring「レイヤー逸脱の扱い」を参照。
    from sensing_foundation.sources.recorded import ReplaySpeed
    from sensing_foundation.sources.simulated import FrameSupplier

__all__ = ["BaseFrameSource", "FrameSource", "RawFrame", "open_source"]


class FrameSource(Protocol):
    """入力元の共通契約（design.md「FrameSource / BaseFrameSource」Service Interface）。

    live / recorded / simulated のいずれのアダプタも、この形として下流へ
    渡される。`for frame in source.frames(): ...` がすべての入口である。
    """

    @property
    def kind(self) -> SourceKind:
        """入力元の種別。"""
        ...

    @property
    def profile(self) -> StreamProfile:
        """このフレーム取得時点で有効な取得設定。"""
        ...

    @property
    def stats(self) -> CaptureStats:
        """現時点までの取得統計の要約。`stop()` 後は値が確定する。"""
        ...

    def start(self) -> None:
        """取得を開始する。`frames()` の前に1度だけ呼ぶ（Precondition）。"""
        ...

    def frames(self) -> Iterator[CaptureFrame]:
        """フレームを1枚ずつ返すイテレータ。下流の唯一の入口。"""
        ...

    def stop(self) -> None:
        """取得を停止する。以後 `stats` は確定した値を返す。"""
        ...

    def __enter__(self) -> "FrameSource":
        ...

    def __exit__(self, *exc: object) -> None:
        ...


@dataclass(frozen=True, slots=True)
class RawFrame:
    """アダプタの `_acquire()` / `_drain_latest()` が返す、変換前の生フレーム。

    `BaseFrameSource` はこれを `CaptureFrame` へ変換する。本タスク（3.1）が
    定義する最小限の形であり、後続タスク（3.2 / 4.5 / 6.1）の各アダプタは
    この形に合わせて構築すること（モジュール docstring「`RawFrame` の形」
    を参照）。

    Attributes:
        seq: 入力元が付けたフレーム番号。欠落検出に使う。
        depth: `uint16`、shape=`(height_px, width_px)` の Depth 配列。
            読み取り専用化は `CaptureFrame` 構築時に行われるため、ここでは
            書き込み可能でよい。
        device_timestamp_ms: デバイス側時刻（ms）。問い合わせ不能なら `None`。
        timestamp_domain: `device_timestamp_ms` が何を基準とした値かを示す。
        capture_latency_ms: 取得レイテンシ（ms）。算出済みの値をそのまま
            運ぶだけであり、本モジュールはこれを計算しない。
    """

    seq: int
    depth: np.ndarray
    device_timestamp_ms: float | None = None
    timestamp_domain: TimestampDomain = TimestampDomain.UNKNOWN
    capture_latency_ms: float | None = None


class BaseFrameSource:
    """`FrameSource` 契約の共有実装（design.md「BaseFrameSource」）。

    ドレイン・欠落検出・統計更新・計測点送出を一元化し、アダプタには
    `_acquire()` / `_drain_latest()` の2操作のみを要求する。

    Preconditions: `start()` は `frames()` の前に1度だけ呼ばれる想定
        （`CaptureFrame` 同様、本クラスはこれを検証しない）。
    Postconditions: `frames()` が返す `CaptureFrame.index` は 0 から欠番なく
        増える。`stop()` 後は `stats` が確定する。
    Invariants: `drain_enabled` が偽のとき `_drain_latest()` は一切呼ばれない。
    """

    def __init__(
        self,
        *,
        kind: SourceKind,
        profile: StreamProfile,
        metrics: CaptureMetrics,
        clock: SessionClock,
        capture_config: CaptureConfig,
    ) -> None:
        """共有状態を初期化する。

        Args:
            kind: 入力元の種別。`kind` プロパティ・`CaptureFrame.source` へ
                そのまま使われる。
            profile: このフレーム取得時点で有効な取得設定。
            metrics: 計測点の送出先。呼び出し側（将来の `open_source()`、
                タスク 4.6）が構築して渡す。
            clock: `wait_ms` / `drain_ms` / `handoff_ms` の計測、および
                `CaptureFrame.t_capture_ms` の算出に使う。`metrics` が
                内部で使う `SessionClock` と**同一インスタンス**を渡すこと
                （モジュール docstring 参照）。
            capture_config: `drain_enabled` / `acquire_timeout_ms` /
                `on_acquire_error` の取得元。入力元固有の設定はここには
                含めない（アダプタごとのサブクラスのコンストラクタで別途
                受ける）。
        """
        self._kind = kind
        self._profile = profile
        self._metrics = metrics
        self._clock = clock
        self._drain_enabled = capture_config.drain_enabled
        self._acquire_timeout_ms = capture_config.acquire_timeout_ms
        self._on_acquire_error = capture_config.on_acquire_error

        self._next_index = 0
        self._last_seq: int | None = None
        self._final_stats: CaptureStats | None = None

    # ------------------------------------------------------------------
    # FrameSource 契約（構造的部分型として満たす）
    # ------------------------------------------------------------------

    @property
    def kind(self) -> SourceKind:
        return self._kind

    @property
    def profile(self) -> StreamProfile:
        return self._profile

    @property
    def stats(self) -> CaptureStats:
        """現時点までの取得統計。`stop()` 後は凍結された値を返す。"""
        if self._final_stats is not None:
            return self._final_stats
        return self._metrics.counters()

    def start(self) -> None:
        """取得を開始する。

        現在の共有実装は明示的な初期化処理を持たないため no-op だが、
        アダプタ側の `start()` オーバーライド（デバイスのオープンなど）が
        `super().start()` を呼べるよう、契約の一部として明示的に定義する。
        """

    def stop(self) -> None:
        """取得を停止し、`stats` を確定させる。

        二重呼び出しは安全（Invariant）。最初の呼び出し時点の
        `CaptureMetrics.counters()` を凍結し、以後の `stats` アクセスは
        その値を返す。
        """
        if self._final_stats is None:
            self._final_stats = self._metrics.counters()

    def __enter__(self) -> "BaseFrameSource":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def frames(self) -> Iterator[CaptureFrame]:
        """フレームを1枚ずつ返す。design.md の取得ループを実装する。"""
        while True:
            t_wait_start = self._clock.now_ms()
            try:
                raw = self._acquire(self._acquire_timeout_ms)
            except StopIteration:
                # 供給の正常な終端（simulated / recorded）。モジュール
                # docstring「`_acquire()` が `None` を返す意味と、終端
                # シグナルとの違い」を参照。
                return
            t_wait_end = self._clock.now_ms()
            wait_ms = t_wait_end - t_wait_start

            if raw is None:
                # 取得失敗（観測された事実。例外にしない。errors.py 参照）。
                self._metrics.acquire_error()
                if self._on_acquire_error == "stop":
                    raise SourceUnavailableError(
                        "フレーム取得に失敗した（on_acquire_error='stop' のため"
                        "取得を停止する）。デバイス接続・タイムアウト設定・"
                        "供給元の状態を確認せよ。"
                    )
                continue

            dropped_before = 0
            t_after_drain = t_wait_end
            drain_ms = 0.0
            if self._drain_enabled:
                latest, discarded = self._drain_latest()
                t_after_drain = self._clock.now_ms()
                drain_ms = t_after_drain - t_wait_end
                dropped_before = discarded
                if latest is not None:
                    raw = latest

            gap_before = self._detect_gap(raw.seq)

            index = self._next_index
            self._next_index += 1
            t_capture_ms = self._clock.now_ms()

            frame = CaptureFrame(
                index=index,
                seq=raw.seq,
                t_capture_ms=t_capture_ms,
                device_timestamp_ms=raw.device_timestamp_ms,
                timestamp_domain=raw.timestamp_domain,
                capture_latency_ms=raw.capture_latency_ms,
                depth=raw.depth,
                profile=self._profile,
                source=self._kind,
                dropped_before=dropped_before,
                gap_before=gap_before,
            )

            t_handoff_end = self._clock.now_ms()
            handoff_ms = t_handoff_end - t_after_drain
            timing = FrameTiming(
                wait_ms=wait_ms,
                drain_ms=drain_ms,
                handoff_ms=handoff_ms,
                total_ms=wait_ms + drain_ms + handoff_ms,
            )
            self._metrics.frame(timing, frame)

            yield frame

    def _detect_gap(self, seq: int) -> int:
        """`seq` の飛びを検出し、飛び幅を返す（0 なら飛びなし）。"""
        if self._last_seq is None:
            gap = 0
        else:
            diff = seq - self._last_seq
            gap = diff - 1 if diff > 1 else 0
        self._last_seq = seq
        return gap

    # ------------------------------------------------------------------
    # アダプタが実装する2つの抽象操作
    # ------------------------------------------------------------------

    def _acquire(self, timeout_ms: int) -> RawFrame | None:
        """1枚取る（待機を伴ってよい）。

        取得できたフレームを `RawFrame` として返す。取得に失敗（タイムアウト
        等）した場合は `None` を返す。有限の供給源が正常に尽きた場合は
        `None` ではなく `StopIteration` を送出すること（モジュール docstring
        参照）。

        Raises:
            StopIteration: 供給が正常に尽きた（simulated / recorded のみ）。
        """
        raise NotImplementedError

    def _drain_latest(self) -> tuple[RawFrame | None, int]:
        """滞留分から最新を取る。

        呼ばれるのは `drain_enabled` が真のときのみ（`BaseFrameSource` が
        判断する）。滞留がなければ `(None, 0)` を返す。滞留があれば、
        捨てた枚数を第2要素に入れつつ最新の1枚を第1要素で返す。
        """
        raise NotImplementedError


# ----------------------------------------------------------------------------
# open_source(): 入力元の唯一の生成口（design.md「FrameSource / BaseFrameSource」
# Service Interface、Components 表 `PublicApi` 行、タスク 4.6）
# ----------------------------------------------------------------------------

_SIMULATED_DEPTH_SCALE_MM = 1.0
"""`SourceKind.SIMULATED` 用に `open_source()` が組み立てる `StreamProfile` の
`depth_scale_mm` に使う既定値。

live のような実カメラの校正値を合成入力は持たないため、既存の値を流用する
のではなく明示的に選ぶ必要がある。本パッケージのテストフィクスチャ
（`tests/sensing_foundation/` 配下の `StreamProfile` を組み立てる全箇所——
`test_source.py` / `test_simulated_source.py` / `test_recorded_source.py` /
`test_writer.py` / `test_reader.py` / `test_ringbuffer.py` / `test_metrics.py`
など）が一貫して `depth_scale_mm=1.0` を採用しているため、その規約に合わせる。
`tech.md` 開発標準1（未検証の数値を要件のように埋め込まない）が戒めるのは
「根拠のない目標値」であり、ここでの `1.0` は「1生カウント=1mm」という
既存の合成データ規約との整合を明示的に選んだ値であって、性能目標や未実測の
主張ではない。"""


def _build_simulated_profile(capture: CaptureConfig) -> StreamProfile:
    """`SourceKind.SIMULATED` 用の `StreamProfile` を `CaptureConfig` から組み立てる。

    `RuntimeSettings.capture`（`CaptureConfig`）が持つ `width_px` /
    `height_px` / `fps` / `color_enabled` はそのまま使えるが、
    `depth_scale_mm` と `intrinsics` は `CaptureConfig` に無い
    （どちらも実カメラの校正値であり、合成入力にはそもそも存在しない）。
    `intrinsics` は `StreamProfile` の docstring が定める正規の値
    「入力元がカメラ内部パラメータを提供できない場合は `None`」をそのまま
    使い、`depth_scale_mm` は `_SIMULATED_DEPTH_SCALE_MM` を使う。
    """
    return StreamProfile(
        width_px=capture.width_px,
        height_px=capture.height_px,
        fps=capture.fps,
        depth_scale_mm=_SIMULATED_DEPTH_SCALE_MM,
        color_enabled=capture.color_enabled,
        intrinsics=None,
    )


def open_source(
    settings: RuntimeSettings,
    metrics: CaptureMetrics,
    *,
    clock: SessionClock,
    supplier: "FrameSupplier | None" = None,
    speed: "ReplaySpeed" = "fast",
) -> FrameSource:
    """設定から適切な `FrameSource` アダプタを構築する、唯一の生成口。

    design.md の Service Interface は `open_source(settings, metrics) ->
    FrameSource` という2引数の擬似コードを示すが、実装済みの3アダプタ
    （`SimulatedSource`（タスク 3.2）・`RecordedSource`（タスク 4.5）・
    `BaseFrameSource`（タスク 3.1））のコンストラクタはいずれも
    `RuntimeSettings` / `CaptureMetrics` だけからは組み立てられない追加の
    キーワード専用引数を要求する（各アダプタのモジュール docstring
    「実装判断2」「構築時の追加引数」参照）。本関数はその事実を踏まえ、
    design.md の2引数シグネチャをキーワード専用引数で拡張する
    （呼び出し側から見た「唯一の生成口」という性質そのものは変わらない
    ——`SimulatedSource`/`RecordedSource`/`RealSenseSource` を直接構築する
    経路をこの関数の外に作らないことが本関数の核心であり、引数の数は
    その核心ではない）:

    - **`clock: SessionClock`（必須）**: `BaseFrameSource.__init__` が要求する
      が、`CaptureMetrics` は自分の `SessionClock` を公開しない
      （`metrics.py` モジュール docstring 参照）。呼び出し側は `metrics` の
      構築に使ったものと**同一インスタンス**を渡すこと（`BaseFrameSource`
      の Precondition）。この点は本関数も検証しない
      （`CaptureFrame`/`SessionRecorder` 同様、Precondition の検証は
      呼び出し側の責務という本 Spec 全体の一貫した方針を踏襲する）。
    - **`supplier: FrameSupplier | None`（`SourceKind.SIMULATED` のときのみ
      必須）**: 合成フレームを供給する差し替え口そのものであり、
      `open_source()` はこれを合成できない（投擲物理・ノイズ生成は本 Spec
      の責務外。要件 4.5）。呼び出し側（テスト、または将来の
      `trajectory-simulator`）が用意する。未指定のまま `settings.source ==
      SourceKind.SIMULATED` を渡すと `SensingConfigError` を送出する
      （「呼び出し方の誤り」区分。`errors.py` 参照）。
    - **`speed: ReplaySpeed`（`SourceKind.RECORDED` のときのみ意味を持つ。
      既定 `"fast"`）**: `RecordedSource` が既に提供する再生速度の選択
      （要件 6.5）を、`open_source()` 経由でも選べるようにするための
      パラメータ。これを素通しできないと、CLI（タスク 8.1）が実時間再生を
      使いたい場合に `open_source()` を迂回して `RecordedSource` を直接
      構築せざるを得なくなり、「唯一の生成口」の原則（design.md
      Integration「`open_source()` が唯一の生成口。CLI も bench も直接
      アダプタを構築しない」）が崩れる。

    `SourceKind.RECORDED` の場合、`reader: SessionReader` は
    `settings.session_path`（design.md/タスク 1.6 の定義どおり「再生対象の
    セッションディレクトリ」そのもの。`root + session_id` の組ではない）
    から自己完結で構築できる——`RuntimeSettings` 単独でこの分岐は完結する。

    `SourceKind.LIVE` の場合、`RealSenseSource`（タスク 6.1）はまだ存在
    しない。本関数はこの分岐の中でだけ `sensing_foundation.sources.realsense`
    を**遅延 import**する（`sources/realsense.py` 自身が `pyrealsense2` を
    関数内で遅延 import する設計、design.md 依存方向表の注記と同じ流儀）。
    これにより:

    1. `settings.source` が `LIVE` 以外である限り、`open_source()` を含む
       `source.py` の import・呼び出しは SDK・実機は疎か
       `sources/realsense.py` というファイルの存在にすら依存しない。
    2. タスク 6.1 が `sources/realsense.py` と `RealSenseSource` を実装した
       時点で、この分岐は `ModuleNotFoundError` ではなく実際の構築へ自然に
       切り替わる——`open_source()` 自体を書き換える必要はない
       （tasks.md 4.6「実機アダプタは同テストの対象に含める形にしておき、
       実行はタスク 9.3 で行う」への対応。`RealSenseSource` の正確な
       コンストラクタ引数はタスク 6.1 が決めるため、下記の呼び出しは
       タスク 6.1 が実装と合わせて調整してよい暫定形である）。

    **`source.py`（レイヤー5）が本来 import してよい対象（design.md
    Dependency Direction 表: レイヤー0〜4のみ）を超えて `sources.simulated`
    （レイヤー6）・`sources.recorded`（レイヤー7）・`recording.reader`
    （レイヤー5、同レイヤーの兄弟モジュール）を参照する、という一見した
    レイヤー逸脱の扱い**: design.md の Directory Structure（`source.py` の
    行コメントが「`FrameSource` プロトコル / `BaseFrameSource` / `open_source`」
    と明記する）・Components 表の `FrameSource / BaseFrameSource` 行の
    Contract（Service Interface の擬似コードが `open_source` を同じコード
    ブロックに置く）・`__init__.py` の再エクスポート方針（`open_source` は
    `source.py` からの再エクスポートとして `__all__` に現れる）はいずれも
    `open_source()` の定義位置を `source.py` に置くことを一貫して示す一方、
    Dependency Direction 表はレイヤー5（`source`）の import 対象をレイヤー
    0〜4に限定しており、両者は**文字どおりには両立しない**（`open_source()`
    は構造上、より高いレイヤーのアダプタを構築せざるを得ないため）。
    本実装はこの矛盾を「**モジュールの実行時 import グラフ**（レイヤー制約が
    実際に守ろうとしているもの——ある層を import しただけで、より高い層や
    SDK 依存が芋づる式に読み込まれないこと）と、**関数本体の中でだけ発生する
    遅延 import**（呼ばれて初めて、必要な分岐のぶんだけ発生する）は別物」
    という解釈で解消する: `source.py` を import した時点、または
    `open_source()` を呼ばずに `BaseFrameSource` だけを使う時点では、
    `sources.simulated` / `sources.recorded` / `recording.reader` のいずれも
    実行時には一切 import されない（`TYPE_CHECKING` 配下の型限定 import を
    除く。モジュール先頭の import 文を参照）。これは design.md がレイヤー6の
    `realsense.py` に対して既に認めている「関数内遅延 import」という前例
    （Dependency Direction 表の「`realsense` のみ `pyrealsense2` を関数内で
    遅延 import」という注記）を、`open_source()` 自身にも一貫して適用した
    ものである。

    Preconditions:
        `settings` は `RuntimeSettings.resolve()` を経て構築されたことが
        望ましい（`session_path` 必須チェックなど）。直接構築した
        `RuntimeSettings` を渡す場合、本関数は `SourceKind.RECORDED` かつ
        `session_path is None` のケースのみ独自に再検証する
        （`resolve()` の検証を経ない呼び出しを想定した安全網）。
    Postconditions:
        返る `FrameSource` は `start()` → `frames()` → `stop()` という
        共通契約のみで下流から扱える（要件 4.1, 4.2）。呼び出し側は
        `SimulatedSource` / `RecordedSource` / `RealSenseSource` という
        具象型を一切知らずに済む。
    Invariants:
        `settings.source == SourceKind.SIMULATED` かつ `settings.source ==
        SourceKind.RECORDED` が同時に成り立つことはない（`SourceKind` は
        単一値の enum）ため、分岐は排他的である。

    Raises:
        SensingConfigError: `SourceKind.SIMULATED` なのに `supplier` が
            `None`、または `SourceKind.RECORDED` なのに `settings.session_path`
            が `None`（いずれも「呼び出し方の誤り」区分）。
    """
    if settings.source == SourceKind.SIMULATED:
        if supplier is None:
            raise SensingConfigError(
                "source が SIMULATED のとき supplier は必須である。"
                "合成フレームを供給する FrameSupplier（`index -> depth 配列"
                "または None`）を open_source(..., supplier=...) として渡すこと。"
                "open_source() 自身は投擲物理・ノイズ生成を持たないため、"
                "供給関数を合成できない（要件 4.5）。"
            )

        from sensing_foundation.sources.simulated import SimulatedSource

        profile = _build_simulated_profile(settings.capture)
        return SimulatedSource(
            supplier,
            profile,
            metrics,
            settings.capture.fps,
            clock=clock,
            capture_config=settings.capture,
        )

    if settings.source == SourceKind.RECORDED:
        if settings.session_path is None:
            raise SensingConfigError(
                "source が RECORDED（再生）のとき session_path は必須である。"
                "再生対象のセッションディレクトリを指定せよ"
                "（RuntimeSettings.resolve() 経由ならここに到達する前に拒否"
                "されるはずだが、resolve() を経ずに直接構築した "
                "RuntimeSettings はこの検証を経ないため、ここでも同じ検証を "
                "行う）。"
            )

        from sensing_foundation.recording.reader import SessionReader
        from sensing_foundation.sources.recorded import RecordedSource

        reader = SessionReader(settings.session_path)
        return RecordedSource(
            reader,
            metrics,
            clock=clock,
            capture_config=settings.capture,
            speed=speed,
        )

    if settings.source == SourceKind.LIVE:
        # タスク 6.1 未実装。遅延 import の結果、SDK・実機の無い環境では
        # ここで ModuleNotFoundError が送出される（意図的。関数 docstring
        # 「LIVE の場合」参照）。
        from sensing_foundation.sources.realsense import RealSenseSource

        return RealSenseSource(settings.capture, metrics, clock=clock)

    raise SensingConfigError(
        f"未知の入力元種別のため入力元を構築できない: {settings.source!r}"
    )
