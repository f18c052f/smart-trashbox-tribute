"""world_frame_calibration.types の値オブジェクトの検証
(tasks.md タスク 1.4 / 要件 1.2, 1.6, 2.5, 2.8, 4.6)。

design.md「L0-L2: 基盤・入力」節の GeoTypes コンポーネント契約を固定する。
本テストが確認すること:

- `Intrinsics` / `StreamSignature` / `PixelRegion` / `DepthImage` / `Plane` /
  `PlaneQuality` / `AnchorObservation` / `FrameGeometry` / `ToleranceSpec` の
  すべてが `frozen=True, slots=True` の dataclass であり値等価であること
  （design.md GeoTypes「Invariants」/ tasks.md タスク 1.4 観察可能な完了状態）
- `Intrinsics` / `StreamSignature` のフィールド名が
  `sensing_foundation.CameraIntrinsics` / `sensing_foundation.StreamProfile`
  の対応部分と完全に一致すること（design.md GeoTypes「Integration」）
- `DepthImage.depth_mm` の無効画素表現が NaN であり 0 で埋めないという方針を
  値として構成できること（design.md GeoTypes「Postconditions」）
- `FrameGeometry.yaw_sensitivity_deg_per_mm` が `degrees(1 / baseline_mm)` と、
  `lateral_error_mm_per_mm_at_1000mm` が `1000 / baseline_mm` と解析的に一致
  すること（tasks.md タスク 1.4 観察可能な完了状態）
- `ToleranceSpec` が `provisional` と `source` を必須で持つこと（要件 4.6）
- `AnchorRole` が `ORIGIN` / `X_AXIS` / `VERIFICATION` を持つ `StrEnum` である
  こと
"""

from __future__ import annotations

import dataclasses
import math

import numpy as np
import pytest
from sensing_foundation import CameraIntrinsics, StreamProfile

from world_frame_calibration import types


def _make_intrinsics(**overrides: object) -> types.Intrinsics:
    kwargs: dict[str, object] = dict(
        width_px=640,
        height_px=480,
        fx_px=386.5,
        fy_px=386.5,
        ppx_px=320.0,
        ppy_px=240.0,
        model="brown_conrady",
        coeffs=(0.0, 0.0, 0.0, 0.0, 0.0),
    )
    kwargs.update(overrides)
    return types.Intrinsics(**kwargs)  # type: ignore[arg-type]


def _make_stream_signature(**overrides: object) -> types.StreamSignature:
    kwargs: dict[str, object] = dict(
        width_px=640,
        height_px=480,
        fps=30,
        depth_scale_mm=1.0,
        color_enabled=True,
    )
    kwargs.update(overrides)
    return types.StreamSignature(**kwargs)  # type: ignore[arg-type]


def _make_pixel_region(**overrides: object) -> types.PixelRegion:
    kwargs: dict[str, object] = dict(x0_px=10, y0_px=20, x1_px=100, y1_px=200)
    kwargs.update(overrides)
    return types.PixelRegion(**kwargs)  # type: ignore[arg-type]


def _make_plane_quality(**overrides: object) -> types.PlaneQuality:
    kwargs: dict[str, object] = dict(
        points_considered=5000,
        inlier_count=4500,
        inlier_ratio=0.9,
        residual_abs_p50_mm=1.2,
        residual_abs_p95_mm=3.4,
        residual_rms_mm=1.5,
        frames_used=5,
        incidence_angle_deg=45.0,
        rng_seed=0,
    )
    kwargs.update(overrides)
    return types.PlaneQuality(**kwargs)  # type: ignore[arg-type]


def _make_plane(**overrides: object) -> types.Plane:
    kwargs: dict[str, object] = dict(
        normal=(0.0, 0.0, 1.0),
        distance_mm=1500.0,
        quality=_make_plane_quality(),
    )
    kwargs.update(overrides)
    return types.Plane(**kwargs)  # type: ignore[arg-type]


