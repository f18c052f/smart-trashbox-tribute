// 画面に出る文言の生成を検証する（要件 2.3 / 2.9 / 3.6 / 5.4）。
//
// ここで固定するのは次の 4 点である。
//   1. **公開関数のどの戻り値にも断定語が含まれないこと**（要件 3.6）。
//      列挙値・数値・単位・指標キーを網羅して走査する
//   2. 未知の指標キー・未知の無効理由が**そのまま返る**こと。
//      表示できない情報を握りつぶさない（設計 Error Handling）
//   3. 既知の列挙値の表示名が**上流の語を残したまま**補足を添える形であること
//      （設計 Format の Implementation Notes）
//   4. 数値が指定桁で丸まり、単位付きで出ること（要件 2.3）
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import * as formatModule from "../src/format.js";
import {
  calibrationStageLabel,
  formatAxisValue,
  formatNumber,
  formatWithUnit,
  invalidReasonLabel,
  metricLabel,
  notEvaluatedReasonLabel,
  provenanceLabel,
  statusLabel,
} from "../src/format.js";
import type {
  AxisValue,
  CalibrationStage,
  CellStatus,
  NotEvaluatedReason,
  ProvenanceKind,
} from "../src/schema.js";

// --- 列挙値の網羅（型で網羅性を固定する） -----------------------------------

// 各表は「値 → true」の完全な写像である。上流の列挙値が増えたとき、
// この表が埋まっていなければ **tsc が落ちる**。走査の網羅性を型で保証する。
function valuesOf<K extends string>(table: Readonly<Record<K, true>>): readonly K[] {
  return Object.keys(table) as K[];
}

const STATUSES = valuesOf<CellStatus>({
  catchable: true, not_catchable: true, not_evaluated: true,
});
const NOT_EVALUATED_REASONS = valuesOf<NotEvaluatedReason>({
  no_floor_crossing: true, no_samples: true, no_valid_prediction: true,
});
const CALIBRATION_STAGES = valuesOf<CalibrationStage>({
  uncalibrated: true, m1_calibrated: true, m2_calibrated: true,
});
const PROVENANCE_KINDS = valuesOf<ProvenanceKind>({ measured: true, assumed: true });

// 上流 `trajectory_sim` が `metrics` に入れるキー（位置誤差・持ち時間・必要移動量・
// 予測誤差・残留速度の平均）。
const KNOWN_METRIC_KEYS: readonly string[] = [
  "position_error_mm", "hold_time_ms", "required_distance_mm",
  "prediction_error_mm", "residual_speed_mm_s",
];

// 上流 `prediction_core.InvalidReason` の値。
const KNOWN_INVALID_REASONS: readonly string[] = [
  "insufficient_samples", "degenerate_time", "no_future_floor_crossing",
  "non_finite_value", "malformed_input",
];

// 表に無いキー。**そのまま返る**ことを確かめる対象である。
// `toString` などは、対応表を素のオブジェクトで持つとプロトタイプ由来の値を拾う。
const UNKNOWN_KEYS: readonly string[] = [
  "unknown_metric", "mean_wobble_mm", "toString", "constructor", "__proto__",
  "", "未知の指標", "hasOwnProperty",
];

const NUMBERS: readonly number[] = [
  -98765.4321, -1234.5678, -1, -0.5, 0, 0.125, 1, 42, 1000, 1234.5678,
];
const UNITS: readonly string[] = ["mm", "ms", "mm/s", "deg", ""];
const AXIS_STRINGS: readonly string[] = ["low", "high", "front", "release_mode"];

// --- 断定語 -----------------------------------------------------------------

// 要件 3.6 が禁じる断定語。設計 Format の Postconditions と同じ集合である。
const FORBIDDEN_WORDS: readonly string[] = ["合否", "合格", "不合格", "達成"];
// ASCII の断定語は大文字小文字を問わず禁じる。ただし語として現れる場合に限る。
// `no_floor_crossing` の "ng" のような部分一致は上流の語であり、断定ではない。
const FORBIDDEN_PATTERN = /\b(pass|fail|ok|ng)\b|nfr[\s-]*7/i;

