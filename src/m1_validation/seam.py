"""継ぎ目 ★ — カメラ座標系の点列を World 座標系の予測入力へ変換する。

design.md「Components and Interfaces / Seam ★」、tasks.md タスク 2.2、
要件 1.1, 1.2, 1.3, 1.4, 1.5, 1.7, 1.8, 1.9。

**本 Spec の存在理由がこのモジュールである。** 上流3 Spec は意図的に
この変換を持たない——`flying_object_tracking` はカメラ座標系の点列までしか
出さず（`CoordinateFrame` のメンバーが `CAMERA` 1つしか無いのはそのため）、
`world_frame_calibration` は変換を作るが適用先を知らない。**両方を import
する正当な理由を持つのは本モジュールだけ**であり、座標系の責任所在をここ
1箇所に定めることで、取り違えが構造的に起きなくなる。

守るべきことは「やらないこと」の側に集中している。

- **座標変換を再実装しない**（要件 1.2）。上流の `WorldTransform.apply_point`
  を呼ぶだけである。自前の行列演算を持った時点で、較正側の検証レポートが
  保証している変換と、実際に適用される変換が別物になり得る
- **時刻を再基準化しない**（要件 1.3）。`CameraPoint.t_ms` をそのまま
  `Sample.t_ms` にする。ずらすと、上流のログに残る時刻と予測入力の時刻が
  別物になり、end-to-end レイテンシ（観測時刻 → その観測に基づく予測）が
  測れなくなる
- **単位変換を挟まない**。上流・下流ともに mm / ms で一致している
- **`Sample` へ品質情報を入れない**（要件 1.9）。`prediction_core.Sample` は
  `t_ms` / `x_mm` / `y_mm` / `z_mm` の4フィールドのままにする——予測は
  デバイス固有の情報に依存してはならないという入力契約であり、1つ足した
  時点でそれが壊れる。品質情報は `SampleProvenance` として**同じ順序・
  同じ長さの別の列**で並走させる
- **カメラ固有の型・画素座標・カメラパラメータを出力側へ出さない**（要件 1.9）
- **除外を静かに行わない**（要件 1.7）。除外した点は理由ごとに数えて返す

**本モジュールは `flying_object_tracking` と `world_frame_calibration` を
import する唯一のモジュールである。** 取得基盤（`sensing_foundation`）の
接点は `upstream.py` であり、こちらへ穴を増やさない。

**本 Spec は追跡の設定値も方式も決めない**（既定値も持たない）。
`resolve_tracking_settings()` が存在するのは、検出・追跡パッケージを import
するのが本モジュールだけという境界を保ったまま、入口層（`cli.py`）が上流の
解決結果を手に入れられるようにするためだけである。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import Path

from flying_object_tracking import (
    HANDOFF_VERSION,
    CameraPoint,
    CameraTrack,
    CoordinateFrame,
    TrackingPipeline,
    TrackingSettings,
)
from m1_validation.config import M1Settings
from m1_validation.errors import FailureReason, M1ConfigError, SeamFailure
from m1_validation.types import SampleProvenance, SampleReject, ThrowSamples
from prediction_core import Sample
from world_frame_calibration import (
    CalibrationConfigError,
    CalibrationFailure,
    CalibrationResult,
    check_compatibility,
    load_calibration,
)

#: 読める受け渡し形式版。上流の `HANDOFF_VERSION` のみを既知とする。
#: **未知の版は内容を推測して読まない**（要件 1.5。上流3 Spec 共通の方針）。
KNOWN_HANDOFF_VERSIONS: frozenset[str] = frozenset({HANDOFF_VERSION})

#: 検証に合格した状態を表す値（上流 `VerificationState.PASSED` の文字列）。
#:
#: ⚠️ 上流は `VerificationState` を公開入口に出していないため、値で比較する
#: （上流の docstring 自身が「呼び出し側は文字列比較のみで分岐できる」と
#: 定めている）。**`passed` 以外はすべて未検証扱いにする**——「検証したが
#: 不合格」を検証済みとして扱うと、誤差の帰属ができないデータを気づかずに
#: 判断へ使うことになる（要件 2.2）。
_VERIFICATION_PASSED = "passed"

#: 上流 `CalibrationFailure.reason` から本 Spec の `FailureReason` への対応。
#:
#: **上流の例外型をそのまま外へ出さない**——呼び出し側が
#: `world_frame_calibration` の例外を捕まえるために import する羽目になると、
#: 「較正パッケージの接点は本モジュールだけ」という境界が例外の経路から崩れる
#: （`upstream.py` が `ThrowRecordVersionError` に対して採っているのと同じ方針）。
_UPSTREAM_FAILURE_REASONS: dict[str, FailureReason] = {
    "unknown_format_version": FailureReason.UNKNOWN_CALIBRATION_VERSION,
    "profile_mismatch": FailureReason.PROFILE_MISMATCH,
}


def open_calibration(
    path: Path,
    *,
    settings: M1Settings,
    signature: object,
    intrinsics: object,
    allow_unverified: bool = False,
) -> CalibrationResult:
    """キャリブレーション結果を読み、検証ゲートと整合性検査を通す。

    **検証を経ていない誤差は帰属できない。** 座標系が数 cm ずれていても、
    症状は「予測が悪い」としか見えない——得られた誤差が系統誤差なのか予測
    誤差なのかを事後に分離する手段が無い。だから未検証の較正で実測を進めると、
    **その投擲群は丸ごと使えなくなる**。この事実を運用上の注意書きではなく
    **構造**で表すのが本関数である: 既定で拒否し、続行するには呼び出し側が
    明示的に許可を書かねばならず、許可した事実は生成物すべてに印として残る
    （要件 2.1 / 2.2）。

    検査は次の順で行う。**整合性検査を検証ゲートより先に置く**のは、両方が
    成り立つときに「未検証だから」とだけ言われると、解像度を戻さないまま
    許可で押し通してしまうからである。

    1. 読み込み（形式版の既知性は上流が見る）
    2. ストリーム設定・カメラ内部パラメータの一致（要件 1.6）
    3. 検証ゲート（要件 2.1）

    Args:
        path: キャリブレーション結果ファイル。
        settings: `settings.seam.require_verified_calibration` がゲートの
            既定を決める（**既定は有効**）。
        signature: 現在の入力元のストリーム識別情報。**本 Spec は中身を
            解釈せず上流の整合性検査へ素通しする**ため注釈は `object`。
        intrinsics: 現在の入力元のカメラ内部パラメータ（同上）。
        allow_unverified: 明示的な許可。`True` なら未検証でも続行する。

    Returns:
        読み込んだ `CalibrationResult`。

    Raises:
        SeamFailure: `UNKNOWN_CALIBRATION_VERSION`（形式版が未知。要件 1.5）/
            `PROFILE_MISMATCH`（ストリーム設定・内部パラメータが不一致。
            要件 1.6）/ `CALIBRATION_NOT_VERIFIED`（検証未通過かつ
            `allow_unverified` が偽。要件 2.1）。
        M1ConfigError: ファイルが読めない・JSON でない・必須キーが欠けている、
            および上流が本 Spec の失敗理由へ対応付けられない失敗を報告した
            場合（例: 回転行列が正規直交でない＝保存された結果が壊れている）。
    """
    result = _load_calibration_result(path)
    _check_compatibility(result, signature, intrinsics)
    _require_verified_calibration(
        result, settings=settings, allow_unverified=allow_unverified
    )
    return result


def calibration_summary(calibration: CalibrationResult) -> dict[str, object]:
    """後段が読める検証の要約を返す（要件 2.3 / 2.4 の材料）。

    投擲ごとの記録とレポートへそのまま載せられるよう、JSON 化できる素の値
    だけで構成する。**検証を実施していない項目は `None`（欠測）**であり、
    0 や空で埋めない——「偏りが 0 だった」と「測っていない」は別である。

    `bias_mm`（平均オフセット）と `scatter_rms_mm`（ばらつき）は、
    誤差帰属が**上流の独立検証と突き合わせる**ための材料である（要件 2.4）。
    片方だけでは言えないことが、両方あると言える。
    """
    verification = calibration.verification
    state = str(calibration.verification_state)
    return {
        "calibration_id": calibration.calibration_id,
        "verification_state": state,
        "verified": _is_verified(state),
        "verified_at_wall_ms": (
            None if verification is None else verification.verified_at_wall_ms
        ),
        "bias_mm": None if verification is None else list(verification.bias_mm),
        "scatter_rms_mm": (
            None if verification is None else verification.scatter_rms_mm
        ),
        "max_error_norm_mm": (
            None if verification is None else verification.max_error_norm_mm
        ),
        "point_count": None if verification is None else verification.point_count,
        "independent_point_count": (
            None if verification is None else verification.independent_point_count
        ),
    }


def build_samples(
    track: CameraTrack,
    calibration: CalibrationResult,
    *,
    settings: M1Settings,
) -> ThrowSamples:
    """カメラ座標系の点列を World 座標系のサンプル列へ変換する（要件 1.1）。

    Args:
        track: 上流の追跡結果（カメラ座標系）。
        calibration: キャリブレーション結果。**本関数が触るのは
            `transform` / `calibration_id` / `verification_state` の3つだけ**
            である（テストが最小のダブルを使えるのはこのため）。
        settings: 除外規則（`settings.seam`）の出所。

    Returns:
        `ThrowSamples`。`samples` と `provenance` は**同じ順序・同じ長さ**
        であり、`rejected` は**除外理由ごとの件数**を持つ。

    Raises:
        SeamFailure: `FRAME_MISMATCH`（点列がカメラ座標系でない。要件 1.4）
            または `UNKNOWN_HANDOFF_VERSION`（受け渡し形式版が未知。要件 1.5）。
            どちらの場合も**サンプルを1つも作らない**——座標系や形式版が
            食い違ったまま値が下流へ流れることが、本 Spec が防ごうとしている
            事故そのものである。

    除外の判定順序は `NOT_FINITE` → `INSUFFICIENT_VALID_PIXELS` →
    `DEPTH_SPREAD_TOO_LARGE` → `BELOW_FLOOR` であり、**1点は最初に該当した
    1つの理由でだけ数える**。複数理由に重複計上すると件数の合計が除外点数と
    合わなくなり、「何点落ちたのか」が読めなくなる。
    """
    _require_camera_frame(track)
    _require_known_handoff_version(track)

    transform = calibration.transform
    floor_margin_mm = settings.seam.floor_margin_mm
    min_valid_depth_px = settings.seam.min_valid_depth_px
    max_depth_spread_mm = settings.seam.max_depth_spread_mm

    samples: list[Sample] = []
    provenance: list[SampleProvenance] = []
    rejected: dict[SampleReject, int] = {}

    def reject(reason: SampleReject) -> None:
        rejected[reason] = rejected.get(reason, 0) + 1

    for track_point in track.points:
        point = track_point.point
        _require_camera_point(point)

        quality_reason = _first_reject_reason(
            point,
            min_valid_depth_px=min_valid_depth_px,
            max_depth_spread_mm=max_depth_spread_mm,
        )
        if quality_reason is not None:
            reject(quality_reason)
            continue

        world_mm = transform.apply_point((point.x_mm, point.y_mm, point.z_mm))
        if world_mm[2] < floor_margin_mm:
            reject(SampleReject.BELOW_FLOOR)
            continue

        samples.append(
            Sample(
                t_ms=point.t_ms,
                x_mm=world_mm[0],
                y_mm=world_mm[1],
                z_mm=world_mm[2],
            )
        )
        provenance.append(
            SampleProvenance(
                frame_index=track_point.frame_index,
                frame_seq=track_point.frame_seq,
                valid_depth_px=point.valid_depth_px,
                depth_spread_mm=point.depth_spread_mm,
                apparent_diameter_px=point.apparent_diameter_px,
                expected_diameter_px=point.expected_diameter_px,
                rivals=track_point.rivals,
                gap_before=track_point.gap_before,
                camera_ray_unit=camera_ray_unit(calibration, world_mm),
            )
        )

    verification_state = str(calibration.verification_state)
    return ThrowSamples(
        samples=tuple(samples),
        provenance=tuple(provenance),
        rejected=tuple(rejected.items()),
        handoff_version=track.handoff_version,
        calibration_id=calibration.calibration_id,
        verification_state=verification_state,
        verified=_is_verified(verification_state),
    )


def camera_ray_unit(
    calibration: CalibrationResult,
    point_world_mm: tuple[float, float, float],
) -> tuple[float, float, float]:
    """World 系で表した、カメラから当該点へ向かう単位ベクトル（要件 6.3 の材料）。

    誤差の共通偏りが **World 固定の方向**を向いているのか**カメラ視線方向**を
    向いているのかで、原因が較正側か観測・検出側かを切り分ける
    （`research.md` Decision 4）。投擲位置が変わればカメラ視線方向は World 上で
    向きを変えるが、較正のずれは向きを変えない——その違いが判別の材料になる。

    カメラ原点の World 座標は `transform.apply_point((0, 0, 0))`、すなわち
    変換の平行移動成分そのものである。

    Args:
        calibration: キャリブレーション結果（`transform` のみ使う）。
        point_world_mm: 対象点の World 座標（mm）。

    Returns:
        単位ベクトル。

    Raises:
        M1ConfigError: 対象点がカメラ原点と一致し、向きが定まらない場合。
            **黙って 0 ベクトルを返さない**——方向が無いことを方向として
            扱うと、帰属の判定がその点だけ静かに狂う。物理的には Depth 0 の
            点であり、上流の有効判定で落ちているはずのものである。
    """
    origin = calibration.transform.apply_point((0.0, 0.0, 0.0))
    dx = point_world_mm[0] - origin[0]
    dy = point_world_mm[1] - origin[1]
    dz = point_world_mm[2] - origin[2]
    norm = math.sqrt(dx * dx + dy * dy + dz * dz)
    if norm == 0.0 or not math.isfinite(norm):
        raise M1ConfigError(
            "カメラ原点と一致する点に対しては視線方向が定まらない: "
            f"point_world_mm={point_world_mm}, camera_origin_world_mm={origin}",
            {"point_world_mm": point_world_mm, "camera_origin_world_mm": origin},
        )
    return (dx / norm, dy / norm, dz / norm)


def resolve_tracking_settings(
    *,
    config_path: Path | None,
    env: Mapping[str, str],
    overrides: Mapping[str, object],
) -> TrackingSettings:
    """上流の `TrackingSettings.resolve()` への素通し。

    上流の署名は `resolve(*, file, env, overrides)` で**3つとも必須**である。
    本関数が `config_path` だけを受け取って `env` / `overrides` を内部で空に
    埋めると、**上流側の環境変数と CLI 上書きが黙って捨てられる**。本 Spec の
    CLI は「CLI 引数 > 環境変数 > 設定ファイル > 既定値」を掲げているため、
    3つとも呼び出し元から受け取ってそのまま渡す。

    **本 Spec は追跡の設定値を一切決めない**（既定値も持たない）。この関数が
    存在するのは、`flying_object_tracking` を import するのが本モジュールだけ
    という境界を保ったまま、`cli.py` が上流の解決結果を手に入れられるように
    するためだけである（OQ-26 の検出方式選定は上流の担当）。
    """
    return TrackingSettings.resolve(file=config_path, env=env, overrides=overrides)


def open_tracking(
    settings: M1Settings,
    tracking_settings: object,
    logger: object,
) -> TrackingPipeline:
    """上流の追跡パイプラインを生成して返す。

    この関数があることで `runner.py` は `flying_object_tracking` を import
    せずに済み、検出・追跡パッケージとの接点が本モジュールだけに保たれる。

    Args:
        settings: 本 Spec の設定。**現時点では追跡の生成に使わない**——
            上流のパイプラインは上流の設定だけで組み立たるためである。
            引数に残しているのは design.md の署名に合わせるためであり、
            本 Spec が追跡の設定へ介入しないことをここで示している。
        tracking_settings: `flying_object_tracking.TrackingSettings`。
            **調達手段は `resolve_tracking_settings()` に限る**。
        logger: `sensing_foundation` の `Logger`。**調達手段は
            `UpstreamGateway.get_logger_handle()` に限る**（取得基盤の接点は
            `upstream.py` 1モジュールという制約を守るため）。

    どちらも**本 Spec が生成できない値**であり、引数で受け取って
    **そのまま渡すだけ**にする。片方でも本モジュールがこしらえた時点で、
    追跡の設定を本 Spec が決めたことになる。中身は解釈しないため、注釈は
    `object` に留める。
    """
    del settings  # 追跡の生成に本 Spec の設定は使わない（上記 docstring 参照）
    return TrackingPipeline(tracking_settings, logger)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# 内部: 前提の検査と除外規則
# --------------------------------------------------------------------------


def _is_verified(verification_state: str) -> bool:
    """検証に合格した状態かを返す。**`passed` 以外はすべて未検証扱い**。

    `failed`（検証したが不合格）と `not_judged`（許容値が無く未判定）を
    検証済みとして扱うと、誤差の帰属ができないデータを気づかずに判断へ
    使うことになる（要件 2.2）。
    """
    return verification_state == _VERIFICATION_PASSED


def _load_calibration_result(path: Path) -> CalibrationResult:
    """上流の `load_calibration()` を呼び、失敗を本 Spec の語彙へ翻訳する。"""
    try:
        return load_calibration(Path(path))
    except CalibrationFailure as exc:
        raise _translate_failure(exc, path=path) from exc
    except CalibrationConfigError as exc:
        raise M1ConfigError(
            f"キャリブレーション結果を読み込めない: {path}（{exc}）",
            {"path": str(path)},
        ) from exc


def _check_compatibility(
    result: CalibrationResult, signature: object, intrinsics: object
) -> None:
    """ストリーム設定・カメラ内部パラメータの一致を検査する（要件 1.6）。

    検査そのものは上流の `check_compatibility()` に委ねる——解像度が変われば
    内部パラメータも変わる、という判断規則は較正側が所有する。本関数は
    失敗を本 Spec の語彙へ翻訳するだけである。
    """
    try:
        check_compatibility(result, signature, intrinsics)  # type: ignore[arg-type]
    except CalibrationFailure as exc:
        raise _translate_failure(exc, path=None) from exc


def _require_verified_calibration(
    result: CalibrationResult,
    *,
    settings: M1Settings,
    allow_unverified: bool,
) -> None:
    """検証ゲート（要件 2.1）。既定で未検証を拒否する。"""
    if not settings.seam.require_verified_calibration or allow_unverified:
        return
    state = str(result.verification_state)
    if _is_verified(state):
        return
    raise SeamFailure(
        FailureReason.CALIBRATION_NOT_VERIFIED,
        "検証を通過していないキャリブレーション結果では実測を進めない: "
        f"verification_state={state!r}（calibration_id="
        f"{result.calibration_id!r}）。"
        "検証を経ていない誤差は系統誤差と予測誤差に分離できないため、"
        "この状態で撮った投擲群は後から使えない。"
        "承知のうえで続けるなら明示的に許可すること（生成物に印が付く）",
        {
            "verification_state": state,
            "calibration_id": result.calibration_id,
        },
    )


def _translate_failure(exc: CalibrationFailure, *, path: Path | None) -> Exception:
    """上流の `CalibrationFailure` を本 Spec の例外へ写す。

    対応する `FailureReason` が無い理由（例: `rotation_not_orthonormal`
    ＝保存された結果そのものが壊れている）は `M1ConfigError` にする。
    **理由を捏造して既存の `FailureReason` へ丸めない**——「形式版が未知」と
    「回転行列が壊れている」は直し方が違う。
    """
    reason = _UPSTREAM_FAILURE_REASONS.get(str(exc.reason))
    if reason is None:
        return M1ConfigError(
            f"キャリブレーション結果が使えない: {exc}",
            {"upstream_reason": str(exc.reason), "path": None if path is None else str(path)},
        )
    return SeamFailure(reason, str(exc), dict(exc.context))


def _require_camera_frame(track: CameraTrack) -> None:
    """点列がカメラ座標系であることを確かめる（要件 1.4）。

    上流の `CoordinateFrame` はメンバーが `CAMERA` ただ1つであり、
    「カメラ座標系以外を取り得ない」ことを型で保証している。それでもここで
    確かめるのは、**上流が将来メンバーを増やした場合**と、**手で組んだ値が
    流れ込んだ場合**に、World 座標の点をもう一度変換してしまう事故を防ぐ
    ためである（二重変換は「予測が悪い」としか見えない）。
    """
    if track.frame is not CoordinateFrame.CAMERA:
        raise SeamFailure(
            FailureReason.FRAME_MISMATCH,
            f"点列がカメラ座標系でない: frame={track.frame!r}"
            f"（期待: {CoordinateFrame.CAMERA!r}）",
            {"frame": str(track.frame), "expected": str(CoordinateFrame.CAMERA)},
        )


def _require_camera_point(point: CameraPoint) -> None:
    """1点ごとにも座標系を確かめる（要件 1.4）。

    点列と個々の点で座標系が食い違う点列は、上流の型では作れないが、
    **食い違ったまま流れれば World 座標の点をもう一度変換する**ことになる。
    見つけた時点で止める。
    """
    if point.frame is not CoordinateFrame.CAMERA:
        raise SeamFailure(
            FailureReason.FRAME_MISMATCH,
            "点列の中にカメラ座標系でない点がある: "
            f"frame={point.frame!r}（t_ms={point.t_ms}）",
            {"frame": str(point.frame), "t_ms": point.t_ms},
        )


def _require_known_handoff_version(track: CameraTrack) -> None:
    """受け渡し形式版が既知であることを確かめる（要件 1.5）。"""
    if track.handoff_version not in KNOWN_HANDOFF_VERSIONS:
        raise SeamFailure(
            FailureReason.UNKNOWN_HANDOFF_VERSION,
            f"受け渡し形式版が未知である: {track.handoff_version!r}"
            f"（既知: {sorted(KNOWN_HANDOFF_VERSIONS)}）。内容を推測して読まない",
            {
                "handoff_version": track.handoff_version,
                "known": sorted(KNOWN_HANDOFF_VERSIONS),
            },
        )


def _first_reject_reason(
    point: CameraPoint,
    *,
    min_valid_depth_px: int,
    max_depth_spread_mm: float,
) -> SampleReject | None:
    """変換前に判定できる除外理由を返す（該当しなければ `None`）。

    床面下（`BELOW_FLOOR`）だけは World 座標が要るため呼び出し側で見る。
    判定順序はここに書いた順であり、**1点は最初に該当した1つの理由でだけ
    数える**（`build_samples()` の docstring 参照）。
    """
    if not (
        math.isfinite(point.x_mm)
        and math.isfinite(point.y_mm)
        and math.isfinite(point.z_mm)
    ):
        return SampleReject.NOT_FINITE
    if point.valid_depth_px < min_valid_depth_px:
        return SampleReject.INSUFFICIENT_VALID_PIXELS
    if point.depth_spread_mm > max_depth_spread_mm:
        return SampleReject.DEPTH_SPREAD_TOO_LARGE
    return None
