// Node.js 組み込みモジュールのうち、テストが実際に使う分だけの型宣言。
// 開発時依存を TypeScript コンパイラ 1 個に保つため、@types/node を導入しない（要件 7.4）。
// 使う API が増えたときだけ、ここへ最小限の宣言を追加する。

declare module "node:test" {
  export function test(name: string, fn: () => void | Promise<void>): void;
  export function describe(name: string, fn: () => void): void;
}

declare module "node:assert/strict" {
  interface StrictAssert {
    ok(value: unknown, message?: string): void;
    equal(actual: unknown, expected: unknown, message?: string): void;
    notEqual(actual: unknown, expected: unknown, message?: string): void;
    deepEqual(actual: unknown, expected: unknown, message?: string): void;
    match(value: string, pattern: RegExp, message?: string): void;
    doesNotMatch(value: string, pattern: RegExp, message?: string): void;
  }
  const assert: StrictAssert;
  export default assert;
}

declare module "node:fs" {
  export function readFileSync(path: string | URL, encoding: "utf8"): string;
  export function existsSync(path: string | URL): boolean;
  export function readdirSync(
    path: string | URL,
    options: { readonly recursive: true; readonly encoding: "utf8" },
  ): string[];
}
