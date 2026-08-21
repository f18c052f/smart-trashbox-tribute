"""`sensing_foundation.doctor` に対するテスト（タスク 6.2）。

観測可能な完了状態（tasks.md 6.2）を固定する:

- SDK 非導入の環境で実行すると SDK 項目のみが失敗し、`os` / `python` /
  `memory` の各項目は正常として全項目（9件）が返る
- 各項目は独立して失敗できる（1つの `fail` が後続をスキップしない）
- `power_stability` は「問題なし」ではなく「この条件では観測されなかった」
  という表現で報告する
- 結果を機械可読な形式（`report_json()`）で保存できる
- `capture` 本体（`open_source()` / CLI）に依存せず独立して構築・実行できる
- `doctor.py` は `pyrealsense2` を一切 import しない（SDK 関連の項目は
  `sources.realsense` の `probe_sdk()` / `probe_devices()` / `RealSenseSource`
  を経由する）

本環境（WSL, `pyrealsense2` 非導入）では `sdk_import` / `device` /
`stream_open` / `power_stability` の各項目は必ず失敗側（`fail` または
`skip`）に倒れる。モックは使わず、実環境の事実（SDK が本当に無いこと）を
そのまま利用する（`test_realsense_source.py` と同じ方針）。

要件: 1.3, 1.4, 1.5, 1.10, 12.7
"""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

import pytest

from sensing_foundation import doctor as doctor_module
from sensing_foundation.config import CaptureConfig
from sensing_foundation.doctor import CheckResult, Doctor

ALL_CHECK_NAMES = (
    "os",
    "python",
    "memory",
    "sdk_import",
    "device",
    "usb",
    "stream_open",
    "power_stability",
    "disk",
)


# ============================================================================
# 全9項目が常に揃う（1項目の失敗が後続をスキップしない）
# ============================================================================


class TestAllChecksAlwaysPresent:
    def test_check_returns_exactly_nine_results(self, tmp_path: Path) -> None:
        doctor = Doctor(destination=tmp_path)
        results = doctor.check()
        assert len(results) == 9

    def test_check_returns_all_expected_names(self, tmp_path: Path) -> None:
        doctor = Doctor(destination=tmp_path)
        results = doctor.check()
        names = {r.name for r in results}
        assert names == set(ALL_CHECK_NAMES)

    def test_check_results_are_in_documented_order(self, tmp_path: Path) -> None:
        doctor = Doctor(destination=tmp_path)
        results = doctor.check()
        assert tuple(r.name for r in results) == ALL_CHECK_NAMES

    def test_all_results_are_check_result_instances(self, tmp_path: Path) -> None:
        doctor = Doctor(destination=tmp_path)
        for result in doctor.check():
            assert isinstance(result, CheckResult)
            assert result.status in ("ok", "warn", "fail", "skip")

    def test_sdk_absent_environment_only_fails_sdk_related_items(
        self, tmp_path: Path
    ) -> None:
        """SDK 非導入環境: OS/Python/メモリは正常、SDK 関連のみ失敗側。

        tasks.md 6.2 の「観測可能な完了状態」そのもの。
        """
        doctor = Doctor(destination=tmp_path)
        by_name = {r.name: r for r in doctor.check()}

        assert by_name["os"].status == "ok"
        assert by_name["python"].status == "ok"
        assert by_name["memory"].status in ("ok", "fail")
        # memory は /proc が読める Linux 環境なら ok になるはず（本テスト環境は WSL）。
        assert by_name["memory"].status == "ok"

        # SDK が実際に無い本環境では、SDK 関連の項目は正常(ok)にはならない。
        assert by_name["sdk_import"].status == "fail"
        assert by_name["device"].status == "fail"
        assert by_name["stream_open"].status == "fail"
        assert by_name["power_stability"].status == "skip"
        # usb は「判定対象が無い」ため fail ではなく skip（本モジュールの設計判断）。
        assert by_name["usb"].status == "skip"

        # disk は SDK に依存しない。
        assert by_name["disk"].status == "ok"


