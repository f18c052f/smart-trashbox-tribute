// 画面の組み立てと操作の結線（`app.ts`）を検証する
// （要件 1.1, 1.3, 1.7, 2.7, 4.4, 4.5, 5.5, 5.8, 6.1〜6.5, 8.4、設計 `#### App`）。
//
// 設計自身が述べるとおり、画面の結線は単体テストの対象外である
// （「だからこそロジックを置かない」）。ブラウザでの見た目・実際の FileReader の
// 非同期性・実ポインタ操作の確認は手動確認手順（設計 5.3、task 5.3）に委ねる。
// ここで固定するのは Node.js 上で決定的に検証できる範囲、すなわち
//   - ファイル選択 → 読み込み → 描画までの結線が実際に呼ばれること
//   - 読み込み失敗時に前の図が消えること
//   - 代表記録が無くても図の描画が続くこと（要件 1.7）
//   - 軸選択の変更が図だけを再描画し、前提と限界の host に触れないこと（要件 6.3）
//   - 再生ループが `requestAnimationFrame` 1 本で駆動し、停止・シーク・先頭復帰が動くこと
//   - 2 件目のファイル読み込みが状態を完全に置き換えること（要件 6.4 の裏付け）
//   - `fetch` / 永続化 API を一切参照しないこと（要件 6.5, 8.4）
//   - 断定語を自ら作らないこと（要件 3.6 相当）
//   - import 境界（層 0〜3 のみ）を守ること
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { startApp } from "../src/app.js";
import {
  makeMixedStatusDocument,
  makeSweepDocument,
  makeSweepDocumentWithRecords,
  makeThrowRecord,
  makeSamples,
  toJsonText,
  NOT_JSON_TEXT,
} from "./fixtures.js";
import {
  FakeDocument,
  FakeElement,
  FakeFileReader,
  makeAppDocument,
  type FakeFile,
} from "./dom-stub.js";

// `app.ts` は DOM 標準のグローバル `FileReader` を直接参照する（`Window` インタフェースに
// `FileReader` が無いという TypeScript の DOM 型定義の制約による。`src/app.ts` のコメント
// を参照）。Node.js のテスト環境にはブラウザの `FileReader` が無いため、ここで
// 手作りの `FakeFileReader` に差し替える。
(globalThis as unknown as { FileReader: unknown }).FileReader = FakeFileReader;

// --- 補助 -------------------------------------------------------------------

function asDocument(doc: FakeDocument): Document {
  return doc as unknown as Document;
}

function required<T>(value: T | undefined, message: string): T {
  if (value === undefined) {
    throw new Error(message);
  }
  return value;
}

function errorFrom(run: () => unknown): unknown {
  try {
    run();
  } catch (cause) {
    return cause;
  }
  throw new Error("例外が投げられなかった");
}

function makeJsonFile(document: unknown, name = "sweep.json"): FakeFile {
  return { name, text: toJsonText(document) };
}

/** ファイル選択を模す: `input.files` を設定し `change` を発火する。 */
function selectFile(mount: FakeElement, file: FakeFile): void {
  const input = required(
    mount.queryAllByClass("app-file-input")[0],
    "ファイル入力要素が無い",
  );
  input.files = [file];
  input.dispatchEvent("change");
}

function playButton(mount: FakeElement): FakeElement {
  return required(mount.queryAllByClass("app-play-button")[0], "再生ボタンが無い");
}
function stopButton(mount: FakeElement): FakeElement {
  return required(mount.queryAllByClass("app-stop-button")[0], "停止ボタンが無い");
}
function resetButton(mount: FakeElement): FakeElement {
  return required(mount.queryAllByClass("app-reset-button")[0], "先頭復帰ボタンが無い");
}
function seekInput(mount: FakeElement): FakeElement {
  return required(mount.queryAllByClass("app-seek-input")[0], "シーク入力が無い");
}

function trackTimeText(host: FakeElement): string {
  const node = required(
    host.queryAllByClass("track-time")[0],
    "再生時刻を表示する要素が無い",
  );
  return node.textContent;
}

const VIZ_ROOT = new URL("../../", import.meta.url);
function readCompiledAppSource(): string {
  return readFileSync(new URL("dist/src/app.js", VIZ_ROOT), "utf8");
}

