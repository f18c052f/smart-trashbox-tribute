"""`detection/masks/frame_diff.py`（タスク 2.3）の検証。

design.md「L4-L5: 検出 → MaskBuilder と3方式」が定める `frame_diff` 方式
（「直前フレームとの Depth 差が `diff_threshold_mm` を超えた画素を前景と
する。状態: 直前フレーム1枚」）について、次を固定する（要件 4.3, 10.1）。

- 初回の `build()` 呼び出しは、内容によらず全 False のマスクを返す
  （直前フレームが無いため差分を取りようがない）
- 静止した合成シーン（直前フレームと同一の Depth 配列）は前景が空になる
  （タスク本文が名指しする観測可能な完了状態その1）
- 移動する塊（一部画素だけが `diff_threshold_mm` を超えて変化）だけが
  前景になる（タスク本文が名指しする観測可能な完了状態その2）
- `diff_threshold_mm` ちょうど・その近傍の境界（「超えた」＝ `>` であり
  `>=` ではない）
- 「無効 ↔ 有効の遷移」を差分として扱わない（要件 10.1）。無効
  （raw=0, `INVALID_DEPTH_RAW`）と有効の間の遷移は、生の数値差がどれほど
  大きくても前景にしない。無効→有効・有効→無効の両方向を確認する
- 両フレームで無効な画素は自明に前景にならないことも明示的に確認する
- `reset()`: 呼び出し後の次の `build()` は、直前に何を見ていたかに関わらず
  再び「初回」として振る舞う
- 非破壊契約: 戻り値は新規配列（入力とメモリを共有しない）、`build()` は
  入力配列を変更しない
- 内部に保持する「直前フレーム」のコピーが呼び出し側の配列と独立している
  こと（`build()` 後に呼び出し側の元配列を書き換えても、次回の `build()`
  の結果が汚染されない）
- `MaskBuilder` の共有シェイプ: `kind` は `DetectorKind.FRAME_DIFF`、
  `ready` は常に `True`
"""

from __future__ import annotations

import numpy as np
import pytest

from flying_object_tracking.detection.masks.frame_diff import FrameDiffMask
from flying_object_tracking.types import DetectorKind

_DIFF_THRESHOLD_MM = 80.0


def _builder(*, diff_threshold_mm: float = _DIFF_THRESHOLD_MM, depth_scale_mm: float = 1.0) -> FrameDiffMask:
    return FrameDiffMask(diff_threshold_mm=diff_threshold_mm, depth_scale_mm=depth_scale_mm)


# --------------------------------------------------------------------------
# 初回フレーム: 前景なし
# --------------------------------------------------------------------------


class TestFirstFrame:
    def test_first_call_returns_all_false_regardless_of_content(self) -> None:
        roi_depth = np.array(
            [[0, 500, 1000, 1500], [2000, 65535, 3000, 0]], dtype=np.uint16
        )
        builder = _builder()

        mask = builder.build(roi_depth)

        assert mask.shape == roi_depth.shape
        assert mask.dtype == bool
        assert not mask.any()


# --------------------------------------------------------------------------
# 観測可能な完了状態: 静止シーンで空、移動する塊のみ前景
# --------------------------------------------------------------------------


class TestStaticVsMovingBlob:
    def test_identical_second_frame_yields_empty_foreground(self) -> None:
        roi_depth = np.array(
            [[1000, 1200, 1400], [1600, 0, 2000], [2200, 2400, 2600]],
            dtype=np.uint16,
        )
        builder = _builder()

        builder.build(roi_depth)  # 初回（前景なしを返す・直前フレームとして保持）
        mask = builder.build(roi_depth.copy())  # 静止: 全く同じ内容の2枚目

        assert not mask.any()

    def test_only_moving_blob_pixels_are_foreground(self) -> None:
        frame1 = np.array(
            [
                [1000, 1000, 1000, 1000],
                [1000, 1000, 1000, 1000],
                [1000, 1000, 1000, 1000],
            ],
            dtype=np.uint16,
        )
        frame2 = frame1.copy()
        # 2x2 の塊だけを閾値を大きく超えて変化させる（移動する物体を模す）。
        frame2[0:2, 1:3] = 1000 + 500
        builder = _builder()

        builder.build(frame1)
        mask = builder.build(frame2)

        expected = np.array(
            [
                [False, True, True, False],
                [False, True, True, False],
                [False, False, False, False],
            ]
        )
        assert np.array_equal(mask, expected)


# --------------------------------------------------------------------------
# 閾値境界（「超えた」= 厳密に上回る場合のみ前景）
# --------------------------------------------------------------------------


class TestThresholdBoundary:
    def test_change_below_threshold_is_not_foreground(self) -> None:
        frame1 = np.array([[1000, 1000]], dtype=np.uint16)
        frame2 = np.array([[1000, 1000 + _DIFF_THRESHOLD_MM - 1]], dtype=np.uint16)
        builder = _builder()

        builder.build(frame1)
        mask = builder.build(frame2)

        assert not mask.any()

    def test_change_exactly_at_threshold_is_not_foreground(self) -> None:
        # 「閾値を超えた」は `>` であり `>=` ではない。ちょうど閾値ぶんの
        # 変化は前景にならない。
        frame1 = np.array([[1000, 1000]], dtype=np.uint16)
        frame2 = np.array([[1000, 1000 + _DIFF_THRESHOLD_MM]], dtype=np.uint16)
        builder = _builder()

        builder.build(frame1)
        mask = builder.build(frame2)

        assert not mask.any()

    def test_change_just_above_threshold_is_foreground(self) -> None:
        frame1 = np.array([[1000, 1000]], dtype=np.uint16)
        frame2 = np.array([[1000, 1000 + _DIFF_THRESHOLD_MM + 1]], dtype=np.uint16)
        builder = _builder()

        builder.build(frame1)
        mask = builder.build(frame2)

        assert np.array_equal(mask, np.array([[False, True]]))


