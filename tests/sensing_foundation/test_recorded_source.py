"""`sensing_foundation.sources.recorded` に対するテスト（タスク 4.5）。

観測可能な完了状態（tasks.md 4.5）を固定する:

- SDK 非導入環境で記録を再生でき、実時間再生と最速再生の返す系列が
  完全一致する

あわせて design.md「RecordedSource」・要件 4.1, 4.4, 6.1, 6.3, 6.4, 6.5, 6.7
が定める以下の点も固定する:

- `kind` は `SourceKind.RECORDED`
- `profile`/intrinsics は live と同じ `FrameSource.profile` 経路で
  `SessionReader.profile` から取得できる
- 既定（`speed="fast"`）の再生は待たず、`SessionReader.iter_frames()` が
  直接返す内容（`seq`/`depth`）と一致する
- `speed="realtime"` は記録時の `t_capture_ms` 差分だけ待ち、`speed="fast"`
  より実測で長くかかる。かつ両モードで返る系列の内容
  （`seq`/`depth`/`device_timestamp_ms`/`timestamp_domain`/
  `capture_latency_ms`/`dropped_before`/`gap_before`）は完全一致する
  （`t_capture_ms` は両モードとも再生セッションのクロック起点で新たに
  算出されるため比較対象から除外する。`recorded.py` モジュール docstring
  「`t_capture_ms` の扱い」参照）
- ドレインを行わない（`drain_enabled` を呼び出し側の指定に関わらず常に
  偽へ固定し、`_drain_latest()` 自体も常に `(None, 0)` を返す。二重防御）
- RealSense の SDK もハードウェアも参照しない
- 記録済みフレーム数を超えて読み出そうとすると `frames()` が例外なく終わる

**実時間ペーシングの検証方針**（実装判断）: 実際に長く眠るテストは避け、
2通りの手段を併用する:

1. `sleep` を差し替え可能な引数として注入し（`RecordedSource.__init__` の
   キーワード専用 `sleep`）、呼ばれた待機秒数そのものを記録するフェイクで
   「記録時の `t_capture_ms` 差分どおりに待とうとしたか」を実時間ゼロで
   検証する（`TestRealtimePacingUsesRecordedDeltas`）。
2. 「最速再生より実測で長くかかる」という観測可能な完了状態自体は、
   `time.sleep` を実際に使いつつも `t_capture_ms` の間隔をミリ秒オーダー
   （数十 ms）に抑えたフィクスチャで検証する（テスト全体を高速に保つ。
   `TestRealtimeIsMeasurablyLongerThanFast`）。

要件: 4.1, 4.4, 6.1, 6.3, 6.4, 6.5, 6.7
"""

from __future__ import annotations

import inspect
import time
from pathlib import Path

import numpy as np
import pytest
from synthetic import make_depth_frame

from sensing_foundation.config import CaptureConfig
from sensing_foundation.metrics import CaptureMetrics
from sensing_foundation.recording import layout
from sensing_foundation.recording.reader import SessionReader
from sensing_foundation.recording.writer import SessionRecorder
from sensing_foundation.sources import recorded as recorded_module
from sensing_foundation.sources.recorded import RecordedSource
from sensing_foundation.timebase import SessionClock
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


class FakeLogger:
    """`Logger` プロトコルに構造的に適合する、`emit()` 呼び出しを記録するだけの偽実装。"""

    def __init__(self) -> None:
        self.enabled = True
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def emit(self, stage: str, event: str, /, **data: object) -> None:
        self.calls.append((stage, event, dict(data)))

    def stage(self, stage: str):  # pragma: no cover - 本テストでは未使用
        raise NotImplementedError

    def timed(self, stage: str, event: str, /, **data: object):  # pragma: no cover
        raise NotImplementedError

    def stats(self):  # pragma: no cover - 本テストでは未使用
        raise NotImplementedError

    def close(self, timeout_ms: float = 1000.0) -> None:  # pragma: no cover
        return None


