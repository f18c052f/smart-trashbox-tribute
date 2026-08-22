"""距離帯ゲート方式の前景マスク生成（design.md「L4-L5: 検出 → MaskBuilder と
3方式」の `depth_band` 行、タスク 2.2）。

design.md の表が定める方式そのもの: 「ROI 内で `z_min_mm <= z <= z_max_mm`
を前景とする」。3方式のうち**状態を持たない最軽量のベースライン**であり、
想定される弱点は「背景が距離帯に入ると常時前景になる」（design.md 同表）。

**`MaskBuilder` プロトコルを継承しない。** `masks/__init__.py` が定める通り、
本クラスは `kind` / `ready` / `build` / `reset` を構造的に満たすダック
タイピングの実装であり、`Protocol` クラスの定義そのものはタスク 2.5 の
責務である。`MaskBuilder` プロトコル（design.md 記載）が要求する形:

    class MaskBuilder(Protocol):
        @property
        def kind(self) -> DetectorKind: ...
        @property
        def ready(self) -> bool: ...
        def build(self, roi_depth: numpy.ndarray) -> numpy.ndarray: ...
        def reset(self) -> None: ...

無効 Depth の扱い（要件 10.1）
--------------------------------
**無効 Depth（raw=0, `INVALID_DEPTH_RAW`）はどの方式でも前景にしない**
（design.md「MaskBuilder / Implementation Notes」）。この判定は上流が
公開する定数 `sensing_foundation.INVALID_DEPTH_RAW` を使い、
`roi_depth != INVALID_DEPTH_RAW` という **`is_valid_depth(raw)` と同値の
式**をベクトル化して適用する（`sensing_foundation.geometry.is_valid_depth`
の実装そのものが `return raw != INVALID_DEPTH_RAW` であり、独自の判定
基準を持ち込んでいない。パフォーマンス上の理由は下記「実装上の注記」を
参照）。理由は2つある。

1. 判定に使う**基準値**（`INVALID_DEPTH_RAW`）を上流の公開定数から得る
   ことで、値そのものが変わった場合（Revalidation Trigger）に追随できる
   （design.md「Revalidation Triggers」の「上流由来（逆投影の基本演算）」
   に `is_valid_depth` が含まれる）。
2. `z_min_mm` が `0` 以下に設定された場合、`0 mm` が数値としては距離帯の
   内側に収まってしまう。もし有効性の判定を経ずに mm 換算後の値だけで
   帯判定をすると、無効画素が誤って前景になる（本モジュールのテストが
   固定する境界ケース）。距離帯の判定より**先に**有効性で弾く。

raw → mm 換算（`depth_scale_mm`）
------------------------------------
raw uint16 カウントは `depth_scale_mm` を掛けるまで無意味な値である
（RealSense のセンサ依存の係数）。この換算式（`raw * depth_scale_mm`）は
`sensing_foundation.geometry.depth_raw_to_mm` の実装そのものであり、
本モジュールは同じ式を配列全体にベクトル化して適用する（design.md
「Allowed Dependencies」: 逆投影の基本演算は上流の公開入口から借りる。
これは逆投影そのものではなく mm 換算だが、同じ規律——raw カウントに
`depth_scale_mm` を掛ける処理を複数箇所で異なる式として再実装しない
——に従う。「同じ式をベクトル化」であって「別の換算式を発明」ではない）。

実装上の注記（スカラー関数を要素ごとに呼ばない理由）
------------------------------------------------------
`is_valid_depth()` / `depth_raw_to_mm()` はスカラー引数の純関数であり、
`numpy.vectorize` で包んで配列に適用すると内部的に Python レベルの
要素ごとループになり、ROI が大きいほど致命的に遅くなる（実測: 200×200
ROI で約35倍、既定 ROI 全域に相当する 640×480 で約231倍、ネイティブな
NumPy 配列演算に対して低速）。これは Pi 4 の低レイテンシ制約
（`docs/development-environment.md §4`）に反し、`depth_band` が3方式中
**最軽量**であるという設計上の役割（design.md 表）そのものを損なう。
そのため本モジュールは、上流関数と**同じ公開定数・同じ式**を
`roi_depth` 全体に対するネイティブな NumPy 配列演算として適用する
（要素ごとに関数を呼び出さない）。これは上流の判定基準・換算式を
再定義するのではなく、同一の基準・式をベクトル化しただけである。

依存方向（design.md「Dependency Direction」表の `detection/masks/*` 行）:
0〜3層（`errors` / `types` / `config` / `metrics` / `detection/mask_ops`）
に加え `sensing_foundation`（公開入口の `INVALID_DEPTH_RAW`）を import
してよい。`cv2` は直接 import しない（`mask_ops` を経由しない理由: 本
方式はモルフォロジ・ラベリングを使わないため `mask_ops` の3関数の
いずれも必要としない）。
"""

