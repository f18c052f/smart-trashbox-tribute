"""キャリブレーション検証ゲートと整合性検査の検証（タスク 2.3 / 要件 1.6, 2.1-2.3）。

観測可能な完了状態（tasks.md 2.3）を固定する:

- **検証状態の4値それぞれに対して拒否・許可・印の付き方が期待どおりになる**
- **解像度違いのキャリブレーションが設定不一致として拒否される**

あわせて次の点も固定する:

- 未検証での実行を**既定で拒否**し、明示的な許可でのみ続行できる（要件 2.1）
- 許可して続行した場合、**生成物すべてに未検証の印が伝播する**（要件 2.2）
- 識別子・検証状態・検証時刻・平均オフセット・ばらつき・合否を、後段が読める
  要約として取り出せる（要件 2.3, 2.4 の材料）
- 上流の例外型をそのまま外へ出さない（接点を1モジュールに閉じる）

キャリブレーション結果は**公開入口から組み立てられない**（`StreamSignature` /
`Plane` / `FrameGeometry` / `AnchorObservation` がいずれも上流の `__all__` に
無い）。そこで**保存形式の JSON を直接書き**、公開入口の `load_calibration()`
に読ませる——本物の読み取り経路をそのまま通すので、ダブルより強い。
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from flying_object_tracking import (
    HANDOFF_VERSION,
    CameraPoint,
    CameraTrack,
    CoordinateFrame,
    SourceKind,
    TrackPoint,
    TrackState,
)
from m1_validation.config import M1Settings
from m1_validation.errors import FailureReason, M1ValidationError, SeamFailure
from m1_validation.layout import LAYOUT_FORMAT_VERSION
from m1_validation.seam import build_samples, calibration_summary, open_calibration
from world_frame_calibration import load_calibration

CALIBRATION_FORMAT_VERSION = "1.0"


def _calibration_payload(
    *,
    width_px: int = 640,
    height_px: int = 480,
    fx_px: float = 385.0,
    verification: dict[str, object] | None = None,
    format_version: str = CALIBRATION_FORMAT_VERSION,
    calibration_id: str = "cal-test-0001",
) -> dict[str, object]:
    """保存形式のキャリブレーション結果 JSON を組み立てる。

    値は形式を満たすための最小限であり、意味のある較正ではない。**検査に
    効くのは `signature` / `intrinsics` / `verification` / 形式版だけ**である。
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
            "translation_mm": [0.0, 0.0, -1000.0],
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


def _verification(verdict: str) -> dict[str, object]:
    return {
        "verified_at_wall_ms": 1_700_000_500_000.0,
        "verdict": verdict,
        "point_count": 6,
        "independent_point_count": 4,
        "bias_mm": [1.0, 2.0, 3.0],
        "scatter_rms_mm": 4.0,
        "max_error_norm_mm": 9.0,
    }


def _write_calibration(tmp_path: Path, **kwargs: object) -> Path:
    path = tmp_path / "calibration.json"
    path.write_text(
        json.dumps(_calibration_payload(**kwargs), ensure_ascii=False),  # type: ignore[arg-type]
        encoding="utf-8",
    )
    return path


