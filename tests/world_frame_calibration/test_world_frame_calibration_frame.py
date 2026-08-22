"""world_frame_calibration.frame（World frame の確立）の検証（tasks.md タスク 3.1 /
要件 2.1, 2.2, 2.3, 2.4, 2.7, 3.7, 5.3）。

design.md「L3-L5: 推定と確立」節の WorldFrameBuilder コンポーネント契約を固定する。
本テストが確認すること（tasks.md タスク 3.1「観測可能な完了状態」および
Critical constraints、および Implementation Notes のタスク1.3・タスク1.4）:

- Z 軸 = 床平面の法線（カメラ側正）、原点 = 原点マーカーの `point_on_plane_mm`、
  +X = 原点マーカー → 方向マーカーの正規化ベクトル、+Y = Z × X（右手系）
- 原点マーカーが World 原点 (0, 0, 0) へ、方向マーカーが (baseline_mm, 0, 0) へ
  写ること。床平面上の任意の点の z が数値誤差の範囲で 0 になること（要件 3.7）
- 基線長が `limits.min_baseline_mm` を下回れば
  `CalibrationFailure(ANCHOR_BASELINE_TOO_SHORT)` として失敗し、
  detail に「+X axis is unstable」の趣旨が含まれ、context に実測基線長と
  下限が入ること（要件 2.7, 5.3）
- 方向ベクトルが床平面法線とほぼ平行（縮退）な場合、基線長が十分でも
  `CalibrationFailure(ANCHOR_DEGENERATE)` として、基線長不足とは別の理由で
  失敗すること（2つの独立した縮退ゲート）
- `linalg.x_hint_degeneracy` が `linalg.orthonormal_basis` より前に呼ばれ、
  縮退時に NaN な基底を静かに下流へ流さないこと（実装ノート「タスク1.3」）
- 基線長から `FrameGeometry.yaw_sensitivity_deg_per_mm` /
  `lateral_error_mm_per_mm_at_1000mm` が解析式どおりに算出されること
  （実装ノート「タスク1.4」。`baseline_mm=1000.0` でのリテラル
  `0.057295779513082325` / `1.0` と突き合わせる）
- `build_world_frame` のシグネチャがカメラの位置・姿勢に相当する引数を
  一切持たないこと（要件 2.9。frame はマーカーの物理配置だけで決まる）
- `WorldTransform.rotation` の各行がカメラ座標系で表した World の各軸で
  あることが docstring に明記されていること

このテストファイルは `world_frame_calibration.frame` がまだ存在しない時点では
`ModuleNotFoundError` で失敗する（RED）。

ファイル名について: `tests/**` に `__init__.py` が無いため、裸の
`test_frame.py` は使わずプレフィックス付きの
`test_world_frame_calibration_frame.py` とする（既存タスクの命名規約。
tasks.md 実装ノート・タスク1.6参照）。
"""

from __future__ import annotations

import inspect
import math

import numpy as np
import pytest

from world_frame_calibration.errors import CalibrationFailure, FailureReason
from world_frame_calibration.plan import PlanLimits
from world_frame_calibration.types import (
    AnchorObservation,
    AnchorRole,
    PixelRegion,
    Plane,
    PlaneQuality,
)

# ---------------------------------------------------------------------------
# ヘルパ
# ---------------------------------------------------------------------------

_DEFAULT_REGION = PixelRegion(x0_px=0, y0_px=0, x1_px=10, y1_px=10)


def _plane(normal: tuple[float, float, float], distance_mm: float) -> Plane:
    """テスト用の `Plane`。品質値はダミー（本モジュールは品質を検査しない）。"""
    quality = PlaneQuality(
        points_considered=1000,
        inlier_count=900,
        inlier_ratio=0.9,
        residual_abs_p50_mm=1.0,
        residual_abs_p95_mm=2.0,
        residual_rms_mm=1.5,
        frames_used=1,
        incidence_angle_deg=45.0,
        rng_seed=0,
    )
    return Plane(normal=normal, distance_mm=distance_mm, quality=quality)


def _anchor(
    label: str,
    role: AnchorRole,
    point_on_plane_mm: tuple[float, float, float],
) -> AnchorObservation:
    """テスト用の `AnchorObservation`。`point_camera_mm` は投影前の代表点を
    模して `point_on_plane_mm` と同じ値を置く（本モジュールが用いるのは
    `point_on_plane_mm` のみであり、`point_camera_mm` の値は無関係）。
    """
    return AnchorObservation(
        label=label,
        role=role,
        point_camera_mm=point_on_plane_mm,
        point_on_plane_mm=point_on_plane_mm,
        height_above_plane_mm=0.0,
        range_from_camera_mm=float(np.linalg.norm(point_on_plane_mm)),
        sample_count=100,
        spread_mm=1.0,
        region=_DEFAULT_REGION,
        frames_used=1,
    )


