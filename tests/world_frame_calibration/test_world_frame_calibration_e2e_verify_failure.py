"""検証が本当に不合格を出せることの end-to-end 確認（tasks.md タスク 7.2 /
要件 4.4, 4.5, 4.6）。

「検証が本当に不合格を出せる」ことの確認が本 Spec の存在意義そのものである。

（tasks.md タスク 7.2 の明示的な指示により、この一文をそのまま docstring に
記す。requirements.md Requirement 4「独立検証ステップ ★」の Objective が
述べるとおり、本 Spec の価値は「変換できること」そのものではなく、変換が
正しいことを独立に確かめられることにある。タスク 7.1 の end-to-end テストが
確認したのは「合成入力に対して検証が PASS すること」だけであり、それだけでは
`verify_calibration` が常に PASS を返す trivial な実装であっても区別が
つかない。本タスクは、既知の量のバイアス/異常を実際のパイプライン
（合成 Depth 生成 → `cli.run_calibrate` → `cli.run_verify`）へ注入し、検証が
その異常を正しく FAIL として検出し、かつレポートの内訳
（`bias_mm` / `scatter_rms_mm` / `range_buckets`）がその異常の性質を正しく
指し示すことを確認する。これによって初めて「検証は本当に機能する検査で
あり、常に合格を返すだけの見せかけではない」ことが証明される。）

本ファイルは `tests/world_frame_calibration/test_world_frame_calibration_
e2e_synthetic.py`（タスク 7.1、2026-08-22 時点で着地済み）と同じ配線
（`cli.run_calibrate` / `cli.run_verify` を `_ScriptedFrameSource` 経由で
駆動する）を土台とし、そのシナリオ定数（カメラ姿勢・内部パラメータ・
マーカー配置・`PlanLimits`）をそのまま踏襲する。ただし本ファイルはタスク
7.1 のテストファイルを一切変更せず、また `tests/**` に `__init__.py` が
無いためタスク 7.1 のプライベートヘルパを import で共有せず、必要な分だけ
本ファイルに複製・改変する（`synthetic.py` モジュール docstring / tasks.md
実装ノート「タスク1.6」が要求する、本ファイル固有の複製方針）。

3種類の異常注入と、それぞれで確認する内容:

1. **床平面のオフセット**（`TestFloorPlaneOffset`）: キャリブレーション時点
   では基準どおりの床（World z=0）で生成するが、検証時点の合成 Depth では
   検証点マーカーの土台高さ（`MarkerBox.base_z_world_mm`）を一様に
   `_FLOOR_OFFSET_MM` だけ底上げして生成する——「設置後に床が変わった／
   キャリブレーション時と検証時で床の実測が食い違っていた」という典型的な
   実運用の失敗形態を模す。検証は `result.plane`（キャリブレーション時に
   確立した平面）を基準にマーカーの高さ帯を判定するため、この床オフセットは
   全独立点に一様な**鉛直（Z）方向のバイアス**として現れるはずである。

2. **原点マーカー観測のオフセット**（`TestOriginMarkerOffset`）: キャリブ
   レーション時点の合成 Depth で、原点マーカーの物理的な中心を +X 方向へ
   `_ORIGIN_OFFSET_MM` だけ実際にずらして生成する（計画ファイルの探索窓は
   従来どおり想定位置 (0,0,0) を中心に据えたままなので、依然としてこの窓の
   内側でマーカーを検出できる——「設置者がマーカーを意図した位置から
   ずれた場所に置いてしまった」を模す）。方向マーカーは変更しない。World
   frame の原点は「原点マーカーが実際に観測された点」として定義される
   ため、この原点のずれは frame 全体を平行移動させ、独立点全部に一様な
   **水平（X）方向のバイアス**として現れるはずである（±X 方向のみのずれ
   なので原点→方向マーカーの向き、つまりヨーは理論上変化しない）。

3. **遠方の検証点だけの誤差増大**（`TestDistanceDependentError`）: タスク
   7.1 と同一のシナリオでは、検証点 A・B（原点マーカーの左右対称位置）の
   カメラからの距離が約1971mm、検証点 C（x_axis 側の延長）が約2031mm
   であり、既定の距離帯境界（`DEFAULT_RANGE_BUCKET_EDGES_MM` の 1000mm刻み）
   ではちょうど A・B が `[1000, 2000)` 帯、C が `[2000, 3000)` 帯に分かれる
   （2026-08-22 実装時に幾何から解析的に確認した自然な分割であり、本ファイル
   のための特別な配置ではない）。検証時点の合成 Depth でのみ、遠い方の C
   の物理位置を大きくずらして生成し、A・B は無改変のまま残す。これは
   tasks.md タスク 7.2 が明示的に許容する注入方法（「遠方の検証点だけ誤差を
   大きくした入力」＝「距離に応じて誤差が増える」または「遠方の点だけに
   影響する」のいずれでもよい、との記述）のうち後者に当たる。

**読み分け規則との対応、および (1)(2) と (3) でアサーションの種類が
異なる理由**（`verify.VerificationStatistics` docstring / `verify.py`
モジュール docstring より）:

    バイアス支配なら座標系、ばらつき支配なら観測、遠方だけ大きいなら
    Depth の距離特性。

この読み分け規則は3つの独立したカテゴリを定める。(1) 床オフセットと
(2) 原点オフセットは、独立点**全部**に対して符号・大きさがほぼ一様な誤差を
注入する設計であるため、この2つについては「`bias_mm` が注入量を指し、
`scatter_rms_mm` は小さいまま保たれる」——すなわちタスク 5.2 が固定した
バイアス/ばらつき分離可能性が、単体テストの手作業注入だけでなく実際の
合成→キャリブレーション→検証という full pipeline を通しても成り立つ
ことを、統合レベルで確認する。

一方 (3) 遠方限定の誤差は、定義からして「独立点の一部だけに大きな誤差が
乗る」非一様な注入であり、これは上記の読み分け規則が言う「バイアス支配」
とも「ばらつき支配」（軸ごとの一様な偏差を除いた残差が小さいこと）とも
異なる**第三のカテゴリ**（「遠方だけ大きい」）である。したがって (3) では
「`scatter_rms_mm` が小さいまま保たれる」ことを主張しない
（実際、A・B は誤差ゼロに近く C だけ大きい状況で全体の `scatter_rms_mm` を
計算すると、C が平均から大きく外れるため点数の少なさも相まって
`scatter_rms_mm` 自体は決して小さくならない——これは実装の欠陥ではなく、
「一様バイアスでは説明できない非一様な誤差」という (3) の注入の本質を
数式的に正しく反映した結果である）。(3) で確認するのは、`range_buckets`
の内訳が「近い帯（A・B を含む `[1000,2000)`）は誤差が小さいまま、遠い帯
（C を含む `[2000,3000)`）だけ誤差が大きい」という**距離局所的な**信号を
正しく示すことであり、これは要件 4.5 が求める「検証点ごとのカメラからの
距離と誤差の対応」の報告そのものである。

**許容値/オフセット量の実測方針**（タスク 7.1 の先例を踏襲）: 本ファイルの
許容値・オフセット量は、いずれも本ファイル自身の `_build_plan()` /
`_scripted_source_with_markers()` などのヘルパを用いて**実際に
`cli.run_calibrate` / `cli.run_verify` を通した経路**で実測した値に基づく
（`plane` / `anchors` / `frame` / `verify` を個別に直接呼ぶ簡易計測は、CLI
のオーケストレーション——特に `depth_scale_mm=1.0mm` 分解能へのラウンド
トリップ——を経ないため、実際にテストが検査する誤差量と一致しない。タスク
7.1 の実装ノート参照）。各異常の期待バイアス・誤差量はコメントに実測値を
明記する。
"""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import numpy as np
import pytest
from sensing_foundation import (
    CameraIntrinsics,
    CaptureFrame,
    RuntimeSettings,
    SourceKind,
    StreamProfile,
    TimestampDomain,
)
from sensing_foundation.config import CaptureConfig, LoggingConfig, RecordingConfig

