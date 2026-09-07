"""フェイク `pyrealsense2`（テスト用共有ヘルパ。`synthetic.py` と同じ位置づけ）。

`src/sensing_foundation/sources/realsense.py` のモジュール docstring
「前提とする pyrealsense2 API 形状」に合わせた最小実装であり、実 SDK を
本環境で検証できない以上、**この形状の前提を固定する一次資料**を兼ねる
（タスク 9.2 の実機突き合わせで前提が崩れていた場合はここを直す）。

テストファイルではなく共有ヘルパとして置くのは、同じ SDK 形状の実装が
2つあると片方だけ更新されて食い違うためである。利用側:

- `test_realsense_source.py`（タスク 6.1 / 6.3）— アダプタ単体の検証
- `test_sensing_cli.py`（タスク 8.3）— `record` の live 経路を端から端まで通す

使い方::

    pipeline = FakePipeline(wait_frames=[make_frameset(0), make_frameset(1)])
    monkeypatch.setitem(sys.modules, "pyrealsense2", make_fake_rs_module(pipeline))

各フェイクは「例外を送出するか、真偽値評価できるか」だけを本物に似せる。
`FakeDevice` の各項目へ `None` を渡すと「この SDK ビルドでは取得できない」を
意味し、`get_info()` が例外を送出する。
"""

from __future__ import annotations

import types
from typing import Any

from synthetic import make_depth_frame

#: `make_frameset()` が既定で作る Depth の大きさ（テストが小さく速く回る値）。
DEFAULT_WIDTH_PX = 8
DEFAULT_HEIGHT_PX = 6


class Enum:
    """`.name` 属性を持つだけの、pyrealsense2 の pybind11 列挙値を模したもの。"""

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:  # pragma: no cover - デバッグ用
        return f"<enum {self.name}>"


class FakeConfig:
    def __init__(self) -> None:
        self.enabled_streams: list[tuple[Any, int, int, Any, int]] = []
        self.device_serial: str | None = None

    def enable_device(self, serial: str) -> None:
        self.device_serial = serial

    def enable_stream(self, stream: Any, width: int, height: int, fmt: Any, fps: int) -> None:
        self.enabled_streams.append((stream, width, height, fmt, fps))


class FakeSensor:
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


class FakeDevice:
    """`device.get_info()` を模したもの。

    各項目に `None` を渡すと「この SDK ビルドでは取得できない」を意味し、
    `get_info()` が例外を送出する（実 SDK の振る舞い。`realsense.py` モジュール
    docstring「API 形状」の前提）。既定値は D435 実機で観測できる形に近い。
    """

    def __init__(
        self,
        *,
        usb_type: str | None = "3.2",
        serial: str | None = "SN-0001",
        firmware: str | None = "5.13.0.50",
        name: str | None = "Intel RealSense D435",
        product_line: str | None = "D400",
        sensor: "FakeSensor | None" = None,
    ) -> None:
        candidates = {
            "serial_number": serial,
            "firmware_version": firmware,
            "name": name,
            "usb_type_descriptor": usb_type,
            "product_line": product_line,
        }
        self._info = {key: value for key, value in candidates.items() if value is not None}
        self.sensor = sensor if sensor is not None else FakeSensor()

    def get_info(self, info_enum: Any) -> str:
        key = info_enum.name
        if key not in self._info:
            raise RuntimeError(f"info not supported by this build: {key}")
        return self._info[key]

    def first_depth_sensor(self) -> FakeSensor:
        return self.sensor


