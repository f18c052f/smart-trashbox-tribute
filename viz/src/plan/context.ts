// 前提・限界・同一性を、図と同じ画面に出すための行の集合へ変換する
// （要件 1.6 / 3.1〜3.5 / 4.1〜4.3、設計 `#### ContextPlanner`）。
//
// このモジュールは**層 2** である。import してよいのは `schema` / `format` のみで、
// DOM に触れない。座標を持たないため `scale` も使わない。
//
// 四つの規律をここで固定する。
//   1. **較正段階と注意書きは常に返す。** 呼び出し側が存在を確かめずに提示できる形にする
//      （要件 3.1 / 3.2）
//   2. **除外要因は段ごとに全項目を返す。** 件数だけの要約に置き換えない（要件 3.3）
//   3. **パラメータの平坦化は構造のみに基づく。** キー名・値の意味を一切解釈しない。
//      出所の記載が無い行は `provenance: null` とし、**「想定」で埋めない**（要件 3.4）
//   4. **機体パラメータの抽出はパスの前置き一致のみで行う。** どのフィールドが
//      機体パラメータかを個別に知らない（要件 4.2）

import {
  calibrationStageLabel as translateCalibrationStage,
  formatAxisValue,
  formatNumber,
} from "../format.js";
import type {
  AxisSpec,
  JsonValue,
  ProvenanceKind,
  SweepDocument,
} from "../schema.js";

// --- 公開する契約 -----------------------------------------------------------

/** ラベル付きの値 1 行。同一性パネルの各項目に使う。 */
export interface LabeledValue {
  readonly label: string;
  readonly value: string;
}

/** パラメータ表の 1 行。 */
export interface ParameterRow {
  readonly path: string;
  readonly value: string;
  readonly provenance: ProvenanceKind | null; // 出所の記載が無ければ null
}

/** モデル除外要因の 1 段。 */
export interface ExclusionGroup {
  readonly stage: string;
  readonly items: readonly string[];
}

/** 前提・限界・同一性のプラン。 */
export interface ContextPlan {
  readonly calibrationStageLabel: string;
  readonly calibrationNotice: string | null;
  readonly identity: readonly LabeledValue[]; // ファイル名・版・掃引種別・軸・試行回数・種・閾値
  readonly exclusions: readonly ExclusionGroup[];
  readonly parameters: readonly ParameterRow[];
  readonly drivetrainRows: readonly ParameterRow[];
  readonly warnings: readonly string[];
}

/**
 * 本モジュールが読む範囲の掃引ビュー。
 *
 * 設計の署名は `buildContextPlan(view: SweepView, ...)` であるが、`SweepView` は
 * 層 1 の `load.ts` にあり、[Dependency Direction](design.md) は層 2 から層 1 への
 * import を許していない（`plan/region.ts` の `RegionSource` と同じ理由）。
 * 読む項目だけを構造的に宣言することで、依存を増やさずに `SweepView` をそのまま渡せる。
 */
export interface ContextSource {
  readonly fileName: string;
  readonly document: SweepDocument;
}

/**
 * 本モジュールが読む範囲の読み込み時の警告 1 件。
 *
 * 設計の署名は `warnings: readonly LoadIssue[]` であるが、`LoadIssue` / `LoadIssueCode`
 * も層 1 の `load.ts` にある。ここが提示するのは文言だけであり、`code` / `path` という
 * Loader 内部の機構を知る必要が無いため、読む項目（`detail`）だけを構造的に宣言する。
 */
export interface ContextWarning {
  readonly detail: string;
}

// --- 同一性 -----------------------------------------------------------------

const FILE_NAME_LABEL = "ファイル名";
const SCHEMA_VERSION_LABEL = "output_schema_version";
const SWEEP_KIND_LABEL = "sweep.kind";
const TRIALS_LABEL = "trials_per_cell";
const SEED_LABEL = "seed";
const THRESHOLD_LABEL = "catch_ratio_threshold";

