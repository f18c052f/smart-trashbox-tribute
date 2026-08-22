"""合成フレーム列を供給する最小の `FrameSource` 実装（タスク 1.5。テストツリー専用）。

`sensing_foundation.FrameSource` は `kind` / `profile` / `stats` / `start()` /
`frames()` / `stop()` / `__enter__` / `__exit__` を構造的に満たせばよい
`Protocol` である（`sensing_foundation/source.py` 参照。同パッケージ自身も
`BaseFrameSource` を `FrameSource` に明示的に継承させず、構造的部分型として
扱う流儀を採っている）。本モジュールはこの構造を、`synthetic.py` が事前に
組み立てた `CaptureFrame` 列をそのまま返すだけの最小のフェイクで満たす。

**設計判断: 上流の `open_source(..., supplier=...)` / `SimulatedSource` を
再利用しなかった理由**。design.md・タスク仕様はこちらの経路（本モジュールの
実装が (a)）と、`SimulatedSource` を `FrameSupplier` 経由で再利用する経路
（(b)）の両方を許容している。(b) を検討したが、
`sensing_foundation/sources/simulated.py` の `SimulatedSource._acquire()` は
「`seq` は供給関数の**呼び出し回数**（0始まりの通し番号）をそのまま使い、
供給内容からの復元は行わない」と明記している（同モジュール docstring
「実装判断1」）。これは `FrameSupplier = Callable[[int], ndarray | None]` が
渡す `index` が常に1つずつ増える呼び出し通し番号であり、たとえ論理的な
軌道インデックスを内部で飛ばして（フレームドロップを表現して）供給しても、
`SimulatedSource` 側はそれを検知しようがないためである。結果として
`SimulatedSource` 経由で得られる `CaptureFrame.seq` は常に欠番なく増加し、
`BaseFrameSource._detect_gap` による `gap_before` は常に `0` になる——
本タスクが要求する「フレーム欠落が `gap_before` として観測できること」
（観測可能な完了状態）を (b) の経路では検証できない。したがって本モジュールは
(a) を採用する: `synthetic.py`（`gap_before` を自前で計算済みの `CaptureFrame`
列を返す）をそのまま流すだけの、`FrameSource` プロトコルを直接満たす
フェイクを実装する。ドレイン・欠落検出・計測送出といった `BaseFrameSource`
が既に持つロジックを二重実装しない（「上流のアダプタを二重化しない」という
タスク要求は、`BaseFrameSource` の**ロジック**を再実装しないことと解釈し、
本フェイクは事前構築済みの列を返すだけの配線に徹する）。

**このモジュールを使うテストファイルへの注意**: `tests/` 配下は
パッケージ化されていない（`__init__.py` が無い）ため、モジュール名は
セッション全体でフラットな `sys.modules` 名前空間を共有する。他 Spec が
将来同じベース名 `fakes.py` を置く事故を避けるため、`synthetic.py`
（`tests/sensing_foundation/synthetic.py` と実際に衝突した実例がある）と
同じ規約に揃え、本ファイルも bare import しないこと。**`tests/
flying_object_tracking/conftest.py` の `fakes_module` フィクスチャ経由で
のみアクセスすること**（同 conftest のモジュール docstring
「`synthetic_module` / `fakes_module` フィクスチャ」参照）。
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from sensing_foundation import CaptureFrame, CaptureStats, SourceKind, StreamProfile

__all__ = ["SyntheticFrameSource"]


class SyntheticFrameSource:
    """事前構築済みの `CaptureFrame` 列を `FrameSource` 契約でラップする最小のフェイク。

    `sensing_foundation.FrameSource`（`Protocol`）を構造的に満たす。ドレイン・
    欠落検出・統計計算のロジックは持たず、渡された列をそのまま返すだけである
    （欠落は `synthetic.py` が `gap_before` として計算済みの値を使う）。

    Preconditions: `frames` は空でないこと。
    Postconditions: `frames()` は構築時に渡した列を渡した順序のまま返す。
        `stop()` 後も `stats` は変わらない（`frames()` を消費しない静的な列の
        ため、`stats` は構築時点で確定している）。
    """

    def __init__(self, frames: Sequence[CaptureFrame], *, kind: SourceKind = SourceKind.SIMULATED) -> None:
        if not frames:
            raise ValueError("frames は空であってはならない")
        self._frames: list[CaptureFrame] = list(frames)
        self._kind = kind
        self._profile: StreamProfile = self._frames[0].profile
        self._started = False
        self._stopped = False

    @property
    def kind(self) -> SourceKind:
        return self._kind

    @property
    def profile(self) -> StreamProfile:
        return self._profile

    @property
    def stats(self) -> CaptureStats:
        """渡された列から算出した静的な統計。`gap_before` の総和を欠落数とする。"""
        return CaptureStats(
            frames_yielded=len(self._frames),
            frames_dropped=0,
            frames_missing=sum(frame.gap_before for frame in self._frames),
            duration_ms=0.0,
            measured_fps=0.0,
            acquire_errors=0,
        )

    def start(self) -> None:
        """取得を開始する（フェイクのため実質 no-op。呼び出し順の確認用）。"""
        self._started = True

    def frames(self) -> Iterator[CaptureFrame]:
        """構築時に渡された `CaptureFrame` 列をそのまま返す。"""
        yield from self._frames

    def stop(self) -> None:
        """取得を停止する（フェイクのため実質 no-op）。"""
        self._stopped = True

    def __enter__(self) -> "SyntheticFrameSource":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()
