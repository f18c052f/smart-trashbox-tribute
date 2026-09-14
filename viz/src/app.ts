// 画面の組み立てと、ファイル選択・軸選択・記録選択・再生操作の結線
// （要件 1.1, 1.3, 1.7, 2.7, 4.4, 4.5, 5.5, 5.8, 6.1〜6.5, 8.4、設計 `#### App`）。
//
// このモジュールは**層 4** である。import してよいのは層 0〜3 のすべて（`schema` /
// `scale` / `format` / `load` / `plan/*` / `view/render`）で、DOM に触れてよい 3 モジュール
// のうちの 1 つである（Dependency Direction）。
//
// **判断を置かない。** 位置・文言・分類はすべて `plan/*` / `format.ts` が決めている。
// 本モジュールの唯一の責務は、それらの出力を DOM 要素へ結線することである
//（設計 App の Risks: 「画面の結線は単体テストの対象外である。だからこそロジックを
// 置かない」）。ここにある `if` はすべて「何を描くか」ではなく「いつ・どの host を
// 再描画するか」だけを決めている。
//
// `root: Document` 以外のグローバル（`document` / `window` の裸参照）を使わない。
// `requestAnimationFrame` / `FileReader` はいずれも `Document` ではなく `Window` に
// 属するため、`root.defaultView` から取り出す。これにより `startApp` の入力が
// 宣言どおり `root: Document` だけで閉じ、テストでは `defaultView` を差し替えるだけで
// RAF とファイル読み出しの双方を模せる。

import { loadSweep, type LoadIssue, type SweepView } from "./load.js";
import { formatAxisValue } from "./format.js";
import type { AxisSpec, AxisValue, ThrowRecordDoc } from "./schema.js";
import { buildRegionPlan, defaultSelection, type AxisSelection } from "./plan/region.js";
import { buildContextPlan } from "./plan/context.js";
import { buildAnimationPlan, frameAt, type AnimationPlan } from "./plan/animation.js";
import {
  renderRegion,
  renderContext,
  renderLoadFailure,
  createAnimationView,
  renderAnimationUnavailable,
  type AnimationView,
  type RenderableIssue,
} from "./view/render.js";

const ROOT_ELEMENT_ID = "app";

// --- 状態 ---------------------------------------------------------------------
//
// 保持する状態はこの 5 フィールド（「現在のビュー」「軸選択」「選択中の記録」
// 「再生時刻」「再生中か否か」）のみである（設計 App の State model、要件 6.5）。
// `localStorage` / `sessionStorage` / Cookie のいずれも使わない。
//
// これとは別に、`AnimationPlan` / `AnimationView` への参照や RAF のハンドルを
// `startApp` 内のローカル変数として持つ。これらは `state` から機械的に再計算できる
// 描画の作業用キャッシュであり、利用者が復元しうる「状態」ではないため、
// 上の 5 フィールドに含めない。

interface AppState {
  view: SweepView | null;
  axisSelection: AxisSelection | null;
  selectedRecordIndex: number | null;
  timeMs: number;
  playing: boolean;
}

function initialState(): AppState {
  return {
    view: null,
    axisSelection: null,
    selectedRecordIndex: null,
    timeMs: 0,
    playing: false,
  };
}

// --- DOM の骨格 -----------------------------------------------------------------
//
// タブ・画面遷移を作らず、1 ページに全パネルを並べる（要件 6.2）。パネルはすべて
// `#app` の内側に動的に生成する。`index.html` は変更しない（既定の空の `#app` で足りる）。

interface Dom {
  readonly fileInput: HTMLInputElement;
  readonly contextHost: Element;
  readonly axisControlsHost: Element;
  readonly regionHost: Element;
  readonly recordControlsHost: Element;
  readonly playButton: HTMLButtonElement;
  readonly stopButton: HTMLButtonElement;
  readonly resetButton: HTMLButtonElement;
  readonly seekInput: HTMLInputElement;
  readonly xyHost: Element;
  readonly xzHost: Element;
}

