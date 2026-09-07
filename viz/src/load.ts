// 未検証のテキストを、検証済みの表示用ビュー（`SweepView`）または問題の一覧へ変換する。
//
// このモジュールは**層 1** であり、`schema` だけを import する。DOM に触れず、
// **ファイル読み出しと通信を行わない**。ファイルの読み出しは `App` の責務であり、
// ここが受け取るのはテキストとファイル名だけである（要件 1.1 / 1.6）。
//
// 規律は 4 つである。
//   1. **未検証の値に触れてよいのはここだけ。** ここを通過した後は、型の付いた
//      検証済みの値しか下流に流れない。下流は再検証を必要としない
//   2. **必須項目が 1 つでも欠ければ失敗。** 部分的に描ける状態を作らない（要件 1.2）
//   3. **最初の 1 件で打ち切らない。** 欠落・型不一致・未知の列挙値をすべて列挙する。
//      何が足りないかを一度に見せるためである（設計 Error Handling）
//   4. **入力に無い値を作らない。** 既定値の補完・推定・単位換算を行わない（要件 1.5）。
//      返す `document` は解釈した値そのものであり、複製も書き換えもしない
//
// 縮退は 1 箇所のみである。代表記録（`throw_records`）が無い / 配列でない /
// 要素が読めない場合は、読み込みを成功させたうえで `recordsIssue` として返す（要件 1.7）。
// 出力形式の版の不一致も同じく警告であり、読み込みは成功する（要件 1.4）。
//
// 未知の列挙値は**エラー**にする。黙って「その他」に丸めると、上流の変更が
// 表示側で見えなくなる（設計 Loader の Implementation Notes）。

import {
  EXPECTED_OUTPUT_SCHEMA_VERSION,
  REQUIRED_TOP_LEVEL_KEYS,
  type CalibrationStage,
  type CellStatus,
  type NotEvaluatedReason,
  type ProvenanceKind,
  type SweepDocument,
  type SweepKind,
  type ThrowRecordDoc,
} from "./schema.js";

// --- 公開する契約 -----------------------------------------------------------

/** 読み込みで見つかる問題の種別。 */
export type LoadIssueCode =
  | "not_json" | "not_object" | "missing_key" | "wrong_type"
  | "unknown_enum_value" | "schema_version_mismatch" | "records_unusable";

/** 問題 1 件。表示用の文言ではなく、場所と内容を持つ構造化された値である。 */
export interface LoadIssue {
  readonly code: LoadIssueCode;
  readonly path: string;     // 例: "cells[3].status"
  readonly detail: string;
}

/** 検証済みの表示用ビュー。 */
export interface SweepView {
  readonly fileName: string;
  readonly document: SweepDocument;
  readonly records: readonly ThrowRecordDoc[];
  readonly recordsIssue: LoadIssue | null;
}

/** 読み込みの結果。失敗のとき `errors` は空にならない。 */
export type LoadResult =
  | { readonly ok: true; readonly view: SweepView; readonly warnings: readonly LoadIssue[] }
  | { readonly ok: false; readonly errors: readonly LoadIssue[] };

// --- 内部の型と列挙値の表 ---------------------------------------------------

/** 検証前のオブジェクト。値の型は分かっていない。 */
type UnknownRecord = { readonly [key: string]: unknown };

/**
 * 列挙値を実行時に走査できる配列へ落とす。
 * 表は「値 → true」の完全な写像であるため、上流の列挙値が増えて `schema.ts` の
 * 直和が広がれば、この表が埋まっていない限り **tsc が落ちる**。
 */
function valuesOf<K extends string>(table: Readonly<Record<K, true>>): readonly string[] {
  return Object.keys(table);
}

const CELL_STATUSES = valuesOf<CellStatus>({
  catchable: true, not_catchable: true, not_evaluated: true,
});
const NOT_EVALUATED_REASONS = valuesOf<NotEvaluatedReason>({
  no_floor_crossing: true, no_samples: true, no_valid_prediction: true,
});
const CALIBRATION_STAGES = valuesOf<CalibrationStage>({
  uncalibrated: true, m1_calibrated: true, m2_calibrated: true,
});
const PROVENANCE_KINDS = valuesOf<ProvenanceKind>({ measured: true, assumed: true });
const SWEEP_KINDS = valuesOf<SweepKind>({ reachability: true, throw: true });

/** 予測の直和を分ける `kind` の値。未知の `kind` は記録側の問題として扱う。 */
const PREDICTION_KINDS: readonly string[] = ["prediction", "invalid"];