// --- 1. 起動 ------------------------------------------------------------------

test("startApp は #app を持つ文書に対して例外を投げない", () => {
  const { doc, mount } = makeAppDocument();
  startApp(asDocument(doc));
  assert.ok(mount.childElementCount > 0, "何らかの骨格が組み立てられていない");
});

test("#app が無い文書では例外を投げる（設計 Error Strategy: host が無いのはプログラムの誤り）", () => {
  const doc = new FakeDocument();
  const thrown = errorFrom(() => startApp(asDocument(doc)));
  assert.ok(thrown instanceof Error, "例外が Error でない");
});

// --- 2. ファイル選択から描画まで（要件 1.1） ------------------------------------

test("有効なファイルを選ぶと、図・前提と限界・アニメーションが描画される", () => {
  const { doc, mount } = makeAppDocument();
  startApp(asDocument(doc));

  const document = makeSweepDocumentWithRecords();
  selectFile(mount, makeJsonFile(document));

  assert.ok(mount.queryAllByClass("region-cell").length > 0, "図が描画されていない");
  assert.ok(
    mount.queryAllByClass("context-calibration-banner").length > 0,
    "較正バナーが描画されていない",
  );
  assert.ok(mount.queryAllByClass("track-view").length === 2, "xy/xz のアニメーション面が揃っていない");
});

// --- 3. 読み込み失敗（要件 1.3） -------------------------------------------------

test("壊れたファイルを選ぶと、直前の図が消えて失敗内容が表示される", () => {
  const { doc, mount } = makeAppDocument();
  startApp(asDocument(doc));

  selectFile(mount, makeJsonFile(makeSweepDocumentWithRecords()));
  assert.ok(mount.queryAllByClass("region-cell").length > 0, "テスト前提: 先に図が描けているはず");

  selectFile(mount, { name: "broken.json", text: NOT_JSON_TEXT });

  assert.equal(mount.queryAllByClass("region-cell").length, 0, "直前の図が残っている");
  assert.ok(mount.queryAllByClass("load-failure").length > 0, "失敗内容が表示されていない");
});

// --- 4. 代表記録が無くても図は描く（要件 1.7） -----------------------------------

test("throw_records が無いスウィープでも図は描かれ、アニメーション面は利用不可を示す", () => {
  const { doc, mount } = makeAppDocument();
  startApp(asDocument(doc));

  selectFile(mount, makeJsonFile(makeSweepDocument()));

  assert.ok(mount.queryAllByClass("region-cell").length > 0, "代表記録が無くても図は描かれるはず（要件 1.7）");
  assert.ok(mount.queryAllByClass("track-unavailable").length === 2, "xy/xz の双方が利用不可を示すはず");
});

// --- 5. 軸選択は図だけを再描画する（要件 6.3） -----------------------------------

