"""1投擲の実行と Throw Record への記録の検証（タスク 3.1 / 要件 2.3, 3.1-3.6, 7.4）。

観測可能な完了状態（tasks.md 3.1）を固定する:

- **合成入力に対して1投擲が通り、記録を読み戻すと予測系列と由来情報が復元される**

あわせて design.md「ThrowRunner」が定める点も固定する:

- **検証ゲートを投擲を始める前に評価する**（走らせてから拒否しない）
- サンプルが追加されるたびに予測を更新する（再計算は予測コアに委ねる）
- `extra["m1"]` へ本 Spec 固有の情報を退避し、他の拡張キーを上書きしない
- **予測経路の中で集計しない**（送出は fire-and-forget）
- `runner.py` は上流パッケージを import しない（接点は `upstream.py` /
  `seam.py` の2つだけ）

**追跡パイプラインはダブルにする。** 本タスクが持つのは「取得 → 追跡 →
継ぎ目 → 逐次予測 → 記録」という**並び**であり、検出そのものは上流の責務で
ある。実物の検出器を通すと、何が失敗したのか（並びか検出か）が読めなくなる。
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
from m1fixtures import verification_summary, write_calibration, write_layout

from flying_object_tracking import (
    HANDOFF_VERSION,
    CameraPoint,
    CameraTrack,
    CoordinateFrame,
    SourceKind,
    TrackPoint,
    TrackState,
    TrackUpdate,
)
from m1_validation import runner as runner_module
from m1_validation.config import M1Settings
from m1_validation.errors import SeamFailure
from m1_validation.runner import ThrowRunResult, run_throw
from m1_validation.types import M1_EXTRA_VERSION
from m1_validation.upstream import (
    STAGE_PREDICT,
    UpstreamGateway,
    resolve_runtime_settings,
)
from prediction_core import SCHEMA_VERSION
from sensing_foundation import INVALID_DEPTH_RAW

SRC_DIR = Path(__file__).resolve().parents[2] / "src" / "m1_validation"

WIDTH_PX = 8
HEIGHT_PX = 6


# ---------------------------------------------------------------------------
# 追跡パイプラインのダブル
# ---------------------------------------------------------------------------


class FakePipeline:
    """フレーム1枚につき1点を追加する追跡パイプライン（`process()` だけを持つ）。

    既知の放物軌道をカメラ座標で刻んで返す。**検出の良し悪しは本タスクの
    関心ではない**ので、点は決め打ちで与える。
    """

    def __init__(self, points: list[CameraPoint], *, track_id: int = 7) -> None:
        self._points = points
        self._track_id = track_id
        self._appended: list[TrackPoint] = []
        self.processed_frames = 0

    def process(self, frame: object) -> TrackUpdate:
        self.processed_frames += 1
        index = len(self._appended)
        appended: TrackPoint | None = None
        if index < len(self._points):
            appended = TrackPoint(
                point=self._points[index],
                frame_index=index,
                frame_seq=1000 + index,
                gap_before=0,
                rivals=0,
            )
            self._appended.append(appended)
        return TrackUpdate(
            track=self._track(),
            appended=appended,
            candidates=1 if appended is not None else 0,
            rejections=(),
            point_failures=(),
        )

    def _track(self) -> CameraTrack:
        return CameraTrack(
            handoff_version=HANDOFF_VERSION,
            frame=CoordinateFrame.CAMERA,
            track_id=self._track_id,
            started_t_ms=5000.0,
            points=tuple(self._appended),
            state=TrackState.TRACKING,
            end_reason=None,
            source=SourceKind.SIMULATED,
            detector_kind="depth_band",
        )


def _camera_point(*, t_ms: float, z_mm: float, valid_depth_px: int = 40) -> CameraPoint:
    return CameraPoint(
        frame=CoordinateFrame.CAMERA,
        t_ms=t_ms,
        x_mm=0.0,
        y_mm=0.0,
        z_mm=z_mm,
        valid_depth_px=valid_depth_px,
        depth_spread_mm=10.0,
        apparent_diameter_px=9.0,
        expected_diameter_px=8.5,
        intrinsics_source="stream_profile",
    )


def _falling_points(count: int = 6) -> list[CameraPoint]:
    """カメラ座標で落下していく点列（World へ移すと z が減っていく）。"""
    return [
        _camera_point(t_ms=5000.0 + 33.0 * i, z_mm=2500.0 - 60.0 * i)
        for i in range(count)
    ]


# ---------------------------------------------------------------------------
# 実行環境
# ---------------------------------------------------------------------------


def _depth_supplier(frame_count: int):
    def supplier(index: int):
        if index >= frame_count:
            return None
        return np.full((HEIGHT_PX, WIDTH_PX), INVALID_DEPTH_RAW + 1, dtype=np.uint16)

    return supplier


@pytest.fixture
def settings(tmp_path: Path) -> M1Settings:
    return M1Settings.resolve(
        file=None,
        env={},
        overrides={"layout_file": str(write_layout(tmp_path))},
    )


@pytest.fixture
def gateway(tmp_path: Path) -> Iterator[UpstreamGateway]:
    spec = resolve_runtime_settings(
        file=None,
        env={},
        overrides={
            "source": "simulated",
            "width_px": WIDTH_PX,
            "height_px": HEIGHT_PX,
            "fps": 30,
            "logging_path": str(tmp_path / "logs"),
            "recording_root": str(tmp_path / "sessions"),
        },
    )
    gw = UpstreamGateway.open(session_id="test-runner", source_spec=spec)
    yield gw
    gw.close()


def _current_profile_of(path: Path):
    from world_frame_calibration import load_calibration

    loaded = load_calibration(path)
    return loaded.signature, loaded.intrinsics


def _run(
    settings: M1Settings,
    gateway: UpstreamGateway,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    points: list[CameraPoint] | None = None,
    verification: dict[str, object] | None = None,
    frame_count: int = 6,
    record_id: str = "throw-0001",
    allow_unverified: bool = False,
) -> tuple[ThrowRunResult, FakePipeline]:
    calibration_path = write_calibration(
        tmp_path,
        verification=verification_summary() if verification is None else verification,
    )
    signature, intrinsics = _current_profile_of(calibration_path)
    pipeline = FakePipeline(_falling_points() if points is None else points)
    monkeypatch.setattr(runner_module, "open_tracking", lambda *a, **k: pipeline)

    result = run_throw(
        settings=settings,
        gateway=gateway,
        calibration_path=calibration_path,
        record_id=record_id,
        tracking_settings=object(),
        signature=signature,
        intrinsics=intrinsics,
        supplier=_depth_supplier(frame_count),
        allow_unverified=allow_unverified,
    )
    return result, pipeline


# ---------------------------------------------------------------------------


class TestOneThrowRunsThrough:
    def test_synthetic_input_produces_a_record(
        self, settings, gateway, tmp_path, monkeypatch
    ) -> None:
        result, pipeline = _run(settings, gateway, tmp_path, monkeypatch)

        assert isinstance(result, ThrowRunResult)
        assert result.record_id == "throw-0001"
        assert result.samples_appended == 6
        assert pipeline.processed_frames == 6
        assert result.failed_reason is None

    def test_prediction_is_updated_for_every_sample(
        self, settings, gateway, tmp_path, monkeypatch
    ) -> None:
        """サンプルが追加されるたびに予測を更新する（要件 3.2）。

        予測系列の長さがサンプル数と一致することで、**まとめて1回だけ
        予測する**実装になっていないことを示す。
        """
        result, _ = _run(settings, gateway, tmp_path, monkeypatch)

        assert len(result.record.samples) == 6
        assert len(result.record.predictions) == 6

    def test_time_is_carried_from_the_track_points(
        self, settings, gateway, tmp_path, monkeypatch
    ) -> None:
        """観測時刻がそのまま予測入力へ渡る（継ぎ目の契約が保たれている）。"""
        result, _ = _run(settings, gateway, tmp_path, monkeypatch)
        assert [sample.t_ms for sample in result.record.samples] == [
            5000.0 + 33.0 * i for i in range(6)
        ]

    def test_source_kind_is_recorded(
        self, settings, gateway, tmp_path, monkeypatch
    ) -> None:
        """入力元の種別を記録に残す（要件 3.6）。"""
        result, _ = _run(settings, gateway, tmp_path, monkeypatch)
        assert result.record.source == "simulated"


class TestSchemaIsNotRedefined:
    def test_record_uses_the_prediction_core_schema(
        self, settings, gateway, tmp_path, monkeypatch
    ) -> None:
        """記録は予測コアのスキーマをそのまま使う（要件 3.3 / 3.4）。"""
        result, _ = _run(settings, gateway, tmp_path, monkeypatch)
        assert result.record.schema_version == SCHEMA_VERSION

    def test_record_round_trips_through_the_gateway(
        self, settings, gateway, tmp_path, monkeypatch
    ) -> None:
        """**記録を読み戻すと予測系列と由来情報が復元される**（完了状態）。"""
        result, _ = _run(settings, gateway, tmp_path, monkeypatch)
        path = tmp_path / "throws.ndjson"
        gateway.store_record(result.record, path)

        loaded = list(gateway.load_records(path))

        assert len(loaded) == 1
        restored = loaded[0]
        assert [s.t_ms for s in restored.samples] == [
            s.t_ms for s in result.record.samples
        ]
        assert len(restored.predictions) == len(result.record.predictions)
        assert len(restored.extra["m1"]["provenance"]) == len(restored.samples)


class TestM1Extra:
    """本 Spec 固有の情報を拡張領域へ退避する（要件 3.4）。"""

    def test_extra_carries_every_key_the_data_model_names(
        self, settings, gateway, tmp_path, monkeypatch
    ) -> None:
        result, _ = _run(settings, gateway, tmp_path, monkeypatch)
        m1 = result.record.extra["m1"]

        assert set(m1) == {
            "m1_extra_version",
            "layout",
            "calibration",
            "tracking",
            "provenance",
            "rejected",
            "truth",
            "verified",
            "failed_reason",
        }
        assert m1["m1_extra_version"] == M1_EXTRA_VERSION

    def test_only_the_m1_key_is_added(
        self, settings, gateway, tmp_path, monkeypatch
    ) -> None:
        """他の拡張キーを上書きしない。

        `sensing_foundation.link_to_session()` が後から `extra["sensing"]` を
        足す（要件 7.7）。ここで `extra` を丸ごと置き換える書き方をすると、
        **順序が変わった瞬間に対応付けが消える**。
        """
        result, _ = _run(settings, gateway, tmp_path, monkeypatch)
        assert set(result.record.extra) == {"m1"}

    def test_an_existing_extra_key_survives_the_m1_payload(
        self, settings, gateway, tmp_path, monkeypatch
    ) -> None:
        """既にある拡張キーを保つ（`extra` を丸ごと置き換えない）。

        ⚠️ **この検査だけは本 Spec の私有ヘルパを直接呼ぶ。**
        `ThrowPredictionTracker.to_record()` が返す `extra` は常に空なので、
        公開経路（`run_throw`）からは「既存キーが残るか」を確かめられない
        ——`set(extra) == {"m1"}` は、丸ごと置き換える実装でも通ってしまう
        （タスク2.2 で見つけた「0 を既定値にしたフィクスチャ」と同じ空振り）。
        `sensing_foundation.link_to_session()` が `extra["sensing"]` を足す
        順序が変われば、この空振りが**そのまま対応付けの消失**になる。
        """
        from world_frame_calibration import load_calibration

        result, _ = _run(settings, gateway, tmp_path, monkeypatch)
        seeded = dataclasses.replace(
            result.record, extra={"sensing": {"session_id": "s-1"}}
        )
        calibration = load_calibration(
            write_calibration(tmp_path, verification=verification_summary())
        )

        merged = runner_module._with_m1_extra(
            seeded,
            settings=settings,
            calibration=calibration,
            track=None,
            provenance=[],
            rejected={},
            failed_reason=None,
        )

        assert set(merged.extra) == {"sensing", "m1"}
        assert merged.extra["sensing"] == {"session_id": "s-1"}

    def test_provenance_matches_the_samples_one_for_one(
        self, settings, gateway, tmp_path, monkeypatch
    ) -> None:
        result, _ = _run(settings, gateway, tmp_path, monkeypatch)
        m1 = result.record.extra["m1"]
        assert len(m1["provenance"]) == len(result.record.samples)
        assert m1["provenance"][0]["frame_seq"] == 1000

    def test_tracking_identity_is_recorded(
        self, settings, gateway, tmp_path, monkeypatch
    ) -> None:
        """追跡の識別情報を残す（生データへ辿るため。要件 3.5）。"""
        tracking = _run(settings, gateway, tmp_path, monkeypatch)[0].record.extra["m1"][
            "tracking"
        ]
        assert tracking["handoff_version"] == HANDOFF_VERSION
        assert tracking["track_id"] == 7
        assert tracking["detector_kind"] == "depth_band"
        assert tracking["started_t_ms"] == 5000.0

    def test_calibration_summary_is_recorded(
        self, settings, gateway, tmp_path, monkeypatch
    ) -> None:
        """使用したキャリブレーションを記録へ残す（要件 2.3）。"""
        calibration = _run(settings, gateway, tmp_path, monkeypatch)[0].record.extra[
            "m1"
        ]["calibration"]
        assert calibration["calibration_id"] == "cal-test-0001"
        assert calibration["verification_state"] == "passed"

    def test_layout_summary_is_recorded(
        self, settings, gateway, tmp_path, monkeypatch
    ) -> None:
        layout = _run(settings, gateway, tmp_path, monkeypatch)[0].record.extra["m1"][
            "layout"
        ]
        assert layout["layout_id"] == "throw-a"

    def test_truth_is_absent_until_it_is_added_later(
        self, settings, gateway, tmp_path, monkeypatch
    ) -> None:
        """真値は投擲の実行とは分離して後から追記する（要件 4.7）。"""
        result, _ = _run(settings, gateway, tmp_path, monkeypatch)
        assert result.record.extra["m1"]["truth"] is None

    def test_extra_is_json_serialisable(
        self, settings, gateway, tmp_path, monkeypatch
    ) -> None:
        result, _ = _run(settings, gateway, tmp_path, monkeypatch)
        assert json.loads(json.dumps(result.record.extra, ensure_ascii=False))


class TestVerificationGateRunsFirst:
    """**検証ゲートを投擲を始める前に評価する**（走らせてから拒否しない）。"""

    def test_unverified_calibration_stops_before_any_frame_is_processed(
        self, settings, gateway, tmp_path, monkeypatch
    ) -> None:
        """1枚もフレームを引かずに失敗する。

        投げてから拒否すると、**その1投擲を無駄にする**。人が物を投げる
        作業なので、投げ直しの費用は小さくない。
        """
        calibration_path = write_calibration(tmp_path, verification=None)
        signature, intrinsics = _current_profile_of(calibration_path)
        pipeline = FakePipeline(_falling_points())
        monkeypatch.setattr(runner_module, "open_tracking", lambda *a, **k: pipeline)

        with pytest.raises(SeamFailure):
            run_throw(
                settings=settings,
                gateway=gateway,
                calibration_path=calibration_path,
                record_id="throw-0002",
                tracking_settings=object(),
                signature=signature,
                intrinsics=intrinsics,
                supplier=_depth_supplier(6),
            )

        assert pipeline.processed_frames == 0

    def test_explicit_permission_marks_the_record(
        self, settings, gateway, tmp_path, monkeypatch
    ) -> None:
        """許可して続行した場合、記録に未検証の印が残る（要件 2.2）。"""
        calibration_path = write_calibration(tmp_path, verification=None)
        signature, intrinsics = _current_profile_of(calibration_path)
        monkeypatch.setattr(
            runner_module, "open_tracking", lambda *a, **k: FakePipeline(_falling_points())
        )

        result = run_throw(
            settings=settings,
            gateway=gateway,
            calibration_path=calibration_path,
            record_id="throw-0003",
            tracking_settings=object(),
            signature=signature,
            intrinsics=intrinsics,
            supplier=_depth_supplier(6),
            allow_unverified=True,
        )

        assert result.record.extra["m1"]["verified"] is False
        assert result.record.extra["m1"]["calibration"]["verification_state"] == (
            "not_verified"
        )


class TestExclusionsAreCounted:
    def test_rejected_points_are_counted_and_recorded(
        self, settings, gateway, tmp_path, monkeypatch
    ) -> None:
        """除外は静かに行わない（要件 1.7）。件数が結果と記録の両方に出る。"""
        points = [
            _camera_point(t_ms=5000.0, z_mm=2500.0),
            _camera_point(t_ms=5033.0, z_mm=2400.0, valid_depth_px=0),
            _camera_point(t_ms=5066.0, z_mm=2300.0),
        ]
        result, _ = _run(settings, gateway, tmp_path, monkeypatch, points=points)

        assert result.samples_appended == 2
        assert dict(result.rejected) == {"insufficient_valid_pixels": 1}
        assert result.record.extra["m1"]["rejected"] == [
            {"reason": "insufficient_valid_pixels", "count": 1}
        ]


class TestMetrics:
    def test_a_predict_stage_event_is_emitted_per_prediction(
        self, settings, gateway, tmp_path, monkeypatch
    ) -> None:
        """予測ごとに `predict` 区間の計測を送出する（要件 7.4）。"""
        _run(settings, gateway, tmp_path, monkeypatch)
        gateway.close()

        rows = [
            json.loads(line)
            for path in (tmp_path / "logs").glob("*.ndjson")
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        predicts = [row for row in rows if row.get("stage") == STAGE_PREDICT]
        assert len(predicts) == 6

    def test_the_material_for_end_to_end_latency_is_left_behind(
        self, settings, gateway, tmp_path, monkeypatch
    ) -> None:
        """観測時刻とその観測に基づく予測が得られた時刻の**両方**を残す。

        end-to-end レイテンシは「観測時刻 → その観測に基づく予測」と定義
        されている（要件 7.2）。片方しか残さないと後から算出できない。
        """
        _run(settings, gateway, tmp_path, monkeypatch)
        gateway.close()

        rows = [
            json.loads(line)
            for path in (tmp_path / "logs").glob("*.ndjson")
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        first = next(row for row in rows if row.get("stage") == STAGE_PREDICT)
        assert first["data"]["sample_t_ms"] == 5000.0
        assert "predicted_at_ms" in first["data"]
        assert first["data"]["sample_count"] == 1


class TestNoAggregationInThePredictionPath:
    def test_runner_never_summarises(self) -> None:
        """予測経路の中で集計・統計処理を行わない（`tech.md` 開発標準5）。

        取得中に集計が乗ると、**計測対象そのものを歪める**。集計はログを
        後から読む側の仕事である。
        """
        source = Path(inspect.getfile(runner_module)).read_text(encoding="utf-8")
        assert "summarize_stages" not in source
        assert "statistics" not in source


class TestBoundary:
    """`runner.py` は上流パッケージを import しない。"""

    @pytest.mark.parametrize(
        "package",
        ["sensing_foundation", "flying_object_tracking", "world_frame_calibration"],
    )
    def test_no_upstream_import(self, package: str) -> None:
        source = Path(inspect.getfile(runner_module)).read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                assert all(
                    alias.name.split(".")[0] != package for alias in node.names
                ), f"{package} at line {node.lineno}"
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] != package, (
                    f"{package} at line {node.lineno}"
                )

    def test_opaque_values_are_not_annotated_with_upstream_types(self) -> None:
        """上流の値の注釈を `object` に留める（型の上でも境界を守る）。"""
        signature = inspect.signature(run_throw)
        for name in ("tracking_settings", "signature", "intrinsics"):
            annotation = signature.parameters[name].annotation
            assert annotation in ("object", object), (name, annotation)


class TestResultShape:
    def test_result_is_immutable(self, settings, gateway, tmp_path, monkeypatch) -> None:
        result, _ = _run(settings, gateway, tmp_path, monkeypatch)
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.record_id = "other"  # type: ignore[misc]

    def test_first_valid_sample_count_is_reported(
        self, settings, gateway, tmp_path, monkeypatch
    ) -> None:
        """初回予測が成立したサンプル数を残す（要件 5.3 の材料）。"""
        result, _ = _run(settings, gateway, tmp_path, monkeypatch)
        assert result.first_valid_sample_count is None or (
            1 <= result.first_valid_sample_count <= 6
        )
