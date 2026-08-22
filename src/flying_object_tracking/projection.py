"""候補領域からカメラ座標系の代表3D点を求める（design.md「L4-L5: 検出 →
PointEstimator」、タスク 3.1）。

**逆投影の基本演算は自前で書かない。** 無効画素の判定・距離値から mm への
換算・画素からカメラ座標への逆投影は、上流 `sensing_foundation` の公開入口
（`sensing_foundation.__init__`）が提供する3つの純関数
（`is_valid_depth` / `depth_raw_to_mm` / `deproject_pixel`）にのみ委ねる
（要件 5.8）。本モジュールが持つのは**集約方針**である:
候補領域内の**どの画素をどう代表させるか**（トリム平均による代表距離の
決定）、候補の絞り込み（寸法・距離帯）、内部パラメータ出所の記録、
失敗理由の分類——これらは design.md「PointEstimator / 責務の分割」表が
定める通り、上流ではなく本 Spec 固有の方針である。

**候補の外接矩形の外側を一切読まない**（要件 5.6）。`estimate()` は
`frame.depth` のうち `candidate.bbox_px` が指すスライス（ビュー。コピー
しない）だけを参照する。**全画面の点群を作らない**（要件 2.6）。
`deproject_pixel()` を呼ぶのは候補1つにつき1回、代表点1個分のみであり、
フレーム全体の画素をループして逆投影することは無い。

`is_valid_depth()` / `depth_raw_to_mm()` の実呼び出し方について
----------------------------------------------------------------------
候補の外接矩形は複数画素からなる配列であり、そのうち「どの画素が有効か」
（無効画素の除外）と「代表距離を求めるためのトリム対象の mm 換算」は
**配列全体**に対する演算になる。これらは `sensing_foundation.geometry` の
`is_valid_depth(raw)`（`raw != INVALID_DEPTH_RAW`）・`depth_raw_to_mm(raw,
scale)`（`raw * scale`）と**まったく同じ式**を `numpy` のネイティブな配列
演算としてベクトル化して適用する（`detection/masks/depth_band.py` /
`detection/candidates.py` と同一の技法・同一の性能上の理由: スカラー純
関数を `numpy.vectorize` 越しに要素ごとに呼ぶと Pi 4 の低レイテンシ制約に
反する致命的な速度低下を招くことが実測済みであり、この配列レベルの
ベクトル化は「上流の判定基準・換算式を再定義する」のではなく「同一の
基準・式を配列に適用する」ことにあたる、というこれまでの3タスク
（depth_band / frame_diff / background の各マスク生成、candidates.py の
`valid_depth_px` 算出）で確立済みのレビュー済み方針を踏襲する）。

一方、**代表距離1個分の mm 換算**（トリム平均で1候補につき1回だけ生じる
スカラー値の変換）は、上記のベクトル化とは性質が異なる——これは「代表値
そのもの」であり、design.md のアルゴリズム上の約束4「代表距離の mm 換算は
`sensing_foundation.depth_raw_to_mm(raw, profile.depth_scale_mm)` でのみ
行う」が名指しする対象そのものである。したがって本モジュールは、トリム後の
代表 raw 値（複数画素のトリム平均。まだ mm 化されていない）を実際に
`sensing_foundation.depth_raw_to_mm()` へ**関数として呼び出して**渡し、
その戻り値をそのまま逆投影の `z_mm` に使う（`* depth_scale_mm` を自分で
書かない）。同様に、**逆投影そのもの**（ピンホール式・主点の減算・焦点
距離での除算）は候補1つにつき1回しか生じないため、常に
`sensing_foundation.deproject_pixel()` を実関数呼び出しとして使う
（ピンホール式を本モジュールに書かない）。

トリム平均を raw 領域で行う理由
----------------------------------
代表距離は「両側 `depth_trim_ratio` を落としたトリム平均」で求める
（design.md アルゴリズム上の約束3）。`depth_scale_mm` は正の定数倍率
であるため、raw 値を昇順に並べて両端をトリムした結果と、mm 換算後の値を
昇順に並べて両端をトリムした結果は**同じ画素集合**になる（正の定数倍は
順序を保つため）。したがって本モジュールは並べ替え・トリムを raw 領域で
行い、トリム後の raw 平均を**1回だけ**実際の `depth_raw_to_mm()` へ渡す。
ばらつき（`depth_spread_mm`）の算出だけは `max_depth_spread_mm` という
mm 領域の閾値と比較するために、トリム後の集合を mm 換算配列（上記の
ベクトル化済み配列）から同じ並べ替えで取り出して使う。

`SIZE_MISMATCH` の返り値の形についての設計判断（★重要・レビュー時に要確認）
------------------------------------------------------------------------------
design.md の `PointEstimator.estimate()` 型注釈は `CameraPoint |
PointFailure` だが、`PointFailureReason` 列挙型には `SIZE_MISMATCH` が
存在しない（`INSUFFICIENT_VALID_PIXELS` / `DEPTH_SPREAD_TOO_LARGE` /
`OUT_OF_DEPTH_BAND` の3つのみ）。一方 `SIZE_MISMATCH` は
`CandidateExtractor`（タスク2.6）の粗いふるいと**同じ** `RejectReason`
列挙型のメンバーとして既に存在する。さらに design.md の Requirements
Traceability 表は要件 3.3/3.4（寸法・距離による絞り込みと除外の計数）の
Interface 欄に `CandidateRejection` を挙げ、対応コンポーネントを
`CandidateFilter, PointEstimator` としている——すなわち design.md 自身が、
`PointEstimator` の寸法本判定の出力形を `CandidateRejection` として
扱うことを示唆している。

これらを踏まえ、本実装は `estimate()` の返り値型を
`CameraPoint | PointFailure | CandidateRejection` の3値に**拡張する**
という設計判断を下す。理由:

1. `PointFailure` は「3D位置の**算出そのもの**が失敗した」ことを表す
   （有効画素不足・ばらつき過大・距離帯外——いずれも「測れなかった」
   という失敗）。一方 `SIZE_MISMATCH` は測距・逆投影には成功したが、
   「対象物として扱わない」という**候補の絞り込み**判断であり、
   `CandidateExtractor` の粗いふるい（`TOO_SMALL_PX` 等）と意味的に同種
   である。両者を同じ `CandidateRejection` 型で表現することで、後続の
   `TrackingPipeline`（タスク6系）が検出段の rejections と本判定の
   rejections を同一の集計経路（`RejectReason` ごとの計数）へ合流でき、
   `PointFailureReason` を汚染しない。
2. `PointFailureReason` に `SIZE_MISMATCH` を追加しない。追加すると
   「候補の絞り込み理由」と「3D化の失敗理由」の意味的区別が列挙型の
   レベルで失われ、`RejectReason.SIZE_MISMATCH` と重複した語彙を持つ
   ことになる。
3. `count=1` の `CandidateRejection` を1候補につき1つ返す（本メソッドは
   候補1つを処理する契約であるため、複数件の集計はしない。呼び出し側
   ——将来の `TrackingPipeline`——が複数候補分を合算する）。

この設計判断は design.md の型注釈を字面どおりには実装しない選択であり、
**タスク実装者の裁量で行った判断としてレビューに明示する**（本モジュール
docstring とタスクのステータスレポート双方に記載する）。

`apparent_diameter_px` の導出方法についての設計判断
--------------------------------------------------------
design.md は `CameraPoint.apparent_diameter_px` フィールドの存在は定める
が、`Candidate` のどのフィールドからどう導出するかを明記していない。
本モジュールは**面積等価円の直径**（`2 * sqrt(area_px / pi)`）を採用する。
対象物（空き缶）はカメラに対して概ね丸い投影を作るという設計上の前提
（requirements.md A-3: 「形状が一定で剛体」）に対して、bbox の幅・高さの
平均よりも、連結成分の実際の前景画素数（`area_px`）から導く面積等価直径
のほうが、細長い誤検出（人の手など）の bbox が偶然正方形に近い場合との
混同を避けやすく、形状の歪みに頑健である。`expected_diameter_px`
（`fx_px * diameter_mm / z_mm`。円の実寸法から導く期待「直径」）と
定義の系統を揃えられる点も採用理由である。

内部パラメータの出所（`intrinsics_source`）
------------------------------------------------
現時点では逆投影に使う `CameraIntrinsics` は常に
`frame.profile.intrinsics`（`sensing_foundation` の `StreamProfile` が
保持する値）から得られるため、`intrinsics_source` には固定文字列
`"stream_profile"` を記録する。design.md「PointEstimator / Implementation
Notes」が明記する通り、これは**将来キャリブレーションが別の内因パラメータ
出所を持つ可能性に備えて先に用意する記録用の枠**であり、現時点で複数の
出所を判別するロジックは存在しない。

内部パラメータが得られない場合（要件 10.4）
------------------------------------------------
`frame.profile.intrinsics` が `None` の場合、**推測して続行せず**
`flying_object_tracking.errors.IntrinsicsUnavailableError` を送出する。
これは「正常系の無効」（値で返す）ではなく「前提の欠落」（例外で失敗する）
に分類される（`errors.py` モジュール docstring の3区分表を参照）。

依存方向（design.md「Dependency Direction」表の `projection` 行）:
0〜2層（`errors` / `types` / `config`）＋ `numpy` ＋ `sensing_foundation`
（公開入口の逆投影基本演算 `depth_raw_to_mm` / `is_valid_depth` /
`deproject_pixel`）を import してよい。`detection` を import しない。
`INVALID_DEPTH_RAW` も同じ公開入口（`sensing_foundation.geometry` が
`is_valid_depth()` の定義に使う公開定数）から追加で import する——
`detection/masks/depth_band.py` が同じ理由（`is_valid_depth` と同値の式を
ベクトル化するための基準値）で既に同じ拡張解釈を採っている（同モジュール
docstring参照）。`cv2` は使わない。
"""