def _make_depth_image(**overrides: object) -> types.DepthImage:
    depth_mm = np.full((4, 4), 1500.0, dtype=np.float64)
    depth_mm[0, 0] = np.nan
    valid_count = np.ones((4, 4), dtype=np.int32)
    kwargs: dict[str, object] = dict(
        depth_mm=depth_mm,
        valid_count=valid_count,
        frames_used=5,
        intrinsics=_make_intrinsics(),
        signature=_make_stream_signature(),
        source_kind="live",
    )
    kwargs.update(overrides)
    return types.DepthImage(**kwargs)  # type: ignore[arg-type]


def _make_anchor_observation(**overrides: object) -> types.AnchorObservation:
    kwargs: dict[str, object] = dict(
        label="origin_marker",
        role=types.AnchorRole.ORIGIN,
        point_camera_mm=(100.0, 200.0, 1500.0),
        point_on_plane_mm=(100.0, 200.0, 1500.0),
        height_above_plane_mm=50.0,
        range_from_camera_mm=1520.0,
        sample_count=80,
        spread_mm=2.0,
        region=_make_pixel_region(),
        frames_used=5,
    )
    kwargs.update(overrides)
    return types.AnchorObservation(**kwargs)  # type: ignore[arg-type]


def _make_frame_geometry(baseline_mm: float = 1000.0, **overrides: object) -> types.FrameGeometry:
    kwargs: dict[str, object] = dict(
        origin_camera_mm=(0.0, 0.0, 1500.0),
        x_axis_camera=(1.0, 0.0, 0.0),
        y_axis_camera=(0.0, 1.0, 0.0),
        z_axis_camera=(0.0, 0.0, 1.0),
        baseline_mm=baseline_mm,
        yaw_sensitivity_deg_per_mm=math.degrees(1.0 / baseline_mm),
        lateral_error_mm_per_mm_at_1000mm=1000.0 / baseline_mm,
    )
    kwargs.update(overrides)
    return types.FrameGeometry(**kwargs)  # type: ignore[arg-type]


def _make_tolerance_spec(**overrides: object) -> types.ToleranceSpec:
    kwargs: dict[str, object] = dict(
        horizontal_mm=20.0,
        vertical_mm=15.0,
        provisional=True,
        source="暫定値: 観測誤差10mm・距離2mで横ずれ約25mmから概算",
    )
    kwargs.update(overrides)
    return types.ToleranceSpec(**kwargs)  # type: ignore[arg-type]


# All GeoTypes dataclasses paired with a no-arg factory, used to drive the
# generic frozen/slots/value-equality checks below without repeating the same
# assertions nine times.
_FACTORIES: dict[str, object] = {
    "Intrinsics": _make_intrinsics,
    "StreamSignature": _make_stream_signature,
    "PixelRegion": _make_pixel_region,
    "DepthImage": _make_depth_image,
    "Plane": _make_plane,
    "PlaneQuality": _make_plane_quality,
    "AnchorObservation": _make_anchor_observation,
    "FrameGeometry": _make_frame_geometry,
    "ToleranceSpec": _make_tolerance_spec,
}


