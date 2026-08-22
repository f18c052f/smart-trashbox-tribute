"""`detection/candidates.py`（タスク 2.6）の検証。

design.md「L4-L5: 検出 → Detector / CandidateExtractor / CandidateFilter」と
要件 3.1, 3.2, 3.4, 3.5, 9.2, 9.3 が定める、連結成分 → `Candidate` 化と
距離非依存の粗い絞り込みについて、次を固定する。

- 座標は**全画面座標**で表す。ROI をずらしても同一物体の候補重心が同一の
  全画面座標になる（タスクの観測可能な完了状態そのもの）
- 粗いふるいは `min_area_px` / `max_area_px` / ROI 端接触の3つのみで、
  除外は理由ごとに件数を数える
- `valid_depth_px` / `mask_score` の算出
- 候補が1つも無い場合は空集合を返し、例外を送出しない
- 同一理由の複数除外が1つの `CandidateRejection` に集約される
- `SIZE_MISMATCH` を一切生成しない（距離が無いこの段階の責務外）
"""

from __future__ import annotations

import numpy as np
import pytest

from flying_object_tracking.config import ObjectModelConfig
from flying_object_tracking.detection.candidates import (
    CandidateExtractionResult,
    CandidateExtractor,
)
from flying_object_tracking.types import RejectReason, Roi

INVALID = 0  # sensing_foundation.INVALID_DEPTH_RAW と同値


def _roi(*, x_px: int = 0, y_px: int = 0, width_px: int = 64, height_px: int = 64) -> Roi:
    return Roi(
        x_px=x_px,
        y_px=y_px,
        width_px=width_px,
        height_px=height_px,
        z_min_mm=None,
        z_max_mm=None,
    )


def _object_model(*, min_area_px: int = 10, max_area_px: int = 100) -> ObjectModelConfig:
    return ObjectModelConfig(min_area_px=min_area_px, max_area_px=max_area_px)


def _extractor(
    *,
    roi: Roi | None = None,
    object_model: ObjectModelConfig | None = None,
    max_candidates: int = 8,
) -> CandidateExtractor:
    return CandidateExtractor(
        object_model=object_model if object_model is not None else _object_model(),
        roi=roi if roi is not None else _roi(),
        max_candidates=max_candidates,
    )


def _blank(height_px: int = 64, width_px: int = 64) -> tuple[np.ndarray, np.ndarray]:
    mask = np.zeros((height_px, width_px), dtype=bool)
    depth = np.full((height_px, width_px), 1000, dtype=np.uint16)
    return mask, depth


# --------------------------------------------------------------------------
# 空マスク: 例外を送出せず空集合を返す
# --------------------------------------------------------------------------


class TestEmptyMask:
    def test_no_blobs_returns_empty_candidates_and_empty_rejections(self) -> None:
        mask, depth = _blank()
        extractor = _extractor()

        result = extractor.extract(mask, depth)

        assert isinstance(result, CandidateExtractionResult)
        assert result.candidates == ()
        assert result.rejections == ()

    def test_does_not_raise(self) -> None:
        mask, depth = _blank()
        extractor = _extractor()

        # 例外を送出しないことそのものが確認内容（要件 3.5）。
        extractor.extract(mask, depth)


# --------------------------------------------------------------------------
# 粗いふるい: 小さすぎ・大きすぎ・ROI端接触・良好な候補の混在
# --------------------------------------------------------------------------


