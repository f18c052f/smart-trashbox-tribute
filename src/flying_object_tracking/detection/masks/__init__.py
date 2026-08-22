"""前景マスク生成方式のサブパッケージ（design.md「File Structure Plan」）。

design.md「L4-L5: 検出 → MaskBuilder と3方式」が定める `MaskBuilder` プロトコルと
生成口（`create_mask_builder`）をここに置く（タスク 2.5）。タスク 2.2〜2.4 が
`DepthBandMask` / `FrameDiffMask` / `BackgroundMask` の3実装を揃え終えたため、
本タスクはそれらを1つの契約へ束ねる。

**3方式は `MaskBuilder` を継承しない。** `depth_band.py` / `frame_diff.py` /
`background.py` はいずれも構造的に `kind` / `ready` / `build` / `reset` を
満たすダックタイピングの実装であり（各モジュールの docstring 参照）、本
`__init__.py` が定める `MaskBuilder` を `@runtime_checkable` な `Protocol` と
して定義することで、`isinstance(builder, MaskBuilder)` が明示的な継承なしに
成立するようにする（`Protocol` を継承させて3実装を書き換える必要が無い）。

`create_mask_builder` が「設定から方式を生成する唯一の生成口」である
----------------------------------------------------------------------
要件 4.2「使用する検出方式を、コードの変更なしに設定として選択できるように
する」を満たすため、`DetectorConfig` + `Roi` + `depth_scale_mm` から3方式の
どれを構築するかを**この関数だけ**が知る。後続タスク（2.7 の `Detector` /
7.x の `DetectorComparison`）は `DepthBandMask` / `FrameDiffMask` /
`BackgroundMask` を直接コンストラクトせず、必ず `create_mask_builder` を経由
すること。これにより「方式の切り替えはコード変更なしに設定だけで行える」
（タスク 2.5 の観測可能な完了状態）が構造的に保証される。

3方式それぞれの構築に使う `DetectorConfig` フィールド:

| `DetectorKind`  | 生成する具象クラス | 渡すフィールド |
|---|---|---|
| `DEPTH_BAND`  | `DepthBandMask`  | `roi`（`z_min_mm` / `z_max_mm`）, `depth_scale_mm` |
| `FRAME_DIFF`  | `FrameDiffMask`  | `config.diff_threshold_mm`, `depth_scale_mm` |
| `BACKGROUND`  | `BackgroundMask` | `config.background_frames`, `config.diff_threshold_mm`, `config.background_update_rate`, `depth_scale_mm` |

未知の方式名を起動前に拒否する（要件 10.5 の防御的二重化）
----------------------------------------------------------------------
`config.kind` は型としては `DetectorKind`（`StrEnum`）だが、Python の
dataclass はフィールド型を実行時に強制しない。`TrackingSettings.resolve()`
（`config.py` の `_coerce_detector_kind`）は文字列から `DetectorKind` への
変換時点で既に未知の方式名を `TrackingConfigError` として拒否しているが、
`create_mask_builder` は `resolve()` を経由しない `DetectorConfig` の直接
構築（例: テストや将来の呼び出し側が `dataclasses.replace(config,
kind="not_a_real_kind")` のように型チェックをすり抜けた値を渡す場合）にも
備える必要がある。したがって本関数はどの `DetectorKind` メンバーとも一致
しない `config.kind` を、マスク構築を一切試みる前に `TrackingConfigError`
として拒否する（「起動前に拒否する」＝ 部分的な構築が起こらないことを含む）。
`TrackingConfigError` は `ValueError` も継承するため、呼び出し側が
`except ValueError` で捕捉することもできる（`errors.py` の設計に倣う）。

依存方向（design.md「Dependency Direction」表の `detection/masks/*` 行）:
0〜3層（`errors` / `types` / `config` / `metrics` / `detection/mask_ops`）を
import してよい。`cv2` は import しない（`numpy.vectorize` も使わない）。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np

from flying_object_tracking.config import DetectorConfig
from flying_object_tracking.detection.masks.background import BackgroundMask
from flying_object_tracking.detection.masks.depth_band import DepthBandMask
from flying_object_tracking.detection.masks.frame_diff import FrameDiffMask
from flying_object_tracking.errors import TrackingConfigError
from flying_object_tracking.types import DetectorKind, Roi

__all__ = ["MaskBuilder", "create_mask_builder"]


@runtime_checkable
class MaskBuilder(Protocol):
    """前景マスク生成方式が共有する形（design.md「MaskBuilder と3方式」）。

    `DepthBandMask` / `FrameDiffMask` / `BackgroundMask` はいずれもこの
    プロトコルを明示的に継承せず、構造的に満たすだけである
    （`@runtime_checkable` により `isinstance` 判定はそれでも機能する）。

    Postconditions（3方式に共通、`test_masks.py` 系が固定する契約）:
        `build()` は入力と同じ shape・dtype（bool）の新規配列を返す。
        `ready` が `False` の間、`build()` は全 False のマスクを返す
        （例外にしない）。
    """

    @property
    def kind(self) -> DetectorKind:
        """この `MaskBuilder` が実装する検出方式。"""
        ...

    @property
    def ready(self) -> bool:
        """`build()` が意味のある前景を返せる状態か。

        背景モデルの初期化中など、内部状態がまだ整っていない間は
        `False` になり得る（`depth_band` / `frame_diff` は常に `True`）。
        """
        ...

    def build(self, roi_depth: np.ndarray) -> np.ndarray:
        """ROI 切り出し済みの raw depth 配列から前景マスクを作る。

        `roi_depth` を変更しない。戻り値は同 shape の新規割り当て
        bool 配列である。
        """
        ...

    def reset(self) -> None:
        """内部状態を初期状態へ戻す。"""
        ...


def create_mask_builder(
    config: DetectorConfig, roi: Roi, depth_scale_mm: float
) -> MaskBuilder:
    """設定から `MaskBuilder` を生成する唯一の生成口（design.md 署名どおり）。

    `config.kind` に応じて `DepthBandMask` / `FrameDiffMask` /
    `BackgroundMask` のいずれかを構築する。**この関数以外の場所で3方式の
    具象クラスを直接コンストラクトしないこと**（モジュール docstring
    「`create_mask_builder` が『設定から方式を生成する唯一の生成口』である」
    節を参照）。

    Args:
        config: 検出方式と方式別パラメータ（`diff_threshold_mm` /
            `background_frames` / `background_update_rate`）を持つ設定。
        roi: 処理対象範囲。`z_min_mm` / `z_max_mm`（距離帯）を
            `DepthBandMask` の構築に使う。他の2方式は `roi` を使わない。
        depth_scale_mm: raw uint16 カウントを mm へ換算する係数
            （`sensing_foundation` の `StreamProfile.depth_scale_mm` を
            渡す想定）。3方式すべてに一様に渡す。

    Returns:
        `config.kind` に対応する `MaskBuilder` の新規インスタンス。

    Raises:
        TrackingConfigError: `config.kind` がどの `DetectorKind` メンバーとも
            一致しない場合。マスクの構築を一切試みる前に送出する
            （「未知の方式名は起動前に拒否する」。`ValueError` としても
            捕捉できる）。
    """
    if config.kind == DetectorKind.DEPTH_BAND:
        return DepthBandMask(roi=roi, depth_scale_mm=depth_scale_mm)
    if config.kind == DetectorKind.FRAME_DIFF:
        return FrameDiffMask(
            diff_threshold_mm=config.diff_threshold_mm,
            depth_scale_mm=depth_scale_mm,
        )
    if config.kind == DetectorKind.BACKGROUND:
        return BackgroundMask(
            background_frames=config.background_frames,
            diff_threshold_mm=config.diff_threshold_mm,
            background_update_rate=config.background_update_rate,
            depth_scale_mm=depth_scale_mm,
        )
    raise TrackingConfigError(f"未知の検出方式: {config.kind!r}")