# ---------------------------------------------------------------------------
# 軸の構成: Z=法線, 原点=原点マーカー, +X=正規化した方向ベクトル, +Y=Z×X
# ---------------------------------------------------------------------------


class TestBuildWorldFrameAxisConstruction:
    """既知の単純な配置（水平な床、+X 方向のマーカー）で軸が期待どおりに
    構成されることを固定する（design.md WorldFrameBuilder「Contracts」
    「Postconditions」/ 要件 2.1〜2.4）。
    """

    def _establish(self):
        from world_frame_calibration import frame

        plane = _plane(normal=(0.0, 0.0, 1.0), distance_mm=1000.0)
        origin_anchor = _anchor("origin", AnchorRole.ORIGIN, (0.0, 0.0, 1000.0))
        x_axis_anchor = _anchor("x_axis", AnchorRole.X_AXIS, (1000.0, 0.0, 1000.0))
        limits = PlanLimits(min_baseline_mm=800.0)

        return (
            frame.build_world_frame(plane, origin_anchor, x_axis_anchor, limits),
            plane,
            origin_anchor,
            x_axis_anchor,
        )

    def test_origin_marker_maps_to_world_origin(self) -> None:
        established, _plane, origin_anchor, _x = self._establish()
        result = established.transform.apply_point(origin_anchor.point_on_plane_mm)
        np.testing.assert_allclose(result, (0.0, 0.0, 0.0), atol=1e-9)

    def test_direction_marker_maps_to_baseline_on_x_axis(self) -> None:
        established, _plane, _origin, x_axis_anchor = self._establish()
        result = established.transform.apply_point(x_axis_anchor.point_on_plane_mm)
        expected_baseline = established.geometry.baseline_mm
        np.testing.assert_allclose(result, (expected_baseline, 0.0, 0.0), atol=1e-9)
        assert expected_baseline == pytest.approx(1000.0, abs=1e-9)

    def test_arbitrary_floor_point_maps_to_z_zero(self) -> None:
        """床平面上の任意の点（マーカーではない点）を変換した結果の z が
        数値誤差の範囲で 0 になる（要件 3.7）。
        """
        established, _plane, _origin, _x = self._establish()
        floor_points = np.array(
            [
                [250.0, -400.0, 1000.0],
                [-900.0, 900.0, 1000.0],
                [0.0, 0.0, 1000.0],
                [5000.0, -3000.0, 1000.0],
            ]
        )
        transformed = established.transform.apply(floor_points)
        np.testing.assert_allclose(transformed[:, 2], 0.0, atol=1e-6)

    def test_z_axis_is_plane_normal(self) -> None:
        established, plane, _origin, _x = self._establish()
        np.testing.assert_allclose(
            established.geometry.z_axis_camera, plane.normal, atol=1e-12
        )

    def test_y_axis_is_cross_of_z_and_x(self) -> None:
        established, _plane, _origin, _x = self._establish()
        z = np.array(established.geometry.z_axis_camera)
        x = np.array(established.geometry.x_axis_camera)
        expected_y = np.cross(z, x)
        np.testing.assert_allclose(
            established.geometry.y_axis_camera, expected_y, atol=1e-9
        )

    def test_x_axis_is_normalized_direction_between_markers(self) -> None:
        established, _plane, origin_anchor, x_axis_anchor = self._establish()
        raw_direction = np.array(x_axis_anchor.point_on_plane_mm) - np.array(
            origin_anchor.point_on_plane_mm
        )
        expected_x_axis = raw_direction / np.linalg.norm(raw_direction)
        np.testing.assert_allclose(
            established.geometry.x_axis_camera, expected_x_axis, atol=1e-9
        )

    def test_rotation_is_orthonormal_with_determinant_one(self) -> None:
        """`WorldTransform` 構築時の検査（正規直交かつ行列式 +1）を通過して
        いること自体が成功した戻り値から間接的に確認できるが、追加で明示的に
        検査する（design.md WorldFrameBuilder「Invariants」）。
        """
        established, _plane, _origin, _x = self._establish()
        rotation = np.array(established.transform.rotation)
        residual = np.max(np.abs(rotation.T @ rotation - np.eye(3)))
        assert residual == pytest.approx(0.0, abs=1e-9)
        determinant = np.linalg.det(rotation)
        assert determinant == pytest.approx(1.0, abs=1e-9)

    def test_establishment_carries_plane_and_anchors_through(self) -> None:
        established, plane, origin_anchor, x_axis_anchor = self._establish()
        assert established.plane is plane
        assert established.origin_anchor is origin_anchor
        assert established.x_axis_anchor is x_axis_anchor
        assert established.geometry.origin_camera_mm == origin_anchor.point_on_plane_mm


