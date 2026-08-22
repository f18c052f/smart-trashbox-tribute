"""コマンドライン入口を実装する（design.md「L8-L9: 比較・入口 → CLI」、タスク 7.3）。

`track` / `compare-detectors` / `bench-overhead` / `export` の4サブコマンドを
提供する、本パッケージの最上位の統合点である（依存方向表「9 | cli | 0〜8」
——このモジュールはパッケージ内のどのレイヤも import してよい）。

============================================================================
2系統の設定を分けて解決する（設計判断）
============================================================================

本 CLI は2つの独立した設定を解決する。

1. **`flying_object_tracking.config.TrackingSettings`**（検出・逆投影・
   追跡・計測の設定）。解決順序は **CLI引数 > 環境変数(`STB_FOT_*`) >
   設定ファイル(`--config`) > 既定値**であり、`TrackingSettings.resolve()`
   （タスク 1.4 で確定済み）をそのまま呼ぶ。**本モジュールは解決順序の
   ロジックを再実装しない。**
2. **`sensing_foundation.RuntimeSettings`**（入力元の選択: `--source` /
   `--session` / 解像度・fps）。**入力元の指定は上流の `open_source()` に
   委ね、本パッケージでアダプタを再実装しない**（design.md「CLI /
   Implementation Notes」・要件 1.5）。`RuntimeSettings.resolve()`
   （上流 `sensing_foundation.config`）をそのまま呼び、`STB_SF_*` 環境変数
   による上流自身の解決順序もそのまま活かす。

この2系統を分ける理由: `TrackingSettings.resolve()` の解決順序（要件 12.1）
が要求するのは「検出・追跡そのものの設定」であり、「どの入力元を開くか」は
`sensing_foundation` が既に持つ別の解決順序（同じく CLI>env>file>既定値の
形）に従う。両者を1つのフラットな設定空間に混ぜると、どちらの解決順序を
守っているのか曖昧になる。

============================================================================
未知の検出方式名の拒否（要件 12.1 / 4.2 の材料）
============================================================================

`--detector-kind` は `argparse` の `choices=` で `DetectorKind` の値だけに
制限する。これにより、未知の値を渡すと `argparse` が **`TrackingSettings.
resolve()` を呼ぶ前**（＝どのサブコマンドの処理も始まる前）にエラー終了
する（タスクブリーフが「argparse の choices= 機構を使うのが自然」と明示する
とおり）。`compare-detectors` の `--kinds`（比較対象を絞り込む）は複数値の
カンマ区切りであり `choices=` では表現できないため、`_parse_kinds()` が
`_dispatch()`（＝どのサブコマンドの実I/Oが始まる前）で同様に拒否する。

============================================================================
既定値の非既成事実化（要件 12.3）
============================================================================

`config.py` が「初期評価候補であり必須性能ではない」と明記する既定値
（`object_model.diameter_mm` / `detector.kind` / `measurement.window_ms`）
について、対応する `--help` 文にも同じ注記（`_PROVISIONAL_NOTE`）を含める。

============================================================================
表示機能を実行の必須要件にしない（要件 12.4。画面のない環境で動く）
============================================================================

本モジュールは `argparse` と標準出力への `print()` だけを使う。GUI
ツールキット（`tkinter` 等）や GUI 付き `matplotlib` バックエンドを一切
import しない。`--print-settings` は「解決済み設定を表示できる」機能
（要件 12.2）であり、これを**指定しない**限り実行される他の処理には一切
関与しない——4サブコマンドいずれも、モニタ・ディスプレイの存在を前提とする
コードパスを持たない（画像処理は `opencv-python-headless` extras に乗る、
`detection/mask_ops.py` の既存実装がそもそも `cv2.imshow` 等の GUI API を
呼ばない設計であることに拠る。本タスクはその上に何も追加しない）。

============================================================================
`export` サブコマンドのスコープ（タスク 7.3 / 7.4 の分担）
============================================================================

design.md の File Structure Plan は `export`（点列の書き出し）を CLI 節に
含める一方、tasks.md は本タスクとは別に**タスク 7.4**（本タスクに
`Depends`）として「点列の書き出しを実装する」を切り出し、design.md
「エクスポート形式（開発用）」節が定めるフィールド単位の変換規則（非数・
無限大はその項目を欠測にする等。要件 7.1, 7.6, 12.6）をそちらの責務に
している。

- **タスク 7.3（本ファイルの土台）** は `export` サブコマンドの argparse 面
  （引数・ヘルプ・起動前検証）を完成させ、`run_export()` という差し替え
  可能な1関数にロジックを閉じた。
- **タスク 7.4** が `run_export()` の本体を、`serialize_camera_track()`
  （本ファイル下部）による design.md 準拠の書き出しへ置き換えた。
  `Boundary: CLI` が7.3と同一のため、専用モジュールへ切り出さずこの
  ファイルを拡張する形を採った（design.md はこの書き出しロジックに
  独立したコンポーネント名を与えていない）。argparse 面には触れていない。

============================================================================
`--source simulated` の内部供給関数
============================================================================

`open_source()`（上流 `sensing_foundation.source`）の `SourceKind.SIMULATED`
分岐は `supplier: FrameSupplier` を必須で要求するが、`SimulatedSource`
自身は投擲物理・ノイズ生成を持たない（上流の設計）。`sensing_foundation.cli`
と同じ理由・同じ解決を踏襲し、`_smoke_supplier()`（本モジュール下部）という
決定的な最小生成器を用意する。**投擲物理・リアリズムを一切主張しない**、
CLI がハードウェアも外部供給関数も無い状態で全サブコマンドを実演できること
だけを目的とした値である。

⚠️ 既知の制約（`tasks.md` タスク6.2 実装メモを参照）:
`sensing_foundation.source._build_simulated_profile()` は
`SourceKind.SIMULATED` の `StreamProfile.intrinsics` を常に `None` にする。
そのため `--source simulated` で**候補が1つでも検出されると**
`PointEstimator.estimate()` が `IntrinsicsUnavailableError`
（`TrackingPipeline` の非捕捉例外）を送出する。`_smoke_supplier()` は
既定の `RoiConfig`（`z_min_mm=500.0`〜`z_max_mm=5000.0`）の**外側**に収まる
一定値を返す決定的パターンにすることで、既定設定ではこれを誘発しない
（depth_band は距離帯外のため前景なし。frame_diff/background は値が
フレーム間で変化しないため差分が生じず前景なし）。**本 Spec のテストは
この制約を避けるため、`--source recorded` と `SessionRecorder` で書き出した
合成セッションを主に用いる**（`tests/flying_object_tracking/
test_flying_object_tracking_detector_comparison.py` 等、既存タスクのテスト
と同じ流儀）。

============================================================================
サブコマンドの実体は `run_*()` 関数として切り出す
============================================================================

`main(argv)` は「argv を解析し、`TrackingSettings` / `RuntimeSettings` を
解決し、`run_*()` を呼び、結果を JSON で表示する」という薄い配線のみを行う。
各サブコマンドの本体は `run_track()` / `run_compare_detectors()` /
`run_bench_overhead()` / `run_export()` という独立した関数として実装し、
`main()` からもテストからも（`supplier` の注入を含め）直接呼べるようにする
（`sensing_foundation.cli` と同じ流儀）。
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import os
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal

import numpy as np

from sensing_foundation import (
    CaptureMetrics,
    FrameSource,
    Logger,
    LoggingConfig,
    NullLogger,
    RuntimeSettings,
    SensingConfigError,
    SensingFoundationError,
    SessionClock,
    SourceKind,
    get_logger,
    open_source,
)

from flying_object_tracking.bench.compare import compare_detectors, write_comparison_result
from flying_object_tracking.bench.overhead import OverheadBench, write_overhead_result
from flying_object_tracking.config import TrackingSettings
from flying_object_tracking.errors import TrackingConfigError, TrackingError
from flying_object_tracking.pipeline import TrackingPipeline
from flying_object_tracking.types import CameraTrack, DetectorKind

__all__ = ["main"]

#: `config.py` モジュール docstring・タスク 1.4 が確立している表現を
#: そのまま踏襲する（既定値の非既成事実化。要件 12.3）。
_PROVISIONAL_NOTE = "初期評価候補であり必須性能ではない"

#: `_collect_tracking_overrides()` が `TrackingSettings.resolve(overrides=...)`
#: へ渡すキー集合。`config.py._FIELD_SPECS` のキー名と1対1で一致させる
#: （`sensing_foundation.cli._SETTINGS_KEYS` と同じ技法。ハイフン区切りの
#: CLI フラグ名は argparse がアンダースコアの dest へ自動変換するため、
#: フラグ名とキー名を素直に揃えれば変換テーブルが不要になる）。
_TRACKING_SETTINGS_KEYS: tuple[str, ...] = (
    "roi_x_px",
    "roi_y_px",
    "roi_width_px",
    "roi_height_px",
    "roi_z_min_mm",
    "roi_z_max_mm",
    "object_diameter_mm",
    "object_min_scale",
    "object_max_scale",
    "object_min_area_px",
    "object_max_area_px",
    "detector_kind",
    "detector_diff_threshold_mm",
    "detector_background_frames",
    "detector_background_update_rate",
    "detector_open_kernel_px",
    "detector_close_kernel_px",
    "detector_max_candidates",
    "projection_min_valid_depth_px",
    "projection_depth_trim_ratio",
    "projection_max_depth_spread_mm",
    "tracker_max_step_mm",
    "tracker_max_missing_frames",
    "tracker_min_points_to_start",
    "tracker_max_points",
    "measurement_enabled",
    "measurement_window_ms",
    "measurement_detect_stage",
    "measurement_track_stage",
    "output_root",
)

#: `_collect_runtime_overrides()` が `RuntimeSettings.resolve(overrides=...)`
#: へ渡すキー集合（入力元の選択に必要な最小部分集合。design.md「CLI /
#: Implementation Notes」: 入力元の指定は上流 `open_source()` に委ねる）。
_RUNTIME_SETTINGS_KEYS: tuple[str, ...] = (
    "source",
    "session_path",
    "width_px",
    "height_px",
    "fps",
)

_TOP_LEVEL_DESCRIPTION = (
    "smart-trashbox-tribute flying-object-tracking の CLI 入口。\n"
    "追跡の実行（track）・検出方式の比較（compare-detectors）・計測の有効"
    "無効比較（bench-overhead）・点列の書き出し（export）を提供する。\n\n"
    "検出・追跡設定は CLI引数 > 環境変数(STB_FOT_*) > 設定ファイル(--config)"
    " > 既定値の順で解決する。--print-settings で解決結果のみを表示できる。\n"
    "入力元の選択（--source / --session 等）は上流 sensing_foundation の"
    "open_source() に委ねる（本パッケージは入力元アダプタを持たない）。"
)


# ----------------------------------------------------------------------------
# `--source simulated` 用の内部スモークテスト生成器
# ----------------------------------------------------------------------------


def _smoke_supplier(width_px: int, height_px: int) -> Callable[[int], "np.ndarray | None"]:
    """`--source simulated` に外部供給関数が配線されていない場合にのみ使う、
    最小限の内部生成器（モジュール docstring「`--source simulated` の内部
    供給関数」参照）。

    投擲物理・ノイズモデルを一切持たない、常に同一の一定値パターンを返す
    だけの「スモークテスト用」生成器である。既定の `RoiConfig` の距離帯
    （500.0mm〜5000.0mm）の外側に収まる値（100 生カウント）を使うことで、
    既定設定では `IntrinsicsUnavailableError`（`SourceKind.SIMULATED` が
    `intrinsics=None` を返す上流の制約に起因する非捕捉例外）を誘発しない。
    """

    def supplier(index: int) -> "np.ndarray | None":
        return np.full((height_px, width_px), 100, dtype=np.uint16)

    return supplier


def _resolve_supplier(
    runtime_settings: RuntimeSettings,
    supplier: Callable[[int], "np.ndarray | None"] | None,
) -> Callable[[int], "np.ndarray | None"] | None:
    """`supplier` が未指定かつ `source=SIMULATED` のときのみ内部生成器へ委ねる。"""
    if supplier is not None:
        return supplier
    if runtime_settings.source == SourceKind.SIMULATED:
        return _smoke_supplier(runtime_settings.capture.width_px, runtime_settings.capture.height_px)
    return None


# ----------------------------------------------------------------------------
# 共通ヘルパ
# ----------------------------------------------------------------------------


def _derive_session_id(runtime_settings: RuntimeSettings, *, prefix: str) -> str:
    """`--session` が指定されていればそのディレクトリ名を、無ければ時刻由来の
    識別子を `session_id` として使う。
    """
    if runtime_settings.session_path is not None:
        return Path(runtime_settings.session_path).name
    return f"{prefix}-{int(time.time() * 1000)}"


def _build_logger(tracking_settings: TrackingSettings, clock: SessionClock) -> Logger:
    """`measurement.enabled` に応じたロガーを組み立てる。

    `TrackingMetrics.__init__`（タスク 5.1）は `config.enabled is False` の
    とき、渡された `logger` に関わらず内部で `NullLogger` へ差し替える
    （要件 8.7）。ここで既に `NullLogger()` を渡しておけば、無効時に
    不要な `StructuredLogger`（バックグラウンド書き込みスレッド）を
    開かずに済む。
    """
    if not tracking_settings.measurement.enabled:
        return NullLogger()
    log_path = tracking_settings.output_root / "logs"
    return get_logger(LoggingConfig(enabled=True, path=log_path), clock)


def _json_default(obj: object) -> object:
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"JSON化できない値: {obj!r}")


def _dump_json(payload: object) -> str:
    return json.dumps(payload, default=_json_default, ensure_ascii=False, indent=2)


# ----------------------------------------------------------------------------
# 非有限値のサニタイズ（要件 7.6 / 12.6 の材料。タスク 7.4）
# ----------------------------------------------------------------------------

#: 非有限値を検出したときの内部センチネル。呼び出し元がキー/要素を除く。
#: `sensing_foundation.obslog` の同名パターン（design.md「エクスポート形式
#: （開発用）」節・`prediction_core.record` と方針を揃える）を、
#: 上流の private ヘルパーを import せずに複製したもの。
_DROP = object()


def _sanitize_value(value: object) -> object:
    """非有限 float を検出したら `_DROP` を返す（呼び出し元がキー/要素を除く）。

    dict は再帰的にサニタイズしてキーを落とし、list/tuple も再帰的に
    サニタイズして非有限要素を除く。それ以外の値はそのまま返す。
    `sensing_foundation.obslog._sanitize_value` と同じ規約。
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else _DROP
    if isinstance(value, dict):
        sanitized: dict[str, object] = {}
        for key, inner in value.items():
            sanitized_inner = _sanitize_value(inner)
            if sanitized_inner is not _DROP:
                sanitized[key] = sanitized_inner
        return sanitized
    if isinstance(value, (list, tuple)):
        sanitized_list: list[object] = []
        for inner in value:
            sanitized_inner = _sanitize_value(inner)
            if sanitized_inner is not _DROP:
                sanitized_list.append(sanitized_inner)
        return sanitized_list
    return value


