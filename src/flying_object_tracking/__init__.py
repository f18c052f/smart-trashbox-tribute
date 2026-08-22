"""flying-object-tracking: Depth フレーム列から飛来物体を検出し、
カメラ座標系での3D位置を追跡してカメラ座標点列を下流へ受け渡すパッケージ。

本モジュールは公開 API の**再エクスポート専用**であり、ロジックを一切持たない
（design.md「PublicApi / Responsibilities & Constraints」、要件 7.1 / 7.3、
タスク 8.1）。**下流（`world-frame-calibration` / `m1-prediction-validation`）
が本パッケージを参照してよい唯一の入口はこの `__init__` である。** 内部モジュール
（`flying_object_tracking.types` / `.config` / `.detection.*` / `.projection` /
`.tracking` / `.pipeline` / `.bench.*` / `.errors` / `.metrics` / `.cli` 等）へ
直接 import しないこと。この `__all__` に**明示列挙されたものだけ**が公開契約
であり、それ以外はすべて内部実装として扱う（変更・削除に予告や再検証の猶予を
要さない）。

design.md の Components 表「PublicApi」行は要件 7.1 / 7.3 を掲げるのみで、
`sensing_foundation.__init__`（タスク 8.2 相当）のように `__all__` 擬似コードを
コード片で与えていない。したがって本タスクは、design.md「Requirements
Traceability」表・各コンポーネント節・実装済みの各モジュールの `__all__` から
公開シンボルを導出し、以下の10カテゴリ（タスク本文が列挙する分類）へ整理した。

1. **座標系**: `CoordinateFrame`
2. **受け渡し型**: `Roi` / `Candidate` / `CandidateRejection` / `CameraPoint` /
   `PointFailure` / `TrackPoint` / `CameraTrack` / `TrackUpdate`
3. **候補と失敗の理由**: `RejectReason` / `PointFailureReason`
4. **検出方式と生成口**: `DetectorKind` / `Detector` / `create_detector` /
   `DetectResult`
5. **設定**: `TrackingSettings` / `RoiConfig` / `ObjectModelConfig` /
   `DetectorConfig` / `ProjectionConfig` / `TrackerConfig` / `MeasurementConfig`
6. **パイプラインと実行関数**: `TrackingPipeline` / `track_source`
7. **計測**: `TrackingMetrics` / `TrackingCounters` / `EffectiveWindow`
8. **比較結果**: `DetectorRunResult` / `DetectorDecision` /
   `DetectorComparisonResult` / `compare_detectors` /
   `OverheadResult` / `OverheadVerdict` / `OverheadReport` / `OverheadBench`
9. **例外**: `TrackingError` / `TrackingConfigError` /
   `IntrinsicsUnavailableError` / `DetectorUnavailableError`
10. **受け渡し形式の版番号**: `HANDOFF_VERSION`

`TrackState` / `TrackEndReason`（`CameraTrack.state` / `.end_reason` の型）と
`SourceKind`（`sensing_foundation` からの再エクスポートを `types.py` がさらに
再エクスポートしたもの。`CameraTrack.source` の型）も同じ理由——公開型
（`CameraTrack`）のフィールド型である以上、それを検査・比較する下流コードが
`isinstance` / 等価比較のために参照できる必要がある——で追加する。design.md
「Revalidation Triggers」が言う `__all__` の**削除・改名**（下流の再検証を要求
する変更）と**追加**（要求しない）の非対称性は `sensing_foundation.__init__`
（タスク 8.2）が既に確立した原則であり、本タスクも同じ扱いに従う。

**含めなかったもの（判断根拠）**:

- **`CaughtExceptionCounters`（`pipeline.py`）— 公開する。**
  `TrackingPipeline.caught_exceptions()` の戻り値型そのものであり、これを
  呼ぶ下流コードが型を import できないと戻り値を検査できない
  （`sensing_foundation` が `ThrowRecordReadIssue` 等の戻り値型をすべて
  公開しているのと同じ理由）。design.md の型注釈には現れないが（`pipeline.py`
  モジュール docstring「捕捉した例外の数え方について」が記す通り、design.md
  の記載からの意図的な逸脱として `TrackingPipeline` 自身が追加した公開契約
  である）、`TrackingPipeline` の一部として公開する。
- **`DefaultDetector`（`detection/detector.py`）— 公開しない。**
  `Detector` プロトコルの唯一の具象実装だが、`create_detector` が
  「設定から `Detector` を構築できる唯一の生成口」（同モジュール docstring）
  であり、`DefaultDetector` を直接コンストラクトしないことが設計上の要求
  そのものである。`sensing_foundation` が `SimulatedSource` /
  `RecordedSource` / `RealSenseSource` を非公開にし `open_source` に一本化
  したのと同じ原則（同パッケージ `__init__.py` docstring 参照）。
- **`MaskBuilder` / `create_mask_builder`（`detection/masks/__init__.py`）と
  3実装（`DepthBandMask` / `FrameDiffMask` / `BackgroundMask`）、
  `detection/mask_ops.py` の `crop_roi` / `clean_mask` / `components` /
  `Component`、`detection/candidates.py` の `CandidateExtractor` /
  `CandidateExtractionResult` — いずれも公開しない。**
  design.md「Requirements Traceability」表が公開契約として挙げるのは
  `Detector`（4.1〜4.3 の Interfaces 列）であり、`MaskBuilder` はその内部で
  検出方式を切り替えるための実装詳細（「前景マスクの作り方だけが異なる」
  design.md「Architecture Integration」）である。`m1-prediction-validation`
  の `seam.py`（本 Spec の唯一の消費者。design.md「Overview / Users」）が
  必要とするのは `CameraTrack` の点列だけであり、検出方式の内部構成
  （マスク生成・ROI 切り出し・形態処理・連結成分ラベリング）を知る必要が
  無い。`create_detector(settings)` が方式の選択（`DetectorConfig.kind`）を
  完結させるため、これらの内部片を公開入口へ出すと「検出方式の生成口を
  一点に集約する」（要件 4.2）という設計意図がかえって薄まる。
- **`PointEstimator`（`projection.py`）— 公開しない。**
  design.md の10カテゴリ列挙（タスク本文）に「逆投影」の独立項目が無く、
  `PointEstimator` はカテゴリ4「パイプラインと実行関数」に折り込まれている
  `TrackingPipeline` の内部依存としてのみ現れる（design.md「Architecture
  Integration」: 「結び付けるのは `TrackingPipeline` だけである」「実装が
  1つしかない抽象は置かない」）。下流（`m1-prediction-validation` の
  `seam.py`）が受け取るのは `PointEstimator.estimate()` の**結果**である
  `CameraPoint` / `CameraTrack` の点列であり、逆投影という演算そのものを
  単体で呼び出す必要は無い（design.md「Users」節も `seam.py` の関心を
  `CameraTrack` と計測値・追跡開始時刻に限定する）。
- **`SingleObjectTracker`（`tracking.py`) — 公開しない。**
  同様に design.md の10カテゴリ列挙に「追跡」の独立項目は無く、
  カテゴリ6「パイプラインと実行関数」（`TrackingPipeline` / `track_source`）
  に統合されている。`SingleObjectTracker` は `TrackingPipeline` が内部で
  構築・所有し（`pipeline.py`: 最初の `process()` 呼び出しまで構築を遅延）、
  呼び出し側が直接構築する契約は design.md のどこにも無い。
- **`bench/compare.py` の `DECISION_CRITERION` / `decide` / `parameter_count` /
  `write_comparison_result` / `comparison_output_path`、
  `bench/overhead.py` の `CRITERION_TEXT` / `compute_verdict` /
  `overhead_output_path` / `write_overhead_result` — いずれも公開しない。**
  `DetectorComparisonResult` / `OverheadReport`（結果型）と
  `compare_detectors` / `OverheadBench`（実行口）だけを公開する。判定規則の
  純粋関数（`decide` / `compute_verdict`）・パラメータ数の内訳計算
  （`parameter_count`）・判定基準の説明文定数（`DECISION_CRITERION` /
  `CRITERION_TEXT`）・JSON 書き出しヘルパー（`write_comparison_result` /
  `comparison_output_path` / `write_overhead_result` / `overhead_output_path`）
  は、いずれも `compare_detectors()` / `OverheadBench.run()` の内部で完結する
  実装詳細か、CLI（`compare-detectors` / `bench-overhead` サブコマンド）が
  結果を人が読む形へ整形するための道具であり、`DetectorComparisonResult` /
  `OverheadReport` 自身が判定基準の説明文（`criterion` フィールド）を既に
  内包しているため、下流が別途これらを呼ぶ必要は無い
  （design.md「DetectorComparison / Batch Job Contract」「比較結果」節参照）。
- **`cli.py` の `main` — 公開しない。**
  design.md「Dependency Direction」表が `cli` を `PublicApi` から独立した
  最終層（`PublicApi --> Cli`。`PublicApi` は `Cli` に依存されるが `Cli` の
  中身には依存しない）として位置づけており、CLI はコマンドとして使うもので
  あって import 契約ではない（`sensing_foundation.__init__` docstring
  「`bench`/`doctor`/`cli` の中身も公開入口には出さない（コマンドとして
  使う）」と同じ扱い）。
- **`detection` / `bench` サブパッケージの `__init__.py` それ自身、
  `mask_ops.py` の非公開ヘルパー、各モジュールの `_` 始まり関数
  （`_resolve_roi` / `_merge_rejections` / `_CapturingLogger` 等）— いずれも
  内部実装であり、そもそも各モジュール自身の `__all__` にも含まれない。**

依存方向（design.md「Dependency Direction」表の `__init__` 行）:
0〜8層すべて（`errors` 〜 `bench/*`）を import してよい（再エクスポートのみ。
ロジックを持たない）。
"""

