"""world_frame_calibration.linalg の線形代数・ロバスト統計ユーティリティの検証
(tasks.md タスク 1.3 / 要件 2.4, 3.3)。

design.md「L0-L2: 基盤・入力」節の LinAlg コンポーネント契約を固定する。
本テストが確認すること:

- `unit` が非零ベクトルを単位長へ正規化する
- `orthonormal_basis` が Z 軸を保ったまま、X ヒントの Z 直交成分から
  右手系・単位長の正規直交基底 (x, y, z) を構成する
  （design.md: 「z を保ち、x_hint の z 直交成分を x とし、y = z × x を返す」）
- `orthonormal_basis` が Z 直交成分の消失を検出できる材料（真偽と大きさ）を
  提供し、判定自体（例外送出）は行わないこと（design.md Integration 注記:
  判定は上位の `frame` モジュールが行う。本モジュールは `numpy` 以外を
  import しない）
- `is_orthonormal` が既知の正規直交行列を真、非正規直交行列を偽と判定する
- `rotation_angle_deg` が既知の回転対（恒等 vs Z 軸まわり90度など）に対し、
  数値誤差の範囲で正しい全体角を返す
- `robust_center` が各軸の中央値と中央絶対偏差ベースの散らばりを返し、
  外れ値に頑健であること
- 本モジュールが `numpy` 以外の何もimportしないこと（design.md Invariants）
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from world_frame_calibration import linalg


class TestUnit:
    """`unit` がベクトルを単位長へ正規化することを固定する。"""

    def test_normalizes_a_non_unit_vector(self) -> None:
        v = np.array([3.0, 0.0, 0.0])
        result = linalg.unit(v)
        assert np.allclose(result, [1.0, 0.0, 0.0])

    def test_normalizes_a_3d_vector_to_unit_length(self) -> None:
        v = np.array([1.0, 2.0, 2.0])  # norm == 3
        result = linalg.unit(v)
        assert math.isclose(float(np.linalg.norm(result)), 1.0, abs_tol=1e-9)
        assert np.allclose(result, [1.0 / 3.0, 2.0 / 3.0, 2.0 / 3.0])

    def test_already_unit_vector_is_unchanged(self) -> None:
        v = np.array([0.0, 1.0, 0.0])
        result = linalg.unit(v)
        assert np.allclose(result, v)


class TestOrthonormalBasis:
    """`orthonormal_basis` が右手系・単位長の基底を構成することを固定する
    （design.md: z を保ち、x_hint の z 直交成分を x とし、y = z × x）。
    """

    def test_returns_three_unit_length_vectors(self) -> None:
        z_axis = np.array([0.0, 0.0, 1.0])
        x_hint = np.array([1.0, 0.3, 0.0])
        x_axis, y_axis, z_out = linalg.orthonormal_basis(z_axis, x_hint)
        for axis in (x_axis, y_axis, z_out):
            assert math.isclose(float(np.linalg.norm(axis)), 1.0, abs_tol=1e-9)

    def test_z_axis_is_preserved_direction(self) -> None:
        z_axis = np.array([0.0, 0.0, 2.0])  # non-unit input
        x_hint = np.array([1.0, 0.0, 0.5])
        _, _, z_out = linalg.orthonormal_basis(z_axis, x_hint)
        assert np.allclose(z_out, [0.0, 0.0, 1.0])

    def test_x_axis_is_the_z_orthogonal_component_of_x_hint(self) -> None:
        z_axis = np.array([0.0, 0.0, 1.0])
        x_hint = np.array([1.0, 0.0, 5.0])  # has a large z component
        x_axis, _, z_out = linalg.orthonormal_basis(z_axis, x_hint)
        # x must be orthogonal to z
        assert math.isclose(float(np.dot(x_axis, z_out)), 0.0, abs_tol=1e-9)
        # and point in the direction of x_hint's z-orthogonal part: (1, 0, 0)
        assert np.allclose(x_axis, [1.0, 0.0, 0.0])

    def test_y_axis_is_cross_of_z_and_x_right_handed(self) -> None:
        z_axis = np.array([0.0, 0.0, 1.0])
        x_hint = np.array([1.0, 0.0, 0.0])
        x_axis, y_axis, z_out = linalg.orthonormal_basis(z_axis, x_hint)
        expected_y = np.cross(z_out, x_axis)
        assert np.allclose(y_axis, expected_y)
        # right-handed: x cross y == z
        assert np.allclose(np.cross(x_axis, y_axis), z_out)

    def test_basis_is_orthonormal_for_a_tilted_z_axis(self) -> None:
        z_axis = np.array([0.0, 0.3, 1.0])
        x_hint = np.array([1.0, 0.2, -0.1])
        x_axis, y_axis, z_out = linalg.orthonormal_basis(z_axis, x_hint)
        r = np.array([x_axis, y_axis, z_out])
        assert linalg.is_orthonormal(r, tol=1e-9)

    def test_degenerate_signal_reports_true_when_x_hint_z_component_vanishes(
        self,
    ) -> None:
        """x_hint が z 軸そのものに一致する（z 直交成分がほぼ消える）場合、
        `orthonormal_basis` は真偽と大きさの信号を提供する。判定（例外送出）
        自体は行わない（design.md Integration 注記）。
        """
        z_axis = np.array([0.0, 0.0, 1.0])
        x_hint = np.array([0.0, 0.0, 5.0])  # parallel to z: no orthogonal part
        # must not raise -- this module only reports the signal
        _, _, _ = linalg.orthonormal_basis(z_axis, x_hint)
        is_degenerate, magnitude = linalg.x_hint_degeneracy(z_axis, x_hint)
        assert is_degenerate is True
        assert magnitude == pytest.approx(0.0, abs=1e-9)

    def test_degenerate_signal_reports_false_for_well_separated_hint(self) -> None:
        z_axis = np.array([0.0, 0.0, 1.0])
        x_hint = np.array([1.0, 0.0, 0.0])
        is_degenerate, magnitude = linalg.x_hint_degeneracy(z_axis, x_hint)
        assert is_degenerate is False
        assert magnitude == pytest.approx(1.0, abs=1e-9)


class TestIsOrthonormal:
    """`is_orthonormal` が正規直交行列とそうでない行列を区別することを固定する。"""

    def test_identity_is_orthonormal(self) -> None:
        assert linalg.is_orthonormal(np.eye(3), tol=1e-9) is True

    def test_known_rotation_matrix_is_orthonormal(self) -> None:
        theta = math.radians(90.0)
        r = np.array(
            [
                [math.cos(theta), -math.sin(theta), 0.0],
                [math.sin(theta), math.cos(theta), 0.0],
                [0.0, 0.0, 1.0],
            ]
        )
        assert linalg.is_orthonormal(r, tol=1e-9) is True

    def test_scaled_matrix_is_not_orthonormal(self) -> None:
        r = np.eye(3) * 1.5
        assert linalg.is_orthonormal(r, tol=1e-6) is False

    def test_sheared_matrix_is_not_orthonormal(self) -> None:
        r = np.array(
            [
                [1.0, 0.5, 0.0],
                [0.0, 1.0, 0.0],
                [0.0, 0.0, 1.0],
            ]
        )
        assert linalg.is_orthonormal(r, tol=1e-6) is False

    def test_tolerance_is_caller_supplied(self) -> None:
        """許容差は呼び出し側が与える（design.md Postconditions）。"""
        # A matrix that's *slightly* off from orthonormal.
        theta = math.radians(90.0)
        r = np.array(
            [
                [math.cos(theta), -math.sin(theta), 0.0],
                [math.sin(theta), math.cos(theta), 0.0],
                [0.0, 0.0, 1.0 + 1e-3],
            ]
        )
        assert linalg.is_orthonormal(r, tol=1e-9) is False
        assert linalg.is_orthonormal(r, tol=1e-2) is True


class TestRotationAngleDeg:
    """`rotation_angle_deg` が2回転間の全体角を正しく返すことを固定する
    （design.md: R_a^T @ R_b の trace から arccos で導出する標準的な方法）。
    """

    def test_identical_rotations_have_zero_angle(self) -> None:
        r = np.eye(3)
        assert linalg.rotation_angle_deg(r, r) == pytest.approx(0.0, abs=1e-6)

    def test_identity_vs_90_degrees_about_z(self) -> None:
        theta = math.radians(90.0)
        r_z90 = np.array(
            [
                [math.cos(theta), -math.sin(theta), 0.0],
                [math.sin(theta), math.cos(theta), 0.0],
                [0.0, 0.0, 1.0],
            ]
        )
        angle = linalg.rotation_angle_deg(np.eye(3), r_z90)
        assert angle == pytest.approx(90.0, abs=1e-6)

    def test_identity_vs_45_degrees_about_x(self) -> None:
        theta = math.radians(45.0)
        r_x45 = np.array(
            [
                [1.0, 0.0, 0.0],
                [0.0, math.cos(theta), -math.sin(theta)],
                [0.0, math.sin(theta), math.cos(theta)],
            ]
        )
        angle = linalg.rotation_angle_deg(np.eye(3), r_x45)
        assert angle == pytest.approx(45.0, abs=1e-6)

    def test_identity_vs_180_degrees_about_y(self) -> None:
        theta = math.radians(180.0)
        r_y180 = np.array(
            [
                [math.cos(theta), 0.0, math.sin(theta)],
                [0.0, 1.0, 0.0],
                [-math.sin(theta), 0.0, math.cos(theta)],
            ]
        )
        angle = linalg.rotation_angle_deg(np.eye(3), r_y180)
        assert angle == pytest.approx(180.0, abs=1e-6)

    def test_angle_is_symmetric_under_argument_order(self) -> None:
        theta = math.radians(30.0)
        r = np.array(
            [
                [math.cos(theta), -math.sin(theta), 0.0],
                [math.sin(theta), math.cos(theta), 0.0],
                [0.0, 0.0, 1.0],
            ]
        )
        forward = linalg.rotation_angle_deg(np.eye(3), r)
        backward = linalg.rotation_angle_deg(r, np.eye(3))
        assert forward == pytest.approx(backward, abs=1e-9)

    def test_does_not_raise_on_floating_point_overshoot_at_zero_angle(self) -> None:
        """arccos の引数が浮動小数の誤差でわずかに [-1, 1] を超えても NaN にならない
        （クリッピングによる数値的安全性）。
        """
        # Two independently-constructed "identical" rotations may differ by
        # floating point noise, pushing the trace-derived arccos argument
        # just past 1.0.
        theta = math.radians(37.0)
        r_a = np.array(
            [
                [math.cos(theta), -math.sin(theta), 0.0],
                [math.sin(theta), math.cos(theta), 0.0],
                [0.0, 0.0, 1.0],
            ]
        )
        r_b = r_a.copy()
        angle = linalg.rotation_angle_deg(r_a, r_b)
        assert not math.isnan(angle)
        assert angle == pytest.approx(0.0, abs=1e-6)


class TestRobustCenter:
    """`robust_center` が各軸の中央値と MAD ベースの散らばりを返すことを固定する。"""

    def test_returns_median_for_symmetric_points(self) -> None:
        points = np.array(
            [
                [0.0, 0.0, 0.0],
                [1.0, 2.0, 3.0],
                [2.0, 4.0, 6.0],
            ]
        )
        center, _spread = linalg.robust_center(points)
        assert np.allclose(center, [1.0, 2.0, 3.0])

    def test_is_robust_to_a_single_outlier(self) -> None:
        points = np.array(
            [
                [0.0, 0.0, 0.0],
                [1.0, 1.0, 1.0],
                [2.0, 2.0, 2.0],
                [3.0, 3.0, 3.0],
                [1000.0, 1000.0, 1000.0],  # gross outlier
            ]
        )
        center, _spread = linalg.robust_center(points)
        assert np.allclose(center, [2.0, 2.0, 2.0])

    def test_spread_is_zero_for_identical_points(self) -> None:
        points = np.tile(np.array([5.0, 5.0, 5.0]), (4, 1))
        _center, spread = linalg.robust_center(points)
        assert spread == pytest.approx(0.0, abs=1e-9)

    def test_spread_increases_with_dispersion(self) -> None:
        tight = np.array(
            [
                [0.0, 0.0, 0.0],
                [0.1, 0.1, 0.1],
                [-0.1, -0.1, -0.1],
            ]
        )
        loose = np.array(
            [
                [0.0, 0.0, 0.0],
                [5.0, 5.0, 5.0],
                [-5.0, -5.0, -5.0],
            ]
        )
        _, tight_spread = linalg.robust_center(tight)
        _, loose_spread = linalg.robust_center(loose)
        assert loose_spread > tight_spread


class TestModuleImportsOnlyNumpy:
    """本モジュールが `numpy` 以外を import しないことを固定する
    （design.md Invariants / tasks.md タスク 1.3）。
    """

    def test_module_source_has_no_intra_package_or_stdlib_imports_besides_numpy(
        self,
    ) -> None:
        import ast
        import inspect

        source = inspect.getsource(linalg)
        tree = ast.parse(source)
        top_level_imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top_level_imports.append(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module is not None:
                    top_level_imports.append(node.module.split(".")[0])

        allowed = {"numpy", "__future__"}
        disallowed = set(top_level_imports) - allowed
        assert disallowed == set(), (
            f"linalg.py may only import numpy (and __future__ for annotations), "
            f"found: {disallowed}"
        )

    def test_module_does_not_import_errors_module(self) -> None:
        import world_frame_calibration.linalg as linalg_module

        assert not hasattr(linalg_module, "CalibrationFailure")
        assert not hasattr(linalg_module, "FailureReason")
