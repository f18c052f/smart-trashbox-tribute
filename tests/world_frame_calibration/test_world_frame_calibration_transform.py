"""world_frame_calibration.transform の剛体変換の保持と適用の検証
(tasks.md タスク 2.2 / 要件 3.1, 3.2, 3.3, 3.6, 7.4, 11.4)。

design.md「WorldTransform」コンポーネント契約を固定する。本テストが確認すること:

- 単点 (`apply_point`) と点群 (`apply`) の変換結果が一致すること（要件 3.1, 3.2）
- 構築時に正規直交性と行列式 +1 を検査し、外れれば構築を拒否すること
  （拡大縮小・せん断・鏡映のいずれも拒否する。要件 3.3）
- `inverse()` を通した往復変換が数値誤差の範囲で恒等になること
- `as_matrix()` が同次変換として `apply_point` と整合すること
- `compare_transforms` が既知の平行移動とヨー回転を、原点のずれとヨーへ正しく
  分離し、他の成分（Z 軸のずれ・全体角のヨー超過分）が ~0 で読めること
- `apply` / `apply_point` の適用にカメラ・SDK・ファイル入出力を要求しないこと
  （import 面から静的に固定する。要件 3.6, 11.4）
- `apply` の本体にログ送出・確保・検証を一切含まないこと（毎フレーム走る
  唯一の経路であるため。tasks.md タスク 2.2 の Critical constraint）
"""

from __future__ import annotations

import ast
import inspect
import math
import textwrap

import numpy as np
import pytest

from world_frame_calibration import transform
from world_frame_calibration.errors import CalibrationFailure, FailureReason

IDENTITY_ROTATION = (
    (1.0, 0.0, 0.0),
    (0.0, 1.0, 0.0),
    (0.0, 0.0, 1.0),
)


def _rotation_about_z_deg(theta_deg: float) -> tuple[tuple[float, float, float], ...]:
    theta = math.radians(theta_deg)
    c, s = math.cos(theta), math.sin(theta)
    return (
        (c, -s, 0.0),
        (s, c, 0.0),
        (0.0, 0.0, 1.0),
    )


def _rotation_about_x_deg(phi_deg: float) -> tuple[tuple[float, float, float], ...]:
    phi = math.radians(phi_deg)
    c, s = math.cos(phi), math.sin(phi)
    return (
        (1.0, 0.0, 0.0),
        (0.0, c, -s),
        (0.0, s, c),
    )


