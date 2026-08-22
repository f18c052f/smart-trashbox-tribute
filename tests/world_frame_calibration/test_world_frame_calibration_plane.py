"""world_frame_calibration.plane（床平面推定）の検証（tasks.md タスク 2.3 /
要件 1.1, 1.2, 1.3, 1.5, 5.1, 5.4, 9.3, 9.4）。

design.md「L3-L5: 推定と確立」節の FloorPlaneEstimator コンポーネント契約を
固定する。本テストが確認すること（tasks.md タスク 2.3「観測可能な完了状態」
および Critical constraints）:

- RANSAC の反復回数が成功確率・外れ値率から**式で導出され**、固定値でない
  こと
- 既知平面から生成した点群で、法線と距離が数値誤差の範囲で復元されること
- 法線がカメラ側（原点側）を正とすること
- 外れ値 40% を混ぜても RANSAC が正しく内点を選ぶこと
- 最終フィットが RANSAC の単一サンプルではなく**内点だけの SVD 再フィット**
  であること（`numpy.linalg.svd` の呼び出しを監視して直接確認する）
- 同一シードで結果が完全に再現されること（決定性）
- 3つの縮退条件（有効点不足・内点下限割れ・入射角過小）がそれぞれ別の
  `FailureReason` として、実測値と下限を `context` に含めて報告されること
- `signed_distance_mm` / `project_onto_plane` が公開・再利用可能な関数で
  あること

このテストファイルは `world_frame_calibration.plane` がまだ存在しない時点
では `ModuleNotFoundError` で失敗する（RED）。

ファイル名について: `tests/**` に `__init__.py` が無いため、裸の
`test_plane.py` は使わずプレフィックス付きの
`test_world_frame_calibration_plane.py` とする（既存タスクの命名規約。
tasks.md 実装ノート・タスク1.6参照）。

`tests/world_frame_calibration/synthetic.py` は `tests/sensing_foundation/
synthetic.py` と裸のモジュール名が衝突するため、`importlib.util.
spec_from_file_location` によるパス指定ロードを使う（`synthetic.py` の
モジュール docstring / `test_world_frame_calibration_synthetic.py` の
参照実装のとおり）。
"""

from __future__ import annotations

import dataclasses
import importlib.util
import math
import sys
from pathlib import Path

import numpy as np
import pytest

from world_frame_calibration import plane as plane_module
from world_frame_calibration.errors import CalibrationFailure, FailureReason
from world_frame_calibration.plan import PlanLimits
from world_frame_calibration.types import (
    DepthImage,
    Intrinsics,
    PixelRegion,
    Plane,
    PlaneQuality,
    StreamSignature,
)

# --- tests/world_frame_calibration/synthetic.py の衝突耐性ロード ---------
_SYNTHETIC_PATH = Path(__file__).resolve().parent / "synthetic.py"
_spec = importlib.util.spec_from_file_location(
    "world_frame_calibration._test_synthetic_for_plane", _SYNTHETIC_PATH
)
assert _spec is not None and _spec.loader is not None
_synthetic = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _synthetic
_spec.loader.exec_module(_synthetic)

camera_pose_looking_at_floor = _synthetic.camera_pose_looking_at_floor
generate_synthetic_depth = _synthetic.generate_synthetic_depth


# ---------------------------------------------------------------------------
# ヘルパ
# ---------------------------------------------------------------------------


def _intrinsics(
    *,
    width_px: int = 640,
    height_px: int = 480,
    fx_px: float = 500.0,
    fy_px: float = 500.0,
    ppx_px: float | None = None,
    ppy_px: float | None = None,
) -> Intrinsics:
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


def _signature(intr: Intrinsics) -> StreamSignature:
    return StreamSignature(
        width_px=intr.width_px,
        height_px=intr.height_px,
        fps=30,
        depth_scale_mm=1.0,
        color_enabled=False,
    )


def _depth_image(depth_mm: np.ndarray, intr: Intrinsics) -> DepthImage:
    valid_count = np.where(np.isnan(depth_mm), 0, 1).astype(np.int32)
    return DepthImage(
        depth_mm=depth_mm,
        valid_count=valid_count,
        frames_used=1,
        intrinsics=intr,
        signature=_signature(intr),
        source_kind="simulated",
    )


