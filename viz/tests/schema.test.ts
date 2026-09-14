// 入力 JSON の型・列挙値・必須項目の宣言を検証する（要件 1.2 / 1.4）。
//
// 型は実行時に消えるため、型の形そのものは **tsc が落ちること** で担保する。
// このファイルの上部に置いた注釈付きの値（well-formed な代表値）が型検査を通ることが
// 「型の形が上流の出力形式と一致している」ことの証拠であり、型が崩れればビルドが落ちる。
// 実行時に確かめられるのは、定数の中身と、モジュールが関数を公開していないことである。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import * as schemaModule from "../src/schema.js";
import {
  EXPECTED_OUTPUT_SCHEMA_VERSION,
  EXPECTED_RECORD_SCHEMA_VERSION,
  REQUIRED_TOP_LEVEL_KEYS,
  type InvalidPredictionEntry,
  type PredictionEntry,
  type SweepDocument,
  type ThrowRecordDoc,
} from "../src/schema.js";

// --- 型検査による表明（コンパイルが通ること自体が表明である） -----------------

// 上流の最上位構造。キー名は上流のものをそのまま用いる。
const SWEEP_DOCUMENT: SweepDocument = {
  output_schema_version: "1.0",
  calibration: { stage: "m1_calibrated", notice: null },
  model_exclusions: {
    throw_physics: ["spin"],
    observation: [],
    drivetrain: [],
    catch: [],
  },
  sweep: {
    kind: "reachability",
    axes: [
      { name: "throw_distance", unit: "mm", values: [1000, 1500] },
      { name: "release_mode", unit: "", values: ["low", "high"] },
    ],
    trials_per_cell: 1,
    seed: 7,
    catch_ratio_threshold: null,
  },
  parameters: {
    robot: { max_speed_mm_s: 1200, flags: [true, null, "x"] },
  },
  parameter_provenance: {
    "robot.max_speed_mm_s": "measured",
    "robot.flags": "assumed",
  },
  cells: [
    {
      axis_values: [1000, "low"],
      status: "catchable",
      success_ratio: 1,
      metrics: { travel_mm: 320 },
      not_evaluated_reason: null,
    },
    {
      axis_values: [1500, "high"],
      status: "not_evaluated",
      success_ratio: null,
      metrics: {},
      not_evaluated_reason: "no_floor_crossing",
    },
  ],
};

const THROW_RECORD: ThrowRecordDoc = {
  schema_version: "1.0",
  record_id: "r-001",
  source: "simulated",
  samples: [{ t_ms: 0, x_mm: 10, y_mm: 20, z_mm: 30 }],
  predictions: [
    {
      kind: "prediction",
      predicted_hit_x_mm: 1,
      predicted_hit_y_mm: 2,
      predicted_hit_time_ms: 3,
      remaining_time_ms: 4,
      residual: 0.5,
      sample_count: 6,
      based_on_time_ms: 7,
    },
    {
      kind: "invalid",
      reason: "no_floor_crossing",
      detail: "上向きの速度しかない",
      sample_count: 2,
      based_on_time_ms: null,
    },
  ],
};

// --- 実行時に確かめられる不変条件 -------------------------------------------

test("必須項目の一覧が最上位構造の全項目と一致する（要件 1.2）", () => {
  assert.deepEqual(
    [...REQUIRED_TOP_LEVEL_KEYS].sort(),
    Object.keys(SWEEP_DOCUMENT).sort(),
    "必須項目の一覧が最上位構造の型と食い違っている",
  );
  assert.equal(REQUIRED_TOP_LEVEL_KEYS.length, 7);
});

test("必須項目に代表記録を含めない（要件 1.7）", () => {
  const keys: readonly string[] = REQUIRED_TOP_LEVEL_KEYS;
  assert.equal(
    keys.includes("throw_records"),
    false,
    "代表記録が無くても図の描画は継続するため、必須項目に含めてはならない",
  );
});

test("想定する出力形式の版と記録スキーマの版を定数として持つ（要件 1.4）", () => {
  assert.equal(EXPECTED_OUTPUT_SCHEMA_VERSION, "1.0");
  assert.equal(EXPECTED_RECORD_SCHEMA_VERSION, "1.0");
});

test("型の名前を上流のフィールド名のまま用いる", () => {
  // 改名（camelCase 化など）が入るとここが落ちる。
  assert.deepEqual(Object.keys(THROW_RECORD.samples[0] ?? {}).sort(), [
    "t_ms",
    "x_mm",
    "y_mm",
    "z_mm",
  ]);
  const cell = SWEEP_DOCUMENT.cells[0];
  assert.deepEqual(Object.keys(cell ?? {}).sort(), [
    "axis_values",
    "metrics",
    "not_evaluated_reason",
    "status",
    "success_ratio",
  ]);
});

test("予測は kind による直和として絞り込める", () => {
  const entries = THROW_RECORD.predictions;
  const valid = entries.filter(
    (entry): entry is PredictionEntry => entry.kind === "prediction",
  );
  const invalid = entries.filter(
    (entry): entry is InvalidPredictionEntry => entry.kind === "invalid",
  );
  assert.equal(valid.length, 1);
  assert.equal(invalid.length, 1);
  assert.equal(valid[0]?.predicted_hit_time_ms, 3);
  assert.equal(invalid[0]?.reason, "no_floor_crossing");
});

test("Schema モジュールは型と定数のみを公開し、関数を公開しない", () => {
  const exported = Object.entries(schemaModule);
  assert.equal(
    exported.length,
    3,
    `公開しているのは 3 つの定数のみのはずである: ${exported.map(([name]) => name).join(", ")}`,
  );
  for (const [name, value] of exported) {
    assert.notEqual(typeof value, "function", `${name} を関数として公開してはならない`);
  }
});

test("Schema モジュールは何も import せず、実行時ロジックを持たない", () => {
  const source = readFileSync(new URL("../src/schema.js", import.meta.url), "utf8");
  assert.doesNotMatch(source, /^\s*import\b/m, "schema は何も import してはならない（層 0）");
  assert.doesNotMatch(source, /\brequire\s*\(/, "require を含んではならない");
  assert.doesNotMatch(source, /\bfunction\b/, "関数定義を含んではならない");
  assert.doesNotMatch(source, /=>/, "アロー関数を含んではならない");
  assert.doesNotMatch(source, /\bdocument\b|\bwindow\b/, "DOM に触れてはならない");
});
