"""`tests/sensing_foundation/synthetic.py` に対するテスト（タスク 1.7）。

観測可能な完了状態（tasks.md 1.7）を固定する:
- 同じ引数で2回生成した系列が完全に一致する（フレーム単位で全比較する）

あわせて、下流タスク（3.2 / 4.6 / 7.2 / 7.3 / 8.1）がこのヘルパへ依存する
前提となる挙動も固定する:
- `make_supplier` が `FrameSupplier`（`Callable[[int], numpy.ndarray | None]`）
  の形（0始まり位置 -> 配列、終端で None）で動くこと
- 欠番のある `seqs` を渡せば、その欠番が供給される配列の内容（埋め込まれた
  `seq`）に反映されること
- `delay_s` を指定すると、その秒数だけ供給が遅くなること
- `violate_at` を指定すると、その位置だけ shape / dtype が目標と矛盾した
  配列が返ること
"""

from __future__ import annotations

import time

import numpy as np
import pytest

from synthetic import (
    DEFAULT_BASE_DEPTH_MM,
    decode_seq_from_frame,
    make_depth_frame,
    make_frame_series,
    make_supplier,
)


class TestMakeDepthFrameDeterminism:
    """`make_depth_frame` が (width_px, height_px, seq) の純関数であることを固定する。"""

    def test_same_arguments_produce_bit_identical_arrays(self) -> None:
        first = make_depth_frame(64, 48, seq=7)
        second = make_depth_frame(64, 48, seq=7)

        assert np.array_equal(first, second)
        assert first.dtype == second.dtype
        assert first.shape == second.shape

    def test_shape_and_dtype(self) -> None:
        depth = make_depth_frame(64, 48, seq=0)
        assert depth.shape == (48, 64)
        assert depth.dtype == np.uint16

    def test_different_seq_produces_different_content(self) -> None:
        a = make_depth_frame(64, 48, seq=0)
        b = make_depth_frame(64, 48, seq=1)
        assert not np.array_equal(a, b)

    def test_different_resolution_produces_different_shape(self) -> None:
        a = make_depth_frame(64, 48, seq=0)
        b = make_depth_frame(32, 24, seq=0)
        assert a.shape != b.shape

    def test_returned_array_is_writeable(self) -> None:
        # 読み取り専用化は CaptureFrame 構築時の責務であり、本関数の責務では
        # ない（呼び出し側が自由に加工できる必要がある）。
        depth = make_depth_frame(8, 8, seq=0)
        depth[0, 1] = 999
        assert depth[0, 1] == 999

    def test_rejects_non_positive_dimensions(self) -> None:
        with pytest.raises(ValueError):
            make_depth_frame(0, 48, seq=0)
        with pytest.raises(ValueError):
            make_depth_frame(64, -1, seq=0)

    def test_base_mm_shifts_baseline(self) -> None:
        low = make_depth_frame(8, 8, seq=5, base_mm=100)
        high = make_depth_frame(8, 8, seq=5, base_mm=DEFAULT_BASE_DEPTH_MM)
        # 左上隅は seq の埋め込みで上書きされるため、それ以外の画素で比較する。
        assert int(high[3, 3]) - int(low[3, 3]) == DEFAULT_BASE_DEPTH_MM - 100


class TestDecodeSeqFromFrame:
    def test_round_trips_seq(self) -> None:
        for seq in (0, 1, 41, 65535):
            depth = make_depth_frame(16, 16, seq=seq)
            assert decode_seq_from_frame(depth) == seq

    def test_wraps_at_65536(self) -> None:
        depth = make_depth_frame(16, 16, seq=65536 + 3)
        assert decode_seq_from_frame(depth) == 3


class TestMakeFrameSeriesDeterminism:
    """観測可能な完了状態: 同じ引数で2回生成した系列が完全に一致する。"""

    def test_two_generations_match_frame_by_frame(self) -> None:
        seqs = [0, 1, 2, 5, 8]
        first_series = make_frame_series(64, 48, seqs)
        second_series = make_frame_series(64, 48, seqs)

        assert len(first_series) == len(second_series) == len(seqs)
        for index, (first_frame, second_frame) in enumerate(zip(first_series, second_series)):
            assert np.array_equal(first_frame, second_frame), f"frame {index} が一致しない"
            assert first_frame.dtype == second_frame.dtype
            assert first_frame.shape == second_frame.shape

    def test_series_order_matches_seqs_order(self) -> None:
        seqs = [3, 1, 4, 1, 5]
        series = make_frame_series(8, 8, seqs)
        recovered = [decode_seq_from_frame(frame) for frame in series]
        # seq=1 が重複するため、埋め込まれた seq の系列は元の seqs と一致する
        # （位置ごとの内容が一意に決まっていることの確認）。
        assert recovered == seqs

    def test_empty_seqs_produces_empty_series(self) -> None:
        assert make_frame_series(8, 8, []) == []


