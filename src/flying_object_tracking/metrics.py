"""検出区間・追跡区間の計測（design.md「L3: 計測と画像処理の基礎 → TrackingMetrics」、
タスク 5.1）。

上流 `sensing_foundation` の構造化ロギング基盤（`Logger` / `NullLogger` /
`StructuredLogger`）にそのまま乗り、**独自のログ形式を定義しない**（要件 8.2）。
本モジュールが足すのは stage 名 `detect` / `track` の2つであり、上流の予約
stage（`system` / `capture` / `record`）とは衝突しない（要件 8.1）。

依存方向（design.md「Dependency Direction」表の `metrics` 行）:
`errors` / `types` / `config`（0〜2層）＋ `sensing_foundation`（`Logger` の
公開入口）だけを import してよい。`detection` / `projection` / `tracking`
を import しない。

計測の実行時無効化（要件 8.6, 8.7）
------------------------------------
`MeasurementConfig.enabled` に基づき、**`TrackingMetrics` 自身が**使用する
ロガーを決定する。`config.enabled is False` のときは、コンストラクタに渡さ
れた `logger` 引数が何であっても内部で `NullLogger()` に差し替える
（design.md「TrackingMetrics / Implementation Notes / Integration」:
「計測が無効なとき、`TrackingMetrics` は上流の `NullLogger` に委譲し、
イベントの生成も文字列化も行わない」）。これにより、呼び出し側が誤って
有効な `StructuredLogger` を渡しつつ `config.enabled=False` にしても、
計測が「半分だけ有効」になる事故を防ぐ。

無効時は `detect()` / `track()` のタイマー処理そのものをスキップする
（`time.perf_counter()` すら呼ばない）。`record_update()` はカウンタの
更新（軽量な整数演算であり「計測値の生成」ではない）は常に行うが、
イベント送出に関わるデータ組み立て（`dict` 内包表記・`emit()` 呼び出し）は
`self._logger.enabled` が真のときだけ行う。

集計を行わない（要件 8.9）
----------------------------
`counters()` は生の累積カウンタのスナップショットを返すだけであり、
代表値・分位点などの統計処理は一切行わない。集計は CLI・
`LogSummarizer`（上流）に委ねる。

`record_update` の割り当てコスト（Risks）
--------------------------------------------
`record_update` は毎フレーム呼ばれるため、ここでの割り当てが支配的になり
うる。カウンタは可変の `int` 属性として持ち、`TrackingCounters`（frozen
dataclass）の生成は `counters()` が呼ばれた時だけ行う。

`gate_rejected` の正しい累積方法（task 4.1 のレビューで実証済みの教訓）
--------------------------------------------------------------------------
`sum(p.rivals for p in track.points)` という事後合計では、ゲート内で敗れた
候補しか数えられず、全候補がゲート外だった（`appended is None`）フレームの
候補が一切拾えないため過小評価になる（`tracking.py` モジュール docstring
「設計判断4」参照）。正しい算出は、`TRACKING` 状態で対応付けを試みた
`update()` 呼び出し**ごと**に次を累積することによる：

- `appended is not None` の場合: `candidates - 1`
- `appended is None` の場合: `candidates`

`IDLE` 状態のまま（まだ追跡が開始していない）呼び出しは対象外とする。
本モジュールは呼び出し直前の `TrackState`（`self._prior_track_state`）を
保持し、その呼び出しが「呼び出し時点で `TRACKING` だったか」を判定する。

detect / track 区間の "frame" イベントの組み立て方（本タスクの境界。
レビュー指摘を受けて改訂）
--------------------------------------------------------------------------
`detect()` / `track()` の公開シグネチャは `frame_index: int` のみを受け取る
（design.md 記載どおり）ため、区間の所要時間（`detect_ms` / `track_ms`）を
知っているのは `detect()` / `track()` 自身だけであり、候補数・除外内訳・
追跡の付随情報（`candidates` / `rejected` / `appended` / `points` /
`rivals` / `gate_rejected`）を知っているのは `record_update()` だけである
（`TrackUpdate` から得られる）。

**初版では `detect()` / `track()` がそれぞれ単独で `frame_index` +
`<stage>_ms` だけの部分イベントを即座に送出し、`record_update()` が別途、
同じ `(stage, event)` に対してドメインデータだけの部分イベントを送出して
いた。** これは1フレームの処理につき同じ `(stage, event)` の NDJSON 行が
2行生まれることを意味し、`sensing_foundation.logsummary.summarize_log()`
で集計すると `EventSummary.count` が実際のフレーム数の2倍になり、各
フィールドの `missing` が本来ゼロであるべきところに現れる（本来存在した
値が「たまたま別の行に書かれていた」ために欠測として数えられてしまう）。
レビューでこの実害を `summarize_log()` に対して直接再現され、指摘された。

**現在の実装はこれを解消し、区間ごとに1フレームにつき1行だけを送出する。**
`detect()` / `track()` はもはや何も送出しない。計測した所要時間を
`self._pending_detect_ms` / `self._pending_track_ms`（次の `record_update()`
呼び出しまでの一時的な内部状態）へ保持するだけの純粋なタイマーになった。
`record_update()` が呼ばれた時点で、保持しておいた所要時間とその呼び出しの
`TrackUpdate` / `CaptureFrame` から得られる全データを1つの `dict` にまとめ、
`stage="detect"`, `event="frame"` と `stage="track"`, `event="frame"` を
それぞれちょうど1回ずつ送出する。送出後、保持していた所要時間は次のフレーム
に持ち越さないよう `None` にリセットする。

**既知の欠落（CONCERNS 参照。上記の二重送出問題とは別種のギャップ）**:
`mask_px`（前景画素数）は `TrackUpdate` に対応するフィールドが無く、本
タスクの境界内では取得手段が無い。したがって `record_update()` が送出する
`stage="detect"` の `"frame"` イベントには `mask_px` を含めない（存在しない
値を捏造しない）。これは design.md の Event Contract を完全には満たさない
既知のギャップであり、`TrackingPipeline`（タスク 6.1）が `DetectResult.
mask_px` を計測へ橋渡しする手段（`TrackUpdate` の拡張、または別経路）を
用意する必要がある。レビューでもこちらは「シグネチャ変更なしには解決でき
ない、本タスクの境界外」として non-blocking 扱いが確認されている。
"""

