"""剛体変換の保持と適用（design.md「L3」節 WorldTransform コンポーネント /
tasks.md タスク 2.2 / 要件 3.1, 3.2, 3.3, 3.6, 7.4, 11.4）。

本モジュールが提供するもの:

- `WorldTransform`: 回転（`R_world_from_camera`）と平行移動を保持する不変値。
  単点変換（`apply_point`）・点群変換（`apply`）・逆変換（`inverse`）・
  4x4 同次行列表現（`as_matrix`）を提供する
- `TransformDifference` / `compare_transforms`: 2つの変換の差分を、原点の
  ずれ・全体の回転角・Z 軸のずれ・面内回転（ヨー）のずれへ分解する

**構築時の検査（design.md WorldTransform「Preconditions」/ 要件 3.3, 5.6）**:
`rotation` は正規直交かつ行列式 +1（拡大縮小・せん断・鏡映のいずれも
含まない）であることを構築時に検査する。外れれば
`CalibrationFailure(reason=FailureReason.ROTATION_NOT_ORTHONORMAL)` を
送出する（design.md「Preconditions」が明示するとおり）。正規直交でない、
または行列式が +1 でない回転行列は、それ自体が有効な剛体座標系を
構成しない——`errors.py` の分類でいう**座標系が定まらない条件**
（`CalibrationFailure` の守備範囲）であり、設定や呼び出し方がまだ
座標系の成否に届いていない誤り（`CalibrationConfigError` の守備範囲）
とは異なる。したがって `context` に測定した正規直交性の残差・行列式と
それぞれの許容差を含め、`reason` で呼び出し側が分岐できるようにする
（要件 5.6）。`rotation` が 3x3 の形状ですらない場合（そもそも回転行列の
体裁を成していない、行列としての形の誤り）のみ `CalibrationConfigError`
を使う。

**`apply` にはログ送出・確保・検証を一切含まない**（design.md WorldTransform
「Risks」/ tasks.md タスク 2.2 Critical constraint）。`WorldTransform.apply`
は飛翔物追跡が毎フレーム呼ぶ唯一の経路であり、`(N, 3)` の行列積と平行移動の
加算だけの最小実装にとどめる。計測は `upstream.timed_apply`（タスク 6.2）が
薄い委譲として別途担う（要件 10.4）。検証は構築時にのみ行い、`apply` の
呼び出しごとには行わない。

**本モジュールは `sensing_foundation` にも `numpy` 以外の外部にも依存しない**
（design.md WorldTransform「Integration」）。下流（`flying-object-tracking` /
`m1-prediction-validation`）は変換の適用にカメラ・SDK・ファイル入出力への
接続を要求されない（要件 3.6, 11.4）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from world_frame_calibration.errors import (
    CalibrationConfigError,
    CalibrationFailure,
    FailureReason,
)
from world_frame_calibration.linalg import rotation_angle_deg

__all__ = [
    "TransformDifference",
    "WorldTransform",
    "compare_transforms",
]

# 構築時検査の許容差。design.md はこの数値自体を指定していないため、
# `linalg` テスト群が用いる代表的な数値誤差の桁（1e-6〜1e-9）のうち、
# 浮動小数で構成された正規直交基底が確実に通る側の緩めの値を採る。
_ORTHONORMAL_TOL = 1e-6
_DETERMINANT_TOL = 1e-6


def _vector_angle_deg(u: "np.ndarray", v: "np.ndarray") -> float:
    """2つの単位ベクトル間の角度を度で返す（浮動小数のオーバーシュートに耐える）。"""
    cos_angle = np.clip(float(np.dot(u, v)), -1.0, 1.0)
    return float(np.degrees(np.arccos(cos_angle)))


@dataclass(frozen=True, slots=True)
class WorldTransform:
    """剛体変換（回転と平行移動のみ）。`p_world = R @ p_camera + t`。

    Attributes:
        rotation: 3x3 の回転行列。`R_world_from_camera`。各行がカメラ座標系で
            表した World の各軸である（`WorldFrameBuilder` の docstring 参照）。
        translation_mm: 平行移動（mm）。

    Preconditions:
        `rotation` は正規直交かつ行列式 +1 であること。外れれば
        `CalibrationFailure(reason=FailureReason.ROTATION_NOT_ORTHONORMAL)`
        を送出する（design.md「Preconditions」）。

    Postconditions:
        `apply` は `(N, 3)` の `float64` を返し、入力を変更しない。
        拡大縮小・せん断を含まない（要件 3.3）。`inverse()` の往復は
        数値誤差の範囲で恒等。
    """

    rotation: tuple[tuple[float, float, float], ...]
    translation_mm: tuple[float, float, float]
    _rotation_matrix: "np.ndarray" = field(
        init=False, repr=False, compare=False
    )
    _translation_vector: "np.ndarray" = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        rotation_matrix = np.asarray(self.rotation, dtype=np.float64)
        if rotation_matrix.shape != (3, 3):
            raise CalibrationConfigError(
                "WorldTransform.rotation must be a 3x3 matrix, got shape "
                f"{rotation_matrix.shape!r}"
            )
        orthonormal_residual = float(
            np.max(
                np.abs(
                    rotation_matrix.T @ rotation_matrix - np.eye(3, dtype=np.float64)
                )
            )
        )
        if orthonormal_residual > _ORTHONORMAL_TOL:
            raise CalibrationFailure(
                reason=FailureReason.ROTATION_NOT_ORTHONORMAL,
                detail=(
                    "WorldTransform.rotation must be orthonormal (R^T R == I) "
                    f"within tol={_ORTHONORMAL_TOL}; a non-orthonormal matrix "
                    "would introduce scale or shear into the coordinate system "
                    "and does not define a valid rigid coordinate frame"
                ),
                context={
                    "orthonormal_residual": orthonormal_residual,
                    "orthonormal_tol": _ORTHONORMAL_TOL,
                },
            )
        determinant = float(np.linalg.det(rotation_matrix))
        if not math.isclose(determinant, 1.0, abs_tol=_DETERMINANT_TOL):
            raise CalibrationFailure(
                reason=FailureReason.ROTATION_NOT_ORTHONORMAL,
                detail=(
                    "WorldTransform.rotation must have determinant +1 (a "
                    "proper rotation, no reflection); a matrix with "
                    f"determinant={determinant!r} does not define a valid "
                    "right-handed rigid coordinate frame"
                ),
                context={
                    "determinant": determinant,
                    "determinant_tol": _DETERMINANT_TOL,
                },
            )
        object.__setattr__(self, "_rotation_matrix", rotation_matrix)
        object.__setattr__(
            self,
            "_translation_vector",
            np.asarray(self.translation_mm, dtype=np.float64),
        )

    def apply_point(self, p_camera_mm) -> tuple[float, float, float]:
        """カメラ座標の1点を World 座標へ変換する。"""
        result = self._rotation_matrix @ np.asarray(
            p_camera_mm, dtype=np.float64
        ) + self._translation_vector
        return (float(result[0]), float(result[1]), float(result[2]))

    def apply(self, points_camera_mm: "np.ndarray") -> "np.ndarray":
        """カメラ座標の点群 `(N, 3)` を World 座標 `(N, 3)` へ変換する。

        毎フレーム走る唯一の経路である。ログ送出・確保・検証を一切行わない
        （design.md「Risks」/ tasks.md タスク 2.2 Critical constraint）。
        """
        return points_camera_mm @ self._rotation_matrix.T + self._translation_vector

    def inverse(self) -> "WorldTransform":
        """逆変換（World 座標 → カメラ座標）を返す。"""
        rotation_inverse = self._rotation_matrix.T
        translation_inverse = -rotation_inverse @ self._translation_vector
        return WorldTransform(
            rotation=tuple(
                tuple(float(v) for v in row) for row in rotation_inverse
            ),
            translation_mm=tuple(float(v) for v in translation_inverse),
        )

    def as_matrix(self) -> "np.ndarray":
        """4x4 の同次変換行列を返す（表示・保存の補助）。"""
        matrix = np.eye(4, dtype=np.float64)
        matrix[:3, :3] = self._rotation_matrix
        matrix[:3, 3] = self._translation_vector
        return matrix


@dataclass(frozen=True, slots=True)
class TransformDifference:
    """2つの `WorldTransform` の差分（design.md WorldTransform 契約）。

    Attributes:
        origin_shift_mm: 原点のずれ（`b.translation_mm - a.translation_mm`）。
        origin_shift_norm_mm: `origin_shift_mm` のノルム。
        axis_angle_deg: 全体の回転角（`linalg.rotation_angle_deg` による）。
        z_axis_angle_deg: 床法線（Z 軸）のずれ。平面推定の再現性を表す。
        yaw_angle_deg: 面内回転（ヨー）のずれ。マーカー設置の再現性を表す。
            `a` の Z 軸を基準平面とし、`b` の +X 軸をその平面へ投影した上で
            `a` の +X 軸との角度として算出する。
    """

    origin_shift_mm: tuple[float, float, float]
    origin_shift_norm_mm: float
    axis_angle_deg: float
    z_axis_angle_deg: float
    yaw_angle_deg: float


def compare_transforms(a: WorldTransform, b: WorldTransform) -> TransformDifference:
    """2つの変換の差分を、原点のずれ・全体の回転角・Z 軸のずれ・ヨーのずれへ分解する。"""
    rotation_a = np.asarray(a.rotation, dtype=np.float64)
    rotation_b = np.asarray(b.rotation, dtype=np.float64)
    translation_a = np.asarray(a.translation_mm, dtype=np.float64)
    translation_b = np.asarray(b.translation_mm, dtype=np.float64)

    origin_shift = translation_b - translation_a
    origin_shift_norm = float(np.linalg.norm(origin_shift))

    axis_angle_deg = rotation_angle_deg(rotation_a, rotation_b)

    z_a = rotation_a[2, :]
    z_b = rotation_b[2, :]
    z_axis_angle_deg = _vector_angle_deg(z_a, z_b)

    x_a = rotation_a[0, :]
    x_b = rotation_b[0, :]
    # b の +X 軸を a の Z 軸に垂直な平面へ投影し、Z 軸のずれ（tilt）の寄与を
    # 取り除いた上でヨーだけを測る。
    x_b_in_a_plane = x_b - np.dot(x_b, z_a) * z_a
    x_b_in_a_plane_norm = np.linalg.norm(x_b_in_a_plane)
    if x_b_in_a_plane_norm > 0.0:
        x_b_in_a_plane_unit = x_b_in_a_plane / x_b_in_a_plane_norm
        yaw_angle_deg = _vector_angle_deg(x_a, x_b_in_a_plane_unit)
    else:
        # b の +X 軸が a の Z 軸とほぼ平行（縮退）な場合、面内成分が定義できない。
        yaw_angle_deg = 0.0

    return TransformDifference(
        origin_shift_mm=(
            float(origin_shift[0]),
            float(origin_shift[1]),
            float(origin_shift[2]),
        ),
        origin_shift_norm_mm=origin_shift_norm,
        axis_angle_deg=axis_angle_deg,
        z_axis_angle_deg=z_axis_angle_deg,
        yaw_angle_deg=yaw_angle_deg,
    )
