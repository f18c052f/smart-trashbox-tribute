// プランを SVG / DOM へ写す。描画の唯一の実装（要件 2.2, 2.6, 3.3, 4.4, 5.3、設計 `#### Renderer`）。
//
// このモジュールは**層 3** である。import してよいのは `schema` / `format` / `scale` / `plan/*`
// のみで、DOM に触れてよい 3 モジュールのうちの 1 つである（Dependency Direction）。
//
// 五つの規律をここで固定する。
//   1. **プランに無い判断をしない。** 位置・文字列・分類はすべて `plan/*` が決めている。
//      本モジュールは写すだけであり、`status` から色を選び直す・成立割合から判定するといった
//      ことをしない
//   2. **色を決めない。** `RegionCell.fillKey` / 凡例の `fillKey` を `classList` へそのまま
//      渡すだけであり、実際の色は `style.css` が持つ。対応表（`fillKey → 色`）は本モジュールに
//      置かない
//   3. **格子点の詳細は SVG 標準の `<title>` 子要素で与える。** 独自のツールチップ実装
//      （`mouseover` ハンドラでの浮動要素の生成など）を作らない（要件 2.6）
//   4. **HTML 文字列の組み立て（マークアップの直接代入）を行わない。** SVG 要素は
//      `host.ownerDocument.createElementNS` で生成し、
//      文字列は `textContent` へ代入する。入力文字列がそのまま画面に出るため、
//      マークアップとして解釈させない
//   5. **再生用の描画は要素を作ってから属性の更新のみを行う。** `createAnimationView` が
//      要素をすべて 1 度だけ作り、返す `showFrame` は毎フレーム属性を更新するだけで、
//      要素を生成しない（毎フレームの DOM 生成を避ける）
//
// `host.ownerDocument` を経由して要素を作るのは、グローバルな `document` を直接参照しないため
// である。これにより、`renderRegion` / `renderContext` などの各関数が渡された `host` の外側
// （兄弟パネルや `document.body`）へ到達する経路を持たない構造になる。較正バナーや除外要因の
// パネルが表示条件の変更で消えないこと（要件 6.3）は、各関数が自分の `host` だけを書き換え、
// 他のパネルの `host` に触れない構造そのものによって担保される。
//
// 座標変換は `scale.ts` の `linearMap`（軸の並び順・観測点の物理座標 → 画面のピクセル座標）と
// `padRange`（軌跡アニメーションの余白）だけを用いる。補間は `plan/animation.ts` が
// `frameAt` の中で済ませており、本モジュールは受け取った `FramePlan.head` を写すだけで
// 再補間しない（`lerp` はここで書かない。境界検査 B-7）。

import { linearMap, padRange, type Range } from "../scale.js";
import { formatWithUnit, invalidReasonLabel, provenanceLabel } from "../format.js";
import type { ContextPlan, ParameterRow } from "../plan/context.js";
import type { LegendEntry, RegionPlan } from "../plan/region.js";
import type {
  AnimationPlan,
  FramePlan,
  Point2D,
  PredictionMarker,
} from "../plan/animation.js";

// --- 公開する契約 -------------------------------------------------------------

/**
 * `LoadIssue` のうち本モジュールが表示する項目（`path` / `detail`）だけを構造的に宣言する。
 * 層 3（`view/render.ts`）は層 1（`load.ts`）を import できないため（Dependency Direction）、
 * `LoadIssue` そのものではなくこの最小構造を受け取る。実際の呼び出しでは `LoadIssue` を
 * 渡してよい（構造的部分型により代入可能。`plan/region.ts` の `RegionSource` と同じ理由）。
 */
export interface RenderableIssue {
  readonly path: string;
  readonly detail: string;
}

/** 軌跡アニメーション 1 面分の再生ハンドル。`showFrame` は属性の更新のみを行う。 */
export interface AnimationView {
  readonly showFrame: (frame: FramePlan) => void;
}

// --- SVG 名前空間・レイアウト定数 ---------------------------------------------
//
// 数値はすべて表示上のレイアウト定数であり、上流の値ではない。座標そのものの変換は
// `linearMap` / `padRange` に委ねる（B-5: 算術は min/max/abs/round/floor/ceil のみ）。