/** 判定閾値が入力に無いことを表す語。数値を作り出さない（要件 1.5）。 */
const NO_THRESHOLD_TEXT = "入力に無い";

/** 軸 1 本の値の一覧を並べる区切り。 */
const AXIS_VALUE_SEPARATOR = " / ";

/** 試行回数・乱数種は整数として表示する。 */
const INTEGER_DIGITS = 0;

/** 判定閾値を表示する小数桁。 */
const THRESHOLD_DIGITS = 3;

/** 掃引軸 1 本を同一性の 1 行へ変換する。 */
function axisIdentityRow(axis: AxisSpec): LabeledValue {
  const values = axis.values
    .map((value) => formatAxisValue(value, axis.unit))
    .join(AXIS_VALUE_SEPARATOR);
  return { label: `軸: ${axis.name}`, value: values };
}

/**
 * 同一性の行（要件 1.6 / 4.3）。
 * ファイル名・出力形式の版・掃引種別・軸・試行回数・乱数種・判定閾値を持つ。
 */
function buildIdentity(source: ContextSource): readonly LabeledValue[] {
  const sweep = source.document.sweep;
  const thresholdText = sweep.catch_ratio_threshold === null
    ? NO_THRESHOLD_TEXT
    : formatNumber(sweep.catch_ratio_threshold, THRESHOLD_DIGITS);
  return [
    { label: FILE_NAME_LABEL, value: source.fileName },
    { label: SCHEMA_VERSION_LABEL, value: source.document.output_schema_version },
    { label: SWEEP_KIND_LABEL, value: sweep.kind },
    ...sweep.axes.map(axisIdentityRow),
    { label: TRIALS_LABEL, value: formatNumber(sweep.trials_per_cell, INTEGER_DIGITS) },
    { label: SEED_LABEL, value: formatNumber(sweep.seed, INTEGER_DIGITS) },
    { label: THRESHOLD_LABEL, value: thresholdText },
  ];
}

// --- モデル除外要因（要件 3.3） ----------------------------------------------

/**
 * 段ごとの除外要因を、段の数・各段の項目数を保ったまま返す。
 * 段名は入力に現れたものをそのまま使う（開いた集合として扱う。段名を決め打ちしない）。
 */
function buildExclusions(doc: SweepDocument): readonly ExclusionGroup[] {
  return Object.entries(doc.model_exclusions).map(([stage, items]) => ({
    stage,
    items: [...items],
  }));
}

// --- パラメータの平坦化（要件 3.4 / 4.1 / 4.2） ------------------------------

/** 平坦化の途中結果。パスとまだ表示用文字列化していない末端値。 */
interface FlatEntry {
  readonly path: string;
  readonly text: string;
}

/** 機体パラメータの抽出に使う前置き。これ以外の判断基準を持たない（要件 4.2）。 */
const DRIVETRAIN_PATH_PREFIX = "drivetrain.";

/** ルート直下の項目は前置きの点を付けない。 */
function childPath(base: string, segment: string): string {
  return base === "" ? segment : `${base}.${segment}`;
}

/** 空のオブジェクト・配列は、それ以上辿れる項目を持たないため末端として扱う。 */
function isEmptyContainer(value: JsonValue): boolean {
  if (Array.isArray(value)) {
    return value.length === 0;
  }
  if (typeof value === "object" && value !== null) {
    return Object.keys(value).length === 0;
  }
  return false;
}

/** 値がこれ以上辿れない末端かどうか。値の意味は見ない。構造だけで判断する。 */
function isLeaf(value: JsonValue): boolean {
  return value === null || typeof value !== "object" || isEmptyContainer(value);
}

