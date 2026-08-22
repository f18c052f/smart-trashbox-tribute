"""床平面と2つのマーカーから World frame を確立する（design.md「L3-L5: 推定と確立」
WorldFrameBuilder コンポーネント / tasks.md タスク 3.1 / 要件 2.1, 2.2, 2.3, 2.4,
2.7, 3.7, 5.3）。

**軸の構成**（design.md WorldFrameBuilder「Responsibilities & Constraints」/
要件 2.1〜2.4, A-2）:

- **+Z** ＝ `plane.normal`（推定済みの床平面の法線。カメラ側が正）
- **原点** ＝ 原点マーカーの `point_on_plane_mm`
- **+X** ＝ 原点マーカー → 方向マーカーへ向かうベクトルを正規化した向き。
  両マーカーの `point_on_plane_mm` は既に同一の床平面へ投影済み
  （タスク 2.4 `AnchorObserver` の責務）であるため、その差分ベクトルは
  追加の投影を要さず自動的に平面内成分となる
- **+Y** ＝ Z × X（右手系）。`linalg.orthonormal_basis` で構成する

**縮退条件は2つの独立したゲートで検査する**（design.md WorldFrameBuilder
「Validation」/ 要件 2.7, 5.3, A-9）:

1. **基線長不足**（`ANCHOR_BASELINE_TOO_SHORT`）: 原点マーカーと方向マーカーの
   床平面上の距離が `limits.min_baseline_mm` を下回る場合。+X 軸の向きが
   マーカー観測のわずかなずれに対して不安定になる（要件 2.7, 5.3）
2. **方向の縮退**（`ANCHOR_DEGENERATE`）: 方向ベクトルの Z 軸直交成分（＝
   平面内成分）がほぼ消える場合。これは基線長が十分でも独立に起こりうる
   条件である（例: `point_on_plane_mm` が実際には `plane` と同一平面上に
   無い異常入力）。どちらか一方の検査だけに減らさない

**呼び出し順序が重要**（`linalg` モジュール docstring / tasks.md 実装ノート
「タスク1.3」参照）: `linalg.orthonormal_basis` は `x_hint` が `z_axis` と
ほぼ平行（縮退）な入力で呼ばれると `unit()` 内の 0/0 により NaN を**例外を
送出せず静かに返す**。そのため本モジュールは `linalg.orthonormal_basis` を
呼ぶ**前に**必ず `linalg.x_hint_degeneracy` で縮退を確認し、縮退時は
`CalibrationFailure(ANCHOR_DEGENERATE)` を送出してから `orthonormal_basis`
を呼ばない。この順序を守らないと、縮退した基底が静かに下流
（`WorldTransform` の構築）へ流れる。

**カメラの位置・姿勢を一切参照しない**（design.md WorldFrameBuilder
「Responsibilities & Constraints」/ 要件 2.9, A-2）。`build_world_frame` の
引数は `plane`・`origin_anchor`・`x_axis_anchor`・`limits` のみであり、
カメラの外部パラメータ（位置・姿勢）に相当する引数を一切持たない。frame は
マーカーの物理配置と床平面だけで決まり、同じ物理配置であればカメラを
どこに設置し直しても同一の World frame が再現される。

**回転行列の軸の対応**（design.md WorldFrameBuilder「Integration」の明示的な
docstring 要求）: 構成する `WorldTransform.rotation` の**各行**が、カメラ
座標系で表した World の X / Y / Z 軸である（1行目が +X 軸、2行目が +Y 軸、
3行目が +Z 軸）。すなわち `R = [[x_axis_camera], [y_axis_camera],
[z_axis_camera]]` であり、`p_world = R @ (p_camera - origin_camera_mm)`。
平行移動は `t = -R @ origin_camera_mm` として求める。

**ヨー感度を確立結果へ含める**（design.md GeoTypes「Risks」/ 要件 2.8）:
基線長から `FrameGeometry.yaw_sensitivity_deg_per_mm`
（`degrees(1 / baseline_mm)`）と `lateral_error_mm_per_mm_at_1000mm`
（`1000 / baseline_mm`）を算出する。これらは `types.py` 自体では計算されない
純粋な値オブジェクトのフィールドであり（`types.py` モジュール docstring
参照）、この解析的関係を実際に計算して埋めるのは本モジュールの責務である
（tasks.md 実装ノート「タスク1.4」）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from world_frame_calibration.errors import CalibrationFailure, FailureReason
from world_frame_calibration.linalg import orthonormal_basis, x_hint_degeneracy
from world_frame_calibration.plan import PlanLimits
from world_frame_calibration.transform import WorldTransform
from world_frame_calibration.types import AnchorObservation, FrameGeometry, Plane

__all__ = [
    "WorldFrameEstablishment",
    "build_world_frame",
]


@dataclass(frozen=True, slots=True)
class WorldFrameEstablishment:
    """`build_world_frame` の返り値（design.md WorldFrameBuilder「Contracts」）。

    Attributes:
        transform: 確立した剛体変換（カメラ座標系 → World 座標系）。
        geometry: World frame の軸・原点・ヨー感度（要件 2.8）。
        plane: 確立に用いた床平面（呼び出し側の追跡・永続化のために保持する）。
        origin_anchor: 確立に用いた原点マーカーの観測。
        x_axis_anchor: 確立に用いた方向マーカーの観測。
    """

    transform: WorldTransform
    geometry: FrameGeometry
    plane: Plane
    origin_anchor: AnchorObservation
    x_axis_anchor: AnchorObservation


def build_world_frame(
    plane: Plane,
    origin_anchor: AnchorObservation,
    x_axis_anchor: AnchorObservation,
    limits: PlanLimits,
) -> WorldFrameEstablishment:
    """床平面と2つのマーカー観測から World frame（剛体変換）を確立する。

    +Z は `plane.normal`、原点は `origin_anchor.point_on_plane_mm`、+X は
    原点マーカー → 方向マーカーへ向かうベクトルを正規化した向き、+Y は
    Z × X（右手系）として構成する（要件 2.1〜2.4）。`WorldTransform.rotation`
    の**各行**がカメラ座標系で表した World の X / Y / Z 軸になる（1行目が
    +X 軸、2行目が +Y 軸、3行目が +Z 軸。モジュール docstring「回転行列の
    軸の対応」参照）。

    カメラの位置・姿勢に相当する引数は一切受け取らない（要件 2.9）。frame は
    `plane` と2つのマーカー観測の物理配置だけで決まる。

    Preconditions:
        `origin_anchor` と `x_axis_anchor` はいずれも `plane` と同一の
        床平面に対して得られた観測であること（両者の `point_on_plane_mm`
        が同一平面上にあること）。

    Postconditions:
        `transform.apply_point(origin_anchor.point_on_plane_mm) ≈ (0, 0, 0)`。
        `transform.apply_point(x_axis_anchor.point_on_plane_mm) ≈
        (geometry.baseline_mm, 0, 0)`。床平面上の任意の点を変換した結果の
        z 成分は数値誤差の範囲で 0（要件 3.7）。`transform.rotation` は
        正規直交かつ行列式 +1（`WorldTransform` 構築時に検査される）。

    Args:
        plane: 推定済みの床平面。`normal` が World の +Z 軸になる。
        origin_anchor: 原点マーカーの観測（`role == AnchorRole.ORIGIN`）。
        x_axis_anchor: 方向マーカーの観測（`role == AnchorRole.X_AXIS`）。
        limits: `min_baseline_mm` を含む下限群。

    Returns:
        確立した変換と frame 幾何を持つ `WorldFrameEstablishment`。

    Raises:
        CalibrationFailure: 基線長が `limits.min_baseline_mm` を下回る場合
            （`FailureReason.ANCHOR_BASELINE_TOO_SHORT`。+X 軸が不安定に
            なる。要件 2.7, 5.3）。方向ベクトルが床平面法線とほぼ平行で
            平面内成分が定まらない場合（`FailureReason.ANCHOR_DEGENERATE`。
            A-9）。この2つは独立した検査であり、基線長が十分でも縮退は
            起こりうる。
    """
    origin_mm = np.asarray(origin_anchor.point_on_plane_mm, dtype=np.float64)
    x_anchor_mm = np.asarray(x_axis_anchor.point_on_plane_mm, dtype=np.float64)
    z_axis_hint = np.asarray(plane.normal, dtype=np.float64)

    # +X 方向ベクトル: 両マーカーは既に同一平面へ投影済み（タスク 2.4）で
    # あるため、その差分は追加の投影なしに自動的に平面内成分となる。
    direction_mm = x_anchor_mm - origin_mm
    baseline_mm = float(np.linalg.norm(direction_mm))

    if baseline_mm < limits.min_baseline_mm:
        raise CalibrationFailure(
            reason=FailureReason.ANCHOR_BASELINE_TOO_SHORT,
            detail=(
                "+X axis is unstable: the floor-plane distance between the "
                f"origin marker {origin_anchor.label!r} and the direction "
                f"marker {x_axis_anchor.label!r} is {baseline_mm:.3f}mm, "
                f"below the required minimum of {limits.min_baseline_mm}mm"
            ),
            context={
                "baseline_mm": baseline_mm,
                "min_baseline_mm": limits.min_baseline_mm,
                "origin_label": origin_anchor.label,
                "x_axis_label": x_axis_anchor.label,
            },
        )

    # `linalg.orthonormal_basis` を呼ぶ前に必ず縮退を確認する（モジュール
    # docstring「呼び出し順序が重要」参照。tasks.md 実装ノート「タスク1.3」）。
    is_degenerate, orthogonal_ratio = x_hint_degeneracy(z_axis_hint, direction_mm)
    if is_degenerate:
        raise CalibrationFailure(
            reason=FailureReason.ANCHOR_DEGENERATE,
            detail=(
                f"direction marker {x_axis_anchor.label!r} is degenerate "
                f"relative to origin marker {origin_anchor.label!r}: the "
                "vector between them is nearly parallel to the floor plane "
                f"normal (in-plane component ratio={orthogonal_ratio:.3e}), "
                "so the +X axis direction cannot be determined"
            ),
            context={
                "baseline_mm": baseline_mm,
                "orthogonal_component_ratio": orthogonal_ratio,
                "origin_label": origin_anchor.label,
                "x_axis_label": x_axis_anchor.label,
            },
        )

    x_axis, y_axis, z_axis = orthonormal_basis(z_axis_hint, direction_mm)

    rotation = (
        (float(x_axis[0]), float(x_axis[1]), float(x_axis[2])),
        (float(y_axis[0]), float(y_axis[1]), float(y_axis[2])),
        (float(z_axis[0]), float(z_axis[1]), float(z_axis[2])),
    )
    rotation_matrix = np.array(rotation, dtype=np.float64)
    translation_vector = -rotation_matrix @ origin_mm
    translation_mm = (
        float(translation_vector[0]),
        float(translation_vector[1]),
        float(translation_vector[2]),
    )

    transform = WorldTransform(rotation=rotation, translation_mm=translation_mm)

    yaw_sensitivity_deg_per_mm = math.degrees(1.0 / baseline_mm)
    lateral_error_mm_per_mm_at_1000mm = 1000.0 / baseline_mm

    geometry = FrameGeometry(
        origin_camera_mm=(float(origin_mm[0]), float(origin_mm[1]), float(origin_mm[2])),
        x_axis_camera=rotation[0],
        y_axis_camera=rotation[1],
        z_axis_camera=rotation[2],
        baseline_mm=baseline_mm,
        yaw_sensitivity_deg_per_mm=yaw_sensitivity_deg_per_mm,
        lateral_error_mm_per_mm_at_1000mm=lateral_error_mm_per_mm_at_1000mm,
    )

    return WorldFrameEstablishment(
        transform=transform,
        geometry=geometry,
        plane=plane,
        origin_anchor=origin_anchor,
        x_axis_anchor=x_axis_anchor,
    )
