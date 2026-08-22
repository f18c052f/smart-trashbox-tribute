"""検出 → 逆投影 → 追跡を結線する処理パイプライン（design.md「L6-L7: 追跡と統合 →
TrackingPipeline」、タスク 6.1）。

`TrackingPipeline` は1フレームを受け取り、

    (1) 検出（`Detector.detect()`）
    (2) 逆投影（候補ごとの `PointEstimator.estimate()`）
    (3) 追跡（`SingleObjectTracker.update()`）

を順に実行し、逐次結果（`TrackUpdate`）を返す（design.md「1フレームの処理」
シーケンス図）。検出区間・追跡区間の計測（`TrackingMetrics.detect()` /
`.track()` / `.record_update()`）を該当区間だけに掛け、上流が検出した
フレーム欠落（`CaptureFrame.gap_before`）を点へ引き継ぐ（要件 10.3）。

## 例外捕捉の範囲について（レビュー指摘を受けて (1)(2)(3) すべてに拡張）

タスク本文は「**1フレームの処理中**の予期しない例外で処理を止めない」と
書いており、design.md の Error Categories 表も「1フレームの処理中の予期
しない例外」という無限定の表現を使う。**「1フレームの処理」は (1) 検出
(2) 逆投影 (3) 追跡の3段すべてを指し、検出・逆投影段だけに限定されない。**
初版の実装は (1)(2) だけを `try/except` で囲み、(3) `SingleObjectTracker.
update()` を外側に置いていたため、追跡側の予期しない例外がそのまま
`process()` から伝播してしまう欠落があった（レビューで独立に再現・指摘
された）。現在の実装は (3) も同じ例外捕捉の対象に含める。

(3) の捕捉には (1)(2) と異なる配慮が要る: `SingleObjectTracker.update()`
はそれ自身が追跡の内部状態（`IDLE`/`TRACKING`/`ENDED` と蓄積した点列）を
持つ副作用のある呼び出しであり、失敗時に「もう一度同じ引数で呼び直せば
副作用なく再現できる」保証が無い。したがって `_advance_tracker()` は
次の3段構えで縮退する:

1. 実際の `points`（このフレームで3D化に成功した点）で `tracker.update()`
   を呼ぶ。
2. 例外を捕捉したら、種別と件数を1件だけ数え（後続の再試行が重ねて失敗
   しても二重に数えない — 同一フレームの同一障害の延長として扱う）、
   **候補ゼロでの再試行を1回だけ許容する**（`points` が空でない場合のみ。
   「そのフレームを候補ゼロとして扱う」という要求そのものであり、かつ
   `tracker` 自身の欠測処理ロジック — `missing_count` の増分・
   `max_missing_frames` 超過時の `LOST` 遷移 — を実際に機能させたいため。
   単に呼び出しをスキップすると、欠測カウントの整合性が壊れる）。
3. 再試行も失敗する場合（`tracker.update()` 自体が呼び出し方によらず
   壊れている、という最悪ケース）は、**これ以上 `tracker` を呼ばず**、
   直前に成功した `CameraTrack` スナップショット（`self._last_camera_track`）
   をそのまま使い回した「このフレームでは何も変わらなかった」
   `TrackUpdate` を合成して返す（`_fallback_track_update()`）。

捕捉した例外のログ（`event="error"`）は、追跡段の失敗であっても
`settings.measurement.detect_stage`（既定 `"detect"`）へ送出する。
design.md「Error Handling / Error Categories and Responses」が
「捕捉し、`stage="detect"`, `event="error"` として記録して」と1段だけを
名指ししているため、この段名をすべての捕捉例外（検出・逆投影・追跡の
いずれが原因でも）に対する共通のエラーチャンネルとして踏襲する（追跡専用
の新しいイベント種別を作らない、という既存の設計判断の一貫した延長）。

**本タスクは `TrackingPipeline` だけを実装する。** `track_source()`
（上流のフレーム供給との結線）はタスク 6.2 の責務であり、ここには実装しない
（design.md 同節に両方の署名が併記されているが、`_Depends:` が示す通り
本タスクの境界は `TrackingPipeline` に限る）。

依存方向（design.md「Dependency Direction」表の `pipeline` 行）:
0〜6層（`errors` / `types` / `config` / `metrics` / `detection` /
`projection` / `tracking`）＋ `sensing_foundation`（`CaptureFrame` / `Logger`
の公開入口）を import してよい。

## 未決事項1の解消: `gate_rejected` ではなく「除外理由の合算」について

タスク 3.1 の Implementation Notes（tasks.md）が指摘する通り、
`PointEstimator.estimate()` が返す `CandidateRejection` は `count=1` の
**単体**（1候補につき1回の寸法本判定に対応）であり、`Detector.detect()` が
返す `DetectResult.rejections` は同一フレーム内で理由ごとに**集約済み**の
件数である。両者は粒度が異なるため、本モジュールは `_merge_rejections()`
で理由（`RejectReason`）ごとに件数を合算してから `TrackUpdate.rejections`
（および `TrackingMetrics` への入力）として使う。単純にタプルを連結すると
同一理由が複数エントリに分裂し、「除外は理由ごとに件数を数える」
（要件 9.3）を壊す（タスク 3.1 Implementation Notes 参照）。

`SingleObjectTracker.update()` はそれ自身は検出・逆投影を知らないため
（`tracking.py` の設計上、`detection` / `projection` を import しない）、
常に `rejections=()` / `point_failures=()` の `TrackUpdate` を返す
（`tracking.py` 実装参照）。本モジュールは `dataclasses.replace()` で
これらのフィールドだけを実際の値（合算済み `rejections` と、収集した
`point_failures`）に差し替えてから呼び出し元へ返し、かつ
`TrackingMetrics.record_update()` にも渡す。`candidates` フィールドは
`SingleObjectTracker` が既に「3D化に成功した候補数」として正しく設定
しているため差し替えない（`metrics.py` モジュール docstring の既存の
合意された意味論をそのまま使う）。

## 未決事項2の解消: `mask_px` の橋渡し

タスク 5.1 の Implementation Notes（tasks.md）が指摘する通り、
`TrackingMetrics.record_update(update, frame)` は design.md のシグネチャ
どおりだが、`TrackUpdate` に `mask_px`（前景画素数）を運ぶフィールドが無い
ため `stage="detect"` の `"frame"` イベントから送出できなかった
（意図的な既知の欠落として `metrics.py` に明記されていた）。

本タスクはこれを、`TrackingMetrics.record_update()` へ**最小の追加
キーワード専用引数** `mask_px: int = 0` を足すことで解消する
（`metrics.py` の他のロジックには一切触れない。default 値付きのため
既存の呼び出し（2引数）はそのまま動作し続ける後方互換な変更である）。
`TrackingPipeline` は `DetectResult.mask_px`（検出に成功したフレームでは
実測値、検出段で例外を捕捉したフレームでは 0）をこの引数へ渡す。

## 捕捉した例外の数え方について

design.md「TrackingPipeline / Risks」は「捕捉した例外は必ず種別と件数を
数え、`counters()` と `session_end` 相当の要約に出す」と書く一方、
design.md 自身の `TrackingPipeline` 契約コードブロックは
`counters() -> TrackingCounters` という、`TrackingMetrics.counters()` と
**同じ戻り値型**を掲げている。しかし `TrackingCounters`
（`metrics.py`、タスク 5.1 で既にレビュー・承認済み）は捕捉例外用の
フィールドを持たず、`metrics.py` は今回 `mask_px` 以外の変更を許されて
いない（タスク境界）。

したがって本モジュールは design.md の型注釈を字面どおりには実装しない
選択を取る（`projection.py` の `SIZE_MISMATCH` 設計判断と同種の、記録した
上での逸脱）: `TrackingPipeline.counters()` は `TrackingMetrics.counters()`
をそのまま委譲して返す（design.md の型 `TrackingCounters` は正確に保つ）。
捕捉例外の種別・件数は、`TrackingPipeline` 自身が持つ**別の**小さな
カウンタ（`CaughtExceptionCounters`）として `caught_exceptions()` という
新しいメソッドから取り出せるようにする。「`counters()` と…要約に出す」を
文字通り同一メソッドに縛られた指示とは解釈せず、「捕捉件数がカウンタ
から取り出せること」という観測可能な完了状態（タスク本文）を、
`TrackingPipeline` が持つ2つの取り出し口（`counters()` /
`caught_exceptions()`）のどちらからでも辿れる形で満たす、という判断で
ある。加えて、捕捉した例外は `stage=<detect_stage>`, `event="error"` として
必ず1件ずつ構造化ログへ記録する（design.md「Error Handling / Monitoring」:
「すべての異常事象は上流の構造化ログへ `event="error"` として残す」）。
このログ送出は `TrackingPipeline.__init__` が直接受け取る `logger` 引数
（`TrackingMetrics` に渡すのとは別の、パイプライン自身が保持する参照）を
使う。`TrackingMetrics` の送出契約（`stage="detect"/"track"`, `event="frame"`
専用）を拡張せずに済ませるための選択であり、計測の有効・無効
（`settings.measurement.enabled`）とは独立して常に送出する
（例外の記録は「計測値」ではなく異常事象の記録であるため。要件 8.7 が
禁じるのは「計測が無効な間の計測値の生成」であり、バグの兆候である例外の
記録はその対象ではないと判断した）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType

from sensing_foundation import CaptureFrame, Logger

from flying_object_tracking.config import TrackingSettings
from flying_object_tracking.detection.detector import Detector, create_detector
from flying_object_tracking.errors import (
    DetectorUnavailableError,
    IntrinsicsUnavailableError,
    TrackingConfigError,
)
from flying_object_tracking.metrics import TrackingCounters, TrackingMetrics
from flying_object_tracking.projection import PointEstimator
from flying_object_tracking.tracking import SingleObjectTracker
from flying_object_tracking.types import (
    HANDOFF_VERSION,
    CameraPoint,
    CameraTrack,
    CandidateRejection,
    CoordinateFrame,
    PointFailure,
    RejectReason,
    Roi,
    SourceKind,
    TrackEndReason,
    TrackState,
    TrackUpdate,
)

__all__ = ["TrackingPipeline", "CaughtExceptionCounters"]


# 例外が起きた場合でも構造化ログには「呼び出し方・設定の誤り」区分と
# 「前提の欠落」区分の例外を捕捉させない（errors.py の3区分表。値で返る
# 「正常系の無効」との対比で、これらは常に呼び出し側へ伝播させる）。
_NON_CATCHABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    TrackingConfigError,
    IntrinsicsUnavailableError,
    DetectorUnavailableError,
)


@dataclass(frozen=True, slots=True)
class CaughtExceptionCounters:
    """`TrackingPipeline` が1フレームの処理中に捕捉した予期しない例外の集計。

    `TrackingMetrics.counters()`（`TrackingCounters`）とは別の、
    `TrackingPipeline` 自身が持つカウンタである（モジュール docstring
    「捕捉した例外の数え方について」参照）。

    Attributes:
        total: 捕捉した例外の総数。
        by_type: 例外の型名（`type(exc).__name__`）ごとの件数。
    """

    total: int
    by_type: Mapping[str, int]


class TrackingPipeline:
    """検出 → 逆投影 → 追跡を結線し、区間計測とフレーム欠落の引き継ぎを行う。

    design.md「TrackingPipeline」。要件 1.1, 8.1, 10.2, 10.3。

    Preconditions:
        `process()` に渡す `frame` は同一セッション内で `profile`
        （寸法・`depth_scale_mm`）と `source`（入力元種別）が一貫している
        こと（`Detector` の ROI 解決・`SingleObjectTracker` の構築は最初の
        `process()` 呼び出しで1度だけ行い、以後のフレームで使い回す）。

    Postconditions:
        `process()` は例外を伝播しない。ただし `TrackingConfigError` /
        `IntrinsicsUnavailableError` / `DetectorUnavailableError`
        （「呼び出し方・設定の誤り」「前提の欠落」区分）はそのまま伝播する
        （要件 10.2 の対象外。`errors.py` の3区分表参照）。
    """

    def __init__(self, settings: TrackingSettings, logger: Logger) -> None:
        self._settings = settings
        self._logger = logger

        self._detector: Detector = create_detector(settings)

        # PointEstimator は roi.z_min_mm / roi.z_max_mm だけを使う
        # （`projection.py` の `__init__` docstring: 「画素矩形部分（x_px
        # 等）は estimate() が受け取る Candidate.bbox_px が既に全画面座標
        # であるため使わない」）。フレーム依存の画素矩形解決（Detector が
        # 最初の detect() で行う ROI 検証と同種の処理）を本モジュールで
        # 重複させる必要が無いため、画素矩形フィールドには未使用の
        # プレースホルダを入れる。
        projection_roi = Roi(
            x_px=0,
            y_px=0,
            width_px=1,
            height_px=1,
            z_min_mm=settings.roi.z_min_mm,
            z_max_mm=settings.roi.z_max_mm,
        )
        self._point_estimator = PointEstimator(
            settings.object_model, settings.projection, projection_roi
        )

        self._metrics = TrackingMetrics(logger, settings.measurement)

        # SingleObjectTracker(config, source, detector_kind) は構築時に
        # frame.source（入力元種別）を要求するが、__init__ の時点ではまだ
        # フレームを受け取っていない。Detector が最初の detect() まで
        # ROI 解決を遅延させるのと同じ理由で、最初の process() 呼び出し
        # まで構築を遅延する。
        self._tracker: SingleObjectTracker | None = None

        # 追跡段で tracker.update() 自体が壊れている最悪ケースの最終防御
        # （`_fallback_track_update()`）に使う、直前に成功した CameraTrack
        # のスナップショット。モジュール docstring「例外捕捉の範囲について」
        # 参照。
        self._last_camera_track: CameraTrack | None = None

        self._caught_exception_total = 0
        self._caught_exception_by_type: dict[str, int] = {}

    def process(self, frame: CaptureFrame) -> TrackUpdate:
        """1フレームを検出 → 逆投影 → 追跡の順に処理し、逐次結果を返す。

        検出（`detector.detect()`）・逆投影（候補ごとの
        `point_estimator.estimate()`）・追跡（`tracker.update()`、
        `_advance_tracker()` に委譲）のいずれで予期しない例外が起きても
        捕捉し、そのフレームを候補ゼロとして扱う（要件 10.2。「1フレームの
        処理中」の範囲は3段すべてに及ぶ。モジュール docstring「例外捕捉の
        範囲について」参照）。計測区間（`metrics.detect()`）は design.md の
        「1フレームの処理」シーケンス図どおり `detector.detect()` の
        呼び出しだけを囲み、逆投影ループは計測区間の外側で行う（区間の
        意味を「検出そのものの所要時間」に保つため）。
        """
        if self._tracker is None:
            self._tracker = SingleObjectTracker(
                self._settings.tracker, frame.source, str(self._detector.kind)
            )

        detect_result = None
        points: list[CameraPoint] = []
        point_failures: list[PointFailure] = []
        singleton_rejections: list[CandidateRejection] = []

        try:
            with self._metrics.detect(frame.index):
                detect_result = self._detector.detect(frame)

            for candidate in detect_result.candidates:
                outcome = self._point_estimator.estimate(frame, candidate)
                if isinstance(outcome, CameraPoint):
                    points.append(outcome)
                elif isinstance(outcome, PointFailure):
                    point_failures.append(outcome)
                else:
                    singleton_rejections.append(outcome)
        except _NON_CATCHABLE_EXCEPTIONS:
            # 設定不正・内部パラメータ欠落・検出手段の前提欠落は「呼び出し
            # 方・設定の誤り」「前提の欠落」区分であり、値で返らず伝播する
            # （errors.py の3区分表。要件 10.2 の対象外）。
            raise
        except Exception as exc:  # noqa: BLE001 - 意図的な広い捕捉（要件 10.2）
            # 予期しない例外はここで打ち止めにし、このフレームを候補ゼロ
            # として扱う（部分的な検出結果は破棄する。「そのフレームを
            # 候補ゼロとして扱う」という要求そのもの）。
            self._record_caught_exception(exc, frame)
            detect_result = None
            points = []
            point_failures = []
            singleton_rejections = []

        mask_px = detect_result.mask_px if detect_result is not None else 0
        aggregated_rejections = detect_result.rejections if detect_result is not None else ()
        merged_rejections = _merge_rejections(aggregated_rejections, singleton_rejections)

        raw_update = self._advance_tracker(points, frame)

        # SingleObjectTracker は検出・逆投影を知らないため rejections=() /
        # point_failures=() を返す（`tracking.py` の設計）。本メソッドが
        # 実際の（合算済みの）値へ差し替える（モジュール docstring
        # 「未決事項1の解消」参照）。`candidates` は差し替えない
        # （`SingleObjectTracker` が既に正しく設定済み。同上）。
        update = replace(
            raw_update, rejections=merged_rejections, point_failures=tuple(point_failures)
        )

        self._metrics.record_update(update, frame, mask_px=mask_px)
        self._last_camera_track = update.track

        return update

    def _advance_tracker(self, points: list[CameraPoint], frame: CaptureFrame) -> TrackUpdate:
        """追跡段（`tracker.update()`）を実行する。予期しない例外が起きても
        捕捉し、このフレームを候補ゼロとして扱う（モジュール docstring
        「例外捕捉の範囲について」の3段構えをそのまま実装する）。
        """
        assert self._tracker is not None

        try:
            with self._metrics.track(frame.index):
                return self._tracker.update(
                    points,
                    frame_index=frame.index,
                    frame_seq=frame.seq,
                    gap_before=frame.gap_before,
                )
        except _NON_CATCHABLE_EXCEPTIONS:
            raise
        except Exception as exc:  # noqa: BLE001 - 意図的な広い捕捉（要件 10.2）
            self._record_caught_exception(exc, frame)

            if points:
                # 実際の候補データ（points）が原因で失敗した可能性を考慮し、
                # 候補ゼロでの再試行を1度だけ許容する。tracker 自身の欠測
                # 処理ロジック（missing_count の増分・LOST 遷移）を実際に
                # 機能させるため（モジュール docstring 参照）。この再試行の
                # 失敗は別個の例外として数え直さない（同一フレームの同一
                # 障害の延長として扱う）。
                try:
                    with self._metrics.track(frame.index):
                        return self._tracker.update(
                            [],
                            frame_index=frame.index,
                            frame_seq=frame.seq,
                            gap_before=frame.gap_before,
                        )
                except _NON_CATCHABLE_EXCEPTIONS:
                    raise
                except Exception:  # noqa: BLE001 - 最終防御。二重に数えない
                    pass

            # tracker.update() が候補ゼロでも例外を送出する場合（呼び出し
            # 自体が壊れている想定）は、これ以上 tracker を呼ばず、直前に
            # 成功した CameraTrack スナップショットを使い回す。
            return self._fallback_track_update(frame)

    def _fallback_track_update(self, frame: CaptureFrame) -> TrackUpdate:
        """`tracker.update()` が候補ゼロの再試行でも例外を送出する場合の最終防御。

        `tracker` の内部状態をこれ以上変更せず（呼び出し自体が壊れている
        可能性があるため）、直前に成功した `CameraTrack` スナップショット
        （`self._last_camera_track`）を使い回した「このフレームでは何も
        変わらなかった」`TrackUpdate` を合成して返す。まだ1度も成功して
        いない場合（最初の `process()` 呼び出しから `tracker.update()` が
        壊れている場合）は `IDLE` 状態の空の `CameraTrack` を組み立てる。
        """
        track = self._last_camera_track
        if track is None:
            track = CameraTrack(
                handoff_version=HANDOFF_VERSION,
                frame=CoordinateFrame.CAMERA,
                track_id=0,
                started_t_ms=0.0,
                points=(),
                state=TrackState.IDLE,
                end_reason=None,
                source=frame.source,
                detector_kind=str(self._detector.kind),
            )
        return TrackUpdate(track=track, appended=None, candidates=0, rejections=(), point_failures=())

    def finish(self) -> CameraTrack:
        """入力が尽きたときに最終的な点列を確定する。

        `SingleObjectTracker.finish(TrackEndReason.SOURCE_END)` を呼ぶ
        （design.md「Postconditions: 入力が尽きたら finish(SOURCE_END)
        相当の最終 CameraTrack を得られる」）。`RESET` / `LOST` /
        `MAX_POINTS` は追跡自身の内部ロジックまたは明示的な打ち切り呼び出し
        で決まる終了理由であり、`TrackingPipeline.finish()`（＝「入力が
        尽きた」という状況そのもの）が意味しうる終了理由は `SOURCE_END`
        だけである。

        `process()` を1度も呼ばずに `finish()` を呼んだ場合（1フレームも
        処理していない）は、`SingleObjectTracker` をまだ構築していないため
        `SourceKind.SIMULATED` を既定として遅延構築する（実機なしでの
        開発・検証が既定である本 Spec 全体の方針に合わせた、実害の無い
        フォールバック。1点も追加されていない `IDLE` の追跡がそのまま
        `ENDED(SOURCE_END)` になるだけであり、`source` の値は出力される
        `CameraTrack.source` にのみ反映される）。
        """
        if self._tracker is None:
            self._tracker = SingleObjectTracker(
                self._settings.tracker, SourceKind.SIMULATED, str(self._detector.kind)
            )
        return self._tracker.finish(TrackEndReason.SOURCE_END)

    def counters(self) -> TrackingCounters:
        """`TrackingMetrics.counters()` をそのまま委譲する（design.md 記載の型）。

        捕捉した例外の種別・件数は `caught_exceptions()` から別途取り出す
        （モジュール docstring「捕捉した例外の数え方について」参照）。
        """
        return self._metrics.counters()

    def caught_exceptions(self) -> CaughtExceptionCounters:
        """1フレームの処理中に捕捉した予期しない例外の種別・件数を返す。

        `TrackingMetrics.counters()`（`TrackingCounters`）とは独立した、
        `TrackingPipeline` 自身の集計である。
        """
        return CaughtExceptionCounters(
            total=self._caught_exception_total,
            by_type=MappingProxyType(dict(self._caught_exception_by_type)),
        )

    def _record_caught_exception(self, exc: Exception, frame: CaptureFrame) -> None:
        """捕捉した例外の種別・件数を数え、`event="error"` として記録する。"""
        type_name = type(exc).__name__
        self._caught_exception_total += 1
        self._caught_exception_by_type[type_name] = (
            self._caught_exception_by_type.get(type_name, 0) + 1
        )
        # 計測の有効・無効（settings.measurement.enabled）とは独立に、
        # 常に記録する（モジュール docstring「捕捉した例外の数え方に
        # ついて」参照。例外の記録は「計測値」ではなく異常事象の記録で
        # あるため）。
        if self._logger.enabled:
            self._logger.emit(
                self._settings.measurement.detect_stage,
                "error",
                frame_index=frame.index,
                exception_type=type_name,
                message=str(exc),
            )


def _merge_rejections(
    aggregated: tuple[CandidateRejection, ...],
    singles: list[CandidateRejection],
) -> tuple[CandidateRejection, ...]:
    """理由（`RejectReason`）ごとに件数を合算する（モジュール docstring
    「未決事項1の解消」参照。要件 9.3「除外は理由ごとに件数を数える」）。

    `aggregated`（`DetectResult.rejections`。既に理由ごとに集約済み）と
    `singles`（`PointEstimator.estimate()` が返した `count=1` の単体を
    候補ごとに集めたもの）を、同一 `reason` なら件数を足し合わせて1つの
    `CandidateRejection` にまとめる。単純な連結（`aggregated + tuple(
    singles)`）は同一理由が複数エントリに分裂しうるため行わない。

    Returns:
        `reason` ごとに1エントリだけを持つタプル。順序は `aggregated` の
        出現順を先頭に保ち、`aggregated` に無かった理由は `singles` に
        現れた順で末尾に追加する（決定的な順序）。
    """
    counts: dict[RejectReason, int] = {}
    order: list[RejectReason] = []
    for rejection in aggregated:
        if rejection.reason not in counts:
            order.append(rejection.reason)
        counts[rejection.reason] = counts.get(rejection.reason, 0) + rejection.count
    for rejection in singles:
        if rejection.reason not in counts:
            order.append(rejection.reason)
        counts[rejection.reason] = counts.get(rejection.reason, 0) + rejection.count
    return tuple(CandidateRejection(reason=reason, count=counts[reason]) for reason in order)