test("軸選択を変更しても、前提と限界パネルの内容は変わらない", () => {
  const { doc, mount } = makeAppDocument();
  startApp(asDocument(doc));

  const axes = [
    { name: "hold_time_ms", unit: "ms", values: [400, 600, 800] },
    { name: "required_distance_mm", unit: "mm", values: [300, 600, 900] },
    { name: "catch_policy", unit: "", values: ["stop_and_wait", "pass_through"] },
  ];
  const document = makeSweepDocumentWithRecords({
    sweep: {
      kind: "reachability",
      axes,
      trials_per_cell: 20,
      seed: 1,
      catch_ratio_threshold: 0.8,
    },
    cells: [],
  });
  selectFile(mount, makeJsonFile(document));

  const contextHost = required(
    mount.queryAllByClass("app-context-host")[0],
    "前提と限界パネルの host が無い",
  );
  const before = contextHost.textContent;
  // テキストの一致だけでは「内容は同じだが作り直した」再描画を見逃す
  // （`renderContext` は毎回 host を丸ごと作り直すため、同じ入力からは同じ文字列になる）。
  // 要素の同一性（参照の一致）まで見て、host に実際に触れていないことを固定する。
  const bannerBefore = required(
    mount.queryAllByClass("context-calibration-banner")[0],
    "較正バナーの要素が無い",
  );
  const regionHost = required(mount.queryAllByClass("app-region-host")[0], "図の host が無い");
  const regionTextBefore = regionHost.textContent;
  // 既定選択（xAxisIndex=0）では X 軸名は hold_time_ms のはず（テスト前提の確認も兼ねる）。
  assert.match(regionTextBefore, /hold_time_ms/, "テスト前提: 既定の X 軸が hold_time_ms のはず");

  const xSelect = required(
    mount.queryAllByClass("app-axis-x-select")[0],
    "X 軸選択が無い（3 軸掃引なので描かれるはず、要件 2.7）",
  );
  // 既定選択は xAxisIndex=0 / yAxisIndex=1。ここでは Y と衝突しない別の軸（index 2）へ切り替える。
  xSelect.value = "2";
  xSelect.dispatchEvent("change");

  const regionTextAfter = regionHost.textContent;
  assert.notEqual(regionTextAfter, regionTextBefore, "軸選択を変えたのに図の内容が変わっていない");
  assert.match(regionTextAfter, /catch_policy/, "軸選択の変更後の X 軸が catch_policy になっていない");
  assert.equal(contextHost.textContent, before, "軸選択の変更で前提と限界パネルが変わった（要件 6.3 違反）");

  const bannerAfter = required(
    mount.queryAllByClass("context-calibration-banner")[0],
    "軸選択の変更後に較正バナーの要素が無い",
  );
  // `node:assert/strict` の `equal` は厳密等価（`strictEqual` の別名）であるため、
  // オブジェクト参照の比較にもそのまま使える。
  assert.equal(
    bannerAfter,
    bannerBefore,
    "軸選択の変更で前提と限界パネルの host が作り直されている（内容が同じでも再描画してはいけない、要件 6.3）",
  );
});

// --- 6. 再生・停止・シーク・先頭復帰（要件 5.5） --------------------------------

test("再生を開始すると RAF のティックごとに再生時刻が進む", () => {
  const { doc, mount } = makeAppDocument();
  startApp(asDocument(doc));

  const samples = makeSamples(); // t_ms: 0, 30, 60, 90, 120
  const record = makeThrowRecord({ samples });
  selectFile(mount, makeJsonFile(makeSweepDocumentWithRecords({ throw_records: [record] })));

  const xyHost = required(mount.queryAllByClass("app-track-host--xy")[0], "xy host が無い");
  assert.match(trackTimeText(xyHost), /^0(\.000)? ms$/, "再生開始前の時刻が 0 でない");

  playButton(mount).dispatchEvent("click");
  doc.defaultView.flush(0); // 最初のティック: 経過 0
  doc.defaultView.flush(10); // 経過 10ms
  assert.match(trackTimeText(xyHost), /^10(\.000)? ms$/, "再生時刻が進んでいない");

  doc.defaultView.flush(25); // 経過 15ms
  assert.match(trackTimeText(xyHost), /^25(\.000)? ms$/, "再生時刻が正しく進んでいない");
});

test("停止すると、それ以上ティックが来ても時刻が進まない", () => {
  const { doc, mount } = makeAppDocument();
  startApp(asDocument(doc));

  selectFile(mount, makeJsonFile(makeSweepDocumentWithRecords({ throw_records: [makeThrowRecord()] })));
  const xyHost = required(mount.queryAllByClass("app-track-host--xy")[0], "xy host が無い");

  playButton(mount).dispatchEvent("click");
  doc.defaultView.flush(0);
  doc.defaultView.flush(10);
  const afterStart = trackTimeText(xyHost);

  stopButton(mount).dispatchEvent("click");
  assert.equal(doc.defaultView.pendingCount, 0, "停止したのに RAF が保留され続けている");
  doc.defaultView.flush(999);
  assert.equal(trackTimeText(xyHost), afterStart, "停止後も時刻が進んでいる");
});

test("シークで任意時刻へ移動できる（再生・停止を経由しなくてよい）", () => {
  const { doc, mount } = makeAppDocument();
  startApp(asDocument(doc));

  selectFile(
    mount,
    makeJsonFile(makeSweepDocumentWithRecords({ throw_records: [makeThrowRecord({ samples: makeSamples() })] })),
  );
  const xyHost = required(mount.queryAllByClass("app-track-host--xy")[0], "xy host が無い");

  const seek = seekInput(mount);
  seek.value = "60";
  seek.dispatchEvent("input");

  assert.match(trackTimeText(xyHost), /^60(\.000)? ms$/, "シークが反映されていない");
});

