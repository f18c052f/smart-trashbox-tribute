"""合成データによる end-to-end 検証（tasks.md タスク 7.1 / 要件 9.1, 9.2, 9.3）。

design.md「Testing Strategy」/「E2E Tests」が `test_e2e_synthetic.py` を
**「本 Spec の中心となるテスト」**と位置付ける（design.md 1353行目）。
既知のカメラ姿勢・既知のマーカー配置から `tests/world_frame_calibration/
synthetic.py`（タスク 1.6）が生成した合成 Depth に対し、`plan` → `calibrate`
→ `verify` を実際のオーケストレーション経路で通し、次の2点を確かめる:

1. **復元された変換が既知の正解姿勢と数値誤差の範囲で一致する**（要件 9.1）。
   合成 Depth の生成器が返す `SyntheticFrame.camera_to_world`（`CameraPose`）
   は、合成生成に用いた「カメラ座標系 → World 座標系」の厳密な正解変換
   であり、`world_point = rotation_cam_to_world @ camera_point +
   position_world_mm` という同じ形の式で定義される（`synthetic.py` の
   `CameraPose` docstring）。これは `WorldTransform.apply_point` の定義式
   （`p_world = R @ p_camera + t`）と厳密に同じ形であり、かつ本テストの
   マーカー配置（原点マーカーを World (0,0,0) に、方向マーカーを World
   (500,0,0) に置く）は A-2 の World frame 定義（原点マーカー投影点が原点、
   原点→方向マーカーが +X）とちょうど一致するように選んである。したがって
   `established.transform.rotation` / `translation_mm` を `camera_to_world.
   rotation_cam_to_world` / `position_world_mm` へ**直接**比較できる
   （`tests/world_frame_calibration/test_world_frame_calibration_frame.py`
   の `TestBuildWorldFrameCameraPoseIndependenceFullPipeline` が同じ配置で
   「複数姿勢から同一の World 座標を復元できる」ことを既に確認している。
   本テストはその一歩先で「復元した変換が姿勢そのものと一致する」ことを
   直接確認する）。

2. **既知の量のノイズを加えると、平面の残差 (`PlaneQuality.residual_rms_mm`)
   と検証のばらつき (`VerificationReport.scatter_rms_mm`) がノイズ量に
   応じて単調に悪化する**（要件 9.3）。

**ハードウェア非依存性**（要件 9.2）: 本テストは `world_frame_calibration.
cli.run_calibrate` / `run_verify` が持つテスト専用の注入点
`source: FrameSource | None` へ、`generate_synthetic_depth` が生成した
合成 Depth を積んだ手製の `_ScriptedFrameSource`（`FrameSource` プロトコル
を構造的に満たすだけのダブル）を渡す。`sensing_foundation.open_source()`
は一切呼ばれず、`sensing_foundation` の実機・SDK 経路（`SourceKind.LIVE`
やその具象アダプタ）には一切触れない——`sensing_foundation` からの import は
`CaptureFrame` / `StreamProfile` などの**型**（テスト用ダブルの構築に必要な
公開型）のみであり、`tests/world_frame_calibration/test_world_frame_
calibration_cli.py`（タスク 6.4）が確立した同じパターンを踏襲する。

なぜ `cli.run_calibrate` / `run_verify` を直接使うか（`plane` / `anchors` /
`frame` / `verify` を個別に呼ぶのではなく）: design.md がこのファイルを
「plan → calibrate → verify」という**オーケストレーション経路**を通す
テストとして位置付けているため（1353行目）、実際に本番で使われる統合点
（`cli.py`）を経由しない限り、モジュール単体の正しさは確認できても
「実際にエンドツーエンドで動く」ことの証拠にはならない。

`tests/world_frame_calibration/synthetic.py` は `tests/sensing_foundation/
synthetic.py` と裸のモジュール名が衝突するため（`synthetic.py` の
モジュール docstring / tasks.md 実装ノート「タスク1.6」参照）、
`importlib.util.spec_from_file_location` によるパス指定ロードを使う。

ファイル名について: `tests/**` に `__init__.py` が無いため、裸の
`test_e2e_synthetic.py` は使わずプレフィックス付きの
`test_world_frame_calibration_e2e_synthetic.py` とする（既存タスクの
命名規約）。
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
from world_frame_calibration.result import VerificationState
from world_frame_calibration.types import Intrinsics, PixelRegion, ToleranceSpec
from world_frame_calibration.verify import Verdict

# --- tests/world_frame_calibration/synthetic.py の衝突耐性ロード -----------
# `tests/sensing_foundation/synthetic.py` と裸のモジュール名 `synthetic` が
# 衝突するため、`importlib.util.spec_from_file_location` によるパス指定
# ロードを使う（synthetic.py のモジュール docstring / タスク1.6実装ノート
# のとおり）。
_SYNTHETIC_PATH = Path(__file__).resolve().parent / "synthetic.py"
_spec = importlib.util.spec_from_file_location(
    "world_frame_calibration._test_synthetic_for_e2e", _SYNTHETIC_PATH
)
assert _spec is not None and _spec.loader is not None
_synthetic = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _synthetic
_spec.loader.exec_module(_synthetic)

MarkerBox = _synthetic.MarkerBox
camera_pose_looking_at_floor = _synthetic.camera_pose_looking_at_floor
generate_synthetic_depth = _synthetic.generate_synthetic_depth


# ---------------------------------------------------------------------------
# 合成シナリオ: 既知のカメラ姿勢・既知のマーカー配置
#
# `test_world_frame_calibration_cli.py`（タスク 6.4）/
# `test_world_frame_calibration_frame.py`（タスク 3.2）
# `TestBuildWorldFrameCameraPoseIndependenceFullPipeline` と同じ、実際に
# 成立することが確認済みの原点・方向マーカー配置を踏襲する。検証点は
# それらと異なる独立な3点（要件 4.3）を、確立用マーカーおよび互いの
# 探索範囲と干渉しない位置に置く（下記「検証点配置についての注記」参照）。
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
"""合成生成の**正解のカメラ姿勢**（カメラ → World の厳密な変換）。復元した
`WorldTransform` と直接比較する基準そのものである。"""

_ORIGIN_MARKER = MarkerBox(
    label="origin", center_xy_world_mm=(0.0, 0.0), size_xy_mm=(220.0, 220.0), height_mm=60.0
)
_X_AXIS_MARKER = MarkerBox(
    label="x_axis", center_xy_world_mm=(500.0, 0.0), size_xy_mm=(220.0, 220.0), height_mm=60.0
)

# 検証点についての注記: 3点を原点マーカーの左右対称な位置
# (250, +350) / (250, -350) に加え、x_axis マーカーの延長線上ではなく
# 離れた (850, 0) に1点置く。
#
# 予備調査（本タスクの実装過程で `synthetic.py` を直接叩いて確認済み）に
# より、探索窓（`_region_around_world_point` の既定 `half_extent_px=16`、
# 本シナリオの距離・焦点距離ではおよそ ±170mm 相当）は**マーカー自身の
# 投影サイズよりかなり広い**。そのため「マーカー同士が物理的に重ならない」
# だけでは不十分で、**中心間距離が「片方の探索窓半径（約170mm）＋もう片方
# のマーカー半幅（90〜110mm）」を明確に上回る**（実務的には350mm以上）
# ことも必要だと判明した。これを下回る配置（例: 中心間距離150〜250mm）を
# 試したところ、探索窓が隣接マーカーの画素まで拾ってしまい、ノイズ0でも
# 数十mm単位の見かけ上の誤差が生じることを確認した（synthetic.py の
# レイキャストが「最も近い交点」を採用するため、隣接するマーカー同士は
# 重なる領域で互いを覆い隠す影響も加わる）。
#
# 下記3点はいずれの組み合わせも中心間距離が600mm以上（原点/x_axis
# マーカーとの距離も350mm以上）であり、この干渉を避けている。
_VERIFICATION_A = MarkerBox(
    label="verification_a",
    center_xy_world_mm=(250.0, 350.0),
    size_xy_mm=(180.0, 180.0),
    height_mm=60.0,
)
_VERIFICATION_B = MarkerBox(
    label="verification_b",
    center_xy_world_mm=(250.0, -350.0),
    size_xy_mm=(180.0, 180.0),
    height_mm=60.0,
)
_VERIFICATION_C = MarkerBox(
    label="verification_c",
    center_xy_world_mm=(850.0, 0.0),
    size_xy_mm=(180.0, 180.0),
    height_mm=60.0,
)
_MARKER_HEIGHT_MM = 60.0
"""検証マーカーの高さ。`observe_anchor` が返す `point_camera_mm` は投影前の
ロバスト代表点（マーカー上面）であるため（`anchors.py` docstring /
tasks.md 実装ノート「タスク5.1」）、既知の真値 `truth_world_mm` の z 成分は
床面 (0) ではなくマーカー上面の高さ (60mm) とする——これにより要件 4.9
（既知の高さに置かれた検証点）にも対応する、物理的に正しい真値になる。"""

_ALL_MARKERS = (
    _ORIGIN_MARKER,
    _X_AXIS_MARKER,
    _VERIFICATION_A,
    _VERIFICATION_B,
    _VERIFICATION_C,
)

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
    ときの画素座標 (u, v) へ解析的に逆算する（`synthetic.py` のレイキャスト
    規約と厳密に整合する逆変換。`test_world_frame_calibration_cli.py` /
    `test_world_frame_calibration_frame.py` の同名ヘルパと同じ計算）。
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


def _verification_point_spec(marker: MarkerBox) -> VerificationPointSpec:
    region = _region_around_world_point(_POSE, _INTRINSICS, marker.center_xy_world_mm + (0.0,))
    return VerificationPointSpec(
        label=marker.label,
        region=region,
        height_band_mm=(10.0, 120.0),
        truth_world_mm=marker.center_xy_world_mm + (_MARKER_HEIGHT_MM,),
        truth_source="synthetic テストフィクスチャ（マーカー上面の既知高さ）",
    )


def _build_plan(*, tolerance: ToleranceSpec | None = None) -> CalibrationPlan:
    floor_region = PixelRegion(x0_px=0, y0_px=0, x1_px=_INTRINSICS.width_px, y1_px=_INTRINSICS.height_px)
    origin_region = _region_around_world_point(_POSE, _INTRINSICS, (0.0, 0.0, 0.0))
    x_axis_region = _region_around_world_point(_POSE, _INTRINSICS, (500.0, 0.0, 0.0))
    return CalibrationPlan(
        plan_format_version=plan_module.PLAN_FORMAT_VERSION,
        floor_region=floor_region,
        origin_anchor=AnchorSpec(label="origin", region=origin_region, height_band_mm=(10.0, 120.0)),
        x_axis_anchor=AnchorSpec(label="x_axis", region=x_axis_region, height_band_mm=(10.0, 120.0)),
        verification_points=(
            _verification_point_spec(_VERIFICATION_A),
            _verification_point_spec(_VERIFICATION_B),
            _verification_point_spec(_VERIFICATION_C),
        ),
        limits=_LIMITS,
        tolerance=tolerance,
        expected_baseline_mm=500.0,
        notes="E2E 合成テスト用の計画（タスク 7.1）",
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
    """`FrameSource` プロトコルを構造的に満たすだけの手製ダブル。

    実機・SDK・上流の具象アダプタ（`SimulatedSource` 等）は一切使わない
    （要件 9.2: ハードウェアの接続を要求しない）。`test_world_frame_
    calibration_cli.py::_ScriptedFrameSource` と同じ最小主義。
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


