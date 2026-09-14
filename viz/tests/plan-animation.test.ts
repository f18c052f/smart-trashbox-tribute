// 軌跡アニメーションのプランを検証する（要件 5.1〜5.4 / 5.6 / 5.7、設計 `#### AnimationPlanner`）。
//
// ここで固定するのは次の 8 点である。
//   1. 現在位置（`head`）は隣り合う 2 観測点の**線形補間のみ**で求まり、中間時刻で中点になる
//      （要件 5.6）
//   2. 無効な予測は `hit: null` と理由・詳細を持ち、有効な予測は `hit` を持ち理由・詳細を持たない
//      （要件 5.4）
//   3. `activePrediction` は「`basedOnTimeMs <= timeMs` を満たす最後の予測」であり、
//      時刻の大小で選び直すのではなく**生成順の最後**で選ぶ（要件 5.2）
//   4. 観測点が 0 件でも 1 件でも破綻しない。記録に無い時刻・位置を作らない（要件 5.7）
//   5. 記録範囲外の時刻は境界のサンプルへ寄せるだけで、外挿しない（要件 5.7）
//   6. 予測は生成順（入力配列の順）のまま並び、時刻や値で並べ替えない
//   7. 観測点も並べ替えない。入力の順序をそのまま `path` / `times` に反映する
//   8. `plan/animation.ts` は `schema` / `scale` のみに依存し、`format` を import しない
//
// 入力は `fixtures.ts` の `makeThrowRecord` 系を再利用する。
// `t = 0/30/60/90/120` の等間隔サンプルは `t=45` が厳密な中点になるよう作られている
// （タスク 2.1 の実装メモ）。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  buildAnimationPlan,
  frameAt,
  type AnimationPlan,
  type Point2D,
} from "../src/plan/animation.js";
import { lerp, rangeOf } from "../src/scale.js";
import type { PredictionEntryUnion, SampleEntry, ThrowRecordDoc } from "../src/schema.js";
import {
  makeInvalidPrediction,
  makePrediction,
  makeSample,
  makeThrowRecord,
  makeEmptySampleThrowRecord,
} from "./fixtures.js";

// --- 補助 ---------------------------------------------------------------------

/** 既定の代表記録から "xy" 投影のプランを作る。 */
function xyPlanOf(overrides: Partial<ThrowRecordDoc> = {}): AnimationPlan {
  return buildAnimationPlan(makeThrowRecord(overrides), "xy");
}

function assertPoint(actual: Point2D | null, expected: Point2D, label: string): void {
  assert.ok(actual !== null, `${label}: head が null である`);
  assert.equal(actual?.u, expected.u, `${label}: u が一致しない`);
  assert.equal(actual?.v, expected.v, `${label}: v が一致しない`);
}

// --- 1. 線形補間のみで現在位置を求める（要件 5.6） --------------------------

test("隣接 2 観測点の中間時刻で head が座標の中点になる（xy 投影）", () => {
  const plan = xyPlanOf();
  // t=30: index1 (x=100, y=40) / t=60: index2 (x=200, y=80)。中点は t=45。
  const frame = frameAt(plan, 45);
  assertPoint(frame.head, { u: 150, v: 60 }, "xy 中点");
  assert.equal(frame.timeMs, 45);
});

test("隣接 2 観測点の中間時刻で head が座標の中点になる（xz 投影）", () => {
  const plan = buildAnimationPlan(makeThrowRecord(), "xz");
  // t=30: index1 (x=100, z=1200) / t=60: index2 (x=200, z=1000)。
  const frame = frameAt(plan, 45);
  assertPoint(frame.head, { u: 150, v: 1100 }, "xz 中点");
});

test("中点以外の時刻でも scale.lerp と同じ比率で補間する", () => {
  const plan = xyPlanOf();
  // t=30 (x=100,y=40) と t=60 (x=200,y=80) の間、t=50 は比率 (50-30)/30 = 2/3。
  const ratio = (50 - 30) / (60 - 30);
  const expected: Point2D = { u: lerp(100, 200, ratio), v: lerp(40, 80, ratio) };
  const frame = frameAt(plan, 50);
  assertPoint(frame.head, expected, "非中点の補間");
});

