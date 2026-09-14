// 表示レイヤの土台そのものを検証する。
// ここで固定するのは「実行時のサードパーティ依存を持たないこと」（要件 7.4）と、
// 「静的ファイルの配信だけでブラウザに表示できる形になっていること」（要件 8.1 / 8.2 / 8.3）である。
import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readdirSync, readFileSync } from "node:fs";

// dist/tests/scaffold.test.js から見た viz/ の位置。
const VIZ_ROOT = new URL("../../", import.meta.url);

function readText(relativePath: string): string {
  return readFileSync(new URL(relativePath, VIZ_ROOT), "utf8");
}

function asRecord(value: unknown, label: string): Record<string, unknown> {
  assert.ok(
    typeof value === "object" && value !== null && !Array.isArray(value),
    `${label} がオブジェクトでない`,
  );
  return value as Record<string, unknown>;
}

function readPackageJson(): Record<string, unknown> {
  return asRecord(JSON.parse(readText("package.json")), "package.json");
}

test("実行時のサードパーティ依存を持たない（要件 7.4）", () => {
  const pkg = readPackageJson();
  assert.deepEqual(pkg["dependencies"], {}, "dependencies は空でなければならない");
  assert.equal(pkg["peerDependencies"], undefined, "peerDependencies を持たない");
  assert.equal(pkg["optionalDependencies"], undefined, "optionalDependencies を持たない");
});

test("開発時依存は TypeScript コンパイラ 1 個のみ", () => {
  const pkg = readPackageJson();
  const devDependencies = asRecord(pkg["devDependencies"], "devDependencies");
  assert.deepEqual(Object.keys(devDependencies), ["typescript"]);
});

test("Node.js 20 以上を要求する", () => {
  const engines = asRecord(readPackageJson()["engines"], "engines");
  const nodeRange = engines["node"];
  assert.equal(typeof nodeRange, "string");
  const matched = /^>=\s*(\d+)/.exec(nodeRange as string);
  assert.ok(matched !== null, `engines.node が下限指定になっていない: ${String(nodeRange)}`);
  const lowerBound = Number(matched?.[1]);
  assert.ok(lowerBound >= 20, `engines.node の下限が 20 未満: ${String(nodeRange)}`);
});

test("ビルドとテストはコンパイラと Node.js 組み込みのテストランナーだけで完結する", () => {
  const scripts = asRecord(readPackageJson()["scripts"], "scripts");
  assert.equal(scripts["build"], "tsc");
  assert.equal(scripts["test"], "tsc && node --test dist/tests/*.test.js");
  for (const command of Object.values(scripts)) {
    assert.doesNotMatch(
      String(command),
      /--experimental/,
      "実験的機能に依存してはならない",
    );
  }
});

test("コンパイル出力がブラウザの読める ES モジュールである", () => {
  const mainUrl = new URL("dist/src/main.js", VIZ_ROOT);
  assert.ok(existsSync(mainUrl), "dist/src/main.js が生成されていない");
  const main = readText("dist/src/main.js");
  assert.match(main, /^\s*(import|export)\b/m, "ES モジュールの構文を含まない");
  assert.doesNotMatch(main, /\brequire\s*\(/, "CommonJS の require を含んではならない");
  assert.doesNotMatch(main, /\bmodule\.exports\b/, "CommonJS の module.exports を含んではならない");
});

test("唯一の HTML がコンパイル出力とスタイルシートを静的に読む（要件 8.2）", () => {
  const html = readText("index.html");
  assert.match(
    html,
    /<script[^>]*\btype="module"[^>]*\bsrc="dist\/src\/main\.js"[^>]*>/,
    "dist/src/main.js を type=\"module\" で読み込んでいない",
  );
  assert.match(html, /<link[^>]*\brel="stylesheet"[^>]*\bhref="style\.css"[^>]*>/);
  assert.doesNotMatch(html, /https?:\/\//, "外部から取得する参照を持ってはならない（要件 7.3）");
});

test("図の色をスタイルシートのカスタムプロパティとして定義する", () => {
  const css = readText("style.css");
  const rootBlock = /:root\s*\{([\s\S]*?)\}/.exec(css);
  assert.ok(rootBlock !== null, ":root ブロックが無い");
  const declarations = rootBlock?.[1] ?? "";
  const customProperties = declarations.match(/--[a-z0-9-]+\s*:/g) ?? [];
  assert.ok(
    customProperties.length >= 3,
    `図の色がカスタムプロパティになっていない: ${customProperties.length} 件`,
  );
  assert.doesNotMatch(css, /@import/, "CSS フレームワークの取り込みを持ってはならない");
});

test("コンパイル出力の相対参照が拡張子付きで、ブラウザがそのまま解決できる", () => {
  const outputDir = new URL("dist/src/", VIZ_ROOT);
  const files = readdirSync(outputDir, { recursive: true, encoding: "utf8" })
    .filter((name) => name.endsWith(".js"));
  assert.ok(files.length > 0, "dist/src にコンパイル出力が無い");
  // tsc は引用符の種類を書き換えないため、二重引用符と単一引用符の双方を拾う。
  const RELATIVE_SPECIFIER = /(?:from|import)\s*\(?\s*(["'])(\.[^"']*)\1/g;
  for (const name of files) {
    const source = readFileSync(new URL(name.split("\\").join("/"), outputDir), "utf8");
    for (const matched of source.matchAll(RELATIVE_SPECIFIER)) {
      const specifier = matched[2] ?? "";
      assert.match(
        specifier,
        /\.js$/,
        `${name} の相対参照が拡張子を持たない: ${specifier}`,
      );
    }
  }
});
