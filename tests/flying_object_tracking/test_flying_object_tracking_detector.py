"""`detection/detector.py`（タスク 2.7）の検証。

design.md「L4-L5: 検出 → Detector / CandidateExtractor / CandidateFilter」と
タスク本文が定める、次の観測可能な完了状態を固定する（要件 3.1〜3.7, 4.1, 4.2）。

- `create_detector(settings)` が `settings.detector.kind` に対応する `Detector`
  を返す唯一の生成口であること
- 3方式それぞれの検出器が、同一の合成フレーム列（それぞれの初期化要件を
  満たす warm-up 後）から移動する塊を候補として返すこと
- 後段（形態処理・候補化・粗い絞り込み）が方式に依らず**構造的に共有**
  されていること（min_area_px 未満の塊を3方式とも同一に棄却する）
- `TrackingSettings.postprocess_fingerprint()` が `detector.kind` だけが
  異なる設定間で一致し、他フィールドが異なれば変わること
- `mask_px` が形態処理後のマスクの真画素数と一致すること
- `reset()` が `frame_diff` / `background` の内部状態を「初回フレーム」
  相当まで巻き戻すこと
- `detect()` が全無効 Depth・前景ゼロのフレームでも例外を送出しないこと
- 未知の `detector.kind` が `create_mask_builder` の既存防御を通じて拒否
  されること
"""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest
from sensing_foundation import (
    CameraIntrinsics,
    CaptureFrame,
    SourceKind,
    StreamProfile,
    TimestampDomain,
)

from flying_object_tracking.config import (
    DetectorConfig,
    ObjectModelConfig,
    RoiConfig,
    TrackingSettings,
)
from flying_object_tracking.detection.detector import (
    DefaultDetector,
    DetectResult,
    Detector,
    create_detector,
)
from flying_object_tracking.errors import TrackingConfigError
from flying_object_tracking.types import CandidateRejection, DetectorKind, RejectReason

_ALL_KINDS = (DetectorKind.DEPTH_BAND, DetectorKind.FRAME_DIFF, DetectorKind.BACKGROUND)

# 3方式が必要とする warm-up 回数（本文「背景差分方式の検出器は前景が
# 見えるようになるまでに数回の warm-up detect() 呼び出しを要してよい。
# frame_diff は最低2フレーム、depth_band はフレーム1から動く」）。
_REQUIRED_WARMUP = {
    DetectorKind.DEPTH_BAND: 0,
    DetectorKind.FRAME_DIFF: 1,
    DetectorKind.BACKGROUND: 3,
}

DIAMETER_MM = 65.0
BACKGROUND_RAW = 6000  # 既定 z 帯 [500, 5000] の外側にする
BLOB_RAW_MM = 1000.0  # 既定 z 帯の内側


def _intrinsics(*, width_px: int, height_px: int) -> CameraIntrinsics:
    return CameraIntrinsics(
        width_px=width_px,
        height_px=height_px,
        fx_px=200.0,
        fy_px=200.0,
        ppx_px=width_px / 2.0,
        ppy_px=height_px / 2.0,
        model="brown_conrady",
        coeffs=(0.0, 0.0, 0.0, 0.0, 0.0),
    )


def _profile(*, width_px: int = 64, height_px: int = 64) -> StreamProfile:
    return StreamProfile(
        width_px=width_px,
        height_px=height_px,
        fps=30,
        depth_scale_mm=1.0,
        color_enabled=False,
        intrinsics=_intrinsics(width_px=width_px, height_px=height_px),
    )


def _frame(
    depth: np.ndarray,
    profile: StreamProfile,
    *,
    t_ms: float = 0.0,
    index: int = 0,
    seq: int = 0,
) -> CaptureFrame:
    """テスト用に手組みの depth 配列から `CaptureFrame` を1枚組み立てる。"""
    return CaptureFrame(
        index=index,
        seq=seq,
        t_capture_ms=t_ms,
        device_timestamp_ms=None,
        timestamp_domain=TimestampDomain.UNKNOWN,
        capture_latency_ms=None,
        depth=depth,
        profile=profile,
        source=SourceKind.SIMULATED,
        dropped_before=0,
        gap_before=0,
    )


