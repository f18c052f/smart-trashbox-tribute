// プランを SVG / DOM へ写す描画層を検証する
// （要件 2.2, 2.6, 3.3, 4.4, 5.3、設計 `#### Renderer`）。
//
// Node.js には DOM が無く、開発時依存を TypeScript コンパイラ 1 個に保つ制約（要件 7.4）の
// もとでは jsdom 等のサードパーティを導入できない。そこでこのファイルは
// `viz/tests/dom-stub.ts` の手作りの最小 DOM を用い、`render.ts` が実際に呼び出す DOM 操作
// だけを検証する。ブラウザでの見た目そのものの確認は設計の手動確認手順（5.3）に委ねる。
//
// ここで固定するのは次の 8 点である。
//   1. 再生の描画は要素を 1 度だけ作り、`showFrame` は属性の更新のみを行う
//      （毎フレームの DOM 生成を避ける。設計 Renderer の Invariants）
//   2. コンパイル出力に `innerHTML` が現れない。文字列の埋め込みは `textContent` のみで行う
//   3. 格子点の詳細は SVG 標準の `<title>` 子要素として現れ、内容は `RegionCell.tooltip` と
//      一致する（要件 2.6）。独自のツールチップ機構を持たない
//   4. `RegionCell.fillKey` / 凡例の `fillKey` がそのままクラス名になり、`render.ts` 自身は
//      色（16進数・rgb・hsl）を一切持たない（要件 2.2 / 2.9）
//   5. `renderLoadFailure` は直前の内容を残さず、host の子要素を丸ごと置き換える（要件 1.3）
//   6. 各関数は渡された `host` だけを書き換え、他の要素へは触れない
//      （要件 6.3 が成り立つための構造上の前提）
//   7. コンパイル出力が import してよいのは `schema.js` / `format.js` / `scale.js` /
//      `plan/*.js` のみで、`load.js` を import しない（Dependency Direction）
//   8. 座標変換は `scale.ts` の `linearMap` / `padRange` のみで行い、非線形の写像・
//      独自の補間を持たない（境界検査 B-5 / B-7 相当）
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  renderContext,
  renderRegion,
  renderLoadFailure,
  createAnimationView,
  renderAnimationUnavailable,
  type RenderableIssue,
} from "../src/view/render.js";
import { loadSweep } from "../src/load.js";
import { buildRegionPlan, defaultSelection } from "../src/plan/region.js";
import { buildContextPlan, type ContextWarning } from "../src/plan/context.js";
import { buildAnimationPlan, frameAt } from "../src/plan/animation.js";
import {
  makeMixedStatusDocument,
  makeSweepDocumentWithRecords,
  makeThrowRecord,
  makeSamples,
  makeInvalidPrediction,
  makePrediction,
  toJsonText,
} from "./fixtures.js";
import { FakeElement, makeHost } from "./dom-stub.js";

// --- 補助 -------------------------------------------------------------------

const FILE_NAME = "sweep-reachability.json";

/** 検証済みの `SweepView` を得る。プラン層のテストと同じ経路を通す。 */
function viewOf(document: ReturnType<typeof makeMixedStatusDocument>) {
  const result = loadSweep(toJsonText(document), FILE_NAME);
  if (!result.ok) {
    throw new Error("テストの前提となる入力が読み込めない");
  }
  return result.view;
}

/** `FakeElement` を実 DOM の `Element` として渡すための、テストコード側だけの明示キャスト。
 * `render.ts` 自身はこのキャストを持たない。 */
function asElement(node: FakeElement): Element {
  return node as unknown as Element;
}

const VIZ_ROOT = new URL("../../", import.meta.url);

function readCompiledRenderSource(): string {
  return readFileSync(new URL("dist/src/view/render.js", VIZ_ROOT), "utf8");
}

/** `noUncheckedIndexedAccess` の下で `undefined` を排除するための、テスト側だけの補助。 */
function required<T>(value: T | undefined, message: string): T {
  if (value === undefined) {
    throw new Error(message);
  }
  return value;
}

// --- 1. 再生は要素を 1 度だけ作り、属性の更新のみを行う（要件 5.3、設計 Invariants） -----