from world_frame_calibration import cli
from world_frame_calibration import plan as plan_module
from world_frame_calibration.plan import AnchorSpec, CalibrationPlan, PlanLimits, VerificationPointSpec
from world_frame_calibration.types import Intrinsics, PixelRegion, ToleranceSpec
from world_frame_calibration.verify import DEFAULT_RANGE_BUCKET_EDGES_MM, Verdict

# --- tests/world_frame_calibration/synthetic.py の衝突耐性ロード -----------
# `tests/sensing_foundation/synthetic.py` と裸のモジュール名 `synthetic` が
# 衝突するため（synthetic.py モジュール docstring / タスク1.6実装ノート）、
# `importlib.util.spec_from_file_location` によるパス指定ロードを使う。
# `sys.modules` に登録する名前は他のテストファイル（タスク7.1 の
# `test_world_frame_calibration_e2e_synthetic.py` 等）と衝突しない一意な
# 名前にする。
_SYNTHETIC_PATH = Path(__file__).resolve().parent / "synthetic.py"
_spec = importlib.util.spec_from_file_location(
    "world_frame_calibration._test_synthetic_for_verify_failure", _SYNTHETIC_PATH
)
assert _spec is not None and _spec.loader is not None
_synthetic = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _synthetic
_spec.loader.exec_module(_synthetic)

MarkerBox = _synthetic.MarkerBox
camera_pose_looking_at_floor = _synthetic.camera_pose_looking_at_floor
generate_synthetic_depth = _synthetic.generate_synthetic_depth


# ---------------------------------------------------------------------------
# 共有シナリオ定数（タスク 7.1 の検証済み配置をそのまま踏襲する）
# ---------------------------------------------------------------------------

_DEPTH_SCALE_MM = 1.0

