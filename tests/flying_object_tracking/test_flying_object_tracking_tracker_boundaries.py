"""`SingleObjectTracker` の境界値と決定性の検証（タスク 4.2）。

タスク 4.1（`test_flying_object_tracking_tracker.py`）が対応付け規則・
タイブレーク・ライフサイクル全般を広く固定したのに対し、本ファイルは
design.md「L6-L7: 追跡と統合 → SingleObjectTracker」が定める2つのしきい値
（`max_missing_frames` / `max_step_mm`）の**厳密な境界**と、要件 11.3 が
求める**決定性**にだけ焦点を絞る（要件 6.5, 6.6, 6.7, 11.3）。

## `max_step_mm` の境界方向についての確認（設計判断の再確認、実装変更なし）

design.md 1162行目は「予測位置から `max_step_mm` **以内**の点を候補とし」
と書いており、日本語の「以内」は境界値そのものを含む（inclusive）。
`src/flying_object_tracking/tracking.py` の `_try_associate` を確認すると、
実際のゲート判定は

```python
if distance <= self._config.max_step_mm:
    gated.append((p, distance))
```

であり、`<=`（inclusive）である。**design.md の記述と実装は一致しており、
矛盾は無い。** 本ファイルはこの一致を境界値ちょうどのケースで固定する
（`test_max_step_mm_boundary_is_inclusive`）。矛盾があった場合に備えて
このファイルは実装を変更せず、確認結果だけをテストとして残す。
"""

from __future__ import annotations

import pytest

from flying_object_tracking.config import TrackerConfig
from flying_object_tracking.tracking import SingleObjectTracker
from flying_object_tracking.types import (
    CameraPoint,
    CameraTrack,
    CoordinateFrame,
    SourceKind,
    TrackEndReason,
    TrackState,
    TrackUpdate,
)

# --------------------------------------------------------------------------
# ヘルパー（test_flying_object_tracking_tracker.py と同じ形）
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


# ==========================================================================
# 1. max_missing_frames の境界値（要件 6.5）
# ==========================================================================
#
# design.md 手順1: 「点が0個なら欠測を1つ数える。max_missing_frames を
# 超えたら ENDED(LOST)」。「超えたら」なので、欠測フレーム数が
# ちょうど max_missing_frames のときはまだ継続し、max_missing_frames + 1
# 個目でちょうど終了する。この非対称な境界（>、超過であって以上ではない）
# を複数のしきい値で固定する。


@pytest.mark.parametrize("max_missing_frames", [0, 1, 3, 10])
def test_missing_frames_exactly_at_limit_still_tracking(
    max_missing_frames: int,
) -> None:
    """欠測フレーム数がちょうど `max_missing_frames` の間は TRACKING を継続する。"""
    tracker = _tracker(_config(max_missing_frames=max_missing_frames))
    tracker.update(
        [_point(0.0, 0.0, 0.0, 1000.0)], frame_index=0, frame_seq=0, gap_before=0
    )
    assert tracker.state is TrackState.TRACKING

    for i in range(1, max_missing_frames + 1):
        result = tracker.update([], frame_index=i, frame_seq=i, gap_before=0)
        assert result.appended is None
        # ちょうど max_missing_frames 回目までは、まだ「超えて」いない。
        assert tracker.state is TrackState.TRACKING, (
            f"max_missing_frames={max_missing_frames} のとき、"
            f"{i}回目の欠測でまだ TRACKING のはず"
        )
        assert result.track.state is TrackState.TRACKING
        assert result.track.end_reason is None