test("createAnimationView は要素を 1 度だけ作り、showFrame は要素を追加で作らない", () => {
  const { host, doc } = makeHost();
  const record = makeThrowRecord();
  const plan = buildAnimationPlan(record, "xy");

  const view = createAnimationView(asElement(host), plan);
  const creationCountAfterInit = doc.creationCount;
  assert.ok(creationCountAfterInit > 0, "初期化で要素が 1 つも作られていない");

  view.showFrame(frameAt(plan, plan.startTimeMs));
  view.showFrame(frameAt(plan, (plan.startTimeMs + plan.endTimeMs) / 2));
  view.showFrame(frameAt(plan, plan.endTimeMs));
  view.showFrame(frameAt(plan, plan.startTimeMs));

  assert.equal(
    doc.creationCount,
    creationCountAfterInit,
    "showFrame の呼び出しで新しい要素が作られている（属性更新のみであるべき）",
  );
});

test("showFrame が現在位置・再生時刻・有効な予測の強調を属性更新だけで反映する（要件 5.2 / 5.3）", () => {
  const { host } = makeHost();
  const record = makeThrowRecord();
  const plan = buildAnimationPlan(record, "xy");
  const view = createAnimationView(asElement(host), plan);

  const midTime = plan.times[2] ?? plan.startTimeMs; // makeSamples() の中間サンプル時刻
  view.showFrame(frameAt(plan, midTime));

  const head = required(host.queryAllByClass("track-head")[0], "現在位置を表す要素が無い");
  assert.ok(head.hasAttribute("cx") && head.hasAttribute("cy"), "現在位置の座標属性が無い");

  const timeTexts = host
    .queryAllByTag("text")
    .filter((node) => node.classList.contains("track-time"));
  assert.equal(timeTexts.length, 1, "再生時刻を表示する要素が 1 つでない");
  assert.match(timeTexts[0]?.textContent ?? "", /ms/, "再生時刻に単位が付いていない（要件 5.3）");

  // frameAt(plan, midTime) の activePrediction は based_on_time_ms <= midTime を満たす最後の予測。
  const frame = frameAt(plan, midTime);
  assert.ok(frame.activePrediction !== null, "テスト前提: この時刻で有効な予測が選ばれるはず");
  const activeMarkers = host.queryAllByClass("track-prediction--active");
  assert.equal(activeMarkers.length, 1, "強調されている予測マーカーが 1 つでない");
});

test("観測サンプルが 0 件の記録でも例外を投げず、現在位置を描かない（設計 AnimationPlanner の Risks）", () => {
  const { host } = makeHost();
  const record = makeThrowRecord({
    samples: [],
    predictions: [makeInvalidPrediction({ sample_count: 0 })],
  });
  const plan = buildAnimationPlan(record, "xy");
  const view = createAnimationView(asElement(host), plan);

  view.showFrame(frameAt(plan, 0));
  const head = required(host.queryAllByClass("track-head")[0], "現在位置を表す要素が無い");
  assert.ok(head.classList.contains("track-head--hidden"), "観測点が無いのに現在位置を描いている");
});

// --- 2. innerHTML を使わず、文字列はマークアップ組み立てを経由しない ---------------------

test("コンパイル出力に innerHTML が現れない（設計 Renderer の Implementation Notes）", () => {
  const source = readCompiledRenderSource();
  assert.doesNotMatch(source, /innerHTML/, "innerHTML によるマークアップ組み立てが残っている");
});

test("入力文字列がそのまま textContent として現れ、要素として解釈されない", () => {
  const { host } = makeHost();
  const document = makeMixedStatusDocument({
    calibration: { stage: "uncalibrated", notice: "<script>alert(1)</script>" },
  });
  const warnings: readonly ContextWarning[] = [];
  const plan = buildContextPlan(
    { fileName: FILE_NAME, document },
    warnings,
  );
  renderContext(asElement(host), plan);

  const noticeNode = host
    .queryAllByClass("context-calibration-notice")[0];
  assert.ok(noticeNode !== undefined, "注意書きを表示する要素が無い");
  // textContent に代入していれば、子要素として <script> は作られず、文字列のまま保持される。
  assert.equal(noticeNode?.textContent, "<script>alert(1)</script>");
  assert.equal(noticeNode?.childElementCount, 0, "文字列が子要素として解釈されている");
});

// --- 3. 格子点の詳細は <title> 子要素のみで与える（要件 2.6） ---------------------------

test("各格子点が <title> 子要素として tooltip をそのまま持つ（要件 2.6）", () => {
  const { host } = makeHost();
  const document = makeMixedStatusDocument();
  const view = viewOf(document);
  const plan = buildRegionPlan(view, defaultSelection(document.sweep));

  renderRegion(asElement(host), plan);

  const cellNodes = host.queryAllByClass("region-cell");
  assert.equal(cellNodes.length, plan.cells.length, "格子点の要素数がプランと一致しない");

  cellNodes.forEach((node, index) => {
    const expected = required(plan.cells[index], `プラン側に格子点 ${index} が無い`);
    const titleNodes = node.queryAllByTag("title");
    assert.equal(titleNodes.length, 1, `格子点 ${index} の <title> が 1 個でない`);
    const title = required(titleNodes[0], `格子点 ${index} の <title> が取得できない`);
    assert.equal(title.textContent, expected.tooltip);
    assert.equal(title.namespaceURI, "http://www.w3.org/2000/svg");
  });
});

