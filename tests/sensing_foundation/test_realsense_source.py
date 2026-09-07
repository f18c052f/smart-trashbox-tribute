"""`sensing_foundation.sources.realsense` に対するテスト（タスク 6.1 / 6.3）。

観測可能な完了状態（tasks.md 6.1）を固定する:

- SDK 非導入環境では専用の例外（`SourceUnavailableError`、「次に何を確認
  すべきか」の案内文つき）が出て、他の入力元は動作する
- SDK を模したモックに対しては、共通表現の系列・欠落検出・USB2 警告が
  期待どおり得られる

あわせて design.md「RealSenseSource（live）」・要件 1.4, 1.5, 2.5, 2.6, 3.5,
3.6, 11.7, 12.1 が定める以下の点も固定する:

- Depth のみ既定で構成され、Color は `color_enabled=True` のときだけ追加される
- フレームキュー容量・グローバル時刻有効化はベストエフォート（失敗しても
  起動を失敗させない）で、有効化できたかどうかを `global_time_enabled` として
  観測できる
- USB2 判定は `usb2_warning`（`bool | None`。取得不能なら `None` で欠測を表す）
  として観測できる
- **タスク 6.3**: `start()` が実際に開いた個体の識別情報を `device_identity`
  として観測できる（`probe_devices()` の列挙結果ではなく、パイプラインが
  開いた個体を指すこと。取得できない項目は `None` で欠測を表すこと）
- 要求モードが拒否された場合は `start()` の時点で `DeviceNotReadyError`
  （`SourceUnavailableError` のサブクラス）を送出し、黙って別モードへ
  フォールバックしない
- Point Cloud を生成しない・GUI を必要としない（design-adherence の
  静的チェック）
- `pyrealsense2` はモジュール内で関数内だけから import される（AST 検証）
- `probe_sdk()` / `probe_devices()` は SDK 有無の両方で例外を送出せず、
  `available` フラグで報告する
- `_drain_latest()` は（`SimulatedSource`/`RecordedSource` と異なり）実際に
  SDK の `poll_for_frames()` 相当へ問い合わせて最新へ追いつく

**`pyrealsense2` のモック方針**: 本環境には実 SDK が無いため、
`sys.modules["pyrealsense2"]` へ最小限のフェイクモジュールを
`monkeypatch.setitem` で注入する。`RealSenseSource` は SDK を関数内で
遅延 import する設計のため、この注入は `source.start()` 呼び出し時点で
自然に効く。フェイクモジュールの形は `realsense.py` モジュール docstring
「前提とする pyrealsense2 API 形状」に厳密に合わせてある——本テストファイル
自身がその前提の一次ドキュメントを兼ねる。

「SDK 非導入」のケースだけは実環境の事実（`pyrealsense2` が本当に
インストールされていない）をそのまま利用し、モックを一切使わない。

要件: 1.4, 1.5, 2.5, 2.6, 3.5, 3.6, 11.7, 12.1
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import sys

import pytest

# フェイク pyrealsense2 の部品は `fakerealsense.py`（テスト用共有ヘルパ。
# `synthetic.py` と同じ位置づけ）にある。タスク 8.3 の `record` テストも
# 同じ形状を使うため、SDK 形状の前提が2箇所へ分かれて食い違わないよう
# 1つのモジュールへ集約している。
from fakerealsense import (
    FakeDevice,
    FakeFrameset,
    FakeIntrinsics,
    FakePipeline,
    FakeSensor,
    make_fake_rs_module,
    make_frameset,
)
from synthetic import make_depth_frame

from sensing_foundation.config import CaptureConfig
from sensing_foundation.errors import DeviceNotReadyError, SourceUnavailableError
from sensing_foundation.metrics import CaptureMetrics
from sensing_foundation.sources import realsense as realsense_module
from sensing_foundation.sources.realsense import (
    RealSenseSource,
    probe_devices,
    probe_sdk,
)
from sensing_foundation.timebase import SessionClock
from sensing_foundation.types import CaptureFrame, SourceKind, TimestampDomain

WIDTH_PX = 8
HEIGHT_PX = 6


# ============================================================================
# フェイクロガー（`tests/sensing_foundation/test_recorded_source.py` と同型）
# ============================================================================


class FakeLogger:
    """`Logger` プロトコルに構造的に適合する、`emit()` 呼び出しを記録するだけの偽実装。"""

    def __init__(self) -> None:
        self.enabled = True
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def emit(self, stage: str, event: str, /, **data: object) -> None:
        self.calls.append((stage, event, dict(data)))

    def stage(self, stage: str):  # pragma: no cover - 本テストでは未使用
        raise NotImplementedError

    def timed(self, stage: str, event: str, /, **data: object):  # pragma: no cover
        raise NotImplementedError

    def stats(self):  # pragma: no cover - 本テストでは未使用
        raise NotImplementedError

    def close(self, timeout_ms: float = 1000.0) -> None:  # pragma: no cover
        return None


def _make_metrics_and_clock(
    session_id: str = "test-realsense-source",
) -> tuple[CaptureMetrics, SessionClock]:
    clock = SessionClock(session_id=session_id)
    metrics = CaptureMetrics(FakeLogger(), clock, sysstat=None)
    return metrics, clock


def _default_capture_config(**overrides: object) -> CaptureConfig:
    base = {
        "width_px": WIDTH_PX,
        "height_px": HEIGHT_PX,
        "fps": 30,
        "color_enabled": False,
        "queue_capacity": 1,
        "drain_enabled": False,
        "acquire_timeout_ms": 1000,
        "on_acquire_error": "continue",
    }
    base.update(overrides)
    return CaptureConfig(**base)  # type: ignore[arg-type]


def _build_source(
    monkeypatch: pytest.MonkeyPatch,
    *,
    capture_config: CaptureConfig | None = None,
    wait_frames: list[FakeFrameset] | None = None,
    poll_frames: list[FakeFrameset] | None = None,
    device: FakeDevice | None = None,
    intrinsics: "FakeIntrinsics | None" = None,
    reject_start: bool = False,
    context_devices: list[FakeDevice] | None = None,
) -> tuple[RealSenseSource, FakePipeline]:
    """モック済み `pyrealsense2` を `sys.modules` へ注入した `RealSenseSource` を返す。"""
    capture_config = capture_config if capture_config is not None else _default_capture_config()
    metrics, clock = _make_metrics_and_clock()
    pipeline = FakePipeline(
        reject_start=reject_start,
        wait_frames=wait_frames,
        poll_frames=poll_frames,
        device=device,
        intrinsics=intrinsics,
    )
    fake_rs = make_fake_rs_module(pipeline, context_devices=context_devices)
    monkeypatch.setitem(sys.modules, "pyrealsense2", fake_rs)

    source = RealSenseSource(capture_config, metrics, clock=clock)
    return source, pipeline


def _take(source: RealSenseSource, n: int) -> list[CaptureFrame]:
    """`frames()`（live のため無限ジェネレータ）から先頭 `n` 枚だけ取り出す。"""
    it = source.frames()
    return [next(it) for _ in range(n)]


# ============================================================================
# SDK 非導入環境（実環境の事実をそのまま使う。モック無し）
# ============================================================================


class TestSdkNotInstalled:
    """観測可能な完了状態: SDK 非導入環境では専用の例外が出て、他の入力元は動作する。

    本環境には実際に `pyrealsense2` が存在しないため、モックを一切使わずに
    このケースを検証できる。
    """

    def test_start_raises_source_unavailable_error_with_actionable_guidance(self) -> None:
        capture_config = _default_capture_config()
        metrics, clock = _make_metrics_and_clock()
        source = RealSenseSource(capture_config, metrics, clock=clock)

        with pytest.raises(SourceUnavailableError) as exc_info:
            source.start()

        message = str(exc_info.value)
        assert "pyrealsense2" in message
        assert "doctor" in message
        assert "sdk_import" in message

    def test_other_sources_still_work_when_sdk_absent(self, tmp_path) -> None:
        # SimulatedSource は pyrealsense2 に一切触れない（要件 4.4）。
        # RealSenseSource が SDK 非導入で失敗しても、この経路は無傷である
        # ことを示す最小限のスモークチェック。
        from sensing_foundation.sources.simulated import SimulatedSource
        from sensing_foundation.types import StreamProfile

        metrics, clock = _make_metrics_and_clock("test-other-source-unaffected")
        profile = StreamProfile(
            width_px=WIDTH_PX,
            height_px=HEIGHT_PX,
            fps=30,
            depth_scale_mm=1.0,
            color_enabled=False,
            intrinsics=None,
        )

        def supplier(index: int):
            return make_depth_frame(WIDTH_PX, HEIGHT_PX, index) if index < 2 else None

        sim_source = SimulatedSource(supplier, profile, metrics, clock=clock)
        with sim_source:
            frames = list(sim_source.frames())
        assert len(frames) == 2

    def test_probe_sdk_reports_unavailable_without_raising(self) -> None:
        result = probe_sdk()
        assert result["available"] is False
        assert result["version"] is None
        assert result["location"] is None
        assert isinstance(result["error"], str)
        assert "pyrealsense2" in result["error"]  # type: ignore[operator]

    def test_probe_devices_reports_unavailable_without_raising(self) -> None:
        result = probe_devices()
        assert result["available"] is False
        assert result["devices"] == ()
        assert isinstance(result["error"], str)


# ============================================================================
# SDK モック: 基本的な取得系列
# ============================================================================


class TestBasicAcquisitionAgainstMockedSdk:
    """観測可能な完了状態: SDK を模したモックに対して共通表現の系列が得られる。"""

    def test_acquired_frames_have_common_representation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        frames_in = [make_frameset(0), make_frameset(1)]
        source, _ = _build_source(monkeypatch, wait_frames=frames_in)

        with source:
            frames = _take(source, 2)

        assert [f.seq for f in frames] == [0, 1]
        assert [f.index for f in frames] == [0, 1]
        for frame in frames:
            assert frame.source == SourceKind.LIVE
            assert frame.depth.shape == (HEIGHT_PX, WIDTH_PX)
            assert frame.depth.dtype.name == "uint16"

    def test_kind_is_live_immediately_after_construction(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source, _ = _build_source(monkeypatch, wait_frames=[])
        assert source.kind == SourceKind.LIVE


# ============================================================================
# 欠落検出（`RawFrame.seq` を正しく SDK から橋渡しできているかを確認する。
# 欠落検出そのものは BaseFrameSource（タスク 3.1）の責務。）
# ============================================================================


class TestGapDetectionSeqPassthrough:
    def test_frame_number_gap_is_detected_via_reported_seq(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        frames_in = [make_frameset(0), make_frameset(1), make_frameset(3)]  # 2 を欠番
        source, _ = _build_source(monkeypatch, wait_frames=frames_in)

        with source:
            frames = _take(source, 3)

        assert [f.seq for f in frames] == [0, 1, 3]
        assert [f.gap_before for f in frames] == [0, 0, 1]


# ============================================================================
# ドレイン: 実際に SDK の poll_for_frames 相当へ問い合わせて最新へ追いつく
# （SimulatedSource / RecordedSource と異なり、これが本物の drain 実装）
# ============================================================================


class TestDrainActuallyQueriesSdk:
    def test_drain_latest_polls_until_empty_and_keeps_newest(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        poll_frames = [make_frameset(10), make_frameset(11), make_frameset(12)]
        source, _ = _build_source(
            monkeypatch,
            capture_config=_default_capture_config(drain_enabled=True),
            wait_frames=[make_frameset(9)],
            poll_frames=poll_frames,
        )
        source.start()

        latest, discarded = source._drain_latest()  # noqa: SLF001 - ホワイトボックス検証

        assert discarded == 2
        assert latest is not None
        assert latest.seq == 12

    def test_drain_latest_returns_none_and_zero_when_queue_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source, _ = _build_source(
            monkeypatch,
            capture_config=_default_capture_config(drain_enabled=True),
            wait_frames=[],
            poll_frames=[],
        )
        source.start()

        assert source._drain_latest() == (None, 0)  # noqa: SLF001

    def test_end_to_end_frames_uses_drained_latest_and_counts_dropped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        poll_frames = [make_frameset(10), make_frameset(11), make_frameset(12)]
        source, _ = _build_source(
            monkeypatch,
            capture_config=_default_capture_config(drain_enabled=True),
            wait_frames=[make_frameset(9)],
            poll_frames=poll_frames,
        )

        with source:
            frames = _take(source, 1)

        assert frames[0].seq == 12
        assert frames[0].dropped_before == 2
        assert source.stats.frames_dropped == 2


class TestDrainEnabledRespectsCaptureConfig:
    """live は `capture_config.drain_enabled` をそのまま尊重する

    （`SimulatedSource`/`RecordedSource` の「常に False へ固定する」二重防御
    とは対照的——モジュール docstring「ドレインは基底クラスの判断に委ねる」）。
    """

    def test_drain_enabled_true_is_not_forced_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        source, _ = _build_source(
            monkeypatch, capture_config=_default_capture_config(drain_enabled=True)
        )
        assert source._drain_enabled is True  # noqa: SLF001

    def test_drain_enabled_false_stays_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        source, _ = _build_source(
            monkeypatch, capture_config=_default_capture_config(drain_enabled=False)
        )
        assert source._drain_enabled is False  # noqa: SLF001


# ============================================================================
# 取得失敗（フレーム番号が取れない等）は「観測された事実」として継続する
# ============================================================================


class TestAcquireFailureIsObservedNotRaised:
    def test_frame_number_unavailable_is_treated_as_acquire_failure_and_continues(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        broken = make_frameset(999, fail_frame_number=True)
        good = make_frameset(7)
        source, _ = _build_source(
            monkeypatch,
            capture_config=_default_capture_config(on_acquire_error="continue"),
            wait_frames=[broken, good],
        )

        with source:
            frames = _take(source, 1)

        assert frames[0].seq == 7
        assert source.stats.acquire_errors == 1


# ============================================================================
# USB2 警告（要件 1.4, 1.5）
# ============================================================================


class TestUsb2Warning:
    def test_usb2_connection_is_flagged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        device = FakeDevice(usb_type="2.1")
        source, _ = _build_source(monkeypatch, device=device)
        source.start()
        assert source.usb2_warning is True

    def test_usb3_connection_is_not_flagged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        device = FakeDevice(usb_type="3.2")
        source, _ = _build_source(monkeypatch, device=device)
        source.start()
        assert source.usb2_warning is False

    def test_missing_usb_type_descriptor_is_reported_as_missing_not_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        device = FakeDevice(usb_type=None)
        source, _ = _build_source(monkeypatch, device=device)
        source.start()
        # 「取れないものは欠測として残す」（要件 3.5）。False を偽装しない。
        assert source.usb2_warning is None

    def test_usb2_warning_is_none_before_start(self, monkeypatch: pytest.MonkeyPatch) -> None:
        source, _ = _build_source(monkeypatch)
        assert source.usb2_warning is None


# ============================================================================
# 開いたデバイスの識別情報（タスク 6.3。要件 1.4 / 5.2）
# ============================================================================


class TestDeviceIdentity:
    """`start()` が実際に開いた個体の識別情報を公開する（タスク 6.3）。

    記録側（タスク 8.3）が `manifest.json` の `device` を埋めるための唯一の
    入手経路である。要件 5.2「メタ情報にデバイス識別情報を含める」。
    """

    def test_identity_is_none_before_start(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """デバイスを開く前は「まだ分からない」を `None` で表す。

        `usb2_warning` と同じ流儀（構築しただけでは何も観測していない）。
        """
        source, _ = _build_source(monkeypatch)
        assert source.device_identity is None

    def test_identity_is_resolved_after_start(self, monkeypatch: pytest.MonkeyPatch) -> None:
        device = FakeDevice(
            name="Intel RealSense D435",
            serial="834412071095",
            firmware="5.17.3.10",
            usb_type="3.2",
            product_line="D400",
        )
        source, _ = _build_source(monkeypatch, device=device)
        source.start()

        identity = source.device_identity
        assert identity is not None
        assert identity.name == "Intel RealSense D435"
        assert identity.serial_number == "834412071095"
        assert identity.firmware_version == "5.17.3.10"
        assert identity.usb_type_descriptor == "3.2"
        assert identity.product_line == "D400"

    def test_identity_is_the_opened_device_not_the_first_enumerated_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """複数台つながっているとき、**パイプラインが開いた個体**を指す。

        tasks.md 6.3「`probe_devices()` の流用で代替しない」の核心。
        列挙の先頭とパイプラインが開いた個体をわざと食い違わせているので、
        `probe_devices()` の結果を流用する実装はこのテストで落ちる。
        """
        opened = FakeDevice(serial="SN-OPENED", firmware="5.17.3.10")
        other = FakeDevice(serial="SN-OTHER", firmware="5.12.0.0")
        source, _ = _build_source(
            monkeypatch, device=opened, context_devices=[other, opened]
        )
        source.start()

        # 前提の確認: 列挙の先頭は「開いていない方」である（この前提が崩れると
        # 以下の表明が食い違いを検出できなくなり、テストが空振りする）。
        enumerated = probe_devices()["devices"]
        assert enumerated[0]["serial_number"] == "SN-OTHER"  # type: ignore[index]

        identity = source.device_identity
        assert identity is not None
        assert identity.serial_number == "SN-OPENED"
        assert identity.firmware_version == "5.17.3.10"

    def test_items_the_sdk_refuses_are_missing_not_faked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """取得できない項目は `None`（欠測）。空文字や既定値で埋めない（要件 3.5）。"""
        device = FakeDevice(firmware=None, usb_type=None, product_line=None)
        source, _ = _build_source(monkeypatch, device=device)
        source.start()

        identity = source.device_identity
        assert identity is not None
        assert identity.firmware_version is None
        assert identity.usb_type_descriptor is None
        assert identity.product_line is None
        # 取れる項目は落とさない（1項目の欠測が他を巻き込まない）。
        assert identity.name == "Intel RealSense D435"
        assert identity.serial_number == "SN-0001"

    def test_camera_info_without_product_line_is_missing_not_an_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`rs.camera_info` に該当の列挙値自体が無い SDK ビルドでも起動できる。

        `get_info()` が例外を送出する場合とは別の失敗の形である
        （design.md Risks「SDK のビルド構成によって取得できるメタデータが変わる」）。
        """
        device = FakeDevice()
        pipeline = FakePipeline(device=device)
        fake_rs = make_fake_rs_module(pipeline)
        del fake_rs.camera_info.product_line  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "pyrealsense2", fake_rs)
        metrics, clock = _make_metrics_and_clock()
        source = RealSenseSource(_default_capture_config(), metrics, clock=clock)

        source.start()

        identity = source.device_identity
        assert identity is not None
        assert identity.product_line is None
        assert identity.serial_number == "SN-0001"

    def test_usb2_warning_agrees_with_the_recorded_descriptor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """警告フラグと記録される接続種別が同じ1つの観測から出る。

        両者が別々に `get_info()` を叩くと、記録には `"2.1"` が残るのに
        警告は立っていない、という食い違いが起こり得る。
        """
        for usb_type, expected in (("2.1", True), ("3.2", False)):
            device = FakeDevice(usb_type=usb_type)
            source, _ = _build_source(monkeypatch, device=device)
            source.start()

            identity = source.device_identity
            assert identity is not None
            assert identity.usb_type_descriptor == usb_type
            assert source.usb2_warning is expected

    def test_identity_survives_stop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """取得を終えた後も読める（記録のメタ情報は取得終了後に書き出され得る）。"""
        source, _ = _build_source(monkeypatch)
        source.start()
        before = source.device_identity
        source.stop()

        assert source.device_identity == before
        assert before is not None
        assert before.serial_number == "SN-0001"

    def test_identity_is_immutable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """呼び出し側が書き換えて記録の内容を汚せない。"""
        source, _ = _build_source(monkeypatch)
        source.start()

        identity = source.device_identity
        assert identity is not None
        with pytest.raises(dataclasses.FrozenInstanceError):
            identity.serial_number = "SN-TAMPERED"  # type: ignore[misc]