def _full_region(intr: Intrinsics) -> PixelRegion:
    return PixelRegion(x0_px=0, y0_px=0, x1_px=intr.width_px, y1_px=intr.height_px)


def _clean_floor_frame(
    *,
    width_px: int = 60,
    height_px: int = 45,
    fx_px: float = 120.0,
    fy_px: float = 120.0,
    position_world_mm: tuple[float, float, float] = (0.0, 0.0, 1500.0),
    yaw_deg: float = 0.0,
    pitch_deg: float = 65.0,
    noise_std_mm: float = 0.0,
    rng_seed: int = 0,
):
    """外れ値・ノイズなしの、既知姿勢からの床のみの合成 Depth を返す。"""
    intr = _intrinsics(width_px=width_px, height_px=height_px, fx_px=fx_px, fy_px=fy_px)
    pose = camera_pose_looking_at_floor(
        position_world_mm=position_world_mm, yaw_deg=yaw_deg, pitch_deg=pitch_deg
    )
    frame = generate_synthetic_depth(
        pose, intr, noise_std_mm=noise_std_mm, rng_seed=rng_seed
    )
    return frame, intr


_DEFAULT_TEST_LIMITS = PlanLimits(
    min_inlier_points=200,
    min_inlier_ratio=0.5,
    plane_inlier_threshold_mm=15.0,
    min_incidence_angle_deg=10.0,
    rng_seed=1234,
)


# ---------------------------------------------------------------------------
# RANSAC 反復回数の導出（固定値を埋め込まない）
# ---------------------------------------------------------------------------


def test_ransac_iteration_count_matches_the_documented_formula() -> None:
    """N = ceil(log(1-p) / log(1-(1-e)^3)) と厳密に一致する（tasks.md タスク2.3
    Critical constraint: 固定値を埋め込まない）。
    """
    p, e = 0.999, 0.5
    expected = math.ceil(math.log(1 - p) / math.log(1 - (1 - e) ** 3))

    result = plane_module._ransac_iteration_count(p, e)

    assert result == expected
    assert result > 1  # 固定回数の当てずっぽうではなく、実際に式から複数回になる


def test_ransac_iteration_count_varies_with_outlier_ratio() -> None:
    """外れ値率を変えると反復回数が変わる（固定値でないことの直接証拠）。"""
    low = plane_module._ransac_iteration_count(0.99, 0.1)
    mid = plane_module._ransac_iteration_count(0.99, 0.5)
    high = plane_module._ransac_iteration_count(0.99, 0.8)

    assert low < mid < high


def test_ransac_iteration_count_varies_with_success_probability() -> None:
    """成功確率を変えると反復回数が変わる。"""
    low = plane_module._ransac_iteration_count(0.9, 0.5)
    high = plane_module._ransac_iteration_count(0.9999, 0.5)

    assert low < high


def test_ransac_iteration_count_is_independent_of_rng_seed() -> None:
    """反復回数は p, e だけで決まり、乱数シード自体には依存しない
    （回数の導出とサンプリングの乱数は別の関心事であることの確認）。
    """
    limits_a = PlanLimits(ransac_success_probability=0.995, ransac_outlier_ratio=0.4, rng_seed=1)
    limits_b = PlanLimits(ransac_success_probability=0.995, ransac_outlier_ratio=0.4, rng_seed=999)

    count_a = plane_module._ransac_iteration_count(
        limits_a.ransac_success_probability, limits_a.ransac_outlier_ratio
    )
    count_b = plane_module._ransac_iteration_count(
        limits_b.ransac_success_probability, limits_b.ransac_outlier_ratio
    )
    assert count_a == count_b


# ---------------------------------------------------------------------------
# 既知平面からの法線・距離の復元
# ---------------------------------------------------------------------------


