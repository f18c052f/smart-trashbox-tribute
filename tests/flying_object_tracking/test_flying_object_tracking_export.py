"""点列の書き出し（`cli.py` の `serialize_camera_track()` / `run_export()`、
タスク 7.4）の検証。

design.md「エクスポート形式（開発用）」節とタスク本文（verbatim）の観測可能な
完了状態を固定する:

- 書き出したファイルを読み戻して点列と一致すること
- 座標系フィールド（`frame`）がカメラ座標系（`"camera"`）であること
- 非数・無限大はその項目を欠測にする（`null` にも文字列にもしない）こと
- World 座標のフィールドが出力形式に一切現れないこと
- 出力先が `TrackingSettings.output_root`（CLI では `--output-root`）で
  変わること
- 投擲記録（`prediction_core.ThrowRecord` / `SCHEMA_VERSION`）を読み書き
  しないこと

要件 7.1, 7.6, 12.6。

## テスト構成の設計判断

`serialize_camera_track()` は素直な純関数（`CameraTrack` → `dict`）なので、
大半のテストは `CameraTrack` を手組みして直接呼び出す（`--source recorded`
の合成セッションを介さない）。出力先の設定反映のみ、`test_flying_object_
tracking_cli.py` と同じ流儀で合成セッションを1つ使う（このファイル内に
自己完結させる。同ファイルの docstring「テスト構成の設計判断」参照）。
"""

from __future__ import annotations

import dataclasses
import inspect
import json
from pathlib import Path

import pytest
from sensing_foundation import CameraIntrinsics, CaptureStats, SessionRecorder, SourceKind, StreamProfile

import flying_object_tracking.cli as cli_module
from flying_object_tracking.cli import main, serialize_camera_track
from flying_object_tracking.types import (
    HANDOFF_VERSION,
    CameraPoint,
    CameraTrack,
    CoordinateFrame,
    TrackEndReason,
    TrackPoint,
    TrackState,
)

# ==============================================================================
# `CameraTrack` の建材（純関数テスト用。合成セッションを介さない）
# ==============================================================================