class FakeIntrinsics:
    def __init__(
        self,
        *,
        width: int = DEFAULT_WIDTH_PX,
        height: int = DEFAULT_HEIGHT_PX,
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


class FakeVideoStreamProfile:
    def __init__(self, intrinsics: "FakeIntrinsics | None") -> None:
        self._intrinsics = intrinsics

    def get_intrinsics(self) -> FakeIntrinsics:
        if self._intrinsics is None:
            raise RuntimeError("intrinsics not available for this stream")
        return self._intrinsics


class FakeStreamProfile:
    def __init__(self, intrinsics: "FakeIntrinsics | None") -> None:
        self._intrinsics = intrinsics

    def as_video_stream_profile(self) -> FakeVideoStreamProfile:
        return FakeVideoStreamProfile(self._intrinsics)


class FakePipelineProfile:
    def __init__(self, device: FakeDevice, intrinsics: "FakeIntrinsics | None") -> None:
        self._device = device
        self._intrinsics = intrinsics

    def get_device(self) -> FakeDevice:
        return self._device

    def get_stream(self, stream_enum: Any) -> FakeStreamProfile:
        del stream_enum
        return FakeStreamProfile(self._intrinsics)


class FakeDepthFrame:
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

    def get_frame_timestamp_domain(self) -> Enum:
        if self._fail_domain:
            raise RuntimeError("domain unavailable")
        return Enum(self._domain_name)

    def get_data(self) -> bytes:
        return self._depth_array.tobytes()


class FakeFrameset:
    def __init__(self, depth_frame: "FakeDepthFrame | None") -> None:
        self._depth_frame = depth_frame

    def __bool__(self) -> bool:
        return self._depth_frame is not None

    def get_depth_frame(self) -> "FakeDepthFrame | None":
        return self._depth_frame


def make_frameset(
    seq: int,
    *,
    domain_name: str = "hardware_clock",
    device_timestamp_ms: float = 0.0,
    width: int = DEFAULT_WIDTH_PX,
    height: int = DEFAULT_HEIGHT_PX,
    **frame_kwargs: object,
) -> FakeFrameset:
    depth = make_depth_frame(width, height, seq)
    depth_frame = FakeDepthFrame(seq, depth, device_timestamp_ms, domain_name, **frame_kwargs)  # type: ignore[arg-type]
    return FakeFrameset(depth_frame)


class FakePipeline:
    """`rs.pipeline()` を模したもの。

    `endless=True` にすると `wait_for_frames()` が在庫を使い切った後も
    フレームを作り続ける。**「一定時間ぶん取得する」利用側（`record` の
    `--duration-s` など）を端から端まで動かすために要る**——在庫制の
    既定のままだと、取得ループが区間の終わりに達する前にフレームが尽き、
    取得失敗として扱われてしまう。
    """

    def __init__(
        self,
        *,
        reject_start: bool = False,
        wait_frames: list[FakeFrameset] | None = None,
        poll_frames: list[FakeFrameset] | None = None,
        device: FakeDevice | None = None,
        intrinsics: "FakeIntrinsics | None" = None,
        endless: bool = False,
        frame_width: int = DEFAULT_WIDTH_PX,
        frame_height: int = DEFAULT_HEIGHT_PX,
    ) -> None:
        self._reject_start = reject_start
        self._wait_queue = list(wait_frames or [])
        self._poll_queue = list(poll_frames or [])
        self._endless = endless
        self._frame_width = frame_width
        self._frame_height = frame_height
        self._next_endless_seq = len(self._wait_queue)
        self.device = device if device is not None else FakeDevice()
        self.intrinsics = intrinsics if intrinsics is not None else FakeIntrinsics()
        self.started = False
        self.stopped = False
        self.last_config: FakeConfig | None = None

    def start(self, config: FakeConfig) -> FakePipelineProfile:
        self.last_config = config
        if self._reject_start:
            raise RuntimeError("Couldn't resolve requests")
        self.started = True
        return FakePipelineProfile(self.device, self.intrinsics)

    def stop(self) -> None:
        self.stopped = True

    def wait_for_frames(self, timeout_ms: int) -> FakeFrameset:
        del timeout_ms
        if not self._wait_queue:
            if not self._endless:
                raise RuntimeError("Frame didn't arrive within timeout")
            frameset = make_frameset(
                self._next_endless_seq,
                width=self._frame_width,
                height=self._frame_height,
            )
            self._next_endless_seq += 1
            return frameset
        return self._wait_queue.pop(0)

    def poll_for_frames(self) -> "FakeFrameset | None":
        if not self._poll_queue:
            return None
        return self._poll_queue.pop(0)


class FakeContext:
    def __init__(self, devices: list[FakeDevice]) -> None:
        self._devices = devices

    def query_devices(self) -> list[FakeDevice]:
        return list(self._devices)


def make_fake_rs_module(
    pipeline: FakePipeline, *, context_devices: list[FakeDevice] | None = None
) -> types.ModuleType:
    fake = types.ModuleType("pyrealsense2")
    fake.__version__ = "2.99.9-fake"  # type: ignore[attr-defined]
    fake.__file__ = "/fake/path/pyrealsense2.so"  # type: ignore[attr-defined]

    fake.stream = types.SimpleNamespace(depth=Enum("depth"), color=Enum("color"))  # type: ignore[attr-defined]
    fake.format = types.SimpleNamespace(z16=Enum("z16"), bgr8=Enum("bgr8"))  # type: ignore[attr-defined]
    fake.camera_info = types.SimpleNamespace(  # type: ignore[attr-defined]
        usb_type_descriptor=Enum("usb_type_descriptor"),
        serial_number=Enum("serial_number"),
        firmware_version=Enum("firmware_version"),
        name=Enum("name"),
        product_line=Enum("product_line"),
    )
    fake.option = types.SimpleNamespace(  # type: ignore[attr-defined]
        frames_queue_size=Enum("frames_queue_size"),
        global_time_enabled=Enum("global_time_enabled"),
    )

    fake.config = FakeConfig  # type: ignore[attr-defined]
    fake.pipeline = lambda: pipeline  # type: ignore[attr-defined]

    devices = context_devices if context_devices is not None else [pipeline.device]
    fake.context = lambda: FakeContext(devices)  # type: ignore[attr-defined]

    return fake
