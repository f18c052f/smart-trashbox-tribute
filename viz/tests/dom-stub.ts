// `view/render.ts` を実 DOM 無しで検証するための、最小の手作りテスト用スタブ。
//
// 開発時依存を TypeScript コンパイラ 1 個に保つ制約（要件 7.4）のもとでは jsdom 等の
// サードパーティを導入できない。そこでこのファイルは `render.ts` が実際に呼び出す
// DOM API（`createElementNS` / `createElement` / `setAttribute` / `appendChild` /
// `replaceChildren` / `classList` / `textContent`）だけを最小限に実装する。
//
// 本番コード（`src/**`）はこのファイルを import しない。テストの土台であり、
// `viz/tests/fixtures.ts` と同じ扱いである。
//
// **グローバルな `document` は用意しない。** `render.ts` は渡された `host` の
// `ownerDocument` からしか要素を作れない構造でなければならず、グローバル参照へ
// 逃げれば Node.js 上で `ReferenceError` になってテストが落ちる。これ自体が
// 「`host` の外側（`document.body` など）へ到達しない」ことの検査になる。

export const SVG_NS = "http://www.w3.org/2000/svg";
export const HTML_NS = "http://www.w3.org/1999/xhtml";

// --- app.ts 検証用の追加スタブ ------------------------------------------------
//
// `app.ts` は `render.ts` と異なり、要素の生成だけでなく `<input>` / `<select>` /
// `<button>` の値・イベント、`FileReader`、`requestAnimationFrame` を扱う。
// 以下はそれらを検証するために追加した最小限の実装であり、`render.ts` 側の
// 既存の型・挙動（`FakeElement` / `FakeDocument` / `makeHost`）は変更しない。

/** `addEventListener` へ渡されるイベントの最小形。 */
export interface FakeEvent {
  readonly target: FakeElement;
}

/** `FileReader` が読む対象の最小形。 */
export interface FakeFile {
  readonly name: string;
  readonly text: string;
  /** 実 FileReader が読み取りに失敗する場合を模すためのテスト専用フラグ。 */
  readonly failToRead?: boolean;
}

/** 開発時依存を増やさない、`FileReader` の最小実装。読み出しは同期で行う。
 * `app.ts` 側は `onload` / `onerror` コールバックだけを使うため、
 * 実際のブラウザが非同期であることに依存したコードにはならない。 */
export class FakeFileReader {
  result: string | null = null;
  error: { readonly message: string } | null = null;
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;

  readAsText(file: FakeFile): void {
    if (file.failToRead === true) {
      this.result = null;
      this.error = { message: "読み取りに失敗した（テスト用スタブ）" };
      this.onerror?.();
      return;
    }
    this.result = file.text;
    this.error = null;
    this.onload?.();
  }
}

/** `requestAnimationFrame` / `cancelAnimationFrame` の最小実装。
 * 実行はしない。`flush(time)` を呼んだときにだけ、その時点で保留中の
 * コールバックだけを 1 回分として実行する（実 RAF の「次のフレームへ回す」
 * 挙動を模す。`flush` 実行中に新たに登録されたコールバックは含めない）。 */
export class FakeWindow {
  readonly FileReader = FakeFileReader;

  private nextHandle = 1;
  private readonly pending = new Map<number, (time: number) => void>();

  requestAnimationFrame(callback: (time: number) => void): number {
    const handle = this.nextHandle;
    this.nextHandle += 1;
    this.pending.set(handle, callback);
    return handle;
  }

  cancelAnimationFrame(handle: number): void {
    this.pending.delete(handle);
  }

  /** テスト用: 保留中のコールバックの個数。 */
  get pendingCount(): number {
    return this.pending.size;
  }

  /** テスト用: 保留中のコールバックを 1 回分だけ実行する。 */
  flush(time: number): void {
    const callbacks = [...this.pending.values()];
    this.pending.clear();
    for (const callback of callbacks) {
      callback(time);
    }
  }
}

/** 実 `DOMTokenList` は空文字列トークンと、空白文字を含むトークンを拒否する
 * （前者は `SyntaxError`、後者は `InvalidCharacterError`）。本スタブが緩いままだと、
 * `render.ts` 側で複合クラス名文字列をそのまま `classList.add` へ渡す不具合が
 * テストで検出できない（実際に起きた不具合）。ここで同じ検証を行う。 */
function assertValidToken(name: string): void {
  if (name.length === 0) {
    throw new Error("SyntaxError: classList のトークンは空文字列にできない（テスト用スタブ）");
  }
  if (/\s/.test(name)) {
    throw new Error(
      `InvalidCharacterError: classList のトークンに空白は含められない（テスト用スタブ）: "${name}"`,
    );
  }
}

/** `element.classList` の最小実装。 */
class FakeClassList {
  private readonly tokens = new Set<string>();

  add(...names: readonly string[]): void {
    for (const name of names) {
      assertValidToken(name);
    }
    for (const name of names) {
      this.tokens.add(name);
    }
  }

  remove(...names: readonly string[]): void {
    for (const name of names) {
      assertValidToken(name);
    }
    for (const name of names) {
      this.tokens.delete(name);
    }
  }

  // 実 `DOMTokenList.contains` はトークンの妥当性検証を行わない（`add` / `remove` /
  // `toggle` と異なり、空文字列・空白入りの引数でも例外を投げず単に false を返す）。
  // そのためここでは検証しない。
  contains(name: string): boolean {
    return this.tokens.has(name);
  }

  toArray(): readonly string[] {
    return [...this.tokens];
  }
}

