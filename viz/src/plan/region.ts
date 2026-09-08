// キャッチ可能領域の図を、DOM に依存しない図形と文字列の集合として組み立てる
//（要件 2.1〜2.9 / 3.5）。
//
// このモジュールは**層 2** である。import してよいのは `schema` / `scale` / `format` のみで、
// DOM に触れない。位置を軸の値の並び順で決めるため、`scale` の線形写像は用いない。
//
// 五つの規律をここで固定する。
//   1. **判定を行わない。** `status` は上流の値をそのまま流す。成立割合から状態を作り直さない。
//      入力 JSON が述べていない判定を表示側で作ることは、この Spec で最も避けたい事故である
//   2. **色を決めない。** 返すのは分類キー（`fillKey`）だけであり、実際の色は CSS が持つ。
//      分類キーは「状態のキー」と「表示上の帯のキー」の組み合わせであり、
//      凡例はその 2 系統を別々の項目として並べる
//   3. **格子点の位置は軸の値の並び順で決める。** 値そのものを座標へ線形写像しない。
//      値の間隔が不揃いな軸でも、格子が図として読める形になる
//   4. **格子点の間を補間しない。** 滑らかな境界を描くことは、上流が評価していない点の値を
//      作ることに等しい（設計 RegionPlanner の Risks）
//   5. **表示上の帯（`SUCCESS_RATIO_BANDS`）は上流の `catch_ratio_threshold` とは別物である。**
//      凡例では両者を区別して示し、色分けが表示上の取り決めであることを `displayNote` で述べる
//
// 入力の扱いには 2 つの層がある。読み込み（`load.ts`）は項目ごとの型を検証するが、
// **項目どうしの整合（格子点数が軸の値の個数の積であること、`axis_values` の長さが
// 軸の本数と一致すること）は検証しない。** それらが崩れた入力は「上流の出力が食い違っている」
// という事実であり、表示側はそれを図にできる範囲で描く（例外にしない）。
// 一方、軸番号が範囲外であることは呼び出し側の誤りであり、例外として伝播させる
//（設計 Error Handling の「プログラムの誤り」）。

import {
  formatAxisValue,
  formatNumber,
  metricLabel,
  notEvaluatedReasonLabel,
  statusLabel,
} from "../format.js";
import type {
  AxisSpec,
  AxisValue,
  CellResult,
  CellStatus,
  SweepDocument,
  SweepSpec,
} from "../schema.js";

// --- 公開する契約 -----------------------------------------------------------

/** 描画に使う軸の選択。軸が 3 本以上のとき、残りの軸を `fixed` の値で絞り込む。 */
export interface AxisSelection {
  readonly xAxisIndex: number;
  readonly yAxisIndex: number | null;                         // 1 軸掃引では null
  readonly fixed: { readonly [axisName: string]: AxisValue };  // 3 軸以上のときの固定値
}

/** 格子点 1 点の描画プラン。位置は軸の値の並び順であり、座標ではない。 */
export interface RegionCell {
  readonly column: number;
  readonly row: number;
  readonly status: CellStatus;   // 上流の値そのまま
  readonly fillKey: string;      // 例: "catchable-band-3" / "not-evaluated"
  readonly tooltip: string;      // 軸の値・状態・成立割合・指標
}

/** 凡例 1 行。 */
export interface LegendEntry {
  readonly fillKey: string;
  readonly label: string;
}

/** 描画に使わず固定した軸 1 本。 */
export interface FixedAxisNote {
  readonly axisName: string;
  readonly valueLabel: string;
}

/** キャッチ可能領域の図のプラン。 */
export interface RegionPlan {
  readonly xAxis: AxisSpec;
  readonly yAxis: AxisSpec | null;
  readonly columnLabels: readonly string[];
  readonly rowLabels: readonly string[];
  readonly cells: readonly RegionCell[];
  readonly legend: readonly LegendEntry[];
  readonly fixedAxes: readonly FixedAxisNote[];
  readonly thresholdNote: string;   // 判定閾値と試行回数（要件 3.5）
  readonly displayNote: string;     // 色分けは表示上の取り決めである旨（要件 2.9）
}

/**
 * 本モジュールが読む範囲の掃引ビュー。
 *
 * 設計の署名は `buildRegionPlan(view: SweepView, ...)` であるが、`SweepView` は
 * 層 1 の `load.ts` にあり、[Dependency Direction](design.md) は層 2 から層 1 への
 * import を許していない。読む項目だけを構造的に宣言することで、依存を増やさずに
 * `SweepView` をそのまま渡せる形にする。
 */
