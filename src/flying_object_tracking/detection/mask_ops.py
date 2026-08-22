"""OpenCV への唯一の窓口：ROI 切り出し・形態処理・連結成分ラベリング
（design.md「L3: 計測と画像処理の基礎 → MaskOps」、タスク 2.1）。

**本パッケージ（`flying_object_tracking`）で `cv2` を import するのは
本モジュールだけである。** Pi 上で OpenCV 導入が難航した場合（OQ-41）の
退避範囲をここに限定するための設計判断であり、`detection/masks/*` や
`Detector` など他のモジュールは `cv2` を直接 import せず、本モジュールが
提供する3関数（`crop_roi` / `clean_mask` / `components`）だけを経由して
画像処理ライブラリの機能を使う（design.md「Dependency Direction」表:
layer 3 `detection/mask_ops` のみが `cv2` を持つ）。

提供する3関数の非破壊契約（要件 1.3 / 2.2 / 2.6 / 3.1）
-------------------------------------------------------
- `crop_roi`: 入力 Depth 配列に対して**コピーせずビューを返す**。
  戻り値への書き込みは入力配列に反映される（ビューであることの裏返し）が、
  本モジュール自身は一切書き込まない。
- `clean_mask` / `components`: 入力配列を変更せず、常に**新しい**配列・値を
  返す。呼び出し側が戻り値を書き換えても入力やそれ以外の呼び出し結果を
  破壊しない。

軸の対応（`crop_roi`）
-----------------------
`sensing_foundation` が渡す Depth 配列は shape `(height_px, width_px)`
（NumPy の 2D 配列の第1軸が行＝y、第2軸が列＝x）である。したがって
`Roi.x_px` / `Roi.width_px` は配列の**第2軸（列）**、`Roi.y_px` /
`Roi.height_px` は**第1軸（行）**に対応する。ここを取り違えると、検出
結果全体が画像上で silently 転置・シフトするため、テストで固定する
（`test_flying_object_tracking_mask_ops.py::TestCropRoi::
test_axis_mapping_x_is_columns_y_is_rows`）。

カーネル 0 の扱い（`clean_mask`）
----------------------------------
`open_px` と `close_px` が両方 0 のとき、`cv2.morphologyEx` の呼び出し
自体を省略する（処理削減の打ち手として残す。タスク 2.1 本文）。この場合
でも戻り値は入力と値が等しい**新しい**配列であり、呼び出し側が戻り値を
書き換えても入力マスクを破壊しない。

型変換の順序についての注記
----------------------------
`astype` による全画面コピーはレイテンシに直結する（`development-
environment.md §4`「不要な画像コピーを減らす」）。本モジュールの関数群は
呼び出し順序として **`crop_roi` で ROI 切り出しをしてから** `clean_mask` /
`components` に渡すことを前提としており、両関数の内部で行う bool→uint8
変換は ROI 切り出し後の（全画面より小さい）配列に対してのみ生じる。

依存方向（design.md「Dependency Direction」表の layer 3 行）:
`errors` ＋ `types` ＋ `config`（0〜2層）＋ `numpy` ＋ `cv2` だけを import
してよい。
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from flying_object_tracking.types import Roi

__all__ = [
    "Component",
    "crop_roi",
    "clean_mask",
    "components",
]


def crop_roi(depth: np.ndarray, roi: Roi) -> np.ndarray:
    """`depth` から `roi` の範囲を切り出したビューを返す（要件 1.3 / 2.2）。

    NumPy のスライシングのみを用い、**コピーしない**。したがって戻り値は
    `depth` とメモリを共有するビューであり、`numpy.shares_memory(cropped,
    depth)` は常に真になる。`depth` が読み取り専用（`flags.writeable is
    False`）の場合、戻り値も読み取り専用のビューとなる（NumPy の標準動作
    を踏襲するだけで、本関数は追加の検証を行わない）。

    Args:
        depth: 切り出し対象の2次元配列。shape は `(height_px, width_px)`
            （第1軸が行＝y、第2軸が列＝x）であることを前提とする。
        roi: 切り出す範囲。`roi.x_px` / `roi.width_px` は列（第2軸）、
            `roi.y_px` / `roi.height_px` は行（第1軸）に対応する。

    Returns:
        `depth` とメモリを共有するビュー。shape は
        `(roi.height_px, roi.width_px)`。

    Note:
        本関数は読み書きしない（読み取り専用配列を渡しても例外にならない）。
        範囲が `depth` の shape を超える場合の挙動は NumPy のスライシング
        規則にそのまま従う（本関数独自の検証・クランプは行わない。ROI が
        フレームに収まるかの検証は呼び出し側／設定解決層の責務。design.md
        「TrackingSettings」節参照）。
    """
    return depth[roi.y_px : roi.y_px + roi.height_px, roi.x_px : roi.x_px + roi.width_px]


def clean_mask(mask: np.ndarray, open_px: int, close_px: int) -> np.ndarray:
    """`mask` に開処理・閉処理を施した**新しい**bool 配列を返す（要件 2.6）。

    `cv2.morphologyEx` を用いる。入力 `mask` は変更せず、常に新規に割り当て
    た配列を返す（開閉のカーネルが両方 0 のときも、値が等しいだけの別
    オブジェクトを返す）。

    Args:
        mask: 前景マスク（bool 配列。0/1 の整数配列でも動作する）。
        open_px: 開処理（収縮→膨張。孤立したノイズ点の除去）に使う
            正方カーネルの一辺の画素数。0 なら開処理を省く。
        close_px: 閉処理（膨張→収縮。ブロブ内の小さな穴を埋める）に使う
            正方カーネルの一辺の画素数。0 なら閉処理を省く。

    Returns:
        入力と同じ shape の**新しい** bool 配列。

    Note:
        `open_px == close_px == 0` の場合、`cv2.morphologyEx` を一度も
        呼ばない（処理削減の打ち手。タスク 2.1 本文）。この場合の戻り値は
        `mask.astype(bool, copy=True)` に相当する値であり、入力と値は
        等しいが同一オブジェクトではない。
    """
    if open_px <= 0 and close_px <= 0:
        return mask.astype(bool, copy=True)

    result = mask.astype(np.uint8, copy=True)

    if open_px > 0:
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (open_px, open_px))
        result = cv2.morphologyEx(result, cv2.MORPH_OPEN, kernel)

    if close_px > 0:
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (close_px, close_px))
        result = cv2.morphologyEx(result, cv2.MORPH_CLOSE, kernel)

    return result.astype(bool)


@dataclass(frozen=True, slots=True)
class Component:
    """連結成分1つぶんの外接矩形・面積・重心（design.md「MaskOps」節）。

    Attributes:
        x_px: 外接矩形の左上 x 座標（画素）。
        y_px: 外接矩形の左上 y 座標（画素）。
        w_px: 外接矩形の幅（画素）。
        h_px: 外接矩形の高さ（画素）。
        area_px: 前景画素数（面積）。
        cx_px: 重心の x 座標（画素、小数）。
        cy_px: 重心の y 座標（画素、小数）。
    """

    x_px: int
    y_px: int
    w_px: int
    h_px: int
    area_px: int
    cx_px: float
    cy_px: float


def components(mask: np.ndarray, max_count: int) -> tuple[Component, ...]:
    """`mask` の連結成分を面積降順で `max_count` 件まで返す（要件 3.1）。

    `cv2.connectedComponentsWithStats`（8連結）を用いる。背景ラベル
    （ラベル0）は結果に含めない。入力 `mask` は変更しない。

    Args:
        mask: 前景マスク（bool 配列。0/1 の整数配列でも動作する）。
        max_count: 返す連結成分の上限件数。面積降順で並べた上位のみ採る。

    Returns:
        面積降順に並んだ `Component` のタプル。連結成分が無ければ空タプル。
        `max_count` が連結成分の総数以上なら全件を返す。
    """
    mask_u8 = mask.astype(np.uint8, copy=True)
    num_labels, _labels, stats, centroids = cv2.connectedComponentsWithStats(
        mask_u8, connectivity=8
    )

    found = [
        Component(
            x_px=int(stats[label, cv2.CC_STAT_LEFT]),
            y_px=int(stats[label, cv2.CC_STAT_TOP]),
            w_px=int(stats[label, cv2.CC_STAT_WIDTH]),
            h_px=int(stats[label, cv2.CC_STAT_HEIGHT]),
            area_px=int(stats[label, cv2.CC_STAT_AREA]),
            cx_px=float(centroids[label, 0]),
            cy_px=float(centroids[label, 1]),
        )
        for label in range(1, num_labels)  # ラベル0は背景
    ]
    found.sort(key=lambda component: component.area_px, reverse=True)
    return tuple(found[:max_count])
