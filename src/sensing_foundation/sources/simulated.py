"""合成入力アダプタを実装する（design.md「L6-L7: アダプタ・保存・診断 / SimulatedSource」）。

`SimulatedSource` は、外部から渡された供給関数（`FrameSupplier`）を
`BaseFrameSource`（タスク 3.1）の契約へ接続する**純粋な配線層**である。
実機も RealSense SDK も無い環境（要件 4.4）で、`for frame in source.frames():`
という共通表現の系列を得るための、live が無い期間の唯一の実行経路になる
（design.md「Risks: テスト専用に見えるが、契約テストの主役である」）。

**投擲物理・ノイズ生成は本モジュールに置かない**（要件 4.5）。供給関数
（`FrameSupplier = Callable[[int], numpy.ndarray | None]`）は呼び出し側
（テストでは `tests/sensing_foundation/synthetic.py`、将来は
`trajectory-simulator` Spec）が用意する。本モジュールはこれを呼び出し、
返ってきた `numpy.ndarray` を `RawFrame` へ包んで `BaseFrameSource` へ渡す
だけであり、投擲の弾道・ノイズモデルに相当する計算を一切持たない。

**実装判断1: `seq` は内部の呼び出し通し番号を使う（供給内容からの復元は
行わない）**。`tests/sensing_foundation/synthetic.py`（タスク 1.7）が生成する
配列は `decode_seq_from_frame()` で埋め込み済みの `seq` を復元できるが、
それは `synthetic.py` が作った配列だけに通用する規約であり、任意の呼び出し側
（`trajectory-simulator` を含む）が渡す一般の Depth 配列には通用しない。
また `src/` は `tests/` を import してはならない（境界。`test_boundaries.py`
相当の静的検証対象）。したがって本モジュールは供給関数を呼んだ回数
（0 始まりの通し番号）をそのまま `RawFrame.seq` に使う。この結果、
`SimulatedSource` 経由で観測される `seq` は常に欠番なく増加し、
`BaseFrameSource` の欠落検出（`gap_before`）は常に 0 になる
（`tests/sensing_foundation/synthetic.py` の `seqs` に非連続な値を渡しても、
それは供給された Depth 配列の**中身**に埋め込まれるだけで、本モジュールが
`RawFrame.seq` として使う値には影響しない）。欠落検出そのものの正しさは
タスク 3.1 の `test_source.py` が既に固定しているため、本モジュールの
テストではこの「呼び出し通し番号を `seq` に使う」という選択自体を
固定するにとどめる。

**実装判断2: コンストラクタへ `clock` / `capture_config` を追加する**。
design.md の `SimulatedSource` Contract は `(supplier, profile, metrics,
fps=30)` のみを示すが、`BaseFrameSource.__init__` は `clock: SessionClock`
（`metrics` と同一インスタンスを共有する必要がある。`clock` は
`CaptureMetrics` から取り出せない — `metrics.py` のモジュール docstring
参照）と `capture_config: CaptureConfig`（`on_acquire_error` 等の取得元）を
必須で要求する。design.md の Contract 表記は「差し替え口を提供する」という
意図を示す最小限のスケッチであり、`BaseFrameSource` を継承する以上
避けられない配線パラメータまでは列挙していないと解釈し（タスク 3.1 の
`source.py` モジュール docstring が同種の実装判断を先例として明記している
のと同じ扱い）、キーワード専用引数として追記した。design.md が明記する
位置引数の並び（`supplier, profile, metrics, fps=30`）はそのまま維持する。

**実装判断3: `fps` は受け取るが、供給のペース制御には使わない**。
design.md の Validation/Risks はいずれも `fps` の使途（実時間ペーシング等）
を明記していない。ペース制御が必要な場合は供給関数側（`synthetic.py` の
`make_supplier(..., delay_s=...)`）が担う設計に既になっており、本モジュールが
重ねて `time.sleep` を挟むと、供給関数のペース制御と二重になり挙動が
不透明になる。したがって `fps` は将来の拡張点として保持するのみとし、
現時点では `_acquire()` の動作に影響しない。

**ドレインの二重防御**（`source.py` Implementation Notes「タスク3.1」の
指摘に従う。要件 6.7 相当・4.5「取りこぼしを新たに作らない」の防御）:

1. `super().__init__(...)` へ渡す `capture_config` は、呼び出し側が何を
   渡しても `drain_enabled=False` に固定して渡す。
2. `_drain_latest()` 自体も、呼ばれることが無い前提に依存せず、常に
   `(None, 0)` を返す。

**契約違反の扱い**: 供給関数が返した配列の `shape` が
`(profile.height_px, profile.width_px)` と一致しない、または `dtype` が
`numpy.uint16` でない場合、`errors.SourceContractError` を送出する
（design.md「Validation」）。これは `CaptureConfig.on_acquire_error` の
継続/停止判断（`None` 返却＝取得失敗）とは別の経路であり、
`on_acquire_error` に関わらず常に例外として送出する——契約違反は
「観測された事実」ではなく「呼び出し方の誤り」（供給関数の実装不備）
だからである（`errors.py` モジュール docstring の区分に従う）。

**終端シグナル**: 供給関数が `None` を返したら、`_acquire()` は `None` を
返さず `StopIteration` を送出する（`source.py` が定める「取得失敗」と
「正常な終端」の区別。`None` 返却は `on_acquire_error` 経由の失敗計数に
回ってしまい、有限系列が正しく終わらない）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import numpy as np

from sensing_foundation.config import CaptureConfig
from sensing_foundation.errors import SourceContractError
from sensing_foundation.metrics import CaptureMetrics
from sensing_foundation.source import BaseFrameSource, RawFrame
from sensing_foundation.timebase import SessionClock
from sensing_foundation.types import SourceKind, StreamProfile

__all__ = ["FrameSupplier", "SimulatedSource"]

#: design.md が定める供給関数の型。`index`（0始まりの呼び出し通し番号）を
#: 受け取り、Depth 配列を返す。もう供給するものが無ければ `None` を返す。
FrameSupplier = Callable[[int], "np.ndarray | None"]

_EXPECTED_DTYPE = np.uint16


class SimulatedSource(BaseFrameSource):
    """合成フレームの差し替え口を提供する `FrameSource` アダプタ。

    投擲物理・ノイズ生成は持たない（要件 4.5）。供給関数を `BaseFrameSource`
    の契約（`_acquire()` / `_drain_latest()` の2操作）へ接続するだけの
    プラミング層である。

    Preconditions: `supplier` は同一の `index` に対し、呼ばれるたびに
        矛盾しない値（同じフレームか、少なくとも矛盾しない終端判定）を
        返すことが望ましいが、本クラスはこれを検証しない
        （`BaseFrameSource` 同様、呼び出し規約はモジュール docstring に
        委ねる）。
    Postconditions: `frames()` が返す枚数は、`supplier` が `None` を返す
        までに応答した枚数と一致する（観測可能な完了状態）。
    Invariants: `drain_enabled` は常に偽として扱われる
        （モジュール docstring「ドレインの二重防御」）。
    """

    def __init__(
        self,
        supplier: FrameSupplier,
        profile: StreamProfile,
        metrics: CaptureMetrics,
        fps: int = 30,
        *,
        clock: SessionClock,
        capture_config: CaptureConfig | None = None,
    ) -> None:
        """`SimulatedSource` を組み立てる。

        Args:
            supplier: `index -> depth 配列 または None`（終端）の呼び出し
                可能オブジェクト。投擲物理・ノイズ生成は呼び出し側の責務
                （モジュール docstring 参照）。
            profile: このフレーム取得時点で有効な取得設定。供給された
                Depth 配列の shape/dtype 検証に使う。
            metrics: 計測点の送出先。
            fps: 将来の拡張点として保持するのみで、現時点では供給の
                ペース制御に使わない（モジュール docstring「実装判断3」）。
            clock: `metrics` と**同一インスタンス**の `SessionClock`
                （design.md の Contract 表記には無いキーワード専用の追加
                引数。モジュール docstring「実装判断2」参照）。
            capture_config: `on_acquire_error` 等の取得元。`None`（既定）
                なら `CaptureConfig()` の既定値を使う。`drain_enabled` は
                ここで何を渡しても無視され、常に `False` に固定される
                （モジュール docstring「ドレインの二重防御」）。
        """
        base_config = capture_config if capture_config is not None else CaptureConfig()
        forced_config = replace(base_config, drain_enabled=False)
        super().__init__(
            kind=SourceKind.SIMULATED,
            profile=profile,
            metrics=metrics,
            clock=clock,
            capture_config=forced_config,
        )
        self._supplier = supplier
        self._fps = fps
        self._next_call_index = 0

    def _acquire(self, timeout_ms: int) -> RawFrame | None:
        """供給関数を1回呼び、`RawFrame` へ変換する。

        `timeout_ms` は共通契約が渡す待機上限だが、供給関数は同期的に
        値を返す（待機自体が必要なら供給関数側が担う。モジュール docstring
        「実装判断3」）ため、本アダプタはこれを使わない。

        Raises:
            StopIteration: 供給関数が `None` を返した（正常な終端）。
            SourceContractError: 返った配列の shape/dtype が `profile` と
                食い違う（契約違反。呼び出し方の誤り）。
        """
        del timeout_ms  # 供給関数は同期的に返すため使わない（docstring参照）。

        index = self._next_call_index
        self._next_call_index += 1

        depth = self._supplier(index)
        if depth is None:
            raise StopIteration

        self._reject_if_contract_violated(depth)
        return RawFrame(seq=index, depth=depth)

    def _drain_latest(self) -> tuple[RawFrame | None, int]:
        """常に `(None, 0)` を返す（ドレインの二重防御の一方）。"""
        return (None, 0)

    def _reject_if_contract_violated(self, depth: np.ndarray) -> None:
        """`depth` の shape/dtype が `profile` と食い違えば `SourceContractError`。"""
        expected_shape = (self.profile.height_px, self.profile.width_px)
        if depth.shape != expected_shape or depth.dtype != _EXPECTED_DTYPE:
            raise SourceContractError(
                "供給関数が返した Depth 配列が契約に反する: "
                f"shape={depth.shape!r}（期待 {expected_shape!r}）, "
                f"dtype={depth.dtype!r}（期待 {_EXPECTED_DTYPE!r}）。"
                "SimulatedSource へ渡す供給関数の実装を確認せよ。"
            )
