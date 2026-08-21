"""`sensing_foundation.sources.realsense` に対するテスト（タスク 6.1）。

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
import inspect
import sys
import types
from typing import Any

import pytest
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


# ============================================================================
# フェイク pyrealsense2 の部品
#
# `realsense.py` モジュール docstring「前提とする pyrealsense2 API 形状」に
# 合わせた最小実装。実 SDK を検証できないため、本テストが「本モジュールが
# 呼ぶ形」を仕様として固定する一次資料を兼ねる。
# ============================================================================


class _Enum:
    """`.name` 属性を持つだけの、pyrealsense2 の pybind11 列挙値を模したもの。"""

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:  # pragma: no cover - デバッグ用
        return f"<enum {self.name}>"


class _FakeConfig:
    def __init__(self) -> None:
        self.enabled_streams: list[tuple[Any, int, int, Any, int]] = []
        self.device_serial: str | None = None

    def enable_device(self, serial: str) -> None:
        self.device_serial = serial

    def enable_stream(self, stream: Any, width: int, height: int, fmt: Any, fps: int) -> None:
        self.enabled_streams.append((stream, width, height, fmt, fps))


class _FakeSensor:
    def __init__(
        self,
        *,
        depth_scale: float = 0.001,
        depth_scale_raises: bool = False,
        global_time_supported: bool = True,
        queue_option_supported: bool = True,
    ) -> None:
        self._depth_scale = depth_scale
        self._depth_scale_raises = depth_scale_raises
        self._global_time_supported = global_time_supported
        self._queue_option_supported = queue_option_supported
        self.set_option_calls: list[tuple[str, object]] = []

    def get_depth_scale(self) -> float:
        if self._depth_scale_raises:
            raise RuntimeError("get_depth_scale not supported")
        return self._depth_scale

    def set_option(self, option: Any, value: object) -> None:
        name = getattr(option, "name", "")
        self.set_option_calls.append((name, value))
        if name == "global_time_enabled" and not self._global_time_supported:
            raise RuntimeError("global_time_enabled not supported by this build")
        if name == "frames_queue_size" and not self._queue_option_supported:
            raise RuntimeError("frames_queue_size not supported by this build")


class _FakeDevice:
    def __init__(
        self,
        *,
        usb_type: str | None = "3.2",
        serial: str = "SN-0001",
        firmware: str = "5.13.0.50",
        name: str = "Intel RealSense D435",
        sensor: "_FakeSensor | None" = None,
    ) -> None:
        info: dict[str, str] = {"serial_number": serial, "firmware_version": firmware, "name": name}
        if usb_type is not None:
            info["usb_type_descriptor"] = usb_type
        self._info = info
        self.sensor = sensor if sensor is not None else _FakeSensor()

    def get_info(self, info_enum: Any) -> str:
        key = info_enum.name
        if key not in self._info:
            raise RuntimeError(f"info not supported by this build: {key}")
        return self._info[key]

    def first_depth_sensor(self) -> _FakeSensor:
        return self.sensor


class _FakeIntrinsics:
    def __init__(
        self,
        *,
        width: int = WIDTH_PX,
        height: int = HEIGHT_PX,
        fx: float = 50.0,
        fy: float = 51.0,
        ppx: float = 4.0,
        ppy: float = 3.0,
        model: str = "brown_conrady",
        coeffs: tuple[float, ...] = (0.0, 0.0, 0.0, 0.0, 0.0),
    ) -> None:
        self.width = width
        self.height = height
        self.fx = fx
        self.fy = fy
        self.ppx = ppx
        self.ppy = ppy
        self.model = model
        self.coeffs = list(coeffs)


class _FakeVideoStreamProfile:
    def __init__(self, intrinsics: "_FakeIntrinsics | None") -> None:
        self._intrinsics = intrinsics

    def get_intrinsics(self) -> _FakeIntrinsics:
        if self._intrinsics is None:
            raise RuntimeError("intrinsics not available for this stream")
        return self._intrinsics


class _FakeStreamProfile:
    def __init__(self, intrinsics: "_FakeIntrinsics | None") -> None:
        self._intrinsics = intrinsics

    def as_video_stream_profile(self) -> _FakeVideoStreamProfile:
        return _FakeVideoStreamProfile(self._intrinsics)


class _FakePipelineProfile:
    def __init__(self, device: _FakeDevice, intrinsics: "_FakeIntrinsics | None") -> None:
        self._device = device
        self._intrinsics = intrinsics

    def get_device(self) -> _FakeDevice:
        return self._device

    def get_stream(self, stream_enum: Any) -> _FakeStreamProfile:
        del stream_enum
        return _FakeStreamProfile(self._intrinsics)


class _FakeDepthFrame:
    def __init__(
        self,
        seq: int,
        depth_array,
        device_timestamp_ms: float,
        domain_name: str,
        *,
        fail_frame_number: bool = False,
        fail_timestamp: bool = False,
        fail_domain: bool = False,
    ) -> None:
        self._seq = seq
        self._depth_array = depth_array
        self._timestamp = device_timestamp_ms
        self._domain_name = domain_name
        self._fail_frame_number = fail_frame_number
        self._fail_timestamp = fail_timestamp
        self._fail_domain = fail_domain

    def __bool__(self) -> bool:
        return True

    def get_frame_number(self) -> int:
        if self._fail_frame_number:
            raise RuntimeError("frame number unavailable")
        return self._seq

    def get_timestamp(self) -> float:
        if self._fail_timestamp:
            raise RuntimeError("timestamp unavailable")
        return self._timestamp

    def get_frame_timestamp_domain(self) -> _Enum:
        if self._fail_domain:
            raise RuntimeError("domain unavailable")
        return _Enum(self._domain_name)

    def get_data(self) -> bytes:
        return self._depth_array.tobytes()


class _FakeFrameset:
    def __init__(self, depth_frame: "_FakeDepthFrame | None") -> None:
        self._depth_frame = depth_frame

    def __bool__(self) -> bool:
        return self._depth_frame is not None

    def get_depth_frame(self) -> "_FakeDepthFrame | None":
        return self._depth_frame


def _make_frameset(
    seq: int,
    *,
    domain_name: str = "hardware_clock",
    device_timestamp_ms: float = 0.0,
    width: int = WIDTH_PX,
    height: int = HEIGHT_PX,
    **frame_kwargs: object,
) -> _FakeFrameset:
    depth = make_depth_frame(width, height, seq)
    depth_frame = _FakeDepthFrame(seq, depth, device_timestamp_ms, domain_name, **frame_kwargs)  # type: ignore[arg-type]
    return _FakeFrameset(depth_frame)


class _FakePipeline:
    def __init__(
        self,
        *,
        reject_start: bool = False,
        wait_frames: list[_FakeFrameset] | None = None,
        poll_frames: list[_FakeFrameset] | None = None,
        device: _FakeDevice | None = None,
        intrinsics: "_FakeIntrinsics | None" = None,
    ) -> None:
        self._reject_start = reject_start
        self._wait_queue = list(wait_frames or [])
        self._poll_queue = list(poll_frames or [])
        self.device = device if device is not None else _FakeDevice()
        self.intrinsics = intrinsics if intrinsics is not None else _FakeIntrinsics()
        self.started = False
        self.stopped = False
        self.last_config: _FakeConfig | None = None

    def start(self, config: _FakeConfig) -> _FakePipelineProfile:
        self.last_config = config
        if self._reject_start:
            raise RuntimeError("Couldn't resolve requests")
        self.started = True
        return _FakePipelineProfile(self.device, self.intrinsics)

    def stop(self) -> None:
        self.stopped = True

    def wait_for_frames(self, timeout_ms: int) -> _FakeFrameset:
        del timeout_ms
        if not self._wait_queue:
            raise RuntimeError("Frame didn't arrive within timeout")
        return self._wait_queue.pop(0)

    def poll_for_frames(self) -> "_FakeFrameset | None":
        if not self._poll_queue:
            return None
        return self._poll_queue.pop(0)


class _FakeContext:
    def __init__(self, devices: list[_FakeDevice]) -> None:
        self._devices = devices

    def query_devices(self) -> list[_FakeDevice]:
        return list(self._devices)


def _make_fake_rs_module(
    pipeline: _FakePipeline, *, context_devices: list[_FakeDevice] | None = None
) -> types.ModuleType:
    fake = types.ModuleType("pyrealsense2")
    fake.__version__ = "2.99.9-fake"  # type: ignore[attr-defined]
    fake.__file__ = "/fake/path/pyrealsense2.so"  # type: ignore[attr-defined]

    fake.stream = types.SimpleNamespace(depth=_Enum("depth"), color=_Enum("color"))  # type: ignore[attr-defined]
    fake.format = types.SimpleNamespace(z16=_Enum("z16"), bgr8=_Enum("bgr8"))  # type: ignore[attr-defined]
    fake.camera_info = types.SimpleNamespace(  # type: ignore[attr-defined]
        usb_type_descriptor=_Enum("usb_type_descriptor"),
        serial_number=_Enum("serial_number"),
        firmware_version=_Enum("firmware_version"),
        name=_Enum("name"),
    )
    fake.option = types.SimpleNamespace(  # type: ignore[attr-defined]
        frames_queue_size=_Enum("frames_queue_size"),
        global_time_enabled=_Enum("global_time_enabled"),
    )

    fake.config = _FakeConfig  # type: ignore[attr-defined]
    fake.pipeline = lambda: pipeline  # type: ignore[attr-defined]

    devices = context_devices if context_devices is not None else [pipeline.device]
    fake.context = lambda: _FakeContext(devices)  # type: ignore[attr-defined]

    return fake


def _build_source(
    monkeypatch: pytest.MonkeyPatch,
    *,
    capture_config: CaptureConfig | None = None,
    wait_frames: list[_FakeFrameset] | None = None,
    poll_frames: list[_FakeFrameset] | None = None,
    device: _FakeDevice | None = None,
    intrinsics: "_FakeIntrinsics | None" = None,
    reject_start: bool = False,
    context_devices: list[_FakeDevice] | None = None,
) -> tuple[RealSenseSource, _FakePipeline]:
    """モック済み `pyrealsense2` を `sys.modules` へ注入した `RealSenseSource` を返す。"""
    capture_config = capture_config if capture_config is not None else _default_capture_config()
    metrics, clock = _make_metrics_and_clock()
    pipeline = _FakePipeline(
        reject_start=reject_start,
        wait_frames=wait_frames,
        poll_frames=poll_frames,
        device=device,
        intrinsics=intrinsics,
    )
    fake_rs = _make_fake_rs_module(pipeline, context_devices=context_devices)
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
        frames_in = [_make_frameset(0), _make_frameset(1)]
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
        frames_in = [_make_frameset(0), _make_frameset(1), _make_frameset(3)]  # 2 を欠番
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
        poll_frames = [_make_frameset(10), _make_frameset(11), _make_frameset(12)]
        source, _ = _build_source(
            monkeypatch,
            capture_config=_default_capture_config(drain_enabled=True),
            wait_frames=[_make_frameset(9)],
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
        poll_frames = [_make_frameset(10), _make_frameset(11), _make_frameset(12)]
        source, _ = _build_source(
            monkeypatch,
            capture_config=_default_capture_config(drain_enabled=True),
            wait_frames=[_make_frameset(9)],
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
        broken = _make_frameset(999, fail_frame_number=True)
        good = _make_frameset(7)
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
        device = _FakeDevice(usb_type="2.1")
        source, _ = _build_source(monkeypatch, device=device)
        source.start()
        assert source.usb2_warning is True

    def test_usb3_connection_is_not_flagged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        device = _FakeDevice(usb_type="3.2")
        source, _ = _build_source(monkeypatch, device=device)
        source.start()
        assert source.usb2_warning is False

    def test_missing_usb_type_descriptor_is_reported_as_missing_not_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        device = _FakeDevice(usb_type=None)
        source, _ = _build_source(monkeypatch, device=device)
        source.start()
        # 「取れないものは欠測として残す」（要件 3.5）。False を偽装しない。
        assert source.usb2_warning is None

    def test_usb2_warning_is_none_before_start(self, monkeypatch: pytest.MonkeyPatch) -> None:
        source, _ = _build_source(monkeypatch)
        assert source.usb2_warning is None


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
        device = _FakeDevice(sensor=_FakeSensor(global_time_supported=True))
        source, _ = _build_source(monkeypatch, device=device)
        source.start()

        assert source.global_time_enabled is True
        assert ("global_time_enabled", 1.0) in device.sensor.set_option_calls

    def test_failure_is_observable_as_false_and_does_not_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        device = _FakeDevice(sensor=_FakeSensor(global_time_supported=False))
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
        device = _FakeDevice(sensor=_FakeSensor(queue_option_supported=True))
        source, _ = _build_source(
            monkeypatch, capture_config=_default_capture_config(queue_capacity=3), device=device
        )
        source.start()

        assert ("frames_queue_size", 3) in device.sensor.set_option_calls

    def test_unsupported_queue_option_does_not_fail_startup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        device = _FakeDevice(sensor=_FakeSensor(queue_option_supported=False))
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
        frameset = _make_frameset(1, domain_name="global_time", device_timestamp_ms=now_wall_ms)

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
            frameset = _make_frameset(1, domain_name=domain_name, device_timestamp_ms=123.0)
            raw = source._raw_frame_from_frameset(frameset)  # noqa: SLF001
            assert raw is not None
            assert raw.capture_latency_ms is None

    def test_none_when_device_timestamp_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        source, _ = _build_source(monkeypatch)
        source.start()
        frameset = _make_frameset(
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
        intrinsics = _FakeIntrinsics(fx=123.0, fy=124.0, ppx=5.0, ppy=6.0)
        device = _FakeDevice(sensor=_FakeSensor(depth_scale=0.0005))
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
        # という `_FakePipeline.__init__` の意味論のため、実際には
        # `_FakeIntrinsics()` に差し替わってしまい狙った状態を作れない
        # （`intrinsics is not None else _FakeIntrinsics()` を参照）。
        # ここでは構築後に `pipeline.intrinsics` を直接 `None` へ差し替え、
        # `get_stream().as_video_stream_profile().get_intrinsics()` が
        # 例外を送出する状況を作る。`start()` はこの失敗を吸収して
        # `intrinsics=None`（欠測。要件 3.5）へ変換するはずである。
        capture_config = _default_capture_config()
        metrics, clock = _make_metrics_and_clock()
        pipeline = _FakePipeline()
        pipeline.intrinsics = None
        fake_rs = _make_fake_rs_module(pipeline)
        monkeypatch.setitem(sys.modules, "pyrealsense2", fake_rs)

        source = RealSenseSource(capture_config, metrics, clock=clock)
        source.start()

        assert source.profile.intrinsics is None

    def test_depth_scale_fallback_when_sensor_query_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        device = _FakeDevice(sensor=_FakeSensor(depth_scale_raises=True))
        source, _ = _build_source(monkeypatch, device=device)
        source.start()

        assert source.profile.depth_scale_mm == pytest.approx(1.0)


# ============================================================================
# Depth バッファの読み取り専用化（Point Cloud は生成しない。要件 2.6）
# ============================================================================


class TestDepthBufferCopiedOnceAndReadOnly:
    def test_raw_frame_depth_is_read_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        source, _ = _build_source(monkeypatch, wait_frames=[_make_frameset(0)])
        source.start()

        raw = source._acquire(1000)  # noqa: SLF001

        assert raw is not None
        assert raw.depth.flags.writeable is False

    def test_capture_frame_depth_is_also_read_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # `CaptureFrame.__post_init__`（types.py, タスク 1.5）が
        # `depth.flags.writeable = False` を独自に立てるため、RawFrame の
        # 時点での読み取り専用化と二重になる。ここではその二重防御込みで
        # 最終的な CaptureFrame も読み取り専用であることを確認する。
        source, _ = _build_source(monkeypatch, wait_frames=[_make_frameset(0)])
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
        pipeline = _FakePipeline()
        fake_rs = _make_fake_rs_module(pipeline)
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
        device_a = _FakeDevice(serial="SN-AAA", firmware="5.1.0")
        device_b = _FakeDevice(serial="SN-BBB", firmware="5.2.0", usb_type="2.0")
        pipeline = _FakePipeline(device=device_a)
        fake_rs = _make_fake_rs_module(pipeline, context_devices=[device_a, device_b])
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
        device = _FakeDevice(usb_type=None)
        pipeline = _FakePipeline(device=device)
        fake_rs = _make_fake_rs_module(pipeline, context_devices=[device])
        monkeypatch.setitem(sys.modules, "pyrealsense2", fake_rs)

        result = probe_devices()

        assert result["devices"][0]["usb_type_descriptor"] is None  # type: ignore[index]
