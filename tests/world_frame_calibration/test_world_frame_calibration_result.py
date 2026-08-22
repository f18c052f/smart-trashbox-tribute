"""world_frame_calibration.result（キャリブレーション結果の組み立てと直列化）の
検証（tasks.md タスク 4.1 / 要件 6.1, 6.2, 6.6）。

design.md「L6-L9: 永続化・検証・入口」節の CalibrationResultStore コンポーネント
契約のうち、本タスクが担う部分（組み立て・直列化・`VerificationState`）を固定する。
本テストが確認すること（tasks.md タスク 4.1「観測可能な完了状態」）:

- `CalibrationResult` が形式版・一意識別子・生成時刻・入力元種別と再生対象・
  ストリーム識別情報・内部パラメータ・平面と推定品質・変換と frame 幾何・
  2つのマーカー観測・計画の要約・備考を1つに束ねること
- 保存 → 読み込みで**同一の変換が再現される**こと（`WorldTransform.apply_point`
  が往復で完全一致することを直接確認する）
- 直列化が非数値（NaN）を許さないこと（`allow_nan=False`）
- 欠測（`session_path` / `verification` / その内部の `tolerance` /
  `report_path`）はキーごと省かれ、`null` として書かれないこと
- 検証結果は未付与（`None`）の状態で組み立てられ、`verification_state` が
  `VerificationState.NOT_VERIFIED` という明示的な値として読み取れること
  （要件 6.6: 未検証であることが「たまたま None」ではなく判別可能な状態で
  あることを固定する）
- `VerificationState` が `NOT_VERIFIED` 以外に `PASSED` / `FAILED` /
  `NOT_JUDGED` の状態を持ち、検証要約が付与された結果でも保存 → 読み込みで
  区別可能なまま往復すること（読み込み・整合性検査そのものはタスク 4.2 の
  範囲であり、本テストは検証要約を手動で付与した `CalibrationResult` の
  直列化契約のみを固定する）
- 出力先の既定が `var/calibration/` であること

このテストファイルは `world_frame_calibration.result` がまだ存在しない時点では
`ModuleNotFoundError` で失敗する（RED）。

ファイル名について: `tests/**` に `__init__.py` が無いため、裸の
`test_result.py` は使わずプレフィックス付きの
`test_world_frame_calibration_result.py` とする（既存タスクの命名規約）。
"""

from __future__ import annotations

import dataclasses
import json
import math
from pathlib import Path

import pytest

from world_frame_calibration.errors import CalibrationConfigError
from world_frame_calibration.frame import build_world_frame
from world_frame_calibration.plan import PlanLimits
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

_DEFAULT_REGION = PixelRegion(x0_px=0, y0_px=0, x1_px=10, y1_px=10)


def _plane(normal: tuple[float, float, float], distance_mm: float) -> Plane:
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
    return Plane(normal=normal, distance_mm=distance_mm, quality=quality)


def _anchor(
    label: str, role: AnchorRole, point_on_plane_mm: tuple[float, float, float]
) -> AnchorObservation:
    return AnchorObservation(
        label=label,
        role=role,
        point_camera_mm=point_on_plane_mm,
        point_on_plane_mm=point_on_plane_mm,
        height_above_plane_mm=0.0,
        range_from_camera_mm=float(
            math.sqrt(sum(c * c for c in point_on_plane_mm))
        ),
        sample_count=120,
        spread_mm=2.5,
        region=_DEFAULT_REGION,
        frames_used=5,
    )


def _establishment():
    plane = _plane(normal=(0.0, 0.0, 1.0), distance_mm=1000.0)
    origin_anchor = _anchor("origin", AnchorRole.ORIGIN, (0.0, 0.0, 1000.0))
    x_axis_anchor = _anchor("x_axis", AnchorRole.X_AXIS, (1000.0, 0.0, 1000.0))
    limits = PlanLimits(min_baseline_mm=800.0)
    return build_world_frame(plane, origin_anchor, x_axis_anchor, limits)


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


def _plan_digest() -> dict[str, object]:
    return {
        "floor_region": {"x0_px": 0, "y0_px": 0, "x1_px": 640, "y1_px": 480},
        "min_baseline_mm": 800.0,
        "notes": "設置A: 待機位置=原点マーカー",
    }


