"""capture 区間の計測点を実装する（design.md「L3-L4: 観測基盤 / CaptureMetrics」）。

`CaptureMetrics` は capture 区間（1フレームの取得〜下流への受け渡し）の
時間計測値・カウンタ・リソース計測値を1箇所に集め、`Logger`（タスク 2.1）へ
送る（要件 9.1, 9.4）。**「区間」という概念の定義はこのモジュール1箇所に
集約する。** 下流 Spec（`world-frame-calibration` / `flying-object-tracking` /
`m1-prediction-validation`）が `detect` / `track` / `predict` のような自分の
区間を計測したいときは、この時間計測ロジックを再実装するのではなく、
`Logger.timed()`（タスク 2.1 で実装済み。区間の開始〜終了を計測し、
`duration_ms` を添えて1件送出する fire-and-forget コンテキストマネージャ）を
そのまま使う（要件 8.9 / 9.4）。本モジュールが持つのは
「capture 区間という**特定の**区間の計測点」であり、汎用の区間計測機構
そのものは `obslog.Logger.timed()` が既に提供している。

**このタスクのスコープ**: `frame()` は、待機・ドレイン・受け渡しの各時間を
**測る**のではなく、既に測り終えた `FrameTiming` を**受け取って**カウンタ更新と
ログ送出を行う。実際に `SessionClock.now_ms()` を複数回呼んで
wait/drain/handoff の時間を組み立てる処理は `BaseFrameSource`（タスク 3.1）の
責務であり、本モジュールの責務ではない（design.md の取得ループのシーケンス図は
`BaseFrameSource` と `CaptureMetrics` の間の会話を概念的に示したものであり、
本モジュールの公開インタフェースは `frame(timing, frame)` という単一の
エントリポイントに集約される）。

**目標値の充足判定は行わない**（要件 9.5）。本モジュールが提供するのは
計測値そのものであり、「これは目標を満たしているか」を判定する `bool` を
一切返さない・ログにも書かない。
"""

from __future__ import annotations

from dataclasses import dataclass

from sensing_foundation.obslog import Logger
from sensing_foundation.sysstat import Sysstat
from sensing_foundation.timebase import SessionClock
from sensing_foundation.types import CaptureFrame, CaptureStats, TimestampDomain

__all__ = ["CaptureMetrics", "FrameTiming"]

#: capture 区間のログイベントで使う stage 名。`obslog.RESERVED_STAGES` の
#: 予約分の1つ（`capture`）と一致する。
_STAGE = "capture"


@dataclass(frozen=True, slots=True)
class FrameTiming:
    """1フレーム分の、既に計測済みの区間内訳（design.md「CaptureMetrics」）。

    `BaseFrameSource`（タスク 3.1）が組み立てて `CaptureMetrics.frame()` へ
    渡す。本モジュール自身はこれらの値を**測らない**。

    Attributes:
        wait_ms: 取得待機に入ってから1枚受け取るまでの時間（ホスト閉区間）。
        drain_ms: ドレイン（滞留分を捨てて最新へ追いつく処理）に要した時間。
        handoff_ms: 共通表現（`CaptureFrame`）の構築から下流へ渡すまでの時間。
        total_ms: `wait_ms + drain_ms + handoff_ms`。
    """

    wait_ms: float
    drain_ms: float
    handoff_ms: float
    total_ms: float


