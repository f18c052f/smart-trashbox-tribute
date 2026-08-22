"""候補・除外理由・カメラ座標系3D点・点列・追跡結果の受け渡し型（タスク 1.3）。

design.md「CoreTypes」節が定める通り、本モジュールは検出方式・入力元に
依存しない不変値だけを持つ。ロジック（絞り込み・逆投影・追跡）は
それぞれ後続レイヤ（`detection` / `projection` / `tracking`）が持ち、
ここでは定義しない（要件 3.2, 5.2, 5.4, 5.7, 7.1, 7.2, 7.3, 7.7, 9.4）。

**座標系を型で表明する。** `CameraPoint` / `CameraTrack` は `frame:
CoordinateFrame` フィールドを持ち、本 Spec が生成する値は常に
`CoordinateFrame.CAMERA` である。`CoordinateFrame` はメンバーが `CAMERA`
一つしか無い列挙型であり、列挙の語彙内で書く限り他の座標系を表現できない
（要件 5.2 / 5.3 / 7.3）。

**World 座標のフィールドをここに足さない。** `CameraTrack` に World 座標
（変換後の位置）のフィールドを足したくなる誘惑は必ず生じるが、足した瞬間
に「カメラ座標系までを持ち、World frame への変換を持たない」という本 Spec
の境界が消える（design.md「Boundary Commitments」/ requirements.md A-2）。
World 変換は `world-frame-calibration` が所有する `WorldTransform` の責務
であり、それを呼び出すのは `m1-prediction-validation` の結合層
（`seam.py`）である。本 Spec はカメラ座標系の点列を渡すところまでを持つ。

`SourceKind` は `sensing_foundation` の公開入口（`sensing_foundation`
パッケージの `__init__`）から**再エクスポートして使う**。同 Spec が
`prediction_core` からさらに再エクスポートしている値であり、本モジュールは
同義の列挙型を新規定義しない（design.md「Implementation Notes / Integration」）。

生成時にフィールドの妥当性を検証しない（`prediction_core` /
`sensing_foundation` の方針を踏襲）。検証は `PointEstimator` と
`SingleObjectTracker` の境界で行う。

依存方向（design.md「Dependency Direction」表の `types` 行）:
`errors` ＋ `numpy` ＋ `sensing_foundation`（公開入口のみ）だけを import
してよい。`prediction_core` を直接 import しない。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sensing_foundation import SourceKind

__all__ = [
    "HANDOFF_VERSION",
    "CoordinateFrame",
    "DetectorKind",
    "RejectReason",
    "PointFailureReason",
    "TrackState",
    "TrackEndReason",
    "Roi",
    "Candidate",
    "CandidateRejection",
    "CameraPoint",
    "PointFailure",
    "TrackPoint",
    "CameraTrack",
    "TrackUpdate",
    "SourceKind",
]

HANDOFF_VERSION: str = "1.0"
"""受け渡し形式の版番号。`CameraTrack.handoff_version` にそのまま格納する。

