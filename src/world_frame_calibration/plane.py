"""床平面推定（design.md「L3-L5: 推定と確立」FloorPlaneEstimator コンポーネント /
tasks.md タスク 2.3 / 要件 1.1, 1.2, 1.3, 1.5, 5.1, 5.4, 9.3, 9.4）。

床探索範囲の点群から床平面を頑健に推定し、推定品質を返す。IMU を持たない
D435 にとって、床平面が唯一のカメラ姿勢の情報源である（research.md「床平面
推定の手法選定」）。

**RANSAC → SVD の二段構成**（design.md FloorPlaneEstimator「Responsibilities」/
tasks.md タスク 2.3）: RANSAC で内点集合を選ぶだけで終わらせず、**内点だけを
使って総最小二乗（SVD）で平面を解き直す**。RANSAC の最良サンプル（3点だけ
から作った平面）をそのまま採用しない。これは家具の脚・ケーブル・ゴミ箱本体
などの外れ値がある現実の床面で、素の最小二乗が引きずられる問題と、RANSAC
単体の3点サンプルが持つノイズ耐性の低さの両方を避けるための標準的構成である
（research.md）。

**RANSAC 反復回数は式から導出する**（`tech.md` 開発標準1: 根拠のない固定値を
埋め込まない）。成功確率 `p`（`limits.ransac_success_probability`）と外れ値率
`e`（`limits.ransac_outlier_ratio`）から
`N = ceil(log(1 - p) / log(1 - (1 - e)^3))` で導く（`_ransac_iteration_count`）。
固定回数をハードコードしない。

**法線の向きはカメラ側（原点側）が正**（要件 2.1）。本モジュールの点群は
すべてカメラ座標系であり、カメラ自身は常に原点にある。したがって「カメラ側
が正」は「原点がこの平面の法線が向く側にある」という、姿勢に依存しない
判定に単純化できる。SVD が返す法線の符号は不定（±どちらもあり得る）ため、
`dot(normal, centroid) < 0` となるよう符号を選ぶ（この符号のとき
`signed_distance_mm(plane, [[0,0,0]]) > 0` になり、原点が法線の正の側、
すなわちカメラ側にあることを意味する）。

**`signed_distance_mm` / `project_onto_plane` を公開する**（design.md
FloorPlaneEstimator「Integration」）。マーカー観測（`AnchorObserver`,
タスク 2.4）と独立検証（`Verifier`, タスク 5.x）が**同じ平面定義**を使う
ことを保証するためであり、各所で符号規約を再実装させない。

**入射角**（`incidence_angle_deg`）は光軸（カメラ座標系の Z 軸 = Depth 方向）
と床面のなす角であり、法線との角度の余角として求める
（`90 - angle_between(optical_axis, normal)`）。床を正面から見下ろすほど
大きく、水平に近いほど 0 に近づき「浅い」（`tests/world_frame_calibration/
synthetic.py` の `camera_pose_looking_at_floor` docstring: `pitch_deg` が
そのままこの量になるよう構成されている）。

**3つの失敗理由**（design.md FloorPlaneEstimator「Validation」/ tasks.md
タスク 2.3、`errors.py` の `FailureReason` 群）:

1. `INSUFFICIENT_DEPTH_POINTS`: RANSAC を始める前に、探索範囲の有効点数が
   `limits.min_inlier_points` に満たない。この判定は RANSAC の前に行う
   （3点未満ではサンプリング自体が破綻するため、順序が重要）。
2. `PLANE_NOT_SUPPORTED`: RANSAC 後の内点数または内点率が
   `limits.min_inlier_points` / `limits.min_inlier_ratio` を下回る。
3. `INCIDENCE_ANGLE_TOO_SHALLOW`: SVD 再フィット後の入射角が
   `limits.min_incidence_angle_deg` を下回る。

いずれも `context` に実測値と判定に用いた下限を入れる（`errors.py` の
`CalibrationFailure` 規約）。

**乱数**: `numpy.random.Generator`（`np.random.default_rng(limits.rng_seed)`）
のみを用い、グローバルな `numpy.random` の状態には一切触れない。同一の
`DepthImage` と同一の `limits.rng_seed` に対して常に同一の平面を返す
（design.md FloorPlaneEstimator「Invariants」/ 要件 9.4）。
"""

from __future__ import annotations

import math

import numpy as np

from world_frame_calibration import deproject
from world_frame_calibration.errors import CalibrationFailure, FailureReason
from world_frame_calibration.plan import PlanLimits
from world_frame_calibration.types import DepthImage, PixelRegion, Plane, PlaneQuality

__all__ = [
    "estimate_floor_plane",
    "project_onto_plane",
    "signed_distance_mm",
]

_SAMPLE_SIZE = 3
"""平面を一意に定める最小サンプル数（3点。共面判定の基本単位）。"""