class TestConstructionValidatesRotation:
    """構築時に正規直交性と行列式 +1 を検査し、外れれば拒否することを固定する
    (design.md WorldTransform「Preconditions」/ tasks.md タスク 2.2)。
    """

    def test_identity_rotation_is_accepted(self) -> None:
        t = transform.WorldTransform(
            rotation=IDENTITY_ROTATION, translation_mm=(0.0, 0.0, 0.0)
        )
        assert t.rotation == IDENTITY_ROTATION

    def test_known_rotation_matrix_is_accepted(self) -> None:
        t = transform.WorldTransform(
            rotation=_rotation_about_z_deg(30.0), translation_mm=(10.0, 20.0, 30.0)
        )
        assert t.translation_mm == (10.0, 20.0, 30.0)

    def test_scaled_matrix_is_rejected(self) -> None:
        """拡大縮小を含む行列は正規直交でないため
        `CalibrationFailure(ROTATION_NOT_ORTHONORMAL)` で拒否される
        （design.md WorldTransform「Preconditions」）。
        """
        scaled = tuple(
            tuple(v * 2.0 for v in row) for row in IDENTITY_ROTATION
        )
        with pytest.raises(CalibrationFailure) as exc_info:
            transform.WorldTransform(rotation=scaled, translation_mm=(0.0, 0.0, 0.0))
        assert exc_info.value.reason == FailureReason.ROTATION_NOT_ORTHONORMAL
        context = exc_info.value.context
        assert context["orthonormal_residual"] > context["orthonormal_tol"]

    def test_sheared_matrix_is_rejected(self) -> None:
        """せん断を含む行列は正規直交でないため
        `CalibrationFailure(ROTATION_NOT_ORTHONORMAL)` で拒否される。
        """
        sheared = (
            (1.0, 0.5, 0.0),
            (0.0, 1.0, 0.0),
            (0.0, 0.0, 1.0),
        )
        with pytest.raises(CalibrationFailure) as exc_info:
            transform.WorldTransform(rotation=sheared, translation_mm=(0.0, 0.0, 0.0))
        assert exc_info.value.reason == FailureReason.ROTATION_NOT_ORTHONORMAL
        context = exc_info.value.context
        assert context["orthonormal_residual"] > context["orthonormal_tol"]

    def test_reflection_matrix_is_rejected(self) -> None:
        """正規直交だが行列式が -1（鏡映）の行列は
        `CalibrationFailure(ROTATION_NOT_ORTHONORMAL)` で拒否される（要件 3.3）。
        """
        reflection = (
            (1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
            (0.0, 0.0, -1.0),
        )
        # sanity: this matrix genuinely is orthonormal but improper.
        r = np.asarray(reflection)
        assert np.allclose(r.T @ r, np.eye(3))
        assert math.isclose(float(np.linalg.det(r)), -1.0, abs_tol=1e-9)
        with pytest.raises(CalibrationFailure) as exc_info:
            transform.WorldTransform(
                rotation=reflection, translation_mm=(0.0, 0.0, 0.0)
            )
        assert exc_info.value.reason == FailureReason.ROTATION_NOT_ORTHONORMAL
        context = exc_info.value.context
        assert context["determinant"] == pytest.approx(-1.0, abs=1e-9)
        assert context["determinant_tol"] > 0.0


class TestApplyPointAndApplyAgree:
    """単点と点群の変換結果が一致することを固定する（要件 3.1, 3.2）。"""

    def test_apply_point_matches_manual_computation(self) -> None:
        t = transform.WorldTransform(
            rotation=_rotation_about_z_deg(90.0), translation_mm=(100.0, 0.0, 0.0)
        )
        result = t.apply_point((1.0, 0.0, 0.0))
        # R_z(90) @ (1,0,0) = (0,1,0); + translation (100,0,0) = (100,1,0)
        assert result == pytest.approx((100.0, 1.0, 0.0), abs=1e-9)

    def test_apply_on_point_cloud_matches_apply_point_elementwise(self) -> None:
        t = transform.WorldTransform(
            rotation=_rotation_about_z_deg(37.0), translation_mm=(5.0, -3.0, 12.0)
        )
        points = np.array(
            [
                [0.0, 0.0, 0.0],
                [10.0, 20.0, 30.0],
                [-5.0, 7.0, 2.0],
                [100.0, -100.0, 50.0],
            ]
        )
        cloud_result = t.apply(points)
        for i, point in enumerate(points):
            single_result = t.apply_point(tuple(point))
            assert cloud_result[i] == pytest.approx(single_result, abs=1e-9)

    def test_apply_returns_float64_array(self) -> None:
        t = transform.WorldTransform(
            rotation=IDENTITY_ROTATION, translation_mm=(1.0, 2.0, 3.0)
        )
        points = np.array([[0.0, 0.0, 0.0]])
        result = t.apply(points)
        assert result.dtype == np.float64

    def test_apply_does_not_mutate_input_array(self) -> None:
        t = transform.WorldTransform(
            rotation=_rotation_about_z_deg(45.0), translation_mm=(1.0, 2.0, 3.0)
        )
        points = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])
        original = points.copy()
        t.apply(points)
        assert np.array_equal(points, original)

    def test_apply_on_floor_point_preserves_z_zero_under_identity(self) -> None:
        """床平面上の点（z=0）を恒等変換すれば z 成分は 0 のままである（要件 3.7 の単純な健全性確認）。"""
        t = transform.WorldTransform(
            rotation=IDENTITY_ROTATION, translation_mm=(0.0, 0.0, 0.0)
        )
        points = np.array([[10.0, 20.0, 0.0], [-5.0, 3.0, 0.0]])
        result = t.apply(points)
        assert np.allclose(result[:, 2], 0.0, atol=1e-9)


