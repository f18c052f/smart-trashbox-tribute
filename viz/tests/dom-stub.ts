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

/** `element.classList` の最小実装。 */
class FakeClassList {
  private readonly tokens = new Set<string>();

  add(...names: readonly string[]): void {
    for (const name of names) {
      this.tokens.add(name);
    }
  }

  remove(...names: readonly string[]): void {
    for (const name of names) {
      this.tokens.delete(name);
    }
  }

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

  private readonly attributes = new Map<string, string>();
  private children: FakeElement[] = [];
  private text = "";

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

  replaceChildren(...nodes: readonly FakeElement[]): void {
    this.children = [...nodes];
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

/** `Document` のうち `render.ts` が実際に使う生成 API だけを持つ最小の文書。 */
export class FakeDocument {
  creationCount = 0;

  createElementNS(namespaceURI: string, tagName: string): FakeElement {
    this.creationCount += 1;
    return new FakeElement(tagName, namespaceURI, this);
  }

  createElement(tagName: string): FakeElement {
    return this.createElementNS(HTML_NS, tagName);
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
