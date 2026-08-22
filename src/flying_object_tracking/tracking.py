"""1投擲 = 1物体の単一物体追跡（タスク 4.1 `SingleObjectTracker`）。

design.md「L6-L7: 追跡と統合 → SingleObjectTracker」が定める対応付け規則
（手順1〜5）をそのまま実装する。要件 6.1〜6.9, 9.1, 9.5 を満たす。

**依存方向（design.md「Dependency Direction」表の `tracking` 行）**:
`errors` / `types` / `config`（0〜2 層）と標準ライブラリのみを import する。
**`detection` / `projection` を import しない**。追跡は「時刻付きカメラ座標
点の列」だけを扱い、その点がどの検出方式・逆投影経路から来たかを知らない
（要件でいう「点だけを受け取る」ことの実装そのもの）。この分離により、
本モジュールのテストは検出・逆投影を経由せず合成点列だけで書ける
（要件 11.2）。

## このタスクで確定させた設計判断

design.md はいくつかの点を書き切っていない。以下は本タスクが下した決定で
あり、根拠と一緒にここへ記録する（後続タスクが参照できるように）。

1. **IDLE 状態での開始規則**: design.md の状態遷移図は「first accepted
   point」で `IDLE -> TRACKING` に遷移するとしか書いておらず、
   `TrackerConfig.min_points_to_start`（既定 1）の意味を明示していない。
   本実装は次のように解釈する: `update()` に渡された `points` の要素数が
   `min_points_to_start` 未満の間は `IDLE` に留まる（まだ追跡開始とは
   見なさない。欠測としても数えない — まだ追跡自体が始まっていないため）。
   `min_points_to_start` 個以上そろった最初のフレームで、その `points`
   全体に対応付け規則の手順4と同じタイブレーク（`(z_mm, y_mm, x_mm)` の
   辞書順で最小）を適用し、1点を確定的に選んで追跡を開始する。既定値 1 の
   下では「最初に得られた1点で即座に開始する」という状態遷移図の記述と
   そのまま一致する。この時点ではまだ予測位置（軌道）が存在しないため、
   選ばれなかった残りの点はすべて `rivals` として記録する（手順3のゲート
   判定を「まだ軌道が無い」場合に拡張した扱い）。

2. **点はあるが全てゲート外の場合の欠測計上**: design.md 手順1は
   「点が0個なら欠測を1つ数える」としか書いていないが、要件 6.5 は
   「対応付け可能な候補が得られない場合」を欠測超過の条件とする。
   `points` が非空でも全候補が `max_step_mm` のゲート外であれば
   「対応付け可能な候補が得られない」という点で0個の場合と変わらないため、
   本実装はこれも欠測として数える（`missing` カウンタを1つ進める）。

3. **`max_points` 到達のタイミング**: 「追跡が…点数上限に達したら終了する」
   を文字通り読み、上限に達した点を追加した**その `update()` 呼び出し内**
   で `ENDED(MAX_POINTS)` に遷移する。次のフレームまで待たない（呼び出し
   側が上限到達を1フレーム遅れて知る事態を避けるため）。

4. **`gate_rejected`（ゲートで落ちた件数）の表現**: `CameraTrack` /
   `TrackUpdate`（タスク 1.3 で確定済みの不変型）に専用のフィールドは
   無い。`SingleObjectTracker` の公開シェイプ（`__init__` / `state` /
   `update` / `finish` / `reset`）を design.md の記載どおりに保つため、
   独立したフィールド・メソッドは追加しない。

   **注意（レビューで実証された誤り）: `sum(p.rivals for p in
   track.points)` という事後合計では真値を再現できない。** あるフレーム
   で候補が複数あっても全てがゲート外だった場合、`appended is None` と
   なり `TrackPoint` が1つも作られない。そのフレームの候補はどの
   `rivals` にも計上されないため、`track.points` を後から辿る方式は
   構造的に過小評価する。

   正しい算出は、`TRACKING` 状態で対応付けを試みた `update()` 呼び出し
   **ごとに**次を累積することによる（`IDLE` でまだ開始していない呼び出し
   は「まだ軌道が無い」ため対象外）:

   - `appended is not None` の場合: `candidates - 1`
     （採用された1点を除く全候補がその回の「不採用」であり、内訳は
     ゲート内で敗れた分 = `appended.rivals` と、ゲート外だった分 =
     `candidates - 1 - appended.rivals` の合計に等しい）
   - `appended is None` の場合: `candidates`
     （0件なら0のまま）

   `update()` が毎呼び出しで返す `TrackUpdate.candidates` /
   `appended` の2値だけから、呼び出し側（`TrackingMetrics`, タスク5.1）が
   正確な累計を再構成できる。

5. **`update()` を `ENDED` 状態で呼んだ場合**: 例外を送出せず、現在の
   スナップショットをそのまま返す（`appended=None`）。以後の呼び出しは
   状態を変えない（冪等）。要件 9.5 の「例外にしない」方針を、終了後の
   誤った呼び出しに対しても一貫させるため。

6. **`finish()` を既に `ENDED` の追跡へ呼んだ場合**: 既存の `end_reason`
   を上書きしない（内部的に確定した終了理由 — 例えば `LOST` — が、後から
   渡された理由で消えるのを防ぐ）。まだ `ENDED` でない場合のみ、渡された
   `reason` を採用して `ENDED` にする。
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from flying_object_tracking.config import TrackerConfig
from flying_object_tracking.types import (
    HANDOFF_VERSION,
    CameraPoint,
    CameraTrack,
    CoordinateFrame,
    SourceKind,
    TrackEndReason,
    TrackPoint,
    TrackState,
    TrackUpdate,
)

__all__ = ["SingleObjectTracker"]


class SingleObjectTracker:
    """1投擲 = 1物体を前提とした、フレーム間対応付けとライフサイクル管理。

    複数物体の同時追跡は持たない（要件 6.8）。誤って人・手などの対象外の
    動体を追跡しても例外にせず、通常の点列として出力する（要件 9.1, 9.5）。
    最終的な棄却判断は持たない。
    """

    def __init__(
        self,
        config: TrackerConfig,
        source: SourceKind,
        detector_kind: str,
    ) -> None:
        self._config = config
        self._source = source
        self._detector_kind = detector_kind

        self._state: TrackState = TrackState.IDLE
        self._end_reason: TrackEndReason | None = None
        self._started_t_ms: float | None = None
        self._points: list[TrackPoint] = []
        self._missing_count: int = 0
        self._next_track_id: int = 0
        self._track_id: int = 0

    @property
    def state(self) -> TrackState:
        """現在のライフサイクル状態（`IDLE` / `TRACKING` / `ENDED`）。"""
        return self._state

    def update(
        self,
        points: Sequence[CameraPoint],
        *,
        frame_index: int,
        frame_seq: int,
        gap_before: int,
    ) -> TrackUpdate:
        """1フレーム分の候補点を対応付け、逐次結果を返す（要件 6.7）。

        追跡の終了を待たず、呼び出しのたびに `TrackUpdate` を返す。
        """
        candidates = len(points)

        if self._state is TrackState.ENDED:
            # 設計判断5: 終了後の呼び出しは何もせず、現在の状態を返す。
            return self._make_update(appended=None, candidates=candidates)

        if self._state is TrackState.IDLE:
            appended = self._try_start(
                points,
                frame_index=frame_index,
                frame_seq=frame_seq,
                gap_before=gap_before,
            )
            if appended is not None:
                self._after_append()
            return self._make_update(appended=appended, candidates=candidates)

        # TrackState.TRACKING
        appended = self._try_associate(
            points,
            frame_index=frame_index,
            frame_seq=frame_seq,
            gap_before=gap_before,
        )
        if appended is not None:
            self._missing_count = 0
            self._after_append()
        else:
            self._missing_count += 1
            if self._missing_count > self._config.max_missing_frames:
                self._state = TrackState.ENDED
                self._end_reason = TrackEndReason.LOST
        return self._make_update(appended=appended, candidates=candidates)

    def finish(self, reason: TrackEndReason) -> CameraTrack:
        """呼び出し側の明示的な打ち切りで追跡を終了する。

        `SOURCE_END`（入力が尽きた）や `RESET`（呼び出し側の明示的な打ち
        切り）のために、`TrackingPipeline` 等の上位から呼ばれる。既に
        `ENDED` の場合は既存の `end_reason` を保つ（設計判断6）。
        """
        if self._state is not TrackState.ENDED:
            self._state = TrackState.ENDED
            self._end_reason = reason
        return self._snapshot()

    def reset(self) -> None:
        """内部状態を `IDLE` へ戻し、新しい追跡を始められるようにする。

        `finish()` とは独立した2段階の操作である。`finish()` は現在の
        追跡を確定した `CameraTrack` として取り出し、`reset()` はその後
        内部状態を空にする。次に `update()` で追跡が始まったときの
        `track_id` は、これまでに開始した追跡数から自動的に増分される。
        """
        self._state = TrackState.IDLE
        self._end_reason = None
        self._started_t_ms = None
        self._points = []
        self._missing_count = 0

    # ------------------------------------------------------------------
    # 内部ヘルパー
    # ------------------------------------------------------------------

    def _try_start(
        self,
        points: Sequence[CameraPoint],
        *,
        frame_index: int,
        frame_seq: int,
        gap_before: int,
    ) -> TrackPoint | None:
        """IDLE 状態での開始判定（設計判断1）。"""
        if len(points) < self._config.min_points_to_start:
            return None

        ordered = sorted(points, key=lambda p: (p.z_mm, p.y_mm, p.x_mm))
        chosen = ordered[0]
        rivals = len(points) - 1

        track_point = TrackPoint(
            point=chosen,
            frame_index=frame_index,
            frame_seq=frame_seq,
            gap_before=gap_before,
            rivals=rivals,
        )
        self._points.append(track_point)
        self._started_t_ms = chosen.t_ms
        self._state = TrackState.TRACKING
        self._track_id = self._next_track_id
        self._next_track_id += 1
        return track_point

    def _try_associate(
        self,
        points: Sequence[CameraPoint],
        *,
        frame_index: int,
        frame_seq: int,
        gap_before: int,
    ) -> TrackPoint | None:
        """TRACKING 状態での対応付け（対応付け規則 手順2〜5）。"""
        if len(points) == 0:
            return None

        predicted = self._predict()
        gated: list[tuple[CameraPoint, float]] = []
        for p in points:
            distance = _distance(predicted, p)
            if distance <= self._config.max_step_mm:
                gated.append((p, distance))

        if not gated:
            return None

        # 手順3: 最も近い点を採る。手順4: 距離同値は (z, y, x) 辞書順最小。
        gated.sort(key=lambda pair: (pair[1], pair[0].z_mm, pair[0].y_mm, pair[0].x_mm))
        winner, _winner_distance = gated[0]
        rivals = len(gated) - 1  # 手順5: ゲート内に居た他の候補数

        track_point = TrackPoint(
            point=winner,
            frame_index=frame_index,
            frame_seq=frame_seq,
            gap_before=gap_before,
            rivals=rivals,
        )
        self._points.append(track_point)
        return track_point

    def _predict(self) -> tuple[float, float, float]:
        """予測位置を返す（手順2: 直近2点の等速外挿、1点なら直前点）。"""
        last = self._points[-1].point
        if len(self._points) >= 2:
            second_last = self._points[-2].point
            return (
                last.x_mm + (last.x_mm - second_last.x_mm),
                last.y_mm + (last.y_mm - second_last.y_mm),
                last.z_mm + (last.z_mm - second_last.z_mm),
            )
        return (last.x_mm, last.y_mm, last.z_mm)

    def _after_append(self) -> None:
        """点数上限チェック（設計判断3: 到達した同じ呼び出し内で終了する）。"""
        if len(self._points) >= self._config.max_points:
            self._state = TrackState.ENDED
            self._end_reason = TrackEndReason.MAX_POINTS

    def _snapshot(self) -> CameraTrack:
        return CameraTrack(
            handoff_version=HANDOFF_VERSION,
            frame=CoordinateFrame.CAMERA,
            track_id=self._track_id,
            started_t_ms=self._started_t_ms if self._started_t_ms is not None else 0.0,
            points=tuple(self._points),
            state=self._state,
            end_reason=self._end_reason,
            source=self._source,
            detector_kind=self._detector_kind,
        )

    def _make_update(self, *, appended: TrackPoint | None, candidates: int) -> TrackUpdate:
        # 本モジュールは検出・逆投影の候補/失敗情報を知らない（点だけを
        # 受け取る）ため、rejections / point_failures は常に空タプル。
        return TrackUpdate(
            track=self._snapshot(),
            appended=appended,
            candidates=candidates,
            rejections=(),
            point_failures=(),
        )


def _distance(predicted: tuple[float, float, float], point: CameraPoint) -> float:
    """予測位置と候補点のユークリッド距離（3D, mm）。"""
    return math.dist(predicted, (point.x_mm, point.y_mm, point.z_mm))