def _make_profile() -> StreamProfile:
    intrinsics = CameraIntrinsics(
        width_px=WIDTH_PX,
        height_px=HEIGHT_PX,
        fx_px=50.0,
        fy_px=50.0,
        ppx_px=4.0,
        ppy_px=3.0,
        model="brown_conrady",
        coeffs=(0.0, 0.0, 0.0, 0.0, 0.0),
    )
    return StreamProfile(
        width_px=WIDTH_PX,
        height_px=HEIGHT_PX,
        fps=30,
        depth_scale_mm=1.0,
        color_enabled=False,
        intrinsics=intrinsics,
    )


def _make_metrics_and_clock(session_id: str = "test-recorded-source") -> tuple[CaptureMetrics, SessionClock]:
    clock = SessionClock(session_id=session_id)
    metrics = CaptureMetrics(FakeLogger(), clock, sysstat=None)
    return metrics, clock


def _write_session(
    tmp_path: Path,
    *,
    seqs: list[int],
    t_capture_ms_values: list[float],
    profile: StreamProfile | None = None,
) -> Path:
    """`SessionRecorder` を使い、指定した `t_capture_ms` を持つ最小セッションを書く。

    `SessionRecorder.write()` は渡された `CaptureFrame.t_capture_ms` を
    そのまま索引へ書く（`writer.py` 参照）ため、ここで意図した間隔
    （`t_capture_ms_values` の差分）を持つフィクスチャを組み立てられる。
    """
    assert len(seqs) == len(t_capture_ms_values)
    profile = profile if profile is not None else _make_profile()
    root = tmp_path / "recordings"
    session_id = layout.new_session_id(time.time() * 1000.0)

    recorder = SessionRecorder(
        root,
        session_id,
        profile,
        device=None,
        runtime={},
        source=SourceKind.SIMULATED,
    )
    with recorder:
        for i, (seq, t_ms) in enumerate(zip(seqs, t_capture_ms_values)):
            depth = make_depth_frame(WIDTH_PX, HEIGHT_PX, seq)
            frame = CaptureFrame(
                index=i,
                seq=seq,
                t_capture_ms=t_ms,
                device_timestamp_ms=float(seq) * 10.0,
                timestamp_domain=TimestampDomain.HARDWARE_CLOCK,
                capture_latency_ms=None,
                depth=depth,
                profile=profile,
                source=SourceKind.SIMULATED,
                dropped_before=0,
                gap_before=0,
            )
            recorder.write(frame)
        recorder.close(
            CaptureStats(
                frames_yielded=len(seqs),
                frames_dropped=0,
                frames_missing=0,
                duration_ms=0.0,
                measured_fps=0.0,
                acquire_errors=0,
            )
        )

    return layout.session_dir(root, session_id)


def _make_source(
    session_dir: Path,
    *,
    speed: str = "fast",
    sleep=None,
    capture_config: CaptureConfig | None = None,
) -> RecordedSource:
    reader = SessionReader(session_dir)
    metrics, clock = _make_metrics_and_clock()
    kwargs: dict[str, object] = {
        "clock": clock,
        "capture_config": capture_config,
        "speed": speed,
    }
    if sleep is not None:
        kwargs["sleep"] = sleep
    return RecordedSource(reader, metrics, **kwargs)


def _assert_frames_equal_ignoring_timing(a: CaptureFrame, b: CaptureFrame) -> None:
    """`t_capture_ms` を除く全フィールドの等価性を比較する。

    `depth` は複数画素を持つと素の `==` が `ValueError` を送出する
    （`tests/sensing_foundation/test_reader.py` の `_assert_frames_equal()`
    と同じ地雷。`np.array_equal` を使う）。`t_capture_ms` を比較対象から
    除く理由は `recorded.py` モジュール docstring「`t_capture_ms` の扱い」
    を参照。
    """
    assert a.index == b.index
    assert a.seq == b.seq
    assert a.device_timestamp_ms == b.device_timestamp_ms
    assert a.timestamp_domain == b.timestamp_domain
    assert a.capture_latency_ms == b.capture_latency_ms
    assert np.array_equal(a.depth, b.depth)
    assert a.profile == b.profile
    assert a.source == b.source
    assert a.dropped_before == b.dropped_before
    assert a.gap_before == b.gap_before