from __future__ import annotations

import numpy as np
from sensing_foundation import INVALID_DEPTH_RAW

from flying_object_tracking.types import DetectorKind, Roi

__all__ = ["DepthBandMask"]


class DepthBandMask:
    """距離帯ゲート方式の `MaskBuilder`（design.md 表の `depth_band` 行）。

    状態を持たない。`ready` は常に `True`（背景モデルのような初期化状態が
    無いため、`MaskBuilder` の Invariant「`ready` が `False` の間は全 False
    のマスクを返す」は本方式には該当しない——`ready` が `False` になること
    自体が無い）。
    """

    def __init__(self, roi: Roi, depth_scale_mm: float) -> None:
        """距離帯を `roi` から、mm 換算係数を `depth_scale_mm` から受け取る。

        `Roi` は画素矩形（`x_px` 等）と距離帯（`z_min_mm` / `z_max_mm`）の
        両方を持つが（design.md「CoreTypes」）、`build()` は既に ROI で
        切り出し済みの配列を受け取る契約であるため、本コンストラクタは
        `roi` のうち距離帯だけを使う。画素矩形部分は無視する。

        後続タスク（2.5/2.7）が `DetectorConfig` + `Roi` + プロファイル
        情報から3方式を一様に構築する際、`depth_scale_mm` は
        `StreamProfile.depth_scale_mm`（`sensing_foundation` 由来）を
        渡す想定である。

        Args:
            roi: 処理対象範囲。`z_min_mm` / `z_max_mm` を距離帯として使う。
                いずれも `None` なら対応する側の境界を設けない（無制限）。
            depth_scale_mm: raw uint16 カウントを mm へ換算する係数。
                `sensing_foundation.geometry.depth_raw_to_mm` と同じ式
                （`raw * depth_scale_mm`）で `build()` 内の mm 換算に使う。
        """
        self._z_min_mm = roi.z_min_mm
        self._z_max_mm = roi.z_max_mm
        self._depth_scale_mm = depth_scale_mm

    @property
    def kind(self) -> DetectorKind:
        return DetectorKind.DEPTH_BAND

    @property
    def ready(self) -> bool:
        """常に `True`。状態を持たないため初期化待ちが発生しない。"""
        return True

    def build(self, roi_depth: np.ndarray) -> np.ndarray:
        """`roi_depth` から距離帯ゲートの前景マスクを作る。

        Preconditions:
            `roi_depth` は ROI 切り出し済みの uint16 raw depth 配列。
            この配列を変更しない。

        Postconditions:
            `roi_depth` と同じ shape の**新規割り当て** bool 配列を返す。
            `True` の画素は「有効な Depth を持ち、かつ mm 換算後の値が
            `z_min_mm` 以上 `z_max_mm` 以下」を満たす画素だけである。

        Args:
            roi_depth: ROI 切り出し済みの uint16 raw depth 配列。

        Returns:
            同 shape の新規 bool 配列。
        """
        # `sensing_foundation.geometry.is_valid_depth` と同一の基準
        # （`raw != INVALID_DEPTH_RAW`）を、公開定数を使ってネイティブな
        # NumPy 配列演算として適用する（モジュール docstring「実装上の
        # 注記」参照。`numpy.vectorize` は使わない）。
        valid = roi_depth != INVALID_DEPTH_RAW
        # `sensing_foundation.geometry.depth_raw_to_mm` と同一の式
        # （`raw * depth_scale_mm`）を配列全体に適用する。
        depth_mm = roi_depth.astype(np.float64) * self._depth_scale_mm

        in_band = np.ones(roi_depth.shape, dtype=bool)
        if self._z_min_mm is not None:
            in_band &= depth_mm >= self._z_min_mm
        if self._z_max_mm is not None:
            in_band &= depth_mm <= self._z_max_mm

        return valid & in_band

    def reset(self) -> None:
        """no-op。`depth_band` は状態を持たないため、リセットすべき内部
        状態が無い。`MaskBuilder` の共有シェイプを満たすためだけに存在する。
        """
        return None
