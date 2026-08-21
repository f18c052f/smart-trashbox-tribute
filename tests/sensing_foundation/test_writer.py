"""`sensing_foundation.recording.writer.SessionRecorder` に対するテスト（タスク 4.3）。

観測可能な完了状態（tasks.md 4.3）を固定する:
- 記録を実行するとセッションディレクトリに4ファイルが生成される
- 索引行数とブロブ内のフレーム数が一致する
- 書き込み先を書き込み不可にしても取得が止まらず失敗件数が増える

あわせて design.md「SessionRecorder」が定める以下の点も固定する:
- 索引行はブロブ書き込みの後に書く（Invariant。索引が実体を超えて指さない）
- 圧縮時は `len`（圧縮後）と `raw_len`（展開後）の両方を索引に残す
- 書き込み失敗は例外として `write()` から一切伝播しない。連続失敗が上限に
  達した場合のみ記録が内部的に停止する（取得は継続する——本テストの範囲では
  「以後の write() が静かに no-op になる」ことで確認する）
- `close()` で `summary.json` が書かれ、以後の `write()` は no-op になる
- `with` ブロックが `close()` 未呼び出しのまま抜けても安全網が `close()` する
- 記録経路（`write()`）に測定点（`Logger.timed`）が置かれている
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pytest
from synthetic import make_depth_frame

from sensing_foundation.errors import SensingConfigError
from sensing_foundation.recording import layout
from sensing_foundation.recording.writer import RecordingStats, SessionRecorder
from sensing_foundation.types import (
    CameraIntrinsics,
    CaptureFrame,
    CaptureStats,
    SourceKind,
    StreamProfile,
    TimestampDomain,
)

WIDTH_PX = 8
HEIGHT_PX = 6


def _make_profile(*, width_px: int = WIDTH_PX, height_px: int = HEIGHT_PX, fps: int = 30) -> StreamProfile:
    intrinsics = CameraIntrinsics(
        width_px=width_px,
        height_px=height_px,
        fx_px=50.0,
        fy_px=50.0,
        ppx_px=width_px / 2.0,
        ppy_px=height_px / 2.0,
        model="brown_conrady",
        coeffs=(0.0, 0.0, 0.0, 0.0, 0.0),
    )
    return StreamProfile(
        width_px=width_px,
        height_px=height_px,
        fps=fps,
        depth_scale_mm=1.0,
        color_enabled=False,
        intrinsics=intrinsics,
    )


def _make_frame(
    profile: StreamProfile,
    *,
    index: int,
    seq: int,
    dropped_before: int = 0,
    gap_before: int = 0,
    device_timestamp_ms: float | None = None,
    timestamp_domain: TimestampDomain = TimestampDomain.UNKNOWN,
    capture_latency_ms: float | None = None,
    depth: np.ndarray | None = None,
) -> CaptureFrame:
    if depth is None:
        depth = make_depth_frame(profile.width_px, profile.height_px, seq)
    return CaptureFrame(
        index=index,
        seq=seq,
        t_capture_ms=float(index) * (1000.0 / profile.fps),
        device_timestamp_ms=device_timestamp_ms,
        timestamp_domain=timestamp_domain,
        capture_latency_ms=capture_latency_ms,
        depth=depth,
        profile=profile,
        source=SourceKind.SIMULATED,
        dropped_before=dropped_before,
        gap_before=gap_before,
    )


def _capture_stats(**overrides: object) -> CaptureStats:
    defaults: dict[str, object] = {
        "frames_yielded": 3,
        "frames_dropped": 0,
        "frames_missing": 0,
        "duration_ms": 100.0,
        "measured_fps": 30.0,
        "acquire_errors": 0,
    }
    defaults.update(overrides)
    return CaptureStats(**defaults)  # type: ignore[arg-type]


class RecordingLogger:
    """`Logger` プロトコルの偽実装。`emit()`/`timed()` の呼び出しを記録する。

    `timed()` は本物の `StructuredLogger.timed()` と同様、区間の所要時間を
    測って `duration_ms` を添えて `emit()` する（テストが「測定点が実際に
    使われたこと」を `duration_ms` の存在で確認できるようにするため）。
    """

    def __init__(self) -> None:
        self.enabled = True
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def emit(self, stage: str, event: str, /, **data: object) -> None:
        self.calls.append((stage, event, dict(data)))

    def stage(self, stage: str):  # pragma: no cover - 本テストでは未使用
        raise NotImplementedError

    @contextmanager
    def timed(self, stage: str, event: str, /, **data: object):
        start = time.perf_counter()
        try:
            yield
        finally:
            payload = dict(data)
            payload.setdefault("duration_ms", (time.perf_counter() - start) * 1000.0)
            self.emit(stage, event, **payload)

    def stats(self):  # pragma: no cover - 本テストでは未使用
        raise NotImplementedError

    def close(self, timeout_ms: float = 1000.0) -> None:  # pragma: no cover
        return None


def _make_recorder(
    tmp_path: Path,
    *,
    profile: StreamProfile | None = None,
    compression: str = "none",
    logger: RecordingLogger | None = None,
    failure_cap: int = 100,
    session_id: str = "20260101T000000Z-aaaaaaaa",
) -> SessionRecorder:
    return SessionRecorder(
        root=tmp_path,
        session_id=session_id,
        profile=profile or _make_profile(),
        device={"serial": "abc123", "firmware": "5.13.0", "usb_type": "3.2", "product_line": "D400"},
        runtime={
            "os": "linux",
            "kernel": "6.6.0",
            "python_version": "3.11.9",
            "sdk_version": None,
            "hostname": "test-host",
            "global_time_enabled": False,
        },
        compression=compression,
        logger=logger if logger is not None else RecordingLogger(),
        source=SourceKind.SIMULATED,
        capture={"queue_capacity": 1, "drain_enabled": True, "acquire_timeout_ms": 5000},
        failure_cap=failure_cap,
    )


class TestFullWriteCycle:
    def test_four_files_are_created(self, tmp_path: Path) -> None:
        profile = _make_profile()
        recorder = _make_recorder(tmp_path, profile=profile)
        for i in range(5):
            recorder.write(_make_frame(profile, index=i, seq=i))
        recorder.close(_capture_stats(frames_yielded=5))

        session_dir = layout.session_dir(tmp_path, "20260101T000000Z-aaaaaaaa")
        assert (session_dir / layout.MANIFEST_NAME).exists()
        assert (session_dir / layout.INDEX_NAME).exists()
        assert (session_dir / layout.BLOB_NAME).exists()
        assert (session_dir / layout.SUMMARY_NAME).exists()

    def test_index_line_count_matches_written_frames(self, tmp_path: Path) -> None:
        profile = _make_profile()
        recorder = _make_recorder(tmp_path, profile=profile)
        for i in range(7):
            recorder.write(_make_frame(profile, index=i, seq=i))
        stats = recorder.close(_capture_stats(frames_yielded=7))

        session_dir = layout.session_dir(tmp_path, "20260101T000000Z-aaaaaaaa")
        lines = (session_dir / layout.INDEX_NAME).read_text(encoding="utf-8").splitlines()
        assert len(lines) == 7
        assert stats.frames_written == 7

    def test_blob_bytes_match_sum_of_index_lengths(self, tmp_path: Path) -> None:
        profile = _make_profile()
        recorder = _make_recorder(tmp_path, profile=profile)
        for i in range(4):
            recorder.write(_make_frame(profile, index=i, seq=i))
        recorder.close(_capture_stats(frames_yielded=4))

        session_dir = layout.session_dir(tmp_path, "20260101T000000Z-aaaaaaaa")
        lines = (session_dir / layout.INDEX_NAME).read_text(encoding="utf-8").splitlines()
        total_len = sum(json.loads(line)["len"] for line in lines)
        blob_size = (session_dir / layout.BLOB_NAME).stat().st_size
        assert blob_size == total_len

    def test_manifest_is_readable_via_layout(self, tmp_path: Path) -> None:
        profile = _make_profile()
        recorder = _make_recorder(tmp_path, profile=profile)
        recorder.write(_make_frame(profile, index=0, seq=0))
        recorder.close(_capture_stats(frames_yielded=1))

        session_dir = layout.session_dir(tmp_path, "20260101T000000Z-aaaaaaaa")
        manifest = layout.read_manifest(session_dir)
        assert manifest["format_version"] == layout.RECORDING_FORMAT_VERSION
        assert manifest["session_id"] == "20260101T000000Z-aaaaaaaa"
        assert manifest["source"] == "simulated"
        assert manifest["profile"]["width_px"] == WIDTH_PX
        assert manifest["profile"]["height_px"] == HEIGHT_PX
        assert manifest["intrinsics"]["model"] == "brown_conrady"
        assert manifest["device"]["serial"] == "abc123"
        assert manifest["runtime"]["hostname"] == "test-host"
        assert manifest["blob"]["file"] == layout.BLOB_NAME
        assert manifest["blob"]["compression"] == "none"

    def test_summary_matches_capture_stats_and_recorder_counters(self, tmp_path: Path) -> None:
        profile = _make_profile()
        recorder = _make_recorder(tmp_path, profile=profile)
        for i in range(3):
            recorder.write(_make_frame(profile, index=i, seq=i))
        stats_in = _capture_stats(
            frames_yielded=3, frames_dropped=2, frames_missing=1, duration_ms=250.0, measured_fps=12.0
        )
        result = recorder.close(stats_in)

        assert isinstance(result, RecordingStats)
        assert result.frames_written == 3
        assert result.frames_dropped == 2
        assert result.frames_missing == 1
        assert result.duration_ms == 250.0
        assert result.measured_fps == 12.0
        assert result.write_errors == 0
        assert result.bytes_written > 0

        session_dir = layout.session_dir(tmp_path, "20260101T000000Z-aaaaaaaa")
        summary = json.loads((session_dir / layout.SUMMARY_NAME).read_text(encoding="utf-8"))
        assert summary["frames_written"] == 3
        assert summary["frames_dropped"] == 2
        assert summary["frames_missing"] == 1
        assert summary["duration_ms"] == 250.0
        assert summary["measured_fps"] == 12.0
        assert summary["write_errors"] == 0
        assert summary["bytes_written"] == result.bytes_written
        assert "closed_wall_ms" in summary


class TestIndexFieldContent:
    def test_all_schema_keys_present_with_correct_values(self, tmp_path: Path) -> None:
        profile = _make_profile()
        recorder = _make_recorder(tmp_path, profile=profile)
        frame = _make_frame(
            profile,
            index=0,
            seq=42,
            dropped_before=3,
            gap_before=2,
            device_timestamp_ms=123.5,
            timestamp_domain=TimestampDomain.GLOBAL_TIME,
            capture_latency_ms=7.5,
        )
        recorder.write(frame)
        recorder.close(_capture_stats(frames_yielded=1))

        session_dir = layout.session_dir(tmp_path, "20260101T000000Z-aaaaaaaa")
        line = (session_dir / layout.INDEX_NAME).read_text(encoding="utf-8").splitlines()[0]
        record = json.loads(line)

        assert record["i"] == 0
        assert record["seq"] == 42
        assert record["t_capture_ms"] == frame.t_capture_ms
        assert record["device_ts_ms"] == 123.5
        assert record["ts_domain"] == "global_time"
        assert record["capture_latency_ms"] == 7.5
        assert record["off"] == 0
        assert record["len"] == record["raw_len"]  # 無圧縮
        assert record["raw_len"] == profile.width_px * profile.height_px * 2
        assert record["dropped_before"] == 3
        assert record["gap_before"] == 2

    def test_null_fields_serialize_as_null(self, tmp_path: Path) -> None:
        profile = _make_profile()
        recorder = _make_recorder(tmp_path, profile=profile)
        recorder.write(_make_frame(profile, index=0, seq=0))
        recorder.close(_capture_stats(frames_yielded=1))

        session_dir = layout.session_dir(tmp_path, "20260101T000000Z-aaaaaaaa")
        line = (session_dir / layout.INDEX_NAME).read_text(encoding="utf-8").splitlines()[0]
        record = json.loads(line)
        assert record["device_ts_ms"] is None
        assert record["capture_latency_ms"] is None
        assert record["ts_domain"] == "unknown"


class TestCompression:
    def test_zlib_compresses_and_keeps_raw_len(self, tmp_path: Path) -> None:
        profile = _make_profile(width_px=64, height_px=64)
        recorder = _make_recorder(tmp_path, profile=profile, compression="zlib")
        depth = np.full((profile.height_px, profile.width_px), 500, dtype=np.uint16)
        recorder.write(_make_frame(profile, index=0, seq=0, depth=depth))
        recorder.close(_capture_stats(frames_yielded=1))

        session_dir = layout.session_dir(tmp_path, "20260101T000000Z-aaaaaaaa")
        line = (session_dir / layout.INDEX_NAME).read_text(encoding="utf-8").splitlines()[0]
        record = json.loads(line)
        assert record["raw_len"] == profile.width_px * profile.height_px * 2
        assert record["len"] < record["raw_len"]

        manifest = layout.read_manifest(session_dir)
        assert manifest["blob"]["compression"] == "zlib"

    def test_none_compression_len_equals_raw_len(self, tmp_path: Path) -> None:
        profile = _make_profile()
        recorder = _make_recorder(tmp_path, profile=profile, compression="none")
        recorder.write(_make_frame(profile, index=0, seq=0))
        recorder.close(_capture_stats(frames_yielded=1))

        session_dir = layout.session_dir(tmp_path, "20260101T000000Z-aaaaaaaa")
        line = (session_dir / layout.INDEX_NAME).read_text(encoding="utf-8").splitlines()[0]
        record = json.loads(line)
        assert record["len"] == record["raw_len"]

    def test_invalid_compression_is_rejected_at_construction(self, tmp_path: Path) -> None:
        with pytest.raises(SensingConfigError):
            _make_recorder(tmp_path, compression="gzip")


class TestIndexAfterBlobOrdering:
    def test_index_never_references_past_blob_end(self, tmp_path: Path) -> None:
        profile = _make_profile()
        recorder = _make_recorder(tmp_path, profile=profile)

        # 3枚目の索引書き込みだけを失敗させ、ブロブ書き込みは成功させる
        # （「ブロブは書けたが索引が書けなかった」状況を模す）。
        original_append_index_line = recorder._append_index_line
        call_count = {"n": 0}

        def flaky_append_index_line(line: str) -> None:
            call_count["n"] += 1
            if call_count["n"] == 3:
                raise OSError("simulated crash before index write")
            original_append_index_line(line)

        recorder._append_index_line = flaky_append_index_line  # type: ignore[method-assign]

        for i in range(5):
            recorder.write(_make_frame(profile, index=i, seq=i))
        recorder.close(_capture_stats(frames_yielded=5))

        session_dir = layout.session_dir(tmp_path, "20260101T000000Z-aaaaaaaa")
        blob_size = (session_dir / layout.BLOB_NAME).stat().st_size
        lines = (session_dir / layout.INDEX_NAME).read_text(encoding="utf-8").splitlines()

        # 索引は4行（5回目の書き込み試行のうち1回だけ索引失敗）だが、
        # ブロブには5枚分のバイト列が物理的に存在する（末尾が索引から
        # 参照されない孤児バイト列になる）。索引の欠落はあっても、
        # 索引がブロブの実体を超えて指すことは絶対に無い、という
        # Invariant を固定する。
        assert len(lines) == 4
        for line in lines:
            record = json.loads(line)
            assert record["off"] + record["len"] <= blob_size


class TestWriteFailureIsolation:
    def test_write_never_raises_and_counts_failures(self, tmp_path: Path) -> None:
        profile = _make_profile()
        recorder = _make_recorder(tmp_path, profile=profile, failure_cap=5)

        def always_fail(payload: bytes) -> int:
            raise OSError("disk full")

        recorder._append_blob_chunk = always_fail  # type: ignore[method-assign]

        for i in range(10):
            recorder.write(_make_frame(profile, index=i, seq=i))  # must never raise

        stats = recorder.close(_capture_stats(frames_yielded=0))
        assert stats.write_errors == 5  # 上限に達した時点で以後は試行自体をやめる
        assert stats.frames_written == 0
        assert stats.bytes_written == 0

    def test_recording_stops_attempting_after_cap_but_capture_continues(self, tmp_path: Path) -> None:
        profile = _make_profile()
        logger = RecordingLogger()
        recorder = _make_recorder(tmp_path, profile=profile, failure_cap=3, logger=logger)

        def always_fail(payload: bytes) -> int:
            raise OSError("disk full")

        recorder._append_blob_chunk = always_fail  # type: ignore[method-assign]

        for i in range(3):
            recorder.write(_make_frame(profile, index=i, seq=i))
        # ここで内部的に記録は停止しているはず。以後 write() を何度呼んでも
        # 例外は出ず、かつ write_errors はもう増えない（試行自体をやめる）。
        for i in range(3, 20):
            recorder.write(_make_frame(profile, index=i, seq=i))

        stats = recorder.close(_capture_stats(frames_yielded=0))
        assert stats.write_errors == 3

        stopped_events = [c for c in logger.calls if c[0] == "record" and c[1] == "stopped"]
        assert len(stopped_events) == 1

    def test_consecutive_failures_reset_after_a_success(self, tmp_path: Path) -> None:
        profile = _make_profile()
        recorder = _make_recorder(tmp_path, profile=profile, failure_cap=3)

        state = {"fail_next_two": True, "count": 0}
        original = recorder._append_blob_chunk

        def flaky(payload: bytes) -> int:
            if state["fail_next_two"] and state["count"] < 2:
                state["count"] += 1
                raise OSError("transient")
            state["fail_next_two"] = False
            return original(payload)

        recorder._append_blob_chunk = flaky  # type: ignore[method-assign]

        # 2回失敗 -> 1回成功（連続失敗カウンタがリセットされる）
        recorder.write(_make_frame(profile, index=0, seq=0))
        recorder.write(_make_frame(profile, index=1, seq=1))
        recorder.write(_make_frame(profile, index=2, seq=2))

        stats = recorder.close(_capture_stats(frames_yielded=1))
        # 上限(3)に一度も達していない（2失敗 -> リセット -> 成功）ため、
        # 記録は停止していない。
        assert stats.write_errors == 2
        assert stats.frames_written == 1


class TestCloseIsNoOpAfterward:
    def test_write_after_close_is_ignored(self, tmp_path: Path) -> None:
        profile = _make_profile()
        recorder = _make_recorder(tmp_path, profile=profile)
        recorder.write(_make_frame(profile, index=0, seq=0))
        recorder.close(_capture_stats(frames_yielded=1))

        # close() 後の write() は例外を出さず、何も変化させない。
        recorder.write(_make_frame(profile, index=1, seq=1))

        session_dir = layout.session_dir(tmp_path, "20260101T000000Z-aaaaaaaa")
        lines = (session_dir / layout.INDEX_NAME).read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1

    def test_close_is_idempotent(self, tmp_path: Path) -> None:
        profile = _make_profile()
        recorder = _make_recorder(tmp_path, profile=profile)
        recorder.write(_make_frame(profile, index=0, seq=0))
        first = recorder.close(_capture_stats(frames_yielded=1))
        second = recorder.close(_capture_stats(frames_yielded=999))  # 無視されるはず
        assert first == second


class TestContextManager:
    def test_enter_returns_self(self, tmp_path: Path) -> None:
        profile = _make_profile()
        recorder = _make_recorder(tmp_path, profile=profile)
        with recorder as ctx:
            assert ctx is recorder
        recorder2 = _make_recorder(tmp_path, profile=profile, session_id="20260101T000000Z-bbbbbbbb")
        # 単独で __enter__ を呼んでも壊れないことも確認する。
        assert recorder2.__enter__() is recorder2
        recorder2.close(_capture_stats(frames_yielded=0))

    def test_exit_closes_when_close_was_never_called(self, tmp_path: Path) -> None:
        profile = _make_profile()
        with _make_recorder(tmp_path, profile=profile, session_id="20260101T000000Z-cccccccc") as recorder:
            recorder.write(_make_frame(profile, index=0, seq=0))
            recorder.write(_make_frame(profile, index=1, seq=1))
            # 明示的に close() を呼ばずに with を抜ける。

        session_dir = layout.session_dir(tmp_path, "20260101T000000Z-cccccccc")
        assert (session_dir / layout.SUMMARY_NAME).exists()
        summary = json.loads((session_dir / layout.SUMMARY_NAME).read_text(encoding="utf-8"))
        assert summary["frames_written"] == 2

    def test_exit_does_not_double_write_after_explicit_close(self, tmp_path: Path) -> None:
        profile = _make_profile()
        session_id = "20260101T000000Z-dddddddd"
        with _make_recorder(tmp_path, profile=profile, session_id=session_id) as recorder:
            recorder.write(_make_frame(profile, index=0, seq=0))
            result = recorder.close(_capture_stats(frames_yielded=1, duration_ms=42.0))

        session_dir = layout.session_dir(tmp_path, session_id)
        summary = json.loads((session_dir / layout.SUMMARY_NAME).read_text(encoding="utf-8"))
        # __exit__ の安全網が上書きしていれば duration_ms が変わってしまう。
        assert summary["duration_ms"] == 42.0
        assert result.duration_ms == 42.0


class TestMeasurementPointsOnRecordingPath:
    def test_write_emits_a_timed_measurement_event(self, tmp_path: Path) -> None:
        profile = _make_profile()
        logger = RecordingLogger()
        recorder = _make_recorder(tmp_path, profile=profile, logger=logger)
        recorder.write(_make_frame(profile, index=0, seq=0))
        recorder.close(_capture_stats(frames_yielded=1))

        write_events = [c for c in logger.calls if c[0] == "record" and c[1] == "write"]
        assert len(write_events) == 1
        assert "duration_ms" in write_events[0][2]

    def test_write_failure_still_emits_a_measurement_event(self, tmp_path: Path) -> None:
        profile = _make_profile()
        logger = RecordingLogger()
        recorder = _make_recorder(tmp_path, profile=profile, logger=logger)

        def always_fail(payload: bytes) -> int:
            raise OSError("disk full")

        recorder._append_blob_chunk = always_fail  # type: ignore[method-assign]
        recorder.write(_make_frame(profile, index=0, seq=0))
        recorder.close(_capture_stats(frames_yielded=0))

        write_events = [c for c in logger.calls if c[0] == "record" and c[1] == "write"]
        error_events = [c for c in logger.calls if c[0] == "record" and c[1] == "write_error"]
        assert len(write_events) == 1  # timed() の測定点は失敗時も送られる
        assert len(error_events) == 1


class TestPreconditionRejection:
    def test_session_dir_creation_rejects_existing_directory(self, tmp_path: Path) -> None:
        profile = _make_profile()
        session_id = "20260101T000000Z-eeeeeeee"
        recorder = _make_recorder(tmp_path, profile=profile, session_id=session_id)
        recorder.close(_capture_stats(frames_yielded=0))

        with pytest.raises(SensingConfigError):
            _make_recorder(tmp_path, profile=profile, session_id=session_id)