_INTRINSICS = Intrinsics(
    width_px=300,
    height_px=240,
    fx_px=180.0,
    fy_px=180.0,
    ppx_px=150.0,
    ppy_px=120.0,
    model="none",
    coeffs=(0.0, 0.0, 0.0, 0.0, 0.0),
)

_POSE = camera_pose_looking_at_floor(
    position_world_mm=(250.0, 0.0, 2000.0), yaw_deg=0.0, pitch_deg=90.0
)
"""合成生成の正解カメラ姿勢。タスク 7.1 の `_POSE` と同一の配置。"""

_MARKER_HEIGHT_MM = 60.0
_HEIGHT_BAND_MM = (10.0, 120.0)

# 想定される（=計画ファイルが仮定する）物理配置。異常を注入する場合でも
# 計画ファイル側の探索窓・既知座標はこの「想定」を基準に据える——設置者や
# 検証時の物理条件が想定からずれても、計画ファイル自身は書き換わらない、
# という実運用を模す。
_NOMINAL_ORIGIN_XY = (0.0, 0.0)
_NOMINAL_X_AXIS_XY = (500.0, 0.0)
_VERIFICATION_A_XY = (250.0, 350.0)
_VERIFICATION_B_XY = (250.0, -350.0)
_VERIFICATION_C_XY = (850.0, 0.0)

_LIMITS = PlanLimits(
    min_inlier_points=500,
    min_inlier_ratio=0.5,
    plane_inlier_threshold_mm=15.0,
    min_incidence_angle_deg=10.0,
    min_baseline_mm=100.0,
    min_anchor_samples=30,
    rng_seed=11,
)


def _pixel_for_world_point(pose, intr: Intrinsics, world_point_mm) -> tuple[float, float]:
    """既知の World 座標点を、指定したカメラ姿勢・内部パラメータで撮影した
    ときの画素座標 (u, v) へ解析的に逆算する（タスク 7.1 の同名ヘルパと同じ
    計算。`synthetic.py` のレイキャスト規約と厳密に整合する逆変換）。
    """
    rotation = pose.rotation_matrix()
    position = pose.position_vector()
    diff = np.asarray(world_point_mm, dtype=np.float64) - position
    cam_vec = rotation.T @ diff
    d_cam = cam_vec / cam_vec[2]
    u = d_cam[0] * intr.fx_px + intr.ppx_px
    v = d_cam[1] * intr.fy_px + intr.ppy_px
    return float(u), float(v)


def _region_around_world_point(
    pose, intr: Intrinsics, world_point_mm, *, half_extent_px: float = 16.0
) -> PixelRegion:
    u, v = _pixel_for_world_point(pose, intr, world_point_mm)
    x0 = max(0, int(math.floor(u - half_extent_px)))
    y0 = max(0, int(math.floor(v - half_extent_px)))
    x1 = min(intr.width_px, int(math.ceil(u + half_extent_px)))
    y1 = min(intr.height_px, int(math.ceil(v + half_extent_px)))
    return PixelRegion(x0_px=x0, y0_px=y0, x1_px=x1, y1_px=y1)


def _verification_point_spec(label: str, center_xy_world_mm: tuple[float, float]) -> VerificationPointSpec:
    """計画ファイル側の検証点仕様を、**想定（=物理異常が無い場合の）**
    位置から組み立てる。探索窓・既知座標（`truth_world_mm`）のいずれも
    この想定位置を基準にする——実際にレンダリングする `MarkerBox` の位置を
    異常注入のために変えても、計画ファイル自身はこの想定を保ったまま
    変わらない。
    """
    region = _region_around_world_point(_POSE, _INTRINSICS, center_xy_world_mm + (0.0,))
    return VerificationPointSpec(
        label=label,
        region=region,
        height_band_mm=_HEIGHT_BAND_MM,
        truth_world_mm=center_xy_world_mm + (_MARKER_HEIGHT_MM,),
        truth_source="synthetic テストフィクスチャ（マーカー上面の既知高さ、想定配置）",
    )


def _build_plan(*, tolerance: ToleranceSpec | None = None) -> CalibrationPlan:
    """タスク 7.1 の `_build_plan()` と同じ想定配置から計画を組み立てる。
    計画ファイル側は常に「想定どおりの物理配置」を前提とし、本ファイルの
    各異常テストはレンダリングする `MarkerBox` の実際の位置だけを変える
    （計画ファイルは書き換えない——設置者・検証時の物理条件が計画作成時の
    想定からずれる、という実運用そのものを模す）。
    """
    floor_region = PixelRegion(x0_px=0, y0_px=0, x1_px=_INTRINSICS.width_px, y1_px=_INTRINSICS.height_px)
    origin_region = _region_around_world_point(_POSE, _INTRINSICS, _NOMINAL_ORIGIN_XY + (0.0,))
    x_axis_region = _region_around_world_point(_POSE, _INTRINSICS, _NOMINAL_X_AXIS_XY + (0.0,))
    return CalibrationPlan(
        plan_format_version=plan_module.PLAN_FORMAT_VERSION,
        floor_region=floor_region,
        origin_anchor=AnchorSpec(label="origin", region=origin_region, height_band_mm=_HEIGHT_BAND_MM),
        x_axis_anchor=AnchorSpec(label="x_axis", region=x_axis_region, height_band_mm=_HEIGHT_BAND_MM),
        verification_points=(
            _verification_point_spec("verification_a", _VERIFICATION_A_XY),
            _verification_point_spec("verification_b", _VERIFICATION_B_XY),
            _verification_point_spec("verification_c", _VERIFICATION_C_XY),
        ),
        limits=_LIMITS,
        tolerance=tolerance,
        expected_baseline_mm=500.0,
        notes="E2E 検証不合格確認テスト用の計画（タスク 7.2）",
    )