const SVG_NS = "http://www.w3.org/2000/svg";

const CELL_SIZE = 32;
const GRID_MARGIN_LEFT = 160;
const GRID_MARGIN_TOP = 28;
const GRID_MARGIN_RIGHT = 260;
const GRID_MARGIN_BOTTOM = 40;
const LEGEND_SWATCH_SIZE = 14;
const LEGEND_ROW_HEIGHT = 20;
const LEGEND_GAP = 12;
const AXIS_LABEL_OFFSET = 8;
const AXIS_NAME_OFFSET = 16;
const Y_AXIS_NAME_X = 12;

const TRACK_WIDTH = 480;
const TRACK_HEIGHT = 320;
const TRACK_MARGIN = 24;
const TRACK_PAD_RATIO = 0.12;
const TRACK_HEAD_RADIUS = 6;
const TRACK_PREDICTION_RADIUS = 4;

const PROVENANCE_ABSENT_LABEL = "出所の記載なし";
const LOAD_FAILURE_HEADING = "読み込みに失敗した項目";

// --- DOM 生成の小さな補助 ------------------------------------------------------
//
// `doc` は常に `host.ownerDocument` から取る。グローバルな `document` を参照しない。

function svgElement(doc: Document, tag: string): Element {
  return doc.createElementNS(SVG_NS, tag);
}

// 呼び出し側は空白区切りの複合クラス名（例: "region-axis-label region-axis-label--column"）を
// 1 引数として渡してくる。実 DOM の `DOMTokenList.add` は各引数を単一トークンとして扱い、
// 空白を含む引数を渡すと `InvalidCharacterError` を投げるため、空白で分解してから渡す
// （空文字列トークンは渡さない）。
function addClassNames(el: Element, className: string): void {
  const tokens = className.split(/\s+/).filter((token) => token.length > 0);
  el.classList.add(...tokens);
}

function svgText(doc: Document, className: string, x: number, y: number, text: string): Element {
  const el = svgElement(doc, "text");
  el.setAttribute("x", String(x));
  el.setAttribute("y", String(y));
  addClassNames(el, className);
  el.textContent = text;
  return el;
}

function htmlText(doc: Document, tag: string, className: string, text: string): Element {
  const el = doc.createElement(tag);
  addClassNames(el, className);
  el.textContent = text;
  return el;
}

// --- キャッチ可能領域の図（要件 2.1〜2.9 / 3.5） -------------------------------

/** 序数の並び（0..count-1）を表す値域。単一列・単一行では幅 0 になる。 */
function ordinalRange(count: number): Range {
  return { min: 0, max: Math.max(count - 1, 0) };
}

/** 序数をセル中心のピクセル位置へ写すための出力域。`linearMap` に渡すだけで済む形にする。 */
function ordinalPixelRange(margin: number, count: number): Range {
  const span = Math.max(count - 1, 0) * CELL_SIZE;
  return { min: margin + CELL_SIZE / 2, max: margin + CELL_SIZE / 2 + span };
}

