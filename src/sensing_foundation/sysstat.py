"""`/proc` から資源計測値を読む `Sysstat` を定義する（design.md「Sysstat」）。

追加の依存を持たず（`tech.md` の L0 レイヤ規約: `errors` / `timebase` / `sysstat`
は標準ライブラリのみで、互いに import しない）、`/proc/stat` （システム全体の
CPU 時間）・`/proc/self/stat` （このプロセスの CPU 時間）・`/proc/self/status`
（このプロセスの RSS）・`/proc/meminfo`（搭載 RAM 総量・利用可能量）から
資源計測値を読む（要件 9.3・1.1）。

**`cpu_percent` の定義**: `ResourceSample.cpu_percent` は「このプロセスが
消費した CPU 時間が、システム全体の CPU 時間に占める割合」を表す。
前回サンプルとの差分（`/proc/stat` の合計 jiffies の差分を分母、
`/proc/self/stat` の utime+stime の差分を分子）から算出する
（design.md の実装メモが `/proc/stat`＝システム CPU、`/proc/self/stat`＝
プロセス CPU の双方を読む、としているのはこの割合計算のためである）。
分子・分母がいずれも「前回サンプルとの差分」であるため、初回のサンプルには
前回値が存在せず、`cpu_percent` は欠測（`None`）として返す。

**`/proc` が読めない環境（Linux 以外・制限されたコンテナ等）では、
例外を送出せずすべてのフィールドを `None`（欠測）として返す。** 要件 9.3 は
「計測値として残せる」ことを求めており、欠測は欠測として値で表現する
（`errors.py` が定義する「観測された事実は例外にしない」という区分を
本モジュールも踏襲する。ただし本モジュールは L0 レイヤであり `errors` を
import しない — 自身の try/except で完結させる）。

**サンプリング間隔の制御は本モジュールの責務ではない。** CPU 使用率は
差分ベースであるため間隔が短すぎると意味を持たないが、間隔を空けて
`sample()` を呼び出すかどうかは呼び出し側（`CaptureMetrics`、タスク 2.2）
の責務であり、本モジュールはいつ呼ばれても、渡された2点間の差分を
そのまま計算するだけである。

`system_total_bytes`（搭載 RAM 総量）は CPU の差分を必要としないため、
構築直後の最初の `sample()` 呼び出しでも単独で取得できる。これは
リングバッファの上限（`RuntimeSettings.recording.max_ring_bytes` の既定値）
の根拠として使われることを想定している。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# `/proc` の参照先。テストでは偽の `/proc` ライクなディレクトリへ
# monkeypatch で差し替えられるよう、モジュールレベルの変数として持つ
# （公開インタフェース `Sysstat.__init__(self) -> None` は変更しない、
# 実装上の差し替え点＝テスト用シームに過ぎない）。
_PROC_ROOT = Path("/proc")


@dataclass(frozen=True, slots=True)
class ResourceSample:
    """1回の `Sysstat.sample()` 呼び出しで得られる資源計測値。

    いずれのフィールドも欠測を `None` で表現する。欠測は異常ではなく、
    `/proc` が読めない・初回サンプルで差分が取れない、といった
    「観測された事実」である。
    """

    cpu_percent: float | None
    """前回サンプルとの差分から算出したプロセスの CPU 使用率（0-100）。

    初回サンプルは前回値が存在しないため欠測（`None`）。
    """

    process_rss_bytes: int | None
    """このプロセスの常駐メモリ量（バイト）。"""

    system_available_bytes: int | None
    """システム全体で利用可能なメモリ量（バイト）。"""

    system_total_bytes: int | None
    """システムに搭載された RAM 総量（バイト）。CPU の差分を必要とせず、
    初回の `sample()` 呼び出しでも単独で取得できる。
    """


_MISSING_SAMPLE = ResourceSample(
    cpu_percent=None,
    process_rss_bytes=None,
    system_available_bytes=None,
    system_total_bytes=None,
)


class Sysstat:
    """`/proc` から CPU 使用率・メモリ使用量・搭載 RAM を読む。

    Preconditions: なし（構築時に `/proc` の存在は検証しない）。
    Postconditions: `sample()` は例外を送出しない。`/proc` が読めない場合は
        すべて欠測の `ResourceSample` を返す。
    Invariants: `cpu_percent` は前回の `sample()` 呼び出しとの差分でのみ
        算出できる。差分の起点となる前回値が無い（初回、または直前の
        呼び出しが読み取りに失敗した）場合は `None` を返す。
    """

    def __init__(self) -> None:
        self._prev_system_total_jiffies: int | None = None
        self._prev_process_jiffies: int | None = None

    def sample(self) -> ResourceSample:
        """現在の資源計測値を1件返す。例外は送出しない。

        `/proc` の読み取り・解析に失敗した場合（`/proc` が存在しない、
        権限が無い、内容が想定した形式でない、等）はすべて欠測の
        `ResourceSample` を返し、次回の `sample()` を「初回」として
        扱えるよう内部の前回値も破棄する（古い前回値を使って不正確な
        差分を計算しないため）。
        """
        try:
            system_total_jiffies = _read_system_total_jiffies()
            process_jiffies = _read_process_jiffies()
            process_rss_bytes = _read_process_rss_bytes()
            system_total_bytes, system_available_bytes = _read_meminfo()
        except (OSError, ValueError, IndexError):
            # 読み取りの途中で失敗した場合は、部分的な値を返さずすべて
            # 欠測にする。次回の呼び出しが誤った差分を計算しないよう、
            # 前回値も破棄する。
            self._prev_system_total_jiffies = None
            self._prev_process_jiffies = None
            return _MISSING_SAMPLE

        cpu_percent = self._compute_cpu_percent(
            system_total_jiffies=system_total_jiffies,
            process_jiffies=process_jiffies,
        )
        self._prev_system_total_jiffies = system_total_jiffies
        self._prev_process_jiffies = process_jiffies

        return ResourceSample(
            cpu_percent=cpu_percent,
            process_rss_bytes=process_rss_bytes,
            system_available_bytes=system_available_bytes,
            system_total_bytes=system_total_bytes,
        )

    def _compute_cpu_percent(
        self, *, system_total_jiffies: int, process_jiffies: int
    ) -> float | None:
        if self._prev_system_total_jiffies is None or self._prev_process_jiffies is None:
            return None

        system_delta = system_total_jiffies - self._prev_system_total_jiffies
        process_delta = process_jiffies - self._prev_process_jiffies

        if system_delta <= 0:
            # 差分が取れない（同一サンプル・時計の異常等）場合は 0 とする。
            return 0.0

        percent = (process_delta / system_delta) * 100.0
        return max(0.0, min(100.0, percent))

    @staticmethod
    def available() -> bool:
        """`/proc` が読めるかどうかを、例外を送出せずに返す。"""
        try:
            return _PROC_ROOT.joinpath("meminfo").is_file()
        except OSError:
            return False


def _read_system_total_jiffies() -> int:
    """`/proc/stat` の先頭行（`cpu ...`）の合計 jiffies を返す。"""
    content = _PROC_ROOT.joinpath("stat").read_text(encoding="utf-8")
    first_line = content.splitlines()[0]
    fields = first_line.split()
    if not fields or not fields[0].startswith("cpu"):
        raise ValueError(f"unexpected /proc/stat first line: {first_line!r}")
    return sum(int(v) for v in fields[1:])


def _read_process_jiffies() -> int:
    """`/proc/self/stat` の utime（14番目）+ stime（15番目）を返す。

    `comm`（2番目のフィールド）は括弧で囲まれ、空白や括弧そのものを
    含み得るため、最後の `)` の直後から空白区切りで数える
    （`man proc` の慣行に従う）。
    """
    content = _PROC_ROOT.joinpath("self", "stat").read_text(encoding="utf-8")
    closing_paren = content.rindex(")")
    rest = content[closing_paren + 1 :].split()
    # rest[0] はフィールド3（state）に対応する。utime=14, stime=15 なので
    # rest のインデックスはそれぞれ 14-3=11, 15-3=12。
    utime = int(rest[11])
    stime = int(rest[12])
    return utime + stime


def _read_process_rss_bytes() -> int:
    """`/proc/self/status` の `VmRSS:` 行（kB）をバイトへ換算して返す。"""
    content = _PROC_ROOT.joinpath("self", "status").read_text(encoding="utf-8")
    for line in content.splitlines():
        if line.startswith("VmRSS:"):
            kb = int(line.split()[1])
            return kb * 1024
    raise ValueError("VmRSS not found in /proc/self/status")


def _read_meminfo() -> tuple[int, int]:
    """`/proc/meminfo` から `(system_total_bytes, system_available_bytes)` を返す。"""
    content = _PROC_ROOT.joinpath("meminfo").read_text(encoding="utf-8")
    total_kb: int | None = None
    available_kb: int | None = None
    for line in content.splitlines():
        if line.startswith("MemTotal:"):
            total_kb = int(line.split()[1])
        elif line.startswith("MemAvailable:"):
            available_kb = int(line.split()[1])
    if total_kb is None or available_kb is None:
        raise ValueError("MemTotal/MemAvailable not found in /proc/meminfo")
    return total_kb * 1024, available_kb * 1024