def _camera_intrinsics_from(intr: Intrinsics) -> CameraIntrinsics:
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


def _stream_profile() -> StreamProfile:
    return StreamProfile(
        width_px=_INTRINSICS.width_px,
        height_px=_INTRINSICS.height_px,
        fps=30,
        depth_scale_mm=_DEPTH_SCALE_MM,
        color_enabled=False,
        intrinsics=_camera_intrinsics_from(_INTRINSICS),
    )


def _depth_mm_to_raw(depth_mm: np.ndarray, depth_scale_mm: float) -> np.ndarray:
    raw = np.where(np.isnan(depth_mm), 0.0, np.round(depth_mm / depth_scale_mm))
    raw = np.clip(raw, 0, 65535)
    return raw.astype(np.uint16)


class _ScriptedFrameSource:
    """`FrameSource` プロトコルを構造的に満たすだけの手製ダブル（タスク 7.1
    の同名クラスと同じ最小主義。実機・SDK・上流の具象アダプタは一切使わない
    ——要件 9.2: ハードウェアの接続を要求しない）。
    """

    def __init__(self, raw_depth: np.ndarray, profile: StreamProfile) -> None:
        self._raw_depth = raw_depth
        self._profile = profile
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def frames(self):
        yield CaptureFrame(
            index=0,
            seq=0,
            t_capture_ms=0.0,
            device_timestamp_ms=None,
            timestamp_domain=TimestampDomain.UNKNOWN,
            capture_latency_ms=None,
            depth=self._raw_depth,
            profile=self._profile,
            source=SourceKind.SIMULATED,
            dropped_before=0,
            gap_before=0,
        )

    def __enter__(self) -> "_ScriptedFrameSource":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()


def _scripted_source_with_markers(
    markers: tuple, *, noise_std_mm: float = 0.0, rng_seed: int = 0
) -> _ScriptedFrameSource:
    """`markers` に**実際に**配置されたマーカー群から合成 Depth を生成した
    フレーム源を作る（タスク 7.1 の `_scripted_source` を、呼び出しごとに
    異なるマーカー集合を渡せるよう一般化したもの）。異常注入はこの `markers`
    引数へ、計画ファイル（`_build_plan()`）が想定する位置とは異なる実際の
    位置・土台高さを持つ `MarkerBox` を渡すことで行う。
    """
    synth = generate_synthetic_depth(
        _POSE, _INTRINSICS, markers=markers, noise_std_mm=noise_std_mm, rng_seed=rng_seed
    )
    profile = _stream_profile()
    raw = _depth_mm_to_raw(synth.depth.depth_mm, profile.depth_scale_mm)
    return _ScriptedFrameSource(raw, profile)


def _settings_no_log() -> RuntimeSettings:
    """実ファイル I/O を避けるため、ロギングを無効化した `RuntimeSettings` を
    直接構築する（タスク 7.1 / タスク 6.4 の先例と同じ組み立て）。
    """
    return RuntimeSettings(
        source=SourceKind.SIMULATED,
        capture=CaptureConfig(),
        recording=RecordingConfig(),
        logging=LoggingConfig(enabled=False),
        session_path=None,
    )


def _nominal_origin_marker() -> "MarkerBox":
    return MarkerBox(
        label="origin", center_xy_world_mm=_NOMINAL_ORIGIN_XY, size_xy_mm=(220.0, 220.0), height_mm=60.0
    )


def _nominal_x_axis_marker() -> "MarkerBox":
    return MarkerBox(
        label="x_axis", center_xy_world_mm=_NOMINAL_X_AXIS_XY, size_xy_mm=(220.0, 220.0), height_mm=60.0
    )


def _nominal_verification_markers(*, base_z_world_mm: float = 0.0) -> tuple:
    return (
        MarkerBox(
            label="verification_a",
            center_xy_world_mm=_VERIFICATION_A_XY,
            size_xy_mm=(180.0, 180.0),
            height_mm=60.0,
            base_z_world_mm=base_z_world_mm,
        ),
        MarkerBox(
            label="verification_b",
            center_xy_world_mm=_VERIFICATION_B_XY,
            size_xy_mm=(180.0, 180.0),
            height_mm=60.0,
            base_z_world_mm=base_z_world_mm,
        ),
        MarkerBox(
            label="verification_c",
            center_xy_world_mm=_VERIFICATION_C_XY,
            size_xy_mm=(180.0, 180.0),
            height_mm=60.0,
            base_z_world_mm=base_z_world_mm,
        ),
    )