class TestAllTypesAreFrozenSlotsDataclasses:
    """design.md GeoTypes「Invariants」: すべて `frozen=True, slots=True`。"""

    @pytest.mark.parametrize("type_name", sorted(_FACTORIES))
    def test_is_dataclass_with_frozen_and_slots(self, type_name: str) -> None:
        cls = getattr(types, type_name)
        assert dataclasses.is_dataclass(cls)
        assert cls.__dataclass_params__.frozen is True
        instance = _FACTORIES[type_name]()
        assert not hasattr(instance, "__dict__")

    @pytest.mark.parametrize("type_name", sorted(_FACTORIES))
    def test_construction_does_not_validate(self, type_name: str) -> None:
        """GeoTypes は構築時に検証しない（design.md GeoTypes「Validation」）。

        既定のファクトリで作れる時点で、この点は間接的に固定される
        （検証があれば不整合な値で例外が飛ぶはずだが、ここでは意図的に
        妥当な値のみを渡しているため、代わりに「例外を投げる __post_init__
        が定義されていないこと」を直接確認する）。
        """
        cls = getattr(types, type_name)
        assert "__post_init__" not in cls.__dict__

    @pytest.mark.parametrize("type_name", sorted(_FACTORIES))
    def test_frozen_instance_rejects_attribute_assignment(self, type_name: str) -> None:
        instance = _FACTORIES[type_name]()
        # Pick any one field name that exists on the instance.
        field_name = dataclasses.fields(instance)[0].name
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(instance, field_name, getattr(instance, field_name))

    @pytest.mark.parametrize("type_name", sorted(_FACTORIES))
    def test_slots_reject_new_attribute(self, type_name: str) -> None:
        instance = _FACTORIES[type_name]()
        with pytest.raises(AttributeError):
            instance.__dict__  # noqa: B018 -- intentionally probing for absence

    @pytest.mark.parametrize("type_name", sorted(_FACTORIES))
    def test_value_equality_for_identical_construction(self, type_name: str) -> None:
        """同じフィールド値で構成した2つのインスタンスは値等価である。

        `DepthImage` は numpy 配列フィールドを持つため、同一の配列オブジェクト
        を共有させて構築する（別々の配列オブジェクト同士の `==` は要素ごとの
        真偽値配列になり `bool()` が使えないため、意図的に同一参照を使う）。
        """
        factory = _FACTORIES[type_name]
        a = factory()
        b = factory()
        if type_name == "DepthImage":
            # Rebuild `b` sharing the exact same ndarray objects as `a`.
            b = types.DepthImage(
                depth_mm=a.depth_mm,
                valid_count=a.valid_count,
                frames_used=a.frames_used,
                intrinsics=a.intrinsics,
                signature=a.signature,
                source_kind=a.source_kind,
            )
        assert a == b

    def test_value_inequality_when_a_field_differs(self) -> None:
        a = _make_pixel_region()
        b = _make_pixel_region(x0_px=999)
        assert a != b


class TestIntrinsicsMirrorsUpstreamFieldNames:
    """`Intrinsics` は `sensing_foundation.CameraIntrinsics` のフィールド名を
    そのまま採る（design.md GeoTypes「Integration」）。
    """

    def test_field_names_match_upstream_camera_intrinsics_exactly(self) -> None:
        own_fields = {f.name for f in dataclasses.fields(types.Intrinsics)}
        upstream_fields = {f.name for f in dataclasses.fields(CameraIntrinsics)}
        assert own_fields == upstream_fields

    def test_can_be_constructed_from_upstream_camera_intrinsics_field_values(self) -> None:
        upstream = CameraIntrinsics(
            width_px=640,
            height_px=480,
            fx_px=386.5,
            fy_px=386.5,
            ppx_px=320.0,
            ppy_px=240.0,
            model="brown_conrady",
            coeffs=(0.0, 0.0, 0.0, 0.0, 0.0),
        )
        mirrored = types.Intrinsics(
            **{f.name: getattr(upstream, f.name) for f in dataclasses.fields(upstream)}
        )
        for f in dataclasses.fields(upstream):
            assert getattr(mirrored, f.name) == getattr(upstream, f.name)


class TestStreamSignatureMirrorsUpstreamFieldNames:
    """`StreamSignature` は `sensing_foundation.StreamProfile` の対応部分
    （`intrinsics` を除く）のフィールド名をそのまま採る
    （design.md GeoTypes「Integration」）。
    """

    def test_field_names_are_a_subset_of_upstream_stream_profile_field_names(self) -> None:
        own_fields = {f.name for f in dataclasses.fields(types.StreamSignature)}
        upstream_fields = {f.name for f in dataclasses.fields(StreamProfile)}
        assert own_fields <= upstream_fields
        assert own_fields == upstream_fields - {"intrinsics"}

    def test_can_be_constructed_from_upstream_stream_profile_field_values(self) -> None:
        upstream = StreamProfile(
            width_px=640,
            height_px=480,
            fps=30,
            depth_scale_mm=1.0,
            color_enabled=True,
            intrinsics=None,
        )
        own_field_names = {f.name for f in dataclasses.fields(types.StreamSignature)}
        mirrored = types.StreamSignature(
            **{name: getattr(upstream, name) for name in own_field_names}
        )
        for name in own_field_names:
            assert getattr(mirrored, name) == getattr(upstream, name)