const SAMPLE_KEYS = ["t_ms", "x_mm", "y_mm", "z_mm"] as const;
const RECORD_STRING_KEYS = ["schema_version", "record_id", "source"] as const;
const PREDICTION_NUMBER_KEYS = [
  "predicted_hit_x_mm", "predicted_hit_y_mm", "predicted_hit_time_ms",
  "remaining_time_ms", "residual", "sample_count", "based_on_time_ms",
] as const;

/** 最上位そのものを指す場所。 */
const ROOT_PATH = "";

// --- 場所の組み立て ---------------------------------------------------------

/** 形の決まったオブジェクトの項目。例: `sweep.trials_per_cell` */
function member(base: string, key: string): string {
  return `${base}.${key}`;
}

/** 配列の要素。例: `cells[3]` */
function element(base: string, index: number): string {
  return `${base}[${index}]`;
}

/** キー名が入力に依存する写像の項目。例: `parameter_provenance["throw.speed_mm_s"]` */
function entry(base: string, key: string): string {
  return `${base}[${JSON.stringify(key)}]`;
}

// --- 問題の組み立て ---------------------------------------------------------

/** 入力の値が何であったかを日本語で言い表す。値そのものは載せない。 */
function typeName(value: unknown): string {
  if (value === null) {
    return "null";
  }
  if (Array.isArray(value)) {
    return "配列";
  }
  switch (typeof value) {
    case "string": return "文字列";
    case "number": return "数値";
    case "boolean": return "真偽値";
    case "object": return "オブジェクト";
    case "undefined": return "未定義";
    default: return typeof value;
  }
}

function pushMissing(issues: LoadIssue[], path: string): void {
  issues.push({ code: "missing_key", path, detail: "必須項目が入力に無い" });
}

function pushWrongType(
  issues: LoadIssue[], path: string, expected: string, value: unknown,
): void {
  issues.push({
    code: "wrong_type",
    path,
    detail: `${expected}である必要があるが、入力は${typeName(value)}である`,
  });
}

function pushUnknownEnum(
  issues: LoadIssue[], path: string, value: string, allowed: readonly string[],
): void {
  issues.push({
    code: "unknown_enum_value",
    path,
    detail: `上流の列挙値に無い値である: ${JSON.stringify(value)}（既知の値: ${allowed.join(" / ")}）`,
  });
}

// --- 個々の値の検査 ---------------------------------------------------------
//
// どの検査も真偽値を返し、合わなければ問題を 1 件積む。呼び出し側は結果を見て
// 中へ降りるかどうかだけを決める。**途中で打ち切らない**のがこの形の目的である。

function checkObject(
  value: unknown, path: string, issues: LoadIssue[],
): value is UnknownRecord {
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    return true;
  }
  pushWrongType(issues, path, "オブジェクト", value);
  return false;
}

function checkArray(
  value: unknown, path: string, issues: LoadIssue[],
): value is readonly unknown[] {
  if (Array.isArray(value)) {
    return true;
  }
  pushWrongType(issues, path, "配列", value);
  return false;
}

function checkString(value: unknown, path: string, issues: LoadIssue[]): boolean {
  if (typeof value === "string") {
    return true;
  }
  pushWrongType(issues, path, "文字列", value);
  return false;
}

function checkNumber(value: unknown, path: string, issues: LoadIssue[]): boolean {
  if (typeof value === "number") {
    return true;
  }
  pushWrongType(issues, path, "数値", value);
  return false;
}

function checkNullableString(value: unknown, path: string, issues: LoadIssue[]): boolean {
  if (value === null || typeof value === "string") {
    return true;
  }
  pushWrongType(issues, path, "文字列または null", value);
  return false;
}

function checkNullableNumber(value: unknown, path: string, issues: LoadIssue[]): boolean {
  if (value === null || typeof value === "number") {
    return true;
  }
  pushWrongType(issues, path, "数値または null", value);
  return false;
}

/** 掃引軸が取る値。上流は数値と文字列のいずれも用いる。 */
function checkAxisValue(value: unknown, path: string, issues: LoadIssue[]): boolean {
  if (typeof value === "number" || typeof value === "string") {
    return true;
  }
  pushWrongType(issues, path, "数値または文字列", value);
  return false;
}

