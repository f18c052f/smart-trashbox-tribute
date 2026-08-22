"""`tests/world_frame_calibration/synthetic.py`（既知姿勢からの合成 Depth 生成器）
の検証（tasks.md タスク 1.6 / 要件 9.1, 9.5）。

本テストが確認すること（tasks.md タスク 1.6「観測可能な完了状態」）:

- 同じ引数で2回生成した Depth 画像が完全に一致すること（決定性。要件 9.4 と
  同じ性質をこのヘルパ自身にも要求する）
- 床平面（World z = 0）だけが見える姿勢で、床の Depth 値がテスト内で独立に
  computed した三角関数・ベクトル計算と数値誤差の範囲で一致すること
  （「実行できるだけ」ではなく幾何的に正しいことを固定する）
- 直方体マーカーが Depth 値を変化させ、その変化量が既知の箱の高さと
  独立計算に一致すること
- 指定した標準偏差のノイズが Depth 値に反映されること
- 指定した割合の無効画素（NaN）が生成されること
- 外れ値平面（床以外の平面）が最近傍として採用されること
- 縮退条件（浅い入射角・マーカー未配置）を実機なしで再現できること

このテストファイルは `tests/world_frame_calibration/synthetic.py` が
まだ存在しない時点では `ModuleNotFoundError` で失敗する（RED）。
"""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import numpy as np
import pytest

from world_frame_calibration.types import Intrinsics, StreamSignature

# `tests/sensing_foundation/synthetic.py` already claims the bare module name
# `synthetic` (imported elsewhere as `from synthetic import ...`, tasks.md
# タスク1.7 of `sensing-foundation`). Because `tests/**` has no `__init__.py`
# anywhere, pytest's default "prepend" import mode caches modules by their
# bare top-level name in `sys.modules`: whichever `synthetic.py` gets
# imported first in a given pytest session "wins" the name, and a plain
# `from synthetic import ...` here would silently resolve to the wrong
# module (or fail with a confusing `ImportError`) whenever both test suites
# run together (i.e. always, in the full suite). Load this package's
# `synthetic.py` by explicit file path instead, under a private,
# collision-proof module name, so this test is independent of collection
# order. Any future test in this directory that needs `synthetic.py`
# (tasks 2.x / 3.2 / 7.x) must do the same -- see the matching warning in
# `synthetic.py`'s module docstring.
_SYNTHETIC_PATH = Path(__file__).resolve().parent / "synthetic.py"
_spec = importlib.util.spec_from_file_location(
    "world_frame_calibration._test_synthetic", _SYNTHETIC_PATH
)
assert _spec is not None and _spec.loader is not None
_synthetic = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _synthetic
_spec.loader.exec_module(_synthetic)

CameraPose = _synthetic.CameraPose
MarkerBox = _synthetic.MarkerBox
OutlierPlane = _synthetic.OutlierPlane
camera_pose_looking_at_floor = _synthetic.camera_pose_looking_at_floor
generate_synthetic_depth = _synthetic.generate_synthetic_depth


def _make_intrinsics(**overrides: object) -> Intrinsics:
    kwargs: dict[str, object] = dict(
        width_px=64,
        height_px=48,
        fx_px=50.0,
        fy_px=50.0,
        ppx_px=32.0,
        ppy_px=24.0,
        model="none",
        coeffs=(0.0, 0.0, 0.0, 0.0, 0.0),
    )
    kwargs.update(overrides)
    return Intrinsics(**kwargs)  # type: ignore[arg-type]


def _make_signature(intrinsics: Intrinsics) -> StreamSignature:
    return StreamSignature(
        width_px=intrinsics.width_px,
        height_px=intrinsics.height_px,
        fps=30,
        depth_scale_mm=1.0,
        color_enabled=False,
    )


