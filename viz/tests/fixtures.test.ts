// テスト用の最小入力そのものを検証する（要件 1.2）。
//
// `fixtures.ts` は後続タスク（Loader / RegionPlanner / ContextPlanner / AnimationPlanner）の
// 全テストが土台にする値である。土台が誤っていれば、その上のテストは
// 「誤った入力に対して正しく振る舞う」ことしか示さなくなる。
// そこでここでは、生成した最小入力が **宣言済みの型と必須項目一覧に矛盾しないこと** を
// 実行時に確かめる。型の形は tsc が、値の中身はこのファイルが担保する。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  REQUIRED_TOP_LEVEL_KEYS,
  type CellResult,
  type JsonValue,
  type SweepDocument,
} from "../src/schema.js";
import {
  CELL_STATUSES,
  MODEL_EXCLUSION_FACTOR_COUNT,
  MODEL_EXCLUSION_STAGES,
  NOT_EVALUATED_REASONS,
  ONE_AXIS,
  THREE_AXES,
  TWO_AXES,
  cyclingStatusAt,
  makeGridDocument,
  makeInvalidPrediction,
  makeMixedStatusDocument,
  makeModelExclusions,
  makeParameterProvenance,
  makeParameters,
  makeSweepDocument,
  makeSweepDocumentWithRecords,
  makeThrowRecord,
  omitKey,
  setKey,
  toJsonText,
  toPlainObject,
} from "./fixtures.js";

// 上流の出力に現れてよい最上位キー。必須 7 キーと、必須ではない代表記録のみ。
const CONTRACT_TOP_LEVEL_KEYS: readonly string[] = [...REQUIRED_TOP_LEVEL_KEYS, "throw_records"];

const CALIBRATION_STAGES: readonly string[] = ["uncalibrated", "m1_calibrated", "m2_calibrated"];
const PROVENANCE_KINDS: readonly string[] = ["measured", "assumed"];
const SWEEP_KINDS: readonly string[] = ["reachability", "throw"];

/** 任意の入れ子をドット区切りのパスへ平坦化する（ContextPlanner が行うのと同じ規則）。 */
function flattenPaths(value: JsonValue, prefix: string, into: string[]): void {
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    const record = value as { readonly [key: string]: JsonValue };
    for (const key of Object.keys(record)) {
      const child = record[key];
      if (child !== undefined) {
        flattenPaths(child, prefix === "" ? key : prefix + "." + key, into);
      }
    }
    return;
  }
  into.push(prefix);
}

function parameterPaths(): readonly string[] {
  const paths: string[] = [];
  flattenPaths(makeParameters(), "", paths);
  return paths;
}

function assertCellEnumsAreDeclared(cell: CellResult, label: string): void {
  assert.ok(CELL_STATUSES.includes(cell.status), label + ": 未宣言の status " + cell.status);
  if (cell.status === "not_evaluated") {
    assert.ok(
      cell.not_evaluated_reason !== null
        && NOT_EVALUATED_REASONS.includes(cell.not_evaluated_reason),
      label + ": 評価対象外の格子点が宣言済みの理由を持たない",
    );
  } else {
    assert.equal(
      cell.not_evaluated_reason,
      null,
      label + ": 評価対象外でない格子点が理由を持っている",
    );
  }
}

function assertDocumentEnumsAreDeclared(doc: SweepDocument, label: string): void {
  assert.ok(CALIBRATION_STAGES.includes(doc.calibration.stage), label + ": 未宣言の較正段階");
  assert.ok(SWEEP_KINDS.includes(doc.sweep.kind), label + ": 未宣言の掃引種別");
  for (const kind of Object.values(doc.parameter_provenance)) {
    assert.ok(PROVENANCE_KINDS.includes(kind), label + ": 未宣言の出所 " + kind);
  }
  doc.cells.forEach((cell, index) =>
    assertCellEnumsAreDeclared(cell, label + " cells[" + index + "]"));
}

// --- 最上位構造 -------------------------------------------------------------

