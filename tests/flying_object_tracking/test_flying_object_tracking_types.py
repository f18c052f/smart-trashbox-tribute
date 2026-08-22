"""受け渡し型と座標系の表明を検証する（タスク 1.3）。

design.md「CoreTypes」節が定める不変値群のうち、本テストが固定するのは
次の観測可能な完了状態である（タスク定義そのもの）。

- 生成した点列（`CameraTrack.points` を含む一連の型）の座標系フィールドが
  `CoordinateFrame.CAMERA` 以外を取り得ないこと。
  `CoordinateFrame` はメンバーが `CAMERA` 一つしか無いことで、これを
  型として保証する（列挙の語彙内で書く限り他の値を作れない）。
- 値等価が成立すること（`frozen=True` の dataclass は独立に構築しても
  フィールドが等しければ等しいと判定される）。

あわせて、全 dataclass が `frozen=True, slots=True` であること
（要件 7.1 / 7.2）、`SourceKind` が `sensing_foundation` からの再エクスポート
そのものであり新規定義でないこと（要件 7.4 の裏付け）、`TrackPoint` /
`CameraTrack` / `TrackUpdate` の入れ子タプルを含む往復構築を検証する。
"""

from __future__ import annotations

import dataclasses

import pytest
import sensing_foundation

from flying_object_tracking.types import (
    HANDOFF_VERSION,
    Candidate,
    CandidateRejection,
    CameraPoint,
    CameraTrack,
    CoordinateFrame,
    PointFailure,
    PointFailureReason,
    RejectReason,
    Roi,
    SourceKind,
    TrackEndReason,
    TrackPoint,
    TrackState,
    TrackUpdate,
)


# --- 各 dataclass の最小構築ヘルパー -----------------------------------


def make_roi(**overrides: object) -> Roi:
    kwargs: dict[str, object] = dict(
        x_px=0,
        y_px=0,
        width_px=640,
        height_px=480,
        z_min_mm=500.0,
        z_max_mm=5000.0,
    )
    kwargs.update(overrides)
    return Roi(**kwargs)  # type: ignore[arg-type]


def make_candidate(**overrides: object) -> Candidate:
    kwargs: dict[str, object] = dict(
        cx_px=100.0,
        cy_px=200.0,
        bbox_px=(90, 190, 20, 20),
        area_px=350,
        valid_depth_px=300,
        mask_score=0.8,
    )
    kwargs.update(overrides)
    return Candidate(**kwargs)  # type: ignore[arg-type]


def make_candidate_rejection(**overrides: object) -> CandidateRejection:
    kwargs: dict[str, object] = dict(reason=RejectReason.TOO_SMALL_PX, count=3)
    kwargs.update(overrides)
    return CandidateRejection(**kwargs)  # type: ignore[arg-type]


def make_camera_point(**overrides: object) -> CameraPoint:
    kwargs: dict[str, object] = dict(
        frame=CoordinateFrame.CAMERA,
        t_ms=1234.5,
        x_mm=10.0,
        y_mm=20.0,
        z_mm=1500.0,
        valid_depth_px=250,
        depth_spread_mm=12.5,
        apparent_diameter_px=18.0,
        expected_diameter_px=17.5,
        intrinsics_source="stream_profile",
    )
    kwargs.update(overrides)
    return CameraPoint(**kwargs)  # type: ignore[arg-type]


def make_point_failure(**overrides: object) -> PointFailure:
    kwargs: dict[str, object] = dict(
        reason=PointFailureReason.INSUFFICIENT_VALID_PIXELS, valid_depth_px=2
    )
    kwargs.update(overrides)
    return PointFailure(**kwargs)  # type: ignore[arg-type]


def make_track_point(**overrides: object) -> TrackPoint:
    kwargs: dict[str, object] = dict(
        point=make_camera_point(),
        frame_index=7,
        frame_seq=7,
        gap_before=0,
        rivals=0,
    )
    kwargs.update(overrides)
    return TrackPoint(**kwargs)  # type: ignore[arg-type]


