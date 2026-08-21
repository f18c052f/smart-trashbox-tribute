"""実行環境の診断を実装する（design.md「L6-L7: アダプタ・保存・診断 / Doctor」）。

`Doctor` はブリングアップの失敗原因を切り分け可能な形で報告する（要件 1.3,
1.4, 1.5, 1.10, 12.7）。`development-environment.md §16` の確認項目に対応する
9項目（`os` / `python` / `memory` / `sdk_import` / `device` / `usb` /
`stream_open` / `power_stability` / `disk`）を **すべて独立して** 実行し、
1項目が失敗しても後続をスキップせず、常に9項目全部の結果を返す
（design.md Doctor「Validation: 各項目は独立して `fail` になれる」）。

**SDK 関連の項目（`sdk_import` / `device` / `usb` / `stream_open` /
`power_stability`）は `sources.realsense` が公開する `probe_sdk()` /
`probe_devices()` / `RealSenseSource` を経由する。本モジュール自身は
`pyrealsense2` を一切 import しない**（依存方向表「7 | doctor | 0〜6。
SDK への問い合わせは `sources/realsense` の probe 関数に委ね、自身は
`pyrealsense2` を import しない」）。境界は `tests/sensing_foundation/
test_doctor.py::TestImportBoundary` が AST 静的解析で固定する。

============================================================================
各項目の設計判断
============================================================================

**`usb`: 判定不能時は `fail` ではなく `skip` にする。** SDK が未導入、
または SDK は使えてもデバイスが1台も列挙されない場合、そもそも
「USB 接続種別」という問いに答える対象が存在しない。この状態を `sdk_import`
/ `device` とは別に `usb` としても `fail` にすると、根本原因（SDK 未導入・
デバイス未接続）が3項目に重複して表れ、切り分けの助けにならない。
design.md の「各項目は独立して `fail` **できる**」は「`fail` になる能力を
持つこと」を求めているのであり「判定不能な場合も必ず `fail` にすること」
までは要求していないと読み、**デバイスが列挙され `usb_type_descriptor` が
取得できたのに判定できない場合（ビルド構成依存の欠測）のみ `fail`**、
**そもそも問う対象が無い場合は `skip`** とした。USB2 を検出した場合は
要件 1.5 の通り `warn`（`fail` ではない — 警告として明示し、fps 計測結果を
無効なものとして扱うのは呼び出し側／`ModeSweep`（タスク 7.2）の責務）。

**`disk`: 空き容量は必須、書き込み速度の目安は簡易な単発計測にとどめる。**
`shutil.disk_usage()` で空き容量・総容量を取得するのは必須要素として実装する。
「書き込み速度の目安」は、既知サイズ（既定 4MiB）の一時ファイルを記録先へ
1回書き込み `os.fsync()` で実ディスクへの書き込みを強制した上で所要時間を
測るだけの**単発計測**にとどめる。複数回の反復・統計的な代表値・fio 相当の
ベンチマークは「クイックな診断ツール」という `Doctor` の性格（要件 1.10
「取得本体とは独立に、手軽に」実行できること）に対して過剰と判断し、意図的に
スコープ外にした。空き容量に対する「十分/不足」の閾値判定も行わない —
実測に基づかない固定バイト数を合否条件として埋め込むことは `tech.md`
開発標準1（未検証の数値を要件のように埋め込まない）に反するため、
測定値をそのまま返すにとどめる。

**`power_stability`: 「問題なし」ではなく「この条件では観測されなかった」
と報告する（design.md Doctor「Risks」）。** この言い回しは、SDK/デバイスが
無く**そもそも試行できなかった**場合（`skip`）・短時間の観測窓の中で実際に
**問題が観測されなかった**場合（`ok`）の**両方**に一貫して適用する。
前者だけに適用して後者を「問題なし」と言い切ってしまうと、後者の短い観測窓
（既定 `_STABILITY_MAX_FRAMES` 枚、`_STABILITY_ACQUIRE_TIMEOUT_MS` ms
タイムアウト）に基づく限定的な観測が、恒久的な安定性の保証であるかのような
過信を招く（design.md の懸念そのもの）。取得中に失敗が生じた場合は `warn`
とし、観測できた範囲の枚数・破棄件数・欠落件数を `value` に残す。
本項目専用の `CaptureConfig` は `on_acquire_error="stop"` へ固定する
（呼び出し側の設定を無視するのではなく、診断が長時間ブロックしないための
本項目固有の安全策——`stream_open` とは別の目的で構築するため、両者は独立
した `RealSenseSource` インスタンスを持つ）。

**`stream_open`: `RealSenseSource.start()` を呼び、`SourceUnavailableError`
（`DeviceNotReadyError` を含む）を `CheckResult` として捕捉する。** 例外を
`Doctor.check()` の外へ伝播させない（「1項目の失敗が後続をスキップしない」
という本タスクの要点そのもの）。想定していない例外（実 SDK の挙動が
`sources/realsense.py` モジュール docstring の前提から外れていた場合など）
も広く捕捉し、`fail` として報告する（`Doctor` 自体をクラッシュさせない
ことを、`RealSenseSource` 実装側の未検証な前提から独立して保証する）。

**`os` / `python` / `memory`: SDK に依存せず、この実行環境で実際に動く。**
`os` は `platform.uname()` と `/etc/os-release`（読めれば）から、`python`
は `sys.version_info` / `sys.executable` から、`memory` は `Sysstat`
（タスク 1.4）から、それぞれ実値を得る。いずれもモック無しでテストできる
（`Sysstat` 同様、`/proc` や `/etc/os-release` が読めない環境では欠測を
値として返し、例外は送出しない）。
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, replace
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Callable

    _CheckFn = Callable[[], "CheckResult"]

from sensing_foundation.config import CaptureConfig, RecordingConfig
from sensing_foundation.errors import SourceUnavailableError
from sensing_foundation.metrics import CaptureMetrics
from sensing_foundation.obslog import NullLogger
from sensing_foundation.sources.realsense import RealSenseSource, probe_devices, probe_sdk
from sensing_foundation.sysstat import Sysstat
from sensing_foundation.timebase import SessionClock

__all__ = ["CheckResult", "Doctor"]

#: `/etc/os-release` の参照先。モジュールレベルの変数にしておくのは
#: `sysstat.py` の `_PROC_ROOT` と同じ理由（テストからの差し替え点）。
_OS_RELEASE_PATH = Path("/etc/os-release")

#: `platform.machine()` が返し得る 64bit アーキテクチャ名（小文字比較）。
_64BIT_MACHINE_NAMES = frozenset(
    {"x86_64", "amd64", "aarch64", "arm64", "ppc64", "ppc64le", "s390x"}
)

#: `power_stability` 項目が試行する最大フレーム数（短時間観測の上限）。
_STABILITY_MAX_FRAMES = 30

#: `power_stability` 項目専用の取得タイムアウト（ms）。診断が長時間
#: ブロックしないよう、呼び出し側の `capture_config.acquire_timeout_ms`
#: に関わらずこの値へ固定する。
_STABILITY_ACQUIRE_TIMEOUT_MS = 500

#: `disk` 項目の書き込み速度計測に使う一時ファイルサイズ（4MiB）。
_DISK_PROBE_SIZE_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class CheckResult:
    """1項目分の診断結果（design.md Doctor の `CheckResult`）。

    Attributes:
        name: 項目名（`os` / `python` / `memory` / `sdk_import` / `device` /
            `usb` / `stream_open` / `power_stability` / `disk` のいずれか）。
        status: `"ok"` / `"warn"` / `"fail"` / `"skip"`。
        detail: 人間可読の説明（切り分けに使う具体的な情報を含める）。
        value: 機械可読の値（JSON 直列化可能な dict / プリミティブ / `None`）。
    """

    name: str
    status: Literal["ok", "warn", "fail", "skip"]
    detail: str
    value: object | None


class Doctor:
    """実行環境が RealSense D435 での取得に足るかを診断する（design.md Doctor）。

    `capture` 本体（`open_source()` / CLI の `capture` サブコマンド等）とは
    独立に構築・実行できる（要件 1.10）。SDK 非導入環境でも構築・`check()`
    の両方が例外を送出せずに完走する。

    Preconditions: なし（構築時に SDK・デバイス・記録先の存在を要求しない）。
    Postconditions: `check()` は常に9件の `CheckResult`（`os` / `python` /
        `memory` / `sdk_import` / `device` / `usb` / `stream_open` /
        `power_stability` / `disk`）を、1件も欠けることなく返す。個々の
        項目が `fail` / `skip` であっても他の項目の実行を妨げない。
    Invariants: `check()` はどの項目の実装が例外を送出しても
        `Doctor.check()` 自体からは例外を伝播させない（項目ごとの
        `try/except` に加え、`check()` 自体にも防御網を持つ二重防御）。
    """

    def __init__(
        self,
        *,
        capture_config: CaptureConfig | None = None,
        destination: Path | None = None,
    ) -> None:
        """`Doctor` を組み立てる。

        Args:
            capture_config: `stream_open` / `power_stability` が試行する
                要求モード。未指定なら `CaptureConfig()`（既定値。
                「初期評価候補であり必須性能ではない」— `config.py`
                モジュール docstring 参照）。
            destination: `disk` 項目が空き容量・書き込み速度を確認する
                記録先ディレクトリ。未指定なら `RecordingConfig().root`
                （既定の記録先ルート）。
        """
        self._capture_config = capture_config if capture_config is not None else CaptureConfig()
        self._destination = destination if destination is not None else RecordingConfig().root
        self._sysstat = Sysstat()

        # 実行順は design.md「確認項目」テーブルの掲載順に合わせる。
        # 各要素は `(name, 0引数の check 関数)` — `check()` はこの名前を
        # 使って、関数自体が例外を送出した場合のフォールバック
        # `CheckResult` を組み立てる。
        self._check_functions: tuple[tuple[str, "_CheckFn"], ...] = (
            ("os", self._check_os),
            ("python", self._check_python),
            ("memory", self._check_memory),
            ("sdk_import", self._check_sdk_import),
            ("device", self._check_device),
            ("usb", self._check_usb),
            ("stream_open", self._check_stream_open),
            ("power_stability", self._check_power_stability),
            ("disk", self._check_disk),
        )

    def check(self) -> tuple[CheckResult, ...]:
        """9項目すべてを独立して実行し、常に9件の結果を返す。

        1項目の実装が想定外の例外を送出しても（各項目は自分自身で広く
        `try/except` するが、`sources/realsense.py` 自身が明言する通り
        実 SDK の挙動は未検証であり、その前提が崩れる可能性がある）、
        `fail` の `CheckResult` へ変換して後続の項目の実行を続ける。
        """
        results: list[CheckResult] = []
        for name, fn in self._check_functions:
            try:
                results.append(fn())
            except Exception as exc:  # noqa: BLE001 - 項目実行の最終防御網
                results.append(
                    CheckResult(
                        name=name,
                        status="fail",
                        detail=f"{name} の実行自体が例外を送出した: {exc!r}",
                        value=None,
                    )
                )
        return tuple(results)

    def report_json(self) -> str:
        """`check()` の結果を、実施結果として保存できる JSON 文字列にする。

        `json.loads(report_json())` は9件の `{"name", "status", "detail",
        "value"}` の配列を返す（往復可能。要件 1.3「実施結果を後から参照
        できる形で記録する」）。
        """
        payload = [asdict(result) for result in self.check()]
        return json.dumps(payload, ensure_ascii=False, allow_nan=False)

    # ------------------------------------------------------------------
    # SDK に依存しない項目（この実行環境で実際に動く）
    # ------------------------------------------------------------------

    def _check_os(self) -> CheckResult:
        uname = platform.uname()
        machine_lower = uname.machine.lower()
        # `platform.machine()` が既知の64bitアーキテクチャ名でなくても、
        # 動作中の Python インタプリタのポインタ幅（`sys.maxsize`）を
        # フォールバックの根拠にする。どちらでも判定できない場合に
        # 診断全体を `fail` にはせず、判定できた範囲の情報を返す。
        is_64bit = machine_lower in _64BIT_MACHINE_NAMES or sys.maxsize > 2**32
        os_release = _read_os_release()

        value: dict[str, object] = {
            "system": uname.system,
            "release": uname.release,
            "version": uname.version,
            "machine": uname.machine,
            "is_64bit": is_64bit,
            "os_release": os_release,
        }

        pretty_name = os_release.get("PRETTY_NAME") if os_release else None
        label = pretty_name if pretty_name else uname.system
        detail = f"{label} / kernel {uname.release} ({uname.machine}), 64bit={is_64bit}"
        return CheckResult(name="os", status="ok", detail=detail, value=value)

    def _check_python(self) -> CheckResult:
        # `sys.version_info` はそのままでは JSON 直列化できない named tuple
        # なので、プレーンな list へ変換して `value` に収める。
        value = {
            "version": platform.python_version(),
            "version_info": list(sys.version_info),
            "executable": sys.executable,
        }
        detail = f"Python {platform.python_version()} ({sys.executable})"
        return CheckResult(name="python", status="ok", detail=detail, value=value)

    def _check_memory(self) -> CheckResult:
        sample = self._sysstat.sample()
        value = {
            "available": Sysstat.available(),
            "system_total_bytes": sample.system_total_bytes,
            "system_available_bytes": sample.system_available_bytes,
        }
        if sample.system_total_bytes is None:
            return CheckResult(
                name="memory",
                status="fail",
                detail=(
                    "/proc からメモリ情報を読めなかった（Linux 以外、または "
                    "制限されたコンテナの可能性がある）"
                ),
                value=value,
            )
        detail = (
            f"搭載 RAM {sample.system_total_bytes} bytes, "
            f"利用可能 {sample.system_available_bytes} bytes"
        )
        return CheckResult(name="memory", status="ok", detail=detail, value=value)

    # ------------------------------------------------------------------
    # SDK 関連の項目（`sources.realsense` の probe 関数 / アダプタを経由し、
    # 本モジュール自身は pyrealsense2 を import しない）
    # ------------------------------------------------------------------

    def _check_sdk_import(self) -> CheckResult:
        result = probe_sdk()
        value = dict(result)
        if not result["available"]:
            return CheckResult(
                name="sdk_import", status="fail", detail=str(result["error"]), value=value
            )
        detail = f"pyrealsense2 version={result['version']!r} at {result['location']!r}"
        return CheckResult(name="sdk_import", status="ok", detail=detail, value=value)

    def _check_device(self) -> CheckResult:
        result = probe_devices()
        value = dict(result)
        if not result["available"]:
            return CheckResult(
                name="device",
                status="fail",
                detail=f"SDK が利用できないためデバイスを列挙できない: {result['error']}",
                value=value,
            )
        if result["error"] is not None:
            return CheckResult(
                name="device",
                status="fail",
                detail=f"デバイス列挙自体が失敗した: {result['error']}",
                value=value,
            )
        devices = result["devices"]
        if not devices:
            return CheckResult(
                name="device",
                status="fail",
                detail="デバイスが1台も列挙されなかった（D435 の接続を確認せよ）",
                value=value,
            )
        labels = ", ".join(str(dev.get("serial_number") or "?") for dev in devices)
        detail = f"{len(devices)}台検出（serial: {labels}）"
        return CheckResult(name="device", status="ok", detail=detail, value=value)

    def _check_usb(self) -> CheckResult:
        # `device` 項目と同じ `probe_devices()` を独立に呼び直す（項目間の
        # 共有状態を持たず、各項目が単独でも成立するようにするため —
        # `probe_devices()` 自体は例外を送出しない設計であり、コストも
        # 軽い。モジュール docstring「`usb`: 判定不能時は `fail` ではなく
        # `skip` にする」参照）。
        result = probe_devices()
        if not result["available"]:
            return CheckResult(
                name="usb",
                status="skip",
                detail=(
                    "SDK が利用できないため USB 接続種別を判定できない"
                    "（sdk_import を確認せよ）"
                ),
                value=None,
            )
        devices = result["devices"]
        if not devices:
            return CheckResult(
                name="usb",
                status="skip",
                detail="デバイスが列挙されなかったため USB 接続種別を判定できない",
                value=None,
            )
        usb_type = devices[0].get("usb_type_descriptor")
        if usb_type is None:
            return CheckResult(
                name="usb",
                status="fail",
                detail="usb_type_descriptor を取得できなかった（SDK ビルド構成に依存する欠測）",
                value=None,
            )
        if str(usb_type).startswith("2."):
            return CheckResult(
                name="usb",
                status="warn",
                detail=(
                    f"USB2 接続を検出した（{usb_type}）。fps 計測結果を有効なものとして"
                    "扱わないこと（要件 1.5）"
                ),
                value=usb_type,
            )
        return CheckResult(
            name="usb", status="ok", detail=f"USB {usb_type} 接続を検出した", value=usb_type
        )

    def _check_stream_open(self) -> CheckResult:
        clock = SessionClock(session_id="doctor-stream-open")
        metrics = CaptureMetrics(NullLogger(), clock, sysstat=None)
        source = RealSenseSource(self._capture_config, metrics, clock=clock)
        try:
            source.start()
        except SourceUnavailableError as exc:
            return CheckResult(name="stream_open", status="fail", detail=str(exc), value=None)
        except Exception as exc:  # noqa: BLE001 - 未検証な実SDK挙動への防御
            return CheckResult(
                name="stream_open",
                status="fail",
                detail=f"予期しない例外でストリームを開けなかった: {exc!r}",
                value=None,
            )

        try:
            profile = source.profile
        finally:
            source.stop()

        cfg = self._capture_config
        detail = (
            f"要求モード {cfg.width_px}x{cfg.height_px}@{cfg.fps}fps"
            f"（color_enabled={cfg.color_enabled}）でストリームを開けた"
        )
        value = {
            "width_px": profile.width_px,
            "height_px": profile.height_px,
            "fps": profile.fps,
            "color_enabled": profile.color_enabled,
        }
        return CheckResult(name="stream_open", status="ok", detail=detail, value=value)

    def _check_power_stability(self) -> CheckResult:
        # 呼び出し側の `capture_config` をそのまま使うと、取得失敗時に
        # `on_acquire_error="continue"`（既定）だと `frames()` が内部で
        # 再試行を繰り返し、診断が長時間ブロックし得る。本項目専用に
        # `on_acquire_error="stop"` かつ短いタイムアウトへ固定した設定で
        # 試行する（モジュール docstring「`power_stability`」参照）。
        probe_config = replace(
            self._capture_config,
            acquire_timeout_ms=_STABILITY_ACQUIRE_TIMEOUT_MS,
            on_acquire_error="stop",
        )
        clock = SessionClock(session_id="doctor-power-stability")
        metrics = CaptureMetrics(NullLogger(), clock, sysstat=None)
        source = RealSenseSource(probe_config, metrics, clock=clock)

        try:
            source.start()
        except SourceUnavailableError as exc:
            return CheckResult(
                name="power_stability",
                status="skip",
                detail=f"この条件では観測されなかった（試行できなかった: {exc}）",
                value=None,
            )
        except Exception as exc:  # noqa: BLE001 - 未検証な実SDK挙動への防御
            return CheckResult(
                name="power_stability",
                status="skip",
                detail=f"この条件では観測されなかった（予期しない例外で試行できなかった: {exc!r}）",
                value=None,
            )

        observed_frames = 0
        failure: str | None = None
        try:
            for _ in islice(source.frames(), _STABILITY_MAX_FRAMES):
                observed_frames += 1
        except SourceUnavailableError as exc:
            # `on_acquire_error="stop"` により送出される。取得失敗そのものは
            # 「観測された事実」として `warn` で報告する（例外を上へ投げない）。
            failure = str(exc)
        except Exception as exc:  # noqa: BLE001 - 未検証な実SDK挙動への防御
            failure = f"予期しない例外: {exc!r}"
        finally:
            stats = source.stats
            source.stop()

        value = {
            "observed_frames": observed_frames,
            "dropped": stats.frames_dropped,
            "missing": stats.frames_missing,
            "acquire_errors": stats.acquire_errors,
        }
        if failure is not None:
            return CheckResult(
                name="power_stability",
                status="warn",
                detail=f"{observed_frames}枚取得後に取得失敗が発生した: {failure}",
                value=value,
            )
        return CheckResult(
            name="power_stability",
            status="ok",
            detail=(
                f"{observed_frames}枚の短時間取得では切断・欠落は観測されなかった"
                "（この条件下でのみの観測であり、恒久的な安定性の保証ではない）"
            ),
            value=value,
        )

    # ------------------------------------------------------------------
    # SDK に依存しない項目（記録先のディスク）
    # ------------------------------------------------------------------

    def _check_disk(self) -> CheckResult:
        try:
            self._destination.mkdir(parents=True, exist_ok=True)
            usage = shutil.disk_usage(self._destination)
        except OSError as exc:
            return CheckResult(
                name="disk",
                status="fail",
                detail=f"記録先 {self._destination} を確認できなかった: {exc}",
                value={"path": str(self._destination)},
            )

        write_speed_mb_s = _measure_write_speed_mb_s(self._destination)
        value = {
            "path": str(self._destination),
            "free_bytes": usage.free,
            "total_bytes": usage.total,
            "write_speed_mb_s": write_speed_mb_s,
        }
        if write_speed_mb_s is None:
            detail = (
                f"{self._destination}: 空き {usage.free} / {usage.total} bytes"
                "（書き込み速度の計測には失敗した）"
            )
        else:
            detail = (
                f"{self._destination}: 空き {usage.free} / {usage.total} bytes, "
                f"書き込み速度目安 {write_speed_mb_s:.1f} MB/s"
            )
        return CheckResult(name="disk", status="ok", detail=detail, value=value)


# --------------------------------------------------------------------------
# ヘルパ（クラス外・SDK 非依存）
# --------------------------------------------------------------------------


def _read_os_release(path: Path = _OS_RELEASE_PATH) -> dict[str, str] | None:
    """`/etc/os-release` を `KEY=VALUE` 行の dict として読む。読めなければ `None`（欠測）。"""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return None
    result: dict[str, str] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw_value = line.partition("=")
        result[key] = raw_value.strip().strip('"')
    return result or None


def _measure_write_speed_mb_s(
    destination: Path, size_bytes: int = _DISK_PROBE_SIZE_BYTES
) -> float | None:
    """既知サイズの一時ファイルを1回書き込み、所要時間から MB/s の目安を返す。

    単発計測にとどめる（モジュール docstring「`disk`」参照）。書き込み・
    `fsync` のいずれかが失敗した場合は `None`（欠測）を返し、例外は
    送出しない。
    """
    try:
        fd, tmp_name = tempfile.mkstemp(dir=destination, prefix=".doctor_disk_probe_")
    except OSError:
        return None

    tmp_path = Path(tmp_name)
    try:
        payload = b"\0" * size_bytes
        started = time.perf_counter()
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        elapsed_s = time.perf_counter() - started
    except OSError:
        return None
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass

    if elapsed_s <= 0:
        return None
    size_mb = size_bytes / (1024 * 1024)
    return size_mb / elapsed_s
