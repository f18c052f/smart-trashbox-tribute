// 値域から画面座標への線形写像と、本 Spec で唯一の補間実装（要件 2.1 / 5.6）。
//
// このモジュールは**層 0** である。何も import せず、DOM に触れず、
// SVG の単位や属性名を知らない。
//
// 三つの規律をここで固定する。
//   1. 線形の写像だけを提供する。対数・平方根・三角関数といった非線形の写像を持たない。
//      軸の意味を変える表示は上流の軸定義から離れるため、見やすさを理由に足さない。
//   2. 補間は `lerp` 1 つだけである。他のモジュールで補間を書かない（要件 5.6）。
//   3. 値域の幅がゼロでも除算を作らない。ゼロ幅は掃引軸の値が 1 通りのときに実際に起きる。

/** 値の下限と上限の対。画面座標では上下が反転し `min > max` になりうる。 */
export interface Range {
  readonly min: number;
  readonly max: number;
}

/**
 * 値 `value` を値域 `from` から値域 `to` へ線形に写す。
 *
 * `from.min` と `from.max` は `to.min` と `to.max` へ**厳密に**一致する。
 * 素朴な `to.min + 比率 * (to.max - to.min)` は媒介変数が厳密に 1 でも端点を外すため、
 * 両端の重み付き和の形で書く。
 *
 * `from.min === from.max` のときは除算を行わず `to` の中央を返す。
 * 値域の外の値は丸めずにそのまま線形に延長する（軸の外にある点を端へ寄せて見せない）。
 */
export function linearMap(value: number, from: Range, to: Range): number {
  const span = from.max - from.min;
  if (span === 0) {
    return (to.min + to.max) / 2;
  }
  const ratio = (value - from.min) / span;
  return (1 - ratio) * to.min + ratio * to.max;
}

/** 値の並びから値域を求める。空の並びは `{ min: 0, max: 0 }` とする。 */
export function rangeOf(values: readonly number[]): Range {
  if (values.length === 0) {
    return { min: 0, max: 0 };
  }
  let min = Number.POSITIVE_INFINITY;
  let max = Number.NEGATIVE_INFINITY;
  for (const value of values) {
    min = Math.min(min, value);
    max = Math.max(max, value);
  }
  return { min, max };
}

/**
 * 値域の両端に、幅の `ratio` 倍の余白を作る。
 *
 * 負の `ratio` は余白ではないため 0 として扱い、値域を狭めない。
 * 幅がゼロの値域は余白もゼロであり、幅ゼロのまま返る（`linearMap` が中央値で受ける）。
 */
export function padRange(range: Range, ratio: number): Range {
  const amount = (range.max - range.min) * Math.max(0, ratio);
  return { min: range.min - amount, max: range.max + amount };
}

/**
 * 始点 `a` と終点 `b` の間を線形に補う。**本 Spec で唯一の補間実装**である（要件 5.6）。
 *
 * `t` は 0 から 1 に丸めるため、記録の範囲外の時刻を渡しても端点の外へ出ない。
 * `t` が 0 のとき `a`、1 のとき `b` を厳密に返す。
 */
export function lerp(a: number, b: number, t: number): number {
  const clamped = Math.min(1, Math.max(0, t));
  return (1 - clamped) * a + clamped * b;
}