class CaptureMetrics:
    """capture 区間の時間・カウンタ・リソースを1箇所に集め、ログへ送る。

    Preconditions: なし。
    Postconditions:
        - `frame()` は呼ばれるたびに `frames_yielded` を1増やし、渡された
          `CaptureFrame.dropped_before` / `gap_before` を `frames_dropped` /
          `frames_missing` へ加算する（要件 2.2, 2.3）。
        - `snapshot_resources()` は前回サンプルから `resource_interval_ms`
          が経過していない限り no-op である（`Sysstat.sample()` を呼ばず、
          ログも送らない）。経過していれば1件だけサンプルして送る
          （要件 9.3。design.md「Risks: /proc 読み取りが支配的になる」対策）。
        - `capture_latency_ms` は `frame.timestamp_domain == GLOBAL_TIME` かつ
          値が `None` でないときのみログの `data` にキーとして現れる。それ
          以外は**キーごと省く**（`None` を値として送らない）。これにより
          「欠測」と「0」を区別する（要件 9.1 / design.md Validation）。
        - `counters()` は目標値の充足を一切判定しない（要件 9.5）。

    Invariants:
        `_start_ms` は構築時に1度だけ `clock.now_ms()` から取得され、以後
        変わらない。`counters()` の `duration_ms` / `measured_fps` はこの
        「`CaptureMetrics` が構築された時刻」を起点に計算する
        （「最初の `frame()` 呼び出し」ではない — 呼び出し側が取得開始前に
        `CaptureMetrics` を構築してから `start()` するまでの時間も
        `duration_ms` に含まれる、という選択を明示するための設計判断。
        `open_source()`（タスク 3.1）が `CaptureMetrics` を `start()` の
        直前に構築すれば、両者はほぼ一致する）。
    """

    def __init__(
        self,
        logger: Logger,
        clock: SessionClock,
        sysstat: Sysstat | None,
        resource_interval_ms: float = 500.0,
    ) -> None:
        self._logger = logger
        self._clock = clock
        self._sysstat = sysstat
        self._resource_interval_ms = resource_interval_ms

        self._start_ms = clock.now_ms()
        self._last_resource_sample_ms: float | None = None

        self._frames_yielded = 0
        self._frames_dropped = 0
        self._frames_missing = 0
        self._acquire_errors = 0

    def frame(self, timing: FrameTiming, frame: CaptureFrame) -> None:
        """1フレーム分の計測値を取り込み、カウンタを更新してログへ送る（要件 2.2, 2.3, 9.1）。

        `frame` は取得に**成功した**フレームのみを表す（`CaptureFrame` は
        成功時にしか構築されない）。取得失敗は `acquire_error()` で別途
        記録する。
        """
        self._frames_yielded += 1
        self._frames_dropped += frame.dropped_before
        self._frames_missing += frame.gap_before

        data: dict[str, object] = {
            "index": frame.index,
            "seq": frame.seq,
            "wait_ms": timing.wait_ms,
            "drain_ms": timing.drain_ms,
            "handoff_ms": timing.handoff_ms,
            "total_ms": timing.total_ms,
            "dropped_before": frame.dropped_before,
            "gap_before": frame.gap_before,
        }
        # capture_latency_ms は GLOBAL_TIME ドメインで、かつ実際に数値が
        # あるときのみキーを足す。それ以外はキーを一切足さない（欠測と 0 の
        # 区別。design.md Validation / 要件 9.1）。
        if (
            frame.timestamp_domain == TimestampDomain.GLOBAL_TIME
            and frame.capture_latency_ms is not None
        ):
            data["capture_latency_ms"] = frame.capture_latency_ms

        self._logger.emit(_STAGE, "frame", **data)

    def acquire_error(self) -> None:
        """フレーム取得の失敗を1件記録する（要件 2.7, 9.2）。

        `CaptureFrame` は成功時にしか存在しないため、取得失敗は `frame()`
        を経由できない。`BaseFrameSource`（タスク 3.1）が `on_acquire_error`
        設定に従って継続する際、この専用メソッドを呼んで失敗件数を数え、
        ログへ送ることを想定する。design.md の Components 表には
        メソッド名として明記されていない、本タスクでの実装判断による追加
        だが、要件 2.7（取得失敗の記録）・9.2（実測値としての破棄/欠落/
        エラー件数の保持）を満たすために必要な最小限の拡張である。
        """
        self._acquire_errors += 1
        self._logger.emit(_STAGE, "acquire_error")

    def counters(self) -> CaptureStats:
        """現時点までの取得統計の要約を返す（要件 2.8, 9.2）。

        目標値の充足判定は行わない（要件 9.5）。`duration_ms` /
        `measured_fps` は `CaptureMetrics` の構築時刻を起点に算出する
        （クラス docstring の Invariants を参照）。
        """
        duration_ms = self._clock.now_ms() - self._start_ms
        if duration_ms > 0.0:
            measured_fps = self._frames_yielded / (duration_ms / 1000.0)
        else:
            measured_fps = 0.0

        return CaptureStats(
            frames_yielded=self._frames_yielded,
            frames_dropped=self._frames_dropped,
            frames_missing=self._frames_missing,
            duration_ms=duration_ms,
            measured_fps=measured_fps,
            acquire_errors=self._acquire_errors,
        )

    def snapshot_resources(self) -> None:
        """前回サンプルから `resource_interval_ms` が経過していれば1件サンプルして送る（要件 9.3）。

        `sysstat` が `None`（リソース計測を行わない構成）の場合は常に
        no-op。経過していない場合も no-op（`Sysstat.sample()` を呼ばない
        —— design.md「Risks: 毎フレーム行うと /proc 読み取りが支配的に
        なる」を避けるための間隔制御そのものがこのメソッドの責務）。

        初回呼び出しは「前回サンプルが存在しない」ため、無条件にサンプルする
        （間隔が既に経過している、とみなす）。
        """
        if self._sysstat is None:
            return

        now_ms = self._clock.now_ms()
        if (
            self._last_resource_sample_ms is not None
            and now_ms - self._last_resource_sample_ms < self._resource_interval_ms
        ):
            return

        sample = self._sysstat.sample()
        self._last_resource_sample_ms = now_ms
        self._logger.emit(
            _STAGE,
            "resources",
            cpu_percent=sample.cpu_percent,
            process_rss_bytes=sample.process_rss_bytes,
            system_available_bytes=sample.system_available_bytes,
            system_total_bytes=sample.system_total_bytes,
        )
