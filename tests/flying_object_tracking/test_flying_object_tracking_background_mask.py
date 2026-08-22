"""`detection/masks/background.py`（タスク 2.4）の検証。

design.md「L4-L5: 検出 → MaskBuilder と3方式」が定める `background` 方式
（「初期 N フレームから作った背景 Depth モデルとの差が閾値を超えた画素を
前景とする。状態: 背景モデル1枚」）について、次を固定する（要件 4.3, 10.1）。

- `ready` は最初の `background_frames` 回の `build()` 呼び出しが終わるまで
  `False` であり、その間 `build()` は内容に関わらず全 False のマスクを返す
  （タスク本文「初期化中は前景なしを返す（例外にしない）」）
- `background_frames` 回ちょうど呼び出した後に `ready` が `True` になる
- 観測可能な完了状態: 初期化枚数に達するまで前景が空であり、達した後に
  移動する塊のみが前景になる（タスク本文が名指しする固定対象）
- 無効画素（raw=0, `INVALID_DEPTH_RAW`）は背景モデルにも前景にも含めない
  （タスク本文）。初期化中に一部フレームだけ無効だった画素の背景値が、
  有効サンプルだけの平均になり、無効サンプルによって 0 側に汚染されない
  ことを固定する
- 初期化完了後のクエリフレームで無効な画素は、学習済み背景値が何であれ
  前景にならない（`frame_diff` の無効遷移テストと同じ精神）
- 既定 `background_update_rate=0.0`: 一度学習した背景モデルは以後の
  `build()` 呼び出しでは更新されず凍結される（タスク本文「既定は更新しない
  （飛翔体を背景へ取り込まないため）」）
- `background_update_rate > 0.0`: 背景モデルは新しい静止内容へ実際に
  ドリフトする。ただし前景と判定された画素は更新対象から除外されるため、
  持続的に異なる塊（飛翔体を模す）は背景に吸収されない
- `reset()`: 呼び出し後、`ready` は再び `False` になり、次の
  `background_frames` 回は新規初期化として振る舞う
- 非破壊契約: 戻り値は新規配列（入力とメモリを共有しない）、`build()` は
  入力配列を変更しない
- 内部に保持する背景モデルが、初期化に使った呼び出し側の配列と独立して
  いること（初期化後に呼び出し側の元配列を書き換えても、学習済み背景が
  汚染されない）
- `MaskBuilder` の共有シェイプ: `kind` は `DetectorKind.BACKGROUND`
"""

from __future__ import annotations

import numpy as np
import pytest

from flying_object_tracking.detection.masks.background import BackgroundMask
from flying_object_tracking.types import DetectorKind

_BACKGROUND_FRAMES = 3
_DIFF_THRESHOLD_MM = 80.0


def _builder(
    *,
    background_frames: int = _BACKGROUND_FRAMES,
    diff_threshold_mm: float = _DIFF_THRESHOLD_MM,
    background_update_rate: float = 0.0,
    depth_scale_mm: float = 1.0,
) -> BackgroundMask:
    return BackgroundMask(
        background_frames=background_frames,
        diff_threshold_mm=diff_threshold_mm,
        background_update_rate=background_update_rate,
        depth_scale_mm=depth_scale_mm,
    )


# --------------------------------------------------------------------------
# 初期化中の ready ゲートと全 False 契約
# --------------------------------------------------------------------------