def make_camera_track(**overrides: object) -> CameraTrack:
    kwargs: dict[str, object] = dict(
        handoff_version=HANDOFF_VERSION,
        frame=CoordinateFrame.CAMERA,
        track_id=1,
        started_t_ms=1000.0,
        points=(make_track_point(),),
        state=TrackState.TRACKING,
        end_reason=None,
        source=SourceKind.RECORDED,
        detector_kind="frame_diff",
    )
    kwargs.update(overrides)
    return CameraTrack(**kwargs)  # type: ignore[arg-type]


def make_track_update(**overrides: object) -> TrackUpdate:
    kwargs: dict[str, object] = dict(
        track=make_camera_track(),
        appended=make_track_point(),
        candidates=2,
        rejections=(make_candidate_rejection(),),
        point_failures=(make_point_failure(),),
    )
    kwargs.update(overrides)
    return TrackUpdate(**kwargs)  # type: ignore[arg-type]


ALL_DATACLASS_FACTORIES = (
    make_roi,
    make_candidate,
    make_candidate_rejection,
    make_camera_point,
    make_point_failure,
    make_track_point,
    make_camera_track,
    make_track_update,
)


# --- HANDOFF_VERSION -----------------------------------------------------


def test_handoff_version_is_a_string() -> None:
    """受け渡し形式の版番号は文字列で定義され、`"1.0"` である。"""
    assert isinstance(HANDOFF_VERSION, str)
    assert HANDOFF_VERSION == "1.0"


# --- frozen / slots --------------------------------------------------------


@pytest.mark.parametrize("factory", ALL_DATACLASS_FACTORIES, ids=lambda f: f.__name__)
def test_dataclass_is_frozen(factory) -> None:
    """すべての受け渡し型は不変（`frozen=True`）であり、構築後の変更を拒否する。"""
    instance = factory()
    field_name = dataclasses.fields(instance)[0].name
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(instance, field_name, getattr(instance, field_name))


@pytest.mark.parametrize("factory", ALL_DATACLASS_FACTORIES, ids=lambda f: f.__name__)
def test_dataclass_is_slotted(factory) -> None:
    """すべての受け渡し型は `slots=True` であり、`__dict__` を持たない。"""
    instance = factory()
    assert not hasattr(instance, "__dict__")


# --- 値等価 -----------------------------------------------------------------


@pytest.mark.parametrize("factory", ALL_DATACLASS_FACTORIES, ids=lambda f: f.__name__)
def test_dataclass_has_value_equality(factory) -> None:
    """独立に構築した同じフィールド値の2インスタンスは等価と判定される。"""
    first = factory()
    second = factory()
    assert first == second
    assert first is not second


def test_camera_track_with_different_track_id_is_not_equal() -> None:
    """値等価であって恒等性ではないことを、フィールド差分で確認する。"""
    a = make_camera_track(track_id=1)
    b = make_camera_track(track_id=2)
    assert a != b


# --- CoordinateFrame: カメラ座標系以外を取り得ないこと -----------------------


def test_coordinate_frame_has_exactly_one_member() -> None:
    """`CoordinateFrame` は `CAMERA` のみを持ち、列挙の語彙内では
    他の座標系を表現できない（本 Spec が生成する値は常にカメラ座標系）。
    """
    assert list(CoordinateFrame) == [CoordinateFrame.CAMERA]


def test_coordinate_frame_camera_value() -> None:
    assert CoordinateFrame.CAMERA.value == "camera"


def test_camera_point_frame_round_trips_as_camera() -> None:
    point = make_camera_point(frame=CoordinateFrame.CAMERA)
    assert point.frame is CoordinateFrame.CAMERA


def test_camera_track_frame_round_trips_as_camera() -> None:
    track = make_camera_track(frame=CoordinateFrame.CAMERA)
    assert track.frame is CoordinateFrame.CAMERA