test("既定の最小文書が必須項目をすべて備え、契約外の最上位キーを持たない（要件 1.2）", () => {
  const doc = makeSweepDocument();
  const keys = Object.keys(doc);
  for (const required of REQUIRED_TOP_LEVEL_KEYS) {
    assert.ok(keys.includes(required), "必須項目 " + required + " が欠けている");
  }
  for (const key of keys) {
    assert.ok(
      CONTRACT_TOP_LEVEL_KEYS.includes(key),
      "契約に無い最上位キー " + key + " を持っている",
    );
  }
});

test("既定の最小文書は代表記録を含まず、代表記録つきの変種のみが含む（要件 1.7）", () => {
  assert.equal(
    Object.keys(makeSweepDocument()).includes("throw_records"),
    false,
    "最小文書に代表記録が混ざっている",
  );
  const withRecords = makeSweepDocumentWithRecords();
  assert.ok(Array.isArray(withRecords.throw_records));
  assert.ok(withRecords.throw_records.length >= 1, "代表記録つきの変種が記録を持たない");
  for (const required of REQUIRED_TOP_LEVEL_KEYS) {
    assert.ok(
      Object.keys(withRecords).includes(required),
      "代表記録つきの変種で必須項目 " + required + " が欠けている",
    );
  }
});

test("列挙値が schema.ts の宣言どおりの値だけを取る", () => {
  assertDocumentEnumsAreDeclared(makeSweepDocument(), "最小文書");
  assertDocumentEnumsAreDeclared(makeMixedStatusDocument(), "混在文書");
  assertDocumentEnumsAreDeclared(makeSweepDocumentWithRecords(), "代表記録つき文書");
  for (const record of makeSweepDocumentWithRecords().throw_records) {
    for (const prediction of record.predictions) {
      assert.ok(
        prediction.kind === "prediction" || prediction.kind === "invalid",
        "未宣言の予測種別 " + prediction.kind,
      );
    }
  }
});

test("較正済みの文書では notice を null にでき、試行 1 回の掃引では閾値を null にできる", () => {
  const base = makeSweepDocument();
  const doc = makeSweepDocument({
    calibration: { stage: "m2_calibrated", notice: null },
    sweep: { ...base.sweep, trials_per_cell: 1, catch_ratio_threshold: null },
  });
  assert.equal(doc.calibration.stage, "m2_calibrated");
  assert.equal(doc.calibration.notice, null);
  assert.equal(doc.sweep.catch_ratio_threshold, null);
  assert.equal(doc.sweep.trials_per_cell, 1);
  assert.notEqual(base.calibration.notice, null, "既定の文書が未較正の注意書きを持たない");
});

// --- 掃引の格子 -------------------------------------------------------------

test("1 軸 / 2 軸 / 3 軸の格子点数が軸の値の個数の積に一致する（要件 2.1 / 2.7 / 2.8）", () => {
  for (const axes of [ONE_AXIS, TWO_AXES, THREE_AXES]) {
    const doc = makeGridDocument({ axes });
    const expected = axes.reduce((product, axis) => product * axis.values.length, 1);
    assert.equal(doc.cells.length, expected, "軸 " + axes.length + " 本での格子点数が合わない");
    assert.equal(doc.sweep.axes.length, axes.length);
    for (const cell of doc.cells) {
      assert.equal(cell.axis_values.length, axes.length, "格子点の軸値の個数が軸数と合わない");
    }
  }
});

test("格子点の軸値の組が重複せず、軸の宣言値の中から取られている", () => {
  const doc = makeGridDocument({ axes: THREE_AXES });
  const seen = new Set<string>();
  for (const cell of doc.cells) {
    const key = JSON.stringify(cell.axis_values);
    assert.equal(seen.has(key), false, "軸値の組が重複している: " + key);
    seen.add(key);
    cell.axis_values.forEach((value, index) => {
      const axis = doc.sweep.axes[index];
      if (axis === undefined) {
        throw new Error("軸の宣言が足りない: index " + index);
      }
      assert.ok(axis.values.includes(value), "軸 " + axis.name + " に無い値 " + String(value));
    });
  }
});