function buildDom(root: Document, mount: Element): Dom {
  const fileInput = root.createElement("input");
  fileInput.type = "file";
  fileInput.accept = "application/json";
  fileInput.classList.add("app-file-input");

  // 較正段階・除外要因・同一性・パラメータを 1 つの host にまとめて描画する
  // （`renderContext` の契約どおり）。この host は軸選択・記録選択・シークのいずれの
  // ハンドラからも触れない（要件 6.3）。
  const contextHost = root.createElement("div");
  contextHost.classList.add("app-context-host");

  const axisControlsHost = root.createElement("div");
  axisControlsHost.classList.add("app-axis-controls-host");

  const regionHost = root.createElement("div");
  regionHost.classList.add("app-region-host");

  const recordControlsHost = root.createElement("div");
  recordControlsHost.classList.add("app-record-controls-host");

  const playButton = root.createElement("button");
  playButton.type = "button";
  playButton.textContent = "再生";
  playButton.classList.add("app-play-button");

  const stopButton = root.createElement("button");
  stopButton.type = "button";
  stopButton.textContent = "停止";
  stopButton.classList.add("app-stop-button");

  const resetButton = root.createElement("button");
  resetButton.type = "button";
  resetButton.textContent = "先頭へ";
  resetButton.classList.add("app-reset-button");

  const seekInput = root.createElement("input");
  seekInput.type = "range";
  seekInput.classList.add("app-seek-input");

  const playbackControls = root.createElement("div");
  playbackControls.classList.add("app-playback-controls");
  playbackControls.append(playButton, stopButton, resetButton, seekInput);

  const xyHost = root.createElement("div");
  xyHost.classList.add("app-track-host", "app-track-host--xy");
  const xzHost = root.createElement("div");
  xzHost.classList.add("app-track-host", "app-track-host--xz");

  mount.replaceChildren(
    fileInput,
    contextHost,
    axisControlsHost,
    regionHost,
    recordControlsHost,
    playbackControls,
    xyHost,
    xzHost,
  );

  return {
    fileInput,
    contextHost,
    axisControlsHost,
    regionHost,
    recordControlsHost,
    playButton,
    stopButton,
    resetButton,
    seekInput,
    xyHost,
    xzHost,
  };
}

// --- 軸選択の操作（要件 2.7） -----------------------------------------------------

/**
 * 軸選択の操作を組み立てる。軸が 2 本未満では選ぶ余地が無い（要件 2.8）ため空を返す。
 * X 軸・Y 軸の選択肢は `sweep.axes` の名前・単位をそのまま options のラベルにする
 * （分類や言い換えを行わない）。固定軸の値は `format.ts` の `formatAxisValue` を通す。
 */