class TestMakeSupplierBasicShape:
    """`make_supplier` が FrameSupplier の形（index -> array/None）で動くことを固定する。"""

    def test_returns_frames_in_order_then_none(self) -> None:
        seqs = [10, 11, 12]
        supplier = make_supplier(16, 12, seqs)

        for index, seq in enumerate(seqs):
            frame = supplier(index)
            assert frame is not None
            assert decode_seq_from_frame(frame) == seq
            assert frame.shape == (12, 16)
            assert frame.dtype == np.uint16

        assert supplier(len(seqs)) is None
        assert supplier(len(seqs) + 5) is None

    def test_negative_index_returns_none(self) -> None:
        supplier = make_supplier(8, 8, [0, 1])
        assert supplier(-1) is None

    def test_empty_seqs_supplier_ends_immediately(self) -> None:
        supplier = make_supplier(8, 8, [])
        assert supplier(0) is None

    def test_content_matches_make_depth_frame(self) -> None:
        seqs = [0, 1, 2]
        supplier = make_supplier(20, 10, seqs)
        for index, seq in enumerate(seqs):
            assert np.array_equal(supplier(index), make_depth_frame(20, 10, seq))

    def test_two_suppliers_with_same_arguments_agree(self) -> None:
        seqs = [0, 2, 4]
        supplier_a = make_supplier(16, 16, seqs)
        supplier_b = make_supplier(16, 16, seqs)
        for index in range(len(seqs)):
            assert np.array_equal(supplier_a(index), supplier_b(index))


class TestMakeSupplierGaps:
    """欠番のある seqs が、供給される配列の内容へそのまま反映されることを固定する。"""

    def test_gappy_seqs_are_reflected_in_frame_identity(self) -> None:
        seqs_with_gap = [0, 1, 3, 4]  # 2 が欠番
        supplier = make_supplier(16, 16, seqs_with_gap)

        recovered = [decode_seq_from_frame(supplier(i)) for i in range(len(seqs_with_gap))]
        assert recovered == seqs_with_gap
        assert 2 not in recovered

    def test_supplier_ends_after_gappy_seqs_exhausted(self) -> None:
        seqs_with_gap = [0, 1, 3, 4]
        supplier = make_supplier(16, 16, seqs_with_gap)
        assert supplier(len(seqs_with_gap)) is None


class TestMakeSupplierSlow:
    """`delay_s` を指定すると、その秒数だけ供給が遅くなることを固定する。"""

    def test_delay_is_applied_before_returning_frame(self) -> None:
        delay_s = 0.03
        supplier = make_supplier(8, 8, [0, 1], delay_s=delay_s)

        start = time.monotonic()
        frame = supplier(0)
        elapsed = time.monotonic() - start

        assert frame is not None
        assert elapsed >= delay_s

    def test_zero_delay_does_not_sleep_noticeably(self) -> None:
        supplier = make_supplier(8, 8, [0, 1], delay_s=0.0)

        start = time.monotonic()
        supplier(0)
        elapsed = time.monotonic() - start

        assert elapsed < 0.03

    def test_delay_not_applied_when_returning_none(self) -> None:
        # 終端（None を返す）では待たない。ドレイン系のテストが終端検出で
        # 無駄に待たされないようにするため。
        supplier = make_supplier(8, 8, [0], delay_s=0.5)
        supplier(0)  # 1枚目を消費する

        start = time.monotonic()
        result = supplier(1)
        elapsed = time.monotonic() - start

        assert result is None
        assert elapsed < 0.1


class TestMakeSupplierContractViolation:
    """`violate_at` / `violate_kind` が意図した契約違反を注入することを固定する。"""

    def test_shape_violation_widens_frame(self) -> None:
        seqs = [0, 1, 2]
        supplier = make_supplier(16, 12, seqs, violate_at=1, violate_kind="shape")

        normal_frame = supplier(0)
        violating_frame = supplier(1)
        other_normal_frame = supplier(2)

        assert normal_frame.shape == (12, 16)
        assert other_normal_frame.shape == (12, 16)
        assert violating_frame.shape == (12, 17)
        assert violating_frame.dtype == np.uint16

    def test_dtype_violation_changes_dtype(self) -> None:
        seqs = [0, 1, 2]
        supplier = make_supplier(16, 12, seqs, violate_at=1, violate_kind="dtype")

        violating_frame = supplier(1)
        assert violating_frame.dtype == np.int32
        assert violating_frame.shape == (12, 16)

    def test_both_violation_changes_shape_and_dtype(self) -> None:
        seqs = [0, 1, 2]
        supplier = make_supplier(16, 12, seqs, violate_at=1, violate_kind="both")

        violating_frame = supplier(1)
        assert violating_frame.shape == (12, 17)
        assert violating_frame.dtype == np.int32

    def test_only_the_targeted_index_violates(self) -> None:
        seqs = [0, 1, 2]
        supplier = make_supplier(16, 12, seqs, violate_at=1, violate_kind="both")

        assert supplier(0).shape == (12, 16)
        assert supplier(0).dtype == np.uint16
        assert supplier(2).shape == (12, 16)
        assert supplier(2).dtype == np.uint16

    def test_default_violate_kind_is_shape(self) -> None:
        supplier = make_supplier(8, 8, [0, 1], violate_at=0)
        assert supplier(0).shape == (8, 9)

    def test_rejects_unknown_violate_kind(self) -> None:
        with pytest.raises(ValueError):
            make_supplier(8, 8, [0, 1], violate_at=0, violate_kind="nonsense")

    def test_rejects_out_of_range_violate_at(self) -> None:
        with pytest.raises(ValueError):
            make_supplier(8, 8, [0, 1], violate_at=5)
        with pytest.raises(ValueError):
            make_supplier(8, 8, [0, 1], violate_at=-1)

    def test_no_violation_by_default(self) -> None:
        supplier = make_supplier(8, 8, [0, 1, 2])
        for index in range(3):
            frame = supplier(index)
            assert frame.shape == (8, 8)
            assert frame.dtype == np.uint16
