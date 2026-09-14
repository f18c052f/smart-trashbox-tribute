// 境界検査（B-1〜B-10）が「実際に違反を検出できること」を証明するテスト（要件 7.5、設計
// `#### BoundaryCheck` / `## 境界検査` / `## Testing Strategy` → `### 境界テスト（要件 7）`）。
//
// タスク 5.1 で `boundaries.test.ts` から切り出された検査関数を、共有の非テスト支援モジュール
// `boundaries-check.ts`（タスク 5.2 の構造修正で導入。理由は同ファイル冒頭のコメントを参照）
// からそのまま import して使う。ここでは検査ロジックを一切再実装しない。違反を含む架空の
// ソース文字列（と、B-1 / B-4 が構造上必要とする架空のファイル名）を各関数へ渡し、
//   1. 違反を含む例では違反が報告されること
//   2. 違反を含まない例では誤検出（false positive）が起きないこと
// の両方を固定する。
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  checkB1ImportDirectionForFile,
  checkB2RelativeImportsOnly,
  checkB3NoNetworkOrPersistenceIdentifiers,
  checkB4DomGlobalScope,
  checkB5MathAllowList,
  checkB6ForbiddenNamePrefixes,
  checkB7InterpolationDeclarationsInFile,
  checkB7SingleInterpolationAcrossTree,
  checkB8NoGravityNumericLiterals,
  checkB9EmptyRuntimeDependencies,
  checkB10NoVerdictWordsInStrings,
} from "./boundaries-check.js";

// ---------------------------------------------------------------------------
// B-1: モジュール間の import が Dependency Direction の表に載る辺のみであること
// ---------------------------------------------------------------------------

test("B-1 違反: plan/region から load への import（表に無い辺）が検出される", () => {
  const source = `import { loadSweep } from "../load.js";\nexport const noop = loadSweep;\n`;
  const violations = checkB1ImportDirectionForFile("plan/region.ts", source);
  assert.equal(violations.length > 0, true, JSON.stringify(violations));
});

test("B-1 健全: app から load への import（表にある辺）は誤検出されない", () => {
  const source = `import { loadSweep } from "./load.js";\nexport const noop = loadSweep;\n`;
  const violations = checkB1ImportDirectionForFile("app.ts", source);
  assert.deepEqual(violations, []);
});

// ---------------------------------------------------------------------------
// B-2: viz/src の import が相対パスのみであること
// ---------------------------------------------------------------------------

test("B-2 違反: bare specifier の import（相対パスでない）が検出される", () => {
  const source = `import ts from "typescript";\nexport const noop = ts;\n`;
  const violations = checkB2RelativeImportsOnly("app.ts", source);
  assert.equal(violations.length > 0, true, JSON.stringify(violations));
});

test("B-2 健全: 相対パスの import は誤検出されない", () => {
  const source = `import { x } from "./y.js";\nexport const noop = x;\n`;
  const violations = checkB2RelativeImportsOnly("app.ts", source);
  assert.deepEqual(violations, []);
});

// ---------------------------------------------------------------------------
// B-3: 通信・永続化の識別子が現れないこと
// ---------------------------------------------------------------------------

test("B-3 違反: 実際の fetch 呼び出しが検出される", () => {
  const source = `export function load(): void {\n  fetch("/x");\n}\n`;
  const violations = checkB3NoNetworkOrPersistenceIdentifiers("app.ts", source);
  assert.equal(violations.length > 0, true, JSON.stringify(violations));
});

test("B-3 違反: 実際の localStorage 呼び出しが検出される", () => {
  const source = `export function save(): void {\n  localStorage.setItem("a", "b");\n}\n`;
  const violations = checkB3NoNetworkOrPersistenceIdentifiers("app.ts", source);
  assert.equal(violations.length > 0, true, JSON.stringify(violations));
});

