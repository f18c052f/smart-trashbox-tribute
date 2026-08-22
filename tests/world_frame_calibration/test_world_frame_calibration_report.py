"""world_frame_calibration.report（レポート出力: 人間可読・機械可読）の検証
（design.md「L6-L9: 永続化・検証・入口」Reporter コンポーネント契約 /
tasks.md タスク 5.4 / 要件 4.8, 6.5, 10.6）。

タスク 5.4 が確認すること（tasks.md タスク 5.4「観測可能な完了状態」他）:

- 検証レポート（`VerificationReport`）・キャリブレーション結果
  （`CalibrationResult`）・比較差分（`TransformDifference`）のそれぞれに、
  機械可読の dict 表現と人間可読の要約テキストが用意されていること
- 検証レポートの要約テキストの**先頭3行が判定・最大誤差・バイアスの順**で
  あること（tasks.md タスク 5.4 の明示的指示）
- 要約テキストに、判定に用いた許容値と**暫定である旨**が必ず現れること
  （暫定許容値で判定した場合、要約に「暫定」の文字列が現れること。要件
  10.6, `tech.md` 開発標準1）
- 機械可読出力（`report_to_dict`）が非数値（NaN / Infinity）を含まず、
  欠測はキーごと省かれること。JSON へ書き出して再読み込みしても同じ値が
  復元できること（往復の値保存性）
- 本モジュールが `world_frame_calibration.upstream`（未実装）にもロギングにも
  依存しないこと。ロギングが有効/無効であってもレポートの内容が変わらない
  こと（design.md Reporter「Implementation Notes」/ tasks.md タスク 5.4）

このテストファイルは `world_frame_calibration.report` がまだ存在しない時点
では `ModuleNotFoundError` で失敗する（RED）。

ファイル名について: `tests/**` に `__init__.py` が無いため、裸の
`test_report.py` は使わずプレフィックス付きの
`test_world_frame_calibration_report.py` とする（既存タスクの命名規約）。
"""

from __future__ import annotations

import ast
import inspect
import json
import logging
import math
from pathlib import Path

import pytest

from world_frame_calibration.frame import build_world_frame
from world_frame_calibration.plan import PlanLimits, VerificationPointSpec
from world_frame_calibration.transform import WorldTransform, compare_transforms
from world_frame_calibration.types import (
    AnchorObservation,
    AnchorRole,
    Intrinsics,
    PixelRegion,
    Plane,
    PlaneQuality,
    StreamSignature,
    ToleranceSpec,
)
from world_frame_calibration.verify import Verdict, verify_calibration

_DEFAULT_REGION = PixelRegion(x0_px=0, y0_px=0, x1_px=10, y1_px=10)

_IDENTITY_ROTATION = (
    (1.0, 0.0, 0.0),
    (0.0, 1.0, 0.0),
    (0.0, 0.0, 1.0),
)


# ---------------------------------------------------------------------------
# ヘルパ: `test_world_frame_calibration_verify.py` と同じ構成の、素直に手計算
# できる CalibrationResult / 検証点を組み立てる。
#
# plane.normal=(0,0,1), origin=(0,0,1000), x_axis=(1000,0,1000) から
# build_world_frame すると、回転は恒等、平行移動は (0,0,-1000) になる:
#   p_world = p_camera - (0, 0, 1000)
# ---------------------------------------------------------------------------


def _plane() -> Plane:
    quality = PlaneQuality(
        points_considered=5000,
        inlier_count=4500,
        inlier_ratio=0.9,
        residual_abs_p50_mm=1.2,
        residual_abs_p95_mm=3.4,
        residual_rms_mm=1.8,
        frames_used=5,
        incidence_angle_deg=42.0,
        rng_seed=7,
    )
    return Plane(normal=(0.0, 0.0, 1.0), distance_mm=1000.0, quality=quality)


def _anchor(
    label: str, role: AnchorRole, point_camera_mm: tuple[float, float, float]
) -> AnchorObservation:
    return AnchorObservation(
        label=label,
        role=role,
        point_camera_mm=point_camera_mm,
        point_on_plane_mm=point_camera_mm,
        height_above_plane_mm=0.0,
        range_from_camera_mm=float(sum(c * c for c in point_camera_mm)) ** 0.5,
        sample_count=120,
        spread_mm=2.5,
        region=_DEFAULT_REGION,
        frames_used=5,
    )