class TestKindIsRecorded:
    def test_kind_property_returns_recorded(self, tmp_path: Path) -> None:
        session_dir = _write_session(tmp_path, seqs=[0], t_capture_ms_values=[0.0])
        source = _make_source(session_dir)

        assert source.kind == SourceKind.RECORDED


class TestProfileSameFrameSourcePathAsLive:
    """観測可能な完了状態: profile/intrinsics を live と同じ経路で取得できる（要件 6.4）。"""

    def test_profile_property_matches_reader_profile(self, tmp_path: Path) -> None:
        profile = _make_profile()
        session_dir = _write_session(
            tmp_path, seqs=[0, 1], t_capture_ms_values=[0.0, 10.0], profile=profile
        )
        reader = SessionReader(session_dir)
        metrics, clock = _make_metrics_and_clock()
        source = RecordedSource(reader, metrics, clock=clock)

        # FrameSource.profile プロパティ（BaseFrameSource が実装する共通経路）
        # がそのまま reader.profile を返す。再生専用の別経路は無い。
        assert source.profile == reader.profile
        assert source.profile.intrinsics is not None
        assert source.profile.intrinsics == profile.intrinsics


class TestFastReplayMatchesReaderContent:
    """観測可能な完了状態: 既定（最速）の再生は待たず、reader が直接返す内容と一致する。"""

    def test_fast_replay_seq_and_depth_match_reader_iter_frames(self, tmp_path: Path) -> None:
        seqs = [10, 11, 12, 13]
        t_values = [0.0, 5.0, 10.0, 15.0]
        session_dir = _write_session(tmp_path, seqs=seqs, t_capture_ms_values=t_values)

        expected_reader = SessionReader(session_dir)
        expected_frames = list(expected_reader.iter_frames())

        source = _make_source(session_dir, speed="fast")
        with source:
            actual_frames = list(source.frames())

        assert len(actual_frames) == len(expected_frames)
        assert [f.seq for f in actual_frames] == [f.seq for f in expected_frames]
        for actual, expected in zip(actual_frames, expected_frames):
            assert np.array_equal(actual.depth, expected.depth)
            assert actual.device_timestamp_ms == expected.device_timestamp_ms
            assert actual.timestamp_domain == expected.timestamp_domain
            assert actual.capture_latency_ms == expected.capture_latency_ms

    def test_frame_count_matches_supplied_count(self, tmp_path: Path) -> None:
        seqs = [0, 1, 2, 3, 4]
        t_values = [float(i) for i in range(len(seqs))]
        session_dir = _write_session(tmp_path, seqs=seqs, t_capture_ms_values=t_values)

        source = _make_source(session_dir, speed="fast")
        with source:
            frames = list(source.frames())

        assert len(frames) == len(seqs)
        assert source.stats.frames_yielded == len(seqs)


class TestEndOfRecording:
    """観測可能な完了状態: 記録済み枚数を超えて読み出そうとすると例外なく終わる。"""

    def test_iteration_ends_cleanly_at_recorded_frame_count(self, tmp_path: Path) -> None:
        seqs = [0, 1, 2]
        t_values = [0.0, 1.0, 2.0]
        session_dir = _write_session(tmp_path, seqs=seqs, t_capture_ms_values=t_values)

        source = _make_source(session_dir, speed="fast")
        with source:
            frames = list(source.frames())

        assert len(frames) == 3
        assert source.stats.frames_yielded == 3


