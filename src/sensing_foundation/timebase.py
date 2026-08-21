"""セッション単調時計（`SessionClock`）を定義する（design.md「SessionClock」）。

セッション開始時に単調時計（`time.perf_counter_ns`）と壁時計（`time.time_ns`）を
**1度だけ**対応付け（アンカ）、以降のフレーム時刻・ログ時刻はすべて
セッション開始からの経過 ms として表す（要件 3.4）。壁時計は起動時の
アンカ取得にのみ用い、経過時間の算出には一切用いない。これにより
NTP 補正や DST などによる壁時計の巻き戻しの影響を受けない、単一の
単調な時間基準をセッション内で共有できる（要件 8.10: ログの時刻と
フレームの取得時刻を同一の時間基準で表す）。

**セッションをまたぐ時刻比較は、必ず `to_wall_ms()` を経由すること。**
`now_ms()` が返す経過 ms は当該 `SessionClock` インスタンスのアンカを
基準にした相対値であり、異なるセッション（＝異なるアンカ）の経過 ms
同士を直接比較することはできない。事後解析で複数セッションの時刻を
突き合わせる場合は、`to_wall_ms()` で共通の壁時計基準（epoch ms）へ
変換してから比較する。

`SessionClock` は取得・記録・ロギングの間で**同一インスタンスを共有**
することを前提とする。共有を妨げる隠れたグローバル状態・シングルトンは
持たず、`now_ms()` の呼び出しは経過時間の読み取り以外の副作用を持たない。
"""

from __future__ import annotations

import time


class SessionClock:
    """セッション開始を原点とする単調時計。

    構築時に壁時計（epoch ms）と単調時計（perf_counter ns）を1度だけ
    対応付け、以降は `now_ms()` が単調時計のみに基づく経過 ms を返す。
    事後解析で壁時計へ戻す必要がある場合は `to_wall_ms()` を用いる。

    Preconditions: なし。
    Postconditions: `now_ms()` は単調非減少である。
    Invariants: `now_ms()` は `time.perf_counter_ns` のみに依存し、
        壁時計の巻き戻し（NTP 補正・DST 等）の影響を受けない。

    セッションをまたぐ時刻比較は `to_wall_ms()` を通すこと。
    """

    def __init__(self, session_id: str) -> None:
        # アンカは構築時に1度だけ取得する。以降どのプロパティ・メソッドも
        # time.time_ns() を再度呼ばない。
        self._session_id = session_id
        self._started_wall_ms = time.time_ns() / 1_000_000
        self._started_monotonic_ns = time.perf_counter_ns()

    @property
    def session_id(self) -> str:
        """このセッションを識別する文字列。

        記録ディレクトリ名・ログファイル名・Throw Record の対応付けで
        共通して使うことを想定する（`session_id` の生成自体は本クラスの
        責務ではない）。
        """
        return self._session_id

    @property
    def started_wall_ms(self) -> float:
        """セッション開始時の壁時計（epoch ms）。構築時に1度だけ取得したアンカ。"""
        return self._started_wall_ms

    @property
    def started_monotonic_ns(self) -> int:
        """セッション開始時の単調時計（`time.perf_counter_ns` の値）。"""
        return self._started_monotonic_ns

    def now_ms(self) -> float:
        """セッション開始からの経過 ms を返す。

        `time.perf_counter_ns` のみを用いて算出するため、壁時計の
        巻き戻しの影響を受けない。連続呼び出しは単調非減少である。
        """
        elapsed_ns = time.perf_counter_ns() - self._started_monotonic_ns
        return elapsed_ns / 1_000_000

    def to_wall_ms(self, t_ms: float) -> float:
        """`now_ms()` が返した経過 ms を壁時計（epoch ms）へ換算する。

        事後解析用の換算のみを行う純粋関数であり、新たな壁時計の読み取りは
        行わない（構築時に取得したアンカ `started_wall_ms` に `t_ms` を
        加算するだけである）。
        """
        return self._started_wall_ms + t_ms