class TestDepthImageInvalidPixelsAreNaN:
    """DepthImage の無効画素は NaN で表し、0 で埋めない
    （design.md GeoTypes「Postconditions」/ tasks.md タスク 1.4）。
    """

    def test_invalid_pixel_is_nan_not_zero(self) -> None:
        image = _make_depth_image()
        assert math.isnan(float(image.depth_mm[0, 0]))

    def test_a_genuine_zero_distance_reading_is_distinguishable_from_missing(self) -> None:
        """実測値としての 0 mm は NaN と区別できる（欠測のセンチネルではない）。"""
        depth_mm = np.full((2, 2), 1500.0, dtype=np.float64)
        depth_mm[0, 0] = np.nan  # missing
        depth_mm[1, 1] = 0.0  # a genuine (if physically odd) zero reading
        image = _make_depth_image(depth_mm=depth_mm)
        assert math.isnan(float(image.depth_mm[0, 0]))
        assert not math.isnan(float(image.depth_mm[1, 1]))
        assert float(image.depth_mm[1, 1]) == 0.0

    def test_construction_does_not_validate_shape_against_intrinsics(self) -> None:
        """構築時には `depth_mm.shape` と `intrinsics` の対応を検証しない
        （design.md GeoTypes「Validation」: 検証は `deproject` / `plan` の
        境界の責務）。
        """
        mismatched_depth = np.zeros((2, 2), dtype=np.float64)
        image = _make_depth_image(depth_mm=mismatched_depth)
        assert image.depth_mm.shape == (2, 2)
        assert image.intrinsics.height_px == 480


class TestAnchorRole:
    """`AnchorRole` は ORIGIN / X_AXIS / VERIFICATION を持つ `StrEnum`。"""

    def test_is_str_enum_with_expected_members(self) -> None:
        from enum import StrEnum

        assert issubclass(types.AnchorRole, StrEnum)
        assert types.AnchorRole.ORIGIN == "origin"
        assert types.AnchorRole.X_AXIS == "x_axis"
        assert types.AnchorRole.VERIFICATION == "verification"

    def test_has_exactly_three_members(self) -> None:
        assert {member.name for member in types.AnchorRole} == {
            "ORIGIN",
            "X_AXIS",
            "VERIFICATION",
        }


