"""`sensing_foundation.cli` に対するテスト（タスク 8.1）。

ファイル名は design.md の File Structure Plan が示す `test_cli.py` から
意図的に外す。`tests/prediction_core/` 配下に `test_cli.py` という同名衝突は
現時点では存在しないが、tasks.md 冒頭の Implementation Notes（タスク1.5）が
「1.6・8.1・8.2 は同じ衝突に当たる見込みなので、着手前にこの節を確認し、
同じ回避方針（`test_sensing_config.py` 等への改名）を踏襲すること」と明記して
おり、実際にタスク1.6は `test_sensing_config.py` として実施済みである。本タスク
もこの確立された命名規約（`test_sensing_*.py`）に揃え、`test_sensing_cli.py`
とする。

観測可能な完了状態（tasks.md 8.1）を固定する:

- `doctor` / `capture` / `record` / `replay-session` / `bench-modes` /
  `bench-logging` / `summarize` の7サブコマンドが存在し、それぞれ
  `--help` が例外なく完走する
- 再生のサブコマンド名は `replay` ではなく `replay-session` である
  （`replay` は argparse に拒否される）
- 合成入力を指定して `record` → `replay-session` → `summarize` をコマンド列
  で通すと、セッション記録・ログ・集計結果の3つが生成される（ヘッドライン
  テスト）
- `--print-settings` は解決済み設定を表示し、サブコマンドの本処理を実行せず
  に終了する
- `--source recorded` を `--session` 無しで指定すると、生トレースバックでは
  なく読める1行のエラーメッセージと非0終了コードで失敗する（起動前検証）
- 設定解決順序（CLI引数 > 環境変数 > 設定ファイル > 既定値）が正しく機能する
- 入力元は必ず文脈管理で開閉される（途中で例外が起きても `source.stop()`
  が走る）
- `--help` に解像度・fps の既定値が「初期評価候補であり必須性能ではない」旨
  が明記されている
- `replay-session` の `--help` に `prediction_core.replay` との区別が明記
  されている

要件: 12.4
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fakerealsense import FakeDevice, FakePipeline, FakeSensor, make_fake_rs_module
from synthetic import make_supplier

from sensing_foundation import cli
from sensing_foundation.config import RuntimeSettings
from sensing_foundation.errors import SourceContractError
from sensing_foundation.source import BaseFrameSource

WIDTH_PX = 8
HEIGHT_PX = 6

SUBCOMMANDS = (
    "doctor",
    "capture",
    "record",
    "replay-session",
    "bench-modes",
    "bench-logging",
    "summarize",
)


def _common_flags(tmp_path: Path, **overrides: str) -> list[str]:
    """テストが repo 実体の `var/` を汚さないよう、記録・ログ出力先を
    `tmp_path` 配下へ固定する共通フラグ列を組み立てる。
    """
    flags = [
        "--source",
        "simulated",
        "--width-px",
        str(WIDTH_PX),
        "--height-px",
        str(HEIGHT_PX),
        "--fps",
        "30",
        "--recording-root",
        str(overrides.get("recording_root", tmp_path / "sessions")),
        "--logging-path",
        str(overrides.get("logging_path", tmp_path / "logs")),
    ]
    return flags


def _live_flags(tmp_path: Path) -> list[str]:
    """`--source live` で `record` を回すための共通フラグ列（タスク 8.3）。"""
    return [
        "--source",
        "live",
        "--width-px",
        str(WIDTH_PX),
        "--height-px",
        str(HEIGHT_PX),
        "--fps",
        "30",
        "--recording-root",
        str(tmp_path / "sessions"),
        "--logging-path",
        str(tmp_path / "logs"),
    ]


def _install_fake_sdk(
    monkeypatch,
    *,
    device: FakeDevice | None = None,
    context_devices: list[FakeDevice] | None = None,
) -> FakePipeline:
    """フェイク `pyrealsense2` を `sys.modules` へ注入する（タスク 8.3）。

    `RealSenseSource` は SDK を関数内で遅延 import するため、この注入は
    `record` が入力元を開いた時点で効く。フレームは尽きない供給にして
    おき、取得の終わりは `--duration-s` が決める（在庫制のままだと区間の
    途中で在庫切れになり、取得失敗として扱われてしまう）。
    """
    pipeline = FakePipeline(
        device=device, endless=True, frame_width=WIDTH_PX, frame_height=HEIGHT_PX
    )
    monkeypatch.setitem(
        sys.modules,
        "pyrealsense2",
        make_fake_rs_module(pipeline, context_devices=context_devices),
    )
    return pipeline


def _record_live(monkeypatch, capsys, tmp_path, **fake_kwargs) -> dict:
    """フェイク SDK を入れた状態で `record` を1回走らせ、manifest を返す。"""
    _install_fake_sdk(monkeypatch, **fake_kwargs)
    rc = cli.main(
        ["record", *_live_flags(tmp_path), "--recording-mode", "continuous", "--duration-s", "0.05"]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    session_dir = Path(payload["session_dir"])
    return json.loads((session_dir / "manifest.json").read_text(encoding="utf-8"))


# ----------------------------------------------------------------------------
# サブコマンドの存在・--help の完走
# ----------------------------------------------------------------------------


class TestSubcommandsExist:
    @pytest.mark.parametrize("name", SUBCOMMANDS)
    def test_help_completes_without_crashing(self, name, capsys):
        with pytest.raises(SystemExit) as exc:
            cli.main([name, "--help"])
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert "usage" in captured.out.lower()

    def test_replay_is_rejected_but_replay_session_is_accepted(self, capsys):
        """再生のサブコマンド名は `replay` ではなく `replay-session` である。"""
        with pytest.raises(SystemExit) as exc:
            cli.main(["replay", "--session", "/nonexistent"])
        assert exc.value.code != 0
        stderr = capsys.readouterr().err
        assert "invalid choice" in stderr.lower()

        with pytest.raises(SystemExit) as exc2:
            cli.main(["replay-session", "--help"])
        assert exc2.value.code == 0

    def test_replay_session_help_documents_distinction_from_prediction_core_replay(
        self, capsys
    ):
        with pytest.raises(SystemExit):
            cli.main(["replay-session", "--help"])
        out = capsys.readouterr().out
        assert "prediction_core.replay" in out
        assert "replay-session" in out

    def test_help_marks_default_resolution_fps_as_initial_candidate(self, capsys):
        with pytest.raises(SystemExit):
            cli.main(["capture", "--help"])
        out = capsys.readouterr().out
        assert "初期評価候補であり必須性能ではない" in out


# ----------------------------------------------------------------------------
# 各サブコマンドの単体動作
# ----------------------------------------------------------------------------


class TestDoctor:
    def test_outputs_json_report_with_nine_items(self, capsys, tmp_path):
        rc = cli.main(["doctor", "--recording-root", str(tmp_path / "sessions")])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert isinstance(payload, list)
        names = {c["name"] for c in payload}
        assert names == {
            "os",
            "python",
            "memory",
            "sdk_import",
            "device",
            "usb",
            "stream_open",
            "power_stability",
            "disk",
        }


class TestCapture:
    def test_runs_against_simulated_and_reports_statistics(self, capsys, tmp_path):
        rc = cli.main(["capture", *_common_flags(tmp_path), "--duration-s", "0.1"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["frames_seen"] > 0
        assert payload["stats"]["frames_yielded"] == payload["frames_seen"]

    def test_stops_gracefully_when_supplier_exhausted_before_duration(self, tmp_path):
        supplier = make_supplier(WIDTH_PX, HEIGHT_PX, list(range(5)))
        settings = RuntimeSettings.resolve(
            file=None,
            env={},
            overrides={
                "source": "simulated",
                "width_px": WIDTH_PX,
                "height_px": HEIGHT_PX,
                "fps": 30,
                "logging_path": str(tmp_path / "logs"),
            },
        )
        result = cli.run_capture(settings, duration_s=10.0, supplier=supplier)
        assert result["frames_seen"] == 5


class TestRecord:
    def test_produces_session_recording_four_files(self, capsys, tmp_path):
        rc = cli.main(
            [
                "record",
                *_common_flags(tmp_path),
                "--recording-mode",
                "continuous",
                "--duration-s",
                "0.1",
            ]
        )
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        session_dir = Path(payload["session_dir"])
        assert (session_dir / "manifest.json").exists()
        assert (session_dir / "frames.ndjson").exists()
        assert (session_dir / "depth.bin").exists()
        assert (session_dir / "summary.json").exists()
        assert payload["frames_written"] == payload["frames_seen"] > 0

    def test_ring_mode_flushes_buffered_frames_once(self, capsys, tmp_path):
        rc = cli.main(
            [
                "record",
                *_common_flags(tmp_path),
                "--recording-mode",
                "ring",
                "--ring-seconds",
                "0.01",
                "--duration-s",
                "0.1",
            ]
        )
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        session_dir = Path(payload["session_dir"])
        assert (session_dir / "summary.json").exists()
        # ring バッファは直近 ring_seconds 分しか保持しないため、書き込み枚数
        # は取得枚数以下になる。
        assert 0 < payload["frames_written"] <= payload["frames_seen"]

    def test_produces_ndjson_log(self, capsys, tmp_path):
        rc = cli.main(
            [
                "record",
                *_common_flags(tmp_path),
                "--recording-mode",
                "continuous",
                "--duration-s",
                "0.1",
            ]
        )
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        log_path = Path(payload["log_path"])
        assert log_path.exists()
        assert log_path.stat().st_size > 0


class TestRecordManifestMetadata:
    """`record` の manifest がデバイス識別情報と有効化結果を持つ（タスク 8.3）。

    タスク9.6 の実機記録では `device` が `null`・`runtime.global_time_enabled`
    が `null` のままだった（要件 5.2 が実機で未充足）。情報が無いのではなく
    渡していなかったのが原因であり、ここでは live 経路を SDK モックで端から
    端まで通してその配線を固定する。要件 5.2 / 6.1。
    """

    def test_live_record_writes_device_identity_into_the_manifest(
        self, monkeypatch, capsys, tmp_path
    ):
        device = FakeDevice(
            name="Intel RealSense D435",
            serial="834412071095",
            firmware="5.17.3.10",
            usb_type="3.2",
            product_line="D400",
        )
        manifest = _record_live(monkeypatch, capsys, tmp_path, device=device)

        assert manifest["device"] == {
            "name": "Intel RealSense D435",
            "serial": "834412071095",
            "firmware": "5.17.3.10",
            "usb_type": "3.2",
            "product_line": "D400",
        }

    def test_live_record_writes_the_global_time_result_as_a_boolean(
        self, monkeypatch, capsys, tmp_path
    ):
        """有効化に成功した事実が `null` ではなく真偽値として残る（要件 6.1）。"""
        manifest = _record_live(monkeypatch, capsys, tmp_path)
        assert manifest["runtime"]["global_time_enabled"] is True

    def test_global_time_failure_is_recorded_as_false_not_null(
        self, monkeypatch, capsys, tmp_path
    ):
        """有効化できなかったことも記録に残る。`null`（未試行）と区別できる。"""
        device = FakeDevice(sensor=FakeSensor(global_time_supported=False))
        manifest = _record_live(monkeypatch, capsys, tmp_path, device=device)
        assert manifest["runtime"]["global_time_enabled"] is False

    def test_device_recorded_is_the_opened_one_not_the_first_enumerated(
        self, monkeypatch, capsys, tmp_path
    ):
        """複数台つながっているとき、開いた個体が記録される（タスク 6.3 の端から端まで）。"""
        opened = FakeDevice(serial="SN-OPENED")
        other = FakeDevice(serial="SN-OTHER")
        manifest = _record_live(
            monkeypatch, capsys, tmp_path, device=opened, context_devices=[other, opened]
        )
        assert manifest["device"]["serial"] == "SN-OPENED"

    def test_items_the_sdk_refuses_stay_null_in_the_manifest(
        self, monkeypatch, capsys, tmp_path
    ):
        """取得できなかった項目は `null`。既定値で埋めない（要件 3.5）。"""
        device = FakeDevice(firmware=None, usb_type=None, product_line=None)
        manifest = _record_live(monkeypatch, capsys, tmp_path, device=device)

        assert manifest["device"]["firmware"] is None
        assert manifest["device"]["usb_type"] is None
        assert manifest["device"]["product_line"] is None
        assert manifest["device"]["serial"] == "SN-0001"

    def test_simulated_record_keeps_device_and_global_time_null(self, capsys, tmp_path):
        """live 以外の入力元では欠測のまま。物理デバイスの情報を捏造しない。"""
        rc = cli.main(
            [
                "record",
                *_common_flags(tmp_path),
                "--recording-mode",
                "continuous",
                "--duration-s",
                "0.1",
            ]
        )
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        manifest = json.loads(
            (Path(payload["session_dir"]) / "manifest.json").read_text(encoding="utf-8")
        )

        assert manifest["device"] is None
        assert manifest["runtime"]["global_time_enabled"] is None

    def test_a_non_live_source_exposing_the_attributes_is_still_recorded_as_null(
        self, monkeypatch, capsys, tmp_path
    ):
        """live 以外の入力元が同名の属性を持っていても、記録へは写さない。

        `RecordedSource` は `profile` / `intrinsics` を manifest から素通し
        する作りなので、将来 `device` も素通しするようになり得る。そのとき
        属性の有無だけで判定していると、**再生を録り直した記録に「元の個体」
        が live で撮ったかのように残る**。`_live_only()` が `kind` で先に
        絞っているのはこの取り違えを防ぐためであり、このテストが無いと
        その絞り込みが効いているかを誰も確かめられない。
        """
        real_open_source = cli.open_source

        def open_with_fabricated_identity(*args, **kwargs):
            source = real_open_source(*args, **kwargs)
            source.device_identity = SimpleNamespace(
                name="偽の機種名",
                serial_number="SN-FABRICATED",
                firmware_version="9.9.9.9",
                usb_type_descriptor="3.2",
                product_line="D400",
            )
            source.global_time_enabled = True
            return source

        monkeypatch.setattr(cli, "open_source", open_with_fabricated_identity)
        rc = cli.main(
            [
                "record",
                *_common_flags(tmp_path),
                "--recording-mode",
                "continuous",
                "--duration-s",
                "0.05",
            ]
        )
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        manifest = json.loads(
            (Path(payload["session_dir"]) / "manifest.json").read_text(encoding="utf-8")
        )

        assert manifest["device"] is None
        assert manifest["runtime"]["global_time_enabled"] is None

    def test_runtime_keeps_the_environment_fields_it_already_had(
        self, monkeypatch, capsys, tmp_path
    ):
        """既存の `runtime` 項目を落とさない（回帰。有効化結果の追加は置換ではない）。"""
        manifest = _record_live(monkeypatch, capsys, tmp_path)
        runtime = manifest["runtime"]
        for key in ("os", "os_release", "python_version", "hostname"):
            assert isinstance(runtime[key], str) and runtime[key]


class TestReplaySession:
    def _record(self, capsys, tmp_path) -> dict:
        rc = cli.main(
            [
                "record",
                *_common_flags(tmp_path),
                "--recording-mode",
                "continuous",
                "--duration-s",
                "0.1",
            ]
        )
        assert rc == 0
        return json.loads(capsys.readouterr().out)

    def test_replays_recorded_session_and_confirms_consistency(self, capsys, tmp_path):
        record_payload = self._record(capsys, tmp_path)

        rc = cli.main(
            [
                "replay-session",
                "--session",
                record_payload["session_dir"],
                "--logging-path",
                str(tmp_path / "logs"),
            ]
        )
        assert rc == 0
        replay_payload = json.loads(capsys.readouterr().out)
        assert replay_payload["consistent"] is True
        assert replay_payload["frames_replayed"] == replay_payload["expected_frames"] > 0
        assert replay_payload["summary_present"] is True

    def test_forces_recorded_source_even_if_source_flag_given(self, capsys, tmp_path):
        """`replay-session` は `--source` の値に関わらず常に recorded を使う。"""
        record_payload = self._record(capsys, tmp_path)

        rc = cli.main(
            [
                "replay-session",
                "--source",
                "simulated",  # 無視されるはず
                "--session",
                record_payload["session_dir"],
                "--logging-path",
                str(tmp_path / "logs"),
            ]
        )
        assert rc == 0
        replay_payload = json.loads(capsys.readouterr().out)
        assert replay_payload["consistent"] is True


class TestBenchModes:
    def test_produces_result_per_candidate_mode(self, capsys, tmp_path):
        rc = cli.main(
            [
                "bench-modes",
                *_common_flags(tmp_path),
                "--modes",
                f"{WIDTH_PX}x{HEIGHT_PX}@30,{WIDTH_PX}x{HEIGHT_PX}@60",
                "--duration-s",
                "0.03",
                "--window-ms",
                "10",
                "--warmup-s",
                "0.0",
            ]
        )
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert len(payload["results"]) == 2
        assert all(result is not None for result in payload["results"])
        assert "effective_samples_per_window_note" in payload
        assert "effective_points_per_window" in payload["effective_samples_per_window_note"]


class TestBenchLogging:
    def test_produces_three_conditions_and_two_verdicts(self, capsys, tmp_path):
        rc = cli.main(
            [
                "bench-logging",
                *_common_flags(tmp_path),
                "--work-dir",
                str(tmp_path / "bench-work"),
                "--segment-s",
                "0.02",
                "--cycles",
                "2",
            ]
        )
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert len(payload["results"]) == 3
        assert set(payload["verdicts"]) == {
            "logging_on_vs_logging_off",
            "recording_on_vs_logging_off",
        }
        assert payload["hardware_disclaimer"] is not None


class TestSummarize:
    def test_aggregates_log_produced_by_record(self, capsys, tmp_path):
        rc = cli.main(
            [
                "record",
                *_common_flags(tmp_path),
                "--recording-mode",
                "continuous",
                "--duration-s",
                "0.1",
            ]
        )
        assert rc == 0
        record_payload = json.loads(capsys.readouterr().out)

        rc = cli.main(["summarize", "--log", record_payload["log_path"]])
        assert rc == 0
        summary_payload = json.loads(capsys.readouterr().out)
        assert summary_payload["total_lines"] > 0
        assert len(summary_payload["events"]) > 0


# ----------------------------------------------------------------------------
# ヘッドラインテスト: record -> replay-session -> summarize のコマンド列
# ----------------------------------------------------------------------------


class TestHeadlineSequence:
    def test_record_then_replay_session_then_summarize_produces_all_three_artifacts(
        self, capsys, tmp_path
    ):
        sessions_root = tmp_path / "sessions"
        logs_dir = tmp_path / "logs"

        # 1. record: セッション記録を生成する。
        rc = cli.main(
            [
                "record",
                "--source",
                "simulated",
                "--width-px",
                str(WIDTH_PX),
                "--height-px",
                str(HEIGHT_PX),
                "--fps",
                "30",
                "--recording-root",
                str(sessions_root),
                "--logging-path",
                str(logs_dir),
                "--recording-mode",
                "continuous",
                "--duration-s",
                "0.15",
            ]
        )
        assert rc == 0
        record_payload = json.loads(capsys.readouterr().out)
        session_dir = Path(record_payload["session_dir"])
        for name in ("manifest.json", "frames.ndjson", "depth.bin", "summary.json"):
            assert (session_dir / name).exists(), name

        # 2. replay-session: 記録を再生し、一致性を確認する。
        rc = cli.main(
            [
                "replay-session",
                "--session",
                str(session_dir),
                "--logging-path",
                str(logs_dir),
            ]
        )
        assert rc == 0
        replay_payload = json.loads(capsys.readouterr().out)
        assert replay_payload["consistent"] is True

        # 3. summarize: record が書いたログを集計する。
        rc = cli.main(["summarize", "--log", record_payload["log_path"]])
        assert rc == 0
        summary_payload = json.loads(capsys.readouterr().out)

        # 3種の成果物がすべて揃っていることを確認する。
        assert session_dir.is_dir()  # (a) セッション記録
        assert Path(record_payload["log_path"]).stat().st_size > 0  # (b) ログ
        assert summary_payload["total_lines"] > 0  # (c) 集計結果
        assert len(summary_payload["events"]) > 0


# ----------------------------------------------------------------------------
# --print-settings
# ----------------------------------------------------------------------------


class TestPrintSettings:
    def test_displays_resolved_settings_and_skips_subcommand_action(
        self, capsys, monkeypatch, tmp_path
    ):
        called: list[bool] = []
        monkeypatch.setattr(cli, "run_capture", lambda *a, **k: called.append(True))

        rc = cli.main(
            [
                "capture",
                "--print-settings",
                *_common_flags(tmp_path),
            ]
        )
        assert rc == 0
        assert called == []  # run_capture は一度も呼ばれない

        payload = json.loads(capsys.readouterr().out)
        assert payload["source"] == "simulated"
        assert payload["capture"]["width_px"] == WIDTH_PX
        assert payload["capture"]["height_px"] == HEIGHT_PX


# ----------------------------------------------------------------------------
# 起動前検証: --source recorded かつ --session 未指定
# ----------------------------------------------------------------------------


class TestStartupValidation:
    def test_recorded_without_session_fails_cleanly_not_traceback(self, capsys):
        rc = cli.main(["capture", "--source", "recorded"])
        assert rc != 0
        captured = capsys.readouterr()
        assert "Traceback" not in captured.err
        assert "session" in captured.err or "セッション" in captured.err

    def test_replay_session_without_session_fails_cleanly(self, capsys):
        rc = cli.main(["replay-session"])
        assert rc != 0
        captured = capsys.readouterr()
        assert "Traceback" not in captured.err

    def test_default_live_source_without_sdk_fails_cleanly_not_traceback(self, capsys, tmp_path):
        """既定 (--source 省略時 = live) で SDK 非導入環境を実行しても、
        生トレースバックではなく `SensingFoundationError` 経由の1行メッセージ
        になる（`SourceUnavailableError` は `RealSenseSource.start()` 内で
        `pyrealsense2` の遅延 import が失敗した際に送出される）。
        """
        rc = cli.main(
            [
                "capture",
                "--duration-s",
                "0.05",
                "--logging-path",
                str(tmp_path / "logs"),
            ]
        )
        assert rc != 0
        captured = capsys.readouterr()
        assert "Traceback" not in captured.err
        assert "pyrealsense2" in captured.err


# ----------------------------------------------------------------------------
# 設定解決順序: CLI引数 > 環境変数 > 設定ファイル > 既定値
# ----------------------------------------------------------------------------


class TestSettingsPrecedence:
    def test_cli_beats_env_beats_file_beats_default(self, tmp_path, monkeypatch, capsys):
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({"fps": 20}), encoding="utf-8")
        monkeypatch.delenv("STB_SF_FPS", raising=False)

        # 既定値のみ（設定ファイルもCLI引数も無し）。
        # 既定 fps はタスク 9.4 の実機実測により 30 → 60 へ変更された
        # （`measurements.md` タスク9.4）。ここで固定しているのは
        # 「解決順序の最下層が既定値である」ことであり、60 という値そのものの
        # 妥当性ではない。
        rc = cli.main(["capture", "--print-settings"])
        assert rc == 0
        assert json.loads(capsys.readouterr().out)["capture"]["fps"] == 60

        # 設定ファイル > 既定値。
        rc = cli.main(["capture", "--print-settings", "--config", str(config_path)])
        assert rc == 0
        assert json.loads(capsys.readouterr().out)["capture"]["fps"] == 20

        # 環境変数 > 設定ファイル。
        monkeypatch.setenv("STB_SF_FPS", "55")
        rc = cli.main(["capture", "--print-settings", "--config", str(config_path)])
        assert rc == 0
        assert json.loads(capsys.readouterr().out)["capture"]["fps"] == 55

        # CLI引数 > 環境変数。
        rc = cli.main(
            ["capture", "--print-settings", "--config", str(config_path), "--fps", "99"]
        )
        assert rc == 0
        assert json.loads(capsys.readouterr().out)["capture"]["fps"] == 99


# ----------------------------------------------------------------------------
# 入力元の文脈管理保証
# ----------------------------------------------------------------------------


class TestContextManagement:
    def test_source_stop_runs_even_on_mid_run_exception(self, monkeypatch, tmp_path):
        """途中で例外（契約違反）が起きても `source.stop()`（`__exit__`）が走る。"""
        calls: list[object] = []
        original_exit = BaseFrameSource.__exit__

        def spy_exit(self, *exc):
            calls.append(exc)
            return original_exit(self, *exc)

        monkeypatch.setattr(BaseFrameSource, "__exit__", spy_exit)

        supplier = make_supplier(WIDTH_PX, HEIGHT_PX, list(range(50)), violate_at=5)
        settings = RuntimeSettings.resolve(
            file=None,
            env={},
            overrides={
                "source": "simulated",
                "width_px": WIDTH_PX,
                "height_px": HEIGHT_PX,
                "fps": 30,
                "recording_root": str(tmp_path / "sessions"),
                "logging_path": str(tmp_path / "logs"),
                "recording_mode": "continuous",
            },
        )

        with pytest.raises(SourceContractError):
            cli.run_record(settings, duration_s=10.0, supplier=supplier)

        assert len(calls) == 1

    def test_recorder_still_writes_summary_on_mid_run_exception(self, tmp_path):
        """`SessionRecorder.__exit__` の安全網により、例外時でも summary.json が書かれる。"""
        supplier = make_supplier(WIDTH_PX, HEIGHT_PX, list(range(50)), violate_at=5)
        sessions_root = tmp_path / "sessions"
        settings = RuntimeSettings.resolve(
            file=None,
            env={},
            overrides={
                "source": "simulated",
                "width_px": WIDTH_PX,
                "height_px": HEIGHT_PX,
                "fps": 30,
                "recording_root": str(sessions_root),
                "logging_path": str(tmp_path / "logs"),
                "recording_mode": "continuous",
            },
        )

        with pytest.raises(SourceContractError):
            cli.run_record(settings, duration_s=10.0, supplier=supplier)

        session_dirs = list(sessions_root.iterdir())
        assert len(session_dirs) == 1
        assert (session_dirs[0] / "summary.json").exists()


# ----------------------------------------------------------------------------
# `python -m sensing_foundation.cli` としての実起動（1件のみの subprocess スモーク）
# ----------------------------------------------------------------------------


def test_module_entrypoint_smoke():
    result = subprocess.run(
        [sys.executable, "-m", "sensing_foundation.cli", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "doctor" in result.stdout
