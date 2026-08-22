"""検出の契約と既定実装（design.md「L4-L5: 検出 → Detector / CandidateExtractor /
CandidateFilter」、タスク 2.7）。

本モジュールは「フレーム → 候補集合 ＋ 除外内訳 ＋ 前景画素数」という1段の
契約（`Detector` プロトコルと `DetectResult`）を定義し、その唯一の既定実装
（`DefaultDetector`）を提供する。既定実装 `DefaultDetector.detect()` は

    (1) ROI 切り出し（`mask_ops.crop_roi`）
    (2) 前景マスク生成（`MaskBuilder.build` — 方式ごとに異なる。ここだけが可変）
    (3) 形態処理（`mask_ops.clean_mask`）
    (4)+(5) 候補化と粗い絞り込み（`CandidateExtractor.extract`。内部で
        `mask_ops.components` を呼ぶ）
    (6) `DetectResult` の組み立て（`mask_px` は (3) の結果の前景画素数）

という手順で構成される。(2) だけが `DetectorConfig.kind` に応じて
`create_mask_builder`（タスク 2.5）が返す具象クラスへ委譲され、(1)(3)(4)(5)(6)
は `detect()` という**単一のメソッド本体**を3方式すべてが共有する
（「方式間で後段が共有されることを構造で保証する」というタスク本文の要求
そのもの）。方式ごとに別クラスを用意して後段を重複実装する余地が構造的に
無い。

**検出の実行に学習済みモデルを必須としない**（要件 3.7）。3方式
（`depth_band` / `frame_diff` / `background`）はいずれも距離・差分の
閾値処理と連結成分ラベリングだけで構成され、ニューラルネットワーク等の
学習済みモデルへの依存を一切持たない。

**対象物の種類判別（分別）を行わない**（要件 3.6）。本モジュールが返す
候補は「前景として検出された領域」であり、それが空き缶か人の手か背景の
揺らぎかを判定する分類器を持たない。誤検出の除外は寸法・距離という幾何的
な条件（粗いふるいは `CandidateExtractor`、寸法の本判定は後続タスクの
`PointEstimator`）に限られる。

`create_detector` が唯一の生成口である
----------------------------------------------------------------------
要件 4.2「使用する検出方式を、コードの変更なしに設定として選択できるように
する」を満たすため、`TrackingSettings` から `Detector` を構築できるのは
**この関数だけ**である。CLI・`DetectorComparison`（タスク 7.x）等の
呼び出し側は `DefaultDetector` を直接コンストラクトせず、必ず
`create_detector` を経由すること（`create_mask_builder` が3方式の生成口を
一点に集約しているのと同じ設計原則。`masks/__init__.py` docstring参照）。

フレーム依存の ROI 解決について
----------------------------------
`RoiConfig.width_px` / `height_px` は `None`（フレーム全体）を許す
（config.py docstring「処理範囲（ROI）の既定挙動」）。`config.py` は
「フレーム依存の検証（ROI がフレームに収まるか）は最初のフレーム受領時に
1度だけ行う（後続タスクの責務）」と明記しており、本タスクがその後続タスク
である。`DefaultDetector` は最初の `detect()` 呼び出しで、渡された
`CaptureFrame.profile` から実寸法を確定した `Roi`（`flying_object_tracking.
types.Roi`）を組み立て、ROI がフレーム範囲に収まっているかを検証する
（収まっていなければ `TrackingConfigError`。設定不正の拒否は「呼び出し方・
設定の誤り」区分であり例外にしてよい——`errors.py` docstring参照）。同時に、
`MaskBuilder`（`create_mask_builder` 経由）と `CandidateExtractor` もこの
時点で構築し、以後のフレームで使い回す。`depth_scale_mm` は
`CaptureFrame.profile` からしか得られないため、`create_detector(settings)`
の時点ではまだ構築できない（同一セッション内でフレームの寸法・
`depth_scale_mm` は変わらない前提を置く）。

`postprocess_fingerprint()` との関係
----------------------------------------
「3方式それぞれの検出器が同一の合成フレーム列から候補を返し、後段設定が
同一であることを指紋の一致で確認できる」（タスク本文の観測可能な完了状態）
は、`TrackingSettings.postprocess_fingerprint()`（タスク 1.4 で実装済み）を
そのまま使う。本モジュールは新しい指紋機構を作らない。`postprocess_fingerprint()`
の対象は `detector`（`kind` を含む）を除く5フィールドであるため、`kind` だけ
が異なる3つの `TrackingSettings` から `create_detector()` で作った3つの
`Detector` は、それぞれの構築元 `TrackingSettings` の指紋が一致することを
呼び出し側（テスト、および将来の `DetectorComparison`）が直接
`settings.postprocess_fingerprint()` で確認できる。`Detector` 自身に指紋を
公開するプロパティを追加する必要はない（呼び出し側が構築に使った
`TrackingSettings` を保持していれば十分であり、それが最も単純な経路である）。

依存方向（design.md「Dependency Direction」表の `detection/detector` 行）:
0〜4層（`errors` / `types` / `config` / `metrics` / `detection/mask_ops` /
`detection/masks/*` / `detection/candidates`）に加え、`Detector.detect()` の
引数型として `sensing_foundation`（公開入口の `CaptureFrame`）を import する
（`types.py` が `SourceKind` を、`candidates.py` が `INVALID_DEPTH_RAW` を
同様に公開入口から import しているのと同じ扱い）。`cv2` は直接 import
しない（`mask_ops` が提供する関数だけを呼ぶ）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np
from sensing_foundation import CaptureFrame

from flying_object_tracking.config import RoiConfig, TrackingSettings
from flying_object_tracking.detection import mask_ops
from flying_object_tracking.detection.candidates import CandidateExtractor
from flying_object_tracking.detection.masks import MaskBuilder, create_mask_builder
from flying_object_tracking.errors import TrackingConfigError
from flying_object_tracking.types import Candidate, CandidateRejection, DetectorKind, Roi

__all__ = ["DetectResult", "Detector", "DefaultDetector", "create_detector"]


@dataclass(frozen=True, slots=True)
class DetectResult:
    """`Detector.detect()` の戻り値（design.md「Detector / CandidateExtractor /
    CandidateFilter」節の型そのもの）。

    `CandidateExtractor.extract()` が返す `CandidateExtractionResult`
    （`candidates` / `rejections`）に、前景画素数 `mask_px` を足した最終形。
    `mask_px` の算出と型の組み立ては本モジュール（タスク 2.7）の責務であり、
    `CandidateExtractor`（タスク 2.6）はこの型を知らない
    （`candidates.py` モジュール docstring 参照）。
    """

    candidates: tuple[Candidate, ...]
    """粗いふるいを通過した候補（全画面座標。面積降順）。"""
    rejections: tuple[CandidateRejection, ...]
    """除外理由ごとの件数。"""
    mask_px: int
    """前景画素数（形態処理後）。方式の暴走を検知する材料。"""


@runtime_checkable
class Detector(Protocol):
    """フレーム → 候補集合という1段の契約（design.md 記載のプロトコルそのもの）。

    `@runtime_checkable` にしてある理由は `masks/__init__.py` の
    `MaskBuilder` と同じ: `isinstance(detector, Detector)` による構造的な
    契約確認をテストで行えるようにするため。
    """

    @property
    def kind(self) -> DetectorKind:
        """この `Detector` が使う検出方式。"""
        ...

    def detect(self, frame: CaptureFrame) -> DetectResult:
        """1フレームから候補集合を検出する。

        Preconditions:
            `frame.depth` は読み取り専用。ROI はフレーム範囲に収まって
            いること。
        Postconditions:
            候補が1つも無ければ `candidates=()` を返し、**例外を送出
            しない**（要件 3.5）。ただし設定不正（ROI がフレーム範囲外・
            未知の検出方式）は「呼び出し方・設定の誤り」区分の例外として
            送出してよい（`errors.py` の3区分参照。実装は初回フレーム
            受領時にこれを検出する）。
        Invariants:
            候補の座標は全画面座標（ROI オフセット加算済み。要件 2.5）。
        """
        ...

    def reset(self) -> None:
        """検出器の内部状態（背景モデル・直前フレーム等）を初期化する。"""
        ...


class DefaultDetector:
    """`Detector` の既定実装（design.md「既定実装を『方式ごとの前景マスク
    生成 ＋ 共通の後段』として構成し、方式間で後段が共有されることを構造で
    保証する」節）。

    3方式のうち可変なのは `MaskBuilder.build()` の呼び出し1箇所だけであり、
    それ以外（ROI 切り出し・形態処理・候補化・粗い絞り込み・`DetectResult`
    組み立て）は `detect()` という単一のメソッド本体を経由する共有コードで
    ある。方式ごとに別クラスを書く余地が構造的に無いことが、「後段が方式間で
    交絡しない」（design.md「MaskBuilder と3方式」節、OQ-26 比較の前提）を
    保証する。

    **学習済みモデルを必須としない**（要件 3.7）。**対象物の種類判別を
    行わない**（要件 3.6）。詳細はモジュール docstring 参照。

    直接コンストラクトしないこと。`create_detector(settings)` を経由する
    こと（モジュール docstring「`create_detector` が唯一の生成口である」）。
    """

    def __init__(self, settings: TrackingSettings) -> None:
        self._settings = settings
        self._kind = settings.detector.kind
        # 最初の detect() 呼び出しまで構築を遅延する（フレーム依存の ROI
        # 解決・depth_scale_mm がこの時点では得られないため。モジュール
        # docstring「フレーム依存の ROI 解決について」参照）。
        self._roi: Roi | None = None
        self._mask_builder: MaskBuilder | None = None
        self._candidate_extractor: CandidateExtractor | None = None

    @property
    def kind(self) -> DetectorKind:
        return self._kind

    def detect(self, frame: CaptureFrame) -> DetectResult:
        if self._roi is None:
            self._initialize(frame)
        assert self._roi is not None
        assert self._mask_builder is not None
        assert self._candidate_extractor is not None

        # (1) ROI 切り出し（共有: 方式に依らないコード）。
        roi_depth = mask_ops.crop_roi(frame.depth, self._roi)

        # (2) 前景マスク生成。ここだけが `settings.detector.kind` に応じて
        # 異なる具象クラス（`create_mask_builder` が返す）へ委譲される。
        raw_mask = self._mask_builder.build(roi_depth)

        # (3) 形態処理（共有: 方式に依らないコード）。
        cleaned_mask = mask_ops.clean_mask(
            raw_mask,
            self._settings.detector.open_kernel_px,
            self._settings.detector.close_kernel_px,
        )

        # (4)+(5) 連結成分の候補化と粗い絞り込み（共有: 内部で
        # `mask_ops.components` を呼ぶ同一の `CandidateExtractor` インスタンス
        # を経由する）。
        extraction = self._candidate_extractor.extract(cleaned_mask, roi_depth)

        # (6) DetectResult の組み立て（共有）。mask_px は形態処理後の
        # 前景画素数（方式の暴走を検知する材料。design.md 記載どおり）。
        mask_px = int(np.count_nonzero(cleaned_mask))

        return DetectResult(
            candidates=extraction.candidates,
            rejections=extraction.rejections,
            mask_px=mask_px,
        )

    def reset(self) -> None:
        """検出方式の内部状態（背景モデル・直前フレーム）を初期化する。

        `MaskBuilder.reset()` へ委譲する。まだ1度も `detect()` を呼んで
        いない場合（`MaskBuilder` 未構築）は no-op。ROI・`CandidateExtractor`
        は破棄しない（フレームの寸法・プロファイルは同一セッション内で
        変わらない前提のため、再構築の必要が無い）。
        """
        if self._mask_builder is not None:
            self._mask_builder.reset()

    def _initialize(self, frame: CaptureFrame) -> None:
        """最初の `detect()` 呼び出しで、フレーム依存の構築を1度だけ行う。"""
        roi = _resolve_roi(self._settings.roi, frame)
        # create_mask_builder が未知の kind を TrackingConfigError として
        # 拒否する（masks/__init__.py 参照）。本メソッドはそれをそのまま
        # 伝播させるだけであり、独自に再実装しない。
        mask_builder = create_mask_builder(
            self._settings.detector, roi, depth_scale_mm=frame.profile.depth_scale_mm
        )
        candidate_extractor = CandidateExtractor(
            self._settings.object_model, roi, self._settings.detector.max_candidates
        )

        self._roi = roi
        self._mask_builder = mask_builder
        self._candidate_extractor = candidate_extractor


def _resolve_roi(roi_config: RoiConfig, frame: CaptureFrame) -> Roi:
    """`RoiConfig`（`width_px`/`height_px` が `None` を許す）から、
    `frame.profile` の実寸法で確定した `Roi` を組み立てる。

    Raises:
        TrackingConfigError: ROI がフレーム範囲に収まらない場合
            （`config.py` の Invariants「ROI が指定されている場合、
            フレーム範囲内に収まること」を最初のフレーム受領時に検証する
            後続タスクの責務。`config.py` docstring「フレーム依存の検証」
            参照）。
    """
    frame_width_px = frame.profile.width_px
    frame_height_px = frame.profile.height_px
    width_px = roi_config.width_px if roi_config.width_px is not None else frame_width_px
    height_px = roi_config.height_px if roi_config.height_px is not None else frame_height_px

    if (
        roi_config.x_px < 0
        or roi_config.y_px < 0
        or width_px <= 0
        or height_px <= 0
        or roi_config.x_px + width_px > frame_width_px
        or roi_config.y_px + height_px > frame_height_px
    ):
        raise TrackingConfigError(
            "roi がフレーム範囲に収まらない: "
            f"roi=(x={roi_config.x_px}, y={roi_config.y_px}, "
            f"w={width_px}, h={height_px}), "
            f"frame=(w={frame_width_px}, h={frame_height_px})"
        )

    return Roi(
        x_px=roi_config.x_px,
        y_px=roi_config.y_px,
        width_px=width_px,
        height_px=height_px,
        z_min_mm=roi_config.z_min_mm,
        z_max_mm=roi_config.z_max_mm,
    )


def create_detector(settings: TrackingSettings) -> Detector:
    """設定から `Detector` を生成する唯一の生成口（design.md 署名どおり）。

    `settings.detector.kind` に応じた前景マスク生成方式を使う
    `DefaultDetector` を構築する。**この関数以外の場所で `DefaultDetector`
    を直接コンストラクトしないこと**（モジュール docstring「`create_detector`
    が唯一の生成口である」節参照）。

    Args:
        settings: 解決済みの実行時設定。

    Returns:
        `settings.detector.kind` に対応する検出方式で動作する `Detector`。
        構築自体は未知の `kind` でも成功する（フレーム依存の初期化を最初の
        `detect()` 呼び出しまで遅延するため）。未知の `kind` はその最初の
        `detect()` 呼び出しで `TrackingConfigError` として拒否される
        （`create_mask_builder` の既存防御。モジュール docstring参照）。
    """
    return DefaultDetector(settings)
