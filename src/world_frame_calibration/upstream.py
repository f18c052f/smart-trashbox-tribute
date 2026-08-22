"""上流の副作用に触れる唯一の接点：フレーム収集・平均化・型写像
(design.md「L0-L2〜L8: 上流接続」節の UpstreamAdapter コンポーネント /
tasks.md タスク 6.1 / 要件 1.3, 1.6, 1.7, 3.8, 8.1, 8.3, 8.4, 8.5)。

**上流の副作用を伴う機能（取得・ロギング・設定解決）を使うのは本モジュール
だけである。** 幾何コアからの上流参照は `deproject.py` の2シンボル
（`deproject_pixel` / `CameraIntrinsics`）に限られる（design.md
「Dependency Direction」表。`test_boundaries.py`（タスク 7.3）が静的に
固定する）。

本モジュールが `sensing_foundation` から import してよいのは、その
**公開入口（`sensing_foundation.__init__` の `__all__`）に列挙された
シンボルのみ**である。`sensing_foundation.source` / `sensing_foundation.
types` のような内部モジュールへ直接 import しない（要件 8.2）。

**入力元の選択（live / recorded / simulated）は上流の `open_source()` に
委ねる。本モジュールはアダプタを新設しない**（要件 8.1 / 8.5）。本モジュール
が受け取る `source` は、既に構築された `sensing_foundation.FrameSource`
（`open_source()` の戻り値、またはそれと構造的に等価なもの）である。

**生 Depth → mm の換算と無効画素の判定は、上流の `depth_raw_to_mm` /
`is_valid_depth` を用いる**（要件 3.8）。`raw * depth_scale_mm` を本モジュール
が自分で書かない。無効値の定義（`INVALID_DEPTH_RAW` = 生カウント 0）も
上流のものを使い、「生値 0」を自前の条件式で判定しない。

**平均化は新しい `float64` 配列へ行う。** 上流の読み取り専用 Depth
（`CaptureFrame.depth`、構築時に `writeable = False` が立てられている）を
読むだけで、書き換えない（要件 1.7）。寄与ゼロの画素は欠測（`NaN`）のまま
残し、0 で埋めない（`types.DepthImage` の Postconditions）。

**収集中にストリーム設定が変化した場合は設定誤りとして失敗させる。**
解像度・fps・Depth スケール・Color 有無・内部パラメータのいずれかが
フレームごとに変われば、異なる設定の観測を1つの平均へ静かに混ぜることに
なり、キャリブレーションそのものの意味が崩れる。これは「座標系が定まらない
条件」（`CalibrationFailure`）ではなく「呼び出し方・入力の誤り」
（`CalibrationConfigError`）である——上流の入力元設定またはその呼び出し方が
そもそも不正であるという性質の失敗であり、平面やマーカーの幾何が縮退した
わけではないため（design.md Errors の3階層区分、`errors.py` docstring）。

**カメラ内部パラメータは常に入力フレームの経路から取得する。** 独自に保持
した固定値を用いない（要件 8.4）。`StreamProfile.intrinsics` が `None`
（内部パラメータを提供できない入力元）の場合もキャリブレーションを続行
できないため `CalibrationConfigError` とする。

**本タスク（6.1）のスコープ外**: 構造化ロギングへの計測値送出
（`CALIBRATE_STAGE` / `stage_logger` / `timed` / `timed_apply`）はタスク 6.2
が持つ（要件 10.x）。`RuntimeSettings` から入力元を開いて本モジュールへ
橋渡しする CLI 向けの入口（`open_depth_image`）はタスク 6.4 の CLI 実装と
合わせて追加する。本モジュールは「既に得られた `FrameSource` からフレーム
列を集めて平均化し、自 Spec の値オブジェクトへ写像する」ことだけを行う。

**`source` のライフサイクル管理は呼び出し側の責務**である。
`sensing_foundation.FrameSource` の契約は `start()` を `frames()` の前に
1度だけ呼ぶことを前提とする（Precondition）。本モジュールの `collect_depth`
はこの前提の検証もライフサイクル管理（`start()` / `stop()` の呼び出し）も
行わない——`CaptureFrame` 自身が Precondition を検証しない方針
（`sensing_foundation.types` docstring）と同じ考え方を踏襲し、`source` を
開く／閉じる操作は呼び出し側（将来の `open_depth_image` / CLI）に委ねる。
"""

from __future__ import annotations

import numpy as np
from sensing_foundation import (
    FrameSource,
    StreamProfile,
    depth_raw_to_mm,
    is_valid_depth,
)

from world_frame_calibration.errors import CalibrationConfigError
from world_frame_calibration.types import DepthImage, Intrinsics, StreamSignature

__all__ = [
    "collect_depth",
    "to_intrinsics",
    "to_signature",
]


def to_intrinsics(profile: StreamProfile) -> Intrinsics:
    """`StreamProfile.intrinsics`（上流 `CameraIntrinsics`）を自 Spec の
    `Intrinsics` へ写像する。

    フィールド名は `types.Intrinsics` の docstring が定めるとおり
    `sensing_foundation.CameraIntrinsics` と完全に一致しているため、機械的な
    1:1 転記である（design.md GeoTypes「Integration」）。

    Raises:
        CalibrationConfigError: `profile.intrinsics` が `None` の場合
            （入力元が内部パラメータを提供できない。要件 8.4 により独自の
            固定値で埋めることはできないため、続行できず失敗させる）。
    """
    if profile.intrinsics is None:
        raise CalibrationConfigError(
            "StreamProfile.intrinsics is None: the input source did not "
            "provide camera intrinsics, and world-frame-calibration must not "
            "substitute an independently-held fixed value (requirement 8.4)"
        )
    intr = profile.intrinsics
    return Intrinsics(
        width_px=intr.width_px,
        height_px=intr.height_px,
        fx_px=intr.fx_px,
        fy_px=intr.fy_px,
        ppx_px=intr.ppx_px,
        ppy_px=intr.ppy_px,
        model=intr.model,
        coeffs=intr.coeffs,
    )