_OPTICAL_AXIS_CAMERA = np.array([0.0, 0.0, 1.0], dtype=np.float64)
"""カメラ座標系における光軸（Depth 方向）。上流 `deproject_pixel` の規約
（`z` がそのまま Depth 方向）と一致する。"""


def _ransac_iteration_count(success_probability: float, outlier_ratio: float) -> int:
    """RANSAC の反復回数を成功確率 `p` と外れ値率 `e` から導出する。

    `N = ceil(log(1 - p) / log(1 - (1 - e)^s))`（`s` はサンプル数=3。
    research.md「床平面推定の手法選定」/ design.md FloorPlaneEstimator
    「Responsibilities」）。固定値を埋め込まない。

    `outlier_ratio <= 0`（外れ値なし）の場合、式は `log(0)` を含む不定形
    になる（分母が理論上 `-inf` に発散する）ため、素の最小二乗1回で十分と
    みなし `1` を返す。
    """
    if outlier_ratio <= 0.0:
        return 1
    denominator = math.log(1.0 - (1.0 - outlier_ratio) ** _SAMPLE_SIZE)
    n = math.log(1.0 - success_probability) / denominator
    return max(1, math.ceil(n))


def signed_distance_mm(plane: Plane, points_mm: "np.ndarray") -> "np.ndarray":
    """点群の平面までの符号付き距離を返す。

    `dot(normal, p) - distance_mm`。正の値は法線が向く側（カメラ側、要件
    2.1）を意味する。マーカー観測・独立検証が同じ平面定義を使うための
    公開関数である（design.md FloorPlaneEstimator「Integration」）。

    Args:
        plane: 対象の平面。
        points_mm: `(N, 3)` のカメラ座標系の点群。

    Returns:
        `(N,)` の符号付き距離（mm）。
    """
    normal = np.asarray(plane.normal, dtype=np.float64)
    points = np.asarray(points_mm, dtype=np.float64)
    return points @ normal - plane.distance_mm


def project_onto_plane(plane: Plane, points_mm: "np.ndarray") -> "np.ndarray":
    """点群を平面へ法線方向に投影した点群を返す。

    Args:
        plane: 対象の平面。
        points_mm: `(N, 3)` のカメラ座標系の点群。

    Returns:
        `(N, 3)` の投影後の点群（平面上、符号付き距離が数値誤差の範囲で 0）。
    """
    normal = np.asarray(plane.normal, dtype=np.float64)
    points = np.asarray(points_mm, dtype=np.float64)
    distances = signed_distance_mm(plane, points)
    return points - distances[:, np.newaxis] * normal[np.newaxis, :]