# ---------------------------------------------------------------------------
# 決定性
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_arguments_produce_bit_identical_depth(self) -> None:
        intrinsics = _make_intrinsics()
        pose = camera_pose_looking_at_floor(
            position_world_mm=(0.0, 0.0, 2000.0), yaw_deg=15.0, pitch_deg=60.0
        )
        markers = (
            MarkerBox(
                label="origin",
                center_xy_world_mm=(0.0, 300.0),
                size_xy_mm=(60.0, 60.0),
                height_mm=80.0,
            ),
        )

        frame_a = generate_synthetic_depth(
            pose,
            intrinsics,
            markers=markers,
            noise_std_mm=5.0,
            invalid_fraction=0.05,
            rng_seed=42,
        )
        frame_b = generate_synthetic_depth(
            pose,
            intrinsics,
            markers=markers,
            noise_std_mm=5.0,
            invalid_fraction=0.05,
            rng_seed=42,
        )

        np.testing.assert_array_equal(
            frame_a.depth.depth_mm, frame_b.depth.depth_mm, strict=True
        )
        np.testing.assert_array_equal(
            frame_a.depth.valid_count, frame_b.depth.valid_count, strict=True
        )

    def test_different_seed_changes_noisy_output(self) -> None:
        intrinsics = _make_intrinsics()
        pose = camera_pose_looking_at_floor(
            position_world_mm=(0.0, 0.0, 2000.0), yaw_deg=0.0, pitch_deg=90.0
        )

        frame_a = generate_synthetic_depth(
            pose, intrinsics, noise_std_mm=10.0, rng_seed=1
        )
        frame_b = generate_synthetic_depth(
            pose, intrinsics, noise_std_mm=10.0, rng_seed=2
        )

        assert not np.array_equal(
            frame_a.depth.depth_mm, frame_b.depth.depth_mm, equal_nan=True
        )


# ---------------------------------------------------------------------------
# 幾何的な正しさ（独立計算との突き合わせ）
# ---------------------------------------------------------------------------


class TestFloorGeometry:
    def test_overhead_camera_reports_constant_height_on_flat_floor(self) -> None:
        """カメラが床を真上から見下ろす姿勢（pitch=90°）では、画像面が床面と
        平行になるため、どの画素の depth 値もカメラ高さに厳密に一致するはず
        である。これは本テストが独立に導いた幾何的事実であり、実装の詳細に
        依存しない（三角関数を使わない自明ケースだが、これが崩れれば投影の
        符号や軸の対応関係が壊れていることを検出できる）。
        """
        height_mm = 1800.0
        intrinsics = _make_intrinsics()
        pose = camera_pose_looking_at_floor(
            position_world_mm=(0.0, 0.0, height_mm), yaw_deg=0.0, pitch_deg=90.0
        )

        frame = generate_synthetic_depth(pose, intrinsics, rng_seed=0)
        depth = frame.depth.depth_mm

        assert np.all(np.isfinite(depth))
        np.testing.assert_allclose(depth, height_mm, rtol=0, atol=1e-6)

    def test_tilted_camera_matches_independent_ray_plane_intersection(self) -> None:
        """傾けたカメラ（pitch=45°）について、中心画素以外の画素の depth 値を
        テスト内で独立に構築したレイ・平面交差計算（NumPy の基本演算のみ）で
        算出し、生成器の出力と突き合わせる。生成器の内部関数を一切呼ばない。
        """
        height_mm = 2000.0
        pitch_deg = 45.0
        yaw_deg = 20.0
        intrinsics = _make_intrinsics()
        pose = camera_pose_looking_at_floor(
            position_world_mm=(100.0, -50.0, height_mm),
            yaw_deg=yaw_deg,
            pitch_deg=pitch_deg,
        )

        frame = generate_synthetic_depth(pose, intrinsics, rng_seed=0)
        depth = frame.depth.depth_mm

        rotation = np.array(pose.rotation_cam_to_world, dtype=np.float64)
        position = np.array(pose.position_world_mm, dtype=np.float64)

        # 独立計算: このテスト自身が pinhole の逆投影方向と
        # レイ・平面（z=0）交差を最初から組み立てる。
        for v_px, u_px in [(0, 0), (47, 63), (24, 32), (10, 55)]:
            d_cam = np.array(
                [
                    (u_px - intrinsics.ppx_px) / intrinsics.fx_px,
                    (v_px - intrinsics.ppy_px) / intrinsics.fy_px,
                    1.0,
                ]
            )
            d_world = rotation @ d_cam
            assert d_world[2] != 0.0, "テストの姿勢選択が退化している"
            t_expected = -position[2] / d_world[2]
            assert t_expected > 0.0

            assert math.isfinite(depth[v_px, u_px])
            assert depth[v_px, u_px] == pytest.approx(t_expected, rel=1e-9, abs=1e-6)

    def test_incidence_angle_equals_pitch_by_construction(self) -> None:
        """`camera_pose_looking_at_floor` の pitch_deg は、光軸（forward）と
        床面（法線 = World +Z）のなす入射角そのものであるように構築している
        （モジュール docstring 参照）。この関係をテストが独立に検証する。
        """
        for pitch_deg in (5.0, 30.0, 60.0, 89.0):
            pose = camera_pose_looking_at_floor(
                position_world_mm=(0.0, 0.0, 2000.0), yaw_deg=13.0, pitch_deg=pitch_deg
            )
            rotation = np.array(pose.rotation_cam_to_world, dtype=np.float64)
            forward_world = rotation @ np.array([0.0, 0.0, 1.0])
            floor_normal = np.array([0.0, 0.0, 1.0])
            incidence_rad = math.asin(abs(float(np.dot(forward_world, floor_normal))))
            assert math.degrees(incidence_rad) == pytest.approx(pitch_deg, abs=1e-6)


