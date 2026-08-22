"""レポート出力（人間可読・機械可読）（design.md「L6-L9: 永続化・検証・入口」
Reporter コンポーネント / tasks.md タスク 5.4 / 要件 4.8, 6.5, 10.6）。

design.md Reporter「Contracts」が定める4関数をそのまま提供する:

- `report_to_dict`: `VerificationReport` の機械可読 dict 表現
- `render_verification_text`: `VerificationReport` の人間可読要約テキスト
- `render_calibration_text`: `CalibrationResult` の人間可読要約テキスト
- `render_difference_text`: `TransformDifference` の人間可読要約テキスト

tasks.md タスク 5.4 は「検証レポート・キャリブレーション結果・比較差分の
**それぞれに**、機械可読の辞書表現と人間可読の要約テキストを用意する」と
要求している。design.md のコード片が明示するのは上記4関数のみ（機械可読
dict は `VerificationReport` の分だけ）だが、これは矛盾ではなく、
`CalibrationResult` の機械可読形式が既にタスク 4.1（`result.save_calibration`
/ 内部の `_result_to_dict`）で確立済みであり、`TransformDifference` が
そもそも列挙・入れ子オブジェクトを持たないフラットな float のみの
dataclass であるため、専用関数が無くても機械可読性そのものは既に
満たされているからである。本モジュールはそれでもタスク 5.4 の文言を
文字どおり満たすため、design.md の4関数に加えて `calibration_result_to_dict`
と `difference_to_dict` を追加公開する（design.md の contract を狭める
のではなく、上乗せする追加）。`calibration_result_to_dict` はタスク 4.1 が
既に確立した `result._result_to_dict`（直列化の単一の真実源）へ委譲し、
2つの変換ロジックが乖離する事故を避ける。

**判定・最大誤差・バイアスを要約テキストの先頭3行に置く**（tasks.md
タスク 5.4: 「要約テキストの先頭3行に判定・最大誤差・バイアスを置く」）:
`render_verification_text` はこの3値をこの順で最初の3行に出す。

**暫定であることを出力から落とさない**（要件 10.6: 「計測した数値を、
実測前の目標値との比較による合否判定に使わない方針を出力上でも守る
（暫定表示を落とさない）」/ `tech.md` 開発標準1）。判定に用いた許容値が
`ToleranceSpec.provisional=True` であれば、要約テキストに必ず「暫定」の
文字列を含める。許容値が与えられていない場合（`tolerance is None`）も、
「判定なし」であることと実測値が無条件に出力されていることを明記する
（要件 4.7 との両立）。`CalibrationResult` に検証要約が付与されている場合
（`render_calibration_text`）も同じ規約を踏襲し、暫定フラグを黙って落とさない。

**上流にもロギングにも依存しない**（design.md Reporter「Implementation
Notes」/ tasks.md タスク 5.4: 「本モジュールは上流にもロギングにも依存しない
（ログ無効時にレポートが変わらないようにする）」）。レポート生成が
ロギングの有効/無効という外部状態に触れると、同じ入力でもログ設定次第で
出力内容が変わりうるという事故につながる（design.md の一般原則、`upstream.py`
の `stage_logger` / `timed` がロギング委譲を専任するのと対称的に、本モジュールは
一切それに触れない）。したがって本モジュールは `logging` を import せず、
`world_frame_calibration.upstream`（タスク 6.1/6.2 でまだ実装されていない
モジュール）にも依存しない。`tests/world_frame_calibration/
test_world_frame_calibration_report.py` の
`TestNoUpstreamOrLoggingDependency` が、静的な import 検査と、
`logging.disable` の有無で出力が変わらないことの双方で固定する。

**機械可読出力は非数値を許さず、欠測はキーごと省く**（design.md Reporter
「Implementation Notes」: 「JSON は `json.dumps(..., allow_nan=False,
ensure_ascii=False)`。欠測はキーごと省く（0 で埋めない）。上流2 Spec と
同じ方針」）。`report_to_dict` が返す dict は NaN / Infinity を一切含まない。
`RangeBucket.range_hi_mm` は既定境界（`DEFAULT_RANGE_BUCKET_EDGES_MM`）の
最終帯で `math.inf` を取りうるが、`math.inf` は Python の `float` としては
有効でも JSON の数値表現ではない（`json.dumps(allow_nan=False)` は NaN と
同様に Infinity も `ValueError` として拒否する）。したがって非有限の
`range_lo_mm` / `range_hi_mm` は「上限（下限）なし」を意味するキー省略として
扱う（`plan.py` / `result.py` が採る「欠測はキーごと省く」の方針を、有限で
ない値にも一貫して適用したもの）。
"""

from __future__ import annotations

import math