def _establishment():
    origin_anchor = _anchor("origin", AnchorRole.ORIGIN, (0.0, 0.0, 1000.0))
    x_axis_anchor = _anchor("x_axis", AnchorRole.X_AXIS, (1000.0, 0.0, 1000.0))
    limits = PlanLimits(min_baseline_mm=800.0)
    return build_world_frame(_plane(), origin_anchor, x_axis_anchor, limits)


def _intrinsics() -> Intrinsics:
    return Intrinsics(
        width_px=640,
        height_px=480,
        fx_px=615.0,
        fy_px=615.0,
        ppx_px=320.0,
        ppy_px=240.0,
        model="brown_conrady",
        coeffs=(0.0, 0.0, 0.0, 0.0, 0.0),
    )


def _signature() -> StreamSignature:
    return StreamSignature(
        width_px=640, height_px=480, fps=30, depth_scale_mm=1.0, color_enabled=False
    )


def _calibration_result():
    from world_frame_calibration import result as result_module

    return result_module.assemble_calibration_result(
        establishment=_establishment(),
        source_kind="simulated",
        session_path=None,
        signature=_signature(),
        intrinsics=_intrinsics(),
        plan_digest={"notes": "report test fixture"},
        notes="",
    )


def _verification_point(
    label: str,
    truth_world_mm: tuple[float, float, float],
    truth_source: str = "tape measure",
) -> VerificationPointSpec:
    return VerificationPointSpec(
        label=label,
        region=_DEFAULT_REGION,
        height_band_mm=(0.0, 500.0),
        truth_world_mm=truth_world_mm,
        truth_source=truth_source,
    )


def _observation(label: str, point_camera_mm: tuple[float, float, float]) -> AnchorObservation:
    return _anchor(label, AnchorRole.VERIFICATION, point_camera_mm)


def _provisional_tolerance() -> ToleranceSpec:
    return ToleranceSpec(
        horizontal_mm=50.0,
        vertical_mm=50.0,
        provisional=True,
        source="暫定初期値（実測前）",
    )


def _measured_tolerance() -> ToleranceSpec:
    return ToleranceSpec(
        horizontal_mm=50.0,
        vertical_mm=50.0,
        provisional=False,
        source="2026-08-01 実測（tape measure, n=5）",
    )


def _verification_report(tolerance: ToleranceSpec | None):
    result = _calibration_result()
    spec1 = _verification_point("vp1", (500.0, 300.0, 0.0))
    obs1 = _observation("vp1", (505.0, 298.0, 1000.0))
    spec2 = _verification_point("vp2", (100.0, -100.0, 300.0))
    obs2 = _observation("vp2", (100.0, -100.0, 1310.0))
    return verify_calibration(
        result,
        [(spec1, obs1), (spec2, obs2)],
        expected_baseline_mm=1000.0,
        tolerance=tolerance,
    )


def _verification_report_with_far_bucket(tolerance: ToleranceSpec | None):
    """`_verification_report` に、既定境界の最終帯（`RangeBucket.
    range_hi_mm=math.inf`。`verify.DEFAULT_RANGE_BUCKET_EDGES_MM` の最後の
    境界 4000.0mm 以上）へ入る検証点を1つ加えた専用フィクスチャ。

    共有フィクスチャ `_verification_report` の2点（vp1, vp2）はいずれも
    カメラ距離が約1159mm・約1318mmで、既定境界の `1000-2000mm` 帯に
    収まり、無限帯を一度も生成しない。`test_report_to_dict_omits_infinite_
    range_bucket_upper_bound` は非有限な `range_hi_mm` を持つ `RangeBucket`
    が実際に生成される場合のキー省略を検証する必要があるため、
    `range_from_camera_mm=4500.0`（>= 4000.0mm）の第3独立点 vp3 を加える。
    """
    result = _calibration_result()
    spec1 = _verification_point("vp1", (500.0, 300.0, 0.0))
    obs1 = _observation("vp1", (505.0, 298.0, 1000.0))
    spec2 = _verification_point("vp2", (100.0, -100.0, 300.0))
    obs2 = _observation("vp2", (100.0, -100.0, 1310.0))
    # obs3: point_camera_mm=(0, 0, 4500) -> range_from_camera_mm = 4500.0mm,
    # which lands in the DEFAULT_RANGE_BUCKET_EDGES_MM final unbounded bucket
    # [4000.0, math.inf). truth_world_mm mirrors the identity-rotation,
    # (0, 0, -1000)-translation frame used throughout this fixture module.
    spec3 = _verification_point("vp3", (0.0, 0.0, 3500.0))
    obs3 = _observation("vp3", (0.0, 0.0, 4500.0))
    return verify_calibration(
        result,
        [(spec1, obs1), (spec2, obs2), (spec3, obs3)],
        expected_baseline_mm=1000.0,
        tolerance=tolerance,
    )