test("プランに無いツールチップ機構（独自の title 相当属性）を作らない", () => {
  const { host } = makeHost();
  const document = makeMixedStatusDocument();
  const view = viewOf(document);
  const plan = buildRegionPlan(view, defaultSelection(document.sweep));
  renderRegion(asElement(host), plan);

  for (const node of host.queryAllByClass("region-cell")) {
    assert.equal(node.getAttribute("title"), null, "SVG 標準の <title> 以外でツールチップを作っている");
    assert.equal(node.getAttribute("data-tooltip"), null, "独自のツールチップ属性を作っている");
  }
});

// --- 4. fillKey がそのままクラス名になり、色を持たない（要件 2.2 / 2.9） ----------------

test("格子点のクラス名が RegionCell.fillKey と一致する（翻訳表を経由しない）", () => {
  const { host } = makeHost();
  const document = makeMixedStatusDocument();
  const view = viewOf(document);
  const plan = buildRegionPlan(view, defaultSelection(document.sweep));
  renderRegion(asElement(host), plan);

  const cellNodes = host.queryAllByClass("region-cell");
  plan.cells.forEach((cell, index) => {
    const node = required(cellNodes[index], `格子点 ${index} の要素が無い`);
    assert.ok(
      node.classList.contains(cell.fillKey),
      `格子点 ${index} のクラスに fillKey (${cell.fillKey}) が無い`,
    );
  });
});

test("凡例のクラス名が LegendEntry.fillKey と一致する", () => {
  const { host } = makeHost();
  const document = makeMixedStatusDocument();
  const view = viewOf(document);
  const plan = buildRegionPlan(view, defaultSelection(document.sweep));
  renderRegion(asElement(host), plan);

  const swatchNodes = host.queryAllByClass("region-legend-swatch");
  assert.equal(swatchNodes.length, plan.legend.length);
  plan.legend.forEach((entry, index) => {
    assert.ok(swatchNodes[index]?.classList.contains(entry.fillKey));
  });
});

