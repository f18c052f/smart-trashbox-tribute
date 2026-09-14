// 上流 `trajectory-simulator` の出力形式に沿った**最小入力**を生成する関数群（要件 1.2）。
//
// このモジュールはテストの土台であり、次の 3 つを規律とする。
//
//   1. **ファイルシステムを使わない。** JSON ファイルを置いて読み直すのではなく、
//      テストから直接呼べる値として組み立てる。ネットワークにも触れない。
//   2. **上流のフィールド名・列挙値をそのまま用いる。** 値は `schema.ts` の宣言に
//      対して型検査されるため、上流の形から外れればビルドが落ちる。
//   3. **既定は最小で、必要な差分だけを呼び出し側が上書きする。** 各生成関数は
//      「必須項目をすべて備えた最小の値」を返し、`overrides` で個別に差し替える。
//
// 具体値（除外要因・指標キー・パラメータのパス・無効理由）は上流の実装
// （`src/trajectory_sim/results.py` / `sweep.py` / `params.py`、
// `src/prediction_core/types.py` / `record.py`）から採った現実的なものである。
//
// `SweepDocument` として型付けされた値は**正しい入力しか作れない**。
// Loader のテストは逆に**壊れた入力**を必要とするため、素のオブジェクト・
// JSON 文字列へ落とす逃げ道（`toPlainObject` / `omitKey` / `setKey` / `toJsonText`）を
// 併せて公開する。呼び出し側が `as any` を書かずに必須キーを落とせるようにするためである。

import {
  EXPECTED_OUTPUT_SCHEMA_VERSION,
  EXPECTED_RECORD_SCHEMA_VERSION,
  type AxisSpec,
  type AxisValue,
  type Calibration,
  type CellResult,
  type CellStatus,
  type InvalidPredictionEntry,
  type JsonValue,
  type NotEvaluatedReason,
  type PredictionEntry,
  type PredictionEntryUnion,
  type ProvenanceKind,
  type SampleEntry,
  type SweepDocument,
  type SweepSpec,
  type ThrowRecordDoc,
} from "../src/schema.js";

// --- 列挙値の網羅表（`schema.ts` の直和と対応する。実行時に走査するために必要） ---

/** `CellStatus` の全値。 */
export const CELL_STATUSES: readonly CellStatus[] = [
  "catchable",
  "not_catchable",
  "not_evaluated",
];

/** `NotEvaluatedReason` の全値。 */
export const NOT_EVALUATED_REASONS: readonly NotEvaluatedReason[] = [
  "no_floor_crossing",
  "no_samples",
  "no_valid_prediction",
];

/** `model_exclusions` の段名（上流 `MODEL_EXCLUSIONS` の 4 段）。 */
export const MODEL_EXCLUSION_STAGES: readonly string[] = [
  "throw_physics",
  "observation",
  "drivetrain",
  "catch",
];

/** 除外要因の総数。段ごとの要因が 1 つでも落ちれば、この値と食い違う（要件 3.3）。 */
export const MODEL_EXCLUSION_FACTOR_COUNT = 12;

/** 未較正のときに上流が添える注意書き（`serialize.py` の `_UNCALIBRATED_NOTICE`）。 */
export const UNCALIBRATED_NOTICE =
  "本結果は較正段階が未較正のため感度分析用であり、絶対値を信用してはならない。";

// --- 掃引軸 -----------------------------------------------------------------

/** 掃引軸 1 本。既定は到達性掃引の持ち時間軸。 */
export function makeAxis(overrides: Partial<AxisSpec> = {}): AxisSpec {
  return { name: "hold_time_ms", unit: "ms", values: [400, 600, 800], ...overrides };
}

/** 1 軸の掃引（要件 2.8: 全格子点が 1 行に並ぶ）。 */
export const ONE_AXIS: readonly AxisSpec[] = [
  makeAxis({ name: "required_distance_mm", unit: "mm", values: [300, 600, 900, 1200] }),
];

/** 2 軸の掃引。既定の掃引定義でもある。 */
export const TWO_AXES: readonly AxisSpec[] = [
  makeAxis({ name: "hold_time_ms", unit: "ms", values: [400, 600, 800] }),
  makeAxis({ name: "required_distance_mm", unit: "mm", values: [300, 600, 900, 1200] }),
];

/** 3 軸の掃引（要件 2.7: 描画に使わない軸が固定軸になる）。文字列を取る軸を 1 本含む。 */
export const THREE_AXES: readonly AxisSpec[] = [
  makeAxis({ name: "hold_time_ms", unit: "ms", values: [400, 600] }),
  makeAxis({ name: "required_distance_mm", unit: "mm", values: [300, 600, 900] }),
  makeAxis({ name: "catch_policy", unit: "", values: ["stop_and_wait", "pass_through"] }),
];