test("観測点と厳密に同じ時刻では補間を介さずその点そのものを返す", () => {
  const plan = xyPlanOf();
  const frame = frameAt(plan, 60);
  assertPoint(frame.head, { u: 200, v: 80 }, "index2 の観測点");
});

// --- 2. 無効な予測と有効な予測の区別（要件 5.4） -----------------------------

test("無効な予測は hit を持たず理由と詳細を持つ", () => {
  const plan = xyPlanOf();
  const invalid = plan.predictions[0];
  assert.ok(invalid !== undefined);
  assert.equal(invalid?.hit, null);
  assert.equal(invalid?.invalidReason, "insufficient_samples");
  assert.equal(invalid?.detail, "有効な観測サンプルが最小サンプル数に満たない");
  assert.equal(invalid?.basedOnTimeMs, 30);
  assert.equal(invalid?.sampleCount, 1);
});

test("有効な予測は hit を持ち、理由と詳細は双方とも null になる", () => {
  const plan = xyPlanOf();
  const valid = plan.predictions[1];
  assert.ok(valid !== undefined);
  assert.deepEqual(valid?.hit, { u: 380, v: 152 });
  assert.equal(valid?.invalidReason, null);
  assert.equal(valid?.detail, null);
  assert.equal(valid?.basedOnTimeMs, 60);
  assert.equal(valid?.sampleCount, 3);

  const valid2 = plan.predictions[2];
  assert.deepEqual(valid2?.hit, { u: 420, v: 168 });
});

test("予測の hit は投影に関わらず predicted_hit_x_mm/y_mm から作る（床の落下地点は高さを持たない）", () => {
  const xy = xyPlanOf();
  const xz = buildAnimationPlan(makeThrowRecord(), "xz");
  assert.deepEqual(xy.predictions[1]?.hit, xz.predictions[1]?.hit);
});

// --- 3. activePrediction は生成順の最後（要件 5.2） --------------------------

test("timeMs=75 では based_on_time_ms 60 の予測が選ばれる（30 でも 90 でもない）", () => {
  const plan = xyPlanOf();
  const frame = frameAt(plan, 75);
  assert.equal(frame.activePrediction?.basedOnTimeMs, 60);
  assert.equal(frame.activePrediction?.index, 1);
});

test("activePrediction は値の大小ではなく生成順（配列順）の最後で選ばれる", () => {
  // 生成順: [based=50, based=20]。timeMs=60 では両方が条件を満たすが、
  // 「最大の based_on_time_ms」なら 50 側が選ばれてしまう。
  // 設計は「予測を計算し直さない」＝配列順で最後に条件を満たしたものを選ぶ、なので 20 側が正しい。
  const predictions: readonly PredictionEntryUnion[] = [
    makePrediction({ based_on_time_ms: 50, sample_count: 2 }),
    makePrediction({ based_on_time_ms: 20, sample_count: 5 }),
  ];
  const plan = buildAnimationPlan(makeThrowRecord({ predictions }), "xy");
  const frame = frameAt(plan, 60);
  assert.equal(frame.activePrediction?.basedOnTimeMs, 20);
  assert.equal(frame.activePrediction?.sampleCount, 5);
  assert.equal(frame.activePrediction?.index, 1);
});

test("based_on_time_ms が null の予測は選ばれない", () => {
  const predictions: readonly PredictionEntryUnion[] = [
    makePrediction({ based_on_time_ms: 20, sample_count: 2 }),
    makeInvalidPrediction({ based_on_time_ms: null, sample_count: 0 }),
  ];
  const plan = buildAnimationPlan(makeThrowRecord({ predictions }), "xy");
  const frame = frameAt(plan, 100);
  assert.equal(frame.activePrediction?.basedOnTimeMs, 20);
});

