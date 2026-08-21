"""直近 N 秒分の `CaptureFrame` を保持するリングバッファ（design.md「FrameRingBuffer」、
要件 5.5 / 5.7）。

**存在理由（要件 5.7）**: 投擲は1秒級の事象であり、常時ディスクへ書き続ける
連続記録は必須ではない。取得ループの中で同期的にファイル I/O を行うと、
記録を有効にした場合とそうでない場合とでレイテンシが変わってしまい比較が
できなくなる（`SessionRecorder` docstring 参照）。`FrameRingBuffer` は
フレームを RAM 上に保持するだけの受動的な入れ物であり、実際のファイル
書き出しはトリガが引かれたときに `flush_to()` 経由で `SessionRecorder` へ
一括で渡す。これにより「記録処理が取得ループの継続を妨げない」（要件 5.7）
という構造が成り立つ（design.md「記録（リングバッファ＋トリガ保存）」の
フロー図: `CaptureFrame stream -> FrameRingBuffer -> (trigger) -> flush to
session directory -> SessionRecorder`）。

**保持する枚数（要件 5.5）**: `ceil(seconds * profile.fps)` 枚を超えて保持
しない。古いものから捨てる（FIFO）。実装は `collections.deque(maxlen=...)`
そのものであり、`maxlen` を超えた `append()` は最古の要素を自動的に
落とす。これ以外の破棄ロジックを自前で持たない。

**コピーしない（design.md Implementation Notes）**: `append()` は
`deque` への追加のみを行い、渡された `CaptureFrame` を複製しない。
`CaptureFrame` は `frozen=True` の値オブジェクトであり、`depth`
（numpy 配列）も構築時に `flags.writeable = False` が立つ読み取り専用
配列である（`sensing_foundation.types.CaptureFrame.__post_init__`、
タスク 1.5）。そのため、保持側・呼び出し側のどちらから見ても
`FrameRingBuffer` に置かれたフレームが後から書き換えられる経路は無く、
複製せずに参照を保持しても安全である。`flush_to()` も同様に、保持して
いる `CaptureFrame` インスタンスをそのまま `recorder.write()` へ渡す
（オブジェクト同一性 `is` が保たれる。テストで固定する）。

**`max_bytes` パラメータの扱い（設計上の読み方）**: design.md の
`FrameRingBuffer` 節は次のように書いている:

    Preconditions: `required_bytes(...) <= max_bytes`
    （超える設定は `RuntimeSettings` が起動時に拒否する）

文面上、この不変条件の**検証を行う主体は `RuntimeSettings.resolve()`**
（タスク 1.6、`sensing_foundation.config` に実装済み）であると明記されて
いる。実際 `config.py` の `_validate()` は `_required_ring_bytes()` と
`_resolve_ring_cap_bytes()`（`max_ring_bytes` 優先、無ければ
`installed_ram_bytes` の25%、どちらも無ければ検査しない）を使って
起動時に `SensingConfigError` を送出しており、この不変条件はそこで
既に強制されている。

したがって本クラスの `__init__` は **`max_bytes` を受け取って保持する
だけ**とし、それ自体を根拠に容量（`deque` の `maxlen`）を縮めたり、
超過を理由に例外を送出したりしない。保持容量は常に
`ceil(seconds * profile.fps)` のみで決まる。`max_bytes` は
`RuntimeSettings` 側が既に妥当性を確認した値をここへも伝搬できるように
コンストラクタが受理する呼び出し規約上の整合（design.md の型注釈が
`max_bytes` を要求している以上、シグネチャを合わせる）に留め、
Precondition の**検証そのものを二重実装しない**（`RuntimeSettings` と
`FrameRingBuffer` の2箇所で異なる基準の RAM チェックが生まれ、片方だけ
更新されて食い違う事故を避けるため）。この読み方はテスト
（`TestMaxBytesParameter`）で固定する: `max_bytes` に `required_bytes()`
を下回る値を渡してもコンストラクタは例外を送出せず、保持容量は
`seconds * profile.fps` から導かれる値のまま変わらない。

**`flush_to()` の `recorder` 引数の型（`SessionRecorder` 未実装への対処）**:
`SessionRecorder`（タスク 4.3）はまだ存在しないため、実体を import
できない。design.md「SessionRecorder」節はそのシグネチャを
`def write(self, frame: CaptureFrame) -> None: ...` と定めている
（`#### SessionRecorder` の Contracts）。本モジュールはこの1メソッドのみを
要求する構造的部分型 `_RecorderLike`（`typing.Protocol`）をローカルに
定義し、`flush_to()` の引数型として使う。`SessionRecorder` を import
しないことは依存方向の制約（`recording/*` は `source` を import しない、
design.md「Dependency Direction」直下の説明文）とも矛盾しない —
そもそも `SessionRecorder` 自体は `recording` パッケージ内の別モジュール
であり import 自体は許されるが、**未実装のため import できない**。
将来 `writer.py` に `SessionRecorder` が実装されたとき、`write(self,
frame: CaptureFrame) -> None` を持つ限り `_RecorderLike` を満たし、
`flush_to()` はそのまま動く（`Protocol` は構造的部分型であり、明示的な
継承を要求しない）。テストでは実体の代わりにこのメソッドだけを持つ
フェイクを使う。

**`flush_to()` は自動的に `clear()` しない**: design.md のインタフェースは
`flush_to()` と `clear()` を別メソッドとして分けており、`flush_to()` の
説明にも「書き出し後に保持内容を破棄する」という記述はない。呼び出し側が
書き出しの成功を確認してから明示的に `clear()` するか、あるいはリング
バッファをそのまま次のトリガまでスライドさせ続けるかを選べるようにする
ため、`flush_to()` は書き出すだけで保持内容を変更しない。
"""

