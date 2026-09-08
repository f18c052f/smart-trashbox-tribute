// 前提・限界・同一性のプランを検証する（要件 1.6 / 3.1〜3.5 / 4.1〜4.3、
// 設計 `#### ContextPlanner`）。
//
// ここで固定するのは次の 8 点である。
//   1. モデル除外要因は段ごとに全項目が残り、件数の要約に丸められない（要件 3.3）
//   2. パラメータの平坦化は構造のみに基づく（オブジェクトはキー、配列は添字で辿る）
//   3. 平坦化したパスが `parameter_provenance` のキーと突き合い、
//      **記載の無い行は `provenance: null` になる（「想定」で埋めない）**（要件 3.4）
//   4. 平坦化したパスは一意である（設計 ContextPlanner の Invariants）
//   5. `drivetrainRows` はパスの前置き一致だけで決まり、`parameters` にも同じ行が残る（要件 4.2）
//   6. 較正段階は常に返り、`notice` はキーが無い／`null`／実在の 3 通りが正しく畳み込まれる
//      （要件 3.1 / 3.2）
//   7. 同一性の行にファイル名・版・掃引種別・軸・試行回数・乱数種・判定閾値が現れる（要件 1.6 / 4.3）
//   8. 読み込み時の警告が文言として現れ、本モジュールが作る文言に断定語が現れない
//
// 入力は `fixtures.ts` で組み立て、`loadSweep` を通してから渡す。
// 実際の経路と同じ形で作ることで、`load.ts` の `SweepView` / `LoadIssue` が
// そのまま渡せることも同時に示す。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { loadSweep, type LoadIssue, type SweepView } from "../src/load.js";
import {
  buildContextPlan,
  type ContextPlan,
  type ParameterRow,
} from "../src/plan/context.js";
import { calibrationStageLabel } from "../src/format.js";
import type { CalibrationStage, JsonValue, SweepDocument } from "../src/schema.js";
import {
  MODEL_EXCLUSION_FACTOR_COUNT,
  MODEL_EXCLUSION_STAGES,
  THREE_AXES,
  UNCALIBRATED_NOTICE,
  makeCalibration,
  makeModelExclusions,
  makeParameterProvenance,
  makeSweepDocument,
  makeSweepSpec,
  toJsonText,
} from "./fixtures.js";

// --- 補助 -------------------------------------------------------------------

const FILE_NAME = "sweep-reachability.json";

/** 掃引結果を実際の読み込み経路へ通し、検証済みのビューを得る。 */
function loadOf(document: SweepDocument): { view: SweepView; warnings: readonly LoadIssue[] } {
  const result = loadSweep(toJsonText(document), FILE_NAME);
  if (!result.ok) {
    const detail = result.errors.map((issue) => `${issue.code} ${issue.path}`).join(" / ");
    throw new Error(`テストの前提となる入力が読み込めない: ${detail}`);
  }
  return { view: result.view, warnings: result.warnings };
}

/** 既定の読み込み経路でプランを組み立てる。警告は空のまま渡す。 */
function planOf(document: SweepDocument): ContextPlan {
  const { view, warnings } = loadOf(document);
  return buildContextPlan(view, warnings);
}

/** プランが持つ文字列をすべて集める。文言の走査に使う。 */
function textsOf(plan: ContextPlan): readonly string[] {
  const texts: string[] = [plan.calibrationStageLabel];
  if (plan.calibrationNotice !== null) {
    texts.push(plan.calibrationNotice);
  }
  for (const row of plan.identity) {
    texts.push(row.label, row.value);
  }
  for (const group of plan.exclusions) {
    texts.push(group.stage, ...group.items);
  }
  for (const row of plan.parameters) {
    texts.push(row.path, row.value);
  }
  texts.push(...plan.warnings);
  return texts;
}

function rowByPath(rows: readonly ParameterRow[], path: string): ParameterRow | undefined {
  return rows.find((row) => row.path === path);
}

// --- モデル除外要因（要件 3.3） ----------------------------------------------

test("モデル除外要因が段ごとに全項目残り、件数の要約に置き換えられない（要件 3.3）", () => {
  const plan = planOf(makeSweepDocument());

  assert.equal(plan.exclusions.length, MODEL_EXCLUSION_STAGES.length);
  const stages = plan.exclusions.map((group) => group.stage);
  assert.deepEqual(new Set(stages), new Set(MODEL_EXCLUSION_STAGES));

  const source = makeModelExclusions();
  let totalItems = 0;
  for (const group of plan.exclusions) {
    const expected = source[group.stage];
    assert.ok(expected !== undefined, `入力に無い段が現れている: ${group.stage}`);
    assert.deepEqual(group.items, expected, `段 ${group.stage} の項目が入力と一致しない`);
    totalItems += group.items.length;
  }
  assert.equal(totalItems, MODEL_EXCLUSION_FACTOR_COUNT, "除外要因の総数が入力と一致しない");
});