@pytest.mark.parametrize("max_missing_frames", [0, 1, 3, 10])
def test_missing_frames_one_beyond_limit_ends_tracking_on_that_call(
    max_missing_frames: int,
) -> None:
    """`max_missing_frames + 1` 個目の欠測フレームで、その呼び出し内で
    ちょうど ENDED(LOST) に遷移する（1回早くも遅くもない）。
    """
    tracker = _tracker(_config(max_missing_frames=max_missing_frames))
    tracker.update(
        [_point(0.0, 0.0, 0.0, 1000.0)], frame_index=0, frame_seq=0, gap_before=0
    )

    # ちょうど max_missing_frames 回、境界まで欠測を送る（まだ継続する）。
    for i in range(1, max_missing_frames + 1):
        result = tracker.update([], frame_index=i, frame_seq=i, gap_before=0)
        assert tracker.state is TrackState.TRACKING

    # +1 個目のこの呼び出しで、まさにこの呼び出し内に ENDED(LOST) へ遷移する。
    boundary_index = max_missing_frames + 1
    final_result = tracker.update(
        [], frame_index=boundary_index, frame_seq=boundary_index, gap_before=0
    )

    assert tracker.state is TrackState.ENDED
    assert final_result.track.state is TrackState.ENDED
    assert final_result.track.end_reason is TrackEndReason.LOST
    assert final_result.appended is None

    # 境界を超えた後の追加の欠測呼び出しは状態を変えない（冪等）。
    after = tracker.update(
        [], frame_index=boundary_index + 1, frame_seq=boundary_index + 1, gap_before=0
    )
    assert after.track.state is TrackState.ENDED
    assert after.track.end_reason is TrackEndReason.LOST


def test_missing_frames_boundary_logic_is_not_hardcoded_for_a_single_value() -> None:
    """同一テスト内で複数のしきい値を切り替え、境界ロジックが特定の
    max_missing_frames 値専用にハードコードされていないことを重ねて確認する。
    """
    for max_missing_frames in (0, 1, 2, 5):
        tracker = _tracker(_config(max_missing_frames=max_missing_frames))
        tracker.update(
            [_point(0.0, 0.0, 0.0, 1000.0)], frame_index=0, frame_seq=0, gap_before=0
        )
        for i in range(1, max_missing_frames + 1):
            tracker.update([], frame_index=i, frame_seq=i, gap_before=0)
        assert tracker.state is TrackState.TRACKING, max_missing_frames

        boundary_index = max_missing_frames + 1
        result = tracker.update(
            [], frame_index=boundary_index, frame_seq=boundary_index, gap_before=0
        )
        assert tracker.state is TrackState.ENDED, max_missing_frames
        assert result.track.end_reason is TrackEndReason.LOST, max_missing_frames


# ==========================================================================
# 2. max_step_mm の境界値（要件 6.6）
# ==========================================================================
#
# design.md「予測位置から max_step_mm 以内の点を候補とし」の「以内」は
# 境界値を含む（inclusive）。実装（`distance <= max_step_mm`）は inclusive
# であり、design.md の記述と一致する（ファイル冒頭の docstring 参照）。
# 予測位置は1点しか無い状態（縮退ゲート＝直前点そのもの）を使い、境界距離
# が浮動小数点で厳密に表現できる値になるよう軸に沿ったオフセットだけを
# 使う（500.0 のようなオフセットの2乗・平方根は float64 で正確に往復する）。


def test_max_step_mm_boundary_is_inclusive_point_exactly_at_limit_is_accepted() -> None:
    """予測位置からちょうど max_step_mm の距離にある点は受理される（inclusive）。"""
    max_step_mm = 500.0
    tracker = _tracker(_config(max_step_mm=max_step_mm, max_missing_frames=10))
    tracker.update(
        [_point(0.0, 0.0, 0.0, 1000.0)], frame_index=0, frame_seq=0, gap_before=0
    )
    # 予測位置は縮退ゲートにより直前点そのもの (0, 0, 1000)。
    boundary_point = _point(33.0, max_step_mm, 0.0, 1000.0)  # 距離 = ちょうど500.0

    result = tracker.update(
        [boundary_point], frame_index=1, frame_seq=1, gap_before=0
    )

    assert result.appended is not None
    assert result.appended.point == boundary_point
    assert tracker.state is TrackState.TRACKING


