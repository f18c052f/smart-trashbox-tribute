"""world_frame_calibration.cli（4サブコマンドの入口）の検証（tasks.md タスク 6.4 /
要件 4.1, 7.1, 7.3, 8.5, 10.3）。

design.md「System Flows」の2つの図（sequenceDiagram「キャリブレーション
（calibrate）」/ flowchart「検証（verify）」）が定めるオーケストレーション
順序と、design.md CLI「Validation」が定める `verify` の終了コード契約
（NOT_JUDGED は成功終了、FAIL のみ非ゼロ終了——判定していないことと不合格を
区別する）を実際に固定する。

本テストが確認すること（tasks.md タスク 6.4「観測可能な完了状態」）:

- `--help` に「既定の下限・許容値はすべて暫定であり実測で見直す」旨が現れる
- `calibrate` が合成入力から成功し、保存直後の結果が `NOT_VERIFIED` である
  こと（検証を実行しないこと）
- `calibrate` の各段の失敗がその場で停止し、後続段のログイベントが一切
  出ないこと
- `verify` の終了コード契約: 許容値なし（NOT_JUDGED）は 0、許容値ありで
  合格（PASS）は 0、許容値ありで不合格（FAIL）は非ゼロ
- `show` が保存済み結果の要約と整合性検査を行い、設定不一致を検出すること
- `compare` が2結果の差分を表示し、ストリーム設定の不一致を検出すること
- 失敗が理由と文脈を標準エラーへ出すこと
- 段階別ロギングイベント（`plane_fit` / `anchor_observe` / `frame_build` /
  `verify`）が `calibrate` stage で送出されること（要件 10.1〜10.3）

ハードウェア非依存の実現方法（要件 8.5, 9.2）: `sensing_foundation.
open_source()` の `SourceKind.SIMULATED` は `StreamProfile.intrinsics` を
常に `None` で構築するため（`sensing_foundation.source.
_build_simulated_profile`）、本 Spec の `upstream.to_intrinsics` はこれを
`CalibrationConfigError` として拒否する。したがって本テストは
`cli.run_calibrate` / `run_verify` / `run_show` が持つテスト専用の注入点
`source: FrameSource | None` を使い、`FrameSource` プロトコルを構造的に
満たすだけの手製ダブル（`_ScriptedFrameSource`）へ、
`tests/world_frame_calibration/synthetic.py`（タスク 1.6）が生成した合成
Depth を積む。実機・SDK・記録済みセッションのいずれも用いない。

ファイル名について: `tests/**` に `__init__.py` が無いため、裸の
`test_cli.py` は使わずプレフィックス付きの
`test_world_frame_calibration_cli.py` とする（既存タスクの命名規約）。

このテストファイルは `world_frame_calibration.cli` がまだ存在しない時点では
`ModuleNotFoundError` で失敗する（RED）。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from contextlib import contextmanager
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
from world_frame_calibration import result as result_module
from world_frame_calibration.errors import FailureReason
from world_frame_calibration.plan import AnchorSpec, CalibrationPlan, PlanLimits, VerificationPointSpec
from world_frame_calibration.result import VerificationState
from world_frame_calibration.types import Intrinsics, PixelRegion, ToleranceSpec
from world_frame_calibration.verify import Verdict

# --- tests/world_frame_calibration/synthetic.py の衝突耐性ロード -----------
# `tests/sensing_foundation/synthetic.py` と裸のモジュール名 `synthetic` が
# 衝突するため、`importlib.util.spec_from_file_location` によるパス指定
# ロードを使う（synthetic.py のモジュール docstring / 実装ノート「タスク1.6」
# のとおり）。
_SYNTHETIC_PATH = Path(__file__).resolve().parent / "synthetic.py"
_spec = importlib.util.spec_from_file_location(
    "world_frame_calibration._test_synthetic_for_cli", _SYNTHETIC_PATH
)
assert _spec is not None and _spec.loader is not None
_synthetic = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _synthetic
_spec.loader.exec_module(_synthetic)

MarkerBox = _synthetic.MarkerBox
camera_pose_looking_at_floor = _synthetic.camera_pose_looking_at_floor
generate_synthetic_depth = _synthetic.generate_synthetic_depth


# ---------------------------------------------------------------------------
# フルパイプライン合成シナリオ（test_world_frame_calibration_frame.py の
# `TestBuildWorldFrameCameraPoseIndependenceFullPipeline` と同じ、実際に
# 成立することが確認済みのパラメータを踏襲する）
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

_ORIGIN_MARKER = MarkerBox(
    label="origin", center_xy_world_mm=(0.0, 0.0), size_xy_mm=(220.0, 220.0), height_mm=60.0
)
_X_AXIS_MARKER = MarkerBox(
    label="x_axis", center_xy_world_mm=(500.0, 0.0), size_xy_mm=(220.0, 220.0), height_mm=60.0
)
_VERIFICATION_MARKER = MarkerBox(
    label="verification",
    center_xy_world_mm=(250.0, 350.0),
    size_xy_mm=(180.0, 180.0),
    height_mm=60.0,
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
    規約と厳密に整合する逆変換。`test_world_frame_calibration_frame.py` の
    同名ヘルパと同じ計算）。
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
    import math

    x0 = max(0, int(math.floor(u - half_extent_px)))
    y0 = max(0, int(math.floor(v - half_extent_px)))
    x1 = min(intr.width_px, int(math.ceil(u + half_extent_px)))
    y1 = min(intr.height_px, int(math.ceil(v + half_extent_px)))
    return PixelRegion(x0_px=x0, y0_px=y0, x1_px=x1, y1_px=y1)


def _build_plan(
    *,
    verification_points: tuple[VerificationPointSpec, ...],
    tolerance: ToleranceSpec | None = None,
) -> CalibrationPlan:
    floor_region = PixelRegion(x0_px=0, y0_px=0, x1_px=_INTRINSICS.width_px, y1_px=_INTRINSICS.height_px)
    origin_region = _region_around_world_point(_POSE, _INTRINSICS, (0.0, 0.0, 0.0))
    x_axis_region = _region_around_world_point(_POSE, _INTRINSICS, (500.0, 0.0, 0.0))
    return CalibrationPlan(
        plan_format_version=plan_module.PLAN_FORMAT_VERSION,
        floor_region=floor_region,
        origin_anchor=AnchorSpec(label="origin", region=origin_region, height_band_mm=(10.0, 120.0)),
        x_axis_anchor=AnchorSpec(label="x_axis", region=x_axis_region, height_band_mm=(10.0, 120.0)),
        verification_points=verification_points,
        limits=_LIMITS,
        tolerance=tolerance,
        expected_baseline_mm=500.0,
        notes="CLI テスト用の合成計画",
    )


def _verification_point_spec() -> VerificationPointSpec:
    region = _region_around_world_point(_POSE, _INTRINSICS, (250.0, 350.0, 0.0))
    return VerificationPointSpec(
        label="verification",
        region=region,
        height_band_mm=(10.0, 120.0),
        truth_world_mm=(250.0, 350.0, 0.0),
        truth_source="synthetic テストフィクスチャ",
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


def _stream_profile(*, width_px: int | None = None, depth_scale_mm: float | None = None) -> StreamProfile:
    intr = _INTRINSICS if width_px is None else Intrinsics(
        width_px=width_px,
        height_px=_INTRINSICS.height_px,
        fx_px=_INTRINSICS.fx_px,
        fy_px=_INTRINSICS.fy_px,
        ppx_px=_INTRINSICS.ppx_px,
        ppy_px=_INTRINSICS.ppy_px,
        model=_INTRINSICS.model,
        coeffs=_INTRINSICS.coeffs,
    )
    return StreamProfile(
        width_px=intr.width_px,
        height_px=intr.height_px,
        fps=30,
        depth_scale_mm=_DEPTH_SCALE_MM if depth_scale_mm is None else depth_scale_mm,
        color_enabled=False,
        intrinsics=_camera_intrinsics_from(intr),
    )


def _depth_mm_to_raw(depth_mm: np.ndarray, depth_scale_mm: float) -> np.ndarray:
    raw = np.where(np.isnan(depth_mm), 0.0, np.round(depth_mm / depth_scale_mm))
    raw = np.clip(raw, 0, 65535)
    return raw.astype(np.uint16)


class _ScriptedFrameSource:
    """`FrameSource` プロトコルを構造的に満たすだけの手製ダブル。

    実機・SDK・上流の具象アダプタ（`SimulatedSource` 等）は一切使わない。
    `cli._collect_depth_image` は `with source:`（`start`/`stop`）と
    `source.frames()` だけを使うため、それ以外のプロトコルメンバ
    （`kind` / `profile` / `stats`）は持たない
    （`test_world_frame_calibration_upstream.py` の `_FakeFrameSource` と
    同じ最小主義）。
    """

    def __init__(self, raw_depth: np.ndarray, profile: StreamProfile, frame_count: int) -> None:
        self._raw_depth = raw_depth
        self._profile = profile
        self._frame_count = frame_count
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def frames(self):
        for index in range(self._frame_count):
            yield CaptureFrame(
                index=index,
                seq=index,
                t_capture_ms=float(index) * 33.0,
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


def _scripted_source(
    *,
    markers=(_ORIGIN_MARKER, _X_AXIS_MARKER, _VERIFICATION_MARKER),
    profile: StreamProfile | None = None,
    frame_count: int = 1,
    rng_seed: int = 0,
) -> _ScriptedFrameSource:
    synth = generate_synthetic_depth(_POSE, _INTRINSICS, markers=markers, rng_seed=rng_seed)
    effective_profile = profile if profile is not None else _stream_profile()
    raw = _depth_mm_to_raw(synth.depth.depth_mm, effective_profile.depth_scale_mm)
    return _ScriptedFrameSource(raw, effective_profile, frame_count)


def _sparse_source(*, profile: StreamProfile | None = None) -> _ScriptedFrameSource:
    """有効画素をほぼ無効化した合成 Depth（`INSUFFICIENT_DEPTH_POINTS` を
    再現するための、平面推定の1段目で必ず失敗するダブル）。
    """
    synth = generate_synthetic_depth(
        _POSE, _INTRINSICS, markers=(), invalid_fraction=0.999, rng_seed=1
    )
    effective_profile = profile if profile is not None else _stream_profile()
    raw = _depth_mm_to_raw(synth.depth.depth_mm, effective_profile.depth_scale_mm)
    return _ScriptedFrameSource(raw, effective_profile, 1)


# ---------------------------------------------------------------------------
# ロギング: 記録専用スパイダブル（実際の I/O・スレッドは持たない。
# `test_world_frame_calibration_upstream.py` の `_SpyLogger` と同じ設計）
# ---------------------------------------------------------------------------


class _SpyStageLogger:
    def __init__(self, parent: "_SpyLogger", stage: str) -> None:
        self._parent = parent
        self._stage = stage

    def emit(self, event: str, /, **data: object) -> None:
        self._parent.emit(self._stage, event, **data)

    @contextmanager
    def timed(self, event: str, /, **data: object):
        self._parent.timed_calls.append((self._stage, event, dict(data)))
        yield


class _SpyLogger:
    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self.emitted: list[tuple[str, str, dict[str, object]]] = []
        self.timed_calls: list[tuple[str, str, dict[str, object]]] = []

    def emit(self, stage: str, event: str, /, **data: object) -> None:
        self.emitted.append((stage, event, dict(data)))

    def stage(self, stage: str) -> _SpyStageLogger:
        return _SpyStageLogger(self, stage)

    @contextmanager
    def timed(self, stage: str, event: str, /, **data: object):
        self.timed_calls.append((stage, event, dict(data)))
        yield

    def stats(self):  # pragma: no cover - 本テストは呼ばない
        raise NotImplementedError

    def close(self, timeout_ms: float = 1000.0) -> None:
        return None


def _settings_no_log() -> RuntimeSettings:
    """実ファイル I/O を避けるため、ロギングを無効化した `RuntimeSettings` を
    直接構築する（`resolve()` を経由しないテスト専用の組み立て）。
    """
    return RuntimeSettings(
        source=SourceKind.SIMULATED,
        capture=CaptureConfig(),
        recording=RecordingConfig(),
        logging=LoggingConfig(enabled=False),
        session_path=None,
    )


# ---------------------------------------------------------------------------
# --help: 暫定であることの明記（tasks.md タスク 6.4 / `tech.md` 開発標準1）
# ---------------------------------------------------------------------------


def test_top_level_help_mentions_defaults_are_provisional(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--help"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "既定の下限・許容値はすべて暫定であり実測で見直す" in captured.out


def test_calibrate_help_mentions_defaults_are_provisional(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["calibrate", "--help"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "既定の下限・許容値はすべて暫定であり実測で見直す" in captured.out


def test_verify_help_mentions_defaults_are_provisional(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["verify", "--help"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "既定の下限・許容値はすべて暫定であり実測で見直す" in captured.out


# ---------------------------------------------------------------------------
# calibrate: 収集 → 平面推定 → マーカー観測 → frame確立 → 保存。検証は未実施
# ---------------------------------------------------------------------------


def test_calibrate_produces_a_saved_not_verified_result_and_logs_each_stage(tmp_path: Path) -> None:
    plan = _build_plan(verification_points=())
    settings = _settings_no_log()
    source = _scripted_source()
    spy = _SpyLogger(enabled=True)
    out_path = tmp_path / "calibration.json"

    result, saved_path = cli.run_calibrate(
        settings, plan, frame_count=1, out=out_path, source=source, logger=spy
    )

    assert saved_path == out_path
    assert out_path.exists()
    assert result.verification is None
    assert result.verification_state == VerificationState.NOT_VERIFIED

    # 原点マーカーが World 原点へ、基線長が既知配置（500mm）に一致する。
    assert result.geometry.baseline_mm == pytest.approx(500.0, rel=0.05)

    reloaded = result_module.load_calibration(out_path)
    assert reloaded.calibration_id == result.calibration_id
    assert reloaded.verification_state == VerificationState.NOT_VERIFIED

    # 段階別ロギング（要件 10.1〜10.3）: calibrate stage に各段のイベントが
    # 出ており、失敗イベントは無い。
    stages_and_events = [(stage, event) for stage, event, _data in spy.emitted]
    assert ("calibrate", "plane_fit") in stages_and_events
    assert ("calibrate", "anchor_observe") in stages_and_events
    assert ("calibrate", "frame_build") in stages_and_events
    assert all(event != "failure" for _stage, event, _data in spy.emitted)
    # anchor_observe は origin / x_axis の2回。
    anchor_labels = [
        data["label"] for stage, event, data in spy.emitted if event == "anchor_observe"
    ]
    assert sorted(anchor_labels) == ["origin", "x_axis"]

    # 各段の所要時間が計測値として残る（要件 10.3）。
    for _stage, _event, data in spy.emitted:
        assert "duration_ms" in data
        assert data["duration_ms"] >= 0.0


def test_calibrate_stops_at_first_stage_failure_and_does_not_run_later_stages(tmp_path: Path) -> None:
    """平面推定が失敗すれば、マーカー観測・frame確立は一切実行されず、
    ログにも現れない（design.md「各段の失敗はその場で停止する」）。
    """
    plan = _build_plan(verification_points=())
    settings = _settings_no_log()
    source = _sparse_source()
    spy = _SpyLogger(enabled=True)

    with pytest.raises(cli.CalibrationFailure) as exc_info:
        cli.run_calibrate(
            settings, plan, frame_count=1, out=tmp_path / "unused.json", source=source, logger=spy
        )

    assert exc_info.value.reason == FailureReason.INSUFFICIENT_DEPTH_POINTS

    events = [event for _stage, event, _data in spy.emitted]
    assert "failure" in events
    assert "plane_fit" not in events
    assert "anchor_observe" not in events
    assert "frame_build" not in events
    # 保存されていないこと。
    assert not (tmp_path / "unused.json").exists()


# ---------------------------------------------------------------------------
# verify: 終了コード契約（NOT_JUDGED/PASS -> 0、FAIL -> 非ゼロ）
# ---------------------------------------------------------------------------


def _calibrate_and_save(tmp_path: Path, *, name: str = "calibration.json") -> Path:
    plan = _build_plan(verification_points=())
    settings = _settings_no_log()
    out_path = tmp_path / name
    cli.run_calibrate(
        settings, plan, frame_count=1, out=out_path, source=_scripted_source(), logger=None
    )
    return out_path


def test_verify_without_tolerance_is_not_judged_and_exits_zero(tmp_path: Path, capsys) -> None:
    calibration_path = _calibrate_and_save(tmp_path)
    plan_path = tmp_path / "plan.json"
    plan_module.save_plan(_build_plan(verification_points=(_verification_point_spec(),)), plan_path)

    returncode = cli.main(
        [
            "verify",
            "--plan",
            str(plan_path),
            "--calibration",
            str(calibration_path),
            "--frames",
            "1",
            "--no-log",
        ],
        source=_scripted_source(),
    )

    assert returncode == 0
    captured = capsys.readouterr()
    assert "not_judged" in captured.out

    reloaded = result_module.load_calibration(calibration_path)
    assert reloaded.verification_state == VerificationState.NOT_JUDGED


def test_verify_with_generous_tolerance_passes_and_exits_zero(tmp_path: Path, capsys) -> None:
    calibration_path = _calibrate_and_save(tmp_path)
    plan_path = tmp_path / "plan.json"
    plan_module.save_plan(_build_plan(verification_points=(_verification_point_spec(),)), plan_path)

    returncode = cli.main(
        [
            "verify",
            "--plan",
            str(plan_path),
            "--calibration",
            str(calibration_path),
            "--frames",
            "1",
            "--no-log",
            "--tolerance-horizontal-mm",
            "100.0",
            "--tolerance-vertical-mm",
            "100.0",
        ],
        source=_scripted_source(),
    )

    assert returncode == 0
    captured = capsys.readouterr()
    assert "pass" in captured.out

    reloaded = result_module.load_calibration(calibration_path)
    assert reloaded.verification_state == VerificationState.PASSED


def test_verify_with_tiny_tolerance_fails_and_exits_nonzero(tmp_path: Path, capsys) -> None:
    calibration_path = _calibrate_and_save(tmp_path)
    plan_path = tmp_path / "plan.json"
    plan_module.save_plan(_build_plan(verification_points=(_verification_point_spec(),)), plan_path)

    returncode = cli.main(
        [
            "verify",
            "--plan",
            str(plan_path),
            "--calibration",
            str(calibration_path),
            "--frames",
            "1",
            "--no-log",
            "--tolerance-horizontal-mm",
            "0.0001",
            "--tolerance-vertical-mm",
            "0.0001",
        ],
        source=_scripted_source(),
    )

    assert returncode != 0
    captured = capsys.readouterr()
    assert "fail" in captured.out

    reloaded = result_module.load_calibration(calibration_path)
    assert reloaded.verification_state == VerificationState.FAILED


def test_verify_uses_plan_tolerance_when_no_cli_override_given(tmp_path: Path) -> None:
    """CLI の --tolerance-* を省略すると計画ファイルの tolerance を使う。"""
    calibration_path = _calibrate_and_save(tmp_path)
    plan = _build_plan(
        verification_points=(_verification_point_spec(),),
        tolerance=ToleranceSpec(
            horizontal_mm=100.0, vertical_mm=100.0, provisional=True, source="計画ファイルの暫定値"
        ),
    )
    settings = _settings_no_log()

    report, updated_result, _report_path = cli.run_verify(
        settings,
        plan,
        calibration_path,
        frame_count=1,
        tolerance=None,
        report_out=tmp_path / "report.json",
        source=_scripted_source(),
    )

    assert report.tolerance is not None
    assert report.tolerance.source == "計画ファイルの暫定値"
    assert report.verdict == Verdict.PASS
    assert updated_result.verification_state == VerificationState.PASSED


def test_verify_without_independent_points_fails_as_calibration_failure(tmp_path: Path) -> None:
    """検証点が1つも無ければ、独立検証として成立せず `CalibrationFailure` に
    なる（要件 4.3）。
    """
    calibration_path = _calibrate_and_save(tmp_path)
    plan = _build_plan(verification_points=())
    settings = _settings_no_log()

    with pytest.raises(cli.CalibrationFailure) as exc_info:
        cli.run_verify(
            settings,
            plan,
            calibration_path,
            frame_count=1,
            tolerance=None,
            report_out=tmp_path / "report.json",
            source=_scripted_source(),
        )

    assert exc_info.value.reason == FailureReason.VERIFICATION_NOT_INDEPENDENT


def test_verify_report_is_saved_as_json_and_reloadable(tmp_path: Path) -> None:
    calibration_path = _calibrate_and_save(tmp_path)
    plan = _build_plan(verification_points=(_verification_point_spec(),))
    settings = _settings_no_log()
    report_path = tmp_path / "report.json"

    report, _updated_result, saved_report_path = cli.run_verify(
        settings,
        plan,
        calibration_path,
        frame_count=1,
        tolerance=None,
        report_out=report_path,
        source=_scripted_source(),
    )

    assert saved_report_path == report_path
    assert report_path.exists()
    reloaded = json.loads(report_path.read_text(encoding="utf-8"))
    assert reloaded["calibration_id"] == report.calibration_id
    assert reloaded["verdict"] == "not_judged"


# ---------------------------------------------------------------------------
# show: 要約表示 + 整合性検査
# ---------------------------------------------------------------------------


def test_show_prints_summary_and_exits_zero_when_compatible(tmp_path: Path, capsys) -> None:
    calibration_path = _calibrate_and_save(tmp_path)

    returncode = cli.main(
        ["show", "--calibration", str(calibration_path), "--frames", "1", "--no-log"],
        source=_scripted_source(),
    )

    assert returncode == 0
    captured = capsys.readouterr()
    assert "整合性" in captured.out


def test_show_detects_profile_mismatch_and_exits_nonzero(tmp_path: Path, capsys) -> None:
    calibration_path = _calibrate_and_save(tmp_path)
    mismatched_profile = _stream_profile(width_px=_INTRINSICS.width_px + 10)
    mismatched_source = _scripted_source(profile=mismatched_profile)

    returncode = cli.main(
        ["show", "--calibration", str(calibration_path), "--frames", "1", "--no-log"],
        source=mismatched_source,
    )

    assert returncode != 0
    captured = capsys.readouterr()
    assert "profile_mismatch" in captured.err


# ---------------------------------------------------------------------------
# compare: 2結果の差分
# ---------------------------------------------------------------------------


def test_compare_reports_transform_difference_and_exits_zero(tmp_path: Path, capsys) -> None:
    path_a = _calibrate_and_save(tmp_path, name="a.json")
    path_b = _calibrate_and_save(tmp_path, name="b.json")

    returncode = cli.main(["compare", str(path_a), str(path_b)])

    assert returncode == 0
    captured = capsys.readouterr()
    assert captured.out.strip() != ""


def test_compare_detects_signature_mismatch_and_exits_nonzero(tmp_path: Path, capsys) -> None:
    plan = _build_plan(verification_points=())
    settings = _settings_no_log()
    path_a = tmp_path / "a.json"
    path_b = tmp_path / "b.json"
    cli.run_calibrate(
        settings, plan, frame_count=1, out=path_a, source=_scripted_source(), logger=None
    )
    mismatched_profile = _stream_profile(width_px=_INTRINSICS.width_px + 10)
    cli.run_calibrate(
        settings,
        plan,
        frame_count=1,
        out=path_b,
        source=_scripted_source(profile=mismatched_profile),
        logger=None,
    )

    returncode = cli.main(["compare", str(path_a), str(path_b)])

    assert returncode != 0
    captured = capsys.readouterr()
    assert "profile_mismatch" in captured.err


# ---------------------------------------------------------------------------
# 失敗は必ず理由と文脈を標準エラーへ出す
# ---------------------------------------------------------------------------


def test_calibrate_failure_prints_reason_and_context_to_stderr(tmp_path: Path, capsys) -> None:
    plan_path = tmp_path / "plan.json"
    plan_module.save_plan(_build_plan(verification_points=()), plan_path)

    returncode = cli.main(
        [
            "calibrate",
            "--plan",
            str(plan_path),
            "--frames",
            "1",
            "--out",
            str(tmp_path / "out.json"),
            "--no-log",
        ],
        source=_sparse_source(),
    )

    assert returncode != 0
    captured = capsys.readouterr()
    assert "insufficient_depth_points" in captured.err
    assert "min_inlier_points" in captured.err
