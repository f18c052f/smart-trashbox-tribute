"""キャリブレーション計画の読み書きと起動時検証（design.md「L0-L2: 基盤・入力」
CalibrationPlan コンポーネント / tasks.md タスク 1.5 / 要件 1.4, 4.9, 7.1）。

設置者の入力（床探索範囲・原点マーカー・方向マーカー・検証点・下限群・
許容値・メジャー実測の基線長）を1つのファイルとして受け取る
（design.md CalibrationPlan「Intent」）。設置ごとに plan ファイルを
作り直す前提であり（design.md CalibrationPlan「Risks」）、`procedure.md`
がその運用を明記する。

**検証は本モジュールの責務**（`types.py` と異なる）。`GeoTypes`（`types.py`）
の値オブジェクトは構築時に一切検証しないが、`CalibrationPlan` は
設置者が手で編集する入力ファイルの入口であるため、`load_plan` が
**起動時に**（`CalibrationFailure` ではなく）`CalibrationConfigError` を
送出して不正な設定を弾く（design.md Errors「`CalibrationConfigError`:
呼び出し方・`CalibrationPlan` の誤り」/ design.md CalibrationPlan
「Batch / Job Contract」）。座標系が定まる／定まらないの判断（縮退条件）
はここでは行わない。それは `CalibrationFailure` の責務であり、平面推定・
マーカー観測が実際に行われる後続コンポーネントが送出する。

起動時に拒否する4条件（tasks.md タスク 1.5）:

1. `plan_format_version` が `PLAN_FORMAT_VERSION` と一致しない（未知の形式版）
2. 画素範囲（`floor_region` / 各マーカー・検証点の `region`）が画像外
   （負の座標を含む）または空（`x1_px <= x0_px` または `y1_px <= y0_px`）
   である。`CalibrationPlan` は実際の画像幅・高さを保持しない（Depth
   ストリームの解像度は実行時にしか分からない）ため、「画像外」は
   「どんな画像に対しても外側になり得る」という保守的な意味、すなわち
   **原点が非負であること**として検証する
3. `height_band_mm` の下限が上限以上である
4. `verification_points` のラベルが重複している

これら4条件は `load_plan` からのみ検査され、`save_plan` は検証しない
（design.md CalibrationPlan「Batch / Job Contract」の `Input / validation`
は `load_plan` 側の契約として記述されている）。これにより、意図的に
不正な plan ファイルを用意して `load_plan` の拒否を確認するテストが、
`save_plan` を経由せず直接ファイルを書いて検証できる。

直列化は標準ライブラリ `json` を用いる（design.md「Technology Stack」）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from world_frame_calibration.errors import CalibrationConfigError
from world_frame_calibration.types import PixelRegion, ToleranceSpec

__all__ = [
    "PLAN_FORMAT_VERSION",
    "AnchorSpec",
    "VerificationPointSpec",
    "PlanLimits",
    "CalibrationPlan",
    "load_plan",
    "save_plan",
]

PLAN_FORMAT_VERSION = "1.0"
"""現在の plan ファイル形式の版。`load_plan` はこれと一致しない
`plan_format_version` を持つファイルを必ず拒否する（未知版を静かに
読み進めない。tasks.md タスク 1.5 / 要件 7.1）。"""


@dataclass(frozen=True, slots=True)
class AnchorSpec:
    """原点マーカー・方向マーカーの探索範囲と高さ帯の仕様。

    Attributes:
        label: マーカーの識別ラベル。
        region: マーカーの探索に用いる画素範囲。
        height_band_mm: 床平面からの高さの下限・上限（mm）。マーカーは
            周囲の床面から区別できる高さを持つ物体であればよく
            （要件 2.6）、この帯で観測点を床面のノイズから区別する。
    """

    label: str
    region: PixelRegion
    height_band_mm: tuple[float, float]


@dataclass(frozen=True, slots=True)
class VerificationPointSpec:
    """独立検証に用いる検証点の仕様（要件 4）。

    検証点の既知座標（`truth_world_mm`）はメジャー実測値であり、World frame
    の確立には使わない独立な点である（要件 4.3）。`truth_source` は
    その実測手段の記録であり、必須フィールド（デフォルト値なし）として
    定義することで、実測手段を記録し忘れた検証点を構築時点で拒否する
    （tasks.md タスク 1.5:「実測手段の記録が必須であることを型で強制する」）。
    `GeoTypes` の値オブジェクトと同様に、フィールドの**内容**（実測手段の
    説明として妥当かどうか）までは検証しない。強制するのはあくまで
    「フィールドが存在すること」であり、これは他の必須 `str` フィールド
    （例: `types.ToleranceSpec.source`）と同じ規約である。

    Attributes:
        label: 検証点の識別ラベル。`CalibrationPlan.verification_points`
            の中で重複してはならない（`load_plan` が起動時に拒否する）。
        region: 検証点の探索に用いる画素範囲。
        height_band_mm: 床平面からの高さの下限・上限（mm）。
        truth_world_mm: メジャー実測による既知の World 座標。床上の
            検証点であれば z 成分は 0（要件 4.9 は、床上の検証点に加えて
            既知の高さに置いた検証点も扱えることを求める）。
        truth_source: 実測手段の記録（例: 「巻尺実測」「三脚高さ計測」）。
            省略不可。
    """

    label: str
    region: PixelRegion
    height_band_mm: tuple[float, float]
    truth_world_mm: tuple[float, float, float]
    truth_source: str


@dataclass(frozen=True, slots=True)
class PlanLimits:
    """縮退条件の判定に用いる下限群（design.md CalibrationPlan「Validation」）。

    すべての既定値は**暫定**であり、実測データが蓄積された時点で
    見直す前提である（`tech.md` 開発標準1: 未実測の数値を合否条件に
    しない、との両立のため「暫定であること」自体を明記する）。

    `min_baseline_mm` の既定値 800.0 の導出根拠（tasks.md タスク 1.5 が
    docstring への明記を要求する暫定値）:

        原点マーカーと方向マーカーの観測位置には、それぞれ**観測誤差
        約 10 mm** 程度のばらつきがあり得る。基線長（マーカー間距離）が
        短いほど、この誤差が +X 軸の向きへ与える角度誤差（ヨー誤差）が
        増幅される（`FrameGeometry.yaw_sensitivity_deg_per_mm` /
        design.md GeoTypes「Risks」）。目安として、**距離 2 m（2000 mm）
        先で横ずれ約 25 mm** となる基線長を下限として置いた。これは
        `research.md`（ヨー誤差の増幅とマーカー間距離の下限）が示す
        概算であり、実測による裏付けはまだない暫定値である。

    Attributes:
        min_inlier_points: 平面推定に必要な最小の有効点数（要件 1.5）。
        min_inlier_ratio: 平面推定に必要な最小の内点率（要件 1.5）。
        plane_inlier_threshold_mm: 平面までの距離残差がこの値以内の点を
            内点として扱う閾値（mm）。
        min_incidence_angle_deg: 光軸と床面のなす入射角の下限（deg）。
            これを下回ると床面を見込む角度が浅すぎるとして失敗させる
            （要件 5.4）。
        min_baseline_mm: 原点マーカーと方向マーカーの床平面上の距離の
            下限（mm）。上記の導出根拠を参照。
        min_anchor_samples: マーカー1点の代表点算出に必要な最小サンプル数。
        ransac_success_probability: RANSAC の反復回数を導く成功確率
            `p`（`N = log(1 - p) / log(1 - (1 - e)^3)`。design.md
            FloorPlaneEstimator「Responsibilities」: 固定値を埋め込まない）。
        ransac_outlier_ratio: RANSAC の反復回数を導く外れ値率 `e`。
        rng_seed: RANSAC 等の乱数種。同一シードで常に同一の平面を返す
            再現性の担保に用いる（design.md FloorPlaneEstimator
            「Invariants」/ 要件 9.4）。
    """

    min_inlier_points: int = 2000
    min_inlier_ratio: float = 0.5
    plane_inlier_threshold_mm: float = 15.0
    min_incidence_angle_deg: float = 10.0
    min_baseline_mm: float = 800.0
    min_anchor_samples: int = 50
    ransac_success_probability: float = 0.999
    ransac_outlier_ratio: float = 0.5
    rng_seed: int = 0


@dataclass(frozen=True, slots=True)
class CalibrationPlan:
    """設置者の入力を1つにまとめたキャリブレーション計画
    （design.md CalibrationPlan「Intent」/ 要件 1.4, 4.9, 7.1）。

    `load_plan` を経由して読み込んだインスタンスは、常に起動時検証
    （形式版・範囲・高さ帯・検証点ラベル重複）を通過済みである。
    直接構築する場合（テストなど）は検証を経ないため、不正な値の
    まま使用できてしまう点に注意する。

    Attributes:
        plan_format_version: この計画ファイルの形式版。`load_plan` は
            `PLAN_FORMAT_VERSION` と一致しない場合に拒否する。
        floor_region: 床平面の探索範囲。
        origin_anchor: 原点マーカーの仕様。
        x_axis_anchor: 方向マーカー（+X 軸）の仕様。
        verification_points: 独立検証に用いる検証点の仕様の並び。
        limits: 縮退条件の判定に用いる下限群。
        tolerance: 独立検証の合否判定に用いる許容値。未指定なら
            `None`（要件 4.7: 許容値が無くても誤差の実測値は報告する）。
        expected_baseline_mm: 原点マーカーと方向マーカーの距離の
            メジャー実測値。World frame の**定義には使わず**、スケールの
            独立確認にのみ用いる（design.md CalibrationPlan
            「Implementation Notes」）。未実測なら `None`。
        notes: 設置に関する自由記述のメモ。
    """

    plan_format_version: str
    floor_region: PixelRegion
    origin_anchor: AnchorSpec
    x_axis_anchor: AnchorSpec
    verification_points: tuple[VerificationPointSpec, ...]
    limits: PlanLimits
    tolerance: ToleranceSpec | None
    expected_baseline_mm: float | None
    notes: str


# ---------------------------------------------------------------------------
# 直列化: dataclass <-> JSON 互換 dict
# ---------------------------------------------------------------------------


def _region_to_dict(region: PixelRegion) -> dict[str, int]:
    return {
        "x0_px": region.x0_px,
        "y0_px": region.y0_px,
        "x1_px": region.x1_px,
        "y1_px": region.y1_px,
    }


def _region_from_dict(data: object) -> PixelRegion:
    if not isinstance(data, Mapping):
        raise CalibrationConfigError(f"画素範囲の形式が不正（object ではない）: {data!r}")
    try:
        return PixelRegion(
            x0_px=int(data["x0_px"]),
            y0_px=int(data["y0_px"]),
            x1_px=int(data["x1_px"]),
            y1_px=int(data["y1_px"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CalibrationConfigError(f"画素範囲の形式が不正: {data!r}") from exc


def _height_band_from_dict(data: object, *, what: str) -> tuple[float, float]:
    try:
        lower, upper = data  # type: ignore[misc]
        return (float(lower), float(upper))
    except (TypeError, ValueError) as exc:
        raise CalibrationConfigError(f"{what} の形式が不正: {data!r}") from exc


def _anchor_to_dict(anchor: AnchorSpec) -> dict[str, object]:
    return {
        "label": anchor.label,
        "region": _region_to_dict(anchor.region),
        "height_band_mm": list(anchor.height_band_mm),
    }


def _anchor_from_dict(data: object, *, what: str) -> AnchorSpec:
    if not isinstance(data, Mapping):
        raise CalibrationConfigError(f"{what} の形式が不正（object ではない）: {data!r}")
    try:
        label = str(data["label"])
        region = _region_from_dict(data["region"])
        height_band_mm = _height_band_from_dict(
            data["height_band_mm"], what=f"{what}.height_band_mm"
        )
    except KeyError as exc:
        raise CalibrationConfigError(f"{what} に必須キーが欠けている: {exc}") from exc
    return AnchorSpec(label=label, region=region, height_band_mm=height_band_mm)


def _verification_point_to_dict(vp: VerificationPointSpec) -> dict[str, object]:
    return {
        "label": vp.label,
        "region": _region_to_dict(vp.region),
        "height_band_mm": list(vp.height_band_mm),
        "truth_world_mm": list(vp.truth_world_mm),
        "truth_source": vp.truth_source,
    }


def _verification_point_from_dict(data: object, *, index: int) -> VerificationPointSpec:
    what = f"verification_points[{index}]"
    if not isinstance(data, Mapping):
        raise CalibrationConfigError(f"{what} の形式が不正（object ではない）: {data!r}")
    try:
        label = str(data["label"])
        region = _region_from_dict(data["region"])
        height_band_mm = _height_band_from_dict(
            data["height_band_mm"], what=f"{what}.height_band_mm"
        )
        tx, ty, tz = data["truth_world_mm"]
        truth_world_mm = (float(tx), float(ty), float(tz))
        truth_source = str(data["truth_source"])
    except KeyError as exc:
        raise CalibrationConfigError(f"{what} に必須キーが欠けている: {exc}") from exc
    except (TypeError, ValueError) as exc:
        raise CalibrationConfigError(f"{what}.truth_world_mm の形式が不正: {data!r}") from exc
    return VerificationPointSpec(
        label=label,
        region=region,
        height_band_mm=height_band_mm,
        truth_world_mm=truth_world_mm,
        truth_source=truth_source,
    )


def _limits_to_dict(limits: PlanLimits) -> dict[str, object]:
    return {
        "min_inlier_points": limits.min_inlier_points,
        "min_inlier_ratio": limits.min_inlier_ratio,
        "plane_inlier_threshold_mm": limits.plane_inlier_threshold_mm,
        "min_incidence_angle_deg": limits.min_incidence_angle_deg,
        "min_baseline_mm": limits.min_baseline_mm,
        "min_anchor_samples": limits.min_anchor_samples,
        "ransac_success_probability": limits.ransac_success_probability,
        "ransac_outlier_ratio": limits.ransac_outlier_ratio,
        "rng_seed": limits.rng_seed,
    }


def _limits_from_dict(data: object) -> PlanLimits:
    if not isinstance(data, Mapping):
        raise CalibrationConfigError(f"limits の形式が不正（object ではない）: {data!r}")
    defaults = PlanLimits()
    try:
        return PlanLimits(
            min_inlier_points=int(data.get("min_inlier_points", defaults.min_inlier_points)),
            min_inlier_ratio=float(data.get("min_inlier_ratio", defaults.min_inlier_ratio)),
            plane_inlier_threshold_mm=float(
                data.get("plane_inlier_threshold_mm", defaults.plane_inlier_threshold_mm)
            ),
            min_incidence_angle_deg=float(
                data.get("min_incidence_angle_deg", defaults.min_incidence_angle_deg)
            ),
            min_baseline_mm=float(data.get("min_baseline_mm", defaults.min_baseline_mm)),
            min_anchor_samples=int(
                data.get("min_anchor_samples", defaults.min_anchor_samples)
            ),
            ransac_success_probability=float(
                data.get("ransac_success_probability", defaults.ransac_success_probability)
            ),
            ransac_outlier_ratio=float(
                data.get("ransac_outlier_ratio", defaults.ransac_outlier_ratio)
            ),
            rng_seed=int(data.get("rng_seed", defaults.rng_seed)),
        )
    except (TypeError, ValueError) as exc:
        raise CalibrationConfigError(f"limits の形式が不正: {data!r}") from exc


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
        raise CalibrationConfigError(f"tolerance の形式が不正（object ではない）: {data!r}")
    try:
        return ToleranceSpec(
            horizontal_mm=float(data["horizontal_mm"]),
            vertical_mm=float(data["vertical_mm"]),
            provisional=bool(data["provisional"]),
            source=str(data["source"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CalibrationConfigError(f"tolerance の形式が不正: {data!r}") from exc


def _plan_to_dict(plan: CalibrationPlan) -> dict[str, object]:
    return {
        "plan_format_version": plan.plan_format_version,
        "floor_region": _region_to_dict(plan.floor_region),
        "origin_anchor": _anchor_to_dict(plan.origin_anchor),
        "x_axis_anchor": _anchor_to_dict(plan.x_axis_anchor),
        "verification_points": [
            _verification_point_to_dict(vp) for vp in plan.verification_points
        ],
        "limits": _limits_to_dict(plan.limits),
        "tolerance": _tolerance_to_dict(plan.tolerance),
        "expected_baseline_mm": plan.expected_baseline_mm,
        "notes": plan.notes,
    }


def _plan_from_dict(data: Mapping[str, object]) -> CalibrationPlan:
    try:
        verification_points_raw = data["verification_points"]
        if not isinstance(verification_points_raw, (list, tuple)):
            raise CalibrationConfigError(
                f"verification_points の形式が不正（配列ではない）: {verification_points_raw!r}"
            )
        verification_points = tuple(
            _verification_point_from_dict(vp, index=index)
            for index, vp in enumerate(verification_points_raw)
        )
        expected_baseline_raw = data.get("expected_baseline_mm")
        expected_baseline_mm = (
            None if expected_baseline_raw is None else float(expected_baseline_raw)  # type: ignore[arg-type]
        )
        return CalibrationPlan(
            plan_format_version=str(data["plan_format_version"]),
            floor_region=_region_from_dict(data["floor_region"]),
            origin_anchor=_anchor_from_dict(data["origin_anchor"], what="origin_anchor"),
            x_axis_anchor=_anchor_from_dict(data["x_axis_anchor"], what="x_axis_anchor"),
            verification_points=verification_points,
            limits=_limits_from_dict(data["limits"]),
            tolerance=_tolerance_from_dict(data.get("tolerance")),
            expected_baseline_mm=expected_baseline_mm,
            notes=str(data.get("notes", "")),
        )
    except KeyError as exc:
        raise CalibrationConfigError(f"計画ファイルに必須キーが欠けている: {exc}") from exc
    except (TypeError, ValueError) as exc:
        raise CalibrationConfigError(f"計画ファイルの形式が不正: {exc}") from exc


# ---------------------------------------------------------------------------
# 起動時検証（tasks.md タスク 1.5 / design.md CalibrationPlan
# 「Batch / Job Contract」の `Input / validation`）
# ---------------------------------------------------------------------------


def _validate_region(region: PixelRegion, *, what: str) -> None:
    """`region` が画像外・空でないことを検証する。

    `CalibrationPlan` は実際の画像幅・高さを保持しないため、「画像外」は
    「原点が非負であること」として検証する（モジュール docstring参照）。
    """
    if region.x0_px < 0 or region.y0_px < 0:
        raise CalibrationConfigError(
            f"{what} の画素範囲が画像外を指している（原点が負）: {region!r}"
        )
    if region.x1_px <= region.x0_px or region.y1_px <= region.y0_px:
        raise CalibrationConfigError(f"{what} の画素範囲が空である: {region!r}")


def _validate_height_band(height_band_mm: tuple[float, float], *, what: str) -> None:
    lower, upper = height_band_mm
    if lower >= upper:
        raise CalibrationConfigError(
            f"{what} の下限が上限以上である（下限={lower}, 上限={upper}）"
        )


def _validate_plan(plan: CalibrationPlan) -> None:
    """`load_plan` が読み込んだ内容を起動時に検証する。

    tasks.md タスク 1.5 が定める4条件のうち、形式版の一致は
    `load_plan` が本関数の呼び出し前に検査済みである（早期に失敗させ、
    形式が一致しない残りのフィールドを無駄にパースしないため）。
    本関数は残る3条件（範囲・高さ帯・検証点ラベル重複）を検証する。
    """
    _validate_region(plan.floor_region, what="floor_region")

    _validate_region(plan.origin_anchor.region, what="origin_anchor.region")
    _validate_height_band(
        plan.origin_anchor.height_band_mm, what="origin_anchor.height_band_mm"
    )

    _validate_region(plan.x_axis_anchor.region, what="x_axis_anchor.region")
    _validate_height_band(
        plan.x_axis_anchor.height_band_mm, what="x_axis_anchor.height_band_mm"
    )

    seen_labels: set[str] = set()
    for vp in plan.verification_points:
        _validate_region(vp.region, what=f"verification_points[{vp.label!r}].region")
        _validate_height_band(
            vp.height_band_mm, what=f"verification_points[{vp.label!r}].height_band_mm"
        )
        if vp.label in seen_labels:
            raise CalibrationConfigError(
                f"verification_points のラベルが重複している: {vp.label!r}"
            )
        seen_labels.add(vp.label)


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------


def load_plan(path: Path) -> CalibrationPlan:
    """`path` の JSON ファイルからキャリブレーション計画を読み込む。

    起動時に以下を検証し、いずれかに該当すれば `CalibrationConfigError`
    を送出する（`CalibrationFailure` ではない。モジュール docstring
    参照）:

    - `plan_format_version` が `PLAN_FORMAT_VERSION` と一致しない
    - 画素範囲（`floor_region` / 各マーカー・検証点の `region`）が
      画像外または空
    - `height_band_mm` の下限が上限以上
    - `verification_points` のラベルが重複している

    Preconditions:
        `path` が存在し、UTF-8 の JSON ファイルであること。
    """
    target_path = Path(path)
    try:
        text = target_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CalibrationConfigError(f"計画ファイルを読み込めない: {target_path}") from exc

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CalibrationConfigError(
            f"計画ファイルが正しい JSON でない: {target_path}"
        ) from exc

    if not isinstance(data, Mapping):
        raise CalibrationConfigError(
            f"計画ファイルの最上位が object でない: {target_path}"
        )

    format_version = data.get("plan_format_version")
    if format_version != PLAN_FORMAT_VERSION:
        raise CalibrationConfigError(
            "未知の計画形式版を読み込もうとした: "
            f"{format_version!r}（対応する形式版: {PLAN_FORMAT_VERSION!r}, "
            f"path={target_path}）"
        )

    plan = _plan_from_dict(data)
    _validate_plan(plan)
    return plan


def save_plan(plan: CalibrationPlan, path: Path) -> None:
    """キャリブレーション計画を `path` へ JSON として書き出す。

    起動時検証は行わない（design.md CalibrationPlan「Batch / Job
    Contract」: 検証は読み込み側の契約。モジュール docstring参照）。
    既存ファイルは上書きする。
    """
    target_path = Path(path)
    plan_dict = _plan_to_dict(plan)
    json_text = json.dumps(
        plan_dict,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    )
    target_path.write_text(json_text, encoding="utf-8")