test("先頭復帰は再生時刻を startTimeMs へ戻す", () => {
  const { doc, mount } = makeAppDocument();
  startApp(asDocument(doc));

  selectFile(
    mount,
    makeJsonFile(makeSweepDocumentWithRecords({ throw_records: [makeThrowRecord({ samples: makeSamples() })] })),
  );
  const xyHost = required(mount.queryAllByClass("app-track-host--xy")[0], "xy host が無い");

  const seek = seekInput(mount);
  seek.value = "90";
  seek.dispatchEvent("input");
  assert.match(trackTimeText(xyHost), /^90(\.000)? ms$/, "テスト前提: シークが効いているはず");

  resetButton(mount).dispatchEvent("click");
  assert.match(trackTimeText(xyHost), /^0(\.000)? ms$/, "先頭復帰が effectively していない");
});

test("再生が終端に達すると自動的に停止し、再生を押しても自動的に先頭へは戻らない", () => {
  const { doc, mount } = makeAppDocument();
  startApp(asDocument(doc));

  selectFile(
    mount,
    makeJsonFile(makeSweepDocumentWithRecords({ throw_records: [makeThrowRecord({ samples: makeSamples() })] })),
  );
  const xyHost = required(mount.queryAllByClass("app-track-host--xy")[0], "xy host が無い");

  playButton(mount).dispatchEvent("click");
  doc.defaultView.flush(0);
  doc.defaultView.flush(1000); // 記録の終端(120ms)をはるかに超える経過

  assert.match(trackTimeText(xyHost), /^120(\.000)? ms$/, "終端で境界に寄せられていない");
  assert.equal(doc.defaultView.pendingCount, 0, "終端到達後も RAF が保留されている（自動停止していない）");

  // 終端にいる状態で再生を押しても、自動的に先頭へループしない（保守的な選択、CONCERNS 参照）。
  playButton(mount).dispatchEvent("click");
  assert.equal(doc.defaultView.pendingCount, 0, "終端で再生を押すと自動的に何かが起きてしまっている");
  assert.match(trackTimeText(xyHost), /^120(\.000)? ms$/, "終端で再生を押した後も時刻が変わってはいけない");
});

// --- 7. 記録選択はアニメーション面だけを再描画する（要件 5.8） ------------------

test("記録を切り替えると、アニメーション面だけが新しい記録の再生開始時刻に戻る", () => {
  const { doc, mount } = makeAppDocument();
  startApp(asDocument(doc));

  const firstRecord = makeThrowRecord({ record_id: "record-a", samples: makeSamples() });
  const secondRecord = makeThrowRecord({
    record_id: "record-b",
    samples: [10, 40, 70].map((t, index) => ({ t_ms: t, x_mm: index * 50, y_mm: 0, z_mm: 1000 })),
  });
  selectFile(
    mount,
    makeJsonFile(makeSweepDocumentWithRecords({ throw_records: [firstRecord, secondRecord] })),
  );

  const contextHost = required(mount.queryAllByClass("app-context-host")[0], "前提と限界パネルの host が無い");
  const contextBefore = contextHost.textContent;
  const regionBefore = required(mount.queryAllByClass("app-region-host")[0], "図の host が無い").textContent;

  const recordSelect = required(mount.queryAllByClass("app-record-select")[0], "記録選択が無い");
  recordSelect.value = "1";
  recordSelect.dispatchEvent("change");

  const xyHost = required(mount.queryAllByClass("app-track-host--xy")[0], "xy host が無い");
  assert.match(trackTimeText(xyHost), /^10(\.000)? ms$/, "新しい記録の開始時刻(10ms)に戻っていない");
  assert.equal(contextHost.textContent, contextBefore, "記録選択の変更で前提と限界パネルが変わった");
  assert.equal(
    required(mount.queryAllByClass("app-region-host")[0], "図の host が無い").textContent,
    regionBefore,
    "記録選択の変更で図が変わった",
  );
});

// --- 8. 2 件目のファイルは状態を完全に置き換える（要件 6.4 の裏付け） -----------