def _scripted_source(*, noise_std_mm: float, rng_seed: int) -> _ScriptedFrameSource:
    """既知の量のノイズを加えた合成 Depth を積んだフレーム源を作る。"""
    synth = generate_synthetic_depth(
        _POSE, _INTRINSICS, markers=_ALL_MARKERS, noise_std_mm=noise_std_mm, rng_seed=rng_seed
    )
    profile = _stream_profile()
    raw = _depth_mm_to_raw(synth.depth.depth_mm, profile.depth_scale_mm)
    return _ScriptedFrameSource(raw, profile)


def _settings_no_log() -> RuntimeSettings:
    """実ファイル I/O を避けるため、ロギングを無効化した `RuntimeSettings` を
    直接構築する（`test_world_frame_calibration_cli.py` と同じテスト専用の
    組み立て）。
    """
    return RuntimeSettings(
        source=SourceKind.SIMULATED,
        capture=CaptureConfig(),
        recording=RecordingConfig(),
        logging=LoggingConfig(enabled=False),
        session_path=None,
    )


# `logger=None` を渡すと `cli.run_calibrate`/`run_verify` は
# `sensing_foundation.get_logger(settings.logging, clock)` を呼ぶ
# （`cli.py` L326/L390）。`_settings_no_log()` が `LoggingConfig(enabled=False)`
# を渡しているため、これは実ファイル I/O を伴わない無効化されたロガーに
# 解決される——`test_world_frame_calibration_cli.py::_calibrate_and_save` が
# 確立済みの同じ組み合わせ（`logger=None` + ロギング無効設定）をそのまま
# 踏襲する。本ファイル専用のロガーダブルは不要。