_LOOSE_TOLERANCE = ToleranceSpec(
    horizontal_mm=1_000.0,
    vertical_mm=1_000.0,
    provisional=True,
    source="E2E検証不合格テスト: 実測値そのものを見るための非常に緩い値（この許容値では合否判定しない）",
)
"""合否判定には使わない、実測値の取得専用の緩い許容値。"""


def _calibrate(tmp_path: Path, name: str, markers: tuple) -> tuple:
    """`markers` を実際に配置した合成 Depth からキャリブレーションを実行し、
    保存済み結果とパスを返す（ノイズ0、決定論的）。
    """
    plan = _build_plan()
    settings = _settings_no_log()
    out_path = tmp_path / f"{name}_calibration.json"
    result, saved_path = cli.run_calibrate(
        settings,
        plan,
        frame_count=1,
        out=out_path,
        source=_scripted_source_with_markers(markers, noise_std_mm=0.0, rng_seed=0),
        logger=None,
    )
    return result, saved_path


def _verify(tmp_path: Path, name: str, saved_path: Path, markers: tuple, *, tolerance: ToleranceSpec):
    """`markers` を実際に配置した合成 Depth で検証を実行し、レポートを返す。"""
    plan = _build_plan()
    settings = _settings_no_log()
    report, updated_result, _report_path = cli.run_verify(
        settings,
        plan,
        saved_path,
        frame_count=1,
        tolerance=tolerance,
        report_out=tmp_path / f"{name}_report.json",
        source=_scripted_source_with_markers(markers, noise_std_mm=0.0, rng_seed=0),
        logger=None,
    )
    return report, updated_result


# ---------------------------------------------------------------------------
# 1. 床平面のオフセット: 検証時点の合成 Depth だけ、検証マーカーの土台高さを
#    一様に底上げする（要件 4.4, 4.6）
# ---------------------------------------------------------------------------

_FLOOR_OFFSET_MM = 30.0
"""検証時点の「床」が calibrate 時点の想定より 30mm 高い、という注入量。

実測根拠: この注入により生じる鉛直バイアスの理論値は注入量そのもの
（30mm）である——`observe_anchor` は `result.plane`（calibrate 時に確立した
平面、床 z=0 を基準）に対する符号付き距離で高さ帯を判定するため、検証時の
マーカー土台が 30mm 高ければ観測される `point_camera_mm` の高さ（Z 成分）も
その分だけ高く測定される。本ファイルの実装過程で `cli.run_calibrate` /
`cli.run_verify` を実際に通して確認したところ、`bias_mm` は
`(-4.04, 0.0, 30.0)` であり、`bias_mm[2]` は理論値 30mm と厳密に一致した
（本シナリオのカメラは床を真上から見込む配置 (`pitch_deg=90`) のため、
純粋な鉛直オフセットには視差由来の量子化誤差が一切乗らない）。水平方向
の `bias_mm[0]=-4.04mm` は無注入時点のベースライン実測（`bias_mm[0]=-2.72mm`、
下記参照）と同程度の小さな量に留まっており、床オフセットの注入によって
水平方向へ大きく漏れてはいないことを示す。許容値 5.0mm はこの理論値
30mm に対して明確に小さく、かつノイズ0のベースライン誤差（水平方向の点
ごとの誤差ノルムは約5.5mm（verification_a・b）／約3.9mm（verification_c）、
鉛直0mm。タスク 7.1 の実測と同水準）に対しては明確に大きい（「ゆるすぎて
何でも通る／厳しすぎて何でも落ちる」の両端を避けた値）。
"""

_FLOOR_OFFSET_TOLERANCE = ToleranceSpec(
    horizontal_mm=20.0,
    vertical_mm=5.0,
    provisional=True,
    source="E2E検証不合格テスト(床オフセット): ノイズ0のベースライン誤差(水平約5.5mm/鉛直0mm)"
    "を上回るが注入量30mmよりは明確に小さい暫定値",
)


