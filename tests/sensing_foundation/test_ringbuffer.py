"""`sensing_foundation.recording.ringbuffer.FrameRingBuffer` に対するテスト（タスク 4.2）。

観測可能な完了状態（tasks.md 4.2）を固定する:
- 秒数×fps を超えて保持されないこと（超過分は古いものから捨てる、FIFO）
- 必要バイト数の算出が解像度・fps・秒数に比例すること

あわせて design.md「FrameRingBuffer」が定める以下の点も固定する:
- `append()` はフレームを複製しない（`flush_to()` 経由で渡されるオブジェクトが
  `append()` されたものと同一 `is` であることで間接的に確認する）
- `flush_to()` は保持順（古い→新しい）で書き出し、書いた枚数を返す
- `flush_to()` は保持内容を変更しない（`clear()` は別操作）
- `clear()` は保持内容を空にする
- `max_bytes` はコンストラクタが受理するのみで、本クラス自身は容量計算にも
  append の拒否にも使わない（`RuntimeSettings.resolve()` が起動時検証の主体
  であるという design.md の記述をそのまま実装した結果。本ファイルの
  `TestMaxBytesParameter` で固定する）
"""

from __future__ import annotations

import math

from synthetic import make_depth_frame

from sensing_foundation.recording.ringbuffer import FrameRingBuffer
from sensing_foundation.types import (
    CameraIntrinsics,
    CaptureFrame,
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


def _make_frame(profile: StreamProfile, *, index: int, seq: int) -> CaptureFrame:
    depth = make_depth_frame(profile.width_px, profile.height_px, seq)
    return CaptureFrame(
        index=index,
        seq=seq,
        t_capture_ms=float(index) * (1000.0 / profile.fps),
        device_timestamp_ms=None,
        timestamp_domain=TimestampDomain.UNKNOWN,
        capture_latency_ms=None,
        depth=depth,
        profile=profile,
        source=SourceKind.SIMULATED,
        dropped_before=0,
        gap_before=0,
    )


class FakeRecorder:
    """`_RecorderLike`（`write(self, frame: CaptureFrame) -> None`）を満たす偽実装。

    `SessionRecorder`（タスク 4.3）は本タスク時点で未実装のため、
    `flush_to()` を検証するためのテスト専用スタブとして使う。渡された
    フレームをそのままリストへ積むだけで、複製もファイル I/O も行わない。
    """

    def __init__(self) -> None:
        self.written: list[CaptureFrame] = []

    def write(self, frame: CaptureFrame) -> None:
        self.written.append(frame)


class TestRequiredBytes:
    """`required_bytes()` の比例性と厳密な数値例（design.md Risks の例）。"""

    def test_exact_numeric_example_from_design_doc(self) -> None:
        # design.md「FrameRingBuffer / Risks」: 640x480/60fps・3秒で約110MB。
        # 厳密には width*height*2*fps*seconds = 640*480*2*60*3 = 110,592,000 bytes。
        profile = _make_profile(width_px=640, height_px=480, fps=60)
        assert FrameRingBuffer.required_bytes(3.0, profile) == 110_592_000

    def test_matches_config_module_formula(self) -> None:
        # sensing_foundation.config._required_ring_bytes() と同一の式・
        # 同一の丸め方（int() によるトランケート）であることを固定する。
        profile = _make_profile(width_px=640, height_px=480, fps=60)
        seconds = 2.5
        expected = int(640 * 480 * 2 * 60 * seconds)
        assert FrameRingBuffer.required_bytes(seconds, profile) == expected

    def test_proportional_to_seconds(self) -> None:
        profile = _make_profile(width_px=100, height_px=50, fps=30)
        base = FrameRingBuffer.required_bytes(1.0, profile)
        doubled = FrameRingBuffer.required_bytes(2.0, profile)
        assert doubled == base * 2

    def test_proportional_to_fps(self) -> None:
        profile_30 = _make_profile(width_px=100, height_px=50, fps=30)
        profile_60 = _make_profile(width_px=100, height_px=50, fps=60)
        base = FrameRingBuffer.required_bytes(1.0, profile_30)
        doubled = FrameRingBuffer.required_bytes(1.0, profile_60)
        assert doubled == base * 2

    def test_proportional_to_resolution(self) -> None:
        profile_small = _make_profile(width_px=100, height_px=50, fps=30)
        profile_wide = _make_profile(width_px=200, height_px=50, fps=30)
        profile_tall = _make_profile(width_px=100, height_px=100, fps=30)
        base = FrameRingBuffer.required_bytes(1.0, profile_small)
        wide = FrameRingBuffer.required_bytes(1.0, profile_wide)
        tall = FrameRingBuffer.required_bytes(1.0, profile_tall)
        assert wide == base * 2
        assert tall == base * 2


class TestCapacityEnforcement:
    """保持枚数が `ceil(seconds * fps)` を超えないこと、古いものから捨てること。"""

    def test_len_never_exceeds_seconds_times_fps(self) -> None:
        profile = _make_profile(fps=10)
        seconds = 0.5  # ceil(0.5 * 10) = 5
        capacity = math.ceil(seconds * profile.fps)
        buf = FrameRingBuffer(seconds, profile)

        for i in range(capacity * 3):
            buf.append(_make_frame(profile, index=i, seq=i))
            assert len(buf) <= capacity

        assert len(buf) == capacity

    def test_oldest_frames_are_discarded_first(self) -> None:
        profile = _make_profile(fps=10)
        seconds = 0.5  # capacity == 5
        capacity = math.ceil(seconds * profile.fps)
        buf = FrameRingBuffer(seconds, profile)

        total = capacity + 3
        for i in range(total):
            buf.append(_make_frame(profile, index=i, seq=i))

        recorder = FakeRecorder()
        buf.flush_to(recorder)
        remaining_seqs = [frame.seq for frame in recorder.written]

        # 最後に append した capacity 件分（新しい順に並べても FIFO でも）
        # の seq だけが残っているはず。
        expected_seqs = list(range(total - capacity, total))
        assert remaining_seqs == expected_seqs

    def test_capacity_computation_uses_ceil(self) -> None:
        # 0.5秒 * 3fps = 1.5 -> ceil で 2 になることを固定する。
        profile = _make_profile(fps=3)
        buf = FrameRingBuffer(0.5, profile)
        for i in range(10):
            buf.append(_make_frame(profile, index=i, seq=i))
        assert len(buf) == 2


class TestNoCopyOnAppend:
    """`append()` がフレームを複製しないこと（オブジェクト同一性で確認）。"""

    def test_flush_to_passes_through_same_object(self) -> None:
        profile = _make_profile(fps=10)
        buf = FrameRingBuffer(1.0, profile)
        frame = _make_frame(profile, index=0, seq=0)
        buf.append(frame)

        recorder = FakeRecorder()
        buf.flush_to(recorder)

        assert len(recorder.written) == 1
        assert recorder.written[0] is frame

    def test_depth_array_identity_preserved(self) -> None:
        # depth 配列そのものが複製されていないことも、参照の同一性で確認する。
        profile = _make_profile(fps=10)
        buf = FrameRingBuffer(1.0, profile)
        frame = _make_frame(profile, index=0, seq=0)
        original_depth = frame.depth
        buf.append(frame)

        recorder = FakeRecorder()
        buf.flush_to(recorder)

        assert recorder.written[0].depth is original_depth


class TestFlushTo:
    """`flush_to()` が正しい枚数・順序で書き出し、保持内容を変更しないこと。"""

    def test_returns_count_written(self) -> None:
        profile = _make_profile(fps=10)
        buf = FrameRingBuffer(1.0, profile)  # capacity == 10
        for i in range(4):
            buf.append(_make_frame(profile, index=i, seq=i))

        recorder = FakeRecorder()
        written_count = buf.flush_to(recorder)

        assert written_count == 4
        assert len(recorder.written) == 4

    def test_writes_in_fifo_chronological_order(self) -> None:
        profile = _make_profile(fps=10)
        buf = FrameRingBuffer(1.0, profile)
        for i in range(5):
            buf.append(_make_frame(profile, index=i, seq=i))

        recorder = FakeRecorder()
        buf.flush_to(recorder)

        assert [f.seq for f in recorder.written] == [0, 1, 2, 3, 4]

    def test_flush_to_does_not_clear_buffer(self) -> None:
        profile = _make_profile(fps=10)
        buf = FrameRingBuffer(1.0, profile)
        for i in range(3):
            buf.append(_make_frame(profile, index=i, seq=i))

        recorder = FakeRecorder()
        buf.flush_to(recorder)

        assert len(buf) == 3

        # 2回連続で flush しても、変更していないので同じ内容が書ける。
        recorder2 = FakeRecorder()
        second_count = buf.flush_to(recorder2)
        assert second_count == 3
        assert [f.seq for f in recorder2.written] == [0, 1, 2]

    def test_flush_empty_buffer_returns_zero(self) -> None:
        profile = _make_profile(fps=10)
        buf = FrameRingBuffer(1.0, profile)
        recorder = FakeRecorder()
        assert buf.flush_to(recorder) == 0
        assert recorder.written == []


class TestClear:
    """`clear()` が保持内容を空にすること。"""

    def test_clear_empties_buffer(self) -> None:
        profile = _make_profile(fps=10)
        buf = FrameRingBuffer(1.0, profile)
        for i in range(5):
            buf.append(_make_frame(profile, index=i, seq=i))
        assert len(buf) == 5

        buf.clear()

        assert len(buf) == 0

    def test_append_after_clear_works_normally(self) -> None:
        profile = _make_profile(fps=10)
        buf = FrameRingBuffer(1.0, profile)
        buf.append(_make_frame(profile, index=0, seq=0))
        buf.clear()
        buf.append(_make_frame(profile, index=1, seq=1))
        assert len(buf) == 1


class TestMaxBytesParameter:
    """`max_bytes` は本クラス内では容量計算にも append 拒否にも使われない。

    design.md「FrameRingBuffer」の Preconditions
    (`required_bytes(...) <= max_bytes`) は `RuntimeSettings.resolve()`
    （タスク 1.6、`sensing_foundation.config`）が起動時に強制する不変条件
    であり、`FrameRingBuffer` 自身はこれを再検証しない、という読みを固定
    するテスト群。
    """

    def test_construction_does_not_raise_when_max_bytes_below_required(self) -> None:
        profile = _make_profile(width_px=640, height_px=480, fps=60)
        required = FrameRingBuffer.required_bytes(3.0, profile)
        # required を大きく下回る max_bytes を渡しても構築時に例外は出ない
        # （検証は RuntimeSettings 側の責務であるため）。
        buf = FrameRingBuffer(3.0, profile, max_bytes=required // 100)
        assert buf is not None

    def test_capacity_unaffected_by_max_bytes_value(self) -> None:
        profile = _make_profile(fps=10)
        seconds = 0.5  # capacity == 5
        capacity = math.ceil(seconds * profile.fps)

        buf_none = FrameRingBuffer(seconds, profile, max_bytes=None)
        buf_tiny = FrameRingBuffer(seconds, profile, max_bytes=1)
        buf_huge = FrameRingBuffer(seconds, profile, max_bytes=10**12)

        for buf in (buf_none, buf_tiny, buf_huge):
            for i in range(capacity * 2):
                buf.append(_make_frame(profile, index=i, seq=i))
            assert len(buf) == capacity

    def test_default_max_bytes_is_none(self) -> None:
        profile = _make_profile(fps=10)
        buf = FrameRingBuffer(1.0, profile)
        # 内部実装の詳細だが、既定値がコンストラクタ通りに None であることは
        # 呼び出し規約（design.md のシグネチャ）を満たす直接的な確認になる。
        assert buf._max_bytes is None  # noqa: SLF001


class TestDependencyDirection:
    """`recording/ringbuffer.py` が `source` を import していないこと（静的検証）。"""

    def test_does_not_import_source_package(self) -> None:
        import ast
        from pathlib import Path

        from sensing_foundation.recording import ringbuffer

        source_path = Path(ringbuffer.__file__)
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported_modules: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.append(node.module)

        assert not any(
            module == "sensing_foundation.source" or module.startswith("sensing_foundation.sources")
            for module in imported_modules
        )