class TestRealtimePacingUsesRecordedDeltas:
    """実装判断: `speed="realtime"` は記録時の `t_capture_ms` 差分を `sleep` へ渡す。

    実時間には一切依存せず、注入した偽の `sleep` が呼ばれた引数（秒）だけを
    検証する。
    """

    def test_sleep_called_with_deltas_derived_from_recorded_t_capture_ms(
        self, tmp_path: Path
    ) -> None:
        seqs = [0, 1, 2, 3]
        t_values = [0.0, 5.0, 17.0, 17.0]  # 差分: (n/a, 5, 12, 0)
        session_dir = _write_session(tmp_path, seqs=seqs, t_capture_ms_values=t_values)

        sleep_calls: list[float] = []

        def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        source = _make_source(session_dir, speed="realtime", sleep=fake_sleep)
        with source:
            list(source.frames())

        # 最初のフレームは待たない。以後は直前フレームとの t_capture_ms
        # 差分（ms）を秒へ変換した値で sleep を呼ぶ。差分が 0（またはそれ
        # 以下）のときは sleep を呼ばない（`delta_ms > 0` の場合のみ呼ぶ）。
        assert sleep_calls == pytest.approx([0.005, 0.012])

    def test_no_sleep_calls_in_fast_mode(self, tmp_path: Path) -> None:
        seqs = [0, 1, 2]
        t_values = [0.0, 100.0, 300.0]
        session_dir = _write_session(tmp_path, seqs=seqs, t_capture_ms_values=t_values)

        sleep_calls: list[float] = []

        def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        source = _make_source(session_dir, speed="fast", sleep=fake_sleep)
        with source:
            list(source.frames())

        assert sleep_calls == []


class TestRealtimeIsMeasurablyLongerThanFast:
    """観測可能な完了状態: 実時間再生は最速再生より実測で長くかかる。

    フレイキーさを避けるため、記録の `t_capture_ms` 間隔をミリ秒オーダー
    （数十 ms）に抑えたフィクスチャで、実際の `time.sleep` を使いつつも
    テスト全体を高速に保つ。
    """

    def test_realtime_replay_takes_longer_wall_clock_time_than_fast(
        self, tmp_path: Path
    ) -> None:
        seqs = [0, 1, 2, 3, 4]
        # 差分 15ms x 4 = 60ms 相当の待機が realtime モードで発生する見込み。
        t_values = [0.0, 15.0, 30.0, 45.0, 60.0]
        session_dir = _write_session(tmp_path, seqs=seqs, t_capture_ms_values=t_values)

        fast_source = _make_source(session_dir, speed="fast")
        start = time.perf_counter()
        with fast_source:
            list(fast_source.frames())
        fast_elapsed_s = time.perf_counter() - start

        realtime_source = _make_source(session_dir, speed="realtime")
        start = time.perf_counter()
        with realtime_source:
            list(realtime_source.frames())
        realtime_elapsed_s = time.perf_counter() - start

        # 60ms 分の待機のうち、スケジューリング上のノイズを見込んでも
        # 明確に検出できるマージン（30ms 以上）を最速再生より長くかかる
        # ことの下限として使う。
        assert realtime_elapsed_s > fast_elapsed_s + 0.03


