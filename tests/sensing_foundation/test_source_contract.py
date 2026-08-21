"""3アダプタ共通の契約テストと `open_source()` に対するテスト（タスク 4.6）。

design.md「Integration Tests」の契約テスト定義をそのまま実装する:

    同一の合成フレーム列を `simulated` と `recorded`（一度記録してから
    再生）へ流し、`CaptureFrame` の系列が等価になること。live は実機タスク
    で同じテストを再実行する（要件 4.1, 4.2, 6.1）。

あわせて tasks.md 4.6 の観測可能な完了状態を固定する:

    合成入力で記録 → 再生した系列が、元の合成系列と時刻・番号・Depth 内容
    まで一致する

本ファイルの構成方針:

1. **`_consume()` が「下流のフレーム処理コード」の唯一の代表**である。
   このファイルのすべてのテストは、種別（simulated / recorded）を問わず
   この1関数だけを `FrameSource` に対して呼ぶ。2つの近い形のテスト関数を
   別々に書いて「同じに見える」だけの状態にしない——`_consume()` を
   両方から**同一の呼び出し**として使うことで、要件 4.2「下流のフレーム
   処理コードの変更を必要としない」を実際に検証する。
2. **`open_source()` が入力元構築の唯一の経路**である。
   `TestOpenSourceBuildsEquivalentAdaptersForSimulatedAndRecorded` 以降の
   テストはすべて `open_source()` 経由でアダプタを得る（先頭の
   `TestSimulatedAndRecordedAdaptersProduceEquivalentSeries` のみ、
   design.md の契約テスト定義が指す「アダプタそのものの等価性」を
   `open_source()` の実装詳細から切り離して固定するため、意図的に
   `SimulatedSource`/`RecordedSource` を直接構築する）。
3. **live（`RealSenseSource`、タスク 6.1）は意図的に保留する。**
   `TestLiveAdapterCaseIsDeferredToTask61` にその理由と、保留が
   `_consume()`/`_assert_series_equivalent()` という共有ヘルパの再利用だけで
   完了する（本ファイルの構造を書き換える必要が無い）ことを明記する。
   これは未完了を隠す `TODO` ではなく、ハードウェア依存という構造的な
   理由による意図的な延期であることを示す（tasks.md 4.6「実機アダプタは
   同テストの対象に含める形にしておき、実行はタスク 9.3 で行う」）。
4. **`t_capture_ms`（時刻）の扱い**は `_assert_series_equivalent()` の
   docstring と `TestReplayRelativeTimingFidelityUnderRealtimeSpeed` を
   参照。tasks.md の「時刻...まで一致する」という記述と、タスク 4.5 が
   既に固定した「`t_capture_ms` の絶対値は記録セッションと再生セッションで
   異なる `SessionClock` 起点になるため一致しない」という事実は、
   絶対値ではなく**相対間隔**（フレーム間の時刻差分）の一致として両立
   させる。これは弱めた妥協ではなく、`t_capture_ms` の定義
   （`timebase.py`: セッション単調時計起点の経過時間であり、セッションを
   またいだ絶対値比較はそもそも `to_wall_ms()` を経由すべきと明記される）
   そのものが要求する唯一の一貫した解釈である。

要件: 4.1, 4.2
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest
from synthetic import make_supplier

from sensing_foundation.config import RuntimeSettings
from sensing_foundation.errors import SensingConfigError
from sensing_foundation.metrics import CaptureMetrics
from sensing_foundation.obslog import NullLogger
from sensing_foundation.recording import layout
from sensing_foundation.recording.reader import SessionReader
from sensing_foundation.recording.writer import SessionRecorder
from sensing_foundation.source import FrameSource, open_source
from sensing_foundation.sources.recorded import RecordedSource
from sensing_foundation.sources.simulated import SimulatedSource
from sensing_foundation.timebase import SessionClock
from sensing_foundation.types import CaptureFrame, CaptureStats, SourceKind, StreamProfile

WIDTH_PX = 8
HEIGHT_PX = 6


# ----------------------------------------------------------------------------
# 共有ヘルパ
# ----------------------------------------------------------------------------


def _make_profile() -> StreamProfile:
    """SIMULATED 用の `StreamProfile`。`open_source()` の `_build_simulated_profile()`
    と同じ規約（`depth_scale_mm=1.0`・`intrinsics=None`）を手動で固定する
    （直接 `SimulatedSource` を構築するテストのため、`open_source()` の
    内部実装には依存しない）。
    """
    return StreamProfile(
        width_px=WIDTH_PX,
        height_px=HEIGHT_PX,
        fps=30,
        depth_scale_mm=1.0,
        color_enabled=False,
        intrinsics=None,
    )


def _make_metrics_and_clock(session_id: str) -> tuple[CaptureMetrics, SessionClock]:
    clock = SessionClock(session_id=session_id)
    metrics = CaptureMetrics(NullLogger(), clock, sysstat=None)
    return metrics, clock


def _consume(source: FrameSource) -> list[CaptureFrame]:
    """「下流のフレーム処理コード」を代表する、唯一の消費側関数（要件 4.2）。

    種別（simulated / recorded / 将来の live）を問わず、`FrameSource` 契約
    （`start()` → `frames()` → `stop()`）だけを使う。本ファイルの全テストは
    この関数を通じて `FrameSource` を消費し、種別ごとに別々の消費コードを
    書かない。
    """
    with source:
        return list(source.frames())


def _record_frames(
    tmp_path: Path,
    frames: list[CaptureFrame],
    profile: StreamProfile,
    stats: CaptureStats,
) -> Path:
    """`frames` をそのまま `SessionRecorder` で記録し、セッションディレクトリを返す。

    渡された `CaptureFrame` の内容（`seq`/`depth`/...）をそのまま
    `frames.ndjson`/`depth.bin` へ書く（`SessionRecorder.write()` は変換を
    行わない）。`stats` は記録元の `FrameSource.stats`（`stop()` 後に凍結
    された本物の値）を渡す——`writer.py` モジュール docstring が明示する
    「保険機構（`__exit__` の自動合成）に頼らず、明示的な `close(stats)` を
    必ず呼ぶこと」という方針に従う。
    """
    root = tmp_path / "sessions"
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
        for frame in frames:
            recorder.write(frame)
        recorder.close(stats)
    return layout.session_dir(root, session_id)


def _assert_series_equivalent(a: list[CaptureFrame], b: list[CaptureFrame]) -> None:
    """`t_capture_ms` と `source` を除く全フィールドで系列の等価性を比較する。

    2つの除外理由:

    - **`t_capture_ms`**: `RecordedSource` が返す `t_capture_ms` は元の
      記録セッションの値ではなく、**再生セッション自身の** `SessionClock`
      を起点に新たに算出される（`recorded.py` モジュール docstring
      「`t_capture_ms` の扱い」・タスク 4.5 の Implementation Notes を参照。
      `source.py` の `BaseFrameSource.frames()` が常に自分の
      `clock.now_ms()` を使う構造上、`_acquire()` の戻り値からの素通しは
      不可能）。異なる `SessionClock` インスタンス（異なるアンカ）の
      絶対値を比較することは `timebase.py` 自体が「セッションをまたぐ
      比較は `to_wall_ms()` を経由すること」と明記しており、素の `==` で
      比較することはそもそも `t_capture_ms` の定義に反する。相対間隔での
      検証は `TestReplayRelativeTimingFidelityUnderRealtimeSpeed` が別途
      固定する。
    - **`source`**: `CaptureFrame.source` はそのフレームを**生成した
      アダプタの種別**を正しく表す値であり（`SimulatedSource` なら
      `SourceKind.SIMULATED`、`RecordedSource` なら `SourceKind.RECORDED`）、
      両者が異なることこそが正しい——一致を要求すると「入力元を差し替えて
      も下流が気づかない」という要件 4.2 の主旨に反して入力元の区別が
      消えてしまう。

    `depth` は複数画素を持つと素の `==` が `ValueError` を送出するため
    `np.array_equal` を使う（`tests/sensing_foundation/test_reader.py` の
    `_assert_frames_equal()` / `test_recorded_source.py` の
    `_assert_frames_equal_ignoring_timing()` と同じ地雷を踏まないための
    ヘルパ。タスク 4.4 の Implementation Notes 参照）。
    """
    assert len(a) == len(b)
    for fa, fb in zip(a, b):
        assert fa.index == fb.index
        assert fa.seq == fb.seq
        assert fa.device_timestamp_ms == fb.device_timestamp_ms
        assert fa.timestamp_domain == fb.timestamp_domain
        assert fa.capture_latency_ms == fb.capture_latency_ms
        assert np.array_equal(fa.depth, fb.depth)
        assert fa.profile == fb.profile
        assert fa.dropped_before == fb.dropped_before
        assert fa.gap_before == fb.gap_before


# ----------------------------------------------------------------------------
# 契約テスト本体: アダプタそのものの等価性（design.md Integration Tests #1）
# ----------------------------------------------------------------------------


class TestSimulatedAndRecordedAdaptersProduceEquivalentSeries:
    """観測可能な完了状態: 合成入力で記録→再生した系列が、元の合成系列と
    番号・Depth 内容まで一致する（tasks.md 4.6）。

    `open_source()` の実装詳細から切り離し、`SimulatedSource` /
    `RecordedSource` を直接構築して比較する——design.md の契約テスト定義
    （「同一の合成フレーム列を simulated と recorded へ流し、系列が
    等価になること」）が指すのはアダプタそのものの契約であり、
    `open_source()` はその契約を満たすアダプタを組み立てる配線でしか
    ないため。

    **意図的に連続する `seqs`（欠番なし）を使う**: `SimulatedSource` は
    `seq` に供給関数の呼び出し通し番号を使い、供給内容に埋め込まれた
    `seq` を復元しない（`simulated.py` 「実装判断1」参照）ため、
    `make_supplier` へ非連続な `seqs`（例 `[0, 1, 3, 4]`）を渡しても
    `SimulatedSource` 越しに観測される `seq` は常に連番になる。
    したがって「同一の合成フレーム列を両アダプタへ流し、系列が等価に
    なる」ことを検証する本テストで非連続な `seqs` を使うと、比較対象の
    片方（simulated 側）だけが供給内容と食い違った `seq` を返すことになり、
    「等価性」の検証にならない。`seq` の欠落検出（`gap_before` の値そのもの
    が正しく計算されるか）は `BaseFrameSource` レベルで既にタスク 3.1
    （`test_source.py`）が固定しており、`RecordedSource` 単体の `seq` 忠実性
    はタスク 4.5（`test_recorded_source.py`）が既に固定している。本クラスの
    責務はそれらとは別で、あくまで「両アダプタが**同じ入力**に対して
    **同じ出力**を返すか」であるため、両アダプタの `seq` 生成規約が
    自然に一致する連続列を選ぶのが正しい設計である。
    """

    def test_replayed_series_matches_original_simulated_series(
        self, tmp_path: Path
    ) -> None:
        seqs = [0, 1, 2, 3, 4]
        supplier = make_supplier(WIDTH_PX, HEIGHT_PX, seqs)
        profile = _make_profile()

        sim_metrics, sim_clock = _make_metrics_and_clock("contract-simulated")
        sim_source = SimulatedSource(supplier, profile, sim_metrics, clock=sim_clock)
        original_frames = _consume(sim_source)  # 同じ消費側関数

        assert [f.seq for f in original_frames] == seqs

        session_dir = _record_frames(tmp_path, original_frames, profile, sim_source.stats)

        reader = SessionReader(session_dir)
        replay_metrics, replay_clock = _make_metrics_and_clock("contract-recorded")
        recorded_source = RecordedSource(reader, replay_metrics, clock=replay_clock)
        replayed_frames = _consume(recorded_source)  # 同じ消費側関数

        _assert_series_equivalent(original_frames, replayed_frames)

    def test_empty_series_round_trips_to_empty_series(self, tmp_path: Path) -> None:
        supplier = make_supplier(WIDTH_PX, HEIGHT_PX, [])
        profile = _make_profile()

        sim_metrics, sim_clock = _make_metrics_and_clock("contract-simulated-empty")
        sim_source = SimulatedSource(supplier, profile, sim_metrics, clock=sim_clock)
        original_frames = _consume(sim_source)
        assert original_frames == []

        session_dir = _record_frames(tmp_path, original_frames, profile, sim_source.stats)

        reader = SessionReader(session_dir)
        replay_metrics, replay_clock = _make_metrics_and_clock("contract-recorded-empty")
        recorded_source = RecordedSource(reader, replay_metrics, clock=replay_clock)
        replayed_frames = _consume(recorded_source)

        assert replayed_frames == []


# ----------------------------------------------------------------------------
# open_source(): 唯一の生成口としての契約（要件 4.1, 4.2, 4.6）
# ----------------------------------------------------------------------------


class TestOpenSourceBuildsEquivalentAdaptersForSimulatedAndRecorded:
    """観測可能な完了状態: `open_source()` が種別ごとに正しいアダプタを
    構築し、`_consume()`（下流のフレーム処理コードの代表）を変更せずに
    入力元を差し替えられる（要件 4.2, 4.6）。

    このクラス以降のテストは `SimulatedSource`/`RecordedSource` を一度も
    直接構築しない——`open_source()` だけがアダプタを組み立てる唯一の経路
    であることを、コード上でも徹底する。
    """

    def test_same_consumer_function_handles_simulated_and_recorded(
        self, tmp_path: Path
    ) -> None:
        seqs = [10, 11, 12, 13]
        supplier = make_supplier(WIDTH_PX, HEIGHT_PX, seqs)

        sim_metrics, sim_clock = _make_metrics_and_clock("open-source-simulated")
        sim_settings = RuntimeSettings.resolve(
            file=None,
            env={},
            overrides={
                "source": "simulated",
                "width_px": WIDTH_PX,
                "height_px": HEIGHT_PX,
            },
        )
        sim_source = open_source(
            sim_settings, sim_metrics, clock=sim_clock, supplier=supplier
        )
        assert sim_source.kind == SourceKind.SIMULATED

        original_frames = _consume(sim_source)  # 同じ消費側関数

        session_dir = _record_frames(
            tmp_path, original_frames, sim_source.profile, sim_source.stats
        )

        replay_metrics, replay_clock = _make_metrics_and_clock("open-source-recorded")
        recorded_settings = RuntimeSettings.resolve(
            file=None,
            env={},
            overrides={"source": "recorded", "session_path": str(session_dir)},
        )
        recorded_source = open_source(recorded_settings, replay_metrics, clock=replay_clock)
        assert recorded_source.kind == SourceKind.RECORDED

        replayed_frames = _consume(recorded_source)  # 同じ消費側関数

        _assert_series_equivalent(original_frames, replayed_frames)

    def test_recorded_speed_parameter_passes_through(self, tmp_path: Path) -> None:
        """`speed` が `open_source()` から `RecordedSource` へ素通しされる
        （要件 6.5。`open_source()` を経由しても再生速度を選べることを
        固定する——さもないと CLI が `open_source()` を迂回して
        `RecordedSource` を直接構築せざるを得なくなり、「唯一の生成口」の
        原則が崩れる）。
        """
        seqs = [0, 1, 2]
        supplier = make_supplier(WIDTH_PX, HEIGHT_PX, seqs)
        profile = _make_profile()

        sim_metrics, sim_clock = _make_metrics_and_clock("speed-passthrough-sim")
        sim_source = SimulatedSource(supplier, profile, sim_metrics, clock=sim_clock)
        original_frames = _consume(sim_source)

        session_dir = _record_frames(tmp_path, original_frames, profile, sim_source.stats)

        replay_metrics, replay_clock = _make_metrics_and_clock("speed-passthrough-recorded")
        settings = RuntimeSettings.resolve(
            file=None,
            env={},
            overrides={"source": "recorded", "session_path": str(session_dir)},
        )
        recorded_source = open_source(
            settings, replay_metrics, clock=replay_clock, speed="realtime"
        )

        replayed_frames = _consume(recorded_source)
        _assert_series_equivalent(original_frames, replayed_frames)


class TestOpenSourceRejectsMisconfiguration:
    """観測可能な完了状態: 呼び出し方の誤りは起動時に `SensingConfigError` で拒否する。"""

    def test_simulated_without_supplier_raises(self) -> None:
        settings = RuntimeSettings(source=SourceKind.SIMULATED)
        metrics, clock = _make_metrics_and_clock("err-simulated-no-supplier")

        with pytest.raises(SensingConfigError):
            open_source(settings, metrics, clock=clock)

    def test_recorded_without_session_path_raises(self) -> None:
        # RuntimeSettings.resolve() ならここへ到達する前に拒否されるが、
        # resolve() を経ずに直接構築した RuntimeSettings（config.py の
        # docstring が許容する使い方）はこの検証を経ないため、
        # open_source() 自身も同じ検証を独自に行うことを固定する。
        settings = RuntimeSettings(source=SourceKind.RECORDED, session_path=None)
        metrics, clock = _make_metrics_and_clock("err-recorded-no-path")

        with pytest.raises(SensingConfigError):
            open_source(settings, metrics, clock=clock)


class TestLiveAdapterCaseIsDeferredToTask61:
    """`live`（`RealSenseSource`）の**内容比較**（3アダプタ共通の系列一致）は
    タスク 9.3（実機必須）まで引き続き保留する。

    tasks.md 4.6「実機アダプタは同テストの対象に含める形にしておき、実行は
    タスク 9.3 で行う」に対応する。第3の契約テストケース
    （`open_source(settings_with_live, ...)` で得た `FrameSource` を
    `_consume()` へ渡し、`_assert_series_equivalent()` で simulated/recorded
    と比較する）は、RealSense の実機が無ければ実行できないため、意図的に
    延期する——これは書き忘れではなく、ハードウェア依存という構造的な理由
    による意図的な延期である。**タスク 6.1 の完了により
    `sensing_foundation.sources.realsense.RealSenseSource` 自体は存在する
    ようになった**（`open_source()` の LIVE 分岐が遅延 import する形に既に
    配線済み。`source.py` の `open_source()` docstring「LIVE の場合」参照）
    が、本 WSL 環境には `pyrealsense2` も実機も無いため、3アダプタ共通の
    内容比較はタスク 9.3（Pi 4 + D435 実機）まで実行できないままである:

        def test_live_case(self, ...):  # タスク 9.3 で有効化する
            settings = RuntimeSettings(source=SourceKind.LIVE, ...)
            metrics, clock = _make_metrics_and_clock(...)
            live_source = open_source(settings, metrics, clock=clock)
            live_frames = _consume(live_source)
            _assert_series_equivalent(reference_frames, live_frames)

    本テストクラス自体は、その延期が「壊れて動かない」のではなく
    「意図的に、SDK/実機なしで安全に検出可能な形で保留されている」ことを
    固定する: `open_source()` は LIVE 分岐へ正しくディスパッチし、
    `RealSenseSource` の構築自体には成功する（タスク 6.1 完了により
    モジュール・クラスが存在するため）。`pyrealsense2` の実際の import は
    `RealSenseSource.start()` まで遅延される設計（タスク 6.1）のため、
    構築ではなく `_consume()`（`with source:` 経由で `start()` を呼ぶ）の
    時点で `SourceUnavailableError`（`sensing_foundation.errors`、
    `pyrealsense2` を import できないことを検出して変換した専用の例外。
    「次に何を確認すべきか」の案内文つき）を送出する——`ModuleNotFoundError`
    そのものではなく、他の例外や無限ハングにもならない。
    """

    def test_live_branch_constructs_but_fails_at_start_without_sdk(self) -> None:
        settings = RuntimeSettings(source=SourceKind.LIVE)
        metrics, clock = _make_metrics_and_clock("live-deferred")

        live_source = open_source(settings, metrics, clock=clock)

        from sensing_foundation.errors import SourceUnavailableError

        with pytest.raises(SourceUnavailableError):
            _consume(live_source)


# ----------------------------------------------------------------------------
# 「時刻...まで一致する」（tasks.md 4.6）の解釈: 相対間隔の再現
# ----------------------------------------------------------------------------


class TestReplayRelativeTimingFidelityUnderRealtimeSpeed:
    """tasks.md 4.6 の観測可能な完了状態「...時刻...まで一致する」を、
    `t_capture_ms` の**絶対値**ではなく**フレーム間の相対間隔**の再現として
    検証する。

    `t_capture_ms` の絶対値は記録セッションと再生セッションで異なる
    `SessionClock`（異なるアンカ）から算出されるため、そもそも構造的に
    一致し得ない（`_assert_series_equivalent()` の docstring・
    `recorded.py` モジュール docstring「`t_capture_ms` の扱い」参照）。
    一方、`speed="realtime"` は記録時のフレーム間隔（`t_capture_ms` の差分）
    だけ実際に待ってから返すため（`RecordedSource._acquire()`）、再生
    セッション内でのフレーム間隔は記録時の間隔を実測レベルで近似的に再現
    する。本テストはこれを「時刻...まで一致する」の妥当な解釈として固定し、
    弱めた妥協ではなく `t_capture_ms` の定義そのものが要求する唯一の
    一貫した比較軸であることを示す。
    """

    def test_realtime_replay_via_open_source_reproduces_relative_frame_intervals(
        self, tmp_path: Path
    ) -> None:
        seqs = [0, 1, 2, 3]
        interval_s = 0.03  # 供給間隔（30ms）。実時間 sleep を使うが短く保つ。
        supplier = make_supplier(WIDTH_PX, HEIGHT_PX, seqs, delay_s=interval_s)
        profile = _make_profile()

        sim_metrics, sim_clock = _make_metrics_and_clock("timing-simulated")
        sim_source = SimulatedSource(supplier, profile, sim_metrics, clock=sim_clock)
        original_frames = _consume(sim_source)

        session_dir = _record_frames(tmp_path, original_frames, profile, sim_source.stats)

        replay_metrics, replay_clock = _make_metrics_and_clock("timing-recorded")
        settings = RuntimeSettings.resolve(
            file=None,
            env={},
            overrides={"source": "recorded", "session_path": str(session_dir)},
        )
        recorded_source = open_source(
            settings, replay_metrics, clock=replay_clock, speed="realtime"
        )
        replayed_frames = _consume(recorded_source)

        # 内容（seq/depth/...）は speed に関わらず完全一致する
        # （既に task 4.5 が固定済みの Invariant。ここでも念のため確認する）。
        for original, replayed in zip(original_frames, replayed_frames):
            assert original.seq == replayed.seq
            assert np.array_equal(original.depth, replayed.depth)

        original_deltas = [
            b.t_capture_ms - a.t_capture_ms
            for a, b in zip(original_frames, original_frames[1:])
        ]
        replayed_deltas = [
            b.t_capture_ms - a.t_capture_ms
            for a, b in zip(replayed_frames, replayed_frames[1:])
        ]

        assert len(original_deltas) == len(replayed_deltas) == len(seqs) - 1
        for original_delta, replayed_delta in zip(original_deltas, replayed_deltas):
            # 実測ゆえのスケジューリングジッタを許容するため、絶対マージン
            # （供給間隔の 2/3 程度）で判定する
            # （`test_recorded_source.py` の `TestRealtimeIsMeasurablyLongerThanFast`
            # と同じ「実測は近似でしか検証できない」という前提を踏襲する）。
            assert replayed_delta == pytest.approx(
                original_delta, abs=interval_s * 1000 * 0.66
            )
