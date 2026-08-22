"""`detection/masks/depth_band.py`（タスク 2.2）の検証。

design.md「L4-L5: 検出 → MaskBuilder と3方式」が定める `depth_band` 方式
（「ROI 内で `z_min_mm <= z <= z_max_mm` を前景とする。状態: 無し」）について、
次を固定する（要件 4.3, 10.1）。

- 距離帯の内側かつ有効な Depth 画素だけが前景（`True`）になる
- 無効 Depth（raw=0, `INVALID_DEPTH_RAW`）は、たとえ `0 mm` が距離帯に
  含まれる設定であっても前景にならない（`is_valid_depth()` を明示的に
  使わず `raw == 0` を素朴に判定していた場合に見逃す境界ケース）
- `build()` の戻り値は新規割り当ての bool 配列であり、入力とメモリを
  共有しない
- `build()` は入力配列を変更しない
- `kind` は `DetectorKind.DEPTH_BAND` を返す
- `ready` は常に `True`（背景モデルのような初期化状態を持たないため）
- `reset()` は呼び出し可能な no-op（`depth_band` は状態を持たないため、
  呼び出し前後で `build()` の挙動が変わらない）
- `z_min_mm` / `z_max_mm` が `None`（無制限）の場合の挙動
- `depth_scale_mm` による raw→mm 換算が実際に使われている
  （`sensing_foundation.depth_raw_to_mm` 経由。raw 値をそのまま mm として
  扱っていないことを、素朴な換算では band 外/内が入れ替わる入力で確認する）
"""

from __future__ import annotations

import numpy as np
import pytest

from flying_object_tracking.detection.masks.depth_band import DepthBandMask
from flying_object_tracking.types import DetectorKind, Roi


def _roi(*, z_min_mm: float | None, z_max_mm: float | None) -> Roi:
    """`build()` はROI切り出し済み配列を受け取るため、画素矩形は使わない値で埋める。"""
    return Roi(x_px=0, y_px=0, width_px=4, height_px=4, z_min_mm=z_min_mm, z_max_mm=z_max_mm)


# --------------------------------------------------------------------------
# 距離帯ゲートの基本動作
# --------------------------------------------------------------------------


class TestBandGating:
    def test_only_inside_band_and_valid_pixels_are_foreground(self) -> None:
        # depth_scale_mm=1.0 なので raw==mm。z_min=1000, z_max=2000。
        roi_depth = np.array(
            [
                [0, 500, 1000, 1500],  # 無効 / 帯下 / 帯内(下端) / 帯内
                [2000, 2500, 3000, 0],  # 帯内(上端) / 帯上 / 帯上 / 無効
                [1200, 1800, 0, 500],  # 帯内 / 帯内 / 無効 / 帯下
                [999, 2001, 1000, 2000],  # 帯下 / 帯上 / 帯内(下端) / 帯内(上端)
            ],
            dtype=np.uint16,
        )
        roi = _roi(z_min_mm=1000.0, z_max_mm=2000.0)
        builder = DepthBandMask(roi=roi, depth_scale_mm=1.0)

        mask = builder.build(roi_depth)

        expected = np.array(
            [
                [False, False, True, True],
                [True, False, False, False],
                [True, True, False, False],
                [False, False, True, True],
            ]
        )
        assert np.array_equal(mask, expected)

    def test_invalid_pixel_not_foreground_even_when_zero_mm_is_inside_band(self) -> None:
        # z_min_mm=0.0 の帯は 0 mm を含むが、raw=0 は測距不能画素であり、
        # 素朴な数値比較（0 <= raw <= z_max）だと誤って前景になってしまう
        # 境界ケース。`is_valid_depth()` を明示的に使っていれば弾ける。
        roi_depth = np.array(
            [
                [0, 100],
                [500, 0],
            ],
            dtype=np.uint16,
        )
        roi = _roi(z_min_mm=0.0, z_max_mm=1000.0)
        builder = DepthBandMask(roi=roi, depth_scale_mm=1.0)

        mask = builder.build(roi_depth)

        expected = np.array(
            [
                [False, True],
                [True, False],
            ]
        )
        assert np.array_equal(mask, expected)

    def test_negative_lower_bound_does_not_admit_invalid_pixel(self) -> None:
        # z_min_mm が負（0 を跨ぐ帯）でも、無効画素(raw=0)は依然として除外される。
        roi_depth = np.array([[0, 50]], dtype=np.uint16)
        roi = _roi(z_min_mm=-100.0, z_max_mm=100.0)
        builder = DepthBandMask(roi=roi, depth_scale_mm=1.0)

        mask = builder.build(roi_depth)

        assert np.array_equal(mask, np.array([[False, True]]))


# --------------------------------------------------------------------------
# 無制限の距離帯（z_min_mm / z_max_mm が None）
# --------------------------------------------------------------------------


