// ブラウザ側にアルゴリズムを持たないことを静的に検査する境界検査ロジック本体（要件 7.1〜7.4, 7.6、
// 設計 `#### BoundaryCheck` / `## 境界検査`）。
//
// 上流 `tests/prediction_core/test_boundaries.py` と同じ方針を採る。
//   1. 検査対象（`viz/src/**/*.ts`）を import しない。常にソースをテキストとして読み、
//      TypeScript Compiler API（`ts.createSourceFile`）で AST として走査する。
//   2. 検査ロジックは関数として切り出す。各関数はソース文字列（と、必要なら対象ファイル名・
//      `package.json` の解釈済みオブジェクト）を引数に取り、違反の一覧を返す。
//      違反を含む架空のソースを渡すテスト（`boundaries-negative.test.ts`、タスク 5.2）は
//      本ファイルの関数を import して使う。
//   3. 許可リスト・禁止語の一覧はソース内の定数として持つ（要件 7.6。広げれば差分に現れる）。
//
// このファイル自体はテストを含まない（`*.test.ts` にマッチしない）非テスト支援モジュールである。
// `node --test dist/tests/*.test.js` のグロブに拾われないことで、`boundaries.test.ts` と
// `boundaries-negative.test.ts` の双方がここを import しても `test(...)` の二重登録が起きない
// （`viz/tests/fixtures.ts` / `viz/tests/dom-stub.ts` と同じ構造上の理由）。
// 実ソースツリー（`viz/src`）・`package.json` に対して実際に検査を走らせる `test(...)` は
// `boundaries.test.ts` に残る。ここには検査関数と、それが依存する許可リスト・禁止語・
// 内部ヘルパーのみを置く。
import * as ts from "typescript";

// ---------------------------------------------------------------------------
// 共通の型・ヘルパー
// ---------------------------------------------------------------------------

/** 違反 1 件。ファイル名・行番号・人間可読なメッセージを持つ。 */
export interface BoundaryViolation {
  readonly file: string;
  readonly line: number;
  readonly message: string;
}

/** ソース文字列を AST として読む。対象を import しない（常にテキストとして読む）。 */
function parseSource(fileName: string, sourceText: string): ts.SourceFile {
  return ts.createSourceFile(fileName, sourceText, ts.ScriptTarget.ES2022, true, ts.ScriptKind.TS);
}

function lineOf(sourceFile: ts.SourceFile, node: ts.Node): number {
  return sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile)).line + 1;
}

/** `sourceFile` の全ノードを深さ優先で訪問する。コメント・文字列の中身は AST に無いため自然に除外される。 */
function forEachDescendant(sourceFile: ts.SourceFile, visitor: (node: ts.Node) => void): void {
  const visit = (node: ts.Node): void => {
    visitor(node);
    ts.forEachChild(node, visit);
  };
  ts.forEachChild(sourceFile, visit);
}

// ---------------------------------------------------------------------------
// B-1: Dependency Direction
// ---------------------------------------------------------------------------

export const MODULE_IDS = [
  "schema",
  "scale",
  "format",
  "load",
  "plan/region",
  "plan/animation",
  "plan/context",
  "view/render",
  "app",
  "main",
] as const;

export type ModuleId = (typeof MODULE_IDS)[number];

export const MODULE_LAYER_ALLOWED_IMPORTS: Readonly<Record<ModuleId, readonly ModuleId[]>> = {
  schema: [],
  scale: [],
  format: ["schema"],
  load: ["schema"],
  "plan/region": ["schema", "scale", "format"],
  "plan/animation": ["schema", "scale"],
  "plan/context": ["schema", "format"],
  "view/render": ["schema", "scale", "format", "plan/region", "plan/animation", "plan/context"],
  app: ["schema", "scale", "format", "load", "plan/region", "plan/animation", "plan/context", "view/render"],
  main: ["app"],
};

/**
 * ファイルパス（`viz/src/` からの相対パスでも、リポジトリルートからの絶対パス風でもよい）を
 * {@link ModuleId} へ解決する。既知のどのモジュールにも一致しなければ `null` を返す。
 */
