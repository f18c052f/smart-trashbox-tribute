"""コマンド入口（`cli.py`、タスク 7.3）の検証。

design.md「L8-L9: 比較・入口 → CLI」と要件 4.2, 4.8, 8.8, 12.1〜12.4, 12.6 が
定める、タスク本文（verbatim）の観測可能な完了状態を固定する:

- 合成セッションに対して全4サブコマンド（`track` / `compare-detectors` /
  `bench-overhead` / `export`）が画面のない環境で完走する
- 解決済み設定の表示（`--print-settings`）が指定した上書きを反映する

加えてタスクブリーフが明示する検証項目（未知の検出方式の拒否・解決順序・
`--help` の非既成事実化の注記・headless 安全性）を固定する。

## テスト構成の設計判断

`main(argv: list[str]) -> int` を直接呼び出す（subprocess を起動しない）。
入力元は `SessionRecorder`（`sensing_foundation`）で書き出した合成セッション
（`--source recorded --session <path>`）を使う——`tests/flying_object_tracking/
test_flying_object_tracking_detector_comparison.py` /
`test_flying_object_tracking_overhead_bench.py` と同じ、実績のある
軌道・profile パラメータをそのまま踏襲する（`--source simulated` は
`sensing_foundation` 側の既知の制約——`SourceKind.SIMULATED` の
`intrinsics` が常に `None` になる——により候補検出が
`IntrinsicsUnavailableError` を誘発しうるため、smoke テストでは避ける。
`cli.py` モジュール docstring「`--source simulated` の内部供給関数」参照）。
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
from sensing_foundation import CameraIntrinsics, CaptureStats, SessionRecorder, SourceKind, StreamProfile

import flying_object_tracking.cli as cli_module
from flying_object_tracking.cli import main

DIAMETER_MM = 65.0
BLOB_Z_MM = 1000.0
BACKGROUND_RAW = 6000
WIDTH_PX = 64
HEIGHT_PX = 64

STEP_MM = 15.0
WARMUP_LEN = 3
MOVING_LEN = 6


# ==============================================================================
# 合成セッションの建材（既存タスクファイルと同じ流儀・同じパラメータ。
# `test_flying_object_tracking_detector_comparison.py` 参照。他テストファイル
# との bare import 衝突を避けるためこのファイル内に自己完結させる）
# ==============================================================================


def _intrinsics() -> CameraIntrinsics:
    return CameraIntrinsics(
        width_px=WIDTH_PX,
        height_px=HEIGHT_PX,
        fx_px=200.0,
        fy_px=200.0,
        ppx_px=WIDTH_PX / 2.0,
        ppy_px=HEIGHT_PX / 2.0,
        model="none",
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


def _trajectory(synthetic_module) -> list:
    TrajectoryPoint = synthetic_module.TrajectoryPoint
    points = []
    t_ms = 0.0
    for _ in range(WARMUP_LEN):
        points.append(TrajectoryPoint(t_ms=t_ms, x_mm=0.0, y_mm=0.0, z_mm=float(BACKGROUND_RAW)))
        t_ms += 33.0
    for i in range(MOVING_LEN):
        points.append(TrajectoryPoint(t_ms=t_ms, x_mm=float(i) * STEP_MM, y_mm=0.0, z_mm=BLOB_Z_MM))
        t_ms += 33.0
    return points


def _synthesize(synthetic_module) -> list:
    trajectory = _trajectory(synthetic_module)
    return synthetic_module.synthesize_frames(
        trajectory, _profile(), DIAMETER_MM, background_raw=BACKGROUND_RAW
    )


def _record_session(root: Path, frames: list, session_id: str) -> Path:
    recorder = SessionRecorder(
        root, session_id, _profile(), device=None, runtime={}, source=SourceKind.SIMULATED
    )
    with recorder:
        for frame in frames:
            recorder.write(frame)
        recorder.close(
            CaptureStats(
                frames_yielded=len(frames),
                frames_dropped=0,
                frames_missing=0,
                duration_ms=0.0,
                measured_fps=0.0,
                acquire_errors=0,
            )
        )
    return root / session_id


@pytest.fixture
def synthetic_session(tmp_path: Path, synthetic_module) -> Path:
    """合成軌道から書き出した記録済みセッションのディレクトリを返す。"""
    frames = _synthesize(synthetic_module)
    return _record_session(tmp_path / "sessions", frames, "cli-smoke-session")


def _source_args(session_dir: Path) -> list[str]:
    return ["--source", "recorded", "--session", str(session_dir)]


def _tracking_override_args(*, detector_kind: str = "depth_band") -> list[str]:
    """既存タスクファイル（compare/overhead テスト）と同じ、実績のある設定値。"""
    return [
        "--roi-z-min-mm",
        "500",
        "--roi-z-max-mm",
        "5000",
        "--object-diameter-mm",
        str(DIAMETER_MM),
        "--detector-kind",
        detector_kind,
        "--detector-diff-threshold-mm",
        "80",
        "--detector-background-frames",
        str(WARMUP_LEN),
        "--detector-background-update-rate",
        "0",
        "--detector-open-kernel-px",
        "1",
        "--detector-close-kernel-px",
        "1",
        "--tracker-max-step-mm",
        "900",
        "--tracker-max-missing-frames",
        "3",
        "--tracker-min-points-to-start",
        "1",
    ]


# ==============================================================================
# 1. 全4サブコマンドが画面のない環境で完走する（タスクの観測可能な完了状態）
# ==============================================================================


class TestAllSubcommandsCompleteHeadlessly:
    @pytest.mark.parametrize("subcommand", ["track", "compare-detectors", "bench-overhead", "export"])
    def test_subcommand_exits_zero_on_synthetic_session(
        self, subcommand: str, tmp_path: Path, synthetic_session: Path
    ) -> None:
        output_root = tmp_path / "var" / "tracking"
        argv = [
            subcommand,
            "--output-root",
            str(output_root),
            *_source_args(synthetic_session),
            *_tracking_override_args(),
        ]

        exit_code = main(argv)

        assert exit_code == 0

    def test_track_reports_counters_and_a_camera_frame_track(
        self, tmp_path: Path, synthetic_session: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        output_root = tmp_path / "var" / "tracking"
        argv = [
            "track",
            "--output-root",
            str(output_root),
            *_source_args(synthetic_session),
            *_tracking_override_args(),
        ]

        exit_code = main(argv)

        assert exit_code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["track"]["frame"] == "camera"
        assert payload["track"]["handoff_version"] == "1.0"
        assert "counters" in payload
        assert payload["counters"]["frames_processed"] == WARMUP_LEN + MOVING_LEN

    def test_export_writes_camera_track_json_file(self, tmp_path: Path, synthetic_session: Path) -> None:
        output_root = tmp_path / "var" / "tracking"
        argv = [
            "export",
            "--output-root",
            str(output_root),
            *_source_args(synthetic_session),
            *_tracking_override_args(),
        ]

        exit_code = main(argv)

        assert exit_code == 0
        written = sorted(output_root.glob("track-*.json"))
        assert len(written) == 1
        payload = json.loads(written[0].read_text(encoding="utf-8"))
        assert payload["frame"] == "camera"
        assert payload["handoff_version"] == "1.0"
        assert "points" in payload

    def test_compare_detectors_writes_result_with_all_three_kinds(
        self, tmp_path: Path, synthetic_session: Path
    ) -> None:
        output_root = tmp_path / "var" / "tracking"
        argv = [
            "compare-detectors",
            "--output-root",
            str(output_root),
            *_source_args(synthetic_session),
            *_tracking_override_args(),
        ]

        exit_code = main(argv)

        assert exit_code == 0
        written = output_root / f"compare-{synthetic_session.name}.json"
        assert written.exists()
        payload = json.loads(written.read_text(encoding="utf-8"))
        assert len(payload["runs"]) == 3

    def test_bench_overhead_writes_verdict(self, tmp_path: Path, synthetic_session: Path) -> None:
        output_root = tmp_path / "var" / "tracking"
        argv = [
            "bench-overhead",
            "--output-root",
            str(output_root),
            "--cycles",
            "1",
            *_source_args(synthetic_session),
            *_tracking_override_args(),
        ]

        exit_code = main(argv)

        assert exit_code == 0
        written = output_root / f"overhead-{synthetic_session.name}.json"
        assert written.exists()
        payload = json.loads(written.read_text(encoding="utf-8"))
        assert "criterion" in payload["verdict"]
        assert "passed" in payload["verdict"]


# ==============================================================================
# 2. --print-settings が指定した上書きを反映する（タスクの観測可能な完了状態）
# ==============================================================================


class TestPrintSettingsReflectsOverrides:
    def test_print_settings_reflects_detector_kind_and_output_root_overrides(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        output_root = tmp_path / "var" / "tracking"

        exit_code = main(
            [
                "track",
                "--print-settings",
                "--detector-kind",
                "depth_band",
                "--output-root",
                str(output_root),
                "--object-diameter-mm",
                "70.0",
            ]
        )

        assert exit_code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["detector"]["kind"] == "depth_band"
        assert payload["output_root"] == str(output_root)
        assert payload["object_model"]["diameter_mm"] == 70.0
        # 表示に「初期評価候補」注記が含まれる（要件 12.3）。
        assert "provisional_defaults" in payload

    def test_print_settings_does_not_require_a_source_to_be_specified(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`--print-settings` は入力元（--source/--session）を要求しない
        （設定表示は実行の必須要件ではなく、実行そのものより先に完結する）。
        """
        exit_code = main(["track", "--print-settings", "--detector-kind", "background"])

        assert exit_code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["detector"]["kind"] == "background"

    def test_print_settings_works_identically_across_all_subcommands(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        for subcommand in ["track", "compare-detectors", "bench-overhead", "export"]:
            exit_code = main([subcommand, "--print-settings", "--detector-kind", "frame_diff"])
            assert exit_code == 0
            payload = json.loads(capsys.readouterr().out)
            assert payload["detector"]["kind"] == "frame_diff"


# ==============================================================================
# 3. 未知の検出方式名は起動前に拒否する（タスクの観測可能な完了状態）
# ==============================================================================


class TestUnknownDetectorKindRejectedBeforeExecution:
    def test_unknown_detector_kind_exits_nonzero_via_argparse_choices(self, tmp_path: Path) -> None:
        output_root = tmp_path / "var" / "tracking"

        with pytest.raises(SystemExit) as excinfo:
            main(
                [
                    "track",
                    "--detector-kind",
                    "not_a_real_detector_kind",
                    "--output-root",
                    str(output_root),
                ]
            )

        assert excinfo.value.code == 2
        # どの処理も始まっていない（出力先すら作られていない）ことの確認。
        assert not output_root.exists()

    def test_unknown_kinds_for_compare_detectors_is_rejected_before_any_source_is_opened(
        self, tmp_path: Path
    ) -> None:
        output_root = tmp_path / "var" / "tracking"

        exit_code = main(
            [
                "compare-detectors",
                "--kinds",
                "not_a_real_detector_kind",
                "--source",
                "recorded",
                "--session",
                str(tmp_path / "does-not-exist"),  # 実行前検証で落ちるため開かれないはず
                "--output-root",
                str(output_root),
            ]
        )

        assert exit_code == 2
        assert not output_root.exists()


# ==============================================================================
# 4. 設定の解決順序（CLI引数 > 環境変数 > 設定ファイル > 既定値）
# ==============================================================================


class TestResolutionOrder:
    def test_cli_arg_wins_over_env_and_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        config_path = tmp_path / "settings.json"
        config_path.write_text(json.dumps({"detector_kind": "background"}), encoding="utf-8")
        monkeypatch.setenv("STB_FOT_DETECTOR_KIND", "frame_diff")

        exit_code = main(
            [
                "track",
                "--print-settings",
                "--config",
                str(config_path),
                "--detector-kind",
                "depth_band",
            ]
        )

        assert exit_code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["detector"]["kind"] == "depth_band"

    def test_env_wins_over_file_when_no_cli_override(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        config_path = tmp_path / "settings.json"
        config_path.write_text(json.dumps({"detector_kind": "background"}), encoding="utf-8")
        monkeypatch.setenv("STB_FOT_DETECTOR_KIND", "frame_diff")

        exit_code = main(["track", "--print-settings", "--config", str(config_path)])

        assert exit_code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["detector"]["kind"] == "frame_diff"

    def test_file_wins_over_default_when_no_cli_or_env_override(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        config_path = tmp_path / "settings.json"
        config_path.write_text(json.dumps({"detector_kind": "background"}), encoding="utf-8")
        monkeypatch.delenv("STB_FOT_DETECTOR_KIND", raising=False)

        exit_code = main(["track", "--print-settings", "--config", str(config_path)])

        assert exit_code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["detector"]["kind"] == "background"


# ==============================================================================
# 5. --help が「初期評価候補であり必須性能ではない」旨を明記する（要件 12.3）
# ==============================================================================


class TestHelpTextDisclaimer:
    def test_help_mentions_provisional_default_disclaimer_for_flagged_fields(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as excinfo:
            main(["track", "--help"])

        assert excinfo.value.code == 0
        help_text = capsys.readouterr().out
        # タスク 1.4 が明示的に「初期評価候補」と旗を立てたフィールド
        # （diameter_mm / kind / window_ms）を含め、少なくとも3箇所で
        # 同じ注記が現れること。
        assert help_text.count("初期評価候補であり必須性能ではない") >= 3
        assert "--object-diameter-mm" in help_text
        assert "--detector-kind" in help_text
        assert "--measurement-window-ms" in help_text

    def test_help_available_for_every_subcommand(self, capsys: pytest.CaptureFixture[str]) -> None:
        for subcommand in ["track", "compare-detectors", "bench-overhead", "export"]:
            with pytest.raises(SystemExit) as excinfo:
                main([subcommand, "--help"])
            assert excinfo.value.code == 0
            help_text = capsys.readouterr().out
            assert "初期評価候補であり必須性能ではない" in help_text


# ==============================================================================
# 6. headless 安全性（GUI ツールキットへ触れない。構造による保証）
# ==============================================================================


class TestHeadlessSafety:
    def test_cli_module_source_does_not_reference_gui_toolkits(self) -> None:
        """`cli.py` が GUI ツールキット（tkinter・GUI 付き matplotlib 等）を
        一切 import しないことをソース走査で確認する（要件 12.4）。
        画像処理は headless な OpenCV extras（`opencv-python-headless`）に
        乗る既存実装（`detection/mask_ops.py`）の上に本タスクは何も
        追加しないため、trivially-true な性質として簡易に固定する。
        """
        source = inspect.getsource(cli_module)
        import_lines = [
            line.strip()
            for line in source.splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        joined_imports = "\n".join(import_lines)
        assert "tkinter" not in joined_imports
        assert "matplotlib" not in joined_imports
        assert "cv2" not in joined_imports  # cli.py 自身は cv2 を直接 import しない

    def test_no_gui_toolkit_is_imported_as_a_side_effect_of_importing_cli(self) -> None:
        import sys

        assert "tkinter" not in sys.modules


# ==============================================================================
# 7. main の返り値は int（プロセス終了コードとして使える）
# ==============================================================================


class TestMainReturnsIntExitCode:
    def test_successful_print_settings_returns_int_zero(self) -> None:
        exit_code = main(["track", "--print-settings"])
        assert exit_code == 0
        assert isinstance(exit_code, int)
