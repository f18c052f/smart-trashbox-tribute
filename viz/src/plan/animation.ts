// 代表 Throw Record を時間軸の再生用データへ変換し、任意時刻のフレームを返す
// （要件 5.1〜5.4 / 5.6 / 5.7、設計 `#### AnimationPlanner`）。
//
// このモジュールは**層 2** である。import してよいのは `schema` / `scale` のみで、
// DOM に触れない。`format` を import しないのは意図的である。アニメーション面の文言は
// `view/render.ts` 側で組み立てる。フレームごとに文字列を作らないための措置である。
//
// 五つの規律をここで固定する。
//   1. **投影は 2 種類のみ。** 床面を真上から見た `"xy"`（u=x_mm, v=y_mm）と、
//      水平方向と高さの `"xz"`（u=x_mm, v=z_mm）。切替 UI は作らない
//      （両方を同時に描く前提のため）。u に共通の x_mm を選ぶのは、2 つの投影を
//      並べたときに横軸が揃って見えるようにするためである
//   2. **現在位置は隣り合う 2 つの観測点の線形補間でのみ求める。** 唯一の補間実装は
//      `scale.ts` の `lerp` であり、本モジュールで別の補間を書かない（要件 5.6）
//   3. **予測は生成順（入力配列の順）に並べる。** 時刻や値で並べ替えない
//   4. **観測点を並べ替えない。** 並んでいなければ上流の問題であり、防御的な整列もしない
//   5. **記録に無い量を作らない。** 移動体の位置・真の軌道・記録範囲外への外挿を返さない
//      （要件 5.7）。範囲外の時刻は境界のサンプルへ寄せるだけで、外挿はしない。
//      予測落下地点（`hit`）は上流が `predicted_hit_x_mm` / `predicted_hit_y_mm`
//      （床面の 2 次元座標）としてのみ持つ量であり、高さ成分は記録に存在しない。
//      そのため `hit` は投影によらず、この 2 つのフィールドから作る
//      （高さ 0 を補って作ることはしない）

import { lerp, rangeOf, type Range } from "../scale.js";
import type {
  PredictionEntryUnion,
  SampleEntry,
  ThrowRecordDoc,
} from "../schema.js";

// --- 公開する契約 -------------------------------------------------------------

/** 投影の種類。床面の上面視 (`"xy"`) と、水平方向・高さの側面視 (`"xz"`)。 */
export type Projection = "xy" | "xz";

/** 投影後の 1 点。 */
export interface Point2D {
  readonly u: number;
  readonly v: number;
}

/** 予測 1 件の描画マーカー。無効な予測は `hit` を持たず、理由と詳細を持つ。 */
export interface PredictionMarker {
  readonly index: number;
  readonly sampleCount: number;
  readonly basedOnTimeMs: number | null;
  readonly hit: Point2D | null; // 無効予測では null
  readonly invalidReason: string | null;
  readonly detail: string | null;
}

/** 軌跡アニメーションのプラン。1 つの Throw Record と 1 つの投影に対して 1 つ作る。 */
export interface AnimationPlan {
  readonly recordId: string;
  readonly projection: Projection;
  readonly startTimeMs: number;
  readonly endTimeMs: number;
  readonly path: readonly Point2D[]; // 全観測点（投影済み）
  readonly times: readonly number[]; // path と同じ長さ
  readonly predictions: readonly PredictionMarker[];
  readonly uRange: Range;
  readonly vRange: Range;
}

/** 任意時刻のフレーム。 */
export interface FramePlan {
  readonly timeMs: number;
  readonly visibleCount: number; // timeMs 以下の観測点数
  readonly head: Point2D | null; // 線形補間で求めた現在位置
  readonly activePrediction: PredictionMarker | null;
}

// --- 投影 ---------------------------------------------------------------------

/** 観測サンプル 1 点を投影する。u は両投影で共通の x_mm。 */
function projectSample(sample: SampleEntry, projection: Projection): Point2D {
  return projection === "xy"
    ? { u: sample.x_mm, v: sample.y_mm }
    : { u: sample.x_mm, v: sample.z_mm };
}

// --- 予測 ---------------------------------------------------------------------

/**
 * 予測 1 件をマーカーへ変換する。
 * `hit` は投影に関わらず `predicted_hit_x_mm` / `predicted_hit_y_mm` から作る。
 * 落下地点は上流がこの 2 フィールドとしてのみ記録しており、高さ成分を持たないためである。
 */