export function resolveModuleId(fileName: string): ModuleId | null {
  let normalized = fileName.replace(/\\/g, "/");
  for (const prefix of ["viz/src/", "src/", "./"]) {
    if (normalized.startsWith(prefix)) {
      normalized = normalized.slice(prefix.length);
    }
  }
  normalized = normalized.replace(/\.(ts|js)$/, "");
  const found = MODULE_IDS.find((id) => normalized === id || normalized.endsWith(`/${id}`));
  return found ?? null;
}

/** `fileName` の場所から見た相対 import 指定子 `specifier` の解決先パス（拡張子つき）を返す。 */
function resolveRelativeModuleTarget(fileName: string, specifier: string): string {
  const fileSegments = fileName.replace(/\\/g, "/").split("/");
  const stack = fileSegments.slice(0, -1); // ディレクトリ部分のみ
  for (const segment of specifier.replace(/\\/g, "/").split("/")) {
    if (segment === "" || segment === ".") continue;
    if (segment === "..") {
      stack.pop();
    } else {
      stack.push(segment);
    }
  }
  return stack.join("/");
}

/**
 * 単一ファイルの import 宣言（`import` / `import type` の両方。型のみの import に例外は無い）を、
 * そのファイルが属する層の許可リスト（{@link MODULE_LAYER_ALLOWED_IMPORTS}）と照合する。
 * `boundaries-negative.test.ts`（タスク 5.2）は、架空のファイル名・ソースを渡してこの関数を
 * 直接呼ぶことで、層の逆流を検出できることを示す。
 */
export function checkB1ImportDirectionForFile(
  fileName: string,
  sourceText: string,
): readonly BoundaryViolation[] {
  const sourceFile = parseSource(fileName, sourceText);
  const ownModuleId = resolveModuleId(fileName);
  if (ownModuleId === null) {
    throw new Error(`checkB1ImportDirectionForFile: "${fileName}" を既知のモジュールに解決できない`);
  }
  const allowed = MODULE_LAYER_ALLOWED_IMPORTS[ownModuleId];
  const violations: BoundaryViolation[] = [];
  for (const statement of sourceFile.statements) {
    if (!ts.isImportDeclaration(statement)) continue;
    if (!ts.isStringLiteral(statement.moduleSpecifier)) continue;
    const specifier = statement.moduleSpecifier.text;
    if (!specifier.startsWith("./") && !specifier.startsWith("../")) continue; // B-2 の対象
    const targetPath = resolveRelativeModuleTarget(fileName, specifier);
    const targetModuleId = resolveModuleId(targetPath);
    if (targetModuleId === null) {
      violations.push({
        file: fileName,
        line: lineOf(sourceFile, statement),
        message: `import 先 "${specifier}" が既知のモジュールに解決できない`,
      });
      continue;
    }
    if (targetModuleId === ownModuleId) continue;
    if (!allowed.includes(targetModuleId)) {
      violations.push({
        file: fileName,
        line: lineOf(sourceFile, statement),
        message: `${ownModuleId} から ${targetModuleId} への import は Dependency Direction の表に無い`,
      });
    }
  }
  return violations;
}

// ---------------------------------------------------------------------------
// B-2: 相対 import のみ
// ---------------------------------------------------------------------------

/** すべての import 宣言の指定子が `./` または `../` で始まることを検査する。 */
export function checkB2RelativeImportsOnly(
  fileName: string,
  sourceText: string,
): readonly BoundaryViolation[] {
  const sourceFile = parseSource(fileName, sourceText);
  const violations: BoundaryViolation[] = [];
  for (const statement of sourceFile.statements) {
    if (!ts.isImportDeclaration(statement)) continue;
    if (!ts.isStringLiteral(statement.moduleSpecifier)) continue;
    const specifier = statement.moduleSpecifier.text;
    if (!specifier.startsWith("./") && !specifier.startsWith("../")) {
      violations.push({
        file: fileName,
        line: lineOf(sourceFile, statement),
        message: `相対パスではない import: "${specifier}"`,
      });
    }
  }
  return violations;
}

// ---------------------------------------------------------------------------
// B-3: 通信・永続化識別子の禁止
// ---------------------------------------------------------------------------

export const FORBIDDEN_NETWORK_PERSISTENCE_IDENTIFIERS = [
  "fetch",
  "XMLHttpRequest",
  "WebSocket",
  "EventSource",
  "sendBeacon",
  "localStorage",
  "sessionStorage",
] as const;

