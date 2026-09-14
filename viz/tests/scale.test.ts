// 値域から画面座標への線形写像と、唯一の補間実装を検証する（要件 2.1 / 5.6）。
//
// ここで固定するのは次の 4 点である。
//   1. 端点で写像が**厳密に**一致すること（設計の事後条件）。素朴な式は丸め誤差で端点を外す
//   2. 幅がゼロの値域でゼロ除算を作らず、画面座標の中央を返すこと
//   3. 補間が中間点で中央値を返し、媒介変数を 0 から 1 に丸めること
//   4. 非線形の写像を持ち込んでいないこと（ソースを走査して確認する）
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import * as scaleModule from "../src/scale.js";
import { lerp, linearMap, padRange, rangeOf, type Range } from "../src/scale.js";

// 掃引軸（mm）から SVG の y 座標（上下反転）への写像。素朴な式が端点を外す実例である。
const AXIS_MM: Range = { min: 124.8, max: 2983.3 };
const SCREEN_Y: Range = { min: 879.7, max: 33.8 };

// --- 線形写像 ---------------------------------------------------------------

test("線形写像が値域の端点で画面座標の端点に厳密一致する（要件 2.1）", () => {
  const cases: readonly (readonly [Range, Range])[] = [
    [{ min: 0, max: 100 }, { min: 40, max: 740 }],
    [{ min: -1500, max: 1500 }, { min: 0, max: 960 }],
    [{ min: 0.1, max: 0.7 }, { min: 12.5, max: 987.3 }],
    [AXIS_MM, SCREEN_Y],
    [{ min: -84.09774643882645, max: -817.888855444382 },
     { min: 834.5528921407183, max: -997.506382132981 }],
  ];
  for (const [from, to] of cases) {
    assert.equal(linearMap(from.min, from, to), to.min, `下端が ${to.min} に一致しない`);
    assert.equal(linearMap(from.max, from, to), to.max, `上端が ${to.max} に一致しない`);
  }
});

test("端点の一致は素朴な式が誤差を出す値でも保たれる（要件 2.1）", () => {
  // 素朴な式 `to.min + (value - from.min) / 幅 * (to.max - to.min)` は、
  // 媒介変数が厳密に 1 でも最後の加算で端点を外す。下の値がその実例である。
  const drifted = SCREEN_Y.min + (SCREEN_Y.max - SCREEN_Y.min);
  assert.notEqual(drifted, SCREEN_Y.max, "この値では素朴な式でも誤差が出ない（検証にならない）");
  assert.equal(linearMap(AXIS_MM.max, AXIS_MM, SCREEN_Y), SCREEN_Y.max);
});

test("線形写像が値域の中点を画面座標の中点へ写す（要件 2.1）", () => {
  assert.equal(linearMap(50, { min: 0, max: 100 }, { min: 40, max: 740 }), 390);
  assert.equal(linearMap(0, { min: -10, max: 10 }, { min: 100, max: 200 }), 150);
});

test("幅がゼロの値域でゼロ除算を作らず画面座標の中央を返す（要件 2.1）", () => {
  const flat: Range = { min: 5, max: 5 };
  const screen: Range = { min: 0, max: 100 };
  const mapped = linearMap(5, flat, screen);
  assert.equal(Number.isFinite(mapped), true, `有限でない値を返した: ${mapped}`);
  assert.equal(mapped, 50);
  // 値域の外の値を渡しても同じ中央値であり、無限大にならない。
  assert.equal(linearMap(9, flat, screen), 50);
  assert.equal(linearMap(-9, flat, screen), 50);
  assert.equal(linearMap(5, { min: -3, max: -3 }, { min: 10, max: 30 }), 20);
});

test("線形写像が単調である（要件 2.1）", () => {
  const from: Range = { min: 100, max: 2500 };
  const rising: Range = { min: 40, max: 940 };
  let previousRising = Number.NEGATIVE_INFINITY;
  let previousFalling = Number.POSITIVE_INFINITY;
  for (let step = 0; step <= 240; step += 1) {
    const value = from.min + ((from.max - from.min) * step) / 240;
    const up = linearMap(value, from, rising);
    const down = linearMap(value, from, SCREEN_Y);
    assert.ok(up >= previousRising, `増加向きの写像が戻った: ${up} < ${previousRising}`);
    assert.ok(down <= previousFalling, `反転した写像が戻った: ${down} > ${previousFalling}`);
    previousRising = up;
    previousFalling = down;
  }
});

test("値域の外の値も丸めずに線形のまま写す（要件 2.1）", () => {
  const from: Range = { min: 0, max: 100 };
  const to: Range = { min: 0, max: 10 };
  assert.equal(linearMap(200, from, to), 20);
  assert.equal(linearMap(-50, from, to), -5);
});

