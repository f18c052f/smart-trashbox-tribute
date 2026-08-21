"""決定的な合成 Depth フレーム生成ヘルパ（タスク 1.7）。

`sensing_foundation` の3種の入力元（live / recorded / simulated）のうち、
`simulated` は「合成フレームを供給する差し替え口を提供するにとどめ、
投擲物理そのものの生成を自身の責務に含めない」（requirements.md 要件 4.5）。
投擲の物理・ノイズモデルによる合成データ生成は `trajectory-simulator` Spec の
責務であり、本 Spec には含めない。

本モジュールはその「差し替え口」を**テストする側**が使う、純粋にテスト専用の
フィクスチャ生成器である。だからこそ `src/sensing_foundation/` ではなく
`tests/sensing_foundation/` に置かれる（`tests/sensing_foundation/synthetic.py`
自体は import 可能な通常の Python モジュールであり、`conftest.py` のような
pytest 専用ファイルではない — 本体の他のテストファイルから
`from synthetic import ...` の形で素直に import して使う）。

design.md の `SimulatedSource` コンポーネントが要求する供給関数の型は

    FrameSupplier = Callable[[int], "numpy.ndarray | None"]

（`index -> depth 配列、または終端を示す None`）である。本モジュールは
この形の呼び出し可能オブジェクトを組み立てる `make_supplier()` と、
その元になる決定的生成の生プリミティブ `make_depth_frame()` /
`make_frame_series()` を提供する。

**決定性の定義**: 乱数・エントロピ源を一切使わない純粋な算術パターンで
Depth 配列を組み立てる。したがって同じ引数（解像度・`seq`）で生成した
配列は、同一プロセス内で何度呼び出してもビット単位で同一であるだけでなく、
別プロセス・別実行でも同一である（PRNG の内部状態やアルゴリズム版に
一切依存しないため）。

下流タスク（3.2: `SimulatedSource` の契約テスト、4.6: 入力元共通の契約テスト、
7.2/7.3: ベンチ、8.1: CLI end-to-end）は本モジュールの存在と挙動を前提にする。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence

import numpy as np

__all__ = [
    "DEFAULT_BASE_DEPTH_MM",
    "decode_seq_from_frame",
    "make_depth_frame",
    "make_frame_series",
    "make_supplier",
]

DEFAULT_BASE_DEPTH_MM = 500
"""`make_depth_frame` が生成する Depth 値のベースライン（mm 換算前の生カウント相当）。"""

_VIOLATE_KINDS = ("shape", "dtype", "both")


def make_depth_frame(
    width_px: int,
    height_px: int,
    seq: int,
    *,
    base_mm: int = DEFAULT_BASE_DEPTH_MM,
) -> np.ndarray:
    """`(width_px, height_px, seq)` から一意に決まる決定的な Depth 配列を生成する。

    乱数・エントロピ源を使わない純関数の算術パターン（画素座標と `seq` の
    線形結合を法で畳んだもの）であり、同じ引数であれば呼び出し回数・
    プロセス・実行タイミングによらず常に同一の配列を返す。

    左上隅の1画素（`[0, 0]`）には `seq % 65536` をそのまま書き込む。
    これにより、生成済みの配列だけを見て「元はどの `seq` だったか」を
    `decode_seq_from_frame()` で復元できる（欠番のある供給を扱う下流テストが、
    供給側の帳簿を別途持たなくても中身から `seq` を確認できるようにするため）。

    Args:
        width_px: 生成する Depth 配列の幅（px）。正の整数。
        height_px: 生成する Depth 配列の高さ（px）。正の整数。
        seq: フレーム番号。内容を一意に決める入力の1つ。
        base_mm: パターンに加算するベースライン値。配列の値域をずらしたい
            場合にのみ変更する。

    Returns:
        `uint16`、shape=`(height_px, width_px)` の配列。書き込み禁止フラグは
        立てない（読み取り専用化は `sensing_foundation.types.CaptureFrame`
        構築時の責務であり、本関数の責務ではない）。呼び出し側は返された
        配列を自由に書き換えてよい。

    Raises:
        ValueError: `width_px` または `height_px` が正の整数でない場合。
    """
    if width_px <= 0 or height_px <= 0:
        raise ValueError(
            f"width_px/height_px は正の整数である必要がある: {width_px}x{height_px}"
        )

    y = np.arange(height_px, dtype=np.uint32).reshape(-1, 1)
    x = np.arange(width_px, dtype=np.uint32).reshape(1, -1)
    seq_term = np.uint32(seq % 65536)
    pattern = (x * np.uint32(7) + y * np.uint32(13) + seq_term * np.uint32(97)) % np.uint32(4000)
    depth = (np.uint32(base_mm) + pattern).astype(np.uint16)

    depth[0, 0] = seq_term
    return depth


def decode_seq_from_frame(depth: np.ndarray) -> int:
    """`make_depth_frame` が左上隅（`[0, 0]`）に埋め込んだ `seq`（mod 65536）を読み出す。

    `make_depth_frame` 以外が生成した配列に対しては意味を持たない。
    """
    return int(depth[0, 0])


def make_frame_series(width_px: int, height_px: int, seqs: Sequence[int]) -> list[np.ndarray]:
    """`seqs` の各値に対応する Depth 配列を、その順序通りに生成する。

    `seqs` は連続している必要はない（例: `[0, 1, 3, 4]` のように欠番があってよい）。
    同じ引数で2回呼び出せば、要素数・各要素の shape/dtype/内容のすべてが
    完全に一致する系列が返る（タスク 1.7 の観測可能な完了状態）。
    """
    return [make_depth_frame(width_px, height_px, seq) for seq in seqs]


def make_supplier(
    width_px: int,
    height_px: int,
    seqs: Sequence[int],
    *,
    delay_s: float = 0.0,
    violate_at: int | None = None,
    violate_kind: str = "shape",
) -> Callable[[int], np.ndarray | None]:
    """design.md の `FrameSupplier`（`Callable[[int], numpy.ndarray | None]`）を組み立てる。

    引数の `index` は `seqs` への **0始まりの位置**であり、`seqs` に格納された
    フレーム番号の値そのものではない（`SimulatedSource` が要求する形と同じ）。
    `index` が範囲外（負、または `len(seqs)` 以上）になった時点で `None` を
    返し、供給の終端を知らせる。

    3つのテスト用途を、1つの関数の任意引数として提供する（設計はタスクの
    illustrative sketch にある `make_gappy_supplier` / `make_slow_supplier` /
    `make_contract_violating_supplier` を、より composable な単一関数へ
    統合したもの）:

    - **欠番のある供給**（gap/missing-frame 検出のテスト用）:
      `seqs` に非連続な値（例 `[0, 1, 3, 4]`）を渡すだけでよい。返された
      各配列は `decode_seq_from_frame()` で元の `seq` を復元でき、
      どこが飛んでいたかを内容から確認できる。
    - **遅い供給**（drain 挙動のテスト用）: `delay_s > 0` を渡すと、
      各呼び出しが配列を返す直前に `time.sleep(delay_s)` する。
    - **契約違反の供給**（アダプタの契約違反リジェクトのテスト用）:
      `violate_at`（`seqs` 上の位置インデックス）を指定すると、その位置の
      呼び出しだけ意図的に矛盾した配列を返す。矛盾のさせ方は `violate_kind`
      で選ぶ:
        - `"shape"`: 幅を1画素広げる（`(height_px, width_px + 1)`）
        - `"dtype"`: `uint16` ではなく `int32` にする
        - `"both"`: 両方を同時に行う
      目標 `StreamProfile` に対する shape/dtype の妥当性検証そのものは
      `SimulatedSource`（タスク 3.2）の責務であり、本関数は「壊れた入力を
      用意する」ところまでを持つ。

    Args:
        width_px: 生成する Depth 配列の幅（px）。違反注入時を除く。
        height_px: 生成する Depth 配列の高さ（px）。違反注入時を除く。
        seqs: 供給するフレーム番号の列。非連続でもよい。空でもよい
            （その場合 `supplier(0)` は直ちに `None` を返す）。
        delay_s: 各呼び出し前に待つ秒数。`0`（既定）なら待たない。
        violate_at: 契約違反を注入する `seqs` 上の位置インデックス。
            `None`（既定）なら注入しない。指定する場合は `0 <= violate_at
            < len(seqs)` でなければならない。
        violate_kind: `"shape"` / `"dtype"` / `"both"` のいずれか
            （既定は `"shape"`）。`violate_at` が `None` のときは無視される。

    Returns:
        `index -> depth 配列 または None` の呼び出し可能オブジェクト。

    Raises:
        ValueError: `violate_kind` が既知の値でない場合、または `violate_at`
            が `seqs` の範囲外の場合。
    """
    if violate_kind not in _VIOLATE_KINDS:
        raise ValueError(
            f"violate_kind は {_VIOLATE_KINDS} のいずれかである必要がある: {violate_kind!r}"
        )
    if violate_at is not None and not (0 <= violate_at < len(seqs)):
        raise ValueError(
            f"violate_at は seqs の範囲内である必要がある: violate_at={violate_at}, "
            f"len(seqs)={len(seqs)}"
        )

    frames = make_frame_series(width_px, height_px, seqs)

    def supplier(index: int) -> np.ndarray | None:
        if index < 0 or index >= len(frames):
            return None

        if delay_s > 0:
            time.sleep(delay_s)

        frame = frames[index]
        if violate_at is not None and index == violate_at:
            frame = frame.copy()
            if violate_kind in ("shape", "both"):
                # 幅を1画素広げ、target StreamProfile の width_px と
                # 一致しなくする。
                frame = np.concatenate([frame, frame[:, -1:]], axis=1)
            if violate_kind in ("dtype", "both"):
                frame = frame.astype(np.int32)
        return frame

    return supplier
