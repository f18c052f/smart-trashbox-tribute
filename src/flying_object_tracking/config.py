"""実行時設定の解決と起動時検証（design.md「L1-L2: 型と設定 / TrackingSettings」）。

`RoiConfig` / `ObjectModelConfig` / `DetectorConfig` / `ProjectionConfig` /
`TrackerConfig` / `MeasurementConfig` / `TrackingSettings` を定義し、
`TrackingSettings.resolve()` で **CLI 引数 > 環境変数 > 設定ファイル > 既定値**
の優先順位で1つの不変な設定へ解決する（要件 12.1, 12.5, 12.6）。解決は
`resolve()` の呼び出し時点、すなわち検出・追跡を開始する前に完了し、不正な
組み合わせはこの時点で `TrackingConfigError` として拒否する（要件 10.5）。
本モジュールの構造は上流 `sensing_foundation.config`（`RuntimeSettings`）を
踏襲しており、`_FieldSpec` テーブルによるキー空間の一元管理・型変換・
起動時検証という同じ技法を用いる。

**対象物の想定寸法を独立した設定として分離する。** `ObjectModelConfig` は
`RoiConfig` / `DetectorConfig` 等から独立したデータクラスであり、OQ-02
（最終スコープの対象物）が決まったときの変更をここだけに閉じる（要件 12.5）。

**`DetectorKind` の置き場所についての設計判断**: design.md は
`DetectorKind`（`StrEnum`。`DEPTH_BAND` / `FRAME_DIFF` / `BACKGROUND`）を
「L4-L5: 検出」節（`MaskBuilder`）で形式的に定義しているが、本モジュール
（L2 `config`）の `DetectorConfig.kind` は構築時点でこの列挙型を必要とする。
「Dependency Direction」表は `Types --> Config`（左から右）のみを許し、
`Config --> Masks`（L2 が L4 を import する逆流）を禁じるため、`config` が
`detection/masks` から `DetectorKind` を import することはできない。
`config` と `detection/masks` の双方が import できる最小の層は `types`
（L1）であるため、`DetectorKind` は `flying_object_tracking.types` に
定義してある（同モジュールのコメントに同じ理由を記載）。本モジュールは
そこから import するだけで、独自に再定義しない。

解決順序の設定キー空間
----------------------
CLI 引数（`overrides`）・環境変数（`env`）・設定ファイル（`file`）の3層は、
**同一のフラットなキー名の集合**を共有する。キー名はどの入れ子設定
（`roi` / `object_model` / `detector` / `projection` / `tracker` /
`measurement`）に属するかを示す接頭辞＋フィールド名で構成する
（例: `roi_x_px` → `RoiConfig.x_px`、`tracker_max_step_mm` →
`TrackerConfig.max_step_mm`）。`output_root` のみ接頭辞を持たない
トップレベルのキーである。

環境変数はこのキー名を大文字化し **`STB_FOT_`** を前置したものを参照する
（例: `roi_x_px` → `STB_FOT_ROI_X_PX`）。上流 `sensing_foundation` の
`STB_SF_` とは接頭辞が異なり、衝突しない（design.md「Implementation Notes /
Integration」）。設定ファイルは JSON で、トップレベルのキーがこのキー名と
一致する必要がある。完全なキー一覧は `_FIELD_SPECS` を参照。

未知のキーの扱いは file / overrides と env で非対称にする。**file /
overrides の未知キーは `TrackingConfigError` として拒否する**（設定ミスを
早期に検出するため）。一方 **env の未知の `STB_FOT_*` は無視する**
（環境変数は本パッケージが知らない用途にも使われ得るため。
`sensing_foundation.config` と同じ方針）。

処理範囲（ROI）の既定挙動
--------------------------
`RoiConfig.width_px` / `height_px` は既定で `None` であり、「フレーム全体を
処理対象とする」ことを表す（要件 2.4）。この解釈は検出層（後続タスク）が
行う。本モジュールは `None` のまま解決結果に残すだけで、フレーム寸法を
知らない。

**フレーム依存の検証（ROI がフレームに収まるか）はここでは行わない。**
design.md の Implementation Notes が明示する通り、その検証は最初のフレーム
受領時に1度だけ行う（後続タスクの責務）。`resolve()` 時点ではフレーム寸法が
まだ分からないため、原理的に検証できない。

既定値についての注記（要件 12.3）
----------------------------------
**既定値のうち `object_model.diameter_mm=65.0` / `detector.kind=FRAME_DIFF` /
`measurement.window_ms=600.0` は「初期評価候補」であり、必須性能でも
達成済み性能でもない。** OQ-02（対象物の最終スコープ）・OQ-26（検出方式の
実測比較）が決着するまでの暫定値であり、`tech.md` 開発標準1（未検証の数値
目標を要件のように埋め込まない）に従い、既成事実化させないよう各
データクラスの docstring と `describe()` の出力の両方に明記する。

`postprocess_fingerprint()` の対象範囲についての設計判断
----------------------------------------------------------
`postprocess_fingerprint()` は「検出方式を差し替えても、それ以外のパイプ
ライン設定が同一であること」を検証するための指紋である（design.md
「決着させる未決事項」「DetectorComparison」節: `compare-detectors` は
実行前に `postprocess_fingerprint` の一致を検証し、不一致なら拒否する）。
したがって指紋の対象は **`roi` / `object_model` / `projection` / `tracker`
/ `measurement`** の5つであり、`detector`（`kind` を含む、検出方式そのもの
を差し替える対象）と `output_root`（処理結果の書き出し先。パイプラインの
挙動そのものには影響しない）は対象から除く。標準ライブラリのみで実装する
（`hashlib.sha256` を対象フィールドの正準 JSON 表現に適用する）。

依存方向（design.md「Dependency Direction」表の `config` 行）:
`errors` ＋ `types`（0〜1層）だけを import してよい。`numpy` / `cv2` /
`sensing_foundation` を import しない。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from flying_object_tracking.errors import TrackingConfigError
from flying_object_tracking.types import DetectorKind

__all__ = [
    "RoiConfig",
    "ObjectModelConfig",
    "DetectorConfig",
    "ProjectionConfig",
    "TrackerConfig",
    "MeasurementConfig",
    "TrackingSettings",
]

# --------------------------------------------------------------------------
# 設定データクラス（design.md TrackingSettings のインタフェースそのまま）
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RoiConfig:
    """処理対象範囲：画素矩形＋距離方向の下限・上限（要件 2.1〜2.4）。

    Attributes:
        x_px: 処理対象矩形の左上 x 座標（画素）。既定 0（フレーム左端から）。
        y_px: 処理対象矩形の左上 y 座標（画素）。既定 0（フレーム上端から）。
        width_px: 処理対象矩形の幅（画素）。`None` なら**フレーム幅全体**を
            対象とする（要件 2.4 の既定挙動）。
        height_px: 処理対象矩形の高さ（画素）。`None` なら**フレーム高さ
            全体**を対象とする（要件 2.4 の既定挙動）。
        z_min_mm: 処理対象とする距離の下限（mm）。`None` なら下限なし。
            **既定値 500.0 は初期評価候補であり必須性能ではない。**
        z_max_mm: 処理対象とする距離の上限（mm）。`None` なら上限なし。
            **既定値 5000.0 は初期評価候補であり必須性能ではない。**
    """

    x_px: int = 0
    y_px: int = 0
    width_px: int | None = None
    height_px: int | None = None
    z_min_mm: float | None = 500.0
    z_max_mm: float | None = 5000.0


@dataclass(frozen=True, slots=True)
class ObjectModelConfig:
    """対象物の物理モデル（要件 12.5）。

    OQ-02（最終スコープの対象物）が決まったとき、変更をこのデータクラス
    だけに閉じるために `RoiConfig` 等から独立させてある。

    Attributes:
        diameter_mm: 対象物の想定直径（mm）。**既定値 65.0（空き缶を想定）は
            初期評価候補であり必須性能でも達成済み性能でもない。** OQ-02
            の最終決定ではない。
        min_scale: 距離から導いた期待画素直径に対する許容下限倍率。
        max_scale: 同上限倍率。`min_scale < max_scale` でなければならない。
        min_area_px: 距離非依存の粗いふるいに使う最小面積（画素数）。
        max_area_px: 同最大面積（画素数）。
    """

    diameter_mm: float = 65.0
    min_scale: float = 0.5
    max_scale: float = 2.0
    min_area_px: int = 12
    max_area_px: int = 4000


@dataclass(frozen=True, slots=True)
class DetectorConfig:
    """検出方式と方式別パラメータ（要件 4.1〜4.3 の材料）。

    Attributes:
        kind: 前景検出方式。**既定値 `DetectorKind.FRAME_DIFF` は初期評価
            候補であり、OQ-26 の実測比較で確定するまでの暫定値である。**
        diff_threshold_mm: `FRAME_DIFF` / `BACKGROUND` 方式で前景と判定する
            Depth 差分の閾値（mm）。
        background_frames: `BACKGROUND` 方式の背景モデル初期化に使う
            フレーム数。
        background_update_rate: `BACKGROUND` 方式の背景モデル更新率。
            **既定 0.0（更新しない）**: 飛翔体を背景に取り込まないため
            （design.md「MaskBuilder / Risks」）。
        open_kernel_px: マスクの開処理（膨張収縮の逆順）に使うカーネル
            サイズ（画素）。
        close_kernel_px: マスクの閉処理に使うカーネルサイズ（画素）。
        max_candidates: 1フレームあたりに採用する候補数の上限。面積降順で
            上位のみ採る。既定 8 は「1投擲＋人・手・背景の揺らぎ」を想定
            した値（design.md「Detector / Risks」）。
    """

    kind: DetectorKind = DetectorKind.FRAME_DIFF
    diff_threshold_mm: float = 80.0
    background_frames: int = 30
    background_update_rate: float = 0.0
    open_kernel_px: int = 3
    close_kernel_px: int = 3
    max_candidates: int = 8


@dataclass(frozen=True, slots=True)
class ProjectionConfig:
    """逆投影パラメータ（要件 5系の材料）。

    Attributes:
        min_valid_depth_px: 候補領域内で3D位置化に必要な有効 Depth 画素数
            の下限。これを下回る場合は3D化に失敗したものとして扱う。
        depth_trim_ratio: 代表値算出時に外れ値として捨てる画素の割合。
            `0.0 <= depth_trim_ratio < 0.5` でなければならない（半分以上を
            捨てると代表値の意味が失われるため）。
        max_depth_spread_mm: 候補領域内の Depth ばらつきの許容上限（mm）。
    """

    min_valid_depth_px: int = 8
    depth_trim_ratio: float = 0.2
    max_depth_spread_mm: float = 200.0


@dataclass(frozen=True, slots=True)
class TrackerConfig:
    """追跡閾値（要件 6系の材料）。

    Attributes:
        max_step_mm: 1フレーム間で許容する3D位置の移動量上限（mm）。
        max_missing_frames: 追跡を打ち切るまでに許容する欠測フレーム数。
            0以上でなければならない。
        min_points_to_start: 追跡を開始したとみなすために必要な最小点数。
        max_points: 1つの追跡が持てる点数の上限（要件 6.9 に関連）。
    """

    max_step_mm: float = 900.0
    max_missing_frames: int = 3
    min_points_to_start: int = 1
    max_points: int = 120


@dataclass(frozen=True, slots=True)
class MeasurementConfig:
    """計測の有効無効と評価窓長（要件 8.4 の材料）。

    Attributes:
        enabled: 計測を有効にするか。無効時は上流の `NullLogger` に委譲する
            想定（design.md「TrackingMetrics / Integration」）。
        window_ms: `effective_points_per_window` の評価窓長（ms）。
            **既定値 600.0 は初期評価候補であり必須性能ではない。** 正で
            なければならない。
        detect_stage: 検出区間の計測ログに使う stage 名。
        track_stage: 追跡区間の計測ログに使う stage 名。
    """

    enabled: bool = True
    window_ms: float = 600.0
    detect_stage: str = "detect"
    track_stage: str = "track"


@dataclass(frozen=True, slots=True)
class TrackingSettings:
    """解決済みの実行時設定（要件 10.5, 12.1, 12.2, 12.5, 12.6）。

    `resolve()` classmethod のみが検証済みの構築の入口である。直接
    コンストラクタで組み立てた `TrackingSettings`（例えばこのクラスの既定値
    のみを使う場合）は `resolve()` が行う起動時検証を経ないため、外部から
    与えられた値を扱う場合は必ず `resolve()` を経由すること。

    Postconditions: 解決後の設定は不変（`frozen=True, slots=True` が入れ子の
        全データクラスに適用されている）であり、処理開始後も変更されない。

    Attributes:
        roi: 処理対象範囲。
        object_model: 対象物の物理モデル。
        detector: 検出方式と方式別パラメータ。
        projection: 逆投影パラメータ。
        tracker: 追跡閾値。
        measurement: 計測の有効無効と評価窓長。
        output_root: 出力・計測結果の保存先ルート（要件 12.6）。版管理対象外
            の場所に置く想定。
    """

    roi: RoiConfig = field(default_factory=RoiConfig)
    object_model: ObjectModelConfig = field(default_factory=ObjectModelConfig)
    detector: DetectorConfig = field(default_factory=DetectorConfig)
    projection: ProjectionConfig = field(default_factory=ProjectionConfig)
    tracker: TrackerConfig = field(default_factory=TrackerConfig)
    measurement: MeasurementConfig = field(default_factory=MeasurementConfig)
    output_root: Path = Path("var/tracking")

    @classmethod
    def resolve(
        cls,
        *,
        file: Path | None,
        env: Mapping[str, str],
        overrides: Mapping[str, object],
    ) -> "TrackingSettings":
        """設定を **CLI 引数 > 環境変数 > 設定ファイル > 既定値** の順で解決する。

        Args:
            file: JSON 設定ファイルのパス。`None` なら設定ファイル層を
                スキップする。存在しない・JSON として解釈できない場合は
                `TrackingConfigError` を送出する。
            env: 環境変数のマッピング（例: `os.environ`）。`STB_FOT_` を
                前置したキーのみを参照する（例: `STB_FOT_TRACKER_MAX_STEP_MM`）。
                未知の `STB_FOT_*` は無視する。
            overrides: CLI 引数など、最優先で適用する値のマッピング。
                キーは `file` / `env` と同じフラットなキー名を使う。未知の
                キーは `TrackingConfigError` を送出する。

        Returns:
            検証済みで不変な `TrackingSettings`。

        Raises:
            TrackingConfigError: 設定値が Preconditions / Invariants を
                満たさない場合（許容倍率の逆転・評価窓長が0以下・距離下限が
                上限以上・外れ値割合が0.5以上など）、設定ファイルが読めない
                ・不正な場合、`file` / `overrides` に未知の設定キーが
                指定された場合。`ValueError` としても捕捉できる。
        """
        values: dict[str, object] = {
            key: _default_value(spec) for key, spec in _FIELD_SPECS.items()
        }

        if file is not None:
            file_values = _load_file(file)
            _apply_layer(values, file_values, layer_name="設定ファイル", strict=True)

        env_values = _extract_env(env)
        _apply_layer(values, env_values, layer_name="環境変数", strict=False)

        _apply_layer(values, overrides, layer_name="CLI引数", strict=True)

        roi = RoiConfig(
            x_px=values["roi_x_px"],  # type: ignore[arg-type]
            y_px=values["roi_y_px"],  # type: ignore[arg-type]
            width_px=values["roi_width_px"],  # type: ignore[arg-type]
            height_px=values["roi_height_px"],  # type: ignore[arg-type]
            z_min_mm=values["roi_z_min_mm"],  # type: ignore[arg-type]
            z_max_mm=values["roi_z_max_mm"],  # type: ignore[arg-type]
        )
        object_model = ObjectModelConfig(
            diameter_mm=values["object_diameter_mm"],  # type: ignore[arg-type]
            min_scale=values["object_min_scale"],  # type: ignore[arg-type]
            max_scale=values["object_max_scale"],  # type: ignore[arg-type]
            min_area_px=values["object_min_area_px"],  # type: ignore[arg-type]
            max_area_px=values["object_max_area_px"],  # type: ignore[arg-type]
        )
        detector = DetectorConfig(
            kind=values["detector_kind"],  # type: ignore[arg-type]
            diff_threshold_mm=values["detector_diff_threshold_mm"],  # type: ignore[arg-type]
            background_frames=values["detector_background_frames"],  # type: ignore[arg-type]
            background_update_rate=values["detector_background_update_rate"],  # type: ignore[arg-type]
            open_kernel_px=values["detector_open_kernel_px"],  # type: ignore[arg-type]
            close_kernel_px=values["detector_close_kernel_px"],  # type: ignore[arg-type]
            max_candidates=values["detector_max_candidates"],  # type: ignore[arg-type]
        )
        projection = ProjectionConfig(
            min_valid_depth_px=values["projection_min_valid_depth_px"],  # type: ignore[arg-type]
            depth_trim_ratio=values["projection_depth_trim_ratio"],  # type: ignore[arg-type]
            max_depth_spread_mm=values["projection_max_depth_spread_mm"],  # type: ignore[arg-type]
        )
        tracker = TrackerConfig(
            max_step_mm=values["tracker_max_step_mm"],  # type: ignore[arg-type]
            max_missing_frames=values["tracker_max_missing_frames"],  # type: ignore[arg-type]
            min_points_to_start=values["tracker_min_points_to_start"],  # type: ignore[arg-type]
            max_points=values["tracker_max_points"],  # type: ignore[arg-type]
        )
        measurement = MeasurementConfig(
            enabled=values["measurement_enabled"],  # type: ignore[arg-type]
            window_ms=values["measurement_window_ms"],  # type: ignore[arg-type]
            detect_stage=values["measurement_detect_stage"],  # type: ignore[arg-type]
            track_stage=values["measurement_track_stage"],  # type: ignore[arg-type]
        )
        settings = cls(
            roi=roi,
            object_model=object_model,
            detector=detector,
            projection=projection,
            tracker=tracker,
            measurement=measurement,
            output_root=values["output_root"],  # type: ignore[arg-type]
        )

        _validate(settings)
        return settings

    def describe(self) -> dict[str, object]:
        """解決済み設定を表示可能な形（`--print-settings`）で返す（要件 12.2）。

        戻り値は JSON 化可能な `dict` であり、各入れ子設定を平坦な
        辞書として含む。加えて `provisional_defaults` キーに、初期評価候補
        である既定値（`diameter_mm` / `kind` / `window_ms`）についての
        注記を含める（要件 12.3。値そのものが既定値かどうかに関わらず、
        常に注記を付す。設定を読む人がこれらの値の由来を都度確認しなくても
        「確定値ではない」ことを見失わないようにするため）。
        """
        return {
            "roi": asdict(self.roi),
            "object_model": asdict(self.object_model),
            "detector": asdict(self.detector),
            "projection": asdict(self.projection),
            "tracker": asdict(self.tracker),
            "measurement": asdict(self.measurement),
            "output_root": str(self.output_root),
            "provisional_defaults": {
                "diameter_mm": (
                    "object_model.diameter_mm は初期評価候補であり、必須性能"
                    "でも達成済み性能でもない（OQ-02 の最終決定ではない）。"
                ),
                "kind": (
                    "detector.kind は初期評価候補であり、必須性能でも"
                    "達成済み性能でもない（OQ-26 の実測比較で確定する）。"
                ),
                "window_ms": (
                    "measurement.window_ms は初期評価候補であり、必須性能"
                    "でも達成済み性能でもない。"
                ),
            },
        }

    def postprocess_fingerprint(self) -> str:
        """後段設定（検出方式を除くパイプライン設定）の同一性を表す指紋。

        検出方式比較（design.md「DetectorComparison」）が「後段設定は全
        方式で同一であること」を実行前に検証するために使う（要件 4.4）。
        対象は `roi` / `object_model` / `projection` / `tracker` /
        `measurement` の5つであり、`detector`（比較対象そのもの）と
        `output_root`（書き出し先。パイプラインの挙動に影響しない）は
        対象から除く（モジュール docstring の設計判断を参照）。

        Returns:
            対象フィールドの正準 JSON 表現に対する SHA-256 の16進文字列。
            同一の対象フィールドを持つ `TrackingSettings` からは常に同一の
            値が得られ、`detector` フィールドのみが異なる設定からも同一の
            値が得られる。
        """
        payload = {
            "roi": asdict(self.roi),
            "object_model": asdict(self.object_model),
            "projection": asdict(self.projection),
            "tracker": asdict(self.tracker),
            "measurement": asdict(self.measurement),
        }
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# キー空間の定義と型変換
# --------------------------------------------------------------------------

_ENV_PREFIX = "STB_FOT_"

_DEFAULT_ROI = RoiConfig()
_DEFAULT_OBJECT_MODEL = ObjectModelConfig()
_DEFAULT_DETECTOR = DetectorConfig()
_DEFAULT_PROJECTION = ProjectionConfig()
_DEFAULT_TRACKER = TrackerConfig()
_DEFAULT_MEASUREMENT = MeasurementConfig()

_TRUE_STRINGS = frozenset({"1", "true", "yes", "on"})
_FALSE_STRINGS = frozenset({"0", "false", "no", "off"})
_NONE_STRINGS = frozenset({"", "none", "null"})


def _coerce_int(raw: object) -> int:
    if isinstance(raw, bool):
        raise TrackingConfigError(f"整数を期待したが真偽値を受け取った: {raw!r}")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError as exc:
            raise TrackingConfigError(f"整数として解釈できない設定値: {raw!r}") from exc
    raise TrackingConfigError(f"整数を期待したが {type(raw).__name__} を受け取った: {raw!r}")


def _coerce_optional_int(raw: object) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, str) and raw.strip().lower() in _NONE_STRINGS:
        return None
    return _coerce_int(raw)


def _coerce_float(raw: object) -> float:
    if isinstance(raw, bool):
        raise TrackingConfigError(f"数値を期待したが真偽値を受け取った: {raw!r}")
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw)
        except ValueError as exc:
            raise TrackingConfigError(f"数値として解釈できない設定値: {raw!r}") from exc
    raise TrackingConfigError(f"数値を期待したが {type(raw).__name__} を受け取った: {raw!r}")


def _coerce_optional_float(raw: object) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, str) and raw.strip().lower() in _NONE_STRINGS:
        return None
    return _coerce_float(raw)


def _coerce_bool(raw: object) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        lowered = raw.strip().lower()
        if lowered in _TRUE_STRINGS:
            return True
        if lowered in _FALSE_STRINGS:
            return False
        raise TrackingConfigError(f"真偽値として解釈できない設定値: {raw!r}")
    raise TrackingConfigError(f"真偽値を期待したが {type(raw).__name__} を受け取った: {raw!r}")


def _coerce_str(raw: object) -> str:
    if isinstance(raw, str):
        return raw
    raise TrackingConfigError(f"文字列を期待したが {type(raw).__name__} を受け取った: {raw!r}")


def _coerce_path(raw: object) -> Path:
    if isinstance(raw, Path):
        return raw
    if isinstance(raw, str):
        return Path(raw)
    raise TrackingConfigError(f"パスを期待したが {type(raw).__name__} を受け取った: {raw!r}")


def _coerce_detector_kind(raw: object) -> DetectorKind:
    if isinstance(raw, DetectorKind):
        return raw
    if isinstance(raw, str):
        try:
            return DetectorKind(raw)
        except ValueError as exc:
            raise TrackingConfigError(f"未知の検出方式: {raw!r}") from exc
    raise TrackingConfigError(
        f"検出方式を期待したが {type(raw).__name__} を受け取った: {raw!r}"
    )


@dataclass(frozen=True, slots=True)
class _FieldSpec:
    """1つの設定キーが対応する（グループ, 属性名, 型変換）。"""

    group: Literal["roi", "object_model", "detector", "projection", "tracker", "measurement", None]
    attr: str
    coerce: Callable[[object], object]


_FIELD_SPECS: dict[str, _FieldSpec] = {
    "roi_x_px": _FieldSpec("roi", "x_px", _coerce_int),
    "roi_y_px": _FieldSpec("roi", "y_px", _coerce_int),
    "roi_width_px": _FieldSpec("roi", "width_px", _coerce_optional_int),
    "roi_height_px": _FieldSpec("roi", "height_px", _coerce_optional_int),
    "roi_z_min_mm": _FieldSpec("roi", "z_min_mm", _coerce_optional_float),
    "roi_z_max_mm": _FieldSpec("roi", "z_max_mm", _coerce_optional_float),
    "object_diameter_mm": _FieldSpec("object_model", "diameter_mm", _coerce_float),
    "object_min_scale": _FieldSpec("object_model", "min_scale", _coerce_float),
    "object_max_scale": _FieldSpec("object_model", "max_scale", _coerce_float),
    "object_min_area_px": _FieldSpec("object_model", "min_area_px", _coerce_int),
    "object_max_area_px": _FieldSpec("object_model", "max_area_px", _coerce_int),
    "detector_kind": _FieldSpec("detector", "kind", _coerce_detector_kind),
    "detector_diff_threshold_mm": _FieldSpec(
        "detector", "diff_threshold_mm", _coerce_float
    ),
    "detector_background_frames": _FieldSpec(
        "detector", "background_frames", _coerce_int
    ),
    "detector_background_update_rate": _FieldSpec(
        "detector", "background_update_rate", _coerce_float
    ),
    "detector_open_kernel_px": _FieldSpec("detector", "open_kernel_px", _coerce_int),
    "detector_close_kernel_px": _FieldSpec("detector", "close_kernel_px", _coerce_int),
    "detector_max_candidates": _FieldSpec("detector", "max_candidates", _coerce_int),
    "projection_min_valid_depth_px": _FieldSpec(
        "projection", "min_valid_depth_px", _coerce_int
    ),
    "projection_depth_trim_ratio": _FieldSpec(
        "projection", "depth_trim_ratio", _coerce_float
    ),
    "projection_max_depth_spread_mm": _FieldSpec(
        "projection", "max_depth_spread_mm", _coerce_float
    ),
    "tracker_max_step_mm": _FieldSpec("tracker", "max_step_mm", _coerce_float),
    "tracker_max_missing_frames": _FieldSpec(
        "tracker", "max_missing_frames", _coerce_int
    ),
    "tracker_min_points_to_start": _FieldSpec(
        "tracker", "min_points_to_start", _coerce_int
    ),
    "tracker_max_points": _FieldSpec("tracker", "max_points", _coerce_int),
    "measurement_enabled": _FieldSpec("measurement", "enabled", _coerce_bool),
    "measurement_window_ms": _FieldSpec("measurement", "window_ms", _coerce_float),
    "measurement_detect_stage": _FieldSpec(
        "measurement", "detect_stage", _coerce_str
    ),
    "measurement_track_stage": _FieldSpec("measurement", "track_stage", _coerce_str),
    "output_root": _FieldSpec(None, "output_root", _coerce_path),
}

_DEFAULT_OBJECTS = {
    "roi": _DEFAULT_ROI,
    "object_model": _DEFAULT_OBJECT_MODEL,
    "detector": _DEFAULT_DETECTOR,
    "projection": _DEFAULT_PROJECTION,
    "tracker": _DEFAULT_TRACKER,
    "measurement": _DEFAULT_MEASUREMENT,
}


def _default_value(spec: _FieldSpec) -> object:
    if spec.group is None:
        return Path("var/tracking") if spec.attr == "output_root" else None
    return getattr(_DEFAULT_OBJECTS[spec.group], spec.attr)


def _extract_env(env: Mapping[str, str]) -> dict[str, str]:
    """既知のキーに対応する `STB_FOT_` 環境変数のみを取り出す。

    未知の `STB_FOT_*` 変数や無関係な環境変数は無視する（環境変数は本
    パッケージが知らない用途にも使われ得るため、file / overrides と異なり
    未知キーでエラーにはしない）。
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
        raise TrackingConfigError(f"設定ファイルを読み込めない: {file}") from exc
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise TrackingConfigError(f"設定ファイルが JSON として解釈できない: {file}") from exc
    if not isinstance(data, dict):
        raise TrackingConfigError(
            f"設定ファイルの最上位は JSON オブジェクトでなければならない: {file}"
        )
    return data