test("B-3 健全: コメントで語を言及するだけでは誤検出されない（AST ベースであることの証明）", () => {
  const source = `// we don't use fetch here\nexport const noop = 1;\n`;
  const violations = checkB3NoNetworkOrPersistenceIdentifiers("app.ts", source);
  assert.deepEqual(violations, []);
});

// ---------------------------------------------------------------------------
// B-4: document / window を参照してよいのは view/render・app・main のみ
// ---------------------------------------------------------------------------

test("B-4 違反: plan/region からの document 参照が検出される", () => {
  const source = `export function bad(): void {\n  document.getElementById("x");\n}\n`;
  const violations = checkB4DomGlobalScope("plan/region.ts", source);
  assert.equal(violations.length > 0, true, JSON.stringify(violations));
});

test("B-4 健全 (a): 同じ document 参照でも view/render からなら誤検出されない", () => {
  const source = `export function ok(): void {\n  document.getElementById("x");\n}\n`;
  const violations = checkB4DomGlobalScope("view/render.ts", source);
  assert.deepEqual(violations, []);
});

test("B-4 健全 (b): plan/region からでも view.document.sweep のようなプロパティ名としての document は誤検出されない", () => {
  const source = `export const value = view.document.sweep;\n`;
  const violations = checkB4DomGlobalScope("plan/region.ts", source);
  assert.deepEqual(violations, []);
});

// ---------------------------------------------------------------------------
// B-5: Math の参照が許可リストのみであること
// ---------------------------------------------------------------------------

test("B-5 違反: Math.sqrt（許可リスト外）が検出される", () => {
  const source = `export const x = Math.sqrt(4);\n`;
  const violations = checkB5MathAllowList("app.ts", source);
  assert.equal(violations.length > 0, true, JSON.stringify(violations));
});

test("B-5 健全: Math.max（許可リスト内）は誤検出されない", () => {
  const source = `export const x = Math.max(1, 2);\n`;
  const violations = checkB5MathAllowList("app.ts", source);
  assert.deepEqual(violations, []);
});

// ---------------------------------------------------------------------------
// B-6: 宣言される名前が禁止語に前方一致しないこと
// ---------------------------------------------------------------------------

test("B-6 違反: predictLandingPoint という宣言名が検出される（-ion 例外に当たらない）", () => {
  const source = `function predictLandingPoint() {\n  return 1;\n}\n`;
  const violations = checkB6ForbiddenNamePrefixes("app.ts", source);
  assert.equal(violations.length > 0, true, JSON.stringify(violations));
});

test("B-6 違反: estimateVelocity という宣言名が検出される（-ion 例外は predict にしか無い）", () => {
  const source = `function estimateVelocity() {\n  return 1;\n}\n`;
  const violations = checkB6ForbiddenNamePrefixes("app.ts", source);
  assert.equal(violations.length > 0, true, JSON.stringify(violations));
});

test("B-6 健全: predictions（-ion 例外）も PredictionMarker（interface。対象外）も誤検出されない", () => {
  const source = `const predictions: number[] = [];\ninterface PredictionMarker {\n  readonly id: string;\n}\n`;
  const violations = checkB6ForbiddenNamePrefixes("app.ts", source);
  assert.deepEqual(violations, []);
});

// ---------------------------------------------------------------------------
// B-7: 補間の実装は scale.ts の lerp 1 個のみであること
// ---------------------------------------------------------------------------

test("B-7 (ファイル単位) 違反: scale.ts 以外での interpolate 宣言が検出される", () => {
  const source = `function interpolate(a: number, b: number, t: number): number {\n  return a + (b - a) * t;\n}\n`;
  const violations = checkB7InterpolationDeclarationsInFile("plan/animation.ts", source);
  assert.equal(violations.length > 0, true, JSON.stringify(violations));
});