// --- パラメータの平坦化と出所（要件 3.4 / 4.1） ------------------------------

test("平坦化したパラメータのパスが parameter_provenance のキーと突き合う（要件 3.4）", () => {
  const plan = planOf(makeSweepDocument());
  const provenance = makeParameterProvenance();

  for (const [path, kind] of Object.entries(provenance)) {
    const row = rowByPath(plan.parameters, path);
    assert.ok(row !== undefined, `出所が記載されたパスが平坦化に現れない: ${path}`);
    assert.equal(row?.provenance, kind);
  }
});

test("出所の記載が無いパスは provenance: null になり、想定で埋められない（要件 3.4）", () => {
  const plan = planOf(makeSweepDocument());
  const provenance = makeParameterProvenance();

  // makeParameters() が持つが makeParameterProvenance() が覆わないパス。
  const uncoveredPath = "throw.release_z_mm";
  assert.equal(Object.hasOwn(provenance, uncoveredPath), false, "テストの前提が崩れている");

  const row = rowByPath(plan.parameters, uncoveredPath);
  assert.ok(row !== undefined, `パスが平坦化結果に無い: ${uncoveredPath}`);
  assert.equal(row?.provenance, null);
  // 「想定」を勝手に補っていないことを、値そのものでも確認する。
  assert.notEqual(row?.provenance, "assumed");
});

test("入力に無いパスの出所は幽霊行として作らない", () => {
  // makeParameterProvenance() にのみ現れ、makeParameters() の木には無いパスは無い前提だが、
  // 仮にそうであっても phantom row を作らないことを、行数と一致させて確認する。
  const document = makeSweepDocument();
  const plan = planOf(document);
  const flattenedPaths = new Set(plan.parameters.map((row) => row.path));
  for (const path of Object.keys(document.parameter_provenance)) {
    assert.ok(flattenedPaths.has(path), `出所のみに存在するパスがある: ${path}`);
  }
});

test("パラメータ全体が到達可能である（要件 4.1）", () => {
  const plan = planOf(makeSweepDocument());
  // makeParameters() の全リーフ数を数える。木は固定なので手で数えた期待値と突き合わせる。
  // throw(7) + observation(4) + drivetrain(4) + catch(3) + layout(2) + calibration_stage(1)
  assert.equal(plan.parameters.length, 7 + 4 + 4 + 3 + 2 + 1);
  assert.ok(rowByPath(plan.parameters, "throw.speed_mm_s") !== undefined);
  assert.ok(rowByPath(plan.parameters, "layout.home_x_mm") !== undefined);
  assert.equal(rowByPath(plan.parameters, "calibration_stage")?.value, "uncalibrated");
});

test("平坦化したパラメータの各パスが一意である（設計 ContextPlanner の Invariants）", () => {
  const plan = planOf(makeSweepDocument());
  const paths = plan.parameters.map((row) => row.path);
  assert.equal(new Set(paths).size, paths.length, "重複するパスがある");
});

// --- 平坦化の意味論: 深い木・配列・多様な末端型 ------------------------------

test("平坦化はオブジェクトのキーをドット区切りで辿り、任意の深さに対応する", () => {
  const parameters: JsonValue = {
    a: { b: { c: { d: 42 } } },
    sibling: 1,
  };
  const document = makeSweepDocument({ parameters, parameter_provenance: {} });
  const plan = planOf(document);

  const paths = new Set(plan.parameters.map((row) => row.path));
  assert.ok(paths.has("a.b.c.d"));
  assert.ok(paths.has("sibling"));
  assert.equal(plan.parameters.length, 2);
  assert.equal(rowByPath(plan.parameters, "a.b.c.d")?.value, "42");
});

test("配列は添字を用いてパスへ展開する（構造のみに基づく規則）", () => {
  const parameters: JsonValue = {
    offsets_mm: [10, 20, 30],
  };
  const document = makeSweepDocument({ parameters, parameter_provenance: {} });
  const plan = planOf(document);

  assert.equal(rowByPath(plan.parameters, "offsets_mm.0")?.value, "10");
  assert.equal(rowByPath(plan.parameters, "offsets_mm.1")?.value, "20");
  assert.equal(rowByPath(plan.parameters, "offsets_mm.2")?.value, "30");
  assert.equal(plan.parameters.length, 3);
});