def test_max_step_mm_boundary_plus_epsilon_is_rejected() -> None:
    """境界距離をわずかに超える点は棄却され、欠測として計上される。"""
    max_step_mm = 500.0
    tracker = _tracker(_config(max_step_mm=max_step_mm, max_missing_frames=10))
    tracker.update(
        [_point(0.0, 0.0, 0.0, 1000.0)], frame_index=0, frame_seq=0, gap_before=0
    )
    beyond_point = _point(33.0, max_step_mm + 0.5, 0.0, 1000.0)  # 距離 = 500.5

    result = tracker.update(
        [beyond_point], frame_index=1, frame_seq=1, gap_before=0
    )

    assert result.appended is None
    assert len(result.track.points) == 1  # 開始点のみ。beyond_point は追加されない
    # 全候補がゲート外 → 欠測として計上される（設計判断2）が、
    # max_missing_frames=10 のためまだ TRACKING を継続する。
    assert tracker.state is TrackState.TRACKING


def test_max_step_mm_boundary_direction_flips_exactly_at_the_limit() -> None:
    """同一の距離量から1つだけ違う2つの候補で、採否がちょうど境界で切り替わる
    ことを1つのテストで対比させて固定する。
    """
    max_step_mm = 250.0

    tracker_at = _tracker(_config(max_step_mm=max_step_mm, max_missing_frames=10))
    tracker_at.update(
        [_point(0.0, 0.0, 0.0, 1000.0)], frame_index=0, frame_seq=0, gap_before=0
    )
    at_limit = _point(33.0, 0.0, max_step_mm, 1000.0)  # y 軸オフセット。距離=250.0
    result_at = tracker_at.update([at_limit], frame_index=1, frame_seq=1, gap_before=0)
    assert result_at.appended is not None

    tracker_over = _tracker(_config(max_step_mm=max_step_mm, max_missing_frames=10))
    tracker_over.update(
        [_point(0.0, 0.0, 0.0, 1000.0)], frame_index=0, frame_seq=0, gap_before=0
    )
    just_over = _point(33.0, 0.0, max_step_mm + 0.1, 1000.0)  # 距離=250.1
    result_over = tracker_over.update(
        [just_over], frame_index=1, frame_seq=1, gap_before=0
    )
    assert result_over.appended is None


# ==========================================================================
# 3. 決定性（要件 11.3）
# ==========================================================================
#
# 欠測フレーム（ギャップ）を1つ、ゲート境界に近い点を1つ含む、単調な直線
# 軌道ではない現実的な軌道を、2つの独立に構築した SingleObjectTracker へ
# 同一の入力（同一の frame_index / frame_seq / gap_before）で流し、
# (a) 最終的な CameraTrack が完全一致すること
# (b) 毎回の update() が返す TrackUpdate の列が完全一致すること
# の両方を固定する。CameraTrack / TrackPoint / CameraPoint は
# frozen dataclass で値等価なので、直接の == 比較で厳密一致を検証できる。

# 各要素: (frame_index, frame_seq, gap_before, [(t_ms, x_mm, y_mm, z_mm), ...])
_DETERMINISM_TRAJECTORY: tuple[tuple[int, int, int, tuple[tuple[float, float, float, float], ...]], ...] = (
    (0, 0, 0, ((0.0, 0.0, 0.0, 1000.0),)),
    (1, 1, 0, ((33.0, 100.0, 0.0, 1000.0),)),
    (2, 2, 0, ()),  # 欠測フレーム（ギャップ）
    (3, 3, 1, ((99.0, 203.0, 0.0, 1000.0),)),  # gap_before=1 で直前の欠落を引き継ぐ
    (4, 4, 0, ((132.0, 455.0, 0.0, 1000.0),)),  # ゲート境界に近い点（予測(306,0,1000)から距離149、gate=150）
    (5, 5, 0, ()),
    (6, 6, 0, ()),
    (7, 7, 0, ()),  # ここで欠測が max_missing_frames=2 を超え ENDED(LOST)
)

_DETERMINISM_CONFIG = _config(
    max_step_mm=150.0, max_missing_frames=2, min_points_to_start=1, max_points=120
)


def _materialize_frame_points(
    raw_points: tuple[tuple[float, float, float, float], ...]
) -> list[CameraPoint]:
    """生のタプルから、その都度**新しい** CameraPoint インスタンスを作る。

    決定性テストで2つの独立したトラッカー実行に「同じオブジェクト参照」
    ではなく「同じ値」の点を渡すことを保証するため（値等価性そのものが
    テスト対象であって、参照の使い回しでごまかさない）。
    """
    return [_point(t, x, y, z) for (t, x, y, z) in raw_points]