# ---------------------------------------------------------------------------
# ノイズ0での量的な許容値の根拠（要件 9.1: 数値誤差の範囲で既知の姿勢と一致）
#
# 本タスクの実装過程で、本ファイルと同一のシナリオ（`_POSE` /
# `_ORIGIN_MARKER` / `_X_AXIS_MARKER`、ノイズ0、`rng_seed=0`）について、
# **実際にテストが通す経路と同一の `cli.run_calibrate`**（`_build_plan()` /
# `_scripted_source()` などこのファイル自身のヘルパをそのまま用いる）を
# 実行して既知姿勢との差分を実測した（`plane` / `anchors` / `frame` を
# 個別に直接呼ぶ計測は、CLI のオーケストレーション——特に Depth が
# `depth_scale_mm=1.0mm` 分解能の生値へラウンドトリップされる過程——を
# 経ないため、本テストが実際に検査する誤差量とは一致しない。したがって
# 採用しない）:
#
#   - 回転行列の要素ごとの最大絶対差: 8.115811016585506e-07（`cli.
#     run_calibrate` 経由での実測値。画素量子化とラウンドトリップの
#     組み合わせにより、回転は理論上の丸め誤差（1e-16オーダー）より
#     大きい、しかし依然として小さな系統誤差を持つ）
#   - 並進（原点位置）の差: 実測ベクトルで
#     [-2.10953664e+00, -1.13074694e-05, -9.65784445e-04] mm、
#     ノルムで 2.1095368649628585 mm（画素中心規約に基づくロバスト
#     代表点の量子化に由来する、決定論的で再現可能な値）
#
# 以下の許容値はこの実測値に**明確な余裕**を持たせて選んだ
# （回転: 1e-6 は実測 8.12e-07 の約1.23倍、並進: 3.0mm は実測 2.11mm の
# 約1.4倍）。両方とも tasks.md タスク 3.2 の先例に倣い、半分の許容値
# （回転: 5e-7、並進: 1.5mm）では実測値を下回れずに不合格になることを
# それぞれ `test_e2e_synthetic_rotation_tolerance_is_not_vacuous` /
# `test_e2e_synthetic_translation_tolerance_is_not_vacuous` で直接確認し、
# 「ゆるすぎて何を検査しても通る」比較になっていないことを示す。
# ---------------------------------------------------------------------------