class TestInverseRoundTrip:
    """`inverse()` を通した往復変換が恒等になることを固定する。"""

    def test_inverse_of_identity_is_identity(self) -> None:
        t = transform.WorldTransform(
            rotation=IDENTITY_ROTATION, translation_mm=(0.0, 0.0, 0.0)
        )
        inv = t.inverse()
        assert np.allclose(np.asarray(inv.rotation), np.asarray(IDENTITY_ROTATION), atol=1e-9)
        assert inv.translation_mm == pytest.approx((0.0, 0.0, 0.0), abs=1e-9)

    def test_apply_then_apply_inverse_round_trips_to_identity(self) -> None:
        t = transform.WorldTransform(
            rotation=_rotation_about_z_deg(64.0), translation_mm=(15.0, -7.0, 3.0)
        )
        points = np.array(
            [
                [1.0, 2.0, 3.0],
                [-10.0, 5.0, 0.0],
                [123.4, -56.7, 89.0],
            ]
        )
        world_points = t.apply(points)
        camera_points_recovered = t.inverse().apply(world_points)
        assert np.allclose(camera_points_recovered, points, atol=1e-6)

    def test_apply_point_then_inverse_apply_point_round_trips(self) -> None:
        t = transform.WorldTransform(
            rotation=_rotation_about_x_deg(20.0), translation_mm=(1.0, 1.0, 1.0)
        )
        original = (5.0, 6.0, 7.0)
        world_point = t.apply_point(original)
        recovered = t.inverse().apply_point(world_point)
        assert recovered == pytest.approx(original, abs=1e-6)


class TestAsMatrix:
    """`as_matrix()` が同次変換として `apply_point` と整合することを固定する。"""

    def test_as_matrix_is_4x4(self) -> None:
        t = transform.WorldTransform(
            rotation=IDENTITY_ROTATION, translation_mm=(1.0, 2.0, 3.0)
        )
        m = t.as_matrix()
        assert m.shape == (4, 4)

    def test_as_matrix_bottom_row_is_homogeneous(self) -> None:
        t = transform.WorldTransform(
            rotation=_rotation_about_z_deg(15.0), translation_mm=(1.0, 2.0, 3.0)
        )
        m = t.as_matrix()
        assert np.allclose(m[3, :], [0.0, 0.0, 0.0, 1.0])

    def test_as_matrix_top_left_is_rotation_and_top_right_is_translation(self) -> None:
        rotation = _rotation_about_z_deg(15.0)
        translation = (1.0, 2.0, 3.0)
        t = transform.WorldTransform(rotation=rotation, translation_mm=translation)
        m = t.as_matrix()
        assert np.allclose(m[:3, :3], np.asarray(rotation))
        assert np.allclose(m[:3, 3], np.asarray(translation))

    def test_as_matrix_applied_to_homogeneous_point_matches_apply_point(self) -> None:
        t = transform.WorldTransform(
            rotation=_rotation_about_x_deg(50.0), translation_mm=(3.0, -2.0, 8.0)
        )
        p = (10.0, -20.0, 30.0)
        p_h = np.array([p[0], p[1], p[2], 1.0])
        expected = np.asarray(t.apply_point(p))
        result_h = t.as_matrix() @ p_h
        assert np.allclose(result_h[:3], expected, atol=1e-9)