def _make_result(module, **overrides: object):
    establishment = overrides.pop("establishment", None) or _establishment()
    kwargs: dict[str, object] = dict(
        establishment=establishment,
        source_kind="simulated",
        session_path=None,
        signature=_signature(),
        intrinsics=_intrinsics(),
        plan_digest=_plan_digest(),
        notes="",
    )
    kwargs.update(overrides)
    return module.assemble_calibration_result(**kwargs)  # type: ignore[arg-type]


class TestCalibrationFormatVersionConstant:
    def test_calibration_format_version_is_exactly_one_point_zero(self) -> None:
        from world_frame_calibration import result as result_module

        assert result_module.CALIBRATION_FORMAT_VERSION == "1.0"


class TestVerificationStateHasFourDistinctValues:
    """`VerificationState` は未検証と3つの検証結果状態を判別可能な値として
    持つ（design.md CalibrationResultStore「Contracts」/ 要件 6.6）。
    """

    def test_has_not_verified_passed_failed_not_judged(self) -> None:
        from world_frame_calibration import result as result_module

        assert result_module.VerificationState.NOT_VERIFIED == "not_verified"
        assert result_module.VerificationState.PASSED == "passed"
        assert result_module.VerificationState.FAILED == "failed"
        assert result_module.VerificationState.NOT_JUDGED == "not_judged"

    def test_all_four_values_are_distinct(self) -> None:
        from world_frame_calibration import result as result_module

        values = {
            result_module.VerificationState.NOT_VERIFIED,
            result_module.VerificationState.PASSED,
            result_module.VerificationState.FAILED,
            result_module.VerificationState.NOT_JUDGED,
        }
        assert len(values) == 4


class TestCalibrationResultAssembly:
    """`assemble_calibration_result` が確立結果と付随メタデータを1つの
    `CalibrationResult` へ束ねる（tasks.md タスク 4.1 / 要件 6.1, 6.2）。
    """

    def test_bundles_establishment_and_metadata(self) -> None:
        from world_frame_calibration import result as result_module

        establishment = _establishment()
        intrinsics = _intrinsics()
        signature = _signature()
        digest = _plan_digest()

        r = _make_result(
            result_module,
            establishment=establishment,
            source_kind="recorded",
            session_path="sessions/2026-08-22/session-01",
            signature=signature,
            intrinsics=intrinsics,
            plan_digest=digest,
            notes="設置A",
        )

        assert r.calibration_format_version == result_module.CALIBRATION_FORMAT_VERSION
        assert isinstance(r.calibration_id, str) and r.calibration_id
        assert isinstance(r.created_at_wall_ms, float) and r.created_at_wall_ms > 0
        assert r.source_kind == "recorded"
        assert r.session_path == "sessions/2026-08-22/session-01"
        assert r.signature == signature
        assert r.intrinsics == intrinsics
        assert r.plane == establishment.plane
        assert r.transform == establishment.transform
        assert r.geometry == establishment.geometry
        assert r.origin_anchor == establishment.origin_anchor
        assert r.x_axis_anchor == establishment.x_axis_anchor
        assert dict(r.plan_digest) == digest
        assert r.notes == "設置A"

    def test_freshly_assembled_result_has_no_verification(self) -> None:
        from world_frame_calibration import result as result_module

        r = _make_result(result_module)

        assert r.verification is None
        assert r.verification_state == result_module.VerificationState.NOT_VERIFIED

    def test_two_assemblies_get_distinct_calibration_ids(self) -> None:
        from world_frame_calibration import result as result_module

        r1 = _make_result(result_module)
        r2 = _make_result(result_module)

        assert r1.calibration_id != r2.calibration_id

    def test_calibration_result_is_frozen_slots_dataclass(self) -> None:
        from world_frame_calibration import result as result_module

        assert dataclasses.is_dataclass(result_module.CalibrationResult)
        assert result_module.CalibrationResult.__dataclass_params__.frozen is True
        r = _make_result(result_module)
        with pytest.raises(AttributeError):
            r.__dict__  # noqa: B018 -- intentionally probing for absence