// --- 格子点 -----------------------------------------------------------------

/** 上流が格子点ごとに平均する指標（`sweep.py` の `_METRIC_FIELDS`）。 */
function makeMetrics(index: number): { readonly [key: string]: number } {
  return {
    position_error_mm: 12.5 + index,
    hold_time_ms: 480 + index * 10,
    required_distance_mm: 620 + index * 25,
    prediction_error_mm: 30.5 + index * 2,
    residual_speed_mm_s: 45 + index,
  };
}

/**
 * 状態に応じた最小の格子点を作る。
 * 評価対象外の格子点だけが理由を持ち、成立割合と指標を持たない。
 */
export function makeCellForStatus(
  status: CellStatus,
  axisValues: readonly AxisValue[],
  index = 0,
): CellResult {
  if (status === "not_evaluated") {
    const reason = NOT_EVALUATED_REASONS[index % NOT_EVALUATED_REASONS.length];
    return {
      axis_values: [...axisValues],
      status,
      success_ratio: null,
      metrics: {},
      not_evaluated_reason: reason ?? "no_samples",
    };
  }
  return {
    axis_values: [...axisValues],
    status,
    success_ratio: status === "catchable" ? 1 : 0,
    metrics: makeMetrics(index),
    not_evaluated_reason: null,
  };
}

/** 任意の 1 格子点。既定は成立した格子点。 */
export function makeCell(overrides: Partial<CellResult> = {}): CellResult {
  return { ...makeCellForStatus("catchable", [400, 300]), ...overrides };
}

/** 格子点ごとの状態を決める関数。 */
export type StatusAt = (index: number, axisValues: readonly AxisValue[]) => CellStatus;

/** 3 状態を順に割り当てる。成立・不成立・評価対象外を必ず混在させたいときに使う。 */
export const cyclingStatusAt: StatusAt = (index) => {
  const status = CELL_STATUSES[index % CELL_STATUSES.length];
  return status ?? "catchable";
};

/** 軸の値の全組み合わせを、先頭の軸が最も外側になる順で返す。 */
export function axisValueCombinations(axes: readonly AxisSpec[]): readonly (readonly AxisValue[])[] {
  let combinations: AxisValue[][] = [[]];
  for (const axis of axes) {
    const expanded: AxisValue[][] = [];
    for (const combination of combinations) {
      for (const value of axis.values) {
        expanded.push([...combination, value]);
      }
    }
    combinations = expanded;
  }
  return combinations;
}

/** 軸の定義から格子点の配列を作る。件数は各軸の値の個数の積になる。 */
export function makeGridCells(
  axes: readonly AxisSpec[],
  statusAt: StatusAt = () => "catchable",
): readonly CellResult[] {
  return axisValueCombinations(axes).map((axisValues, index) =>
    makeCellForStatus(statusAt(index, axisValues), axisValues, index));
}

// --- 掃引定義・較正・前提と限界 ---------------------------------------------

/** 掃引定義。試行 1 回にするときは `catch_ratio_threshold` を `null` へ上書きする。 */
export function makeSweepSpec(overrides: Partial<SweepSpec> = {}): SweepSpec {
  return {
    kind: "reachability",
    axes: TWO_AXES,
    trials_per_cell: 20,
    seed: 12345,
    catch_ratio_threshold: 0.8,
    ...overrides,
  };
}

/** 較正情報。既定は未較正（注意書きあり）。較正済みでは `notice` を `null` にできる。 */
export function makeCalibration(overrides: Partial<Calibration> = {}): Calibration {
  return { stage: "uncalibrated", notice: UNCALIBRATED_NOTICE, ...overrides };
}

/** 段ごとの除外要因（上流 `MODEL_EXCLUSIONS` そのもの。4 段 12 要因）。 */
export function makeModelExclusions(): { readonly [stage: string]: readonly string[] } {
  return {
    throw_physics: ["air_drag", "spin", "bounce"],
    observation: ["sensor_distortion", "field_of_view", "occlusion", "timestamp_jitter"],
    drivetrain: [
      "wheel_slip",
      "direction_dependent_performance",
      "inverse_kinematics",
      "speed_control_dynamics",
    ],
    catch: ["bounce_out"],
  };
}

