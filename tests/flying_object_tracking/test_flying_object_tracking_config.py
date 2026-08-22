"""`flying_object_tracking.config`（`TrackingSettings`）のテスト（タスク 1.4）。

**ファイル名についての注記**: `tests/` にはどの階層にも `__init__.py` が無く、
pytest の既定 import mode（prepend）は同名 test ファイルをツリー全体で一意にしか
扱えない。`tests/prediction_core/test_config.py` /
`tests/sensing_foundation/test_sensing_config.py` が既に `test_config.py` 系の
basename を使っているため、本ファイルはタスク定義の指示どおり完全修飾名
`test_flying_object_tracking_config.py` を用いる（衝突回避。境界違反ではない）。

観測可能な完了状態（tasks.md 1.4）を固定する:

- 解決順序（CLI 引数 > 環境変数 > 設定ファイル > 既定値）が上位から順に勝つこと
- 処理範囲が未指定のときはフレーム全体を対象とする既定挙動（`width_px` /
  `height_px` が `None` で表現できること）
- 不正な組み合わせ（許容倍率の逆転／評価窓長が0以下／距離下限が上限以上／
  外れ値割合が0.5以上）がすべて起動時（`resolve()` 呼び出し時）に
  `TrackingConfigError`（`ValueError` としても捕捉できる）として拒否されること
- 境界値ちょうど手前の妥当な組み合わせは拒否されないこと
- 同一の後段設定（`roi` / `object_model` / `projection` / `tracker` /
  `measurement`）から同一の `postprocess_fingerprint()` が得られ、
  `detector.kind` など検出方式固有のフィールドだけが異なる設定からは
  同一の指紋が、後段設定が異なる設定からは異なる指紋が得られること
- `describe()` が JSON 化可能な dict を返し、`diameter_mm` / `kind` /
  `window_ms` について「初期評価候補であり必須性能でも達成済み性能でもない」
  旨の注記を含むこと
- 解決結果（`TrackingSettings` とその全フィールドの入れ子設定）が不変であること
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from flying_object_tracking.config import (
    DetectorConfig,
    MeasurementConfig,
    ObjectModelConfig,
    ProjectionConfig,
    RoiConfig,
    TrackerConfig,
    TrackingSettings,
)
from flying_object_tracking.errors import TrackingConfigError
from flying_object_tracking.types import DetectorKind

# ---------------------------------------------------------------------------
# 既定値・不変オブジェクトとしての最小コンポーネント
# ---------------------------------------------------------------------------


def test_roi_config_defaults_cover_whole_frame():
    """処理範囲が未指定のときはフレーム全体を対象とする既定挙動（要件 2.4）。"""
    roi = RoiConfig()
    assert roi.x_px == 0
    assert roi.y_px == 0
    assert roi.width_px is None
    assert roi.height_px is None
    assert roi.z_min_mm == 500.0
    assert roi.z_max_mm == 5000.0


def test_object_model_config_defaults():
    object_model = ObjectModelConfig()
    assert object_model.diameter_mm == 65.0
    assert object_model.min_scale == 0.5
    assert object_model.max_scale == 2.0
    assert object_model.min_area_px == 12
    assert object_model.max_area_px == 4000


def test_detector_config_defaults():
    detector = DetectorConfig()
    assert detector.kind == DetectorKind.FRAME_DIFF
    assert detector.diff_threshold_mm == 80.0
    assert detector.background_frames == 30
    assert detector.background_update_rate == 0.0
    assert detector.open_kernel_px == 3
    assert detector.close_kernel_px == 3
    assert detector.max_candidates == 8


def test_projection_config_defaults():
    projection = ProjectionConfig()
    assert projection.min_valid_depth_px == 8
    assert projection.depth_trim_ratio == 0.2
    assert projection.max_depth_spread_mm == 200.0


def test_tracker_config_defaults():
    tracker = TrackerConfig()
    assert tracker.max_step_mm == 900.0
    assert tracker.max_missing_frames == 3
    assert tracker.min_points_to_start == 1
    assert tracker.max_points == 120


def test_measurement_config_defaults():
    measurement = MeasurementConfig()
    assert measurement.enabled is True
    assert measurement.window_ms == 600.0
    assert measurement.detect_stage == "detect"
    assert measurement.track_stage == "track"


def test_tracking_settings_defaults_construct_without_args():
    settings = TrackingSettings()
    assert settings.roi == RoiConfig()
    assert settings.object_model == ObjectModelConfig()
    assert settings.detector == DetectorConfig()
    assert settings.projection == ProjectionConfig()
    assert settings.tracker == TrackerConfig()
    assert settings.measurement == MeasurementConfig()
    assert settings.output_root == Path("var/tracking")


def test_provisional_defaults_documented_as_initial_candidates_not_requirements():
    """tasks.md 1.4: 既定値は初期評価候補であり必須性能でも達成済み性能でもない。"""
    doc = (
        (ObjectModelConfig.__doc__ or "")
        + (DetectorConfig.__doc__ or "")
        + (MeasurementConfig.__doc__ or "")
        + (TrackingSettings.__doc__ or "")
    )
    assert "初期評価候補" in doc
    assert "必須性能" in doc


# ---------------------------------------------------------------------------
# resolve(): 不変性（Postcondition）
# ---------------------------------------------------------------------------


def test_resolved_settings_are_frozen():
    settings = TrackingSettings.resolve(file=None, env={}, overrides={})
    with pytest.raises(Exception):  # dataclasses.FrozenInstanceError
        settings.output_root = Path("elsewhere")  # type: ignore[misc]
    with pytest.raises(Exception):
        settings.roi.x_px = 10  # type: ignore[misc]
    with pytest.raises(Exception):
        settings.object_model.diameter_mm = 1.0  # type: ignore[misc]
    with pytest.raises(Exception):
        settings.detector.kind = DetectorKind.DEPTH_BAND  # type: ignore[misc]
    with pytest.raises(Exception):
        settings.projection.depth_trim_ratio = 0.0  # type: ignore[misc]
    with pytest.raises(Exception):
        settings.tracker.max_step_mm = 1.0  # type: ignore[misc]
    with pytest.raises(Exception):
        settings.measurement.window_ms = 1.0  # type: ignore[misc]


def test_tracking_settings_dataclass_is_frozen_slots():
    settings = TrackingSettings()
    with pytest.raises(Exception):
        settings.new_attr = "x"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# resolve(): 既定値のみ
# ---------------------------------------------------------------------------


def test_resolve_with_nothing_supplied_yields_defaults():
    settings = TrackingSettings.resolve(file=None, env={}, overrides={})
    assert settings == TrackingSettings()


def test_resolve_roi_unspecified_stays_whole_frame():
    settings = TrackingSettings.resolve(file=None, env={}, overrides={})
    assert settings.roi.width_px is None
    assert settings.roi.height_px is None


# ---------------------------------------------------------------------------
# resolve(): 解決順序（CLI 引数 > 環境変数 > 設定ファイル > 既定値）
# ---------------------------------------------------------------------------


def test_file_beats_defaults(tmp_path: Path):
    config_file = tmp_path / "settings.json"
    config_file.write_text(
        json.dumps({"tracker_max_step_mm": 123.0, "object_min_area_px": 20}),
        encoding="utf-8",
    )

    settings = TrackingSettings.resolve(file=config_file, env={}, overrides={})

    assert settings.tracker.max_step_mm == 123.0
    assert settings.object_model.min_area_px == 20
    # ファイルが指定していないキーは既定値のまま
    assert settings.object_model.max_area_px == 4000


def test_env_beats_file(tmp_path: Path):
    config_file = tmp_path / "settings.json"
    config_file.write_text(
        json.dumps({"tracker_max_step_mm": 123.0}), encoding="utf-8"
    )

    settings = TrackingSettings.resolve(
        file=config_file, env={"STB_FOT_TRACKER_MAX_STEP_MM": "456.0"}, overrides={}
    )

    assert settings.tracker.max_step_mm == 456.0  # env が file に勝つ


def test_overrides_beat_env_and_file(tmp_path: Path):
    config_file = tmp_path / "settings.json"
    config_file.write_text(
        json.dumps({"tracker_max_step_mm": 123.0}), encoding="utf-8"
    )

    settings = TrackingSettings.resolve(
        file=config_file,
        env={"STB_FOT_TRACKER_MAX_STEP_MM": "456.0"},
        overrides={"tracker_max_step_mm": 789.0},
    )

    assert settings.tracker.max_step_mm == 789.0  # overrides (CLI) が最優先


def test_precedence_resolves_top_to_bottom_across_all_four_sources(tmp_path: Path):
    """4層すべてが異なる値を主張する場合、上位が順に勝つことを固定する。"""
    config_file = tmp_path / "settings.json"
    config_file.write_text(
        json.dumps({"tracker_max_step_mm": 100.0}), encoding="utf-8"
    )

    # 設定ファイルのみ -> ファイルの値
    settings = TrackingSettings.resolve(file=config_file, env={}, overrides={})
    assert settings.tracker.max_step_mm == 100.0

    # + 環境変数 -> 環境変数の値
    settings = TrackingSettings.resolve(
        file=config_file, env={"STB_FOT_TRACKER_MAX_STEP_MM": "200.0"}, overrides={}
    )
    assert settings.tracker.max_step_mm == 200.0

    # + CLI overrides -> overrides の値
    settings = TrackingSettings.resolve(
        file=config_file,
        env={"STB_FOT_TRACKER_MAX_STEP_MM": "200.0"},
        overrides={"tracker_max_step_mm": 300.0},
    )
    assert settings.tracker.max_step_mm == 300.0


def test_env_var_prefix_is_stb_fot_and_coerces_enum_and_bool():
    settings = TrackingSettings.resolve(
        file=None,
        env={
            "STB_FOT_DETECTOR_KIND": "depth_band",
            "STB_FOT_MEASUREMENT_ENABLED": "false",
        },
        overrides={},
    )
    assert settings.detector.kind == DetectorKind.DEPTH_BAND
    assert settings.measurement.enabled is False


def test_roi_width_height_resolvable_from_env_and_file_stay_none_by_default():
    settings = TrackingSettings.resolve(
        file=None,
        env={"STB_FOT_ROI_WIDTH_PX": "320", "STB_FOT_ROI_HEIGHT_PX": "240"},
        overrides={},
    )
    assert settings.roi.width_px == 320
    assert settings.roi.height_px == 240


def test_output_root_resolvable_from_overrides():
    settings = TrackingSettings.resolve(
        file=None, env={}, overrides={"output_root": "var/custom"}
    )
    assert settings.output_root == Path("var/custom")


# ---------------------------------------------------------------------------
# resolve(): 不正な組み合わせを起動時に拒否する（要件 10.5）
# ---------------------------------------------------------------------------


def test_rejects_scale_inversion():
    """許容倍率の逆転（min_scale >= max_scale）。"""
    with pytest.raises(TrackingConfigError):
        TrackingSettings.resolve(
            file=None,
            env={},
            overrides={"object_min_scale": 2.0, "object_max_scale": 2.0},
        )
    with pytest.raises(ValueError):  # TrackingConfigError は ValueError も継承する
        TrackingSettings.resolve(
            file=None,
            env={},
            overrides={"object_min_scale": 3.0, "object_max_scale": 2.0},
        )


def test_rejects_nonpositive_window_ms():
    """評価窓長が0以下。"""
    with pytest.raises(TrackingConfigError):
        TrackingSettings.resolve(
            file=None, env={}, overrides={"measurement_window_ms": 0.0}
        )
    with pytest.raises(TrackingConfigError):
        TrackingSettings.resolve(
            file=None, env={}, overrides={"measurement_window_ms": -1.0}
        )


def test_rejects_z_min_at_or_above_z_max():
    """距離下限が上限以上。"""
    with pytest.raises(TrackingConfigError):
        TrackingSettings.resolve(
            file=None,
            env={},
            overrides={"roi_z_min_mm": 1000.0, "roi_z_max_mm": 1000.0},
        )
    with pytest.raises(TrackingConfigError):
        TrackingSettings.resolve(
            file=None,
            env={},
            overrides={"roi_z_min_mm": 5000.0, "roi_z_max_mm": 1000.0},
        )


def test_rejects_depth_trim_ratio_at_or_above_half():
    """外れ値割合が0.5以上。"""
    with pytest.raises(TrackingConfigError):
        TrackingSettings.resolve(
            file=None, env={}, overrides={"projection_depth_trim_ratio": 0.5}
        )
    with pytest.raises(TrackingConfigError):
        TrackingSettings.resolve(
            file=None, env={}, overrides={"projection_depth_trim_ratio": 0.9}
        )


def test_rejects_negative_depth_trim_ratio():
    with pytest.raises(TrackingConfigError):
        TrackingSettings.resolve(
            file=None, env={}, overrides={"projection_depth_trim_ratio": -0.1}
        )


def test_rejects_negative_max_missing_frames():
    with pytest.raises(TrackingConfigError):
        TrackingSettings.resolve(
            file=None, env={}, overrides={"tracker_max_missing_frames": -1}
        )


@pytest.mark.parametrize(
    "key,bad_value",
    [
        ("roi_width_px", 0),
        ("roi_height_px", -1),
        ("object_min_area_px", 0),
        ("object_max_area_px", 0),
        ("detector_open_kernel_px", 0),
        ("detector_close_kernel_px", -1),
        ("projection_min_valid_depth_px", 0),
    ],
)
def test_rejects_nonpositive_size_fields(key: str, bad_value: int):
    with pytest.raises(TrackingConfigError):
        TrackingSettings.resolve(file=None, env={}, overrides={key: bad_value})


# ---------------------------------------------------------------------------
# resolve(): 境界値ちょうど手前の妥当な組み合わせは拒否されない
# ---------------------------------------------------------------------------


def test_valid_boundary_combinations_do_not_raise():
    settings = TrackingSettings.resolve(
        file=None,
        env={},
        overrides={
            "object_min_scale": 1.9999,
            "object_max_scale": 2.0,
            "measurement_window_ms": 0.001,
            "roi_z_min_mm": 999.999,
            "roi_z_max_mm": 1000.0,
            "projection_depth_trim_ratio": 0.499,
            "tracker_max_missing_frames": 0,
        },
    )
    assert settings.object_model.min_scale == 1.9999
    assert settings.measurement.window_ms == 0.001
    assert settings.projection.depth_trim_ratio == 0.499
    assert settings.tracker.max_missing_frames == 0


def test_depth_trim_ratio_zero_is_valid():
    settings = TrackingSettings.resolve(
        file=None, env={}, overrides={"projection_depth_trim_ratio": 0.0}
    )
    assert settings.projection.depth_trim_ratio == 0.0


def test_roi_z_bounds_unset_is_valid():
    """z_min_mm / z_max_mm のどちらか一方だけが None のときは比較しない。"""
    settings = TrackingSettings.resolve(
        file=None, env={}, overrides={"roi_z_min_mm": "none"}
    )
    assert settings.roi.z_min_mm is None
    assert settings.roi.z_max_mm == 5000.0


# ---------------------------------------------------------------------------
# postprocess_fingerprint(): 後段設定の同一性を表す指紋
# ---------------------------------------------------------------------------


def test_fingerprint_is_deterministic_for_same_settings():
    a = TrackingSettings.resolve(file=None, env={}, overrides={})
    b = TrackingSettings.resolve(file=None, env={}, overrides={})
    assert a.postprocess_fingerprint() == b.postprocess_fingerprint()
    # 同一インスタンスへの2回の呼び出しも同じ結果
    assert a.postprocess_fingerprint() == a.postprocess_fingerprint()


def test_fingerprint_is_stable_across_detector_kind_and_detector_specific_fields():
    base = TrackingSettings.resolve(file=None, env={}, overrides={})
    other_kind = replace(
        base, detector=replace(base.detector, kind=DetectorKind.BACKGROUND)
    )
    other_detector_params = replace(
        base,
        detector=replace(
            base.detector, diff_threshold_mm=999.0, background_frames=1
        ),
    )
    assert base.postprocess_fingerprint() == other_kind.postprocess_fingerprint()
    assert (
        base.postprocess_fingerprint()
        == other_detector_params.postprocess_fingerprint()
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: replace(s, roi=replace(s.roi, x_px=5)),
        lambda s: replace(
            s, object_model=replace(s.object_model, diameter_mm=70.0)
        ),
        lambda s: replace(
            s, projection=replace(s.projection, max_depth_spread_mm=1.0)
        ),
        lambda s: replace(s, tracker=replace(s.tracker, max_step_mm=1.0)),
        lambda s: replace(
            s, measurement=replace(s.measurement, window_ms=100.0)
        ),
    ],
)
def test_fingerprint_changes_when_postprocess_settings_differ(mutate):
    base = TrackingSettings.resolve(file=None, env={}, overrides={})
    mutated = mutate(base)
    assert base.postprocess_fingerprint() != mutated.postprocess_fingerprint()


# ---------------------------------------------------------------------------
# describe(): --print-settings 用の表示（要件 12.2 / 12.3）
# ---------------------------------------------------------------------------


def test_describe_returns_json_serializable_dict():
    settings = TrackingSettings.resolve(file=None, env={}, overrides={})
    described = settings.describe()
    assert isinstance(described, dict)
    serialized = json.dumps(described)  # 例外を送出しないことそのものが検証
    assert isinstance(serialized, str)


def test_describe_mentions_provisional_disclaimer_for_known_provisional_defaults():
    settings = TrackingSettings.resolve(file=None, env={}, overrides={})
    described = settings.describe()
    disclaimer_text = json.dumps(described, ensure_ascii=False)
    assert "初期評価候補" in disclaimer_text
    assert "diameter_mm" in disclaimer_text
    assert "kind" in disclaimer_text
    assert "window_ms" in disclaimer_text


def test_describe_reflects_resolved_values():
    settings = TrackingSettings.resolve(
        file=None, env={}, overrides={"tracker_max_step_mm": 42.0}
    )
    described = settings.describe()
    assert described["tracker"]["max_step_mm"] == 42.0


# ---------------------------------------------------------------------------
# resolve(): 未知の設定キーの扱い
# ---------------------------------------------------------------------------


def test_rejects_unknown_key_in_overrides():
    with pytest.raises(TrackingConfigError):
        TrackingSettings.resolve(
            file=None, env={}, overrides={"no_such_key": 1}
        )


def test_ignores_unknown_stb_fot_env_vars():
    """file / overrides と異なり、未知の STB_FOT_* は無視する（sensing_foundation と同じ方針）。"""
    settings = TrackingSettings.resolve(
        file=None, env={"STB_FOT_NO_SUCH_KEY": "1"}, overrides={}
    )
    assert settings == TrackingSettings()
