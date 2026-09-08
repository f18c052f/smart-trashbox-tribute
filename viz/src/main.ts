// エントリポイント。表示先の要素を取り出すところまでを担う。
// 画面の組み立ては app.ts が持つ（依存方向の表のとおり、main.ts が取り込んでよいのは app のみ）。

import { startApp } from "./app.js";

const ROOT_ELEMENT_ID = "app";

function rootElement(host: Document): Element | null {
  return host.getElementById(ROOT_ELEMENT_ID);
}

// 表示先の要素が無いのは HTML と実装の食い違い、すなわちプログラムの誤りである。
// Error Strategy の区分どおり例外としてそのまま伝播させる（握りつぶさない・コンソールに頼らない）。
const root = rootElement(document);
if (root === null) {
  throw new Error(`表示先の要素 #${ROOT_ELEMENT_ID} が見つからない`);
}

startApp(document);

export {};