/** 通信・永続化に使う識別子（{@link FORBIDDEN_NETWORK_PERSISTENCE_IDENTIFIERS}）が現れないことを検査する。 */
export function checkB3NoNetworkOrPersistenceIdentifiers(
  fileName: string,
  sourceText: string,
): readonly BoundaryViolation[] {
  const sourceFile = parseSource(fileName, sourceText);
  const forbidden: ReadonlySet<string> = new Set(FORBIDDEN_NETWORK_PERSISTENCE_IDENTIFIERS);
  const violations: BoundaryViolation[] = [];
  forEachDescendant(sourceFile, (node) => {
    if (ts.isIdentifier(node) && forbidden.has(node.text)) {
      violations.push({
        file: fileName,
        line: lineOf(sourceFile, node),
        message: `通信・永続化の識別子 "${node.text}" が現れている`,
      });
    }
  });
  return violations;
}

// ---------------------------------------------------------------------------
// B-4: document / window を参照してよいのは 3 モジュールのみ
// ---------------------------------------------------------------------------

export const DOM_GLOBAL_ALLOWED_MODULES: readonly ModuleId[] = ["view/render", "app", "main"];

/**
 * `document` / `window` という識別子が、その `.name`（プロパティアクセスの被アクセス名）・
 * プロパティ名・宣言名の位置ではなく、実際に値として参照される位置に現れているかを判定する
 * （Decision 2）。`foo.document` の `document` や、`{ document: ... }` / `interface X { document }`
 * のフィールド名、ローカルな仮引数・変数名としての `document` はここでは「値の参照」ではない。
 */
function isNonValueNamePosition(identifier: ts.Identifier): boolean {
  const parent = identifier.parent;
  if (parent === undefined) return false;
  if (ts.isPropertyAccessExpression(parent) && parent.name === identifier) return true;
  if (ts.isPropertySignature(parent) && parent.name === identifier) return true;
  if (ts.isPropertyAssignment(parent) && parent.name === identifier) return true;
  if (ts.isPropertyDeclaration(parent) && parent.name === identifier) return true;
  if (ts.isMethodSignature(parent) && parent.name === identifier) return true;
  if (ts.isMethodDeclaration(parent) && parent.name === identifier) return true;
  if (ts.isShorthandPropertyAssignment(parent) && parent.name === identifier) return true;
  if (ts.isVariableDeclaration(parent) && parent.name === identifier) return true;
  if (ts.isParameter(parent) && parent.name === identifier) return true;
  if (ts.isBindingElement(parent) && parent.name === identifier) return true;
  return false;
}

/** `document` / `window` を値として参照してよいのは {@link DOM_GLOBAL_ALLOWED_MODULES} のみであることを検査する。 */
export function checkB4DomGlobalScope(
  fileName: string,
  sourceText: string,
): readonly BoundaryViolation[] {
  const sourceFile = parseSource(fileName, sourceText);
  const moduleId = resolveModuleId(fileName);
  const allowed = moduleId !== null && (DOM_GLOBAL_ALLOWED_MODULES as readonly string[]).includes(moduleId);
  const violations: BoundaryViolation[] = [];
  forEachDescendant(sourceFile, (node) => {
    if (!ts.isIdentifier(node)) return;
    if (node.text !== "document" && node.text !== "window") return;
    if (isNonValueNamePosition(node)) return;
    if (allowed) return;
    violations.push({
      file: fileName,
      line: lineOf(sourceFile, node),
      message: `"${node.text}" をこのモジュールから参照できない（許可対象: ${DOM_GLOBAL_ALLOWED_MODULES.join(", ")}）`,
    });
  });
  return violations;
}

// ---------------------------------------------------------------------------
// B-5: Math 許可リスト
// ---------------------------------------------------------------------------

export const ALLOWED_MATH_MEMBERS = ["min", "max", "abs", "round", "floor", "ceil"] as const;

