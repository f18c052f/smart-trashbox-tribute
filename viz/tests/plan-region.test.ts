// キャッチ可能領域のプランを検証する（要件 2.1〜2.9 / 3.5 / 3.6、設計 `#### RegionPlanner`）。
//
// ここで固定するのは次の 8 点である。
//   1. 格子点は**軸の値の並び順**で置かれる。値の大きさに比例した位置にしない（要件 2.1）
//   2. **判定を行わない。** 状態は上流の `status` をそのまま流す。成立割合から状態を作らない
//   3. 評価対象外は不成立と**異なる分類キー**を得て、理由が詳細文に出る（要件 2.5 / 2.6）
//   4. 軸が 3 本以上なら固定値で絞り込み、固定した軸と値がプランに現れる（要件 2.7）。
//      軸が 1 本なら全格子点が 1 行に並ぶ（要件 2.8）
//   5. 凡例に**上流の判定閾値**と**表示上の帯の境界**が区別して現れ、試行回数が併記される
//      （要件 2.4 / 3.5）。試行 1 回（閾値が `null`）でも凡例が破綻しない
//   6. 実際の色を決めない。返るのは分類キーだけであり、凡例のキーの組み合わせに収まる
//   7. 本モジュールが作る文字列に断定語が現れない（要件 3.6、境界検査 B-10）
//   8. 上流の値どうしの整合が崩れた入力（格子点の欠け・軸に無い値・長さの不一致）でも
//      例外を投げない。軸番号の範囲外だけは**プログラムの誤り**として例外になる
//
// 入力は `fixtures.ts` が組み立て、`loadSweep` を通してから渡す。
// 実際の経路と同じ形で作ることで、`load.ts` の `SweepView` がそのまま渡せることも同時に示す。
import { test } from "node:test";
import assert from "node:assert/strict";

import { loadSweep, type SweepView } from "../src/load.js";
import {
  buildRegionPlan,
  defaultSelection,
  type AxisSelection,
  type RegionPlan,
} from "../src/plan/region.js";
import type { AxisSpec, CellResult, SweepDocument } from "../src/schema.js";
import {
  ONE_AXIS,
  THREE_AXES,
  TWO_AXES,
  cyclingStatusAt,
  makeAxis,
  makeCell,
  makeGridDocument,
  makeMixedStatusDocument,
  makeSweepDocument,
  makeSweepSpec,
  toJsonText,
} from "./fixtures.js";

// --- 補助 -------------------------------------------------------------------

const FILE_NAME = "sweep-reachability.json";

/** 掃引結果を実際の読み込み経路へ通し、検証済みのビューを得る。 */
function viewOf(document: SweepDocument): SweepView {
  const result = loadSweep(toJsonText(document), FILE_NAME);
  if (!result.ok) {
    const detail = result.errors.map((issue) => `${issue.code} ${issue.path}`).join(" / ");
    throw new Error(`テストの前提となる入力が読み込めない: ${detail}`);
  }
  return result.view;
}

/** 既定の軸選択でプランを組み立てる。 */
function planOf(document: SweepDocument): RegionPlan {
  return buildRegionPlan(viewOf(document), defaultSelection(document.sweep));
}

/** 掃引軸の定義を作る。`cells` を自前で組み立てるときに使う。 */
function documentWithCells(
  axes: readonly AxisSpec[],
  cells: readonly CellResult[],
): SweepDocument {
  return makeSweepDocument({ sweep: makeSweepSpec({ axes }), cells });
}

/** 関数が投げた値を返す。投げなければテストをその場で落とす。 */
function errorFrom(run: () => unknown): unknown {
  try {
    run();
  } catch (cause) {
    return cause;
  }
  throw new Error("例外が投げられなかった");
}