from world_frame_calibration import result as result_module
from world_frame_calibration.result import CalibrationResult
from world_frame_calibration.transform import TransformDifference
from world_frame_calibration.types import StreamSignature, ToleranceSpec
from world_frame_calibration.verify import (
    PointVerification,
    RangeBucket,
    ScaleCheck,
    Verdict,
    VerificationReport,
)

__all__ = [
    "calibration_result_to_dict",
    "difference_to_dict",
    "render_calibration_text",
    "render_difference_text",
    "render_verification_text",
    "report_to_dict",
]


# ---------------------------------------------------------------------------
# 共通ヘルパ
# ---------------------------------------------------------------------------


def _drop_none(data: dict[str, object]) -> dict[str, object]:
    """値が `None` のキーを取り除く（欠測はキーごと省く。`plan.py` /
    `result.py` と同じ規約）。"""
    return {key: value for key, value in data.items() if value is not None}


def _fmt_mm(value: float) -> str:
    return f"{value:.2f}mm"


def _fmt_deg(value: float) -> str:
    return f"{value:.4f}deg"


_VERDICT_LABELS: dict[Verdict, str] = {
    Verdict.PASS: "合格 (pass)",
    Verdict.FAIL: "不合格 (fail)",
    Verdict.NOT_JUDGED: "未判定 (not_judged)",
}


def _verdict_label(verdict: Verdict) -> str:
    return _VERDICT_LABELS[verdict]


def _tolerance_line(tolerance: ToleranceSpec | None) -> str:
    """判定に用いた許容値の行を組み立てる。

    **必ず許容値の有無と暫定/実測由来の別を出す**（要件 4.6, 10.6）。
    `tolerance is None` の場合でも「判定していない」ことと「実測値は無条件に
    報告される」ことを明記し、`provisional=True` の場合は必ず「暫定」の
    文字列を含める（暫定表示を落とさない）。
    """
    if tolerance is None:
        return "許容値: 未設定（判定なし。誤差の実測値は無条件に報告する）"
    status = "暫定" if tolerance.provisional else "実測由来"
    return (
        f"許容値: 水平={_fmt_mm(tolerance.horizontal_mm)} "
        f"垂直={_fmt_mm(tolerance.vertical_mm)} ({status}, 出典: {tolerance.source})"
    )


def _signature_to_dict(signature: StreamSignature) -> dict[str, object]:
    return {
        "width_px": signature.width_px,
        "height_px": signature.height_px,
        "fps": signature.fps,
        "depth_scale_mm": signature.depth_scale_mm,
        "color_enabled": signature.color_enabled,
    }


def _tolerance_to_dict(tolerance: ToleranceSpec | None) -> dict[str, object] | None:
    if tolerance is None:
        return None
    return {
        "horizontal_mm": tolerance.horizontal_mm,
        "vertical_mm": tolerance.vertical_mm,
        "provisional": tolerance.provisional,
        "source": tolerance.source,
    }


# ---------------------------------------------------------------------------
# VerificationReport: 機械可読 dict / 人間可読要約
# ---------------------------------------------------------------------------


def _point_verification_to_dict(point: PointVerification) -> dict[str, object]:
    return {
        "label": point.label,
        "independent": point.independent,
        "measured_world_mm": list(point.measured_world_mm),
        "truth_world_mm": list(point.truth_world_mm),
        "truth_source": point.truth_source,
        "error_mm": list(point.error_mm),
        "error_norm_mm": point.error_norm_mm,
        "horizontal_error_mm": point.horizontal_error_mm,
        "vertical_error_mm": point.vertical_error_mm,
        "range_from_camera_mm": point.range_from_camera_mm,
        "spread_mm": point.spread_mm,
        "sample_count": point.sample_count,
        "verdict": point.verdict.value,
    }


def _range_bucket_to_dict(bucket: RangeBucket) -> dict[str, object]:
    """`RangeBucket` を dict 化する。

    `range_lo_mm` / `range_hi_mm` が非有限（`math.inf` 等、既定の最終帯の
    上限に現れる）の場合はキーごと省く（`json.dumps(allow_nan=False)` は
    Infinity も NaN と同様に拒否するため。モジュール docstring 参照）。
    """
    data: dict[str, object] = {
        "point_count": bucket.point_count,
        "mean_error_norm_mm": bucket.mean_error_norm_mm,
        "max_error_norm_mm": bucket.max_error_norm_mm,
    }
    if math.isfinite(bucket.range_lo_mm):
        data["range_lo_mm"] = bucket.range_lo_mm
    if math.isfinite(bucket.range_hi_mm):
        data["range_hi_mm"] = bucket.range_hi_mm
    return data


def _scale_check_to_dict(check: ScaleCheck) -> dict[str, object]:
    data: dict[str, object] = {
        "expected_baseline_mm": check.expected_baseline_mm,
        "measured_baseline_mm": check.measured_baseline_mm,
        "difference_mm": check.difference_mm,
    }
    return _drop_none(data)


