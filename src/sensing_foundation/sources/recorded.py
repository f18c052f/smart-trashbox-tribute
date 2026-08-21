"""記録の再生アダプタを実装する（design.md「L6-L7: アダプタ・保存・診断 / RecordedSource」）。

`RecordedSource` は `SessionReader`（タスク 4.4）を包み、live と**同じ契約**
（`BaseFrameSource` 経由の `FrameSource`）でフレーム系列を返す（要件 4.1,
6.1）。`SessionReader` が索引とブロブから再構成する `CaptureFrame` を
`RawFrame` へ変換して `BaseFrameSource._acquire()` の期待する形へ渡すだけの
**薄い配線層**であり、`SimulatedSource`（タスク 3.2）と同じ立ち位置を取る。

**依存方向（design.md 依存方向表）**: `sources/recorded` はレイヤー7に置く
（`sources/simulated` はレイヤー6）。これは本モジュールが `recording.reader`
（レイヤー5）に依存するためであり、tasks.md 4.5 の `_Boundary:_` 節が
明記する区別である。SDK・実機は一切参照しない（要件 4.4, 6.3）。

**profile / intrinsics は live と同じ経路で取得する（要件 6.4）**:
`RecordedSource` 自身は `profile` プロパティを再定義しない。
`BaseFrameSource.__init__` へ `profile=reader.profile` をそのまま渡し、
以後は `FrameSource.profile` プロパティ（`BaseFrameSource` が実装する
共通経路）がこれを返す。`reader.profile` は `SessionReader` が
`manifest.json` から復元した `StreamProfile`（`intrinsics` を含む）であり、
再生専用の別経路（サイドチャネル）を新設しない。

**`speed` 設定（要件 6.5）**: `"fast"`（既定、待たない）と `"realtime"`
（記録時の `t_capture_ms` 差分だけ待つ）を選べる。**どちらを選んでも
`frames()` が返す系列（`seq` / `depth` / `device_timestamp_ms` /
`timestamp_domain` / `capture_latency_ms` / `dropped_before` /
`gap_before` の内容と順序）は同一**である（要件 6.1, 6.5）。

**`t_capture_ms` の扱い（実装判断・設計上の要点）**: `RawFrame`
（`source.py`、タスク 3.1）には `t_capture_ms` を運ぶフィールドが無く、
`BaseFrameSource.frames()` は常に**自分の** `SessionClock.now_ms()` から
`CaptureFrame.t_capture_ms` を新たに算出する（`source.py` を実装として
確認済み。`_acquire()` の戻り値からの素通しは構造的に不可能）。したがって
再生で得る `t_capture_ms` は「記録時のセッション時刻」ではなく「**この
再生セッションの** `SessionClock` を起点とした経過時刻」になる——これは
要件 6.1「記録時と**同じフレーム系列を同じ順序で**下流へ渡す」を、
`seq` / `depth` などの**内容と順序**の一致として満たすことと矛盾しない
（別セッションの絶対時刻を一致させることは、そもそも `t_capture_ms` の
定義（セッション単調時計起点）が要求していない）。

一方、`speed="realtime"` の**ペーシング**（フレーム間でどれだけ待つか）は
記録時の実際の間隔を再現する必要がある（要件 6.5）。このため、待機時間の
算出には `self._reader.read(i).t_capture_ms` と `read(i-1).t_capture_ms`
の差分を**アダプタ内部だけで**使う——最終的に `frames()` が yield する
`CaptureFrame.t_capture_ms` には反映されない、`_acquire()` 内部限定の
プライベートな値として消費する。この結果、`speed` の値によらず yield
される内容（`seq`/`depth`/...）と順序は完全に一致する一方、`t_capture_ms`
そのものは（両モードとも再生セッションのクロック起点で新たに算出される
ため）モード間でも記録時とも数値的には異なりうる。これは意図した設計判断
であり、`speed="fast"` と `speed="realtime"` の等価性を検証するテストは
`t_capture_ms` を比較対象から除外する（`tests/sensing_foundation/
test_recorded_source.py` の `_assert_frames_equal_ignoring_timing()`
参照）。

**ドレインの二重防御**（`source.py` Implementation Notes「タスク3.1」・
design.md「Risks: ドレインを継承しない。`_drain_latest()` は常に
`(None, 0)` を返す（要件 6.7）」に従う）:

1. `super().__init__(...)` へ渡す `capture_config` は、呼び出し側が何を
   渡しても `drain_enabled=False` に固定して渡す。
2. `_drain_latest()` 自体も、呼ばれることが無い前提に依存せず、常に
   `(None, 0)` を返す。

**構築時の追加引数（design.md 未記載の実装判断）**: design.md の
`RecordedSource` 節は Contract のシグネチャを明示していない
（`SimulatedSource` と異なり pseudocode が無い）。`SimulatedSource`
（実装判断2）と同じ理由（`BaseFrameSource.__init__` が要求する
`clock: SessionClock` を `CaptureMetrics` から取り出せない）により、
本モジュールも `clock` をキーワード専用の必須引数として追加する。加えて
`speed` の実時間ペーシングをテストで決定的に検証できるよう、実際に眠る
呼び出し可能オブジェクトを `sleep: Callable[[float], None] = time.sleep`
としてキーワード専用・差し替え可能にする（テストの継ぎ目。`obslog.py` /
`source.py` が既に採用している「テスト用に差し替え可能な依存を明示的な
引数として受ける」流儀を踏襲する）。**タスク4.6（`open_source`）は
この拡張シグネチャ（`reader, metrics, *, clock, capture_config=None,
speed="fast", sleep=time.sleep`）を把握して呼び出すこと。**

**終端シグナル**: `self._next_index >= len(self._reader)` になったら
`_acquire()` は `None` を返さず `StopIteration` を送出する（`source.py`
が定める「取得失敗」と「正常な終端」の区別。`SessionReader.iter_frames()`
自身の generator 終了（通常の Python 反復終了）を、`_acquire()` の
呼び出し規約へ翻訳する）。`iter_frames()` を直接使わず `read(i)` を内部
カウンタで呼ぶ（`SessionReader.read(i)` は内部状態を持たないため、
`RecordedSource` 側の `_next_index` だけが唯一のカーソルになる。
`reader.py` docstring「`read(i)` は内部状態を持たない」と整合する）。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace
from typing import Literal

from sensing_foundation.config import CaptureConfig
from sensing_foundation.errors import SensingConfigError
from sensing_foundation.metrics import CaptureMetrics
from sensing_foundation.recording.reader import SessionReader
from sensing_foundation.source import BaseFrameSource, RawFrame
from sensing_foundation.timebase import SessionClock
from sensing_foundation.types import SourceKind

__all__ = ["RecordedSource", "ReplaySpeed"]

#: design.md「Validation」が定める再生速度の選択肢。
ReplaySpeed = Literal["fast", "realtime"]

_VALID_SPEEDS: tuple[ReplaySpeed, ...] = ("fast", "realtime")


class RecordedSource(BaseFrameSource):
    """記録済みセッションを live と同じ契約で再生する `FrameSource` アダプタ。

    `SessionReader` を包むだけの薄い配線層であり、投擲物理・SDK・実機の
    いずれも参照しない（要件 4.4, 6.3）。

    Preconditions:
        `reader` は構築済みの `SessionReader`（未知の形式版・破損した
        索引は `SessionReader.__init__` の時点で既に拒否されている）。
    Postconditions:
        `frames()` が返す枚数は `len(reader)` と一致する。`speed` に
        関わらず、yield される各 `CaptureFrame` の `seq` / `depth` /
        `device_timestamp_ms` / `timestamp_domain` / `capture_latency_ms`
        / `dropped_before` / `gap_before` は同一系列・同一順序になる
        （`t_capture_ms` を除く。モジュール docstring参照）。
    Invariants:
        `drain_enabled` は常に偽として扱われる（モジュール docstring
        「ドレインの二重防御」）。
    """

    def __init__(
        self,
        reader: SessionReader,
        metrics: CaptureMetrics,
        *,
        clock: SessionClock,
        capture_config: CaptureConfig | None = None,
        speed: ReplaySpeed = "fast",
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """`RecordedSource` を組み立てる。

        Args:
            reader: 再生対象のセッションを既に開いた `SessionReader`。
                `profile` / `intrinsics` の取得元（要件 6.4）。
            metrics: 計測点の送出先。
            clock: `metrics` と**同一インスタンス**の `SessionClock`
                （design.md の Contract 表記には無いキーワード専用の追加
                引数。モジュール docstring「構築時の追加引数」参照）。
            capture_config: `on_acquire_error` 等の取得元。`None`（既定）
                なら `CaptureConfig()` の既定値を使う。`drain_enabled` は
                ここで何を渡しても無視され、常に `False` に固定される
                （モジュール docstring「ドレインの二重防御」）。
            speed: `"fast"`（既定、待たない）または `"realtime"`（記録時
                の `t_capture_ms` 差分だけ待つ）。既定は最速（design.md
                「実時間再生と最速再生を選べる。既定は最速」）。
            sleep: `speed="realtime"` のときに待機へ使う呼び出し可能
                オブジェクト。既定は `time.sleep`。テストで決定的に検証
                できるよう差し替え可能にしてある（モジュール docstring
                「構築時の追加引数」参照）。

        Raises:
            SensingConfigError: `speed` が `"fast"`/`"realtime"` 以外の
                場合（呼び出し方の誤り）。
        """
        if speed not in _VALID_SPEEDS:
            raise SensingConfigError(
                f"speed は {_VALID_SPEEDS} のいずれかである必要がある: {speed!r}"
            )

        base_config = capture_config if capture_config is not None else CaptureConfig()
        forced_config = replace(base_config, drain_enabled=False)
        super().__init__(
            kind=SourceKind.RECORDED,
            profile=reader.profile,
            metrics=metrics,
            clock=clock,
            capture_config=forced_config,
        )
        self._reader = reader
        self._speed = speed
        self._sleep = sleep
        # 注意: `BaseFrameSource` は既に `self._next_index`（出力する
        # `CaptureFrame.index` 用のカーソル）を持つ。同名にすると同じ
        # 属性を取り合って二重にインクリメントされ、フレームを飛ばして
        # 読む致命的なバグになる（実装時に実測で踏んだ）。読み出し位置用の
        # カーソルは明確に別名にする。
        self._next_read_index = 0
        self._prev_recorded_t_capture_ms: float | None = None

    def _acquire(self, timeout_ms: int) -> RawFrame | None:
        """索引位置 `self._next_read_index` のフレームを読み、`RawFrame` へ変換する。

        `speed="realtime"` の場合、直前に読んだフレームとの記録時
        `t_capture_ms` 差分だけ `self._sleep()` で待ってから返す
        （モジュール docstring「`t_capture_ms` の扱い」参照。この待機は
        `_acquire()` 内部だけで完結し、返す `RawFrame` には現れない）。

        Raises:
            StopIteration: `len(self._reader)` まで読み終えた（正常な終端）。
        """
        del timeout_ms  # SessionReader は同期的に返す（供給側にタイムアウトの概念が無い）。

        if self._next_read_index >= len(self._reader):
            raise StopIteration

        index = self._next_read_index
        self._next_read_index += 1

        recorded_frame = self._reader.read(index)

        if self._speed == "realtime" and self._prev_recorded_t_capture_ms is not None:
            delta_ms = recorded_frame.t_capture_ms - self._prev_recorded_t_capture_ms
            if delta_ms > 0:
                self._sleep(delta_ms / 1000.0)
        self._prev_recorded_t_capture_ms = recorded_frame.t_capture_ms

        return RawFrame(
            seq=recorded_frame.seq,
            depth=recorded_frame.depth,
            device_timestamp_ms=recorded_frame.device_timestamp_ms,
            timestamp_domain=recorded_frame.timestamp_domain,
            capture_latency_ms=recorded_frame.capture_latency_ms,
        )

    def _drain_latest(self) -> tuple[RawFrame | None, int]:
        """常に `(None, 0)` を返す（ドレインの二重防御の一方。要件 6.7）。"""
        return (None, 0)