class TestInitializationGating:
    def test_ready_false_before_any_build_and_through_init_window(self) -> None:
        frame = np.array([[1000, 1000], [1000, 1000]], dtype=np.uint16)
        builder = _builder(background_frames=_BACKGROUND_FRAMES)

        assert builder.ready is False

        for _ in range(_BACKGROUND_FRAMES - 1):
            mask = builder.build(frame)
            assert builder.ready is False
            assert not mask.any()

    def test_ready_true_after_exactly_background_frames_calls(self) -> None:
        frame = np.array([[1000, 1000], [1000, 1000]], dtype=np.uint16)
        builder = _builder(background_frames=_BACKGROUND_FRAMES)

        for _ in range(_BACKGROUND_FRAMES):
            builder.build(frame)

        assert builder.ready is True

    def test_build_returns_all_false_during_init_even_with_moving_blob(self) -> None:
        # 初期化中に内容が毎回変わる（移動する塊を模す）場合でも、
        # 初期化完了までは全 False を返す（タスク本文の明示要件）。
        frames = [
            np.array([[1000, 1000], [1000, 1000]], dtype=np.uint16),
            np.array([[1000, 5000], [1000, 1000]], dtype=np.uint16),
            np.array([[1000, 1000], [5000, 1000]], dtype=np.uint16),
        ]
        builder = _builder(background_frames=len(frames))

        for index, frame in enumerate(frames):
            mask = builder.build(frame)
            assert not mask.any()
            expected_ready = index == len(frames) - 1
            assert builder.ready is expected_ready


# --------------------------------------------------------------------------
# 観測可能な完了状態: 静止シーンで空、移動する塊のみ前景
# --------------------------------------------------------------------------


class TestStaticVsMovingBlobAfterReady:
    def test_static_scene_matching_background_yields_empty_foreground(self) -> None:
        frame = np.array([[1000, 1000], [1000, 1000]], dtype=np.uint16)
        builder = _builder(background_frames=2)
        builder.build(frame)
        builder.build(frame)
        assert builder.ready is True

        mask = builder.build(frame.copy())

        assert not mask.any()

    def test_blob_shifted_beyond_threshold_is_foreground_at_blob_pixels_only(self) -> None:
        background_frame = np.array(
            [[1000, 1000, 1000], [1000, 1000, 1000]], dtype=np.uint16
        )
        builder = _builder(background_frames=2)
        builder.build(background_frame)
        builder.build(background_frame)
        assert builder.ready is True

        query = background_frame.copy()
        query[0, 1:3] = 1000 + _DIFF_THRESHOLD_MM + 50  # 2画素の塊だけ大きく変化

        mask = builder.build(query)

        expected = np.array(
            [[False, True, True], [False, False, False]]
        )
        assert np.array_equal(mask, expected)


# --------------------------------------------------------------------------
# 無効画素を背景モデルにも前景にも含めない（タスク本文 / 要件 10.1）
# --------------------------------------------------------------------------


class TestInvalidPixelHandling:
    def test_invalid_sample_during_init_does_not_corrupt_valid_neighbor_average(
        self,
    ) -> None:
        # pixel(0,0): 初期化中2回無効、1回だけ有効(1200)。
        # pixel(0,1): 常に有効(1000)。
        frame1 = np.array([[0, 1000]], dtype=np.uint16)
        frame2 = np.array([[0, 1000]], dtype=np.uint16)
        frame3 = np.array([[1200, 1000]], dtype=np.uint16)
        builder = _builder(background_frames=3)

        builder.build(frame1)
        builder.build(frame2)
        builder.build(frame3)
        assert builder.ready is True

        # 学習済み背景が有効サンプルだけの平均（pixel0=1200, pixel1=1000）
        # であれば、この問い合わせは前景なしになるはず。
        # もし無効サンプルを 0 として平均に混入していたら
        # pixel0 の背景は (0+0+1200)/3=400 になり、1200 との差(800)が
        # 閾値を超えて誤って前景になってしまう。
        query = np.array([[1200, 1000]], dtype=np.uint16)
        mask = builder.build(query)

        assert not mask.any()

    def test_invalid_pixel_in_query_frame_is_never_foreground(self) -> None:
        frame = np.array([[1000, 1000]], dtype=np.uint16)
        builder = _builder(background_frames=2)
        builder.build(frame)
        builder.build(frame)
        assert builder.ready is True

        # 背景は 1000 だが、今回のクエリでは pixel0 が無効(raw=0)。
        # 生の数値差は大きいが、無効画素は前景にしない。
        query = np.array([[0, 1000]], dtype=np.uint16)
        mask = builder.build(query)

        assert not mask.any()


# --------------------------------------------------------------------------
# 既定 background_update_rate=0.0: 背景は凍結される
# --------------------------------------------------------------------------