/** `Math.<member>` が {@link ALLOWED_MATH_MEMBERS} のみであることを検査する。 */
export function checkB5MathAllowList(
  fileName: string,
  sourceText: string,
): readonly BoundaryViolation[] {
  const sourceFile = parseSource(fileName, sourceText);
  const allowed: ReadonlySet<string> = new Set(ALLOWED_MATH_MEMBERS);
  const violations: BoundaryViolation[] = [];
  forEachDescendant(sourceFile, (node) => {
    if (!ts.isPropertyAccessExpression(node)) return;
    if (!ts.isIdentifier(node.expression) || node.expression.text !== "Math") return;
    const member = node.name.text;
    if (!allowed.has(member)) {
      violations.push({
        file: fileName,
        line: lineOf(sourceFile, node),
        message: `Math.${member} は許可リスト外（許可: ${ALLOWED_MATH_MEMBERS.join(", ")}）`,
      });
    }
  });
  return violations;
}

// ---------------------------------------------------------------------------
// B-6: 禁止語の前方一致
// ---------------------------------------------------------------------------

export const FORBIDDEN_NAME_PREFIXES = [
  "predict",
  "fit",
  "solve",
  "integrate",
  "simulate",
  "physics",
  "gravity",
  "estimate",
  "extrapolate",
] as const;

/** 宣言された名前 1 件。関数・メソッド・クラス・変数の宣言のみを対象とする（interface / type alias は対象外）。 */
interface DeclaredName {
  readonly name: string;
  readonly line: number;
}

function collectBindingNames(name: ts.BindingName, out: ts.Identifier[]): void {
  if (ts.isIdentifier(name)) {
    out.push(name);
    return;
  }
  for (const element of name.elements) {
    if (ts.isOmittedExpression(element)) continue;
    collectBindingNames(element.name, out);
  }
}

/**
 * `sourceFile` から「宣言される名前」（関数・メソッド・クラス・変数）を集める。
 * `interface` / `type` エイリアスの中身（`PropertySignature` / `MethodSignature`）は
 * ここで扱うどの種別とも一致しないため、特別扱いせずとも自然に除外される
 * （設計の「関数・メソッド・クラス・変数」という列挙どおり）。
 */
function collectDeclaredNames(sourceFile: ts.SourceFile): readonly DeclaredName[] {
  const identifiers: ts.Identifier[] = [];
  forEachDescendant(sourceFile, (node) => {
    if ((ts.isFunctionDeclaration(node) || ts.isFunctionExpression(node)) && node.name !== undefined) {
      identifiers.push(node.name);
    } else if ((ts.isClassDeclaration(node) || ts.isClassExpression(node)) && node.name !== undefined) {
      identifiers.push(node.name);
    } else if (ts.isMethodDeclaration(node) && ts.isIdentifier(node.name)) {
      identifiers.push(node.name);
    } else if (ts.isVariableDeclaration(node)) {
      collectBindingNames(node.name, identifiers);
    }
  });
  return identifiers.map((identifier) => ({
    name: identifier.text,
    line: lineOf(sourceFile, identifier),
  }));
}

/**
 * 宣言名 `name` が禁止語のいずれかに前方一致するかを判定する。一致すればその禁止語を返す。
 * `predict` のみ、直後 3 文字が `ion`（大小無視）であれば例外とする（Decision 1。
 * `predictions` / `PredictionEntry` / `PREDICTION_KINDS` は上流の出力フィールド名であって
 * アルゴリズムではないため。要件 1.2）。他の 8 語にこの例外は適用しない。
 */
function matchesForbiddenNamePrefix(name: string): string | null {
  const lower = name.toLowerCase();
  for (const word of FORBIDDEN_NAME_PREFIXES) {
    if (!lower.startsWith(word)) continue;
    if (word === "predict" && lower.slice(word.length, word.length + 3) === "ion") {
      continue; // prediction / predictions の名詞形（-ion / -ions）は例外
    }
    return word;
  }
  return null;
}

/** 宣言される名前が {@link FORBIDDEN_NAME_PREFIXES} に前方一致しないことを検査する。 */
export function checkB6ForbiddenNamePrefixes(
  fileName: string,
  sourceText: string,
): readonly BoundaryViolation[] {
  const sourceFile = parseSource(fileName, sourceText);
  const violations: BoundaryViolation[] = [];
  for (const declared of collectDeclaredNames(sourceFile)) {
    const matched = matchesForbiddenNamePrefix(declared.name);
    if (matched !== null) {
      violations.push({
        file: fileName,
        line: declared.line,
        message: `宣言名 "${declared.name}" が禁止語 "${matched}" に前方一致する`,
      });
    }
  }
  return violations;
}

