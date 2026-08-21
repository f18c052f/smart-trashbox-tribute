"""`sensing_foundation.geometry` のピンホール逆投影基本演算に対するテスト（タスク 1.8）。

観測可能な完了状態（tasks.md 1.8）を固定する:
- 主点画素 `(ppx_px, ppy_px)` の逆投影が `x=y=0` になること
  （画素中心規約に `+0.5` を足していないことを検出する回帰テスト）
- `is_valid_depth(0)` が `False`（無効値の判定）
- 既知の内部パラメータでの逆投影値が、手計算した期待値と一致すること
- `fx_px=0` / `fy_px=0` の内部パラメータが拒否されること

あわせて design.md「Geometry」の Responsibilities & Constraints が定める以下の点も固定する:
- `depth_raw_to_mm()` が `raw * depth_scale_mm` を唯一の適用箇所として行うこと
- `INVALID_DEPTH_RAW` が `0` であること
"""

from __future__ import annotations

import pytest

from sensing_foundation.errors import SensingConfigError
from sensing_foundation.geometry import (
    INVALID_DEPTH_RAW,
    depth_raw_to_mm,
    deproject_pixel,
    is_valid_depth,
)
from sensing_foundation.types import CameraIntrinsics


def _make_intrinsics(
    *,
    fx_px: float = 615.0,
    fy_px: float = 620.0,
    ppx_px: float = 320.0,
    ppy_px: float = 240.0,
) -> CameraIntrinsics:
    return CameraIntrinsics(
        width_px=640,
        height_px=480,
        fx_px=fx_px,
        fy_px=fy_px,
        ppx_px=ppx_px,
        ppy_px=ppy_px,
        model="brown_conrady",
        coeffs=(0.0, 0.0, 0.0, 0.0, 0.0),
    )


class TestInvalidDepthSentinel:
    def test_invalid_depth_raw_is_zero(self) -> None:
        assert INVALID_DEPTH_RAW == 0


class TestIsValidDepth:
    def test_zero_is_invalid(self) -> None:
        assert is_valid_depth(0) is False

    def test_invalid_depth_raw_constant_is_invalid(self) -> None:
        assert is_valid_depth(INVALID_DEPTH_RAW) is False

    def test_positive_raw_is_valid(self) -> None:
        assert is_valid_depth(1) is True
        assert is_valid_depth(65535) is True


class TestDepthRawToMm:
    def test_applies_scale_exactly_once(self) -> None:
        # raw * depth_scale_mm がこの関数の唯一の適用箇所である（design.md）。
        assert depth_raw_to_mm(1000, 0.1) == pytest.approx(100.0)

    def test_zero_raw_converts_to_zero_mm(self) -> None:
        # 変換自体は 0 を弾かない（無効判定は is_valid_depth の責務）。
        assert depth_raw_to_mm(0, 0.1) == pytest.approx(0.0)

    def test_typical_d435_scale(self) -> None:
        # D435 の典型的な depth_scale は 1 カウント = 1mm。
        assert depth_raw_to_mm(2500, 1.0) == pytest.approx(2500.0)


class TestDeprojectPixel:
    def test_principal_point_deprojects_to_x_zero_y_zero(self) -> None:
        # 画素中心規約の回帰テスト: +0.5 を誤って足すと主点画素が
        # x=0, y=0 からずれてしまう。ここでそれを検出する。
        intrinsics = _make_intrinsics(ppx_px=320.0, ppy_px=240.0)

        x_mm, y_mm, z_mm = deproject_pixel(
            intrinsics, u_px=320.0, v_px=240.0, z_mm=1000.0
        )

        assert x_mm == pytest.approx(0.0)
        assert y_mm == pytest.approx(0.0)
        assert z_mm == pytest.approx(1000.0)

    def test_known_intrinsics_produce_expected_value(self) -> None:
        # x_mm = (u_px - ppx_px) / fx_px * z_mm
        # y_mm = (v_px - ppy_px) / fy_px * z_mm
        intrinsics = _make_intrinsics(
            fx_px=615.0, fy_px=620.0, ppx_px=320.0, ppy_px=240.0
        )

        x_mm, y_mm, z_mm = deproject_pixel(
            intrinsics, u_px=420.0, v_px=180.0, z_mm=2000.0
        )

        expected_x_mm = (420.0 - 320.0) / 615.0 * 2000.0
        expected_y_mm = (180.0 - 240.0) / 620.0 * 2000.0
        assert x_mm == pytest.approx(expected_x_mm)
        assert y_mm == pytest.approx(expected_y_mm)
        assert z_mm == pytest.approx(2000.0)

    def test_z_mm_passes_through_unchanged(self) -> None:
        intrinsics = _make_intrinsics()

        _, _, z_mm = deproject_pixel(intrinsics, u_px=0.0, v_px=0.0, z_mm=1234.5)

        assert z_mm == pytest.approx(1234.5)

    def test_accepts_subpixel_float_coordinates(self) -> None:
        # 重心などの小数座標をそのまま渡せる（画素中心規約: +0.5 を足さない）。
        intrinsics = _make_intrinsics(ppx_px=320.0, ppy_px=240.0)

        x_mm, y_mm, _ = deproject_pixel(
            intrinsics, u_px=320.5, v_px=240.5, z_mm=1000.0
        )

        assert x_mm == pytest.approx(0.5 / 615.0 * 1000.0)
        assert y_mm == pytest.approx(0.5 / 620.0 * 1000.0)

    def test_zero_fx_is_rejected(self) -> None:
        intrinsics = _make_intrinsics(fx_px=0.0)

        with pytest.raises(SensingConfigError):
            deproject_pixel(intrinsics, u_px=320.0, v_px=240.0, z_mm=1000.0)

    def test_zero_fy_is_rejected(self) -> None:
        intrinsics = _make_intrinsics(fy_px=0.0)

        with pytest.raises(SensingConfigError):
            deproject_pixel(intrinsics, u_px=320.0, v_px=240.0, z_mm=1000.0)