class TestFrameGeometryYawSensitivityMatchesBaselineAnalytically:
    """`yaw_sensitivity_deg_per_mm` == `degrees(1 / baseline_mm)`、
    `lateral_error_mm_per_mm_at_1000mm` == `1000 / baseline_mm`
    （tasks.md タスク 1.4 観察可能な完了状態 / design.md GeoTypes「Risks」）。
    """

    def test_yaw_sensitivity_matches_independently_precomputed_literal_at_1000mm_baseline(
        self,
    ) -> None:
        """`baseline_mm=1000.0` について、このテストファイル内の式を再利用せず、
        独立に事前計算したリテラル値（`math.degrees(1/1000)` の結果を別途確認
        した上でハードコードしたもの）と照合する。

        `_make_frame_geometry` フィクスチャは
        `yaw_sensitivity_deg_per_mm=math.degrees(1.0 / baseline_mm)` という
        *同一の式* を使ってフィールド値を構成しているため、フィクスチャが
        計算した値をそのままテスト側でも同じ式で再計算して比較するだけでは、
        フィクスチャの自己無矛盾性を確認しているに過ぎず、解析式そのものの
        正しさは検証できない（式にバグが混入しても両辺が揃って壊れるため
        検出できない）。ハードコードしたリテラルと突き合わせることで、
        この式の正しさを実際にピン留めする。
        """
        geometry = _make_frame_geometry(baseline_mm=1000.0)
        assert geometry.yaw_sensitivity_deg_per_mm == pytest.approx(
            0.057295779513082325, rel=1e-12
        )

    def test_lateral_error_matches_independently_precomputed_literal_at_1000mm_baseline(
        self,
    ) -> None:
        """上と同じ理由により、`lateral_error_mm_per_mm_at_1000mm` も
        `baseline_mm=1000.0` におけるハードコードしたリテラル値と照合する。
        """
        geometry = _make_frame_geometry(baseline_mm=1000.0)
        assert geometry.lateral_error_mm_per_mm_at_1000mm == pytest.approx(1.0, rel=1e-12)

    @pytest.mark.parametrize("baseline_mm", [100.0, 500.0, 800.0, 1000.0, 2000.0, 3500.0])
    def test_yaw_sensitivity_matches_inverse_baseline_formula(self, baseline_mm: float) -> None:
        geometry = _make_frame_geometry(baseline_mm=baseline_mm)
        expected = math.degrees(1.0 / geometry.baseline_mm)
        assert geometry.yaw_sensitivity_deg_per_mm == pytest.approx(expected, rel=1e-12)

    @pytest.mark.parametrize("baseline_mm", [100.0, 500.0, 800.0, 1000.0, 2000.0, 3500.0])
    def test_lateral_error_matches_thousand_over_baseline_formula(self, baseline_mm: float) -> None:
        geometry = _make_frame_geometry(baseline_mm=baseline_mm)
        expected = 1000.0 / geometry.baseline_mm
        assert geometry.lateral_error_mm_per_mm_at_1000mm == pytest.approx(expected, rel=1e-12)

    def test_shorter_baseline_yields_larger_yaw_sensitivity(self) -> None:
        """基線長が短い設置ほどヨー感度が大きくなる（design.md GeoTypes「Risks」の
        「基線長が短い設置で静かに精度が落ちる罠」を可視化する指標であること）。
        """
        short = _make_frame_geometry(baseline_mm=500.0)
        long_ = _make_frame_geometry(baseline_mm=2000.0)
        assert short.yaw_sensitivity_deg_per_mm > long_.yaw_sensitivity_deg_per_mm
        assert short.lateral_error_mm_per_mm_at_1000mm > long_.lateral_error_mm_per_mm_at_1000mm


class TestToleranceSpecRequiresProvisionalFlagAndSource:
    """許容値仕様は「暫定か実測由来か」と出典文字列を必須で持つ（要件 4.6）。"""

    def test_provisional_and_source_fields_exist(self) -> None:
        field_names = {f.name for f in dataclasses.fields(types.ToleranceSpec)}
        assert "provisional" in field_names
        assert "source" in field_names

    def test_provisional_true_with_source(self) -> None:
        spec = _make_tolerance_spec(provisional=True, source="暫定値: 根拠A")
        assert spec.provisional is True
        assert spec.source == "暫定値: 根拠A"

    def test_measured_false_with_source(self) -> None:
        spec = _make_tolerance_spec(
            provisional=False, source="実測: 2026-08-22 メジャー計測による"
        )
        assert spec.provisional is False
        assert spec.source == "実測: 2026-08-22 メジャー計測による"

    def test_source_is_required_positional_or_keyword_field(self) -> None:
        with pytest.raises(TypeError):
            types.ToleranceSpec(horizontal_mm=20.0, vertical_mm=15.0, provisional=True)  # type: ignore[call-arg]


class TestPlaneNormalAndQualityComposition:
    """`Plane` が `PlaneQuality` を保持し、値等価であることを固定する。"""

    def test_plane_holds_its_quality(self) -> None:
        plane = _make_plane()
        assert isinstance(plane.quality, types.PlaneQuality)

    def test_plane_quality_carries_frames_used_for_multi_frame_stabilization(self) -> None:
        """複数フレームで安定化した場合、使用したフレーム数が品質に含まれる
        （要件 1.6）。
        """
        quality = _make_plane_quality(frames_used=12)
        assert quality.frames_used == 12