function buildPredictionMarker(entry: PredictionEntryUnion, index: number): PredictionMarker {
  if (entry.kind === "invalid") {
    return {
      index,
      sampleCount: entry.sample_count,
      basedOnTimeMs: entry.based_on_time_ms,
      hit: null,
      invalidReason: entry.reason,
      detail: entry.detail,
    };
  }
  return {
    index,
    sampleCount: entry.sample_count,
    basedOnTimeMs: entry.based_on_time_ms,
    hit: { u: entry.predicted_hit_x_mm, v: entry.predicted_hit_y_mm },
    invalidReason: null,
    detail: null,
  };
}

/**
 * 「`basedOnTimeMs <= timeMs` を満たす最後の予測」を、配列の先頭から辿って選ぶ。
 * 値の大小で選び直すのではなく、生成順（配列順）の最後を残す。
 * `basedOnTimeMs` が `null` の予測は条件を満たせない（選ばれない）。
 */
function activePredictionAt(
  predictions: readonly PredictionMarker[],
  timeMs: number,
): PredictionMarker | null {
  let selected: PredictionMarker | null = null;
  for (const prediction of predictions) {
    if (prediction.basedOnTimeMs !== null && prediction.basedOnTimeMs <= timeMs) {
      selected = prediction;
    }
  }
  return selected;
}

// --- 現在位置（head） ----------------------------------------------------------

/**
 * `timeMs` における現在位置を求める。
 *
 * - 観測点が 0 件なら `null`（描く量が無い）
 * - 観測点が 1 件なら、補間を試みずその 1 点を常に返す
 * - `timeMs` が範囲外なら、外挿せず境界の観測点へ寄せる
 * - それ以外は `timeMs` を挟む隣接 2 点の間を `scale.lerp` で線形に補う
 */
function headAt(
  path: readonly Point2D[],
  times: readonly number[],
  timeMs: number,
): Point2D | null {
  const first = path[0];
  const firstTime = times[0];
  if (first === undefined || firstTime === undefined) {
    return null;
  }
  const lastIndex = path.length - 1;
  const last = path[lastIndex];
  const lastTime = times[lastIndex];
  if (last === undefined || lastTime === undefined) {
    return first;
  }

  if (timeMs <= firstTime) {
    return first;
  }
  if (timeMs >= lastTime) {
    return last;
  }

  for (let i = 0; i < lastIndex; i += 1) {
    const t0 = times[i];
    const t1 = times[i + 1];
    const p0 = path[i];
    const p1 = path[i + 1];
    if (t0 === undefined || t1 === undefined || p0 === undefined || p1 === undefined) {
      continue;
    }
    if (timeMs >= t0 && timeMs <= t1) {
      const span = t1 - t0;
      const ratio = span === 0 ? 0 : (timeMs - t0) / span;
      return { u: lerp(p0.u, p1.u, ratio), v: lerp(p0.v, p1.v, ratio) };
    }
  }
  // 観測点が時刻順でない場合など、どの隣接区間にも収まらない時刻はここに来る。
  // 上流の並び順をそのまま使う規律（並べ替えない）の帰結であり、境界へ寄せる。
  return last;
}

// --- 入口 -----------------------------------------------------------------

/**
 * Throw Record と投影からアニメーションのプランを組み立てる。
 *
 * 観測点は入力の順序をそのまま `path` / `times` に反映する（並べ替えない）。
 * 観測点が 0 件でも例外を投げず、空の `path` / `times` を返す。
 */
export function buildAnimationPlan(record: ThrowRecordDoc, projection: Projection): AnimationPlan {
  const path = record.samples.map((sample) => projectSample(sample, projection));
  const times = record.samples.map((sample) => sample.t_ms);
  const timeRange = rangeOf(times);
  const predictions = record.predictions.map((entry, index) => buildPredictionMarker(entry, index));

  return {
    recordId: record.record_id,
    projection,
    startTimeMs: timeRange.min,
    endTimeMs: timeRange.max,
    path,
    times,
    predictions,
    uRange: rangeOf(path.map((point) => point.u)),
    vRange: rangeOf(path.map((point) => point.v)),
  };
}

/**
 * `timeMs` におけるフレームを返す。
 *
 * `activePrediction` は記録済みの `predictions` から選ぶだけであり、予測を再計算しない。
 */
export function frameAt(plan: AnimationPlan, timeMs: number): FramePlan {
  let visibleCount = 0;
  for (const t of plan.times) {
    if (t <= timeMs) {
      visibleCount += 1;
    }
  }
  return {
    timeMs,
    visibleCount,
    head: headAt(plan.path, plan.times, timeMs),
    activePrediction: activePredictionAt(plan.predictions, timeMs),
  };
}