function buildAxisControls(
  root: Document,
  axes: readonly AxisSpec[],
  initialSelection: AxisSelection,
  onSelectionChange: (next: AxisSelection) => void,
): Element {
  const container = root.createElement("div");
  container.classList.add("app-axis-controls");
  if (axes.length < 2) {
    return container;
  }

  const axisOption = (axis: AxisSpec, index: number): HTMLOptionElement => {
    const option = root.createElement("option");
    option.value = String(index);
    option.textContent = `${axis.name} [${axis.unit}]`;
    return option;
  };

  const xSelect = root.createElement("select");
  xSelect.classList.add("app-axis-x-select");
  const ySelect = root.createElement("select");
  ySelect.classList.add("app-axis-y-select");
  axes.forEach((axis, index) => {
    xSelect.appendChild(axisOption(axis, index));
    ySelect.appendChild(axisOption(axis, index));
  });
  xSelect.value = String(initialSelection.xAxisIndex);
  ySelect.value = String(initialSelection.yAxisIndex ?? 0);

  const fixedContainer = root.createElement("div");
  fixedContainer.classList.add("app-axis-fixed-controls");

  let fixedSelects = new Map<number, HTMLSelectElement>();

  function readSelection(): AxisSelection {
    const xAxisIndex = Number(xSelect.value);
    const yAxisIndex = Number(ySelect.value);
    const fixed: { [axisName: string]: AxisValue } = {};
    for (const [axisIndex, select] of fixedSelects) {
      const axis = axes[axisIndex];
      if (axis === undefined) {
        continue;
      }
      const value = axis.values[Number(select.value)];
      if (value !== undefined) {
        fixed[axis.name] = value;
      }
    }
    return { xAxisIndex, yAxisIndex, fixed };
  }

  function handleChange(): void {
    onSelectionChange(readSelection());
  }

  function rebuildFixedSelects(carryOverFixed: AxisSelection["fixed"]): void {
    const xIndex = Number(xSelect.value);
    const yIndex = Number(ySelect.value);
    fixedSelects = new Map();
    const labels: Element[] = [];
    axes.forEach((axis, index) => {
      if (index === xIndex || index === yIndex) {
        return;
      }
      const select = root.createElement("select");
      select.classList.add("app-axis-fixed-select");
      axis.values.forEach((value, valueIndex) => {
        const option = root.createElement("option");
        option.value = String(valueIndex);
        option.textContent = formatAxisValue(value, axis.unit);
        select.appendChild(option);
      });
      const carriedValue = Object.hasOwn(carryOverFixed, axis.name)
        ? carryOverFixed[axis.name]
        : axis.values[0];
      const carriedIndex = carriedValue === undefined
        ? 0
        : Math.max(axis.values.indexOf(carriedValue), 0);
      select.value = String(carriedIndex);
      select.addEventListener("change", handleChange);
      fixedSelects.set(index, select);

      const label = root.createElement("label");
      label.classList.add("app-axis-fixed-label");
      label.textContent = `${axis.name} [${axis.unit}]`;
      label.appendChild(select);
      labels.push(label);
    });
    fixedContainer.replaceChildren(...labels);
  }

  function handleAxisPick(): void {
    // X/Y の選択が変わると「固定軸として現れる軸の集合」自体が変わる。
    // 直前の固定値を引き継げるものは引き継ぎ、新たに固定される軸は先頭の値を既定にする
    // （`plan/region.ts` の `defaultSelection` と同じ既定の付け方）。
    rebuildFixedSelects(readSelection().fixed);
    handleChange();
  }

  xSelect.addEventListener("change", handleAxisPick);
  ySelect.addEventListener("change", handleAxisPick);
  rebuildFixedSelects(initialSelection.fixed);

  const xLabel = root.createElement("label");
  xLabel.classList.add("app-axis-label", "app-axis-x-label");
  xLabel.textContent = "X軸";
  xLabel.appendChild(xSelect);

  const yLabel = root.createElement("label");
  yLabel.classList.add("app-axis-label", "app-axis-y-label");
  yLabel.textContent = "Y軸";
  yLabel.appendChild(ySelect);

  container.replaceChildren(xLabel, yLabel, fixedContainer);
  return container;
}

// --- 記録選択の操作（要件 5.8） ---------------------------------------------------

function buildRecordControls(
  root: Document,
  records: readonly ThrowRecordDoc[],
  selectedIndex: number,
  onChange: (index: number) => void,
): Element {
  const container = root.createElement("div");
  container.classList.add("app-record-controls");
  if (records.length === 0) {
    return container;
  }

  const select = root.createElement("select");
  select.classList.add("app-record-select");
  records.forEach((record, index) => {
    const option = root.createElement("option");
    option.value = String(index);
    option.textContent = record.record_id;
    select.appendChild(option);
  });
  select.value = String(selectedIndex);
  select.addEventListener("change", () => {
    onChange(Number(select.value));
  });

  const label = root.createElement("label");
  label.classList.add("app-record-label");
  label.textContent = "記録";
  label.appendChild(select);
  container.appendChild(label);
  return container;
}

// --- 入口 -------------------------------------------------------------------------

/**
 * 画面を組み立て、ファイル選択・軸選択・記録選択・再生操作を結線する。
 *
 * 前提条件は `root` の中に `#app` 要素が存在すること。無ければプログラムの誤りとして
 * 例外を投げる（設計 Error Strategy）。
 */