# --------------------------------------------------------------------------
# 無効 <-> 有効の遷移を差分として扱わない（要件 10.1）
# --------------------------------------------------------------------------


class TestInvalidValidTransition:
    def test_invalid_to_valid_transition_is_not_foreground(self) -> None:
        # 直前フレームで無効(raw=0)、今フレームで有効な大きな値。
        # 生の数値差は巨大だが、無効->有効の遷移は前景にしない。
        frame1 = np.array([[0, 1000]], dtype=np.uint16)
        frame2 = np.array([[3000, 1000]], dtype=np.uint16)
        builder = _builder()

        builder.build(frame1)
        mask = builder.build(frame2)

        assert not mask.any()

    def test_valid_to_invalid_transition_is_not_foreground(self) -> None:
        # 直前フレームで有効、今フレームで無効(raw=0)。逆方向も同様。
        frame1 = np.array([[3000, 1000]], dtype=np.uint16)
        frame2 = np.array([[0, 1000]], dtype=np.uint16)
        builder = _builder()

        builder.build(frame1)
        mask = builder.build(frame2)

        assert not mask.any()

    def test_invalid_in_both_frames_stays_not_foreground(self) -> None:
        frame1 = np.array([[0, 1000]], dtype=np.uint16)
        frame2 = np.array([[0, 1000]], dtype=np.uint16)
        builder = _builder()

        builder.build(frame1)
        mask = builder.build(frame2)

        assert not mask.any()


# --------------------------------------------------------------------------
# reset()
# --------------------------------------------------------------------------


class TestReset:
    def test_reset_makes_next_build_behave_like_first_call(self) -> None:
        frame1 = np.array([[1000, 1000]], dtype=np.uint16)
        frame2 = np.array([[1000, 1000 + _DIFF_THRESHOLD_MM + 50]], dtype=np.uint16)
        builder = _builder()

        builder.build(frame1)
        builder.reset()
        mask = builder.build(frame2)  # reset 後なので「初回」扱い

        assert not mask.any()

    def test_reset_is_callable_and_returns_none(self) -> None:
        builder = _builder()

        assert builder.reset() is None


# --------------------------------------------------------------------------
# 非破壊契約・戻り値の形
# --------------------------------------------------------------------------


class TestNonDestructiveContract:
    def test_returns_new_array_not_view(self) -> None:
        roi_depth = np.array([[1000, 1000]], dtype=np.uint16)
        builder = _builder()

        builder.build(roi_depth)
        mask = builder.build(roi_depth)

        assert mask is not roi_depth
        assert not np.shares_memory(mask, roi_depth)
        assert mask.shape == roi_depth.shape
        assert mask.dtype == bool

    def test_current_and_stored_previous_arrays_not_mutated(self) -> None:
        frame1 = np.array([[1000, 1000, 0], [2000, 3000, 0]], dtype=np.uint16)
        frame2 = np.array([[1000, 1200, 100], [2000, 3200, 0]], dtype=np.uint16)
        frame1_before = frame1.tobytes()
        frame2_before = frame2.tobytes()
        builder = _builder()

        builder.build(frame1)
        builder.build(frame2)

        assert frame1.tobytes() == frame1_before
        assert frame2.tobytes() == frame2_before

    def test_writing_to_output_does_not_affect_input(self) -> None:
        frame1 = np.array([[1000, 1000]], dtype=np.uint16)
        frame2 = np.array([[1000, 1000 + _DIFF_THRESHOLD_MM + 1]], dtype=np.uint16)
        builder = _builder()

        builder.build(frame1)
        mask = builder.build(frame2)
        mask[0, 0] = not mask[0, 0]

        assert frame2.tobytes() == np.array(
            [[1000, 1000 + _DIFF_THRESHOLD_MM + 1]], dtype=np.uint16
        ).tobytes()

    def test_stored_previous_frame_independent_of_callers_array(self) -> None:
        # build() 呼び出し後に呼び出し側の元配列を書き換えても、内部で
        # 保持している「直前フレーム」のコピーは影響を受けない
        # （ビューではなく独立したコピーを保持していることの検証）。
        frame1 = np.array([[1000, 1000]], dtype=np.uint16)
        builder = _builder()

        builder.build(frame1)
        frame1[0, 0] = 9999  # 呼び出し側で元配列を後から書き換える（汚染を試みる）

        # 内部の「直前フレーム」が汚染されていれば、同一内容のはずの次の
        # build() で意図しない前景が出る（静止シーンなのに前景ができる）。
        frame_same_as_original = np.array([[1000, 1000]], dtype=np.uint16)
        mask = builder.build(frame_same_as_original)

        assert not mask.any()


# --------------------------------------------------------------------------
# MaskBuilder の共有シェイプ（kind / ready）
# --------------------------------------------------------------------------


class TestSharedShape:
    def test_kind_is_frame_diff(self) -> None:
        builder = _builder()

        assert builder.kind == DetectorKind.FRAME_DIFF

    def test_ready_is_always_true(self) -> None:
        builder = _builder()

        assert builder.ready is True

        builder.build(np.array([[1000]], dtype=np.uint16))
        assert builder.ready is True