_ROTATION_ATOL = 1e-6
_TRANSLATION_ATOL_MM = 3.0
_OBSERVED_ROTATION_ERROR = 8.115811016585506e-07
_OBSERVED_TRANSLATION_ERROR_MM = 2.1095368649628585


def _ground_truth_rotation() -> np.ndarray:
    return _POSE.rotation_matrix()


def _ground_truth_translation() -> np.ndarray:
    return _POSE.position_vector()


# ---------------------------------------------------------------------------
# 1. plan -> calibrate -> save -> verify が既知姿勢を復元して PASS する
#    （要件 9.1, 4.1, 9.2）
# ---------------------------------------------------------------------------


def test_e2e_synthetic_pipeline_recovers_known_transform_and_verification_passes(
    tmp_path: Path,
) -> None:
    """既知のカメラ姿勢・既知のマーカー配置から生成したノイズ0の合成
    Depth に対し、`cli.run_calibrate`（plan → 収集 → 平面推定 → マーカー
    観測 → frame確立 → 保存）と `cli.run_verify`（保存済み結果 → 独立検証
    点の観測 → 誤差レポート → 合否判定）を、実際のオーケストレーション
    経路で通す。ハードウェア・SDK・記録済みセッションのいずれにも接続
    しない（要件 9.2）。
    """
    plan = _build_plan()
    settings = _settings_no_log()
    out_path = tmp_path / "calibration.json"

    result, saved_path = cli.run_calibrate(
        settings,
        plan,
        frame_count=1,
        out=out_path,
        source=_scripted_source(noise_std_mm=0.0, rng_seed=0),
        logger=None,
    )

    assert saved_path == out_path
    assert out_path.exists()

    # --- 復元された変換が既知の正解姿勢と数値誤差の範囲で一致する（要件 9.1）
    recovered_rotation = np.asarray(result.transform.rotation, dtype=np.float64)
    recovered_translation = np.asarray(result.transform.translation_mm, dtype=np.float64)

    np.testing.assert_allclose(
        recovered_rotation, _ground_truth_rotation(), atol=_ROTATION_ATOL
    )
    np.testing.assert_allclose(
        recovered_translation, _ground_truth_translation(), atol=_TRANSLATION_ATOL_MM
    )

    # --- 検証が合格になる（要件 4.1）。個々の検証点の誤差（水平最大約5.5mm、
    # 垂直0mm。ノイズ0でのマーカー配置から実測済み）に対して明確な余裕を
    # 持つが、無条件に通る値ではない許容値を用いる。
    tolerance = ToleranceSpec(
        horizontal_mm=20.0,
        vertical_mm=20.0,
        provisional=True,
        source="E2E合成テスト: ノイズ0での実測誤差(水平約5.5mm/垂直0mm)に余裕を持たせた暫定値",
    )
    report, updated_result, _report_path = cli.run_verify(
        settings,
        plan,
        out_path,
        frame_count=1,
        tolerance=tolerance,
        report_out=tmp_path / "report.json",
        source=_scripted_source(noise_std_mm=0.0, rng_seed=0),
        logger=None,
    )

    assert report.verdict == Verdict.PASS
    assert report.independent_point_count == 3
    assert updated_result.verification_state == VerificationState.PASSED