class TestCompareTransforms:
    """`compare_transforms` が既知の平行移動とヨー回転を分離して返すことを固定する
    (design.md WorldTransform「Postconditions」/ tasks.md タスク 2.2 観測可能な完了状態)。
    """

    def test_identity_vs_identity_has_zero_difference(self) -> None:
        a = transform.WorldTransform(
            rotation=IDENTITY_ROTATION, translation_mm=(0.0, 0.0, 0.0)
        )
        b = transform.WorldTransform(
            rotation=IDENTITY_ROTATION, translation_mm=(0.0, 0.0, 0.0)
        )
        diff = transform.compare_transforms(a, b)
        assert diff.origin_shift_mm == pytest.approx((0.0, 0.0, 0.0), abs=1e-9)
        assert diff.origin_shift_norm_mm == pytest.approx(0.0, abs=1e-9)
        assert diff.axis_angle_deg == pytest.approx(0.0, abs=1e-9)
        assert diff.z_axis_angle_deg == pytest.approx(0.0, abs=1e-9)
        assert diff.yaw_angle_deg == pytest.approx(0.0, abs=1e-9)

    def test_known_translation_and_yaw_are_separated_from_other_components(
        self,
    ) -> None:
        """既知の平行移動とヨー回転だけを与えた差分が、原点のずれとヨーへ分離され、
        Z 軸のずれと非ヨー回転は ~0 で読める（tasks.md タスク 2.2 観測可能な完了状態）。
        """
        a = transform.WorldTransform(
            rotation=IDENTITY_ROTATION, translation_mm=(0.0, 0.0, 0.0)
        )
        known_translation = (50.0, -25.0, 0.0)
        known_yaw_deg = 12.0
        b = transform.WorldTransform(
            rotation=_rotation_about_z_deg(known_yaw_deg),
            translation_mm=known_translation,
        )
        diff = transform.compare_transforms(a, b)

        # Origin offset is separated out exactly.
        assert diff.origin_shift_mm == pytest.approx(known_translation, abs=1e-9)
        assert diff.origin_shift_norm_mm == pytest.approx(
            float(np.linalg.norm(known_translation)), abs=1e-9
        )
        # Yaw is separated out exactly.
        assert diff.yaw_angle_deg == pytest.approx(known_yaw_deg, abs=1e-6)
        # Z-axis deviation reads as ~0 (pure yaw does not tilt the floor normal).
        assert diff.z_axis_angle_deg == pytest.approx(0.0, abs=1e-6)
        # The non-yaw portion of the total rotation angle reads as ~0: for a
        # pure yaw rotation the total axis angle equals the yaw itself.
        assert diff.axis_angle_deg == pytest.approx(known_yaw_deg, abs=1e-6)
        assert diff.axis_angle_deg - diff.yaw_angle_deg == pytest.approx(
            0.0, abs=1e-6
        )

    def test_known_tilt_is_reported_as_z_axis_deviation_not_yaw(self) -> None:
        """床法線だけを傾けた（ヨーを含まない）差分は Z 軸のずれとして現れ、
        ヨーは ~0 で読める。
        """
        a = transform.WorldTransform(
            rotation=IDENTITY_ROTATION, translation_mm=(0.0, 0.0, 0.0)
        )
        known_tilt_deg = 8.0
        b = transform.WorldTransform(
            rotation=_rotation_about_x_deg(known_tilt_deg),
            translation_mm=(0.0, 0.0, 0.0),
        )
        diff = transform.compare_transforms(a, b)
        assert diff.z_axis_angle_deg == pytest.approx(known_tilt_deg, abs=1e-6)
        assert diff.yaw_angle_deg == pytest.approx(0.0, abs=1e-6)