class TestFloorPlaneOffset:
    """異常1: 検証時点の床が calibrate 時点の想定からずれている。"""

    def test_uniform_vertical_bias_causes_fail_and_bias_points_at_injected_amount(
        self, tmp_path: Path
    ) -> None:
        """calibrate は想定どおりの床（土台高さ0）で行い、verify だけ検証
        マーカーの土台高さを `_FLOOR_OFFSET_MM` だけ底上げした合成 Depth を
        与える。全独立点に一様な鉛直バイアスが乗り、`Verdict.FAIL` となり、
        `bias_mm[2]` が注入量を指し、`scatter_rms_mm` は小さいまま保たれる
        ことを確認する。
        """
        calibrate_markers = (_nominal_origin_marker(), _nominal_x_axis_marker())
        result, saved_path = _calibrate(tmp_path, "floor_offset", calibrate_markers)

        verify_markers = _nominal_verification_markers(base_z_world_mm=_FLOOR_OFFSET_MM)

        # --- まず実測値そのものを緩い許容値で取得する（合否判定はしない）
        loose_report, _ = _verify(
            tmp_path, "floor_offset_loose", saved_path, verify_markers, tolerance=_LOOSE_TOLERANCE
        )
        assert loose_report.independent_point_count == 3

        bias_x, bias_y, bias_z = loose_report.bias_mm
        # 鉛直方向のバイアスが注入量30mmに近い（数mm以内）ことを確認する。
        assert bias_z == pytest.approx(_FLOOR_OFFSET_MM, abs=2.0), (
            f"bias_mm[2] は注入した床オフセット {_FLOOR_OFFSET_MM}mm を指すはず: {loose_report.bias_mm}"
        )
        # 水平方向は注入していないため、無注入時点のベースライン水準
        # （実測: bias_mm[0]=-2.72mm、下記「無注入」参照）のまま。
        assert abs(bias_x) < 6.0, f"床オフセットは水平方向へ漏れないはず: bias_mm={loose_report.bias_mm}"
        assert abs(bias_y) < 6.0, f"床オフセットは水平方向へ漏れないはず: bias_mm={loose_report.bias_mm}"

        # --- バイアス/ばらつき分離可能性(タスク5.2)が full pipeline でも
        # 成り立つ: 一様バイアスを注入しても scatter は小さいまま
        # （タスク7.1のノイズ0ベースライン実測: 水平誤差ばらつき程度の
        # 数mmオーダーに留まる）。
        assert loose_report.scatter_rms_mm < 10.0, (
            f"一様バイアス注入下でも scatter_rms_mm は小さいまま保たれるはず: "
            f"{loose_report.scatter_rms_mm}"
        )

        # --- 本番の許容値で不合格になることを確認する（要件4.6）。
        report, updated_result = _verify(
            tmp_path, "floor_offset_strict", saved_path, verify_markers, tolerance=_FLOOR_OFFSET_TOLERANCE
        )
        assert report.verdict == Verdict.FAIL
        assert all(
            point.verdict == Verdict.FAIL for point in report.points if point.independent
        ), "全独立点に一様に乗ったバイアスなので、全独立点が不合格になるはず"
        assert report.tolerance is not None
        assert report.tolerance.provisional is True


# ---------------------------------------------------------------------------
# 2. 原点マーカー観測のオフセット: calibrate 時点だけ、原点マーカーの実際の
#    物理位置を +X 方向へずらす（要件 4.4, 4.6）
# ---------------------------------------------------------------------------

_ORIGIN_OFFSET_MM = 45.0
"""calibrate 時点で原点マーカーが想定位置 (0,0) から +X 方向へ実際に
45mm ずれて設置されていた、という注入量。

45mm は探索窓（`half_extent_px=16` の `_region_around_world_point` が作る
窓。本シナリオの距離・焦点距離ではおよそ ±170mm 相当。タスク 7.1 の実装
ノート参照）の内側に収まる——想定位置を中心にした計画ファイルの探索窓は
変えないまま、実際にずれたマーカーを依然として検出できる、という前提を
満たす。

理論値の導出: World frame の原点は「原点マーカーが実際に観測された点」
そのものとして定義される（`frame.build_world_frame`）。方向マーカーの位置
（想定どおり (500,0,0)）は変えないため、原点→方向マーカーの向きは
+X 方向のまま変わらない（ヨーは理論上不変）。したがって frame 全体が
World +X 方向へおよそ45mm 平行移動するのと等価であり、検証点の
`measured_world_mm` は想定（無注入）時点より X 方向へおよそ -45mm ずれる
（`error_mm = measured - truth`、`truth_world_mm` は想定どおりの物理配置を
基準に据えたまま変えていないため）。本ファイルの実装過程で実際に
`cli.run_calibrate`/`run_verify` を通して確認したところ、`bias_mm` は
`(-45.83, 0.0, 0.0)` であった（理論値 -45mm に対し、無注入時点でも生じる
観測・量子化由来の系統誤差（下記のベースライン実測 `bias_mm[0]=-2.72mm`と
同種のもの）が約0.83mm（1mm弱）上乗せされている）。
"""

_ORIGIN_OFFSET_TOLERANCE = ToleranceSpec(
    horizontal_mm=20.0,
    vertical_mm=20.0,
    provisional=True,
    source="E2E検証不合格テスト(原点オフセット): ノイズ0のベースライン誤差(水平約5.5mm)"
    "を上回るが注入量45mmよりは明確に小さい暫定値",
)


