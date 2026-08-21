"""`sensing_foundation.recording.reader.SessionReader` に対するテスト（タスク 4.4）。

**主な検証手段は手書きの最小フィクスチャである**（`SessionRecorder`（タスク
4.3）は使わない）。tasks.md 4.4 の「テスト用の記録は手書きの最小フィクスチャで
用意し、書き出し側（4.3）の完成を待たずに検証できるようにする」という記述の
本来の意図は、4.3 が完成済みの今も変わらない: 読み出し側の正しさを、書き出し
側の実装から独立に証明することで、両者に対称的なバグが入って互いに帳尻を
合わせてしまう事故（一方のバグをもう一方のバグが隠す）を防ぐためである。
本ファイルの `_write_session()` は `manifest.json` / `frames.ndjson` /
`depth.bin` / `summary.json` の4ファイルを `Path.write_text` /
`Path.write_bytes` で直接組み立てる（`writer.py` の内部実装を一切呼ばない）。

`SessionRecorder` を使うのは `test_integration_roundtrip_with_session_recorder`
の1件のみであり、これは手書きフィクスチャによる検証を補強する副次的な
確認にとどめる（主たる証拠にはしない）。

観測可能な完了状態（tasks.md 4.4）を固定する:
- 索引欠損（索引行の JSON 破損）・位置改竄・未知の形式版の3ケースでそれぞれ
  期待した専用の例外が送出される
- 正常なセッションでは2回の反復（同一インスタンス）が完全一致する

あわせて design.md「SessionReader」が定める以下の点も固定する:
- `read(i)` はブロブのオフセットと長さのみで決まり、内部状態（カーソル）に
  依存しない（`read(2)` → `read(0)` → `read(2)` が同じ結果を返す）
- 展開後の長さが索引の `raw_len` と一致しない場合は `read()`/`iter_frames()`
  の実行時に `RecordingFormatError` が送出され、黙って打ち切られない
  （部分的な読み出しを成功として返さない）
- `summary.json` 不在（書きかけセッション）は構築を失敗させず、警告
  （`InProgressSessionWarning`）のみで読める範囲を読む
- 返された `CaptureFrame.depth` は読み取り専用である（タスク 1.5 の不変条件）
- `CaptureFrame` の `__eq__`（dataclass 自動生成）は複数画素の `depth` 配列を
  含むと `ValueError` を送出する（numpy 配列の真偽値評価の曖昧さのため）ため、
  フレームの等価性はフィールドごとの比較（`depth` は `np.array_equal`）で行う
"""

from __future__ import annotations

import json
import sys
import zlib
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest
from synthetic import make_depth_frame

from sensing_foundation.errors import RecordingFormatError, RecordingVersionError
from sensing_foundation.recording import layout
from sensing_foundation.recording.reader import InProgressSessionWarning, SessionReader
from sensing_foundation.recording.writer import SessionRecorder
from sensing_foundation.types import (
    CaptureFrame,
    CaptureStats,
    SourceKind,
    TimestampDomain,
)

WIDTH_PX = 4
HEIGHT_PX = 3
FPS = 30


def _build_manifest(
    *,
    format_version: str = layout.RECORDING_FORMAT_VERSION,
    compression: str = "none",
    source: str = "simulated",
) -> dict[str, object]:
    intrinsics_meta = {
        "fx_px": 50.0,
        "fy_px": 50.0,
        "ppx_px": WIDTH_PX / 2.0,
        "ppy_px": HEIGHT_PX / 2.0,
        "model": "brown_conrady",
        "coeffs": [0.0, 0.0, 0.0, 0.0, 0.0],
    }
    profile_meta = {
        "width_px": WIDTH_PX,
        "height_px": HEIGHT_PX,
        "fps": FPS,
        "depth_scale_mm": 1.0,
        "color_enabled": False,
        "pixel_format": "z16",
    }
    return {
        "format_version": format_version,
        "session_id": "handwritten-session",
        "source": source,
        "started_wall_ms": 0.0,
        "profile": profile_meta,
        "intrinsics": intrinsics_meta,
        "device": None,
        "runtime": {},
        "capture": {},
        "blob": {
            "file": layout.BLOB_NAME,
            "dtype": "uint16",
            "little_endian": sys.byteorder == "little",
            "frame_bytes": WIDTH_PX * HEIGHT_PX * 2,
            "compression": compression,
        },
    }