class TestNoIODependencies:
    """適用にカメラ・SDK・ファイル入出力を要求しないことを import 面から固定する
    （要件 3.6, 11.4 / design.md WorldTransform「Integration」: 本モジュールは
    `sensing_foundation` にも `numpy` 以外の外部にも依存しない）。
    """

    def test_module_imports_only_numpy_stdlib_and_intra_package_symbols(self) -> None:
        """トップレベル import が `numpy` / 標準ライブラリ / 自パッケージの
        `errors` / `linalg` に限られ、`sensing_foundation` を含む上流や
        `cv2` / `pyrealsense2` / `prediction_core` を import しないことを固定する
        （design.md WorldTransform「Integration」/ 要件 3.6, 11.4）。
        """
        source = inspect.getsource(transform)
        tree = ast.parse(source)
        forbidden_prefixes = {
            "sensing_foundation",
            "cv2",
            "pyrealsense2",
            "prediction_core",
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    assert top not in forbidden_prefixes, (
                        f"transform.py must not import {top!r}"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module is not None:
                    top = node.module.split(".")[0]
                    assert top not in forbidden_prefixes, (
                        f"transform.py must not import {top!r}"
                    )
                    if top == "world_frame_calibration":
                        submodule = node.module.split(".")[1] if "." in node.module else None
                        assert submodule in {"errors", "linalg"}, (
                            "transform.py may only import world_frame_calibration."
                            f"errors / .linalg; found world_frame_calibration.{submodule}"
                        )

    def test_module_does_not_import_sensing_foundation(self) -> None:
        source = inspect.getsource(transform)
        tree = ast.parse(source)
        imported_top_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_top_names.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported_top_names.add(node.module.split(".")[0])
        assert "sensing_foundation" not in imported_top_names

    def test_construction_and_apply_require_no_camera_sdk_or_file_io(self) -> None:
        """変換の構築・適用がファイル I/O やハードウェア接続を一切行わないことを、
        ハードウェア・ファイルシステムに触れずに完了できることそのもので示す。
        """
        t = transform.WorldTransform(
            rotation=_rotation_about_z_deg(5.0), translation_mm=(1.0, 1.0, 1.0)
        )
        points = np.zeros((5, 3))
        t.apply(points)
        t.apply_point((0.0, 0.0, 0.0))
        t.inverse()
        t.as_matrix()


class TestApplyHasNoLoggingAllocationOrValidation:
    """`apply` の本体にログ送出・確保・検証を含まないことを静的に固定する
    (tasks.md タスク 2.2 Critical constraint: 「適用の中でログ送出・確保・検証を
    行わない」。毎フレーム走る唯一の経路であるため)。
    """

    def test_apply_body_has_no_raise_statements(self) -> None:
        source = textwrap.dedent(inspect.getsource(transform.WorldTransform.apply))
        tree = ast.parse(source)
        raises = [node for node in ast.walk(tree) if isinstance(node, ast.Raise)]
        assert raises == [], "apply() must not perform validation (no raise statements)"

    def test_apply_body_has_no_logging_calls(self) -> None:
        source = textwrap.dedent(inspect.getsource(transform.WorldTransform.apply))
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = getattr(func, "attr", None) or getattr(func, "id", "")
                assert "log" not in name.lower(), (
                    f"apply() must not perform logging, found call to {name!r}"
                )

    def test_apply_point_body_has_no_raise_statements(self) -> None:
        source = textwrap.dedent(
            inspect.getsource(transform.WorldTransform.apply_point)
        )
        tree = ast.parse(source)
        raises = [node for node in ast.walk(tree) if isinstance(node, ast.Raise)]
        assert raises == [], (
            "apply_point() must not perform validation (no raise statements)"
        )

    def test_apply_body_is_a_single_expression_returning_the_transformed_array(
        self,
    ) -> None:
        """`apply` が「行列積 + 平行移動の加算」だけの最小実装であることを固定する。"""
        source = textwrap.dedent(inspect.getsource(transform.WorldTransform.apply))
        tree = ast.parse(source)
        func_def = tree.body[0]
        assert isinstance(func_def, ast.FunctionDef)
        # Drop a leading docstring expression statement, if present.
        body = [
            stmt
            for stmt in func_def.body
            if not (
                isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant)
            )
        ]
        assert len(body) == 1
        assert isinstance(body[0], ast.Return)
