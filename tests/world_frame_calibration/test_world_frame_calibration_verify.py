"""world_frame_calibration.verify（独立検証の中核: 検証点の World 座標算出と
独立性の検査）の検証（tasks.md タスク 5.1 / 要件 4.1, 4.2, 4.3, 4.9）。

design.md「L6-L9: 永続化・検証・入口」節の Verifier コンポーネント契約のうち、
本タスクが担う部分（検証点の World 座標算出・既知位置との軸ごとの差分・
確立用マーカーとの重複除外）を固定する。requirements.md Requirement 4
「独立検証ステップ ★」の Objective が述べるとおり、本 Spec の価値は
「変換できること」ではなく「変換が正しいことを独立に確かめられること」に
あり、その中心がこのモジュールである。

本テストが確認すること（tasks.md タスク 5.1「観測可能な完了状態」）:

- 保存済みの `CalibrationResult` と検証点の観測から、各検証点の World 座標を
  算出し、既知の位置との差分を**軸ごとに**（スカラー距離ではなく）求めること
- 検証は物体検出・追跡・予測のいずれも呼ばずに完了できること
  （入力は結果と観測だけであることを静的に確認する）
- 確立用マーカー（`origin_anchor` / `x_axis_anchor`）と重複する検証点は
  集計から除外され、重複として明示される（`independent=False`）こと。
  一方で独立な検証点は誤って除外されないこと
- 独立な検証点が1つも無ければ `CalibrationFailure
  (VERIFICATION_NOT_INDEPENDENT)` として失敗すること（空の結果を
  vacuously 成功として返さない）
- 床上の検証点（z=0）と既知の高さを持つ検証点（z≠0）の双方が、同じ
  計算経路で正しく軸ごとの差分を返すこと

このテストファイルは `world_frame_calibration.verify` がまだ存在しない時点
では `ModuleNotFoundError` で失敗する（RED）。

ファイル名について: `tests/**` に `__init__.py` が無いため、裸の
`test_verify.py` は使わずプレフィックス付きの
`test_world_frame_calibration_verify.py` とする（既存タスクの命名規約）。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from world_frame_calibration.errors import CalibrationConfigError, CalibrationFailure, FailureReason
from world_frame_calibration.frame import build_world_frame
from world_frame_calibration.plan import PlanLimits, VerificationPointSpec
from world_frame_calibration.types import (
    AnchorObservation,
    AnchorRole,
    Intrinsics,
    PixelRegion,
    Plane,
    PlaneQuality,
    StreamSignature,
)

_DEFAULT_REGION = PixelRegion(x0_px=0, y0_px=0, x1_px=10, y1_px=10)


# ---------------------------------------------------------------------------
# ヘルパ: `test_world_frame_calibration_result.py` と同じ構成の、素直に手計算
# できる CalibrationResult を組み立てる。
#
# plane.normal=(0,0,1), origin=(0,0,1000), x_axis=(1000,0,1000) から
# build_world_frame すると、回転は恒等（x=(1,0,0), y=(0,1,0), z=(0,0,1)）、
# 平行移動は (0,0,-1000) になる。すなわち
#   p_world = p_camera - (0, 0, 1000)
# という単純な平行移動のみの変換になり、期待値を手計算できる。
# ---------------------------------------------------------------------------


def _plane() -> Plane:
    quality = PlaneQuality(
        points_considered=5000,
        inlier_count=4500,
        inlier_ratio=0.9,
        residual_abs_p50_mm=1.2,
        residual_abs_p95_mm=3.4,
        residual_rms_mm=1.8,
        frames_used=5,
        incidence_angle_deg=42.0,
        rng_seed=7,
    )
    return Plane(normal=(0.0, 0.0, 1.0), distance_mm=1000.0, quality=quality)


def _anchor(
    label: str, role: AnchorRole, point_camera_mm: tuple[float, float, float]
) -> AnchorObservation:
    return AnchorObservation(
        label=label,
        role=role,
        point_camera_mm=point_camera_mm,
        point_on_plane_mm=point_camera_mm,
        height_above_plane_mm=0.0,
        range_from_camera_mm=float(sum(c * c for c in point_camera_mm)) ** 0.5,
        sample_count=120,
        spread_mm=2.5,
        region=_DEFAULT_REGION,
        frames_used=5,
    )


def _establishment():
    origin_anchor = _anchor("origin", AnchorRole.ORIGIN, (0.0, 0.0, 1000.0))
    x_axis_anchor = _anchor("x_axis", AnchorRole.X_AXIS, (1000.0, 0.0, 1000.0))
    limits = PlanLimits(min_baseline_mm=800.0)
    return build_world_frame(_plane(), origin_anchor, x_axis_anchor, limits)


def _intrinsics() -> Intrinsics:
    return Intrinsics(
        width_px=640,
        height_px=480,
        fx_px=615.0,
        fy_px=615.0,
        ppx_px=320.0,
        ppy_px=240.0,
        model="brown_conrady",
        coeffs=(0.0, 0.0, 0.0, 0.0, 0.0),
    )


def _signature() -> StreamSignature:
    return StreamSignature(
        width_px=640, height_px=480, fps=30, depth_scale_mm=1.0, color_enabled=False
    )


def _calibration_result():
    from world_frame_calibration import result as result_module

    return result_module.assemble_calibration_result(
        establishment=_establishment(),
        source_kind="simulated",
        session_path=None,
        signature=_signature(),
        intrinsics=_intrinsics(),
        plan_digest={"notes": "test fixture"},
        notes="",
    )


def _verification_point(
    label: str,
    truth_world_mm: tuple[float, float, float],
    truth_source: str = "tape measure",
) -> VerificationPointSpec:
    return VerificationPointSpec(
        label=label,
        region=_DEFAULT_REGION,
        height_band_mm=(0.0, 500.0),
        truth_world_mm=truth_world_mm,
        truth_source=truth_source,
    )


def _observation(label: str, point_camera_mm: tuple[float, float, float]) -> AnchorObservation:
    return _anchor(label, AnchorRole.VERIFICATION, point_camera_mm)


# ---------------------------------------------------------------------------
# 検証点の World 座標算出と軸ごとの差分
# ---------------------------------------------------------------------------


class TestPerAxisDifference:
    """既知の World 座標に置かれた検証対象の観測から、算出座標と既知位置との
    差分を軸ごとに報告する（要件 4.2）。
    """

    def test_floor_level_point_reports_per_axis_error_not_scalar(self) -> None:
        from world_frame_calibration import verify as verify_module

        result = _calibration_result()
        # truth = (500, 300, 0). measured camera point = (505, 298, 1000)
        # -> world = (505, 298, 0) -> error = (5, -2, 0).
        spec = _verification_point("vp1", (500.0, 300.0, 0.0))
        observation = _observation("vp1", (505.0, 298.0, 1000.0))

        points = verify_module.evaluate_verification_points(result, [(spec, observation)])

        assert len(points) == 1
        point = points[0]
        assert point.label == "vp1"
        assert point.independent is True
        assert point.measured_world_mm == pytest.approx((505.0, 298.0, 0.0), abs=1e-9)
        assert point.truth_world_mm == (500.0, 300.0, 0.0)
        assert point.truth_source == "tape measure"
        # Per-axis, not a scalar distance: x and y differ, z is exact.
        assert point.error_mm == pytest.approx((5.0, -2.0, 0.0), abs=1e-9)

    def test_zero_error_when_measured_matches_truth_exactly(self) -> None:
        from world_frame_calibration import verify as verify_module

        result = _calibration_result()
        spec = _verification_point("vp1", (200.0, -150.0, 0.0))
        observation = _observation("vp1", (200.0, -150.0, 1000.0))

        points = verify_module.evaluate_verification_points(result, [(spec, observation)])

        assert points[0].error_mm == pytest.approx((0.0, 0.0, 0.0), abs=1e-9)


class TestElevatedVerificationPoints:
    """既知の高さを持つ検証点（床上でない点）も同じ形式で扱える
    （要件 4.9）。床上の点と共通の計算経路を通ることを、同じテストクラス内で
    両方確認する。
    """

    def test_elevated_point_reports_nonzero_vertical_error_uniformly(self) -> None:
        from world_frame_calibration import verify as verify_module

        result = _calibration_result()
        # truth height = 150mm above floor. measured camera point has height
        # 160mm above floor (world z = camera_z - 1000).
        spec = _verification_point("vp_high", (200.0, -100.0, 150.0))
        observation = _observation("vp_high", (200.0, -100.0, 1160.0))

        points = verify_module.evaluate_verification_points(result, [(spec, observation)])

        point = points[0]
        assert point.measured_world_mm == pytest.approx((200.0, -100.0, 160.0), abs=1e-9)
        assert point.error_mm == pytest.approx((0.0, 0.0, 10.0), abs=1e-9)

    def test_floor_and_elevated_points_share_the_same_computation(self) -> None:
        from world_frame_calibration import verify as verify_module

        result = _calibration_result()
        floor_spec = _verification_point("vp_floor", (100.0, 100.0, 0.0))
        floor_obs = _observation("vp_floor", (100.0, 100.0, 1000.0))
        high_spec = _verification_point("vp_high", (100.0, 100.0, 300.0))
        high_obs = _observation("vp_high", (100.0, 100.0, 1300.0))

        points = verify_module.evaluate_verification_points(
            result, [(floor_spec, floor_obs), (high_spec, high_obs)]
        )

        by_label = {p.label: p for p in points}
        assert by_label["vp_floor"].error_mm == pytest.approx((0.0, 0.0, 0.0), abs=1e-9)
        assert by_label["vp_high"].error_mm == pytest.approx((0.0, 0.0, 0.0), abs=1e-9)
        assert by_label["vp_floor"].independent is True
        assert by_label["vp_high"].independent is True


# ---------------------------------------------------------------------------
# 確立用マーカーとの重複除外
# ---------------------------------------------------------------------------


class TestDuplicateExclusion:
    """確立に使ったマーカーと重複する検証点を集計から除外し、重複として
    明示する（要件 4.3）。確立に使った点は定義上変換と一致するため、
    それを検証点に混ぜても検証にならない。
    """

    def test_verification_point_matching_origin_label_is_flagged_not_independent(
        self,
    ) -> None:
        from world_frame_calibration import verify as verify_module

        result = _calibration_result()
        # This point's label matches the origin establishment marker.
        duplicate_spec = _verification_point("origin", (0.0, 0.0, 0.0))
        duplicate_obs = _observation("origin", (0.0, 0.0, 1000.0))
        genuine_spec = _verification_point("vp1", (400.0, 200.0, 0.0))
        genuine_obs = _observation("vp1", (400.0, 200.0, 1000.0))

        points = verify_module.evaluate_verification_points(
            result, [(duplicate_spec, duplicate_obs), (genuine_spec, genuine_obs)]
        )

        by_label = {p.label: p for p in points}
        assert by_label["origin"].independent is False
        assert by_label["vp1"].independent is True

    def test_verification_point_matching_x_axis_label_is_flagged_not_independent(
        self,
    ) -> None:
        from world_frame_calibration import verify as verify_module

        result = _calibration_result()
        duplicate_spec = _verification_point("x_axis", (1000.0, 0.0, 0.0))
        duplicate_obs = _observation("x_axis", (1000.0, 0.0, 1000.0))
        genuine_spec = _verification_point("vp1", (400.0, 200.0, 0.0))
        genuine_obs = _observation("vp1", (400.0, 200.0, 1000.0))

        points = verify_module.evaluate_verification_points(
            result, [(duplicate_spec, duplicate_obs), (genuine_spec, genuine_obs)]
        )

        by_label = {p.label: p for p in points}
        assert by_label["x_axis"].independent is False
        assert by_label["vp1"].independent is True

    def test_genuinely_independent_point_is_not_accidentally_excluded(self) -> None:
        """負の確認: 確立用ラベルと似ていない独立点が、誤って除外されないこと。"""
        from world_frame_calibration import verify as verify_module

        result = _calibration_result()
        spec = _verification_point("far_corner", (900.0, 900.0, 0.0))
        observation = _observation("far_corner", (900.0, 900.0, 1000.0))

        points = verify_module.evaluate_verification_points(result, [(spec, observation)])

        assert points[0].independent is True


# ---------------------------------------------------------------------------
# 独立点ゼロは失敗させる
# ---------------------------------------------------------------------------


class TestZeroIndependentPointsFails:
    """独立な検証点が1つも無ければ、検証として成立しないものとして失敗させる
    （要件 4.3）。空の結果を vacuous な成功として返さない。
    """

    def test_only_duplicate_points_raises_verification_not_independent(self) -> None:
        from world_frame_calibration import verify as verify_module

        result = _calibration_result()
        duplicate_spec = _verification_point("origin", (0.0, 0.0, 0.0))
        duplicate_obs = _observation("origin", (0.0, 0.0, 1000.0))

        with pytest.raises(CalibrationFailure) as excinfo:
            verify_module.evaluate_verification_points(result, [(duplicate_spec, duplicate_obs)])

        assert excinfo.value.reason == FailureReason.VERIFICATION_NOT_INDEPENDENT

    def test_empty_observations_raises_verification_not_independent(self) -> None:
        from world_frame_calibration import verify as verify_module

        result = _calibration_result()

        with pytest.raises(CalibrationFailure) as excinfo:
            verify_module.evaluate_verification_points(result, [])

        assert excinfo.value.reason == FailureReason.VERIFICATION_NOT_INDEPENDENT

    def test_both_establishment_markers_duplicated_raises(self) -> None:
        from world_frame_calibration import verify as verify_module

        result = _calibration_result()
        origin_dup_spec = _verification_point("origin", (0.0, 0.0, 0.0))
        origin_dup_obs = _observation("origin", (0.0, 0.0, 1000.0))
        x_axis_dup_spec = _verification_point("x_axis", (1000.0, 0.0, 0.0))
        x_axis_dup_obs = _observation("x_axis", (1000.0, 0.0, 1000.0))

        with pytest.raises(CalibrationFailure) as excinfo:
            verify_module.evaluate_verification_points(
                result, [(origin_dup_spec, origin_dup_obs), (x_axis_dup_spec, x_axis_dup_obs)]
            )

        assert excinfo.value.reason == FailureReason.VERIFICATION_NOT_INDEPENDENT

    def test_failure_context_reports_excluded_labels(self) -> None:
        from world_frame_calibration import verify as verify_module

        result = _calibration_result()
        duplicate_spec = _verification_point("origin", (0.0, 0.0, 0.0))
        duplicate_obs = _observation("origin", (0.0, 0.0, 1000.0))

        with pytest.raises(CalibrationFailure) as excinfo:
            verify_module.evaluate_verification_points(result, [(duplicate_spec, duplicate_obs)])

        assert "origin" in excinfo.value.context.get("excluded_labels", ())


# ---------------------------------------------------------------------------
# 呼び出し方の誤り: spec と observation のラベル不一致
# ---------------------------------------------------------------------------


class TestMismatchedLabelsRejected:
    """`(spec, observation)` のペアで label が食い違うのは呼び出し側の誤りで
    あり、座標系が定まらない条件（`CalibrationFailure`）ではなく呼び出し方の
    誤り（`CalibrationConfigError`）として拒否する。
    """

    def test_mismatched_pair_labels_raise_calibration_config_error(self) -> None:
        from world_frame_calibration import verify as verify_module

        result = _calibration_result()
        spec = _verification_point("vp1", (400.0, 200.0, 0.0))
        observation = _observation("vp_typo", (400.0, 200.0, 1000.0))

        with pytest.raises(CalibrationConfigError):
            verify_module.evaluate_verification_points(result, [(spec, observation)])


# ---------------------------------------------------------------------------
# 検出・追跡・予測を一切呼ばずに完結する
# ---------------------------------------------------------------------------


class TestNoDetectionTrackingPredictionDependency:
    """検証を、物体検出・追跡・予測のいずれも実行せずに単体で完了できる手段
    として提供する（要件 4.1）。入力は「保存済み結果」と「検証点の観測」だけ
    であることを、関数シグネチャとモジュールの import 集合の双方から静的に
    確認する。
    """

    _FORBIDDEN_SUBSTRINGS = ("detect", "track", "predict")

    def test_evaluate_verification_points_signature_has_only_result_and_observations(
        self,
    ) -> None:
        from world_frame_calibration import verify as verify_module

        signature = inspect.signature(verify_module.evaluate_verification_points)
        param_names = list(signature.parameters.keys())

        assert param_names == ["result", "observations"]

    def test_module_does_not_import_detection_tracking_or_prediction_packages(self) -> None:
        from world_frame_calibration import verify as verify_module

        module_path = Path(inspect.getfile(verify_module))
        tree = ast.parse(module_path.read_text(encoding="utf-8"))

        imported_names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module is not None:
                    imported_names.append(node.module)
                imported_names.extend(alias.name for alias in node.names)

        lowered = [name.lower() for name in imported_names]
        for forbidden in self._FORBIDDEN_SUBSTRINGS:
            offending = [name for name in lowered if forbidden in name]
            assert offending == [], (
                f"verify.py imports something matching forbidden substring "
                f"{forbidden!r}: {offending!r}"
            )

    def test_module_only_imports_from_the_declared_boundary(self) -> None:
        """境界: `types` / `errors` / `transform` / `anchors` / `plan` /
        `result` / `numpy` 以外の自パッケージ内モジュールを import しない。
        """
        from world_frame_calibration import verify as verify_module

        allowed_wfc_submodules = {
            "types",
            "errors",
            "transform",
            "anchors",
            "plan",
            "result",
        }

        module_path = Path(inspect.getfile(verify_module))
        tree = ast.parse(module_path.read_text(encoding="utf-8"))

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                if node.module.startswith("world_frame_calibration."):
                    submodule = node.module.split(".", 1)[1].split(".", 1)[0]
                    assert submodule in allowed_wfc_submodules, (
                        f"unexpected intra-package import: {node.module!r}"
                    )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("world_frame_calibration."):
                        submodule = alias.name.split(".", 1)[1].split(".", 1)[0]
                        assert submodule in allowed_wfc_submodules, (
                            f"unexpected intra-package import: {alias.name!r}"
                        )