def _write_session(
    tmp_path: Path,
    *,
    seqs: Sequence[int] = (100, 101, 102),
    compression: str = "none",
    format_version: str = layout.RECORDING_FORMAT_VERSION,
    include_summary: bool = True,
    tamper_raw_len_at: int | None = None,
) -> tuple[Path, list[np.ndarray]]:
    """手書きの最小フィクスチャ（4ファイル）を組み立てる。

    `writer.py` の `_write_frame()` / `_build_index_line()` が生成するのと
    同じキー構成・順序・オフセット計算を、本関数が独立に手で再現する
    （`SessionRecorder` は一切呼ばない）。

    Args:
        tamper_raw_len_at: 指定した位置の索引行だけ `raw_len` を意図的に
            ずらす（off/len 自体は正しいままにする）。`read()` 実行時の
            「展開後の長さ不一致」検出を確認するために使う。
    """
    session_dir = tmp_path / "session"
    session_dir.mkdir()

    manifest = _build_manifest(format_version=format_version, compression=compression)
    (session_dir / layout.MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )

    depths = [make_depth_frame(WIDTH_PX, HEIGHT_PX, seq) for seq in seqs]

    index_lines: list[str] = []
    offset = 0
    blob_path = session_dir / layout.BLOB_NAME
    with blob_path.open("wb") as blob_f:
        for i, (seq, depth) in enumerate(zip(seqs, depths)):
            raw_bytes = depth.tobytes()
            raw_len = len(raw_bytes)
            payload = zlib.compress(raw_bytes) if compression == "zlib" else raw_bytes
            blob_f.write(payload)

            reported_raw_len = raw_len
            if tamper_raw_len_at is not None and i == tamper_raw_len_at:
                reported_raw_len = raw_len + 1

            record = {
                "i": i,
                "seq": seq,
                "t_capture_ms": float(i) * (1000.0 / FPS),
                "device_ts_ms": None,
                "ts_domain": str(TimestampDomain.UNKNOWN),
                "capture_latency_ms": None,
                "off": offset,
                "len": len(payload),
                "raw_len": reported_raw_len,
                "dropped_before": 0,
                "gap_before": 0,
            }
            index_lines.append(json.dumps(record, ensure_ascii=False))
            offset += len(payload)

    (session_dir / layout.INDEX_NAME).write_text(
        "\n".join(index_lines) + "\n", encoding="utf-8"
    )

    if include_summary:
        summary = {
            "frames_written": len(seqs),
            "frames_dropped": 0,
            "frames_missing": 0,
            "measured_fps": float(FPS),
            "duration_ms": 100.0,
            "bytes_written": offset,
            "write_errors": 0,
            "closed_wall_ms": 1000.0,
        }
        (session_dir / layout.SUMMARY_NAME).write_text(
            json.dumps(summary, ensure_ascii=False), encoding="utf-8"
        )

    return session_dir, depths


def _assert_frames_equal(a: CaptureFrame, b: CaptureFrame) -> None:
    """`CaptureFrame` 同士の等価性をフィールドごとに比較する（`depth` は `np.array_equal`）。

    dataclass 自動生成の `__eq__` は複数画素の `depth` を含むタプル比較で
    `ValueError` を送出する（`test_dataclass_eq_raises_on_multi_pixel_depth`
    参照）ため、素の `==` は使わない。
    """
    assert a.index == b.index
    assert a.seq == b.seq
    assert a.t_capture_ms == b.t_capture_ms
    assert a.device_timestamp_ms == b.device_timestamp_ms
    assert a.timestamp_domain == b.timestamp_domain
    assert a.capture_latency_ms == b.capture_latency_ms
    assert np.array_equal(a.depth, b.depth)
    assert a.profile == b.profile
    assert a.source == b.source
    assert a.dropped_before == b.dropped_before
    assert a.gap_before == b.gap_before