# ---------------------------------------------------------------------------
# マーカー箱
# ---------------------------------------------------------------------------


class TestMarkerBox:
    def test_marker_box_reduces_depth_by_its_height_under_overhead_camera(
        self,
    ) -> None:
        height_mm = 1800.0
        box_height_mm = 120.0
        intrinsics = _make_intrinsics()
        pose = camera_pose_looking_at_floor(
            position_world_mm=(0.0, 0.0, height_mm), yaw_deg=0.0, pitch_deg=90.0
        )
        marker = MarkerBox(
            label="origin",
            center_xy_world_mm=(0.0, 0.0),
            size_xy_mm=(200.0, 200.0),
            height_mm=box_height_mm,
        )

        frame = generate_synthetic_depth(pose, intrinsics, markers=(marker,), rng_seed=0)
        depth = frame.depth.depth_mm

        center_v, center_u = intrinsics.height_px // 2, intrinsics.width_px // 2
        # 中心画素はマーカーの真上にあるため、床ではなく箱の天面を見込む。
        assert depth[center_v, center_u] == pytest.approx(
            height_mm - box_height_mm, rel=0, abs=1e-6
        )
        # 遠く離れた隅の画素はマーカーの範囲外であり、床のままのはず。
        assert depth[0, 0] == pytest.approx(height_mm, rel=0, abs=1e-6)

    def test_floating_marker_box_does_not_touch_floor(self) -> None:
        """`base_z_world_mm > 0` の箱は「浮遊物」として扱える
        （tasks.md タスク 1.6: 外れ値として床以外の平面や浮遊物を混ぜられる
        こと）。床から浮いた箱の底面より奥（床側）の depth は観測されない。
        """
        height_mm = 1800.0
        intrinsics = _make_intrinsics()
        pose = camera_pose_looking_at_floor(
            position_world_mm=(0.0, 0.0, height_mm), yaw_deg=0.0, pitch_deg=90.0
        )
        floating = MarkerBox(
            label="floating",
            center_xy_world_mm=(0.0, 0.0),
            size_xy_mm=(200.0, 200.0),
            height_mm=100.0,
            base_z_world_mm=500.0,  # 床から 500mm 浮いている
        )

        frame = generate_synthetic_depth(
            pose, intrinsics, markers=(floating,), rng_seed=0
        )
        depth = frame.depth.depth_mm
        center_v, center_u = intrinsics.height_px // 2, intrinsics.width_px // 2

        # 天面（base + height = 600mm 床から）を見込む。
        expected = height_mm - (floating.base_z_world_mm + floating.height_mm)
        assert depth[center_v, center_u] == pytest.approx(expected, rel=0, abs=1e-6)


# ---------------------------------------------------------------------------
# 外れ値平面
# ---------------------------------------------------------------------------