/**
 * 末端の値を表示用の文字列にする。パラメータの意味を知らないため、
 * 数値を軸・指標向けの固定 3 桁へ丸めない（`format.ts` の丸めは、精度が意味を持つと
 * 分かっている軸の値・指標のための規約であり、意味を知らない値へ持ち込むと、
 * 小さいが非ゼロの値（許容誤差・係数など）が丸めで真のゼロと同じ表記に潰れてしまう。
 * これは要件 A-3「誤った安心を防ぐ」に反する）。
 * `String()` による既定の文字列化は有限の数値を可逆に表せるため、これを直接用いる。
 */
function stringifyLeaf(value: JsonValue): string {
  if (value === null) {
    return "null";
  }
  if (typeof value === "boolean") {
    return String(value);
  }
  if (typeof value === "number") {
    return String(value);
  }
  if (typeof value === "string") {
    return value;
  }
  // 空配列・空オブジェクトのみここに到達する（`isLeaf` が末端として扱うため）。
  return Array.isArray(value) ? "[]" : "{}";
}

/**
 * 深さ優先で末端まで下り、パスと文字列化した値を集める。
 * オブジェクトのキーはそのままパスの区切りへ、配列の添字は数値の区切りへ変える。
 * どちらの規則も構造だけで決まり、パラメータの意味を必要としない。
 */
function flattenInto(value: JsonValue, path: string, out: FlatEntry[]): void {
  if (isLeaf(value)) {
    out.push({ path, text: stringifyLeaf(value) });
    return;
  }
  if (Array.isArray(value)) {
    value.forEach((item, index) => flattenInto(item, childPath(path, String(index)), out));
    return;
  }
  for (const [key, child] of Object.entries(value as { readonly [key: string]: JsonValue })) {
    flattenInto(child, childPath(path, key), out);
  }
}

function flattenParameters(parameters: JsonValue): readonly FlatEntry[] {
  const out: FlatEntry[] = [];
  flattenInto(parameters, "", out);
  return out;
}

/**
 * 平坦化したパスを `parameter_provenance` と突き合わせる。
 * **記載が無いパスは `null` のままにする。「想定」で埋めない**（要件 3.4 の中心）。
 */
function buildParameterRows(doc: SweepDocument): readonly ParameterRow[] {
  const provenance = doc.parameter_provenance;
  return flattenParameters(doc.parameters).map((entry) => ({
    path: entry.path,
    value: entry.text,
    provenance: provenance[entry.path] ?? null,
  }));
}

/** パスが `drivetrain.` で始まる行だけを抽出する。前置き一致のみが基準である（要件 4.2）。 */
function buildDrivetrainRows(rows: readonly ParameterRow[]): readonly ParameterRow[] {
  return rows.filter((row) => row.path.startsWith(DRIVETRAIN_PATH_PREFIX));
}

// --- 読み込み時の警告 ---------------------------------------------------------

function buildWarnings(warnings: readonly ContextWarning[]): readonly string[] {
  return warnings.map((warning) => warning.detail);
}

// --- 入口 ---------------------------------------------------------------------

/**
 * 掃引ビューと読み込み時の警告から、前提・限界・同一性のプランを組み立てる。
 *
 * 前提条件は無い。`doc.parameters` がどのような入れ子であっても例外を投げない。
 * 事後条件として `exclusions` の要素数と各 `items` の長さは `doc.model_exclusions` と
 * 一致し、`parameters` の各行の `path` は一意である（設計 ContextPlanner の Invariants）。
 */
export function buildContextPlan(
  view: ContextSource,
  warnings: readonly ContextWarning[],
): ContextPlan {
  const doc = view.document;
  const parameters = buildParameterRows(doc);
  return {
    calibrationStageLabel: translateCalibrationStage(doc.calibration.stage),
    calibrationNotice: doc.calibration.notice ?? null,
    identity: buildIdentity(view),
    exclusions: buildExclusions(doc),
    parameters,
    drivetrainRows: buildDrivetrainRows(parameters),
    warnings: buildWarnings(warnings),
  };
}