class TestSaveLoadRoundTrip:
    """保存 → 読み込みで同一の変換が再現される
    （design.md CalibrationResultStore 観察可能な完了状態 / 要件 6.1）。
    """

    def test_round_trip_reproduces_identical_transform(self, tmp_path: Path) -> None:
        from world_frame_calibration import result as result_module

        original = _make_result(result_module)
        path = tmp_path / "calib.json"
        result_module.save_calibration(original, path)
        loaded = result_module.load_calibration(path)

        sample_points = [
            (0.0, 0.0, 1000.0),
            (1000.0, 0.0, 1000.0),
            (300.0, 450.0, 1200.0),
            (-250.0, 900.0, 800.0),
        ]
        for point in sample_points:
            assert loaded.transform.apply_point(point) == original.transform.apply_point(
                point
            )

    def test_round_trip_preserves_full_equality_for_unverified_result(
        self, tmp_path: Path
    ) -> None:
        from world_frame_calibration import result as result_module

        original = _make_result(result_module)
        path = tmp_path / "calib.json"
        result_module.save_calibration(original, path)
        loaded = result_module.load_calibration(path)

        assert loaded == original
        assert loaded.verification_state == result_module.VerificationState.NOT_VERIFIED

    def test_round_trip_preserves_session_path_when_present(self, tmp_path: Path) -> None:
        from world_frame_calibration import result as result_module

        original = _make_result(
            result_module, source_kind="recorded", session_path="sessions/foo"
        )
        path = tmp_path / "calib.json"
        result_module.save_calibration(original, path)
        loaded = result_module.load_calibration(path)

        assert loaded.session_path == "sessions/foo"
        assert loaded == original

    def test_round_trip_with_attached_verification_summaries(self, tmp_path: Path) -> None:
        """検証要約が付与された結果も、状態を保ったまま往復する
        （検証要約の付与そのものはタスク 4.2 `attach_verification` が持つが、
        本タスクは `CalibrationResult.verification` フィールドの直列化契約
        を固定する）。
        """
        from world_frame_calibration import result as result_module

        base = _make_result(result_module)

        for verdict in (
            result_module.VerificationState.PASSED,
            result_module.VerificationState.FAILED,
            result_module.VerificationState.NOT_JUDGED,
        ):
            summary = result_module.VerificationSummary(
                verified_at_wall_ms=1_700_000_000_000.0,
                verdict=verdict,
                point_count=5,
                independent_point_count=3,
                bias_mm=(1.5, -2.0, 0.5),
                scatter_rms_mm=3.2,
                max_error_norm_mm=6.7,
                tolerance=ToleranceSpec(
                    horizontal_mm=20.0,
                    vertical_mm=15.0,
                    provisional=True,
                    source="暫定値",
                ),
                report_path="var/calibration/report.json",
            )
            result_with_verification = dataclasses.replace(base, verification=summary)

            path = tmp_path / f"calib_{verdict.value}.json"
            result_module.save_calibration(result_with_verification, path)
            loaded = result_module.load_calibration(path)

            assert loaded == result_with_verification
            assert loaded.verification_state == verdict
            assert loaded.verification is not None
            assert loaded.verification.tolerance == summary.tolerance
            assert loaded.verification.report_path == summary.report_path

    def test_round_trip_with_verification_summary_missing_optional_fields(
        self, tmp_path: Path
    ) -> None:
        """`VerificationSummary.tolerance` / `report_path` が `None` の場合も
        往復し、`null` ではなくキーごと省かれる（下の
        `TestOptionalFieldsOmittedByKey` と対の確認）。
        """
        from world_frame_calibration import result as result_module

        base = _make_result(result_module)
        summary = result_module.VerificationSummary(
            verified_at_wall_ms=1_700_000_000_000.0,
            verdict=result_module.VerificationState.NOT_JUDGED,
            point_count=3,
            independent_point_count=3,
            bias_mm=(0.0, 0.0, 0.0),
            scatter_rms_mm=1.0,
            max_error_norm_mm=2.0,
            tolerance=None,
            report_path=None,
        )
        result_with_verification = dataclasses.replace(base, verification=summary)

        path = tmp_path / "calib.json"
        result_module.save_calibration(result_with_verification, path)
        loaded = result_module.load_calibration(path)

        assert loaded == result_with_verification
        assert loaded.verification is not None
        assert loaded.verification.tolerance is None
        assert loaded.verification.report_path is None


