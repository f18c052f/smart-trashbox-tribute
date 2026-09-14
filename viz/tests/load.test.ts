// 未検証のテキストを検証済みの表示用ビューへ変換する経路を検証する
// （要件 1.2 / 1.3 / 1.4 / 1.5 / 1.7、設計 `#### Loader`）。
//
// ここで固定するのは次の 6 点である。
//   1. 必須 7 項目のいずれが欠けても**図を描ける状態を作らない**（要件 1.2）。
//      欠落は 1 つずつ落とした 7 通りすべてで確かめる
//   2. 欠落・型不一致は**最初の 1 件で打ち切らず、すべて列挙する**
//      （設計 Error Handling「最初の 1 件で打ち切らない」）
//   3. 解釈できない入力・オブジェクトでない入力が `not_json` / `not_object` になる（要件 1.3）
//   4. 未知の列挙値は**エラー**である。黙って丸めれば上流の変更が表示側で見えなくなる
//      （設計 Loader の Implementation Notes）
//   5. 出力形式の版の不一致は**警告**であり、読み込みは成功する（要件 1.4）
//   6. 代表記録の欠如・破損は**記録側の問題**にとどまり、図の読み込みは成功する（要件 1.7）
//
// 入力は `fixtures.ts` が組み立てる。正しい値は型検査を通る形で作り、
// 壊れた値は素のオブジェクトへ落としてから壊す。テストは `as any` を書かない。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  loadSweep,
  type LoadIssue,
  type LoadResult,
  type SweepView,
} from "../src/load.js";
import { REQUIRED_TOP_LEVEL_KEYS, type Calibration } from "../src/schema.js";
import {
  JSON_ARRAY_TEXT,
  JSON_NULL_TEXT,
  NOT_JSON_TEXT,
  makeSweepDocument,
  makeSweepDocumentWithRecords,
  omitKey,
  setKey,
  toJsonText,
  toPlainObject,
  type PlainDocument,
} from "./fixtures.js";

// --- 補助 -------------------------------------------------------------------

const FILE_NAME = "sweep-reachability.json";

/** 問題を「種別 と 場所」の 1 行へ落とす。詳細文はここでは比べない。 */
function signaturesOf(issues: readonly LoadIssue[]): readonly string[] {
  return issues.map((issue) => `${issue.code} ${issue.path}`);
}

function codesOf(issues: readonly LoadIssue[]): readonly string[] {
  return issues.map((issue) => issue.code);
}

function pathsWithCode(issues: readonly LoadIssue[], code: LoadIssue["code"]): readonly string[] {
  return issues.filter((issue) => issue.code === code).map((issue) => issue.path);
}

/** 値を JSON 文字列へ落としてから読み込む。ファイルには書き出さない。 */
function load(value: unknown, fileName: string = FILE_NAME): LoadResult {
  return loadSweep(toJsonText(value), fileName);
}

/** 成功した結果を取り出す。失敗していればその場で落とす。 */
function okOf(result: LoadResult): {
  readonly view: SweepView;
  readonly warnings: readonly LoadIssue[];
} {
  if (!result.ok) {
    throw new Error(`読み込みが成功しなかった: ${signaturesOf(result.errors).join(" / ")}`);
  }
  return result;
}

/** 失敗した結果の問題一覧を取り出す。成功していればその場で落とす。 */
function errorsOf(result: LoadResult): readonly LoadIssue[] {
  if (result.ok) {
    throw new Error("壊れた入力の読み込みが失敗しなかった");
  }
  return result.errors;
}