def serialize_camera_track(track: CameraTrack) -> dict[str, object]:
    """`CameraTrack` を design.md「エクスポート形式（開発用）」節が定める
    フィールド単位の辞書へ変換する（タスク 7.4。要件 7.1, 7.6, 12.6）。

    出力キーは同節の表と一対一対応する: `handoff_version` / `frame` /
    `track_id` / `started_t_ms` / `source` / `detector_kind` / `state` /
    `end_reason` / `points[]`（各点は `t_ms` / `x_mm` / `y_mm` / `z_mm` /
    `valid_depth_px` / `depth_spread_mm` / `apparent_diameter_px` /
    `expected_diameter_px` / `frame_index` / `frame_seq` / `gap_before` /
    `rivals`）。`dataclasses.asdict()` をそのまま使わない理由: `TrackPoint`
    は `point: CameraPoint` を入れ子に持つが、この表はフィールドを
    フラットに並べる契約であるため、ここで平坦化する（`CameraPoint` の
    `frame` / `intrinsics_source` はこの表に現れないため出力しない）。

    **非有限値（NaN / Infinity）はその項目だけ欠測にする**（上流
    `sensing_foundation.obslog` と `prediction_core.record` の方針に揃える。
    要件 10.6 「欠測を 0 や推定値で埋めず、欠測として扱う」とも整合する）。
    値を書かず null にもせず、キー自体を辞書から取り除く。これにより
    後段の `json.dumps(..., allow_nan=False)` は常に安全に呼べる
    （非有限値が事前に取り除かれているため `ValueError` を起こさない）。

    **World 座標のフィールドをこの形式に足さない。** `CameraPoint` /
    `CameraTrack`（`types.py`）自体が World 座標のフィールドを持たない
    設計であり（`types.py` モジュール docstring参照）、本関数もそれを
    前提に上記の固定フィールド集合だけを書き出す。将来この関数へ
    `x_world_mm` 等を足したくなっても、それは `world-frame-calibration` /
    `m1-prediction-validation` の責務であり、本 Spec の境界を壊す
    （design.md「Boundary Commitments」）。

    **投擲記録（`prediction_core.ThrowRecord` / `SCHEMA_VERSION`）のスキーマを
    新たに定義せず、本 Spec は投擲記録を一切読み書きしない。** ここで書く
    JSON は design.md が明示する通り「エクスポート形式（開発用）」——下流の
    手動確認用の開発時ダンプであり、`prediction_core` が所有する投擲記録の
    正式スキームとは無関係の別形式である（要件 7.6 / 13.1: 予測コアへの
    変更・依存追加を持たない）。
    """
    points_payload: list[object] = [
        {
            "t_ms": track_point.point.t_ms,
            "x_mm": track_point.point.x_mm,
            "y_mm": track_point.point.y_mm,
            "z_mm": track_point.point.z_mm,
            "valid_depth_px": track_point.point.valid_depth_px,
            "depth_spread_mm": track_point.point.depth_spread_mm,
            "apparent_diameter_px": track_point.point.apparent_diameter_px,
            "expected_diameter_px": track_point.point.expected_diameter_px,
            "frame_index": track_point.frame_index,
            "frame_seq": track_point.frame_seq,
            "gap_before": track_point.gap_before,
            "rivals": track_point.rivals,
        }
        for track_point in track.points
    ]

    payload: dict[str, object] = {
        "handoff_version": track.handoff_version,
        "frame": track.frame,
        "track_id": track.track_id,
        "started_t_ms": track.started_t_ms,
        "source": track.source,
        "detector_kind": track.detector_kind,
        "state": track.state,
        "end_reason": track.end_reason,
        "points": points_payload,
    }

    sanitized = _sanitize_value(payload)
    assert isinstance(sanitized, dict)
    return sanitized


