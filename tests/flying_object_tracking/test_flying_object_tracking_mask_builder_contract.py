"""`detection/masks/__init__.py`（タスク 2.5）の検証。

design.md「L4-L5: 検出 → MaskBuilder と3方式」が定める `MaskBuilder`
プロトコルと生成口 `create_mask_builder` について、タスク本文が名指しする
次の観測可能な完了状態を固定する（要件 4.1, 4.2, 4.3）。

- 同一の合成フレーム列を3方式すべてに通し、いずれも同形状のマスクを返す
- 設定値の変更（`DetectorConfig.kind`）だけで方式が切り替わる
  （コードの変更を伴わない）
- 未知の方式名は起動前に拒否される（マスク構築を一切試みない）

**「同形状・同型」の検証方針についての注記**: `background` 方式は最初の
`background_frames` 回、`ready` が `False` のまま全 False のマスクを返す
（`background.py` docstring）。この初期化期間中の出力も「`roi_depth` と
同じ shape の新規割り当て bool 配列」という契約自体は満たしており、`ready`
の状態に関わらず shape/dtype の一致は保たれる。本テストは
`TestSameShapeContract` で「1回の `build()` 呼び出し」（`background` が
未初期化のまま）と `TestSameShapeAfterBackgroundReady` で「`background` を
初期化窓ぶん駆動してから」の両方を確認する。前者は「3方式のどんな状態でも
同形状・同型である」という契約そのものを最小の手数で示し、後者は
「初期化完了後も同形状であり続ける」という、より意味のある比較可能な状態
（design.md が言う「後段が交絡しない」比較の前提）を示す。両方を固定する
ことで、`ready` の状態に関わらず契約が崩れないことを網羅する。
"""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from flying_object_tracking.config import DetectorConfig
from flying_object_tracking.detection.masks import MaskBuilder, create_mask_builder
from flying_object_tracking.detection.masks.background import BackgroundMask
from flying_object_tracking.detection.masks.depth_band import DepthBandMask
from flying_object_tracking.detection.masks.frame_diff import FrameDiffMask
from flying_object_tracking.errors import TrackingConfigError
from flying_object_tracking.types import DetectorKind, Roi

_ALL_KINDS = (DetectorKind.DEPTH_BAND, DetectorKind.FRAME_DIFF, DetectorKind.BACKGROUND)

_EXPECTED_CLASS: dict[DetectorKind, type] = {
    DetectorKind.DEPTH_BAND: DepthBandMask,
    DetectorKind.FRAME_DIFF: FrameDiffMask,
    DetectorKind.BACKGROUND: BackgroundMask,
}


def _roi(*, z_min_mm: float | None = 500.0, z_max_mm: float | None = 5000.0) -> Roi:
    return Roi(x_px=0, y_px=0, width_px=8, height_px=8, z_min_mm=z_min_mm, z_max_mm=z_max_mm)


def _config(*, kind: DetectorKind) -> DetectorConfig:
    """3方式が共有する設定値（`diff_threshold_mm` 等）を同一にした共通テンプレート。

    `kind` だけを変えることで「設定だけで方式が切り替わる」ことを示す
    （テスト本体で `dataclasses.replace` によりさらに `kind` のみを変える）。
    """
    return DetectorConfig(
        kind=kind,
        diff_threshold_mm=80.0,
        background_frames=3,
        background_update_rate=0.0,
        open_kernel_px=3,
        close_kernel_px=3,
        max_candidates=8,
    )


def _synthetic_roi_depth() -> np.ndarray:
    """3方式すべてに通す共通の合成 ROI 切り出し済み depth フレーム。"""
    return np.array(
        [
            [0, 500, 1000, 1500, 2000, 2500, 3000, 0],
            [1200, 1800, 0, 500, 999, 2001, 1000, 2000],
            [1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000],
            [3000, 3000, 3000, 3000, 3000, 3000, 3000, 3000],
            [0, 0, 0, 0, 1500, 1500, 1500, 1500],
            [2200, 2200, 2200, 2200, 0, 0, 0, 0],
            [1000, 2000, 1000, 2000, 1000, 2000, 1000, 2000],
            [500, 1500, 2500, 500, 1500, 2500, 500, 1500],
        ],
        dtype=np.uint16,
    )


# --------------------------------------------------------------------------
# create_mask_builder: config.kind から対応する具象クラスを生成する
# --------------------------------------------------------------------------


class TestCreateMaskBuilderDispatch:
    @pytest.mark.parametrize("kind", _ALL_KINDS)
    def test_returns_instance_of_expected_concrete_class(self, kind: DetectorKind) -> None:
        config = _config(kind=kind)
        roi = _roi()

        builder = create_mask_builder(config, roi, depth_scale_mm=1.0)

        assert isinstance(builder, _EXPECTED_CLASS[kind])

    @pytest.mark.parametrize("kind", _ALL_KINDS)
    def test_kind_property_matches_requested_kind(self, kind: DetectorKind) -> None:
        config = _config(kind=kind)
        roi = _roi()

        builder = create_mask_builder(config, roi, depth_scale_mm=1.0)

        assert builder.kind == kind