// ---------------------------------------------------------------------------
// B-7: 補間の実装は 1 個のみ
// ---------------------------------------------------------------------------

/** 補間の宣言として検査する名前（大小無視の完全一致）。`lerp` 以外は宣言そのものが常に違反である。 */
export const INTERPOLATION_EXACT_NAMES = ["lerp", "interpolate", "spline", "bezier"] as const;

/** `lerp` 以外の、どこで宣言されても常に違反になる補間名。 */
const OTHER_INTERPOLATION_IMPLEMENTATION_NAMES: readonly string[] = INTERPOLATION_EXACT_NAMES.filter(
  (name) => name !== "lerp",
);

/**
 * 単一ファイル内の補間関連の宣言を検査する。`interpolate` / `spline` / `bezier`（大小無視）の
 * 宣言はどのファイルにあっても違反であり、`lerp` は `scale.ts` 以外での宣言が違反である。
 * `boundaries-negative.test.ts`（タスク 5.2）が架空の 1 ファイルを渡すのに使う。
 */
export function checkB7InterpolationDeclarationsInFile(
  fileName: string,
  sourceText: string,
): readonly BoundaryViolation[] {
  const sourceFile = parseSource(fileName, sourceText);
  const moduleId = resolveModuleId(fileName);
  const violations: BoundaryViolation[] = [];
  for (const declared of collectDeclaredNames(sourceFile)) {
    const lower = declared.name.toLowerCase();
    if (OTHER_INTERPOLATION_IMPLEMENTATION_NAMES.includes(lower)) {
      violations.push({
        file: fileName,
        line: declared.line,
        message: `補間の別実装 "${declared.name}" の宣言（唯一の実装は scale.ts の lerp）`,
      });
    } else if (lower === "lerp" && moduleId !== "scale") {
      violations.push({
        file: fileName,
        line: declared.line,
        message: `"lerp" は scale.ts 以外で宣言できない`,
      });
    }
  }
  return violations;
}

/**
 * ソースツリー全体で、`lerp` の宣言がちょうど 1 個だけ・かつ `scale.ts` にあることと、
 * `interpolate` / `spline` / `bezier` の宣言がどこにも無いことを検査する。
 */
export function checkB7SingleInterpolationAcrossTree(
  fileSources: ReadonlyMap<string, string>,
): readonly BoundaryViolation[] {
  const violations: BoundaryViolation[] = [];
  let lerpCount = 0;
  let lerpOutsideScaleFound = false;
  for (const [fileName, sourceText] of fileSources) {
    const sourceFile = parseSource(fileName, sourceText);
    const moduleId = resolveModuleId(fileName);
    for (const declared of collectDeclaredNames(sourceFile)) {
      const lower = declared.name.toLowerCase();
      if (OTHER_INTERPOLATION_IMPLEMENTATION_NAMES.includes(lower)) {
        violations.push({
          file: fileName,
          line: declared.line,
          message: `補間の別実装 "${declared.name}" の宣言（唯一の実装は scale.ts の lerp）`,
        });
      } else if (lower === "lerp") {
        lerpCount += 1;
        if (moduleId !== "scale") {
          lerpOutsideScaleFound = true;
          violations.push({
            file: fileName,
            line: declared.line,
            message: `"lerp" は scale.ts 以外で宣言されている`,
          });
        }
      }
    }
  }
  if (!lerpOutsideScaleFound && lerpCount !== 1) {
    violations.push({
      file: "(tree)",
      line: 0,
      message: `補間の実装は scale.ts の lerp 1 個に限られる（検出数: ${lerpCount}）`,
    });
  }
  return violations;
}

// ---------------------------------------------------------------------------
// B-8: 重力に相当する数値リテラルの禁止
// ---------------------------------------------------------------------------

export const FORBIDDEN_GRAVITY_LITERAL_VALUES: readonly number[] = [9806.65, 9.80665, 9.81, 9800];