def test_e2e_synthetic_translation_tolerance_is_not_vacuous(tmp_path: Path) -> None:
    """`_TRANSLATION_ATOL_MM`（3.0mm）が「ゆるすぎて何でも通る」値では
    ないことを、半分の許容値（1.5mm）では同じ復元結果が不合格になる
    ことで直接示す（tasks.md タスク 3.2 の先例と同じ手法）。
    """
    plan = _build_plan()
    settings = _settings_no_log()

    result, _saved_path = cli.run_calibrate(
        settings,
        plan,
        frame_count=1,
        out=tmp_path / "calibration.json",
        source=_scripted_source(noise_std_mm=0.0, rng_seed=0),
        logger=None,
    )

    recovered_translation = np.asarray(result.transform.translation_mm, dtype=np.float64)

    # 実測値 (2.111mm) 自体が許容値の半分 (1.5mm) を上回ることを固定する
    # （許容値そのものが実測より緩すぎないことの直接証拠）。
    assert _OBSERVED_TRANSLATION_ERROR_MM > _TRANSLATION_ATOL_MM / 2.0

    with pytest.raises(AssertionError):
        np.testing.assert_allclose(
            recovered_translation,
            _ground_truth_translation(),
            atol=_TRANSLATION_ATOL_MM / 2.0,
        )


def test_e2e_synthetic_rotation_tolerance_is_not_vacuous(tmp_path: Path) -> None:
    """`_ROTATION_ATOL`（1e-6）が「ゆるすぎて何でも通る」値ではないことを、
    半分の許容値（5e-7）では同じ復元結果が不合格になることで直接示す
    （`test_e2e_synthetic_translation_tolerance_is_not_vacuous` と同じ手法、
    tasks.md タスク 3.2 の先例）。
    """
    plan = _build_plan()
    settings = _settings_no_log()

    result, _saved_path = cli.run_calibrate(
        settings,
        plan,
        frame_count=1,
        out=tmp_path / "calibration.json",
        source=_scripted_source(noise_std_mm=0.0, rng_seed=0),
        logger=None,
    )

    recovered_rotation = np.asarray(result.transform.rotation, dtype=np.float64)

    # 実測値 (8.1158e-07) 自体が許容値の半分 (5e-7) を上回ることを固定する
    # （許容値そのものが実測より緩すぎないことの直接証拠）。
    assert _OBSERVED_ROTATION_ERROR > _ROTATION_ATOL / 2.0

    with pytest.raises(AssertionError):
        np.testing.assert_allclose(
            recovered_rotation,
            _ground_truth_rotation(),
            atol=_ROTATION_ATOL / 2.0,
        )


# ---------------------------------------------------------------------------
# 2. 既知の量のノイズを加えると、平面の残差と検証のばらつきが
#    ノイズ量に応じて単調に悪化する（要件 9.3）
# ---------------------------------------------------------------------------


