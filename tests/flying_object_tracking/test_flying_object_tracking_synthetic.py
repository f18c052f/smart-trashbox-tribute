"""合成入力生成器（`synthetic.py`）とテスト用フレーム供給（`fakes.py`）のテスト（タスク 1.5）。

要件 1.3（Depth 非破壊）/ 1.7（実機・SDK 無しで全経路が動く）/
11.1（実機なしで単体・結合テスト可能）/ 11.2（合成軌道からの往復検証）/
11.3（決定性）を、この2ファイルの組み合わせについて固定する。

**`synthetic_module` / `fakes_module` フィクスチャを使う理由**: `synthetic.py`
は `tests/sensing_foundation/synthetic.py` と同じベース名を持つため、素朴な
`from synthetic import ...`（bare import）は `sys.modules["synthetic"]` の
衝突を通じて他方の Spec のテストを壊しうる。回避策は `tests/
flying_object_tracking/conftest.py` の `synthetic_module` / `fakes_module`
フィクスチャに集約されている（同ファイルのモジュール docstring参照）。
本ファイルはそれらフィクスチャ経由でのみ `synthetic.py` / `fakes.py` の
中身にアクセスする。
"""

from __future__ import annotations

import numpy as np
import pytest
from sensing_foundation import CameraIntrinsics, StreamProfile, is_valid_depth

WIDTH_PX = 640
HEIGHT_PX = 480
DIAMETER_MM = 65.0
BACKGROUND_RAW = 4000


def _intrinsics() -> CameraIntrinsics:
    return CameraIntrinsics(
        width_px=WIDTH_PX,
        height_px=HEIGHT_PX,
        fx_px=600.0,
        fy_px=600.0,
        ppx_px=320.0,
        ppy_px=240.0,
        model="brown_conrady",
        coeffs=(0.0, 0.0, 0.0, 0.0, 0.0),
    )


def _profile() -> StreamProfile:
    return StreamProfile(
        width_px=WIDTH_PX,
        height_px=HEIGHT_PX,
        fps=30,
        depth_scale_mm=1.0,
        color_enabled=False,
        intrinsics=_intrinsics(),
    )


def _constant_velocity_trajectory(synthetic_module) -> list:
    """等速直線運動を模した既知の3D軌道（3点）。"""
    TrajectoryPoint = synthetic_module.TrajectoryPoint
    return [
        TrajectoryPoint(t_ms=0.0, x_mm=0.0, y_mm=0.0, z_mm=2000.0),
        TrajectoryPoint(t_ms=33.0, x_mm=100.0, y_mm=-50.0, z_mm=1800.0),
        TrajectoryPoint(t_ms=66.0, x_mm=-150.0, y_mm=80.0, z_mm=1500.0),
    ]


# ---------------------------------------------------------------------------
# 生成・反復
# ---------------------------------------------------------------------------


def test_synthesize_frames_iterates_via_synthetic_frame_source(synthetic_module, fakes_module) -> None:
    """既知の軌道から生成したフレーム列を SyntheticFrameSource 経由で反復できる。"""
    trajectory = _constant_velocity_trajectory(synthetic_module)
    frames = synthetic_module.synthesize_frames(
        trajectory, _profile(), DIAMETER_MM, background_raw=BACKGROUND_RAW
    )

    source = fakes_module.SyntheticFrameSource(frames)
    with source:
        collected = list(source.frames())

    assert len(collected) == len(trajectory)
    assert [f.t_capture_ms for f in collected] == [p.t_ms for p in trajectory]
    assert [f.index for f in collected] == [0, 1, 2]
    assert [f.seq for f in collected] == [0, 1, 2]


def test_synthesize_frames_is_deterministic(synthetic_module) -> None:
    """同一引数から2回生成した Depth 配列はビット単位で一致する（要件 11.3）。"""
    trajectory = _constant_velocity_trajectory(synthetic_module)
    frames_a = synthetic_module.synthesize_frames(
        trajectory, _profile(), DIAMETER_MM, background_raw=BACKGROUND_RAW
    )
    frames_b = synthetic_module.synthesize_frames(
        trajectory, _profile(), DIAMETER_MM, background_raw=BACKGROUND_RAW
    )

    for fa, fb in zip(frames_a, frames_b, strict=True):
        assert np.array_equal(fa.depth, fb.depth)


# ---------------------------------------------------------------------------
# 読み取り専用
# ---------------------------------------------------------------------------