test("配列とオブジェクトが混在する木でもパスが一意なまま平坦化される", () => {
  const parameters: JsonValue = {
    stages: [
      { name: "a", weight: 1 },
      { name: "b", weight: 2 },
    ],
  };
  const document = makeSweepDocument({ parameters, parameter_provenance: {} });
  const plan = planOf(document);

  const paths = plan.parameters.map((row) => row.path);
  assert.deepEqual(
    new Set(paths),
    new Set(["stages.0.name", "stages.0.weight", "stages.1.name", "stages.1.weight"]),
  );
  assert.equal(new Set(paths).size, paths.length);
  assert.equal(rowByPath(plan.parameters, "stages.1.name")?.value, "b");
});

test("末端の型ごとに文字列化の規則が異なる（null・真偽値・文字列・整数・小数）", () => {
  const parameters: JsonValue = {
    missing: null,
    enabled: true,
    disabled: false,
    label: "stop_and_wait",
    count: 4,
    ratio: 0.125,
  };
  const document = makeSweepDocument({ parameters, parameter_provenance: {} });
  const plan = planOf(document);

  assert.equal(rowByPath(plan.parameters, "missing")?.value, "null");
  assert.equal(rowByPath(plan.parameters, "enabled")?.value, "true");
  assert.equal(rowByPath(plan.parameters, "disabled")?.value, "false");
  assert.equal(rowByPath(plan.parameters, "label")?.value, "stop_and_wait");
  assert.equal(rowByPath(plan.parameters, "count")?.value, "4");
  assert.equal(rowByPath(plan.parameters, "ratio")?.value, "0.125");
});

test("小さいが非ゼロの数値パラメータが真のゼロと見分けの付かない文字列にならない（要件 4.1 / A-3）", () => {
  // 固定 3 桁への丸めを流用すると 0.0001 は "0.000" になり、真のゼロと見分けが付かなくなる。
  // パラメータの意味を知らない本モジュールに、軸・指標向けの精度規約を持ち込んではならない。
  const parameters: JsonValue = {
    tiny_tolerance: 0.0001,
    zero_tolerance: 0,
  };
  const document = makeSweepDocument({ parameters, parameter_provenance: {} });
  const plan = planOf(document);

  const tiny = rowByPath(plan.parameters, "tiny_tolerance")?.value;
  const zero = rowByPath(plan.parameters, "zero_tolerance")?.value;
  assert.ok(tiny !== undefined, "小さい非ゼロ値の行が無い");
  assert.ok(zero !== undefined, "ゼロ値の行が無い");
  assert.notEqual(tiny, "0.000", "小さい非ゼロ値が固定 3 桁丸めで真のゼロと同じ表記になっている");
  assert.notEqual(tiny, zero, "非ゼロの値が真のゼロと同じ文字列になっている");
  assert.match(tiny ?? "", /1/, "非ゼロの桁が文字列から失われている");
});

test("空の配列・空のオブジェクトは末端として 1 行になり、消えない", () => {
  const parameters: JsonValue = {
    empty_list: [],
    empty_object: {},
  };
  const document = makeSweepDocument({ parameters, parameter_provenance: {} });
  const plan = planOf(document);

  assert.equal(plan.parameters.length, 2);
  assert.equal(rowByPath(plan.parameters, "empty_list")?.value, "[]");
  assert.equal(rowByPath(plan.parameters, "empty_object")?.value, "{}");
});

test("大きく深いパラメータ木でも一意なパス数が行数と一致する（回帰検出用）", () => {
  // 5 段 × 各段 5 キーの木を組み立てる。リーフは末端の 1 キーだけ。
  function buildDeep(depth: number): JsonValue {
    if (depth === 0) {
      return 1;
    }
    const node: { [key: string]: JsonValue } = {};
    for (let i = 0; i < 5; i += 1) {
      node[`k${depth}_${i}`] = i === 0 ? buildDeep(depth - 1) : depth * 100 + i;
    }
    return node;
  }
  const parameters = buildDeep(5);
  const document = makeSweepDocument({ parameters, parameter_provenance: {} });
  const plan = planOf(document);

  const paths = plan.parameters.map((row) => row.path);
  assert.equal(new Set(paths).size, paths.length, "深い木で重複するパスが生じている");
  assert.ok(plan.parameters.length > 20, `リーフ数が少なすぎる: ${plan.parameters.length}`);
});

