"""実機で記録したセッションを WSL で再生する往復の検証（タスク 9.6）。

タスク 9.6 の観測可能な完了状態を固定する:

    Pi で記録したセッションを WSL 上で2回再生して系列が一致し、
    対応付けたセッション識別子から記録側のフレーム範囲を引ける

**本ファイルが検証するのは「実データ」に対する往復である。** 合成データを
記録 → 再生する往復は `test_source_contract.py`（タスク 4.6）が既に固定して
おり、本ファイルはそれと重複しない。ここでの新規性は次の2点に尽きる:

1. **記録元が実機である**こと（`manifest.json` の `source` が `live`）。
   合成入力で作った記録に対して同じ検証を全部通しても 9.6 の主張には
   ならないため、実機由来であることを最初に表明で確かめ、そうでなければ
   即座に失敗する。
2. **記録した機械と再生する機械が違う**こと。Pi（SDK 有り・実機接続）で
   書いたバイト列を、WSL（SDK 無し・実機無し）が読み直す（要件 6.3）。

## 実データの与え方（版管理しない生データの扱い）

セッション記録は生データであり版管理しない（tasks.md 9 節の前書き）。
そのため本ファイルのテストは環境変数 `SENSING_REAL_SESSION_DIR` に
セッションディレクトリのパスが与えられたときだけ実行し、与えられなければ
skip する。タスク 9.3 が live 実機の有無で `skipif` した構造と同じ考え方で
あり、**実データが無い環境（他の開発機・実機を外した状態）でテストスイートを
赤くしない**。

    SENSING_REAL_SESSION_DIR=var/real-sessions/<session-id> \
        uv run --extra sensing pytest tests/sensing_foundation/test_real_session_roundtrip.py

## 検証を「同じ経路」で行わないこと（タスク 9.3 の申し送り）

`RecordedSource` は内部で `SessionReader` を使う。したがって再生結果を
`SessionReader` で読み直して突き合わせても、同じコードが同じ答えを返した
だけであり、記録の忠実性の検証にならない。本ファイルは**期待値を
`frames.ndjson` / `depth.bin` / `manifest.json` から素の `json` と
`zlib` で独立に組み立て直し**、再生結果と突き合わせる（`sensing_foundation`
の読み出しコードを一切通さない参照実装）。これにより「記録側と読み出し側の
対称なバグが互いを隠す」事態を避ける（タスク 4.4 が手書きフィクスチャを
選んだのと同じ理由）。

## フレーム系列を配列へ溜めないこと

実機の記録は 640x480@60 で数百〜数千枚に達する（本タスクで用いたのは 900 枚）。
1枚 614,400 バイトなので、900 枚を `list` へ溜めると 553MB、2回の再生を
同時に保持すると 1.1GB になる。本ファイルの比較はすべて**2つの反復を
同時に進める**（`zip` によるロックステップ）形で書き、系列全体を実体化
しない。参照実装の側も `depth.bin` を索引行ごとに開き直さず、**1つの
ファイルハンドルを `seek` で使い回す**。

要件: 5.1, 6.1, 6.2, 6.3, 7.7
"""

from __future__ import annotations

import json
import os
import zlib
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from itertools import islice
from pathlib import Path
from typing import BinaryIO

import numpy as np
import pytest
from prediction_core import PredictionConfig, Sample, ThrowRecord

from sensing_foundation.config import RuntimeSettings
from sensing_foundation.metrics import CaptureMetrics
from sensing_foundation.obslog import NullLogger
from sensing_foundation.recording import layout
from sensing_foundation.recording.reader import SessionReader
from sensing_foundation.source import open_source
from sensing_foundation.throw_store import ThrowRecordStore, link_to_session
from sensing_foundation.timebase import SessionClock
from sensing_foundation.types import CaptureFrame, SourceKind

#: 実機で記録したセッションディレクトリを指す環境変数。
REAL_SESSION_ENV = "SENSING_REAL_SESSION_DIR"

_RAW_SESSION_DIR = os.environ.get(REAL_SESSION_ENV)

requires_real_session = pytest.mark.skipif(
    not _RAW_SESSION_DIR,
    reason=(
        f"実機で記録したセッションが必要（タスク 9.6）。"
        f"{REAL_SESSION_ENV} にセッションディレクトリを指定すると実行される"
    ),
)


