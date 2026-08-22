"""world_frame_calibration.deproject の範囲限定逆投影・歪み受理判定の検証
(tasks.md タスク 2.1 / 要件 1.3, 1.4, 3.4, 3.5, 3.8)。

design.md「L0-L2: 基盤・入力」節の Deprojector コンポーネント契約を固定する。
本テストが確認すること:

- `deproject_region` が範囲内の各有効画素について、上流
  `sensing_foundation.deproject_pixel` の返り値と**完全一致**する点群を
  返すこと（式を自前で再実装していないことの回帰、要件 3.4, 3.8）
- 範囲外の画素は結果に含まれないだけでなく、`deproject_pixel` へ一切渡され
  ない（呼び出し引数そのものを monkeypatch で捕捉して検証する。要件 1.4）
- 平均化後に欠測（NaN）となった画素が点群から除外されること（要件 1.3）
- レンズ歪み係数が厳密に非ゼロの場合、近似で受理せず
  `CalibrationFailure(FailureReason.UNSUPPORTED_DISTORTION)` を、モデル名と
  係数を添えて送出すること（要件 3.5）
- 焦点距離が 0 の未初期化な内部パラメータを `CalibrationConfigError` として
  拒否すること
- `to_camera_intrinsics` が `Intrinsics` → `CameraIntrinsics` の機械的な
  1:1 フィールド写像であること（tasks.md タスク 2.1）
- 本モジュールが `sensing_foundation` から参照するシンボルが
  `deproject_pixel` と `CameraIntrinsics` の2つだけであること（要件 3.8 /
  8.2 の簡易版。網羅的な境界検査はタスク 7.3 の `test_boundaries.py` が持つ）

ファイル名について: `tests/**` に `__init__.py` が無いため、裸の
`test_deproject.py` は使わずプレフィックス付きの
`test_world_frame_calibration_deproject.py` とする（既存タスクの命名規約。
tasks.md 実装ノート・タスク1.6参照）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pytest
from sensing_foundation import CameraIntrinsics, deproject_pixel

from world_frame_calibration import deproject
from world_frame_calibration.errors import (
    CalibrationConfigError,
    CalibrationFailure,
    FailureReason,
)
from world_frame_calibration.types import DepthImage, Intrinsics, PixelRegion, StreamSignature

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPROJECT_SRC = REPO_ROOT / "src" / "world_frame_calibration" / "deproject.py"


def _intrinsics(**overrides: object) -> Intrinsics:
    kwargs: dict[str, object] = dict(
        width_px=640,
        height_px=480,
        fx_px=615.0,
        fy_px=615.0,
        ppx_px=320.5,
        ppy_px=240.5,
        model="brown_conrady",
        coeffs=(0.0, 0.0, 0.0, 0.0, 0.0),
    )
    kwargs.update(overrides)
    return Intrinsics(**kwargs)  # type: ignore[arg-type]


def _signature() -> StreamSignature:
    return StreamSignature(
        width_px=640,
        height_px=480,
        fps=30,
        depth_scale_mm=1.0,
        color_enabled=False,
    )


def _depth_image(depth_mm: np.ndarray, intrinsics: Intrinsics | None = None) -> DepthImage:
    intr = intrinsics if intrinsics is not None else _intrinsics()
    valid_count = np.where(np.isnan(depth_mm), 0, 1).astype(np.int32)
    return DepthImage(
        depth_mm=depth_mm,
        valid_count=valid_count,
        frames_used=1,
        intrinsics=intr,
        signature=_signature(),
        source_kind="simulated",
    )


def _to_upstream_camera_intrinsics(intr: Intrinsics) -> CameraIntrinsics:
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


def test_deproject_region_matches_upstream_deproject_pixel_exactly() -> None:
    """範囲内の各点が上流 deproject_pixel の返り値と完全一致する（要件 3.4, 3.8）。"""
    depth = np.full((10, 10), np.nan, dtype=np.float64)
    depth[2:6, 2:6] = 1000.0
    depth[3, 3] = 1500.0
    intr = _intrinsics()
    image = _depth_image(depth, intr)
    region = PixelRegion(x0_px=2, y0_px=2, x1_px=6, y1_px=6)

    points, pixels = deproject.deproject_region(image, region)

    camera_intrinsics = _to_upstream_camera_intrinsics(intr)
    expected_points = []
    expected_pixels = []
    for v in range(2, 6):
        for u in range(2, 6):
            z = depth[v, u]
            expected_points.append(deproject_pixel(camera_intrinsics, float(u), float(v), float(z)))
            expected_pixels.append((float(u), float(v)))

    assert points.shape == (16, 3)
    assert pixels.shape == (16, 2)
    np.testing.assert_array_equal(points, np.asarray(expected_points, dtype=np.float64))
    np.testing.assert_array_equal(pixels, np.asarray(expected_pixels, dtype=np.float64))


def test_deproject_region_excludes_out_of_range_pixels_and_never_touches_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """範囲外の画素は結果から除外されるだけでなく deproject_pixel へ一切渡されない
    （要件 1.4: 全画素の3次元展開をしないことの実装上の担保）。
    """
    depth = np.full((10, 10), 1000.0, dtype=np.float64)
    image = _depth_image(depth)
    region = PixelRegion(x0_px=3, y0_px=3, x1_px=5, y1_px=5)

    calls: list[tuple[float, float]] = []
    real = deproject.deproject_pixel

    def _spy(intrinsics: CameraIntrinsics, u_px: float, v_px: float, z_mm: float):
        calls.append((u_px, v_px))
        return real(intrinsics, u_px, v_px, z_mm)

    monkeypatch.setattr(deproject, "deproject_pixel", _spy)

    points, _pixels = deproject.deproject_region(image, region)

    assert len(calls) == 4  # 2x2 region: (3,3) (4,3) (3,4) (4,4)
    for u, v in calls:
        assert 3 <= u < 5
        assert 3 <= v < 5
    assert points.shape == (4, 3)


def test_deproject_region_excludes_nan_pixels_within_range() -> None:
    """平均化で寄与ゼロになった NaN 画素は点群から除外される（要件 1.3）。

    上流の無効判定は生値に対する述語であり、平均化後の欠測は本 Spec の
    責務である（design.md Deprojector「Validation」）。
    """
    depth = np.full((6, 6), 1000.0, dtype=np.float64)
    depth[2, 2] = np.nan  # u=2, v=2
    depth[3, 4] = np.nan  # u=4, v=3
    image = _depth_image(depth)
    region = PixelRegion(x0_px=0, y0_px=0, x1_px=6, y1_px=6)

    points, pixels = deproject.deproject_region(image, region)

    assert points.shape == (34, 3)  # 36 - 2 個の NaN
    assert not np.any(np.isnan(points))
    for u, v in pixels:
        assert not (u == 2.0 and v == 2.0)
        assert not (u == 4.0 and v == 3.0)


def test_non_zero_distortion_coefficients_are_rejected_not_approximated() -> None:
    """歪み係数が非ゼロなら近似せず CalibrationFailure(UNSUPPORTED_DISTORTION) を
    モデル名と係数を添えて送出する（要件 3.5）。
    """
    depth = np.full((4, 4), 1000.0, dtype=np.float64)
    intr = _intrinsics(model="brown_conrady", coeffs=(1e-9, 0.0, 0.0, 0.0, 0.0))
    image = _depth_image(depth, intr)
    region = PixelRegion(x0_px=0, y0_px=0, x1_px=4, y1_px=4)

    with pytest.raises(CalibrationFailure) as exc_info:
        deproject.deproject_region(image, region)

    assert exc_info.value.reason == FailureReason.UNSUPPORTED_DISTORTION
    message = str(exc_info.value)
    assert "brown_conrady" in message
    assert str(intr.coeffs) in message


def test_zero_distortion_coefficients_are_accepted() -> None:
    """歪み係数が厳密に 0 の場合のみ受理される（要件 3.4, 3.5 の正常系）。"""
    depth = np.full((2, 2), 1000.0, dtype=np.float64)
    intr = _intrinsics(coeffs=(0.0, 0.0, 0.0, 0.0, 0.0))
    image = _depth_image(depth, intr)
    region = PixelRegion(x0_px=0, y0_px=0, x1_px=2, y1_px=2)

    points, _pixels = deproject.deproject_region(image, region)
    assert points.shape == (4, 3)


def test_uninitialized_intrinsics_are_rejected_as_config_error() -> None:
    """焦点距離 0 の未初期化な内部パラメータは設定誤りとして CalibrationConfigError
    で拒否する（sensing_foundation.SensingConfigError をそのまま bubble させない）。
    """
    depth = np.full((2, 2), 1000.0, dtype=np.float64)
    intr = _intrinsics(fx_px=0.0)
    image = _depth_image(depth, intr)
    region = PixelRegion(x0_px=0, y0_px=0, x1_px=2, y1_px=2)

    with pytest.raises(CalibrationConfigError):
        deproject.deproject_region(image, region)


def test_ensure_supported_distortion_rejects_near_zero_not_just_exact_nonzero() -> None:
    """近似的に 0 (1e-12) であっても厳密に 0 でなければ拒否する（近似で受理しない）。"""
    intr = _intrinsics(coeffs=(1e-12, 0.0, 0.0, 0.0, 0.0))

    with pytest.raises(CalibrationFailure) as exc_info:
        deproject.ensure_supported_distortion(intr)

    assert exc_info.value.reason == FailureReason.UNSUPPORTED_DISTORTION


def test_ensure_supported_distortion_accepts_all_zero_coefficients() -> None:
    """歪み係数が全て 0 なら例外を送出しない。"""
    intr = _intrinsics(coeffs=(0.0, 0.0, 0.0, 0.0, 0.0))
    deproject.ensure_supported_distortion(intr)  # 例外を送出しないことそのものが確認事項


def test_to_camera_intrinsics_is_a_mechanical_field_mapping() -> None:
    """to_camera_intrinsics は Intrinsics -> CameraIntrinsics の機械的な
    1:1 フィールド写像である（フィールド名は同一。tasks.md タスク 2.1）。
    """
    intr = _intrinsics()

    result = deproject.to_camera_intrinsics(intr)

    assert isinstance(result, CameraIntrinsics)
    assert result.width_px == intr.width_px
    assert result.height_px == intr.height_px
    assert result.fx_px == intr.fx_px
    assert result.fy_px == intr.fy_px
    assert result.ppx_px == intr.ppx_px
    assert result.ppy_px == intr.ppy_px
    assert result.model == intr.model
    assert result.coeffs == intr.coeffs


def test_to_camera_intrinsics_rejects_zero_focal_length_as_config_error() -> None:
    """to_camera_intrinsics 単体でも未初期化な焦点距離を設定誤りとして拒否する。"""
    intr = _intrinsics(fx_px=0.0)

    with pytest.raises(CalibrationConfigError):
        deproject.to_camera_intrinsics(intr)


def test_deproject_module_imports_exactly_two_sensing_foundation_symbols() -> None:
    """deproject.py が sensing_foundation から参照してよいのは deproject_pixel と
    CameraIntrinsics の2シンボルだけである（要件 3.8。タスク 7.3 の網羅的境界
    テストの簡易プレビュー。本テストはソースをテキストとして静的解析するのみで
    deproject モジュールを import しない実装との重複を避ける）。
    """
    tree = ast.parse(DEPROJECT_SRC.read_text(encoding="utf-8"))
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "sensing_foundation":
            for alias in node.names:
                imported_names.add(alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] != "sensing_foundation", (
                    "deproject.py は 'import sensing_foundation' 形式を使ってはならない"
                    "（from 節で2シンボルだけを取り込む）"
                )

    assert imported_names == {"deproject_pixel", "CameraIntrinsics"}
