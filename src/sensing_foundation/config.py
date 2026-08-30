"""実行時設定の解決と起動時検証（design.md「L0-L2: 基盤 / RuntimeSettings」）。

`CaptureConfig` / `RecordingConfig` / `LoggingConfig` / `RuntimeSettings` を
定義し、`RuntimeSettings.resolve()` で **CLI 引数 > 環境変数 > 設定ファイル >
既定値** の優先順位で1つの不変な設定へ解決する（要件 12.4）。解決は
`resolve()` の呼び出し時点、すなわち**取得を開始する前**に完了し、不正な
組み合わせはこの時点で `SensingConfigError` として拒否する（design.md
RuntimeSettings「Validation: 拒否は起動時に行う。取得開始後に不正が判明する
状態を作らない」）。

**Color は既定で無効**にする（`CaptureConfig.color_enabled = False`）。
これは `tech.md` §13.2 の改善順序1番目（Color ストリームを止めて Depth のみに
絞る）を既定で満たすためである（要件 11.7）。

**解像度・fps の既定値（640×480 / 30fps）は「初期評価候補」であり、
必須性能でも達成済み性能でもない。** タスク 9.4 の実測比較で採用設定が
決まるまでの暫定値であり、`tech.md` 開発標準1（未検証の数値目標を要件のように
埋め込まない）に従い、既成事実化しないようこの docstring と `--help`
（将来の CLI タスク）の双方に明記する。

解決順序の設定キー空間
----------------------
CLI 引数（`overrides`）・環境変数（`env`）・設定ファイル（`file`）の3層は、
**同一のフラットなキー名の集合**を共有する（例: `"fps"`）。環境変数は
このキー名を大文字化し `STB_SF_` を前置したものを参照する
（例: `fps` → `STB_SF_FPS`）。設定ファイルは JSON で、トップレベルの
キーがこのキー名と一致する必要がある。キー名が `CaptureConfig` /
`RecordingConfig` / `LoggingConfig` の間でフィールド名が重複する場合
（`enabled` / `queue_capacity`）は、`recording_enabled` / `logging_enabled` /
`queue_capacity`（capture 側）/ `logging_queue_capacity` のように区別する。
完全なキー一覧は `_FIELD_SPECS` を参照。

**`Sysstat` への依存についての設計判断**: design.md の Components table
（`## Components and Interfaces`）は `RuntimeSettings` の Key Dependencies を
`types (P1)` のみと列挙しており、`Sysstat`（L0 計測）を挙げていない。一方
RuntimeSettings の Invariants は「`recording.mode == "ring"` のとき、
必要 RAM が `max_ring_bytes`（既定は搭載 RAM の 25%）を超えたら
`SensingConfigError`」と書いており、搭載 RAM の実測値がどこかから供給される
必要がある。本モジュールはこの2つの記述を「`config.py` が `Sysstat` を
直接 import してよい」とは読まず、**搭載 RAM 量を `resolve()` の明示引数
`installed_ram_bytes: int | None = None` として受け取る**設計を採った。
理由:

1. Boundary（本タスクの `_Boundary: RuntimeSettings_`）と Dependency
   Direction は `config`（L2）が `errors` と `types` のみを import してよいと
   定めており、`Sysstat`（L0）は依存方向としては下位レイヤで import 自体は
   許されるが、Components table の Key Dependencies 欄には明記されていない。
   設計意図が薄い依存を追加するより、呼び出し側（将来の CLI 配線タスク 8.1）
   が `Sysstat.sample().system_total_bytes` を求めて渡す形の方が、
   「`config.py` は設定の解決のみを行い、資源計測は行わない」という役割分担が
   明確になる。
2. `resolve()` は現時点でも純粋な関数であり、`Sysstat`（`/proc` を読む I/O）を
   持ち込むとテストが環境依存になる。`installed_ram_bytes` を引数化すれば、
   テストからは既知の値を注入でき、実行環境では呼び出し側が実測値を渡す。

`max_ring_bytes`（明示設定）も `installed_ram_bytes`（呼び出し側からの供給）も
無い場合、根拠の無い固定バイト数を安全上限として捏造するより **RAM 予算
チェックを行わない方が誠実**と判断した（`tech.md` 開発標準1: 検証されていない
数値を要件のように埋め込まない、という考え方をここにも適用した）。この場合の
挙動はテスト
`test_ram_budget_check_skipped_when_no_cap_and_no_installed_ram_supplied`
で固定している。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from sensing_foundation.errors import SensingConfigError
from sensing_foundation.types import SourceKind

# --------------------------------------------------------------------------
# 設定データクラス（design.md RuntimeSettings のインタフェースそのまま）
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CaptureConfig:
    """取得設定（要件 11.1, 11.7）。

    Attributes:
        width_px: Depth ストリームの幅（px）。⚠️ **依然として初期評価候補で
            あり必須性能ではない。** タスク 9.4 の候補モード列は
            design.md が定めるとおり 640×480/30fps と 640×480/60fps の2つ
            であり（要件 11.2）、**解像度は他の値と比較していない**。
            fps とは異なりこの値には実測の裏付けが無い。
        height_px: Depth ストリームの高さ（px）。width_px と同様、
            **初期評価候補であり必須性能ではない**。
        fps: 要求フレームレート（fps）。**タスク 9.4 の実機実測に基づき
            30 から 60 へ変更した。** 640×480 で 30fps と 60fps を同一条件で
            比較した結果、60fps は 200ms 窓あたりの実効サンプル数が
            約 6.0 → 約 12.0 とほぼ正確に倍増し、破棄ゼロ・欠落ほぼゼロ
            （1200枚中1件）・CPU 約 5〜6 % で成立した
            （`measurements.md` タスク9.4）。
            これは「60 fps ありき」ではなく実効サンプル数に基づく選定である
            （要件 11 Objective）——取りこぼしが出ていれば 30fps を採る判断
            だった。
            ⚠️ **依然として合否条件ではない**（要件 11.6）。また実効サンプル数
            には**フレーム層と点層の2つがある**。ここで倍増したのは
            **フレーム層**であり、検出処理を含む**点層**の実効点数は
            `flying-object-tracking` の測定対象で未測定である。下流の処理が
            1フレーム 16.6ms に収まらない場合、ドレインが働いて点層の
            サンプル数は倍増しない（この場合でも欠落は生じず、余分な取得
            コストを払うだけである）。下流の実測後に再検討してよい。
        color_enabled: Color ストリームを有効にするか。**既定で無効**
            （`tech.md` §13.2 改善順序1番目を既定で満たす。要件 11.7）。
        queue_capacity: 取得キューの容量。小さく保つことで古いフレームの
            滞留を防ぐ。
        drain_enabled: ドレイン（待機で1枚受け取った直後に滞留分を捨てて
            最新へ追いつく）を行うか。live のみで意味を持つ。
        acquire_timeout_ms: 1フレーム取得の最大待機時間（ms）。
        on_acquire_error: 取得失敗時の挙動。`"continue"` なら失敗を計数して
            継続し、`"stop"` なら取得を停止する。
    """

    width_px: int = 640
    height_px: int = 480
    fps: int = 60
    color_enabled: bool = False
    queue_capacity: int = 1
    drain_enabled: bool = True
    acquire_timeout_ms: int = 5000
    on_acquire_error: Literal["continue", "stop"] = "continue"


@dataclass(frozen=True, slots=True)
class RecordingConfig:
    """記録設定（要件 5系）。

    Attributes:
        enabled: 記録を有効にするか。
        mode: `"ring"`（直近 N 秒のみ保持）か `"continuous"`（継続記録）か。
        ring_seconds: `mode == "ring"` のときに保持する秒数。
        compression: ブロブの圧縮方式。
        root: セッションディレクトリの出力先ルート。版管理対象外の場所に
            置く（要件 12.6）。
        max_ring_bytes: リングバッファの許容バイト数の上限。`None` の場合、
            `RuntimeSettings.resolve()` が搭載 RAM（呼び出し側が
            `installed_ram_bytes` として供給した場合）から上限を導く。
    """

    enabled: bool = False
    mode: Literal["ring", "continuous"] = "ring"
    ring_seconds: float = 3.0
    compression: Literal["none", "zlib"] = "none"
    root: Path = Path("var/sessions")
    max_ring_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class LoggingConfig:
    """ロギング設定（要件 8系）。

    Attributes:
        enabled: ロギングを有効にするか。無効時は `NullLogger`（タスク 2.1）
            に差し替えられる想定。
        path: ログファイルの出力先ディレクトリ。版管理対象外の場所に置く
            （要件 12.6）。
        queue_capacity: ログ送出キューの容量。満杯時はログを破棄して取得を
            優先する（要件 8.6）。
        flush_each_line: 行単位でフラッシュするか。
    """

    enabled: bool = True
    path: Path = Path("var/logs")
    queue_capacity: int = 4096
    flush_each_line: bool = True


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    """解決済みの実行時設定（要件 11.1, 11.7, 12.4）。

    `resolve()` classmethod のみが構築の入口である。直接コンストラクタで
    組み立てた `RuntimeSettings` は `resolve()` が行う Precondition /
    Invariant の検証（起動時拒否）を経ないため、実行時に組み立てる場合は
    必ず `resolve()` を経由すること。

    **解像度・fps（`capture.width_px` / `capture.height_px` / `capture.fps`）
    の既定値は「初期評価候補」であり、必須性能でも達成済み性能でもない。**
    タスク 9.4 の実測比較（実効サンプル数での評価）で採用値が決まるまでの
    暫定値である（`tech.md` 開発標準1）。

    Postconditions: 解決後の設定は不変（`frozen=True, slots=True`）であり、
        取得開始後も変更されない。

    Attributes:
        source: 入力元の種別。`SourceKind.RECORDED` のとき `session_path`
            が必須になる（Invariant）。
        capture: 取得設定。
        recording: 記録設定。
        logging: ロギング設定。
        session_path: 再生対象のセッションディレクトリ。`source ==
            SourceKind.RECORDED` のときのみ必須。
    """

    source: SourceKind = SourceKind.LIVE
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    recording: RecordingConfig = field(default_factory=RecordingConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    session_path: Path | None = None

    @classmethod
    def resolve(
        cls,
        *,
        file: Path | None,
        env: Mapping[str, str],
        overrides: Mapping[str, object],
        installed_ram_bytes: int | None = None,
    ) -> "RuntimeSettings":
        """設定を **CLI 引数 > 環境変数 > 設定ファイル > 既定値** の順で解決する。

        Args:
            file: JSON 設定ファイルのパス。`None` なら設定ファイル層を
                スキップする。存在しない・JSON として解釈できない場合は
                `SensingConfigError` を送出する。
            env: 環境変数のマッピング（例: `os.environ`）。`STB_SF_` を
                前置したキーのみを参照する（例: `STB_SF_FPS`）。
            overrides: CLI 引数など、最優先で適用する値のマッピング。
                キーは `file` / `env` と同じフラットなキー名を使う。
            installed_ram_bytes: 搭載 RAM のバイト数。`recording.max_ring_bytes`
                が未設定（`None`）のときのリングバッファ RAM 上限
                （既定: この値の25%）を導出するために使う。呼び出し側が
                `Sysstat.sample().system_total_bytes` などから取得して渡す
                想定（モジュール docstring の設計判断を参照）。省略した場合、
                かつ `recording.max_ring_bytes` も未設定の場合は RAM 予算
                チェックを行わない。

        Returns:
            検証済みで不変な `RuntimeSettings`。

        Raises:
            SensingConfigError: 設定値が Precondition / Invariant を満たさ
                ない場合（`fps<=0` など）、`source == SourceKind.RECORDED`
                なのに `session_path` が未指定の場合、リングバッファの
                必要 RAM が上限を超える場合、設定ファイルが読めない・
                不正な場合、未知の設定キーが指定された場合。
        """
        values: dict[str, object] = {
            key: _default_value(spec) for key, spec in _FIELD_SPECS.items()
        }

        if file is not None:
            file_values = _load_file(file)
            _apply_layer(values, file_values, layer_name="設定ファイル")

        env_values = _extract_env(env)
        _apply_layer(values, env_values, layer_name="環境変数")

        _apply_layer(values, overrides, layer_name="CLI引数")

        capture = CaptureConfig(
            width_px=values["width_px"],  # type: ignore[arg-type]
            height_px=values["height_px"],  # type: ignore[arg-type]
            fps=values["fps"],  # type: ignore[arg-type]
            color_enabled=values["color_enabled"],  # type: ignore[arg-type]
            queue_capacity=values["queue_capacity"],  # type: ignore[arg-type]
            drain_enabled=values["drain_enabled"],  # type: ignore[arg-type]
            acquire_timeout_ms=values["acquire_timeout_ms"],  # type: ignore[arg-type]
            on_acquire_error=values["on_acquire_error"],  # type: ignore[arg-type]
        )
        recording = RecordingConfig(
            enabled=values["recording_enabled"],  # type: ignore[arg-type]
            mode=values["recording_mode"],  # type: ignore[arg-type]
            ring_seconds=values["ring_seconds"],  # type: ignore[arg-type]
            compression=values["compression"],  # type: ignore[arg-type]
            root=values["recording_root"],  # type: ignore[arg-type]
            max_ring_bytes=values["max_ring_bytes"],  # type: ignore[arg-type]
        )
        logging_cfg = LoggingConfig(
            enabled=values["logging_enabled"],  # type: ignore[arg-type]
            path=values["logging_path"],  # type: ignore[arg-type]
            queue_capacity=values["logging_queue_capacity"],  # type: ignore[arg-type]
            flush_each_line=values["flush_each_line"],  # type: ignore[arg-type]
        )
        settings = cls(
            source=values["source"],  # type: ignore[arg-type]
            capture=capture,
            recording=recording,
            logging=logging_cfg,
            session_path=values["session_path"],  # type: ignore[arg-type]
        )

        _validate(settings, installed_ram_bytes=installed_ram_bytes)
        return settings


# --------------------------------------------------------------------------
# キー空間の定義と型変換
# --------------------------------------------------------------------------

_ENV_PREFIX = "STB_SF_"
_DEFAULT_CAPTURE = CaptureConfig()
_DEFAULT_RECORDING = RecordingConfig()
_DEFAULT_LOGGING = LoggingConfig()

_TRUE_STRINGS = frozenset({"1", "true", "yes", "on"})
_FALSE_STRINGS = frozenset({"0", "false", "no", "off"})
_NONE_STRINGS = frozenset({"", "none", "null"})


def _coerce_int(raw: object) -> int:
    if isinstance(raw, bool):
        raise SensingConfigError(f"整数を期待したが真偽値を受け取った: {raw!r}")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError as exc:
            raise SensingConfigError(f"整数として解釈できない設定値: {raw!r}") from exc
    raise SensingConfigError(f"整数を期待したが {type(raw).__name__} を受け取った: {raw!r}")


def _coerce_optional_int(raw: object) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, str) and raw.strip().lower() in _NONE_STRINGS:
        return None
    return _coerce_int(raw)


def _coerce_float(raw: object) -> float:
    if isinstance(raw, bool):
        raise SensingConfigError(f"数値を期待したが真偽値を受け取った: {raw!r}")
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw)
        except ValueError as exc:
            raise SensingConfigError(f"数値として解釈できない設定値: {raw!r}") from exc
    raise SensingConfigError(f"数値を期待したが {type(raw).__name__} を受け取った: {raw!r}")


def _coerce_bool(raw: object) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        lowered = raw.strip().lower()
        if lowered in _TRUE_STRINGS:
            return True
        if lowered in _FALSE_STRINGS:
            return False
        raise SensingConfigError(f"真偽値として解釈できない設定値: {raw!r}")
    raise SensingConfigError(f"真偽値を期待したが {type(raw).__name__} を受け取った: {raw!r}")


def _coerce_str(raw: object) -> str:
    if isinstance(raw, str):
        return raw
    raise SensingConfigError(f"文字列を期待したが {type(raw).__name__} を受け取った: {raw!r}")


def _coerce_path(raw: object) -> Path:
    if isinstance(raw, Path):
        return raw
    if isinstance(raw, str):
        return Path(raw)
    raise SensingConfigError(f"パスを期待したが {type(raw).__name__} を受け取った: {raw!r}")


def _coerce_optional_path(raw: object) -> Path | None:
    if raw is None:
        return None
    if isinstance(raw, str) and raw.strip().lower() in _NONE_STRINGS:
        return None
    return _coerce_path(raw)


def _coerce_source_kind(raw: object) -> SourceKind:
    if isinstance(raw, SourceKind):
        return raw
    if isinstance(raw, str):
        try:
            return SourceKind(raw)
        except ValueError as exc:
            raise SensingConfigError(f"未知の入力元種別: {raw!r}") from exc
    raise SensingConfigError(
        f"入力元種別を期待したが {type(raw).__name__} を受け取った: {raw!r}"
    )


@dataclass(frozen=True, slots=True)
class _FieldSpec:
    """1つの設定キーが対応する（グループ, 属性名, 既定値, 型変換）。"""

    group: Literal["capture", "recording", "logging", None]
    attr: str
    coerce: Callable[[object], object]


_FIELD_SPECS: dict[str, _FieldSpec] = {
    "source": _FieldSpec(None, "source", _coerce_source_kind),
    "session_path": _FieldSpec(None, "session_path", _coerce_optional_path),
    "width_px": _FieldSpec("capture", "width_px", _coerce_int),
    "height_px": _FieldSpec("capture", "height_px", _coerce_int),
    "fps": _FieldSpec("capture", "fps", _coerce_int),
    "color_enabled": _FieldSpec("capture", "color_enabled", _coerce_bool),
    "queue_capacity": _FieldSpec("capture", "queue_capacity", _coerce_int),
    "drain_enabled": _FieldSpec("capture", "drain_enabled", _coerce_bool),
    "acquire_timeout_ms": _FieldSpec("capture", "acquire_timeout_ms", _coerce_int),
    "on_acquire_error": _FieldSpec("capture", "on_acquire_error", _coerce_str),
    "recording_enabled": _FieldSpec("recording", "enabled", _coerce_bool),
    "recording_mode": _FieldSpec("recording", "mode", _coerce_str),
    "ring_seconds": _FieldSpec("recording", "ring_seconds", _coerce_float),
    "compression": _FieldSpec("recording", "compression", _coerce_str),
    "recording_root": _FieldSpec("recording", "root", _coerce_path),
    "max_ring_bytes": _FieldSpec("recording", "max_ring_bytes", _coerce_optional_int),
    "logging_enabled": _FieldSpec("logging", "enabled", _coerce_bool),
    "logging_path": _FieldSpec("logging", "path", _coerce_path),
    "logging_queue_capacity": _FieldSpec("logging", "queue_capacity", _coerce_int),
    "flush_each_line": _FieldSpec("logging", "flush_each_line", _coerce_bool),
}

_DEFAULT_OBJECTS = {
    "capture": _DEFAULT_CAPTURE,
    "recording": _DEFAULT_RECORDING,
    "logging": _DEFAULT_LOGGING,
}


def _default_value(spec: _FieldSpec) -> object:
    if spec.group is None:
        return SourceKind.LIVE if spec.attr == "source" else None
    return getattr(_DEFAULT_OBJECTS[spec.group], spec.attr)


def _extract_env(env: Mapping[str, str]) -> dict[str, str]:
    """既知のキーに対応する `STB_SF_` 環境変数のみを取り出す。

    未知の `STB_SF_*` 変数や無関係な環境変数は無視する（環境変数は本パッケージ
    が知らない用途にも使われ得るため、file / overrides と異なり未知キーで
    エラーにはしない）。
    """
    extracted: dict[str, str] = {}
    for key in _FIELD_SPECS:
        env_key = _ENV_PREFIX + key.upper()
        if env_key in env:
            extracted[key] = env[env_key]
    return extracted


def _load_file(file: Path) -> Mapping[str, object]:
    try:
        content = file.read_text(encoding="utf-8")
    except OSError as exc:
        raise SensingConfigError(f"設定ファイルを読み込めない: {file}") from exc
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise SensingConfigError(f"設定ファイルが JSON として解釈できない: {file}") from exc
    if not isinstance(data, dict):
        raise SensingConfigError(
            f"設定ファイルの最上位は JSON オブジェクトでなければならない: {file}"
        )
    return data


def _apply_layer(
    values: dict[str, object], raw_layer: Mapping[str, object], *, layer_name: str
) -> None:
    for key, raw in raw_layer.items():
        spec = _FIELD_SPECS.get(key)
        if spec is None:
            raise SensingConfigError(f"未知の設定キー {key!r}（{layer_name}で指定）")
        values[key] = spec.coerce(raw)


# --------------------------------------------------------------------------
# 起動時検証（Preconditions / Invariants）
# --------------------------------------------------------------------------

_BYTES_PER_DEPTH_PIXEL = 2  # uint16
_DEFAULT_RAM_CAP_FRACTION = 0.25  # design.md Invariants: 「既定は搭載 RAM の25%」


def _validate(settings: RuntimeSettings, *, installed_ram_bytes: int | None) -> None:
    capture = settings.capture
    recording = settings.recording
    logging_cfg = settings.logging

    if capture.fps <= 0:
        raise SensingConfigError(f"capture.fps は正でなければならない: {capture.fps}")
    if capture.width_px <= 0:
        raise SensingConfigError(
            f"capture.width_px は正でなければならない: {capture.width_px}"
        )
    if capture.height_px <= 0:
        raise SensingConfigError(
            f"capture.height_px は正でなければならない: {capture.height_px}"
        )
    if capture.queue_capacity < 1:
        raise SensingConfigError(
            f"capture.queue_capacity は1以上でなければならない: {capture.queue_capacity}"
        )
    if logging_cfg.queue_capacity < 1:
        raise SensingConfigError(
            f"logging.queue_capacity は1以上でなければならない: {logging_cfg.queue_capacity}"
        )
    if recording.ring_seconds <= 0:
        raise SensingConfigError(
            f"recording.ring_seconds は正でなければならない: {recording.ring_seconds}"
        )

    if settings.source == SourceKind.RECORDED and settings.session_path is None:
        raise SensingConfigError(
            "source が RECORDED（再生）のとき session_path は必須である。"
            "再生対象のセッションディレクトリを指定せよ。"
        )

    if recording.mode == "ring":
        required_bytes = _required_ring_bytes(capture, recording)
        cap_bytes = _resolve_ring_cap_bytes(recording.max_ring_bytes, installed_ram_bytes)
        if cap_bytes is not None and required_bytes > cap_bytes:
            raise SensingConfigError(
                f"リングバッファに必要な RAM（約 {required_bytes} bytes）が上限"
                f"（{cap_bytes} bytes）を超えている。解像度・fps・ring_seconds を"
                "小さくするか、max_ring_bytes を明示的に指定せよ。"
            )


def _required_ring_bytes(capture: CaptureConfig, recording: RecordingConfig) -> int:
    """`design.md` Invariants の式: `width*height*2*fps*ring_seconds`。"""
    return int(
        capture.width_px
        * capture.height_px
        * _BYTES_PER_DEPTH_PIXEL
        * capture.fps
        * recording.ring_seconds
    )


def _resolve_ring_cap_bytes(
    max_ring_bytes: int | None, installed_ram_bytes: int | None
) -> int | None:
    """有効な RAM 上限バイト数を返す。チェック不要なら `None`。

    優先順位: 明示 `max_ring_bytes` > `installed_ram_bytes` から導出（25%）
    > チェックを行わない（両方無い場合）。モジュール docstring の
    「`Sysstat` への依存についての設計判断」を参照。
    """
    if max_ring_bytes is not None:
        return max_ring_bytes
    if installed_ram_bytes is None:
        return None
    return int(installed_ram_bytes * _DEFAULT_RAM_CAP_FRACTION)