def _detector_config(
    kind: DetectorKind,
    *,
    background_frames: int = 3,
    open_kernel_px: int = 3,
    close_kernel_px: int = 3,
) -> DetectorConfig:
    return DetectorConfig(
        kind=kind,
        diff_threshold_mm=80.0,
        background_frames=background_frames,
        background_update_rate=0.0,
        open_kernel_px=open_kernel_px,
        close_kernel_px=close_kernel_px,
        max_candidates=8,
    )


def _trajectory(synthetic_module, n_warmup: int):
    """`n_warmup` 回の「塊が画面外にある」フレーム＋最後に「塊が中心にある」フレーム。"""
    TrajectoryPoint = synthetic_module.TrajectoryPoint
    off_canvas = [
        TrajectoryPoint(t_ms=float(i) * 33.0, x_mm=1_000_000.0, y_mm=0.0, z_mm=BLOB_RAW_MM)
        for i in range(n_warmup)
    ]
    on_canvas = TrajectoryPoint(
        t_ms=float(n_warmup) * 33.0, x_mm=0.0, y_mm=0.0, z_mm=BLOB_RAW_MM
    )
    return off_canvas + [on_canvas]


# --------------------------------------------------------------------------
# create_detector: 唯一の生成口であり、settings.detector.kind を反映する
# --------------------------------------------------------------------------


class TestCreateDetectorDispatch:
    @pytest.mark.parametrize("kind", _ALL_KINDS)
    def test_kind_property_matches_settings(self, kind: DetectorKind) -> None:
        settings = TrackingSettings(detector=_detector_config(kind))

        detector = create_detector(settings)

        assert detector.kind == kind

    @pytest.mark.parametrize("kind", _ALL_KINDS)
    def test_returned_object_satisfies_detector_protocol(self, kind: DetectorKind) -> None:
        settings = TrackingSettings(detector=_detector_config(kind))

        detector = create_detector(settings)

        assert isinstance(detector, Detector)
        assert hasattr(detector, "kind")
        assert callable(detector.detect)
        assert callable(detector.reset)

    def test_create_detector_returns_default_detector_instance(self) -> None:
        settings = TrackingSettings(detector=_detector_config(DetectorKind.DEPTH_BAND))

        detector = create_detector(settings)

        assert isinstance(detector, DefaultDetector)


# --------------------------------------------------------------------------
# 3方式が同一の合成フレーム列から移動する塊を候補として返す
# --------------------------------------------------------------------------


class TestDetectsMovingBlobPerKind:
    @pytest.mark.parametrize("kind", _ALL_KINDS)
    def test_detects_blob_after_required_warmup(self, synthetic_module, kind: DetectorKind) -> None:
        profile = _profile()
        n_warmup = _REQUIRED_WARMUP[kind]
        trajectory = _trajectory(synthetic_module, n_warmup)
        frames = synthetic_module.synthesize_frames(
            trajectory, profile, DIAMETER_MM, background_raw=BACKGROUND_RAW
        )
        settings = TrackingSettings(
            detector=_detector_config(kind, background_frames=max(n_warmup, 1))
        )
        detector = create_detector(settings)

        for warmup_frame in frames[:-1]:
            detector.detect(warmup_frame)

        result = detector.detect(frames[-1])

        assert isinstance(result, DetectResult)
        assert len(result.candidates) >= 1
        assert result.mask_px >= 1


# --------------------------------------------------------------------------
# 後段の構造的共有: min_area_px 未満の塊を3方式とも同一に棄却する
# --------------------------------------------------------------------------