export interface RegionSource {
  readonly document: SweepDocument;
}

// --- 表示上の取り決め -------------------------------------------------------

/**
 * 成立割合の帯。**表示上の取り決めであり、上流の `catch_ratio_threshold` とは別物である。**
 * 境界はこの表にのみ置く（設計 RegionPlanner の Implementation Notes）。
 */
interface SuccessRatioBand {
  readonly key: string;
  readonly lowerBound: number;
  readonly boundsLabel: string;
}

const SUCCESS_RATIO_BANDS: readonly SuccessRatioBand[] = [
  { key: "band-0", lowerBound: 0, boundsLabel: "0.250 未満" },
  { key: "band-1", lowerBound: 0.25, boundsLabel: "0.250 以上 0.500 未満" },
  { key: "band-2", lowerBound: 0.5, boundsLabel: "0.500 以上 0.750 未満" },
  { key: "band-3", lowerBound: 0.75, boundsLabel: "0.750 以上" },
];

/** 状態 → 分類キー。上流の列挙値が増えれば、この表が埋まっていない限り tsc が落ちる。 */
const STATUS_FILL_KEYS: Readonly<Record<CellStatus, string>> = {
  catchable: "catchable",
  not_catchable: "not-catchable",
  not_evaluated: "not-evaluated",
};

/** 凡例に状態を並べる順序。 */
const LEGEND_STATUS_ORDER: readonly CellStatus[] = ["catchable", "not_catchable", "not_evaluated"];

/** 上流の判定閾値を述べる凡例のキー。格子点の分類キーとは重ならない。 */
const THRESHOLD_FILL_KEY = "threshold";

/** 成立割合を表示する小数桁。 */
const RATIO_DIGITS = 3;

/** 指標を表示する小数桁。 */
const METRIC_DIGITS = 3;

/** 詳細文の項目の区切り。 */
const FIELD_SEPARATOR = " / ";

/** 入力に値が無いことを述べる語。値を補わずに、無いことをそのまま出す（要件 1.5）。 */
const ABSENT = "入力に無い";

/** 軸を 1 本も持たない掃引で `xAxis` に置く、値の無い軸。 */
const AXIS_WITHOUT_VALUES: AxisSpec = { name: "", unit: "", values: [] };

/** 軸の値の一覧に無いことを表す位置。 */
const NOT_ON_AXIS = -1;

/**
 * 色分けが表示上の取り決めであることを述べる（要件 2.9 / 3.6）。
 * 上流が出力していない判断を、この文言が作らないようにする。
 */
const DISPLAY_NOTE =
  "色分けと成立割合の帯は表示上の取り決めであり、成立の条件ではない。"
  + "状態は上流の status の値をそのまま用いており、表示側で判定を作っていない。"
  + "帯の境界は表示のための区切りであって、上流の catch_ratio_threshold とは別物である。";

// --- 帯と分類キー -----------------------------------------------------------

/**
 * 成立割合が入る帯を返す。
 * 下限を下回る値（および比較の成り立たない値）は最初の帯に置く。上流の値を捨てないためである。
 */
function bandOf(ratio: number): SuccessRatioBand | null {
  let selected: SuccessRatioBand | null = null;
  for (const band of SUCCESS_RATIO_BANDS) {
    if (ratio >= band.lowerBound) {
      selected = band;
    }
  }
  return selected ?? SUCCESS_RATIO_BANDS[0] ?? null;
}

/**
 * 格子点の分類キー。状態のキーと帯のキーの組み合わせであり、**色ではない**。
 * 成立割合が入力に無い格子点は状態のキーだけを持つ。
 */
function fillKeyOf(cell: CellResult): string {
  const statusKey = STATUS_FILL_KEYS[cell.status];
  if (cell.success_ratio === null) {
    return statusKey;
  }
  const band = bandOf(cell.success_ratio);
  return band === null ? statusKey : `${statusKey}-${band.key}`;
}

// --- 文言 -------------------------------------------------------------------

