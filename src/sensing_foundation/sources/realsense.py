"""RealSense の live アダプタを実装する（design.md「L6-L7: アダプタ・保存・診断 / RealSenseSource（live）」）。

`RealSenseSource` は D435 から Depth（任意で Color）を取得し、
`BaseFrameSource`（タスク 3.1）が定める `_acquire()` / `_drain_latest()` の
2操作を実装する **`pyrealsense2` を import する唯一のモジュール** である
（design.md 依存方向表の注記、tasks.md 6.1「SDK への問い合わせ関数を本モジュール
が公開し、`pyrealsense2` を import するモジュールを本モジュール1つに限定する」）。

**`pyrealsense2` は関数内でのみ遅延 import する**（本モジュール docstring・
`_import_pyrealsense2()` 参照）。モジュールの先頭レベルではこの SDK を一切
参照しないため、SDK 未導入の環境でも `import sensing_foundation.sources.realsense`
自体は成功する（`open_source()` の LIVE 分岐がこのモジュールを遅延 import する
ことと合わせ、SDK 非導入環境で他の入力元が動作し続けることを保証する。
tasks.md 6.1「観測可能な完了状態」）。

============================================================================
前提とする pyrealsense2 API 形状（検証不能な前提。実 SDK が本環境に無いため
公式ドキュメント・`research.md`（Research 3/4）が言及する範囲の呼び出し規約を
根拠に、以下の形状を**明示的な前提**として選ぶ。実機導通（タスク 9.3）で
この前提が崩れていた場合はこのモジュールを調整すること）
============================================================================

    rs.config()
        .enable_device(serial: str) -> None
        .enable_stream(stream, width, height, format, fps) -> None
    rs.stream.depth / rs.stream.color
    rs.format.z16 / rs.format.bgr8
    rs.pipeline()
        .start(config) -> pipeline_profile      # モード拒否時は例外を送出する
        .wait_for_frames(timeout_ms) -> frameset  # タイムアウト時は例外を送出する
        .poll_for_frames() -> frameset|None       # 真偽値評価可能であれば形は問わない
        .stop() -> None
    frameset（真偽値評価可能。`bool(frameset)` で有効フレームの有無を判定できる）
        .get_depth_frame() -> depth_frame|None
    depth_frame
        .get_frame_number() -> int
        .get_timestamp() -> float                 # ms
        .get_frame_timestamp_domain() -> object    # `.name` 属性を持つ列挙値
        .get_data() -> buffer（`numpy.frombuffer` に渡せるもの）
    pipeline_profile
        .get_device() -> device
        .get_stream(rs.stream.depth) -> stream_profile
    stream_profile.as_video_stream_profile().get_intrinsics() -> intrinsics
        （`.width` / `.height` / `.fx` / `.fy` / `.ppx` / `.ppy` / `.model` /
        `.coeffs` を持つ）
    device
        .get_info(rs.camera_info.usb_type_descriptor|serial_number|
                   firmware_version|name|product_line) -> str
                                                  # 未対応なら例外を送出する
                                                  # （`rs.camera_info` に列挙値
                                                  #   自体が無いビルドもある）
        .first_depth_sensor() -> sensor
    sensor
        .get_depth_scale() -> float（メートル/カウント。mm 換算は本モジュールが行う）
        .set_option(rs.option.frames_queue_size|global_time_enabled, value) -> None
        （未対応オプションは例外を送出する）
    rs.context().query_devices() -> Iterable[device]
    rs.__version__ / rs.__file__（無ければ `None` 扱いにする）

本モジュールのコードは上記のうち「例外を送出するか、真偽値評価できるか」
にしか依存しないよう、可能な限り広く `try/except Exception` で防御する
（`research.md` Research 1「SDK のビルド構成によって取得できるメタデータが
変わる」・design.md Risks「取れないものは欠測として残す」への対応。要件 3.5）。

============================================================================
実装判断
============================================================================

**構築時に `profile` を暫定値で埋め、`start()` で実機値へ差し替える**:
`BaseFrameSource.__init__` は `profile: StreamProfile` を必須の構築時引数と
して要求するが、実カメラの内部パラメータ（`intrinsics`）・Depth スケールは
デバイスをオープンするまで分からない。`SimulatedSource`/`RecordedSource`
（それぞれタスク 3.2/4.5）は構築時点で `profile` を確定できる（供給関数の
`profile` 引数、または `SessionReader.profile`）が、live はその前提が成立
しない。本実装は `__init__` で `intrinsics=None` の暫定 `StreamProfile` を
`super().__init__()` へ渡し、`start()` で実機から得た値に基づいて
`self._profile` を**再代入**する（`FrameSource.profile` プロパティは
`BaseFrameSource` の実装をそのまま使うため、格納先の属性名を変える必要は
ない）。これは「`BaseFrameSource` の私有属性名と衝突させない」という
タスク 4.5 の教訓（下記）とは別物である —— `_profile` は `BaseFrameSource`
**自身が所有する**属性であり、本クラスが新しい名前でそれを覆い隠すのではなく、
既存の格納先へ正しい値を書き戻しているだけである。

**`BaseFrameSource` の私有属性名との衝突を避ける（タスク 4.5 の教訓）**:
`BaseFrameSource` は `_kind` / `_profile` / `_metrics` / `_clock` /
`_drain_enabled` / `_acquire_timeout_ms` / `_on_acquire_error` /
`_next_index` / `_last_seq` / `_final_stats` を私有属性として持つ
（`source.py` Implementation Notes「タスク4.5」参照。`RecordedSource` は
これと衝突する独自カーソル名を選んで1フレームおきに読み飛ばすバグを実際に
踏んだ）。本クラスが新設する属性（`_capture_config` / `_device_serial` /
`_rs` / `_pipeline` / `_pipeline_profile` / `_usb2_warning` /
`_global_time_enabled`）はいずれもこの集合と衝突しない名前を選んでいる。
なお live アダプタは recorded と異なり「読み出し位置カーソル」に相当する
状態を持たない（`wait_for_frames()` の呼び出しごとにデバイスから新しい
フレームを取得するだけであり、`_next_index` のような自前のカーソルは
そもそも不要）。

**ドレインは基底クラスの `drain_enabled` 判断に委ねる（他の2アダプタとの
違い）**: `SimulatedSource`/`RecordedSource` は `capture_config` の
`drain_enabled` を呼び出し側の指定に関わらず常に `False` へ固定し、
`_drain_latest()` 自体も常に `(None, 0)` を返す「二重防御」を行う
（要件 4.5/6.7「再生・合成では取りこぼしを新たに作らない」）。**live は
逆である** —— ドレインこそが要件 2.1（未処理のフレームを蓄積せず最新を
優先する）を満たす主要な仕組みであり、`CaptureConfig.drain_enabled` を
そのまま尊重する。本クラスは `capture_config` を一切 `replace()` せず
`super().__init__()` へそのまま渡し、`_drain_latest()` は実際に
`pipeline.poll_for_frames()` を空になるまで呼んで最新へ追いつく
（design.md Implementation Notes「取得は `wait_for_frames(timeout)` で
1枚 → `poll_for_frames()` を空になるまで回して最新へ追いつく」
（`research.md` Research 4）を参照）。

**キューを空と判定する条件**: `poll_for_frames()` の戻り値を `if not
polled:` で判定する。実 SDK が `None` を返すか、無効だが真偽値評価可能な
frameset オブジェクトを返すかを検証できないため、どちらの流儀でも動く
よう真偽値評価のみに依存する（本モジュール docstring「API 形状」参照）。

**取得失敗の扱い**: `wait_for_frames()` が例外を送出した場合（タイムアウト
等）は `_acquire()` が `None` を返す（`BaseFrameSource` が
`on_acquire_error` に従って継続/停止を判断する。「観測された事実」区分、
`errors.py` 参照）。`_acquire()` 自身が `StopIteration` を送出することは
ない —— live 入力に「正常な終端」という概念は無く、常に継続を試みる
（`SimulatedSource`/`RecordedSource` との違い）。

**取得レイテンシの算出**: `timestamp_domain == GLOBAL_TIME` のときのみ、
`SessionClock.to_wall_ms(now_ms())`（現在のホスト壁時計 epoch ms）と
`device_timestamp_ms`（GLOBAL_TIME ドメインではホスト壁時計と同じ epoch
基準に補正された値、という `RS2_OPTION_GLOBAL_TIME_ENABLED` の定義に基づく
前提。`research.md` Research 3 参照）の差分として `capture_latency_ms` を
算出する。それ以外のドメインでは `None`（欠測として残す。要件 3.5）。

**Depth バッファの複製**: `depth_frame.get_data()` を `numpy.frombuffer` で
読み、**1回だけ** `.copy()` してから読み取り専用化する（Point Cloud は
生成しない。要件 2.6）。`CaptureFrame.__post_init__`（`types.py`）も
`depth.flags.writeable = False` を独自に立てるため、本モジュールでの
読み取り専用化はそれと**二重**になるが、`RawFrame` の時点（`CaptureFrame`
構築前）でも安全な読み取り専用状態を保つため、意図的に冗長性を残す
（design.md Implementation Notes「必要な1回だけコピーして読み取り専用に
する」を `RawFrame` の時点で先取りして満たす）。

**USB2 警告・グローバル時刻・キュー容量はいずれも「取れなければ欠測」**:
`usb2_warning` は `bool | None`（`None` は判定不能。**`False` を既定として
偽装しない**——要件 3.5「取れないものは欠測として残す」）。`global_time_enabled`
は `bool`（試行して成功したか失敗したかは常に判定できる操作であるため
`None` にはしない）。フレームキュー容量の適用（`rs.option.frames_queue_size`）
はベストエフォートとし、失敗しても起動を失敗させない（キュー容量の反映は
ドレイン戦略の補助であり、要件 1.4/1.5/2.6 のような起動時必須検証の対象
ではない）。

**モード拒否は起動時に失敗させる**: `pipeline.start(config)` が例外を
送出した場合、`DeviceNotReadyError`（`SourceUnavailableError` のサブクラス。
`errors.py` タスク 1.2）へ変換して送出する。**黙って別のモードにフォール
バックしない**（design.md Validation、要件 1.4 に相当）。実 SDK が送出する
例外の正確な型を検証できないため、`Exception` を広く捕捉する（本モジュール
docstring「API 形状」の防御方針と同じ理由）。

**`probe_sdk()` / `probe_devices()`**: `Doctor`（タスク 6.2）が
`pyrealsense2` を直接 import せずに SDK 版・実体の場所・デバイス列挙・USB
接続種別を確認できるよう、本モジュールが公開する（tasks.md 6.1
「観測可能な完了状態」・design.md「Dependencies: Inbound: Doctor（P0）—
`probe_sdk()` / `probe_devices()` を利用する」）。**SDK 未導入でも例外を
送出せず**、戻り値の `available` フラグで報告する —— `Doctor` の「各項目は
独立して失敗できる」（design.md Doctor Validation）という方針を、本関数の
側でも「例外で呼び出し元を落とさない」という形で先取りする。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from sensing_foundation.config import CaptureConfig
from sensing_foundation.errors import DeviceNotReadyError, SourceUnavailableError
from sensing_foundation.metrics import CaptureMetrics
from sensing_foundation.source import BaseFrameSource, RawFrame
from sensing_foundation.timebase import SessionClock
from sensing_foundation.types import (
    CameraIntrinsics,
    SourceKind,
    StreamProfile,
    TimestampDomain,
)

__all__ = ["DeviceIdentity", "RealSenseSource", "probe_devices", "probe_sdk"]

#: `depth_frame.get_frame_timestamp_domain()` が返す列挙値の `.name`（小文字化
#: 済み）から `TimestampDomain` への対応表。本モジュール docstring「API 形状」
#: の前提に基づく。未知の名前は `TimestampDomain.UNKNOWN` にフォールバックする。
_DOMAIN_NAME_MAP: dict[str, TimestampDomain] = {
    "hardware_clock": TimestampDomain.HARDWARE_CLOCK,
    "system_time": TimestampDomain.SYSTEM_TIME,
    "global_time": TimestampDomain.GLOBAL_TIME,
}

#: 内部パラメータが得られない場合のプレースホルダ歪み係数（5要素、全て0）。
_ZERO_COEFFS: tuple[float, float, float, float, float] = (0.0, 0.0, 0.0, 0.0, 0.0)

#: `sensor.get_depth_scale()` が失敗した場合のフォールバック値（mm/カウント）。
#: 実デバイスでは `pipeline.start()` が成功した時点でほぼ確実に取得できる
#: 基本的なハードウェア特性であり、この分岐はテスト用の壊れたモック等に
#: 対する防御に過ぎない（本モジュール docstring 参照）。
_FALLBACK_DEPTH_SCALE_MM = 1.0


@dataclass(frozen=True, slots=True)
class DeviceIdentity:
    """`pipeline.start()` が**実際に開いた**1個体の識別情報（タスク 6.3）。

    「どの個体・どのファームウェアで撮った記録か」を後から追えるようにする
    ための値であり、記録側（タスク 8.3）が `manifest.json` の `device` を
    埋めるときの入手経路になる（要件 5.2）。

    フィールド名は `rs.camera_info` の列挙値名をそのまま使う。`manifest.json`
    の `device` は別語彙（`serial` / `firmware` / `usb_type` / `product_line`。
    design.md「Data Models / `manifest.json`」）であり、**その対応付けは
    記録側の責務**である——本モジュールは SDK から見えた事実だけを持つ。

    Attributes:
        name: 製品名（例 `"Intel RealSense D435"`）。
        serial_number: 個体のシリアル番号。同じ機種を複数台つないだときに
            個体を区別できる唯一の値である。
        firmware_version: ファームウェアの版。
        usb_type_descriptor: USB 接続種別の記述子（例 `"3.2"` / `"2.1"`）。
            `usb2_warning` はこの値から導かれる（要件 1.4 / 1.5）。
        product_line: 製品系列（例 `"D400"`）。

    Invariants:
        取得できなかった項目は `None`（欠測）であり、既定値や空文字で
        埋めない（要件 3.5「取れないものは欠測として残す」）。全項目が
        `None` になることもある（メタデータを一切公開しない SDK ビルド）。
    """

    name: str | None
    serial_number: str | None
    firmware_version: str | None
    usb_type_descriptor: str | None
    product_line: str | None


def _import_pyrealsense2() -> Any:
    """`pyrealsense2` を関数内で遅延 import する（本モジュールの唯一の SDK 参照点）。

    Raises:
        SourceUnavailableError: SDK が import できない。「次に何を確認すべきか」
            （`doctor` の `sdk_import`）をメッセージに含める（`errors.py`
            タスク 1.2 の契約）。
    """
    try:
        import pyrealsense2 as rs
    except ModuleNotFoundError as exc:
        raise SourceUnavailableError(
            "pyrealsense2 を import できない。`doctor` の sdk_import を確認せよ"
            "（SDK 未導入・ビルド未完了・Python 版の取り違え・`.so` の配置ミスの"
            "いずれかが疑われる。`docs/development-environment.md §16` のブリング"
            "アップ手順に従い、認識・USB3・給電・SDK 導入の順で切り分けること）。"
        ) from exc
    return rs


def _device_info(rs: Any, device: Any, attr_name: str) -> str | None:
    """`device.get_info(rs.camera_info.<attr_name>)` を欠測許容で取得する。

    SDK ビルド構成によって取得できるメタデータが変わる（design.md Risks）
    ため、`rs.camera_info` に該当属性が無い場合・`get_info()` が例外を送出
    する場合のいずれも `None`（欠測）として返し、値を偽装しない。
    """
    info_enum = getattr(rs.camera_info, attr_name, None)
    if info_enum is None:
        return None
    try:
        return device.get_info(info_enum)
    except Exception:
        return None


def _usb2_from_descriptor(usb_type_descriptor: str | None) -> bool | None:
    """USB 接続種別の記述子から USB2 接続かどうかを判定する（要件 1.4, 1.5）。

    Args:
        usb_type_descriptor: `rs.camera_info.usb_type_descriptor` の値
            （例 `"3.2"` / `"2.1"`）。取得できなかった場合は `None`。

    Returns:
        `True` なら USB2 接続、`False` なら USB2 ではない（USB3 など）、
        `None` なら判定不能（欠測。要件 3.5——偽の `False` を返さない）。
    """
    if usb_type_descriptor is None:
        return None
    return str(usb_type_descriptor).startswith("2.")


def _read_device_identity(rs: Any, device: Any) -> DeviceIdentity:
    """1個体の識別情報を欠測許容で読み出す（`_device_info()` の方針をそのまま適用）。

    `probe_devices()` は**接続中の全デバイスを列挙する**ための別経路であり、
    `Doctor`（タスク 6.2）との間で `dict` の形が契約になっているため統合
    しない。本関数は `RealSenseSource.start()` が `pipeline_profile.
    get_device()` から得た**実際に開いた1個体**にだけ使う（tasks.md 6.3
    「`probe_devices()` の流用で代替しない」）。
    """
    return DeviceIdentity(
        name=_device_info(rs, device, "name"),
        serial_number=_device_info(rs, device, "serial_number"),
        firmware_version=_device_info(rs, device, "firmware_version"),
        usb_type_descriptor=_device_info(rs, device, "usb_type_descriptor"),
        product_line=_device_info(rs, device, "product_line"),
    )


def _map_domain(rs_domain: Any) -> TimestampDomain:
    """`depth_frame.get_frame_timestamp_domain()` の戻り値を `TimestampDomain` へ写像する。"""
    name = getattr(rs_domain, "name", None)
    if name is None:
        name = str(rs_domain).rsplit(".", 1)[-1]
    return _DOMAIN_NAME_MAP.get(str(name).lower(), TimestampDomain.UNKNOWN)


def probe_sdk() -> dict[str, object]:
    """`pyrealsense2` の import 可否・版・実体の場所を報告する（`Doctor` の `sdk_import` 項目が経由する）。

    SDK 未導入でも例外を送出せず、`available=False` として報告する
    （tasks.md 6.1「観測可能な完了状態」・design.md Doctor Validation「各項目は
    独立して失敗できる」への対応）。

    Returns:
        `{"available": bool, "version": str | None, "location": str | None,
        "error": str | None}`。`available=False` のとき `error` に
        `SourceUnavailableError` のメッセージ（案内文つき）を入れる。
        `version`/`location` は SDK が公開していなければ `None`（欠測）。
    """
    try:
        rs = _import_pyrealsense2()
    except SourceUnavailableError as exc:
        return {
            "available": False,
            "version": None,
            "location": None,
            "error": str(exc),
        }

    return {
        "available": True,
        "version": getattr(rs, "__version__", None),
        "location": getattr(rs, "__file__", None),
        "error": None,
    }


def probe_devices() -> dict[str, object]:
    """接続された RealSense デバイスを列挙する（`Doctor` の `device`/`usb` 項目が経由する）。

    SDK 未導入でも例外を送出せず、`available=False` として報告する
    （`probe_sdk()` と同じ方針）。SDK は import できても列挙自体が失敗した
    場合（`rs.context()` の構築失敗等）は `available=True, devices=(),
    error=...` として報告し、`Doctor` の他項目の実行を妨げない。

    Returns:
        `{"available": bool, "devices": tuple[dict, ...], "error": str | None}`。
        各デバイスの dict は `name` / `serial_number` / `firmware_version` /
        `usb_type_descriptor` を持ち、取得できない項目は `None`（欠測）。
    """
    try:
        rs = _import_pyrealsense2()
    except SourceUnavailableError as exc:
        return {"available": False, "devices": (), "error": str(exc)}

    try:
        ctx = rs.context()
        devices = tuple(
            {
                "name": _device_info(rs, dev, "name"),
                "serial_number": _device_info(rs, dev, "serial_number"),
                "firmware_version": _device_info(rs, dev, "firmware_version"),
                "usb_type_descriptor": _device_info(rs, dev, "usb_type_descriptor"),
            }
            for dev in ctx.query_devices()
        )
        return {"available": True, "devices": devices, "error": None}
    except Exception as exc:
        return {"available": True, "devices": (), "error": str(exc)}


def _build_provisional_profile(capture_config: CaptureConfig) -> StreamProfile:
    """`start()` 前（構築直後）に使う暫定 `StreamProfile`。

    `intrinsics=None`・`depth_scale_mm=1.0` の暫定値で構築し、`start()` が
    実機から得た値で `RealSenseSource._profile` を差し替える（モジュール
    docstring「実装判断」参照）。
    """
    return StreamProfile(
        width_px=capture_config.width_px,
        height_px=capture_config.height_px,
        fps=capture_config.fps,
        depth_scale_mm=_FALLBACK_DEPTH_SCALE_MM,
        color_enabled=capture_config.color_enabled,
        intrinsics=None,
    )


class RealSenseSource(BaseFrameSource):
    """D435 から Depth（任意で Color）を取得する `FrameSource` アダプタ（live）。

    `pyrealsense2` を import する唯一のモジュールであり、常に関数内で遅延
    import する（モジュール docstring参照）。

    Preconditions:
        `start()` は `frames()` の前に1度だけ呼ばれる（`BaseFrameSource` と
        同じ契約）。`start()` が `pyrealsense2` の import・デバイスのオープン
        を行う。
    Postconditions:
        `start()` が成功した後、`profile.intrinsics` は実機から取得できた
        場合は非 `None`、取得できなかった場合は `None`（欠測。偽装しない）。
        `global_time_enabled` / `usb2_warning` / `device_identity` は
        `start()` 完了後に確定する。
    Invariants:
        `drain_enabled` は `capture_config` の指定をそのまま尊重する
        （`SimulatedSource`/`RecordedSource` と異なり固定しない。モジュール
        docstring「ドレインは基底クラスの判断に委ねる」参照）。
    """

    def __init__(
        self,
        capture_config: CaptureConfig,
        metrics: CaptureMetrics,
        *,
        clock: SessionClock,
        device_serial: str | None = None,
    ) -> None:
        """`RealSenseSource` を組み立てる（デバイスはまだ開かない）。

        `open_source()`（`source.py`、タスク 4.6）は
        `RealSenseSource(settings.capture, metrics, clock=clock)` という
        2位置引数 + キーワード専用 `clock` の形で呼び出す。本コンストラクタ
        はこの呼び出し形を満たす（`device_serial` は `open_source()` が
        現時点では渡さない追加のキーワード専用引数であり、未指定なら
        SDK が既定で選ぶ最初のデバイスを使う）。

        Args:
            capture_config: 解像度・fps・Color 有無・キュー容量・ドレイン
                可否・取得タイムアウト・取得失敗時の挙動の取得元。
            metrics: 計測点の送出先。
            clock: `metrics` と**同一インスタンス**の `SessionClock`
                （`BaseFrameSource.__init__` の要求。他の2アダプタと同じ
                実装判断）。
            device_serial: 接続する D435 のシリアル番号。`None`（既定）
                なら SDK が既定で選ぶ最初のデバイスを使う（複数台接続時の
                選択はこの引数の責務。現時点で `open_source()` はこれを
                渡さない）。
        """
        provisional_profile = _build_provisional_profile(capture_config)
        super().__init__(
            kind=SourceKind.LIVE,
            profile=provisional_profile,
            metrics=metrics,
            clock=clock,
            capture_config=capture_config,
        )
        self._capture_config = capture_config
        self._device_serial = device_serial
        self._rs: Any = None
        self._pipeline: Any = None
        self._pipeline_profile: Any = None
        self._usb2_warning: bool | None = None
        self._global_time_enabled: bool = False
        self._device_identity: DeviceIdentity | None = None

    # ------------------------------------------------------------------
    # 追加プロパティ（design.md「有効化できたかどうかをメタ情報とログに
    # 残す」「USB2 の場合は警告として明示する」、および tasks.md 6.3
    # 「開いたデバイスの識別情報を取り出せるようにする」への対応。
    # ログ・manifest への実際の配線は本モジュールの責務ではない——
    # 呼び出し側（`cli.py`、タスク 8.3）がこれらを読んで記録する）
    # ------------------------------------------------------------------

    @property
    def global_time_enabled(self) -> bool:
        """`RS2_OPTION_GLOBAL_TIME_ENABLED` の有効化に成功したか。

        `start()` 完了前は `False`（未試行）。
        """
        return self._global_time_enabled

    @property
    def device_identity(self) -> DeviceIdentity | None:
        """`start()` が**実際に開いた個体**の識別情報（タスク 6.3。要件 1.4, 5.2）。

        `start()` 完了前は `None`（まだどのデバイスも開いていない）。
        `start()` 完了後は非 `None` だが、個々の項目は欠測し得る
        （`DeviceIdentity` の Invariants 参照）。**`stop()` 後も保持する**
        ——記録のメタ情報は取得が終わってから書き出されることがあり、
        そこで「どの個体で撮ったか」が消えていては用を成さない。

        **接続中のデバイス一覧が欲しい場合は `probe_devices()` を使うこと。**
        本プロパティは逆に「複数台つながっているとき、このパイプラインが
        開いたのはどれか」だけを答える——記録のメタ情報に残すべきなのは
        こちらである（要件 5.2）。
        """
        return self._device_identity

    @property
    def usb2_warning(self) -> bool | None:
        """USB2 接続が検出されたか（要件 1.4, 1.5）。

        `True`: USB2 接続を検出した（fps 計測結果を有効なものとして扱わない
        べきという警告。実際に無効化する判断は `ModeSweep`（タスク 7.2）の
        責務）。
        `False`: USB2 ではないと判定できた（USB3 など）。
        `None`: `usb_type_descriptor` を取得できず判定不能（欠測。要件 3.5
        「取れないものは欠測として残す」——偽の `False` を返さない）。

        値は `device_identity.usb_type_descriptor` から導かれる（同じ1回の
        観測を出典にするため、記録に残る接続種別と本フラグが食い違わない）。
        """
        return self._usb2_warning

    # ------------------------------------------------------------------
    # FrameSource 契約（ライフサイクル）
    # ------------------------------------------------------------------

    def start(self) -> None:
        """デバイスを開き、要求したモードでストリームを開始する。

        Raises:
            SourceUnavailableError: `pyrealsense2` を import できない
                （`_import_pyrealsense2()` 参照）。
            DeviceNotReadyError: 要求した解像度・fps（および Color 有無）を
                SDK が拒否した。黙って別のモードへフォールバックしない
                （design.md Validation、要件 1.4 相当）。
        """
        super().start()
        rs = _import_pyrealsense2()
        self._rs = rs

        config = rs.config()
        if self._device_serial is not None:
            config.enable_device(self._device_serial)
        config.enable_stream(
            rs.stream.depth,
            self._capture_config.width_px,
            self._capture_config.height_px,
            rs.format.z16,
            self._capture_config.fps,
        )
        if self._capture_config.color_enabled:
            config.enable_stream(
                rs.stream.color,
                self._capture_config.width_px,
                self._capture_config.height_px,
                rs.format.bgr8,
                self._capture_config.fps,
            )

        pipeline = rs.pipeline()
        try:
            pipeline_profile = pipeline.start(config)
        except Exception as exc:
            raise DeviceNotReadyError(
                "要求したモード（"
                f"{self._capture_config.width_px}x{self._capture_config.height_px}"
                f"@{self._capture_config.fps}fps, color_enabled="
                f"{self._capture_config.color_enabled}）でストリームを開けなかった。"
                "`doctor` の stream_open を確認せよ。黙って別のモードで動作させる"
                "ことはしない（design.md Validation、要件 1.4）。"
            ) from exc

        self._pipeline = pipeline
        self._pipeline_profile = pipeline_profile

        device = pipeline_profile.get_device()
        self._apply_queue_capacity(rs, device)
        self._global_time_enabled = self._try_enable_global_time(rs, device)
        # 識別情報はここで1度だけ読み、USB2 警告もその値から導く
        # （記録に残る接続種別と警告フラグが食い違わないようにするため）。
        identity = _read_device_identity(rs, device)
        self._device_identity = identity
        self._usb2_warning = _usb2_from_descriptor(identity.usb_type_descriptor)
        self._profile = self._resolve_profile(rs, pipeline_profile, device)

    def stop(self) -> None:
        """取得を停止し、パイプラインを閉じる。二重呼び出しは安全。"""
        super().stop()
        if self._pipeline is not None:
            try:
                self._pipeline.stop()
            except Exception:
                # 「観測された事実」区分の外（パイプライン破棄処理の失敗）
                # だが、停止処理自体で例外を上げて呼び出し側の後始末を
                # 阻害しないことを優先する。
                pass
            self._pipeline = None

    # ------------------------------------------------------------------
    # start() が使う補助（キュー容量・グローバル時刻・内部パラメータ）
    # ------------------------------------------------------------------

    def _apply_queue_capacity(self, rs: Any, device: Any) -> None:
        """`CaptureConfig.queue_capacity` をセンサへベストエフォートで適用する。

        失敗しても起動を失敗させない（キュー容量の反映はドレイン戦略の
        補助であり、モジュール docstring「キュー容量の適用はベストエフォート」
        参照）。
        """
        try:
            sensor = device.first_depth_sensor()
            sensor.set_option(rs.option.frames_queue_size, self._capture_config.queue_capacity)
        except Exception:
            pass

    def _try_enable_global_time(self, rs: Any, device: Any) -> bool:
        """`RS2_OPTION_GLOBAL_TIME_ENABLED` の有効化を試み、成功したかを返す。

        有効化できたかどうかを呼び出し側が判定できるようにする（design.md
        「有効化できたかどうかをメタ情報とログに残す」。実際のログ・manifest
        配線は本タスクの範囲外——本プロパティを読んだ呼び出し側が行う）。
        """
        try:
            sensor = device.first_depth_sensor()
            sensor.set_option(rs.option.global_time_enabled, 1.0)
            return True
        except Exception:
            return False

    def _resolve_profile(self, rs: Any, pipeline_profile: Any, device: Any) -> StreamProfile:
        """実機から得た内部パラメータ・Depth スケールで `StreamProfile` を再構築する。"""
        intrinsics = self._resolve_intrinsics(rs, pipeline_profile)
        depth_scale_mm = self._resolve_depth_scale_mm(device)
        return StreamProfile(
            width_px=self._capture_config.width_px,
            height_px=self._capture_config.height_px,
            fps=self._capture_config.fps,
            depth_scale_mm=depth_scale_mm,
            color_enabled=self._capture_config.color_enabled,
            intrinsics=intrinsics,
        )

    def _resolve_intrinsics(self, rs: Any, pipeline_profile: Any) -> CameraIntrinsics | None:
        """Depth ストリームのカメラ内部パラメータを取得する。取得不能なら `None`（欠測。要件 3.5, 3.6）。"""
        try:
            depth_stream = pipeline_profile.get_stream(rs.stream.depth)
            video_stream = depth_stream.as_video_stream_profile()
            rs_intrinsics = video_stream.get_intrinsics()
            coeffs_raw = getattr(rs_intrinsics, "coeffs", None)
            coeffs = tuple(float(c) for c in coeffs_raw) if coeffs_raw else _ZERO_COEFFS
            model = str(getattr(rs_intrinsics, "model", "unknown"))
            return CameraIntrinsics(
                width_px=int(rs_intrinsics.width),
                height_px=int(rs_intrinsics.height),
                fx_px=float(rs_intrinsics.fx),
                fy_px=float(rs_intrinsics.fy),
                ppx_px=float(rs_intrinsics.ppx),
                ppy_px=float(rs_intrinsics.ppy),
                model=model,
                coeffs=coeffs,  # type: ignore[arg-type]
            )
        except Exception:
            return None

    def _resolve_depth_scale_mm(self, device: Any) -> float:
        """Depth の1カウントあたりの距離（mm）を取得する。

        `sensor.get_depth_scale()` はメートル/カウントを返すため、1000倍
        して mm 換算する。取得できない場合は `_FALLBACK_DEPTH_SCALE_MM`
        （モジュール docstring参照。実デバイスでは通常発生しない防御的
        フォールバック）。
        """
        try:
            sensor = device.first_depth_sensor()
            scale_m = sensor.get_depth_scale()
            return float(scale_m) * 1000.0
        except Exception:
            return _FALLBACK_DEPTH_SCALE_MM

    # ------------------------------------------------------------------
    # アダプタが実装する2つの抽象操作（`BaseFrameSource` の契約）
    # ------------------------------------------------------------------

    def _acquire(self, timeout_ms: int) -> RawFrame | None:
        """`wait_for_frames(timeout_ms)` で1枚待つ（design.md「取得は wait_for_frames で1枚」）。

        取得失敗（タイムアウト等、SDK が例外を送出するケース）は `None` を
        返す（「観測された事実」。`on_acquire_error` の継続/停止判断へ渡す）。
        live に「正常な終端」は無いため `StopIteration` は送出しない。
        """
        try:
            frameset = self._pipeline.wait_for_frames(timeout_ms)
        except Exception:
            return None
        return self._raw_frame_from_frameset(frameset)

    def _drain_latest(self) -> tuple[RawFrame | None, int]:
        """`poll_for_frames()` を空になるまで回し、最新の1枚へ追いつく（design.md「Research 4」）。

        `BaseFrameSource` が `drain_enabled` のときのみ呼ぶ（モジュール
        docstring「ドレインは基底クラスの判断に委ねる」）。他の2アダプタと
        異なり、本メソッドは実際に SDK へ問い合わせる（常に `(None, 0)` を
        返す no-op ではない）。
        """
        latest: RawFrame | None = None
        discarded = 0
        while True:
            try:
                polled = self._pipeline.poll_for_frames()
            except Exception:
                break
            if not polled:
                break
            raw = self._raw_frame_from_frameset(polled)
            if raw is None:
                break
            if latest is not None:
                discarded += 1
            latest = raw
        return latest, discarded

    # ------------------------------------------------------------------
    # frameset -> RawFrame 変換
    # ------------------------------------------------------------------

    def _raw_frame_from_frameset(self, frameset: Any) -> RawFrame | None:
        """`frameset` から Depth フレームを取り出し `RawFrame` へ変換する。

        取得できなければ `None`（取得失敗として扱う）。
        """
        if not frameset:
            return None
        depth_frame = frameset.get_depth_frame()
        if not depth_frame:
            return None

        try:
            seq = depth_frame.get_frame_number()
        except Exception:
            # フレーム番号すら取れない場合は欠落検出の前提が成立しないため
            # 取得失敗として扱う（`RawFrame.seq` は必須フィールドであり
            # `None` を許さない）。
            return None

        try:
            device_timestamp_ms: float | None = float(depth_frame.get_timestamp())
        except Exception:
            device_timestamp_ms = None

        try:
            domain = _map_domain(depth_frame.get_frame_timestamp_domain())
        except Exception:
            domain = TimestampDomain.UNKNOWN

        depth = self._copy_depth_readonly(depth_frame)
        capture_latency_ms = self._compute_capture_latency_ms(domain, device_timestamp_ms)

        return RawFrame(
            seq=seq,
            depth=depth,
            device_timestamp_ms=device_timestamp_ms,
            timestamp_domain=domain,
            capture_latency_ms=capture_latency_ms,
        )

    def _copy_depth_readonly(self, depth_frame: Any) -> np.ndarray:
        """`get_data()` を `numpy.frombuffer` で読み、1回だけ複製して読み取り専用化する。

        Point Cloud は生成しない（要件 2.6）。生バッファをそのまま保持せず、
        独立したコピーを1回だけ作ることで、SDK 側のバッファ再利用による
        書き換えから守る（design.md Implementation Notes参照）。
        """
        width = self._capture_config.width_px
        height = self._capture_config.height_px
        buffer = depth_frame.get_data()
        array = (
            np.frombuffer(buffer, dtype=np.uint16, count=width * height)
            .reshape(height, width)
            .copy()
        )
        array.flags.writeable = False
        return array

    def _compute_capture_latency_ms(
        self, domain: TimestampDomain, device_timestamp_ms: float | None
    ) -> float | None:
        """`GLOBAL_TIME` ドメインのときのみ取得レイテンシ（ms）を算出する。

        それ以外のドメイン、または `device_timestamp_ms` が欠測のときは
        `None`（要件 3.5）。
        """
        if domain != TimestampDomain.GLOBAL_TIME or device_timestamp_ms is None:
            return None
        host_wall_ms = self._clock.to_wall_ms(self._clock.now_ms())
        return host_wall_ms - device_timestamp_ms
