"""マーカー・検証点の観測（design.md「L3-L5: 推定と確立」AnchorObserver
コンポーネント / tasks.md タスク 2.4 / 要件 2.2, 2.6, 5.2, 9.3）。

**範囲 + 高さバンド + 中央値**という3段だけで観測する（design.md
AnchorObserver「Responsibilities」）。連結成分抽出も形状当てはめも行わない。
マーカーの**色・柄・既知寸法を要求しない**（要件 2.6）。周囲の床面から
区別できる高さを持つ物体でありさえすればよい。

**選定基準は床平面からの符号付き距離のみ**（design.md AnchorObserver
「Invariants」）: `spec.region` の逆投影で得た点群のうち、`plane.
signed_distance_mm`（タスク 2.3 が公開する床平面演算）で求めた「床平面から
の符号付き距離」が `spec.height_band_mm` の帯に入る点だけを選ぶ。画像上の
位置や絶対的な奥行きでは判定しない。この符号付き距離の計算・平面への投影は
一切再実装せず、`world_frame_calibration.plane` の公開関数
（`signed_distance_mm` / `project_onto_plane`）へ委譲する。マーカー観測と
独立検証（`Verifier`, タスク 5.x）が同じ平面定義を使うことを保証するため
である。

**代表点はロバスト統計で求める**: 選んだ点群の中央値ベースの代表点
（`world_frame_calibration.linalg.robust_center`、タスク 1.3）を求め、床平面
へ**法線方向に投影**した点（`plane.project_onto_plane`）を観測結果とする。
マーカーの高さそのものは、この投影によって最終結果から意図的に消える
（要件 2.6: マーカーの高さは判定に使うだけで、位置には使わない）。

**確立用マーカーと検証点は同一の関数を使う**（design.md AnchorObserver
「Integration」/ tasks.md タスク 2.4）: `role`（`AnchorRole.ORIGIN` /
`X_AXIS` / `VERIFICATION`）は観測結果に付与するラベルに過ぎず、`role` に
応じて処理を分岐させない。確立と検証で観測方法が異なると、検証が「観測方法
の違い」を測ってしまい、座標系の正しさを測れなくなるためである。

**点数不足はどのマーカーかを特定できる形で失敗させる**（要件 5.2）:
高さ帯内の点数が `limits.min_anchor_samples` を下回れば
`CalibrationFailure(FailureReason.ANCHOR_NOT_FOUND)` を送出し、`context` に
`label`（どのマーカーか）・`region`・`height_band_mm`・実際の点数・下限を
含める。
"""

from __future__ import annotations

import numpy as np

from world_frame_calibration import plane as plane_module
from world_frame_calibration.deproject import deproject_region
from world_frame_calibration.errors import CalibrationFailure, FailureReason
from world_frame_calibration.linalg import robust_center
from world_frame_calibration.plan import AnchorSpec, PlanLimits
from world_frame_calibration.types import AnchorObservation, AnchorRole, DepthImage, Plane

__all__ = [
    "observe_anchor",
]


def observe_anchor(
    image: DepthImage,
    plane: Plane,
    spec: AnchorSpec,
    role: AnchorRole,
    limits: PlanLimits,
) -> AnchorObservation:
    """`spec.region` 内で `spec.height_band_mm` の高さ帯に入る点からマーカー・
    検証点を観測する。

    Preconditions:
        `plane` が推定済み。`spec.region` が画像内。

    Postconditions:
        `point_on_plane_mm` は平面上（`plane.signed_distance_mm` が数値誤差の
        範囲で 0）。`sample_count >= limits.min_anchor_samples`。

    Invariants:
        高さ帯は**床平面からの符号付き距離**で判定する。画像上の位置や
        絶対的な奥行きでは判定しない。

    Args:
        image: 探索対象の Depth 画像。
        plane: 推定済みの床平面（マーカー観測・独立検証で共有する平面定義）。
        spec: 観測対象の探索範囲・高さ帯・ラベル。
        role: この観測が果たす役割（`ORIGIN` / `X_AXIS` / `VERIFICATION`）。
            観測結果への付与のみに用い、処理を分岐させない
            （design.md AnchorObserver「Integration」）。
        limits: `min_anchor_samples` を含む下限群。

    Returns:
        観測結果。`point_camera_mm` は投影前のロバスト代表点、
        `point_on_plane_mm` は床平面へ法線方向に投影した点。

    Raises:
        CalibrationFailure: 高さ帯内の点数が `limits.min_anchor_samples` に
            満たない場合（`FailureReason.ANCHOR_NOT_FOUND`）。`context` に
            `label` を含み、どのマーカーが見つからなかったか特定できる。
    """
    points_mm, _pixels_px = deproject_region(image, spec.region)

    if points_mm.shape[0] > 0:
        distances_mm = plane_module.signed_distance_mm(plane, points_mm)
        lower_mm, upper_mm = spec.height_band_mm
        in_band_mask = (distances_mm >= lower_mm) & (distances_mm <= upper_mm)
        selected_points_mm = points_mm[in_band_mask]
    else:
        selected_points_mm = points_mm

    sample_count = int(selected_points_mm.shape[0])
    if sample_count < limits.min_anchor_samples:
        raise CalibrationFailure(
            reason=FailureReason.ANCHOR_NOT_FOUND,
            detail=(
                f"marker {spec.label!r} was not found: only {sample_count} points "
                f"fell within height_band_mm={spec.height_band_mm} in region "
                f"{spec.region!r}, below the required minimum of "
                f"{limits.min_anchor_samples}"
            ),
            context={
                "label": spec.label,
                "region": spec.region,
                "height_band_mm": spec.height_band_mm,
                "sample_count": sample_count,
                "min_anchor_samples": limits.min_anchor_samples,
            },
        )

    center_mm, spread_mm = robust_center(selected_points_mm)
    center_point_mm = center_mm.reshape(1, 3)

    height_above_plane_mm = float(
        plane_module.signed_distance_mm(plane, center_point_mm)[0]
    )
    projected_mm = plane_module.project_onto_plane(plane, center_point_mm)[0]
    range_from_camera_mm = float(np.linalg.norm(center_mm))

    return AnchorObservation(
        label=spec.label,
        role=role,
        point_camera_mm=(float(center_mm[0]), float(center_mm[1]), float(center_mm[2])),
        point_on_plane_mm=(
            float(projected_mm[0]),
            float(projected_mm[1]),
            float(projected_mm[2]),
        ),
        height_above_plane_mm=height_above_plane_mm,
        range_from_camera_mm=range_from_camera_mm,
        sample_count=sample_count,
        spread_mm=spread_mm,
        region=spec.region,
        frames_used=image.frames_used,
    )