from __future__ import annotations

from flying_object_tracking.bench.compare import (
    DetectorComparisonResult,
    DetectorDecision,
    DetectorRunResult,
    compare_detectors,
)
from flying_object_tracking.bench.overhead import (
    OverheadBench,
    OverheadReport,
    OverheadResult,
    OverheadVerdict,
)
from flying_object_tracking.config import (
    DetectorConfig,
    MeasurementConfig,
    ObjectModelConfig,
    ProjectionConfig,
    RoiConfig,
    TrackerConfig,
    TrackingSettings,
)
from flying_object_tracking.detection.detector import Detector, DetectResult, create_detector
from flying_object_tracking.errors import (
    DetectorUnavailableError,
    IntrinsicsUnavailableError,
    TrackingConfigError,
    TrackingError,
)
from flying_object_tracking.metrics import EffectiveWindow, TrackingCounters, TrackingMetrics
from flying_object_tracking.pipeline import CaughtExceptionCounters, TrackingPipeline, track_source
from flying_object_tracking.types import (
    HANDOFF_VERSION,
    Candidate,
    CandidateRejection,
    CameraPoint,
    CameraTrack,
    CoordinateFrame,
    DetectorKind,
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

__all__ = [
    # 座標系（types）
    "CoordinateFrame",
    # 受け渡し型（types）
    "Roi",
    "Candidate",
    "CandidateRejection",
    "CameraPoint",
    "PointFailure",
    "TrackPoint",
    "CameraTrack",
    "TrackUpdate",
    # 追跡状態・終了理由・入力元種別（CameraTrack のフィールド型。types）
    "TrackState",
    "TrackEndReason",
    "SourceKind",
    # 候補と失敗の理由（types）
    "RejectReason",
    "PointFailureReason",
    # 検出方式と生成口（types / detection.detector）
    "DetectorKind",
    "Detector",
    "create_detector",
    "DetectResult",
    # 設定（config）
    "TrackingSettings",
    "RoiConfig",
    "ObjectModelConfig",
    "DetectorConfig",
    "ProjectionConfig",
    "TrackerConfig",
    "MeasurementConfig",
    # パイプラインと実行関数（pipeline）
    "TrackingPipeline",
    "CaughtExceptionCounters",
    "track_source",
    # 計測（metrics）
    "TrackingMetrics",
    "TrackingCounters",
    "EffectiveWindow",
    # 比較結果（bench.compare / bench.overhead）
    "DetectorRunResult",
    "DetectorDecision",
    "DetectorComparisonResult",
    "compare_detectors",
    "OverheadResult",
    "OverheadVerdict",
    "OverheadReport",
    "OverheadBench",
    # 例外（errors）
    "TrackingError",
    "TrackingConfigError",
    "IntrinsicsUnavailableError",
    "DetectorUnavailableError",
    # 受け渡し形式の版番号（types）
    "HANDOFF_VERSION",
]