test("状態を混在させた文書が 3 状態をすべて含み、理由の有無が状態と対応する（要件 2.5）", () => {
  const doc = makeMixedStatusDocument();
  const statuses = new Set(doc.cells.map((cell) => cell.status));
  for (const status of CELL_STATUSES) {
    assert.ok(statuses.has(status), "状態 " + status + " を含む格子点が無い");
  }
  doc.cells.forEach((cell, index) => assertCellEnumsAreDeclared(cell, "cells[" + index + "]"));
  for (const cell of doc.cells) {
    if (cell.status === "not_evaluated") {
      assert.equal(cell.success_ratio, null, "評価対象外の格子点が成立割合を持っている");
    } else {
      assert.notEqual(cell.success_ratio, null, "評価対象の格子点が成立割合を持たない");
    }
  }
});

test("状態の割り当てを呼び出し側が決められる", () => {
  const allCatchable = makeGridDocument({ axes: TWO_AXES, statusAt: () => "catchable" });
  assert.ok(allCatchable.cells.every((cell) => cell.status === "catchable"));
  const cycled = makeGridDocument({ axes: TWO_AXES, statusAt: cyclingStatusAt });
  assert.equal(new Set(cycled.cells.map((cell) => cell.status)).size, CELL_STATUSES.length);
});

// --- 前提と限界 -------------------------------------------------------------

test("model_exclusions が 4 段 12 要因を 1 つも落とさずに持つ（要件 3.3）", () => {
  const exclusions = makeModelExclusions();
  assert.deepEqual(Object.keys(exclusions), [...MODEL_EXCLUSION_STAGES]);
  const total = Object.values(exclusions).reduce((sum, items) => sum + items.length, 0);
  assert.equal(total, MODEL_EXCLUSION_FACTOR_COUNT, "除外要因の総数が変わっている");
  assert.equal(MODEL_EXCLUSION_FACTOR_COUNT, 12);
  for (const [stage, items] of Object.entries(exclusions)) {
    assert.ok(items.length >= 1, "段 " + stage + " の要因が空である");
    assert.equal(new Set(items).size, items.length, "段 " + stage + " の要因が重複している");
  }
});

test("parameter_provenance が平坦化パスの一部だけを覆い、記載の無い行を残す（要件 3.4）", () => {
  const paths = parameterPaths();
  const provenance = makeParameterProvenance();
  const covered = Object.keys(provenance);
  assert.ok(paths.length >= 1, "平坦化できるパラメータが無い");
  assert.ok(covered.length >= 1, "出所の記載が 1 件も無い");
  for (const key of covered) {
    assert.ok(paths.includes(key), "出所の記載が平坦化パスに無い: " + key);
  }
  assert.ok(
    covered.length < paths.length,
    "すべてのパスに出所が記載されており、provenance が null になる行を作れない",
  );
});

test("機体パラメータとして drivetrain. で始まるパスを持つ（要件 4.2）", () => {
  const drivetrainPaths = parameterPaths().filter((path) => path.startsWith("drivetrain."));
  assert.ok(drivetrainPaths.length >= 2, "drivetrain. で始まるパラメータが足りない");
  const covered = Object.keys(makeParameterProvenance())
    .filter((path) => path.startsWith("drivetrain."));
  assert.ok(covered.length >= 1, "drivetrain. で始まる出所の記載が無い");
  assert.ok(covered.length < drivetrainPaths.length, "機体パラメータの全行に出所が記載されている");
});

// --- 代表記録 ---------------------------------------------------------------

test("代表記録が有効な予測と無効な予測の双方を含み、無効な予測は落下地点を持たない（要件 5.4）", () => {
  const record = makeThrowRecord();
  const valid = record.predictions.filter((entry) => entry.kind === "prediction");
  const invalid = record.predictions.filter((entry) => entry.kind === "invalid");
  assert.ok(valid.length >= 1, "有効な予測が無い");
  assert.ok(invalid.length >= 1, "無効な予測が無い");
  for (const entry of invalid) {
    assert.equal(
      Object.keys(entry).includes("predicted_hit_x_mm"),
      false,
      "無効な予測が落下地点を持っている",
    );
  }
  for (const entry of record.predictions) {
    if (entry.kind === "invalid") {
      assert.ok(entry.reason.length >= 1, "無効な予測が理由を持たない");
    }
  }
});