/** 重力に相当する数値リテラル（{@link FORBIDDEN_GRAVITY_LITERAL_VALUES}）が現れないことを、解析済みの値で検査する。 */
export function checkB8NoGravityNumericLiterals(
  fileName: string,
  sourceText: string,
): readonly BoundaryViolation[] {
  const sourceFile = parseSource(fileName, sourceText);
  const violations: BoundaryViolation[] = [];
  forEachDescendant(sourceFile, (node) => {
    if (!ts.isNumericLiteral(node)) return;
    const raw = node.getText(sourceFile).replace(/_/g, "");
    const value = Number(raw);
    if (FORBIDDEN_GRAVITY_LITERAL_VALUES.includes(value)) {
      violations.push({
        file: fileName,
        line: lineOf(sourceFile, node),
        message: `重力定数に相当する数値リテラル: ${node.getText(sourceFile)}`,
      });
    }
  });
  return violations;
}

// ---------------------------------------------------------------------------
// B-9: package.json の dependencies が空
// ---------------------------------------------------------------------------

/** `package.json` の `dependencies` が空オブジェクトであることを検査する。 */
export function checkB9EmptyRuntimeDependencies(
  packageJson: Record<string, unknown>,
): readonly BoundaryViolation[] {
  const dependencies = packageJson["dependencies"];
  if (dependencies === undefined) {
    return [{ file: "package.json", line: 0, message: `dependencies フィールドが無い` }];
  }
  if (typeof dependencies !== "object" || dependencies === null || Array.isArray(dependencies)) {
    return [{ file: "package.json", line: 0, message: `dependencies がオブジェクトでない` }];
  }
  const keys = Object.keys(dependencies);
  if (keys.length !== 0) {
    return [{ file: "package.json", line: 0, message: `dependencies が空でない: ${keys.join(", ")}` }];
  }
  return [];
}

// ---------------------------------------------------------------------------
// B-10: 文字列リテラル中の断定語の禁止
// ---------------------------------------------------------------------------

export const FORBIDDEN_VERDICT_SUBSTRINGS_JA = ["合否", "合格", "不合格", "達成"] as const;

const NFR7_PATTERN = /nfr[\s-]*7/i; // "NFR-7" / "NFR 7" / "NFR7" のいずれも拾う（1.4 の申し送りと同じ方針）
const PASS_FAIL_WORD_BOUNDARY_PATTERN = /\b(pass|fail)\b/i; // "load-failure" 等の語の一部を誤検知しない

/** 文字列リテラルのテキストに断定語が含まれていれば、その語（表示用）を返す。 */
function findVerdictWord(text: string): string | null {
  for (const word of FORBIDDEN_VERDICT_SUBSTRINGS_JA) {
    if (text.includes(word)) return word;
  }
  if (NFR7_PATTERN.test(text)) return "NFR-7";
  const match = PASS_FAIL_WORD_BOUNDARY_PATTERN.exec(text);
  const word = match?.[1];
  if (word !== undefined) return word.toUpperCase();
  return null;
}

/**
 * 文字列リテラル（`StringLiteral` / `NoSubstitutionTemplateLiteral`）と、テンプレート式の
 * リテラル区間（`TemplateHead` / `TemplateMiddle` / `TemplateTail`。`${...}` の式部分は
 * 別の種類のノードであり、ここには含まれない）に断定語が現れないことを検査する。
 */
export function checkB10NoVerdictWordsInStrings(
  fileName: string,
  sourceText: string,
): readonly BoundaryViolation[] {
  const sourceFile = parseSource(fileName, sourceText);
  const violations: BoundaryViolation[] = [];
  forEachDescendant(sourceFile, (node) => {
    const isLiteralTextNode =
      ts.isStringLiteral(node) ||
      ts.isNoSubstitutionTemplateLiteral(node) ||
      ts.isTemplateHead(node) ||
      ts.isTemplateMiddle(node) ||
      ts.isTemplateTail(node);
    if (!isLiteralTextNode) return;
    const matched = findVerdictWord(node.text);
    if (matched !== null) {
      violations.push({
        file: fileName,
        line: lineOf(sourceFile, node),
        message: `文字列リテラルに断定語 "${matched}" が含まれる`,
      });
    }
  });
  return violations;
}