function checkEnum(
  value: unknown, allowed: readonly string[], path: string, issues: LoadIssue[],
): boolean {
  if (typeof value !== "string") {
    pushWrongType(issues, path, "文字列", value);
    return false;
  }
  if (!allowed.includes(value)) {
    pushUnknownEnum(issues, path, value, allowed);
    return false;
  }
  return true;
}

function checkNullableEnum(
  value: unknown, allowed: readonly string[], path: string, issues: LoadIssue[],
): boolean {
  if (value === null) {
    return true;
  }
  return checkEnum(value, allowed, path, issues);
}

/** 項目の存在だけを確かめる。無ければ欠落として積む。 */
function requireMember(
  container: UnknownRecord, base: string, key: string, issues: LoadIssue[],
): boolean {
  if (Object.hasOwn(container, key)) {
    return true;
  }
  pushMissing(issues, member(base, key));
  return false;
}

// --- 掃引結果の検査 ---------------------------------------------------------

function validateCalibration(value: unknown, path: string, issues: LoadIssue[]): void {
  if (!checkObject(value, path, issues)) {
    return;
  }
  if (requireMember(value, path, "stage", issues)) {
    checkEnum(value["stage"], CALIBRATION_STAGES, member(path, "stage"), issues);
  }
  // 上流は較正済みのとき `notice` キー自体を省略する
  // （`src/trajectory_sim/serialize.py` の `_calibration_to_dict`）。
  // 無いことを欠落として扱わず、また `null` を補いもしない（要件 1.5）。
  if (Object.hasOwn(value, "notice")) {
    checkNullableString(value["notice"], member(path, "notice"), issues);
  }
}

function validateModelExclusions(value: unknown, path: string, issues: LoadIssue[]): void {
  if (!checkObject(value, path, issues)) {
    return;
  }
  // 段名は入力に現れたものをそのまま受ける。上流が段を増やしても要因を落とさない。
  for (const [stage, factors] of Object.entries(value)) {
    const stagePath = entry(path, stage);
    if (!checkArray(factors, stagePath, issues)) {
      continue;
    }
    factors.forEach((factor, index) => {
      checkString(factor, element(stagePath, index), issues);
    });
  }
}

function validateAxis(value: unknown, path: string, issues: LoadIssue[]): void {
  if (!checkObject(value, path, issues)) {
    return;
  }
  if (requireMember(value, path, "name", issues)) {
    checkString(value["name"], member(path, "name"), issues);
  }
  if (requireMember(value, path, "unit", issues)) {
    checkString(value["unit"], member(path, "unit"), issues);
  }
  if (requireMember(value, path, "values", issues)) {
    const valuesPath = member(path, "values");
    const axisValues = value["values"];
    if (checkArray(axisValues, valuesPath, issues)) {
      axisValues.forEach((axisValue, index) => {
        checkAxisValue(axisValue, element(valuesPath, index), issues);
      });
    }
  }
}

function validateSweepSpec(value: unknown, path: string, issues: LoadIssue[]): void {
  if (!checkObject(value, path, issues)) {
    return;
  }
  if (requireMember(value, path, "kind", issues)) {
    checkEnum(value["kind"], SWEEP_KINDS, member(path, "kind"), issues);
  }
  if (requireMember(value, path, "axes", issues)) {
    const axesPath = member(path, "axes");
    const axes = value["axes"];
    if (checkArray(axes, axesPath, issues)) {
      axes.forEach((axis, index) => validateAxis(axis, element(axesPath, index), issues));
    }
  }
  if (requireMember(value, path, "trials_per_cell", issues)) {
    checkNumber(value["trials_per_cell"], member(path, "trials_per_cell"), issues);
  }
  if (requireMember(value, path, "seed", issues)) {
    checkNumber(value["seed"], member(path, "seed"), issues);
  }
  if (requireMember(value, path, "catch_ratio_threshold", issues)) {
    checkNullableNumber(
      value["catch_ratio_threshold"], member(path, "catch_ratio_threshold"), issues,
    );
  }
}

function validateProvenance(value: unknown, path: string, issues: LoadIssue[]): void {
  if (!checkObject(value, path, issues)) {
    return;
  }
  for (const [parameterPath, kind] of Object.entries(value)) {
    checkEnum(kind, PROVENANCE_KINDS, entry(path, parameterPath), issues);
  }
}

function validateMetrics(value: unknown, path: string, issues: LoadIssue[]): void {
  if (!checkObject(value, path, issues)) {
    return;
  }
  // キー名は掃引設定に依存するため決め打ちしない。未知のキーはエラーにしない。
  for (const [key, metric] of Object.entries(value)) {
    checkNumber(metric, entry(path, key), issues);
  }
}