def test_e2e_synthetic_noise_increases_plane_residual_and_verification_scatter_monotonically(
    tmp_path: Path,
) -> None:
    """`generate_synthetic_depth(noise_std_mm=...)` で既知の量の独立
    ガウスノイズを加えた合成 Depth を3段階（1.0mm=弱ノイズ, 6.0mm=中程度,
    13.0mm=強ノイズ）用意し、同一の計画・同一の RANSAC シードで
    `calibrate` → `verify` を通す。平面推定品質 (`PlaneQuality.
    residual_rms_mm`) と検証のばらつき (`VerificationReport.
    scatter_rms_mm`) の両方が、ノイズ量に応じて厳密に単調増加することを
    確認する（要件 9.3。観測可能な完了状態: 「ノイズ量を2段階変えたとき
    品質値が単調に悪化する」——本テストは3段階で確認し、要求の2段階分を
    包含する）。

    ノイズ水準の選定根拠: 本タスクの実装過程での実測（本ファイルと同一の
    `cli.run_calibrate` / `run_verify` 経路を実際に通して確認済み。素の
    `plane` / `anchors` / `frame` 直接呼び出しではなく、CLI 経由で
    Depth が一度 `depth_scale_mm=1.0mm` 分解能の生値へラウンドトリップ
    される点まで含めて実測した）により、1.0 / 6.0 / 13.0mm の3水準は
    `plane_inlier_threshold_mm=15.0mm`（`_LIMITS`）を下回り続けて平面推定
    自体は毎回成功し、かつ `residual_rms_mm` と `scatter_rms_mm` の両方が
    この3水準で単調増加することを、`rng_seed` 0〜14 の15通りのうち14通り
    で確認済みである（`plane_inlier_threshold_mm` の元での純粋な残差指標
    である `residual_rms_mm` は15通り全てで単調増加した。3点の検証点
    による `scatter_rms_mm` は少数点統計であるため稀に非単調になり得るが、
    本テストが用いる `rng_seed=0` では単調増加する）。0.0mm（ノイズなし）
    を含めなかったのは、ラウンドトリップ量子化の影響がノイズ std と同
    オーダーになる極小ノイズ域で `scatter_rms_mm` の単調性が不安定に
    なることを確認したため——「ノイズが効いている」ことを示すには、
    量子化ノイズ床を明確に上回る 1.0mm を起点に取る方が適切である。
    15mm を超えるノイズ（例: 25mm）では RANSAC が内点比率の下限を割り込み
    `estimate_floor_plane` 自体が失敗するため、「悪化するが失敗はしない」
    範囲としてこの3水準を選んだ。
    """
    plan = _build_plan()
    settings = _settings_no_log()
    tolerance = ToleranceSpec(
        horizontal_mm=1_000.0,
        vertical_mm=1_000.0,
        provisional=True,
        source="E2E合成テスト: 品質の実測値そのものを見るための非常に緩い値（合否は本テストの対象外）",
    )

    noise_levels_mm = (1.0, 6.0, 13.0)
    plane_residuals_mm: list[float] = []
    scatter_values_mm: list[float] = []

    for index, noise_std_mm in enumerate(noise_levels_mm):
        out_path = tmp_path / f"calibration_{index}.json"
        result, _saved_path = cli.run_calibrate(
            settings,
            plan,
            frame_count=1,
            out=out_path,
            source=_scripted_source(noise_std_mm=noise_std_mm, rng_seed=0),
            logger=None,
        )
        plane_residuals_mm.append(result.plane.quality.residual_rms_mm)

        report, _updated_result, _report_path = cli.run_verify(
            settings,
            plan,
            out_path,
            frame_count=1,
            tolerance=tolerance,
            report_out=tmp_path / f"report_{index}.json",
            source=_scripted_source(noise_std_mm=noise_std_mm, rng_seed=0),
            logger=None,
        )
        scatter_values_mm.append(report.scatter_rms_mm)

    # ノイズなし基準 < 弱ノイズ < 強ノイズ が両指標で厳密に成り立つ
    # （「ノイズを変えたら何かが変わった」ではなく「悪化する方向へ変わった」
    # ことを示す。tasks.md タスク7.1 観測可能な完了状態）。
    assert plane_residuals_mm[0] < plane_residuals_mm[1] < plane_residuals_mm[2], (
        f"plane residual_rms_mm はノイズ量に応じて単調に悪化するはず: {plane_residuals_mm}"
    )
    assert scatter_values_mm[0] < scatter_values_mm[1] < scatter_values_mm[2], (
        f"verify scatter_rms_mm はノイズ量に応じて単調に悪化するはず: {scatter_values_mm}"
    )