test("2 件目のファイルを選ぶと、1 件目の図が残らず完全に置き換わる", () => {
  const { doc, mount } = makeAppDocument();
  startApp(asDocument(doc));

  const firstDocument = makeMixedStatusDocument();
  selectFile(mount, makeJsonFile(firstDocument));
  const firstCellCount = mount.queryAllByClass("region-cell").length;
  assert.ok(firstCellCount > 0, "テスト前提: 1 件目の図が描けているはず");

  const secondAxes = [{ name: "hold_time_ms", unit: "ms", values: [500] }];
  const secondDocument = makeSweepDocument({
    sweep: {
      kind: "reachability",
      axes: secondAxes,
      trials_per_cell: 5,
      seed: 2,
      catch_ratio_threshold: null,
    },
    cells: [
      {
        axis_values: [500],
        status: "catchable",
        success_ratio: 1,
        metrics: {},
        not_evaluated_reason: null,
      },
    ],
  });
  selectFile(mount, makeJsonFile(secondDocument));

  const svgs = mount.queryAllByTag("svg").filter((el) => el.classList.contains("region-figure"));
  assert.equal(svgs.length, 1, "図が複数並置されている（自動比較を作ってはいけない、要件 4.5）");
  assert.equal(mount.queryAllByClass("region-cell").length, 1, "1 件目の格子点が残っている");
});

// --- 9. 通信・永続化を一切使わない（要件 6.5 / 8.4） -----------------------------

test("コンパイル出力に fetch / XMLHttpRequest / 永続化 API への参照が無い", () => {
  const source = readCompiledAppSource();
  assert.doesNotMatch(source, /\bfetch\s*\(/, "fetch を使っている");
  assert.doesNotMatch(source, /XMLHttpRequest/, "XMLHttpRequest を使っている");
  assert.doesNotMatch(source, /localStorage/, "localStorage を使っている");
  assert.doesNotMatch(source, /sessionStorage/, "sessionStorage を使っている");
  assert.doesNotMatch(source, /document\.cookie/, "document.cookie を使っている");
});

// --- 10. 断定語を自ら作らない（要件 3.6 相当） -----------------------------------

test("app.ts のソースに断定語が現れない", () => {
  const source = readFileSync(new URL("src/app.ts", VIZ_ROOT), "utf8");
  // コメント中の要件説明ではなく、文字列リテラルに現れないことを見る。
  const stringLiterals = [...source.matchAll(/"((?:[^"\\]|\\.)*)"/g)].map((m) => m[1] ?? "");
  const forbidden = ["合格", "不合格", "合否", "達成", "NFR-7", "PASS", "FAIL"];
  for (const literal of stringLiterals) {
    for (const word of forbidden) {
      assert.doesNotMatch(literal, new RegExp(word), `文字列 "${literal}" に断定語 "${word}" が含まれる`);
    }
  }
});

// --- 11. import 境界（層 0〜3 のみ） ----------------------------------------------

test("コンパイル出力が import してよいのは層 0〜3 のみで、main.js を import しない", () => {
  const source = readCompiledAppSource();
  const specifiers = [...source.matchAll(/from\s*["']([^"']+)["']/g)].map((m) => m[1] ?? "");
  assert.ok(specifiers.length > 0, "import 文が 1 つも無い");
  for (const specifier of specifiers) {
    assert.doesNotMatch(specifier, /\bmain\.js$/, `main.js を import している: ${specifier}`);
    assert.ok(
      /^\.\/(schema|scale|format|load)\.js$/.test(specifier)
        || /^\.\/plan\//.test(specifier)
        || /^\.\/view\//.test(specifier),
      `許可されていない import: ${specifier}`,
    );
  }
});

test("app.ts のコンパイル出力が非線形の算術（sqrt/pow/三角関数）を持たない（境界検査 B-5 相当）", () => {
  const source = readCompiledAppSource();
  assert.doesNotMatch(source, /Math\.(sqrt|pow|sin|cos|tan|atan2|log|exp)\b/);
});

test("app.ts のコンパイル出力に補間の再実装（lerp 相当の宣言）が無い（境界検査 B-7 相当）", () => {
  const source = readCompiledAppSource();
  assert.doesNotMatch(source, /function\s+lerp\b/, "lerp を再宣言している");
});
