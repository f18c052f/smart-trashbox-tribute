"""上流の型に依存しない値オブジェクト群（design.md「L0-L2: 基盤・入力」
GeoTypes コンポーネント / tasks.md タスク 1.4 / 要件 1.2, 1.6, 2.5, 2.8, 4.6）。

本モジュールが定義するのは、床平面推定・マーカー観測・World frame 確立・
独立検証のすべての段階で使い回す不変な値の集合である。すべて
`frozen=True, slots=True` の dataclass で値等価とする（design.md GeoTypes
「Invariants」）。

**単位規約**: 距離は `_mm`、角度は `_deg`、画素は `_px` をフィールド名に含める
（design.md GeoTypes「Invariants」/ tasks.md タスク 1.4）。この規約は
`sensing_foundation.types` および `prediction_core.types` が採るものと同じ
考え方を、World frame キャリブレーションの値オブジェクトに適用したものである。

**Depth の欠測表現**: `DepthImage.depth_mm` の無効画素は `NaN` で表し、
**0 で埋めない**（design.md GeoTypes「Postconditions」）。0 は「実測値として
0 mm（距離0）」を意味しうるため、欠測とは区別しなければならない
（`-1` のようなセンチネル値も使わない。`sensing_foundation.types` の
`device_timestamp_ms` が `None` で欠測を表す方針と同じ考え方である）。

**上流フィールド名の写経**: `Intrinsics` は `sensing_foundation.CameraIntrinsics`
の、`StreamSignature` は `sensing_foundation.StreamProfile` の対応部分の
フィールド名を**そのまま**採る（design.md GeoTypes「Integration」）。これは
上流 → 自 Spec の写像（`upstream.to_intrinsics`）と自 Spec → 上流の逆写像
（`deproject.to_camera_intrinsics`）を、フィールド名の読み替えなしに機械的な
転記へ落とすためであり、写経ミスを防ぐ（tasks.md タスク 1.4）。上流の
フィールド名が変われば、この2箇所の写像が Revalidation Trigger の対象になる
（design.md GeoTypes「Risks」）。

**ToleranceSpec の出典必須化**: 許容値は「暫定目標値か実測由来か」
（`provisional`）と出典の説明文（`source`）を必須のフィールドとして持つ
（要件 4.6: 「判定に用いた許容値とその出典が暫定か実測由来かを併せて報告する」）。

**FrameGeometry のヨー感度**: `yaw_sensitivity_deg_per_mm` は
`degrees(1 / baseline_mm)`、`lateral_error_mm_per_mm_at_1000mm` は
`1000 / baseline_mm` として算出される値を保持する。基線長（原点マーカーと
方向マーカーの床平面上の距離）が短い設置ほどマーカー観測のわずかなずれが
+X 軸の向きへ大きく増幅されることの**唯一の可視化手段**であり
（design.md GeoTypes「Risks」/ [research.md](../../.kiro/specs/
world-frame-calibration/research.md#ヨー誤差の増幅とマーカー間距離の下限)）、
確立結果やレポートから落としてはならない（要件 2.8）。

**検証しない**: 本モジュールのすべての dataclass は構築時に何も検証しない
（`__post_init__` を持たない）。これは `prediction_core.types` /
`sensing_foundation.types` の値オブジェクトが検証しない方針を踏襲したもの
であり（design.md GeoTypes「Validation」）、Precondition
（例: `depth_mm.shape == (intrinsics.height_px, intrinsics.width_px)`、
`PixelRegion` が画像内かつ非空であること）の検証は `deproject` と `plan`
の境界の責務である。本モジュールは純粋な値の運び手（データ層）にとどまる。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import numpy as np

__all__ = [
    "AnchorObservation",
    "AnchorRole",
    "DepthImage",
    "FrameGeometry",
    "Intrinsics",
    "PixelRegion",
    "Plane",
    "PlaneQuality",
    "StreamSignature",
    "ToleranceSpec",
]


@dataclass(frozen=True, slots=True)
class Intrinsics:
    """`sensing_foundation.CameraIntrinsics` の写像（design.md GeoTypes）。

    フィールド名は `sensing_foundation.CameraIntrinsics` のそれと完全に
    一致させる（design.md GeoTypes「Integration」。写経ミスを防ぐため、
    上流のフィールド名を読み替えない）。

    Attributes:
        width_px: このパラメータが対応する画像の幅（px）。
        height_px: このパラメータが対応する画像の高さ（px）。
        fx_px: X 方向の焦点距離（px）。
        fy_px: Y 方向の焦点距離（px）。
        ppx_px: 主点の X 座標（px）。
        ppy_px: 主点の Y 座標（px）。
        model: 歪みモデルの名称（例: `"brown_conrady"`）。
        coeffs: 歪み係数（5要素）。
    """

    width_px: int
    height_px: int
    fx_px: float
    fy_px: float
    ppx_px: float
    ppy_px: float
    model: str
    coeffs: tuple[float, float, float, float, float]


@dataclass(frozen=True, slots=True)
class StreamSignature:
    """整合性検査の対象となるストリーム識別情報（design.md GeoTypes / 要件 6.4）。

    フィールド名は `sensing_foundation.StreamProfile` の対応部分と完全に
    一致させる（design.md GeoTypes「Integration」）。永続化されたキャリブ
    レーション結果と現在の入力元のストリーム設定が食い違えば、この型を
    突き合わせることで検出できる（要件 6.4）。

    Attributes:
        width_px: Depth ストリームの幅（px）。
        height_px: Depth ストリームの高さ（px）。
        fps: 要求フレームレート（fps）。
        depth_scale_mm: Depth の1カウントあたりの距離（mm）。
        color_enabled: Color ストリームが有効かどうか。
    """

    width_px: int
    height_px: int
    fps: int
    depth_scale_mm: float
    color_enabled: bool


@dataclass(frozen=True, slots=True)
class PixelRegion:
    """画素範囲（半開区間）。

    Attributes:
        x0_px: 範囲の左端（含む）。
        y0_px: 範囲の上端（含む）。
        x1_px: 範囲の右端（含まない）。
        y1_px: 範囲の下端（含まない）。
    """

    x0_px: int
    y0_px: int
    x1_px: int
    y1_px: int


@dataclass(frozen=True, slots=True)
class DepthImage:
    """探索範囲の逆投影・平面推定に用いる、mm 換算済みの Depth 画像。

    Preconditions（構築時には検証しない。検証は `deproject` / `plan` の
    境界の責務。design.md GeoTypes「Validation」）:
        `depth_mm.shape == (intrinsics.height_px, intrinsics.width_px)`

    Postconditions:
        `depth_mm` の無効画素は `NaN` を意味し、0 で埋めない
        （design.md GeoTypes「Postconditions」）。

    Attributes:
        depth_mm: `float64`、shape=`(h, w)` の mm 換算済み Depth。
            無効画素は `NaN`。
        valid_count: `int32`、shape=`(h, w)`。平均に寄与した枚数
            （複数フレームで安定化した場合、各画素が何枚の有効値の平均か）。
        frames_used: 平均化に使用したフレーム数。
        intrinsics: このフレーム取得時点のカメラ内部パラメータ。
        signature: このフレーム取得時点のストリーム識別情報。
        source_kind: 入力元の種別（`"live"` / `"recorded"` / `"simulated"`）。
    """

    depth_mm: np.ndarray
    valid_count: np.ndarray
    frames_used: int
    intrinsics: Intrinsics
    signature: StreamSignature
    source_kind: str


@dataclass(frozen=True, slots=True)
class Plane:
    """カメラ座標系における床平面のパラメータ。

    Postconditions:
        `normal` は単位長かつカメラ側（原点側）が正
        （design.md GeoTypes「Postconditions」/ 要件 2.1）。

    Attributes:
        normal: 平面の単位法線ベクトル（カメラ座標系、カメラ側が正）。
        distance_mm: 平面上の点 `p` に対し `dot(normal, p) == distance_mm`
            を満たす距離。
        quality: この平面推定の品質。
    """

    normal: tuple[float, float, float]
    distance_mm: float
    quality: PlaneQuality


@dataclass(frozen=True, slots=True)
class PlaneQuality:
    """床平面推定の品質（要件 1.2, 1.6）。

    Attributes:
        points_considered: 平面推定に用いた有効点の数。
        inlier_count: 平面に適合した点の数。
        inlier_ratio: 平面に適合した点の割合。
        residual_abs_p50_mm: 平面までの距離残差（絶対値）の中央値。
        residual_abs_p95_mm: 平面までの距離残差（絶対値）の95パーセンタイル。
        residual_rms_mm: 平面までの距離残差の二乗平均平方根。
        frames_used: 推定の安定化に使用したフレーム数（要件 1.6）。
        incidence_angle_deg: 光軸と床面のなす入射角（deg）。小さいほど浅い。
        rng_seed: RANSAC 等の乱数種。同一シードで常に同一の平面を返すことの
            再現性を担保する（design.md FloorPlaneEstimator「Invariants」）。
    """

    points_considered: int
    inlier_count: int
    inlier_ratio: float
    residual_abs_p50_mm: float
    residual_abs_p95_mm: float
    residual_rms_mm: float
    frames_used: int
    incidence_angle_deg: float
    rng_seed: int


class AnchorRole(StrEnum):
    """マーカー観測が World frame の確立において果たす役割。"""

    ORIGIN = "origin"
    """原点マーカー（要件 2.2）。"""

    X_AXIS = "x_axis"
    """方向マーカー（+X 軸の向きを定める。要件 2.3）。"""

    VERIFICATION = "verification"
    """検証点。World frame の確立には用いない（要件 4.3）。"""


@dataclass(frozen=True, slots=True)
class AnchorObservation:
    """マーカー・検証点の観測結果。

    Attributes:
        label: マーカー・検証点の識別ラベル。
        role: このマーカーが果たす役割。
        point_camera_mm: ロバスト代表点（床平面への投影前、カメラ座標系）。
        point_on_plane_mm: 床平面へ法線方向に投影した点（カメラ座標系）。
        height_above_plane_mm: 床平面からの高さ。
        range_from_camera_mm: カメラからの距離。
        sample_count: 代表点の算出に用いたサンプル数。
        spread_mm: 代表点の散らばり（ロバスト統計に基づく）。
        region: このマーカーの探索に用いた画素範囲。
        frames_used: 観測の安定化に使用したフレーム数。
    """

    label: str
    role: AnchorRole
    point_camera_mm: tuple[float, float, float]
    point_on_plane_mm: tuple[float, float, float]
    height_above_plane_mm: float
    range_from_camera_mm: float
    sample_count: int
    spread_mm: float
    region: PixelRegion
    frames_used: int


@dataclass(frozen=True, slots=True)
class FrameGeometry:
    """World frame の軸・原点と、確立結果の感度指標。

    Attributes:
        origin_camera_mm: World frame の原点（カメラ座標系）。
        x_axis_camera: World frame の +X 軸（カメラ座標系、単位ベクトル）。
        y_axis_camera: World frame の +Y 軸（カメラ座標系、単位ベクトル）。
        z_axis_camera: World frame の +Z 軸（カメラ座標系、単位ベクトル）。
        baseline_mm: 原点マーカーと方向マーカーの床平面上の距離。
        yaw_sensitivity_deg_per_mm: `degrees(1 / baseline_mm)`。マーカー観測の
            1mm のずれが +X 軸の向きへ与える角度誤差（deg）。基線長が短い
            設置ほど大きくなる（design.md GeoTypes「Risks」/ 要件 2.8）。
        lateral_error_mm_per_mm_at_1000mm: `1000 / baseline_mm`。距離1000mm
            地点における、マーカー観測1mmのずれに対応する横方向誤差（mm）。
    """

    origin_camera_mm: tuple[float, float, float]
    x_axis_camera: tuple[float, float, float]
    y_axis_camera: tuple[float, float, float]
    z_axis_camera: tuple[float, float, float]
    baseline_mm: float
    yaw_sensitivity_deg_per_mm: float
    lateral_error_mm_per_mm_at_1000mm: float


@dataclass(frozen=True, slots=True)
class ToleranceSpec:
    """独立検証の合否判定に用いる許容値と、その出典（要件 4.6）。

    Attributes:
        horizontal_mm: 水平方向（x, y）の許容誤差。
        vertical_mm: 鉛直方向（z）の許容誤差。
        provisional: 暫定目標値であれば `True`、実測由来であれば `False`
            （要件 4.6）。
        source: 出典の説明文。`provisional` の別に関わらず必須
            （tasks.md タスク 1.4: 「許容値仕様に『暫定か実測由来か』と
            出典文字列を必須で持たせる」）。
    """

    horizontal_mm: float
    vertical_mm: float
    provisional: bool
    source: str