class TestOutlierPlane:
    def test_outlier_plane_nearer_than_floor_is_selected(self) -> None:
        height_mm = 2000.0
        intrinsics = _make_intrinsics()
        pose = camera_pose_looking_at_floor(
            position_world_mm=(0.0, 0.0, height_mm), yaw_deg=0.0, pitch_deg=90.0
        )
        # カメラの真下 500mm に、床と平行などこまでも広い平面を置く
        # （壁ではないが「床以外の平面」として最近傍判定を確認するには十分）。
        wall_z = height_mm - 500.0
        outlier = OutlierPlane(
            point_world_mm=(0.0, 0.0, wall_z), normal_world=(0.0, 0.0, 1.0)
        )

        frame = generate_synthetic_depth(
            pose, intrinsics, outlier_planes=(outlier,), rng_seed=0
        )
        depth = frame.depth.depth_mm

        np.testing.assert_allclose(depth, 500.0, atol=1e-6)


# ---------------------------------------------------------------------------
# ノイズ・無効画素
# ---------------------------------------------------------------------------


class TestNoiseAndInvalid:
    def test_noise_std_dev_is_reflected_in_output(self) -> None:
        intrinsics = _make_intrinsics(width_px=200, height_px=200)
        pose = camera_pose_looking_at_floor(
            position_world_mm=(0.0, 0.0, 2000.0), yaw_deg=0.0, pitch_deg=90.0
        )
        noise_std_mm = 8.0

        frame = generate_synthetic_depth(
            pose, intrinsics, noise_std_mm=noise_std_mm, rng_seed=7
        )
        depth = frame.depth.depth_mm
        observed_std = float(np.std(depth))

        assert observed_std == pytest.approx(noise_std_mm, rel=0.15)

    def test_zero_noise_gives_exact_depth(self) -> None:
        intrinsics = _make_intrinsics()
        pose = camera_pose_looking_at_floor(
            position_world_mm=(0.0, 0.0, 2000.0), yaw_deg=0.0, pitch_deg=90.0
        )

        frame = generate_synthetic_depth(pose, intrinsics, noise_std_mm=0.0, rng_seed=0)
        np.testing.assert_allclose(frame.depth.depth_mm, 2000.0, atol=0.0)

    def test_invalid_fraction_produces_approximately_that_fraction_of_nan(
        self,
    ) -> None:
        intrinsics = _make_intrinsics(width_px=200, height_px=200)
        pose = camera_pose_looking_at_floor(
            position_world_mm=(0.0, 0.0, 2000.0), yaw_deg=0.0, pitch_deg=90.0
        )
        invalid_fraction = 0.3

        frame = generate_synthetic_depth(
            pose, intrinsics, invalid_fraction=invalid_fraction, rng_seed=3
        )
        depth = frame.depth.depth_mm
        observed_fraction = float(np.mean(np.isnan(depth)))

        assert observed_fraction == pytest.approx(invalid_fraction, abs=0.05)

        valid_count = frame.depth.valid_count
        assert np.array_equal(valid_count == 0, np.isnan(depth))

    def test_invalid_fraction_zero_yields_no_nan_when_everything_hits_floor(
        self,
    ) -> None:
        intrinsics = _make_intrinsics()
        pose = camera_pose_looking_at_floor(
            position_world_mm=(0.0, 0.0, 2000.0), yaw_deg=0.0, pitch_deg=90.0
        )

        frame = generate_synthetic_depth(pose, intrinsics, invalid_fraction=0.0, rng_seed=0)
        assert not np.any(np.isnan(frame.depth.depth_mm))
        assert np.all(frame.depth.valid_count == 1)


# ---------------------------------------------------------------------------
# 縮退条件の再現（要件 9.5）
# ---------------------------------------------------------------------------


