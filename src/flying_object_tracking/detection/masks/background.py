"""背景差分方式の前景マスク生成（design.md「L4-L5: 検出 → MaskBuilder と
3方式」の `background` 行、タスク 2.4）。

design.md の表が定める方式そのもの: 「初期 N フレームから作った背景 Depth
モデルとの差が閾値を超えた画素を前景とする」。状態は「背景モデル1枚」で
あり、想定される弱点は「背景が変わると全体が前景になる。初期化中は検出
できない」（design.md 同表）。

**`MaskBuilder` プロトコルを継承しない。** `depth_band.py`（タスク 2.2）・
`frame_diff.py`（タスク 2.3）と同じダックタイピングの実装であり、`kind` /
`ready` / `build` / `reset` を構造的に満たす。`Protocol` クラスの定義
そのものはタスク 2.5 の責務である。

`ready` が本当に初期化待ちを表す唯一の方式
--------------------------------------------------------------------
`depth_band` / `frame_diff` は状態が無い（または「直前フレーム1枚」しか
持たない）ため `ready` は常に `True` だったが、本方式は
design.md「MaskBuilder」節の Invariant「`ready` が `False` の間、`build()`
は全 False のマスクを返す（例外にしない）」が実際に意味を持つ唯一の方式で
ある。`background_frames` 枚の初期化フレームを蓄積し終えるまで `ready` は
`False` であり、その間の `build()` は**内容に関わらず**（初期化中に移動す
る塊が写っていても）全 False を返す（タスク本文「初期化中は前景なしを返す
（例外にしない）ことと、初期化完了を問い合わせられることを契約にする」）。
ちょうど `background_frames` 回目の `build()` 呼び出しでモデルが完成し、
`ready` が `True` になる（その回自身の戻り値はなお「初期化データの一部」
として全 False のまま——`frame_diff` の初回呼び出しが直前フレームを保持
するだけで全 False を返すのと同じ設計原則）。

無効 Depth の扱い（要件 10.1）と「無効画素を背景モデルにも前景にも含めな
い」（タスク本文）
--------------------------------------------------------------------
**無効 Depth（raw=0, `INVALID_DEPTH_RAW`）はどの方式でも前景にしない**
（design.md「MaskBuilder / Implementation Notes」）。判定は
`depth_band` / `frame_diff` と同一の基準（`roi_depth != INVALID_DEPTH_RAW`）
を使う。

本方式に固有の追加要件が「無効画素を背景モデルにも前景にも含めない」
（タスク本文）である。これは2段階で効く。

1. **背景モデルの構築時**: 初期化窓の N フレームのうち、ある画素が一部の
   フレームでは無効、別のフレームでは有効ということが起こり得る（例:
   센서のノイズで瞬間的に測距できない）。この画素の背景値は**有効だった
   サンプルだけの平均**として求める（無効サンプルの raw 値である `0` を
   平均に混入させない）。実装は画素ごとに「有効サンプル数」と「有効サン
   プルの合計 mm」を初期化窓全体で積算し、初期化完了時に
   `合計 / 有効サンプル数`（有効サンプル数が0の画素は対象外）として平均
   する。初期化窓の全フレームでその画素が無効だった場合、その画素の背景
   は「未知」として扱い、`_background_valid` を `False` のままにする。
   「未知」の背景を持つ画素は、以後どんなクエリ値が来ても
   `_background_valid` が `False` であるために前景判定式（下記）の論理積
   で常に `False` になる——「その画素については恒久的に前景にしない」と
   いう選択である（設計判断: 何が正常かを一度も観測できていない画素につ
   いて、恒久的に無条件で前景と判定する方が誤検出の温床になりやすいと
   判断した。逆に「恒久的に前景」にする代替案は、背景差分方式の弱点
   （design.md 表「背景が変わると全体が前景になる」）を悪化させるだけで
   採らない）。
2. **クエリ時**: 今回のフレームで無効な画素は、学習済み背景値が何であれ
   前景にしない（`frame_diff` の「無効↔有効の遷移を差分として扱わない」
   と同じ精神——無効は「測距できなかった」だけであり「物体が現れた」こと
   を意味しない）。

前景判定式は次の論理積である（有効性2つと閾値超過の3条件すべてを満たす
画素だけが前景）:

    今回有効 AND 背景が既知（_background_valid） AND |今回mm - 背景mm| > diff_threshold_mm

背景モデルの更新（`background_update_rate`）
--------------------------------------------------------------------
`background_update_rate` は既定 `0.0`（更新しない）である。design.md
「MaskBuilder / Risks」: 「`background_update_rate > 0` にすると飛翔体を
背景に取り込む恐れがある。既定は 0.0（更新しない）とし、更新が必要と判明
した場合のみ上げる」。既定値のまま運用する限り、`ready` になった時点の
背景モデルは以後変化しない（凍結される）。

`background_update_rate > 0.0` を指定した場合のみ、`ready` 化後の各
`build()` 呼び出しが背景モデルを指数移動平均で更新する:

    背景mm = (1 - rate) * 背景mm + rate * 今回mm

**この更新は「今回有効」かつ「今回前景と判定されなかった」画素だけに
適用する。** design.md はこの更新粒度そのものは明記していないが、タスク
本文の理由づけ（「飛翔体を背景へ取り込まないため」）から、更新が有効な
場合であっても**前景と判定された画素だけは更新対象から除外する**のが
筋が通っている、と判断した（さもなければ `background_update_rate > 0` の
たびに飛翔体そのものが背景へ溶け込んでしまい、design.md が名指しする
リスクをそのまま再現することになる）。したがって持続的に背景と異なる
塊（飛翔体）は、`background_update_rate` の値に関わらず背景へ吸収されない
——毎回「前景」と判定され続ける限り、更新対象からも除外され続けるためで
ある。一方、**閾値未満の緩やかな変化**（背景光の揺らぎ等を想定）は「前景
ではない」と判定されるため更新対象に含まれ、繰り返し与えられることで
背景が徐々にその内容へドリフトし得る。

更新対象のうち、初期化時に背景が「未知」（`_background_valid` が `False`）
だった画素については、指数移動平均ではなく**今回の mm 値をそのまま採用**
する（`_background_valid` を `True` にする）。プレースホルダとして入って
いる `0.0`（背景未定義時の内部配列の初期値）を移動平均のベースにしてしま
うと、未定義だった画素が不自然に小さい値へ引っ張られるためである。

保持する状態とコピーの理由
----------------------------
本クラスは初期化中、画素ごとの「有効サンプル合計 mm」と「有効サンプル
数」を蓄積し、`ready` 化後は画素ごとの「背景 mm」と「背景が既知か」を
保持する。いずれも `roi_depth.astype(np.float64)` の結果（後述）から算出
した**新規配列**であり、`roi_depth` 自体を保持しない。`astype()` は
dtype が異なる場合（`uint16` → `float64`）常に新規メモリを割り当てる
ため、`frame_diff.py` のように明示的な `.copy()` を別途行う必要が無い
（本モジュールが `roi_depth` そのものへの参照・ビューを一切保持しない
設計であるため）。呼び出し側が `build()` 呼び出し後に元の配列を書き換え
ても、内部状態はその影響を受けない（要件 1.3 の裏返し。`frame_diff.py`
docstring 参照）。

raw → mm 換算（`depth_scale_mm`）
------------------------------------
`depth_band.py` / `frame_diff.py` と同一の理由・同一の式
（`raw * depth_scale_mm`）を使う。詳細は `depth_band.py` の docstring
「raw → mm 換算」節を参照。

実装上の注記（スカラー関数を要素ごとに呼ばない理由）
------------------------------------------------------
`depth_band.py` / `frame_diff.py` と同一の理由（`numpy.vectorize` は
Python レベルの要素ごとループになり致命的に遅い、実測 35倍〜231倍）に
より、本モジュールも `is_valid_depth` / `depth_raw_to_mm` と同じ公開定数
・同じ式を、ネイティブな NumPy 配列演算として `roi_depth` 全体に適用する。
背景モデルの蓄積・更新も同様にネイティブな配列演算（`np.where` / 真偽
配列によるファンシーインデックス代入）で行い、画素ごとの Python ループは
使わない。

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

__all__ = ["BackgroundMask"]


class BackgroundMask:
    """背景差分方式の `MaskBuilder`（design.md 表の `background` 行）。

    初期 `background_frames` 回の `build()` 呼び出しで静止背景の Depth
    モデルを学習し（`ready` は `False`、戻り値は常に全 False）、それ以降は
    学習済み背景との差分で前景マスクを作る（`ready` は `True`）。
    """

    def __init__(
        self,
        background_frames: int,
        diff_threshold_mm: float,
        background_update_rate: float,
        depth_scale_mm: float,
    ) -> None:
        """背景モデルの初期化窓・差分閾値・更新率・mm 換算係数を受け取る。

        `depth_band.py` / `frame_diff.py` と同じ位置に `depth_scale_mm` を
        置く（末尾の引数）。`diff_threshold_mm` の名前は `DetectorConfig`
        （`config.py`）が `FRAME_DIFF` / `BACKGROUND` の両方式で共有する
        フィールド名と一致させてある。

        Args:
            background_frames: 背景モデルの初期化に使うフレーム数。
                この回数ぶん `build()` を呼び出し終えるまで `ready` は
                `False`。
            diff_threshold_mm: 前景と判定する Depth 差分の閾値（mm）。
                `|今回mm - 背景mm| > diff_threshold_mm` を満たす画素だけが
                前景になる（「超えた」＝ 厳密に上回る場合のみ）。
            background_update_rate: 背景モデルの指数移動平均の更新率。
                **既定として `0.0`（更新しない）が渡されることを想定する**
                （`DetectorConfig.background_update_rate` の既定値）。
                `0.0` より大きい場合のみ、`ready` 化後の `build()` 呼び出し
                ごとに、前景と判定されなかった有効画素について
                `背景mm = (1 - rate) * 背景mm + rate * 今回mm` で更新する
                （モジュール docstring「背景モデルの更新」節参照）。
            depth_scale_mm: raw uint16 カウントを mm へ換算する係数。
                `sensing_foundation.geometry.depth_raw_to_mm` と同じ式
                （`raw * depth_scale_mm`）で `build()` 内の mm 換算に使う。
        """
        self._background_frames = background_frames
        self._diff_threshold_mm = diff_threshold_mm
        self._background_update_rate = background_update_rate
        self._depth_scale_mm = depth_scale_mm

        self._ready = False
        self._frames_seen = 0
        # 初期化中だけ使う蓄積用配列（形状は最初の build() 呼び出しで確定）。
        self._sum_mm: np.ndarray | None = None
        self._valid_count: np.ndarray | None = None
        # ready 化後に使う学習済み背景モデル。
        self._background_mm: np.ndarray | None = None
        self._background_valid: np.ndarray | None = None

    @property
    def kind(self) -> DetectorKind:
        return DetectorKind.BACKGROUND

    @property
    def ready(self) -> bool:
        """`background_frames` 回ぶんの初期化フレームを蓄積し終えたら `True`。

        3方式のうち、初期化待ちの状態が実在するのは本方式だけである
        （`depth_band` / `frame_diff` は常に `True`）。
        """
        return self._ready

    def build(self, roi_depth: np.ndarray) -> np.ndarray:
        """`roi_depth` を蓄積または学習済み背景と比較し、前景マスクを作る。

        Preconditions:
            `roi_depth` は ROI 切り出し済みの uint16 raw depth 配列。
            この配列を変更しない。

        Postconditions:
            `roi_depth` と同じ shape の**新規割り当て** bool 配列を返す。
            `ready` が `False` の間（初期化窓の `background_frames` 回）は、
            内容に関わらず全 False（モジュール docstring 参照）。`ready` に
            なった後は、今回・背景の両方で有効な Depth を持ち、かつ mm
            換算後の差の絶対値が `diff_threshold_mm` を厳密に超える画素
            だけが `True`（無効画素・背景未定義画素は前景にしない）。

        Args:
            roi_depth: ROI 切り出し済みの uint16 raw depth 配列。

        Returns:
            同 shape の新規 bool 配列。
        """
        # `sensing_foundation.geometry.is_valid_depth` と同一の基準を
        # ネイティブな NumPy 配列演算として適用する（`numpy.vectorize` は
        # 使わない。モジュール docstring「実装上の注記」参照）。
        valid_now = roi_depth != INVALID_DEPTH_RAW
        # `sensing_foundation.geometry.depth_raw_to_mm` と同一の式。
        # `astype()` は dtype 変換に伴い常に新規配列を割り当てるため、
        # `roi_depth` へのビュー・参照をここから先で一切保持しない。
        mm_now = roi_depth.astype(np.float64) * self._depth_scale_mm

        if not self._ready:
            self._accumulate(roi_depth.shape, valid_now, mm_now)
            return np.zeros(roi_depth.shape, dtype=bool)

        assert self._background_mm is not None
        assert self._background_valid is not None

        diff_mm = np.abs(mm_now - self._background_mm)
        foreground = valid_now & self._background_valid & (diff_mm > self._diff_threshold_mm)

        if self._background_update_rate > 0.0:
            self._update_background(valid_now, mm_now, foreground)

        return foreground

    def reset(self) -> None:
        """学習済み背景モデルと初期化状態を破棄する。

        次回の `build()` 呼び出しから、`background_frames` 回の新規初期化
        窓が始まる（`ready` は再び `False` になる）。
        """
        self._ready = False
        self._frames_seen = 0
        self._sum_mm = None
        self._valid_count = None
        self._background_mm = None
        self._background_valid = None

    # ----------------------------------------------------------------
    # 内部ヘルパー
    # ----------------------------------------------------------------

    def _accumulate(
        self, shape: tuple[int, ...], valid_now: np.ndarray, mm_now: np.ndarray
    ) -> None:
        """初期化窓の1フレームぶんを画素ごとの合計・有効サンプル数へ積算する。"""
        if self._sum_mm is None:
            self._sum_mm = np.zeros(shape, dtype=np.float64)
            self._valid_count = np.zeros(shape, dtype=np.int64)
        assert self._valid_count is not None

        # 無効画素の値（raw=0 由来の mm）を合計に混入させない
        # （タスク本文「無効画素を背景モデルにも…含めない」）。
        self._sum_mm += np.where(valid_now, mm_now, 0.0)
        self._valid_count += valid_now

        self._frames_seen += 1
        if self._frames_seen >= self._background_frames:
            self._finalize_background()

    def _finalize_background(self) -> None:
        """蓄積した合計・有効サンプル数から画素ごとの背景 mm を確定する。"""
        assert self._sum_mm is not None
        assert self._valid_count is not None

        # 有効サンプルが1回も無かった画素は背景「未知」として扱う
        # （モジュール docstring「無効 Depth の扱い」節、設計判断1参照）。
        self._background_valid = self._valid_count > 0
        self._background_mm = np.zeros_like(self._sum_mm)
        np.divide(
            self._sum_mm,
            self._valid_count,
            out=self._background_mm,
            where=self._background_valid,
        )

        self._sum_mm = None
        self._valid_count = None
        self._ready = True

    def _update_background(
        self, valid_now: np.ndarray, mm_now: np.ndarray, foreground: np.ndarray
    ) -> None:
        """`background_update_rate > 0.0` のときだけ呼ばれる背景モデルの更新。

        今回有効かつ前景と判定されなかった画素だけを更新対象にする
        （モジュール docstring「背景モデルの更新」節参照。飛翔体を背景へ
        取り込まないための除外）。
        """
        assert self._background_mm is not None
        assert self._background_valid is not None

        update_target = valid_now & ~foreground
        # 背景が「未知」だった画素は、指数移動平均のベースとなる値を
        # 持たないため、今回の値をそのまま採用する。
        newly_defined = update_target & ~self._background_valid
        already_defined = update_target & self._background_valid

        self._background_mm[newly_defined] = mm_now[newly_defined]

        rate = self._background_update_rate
        self._background_mm[already_defined] = (
            (1.0 - rate) * self._background_mm[already_defined]
            + rate * mm_now[already_defined]
        )

        self._background_valid = self._background_valid | newly_defined