def _rotation_about_z_deg(angle_deg: float) -> tuple[tuple[float, float, float], ...]:
    angle_rad = math.radians(angle_deg)
    cos_a, sin_a = math.cos(angle_rad), math.sin(angle_rad)
    return (
        (cos_a, -sin_a, 0.0),
        (sin_a, cos_a, 0.0),
        (0.0, 0.0, 1.0),
    )


def _transform_difference():
    a = WorldTransform(rotation=_IDENTITY_ROTATION, translation_mm=(0.0, 0.0, 0.0))
    b = WorldTransform(
        rotation=_rotation_about_z_deg(12.0), translation_mm=(50.0, -25.0, 5.0)
    )
    return compare_transforms(a, b)


# ---------------------------------------------------------------------------
# VerificationReport: 機械可読 dict
# ---------------------------------------------------------------------------


class TestReportToDict:
    """`report_to_dict` が非数値を含まず、JSON へ書き出して再読み込みしても
    値が保たれること（要件 4.8, 6.5）。
    """

    def test_report_to_dict_survives_allow_nan_false_json_round_trip(self) -> None:
        from world_frame_calibration import report as report_module

        report = _verification_report(_provisional_tolerance())
        data = report_module.report_to_dict(report)

        json_text = json.dumps(data, allow_nan=False, ensure_ascii=False, sort_keys=True)
        reloaded = json.loads(json_text)

        assert reloaded == data

    def test_report_to_dict_omits_tolerance_key_when_not_judged(self) -> None:
        from world_frame_calibration import report as report_module

        report = _verification_report(None)
        data = report_module.report_to_dict(report)

        assert "tolerance" not in data
        assert data["verdict"] == Verdict.NOT_JUDGED.value
        # Raw measured values are still reported unconditionally (要件 4.7).
        assert data["max_error_norm_mm"] == pytest.approx(report.max_error_norm_mm)
        assert data["bias_mm"] == pytest.approx(list(report.bias_mm))

    def test_report_to_dict_omits_infinite_range_bucket_upper_bound(self) -> None:
        from world_frame_calibration import report as report_module

        report = _verification_report_with_far_bucket(_provisional_tolerance())

        # Precondition: the fixture must genuinely produce a RangeBucket with
        # a non-finite range_hi_mm (the DEFAULT_RANGE_BUCKET_EDGES_MM final
        # unbounded bucket). Without this, the assertions below could pass
        # vacuously without ever exercising the math.isfinite guard in
        # `_range_bucket_to_dict` / the "∞" label in `render_verification_text`.
        assert any(not math.isfinite(bucket.range_hi_mm) for bucket in report.range_buckets), (
            "fixture must produce at least one RangeBucket with an infinite "
            "range_hi_mm, otherwise this test does not exercise the "
            "non-finite-bound omission behaviour it claims to verify"
        )

        for bucket, bucket_dict in zip(
            report.range_buckets, report_module.report_to_dict(report)["range_buckets"]
        ):
            if not math.isfinite(bucket.range_hi_mm):
                assert "range_hi_mm" not in bucket_dict
            else:
                assert bucket_dict["range_hi_mm"] == pytest.approx(bucket.range_hi_mm)

        # The human-readable side must render the "∞" label for the same
        # infinite bucket rather than silently dropping the whole line.
        text = report_module.render_verification_text(report)
        assert "∞)" in text

    def test_report_to_dict_rejects_non_finite_values_via_allow_nan_false(self) -> None:
        """`report_to_dict` 自体は非有限値を作らないが、万一 dict に NaN が
        混入した場合でも `json.dumps(allow_nan=False)` がそれを拒否すること
        （task 4.1 の `save_calibration` と同じ discipline）を固定する。
        """
        from world_frame_calibration import report as report_module

        report = _verification_report(_provisional_tolerance())
        data = report_module.report_to_dict(report)
        data["scatter_rms_mm"] = math.nan

        with pytest.raises(ValueError):
            json.dumps(data, allow_nan=False)


# ---------------------------------------------------------------------------
# VerificationReport: 人間可読要約テキスト
# ---------------------------------------------------------------------------


