"""`detection/mask_ops.py`（タスク 2.1）の検証。

design.md「L3: 計測と画像処理の基礎 → MaskOps」が定める3関数
（`crop_roi` / `clean_mask` / `components`）について、次を固定する
（要件 1.3, 2.2, 2.6, 3.1）。

- `crop_roi` はコピーせずビューを返し、入力配列を変更しない
- `crop_roi` の軸対応: 戻り値の shape は `(height_px, width_px)` であり、
  `roi.x_px` / `roi.width_px` は配列の**第2軸（列）**、`roi.y_px` /
  `roi.height_px` は**第1軸（行）**に対応する
- `clean_mask` / `components` は新しい配列・値を返し、入力を変更しない
- カーネル指定が両方 0 のとき `clean_mask` は `cv2.morphologyEx` の呼び出し
  自体を省く（処理削減。task 2.1 本文）
- `components` は面積・外接矩形・重心を同時に返し、面積降順で
  `max_count` 件まで切る
- 読み取り専用配列を入力しても全関数が例外なく完走し、入力が変化しない
- 本パッケージで `cv2` を import するのは本モジュールだけである
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest

from flying_object_tracking.detection.mask_ops import (
    Component,
    clean_mask,
    components,
    crop_roi,
)
from flying_object_tracking.types import Roi

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src" / "flying_object_tracking"


def _roi(
    *,
    x_px: int = 0,
    y_px: int = 0,
    width_px: int = 1,
    height_px: int = 1,
    z_min_mm: float | None = None,
    z_max_mm: float | None = None,
) -> Roi:
    return Roi(
        x_px=x_px,
        y_px=y_px,
        width_px=width_px,
        height_px=height_px,
        z_min_mm=z_min_mm,
        z_max_mm=z_max_mm,
    )


# --------------------------------------------------------------------------
# crop_roi
# --------------------------------------------------------------------------


class TestCropRoi:
    def test_returns_view_not_copy(self) -> None:
        depth = np.arange(100, dtype=np.uint16).reshape(10, 10)
        roi = _roi(x_px=2, y_px=3, width_px=4, height_px=5)

        cropped = crop_roi(depth, roi)

        assert np.shares_memory(cropped, depth)

        # ビューへの書き込みが元配列に反映される（コピーではないことの直接証明）。
        cropped[0, 0] = 12345
        assert depth[3, 2] == 12345

    def test_axis_mapping_x_is_columns_y_is_rows(self) -> None:
        # 行×列で値が異なる配列を用意し、(y_px, x_px) が (行, 列) に
        # 対応することを固定する。誤って入れ替わっていれば形状か値がずれる。
        depth = np.arange(8 * 6, dtype=np.int32).reshape(8, 6)  # shape=(height=8, width=6)
        roi = _roi(x_px=1, y_px=2, width_px=3, height_px=4)

        cropped = crop_roi(depth, roi)

        assert cropped.shape == (4, 3)  # (height_px, width_px)
        expected = depth[2:6, 1:4]
        assert np.array_equal(cropped, expected)
        # 個別画素でも軸の対応を確認する。
        assert cropped[0, 0] == depth[2, 1]
        assert cropped[3, 2] == depth[5, 3]

    def test_input_unchanged_after_call(self) -> None:
        depth = np.arange(100, dtype=np.uint16).reshape(10, 10)
        before = depth.copy()
        roi = _roi(x_px=2, y_px=3, width_px=4, height_px=5)

        crop_roi(depth, roi)

        assert np.array_equal(depth, before)

    def test_does_not_raise_on_readonly_input(self) -> None:
        depth = np.arange(100, dtype=np.uint16).reshape(10, 10)
        depth.flags.writeable = False
        roi = _roi(x_px=2, y_px=3, width_px=4, height_px=5)

        cropped = crop_roi(depth, roi)

        assert np.array_equal(cropped, depth[3:8, 2:6])


# --------------------------------------------------------------------------
# clean_mask
# --------------------------------------------------------------------------


class TestCleanMask:
    def test_zero_kernels_pass_through_value_identical_new_array(self) -> None:
        mask = np.zeros((10, 10), dtype=bool)
        mask[2, 2] = True  # 孤立したノイズ点（open=0 なので除去されない想定）
        mask[5:8, 5:8] = True
        mask[6, 6] = False  # 1画素の穴（close=0 なので埋まらない想定）
        before = mask.copy()

        result = clean_mask(mask, open_px=0, close_px=0)

        assert np.array_equal(result, mask)
        assert result is not mask
        assert not np.shares_memory(result, mask)
        assert np.array_equal(mask, before)  # 入力不変

    def test_zero_kernels_skip_cv2_call_entirely(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from flying_object_tracking.detection import mask_ops

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("open_px=close_px=0 のとき cv2.morphologyEx を呼んではならない")

        monkeypatch.setattr(mask_ops.cv2, "morphologyEx", _boom)

        mask = np.zeros((6, 6), dtype=bool)
        mask[1, 1] = True

        result = clean_mask(mask, open_px=0, close_px=0)

        assert np.array_equal(result, mask)

    def test_open_removes_noise_speck(self) -> None:
        mask = np.zeros((15, 15), dtype=bool)
        mask[7, 7] = True  # 孤立したノイズ点（1画素）
        before = mask.copy()

        result = clean_mask(mask, open_px=3, close_px=0)

        assert result[7, 7] == False  # noqa: E712 - ndarray bool 比較の明示
        assert not result.any()
        assert np.array_equal(mask, before)  # 入力不変
        assert not np.shares_memory(result, mask)

    def test_close_fills_gap(self) -> None:
        mask = np.zeros((15, 15), dtype=bool)
        mask[3:10, 3:10] = True
        mask[6, 6] = False  # ブロブ内の1画素の穴
        before = mask.copy()

        result = clean_mask(mask, open_px=0, close_px=3)

        assert result[6, 6] == True  # noqa: E712
        assert np.array_equal(mask, before)  # 入力不変
        assert not np.shares_memory(result, mask)

    def test_output_is_new_array_for_nonzero_kernels(self) -> None:
        mask = np.zeros((10, 10), dtype=bool)
        mask[3:6, 3:6] = True

        result = clean_mask(mask, open_px=3, close_px=3)

        assert not np.shares_memory(result, mask)
        assert result.dtype == bool


# --------------------------------------------------------------------------
# components
# --------------------------------------------------------------------------


class TestComponents:
    def _three_blob_mask(self) -> np.ndarray:
        mask = np.zeros((20, 20), dtype=bool)
        mask[1:5, 1:5] = True  # blob A: x=1,y=1,w=4,h=4, area=16
        mask[10:12, 10:12] = True  # blob B: x=10,y=10,w=2,h=2, area=4
        mask[2:5, 15:18] = True  # blob C: x=15,y=2,w=3,h=3, area=9
        return mask

    def test_returns_area_sorted_descending_with_expected_fields(self) -> None:
        mask = self._three_blob_mask()

        result = components(mask, max_count=10)

        assert len(result) == 3
        areas = [c.area_px for c in result]
        assert areas == sorted(areas, reverse=True)
        assert areas == [16, 9, 4]

        blob_a = result[0]
        assert isinstance(blob_a, Component)
        assert (blob_a.x_px, blob_a.y_px, blob_a.w_px, blob_a.h_px) == (1, 1, 4, 4)
        assert blob_a.area_px == 16
        assert blob_a.cx_px == pytest.approx(2.5)
        assert blob_a.cy_px == pytest.approx(2.5)

    def test_max_count_truncates_smallest_blobs(self) -> None:
        mask = self._three_blob_mask()

        result = components(mask, max_count=2)

        assert len(result) == 2
        assert [c.area_px for c in result] == [16, 9]

    def test_input_unchanged_after_call(self) -> None:
        mask = self._three_blob_mask()
        before = mask.copy()

        components(mask, max_count=10)

        assert np.array_equal(mask, before)

    def test_empty_mask_returns_empty_tuple(self) -> None:
        mask = np.zeros((10, 10), dtype=bool)

        result = components(mask, max_count=10)

        assert result == ()


# --------------------------------------------------------------------------
# 非破壊・読み取り専用配列に対する完走性（タスクの「観測可能な完了状態」）
# --------------------------------------------------------------------------


class TestReadOnlySmoke:
    def test_full_chain_completes_without_exception_and_preserves_input(self) -> None:
        depth = (np.arange(400, dtype=np.uint16).reshape(20, 20) % 5000) + 500
        depth.flags.writeable = False
        before = depth.copy()
        roi = _roi(x_px=2, y_px=2, width_px=10, height_px=10)

        cropped = crop_roi(depth, roi)
        # 比較演算は読み取りのみで完結するため、新規の書き込み可能配列が得られる。
        mask = cropped > (cropped.min() + 1)
        mask.flags.writeable = False

        cleaned = clean_mask(mask, open_px=2, close_px=2)
        found = components(cleaned, max_count=5)

        assert isinstance(found, tuple)
        assert np.array_equal(depth, before)

    def test_clean_mask_does_not_raise_on_readonly_mask(self) -> None:
        mask = np.zeros((10, 10), dtype=bool)
        mask[3:6, 3:6] = True
        mask.flags.writeable = False

        result = clean_mask(mask, open_px=3, close_px=3)

        assert result.any()

    def test_components_does_not_raise_on_readonly_mask(self) -> None:
        mask = np.zeros((10, 10), dtype=bool)
        mask[3:6, 3:6] = True
        mask.flags.writeable = False

        result = components(mask, max_count=5)

        assert len(result) == 1


# --------------------------------------------------------------------------
# 境界の軽量ガード（このタスクの diff 内で自己完結する簡易チェック）
# --------------------------------------------------------------------------


def test_mask_ops_is_the_only_module_importing_cv2_in_this_tasks_diff() -> None:
    """`src/flying_object_tracking/**` のうち `cv2` を import するのは
    `detection/mask_ops.py` だけであることを確認する（design.md
    Implementation Notes / Integration）。

    フルの境界走査（`prediction_core` 等も含む）はタスク 8.2 の
    `test_boundaries.py` が持つ。ここでは本タスクが追加する自己の約束が
    最初から破れていないことだけを、軽量な正規表現で確認する。
    """
    cv2_import_pattern = re.compile(r"^\s*(import cv2\b|from cv2\b)", re.MULTILINE)

    offenders: list[str] = []
    for py_file in _SRC_ROOT.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        if cv2_import_pattern.search(text):
            offenders.append(str(py_file.relative_to(_SRC_ROOT)))

    assert offenders == [str(Path("detection") / "mask_ops.py")]