# ============================================================================
# os / python / memory: 実環境に対して実際に動く（モック無し）
# ============================================================================


class TestOsPythonMemoryChecks:
    def test_os_check_reports_ok_with_platform_info(self, tmp_path: Path) -> None:
        doctor = Doctor(destination=tmp_path)
        result = next(r for r in doctor.check() if r.name == "os")
        assert result.status == "ok"
        assert isinstance(result.value, dict)
        assert result.value["system"] == "Linux"
        assert result.value["is_64bit"] is True
        assert "machine" in result.value
        assert "release" in result.value

    def test_python_check_reports_actual_interpreter(self, tmp_path: Path) -> None:
        import sys

        doctor = Doctor(destination=tmp_path)
        result = next(r for r in doctor.check() if r.name == "python")
        assert result.status == "ok"
        assert isinstance(result.value, dict)
        assert result.value["executable"] == sys.executable
        assert result.value["version"] == f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    def test_memory_check_reports_real_ram_via_sysstat(self, tmp_path: Path) -> None:
        doctor = Doctor(destination=tmp_path)
        result = next(r for r in doctor.check() if r.name == "memory")
        assert result.status == "ok"
        assert isinstance(result.value, dict)
        assert isinstance(result.value["system_total_bytes"], int)
        assert result.value["system_total_bytes"] > 0
        assert isinstance(result.value["system_available_bytes"], int)


# ============================================================================
# sdk_import / device: probe_sdk() / probe_devices() を経由し、SDK 非導入では失敗
# ============================================================================


class TestSdkAndDeviceChecks:
    def test_sdk_import_fails_with_informative_detail(self, tmp_path: Path) -> None:
        doctor = Doctor(destination=tmp_path)
        result = next(r for r in doctor.check() if r.name == "sdk_import")
        assert result.status == "fail"
        assert "pyrealsense2" in result.detail
        assert isinstance(result.value, dict)
        assert result.value["available"] is False

    def test_device_fails_when_sdk_unavailable(self, tmp_path: Path) -> None:
        doctor = Doctor(destination=tmp_path)
        result = next(r for r in doctor.check() if r.name == "device")
        assert result.status == "fail"
        assert isinstance(result.value, dict)
        assert result.value["available"] is False


# ============================================================================
# usb: 判定不能時は skip（本モジュールの設計判断。fail にはしない）
# ============================================================================


class TestUsbCheckSkipDecision:
    def test_usb_is_skip_not_fail_when_sdk_unavailable(self, tmp_path: Path) -> None:
        """USB 接続種別は「判定対象（デバイス）が存在しない」ため fail ではなく skip。

        `device`/`sdk_import` が既に SDK 未導入を fail として報告しており、
        `usb` まで fail にすると同じ根本原因が3項目に重複して表れる
        （doctor.py モジュール docstring「usb」参照）。
        """
        doctor = Doctor(destination=tmp_path)
        result = next(r for r in doctor.check() if r.name == "usb")
        assert result.status == "skip"
        assert result.value is None


# ============================================================================
# stream_open / power_stability: SDK/デバイス無しでも Doctor.check() をクラッシュさせない
# ============================================================================


class TestStreamOpenAndPowerStabilityGracefulFailure:
    def test_stream_open_fails_gracefully_without_sdk(self, tmp_path: Path) -> None:
        doctor = Doctor(destination=tmp_path)
        # check() 自体が例外を送出しないことがこのテストの主眼。
        result = next(r for r in doctor.check() if r.name == "stream_open")
        assert result.status == "fail"
        assert "pyrealsense2" in result.detail

    def test_power_stability_does_not_raise_without_sdk(self, tmp_path: Path) -> None:
        doctor = Doctor(destination=tmp_path)
        result = next(r for r in doctor.check() if r.name == "power_stability")
        assert result.status == "skip"

    def test_power_stability_uses_not_observed_framing_not_no_problem(
        self, tmp_path: Path
    ) -> None:
        """「問題なし」ではなく「この条件では観測されなかった」という表現を使う。

        design.md Doctor「Risks」の直接要求。SDK が無く試行できない場合の
        `detail` に、この言い回し（または同等の表現）が含まれることを固定する。
        """
        doctor = Doctor(destination=tmp_path)
        result = next(r for r in doctor.check() if r.name == "power_stability")
        assert "観測されなかった" in result.detail
        # 「問題なし」という断定的な表現は使わない。
        assert "問題なし" not in result.detail

    def test_doctor_check_does_not_raise_even_with_custom_capture_config(
        self, tmp_path: Path
    ) -> None:
        # stream_open / power_stability が RealSenseSource を実際に構築する
        # ため、独自の CaptureConfig を渡しても check() が例外を送出しない
        # ことを確認する（構築時の防御が機能していることの間接的な確認）。
        cfg = CaptureConfig(width_px=320, height_px=240, fps=15)
        doctor = Doctor(capture_config=cfg, destination=tmp_path)
        results = doctor.check()
        assert len(results) == 9