class TestRenderVerificationText:
    """要約テキストの先頭3行に判定・最大誤差・バイアスをこの順で置くこと
    （tasks.md タスク 5.4）。暫定許容値のときは「暫定」が現れること
    （要件 10.6）。
    """

    def test_first_three_lines_are_verdict_then_max_error_then_bias(self) -> None:
        from world_frame_calibration import report as report_module

        report = _verification_report(_provisional_tolerance())
        text = report_module.render_verification_text(report)
        lines = text.splitlines()

        assert len(lines) >= 3
        assert lines[0].startswith("判定")
        assert report.verdict.value in lines[0] or "合格" in lines[0] or "不合格" in lines[0]
        assert lines[1].startswith("最大誤差")
        assert f"{report.max_error_norm_mm:.2f}" in lines[1]
        assert lines[2].startswith("バイアス")

    def test_provisional_tolerance_appears_in_text(self) -> None:
        from world_frame_calibration import report as report_module

        report = _verification_report(_provisional_tolerance())
        text = report_module.render_verification_text(report)

        assert "暫定" in text

    def test_measured_tolerance_does_not_claim_provisional(self) -> None:
        from world_frame_calibration import report as report_module

        report = _verification_report(_measured_tolerance())
        text = report_module.render_verification_text(report)

        assert "実測由来" in text
        assert "暫定" not in text

    def test_not_judged_still_reports_raw_measured_values(self) -> None:
        """許容値が無ければ判定しないが（要件 4.7）、要約テキストには
        実測値そのもの（最大誤差・バイアス）が無条件に現れる。
        """
        from world_frame_calibration import report as report_module

        report = _verification_report(None)
        text = report_module.render_verification_text(report)

        assert "未判定" in text
        assert f"{report.max_error_norm_mm:.2f}" in text
        assert "判定なし" in text

    def test_tolerance_values_and_source_are_included_when_judged(self) -> None:
        from world_frame_calibration import report as report_module

        tolerance = _provisional_tolerance()
        report = _verification_report(tolerance)
        text = report_module.render_verification_text(report)

        assert f"{tolerance.horizontal_mm:.2f}" in text
        assert f"{tolerance.vertical_mm:.2f}" in text
        assert tolerance.source in text


# ---------------------------------------------------------------------------
# CalibrationResult: 機械可読 dict / 人間可読要約
# ---------------------------------------------------------------------------


class TestCalibrationResultReport:
    def test_calibration_result_to_dict_survives_allow_nan_false_json_round_trip(
        self,
    ) -> None:
        from world_frame_calibration import report as report_module

        result = _calibration_result()
        data = report_module.calibration_result_to_dict(result)

        json_text = json.dumps(data, allow_nan=False, ensure_ascii=False, sort_keys=True)
        reloaded = json.loads(json_text)

        assert reloaded == data
        assert data["calibration_id"] == result.calibration_id

    def test_render_calibration_text_includes_identifier_and_verification_state(
        self,
    ) -> None:
        from world_frame_calibration import report as report_module

        result = _calibration_result()
        text = report_module.render_calibration_text(result)

        assert result.calibration_id in text
        assert result.verification_state.value in text

    def test_render_calibration_text_does_not_drop_provisional_flag_when_attached(
        self,
    ) -> None:
        """検証要約が付与された `CalibrationResult` の要約テキストからも、
        暫定であることを黙って落とさない（要件 10.6）。
        """
        from world_frame_calibration import report as report_module
        from world_frame_calibration.result import attach_verification

        result = _calibration_result()
        report = _verification_report(_provisional_tolerance())
        summary = _to_summary_like(report)
        attached = attach_verification(result, summary)

        text = report_module.render_calibration_text(attached)

        assert "暫定" in text


def _to_summary_like(report):
    from world_frame_calibration.verify import to_summary

    return to_summary(report)


# ---------------------------------------------------------------------------
# TransformDifference: 機械可読 dict / 人間可読要約
# ---------------------------------------------------------------------------


class TestTransformDifferenceReport:
    def test_difference_to_dict_matches_dataclass_fields(self) -> None:
        from world_frame_calibration import report as report_module

        diff = _transform_difference()
        data = report_module.difference_to_dict(diff)

        assert data["origin_shift_mm"] == pytest.approx(list(diff.origin_shift_mm))
        assert data["origin_shift_norm_mm"] == pytest.approx(diff.origin_shift_norm_mm)
        assert data["axis_angle_deg"] == pytest.approx(diff.axis_angle_deg)
        assert data["z_axis_angle_deg"] == pytest.approx(diff.z_axis_angle_deg)
        assert data["yaw_angle_deg"] == pytest.approx(diff.yaw_angle_deg)

        json_text = json.dumps(data, allow_nan=False, ensure_ascii=False)
        assert json.loads(json_text) == data

    def test_render_difference_text_reports_origin_and_yaw_reproducibility_reading(
        self,
    ) -> None:
        from world_frame_calibration import report as report_module

        diff = _transform_difference()
        text = report_module.render_difference_text(diff)

        assert f"{diff.origin_shift_norm_mm:.2f}" in text
        assert f"{diff.yaw_angle_deg:.4f}" in text
        assert "平面推定の再現性" in text
        assert "マーカー設置の再現性" in text