@pytest.fixture
def settings(tmp_path: Path) -> M1Settings:
    layout_path = tmp_path / "layout.json"
    layout_path.write_text(
        json.dumps(
            {
                "format_version": LAYOUT_FORMAT_VERSION,
                "layouts": [
                    {
                        "layout_id": "throw-a",
                        "release_position_world_mm": None,
                        "release_height_mm": 1500.0,
                        "throw_direction_deg": 0.0,
                        "standby_position_world_mm": [0.0, 0.0],
                        "object_diameter_mm": 65.0,
                        "aperture_diameter_mm": 200.0,
                        "camera_position_world_mm": [0.0, -1500.0, 1000.0],
                        "notes": "仮値。",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return M1Settings.resolve(
        file=None, env={}, overrides={"layout_file": str(layout_path)}
    )


def _current_profile_of(path: Path):
    """そのファイル自身の `signature` / `intrinsics` を「現在の入力元」として使う。

    ⚠️ 上流の `StreamSignature` / `Intrinsics` は `__all__` に無いため本物を
    組み立てられない。しかも `check_compatibility()` は
    `result.signature != signature` という**オブジェクト同士の比較**なので、
    構造が同じだけの別クラスを渡すと常に不一致になる。そこで公開関数
    `load_calibration()` で読んだ結果自身の値を「現在の入力元」として渡し、
    一致の場合を作る。不一致の場合は `dataclasses.replace()` でずらす。

    **これは実運用の経路ではない。** 実運用では「いま開いている入力元」の
    値が要るが、それを上流の公開入口から作る手段が無い——本 Spec の
    Implementation Notes（タスク2.3）に上流への申し送りとして記録した。
    """
    loaded = load_calibration(path)
    return loaded.signature, loaded.intrinsics


class TestVerificationGate:
    """検証状態の4値に対する拒否・許可（要件 2.1, 2.2）。"""

    def test_passed_calibration_is_accepted(
        self, settings: M1Settings, tmp_path: Path
    ) -> None:
        path = _write_calibration(tmp_path, verification=_verification("passed"))
        signature, intrinsics = _current_profile_of(path)

        result = open_calibration(
            path, settings=settings, signature=signature, intrinsics=intrinsics
        )

        assert result.calibration_id == "cal-test-0001"
        assert result.verification_state == "passed"

    @pytest.mark.parametrize("verdict", ["failed", "not_judged"])
    def test_verified_but_not_passed_is_rejected_by_default(
        self, settings: M1Settings, tmp_path: Path, verdict: str
    ) -> None:
        """「検証したが合格していない」も既定で拒否する。

        `failed` / `not_judged` を通すと、**誤差の帰属ができないデータを
        気づかず判断へ使う**ことになる。
        """
        path = _write_calibration(tmp_path, verification=_verification(verdict))
        signature, intrinsics = _current_profile_of(path)

        with pytest.raises(SeamFailure) as exc:
            open_calibration(
                path, settings=settings, signature=signature, intrinsics=intrinsics
            )
        assert exc.value.reason == FailureReason.CALIBRATION_NOT_VERIFIED

    def test_never_verified_is_rejected_by_default(
        self, settings: M1Settings, tmp_path: Path
    ) -> None:
        path = _write_calibration(tmp_path, verification=None)
        signature, intrinsics = _current_profile_of(path)

        with pytest.raises(SeamFailure) as exc:
            open_calibration(
                path, settings=settings, signature=signature, intrinsics=intrinsics
            )
        assert exc.value.reason == FailureReason.CALIBRATION_NOT_VERIFIED
        assert exc.value.context.get("verification_state") == "not_verified"

    @pytest.mark.parametrize("verdict", [None, "failed", "not_judged"])
    def test_explicit_permission_allows_continuing(
        self, settings: M1Settings, tmp_path: Path, verdict: str | None
    ) -> None:
        """明示的な許可でのみ続行できる（要件 2.2）。"""
        verification = None if verdict is None else _verification(verdict)
        path = _write_calibration(tmp_path, verification=verification)
        signature, intrinsics = _current_profile_of(path)

        result = open_calibration(
            path,
            settings=settings,
            signature=signature,
            intrinsics=intrinsics,
            allow_unverified=True,
        )
        assert result is not None

    def test_settings_can_turn_the_requirement_off(
        self, settings: M1Settings, tmp_path: Path
    ) -> None:
        """設定でゲートを外すこともできる（既定は有効）。"""
        relaxed = dataclasses.replace(
            settings,
            seam=dataclasses.replace(settings.seam, require_verified_calibration=False),
        )
        path = _write_calibration(tmp_path, verification=None)
        signature, intrinsics = _current_profile_of(path)

        assert open_calibration(
            path, settings=relaxed, signature=signature, intrinsics=intrinsics
        )


class TestUnverifiedMarkPropagates:
    """許可して続行した場合、生成物すべてに未検証の印が伝播する（要件 2.2）。"""

    def test_samples_built_from_an_unverified_calibration_are_marked(
        self, settings: M1Settings, tmp_path: Path
    ) -> None:
        path = _write_calibration(tmp_path, verification=None)
        signature, intrinsics = _current_profile_of(path)
        calibration = open_calibration(
            path,
            settings=settings,
            signature=signature,
            intrinsics=intrinsics,
            allow_unverified=True,
        )

        track = CameraTrack(
            handoff_version=HANDOFF_VERSION,
            frame=CoordinateFrame.CAMERA,
            track_id=1,
            started_t_ms=5000.0,
            points=(
                TrackPoint(
                    point=CameraPoint(
                        frame=CoordinateFrame.CAMERA,
                        t_ms=5010.0,
                        x_mm=0.0,
                        y_mm=0.0,
                        z_mm=1500.0,
                        valid_depth_px=40,
                        depth_spread_mm=10.0,
                        apparent_diameter_px=9.0,
                        expected_diameter_px=8.5,
                        intrinsics_source="stream_profile",
                    ),
                    frame_index=0,
                    frame_seq=1000,
                    gap_before=0,
                    rivals=0,
                ),
            ),
            state=TrackState.ENDED,
            end_reason=None,
            source=SourceKind.SIMULATED,
            detector_kind="depth_band",
        )

        result = build_samples(track, calibration, settings=settings)

        assert result.verified is False
        assert result.verification_state == "not_verified"
        assert len(result.samples) == 1


class TestCompatibility:
    """整合性検査（要件 1.6）。"""

    def test_different_resolution_is_rejected(
        self, settings: M1Settings, tmp_path: Path
    ) -> None:
        """解像度違いのキャリブレーションを拒否する（tasks.md 2.3 の完了状態）。

        解像度が変われば内部パラメータも変わる。古い結果を使い回すと、
        **座標系がわずかにずれたまま検出も予測も経由せず下流へ流れ込む**。
        """
        path = _write_calibration(tmp_path, verification=_verification("passed"))
        signature, intrinsics = _current_profile_of(path)
        current_signature = dataclasses.replace(signature, width_px=1280)

        with pytest.raises(SeamFailure) as exc:
            open_calibration(
                path,
                settings=settings,
                signature=current_signature,
                intrinsics=intrinsics,
            )
        assert exc.value.reason == FailureReason.PROFILE_MISMATCH

    def test_different_intrinsics_is_rejected(
        self, settings: M1Settings, tmp_path: Path
    ) -> None:
        path = _write_calibration(tmp_path, verification=_verification("passed"))
        signature, intrinsics = _current_profile_of(path)
        current_intrinsics = dataclasses.replace(intrinsics, fx_px=999.0)

        with pytest.raises(SeamFailure) as exc:
            open_calibration(
                path,
                settings=settings,
                signature=signature,
                intrinsics=current_intrinsics,
            )
        assert exc.value.reason == FailureReason.PROFILE_MISMATCH

    def test_compatibility_is_checked_before_the_verification_gate(
        self, settings: M1Settings, tmp_path: Path
    ) -> None:
        """設定不一致は未検証より先に出る。

        両方が成り立つとき「未検証だから」とだけ言われると、解像度を戻さずに
        `--allow-unverified` で押し通してしまう。
        """
        path = _write_calibration(tmp_path, verification=None)
        signature, intrinsics = _current_profile_of(path)

        with pytest.raises(SeamFailure) as exc:
            open_calibration(
                path,
                settings=settings,
                signature=dataclasses.replace(signature, width_px=1280),
                intrinsics=intrinsics,
            )
        assert exc.value.reason == FailureReason.PROFILE_MISMATCH


class TestFormatVersion:
    def test_unknown_format_version_is_rejected(
        self, settings: M1Settings, tmp_path: Path
    ) -> None:
        """未知の形式版は内容を推測して読まない（要件 1.5）。"""
        path = tmp_path / "calibration.json"
        path.write_text(
            json.dumps(_calibration_payload(format_version="99.0"), ensure_ascii=False),
            encoding="utf-8",
        )

        with pytest.raises(SeamFailure) as exc:
            open_calibration(
                path, settings=settings, signature=object(), intrinsics=object()
            )
        assert exc.value.reason == FailureReason.UNKNOWN_CALIBRATION_VERSION


class TestUpstreamExceptionsDoNotLeak:
    """上流の例外型をそのまま外へ出さない（接点を1モジュールに閉じる）。"""

    def test_missing_file_raises_this_specs_error(
        self, settings: M1Settings, tmp_path: Path
    ) -> None:
        with pytest.raises(M1ValidationError):
            open_calibration(
                tmp_path / "absent.json",
                settings=settings,
                signature=object(),
                intrinsics=object(),
            )

    def test_malformed_json_raises_this_specs_error(
        self, settings: M1Settings, tmp_path: Path
    ) -> None:
        path = tmp_path / "calibration.json"
        path.write_text("{ not json", encoding="utf-8")
        with pytest.raises(M1ValidationError):
            open_calibration(
                path, settings=settings, signature=object(), intrinsics=object()
            )


class TestCalibrationSummary:
    """後段が読める要約（要件 2.3, 2.4 の材料）。"""

    def test_summary_carries_every_item_the_task_names(self, tmp_path: Path) -> None:
        path = _write_calibration(tmp_path, verification=_verification("passed"))

        summary = calibration_summary(load_calibration(path))

        assert summary["calibration_id"] == "cal-test-0001"
        assert summary["verification_state"] == "passed"
        assert summary["verified"] is True
        assert summary["verified_at_wall_ms"] == 1_700_000_500_000.0
        assert summary["bias_mm"] == [1.0, 2.0, 3.0]
        assert summary["scatter_rms_mm"] == 4.0

    def test_summary_is_json_serialisable(self, tmp_path: Path) -> None:
        """記録・レポートへそのまま載せられる（要件 2.3: 投擲ごとの記録に残す）。"""
        path = _write_calibration(tmp_path, verification=_verification("failed"))

        summary = calibration_summary(load_calibration(path))
        assert json.loads(json.dumps(summary, ensure_ascii=False))

    def test_summary_of_a_never_verified_result_is_still_readable(
        self, tmp_path: Path
    ) -> None:
        """未検証でも識別子と状態は分かる（欠測を `null` で表す）。"""
        path = _write_calibration(tmp_path, verification=None)

        summary = calibration_summary(load_calibration(path))

        assert summary["calibration_id"] == "cal-test-0001"
        assert summary["verification_state"] == "not_verified"
        assert summary["verified"] is False
        assert summary["verified_at_wall_ms"] is None
        assert summary["bias_mm"] is None