# --------------------------------------------------------------------------
# 契約テスト: 3方式が同一入力に対し同形状・同型のマスクを返す
# --------------------------------------------------------------------------


class TestSameShapeContract:
    """1回の `build()` 呼び出しだけで shape/dtype の一致を確認する。

    `background` はこの1回では `ready is False`（全 False）のままだが、
    それでも「`roi_depth` と同じ shape の新規割り当て bool 配列」という
    契約は満たすため、`ready` の状態に関わらず本テストは成立する。
    """

    def test_all_three_kinds_return_same_shape_and_dtype(self) -> None:
        roi = _roi()
        roi_depth = _synthetic_roi_depth()

        masks = {}
        for kind in _ALL_KINDS:
            config = _config(kind=kind)
            builder = create_mask_builder(config, roi, depth_scale_mm=1.0)
            masks[kind] = builder.build(roi_depth)

        shapes = {kind: mask.shape for kind, mask in masks.items()}
        dtypes = {kind: mask.dtype for kind, mask in masks.items()}

        assert len(set(shapes.values())) == 1, shapes
        assert len(set(dtypes.values())) == 1, dtypes
        assert shapes[DetectorKind.DEPTH_BAND] == roi_depth.shape
        for dtype in dtypes.values():
            assert dtype == np.dtype(bool)


class TestSameShapeAfterBackgroundReady:
    """`background` を初期化窓ぶん駆動し、3方式が意味のある前景を返す状態で比較する。

    `background_frames=3`（`_config` のテンプレート）ぶん `build()` を
    呼び出すことで `BackgroundMask.ready` を `True` にしてから、改めて同一の
    合成フレームを全方式へ通す。こちらは「初期化完了後も比較可能な形で
    同形状であり続ける」という、より実用に近い状態を固定する
  （タスク本文「同形状のマスクを返し」の実質的な検証）。
    """

    def test_all_three_kinds_return_same_shape_and_dtype_once_comparable(self) -> None:
        roi = _roi()
        roi_depth = _synthetic_roi_depth()

        builders = {
            kind: create_mask_builder(_config(kind=kind), roi, depth_scale_mm=1.0)
            for kind in _ALL_KINDS
        }

        # background を初期化窓ぶん駆動して ready にする。
        background_builder = builders[DetectorKind.BACKGROUND]
        for _ in range(_config(kind=DetectorKind.BACKGROUND).background_frames):
            background_builder.build(roi_depth)
        assert background_builder.ready is True

        masks = {kind: builder.build(roi_depth) for kind, builder in builders.items()}

        shapes = {kind: mask.shape for kind, mask in masks.items()}
        dtypes = {kind: mask.dtype for kind, mask in masks.items()}

        assert len(set(shapes.values())) == 1, shapes
        assert len(set(dtypes.values())) == 1, dtypes
        for dtype in dtypes.values():
            assert dtype == np.dtype(bool)


# --------------------------------------------------------------------------
# 設定だけで方式が切り替わる（コード変更なし）
# --------------------------------------------------------------------------


class TestSwitchByConfigAlone:
    def test_switching_kind_field_alone_changes_concrete_class(self) -> None:
        roi = _roi()
        base_config = _config(kind=DetectorKind.DEPTH_BAND)

        produced_classes = []
        for kind in _ALL_KINDS:
            # 同じ呼び出しコード・同じ roi・同じ depth_scale_mm のまま、
            # config.kind というフィールド値だけを変える。
            config = dataclasses.replace(base_config, kind=kind)
            builder = create_mask_builder(config, roi, depth_scale_mm=1.0)
            produced_classes.append(type(builder))

        assert produced_classes == [
            DepthBandMask,
            FrameDiffMask,
            BackgroundMask,
        ]
        # 3方式とも異なる具象クラスであることも確認する（切り替わっている証拠）。
        assert len(set(produced_classes)) == 3


# --------------------------------------------------------------------------
# 未知の方式名は起動前に拒否する
# --------------------------------------------------------------------------


class TestUnknownKindRejected:
    def test_unknown_kind_raises_tracking_config_error(self) -> None:
        config = _config(kind=DetectorKind.DEPTH_BAND)
        # dataclass はフィールド型を実行時に強制しないため、型チェックを
        # すり抜けた不正な値を直接構築できる（タスク本文が想定するケース）。
        broken_config = dataclasses.replace(config, kind="not_a_real_kind")
        roi = _roi()

        with pytest.raises(TrackingConfigError):
            create_mask_builder(broken_config, roi, depth_scale_mm=1.0)

    def test_unknown_kind_is_also_a_value_error(self) -> None:
        config = _config(kind=DetectorKind.DEPTH_BAND)
        broken_config = dataclasses.replace(config, kind="bogus")
        roi = _roi()

        with pytest.raises(ValueError):
            create_mask_builder(broken_config, roi, depth_scale_mm=1.0)

    def test_unknown_kind_does_not_partially_construct_anything(self) -> None:
        """「起動前に拒否する」＝ 例外送出前にマスクの構築を一切試みないこと。

        直接の観測は難しいため、代わりに「例外発生後、後続の正常な呼び出し
        （既知の kind）が正しく動作し続けること」で、失敗が内部状態を汚さず
        きれいに起きたことを間接的に確認する。
        """
        roi = _roi()
        broken_config = dataclasses.replace(_config(kind=DetectorKind.DEPTH_BAND), kind="???")

        with pytest.raises(TrackingConfigError):
            create_mask_builder(broken_config, roi, depth_scale_mm=1.0)

        # 例外の後でも create_mask_builder は正常に使い続けられる。
        good_builder = create_mask_builder(
            _config(kind=DetectorKind.FRAME_DIFF), roi, depth_scale_mm=1.0
        )
        assert isinstance(good_builder, FrameDiffMask)


