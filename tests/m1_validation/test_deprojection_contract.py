"""★ クロス Spec 契約: 上流2経路の逆投影が完全に一致する（タスク 2.4 / 要件 1.10）。

**なぜここでしか検証できないのか。**

`flying_object_tracking`（候補から3次元点を復元する経路）と
`world_frame_calibration`（床平面推定のために探索範囲を逆投影する経路）は、
どちらも `sensing_foundation.geometry` の
`depth_raw_to_mm` / `is_valid_depth` / `deproject_pixel` に乗る前提で書かれて
いる。しかし**両方を import する正当な理由を持つのは本 Spec だけ**である
——上流2 Spec は互いを知らないし、知る必要もない。したがって「2つの経路が
同じ答えを出す」ことを確かめられる場所は、継ぎ目を持つ本 Spec しかない。

**ずれた場合に何が起きるか。**

2経路がずれても、症状は「予測が落下地点を外す」としか見えない。ずれは
**投擲群に共通する偏り**として現れるため、誤差の帰属（要件 6）は
それを較正のずれとも観測ノイズとも区別できず、**「予測が悪い」という単一の
症状に潰れる**。`docs/requirements.md §6.2` が警告している失敗そのものである。
だから**許容差を置かない**——浮動小数の厳密比較にする。「ほぼ一致」を許すと、
その「ほぼ」が丸ごと共通偏りとして実測へ乗る。

**比較の仕方。**

同一の `CaptureFrame`（同じ生の Depth 配列・同じ内部パラメータ・同じ
Depth スケール）を両経路へ通し、同じ画素の3次元点を突き合わせる。
生の値から始めるので、**奥行きスケールの適用位置**（検出側は代表値算出後に
1回、較正側は取得直後に1回）の食い違いもここで出る。

⚠️ **本テストは上流の内部モジュールを import する。**
どちらの逆投影経路も上流の `__all__` に無いためである
（`flying_object_tracking.projection.PointEstimator` /
`world_frame_calibration.deproject.deproject_region`）。**これは本体側の
境界規則を緩めるものではない**——本体で上流基盤へ触れてよいのは接点モジュール
だけ、という規則はそのまま守られている（`test_m1_seam.py` /
`test_m1_upstream.py` が静的に固定している）。上流が逆投影経路を公開入口へ
出したら、本テストはそちら経由へ差し替えること（tasks.md の Implementation
Notes に申し送り済み）。
"""

from __future__ import annotations

from collections.abc import Iterator

import numpy as np
import pytest

from flying_object_tracking import (
    CameraPoint,
    Candidate,
    ObjectModelConfig,
    ProjectionConfig,
    Roi,
)
from flying_object_tracking.projection import PointEstimator
from prediction_core import SourceKind
from sensing_foundation import (
    INVALID_DEPTH_RAW,
    CameraIntrinsics,
    CaptureFrame,
    StreamProfile,
    TimestampDomain,
)
from world_frame_calibration.deproject import deproject_region
from world_frame_calibration.types import PixelRegion
from world_frame_calibration.upstream import collect_depth

WIDTH_PX = 64
HEIGHT_PX = 48

#: 主点を**整数画素**に置く。整数の主点画素で `x_mm == 0.0` になることが、
#: 「画素中心の規約に `+0.5` を足していない」ことの検査になる（両経路とも）。
PPX_PX = 32.0
PPY_PX = 24.0

INTRINSICS = CameraIntrinsics(
    width_px=WIDTH_PX,
    height_px=HEIGHT_PX,
    fx_px=385.5,
    fy_px=384.25,
    ppx_px=PPX_PX,
    ppy_px=PPY_PX,
    model="brown_conrady",
    coeffs=(0.0, 0.0, 0.0, 0.0, 0.0),
)

#: 検出側の絞り込みを一切効かせない設定。**本テストが見たいのは逆投影の
#: 数値だけ**であり、候補の妥当性判定はここでの関心ではない。
PERMISSIVE_OBJECT_MODEL = ObjectModelConfig(
    diameter_mm=65.0,
    min_scale=0.0,
    max_scale=1.0e12,
    min_area_px=0,
    max_area_px=10**9,
)

#: 有効画素1つで代表値が確定するようにする（トリムも下限も効かせない）。
#: こうすると代表 raw 値が入力そのものになり、比較が逆投影の式だけを見る。
EXACT_PROJECTION = ProjectionConfig(
    min_valid_depth_px=1,
    depth_trim_ratio=0.0,
    max_depth_spread_mm=1.0e12,
)

WIDE_ROI = Roi(
    x_px=0,
    y_px=0,
    width_px=WIDTH_PX,
    height_px=HEIGHT_PX,
    z_min_mm=None,
    z_max_mm=None,
)