class TestSpeedContentEquality:
    """観測可能な完了状態: 実時間再生と最速再生の返す系列が完全一致する（要件 6.5）。"""

    def test_fast_and_realtime_yield_identical_content_and_order(
        self, tmp_path: Path
    ) -> None:
        seqs = [100, 101, 102, 103]
        t_values = [0.0, 3.0, 3.0, 9.0]
        session_dir = _write_session(tmp_path, seqs=seqs, t_capture_ms_values=t_values)

        fast_source = _make_source(session_dir, speed="fast")
        with fast_source:
            fast_frames = list(fast_source.frames())

        # 実時間には依存させない（sleep を注入して即座に返す）ことで
        # テストを高速かつ決定的に保つ。ここで検証したいのは「待つかどうか」
        # ではなく「返す内容が一致するかどうか」である。
        realtime_source = _make_source(
            session_dir, speed="realtime", sleep=lambda seconds: None
        )
        with realtime_source:
            realtime_frames = list(realtime_source.frames())

        assert len(fast_frames) == len(realtime_frames) == len(seqs)
        for fast_frame, realtime_frame in zip(fast_frames, realtime_frames):
            _assert_frames_equal_ignoring_timing(fast_frame, realtime_frame)

        # t_capture_ms 自体は両モードとも再生セッションの SessionClock 起点
        # で新たに算出されるため、厳密な一致は要求しない（意図した設計判断。
        # `recorded.py` モジュール docstring 参照）が、各系列内では単調
        # 非減少であることは確認する。
        for frames in (fast_frames, realtime_frames):
            t_values_seen = [f.t_capture_ms for f in frames]
            assert t_values_seen == sorted(t_values_seen)


class TestDrainNeverInvoked:
    """観測可能な完了状態: 再生側の都合で取りこぼしを新たに作らない（二重防御。要件 6.7）。"""

    def test_drain_enabled_is_forced_false_regardless_of_caller_supplied_config(
        self, tmp_path: Path
    ) -> None:
        session_dir = _write_session(tmp_path, seqs=[0, 1, 2], t_capture_ms_values=[0.0, 1.0, 2.0])
        source = _make_source(
            session_dir, capture_config=CaptureConfig(drain_enabled=True)
        )

        assert source._drain_enabled is False  # noqa: SLF001 - 防御(a)のホワイトボックス検証

    def test_drain_latest_always_returns_none_and_zero_unconditionally(
        self, tmp_path: Path
    ) -> None:
        session_dir = _write_session(tmp_path, seqs=[0, 1, 2], t_capture_ms_values=[0.0, 1.0, 2.0])
        source = _make_source(session_dir)

        assert source._drain_latest() == (None, 0)
        assert source._drain_latest() == (None, 0)

    def test_no_discard_count_accumulates_even_with_default_drain_enabled_config(
        self, tmp_path: Path
    ) -> None:
        session_dir = _write_session(
            tmp_path, seqs=[0, 1, 2, 3, 4], t_capture_ms_values=[0.0, 1.0, 2.0, 3.0, 4.0]
        )
        source = _make_source(session_dir)

        with source:
            frames = list(source.frames())

        assert all(f.dropped_before == 0 for f in frames)
        assert source.stats.frames_dropped == 0


class TestInvalidSpeedRejected:
    def test_unknown_speed_raises_config_error(self, tmp_path: Path) -> None:
        session_dir = _write_session(tmp_path, seqs=[0], t_capture_ms_values=[0.0])
        reader = SessionReader(session_dir)
        metrics, clock = _make_metrics_and_clock()

        from sensing_foundation.errors import SensingConfigError

        with pytest.raises(SensingConfigError):
            RecordedSource(reader, metrics, clock=clock, speed="turbo")  # type: ignore[arg-type]


class TestNoHardwareOrSdkDependency:
    """観測可能な完了状態: SDK 非導入環境で記録を再生できる（実機・SDK 参照無し）。"""

    def test_module_does_not_import_pyrealsense2(self) -> None:
        source_code = inspect.getsource(recorded_module)
        assert "pyrealsense2" not in source_code
        assert "import rs" not in source_code

    def test_end_to_end_replay_without_hardware(self, tmp_path: Path) -> None:
        seqs = [0, 1, 2]
        session_dir = _write_session(
            tmp_path, seqs=seqs, t_capture_ms_values=[0.0, 1.0, 2.0]
        )
        source = _make_source(session_dir, speed="fast")

        with source:
            frames = list(source.frames())

        assert len(frames) == 3
        for frame in frames:
            assert frame.depth.shape == (HEIGHT_PX, WIDTH_PX)
            assert frame.depth.dtype == np.uint16
            assert frame.source == SourceKind.RECORDED
