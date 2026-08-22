"""world_frame_calibration.plan の読み書きと起動時検証の検証
(tasks.md タスク 1.5 / 要件 1.4, 4.9, 7.1)。

design.md「L0-L2: 基盤・入力」節の CalibrationPlan コンポーネント契約を固定する。
本テストが確認すること:

- 床探索範囲・原点マーカー・方向マーカー・検証点・下限群・許容値・
  メジャー実測の基線長を1つの `CalibrationPlan` として保存 → 読み込みでき、
  内容が完全に一致すること（design.md CalibrationPlan「Batch / Job Contract」
  観察可能な完了状態）
- 形式版 (`plan_format_version`) が未知の場合、`load_plan` が**起動時に**
  `CalibrationConfigError` を送出すること（`CalibrationFailure` ではない）
- 画素範囲が画像外（負の座標）または空（下限≧上限相当）の場合、`load_plan`
  が起動時に `CalibrationConfigError` を送出すること
- `height_band_mm` の下限が上限以上の場合、`load_plan` が起動時に
  `CalibrationConfigError` を送出すること
- 検証点ラベルが重複する場合、`load_plan` が起動時に `CalibrationConfigError`
  を送出すること
- `PlanLimits` の既定値が design.md の契約どおりであること、および
  `min_baseline_mm` の導出根拠（観測誤差 10 mm・距離 2 m で横ずれ約 25 mm）が
  docstring に記載されていること
- `VerificationPointSpec.truth_source` が型として必須（省略不可）であること
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from world_frame_calibration.errors import CalibrationConfigError, CalibrationFailure
from world_frame_calibration.types import PixelRegion, ToleranceSpec


def _region(**overrides: object) -> PixelRegion:
    kwargs: dict[str, object] = dict(x0_px=10, y0_px=10, x1_px=630, y1_px=470)
    kwargs.update(overrides)
    return PixelRegion(**kwargs)  # type: ignore[arg-type]


def _make_valid_plan(plan_module, **overrides: object):
    """design.md の契約に適合する最小限の妥当な `CalibrationPlan` を返す。"""
    kwargs: dict[str, object] = dict(
        plan_format_version=plan_module.PLAN_FORMAT_VERSION,
        floor_region=_region(x0_px=0, y0_px=0, x1_px=640, y1_px=480),
        origin_anchor=plan_module.AnchorSpec(
            label="origin_marker",
            region=_region(x0_px=100, y0_px=300, x1_px=140, y1_px=340),
            height_band_mm=(20.0, 120.0),
        ),
        x_axis_anchor=plan_module.AnchorSpec(
            label="x_axis_marker",
            region=_region(x0_px=400, y0_px=300, x1_px=440, y1_px=340),
            height_band_mm=(20.0, 120.0),
        ),
        verification_points=(
            plan_module.VerificationPointSpec(
                label="verify_floor_1",
                region=_region(x0_px=200, y0_px=350, x1_px=240, y1_px=390),
                height_band_mm=(0.0, 60.0),
                truth_world_mm=(500.0, 300.0, 0.0),
                truth_source="実測: 2026-08-22 メジャー計測（原点からの巻尺実測）",
            ),
            plan_module.VerificationPointSpec(
                label="verify_elevated_1",
                region=_region(x0_px=250, y0_px=200, x1_px=290, y1_px=240),
                height_band_mm=(400.0, 500.0),
                truth_world_mm=(600.0, 350.0, 450.0),
                truth_source="実測: 2026-08-22 メジャー + 三脚高さ計測",
            ),
        ),
        limits=plan_module.PlanLimits(),
        tolerance=ToleranceSpec(
            horizontal_mm=20.0,
            vertical_mm=15.0,
            provisional=True,
            source="暫定値: 観測誤差10mm・距離2mで横ずれ約25mmから概算",
        ),
        expected_baseline_mm=1234.5,
        notes="設置A: 待機位置=原点マーカー、投擲方向=x_axis_marker 方向",
    )
    kwargs.update(overrides)
    return plan_module.CalibrationPlan(**kwargs)  # type: ignore[arg-type]


def _plan_to_json_dict(plan_module, plan) -> dict:
    """一度 `save_plan` してから読み戻すことで、素の JSON dict を取得する。

    テストが直接改変してから書き込み直せるようにするためのヘルパ。
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "plan.json"
        plan_module.save_plan(plan, path)
        return json.loads(path.read_text(encoding="utf-8"))