def test_estimate_floor_plane_recovers_known_normal_and_distance() -> None:
    """既知のカメラ姿勢から生成した合成 Depth に対し、復元した法線と距離が
    正解のカメラ→World 変換から解析的に計算した値と数値誤差の範囲で一致する
    （tasks.md タスク 2.3「観測可能な完了状態」）。
    """
    frame, intr = _clean_floor_frame(pitch_deg=65.0, position_world_mm=(0.0, 0.0, 1500.0))
    region = _full_region(intr)

    result = plane_module.estimate_floor_plane(frame.depth, region, _DEFAULT_TEST_LIMITS)

    rotation = frame.camera_to_world.rotation_matrix()  # columns = camera axes in world
    position = frame.camera_to_world.position_vector()
    expected_normal_cam = rotation.T @ np.array([0.0, 0.0, 1.0])  # world "up" -> camera frame
    expected_distance_mm = -float(position[2])

    np.testing.assert_allclose(result.normal, expected_normal_cam, atol=1e-3)
    assert math.isclose(result.distance_mm, expected_distance_mm, abs_tol=1.0)

    # incidence angle == pitch_deg by synthetic.py's construction
    assert math.isclose(result.quality.incidence_angle_deg, 65.0, abs_tol=0.5)
    assert result.quality.frames_used == frame.depth.frames_used
    assert result.quality.rng_seed == _DEFAULT_TEST_LIMITS.rng_seed
    assert result.quality.inlier_ratio > 0.95  # noise-free floor: nearly all points fit


def test_estimate_floor_plane_normal_points_toward_the_camera_side() -> None:
    """法線はカメラ側（原点側）が正である（要件 2.1）。カメラは常にカメラ
    座標系の原点にあるため、これは「原点の符号付き距離が正である」ことと
    同値であり、姿勢に依存しない直接的な確認になる。
    """
    frame, intr = _clean_floor_frame(pitch_deg=50.0)
    region = _full_region(intr)

    result = plane_module.estimate_floor_plane(frame.depth, region, _DEFAULT_TEST_LIMITS)

    origin_signed_distance = plane_module.signed_distance_mm(
        result, np.array([[0.0, 0.0, 0.0]])
    )
    assert origin_signed_distance[0] > 0.0


# ---------------------------------------------------------------------------
# 外れ値 40% を混ぜても正しく内点が選ばれる
# ---------------------------------------------------------------------------


def _inject_outliers(
    depth_image: DepthImage, *, fraction: float, offset_mm: float, seed: int
) -> DepthImage:
    """有効画素の `fraction` を選び、深度へ `offset_mm` を加えて外れ値化する。"""
    depth_mm = np.array(depth_image.depth_mm, copy=True)
    valid_positions = np.argwhere(~np.isnan(depth_mm))
    rng = np.random.default_rng(seed)
    n_outliers = int(round(len(valid_positions) * fraction))
    chosen_idx = rng.choice(len(valid_positions), size=n_outliers, replace=False)
    for idx in chosen_idx:
        v, u = valid_positions[idx]
        depth_mm[v, u] = depth_mm[v, u] + offset_mm
    return dataclasses.replace(depth_image, depth_mm=depth_mm)


def test_estimate_floor_plane_selects_correct_inliers_with_40_percent_outliers() -> None:
    """外れ値 40% を混ぜても RANSAC が正しい内点集合を選び、平面が正しく
    復元される（tasks.md タスク 2.3「観測可能な完了状態」）。
    """
    frame, intr = _clean_floor_frame(pitch_deg=65.0, position_world_mm=(0.0, 0.0, 1500.0))
    contaminated = _inject_outliers(
        frame.depth, fraction=0.4, offset_mm=5000.0, seed=777
    )
    region = _full_region(intr)

    result = plane_module.estimate_floor_plane(contaminated, region, _DEFAULT_TEST_LIMITS)

    # ~60% の点が真の内点であるはず
    assert 0.5 <= result.quality.inlier_ratio <= 0.68

    rotation = frame.camera_to_world.rotation_matrix()
    position = frame.camera_to_world.position_vector()
    expected_normal_cam = rotation.T @ np.array([0.0, 0.0, 1.0])
    expected_distance_mm = -float(position[2])

    np.testing.assert_allclose(result.normal, expected_normal_cam, atol=1e-2)
    assert math.isclose(result.distance_mm, expected_distance_mm, abs_tol=5.0)