class TestOriginMarkerOffset:
    """異常2: calibrate 時点で原点マーカーが想定位置からずれて設置されていた。"""

    def test_uniform_horizontal_bias_causes_fail_and_bias_points_at_injected_amount(
        self, tmp_path: Path
    ) -> None:
        """calibrate 時点の合成 Depth でのみ、原点マーカーの実際の中心を
        想定位置 (0,0) から (`_ORIGIN_OFFSET_MM`, 0) へずらす。方向マーカー・
        検証点はいずれも想定どおりの物理位置のまま（＝計画ファイルの
        `truth_world_mm` と一致）で verify する。全独立点に一様な水平(X)
        バイアスが乗り、`Verdict.FAIL` となり、`bias_mm[0]` が注入量を
        （符号反転して、frame が原点方向へ平行移動する分だけ）指し、
        `scatter_rms_mm` は小さいまま保たれることを確認する。
        """
        shifted_origin = MarkerBox(
            label="origin",
            center_xy_world_mm=(_NOMINAL_ORIGIN_XY[0] + _ORIGIN_OFFSET_MM, _NOMINAL_ORIGIN_XY[1]),
            size_xy_mm=(220.0, 220.0),
            height_mm=60.0,
        )
        calibrate_markers = (shifted_origin, _nominal_x_axis_marker())
        result, saved_path = _calibrate(tmp_path, "origin_offset", calibrate_markers)

        verify_markers = _nominal_verification_markers()

        loose_report, _ = _verify(
            tmp_path, "origin_offset_loose", saved_path, verify_markers, tolerance=_LOOSE_TOLERANCE
        )
        assert loose_report.independent_point_count == 3

        bias_x, bias_y, bias_z = loose_report.bias_mm
        assert bias_x == pytest.approx(-_ORIGIN_OFFSET_MM, abs=3.0), (
            f"bias_mm[0] は注入した原点オフセット -{_ORIGIN_OFFSET_MM}mm を指すはず: "
            f"{loose_report.bias_mm}"
        )
        assert abs(bias_y) < 5.0, f"純X方向のオフセットはY方向へ漏れないはず: bias_mm={loose_report.bias_mm}"
        assert abs(bias_z) < 5.0, f"純X方向のオフセットはZ方向へ漏れないはず: bias_mm={loose_report.bias_mm}"

        assert loose_report.scatter_rms_mm < 10.0, (
            f"一様バイアス注入下でも scatter_rms_mm は小さいまま保たれるはず: "
            f"{loose_report.scatter_rms_mm}"
        )

        report, updated_result = _verify(
            tmp_path, "origin_offset_strict", saved_path, verify_markers, tolerance=_ORIGIN_OFFSET_TOLERANCE
        )
        assert report.verdict == Verdict.FAIL
        assert all(
            point.verdict == Verdict.FAIL for point in report.points if point.independent
        ), "全独立点に一様に乗ったバイアスなので、全独立点が不合格になるはず"
        assert report.tolerance is not None
        assert report.tolerance.provisional is True


# ---------------------------------------------------------------------------
# 3. 遠方の検証点だけの誤差増大: verify 時点の合成 Depth だけ、遠い方の
#    検証点 C の物理位置を大きくずらす（要件 4.5）
# ---------------------------------------------------------------------------

_FAR_POINT_OFFSET_MM = 120.0
"""verify 時点の検証点 C（カメラからの距離 約2031mm）だけを、想定物理位置
から水平方向へ 120mm ずらす注入量。検証点 A・B（距離 約1971mm）は無改変。

距離帯の内訳（`_LOOSE_TOLERANCE` で合否判定を伴わずに実測。本ファイルの
実装過程で実際に `cli.run_calibrate`/`run_verify` を通して確認済み）:

  - A・B の距離（約1971mm）は `DEFAULT_RANGE_BUCKET_EDGES_MM` の
    `[1000.0, 2000.0)` 帯に入る（1971 < 2000）。
  - C の距離（約2031mm）は `[2000.0, 3000.0)` 帯に入る（2031 >= 2000）。

これはタスク 7.1 の元シナリオがそのまま持つ幾何学的な性質であり、本
テストのために特別に調整した配置ではない。本ファイルの実装過程で実際に
`cli.run_calibrate`/`run_verify` を通して確認したところ、120mm の Y方向
ずれにより C の `error_norm_mm` は 102.79mm まで増大した（真上から見込む
カメラでも、マーカー上面の高さ 60mm ぶんの視差により、水平オフセットが
そのまま100%誤差に変換されるわけではなく、約85%（102.79/120）に幾何学的
に圧縮される）一方、A・B は無注入のベースライン誤差（5.53mm、タスク 7.1
の実測と同水準）のまま——したがって `[1000,2000)` 帯の
`mean_error_norm_mm`（5.53mm）は小さく、`[2000,3000)` 帯の
`mean_error_norm_mm`/`max_error_norm_mm`（いずれも102.79mm、独立点1点の
帯のため平均・最大が一致する）だけが約18.6倍まで大きく増える。
"""


