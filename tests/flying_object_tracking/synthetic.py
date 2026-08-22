"""既知の3D軌道から Depth フレーム列を生成する合成器（タスク 1.5。テストツリー専用）。

本モジュールは `src/flying_object_tracking/**` の一部ではない。design.md
「File Structure Plan」が `tests/flying_object_tracking/synthetic.py` として
明示する通り、これは**テストが使う入力データ生成器**であり、本体のロジック
（検出・逆投影・追跡）を一切持たない。

**上流の逆投影と正確に対になる forward projection でなければならない**。
後続タスク（3.1 の `PointEstimator`、3.2 の候補抽出、6.3 の
`test_projection.py` 往復テスト）は、「本合成器が置いた3D点 → 検出 →
`PointEstimator`（= `sensing_foundation.deproject_pixel` を呼ぶ）→ 復元した
3D点」が既知の許容誤差内で一致することを検証する。もし本合成器が上流と異なる
画素中心規約・`depth_scale_mm` の適用位置を使うと、その往復テストは
サイレントに壊れ、原因不明の誤差として現れる。したがって本モジュールは
`sensing_foundation.geometry.deproject_pixel` の逆演算を**手で愚直に**書き、
上流と同じ規約（画素中心規約: 整数座標 `(u, v)` はその画素の中心を指し、
`+0.5` 補正を加えない）に厳密に従う:

    deproject_pixel:  x_mm = (u_px - ppx_px) / fx_px * z_mm
                       y_mm = (v_px - ppy_px) / fy_px * z_mm

    その逆演算（本モジュールの forward projection）:
                       u_px = ppx_px + x_mm / z_mm * fx_px
                       v_px = ppy_px + y_mm / z_mm * fy_px

mm 換算も同様に、上流 `depth_raw_to_mm(raw, depth_scale_mm) = raw *
depth_scale_mm` の逆演算 `raw = round(z_mm / depth_scale_mm)` を用いる。
`depth_scale_mm` は `StreamProfile` から読み取るパラメータであり、本モジュール
内に固定値としては埋め込まない（呼び出し側が `StreamProfile.depth_scale_mm`
を選ぶ。テストの既定は `sensing_foundation` 自身のテスト規約に合わせて
`1.0`——「1 生カウント = 1 mm」——を使うことが多いが、これは呼び出し側の
選択であり、本モジュールが強制する値ではない）。

**塊（blob）の描き方**: 3D中心点を forward projection で画素位置
`(u_px, v_px)` に写像し、対象物の実寸法 `diameter_mm` と距離 `z_mm` から
`expected_diameter_px = fx_px * diameter_mm / z_mm` を算出し、その画素直径の
円盤を `(u_px, v_px)` を中心に描く。円盤内部の Depth 値は、3D中心点の
`z_mm` から求めた raw カウント（一定値）で塗る。円盤外は静止背景値で埋める。

**静止背景・無効画素・フレーム欠落の注入**: 静止背景値（raw カウント）は
呼び出し側が指定する。無効画素（`INVALID_DEPTH_RAW = 0`）は座標を指定して
個別に注入できる。フレーム欠落は、軌道上の特定のインデックスを「生成しない」
ことで表現し、後続フレームの `seq` が飛ぶ（`gap_before` に反映される）形で
観測できるようにする——`sensing_foundation.source.BaseFrameSource._detect_gap`
と同じ計算式（`diff - 1 if diff > 1 else 0`）をそのまま用いる。

**このモジュールを使うテストファイルへの注意**: `tests/` 配下は
パッケージ化されていない（`__init__.py` が無い）ため、モジュール名は
セッション全体でフラットな `sys.modules` 名前空間を共有する。
`tests/sensing_foundation/synthetic.py` が既に同じベース名 `synthetic.py` で
存在するため、素朴な `from synthetic import ...`（bare import）は pytest の
コレクション順序次第でどちらのファイルが `sys.modules["synthetic"]` に入るかが
決まってしまい、他方の Spec のテストを壊しうる。本ファイルを bare import
しないこと。**`tests/flying_object_tracking/conftest.py` の
`synthetic_module` フィクスチャ経由でのみアクセスすること**
（同 conftest のモジュール docstring「`synthetic_module` / `fakes_module`
フィクスチャ」に回避策と理由が集約されている）。
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

import numpy as np
from sensing_foundation import (
    INVALID_DEPTH_RAW,
    CameraIntrinsics,
    CaptureFrame,
    SourceKind,
    StreamProfile,
    TimestampDomain,
)

__all__ = [
    "TrajectoryPoint",
    "expected_diameter_px",
    "make_depth_frame",
    "project_to_pixel",
    "raw_from_z_mm",
    "synthesize_frames",
]


@dataclass(frozen=True, slots=True)
class TrajectoryPoint:
    """既知の3D軌道上の1点。カメラ座標系・mm・ms（本 Spec の単位規約）。"""

    t_ms: float
    x_mm: float
    y_mm: float
    z_mm: float


def project_to_pixel(intrinsics: CameraIntrinsics, x_mm: float, y_mm: float, z_mm: float) -> tuple[float, float]:
    """カメラ座標系の3D点を画素位置へ写像する。

    `sensing_foundation.geometry.deproject_pixel` の厳密な逆演算である
    （モジュール docstring 参照）。画素中心規約に従い `+0.5` 補正は加えない。
    """
    u_px = intrinsics.ppx_px + x_mm / z_mm * intrinsics.fx_px
    v_px = intrinsics.ppy_px + y_mm / z_mm * intrinsics.fy_px
    return u_px, v_px


def expected_diameter_px(intrinsics: CameraIntrinsics, diameter_mm: float, z_mm: float) -> float:
    """距離 `z_mm` における対象物（実寸 `diameter_mm`）の期待画素直径。"""
    return intrinsics.fx_px * diameter_mm / z_mm


def raw_from_z_mm(z_mm: float, depth_scale_mm: float) -> int:
    """`sensing_foundation.geometry.depth_raw_to_mm` の逆演算。

    `depth_raw_to_mm(raw, depth_scale_mm) = raw * depth_scale_mm` の逆であり、
    `round(z_mm / depth_scale_mm)` を返す。`uint16` の範囲へ収める。
    """
    raw = round(z_mm / depth_scale_mm)
    return max(0, min(65535, raw))


def make_depth_frame(
    profile: StreamProfile,
    point: TrajectoryPoint,
    diameter_mm: float,
    *,
    background_raw: int = 4000,
    invalid_pixels: Sequence[tuple[int, int]] = (),
) -> np.ndarray:
    """1つの3D点から、対象物の塊を描いた `uint16` Depth 配列を1枚生成する。

    Args:
        profile: `intrinsics` と `depth_scale_mm` の取得元。`intrinsics` が
            `None` の場合は forward projection ができないため `ValueError`。
        point: 塊の中心となる3D点（カメラ座標系）。
        diameter_mm: 対象物の実寸法（mm）。
        background_raw: 塊の外を埋める静止背景の raw カウント値。
        invalid_pixels: 生成後に `INVALID_DEPTH_RAW` で上書きする
            `(x_px, y_px)`（全画面画素座標）の列。塊・背景を問わず上書きする。

    Returns:
        `uint16`、shape=`(profile.height_px, profile.width_px)` の配列。
        読み取り専用化は行わない（`CaptureFrame` 構築時の責務）。

    Raises:
        ValueError: `profile.intrinsics` が `None` の場合。
    """
    intrinsics = profile.intrinsics
    if intrinsics is None:
        raise ValueError(
            "profile.intrinsics が None のため forward projection ができない。"
            "合成器へ渡す StreamProfile には CameraIntrinsics を設定すること。"
        )

    depth = np.full((profile.height_px, profile.width_px), background_raw, dtype=np.uint16)

    u_px, v_px = project_to_pixel(intrinsics, point.x_mm, point.y_mm, point.z_mm)
    diam_px = expected_diameter_px(intrinsics, diameter_mm, point.z_mm)
    radius_px = diam_px / 2.0
    raw = raw_from_z_mm(point.z_mm, profile.depth_scale_mm)

    yy, xx = np.ogrid[0 : profile.height_px, 0 : profile.width_px]
    blob_mask = (xx - u_px) ** 2 + (yy - v_px) ** 2 <= radius_px**2
    depth[blob_mask] = np.uint16(raw)

    for x_px, y_px in invalid_pixels:
        depth[y_px, x_px] = INVALID_DEPTH_RAW

    return depth


def synthesize_frames(
    trajectory: Sequence[TrajectoryPoint],
    profile: StreamProfile,
    diameter_mm: float,
    *,
    background_raw: int = 4000,
    invalid_pixels_by_index: Mapping[int, Sequence[tuple[int, int]]] | None = None,
    dropped_indices: Collection[int] = (),
    source: SourceKind = SourceKind.SIMULATED,
    timestamp_domain: TimestampDomain = TimestampDomain.UNKNOWN,
) -> list[CaptureFrame]:
    """既知の3D軌道から `CaptureFrame` 列を合成する（本モジュールの主入口）。

    軌道上の各点を1フレームに対応させ、`make_depth_frame` で塊を描いた
    Depth 配列を持つ `CaptureFrame` を組み立てる。`dropped_indices` に含まれる
    軌道インデックスはフレームとして生成しない（フレーム欠落の注入）。
    欠落は `seq` の飛びとして表現され、`gap_before` は
    `sensing_foundation.source.BaseFrameSource._detect_gap` と同じ計算式
    （`diff - 1 if diff > 1 else 0`）で算出する。

    Args:
        trajectory: 既知の3D軌道（時刻付き）。空でないこと。
        profile: 各フレームに付与する取得設定。`intrinsics` が必要
            （`make_depth_frame` 参照）。
        diameter_mm: 対象物の実寸法（mm）。固定値として埋め込まず、
            呼び出し側が渡すパラメータとする。
        background_raw: 静止背景の raw カウント値。
        invalid_pixels_by_index: 軌道インデックス → 無効化する画素座標の列。
            指定の無いインデックスには無効画素を注入しない。
        dropped_indices: フレームとして生成しない軌道インデックスの集合。
        source: 生成する `CaptureFrame.source` に使う入力元種別。
        timestamp_domain: 生成する `CaptureFrame.timestamp_domain`。

    Returns:
        `CaptureFrame` のリスト。`index` は 0 から欠番なく増加し
        （実際に生成されたフレームの通し番号）、`seq` は軌道インデックスに
        由来する飛びを含みうる。

    Raises:
        ValueError: `trajectory` が空の場合。
    """
    if not trajectory:
        raise ValueError("trajectory は空であってはならない")

    invalid_pixels_by_index = invalid_pixels_by_index or {}

    frames: list[CaptureFrame] = []
    out_index = 0
    last_emitted_seq: int | None = None

    for traj_index, point in enumerate(trajectory):
        seq = traj_index
        if traj_index in dropped_indices:
            continue

        depth = make_depth_frame(
            profile,
            point,
            diameter_mm,
            background_raw=background_raw,
            invalid_pixels=invalid_pixels_by_index.get(traj_index, ()),
        )

        if last_emitted_seq is None:
            gap_before = 0
        else:
            diff = seq - last_emitted_seq
            gap_before = diff - 1 if diff > 1 else 0

        frames.append(
            CaptureFrame(
                index=out_index,
                seq=seq,
                t_capture_ms=point.t_ms,
                device_timestamp_ms=None,
                timestamp_domain=timestamp_domain,
                capture_latency_ms=None,
                depth=depth,
                profile=profile,
                source=source,
                dropped_before=0,
                gap_before=gap_before,
            )
        )
        last_emitted_seq = seq
        out_index += 1

    return frames