function assertNoVerdictWord(text: string, source: string): void {
  for (const word of FORBIDDEN_WORDS) {
    assert.ok(
      !text.includes(word),
      `${source} の戻り値が断定語「${word}」を含む: ${text}`,
    );
  }
  assert.doesNotMatch(text, FORBIDDEN_PATTERN, `${source} の戻り値が断定語を含む: ${text}`);
}

/** 公開関数の戻り値を、呼び出し元の説明付きで網羅的に集める。 */
function everyOutput(): readonly (readonly [string, string])[] {
  const collected: [string, string][] = [];
  const add = (source: string, text: string): void => {
    collected.push([source, text]);
  };

  for (const status of STATUSES) add(`statusLabel(${status})`, statusLabel(status));
  for (const reason of NOT_EVALUATED_REASONS) {
    add(`notEvaluatedReasonLabel(${reason})`, notEvaluatedReasonLabel(reason));
  }
  for (const stage of CALIBRATION_STAGES) {
    add(`calibrationStageLabel(${stage})`, calibrationStageLabel(stage));
  }
  for (const kind of PROVENANCE_KINDS) add(`provenanceLabel(${kind})`, provenanceLabel(kind));

  // 未知のキーはそのまま返るため、断定語を含まないキーだけを走査に載せる。
  for (const key of [...KNOWN_METRIC_KEYS, ...UNKNOWN_KEYS]) {
    add(`metricLabel(${key})`, metricLabel(key));
  }
  for (const reason of [...KNOWN_INVALID_REASONS, ...UNKNOWN_KEYS]) {
    add(`invalidReasonLabel(${reason})`, invalidReasonLabel(reason));
  }

  for (const value of NUMBERS) {
    for (const digits of [0, 1, 2, 3, 6]) {
      add(`formatNumber(${value}, ${digits})`, formatNumber(value, digits));
    }
    for (const unit of UNITS) {
      add(`formatWithUnit(${value}, ${unit})`, formatWithUnit(value, unit));
      add(`formatAxisValue(${value}, ${unit})`, formatAxisValue(value, unit));
    }
  }
  for (const value of AXIS_STRINGS) {
    for (const unit of UNITS) {
      add(`formatAxisValue(${value}, ${unit})`, formatAxisValue(value, unit));
    }
  }
  return collected;
}

test("公開関数のどの戻り値も断定語を含まない（要件 3.6）", () => {
  const outputs = everyOutput();
  assert.ok(outputs.length >= 200, `走査の網羅が足りない: ${outputs.length} 件`);
  for (const [source, text] of outputs) {
    assertNoVerdictWord(text, source);
  }
});

test("断定語の検出そのものが機能している（この走査は落ちうる）", () => {
  const samples = ["合格", "不合格", "合否", "達成", "PASS", "fail", "OK", "ng", "NFR-7"];
  for (const sample of samples) {
    let detected = false;
    try {
      assertNoVerdictWord(`catchable（${sample}）`, "架空の実装");
    } catch {
      detected = true;
    }
    assert.ok(detected, `断定語「${sample}」を検出できていない`);
  }
  // 上流の語に含まれる部分一致で誤検出しないこと。
  assertNoVerdictWord("no_floor_crossing（床面通過なし）", "上流の語");
  assertNoVerdictWord("residual_speed_mm_s（残留速度 mm/s）", "上流の語");
});

// --- 未知のキーはそのまま返る -----------------------------------------------

test("未知の指標キーをそのまま返す（設計 Error Handling）", () => {
  for (const key of UNKNOWN_KEYS) {
    assert.equal(metricLabel(key), key, `未知のキーが書き換えられた: ${key}`);
  }
});

test("未知の無効理由をそのまま返す（要件 5.4）", () => {
  for (const reason of [...UNKNOWN_KEYS, "some_new_reason"]) {
    assert.equal(invalidReasonLabel(reason), reason, `未知の理由が書き換えられた: ${reason}`);
  }
});

// --- 既知の値の表示名 -------------------------------------------------------