def test_estimate_floor_plane_refits_via_svd_on_inliers_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """最終フィットは RANSAC の3点サンプルではなく、内点だけを使った SVD
    再フィットである（tasks.md タスク 2.3 Critical constraint: 「速度差では
    なく、SVD 再フィットのステップが実際に呼ばれ、結果を左右すること」を
    直接確認する）。`numpy.linalg.svd` の呼び出しを監視し、渡された点群が
    最終的な内点数と一致すること（＝3点だけでも、全点でもない）を確認する。
    """
    frame, intr = _clean_floor_frame(pitch_deg=65.0, position_world_mm=(0.0, 0.0, 1500.0))
    contaminated = _inject_outliers(
        frame.depth, fraction=0.4, offset_mm=5000.0, seed=777
    )
    region = _full_region(intr)

    svd_call_args: list[np.ndarray] = []
    real_svd = np.linalg.svd

    def _spy_svd(a, *args, **kwargs):
        svd_call_args.append(np.array(a, copy=True))
        return real_svd(a, *args, **kwargs)

    monkeypatch.setattr(np.linalg, "svd", _spy_svd)

    result = plane_module.estimate_floor_plane(contaminated, region, _DEFAULT_TEST_LIMITS)

    assert len(svd_call_args) == 1
    svd_input = svd_call_args[0]
    assert svd_input.shape == (result.quality.inlier_count, 3)
    # 3点サンプルだけのフィットではない
    assert svd_input.shape[0] > plane_module._SAMPLE_SIZE
    # 全点でのフィットでもない（40% は外れ値として除かれているはず）
    assert svd_input.shape[0] < result.quality.points_considered


# ---------------------------------------------------------------------------
# 決定性
# ---------------------------------------------------------------------------


def test_estimate_floor_plane_is_deterministic_given_the_same_rng_seed() -> None:
    """同一の DepthImage と同一の rng_seed に対して、常に同一の平面を返す
    （要件 9.4 / design.md FloorPlaneEstimator「Invariants」）。
    """
    frame, intr = _clean_floor_frame(pitch_deg=55.0)
    region = _full_region(intr)
    limits = PlanLimits(min_inlier_points=100, rng_seed=42)

    result_a = plane_module.estimate_floor_plane(frame.depth, region, limits)
    result_b = plane_module.estimate_floor_plane(frame.depth, region, limits)

    assert result_a == result_b


# ---------------------------------------------------------------------------
# 縮退条件: 3つの別々の失敗理由
# ---------------------------------------------------------------------------


def test_estimate_floor_plane_raises_insufficient_depth_points_before_ransac_runs() -> None:
    """有効点が2点しかない場合、RANSAC のサンプリング（3点必要）を試みる前に
    INSUFFICIENT_DEPTH_POINTS として失敗する。このチェックが RANSAC の後に
    位置していたら、`rng.choice(2, size=3, replace=False)` が
    `CalibrationFailure` ではない `ValueError` を送出してテストが失敗する
    はずであり、これがチェック順序を固定するテストになる。
    """
    intr = _intrinsics(width_px=4, height_px=4)
    depth = np.full((4, 4), np.nan, dtype=np.float64)
    depth[0, 0] = 1000.0
    depth[1, 1] = 1000.0
    image = _depth_image(depth, intr)
    region = _full_region(intr)
    limits = PlanLimits(min_inlier_points=10)

    with pytest.raises(CalibrationFailure) as exc_info:
        plane_module.estimate_floor_plane(image, region, limits)

    assert exc_info.value.reason == FailureReason.INSUFFICIENT_DEPTH_POINTS
    assert exc_info.value.context["points_considered"] == 2
    assert exc_info.value.context["min_inlier_points"] == 10


def test_estimate_floor_plane_raises_plane_not_supported_with_measured_and_threshold() -> None:
    """RANSAC 後の内点率が下限を下回れば PLANE_NOT_SUPPORTED として失敗し、
    実測値と下限が context に入る（要件 1.5, 5.1）。極端なノイズ（標準偏差
    200mm）と極めて厳しい内点しきい値（0.5mm）を組み合わせ、どの候補平面
    についても大多数の点が内点にならない状況を作る。
    """
    frame, intr = _clean_floor_frame(
        pitch_deg=65.0, noise_std_mm=200.0, rng_seed=555
    )
    region = _full_region(intr)
    limits = PlanLimits(
        min_inlier_points=50,
        min_inlier_ratio=0.5,
        plane_inlier_threshold_mm=0.5,
        min_incidence_angle_deg=5.0,
        rng_seed=9,
    )

    with pytest.raises(CalibrationFailure) as exc_info:
        plane_module.estimate_floor_plane(frame.depth, region, limits)

    assert exc_info.value.reason == FailureReason.PLANE_NOT_SUPPORTED
    assert exc_info.value.context["min_inlier_points"] == 50
    assert exc_info.value.context["min_inlier_ratio"] == 0.5
    assert exc_info.value.context["inlier_ratio"] < 0.5
    assert exc_info.value.context["inlier_count"] < limits.min_inlier_points or (
        exc_info.value.context["inlier_ratio"] < limits.min_inlier_ratio
    )