# ============================================================================
# Color ストリームの有無（要件 11.7）
# ============================================================================


class TestColorStreamConfiguration:
    def test_color_disabled_by_default_configures_depth_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source, pipeline = _build_source(
            monkeypatch, capture_config=_default_capture_config(color_enabled=False)
        )
        source.start()

        assert pipeline.last_config is not None
        streams = pipeline.last_config.enabled_streams
        assert len(streams) == 1
        assert streams[0][0].name == "depth"

    def test_color_enabled_adds_color_stream(self, monkeypatch: pytest.MonkeyPatch) -> None:
        source, pipeline = _build_source(
            monkeypatch, capture_config=_default_capture_config(color_enabled=True)
        )
        source.start()

        assert pipeline.last_config is not None
        streams = pipeline.last_config.enabled_streams
        assert [s[0].name for s in streams] == ["depth", "color"]
        assert streams[1][3].name == "bgr8"


# ============================================================================
# グローバル時刻の有効化試行とキュー容量（ベストエフォート）
# ============================================================================


class TestGlobalTimeEnable:
    def test_success_is_observable_as_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        device = FakeDevice(sensor=FakeSensor(global_time_supported=True))
        source, _ = _build_source(monkeypatch, device=device)
        source.start()

        assert source.global_time_enabled is True
        assert ("global_time_enabled", 1.0) in device.sensor.set_option_calls

    def test_failure_is_observable_as_false_and_does_not_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        device = FakeDevice(sensor=FakeSensor(global_time_supported=False))
        source, _ = _build_source(monkeypatch, device=device)
        source.start()  # 例外を送出しない

        assert source.global_time_enabled is False

    def test_default_before_start_is_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        source, _ = _build_source(monkeypatch)
        assert source.global_time_enabled is False


