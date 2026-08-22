"""フレーム間差分方式の前景マスク生成（design.md「L4-L5: 検出 → MaskBuilder と
3方式」の `frame_diff` 行、タスク 2.3）。

design.md の表が定める方式そのもの: 「直前フレームとの Depth 差が
`diff_threshold_mm` を超えた画素を前景とする」。状態は「直前フレーム1枚」
であり、想定される弱点は「静止した瞬間に消える。二重像（残像）が出る」
（design.md 同表）。

**`MaskBuilder` プロトコルを継承しない。** `depth_band.py`（タスク 2.2）と
同じダックタイピングの実装であり、`kind` / `ready` / `build` / `reset` を
構造的に満たす。`Protocol` クラスの定義そのものはタスク 2.5 の責務である。

無効 Depth の扱い（要件 10.1）と「無効↔有効の遷移」
--------------------------------------------------------
**無効 Depth（raw=0, `INVALID_DEPTH_RAW`）はどの方式でも前景にしない**
（design.md「MaskBuilder / Implementation Notes」）。`depth_band` と同様、
上流が公開する定数 `sensing_foundation.INVALID_DEPTH_RAW` を使った
`roi_depth != INVALID_DEPTH_RAW` を有効性の判定に使う。

差分方式に固有の追加要件が「**無効 → 有効の変化を差分として扱わない**」
（design.md 同 Implementation Notes / 要件 10.1）である。無効画素の raw 値
は `0` であり、そのまま mm 換算して有効な深度値と差分を取ると、
「無効(0mm) → 有効(例: 3000mm)」という**測距状態の変化**が「2999mm 動いた」
という**巨大な前景差分**に見えてしまう。これは物体の移動ではなく、単に
その画素が測距できるようになった／できなくなっただけであり、前景として
扱ってはならない。

したがって前景の判定は「直前・今回の**両方**で有効」かつ「mm 差分が
`diff_threshold_mm` を超える」の論理積で行う。片方でも無効なら（無効→
有効・有効→無効・両方無効のいずれでも）前景にしない。

保持する状態とコピーの理由
----------------------------
本クラスは「直前フレームの ROI 切り出し済み raw depth 配列」を1枚だけ
保持する。呼び出し側（`sensing_foundation`）が渡す配列は読み取り専用の
ビューであり得るうえ、たとえ書き込み可能でも**呼び出し側が後から中身を
書き換える可能性がある**参照をそのまま保持すると、次回の `build()` 呼び
出し時に「直前フレーム」が呼び出し側の現在の状態に汚染され、静止シーンの
はずが誤って前景を生成する、といった再現困難なバグになる
（要件 1.3「上流が提供する Depth バッファを変更せず、読み取りのみを行う」
の裏返し——読み取り専用の入力を保持するときも、保持側が意図せず影響を
受けない独立コピーにする）。そのため `roi_depth.copy()` で明示的にコピー
してから保持する。本モジュールにおいて数少ない正当なコピー箇所の一つ
（`mask_ops.py` docstring が述べる「不要な画像コピーを減らす」方針の例外）
である。

raw → mm 換算（`depth_scale_mm`）
------------------------------------
`depth_band.py` と同一の理由・同一の式（`raw * depth_scale_mm`）を使う。
詳細は同モジュールの docstring「raw → mm 換算」節を参照。

実装上の注記（スカラー関数を要素ごとに呼ばない理由）
------------------------------------------------------
`depth_band.py` と同一の理由（`numpy.vectorize` は Python レベルの要素
ごとループになり致命的に遅い、実測 35倍〜231倍）により、本モジュールも
`is_valid_depth` / `depth_raw_to_mm` と同じ公開定数・同じ式を、ネイティブ
な NumPy 配列演算として `roi_depth` 全体に適用する。

依存方向（design.md「Dependency Direction」表の `detection/masks/*` 行）:
0〜3層（`errors` / `types` / `config` / `metrics` / `detection/mask_ops`）
に加え `sensing_foundation`（公開入口の `INVALID_DEPTH_RAW`）を import
してよい。`cv2` は直接 import しない（本方式もモルフォロジ・ラベリングを
使わないため `mask_ops` の3関数のいずれも必要としない）。
"""

from __future__ import annotations

import numpy as np
from sensing_foundation import INVALID_DEPTH_RAW

