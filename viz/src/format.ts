// 画面に出る文言をここだけで組み立てる（要件 2.3 / 2.9 / 3.6 / 5.4）。
//
// このモジュールは**層 1** である。import してよいのは `schema` のみで、DOM に触れない。
//
// 三つの規律を置く。
//   1. 上流の列挙値から表示名への対応表は**このモジュールにのみ**置く。
//      表示名を別の場所で作れば、同じ値が画面の場所ごとに違う語で出る
//   2. **上流の語を判定語へ読み替えない。** 上流の語を残したまま補足を添える
//      （`catchable` → 「catchable（到達可）」）。読み替えは、入力 JSON が
//      述べていない判定を表示側で作ることに等しい（要件 3.7）
//   3. **未知の指標キー・未知の無効理由はそのまま返す。** 「その他」へ丸めない。
//      表示できない情報を握りつぶすと、上流の変更が画面から見えなくなる
//
// これらの帰結として、どの公開関数も断定語を含む文字列を返さない
//（要件 3.6、境界検査 B-10）。この性質は `tests/format.test.ts` が全戻り値を
// 走査して固定する。
import type {
  AxisValue,
  CalibrationStage,
  CellStatus,
  NotEvaluatedReason,
  ProvenanceKind,
} from "./schema.js";

// `Number.prototype.toFixed` が受け付ける小数桁の上限。
const MAX_FRACTION_DIGITS = 20;

// 桁数の指定が無い場面（単位付き表示）で用いる小数桁。
const DEFAULT_FRACTION_DIGITS = 3;

/**
 * 数値を指定の小数桁で表示する。
 * 有限でない値は文字列化してそのまま返す（値が壊れていることを画面から隠さない）。
 */
export function formatNumber(value: number, digits: number): string {
  if (!Number.isFinite(value)) {
    return String(value);
  }
  const clamped = Math.min(Math.max(Math.round(digits), 0), MAX_FRACTION_DIGITS);
  return value.toFixed(clamped);
}

/**
 * 数値を単位付きで表示する。単位は上流の軸定義・フィールド名の接尾辞に従う。
 * 整数は小数点を付けずに出す（上流の軸の値をそのままの形で読めるようにする）。
 */
export function formatWithUnit(value: number, unit: string): string {
  const digits = Number.isInteger(value) ? 0 : DEFAULT_FRACTION_DIGITS;
  const text = formatNumber(value, digits);
  return unit === "" ? text : `${text} ${unit}`;
}

/**
 * 掃引軸の値を表示する。
 * 文字列の軸の値は分類名であり、単位を付けると上流に無い意味を足すことになるため、
 * そのまま返す。
 */
export function formatAxisValue(value: AxisValue, unit: string): string {
  if (typeof value === "string") {
    return value;
  }
  return formatWithUnit(value, unit);
}

// 上流の列挙値 → 表示名。`Record` で持つため、上流の列挙値が増えたときに
// この表が埋まっていなければ **tsc が落ちる**。
const STATUS_LABELS: Readonly<Record<CellStatus, string>> = {
  catchable: "catchable（到達可）",
  not_catchable: "not_catchable（到達不可）",
  not_evaluated: "not_evaluated（評価対象外）",
};

const NOT_EVALUATED_REASON_LABELS: Readonly<Record<NotEvaluatedReason, string>> = {
  no_floor_crossing: "no_floor_crossing（床面の通過が無い）",
  no_samples: "no_samples（観測サンプルが無い）",
  no_valid_prediction: "no_valid_prediction（有効な予測が無い）",
};

const CALIBRATION_STAGE_LABELS: Readonly<Record<CalibrationStage, string>> = {
  uncalibrated: "uncalibrated（未較正）",
  m1_calibrated: "m1_calibrated（M1 較正済み）",
  m2_calibrated: "m2_calibrated（M2 較正済み）",
};

const PROVENANCE_LABELS: Readonly<Record<ProvenanceKind, string>> = {
  measured: "measured（実測）",
  assumed: "assumed（想定）",
};

// 上流 `trajectory_sim` が `metrics` に入れる既知のキー → 単位付きの表示名。
// 表に無いキーはそのまま返るため、上流がキーを増やしても画面から消えない。
// `Map` で持つのは、素のオブジェクトでは `toString` のようなキーが
// プロトタイプ由来の値を拾ってしまうためである。
const METRIC_LABELS: ReadonlyMap<string, string> = new Map([
  ["position_error_mm", "position_error_mm（位置誤差 mm）"],
  ["hold_time_ms", "hold_time_ms（持ち時間 ms）"],
  ["required_distance_mm", "required_distance_mm（必要移動量 mm）"],
  ["prediction_error_mm", "prediction_error_mm（予測誤差 mm）"],
  ["residual_speed_mm_s", "residual_speed_mm_s（残留速度 mm/s）"],
]);

// 上流 `prediction_core.InvalidReason` の値 → 表示名。
const INVALID_REASON_LABELS: ReadonlyMap<string, string> = new Map([
  ["insufficient_samples", "insufficient_samples（観測サンプル数が下限に届かない）"],
  ["degenerate_time", "degenerate_time（観測時刻が縮退している）"],
  ["no_future_floor_crossing", "no_future_floor_crossing（以後に床面を通過しない）"],
  ["non_finite_value", "non_finite_value（有限でない値を含む）"],
  ["malformed_input", "malformed_input（入力が契約を満たさない）"],
]);

/** 格子点の状態の表示名。 */
export function statusLabel(status: CellStatus): string {
  return STATUS_LABELS[status];
}

/** 評価対象外となった理由の表示名。 */
export function notEvaluatedReasonLabel(reason: NotEvaluatedReason): string {
  return NOT_EVALUATED_REASON_LABELS[reason];
}

/** 較正段階の表示名。 */
export function calibrationStageLabel(stage: CalibrationStage): string {
  return CALIBRATION_STAGE_LABELS[stage];
}

/** パラメータの出所の表示名。 */
export function provenanceLabel(kind: ProvenanceKind): string {
  return PROVENANCE_LABELS[kind];
}

/** 指標キーの表示名。**未知のキーはそのまま返す。** */
export function metricLabel(key: string): string {
  return METRIC_LABELS.get(key) ?? key;
}

/** 無効な予測の理由の表示名。**未知の理由はそのまま返す。** */
export function invalidReasonLabel(reason: string): string {
  return INVALID_REASON_LABELS.get(reason) ?? reason;
}