class TestDegenerateConditions:
    def test_shallow_incidence_angle_leaves_many_pixels_out_of_range(self) -> None:
        """入射角が浅い（pitch が小さい）ほど、床までの depth 値
        （t = -O_z / D_z）が急激に大きくなり、多くの画素が現実的な
        センサー最大測距距離を超えて無効になる。これは
        `min_incidence_angle_deg` の縮退分岐（要件 5.4）をハードウェアなしで
        再現するために、このヘルパが持つべき性質である。
        """
        intrinsics = _make_intrinsics(
            width_px=200, height_px=200, ppx_px=100.0, ppy_px=100.0, fx_px=150.0,
            fy_px=150.0,
        )
        pose = camera_pose_looking_at_floor(
            position_world_mm=(0.0, 0.0, 500.0), yaw_deg=0.0, pitch_deg=2.0
        )

        frame = generate_synthetic_depth(
            pose, intrinsics, max_range_mm=4000.0, rng_seed=0
        )
        depth = frame.depth.depth_mm

        assert np.mean(np.isnan(depth)) > 0.5

    def test_marker_placed_outside_camera_frustum_is_never_observed(self) -> None:
        """マーカーがカメラの視野外に置かれた場合、どの画素もそのマーカーの
        天面を観測しない（＝箱の高さぶん depth が短くなる画素が一切現れない）。
        これは「マーカー未配置」縮退分岐（要件 5.2）の再現手段である。
        """
        height_mm = 2000.0
        intrinsics = _make_intrinsics()
        pose = camera_pose_looking_at_floor(
            position_world_mm=(0.0, 0.0, height_mm), yaw_deg=0.0, pitch_deg=90.0
        )
        far_away_marker = MarkerBox(
            label="unreachable",
            center_xy_world_mm=(50_000.0, 50_000.0),
            size_xy_mm=(60.0, 60.0),
            height_mm=80.0,
        )

        frame = generate_synthetic_depth(
            pose, intrinsics, markers=(far_away_marker,), rng_seed=0
        )
        depth = frame.depth.depth_mm

        np.testing.assert_allclose(depth, height_mm, atol=1e-6)

    def test_short_baseline_markers_are_expressible(self) -> None:
        """基線長（原点マーカーと方向マーカーの距離）が短い配置も、単に2つの
        `MarkerBox` を近接させるだけで表現できる（縮退判定自体は上位の
        `frame` モジュールの責務であり、ここでは配置が可能であることのみを
        確認する）。
        """
        origin = MarkerBox(
            label="origin",
            center_xy_world_mm=(0.0, 0.0),
            size_xy_mm=(40.0, 40.0),
            height_mm=80.0,
        )
        x_axis = MarkerBox(
            label="x_axis",
            center_xy_world_mm=(50.0, 0.0),  # 基線長 50mm: 極端に短い
            size_xy_mm=(40.0, 40.0),
            height_mm=80.0,
        )
        baseline_mm = math.dist(origin.center_xy_world_mm, x_axis.center_xy_world_mm)
        assert baseline_mm == pytest.approx(50.0)

        intrinsics = _make_intrinsics()
        pose = camera_pose_looking_at_floor(
            position_world_mm=(0.0, 0.0, 2000.0), yaw_deg=0.0, pitch_deg=90.0
        )
        # 単に例外なく生成できることを確認する（縮退の判定自体は本ヘルパの
        # 責務ではない）。
        frame = generate_synthetic_depth(
            pose, intrinsics, markers=(origin, x_axis), rng_seed=0
        )
        assert frame.depth.depth_mm.shape == (intrinsics.height_px, intrinsics.width_px)


# ---------------------------------------------------------------------------
# 返り値の形（正解の変換を同時に返す）
# ---------------------------------------------------------------------------


class TestReturnsGroundTruth:
    def test_returns_the_camera_pose_used_for_generation(self) -> None:
        intrinsics = _make_intrinsics()
        pose = camera_pose_looking_at_floor(
            position_world_mm=(10.0, 20.0, 1500.0), yaw_deg=5.0, pitch_deg=70.0
        )

        frame = generate_synthetic_depth(pose, intrinsics, rng_seed=0)

        assert isinstance(frame.camera_to_world, CameraPose)
        assert frame.camera_to_world.position_world_mm == pose.position_world_mm
        assert frame.camera_to_world.rotation_cam_to_world == pose.rotation_cam_to_world

    def test_depth_image_carries_supplied_intrinsics_and_signature(self) -> None:
        intrinsics = _make_intrinsics()
        signature = _make_signature(intrinsics)
        pose = camera_pose_looking_at_floor(
            position_world_mm=(0.0, 0.0, 2000.0), yaw_deg=0.0, pitch_deg=90.0
        )

        frame = generate_synthetic_depth(
            pose, intrinsics, stream_signature=signature, rng_seed=0
        )

        assert frame.depth.intrinsics is intrinsics
        assert frame.depth.signature == signature
        assert frame.depth.source_kind == "simulated"
        assert frame.depth.frames_used == 1
