"""`projection.py`（タスク 3.1）の検証。

design.md「L4-L5: 検出 → PointEstimator」とアルゴリズム上の約束7段階、
要件 2.5, 2.6, 3.3, 5.1〜5.8, 10.1, 10.4, 10.6 が定める、候補領域からの
カメラ座標系代表3D点の算出について、次を固定する。

- 既知の内部パラメータ・既知の距離に対する逆投影が解析解と一致する
  （タスクの観測可能な完了状態そのもの）
- 上流の実関数（`depth_raw_to_mm` / `deproject_pixel`）へ実際に委譲して
  いること（スパイで確認）。`is_valid_depth` はネイティブな配列演算
  （`raw != INVALID_DEPTH_RAW`）で表現されるため、有効性判定の意味論を
  境界ケースで確認する
- 有効画素不足 → `PointFailure(INSUFFICIENT_VALID_PIXELS)`
- ばらつき過大 → `PointFailure(DEPTH_SPREAD_TOO_LARGE)`
- 距離帯外 → `PointFailure(OUT_OF_DEPTH_BAND)`
- 寸法不一致 → `CandidateRejection(SIZE_MISMATCH)`（本タスクの設計判断。
  `projection.py` モジュール docstring 参照）
- 無効画素が有効画素数・代表値の両方から除外される
- 候補の外接矩形の外側を一切読まない
- `intrinsics is None` → `IntrinsicsUnavailableError`
- `t_ms` が `frame.t_capture_ms` をそのまま引き継ぐ
- `intrinsics_source` フィールドが埋まる
- トリム平均による代表値の算出（手計算した期待値と一致）
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from sensing_foundation import (
    CameraIntrinsics,
    CaptureFrame,
    SourceKind,
    StreamProfile,
    TimestampDomain,
)

import flying_object_tracking.projection as projection_module
from flying_object_tracking.config import ObjectModelConfig, ProjectionConfig
from flying_object_tracking.errors import IntrinsicsUnavailableError
from flying_object_tracking.projection import PointEstimator
from flying_object_tracking.types import (
    Candidate,
    CameraPoint,
    CandidateRejection,
    CoordinateFrame,
    PointFailure,
    PointFailureReason,
    RejectReason,
    Roi,
)

INVALID = 0  # sensing_foundation.INVALID_DEPTH_RAW と同値
WIDTH_PX = 640
HEIGHT_PX = 480
FX_PX = 600.0
FY_PX = 600.0
PPX_PX = 320.0
PPY_PX = 240.0
DEPTH_SCALE_MM = 1.0  # 1 raw count = 1 mm。テストの計算を追いやすくするため。


def _intrinsics(
    *,
    fx_px: float = FX_PX,
    fy_px: float = FY_PX,
    ppx_px: float = PPX_PX,
    ppy_px: float = PPY_PX,
) -> CameraIntrinsics:
    return CameraIntrinsics(
        width_px=WIDTH_PX,
        height_px=HEIGHT_PX,
        fx_px=fx_px,
        fy_px=fy_px,
        ppx_px=ppx_px,
        ppy_px=ppy_px,
        model="none",
        coeffs=(0.0, 0.0, 0.0, 0.0, 0.0),
    )


def _profile(
    *, intrinsics: CameraIntrinsics | None, depth_scale_mm: float = DEPTH_SCALE_MM
) -> StreamProfile:
    return StreamProfile(
        width_px=WIDTH_PX,
        height_px=HEIGHT_PX,
        fps=30,
        depth_scale_mm=depth_scale_mm,
        color_enabled=False,
        intrinsics=intrinsics,
    )


def _frame(
    depth: np.ndarray, profile: StreamProfile, *, t_capture_ms: float = 1000.0
) -> CaptureFrame:
    return CaptureFrame(
        index=0,
        seq=0,
        t_capture_ms=t_capture_ms,
        device_timestamp_ms=None,
        timestamp_domain=TimestampDomain.UNKNOWN,
        capture_latency_ms=None,
        depth=depth,
        profile=profile,
        source=SourceKind.SIMULATED,
        dropped_before=0,
        gap_before=0,
    )


def _background_depth(raw: int = 4000) -> np.ndarray:
    return np.full((HEIGHT_PX, WIDTH_PX), raw, dtype=np.uint16)


def _candidate(
    *,
    cx_px: float,
    cy_px: float,
    bbox_px: tuple[int, int, int, int],
    area_px: int,
    valid_depth_px: int = 0,
    mask_score: float = 1.0,
) -> Candidate:
    return Candidate(
        cx_px=cx_px,
        cy_px=cy_px,
        bbox_px=bbox_px,
        area_px=area_px,
        valid_depth_px=valid_depth_px,
        mask_score=mask_score,
    )


def _roi(*, z_min_mm: float | None = 500.0, z_max_mm: float | None = 5000.0) -> Roi:
    return Roi(
        x_px=0, y_px=0, width_px=WIDTH_PX, height_px=HEIGHT_PX, z_min_mm=z_min_mm, z_max_mm=z_max_mm
    )


def _estimator(
    *,
    object_model: ObjectModelConfig | None = None,
    projection: ProjectionConfig | None = None,
    roi: Roi | None = None,
) -> PointEstimator:
    return PointEstimator(
        object_model=object_model if object_model is not None else ObjectModelConfig(),
        projection=projection if projection is not None else ProjectionConfig(),
        roi=roi if roi is not None else _roi(),
    )


def _fill_disk(
    depth: np.ndarray, *, cx_px: float, cy_px: float, radius_px: float, raw: int
) -> None:
    yy, xx = np.ogrid[0:HEIGHT_PX, 0:WIDTH_PX]
    mask = (xx - cx_px) ** 2 + (yy - cy_px) ** 2 <= radius_px**2
    depth[mask] = np.uint16(raw)


def _bbox_around(cx_px: float, cy_px: float, radius_px: float, margin_px: int = 4) -> tuple[int, int, int, int]:
    x0 = max(0, int(cx_px - radius_px) - margin_px)
    y0 = max(0, int(cy_px - radius_px) - margin_px)
    x1 = min(WIDTH_PX, int(cx_px + radius_px) + margin_px + 1)
    y1 = min(HEIGHT_PX, int(cy_px + radius_px) + margin_px + 1)
    return x0, y0, x1 - x0, y1 - y0


# --------------------------------------------------------------------------
# 既知の3D点との解析解一致（往復。合成器の forward projection と対）
# --------------------------------------------------------------------------


class TestAnalyticRoundTrip:
    def test_recovers_known_3d_point(self, synthetic_module) -> None:
        intrinsics = _intrinsics()
        profile = _profile(intrinsics=intrinsics)

        x_mm, y_mm, z_mm = 150.0, -80.0, 2000.0
        u_px, v_px = synthetic_module.project_to_pixel(intrinsics, x_mm, y_mm, z_mm)
        diameter_mm = 65.0
        diam_px = synthetic_module.expected_diameter_px(intrinsics, diameter_mm, z_mm)
        radius_px = diam_px / 2.0
        raw = synthetic_module.raw_from_z_mm(z_mm, profile.depth_scale_mm)

        # bbox は矩形だが塊は円形であるため、bbox 内で円の外側に位置する
        # 画素は背景になる。背景を無効値(0)にすることで、有効画素が
        # 円内の塊画素だけになり、代表値が汚染されない（解析解との厳密な
        # 一致を検証するテストの前提）。
        depth = _background_depth(raw=INVALID)
        _fill_disk(depth, cx_px=u_px, cy_px=v_px, radius_px=radius_px, raw=raw)
        frame = _frame(depth, profile, t_capture_ms=4242.0)

        bbox_px = _bbox_around(u_px, v_px, radius_px)
        area_px = int(math.pi * radius_px**2)
        candidate = _candidate(cx_px=u_px, cy_px=v_px, bbox_px=bbox_px, area_px=area_px)

        estimator = _estimator(
            object_model=ObjectModelConfig(diameter_mm=diameter_mm),
            roi=_roi(z_min_mm=500.0, z_max_mm=5000.0),
        )
        result = estimator.estimate(frame, candidate)

        assert isinstance(result, CameraPoint)
        assert result.frame == CoordinateFrame.CAMERA
        assert result.x_mm == pytest.approx(x_mm, abs=1e-6)
        assert result.y_mm == pytest.approx(y_mm, abs=1e-6)
        assert result.z_mm == pytest.approx(z_mm, abs=1e-6)
        assert result.t_ms == 4242.0


# --------------------------------------------------------------------------
# 上流への委譲の証明
# --------------------------------------------------------------------------


class TestDelegationProof:
    def test_calls_real_depth_raw_to_mm_and_deproject_pixel(self, monkeypatch) -> None:
        depth_raw_to_mm_calls: list[tuple[float, float]] = []
        deproject_pixel_calls: list[tuple[object, float, float, float]] = []

        real_depth_raw_to_mm = projection_module.depth_raw_to_mm
        real_deproject_pixel = projection_module.deproject_pixel

        def spy_depth_raw_to_mm(raw: float, depth_scale_mm: float) -> float:
            depth_raw_to_mm_calls.append((raw, depth_scale_mm))
            return real_depth_raw_to_mm(raw, depth_scale_mm)

        def spy_deproject_pixel(intrinsics, u_px, v_px, z_mm):
            deproject_pixel_calls.append((intrinsics, u_px, v_px, z_mm))
            return real_deproject_pixel(intrinsics, u_px, v_px, z_mm)

        monkeypatch.setattr(projection_module, "depth_raw_to_mm", spy_depth_raw_to_mm)
        monkeypatch.setattr(projection_module, "deproject_pixel", spy_deproject_pixel)

        intrinsics = _intrinsics()
        profile = _profile(intrinsics=intrinsics)
        depth = _background_depth(raw=INVALID)
        _fill_disk(depth, cx_px=300.0, cy_px=200.0, radius_px=10.0, raw=2000)
        frame = _frame(depth, profile)
        bbox_px = _bbox_around(300.0, 200.0, 10.0)
        area_px = int(math.pi * 10.0**2)
        candidate = _candidate(cx_px=300.0, cy_px=200.0, bbox_px=bbox_px, area_px=area_px)

        estimator = _estimator()
        result = estimator.estimate(frame, candidate)

        assert isinstance(result, CameraPoint)
        assert len(depth_raw_to_mm_calls) == 1
        assert depth_raw_to_mm_calls[0] == (2000.0, DEPTH_SCALE_MM)
        assert len(deproject_pixel_calls) == 1
        called_intrinsics, called_u, called_v, called_z = deproject_pixel_calls[0]
        assert called_intrinsics is intrinsics
        assert called_u == 300.0
        assert called_v == 200.0
        assert called_z == 2000.0

    def test_matches_correct_pinhole_convention_not_plus_half_offset(self) -> None:
        # +0.5 補正を誤って加えると測定可能な差が出るように、主点からずらした
        # 画素位置・距離を選ぶ。正しい規約（補正なし）の結果と一致し、
        # +0.5 補正を加えた誤った式の結果とは一致しないことを確認する。
        intrinsics = _intrinsics()
        profile = _profile(intrinsics=intrinsics)
        cx_px, cy_px, z_mm = 420.0, 340.0, 3000.0
        raw = int(z_mm / DEPTH_SCALE_MM)

        depth = _background_depth(raw=INVALID)
        _fill_disk(depth, cx_px=cx_px, cy_px=cy_px, radius_px=8.0, raw=raw)
        frame = _frame(depth, profile)
        bbox_px = _bbox_around(cx_px, cy_px, 8.0)
        area_px = int(math.pi * 8.0**2)
        candidate = _candidate(cx_px=cx_px, cy_px=cy_px, bbox_px=bbox_px, area_px=area_px)

        estimator = _estimator(roi=_roi(z_min_mm=500.0, z_max_mm=5000.0))
        result = estimator.estimate(frame, candidate)
        assert isinstance(result, CameraPoint)

        correct_x_mm = (cx_px - PPX_PX) / FX_PX * z_mm
        correct_y_mm = (cy_px - PPY_PX) / FY_PX * z_mm
        wrong_x_mm = (cx_px + 0.5 - PPX_PX) / FX_PX * z_mm
        wrong_y_mm = (cy_px + 0.5 - PPY_PX) / FY_PX * z_mm

        assert result.x_mm == pytest.approx(correct_x_mm, abs=1e-6)
        assert result.y_mm == pytest.approx(correct_y_mm, abs=1e-6)
        assert abs(result.x_mm - wrong_x_mm) > 0.1
        assert abs(result.y_mm - wrong_y_mm) > 0.1


# --------------------------------------------------------------------------
# 有効画素不足
# --------------------------------------------------------------------------


class TestInsufficientValidPixels:
    def test_returns_point_failure_with_reason_and_count(self) -> None:
        depth = np.full((20, 20), INVALID, dtype=np.uint16)
        depth[0:2, 0:2] = 2000  # 4 valid pixels < 既定 min_valid_depth_px=8
        full = _background_depth(raw=INVALID)
        full[100:120, 100:120] = depth
        profile = _profile(intrinsics=_intrinsics())
        frame = _frame(full, profile)

        candidate = _candidate(cx_px=101.0, cy_px=101.0, bbox_px=(100, 100, 20, 20), area_px=50)
        estimator = _estimator()

        result = estimator.estimate(frame, candidate)

        assert isinstance(result, PointFailure)
        assert result.reason == PointFailureReason.INSUFFICIENT_VALID_PIXELS
        assert result.valid_depth_px == 4


# --------------------------------------------------------------------------
# ばらつき過大
# --------------------------------------------------------------------------


class TestDepthSpreadTooLarge:
    def test_returns_point_failure_when_spread_exceeds_limit(self) -> None:
        # トリムなし・spread 上限を小さく設定し、大きくばらついた値で確実に
        # 超過させる。
        full = _background_depth(raw=INVALID)
        # 10x20=200画素のうち半分を近距離、半分を遠距離にする（trim後も
        # 大きな spread が残る）。
        full[100:110, 100:110] = 1000
        full[100:110, 110:120] = 3000
        profile = _profile(intrinsics=_intrinsics())
        frame = _frame(full, profile)

        candidate = _candidate(cx_px=110.0, cy_px=105.0, bbox_px=(100, 100, 20, 10), area_px=200)
        estimator = _estimator(
            projection=ProjectionConfig(
                min_valid_depth_px=8, depth_trim_ratio=0.1, max_depth_spread_mm=50.0
            )
        )

        result = estimator.estimate(frame, candidate)

        assert isinstance(result, PointFailure)
        assert result.reason == PointFailureReason.DEPTH_SPREAD_TOO_LARGE
        assert result.valid_depth_px == 200


# --------------------------------------------------------------------------
# 距離帯外
# --------------------------------------------------------------------------


class TestOutOfDepthBand:
    def test_returns_point_failure_when_beyond_z_max(self) -> None:
        intrinsics = _intrinsics()
        profile = _profile(intrinsics=intrinsics)
        z_mm = 6000.0  # roi.z_max_mm=5000 を超える
        diameter_mm = 65.0
        diam_px = intrinsics.fx_px * diameter_mm / z_mm
        radius_px = diam_px / 2.0
        raw = int(z_mm / DEPTH_SCALE_MM)

        depth = _background_depth(raw=INVALID)
        _fill_disk(depth, cx_px=300.0, cy_px=200.0, radius_px=radius_px, raw=raw)
        frame = _frame(depth, profile)
        bbox_px = _bbox_around(300.0, 200.0, radius_px)
        area_px = int(math.pi * radius_px**2)
        candidate = _candidate(cx_px=300.0, cy_px=200.0, bbox_px=bbox_px, area_px=area_px)

        estimator = _estimator(
            object_model=ObjectModelConfig(diameter_mm=diameter_mm),
            roi=_roi(z_min_mm=500.0, z_max_mm=5000.0),
        )
        result = estimator.estimate(frame, candidate)

        assert isinstance(result, PointFailure)
        assert result.reason == PointFailureReason.OUT_OF_DEPTH_BAND

    def test_returns_point_failure_when_below_z_min(self) -> None:
        intrinsics = _intrinsics()
        profile = _profile(intrinsics=intrinsics)
        z_mm = 300.0  # roi.z_min_mm=500 未満
        diameter_mm = 65.0
        diam_px = intrinsics.fx_px * diameter_mm / z_mm
        radius_px = diam_px / 2.0
        raw = int(z_mm / DEPTH_SCALE_MM)

        depth = _background_depth(raw=INVALID)
        _fill_disk(depth, cx_px=300.0, cy_px=200.0, radius_px=radius_px, raw=raw)
        frame = _frame(depth, profile)
        bbox_px = _bbox_around(300.0, 200.0, radius_px)
        area_px = int(math.pi * radius_px**2)
        candidate = _candidate(cx_px=300.0, cy_px=200.0, bbox_px=bbox_px, area_px=area_px)

        estimator = _estimator(
            object_model=ObjectModelConfig(diameter_mm=diameter_mm),
            roi=_roi(z_min_mm=500.0, z_max_mm=5000.0),
        )
        result = estimator.estimate(frame, candidate)

        assert isinstance(result, PointFailure)
        assert result.reason == PointFailureReason.OUT_OF_DEPTH_BAND


# --------------------------------------------------------------------------
# 寸法不一致
# --------------------------------------------------------------------------


class TestSizeMismatch:
    def test_returns_candidate_rejection_when_apparent_diameter_too_small(self) -> None:
        intrinsics = _intrinsics()
        profile = _profile(intrinsics=intrinsics)
        z_mm = 2000.0
        raw = int(z_mm / DEPTH_SCALE_MM)

        depth = _background_depth(raw=INVALID)
        _fill_disk(depth, cx_px=300.0, cy_px=200.0, radius_px=10.0, raw=raw)
        frame = _frame(depth, profile)
        bbox_px = _bbox_around(300.0, 200.0, 10.0)

        # area_px を極端に小さく偽装し、見かけの直径を期待値の許容範囲外にする。
        candidate = _candidate(cx_px=300.0, cy_px=200.0, bbox_px=bbox_px, area_px=1)

        estimator = _estimator(
            object_model=ObjectModelConfig(diameter_mm=65.0, min_scale=0.5, max_scale=2.0),
            roi=_roi(z_min_mm=500.0, z_max_mm=5000.0),
        )
        result = estimator.estimate(frame, candidate)

        assert isinstance(result, CandidateRejection)
        assert result.reason == RejectReason.SIZE_MISMATCH
        assert result.count == 1

    def test_returns_candidate_rejection_when_apparent_diameter_too_large(self) -> None:
        intrinsics = _intrinsics()
        profile = _profile(intrinsics=intrinsics)
        z_mm = 2000.0
        raw = int(z_mm / DEPTH_SCALE_MM)

        depth = _background_depth(raw=INVALID)
        _fill_disk(depth, cx_px=300.0, cy_px=200.0, radius_px=10.0, raw=raw)
        frame = _frame(depth, profile)
        bbox_px = _bbox_around(300.0, 200.0, 10.0)

        # area_px を極端に大きく偽装する（bbox 内の実際の有効画素数とは
        # 独立したフィールドである点に注意）。
        candidate = _candidate(cx_px=300.0, cy_px=200.0, bbox_px=bbox_px, area_px=1_000_000)

        estimator = _estimator(
            object_model=ObjectModelConfig(diameter_mm=65.0, min_scale=0.5, max_scale=2.0),
            roi=_roi(z_min_mm=500.0, z_max_mm=5000.0),
        )
        result = estimator.estimate(frame, candidate)

        assert isinstance(result, CandidateRejection)
        assert result.reason == RejectReason.SIZE_MISMATCH


# --------------------------------------------------------------------------
# 無効画素の除外
# --------------------------------------------------------------------------


class TestInvalidPixelsExcludedFromAggregation:
    def test_invalid_pixels_do_not_affect_valid_count_or_representative_value(self) -> None:
        # 10x10 の bbox の一部を無効(0)にし、残りを一定値で埋める。
        # 無効画素が有効画素数からもトリム平均からも除外されることを確認する。
        full = _background_depth(raw=INVALID)
        block = np.full((10, 10), 2000, dtype=np.uint16)
        block[0:3, 0:3] = INVALID  # 9画素を無効化（100 - 9 = 91有効）
        full[100:110, 100:110] = block
        profile = _profile(intrinsics=_intrinsics())
        frame = _frame(full, profile)

        candidate = _candidate(cx_px=105.0, cy_px=105.0, bbox_px=(100, 100, 10, 10), area_px=100)
        estimator = _estimator(roi=_roi(z_min_mm=500.0, z_max_mm=5000.0))

        result = estimator.estimate(frame, candidate)

        assert isinstance(result, CameraPoint)
        assert result.valid_depth_px == 91
        # 有効画素はすべて同一値(2000)であるため、トリム平均・スプレッドとも
        # ゼロずれで一意に決まる。
        assert result.z_mm == pytest.approx(2000.0, abs=1e-6)
        assert result.depth_spread_mm == pytest.approx(0.0, abs=1e-9)


# --------------------------------------------------------------------------
# 外接矩形の外側を読まない
# --------------------------------------------------------------------------


class TestReadsOnlyWithinBbox:
    def test_pixels_outside_bbox_do_not_affect_result(self) -> None:
        profile = _profile(intrinsics=_intrinsics())

        clean = _background_depth(raw=500)
        clean[100:110, 100:110] = 2000
        frame_clean = _frame(clean, profile)

        chaotic = _background_depth(raw=500)
        chaotic[100:110, 100:110] = 2000
        # bbox の外側を無効値・極端値で汚染する。
        chaotic[0:50, 0:50] = INVALID
        chaotic[200:300, 200:300] = 65000 % 65536
        chaotic[99, 99] = INVALID  # bbox に隣接するがすぐ外側
        frame_chaotic = _frame(chaotic, profile)

        candidate = _candidate(cx_px=105.0, cy_px=105.0, bbox_px=(100, 100, 10, 10), area_px=100)
        estimator = _estimator(roi=_roi(z_min_mm=500.0, z_max_mm=5000.0))

        result_clean = estimator.estimate(frame_clean, candidate)
        result_chaotic = estimator.estimate(frame_chaotic, candidate)

        assert isinstance(result_clean, CameraPoint)
        assert isinstance(result_chaotic, CameraPoint)
        assert result_clean.x_mm == pytest.approx(result_chaotic.x_mm, abs=1e-9)
        assert result_clean.y_mm == pytest.approx(result_chaotic.y_mm, abs=1e-9)
        assert result_clean.z_mm == pytest.approx(result_chaotic.z_mm, abs=1e-9)
        assert result_clean.valid_depth_px == result_chaotic.valid_depth_px


# --------------------------------------------------------------------------
# 内部パラメータ欠落
# --------------------------------------------------------------------------


class TestIntrinsicsUnavailable:
    def test_raises_with_actionable_message_when_intrinsics_none(self) -> None:
        profile = _profile(intrinsics=None)
        depth = _background_depth(raw=500)
        frame = _frame(depth, profile)
        candidate = _candidate(cx_px=10.0, cy_px=10.0, bbox_px=(0, 0, 5, 5), area_px=25)
        estimator = _estimator()

        with pytest.raises(IntrinsicsUnavailableError) as exc_info:
            estimator.estimate(frame, candidate)

        message = str(exc_info.value)
        assert "StreamProfile" in message
        assert "intrinsics" in message.lower() or "内部パラメータ" in message


# --------------------------------------------------------------------------
# 時刻の引き継ぎと内部パラメータ出所
# --------------------------------------------------------------------------


class TestTimeAndIntrinsicsSourcePassthrough:
    def _good_candidate_and_frame(self, *, t_capture_ms: float):
        intrinsics = _intrinsics()
        profile = _profile(intrinsics=intrinsics)
        z_mm = 2000.0
        raw = int(z_mm / DEPTH_SCALE_MM)
        depth = _background_depth(raw=INVALID)
        _fill_disk(depth, cx_px=300.0, cy_px=200.0, radius_px=10.0, raw=raw)
        frame = _frame(depth, profile, t_capture_ms=t_capture_ms)
        bbox_px = _bbox_around(300.0, 200.0, 10.0)
        area_px = int(math.pi * 10.0**2)
        candidate = _candidate(cx_px=300.0, cy_px=200.0, bbox_px=bbox_px, area_px=area_px)
        return frame, candidate

    def test_t_ms_matches_frame_t_capture_ms_exactly(self) -> None:
        frame, candidate = self._good_candidate_and_frame(t_capture_ms=98765.4321)
        estimator = _estimator(roi=_roi(z_min_mm=500.0, z_max_mm=5000.0))

        result = estimator.estimate(frame, candidate)

        assert isinstance(result, CameraPoint)
        assert result.t_ms == 98765.4321

    def test_intrinsics_source_is_populated(self) -> None:
        frame, candidate = self._good_candidate_and_frame(t_capture_ms=0.0)
        estimator = _estimator(roi=_roi(z_min_mm=500.0, z_max_mm=5000.0))

        result = estimator.estimate(frame, candidate)

        assert isinstance(result, CameraPoint)
        assert result.intrinsics_source == "stream_profile"
        assert isinstance(result.intrinsics_source, str)
        assert result.intrinsics_source != ""


# --------------------------------------------------------------------------
# トリム平均による代表値
# --------------------------------------------------------------------------


class TestTrimmedMeanRepresentative:
    def test_outliers_excluded_by_trim_ratio_match_hand_computed_mean(self) -> None:
        # 10画素: 中心付近8画素が2000、両端に極端な外れ値(500, 4500)を1つずつ。
        # depth_trim_ratio=0.2 → 両側 floor(10*0.2)=2画素ずつトリム
        # (合計4画素除去、6画素残存)。ソート順は
        # [500, 2000,2000,2000,2000,2000,2000,2000,2000, 4500]
        # トリム後: [2000,2000,2000,2000,2000,2000] → 平均=2000.0
        full = _background_depth(raw=INVALID)
        block = np.full((1, 10), 2000, dtype=np.uint16)
        block[0, 0] = 500
        block[0, 9] = 4500
        full[100, 100:110] = block
        profile = _profile(intrinsics=_intrinsics())
        frame = _frame(full, profile)

        candidate = _candidate(cx_px=105.0, cy_px=100.0, bbox_px=(100, 100, 10, 1), area_px=10)
        estimator = _estimator(
            # 本テストの主眼はトリム平均の算出であり寸法の本判定ではない
            # ため、許容倍率を大きく緩めて SIZE_MISMATCH による早期リターン
            # を避ける（area_px=10 という細長い候補形状は円としての見かけの
            # 直径が小さく、既定の許容倍率では寸法不一致になってしまう）。
            object_model=ObjectModelConfig(min_scale=0.0, max_scale=1000.0),
            projection=ProjectionConfig(
                min_valid_depth_px=8, depth_trim_ratio=0.2, max_depth_spread_mm=1000.0
            ),
            roi=_roi(z_min_mm=500.0, z_max_mm=5000.0),
        )

        result = estimator.estimate(frame, candidate)

        assert isinstance(result, CameraPoint)
        assert result.valid_depth_px == 10
        assert result.z_mm == pytest.approx(2000.0, abs=1e-6)
        assert result.depth_spread_mm == pytest.approx(0.0, abs=1e-9)


# --------------------------------------------------------------------------
# depth_scale_mm != 1.0（レビュー指摘: 既存15テストは全て depth_scale_mm=1.0
# を使っており、「1カウント = 1mm と決め打ちしない」（要件5.8）という制約への
# 回帰を検出できない盲点があった）
# --------------------------------------------------------------------------


class TestNonUnitDepthScale:
    def test_recovers_known_3d_point_with_non_unit_depth_scale_mm(self, synthetic_module) -> None:
        """depth_scale_mm=1.7（!= 1.0）でも正しく mm 換算されることを確認する。

        手計算した raw カウントと期待値でアサーションを固定する。もし実装が
        誤って `depth_scale_mm` を無視する、または `1.0` に決め打ちしていた
        場合、`result.z_mm` は raw の生の値（1176）に近い値になり、正しい
        期待値（1999.2）とは大きく乖離する——この対照によって回帰検出力を
        担保する。
        """
        depth_scale_mm = 1.7
        intrinsics = _intrinsics()
        profile = _profile(intrinsics=intrinsics, depth_scale_mm=depth_scale_mm)

        x_mm, y_mm, z_mm = 150.0, -80.0, 2000.0
        u_px, v_px = synthetic_module.project_to_pixel(intrinsics, x_mm, y_mm, z_mm)
        diameter_mm = 65.0
        diam_px = synthetic_module.expected_diameter_px(intrinsics, diameter_mm, z_mm)
        radius_px = diam_px / 2.0

        # 手計算: raw = round(z_mm / depth_scale_mm) = round(2000 / 1.7)
        #             = round(1176.470588...) = 1176
        raw = synthetic_module.raw_from_z_mm(z_mm, depth_scale_mm)
        assert raw == 1176  # 手計算した raw カウントで固定する

        depth = _background_depth(raw=INVALID)
        _fill_disk(depth, cx_px=u_px, cy_px=v_px, radius_px=radius_px, raw=raw)
        frame = _frame(depth, profile, t_capture_ms=4242.0)

        bbox_px = _bbox_around(u_px, v_px, radius_px)
        area_px = int(math.pi * radius_px**2)
        candidate = _candidate(cx_px=u_px, cy_px=v_px, bbox_px=bbox_px, area_px=area_px)

        estimator = _estimator(
            object_model=ObjectModelConfig(diameter_mm=diameter_mm),
            roi=_roi(z_min_mm=500.0, z_max_mm=5000.0),
        )
        result = estimator.estimate(frame, candidate)

        assert isinstance(result, CameraPoint)

        # 手計算: raw * depth_scale_mm = 1176 * 1.7 = 1999.2mm。
        # z_mm がちょうど 2000.0 には戻らない（round() による 0.8mm の丸め
        # 誤差が正しく反映される）ことそのものが、mm 換算を上流の実関数
        # （`depth_raw_to_mm`）に委ねている証拠になる。
        expected_z_mm = raw * depth_scale_mm
        assert expected_z_mm == pytest.approx(1999.2, abs=1e-9)
        assert result.z_mm == pytest.approx(expected_z_mm, abs=1e-6)

        expected_x_mm = (u_px - PPX_PX) / FX_PX * expected_z_mm
        expected_y_mm = (v_px - PPY_PX) / FY_PX * expected_z_mm
        assert result.x_mm == pytest.approx(expected_x_mm, abs=1e-6)
        assert result.y_mm == pytest.approx(expected_y_mm, abs=1e-6)

        # 対照アサーション: depth_scale_mm を無視して 1.0 決め打ちした
        # 誤実装なら z_mm は raw の生の値（1176）に近い値になるはずであり、
        # 正しい期待値（1999.2）とは 100mm 以上乖離する。
        wrong_z_mm_if_scale_hardcoded_to_one = float(raw)
        assert abs(result.z_mm - wrong_z_mm_if_scale_hardcoded_to_one) > 100.0


# --------------------------------------------------------------------------
# bbox 全体が無効（valid_count=0）
# --------------------------------------------------------------------------


class TestAllInvalidBbox:
    def test_returns_insufficient_valid_pixels_with_zero_count_when_bbox_entirely_invalid(
        self,
    ) -> None:
        full = _background_depth(raw=INVALID)
        # bbox の範囲(100,100,10,10)はすでに全体が INVALID(0) で埋まって
        # いる（背景そのものが無効値）。
        profile = _profile(intrinsics=_intrinsics())
        frame = _frame(full, profile)
        candidate = _candidate(cx_px=105.0, cy_px=105.0, bbox_px=(100, 100, 10, 10), area_px=100)
        estimator = _estimator()

        result = estimator.estimate(frame, candidate)

        assert isinstance(result, PointFailure)
        assert result.reason == PointFailureReason.INSUFFICIENT_VALID_PIXELS
        assert result.valid_depth_px == 0


# --------------------------------------------------------------------------
# 複数の失敗条件が同時に成立する場合の分岐優先順位
# （design.md アルゴリズム上の約束の手順順序: 2 → 3 → 6 → 7 の評価順を固定）
# --------------------------------------------------------------------------


class TestBranchPriorityOrdering:
    def test_insufficient_valid_pixels_wins_over_would_be_size_mismatch(self) -> None:
        # 有効画素が4画素(< min_valid_depth_px=8)しかない候補に、もし手順6
        # （寸法本判定）まで到達すれば確実に SIZE_MISMATCH になる極端な
        # area_px=1 を同時に与える。手順2（有効画素数チェック）は手順6より
        # 先に評価されるため、INSUFFICIENT_VALID_PIXELS が優先されるはずである。
        full = _background_depth(raw=INVALID)
        full[100:102, 100:102] = 2000  # 4 valid pixels
        profile = _profile(intrinsics=_intrinsics())
        frame = _frame(full, profile)

        candidate = _candidate(cx_px=101.0, cy_px=101.0, bbox_px=(100, 100, 20, 20), area_px=1)
        estimator = _estimator()

        result = estimator.estimate(frame, candidate)

        assert isinstance(result, PointFailure)
        assert result.reason == PointFailureReason.INSUFFICIENT_VALID_PIXELS
        assert result.valid_depth_px == 4

    def test_depth_spread_too_large_wins_over_would_be_size_mismatch(self) -> None:
        # 十分な有効画素数(200 >= 8)を持つが、ばらつきが上限(50mm)を大幅に
        # 超える候補に、もし手順6まで到達すれば確実に SIZE_MISMATCH になる
        # area_px=1 を同時に与える。手順3（ばらつきチェック）は手順6より先に
        # 評価されるため、DEPTH_SPREAD_TOO_LARGE が優先されるはずである。
        full = _background_depth(raw=INVALID)
        full[100:110, 100:110] = 1000
        full[100:110, 110:120] = 3000
        profile = _profile(intrinsics=_intrinsics())
        frame = _frame(full, profile)

        candidate = _candidate(cx_px=110.0, cy_px=105.0, bbox_px=(100, 100, 20, 10), area_px=1)
        estimator = _estimator(
            projection=ProjectionConfig(
                min_valid_depth_px=8, depth_trim_ratio=0.1, max_depth_spread_mm=50.0
            )
        )

        result = estimator.estimate(frame, candidate)

        assert isinstance(result, PointFailure)
        assert result.reason == PointFailureReason.DEPTH_SPREAD_TOO_LARGE

    def test_size_mismatch_wins_over_would_be_out_of_depth_band(self) -> None:
        # 距離帯外(手順7なら OUT_OF_DEPTH_BAND)になるはずの z=6000mm
        # (roi.z_max_mm=5000 超過)だが、寸法本判定(手順6)は距離帯判定
        # （手順7）より先に評価される。area_px=1 という極端な寸法不一致を
        # 与えれば、SIZE_MISMATCH が先に返るはずである。
        intrinsics = _intrinsics()
        profile = _profile(intrinsics=intrinsics)
        z_mm = 6000.0
        raw = int(z_mm / DEPTH_SCALE_MM)
        depth = _background_depth(raw=INVALID)
        _fill_disk(depth, cx_px=300.0, cy_px=200.0, radius_px=10.0, raw=raw)
        frame = _frame(depth, profile)
        bbox_px = _bbox_around(300.0, 200.0, 10.0)

        candidate = _candidate(cx_px=300.0, cy_px=200.0, bbox_px=bbox_px, area_px=1)
        estimator = _estimator(roi=_roi(z_min_mm=500.0, z_max_mm=5000.0))

        result = estimator.estimate(frame, candidate)

        assert isinstance(result, CandidateRejection)
        assert result.reason == RejectReason.SIZE_MISMATCH