/** プランが持つ文字列をすべて集める。文言の走査に使う。 */
function textsOf(plan: RegionPlan): readonly string[] {
  const texts: string[] = [plan.displayNote, plan.thresholdNote];
  texts.push(plan.xAxis.name, plan.xAxis.unit);
  if (plan.yAxis !== null) {
    texts.push(plan.yAxis.name, plan.yAxis.unit);
  }
  texts.push(...plan.columnLabels, ...plan.rowLabels);
  for (const entry of plan.legend) {
    texts.push(entry.fillKey, entry.label);
  }
  for (const note of plan.fixedAxes) {
    texts.push(note.axisName, note.valueLabel);
  }
  for (const cell of plan.cells) {
    texts.push(cell.fillKey, cell.tooltip);
  }
  return texts;
}

/** 凡例のうち、上流の判定閾値を述べている項目。 */
function thresholdEntriesOf(plan: RegionPlan): readonly { fillKey: string; label: string }[] {
  return plan.legend.filter((entry) => entry.label.includes("catch_ratio_threshold"));
}

/** 凡例のうち、表示上の帯を述べている項目のキー。 */
function bandKeysOf(plan: RegionPlan): readonly string[] {
  return plan.legend
    .filter((entry) => entry.fillKey.startsWith("band-"))
    .map((entry) => entry.fillKey);
}

/** 位置を 1 行の文字列へ落とす。重なりの検出に使う。 */
function positionsOf(plan: RegionPlan): readonly string[] {
  return plan.cells.map((cell) => `${cell.column},${cell.row}`);
}

// --- 格子の配置（要件 2.1 / 2.3） -------------------------------------------

test("2 軸の掃引で格子点数が軸の値の個数の積に一致する（要件 2.1）", () => {
  const plan = planOf(makeGridDocument({ axes: TWO_AXES }));

  assert.equal(plan.columnLabels.length, 3);
  assert.equal(plan.rowLabels.length, 4);
  assert.equal(plan.cells.length, plan.columnLabels.length * plan.rowLabels.length);

  // 格子点は 1 点ずつ別の位置を占め、軸の範囲から出ない。
  const positions = positionsOf(plan);
  assert.equal(new Set(positions).size, positions.length, "位置が重なっている格子点がある");
  for (const cell of plan.cells) {
    assert.ok(cell.column >= 0 && cell.column < plan.columnLabels.length, "列が軸の外にある");
    assert.ok(cell.row >= 0 && cell.row < plan.rowLabels.length, "行が軸の外にある");
  }
});

test("格子点の位置が軸の値の並び順で決まり、値の大きさに比例しない（要件 2.1）", () => {
  // 値の間隔が大きく異なる軸を使う。位置を値の線形写像で決めていれば、
  // 値 100 の列は 2 列目ではなくはるか遠くになる。
  const axes: readonly AxisSpec[] = [
    makeAxis({ name: "gap_mm", unit: "mm", values: [1, 2, 100] }),
    makeAxis({ name: "hold_time_ms", unit: "ms", values: [400, 600] }),
  ];
  const plan = planOf(makeGridDocument({ axes }));

  assert.equal(plan.cells.length, 6);
  const columnOf = (text: string): readonly number[] =>
    plan.cells.filter((cell) => cell.tooltip.includes(text)).map((cell) => cell.column);
  assert.deepEqual(columnOf("gap_mm: 1 mm"), [0, 0]);
  assert.deepEqual(columnOf("gap_mm: 2 mm"), [1, 1]);
  assert.deepEqual(columnOf("gap_mm: 100 mm"), [2, 2]);
});

test("軸の名前・単位・値が図のラベルとして現れる（要件 2.3）", () => {
  const plan = planOf(makeGridDocument({ axes: TWO_AXES }));

  assert.equal(plan.xAxis.name, "hold_time_ms");
  assert.equal(plan.xAxis.unit, "ms");
  assert.equal(plan.yAxis?.name, "required_distance_mm");
  assert.equal(plan.yAxis?.unit, "mm");
  assert.deepEqual(plan.columnLabels, ["400 ms", "600 ms", "800 ms"]);
  assert.deepEqual(plan.rowLabels, ["300 mm", "600 mm", "900 mm", "1200 mm"]);
});

// --- 判定を行わない（要件 2.2 / 3.7） ---------------------------------------