def to_signature(profile: StreamProfile) -> StreamSignature:
    """上流 `StreamProfile` を整合性検査対象の `StreamSignature` へ写像する。

    フィールド名は `types.StreamSignature` の docstring が定めるとおり
    `sensing_foundation.StreamProfile` の対応部分と完全に一致する
    （design.md GeoTypes「Integration」）。
    """
    return StreamSignature(
        width_px=profile.width_px,
        height_px=profile.height_px,
        fps=profile.fps,
        depth_scale_mm=profile.depth_scale_mm,
        color_enabled=profile.color_enabled,
    )


def collect_depth(source: FrameSource, *, frame_count: int) -> DepthImage:
    """`source.frames()` から `frame_count` 枚を取り、平均化した `DepthImage`
    を返す（design.md UpstreamAdapter 契約 / 要件 1.3, 1.6, 1.7, 3.8, 8.1,
    8.3, 8.4）。

    無効画素（上流 `is_valid_depth` が偽）は平均から除外し、生カウントから
    mm への換算は上流 `depth_raw_to_mm` に委ねる（要件 3.8）。平均は新しい
    `float64` 配列へ確保し、`frame.depth`（上流の読み取り専用 Depth）を
    一切書き換えない（要件 1.7）。寄与した枚数がゼロの画素は `NaN`
    （欠測）のまま残す（0 で埋めない）。

    Preconditions:
        `frame_count >= 1`。`source` は既に `start()` されており、
        `frames()` を呼べる状態であること（本関数はこれを検証しない。
        モジュール docstring「`source` のライフサイクル管理」を参照）。

    Postconditions:
        `DepthImage.frames_used` は実際に平均へ寄与した枚数
        （収集できたフレーム数と一致する）。同一のフレーム列に対して常に
        同一の `DepthImage` を返す（要件 8.3。入力が同じなら出力も同じ、
        という決定的な処理しか行わない）。

    Raises:
        CalibrationConfigError:
            - `frame_count < 1` の場合。
            - `source` が `frame_count` 枚に満たないフレームしか供給
              できなかった場合（フレーム列が尽きた）。
            - 収集中に `StreamProfile` が変化した場合（解像度・fps・
              Depth スケール・Color 有無・内部パラメータのいずれかが
              フレームごとに異なる設定を、1つの平均へ静かに混ぜることに
              なるため。設定不一致は「呼び出し方の誤り」であり、座標系が
              定まらない `CalibrationFailure` ではない）。
            - いずれかのフレームで `StreamProfile.intrinsics` が `None`
              の場合（`to_intrinsics` が送出する）。
    """
    if frame_count < 1:
        raise CalibrationConfigError(f"frame_count must be >= 1: {frame_count!r}")

    frame_iter = iter(source.frames())

    reference_profile: StreamProfile | None = None
    source_kind: str | None = None
    sum_mm: np.ndarray | None = None
    valid_count: np.ndarray | None = None
    frames_used = 0

    for frame_index in range(frame_count):
        try:
            frame = next(frame_iter)
        except StopIteration:
            raise CalibrationConfigError(
                "frame source was exhausted after supplying "
                f"{frames_used} of the {frame_count} requested frames"
            ) from None

        profile = frame.profile
        if reference_profile is None:
            reference_profile = profile
            source_kind = frame.source.value
            height, width = profile.height_px, profile.width_px
            sum_mm = np.zeros((height, width), dtype=np.float64)
            valid_count = np.zeros((height, width), dtype=np.int32)
        elif profile != reference_profile:
            raise CalibrationConfigError(
                "stream configuration changed mid-collection at frame "
                f"{frame_index}: {reference_profile!r} -> {profile!r}. "
                "Averaging frames captured under different stream "
                "configurations would silently corrupt the calibration."
            )

        depth_scale_mm = profile.depth_scale_mm
        raw_depth = frame.depth  # 読み取り専用。ここでは読むだけで書き換えない。
        height, width = raw_depth.shape
        for v in range(height):
            for u in range(width):
                raw = int(raw_depth[v, u])
                if not is_valid_depth(raw):
                    continue
                z_mm = depth_raw_to_mm(raw, depth_scale_mm)
                sum_mm[v, u] += z_mm
                valid_count[v, u] += 1
        frames_used += 1

    assert reference_profile is not None
    assert source_kind is not None
    assert sum_mm is not None
    assert valid_count is not None

    depth_mm = np.full(sum_mm.shape, np.nan, dtype=np.float64)
    contributed = valid_count > 0
    depth_mm[contributed] = sum_mm[contributed] / valid_count[contributed]

    return DepthImage(
        depth_mm=depth_mm,
        valid_count=valid_count,
        frames_used=frames_used,
        intrinsics=to_intrinsics(reference_profile),
        signature=to_signature(reference_profile),
        source_kind=source_kind,
    )
