"""コマンドライン入口（design.md「L6-L9: 永続化・検証・入口」CLI コンポーネント /
tasks.md タスク 6.4 / 要件 4.1, 7.1, 7.3, 8.5, 10.3）。

`calibrate`（収集 → 平面推定 → マーカー観測 → frame 確立 → 保存）・
`verify`（保存済み結果 + 検証点の観測 → 誤差レポートと合否）・`show`
（保存済み結果の要約表示と整合性検査）・`compare`（2結果の差分）の4サブ
コマンドを提供する、本 Spec の最上位の統合点である（design.md 依存方向表
「9 | cli | 0〜8」——このモジュールはパッケージ内のどの層も import してよい）。

**本モジュールは `sensing_foundation` を import してよい3モジュールの1つ**
（`upstream.py` / `deproject.py` と並び、design.md 依存方向表が明示する例外）
であり、ここでの用途は**入力元と設定の解決**（`sensing_foundation.
RuntimeSettings` / `open_source`）に限る。取得ロジックそのもの（フレーム
収集・平均化・型写像）は再実装せず、`world_frame_calibration.upstream.
collect_depth` へ委譲する（要件 3.8, 8.1 の踏襲）。

============================================================================
オーケストレーションの順序（design.md「System Flows」の2つの図を正とする）
============================================================================

`calibrate` は「収集 → 平面推定 → マーカー観測(origin) → マーカー観測
(x_axis) → frame 確立 → 保存」を**各段の失敗でその場停止**しながら順に実行
する（design.md sequenceDiagram「キャリブレーション（calibrate）」）。
**検証はここでは実行しない**——保存直後の結果は常に
`VerificationState.NOT_VERIFIED` のままである（design.md「フロー上の決定」:
「保存時点では検証が未実施であり、結果はその状態を保持する」）。後続の段を
「とりあえず」実行することはしない（同フロー図の注記）。

`verify` は design.md flowchart「検証（verify）」の順序をそのまま実装する:
結果を読む → 形式版検査（`load_calibration` 内部） → 現在の設定との一致
検査（`check_compatibility`） → 検証点ごとに観測 → 独立点の有無検査
（`evaluate_verification_points` 内部） → World 座標へ変換し誤差算出 →
バイアス・ばらつき・距離帯 → 許容値があれば合否判定 → レポート保存・結果
への関連付け。

============================================================================
`verify` の終了コード契約（本タスクで最も安全性に関わる決定）
============================================================================

`verify.Verdict` の3値と CLI 終了コードの対応:

    NOT_JUDGED（許容値が与えられていない） -> 終了コード 0（成功）
    PASS（許容値を満たした）               -> 終了コード 0（成功）
    FAIL（許容値を満たさなかった）          -> 終了コード 0 以外

**「判定していないこと」と「不合格」を区別する**（design.md CLI
「Validation」/ tasks.md タスク 6.4）。許容値が無い実行を自動化スクリプトが
「合格」とも「不合格」とも誤認しないよう、NOT_JUDGED は明示的に成功として
扱う。実測値そのものは `Verdict` の値に関わらず常にレポートへ出力される
（要件 4.7 / `tech.md` 開発標準1）。

============================================================================
ハードウェア非依存の実現方法: `source` / `logger` 注入点
============================================================================

`sensing_foundation.open_source()` の `SourceKind.SIMULATED` 分岐は
`StreamProfile.intrinsics` を常に `None` で構築する
（`sensing_foundation.source._build_simulated_profile` の設計判断: 合成
入力にはカメラ内部パラメータという概念自体が存在しない）。しかし本 Spec の
`upstream.to_intrinsics` は `profile.intrinsics is None` を
`CalibrationConfigError` として拒否する（要件 8.4: 内部パラメータを独自の
固定値で埋めない）ため、**`--source simulated` を経由する経路は本 Spec の
実測パイプラインを一切通せない**。

したがって本モジュールの `run_calibrate` / `run_verify` / `run_show` は
`source: FrameSource | None` というテスト専用の注入点を持つ
（`sensing_foundation.cli` が `run_capture` / `run_record` に対して行う
`supplier` 注入と同じ考え方——「argv 経由の `main()` では表現できない注入」
という同モジュールの docstring の注記も踏襲する）。与えられれば
`sensing_foundation.open_source()` を一切呼ばず、そのまま
`upstream.collect_depth` へ渡す。これにより、実機・SDK・記録済みセッション
のいずれも無い環境で、`tests/world_frame_calibration/synthetic.py` が生成
した合成 Depth を積んだ手製の `FrameSource` 実装（プロトコルを構造的に
満たすダブル）だけで4サブコマンドを最後まで駆動できる（要件 8.5, 9.2）。
同じ理由で `logger: Logger | None` も注入点として提供し、構造化ログの
段階別イベント（要件 10.1〜10.3）を実際の I/O・スレッドを持つ
`StructuredLogger` を使わずに記録専用ダブルで確認できるようにする
（`test_world_frame_calibration_upstream.py` の `_SpyLogger` と同じ設計）。

`main(argv, *, source=None, logger=None)` もこの2引数をそのまま素通しする
——`argv` には現れない、`main()` 自身のテスト専用キーワード引数である。

============================================================================
段階別ロギング: `timed()` ではなく手動計測 + `stage_logger().emit()` を使う
============================================================================

`upstream.timed(logger, event, **data)` は `**data` をコンテキストマネージャ
への進入時点で固定するため、`estimate_floor_plane` などの**戻り値**
（`PlaneQuality` の内点率・残差など）を送出データへ含めることができない。
そのため本モジュールは `time.perf_counter()` による手動計測 +
`upstream.stage_logger(logger).emit(event, duration_ms=..., **quality)` を
使う（`_run_timed_stage` に集約）。基盤・形式・送出機構そのもの
（`stage_logger` 自身の実装）は再実装せず、`upstream.py` のプリミティブを
そのまま呼ぶ（要件 10.2 の踏襲）。失敗時も `failure` イベントを送出してから
例外を re-raise する（design.md Monitoring「成功時しかログが残らない状態を
作らない」/ 要件 10.1）。ロギングが無効（`logger` が `None` または上流の
無効な `NullLogger`）なら、計測値の算出そのものを行わない（要件 10.5。
`upstream.collect_depth` が採る方針と同じ）。

============================================================================
`--help` の必須文言
============================================================================

`tech.md` 開発標準1（未実測の数値を合否条件にしない）に基づき、**既定の
下限・許容値はすべて暫定であり実測で見直す**旨をトップレベルおよび各サブ
コマンドの `--help` に明記する（`_HELP_EPILOGUE` を参照）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path

from sensing_foundation import (
    CaptureMetrics,
    FrameSource,
    Logger,
    RuntimeSettings,
    SensingConfigError,
    SensingFoundationError,
    SessionClock,
    Sysstat,
    get_logger,
    open_source,
)

from world_frame_calibration import anchors as anchors_module
from world_frame_calibration import frame as frame_module
from world_frame_calibration import plan as plan_module
from world_frame_calibration import plane as plane_module
from world_frame_calibration import report as report_module
from world_frame_calibration import result as result_module
from world_frame_calibration import upstream
from world_frame_calibration import verify as verify_module
from world_frame_calibration.errors import (
    CalibrationConfigError,
    CalibrationFailure,
    WorldFrameCalibrationError,
)
from world_frame_calibration.plan import CalibrationPlan
from world_frame_calibration.result import CalibrationResult
from world_frame_calibration.types import AnchorRole, DepthImage, ToleranceSpec
from world_frame_calibration.verify import Verdict, VerificationReport

__all__ = ["main"]

_HELP_EPILOGUE = (
    "既定の下限・許容値はすべて暫定であり実測で見直す"
    "（tech.md 開発標準1: 未実測の数値を合否条件にしない）。"
)


# ---------------------------------------------------------------------------
# セッション・ロギング共通ヘルパ
# ---------------------------------------------------------------------------


def _new_session_id() -> str:
    """CLI 実行ごとに一意なセッション識別子を作る（ログファイル名などに使う）。"""
    return f"wfc-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"


def _measuring(logger: Logger | None) -> bool:
    return logger is not None and logger.enabled


def _run_timed_stage(
    logger: Logger | None,
    event: str,
    fn: Callable[[], object],
    data_fn: Callable[[object], dict[str, object]],
) -> object:
    """`fn()` を実行し、ロギングが有効なら `event` を `duration_ms` 付きで
    送出する（`data_fn(結果)` が付随データを組み立てる）。`CalibrationFailure`
    が送出された場合は `failure` イベントを送ってから re-raise する
    （モジュール docstring「段階別ロギング」参照）。
    """
    measuring = _measuring(logger)
    start = time.perf_counter() if measuring else None
    stage = upstream.stage_logger(logger)
    try:
        result = fn()
    except CalibrationFailure as exc:
        if measuring:
            stage.emit(
                "failure",
                reason=exc.reason.value,
                detail=exc.detail,
                context=dict(exc.context),
            )
        raise
    if measuring:
        assert start is not None
        data = dict(data_fn(result))
        data["duration_ms"] = (time.perf_counter() - start) * 1000.0
        stage.emit(event, **data)
    return result


def _run_plane_fit(depth_image: DepthImage, plan: CalibrationPlan, logger: Logger | None):
    return _run_timed_stage(
        logger,
        "plane_fit",
        lambda: plane_module.estimate_floor_plane(depth_image, plan.floor_region, plan.limits),
        lambda plane_result: {
            "points_considered": plane_result.quality.points_considered,
            "inlier_count": plane_result.quality.inlier_count,
            "inlier_ratio": plane_result.quality.inlier_ratio,
            "residual_rms_mm": plane_result.quality.residual_rms_mm,
            "incidence_angle_deg": plane_result.quality.incidence_angle_deg,
            "rng_seed": plane_result.quality.rng_seed,
        },
    )


def _run_anchor_observe(depth_image, plane_result, spec, role, limits, logger: Logger | None):
    return _run_timed_stage(
        logger,
        "anchor_observe",
        lambda: anchors_module.observe_anchor(depth_image, plane_result, spec, role, limits),
        lambda obs: {
            "label": obs.label,
            "role": role.value,
            "sample_count": obs.sample_count,
            "spread_mm": obs.spread_mm,
            "height_above_plane_mm": obs.height_above_plane_mm,
        },
    )


def _run_frame_build(plane_result, origin_obs, x_axis_obs, limits, logger: Logger | None):
    return _run_timed_stage(
        logger,
        "frame_build",
        lambda: frame_module.build_world_frame(plane_result, origin_obs, x_axis_obs, limits),
        lambda establishment: {
            "baseline_mm": establishment.geometry.baseline_mm,
            "yaw_sensitivity_deg_per_mm": establishment.geometry.yaw_sensitivity_deg_per_mm,
        },
    )


def _run_verify_stage(
    result: CalibrationResult,
    observations,
    *,
    expected_baseline_mm: float | None,
    tolerance: ToleranceSpec | None,
    logger: Logger | None,
) -> VerificationReport:
    return _run_timed_stage(
        logger,
        "verify",
        lambda: verify_module.verify_calibration(
            result,
            observations,
            expected_baseline_mm=expected_baseline_mm,
            tolerance=tolerance,
        ),
        lambda report: {
            "independent_point_count": report.independent_point_count,
            "max_error_norm_mm": report.max_error_norm_mm,
            "bias_mm": list(report.bias_mm),
            "verdict": report.verdict.value,
            "tolerance_provisional": tolerance.provisional if tolerance is not None else None,
        },
    )


def _collect_depth_image(
    settings: RuntimeSettings,
    *,
    frame_count: int,
    logger: Logger | None,
    source: FrameSource | None = None,
) -> DepthImage:
    """現在の入力元から `frame_count` 枚を収集して `DepthImage` を返す。

    `source` が与えられればそれをそのまま使い、`sensing_foundation.
    open_source()` は一切呼ばない（モジュール docstring「ハードウェア非依存
    の実現方法」参照）。`source is None` のときのみ、`settings` から実際の
    入力元を開く本番経路を通る。
    """
    if source is not None:
        with source:
            return upstream.collect_depth(source, frame_count=frame_count, logger=logger)

    clock = SessionClock(session_id=_new_session_id())
    metrics = CaptureMetrics(logger if logger is not None else get_logger(settings.logging, clock), clock, Sysstat())
    opened = open_source(settings, metrics, clock=clock)
    with opened:
        return upstream.collect_depth(opened, frame_count=frame_count, logger=logger)


# ---------------------------------------------------------------------------
# サブコマンド本体（`main()` からも、テストからも直接呼べる）
# ---------------------------------------------------------------------------


def run_calibrate(
    settings: RuntimeSettings,
    plan: CalibrationPlan,
    *,
    frame_count: int,
    out: Path | None = None,
    source: FrameSource | None = None,
    logger: Logger | None = None,
) -> tuple[CalibrationResult, Path]:
    """`calibrate` サブコマンドの本体（design.md sequenceDiagram
    「キャリブレーション（calibrate）」をそのまま実装する）。

    収集 → 平面推定 → マーカー観測（origin） → マーカー観測（x_axis） →
    frame 確立 → 保存、の順に実行し、各段の失敗でその場停止する。**検証は
    実行しない**——返る結果は常に `VerificationState.NOT_VERIFIED`。

    Returns:
        組み立てて保存した `CalibrationResult` と、実際の保存先パス。
    """
    clock = SessionClock(session_id=_new_session_id())
    effective_logger = logger if logger is not None else get_logger(settings.logging, clock)
    try:
        depth_image = _collect_depth_image(
            settings, frame_count=frame_count, logger=effective_logger, source=source
        )
        plane_result = _run_plane_fit(depth_image, plan, effective_logger)
        origin_obs = _run_anchor_observe(
            depth_image, plane_result, plan.origin_anchor, AnchorRole.ORIGIN, plan.limits, effective_logger
        )
        x_axis_obs = _run_anchor_observe(
            depth_image, plane_result, plan.x_axis_anchor, AnchorRole.X_AXIS, plan.limits, effective_logger
        )
        establishment = _run_frame_build(
            plane_result, origin_obs, x_axis_obs, plan.limits, effective_logger
        )
    finally:
        effective_logger.close()

    session_path = str(settings.session_path) if settings.session_path is not None else None
    result = result_module.assemble_calibration_result(
        establishment,
        source_kind=depth_image.source_kind,
        session_path=session_path,
        signature=depth_image.signature,
        intrinsics=depth_image.intrinsics,
        plan_digest=plan_module._plan_to_dict(plan),  # noqa: SLF001 -- 単一の直列化真実源を再利用する
        notes=plan.notes,
    )
    saved_path = (
        out if out is not None else result_module._DEFAULT_OUTPUT_DIR / f"{result.calibration_id}.json"  # noqa: SLF001
    )
    result_module.save_calibration(result, path=saved_path)
    return result, saved_path


def run_verify(
    settings: RuntimeSettings,
    plan: CalibrationPlan,
    calibration_path: Path,
    *,
    frame_count: int,
    tolerance: ToleranceSpec | None,
    report_out: Path | None = None,
    source: FrameSource | None = None,
    logger: Logger | None = None,
) -> tuple[VerificationReport, CalibrationResult, Path]:
    """`verify` サブコマンドの本体（design.md flowchart「検証（verify）」を
    そのまま実装する）。

    保存済み結果を読み、現在の入力元との整合性を検査し、計画ファイルの
    検証点を観測して誤差レポートを組み立てる。レポートはファイルへ保存し、
    その要約を結果へ関連付けて元のパスへ再保存する。

    `tolerance` が `None` の場合は `plan.tolerance`（計画ファイルの許容値。
    それも `None` なら未指定）を用いる。呼び出し側（`_cmd_verify`）は
    `Verdict.FAIL` のときのみ非ゼロ終了コードを返すこと（`NOT_JUDGED` /
    `PASS` はいずれも成功終了。モジュール docstring「終了コード契約」）。

    Returns:
        検証レポート、検証要約を付与した新しい結果、レポートの保存先。
    """
    result = result_module.load_calibration(calibration_path)

    clock = SessionClock(session_id=_new_session_id())
    effective_logger = logger if logger is not None else get_logger(settings.logging, clock)
    try:
        depth_image = _collect_depth_image(
            settings, frame_count=frame_count, logger=effective_logger, source=source
        )
        result_module.check_compatibility(result, depth_image.signature, depth_image.intrinsics)

        observations = [
            (
                spec,
                _run_anchor_observe(
                    depth_image, result.plane, spec, AnchorRole.VERIFICATION, plan.limits, effective_logger
                ),
            )
            for spec in plan.verification_points
        ]

        effective_tolerance = tolerance if tolerance is not None else plan.tolerance
        report = _run_verify_stage(
            result,
            observations,
            expected_baseline_mm=plan.expected_baseline_mm,
            tolerance=effective_tolerance,
            logger=effective_logger,
        )
    finally:
        effective_logger.close()

    report_path = (
        report_out
        if report_out is not None
        else result_module._DEFAULT_OUTPUT_DIR / f"{result.calibration_id}.verification.json"  # noqa: SLF001
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            report_module.report_to_dict(report),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        ),
        encoding="utf-8",
    )

    summary = verify_module.to_summary(report, report_path=report_path)
    updated_result = result_module.attach_verification(result, summary)
    result_module.save_calibration(updated_result, path=calibration_path)

    return report, updated_result, report_path


def run_show(
    settings: RuntimeSettings,
    calibration_path: Path,
    *,
    frame_count: int,
    source: FrameSource | None = None,
    logger: Logger | None = None,
) -> CalibrationResult:
    """`show` サブコマンドの本体: 保存済み結果を読み、`verify` と同じ整合性
    検査（`check_compatibility`）を現在の入力元に対して行った上で返す。

    `CalibrationFailure(PROFILE_MISMATCH)` は握り潰さずそのまま伝播させる
    （`_cmd_show` / `main()` が捕捉して非ゼロ終了へ変換する）。
    """
    result = result_module.load_calibration(calibration_path)
    clock = SessionClock(session_id=_new_session_id())
    effective_logger = logger if logger is not None else get_logger(settings.logging, clock)
    try:
        depth_image = _collect_depth_image(
            settings, frame_count=frame_count, logger=effective_logger, source=source
        )
        result_module.check_compatibility(result, depth_image.signature, depth_image.intrinsics)
    finally:
        effective_logger.close()
    return result


def run_compare(result_a_path: Path, result_b_path: Path):
    """`compare` サブコマンドの本体: 2つの保存済み結果を読み、変換差分を返す。

    入力元・計画ファイルのいずれも要求しない（保存済みの結果ファイル2つだけ
    で完結する。design.md Components 表「CalibrationResultStore」）。
    """
    result_a = result_module.load_calibration(result_a_path)
    result_b = result_module.load_calibration(result_b_path)
    return result_module.compare_calibrations(result_a, result_b)


# ---------------------------------------------------------------------------
# argparse 配線
# ---------------------------------------------------------------------------


def _add_common_args(subparser: argparse.ArgumentParser, *, need_plan: bool) -> None:
    if need_plan:
        subparser.add_argument(
            "--plan",
            type=Path,
            required=True,
            help="キャリブレーション計画ファイル（plan.json）。",
        )
    subparser.add_argument(
        "--source",
        choices=["live", "recorded", "simulated"],
        default="live",
        help="入力元の種別（既定: live）。実機が無い環境では recorded を使う。",
    )
    subparser.add_argument(
        "--session",
        type=Path,
        default=None,
        help="再生対象のセッションディレクトリ（--source recorded のとき必須）。",
    )
    subparser.add_argument(
        "--frames",
        type=int,
        default=10,
        help="収集するフレーム数（既定: 10。初期評価候補であり暫定）。",
    )
    subparser.add_argument(
        "--log-path",
        type=Path,
        default=None,
        help="構造化ログの出力先ディレクトリ（指定するとロギングを有効化する）。",
    )
    subparser.add_argument(
        "--no-log",
        action="store_true",
        help="構造化ロギングを無効にする。",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="world-frame-calibration",
        description=(
            "床平面推定と2つの基準マーカーから World frame"
            "（カメラ座標系→World座標系の剛体変換）を確立し、既知の物理位置との"
            "照合で座標系そのものを独立に検証する CLI。\n\n" + _HELP_EPILOGUE
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    calibrate_parser = subparsers.add_parser(
        "calibrate",
        help="フレーム収集→平面推定→マーカー観測→frame確立→保存（検証は行わない）",
        description=(
            "床平面と原点/方向マーカーの観測から World frame を確立して保存する。"
            "検証はこのコマンドでは実行しない（保存直後は not_verified 状態のまま"
            "であり、procedure.md は verify を必須の後続段階と位置付ける）。\n\n"
            + _HELP_EPILOGUE
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_common_args(calibrate_parser, need_plan=True)
    calibrate_parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="キャリブレーション結果の保存先（既定: var/calibration/<id>.json）。",
    )

    verify_parser = subparsers.add_parser(
        "verify",
        help="保存済み結果を検証し合否を判定する（未判定は成功終了）",
        description=(
            "保存済みのキャリブレーション結果を、計画ファイルの検証点で独立に"
            "検証する。許容値が与えられなければ NOT_JUDGED として成功終了し"
            "（判定していないことと不合格を区別する）、許容値を与えて不合格"
            "（FAIL）だった場合のみ非ゼロ終了する。\n\n" + _HELP_EPILOGUE
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_common_args(verify_parser, need_plan=True)
    verify_parser.add_argument(
        "--calibration",
        type=Path,
        required=True,
        help="検証対象のキャリブレーション結果ファイル。",
    )
    verify_parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="検証レポートの保存先（既定: var/calibration/<id>.verification.json）。",
    )
    verify_parser.add_argument(
        "--tolerance-horizontal-mm",
        type=float,
        default=None,
        help=(
            "水平方向の許容誤差（mm）。--tolerance-vertical-mm と同時に指定する。"
            "省略時は計画ファイルの tolerance を用い、それも無ければ NOT_JUDGED になる。"
        ),
    )
    verify_parser.add_argument(
        "--tolerance-vertical-mm",
        type=float,
        default=None,
        help="鉛直方向の許容誤差（mm）。--tolerance-horizontal-mm と同時に指定する。",
    )
    verify_parser.add_argument(
        "--tolerance-source",
        type=str,
        default="CLI --tolerance-* オプション（暫定）",
        help="CLI から与えた許容値の出典説明。",
    )
    verify_parser.add_argument(
        "--tolerance-final",
        action="store_true",
        help=(
            "CLI から与えた許容値が実測由来（暫定でない）であることを明示する。"
            + _HELP_EPILOGUE
        ),
    )

    show_parser = subparsers.add_parser(
        "show",
        help="保存済み結果の要約表示と整合性検査",
        description=(
            "保存済みのキャリブレーション結果を表示し、現在の入力元との整合性"
            "（ストリーム設定・カメラ内部パラメータ）を検査する。\n\n" + _HELP_EPILOGUE
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_common_args(show_parser, need_plan=False)
    show_parser.add_argument(
        "--calibration",
        type=Path,
        required=True,
        help="表示対象のキャリブレーション結果ファイル。",
    )

    compare_parser = subparsers.add_parser(
        "compare",
        help="2つの結果の差分（再現性の確認）",
        description=(
            "2つの保存済みキャリブレーション結果の変換差分（原点のずれ・全体の"
            "回転角・Z軸のずれ・ヨーのずれ）を表示する。\n\n" + _HELP_EPILOGUE
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    compare_parser.add_argument("result_a", type=Path, help="比較対象の一方の結果ファイル。")
    compare_parser.add_argument("result_b", type=Path, help="比較対象のもう一方の結果ファイル。")

    return parser


def _resolve_settings(args: argparse.Namespace) -> RuntimeSettings:
    """`args` から `sensing_foundation.RuntimeSettings` を解決する。

    設定の解決ロジック自体（CLI引数 > 環境変数 > 設定ファイル > 既定値）は
    再実装せず `RuntimeSettings.resolve()` へそのまま委ねる（design.md CLI
    「Integration」/ tasks.md タスク 6.4: 「設定の解決は上流に乗せる」）。
    """
    overrides: dict[str, object] = {"source": args.source}
    if args.session is not None:
        overrides["session_path"] = args.session
    if args.no_log:
        overrides["logging_enabled"] = False
    if args.log_path is not None:
        overrides["logging_path"] = args.log_path
        overrides["logging_enabled"] = True
    return RuntimeSettings.resolve(file=None, env=os.environ, overrides=overrides)


def _tolerance_from_args(args: argparse.Namespace, plan: CalibrationPlan) -> ToleranceSpec | None:
    horizontal_mm = args.tolerance_horizontal_mm
    vertical_mm = args.tolerance_vertical_mm
    if horizontal_mm is None and vertical_mm is None:
        return plan.tolerance
    if horizontal_mm is None or vertical_mm is None:
        raise CalibrationConfigError(
            "--tolerance-horizontal-mm と --tolerance-vertical-mm は同時に指定すること"
            f"（horizontal={horizontal_mm!r}, vertical={vertical_mm!r}）"
        )
    return ToleranceSpec(
        horizontal_mm=horizontal_mm,
        vertical_mm=vertical_mm,
        provisional=not args.tolerance_final,
        source=args.tolerance_source,
    )


def _print_calibration_error(exc: WorldFrameCalibrationError) -> None:
    """失敗の理由と文脈を必ず標準エラーへ出す（tasks.md タスク 6.4）。"""
    if isinstance(exc, CalibrationFailure):
        print(f"失敗: {exc.reason.value}: {exc.detail}", file=sys.stderr)
        if exc.context:
            print(f"詳細: {dict(exc.context)}", file=sys.stderr)
    else:
        print(f"エラー: {exc}", file=sys.stderr)


def _cmd_calibrate(
    args: argparse.Namespace, *, source: FrameSource | None, logger: Logger | None
) -> int:
    settings = _resolve_settings(args)
    plan = plan_module.load_plan(args.plan)
    result, saved_path = run_calibrate(
        settings, plan, frame_count=args.frames, out=args.out, source=source, logger=logger
    )
    print(report_module.render_calibration_text(result))
    print(f"保存先: {saved_path}")
    print("検証は未実施（verify サブコマンドを実行するまで運用に用いないこと）。")
    return 0


def _cmd_verify(
    args: argparse.Namespace, *, source: FrameSource | None, logger: Logger | None
) -> int:
    settings = _resolve_settings(args)
    plan = plan_module.load_plan(args.plan)
    tolerance = _tolerance_from_args(args, plan)
    report, _updated_result, report_path = run_verify(
        settings,
        plan,
        args.calibration,
        frame_count=args.frames,
        tolerance=tolerance,
        report_out=args.out,
        source=source,
        logger=logger,
    )
    print(report_module.render_verification_text(report))
    print(f"レポート保存先: {report_path}")
    # NOT_JUDGED と PASS はどちらも成功終了、FAIL のみ非ゼロ終了
    # （判定していないことと不合格を区別する。モジュール docstring参照）。
    if report.verdict == Verdict.FAIL:
        return 1
    return 0


def _cmd_show(
    args: argparse.Namespace, *, source: FrameSource | None, logger: Logger | None
) -> int:
    settings = _resolve_settings(args)
    result = run_show(settings, args.calibration, frame_count=args.frames, source=source, logger=logger)
    print(report_module.render_calibration_text(result))
    print("整合性: 現在の入力元のストリーム設定・カメラ内部パラメータと一致している。")
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    diff = run_compare(args.result_a, args.result_b)
    print(report_module.render_difference_text(diff))
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    source: FrameSource | None = None,
    logger: Logger | None = None,
) -> int:
    """CLI エントリポイント。`python -m world_frame_calibration.cli` から呼ばれる。

    `source` / `logger` は `argv` には現れないテスト専用の注入点である
    （モジュール docstring「ハードウェア非依存の実現方法」参照）。実際の
    サブコマンドで `source` / `logger` を使わない `compare` には渡さない。
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "calibrate":
            return _cmd_calibrate(args, source=source, logger=logger)
        if args.command == "verify":
            return _cmd_verify(args, source=source, logger=logger)
        if args.command == "show":
            return _cmd_show(args, source=source, logger=logger)
        if args.command == "compare":
            return _cmd_compare(args)
        raise AssertionError(f"未知のサブコマンド: {args.command}")  # argparse の choices で到達しないはず
    except CalibrationConfigError as exc:
        print(f"設定エラー: {exc}", file=sys.stderr)
        return 2
    except WorldFrameCalibrationError as exc:
        _print_calibration_error(exc)
        return 1
    except SensingConfigError as exc:
        print(f"設定エラー: {exc}", file=sys.stderr)
        return 2
    except SensingFoundationError as exc:
        print(f"入力元エラー: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover - 実行スクリプトとしての入口
    raise SystemExit(main())