# -- 正常系: profile / manifest / summary / len ------------------------------


def test_profile_manifest_summary_and_len(tmp_path: Path) -> None:
    session_dir, _depths = _write_session(tmp_path, seqs=(100, 101, 102))

    reader = SessionReader(session_dir)

    assert reader.profile.width_px == WIDTH_PX
    assert reader.profile.height_px == HEIGHT_PX
    assert reader.profile.fps == FPS
    assert reader.profile.color_enabled is False
    assert reader.profile.intrinsics is not None
    assert reader.profile.intrinsics.model == "brown_conrady"

    assert reader.manifest["session_id"] == "handwritten-session"
    assert reader.manifest["format_version"] == layout.RECORDING_FORMAT_VERSION

    assert reader.summary is not None
    assert reader.summary["frames_written"] == 3

    assert len(reader) == 3


# -- 正常系: read(i) の再構成 --------------------------------------------------


def test_read_reconstructs_each_frame(tmp_path: Path) -> None:
    session_dir, depths = _write_session(tmp_path, seqs=(100, 101, 102))
    reader = SessionReader(session_dir)

    for i, (seq, depth) in enumerate(zip((100, 101, 102), depths)):
        frame = reader.read(i)
        assert frame.index == i
        assert frame.seq == seq
        assert frame.t_capture_ms == pytest.approx(float(i) * (1000.0 / FPS))
        assert frame.device_timestamp_ms is None
        assert frame.timestamp_domain == TimestampDomain.UNKNOWN
        assert frame.capture_latency_ms is None
        assert np.array_equal(frame.depth, depth)
        assert frame.profile == reader.profile
        assert frame.source == SourceKind.SIMULATED
        assert frame.dropped_before == 0
        assert frame.gap_before == 0


def test_read_out_of_range_raises_index_error(tmp_path: Path) -> None:
    session_dir, _ = _write_session(tmp_path, seqs=(100, 101, 102))
    reader = SessionReader(session_dir)

    with pytest.raises(IndexError):
        reader.read(3)
    with pytest.raises(IndexError):
        reader.read(-1)


# -- 正常系: read(i) の無状態性 -------------------------------------------------


def test_read_is_stateless(tmp_path: Path) -> None:
    session_dir, depths = _write_session(tmp_path, seqs=(100, 101, 102))
    reader = SessionReader(session_dir)

    first = reader.read(2)
    reader.read(0)
    third = reader.read(2)

    _assert_frames_equal(first, third)
    assert np.array_equal(first.depth, depths[2])
    assert np.array_equal(third.depth, depths[2])


# -- 正常系: 同一インスタンスの複数回反復 --------------------------------------


def test_iter_frames_same_instance_twice_matches(tmp_path: Path) -> None:
    session_dir, depths = _write_session(tmp_path, seqs=(100, 101, 102))
    reader = SessionReader(session_dir)

    first_pass = list(reader.iter_frames())
    second_pass = list(reader.iter_frames())

    assert len(first_pass) == len(second_pass) == 3
    for a, b, depth in zip(first_pass, second_pass, depths):
        _assert_frames_equal(a, b)
        assert np.array_equal(a.depth, depth)