from __future__ import annotations

import time
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass

from sensing_foundation import CaptureFrame, Logger, NullLogger

from flying_object_tracking.config import MeasurementConfig
from flying_object_tracking.types import TrackState, TrackUpdate

__all__ = ["TrackingCounters", "TrackingMetrics", "EffectiveWindow"]


@dataclass(frozen=True, slots=True)
class TrackingCounters:
    """検出・追跡の累積カウンタのスナップショット（design.md「TrackingMetrics」）。

    `TrackingMetrics.counters()` が要求時にのみ生成する不変値。生成後に
    `TrackingMetrics` 側の内部状態が変化しても、既に取得したスナップショット
    は変化しない（frozen dataclass の値意味論）。

    Attributes:
        frames_processed: `record_update()` が呼ばれた回数（処理したフレーム数）。
        frames_without_candidate: 取りこぼし。候補（`TrackUpdate.candidates`）が
            ゼロだったフレーム数（要件 8.5 前半）。
        candidates_total: `record_update()` を通過した候補数の累計。
        candidates_rejected: 検出段の除外件数の累計
            （`TrackUpdate.rejections` の `count` 合計。要件 9.3）。
        points_estimated: 3D位置化に成功した候補数の累計。
        point_failures: 3D化の失敗数の累計（`TrackUpdate.point_failures` の
            件数合計。要件 8.5 後半。`frames_without_candidate` とは独立に
            数える）。
        points_appended: 追跡中の点列へ実際に追加された点数の累計。
        gate_rejected: ゲートで落ちた候補数の累計（要件 6.6）。`TRACKING`
            状態で対応付けを試みた呼び出しについてのみ、`candidates - 1`
            （採用時）または `candidates`（不採用時）を累積した値。
        tracks_started: `IDLE -> TRACKING` の遷移が起きた回数。
        tracks_ended: いずれかの終了理由で `-> ENDED` に遷移した回数。
    """

    frames_processed: int
    frames_without_candidate: int
    candidates_total: int
    candidates_rejected: int
    points_estimated: int
    point_failures: int
    points_appended: int
    gate_rejected: int
    tracks_started: int
    tracks_ended: int


