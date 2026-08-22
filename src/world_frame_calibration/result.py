"""キャリブレーション結果の組み立てと直列化（design.md「L6-L9: 永続化・検証・入口」
CalibrationResultStore コンポーネント / tasks.md タスク 4.1 / 要件 6.1, 6.2, 6.6）。

本モジュールが提供するもの:

- `CalibrationResult`: 形式版・一意識別子・生成時刻・入力元種別と再生対象・
  ストリーム識別情報・内部パラメータ・平面と推定品質・変換と frame 幾何・
  2つのマーカー観測（原点・方向）・使用した計画の要約・備考・検証要約を
  1つに束ねた不変値（design.md CalibrationResultStore「Contracts」）
- `VerificationState`: 未検証・合格・不合格・未判定の4状態
  （design.md CalibrationResultStore「Contracts」）。`verification` が
  `None` のとき `verification_state` は `NOT_VERIFIED` を返し、検証を
  経ていない結果を運用に使わないという手順上の規則（要件 7.3）を、
  値として機械的に判別できるようにする（要件 6.6）
- `assemble_calibration_result`: `frame.build_world_frame` の返り値
  （`WorldFrameEstablishment`）と付随メタデータから `CalibrationResult`
  を組み立てる。組み立て直後は `verification=None`
  （＝ `VerificationState.NOT_VERIFIED`）で返す
- `save_calibration` / `load_calibration`: JSON への保存・読み込み。
  保存 → 読み込みで**同一の変換が再現される**（要件 6.1）

**本タスクが持たないもの**（design.md CalibrationResultStore「Contracts」に
現れるが、後続タスクの担当）:

- `check_compatibility`（読み込み時のストリーム設定・内部パラメータ整合性
  検査。タスク 4.2）
- `attach_verification`（検証要約の付与。タスク 4.2 / 5.x）
- `compare_calibrations`（結果どうしの比較。タスク 4.3）

これらは本モジュールに存在しない。`load_calibration` は形式版の未知判定・
回転の正規直交性以外の整合性検査（ストリーム設定・内部パラメータの一致）を
一切行わない（タスク 4.2 の責務）。`WorldTransform` 自体が構築時に正規直交性・
行列式 +1 を検査するため（`transform.py` 参照）、読み込んだ回転が不正なら
その検査は自然に働く。

**直列化の方針**（`plan.py` と同じ規約。design.md「Existing Architecture
Analysis」/ tasks.md タスク 4.1）:

- `json.dumps(..., allow_nan=False, ensure_ascii=False)` を用いる。
  NaN / Infinity を含む値は保存時に `ValueError` として拒否される
- 欠測（`None`）はキーごと省く。`0` や `null` で埋めない
  （`session_path` / `verification` およびその内部の `tolerance` /
  `report_path` が対象）

**出力先の既定**: `var/calibration/`（tasks.md タスク 4.1）。`save_calibration`
の `path` を省略すると `var/calibration/<calibration_id>.json` へ保存し、
親ディレクトリが無ければ作成する（`sensing_foundation.throw_store` の
`self._path.parent.mkdir(parents=True, exist_ok=True)` と同じ方針を踏襲）。

**一意識別子**: `calibration_id` は生成時刻とランダムなソルトから作る
ハッシュ由来の識別子である（design.md CalibrationResult「生成時刻とハッシュ
から作る一意識別子」）。決定性は要求されない（要件が求めるのは一意性のみ）。
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from world_frame_calibration.errors import CalibrationConfigError
from world_frame_calibration.frame import WorldFrameEstablishment
from world_frame_calibration.transform import WorldTransform
from world_frame_calibration.types import (
    AnchorObservation,
    AnchorRole,
    FrameGeometry,
    Intrinsics,
    PixelRegion,
    Plane,
    PlaneQuality,
    StreamSignature,
    ToleranceSpec,
)

__all__ = [
    "CALIBRATION_FORMAT_VERSION",
    "CalibrationResult",
    "VerificationState",
    "VerificationSummary",
    "assemble_calibration_result",
    "load_calibration",
    "save_calibration",
]

CALIBRATION_FORMAT_VERSION = "1.0"
"""現在のキャリブレーション結果ファイル形式の版（design.md
CalibrationResultStore「Contracts」）。未知の版を読み込んだ場合に失敗させる
検査はタスク 4.2 が実装する（要件 6.3）。"""

_DEFAULT_OUTPUT_DIR = Path("var/calibration")
"""`save_calibration` が `path` 省略時に用いる既定の出力先ディレクトリ
（tasks.md タスク 4.1: 「出力先の既定を `var/calibration/` とする」）。"""


class VerificationState(StrEnum):
    """`CalibrationResult` に付与された検証の状態（design.md
    CalibrationResultStore「Contracts」/ 要件 6.6）。

    `StrEnum` を用いることで、呼び出し側は文字列比較のみで分岐できる
    （`errors.FailureReason` と同じ規約）。
    """

    NOT_VERIFIED = "not_verified"
    """検証が一度も付与されていない（`CalibrationResult.verification is
    None`）。組み立て直後の既定状態。"""

    PASSED = "passed"
    """検証を実施し、許容値に対して合格した。"""

    FAILED = "failed"
    """検証を実施し、許容値に対して不合格だった。"""

    NOT_JUDGED = "not_judged"
    """検証は実施したが許容値が無く判定していない。"""


@dataclass(frozen=True, slots=True)
class VerificationSummary:
    """`CalibrationResult` に関連付ける、最後に実施した検証の要約
    （design.md CalibrationResultStore「Contracts」/ 要件 6.5）。

    Attributes:
        verified_at_wall_ms: 検証を実施した壁時計時刻（ms）。
        verdict: 検証の状態。
        point_count: 検証に用いた全検証点数。
        independent_point_count: うち独立点（確立用マーカーと重複しない）の数。
        bias_mm: 軸ごとの平均オフセット（バイアス）。
        scatter_rms_mm: バイアス除去後の残差の二乗平均平方根（ばらつき）。
        max_error_norm_mm: 検証点の誤差ノルムの最大値。
        tolerance: 判定に用いた許容値仕様。未指定なら `None`（未判定）。
        report_path: 検証レポートの保存先。未保存なら `None`。
    """

    verified_at_wall_ms: float
    verdict: VerificationState
    point_count: int
    independent_point_count: int
    bias_mm: tuple[float, float, float]
    scatter_rms_mm: float
    max_error_norm_mm: float
    tolerance: ToleranceSpec | None
    report_path: str | None


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    """永続化されたキャリブレーション結果1件（design.md
    CalibrationResultStore「Contracts」/ 要件 6.1, 6.2）。

    Attributes:
        calibration_format_version: この結果ファイルの形式版。
        calibration_id: 一意識別子（生成時刻とハッシュから作る）。
        created_at_wall_ms: 取得日時（壁時計時刻、ms）。
        source_kind: 入力元の種別（`"live"` / `"recorded"` / `"simulated"`）。
        session_path: 再生対象（記録済みセッションのパス）。live/simulated
            など再生対象が無い場合は `None`。
        signature: 取得時点のストリーム識別情報（整合性検査の対象。要件 6.4）。
        intrinsics: 取得時点のカメラ内部パラメータ。
        plane: 推定した床平面と推定品質。
        transform: 確立した剛体変換（カメラ座標系 → World 座標系）。
        geometry: World frame の軸・原点・ヨー感度。
        origin_anchor: 確立に用いた原点マーカーの観測。
        x_axis_anchor: 確立に用いた方向マーカーの観測。
        plan_digest: 使用した計画（`CalibrationPlan`）の要約（範囲・下限・
            許容値など）。`plan` ファイル自体が失われても最低限の再現材料が
            残るようにする（design.md CalibrationResultStore「Risks」）。
        verification: 最後に実施した検証の要約。未付与なら `None`
            （＝ `verification_state` が `NOT_VERIFIED`。要件 6.6）。
        notes: 自由記述の備考。
    """

    calibration_format_version: str
    calibration_id: str
    created_at_wall_ms: float
    source_kind: str
    session_path: str | None
    signature: StreamSignature
    intrinsics: Intrinsics
    plane: Plane
    transform: WorldTransform
    geometry: FrameGeometry
    origin_anchor: AnchorObservation
    x_axis_anchor: AnchorObservation
    plan_digest: Mapping[str, object]
    verification: VerificationSummary | None
    notes: str

    @property
    def verification_state(self) -> VerificationState:
        """検証の状態を判別可能な値として返す（要件 6.6）。

        `verification` が `None`（未付与）であることと、検証を実施した上で
        不合格・未判定であることを、呼び出し側が文字列比較で区別できる。
        """
        if self.verification is None:
            return VerificationState.NOT_VERIFIED
        return self.verification.verdict


def _generate_calibration_id(created_at_wall_ms: float) -> str:
    """生成時刻とランダムなソルトのハッシュから一意識別子を作る。

    決定性は要求しない（要件が求めるのは一意性のみ）。時刻を先頭に置くことで
    ファイル一覧のソート順が生成順とおおむね一致する副次的な利点がある。
    """
    salt = uuid.uuid4().hex
    digest = hashlib.sha256(f"{created_at_wall_ms!r}:{salt}".encode("utf-8")).hexdigest()
    return f"{int(created_at_wall_ms)}-{digest[:16]}"


def assemble_calibration_result(
    establishment: WorldFrameEstablishment,
    *,
    source_kind: str,
    session_path: str | None,
    signature: StreamSignature,
    intrinsics: Intrinsics,
    plan_digest: Mapping[str, object],
    notes: str = "",
) -> CalibrationResult:
    """`WorldFrameEstablishment` と付随メタデータから `CalibrationResult` を
    組み立てる（tasks.md タスク 4.1 / 要件 6.1, 6.2）。

    `WorldFrameEstablishment`（`frame.build_world_frame` の返り値）は
    `transform` / `geometry` / `plane` / `origin_anchor` / `x_axis_anchor`
    をすでに1つに束ねているため、本関数はそれをそのまま転記し、形式版・
    一意識別子・生成時刻・検証状態（未付与）を新たに補う。

    組み立て直後の結果は常に `verification=None`
    （＝ `verification_state == VerificationState.NOT_VERIFIED`）で返す。
    検証要約の付与は `attach_verification`（タスク 4.2）の責務であり、
    本関数は行わない。

    Args:
        establishment: `frame.build_world_frame` が返す確立結果。
        source_kind: 入力元の種別（`"live"` / `"recorded"` / `"simulated"`）。
        session_path: 再生対象のパス。無ければ `None`。
        signature: 取得時点のストリーム識別情報。
        intrinsics: 取得時点のカメラ内部パラメータ。
        plan_digest: 使用した計画の要約。JSON 直列化可能な値のみを含めること
            （`save_calibration` がそのまま `json.dumps` へ渡すため）。
        notes: 自由記述の備考。既定は空文字列。

    Returns:
        新たに組み立てた `CalibrationResult`。
    """
    created_at_wall_ms = time.time() * 1000.0
    calibration_id = _generate_calibration_id(created_at_wall_ms)
    return CalibrationResult(
        calibration_format_version=CALIBRATION_FORMAT_VERSION,
        calibration_id=calibration_id,
        created_at_wall_ms=created_at_wall_ms,
        source_kind=source_kind,
        session_path=session_path,
        signature=signature,
        intrinsics=intrinsics,
        plane=establishment.plane,
        transform=establishment.transform,
        geometry=establishment.geometry,
        origin_anchor=establishment.origin_anchor,
        x_axis_anchor=establishment.x_axis_anchor,
        plan_digest=dict(plan_digest),
        verification=None,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# 直列化: dataclass <-> JSON 互換 dict（`plan.py` と同じ規約）
# ---------------------------------------------------------------------------


def _drop_none(data: dict[str, object]) -> dict[str, object]:
    """値が `None` のキーを取り除く（欠測はキーごと省く。`null` で埋めない）。"""
    return {key: value for key, value in data.items() if value is not None}


def _region_to_dict(region: PixelRegion) -> dict[str, object]:
    return {
        "x0_px": region.x0_px,
        "y0_px": region.y0_px,
        "x1_px": region.x1_px,
        "y1_px": region.y1_px,
    }


def _region_from_dict(data: object) -> PixelRegion:
    if not isinstance(data, Mapping):
        raise CalibrationConfigError(f"画素範囲の形式が不正（object ではない）: {data!r}")
    return PixelRegion(
        x0_px=int(data["x0_px"]),  # type: ignore[index]
        y0_px=int(data["y0_px"]),  # type: ignore[index]
        x1_px=int(data["x1_px"]),  # type: ignore[index]
        y1_px=int(data["y1_px"]),  # type: ignore[index]
    )


def _signature_to_dict(signature: StreamSignature) -> dict[str, object]:
    return {
        "width_px": signature.width_px,
        "height_px": signature.height_px,
        "fps": signature.fps,
        "depth_scale_mm": signature.depth_scale_mm,
        "color_enabled": signature.color_enabled,
    }


def _signature_from_dict(data: object) -> StreamSignature:
    if not isinstance(data, Mapping):
        raise CalibrationConfigError(
            f"ストリーム識別情報の形式が不正（object ではない）: {data!r}"
        )
    return StreamSignature(
        width_px=int(data["width_px"]),  # type: ignore[index]
        height_px=int(data["height_px"]),  # type: ignore[index]
        fps=int(data["fps"]),  # type: ignore[index]
        depth_scale_mm=float(data["depth_scale_mm"]),  # type: ignore[index]
        color_enabled=bool(data["color_enabled"]),  # type: ignore[index]
    )


def _intrinsics_to_dict(intrinsics: Intrinsics) -> dict[str, object]:
    return {
        "width_px": intrinsics.width_px,
        "height_px": intrinsics.height_px,
        "fx_px": intrinsics.fx_px,
        "fy_px": intrinsics.fy_px,
        "ppx_px": intrinsics.ppx_px,
        "ppy_px": intrinsics.ppy_px,
        "model": intrinsics.model,
        "coeffs": list(intrinsics.coeffs),
    }


def _intrinsics_from_dict(data: object) -> Intrinsics:
    if not isinstance(data, Mapping):
        raise CalibrationConfigError(f"内部パラメータの形式が不正（object ではない）: {data!r}")
    coeffs_raw = data["coeffs"]  # type: ignore[index]
    coeffs = tuple(float(c) for c in coeffs_raw)  # type: ignore[union-attr]
    if len(coeffs) != 5:
        raise CalibrationConfigError(f"内部パラメータの coeffs は5要素でなければならない: {coeffs!r}")
    return Intrinsics(
        width_px=int(data["width_px"]),  # type: ignore[index]
        height_px=int(data["height_px"]),  # type: ignore[index]
        fx_px=float(data["fx_px"]),  # type: ignore[index]
        fy_px=float(data["fy_px"]),  # type: ignore[index]
        ppx_px=float(data["ppx_px"]),  # type: ignore[index]
        ppy_px=float(data["ppy_px"]),  # type: ignore[index]
        model=str(data["model"]),  # type: ignore[index]
        coeffs=coeffs,  # type: ignore[arg-type]
    )


def _plane_quality_to_dict(quality: PlaneQuality) -> dict[str, object]:
    return {
        "points_considered": quality.points_considered,
        "inlier_count": quality.inlier_count,
        "inlier_ratio": quality.inlier_ratio,
        "residual_abs_p50_mm": quality.residual_abs_p50_mm,
        "residual_abs_p95_mm": quality.residual_abs_p95_mm,
        "residual_rms_mm": quality.residual_rms_mm,
        "frames_used": quality.frames_used,
        "incidence_angle_deg": quality.incidence_angle_deg,
        "rng_seed": quality.rng_seed,
    }


def _plane_quality_from_dict(data: object) -> PlaneQuality:
    if not isinstance(data, Mapping):
        raise CalibrationConfigError(f"平面推定品質の形式が不正（object ではない）: {data!r}")
    return PlaneQuality(
        points_considered=int(data["points_considered"]),  # type: ignore[index]
        inlier_count=int(data["inlier_count"]),  # type: ignore[index]
        inlier_ratio=float(data["inlier_ratio"]),  # type: ignore[index]
        residual_abs_p50_mm=float(data["residual_abs_p50_mm"]),  # type: ignore[index]
        residual_abs_p95_mm=float(data["residual_abs_p95_mm"]),  # type: ignore[index]
        residual_rms_mm=float(data["residual_rms_mm"]),  # type: ignore[index]
        frames_used=int(data["frames_used"]),  # type: ignore[index]
        incidence_angle_deg=float(data["incidence_angle_deg"]),  # type: ignore[index]
        rng_seed=int(data["rng_seed"]),  # type: ignore[index]
    )


def _plane_to_dict(plane: Plane) -> dict[str, object]:
    return {
        "normal": list(plane.normal),
        "distance_mm": plane.distance_mm,
        "quality": _plane_quality_to_dict(plane.quality),
    }


def _plane_from_dict(data: object) -> Plane:
    if not isinstance(data, Mapping):
        raise CalibrationConfigError(f"平面の形式が不正（object ではない）: {data!r}")
    nx, ny, nz = data["normal"]  # type: ignore[misc]
    return Plane(
        normal=(float(nx), float(ny), float(nz)),
        distance_mm=float(data["distance_mm"]),  # type: ignore[index]
        quality=_plane_quality_from_dict(data["quality"]),  # type: ignore[index]
    )


def _transform_to_dict(transform: WorldTransform) -> dict[str, object]:
    return {
        "rotation": [list(row) for row in transform.rotation],
        "translation_mm": list(transform.translation_mm),
    }


def _transform_from_dict(data: object) -> WorldTransform:
    if not isinstance(data, Mapping):
        raise CalibrationConfigError(f"変換の形式が不正（object ではない）: {data!r}")
    rotation_raw = data["rotation"]  # type: ignore[index]
    rotation = tuple(
        tuple(float(v) for v in row) for row in rotation_raw  # type: ignore[union-attr]
    )
    tx, ty, tz = data["translation_mm"]  # type: ignore[misc]
    return WorldTransform(
        rotation=rotation,  # type: ignore[arg-type]
        translation_mm=(float(tx), float(ty), float(tz)),
    )


def _vec3_from_mapping(data: Mapping[str, object], key: str) -> tuple[float, float, float]:
    x, y, z = data[key]  # type: ignore[misc]
    return (float(x), float(y), float(z))


def _geometry_to_dict(geometry: FrameGeometry) -> dict[str, object]:
    return {
        "origin_camera_mm": list(geometry.origin_camera_mm),
        "x_axis_camera": list(geometry.x_axis_camera),
        "y_axis_camera": list(geometry.y_axis_camera),
        "z_axis_camera": list(geometry.z_axis_camera),
        "baseline_mm": geometry.baseline_mm,
        "yaw_sensitivity_deg_per_mm": geometry.yaw_sensitivity_deg_per_mm,
        "lateral_error_mm_per_mm_at_1000mm": geometry.lateral_error_mm_per_mm_at_1000mm,
    }


def _geometry_from_dict(data: object) -> FrameGeometry:
    if not isinstance(data, Mapping):
        raise CalibrationConfigError(f"frame 幾何の形式が不正（object ではない）: {data!r}")
    return FrameGeometry(
        origin_camera_mm=_vec3_from_mapping(data, "origin_camera_mm"),
        x_axis_camera=_vec3_from_mapping(data, "x_axis_camera"),
        y_axis_camera=_vec3_from_mapping(data, "y_axis_camera"),
        z_axis_camera=_vec3_from_mapping(data, "z_axis_camera"),
        baseline_mm=float(data["baseline_mm"]),  # type: ignore[index]
        yaw_sensitivity_deg_per_mm=float(data["yaw_sensitivity_deg_per_mm"]),  # type: ignore[index]
        lateral_error_mm_per_mm_at_1000mm=float(
            data["lateral_error_mm_per_mm_at_1000mm"]  # type: ignore[index]
        ),
    )


def _anchor_observation_to_dict(anchor: AnchorObservation) -> dict[str, object]:
    return {
        "label": anchor.label,
        "role": anchor.role.value,
        "point_camera_mm": list(anchor.point_camera_mm),
        "point_on_plane_mm": list(anchor.point_on_plane_mm),
        "height_above_plane_mm": anchor.height_above_plane_mm,
        "range_from_camera_mm": anchor.range_from_camera_mm,
        "sample_count": anchor.sample_count,
        "spread_mm": anchor.spread_mm,
        "region": _region_to_dict(anchor.region),
        "frames_used": anchor.frames_used,
    }


def _anchor_observation_from_dict(data: object) -> AnchorObservation:
    if not isinstance(data, Mapping):
        raise CalibrationConfigError(f"マーカー観測の形式が不正（object ではない）: {data!r}")
    return AnchorObservation(
        label=str(data["label"]),  # type: ignore[index]
        role=AnchorRole(data["role"]),  # type: ignore[index]
        point_camera_mm=_vec3_from_mapping(data, "point_camera_mm"),
        point_on_plane_mm=_vec3_from_mapping(data, "point_on_plane_mm"),
        height_above_plane_mm=float(data["height_above_plane_mm"]),  # type: ignore[index]
        range_from_camera_mm=float(data["range_from_camera_mm"]),  # type: ignore[index]
        sample_count=int(data["sample_count"]),  # type: ignore[index]
        spread_mm=float(data["spread_mm"]),  # type: ignore[index]
        region=_region_from_dict(data["region"]),  # type: ignore[index]
        frames_used=int(data["frames_used"]),  # type: ignore[index]
    )


def _tolerance_to_dict(tolerance: ToleranceSpec | None) -> dict[str, object] | None:
    if tolerance is None:
        return None
    return {
        "horizontal_mm": tolerance.horizontal_mm,
        "vertical_mm": tolerance.vertical_mm,
        "provisional": tolerance.provisional,
        "source": tolerance.source,
    }


def _tolerance_from_dict(data: object) -> ToleranceSpec | None:
    if data is None:
        return None
    if not isinstance(data, Mapping):
        raise CalibrationConfigError(f"許容値仕様の形式が不正（object ではない）: {data!r}")
    return ToleranceSpec(
        horizontal_mm=float(data["horizontal_mm"]),  # type: ignore[index]
        vertical_mm=float(data["vertical_mm"]),  # type: ignore[index]
        provisional=bool(data["provisional"]),  # type: ignore[index]
        source=str(data["source"]),  # type: ignore[index]
    )


def _verification_summary_to_dict(summary: VerificationSummary) -> dict[str, object]:
    data: dict[str, object] = {
        "verified_at_wall_ms": summary.verified_at_wall_ms,
        "verdict": summary.verdict.value,
        "point_count": summary.point_count,
        "independent_point_count": summary.independent_point_count,
        "bias_mm": list(summary.bias_mm),
        "scatter_rms_mm": summary.scatter_rms_mm,
        "max_error_norm_mm": summary.max_error_norm_mm,
        "tolerance": _tolerance_to_dict(summary.tolerance),
        "report_path": summary.report_path,
    }
    return _drop_none(data)


def _verification_summary_from_dict(data: object) -> VerificationSummary:
    if not isinstance(data, Mapping):
        raise CalibrationConfigError(f"検証要約の形式が不正（object ではない）: {data!r}")
    bx, by, bz = data["bias_mm"]  # type: ignore[misc]
    return VerificationSummary(
        verified_at_wall_ms=float(data["verified_at_wall_ms"]),  # type: ignore[index]
        verdict=VerificationState(data["verdict"]),  # type: ignore[index]
        point_count=int(data["point_count"]),  # type: ignore[index]
        independent_point_count=int(data["independent_point_count"]),  # type: ignore[index]
        bias_mm=(float(bx), float(by), float(bz)),
        scatter_rms_mm=float(data["scatter_rms_mm"]),  # type: ignore[index]
        max_error_norm_mm=float(data["max_error_norm_mm"]),  # type: ignore[index]
        tolerance=_tolerance_from_dict(data.get("tolerance")),  # type: ignore[union-attr]
        report_path=data.get("report_path"),  # type: ignore[union-attr]
    )


def _result_to_dict(result: CalibrationResult) -> dict[str, object]:
    data: dict[str, object] = {
        "calibration_format_version": result.calibration_format_version,
        "calibration_id": result.calibration_id,
        "created_at_wall_ms": result.created_at_wall_ms,
        "source_kind": result.source_kind,
        "session_path": result.session_path,
        "signature": _signature_to_dict(result.signature),
        "intrinsics": _intrinsics_to_dict(result.intrinsics),
        "plane": _plane_to_dict(result.plane),
        "transform": _transform_to_dict(result.transform),
        "geometry": _geometry_to_dict(result.geometry),
        "origin_anchor": _anchor_observation_to_dict(result.origin_anchor),
        "x_axis_anchor": _anchor_observation_to_dict(result.x_axis_anchor),
        "plan_digest": dict(result.plan_digest),
        "verification": (
            _verification_summary_to_dict(result.verification)
            if result.verification is not None
            else None
        ),
        "notes": result.notes,
    }
    return _drop_none(data)


def _result_from_dict(data: Mapping[str, object]) -> CalibrationResult:
    verification_raw = data.get("verification")
    verification = (
        _verification_summary_from_dict(verification_raw)
        if verification_raw is not None
        else None
    )
    return CalibrationResult(
        calibration_format_version=str(data["calibration_format_version"]),
        calibration_id=str(data["calibration_id"]),
        created_at_wall_ms=float(data["created_at_wall_ms"]),  # type: ignore[arg-type]
        source_kind=str(data["source_kind"]),
        session_path=data.get("session_path"),  # type: ignore[assignment]
        signature=_signature_from_dict(data["signature"]),
        intrinsics=_intrinsics_from_dict(data["intrinsics"]),
        plane=_plane_from_dict(data["plane"]),
        transform=_transform_from_dict(data["transform"]),
        geometry=_geometry_from_dict(data["geometry"]),
        origin_anchor=_anchor_observation_from_dict(data["origin_anchor"]),
        x_axis_anchor=_anchor_observation_from_dict(data["x_axis_anchor"]),
        plan_digest=dict(data["plan_digest"]),  # type: ignore[arg-type]
        verification=verification,
        notes=str(data.get("notes", "")),
    )


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------


def save_calibration(result: CalibrationResult, path: Path | None = None) -> None:
    """`result` を JSON として `path` へ保存する（design.md
    CalibrationResultStore「Contracts」/ 要件 6.1, 6.2）。

    `path` を省略すると `var/calibration/<calibration_id>.json`
    （既定の出力先。tasks.md タスク 4.1）へ保存し、親ディレクトリが無ければ
    作成する。直列化は `json.dumps(..., allow_nan=False)` を用いるため、
    `result` のいずれかの数値フィールドが NaN / Infinity を含む場合は
    `ValueError` を送出して保存を拒否する。欠測（`None`）のフィールドは
    キーごと省き、`null` として書かない。

    Args:
        result: 保存するキャリブレーション結果。
        path: 保存先。省略時は既定の出力先を用いる。

    Raises:
        ValueError: `result` が NaN / Infinity を含む数値フィールドを持つ場合
            （`json.dumps(allow_nan=False)` が送出する）。
    """
    target_path = (
        Path(path)
        if path is not None
        else _DEFAULT_OUTPUT_DIR / f"{result.calibration_id}.json"
    )
    target_path.parent.mkdir(parents=True, exist_ok=True)
    data = _result_to_dict(result)
    json_text = json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    )
    target_path.write_text(json_text, encoding="utf-8")


def load_calibration(path: Path) -> CalibrationResult:
    """`path` の JSON ファイルからキャリブレーション結果を読み込む
    （design.md CalibrationResultStore「Contracts」/ 要件 6.1）。

    本関数が行うのは組み立てた JSON の再構成のみである。形式版の未知判定・
    ストリーム設定やカメラ内部パラメータの整合性検査は行わない
    （タスク 4.2 `check_compatibility` の責務）。ただし `WorldTransform` は
    構築時に自身で正規直交性・行列式 +1 を検査するため（`transform.py`
    参照）、破損した回転行列は `CalibrationFailure(ROTATION_NOT_ORTHONORMAL)`
    として自然に検出される。

    Args:
        path: 読み込むファイルのパス。

    Returns:
        再構成した `CalibrationResult`。

    Raises:
        CalibrationConfigError: ファイルが読み込めない、正しい JSON でない、
            または必須キーが欠けている・形式が不正な場合。
    """
    target_path = Path(path)
    try:
        text = target_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CalibrationConfigError(
            f"キャリブレーション結果を読み込めない: {target_path}"
        ) from exc

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CalibrationConfigError(
            f"キャリブレーション結果が正しい JSON でない: {target_path}"
        ) from exc

    if not isinstance(data, Mapping):
        raise CalibrationConfigError(
            f"キャリブレーション結果の最上位が object でない: {target_path}"
        )

    try:
        return _result_from_dict(data)
    except (KeyError, TypeError, ValueError) as exc:
        raise CalibrationConfigError(
            f"キャリブレーション結果の形式が不正: {target_path}"
        ) from exc