class TestQueueCapacityBestEffort:
    def test_queue_capacity_is_applied_when_supported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        device = FakeDevice(sensor=FakeSensor(queue_option_supported=True))
        source, _ = _build_source(
            monkeypatch, capture_config=_default_capture_config(queue_capacity=3), device=device
        )
        source.start()

        assert ("frames_queue_size", 3) in device.sensor.set_option_calls

    def test_unsupported_queue_option_does_not_fail_startup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        device = FakeDevice(sensor=FakeSensor(queue_option_supported=False))
        source, _ = _build_source(monkeypatch, device=device)
        source.start()  # 例外を送出しない


# ============================================================================
# 取得レイテンシ（GLOBAL_TIME ドメインのときのみ算出。要件 3.5）
# ============================================================================


class TestCaptureLatencyMs:
    def test_computed_for_global_time_domain(self, monkeypatch: pytest.MonkeyPatch) -> None:
        source, _ = _build_source(monkeypatch)
        source.start()
        now_wall_ms = source._clock.to_wall_ms(source._clock.now_ms())  # noqa: SLF001
        frameset = make_frameset(1, domain_name="global_time", device_timestamp_ms=now_wall_ms)

        # `_acquire()` は `wait_for_frames()` 経由（キュー未投入のため使えない）
        # のに対し、`_raw_frame_from_frameset()` は frameset -> RawFrame
        # 変換だけを直接検証できるヘルパである。
        raw = source._raw_frame_from_frameset(frameset)  # noqa: SLF001

        assert raw is not None
        assert raw.timestamp_domain == TimestampDomain.GLOBAL_TIME
        assert raw.capture_latency_ms is not None
        assert abs(raw.capture_latency_ms) < 100.0  # 同一プロセス内、ms オーダーの誤差のみ許容

    def test_none_for_non_global_time_domains(self, monkeypatch: pytest.MonkeyPatch) -> None:
        source, _ = _build_source(monkeypatch)
        source.start()

        for domain_name in ("hardware_clock", "system_time", "unknown"):
            frameset = make_frameset(1, domain_name=domain_name, device_timestamp_ms=123.0)
            raw = source._raw_frame_from_frameset(frameset)  # noqa: SLF001
            assert raw is not None
            assert raw.capture_latency_ms is None

    def test_none_when_device_timestamp_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        source, _ = _build_source(monkeypatch)
        source.start()
        frameset = make_frameset(
            1, domain_name="global_time", device_timestamp_ms=0.0, fail_timestamp=True
        )

        raw = source._raw_frame_from_frameset(frameset)  # noqa: SLF001

        assert raw is not None
        assert raw.device_timestamp_ms is None
        assert raw.capture_latency_ms is None