class TestCoarseFilter:
    def _mixed_mask(self) -> tuple[np.ndarray, np.ndarray]:
        # ROI ローカル 64x64。
        # - too_small: 2x2=4画素 (< min_area_px=10)
        # - too_large: 12x12=144画素 (> max_area_px=100)
        # - touches_edge: 左端 x=0 から始まる 5x5=25画素（良好サイズだが ROI端接触）
        # - good: 10x10=100画素、ROI内部、有効Depthが半分の代表的候補
        mask, depth = _blank()
        mask[5:7, 5:7] = True  # too_small: area=4, x=5,y=5,w=2,h=2
        mask[20:32, 20:32] = True  # too_large: area=144, x=20,y=20,w=12,h=12
        mask[40:45, 0:5] = True  # touches_edge: area=25, x=0,y=40,w=5,h=5
        mask[50:60, 10:20] = True  # good: area=100, x=10,y=50,w=10,h=10
        return mask, depth

    def test_exactly_one_survives_and_three_are_rejected_with_correct_reasons(
        self,
    ) -> None:
        mask, depth = self._mixed_mask()
        extractor = _extractor()

        result = extractor.extract(mask, depth)

        assert len(result.candidates) == 1
        survivor = result.candidates[0]
        # good blob: bbox (x=10, y=50, w=10, h=10), area=100
        assert survivor.area_px == 100
        assert survivor.bbox_px == (10, 50, 10, 10)

        reasons = {r.reason: r.count for r in result.rejections}
        assert reasons == {
            RejectReason.TOO_SMALL_PX: 1,
            RejectReason.TOO_LARGE_PX: 1,
            RejectReason.TOUCHES_ROI_EDGE: 1,
        }

    def test_never_produces_size_mismatch_or_depth_band_or_out_of_roi(self) -> None:
        # 距離情報が無いこの段階では SIZE_MISMATCH / OUT_OF_DEPTH_BAND /
        # OUT_OF_ROI のいずれも判定できない（PointEstimator の責務）。
        mask, depth = self._mixed_mask()
        extractor = _extractor()

        result = extractor.extract(mask, depth)

        produced_reasons = {r.reason for r in result.rejections}
        assert RejectReason.SIZE_MISMATCH not in produced_reasons
        assert RejectReason.OUT_OF_DEPTH_BAND not in produced_reasons
        assert RejectReason.OUT_OF_ROI not in produced_reasons


class TestRejectionCounting:
    def test_multiple_rejects_of_same_reason_collapse_into_one_entry(self) -> None:
        mask, depth = _blank()
        # 3つの too-small ブロブ（互いに非連結）。
        mask[2:4, 2:4] = True  # area=4
        mask[2:4, 10:12] = True  # area=4
        mask[2:4, 20:22] = True  # area=4
        extractor = _extractor(object_model=_object_model(min_area_px=10, max_area_px=1000))

        result = extractor.extract(mask, depth)

        assert result.candidates == ()
        assert len(result.rejections) == 1
        assert result.rejections[0].reason == RejectReason.TOO_SMALL_PX
        assert result.rejections[0].count == 3


# --------------------------------------------------------------------------
# 座標変換: ROI をずらしても全画面座標での重心・bbox が一致する
# （タスクの観測可能な完了状態そのもの）
# --------------------------------------------------------------------------


class TestCoordinateTranslation:
    def test_same_object_same_full_frame_position_across_different_roi_offsets(
        self,
    ) -> None:
        object_model = _object_model(min_area_px=10, max_area_px=1000)

        # 物体は全画面座標で bbox=(70, 60, 10, 10) にある良好サイズのブロブ。
        # ROI は十分大きく取り、どちらのオフセットでも物体全体と ROI 端接触
        # の余白がローカル座標内に収まるようにする（境界に接触させない）。
        full_x_px, full_y_px, w_px, h_px = 70, 60, 10, 10
        roi_width_px, roi_height_px = 120, 120

        # --- ROI offset (0, 0) ---
        roi_a = _roi(x_px=0, y_px=0, width_px=roi_width_px, height_px=roi_height_px)
        mask_a, depth_a = _blank(height_px=roi_height_px, width_px=roi_width_px)
        local_x_a = full_x_px - roi_a.x_px
        local_y_a = full_y_px - roi_a.y_px
        mask_a[local_y_a : local_y_a + h_px, local_x_a : local_x_a + w_px] = True
        extractor_a = CandidateExtractor(
            object_model=object_model, roi=roi_a, max_candidates=8
        )
        result_a = extractor_a.extract(mask_a, depth_a)

        # --- ROI offset (50, 30): 同じ物体が ROI ローカル位置としては
        #     異なる場所に現れるが、全画面位置は変わらない。
        roi_b = _roi(x_px=50, y_px=30, width_px=roi_width_px, height_px=roi_height_px)
        mask_b, depth_b = _blank(height_px=roi_height_px, width_px=roi_width_px)
        local_x_b = full_x_px - roi_b.x_px
        local_y_b = full_y_px - roi_b.y_px
        assert (local_x_b, local_y_b) != (local_x_a, local_y_a)  # 前提: ROIローカル位置は異なる
        mask_b[local_y_b : local_y_b + h_px, local_x_b : local_x_b + w_px] = True
        extractor_b = CandidateExtractor(
            object_model=object_model, roi=roi_b, max_candidates=8
        )
        result_b = extractor_b.extract(mask_b, depth_b)

        assert len(result_a.candidates) == 1
        assert len(result_b.candidates) == 1
        candidate_a = result_a.candidates[0]
        candidate_b = result_b.candidates[0]

        assert candidate_a.bbox_px == candidate_b.bbox_px == (70, 60, 10, 10)
        assert candidate_a.cx_px == pytest.approx(candidate_b.cx_px)
        assert candidate_a.cy_px == pytest.approx(candidate_b.cy_px)
        # 全画面座標での重心が bbox の中心と一致することも確認する。
        assert candidate_a.cx_px == pytest.approx(70 + 10 / 2 - 0.5, abs=0.5)
        assert candidate_a.cy_px == pytest.approx(60 + 10 / 2 - 0.5, abs=0.5)


