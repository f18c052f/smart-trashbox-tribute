"""検出方式の比較と判定規則（`bench/compare.py`、タスク 7.1）の検証。

design.md「L8-L9: 比較・入口 → DetectorComparison」と要件 4.4〜4.9, 8.4,
8.10, 11.5 が定める、タスク本文（verbatim）の観測可能な完了状態を固定する:

- 合成セッションに対して3方式の比較が完走して結果ファイルが生成される
- 後段設定の不一致が実行前に拒否される
- 差が四分位範囲以内の人工データで処理時間による決着が働く

## テスト構成の設計判断

タスクブリーフが明示する通り、判定規則（`decide()`）は実行を経由しない
**純粋関数**として切り出してある。したがって:

- 判定規則そのもの（ルール1〜5の分岐）は、手で組み立てた人工的な
  `DetectorRunResult` を `decide()` へ直接与えて単体で検証する
  （`TestDecideJudgmentRule` 群）。実際の合成フレーム列を3方式ぶん実行して
  自然に IQR 以内の僅差が出ることを期待しない（タスクブリーフ
  「IQR判定規則のテスト可能性」節が明示的に指示する分離）。
- 実際の3方式比較実行（`compare_detectors()`）は、完走することと、
  事前検証・独立実行・書き出しという実行固有の振る舞いだけを検証する
  （`TestCompareDetectorsValidation` / `TestIndependentFailureTolerance` /
  `TestProvisionalFlag` / `TestEndToEndSmoke` 群）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sensing_foundation import (
    CameraIntrinsics,
    CaptureMetrics,
    CaptureStats,
    FrameSource,
    NullLogger,
    RuntimeSettings,
    SessionClock,
    SessionRecorder,
    SourceKind,
    StreamProfile,
    open_source,
)

import flying_object_tracking.bench.compare as compare_module
from flying_object_tracking.bench.compare import (
    DECISION_CRITERION,
    DetectorComparisonResult,
    DetectorRunResult,
    compare_detectors,
    comparison_output_path,
    decide,
    parameter_count,
    write_comparison_result,
)
from flying_object_tracking.config import (
    DetectorConfig,
    MeasurementConfig,
    ObjectModelConfig,
    ProjectionConfig,
    RoiConfig,
    TrackerConfig,
    TrackingSettings,
)
from flying_object_tracking.errors import TrackingConfigError
from flying_object_tracking.types import DetectorKind

DIAMETER_MM = 65.0
BLOB_Z_MM = 1000.0
BACKGROUND_RAW = 6000
WIDTH_PX = 64
HEIGHT_PX = 64

STEP_MM = 15.0
WARMUP_LEN = 3
MOVING_LEN = 6


# ==============================================================================
# ドメイン値のビルダ（他テストファイルとの bare import 衝突を避けるため
# このファイル内に自己完結させる。既存タスクファイルと同じ流儀）
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


def _settings(kind: DetectorKind, *, measurement_enabled: bool = True) -> TrackingSettings:
    """`kind` だけが異なる、それ以外は全方式で同一の後段設定（要件 4.4 の精神）。"""
    return TrackingSettings(
        roi=RoiConfig(z_min_mm=500.0, z_max_mm=5000.0),
        object_model=ObjectModelConfig(diameter_mm=DIAMETER_MM),
        detector=DetectorConfig(
            kind=kind,
            diff_threshold_mm=80.0,
            background_frames=WARMUP_LEN,
            background_update_rate=0.0,
            open_kernel_px=0,
            close_kernel_px=0,
        ),
        projection=ProjectionConfig(),
        tracker=TrackerConfig(max_step_mm=900.0, max_missing_frames=3, min_points_to_start=1),
        measurement=MeasurementConfig(enabled=measurement_enabled),
    )


def _settings_by_kind(
    kinds: tuple[DetectorKind, ...] = (
        DetectorKind.DEPTH_BAND,
        DetectorKind.FRAME_DIFF,
        DetectorKind.BACKGROUND,
    ),
) -> dict[DetectorKind, TrackingSettings]:
    return {kind: _settings(kind) for kind in kinds}


def _full_trajectory(synthetic_module) -> list:
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
    trajectory = _full_trajectory(synthetic_module)
    return synthetic_module.synthesize_frames(
        trajectory, _profile(), DIAMETER_MM, background_raw=BACKGROUND_RAW
    )


def _record_session(root: Path, frames: list, session_id: str) -> Path:
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


def _forbidden_open_source() -> FrameSource:
    """呼ばれたら即座に失敗するダミーのソースファクトリ。

    実行前検証（`postprocess_fingerprint` 不一致・空の `settings_by_kind` ・
    `kind` キーの不一致）で拒否されるべきテストに使う。もし検証をすり抜けて
    実際に呼ばれてしまえば、この `AssertionError` がテスト失敗として
    はっきり現れる。
    """
    raise AssertionError("open_source が呼ばれた: 実行前検証をすり抜けている")


def _make_recorded_source_factory(session_dir: Path, session_id: str):
    """呼ぶたびに新規 `FrameSource` を返すファクトリ（`compare_detectors` の契約）。"""
    counter = {"n": 0}

    def _open() -> FrameSource:
        counter["n"] += 1
        runtime_settings = RuntimeSettings(source=SourceKind.RECORDED, session_path=session_dir)
        clock = SessionClock(session_id=f"{session_id}-replay-{counter['n']}")
        metrics = CaptureMetrics(NullLogger(), clock, sysstat=None)
        return open_source(runtime_settings, metrics, clock=clock)

    return _open


# ==============================================================================
# 判定規則の建材（人工的な DetectorRunResult。実行を経由しない）
# ==============================================================================


def _run_result(
    *,
    detector_kind: str,
    session_id: str = "artificial-session",
    source: SourceKind = SourceKind.LIVE,
    effective_points_per_window: float,
    detect_ms_p50: float = 5.0,
    detect_ms_p95: float,
    detect_ms_iqr: float,
    fingerprint: str = "fp-fixed",
) -> DetectorRunResult:
    return DetectorRunResult(
        detector_kind=detector_kind,
        session_id=session_id,
        source=source,
        frames_processed=100,
        frames_without_candidate=10,
        points_appended=80,
        point_failures=2,
        effective_points_per_window=effective_points_per_window,
        effective_points_peak=int(effective_points_per_window) + 1,
        detect_ms_p50=detect_ms_p50,
        detect_ms_p95=detect_ms_p95,
        detect_ms_iqr=detect_ms_iqr,
        track_ms_p50=1.0,
        track_ms_p95=2.0,
        tracks_started=1,
        postprocess_fingerprint=fingerprint,
    )


# ==============================================================================
# 1. 判定規則（純粋関数 decide()）: タスクの観測可能な完了状態そのもの
# ==============================================================================


class TestDecideJudgmentRule:
    def test_rule1_clear_leader_outside_iqr_is_selected_directly(self) -> None:
        """3方式のうち1つが effective_points_per_window で明確に上回る
        （他方式の detect_ms_iqr の外）場合、その方式が直接選ばれる。
        """
        leader = _run_result(
            detector_kind="depth_band",
            effective_points_per_window=100.0,
            detect_ms_p95=15.0,
            detect_ms_iqr=1.0,
        )
        runner_up = _run_result(
            detector_kind="frame_diff",
            effective_points_per_window=50.0,
            detect_ms_p95=10.0,
            detect_ms_iqr=1.0,
        )
        last = _run_result(
            detector_kind="background",
            effective_points_per_window=40.0,
            detect_ms_p95=5.0,
            detect_ms_iqr=1.0,
        )

        decision = decide([leader, runner_up, last])

        assert decision.selected == "depth_band"
        assert decision.criterion == DECISION_CRITERION
        assert "ルール1" in decision.rationale
        assert decision.provisional is False  # 3件とも source=LIVE

    def test_rule2_falls_through_to_detect_ms_p95_when_within_iqr(self) -> None:
        """上位2方式の effective_points_per_window の差が互いの detect_ms_iqr
        以内なら、区別がついていないとみなして detect_ms_p95 が小さい方を採る。
        """
        leader_by_points = _run_result(
            detector_kind="frame_diff",
            effective_points_per_window=100.0,
            detect_ms_p95=15.0,
            detect_ms_iqr=20.0,
        )
        close_second = _run_result(
            detector_kind="depth_band",
            effective_points_per_window=95.0,  # 差5 <= IQR20 → 区別つかず
            detect_ms_p95=10.0,  # こちらの方が小さい
            detect_ms_iqr=20.0,
        )
        far_third = _run_result(
            detector_kind="background",
            effective_points_per_window=40.0,  # 差60 > IQR20 → 区別つく（除外）
            detect_ms_p95=5.0,
            detect_ms_iqr=20.0,
        )

        decision = decide([leader_by_points, close_second, far_third])

        # 実効点数の最大は frame_diff だが、区別がつかない depth_band との
        # 間では detect_ms_p95 が小さい depth_band を採る。
        assert decision.selected == "depth_band"
        assert "ルール2" in decision.rationale

    def test_rule2_threshold_uses_max_of_both_iqrs_not_leader_only(self) -> None:
        """ルール2の「四分位範囲以内」は比較対象2方式の `detect_ms_iqr` の
        **大きい方**を基準にする（`compare.py` モジュール docstring「ルール2
        『四分位範囲』の解釈についての設計判断」、実装は
        `max(leader.detect_ms_iqr, run.detect_ms_iqr)`）。

        `TestDecideJudgmentRule` の他のフィクスチャは比較対象2方式の
        `detect_ms_iqr` を常に同値にしているため、`max(...)` を
        `leader.detect_ms_iqr` 単独に書き換える回帰が混入しても他のテストは
        気づけない（レビュー指摘）。本テストは非対称な IQR
        （leader=5, contender=30）と、その中間の差（20）を持つ人工データで
        両者を判別する:

        - 差20 は leader 単独の IQR=5 より大きい → leader 単独 IQR 判定なら
          「区別がついている」とみなし、ルール1で leader を即決定してしまう
          （effective_points_per_window が大きい depth_band が選ばれる）
        - 差20 は `max(5, 30)=30` 以内 → 正しい実装は「区別がついていない」
          とみなし、ルール2（detect_ms_p95 の比較）へフォールスルーする
          （detect_ms_p95 が小さい frame_diff が選ばれる）
        """
        leader = _run_result(
            detector_kind="depth_band",
            effective_points_per_window=100.0,
            detect_ms_p95=20.0,  # leader の方が処理時間は大きい
            detect_ms_iqr=5.0,
        )
        contender = _run_result(
            detector_kind="frame_diff",
            effective_points_per_window=80.0,  # leader との差は20
            detect_ms_p95=8.0,  # フォールスルーすればこちらが勝つ
            detect_ms_iqr=30.0,
        )

        decision = decide([leader, contender])

        # max ベースの正しい実装ならルール2へフォールスルーし、
        # detect_ms_p95 が小さい frame_diff（contender）が選ばれる。
        # leader 単独 IQR への回帰（変異）が起きると、ルール1で depth_band
        # （leader）が即決定されてしまい、このアサーションが失敗する。
        assert decision.selected == "frame_diff"
        assert "ルール2" in decision.rationale

    def test_rule3_falls_through_to_parameter_count_when_p95_also_ties(self) -> None:
        """effective_points_per_window・detect_ms_p95 の両方が並ぶ場合、
        設定パラメータ数（parameter_count()）が少ない方式を採る。
        """
        frame_diff_run = _run_result(
            detector_kind="frame_diff",  # parameter_count=4
            effective_points_per_window=100.0,
            detect_ms_p95=10.0,
            detect_ms_iqr=20.0,
        )
        depth_band_run = _run_result(
            detector_kind="depth_band",  # parameter_count=3（最少）
            effective_points_per_window=95.0,
            detect_ms_p95=10.0,  # p95 も完全に同値
            detect_ms_iqr=20.0,
        )

        assert parameter_count("depth_band") < parameter_count("frame_diff")

        decision = decide([frame_diff_run, depth_band_run])

        assert decision.selected == "depth_band"
        assert "ルール3" in decision.rationale

    def test_rule5_everything_tied_yields_none_as_a_normal_result(self) -> None:
        """effective_points_per_window・detect_ms_p95・パラメータ数のいずれも
        並ぶ場合、無理に1つを選ばず `selected=None` を正常な結果として返す
        （要件 4.9）。同一方式（同じ parameter_count）の2つの実行として構成
        する——3方式（3/4/6パラメータ）は互いに異なるため、実在する異なる
        2方式だけではパラメータ数まで完全に一致させられない。
        """
        run_a = _run_result(
            detector_kind="depth_band",
            session_id="session-a",
            effective_points_per_window=100.0,
            detect_ms_p95=10.0,
            detect_ms_iqr=20.0,
        )
        run_b = _run_result(
            detector_kind="depth_band",
            session_id="session-b",
            effective_points_per_window=95.0,
            detect_ms_p95=10.0,
            detect_ms_iqr=20.0,
        )

        decision = decide([run_a, run_b])

        assert decision.selected is None
        assert decision.criterion == DECISION_CRITERION
        assert decision.rationale  # 空文字列ではない、理由が残る
        assert decision.provisional is False

    def test_empty_runs_yields_none_and_provisional_true(self) -> None:
        """実行結果が1件も無い場合も例外にせず、決めきれない結果として返す。"""
        decision = decide([])

        assert decision.selected is None
        assert decision.provisional is True
        assert decision.criterion == DECISION_CRITERION

    def test_decision_is_recomputable_from_raw_run_results(self) -> None:
        """`decide()` は純粋関数であり、同一の生の `DetectorRunResult` から
        同一の決定を再現できる（「生の計測値を残し、判定を後から再計算できる
        ようにする」というタスク本文の要求そのもの）。
        """
        runs = [
            _run_result(
                detector_kind="depth_band",
                effective_points_per_window=100.0,
                detect_ms_p95=15.0,
                detect_ms_iqr=1.0,
            ),
            _run_result(
                detector_kind="frame_diff",
                effective_points_per_window=10.0,
                detect_ms_p95=5.0,
                detect_ms_iqr=1.0,
            ),
        ]

        first = decide(runs)
        second = decide(runs)

        assert first == second


class TestParameterCount:
    def test_parameter_counts_differ_across_kinds(self) -> None:
        """3方式の parameter_count は互いに異なる（ルール3が機能する前提）。"""
        counts = {
            "depth_band": parameter_count("depth_band"),
            "frame_diff": parameter_count("frame_diff"),
            "background": parameter_count("background"),
        }
        assert counts["depth_band"] < counts["frame_diff"] < counts["background"]

    def test_unknown_kind_raises_key_error(self) -> None:
        with pytest.raises(KeyError):
            parameter_count("not_a_real_detector_kind")


# ==============================================================================
# 2. 実行前検証: 後段設定の不一致は拒否される（タスクの観測可能な完了状態）
# ==============================================================================


class TestCompareDetectorsValidation:
    def test_postprocess_fingerprint_mismatch_is_rejected_before_any_execution(
        self, monkeypatch, synthetic_module, fakes_module
    ) -> None:
        """後段設定が異なる（例: object_model.diameter_mm）3方式を渡すと、
        どの方式の実行も始まる前に TrackingConfigError で拒否される。
        """
        settings_by_kind = _settings_by_kind()
        # BACKGROUND だけ object_model.diameter_mm を変える（後段設定の不一致）。
        from dataclasses import replace as dc_replace

        mismatched = dc_replace(
            settings_by_kind[DetectorKind.BACKGROUND],
            object_model=ObjectModelConfig(diameter_mm=DIAMETER_MM + 10.0),
        )
        settings_by_kind[DetectorKind.BACKGROUND] = mismatched

        call_count = {"n": 0}

        def _spy_run_single_detector(*args, **kwargs):
            call_count["n"] += 1
            raise AssertionError("後段設定の不一致は実行前に拒否されるはずで、ここへ到達しない")

        monkeypatch.setattr(compare_module, "_run_single_detector", _spy_run_single_detector)

        frames = _synthesize(synthetic_module)
        source = fakes_module.SyntheticFrameSource(frames, kind=SourceKind.SIMULATED)

        with pytest.raises(TrackingConfigError):
            compare_detectors("mismatch-session", lambda: source, settings_by_kind)

        assert call_count["n"] == 0

    def test_empty_settings_by_kind_is_rejected(self) -> None:
        with pytest.raises(TrackingConfigError):
            compare_detectors("empty-session", _forbidden_open_source, {})

    def test_kind_key_mismatch_with_settings_detector_kind_is_rejected(self) -> None:
        settings_by_kind = {DetectorKind.DEPTH_BAND: _settings(DetectorKind.FRAME_DIFF)}
        with pytest.raises(TrackingConfigError):
            compare_detectors(
                "kind-mismatch-session",
                _forbidden_open_source,
                settings_by_kind,
            )


# ==============================================================================
# 3. 独立実行と失敗耐性: タスクの観測可能な完了状態そのもの
# ==============================================================================


class TestIndependentFailureTolerance:
    def test_one_kind_failing_does_not_discard_the_others(
        self, monkeypatch, synthetic_module, tmp_path: Path
    ) -> None:
        """BACKGROUND 方式の実行だけが例外を送出しても、他の2方式の結果は
        `DetectorComparisonResult.runs` に残り続ける（結果セットから捨てられ
        ない）。
        """
        original_run_single = compare_module._run_single_detector

        def _flaky(kind, settings, open_source_fn, session_id):
            if kind is DetectorKind.BACKGROUND:
                raise RuntimeError("模擬的な実行時障害")
            return original_run_single(kind, settings, open_source_fn, session_id)

        monkeypatch.setattr(compare_module, "_run_single_detector", _flaky)

        frames = _synthesize(synthetic_module)
        session_dir = _record_session(tmp_path, frames, "failure-tolerance-session")
        open_factory = _make_recorded_source_factory(session_dir, "failure-tolerance-session")

        settings_by_kind = _settings_by_kind()
        result = compare_detectors("failure-tolerance-session", open_factory, settings_by_kind)

        kinds_in_order = list(settings_by_kind.keys())
        background_index = kinds_in_order.index(DetectorKind.BACKGROUND)

        assert result.runs[background_index] is None
        assert str(DetectorKind.BACKGROUND) in result.failures
        assert "RuntimeError" in result.failures[str(DetectorKind.BACKGROUND)]

        other_indices = [i for i in range(len(kinds_in_order)) if i != background_index]
        for idx in other_indices:
            assert result.runs[idx] is not None
            assert isinstance(result.runs[idx], DetectorRunResult)

        # decide() は成功した実行だけを対象にする。
        assert result.decision is not None


# ==============================================================================
# 4. provisional フラグ: タスクの観測可能な完了状態そのもの（要件 11.5）
# ==============================================================================


class TestProvisionalFlag:
    def test_simulated_source_marks_result_as_provisional(
        self, synthetic_module, tmp_path: Path
    ) -> None:
        """入力元が simulated（実機由来でない）セッションに対する比較結果は
        `decision.provisional=True` になり、`note` が「実機の結論として扱わ
        ない」旨を明示する。
        """
        frames = _synthesize(synthetic_module)
        session_dir = _record_session(tmp_path, frames, "provisional-session")
        open_factory = _make_recorded_source_factory(session_dir, "provisional-session")

        settings_by_kind = _settings_by_kind((DetectorKind.DEPTH_BAND, DetectorKind.FRAME_DIFF))
        result = compare_detectors("provisional-session", open_factory, settings_by_kind)

        assert result.decision.provisional is True
        assert "実機の結論として扱わない" in result.note


# ==============================================================================
# 5. 結合レベルの完走テスト（タスクブリーフ「IQR判定規則のテスト可能性」節
#    の指示どおり、判定規則そのものではなく完走・出力を検証する）
# ==============================================================================


class TestEndToEndSmoke:
    def test_three_detector_kinds_complete_and_produce_a_result_file(
        self, synthetic_module, tmp_path: Path
    ) -> None:
        frames = _synthesize(synthetic_module)
        session_id = "smoke-session"
        session_dir = _record_session(tmp_path, frames, session_id)
        open_factory = _make_recorded_source_factory(session_dir, session_id)

        settings_by_kind = _settings_by_kind()
        result = compare_detectors(session_id, open_factory, settings_by_kind)

        assert isinstance(result, DetectorComparisonResult)
        assert len(result.runs) == 3
        assert all(run is not None for run in result.runs)
        assert not result.failures

        fingerprints = {run.postprocess_fingerprint for run in result.runs if run is not None}
        assert len(fingerprints) == 1  # 後段設定は全方式で同一（要件 4.4）

        for run in result.runs:
            assert run is not None
            assert run.session_id == session_id
            assert run.frames_processed == len(frames)

        assert result.decision.criterion == DECISION_CRITERION

        output_root = tmp_path / "var" / "tracking"
        written_path = write_comparison_result(result, output_root, session_id)

        assert written_path == comparison_output_path(output_root, session_id)
        assert written_path.exists()

        import json

        payload = json.loads(written_path.read_text(encoding="utf-8"))
        assert len(payload["runs"]) == 3
        assert payload["decision"]["criterion"] == DECISION_CRITERION
        assert payload["failures"] == {}

    def test_smoke_run_tolerates_one_broken_kind_via_failing_source_factory(
        self, synthetic_module, tmp_path: Path
    ) -> None:
        """3方式のうち1つ（呼び出し順で2番目）の `open_source()` 呼び出し自体
        が失敗する現実的なケースでも、比較全体は完走し他方式の結果を残す。
        `settings_by_kind` の反復順は挿入順（`_settings_by_kind()` の定義順:
        DEPTH_BAND, FRAME_DIFF, BACKGROUND）と一致するため、2回目の呼び出し
        は FRAME_DIFF に対応する。
        """
        frames = _synthesize(synthetic_module)
        session_id = "smoke-partial-failure-session"
        session_dir = _record_session(tmp_path, frames, session_id)
        base_factory = _make_recorded_source_factory(session_dir, session_id)

        call_count = {"n": 0}

        def _flaky_factory() -> FrameSource:
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("模擬的なセッションオープン失敗")
            return base_factory()

        settings_by_kind = _settings_by_kind()
        result = compare_detectors(session_id, _flaky_factory, settings_by_kind)

        kinds_in_order = list(settings_by_kind.keys())
        assert kinds_in_order[1] == DetectorKind.FRAME_DIFF
        assert result.runs[1] is None
        assert str(DetectorKind.FRAME_DIFF) in result.failures

        remaining = [r for r in result.runs if r is not None]
        assert len(remaining) == 2