class TestSharedPostprocessingStructure:
    """モルフォロジを無効化（open/close=0）し、`CandidateExtractor` の
    粗いふるいだけを観測する。3方式とも同一の `min_area_px` 判定を通る
    ことを確認することで、後段が方式間で構造的に共有されていることを示す。
    """

    def _background_only_depth(self, profile: StreamProfile) -> np.ndarray:
        return np.full((profile.height_px, profile.width_px), BACKGROUND_RAW, dtype=np.uint16)

    def _block_depth(self, profile: StreamProfile) -> np.ndarray:
        depth = self._background_only_depth(profile)
        # 2x2=4画素の小さすぎる塊（既定 min_area_px=12 未満）。
        depth[10:12, 10:12] = np.uint16(BLOB_RAW_MM)
        return depth

    @pytest.mark.parametrize("kind", _ALL_KINDS)
    def test_all_kinds_reject_undersized_blob_identically(self, kind: DetectorKind) -> None:
        profile = _profile(width_px=32, height_px=32)
        settings = TrackingSettings(
            detector=_detector_config(
                kind, background_frames=2, open_kernel_px=0, close_kernel_px=0
            ),
            object_model=ObjectModelConfig(),  # min_area_px=12 既定
        )
        detector = create_detector(settings)

        n_warmup = {
            DetectorKind.DEPTH_BAND: 0,
            DetectorKind.FRAME_DIFF: 1,
            DetectorKind.BACKGROUND: 2,
        }[kind]
        for i in range(n_warmup):
            detector.detect(_frame(self._background_only_depth(profile), profile, seq=i))

        result = detector.detect(
            _frame(self._block_depth(profile), profile, seq=n_warmup)
        )

        assert result.candidates == ()
        assert result.rejections == (
            CandidateRejection(reason=RejectReason.TOO_SMALL_PX, count=1),
        )


# --------------------------------------------------------------------------
# postprocess_fingerprint: kind だけが異なる設定間で一致する
# --------------------------------------------------------------------------


class TestPostprocessFingerprint:
    def test_fingerprint_identical_across_all_kinds_when_only_kind_differs(self) -> None:
        settings_by_kind = {
            kind: TrackingSettings(detector=_detector_config(kind)) for kind in _ALL_KINDS
        }

        fingerprints = {
            kind: settings.postprocess_fingerprint()
            for kind, settings in settings_by_kind.items()
        }

        assert len(set(fingerprints.values())) == 1, fingerprints

    def test_fingerprint_changes_when_object_model_differs(self) -> None:
        base = TrackingSettings(detector=_detector_config(DetectorKind.DEPTH_BAND))
        different = TrackingSettings(
            detector=_detector_config(DetectorKind.DEPTH_BAND),
            object_model=ObjectModelConfig(diameter_mm=99.0),
        )

        assert base.postprocess_fingerprint() != different.postprocess_fingerprint()

    def test_detectors_built_from_same_fingerprint_settings_share_kind_independent_config(
        self,
    ) -> None:
        """指紋が一致する3設定から作った3検出器が、実際に同一の後段設定
        （min_area_px 等）で動作していることを、検出結果でも裏付ける。"""
        settings_by_kind = {
            kind: TrackingSettings(
                detector=_detector_config(kind, open_kernel_px=0, close_kernel_px=0)
            )
            for kind in _ALL_KINDS
        }
        fingerprints = {
            kind: settings.postprocess_fingerprint()
            for kind, settings in settings_by_kind.items()
        }
        assert len(set(fingerprints.values())) == 1

        profile = _profile(width_px=32, height_px=32)
        depth = np.full((32, 32), BACKGROUND_RAW, dtype=np.uint16)
        depth[10:12, 10:12] = np.uint16(BLOB_RAW_MM)  # min_area_px=12 未満

        for kind, settings in settings_by_kind.items():
            detector = create_detector(settings)
            n_warmup = _REQUIRED_WARMUP[kind]
            for i in range(n_warmup):
                bg = np.full((32, 32), BACKGROUND_RAW, dtype=np.uint16)
                detector.detect(_frame(bg, profile, seq=i))
            result = detector.detect(_frame(depth, profile, seq=n_warmup))
            assert result.candidates == ()