class _SingleFrameSource:
    """`collect_depth()` が必要とする最小の入力元（`frames()` だけを持つ）。"""

    def __init__(self, frame: CaptureFrame) -> None:
        self._frame = frame

    def frames(self) -> Iterator[CaptureFrame]:
        yield self._frame


def _profile(depth_scale_mm: float) -> StreamProfile:
    return StreamProfile(
        width_px=WIDTH_PX,
        height_px=HEIGHT_PX,
        fps=30,
        depth_scale_mm=depth_scale_mm,
        color_enabled=False,
        intrinsics=INTRINSICS,
    )


def _frame_with_one_valid_pixel(
    *, u_px: int, v_px: int, raw: int, depth_scale_mm: float
) -> CaptureFrame:
    """(u, v) だけが有効な生 Depth 配列を持つフレームを作る。

    他の画素は `INVALID_DEPTH_RAW` にする。**両経路とも「無効は埋めない」**
    ので、有効画素が1つだけなら代表値も逆投影対象も一意に定まる。
    """
    depth = np.full((HEIGHT_PX, WIDTH_PX), INVALID_DEPTH_RAW, dtype=np.uint16)
    depth[v_px, u_px] = raw
    depth.setflags(write=False)
    return CaptureFrame(
        index=0,
        seq=0,
        t_capture_ms=0.0,
        device_timestamp_ms=None,
        timestamp_domain=TimestampDomain.UNKNOWN,
        capture_latency_ms=None,
        depth=depth,
        profile=_profile(depth_scale_mm),
        source=SourceKind.SIMULATED,
        dropped_before=0,
        gap_before=0,
    )


def _tracking_path(frame: CaptureFrame, *, u_px: int, v_px: int):
    """検出・追跡側の逆投影経路（`PointEstimator.estimate`）を1回通す。"""
    estimator = PointEstimator(PERMISSIVE_OBJECT_MODEL, EXACT_PROJECTION, WIDE_ROI)
    candidate = Candidate(
        # 逆投影に渡る画素座標はここ（重心）である。有効 Depth 画素の位置とは
        # 独立に指定できるので、比較したい画素をそのまま指定する。
        cx_px=float(u_px),
        cy_px=float(v_px),
        bbox_px=(u_px, v_px, 1, 1),
        area_px=100,
        valid_depth_px=1,
        mask_score=1.0,
    )
    return estimator.estimate(frame, candidate)


def _calibration_path(frame: CaptureFrame, *, u_px: int, v_px: int):
    """較正側の逆投影経路（`collect_depth` → `deproject_region`）を1回通す。

    生の値から mm への換算も**上流の取得アダプタに行わせる**。ここを自分で
    換算してしまうと、奥行きスケールの適用位置の食い違いを見逃す。
    """
    image = collect_depth(_SingleFrameSource(frame), frame_count=1)
    points_mm, _pixels = deproject_region(
        image, PixelRegion(x0_px=u_px, y0_px=v_px, x1_px=u_px + 1, y1_px=v_px + 1)
    )
    return points_mm


#: 比較する入力の組。境界と、丸めが効きやすい値を含める。
CASES = [
    pytest.param(32, 24, 1000, 1.0, id="principal-point"),
    pytest.param(0, 0, 1000, 1.0, id="corner"),
    pytest.param(63, 47, 1000, 1.0, id="far-corner"),
    pytest.param(17, 29, 1, 1.0, id="smallest-valid-raw"),
    pytest.param(17, 29, 65535, 1.0, id="largest-uint16-raw"),
    # 0.1 は2進で厳密に表せない。スケールの適用位置が違うと下位桁がずれる。
    pytest.param(17, 29, 3, 0.1, id="scale-not-exactly-representable"),
    pytest.param(17, 29, 12345, 0.001, id="millimetre-scale"),
    pytest.param(5, 41, 7, 0.25, id="power-of-two-scale"),
]