test("状態は上流の値をそのまま流し、成立割合から作り直さない（要件 2.2）", () => {
  // 閾値 0.8 に対し、状態と成立割合が食い違う格子点を並べる。
  // 表示側が判定を行っていれば、状態が入力と違う値になる。
  const cells: readonly CellResult[] = [
    makeCell({ axis_values: [400, 300], status: "not_catchable", success_ratio: 0.95 }),
    makeCell({ axis_values: [600, 300], status: "catchable", success_ratio: 0.05 }),
    makeCell({
      axis_values: [800, 300],
      status: "not_evaluated",
      success_ratio: null,
      metrics: {},
      not_evaluated_reason: "no_samples",
    }),
  ];
  const plan = planOf(documentWithCells(TWO_AXES, cells));

  assert.deepEqual(
    plan.cells.map((cell) => cell.status),
    ["not_catchable", "catchable", "not_evaluated"],
  );
  assert.match(plan.cells[0]?.tooltip ?? "", /0\.950/);
  assert.match(plan.cells[1]?.tooltip ?? "", /0\.050/);
});

test("評価対象外が不成立と異なる分類キーを得て、理由が詳細文に出る（要件 2.5 / 2.6）", () => {
  const plan = planOf(makeMixedStatusDocument());

  const notEvaluated = plan.cells.filter((cell) => cell.status === "not_evaluated");
  const notCatchable = plan.cells.filter((cell) => cell.status === "not_catchable");
  assert.ok(notEvaluated.length > 0, "評価対象外の格子点が入力に無い");
  assert.ok(notCatchable.length > 0, "不成立の格子点が入力に無い");

  const notCatchableKeys = new Set(notCatchable.map((cell) => cell.fillKey));
  for (const cell of notEvaluated) {
    assert.equal(
      notCatchableKeys.has(cell.fillKey),
      false,
      `評価対象外が不成立と同じ分類キーを得ている: ${cell.fillKey}`,
    );
    assert.match(cell.tooltip, /no_floor_crossing|no_samples|no_valid_prediction/);
  }
});

test("格子点の詳細文に軸の値・状態・成立割合・指標が出る（要件 2.6）", () => {
  const cells: readonly CellResult[] = [
    makeCell({ axis_values: [400, 300], status: "catchable", success_ratio: 0.85 }),
  ];
  const plan = planOf(documentWithCells(TWO_AXES, cells));
  const tooltip = plan.cells[0]?.tooltip ?? "";

  assert.match(tooltip, /hold_time_ms: 400 ms/);
  assert.match(tooltip, /required_distance_mm: 300 mm/);
  assert.match(tooltip, /catchable/);
  assert.match(tooltip, /成立割合: 0\.850/);
  assert.match(tooltip, /position_error_mm/);
  assert.match(tooltip, /12\.500/);
});

// --- 軸の選択と固定（要件 2.7 / 2.8） ---------------------------------------

test("3 軸の掃引で固定軸が絞り込みとして働く（要件 2.7）", () => {
  const document = makeGridDocument({ axes: THREE_AXES });
  const view = viewOf(document);
  const selection = defaultSelection(document.sweep);
  assert.equal(selection.fixed["catch_policy"], "stop_and_wait");

  const plan = buildRegionPlan(view, selection);
  // 12 点の格子から、固定軸の値が一致する 2 × 3 点だけが残る。
  assert.equal(document.cells.length, 12);
  assert.equal(plan.cells.length, 6);
  assert.deepEqual(plan.fixedAxes, [{ axisName: "catch_policy", valueLabel: "stop_and_wait" }]);
  for (const cell of plan.cells) {
    assert.match(cell.tooltip, /catch_policy: stop_and_wait/);
  }

  const other = buildRegionPlan(view, { ...selection, fixed: { catch_policy: "pass_through" } });
  assert.equal(other.cells.length, 6);
  assert.deepEqual(other.fixedAxes, [{ axisName: "catch_policy", valueLabel: "pass_through" }]);
  for (const cell of other.cells) {
    assert.match(cell.tooltip, /catch_policy: pass_through/);
  }
});