class TestSaveLoadRoundTrip:
    """保存 → 読み込みで内容が完全に一致する（design.md CalibrationPlan 観察可能な完了状態）。"""

    def test_round_trip_preserves_content_exactly(self, tmp_path: Path) -> None:
        from world_frame_calibration import plan as plan_module

        original = _make_valid_plan(plan_module)
        path = tmp_path / "plan.json"

        plan_module.save_plan(original, path)
        loaded = plan_module.load_plan(path)

        assert loaded == original

    def test_round_trip_with_none_tolerance_and_none_expected_baseline(
        self, tmp_path: Path
    ) -> None:
        from world_frame_calibration import plan as plan_module

        original = _make_valid_plan(
            plan_module, tolerance=None, expected_baseline_mm=None
        )
        path = tmp_path / "plan.json"

        plan_module.save_plan(original, path)
        loaded = plan_module.load_plan(path)

        assert loaded == original
        assert loaded.tolerance is None
        assert loaded.expected_baseline_mm is None

    def test_round_trip_with_empty_verification_points(self, tmp_path: Path) -> None:
        from world_frame_calibration import plan as plan_module

        original = _make_valid_plan(plan_module, verification_points=())
        path = tmp_path / "plan.json"

        plan_module.save_plan(original, path)
        loaded = plan_module.load_plan(path)

        assert loaded == original
        assert loaded.verification_points == ()

    def test_save_plan_writes_readable_json_containing_format_version(
        self, tmp_path: Path
    ) -> None:
        from world_frame_calibration import plan as plan_module

        original = _make_valid_plan(plan_module)
        path = tmp_path / "plan.json"
        plan_module.save_plan(original, path)

        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["plan_format_version"] == plan_module.PLAN_FORMAT_VERSION


class TestUnknownFormatVersionRejectedAtLoadTime:
    """未知の `plan_format_version` を読み込んだら起動時に失敗する（要件 7.1）。"""

    def test_unknown_format_version_raises_calibration_config_error(
        self, tmp_path: Path
    ) -> None:
        from world_frame_calibration import plan as plan_module

        original = _make_valid_plan(plan_module)
        data = _plan_to_json_dict(plan_module, original)
        data["plan_format_version"] = "9.9"

        path = tmp_path / "plan.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        with pytest.raises(CalibrationConfigError):
            plan_module.load_plan(path)

    def test_unknown_format_version_does_not_raise_calibration_failure(
        self, tmp_path: Path
    ) -> None:
        """`CalibrationFailure`（座標系が定まらない条件）ではなく
        `CalibrationConfigError`（設定の誤り）であることを区別する。
        """
        from world_frame_calibration import plan as plan_module

        original = _make_valid_plan(plan_module)
        data = _plan_to_json_dict(plan_module, original)
        data["plan_format_version"] = "0.1"

        path = tmp_path / "plan.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        try:
            plan_module.load_plan(path)
        except CalibrationConfigError as exc:
            assert not isinstance(exc, CalibrationFailure)
        else:
            pytest.fail("CalibrationConfigError が送出されなかった")

    def test_missing_format_version_key_raises_calibration_config_error(
        self, tmp_path: Path
    ) -> None:
        from world_frame_calibration import plan as plan_module

        original = _make_valid_plan(plan_module)
        data = _plan_to_json_dict(plan_module, original)
        del data["plan_format_version"]

        path = tmp_path / "plan.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        with pytest.raises(CalibrationConfigError):
            plan_module.load_plan(path)


