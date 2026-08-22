"""`pipeline.track_source()`（タスク 6.2 上流のフレーム供給との結線）の検証。

design.md「L6-L7: 追跡と統合 → TrackingPipeline / track_source」と要件
1.1, 1.2, 1.5, 1.6, 1.7 が定める、タスク本文（verbatim）の観測可能な完了
状態を固定する:

- 上流のフレーム反復（`source.frames()`）を唯一の入力経路とし、入力元の
  種別で処理を分岐させない
- 上流の公開された入口（`sensing_foundation` トップレベル）からのみ参照する
- 反復を途中で捨てられても上流の `stop()` が呼ばれる
- テスト用のフレーム供給と、上流の記録再生に相当する供給の双方から、
  同一内容の入力に対して同一の点列が得られる

## クロスソース等価性テストの設計判断（重要）

タスクブリーフは「`SourceKind.SIMULATED` を `sensing_foundation.
open_source(..., supplier=...)` 経由で構築したものと、`SourceKind.RECORDED`
を `open_source()` 経由で構築したものの両方を比較する」ことを示唆している
が、実装を確認した結果これは成立しない: `sensing_foundation.source.
open_source()` の `_build_simulated_profile()` は `SourceKind.SIMULATED`
用の `StreamProfile` を常に `intrinsics=None` で組み立てる（校正値を持たない
合成入力には元々存在しないため）。一方 `flying_object_tracking.projection.
PointEstimator.estimate()` は `frame.profile.intrinsics` が `None` の場合
`IntrinsicsUnavailableError`（「呼び出し方・設定の誤り」区分。伝播する）を
送出する。したがって `open_source(SourceKind.SIMULATED)` 経由のフレームは
候補が1つでも検出されると即座に例外を伝播し、意味のある点列比較ができない
（`test_flying_object_tracking_pipeline.py`
`TestNonCatchableExceptionsPropagate.test_intrinsics_unavailable_propagates`
が同じ事実を既に固定している）。

したがって本ファイルは、タスク本文の文言そのもの——「**テスト用の**フレーム
供給」と「上流の**記録再生に相当する**供給」——を字義通りに実装する:

- 「テスト用のフレーム供給」= `tests/flying_object_tracking/fakes.py` の
  `SyntheticFrameSource`（design.md「File Structure Plan」が定める、本
  Spec 専用のテストフレーム供給。他 Spec のテストとの衝突回避のため
  `fakes_module` フィクスチャ経由で使う）
- 「上流の記録再生に相当する供給」= `sensing_foundation.SessionRecorder` で
  セッションを書き出し、`sensing_foundation.open_source(SourceKind.
  RECORDED, ...)` で読み戻したもの（`intrinsics` を含む `StreamProfile` が
  manifest 経由で正しく往復するため、上記の制約を受けない）

両者に**同一内容**（同一 `CaptureFrame` 列。ただし `SessionRecorder` に
一度通すぶん、`t_capture_ms` は再生セッション独自の `SessionClock` から
新たに算出され直す——`sensing_foundation.sources.recorded` モジュール
docstring「`t_capture_ms` の扱い」が明記する仕様——ため、点列の比較では
時刻由来のフィールドを比較対象から除外する。`source`（入力元種別）も同様に
比較対象から除外する: どちらの入力元から来たかを示すメタ情報であり、
`FrameSource` の種別が異なる2経路を比較するテストである以上、この値
自体が異なることは仕様どおりであって不一致ではない）を与え、
`track_source()` が返す `TrackUpdate` 列が（時刻・入力元種別を除いて）
完全一致することを確認する。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from sensing_foundation import (
    CameraIntrinsics,
    CaptureFrame,
    CaptureMetrics,
    CaptureStats,
    FrameSource,
    NullLogger,
    RuntimeSettings,
    SessionClock,
    SessionRecorder,
    SourceKind,
    StreamProfile,
    TimestampDomain,
    open_source,
)

import flying_object_tracking.pipeline as pipeline_module
from flying_object_tracking.config import (
    DetectorConfig,
    MeasurementConfig,
    ObjectModelConfig,
    ProjectionConfig,
    RoiConfig,
    TrackerConfig,
    TrackingSettings,
)
from flying_object_tracking.pipeline import track_source
from flying_object_tracking.types import (
    CameraPoint,
    CameraTrack,
    DetectorKind,
    TrackPoint,
    TrackUpdate,
)

DIAMETER_MM = 65.0
BLOB_Z_MM = 1000.0  # 既定 z 帯 [500, 5000] の内側
BACKGROUND_RAW = 6000  # 既定 z 帯の外側
WIDTH_PX = 64
HEIGHT_PX = 64


# ==============================================================================
# ドメイン値のビルダ（他テストファイルとの bare import 衝突を避けるため
# このファイル内に自己完結させる。test_flying_object_tracking_pipeline.py と
# 同じ流儀）
# ==============================================================================


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


def _settings() -> TrackingSettings:
    return TrackingSettings(
        roi=RoiConfig(z_min_mm=500.0, z_max_mm=5000.0),
        object_model=ObjectModelConfig(),
        detector=DetectorConfig(kind=DetectorKind.DEPTH_BAND, open_kernel_px=0, close_kernel_px=0),
        projection=ProjectionConfig(),
        tracker=TrackerConfig(max_step_mm=900.0, max_missing_frames=3, min_points_to_start=1),
        measurement=MeasurementConfig(enabled=True),
    )


def _trajectory(synthetic_module, n: int, *, step_mm: float = 15.0):
    TrajectoryPoint = synthetic_module.TrajectoryPoint
    return [
        TrajectoryPoint(t_ms=float(i) * 33.0, x_mm=float(i) * step_mm, y_mm=0.0, z_mm=BLOB_Z_MM)
        for i in range(n)
    ]


def _synthesize(synthetic_module, n: int, *, step_mm: float = 15.0) -> list[CaptureFrame]:
    trajectory = _trajectory(synthetic_module, n, step_mm=step_mm)
    return synthetic_module.synthesize_frames(
        trajectory, _profile(), DIAMETER_MM, background_raw=BACKGROUND_RAW
    )


def _record_session(root: Path, frames: list[CaptureFrame]) -> Path:
    """`frames` をそのまま `SessionRecorder` で書き出し、セッションディレクトリを返す。"""
    session_id = "track-source-equivalence-session"
    recorder = SessionRecorder(
        root,
        session_id,
        _profile(),
        device=None,
        runtime={},
        source=SourceKind.SIMULATED,
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


def _open_recorded_source(session_dir: Path) -> FrameSource:
    runtime_settings = RuntimeSettings(source=SourceKind.RECORDED, session_path=session_dir)
    clock = SessionClock(session_id="track-source-recorded-replay")
    metrics = CaptureMetrics(NullLogger(), clock, sysstat=None)
    return open_source(runtime_settings, metrics, clock=clock)


# ==============================================================================
# 正規化（時刻・入力元種別を比較対象から除外する。モジュール docstring
# 「クロスソース等価性テストの設計判断」参照）
# ==============================================================================


def _normalize_point(point: CameraPoint) -> CameraPoint:
    return replace(point, t_ms=0.0)


def _normalize_track_point(track_point: TrackPoint | None) -> TrackPoint | None:
    if track_point is None:
        return None
    return replace(track_point, point=_normalize_point(track_point.point))


def _normalize_track(track: CameraTrack) -> CameraTrack:
    return replace(
        track,
        started_t_ms=0.0,
        source=SourceKind.SIMULATED,
        points=tuple(_normalize_track_point(p) for p in track.points),
    )


def _normalize_update(update: TrackUpdate) -> TrackUpdate:
    return replace(
        update,
        track=_normalize_track(update.track),
        appended=_normalize_track_point(update.appended),
    )


# ==============================================================================
# 1. クロスソース等価性: タスクの観測可能な完了状態そのもの
# ==============================================================================


class TestCrossSourceEquivalence:
    def test_synthetic_and_recorded_sources_yield_identical_track_updates(
        self, fakes_module, synthetic_module, tmp_path: Path
    ) -> None:
        frames = _synthesize(synthetic_module, 6)
        settings = _settings()

        # (a) テスト用のフレーム供給。
        synthetic_source = fakes_module.SyntheticFrameSource(frames, kind=SourceKind.SIMULATED)
        synthetic_updates = list(track_source(synthetic_source, settings, NullLogger()))

        # (b) 上流の記録再生に相当する供給（SessionRecorder → open_source(RECORDED)）。
        session_dir = _record_session(tmp_path, frames)
        recorded_source = _open_recorded_source(session_dir)
        recorded_updates = list(track_source(recorded_source, settings, NullLogger()))

        assert len(synthetic_updates) == len(frames)
        assert len(recorded_updates) == len(frames)

        # 実際に検出・追跡が機能したことも確認する（自明に空の系列同士が
        # 一致しているだけではない）。
        assert any(u.appended is not None for u in synthetic_updates)

        normalized_synthetic = [_normalize_update(u) for u in synthetic_updates]
        normalized_recorded = [_normalize_update(u) for u in recorded_updates]
        assert normalized_synthetic == normalized_recorded

        # 最終的な CameraTrack（点列そのもの）も一致する。
        final_synthetic = _normalize_track(synthetic_updates[-1].track)
        final_recorded = _normalize_track(recorded_updates[-1].track)
        assert final_synthetic == final_recorded
        assert len(final_synthetic.points) > 0


# ==============================================================================
# 2. 反復の早期放棄でも上流の stop() が呼ばれる
# ==============================================================================


class _StopSpySource:
    """`FrameSource` を包み、`stop()` が呼ばれたかどうかを観測するだけのスパイ。

    `fakes.py`（本体には置かない、という Boundary の対象）を変更せず、
    本テストファイル内で `SyntheticFrameSource` をラップするだけの最小の
    テスト専用アダプタ。
    """

    def __init__(self, inner: FrameSource) -> None:
        self._inner = inner
        self.stop_called = False

    @property
    def kind(self):
        return self._inner.kind

    @property
    def profile(self):
        return self._inner.profile

    @property
    def stats(self):
        return self._inner.stats

    def start(self) -> None:
        self._inner.start()

    def frames(self):
        return self._inner.frames()

    def stop(self) -> None:
        self.stop_called = True
        self._inner.stop()

    def __enter__(self) -> "_StopSpySource":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()


class TestEarlyAbandonmentCallsStop:
    def test_breaking_out_of_the_consuming_loop_still_calls_stop(self, synthetic_module, fakes_module) -> None:
        frames = _synthesize(synthetic_module, 10)
        inner = fakes_module.SyntheticFrameSource(frames, kind=SourceKind.SIMULATED)
        spy = _StopSpySource(inner)
        settings = _settings()

        processed = 0
        for _update in track_source(spy, settings, NullLogger()):
            processed += 1
            if processed == 2:
                break

        assert processed == 2
        assert spy.stop_called is True

    def test_explicit_generator_close_calls_stop_synchronously(self, synthetic_module, fakes_module) -> None:
        frames = _synthesize(synthetic_module, 10)
        inner = fakes_module.SyntheticFrameSource(frames, kind=SourceKind.SIMULATED)
        spy = _StopSpySource(inner)
        settings = _settings()

        generator = track_source(spy, settings, NullLogger())
        next(generator)
        next(generator)
        assert spy.stop_called is False

        generator.close()

        assert spy.stop_called is True

    def test_stop_is_called_even_without_consuming_any_frame(self, synthetic_module, fakes_module) -> None:
        """1度も `next()` を呼ばずに `.close()` した場合でも `with source:` の
        `__enter__`/`__exit__` は起動していない（ジェネレータが一度も
        実行されていないため）。この場合 `stop()` が呼ばれないのは正しい
        挙動である——`start()` も呼ばれていない状態で `stop()` だけが
        呼ばれるのは、`with` の対称性が壊れていることになる。この非対称
        でない振る舞いを回帰的に固定する。
        """
        frames = _synthesize(synthetic_module, 3)
        inner = fakes_module.SyntheticFrameSource(frames, kind=SourceKind.SIMULATED)
        spy = _StopSpySource(inner)
        settings = _settings()

        generator = track_source(spy, settings, NullLogger())
        generator.close()

        assert spy.stop_called is False


# ==============================================================================
# 3. 基本的な反復の正しさ: 1フレームにつき1個の TrackUpdate を順序通り返す
# ==============================================================================


class TestBasicIteration:
    def test_yields_one_update_per_frame_in_order(self, synthetic_module, fakes_module) -> None:
        frames = _synthesize(synthetic_module, 5)
        source = fakes_module.SyntheticFrameSource(frames, kind=SourceKind.SIMULATED)
        settings = _settings()

        updates = list(track_source(source, settings, NullLogger()))

        assert len(updates) == len(frames)
        assert all(isinstance(u, TrackUpdate) for u in updates)
        # frame_index は appended された点を通じて単調に対応する。
        appended_frame_indices = [
            u.appended.frame_index for u in updates if u.appended is not None
        ]
        assert appended_frame_indices == sorted(appended_frame_indices)

    def test_start_is_called_before_frames_are_consumed(self, synthetic_module, fakes_module) -> None:
        frames = _synthesize(synthetic_module, 2)
        source = fakes_module.SyntheticFrameSource(frames, kind=SourceKind.SIMULATED)
        settings = _settings()

        assert source._started is False  # type: ignore[attr-defined]
        list(track_source(source, settings, NullLogger()))
        assert source._started is True  # type: ignore[attr-defined]
        assert source._stopped is True  # type: ignore[attr-defined]


# ==============================================================================
# 4. 入力元の種別で分岐しない・上流の公開入口のみを参照する（静的検査）
# ==============================================================================


class TestNoSourceKindBranchingAndPublicEntryPointOnly:
    def test_track_source_source_does_not_reference_source_kind_values(self) -> None:
        """`track_source` の**実行コード**（docstring を除く）が
        `SourceKind.LIVE`/`SIMULATED`/`RECORDED` を一切参照しない
        （＝入力元の種別で分岐しない）ことを固定する。振る舞いによる証拠は
        `TestCrossSourceEquivalence` が既に与えているため、本テストは
        ソースレベルの静的な裏付けを追加する。`ast` で docstring（説明文の
        中で `SourceKind.LIVE` 等に言及すること自体は禁じられない）を除いた
        本体だけを対象にすることで、モジュール docstring の言い回しに
        テストが引きずられないようにする。
        """
        import ast
        import inspect

        source_text = inspect.getsource(pipeline_module.track_source)
        func_node = ast.parse(source_text).body[0]
        assert isinstance(func_node, (ast.FunctionDef, ast.AsyncFunctionDef))

        body = func_node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body = body[1:]  # docstring を除く

        code_only = "\n".join(ast.unparse(stmt) for stmt in body)

        assert "SourceKind.LIVE" not in code_only
        assert "SourceKind.SIMULATED" not in code_only
        assert "SourceKind.RECORDED" not in code_only
        assert ".kind ==" not in code_only
        assert ".kind !=" not in code_only

    def test_pipeline_module_imports_only_from_sensing_foundation_public_entry_point(self) -> None:
        """`pipeline.py` の**import 文**が `sensing_foundation` の内部モジュール
        （`sensing_foundation.source` / `sensing_foundation.sources.*` /
        `sensing_foundation.recording.*`）を直接 import していないことを
        固定する。許される参照は `from sensing_foundation import ...`
        （トップレベルパッケージ）のみ。docstring 中の説明文（同じ文字列に
        言及すること自体）は対象外とするため、`import ` / `from ` で始まる
        行だけを見る。
        """
        pipeline_path = Path(pipeline_module.__file__)
        import_lines = [
            line.strip()
            for line in pipeline_path.read_text(encoding="utf-8").splitlines()
            if line.strip().startswith(("import ", "from "))
        ]

        assert any(line.startswith("from sensing_foundation import") for line in import_lines)
        assert not any("sensing_foundation.source" in line for line in import_lines)
        assert not any("sensing_foundation.sources" in line for line in import_lines)
        assert not any("sensing_foundation.recording" in line for line in import_lines)


# ==============================================================================
# 5. 例外の伝播: with source: の内側であっても TrackingConfigError 等は
#    そのまま伝播し、かつ stop() は呼ばれる
# ==============================================================================


class TestExceptionPropagationStillStopsSource:
    def test_propagated_exception_still_triggers_stop(self, synthetic_module, fakes_module) -> None:
        # ROI がフレームに収まらない設定で TrackingConfigError を伝播させる
        # （test_flying_object_tracking_pipeline.py の
        # TestNonCatchableExceptionsPropagate と同じ細工）。
        frames = _synthesize(synthetic_module, 2)
        inner = fakes_module.SyntheticFrameSource(frames, kind=SourceKind.SIMULATED)
        spy = _StopSpySource(inner)
        broken_settings = replace(
            _settings(), roi=RoiConfig(x_px=0, y_px=0, width_px=999, height_px=999)
        )

        from flying_object_tracking.errors import TrackingConfigError

        with pytest.raises(TrackingConfigError):
            list(track_source(spy, broken_settings, NullLogger()))

        assert spy.stop_called is True