// --- 機体パラメータの抽出（要件 4.2） ----------------------------------------

test("drivetrainRows はパスが drivetrain. で始まる行のみを含み、parameters にも残る（要件 4.2）", () => {
  const plan = planOf(makeSweepDocument());

  assert.ok(plan.drivetrainRows.length > 0, "drivetrain の行が 1 つも無い");
  for (const row of plan.drivetrainRows) {
    assert.ok(row.path.startsWith("drivetrain."), `前置きが drivetrain. でない: ${row.path}`);
  }
  const drivetrainPathsInAll = plan.parameters
    .filter((row) => row.path.startsWith("drivetrain."))
    .map((row) => row.path);
  assert.deepEqual(
    new Set(plan.drivetrainRows.map((row) => row.path)),
    new Set(drivetrainPathsInAll),
    "drivetrainRows が parameters の drivetrain. 行と食い違う",
  );
  for (const row of plan.drivetrainRows) {
    const same = rowByPath(plan.parameters, row.path);
    assert.deepEqual(same, row, "drivetrainRows の行が parameters の行と一致しない");
  }
});

test("前置きが drivetrain. で始まらない類似パスは抽出されない（前置き一致のみが基準）", () => {
  const parameters: JsonValue = {
    drivetrain: { max_speed_mm_s: 1800 },
    drivetrain_note: "unused",
    not_drivetrain: { drivetrain: 1 },
  };
  const document = makeSweepDocument({ parameters, parameter_provenance: {} });
  const plan = planOf(document);

  const drivetrainPaths = plan.drivetrainRows.map((row) => row.path);
  assert.deepEqual(drivetrainPaths, ["drivetrain.max_speed_mm_s"]);
  assert.equal(
    plan.drivetrainRows.some((row) => row.path === "drivetrain_note"),
    false,
  );
  assert.equal(
    plan.drivetrainRows.some((row) => row.path === "not_drivetrain.drivetrain"),
    false,
  );
});

// --- 較正段階と注意書き（要件 3.1 / 3.2） ------------------------------------

test("較正段階の表示名が format.ts の翻訳と一致する（要件 3.1）", () => {
  const stages: readonly CalibrationStage[] = ["uncalibrated", "m1_calibrated", "m2_calibrated"];
  for (const stage of stages) {
    const document = makeSweepDocument({ calibration: makeCalibration({ stage, notice: null }) });
    const plan = planOf(document);
    assert.equal(plan.calibrationStageLabel, calibrationStageLabel(stage));
  }
});

test("notice キー自体が入力に無いとき calibrationNotice は null になる（要件 3.2）", () => {
  // makeCalibration({ notice: undefined }) は JSON へ直列化する際にキーごと消える。
  // 「キーが無い」ことを、値を補わずに再現する。
  const document = makeSweepDocument({
    calibration: makeCalibration({ stage: "m1_calibrated", notice: undefined }),
  });
  const { view } = loadOf(document);
  assert.equal(Object.hasOwn(view.document.calibration, "notice"), false, "テストの前提が崩れている");

  const plan = buildContextPlan(view, []);
  assert.equal(plan.calibrationNotice, null);
});

test("notice が null で存在するとき calibrationNotice も null になる（要件 3.2）", () => {
  const document = makeSweepDocument({
    calibration: makeCalibration({ stage: "m1_calibrated", notice: null }),
  });
  const plan = planOf(document);
  assert.equal(plan.calibrationNotice, null);
});

test("notice に実在の注意書きがあれば、そのまま変更せず返る（要件 3.2）", () => {
  const document = makeSweepDocument({
    calibration: makeCalibration({ stage: "uncalibrated", notice: UNCALIBRATED_NOTICE }),
  });
  const plan = planOf(document);
  assert.equal(plan.calibrationNotice, UNCALIBRATED_NOTICE);
});

// --- 同一性の行（要件 1.6 / 4.3） --------------------------------------------