test("状態の表示名が上流の語を残したまま補足する（要件 2.9 / 3.6）", () => {
  const labels = STATUSES.map((status) => statusLabel(status));
  for (const status of STATUSES) {
    const label = statusLabel(status);
    assert.ok(label.includes(status), `上流の語が消えている: ${status} -> ${label}`);
    assert.notEqual(label, status, `補足が付いていない: ${status}`);
  }
  assert.equal(new Set(labels).size, labels.length, "状態の表示名が重複している");
});

test("評価対象外の理由の表示名が上流の語を残したまま補足する（要件 2.5 / 3.6）", () => {
  const labels = NOT_EVALUATED_REASONS.map((reason) => notEvaluatedReasonLabel(reason));
  for (const reason of NOT_EVALUATED_REASONS) {
    const label = notEvaluatedReasonLabel(reason);
    assert.ok(label.includes(reason), `上流の語が消えている: ${reason} -> ${label}`);
    assert.notEqual(label, reason, `補足が付いていない: ${reason}`);
  }
  assert.equal(new Set(labels).size, labels.length, "理由の表示名が重複している");
});

test("較正段階の表示名が上流の語を残したまま補足する（要件 3.1）", () => {
  const labels = CALIBRATION_STAGES.map((stage) => calibrationStageLabel(stage));
  for (const stage of CALIBRATION_STAGES) {
    const label = calibrationStageLabel(stage);
    assert.ok(label.includes(stage), `上流の語が消えている: ${stage} -> ${label}`);
    assert.notEqual(label, stage, `補足が付いていない: ${stage}`);
  }
  assert.equal(new Set(labels).size, labels.length, "較正段階の表示名が重複している");
});

test("出所の表示名が実測と想定を区別する（要件 3.4）", () => {
  const labels = PROVENANCE_KINDS.map((kind) => provenanceLabel(kind));
  for (const kind of PROVENANCE_KINDS) {
    const label = provenanceLabel(kind);
    assert.ok(label.includes(kind), `上流の語が消えている: ${kind} -> ${label}`);
    assert.notEqual(label, kind, `補足が付いていない: ${kind}`);
  }
  assert.equal(new Set(labels).size, labels.length, "出所の表示名が重複している");
  assert.match(provenanceLabel("measured"), /実測/);
  assert.match(provenanceLabel("assumed"), /想定/);
});

test("既知の指標キーに単位付きの表示名を与える（要件 2.6 / 5.7）", () => {
  const expectedUnit: Readonly<Record<string, string>> = {
    position_error_mm: "mm",
    hold_time_ms: "ms",
    required_distance_mm: "mm",
    prediction_error_mm: "mm",
    residual_speed_mm_s: "mm/s",
  };
  for (const key of KNOWN_METRIC_KEYS) {
    const label = metricLabel(key);
    assert.ok(label.includes(key), `上流のキーが消えている: ${key} -> ${label}`);
    assert.notEqual(label, key, `既知のキーに表示名が無い: ${key}`);
    const unit = expectedUnit[key] ?? "";
    assert.ok(unit !== "", `検証側の単位表が欠けている: ${key}`);
    assert.ok(label.includes(unit), `単位が示されていない: ${key} -> ${label}`);
  }
});

test("既知の無効理由に表示名を与える（要件 5.4）", () => {
  const labels = KNOWN_INVALID_REASONS.map((reason) => invalidReasonLabel(reason));
  for (const reason of KNOWN_INVALID_REASONS) {
    const label = invalidReasonLabel(reason);
    assert.ok(label.includes(reason), `上流の語が消えている: ${reason} -> ${label}`);
    assert.notEqual(label, reason, `既知の理由に表示名が無い: ${reason}`);
  }
  assert.equal(new Set(labels).size, labels.length, "無効理由の表示名が重複している");
});

// --- 数値の表示 -------------------------------------------------------------

test("数値を指定の小数桁で表示する（要件 2.3）", () => {
  assert.equal(formatNumber(1234.5678, 2), "1234.57");
  assert.equal(formatNumber(1234.5678, 0), "1235");
  assert.equal(formatNumber(1234.5678, 4), "1234.5678");
  assert.equal(formatNumber(-0.5, 1), "-0.5");
  assert.equal(formatNumber(1000, 3), "1000.000");
  assert.equal(formatNumber(0, 2), "0.00");
});

