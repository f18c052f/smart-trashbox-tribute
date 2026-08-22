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

import dataclasses
import importlib.util
import inspect
import math
import sys
from pathlib import Path

import numpy as np
import pytest

from world_frame_calibration import anchors as anchors_module
from world_frame_calibration import plane as plane_module
from world_frame_calibration.errors import CalibrationFailure, FailureReason
from world_frame_calibration.plan import AnchorSpec, PlanLimits
from world_frame_calibration.types import (
    AnchorObservation,
    AnchorRole,
    Intrinsics,
    PixelRegion,
    Plane,
    PlaneQuality,
)

# --- tests/world_frame_calibration/synthetic.py の衝突耐性ロード ---------
# `tests/sensing_foundation/synthetic.py` と裸のモジュール名 `synthetic` が
# 衝突するため、`importlib.util.spec_from_file_location` によるパス指定
# ロードを使う（synthetic.py のモジュール docstring /
# `test_world_frame_calibration_synthetic.py` の参照実装のとおり。
# tasks.md 実装ノート・タスク1.6が本ファイルをこの衝突の影響先として名指し
# している）。
_SYNTHETIC_PATH = Path(__file__).resolve().parent / "synthetic.py"
_spec = importlib.util.spec_from_file_location(
    "world_frame_calibration._test_synthetic_for_frame", _SYNTHETIC_PATH
)
assert _spec is not None and _spec.loader is not None
_synthetic = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _synthetic
_spec.loader.exec_module(_synthetic)

MarkerBox = _synthetic.MarkerBox
camera_pose_looking_at_floor = _synthetic.camera_pose_looking_at_floor
generate_synthetic_depth = _synthetic.generate_synthetic_depth

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


# ---------------------------------------------------------------------------
# タスク 3.2: カメラ姿勢非依存性と縮退分岐を回帰テストで固定する
# （要件 2.8, 2.9, 9.5）
# ---------------------------------------------------------------------------
#
# 上のクラス群（task 3.1）は `build_world_frame` 単体の契約を、直接構築した
# `Plane` / `AnchorObservation` で固定していた。ここから先は、実際の
# パイプライン全体（`deproject.deproject_region` → `plane.estimate_floor_plane`
# → `anchors.observe_anchor` → `frame.build_world_frame`）を
# `tests/world_frame_calibration/synthetic.py` の合成 Depth 生成器で駆動する
# 回帰テストを追加する（tasks.md タスク 3.2「観測可能な完了状態」）。
#
# **`ANCHOR_DEGENERATE` を除く5分岐**をここでフルパイプラインから再現する。
# 実装ノート「タスク3.1」が明らかにしたとおり、`ANCHOR_DEGENERATE` は
# 原点マーカー・方向マーカーの `point_on_plane_mm` が常に同一の `Plane` へ
# 投影済みであるという契約（タスク 2.4 `AnchorObserver` の責務）のため、
# その差分ベクトルは構造的に常に平面内（Z 直交）になり、フルパイプライン
# （カメラ姿勢 → Depth → 平面推定 → マーカー観測）経由では原理的に発火
# しえない（`point_on_plane_mm` が渡された `plane` と矛盾する不整合入力
# でしか発火しない）。この分岐は本ファイル冒頭の
# `TestBuildWorldFrameDirectionDegenerate`（task 3.1。直接構築した
# `Plane` / `AnchorObservation` による再現）が既に固定しており、フル
# パイプラインでの再現を試みる追加の努力はしない（tasks.md タスク 3.2の
# 指示、および Implementation Notes 「タスク3.1」）。


def _pipeline_intrinsics(
    *,
    width_px: int,
    height_px: int,
    fx_px: float,
    fy_px: float,
    ppx_px: float | None = None,
    ppy_px: float | None = None,
) -> Intrinsics:
    """フルパイプライン回帰テスト用の `Intrinsics`（歪みなし）。"""
    return Intrinsics(
        width_px=width_px,
        height_px=height_px,
        fx_px=fx_px,
        fy_px=fy_px,
        ppx_px=ppx_px if ppx_px is not None else width_px / 2.0,
        ppy_px=ppy_px if ppy_px is not None else height_px / 2.0,
        model="none",
        coeffs=(0.0, 0.0, 0.0, 0.0, 0.0),
    )