class EffectiveWindow:
    """一定時間窓内に得られた有効な3D位置サンプル数を算出する（タスク 5.2）。

    design.md「L3: 計測と画像処理の基礎 → EffectiveWindow」。要件 4.5, 8.4。

    比較の第一指標（`effective_points_per_window`。要件 4.5）と、`peak()`
    が返す最密窓の値（`effective_points_peak`）を提供する。`add(t_ms)` で
    有効な3D位置サンプルの時刻を1件ずつ記録し、`value()` / `peak()` は
    それまでに記録された全サンプルから算出する（副作用を持つ集計オブジェクト
    であり、`add()` を呼ぶたびに以後の `value()` / `peak()` の結果が変わる）。

    名前を上流と区別する（Integration 参照。要件 4.5 の Revalidation
    Trigger）
    ------------------------------------------------------------------
    `sensing_foundation.bench.modes` の `effective_samples_per_window()`
    （自由関数）／ `ModeResult.effective_samples_per_window`（フィールド）は
    **フレーム層**の実効サンプル数であり、`capture` 区間で得られたフレーム
    数を数える。本クラスの `value()` が算出する `effective_points_per_window`
    は**点層**の実効点数であり、検出・逆投影を通過して3D位置になった点の数を
    数える（`sensing_foundation/bench/modes.py` モジュール docstring
    「指標名の対応関係」: 「両者は層が異なるが対になる指標として比較・報告
    される想定であり、片方だけ定義を変えると比較が成立しなくなる」）。
    取り違えると `DetectorComparison`（要件 4.5 の判定規則）が誤った入力で
    走ることになるため、本クラスはクラス名・メソッド名の両方で上流の語彙
    （`effective_samples_per_window`）と衝突しない語彙
    （`EffectiveWindow` / `add()` / `value()` / `peak()`）を使う。**上流の
    関数・フィールドを再エクスポートやエイリアスもしない。**

    窓長は固定値として埋め込まない（要件 8.4）
    ------------------------------------------------------------------
    `window_ms` は**コンストラクタ引数として必ず受け取る**
    （`MeasurementConfig.window_ms` から渡される想定。既定 600.0 は初期
    評価候補であり、本クラス自身は既定値を持たない）。内部で `window_ms`
    の固定値リテラルを使わない。

    アルゴリズム（本クラス固有の定義。上流とアルゴリズムを一致させる要求は
    design.md のどこにも無い——「対になる指標」であって「同一アルゴリズム」
    ではない）
    ------------------------------------------------------------------
    `value()`（窓あたりの平均点数）:
        記録済み全タイムスタンプの観測区間長 `span_ms = max(t) - min(t)`
        を `window_ms` で割った値を「窓の個数」とみなし（`span_ms <
        window_ms` の疎な場合は1個とみなす。0除算を避けると同時に、
        「窓0.3個」のような単位として無意味な値を作らないため）、記録済み
        総数をその窓数で割った連続値（float）を返す。サンプルが1つも無け
        れば `0.0`。

    `peak()`（最も密な窓での点数）:
        開始位置を任意の観測時刻に固定できる、幅 `window_ms` の半開区間
        `[t, t+window_ms)` のうち、含まれるタイムスタンプ数が最大のものを
        返す（**真のスライディングウィンドウ**）。原点整列した固定ビンでは
        なく真のスライディングを採用したのは、バーストがビン境界をまたぐと
        固定ビンでは過小評価するため（具体例: タイムスタンプが `window_ms`
        の倍数付近をまたいで密集する場合。`tests/flying_object_tracking/
        test_flying_object_tracking_effective_window.py`
        `TestSlidingWindowVsFixedBinning` が、固定ビンなら 3、真のスライ
        ディングなら 4 になる具体的な時刻列でこの差を回帰テストとして
        固定している）。ソート済みタイムスタンプに対する二本指針
        （尺取り法）で `O(n log n + n)` で求める（分析時にのみ呼ばれ、
        毎フレームのホットパスではないため、この計算量で十分。design.md
        「Contracts」: Service）。サンプルが1つも無ければ `0`。
    """

    def __init__(self, window_ms: float) -> None:
        self._window_ms = window_ms
        self._timestamps_ms: list[float] = []

    def add(self, t_ms: float) -> None:
        """有効な3D位置サンプルの時刻を1件記録する。"""
        self._timestamps_ms.append(t_ms)

    def value(self) -> float:
        """窓あたりの平均点数（`effective_points_per_window`。要件 4.5）。"""
        if not self._timestamps_ms:
            return 0.0
        span_ms = max(self._timestamps_ms) - min(self._timestamps_ms)
        windows = max(1.0, span_ms / self._window_ms)
        return len(self._timestamps_ms) / windows

    def peak(self) -> int:
        """最も密な窓での点数（`effective_points_peak`）。"""
        if not self._timestamps_ms:
            return 0
        sorted_ms = sorted(self._timestamps_ms)
        count = len(sorted_ms)
        best = 0
        right = 0
        for left in range(count):
            if right < left:
                right = left
            while right < count and sorted_ms[right] < sorted_ms[left] + self._window_ms:
                right += 1
            best = max(best, right - left)
        return best


