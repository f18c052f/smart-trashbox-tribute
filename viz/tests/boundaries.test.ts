// ブラウザ側にアルゴリズムを持たないことを静的に検査する（要件 7.1〜7.4, 7.6、設計
// `#### BoundaryCheck` / `## 境界検査`）。
//
// 上流 `tests/prediction_core/test_boundaries.py` と同じ方針を採る。
//   1. 検査対象（`viz/src/**/*.ts`）を import しない。常にソースをテキストとして読み、
//      TypeScript Compiler API（`ts.createSourceFile`）で AST として走査する。
//   2. 検査ロジックは関数として切り出す。各関数はソース文字列（と、必要なら対象ファイル名・
//      `package.json` の解釈済みオブジェクト）を引数に取り、違反の一覧を返す。
//      違反を含む架空のソースを渡すテスト（`boundaries-negative.test.ts`、タスク 5.2）は
//      `./boundaries-check.js` の関数を import して使う。
//   3. 許可リスト・禁止語の一覧はソース内の定数として持つ（要件 7.6。広げれば差分に現れる）。
//
// 検査関数・許可リスト・禁止語・内部ヘルパーの実体は `./boundaries-check.ts`（非テスト支援
// モジュール）に置く。`node --test` は `*.test.js` のみを実行するため、もし本ファイルが
// それらを直接持ったまま `boundaries-negative.test.ts` から import されると、本ファイルの
// `test(...)` が二重に登録・実行されてしまう。それを避けるため、両方のテストファイルは
// テストを含まない共有モジュール `boundaries-check.ts` から検査ロジックを import する
// （`viz/tests/fixtures.ts` / `viz/tests/dom-stub.ts` と同じ構造）。
// ここには、実ソースツリー（`viz/src`）・`package.json` に対して実際に検査を走らせる
// `test(...)` のみが残る。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import {
  MODULE_IDS,
  resolveModuleId,
  checkB1ImportDirectionForFile,
  checkB2RelativeImportsOnly,
  checkB3NoNetworkOrPersistenceIdentifiers,
  checkB4DomGlobalScope,
  checkB5MathAllowList,
  checkB6ForbiddenNamePrefixes,
  checkB7SingleInterpolationAcrossTree,
  checkB8NoGravityNumericLiterals,
  checkB9EmptyRuntimeDependencies,
  checkB10NoVerdictWordsInStrings,
} from "./boundaries-check.js";

// ---------------------------------------------------------------------------
// 実ソースツリーに対するテスト
// ---------------------------------------------------------------------------

const VIZ_ROOT = new URL("../../", import.meta.url); // dist/tests から見た viz/
const SRC_ROOT = new URL("src/", VIZ_ROOT);

function listSourceFiles(): readonly string[] {
  const entries = readdirSync(SRC_ROOT, { recursive: true, encoding: "utf8" });
  return entries
    .map((entry) => entry.replace(/\\/g, "/"))
    .filter((entry) => entry.endsWith(".ts"))
    .sort();
}

function readSource(relativePath: string): string {
  return readFileSync(new URL(relativePath, SRC_ROOT), "utf8");
}

function readAllSources(): ReadonlyMap<string, string> {
  const map = new Map<string, string>();
  for (const relativePath of listSourceFiles()) {
    map.set(relativePath, readSource(relativePath));
  }
  return map;
}

function readPackageJson(): Record<string, unknown> {
  const text = readFileSync(new URL("package.json", VIZ_ROOT), "utf8");
  return JSON.parse(text) as Record<string, unknown>;
}

test("走査対象が想定どおり 10 モジュールである", () => {
  const resolved = listSourceFiles().map((relativePath) => resolveModuleId(relativePath));
  assert.deepEqual(new Set(resolved), new Set(MODULE_IDS), JSON.stringify(resolved));
});

test("B-1: モジュール間の import が Dependency Direction の表に載る辺のみである", () => {
  const violations = listSourceFiles().flatMap((relativePath) =>
    checkB1ImportDirectionForFile(relativePath, readSource(relativePath)),
  );
  assert.deepEqual(violations, [], JSON.stringify(violations));
});

test("B-2: viz/src の import が相対パスのみである", () => {
  const violations = listSourceFiles().flatMap((relativePath) =>
    checkB2RelativeImportsOnly(relativePath, readSource(relativePath)),
  );
  assert.deepEqual(violations, [], JSON.stringify(violations));
});

test("B-3: 通信・永続化の識別子が現れない", () => {
  const violations = listSourceFiles().flatMap((relativePath) =>
    checkB3NoNetworkOrPersistenceIdentifiers(relativePath, readSource(relativePath)),
  );
  assert.deepEqual(violations, [], JSON.stringify(violations));
});

test("B-4: document / window を参照してよいのは view/render・app・main のみ", () => {
  const violations = listSourceFiles().flatMap((relativePath) =>
    checkB4DomGlobalScope(relativePath, readSource(relativePath)),
  );
  assert.deepEqual(violations, [], JSON.stringify(violations));
});

test("B-5: Math の参照が許可リストのみである", () => {
  const violations = listSourceFiles().flatMap((relativePath) =>
    checkB5MathAllowList(relativePath, readSource(relativePath)),
  );
  assert.deepEqual(violations, [], JSON.stringify(violations));
});

test("B-6: 宣言される名前が禁止語に前方一致しない", () => {
  const violations = listSourceFiles().flatMap((relativePath) =>
    checkB6ForbiddenNamePrefixes(relativePath, readSource(relativePath)),
  );
  assert.deepEqual(violations, [], JSON.stringify(violations));
});

test("B-7: 補間の実装は scale.ts の lerp 1 個のみである", () => {
  const violations = checkB7SingleInterpolationAcrossTree(readAllSources());
  assert.deepEqual(violations, [], JSON.stringify(violations));
});

test("B-8: 重力に相当する数値リテラルが現れない", () => {
  const violations = listSourceFiles().flatMap((relativePath) =>
    checkB8NoGravityNumericLiterals(relativePath, readSource(relativePath)),
  );
  assert.deepEqual(violations, [], JSON.stringify(violations));
});

test("B-9: package.json の dependencies が空である", () => {
  const violations = checkB9EmptyRuntimeDependencies(readPackageJson());
  assert.deepEqual(violations, [], JSON.stringify(violations));
});

test("B-10: 文字列リテラルに断定語が現れない", () => {
  const violations = listSourceFiles().flatMap((relativePath) =>
    checkB10NoVerdictWordsInStrings(relativePath, readSource(relativePath)),
  );
  assert.deepEqual(violations, [], JSON.stringify(violations));
});