# ---------------------------------------------------------------------------
# 縮退条件その1: 基線長不足 (ANCHOR_BASELINE_TOO_SHORT)
# ---------------------------------------------------------------------------


class TestBuildWorldFrameBaselineTooShort:
    """原点マーカーと方向マーカーの床平面上の距離が `limits.min_baseline_mm`
    を下回れば `CalibrationFailure(ANCHOR_BASELINE_TOO_SHORT)` として失敗する
    （design.md WorldFrameBuilder「Validation」/ 要件 2.7, 5.3）。
    """

    def test_short_baseline_raises_anchor_baseline_too_short(self) -> None:
        from world_frame_calibration import frame

        plane = _plane(normal=(0.0, 0.0, 1.0), distance_mm=1000.0)
        origin_anchor = _anchor("origin", AnchorRole.ORIGIN, (0.0, 0.0, 1000.0))
        # 平面内の方向ベクトルだが距離はわずか 100mm（下限 800mm を下回る）
        x_axis_anchor = _anchor("x_axis", AnchorRole.X_AXIS, (100.0, 0.0, 1000.0))
        limits = PlanLimits(min_baseline_mm=800.0)

        with pytest.raises(CalibrationFailure) as exc_info:
            frame.build_world_frame(plane, origin_anchor, x_axis_anchor, limits)

        assert exc_info.value.reason == FailureReason.ANCHOR_BASELINE_TOO_SHORT

    def test_failure_detail_states_x_axis_is_unstable(self) -> None:
        from world_frame_calibration import frame

        plane = _plane(normal=(0.0, 0.0, 1.0), distance_mm=1000.0)
        origin_anchor = _anchor("origin", AnchorRole.ORIGIN, (0.0, 0.0, 1000.0))
        x_axis_anchor = _anchor("x_axis", AnchorRole.X_AXIS, (100.0, 0.0, 1000.0))
        limits = PlanLimits(min_baseline_mm=800.0)

        with pytest.raises(CalibrationFailure) as exc_info:
            frame.build_world_frame(plane, origin_anchor, x_axis_anchor, limits)

        detail_lower = exc_info.value.detail.lower()
        assert "unstable" in detail_lower
        assert "+x" in detail_lower or "x axis" in detail_lower or "x-axis" in detail_lower

    def test_failure_context_reports_measured_baseline_and_threshold(self) -> None:
        from world_frame_calibration import frame

        plane = _plane(normal=(0.0, 0.0, 1.0), distance_mm=1000.0)
        origin_anchor = _anchor("origin", AnchorRole.ORIGIN, (0.0, 0.0, 1000.0))
        x_axis_anchor = _anchor("x_axis", AnchorRole.X_AXIS, (100.0, 0.0, 1000.0))
        limits = PlanLimits(min_baseline_mm=800.0)

        with pytest.raises(CalibrationFailure) as exc_info:
            frame.build_world_frame(plane, origin_anchor, x_axis_anchor, limits)

        context = exc_info.value.context
        assert context.get("baseline_mm") == pytest.approx(100.0, abs=1e-9)
        assert context.get("min_baseline_mm") == 800.0


# ---------------------------------------------------------------------------
# 縮退条件その2: 方向ベクトルの縮退 (ANCHOR_DEGENERATE) -- 基線長不足とは
# 独立した、別のゲート
# ---------------------------------------------------------------------------