test("3 軸の掃引で描画に使う 2 軸を選び替えると、残りの軸が固定軸になる（要件 2.7）", () => {
  const document = makeGridDocument({ axes: THREE_AXES });
  const selection: AxisSelection = {
    xAxisIndex: 1,
    yAxisIndex: 2,
    fixed: { hold_time_ms: 600 },
  };
  const plan = buildRegionPlan(viewOf(document), selection);

  assert.equal(plan.xAxis.name, "required_distance_mm");
  assert.equal(plan.yAxis?.name, "catch_policy");
  assert.deepEqual(plan.columnLabels, ["300 mm", "600 mm", "900 mm"]);
  assert.deepEqual(plan.rowLabels, ["stop_and_wait", "pass_through"]);
  assert.equal(plan.cells.length, 6);
  assert.deepEqual(plan.fixedAxes, [{ axisName: "hold_time_ms", valueLabel: "600 ms" }]);
  for (const cell of plan.cells) {
    assert.match(cell.tooltip, /hold_time_ms: 600 ms/);
  }
});

test("1 軸の掃引が 1 行の並びになる（要件 2.8）", () => {
  const plan = planOf(makeGridDocument({ axes: ONE_AXIS }));

  assert.equal(plan.yAxis, null);
  assert.deepEqual(plan.rowLabels, []);
  assert.equal(plan.cells.length, 4);
  assert.deepEqual(plan.fixedAxes, []);
  for (const cell of plan.cells) {
    assert.equal(cell.row, 0, "1 軸の掃引で行が 0 でない格子点がある");
  }
  assert.deepEqual(plan.cells.map((cell) => cell.column), [0, 1, 2, 3]);
});

test("既定の軸選択が 1 軸・2 軸・3 軸のそれぞれで成り立つ（要件 2.7 / 2.8）", () => {
  const oneAxis = defaultSelection(makeSweepSpec({ axes: ONE_AXIS }));
  assert.equal(oneAxis.xAxisIndex, 0);
  assert.equal(oneAxis.yAxisIndex, null);
  assert.deepEqual(oneAxis.fixed, {});

  const twoAxes = defaultSelection(makeSweepSpec({ axes: TWO_AXES }));
  assert.equal(twoAxes.xAxisIndex, 0);
  assert.equal(twoAxes.yAxisIndex, 1);
  assert.deepEqual(twoAxes.fixed, {});

  const threeAxes = defaultSelection(makeSweepSpec({ axes: THREE_AXES }));
  assert.equal(threeAxes.xAxisIndex, 0);
  assert.equal(threeAxes.yAxisIndex, 1);
  assert.deepEqual(threeAxes.fixed, { catch_policy: "stop_and_wait" });
});

// --- 凡例（要件 2.4 / 2.9 / 3.5） -------------------------------------------

test("凡例に上流の判定閾値と表示上の帯の境界が区別して現れる（要件 2.4 / 3.5）", () => {
  const plan = planOf(makeMixedStatusDocument());

  const thresholdEntries = thresholdEntriesOf(plan);
  assert.equal(thresholdEntries.length, 1, "上流の判定閾値を述べる凡例が 1 件でない");
  const threshold = thresholdEntries[0];
  assert.match(threshold?.label ?? "", /0\.800/);
  assert.match(threshold?.label ?? "", /試行回数[^0-9]*20/);

  const bandKeys = bandKeysOf(plan);
  assert.ok(bandKeys.length >= 2, `表示上の帯の凡例が足りない: ${bandKeys.length} 件`);
  assert.equal(
    bandKeys.includes(threshold?.fillKey ?? ""),
    false,
    "閾値の凡例と帯の凡例が同じキーを持っている",
  );
  const bandLabels = plan.legend
    .filter((entry) => entry.fillKey.startsWith("band-"))
    .map((entry) => entry.label);
  for (const label of bandLabels) {
    assert.match(label, /表示上の帯/, "帯の凡例が表示上の取り決めであることを述べていない");
    assert.equal(
      label.includes("catch_ratio_threshold"),
      false,
      "帯の凡例が上流の閾値と混ざっている",
    );
  }
  // 帯の境界の値が、上流の閾値とは別の数として凡例に現れる。
  assert.ok(
    bandLabels.some((label) => /0\.750/.test(label)),
    `帯の境界が凡例に現れない: ${bandLabels.join(" / ")}`,
  );

  // 状態そのものの凡例も残る。
  const statusKeys = plan.legend.map((entry) => entry.fillKey);
  for (const key of ["catchable", "not-catchable", "not-evaluated"]) {
    assert.ok(statusKeys.includes(key), `状態の凡例が無い: ${key}`);
  }

  assert.match(plan.thresholdNote, /catch_ratio_threshold/);
  assert.match(plan.thresholdNote, /0\.800/);
  assert.match(plan.thresholdNote, /試行回数[^0-9]*20/);
});