def _camera_point(
    *,
    t_ms: float,
    x_mm: float = 10.0,
    y_mm: float = 20.0,
    z_mm: float = 1000.0,
    valid_depth_px: int = 150,
    depth_spread_mm: float = 5.5,
    apparent_diameter_px: float = 12.0,
    expected_diameter_px: float = 13.0,
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


def _track_point(
    point: CameraPoint,
    *,
    frame_index: int,
    frame_seq: int,
    gap_before: int = 0,
    rivals: int = 0,
) -> TrackPoint:
    return TrackPoint(
        point=point,
        frame_index=frame_index,
        frame_seq=frame_seq,
        gap_before=gap_before,
        rivals=rivals,
    )


def _camera_track(
    points: list[TrackPoint],
    *,
    track_id: int = 7,
    started_t_ms: float = 100.0,
    state: TrackState = TrackState.ENDED,
    end_reason: TrackEndReason | None = TrackEndReason.SOURCE_END,
    source: SourceKind = SourceKind.RECORDED,
    detector_kind: str = "depth_band",
) -> CameraTrack:
    return CameraTrack(
        handoff_version=HANDOFF_VERSION,
        frame=CoordinateFrame.CAMERA,
        track_id=track_id,
        started_t_ms=started_t_ms,
        points=tuple(points),
        state=state,
        end_reason=end_reason,
        source=source,
        detector_kind=detector_kind,
    )


def _two_point_track() -> CameraTrack:
    p1 = _camera_point(t_ms=100.0, x_mm=10.0, y_mm=20.0, z_mm=1000.0, valid_depth_px=150)
    p2 = _camera_point(
        t_ms=133.0,
        x_mm=25.0,
        y_mm=21.0,
        z_mm=980.0,
        valid_depth_px=140,
        depth_spread_mm=6.1,
        apparent_diameter_px=13.0,
        expected_diameter_px=13.5,
    )
    tp1 = _track_point(p1, frame_index=3, frame_seq=3, gap_before=0, rivals=0)
    tp2 = _track_point(p2, frame_index=4, frame_seq=4, gap_before=0, rivals=1)
    return _camera_track([tp1, tp2])


# ==============================================================================
# 1. 往復忠実性（タスクの観測可能な完了状態そのもの）
# ==============================================================================


class TestRoundTripFidelity:
    def test_write_and_read_back_matches_every_documented_field(self, tmp_path: Path) -> None:
        track = _two_point_track()
        payload = serialize_camera_track(track)

        path = tmp_path / "track-roundtrip.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        read_back = json.loads(path.read_text(encoding="utf-8"))

        assert read_back["handoff_version"] == track.handoff_version
        assert read_back["frame"] == "camera"
        assert read_back["track_id"] == track.track_id
        assert read_back["started_t_ms"] == track.started_t_ms
        assert read_back["source"] == str(track.source)
        assert read_back["detector_kind"] == track.detector_kind
        assert read_back["state"] == str(track.state)
        assert read_back["end_reason"] == str(track.end_reason)

        assert len(read_back["points"]) == len(track.points)
        for expected_tp, actual in zip(track.points, read_back["points"], strict=True):
            assert actual["t_ms"] == expected_tp.point.t_ms
            assert actual["x_mm"] == expected_tp.point.x_mm
            assert actual["y_mm"] == expected_tp.point.y_mm
            assert actual["z_mm"] == expected_tp.point.z_mm
            assert actual["valid_depth_px"] == expected_tp.point.valid_depth_px
            assert actual["depth_spread_mm"] == expected_tp.point.depth_spread_mm
            assert actual["apparent_diameter_px"] == expected_tp.point.apparent_diameter_px
            assert actual["expected_diameter_px"] == expected_tp.point.expected_diameter_px
            assert actual["frame_index"] == expected_tp.frame_index
            assert actual["frame_seq"] == expected_tp.frame_seq
            assert actual["gap_before"] == expected_tp.gap_before
            assert actual["rivals"] == expected_tp.rivals

    def test_end_reason_none_survives_as_null_when_still_tracking(self, tmp_path: Path) -> None:
        track = _camera_track(
            [_track_point(_camera_point(t_ms=0.0), frame_index=0, frame_seq=0)],
            state=TrackState.TRACKING,
            end_reason=None,
        )
        payload = serialize_camera_track(track)
        text = json.dumps(payload, ensure_ascii=False, allow_nan=False)

        assert json.loads(text)["end_reason"] is None


# ==============================================================================
# 2. `frame` はカメラ座標系（タスクの named 完了状態）
# ==============================================================================


class TestFrameFieldIsAlwaysCamera:
    def test_frame_field_equals_camera(self) -> None:
        track = _two_point_track()
        payload = serialize_camera_track(track)

        assert payload["frame"] == "camera"
        assert payload["frame"] == CoordinateFrame.CAMERA


# ==============================================================================
# 3. 非数・無限大はその項目を欠測にする（null にも文字列にもしない）
# ==============================================================================


class TestNonFiniteSanitization:
    @pytest.mark.parametrize(
        "bad_value",
        [float("nan"), float("inf"), float("-inf")],
        ids=["nan", "inf", "-inf"],
    )
    def test_depth_spread_mm_nan_or_infinite_is_omitted_key(self, bad_value: float) -> None:
        point = _camera_point(t_ms=50.0, depth_spread_mm=bad_value)
        track = _camera_track([_track_point(point, frame_index=1, frame_seq=1)])

        payload = serialize_camera_track(track)

        # allow_nan=False での直列化が例外を起こさないこと（非有限値が
        # 事前に取り除かれている証拠でもある）。
        text = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        point_payload = payload["points"][0]

        assert "depth_spread_mm" not in point_payload
        assert "depth_spread_mm" not in json.loads(text)["points"][0]
        # 文字列 "NaN" / "Infinity" としても残っていないこと。
        assert "NaN" not in text
        assert "Infinity" not in text

    @pytest.mark.parametrize(
        "bad_value",
        [float("nan"), float("inf"), float("-inf")],
    )
    def test_other_fields_on_same_point_are_unaffected(self, bad_value: float) -> None:
        point = _camera_point(
            t_ms=50.0, x_mm=11.0, y_mm=22.0, z_mm=990.0, apparent_diameter_px=9.5, depth_spread_mm=bad_value
        )
        track = _camera_track([_track_point(point, frame_index=2, frame_seq=2, rivals=3)])

        payload = serialize_camera_track(track)
        point_payload = payload["points"][0]

        assert point_payload["t_ms"] == 50.0
        assert point_payload["x_mm"] == 11.0
        assert point_payload["y_mm"] == 22.0
        assert point_payload["z_mm"] == 990.0
        assert point_payload["apparent_diameter_px"] == 9.5
        assert point_payload["rivals"] == 3

    def test_multiple_non_finite_fields_on_same_point_are_all_omitted(self) -> None:
        point = _camera_point(
            t_ms=float("nan"), z_mm=float("inf"), depth_spread_mm=float("-inf")
        )
        track = _camera_track([_track_point(point, frame_index=0, frame_seq=0)])

        payload = serialize_camera_track(track)
        point_payload = payload["points"][0]

        assert "t_ms" not in point_payload
        assert "z_mm" not in point_payload
        assert "depth_spread_mm" not in point_payload
        # 汚染されていないフィールドは残る。
        assert "x_mm" in point_payload
        assert "y_mm" in point_payload

        # allow_nan=False で例外なく直列化できる。
        json.dumps(payload, ensure_ascii=False, allow_nan=False)

    def test_started_t_ms_non_finite_is_omitted_at_top_level(self) -> None:
        track = _camera_track(
            [_track_point(_camera_point(t_ms=0.0), frame_index=0, frame_seq=0)],
            started_t_ms=float("nan"),
        )
        payload = serialize_camera_track(track)

        assert "started_t_ms" not in payload
        # 他の項目は無事。
        assert payload["track_id"] == track.track_id
        json.dumps(payload, ensure_ascii=False, allow_nan=False)


# ==============================================================================
# 4. World 座標のフィールドが出力形式に一切現れない
# ==============================================================================

_DOCUMENTED_TOP_LEVEL_KEYS = {
    "handoff_version",
    "frame",
    "track_id",
    "started_t_ms",
    "source",
    "detector_kind",
    "state",
    "end_reason",
    "points",
}

_DOCUMENTED_POINT_KEYS = {
    "t_ms",
    "x_mm",
    "y_mm",
    "z_mm",
    "valid_depth_px",
    "depth_spread_mm",
    "apparent_diameter_px",
    "expected_diameter_px",
    "frame_index",
    "frame_seq",
    "gap_before",
    "rivals",
}


class TestNoWorldFrameFields:
    def test_top_level_key_set_matches_design_doc_table_exactly(self) -> None:
        payload = serialize_camera_track(_two_point_track())

        assert set(payload.keys()) == _DOCUMENTED_TOP_LEVEL_KEYS
        assert not any("world" in key.lower() for key in payload.keys())

    def test_point_key_set_matches_design_doc_table_exactly(self) -> None:
        payload = serialize_camera_track(_two_point_track())

        for point_payload in payload["points"]:
            assert set(point_payload.keys()) == _DOCUMENTED_POINT_KEYS
            assert not any("world" in key.lower() for key in point_payload.keys())

    def test_camera_track_and_camera_point_types_have_no_world_fields(self) -> None:
        """`types.py`（タスク 1.3）が持つ不変条件そのものを、この Spec の
        書き出しロジック側からも確認する（`serialize_camera_track()` が
        今後 World フィールドを足す変更に対する回帰検知）。"""
        track_fields = {f.name for f in dataclasses.fields(CameraTrack)}
        point_fields = {f.name for f in dataclasses.fields(CameraPoint)}

        assert not any("world" in name.lower() for name in track_fields)
        assert not any("world" in name.lower() for name in point_fields)


# ==============================================================================
# 5. 出力先が設定（`TrackingSettings.output_root` / `--output-root`）で変わる
# ==============================================================================

DIAMETER_MM = 65.0
BLOB_Z_MM = 1000.0
BACKGROUND_RAW = 6000
WIDTH_PX = 64
HEIGHT_PX = 64

STEP_MM = 15.0
WARMUP_LEN = 3
MOVING_LEN = 6


def _intrinsics() -> CameraIntrinsics:
    return CameraIntrinsics(
        width_px=WIDTH_PX,
        height_px=HEIGHT_PX,
        fx_px=200.0,
        fy_px=200.0,
        ppx_px=WIDTH_PX / 2.0,
        ppy_px=HEIGHT_PX / 2.0,
        model="none",
        coeffs=(0.0, 0.0, 0.0, 0.0, 0.0),
    )


def _profile() -> StreamProfile:
    return StreamProfile(
        width_px=WIDTH_PX,
        height_px=HEIGHT_PX,
        fps=30,
        depth_scale_mm=1.0,
        color_enabled=False,
        intrinsics=_intrinsics(),
    )


def _trajectory(synthetic_module) -> list:
    TrajectoryPoint = synthetic_module.TrajectoryPoint
    points = []
    t_ms = 0.0
    for _ in range(WARMUP_LEN):
        points.append(TrajectoryPoint(t_ms=t_ms, x_mm=0.0, y_mm=0.0, z_mm=float(BACKGROUND_RAW)))
        t_ms += 33.0
    for i in range(MOVING_LEN):
        points.append(TrajectoryPoint(t_ms=t_ms, x_mm=float(i) * STEP_MM, y_mm=0.0, z_mm=BLOB_Z_MM))
        t_ms += 33.0
    return points


def _synthesize(synthetic_module) -> list:
    trajectory = _trajectory(synthetic_module)
    return synthetic_module.synthesize_frames(
        trajectory, _profile(), DIAMETER_MM, background_raw=BACKGROUND_RAW
    )


def _record_session(root: Path, frames: list, session_id: str) -> Path:
    recorder = SessionRecorder(
        root, session_id, _profile(), device=None, runtime={}, source=SourceKind.SIMULATED
    )
    with recorder:
        for frame in frames:
            recorder.write(frame)
        recorder.close(
            CaptureStats(
                frames_yielded=len(frames),
                frames_dropped=0,
                frames_missing=0,
                duration_ms=0.0,
                measured_fps=0.0,
                acquire_errors=0,
            )
        )
    return root / session_id


@pytest.fixture
def synthetic_session(tmp_path: Path, synthetic_module) -> Path:
    frames = _synthesize(synthetic_module)
    return _record_session(tmp_path / "sessions", frames, "export-output-root-session")


def _tracking_override_args() -> list[str]:
    return [
        "--roi-z-min-mm",
        "500",
        "--roi-z-max-mm",
        "5000",
        "--object-diameter-mm",
        str(DIAMETER_MM),
        "--detector-kind",
        "depth_band",
        "--detector-diff-threshold-mm",
        "80",
        "--detector-background-frames",
        str(WARMUP_LEN),
        "--detector-background-update-rate",
        "0",
        "--detector-open-kernel-px",
        "1",
        "--detector-close-kernel-px",
        "1",
        "--tracker-max-step-mm",
        "900",
        "--tracker-max-missing-frames",
        "3",
        "--tracker-min-points-to-start",
        "1",
    ]


class TestOutputRootIsConfigurable:
    def test_changing_output_root_flag_changes_where_file_is_written(
        self, tmp_path: Path, synthetic_session: Path
    ) -> None:
        root_a = tmp_path / "var" / "tracking-a"
        root_b = tmp_path / "var" / "tracking-b"

        argv_common = [
            "export",
            "--source",
            "recorded",
            "--session",
            str(synthetic_session),
            *_tracking_override_args(),
        ]

        exit_code_a = main([*argv_common, "--output-root", str(root_a)])
        exit_code_b = main([*argv_common, "--output-root", str(root_b)])

        assert exit_code_a == 0
        assert exit_code_b == 0

        written_a = list(root_a.glob("track-*.json"))
        written_b = list(root_b.glob("track-*.json"))
        assert len(written_a) == 1
        assert len(written_b) == 1
        # `--output-root` の値そのものが書き出し先ディレクトリに反映される。
        assert written_a[0].parent == root_a
        assert written_b[0].parent == root_b
        assert written_a[0].name.startswith(f"track-{synthetic_session.name}-")

        payload_a = json.loads(written_a[0].read_text(encoding="utf-8"))
        payload_b = json.loads(written_b[0].read_text(encoding="utf-8"))
        assert payload_a["frame"] == "camera"
        assert payload_b["frame"] == "camera"
        # `t_ms` は実行毎の `SessionClock` 実時刻に依存するため厳密一致は
        # 求めない。同じ合成セッションを辿った以上、点数・トラック ID・
        # 追跡開始時刻ゼロ点は同一である。
        assert payload_a["track_id"] == payload_b["track_id"]
        assert len(payload_a["points"]) == len(payload_b["points"])

    def test_run_export_uses_tracking_settings_output_root_directly(self) -> None:
        """`run_export()` が独自の出力先ロジックを持たず、
        `tracking_settings.output_root` をそのまま使っていることをソースで
        確認する（回帰防止。ハードコードされたパスへ戻す変更を検知する）。"""
        source = inspect.getsource(cli_module.run_export)
        assert "tracking_settings.output_root" in source


# ==============================================================================
# 6. 投擲記録スキーマ（`prediction_core`）を読み書きしない
# ==============================================================================


class TestDoesNotTouchThrowRecordSchema:
    def test_cli_module_does_not_import_prediction_core(self) -> None:
        """`import` 文（コメント・docstring中の言及は対象外）だけを AST で
        走査し、`prediction_core` が実際に import されていないことを確認する。
        `import prediction_core` 自体は上流 `sensing_foundation.obslog` との
        方針一致をドキュメントで言及するために docstring 中に文字列として
        現れる（`import` 文ではない）ため、単純な部分文字列一致は使わない。
        """
        import ast

        tree = ast.parse(inspect.getsource(cli_module))
        imported_module_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_module_names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_module_names.add(node.module)

        assert not any(
            name == "prediction_core" or name.startswith("prediction_core.")
            for name in imported_module_names
        )

    def test_cli_module_does_not_bind_throw_record_or_schema_version_names(self) -> None:
        """モジュール名前空間に `ThrowRecord` / `SCHEMA_VERSION` という束縛が
        存在しないことを確認する（import されていれば必ずここに現れる）。
        docstring 中の言及（投擲記録スキーマを読み書きしない旨の説明文）は
        束縛を作らないため、この検査には影響しない。
        """
        assert "ThrowRecord" not in vars(cli_module)
        assert "SCHEMA_VERSION" not in vars(cli_module)