class TestBuildWorldFrameDirectionDegenerate:
    """方向ベクトルが床平面法線とほぼ平行（in-plane 成分がほぼ消える）な
    場合、基線長そのものは下限を満たしていても
    `CalibrationFailure(ANCHOR_DEGENERATE)` として失敗する。これは
    `ANCHOR_BASELINE_TOO_SHORT` とは独立した検査であることの直接証拠
    （design.md WorldFrameBuilder「Validation」/ A-9）。
    """

    def test_direction_parallel_to_normal_raises_anchor_degenerate(self) -> None:
        from world_frame_calibration import frame

        plane = _plane(normal=(0.0, 0.0, 1.0), distance_mm=1000.0)
        origin_anchor = _anchor("origin", AnchorRole.ORIGIN, (0.0, 0.0, 1000.0))
        # 方向ベクトル (0,0,2000) は法線 (0,0,1) と厳密に平行（縮退）。
        # ノルムは 2000mm で基線長下限(800mm)を優に上回るため、この失敗は
        # 基線長不足とは無関係であることが分かる。
        x_axis_anchor = _anchor("x_axis", AnchorRole.X_AXIS, (0.0, 0.0, 3000.0))
        limits = PlanLimits(min_baseline_mm=800.0)

        with pytest.raises(CalibrationFailure) as exc_info:
            frame.build_world_frame(plane, origin_anchor, x_axis_anchor, limits)

        assert exc_info.value.reason == FailureReason.ANCHOR_DEGENERATE
        assert exc_info.value.reason != FailureReason.ANCHOR_BASELINE_TOO_SHORT

    def test_degenerate_failure_context_reports_baseline_and_ratio(self) -> None:
        from world_frame_calibration import frame

        plane = _plane(normal=(0.0, 0.0, 1.0), distance_mm=1000.0)
        origin_anchor = _anchor("origin", AnchorRole.ORIGIN, (0.0, 0.0, 1000.0))
        x_axis_anchor = _anchor("x_axis", AnchorRole.X_AXIS, (0.0, 0.0, 3000.0))
        limits = PlanLimits(min_baseline_mm=800.0)

        with pytest.raises(CalibrationFailure) as exc_info:
            frame.build_world_frame(plane, origin_anchor, x_axis_anchor, limits)

        context = exc_info.value.context
        assert context.get("baseline_mm") == pytest.approx(2000.0, abs=1e-6)
        assert context.get("orthogonal_component_ratio") == pytest.approx(0.0, abs=1e-6)

    def test_nearly_in_plane_direction_does_not_raise_anchor_degenerate(self) -> None:
        """方向ベクトルが平面内に収まっていれば（縮退していなければ）、
        `ANCHOR_DEGENERATE` を送出せず正常に確立できる（偽陽性が無いことの
        確認）。
        """
        from world_frame_calibration import frame

        plane = _plane(normal=(0.0, 0.0, 1.0), distance_mm=1000.0)
        origin_anchor = _anchor("origin", AnchorRole.ORIGIN, (0.0, 0.0, 1000.0))
        x_axis_anchor = _anchor("x_axis", AnchorRole.X_AXIS, (1000.0, 0.0, 1000.0))
        limits = PlanLimits(min_baseline_mm=800.0)

        established = frame.build_world_frame(plane, origin_anchor, x_axis_anchor, limits)
        assert established.geometry.baseline_mm == pytest.approx(1000.0, abs=1e-9)


# ---------------------------------------------------------------------------
# ヨー感度: 実装ノート「タスク1.4」が要求する式を、本モジュールが実際に計算する
# ---------------------------------------------------------------------------


class TestBuildWorldFrameYawSensitivity:
    """基線長から `FrameGeometry.yaw_sensitivity_deg_per_mm` /
    `lateral_error_mm_per_mm_at_1000mm` を解析式で算出する（要件 2.8。
    実装ノート「タスク1.4」がタスク 3.1 に要求する計算そのもの）。
    """

    def _establish_with_baseline(self, baseline_mm: float):
        from world_frame_calibration import frame

        plane = _plane(normal=(0.0, 0.0, 1.0), distance_mm=1000.0)
        origin_anchor = _anchor("origin", AnchorRole.ORIGIN, (0.0, 0.0, 1000.0))
        x_axis_anchor = _anchor("x_axis", AnchorRole.X_AXIS, (baseline_mm, 0.0, 1000.0))
        limits = PlanLimits(min_baseline_mm=100.0)
        return frame.build_world_frame(plane, origin_anchor, x_axis_anchor, limits)

    def test_yaw_sensitivity_matches_independently_precomputed_literal_at_1000mm(
        self,
    ) -> None:
        """`baseline_mm=1000.0` について、このテストファイル内の式を再利用
        せず、独立に事前計算したリテラル値（タスク 1.4 のテストが固定した
        ものと同一のリテラル）と照合する。式のバグを検出できるようにする
        ため、同じ式での再計算による自己無矛盾チェックだけに頼らない。
        """
        established = self._establish_with_baseline(1000.0)
        assert established.geometry.yaw_sensitivity_deg_per_mm == pytest.approx(
            0.057295779513082325, rel=1e-12
        )

    def test_lateral_error_matches_independently_precomputed_literal_at_1000mm(
        self,
    ) -> None:
        established = self._establish_with_baseline(1000.0)
        assert established.geometry.lateral_error_mm_per_mm_at_1000mm == pytest.approx(
            1.0, rel=1e-12
        )

    @pytest.mark.parametrize("baseline_mm", [200.0, 500.0, 800.0, 1000.0, 2000.0, 3500.0])
    def test_yaw_sensitivity_matches_inverse_baseline_formula(self, baseline_mm: float) -> None:
        established = self._establish_with_baseline(baseline_mm)
        expected = math.degrees(1.0 / baseline_mm)
        assert established.geometry.yaw_sensitivity_deg_per_mm == pytest.approx(
            expected, rel=1e-12
        )

    @pytest.mark.parametrize("baseline_mm", [200.0, 500.0, 800.0, 1000.0, 2000.0, 3500.0])
    def test_lateral_error_matches_thousand_over_baseline_formula(
        self, baseline_mm: float
    ) -> None:
        established = self._establish_with_baseline(baseline_mm)
        expected = 1000.0 / baseline_mm
        assert established.geometry.lateral_error_mm_per_mm_at_1000mm == pytest.approx(
            expected, rel=1e-12
        )