test("timeMs より前に基準時刻を持つ予測が 1 件も無ければ activePrediction は null", () => {
  const plan = xyPlanOf();
  const frame = frameAt(plan, 10);
  assert.equal(frame.activePrediction, null);
});

test("予測は生成順（入力配列の順）のまま並び、基準時刻順に並べ替えない", () => {
  const predictions: readonly PredictionEntryUnion[] = [
    makePrediction({ based_on_time_ms: 90, sample_count: 4 }),
    makeInvalidPrediction({ based_on_time_ms: 30, sample_count: 1 }),
    makePrediction({ based_on_time_ms: 60, sample_count: 3 }),
  ];
  const plan = buildAnimationPlan(makeThrowRecord({ predictions }), "xy");
  assert.deepEqual(
    plan.predictions.map((p) => p.basedOnTimeMs),
    [90, 30, 60],
  );
});

// --- 4. 観測点 0 件 / 1 件でも破綻しない（要件 5.7） --------------------------

test("観測点が 0 件でも buildAnimationPlan は例外を投げず、path と times が空になる", () => {
  const plan = buildAnimationPlan(makeEmptySampleThrowRecord(), "xy");
  assert.deepEqual(plan.path, []);
  assert.deepEqual(plan.times, []);
  assert.equal(plan.startTimeMs, 0);
  assert.equal(plan.endTimeMs, 0);
});

test("観測点が 0 件のとき frameAt はどの時刻でも head: null を返し、破綻しない", () => {
  const plan = buildAnimationPlan(makeEmptySampleThrowRecord(), "xy");
  for (const timeMs of [-100, 0, 30, 1000]) {
    const frame = frameAt(plan, timeMs);
    assert.equal(frame.head, null);
    assert.equal(frame.visibleCount, 0);
  }
});

test("観測点が 0 件でも記録済みの予測は選択できる（予測の再計算をしない経路の確認）", () => {
  const plan = buildAnimationPlan(makeEmptySampleThrowRecord(), "xy");
  const frame = frameAt(plan, 30);
  assert.equal(frame.activePrediction?.invalidReason, "insufficient_samples");
});

test("観測点が 1 件のとき path の長さは 1 で、補間を試みずその点を返す", () => {
  const singleSample: SampleEntry = makeSample({ t_ms: 50, x_mm: 10, y_mm: 20, z_mm: 900 });
  const plan = buildAnimationPlan(makeThrowRecord({ samples: [singleSample] }), "xy");
  assert.equal(plan.path.length, 1);
  assert.equal(plan.startTimeMs, 50);
  assert.equal(plan.endTimeMs, 50);
  for (const timeMs of [0, 50, 999]) {
    const frame = frameAt(plan, timeMs);
    assertPoint(frame.head, { u: 10, v: 20 }, `単一観測点 timeMs=${timeMs}`);
  }
});

// --- 5. 記録範囲外は境界へ寄せるだけで外挿しない（要件 5.7） ------------------

test("startTimeMs より前の時刻では head が先頭の観測点に留まる（外挿しない）", () => {
  const plan = xyPlanOf();
  const frame = frameAt(plan, -1000);
  assertPoint(frame.head, { u: 0, v: 0 }, "先頭に留まる");
  assert.equal(frame.visibleCount, 0);
});

test("endTimeMs より後の時刻では head が末尾の観測点に留まる（外挿しない）", () => {
  const plan = xyPlanOf();
  const frame = frameAt(plan, 100000);
  assertPoint(frame.head, { u: 400, v: 160 }, "末尾に留まる");
  assert.equal(frame.visibleCount, plan.path.length);
});

// --- 事後条件（設計 AnimationPlanner の Postconditions） --------------------

test("非空の記録では frameAt(startTimeMs).visibleCount が 1 以上になる", () => {
  const plan = xyPlanOf();
  assert.ok(frameAt(plan, plan.startTimeMs).visibleCount >= 1);
});