test("小数桁の指定を実際に使える範囲へ丸める（要件 2.3）", () => {
  assert.equal(formatNumber(1.25, -3), "1", "負の桁数で例外を投げてはならない");
  assert.equal(formatNumber(1.25, 1.6), "1.25", "桁数を整数へ丸めていない");
  assert.equal(
    formatNumber(1.25, 400).startsWith("1.25"),
    true,
    "過大な桁数で例外を投げてはならない",
  );
});

test("有限でない値を握りつぶさない（要件 3.7）", () => {
  assert.equal(formatNumber(Number.NaN, 2), "NaN");
  assert.equal(formatNumber(Number.POSITIVE_INFINITY, 2), "Infinity");
  assert.equal(formatNumber(Number.NEGATIVE_INFINITY, 2), "-Infinity");
});

test("数値を単位付きで表示する（要件 2.3）", () => {
  assert.equal(formatWithUnit(1000, "mm"), "1000 mm");
  assert.equal(formatWithUnit(-250, "ms"), "-250 ms");
  assert.equal(formatWithUnit(1234.5678, "mm"), "1234.568 mm");
  assert.equal(formatWithUnit(0.5, "mm/s"), "0.500 mm/s");
});

test("単位が空のときは余分な空白を付けない（要件 2.3）", () => {
  assert.equal(formatWithUnit(12, ""), "12");
  assert.equal(formatWithUnit(0.25, ""), "0.250");
  for (const value of NUMBERS) {
    assert.doesNotMatch(formatWithUnit(value, ""), /\s$/, "末尾に空白が残っている");
  }
});

test("数値の軸の値を単位付きで表示する（要件 2.3）", () => {
  const numeric: AxisValue = 1500;
  assert.equal(formatAxisValue(numeric, "mm"), "1500 mm");
  assert.equal(formatAxisValue(2.5, "deg"), "2.500 deg");
  for (const value of NUMBERS) {
    for (const unit of UNITS) {
      assert.equal(formatAxisValue(value, unit), formatWithUnit(value, unit));
    }
  }
});

test("文字列の軸の値をそのまま表示し、単位を付け足さない（要件 2.3 / 3.7）", () => {
  for (const value of AXIS_STRINGS) {
    for (const unit of UNITS) {
      assert.equal(
        formatAxisValue(value, unit),
        value,
        `分類名の軸の値に単位が付いた: ${value} / ${unit}`,
      );
    }
  }
});

// --- 境界（層 1 であること） -------------------------------------------------

test("Format モジュールが公開するのは 9 つの関数のみである", () => {
  const exported = Object.entries(formatModule);
  assert.deepEqual(
    exported.map(([name]) => name).sort(),
    [
      "calibrationStageLabel", "formatAxisValue", "formatNumber", "formatWithUnit",
      "invalidReasonLabel", "metricLabel", "notEvaluatedReasonLabel", "provenanceLabel",
      "statusLabel",
    ],
  );
  for (const [name, value] of exported) {
    assert.equal(typeof value, "function", `${name} を関数として公開していない`);
  }
});

test("Format モジュールは schema のみを import し、DOM に触れない（設計 Dependency Direction）", () => {
  const source = readFileSync(new URL("../src/format.js", import.meta.url), "utf8");
  const specifiers = [...source.matchAll(/(?:from|import)\s*\(?\s*(["'])([^"']*)\1/g)]
    .map((matched) => matched[2] ?? "");
  for (const specifier of specifiers) {
    assert.equal(specifier, "./schema.js", `層 1 が許されない import を持つ: ${specifier}`);
  }
  assert.doesNotMatch(source, /\bdocument\b|\bwindow\b/, "DOM に触れてはならない");
  assert.doesNotMatch(source, /\brequire\s*\(/, "require を含んではならない");
  assert.doesNotMatch(
    source,
    /\b(lerp|interpolate|spline|bezier)\b/,
    "補間は scale.ts にのみ置く（境界検査 B-7）",
  );
});

test("ソースの文字列リテラルに断定語が現れない（境界検査 B-10）", () => {
  const source = readFileSync(new URL("../src/format.js", import.meta.url), "utf8");
  assertNoVerdictWord(source, "format.js のソース");
});