# --------------------------------------------------------------------------
# valid_depth_px / mask_score
# --------------------------------------------------------------------------


class TestValidDepthAndMaskScore:
    def test_valid_depth_px_matches_hand_computed_count(self) -> None:
        mask, depth = _blank()
        # 良好サイズの候補: bbox (x=10, y=10, w=10, h=10), area=100。
        mask[10:20, 10:20] = True
        # bbox 内の depth のうち、半分（上半分 10x5=50画素）を無効値にする。
        depth[10:15, 10:20] = INVALID
        depth[15:20, 10:20] = 1500

        extractor = _extractor(object_model=_object_model(min_area_px=10, max_area_px=1000))
        result = extractor.extract(mask, depth)

        assert len(result.candidates) == 1
        candidate = result.candidates[0]
        assert candidate.area_px == 100
        assert candidate.valid_depth_px == 50  # 下半分だけが有効

    def test_mask_score_matches_documented_formula_and_is_in_unit_interval(self) -> None:
        mask, depth = _blank()
        mask[10:20, 10:20] = True  # area=100
        depth[10:15, 10:20] = INVALID  # 50画素が無効
        depth[15:20, 10:20] = 1500  # 50画素が有効

        extractor = _extractor(object_model=_object_model(min_area_px=10, max_area_px=1000))
        result = extractor.extract(mask, depth)

        candidate = result.candidates[0]
        # 文書化した式: clamp(valid_depth_px / area_px, 0.0, 1.0)
        expected_score = min(1.0, max(0.0, candidate.valid_depth_px / candidate.area_px))
        assert candidate.mask_score == pytest.approx(expected_score)
        assert candidate.mask_score == pytest.approx(0.5)
        assert 0.0 <= candidate.mask_score <= 1.0

    def test_mask_score_clamped_when_bbox_has_more_valid_depth_than_area(self) -> None:
        # bbox 全体（前景以外を含む）が有効 Depth を持つ非長方形ブロブを作り、
        # valid_depth_px > area_px となるケースで 1.0 にクランプされることを
        # 確認する（L字型ブロブ: bbox=5x5=25 だが前景画素は少ない）。
        mask, depth = _blank()
        # L字型: 縦棒 + 横棒（連結）。bbox は 5x5=25 だが area は 9 のみ。
        mask[10, 10:15] = True  # 横棒5画素 (y=10, x=10..14)
        mask[10:15, 10] = True  # 縦棒5画素 (x=10, y=10..14) - (10,10)が重複
        # area = 5 + 5 - 1(重複) = 9
        depth[10:15, 10:15] = 1200  # bbox全域 (25画素) を有効値にする

        extractor = _extractor(object_model=_object_model(min_area_px=5, max_area_px=1000))
        result = extractor.extract(mask, depth)

        assert len(result.candidates) == 1
        candidate = result.candidates[0]
        assert candidate.area_px == 9
        assert candidate.valid_depth_px == 25
        assert candidate.mask_score == pytest.approx(1.0)


# --------------------------------------------------------------------------
# 例外非送出（さまざまな入力形状に対して）
# --------------------------------------------------------------------------


class TestNeverRaises:
    @pytest.mark.parametrize(
        "mutate",
        [
            lambda mask, depth: None,  # 何もしない（空マスク）
            lambda mask, depth: mask.__setitem__((slice(0, 1), slice(0, 1)), True),
            lambda mask, depth: mask.__setitem__((slice(None), slice(None)), True),
        ],
    )
    def test_various_inputs_do_not_raise(self, mutate) -> None:
        mask, depth = _blank()
        mutate(mask, depth)
        extractor = _extractor(object_model=_object_model(min_area_px=1, max_area_px=100000))

        extractor.extract(mask, depth)  # 例外が送出されないこと自体が確認内容