class TestUnboundedBand:
    def test_z_min_none_means_no_lower_bound(self) -> None:
        roi_depth = np.array([[10, 5000]], dtype=np.uint16)
        roi = _roi(z_min_mm=None, z_max_mm=6000.0)
        builder = DepthBandMask(roi=roi, depth_scale_mm=1.0)

        mask = builder.build(roi_depth)

        # 10mm は下限が無いので前景。5000mm も上限(6000)内なので前景。
        assert np.array_equal(mask, np.array([[True, True]]))

    def test_z_max_none_means_no_upper_bound(self) -> None:
        roi_depth = np.array([[100, 60000]], dtype=np.uint16)
        roi = _roi(z_min_mm=50.0, z_max_mm=None)
        builder = DepthBandMask(roi=roi, depth_scale_mm=1.0)

        mask = builder.build(roi_depth)

        assert np.array_equal(mask, np.array([[True, True]]))

    def test_both_none_means_all_valid_pixels_are_foreground(self) -> None:
        roi_depth = np.array([[0, 1, 65535]], dtype=np.uint16)
        roi = _roi(z_min_mm=None, z_max_mm=None)
        builder = DepthBandMask(roi=roi, depth_scale_mm=1.0)

        mask = builder.build(roi_depth)

        assert np.array_equal(mask, np.array([[False, True, True]]))


# --------------------------------------------------------------------------
# depth_scale_mm による raw -> mm 換算
# --------------------------------------------------------------------------


class TestDepthScaleConversion:
    def test_depth_scale_mm_is_applied_before_band_comparison(self) -> None:
        # depth_scale_mm=2.0: raw=1000 -> 2000mm（帯内）、raw=100 -> 200mm（帯外）。
        # raw をそのまま mm として比較する誤実装だと、raw=1000 は帯(1500-2500)外
        # と判定されてしまうため、この期待値は換算が実際に効いていることを示す。
        roi_depth = np.array([[100, 1000]], dtype=np.uint16)
        roi = _roi(z_min_mm=1500.0, z_max_mm=2500.0)
        builder = DepthBandMask(roi=roi, depth_scale_mm=2.0)

        mask = builder.build(roi_depth)

        assert np.array_equal(mask, np.array([[False, True]]))


# --------------------------------------------------------------------------
# 非破壊契約・戻り値の形
# --------------------------------------------------------------------------


class TestNonDestructiveContract:
    def test_returns_new_array_not_view(self) -> None:
        roi_depth = np.array([[1000, 1000], [1000, 1000]], dtype=np.uint16)
        roi = _roi(z_min_mm=500.0, z_max_mm=2000.0)
        builder = DepthBandMask(roi=roi, depth_scale_mm=1.0)

        mask = builder.build(roi_depth)

        assert mask is not roi_depth
        assert not np.shares_memory(mask, roi_depth)
        assert mask.shape == roi_depth.shape
        assert mask.dtype == bool

    def test_input_not_mutated(self) -> None:
        roi_depth = np.array(
            [[0, 500, 1000, 1500], [2000, 2500, 3000, 0]], dtype=np.uint16
        )
        before_bytes = roi_depth.tobytes()
        roi = _roi(z_min_mm=1000.0, z_max_mm=2000.0)
        builder = DepthBandMask(roi=roi, depth_scale_mm=1.0)

        builder.build(roi_depth)

        assert roi_depth.tobytes() == before_bytes

    def test_writing_to_output_does_not_affect_input(self) -> None:
        roi_depth = np.array([[1000, 1000]], dtype=np.uint16)
        roi = _roi(z_min_mm=500.0, z_max_mm=2000.0)
        builder = DepthBandMask(roi=roi, depth_scale_mm=1.0)

        mask = builder.build(roi_depth)
        mask[0, 0] = not mask[0, 0]

        assert roi_depth.tobytes() == np.array([[1000, 1000]], dtype=np.uint16).tobytes()


# --------------------------------------------------------------------------
# MaskBuilder の共有シェイプ（kind / ready / reset）
# --------------------------------------------------------------------------


class TestSharedShape:
    def test_kind_is_depth_band(self) -> None:
        roi = _roi(z_min_mm=500.0, z_max_mm=2000.0)
        builder = DepthBandMask(roi=roi, depth_scale_mm=1.0)

        assert builder.kind == DetectorKind.DEPTH_BAND

    def test_ready_is_always_true(self) -> None:
        roi = _roi(z_min_mm=500.0, z_max_mm=2000.0)
        builder = DepthBandMask(roi=roi, depth_scale_mm=1.0)

        assert builder.ready is True

        # build() を呼んだ前後でも変化しない（状態を持たないため）。
        builder.build(np.array([[1000]], dtype=np.uint16))
        assert builder.ready is True

    def test_reset_is_callable_and_returns_none(self) -> None:
        roi = _roi(z_min_mm=500.0, z_max_mm=2000.0)
        builder = DepthBandMask(roi=roi, depth_scale_mm=1.0)

        assert builder.reset() is None

    def test_reset_does_not_change_subsequent_build_behavior(self) -> None:
        roi_depth = np.array(
            [[0, 500, 1000, 1500], [2000, 2500, 3000, 0]], dtype=np.uint16
        )
        roi = _roi(z_min_mm=1000.0, z_max_mm=2000.0)
        builder = DepthBandMask(roi=roi, depth_scale_mm=1.0)

        before_reset = builder.build(roi_depth)
        builder.reset()
        after_reset = builder.build(roi_depth)

        assert np.array_equal(before_reset, after_reset)