def _pipeline_region(intr: Intrinsics) -> PixelRegion:
    return PixelRegion(x0_px=0, y0_px=0, x1_px=intr.width_px, y1_px=intr.height_px)


def _pixel_for_world_point(pose, intr: Intrinsics, world_point_mm) -> tuple[float, float]:
    """既知の World 座標点を、指定したカメラ姿勢・内部パラメータで撮影した
    ときの画素座標 (u, v) へ解析的に逆算する。

    `synthetic.generate_synthetic_depth` のレイキャスト規約
    （`d_cam = ((u-ppx)/fx, (v-ppy)/fy, 1)`、画素中心規約に `+0.5` を
    加えない）と厳密に整合する逆変換であり、複数マーカーが近接する
    シーンで**マーカーごとに重ならない探索範囲**を機械的に求めるために
    使う（`AnchorSpec.region` を画像全体にすると、同じ高さ帯を持つ複数
    マーカーの点群が1つの `observe_anchor` 呼び出しへ混ざってしまうため）。
    """
    rotation = pose.rotation_matrix()
    position = pose.position_vector()
    diff = np.asarray(world_point_mm, dtype=np.float64) - position
    cam_vec = rotation.T @ diff
    d_cam = cam_vec / cam_vec[2]
    u = d_cam[0] * intr.fx_px + intr.ppx_px
    v = d_cam[1] * intr.fy_px + intr.ppy_px
    return float(u), float(v)


def _region_around_world_point(
    pose, intr: Intrinsics, world_point_mm, *, half_extent_px: float
) -> PixelRegion:
    """`world_point_mm` の投影画素を中心に、半径 `half_extent_px` 画素の
    矩形範囲を画像内へクリップして返す（`_pixel_for_world_point` 参照）。
    """
    u, v = _pixel_for_world_point(pose, intr, world_point_mm)
    x0 = max(0, int(math.floor(u - half_extent_px)))
    y0 = max(0, int(math.floor(v - half_extent_px)))
    x1 = min(intr.width_px, int(math.ceil(u + half_extent_px)))
    y1 = min(intr.height_px, int(math.ceil(v + half_extent_px)))
    return PixelRegion(x0_px=x0, y0_px=y0, x1_px=x1, y1_px=y1)


_PIPELINE_LIMITS = PlanLimits(
    min_inlier_points=500,
    min_inlier_ratio=0.5,
    plane_inlier_threshold_mm=15.0,
    min_incidence_angle_deg=10.0,
    min_baseline_mm=100.0,
    min_anchor_samples=30,
    rng_seed=11,
)
"""フルパイプライン回帰テストの既定下限群。基線長下限(100mm)は本節の
既定マーカー配置（基線長 500mm）を安定して満たすが、意図的な短基線
テストでは呼び出しごとに上書きする。"""