def _run_trajectory(config: TrackerConfig) -> tuple[SingleObjectTracker, list[TrackUpdate]]:
    tracker = _tracker(config)
    updates: list[TrackUpdate] = []
    for frame_index, frame_seq, gap_before, raw_points in _DETERMINISM_TRAJECTORY:
        points = _materialize_frame_points(raw_points)
        result = tracker.update(
            points, frame_index=frame_index, frame_seq=frame_seq, gap_before=gap_before
        )
        updates.append(result)
    return tracker, updates


def test_determinism_same_input_sequence_yields_identical_final_track() -> None:
    """同一の点列を2回処理すると、同一の点列と同一の終了理由が得られる（要件 11.3）。"""
    tracker_a, _updates_a = _run_trajectory(_DETERMINISM_CONFIG)
    tracker_b, _updates_b = _run_trajectory(_DETERMINISM_CONFIG)

    track_a = tracker_a.finish(TrackEndReason.SOURCE_END)
    track_b = tracker_b.finish(TrackEndReason.SOURCE_END)

    # finish() は既に ENDED(LOST) を上書きしない（設計判断6）ため、
    # 両方とも実際に発生した LOST のまま一致するはず。
    assert track_a.end_reason is TrackEndReason.LOST
    assert track_b.end_reason is TrackEndReason.LOST

    assert isinstance(track_a, CameraTrack)
    assert isinstance(track_b, CameraTrack)
    assert track_a == track_b  # frozen dataclass の値等価性による完全一致
    assert track_a.points == track_b.points
    assert track_a.started_t_ms == track_b.started_t_ms
    assert track_a.state == track_b.state
    assert track_a.end_reason == track_b.end_reason
    assert [p.point for p in track_a.points] == [p.point for p in track_b.points]


def test_determinism_per_call_trackupdate_sequence_is_identical() -> None:
    """最終状態だけでなく、毎回の update() 呼び出しが返す TrackUpdate の
    列そのものが2回の実行で完全一致することを確認する（最終状態比較より
    強い決定性の主張）。
    """
    _tracker_a, updates_a = _run_trajectory(_DETERMINISM_CONFIG)
    _tracker_b, updates_b = _run_trajectory(_DETERMINISM_CONFIG)

    assert len(updates_a) == len(updates_b) == len(_DETERMINISM_TRAJECTORY)

    for i, (update_a, update_b) in enumerate(zip(updates_a, updates_b)):
        assert update_a == update_b, f"frame index {i} で TrackUpdate が食い違った"
        assert update_a.appended == update_b.appended
        assert update_a.candidates == update_b.candidates
        assert update_a.track == update_b.track
        assert update_a.track.points == update_b.track.points


def test_determinism_holds_across_three_independent_runs() -> None:
    """2回に限らず3回実行しても揺れないことを重ねて確認する。"""
    tracks = []
    for _ in range(3):
        tracker, _updates = _run_trajectory(_DETERMINISM_CONFIG)
        tracks.append(tracker.finish(TrackEndReason.SOURCE_END))

    assert tracks[0] == tracks[1] == tracks[2]


# ==========================================================================
# 4. 途中経過の逐次取り出しの正確性（要件 6.7）
# ==========================================================================
#
# TrackUpdate.track.points がそのフレーム時点までに追加された点だけを
# 含み、直前フレームの点が欠けたり、まだ追加されていない未来の点が
# 混入したりしないことを、決定性テストと同じ軌道の複数の時点で固定する。