def _apply_layer(
    values: dict[str, object],
    raw_layer: Mapping[str, object],
    *,
    layer_name: str,
    strict: bool,
) -> None:
    for key, raw in raw_layer.items():
        spec = _FIELD_SPECS.get(key)
        if spec is None:
            if strict:
                raise TrackingConfigError(f"未知の設定キー {key!r}（{layer_name}で指定）")
            continue
        values[key] = spec.coerce(raw)


# --------------------------------------------------------------------------
# 起動時検証（Preconditions / Invariants）
# --------------------------------------------------------------------------


def _validate(settings: TrackingSettings) -> None:
    roi = settings.roi
    object_model = settings.object_model
    detector = settings.detector
    projection = settings.projection
    tracker = settings.tracker
    measurement = settings.measurement

    # 「すべての _px は正」（Preconditions）。ただし roi.x_px / roi.y_px は
    # ROI の左上オフセットであり、0 は「フレーム端から開始する」という
    # 正当な既定値そのものである（RoiConfig の既定値が x_px=0, y_px=0）。
    # 文字通り「正」を課すと既定値が拒否される矛盾が生じるため、この2
    # フィールドのみ「非負」に緩め、サイズ・個数を表す _px フィールドには
    # 文字通り「正」を課す（本モジュール docstring に同じ理由を記載）。
    if roi.x_px < 0:
        raise TrackingConfigError(f"roi.x_px は非負でなければならない: {roi.x_px}")
    if roi.y_px < 0:
        raise TrackingConfigError(f"roi.y_px は非負でなければならない: {roi.y_px}")
    if roi.width_px is not None and roi.width_px <= 0:
        raise TrackingConfigError(f"roi.width_px は正でなければならない: {roi.width_px}")
    if roi.height_px is not None and roi.height_px <= 0:
        raise TrackingConfigError(
            f"roi.height_px は正でなければならない: {roi.height_px}"
        )
    if object_model.min_area_px <= 0:
        raise TrackingConfigError(
            f"object_model.min_area_px は正でなければならない: {object_model.min_area_px}"
        )
    if object_model.max_area_px <= 0:
        raise TrackingConfigError(
            f"object_model.max_area_px は正でなければならない: {object_model.max_area_px}"
        )
    if detector.open_kernel_px <= 0:
        raise TrackingConfigError(
            f"detector.open_kernel_px は正でなければならない: {detector.open_kernel_px}"
        )
    if detector.close_kernel_px <= 0:
        raise TrackingConfigError(
            f"detector.close_kernel_px は正でなければならない: {detector.close_kernel_px}"
        )
    if projection.min_valid_depth_px <= 0:
        raise TrackingConfigError(
            "projection.min_valid_depth_px は正でなければならない: "
            f"{projection.min_valid_depth_px}"
        )

    # 距離下限が上限以上（Invariants: z_min_mm < z_max_mm。いずれも指定
    # されている場合のみ比較する）。
    if (
        roi.z_min_mm is not None
        and roi.z_max_mm is not None
        and roi.z_min_mm >= roi.z_max_mm
    ):
        raise TrackingConfigError(
            "roi.z_min_mm は roi.z_max_mm 未満でなければならない: "
            f"z_min_mm={roi.z_min_mm}, z_max_mm={roi.z_max_mm}"
        )

    # 許容倍率の逆転（min_scale < max_scale）。
    if object_model.min_scale >= object_model.max_scale:
        raise TrackingConfigError(
            "object_model.min_scale は max_scale 未満でなければならない: "
            f"min_scale={object_model.min_scale}, max_scale={object_model.max_scale}"
        )

    # 外れ値割合（0.0 <= depth_trim_ratio < 0.5）。
    if not (0.0 <= projection.depth_trim_ratio < 0.5):
        raise TrackingConfigError(
            "projection.depth_trim_ratio は [0.0, 0.5) の範囲でなければならない: "
            f"{projection.depth_trim_ratio}"
        )

    # 欠測許容フレーム数（max_missing_frames >= 0）。
    if tracker.max_missing_frames < 0:
        raise TrackingConfigError(
            "tracker.max_missing_frames は0以上でなければならない: "
            f"{tracker.max_missing_frames}"
        )

    # 評価窓長が0以下（window_ms > 0）。
    if measurement.window_ms <= 0:
        raise TrackingConfigError(
            f"measurement.window_ms は正でなければならない: {measurement.window_ms}"
        )