export function startApp(root: Document): void {
  const mount = root.getElementById(ROOT_ELEMENT_ID);
  if (mount === null) {
    throw new Error(`表示先の要素 #${ROOT_ELEMENT_ID} が見つからない`);
  }

  // `requestAnimationFrame` は Window のものであり、`root: Document` だけを入力とする
  // 本関数では `root.defaultView` から取り出す（設計 Concurrency strategy。裸の
  // グローバル `window` / `requestAnimationFrame` を参照しない）。
  //
  // 素の `root.defaultView`（`Window | null`）に対する null チェックの絞り込みは、
  // TypeScript の制御フロー解析が関数境界（下で定義するクロージャ群）を越えて
  // 引き継がない。そのため一度 `Window` 型（非 null）の変数へ代入し直し、
  // 以降はその変数だけを参照する。
  const maybeWindow = root.defaultView;
  if (maybeWindow === null) {
    throw new Error("root.defaultView が無い（root は window に属する document である必要がある）");
  }
  const browserWindow: Window = maybeWindow;

  // `FileReader` は `requestAnimationFrame` と同じ理由で `root.defaultView` から
  // 取り出したいところだが、TypeScript の DOM 型定義（`lib.dom.d.ts`）は `FileReader`
  // を `Window` インタフェースのメンバーとして宣言していない（グローバルの
  // `declare var FileReader` としてのみ存在する）。`Window` 型を偽装するキャストを
  // 増やすより、DOM 標準のグローバルコンストラクタとして素直に参照する方を選ぶ。
  const FileReaderCtor = FileReader;

  const dom = buildDom(root, mount);

  const state: AppState = initialState();
  // 描画のための作業用キャッシュ。`state` の一部ではない（上の State セクション参照）。
  let animationPlans: { xy: AnimationPlan; xz: AnimationPlan } | null = null;
  let animationViews: { xy: AnimationView; xz: AnimationView } | null = null;
  let rafHandle: number | null = null;
  let lastTickTime: number | null = null;

  // --- 再生ループ（設計 Concurrency strategy: requestAnimationFrame 1 本のみ） ---

  function renderFrame(timeMs: number): void {
    if (animationPlans === null || animationViews === null) {
      return;
    }
    animationViews.xy.showFrame(frameAt(animationPlans.xy, timeMs));
    animationViews.xz.showFrame(frameAt(animationPlans.xz, timeMs));
  }

  function stop(): void {
    state.playing = false;
    if (rafHandle !== null) {
      browserWindow.cancelAnimationFrame(rafHandle);
      rafHandle = null;
    }
    lastTickTime = null;
  }

  function tick(now: number): void {
    if (!state.playing || animationPlans === null) {
      return;
    }
    const last = lastTickTime ?? now;
    lastTickTime = now;
    const deltaMs = now - last;
    const end = animationPlans.xy.endTimeMs;
    const next = Math.min(state.timeMs + deltaMs, end);
    state.timeMs = next;
    dom.seekInput.value = String(next);
    renderFrame(next);
    if (next >= end) {
      // 再生の終端に到達した（設計 再生のフロー: Playing -> Stopped: reached end）。
      stop();
      return;
    }
    rafHandle = browserWindow.requestAnimationFrame(tick);
  }

  function play(): void {
    if (state.playing || animationPlans === null) {
      return;
    }
    // 終端で再生を押しても自動的に先頭へは戻らない（設計に明記が無いため、
    // より保守的な「先頭復帰を明示的な操作として要求する」挙動を選ぶ）。
    if (state.timeMs >= animationPlans.xy.endTimeMs) {
      return;
    }
    state.playing = true;
    lastTickTime = null;
    rafHandle = browserWindow.requestAnimationFrame(tick);
  }

  function seekTo(rawTimeMs: number): void {
    if (animationPlans === null) {
      return;
    }
    const clamped = Math.min(
      Math.max(rawTimeMs, animationPlans.xy.startTimeMs),
      animationPlans.xy.endTimeMs,
    );
    state.timeMs = clamped;
    dom.seekInput.value = String(clamped);
    renderFrame(clamped);
  }

  // --- 記録選択（要件 5.8） ---------------------------------------------------------
  // 再生対象の切り替えは、アニメーション面（xy / xz の 2 host）だけを再描画する。
  // 図・前提と限界の host には触れない（要件 6.3）。

  function selectRecord(index: number): void {
    if (state.view === null) {
      return;
    }
    const record = state.view.records[index];
    if (record === undefined) {
      return;
    }
    stop();
    state.selectedRecordIndex = index;

    const xyPlan = buildAnimationPlan(record, "xy");
    const xzPlan = buildAnimationPlan(record, "xz");
    animationPlans = { xy: xyPlan, xz: xzPlan };
    animationViews = {
      xy: createAnimationView(dom.xyHost, xyPlan),
      xz: createAnimationView(dom.xzHost, xzPlan),
    };

    dom.seekInput.disabled = false;
    dom.seekInput.min = String(xyPlan.startTimeMs);
    dom.seekInput.max = String(xyPlan.endTimeMs);

    state.timeMs = xyPlan.startTimeMs;
    dom.seekInput.value = String(state.timeMs);
    renderFrame(state.timeMs);
  }

  // --- 軸選択（要件 2.7） ------------------------------------------------------------
  // 軸選択の変更は、図の host だけを再描画する。前提と限界・アニメーション面には触れない
  //（要件 6.3）。

  function handleAxisSelectionChange(next: AxisSelection): void {
    if (state.view === null) {
      return;
    }
    state.axisSelection = next;
    renderRegion(dom.regionHost, buildRegionPlan(state.view, next));
  }

  // --- 読み込み（要件 1.1〜1.3, 1.7） -------------------------------------------------

  function showLoadFailure(issues: readonly RenderableIssue[]): void {
    // 読み込み中（および読み込み失敗の確定時）は再生を止める（設計 Concurrency strategy）。
    stop();
    // 直前の図を消してから失敗内容を表示する（要件 1.3）。前提と限界・アニメーション面は
    // このタスクの範囲では「直前の図」に含めない（renderLoadFailure の契約が図の host のみを
    // 対象とするため）。
    renderLoadFailure(dom.regionHost, issues);
  }

  function applySuccessfulLoad(view: SweepView, warnings: readonly LoadIssue[]): void {
    stop();
    state.view = view;
    const selection = defaultSelection(view.document.sweep);
    state.axisSelection = selection;

    // 前提・限界・同一性は読み込み成功の度に 1 回だけ再構築する。以降の軸選択・記録選択・
    // シークではこの host に触れない（要件 6.3 の描画側の実装。`view`/`warnings` はどちらも
    // `ContextSource` / `ContextWarning` に構造的に代入できるため、そのまま渡す）。
    renderContext(dom.contextHost, buildContextPlan(view, warnings));

    renderRegion(dom.regionHost, buildRegionPlan(view, selection));

    dom.axisControlsHost.replaceChildren(
      buildAxisControls(root, view.document.sweep.axes, selection, handleAxisSelectionChange),
    );

    dom.recordControlsHost.replaceChildren(
      buildRecordControls(root, view.records, 0, selectRecord),
    );

    const hasRecords = view.records.length > 0;
    dom.playButton.disabled = !hasRecords;
    dom.stopButton.disabled = !hasRecords;
    dom.resetButton.disabled = !hasRecords;

    if (hasRecords) {
      selectRecord(0);
      return;
    }

    // 代表 Throw Record が無くても図の描画は継続する（要件 1.7）。縮退はアニメーション面のみ。
    state.selectedRecordIndex = null;
    animationPlans = null;
    animationViews = null;
    state.timeMs = 0;
    dom.seekInput.disabled = true;
    // `recordsIssue` は records が空のとき Loader が必ず添える（`load.ts` の契約）。
    // ここでの `??` は型システム上の null 安全のためのみで、実際には到達しない分岐である。
    const reason = view.recordsIssue?.detail ?? "軌跡アニメーションが利用できない";
    renderAnimationUnavailable(dom.xyHost, reason);
    renderAnimationUnavailable(dom.xzHost, reason);
  }

  function handleFileInputChange(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (file === undefined || file === null) {
      return;
    }
    // ファイル読み込み中は再生を停止する（設計 Concurrency strategy）。
    stop();

    const reader = new FileReaderCtor();
    reader.onload = () => {
      const text = typeof reader.result === "string" ? reader.result : "";
      const result = loadSweep(text, file.name);
      if (result.ok) {
        applySuccessfulLoad(result.view, result.warnings);
      } else {
        showLoadFailure(result.errors);
      }
    };
    reader.onerror = () => {
      // JSON の内容とは別の失敗（ファイル自体を読み出せない）。`LoadIssueCode` の
      // いずれにも正確に当てはまらないため、`LoadIssue` を偽装せず、
      // `RenderableIssue` の最小形（path / detail）だけを直接組み立てる。
      showLoadFailure([{ path: "", detail: `ファイル ${file.name} を読み込めない` }]);
    };
    reader.readAsText(file);
  }

  dom.fileInput.addEventListener("change", handleFileInputChange);
  dom.playButton.addEventListener("click", play);
  dom.stopButton.addEventListener("click", stop);
  dom.resetButton.addEventListener("click", () => {
    if (animationPlans !== null) {
      seekTo(animationPlans.xy.startTimeMs);
    }
  });
  dom.seekInput.addEventListener("input", () => {
    seekTo(Number(dom.seekInput.value));
  });

  dom.playButton.disabled = true;
  dom.stopButton.disabled = true;
  dom.resetButton.disabled = true;
  dom.seekInput.disabled = true;
}