from __future__ import annotations

import math
from collections import deque
from typing import Protocol

from sensing_foundation.types import CaptureFrame, StreamProfile

__all__ = ["FrameRingBuffer"]

_BYTES_PER_DEPTH_PIXEL = 2
"""Depth 1画素あたりのバイト数（`uint16`）。`config.py` の
`_BYTES_PER_DEPTH_PIXEL` と同じ値・同じ根拠（design.md Invariants の式
`width*height*2*fps*seconds`）。"""


class _RecorderLike(Protocol):
    """`flush_to()` が要求する最小限の記録器インターフェース。

    `sensing_foundation.recording.writer.SessionRecorder`（タスク 4.3、
    本タスク時点では未実装）が最終的に満たす構造を、実体への依存なしに
    型として表す。design.md「SessionRecorder」節が定める
    `write(self, frame: CaptureFrame) -> None` のみを要求する。
    """

    def write(self, frame: CaptureFrame) -> None: ...


class FrameRingBuffer:
    """直近 `seconds` 秒相当の `CaptureFrame` を保持するリングバッファ。

    Attributes:
        なし（内部状態は非公開）。

    Invariants:
        保持する枚数は常に `ceil(seconds * profile.fps)` を超えない。
        超過分は最も古いフレームから捨てる（FIFO、`deque(maxlen=...)`
        そのものの挙動）。
    """

    def __init__(
        self,
        seconds: float,
        profile: StreamProfile,
        max_bytes: int | None = None,
    ) -> None:
        """リングバッファを構築する。

        Args:
            seconds: 保持する直近の秒数。
            profile: 保持対象フレームの取得設定（`fps` を容量計算に使う）。
            max_bytes: 呼び出し規約上受理するのみで、本クラスは容量計算にも
                append の拒否にも使わない（モジュール docstring
                「`max_bytes` パラメータの扱い」を参照）。
                `required_bytes(seconds, profile) <= max_bytes` の検証は
                `RuntimeSettings.resolve()` が起動時に行う。
        """
        self._seconds = seconds
        self._profile = profile
        self._max_bytes = max_bytes
        capacity = max(math.ceil(seconds * profile.fps), 0)
        self._frames: deque[CaptureFrame] = deque(maxlen=capacity)

    @staticmethod
    def required_bytes(seconds: float, profile: StreamProfile) -> int:
        """`seconds` 秒分を保持するのに必要な概算バイト数を返す。

        design.md Invariants の式そのもの: `width*height*2*fps*seconds`。
        `sensing_foundation.config._required_ring_bytes()` と同じ式・
        同じ丸め方（`int()` によるトランケート）である。

        Args:
            seconds: 保持したい秒数。
            profile: 対象の `StreamProfile`（`width_px` / `height_px` /
                `fps` を使う）。

        Returns:
            概算バイト数。解像度・fps・秒数のいずれについても比例する。
        """
        return int(
            profile.width_px
            * profile.height_px
            * _BYTES_PER_DEPTH_PIXEL
            * profile.fps
            * seconds
        )

    def append(self, frame: CaptureFrame) -> None:
        """フレームをバッファへ追加する。

        `deque(maxlen=...)` への追加のみで、`frame` を複製しない。
        容量（`ceil(seconds * profile.fps)`）を超えた場合、最も古い
        フレームが自動的に破棄される。

        Args:
            frame: 追加する `CaptureFrame`。
        """
        self._frames.append(frame)

    def flush_to(self, recorder: _RecorderLike) -> int:
        """保持している全フレームを、保持順（古い→新しい）で `recorder` へ書き出す。

        保持内容は変更しない（`clear()` は別途明示的に呼ぶこと。モジュール
        docstring「`flush_to()` は自動的に `clear()` しない」を参照）。
        渡すフレームは複製せず、保持していたインスタンスをそのまま
        `recorder.write()` へ渡す（オブジェクト同一性が保たれる）。

        Args:
            recorder: `write(self, frame: CaptureFrame) -> None` を持つ
                記録器（`SessionRecorder` 実装、またはそれと構造的に
                互換なもの）。

        Returns:
            書き出したフレーム数。
        """
        count = 0
        for frame in self._frames:
            recorder.write(frame)
            count += 1
        return count

    def clear(self) -> None:
        """保持しているフレームをすべて破棄する。"""
        self._frames.clear()

    def __len__(self) -> int:
        """現在保持しているフレーム数を返す。"""
        return len(self._frames)
