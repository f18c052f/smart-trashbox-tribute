"""`tracking.py`（タスク 4.1 `SingleObjectTracker`）の検証。

design.md「L6-L7: 追跡と統合 → SingleObjectTracker」の対応付け規則（手順1〜5）
と、要件 6.1〜6.9, 9.1, 9.5 が定める観測可能な完了状態を固定する。

design.md が書き切っていない部分について本タスクが下した設計判断
（`src/flying_object_tracking/tracking.py` モジュール docstring に詳細を
記載）をここでも確認する。

- IDLE 状態での開始規則: `min_points_to_start` 個そろって初めて開始し、
  そのフレームの点集合全体から `(z_mm, y_mm, x_mm)` 最小のものを選ぶ
- 点はあるが全候補がゲート外 → 0個の場合と同じ欠測として計上する
- `max_points` 到達は、その点を追加した `update()` 呼び出し内で終了する
- `gate_rejected`（ゲートで落ちた件数の累計）に専用フィールドは無い。
  **`sum(TrackPoint.rivals)` という事後合計では真値を再現できない**
  （候補全滅で `appended is None` のフレームは `rivals` に一切残らず
  過小評価する）。正しい算出は `TRACKING` 状態の `update()` 呼び出し
  ごとに、採用時は `candidates - 1`、非採用時は `candidates` を
  累積することによる（詳細は `tracking.py` モジュール docstring 参照）
- `update()` を `ENDED` 状態で呼んでも例外にならず、現在のスナップショットを
  そのまま返す（冪等）
- `finish()` を既に `ENDED` の追跡へ呼んでも、既存の `end_reason` を
  上書きしない
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from flying_object_tracking.config import TrackerConfig
from flying_object_tracking.tracking import SingleObjectTracker
from flying_object_tracking.types import (
    CameraPoint,
    CoordinateFrame,
    SourceKind,
    TrackEndReason,
    TrackState,
)

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "flying_object_tracking"


# --------------------------------------------------------------------------
# ヘルパー
# --------------------------------------------------------------------------


def _point(
    t_ms: float,
    x_mm: float,
    y_mm: float,
    z_mm: float,
    *,
    valid_depth_px: int = 50,
    depth_spread_mm: float = 5.0,
    apparent_diameter_px: float = 20.0,
    expected_diameter_px: float = 20.0,
    intrinsics_source: str = "stream_profile",
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
        intrinsics_source=intrinsics_source,
    )


def _config(
    *,
    max_step_mm: float = 900.0,
    max_missing_frames: int = 3,
    min_points_to_start: int = 1,
    max_points: int = 120,
) -> TrackerConfig:
    return TrackerConfig(
        max_step_mm=max_step_mm,
        max_missing_frames=max_missing_frames,
        min_points_to_start=min_points_to_start,
        max_points=max_points,
    )


def _tracker(config: TrackerConfig | None = None) -> SingleObjectTracker:
    return SingleObjectTracker(
        config or _config(),
        SourceKind.SIMULATED,
        "frame_diff",
    )


# --------------------------------------------------------------------------
# 初期状態・追跡開始（要件 6.1〜6.3）
# --------------------------------------------------------------------------


def test_initial_state_is_idle() -> None:
    tracker = _tracker()
    assert tracker.state is TrackState.IDLE


def test_first_accepted_point_starts_tracking_and_records_started_t_ms() -> None:
    tracker = _tracker()
    p0 = _point(100.0, 0.0, 0.0, 1000.0)

    result = tracker.update([p0], frame_index=0, frame_seq=0, gap_before=0)

    assert tracker.state is TrackState.TRACKING
    assert result.track.state is TrackState.TRACKING
    assert result.track.started_t_ms == 100.0
    assert result.appended is not None
    assert result.appended.point == p0
    assert result.track.points == (result.appended,)
    assert result.candidates == 1


def test_min_points_to_start_requires_the_configured_count_in_one_frame() -> None:
    """`min_points_to_start=2` の場合、1点だけのフレームでは開始しない。

    2点そろったフレームで初めて開始し、`(z_mm, y_mm, x_mm)` 最小の点が
    選ばれる（設計判断1）。
    """
    tracker = _tracker(_config(min_points_to_start=2))

    only_one = tracker.update(
        [_point(0.0, 0.0, 0.0, 1000.0)], frame_index=0, frame_seq=0, gap_before=0
    )
    assert tracker.state is TrackState.IDLE
    assert only_one.appended is None
    assert only_one.track.points == ()

    smaller = _point(33.0, 0.0, 0.0, 900.0)  # z が小さい方
    larger = _point(33.0, 0.0, 0.0, 1000.0)
    started = tracker.update([larger, smaller], frame_index=1, frame_seq=1, gap_before=0)

    assert tracker.state is TrackState.TRACKING
    assert started.appended is not None
    assert started.appended.point == smaller
    assert started.appended.rivals == 1
    assert started.track.started_t_ms == 33.0


# --------------------------------------------------------------------------
# 直線軌道の逐次追加（要件 6.2, 6.4）
# --------------------------------------------------------------------------


def test_straight_line_trajectory_is_accepted_and_appended_in_order() -> None:
    tracker = _tracker(_config(max_step_mm=150.0))
    trajectory = [_point(i * 33.0, i * 100.0, 0.0, 1000.0) for i in range(5)]

    appended_points = []
    for i, p in enumerate(trajectory):
        result = tracker.update([p], frame_index=i, frame_seq=i, gap_before=0)
        assert result.appended is not None
        appended_points.append(result.appended.point)

    assert appended_points == trajectory
    assert tracker.state is TrackState.TRACKING


# --------------------------------------------------------------------------
# 予測位置ゲート（要件 6.2, 6.6）
# --------------------------------------------------------------------------


def test_point_within_extrapolated_gate_is_accepted() -> None:
    tracker = _tracker(_config(max_step_mm=150.0))
    p0 = _point(0.0, 0.0, 0.0, 1000.0)
    p1 = _point(33.0, 100.0, 0.0, 1000.0)
    tracker.update([p0], frame_index=0, frame_seq=0, gap_before=0)
    tracker.update([p1], frame_index=1, frame_seq=1, gap_before=0)

    # 等速外挿による予測位置は (200, 0, 1000)。30mm 手前の候補はゲート内。
    candidate = _point(66.0, 230.0, 0.0, 1000.0)
    result = tracker.update([candidate], frame_index=2, frame_seq=2, gap_before=0)

    assert result.appended is not None
    assert result.appended.point == candidate
    assert tracker.state is TrackState.TRACKING


def test_point_outside_extrapolated_gate_is_rejected_and_counts_as_missing() -> None:
    config = _config(max_step_mm=150.0, max_missing_frames=3)
    tracker = _tracker(config)
    p0 = _point(0.0, 0.0, 0.0, 1000.0)
    p1 = _point(33.0, 100.0, 0.0, 1000.0)
    tracker.update([p0], frame_index=0, frame_seq=0, gap_before=0)
    tracker.update([p1], frame_index=1, frame_seq=1, gap_before=0)

    # 予測位置 (200, 0, 1000) から遠く離れた候補。ゲート外。
    far_candidate = _point(66.0, 700.0, 0.0, 1000.0)
    result = tracker.update([far_candidate], frame_index=2, frame_seq=2, gap_before=0)

    assert result.appended is None
    assert result.track.points == (
        result.track.points[0],
        result.track.points[1],
    )  # 2点のまま増えない
    assert len(result.track.points) == 2
    # まだ欠測超過ではないので TRACKING を継続する
    assert tracker.state is TrackState.TRACKING


def test_extrapolated_prediction_is_load_bearing_not_naive_last_point() -> None:
    """要件6.2: 直近2点の**等速外挿**で予測位置を作ること（直前点だけを
    予測位置とする素朴な実装ではないこと）をピンポイントで確認する。

    ゲートを外挿予測ちょうどの近傍だけに絞ることで、正しい外挿と
    素朴な「直前点＝予測位置」実装とで受理・棄却の結果が食い違う配置を
    作る。
    """
    # gate=30mm、確立ステップ=20mm（<=gate。1点しか無い間の縮退ゲートを
    # 満たすために必要）。正しい外挿予測は (40,0,1000)、素朴な直前点予測は
    # (20,0,1000) であり、この2つは20mmしか離れていない。したがって
    # 単純に「片方の予測位置ちょうど」を候補にしても両実装で結果が一致
    # してしまう（20mm差はゲート30mm以内に収まるため）。そこで、2つの
    # 予測位置からそれぞれ反対方向にオフセットした候補を使い、
    # 「correct 予測からは近いが naive 予測からは遠い」候補 (a) と、
    # 「naive 予測からは近いが correct 予測からは遠い」候補 (b) を作る。
    config = _config(max_step_mm=30.0, max_missing_frames=10)

    # (a) 候補 (60,0,1000): correct 予測 (40,0,1000) からは距離20mm
    #     （ゲート内、受理される）。naive 予測 (20,0,1000) からは
    #     距離40mm（ゲート外）。素朴な直前点予測の実装ならここで
    #     棄却されるはずの候補が、正しい外挿の下では受理される。
    tracker_a = _tracker(config)
    tracker_a.update(
        [_point(0.0, 0.0, 0.0, 1000.0)], frame_index=0, frame_seq=0, gap_before=0
    )
    tracker_a.update(
        [_point(33.0, 20.0, 0.0, 1000.0)], frame_index=1, frame_seq=1, gap_before=0
    )
    near_correct_prediction = _point(66.0, 60.0, 0.0, 1000.0)
    result_a = tracker_a.update(
        [near_correct_prediction], frame_index=2, frame_seq=2, gap_before=0
    )
    assert result_a.appended is not None
    assert result_a.appended.point == near_correct_prediction

    # (b) 候補 (-9,0,1000): naive 予測 (20,0,1000) からは距離29mm
    #     （ゲート内。素朴な直前点予測の実装ならここで受理されてしまう）。
    #     correct 予測 (40,0,1000) からは距離49mm（ゲート外、棄却される）。
    tracker_b = _tracker(config)
    tracker_b.update(
        [_point(0.0, 0.0, 0.0, 1000.0)], frame_index=0, frame_seq=0, gap_before=0
    )
    tracker_b.update(
        [_point(33.0, 20.0, 0.0, 1000.0)], frame_index=1, frame_seq=1, gap_before=0
    )
    near_naive_prediction = _point(66.0, -9.0, 0.0, 1000.0)
    result_b = tracker_b.update(
        [near_naive_prediction], frame_index=2, frame_seq=2, gap_before=0
    )
    assert result_b.appended is None


def test_points_present_but_all_outside_gate_counts_toward_missing_limit() -> None:
    """設計判断2: 点はあるが全候補がゲート外の場合も欠測として計上する。"""
    config = _config(max_step_mm=10.0, max_missing_frames=2)
    tracker = _tracker(config)
    tracker.update(
        [_point(0.0, 0.0, 0.0, 1000.0)], frame_index=0, frame_seq=0, gap_before=0
    )
    assert tracker.state is TrackState.TRACKING

    far = _point(0.0, 9999.0, 0.0, 1000.0)
    tracker.update([far], frame_index=1, frame_seq=1, gap_before=0)
    assert tracker.state is TrackState.TRACKING
    tracker.update([far], frame_index=2, frame_seq=2, gap_before=0)
    assert tracker.state is TrackState.TRACKING
    result = tracker.update([far], frame_index=3, frame_seq=3, gap_before=0)

    assert tracker.state is TrackState.ENDED
    assert result.track.end_reason is TrackEndReason.LOST


# --------------------------------------------------------------------------
# 決定的なタイブレーク（要件 6.6）
# --------------------------------------------------------------------------


def _run_tiebreak_scenario() -> tuple[SingleObjectTracker, CameraPoint, CameraPoint, CameraPoint]:
    tracker = _tracker(_config(max_step_mm=150.0))
    p0 = _point(0.0, 0.0, 0.0, 1000.0)
    tracker.update([p0], frame_index=0, frame_seq=0, gap_before=0)

    # 予測位置は p0 自身 (0, 0, 1000)（点が1つのため直前点に縮退）。
    # candidate_a / candidate_b は predicted から等距離 (100mm)。
    candidate_a = _point(33.0, 100.0, 0.0, 1000.0)
    candidate_b = _point(33.0, -100.0, 0.0, 1000.0)
    return tracker, p0, candidate_a, candidate_b


def test_equidistant_candidates_break_ties_by_zyx_lexicographic_order() -> None:
    tracker, _p0, candidate_a, candidate_b = _run_tiebreak_scenario()

    result = tracker.update(
        [candidate_a, candidate_b], frame_index=1, frame_seq=1, gap_before=0
    )

    # z, y は同値。x は candidate_b (-100) < candidate_a (100) なので b が勝つ。
    assert result.appended is not None
    assert result.appended.point == candidate_b
    assert result.appended.rivals == 1


def test_tiebreak_is_deterministic_across_repeated_runs() -> None:
    winners = []
    for _ in range(5):
        tracker, _p0, candidate_a, candidate_b = _run_tiebreak_scenario()
        result = tracker.update(
            [candidate_a, candidate_b], frame_index=1, frame_seq=1, gap_before=0
        )
        assert result.appended is not None
        winners.append(result.appended.point)

    assert len(set(winners)) == 1
    assert winners[0] == candidate_b


def test_tiebreak_prioritizes_z_over_x_on_genuine_conflict() -> None:
    """z が小さい方を優先することを、x とは逆の結果になる配置で確認する。

    `candidate_a`/`candidate_b` は x だけが異なり z・y は同値だったため
    （直前のテスト）、z が「最初に見られる軸」であることまでは確認
    できない（x を先に見ても y・z が同値なら同じ結果になってしまう）。
    ここでは z が小さい方が x は大きい、という**本物の対立**を作り、
    `(z, y, x)` の優先順位で z が最優先であることを直接固定する。
    """
    tracker = _tracker(_config(max_step_mm=150.0))
    tracker.update(
        [_point(0.0, 0.0, 0.0, 1000.0)], frame_index=0, frame_seq=0, gap_before=0
    )
    # 予測位置は (0, 0, 1000)。どちらも距離100mmで同値。
    smaller_z_larger_x = _point(33.0, 60.0, 0.0, 920.0)  # dist = sqrt(60^2+80^2) = 100
    larger_z_smaller_x = _point(33.0, 0.0, 0.0, 1100.0)  # dist = 100

    result = tracker.update(
        [smaller_z_larger_x, larger_z_smaller_x], frame_index=1, frame_seq=1, gap_before=0
    )

    # x だけを見れば larger_z_smaller_x (x=0) が勝つはずだが、
    # z が優先されるため smaller_z_larger_x (z=920) が勝つ。
    assert result.appended is not None
    assert result.appended.point == smaller_z_larger_x


def test_tiebreak_prioritizes_y_over_x_when_z_is_tied() -> None:
    """z が同値のとき、x より先に y で決着することを、x とは逆の結果になる
    配置で確認する。
    """
    tracker = _tracker(_config(max_step_mm=150.0))
    tracker.update(
        [_point(0.0, 0.0, 0.0, 1000.0)], frame_index=0, frame_seq=0, gap_before=0
    )
    # 予測位置は (0, 0, 1000)。z は両方とも同値（=1000）。距離はどちらも100mm。
    smaller_y_larger_x = _point(33.0, 80.0, -60.0, 1000.0)  # dist = sqrt(80^2+60^2) = 100
    larger_y_smaller_x = _point(33.0, 0.0, 100.0, 1000.0)  # dist = 100

    result = tracker.update(
        [smaller_y_larger_x, larger_y_smaller_x], frame_index=1, frame_seq=1, gap_before=0
    )

    # x だけを見れば larger_y_smaller_x (x=0) が勝つはずだが、
    # z が同値の場合は y が優先されるため smaller_y_larger_x (y=-60) が勝つ。
    assert result.appended is not None
    assert result.appended.point == smaller_y_larger_x


# --------------------------------------------------------------------------
# rivals / gate_rejected（要件 6.6, 9.4）
# --------------------------------------------------------------------------


def test_rivals_equals_gate_candidates_minus_one() -> None:
    tracker = _tracker(_config(max_step_mm=500.0))
    p0 = _point(0.0, 0.0, 0.0, 1000.0)
    tracker.update([p0], frame_index=0, frame_seq=0, gap_before=0)

    nearest = _point(33.0, 10.0, 0.0, 1000.0)
    mid = _point(33.0, 50.0, 0.0, 1000.0)
    farthest = _point(33.0, 100.0, 0.0, 1000.0)  # それでもゲート内 (<=500mm)

    result = tracker.update(
        [farthest, nearest, mid], frame_index=1, frame_seq=1, gap_before=0
    )

    assert result.appended is not None
    assert result.appended.point == nearest
    assert result.appended.rivals == 2  # ゲート内3候補のうち選ばれなかった2件


def test_gate_rejected_true_total_requires_per_call_accumulation_not_rivals_sum() -> None:
    """設計判断4（訂正版）: `gate_rejected` の正しい累計は `track.points` の
    `rivals` を事後的に合計しても再現できない。

    レビューで実証された欠陥: あるフレームで候補が複数あっても**全てが
    ゲート外**だった場合、`appended is None` となり `TrackPoint` が1つも
    作られない。そのフレームの候補はどの `rivals` にも計上されないため、
    `sum(p.rivals for p in track.points)` は真の「ゲートで落ちた件数」を
    過小評価する。

    正しい算出は、`TRACKING` 状態で対応付けを試みた `update()` 呼び出し
    ごとに次を累積することによる（`IDLE` でまだ開始していない呼び出しは
    「まだ軌道が無い」ため対象外）:

    - `appended is not None` の場合: `candidates - 1`
      （採用された1点を除く全候補が「その回で不採用」であり、内訳は
      ゲート内で敗れた分 = `appended.rivals` と、ゲート外だった分 =
      `candidates - 1 - appended.rivals` の合計に等しい）
    - `appended is None` の場合: `candidates`
      （0件なら0のまま。候補があったのに1つも採用されなかった＝
      全てゲート外だった、という場合のみ意味のある値になる）

    `SingleObjectTracker.update()` が返す `TrackUpdate.candidates` /
    `appended` の2値だけで呼び出し側（`TrackingMetrics`, タスク5.1）が
    正確な累計を再構成できるため、`SingleObjectTracker` 自身は専用の
    カウンタを持たない（`__init__`/`state`/`update`/`finish`/`reset` という
    design.md の Authoritative shape を変更しない）。
    """
    config = _config(max_step_mm=50.0, max_missing_frames=10)
    tracker = _tracker(config)
    correct_total = 0

    def _accumulate(result, *, was_tracking_before_call: bool) -> None:
        nonlocal correct_total
        if not was_tracking_before_call:
            return
        if result.appended is not None:
            correct_total += result.candidates - 1
        else:
            correct_total += result.candidates

    # コールドスタート（まだ軌道が無く、ゲート概念の対象外）。
    was_tracking = tracker.state is TrackState.TRACKING
    r0 = tracker.update(
        [_point(0.0, 0.0, 0.0, 1000.0)], frame_index=0, frame_seq=0, gap_before=0
    )
    _accumulate(r0, was_tracking_before_call=was_tracking)
    assert tracker.state is TrackState.TRACKING

    # frame1: 単一候補・ゲート内で採用。rivals=0 のため寄与も 0。
    was_tracking = tracker.state is TrackState.TRACKING
    r1 = tracker.update(
        [_point(33.0, 30.0, 0.0, 1000.0)], frame_index=1, frame_seq=1, gap_before=0
    )
    _accumulate(r1, was_tracking_before_call=was_tracking)
    assert r1.appended is not None
    assert r1.appended.rivals == 0

    # frame2: 予測位置 (60,0,1000)。ゲート内2件（採用+rival1件）に加え
    # ゲート外2件。採用されて rivals=1 だが、真の寄与は
    # candidates-1=3（ゲート内rival 1件 + ゲート外2件）であり、
    # rivals 単体（=1）とは一致しない。
    was_tracking = tracker.state is TrackState.TRACKING
    r2 = tracker.update(
        [
            _point(66.0, 65.0, 0.0, 1000.0),  # 採用（距離5、ゲート内）
            _point(66.0, 70.0, 0.0, 1000.0),  # ゲート内 rival（距離10）
            _point(66.0, 200.0, 0.0, 1000.0),  # ゲート外（距離140）
            _point(66.0, 300.0, 0.0, 1000.0),  # ゲート外（距離240）
        ],
        frame_index=2,
        frame_seq=2,
        gap_before=0,
    )
    _accumulate(r2, was_tracking_before_call=was_tracking)
    assert r2.appended is not None
    assert r2.appended.rivals == 1
    assert r2.candidates == 4

    # frame3: レビューの再現ケースそのもの。全候補がゲート外 →
    # appended is None。TrackPoint が1つも作られないため rivals の
    # 事後合計では検出できないが、真の寄与は candidates=3。
    was_tracking = tracker.state is TrackState.TRACKING
    r3 = tracker.update(
        [
            _point(99.0, 500.0, 0.0, 1000.0),
            _point(99.0, 600.0, 0.0, 1000.0),
            _point(99.0, 700.0, 0.0, 1000.0),
        ],
        frame_index=3,
        frame_seq=3,
        gap_before=0,
    )
    _accumulate(r3, was_tracking_before_call=was_tracking)
    assert r3.appended is None
    assert r3.candidates == 3
    assert tracker.state is TrackState.TRACKING  # まだ欠測超過ではない

    naive_sum_of_rivals = sum(p.rivals for p in r3.track.points)

    # 正しい累計: 0(冷開始・対象外) + 0(frame1) + 3(frame2) + 3(frame3) = 6。
    assert correct_total == 6
    # rivals の事後合計では 1 にしかならず、真値を再現できないことを固定する。
    assert naive_sum_of_rivals == 1
    assert naive_sum_of_rivals != correct_total


# --------------------------------------------------------------------------
# 欠測許容とライフサイクル終了（要件 6.5）
# --------------------------------------------------------------------------


def test_missing_frames_up_to_limit_keep_tracking() -> None:
    config = _config(max_missing_frames=3)
    tracker = _tracker(config)
    tracker.update(
        [_point(0.0, 0.0, 0.0, 1000.0)], frame_index=0, frame_seq=0, gap_before=0
    )

    for i in range(1, 4):  # ちょうど3回の欠測
        result = tracker.update([], frame_index=i, frame_seq=i, gap_before=0)
        assert result.appended is None
        assert tracker.state is TrackState.TRACKING


def test_missing_frames_beyond_limit_end_tracking_with_lost() -> None:
    config = _config(max_missing_frames=3)
    tracker = _tracker(config)
    tracker.update(
        [_point(0.0, 0.0, 0.0, 1000.0)], frame_index=0, frame_seq=0, gap_before=0
    )

    for i in range(1, 4):
        tracker.update([], frame_index=i, frame_seq=i, gap_before=0)
    assert tracker.state is TrackState.TRACKING

    result = tracker.update([], frame_index=4, frame_seq=4, gap_before=0)

    assert tracker.state is TrackState.ENDED
    assert result.track.state is TrackState.ENDED
    assert result.track.end_reason is TrackEndReason.LOST


# --------------------------------------------------------------------------
# max_points（要件 6.9）
# --------------------------------------------------------------------------


def test_max_points_ends_tracking_within_the_same_update_call() -> None:
    """設計判断3: 上限に達した点を追加した同じ `update()` 呼び出し内で終了する。"""
    config = _config(max_points=3, max_step_mm=1000.0)
    tracker = _tracker(config)

    r0 = tracker.update(
        [_point(0.0, 0.0, 0.0, 1000.0)], frame_index=0, frame_seq=0, gap_before=0
    )
    assert r0.track.state is TrackState.TRACKING
    r1 = tracker.update(
        [_point(33.0, 100.0, 0.0, 1000.0)], frame_index=1, frame_seq=1, gap_before=0
    )
    assert r1.track.state is TrackState.TRACKING

    r2 = tracker.update(
        [_point(66.0, 200.0, 0.0, 1000.0)], frame_index=2, frame_seq=2, gap_before=0
    )

    assert r2.appended is not None
    assert len(r2.track.points) == 3
    assert r2.track.state is TrackState.ENDED
    assert r2.track.end_reason is TrackEndReason.MAX_POINTS
    assert tracker.state is TrackState.ENDED


# --------------------------------------------------------------------------
# finish() / reset()（明示的打ち切り）
# --------------------------------------------------------------------------


def test_finish_source_end_returns_final_track_with_reason() -> None:
    tracker = _tracker()
    tracker.update(
        [_point(0.0, 0.0, 0.0, 1000.0)], frame_index=0, frame_seq=0, gap_before=0
    )
    tracker.update(
        [_point(33.0, 100.0, 0.0, 1000.0)], frame_index=1, frame_seq=1, gap_before=0
    )

    track = tracker.finish(TrackEndReason.SOURCE_END)

    assert track.state is TrackState.ENDED
    assert track.end_reason is TrackEndReason.SOURCE_END
    assert len(track.points) == 2
    assert tracker.state is TrackState.ENDED


def test_finish_does_not_overwrite_an_existing_end_reason() -> None:
    """設計判断6: 既に ENDED の追跡へ `finish()` しても既存の理由を保つ。"""
    config = _config(max_missing_frames=0)
    tracker = _tracker(config)
    tracker.update(
        [_point(0.0, 0.0, 0.0, 1000.0)], frame_index=0, frame_seq=0, gap_before=0
    )
    tracker.update([], frame_index=1, frame_seq=1, gap_before=0)
    assert tracker.state is TrackState.ENDED

    track = tracker.finish(TrackEndReason.SOURCE_END)

    assert track.end_reason is TrackEndReason.LOST


def test_reset_returns_to_idle_and_next_track_gets_new_id() -> None:
    tracker = _tracker()
    first = tracker.update(
        [_point(0.0, 0.0, 0.0, 1000.0)], frame_index=0, frame_seq=0, gap_before=0
    )
    first_track_id = first.track.track_id
    tracker.finish(TrackEndReason.RESET)
    assert tracker.state is TrackState.ENDED

    tracker.reset()
    assert tracker.state is TrackState.IDLE

    second = tracker.update(
        [_point(500.0, 5.0, 5.0, 500.0)], frame_index=0, frame_seq=0, gap_before=0
    )

    assert second.track.track_id != first_track_id
    assert second.track.started_t_ms == 500.0
    assert len(second.track.points) == 1


# --------------------------------------------------------------------------
# 逐次結果・冪等性（要件 6.7, 9.1, 9.5）
# --------------------------------------------------------------------------


def test_every_update_call_returns_a_trackupdate_reflecting_current_state() -> None:
    tracker = _tracker(_config(max_missing_frames=2))
    r0 = tracker.update(
        [_point(0.0, 0.0, 0.0, 1000.0)], frame_index=0, frame_seq=0, gap_before=0
    )
    assert r0.track.state is TrackState.TRACKING  # 終了を待たずに逐次反映

    r1 = tracker.update([], frame_index=1, frame_seq=1, gap_before=0)
    assert r1.track.state is TrackState.TRACKING
    assert r1.appended is None


def test_update_after_ended_is_a_noop_and_never_raises() -> None:
    config = _config(max_missing_frames=0)
    tracker = _tracker(config)
    tracker.update(
        [_point(0.0, 0.0, 0.0, 1000.0)], frame_index=0, frame_seq=0, gap_before=0
    )
    tracker.update([], frame_index=1, frame_seq=1, gap_before=0)
    assert tracker.state is TrackState.ENDED
    points_before = tracker.update([], frame_index=2, frame_seq=2, gap_before=0).track.points

    result = tracker.update(
        [_point(999.0, 1.0, 1.0, 1.0)], frame_index=3, frame_seq=3, gap_before=0
    )

    assert result.appended is None
    assert result.track.points == points_before
    assert tracker.state is TrackState.ENDED


def test_rogue_erratic_trajectory_never_raises_and_is_treated_as_ordinary_points() -> None:
    """要件 9.1 / 9.5: 誤検出（人・手など）を追跡しても例外にしない。"""
    tracker = _tracker(_config(max_step_mm=80.0, max_missing_frames=50))
    erratic_xyz = [
        (0.0, 0.0, 1000.0),
        (500.0, -300.0, 200.0),
        (-9999.0, 4000.0, 50000.0),
        (0.0, 0.0, 0.0),
        (10.0, 10.0, 1000.0),
        (-10.0, -10.0, 990.0),
        (1e6, -1e6, 1e6),
    ]

    for i, (x, y, z) in enumerate(erratic_xyz):
        result = tracker.update(
            [_point(float(i) * 33.0, x, y, z)], frame_index=i, frame_seq=i, gap_before=0
        )
        assert result.candidates == 1

    assert tracker.state in (TrackState.TRACKING, TrackState.ENDED)


# --------------------------------------------------------------------------
# source / detector_kind の穴通し（要件 7.1, 7.2）
# --------------------------------------------------------------------------


def test_source_and_detector_kind_are_populated_and_threaded_through() -> None:
    tracker = SingleObjectTracker(_config(), SourceKind.RECORDED, "depth_band")

    update_result = tracker.update(
        [_point(0.0, 0.0, 0.0, 1000.0)], frame_index=0, frame_seq=0, gap_before=0
    )
    assert update_result.track.source is SourceKind.RECORDED
    assert update_result.track.detector_kind == "depth_band"

    finished = tracker.finish(TrackEndReason.SOURCE_END)
    assert finished.source is SourceKind.RECORDED
    assert finished.detector_kind == "depth_band"


# --------------------------------------------------------------------------
# gap_before / frame_index / frame_seq の受け渡し（要件 7.7, 10.3）
# --------------------------------------------------------------------------


def test_frame_identifiers_and_gap_before_are_threaded_onto_appended_point() -> None:
    tracker = _tracker()
    result = tracker.update(
        [_point(0.0, 0.0, 0.0, 1000.0)], frame_index=42, frame_seq=7, gap_before=2
    )

    assert result.appended is not None
    assert result.appended.frame_index == 42
    assert result.appended.frame_seq == 7
    assert result.appended.gap_before == 2


# --------------------------------------------------------------------------
# 境界の軽量ガード（このタスクの diff 内で自己完結する簡易チェック）
# --------------------------------------------------------------------------


def test_tracking_module_does_not_import_detection_or_projection() -> None:
    """`tracking.py` は `errors` / `types` / `config` と標準ライブラリのみを
    import すること（design.md「Dependency Direction」表の `tracking` 行）。

    フルの境界走査（`prediction_core` 等も含む）はタスク 8.2 の
    `test_boundaries.py` が持つ。ここでは本タスクが追加する自己の約束が
    最初から破れていないことだけを、軽量な正規表現で確認する。
    """
    text = (_SRC_ROOT / "tracking.py").read_text(encoding="utf-8")
    forbidden_pattern = re.compile(
        r"^\s*(import|from)\s+"
        r"(flying_object_tracking\.)?(detection|projection)\b",
        re.MULTILINE,
    )
    assert forbidden_pattern.search(text) is None

    cv2_pattern = re.compile(r"^\s*(import cv2\b|from cv2\b)", re.MULTILINE)
    assert cv2_pattern.search(text) is None

    numpy_pattern = re.compile(r"^\s*(import numpy\b|from numpy\b)", re.MULTILINE)
    assert numpy_pattern.search(text) is None
