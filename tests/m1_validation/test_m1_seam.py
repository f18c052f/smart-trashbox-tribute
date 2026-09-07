"""継ぎ目（カメラ座標系 → 予測入力）の検証（タスク 2.2 / 要件 1.1-1.5, 1.7-1.9）。

観測可能な完了状態（tasks.md 2.2）を固定する:

- **既知の姿勢で合成した点列を変換すると既知の World 座標と一致する**
- **時刻が入力と同一値**である（再基準化しない）
- **サンプル列と由来情報列の長さが一致する**（タスク1.3 からの申し送り。
  検査の持ち主は本モジュールである）
- 追跡設定の素通し入口が上流の解決結果をそのまま返す
- パイプライン生成入口が**受け取った2値を改変せずに渡す**

あわせて design.md「Seam」が定める点も固定する:

- `flying_object_tracking` / `world_frame_calibration` を import する唯一のモジュール
- **除外理由ごとの件数を必ず返す**（静かに捨てない。要件 1.7）
- `Sample` は4フィールドのまま。カメラ固有の情報を出力側へ出さない（要件 1.9）

`CalibrationResult` は公開入口から組み立てられない（`StreamSignature` /
`Plane` 等が非公開）ため、**本モジュールが実際に触る3属性だけを持つ最小の
ダブル**を使う。`WorldTransform` は公開されているので**本物**を使い、
既知の姿勢に対する変換結果を突き合わせる。
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import math
from dataclasses import dataclass
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
from flying_object_tracking import TrackingSettings as UpstreamTrackingSettings
from m1_validation import seam as seam_module
from m1_validation.config import M1Settings
from m1_validation.errors import FailureReason, M1ConfigError, SeamFailure
from m1_validation.layout import LAYOUT_FORMAT_VERSION
from m1_validation.seam import (
    build_samples,
    camera_ray_unit,
    open_tracking,
    resolve_tracking_settings,
)
from m1_validation.types import SampleReject
from prediction_core import Sample
from world_frame_calibration import WorldTransform

SRC_DIR = Path(__file__).resolve().parents[2] / "src" / "m1_validation"

IDENTITY = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
YAW_90 = ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0))


@dataclass(frozen=True)
class FakeCalibration:
    """`build_samples()` が実際に触る3属性だけを持つ最小のダブル。

    本物の `CalibrationResult` は `StreamSignature` / `Plane` /
    `FrameGeometry` / `AnchorObservation` を要求するが、いずれも上流の公開
    入口に無い。**継ぎ目が使うのはここにある3つだけ**なので、それ以外を
    無理に組み立てても検証の強さは変わらない。
    """

    transform: WorldTransform
    calibration_id: str = "cal-0001"
    verification_state: str = "passed"


def _camera_point(
    *,
    t_ms: float = 0.0,
    x_mm: float = 0.0,
    y_mm: float = 0.0,
    z_mm: float = 1500.0,
    valid_depth_px: int = 40,
    depth_spread_mm: float = 12.0,
    apparent_diameter_px: float = 9.0,
    expected_diameter_px: float = 8.5,
) -> CameraPoint:
    return CameraPoint(
        frame=CoordinateFrame.CAMERA,
        t_ms=t_ms,
        x_mm=x_mm,
        y_mm=y_mm,
        z_mm=z_mm,
        valid_depth_px=valid_depth_px,
        depth_spread_mm=depth_spread_mm,
        apparent_diameter_px=apparent_diameter_px,
        expected_diameter_px=expected_diameter_px,
        intrinsics_source="stream_profile",
    )


def _track_point(point: CameraPoint, *, index: int = 0) -> TrackPoint:
    return TrackPoint(
        point=point,
        frame_index=index,
        frame_seq=1000 + index,
        gap_before=0,
        rivals=0,
    )


def _track(
    *points: CameraPoint,
    frame: object = CoordinateFrame.CAMERA,
    handoff_version: str = HANDOFF_VERSION,
) -> CameraTrack:
    return CameraTrack(
        handoff_version=handoff_version,
        frame=frame,  # type: ignore[arg-type]
        track_id=1,
        # 0 以外にする。0 のままだと  という再基準化が
        # 恒等写像になり、時刻を素通ししているかどうかの検査が空振りする
        # （負の対照で実際に空振りしていることを確認して直した）。
        started_t_ms=5000.0,
        points=tuple(
            _track_point(point, index=index) for index, point in enumerate(points)
        ),
        state=TrackState.ENDED,
        end_reason=None,
        source=SourceKind.SIMULATED,
        detector_kind="depth_band",
    )


@pytest.fixture
def settings(tmp_path: Path) -> M1Settings:
    import json

    layout_path = tmp_path / "layout.json"
    layout_path.write_text(
        json.dumps(
            {
                "format_version": LAYOUT_FORMAT_VERSION,
                "layouts": [
                    {
                        "layout_id": "throw-a",
                        "release_position_world_mm": [-2000.0, 0.0, 1500.0],
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


class TestTransformIsDelegated:
    """座標変換は上流の変換を呼ぶだけ。変換式を再実装しない（要件 1.2）。"""

    def test_known_pose_produces_known_world_coordinates(
        self, settings: M1Settings
    ) -> None:
        """既知の姿勢の点列が既知の World 座標になる（tasks.md 2.2 の完了状態）。"""
        transform = WorldTransform(
            rotation=IDENTITY, translation_mm=(100.0, 200.0, -1000.0)
        )
        track = _track(_camera_point(x_mm=10.0, y_mm=20.0, z_mm=1500.0))

        result = build_samples(
            track, FakeCalibration(transform=transform), settings=settings
        )

        assert len(result.samples) == 1
        sample = result.samples[0]
        assert (sample.x_mm, sample.y_mm, sample.z_mm) == (110.0, 220.0, 500.0)

    def test_rotation_is_applied_by_the_upstream_transform(
        self, settings: M1Settings
    ) -> None:
        """回転も上流に委ねる（自前の行列演算を持たない）。"""
        transform = WorldTransform(rotation=YAW_90, translation_mm=(0.0, 0.0, 0.0))
        track = _track(_camera_point(x_mm=1.0, y_mm=0.0, z_mm=100.0))

        sample = build_samples(
            track, FakeCalibration(transform=transform), settings=settings
        ).samples[0]

        expected = transform.apply_point((1.0, 0.0, 100.0))
        assert (sample.x_mm, sample.y_mm, sample.z_mm) == expected


class TestTimeAndUnitsArePreserved:
    def test_time_is_carried_through_unchanged(self, settings: M1Settings) -> None:
        """**時刻を再基準化しない**（要件 1.3）。

        時間基準をずらすと、上流のログに残る時刻と予測入力の時刻が別物になり、
        end-to-end レイテンシ（観測時刻 → その観測に基づく予測）が測れなくなる。
        """
        transform = WorldTransform(rotation=IDENTITY, translation_mm=(0.0, 0.0, 0.0))
        track = _track(
            _camera_point(t_ms=12345.5, z_mm=1000.0),
            _camera_point(t_ms=12378.25, z_mm=900.0),
        )

        result = build_samples(
            track, FakeCalibration(transform=transform), settings=settings
        )

        assert [sample.t_ms for sample in result.samples] == [12345.5, 12378.25]

    def test_no_unit_conversion_is_applied(self, settings: M1Settings) -> None:
        """単位変換を挟まない（上流・下流ともに mm / ms）。"""
        transform = WorldTransform(rotation=IDENTITY, translation_mm=(0.0, 0.0, 0.0))
        track = _track(_camera_point(x_mm=1234.5, y_mm=-678.25, z_mm=90.125))

        sample = build_samples(
            track, FakeCalibration(transform=transform), settings=settings
        ).samples[0]

        assert (sample.x_mm, sample.y_mm, sample.z_mm) == (1234.5, -678.25, 90.125)


class TestProvenancePairing:
    """サンプル列と由来情報列の長さ一致（タスク1.3 からの申し送り / 要件 1.8）。"""

    def test_lengths_match(self, settings: M1Settings) -> None:
        transform = WorldTransform(rotation=IDENTITY, translation_mm=(0.0, 0.0, 0.0))
        track = _track(*[_camera_point(t_ms=float(i), z_mm=1000.0) for i in range(5)])

        result = build_samples(
            track, FakeCalibration(transform=transform), settings=settings
        )

        assert len(result.samples) == len(result.provenance) == 5

    def test_lengths_still_match_when_points_are_excluded(
        self, settings: M1Settings
    ) -> None:
        """除外があっても対応が崩れない（**ここが壊れやすい**）。"""
        transform = WorldTransform(rotation=IDENTITY, translation_mm=(0.0, 0.0, 0.0))
        track = _track(
            _camera_point(t_ms=0.0, z_mm=1000.0),
            _camera_point(t_ms=1.0, z_mm=1000.0, valid_depth_px=0),
            _camera_point(t_ms=2.0, z_mm=1000.0),
        )

        result = build_samples(
            track, FakeCalibration(transform=transform), settings=settings
        )

        assert len(result.samples) == len(result.provenance) == 2
        assert [sample.t_ms for sample in result.samples] == [0.0, 2.0]

    def test_provenance_carries_the_quality_of_its_own_point(
        self, settings: M1Settings
    ) -> None:
        """同じ順序で対応する（i 番目の由来情報が i 番目のサンプルのもの）。"""
        transform = WorldTransform(rotation=IDENTITY, translation_mm=(0.0, 0.0, 0.0))
        track = _track(
            _camera_point(t_ms=0.0, z_mm=1000.0, valid_depth_px=41, depth_spread_mm=11.0),
            _camera_point(t_ms=1.0, z_mm=1000.0, valid_depth_px=42, depth_spread_mm=22.0),
        )

        result = build_samples(
            track, FakeCalibration(transform=transform), settings=settings
        )

        assert [item.valid_depth_px for item in result.provenance] == [41, 42]
        assert [item.depth_spread_mm for item in result.provenance] == [11.0, 22.0]

    def test_provenance_carries_the_frame_identity(self, settings: M1Settings) -> None:
        """元フレームへ辿れる識別情報を引き継ぐ（要件 1.8）。"""
        transform = WorldTransform(rotation=IDENTITY, translation_mm=(0.0, 0.0, 0.0))
        result = build_samples(
            _track(_camera_point(z_mm=1000.0)),
            FakeCalibration(transform=transform),
            settings=settings,
        )
        assert result.provenance[0].frame_index == 0
        assert result.provenance[0].frame_seq == 1000


class TestFailures:
    def test_non_camera_frame_is_rejected(self, settings: M1Settings) -> None:
        """座標系がカメラ座標系でなければサンプルを作らない（要件 1.4）。

        ⚠️ 上流の `CoordinateFrame` は**メンバーが `CAMERA` ただ1つ**であり、
        「カメラ座標系以外を取り得ない」ことを型で保証している。したがって
        この検査を働かせるには、**現行の列挙から来ていない値**を渡すしかない
        ——それはまさにこの検査が備える相手である（上流が将来メンバーを
        増やした場合、あるいは手で組んだ値が流れ込んだ場合）。
        """
        transform = WorldTransform(rotation=IDENTITY, translation_mm=(0.0, 0.0, 0.0))
        track = _track(_camera_point(), frame="world")

        with pytest.raises(SeamFailure) as exc:
            build_samples(track, FakeCalibration(transform=transform), settings=settings)
        assert exc.value.reason == FailureReason.FRAME_MISMATCH

    def test_unknown_handoff_version_is_rejected(self, settings: M1Settings) -> None:
        """未知の受け渡し形式版は**推測して読まない**（要件 1.5）。"""
        transform = WorldTransform(rotation=IDENTITY, translation_mm=(0.0, 0.0, 0.0))
        track = _track(_camera_point(), handoff_version="99.0")

        with pytest.raises(SeamFailure) as exc:
            build_samples(track, FakeCalibration(transform=transform), settings=settings)
        assert exc.value.reason == FailureReason.UNKNOWN_HANDOFF_VERSION
        assert "99.0" in str(exc.value)


class TestExclusion:
    """除外理由ごとの件数を必ず返す（要件 1.7）。静かに捨てない。"""

    def _run(self, settings: M1Settings, *points: CameraPoint):
        transform = WorldTransform(rotation=IDENTITY, translation_mm=(0.0, 0.0, 0.0))
        return build_samples(
            _track(*points), FakeCalibration(transform=transform), settings=settings
        )

    def test_not_finite_point_is_excluded_with_its_reason(
        self, settings: M1Settings
    ) -> None:
        result = self._run(settings, _camera_point(x_mm=math.nan, z_mm=1000.0))
        assert dict(result.rejected) == {SampleReject.NOT_FINITE: 1}
        assert result.samples == ()

    def test_below_floor_point_is_excluded(self, settings: M1Settings) -> None:
        """床面下は除外する（既定の余裕は -50mm）。"""
        result = self._run(settings, _camera_point(z_mm=-100.0))
        assert dict(result.rejected) == {SampleReject.BELOW_FLOOR: 1}

    def test_point_just_inside_the_floor_margin_survives(
        self, settings: M1Settings
    ) -> None:
        """余裕の内側は残す（境界を跨いだ瞬間だけ落ちる）。"""
        result = self._run(settings, _camera_point(z_mm=-49.0))
        assert len(result.samples) == 1

    def test_depth_spread_over_the_limit_is_excluded(self, settings: M1Settings) -> None:
        result = self._run(settings, _camera_point(z_mm=1000.0, depth_spread_mm=1000.0))
        assert dict(result.rejected) == {SampleReject.DEPTH_SPREAD_TOO_LARGE: 1}

    def test_insufficient_valid_pixels_is_excluded(self, settings: M1Settings) -> None:
        result = self._run(settings, _camera_point(z_mm=1000.0, valid_depth_px=1))
        assert dict(result.rejected) == {SampleReject.INSUFFICIENT_VALID_PIXELS: 1}

    def test_counts_are_per_reason_and_sum_to_the_excluded_total(
        self, settings: M1Settings
    ) -> None:
        """件数の合計が除外された点数と一致する（1点は1理由で数える）。

        複数の理由に該当する点を理由ごとに重複計上すると、合計が点数と
        合わなくなり「何点落ちたのか」が読めなくなる。
        """
        result = self._run(
            settings,
            _camera_point(z_mm=1000.0),
            _camera_point(z_mm=1000.0, valid_depth_px=0),
            _camera_point(z_mm=1000.0, valid_depth_px=0, depth_spread_mm=9999.0),
            _camera_point(z_mm=-500.0),
        )
        assert len(result.samples) == 1
        assert sum(count for _, count in result.rejected) == 3

    def test_reasons_with_no_occurrence_are_not_listed(
        self, settings: M1Settings
    ) -> None:
        """0件の理由を並べない（読み手が「起きた」と誤読しない）。"""
        result = self._run(settings, _camera_point(z_mm=1000.0))
        assert result.rejected == ()


class TestOutputContract:
    """カメラ固有の情報を予測入力側へ出さない（要件 1.9）。"""

    def test_samples_are_plain_prediction_core_samples(
        self, settings: M1Settings
    ) -> None:
        transform = WorldTransform(rotation=IDENTITY, translation_mm=(0.0, 0.0, 0.0))
        result = build_samples(
            _track(_camera_point(z_mm=1000.0)),
            FakeCalibration(transform=transform),
            settings=settings,
        )
        assert type(result.samples[0]) is Sample

    def test_sample_still_has_exactly_four_fields(self) -> None:
        """`Sample` は4フィールドのまま（品質情報を入れない）。

        入れた時点で `prediction_core` の入力契約が壊れる——予測はデバイス
        固有の情報に依存してはならない。品質情報は `SampleProvenance` の側で
        並走させる。
        """
        assert [field.name for field in dataclasses.fields(Sample)] == [
            "t_ms",
            "x_mm",
            "y_mm",
            "z_mm",
        ]

    def test_no_pixel_information_leaks_into_the_samples(
        self, settings: M1Settings
    ) -> None:
        transform = WorldTransform(rotation=IDENTITY, translation_mm=(0.0, 0.0, 0.0))
        sample = build_samples(
            _track(_camera_point(z_mm=1000.0)),
            FakeCalibration(transform=transform),
            settings=settings,
        ).samples[0]
        assert not any("px" in name for name in dataclasses.asdict(sample))


class TestCalibrationMarkers:
    """使用したキャリブレーションの識別子と検証状態を伝える（要件 2.3, 2.2）。"""

    def test_identity_and_state_are_carried(self, settings: M1Settings) -> None:
        transform = WorldTransform(rotation=IDENTITY, translation_mm=(0.0, 0.0, 0.0))
        result = build_samples(
            _track(_camera_point(z_mm=1000.0)),
            FakeCalibration(transform=transform, calibration_id="cal-xyz"),
            settings=settings,
        )
        assert result.calibration_id == "cal-xyz"
        assert result.verification_state == "passed"
        assert result.verified is True
        assert result.handoff_version == HANDOFF_VERSION

    @pytest.mark.parametrize("state", ["not_verified", "failed", "not_judged"])
    def test_anything_other_than_passed_is_unverified(
        self, settings: M1Settings, state: str
    ) -> None:
        """合格以外はすべて未検証の印が付く（要件 2.2）。

        「検証を実施したが不合格」を検証済みとして扱うと、**誤差の帰属が
        できないデータを気づかず判断に使う**。
        """
        transform = WorldTransform(rotation=IDENTITY, translation_mm=(0.0, 0.0, 0.0))
        result = build_samples(
            _track(_camera_point(z_mm=1000.0)),
            FakeCalibration(transform=transform, verification_state=state),
            settings=settings,
        )
        assert result.verified is False
        assert result.verification_state == state


class TestCameraRayUnit:
    """World 系で表したカメラ視線方向（要件 6.3 の材料）。"""

    def test_points_from_the_camera_origin_towards_the_point(self) -> None:
        transform = WorldTransform(
            rotation=IDENTITY, translation_mm=(0.0, -1500.0, 1000.0)
        )
        calibration = FakeCalibration(transform=transform)

        # カメラ原点は World の (0, -1500, 1000)。そこから +Y へ 1500 進むと原点。
        unit = camera_ray_unit(calibration, (0.0, 0.0, 1000.0))

        assert unit == pytest.approx((0.0, 1.0, 0.0))

    def test_result_is_a_unit_vector(self) -> None:
        transform = WorldTransform(
            rotation=IDENTITY, translation_mm=(10.0, 20.0, 30.0)
        )
        unit = camera_ray_unit(FakeCalibration(transform=transform), (110.0, 220.0, 60.0))
        assert math.hypot(*unit) == pytest.approx(1.0)

    def test_direction_changes_with_the_point(self) -> None:
        """向きが点に依存する（帰属はこの違いで原因を切り分ける）。"""
        transform = WorldTransform(rotation=IDENTITY, translation_mm=(0.0, 0.0, 0.0))
        calibration = FakeCalibration(transform=transform)
        assert camera_ray_unit(calibration, (1.0, 0.0, 0.0)) != camera_ray_unit(
            calibration, (0.0, 1.0, 0.0)
        )

    def test_point_at_the_camera_origin_is_rejected(self) -> None:
        """方向が定まらない入力を黙って 0 ベクトルにしない。"""
        transform = WorldTransform(rotation=IDENTITY, translation_mm=(5.0, 5.0, 5.0))
        with pytest.raises(M1ConfigError):
            camera_ray_unit(FakeCalibration(transform=transform), (5.0, 5.0, 5.0))

    def test_provenance_gets_the_ray_for_its_own_sample(
        self, settings: M1Settings
    ) -> None:
        transform = WorldTransform(
            rotation=IDENTITY, translation_mm=(0.0, -1000.0, 0.0)
        )
        calibration = FakeCalibration(transform=transform)
        result = build_samples(
            _track(_camera_point(x_mm=0.0, y_mm=1000.0, z_mm=0.0)),
            calibration,
            settings=settings,
        )
        sample = result.samples[0]
        assert result.provenance[0].camera_ray_unit == pytest.approx(
            camera_ray_unit(calibration, (sample.x_mm, sample.y_mm, sample.z_mm))
        )


class TestTrackingEntryPoints:
    """検出・追跡パッケージへの入口は2つだけ（tasks.md 2.2）。"""

    def test_resolve_returns_the_upstream_result_unchanged(self, tmp_path: Path) -> None:
        """素通しの入口が上流の解決結果をそのまま返す。

        **本 Spec は追跡の設定値も方式も決めない。** この入口があるのは、
        検出・追跡パッケージを import するのが本モジュールだけという境界を
        保ったまま、入口層が上流の解決結果を手に入れられるようにするため
        だけである。
        """
        resolved = resolve_tracking_settings(config_path=None, env={}, overrides={})
        assert resolved == UpstreamTrackingSettings.resolve(
            file=None, env={}, overrides={}
        )

    def test_resolve_passes_all_three_layers_through(self, tmp_path: Path) -> None:
        """3層とも上流へ渡す（内部で空に埋めない）。

        一部を落とすと**上流側の環境変数と CLI 上書きが黙って捨てられる**。
        """
        import json

        config_path = tmp_path / "tracking.json"
        config_path.write_text(
            json.dumps({"object_diameter_mm": 70.0}), encoding="utf-8"
        )
        via_seam = resolve_tracking_settings(
            config_path=config_path, env={}, overrides={"object_diameter_mm": 90.0}
        )
        direct = UpstreamTrackingSettings.resolve(
            file=config_path, env={}, overrides={"object_diameter_mm": 90.0}
        )
        assert via_seam == direct

    def test_open_tracking_passes_both_values_through_unchanged(
        self, settings: M1Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """パイプライン生成入口が受け取った2値を改変せずに渡す。

        **本 Spec が生成できない2つの値**（上流の追跡設定と上流のログ器）を
        引数で受け取り、そのまま渡すだけにする。片方でも本モジュールが
        こしらえると、追跡の設定を本 Spec が決めたことになる。
        """
        seen: list[tuple[object, object]] = []

        class RecordingPipeline:
            def __init__(self, tracking_settings: object, logger: object) -> None:
                seen.append((tracking_settings, logger))

        monkeypatch.setattr(seam_module, "TrackingPipeline", RecordingPipeline)

        sentinel_settings = object()
        sentinel_logger = object()
        pipeline = open_tracking(settings, sentinel_settings, sentinel_logger)

        assert isinstance(pipeline, RecordingPipeline)
        assert seen == [(sentinel_settings, sentinel_logger)]
        assert seen[0][0] is sentinel_settings
        assert seen[0][1] is sentinel_logger


class TestSoleContactPoint:
    """検出・追跡・較正パッケージを import する唯一のモジュールである。"""

    @pytest.mark.parametrize(
        "package", ["flying_object_tracking", "world_frame_calibration"]
    )
    def test_no_other_m1_module_imports_it(self, package: str) -> None:
        offenders: list[str] = []
        for path in sorted(SRC_DIR.rglob("*.py")):
            if path.name == "seam.py":
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                roots: list[str] = []
                if isinstance(node, ast.Import):
                    roots = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    roots = [node.module.split(".")[0]]
                if package in roots:
                    offenders.append(f"{path.name}:{node.lineno}")
        assert offenders == []

    def test_seam_does_not_import_sensing_foundation(self) -> None:
        """取得基盤の接点は `upstream.py` のままである（穴を増やさない）。"""
        source = Path(inspect.getfile(seam_module)).read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] != "sensing_foundation"
            if isinstance(node, ast.Import):
                assert all(
                    alias.name.split(".")[0] != "sensing_foundation"
                    for alias in node.names
                )
