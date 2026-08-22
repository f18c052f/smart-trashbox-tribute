"""範囲限定の逆投影と歪みモデルの受理判定（design.md「L0-L2: 基盤・入力」
Deprojector コンポーネント / tasks.md タスク 2.1 / 要件 1.3, 1.4, 3.4, 3.5, 3.8）。

**ピンホールの式は書かない。** 1点ごとの逆投影は `sensing_foundation` の
公開入口が提供する `deproject_pixel` へ委譲する（要件 3.8）。画素中心規約
（`+0.5` を足さない）・mm 換算の位置・無効値の定義は上流が持つ規約であり、
本モジュールで決め直さない（`sensing_foundation.geometry` の docstring が正）。

**本モジュールが上流から参照してよいのは `deproject_pixel` と
`CameraIntrinsics` の2シンボルだけである。** これは design.md
「Dependency Direction」表が定める「接点は `upstream.py` だけ」という当初の
制約を意図的に緩めたものである。基本演算は L2 の逆投影で必要になり、L8
の `upstream.py` 経由では層を逆流させずに届かないためであり、緩めた代わりに
参照シンボルをこの2つへ限定し、`test_boundaries.py`（タスク 7.3）が列挙で
固定する。本モジュール自身も
`test_world_frame_calibration_deproject.py` が同じ制約を静的に検査する。

本モジュールが持つ、上流に無い自 Spec 固有の責務は次の3つである:

1. **歪み係数の受理判定**（`ensure_supported_distortion`）。上流は歪み補正を
   持たず、係数が全て 0 である前提で恒等として扱う。非ゼロ係数を弾く番人は
   本 Spec の責務であり、**近似で受理しない**（厳密な 0 比較。少しだけ歪んだ
   座標が静かに流れる事故を防ぐ）
2. **範囲外画素への非接触**（`deproject_region` が `region` の外を一切
   走査しない）。全画素の3次元展開をしない、という要件 1.4 の実装上の担保
3. **平均化後の欠測（NaN）の除外**（`deproject_region` が NaN 画素を点群
   から除く）。上流の `is_valid_depth` は生値に対する述語であり、平均化後の
   欠測（`valid_count == 0` → NaN）を弾くのは本モジュールの仕事である

**速度を理由に式をベクトル化して書き直さない。** `m1-prediction-validation`
のクロス Spec 契約テスト（同 Spec 要件 1.10 /
`tests/m1_validation/test_deprojection_contract.py`）が、本 Spec の経路と
`flying-object-tracking` の経路の**許容差なしの一致**を要求している。
`deproject_region` は `region` の画素を Python ループで1点ずつ
`deproject_pixel` へ渡す。ベクトル版が必要になった場合は上流の
`geometry.py` へ足す（本モジュールでは書かない）。
"""

from __future__ import annotations

import numpy as np
from sensing_foundation import CameraIntrinsics, deproject_pixel  # この2シンボルのみ

from world_frame_calibration.errors import (
    CalibrationConfigError,
    CalibrationFailure,
    FailureReason,
)
from world_frame_calibration.types import DepthImage, Intrinsics, PixelRegion

__all__ = [
    "deproject_region",
    "ensure_supported_distortion",
    "to_camera_intrinsics",
]


def ensure_supported_distortion(intr: Intrinsics) -> None:
    """歪み係数がすべて厳密に 0 でなければ `CalibrationFailure` を送出する。

    上流は歪み補正を持たず係数が 0 である前提で恒等として扱うため、非ゼロを
    弾く番人は本 Spec の責務である（design.md Deprojector「Validation」）。
    **近似では受理しない。** わずかでも非ゼロなら失敗させる。

    Raises:
        CalibrationFailure: `reason=FailureReason.UNSUPPORTED_DISTORTION`。
            `detail` にモデル名と係数を含める（要件 3.5）。
    """
    if any(coeff != 0.0 for coeff in intr.coeffs):
        raise CalibrationFailure(
            reason=FailureReason.UNSUPPORTED_DISTORTION,
            detail=(
                f"distortion model {intr.model!r} has non-zero coefficients "
                f"{intr.coeffs!r}; only exactly-zero distortion coefficients are "
                "supported (non-zero values are rejected, not approximated)"
            ),
            context={"model": intr.model, "coeffs": intr.coeffs},
        )