/**
 * シナリオパラメータの木（上流 `ScenarioParams` の形）。
 * ドット区切りへ平坦化すると `throw.` / `observation.` / `drivetrain.` / `catch.` /
 * `layout.` で始まるパスになる。
 */
export function makeParameters(): JsonValue {
  return {
    throw: {
      release_x_mm: 0,
      release_y_mm: 0,
      release_z_mm: 1400,
      speed_mm_s: 4200,
      elevation_deg: 35,
      azimuth_deg: 0,
      object_diameter_mm: 65,
    },
    observation: {
      detection_start_delay_ms: 20,
      sample_period_ms: 10,
      sample_latency_ms: 5,
      prediction_latency_ms: 4,
    },
    drivetrain: {
      max_speed_mm_s: 1800,
      control_period_ms: 10,
      command_latency_ms: 30,
      integration_step_ms: 1,
    },
    catch: {
      policy: "stop_and_wait",
      position_tolerance_mm: 67.5,
      residual_speed_tolerance_mm_s: 200,
    },
    layout: { home_x_mm: 0, home_y_mm: -500 },
    calibration_stage: "uncalibrated",
  };
}

/**
 * パラメータの出所。`makeParameters` の平坦化パスの**一部だけ**を覆う。
 * 覆われないパスがあることで、記載の無い行を `provenance: null` として扱う
 * 経路が下流のテストから到達できる（要件 3.4）。`drivetrain.` で始まるパスを含む。
 */
export function makeParameterProvenance(): { readonly [path: string]: ProvenanceKind } {
  return {
    "throw.speed_mm_s": "measured",
    "throw.elevation_deg": "assumed",
    "observation.sample_period_ms": "measured",
    "drivetrain.max_speed_mm_s": "measured",
    "drivetrain.command_latency_ms": "assumed",
    "catch.position_tolerance_mm": "assumed",
  };
}

// --- 代表 Throw Record ------------------------------------------------------

/** 観測サンプル 1 点。 */
export function makeSample(overrides: Partial<SampleEntry> = {}): SampleEntry {
  return { t_ms: 0, x_mm: 0, y_mm: 0, z_mm: 1400, ...overrides };
}

/**
 * 等間隔・等速の観測サンプル列。
 * 隣接 2 点の中間時刻がちょうど中点になるため、線形補間の検証に使える（要件 5.6）。
 */
export function makeSamples(): readonly SampleEntry[] {
  return [0, 30, 60, 90, 120].map((t, index) =>
    makeSample({ t_ms: t, x_mm: index * 100, y_mm: index * 40, z_mm: 1400 - index * 200 }));
}

/** 有効な予測 1 件。 */
export function makePrediction(
  overrides: Partial<Omit<PredictionEntry, "kind">> = {},
): PredictionEntry {
  return {
    kind: "prediction",
    predicted_hit_x_mm: 420,
    predicted_hit_y_mm: 168,
    predicted_hit_time_ms: 240,
    remaining_time_ms: 150,
    residual: 1.8,
    sample_count: 4,
    based_on_time_ms: 90,
    ...overrides,
  };
}

/** 成立しなかった予測 1 件。落下地点の座標を持たない。 */
export function makeInvalidPrediction(
  overrides: Partial<Omit<InvalidPredictionEntry, "kind">> = {},
): InvalidPredictionEntry {
  return {
    kind: "invalid",
    reason: "insufficient_samples",
    detail: "有効な観測サンプルが最小サンプル数に満たない",
    sample_count: 1,
    based_on_time_ms: 30,
    ...overrides,
  };
}

/**
 * 予測の系列。無効な予測 1 件と有効な予測 2 件を、基準時刻の異なる順序で持つ。
 * 「`based_on_time_ms <= timeMs` を満たす最後の予測」の選択を試せる（要件 5.2）。
 */
export function makePredictions(): readonly PredictionEntryUnion[] {
  return [
    makeInvalidPrediction({ based_on_time_ms: 30, sample_count: 1 }),
    makePrediction({
      based_on_time_ms: 60,
      predicted_hit_x_mm: 380,
      predicted_hit_y_mm: 152,
      predicted_hit_time_ms: 250,
      remaining_time_ms: 190,
      residual: 4.4,
      sample_count: 3,
    }),
    makePrediction({ based_on_time_ms: 90 }),
  ];
}

/** 代表記録 1 件。読むキーだけを持つ（`config` / `extra` などは含めない）。 */
export function makeThrowRecord(overrides: Partial<ThrowRecordDoc> = {}): ThrowRecordDoc {
  return {
    schema_version: EXPECTED_RECORD_SCHEMA_VERSION,
    record_id: "sweep-cell-0007-trial-000",
    source: "simulated",
    samples: makeSamples(),
    predictions: makePredictions(),
    ...overrides,
  };
}