function validateCell(value: unknown, path: string, issues: LoadIssue[]): void {
  if (!checkObject(value, path, issues)) {
    return;
  }
  if (requireMember(value, path, "axis_values", issues)) {
    const axisValuesPath = member(path, "axis_values");
    const axisValues = value["axis_values"];
    if (checkArray(axisValues, axisValuesPath, issues)) {
      axisValues.forEach((axisValue, index) => {
        checkAxisValue(axisValue, element(axisValuesPath, index), issues);
      });
    }
  }
  if (requireMember(value, path, "status", issues)) {
    checkEnum(value["status"], CELL_STATUSES, member(path, "status"), issues);
  }
  if (requireMember(value, path, "success_ratio", issues)) {
    checkNullableNumber(value["success_ratio"], member(path, "success_ratio"), issues);
  }
  if (requireMember(value, path, "metrics", issues)) {
    validateMetrics(value["metrics"], member(path, "metrics"), issues);
  }
  if (requireMember(value, path, "not_evaluated_reason", issues)) {
    checkNullableEnum(
      value["not_evaluated_reason"], NOT_EVALUATED_REASONS,
      member(path, "not_evaluated_reason"), issues,
    );
  }
}

function validateCells(value: unknown, path: string, issues: LoadIssue[]): void {
  if (!checkArray(value, path, issues)) {
    return;
  }
  value.forEach((cell, index) => validateCell(cell, element(path, index), issues));
}

/**
 * 最上位の必須項目をすべて検査する。
 * 欠けている項目を先に列挙してから、存在する項目の中身へ降りる。
 * どちらも**最初の 1 件で打ち切らない**。
 */
function validateDocument(root: UnknownRecord, issues: LoadIssue[]): void {
  for (const key of REQUIRED_TOP_LEVEL_KEYS) {
    if (!Object.hasOwn(root, key)) {
      pushMissing(issues, key);
    }
  }
  if (Object.hasOwn(root, "output_schema_version")) {
    checkString(root["output_schema_version"], "output_schema_version", issues);
  }
  if (Object.hasOwn(root, "calibration")) {
    validateCalibration(root["calibration"], "calibration", issues);
  }
  if (Object.hasOwn(root, "model_exclusions")) {
    validateModelExclusions(root["model_exclusions"], "model_exclusions", issues);
  }
  if (Object.hasOwn(root, "sweep")) {
    validateSweepSpec(root["sweep"], "sweep", issues);
  }
  // `parameters` は任意の入れ子であり、**構造検証を行わない**（設計 Loader の Risks）。
  // `JSON.parse` が返す値は定義により `JsonValue` であるため、存在の確認で足りる。
  if (Object.hasOwn(root, "parameter_provenance")) {
    validateProvenance(root["parameter_provenance"], "parameter_provenance", issues);
  }
  if (Object.hasOwn(root, "cells")) {
    validateCells(root["cells"], "cells", issues);
  }
}

// --- 代表記録の検査（要件 1.7） ---------------------------------------------

function validateSample(value: unknown, path: string, issues: LoadIssue[]): void {
  if (!checkObject(value, path, issues)) {
    return;
  }
  for (const key of SAMPLE_KEYS) {
    if (requireMember(value, path, key, issues)) {
      checkNumber(value[key], member(path, key), issues);
    }
  }
}

function validatePrediction(value: unknown, path: string, issues: LoadIssue[]): void {
  if (!checkObject(value, path, issues)) {
    return;
  }
  if (!requireMember(value, path, "kind", issues)) {
    return;
  }
  const kind = value["kind"];
  if (kind === "prediction") {
    for (const key of PREDICTION_NUMBER_KEYS) {
      if (requireMember(value, path, key, issues)) {
        checkNumber(value[key], member(path, key), issues);
      }
    }
    return;
  }
  if (kind === "invalid") {
    // 無効理由（`reason`）の語は上流が増やしうる。列挙値として絞らず、文字列として受ける。
    for (const key of ["reason", "detail"]) {
      if (requireMember(value, path, key, issues)) {
        checkString(value[key], member(path, key), issues);
      }
    }
    if (requireMember(value, path, "sample_count", issues)) {
      checkNumber(value["sample_count"], member(path, "sample_count"), issues);
    }
    if (requireMember(value, path, "based_on_time_ms", issues)) {
      checkNullableNumber(value["based_on_time_ms"], member(path, "based_on_time_ms"), issues);
    }
    return;
  }
  checkEnum(kind, PREDICTION_KINDS, member(path, "kind"), issues);
}