def to_camera_intrinsics(intr: Intrinsics) -> CameraIntrinsics:
    """自 Spec の `Intrinsics` を上流の `CameraIntrinsics` へ戻す。

    フィールド名はタスク 1.4 の設計により両者で完全に一致しているため、
    機械的な 1:1 転記である（design.md Deprojector 契約コメント）。

    未初期化（`fx_px` または `fy_px` が 0）な内部パラメータはここで
    `CalibrationConfigError` として拒否する。上流の `deproject_pixel` も
    同条件で `SensingConfigError` を送出するが、本モジュールが参照してよい
    上流シンボルは `deproject_pixel` と `CameraIntrinsics` の2つに限られ
    `SensingConfigError` を import できないため、上流へ渡す前に自前で検査する。

    Raises:
        CalibrationConfigError: `fx_px` または `fy_px` が 0 の場合。
    """
    if intr.fx_px == 0.0 or intr.fy_px == 0.0:
        raise CalibrationConfigError(
            "Intrinsics is uninitialized (fx_px / fy_px must be non-zero): "
            f"fx_px={intr.fx_px!r}, fy_px={intr.fy_px!r}"
        )
    return CameraIntrinsics(
        width_px=intr.width_px,
        height_px=intr.height_px,
        fx_px=intr.fx_px,
        fy_px=intr.fy_px,
        ppx_px=intr.ppx_px,
        ppy_px=intr.ppy_px,
        model=intr.model,
        coeffs=intr.coeffs,
    )


def deproject_region(
    image: DepthImage, region: PixelRegion
) -> tuple[np.ndarray, np.ndarray]:
    """`region` 内の有効画素だけを `(N, 3)` の点群と `(N, 2)` の画素座標として返す。

    1点ごとの逆投影は上流の `deproject_pixel` に委ね、本関数は範囲の切り出し
    と無効点（NaN）の除外だけを行う（design.md Deprojector 契約コメント）。

    Preconditions:
        `image.depth_mm` はすでに mm 単位である（換算は `upstream.py` が
        上流の `depth_raw_to_mm` で済ませている）。

    Postconditions:
        返り値は新しい配列であり、`image.depth_mm` を参照・変更しない。
        NaN 画素は除外される（要件 1.3）。`region` の外側は一切走査しない
        （要件 1.4）。

    Raises:
        CalibrationFailure: 歪み係数が非ゼロの場合
            （`FailureReason.UNSUPPORTED_DISTORTION`。要件 3.5）。
        CalibrationConfigError: 内部パラメータが未初期化の場合。
    """
    ensure_supported_distortion(image.intrinsics)
    camera_intrinsics = to_camera_intrinsics(image.intrinsics)

    points: list[tuple[float, float, float]] = []
    pixels: list[tuple[float, float]] = []
    depth_mm = image.depth_mm
    for v in range(region.y0_px, region.y1_px):
        for u in range(region.x0_px, region.x1_px):
            z_mm = depth_mm[v, u]
            if np.isnan(z_mm):
                continue
            point = deproject_pixel(camera_intrinsics, float(u), float(v), float(z_mm))
            points.append(point)
            pixels.append((float(u), float(v)))

    if points:
        points_mm = np.asarray(points, dtype=np.float64)
        pixels_px = np.asarray(pixels, dtype=np.float64)
    else:
        points_mm = np.zeros((0, 3), dtype=np.float64)
        pixels_px = np.zeros((0, 2), dtype=np.float64)
    return points_mm, pixels_px