def report_to_dict(report: VerificationReport) -> dict[str, object]:
    """`VerificationReport` の機械可読 dict 表現を返す（design.md Reporter
    「Contracts」/ tasks.md タスク 5.4 / 要件 4.8, 6.5）。

    `json.dumps(..., allow_nan=False, ensure_ascii=False)` へそのまま渡せる
    （NaN / Infinity を含まない。`RangeBucket` の非有限な境界値はキーごと
    省く）。欠測（`tolerance is None` 等）はキーごと省く。

    Args:
        report: `verify_calibration`（`verify.py`）が返した検証レポート。

    Returns:
        JSON 互換の dict。`json.loads(json.dumps(report_to_dict(report)))`
        は `report_to_dict(report)` と等しい（往復で値が保たれる）。
    """
    data: dict[str, object] = {
        "calibration_id": report.calibration_id,
        "calibration_format_version": report.calibration_format_version,
        "signature": _signature_to_dict(report.signature),
        "generated_at_wall_ms": report.generated_at_wall_ms,
        "points": [_point_verification_to_dict(point) for point in report.points],
        "independent_point_count": report.independent_point_count,
        "excluded_labels": list(report.excluded_labels),
        "bias_mm": list(report.bias_mm),
        "scatter_rms_mm": report.scatter_rms_mm,
        "max_error_norm_mm": report.max_error_norm_mm,
        "max_horizontal_error_mm": report.max_horizontal_error_mm,
        "max_vertical_error_mm": report.max_vertical_error_mm,
        "range_buckets": [_range_bucket_to_dict(bucket) for bucket in report.range_buckets],
        "scale_check": _scale_check_to_dict(report.scale_check),
        "tolerance": _tolerance_to_dict(report.tolerance),
        "verdict": report.verdict.value,
    }
    return _drop_none(data)