def test_sequential_snapshot_contains_exactly_points_appended_so_far() -> None:
    tracker = _tracker(_DETERMINISM_CONFIG)
    updates: list[TrackUpdate] = []

    for frame_index, frame_seq, gap_before, raw_points in _DETERMINISM_TRAJECTORY:
        points = _materialize_frame_points(raw_points)
        result = tracker.update(
            points, frame_index=frame_index, frame_seq=frame_seq, gap_before=gap_before
        )
        updates.append(result)

    # frame_index=0: 開始点1つだけ。
    assert len(updates[0].track.points) == 1
    assert updates[0].track.points[0].point == _point(0.0, 0.0, 0.0, 1000.0)
    assert updates[0].appended is not None
    assert updates[0].track.points[-1] == updates[0].appended

    # frame_index=1: 2点目が追加され、直前の点はそのまま残る（消えない）。
    assert len(updates[1].track.points) == 2
    assert updates[1].track.points[0].point == _point(0.0, 0.0, 0.0, 1000.0)
    assert updates[1].track.points[1].point == _point(33.0, 100.0, 0.0, 1000.0)
    assert updates[1].appended is not None
    assert updates[1].track.points[-1] == updates[1].appended

    # frame_index=2: 欠測フレーム。appended is None であり、
    # スナップショットの点数は frame1 のときから変化しない（未来の点が
    # 混入しない・今回追加されるべき点が欠けてもいない）。
    assert updates[2].appended is None
    assert len(updates[2].track.points) == 2
    assert updates[2].track.points == updates[1].track.points

    # frame_index=3: 3点目が追加され、それ以前の2点はそのまま。
    assert len(updates[3].track.points) == 3
    assert updates[3].track.points[:2] == updates[1].track.points
    assert updates[3].appended is not None
    assert updates[3].track.points[2] == updates[3].appended
    assert updates[3].track.points[2].point == _point(99.0, 203.0, 0.0, 1000.0)

    # frame_index=4: 4点目（ゲート境界に近い点）が追加される。
    # このフレームで「今追加された点」が末尾に確実に含まれ、かつ末尾以外の
    # 過去3点がそのまま保たれていることを確認する（追加のタイミングに
    # off-by-one が無いことの直接的な証拠）。
    assert len(updates[4].track.points) == 4
    assert updates[4].track.points[:3] == updates[3].track.points
    assert updates[4].appended is not None
    assert updates[4].track.points[3] == updates[4].appended
    assert updates[4].track.points[3].point == _point(132.0, 455.0, 0.0, 1000.0)

    # frame_index=5, 6: 欠測が続く間、点数は4のまま変化しない。
    assert len(updates[5].track.points) == 4
    assert updates[5].track.points == updates[4].track.points
    assert len(updates[6].track.points) == 4
    assert updates[6].track.points == updates[4].track.points

    # frame_index=7: 欠測が max_missing_frames=2 を超えて ENDED(LOST) に
    # なるが、既に確定した点列そのものは変化しない（終了は点列を書き換え
    # ない）。
    assert updates[7].appended is None
    assert len(updates[7].track.points) == 4
    assert updates[7].track.points == updates[4].track.points
    assert updates[7].track.state is TrackState.ENDED
    assert updates[7].track.end_reason is TrackEndReason.LOST


def test_sequential_snapshot_never_includes_a_point_not_yet_appended() -> None:
    """未来の点がまだ混入していないことを、軌道の各段階で明示的に確認する。

    決定性テストの軌道全体をあらかじめ知っている状態で、各 update() 直後の
    スナップショットの点数が「これまでに appended != None だった回数」と
    厳密に一致することをフレームごとに検証する（1つでもタイミングがずれる
    と失敗する強いアサーション）。
    """
    tracker = _tracker(_DETERMINISM_CONFIG)
    expected_appended_count = 0

    for frame_index, frame_seq, gap_before, raw_points in _DETERMINISM_TRAJECTORY:
        points = _materialize_frame_points(raw_points)
        result = tracker.update(
            points, frame_index=frame_index, frame_seq=frame_seq, gap_before=gap_before
        )
        if result.appended is not None:
            expected_appended_count += 1
        assert len(result.track.points) == expected_appended_count, (
            f"frame_index={frame_index} でスナップショットの点数が食い違った"
        )
        if result.appended is not None:
            # 今回追加された点が、スナップショットの末尾そのものであること
            # （追加が反映される前の古いスナップショットを返していない）。
            assert result.track.points[-1] == result.appended