# ----------------------------------------------------------------------------
# フィクスチャ
# ----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def session_dir() -> Path:
    """`SENSING_REAL_SESSION_DIR` が指すセッションディレクトリ。

    `skipif` を通過した時点で環境変数は非空である（`requires_real_session`）。
    パスが存在しない・必要なファイルが揃わない場合は skip ではなく**失敗**
    させる——指定されたのに読めないのは「データが無い環境」ではなく設定の
    誤りであり、静かに skip すると 9.6 の検証を実行しないまま緑になる。
    """
    assert _RAW_SESSION_DIR is not None
    path = Path(_RAW_SESSION_DIR)
    assert path.is_dir(), f"{REAL_SESSION_ENV} が指すディレクトリが無い: {path}"
    for name in (layout.MANIFEST_NAME, layout.INDEX_NAME, layout.BLOB_NAME):
        assert (path / name).is_file(), f"セッション記録に {name} が無い: {path}"
    return path


@pytest.fixture(scope="module")
def manifest(session_dir: Path) -> dict:
    """`manifest.json` を素の `json` で読む（`SessionReader` を通さない参照値）。"""
    return json.loads((session_dir / layout.MANIFEST_NAME).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def index_rows(session_dir: Path) -> list[dict]:
    """`frames.ndjson` を素の `json` で1行ずつ読む（同上）。

    索引そのものは 900 行で 218KB 程度なので、これは実体化してよい
    （実体化を避けるのは `depth` を持つフレーム系列の側である）。
    """
    text = (session_dir / layout.INDEX_NAME).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


# ----------------------------------------------------------------------------
# 参照実装（`sensing_foundation` の読み出しコードを通さない）
# ----------------------------------------------------------------------------


def _reference_depth(blob: BinaryIO, manifest: dict, row: dict) -> np.ndarray:
    """索引行1件から Depth 配列を**独立に**再構成する参照実装。

    `SessionReader._reconstruct_depth()` と同じ結果を出すべきだが、実装は
    共有しない（共有すると同じバグを同じように再現してしまい、突き合わせが
    意味を失う）。`depth.bin` を `off`/`len` で直接切り出し、圧縮時のみ
    `zlib` で展開して `manifest["blob"]` の dtype・形状へ組み直す。

    `blob` は**開いたままのファイルハンドル**を受け取る。索引行ごとに
    `read_bytes()` でファイル全体（本タスクでは 124MB）を読み直すと、
    900 行に対して 100GB 超の読み出しになるため（モジュール docstring
    「フレーム系列を配列へ溜めないこと」）。
    """
    blob_meta = manifest["blob"]
    blob.seek(row["off"])
    payload = blob.read(row["len"])
    assert len(payload) == row["len"]
    if blob_meta["compression"] == "zlib":
        payload = zlib.decompress(payload)
    assert len(payload) == row["raw_len"]
    dtype = np.dtype(blob_meta["dtype"]).newbyteorder(
        "<" if blob_meta["little_endian"] else ">"
    )
    profile = manifest["profile"]
    return np.frombuffer(payload, dtype=dtype).reshape(
        profile["height_px"], profile["width_px"]
    )


# ----------------------------------------------------------------------------
# 再生（系列を実体化しない）
# ----------------------------------------------------------------------------


@contextmanager
def _replay(session_dir: Path, session_id: str) -> Iterator[Iterator[CaptureFrame]]:
    """セッション記録を1回再生し、フレームの**反復子**を渡す。

    `open_source()`（入力元構築の唯一の経路）を通し、`FrameSource` 契約
    だけを使う——再生専用の別経路を用意すると「live と同じ経路で扱える」
    という要件 6.4 の主張が崩れる。

    系列を `list` にせず反復子のまま渡すのは、`depth` を持つフレームを
    まとめて保持するとメモリを食い潰すためである（モジュール docstring）。
    """
    clock = SessionClock(session_id=session_id)
    metrics = CaptureMetrics(NullLogger(), clock, sysstat=None)
    settings = RuntimeSettings(source=SourceKind.RECORDED, session_path=session_dir)
    source = open_source(settings, metrics, clock=clock)
    with source:
        yield source.frames()


def _assert_frames_equivalent(fa: CaptureFrame, fb: CaptureFrame) -> None:
    """`t_capture_ms` を除く全フィールドで2枚のフレームを比較する。

    `t_capture_ms` の除外理由は `test_source_contract.py` の
    `_assert_series_equivalent()` docstring と `recorded.py` モジュール
    docstring「`t_capture_ms` の扱い」に詳しい——`RecordedSource` が返す
    `t_capture_ms` は**再生セッション自身の** `SessionClock` を起点に
    算出されるため、別々の再生セッション間で絶対値が一致しないのが正しい
    挙動である。

    ここでは比較対象が2回の**再生**であり `source` はどちらも
    `SourceKind.RECORDED` になるため、`source` は除外しない
    （`test_source_contract.py` 側が `source` を除外していたのは、合成系列と
    再生系列という**種別の異なるもの**を比べていたためである）。

    `depth` は複数画素を持つと素の `==` が `ValueError` を送出するため
    `np.array_equal` を使う（タスク 4.4 の Implementation Notes）。
    """
    assert fa.index == fb.index
    assert fa.seq == fb.seq
    assert fa.source == fb.source
    assert fa.device_timestamp_ms == fb.device_timestamp_ms
    assert fa.timestamp_domain == fb.timestamp_domain
    assert fa.capture_latency_ms == fb.capture_latency_ms
    assert np.array_equal(fa.depth, fb.depth)
    assert fa.profile == fb.profile
    assert fa.dropped_before == fb.dropped_before
    assert fa.gap_before == fb.gap_before


def _zip_exact(
    a: Iterable[CaptureFrame], b: Iterable[CaptureFrame]
) -> Iterator[tuple[CaptureFrame, CaptureFrame]]:
    """2つの反復子をロックステップで進め、**長さの相違を失敗として検出する**。

    素の `zip()` は短い方で黙って打ち切るため、片方が途中で尽きても
    「全件一致した」ように見えてしまう。`strict=True` はそれを例外に
    するが、`zip` の例外は `pytest` の assert ではないため、意図を
    明示するために本ヘルパへ包む。
    """
    return zip(a, b, strict=True)


# ----------------------------------------------------------------------------
# 前提: そもそも実機由来の記録か
# ----------------------------------------------------------------------------


@requires_real_session
class TestRecordingActuallyCameFromLiveHardware:
    """記録が実機由来であることを最初に確かめる（タスク 9.6「実データ」）。

    `manifest.json` は記録元の種別を保持しており（タスク 4.3 で `source` を
    必須引数にしたのはこのためである）、ここで実機由来を表明することが、
    以降の全テストを「実データに対する検証」たらしめる前提になる。
    """

    def test_manifest_records_live_as_the_source(self, manifest: dict) -> None:
        assert manifest["source"] == str(SourceKind.LIVE)

    def test_manifest_carries_real_camera_intrinsics(self, manifest: dict) -> None:
        """実機の記録には内部パラメータが載っている（合成入力では `None`）。

        `open_source()` の合成用 `StreamProfile` は `intrinsics=None` を使う
        規約であるため（タスク 4.6 の Implementation Notes）、非 `None` かつ
        焦点距離が正であることは記録元が実機だったことの独立した傍証になる。
        """
        intrinsics = manifest["intrinsics"]
        assert intrinsics is not None
        assert intrinsics["fx_px"] > 0.0
        assert intrinsics["fy_px"] > 0.0

    def test_recording_is_not_empty(self, index_rows: list[dict]) -> None:
        """記録が空でない（0枚の記録に対しては以降の等価性表明が恒真になる）。"""
        assert len(index_rows) > 0


# ----------------------------------------------------------------------------
# 要件 6.2: 同一記録を複数回再生して同一系列になる
# ----------------------------------------------------------------------------


@requires_real_session
class TestRepeatedReplayOfRealSessionYieldsIdenticalSeries:
    """観測可能な完了状態の前半: WSL 上で2回再生して系列が一致する（要件 6.2）。"""

    def test_two_replays_produce_equivalent_series(
        self, session_dir: Path, index_rows: list[dict]
    ) -> None:
        compared = 0
        with _replay(session_dir, "replay-1") as first, _replay(
            session_dir, "replay-2"
        ) as second:
            for fa, fb in _zip_exact(first, second):
                _assert_frames_equivalent(fa, fb)
                compared += 1
        # 比較した枚数が索引の行数と一致することまで確かめる——両方の再生が
        # 同じ位置で早期に尽きた場合、上のループは「一致した」まま 0 件でも
        # 通ってしまう。
        assert compared == len(index_rows)


# ----------------------------------------------------------------------------
# 要件 6.1 / 6.4: 記録時と同じ系列・同じメタ情報が返る
# ----------------------------------------------------------------------------


@requires_real_session
class TestReplayReproducesWhatWasWrittenOnThePi:
    """再生結果が Pi の書いたバイト列と一致する（要件 6.1）。

    期待値は `frames.ndjson` / `depth.bin` から素の `json` と `zlib` で
    組み立てる（モジュール docstring「検証を『同じ経路』で行わないこと」）。
    """

    def test_replayed_metadata_matches_index_rows(
        self, session_dir: Path, index_rows: list[dict]
    ) -> None:
        """通し番号・フレーム番号・デバイス側時刻・破棄／欠落件数が索引と一致する。"""
        seen = 0
        with _replay(session_dir, "replay-meta") as frames:
            for frame, row in zip(frames, index_rows, strict=True):
                assert frame.index == row["i"]
                assert frame.seq == row["seq"]
                assert frame.device_timestamp_ms == row["device_ts_ms"]
                assert str(frame.timestamp_domain) == row["ts_domain"]
                assert frame.capture_latency_ms == row["capture_latency_ms"]
                assert frame.dropped_before == row["dropped_before"]
                assert frame.gap_before == row["gap_before"]
                seen += 1
        assert seen == len(index_rows)

    def test_replayed_depth_matches_blob_reconstructed_independently(
        self, session_dir: Path, manifest: dict, index_rows: list[dict]
    ) -> None:
        """Depth の中身が `depth.bin` の該当区間と一致する（圧縮時は展開して比較）。"""
        seen = 0
        with (session_dir / layout.BLOB_NAME).open("rb") as blob:
            with _replay(session_dir, "replay-depth") as frames:
                for frame, row in zip(frames, index_rows, strict=True):
                    expected = _reference_depth(blob, manifest, row)
                    assert np.array_equal(frame.depth, expected)
                    seen += 1
        assert seen == len(index_rows)

    def test_depth_contains_more_than_one_distinct_value(
        self, session_dir: Path
    ) -> None:
        """Depth が一様な値で埋まっていない（実際に測距した中身があること）。

        全画素が同一値（例えば全 0 = 測距不能）の記録でも、上の各表明は
        すべて通ってしまう。実データを扱っていることの最低限の非空性検査
        として、少なくとも1枚に複数の異なる値が現れることを求める。
        """
        with _replay(session_dir, "replay-content") as frames:
            assert any(np.unique(frame.depth).size > 1 for frame in frames)

    def test_profile_is_available_through_the_same_path_as_live(
        self, session_dir: Path, manifest: dict
    ) -> None:
        """解像度・fps・Depth スケール・内部パラメータを live と同じ経路で得る（要件 6.4）。

        `FrameSource.profile` は live / recorded / simulated に共通の契約で
        あり、再生専用の取り出し口を使わない。
        """
        clock = SessionClock(session_id="replay-profile")
        metrics = CaptureMetrics(NullLogger(), clock, sysstat=None)
        settings = RuntimeSettings(source=SourceKind.RECORDED, session_path=session_dir)
        source = open_source(settings, metrics, clock=clock)
        with source:
            profile = source.profile

        expected = manifest["profile"]
        assert profile.width_px == expected["width_px"]
        assert profile.height_px == expected["height_px"]
        assert profile.fps == expected["fps"]
        assert profile.depth_scale_mm == expected["depth_scale_mm"]
        assert profile.color_enabled == expected["color_enabled"]

        assert profile.intrinsics is not None
        assert profile.intrinsics.fx_px == manifest["intrinsics"]["fx_px"]
        assert profile.intrinsics.ppx_px == manifest["intrinsics"]["ppx_px"]


# ----------------------------------------------------------------------------
# 要件 7.7: セッション識別子で Throw Record と記録を結び付ける
# ----------------------------------------------------------------------------


def _throw_record(record_id: str) -> ThrowRecord:
    """対応付けの検証に使う最小の `ThrowRecord`。

    サンプル内容は本タスクの検証対象ではない（サンプル層の中身は
    `flying-object-tracking` の責務）。ここで確かめたいのは
    `extra["sensing"]` の往復と、そこから記録側を引けることだけである。
    """
    return ThrowRecord(
        record_id=record_id,
        source=SourceKind.LIVE,
        config=PredictionConfig(),
        samples=(Sample(t_ms=0.0, x_mm=0.0, y_mm=0.0, z_mm=500.0),),
        predictions=(),
    )


@requires_real_session
class TestThrowRecordLinksBackToTheRecordedFrameRange:
    """観測可能な完了状態の後半: 対応付けたセッション識別子から
    記録側のフレーム範囲を引ける（要件 7.7）。
    """

    def test_linked_record_round_trips_and_resolves_to_the_frame_range(
        self,
        session_dir: Path,
        manifest: dict,
        index_rows: list[dict],
        tmp_path: Path,
    ) -> None:
        session_id = manifest["session_id"]
        # 記録の中ほどを「1投擲ぶん」の範囲に見立てる（両端を含む閉区間）。
        frame_index_from = len(index_rows) // 4
        frame_index_to = len(index_rows) // 2
        assert frame_index_from < frame_index_to

        store = ThrowRecordStore(tmp_path / "throws.ndjson")
        store.append(
            link_to_session(
                _throw_record("throw-9-6"),
                session_id,
                frame_index_from,
                frame_index_to,
            )
        )

        # --- 読み戻し（保存→復元の往復で対応付けが失われないこと） ---
        (restored,) = list(store.iter_records())
        link = restored.extra["sensing"]
        assert link["session_id"] == session_id
        assert link["frame_index_from"] == frame_index_from
        assert link["frame_index_to"] == frame_index_to

        # --- 対応付けから記録側を引く ---
        # セッション識別子はディレクトリ名の規約でもあるため、識別子だけで
        # 記録側に到達できる（`layout.session_dir()` が唯一の規約点）。
        resolved_dir = layout.session_dir(session_dir.parent, link["session_id"])
        assert resolved_dir == session_dir

        # 引いた範囲が記録側の正しい区間を指していることを、**独立な参照**
        # （索引行 + ブロブ）と突き合わせて確かめる。
        #
        # ここで `SessionReader.read(i)` の結果と再生結果を比べても、
        # `RecordedSource` は内部で `SessionReader` を使うため同じコードが
        # 同じ答えを返しただけになる（モジュール docstring「検証を『同じ経路』
        # で行わないこと」）。範囲がずれていないことの証拠として意味を持つのは、
        # 再生の当該区間が **`frames.ndjson` のその行番号の内容**と一致する
        # ことである。
        # ⚠️ 前提を明示する: 本記録はリングが1枚も追い出していない
        # （取得時間 = リング長）ため、「索引ファイルの行位置」と「記録
        # セッション側の通し番号 `i`」がたまたま一致している。
        #
        # リングが実際に追い出した記録では両者は食い違う（タスク 9.6 で実測:
        # 181 枚取得して直近 60 枚を保存した記録では、行位置 0 の `i` が 121）。
        # `SessionReader.read(i)` の `i` は**行位置**、`RecordedSource` が返す
        # `CaptureFrame.index` は**再生セッションの 0 始まり通し番号**、
        # 索引行の `i` は**記録セッションの通し番号**であり、3つ目だけが
        # ずれる。`frame_index_from`/`frame_index_to` がこのどれを指すのかは
        # design.md でも requirements.md でも定義されていない（未決の設計課題。
        # measurements.md タスク9.6「発見した設計上の欠陥」を参照）。
        assert index_rows[0]["i"] == 0, (
            "以下の表明は行位置と記録側通し番号の一致を前提にしている。"
            "リングが追い出した記録ではこの前提が崩れる（未決の設計課題）。"
        )

        span = range(link["frame_index_from"], link["frame_index_to"] + 1)
        checked = 0
        with (session_dir / layout.BLOB_NAME).open("rb") as blob:
            with _replay(session_dir, "replay-linked") as frames:
                window = islice(frames, frame_index_from, frame_index_to + 1)
                for i, replayed in zip(span, window, strict=True):
                    row = index_rows[i]
                    assert replayed.index == row["i"] == i
                    assert replayed.seq == row["seq"]
                    assert np.array_equal(
                        replayed.depth, _reference_depth(blob, manifest, row)
                    )
                    checked += 1
        assert checked == frame_index_to - frame_index_from + 1

        # 同じ範囲を `SessionReader` からも引ける（対応付けの利用側が
        # 再生を回さずに該当フレームだけ取り出す経路。要件 7.7）。
        reader = SessionReader(resolved_dir)
        assert [reader.read(i).index for i in span] == list(span)