class TestDefaultUpdateRateFreezesBackground:
    def test_frozen_background_keeps_classifying_original_blob_as_foreground(
        self,
    ) -> None:
        frame = np.array([[1000, 1000]], dtype=np.uint16)
        builder = _builder(background_frames=2, background_update_rate=0.0)
        builder.build(frame)
        builder.build(frame)
        assert builder.ready is True

        # 閾値未満の微小な変化を何度も与える（もし既定で更新されるバグが
        # あれば、背景が少しずつドリフトしてしまう）。
        drift_candidate = np.array([[1000 + _DIFF_THRESHOLD_MM - 20, 1000]], dtype=np.uint16)
        for _ in range(20):
            mask = builder.build(drift_candidate.copy())
            assert not mask.any()  # 閾値未満なのでこれ自体は前景ではない

        # 背景が凍結されたままなら、元の背景(1000)から見て閾値を超える
        # 変化は今なお前景と判定されるはず。
        original_blob = np.array([[1000 + _DIFF_THRESHOLD_MM + 1, 1000]], dtype=np.uint16)
        mask = builder.build(original_blob)

        assert np.array_equal(mask, np.array([[True, False]]))


# --------------------------------------------------------------------------
# background_update_rate > 0.0: 背景は実際にドリフトするが、前景は
# 更新対象から除外されるため吸収されない
# --------------------------------------------------------------------------


class TestPositiveUpdateRate:
    def test_background_drifts_toward_new_static_content_over_repeated_calls(
        self,
    ) -> None:
        frame = np.array([[1000]], dtype=np.uint16)

        # 対比用: 勾配なしにいきなり大きく変化した内容を見せた場合は
        # 前景と判定されることを先に確認する（ベースラインの確認）。
        jump_builder = _builder(background_frames=2, background_update_rate=1.0)
        jump_builder.build(frame)
        jump_builder.build(frame)
        far_content = np.array([[1250]], dtype=np.uint16)
        jump_mask = jump_builder.build(far_content)
        assert np.array_equal(jump_mask, np.array([[True]]))

        # 本題: 閾値未満の小刻みな変化を繰り返し与えて徐々にドリフトさせる。
        # 各ステップの差分(50)は閾値(80)未満なので毎回「前景ではない」と
        # 判定され、更新対象に含まれ続ける。
        drift_builder = _builder(background_frames=2, background_update_rate=1.0)
        drift_builder.build(frame)
        drift_builder.build(frame)
        assert drift_builder.ready is True

        content = 1000.0
        for _ in range(5):
            content += 50.0
            step_frame = np.array([[content]], dtype=np.uint16)
            mask = drift_builder.build(step_frame)
            assert not mask.any()  # 各ステップは閾値未満なので前景ではない

        assert content == 1250.0  # 元の背景(1000)から見れば閾値を大きく超える

        # 背景が実際に 1250 までドリフトしていれば、この問い合わせは
        # もう前景にならない（＝「元々前景だった内容が背景になった」）。
        final_mask = drift_builder.build(np.array([[1250]], dtype=np.uint16))
        assert not final_mask.any()

    def test_persistent_blob_is_not_absorbed_even_with_positive_update_rate(
        self,
    ) -> None:
        frame = np.array([[1000, 1000]], dtype=np.uint16)
        builder = _builder(background_frames=2, background_update_rate=1.0)
        builder.build(frame)
        builder.build(frame)
        assert builder.ready is True

        persistent_blob = np.array(
            [[1000, 1000 + _DIFF_THRESHOLD_MM + 50]], dtype=np.uint16
        )
        for _ in range(10):
            mask = builder.build(persistent_blob.copy())
            # pixel0 は常に背景と一致（前景でない）。pixel1 は常に閾値を
            # 超えるので前景として除外され続け、更新されない。
            assert np.array_equal(mask, np.array([[False, True]]))

        # 10 回更新を試みた後でも pixel1 は依然として前景のまま
        # （背景に吸収されていない）。
        final_mask = builder.build(persistent_blob.copy())
        assert np.array_equal(final_mask, np.array([[False, True]]))


