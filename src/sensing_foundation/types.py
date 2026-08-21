"""入力元に依存しないフレーム表現（design.md「L0-L2 基盤層 / CoreTypes」）。

本モジュールが定義するのは、live / recorded / simulated の**どの入力元から
得たフレームも同じ形**として下流へ渡すための不変な値の集合である
（要件 3.1 / 3.2）。すべて `frozen=True, slots=True` の dataclass で値等価とし、
距離は mm・時刻は ms・画素量は px で表し、それらを表すフィールド名には単位を
含める（要件 3.3）。

**単位規約**: `_mm` は距離、`_ms` は時刻・経過時間、`_px` は画素座標・画素数、
`fps` は周波数を表す。`prediction_core.types` が採る単位規約（要件 10.4）と
同じ考え方をフレーム表現に適用したものである。

**Depth の読み取り専用化**: `CaptureFrame.depth` は `uint16` の2次元 `numpy`
配列として保持し、構築時に書き込み禁止フラグ（`flags.writeable = False`）を
立てる。呼び出し側が書き込み可能な配列を渡しても、`CaptureFrame` を経由すれば
必ず読み取り専用になる。これを怠ると、下流（検出・追跡）が Depth を
in-place で書き換え、Replay の再現性（同じ記録を何度再生しても同じ結果に
なること、要件 6）が壊れる（design.md CoreTypes「Risks」）。

**欠測の表現**: `device_timestamp_ms` と `capture_latency_ms` はいずれも
`float | None` とし、欠測を `None` で表す。`-1` のようなセンチネル値は
使わない。`capture_latency_ms` は `timestamp_domain` が `GLOBAL_TIME` の
ときのみ意味を持つ（他ドメインでは値があっても無視してよい）。

**検証しない**: `CaptureFrame` は構築時に `depth.shape == (profile.height_px,
profile.width_px)` や `depth.dtype == uint16` を検証しない
（`prediction_core.types` の値オブジェクトが検証しない方針をそのまま踏襲する。
`prediction_core/types.py` docstring 参照）。これらの Precondition の検証は
各入力元アダプタと `SessionReader` の境界の責務であり、本モジュールの責務では
ない（design.md CoreTypes「Implementation Notes / Validation」）。読み取り
専用フラグを立てる処理は「検証」ではなく「下流からの破壊的変更を防ぐ
enforcement」であり、この方針とは矛盾しない。

**スコープ外（意図的に含めない）**: 本モジュールはカメラ座標系への逆投影
（ピンホール基本演算: 生 Depth → mm 換算・画素中心規約・逆投影式・無効画素
判定）を持たない。それは `geometry.py` の責務である。さらに World frame への
座標変換・床平面の推定は、`geometry.py` の範囲すら超えて `world-frame-calibration`
Spec の責務であり、本モジュールにも `geometry.py` にも含めない（要件 3.7）。
`CaptureFrame` が保持するのはカメラ座標系にすら変換していない生の Depth と
その取得設定のみである。

**`prediction_core` への依存**: 本モジュールは `sensing_foundation` の中で
`prediction_core` を import してよい2モジュールのうちの1つである（もう1つは
将来実装される Throw Record 保存モジュール）。ここでの参照は
**`SourceKind` の再エクスポートのみ**に限る。入力元の種別を表す列挙型を
本モジュールで新たに定義すると、`prediction_core` が持つ `SourceKind` と
値は同じでも別オブジェクトになり、Throw Record の組み立て時に
「気付かれないまま異なる列挙値が書き込まれる」事故につながる（要件 4.3）。
そのためここでは新規定義ではなく再エクスポートのみを行い、
`sensing_foundation.types.SourceKind is prediction_core.SourceKind` が
常に成り立つようにする。Throw Record **スキーマ**（`ThrowRecord` /
`SCHEMA_VERSION` / `replay` / `predictions_equivalent` など）には一切
触れない。スキーマの参照点は Throw Record 保存モジュールに一本化する
（design.md「Dependency Direction」直下の説明文を参照）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import numpy as np
from prediction_core import SourceKind

__all__ = [
    "CameraIntrinsics",
    "CaptureFrame",
    "CaptureStats",
    "SourceKind",
    "StreamProfile",
    "TimestampDomain",
]


class TimestampDomain(StrEnum):
    """`CaptureFrame` の時刻が何を基準とした値かを表す（要件 3.5）。

    センサ側の時刻かホスト側の時刻かを利用側が識別できるようにするための
    区分であり、`t_capture_ms`（セッション単調時計）そのものの基準を
    変えるものではない。
    """

    HARDWARE_CLOCK = "hardware_clock"
    """デバイス（RealSense 本体）のハードウェアクロックに基づく時刻。"""

    SYSTEM_TIME = "system_time"
    """ホスト OS のシステム時刻に基づく時刻。"""

    GLOBAL_TIME = "global_time"
    """デバイスとホストの時刻をライブラリが補正した、両者に共通の時刻。

    `capture_latency_ms` が意味を持つのはこのドメインのときのみである。
    """

    UNKNOWN = "unknown"
    """recorded / simulated 入力、またはドメインを問い合わせられない場合。"""


@dataclass(frozen=True, slots=True)
class CameraIntrinsics:
    """ピンホールカメラモデルの内部パラメータ。

    `geometry.py` の逆投影演算が要求する入力であり、本モジュール自身は
    これらの値を使って座標変換を行わない（要件 3.6 / 3.7 のスコープ外）。

    Attributes:
        width_px: このパラメータが対応する画像の幅（px）。
        height_px: このパラメータが対応する画像の高さ（px）。
        fx_px: X 方向の焦点距離（px）。
        fy_px: Y 方向の焦点距離（px）。
        ppx_px: 主点の X 座標（px）。
        ppy_px: 主点の Y 座標（px）。
        model: 歪みモデルの名称（例: `"brown_conrady"`）。
        coeffs: 歪み係数（5要素）。D435 の Depth ストリームでは通常すべて 0。
    """

    width_px: int
    height_px: int
    fx_px: float
    fy_px: float
    ppx_px: float
    ppy_px: float
    model: str
    coeffs: tuple[float, float, float, float, float]


@dataclass(frozen=True, slots=True)
class StreamProfile:
    """フレーム取得時点で有効だった取得設定（要件 3.2）。

    Attributes:
        width_px: Depth ストリームの幅（px）。
        height_px: Depth ストリームの高さ（px）。
        fps: 要求フレームレート（fps）。
        depth_scale_mm: Depth の1カウントあたりの距離（mm）。
            生の `uint16` 値を mm へ換算する係数（換算自体は `geometry.py`
            の `depth_raw_to_mm()` が単一の適用箇所を持つ）。
        color_enabled: Color ストリームが有効かどうか。
        intrinsics: このプロファイルに対応するカメラ内部パラメータ。
            入力元がカメラ内部パラメータを提供できない場合は `None`。
    """

    width_px: int
    height_px: int
    fps: int
    depth_scale_mm: float
    color_enabled: bool
    intrinsics: CameraIntrinsics | None


@dataclass(frozen=True, slots=True)
class CaptureFrame:
    """入力元に依存しない、1フレーム分の共通表現（要件 3.1 / 3.2）。

    live / recorded / simulated のいずれから得たフレームも、この型として
    下流へ渡される。座標変換・床平面推定に相当するフィールド・処理は
    意図的に持たない（それらのスコープはモジュール docstring を参照。
    `geometry.py` がカメラ座標系への逆投影の基本演算を、
    `world-frame-calibration` Spec がそれ以降の変換・推定を担う）。

    Attributes:
        index: セッション内の 0 始まり通し番号。欠番なく増加する
            （Invariant。構築時には検証しない）。
        seq: 入力元が付けたフレーム番号。欠落検出に使う。
        t_capture_ms: セッション単調時計（`SessionClock`）による取得時刻（ms）。
            **これが正の時間基準**であり、同一セッション内で単調非減少である
            （Invariant。構築時には検証しない）。
        device_timestamp_ms: デバイス側時刻（ms）。問い合わせ不能な場合は
            `None`（欠測をセンチネル値ではなく `None` で表す）。
        timestamp_domain: `device_timestamp_ms` が何を基準とした値かを示す。
        capture_latency_ms: 取得レイテンシ（ms）。`timestamp_domain` が
            `GLOBAL_TIME` のときのみ意味を持つ。他ドメインのとき、または
            計測できないときは `None`。
        depth: `uint16`、shape=`(height_px, width_px)` の Depth 配列。
            構築時に読み取り専用化される（下流での破壊的変更を防ぐ）。
        profile: このフレーム取得時点で有効だった取得設定。
        source: 入力元の種別（`prediction_core.SourceKind` そのもの）。
        dropped_before: 直前のドレイン（最新追従のための破棄）で捨てた枚数。
        gap_before: 直前に検出した `seq` の飛び（欠落）の大きさ。

    Preconditions（構築時には検証しない。検証は各アダプタと `SessionReader`
    の境界の責務。design.md CoreTypes「Implementation Notes / Validation」）:
        `depth.shape == (profile.height_px, profile.width_px)`
        `depth.dtype == numpy.uint16`

    Invariants（同一セッション内。構築時には検証しない）:
        `t_capture_ms` は単調非減少である。
        `index` は 0 から欠番なく増加する。
    """

    index: int
    seq: int
    t_capture_ms: float
    device_timestamp_ms: float | None
    timestamp_domain: TimestampDomain
    capture_latency_ms: float | None
    depth: np.ndarray
    profile: StreamProfile
    source: SourceKind
    dropped_before: int
    gap_before: int

    def __post_init__(self) -> None:
        # 読み取り専用フラグを立てるのみで、shape / dtype の検証は行わない
        # （design.md CoreTypes の方針。docstring 参照）。frozen dataclass の
        # フィールド自体を再代入するわけではないため FrozenInstanceError には
        # 抵触しない。
        self.depth.flags.writeable = False


@dataclass(frozen=True, slots=True)
class CaptureStats:
    """1セッション分の取得統計の要約（要件 2.8）。

    Attributes:
        frames_yielded: 下流へ渡したフレームの総数。
        frames_dropped: ドレイン（最新追従）で捨てた総数。
        frames_missing: `seq` の飛び（欠落）の総数。
        duration_ms: 取得を継続した時間（ms）。
        measured_fps: 実測フレームレート（fps）。
        acquire_errors: フレーム取得に失敗した回数。
    """

    frames_yielded: int
    frames_dropped: int
    frames_missing: int
    duration_ms: float
    measured_fps: float
    acquire_errors: int