class TrackingMetrics:
    """detect / track 区間の計測とカウンタを1箇所に集め、上流のロガーへ送る。

    design.md「TrackingMetrics」。要件 8.1, 8.2, 8.3, 8.5, 8.6, 8.7, 8.9, 9.3。

    `window()`（`EffectiveWindow` を返す）は**タスク 5.2 の境界外**であり、
    本クラスには実装しない。タスク 5.2 の `_Boundary:` は `EffectiveWindow`
    （同名クラス単体）に限定されており、`TrackingMetrics`（タスク 5.1で
    既に承認済みの境界）ではない。タスク 6.1（`TrackingPipeline`）の
    `_Depends:` もタスク 5.1 のみを挙げてタスク 5.2 を含めていないことが、
    `window()` の配線をこの時点で行う必然性が無いことを裏付けている。
    `window()` を必要とする側（`DetectorComparison` 等、タスク 7.1 が対象）
    が実装される際に、あらためてこのファイルへ追記する必要がある
    （モジュール docstring・タスク 5.2 の CONCERNS を参照）。
    """

    def __init__(self, logger: Logger, config: MeasurementConfig) -> None:
        # 計測の実行時無効化（要件 8.6, 8.7）: config.enabled が False なら、
        # 渡された logger の実体に関わらず NullLogger に差し替える。
        self._logger: Logger = logger if config.enabled else NullLogger()
        self._detect_stage = config.detect_stage
        self._track_stage = config.track_stage

        # カウンタは可変の内部状態として持つ（Risks: record_update は毎フレーム
        # 呼ばれるため、ここでの割り当てを避ける）。
        self._frames_processed = 0
        self._frames_without_candidate = 0
        self._candidates_total = 0
        self._candidates_rejected = 0
        self._points_estimated = 0
        self._point_failures = 0
        self._points_appended = 0
        self._gate_rejected = 0
        self._tracks_started = 0
        self._tracks_ended = 0

        # gate_rejected の累積（要件 6.6）に、直前呼び出し時点の TrackState を
        # 使う（tracking.py 設計判断4）。SingleObjectTracker の初期状態と同じ
        # IDLE から始める。
        self._prior_track_state: TrackState = TrackState.IDLE

        # detect() / track() が測った所要時間を、次の record_update() 呼び出し
        # まで一時的に保持する（モジュール docstring「detect / track 区間の
        # "frame" イベントの組み立て方」参照）。record_update() が消費したら
        # None に戻す。
        self._pending_detect_ms: float | None = None
        self._pending_track_ms: float | None = None

    def detect(self, frame_index: int) -> AbstractContextManager[None]:
        """検出区間の所要時間を計測する（要件 8.1）。

        区間終了時にイベントを送出せず、所要時間（`detect_ms`）を内部に
        保持するだけの純粋なタイマーである。実際の送出は、この所要時間と
        `candidates` / `rejected` を1つにまとめた `record_update()` が行う
        （1フレームにつき `stage="detect"`, `event="frame"` を正確に1回
        だけ送出するため）。無効時（`NullLogger`）は計時処理自体を行わず、
        真の no-op になる（要件 8.7）。`frame_index` は将来の整合性検証の
        余地として受け取るが、現在の実装は使用しない
        （実際の `frame_index` は `record_update()` に渡された
        `CaptureFrame.index` を使う）。
        """
        return self._buffer_elapsed_ms(is_detect=True)

    def track(self, frame_index: int) -> AbstractContextManager[None]:
        """追跡区間の所要時間を計測する（要件 8.1）。

        `detect()` と同様、区間終了時にイベントを送出せず、所要時間
        （`track_ms`）を内部に保持するだけの純粋なタイマーである。実際の
        送出は `record_update()` が行う。無効時（`NullLogger`）は計時処理
        自体を行わず、真の no-op になる（要件 8.7）。
        """
        return self._buffer_elapsed_ms(is_detect=False)

    @contextmanager
    def _buffer_elapsed_ms(self, *, is_detect: bool):
        """`detect()` / `track()` に共通のタイマー実装（送出はしない）。

        無効時は `time.perf_counter()` すら呼ばない（要件 8.7: 計測値の
        生成そのものを行わない）。
        """
        if not self._logger.enabled:
            yield
            return
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            if is_detect:
                self._pending_detect_ms = elapsed_ms
            else:
                self._pending_track_ms = elapsed_ms

    def record_update(self, update: TrackUpdate, frame: CaptureFrame) -> None:
        """1フレーム分の `TrackUpdate` からカウンタを更新し、付随イベントを送出する。

        カウンタの更新は計測の有効・無効に関わらず常に行う（軽量な整数演算
        であり「計測値の生成」ではない）。イベント送出（`dict` の組み立てと
        `emit()` 呼び出し）は `self._logger.enabled` が真のときだけ行う
        （要件 8.7）。

        Args:
            update: そのフレームの追跡結果（`TrackUpdate.candidates` は
                `SingleObjectTracker.update()` に渡された、3D化に成功した
                候補数と同義。`gate_rejected` の算出はこの値を使う）。
            frame: 元となったフレーム。`frame.index` をイベントの
                `frame_index` として使う（要件 8.3: フレームの識別情報との
                突き合わせ）。
        """
        self._frames_processed += 1
        if update.candidates == 0:
            self._frames_without_candidate += 1
        self._candidates_total += update.candidates
        self._candidates_rejected += sum(r.count for r in update.rejections)
        self._points_estimated += update.candidates
        self._point_failures += len(update.point_failures)

        appended = update.appended is not None
        if appended:
            self._points_appended += 1

        # gate_rejected（要件 6.6）: TRACKING 状態で対応付けを試みた呼び出し
        # についてのみ累積する（tracking.py 設計判断4）。
        call_gate_rejected = 0
        if self._prior_track_state is TrackState.TRACKING:
            call_gate_rejected = (update.candidates - 1) if appended else update.candidates
            self._gate_rejected += call_gate_rejected

        new_state = update.track.state
        if self._prior_track_state is TrackState.IDLE and new_state is TrackState.TRACKING:
            self._tracks_started += 1
        track_ended_now = (
            self._prior_track_state is not TrackState.ENDED and new_state is TrackState.ENDED
        )
        if track_ended_now:
            self._tracks_ended += 1

        if self._logger.enabled:
            # stage="detect", event="frame": このフレームにつきちょうど1回
            # だけ送出する。detect() が測っていれば detect_ms を含める
            # （測っていなければ、実際に計測していないので省略する ——
            # これは「本当の欠測」であり、レビューが指摘した「二重送出に
            # よる見せかけの欠測」とは異なる）。
            detect_data: dict[str, object] = {
                "frame_index": frame.index,
                "candidates": update.candidates,
                "rejected": {
                    rejection.reason: rejection.count for rejection in update.rejections
                },
            }
            if self._pending_detect_ms is not None:
                detect_data["detect_ms"] = self._pending_detect_ms
            self._logger.emit(self._detect_stage, "frame", **detect_data)

            # stage="track", event="frame": 同様にちょうど1回だけ送出する。
            rivals = update.appended.rivals if update.appended is not None else 0
            track_data: dict[str, object] = {
                "frame_index": frame.index,
                "appended": appended,
                "points": len(update.track.points),
                "rivals": rivals,
                "gate_rejected": call_gate_rejected,
            }
            if self._pending_track_ms is not None:
                track_data["track_ms"] = self._pending_track_ms
            self._logger.emit(self._track_stage, "frame", **track_data)

            if track_ended_now:
                track = update.track
                duration_ms = 0.0
                if track.points:
                    duration_ms = track.points[-1].point.t_ms - track.started_t_ms
                self._logger.emit(
                    self._track_stage,
                    "track_end",
                    track_id=track.track_id,
                    points=len(track.points),
                    duration_ms=duration_ms,
                    end_reason=track.end_reason,
                )

        # 次のフレームへ持ち越さない（消費済みの所要時間をリセットする）。
        self._pending_detect_ms = None
        self._pending_track_ms = None
        self._prior_track_state = new_state

    def counters(self) -> TrackingCounters:
        """現時点の累積カウンタのスナップショットを返す（要件 8.5, 9.3）。

        集計（代表値・分位点）は行わない。生のカウンタをそのまま返す
        （要件 8.9）。
        """
        return TrackingCounters(
            frames_processed=self._frames_processed,
            frames_without_candidate=self._frames_without_candidate,
            candidates_total=self._candidates_total,
            candidates_rejected=self._candidates_rejected,
            points_estimated=self._points_estimated,
            point_failures=self._point_failures,
            points_appended=self._points_appended,
            gate_rejected=self._gate_rejected,
            tracks_started=self._tracks_started,
            tracks_ended=self._tracks_ended,
        )