function buildGridSvg(doc: Document, plan: RegionPlan): Element {
  const columnCount = Math.max(plan.columnLabels.length, 1);
  const rowCount = Math.max(plan.rowLabels.length, 1);
  const width = GRID_MARGIN_LEFT + columnCount * CELL_SIZE + GRID_MARGIN_RIGHT;
  const height = GRID_MARGIN_TOP + rowCount * CELL_SIZE + GRID_MARGIN_BOTTOM;

  const columnDomain = ordinalRange(columnCount);
  const rowDomain = ordinalRange(rowCount);
  const columnPixels = ordinalPixelRange(GRID_MARGIN_LEFT, columnCount);
  const rowPixels = ordinalPixelRange(GRID_MARGIN_TOP, rowCount);

  const svg = svgElement(doc, "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("width", String(width));
  svg.setAttribute("height", String(height));
  svg.classList.add("region-figure");

  // 列ラベル（要件 2.3）。
  plan.columnLabels.forEach((label, index) => {
    const x = linearMap(index, columnDomain, columnPixels);
    svg.appendChild(
      svgText(doc, "region-axis-label region-axis-label--column", x, GRID_MARGIN_TOP - AXIS_LABEL_OFFSET, label),
    );
  });

  // 行ラベル（要件 2.3）。1 軸掃引では空であり、何も描かれない。
  plan.rowLabels.forEach((label, index) => {
    const y = linearMap(index, rowDomain, rowPixels);
    svg.appendChild(
      svgText(doc, "region-axis-label region-axis-label--row", GRID_MARGIN_LEFT - AXIS_LABEL_OFFSET, y, label),
    );
  });

  // 軸名（要件 2.3）。1 軸掃引では `yAxis` が無いため描かない。
  svg.appendChild(
    svgText(
      doc,
      "region-axis-name region-axis-name--x",
      GRID_MARGIN_LEFT + (columnCount * CELL_SIZE) / 2,
      height - GRID_MARGIN_BOTTOM + AXIS_NAME_OFFSET,
      `${plan.xAxis.name} [${plan.xAxis.unit}]`,
    ),
  );
  if (plan.yAxis !== null) {
    svg.appendChild(
      svgText(
        doc,
        "region-axis-name region-axis-name--y",
        Y_AXIS_NAME_X,
        GRID_MARGIN_TOP + (rowCount * CELL_SIZE) / 2,
        `${plan.yAxis.name} [${plan.yAxis.unit}]`,
      ),
    );
  }

  // 格子点（要件 2.1 / 2.2 / 2.5 / 2.6）。位置は序数の並びから決まり、値へは線形写像しない
  // （`plan/region.ts` が既に並び順で `column` / `row` を決めているため、ここでは
  // その序数をピクセルへ写すだけである）。
  for (const cell of plan.cells) {
    const cx = linearMap(cell.column, columnDomain, columnPixels);
    const cy = linearMap(cell.row, rowDomain, rowPixels);
    const rect = svgElement(doc, "rect");
    rect.setAttribute("x", String(cx - CELL_SIZE / 2));
    rect.setAttribute("y", String(cy - CELL_SIZE / 2));
    rect.setAttribute("width", String(CELL_SIZE));
    rect.setAttribute("height", String(CELL_SIZE));
    // fillKey をそのままクラス名へ写すだけであり、色や意味の翻訳をここに置かない。
    rect.classList.add("region-cell", cell.fillKey);
    const title = svgElement(doc, "title");
    title.textContent = cell.tooltip;
    rect.appendChild(title);
    svg.appendChild(rect);
  }

  // 凡例（要件 2.4 / 2.9 / 3.5）。
  const legendX = GRID_MARGIN_LEFT + columnCount * CELL_SIZE + LEGEND_GAP;
  plan.legend.forEach((entry: LegendEntry, index) => {
    const y = GRID_MARGIN_TOP + index * LEGEND_ROW_HEIGHT;
    const swatch = svgElement(doc, "rect");
    swatch.setAttribute("x", String(legendX));
    swatch.setAttribute("y", String(y));
    swatch.setAttribute("width", String(LEGEND_SWATCH_SIZE));
    swatch.setAttribute("height", String(LEGEND_SWATCH_SIZE));
    swatch.classList.add("region-legend-swatch", entry.fillKey);
    svg.appendChild(swatch);
    svg.appendChild(
      svgText(
        doc,
        "region-legend-label",
        legendX + LEGEND_SWATCH_SIZE + AXIS_LABEL_OFFSET,
        y + LEGEND_SWATCH_SIZE - 2,
        entry.label,
      ),
    );
  });

  return svg;
}

export function renderRegion(host: Element, plan: RegionPlan): void {
  const doc = host.ownerDocument;
  const svg = buildGridSvg(doc, plan);

  const notes = doc.createElement("div");
  notes.classList.add("region-notes");

  if (plan.fixedAxes.length > 0) {
    const fixedList = doc.createElement("ul");
    fixedList.classList.add("region-fixed-axes");
    for (const note of plan.fixedAxes) {
      const item = doc.createElement("li");
      item.textContent = `${note.axisName}: ${note.valueLabel}`;
      fixedList.appendChild(item);
    }
    notes.appendChild(fixedList);
  }

  notes.appendChild(htmlText(doc, "p", "region-threshold-note", plan.thresholdNote));
  notes.appendChild(htmlText(doc, "p", "region-display-note", plan.displayNote));

  host.replaceChildren(svg, notes);
}

// --- 前提・限界・同一性の提示（要件 3.1〜3.5 / 4.1〜4.4） -----------------------

function parameterRowElement(doc: Document, row: ParameterRow): Element {
  const tr = doc.createElement("tr");
  const pathCell = doc.createElement("td");
  pathCell.classList.add("context-parameter-path");
  pathCell.textContent = row.path;
  const valueCell = doc.createElement("td");
  valueCell.classList.add("context-parameter-value");
  valueCell.textContent = row.value;
  const provenanceCell = doc.createElement("td");
  provenanceCell.classList.add("context-parameter-provenance");
  // 出所の記載が無い行は「出所の記載なし」と表示し、「想定」で埋めない
  // （設計 ContextPlanner の Implementation Notes）。
  provenanceCell.textContent = row.provenance === null
    ? PROVENANCE_ABSENT_LABEL
    : provenanceLabel(row.provenance);
  tr.appendChild(pathCell);
  tr.appendChild(valueCell);
  tr.appendChild(provenanceCell);
  return tr;
}

function parameterTable(doc: Document, className: string, rows: readonly ParameterRow[]): Element {
  const table = doc.createElement("table");
  addClassNames(table, className);
  for (const row of rows) {
    table.appendChild(parameterRowElement(doc, row));
  }
  return table;
}

export function renderContext(host: Element, plan: ContextPlan): void {
  const doc = host.ownerDocument;
  const container = doc.createElement("div");
  container.classList.add("context-panel");

  // 較正バナー（要件 3.1 / 3.2）。常時表示のバナーとして、常にこの host の先頭に置く。
  const banner = doc.createElement("div");
  banner.classList.add("context-calibration-banner");
  banner.appendChild(htmlText(doc, "span", "context-calibration-stage", plan.calibrationStageLabel));
  if (plan.calibrationNotice !== null) {
    banner.appendChild(htmlText(doc, "p", "context-calibration-notice", plan.calibrationNotice));
  }
  container.appendChild(banner);

  if (plan.warnings.length > 0) {
    const warningList = doc.createElement("ul");
    warningList.classList.add("context-warnings");
    for (const warning of plan.warnings) {
      const item = doc.createElement("li");
      item.textContent = warning;
      warningList.appendChild(item);
    }
    container.appendChild(warningList);
  }

  // 同一性（要件 1.6 / 4.1 / 4.3 / 4.4）。
  const identity = doc.createElement("dl");
  identity.classList.add("context-identity");
  for (const row of plan.identity) {
    identity.appendChild(htmlText(doc, "dt", "context-identity-label", row.label));
    identity.appendChild(htmlText(doc, "dd", "context-identity-value", row.value));
  }
  container.appendChild(identity);

  // モデル除外要因（要件 3.3）。段ごとに全項目を並べる。折り畳まない。
  const exclusions = doc.createElement("div");
  exclusions.classList.add("context-exclusions");
  for (const group of plan.exclusions) {
    const groupEl = doc.createElement("div");
    groupEl.classList.add("context-exclusion-group");
    groupEl.appendChild(htmlText(doc, "h4", "context-exclusion-stage", group.stage));
    const list = doc.createElement("ul");
    list.classList.add("context-exclusion-items");
    for (const item of group.items) {
      const li = doc.createElement("li");
      li.textContent = item;
      list.appendChild(li);
    }
    groupEl.appendChild(list);
    exclusions.appendChild(groupEl);
  }
  container.appendChild(exclusions);

  // 機体パラメータ（要件 4.2）と全パラメータ（要件 3.4 / 4.1）。
  container.appendChild(parameterTable(doc, "context-drivetrain", plan.drivetrainRows));
  container.appendChild(parameterTable(doc, "context-parameters", plan.parameters));

  host.replaceChildren(container);
}

// --- 読み込み失敗（要件 1.2 / 1.3） --------------------------------------------

export function renderLoadFailure(host: Element, issues: readonly RenderableIssue[]): void {
  const doc = host.ownerDocument;
  const container = doc.createElement("div");
  container.classList.add("load-failure");
  container.appendChild(htmlText(doc, "p", "load-failure-heading", LOAD_FAILURE_HEADING));

  const list = doc.createElement("ul");
  list.classList.add("load-failure-issues");
  for (const issue of issues) {
    const item = doc.createElement("li");
    item.classList.add("load-failure-issue");
    // 場所と内容をどちらも省略せずに列挙する（要件 1.2）。場所の無い問題（ルート直下の
    // 解釈失敗）は内容だけを示す。
    item.textContent = issue.path === "" ? issue.detail : `${issue.path}: ${issue.detail}`;
    list.appendChild(item);
  }
  container.appendChild(list);

  // host を丸ごと置き換えるため、直前に描かれていた図（renderRegion の出力を含む）は
  // 残らない（要件 1.3、設計 Renderer の Postconditions）。
  host.replaceChildren(container);
}

// --- 軌跡アニメーション（要件 5.1〜5.5 / 5.8） ---------------------------------

interface PixelPoint {
  readonly x: number;
  readonly y: number;
}

interface TrackProjection {
  readonly uDomain: Range;
  readonly vDomain: Range;
  readonly uPixel: Range;
  readonly vPixel: Range;
}

function buildTrackProjection(plan: AnimationPlan): TrackProjection {
  return {
    uDomain: padRange(plan.uRange, TRACK_PAD_RATIO),
    vDomain: padRange(plan.vRange, TRACK_PAD_RATIO),
    uPixel: { min: TRACK_MARGIN, max: TRACK_WIDTH - TRACK_MARGIN },
    // 画面座標は下方向が正のため、v の値域を反転して写す（v が大きいほど上に描く）。
    vPixel: { min: TRACK_HEIGHT - TRACK_MARGIN, max: TRACK_MARGIN },
  };
}

function toPixel(point: Point2D, projection: TrackProjection): PixelPoint {
  return {
    x: linearMap(point.u, projection.uDomain, projection.uPixel),
    y: linearMap(point.v, projection.vDomain, projection.vPixel),
  };
}

function pointsAttribute(points: readonly PixelPoint[]): string {
  return points.map((point) => `${point.x},${point.y}`).join(" ");
}

const PREDICTION_DETAIL_SEPARATOR = " / ";

/** 予測 1 件の一覧行の文言。上流の語は `format.ts` の `invalidReasonLabel` を通す。 */
function predictionRowText(prediction: PredictionMarker): string {
  const parts: string[] = [
    `#${prediction.index}`,
    `sample_count: ${prediction.sampleCount}`,
  ];
  if (prediction.basedOnTimeMs !== null) {
    parts.push(`based_on_time_ms: ${prediction.basedOnTimeMs}`);
  }
  if (prediction.invalidReason !== null) {
    parts.push(`理由: ${invalidReasonLabel(prediction.invalidReason)}`);
  }
  if (prediction.detail !== null) {
    parts.push(prediction.detail);
  }
  return parts.join(PREDICTION_DETAIL_SEPARATOR);
}

export function createAnimationView(host: Element, plan: AnimationPlan): AnimationView {
  const doc = host.ownerDocument;
  const projection = buildTrackProjection(plan);

  const svg = svgElement(doc, "svg");
  svg.setAttribute("viewBox", `0 0 ${TRACK_WIDTH} ${TRACK_HEIGHT}`);
  svg.setAttribute("width", String(TRACK_WIDTH));
  svg.setAttribute("height", String(TRACK_HEIGHT));
  svg.classList.add("track-figure");

  // 全観測点の投影（要件 5.1 / 5.7）。1 度だけ計算し、以降はこの配列を読むだけにする。
  const fullPathPixels = plan.path.map((point) => toPixel(point, projection));

  const pathLine = svgElement(doc, "polyline");
  pathLine.classList.add("track-path");
  pathLine.setAttribute("points", pointsAttribute(fullPathPixels));
  svg.appendChild(pathLine);

  // 再生中に「ここまで見えている」区間を示す線。`points` 属性だけを毎フレーム更新する。
  const traversedLine = svgElement(doc, "polyline");
  traversedLine.classList.add("track-path-traversed");
  svg.appendChild(traversedLine);

  // 予測マーカー（要件 5.2 / 5.4）。有効な予測（`hit` を持つもの）だけを図に置く。
  // 無効な予測は座標を持たないため、図には描かず一覧にのみ現れる。
  const predictionsGroup = svgElement(doc, "g");
  predictionsGroup.classList.add("track-predictions");
  const predictionMarkers = new Map<number, Element>();
  for (const prediction of plan.predictions) {
    if (prediction.hit === null) {
      continue;
    }
    const pixel = toPixel(prediction.hit, projection);
    const marker = svgElement(doc, "circle");
    marker.classList.add("track-prediction");
    marker.setAttribute("cx", String(pixel.x));
    marker.setAttribute("cy", String(pixel.y));
    marker.setAttribute("r", String(TRACK_PREDICTION_RADIUS));
    if (prediction.detail !== null) {
      const title = svgElement(doc, "title");
      title.textContent = prediction.detail;
      marker.appendChild(title);
    }
    predictionsGroup.appendChild(marker);
    predictionMarkers.set(prediction.index, marker);
  }
  svg.appendChild(predictionsGroup);

  // 現在位置（要件 5.6）。`frameAt` が既に線形補間した `head` を写すだけで、再補間しない。
  const head = svgElement(doc, "circle");
  head.classList.add("track-head");
  head.setAttribute("r", String(TRACK_HEAD_RADIUS));
  head.setAttribute("cx", String(projection.uPixel.min));
  head.setAttribute("cy", String(projection.vPixel.min));
  svg.appendChild(head);

  // 現在の再生時刻（要件 5.3）。
  const timeText = svgElement(doc, "text");
  timeText.classList.add("track-time");
  timeText.setAttribute("x", String(TRACK_MARGIN));
  timeText.setAttribute("y", String(TRACK_MARGIN));
  svg.appendChild(timeText);

  // 予測の一覧（要件 5.2 / 5.4）。無効な予測もここに理由付きで現れる。
  const detailList = doc.createElement("ul");
  detailList.classList.add("track-prediction-list");
  const predictionRows = new Map<number, Element>();
  for (const prediction of plan.predictions) {
    const item = doc.createElement("li");
    item.classList.add("track-prediction-row");
    item.textContent = predictionRowText(prediction);
    detailList.appendChild(item);
    predictionRows.set(prediction.index, item);
  }

  const container = doc.createElement("div");
  container.classList.add("track-view");
  container.appendChild(svg);
  container.appendChild(detailList);
  host.replaceChildren(container);

  function showFrame(frame: FramePlan): void {
    // ここまで見えている区間だけを結ぶ。要素は作らず、`points` 属性のみを差し替える。
    traversedLine.setAttribute("points", pointsAttribute(fullPathPixels.slice(0, frame.visibleCount)));

    if (frame.head === null) {
      head.classList.add("track-head--hidden");
    } else {
      head.classList.remove("track-head--hidden");
      const pixel = toPixel(frame.head, projection);
      head.setAttribute("cx", String(pixel.x));
      head.setAttribute("cy", String(pixel.y));
    }

    timeText.textContent = formatWithUnit(frame.timeMs, "ms");

    const activeIndex = frame.activePrediction === null ? null : frame.activePrediction.index;
    for (const [index, marker] of predictionMarkers) {
      if (index === activeIndex) {
        marker.classList.add("track-prediction--active");
      } else {
        marker.classList.remove("track-prediction--active");
      }
    }
    for (const [index, row] of predictionRows) {
      if (index === activeIndex) {
        row.classList.add("track-prediction-row--active");
      } else {
        row.classList.remove("track-prediction-row--active");
      }
    }
  }

  return { showFrame };
}

export function renderAnimationUnavailable(host: Element, reason: string): void {
  const doc = host.ownerDocument;
  const message = htmlText(doc, "p", "track-unavailable", reason);
  host.replaceChildren(message);
}