/** `Element` のうち `render.ts` が実際に使う操作だけを持つ最小のノード。 */
export class FakeElement {
  readonly tagName: string;
  readonly namespaceURI: string;
  readonly ownerDocument: FakeDocument;
  readonly classList = new FakeClassList();

  // `<input>` / `<select>` / `<button>` 相当の操作に使う、テスト用の最小プロパティ。
  // `render.ts` は使わないため、`render.test.ts` の挙動には影響しない。
  value = "";
  disabled = false;
  files: readonly FakeFile[] = [];

  private readonly attributes = new Map<string, string>();
  private children: FakeElement[] = [];
  private text = "";
  private readonly listeners = new Map<string, Set<(event: FakeEvent) => void>>();

  constructor(tagName: string, namespaceURI: string, ownerDocument: FakeDocument) {
    this.tagName = tagName;
    this.namespaceURI = namespaceURI;
    this.ownerDocument = ownerDocument;
  }

  setAttribute(name: string, value: string): void {
    this.attributes.set(name, String(value));
  }

  getAttribute(name: string): string | null {
    return this.attributes.has(name) ? (this.attributes.get(name) ?? null) : null;
  }

  hasAttribute(name: string): boolean {
    return this.attributes.has(name);
  }

  appendChild(child: FakeElement): FakeElement {
    this.children.push(child);
    return child;
  }

  append(...nodes: readonly FakeElement[]): void {
    for (const node of nodes) {
      this.appendChild(node);
    }
  }

  replaceChildren(...nodes: readonly FakeElement[]): void {
    this.children = [...nodes];
  }

  addEventListener(type: string, handler: (event: FakeEvent) => void): void {
    let handlers = this.listeners.get(type);
    if (handlers === undefined) {
      handlers = new Set();
      this.listeners.set(type, handlers);
    }
    handlers.add(handler);
  }

  removeEventListener(type: string, handler: (event: FakeEvent) => void): void {
    this.listeners.get(type)?.delete(handler);
  }

  /** テスト側からユーザー操作を模すための発火。実 DOM の `dispatchEvent` と違い、
   * イベントオブジェクトは `{ target: this }` の最小形で足りる（`app.ts` は
   * `event.target` 以外を読まない）。 */
  dispatchEvent(type: string): void {
    const event: FakeEvent = { target: this };
    for (const handler of [...(this.listeners.get(type) ?? [])]) {
      handler(event);
    }
  }

  get children_(): readonly FakeElement[] {
    return this.children;
  }

  get childElementCount(): number {
    return this.children.length;
  }

  get firstElementChild(): FakeElement | null {
    return this.children[0] ?? null;
  }

  set textContent(value: string) {
    this.text = value;
    // 実 DOM と同じく、textContent への代入は既存の子要素をすべて置き換える。
    this.children = [];
  }

  get textContent(): string {
    if (this.children.length === 0) {
      return this.text;
    }
    return this.children.map((child) => child.textContent).join("");
  }

  /** 深さ優先ですべての子孫を辿る。テストの走査専用で、`render.ts` からは呼ばれない。 */
  *descendants(): Generator<FakeElement> {
    for (const child of this.children) {
      yield child;
      yield* child.descendants();
    }
  }

  /** クラス名だけを条件にした、テスト用の最小限の子孫検索。 */
  queryAllByClass(className: string): readonly FakeElement[] {
    return [...this.descendants()].filter((node) => node.classList.contains(className));
  }

  /** タグ名だけを条件にした、テスト用の最小限の子孫検索。 */
  queryAllByTag(tagName: string): readonly FakeElement[] {
    return [...this.descendants()].filter((node) => node.tagName === tagName);
  }
}

/** `Document` のうち `render.ts` / `app.ts` が実際に使う生成 API だけを持つ最小の文書。 */
export class FakeDocument {
  creationCount = 0;
  /** `root.defaultView` 経由で RAF / FileReader を取り出す `app.ts` の方針に合わせたスタブ。 */
  readonly defaultView = new FakeWindow();

  private lookupRoot: FakeElement | null = null;

  createElementNS(namespaceURI: string, tagName: string): FakeElement {
    this.creationCount += 1;
    return new FakeElement(tagName, namespaceURI, this);
  }

  createElement(tagName: string): FakeElement {
    return this.createElementNS(HTML_NS, tagName);
  }

  /** `getElementById` が辿る木の根を登録する（`app.ts` の `#app` 取得用）。 */
  setLookupRoot(element: FakeElement): void {
    this.lookupRoot = element;
  }

  getElementById(id: string): FakeElement | null {
    if (this.lookupRoot === null) {
      return null;
    }
    if (this.lookupRoot.getAttribute("id") === id) {
      return this.lookupRoot;
    }
    for (const node of this.lookupRoot.descendants()) {
      if (node.getAttribute("id") === id) {
        return node;
      }
    }
    return null;
  }
}

/** テスト 1 件分の、孤立した `host` と、その生成回数を数える文書を作る。 */
export function makeHost(): { readonly host: FakeElement; readonly doc: FakeDocument } {
  const doc = new FakeDocument();
  const host = doc.createElement("div");
  // ホスト自身の生成はテスト対象コードの呼び出しではないため、カウントから除く。
  doc.creationCount = 0;
  return { host, doc };
}

/** `app.ts` の `startApp(root)` を検証するための、`#app` を持つ文書一式を作る。 */
export function makeAppDocument(): { readonly doc: FakeDocument; readonly mount: FakeElement } {
  const doc = new FakeDocument();
  const mount = doc.createElement("main");
  mount.setAttribute("id", "app");
  doc.setLookupRoot(mount);
  doc.creationCount = 0;
  return { doc, mount };
}
