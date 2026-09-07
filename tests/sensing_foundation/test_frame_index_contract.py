"""「フレーム番号」が指す量の契約を、境界をまたいで固定する（タスク 4.7）。

同じ「フレーム番号」に見える量が3つあり、**リングが古いフレームを追い出した
ときだけ**食い違う（design.md「SessionReader /『フレーム番号』が指す3つの量」）:

- (a) **行位置**: `SessionReader.read(i)` の引数 `i`
- (b) **記録側の通し番号**: 索引行の `i`。記録時の `CaptureFrame.index` そのもの
- (c) **再生側の通し番号**: `RecordedSource` が下流へ渡す `CaptureFrame.index`

契約は「`ThrowRecord.extra["sensing"]` の `frame_index_from` / `frame_index_to`
は **(b)**、**両端を含む閉区間**」である（要件 7.7）。

**なぜ独立したファイルなのか**: この欠陥は `FrameRingBuffer` / `SessionRecorder`
/ `SessionReader` / `link_to_session` / `types.CaptureFrame` の**間**の不整合で
あり、どれか1つの境界の中では契約を定義できない。各モジュールの単体テストは
それぞれの境界に閉じたままにし、境界をまたぐ約束だけを本ファイルへ集める。

**なぜ追い出しを起こすのか**: 追い出しが無ければ (a) と (b) が一致してしまい、
どちらの量を扱っているか区別できない。区別せずに書いたコードは追い出しの無い
記録では正常に動き、投擲だけを残すリング運用（要件 5.5）で初めて**静かに
ずれた範囲を読む**。したがって本ファイルのテストは**必ず追い出しを起こす**。

要件: 5.5, 6.1, 7.7
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest
from prediction_core import PredictionConfig, Sample, ThrowRecord
from synthetic import make_depth_frame

from sensing_foundation.metrics import CaptureMetrics
from sensing_foundation.obslog import NullLogger
from sensing_foundation.recording import layout
from sensing_foundation.recording.reader import SessionReader
from sensing_foundation.recording.ringbuffer import FrameRingBuffer
from sensing_foundation.recording.writer import SessionRecorder
from sensing_foundation.sources.recorded import RecordedSource
from sensing_foundation.throw_store import link_to_session
from sensing_foundation.timebase import SessionClock
from sensing_foundation.types import (
    CameraIntrinsics,
    CaptureFrame,
    CaptureStats,
    SourceKind,
    StreamProfile,
    TimestampDomain,
)

WIDTH_PX = 4
HEIGHT_PX = 3
FPS = 10

#: リングの容量（`ceil(RING_SECONDS * FPS)` = 6 枚）。
RING_SECONDS = 0.6
RING_CAPACITY = 6

#: 取得する総枚数。容量より多いので必ず追い出しが起きる。
TOTAL_FRAMES = 20

#: 追い出し後に残る記録側通し番号（閉区間）。
EXPECTED_FIRST = TOTAL_FRAMES - RING_CAPACITY  # 14
EXPECTED_LAST = TOTAL_FRAMES - 1  # 19


def _profile() -> StreamProfile:
    return StreamProfile(
        width_px=WIDTH_PX,
        height_px=HEIGHT_PX,
        fps=FPS,
        depth_scale_mm=1.0,
        color_enabled=False,
        intrinsics=CameraIntrinsics(
            width_px=WIDTH_PX,
            height_px=HEIGHT_PX,
            fx_px=50.0,
            fy_px=50.0,
            ppx_px=WIDTH_PX / 2.0,
            ppy_px=HEIGHT_PX / 2.0,
            model="brown_conrady",
            coeffs=(0.0, 0.0, 0.0, 0.0, 0.0),
        ),
    )


def _frame(profile: StreamProfile, index: int) -> CaptureFrame:
    """`index` から内容が一意に決まるフレーム（`depth` から番号を復元できる）。"""
    return CaptureFrame(
        index=index,
        seq=1000 + index,
        t_capture_ms=float(index) * (1000.0 / profile.fps),
        device_timestamp_ms=None,
        timestamp_domain=TimestampDomain.UNKNOWN,
        capture_latency_ms=None,
        depth=make_depth_frame(WIDTH_PX, HEIGHT_PX, 1000 + index),
        profile=profile,
        source=SourceKind.SIMULATED,
        dropped_before=0,
        gap_before=0,
    )


def _throw_record() -> ThrowRecord:
    """対応付けの器としての最小の `ThrowRecord`（内容は本テストの関心外）。"""
    return ThrowRecord(
        record_id="throw-frame-index-contract",
        source=SourceKind.SIMULATED,
        config=PredictionConfig(),
        samples=(
            Sample(t_ms=0.0, x_mm=0.0, y_mm=0.0, z_mm=500.0),
            Sample(t_ms=50.0, x_mm=12.5, y_mm=-2.5, z_mm=480.0),
        ),
        predictions=(),
        extra={},
    )


@pytest.fixture
def evicted_session(tmp_path: Path) -> tuple[Path, dict[int, np.ndarray]]:
    """`TOTAL_FRAMES` 枚を取得し、リングが保持した末尾だけを記録したセッション。

    Returns:
        `(session_dir, 記録側通し番号 -> 元の depth 配列)`。期待値は
        `FrameRingBuffer` / `SessionRecorder` を通す前の**元のフレーム**から
        作るため、記録経路と読み出し経路が対称に壊れても検出できる。
    """
    profile = _profile()
    ring = FrameRingBuffer(RING_SECONDS, profile)

    originals: dict[int, np.ndarray] = {}
    for index in range(TOTAL_FRAMES):
        frame = _frame(profile, index)
        originals[index] = np.array(frame.depth)  # 書き込み前に控えを取る
        ring.append(frame)

    assert len(ring) == RING_CAPACITY, "リングが追い出していないと前提が崩れる"

    session_id = layout.new_session_id(time.time() * 1000.0)
    recorder = SessionRecorder(
        root=tmp_path / "sessions",
        session_id=session_id,
        profile=profile,
        device=None,
        runtime={"origin": "test_frame_index_contract"},
        compression="none",
        logger=NullLogger(),
        source=SourceKind.SIMULATED,
        capture=None,
    )
    try:
        written = ring.flush_to(recorder)
    finally:
        recorder.close(
            CaptureStats(
                frames_yielded=TOTAL_FRAMES,
                frames_dropped=0,
                frames_missing=0,
                duration_ms=1000.0,
                measured_fps=float(FPS),
                acquire_errors=0,
            )
        )
    assert written == RING_CAPACITY

    return tmp_path / "sessions" / session_id, originals


# ---------------------------------------------------------------------------
# 前提: 実際に追い出しが起きており、(a) と (b) が食い違っている
# ---------------------------------------------------------------------------


def test_the_recording_actually_evicted_frames_so_the_quantities_differ(
    evicted_session,
) -> None:
    """本ファイルのテストが空振りしていないことを最初に確かめる。

    追い出しが起きていなければ (a) 行位置 と (b) 記録側通し番号 が一致し、
    以降のテストはどちらの量を扱っていても通ってしまう。
    """
    session_dir, _ = evicted_session
    reader = SessionReader(session_dir)

    assert len(reader) == RING_CAPACITY
    assert reader.recorded_index_range == (EXPECTED_FIRST, EXPECTED_LAST)
    # 行位置は 0 始まり、記録側通し番号は 14 始まりで食い違う。
    assert [reader.read(i).index for i in range(len(reader))] != list(range(len(reader)))
    assert reader.read(0).index == EXPECTED_FIRST


# ---------------------------------------------------------------------------
# 契約: link_to_session が残した範囲から元のフレームを取り直せる
# ---------------------------------------------------------------------------


def test_range_stored_by_link_to_session_reads_back_the_original_frames(
    evicted_session,
) -> None:
    """タスク 4.7 の観測可能な完了状態そのもの。

    `link_to_session()` へ渡すのは**記録側の通し番号**であり、保存された値を
    そのまま `iter_recorded_range()` へ渡すと元のフレームが戻る。期待値は
    記録経路を通す前の元配列（`originals`）と突き合わせる。
    """
    session_dir, originals = evicted_session
    reader = SessionReader(session_dir)

    first = reader.read(0)
    last = reader.read(len(reader) - 1)

    linked = link_to_session(_throw_record(), session_dir.name, first.index, last.index)
    sensing = linked.extra["sensing"]

    assert sensing["session_id"] == session_dir.name
    assert (sensing["frame_index_from"], sensing["frame_index_to"]) == (
        EXPECTED_FIRST,
        EXPECTED_LAST,
    )

    frames = list(
        reader.iter_recorded_range(
            sensing["frame_index_from"], sensing["frame_index_to"]
        )
    )
    assert len(frames) == RING_CAPACITY
    for frame in frames:
        assert np.array_equal(frame.depth, originals[frame.index]), frame.index


def test_treating_the_stored_range_as_row_positions_does_not_silently_succeed(
    evicted_session,
) -> None:
    """取り違えたときに**静かにずれた範囲を読まない**ことを固定する（非空虚性）。

    保存されているのは記録側の通し番号（14..19）であり、これを行位置として
    `read()` へ渡すのは誤りである。旧来この誤りは範囲内なら例外にならず、
    別のフレームを黙って返し得た。本テストは「範囲外なら失敗する」ことと、
    「範囲内でも別のフレームを指す」ことの両方を示す。
    """
    session_dir, _ = evicted_session
    reader = SessionReader(session_dir)

    # 14..19 は行位置としては範囲外（行位置は 0..5）なので失敗する。
    with pytest.raises(IndexError):
        reader.read(EXPECTED_FIRST)

    # 逆向きの取り違え（行位置を通し番号として渡す）も失敗する。
    with pytest.raises(IndexError) as excinfo:
        reader.read_recorded(0)
    assert str(EXPECTED_FIRST) in str(excinfo.value)

    # 有効な行位置のどれをとっても、それ自身と等しい記録側通し番号にはならない
    # ——2つの量が全域で食い違っていることを、範囲検査に頼らずに示す。
    for position in range(len(reader)):
        assert reader.read(position).index != position


def test_replay_renumbers_from_zero_so_the_frame_source_invariant_holds(
    evicted_session,
) -> None:
    """(c) 再生側の通し番号は 0 始まりへ振り直される。

    `types.CaptureFrame` の不変条件「`index` は 0 から欠番なく増加する」は
    **`FrameSource` が下流へ渡すフレーム**についてのものであり、
    `SessionReader.read()` が返す記録済みフレームには適用されない。
    その差をここで固定する。
    """
    session_dir, _ = evicted_session
    reader = SessionReader(session_dir)

    # (b) 記録側: 14 始まり
    assert reader.read(0).index == EXPECTED_FIRST

    # (c) 再生側: 0 始まりで欠番なし
    clock = SessionClock(session_id="frame-index-contract")
    metrics = CaptureMetrics(NullLogger(), clock, sysstat=None)
    source = RecordedSource(SessionReader(session_dir), metrics, clock=clock, speed="fast")
    with source:
        replayed = list(source.frames())
    assert [f.index for f in replayed] == list(range(RING_CAPACITY))
    # 同じフレームであることは seq で確かめる（seq は記録時の値を保つ）。
    assert [f.seq for f in replayed] == [
        1000 + n for n in range(EXPECTED_FIRST, EXPECTED_LAST + 1)
    ]