def test_depth_array_is_read_only(synthetic_module, fakes_module) -> None:
    """CaptureFrame.depth は読み取り専用であり、in-place 変更は拒否される。"""
    trajectory = _constant_velocity_trajectory(synthetic_module)
    frames = synthetic_module.synthesize_frames(
        trajectory, _profile(), DIAMETER_MM, background_raw=BACKGROUND_RAW
    )

    source = fakes_module.SyntheticFrameSource(frames)
    with source:
        for frame in source.frames():
            assert frame.depth.flags.writeable is False
            with pytest.raises(ValueError):
                frame.depth[0, 0] = 123


# ---------------------------------------------------------------------------
# 静止背景
# ---------------------------------------------------------------------------


def test_static_background_fills_non_blob_pixels(synthetic_module) -> None:
    """塊の外側は指定した静止背景値で埋められる。"""
    point = synthetic_module.TrajectoryPoint(t_ms=0.0, x_mm=0.0, y_mm=0.0, z_mm=2000.0)
    depth = synthetic_module.make_depth_frame(_profile(), point, DIAMETER_MM, background_raw=BACKGROUND_RAW)

    # 塊から明らかに離れた四隅は背景値のはず。
    assert depth[0, 0] == BACKGROUND_RAW
    assert depth[0, WIDTH_PX - 1] == BACKGROUND_RAW
    assert depth[HEIGHT_PX - 1, 0] == BACKGROUND_RAW
    assert depth[HEIGHT_PX - 1, WIDTH_PX - 1] == BACKGROUND_RAW

    # 背景の占める画素数は塊よりずっと多い(塊が全画面を覆っていないことの確認)。
    background_count = int(np.count_nonzero(depth == BACKGROUND_RAW))
    assert background_count > (WIDTH_PX * HEIGHT_PX) // 2


# ---------------------------------------------------------------------------
# 無効画素の注入
# ---------------------------------------------------------------------------


def test_invalid_pixel_injection_reads_back_as_invalid(synthetic_module) -> None:
    """注入した無効画素は INVALID_DEPTH_RAW として読み出せ、is_valid_depth が False を返す。"""
    trajectory = _constant_velocity_trajectory(synthetic_module)
    invalid_xy = (10, 10)
    frames = synthetic_module.synthesize_frames(
        trajectory,
        _profile(),
        DIAMETER_MM,
        background_raw=BACKGROUND_RAW,
        invalid_pixels_by_index={0: [invalid_xy]},
    )

    injected_frame = frames[0]
    x_px, y_px = invalid_xy
    raw_value = int(injected_frame.depth[y_px, x_px])

    assert raw_value == 0
    assert is_valid_depth(raw_value) is False
    # 背景値そのものは有効値として扱われる(無効化していない画素との対比)。
    assert is_valid_depth(BACKGROUND_RAW) is True


# ---------------------------------------------------------------------------
# フレーム欠落(drop / gap)注入
# ---------------------------------------------------------------------------


def test_dropped_frame_is_observable_as_gap_before(synthetic_module) -> None:
    """軌道インデックスをドロップすると、後続フレームの gap_before に反映される。"""
    TrajectoryPoint = synthetic_module.TrajectoryPoint
    trajectory = [TrajectoryPoint(t_ms=float(i) * 33.0, x_mm=0.0, y_mm=0.0, z_mm=2000.0) for i in range(5)]
    frames = synthetic_module.synthesize_frames(
        trajectory,
        _profile(),
        DIAMETER_MM,
        background_raw=BACKGROUND_RAW,
        dropped_indices={2},
    )

    # 軌道インデックス 2 がドロップされたため、4フレームだけが生成される。
    assert len(frames) == 4
    seqs = [f.seq for f in frames]
    assert seqs == [0, 1, 3, 4]

    gap_before_values = [f.gap_before for f in frames]
    # ドロップの直前フレーム(seq=1)までは飛びなし、直後(seq=3)で飛びが観測される。
    assert gap_before_values == [0, 0, 1, 0]

    # index は実際に生成された枚数分、欠番なく 0 から増える。
    assert [f.index for f in frames] == [0, 1, 2, 3]


def test_dropped_frame_observable_via_frame_source(synthetic_module, fakes_module) -> None:
    """フレーム欠落は SyntheticFrameSource を経由した反復でも観測できる。"""
    TrajectoryPoint = synthetic_module.TrajectoryPoint
    trajectory = [TrajectoryPoint(t_ms=float(i) * 33.0, x_mm=0.0, y_mm=0.0, z_mm=2000.0) for i in range(4)]
    frames = synthetic_module.synthesize_frames(
        trajectory,
        _profile(),
        DIAMETER_MM,
        background_raw=BACKGROUND_RAW,
        dropped_indices={1},
    )
    source = fakes_module.SyntheticFrameSource(frames)
    with source:
        collected = list(source.frames())

    assert [f.gap_before for f in collected] == [0, 1, 0]
    assert source.stats.frames_missing == 1