# --------------------------------------------------------------------------
# mask_px: 形態処理後のマスクの真画素数と一致する
# --------------------------------------------------------------------------


class TestMaskPxMatchesCleanedMaskCount:
    def test_mask_px_equals_known_blob_area_when_morphology_is_noop(self) -> None:
        profile = _profile(width_px=32, height_px=32)
        depth = np.full((32, 32), BACKGROUND_RAW, dtype=np.uint16)
        # 4x6=24画素の帯内領域。open/close=0 なので形態処理は入力をそのまま
        # 返す（`mask_ops.clean_mask` docstring: 両カーネルが0なら
        # `astype(bool, copy=True)` 相当）。
        depth[5:9, 5:11] = np.uint16(BLOB_RAW_MM)
        settings = TrackingSettings(
            detector=_detector_config(
                DetectorKind.DEPTH_BAND, open_kernel_px=0, close_kernel_px=0
            )
        )
        detector = create_detector(settings)

        result = detector.detect(_frame(depth, profile))

        assert result.mask_px == 24
        # ふるい条件も満たすため候補としても採用される（既定 min/max_area_px内）。
        assert len(result.candidates) == 1
        assert result.candidates[0].area_px == 24


# --------------------------------------------------------------------------
# reset(): frame_diff / background を「初回フレーム」相当まで巻き戻す
# --------------------------------------------------------------------------


class TestReset:
    def test_frame_diff_reset_returns_to_first_frame_semantics(self) -> None:
        profile = _profile(width_px=16, height_px=16)
        background = np.full((16, 16), BACKGROUND_RAW, dtype=np.uint16)
        blob = background.copy()
        blob[6:10, 6:10] = np.uint16(BLOB_RAW_MM)

        settings = TrackingSettings(
            detector=_detector_config(
                DetectorKind.FRAME_DIFF, open_kernel_px=0, close_kernel_px=0
            )
        )
        detector = create_detector(settings)

        # 通常運転: 直前フレームを確立してから塊を見せると検出できる。
        detector.detect(_frame(background, profile, seq=0))
        result_before_reset = detector.detect(_frame(blob, profile, seq=1))
        assert len(result_before_reset.candidates) == 1

        detector.reset()

        # reset 直後の1回目は「初回フレーム」相当であり、直前フレームが
        # 無いため全 False のマスクを返す（frame_diff.py の契約どおり）。
        result_after_reset = detector.detect(_frame(blob, profile, seq=2))
        assert result_after_reset.candidates == ()
        assert result_after_reset.mask_px == 0

    def test_background_reset_returns_to_uninitialized_semantics(self) -> None:
        profile = _profile(width_px=16, height_px=16)
        background = np.full((16, 16), BACKGROUND_RAW, dtype=np.uint16)
        blob = background.copy()
        blob[6:10, 6:10] = np.uint16(BLOB_RAW_MM)

        settings = TrackingSettings(
            detector=_detector_config(
                DetectorKind.BACKGROUND,
                background_frames=2,
                open_kernel_px=0,
                close_kernel_px=0,
            )
        )
        detector = create_detector(settings)

        detector.detect(_frame(background, profile, seq=0))
        detector.detect(_frame(background, profile, seq=1))  # ready になる
        result_before_reset = detector.detect(_frame(blob, profile, seq=2))
        assert len(result_before_reset.candidates) == 1

        detector.reset()

        # reset 後は再び初期化窓の途中であり、内容に関わらず全 False。
        result_after_reset = detector.detect(_frame(blob, profile, seq=3))
        assert result_after_reset.candidates == ()
        assert result_after_reset.mask_px == 0

    def test_reset_before_any_detect_call_is_a_safe_noop(self) -> None:
        settings = TrackingSettings(detector=_detector_config(DetectorKind.BACKGROUND))
        detector = create_detector(settings)

        detector.reset()  # detect() を一度も呼んでいない状態での reset()