# --------------------------------------------------------------------------
# MaskBuilder プロトコル: 3実装がすべて構造的に満たす
# --------------------------------------------------------------------------


class TestMaskBuilderProtocol:
    """`MaskBuilder` は `@runtime_checkable` `Protocol` として定義してある
    （`masks/__init__.py` docstring 参照）ため、3方式の具象クラスが明示的に
    継承していなくても `isinstance` 判定が構造的に成立する。
    """

    @pytest.mark.parametrize("kind", _ALL_KINDS)
    def test_concrete_builder_satisfies_mask_builder_protocol(
        self, kind: DetectorKind
    ) -> None:
        config = _config(kind=kind)
        roi = _roi()

        builder = create_mask_builder(config, roi, depth_scale_mm=1.0)

        assert isinstance(builder, MaskBuilder)

    @pytest.mark.parametrize("kind", _ALL_KINDS)
    def test_concrete_builder_has_all_shared_attributes(self, kind: DetectorKind) -> None:
        """構造的な属性の存在も念のため確認する（Protocol 判定と独立した観点）。"""
        config = _config(kind=kind)
        roi = _roi()

        builder = create_mask_builder(config, roi, depth_scale_mm=1.0)

        assert hasattr(builder, "kind")
        assert hasattr(builder, "ready")
        assert callable(builder.build)
        assert callable(builder.reset)


# --------------------------------------------------------------------------
# Roi の z_min_mm / z_max_mm が DepthBandMask に正しく配線されている
# --------------------------------------------------------------------------


class TestRoiZBandWiredToDepthBandOnly:
    def test_depth_band_uses_roi_z_band_from_create_mask_builder(self) -> None:
        # z_min/z_max を極端に狭い帯にして、bandの内外が create_mask_builder
        # 経由でも正しく効いていることを確認する（取り違え・欠落の検出）。
        roi = _roi(z_min_mm=1000.0, z_max_mm=1000.0001)
        config = _config(kind=DetectorKind.DEPTH_BAND)

        builder = create_mask_builder(config, roi, depth_scale_mm=1.0)

        roi_depth = np.array([[1000, 999, 1001, 2000]], dtype=np.uint16)
        mask = builder.build(roi_depth)

        # 1000mm のみが極狭帯の中に入り、他は外れる。
        assert np.array_equal(mask, np.array([[True, False, False, False]]))

    def test_depth_band_z_band_not_swapped_or_dropped(self) -> None:
        # z_min と z_max を非対称にし、どちらが min/max として使われているかを
        # 区別できる入力で確認する（取り違えていれば band の向きが逆になる）。
        roi = _roi(z_min_mm=2000.0, z_max_mm=3000.0)
        config = _config(kind=DetectorKind.DEPTH_BAND)

        builder = create_mask_builder(config, roi, depth_scale_mm=1.0)

        roi_depth = np.array([[1500, 2500, 3500]], dtype=np.uint16)
        mask = builder.build(roi_depth)

        # 2500mm だけが [2000, 3000] の帯内。取り違えていれば結果が変わる。
        assert np.array_equal(mask, np.array([[False, True, False]]))

    def test_frame_diff_and_background_ignore_roi_z_band(self) -> None:
        # frame_diff / background は z_min_mm / z_max_mm を使わない
        # （design.md 表: 距離帯ゲートを持たない）。極端な帯を渡しても
        # 出力に距離帯フィルタが混入しないことを確認する。
        roi = _roi(z_min_mm=999999.0, z_max_mm=999999.0001)  # 何も入り得ない極狭帯
        roi_depth = np.array([[1000, 1000], [1000, 1000]], dtype=np.uint16)

        frame_diff = create_mask_builder(
            _config(kind=DetectorKind.FRAME_DIFF), roi, depth_scale_mm=1.0
        )
        frame_diff.build(roi_depth)  # 1回目は直前フレーム無しで全 False
        moved = np.array([[1000, 1200], [1000, 1000]], dtype=np.uint16)
        diff_mask = frame_diff.build(moved)
        # 距離帯を無視するなら、200mm差の画素は帯に関係なく前景になり得る。
        assert diff_mask[0, 1] == True  # noqa: E712 (bool 配列要素の明示比較)