def test_generated_track_points_frame_field_is_always_camera() -> None:
    """生成した点列の座標系フィールドがカメラ座標系以外を取り得ないこと
    （タスク 1.3 の観測可能な完了状態）。
    """
    track = make_camera_track(
        points=(
            make_track_point(point=make_camera_point()),
            make_track_point(point=make_camera_point(t_ms=1300.0)),
        )
    )
    assert track.frame is CoordinateFrame.CAMERA
    assert all(
        tp.point.frame is CoordinateFrame.CAMERA for tp in track.points
    )


# --- SourceKind: 再エクスポートであり新規定義でないこと ------------------------


def test_source_kind_is_the_upstream_sensing_foundation_symbol() -> None:
    """`flying_object_tracking.types.SourceKind` は `sensing_foundation` の
    公開入口が再エクスポートする、まさにそのオブジェクトである
    （同義の列挙型を新規定義していないことの表明）。
    """
    assert SourceKind is sensing_foundation.SourceKind


# --- 他の列挙型のメンバー ---------------------------------------------------


def test_reject_reason_members() -> None:
    assert {member.value for member in RejectReason} == {
        "too_small_px",
        "too_large_px",
        "out_of_roi",
        "out_of_depth_band",
        "size_mismatch",
        "touches_roi_edge",
    }


def test_point_failure_reason_members() -> None:
    assert {member.value for member in PointFailureReason} == {
        "insufficient_valid_pixels",
        "depth_spread_too_large",
        "out_of_depth_band",
    }


def test_track_state_members() -> None:
    assert {member.value for member in TrackState} == {
        "idle",
        "tracking",
        "ended",
    }


def test_track_end_reason_members() -> None:
    assert {member.value for member in TrackEndReason} == {
        "lost",
        "source_end",
        "max_points",
        "reset",
    }


# --- 構築の往復（入れ子タプルを含む） ---------------------------------------


def test_track_point_round_trip() -> None:
    point = make_camera_point()
    track_point = make_track_point(point=point, frame_index=42, frame_seq=41, gap_before=1, rivals=2)
    assert track_point.point is point
    assert track_point.frame_index == 42
    assert track_point.frame_seq == 41
    assert track_point.gap_before == 1
    assert track_point.rivals == 2


def test_camera_track_round_trip_with_nested_points_tuple() -> None:
    tp1 = make_track_point(frame_index=1, frame_seq=1)
    tp2 = make_track_point(frame_index=2, frame_seq=2)
    track = make_camera_track(points=(tp1, tp2), end_reason=TrackEndReason.LOST, state=TrackState.ENDED)
    assert track.points == (tp1, tp2)
    assert track.handoff_version == HANDOFF_VERSION
    assert track.end_reason is TrackEndReason.LOST
    assert track.state is TrackState.ENDED
    assert isinstance(track.source, SourceKind)


def test_track_update_round_trip_with_nested_tuples() -> None:
    rejection = make_candidate_rejection(reason=RejectReason.OUT_OF_ROI, count=5)
    failure = make_point_failure(reason=PointFailureReason.DEPTH_SPREAD_TOO_LARGE, valid_depth_px=1)
    appended = make_track_point()
    update = make_track_update(
        appended=appended,
        candidates=4,
        rejections=(rejection,),
        point_failures=(failure,),
    )
    assert update.appended is appended
    assert update.candidates == 4
    assert update.rejections == (rejection,)
    assert update.point_failures == (failure,)
    assert isinstance(update.track, CameraTrack)


def test_track_update_appended_may_be_none() -> None:
    """そのフレームで追加が無かった場合、`appended` は `None` を取れる。"""
    update = make_track_update(appended=None)
    assert update.appended is None


def test_roi_z_bounds_are_optional() -> None:
    """ROI の距離帯は未指定（`None`）を許容する（フレーム全体・距離無制限）。"""
    roi = make_roi(z_min_mm=None, z_max_mm=None)
    assert roi.z_min_mm is None
    assert roi.z_max_mm is None