test("同一性にファイル名・版・掃引種別・試行回数・乱数種・判定閾値が現れる（要件 1.6 / 4.3）", () => {
  const document = makeSweepDocument();
  const plan = planOf(document);

  const byLabel = (label: string): string | undefined =>
    plan.identity.find((row) => row.label === label)?.value;

  assert.equal(byLabel("ファイル名"), FILE_NAME);
  assert.equal(byLabel("output_schema_version"), document.output_schema_version);
  assert.equal(byLabel("sweep.kind"), document.sweep.kind);
  assert.equal(byLabel("trials_per_cell"), "20");
  assert.equal(byLabel("seed"), "12345");
  assert.equal(byLabel("catch_ratio_threshold"), "0.800");

  for (const axis of document.sweep.axes) {
    const row = plan.identity.find((entry) => entry.label === `軸: ${axis.name}`);
    assert.ok(row !== undefined, `軸の行が無い: ${axis.name}`);
    for (const value of axis.values) {
      assert.ok(
        row?.value.includes(String(value)),
        `軸 ${axis.name} の値 ${String(value)} が同一性の行に現れない`,
      );
    }
  }
});

test("軸が 3 本のとき、同一性にも 3 本分の軸の行が現れる", () => {
  const document = makeSweepDocument({ sweep: makeSweepSpec({ axes: THREE_AXES }) });
  const plan = planOf(document);

  const axisRows = plan.identity.filter((row) => row.label.startsWith("軸: "));
  assert.equal(axisRows.length, THREE_AXES.length);
});

test("判定閾値が null（試行 1 回）のとき、数値を作り出さず「入力に無い」と表す（要件 1.6 / 4.3）", () => {
  const document = makeSweepDocument({
    sweep: makeSweepSpec({ trials_per_cell: 1, catch_ratio_threshold: null }),
  });
  const plan = planOf(document);

  const threshold = plan.identity.find((row) => row.label === "catch_ratio_threshold");
  assert.ok(threshold !== undefined);
  assert.equal(threshold?.value, "入力に無い");
  assert.notEqual(threshold?.value, "0");
});

// --- 読み込み時の警告 ---------------------------------------------------------

test("読み込み時の警告（版の不一致）が文言として提示対象へ含まれる", () => {
  const document = makeSweepDocument({ output_schema_version: "0.9" });
  const { view, warnings } = loadOf(document);
  assert.equal(warnings.length, 1, "版の不一致が警告として現れていない");

  const plan = buildContextPlan(view, warnings);
  assert.equal(plan.warnings.length, 1);
  assert.equal(plan.warnings[0], warnings[0]?.detail);
  assert.match(plan.warnings[0] ?? "", /output_schema_version|版/);
});

test("警告が無い読み込みでは warnings が空になる", () => {
  const plan = planOf(makeSweepDocument());
  assert.deepEqual(plan.warnings, []);
});

// --- 断定語を含まない（要件 3.6 系の慣行） -----------------------------------

test("本モジュールが作る文言に断定語が現れない", () => {
  const documents: readonly SweepDocument[] = [
    makeSweepDocument(),
    makeSweepDocument({
      calibration: makeCalibration({ stage: "m2_calibrated", notice: undefined }),
    }),
    makeSweepDocument({ sweep: makeSweepSpec({ axes: THREE_AXES }) }),
  ];
  const plans = documents.map((document) => planOf(document));

  const asciiVerdicts = /\b(PASS|FAIL|OK|NG)\b/;
  const verdicts = ["合否", "合格", "不合格", "達成", "NFR-7"];
  for (const plan of plans) {
    for (const text of textsOf(plan)) {
      assert.doesNotMatch(text, asciiVerdicts, `断定語が現れている: ${text}`);
      for (const verdict of verdicts) {
        assert.equal(text.includes(verdict), false, `断定語「${verdict}」が現れている: ${text}`);
      }
    }
  }
});

// --- 決定性 -------------------------------------------------------------------

test("同じ入力から同じプランが得られる", () => {
  const document = makeSweepDocument();
  assert.deepEqual(planOf(document), planOf(document));
});

// --- レイヤ 2 の規律（Dependency Direction） ---------------------------------

test("コンパイル済みの context.js が schema.js / format.js 以外を import しない", () => {
  const url = new URL("../src/plan/context.js", import.meta.url);
  const source = readFileSync(url, "utf8");
  const specifiers = [...source.matchAll(/from\s+["']([^"']+)["']/g)].map((m) => m[1]);
  assert.ok(specifiers.length > 0, "import 文が 1 つも無い");
  for (const specifier of specifiers) {
    assert.match(
      specifier ?? "",
      /^\.\.\/(schema|format)\.js$/,
      `層 2 の許可範囲外の import がある: ${specifier}`,
    );
  }
  assert.doesNotMatch(source, /\bdocument\.(createElement|getElementById|querySelector)\b/);
  assert.doesNotMatch(source, /\bwindow\./);
});