test("B-7 (ファイル単位) 違反: scale.ts 以外での lerp 宣言が検出される", () => {
  const source = `function lerp(a: number, b: number, t: number): number {\n  return a + (b - a) * t;\n}\n`;
  const violations = checkB7InterpolationDeclarationsInFile("plan/animation.ts", source);
  assert.equal(violations.length > 0, true, JSON.stringify(violations));
});

test("B-7 (ファイル単位) 健全: scale.ts での lerp 宣言は誤検出されない", () => {
  const source = `export function lerp(a: number, b: number, t: number): number {\n  return a + (b - a) * t;\n}\n`;
  const violations = checkB7InterpolationDeclarationsInFile("scale.ts", source);
  assert.deepEqual(violations, []);
});

test("B-7 (木全体) 違反: lerp がどこにも 1 個も無い（検出数 0）ことが検出される", () => {
  const fileSources = new Map<string, string>([
    ["scale.ts", `export const noop = 1;\n`],
    ["app.ts", `export const other = 2;\n`],
  ]);
  const violations = checkB7SingleInterpolationAcrossTree(fileSources);
  assert.equal(violations.length > 0, true, JSON.stringify(violations));
});

test("B-7 (木全体) 健全: scale.ts に lerp がちょうど 1 個だけあれば誤検出されない", () => {
  const fileSources = new Map<string, string>([
    ["scale.ts", `export function lerp(a: number, b: number, t: number): number {\n  return a + (b - a) * t;\n}\n`],
  ]);
  const violations = checkB7SingleInterpolationAcrossTree(fileSources);
  assert.deepEqual(violations, []);
});

// ---------------------------------------------------------------------------
// B-8: 重力に相当する数値リテラルが現れないこと
// ---------------------------------------------------------------------------

test("B-8 違反: 9.81（重力定数に相当する値）が検出される", () => {
  const source = `export const G = 9.81;\n`;
  const violations = checkB8NoGravityNumericLiterals("app.ts", source);
  assert.equal(violations.length > 0, true, JSON.stringify(violations));
});

test("B-8 健全: 9.8（丸めた近似値。厳密一致ではないので許可リスト外）は誤検出されない", () => {
  const source = `export const G = 9.8;\n`;
  const violations = checkB8NoGravityNumericLiterals("app.ts", source);
  assert.deepEqual(violations, []);
});

// ---------------------------------------------------------------------------
// B-9: package.json の dependencies が空であること
// ---------------------------------------------------------------------------

test("B-9 違反: dependencies に実行時依存が 1 件でもあれば検出される", () => {
  const packageJson = { dependencies: { "left-pad": "^1.0.0" } };
  const violations = checkB9EmptyRuntimeDependencies(packageJson);
  assert.equal(violations.length > 0, true, JSON.stringify(violations));
});

test("B-9 健全: dependencies が空オブジェクトなら誤検出されない", () => {
  const packageJson = { dependencies: {} };
  const violations = checkB9EmptyRuntimeDependencies(packageJson);
  assert.deepEqual(violations, []);
});

// ---------------------------------------------------------------------------
// B-10: 文字列リテラルに断定語が現れないこと
// ---------------------------------------------------------------------------

test("B-10 違反: 文字列リテラル中の断定語が検出される", () => {
  const source = `export const message = "判定: 合格";\n`;
  const violations = checkB10NoVerdictWordsInStrings("app.ts", source);
  assert.equal(violations.length > 0, true, JSON.stringify(violations));
});

test("B-10 健全 (a): no_floor_crossing のような近似部分文字列（誤検出リスクの古典例）は誤検出されない", () => {
  const source = `export const reason = "no_floor_crossing";\n`;
  const violations = checkB10NoVerdictWordsInStrings("app.ts", source);
  assert.deepEqual(violations, []);
});

test("B-10 健全 (b): track-path-traversed（実際の過去の不具合修正対象の文字列）は誤検出されない", () => {
  const source = `export const className = "track-path-traversed";\n`;
  const violations = checkB10NoVerdictWordsInStrings("app.ts", source);
  assert.deepEqual(violations, []);
});