/** 上流の判定閾値と試行回数（要件 2.4 / 3.5）。凡例と `thresholdNote` で同じ文を使う。 */
function thresholdText(sweep: SweepSpec): string {
  const threshold = sweep.catch_ratio_threshold === null
    ? ABSENT
    : formatNumber(sweep.catch_ratio_threshold, RATIO_DIGITS);
  return `上流の判定閾値 catch_ratio_threshold: ${threshold}`
    + `${FIELD_SEPARATOR}試行回数 trials_per_cell: ${formatNumber(sweep.trials_per_cell, 0)}`;
}

/**
 * 格子点の詳細文（要件 2.6）。軸の値・状態・成立割合・指標を並べる。
 * 評価対象外の格子点はその理由も持つ（要件 2.5）。
 */
function tooltipOf(axes: readonly AxisSpec[], cell: CellResult): string {
  const parts: string[] = [];
  axes.forEach((axis, index) => {
    const value = cell.axis_values[index];
    const text = value === undefined ? ABSENT : formatAxisValue(value, axis.unit);
    parts.push(`${axis.name}: ${text}`);
  });
  parts.push(`状態: ${statusLabel(cell.status)}`);
  if (cell.not_evaluated_reason !== null) {
    parts.push(`評価対象外の理由: ${notEvaluatedReasonLabel(cell.not_evaluated_reason)}`);
  }
  const ratio = cell.success_ratio === null
    ? ABSENT
    : formatNumber(cell.success_ratio, RATIO_DIGITS);
  parts.push(`成立割合: ${ratio}`);
  const metrics = Object.entries(cell.metrics);
  if (metrics.length === 0) {
    parts.push(`指標: ${ABSENT}`);
  } else {
    for (const [key, value] of metrics) {
      parts.push(`${metricLabel(key)}: ${formatNumber(value, METRIC_DIGITS)}`);
    }
  }
  return parts.join(FIELD_SEPARATOR);
}

/**
 * 凡例（要件 2.4 / 2.9 / 3.5）。
 * 状態の系統・表示上の帯の系統・上流の判定閾値を、**別々の項目として**並べる。
 * 格子点の分類キーはこのうち状態のキーと帯のキーの組み合わせである。
 */
function buildLegend(sweep: SweepSpec): readonly LegendEntry[] {
  const entries: LegendEntry[] = [];
  for (const status of LEGEND_STATUS_ORDER) {
    entries.push({ fillKey: STATUS_FILL_KEYS[status], label: statusLabel(status) });
  }
  for (const band of SUCCESS_RATIO_BANDS) {
    entries.push({
      fillKey: band.key,
      label: `成立割合 ${band.boundsLabel}（表示上の帯の境界）`,
    });
  }
  entries.push({ fillKey: THRESHOLD_FILL_KEY, label: thresholdText(sweep) });
  return entries;
}

// --- 軸の選択 ---------------------------------------------------------------

/**
 * 軸番号から軸を取り出す。範囲外は**プログラムの誤り**として例外にする
 *（設計 Error Handling）。値へ丸めると、選択の誤りが図の誤りとして現れてしまう。
 */
function axisAt(axes: readonly AxisSpec[], index: number, label: string): AxisSpec {
  const axis = axes[index];
  if (axis === undefined) {
    throw new RangeError(
      `描画に用いる軸の番号が掃引の軸の範囲外である: ${label}=${String(index)}、軸の本数=${String(axes.length)}`,
    );
  }
  return axis;
}

/**
 * 掃引定義から既定の軸選択を作る。
 * 先頭の 2 軸を描画に使い、残りの軸はそれぞれ先頭の値で絞り込む。
 * 軸を 1 本も持たない掃引では描く軸が無く、`buildRegionPlan` が空のプランを返す。
 */
export function defaultSelection(sweep: SweepSpec): AxisSelection {
  const axes = sweep.axes;
  const yAxisIndex = axes.length >= 2 ? 1 : null;
  const fixed: { [axisName: string]: AxisValue } = {};
  axes.forEach((axis, index) => {
    if (index === 0 || index === yAxisIndex) {
      return;
    }
    const first = axis.values[0];
    if (first !== undefined) {
      fixed[axis.name] = first;
    }
  });
  return { xAxisIndex: 0, yAxisIndex, fixed };
}

/** 描画に使わない軸 1 本の絞り込み。 */
interface FixedConstraint {
  readonly axisIndex: number;
  readonly value: AxisValue;
}

/** 格子点が、描画に使わない軸すべての固定値と一致するか。 */
function matchesFixed(cell: CellResult, constraints: readonly FixedConstraint[]): boolean {
  for (const constraint of constraints) {
    if (cell.axis_values[constraint.axisIndex] !== constraint.value) {
      return false;
    }
  }
  return true;
}