class TestDeprojectionContract:
    """同一入力に対し、2経路が**完全に同一の値**を返す（要件 1.10）。"""

    @pytest.mark.parametrize(("u_px", "v_px", "raw", "depth_scale_mm"), CASES)
    def test_both_paths_agree_exactly(
        self, u_px: int, v_px: int, raw: int, depth_scale_mm: float
    ) -> None:
        frame = _frame_with_one_valid_pixel(
            u_px=u_px, v_px=v_px, raw=raw, depth_scale_mm=depth_scale_mm
        )

        tracking = _tracking_path(frame, u_px=u_px, v_px=v_px)
        calibration = _calibration_path(frame, u_px=u_px, v_px=v_px)

        assert isinstance(tracking, CameraPoint), tracking
        assert calibration.shape == (1, 3)

        # **許容差を置かない。** 「ほぼ一致」を許すと、その差が丸ごと
        # 投擲群の共通偏りとして実測へ乗る。
        assert (tracking.x_mm, tracking.y_mm, tracking.z_mm) == (
            calibration[0, 0],
            calibration[0, 1],
            calibration[0, 2],
        )

    def test_principal_point_maps_to_the_optical_axis_in_both_paths(self) -> None:
        """整数の主点画素で `x = y = 0`（両経路とも `+0.5` 補正を足していない）。

        画素中心の規約が片方だけずれていると、**全画素に一定の横ずれ**が乗る。
        投擲群の共通偏りとして現れるので、これも「予測が悪い」に潰れる。
        """
        frame = _frame_with_one_valid_pixel(
            u_px=int(PPX_PX), v_px=int(PPY_PX), raw=1000, depth_scale_mm=1.0
        )

        tracking = _tracking_path(frame, u_px=int(PPX_PX), v_px=int(PPY_PX))
        calibration = _calibration_path(frame, u_px=int(PPX_PX), v_px=int(PPY_PX))

        assert isinstance(tracking, CameraPoint)
        assert (tracking.x_mm, tracking.y_mm) == (0.0, 0.0)
        assert (calibration[0, 0], calibration[0, 1]) == (0.0, 0.0)

    @pytest.mark.parametrize("depth_scale_mm", [1.0, 0.1, 0.001])
    def test_depth_scale_is_applied_the_same_way_in_both_paths(
        self, depth_scale_mm: float
    ) -> None:
        """奥行きスケールの適用位置が効く値で一致する。

        検出側は代表値を出してから1回換算し、較正側は取得直後に画素ごとに
        1回換算する。**適用位置が違っても順序が同じなら同じ値になる**——
        違ってしまえば下位桁がずれ、距離に比例した共通偏りになる。
        """
        frame = _frame_with_one_valid_pixel(
            u_px=17, v_px=29, raw=3, depth_scale_mm=depth_scale_mm
        )
        tracking = _tracking_path(frame, u_px=17, v_px=29)
        calibration = _calibration_path(frame, u_px=17, v_px=29)

        assert isinstance(tracking, CameraPoint)
        assert tracking.z_mm == calibration[0, 2]


class TestInvalidDepthIsAgreedUpon:
    """無効な生の奥行き値の扱いが両経路で一致する（要件 1.10 の境界）。"""

    def test_invalid_raw_yields_no_point_in_either_path(self) -> None:
        """無効値そのもの（`INVALID_DEPTH_RAW`）では、どちらも点を作らない。

        片方が 0 を「距離 0mm の点」として通すと、**カメラ位置に張り付いた
        偽の観測点**が混ざる。除外の規約がずれること自体が事故である。
        """
        depth = np.full((HEIGHT_PX, WIDTH_PX), INVALID_DEPTH_RAW, dtype=np.uint16)
        depth.setflags(write=False)
        frame = CaptureFrame(
            index=0,
            seq=0,
            t_capture_ms=0.0,
            device_timestamp_ms=None,
            timestamp_domain=TimestampDomain.UNKNOWN,
            capture_latency_ms=None,
            depth=depth,
            profile=_profile(1.0),
            source=SourceKind.SIMULATED,
            dropped_before=0,
            gap_before=0,
        )

        tracking = _tracking_path(frame, u_px=17, v_px=29)
        calibration = _calibration_path(frame, u_px=17, v_px=29)

        assert not isinstance(tracking, CameraPoint), tracking
        assert calibration.shape == (0, 3)

    def test_the_smallest_valid_raw_is_a_point_in_both_paths(self) -> None:
        """無効値の**隣**（raw=1）は、どちらも点として通す。

        境界がどちらか一方だけ1つずれていると、暗い画素の扱いが食い違う。
        """
        frame = _frame_with_one_valid_pixel(
            u_px=17, v_px=29, raw=1, depth_scale_mm=1.0
        )
        tracking = _tracking_path(frame, u_px=17, v_px=29)
        calibration = _calibration_path(frame, u_px=17, v_px=29)

        assert isinstance(tracking, CameraPoint)
        assert calibration.shape == (1, 3)


class TestSharedPrimitiveIsActuallyUsed:
    """前提（逆投影の基本演算の一元化）が landing していることを確かめる。

    ここが崩れていると、上の一致検査は「2つの独立実装がたまたま同じ式で
    書かれている」ことしか示さなくなる。
    """

    def test_invalid_depth_marker_is_the_shared_one(self) -> None:
        from flying_object_tracking import projection as fot_projection

        assert fot_projection.INVALID_DEPTH_RAW is INVALID_DEPTH_RAW

    def test_both_modules_reference_the_shared_deprojection(self) -> None:
        from flying_object_tracking import projection as fot_projection
        from sensing_foundation import deproject_pixel
        from world_frame_calibration import deproject as wfc_deproject

        assert fot_projection.deproject_pixel is deproject_pixel
        assert wfc_deproject.deproject_pixel is deproject_pixel