# ---------------------------------------------------------------------------
# カメラ位置・姿勢を一切参照しない (要件 2.9)
# ---------------------------------------------------------------------------


class TestBuildWorldFrameDoesNotReferenceCameraPose:
    """`build_world_frame` のシグネチャがカメラの位置・姿勢に相当する引数を
    一切持たないことを静的に固定する（design.md WorldFrameBuilder
    「Responsibilities & Constraints」/ 要件 2.9: frame はマーカーの物理配置
    だけで決まる。カメラの設置位置に依存しない）。
    """

    def test_signature_has_exactly_the_four_non_camera_parameters(self) -> None:
        from world_frame_calibration import frame

        signature = inspect.signature(frame.build_world_frame)
        param_names = set(signature.parameters.keys())
        assert param_names == {"plane", "origin_anchor", "x_axis_anchor", "limits"}

    def test_no_parameter_name_mentions_camera_pose_or_extrinsics(self) -> None:
        from world_frame_calibration import frame

        signature = inspect.signature(frame.build_world_frame)
        forbidden_substrings = ("camera_pose", "pose", "extrinsic", "camera_position")
        for name in signature.parameters:
            lowered = name.lower()
            for forbidden in forbidden_substrings:
                assert (
                    forbidden not in lowered
                ), f"parameter {name!r} appears to reference camera pose/extrinsics"

    def test_same_marker_placement_reproduces_identical_frame_regardless_of_camera(
        self,
    ) -> None:
        """カメラの位置・姿勢を表す情報を一切与えていない（`plane.normal` と
        マーカーの `point_on_plane_mm` だけで frame が決まる）ため、同一の
        マーカー物理配置を表す入力から常に同一の変換が得られる（要件 2.9 の
        直接的な観測可能な証拠。カメラ姿勢自体を入力に含めていないため、
        呼び出しを繰り返しても同一物理配置なら同一結果になることを示す）。
        """
        from world_frame_calibration import frame

        plane = _plane(normal=(0.0, 0.0, 1.0), distance_mm=1000.0)
        origin_anchor = _anchor("origin", AnchorRole.ORIGIN, (0.0, 0.0, 1000.0))
        x_axis_anchor = _anchor("x_axis", AnchorRole.X_AXIS, (1000.0, 0.0, 1000.0))
        limits = PlanLimits(min_baseline_mm=800.0)

        established_a = frame.build_world_frame(plane, origin_anchor, x_axis_anchor, limits)
        established_b = frame.build_world_frame(plane, origin_anchor, x_axis_anchor, limits)

        assert established_a.transform.rotation == established_b.transform.rotation
        assert established_a.transform.translation_mm == established_b.transform.translation_mm


# ---------------------------------------------------------------------------
# docstring: 回転行列の各行が World の各軸であることの明記
# ---------------------------------------------------------------------------


class TestBuildWorldFrameDocumentsRotationRowConvention:
    """`WorldTransform.rotation` の各行がカメラ座標系で表した World の各軸で
    あることが `build_world_frame` の docstring（またはモジュール docstring）
    に明記されている（design.md WorldFrameBuilder「Integration」の明示的な
    docstring 要求）。
    """

    def test_docstring_or_module_docstring_documents_row_axis_convention(self) -> None:
        from world_frame_calibration import frame

        combined_doc = (frame.__doc__ or "") + (frame.build_world_frame.__doc__ or "")
        mentions_row = "各行" in combined_doc or "row" in combined_doc.lower()
        mentions_axis = "軸" in combined_doc or "axis" in combined_doc.lower()
        assert mentions_row and mentions_axis