def test_dataclass_eq_raises_on_multi_pixel_depth(tmp_path: Path) -> None:
    """`CaptureFrame` の素の `==` は複数画素の `depth` を含むと `ValueError` を送出する。

    numpy 配列の `==` は要素ごとの真偽値配列を返し、dataclass 生成の
    `__eq__` はそれをタプル比較の一部として `bool()` 評価しようとするため、
    「配列の真偽値は曖昧」というエラーになる。フレームの等価性判定に
    素の `==` を使ってはならない理由をここで明示的に固定する。
    """
    session_dir, _ = _write_session(tmp_path, seqs=(100, 101, 102))
    reader = SessionReader(session_dir)

    a = reader.read(0)
    b = reader.read(0)

    with pytest.raises(ValueError):
        _ = a == b


# -- 異常系: 未知の形式版 -------------------------------------------------------


def test_unknown_format_version_raises(tmp_path: Path) -> None:
    session_dir, _ = _write_session(tmp_path, format_version="0.1-bogus")

    with pytest.raises(RecordingVersionError):
        SessionReader(session_dir)


def test_recording_version_error_is_recording_format_error() -> None:
    assert issubclass(RecordingVersionError, RecordingFormatError)


# -- 異常系: 索引行の JSON 破損（索引欠損） -------------------------------------


def test_corrupted_index_line_raises(tmp_path: Path) -> None:
    session_dir, _ = _write_session(tmp_path, seqs=(100, 101, 102))

    index_path = session_dir / layout.INDEX_NAME
    lines = index_path.read_text(encoding="utf-8").splitlines()
    # 2行目を意図的に壊す（閉じ括弧を欠いた不完全な JSON）。
    lines[1] = '{"i": 1, "seq": 101, "off": 24, "len": 24'
    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(RecordingFormatError):
        SessionReader(session_dir)


# -- 異常系: ブロブの位置改竄 ---------------------------------------------------


def test_tampered_blob_position_raises(tmp_path: Path) -> None:
    session_dir, _ = _write_session(tmp_path, seqs=(100, 101, 102))

    blob_size = (session_dir / layout.BLOB_NAME).stat().st_size
    index_path = session_dir / layout.INDEX_NAME
    lines = index_path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[-1])
    record["off"] = blob_size + 1000  # 実体のサイズを大きく超える位置に改竄する
    lines[-1] = json.dumps(record, ensure_ascii=False)
    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(RecordingFormatError):
        SessionReader(session_dir)


# -- 異常系: 展開後の長さ不一致（read 実行時に検出・伝播する） -------------------


def test_raw_len_mismatch_raises_on_read_not_on_construction(tmp_path: Path) -> None:
    session_dir, _ = _write_session(
        tmp_path, seqs=(100, 101, 102), tamper_raw_len_at=1
    )

    # off/len 自体は正しいので構築（＝索引全体の JSON 解析と位置検証）は成功する。
    reader = SessionReader(session_dir)

    # 改竄されていない先頭フレームは問題なく読める。
    reader.read(0)

    with pytest.raises(RecordingFormatError):
        reader.read(1)


def test_raw_len_mismatch_propagates_through_iter_frames_without_truncating(
    tmp_path: Path,
) -> None:
    """反復の途中で破損を検出した場合、黙って打ち切らず例外を伝播させる。"""
    session_dir, _ = _write_session(
        tmp_path, seqs=(100, 101, 102), tamper_raw_len_at=1
    )
    reader = SessionReader(session_dir)

    collected: list[CaptureFrame] = []
    with pytest.raises(RecordingFormatError):
        for frame in reader.iter_frames():
            collected.append(frame)

    # 破損位置より前のフレームは既に yield されているが、系列全体としては
    # 例外により打ち切られており、「3件そろった正常な結果」を装って
    # 呼び出し側へ返ってくることはない。
    assert len(collected) == 1


# -- 異常系: summary.json 不在（書きかけセッション） ----------------------------


def test_missing_summary_warns_and_reads_available_frames(tmp_path: Path) -> None:
    session_dir, depths = _write_session(
        tmp_path, seqs=(100, 101, 102), include_summary=False
    )

    with pytest.warns(InProgressSessionWarning):
        reader = SessionReader(session_dir)

    assert reader.summary is None
    assert len(reader) == 3

    frame0 = reader.read(0)
    assert np.array_equal(frame0.depth, depths[0])

    frames = list(reader.iter_frames())
    assert len(frames) == 3