test("予測の based_on_time_ms が互いに異なり、時刻での選択を試せる（要件 5.2）", () => {
  const times = makeThrowRecord().predictions
    .map((entry) => entry.based_on_time_ms)
    .filter((time): time is number => time !== null);
  assert.ok(times.length >= 2, "時刻を持つ予測が 2 件未満である");
  assert.equal(new Set(times).size, times.length, "予測の基準時刻が重複している");
});

test("観測サンプルが時刻昇順で、2 点の中間時刻を取れる（要件 5.6）", () => {
  const samples = makeThrowRecord().samples;
  assert.ok(samples.length >= 2, "サンプルが 2 点未満である");
  for (let index = 1; index < samples.length; index += 1) {
    const previous = samples[index - 1];
    const current = samples[index];
    if (previous === undefined || current === undefined) {
      throw new Error("サンプル列に穴がある: index " + index);
    }
    assert.ok(current.t_ms > previous.t_ms, "サンプルの時刻が昇順でない");
  }
});

test("サンプル 0 件の記録を作れる", () => {
  const record = makeThrowRecord({ samples: [] });
  assert.equal(record.samples.length, 0);
  assert.ok(record.predictions.length >= 1, "サンプルを空にすると予測まで消えている");
});

// --- 不正な入力を組み立てるための逃げ道 -------------------------------------

test("最上位キーを 1 つずつ落とした素のオブジェクトを型検査と戦わずに作れる（要件 1.2）", () => {
  const plain = toPlainObject(makeSweepDocument());
  for (const required of REQUIRED_TOP_LEVEL_KEYS) {
    const broken = omitKey(plain, required);
    assert.equal(Object.keys(broken).includes(required), false, required + " を落とせていない");
    assert.equal(
      Object.keys(broken).length,
      REQUIRED_TOP_LEVEL_KEYS.length - 1,
      "落とした以外のキーまで消えている",
    );
    assert.ok(Object.keys(plain).includes(required), "元のオブジェクトが書き換えられている");
  }
});

test("最上位キーを任意の値へ差し替えられる（要件 1.4 / 1.7）", () => {
  const plain = toPlainObject(makeSweepDocument());
  assert.equal(setKey(plain, "output_schema_version", "9.9")["output_schema_version"], "9.9");
  assert.equal(setKey(plain, "throw_records", 42)["throw_records"], 42);
  assert.equal(
    Object.keys(plain).includes("throw_records"),
    false,
    "元のオブジェクトが書き換えられている",
  );
});

test("JSON 文字列との往復で内容が保存される", () => {
  for (const doc of [makeSweepDocument(), makeMixedStatusDocument()]) {
    assert.deepEqual(JSON.parse(toJsonText(doc)), doc);
  }
  const withRecords = makeSweepDocumentWithRecords();
  assert.deepEqual(JSON.parse(toJsonText(withRecords)), withRecords);
  const invalid = makeInvalidPrediction();
  assert.deepEqual(JSON.parse(toJsonText(invalid)), invalid);
});

test("素のオブジェクトへの複製が元の値を共有しない", () => {
  const doc = makeSweepDocument();
  const plain = toPlainObject(doc);
  const calibration = plain["calibration"] as { stage: string };
  calibration.stage = "m2_calibrated";
  assert.notEqual(doc.calibration.stage, "m2_calibrated", "複製が元の値を参照している");
});

// --- 境界 -------------------------------------------------------------------

test("fixtures.ts がファイルシステムにもネットワークにも触れない", () => {
  const source = readFileSync(new URL("../../tests/fixtures.ts", import.meta.url), "utf8");
  assert.doesNotMatch(source, /node:fs/, "ファイルシステムを import している");
  assert.doesNotMatch(source, /readFileSync|readdirSync|existsSync/, "ファイル読み取りを行っている");
  assert.doesNotMatch(source, /\bfetch\s*\(|XMLHttpRequest/, "ネットワークに触れている");
});