# ============================================================================
# disk: 実ファイルシステムに対する空き容量・書き込み速度の目安
# ============================================================================


class TestDiskCheck:
    def test_disk_check_reports_real_free_space_for_tmp_path(self, tmp_path: Path) -> None:
        doctor = Doctor(destination=tmp_path)
        result = next(r for r in doctor.check() if r.name == "disk")
        assert result.status == "ok"
        assert isinstance(result.value, dict)
        assert result.value["path"] == str(tmp_path)
        assert isinstance(result.value["free_bytes"], int)
        assert result.value["free_bytes"] > 0
        assert isinstance(result.value["total_bytes"], int)
        assert result.value["total_bytes"] >= result.value["free_bytes"]

    def test_disk_check_includes_write_speed_estimate(self, tmp_path: Path) -> None:
        doctor = Doctor(destination=tmp_path)
        result = next(r for r in doctor.check() if r.name == "disk")
        assert result.status == "ok"
        speed = result.value["write_speed_mb_s"]
        # 実ファイルシステムへの書き込みが成功する環境では非 None のはず。
        assert speed is None or speed > 0

    def test_disk_check_creates_missing_destination_directory(self, tmp_path: Path) -> None:
        destination = tmp_path / "does" / "not" / "exist" / "yet"
        assert not destination.exists()
        doctor = Doctor(destination=destination)
        result = next(r for r in doctor.check() if r.name == "disk")
        assert result.status == "ok"
        assert destination.exists()

    def test_disk_probe_temp_file_is_cleaned_up(self, tmp_path: Path) -> None:
        doctor = Doctor(destination=tmp_path)
        doctor.check()
        leftover = list(tmp_path.glob(".doctor_disk_probe_*"))
        assert leftover == []

    def test_default_destination_is_recording_config_root(self) -> None:
        from sensing_foundation.config import RecordingConfig

        doctor = Doctor()
        assert doctor._destination == RecordingConfig().root  # noqa: SLF001


# ============================================================================
# check() 自身の防御網: どの check 関数が例外を送出しても fail へ変換し、
# 残りの項目の実行を妨げない（task 6.2 の観測可能な完了状態そのもの）
# ============================================================================