class TestInvalidRegionRejectedAtLoadTime:
    """画素範囲が画像外・空の場合は起動時に拒否する（design.md CalibrationPlan
    「Batch / Job Contract」、tasks.md タスク 1.5）。
    """

    def test_floor_region_with_negative_origin_is_rejected(self, tmp_path: Path) -> None:
        from world_frame_calibration import plan as plan_module

        original = _make_valid_plan(plan_module)
        data = _plan_to_json_dict(plan_module, original)
        data["floor_region"]["x0_px"] = -5

        path = tmp_path / "plan.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        with pytest.raises(CalibrationConfigError):
            plan_module.load_plan(path)

    def test_floor_region_that_is_empty_is_rejected(self, tmp_path: Path) -> None:
        from world_frame_calibration import plan as plan_module

        original = _make_valid_plan(plan_module)
        data = _plan_to_json_dict(plan_module, original)
        data["floor_region"]["x1_px"] = data["floor_region"]["x0_px"]

        path = tmp_path / "plan.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        with pytest.raises(CalibrationConfigError):
            plan_module.load_plan(path)

    def test_origin_anchor_region_with_negative_coordinate_is_rejected(
        self, tmp_path: Path
    ) -> None:
        from world_frame_calibration import plan as plan_module

        original = _make_valid_plan(plan_module)
        data = _plan_to_json_dict(plan_module, original)
        data["origin_anchor"]["region"]["y0_px"] = -1

        path = tmp_path / "plan.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        with pytest.raises(CalibrationConfigError):
            plan_module.load_plan(path)

    def test_verification_point_region_that_is_empty_is_rejected(
        self, tmp_path: Path
    ) -> None:
        from world_frame_calibration import plan as plan_module

        original = _make_valid_plan(plan_module)
        data = _plan_to_json_dict(plan_module, original)
        vp_region = data["verification_points"][0]["region"]
        vp_region["y1_px"] = vp_region["y0_px"]

        path = tmp_path / "plan.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        with pytest.raises(CalibrationConfigError):
            plan_module.load_plan(path)


class TestInvalidHeightBandRejectedAtLoadTime:
    """`height_band_mm` の下限が上限以上の場合は起動時に拒否する
    （tasks.md タスク 1.5）。
    """

    def test_origin_anchor_height_band_lower_bound_equal_to_upper_is_rejected(
        self, tmp_path: Path
    ) -> None:
        from world_frame_calibration import plan as plan_module

        original = _make_valid_plan(plan_module)
        data = _plan_to_json_dict(plan_module, original)
        data["origin_anchor"]["height_band_mm"] = [50.0, 50.0]

        path = tmp_path / "plan.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        with pytest.raises(CalibrationConfigError):
            plan_module.load_plan(path)

    def test_x_axis_anchor_height_band_lower_bound_above_upper_is_rejected(
        self, tmp_path: Path
    ) -> None:
        from world_frame_calibration import plan as plan_module

        original = _make_valid_plan(plan_module)
        data = _plan_to_json_dict(plan_module, original)
        data["x_axis_anchor"]["height_band_mm"] = [100.0, 20.0]

        path = tmp_path / "plan.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        with pytest.raises(CalibrationConfigError):
            plan_module.load_plan(path)

    def test_verification_point_height_band_lower_bound_above_upper_is_rejected(
        self, tmp_path: Path
    ) -> None:
        from world_frame_calibration import plan as plan_module

        original = _make_valid_plan(plan_module)
        data = _plan_to_json_dict(plan_module, original)
        data["verification_points"][0]["height_band_mm"] = [80.0, 10.0]

        path = tmp_path / "plan.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        with pytest.raises(CalibrationConfigError):
            plan_module.load_plan(path)


class TestDuplicateVerificationPointLabelsRejectedAtLoadTime:
    """検証点ラベルの重複は起動時に拒否する（tasks.md タスク 1.5）。"""

    def test_duplicate_verification_point_labels_are_rejected(self, tmp_path: Path) -> None:
        from world_frame_calibration import plan as plan_module

        original = _make_valid_plan(plan_module)
        data = _plan_to_json_dict(plan_module, original)
        data["verification_points"][1]["label"] = data["verification_points"][0]["label"]

        path = tmp_path / "plan.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        with pytest.raises(CalibrationConfigError):
            plan_module.load_plan(path)

    def test_unique_verification_point_labels_are_accepted(self, tmp_path: Path) -> None:
        from world_frame_calibration import plan as plan_module

        original = _make_valid_plan(plan_module)
        path = tmp_path / "plan.json"
        plan_module.save_plan(original, path)

        loaded = plan_module.load_plan(path)
        labels = [vp.label for vp in loaded.verification_points]
        assert len(labels) == len(set(labels))