# ---------------------------------------------------------------------------
# forward projection の幾何的正しさ(上流 deproject_pixel の厳密な逆演算)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("x_mm", "y_mm", "z_mm"),
    [
        (0.0, 0.0, 2000.0),
        (100.0, -50.0, 1800.0),
        (-150.0, 80.0, 1500.0),
    ],
)
def test_blob_centered_at_forward_projected_pixel(
    x_mm: float, y_mm: float, z_mm: float, synthetic_module
) -> None:
    """塊の重心画素が forward projection 式で予測した画素位置と一致する。"""
    intrinsics = _intrinsics()
    point = synthetic_module.TrajectoryPoint(t_ms=0.0, x_mm=x_mm, y_mm=y_mm, z_mm=z_mm)
    depth = synthetic_module.make_depth_frame(_profile(), point, DIAMETER_MM, background_raw=BACKGROUND_RAW)

    expected_u_px, expected_v_px = synthetic_module.project_to_pixel(intrinsics, x_mm, y_mm, z_mm)

    # 手計算でも同じ式であることを固定する(実装詳細への依存を避けるため
    # project_to_pixel を経由せず、ここでも独立に計算する)。
    manual_u_px = intrinsics.ppx_px + x_mm / z_mm * intrinsics.fx_px
    manual_v_px = intrinsics.ppy_px + y_mm / z_mm * intrinsics.fy_px
    assert expected_u_px == pytest.approx(manual_u_px)
    assert expected_v_px == pytest.approx(manual_v_px)

    foreground = np.argwhere(depth != BACKGROUND_RAW)
    assert foreground.size > 0
    centroid_y_px = float(np.mean(foreground[:, 0]))
    centroid_x_px = float(np.mean(foreground[:, 1]))

    assert centroid_x_px == pytest.approx(expected_u_px, abs=1.0)
    assert centroid_y_px == pytest.approx(expected_v_px, abs=1.0)


@pytest.mark.parametrize(
    ("x_mm", "y_mm", "z_mm"),
    [
        (0.0, 0.0, 2000.0),
        (100.0, -50.0, 1800.0),
        (-150.0, 80.0, 1500.0),
    ],
)
def test_blob_pixel_diameter_matches_expected_size(
    x_mm: float, y_mm: float, z_mm: float, synthetic_module
) -> None:
    """塊の画素直径が fx_px * diameter_mm / z_mm と丸め誤差の範囲で一致する。"""
    intrinsics = _intrinsics()
    point = synthetic_module.TrajectoryPoint(t_ms=0.0, x_mm=x_mm, y_mm=y_mm, z_mm=z_mm)
    depth = synthetic_module.make_depth_frame(_profile(), point, DIAMETER_MM, background_raw=BACKGROUND_RAW)

    expected_diam_px = synthetic_module.expected_diameter_px(intrinsics, DIAMETER_MM, z_mm)
    manual_expected_diam_px = intrinsics.fx_px * DIAMETER_MM / z_mm
    assert expected_diam_px == pytest.approx(manual_expected_diam_px)

    foreground = np.argwhere(depth != BACKGROUND_RAW)
    width_px = foreground[:, 1].max() - foreground[:, 1].min() + 1
    height_px = foreground[:, 0].max() - foreground[:, 0].min() + 1

    # 円盤の外接矩形の一辺 ≈ 画素直径。離散化による誤差を ±2px 許容する。
    assert width_px == pytest.approx(expected_diam_px, abs=2.0)
    assert height_px == pytest.approx(expected_diam_px, abs=2.0)


def test_make_depth_frame_requires_intrinsics(synthetic_module) -> None:
    """StreamProfile.intrinsics が None なら forward projection できず ValueError。"""
    profile = StreamProfile(
        width_px=WIDTH_PX,
        height_px=HEIGHT_PX,
        fps=30,
        depth_scale_mm=1.0,
        color_enabled=False,
        intrinsics=None,
    )
    point = synthetic_module.TrajectoryPoint(t_ms=0.0, x_mm=0.0, y_mm=0.0, z_mm=2000.0)

    with pytest.raises(ValueError):
        synthetic_module.make_depth_frame(profile, point, DIAMETER_MM)


def test_synthesize_frames_rejects_empty_trajectory(synthetic_module) -> None:
    with pytest.raises(ValueError):
        synthetic_module.synthesize_frames([], _profile(), DIAMETER_MM)


def test_synthetic_frame_source_rejects_empty_frames(fakes_module) -> None:
    with pytest.raises(ValueError):
        fakes_module.SyntheticFrameSource([])