test("有限の入力に対して線形写像の出力は有限である（要件 2.1）", () => {
  const samples: readonly number[] = [-1e6, -0.5, 0, 0.3, 1234.5, 1e6];
  for (const value of samples) {
    const mapped = linearMap(value, AXIS_MM, SCREEN_Y);
    assert.equal(Number.isFinite(mapped), true, `${value} の写像が有限でない: ${mapped}`);
  }
});

// --- 値域の算出 -------------------------------------------------------------

test("値の並びから最小と最大の値域を求める（要件 2.1）", () => {
  assert.deepEqual(rangeOf([3, 1, 2]), { min: 1, max: 3 });
  assert.deepEqual(rangeOf([-5, -20, -1]), { min: -20, max: -1 });
  assert.deepEqual(rangeOf([7]), { min: 7, max: 7 });
  assert.deepEqual(rangeOf([2.5, 2.5]), { min: 2.5, max: 2.5 });
});

test("空の並びの値域は 0 から 0 である（要件 2.1）", () => {
  assert.deepEqual(rangeOf([]), { min: 0, max: 0 });
});

// --- 余白の付与 -------------------------------------------------------------

test("余白付与が値域を両端へ対称に広げる（要件 2.1）", () => {
  assert.deepEqual(padRange({ min: 0, max: 100 }, 0.1), { min: -10, max: 110 });
  assert.deepEqual(padRange({ min: -20, max: 20 }, 0.5), { min: -40, max: 40 });
  assert.deepEqual(padRange({ min: 10, max: 30 }, 0), { min: 10, max: 30 });
});

test("幅がゼロの値域に余白を付与しても破綻しない（要件 2.1）", () => {
  const padded = padRange({ min: 5, max: 5 }, 0.1);
  assert.deepEqual(padded, { min: 5, max: 5 });
  // 幅ゼロのまま写像へ渡しても中央値が返り、無限大にならない。
  assert.equal(linearMap(5, padded, { min: 0, max: 100 }), 50);
});

test("負の比率が値域を狭めない（要件 2.1）", () => {
  const original: Range = { min: 0, max: 100 };
  assert.deepEqual(padRange(original, -0.25), original);
});

// --- 補間 -------------------------------------------------------------------

test("補間が中間点で中央値を返す（要件 5.6）", () => {
  assert.equal(lerp(3, 8, 0.5), 5.5);
  assert.equal(lerp(-10, 10, 0.5), 0);
  assert.equal(lerp(3, 8, 0.25), 4.25);
});

test("補間が始点と終点を厳密に返す（要件 5.6）", () => {
  assert.equal(lerp(0.1, 0.7, 0), 0.1);
  assert.equal(lerp(0.1, 0.7, 1), 0.7);
  assert.equal(lerp(SCREEN_Y.min, SCREEN_Y.max, 1), SCREEN_Y.max);
});

test("補間の媒介変数を 0 から 1 に丸める（要件 5.6）", () => {
  assert.equal(lerp(2, 6, -1), 2, "0 未満の媒介変数が始点へ丸められていない");
  assert.equal(lerp(2, 6, 2), 6, "1 を超える媒介変数が終点へ丸められていない");
  assert.equal(lerp(2, 6, -0.0001), 2);
  assert.equal(lerp(2, 6, 1.0001), 6);
});

test("補間が媒介変数について単調である（要件 5.6）", () => {
  let previous = Number.NEGATIVE_INFINITY;
  for (let step = -20; step <= 120; step += 1) {
    const current = lerp(-4, 12, step / 100);
    assert.ok(current >= previous, `補間が戻った: ${current} < ${previous}`);
    previous = current;
  }
  assert.equal(previous, 12);
});

// --- 境界（層 0 であること） -------------------------------------------------

test("Scale モジュールが公開するのは 4 つの関数のみである", () => {
  const exported = Object.entries(scaleModule);
  assert.deepEqual(
    exported.map(([name]) => name).sort(),
    ["lerp", "linearMap", "padRange", "rangeOf"],
  );
  for (const [name, value] of exported) {
    assert.equal(typeof value, "function", `${name} を関数として公開していない`);
  }
});

test("Scale モジュールは何も import せず、DOM と非線形の演算を持たない（要件 2.1 / 5.6）", () => {
  const source = readFileSync(new URL("../src/scale.js", import.meta.url), "utf8");
  assert.doesNotMatch(source, /^\s*import\b/m, "scale は何も import してはならない（層 0）");
  assert.doesNotMatch(source, /\brequire\s*\(/, "require を含んではならない");
  assert.doesNotMatch(source, /\bdocument\b|\bwindow\b/, "DOM に触れてはならない");
  assert.doesNotMatch(
    source,
    /Math\.(?!min\b|max\b|abs\b|round\b|floor\b|ceil\b)[A-Za-z]/,
    "許可された算術（min / max / abs / round / floor / ceil）以外を用いてはならない",
  );
  assert.doesNotMatch(
    source,
    /\b(sqrt|pow|log|exp|sin|cos|tan|atan2|hypot)\s*\(/,
    "非線形の演算を含んではならない",
  );
});