# ---------------------------------------------------------------------------
# 上流にもロギングにも依存しない
# ---------------------------------------------------------------------------


class TestNoUpstreamOrLoggingDependency:
    """本モジュールが `world_frame_calibration.upstream`（未実装）にも
    ロギングにも依存しないこと（design.md Reporter「Implementation Notes」/
    tasks.md タスク 5.4）。ログ無効時にレポートの内容が変わらないことを、
    静的な import 検査と実際の出力比較の双方で固定する。
    """

    _FORBIDDEN_SUBSTRINGS = ("upstream", "logging")

    def test_module_does_not_import_upstream_or_logging(self) -> None:
        from world_frame_calibration import report as report_module

        module_path = Path(inspect.getfile(report_module))
        tree = ast.parse(module_path.read_text(encoding="utf-8"))

        imported_names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module is not None:
                    imported_names.append(node.module)
                imported_names.extend(alias.name for alias in node.names)

        lowered = [name.lower() for name in imported_names]
        for forbidden in self._FORBIDDEN_SUBSTRINGS:
            offending = [name for name in lowered if forbidden in name]
            assert offending == [], (
                f"report.py imports something matching forbidden substring "
                f"{forbidden!r}: {offending!r}"
            )

    def test_module_only_imports_from_the_declared_boundary(self) -> None:
        """境界: `types` / `errors` / `verify` / `result` / `transform` 以外の
        自パッケージ内モジュールを import しない。
        """
        from world_frame_calibration import report as report_module

        allowed_wfc_submodules = {"types", "errors", "verify", "result", "transform"}

        module_path = Path(inspect.getfile(report_module))
        tree = ast.parse(module_path.read_text(encoding="utf-8"))

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                if node.module.startswith("world_frame_calibration."):
                    submodule = node.module.split(".", 1)[1].split(".", 1)[0]
                    assert submodule in allowed_wfc_submodules, (
                        f"unexpected intra-package import: {node.module!r}"
                    )
                elif node.module == "world_frame_calibration":
                    # `from world_frame_calibration import result as ...` has
                    # module == "world_frame_calibration" and the submodule
                    # name appears in `node.names` instead.
                    for alias in node.names:
                        assert alias.name in allowed_wfc_submodules, (
                            f"unexpected intra-package import: "
                            f"world_frame_calibration.{alias.name}"
                        )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("world_frame_calibration."):
                        submodule = alias.name.split(".", 1)[1].split(".", 1)[0]
                        assert submodule in allowed_wfc_submodules, (
                            f"unexpected intra-package import: {alias.name!r}"
                        )

    def test_report_generation_is_identical_regardless_of_logging_disabled_state(
        self,
    ) -> None:
        """ログを完全に無効化しても、しなくても、生成されるレポート文字列は
        1バイトも変わらない（レポート生成がロギングという外部状態に触れて
        いないことの振る舞いによる確認）。
        """
        from world_frame_calibration import report as report_module

        report = _verification_report(_provisional_tolerance())
        result = _calibration_result()
        diff = _transform_difference()

        logging.disable(logging.NOTSET)
        try:
            text_before = (
                report_module.render_verification_text(report)
                + report_module.render_calibration_text(result)
                + report_module.render_difference_text(diff)
            )
            dict_before = (
                report_module.report_to_dict(report),
                report_module.calibration_result_to_dict(result),
                report_module.difference_to_dict(diff),
            )

            logging.disable(logging.CRITICAL)
            text_after = (
                report_module.render_verification_text(report)
                + report_module.render_calibration_text(result)
                + report_module.render_difference_text(diff)
            )
            dict_after = (
                report_module.report_to_dict(report),
                report_module.calibration_result_to_dict(result),
                report_module.difference_to_dict(diff),
            )
        finally:
            logging.disable(logging.NOTSET)

        assert text_before == text_after
        assert dict_before == dict_after