/** 観測サンプルが 1 点も無い記録（軌跡を描けない場合の縮退経路）。 */
export function makeEmptySampleThrowRecord(): ThrowRecordDoc {
  return makeThrowRecord({
    record_id: "sweep-cell-0011-trial-000",
    samples: [],
    predictions: [makeInvalidPrediction({ reason: "insufficient_samples", sample_count: 0 })],
  });
}

// --- 掃引結果ドキュメント ---------------------------------------------------

/** 代表記録を含む掃引結果。`throw_records` は必須ではないため、型を分けて表す。 */
export interface SweepDocumentWithRecords extends SweepDocument {
  readonly throw_records: readonly ThrowRecordDoc[];
}

/**
 * 必須項目をすべて備えた最小の掃引結果。
 * **代表記録を含まない**（要件 1.7: 代表記録が無くても図の描画は継続する）。
 */
export function makeSweepDocument(overrides: Partial<SweepDocument> = {}): SweepDocument {
  return {
    output_schema_version: EXPECTED_OUTPUT_SCHEMA_VERSION,
    calibration: makeCalibration(),
    model_exclusions: makeModelExclusions(),
    sweep: makeSweepSpec(),
    parameters: makeParameters(),
    parameter_provenance: makeParameterProvenance(),
    cells: makeGridCells(TWO_AXES, cyclingStatusAt),
    ...overrides,
  };
}

/** 代表記録を含む掃引結果。既定で有効な予測と無効な予測の双方を持つ記録を 1 件持つ。 */
export function makeSweepDocumentWithRecords(
  overrides: Partial<SweepDocumentWithRecords> = {},
): SweepDocumentWithRecords {
  return { ...makeSweepDocument(), throw_records: [makeThrowRecord()], ...overrides };
}

/** `makeGridDocument` の指定。 */
export interface GridOptions {
  readonly axes?: readonly AxisSpec[];
  readonly statusAt?: StatusAt;
  readonly overrides?: Partial<SweepDocument>;
}

/**
 * 軸の定義から掃引結果を作る。`sweep.axes` と `cells` が必ず整合し、
 * 格子点数は各軸の値の個数の積になる。1 軸 / 2 軸 / 3 軸の掃引をここから作る。
 */
export function makeGridDocument(options: GridOptions = {}): SweepDocument {
  const axes = options.axes ?? TWO_AXES;
  const statusAt = options.statusAt ?? (() => "catchable");
  return makeSweepDocument({
    sweep: makeSweepSpec({ axes }),
    cells: makeGridCells(axes, statusAt),
    ...options.overrides,
  });
}

/** 成立・不成立・評価対象外が混在する掃引結果。 */
export function makeMixedStatusDocument(overrides: Partial<SweepDocument> = {}): SweepDocument {
  return makeGridDocument({ axes: TWO_AXES, statusAt: cyclingStatusAt, overrides });
}

// --- 壊れた入力を組み立てるための逃げ道 -------------------------------------

/** 型の制約を外した、書き換え可能な素のオブジェクト。 */
export type PlainDocument = { [key: string]: unknown };

/** JSON 文字列へ落とす。ファイルには書き出さない。 */
export function toJsonText(value: unknown): string {
  return JSON.stringify(value);
}

/**
 * 深い複製を作り、書き換え可能な素のオブジェクトとして返す。
 * 元の値とは何も共有しないため、呼び出し側は自由に壊してよい。
 */
export function toPlainObject(value: object): PlainDocument {
  return JSON.parse(JSON.stringify(value)) as PlainDocument;
}

/** キーを 1 つ落とした複製を返す。元のオブジェクトは変更しない。 */
export function omitKey(document: PlainDocument, key: string): PlainDocument {
  const copy: PlainDocument = { ...document };
  delete copy[key];
  return copy;
}

/** キーを 1 つ差し替えた複製を返す。元のオブジェクトは変更しない。 */
export function setKey(document: PlainDocument, key: string, value: unknown): PlainDocument {
  return { ...document, [key]: value };
}

/** JSON として解釈できない文字列。 */
export const NOT_JSON_TEXT = "{ これは JSON ではない";

/** JSON ではあるがオブジェクトでない入力。 */
export const JSON_ARRAY_TEXT = "[]";

/** JSON ではあるがオブジェクトでない入力（`null`）。 */
export const JSON_NULL_TEXT = "null";
