"""1投擲の実行と Throw Record への記録の検証
（タスク 3.1 / 3.2、要件 2.3, 3.1-3.8, 7.4）。

観測可能な完了状態（tasks.md 3.1 / 3.2）を固定する:

- **合成入力に対して1投擲が通り、記録を読み戻すと予測系列と由来情報が復元される**
- **検出が1点も出ない入力で失敗理由付きの記録が残り、同一入力の2回実行が
  同一内容の記録を生成する**

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
from m1_validation.errors import FailureReason, M1ConfigError, SeamFailure
from m1_validation.runner import (
    UPSTREAM_FAILURE,
    ThrowRunResult,
    failed_reason_of,
    run_throw,
    successful_throws,
)
from m1_validation.types import M1_EXTRA_VERSION
from m1_validation.upstream import (
    STAGE_PREDICT,
    UpstreamGateway,
    resolve_runtime_settings,
)
from prediction_core import SCHEMA_VERSION, predictions_equivalent
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
            # 点が1つも無い点列を `TRACKING` と名乗らせないための分岐。
            # **記録からは観測できない**（`extra["m1"]["tracking"]` に
            # `state` は無い）ので、この分岐を落としても本ファイルのテストは
            # 全て通る。それでも上流の意味に合わせておく——ダブルがここで
            # 嘘をつくと、追跡状態を記録へ足す後続タスクが誤った前提に乗る。
            state=TrackState.TRACKING if self._appended else TrackState.IDLE,
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
    pipeline: object | None = None,
    supplier: object | None = None,
) -> tuple[ThrowRunResult, object]:
    calibration_path = write_calibration(
        tmp_path,
        verification=verification_summary() if verification is None else verification,
    )
    signature, intrinsics = _current_profile_of(calibration_path)
    if pipeline is None:
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
        supplier=_depth_supplier(frame_count) if supplier is None else supplier,
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


# ---------------------------------------------------------------------------
# タスク 3.2: 失敗投擲の扱いと再実行の一致（要件 3.7, 3.8）
# ---------------------------------------------------------------------------


class ExplodingPipeline:
    """`fail_at` 枚目のフレームで上流の失敗を模す追跡パイプライン。

    実験中に上流が落ちても**1投擲を失うだけ**で済むことを示すための
    ダブルである（design.md「ThrowRunner / Implementation Notes」Risks）。
    """

    def __init__(
        self,
        points: list[CameraPoint],
        *,
        fail_at: int,
        error: BaseException | None = None,
    ) -> None:
        self._inner = FakePipeline(points)
        self._fail_at = fail_at
        self._error = RuntimeError("追跡が落ちた") if error is None else error
        self.processed_frames = 0

    def process(self, frame: object) -> TrackUpdate:
        self.processed_frames += 1
        if self.processed_frames == self._fail_at:
            raise self._error
        return self._inner.process(frame)


def _exploding_supplier(fail_at: int):
    """`fail_at` 枚目で落ちるフレーム供給関数（取得側の失敗）。"""

    def supplier(index: int):
        if index == fail_at:
            raise RuntimeError("入力元が落ちた")
        return np.full((HEIGHT_PX, WIDTH_PX), INVALID_DEPTH_RAW + 1, dtype=np.uint16)

    return supplier


def _comparable(record) -> dict:
    """実行ごとに動く**処理時間**を落とした記録の内容。

    `elapsed_ms` は予測1回あたりの処理時間の実測値であり、同じ入力でも
    呼び出しのたびに変わる。`prediction_core.predictions_equivalent()` が
    比較から外しているのと同じ理由でここでも落とす——要件 3.7 が言う
    「同一の予測系列」は**処理時間の一致ではない**。
    ここ以外（サンプル・予測値・設定・`extra["m1"]` の全体）は
    **一切落とさずに**比較する。
    """
    data = record.to_dict()
    for prediction in data["predictions"]:
        prediction.pop("elapsed_ms", None)
    return data


class TestFailedThrowsAreRecordedWithAReason:
    """有効サンプル0件・追跡不成立の投擲を、理由付きで記録する（要件 3.8）。

    design.md「Error Categories and Responses」は観測の不成立を
    **値として扱う**と定めている。例外にすると実験そのものが止まり、
    人が物を投げ直す費用を払わされる。
    """

    def test_a_throw_with_no_detected_point_is_recorded_as_failed(
        self, settings, gateway, tmp_path, monkeypatch
    ) -> None:
        """**検出が1点も出ない入力で失敗理由付きの記録が残る**（完了状態）。"""
        result, _ = _run(settings, gateway, tmp_path, monkeypatch, points=[])

        assert result.samples_appended == 0
        assert result.record.samples == ()
        assert result.failed_reason == FailureReason.NO_VALID_SAMPLE
        assert result.record.extra["m1"]["failed_reason"] == "no_valid_sample"
        # 失敗しても記録そのものは組み立てられる（捨てるのではなく残す）。
        assert result.record.record_id == "throw-0001"
        assert result.record.schema_version == SCHEMA_VERSION

    def test_a_throw_whose_points_are_all_rejected_keeps_the_reject_counts(
        self, settings, gateway, tmp_path, monkeypatch
    ) -> None:
        """有効サンプル0件でも**なぜ0件なのか**が記録から読める。

        追跡は成立していた（点は来た）が継ぎ目が全点を除外した、という
        場合を「検出が1点も出なかった」と混同すると、原因が検出側なのか
        観測品質なのかが後から切り分けられない。
        """
        points = [
            _camera_point(t_ms=5000.0 + 33.0 * i, z_mm=2500.0, valid_depth_px=0)
            for i in range(3)
        ]
        result, _ = _run(settings, gateway, tmp_path, monkeypatch, points=points)

        assert result.failed_reason == FailureReason.NO_VALID_SAMPLE
        assert dict(result.rejected) == {"insufficient_valid_pixels": 3}
        assert result.record.extra["m1"]["rejected"] == [
            {"reason": "insufficient_valid_pixels", "count": 3}
        ]
        # 追跡自体は成立していたので、追跡の識別情報は残る。
        assert result.record.extra["m1"]["tracking"]["track_id"] == 7

    def test_a_successful_throw_carries_no_reason(
        self, settings, gateway, tmp_path, monkeypatch
    ) -> None:
        """成功した投擲に理由は付かない（失敗の印が常時点いていない）。"""
        result, _ = _run(settings, gateway, tmp_path, monkeypatch)
        assert result.failed_reason is None
        assert result.record.extra["m1"]["failed_reason"] is None


class TestUpstreamFailuresAreCapturedPerThrow:
    """上流の失敗を投擲単位で捕捉し、次の投擲へ進める。

    design.md「ThrowRunner / Implementation Notes」Risks:
    **実験中に例外で落ちると1試行を失う。**
    """

    def test_a_failure_from_the_tracking_pipeline_does_not_escape(
        self, settings, gateway, tmp_path, monkeypatch
    ) -> None:
        pipeline = ExplodingPipeline(_falling_points(), fail_at=4)
        result, _ = _run(settings, gateway, tmp_path, monkeypatch, pipeline=pipeline)

        assert result.failed_reason == UPSTREAM_FAILURE
        assert result.record.extra["m1"]["failed_reason"] == UPSTREAM_FAILURE

    def test_the_samples_collected_before_the_failure_are_kept(
        self, settings, gateway, tmp_path, monkeypatch
    ) -> None:
        """中断までに観測できた点は捨てない（原因究明の材料である）。"""
        pipeline = ExplodingPipeline(_falling_points(), fail_at=4)
        result, _ = _run(settings, gateway, tmp_path, monkeypatch, pipeline=pipeline)

        assert result.samples_appended == 3
        assert len(result.record.samples) == 3
        assert len(result.record.extra["m1"]["provenance"]) == 3

    def test_a_failure_from_the_frame_source_is_captured_too(
        self, settings, gateway, tmp_path, monkeypatch
    ) -> None:
        """取得側で落ちた場合も同じ扱いにする（追跡側だけを守らない）。"""
        result, _ = _run(
            settings,
            gateway,
            tmp_path,
            monkeypatch,
            supplier=_exploding_supplier(3),
        )
        assert result.failed_reason == UPSTREAM_FAILURE

    def test_a_seam_failure_during_the_throw_is_not_turned_into_a_record(
        self, settings, gateway, tmp_path, monkeypatch
    ) -> None:
        """継ぎ目の不成立は**投擲中でも例外のまま**出す（失敗投擲へ丸めない）。

        design.md「Error Handling / Error Strategy」:「継ぎ目の不成立は例外
        とする。座標系・形式版・設定が食い違ったまま値が下流へ流れることは、
        本 Spec が防ごうとしている事故そのものだからである」。`errors.py` も
        「ここだけは、**呼び出し側が戻り値の確認を怠っても処理が止まる形に
        する**」と定めている。

        **継ぎ目の拒否は上流の失敗ではなく本 Spec 自身の拒否**であり、要件
        3.8 が言う「追跡が成立しなかった場合」でもない。値にすると、上流が
        `handoff_version` を上げたときに一度も送出されないまま、全投擲が
        失敗理由付きの記録として静かに積み上がる（設計どおりなら最初の1投で
        止まり、その場で直せる）。
        """

        def refuse(track: object, calibration: object, *, settings: object):
            raise SeamFailure(
                FailureReason.UNKNOWN_HANDOFF_VERSION,
                "受け渡し形式版が未知である",
                {},
            )

        monkeypatch.setattr(runner_module, "build_samples", refuse)

        with pytest.raises(SeamFailure) as caught:
            _run(settings, gateway, tmp_path, monkeypatch)

        assert caught.value.reason == FailureReason.UNKNOWN_HANDOFF_VERSION

    def test_the_next_throw_still_runs_after_a_failed_one(
        self, settings, gateway, tmp_path, monkeypatch
    ) -> None:
        """**次の投擲へ進める**（1試行の失敗がセッションを終わらせない）。"""
        failed, _ = _run(
            settings,
            gateway,
            tmp_path,
            monkeypatch,
            pipeline=ExplodingPipeline(_falling_points(), fail_at=2),
            record_id="throw-0009",
        )
        ok, _ = _run(settings, gateway, tmp_path, monkeypatch, record_id="throw-0010")

        assert failed.failed_reason == UPSTREAM_FAILURE
        assert ok.failed_reason is None
        assert ok.samples_appended == 6

    def test_a_keyboard_interrupt_is_not_swallowed(
        self, settings, gateway, tmp_path, monkeypatch
    ) -> None:
        """人が実験を止める手段を奪わない（Ctrl-C は失敗投擲ではない）。"""
        pipeline = ExplodingPipeline(
            _falling_points(), fail_at=3, error=KeyboardInterrupt()
        )
        with pytest.raises(KeyboardInterrupt):
            _run(settings, gateway, tmp_path, monkeypatch, pipeline=pipeline)

    def test_a_configuration_error_is_not_recorded_as_a_failed_throw(
        self, settings, gateway, tmp_path, monkeypatch
    ) -> None:
        """設定の誤りは**起動時に拒否する**分類であり、投擲単位で飲み込まない。

        design.md「Error Categories and Responses」。飲み込むと、同じ設定
        誤りで**全投擲が静かに失敗し続ける**（失敗理由は「上流が落ちた」に
        見えるので、設定を直せば済むことに気付けない）。
        """

        def refuse(self, stage: str, event: str, data: object) -> None:
            raise M1ConfigError("段階名が予約と衝突している", {"stage": stage})

        monkeypatch.setattr(UpstreamGateway, "emit", refuse)
        with pytest.raises(M1ConfigError):
            _run(settings, gateway, tmp_path, monkeypatch)


class TestFailedThrowsAreExcludedFromAggregation:
    """失敗投擲を成功試行の集計から除外する（要件 3.8）。

    除外は**記録を捨てることではない**。理由付きで残したうえで、集計の
    入口で外す（design.md「Error Categories and Responses」: 観測の不成立は
    値として扱い、集計から除く）。
    """

    def test_only_the_successful_records_are_left_for_aggregation(
        self, settings, gateway, tmp_path, monkeypatch
    ) -> None:
        failed, _ = _run(
            settings,
            gateway,
            tmp_path,
            monkeypatch,
            points=[],
            record_id="throw-fail",
        )
        ok, _ = _run(settings, gateway, tmp_path, monkeypatch, record_id="throw-ok")

        kept = successful_throws([failed.record, ok.record])

        assert [record.record_id for record in kept] == ["throw-ok"]

    def test_the_reason_is_readable_from_a_stored_record(
        self, settings, gateway, tmp_path, monkeypatch
    ) -> None:
        """保存して読み戻した記録からも失敗と理由が読める（完了状態）。

        集計は**後から**ログ・記録を読んで行う（`tech.md` 開発標準5）ので、
        判定がメモリ上の値オブジェクトでしか成立しないなら意味が無い。
        """
        failed, _ = _run(
            settings, gateway, tmp_path, monkeypatch, points=[], record_id="throw-fail"
        )
        ok, _ = _run(settings, gateway, tmp_path, monkeypatch, record_id="throw-ok")
        path = tmp_path / "throws.ndjson"
        gateway.store_record(failed.record, path)
        gateway.store_record(ok.record, path)

        loaded = list(gateway.load_records(path))

        assert [failed_reason_of(record) for record in loaded] == [
            "no_valid_sample",
            None,
        ]
        assert [record.record_id for record in successful_throws(loaded)] == [
            "throw-ok"
        ]

    def test_a_record_without_the_m1_extra_is_not_treated_as_failed(
        self, settings, gateway, tmp_path, monkeypatch
    ) -> None:
        """本 Spec の拡張を持たない記録を失敗と決めつけない。

        `extra["m1"]` が無いのは「本 Spec が失敗と判定した事実が無い」と
        いうだけであり、失敗の証拠ではない。
        """
        result, _ = _run(settings, gateway, tmp_path, monkeypatch)
        bare = dataclasses.replace(result.record, extra={})

        assert failed_reason_of(bare) is None
        assert successful_throws([bare]) == (bare,)


class TestRerunningTheSameInputRepeatsItself:
    """同一の記録済み入力に対する再実行が同一の結果を返す（要件 3.7）。

    **同一入力の2回実行が同一内容の記録を生成する**（完了状態）。ここが
    崩れると、記録を読み直して集計をやり直しても前回と同じ結論にならず、
    「実測で判断する」という本 Spec の前提が立たない。
    """

    def test_two_runs_produce_equivalent_prediction_sequences(
        self, settings, gateway, tmp_path, monkeypatch
    ) -> None:
        first, _ = _run(settings, gateway, tmp_path, monkeypatch)
        second, _ = _run(settings, gateway, tmp_path, monkeypatch)

        assert len(first.record.predictions) == 6
        assert predictions_equivalent(
            first.record.predictions, second.record.predictions
        )

    def test_two_runs_produce_the_same_record_content(
        self, settings, gateway, tmp_path, monkeypatch
    ) -> None:
        first, _ = _run(settings, gateway, tmp_path, monkeypatch)
        second, _ = _run(settings, gateway, tmp_path, monkeypatch)

        left = _comparable(first.record)
        # 比較対象が空でないこと（何も入っていない dict 同士を比べていない）。
        assert left["extra"]["m1"]["provenance"] and left["samples"]
        assert left == _comparable(second.record)

    def test_two_runs_of_a_failed_throw_produce_the_same_record(
        self, settings, gateway, tmp_path, monkeypatch
    ) -> None:
        """失敗投擲でも再実行が一致する（失敗の記録も後から読み直せる）。"""
        first, _ = _run(settings, gateway, tmp_path, monkeypatch, points=[])
        second, _ = _run(settings, gateway, tmp_path, monkeypatch, points=[])

        assert first.failed_reason == FailureReason.NO_VALID_SAMPLE
        assert _comparable(first.record) == _comparable(second.record)

    def test_the_same_measured_values_are_returned(
        self, settings, gateway, tmp_path, monkeypatch
    ) -> None:
        """返り値側の実測値も一致する（記録だけが揃っても足りない）。"""
        first, _ = _run(settings, gateway, tmp_path, monkeypatch)
        second, _ = _run(settings, gateway, tmp_path, monkeypatch)

        assert first.samples_appended == second.samples_appended
        assert first.rejected == second.rejected
        assert first.first_valid_sample_count == second.first_valid_sample_count
        assert first.failed_reason == second.failed_reason