design.md「Revalidation Triggers」: この値の変更は下流
（`world-frame-calibration` / `m1-prediction-validation`）の結合再確認を
要求する。
"""


class CoordinateFrame(StrEnum):
    """3D点・点列が属する座標系（要件 5.2 / 5.3 / 7.3）。

    本 Spec が生成する値は常に `CAMERA` である。メンバーが `CAMERA` の
    一つだけであることが、「カメラ座標系以外を取り得ない」ことの型による
    保証そのものである（World frame の値を表現するメンバーを追加しない）。
    """

    CAMERA = "camera"
    """本 Spec が出す唯一の値。"""


class DetectorKind(StrEnum):
    """前景検出方式の識別子（design.md「L4-L5: 検出」節の `MaskBuilder`）。

    **本来は L4-L5（`detection/masks`）に属する列挙型だが、`config` 層
    （L2）の `DetectorConfig.kind` が既定値としてこの値を必要とし、かつ
    「Dependency Direction」表は左から右への import しか許さない
    （`Types --> Config` はあるが `Config --> Masks` の逆流は無い）。
    `config`（L2）と `detection/masks`（L4）の両方が import できる最小の
    層は `types`（L1）であるため、ここに置く。`detection/masks/*` は
    実装時にこのメンバーをそのまま使い、独自に再定義しない。
    """

    DEPTH_BAND = "depth_band"
    """ROI 内で距離帯に入る画素を前景とする。状態を持たない。"""
    FRAME_DIFF = "frame_diff"
    """直前フレームとの Depth 差分を前景とする。既定（暫定。OQ-26 の実測で確定する）。"""
    BACKGROUND = "background"
    """初期フレーム群から作った背景 Depth モデルとの差分を前景とする。"""


class RejectReason(StrEnum):
    """検出段で候補を除外した理由（要件 3.4 / 9.2）。"""

    TOO_SMALL_PX = "too_small_px"
    TOO_LARGE_PX = "too_large_px"
    OUT_OF_ROI = "out_of_roi"
    OUT_OF_DEPTH_BAND = "out_of_depth_band"
    SIZE_MISMATCH = "size_mismatch"
    """距離から導いた期待寸法と合わない。"""
    TOUCHES_ROI_EDGE = "touches_roi_edge"


class PointFailureReason(StrEnum):
    """候補が3D位置化に失敗した理由（要件 5.5）。"""

    INSUFFICIENT_VALID_PIXELS = "insufficient_valid_pixels"
    DEPTH_SPREAD_TOO_LARGE = "depth_spread_too_large"
    OUT_OF_DEPTH_BAND = "out_of_depth_band"


class TrackState(StrEnum):
    """単一物体追跡のライフサイクル状態（要件 6.1 / 6.5）。"""

    IDLE = "idle"
    TRACKING = "tracking"
    ENDED = "ended"


class TrackEndReason(StrEnum):
    """追跡が終了した理由（要件 6.5）。

    「なぜ点列がそこで切れたか」を明示することで、下流
    （`m1-prediction-validation`）が取りこぼしの原因を検出側か取得側かに
    切り分けられるようにする。
    """

    LOST = "lost"
    """欠測が許容フレーム数を超えた。"""
    SOURCE_END = "source_end"
    """入力が尽きた。"""
    MAX_POINTS = "max_points"
    """点数上限に達した。"""
    RESET = "reset"
    """呼び出し側の明示的な打ち切り。"""


@dataclass(frozen=True, slots=True)
class Roi:
    """処理対象範囲（画素矩形＋距離帯）（要件 2.1〜2.3）。"""

    x_px: int
    y_px: int
    width_px: int
    height_px: int
    z_min_mm: float | None
    z_max_mm: float | None


@dataclass(frozen=True, slots=True)
class Candidate:
    """検出段の出力。まだ3D位置を持たない（要件 3.1 / 3.2）。"""

    cx_px: float
    """全画面座標での重心（ROI オフセット加算済み。要件 2.5）。"""
    cy_px: float
    bbox_px: tuple[int, int, int, int]
    """全画面座標 (x, y, w, h)。"""
    area_px: int
    """前景画素数。"""
    valid_depth_px: int
    """bbox 内の有効 Depth 画素数。"""
    mask_score: float
    """方式ごとの前景らしさ（比較可能性のため 0..1 に正規化）。"""


@dataclass(frozen=True, slots=True)
class CandidateRejection:
    """除外理由ごとの件数（要件 3.4 / 9.2 / 9.3）。"""

    reason: RejectReason
    count: int


@dataclass(frozen=True, slots=True)
class CameraPoint:
    """カメラ座標系の3D点（要件 5.2 / 5.4 / 5.7）。"""

    frame: CoordinateFrame
    """常に `CoordinateFrame.CAMERA`。"""
    t_ms: float
    """上流の `t_capture_ms` をそのまま引き継ぐ（同一の時間基準。要件 5.7）。"""
    x_mm: float
    y_mm: float
    z_mm: float
    valid_depth_px: int
    depth_spread_mm: float
    """代表値まわりのばらつき（信頼度の材料。要件 9.4）。"""
    apparent_diameter_px: float
    expected_diameter_px: float
    """距離と `object_diameter_mm` から導いた期待値。"""
    intrinsics_source: str
    """3D位置の算出に用いたカメラパラメータの出所（例: `"stream_profile"`。要件 5.4）。"""


@dataclass(frozen=True, slots=True)
class PointFailure:
    """候補が3D位置化に失敗したことと、その理由（要件 5.5）。"""

    reason: PointFailureReason
    valid_depth_px: int


@dataclass(frozen=True, slots=True)
class TrackPoint:
    """点から元フレームへ辿るための識別情報を添えた1点（要件 7.7 / 10.3）。"""

    point: CameraPoint
    frame_index: int
    """`CaptureFrame.index`（セッション内の通し番号。要件 7.7）。"""
    frame_seq: int
    """`CaptureFrame.seq`（入力元が付けたフレーム番号。要件 7.7）。"""
    gap_before: int
    """この点の直前に上流が検出したフレーム番号の飛び（欠落数。要件 10.3）。"""
    rivals: int
    """同フレームでゲート内に居た他候補の数（要件 6.6 / 9.4）。"""


@dataclass(frozen=True, slots=True)
class CameraTrack:
    """1投擲ぶんの点列。本 Spec の唯一の受け渡し型（要件 7.1）。

    **World 座標のフィールドをここに足さない。** モジュール docstring に
    記載の通り、追加した瞬間に座標系の境界が消える。
    """

    handoff_version: str
    """`HANDOFF_VERSION` をそのまま格納する。"""
    frame: CoordinateFrame
    """常に `CoordinateFrame.CAMERA`。"""
    track_id: int
    started_t_ms: float
    """追跡開始時刻（要件 6.3）。"""
    points: tuple[TrackPoint, ...]
    state: TrackState
    end_reason: TrackEndReason | None
    source: SourceKind
    """上流の入力元種別（`sensing_foundation` からの再エクスポート。要件 11.5 の材料）。"""
    detector_kind: str
    """どの検出方式で得た点列か（比較のため）。"""


@dataclass(frozen=True, slots=True)
class TrackUpdate:
    """1フレーム処理後の逐次結果（要件 6.7）。"""

    track: CameraTrack
    """そのフレーム時点までの点列。"""
    appended: TrackPoint | None
    """このフレームで追加された点。追加が無かった場合は `None`。"""
    candidates: int
    rejections: tuple[CandidateRejection, ...]
    point_failures: tuple[PointFailure, ...]