test("試行 1 回で判定閾値が入力に無くても凡例が成り立つ（要件 2.4 / 3.5）", () => {
  const document = makeGridDocument({
    axes: TWO_AXES,
    overrides: {
      sweep: makeSweepSpec({ axes: TWO_AXES, trials_per_cell: 1, catch_ratio_threshold: null }),
    },
  });
  const plan = planOf(document);

  const threshold = thresholdEntriesOf(plan)[0];
  assert.ok(threshold !== undefined, "閾値を述べる凡例が無い");
  assert.match(threshold?.label ?? "", /入力に無い/);
  assert.match(threshold?.label ?? "", /試行回数[^0-9]*1/);
  assert.match(plan.thresholdNote, /入力に無い/);
  assert.ok(bandKeysOf(plan).length >= 2, "帯の凡例が消えている");
  assert.equal(plan.cells.length, 12, "閾値が無い掃引で格子が描けなくなっている");
});

test("色を決めず、格子点は凡例の分類キーの組み合わせだけを返す（要件 2.2）", () => {
  const plan = planOf(makeMixedStatusDocument());

  const bandKeys = bandKeysOf(plan);
  const thresholdKey = thresholdEntriesOf(plan)[0]?.fillKey ?? "";
  const statusKeys = plan.legend
    .map((entry) => entry.fillKey)
    .filter((key) => !key.startsWith("band-") && key !== thresholdKey);
  assert.ok(statusKeys.length > 0 && bandKeys.length > 0);

  const allowed = new Set<string>();
  for (const status of statusKeys) {
    allowed.add(status);
    for (const band of bandKeys) {
      allowed.add(`${status}-${band}`);
    }
  }
  for (const cell of plan.cells) {
    assert.ok(allowed.has(cell.fillKey), `凡例に無い分類キーを返している: ${cell.fillKey}`);
  }

  // 実際の色は CSS が持つ。プランは色を述べない。
  for (const text of textsOf(plan)) {
    assert.doesNotMatch(text, /#[0-9a-fA-F]{3}|rgba?\(|hsla?\(/, `色の指定が現れている: ${text}`);
  }
});

test("色分けが表示上の取り決めであり成立の条件でないことをプランが述べる（要件 2.9）", () => {
  const plan = planOf(makeMixedStatusDocument());

  assert.match(plan.displayNote, /表示上の取り決め/);
  assert.match(plan.displayNote, /条件ではない/);
  assert.match(plan.displayNote, /status/, "状態が上流の値であることを述べていない");
  assert.match(plan.displayNote, /catch_ratio_threshold/, "帯と上流の閾値の区別を述べていない");
});

test("本モジュールが作る文言に断定語が現れない（要件 3.6、境界検査 B-10）", () => {
  const plans: readonly RegionPlan[] = [
    planOf(makeMixedStatusDocument()),
    planOf(makeGridDocument({ axes: ONE_AXIS, statusAt: cyclingStatusAt })),
    planOf(makeGridDocument({ axes: THREE_AXES, statusAt: cyclingStatusAt })),
  ];
  // ASCII の語は単語の境界で照合する。上流の語 `no_floor_crossing` のような
  // 正しい値を誤って拾わないためである。
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

// --- プログラムの誤り（設計 Error Handling） --------------------------------

test("軸番号が範囲外・同一のときは例外になる（設計 Error Handling）", () => {
  const view = viewOf(makeGridDocument({ axes: TWO_AXES }));
  const cases: readonly AxisSelection[] = [
    { xAxisIndex: 2, yAxisIndex: null, fixed: {} },
    { xAxisIndex: -1, yAxisIndex: null, fixed: {} },
    { xAxisIndex: 0, yAxisIndex: 5, fixed: {} },
    { xAxisIndex: 0, yAxisIndex: 0, fixed: {} },
  ];
  for (const selection of cases) {
    const thrown = errorFrom(() => buildRegionPlan(view, selection));
    assert.ok(
      thrown instanceof RangeError,
      `軸番号 ${JSON.stringify(selection)} が例外にならない`,
    );
  }
});

// --- 上流の整合が崩れた入力（読み込みが検証しない範囲） ----------------------

test("格子点が 1 つも無い掃引でも例外を投げない", () => {
  const plan = planOf(documentWithCells(TWO_AXES, []));

  assert.deepEqual(plan.cells, []);
  assert.deepEqual(plan.columnLabels, ["400 ms", "600 ms", "800 ms"]);
  assert.ok(thresholdEntriesOf(plan).length === 1, "凡例が失われている");
});

test("軸が 1 本も無い掃引でも例外を投げない", () => {
  const plan = planOf(documentWithCells([], [makeCell({ axis_values: [] })]));

  assert.deepEqual(plan.cells, []);
  assert.deepEqual(plan.columnLabels, []);
  assert.deepEqual(plan.rowLabels, []);
  assert.equal(plan.yAxis, null);
  assert.deepEqual(plan.xAxis.values, []);
  assert.ok(thresholdEntriesOf(plan).length === 1, "凡例が失われている");
});

test("値を持たない軸でも例外を投げない", () => {
  const axes: readonly AxisSpec[] = [
    makeAxis({ name: "gap_mm", unit: "mm", values: [] }),
    makeAxis({ name: "hold_time_ms", unit: "ms", values: [400, 600] }),
  ];
  const plan = planOf(documentWithCells(axes, [makeCell({ axis_values: [300, 400] })]));

  assert.deepEqual(plan.columnLabels, []);
  assert.deepEqual(plan.cells, [], "軸に無い値を持つ格子点は置き場所が無い");
});

test("軸の本数と食い違う格子点は、置ける格子点だけが図に出る", () => {
  const cells: readonly CellResult[] = [
    makeCell({ axis_values: [400] }),                 // 2 本目の軸の値が無い
    makeCell({ axis_values: [400, 300, "余分"] }),     // 余分な軸の値を持つ
    makeCell({ axis_values: [9999, 300] }),           // 軸の値の一覧に無い値
  ];
  const plan = planOf(documentWithCells(TWO_AXES, cells));

  assert.equal(plan.cells.length, 1);
  assert.equal(plan.cells[0]?.column, 0);
  assert.equal(plan.cells[0]?.row, 0);
});

test("同じ位置の格子点が複数あっても落とさずに返す", () => {
  const cells: readonly CellResult[] = [
    makeCell({ axis_values: [400, 300], status: "catchable" }),
    makeCell({ axis_values: [400, 300], status: "not_catchable", success_ratio: 0 }),
  ];
  const plan = planOf(documentWithCells(TWO_AXES, cells));

  assert.equal(plan.cells.length, 2, "入力にある格子点を表示側で間引いてはならない");
  assert.deepEqual(positionsOf(plan), ["0,0", "0,0"]);
  assert.deepEqual(plan.cells.map((cell) => cell.status), ["catchable", "not_catchable"]);
});

test("同じ入力と同じ選択から同じプランが得られる（設計 Domain Model の不変条件）", () => {
  const document = makeMixedStatusDocument();

  assert.deepEqual(planOf(document), planOf(document));
});