class TestDistanceDependentError:
    """異常3: 遠方の検証点だけ、verify 時点の物理位置が想定からずれている。"""

    def test_only_far_range_bucket_shows_large_error(self, tmp_path: Path) -> None:
        """calibrate は完全に想定どおりの配置で行う。verify では検証点 C
        （遠方、距離帯 `[2000,3000)`）の物理位置だけを大きくずらし、A・B
        （近傍、距離帯 `[1000,2000)`）は無改変のまま与える。全体としては
        `Verdict.FAIL` になり、かつ `range_buckets` の内訳が「近い帯は誤差
        小、遠い帯だけ誤差大」という距離局所的な信号を示すことを確認する
        （要件 4.5）。

        本異常は (1)(2) と異なり一様バイアスではないため、`scatter_rms_mm`
        が小さいままであることは主張しない（モジュール docstring の
        「(1)(2) と (3) でアサーションの種類が異なる理由」参照）。
        """
        calibrate_markers = (_nominal_origin_marker(), _nominal_x_axis_marker())
        result, saved_path = _calibrate(tmp_path, "far_offset", calibrate_markers)

        near_ok_a = MarkerBox(
            label="verification_a",
            center_xy_world_mm=_VERIFICATION_A_XY,
            size_xy_mm=(180.0, 180.0),
            height_mm=60.0,
        )
        near_ok_b = MarkerBox(
            label="verification_b",
            center_xy_world_mm=_VERIFICATION_B_XY,
            size_xy_mm=(180.0, 180.0),
            height_mm=60.0,
        )
        far_shifted_c = MarkerBox(
            label="verification_c",
            center_xy_world_mm=(
                _VERIFICATION_C_XY[0],
                _VERIFICATION_C_XY[1] + _FAR_POINT_OFFSET_MM,
            ),
            size_xy_mm=(180.0, 180.0),
            height_mm=60.0,
        )
        verify_markers = (near_ok_a, near_ok_b, far_shifted_c)

        loose_report, _ = _verify(
            tmp_path, "far_offset_loose", saved_path, verify_markers, tolerance=_LOOSE_TOLERANCE
        )
        assert loose_report.independent_point_count == 3

        points_by_label = {point.label: point for point in loose_report.points}
        near_a_error = points_by_label["verification_a"].error_norm_mm
        near_b_error = points_by_label["verification_b"].error_norm_mm
        far_c_error = points_by_label["verification_c"].error_norm_mm

        assert near_a_error < 15.0, f"無改変の近傍点Aはベースライン誤差程度のはず: {near_a_error}"
        assert near_b_error < 15.0, f"無改変の近傍点Bはベースライン誤差程度のはず: {near_b_error}"
        assert far_c_error > 80.0, (
            f"注入量120mmに近い大きな誤差が遠方点Cにだけ乗るはず: {far_c_error}"
        )

        # --- 距離帯集計そのものが「近い帯は小さい、遠い帯だけ大きい」を
        # 示すことを確認する（要件4.5: 検証点ごとの距離と誤差の対応）。
        buckets_by_range = {(b.range_lo_mm, b.range_hi_mm): b for b in loose_report.range_buckets}
        near_bucket = buckets_by_range.get((1000.0, 2000.0))
        far_bucket = buckets_by_range.get((2000.0, 3000.0))
        assert near_bucket is not None, (
            f"検証点A・B（距離約1971mm）は[1000,2000)帯に入るはず: {loose_report.range_buckets}"
        )
        assert far_bucket is not None, (
            f"検証点C（距離約2031mm）は[2000,3000)帯に入るはず: {loose_report.range_buckets}"
        )
        assert near_bucket.point_count == 2
        assert far_bucket.point_count == 1
        assert near_bucket.mean_error_norm_mm < 15.0
        assert far_bucket.mean_error_norm_mm > 80.0
        assert far_bucket.mean_error_norm_mm > near_bucket.mean_error_norm_mm * 5.0, (
            "遠い帯だけ誤差が大きい、という距離局所的な信号がbucketの平均誤差に現れるはず: "
            f"near={near_bucket.mean_error_norm_mm}, far={far_bucket.mean_error_norm_mm}"
        )
        assert far_bucket.max_error_norm_mm > near_bucket.max_error_norm_mm * 5.0

        # --- 許容値による合否判定でも不合格になることを確認する（要件4.6）。
        # 遠方点Cの誤差だけで水平許容値を超えるように選んだ許容値。
        tolerance = ToleranceSpec(
            horizontal_mm=20.0,
            vertical_mm=20.0,
            provisional=True,
            source="E2E検証不合格テスト(距離依存誤差): 近傍点のベースライン誤差(数mm)"
            "を上回るが注入量120mmよりは明確に小さい暫定値",
        )
        report, _updated_result = _verify(
            tmp_path, "far_offset_strict", saved_path, verify_markers, tolerance=tolerance
        )
        assert report.verdict == Verdict.FAIL
        far_point = next(p for p in report.points if p.label == "verification_c")
        assert far_point.verdict == Verdict.FAIL
        near_point_a = next(p for p in report.points if p.label == "verification_a")
        near_point_b = next(p for p in report.points if p.label == "verification_b")
        assert near_point_a.verdict == Verdict.PASS, "無改変の近傍点は合格するはず"
        assert near_point_b.verdict == Verdict.PASS, "無改変の近傍点は合格するはず"