class TestPlanLimitsDefaults:
    """`PlanLimits` の既定値が design.md CalibrationPlan の契約どおりであること。"""

    def test_default_values_match_design_contract(self) -> None:
        from world_frame_calibration import plan as plan_module

        limits = plan_module.PlanLimits()
        assert limits.min_inlier_points == 2000
        assert limits.min_inlier_ratio == pytest.approx(0.5)
        assert limits.plane_inlier_threshold_mm == pytest.approx(15.0)
        assert limits.min_incidence_angle_deg == pytest.approx(10.0)
        assert limits.min_baseline_mm == pytest.approx(800.0)
        assert limits.min_anchor_samples == 50
        assert limits.ransac_success_probability == pytest.approx(0.999)
        assert limits.ransac_outlier_ratio == pytest.approx(0.5)
        assert limits.rng_seed == 0

    def test_min_baseline_mm_rationale_is_documented_in_a_docstring(self) -> None:
        """`min_baseline_mm` の既定値 800.0 の導出根拠（観測誤差 10 mm・
        距離 2 m で横ずれ約 25 mm）が docstring に記載されている
        （tasks.md タスク 1.5 / design.md CalibrationPlan「Validation」）。
        """
        from world_frame_calibration import plan as plan_module

        doc = plan_module.PlanLimits.__doc__ or ""
        assert "10 mm" in doc
        assert "2 m" in doc
        assert "25" in doc
        assert "mm" in doc

    def test_plan_limits_is_frozen_slots_dataclass(self) -> None:
        import dataclasses

        from world_frame_calibration import plan as plan_module

        assert dataclasses.is_dataclass(plan_module.PlanLimits)
        assert plan_module.PlanLimits.__dataclass_params__.frozen is True
        instance = plan_module.PlanLimits()
        with pytest.raises(AttributeError):
            instance.__dict__  # noqa: B018 -- intentionally probing for absence


class TestVerificationPointSpecRequiresTruthSource:
    """検証点の既知座標はメジャー実測値であり、実測手段の記録
    (`truth_source`) が必須であることを型で強制する（tasks.md タスク 1.5）。
    """

    def test_omitting_truth_source_raises_type_error(self) -> None:
        from world_frame_calibration import plan as plan_module

        with pytest.raises(TypeError):
            plan_module.VerificationPointSpec(  # type: ignore[call-arg]
                label="verify_1",
                region=_region(),
                height_band_mm=(0.0, 60.0),
                truth_world_mm=(500.0, 300.0, 0.0),
            )

    def test_truth_source_is_stored_and_round_trips(self, tmp_path: Path) -> None:
        from world_frame_calibration import plan as plan_module

        original = _make_valid_plan(plan_module)
        path = tmp_path / "plan.json"
        plan_module.save_plan(original, path)
        loaded = plan_module.load_plan(path)

        for vp in loaded.verification_points:
            assert vp.truth_source
            assert isinstance(vp.truth_source, str)


class TestAnchorAndCalibrationPlanAreFrozenSlotsDataclasses:
    """`AnchorSpec` / `VerificationPointSpec` / `CalibrationPlan` も
    design.md GeoTypes と同じ規約（frozen かつ slots）に従う。
    """

    def test_anchor_spec_is_frozen_slots_dataclass(self) -> None:
        import dataclasses

        from world_frame_calibration import plan as plan_module

        assert dataclasses.is_dataclass(plan_module.AnchorSpec)
        assert plan_module.AnchorSpec.__dataclass_params__.frozen is True

    def test_verification_point_spec_is_frozen_slots_dataclass(self) -> None:
        import dataclasses

        from world_frame_calibration import plan as plan_module

        assert dataclasses.is_dataclass(plan_module.VerificationPointSpec)
        assert plan_module.VerificationPointSpec.__dataclass_params__.frozen is True

    def test_calibration_plan_is_frozen_slots_dataclass(self) -> None:
        import dataclasses

        from world_frame_calibration import plan as plan_module

        assert dataclasses.is_dataclass(plan_module.CalibrationPlan)
        assert plan_module.CalibrationPlan.__dataclass_params__.frozen is True


class TestPlanFormatVersionConstant:
    def test_plan_format_version_is_exactly_one_point_zero(self) -> None:
        from world_frame_calibration import plan as plan_module

        assert plan_module.PLAN_FORMAT_VERSION == "1.0"