# --------------------------------------------------------------------------
# detect() は例外を送出しない（全無効 Depth・前景ゼロ）
# --------------------------------------------------------------------------


class TestNeverRaises:
    @pytest.mark.parametrize("kind", _ALL_KINDS)
    def test_all_invalid_depth_roi_does_not_raise(self, kind: DetectorKind) -> None:
        profile = _profile(width_px=16, height_px=16)
        all_invalid = np.zeros((16, 16), dtype=np.uint16)  # INVALID_DEPTH_RAW == 0
        settings = TrackingSettings(
            detector=_detector_config(kind, background_frames=2)
        )
        detector = create_detector(settings)

        n_warmup = {
            DetectorKind.DEPTH_BAND: 0,
            DetectorKind.FRAME_DIFF: 1,
            DetectorKind.BACKGROUND: 2,
        }[kind]
        for i in range(n_warmup):
            detector.detect(_frame(all_invalid, profile, seq=i))

        result = detector.detect(_frame(all_invalid, profile, seq=n_warmup))

        assert result.candidates == ()
        assert result.mask_px == 0

    @pytest.mark.parametrize("kind", _ALL_KINDS)
    def test_empty_foreground_result_does_not_raise(self, kind: DetectorKind) -> None:
        profile = _profile(width_px=16, height_px=16)
        # 帯の外側の一様な背景のみ。前景が一切生まれない。
        background = np.full((16, 16), BACKGROUND_RAW, dtype=np.uint16)
        settings = TrackingSettings(
            detector=_detector_config(kind, background_frames=2)
        )
        detector = create_detector(settings)

        n_warmup = {
            DetectorKind.DEPTH_BAND: 0,
            DetectorKind.FRAME_DIFF: 1,
            DetectorKind.BACKGROUND: 2,
        }[kind]
        for i in range(n_warmup):
            detector.detect(_frame(background, profile, seq=i))

        result = detector.detect(_frame(background, profile, seq=n_warmup))

        assert result.candidates == ()


# --------------------------------------------------------------------------
# 未知の kind は create_mask_builder の既存防御を通じて拒否される
# --------------------------------------------------------------------------


class TestUnknownKindRejected:
    def test_detect_propagates_tracking_config_error_for_unknown_kind(self) -> None:
        broken_detector = dataclasses.replace(DetectorConfig(), kind="not_a_real_kind")
        settings = TrackingSettings(detector=broken_detector)
        detector = create_detector(settings)
        profile = _profile(width_px=8, height_px=8)
        frame = _frame(np.full((8, 8), BACKGROUND_RAW, dtype=np.uint16), profile)

        with pytest.raises(TrackingConfigError):
            detector.detect(frame)

    def test_unknown_kind_error_is_also_a_value_error(self) -> None:
        broken_detector = dataclasses.replace(DetectorConfig(), kind="bogus")
        settings = TrackingSettings(detector=broken_detector)
        detector = create_detector(settings)
        profile = _profile(width_px=8, height_px=8)
        frame = _frame(np.full((8, 8), BACKGROUND_RAW, dtype=np.uint16), profile)

        with pytest.raises(ValueError):
            detector.detect(frame)


# --------------------------------------------------------------------------
# ROI がフレーム範囲に収まらない場合は起動（初回フレーム）で拒否する
# --------------------------------------------------------------------------


class TestRoiOutOfFrameBoundsRejected:
    def test_roi_wider_than_frame_raises_tracking_config_error(self) -> None:
        settings = TrackingSettings(
            roi=RoiConfig(x_px=0, y_px=0, width_px=999, height_px=999),
            detector=_detector_config(DetectorKind.DEPTH_BAND),
        )
        detector = create_detector(settings)
        profile = _profile(width_px=16, height_px=16)
        frame = _frame(np.full((16, 16), BACKGROUND_RAW, dtype=np.uint16), profile)

        with pytest.raises(TrackingConfigError):
            detector.detect(frame)