# -- 正常系: zlib 圧縮の往復 ----------------------------------------------------


def test_compression_zlib_roundtrip(tmp_path: Path) -> None:
    session_dir, depths = _write_session(
        tmp_path, seqs=(200, 201), compression="zlib"
    )
    reader = SessionReader(session_dir)

    for i, depth in enumerate(depths):
        frame = reader.read(i)
        assert np.array_equal(frame.depth, depth)

    # 圧縮後のバイト列は明らかに raw と異なることを確認しておく
    # （手書きフィクスチャが実際に zlib 圧縮を経由していることの裏付け）。
    blob_bytes = (session_dir / layout.BLOB_NAME).read_bytes()
    assert blob_bytes != b"".join(d.tobytes() for d in depths)


# -- 正常系: depth の読み取り専用性 ---------------------------------------------


def test_depth_array_is_read_only(tmp_path: Path) -> None:
    session_dir, _ = _write_session(tmp_path, seqs=(100,))
    reader = SessionReader(session_dir)

    frame = reader.read(0)
    assert frame.depth.flags.writeable is False

    with pytest.raises(ValueError):
        frame.depth[0, 0] = 12345


# -- 副次的な確認: SessionRecorder（4.3）との統合往復 ---------------------------


def test_integration_roundtrip_with_session_recorder(tmp_path: Path) -> None:
    """`SessionRecorder` で実際に書き、`SessionReader` で読み戻す（副次的な確認）。

    主たる正しさの証拠は手書きフィクスチャによる上記の各テストであり、
    本テストはそれを補強するにとどまる（tasks.md 4.4 の設計意図）。
    """
    from sensing_foundation.types import CameraIntrinsics, StreamProfile

    intrinsics = CameraIntrinsics(
        width_px=WIDTH_PX,
        height_px=HEIGHT_PX,
        fx_px=60.0,
        fy_px=60.0,
        ppx_px=WIDTH_PX / 2.0,
        ppy_px=HEIGHT_PX / 2.0,
        model="brown_conrady",
        coeffs=(0.0, 0.0, 0.0, 0.0, 0.0),
    )
    profile = StreamProfile(
        width_px=WIDTH_PX,
        height_px=HEIGHT_PX,
        fps=FPS,
        depth_scale_mm=1.0,
        color_enabled=False,
        intrinsics=intrinsics,
    )

    session_id = "integration-session"
    recorder = SessionRecorder(
        tmp_path,
        session_id,
        profile,
        device=None,
        runtime={},
        source=SourceKind.SIMULATED,
    )

    written_frames = []
    for i, seq in enumerate((10, 11, 12)):
        depth = make_depth_frame(WIDTH_PX, HEIGHT_PX, seq)
        frame = CaptureFrame(
            index=i,
            seq=seq,
            t_capture_ms=float(i) * (1000.0 / FPS),
            device_timestamp_ms=None,
            timestamp_domain=TimestampDomain.UNKNOWN,
            capture_latency_ms=None,
            depth=depth,
            profile=profile,
            source=SourceKind.SIMULATED,
            dropped_before=0,
            gap_before=0,
        )
        recorder.write(frame)
        written_frames.append(frame)

    recorder.close(
        CaptureStats(
            frames_yielded=3,
            frames_dropped=0,
            frames_missing=0,
            duration_ms=100.0,
            measured_fps=30.0,
            acquire_errors=0,
        )
    )

    reader = SessionReader(layout.session_dir(tmp_path, session_id))
    assert len(reader) == 3
    assert reader.summary is not None

    read_back = list(reader.iter_frames())
    assert len(read_back) == 3
    for written, got in zip(written_frames, read_back):
        assert got.index == written.index
        assert got.seq == written.seq
        assert np.array_equal(got.depth, written.depth)