from __future__ import annotations

import math

import numpy as np
from sensing_foundation import (
    INVALID_DEPTH_RAW,
    CaptureFrame,
    depth_raw_to_mm,
    deproject_pixel,
)

from flying_object_tracking.config import ObjectModelConfig, ProjectionConfig
from flying_object_tracking.errors import IntrinsicsUnavailableError
from flying_object_tracking.types import (
    Candidate,
    CameraPoint,
    CandidateRejection,
    CoordinateFrame,
    PointFailure,
    PointFailureReason,
    RejectReason,
    Roi,
)

__all__ = ["PointEstimator"]

_INTRINSICS_SOURCE = "stream_profile"
"""現時点で逆投影に使う内部パラメータの唯一の出所（モジュール docstring参照）。"""


class PointEstimator:
    """候補領域内の有効 Depth 画素だけからカメラ座標系の代表3D点を求める。

    逆投影の基本演算（無効画素の判定・mm 換算・ピンホール逆投影）は上流
    `sensing_foundation` の公開入口に委ね、本クラスは「候補領域内のどの
    画素をどう代表させるか」という集約方針と、寸法・距離帯による候補の
    本判定だけを持つ（design.md「PointEstimator / 責務の分割」表）。

    Preconditions:
        `estimate()` に渡す `frame.profile.intrinsics` が `None` でない
        こと。`None` の場合は `IntrinsicsUnavailableError` を送出する
        （要件 10.4）。

    Postconditions:
        返り値が `CameraPoint` のとき、`frame == CoordinateFrame.CAMERA`
        かつ `t_ms == frame.t_capture_ms`（上流の時刻をそのまま引き継ぐ。
        要件 5.7）。

    Invariants:
        **候補の外接矩形の外側を一切読まない**（要件 5.6）。
        **全画面の点群を作らない**（要件 2.6）: `deproject_pixel()` を
        呼ぶのは1候補につき代表点1個分だけである。
    """

    def __init__(
        self, object_model: ObjectModelConfig, projection: ProjectionConfig, roi: Roi
    ) -> None:
        """絞り込み条件と距離帯を固定する。

        Args:
            object_model: 対象物の想定直径（`diameter_mm`）と寸法本判定の
                許容倍率（`min_scale` / `max_scale`）を持つ設定。
            projection: 有効画素数の下限（`min_valid_depth_px`）・
                トリム比率（`depth_trim_ratio`）・ばらつき上限
                （`max_depth_spread_mm`）を持つ設定。
            roi: 処理対象範囲。`z_min_mm` / `z_max_mm`（距離帯の本判定）
                だけを使う。画素矩形部分（`x_px` 等）は `estimate()` が
                受け取る `Candidate.bbox_px` が既に全画面座標であるため
                使わない。
        """
        self._object_model = object_model
        self._projection = projection
        self._roi_z_min_mm = roi.z_min_mm
        self._roi_z_max_mm = roi.z_max_mm

    def estimate(
        self, frame: CaptureFrame, candidate: Candidate
    ) -> CameraPoint | PointFailure | CandidateRejection:
        """候補1つからカメラ座標系の代表3D点を求める（design.md の7段階）。

        処理の流れ（design.md「PointEstimator / アルゴリズム上の約束」）:

        1. 候補の外接矩形（`candidate.bbox_px`。全画面座標）内の Depth を
           読み、無効画素を除外する（`is_valid_depth()` と同値の判定。
           埋めない）。
        2. 有効画素数が `projection.min_valid_depth_px` 未満なら
           `PointFailure(INSUFFICIENT_VALID_PIXELS, ...)`。
        3. 代表距離をトリム平均で求める。ばらつき
           （`max(トリム後) - min(トリム後)`、mm）が
           `projection.max_depth_spread_mm` を超えたら
           `PointFailure(DEPTH_SPREAD_TOO_LARGE, ...)`。
        4. トリム後の代表 raw 値を `sensing_foundation.depth_raw_to_mm()`
           へ実際に渡して mm 換算する。
        5. `sensing_foundation.deproject_pixel()` へ、手順4の `z_mm` と
           候補重心の小数画素座標（`candidate.cx_px` / `cy_px`。`+0.5`
           補正なし）を渡してカメラ座標系の点を得る。`t_ms` は
           `frame.t_capture_ms` をそのまま引き継ぐ。
        6. 寸法の本判定: 実測距離から期待画素直径を導出し、候補の見かけの
           直径（面積等価円）が許容倍率の範囲外なら
           `CandidateRejection(SIZE_MISMATCH, count=1)`
           （返り値の形についての設計判断はモジュール docstring 参照）。
        7. 距離帯（`roi.z_min_mm` / `roi.z_max_mm`）の外なら
           `PointFailure(OUT_OF_DEPTH_BAND, ...)`。

        Args:
            frame: 処理対象のフレーム。`frame.depth` は候補の外接矩形の
                範囲だけを読む（読み取り専用のビュー。変更しない）。
            candidate: 3D位置を求める対象の候補（全画面座標）。

        Returns:
            成功時は `CameraPoint`。3D化そのものが失敗した場合は
            `PointFailure`。寸法が対象物と合わず候補として除外する場合は
            `CandidateRejection`。

        Raises:
            IntrinsicsUnavailableError: `frame.profile.intrinsics` が
                `None` の場合（要件 10.4）。推測して続行しない。
        """
        intrinsics = frame.profile.intrinsics
        if intrinsics is None:
            raise IntrinsicsUnavailableError(
                "frame.profile.intrinsics が None のため逆投影を実行できない。"
                "入力元の StreamProfile にカメラ内部パラメータが設定されて"
                "いない（RealSense 実機からの取得に失敗しているか、"
                "simulated/recorded 入力の StreamProfile 構築時に "
                "CameraIntrinsics を渡し忘れていないか確認すること）。"
            )

        x0_px, y0_px, w_px, h_px = candidate.bbox_px
        # 候補の外接矩形の外側を一切読まない（要件 5.6）。スライスは
        # ビューであり、`frame.depth` 全体をコピーしない。
        bbox_depth = frame.depth[y0_px : y0_px + h_px, x0_px : x0_px + w_px]

        # 手順1: 無効画素の除外。`sensing_foundation.geometry.
        # is_valid_depth()`（`raw != INVALID_DEPTH_RAW`）と同一の式を
        # 配列全体へネイティブな NumPy 演算として適用する（モジュール
        # docstring「`is_valid_depth()` / `depth_raw_to_mm()` の実呼び出し
        # 方について」参照。`numpy.vectorize` は使わない）。欠測を 0 や
        # 推定値で埋めない（要件 10.1 / 10.6）。
        valid_mask = bbox_depth != INVALID_DEPTH_RAW
        valid_count = int(np.count_nonzero(valid_mask))

        # 手順2: 有効画素数の下限。
        if valid_count < self._projection.min_valid_depth_px:
            return PointFailure(
                reason=PointFailureReason.INSUFFICIENT_VALID_PIXELS,
                valid_depth_px=valid_count,
            )

        valid_raw = bbox_depth[valid_mask]
        # 代表値算出のためだけに使う mm 配列。`sensing_foundation.geometry.
        # depth_raw_to_mm()`（`raw * depth_scale_mm`）と同一の式を配列
        # 全体へネイティブな NumPy 演算として適用する（同上の理由）。
        valid_mm = valid_raw.astype(np.float64) * frame.profile.depth_scale_mm

        # 手順3: トリム平均（raw 領域で並べ替え・トリムする理由はモジュール
        # docstring「トリム平均を raw 領域で行う理由」参照）。
        order = np.argsort(valid_raw, kind="stable")
        sorted_raw = valid_raw[order]
        sorted_mm = valid_mm[order]
        n = sorted_raw.shape[0]
        trim_count = int(n * self._projection.depth_trim_ratio)
        if n - 2 * trim_count <= 0:
            # 安全側フォールバック: トリムすると空になる場合はトリムしない
            # （`ProjectionConfig` の Precondition `0.0 <= depth_trim_ratio
            # < 0.5` により通常発生しないが、極小の `valid_count` でも
            # 空スライスによる例外を避ける）。
            trim_count = 0
        kept_raw = sorted_raw[trim_count : n - trim_count] if trim_count > 0 else sorted_raw
        kept_mm = sorted_mm[trim_count : n - trim_count] if trim_count > 0 else sorted_mm

        spread_mm = float(kept_mm.max() - kept_mm.min()) if kept_mm.size > 1 else 0.0
        if spread_mm > self._projection.max_depth_spread_mm:
            return PointFailure(
                reason=PointFailureReason.DEPTH_SPREAD_TOO_LARGE,
                valid_depth_px=valid_count,
            )

        # 手順4: 代表 raw 値（トリム平均）の mm 換算。上流の実関数を呼ぶ
        # （`* depth_scale_mm` を自分で書かない。要件 5.8）。
        representative_raw = float(np.mean(kept_raw))
        z_mm = depth_raw_to_mm(representative_raw, frame.profile.depth_scale_mm)

        # 手順5: 逆投影。上流の実関数を呼ぶ（ピンホール式を自分で書かない。
        # 要件 5.8）。候補重心の小数画素座標をそのまま渡す（`+0.5` 補正
        # なし。画素中心の規約は上流が持つ）。
        x_mm, y_mm, z_mm_out = deproject_pixel(
            intrinsics, u_px=candidate.cx_px, v_px=candidate.cy_px, z_mm=z_mm
        )

        # 手順6: 寸法の本判定（要件 3.3）。`expected_diameter_px` は
        # ピンホール逆投影そのものではなく、対象物の実寸法から導く期待
        # 画素サイズであり、本 Spec 固有の候補絞り込みロジックである
        # （モジュール docstring 参照。`sensing_foundation` にはこの式は
        # 存在しない）。
        expected_diameter_px = (
            intrinsics.fx_px * self._object_model.diameter_mm / z_mm_out
        )
        apparent_diameter_px = _circle_equivalent_diameter_px(candidate.area_px)

        lower_bound_px = self._object_model.min_scale * expected_diameter_px
        upper_bound_px = self._object_model.max_scale * expected_diameter_px
        if not (lower_bound_px <= apparent_diameter_px <= upper_bound_px):
            return CandidateRejection(reason=RejectReason.SIZE_MISMATCH, count=1)

        # 手順7: 距離帯の本判定。
        if self._roi_z_min_mm is not None and z_mm_out < self._roi_z_min_mm:
            return PointFailure(
                reason=PointFailureReason.OUT_OF_DEPTH_BAND, valid_depth_px=valid_count
            )
        if self._roi_z_max_mm is not None and z_mm_out > self._roi_z_max_mm:
            return PointFailure(
                reason=PointFailureReason.OUT_OF_DEPTH_BAND, valid_depth_px=valid_count
            )

        return CameraPoint(
            frame=CoordinateFrame.CAMERA,
            t_ms=frame.t_capture_ms,
            x_mm=x_mm,
            y_mm=y_mm,
            z_mm=z_mm_out,
            valid_depth_px=valid_count,
            depth_spread_mm=spread_mm,
            apparent_diameter_px=apparent_diameter_px,
            expected_diameter_px=expected_diameter_px,
            intrinsics_source=_INTRINSICS_SOURCE,
        )


def _circle_equivalent_diameter_px(area_px: int) -> float:
    """前景画素数から面積等価円の直径を求める（モジュール docstring
    「`apparent_diameter_px` の導出方法についての設計判断」参照）。

    `area_px <= 0` は候補として構造的に発生しない想定だが（`Candidate` は
    `CandidateExtractor` の粗いふるいを通過した連結成分から作られ、
    `min_area_px > 0` を満たす）、念のため 0.0 で防御する。
    """
    if area_px <= 0:
        return 0.0
    return 2.0 * math.sqrt(area_px / math.pi)