def test_estimate_floor_plane_raises_incidence_angle_too_shallow_with_measured_and_threshold() -> None:
    """入射角が下限を下回れば INCIDENCE_ANGLE_TOO_SHALLOW として失敗し、
    実測値と下限が context に入る（要件 5.4）。`pitch_deg` は
    `incidence_angle_deg` と一致するよう構成されているため、下限未満の
    pitch を与えるだけで直接この条件を再現できる。
    """
    frame, intr = _clean_floor_frame(
        width_px=80,
        height_px=60,
        fx_px=250.0,
        fy_px=250.0,
        position_world_mm=(0.0, 0.0, 800.0),
        pitch_deg=6.0,
    )
    region = _full_region(intr)
    limits = PlanLimits(
        min_inlier_points=30,
        min_inlier_ratio=0.3,
        plane_inlier_threshold_mm=15.0,
        min_incidence_angle_deg=10.0,
        rng_seed=3,
    )

    with pytest.raises(CalibrationFailure) as exc_info:
        plane_module.estimate_floor_plane(frame.depth, region, limits)

    assert exc_info.value.reason == FailureReason.INCIDENCE_ANGLE_TOO_SHALLOW
    assert exc_info.value.context["min_incidence_angle_deg"] == 10.0
    assert exc_info.value.context["incidence_angle_deg"] < 10.0
    assert math.isclose(exc_info.value.context["incidence_angle_deg"], 6.0, abs_tol=1.0)


# ---------------------------------------------------------------------------
# signed_distance_mm / project_onto_plane: 公開・再利用可能な平面演算
# ---------------------------------------------------------------------------


def _make_plane(normal: tuple[float, float, float], distance_mm: float) -> Plane:
    quality = PlaneQuality(
        points_considered=100,
        inlier_count=90,
        inlier_ratio=0.9,
        residual_abs_p50_mm=1.0,
        residual_abs_p95_mm=2.0,
        residual_rms_mm=1.5,
        frames_used=1,
        incidence_angle_deg=45.0,
        rng_seed=0,
    )
    return Plane(normal=normal, distance_mm=distance_mm, quality=quality)


def test_signed_distance_mm_and_project_onto_plane_are_consistent() -> None:
    """符号付き距離と平面への投影が整合的である（design.md FloorPlaneEstimator
    「Integration」: マーカー観測・独立検証が同じ平面定義を使うための公開
    関数の正しさそのものを固定する）。
    """
    test_plane = _make_plane(normal=(0.0, 0.0, 1.0), distance_mm=500.0)
    points = np.array(
        [[0.0, 0.0, 500.0], [10.0, 20.0, 600.0], [5.0, 5.0, 400.0]], dtype=np.float64
    )

    distances = plane_module.signed_distance_mm(test_plane, points)
    np.testing.assert_allclose(distances, [0.0, 100.0, -100.0])

    projected = plane_module.project_onto_plane(test_plane, points)
    np.testing.assert_allclose(projected[:, 2], [500.0, 500.0, 500.0])
    np.testing.assert_allclose(projected[:, :2], points[:, :2])

    # 投影済みの点はすでに平面上にあり、再投影しても符号付き距離は 0
    reprojected_distances = plane_module.signed_distance_mm(test_plane, projected)
    np.testing.assert_allclose(reprojected_distances, [0.0, 0.0, 0.0], atol=1e-9)


def test_signed_distance_mm_and_project_onto_plane_are_publicly_exported() -> None:
    """`signed_distance_mm` / `project_onto_plane` はプライベートなヘルパに
    埋もれておらず、公開契約（`__all__`）に含まれる（design.md
    FloorPlaneEstimator の契約シグネチャ）。
    """
    assert "signed_distance_mm" in plane_module.__all__
    assert "project_onto_plane" in plane_module.__all__
    assert "estimate_floor_plane" in plane_module.__all__