# ----------------------------------------------------------------------------
# サブコマンド本体（`main()` からも、テストからも `supplier` 注入つきで呼べる）
# ----------------------------------------------------------------------------


def _run_pipeline(
    tracking_settings: TrackingSettings,
    runtime_settings: RuntimeSettings,
    *,
    supplier: Callable[[int], "np.ndarray | None"] | None,
    speed: Literal["fast", "realtime"],
) -> tuple[str, CameraTrack, object, object]:
    """`run_track()` / `run_export()` が共有するパイプライン実行本体。

    `track_source()`（`pipeline.py`）ではなく `TrackingPipeline` を直接
    構築する（design.md「TrackingPipeline / Preconditions: `open_source()`
    を呼ぶのは CLI と bench だけ」）。`pipeline.finish()` を呼ぶことで、
    入力が尽きたことを `TrackEndReason.SOURCE_END` として確定できる
    （`track_source()` は内部にパイプラインを隠蔽するため `finish()` を
    呼べない設計になっている——`pipeline.py` モジュール docstring
    「`TrackingPipeline.finish()` は本関数から呼ばない」参照）。

    入力元は `with source:` で開閉する（要件 1.5〜1.7。途中終了・例外時でも
    `source.stop()` が走る）。`run_export()` が `CameraTrack` の実体
    （`dataclasses.asdict()` 後の辞書ではなく）を必要とするため（タスク
    7.4: `serialize_camera_track()` へそのまま渡す）、両サブコマンドの
    共通部分をここへ抽出した。
    """
    session_id = _derive_session_id(runtime_settings, prefix="track")
    clock = SessionClock(session_id=session_id)
    capture_metrics = CaptureMetrics(NullLogger(), clock, sysstat=None)
    logger = _build_logger(tracking_settings, clock)
    pipeline = TrackingPipeline(tracking_settings, logger)

    try:
        source = open_source(
            runtime_settings, capture_metrics, clock=clock, supplier=supplier, speed=speed
        )
        with source:
            for frame in source.frames():
                pipeline.process(frame)
        final_track = pipeline.finish()
    finally:
        logger.close()

    counters = pipeline.counters()
    caught = pipeline.caught_exceptions()
    return session_id, final_track, counters, caught