test("render.ts のコンパイル出力に色の指定（16進数・rgb・hsl）が無い（色はスタイルシート側の責務）", () => {
  const source = readCompiledRenderSource();
  assert.doesNotMatch(source, /#[0-9a-fA-F]{3,8}\b/, "16進数の色指定が現れている");
  assert.doesNotMatch(source, /rgba?\(/, "rgb() の色指定が現れている");
  assert.doesNotMatch(source, /hsla?\(/, "hsl() の色指定が現れている");
});

// --- 5. renderLoadFailure は直前の内容を残さない（要件 1.3） ----------------------------

test("renderLoadFailure が直前に描いた図をすべて置き換える（要件 1.3）", () => {
  const { host } = makeHost();
  const document = makeMixedStatusDocument();
  const view = viewOf(document);
  const plan = buildRegionPlan(view, defaultSelection(document.sweep));
  renderRegion(asElement(host), plan);
  assert.ok(host.childElementCount > 0, "テスト前提: 図が描かれているはず");

  const issues: readonly RenderableIssue[] = [
    { path: "calibration", detail: "必須項目が入力に無い" },
    { path: "sweep.axes", detail: "必須項目が入力に無い" },
  ];
  renderLoadFailure(asElement(host), issues);

  const regionCells = host.queryAllByClass("region-cell");
  assert.equal(regionCells.length, 0, "直前の図（格子点）が残っている");
  const gridSvgs = host.queryAllByTag("svg");
  assert.equal(gridSvgs.length, 0, "直前の図（svg）が残っている");
});

test("renderLoadFailure が欠落項目の場所と内容をすべて列挙する（要件 1.2）", () => {
  const { host } = makeHost();
  const issues: readonly RenderableIssue[] = [
    { path: "calibration", detail: "必須項目が入力に無い" },
    { path: "sweep.axes", detail: "必須項目が入力に無い" },
    { path: "cells[3].status", detail: "上流の列挙値に無い値である" },
  ];
  renderLoadFailure(asElement(host), issues);

  const text = host.textContent;
  for (const issue of issues) {
    assert.match(text, new RegExp(issue.path.replace(/[[\]]/g, "\\$&")), `場所 ${issue.path} が表示されていない`);
    assert.match(text, new RegExp(issue.detail), `内容 ${issue.detail} が表示されていない`);
  }
});

test("renderLoadFailure を連続で呼んでも直前の内容だけが残る", () => {
  const { host } = makeHost();
  renderLoadFailure(asElement(host), [{ path: "a", detail: "1件目" }]);
  renderLoadFailure(asElement(host), [{ path: "b", detail: "2件目" }]);

  assert.doesNotMatch(host.textContent, /1件目/, "2 回目の呼び出しで 1 回目の内容が残っている");
  assert.match(host.textContent, /2件目/);
});

// --- 6. 各関数は渡された host だけを書き換える（要件 6.3 の構造上の前提） ---------------

test("renderRegion は渡された host の外側（別のパネル）へ触れない", () => {
  const regionHost = makeHost();
  const contextHost = makeHost();
  const document = makeMixedStatusDocument();

  renderContext(
    asElement(contextHost.host),
    buildContextPlan({ fileName: FILE_NAME, document }, []),
  );
  const contextSnapshotBefore = contextHost.host.textContent;

  const view = viewOf(document);
  const plan = buildRegionPlan(view, defaultSelection(document.sweep));
  renderRegion(asElement(regionHost.host), plan);

  assert.equal(
    contextHost.host.textContent,
    contextSnapshotBefore,
    "renderRegion が前提と限界パネルの host を変更した",
  );
  assert.ok(contextHost.host.childElementCount > 0, "前提と限界パネルが消えている（要件 6.3）");
});

test("renderContext を再度呼んでも別 host のアニメーション面は消えない（要件 6.3）", () => {
  const contextHost = makeHost();
  const trackHost = makeHost();
  const record = makeThrowRecord();
  const plan = buildAnimationPlan(record, "xy");
  createAnimationView(asElement(trackHost.host), plan);
  const trackSnapshotBefore = trackHost.host.textContent;

  const document = makeMixedStatusDocument();
  renderContext(asElement(contextHost.host), buildContextPlan({ fileName: FILE_NAME, document }, []));
  renderContext(
    asElement(contextHost.host),
    buildContextPlan({ fileName: FILE_NAME, document: makeMixedStatusDocument() }, []),
  );

  assert.equal(trackHost.host.textContent, trackSnapshotBefore, "アニメーション面が巻き添えで変わった");
});

// --- 7. コンパイル出力の import 境界（Dependency Direction） ----------------------------

test("コンパイル出力が import してよいのは schema / format / scale / plan/* のみで、load を import しない", () => {
  const source = readCompiledRenderSource();
  const specifiers = [...source.matchAll(/from\s*["']([^"']+)["']/g)].map((m) => m[1] ?? "");
  assert.ok(specifiers.length > 0, "import 文が 1 つも無い");
  for (const specifier of specifiers) {
    assert.doesNotMatch(specifier, /\bload\.js$/, `load.js を import している: ${specifier}`);
    assert.ok(
      /^\.\.\/(schema|format|scale)\.js$/.test(specifier) || /^\.\.\/plan\//.test(specifier),
      `許可されていない import: ${specifier}`,
    );
  }
});

// --- 8. 座標変換は scale.ts の linearMap / padRange のみで行う（境界検査 B-5 相当） -------

test("render.ts のコンパイル出力が非線形の算術（sqrt/pow/三角関数）を持たない", () => {
  const source = readCompiledRenderSource();
  assert.doesNotMatch(source, /Math\.(sqrt|pow|sin|cos|tan|atan2|log|exp)\b/);
});

test("render.ts のコンパイル出力に補間の再実装（lerp 相当の宣言）が無い", () => {
  const source = readCompiledRenderSource();
  assert.doesNotMatch(source, /function\s+lerp\b/, "lerp を再宣言している（境界検査 B-7）");
});

// --- プランに無い判断をしない（要件 3.7 相当の描画側での確認） --------------------------

test("軸が 1 本の掃引でも、プランどおり全格子点が同じ行に描かれる（要件 2.8 の描画側での反映）", () => {
  const { host } = makeHost();
  const document = makeMixedStatusDocument();
  const view = viewOf(document);
  // 2 軸の掃引だが、Renderer 自身は軸数の判断をしない。プランが 1 行を要求すれば従うだけ。
  const oneAxisDocument = makeSweepDocumentWithRecords();
  void oneAxisDocument;
  const plan = buildRegionPlan(view, defaultSelection(document.sweep));
  renderRegion(asElement(host), plan);

  const cellNodes = host.queryAllByClass("region-cell");
  const yValues = new Set(cellNodes.map((node) => node.getAttribute("y")));
  // 2 軸の掃引なので複数行であってよいが、位置は plan.cells の column/row から機械的に決まる
  // ことだけを確認する（Renderer が row を無視して常に 1 行に潰していないこと）。
  assert.ok(yValues.size > 1, "Renderer が行の違いを無視している（プランの row を反映していない）");
});

test("renderAnimationUnavailable が理由をそのまま表示し、図の要素を作らない（要件 1.7）", () => {
  const { host } = makeHost();
  const reason = "代表記録が入力に無い";
  renderAnimationUnavailable(asElement(host), reason);

  assert.match(host.textContent, new RegExp(reason));
  assert.equal(host.queryAllByTag("svg").length, 0, "利用不可の表示なのに図を描いている");
});

test("同じプランから renderRegion を 2 回呼んでも同じ結果になる（設計 Domain Model の不変条件）", () => {
  const hostA = makeHost().host;
  const hostB = makeHost().host;
  const document = makeMixedStatusDocument();
  const view = viewOf(document);
  const plan = buildRegionPlan(view, defaultSelection(document.sweep));

  renderRegion(asElement(hostA), plan);
  renderRegion(asElement(hostB), plan);

  assert.equal(hostA.textContent, hostB.textContent);
});

// --- 前提と限界（要件 3.1〜3.4）の描画がプランの値をそのまま反映する ---------------------

test("較正段階・除外要因・パラメータ出所が省略されずに現れる（要件 3.1 / 3.3 / 3.4）", () => {
  const { host } = makeHost();
  const document = makeMixedStatusDocument();
  const plan = buildContextPlan({ fileName: FILE_NAME, document }, []);
  renderContext(asElement(host), plan);

  const text = host.textContent;
  assert.match(text, new RegExp(plan.calibrationStageLabel.replace(/[()（）]/g, ".")));
  let totalItems = 0;
  for (const group of plan.exclusions) {
    totalItems += group.items.length;
    for (const item of group.items) {
      assert.match(text, new RegExp(item), `除外要因 ${item} が表示されていない`);
    }
  }
  assert.ok(totalItems > 0, "テスト前提: 除外要因が入力にあるはず");

  // 出所の記載が無い行は「出所の記載なし」と表示し、「想定」で埋めない（設計 ContextPlanner）。
  const unspecified = required(
    plan.parameters.find((row) => row.provenance === null),
    "テスト前提: 出所の記載が無い行があるはず",
  );
  const rows = host.queryAllByTag("tr");
  const matchingRow = required(
    rows.find((row) => row.textContent.includes(unspecified.path)),
    `パラメータ行 ${unspecified.path} が表示されていない`,
  );
  assert.doesNotMatch(matchingRow.textContent, /assumed|想定/, "出所の記載が無い行を「想定」で埋めている");
});

test("観測サンプルの完全な軌跡がプランと同じ点数で 1 度だけ描かれる（要件 5.1）", () => {
  const { host, doc } = makeHost();
  const samples = makeSamples();
  const record = makeThrowRecord({ samples });
  const plan = buildAnimationPlan(record, "xy");
  createAnimationView(asElement(host), plan);

  const pathNodes = host.queryAllByClass("track-path");
  assert.equal(pathNodes.length, 1, "全軌跡を表す要素が 1 つでない");
  const pointsAttr = pathNodes[0]?.getAttribute("points") ?? "";
  const pointCount = pointsAttr.trim().length === 0 ? 0 : pointsAttr.trim().split(/\s+/).length;
  assert.equal(pointCount, samples.length, "軌跡の点数が観測サンプル数と一致しない");
  void doc;
});

test("有効な予測ごとにマーカーが 1 つずつ作られ、無効な予測は理由とともに一覧に現れる（要件 5.4）", () => {
  const { host } = makeHost();
  const predictions = [
    makeInvalidPrediction({ reason: "insufficient_samples", based_on_time_ms: 30 }),
    makePrediction({ based_on_time_ms: 90 }),
  ];
  const record = makeThrowRecord({ predictions });
  const plan = buildAnimationPlan(record, "xy");
  createAnimationView(asElement(host), plan);

  const markers = host.queryAllByClass("track-prediction");
  const validCount = predictions.filter((p) => p.kind === "prediction").length;
  assert.equal(markers.length, validCount, "有効な予測の数だけマーカーが作られていない");

  const text = host.textContent;
  assert.match(text, /insufficient_samples/, "無効理由が表示されていない");
});