from flying_object_tracking.types import DetectorKind

__all__ = ["FrameDiffMask"]


class FrameDiffMask:
    """フレーム間差分方式の `MaskBuilder`（design.md 表の `frame_diff` 行）。

    直前フレーム1枚だけを状態として持つ。`ready` は常に `True`（`background`
    方式のような初期化待ちの状態が無いため）。初回の `build()` 呼び出しは
    直前フレームが無いため差分を取れず、全 False のマスクを返す（タスク
    本文「初回フレームでは前景なしを返す」）。
    """

    def __init__(self, diff_threshold_mm: float, depth_scale_mm: float) -> None:
        """差分の判定閾値と mm 換算係数を受け取る。

        `depth_band.py` と異なり `Roi` を必要としない。`frame_diff` は
        距離帯ゲートを持たず、直前フレームとの差分だけを見るためである。

        Args:
            diff_threshold_mm: 前景と判定する Depth 差分の閾値（mm）。
                `|今回mm - 直前mm| > diff_threshold_mm` を満たす画素だけが
                前景になる（「超えた」＝ 厳密に上回る場合のみ。ちょうど
                閾値ぶんの変化は前景にしない）。
            depth_scale_mm: raw uint16 カウントを mm へ換算する係数。
                `sensing_foundation.geometry.depth_raw_to_mm` と同じ式
                （`raw * depth_scale_mm`）で `build()` 内の mm 換算に使う。
        """
        self._diff_threshold_mm = diff_threshold_mm
        self._depth_scale_mm = depth_scale_mm
        self._previous_raw: np.ndarray | None = None

    @property
    def kind(self) -> DetectorKind:
        return DetectorKind.FRAME_DIFF

    @property
    def ready(self) -> bool:
        """常に `True`。`background` のような初期化待ちが無いため。"""
        return True

    def build(self, roi_depth: np.ndarray) -> np.ndarray:
        """`roi_depth` と直前フレームとの差分から前景マスクを作る。

        Preconditions:
            `roi_depth` は ROI 切り出し済みの uint16 raw depth 配列。
            この配列を変更しない。

        Postconditions:
            `roi_depth` と同じ shape の**新規割り当て** bool 配列を返す。
            初回呼び出し（保持する直前フレームが無い場合）は全 False。
            2回目以降は、直前・今回の**両方**で有効な Depth を持ち、かつ
            mm 換算後の差の絶対値が `diff_threshold_mm` を厳密に超える
            画素だけが `True` になる（無効↔有効の遷移は前景にしない。
            モジュール docstring 参照）。
            呼び出し後、`roi_depth` の**独立したコピー**を次回の直前
            フレームとして保持する。

        Args:
            roi_depth: ROI 切り出し済みの uint16 raw depth 配列。

        Returns:
            同 shape の新規 bool 配列。
        """
        if self._previous_raw is None:
            foreground = np.zeros(roi_depth.shape, dtype=bool)
        else:
            # `sensing_foundation.geometry.is_valid_depth` と同一の基準を
            # 今回・直前の両方に適用する（モジュール docstring「無効 Depth
            # の扱い」参照。`numpy.vectorize` は使わない）。
            valid_now = roi_depth != INVALID_DEPTH_RAW
            valid_previous = self._previous_raw != INVALID_DEPTH_RAW
            both_valid = valid_now & valid_previous

            # `sensing_foundation.geometry.depth_raw_to_mm` と同一の式を
            # 配列全体に適用する。
            mm_now = roi_depth.astype(np.float64) * self._depth_scale_mm
            mm_previous = self._previous_raw.astype(np.float64) * self._depth_scale_mm
            diff_mm = np.abs(mm_now - mm_previous)

            foreground = both_valid & (diff_mm > self._diff_threshold_mm)

        # 独立したコピーを保持する（モジュール docstring「保持する状態と
        # コピーの理由」参照。呼び出し側の配列を後から書き換えても内部
        # 状態が汚染されないようにするため）。
        self._previous_raw = roi_depth.copy()

        return foreground

    def reset(self) -> None:
        """保持している直前フレームを破棄する。

        次回の `build()` 呼び出しは、`reset()` 前に何を見ていたかに関わらず
        「初回」として扱われ、全 False のマスクを返す。
        """
        self._previous_raw = None