def run_track(
    tracking_settings: TrackingSettings,
    runtime_settings: RuntimeSettings,
    *,
    supplier: Callable[[int], "np.ndarray | None"] | None = None,
    speed: Literal["fast", "realtime"] = "fast",
) -> dict[str, object]:
    """`track` サブコマンドの本体。入力元から追跡を実行し、点列と統計を返す。"""
    session_id, final_track, counters, caught = _run_pipeline(
        tracking_settings, runtime_settings, supplier=supplier, speed=speed
    )

    return {
        "session_id": session_id,
        "track": dataclasses.asdict(final_track),
        "counters": dataclasses.asdict(counters),
        "caught_exceptions": {"total": caught.total, "by_type": dict(caught.by_type)},
    }


def run_export(
    tracking_settings: TrackingSettings,
    runtime_settings: RuntimeSettings,
    *,
    supplier: Callable[[int], "np.ndarray | None"] | None = None,
    speed: Literal["fast", "realtime"] = "fast",
) -> dict[str, object]:
    """`export` サブコマンドの本体。点列を JSON として書き出す（下流の手動確認用）。

    書き出す先は `tracking_settings.output_root`（要件 12.6: 出力先を
    コード外から与えられる。CLI では `--output-root` / `STB_FOT_OUTPUT_ROOT`
    / `--config` 経由で上書きできる——`TrackingSettings.resolve()` の解決順序
    をそのまま使う）。ファイル名は design.md が定める
    `track-<session_id>-<track_id>.json`。

    実際のフィールド単位の変換（非有限値の欠測化・World 座標フィールドの
    非追加・投擲記録スキーマとの非関係）は `serialize_camera_track()`
    （本ファイル上部、タスク 7.4）に委ねる。
    """
    session_id, final_track, counters, _caught = _run_pipeline(
        tracking_settings, runtime_settings, supplier=supplier, speed=speed
    )
    track_payload = serialize_camera_track(final_track)

    output_root = tracking_settings.output_root
    output_root.mkdir(parents=True, exist_ok=True)
    path = output_root / f"track-{session_id}-{final_track.track_id}.json"
    path.write_text(
        json.dumps(track_payload, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    return {
        "output_path": str(path),
        "track": track_payload,
        "counters": dataclasses.asdict(counters),
    }


def run_compare_detectors(
    tracking_settings: TrackingSettings,
    runtime_settings: RuntimeSettings,
    *,
    kinds: Sequence[DetectorKind] | None = None,
    supplier: Callable[[int], "np.ndarray | None"] | None = None,
    speed: Literal["fast", "realtime"] = "fast",
) -> dict[str, object]:
    """`compare-detectors` サブコマンドの本体。3方式の比較と判定規則を適用する（OQ-26）。

    `bench/compare.py`（タスク 7.1）の `compare_detectors()` /
    `write_comparison_result()` をそのまま呼ぶ。`open_source()` を方式ごとに
    独立に呼べるよう、`_open()` ファクトリを毎回新しい `SessionClock` /
    `CaptureMetrics` で組み立てる（`compare_detectors()` の契約: 「`len(
    settings_by_kind)` 回呼ばれる、同一セッションを最初から辿る新規
    `FrameSource` を返すファクトリ」）。
    """
    resolved_kinds = tuple(kinds) if kinds else tuple(DetectorKind)
    settings_by_kind = {
        kind: dataclasses.replace(
            tracking_settings, detector=dataclasses.replace(tracking_settings.detector, kind=kind)
        )
        for kind in resolved_kinds
    }

    session_id = _derive_session_id(runtime_settings, prefix="compare")
    call_counter = {"n": 0}

    def _open() -> FrameSource:
        call_counter["n"] += 1
        clock = SessionClock(session_id=f"{session_id}-compare-{call_counter['n']}")
        metrics = CaptureMetrics(NullLogger(), clock, sysstat=None)
        return open_source(runtime_settings, metrics, clock=clock, supplier=supplier, speed=speed)

    result = compare_detectors(session_id, _open, settings_by_kind)
    path = write_comparison_result(result, tracking_settings.output_root, session_id)
    payload = json.loads(path.read_text(encoding="utf-8"))

    return {"output_path": str(path), "result": payload}


def run_bench_overhead(
    tracking_settings: TrackingSettings,
    runtime_settings: RuntimeSettings,
    *,
    cycles: int = 3,
    supplier: Callable[[int], "np.ndarray | None"] | None = None,
    speed: Literal["fast", "realtime"] = "fast",
) -> dict[str, object]:
    """`bench-overhead` サブコマンドの本体。計測 ON/OFF 比較（要件 8.8）。

    `OverheadBench.run()`（`bench/overhead.py`、タスク 7.2）は
    `Sequence[CaptureFrame]` を受け取る契約のため、入力元を一度だけ開いて
    フレーム列をメモリへ読み切ってから渡す。
    """
    session_id = _derive_session_id(runtime_settings, prefix="overhead")
    clock = SessionClock(session_id=f"{session_id}-frames")
    metrics = CaptureMetrics(NullLogger(), clock, sysstat=None)
    source = open_source(runtime_settings, metrics, clock=clock, supplier=supplier, speed=speed)
    with source:
        frames = list(source.frames())

    bench = OverheadBench(cycles=cycles)
    work_dir = tracking_settings.output_root / "bench-overhead-work"
    report = bench.run(frames, tracking_settings, work_dir=work_dir)
    path = write_overhead_result(report, tracking_settings.output_root, session_id)
    payload = json.loads(path.read_text(encoding="utf-8"))

    return {"output_path": str(path), "result": payload}


# ----------------------------------------------------------------------------
# argparse 構築
# ----------------------------------------------------------------------------


def _build_common_parser() -> argparse.ArgumentParser:
    """全サブコマンドが継承する、検出・追跡設定＋入力元選択のフラグ群。"""
    parser = argparse.ArgumentParser(add_help=False)

    settings_group = parser.add_argument_group(
        "検出・追跡設定（解決順序: CLI引数 > 環境変数(STB_FOT_*) > 設定ファイル(--config) > 既定値）"
    )
    settings_group.add_argument(
        "--config", metavar="PATH", default=None, help="TrackingSettings 用の JSON 設定ファイルのパス。"
    )
    settings_group.add_argument(
        "--roi-x-px", type=int, default=None, help="処理対象矩形の左上 x 座標（画素）。"
    )
    settings_group.add_argument(
        "--roi-y-px", type=int, default=None, help="処理対象矩形の左上 y 座標（画素）。"
    )
    settings_group.add_argument(
        "--roi-width-px",
        type=int,
        default=None,
        help="処理対象矩形の幅（画素）。未指定はフレーム全体を対象とする。",
    )
    settings_group.add_argument(
        "--roi-height-px",
        type=int,
        default=None,
        help="処理対象矩形の高さ（画素）。未指定はフレーム全体を対象とする。",
    )
    settings_group.add_argument(
        "--roi-z-min-mm",
        type=float,
        default=None,
        help=f"処理対象とする距離の下限（mm）。既定 500.0 は{_PROVISIONAL_NOTE}。",
    )
    settings_group.add_argument(
        "--roi-z-max-mm",
        type=float,
        default=None,
        help=f"処理対象とする距離の上限（mm）。既定 5000.0 は{_PROVISIONAL_NOTE}。",
    )
    settings_group.add_argument(
        "--object-diameter-mm",
        type=float,
        default=None,
        help=(
            f"対象物の想定直径（mm）。既定 65.0（空き缶を想定）は{_PROVISIONAL_NOTE}"
            "（OQ-02 の最終決定ではない）。"
        ),
    )
    settings_group.add_argument(
        "--object-min-scale", type=float, default=None, help="期待画素直径に対する許容下限倍率。"
    )
    settings_group.add_argument(
        "--object-max-scale", type=float, default=None, help="期待画素直径に対する許容上限倍率。"
    )
    settings_group.add_argument(
        "--object-min-area-px",
        type=int,
        default=None,
        help="距離非依存の粗いふるいに使う最小面積（画素数）。",
    )
    settings_group.add_argument(
        "--object-max-area-px",
        type=int,
        default=None,
        help="距離非依存の粗いふるいに使う最大面積（画素数）。",
    )
    settings_group.add_argument(
        "--detector-kind",
        choices=[str(kind) for kind in DetectorKind],
        default=None,
        help=(
            "前景検出方式。未知の値は起動前に拒否する（argparse choices）。"
            f"既定 frame_diff は{_PROVISIONAL_NOTE}（OQ-26 の実測比較で確定する）。"
        ),
    )
    settings_group.add_argument(
        "--detector-diff-threshold-mm",
        type=float,
        default=None,
        help="frame_diff / background 方式の前景判定閾値（mm）。",
    )
    settings_group.add_argument(
        "--detector-background-frames",
        type=int,
        default=None,
        help="background 方式の背景モデル初期化に使うフレーム数。",
    )
    settings_group.add_argument(
        "--detector-background-update-rate",
        type=float,
        default=None,
        help="background 方式の背景モデル更新率（既定 0.0 = 更新しない）。",
    )
    settings_group.add_argument(
        "--detector-open-kernel-px", type=int, default=None, help="マスクの開処理カーネルサイズ（画素）。"
    )
    settings_group.add_argument(
        "--detector-close-kernel-px", type=int, default=None, help="マスクの閉処理カーネルサイズ（画素）。"
    )
    settings_group.add_argument(
        "--detector-max-candidates",
        type=int,
        default=None,
        help="1フレームあたりに採用する候補数の上限。",
    )
    settings_group.add_argument(
        "--projection-min-valid-depth-px",
        type=int,
        default=None,
        help="3D位置化に必要な有効 Depth 画素数の下限。",
    )
    settings_group.add_argument(
        "--projection-depth-trim-ratio",
        type=float,
        default=None,
        help="代表値算出時に外れ値として捨てる割合（[0.0, 0.5)）。",
    )
    settings_group.add_argument(
        "--projection-max-depth-spread-mm",
        type=float,
        default=None,
        help="候補領域内の Depth ばらつきの許容上限（mm）。",
    )
    settings_group.add_argument(
        "--tracker-max-step-mm",
        type=float,
        default=None,
        help="1フレーム間で許容する3D位置の移動量上限（mm）。",
    )
    settings_group.add_argument(
        "--tracker-max-missing-frames",
        type=int,
        default=None,
        help="追跡を打ち切るまでに許容する欠測フレーム数。",
    )
    settings_group.add_argument(
        "--tracker-min-points-to-start",
        type=int,
        default=None,
        help="追跡を開始したとみなすために必要な最小点数。",
    )
    settings_group.add_argument(
        "--tracker-max-points", type=int, default=None, help="1つの追跡が持てる点数の上限。"
    )
    settings_group.add_argument(
        "--measurement-enabled",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="detect/track 区間の計測を有効にするか（既定: 有効）。",
    )
    settings_group.add_argument(
        "--measurement-window-ms",
        type=float,
        default=None,
        help=f"effective_points_per_window の評価窓長（ms）。既定 600.0 は{_PROVISIONAL_NOTE}。",
    )
    settings_group.add_argument(
        "--measurement-detect-stage", type=str, default=None, help="検出区間の計測ログに使う stage 名。"
    )
    settings_group.add_argument(
        "--measurement-track-stage", type=str, default=None, help="追跡区間の計測ログに使う stage 名。"
    )
    settings_group.add_argument(
        "--output-root", type=str, default=None, help="出力・計測結果の保存先ルート。"
    )
    settings_group.add_argument(
        "--print-settings",
        action="store_true",
        default=False,
        help="解決済みの TrackingSettings を JSON で表示し、サブコマンドの本処理を実行せずに終了する。",
    )

    source_group = parser.add_argument_group(
        "入力元（上流 sensing_foundation.open_source() へ委譲する。本パッケージはアダプタを持たない）"
    )
    source_group.add_argument(
        "--source",
        choices=["live", "recorded", "simulated"],
        default=None,
        help="入力元の種別（既定 live）。",
    )
    source_group.add_argument(
        "--session",
        dest="session_path",
        metavar="PATH",
        default=None,
        help="再生対象のセッションディレクトリ（--source recorded で必須）。",
    )
    source_group.add_argument(
        "--width-px", type=int, default=None, help="Depth ストリームの幅（px。--source simulated で使用）。"
    )
    source_group.add_argument(
        "--height-px",
        type=int,
        default=None,
        help="Depth ストリームの高さ（px。--source simulated で使用）。",
    )
    source_group.add_argument(
        "--fps", type=int, default=None, help="要求フレームレート（fps。--source simulated で使用）。"
    )
    source_group.add_argument(
        "--speed",
        choices=["fast", "realtime"],
        default="fast",
        help="--source recorded のときのみ意味を持つ再生速度。",
    )
    return parser


def _build_parser() -> argparse.ArgumentParser:
    common = _build_common_parser()
    parser = argparse.ArgumentParser(
        prog="flying-object-tracking",
        description=_TOP_LEVEL_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "track", parents=[common], help="入力元から追跡を実行し、点列と統計を出力する。"
    )

    compare_parser = subparsers.add_parser(
        "compare-detectors", parents=[common], help="3方式の比較と判定規則の適用（OQ-26）。"
    )
    compare_parser.add_argument(
        "--kinds",
        default=None,
        help=(
            "比較対象の検出方式のカンマ区切りリスト（既定: 3方式すべて）。"
            "未知の値は起動前に拒否する。"
        ),
    )

    overhead_parser = subparsers.add_parser("bench-overhead", parents=[common], help="計測 ON/OFF 比較。")
    overhead_parser.add_argument(
        "--cycles", type=int, default=3, help="(measurement_off, measurement_on) 1巡の繰り返し回数。"
    )

    subparsers.add_parser(
        "export", parents=[common], help="点列を JSON として書き出す（下流の手動確認用）。"
    )

    return parser


def _collect_tracking_overrides(args: argparse.Namespace) -> dict[str, object]:
    overrides: dict[str, object] = {}
    for key in _TRACKING_SETTINGS_KEYS:
        value = getattr(args, key, None)
        if value is not None:
            overrides[key] = value
    return overrides


def _collect_runtime_overrides(args: argparse.Namespace) -> dict[str, object]:
    overrides: dict[str, object] = {}
    for key in _RUNTIME_SETTINGS_KEYS:
        value = getattr(args, key, None)
        if value is not None:
            overrides[key] = value
    return overrides


def _resolve_tracking_settings(args: argparse.Namespace) -> TrackingSettings:
    overrides = _collect_tracking_overrides(args)
    file_path = Path(args.config) if args.config else None
    return TrackingSettings.resolve(file=file_path, env=os.environ, overrides=overrides)


def _resolve_runtime_settings(args: argparse.Namespace) -> RuntimeSettings:
    overrides = _collect_runtime_overrides(args)
    return RuntimeSettings.resolve(file=None, env=os.environ, overrides=overrides)


def _parse_kinds(spec: str | None) -> tuple[DetectorKind, ...] | None:
    """`--kinds` のカンマ区切り仕様を解析する。未知の値は起動前に拒否する。"""
    if spec is None:
        return None
    kinds: list[DetectorKind] = []
    for token in (t.strip() for t in spec.split(",")):
        if not token:
            continue
        try:
            kinds.append(DetectorKind(token))
        except ValueError as exc:
            raise TrackingConfigError(f"未知の検出方式: {token!r}") from exc
    if not kinds:
        raise TrackingConfigError("--kinds に少なくとも1つの検出方式を指定する必要がある")
    return tuple(kinds)


def _dispatch(
    args: argparse.Namespace, tracking_settings: TrackingSettings, runtime_settings: RuntimeSettings
) -> dict[str, object]:
    supplier = _resolve_supplier(runtime_settings, None)
    speed = args.speed

    if args.command == "track":
        return run_track(tracking_settings, runtime_settings, supplier=supplier, speed=speed)
    if args.command == "export":
        return run_export(tracking_settings, runtime_settings, supplier=supplier, speed=speed)
    if args.command == "compare-detectors":
        kinds = _parse_kinds(args.kinds)
        return run_compare_detectors(
            tracking_settings, runtime_settings, kinds=kinds, supplier=supplier, speed=speed
        )
    if args.command == "bench-overhead":
        return run_bench_overhead(
            tracking_settings, runtime_settings, cycles=args.cycles, supplier=supplier, speed=speed
        )
    raise AssertionError(f"未知のサブコマンド: {args.command}")  # argparse の choices で到達しないはず


def main(argv: Sequence[str] | None = None) -> int:
    """CLI エントリポイント。`python -m flying_object_tracking.cli` からも呼ばれる。"""
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        tracking_settings = _resolve_tracking_settings(args)
    except TrackingConfigError as exc:
        print(f"設定エラー: {exc}", file=sys.stderr)
        return 2

    if args.print_settings:
        print(_dump_json(tracking_settings.describe()))
        return 0

    try:
        runtime_settings = _resolve_runtime_settings(args)
    except SensingConfigError as exc:
        print(f"入力元設定エラー: {exc}", file=sys.stderr)
        return 2

    try:
        result = _dispatch(args, tracking_settings, runtime_settings)
    except TrackingConfigError as exc:
        print(f"設定エラー: {exc}", file=sys.stderr)
        return 2
    except SensingConfigError as exc:
        print(f"入力元設定エラー: {exc}", file=sys.stderr)
        return 2
    except (TrackingError, SensingFoundationError) as exc:
        print(f"実行エラー: {exc}", file=sys.stderr)
        return 1

    print(_dump_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
