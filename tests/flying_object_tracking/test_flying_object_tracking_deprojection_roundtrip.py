"""逆投影の往復を合成入力で検証する（タスク 3.2）。

`PointEstimator`（タスク 3.1、`src/flying_object_tracking/projection.py`。
本タスクでは変更しない）を、検出（`Detector`/`CandidateExtractor`）を
経由せず `Candidate` を直接手で構築して呼び出し、合成器
（`tests/flying_object_tracking/synthetic.py`、タスク 1.5）が forward
projection で置いた既知の3D位置が、逆投影によって許容誤差内で復元される
ことを固定する。design.md「L5: 逆投影 → PointEstimator」・
「PointEstimator / アルゴリズム上の約束」、要件 2.5, 5.1, 5.8, 11.2 に対応する。

固定する内容:

- **距離 × 画像上の位置 × 対象物直径のマトリクス**（3×3×2=18通り、全通り）。
  画像上の位置は中心1点＋オフセット2点（符号が逆の2方向）を含み、
  `ppx_px`/`ppy_px` の減算やその符号を取り違えるピンホール式のバグは
  中心だけのテストでは見えない、という古典的な盲点を塞ぐ
- **ROI（処理対象範囲）を変えても復元される3D位置が変わらないこと**
  （要件 2.5）。`PointEstimator.estimate()` が受け取る `Candidate.cx_px` /
  `cy_px` / `bbox_px` は既に全画面座標であり（design.md
  「Detector / Invariants」）、`Roi.x_px` / `y_px` / `width_px` /
  `height_px` は `PointEstimator` 内では一切参照されない
  （`z_min_mm` / `z_max_mm` の距離帯判定にのみ使われる）。したがって
  同一の `Candidate`/`CaptureFrame` を異なる `Roi`（画素矩形が異なる、
  あるいは z 帯の幅が異なる）で処理しても、復元される
  `CameraPoint.x_mm/y_mm/z_mm` は完全に同一であるはずであり、これを
  直接アサーションで固定する
- 全ての復元点で `CoordinateFrame.CAMERA` であること

**許容誤差の根拠**（`TOL_MM = 1.0`）: このテストファイルの
`DEPTH_SCALE_MM = 1.0`（1 raw count = 1 mm）のもとでは、代表距離の mm 化は
`raw = round(z_mm / depth_scale_mm)` の丸めにより最大 ±0.5mm の誤差を
生む（`synthetic.py` の `raw_from_z_mm` と `projection.py` が呼ぶ
`sensing_foundation.depth_raw_to_mm` の組み合わせ）。この誤差が
`x_mm`/`y_mm` へ伝播する量は、ピンホール式の性質上
`|x_mm 誤差| = |(u_px - ppx_px) / fx_px| * |z_mm 誤差|` で頭打ちになり、
本テストの画素オフセットの最大値（220px、比 220/600 ≈ 0.367）と
組み合わせても 0.367 * 0.5mm ≈ 0.18mm を超えない。距離入力にわざと
半端な値（例: 549.7mm）を選び、この丸め誤差が実際に発生する経路を
毎ケース通す。`TOL_MM = 1.0` は理論最大誤差（約 0.18mm）に対して
5倍以上の余裕を持たせつつ、mm オーダーの実バグ（符号取り違え・
オフセット未加算・スケール未適用等）は数百 mm 単位でずれるため確実に
検出できる、小さく閉じた値である。

**合成器と `PointEstimator` の規約一致について**: `synthetic.py`
（タスク 1.5、境界外・本タスクでは変更しない）は、`deproject_pixel` の
厳密な逆演算として画素中心規約（`+0.5` 補正なし）で forward projection を
実装済みとレビュー済みである。本ファイルの全テストは、その forward
projection で置いた3D点を `PointEstimator`（`deproject_pixel` を呼ぶ）で
復元するという往復であるため、もし両者の規約が食い違っていれば
（例えばどちらかが `+0.5` を加える／`ppx_px`/`ppy_px` の符号や適用順を
誤る）、本ファイルの各アサーションが必ず失敗する。全ケースが成功する
ことをもって、この規約一致を確認する（規約不一致を発見した場合は
`synthetic.py`/`fakes.py` 側を本タスクで修正せず、ステータスレポートで
報告する——両ファイルは本タスクの境界外）。

クロス Spec 契約テスト（`m1-prediction-validation` の
`tests/m1_validation/test_deprojection_contract.py`、要件 1.10）は
本タスクの対象外であり、ここでは実装しない。
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

from flying_object_tracking.config import ObjectModelConfig, ProjectionConfig
from flying_object_tracking.projection import PointEstimator
from flying_object_tracking.types import Candidate, CameraPoint, CoordinateFrame, Roi

INVALID = 0  # sensing_foundation.INVALID_DEPTH_RAW と同値
WIDTH_PX = 640
HEIGHT_PX = 480
FX_PX = 600.0
FY_PX = 600.0
PPX_PX = 320.0
PPY_PX = 240.0
DEPTH_SCALE_MM = 1.0  # 1 raw count = 1 mm。丸め誤差の見積もりを単純にするため。

TOL_MM = 1.0
"""往復で許容する誤差（mm）。根拠はモジュール docstring「許容誤差の根拠」参照。"""

# ROI の距離帯（下記マトリクスの全距離を余裕を持って包含する）。
ROI_Z_MIN_MM = 400.0
ROI_Z_MAX_MM = 4600.0

# --------------------------------------------------------------------------
# マトリクス条件
# --------------------------------------------------------------------------

# 距離: わざと端数を持たせ、raw への丸め誤差（最大 ±0.5mm）が実際に発生する
# 経路を毎ケース通す（モジュール docstring「許容誤差の根拠」参照）。
DISTANCES_MM: dict[str, float] = {
    "near": 549.7,
    "mid": 1998.3,
    "far": 4201.6,
}

# 画像上の位置: 主点（中心）に加え、符号の異なる2つのオフセットを画素単位で
# 指定する。中心だけのテストでは `ppx_px`/`ppy_px` の減算漏れ・符号取り違えが
# 見えないため、正負両方向のオフセットを含める（タスクの意図そのもの）。
POSITION_OFFSETS_PX: dict[str, tuple[float, float]] = {
    "center": (0.0, 0.0),
    "corner_neg": (-220.0, -150.0),
    "corner_pos": (220.0, 150.0),
}

# 対象物直径: 小・大の2値。直径は逆投影の精度そのものには影響しない
# （代表 z_mm と画素重心だけで決まる）が、`expected_diameter_px` /
# `SIZE_MISMATCH` 判定を極端側でも誤って発火させないことを併せて確認する。
DIAMETERS_MM: dict[str, float] = {
    "small": 45.0,
    "large": 90.0,
}


def _intrinsics() -> CameraIntrinsics:
    return CameraIntrinsics(
        width_px=WIDTH_PX,
        height_px=HEIGHT_PX,
        fx_px=FX_PX,
        fy_px=FY_PX,
        ppx_px=PPX_PX,
        ppy_px=PPY_PX,
        model="none",
        coeffs=(0.0, 0.0, 0.0, 0.0, 0.0),
    )


def _profile(intrinsics: CameraIntrinsics) -> StreamProfile:
    return StreamProfile(
        width_px=WIDTH_PX,
        height_px=HEIGHT_PX,
        fps=30,
        depth_scale_mm=DEPTH_SCALE_MM,
        color_enabled=False,
        intrinsics=intrinsics,
    )


def _frame(depth: np.ndarray, profile: StreamProfile, *, t_capture_ms: float) -> CaptureFrame:
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


def _roi(
    *,
    z_min_mm: float | None,
    z_max_mm: float | None,
    x_px: int = 0,
    y_px: int = 0,
    width_px: int = WIDTH_PX,
    height_px: int = HEIGHT_PX,
) -> Roi:
    return Roi(
        x_px=x_px,
        y_px=y_px,
        width_px=width_px,
        height_px=height_px,
        z_min_mm=z_min_mm,
        z_max_mm=z_max_mm,
    )


def _estimator(*, diameter_mm: float, roi: Roi) -> PointEstimator:
    return PointEstimator(
        object_model=ObjectModelConfig(diameter_mm=diameter_mm),
        projection=ProjectionConfig(),
        roi=roi,
    )


def _bbox_around(
    cx_px: float, cy_px: float, radius_px: float, margin_px: int = 4
) -> tuple[int, int, int, int]:
    x0 = max(0, int(cx_px - radius_px) - margin_px)
    y0 = max(0, int(cy_px - radius_px) - margin_px)
    x1 = min(WIDTH_PX, int(cx_px + radius_px) + margin_px + 1)
    y1 = min(HEIGHT_PX, int(cy_px + radius_px) + margin_px + 1)
    return x0, y0, x1 - x0, y1 - y0


def _build_candidate_and_frame(
    synthetic_module,
    *,
    x_mm: float,
    y_mm: float,
    z_mm: float,
    diameter_mm: float,
    t_ms: float,
) -> tuple[CaptureFrame, Candidate]:
    """既知の3D点を合成器の forward projection で塊にし、対応する `Candidate`
    を検出を経由せず直接構築する（本タスクの前提: 「検出を経由せず候補を
    直接与えて逆投影」）。

    背景を `INVALID`（0）で埋めることで、bbox 内の有効画素が塊画素だけに
    限定され、代表値が背景に汚染されない（解析解との厳密な比較が可能になる）。
    """
    intrinsics = _intrinsics()
    profile = _profile(intrinsics)

    u_px, v_px = synthetic_module.project_to_pixel(intrinsics, x_mm, y_mm, z_mm)
    diam_px = synthetic_module.expected_diameter_px(intrinsics, diameter_mm, z_mm)
    radius_px = diam_px / 2.0

    point = synthetic_module.TrajectoryPoint(t_ms=t_ms, x_mm=x_mm, y_mm=y_mm, z_mm=z_mm)
    depth = synthetic_module.make_depth_frame(
        profile, point, diameter_mm, background_raw=INVALID
    )
    frame = _frame(depth, profile, t_capture_ms=t_ms)

    bbox_px = _bbox_around(u_px, v_px, radius_px)
    area_px = int(math.pi * radius_px**2)
    candidate = Candidate(
        cx_px=u_px,
        cy_px=v_px,
        bbox_px=bbox_px,
        area_px=area_px,
        valid_depth_px=0,
        mask_score=1.0,
    )
    return frame, candidate


def _matrix_cases() -> list:
    cases = []
    for dist_label, z_mm in DISTANCES_MM.items():
        for pos_label, (offset_u_px, offset_v_px) in POSITION_OFFSETS_PX.items():
            for diam_label, diameter_mm in DIAMETERS_MM.items():
                # x_mm/y_mm は「画素オフセットを z_mm 倍率で3D空間へ引き戻した値」
                # として定義する（= u_px = ppx_px + x_mm/z_mm*fx_px の逆算）。
                # こうすることで距離が変わっても同じ画角比のオフセットになり、
                # どの距離でもフレーム内に収まる画像上の位置を保証できる。
                x_mm = offset_u_px / FX_PX * z_mm
                y_mm = offset_v_px / FY_PX * z_mm
                case_id = f"{dist_label}-{pos_label}-{diam_label}"
                cases.append(
                    pytest.param(x_mm, y_mm, z_mm, diameter_mm, id=case_id)
                )
    return cases


# --------------------------------------------------------------------------
# マトリクス: 距離 × 画像上の位置 × 対象物直径（3×3×2=18通り、全通り）
# --------------------------------------------------------------------------


class TestDeprojectionRoundTripMatrix:
    @pytest.mark.parametrize("x_mm, y_mm, z_mm, diameter_mm", _matrix_cases())
    def test_recovers_known_3d_point_within_tolerance(
        self, synthetic_module, x_mm: float, y_mm: float, z_mm: float, diameter_mm: float
    ) -> None:
        frame, candidate = _build_candidate_and_frame(
            synthetic_module,
            x_mm=x_mm,
            y_mm=y_mm,
            z_mm=z_mm,
            diameter_mm=diameter_mm,
            t_ms=1234.5,
        )
        estimator = _estimator(
            diameter_mm=diameter_mm,
            roi=_roi(z_min_mm=ROI_Z_MIN_MM, z_max_mm=ROI_Z_MAX_MM),
        )

        result = estimator.estimate(frame, candidate)

        assert isinstance(result, CameraPoint), (
            f"投入値 x_mm={x_mm}, y_mm={y_mm}, z_mm={z_mm}, diameter_mm={diameter_mm} "
            f"に対して CameraPoint ではなく {result!r} が返った"
            "（SIZE_MISMATCH/PointFailure 等。マトリクスの前提が崩れている）"
        )
        assert result.frame == CoordinateFrame.CAMERA
        assert result.x_mm == pytest.approx(x_mm, abs=TOL_MM)
        assert result.y_mm == pytest.approx(y_mm, abs=TOL_MM)
        assert result.z_mm == pytest.approx(z_mm, abs=TOL_MM)


# --------------------------------------------------------------------------
# ROI（処理対象範囲）を変えても復元される3D位置が変わらない（要件 2.5）
# --------------------------------------------------------------------------


class TestRoiInvarianceOfRecoveredPosition:
    def test_different_roi_pixel_windows_yield_identical_position(
        self, synthetic_module
    ) -> None:
        """`Roi` の画素矩形（`x_px`/`y_px`/`width_px`/`height_px`）を変えても、
        同一の `Candidate`/`CaptureFrame` に対する復元3D位置は完全に同一になる。

        `Candidate.cx_px`/`cy_px`/`bbox_px` は既に全画面座標であり
        （design.md「Detector / Invariants」、要件 2.5）、`PointEstimator`
        は `Roi` から `z_min_mm`/`z_max_mm` しか読まない（`projection.py`
        の `__init__` 参照）。したがって画素矩形が異なる2つの `Roi` で
        同じ候補を処理しても、座標基準（結果の x_mm/y_mm/z_mm）は変化しない
        はずである。
        """
        x_mm, y_mm, z_mm = -300.4, 220.6, 1999.1
        diameter_mm = 65.0
        frame, candidate = _build_candidate_and_frame(
            synthetic_module,
            x_mm=x_mm,
            y_mm=y_mm,
            z_mm=z_mm,
            diameter_mm=diameter_mm,
            t_ms=777.0,
        )

        roi_full_frame = _roi(
            z_min_mm=ROI_Z_MIN_MM, z_max_mm=ROI_Z_MAX_MM, x_px=0, y_px=0,
            width_px=WIDTH_PX, height_px=HEIGHT_PX,
        )
        roi_small_offset_window = _roi(
            z_min_mm=ROI_Z_MIN_MM, z_max_mm=ROI_Z_MAX_MM, x_px=150, y_px=100,
            width_px=200, height_px=200,
        )

        result_full = _estimator(diameter_mm=diameter_mm, roi=roi_full_frame).estimate(
            frame, candidate
        )
        result_windowed = _estimator(
            diameter_mm=diameter_mm, roi=roi_small_offset_window
        ).estimate(frame, candidate)

        assert isinstance(result_full, CameraPoint)
        assert isinstance(result_windowed, CameraPoint)
        assert result_full.x_mm == result_windowed.x_mm
        assert result_full.y_mm == result_windowed.y_mm
        assert result_full.z_mm == result_windowed.z_mm
        # 完全一致の確認に加え、投入した3D位置そのものも許容誤差内で復元
        # されていることを固定する（往復としての完了状態）。
        assert result_full.x_mm == pytest.approx(x_mm, abs=TOL_MM)
        assert result_full.y_mm == pytest.approx(y_mm, abs=TOL_MM)
        assert result_full.z_mm == pytest.approx(z_mm, abs=TOL_MM)

    def test_different_roi_z_bands_yield_identical_position(self, synthetic_module) -> None:
        """`Roi` の距離帯（z 帯）の幅・位置を変えても（点を含む範囲である限り）、
        復元される3D位置は変わらない。

        z 帯は距離帯判定（手順7. 範囲外なら `OUT_OF_DEPTH_BAND`）にのみ
        使われ、`x_mm`/`y_mm`/`z_mm` の算出そのものには影響しない
        （`projection.py` の `estimate()` 手順1〜6は `roi` を参照しない）。
        """
        x_mm, y_mm, z_mm = 400.3, -260.7, 3000.9
        diameter_mm = 65.0
        frame, candidate = _build_candidate_and_frame(
            synthetic_module,
            x_mm=x_mm,
            y_mm=y_mm,
            z_mm=z_mm,
            diameter_mm=diameter_mm,
            t_ms=42.0,
        )

        roi_narrow_band = _roi(z_min_mm=2900.0, z_max_mm=3100.0)
        roi_wide_band = _roi(z_min_mm=100.0, z_max_mm=6000.0, x_px=200, y_px=150, width_px=100, height_px=100)

        result_narrow = _estimator(diameter_mm=diameter_mm, roi=roi_narrow_band).estimate(
            frame, candidate
        )
        result_wide = _estimator(diameter_mm=diameter_mm, roi=roi_wide_band).estimate(
            frame, candidate
        )

        assert isinstance(result_narrow, CameraPoint)
        assert isinstance(result_wide, CameraPoint)
        assert result_narrow.x_mm == result_wide.x_mm
        assert result_narrow.y_mm == result_wide.y_mm
        assert result_narrow.z_mm == result_wide.z_mm