/** 素のオブジェクトの中を降りる。降りられなければテストの前提が崩れている。 */
function objectAt(value: unknown, label: string): PlainDocument {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${label} がオブジェクトでない`);
  }
  return value as PlainDocument;
}

function arrayAt(value: unknown, label: string): unknown[] {
  if (!Array.isArray(value)) {
    throw new Error(`${label} が配列でない`);
  }
  return value;
}

/** 必須項目をすべて備えた、書き換え可能な掃引結果。 */
function plainDocument(): PlainDocument {
  return toPlainObject(makeSweepDocument());
}

/** 代表記録を 1 件持つ、書き換え可能な掃引結果。 */
function plainDocumentWithRecords(): PlainDocument {
  return toPlainObject(makeSweepDocumentWithRecords());
}

function cellAt(document: PlainDocument, index: number): PlainDocument {
  return objectAt(arrayAt(document["cells"], "cells")[index], `cells[${index}]`);
}

function firstRecord(document: PlainDocument): PlainDocument {
  return objectAt(arrayAt(document["throw_records"], "throw_records")[0], "throw_records[0]");
}

// --- 必須項目の欠落（要件 1.2） ---------------------------------------------

test("必須項目を 1 つ落とした入力がすべて失敗し、欠落項目名が現れる（要件 1.2）", () => {
  assert.equal(REQUIRED_TOP_LEVEL_KEYS.length, 7);
  for (const key of REQUIRED_TOP_LEVEL_KEYS) {
    const errors = errorsOf(load(omitKey(plainDocument(), key)));
    assert.deepEqual(
      pathsWithCode(errors, "missing_key"),
      [key],
      `${key} の欠落が欠落として現れない`,
    );
  }
});

test("必須項目が複数欠けていれば、最初の 1 件で打ち切らずすべて列挙する（要件 1.2）", () => {
  const dropped = ["calibration", "sweep", "cells"];
  let plain = plainDocument();
  for (const key of dropped) {
    plain = omitKey(plain, key);
  }
  const errors = errorsOf(load(plain));
  assert.deepEqual([...pathsWithCode(errors, "missing_key")].sort(), [...dropped].sort());
  assert.equal(errors.length, dropped.length, "欠落以外の問題が混ざっている");
});

test("入れ子の必須項目の欠落も場所とともに現れる", () => {
  const plain = plainDocument();
  const cell = cellAt(plain, 0);
  delete cell["status"];
  assert.deepEqual(signaturesOf(errorsOf(load(plain))), ["missing_key cells[0].status"]);
});

// --- 解釈できない入力（要件 1.3） -------------------------------------------

test("JSON として解釈できない入力を not_json として返す（要件 1.3）", () => {
  for (const text of [NOT_JSON_TEXT, ""]) {
    assert.deepEqual(codesOf(errorsOf(loadSweep(text, FILE_NAME))), ["not_json"]);
  }
});

test("JSON ではあるがオブジェクトでない入力を not_object として返す（要件 1.3）", () => {
  for (const text of [JSON_ARRAY_TEXT, JSON_NULL_TEXT]) {
    assert.deepEqual(codesOf(errorsOf(loadSweep(text, FILE_NAME))), ["not_object"]);
  }
});

// --- 型の食い違い -----------------------------------------------------------

test("最上位の型の食い違いを場所とともに返す", () => {
  const errors = errorsOf(load(setKey(plainDocument(), "cells", {})));
  assert.deepEqual(signaturesOf(errors), ["wrong_type cells"]);
});

test("入れ子の型の食い違いを場所とともに返す", () => {
  const plain = plainDocument();
  objectAt(plain["sweep"], "sweep")["trials_per_cell"] = "20";
  assert.deepEqual(signaturesOf(errorsOf(load(plain))), ["wrong_type sweep.trials_per_cell"]);
});

test("格子点の型の食い違いを添字付きの場所として返す", () => {
  const plain = plainDocument();
  cellAt(plain, 3)["status"] = 5;
  assert.deepEqual(signaturesOf(errorsOf(load(plain))), ["wrong_type cells[3].status"]);
});

test("段ごとの除外要因の型の食い違いを場所とともに返す", () => {
  const plain = plainDocument();
  const exclusions = objectAt(plain["model_exclusions"], "model_exclusions");
  arrayAt(exclusions["throw_physics"], "throw_physics")[1] = 7;
  assert.deepEqual(
    signaturesOf(errorsOf(load(plain))),
    ["wrong_type model_exclusions[\"throw_physics\"][1]"],
  );
});

test("型の食い違いが複数あれば、最初の 1 件で打ち切らずすべて列挙する", () => {
  const plain = plainDocument();
  objectAt(plain["sweep"], "sweep")["seed"] = "12345";
  objectAt(plain["calibration"], "calibration")["notice"] = 3;
  cellAt(plain, 1)["success_ratio"] = "0.5";
  cellAt(plain, 4)["metrics"] = [];
  const errors = errorsOf(load(plain));
  assert.deepEqual([...signaturesOf(errors)].sort(), [
    "wrong_type calibration.notice",
    "wrong_type cells[1].success_ratio",
    "wrong_type cells[4].metrics",
    "wrong_type sweep.seed",
  ]);
});

// --- 未知の列挙値 -----------------------------------------------------------

interface EnumCase {
  readonly label: string;
  readonly path: string;
  readonly apply: (document: PlainDocument) => void;
}

// 上流の列挙値が増えたとき、型は狭いまま実行時に未知の値が来る。その隙間を塞ぐ経路。
const UNKNOWN_ENUM_CASES: readonly EnumCase[] = [
  {
    label: "格子点の状態",
    path: "cells[0].status",
    apply: (document) => {
      cellAt(document, 0)["status"] = "probably_catchable";
    },
  },
  {
    label: "評価対象外の理由",
    path: "cells[2].not_evaluated_reason",
    apply: (document) => {
      cellAt(document, 2)["not_evaluated_reason"] = "no_idea";
    },
  },
  {
    label: "較正段階",
    path: "calibration.stage",
    apply: (document) => {
      objectAt(document["calibration"], "calibration")["stage"] = "m3_calibrated";
    },
  },
  {
    label: "パラメータの出所",
    path: "parameter_provenance[\"throw.speed_mm_s\"]",
    apply: (document) => {
      objectAt(document["parameter_provenance"], "parameter_provenance")["throw.speed_mm_s"] =
        "guessed";
    },
  },
  {
    label: "掃引の種別",
    path: "sweep.kind",
    apply: (document) => {
      objectAt(document["sweep"], "sweep")["kind"] = "landing";
    },
  },
];

test("未知の列挙値を失敗として扱う（丸めれば上流の変更が表示側で見えなくなる）", () => {
  for (const enumCase of UNKNOWN_ENUM_CASES) {
    const plain = plainDocument();
    enumCase.apply(plain);
    assert.deepEqual(
      signaturesOf(errorsOf(load(plain))),
      [`unknown_enum_value ${enumCase.path}`],
      `${enumCase.label} の未知の値が失敗にならない`,
    );
  }
});

test("評価対象外の理由は null を取りうる", () => {
  const plain = plainDocument();
  cellAt(plain, 2)["not_evaluated_reason"] = null;
  okOf(load(plain));
});

// --- 出力形式の版（要件 1.4） -----------------------------------------------

test("出力形式の版が一致すれば警告を返さない（要件 1.4）", () => {
  const { warnings } = okOf(load(plainDocument()));
  assert.deepEqual([...warnings], []);
});

test("出力形式の版の不一致を警告として返し、読み込みは成功させる（要件 1.4）", () => {
  const plain = setKey(plainDocument(), "output_schema_version", "9.9");
  const { view, warnings } = okOf(load(plain));
  assert.deepEqual(signaturesOf(warnings), ["schema_version_mismatch output_schema_version"]);
  assert.equal(view.document.output_schema_version, "9.9");
});

// --- 代表記録（要件 1.7） ---------------------------------------------------

test("代表記録が無くても読み込みは成功し、記録側の問題として返す（要件 1.7）", () => {
  const { view } = okOf(load(plainDocument()));
  assert.equal(view.recordsIssue?.code, "records_unusable");
  assert.deepEqual([...view.records], []);
});

test("代表記録が配列でなくても読み込みは成功し、記録側の問題として返す（要件 1.7）", () => {
  const plain = setKey(plainDocumentWithRecords(), "throw_records", { count: 1 });
  const { view } = okOf(load(plain));
  assert.equal(view.recordsIssue?.code, "records_unusable");
  assert.equal(view.recordsIssue?.path, "throw_records");
  assert.deepEqual([...view.records], []);
});

test("代表記録が空の配列でも読み込みは成功し、記録側の問題として返す（要件 1.7）", () => {
  const plain = setKey(plainDocumentWithRecords(), "throw_records", []);
  const { view } = okOf(load(plain));
  assert.equal(view.recordsIssue?.code, "records_unusable");
  assert.deepEqual([...view.records], []);
});

test("代表記録の要素が読めなければ、場所を添えて記録側の問題として返す（要件 1.7）", () => {
  const plain = plainDocumentWithRecords();
  const samples = arrayAt(firstRecord(plain)["samples"], "samples");
  objectAt(samples[1], "samples[1]")["t_ms"] = "30";
  const { view } = okOf(load(plain));
  assert.equal(view.recordsIssue?.code, "records_unusable");
  assert.equal(view.recordsIssue?.path, "throw_records[0].samples[1].t_ms");
  assert.deepEqual([...view.records], []);
});

test("予測の種別が未知であれば、記録側の問題として返す（要件 1.7）", () => {
  const plain = plainDocumentWithRecords();
  const predictions = arrayAt(firstRecord(plain)["predictions"], "predictions");
  objectAt(predictions[0], "predictions[0]")["kind"] = "maybe";
  const { view } = okOf(load(plain));
  assert.equal(view.recordsIssue?.code, "records_unusable");
  assert.deepEqual([...view.records], []);
});

test("整った代表記録は問題なしで読み込まれる（要件 1.7）", () => {
  const { view } = okOf(load(makeSweepDocumentWithRecords()));
  assert.equal(view.recordsIssue, null);
  assert.equal(view.records.length, 1);
  assert.equal(view.records[0]?.record_id, "sweep-cell-0007-trial-000");
  assert.equal(view.records[0]?.samples.length, 5);
  assert.equal(view.records[0]?.predictions.length, 3);
});

test("記録の未知の無効理由は握りつぶさず、そのまま読み込む（設計 Error Handling）", () => {
  const plain = plainDocumentWithRecords();
  const predictions = arrayAt(firstRecord(plain)["predictions"], "predictions");
  objectAt(predictions[0], "predictions[0]")["reason"] = "brand_new_reason";
  const { view } = okOf(load(plain));
  assert.equal(view.recordsIssue, null);
  assert.equal(view.records.length, 1);
});

// --- 入力に無い値を作らない（要件 1.5） -------------------------------------

test("読み込んだ値をそのまま保持し、入力に無い値を作らない（要件 1.5）", () => {
  const plain = plainDocument();
  const { view } = okOf(load(plain));
  assert.deepEqual(view.document, plain, "既定値の補完・推定・単位換算が入っている");
});

test("較正済みで注意書きのキーが無い入力を受け付け、注意書きを作り出さない（要件 1.5）", () => {
  // 上流 `_calibration_to_dict` は較正済みのとき `notice` キー自体を省略する。
  // 型検査による表明: `notice` を省いた較正情報が `Calibration` として通る。
  const calibrated: Calibration = { stage: "m1_calibrated" };
  const plain = setKey(plainDocument(), "calibration", calibrated);
  const { view } = okOf(load(plain));
  assert.equal(view.document.calibration.stage, "m1_calibrated");
  assert.equal(
    Object.hasOwn(view.document.calibration, "notice"),
    false,
    "入力に無い注意書きを作り出してはならない",
  );
});

test("未較正の注意書きはそのまま保持する", () => {
  const plain = plainDocument();
  const notice = objectAt(plain["calibration"], "calibration")["notice"];
  const { view } = okOf(load(plain));
  assert.equal(view.document.calibration.notice, notice);
});

test("パラメータは任意の入れ子を受け付け、構造検証を行わない（設計 Loader の Risks）", () => {
  const anyNesting: readonly unknown[] = [
    { a: [1, "x", null, true, { b: {} }] },
    [],
    null,
    3,
    "text",
  ];
  for (const parameters of anyNesting) {
    okOf(load(setKey(plainDocument(), "parameters", parameters)));
  }
});

test("未知の指標キーはエラーにせず、そのまま読み込む（設計 Error Handling）", () => {
  const plain = plainDocument();
  objectAt(cellAt(plain, 0)["metrics"], "metrics")["brand_new_metric_mm"] = 42;
  const { view } = okOf(load(plain));
  assert.equal(view.document.cells[0]?.metrics["brand_new_metric_mm"], 42);
});

// --- ファイル名と不変条件 ---------------------------------------------------

test("読み込んだファイル名をビューへそのまま持ち回る（要件 1.6）", () => {
  const { view } = okOf(load(plainDocument(), "drivetrain-wheel48.json"));
  assert.equal(view.fileName, "drivetrain-wheel48.json");
});

test("失敗のとき問題の一覧は空にならない（設計 Loader の Invariants）", () => {
  const broken: readonly string[] = [
    NOT_JSON_TEXT,
    JSON_ARRAY_TEXT,
    JSON_NULL_TEXT,
    toJsonText(omitKey(plainDocument(), "cells")),
    toJsonText(setKey(plainDocument(), "sweep", 3)),
  ];
  for (const text of broken) {
    const result = loadSweep(text, FILE_NAME);
    assert.equal(result.ok, false);
    assert.ok(errorsOf(result).length > 0, "失敗したのに問題が 1 件も無い");
  }
});

test("記録側の問題があるとき記録は空である（設計 Loader の Invariants）", () => {
  const variants: readonly PlainDocument[] = [
    plainDocument(),
    setKey(plainDocumentWithRecords(), "throw_records", { count: 1 }),
    setKey(plainDocumentWithRecords(), "throw_records", []),
    setKey(plainDocumentWithRecords(), "throw_records", [{ record_id: "r" }]),
  ];
  for (const variant of variants) {
    const { view } = okOf(load(variant));
    assert.notEqual(view.recordsIssue, null, "記録側の問題が見つかっていない");
    assert.deepEqual([...view.records], [], "記録側の問題があるのに記録が残っている");
  }
});

// --- 境界（層 1。ファイル読み出しと通信を行わない） -------------------------

test("Loader は schema のみを import し、ファイル読み出しと通信を行わない", () => {
  const source = readFileSync(new URL("../src/load.js", import.meta.url), "utf8");
  const specifiers = [...source.matchAll(/from\s*(["'])([^"']+)\1/g)].map((matched) => matched[2]);
  assert.deepEqual([...new Set(specifiers)], ["./schema.js"], "層 1 は schema のみを import する");
  assert.doesNotMatch(source, /\bfetch\s*\(/, "通信を行ってはならない");
  assert.doesNotMatch(source, /\bXMLHttpRequest\b/, "通信を行ってはならない");
  assert.doesNotMatch(source, /\brequire\s*\(/, "require を含んではならない");
  assert.doesNotMatch(source, /\bnode:/, "Node.js 組み込みモジュールに触れてはならない");
  assert.doesNotMatch(source, /\bwindow\b/, "DOM に触れてはならない");
  assert.doesNotMatch(
    source,
    /\bgetElementById\b|\bquerySelector\b|\bcreateElement\b/,
    "DOM に触れてはならない",
  );
});
