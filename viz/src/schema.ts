// 上流 `trajectory-simulator` が書き出す掃引結果 JSON の型・列挙値・必須項目の宣言。
//
// このモジュールは**層 0** である。何も import せず、DOM に触れず、関数を公開しない。
// 型と定数だけを置く（要件 1.2 / 1.4）。
//
// 二つの規律をここで固定する。
//   1. 上流のフィールド名をそのまま用いる。読みやすさのための改名を行わない
//      （`x_mm` / `t_ms` / `success_ratio` / `output_schema_version` ...）。
//   2. 読む項目だけを型に書く。読まない項目（`estimated_v*` / `trajectory` /
//      `config` / `elapsed_ms` / `extra`）は型に含めない。
//
// 列挙値は上流が用いる値そのままである。画面に出す文言への翻訳は `format.ts` が持つ。
// 上流の列挙値が増えた場合、型は狭いまま実行時に未知の値が来る。その隙間は
// `load.ts` が検証時に未知の値を検出することで塞ぐ。

/** シナリオパラメータのように、任意の入れ子を取りうる値。 */
export type JsonValue =
  | null | boolean | number | string
  | readonly JsonValue[]
  | { readonly [key: string]: JsonValue };

/** 格子点の状態。 */
export type CellStatus = "catchable" | "not_catchable" | "not_evaluated";

/** 評価対象外となった理由。 */
export type NotEvaluatedReason = "no_floor_crossing" | "no_samples" | "no_valid_prediction";

/** 較正段階。 */
export type CalibrationStage = "uncalibrated" | "m1_calibrated" | "m2_calibrated";

/** パラメータの出所。 */
export type ProvenanceKind = "measured" | "assumed";

/** 掃引の種別。 */
export type SweepKind = "reachability" | "throw";

/** 掃引軸が取る値。数値と文字列のいずれもありうる。 */
export type AxisValue = number | string;

/** 掃引軸 1 本の定義。 */
export interface AxisSpec {
  readonly name: string;
  readonly unit: string;
  readonly values: readonly AxisValue[];
}

/** 掃引の定義。`catch_ratio_threshold` は試行 1 回の掃引で `null` になりうる。 */
export interface SweepSpec {
  readonly kind: SweepKind;
  readonly axes: readonly AxisSpec[];
  readonly trials_per_cell: number;
  readonly seed: number;
  readonly catch_ratio_threshold: number | null;
}

/**
 * 格子点 1 点の結果。
 * `metrics` のキー名は掃引設定に依存するため決め打ちしない。
 */
export interface CellResult {
  readonly axis_values: readonly AxisValue[];
  readonly status: CellStatus;
  readonly success_ratio: number | null;
  readonly metrics: { readonly [key: string]: number };
  readonly not_evaluated_reason: NotEvaluatedReason | null;
}

/**
 * 較正情報。`notice` は未較正のときのみ存在する。
 * 較正済み（`m1_calibrated` / `m2_calibrated`）では上流が**キー自体を省略する**
 * （`src/trajectory_sim/serialize.py` の `_calibration_to_dict`）。
 */
export interface Calibration {
  readonly stage: CalibrationStage;
  readonly notice?: string | null;
}

/** 観測サンプル 1 点。 */
export interface SampleEntry {
  readonly t_ms: number;
  readonly x_mm: number;
  readonly y_mm: number;
  readonly z_mm: number;
}

/** 有効な予測 1 件。 */
export interface PredictionEntry {
  readonly kind: "prediction";
  readonly predicted_hit_x_mm: number;
  readonly predicted_hit_y_mm: number;
  readonly predicted_hit_time_ms: number;
  readonly remaining_time_ms: number;
  readonly residual: number;
  readonly sample_count: number;
  readonly based_on_time_ms: number;
}

/** 予測が成立しなかった 1 件。 */
export interface InvalidPredictionEntry {
  readonly kind: "invalid";
  readonly reason: string;
  readonly detail: string;
  readonly sample_count: number;
  readonly based_on_time_ms: number | null;
}

/** `kind` キーによる直和。 */
export type PredictionEntryUnion = PredictionEntry | InvalidPredictionEntry;

/** 代表 Throw Record 1 件。読むキーだけを持つ。 */
export interface ThrowRecordDoc {
  readonly schema_version: string;
  readonly record_id: string;
  readonly source: string;
  readonly samples: readonly SampleEntry[];
  readonly predictions: readonly PredictionEntryUnion[];
}

/**
 * 掃引結果 JSON の最上位構造のうち、必須項目のみ。
 * 代表記録（`throw_records`）は必須ではないため、ここには含めない（要件 1.7）。
 */
export interface SweepDocument {
  readonly output_schema_version: string;
  readonly calibration: Calibration;
  readonly model_exclusions: { readonly [stage: string]: readonly string[] };
  readonly sweep: SweepSpec;
  readonly parameters: JsonValue;
  readonly parameter_provenance: { readonly [path: string]: ProvenanceKind };
  readonly cells: readonly CellResult[];
}

/**
 * 欠けていれば図を描画しない必須項目の一覧（要件 1.2）。
 * 代表記録を含めない。代表記録が無くても図の描画は継続する（要件 1.7）。
 */
export const REQUIRED_TOP_LEVEL_KEYS = [
  "output_schema_version", "calibration", "model_exclusions", "sweep",
  "parameters", "parameter_provenance", "cells",
] as const;

/** 本ビューアが想定する出力形式の版。不一致は利用者に提示する（要件 1.4）。 */
export const EXPECTED_OUTPUT_SCHEMA_VERSION = "1.0";

/** 本ビューアが想定する記録スキーマの版。 */
export const EXPECTED_RECORD_SCHEMA_VERSION = "1.0";