class TestOuterCheckGuardConvertsUnrelatedExceptions:
    """`Doctor.check()` の外側の防御網（各 check 呼び出しを包む try/except）を検証する。

    ここまでの他のテスト（`sdk_import` / `device` / `stream_open` /
    `power_stability` が `fail`/`skip` になるケース）は、いずれも各項目
    「自分自身の」内部 `try/except`（`SourceUnavailableError` 等の捕捉）
    経由でしか `fail` に到達しておらず、`check()` 自体が持つ外側の防御網
    （どの check 関数が例外を送出しても `fail` の `CheckResult` へ変換し、
    残り8項目を実行し続ける）は一度も通っていなかった。ここでは
    SDK/ハードウェアと無関係な汎用例外を内部メソッドへ直接注入し、
    外側の防御網そのものの正しさ（正しい `name` に正しく束縛されるか、
    他の項目を巻き込んで中断しないか）を検証する。

    `Doctor.__init__` は `self._check_python` のような束縛メソッドを構築時に
    `_check_functions` タプルへ格納するため、パッチはインスタンス構築
    **より前**にクラス属性へ当てる必要がある（構築後に `Doctor._check_python`
    を差し替えても、既に格納済みの束縛メソッドには影響しない）。
    """

    def test_generic_exception_in_one_check_becomes_fail_without_affecting_others(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        def _boom(self: Doctor) -> CheckResult:  # noqa: ARG001
            raise RuntimeError("boom-forced-generic-failure")

        monkeypatch.setattr(Doctor, "_check_python", _boom)

        doctor = Doctor(destination=tmp_path)
        results = doctor.check()

        assert len(results) == 9
        by_name = {r.name: r for r in results}

        python_result = by_name["python"]
        assert python_result.status == "fail"
        assert python_result.name == "python"
        assert "boom-forced-generic-failure" in python_result.detail
        assert "RuntimeError" in python_result.detail
        assert python_result.value is None

        # 他の8項目は影響を受けず、通常通り実行されている（この固定された
        # 環境における通常の status のまま——強制した例外の影響を一切
        # 受けていないことを確認する）。
        assert by_name["os"].status == "ok"
        assert by_name["memory"].status == "ok"
        assert by_name["disk"].status == "ok"
        assert by_name["sdk_import"].status == "fail"
        assert by_name["device"].status == "fail"
        assert by_name["usb"].status == "skip"
        assert by_name["stream_open"].status == "fail"
        assert by_name["power_stability"].status == "skip"

    def test_generic_exceptions_in_two_independent_checks_are_both_reported_correctly(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """複数項目が同時に汎用例外で落ちても、それぞれ正しい `name` に紐づく。

        外側の防御網がループの中で `name`/`detail` の対応関係を取り違えて
        いないか（例えばクロージャ変数の使い回しバグなど）を、2項目を
        同時に強制失敗させることでより強く固定する。
        """

        def _boom_os(self: Doctor) -> CheckResult:  # noqa: ARG001
            raise RuntimeError("os-check-forced-failure")

        def _boom_memory(self: Doctor) -> CheckResult:  # noqa: ARG001
            raise ValueError("memory-check-forced-failure")

        monkeypatch.setattr(Doctor, "_check_os", _boom_os)
        monkeypatch.setattr(Doctor, "_check_memory", _boom_memory)

        doctor = Doctor(destination=tmp_path)
        results = doctor.check()

        assert len(results) == 9
        by_name = {r.name: r for r in results}

        assert by_name["os"].status == "fail"
        assert by_name["os"].name == "os"
        assert "os-check-forced-failure" in by_name["os"].detail

        assert by_name["memory"].status == "fail"
        assert by_name["memory"].name == "memory"
        assert "memory-check-forced-failure" in by_name["memory"].detail

        # python/disk など、強制失敗させていない項目はこの介入の影響を
        # 受けない。
        assert by_name["python"].status == "ok"
        assert by_name["disk"].status == "ok"


# ============================================================================
# report_json(): 実施結果として保存できる機械可読形式（往復可能）
# ============================================================================


class TestReportJson:
    def test_report_json_round_trips_all_nine_checks(self, tmp_path: Path) -> None:
        doctor = Doctor(destination=tmp_path)
        payload = json.loads(doctor.report_json())
        assert isinstance(payload, list)
        assert len(payload) == 9
        names = {entry["name"] for entry in payload}
        assert names == set(ALL_CHECK_NAMES)
        for entry in payload:
            assert set(entry.keys()) == {"name", "status", "detail", "value"}
            assert entry["status"] in ("ok", "warn", "fail", "skip")

    def test_report_json_is_valid_json_string(self, tmp_path: Path) -> None:
        doctor = Doctor(destination=tmp_path)
        report = doctor.report_json()
        assert isinstance(report, str)
        json.loads(report)  # 例外を送出しなければ良い

    def test_report_json_reflects_actual_check_results(self, tmp_path: Path) -> None:
        doctor = Doctor(destination=tmp_path)
        results = doctor.check()
        payload = json.loads(doctor.report_json())
        # check() を2回呼んでいる（直接1回、report_json 内部で1回）が、この
        # 固定された環境（SDK 非導入・実ファイルシステム）では各項目の
        # status は決定的なので、そのまま一致することを期待できる。
        by_name_status = {entry["name"]: entry["status"] for entry in payload}
        for result in results:
            assert by_name_status[result.name] == result.status


# ============================================================================
# Doctor は capture 本体（open_source / CLI）に依存せず独立して構築・実行できる
# ============================================================================


class TestIndependentFromCaptureBody:
    def test_doctor_constructible_with_no_arguments(self) -> None:
        # open_source() や CLI 配線を一切経由せずに構築できる。
        doctor = Doctor()
        assert isinstance(doctor, Doctor)

    def test_doctor_module_does_not_import_open_source_or_cli(self) -> None:
        # 散文（docstring）中に `open_source()` という語が現れることはあるが、
        # 実際の import 文としては現れないことを AST で確認する（本文中の
        # 単純な文字列一致では docstring の言及を誤検出するため使わない）。
        source_code = inspect.getsource(doctor_module)
        tree = ast.parse(source_code)
        imported_names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported_names.append(node.module)
                imported_names.extend(alias.name for alias in node.names)
        assert not any("cli" in name for name in imported_names)
        assert "open_source" not in imported_names
        # `sensing_foundation.source`（単数形、`open_source()` を定義する
        # モジュール）は import しない。`sensing_foundation.sources.realsense`
        # （複数形、SDK probe 関数のモジュール）は import してよい —
        # 名前が似ているため厳密に区別する。
        assert "sensing_foundation.source" not in imported_names


# ============================================================================
# 依存境界: doctor.py は pyrealsense2 を一切 import しない（AST 静的解析）
# ============================================================================


class TestImportBoundaryNeverImportsPyrealsense2:
    """`doctor.py` は SDK への問い合わせを `sources.realsense` の probe 関数 /
    `RealSenseSource` に委ね、自身は `pyrealsense2` を（モジュール直下・
    関数内を問わず）一切 import しないことを固定する（依存方向表「7 |
    doctor」の規約）。`test_realsense_source.py::TestImportBoundary` の
    「関数内でなら import してよい」という方針とは対照的に、本モジュールは
    「関数内であっても import しない」ことを要求される点に注意。
    """

    def test_pyrealsense2_is_never_imported_anywhere_in_the_module(self) -> None:
        source_code = inspect.getsource(doctor_module)
        tree = ast.parse(source_code)

        offending: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module] if node.module else []
            else:
                continue
            if any(name and "pyrealsense2" in name for name in names):
                offending.append(ast.dump(node))

        assert offending == [], f"doctor.py が pyrealsense2 を import している: {offending}"

    def test_pyrealsense2_substring_does_not_appear_as_a_literal_import_name(
        self,
    ) -> None:
        # 補助チェック: ソース中に "import pyrealsense2" という字面が
        # そもそも存在しないことも固定する（動的 import 文字列などの
        # 回避策も含めて封じる意図の簡易ガード）。
        source_code = inspect.getsource(doctor_module)
        assert "import pyrealsense2" not in source_code

    def test_doctor_module_only_reaches_sdk_via_sources_realsense(self) -> None:
        source_code = inspect.getsource(doctor_module)
        assert "from sensing_foundation.sources.realsense import" in source_code
        assert "probe_sdk" in source_code
        assert "probe_devices" in source_code
        assert "RealSenseSource" in source_code


# ============================================================================
# CheckResult 自体の形（design.md のインタフェースそのまま）
# ============================================================================


class TestCheckResultShape:
    def test_check_result_is_frozen_dataclass_with_expected_fields(self) -> None:
        result = CheckResult(name="x", status="ok", detail="d", value=None)
        assert result.name == "x"
        assert result.status == "ok"
        assert result.detail == "d"
        assert result.value is None
        with pytest.raises(AttributeError):
            result.name = "y"  # type: ignore[misc]