def render_verification_text(report: VerificationReport) -> str:
    """`VerificationReport` の人間可読要約テキストを返す（design.md Reporter
    「Contracts」/ tasks.md タスク 5.4 / 要件 4.8, 4.6, 4.7, 10.6）。

    **先頭3行は判定・最大誤差・バイアスの順に固定する**（tasks.md タスク
    5.4: 「要約テキストの先頭3行に判定・最大誤差・バイアスを置く」。要約が
    長すぎると読まれないため、最も重要な3値を先頭に置く）。

    判定に用いた許容値と、それが暫定か実測由来かを必ず含める（要件 4.6,
    10.6）。許容値が無い場合でも実測値そのものは常に出力される（要件 4.7、
    このテキストの `max_error_norm_mm` / `bias_mm` 等は `tolerance` の
    有無に関わらず常に出す）。

    読み分け規則（バイアス支配なら座標系、ばらつき支配なら観測、遠方だけ
    大きいなら Depth の距離特性。`verify.VerificationStatistics` の docstring
    と同じ規則）を末尾に明記する（design.md Verifier「Implementation
    Notes」）。
    """
    bias_x, bias_y, bias_z = report.bias_mm
    lines = [
        f"判定: {_verdict_label(report.verdict)}",
        (
            "最大誤差: 全体="
            f"{_fmt_mm(report.max_error_norm_mm)} "
            f"水平={_fmt_mm(report.max_horizontal_error_mm)} "
            f"垂直={_fmt_mm(report.max_vertical_error_mm)}"
        ),
        (
            f"バイアス: x={_fmt_mm(bias_x)} y={_fmt_mm(bias_y)} z={_fmt_mm(bias_z)}"
        ),
        f"ばらつき(残差RMS): {_fmt_mm(report.scatter_rms_mm)}",
        _tolerance_line(report.tolerance),
        (
            f"独立点数: {report.independent_point_count} / 全{len(report.points)}点"
        ),
    ]
    if report.excluded_labels:
        lines.append(
            "除外（確立用マーカーと重複、検証にならない）: "
            + ", ".join(report.excluded_labels)
        )
    lines.append(
        f"対象: calibration_id={report.calibration_id} "
        f"format={report.calibration_format_version}"
    )
    if report.scale_check.expected_baseline_mm is not None:
        lines.append(
            "スケール確認: 実測基線長="
            f"{_fmt_mm(report.scale_check.expected_baseline_mm)} "
            f"算出基線長={_fmt_mm(report.scale_check.measured_baseline_mm)} "
            f"差={_fmt_mm(report.scale_check.difference_mm or 0.0)}"
        )
    else:
        lines.append(
            "スケール確認: 実測基線長が未記録のため差分なし（算出基線長="
            f"{_fmt_mm(report.scale_check.measured_baseline_mm)}）"
        )
    for bucket in report.range_buckets:
        hi_label = "∞" if not math.isfinite(bucket.range_hi_mm) else _fmt_mm(bucket.range_hi_mm)
        lines.append(
            f"距離帯 [{_fmt_mm(bucket.range_lo_mm)}, {hi_label}): "
            f"{bucket.point_count}点 平均={_fmt_mm(bucket.mean_error_norm_mm)} "
            f"最大={_fmt_mm(bucket.max_error_norm_mm)}"
        )
    lines.append(
        "読み分け: バイアス支配なら座標系、ばらつき支配なら観測、"
        "遠方だけ大きいなら Depth の距離特性。"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CalibrationResult: 機械可読 dict / 人間可読要約
# ---------------------------------------------------------------------------


def calibration_result_to_dict(result: CalibrationResult) -> dict[str, object]:
    """`CalibrationResult` の機械可読 dict 表現を返す（tasks.md タスク 5.4 /
    要件 4.8）。

    直列化ロジックそのものはタスク 4.1（`result.save_calibration` が使う
    内部の直列化）が既に確立している唯一の真実源であり、本関数はそれへ
    委譲する（2つの変換ロジックが乖離すると、永続化された JSON とレポート
    出力の JSON が異なる値を報告しかねないため、意図的に再実装しない）。
    `json.dumps(..., allow_nan=False, ensure_ascii=False)` へそのまま渡せる。
    """
    return result_module._result_to_dict(result)  # noqa: SLF001


def render_calibration_text(result: CalibrationResult) -> str:
    """`CalibrationResult` の人間可読要約テキストを返す（design.md Reporter
    「Contracts」/ tasks.md タスク 5.4 / 要件 4.8）。

    付与済みの検証要約（`result.verification`）があれば、その許容値と
    暫定/実測由来の別を含める（要件 10.6: 暫定表示を落とさない）。
    """
    lines = [
        f"calibration_id={result.calibration_id} (format={result.calibration_format_version})",
        f"取得: source={result.source_kind} at {result.created_at_wall_ms:.0f}ms",
        f"検証状態: {result.verification_state.value}",
        (
            f"基線長: {_fmt_mm(result.geometry.baseline_mm)} "
            f"ヨー感度: {result.geometry.yaw_sensitivity_deg_per_mm:.6f} deg/mm"
        ),
        (
            "平面推定: 内点率="
            f"{result.plane.quality.inlier_ratio:.3f} "
            f"残差RMS={_fmt_mm(result.plane.quality.residual_rms_mm)} "
            f"入射角={_fmt_deg(result.plane.quality.incidence_angle_deg)}"
        ),
    ]
    if result.verification is not None:
        lines.append(_tolerance_line(result.verification.tolerance))
        lines.append(
            "最後の検証: 最大誤差="
            f"{_fmt_mm(result.verification.max_error_norm_mm)} "
            f"独立点数={result.verification.independent_point_count}"
            f"/{result.verification.point_count}"
        )
    if result.notes:
        lines.append(f"備考: {result.notes}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# TransformDifference: 機械可読 dict / 人間可読要約
# ---------------------------------------------------------------------------


def difference_to_dict(diff: TransformDifference) -> dict[str, object]:
    """`TransformDifference` の機械可読 dict 表現を返す（tasks.md タスク 5.4
    / 要件 4.8, 7.4）。

    `TransformDifference` はすべてのフィールドが常に有限の `float`（または
    その3要素タプル）であり、`None` を取るフィールドを持たない（`欠測」が
    存在しない）ため、キー省略は不要である。
    """
    return {
        "origin_shift_mm": list(diff.origin_shift_mm),
        "origin_shift_norm_mm": diff.origin_shift_norm_mm,
        "axis_angle_deg": diff.axis_angle_deg,
        "z_axis_angle_deg": diff.z_axis_angle_deg,
        "yaw_angle_deg": diff.yaw_angle_deg,
    }


def render_difference_text(diff: TransformDifference) -> str:
    """`TransformDifference` の人間可読要約テキストを返す（design.md Reporter
    「Contracts」/ tasks.md タスク 5.4 / 要件 7.4）。

    Z軸のずれは平面推定の再現性、ヨーのずれはマーカー設置の再現性を表す
    （`result.compare_calibrations` の docstring と同じ読み分け）。
    """
    ox, oy, oz = diff.origin_shift_mm
    lines = [
        (
            f"原点のずれ: {_fmt_mm(diff.origin_shift_norm_mm)} "
            f"(x={_fmt_mm(ox)}, y={_fmt_mm(oy)}, z={_fmt_mm(oz)})"
        ),
        f"全体回転角: {_fmt_deg(diff.axis_angle_deg)}",
        f"Z軸のずれ（平面推定の再現性）: {_fmt_deg(diff.z_axis_angle_deg)}",
        f"ヨーのずれ（マーカー設置の再現性）: {_fmt_deg(diff.yaw_angle_deg)}",
    ]
    return "\n".join(lines)