class TestBuildWorldFrameCameraPoseIndependenceFullPipeline:
    """合成生成器でカメラ姿勢だけを変え、マーカー配置が同じなら、実際の
    パイプライン全体を通しても同一の World 座標が得られることを固定する
    （tasks.md タスク 3.2「観測可能な完了状態」/ 要件 2.9）。

    `TestBuildWorldFrameDoesNotReferenceCameraPose`（task 3.1）は
    `build_world_frame` のシグネチャがカメラ姿勢を受け取らないことを
    静的に固定していた。本クラスは、3通りの異なるカメラ姿勢から合成
    Depth を実際に生成し、パイプライン全体（平面推定・マーカー観測を
    含む）を通した末に数値的に一致する結果が得られることまで確認する、
    より強い観測可能な証拠を追加する。
    """

    _ORIGIN_MARKER = MarkerBox(
        label="origin",
        center_xy_world_mm=(0.0, 0.0),
        size_xy_mm=(220.0, 220.0),
        height_mm=60.0,
    )
    _X_AXIS_MARKER = MarkerBox(
        label="x_axis",
        center_xy_world_mm=(500.0, 0.0),
        size_xy_mm=(220.0, 220.0),
        height_mm=60.0,
    )
    _VERIFICATION_MARKER = MarkerBox(
        label="verification",
        center_xy_world_mm=(250.0, 350.0),
        size_xy_mm=(180.0, 180.0),
        height_mm=60.0,
    )
    _MARKERS = (_ORIGIN_MARKER, _X_AXIS_MARKER, _VERIFICATION_MARKER)

    # 高めの解像度・焦点距離を使い、マーカー観測のロバスト代表点が受ける
    # 画素量子化の影響を抑える（3姿勢間の数値一致をより厳密な許容差で
    # 確認できるようにするため）。
    _INTRINSICS = _pipeline_intrinsics(width_px=300, height_px=240, fx_px=180.0, fy_px=180.0)

    # カメラ姿勢だけを変える（位置・yaw を変えても pitch=90 で常に床を
    # 真下に見込むため、マーカー物理配置が視野内に収まる）。マーカー
    # 配置（`_MARKERS`）は3姿勢すべてで完全に同一。
    _POSES = (
        camera_pose_looking_at_floor(
            position_world_mm=(250.0, 0.0, 2000.0), yaw_deg=0.0, pitch_deg=90.0
        ),
        camera_pose_looking_at_floor(
            position_world_mm=(320.0, 70.0, 2400.0), yaw_deg=35.0, pitch_deg=90.0
        ),
        camera_pose_looking_at_floor(
            position_world_mm=(150.0, -80.0, 1800.0), yaw_deg=-50.0, pitch_deg=90.0
        ),
    )

    _HALF_EXTENT_PX = 16.0
    """各マーカーの探索範囲の半径（画素）。マーカー物理配置
    （基線長 500mm、原点/方向/検証の3マーカー）が3姿勢すべてで互いに
    重ならない範囲になるよう選んだ値（`_region_around_world_point` 参照。
    範囲を画像全体にすると、同じ高さ帯を持つ複数マーカーの点群が
    1つの `observe_anchor` 呼び出しへ混ざってしまう）。"""

    def _establish(self, pose):
        synth = generate_synthetic_depth(
            pose, self._INTRINSICS, markers=self._MARKERS, rng_seed=0
        )
        floor_region = _pipeline_region(self._INTRINSICS)
        plane = plane_module.estimate_floor_plane(synth.depth, floor_region, _PIPELINE_LIMITS)

        origin_region = _region_around_world_point(
            pose, self._INTRINSICS, self._ORIGIN_MARKER.center_xy_world_mm + (0.0,),
            half_extent_px=self._HALF_EXTENT_PX,
        )
        x_axis_region = _region_around_world_point(
            pose, self._INTRINSICS, self._X_AXIS_MARKER.center_xy_world_mm + (0.0,),
            half_extent_px=self._HALF_EXTENT_PX,
        )
        verification_region = _region_around_world_point(
            pose, self._INTRINSICS, self._VERIFICATION_MARKER.center_xy_world_mm + (0.0,),
            half_extent_px=self._HALF_EXTENT_PX,
        )

        origin_spec = AnchorSpec(
            label="origin", region=origin_region, height_band_mm=(10.0, 120.0)
        )
        x_axis_spec = AnchorSpec(
            label="x_axis", region=x_axis_region, height_band_mm=(10.0, 120.0)
        )
        verification_spec = AnchorSpec(
            label="verification", region=verification_region, height_band_mm=(10.0, 120.0)
        )

        origin_obs = anchors_module.observe_anchor(
            synth.depth, plane, origin_spec, AnchorRole.ORIGIN, _PIPELINE_LIMITS
        )
        x_axis_obs = anchors_module.observe_anchor(
            synth.depth, plane, x_axis_spec, AnchorRole.X_AXIS, _PIPELINE_LIMITS
        )
        verification_obs = anchors_module.observe_anchor(
            synth.depth, plane, verification_spec, AnchorRole.VERIFICATION, _PIPELINE_LIMITS
        )

        from world_frame_calibration import frame

        established = frame.build_world_frame(plane, origin_obs, x_axis_obs, _PIPELINE_LIMITS)
        return established, verification_obs

    def test_independent_point_recovers_the_same_world_coordinates_across_three_poses(
        self,
    ) -> None:
        """確立に使っていない検証マーカー（`_VERIFICATION_MARKER`, 既知の
        World 座標 (250, 350, 0)）を3通りの異なるカメラ姿勢それぞれで観測
        し、その姿勢ごとに得た変換で World 座標へ写すと、すべて既知の
        物理位置および互いに数値誤差の範囲で一致する（要件 2.9）。
        """
        recovered_points = []
        for pose in self._POSES:
            established, verification_obs = self._establish(pose)
            recovered = established.transform.apply_point(
                verification_obs.point_on_plane_mm
            )
            recovered_points.append(np.asarray(recovered, dtype=np.float64))

        truth = np.array([250.0, 350.0, 0.0])
        for recovered in recovered_points:
            np.testing.assert_allclose(recovered, truth, atol=10.0)

        for other in recovered_points[1:]:
            np.testing.assert_allclose(other, recovered_points[0], atol=12.0)

    def test_baseline_and_yaw_sensitivity_agree_across_three_poses(self) -> None:
        """基線長（原点マーカー・方向マーカーの物理配置）はカメラ姿勢に
        依存しないため、3姿勢で算出した `baseline_mm` は数値誤差の範囲で
        一致する。さらに、それぞれの姿勢で得た基線長からヨー感度・横方向
        誤差係数を解析式（`degrees(1/baseline_mm)` / `1000/baseline_mm`）で
        独立に計算した値と突き合わせ、感度の算出そのものがカメラ姿勢に
        依存しないことを確かめる（要件 2.8, 2.9）。
        """
        established_list = [self._establish(pose)[0] for pose in self._POSES]
        baselines = [est.geometry.baseline_mm for est in established_list]

        for baseline in baselines:
            assert baseline == pytest.approx(baselines[0], rel=0.02)
        assert baselines[0] == pytest.approx(500.0, rel=0.02)

        for est in established_list:
            expected_yaw_sensitivity = math.degrees(1.0 / est.geometry.baseline_mm)
            assert est.geometry.yaw_sensitivity_deg_per_mm == pytest.approx(
                expected_yaw_sensitivity, rel=1e-9
            )
            expected_lateral_error = 1000.0 / est.geometry.baseline_mm
            assert est.geometry.lateral_error_mm_per_mm_at_1000mm == pytest.approx(
                expected_lateral_error, rel=1e-9
            )