# ============================================================================
# 起動時失敗（要求モード拒否）。要件 1.4 相当。
# ============================================================================


class TestStartupRejectionFailsFast:
    def test_rejected_mode_raises_device_not_ready_error_at_start(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source, _ = _build_source(monkeypatch, reject_start=True)

        with pytest.raises(DeviceNotReadyError) as exc_info:
            source.start()

        # DeviceNotReadyError は SourceUnavailableError のサブクラスとして
        # 捕捉できる（errors.py タスク 1.2 の契約）。
        assert isinstance(exc_info.value, SourceUnavailableError)
        message = str(exc_info.value)
        assert "stream_open" in message or "doctor" in message

    def test_rejection_does_not_silently_fall_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        source, pipeline = _build_source(monkeypatch, reject_start=True)
        with pytest.raises(DeviceNotReadyError):
            source.start()
        # 起動に失敗した以上、パイプラインは "started" 状態になっていない。
        assert pipeline.started is False


# ============================================================================
# 内部パラメータと Depth スケール（要件 3.6）
# ============================================================================


class TestIntrinsicsAndDepthScale:
    def test_intrinsics_are_resolved_from_device_when_available(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        intrinsics = FakeIntrinsics(fx=123.0, fy=124.0, ppx=5.0, ppy=6.0)
        device = FakeDevice(sensor=FakeSensor(depth_scale=0.0005))
        source, _ = _build_source(monkeypatch, device=device, intrinsics=intrinsics)
        source.start()

        resolved = source.profile.intrinsics
        assert resolved is not None
        assert resolved.fx_px == 123.0
        assert resolved.fy_px == 124.0
        assert resolved.ppx_px == 5.0
        assert resolved.ppy_px == 6.0
        assert source.profile.depth_scale_mm == pytest.approx(0.5)

    def test_intrinsics_missing_is_recorded_as_none_not_faked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # `_build_source(..., intrinsics=None)` は「未指定なら既定値を使う」
        # という `FakePipeline.__init__` の意味論のため、実際には
        # `FakeIntrinsics()` に差し替わってしまい狙った状態を作れない
        # （`intrinsics is not None else FakeIntrinsics()` を参照）。
        # ここでは構築後に `pipeline.intrinsics` を直接 `None` へ差し替え、
        # `get_stream().as_video_stream_profile().get_intrinsics()` が
        # 例外を送出する状況を作る。`start()` はこの失敗を吸収して
        # `intrinsics=None`（欠測。要件 3.5）へ変換するはずである。
        capture_config = _default_capture_config()
        metrics, clock = _make_metrics_and_clock()
        pipeline = FakePipeline()
        pipeline.intrinsics = None
        fake_rs = make_fake_rs_module(pipeline)
        monkeypatch.setitem(sys.modules, "pyrealsense2", fake_rs)

        source = RealSenseSource(capture_config, metrics, clock=clock)
        source.start()

        assert source.profile.intrinsics is None

    def test_depth_scale_fallback_when_sensor_query_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        device = FakeDevice(sensor=FakeSensor(depth_scale_raises=True))
        source, _ = _build_source(monkeypatch, device=device)
        source.start()

        assert source.profile.depth_scale_mm == pytest.approx(1.0)


# ============================================================================
# Depth バッファの読み取り専用化（Point Cloud は生成しない。要件 2.6）
# ============================================================================


class TestDepthBufferCopiedOnceAndReadOnly:
    def test_raw_frame_depth_is_read_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        source, _ = _build_source(monkeypatch, wait_frames=[make_frameset(0)])
        source.start()

        raw = source._acquire(1000)  # noqa: SLF001

        assert raw is not None
        assert raw.depth.flags.writeable is False

    def test_capture_frame_depth_is_also_read_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # `CaptureFrame.__post_init__`（types.py, タスク 1.5）が
        # `depth.flags.writeable = False` を独自に立てるため、RawFrame の
        # 時点での読み取り専用化と二重になる。ここではその二重防御込みで
        # 最終的な CaptureFrame も読み取り専用であることを確認する。
        source, _ = _build_source(monkeypatch, wait_frames=[make_frameset(0)])
        with source:
            frames = _take(source, 1)
        assert frames[0].depth.flags.writeable is False


# ============================================================================
# design-adherence: Point Cloud を生成しない・GUI を必要としない（headless）
# ============================================================================


class TestNoPointCloudGeneration:
    def test_module_never_references_point_cloud_apis(self) -> None:
        source_code = inspect.getsource(realsense_module).lower()
        assert "pointcloud" not in source_code
        assert "point_cloud" not in source_code
        assert "calculate(" not in source_code


class TestHeadless:
    def test_module_never_references_gui_apis(self) -> None:
        source_code = inspect.getsource(realsense_module).lower()
        for forbidden in ("imshow", "waitkey", "namedwindow", "cv2.", "tkinter", "qt"):
            assert forbidden not in source_code


# ============================================================================
# 依存境界: pyrealsense2 を import するのは本モジュールのみ、かつ関数内のみ
# ============================================================================


class TestImportBoundary:
    def test_pyrealsense2_import_exists_somewhere_in_the_module(self) -> None:
        source_code = inspect.getsource(realsense_module)
        assert "import pyrealsense2" in source_code

    def test_pyrealsense2_is_never_imported_at_module_top_level(self) -> None:
        source_code = inspect.getsource(realsense_module)
        tree = ast.parse(source_code)

        for node in tree.body:  # モジュール直下（関数の中は含まない）のみを見る
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module] if node.module else []
            else:
                continue
            assert not any(name and "pyrealsense2" in name for name in names), (
                "pyrealsense2 はモジュール直下（関数の外）から import されている: "
                f"{ast.dump(node)}"
            )

    def test_pyrealsense2_import_is_nested_inside_a_function(self) -> None:
        source_code = inspect.getsource(realsense_module)
        tree = ast.parse(source_code)

        found_nested = False
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(func):
                if isinstance(node, ast.Import) and any(
                    "pyrealsense2" in alias.name for alias in node.names
                ):
                    found_nested = True
        assert found_nested, "pyrealsense2 の import が関数内に見つからない"


# ============================================================================
# probe_sdk() / probe_devices()（SDK 有無の両方で例外を送出しない）
# ============================================================================


class TestProbeSdkMocked:
    def test_reports_version_and_location_when_available(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pipeline = FakePipeline()
        fake_rs = make_fake_rs_module(pipeline)
        monkeypatch.setitem(sys.modules, "pyrealsense2", fake_rs)

        result = probe_sdk()

        assert result == {
            "available": True,
            "version": "2.99.9-fake",
            "location": "/fake/path/pyrealsense2.so",
            "error": None,
        }


class TestProbeDevicesMocked:
    def test_reports_enumerated_devices_with_serial_and_firmware(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        device_a = FakeDevice(serial="SN-AAA", firmware="5.1.0")
        device_b = FakeDevice(serial="SN-BBB", firmware="5.2.0", usb_type="2.0")
        pipeline = FakePipeline(device=device_a)
        fake_rs = make_fake_rs_module(pipeline, context_devices=[device_a, device_b])
        monkeypatch.setitem(sys.modules, "pyrealsense2", fake_rs)

        result = probe_devices()

        assert result["available"] is True
        assert result["error"] is None
        devices = result["devices"]
        assert len(devices) == 2  # type: ignore[arg-type]
        assert devices[0]["serial_number"] == "SN-AAA"  # type: ignore[index]
        assert devices[1]["serial_number"] == "SN-BBB"  # type: ignore[index]
        assert devices[1]["usb_type_descriptor"] == "2.0"  # type: ignore[index]

    def test_missing_metadata_is_reported_as_none_not_faked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        device = FakeDevice(usb_type=None)
        pipeline = FakePipeline(device=device)
        fake_rs = make_fake_rs_module(pipeline, context_devices=[device])
        monkeypatch.setitem(sys.modules, "pyrealsense2", fake_rs)

        result = probe_devices()

        assert result["devices"][0]["usb_type_descriptor"] is None  # type: ignore[index]