# --------------------------------------------------------------------------
# reset()
# --------------------------------------------------------------------------


class TestReset:
    def test_reset_returns_to_not_ready_and_next_window_behaves_like_fresh_init(
        self,
    ) -> None:
        old_background = np.array([[1000, 1000]], dtype=np.uint16)
        builder = _builder(background_frames=2)
        builder.build(old_background)
        builder.build(old_background)
        assert builder.ready is True

        builder.reset()
        assert builder.ready is False

        # reset 後の新しい初期化窓（古い背景 1000 とは全く異なる内容）。
        new_background = np.array([[3000, 3000]], dtype=np.uint16)
        first = builder.build(new_background)
        assert not first.any()
        assert builder.ready is False
        second = builder.build(new_background)
        assert not second.any()
        assert builder.ready is True

        # 古い背景(1000)ではなく新しい背景(3000)が学習されていることを確認。
        mask_at_new_background = builder.build(new_background.copy())
        assert not mask_at_new_background.any()

        mask_at_old_background = builder.build(old_background)
        assert mask_at_old_background.any()

    def test_reset_is_callable_and_returns_none(self) -> None:
        builder = _builder()

        assert builder.reset() is None


# --------------------------------------------------------------------------
# 非破壊契約・戻り値の形・内部状態の独立性
# --------------------------------------------------------------------------


class TestNonDestructiveContract:
    def test_returns_new_array_not_view(self) -> None:
        frame = np.array([[1000, 1000]], dtype=np.uint16)
        builder = _builder(background_frames=2)
        builder.build(frame)
        builder.build(frame)

        mask = builder.build(frame)

        assert mask is not frame
        assert not np.shares_memory(mask, frame)
        assert mask.shape == frame.shape
        assert mask.dtype == bool

    def test_input_not_mutated_during_init_and_after_ready(self) -> None:
        frame = np.array([[1000, 1200, 0], [1400, 1600, 0]], dtype=np.uint16)
        before_bytes = frame.tobytes()
        builder = _builder(background_frames=2)

        builder.build(frame)  # 初期化中
        assert frame.tobytes() == before_bytes

        builder.build(frame)  # ready になる
        assert frame.tobytes() == before_bytes

        builder.build(frame)  # ready 後のクエリ
        assert frame.tobytes() == before_bytes

    def test_writing_to_output_does_not_affect_input(self) -> None:
        frame = np.array([[1000, 1000]], dtype=np.uint16)
        builder = _builder(background_frames=2)
        builder.build(frame)
        builder.build(frame)

        mask = builder.build(frame)
        mask[0, 0] = not mask[0, 0]

        assert frame.tobytes() == np.array([[1000, 1000]], dtype=np.uint16).tobytes()

    def test_internal_background_model_independent_of_callers_init_arrays(
        self,
    ) -> None:
        # 初期化に使った呼び出し側の配列を build() 呼び出し後に書き換えても、
        # 内部で学習した背景モデルは影響を受けない（2.3 のレビューで
        # 固定されたエイリアシング検証と同じ観点）。
        frame1 = np.array([[1000, 1000]], dtype=np.uint16)
        frame2 = np.array([[1000, 1000]], dtype=np.uint16)
        builder = _builder(background_frames=2)

        builder.build(frame1)
        frame1[0, 0] = 9999  # 1枚目の呼び出し側配列を後から書き換える
        builder.build(frame2)
        frame2[0, 0] = 8888  # 2枚目も同様
        assert builder.ready is True

        # 内部背景が汚染されていなければ、元の値(1000)と一致するクエリは
        # 前景なしになるはず。
        query = np.array([[1000, 1000]], dtype=np.uint16)
        mask = builder.build(query)

        assert not mask.any()


# --------------------------------------------------------------------------
# MaskBuilder の共有シェイプ（kind）
# --------------------------------------------------------------------------


class TestSharedShape:
    def test_kind_is_background(self) -> None:
        builder = _builder()

        assert builder.kind == DetectorKind.BACKGROUND