test("非空の記録では frameAt(endTimeMs).visibleCount が path の長さと一致する", () => {
  const plan = xyPlanOf();
  assert.equal(frameAt(plan, plan.endTimeMs).visibleCount, plan.path.length);
});

// --- 6/7. 観測点を並べ替えない ------------------------------------------------

test("観測点を並べ替えない。入力の順序をそのまま path / times へ反映する", () => {
  // 意図的に時刻の昇順ではない並びを与える。上流の問題を上流の問題のまま通す。
  const unsorted: readonly SampleEntry[] = [
    makeSample({ t_ms: 60, x_mm: 200, y_mm: 80, z_mm: 1000 }),
    makeSample({ t_ms: 0, x_mm: 0, y_mm: 0, z_mm: 1400 }),
    makeSample({ t_ms: 30, x_mm: 100, y_mm: 40, z_mm: 1200 }),
  ];
  const plan = buildAnimationPlan(makeThrowRecord({ samples: unsorted }), "xy");
  assert.deepEqual(plan.times, [60, 0, 30]);
  assert.deepEqual(
    plan.path.map((p) => p.u),
    [200, 0, 100],
  );
});

// --- uRange / vRange は rangeOf で求める（値を作らない） ---------------------

test("uRange / vRange は scale.rangeOf を実際の path から計算したものと一致する", () => {
  const plan = xyPlanOf();
  const expectedU = rangeOf(plan.path.map((p) => p.u));
  const expectedV = rangeOf(plan.path.map((p) => p.v));
  assert.deepEqual(plan.uRange, expectedU);
  assert.deepEqual(plan.vRange, expectedV);
});

test("観測点 0 件では uRange / vRange が rangeOf の空入力と同じ規約（min:0, max:0）になる", () => {
  const plan = buildAnimationPlan(makeEmptySampleThrowRecord(), "xy");
  assert.deepEqual(plan.uRange, { min: 0, max: 0 });
  assert.deepEqual(plan.vRange, { min: 0, max: 0 });
});

// --- 投影ごとに異なる値になる ------------------------------------------------

test("xy と xz は同じ記録から異なる v 値の path を作る（u は共通の x_mm）", () => {
  const xy = xyPlanOf();
  const xz = buildAnimationPlan(makeThrowRecord(), "xz");
  assert.deepEqual(xy.path.map((p) => p.u), xz.path.map((p) => p.u));
  assert.ok(
    xy.path.some((p, index) => p.v !== xz.path[index]?.v),
    "xy と xz の v 値が一致してはならない",
  );
  assert.deepEqual(
    xy.path.map((p) => p.v),
    makeThrowRecord().samples.map((s) => s.y_mm),
  );
  assert.deepEqual(
    xz.path.map((p) => p.v),
    makeThrowRecord().samples.map((s) => s.z_mm),
  );
});

// --- recordId / projection がそのまま反映される ------------------------------

test("recordId と projection がプランにそのまま反映される", () => {
  const record = makeThrowRecord({ record_id: "sweep-cell-0099-trial-000" });
  const plan = buildAnimationPlan(record, "xz");
  assert.equal(plan.recordId, "sweep-cell-0099-trial-000");
  assert.equal(plan.projection, "xz");
});

// --- 8. 層の依存関係（設計 Dependency Direction） ----------------------------

test("コンパイル出力は schema.js / scale.js のみを import し、format.js を import しない", () => {
  const compiledUrl = new URL("../../dist/src/plan/animation.js", import.meta.url);
  const source = readFileSync(compiledUrl, "utf8");
  const specifiers = [...source.matchAll(/from\s*["']([^"']+)["']/g)].map((m) => m[1]);
  for (const specifier of specifiers) {
    assert.ok(
      specifier === "../schema.js" || specifier === "../scale.js",
      `想定外の import: ${String(specifier)}`,
    );
  }
  assert.ok(!specifiers.includes("../format.js"), "format.js を import してはならない");
  assert.doesNotMatch(source, /\bdocument\b|\bwindow\b/, "DOM に触れてはならない");
});