function validateRecord(value: unknown, path: string, issues: LoadIssue[]): void {
  if (!checkObject(value, path, issues)) {
    return;
  }
  for (const key of RECORD_STRING_KEYS) {
    if (requireMember(value, path, key, issues)) {
      checkString(value[key], member(path, key), issues);
    }
  }
  if (requireMember(value, path, "samples", issues)) {
    const samplesPath = member(path, "samples");
    const samples = value["samples"];
    if (checkArray(samples, samplesPath, issues)) {
      samples.forEach((sample, index) => {
        validateSample(sample, element(samplesPath, index), issues);
      });
    }
  }
  if (requireMember(value, path, "predictions", issues)) {
    const predictionsPath = member(path, "predictions");
    const predictions = value["predictions"];
    if (checkArray(predictions, predictionsPath, issues)) {
      predictions.forEach((prediction, index) => {
        validatePrediction(prediction, element(predictionsPath, index), issues);
      });
    }
  }
}

function unusable(path: string, detail: string): LoadIssue {
  return { code: "records_unusable", path, detail };
}

/**
 * 代表記録を取り出す。**図の読み込みは止めない**（要件 1.7）。
 * 記録側に問題があれば `records` は空のまま、理由を 1 件の問題として返す。
 */
function readRecords(root: UnknownRecord): {
  readonly records: readonly ThrowRecordDoc[];
  readonly recordsIssue: LoadIssue | null;
} {
  const path = "throw_records";
  if (!Object.hasOwn(root, path)) {
    return { records: [], recordsIssue: unusable(path, "代表記録が入力に無い") };
  }
  const value = root[path];
  if (!Array.isArray(value)) {
    return {
      records: [],
      recordsIssue: unusable(path, `代表記録が配列でない: 入力は${typeName(value)}である`),
    };
  }
  if (value.length === 0) {
    return { records: [], recordsIssue: unusable(path, "代表記録が 1 件も含まれていない") };
  }
  const issues: LoadIssue[] = [];
  value.forEach((record, index) => validateRecord(record, element(path, index), issues));
  const first = issues[0];
  if (first !== undefined) {
    const detail = issues.length === 1
      ? first.detail
      : `${first.detail}（ほかに ${issues.length - 1} 件）`;
    return { records: [], recordsIssue: unusable(first.path, detail) };
  }
  return { records: value as readonly ThrowRecordDoc[], recordsIssue: null };
}

// --- 入口 -------------------------------------------------------------------

function messageOf(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

/**
 * テキストとファイル名から、検証済みのビューまたは問題の一覧を作る。
 *
 * 前提条件は無い。あらゆる文字列を受け付け、例外を投げない。
 * 成功したとき `view.document` の全必須項目が型どおりに存在し、下流は再検証を必要としない。
 */
export function loadSweep(text: string, fileName: string): LoadResult {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text) as unknown;
  } catch (cause) {
    return {
      ok: false,
      errors: [{
        code: "not_json",
        path: ROOT_PATH,
        detail: `JSON として解釈できない: ${messageOf(cause)}`,
      }],
    };
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return {
      ok: false,
      errors: [{
        code: "not_object",
        path: ROOT_PATH,
        detail: `最上位がオブジェクトでない: 入力は${typeName(parsed)}である`,
      }],
    };
  }

  const root: UnknownRecord = parsed as UnknownRecord;
  const errors: LoadIssue[] = [];
  validateDocument(root, errors);
  if (errors.length > 0) {
    return { ok: false, errors };
  }

  const warnings: LoadIssue[] = [];
  const version = root["output_schema_version"];
  if (version !== EXPECTED_OUTPUT_SCHEMA_VERSION) {
    warnings.push({
      code: "schema_version_mismatch",
      path: "output_schema_version",
      detail: `本ビューアが想定する版と一致しない: 入力は ${JSON.stringify(version)}、`
        + `想定は ${JSON.stringify(EXPECTED_OUTPUT_SCHEMA_VERSION)}`,
    });
  }

  const { records, recordsIssue } = readRecords(root);
  // 解釈した値をそのまま渡す。複製・補完・単位換算を行わない（要件 1.5）。
  const view: SweepView = {
    fileName,
    document: root as unknown as SweepDocument,
    records,
    recordsIssue,
  };
  return { ok: true, view, warnings };
}
