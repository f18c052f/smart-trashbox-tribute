"""連結成分 → `Candidate` 化と、距離非依存の粗い絞り込み
（design.md「L4-L5: 検出 → Detector / CandidateExtractor / CandidateFilter」、
タスク 2.6）。

`mask_ops.components()` が返す連結成分（`Component`）は **ROI ローカル座標**
（`crop_roi` で切り出し済みの配列に対するラベリング結果）である。本モジュールは
それを

1. **全画面座標へ変換**する（処理範囲の指定が出力座標の基準を変えないため。
   要件 2.5）。
2. bbox 内の有効 Depth 画素数・前景らしさ（`mask_score`）を添えて `Candidate`
   化する（要件 3.2）。
3. **距離が求まる前にかけられる粗いふるいだけ**を適用する
   （`min_area_px` / `max_area_px` / ROI 端接触）。除外は理由ごとに件数を
   数える（要件 3.4 / 9.2 / 9.3）。

**この段階では行わないこと**（design.md「Detector / CandidateExtractor /
CandidateFilter」Implementation Notes: 「粗いふるいは `min_area_px` /
`max_area_px` と ROI 端接触のみ。寸法による本判定は距離が求まる
`PointEstimator` で行う」— `research.md` Decision 4）:

- **`RejectReason.SIZE_MISMATCH`**（対象物の想定寸法との照合）は、距離
  （mm）が分からないと期待画素直径 `fx_px * diameter_mm / z_mm` を算出でき
  ないため、ここでは判定しない。距離が求まったあとの `PointEstimator`
  （タスク 3.1）の責務である。
- **`RejectReason.OUT_OF_DEPTH_BAND`** も同じ理由（距離帯判定は mm 換算後
  の代表距離が必要）で `PointEstimator` の責務である。
- **`RejectReason.OUT_OF_ROI`** はここでは構造的に発生しない。
  `mask_ops.components()` は `crop_roi` で ROI 切り出し済みの配列だけを
  見るため、ROI の外側の画素はそもそも連結成分の対象にならない
  （ROI 外を弾く判定を重ねて実装する余地が無い）。

したがって本モジュールが生成しうる `RejectReason` は
`TOO_SMALL_PX` / `TOO_LARGE_PX` / `TOUCHES_ROI_EDGE` の3つに限られる。

`mask_score` の定義（design.md が式を明記していないための設計判断）
----------------------------------------------------------------------
design.md の `Candidate.mask_score` docstring は「方式ごとの前景らしさ
（比較可能性のため 0..1 に正規化）」とだけ定め、具体的な式を残している。
本モジュールは

    mask_score = clamp(valid_depth_px / area_px, 0.0, 1.0)

を採用する。`valid_depth_px` は bbox（外接矩形。前景以外の画素も含みうる
矩形領域）内の有効 Depth 画素数であり、`area_px` は連結成分そのものの
前景画素数（bbox 以下）である。したがって比率は「この前景ブロブの形状に
対して、bbox 内でどれだけ Depth が有効に取れているか」を表す 0 起点の
信頼度信号であり、距離情報が無いこの段階で候補ごとに比較可能な値になる。
`area_px < valid_depth_px` となる場合（bbox が前景以外の有効 Depth 画素を
多く含む場合）に比率が 1.0 を超えうるため、明示的に `[0.0, 1.0]` へ
クランプする。

依存方向（design.md「Dependency Direction」表）
------------------------------------------------
`detection/masks/*`（layer 4）と同様の扱いとする: 0〜3層
（`errors` / `types` / `config` / `metrics` / `detection/mask_ops`）に加え、
`sensing_foundation`（公開入口の `INVALID_DEPTH_RAW`）を import してよい。
`cv2` は直接 import しない（`mask_ops.components()` 経由でのみ連結成分を
得る）。`numpy.vectorize` は使わない（`detection/masks/depth_band.py` と
同じ性能上の理由。ネイティブな NumPy 配列演算のみを使う）。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sensing_foundation import INVALID_DEPTH_RAW

from flying_object_tracking.config import ObjectModelConfig
from flying_object_tracking.detection import mask_ops
from flying_object_tracking.detection.mask_ops import Component
from flying_object_tracking.types import Candidate, CandidateRejection, RejectReason, Roi

__all__ = ["CandidateExtractionResult", "CandidateExtractor"]


# 出力する `CandidateRejection` の順序を固定するための正準順序。
# 発生順（連結成分の面積降順）ではなく理由の種類で安定させることで、
# 同一の入力に対して常に同一の順序の `rejections` タプルを返す（要件 11.3
# の決定性と同じ考え方をこのモジュール内で守る）。
_COARSE_REJECT_ORDER: tuple[RejectReason, ...] = (
    RejectReason.TOO_SMALL_PX,
    RejectReason.TOO_LARGE_PX,
    RejectReason.TOUCHES_ROI_EDGE,
)


@dataclass(frozen=True, slots=True)
class CandidateExtractionResult:
    """`CandidateExtractor.extract()` の戻り値。

    design.md の `DetectResult`（`Detector` の戻り値。`mask_px` を含む）とは
    別の型である。`mask_px`（前景画素数そのもの）は `MaskBuilder.build()` の
    出力から直接得られる値であり、`Detector`（タスク 2.7）がここへ足す。
    本モジュールは候補化と粗い絞り込みだけに責務を閉じる。
    """

    candidates: tuple[Candidate, ...]
    """粗いふるいを通過した候補（全画面座標）。面積降順。"""
    rejections: tuple[CandidateRejection, ...]
    """除外理由ごとの件数。理由が一度も発生しなければタプルに含めない
    （「0件」の `CandidateRejection` は情報を持たないため生成しない）。"""


class CandidateExtractor:
    """連結成分を `Candidate` へ変換し、距離非依存の粗いふるいをかける。

    `PointEstimator`（design.md 参照）と同じ構成方針を採る: 呼び出しの間で
    変わらない設定（`object_model` / `roi` / `max_candidates`）はコンストラクタ
    で受け取り、フレームごとに変わるデータ（マスクと Depth）は
    `extract()` の引数として受け取る。
    """

    def __init__(
        self, object_model: ObjectModelConfig, roi: Roi, max_candidates: int
    ) -> None:
        """絞り込み条件と ROI オフセットを固定する。

        Args:
            object_model: `min_area_px` / `max_area_px`（距離非依存の粗い
                ふるい）を持つ設定。`diameter_mm` / `min_scale` / `max_scale`
                （距離が必要な本判定用）はここでは使わない。
            roi: 処理対象範囲。`x_px` / `y_px` を全画面座標への変換オフセット
                として使う。`extract()` に渡すマスク・Depth 配列は、この
                `roi` で切り出し済み（`mask_ops.crop_roi` 済み）であることを
                前提とする。
            max_candidates: `mask_ops.components()` に渡す上限件数
                （`DetectorConfig.max_candidates`）。
        """
        self._min_area_px = object_model.min_area_px
        self._max_area_px = object_model.max_area_px
        self._roi_x_px = roi.x_px
        self._roi_y_px = roi.y_px
        self._max_candidates = max_candidates

    def extract(
        self, mask: np.ndarray, roi_depth: np.ndarray
    ) -> CandidateExtractionResult:
        """ROI 切り出し済みの前景マスクと Depth から候補集合を作る。

        Preconditions:
            `mask` は `roi_depth` と同じ shape の bool 配列であり、いずれも
            コンストラクタで受け取った `roi` で切り出し済みである（ROI
            ローカル座標）。両配列を変更しない。

        Postconditions:
            候補が1つも見つからなくても例外を送出せず、`candidates=()` /
            `rejections=()`（該当理由が無ければ）を返す（要件 3.5）。
            返す `Candidate.cx_px` / `cy_px` / `bbox_px` は**全画面座標**
            （ROI オフセット加算済み。要件 2.5）。

        Args:
            mask: ROI 切り出し済みの前景マスク（bool 配列）。
            roi_depth: ROI 切り出し済みの uint16 raw depth 配列。`mask` と
                同じ shape であること。

        Returns:
            粗いふるいを通過した候補と、除外理由ごとの件数。
        """
        roi_height_px, roi_width_px = mask.shape

        found = mask_ops.components(mask, self._max_candidates)

        candidates: list[Candidate] = []
        rejection_counts: dict[RejectReason, int] = {}

        for component in found:
            reason = self._coarse_reject_reason(
                component, roi_width_px=roi_width_px, roi_height_px=roi_height_px
            )
            if reason is not None:
                rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
                continue

            candidates.append(self._to_candidate(component, roi_depth))

        rejections = tuple(
            CandidateRejection(reason=reason, count=rejection_counts[reason])
            for reason in _COARSE_REJECT_ORDER
            if reason in rejection_counts
        )
        return CandidateExtractionResult(
            candidates=tuple(candidates), rejections=rejections
        )

    def _coarse_reject_reason(
        self, component: Component, *, roi_width_px: int, roi_height_px: int
    ) -> RejectReason | None:
        """距離非依存の粗いふるいを適用し、除外理由（無ければ `None`）を返す。

        判定順序（面積 → ROI 端接触）に規範的な意味は無いが、1候補につき
        除外理由を1つだけに決める必要があるため、この順序で固定する
        （小さすぎる/大きすぎる候補が同時に ROI 端に接する場合、面積の
        理由を優先する）。
        """
        if component.area_px < self._min_area_px:
            return RejectReason.TOO_SMALL_PX
        if component.area_px > self._max_area_px:
            return RejectReason.TOO_LARGE_PX
        if self._touches_roi_edge(
            component, roi_width_px=roi_width_px, roi_height_px=roi_height_px
        ):
            return RejectReason.TOUCHES_ROI_EDGE
        return None

    @staticmethod
    def _touches_roi_edge(
        component: Component, *, roi_width_px: int, roi_height_px: int
    ) -> bool:
        """`component` の外接矩形が ROI 切り出し済み配列の境界に接するか。

        ROI ローカル座標（オフセット加算前）で判定する。ROI 境界に接する
        候補は、実物体が ROI の外側へはみ出している可能性があり、その
        大きさ・重心が信頼できない（モジュール docstring 参照）。
        """
        return (
            component.x_px == 0
            or component.y_px == 0
            or component.x_px + component.w_px == roi_width_px
            or component.y_px + component.h_px == roi_height_px
        )

    def _to_candidate(self, component: Component, roi_depth: np.ndarray) -> Candidate:
        """粗いふるいを通過した連結成分を、全画面座標の `Candidate` へ変換する。"""
        valid_depth_px = _count_valid_depth_in_bbox(roi_depth, component)
        mask_score = _mask_score(valid_depth_px, component.area_px)

        return Candidate(
            cx_px=component.cx_px + self._roi_x_px,
            cy_px=component.cy_px + self._roi_y_px,
            bbox_px=(
                component.x_px + self._roi_x_px,
                component.y_px + self._roi_y_px,
                component.w_px,
                component.h_px,
            ),
            area_px=component.area_px,
            valid_depth_px=valid_depth_px,
            mask_score=mask_score,
        )


def _count_valid_depth_in_bbox(roi_depth: np.ndarray, component: Component) -> int:
    """`component` の外接矩形内にある有効 Depth 画素数を数える。

    `sensing_foundation.INVALID_DEPTH_RAW`（公開定数）を基準にした
    `raw != INVALID_DEPTH_RAW` を、bbox 領域全体へネイティブな NumPy 配列
    演算として適用する（`numpy.vectorize` は使わない。
    `detection/masks/depth_band.py` と同じ性能上の理由）。
    """
    bbox_depth = roi_depth[
        component.y_px : component.y_px + component.h_px,
        component.x_px : component.x_px + component.w_px,
    ]
    valid = bbox_depth != INVALID_DEPTH_RAW
    return int(np.count_nonzero(valid))


def _mask_score(valid_depth_px: int, area_px: int) -> float:
    """`valid_depth_px / area_px` を `[0.0, 1.0]` へクランプする（モジュール
    docstring「`mask_score` の定義」参照）。

    `area_px` は `mask_ops.components()` が返す連結成分の面積であり、粗い
    ふるいを通過した時点で `min_area_px > 0` を満たすため 0 にはならない
    が、念のためゼロ除算を避ける。
    """
    if area_px <= 0:
        return 0.0
    score = valid_depth_px / area_px
    return max(0.0, min(1.0, score))