class TestDegenerateBranchesReproducedThroughTheFullSyntheticPipeline:
    """縮退6分岐のうち、`ANCHOR_DEGENERATE` を除く5分岐
    （内点不足・浅い入射角・マーカー未検出・基線長不足・非対応の歪み）を、
    実機を用いず合成 Depth からフルパイプラインで再現する（tasks.md
    タスク 3.2「観測可能な完了状態」/ 要件 9.5）。各失敗が「どの下限に
    対してどの実測値だったか」を `context` から読み取れることも確認する
    （tasks.md タスク 3.2 Critical constraint）。
    """

    def test_insufficient_depth_points_reproduced_from_sparse_synthetic_depth(
        self,
    ) -> None:
        """有効画素をほぼ無効化した合成 Depth を `plane.estimate_floor_plane`
        （内部で `deproject.deproject_region` を呼ぶ実パイプライン）へ渡す
        と、RANSAC を試みる前に `INSUFFICIENT_DEPTH_POINTS` として失敗する
        （要件 5.1, 9.5）。`context` の実測点数が下限を下回っていることを
        直接確認する。
        """
        intr = _pipeline_intrinsics(width_px=40, height_px=30, fx_px=80.0, fy_px=80.0)
        pose = camera_pose_looking_at_floor(
            position_world_mm=(0.0, 0.0, 1500.0), yaw_deg=0.0, pitch_deg=90.0
        )
        synth = generate_synthetic_depth(pose, intr, invalid_fraction=0.99, rng_seed=5)
        region = _pipeline_region(intr)
        limits = PlanLimits(min_inlier_points=50, rng_seed=1)

        with pytest.raises(CalibrationFailure) as exc_info:
            plane_module.estimate_floor_plane(synth.depth, region, limits)

        assert exc_info.value.reason == FailureReason.INSUFFICIENT_DEPTH_POINTS
        context = exc_info.value.context
        assert context["min_inlier_points"] == 50
        assert context["points_considered"] < 50

    def test_incidence_angle_too_shallow_reproduced_from_grazing_synthetic_view(
        self,
    ) -> None:
        """入射角が下限を下回る合成姿勢（`pitch_deg` が浅い）から生成した
        Depth を `plane.estimate_floor_plane` へ渡すと
        `INCIDENCE_ANGLE_TOO_SHALLOW` として失敗する（要件 5.4, 9.5）。
        `camera_pose_looking_at_floor` は `pitch_deg` が入射角そのものに
        なるよう構成されているため（synthetic.py モジュール docstring）、
        実測 `incidence_angle_deg` が `pitch_deg` と一致することも確認する。
        """
        intr = _pipeline_intrinsics(width_px=80, height_px=60, fx_px=250.0, fy_px=250.0)
        pose = camera_pose_looking_at_floor(
            position_world_mm=(0.0, 0.0, 800.0), yaw_deg=0.0, pitch_deg=6.0
        )
        synth = generate_synthetic_depth(pose, intr, rng_seed=3)
        region = _pipeline_region(intr)
        limits = PlanLimits(
            min_inlier_points=30,
            min_inlier_ratio=0.3,
            plane_inlier_threshold_mm=15.0,
            min_incidence_angle_deg=10.0,
            rng_seed=3,
        )

        with pytest.raises(CalibrationFailure) as exc_info:
            plane_module.estimate_floor_plane(synth.depth, region, limits)

        assert exc_info.value.reason == FailureReason.INCIDENCE_ANGLE_TOO_SHALLOW
        context = exc_info.value.context
        assert context["min_incidence_angle_deg"] == 10.0
        assert context["incidence_angle_deg"] < 10.0
        assert math.isclose(context["incidence_angle_deg"], 6.0, abs_tol=1.0)

    def test_anchor_not_found_reproduced_when_height_band_misses_the_marker(
        self,
    ) -> None:
        """合成 Depth 上に実在するマーカーであっても、その実際の高さ
        （60mm）を外れた `AnchorSpec.height_band_mm` を与えれば
        `ANCHOR_NOT_FOUND` として失敗する（マーカーが「観測できなかった」
        状態の直接的な再現。要件 5.2, 9.5）。
        """
        intr = _pipeline_intrinsics(width_px=100, height_px=80, fx_px=100.0, fy_px=100.0)
        pose = camera_pose_looking_at_floor(
            position_world_mm=(250.0, 0.0, 2000.0), yaw_deg=0.0, pitch_deg=90.0
        )
        origin_marker = MarkerBox(
            label="origin",
            center_xy_world_mm=(0.0, 0.0),
            size_xy_mm=(220.0, 220.0),
            height_mm=60.0,
        )
        x_axis_marker = MarkerBox(
            label="x_axis",
            center_xy_world_mm=(500.0, 0.0),
            size_xy_mm=(220.0, 220.0),
            height_mm=60.0,
        )
        synth = generate_synthetic_depth(
            pose, intr, markers=[origin_marker, x_axis_marker], rng_seed=0
        )
        region = _pipeline_region(intr)
        plane = plane_module.estimate_floor_plane(synth.depth, region, _PIPELINE_LIMITS)

        # x_axis マーカーの実際の高さは 60mm だが、探索する高さ帯を意図的
        # に (200, 300) へずらす。マーカーは合成 Depth 上に実在するが、
        # この高さ帯には一切の点が入らない。
        wrong_band_spec = AnchorSpec(
            label="x_axis", region=region, height_band_mm=(200.0, 300.0)
        )

        with pytest.raises(CalibrationFailure) as exc_info:
            anchors_module.observe_anchor(
                synth.depth, plane, wrong_band_spec, AnchorRole.X_AXIS, _PIPELINE_LIMITS
            )

        assert exc_info.value.reason == FailureReason.ANCHOR_NOT_FOUND
        context = exc_info.value.context
        assert context["label"] == "x_axis"
        assert context["sample_count"] < _PIPELINE_LIMITS.min_anchor_samples
        assert context["min_anchor_samples"] == _PIPELINE_LIMITS.min_anchor_samples

    def test_anchor_baseline_too_short_reproduced_from_closely_placed_synthetic_markers(
        self,
    ) -> None:
        """物理的に近接した2マーカー（基線長 50mm）を合成配置し、フル
        パイプライン（平面推定 → 両マーカー観測 → frame 確立）を通すと、
        平面推定・マーカー観測は成功するが `build_world_frame` が
        `ANCHOR_BASELINE_TOO_SHORT` として失敗する（要件 2.7, 5.3, 9.5）。
        """
        # 通常より長い焦点距離(高解像度)を使い、50mm という近接した基線でも
        # 2マーカーが画素空間で重ならない別々の探索範囲を確保できるように
        # する（`_region_around_world_point` で個別に算出する）。
        intr = _pipeline_intrinsics(width_px=100, height_px=80, fx_px=600.0, fy_px=600.0)
        pose = camera_pose_looking_at_floor(
            position_world_mm=(25.0, 0.0, 1500.0), yaw_deg=0.0, pitch_deg=90.0
        )
        origin_marker = MarkerBox(
            label="origin",
            center_xy_world_mm=(0.0, 0.0),
            size_xy_mm=(20.0, 20.0),
            height_mm=60.0,
        )
        x_axis_marker = MarkerBox(
            label="x_axis",
            center_xy_world_mm=(50.0, 0.0),
            size_xy_mm=(20.0, 20.0),
            height_mm=60.0,
        )
        synth = generate_synthetic_depth(
            pose, intr, markers=[origin_marker, x_axis_marker], rng_seed=0
        )
        floor_region = _pipeline_region(intr)
        limits = PlanLimits(
            min_inlier_points=200,
            min_inlier_ratio=0.5,
            plane_inlier_threshold_mm=15.0,
            min_incidence_angle_deg=10.0,
            min_baseline_mm=800.0,
            min_anchor_samples=10,
            rng_seed=2,
        )

        plane = plane_module.estimate_floor_plane(synth.depth, floor_region, limits)
        origin_region = _region_around_world_point(
            pose, intr, origin_marker.center_xy_world_mm + (0.0,), half_extent_px=6.0
        )
        x_axis_region = _region_around_world_point(
            pose, intr, x_axis_marker.center_xy_world_mm + (0.0,), half_extent_px=6.0
        )
        origin_spec = AnchorSpec(
            label="origin", region=origin_region, height_band_mm=(10.0, 120.0)
        )
        x_axis_spec = AnchorSpec(
            label="x_axis", region=x_axis_region, height_band_mm=(10.0, 120.0)
        )
        origin_obs = anchors_module.observe_anchor(
            synth.depth, plane, origin_spec, AnchorRole.ORIGIN, limits
        )
        x_axis_obs = anchors_module.observe_anchor(
            synth.depth, plane, x_axis_spec, AnchorRole.X_AXIS, limits
        )

        from world_frame_calibration import frame

        with pytest.raises(CalibrationFailure) as exc_info:
            frame.build_world_frame(plane, origin_obs, x_axis_obs, limits)

        assert exc_info.value.reason == FailureReason.ANCHOR_BASELINE_TOO_SHORT
        context = exc_info.value.context
        assert context["min_baseline_mm"] == 800.0
        assert context["baseline_mm"] < 800.0
        assert math.isclose(context["baseline_mm"], 50.0, abs_tol=5.0)

    def test_unsupported_distortion_reproduced_from_synthetic_depth_with_nonzero_coeffs(
        self,
    ) -> None:
        """歪み係数が非ゼロの `Intrinsics` を持つ合成 Depth を
        `plane.estimate_floor_plane`（内部で `deproject.deproject_region` →
        `ensure_supported_distortion` を呼ぶ）へ渡すと、
        `UNSUPPORTED_DISTORTION` として失敗する（要件 3.5, 9.5）。合成
        レンダラー自体は理想的なピンホールレイキャストであり歪みを適用
        しないが（synthetic.py モジュール docstring）、
        `ensure_supported_distortion` は `Intrinsics.coeffs` フィールドの
        値だけを検査する番人であるため、シーン内容とは独立に非ゼロ係数を
        拒否することを確認する。
        """
        clean_intr = _pipeline_intrinsics(width_px=60, height_px=45, fx_px=120.0, fy_px=120.0)
        distorted_intr = dataclasses.replace(
            clean_intr, model="brown_conrady", coeffs=(0.01, 0.0, 0.0, 0.0, 0.0)
        )
        pose = camera_pose_looking_at_floor(
            position_world_mm=(0.0, 0.0, 1500.0), yaw_deg=0.0, pitch_deg=65.0
        )
        synth = generate_synthetic_depth(pose, clean_intr, rng_seed=0)
        distorted_depth = dataclasses.replace(synth.depth, intrinsics=distorted_intr)
        region = _pipeline_region(distorted_intr)

        with pytest.raises(CalibrationFailure) as exc_info:
            plane_module.estimate_floor_plane(distorted_depth, region, _PIPELINE_LIMITS)

        assert exc_info.value.reason == FailureReason.UNSUPPORTED_DISTORTION
        context = exc_info.value.context
        assert context["model"] == "brown_conrady"
        assert context["coeffs"] == (0.01, 0.0, 0.0, 0.0, 0.0)