/**
 * 格子点が軸のどの位置に置かれるかを、**軸の値の並び順**で求める（要件 2.1）。
 * 軸の値の一覧に無い値（および値を持たない格子点）は置き場所を持たない。
 */
function positionOf(axis: AxisSpec, cell: CellResult, axisIndex: number): number {
  const value = cell.axis_values[axisIndex];
  if (value === undefined) {
    return NOT_ON_AXIS;
  }
  return axis.values.indexOf(value);
}

function labelsOf(axis: AxisSpec): readonly string[] {
  return axis.values.map((value) => formatAxisValue(value, axis.unit));
}

// --- 入口 -------------------------------------------------------------------

/**
 * 掃引ビューと軸の選択からキャッチ可能領域のプランを組み立てる。
 *
 * 前提条件は `selection` の軸番号が `sweep.axes` の範囲内であることであり、
 * 破れば例外になる。ただし軸を 1 本も持たない掃引は入力側の事実であるため、
 * 軸番号を見る前に空のプランを返す。
 *
 * 事後条件として `cells` に含まれるのは、固定軸の値が一致する格子点のうち、
 * 描画に使う軸の値の一覧に位置を持つものだけである。
 * 不変条件として `yAxis === null` のとき全ての `RegionCell.row` は 0 である。
 */
export function buildRegionPlan(view: RegionSource, selection: AxisSelection): RegionPlan {
  const sweep = view.document.sweep;
  const axes = sweep.axes;
  const legend = buildLegend(sweep);
  const thresholdNote = thresholdText(sweep);

  if (axes.length === 0) {
    // 軸が無い掃引には格子が無い。描けるものが無いことを、値を作らずに表す。
    return {
      xAxis: AXIS_WITHOUT_VALUES,
      yAxis: null,
      columnLabels: [],
      rowLabels: [],
      cells: [],
      legend,
      fixedAxes: [],
      thresholdNote,
      displayNote: DISPLAY_NOTE,
    };
  }

  const xAxis = axisAt(axes, selection.xAxisIndex, "xAxisIndex");
  const yAxisIndex = selection.yAxisIndex;
  const yAxis = yAxisIndex === null ? null : axisAt(axes, yAxisIndex, "yAxisIndex");
  if (yAxisIndex !== null && yAxisIndex === selection.xAxisIndex) {
    throw new RangeError(
      `描画に用いる 2 軸が同じ軸を指している: xAxisIndex=${String(selection.xAxisIndex)}`,
    );
  }

  // 描画に使わない軸を固定値で絞り込む（要件 2.7）。
  // 選択に固定値が無い軸は先頭の値で絞り込む。値を持たない軸は絞り込みに使えない。
  const constraints: FixedConstraint[] = [];
  const fixedAxes: FixedAxisNote[] = [];
  axes.forEach((axis, index) => {
    if (index === selection.xAxisIndex || index === yAxisIndex) {
      return;
    }
    const chosen = Object.hasOwn(selection.fixed, axis.name)
      ? selection.fixed[axis.name]
      : axis.values[0];
    if (chosen === undefined) {
      return;
    }
    constraints.push({ axisIndex: index, value: chosen });
    fixedAxes.push({ axisName: axis.name, valueLabel: formatAxisValue(chosen, axis.unit) });
  });

  const cells: RegionCell[] = [];
  for (const cell of view.document.cells) {
    if (!matchesFixed(cell, constraints)) {
      continue;
    }
    const column = positionOf(xAxis, cell, selection.xAxisIndex);
    if (column === NOT_ON_AXIS) {
      continue;
    }
    const row = yAxis === null || yAxisIndex === null
      ? 0
      : positionOf(yAxis, cell, yAxisIndex);
    if (row === NOT_ON_AXIS) {
      continue;
    }
    // 格子点の間を補間しない。上流が評価した点だけを、そのまま 1 点として置く。
    cells.push({
      column,
      row,
      status: cell.status,
      fillKey: fillKeyOf(cell),
      tooltip: tooltipOf(axes, cell),
    });
  }

  return {
    xAxis,
    yAxis,
    columnLabels: labelsOf(xAxis),
    rowLabels: yAxis === null ? [] : labelsOf(yAxis),
    cells,
    legend,
    fixedAxes,
    thresholdNote,
    displayNote: DISPLAY_NOTE,
  };
}