class TestOptionalFieldsOmittedByKey:
    """欠測フィールドはキーごと省かれ、`null` として書かれない
    （tasks.md タスク 4.1: 「欠測はキーごと省く（0 で埋めない）」）。
    """

    def test_session_path_none_is_omitted_not_null(self, tmp_path: Path) -> None:
        from world_frame_calibration import result as result_module

        original = _make_result(result_module, source_kind="simulated", session_path=None)
        path = tmp_path / "calib.json"
        result_module.save_calibration(original, path)

        raw = json.loads(path.read_text(encoding="utf-8"))
        assert "session_path" not in raw

    def test_verification_none_is_omitted_not_null(self, tmp_path: Path) -> None:
        from world_frame_calibration import result as result_module

        original = _make_result(result_module)
        path = tmp_path / "calib.json"
        result_module.save_calibration(original, path)

        raw = json.loads(path.read_text(encoding="utf-8"))
        assert "verification" not in raw

    def test_verification_tolerance_and_report_path_none_are_omitted(
        self, tmp_path: Path
    ) -> None:
        from world_frame_calibration import result as result_module

        base = _make_result(result_module)
        summary = result_module.VerificationSummary(
            verified_at_wall_ms=1_700_000_000_000.0,
            verdict=result_module.VerificationState.NOT_JUDGED,
            point_count=3,
            independent_point_count=3,
            bias_mm=(0.0, 0.0, 0.0),
            scatter_rms_mm=1.0,
            max_error_norm_mm=2.0,
            tolerance=None,
            report_path=None,
        )
        result_with_verification = dataclasses.replace(base, verification=summary)

        path = tmp_path / "calib.json"
        result_module.save_calibration(result_with_verification, path)

        raw = json.loads(path.read_text(encoding="utf-8"))
        assert "verification" in raw
        assert "tolerance" not in raw["verification"]
        assert "report_path" not in raw["verification"]


class TestSerializationRejectsNonNumericValues:
    """直列化は非数値（NaN）を許さない（`allow_nan=False`。tasks.md タスク 4.1）。"""

    def test_nan_in_plane_distance_raises_value_error_on_save(
        self, tmp_path: Path
    ) -> None:
        from world_frame_calibration import result as result_module

        original = _make_result(result_module)
        bad_plane = dataclasses.replace(original.plane, distance_mm=float("nan"))
        bad_result = dataclasses.replace(original, plane=bad_plane)

        path = tmp_path / "calib.json"
        with pytest.raises(ValueError):
            result_module.save_calibration(bad_result, path)


class TestDefaultOutputDirectory:
    """出力先の既定を `var/calibration/` とする（tasks.md タスク 4.1）。"""

    def test_save_calibration_without_path_uses_var_calibration_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from world_frame_calibration import result as result_module

        monkeypatch.chdir(tmp_path)
        original = _make_result(result_module)

        result_module.save_calibration(original)

        expected_path = tmp_path / "var" / "calibration" / f"{original.calibration_id}.json"
        assert expected_path.exists()

        loaded = result_module.load_calibration(expected_path)
        assert loaded == original


class TestLoadCalibrationRejectsMalformedFiles:
    """壊れたファイル（JSON でない・必須キー欠落）は `CalibrationConfigError`
    として拒否する（`plan.py` の規約を踏襲）。
    """

    def test_non_json_file_raises_calibration_config_error(self, tmp_path: Path) -> None:
        from world_frame_calibration import result as result_module

        path = tmp_path / "calib.json"
        path.write_text("not json at all {{{", encoding="utf-8")

        with pytest.raises(CalibrationConfigError):
            result_module.load_calibration(path)

    def test_missing_required_key_raises_calibration_config_error(
        self, tmp_path: Path
    ) -> None:
        from world_frame_calibration import result as result_module

        original = _make_result(result_module)
        path = tmp_path / "calib.json"
        result_module.save_calibration(original, path)

        raw = json.loads(path.read_text(encoding="utf-8"))
        del raw["transform"]
        path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

        with pytest.raises(CalibrationConfigError):
            result_module.load_calibration(path)
