"""m1_validation のテスト共通フィクスチャ（`synthetic.py` と同じ共有ヘルパ）。

上流の値のうち、**公開入口から組み立てられないもの**をここで用意する。

- キャリブレーション結果: `StreamSignature` / `Plane` / `FrameGeometry` /
  `AnchorObservation` がいずれも上流の `__all__` に無いため、値オブジェクトを
  直接は作れない。代わりに**保存形式の JSON を書き**、公開入口の
  `load_calibration()` に読ませる（本物の読み取り経路・形式版検査・整合性検査を
  そのまま通るので、ダブルより強い）。
- 投擲レイアウト: `M1Settings.resolve()` が要求するファイルを書き出す。

⚠️ キャリブレーション JSON の形は上流の保存形式そのものである。**上流が
`CALIBRATION_FORMAT_VERSION` を上げたらここを作り直すこと。**

ファイル名を `m1fixtures.py` としているのは、`tests/` 配下がパッケージ化されて
おらずモジュール名がセッション全体で衝突するためである（tasks.md
「Implementation Notes」タスク1.1）。
"""

from __future__ import annotations

import json
from pathlib import Path

CALIBRATION_FORMAT_VERSION = "1.0"
LAYOUT_FORMAT_VERSION = "1.0"


def calibration_payload(
    *,
    width_px: int = 640,
    height_px: int = 480,
    fx_px: float = 385.0,
    verification: dict[str, object] | None = None,
    format_version: str = CALIBRATION_FORMAT_VERSION,
    calibration_id: str = "cal-test-0001",
    translation_mm: tuple[float, float, float] = (0.0, 0.0, -1000.0),
) -> dict[str, object]:
    """保存形式のキャリブレーション結果 JSON を組み立てる。

    値は形式を満たすための最小限であり、意味のある較正ではない。**検査に
    効くのは `signature` / `intrinsics` / `verification` / 形式版 / `transform`
    だけ**である。
    """
    anchor = {
        "label": "origin",
        "role": "origin",
        "point_camera_mm": [0.0, 0.0, 1000.0],
        "point_on_plane_mm": [0.0, 0.0, 1000.0],
        "height_above_plane_mm": 0.0,
        "range_from_camera_mm": 1000.0,
        "sample_count": 100,
        "spread_mm": 1.0,
        "region": {"x0_px": 0, "y0_px": 0, "x1_px": 10, "y1_px": 10},
        "frames_used": 5,
    }
    payload: dict[str, object] = {
        "calibration_format_version": format_version,
        "calibration_id": calibration_id,
        "created_at_wall_ms": 1_700_000_000_000.0,
        "source_kind": "simulated",
        "session_path": None,
        "signature": {
            "width_px": width_px,
            "height_px": height_px,
            "fps": 30,
            "depth_scale_mm": 1.0,
            "color_enabled": False,
        },
        "intrinsics": {
            "width_px": width_px,
            "height_px": height_px,
            "fx_px": fx_px,
            "fy_px": 385.0,
            "ppx_px": 320.0,
            "ppy_px": 240.0,
            "model": "brown_conrady",
            "coeffs": [0.0, 0.0, 0.0, 0.0, 0.0],
        },
        "plane": {
            "normal": [0.0, 0.0, 1.0],
            "distance_mm": 0.0,
            "quality": {
                "points_considered": 1000,
                "inlier_count": 990,
                "inlier_ratio": 0.99,
                "residual_abs_p50_mm": 1.0,
                "residual_abs_p95_mm": 2.0,
                "residual_rms_mm": 1.5,
                "frames_used": 5,
                "incidence_angle_deg": 45.0,
                "rng_seed": 0,
            },
        },
        "transform": {
            "rotation": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
            "translation_mm": list(translation_mm),
        },
        "geometry": {
            "origin_camera_mm": [0.0, 0.0, 1000.0],
            "x_axis_camera": [1.0, 0.0, 0.0],
            "y_axis_camera": [0.0, 1.0, 0.0],
            "z_axis_camera": [0.0, 0.0, 1.0],
            "baseline_mm": 500.0,
            "yaw_sensitivity_deg_per_mm": 0.1,
            "lateral_error_mm_per_mm_at_1000mm": 0.02,
        },
        "origin_anchor": dict(anchor),
        "x_axis_anchor": {**anchor, "label": "x", "role": "x_axis"},
        "plan_digest": {"note": "test"},
        "notes": "test fixture",
    }
    if verification is not None:
        payload["verification"] = verification
    return payload


def verification_summary(verdict: str = "passed") -> dict[str, object]:
    """検証要約（保存形式）。`verdict` に4値のいずれかを与える。"""
    return {
        "verified_at_wall_ms": 1_700_000_500_000.0,
        "verdict": verdict,
        "point_count": 6,
        "independent_point_count": 4,
        "bias_mm": [1.0, 2.0, 3.0],
        "scatter_rms_mm": 4.0,
        "max_error_norm_mm": 9.0,
    }


def write_calibration(tmp_path: Path, **kwargs: object) -> Path:
    """キャリブレーション結果ファイルを書き出してパスを返す。"""
    path = tmp_path / "calibration.json"
    path.write_text(
        json.dumps(calibration_payload(**kwargs), ensure_ascii=False),  # type: ignore[arg-type]
        encoding="utf-8",
    )
    return path


def write_layout(tmp_path: Path, **overrides: object) -> Path:
    """投擲レイアウトファイルを書き出してパスを返す（仮値）。"""
    layout: dict[str, object] = {
        "layout_id": "throw-a",
        "release_position_world_mm": [-2000.0, 0.0, 1500.0],
        "release_height_mm": 1500.0,
        "throw_direction_deg": 0.0,
        "standby_position_world_mm": [0.0, 0.0],
        "object_diameter_mm": 65.0,
        "aperture_diameter_mm": 200.0,
        "camera_position_world_mm": [0.0, -1500.0, 1000.0],
        "notes": "仮値。確定ではない。",
    }
    layout.update(overrides)
    path = tmp_path / "layout.json"
    path.write_text(
        json.dumps(
            {"format_version": LAYOUT_FORMAT_VERSION, "layouts": [layout]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path