def estimate_floor_plane(image: DepthImage, region: PixelRegion, limits: PlanLimits) -> Plane:
    """`region` 内の Depth 点群から床平面を推定する。

    手順（design.md FloorPlaneEstimator「Responsibilities」）:

    1. `deproject.deproject_region` で範囲内の有効点群を得る（無効画素は
       既に除外済み）。有効点数が `limits.min_inlier_points` に満たなければ
       `CalibrationFailure(INSUFFICIENT_DEPTH_POINTS)`。
    2. RANSAC で内点集合を選ぶ。反復回数は `_ransac_iteration_count` で
       導出する（固定値を埋め込まない）。内点数・内点率が下限を下回れば
       `CalibrationFailure(PLANE_NOT_SUPPORTED)`。
    3. **内点だけ**を使って重心中心の SVD による総最小二乗を解き直す
       （RANSAC の3点サンプルをそのまま採用しない）。
    4. 法線の符号をカメラ側（原点側）正に統一する。
    5. 入射角を算出し、下限を下回れば
       `CalibrationFailure(INCIDENCE_ANGLE_TOO_SHALLOW)`。
    6. 推定品質（点数・内点率・残差・入射角・使用フレーム数・乱数シード）
       を添えて `Plane` を返す。

    Preconditions:
        `region` が画像内であること（`deproject.deproject_region` が検査）。

    Postconditions:
        返す `Plane.normal` は単位長かつカメラ側正。`quality.rng_seed` に
        `limits.rng_seed` がそのまま入る。同一の `image` と同一の
        `limits.rng_seed` に対して常に同一の平面を返す（要件 9.4）。

    Raises:
        CalibrationFailure: 縮退条件（3種、モジュール docstring 参照）。
        CalibrationConfigError: `deproject.deproject_region` が検出する
            設定誤り（内部パラメータ未初期化など）。
    """
    points_mm, _pixels_px = deproject.deproject_region(image, region)
    points_considered = int(points_mm.shape[0])

    if points_considered < limits.min_inlier_points:
        raise CalibrationFailure(
            reason=FailureReason.INSUFFICIENT_DEPTH_POINTS,
            detail=(
                f"only {points_considered} valid depth points are available in "
                "the floor search region, below the minimum of "
                f"{limits.min_inlier_points} required before RANSAC can run"
            ),
            context={
                "points_considered": points_considered,
                "min_inlier_points": limits.min_inlier_points,
            },
        )

    rng = np.random.default_rng(limits.rng_seed)
    n_iterations = _ransac_iteration_count(
        limits.ransac_success_probability, limits.ransac_outlier_ratio
    )

    best_inlier_mask: "np.ndarray | None" = None
    best_inlier_count = -1
    for _ in range(n_iterations):
        sample_idx = rng.choice(points_considered, size=_SAMPLE_SIZE, replace=False)
        sample = points_mm[sample_idx]
        edge1 = sample[1] - sample[0]
        edge2 = sample[2] - sample[0]
        candidate_normal = np.cross(edge1, edge2)
        candidate_norm = np.linalg.norm(candidate_normal)
        if candidate_norm < 1e-12:
            continue  # 縮退したサンプル（ほぼ共線）は候補としない
        candidate_normal = candidate_normal / candidate_norm
        candidate_distance = float(np.dot(candidate_normal, sample[0]))
        residuals = np.abs(points_mm @ candidate_normal - candidate_distance)
        inlier_mask = residuals <= limits.plane_inlier_threshold_mm
        inlier_count = int(np.count_nonzero(inlier_mask))
        if inlier_count > best_inlier_count:
            best_inlier_count = inlier_count
            best_inlier_mask = inlier_mask

    inlier_mask = (
        best_inlier_mask
        if best_inlier_mask is not None
        else np.zeros(points_considered, dtype=bool)
    )
    inlier_count = int(np.count_nonzero(inlier_mask))
    inlier_ratio = inlier_count / points_considered

    if inlier_count < limits.min_inlier_points or inlier_ratio < limits.min_inlier_ratio:
        raise CalibrationFailure(
            reason=FailureReason.PLANE_NOT_SUPPORTED,
            detail=(
                f"RANSAC found only {inlier_count} inliers ({inlier_ratio:.3f} of "
                f"{points_considered} points) within "
                f"{limits.plane_inlier_threshold_mm}mm of the best candidate plane, "
                f"below the required minimum of {limits.min_inlier_points} points / "
                f"{limits.min_inlier_ratio} ratio"
            ),
            context={
                "inlier_count": inlier_count,
                "min_inlier_points": limits.min_inlier_points,
                "inlier_ratio": inlier_ratio,
                "min_inlier_ratio": limits.min_inlier_ratio,
            },
        )

    # 内点だけで SVD による総最小二乗を解き直す（RANSAC の3点サンプルを
    # そのまま採用しない。tasks.md タスク 2.3 の明示的要求）。
    inlier_points = points_mm[inlier_mask]
    center = inlier_points.mean(axis=0)
    centered = inlier_points - center
    _u, _s, vt = np.linalg.svd(centered, full_matrices=False)
    normal = vt[-1]
    normal = normal / np.linalg.norm(normal)
    if np.dot(normal, center) > 0.0:
        # カメラ（原点）がこの平面の法線が向く側に来るよう符号を統一する
        # （要件 2.1: 法線はカメラ側が正）。
        normal = -normal
    distance_mm = float(np.dot(normal, center))

    cos_to_normal = min(1.0, abs(float(np.dot(normal, _OPTICAL_AXIS_CAMERA))))
    incidence_angle_deg = 90.0 - math.degrees(math.acos(cos_to_normal))
    if incidence_angle_deg < limits.min_incidence_angle_deg:
        raise CalibrationFailure(
            reason=FailureReason.INCIDENCE_ANGLE_TOO_SHALLOW,
            detail=(
                f"incidence angle {incidence_angle_deg:.3f}deg between the optical "
                f"axis and the floor plane is below the minimum of "
                f"{limits.min_incidence_angle_deg}deg; the floor is being viewed "
                "too edge-on for a reliable plane fit"
            ),
            context={
                "incidence_angle_deg": incidence_angle_deg,
                "min_incidence_angle_deg": limits.min_incidence_angle_deg,
            },
        )

    final_residuals = np.abs(inlier_points @ normal - distance_mm)
    residual_abs_p50_mm = float(np.percentile(final_residuals, 50))
    residual_abs_p95_mm = float(np.percentile(final_residuals, 95))
    residual_rms_mm = float(np.sqrt(np.mean(np.square(final_residuals))))

    quality = PlaneQuality(
        points_considered=points_considered,
        inlier_count=inlier_count,
        inlier_ratio=inlier_ratio,
        residual_abs_p50_mm=residual_abs_p50_mm,
        residual_abs_p95_mm=residual_abs_p95_mm,
        residual_rms_mm=residual_rms_mm,
        frames_used=image.frames_used,
        incidence_angle_deg=incidence_angle_deg,
        rng_seed=limits.rng_seed,
    )
    return Plane(
        normal=(float(normal[0]), float(normal[1]), float(normal[2])),
        distance_mm=distance_mm,
        quality=quality,
    )
