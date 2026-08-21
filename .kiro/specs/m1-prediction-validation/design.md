# Technical Design Document: m1-prediction-validation

## Overview

**Purpose**: 本機能は、既に仕様化・実装された部品（`sensing-foundation` / `flying-object-tracking` /
`world-frame-calibration` / `prediction-core`）を**実データで一本に繋ぎ、実際に投げて測る**。
本設計の重心は「繋ぐこと」そのものではなく、**繋いだ結果として誤差が1つの数値に潰れないようにすること**にある。
`docs/requirements.md §6.2` が警告するとおり、座標系が数 cm ずれても症状は「予測が悪い」にしか見えない。
Spec をキャリブレーション・検出・予測に分けたのは、その3つを分離可能にするためであり、
**統合 Spec がその分離可能性を壊すなら、分けた意味が消える。**

**Users**: M1 の実施者（実機で投げ、記録し、集計する）。プロジェクトの意思決定者（時間予算表の更新値と
OQ-27 の結論を受け取る）。下流としては M2 / M3（移動体に残された時間の確定値）と
`trajectory-simulator`（検出遅れ・予測誤差の較正入力）。

**Impact**: 本 Spec は **`CameraTrack` → `prediction_core.Sample` の継ぎ目**をリポジトリに初めて作る。
`flying-object-tracking` が `prediction_core` を import しない設計である以上、この継ぎ目はどの上流にも存在しない。
あわせて **OQ-27 を決着させ**、**`docs/requirements.md §3` の時間予算表を実測値で更新する**。

### Goals

- カメラ座標系の点列を World 座標系の予測入力へ変換する経路を、**単一のモジュールに閉じて**提供する（要件 1）
- 検証を通していないキャリブレーションでの実測を、**構造として拒否する**（要件 2）
- 1投擲を取得から逐次予測まで通して実行し、`prediction-core` のスキーマで記録する（要件 3）
- 実際の落下地点・落下時刻・リリース時刻の**求め方を定義し、不確かさとともに残す**（要件 4）
- M1 実測7項目を、ばらつきと試行数を伴って算出する（要件 5）
- 誤差を**キャリブレーション／検出／予測へ帰属**させ、判別不能を正常な結果として扱う（要件 6）
- 段階別レイテンシを集計し、**計測が計測対象を歪めていない**ことを確認する（要件 7）
- 落下地点を図として出し、**その依存を実機側の実行経路から締め出す**（要件 8）
- **OQ-27 を、実測前に固定した3値の規則で判断する**（要件 9）
- OQ-05 の判断材料を提示し、**決着させない**（要件 10）
- 実測値が揃ったことをゲートに、`docs/requirements.md §3` を更新する（要件 11）
- 実機と上流実装が揃う前に、継ぎ目から判断・可視化までを作り切れるようにする（要件 12）

### Non-Goals

- フレーム取得・記録・再生・構造化ロギング**基盤**（`sensing-foundation`）
- 検出方式の決定・追跡・カメラ座標系の3D位置算出（`flying-object-tracking` / OQ-26）
- 床平面推定・World frame の確立・変換の**実装**（`world-frame-calibration` / OQ-03）
- 予測アルゴリズム・Throw Record スキーマ（`prediction-core` / D-8。実装済み・変更しない）
- **予測アルゴリズムの改良。** 本 Spec は測定であって改善ではない
- 移動体に関するすべて（M2 / M3 / M4）、ESP32 への送信、テレオペ
- 投擲アーカイブ／ベンチマーク（OQ-39。本 Spec でデータが貯まってから再判断）
- **ハードウェアの置き換えの実行**（判断材料と結論の提示までを持つ）
- ブラウザ可視化（`simulator-visualization` / OQ-34）
- リポジトリ全体のディレクトリ構成（OQ-40）と Python 環境構築方針（OQ-41）の確定

> 詳細と担当先は下の [Out of Boundary](#out-of-boundary) を正とする。

---

## Boundary Commitments

### This Spec Owns

- **継ぎ目**: `CameraTrack` ＋ `WorldTransform` → `prediction_core.Sample` の変換と、
  変換に伴う除外規則・除外理由の計数（要件 1）
- **キャリブレーション検証ゲート**: 未検証結果での実測を拒否する規則と、許可時の印の付け方（要件 2）
- **1投擲の実行順序**: 取得 → 検出・追跡 → 変換 → 逐次予測 → 記録という組み立て（要件 3）
- **真値の定義**: 実際の落下地点・落下時刻・リリース時刻の求め方と、不確かさの併記規則（要件 4）
- **M1 実測7項目の算出定義**と、投擲群としての集計（要件 5）
- **誤差の帰属**: 分解の仕方、向きによる判別、判定規則（要件 6。**本 Spec の核**）
- **end-to-end レイテンシの定義**と、投擲単位への束ね直し、計測 ON/OFF 比較（要件 7）
- **可視化の内容と、可視化依存の隔離規則**（要件 8）
- **OQ-27 の判定規則と結論**（要件 9。**OQ-27 を決着させる**）
- **OQ-05 の判断材料の算出定義**（要件 10。**決着させない**）
- **`docs/requirements.md §3`（時間予算表と、そこから導出される予測レイテンシの暫定目標）の更新**（要件 11）
- **物理配置**: `src/m1_validation/**`、`tests/m1_validation/**`、
  `.kiro/specs/m1-prediction-validation/measurements.md`、同 `procedure.md`、
  ルート `pyproject.toml` への**追記**、および `docs/requirements.md §3` の更新

### Out of Boundary

- フレーム取得・ドレイン・セッション記録・再生・構造化ロギング基盤（`sensing-foundation`）
- 検出方式・マスク生成・候補絞り込み・カメラ座標系の逆投影・フレーム間追跡（`flying-object-tracking`）
- 床平面推定・World frame の確立・変換の実装・検証レポートの**算出**（`world-frame-calibration`）。
  本 Spec は検証レポートを**読むだけ**であり、合否の定義は上流が正
- フィッティング・落下地点算出・Throw Record スキーマ（`prediction-core`）
- 予測アルゴリズムの改良、カルマンフィルタ等への差し替え（測って不足が分かってから別 Spec）
- 投擲物理・合成データの物理モデル（`trajectory-simulator` / OQ-33）
- 移動体側のすべて、ESP32 への送信、目標データのプロトコル（OQ-30）
- `docs/open-questions.md` からの OQ-27 行の削除と `docs/decisions.md` への移設。
  上流 Spec と同じく**実装完了時の文書整備として別途行う**
- `docs/requirements.md` の §3 以外の節、および `docs/` の他ファイル
- `src/prediction_core/**` / `src/sensing_foundation/**` / `src/flying_object_tracking/**` /
  `src/world_frame_calibration/**` への一切の変更
- 他 Spec の `.kiro/specs/<name>/**` および `.kiro/steering/roadmap.md`

### Allowed Dependencies

- **上流4パッケージの公開入口のみ。** 内部モジュールへ直接 import しない
  - `prediction_core.__init__` の18シンボル（実行時依存ゼロ。**どの層からでも参照してよい共通語彙**）
  - `sensing_foundation.__init__` — **接点は `upstream.py` 1モジュールに限る**
  - `flying_object_tracking.__init__` / `world_frame_calibration.__init__` —
    **接点は `seam.py` 1モジュールに限る**（`runner.py` は `seam.py` 経由で使う）
- **本 Spec 自身が宣言するサードパーティ依存は `matplotlib` 1つだけ**（extras `m1-viz`）。
  それを import するのは `plot.py` 1モジュールに限る。
  **`[project].dependencies` は空のまま維持する。**
  `tests/prediction_core/test_packaging.py::test_no_third_party_runtime_dependencies` は
  `[project].dependencies == []` に加えて **extras 名が許可リストの範囲内にあること**を検証する形へ
  改訂される（許可リスト `ALLOWED_OPTIONAL_EXTRAS = {"sensing", "tracking", "calibration", "m1-viz"}`）。
  ⚠️ **この改訂は `sensing-foundation`（Wave 0）が所有し、本 Spec は同テストを再改訂しない。**
  本 Spec が必要とするのは、許可リストに **`m1-viz` が含まれていること**だけである（下記 Prerequisite 参照）
- 標準ライブラリ（`json` / `math` / `statistics` / `dataclasses` / `enum` / `pathlib` / `argparse` /
  `random` / `typing` / `collections`）
- **禁止**:
  - `prediction_core` へのサードパーティ依存の逆流、および同パッケージへの一切の変更
  - `cv2` / `pyrealsense2` / `numpy` の直接 import（前2者は上流の道具、numpy は上流 extras の中で完結させる）
  - **評価側（`truth` / `metrics` / `attribution` / `judgement` / `report` / `plot`）からの
    `sensing_foundation` / `flying_object_tracking` / `world_frame_calibration` の import**。
    評価側は**記録された値だけ**を読む（要件 12.5 を構造で保証する）
  - **実機側の実行経路（`upstream` / `seam` / `runner`）からの `plot` の import**（要件 8.8）
  - 下流 Spec（`simulator-visualization`）および `trajectory_simulator` への依存

### Prerequisites（他 Spec が先に landing している必要があるもの）

- **`tests/prediction_core/test_packaging.py` の extras 許可リスト化**（`sensing-foundation` が所有）。
  現行の同テストは `[project].optional-dependencies == {}` を主張しており、
  **本 Spec の `pyproject.toml` 追記（`m1-viz`）はこの改訂が landing した後にのみ有効**である。
  改訂前に追記すると、マージ済みの `prediction-core` テスト群が赤くなる。
  タスク1.1 の着手前に、許可リストに `m1-viz` が含まれていることを確認する
- ★ **ピンホール逆投影の基本演算の一元化**（`sensing-foundation` が所有）。
  `sensing_foundation.geometry` が `depth_raw_to_mm` / `is_valid_depth` / `deproject_pixel` を公開し、
  `flying-object-tracking` と `world-frame-calibration` が**それぞれの独自実装を捨てて同じ演算に乗る**
  （両 Spec は自分の集約方針だけを持つ）。**本 Spec はこの一元化を前提として要件 1.10 の
  クロス Spec 契約テストを書くだけであり、基本演算そのものも上流2 Spec の呼び出し側も所有しない**

### Revalidation Triggers

以下が発生した場合、本 Spec の結合と集計は再確認が必要になる。

- **上流由来**:
  - `flying_object_tracking` の `HANDOFF_VERSION` 変更、`CameraTrack` / `TrackPoint` / `CameraPoint` の
    追加以外の変更、`CoordinateFrame` の意味変更、`detect` / `track` の stage 名変更
  - `world_frame_calibration` の **World frame 定義の変更**、`CALIBRATION_FORMAT_VERSION` 変更、
    `WorldTransform` の意味論変更、検証レポートの誤差定義変更、`calibrate` stage のキー変更
  - `sensing_foundation` の `RECORDING_FORMAT_VERSION` / `LOG_FORMAT_VERSION` 変更、
    予約 stage の意味変更、Throw Record 保存レイアウトの変更、時間基準の変更
  - ★ **`sensing_foundation.geometry` のピンホール逆投影の基本演算
    （`depth_raw_to_mm` / `is_valid_depth` / `deproject_pixel`）の変更**
    （画素中心規約・`depth_scale_mm` の適用位置・無効画素判定を含む）。
    `flying_object_tracking`（3次元点の復元）と `world_frame_calibration`（床平面推定）の
    **両方が同じ演算に乗っている**ため、この変更は上流2経路の結果を同時に動かす。
    **本 Spec の帰属（`attribution.py`）は2経路が完全に一致していることを前提に成立している**ので、
    変更があれば要件 1.10 のクロス Spec 契約テストを必ず再確認する
  - `prediction_core` の `SCHEMA_VERSION` 変更、公開18シンボルの変更
- **本 Spec 由来**（下流 = M2 / M3 / `trajectory-simulator` が再確認する）:
  - `M1_EXTRA_VERSION`（Throw Record の `extra["m1"]` の形）の変更
  - **真値の求め方の変更**（外挿・内挿の定義）。実測値の意味が変わる
  - **実測7項目の算出定義の変更**
  - **判定規則の変更**（帰属・収束・OQ-27・計測 ON/OFF）
  - `docs/requirements.md §3` の更新内容

---

## 決着させる未決事項

| OQ | 決定 | 決め方 |
|---|---|---|
| **OQ-27** ★ Pi 4 継続可否 | **設計では決めない。判定規則だけを実測前に固定する。** 判定値は `continue` / `continue_with_constraints` / `insufficient` / `deferred` の4値 | `judge-oq27` が段階別レイテンシ・資源使用・改善適用履歴から規則を適用し、結論と根拠を `measurements.md` に記録する |

**OQ-27 の判定規則**（[Oq27Judge](#oq27judge) と同一。**実測前に確定させ、結果とともに記録する**）:

> **GATE 0（前提）**: `docs/development-environment.md §13.2` の改善項目（Color 削減 → 解像度 → ROI →
> fps → 画像処理削減 → PointCloud 回避 → アルゴリズム簡略化 → 最適化）のうち**未適用のものが残っている間、
> `insufficient` を出さない**。適用済み項目と前後の計測値を証跡として要求する（`tech.md` 開発標準4）。
>
> **GATE 1（試行数）**: 有効投擲数またはセッション数が設定下限に満たない場合は `deferred`。
> **投擲はばらつきが大きく再現性が低いため、単発では判断しない**。
>
> **GATE 2（入力元）**: 実機（live）由来の投擲が1件も無い場合は `deferred` とし、
> 合成・記録再生の結果を実機の結論として扱わない。
>
> 上記を通過したうえで、
> 1. **end-to-end レイテンシ p95 が、同一測定で得た実測オーバーヘッド相当値を超えない** → `continue`
> 2. 超え、かつ **CPU 使用率の飽和または取りこぼしの増加**を伴う → `insufficient`
> 3. 超えるが計算資源に余裕があり、律速段階が取得区間である → `continue_with_constraints`
>    （律速している取得条件を明示する）
> 4. 上記で決まらない → `deferred`（理由を明示）
>
> **絶対値の目標を置かない。** 比較対象は同一測定内で得た量であり、判定はばらつきと併記する
> （`tech.md` 開発標準1）。

**OQ-05 は決着させない**（要件 10）。予測側から見た成功率の上限と、必要試行数の算出のみを材料として出す。
成功率の目標値は移動体の実測（M2）が入って初めて置ける。

**未決のまま残すもの**（明示）:

- **OQ-40**（全体のディレクトリ構成）: 本 Spec は `src/m1_validation/` 1パッケージの位置だけを定める
- **OQ-41**（Python 環境構築・パッケージ管理）: 既存の `pyproject.toml` に**追記のみ**で乗る
- **OQ-01**（投擲レイアウトの定義）: 設定として外部化し、本 Spec の実測を確定の材料として提供する。
  **設計で数値を確定させない**
- **OQ-02**（対象ゴミの最終スコープ）: 空き缶 φ65 mm は M1 の仮値。設定値として分離する
- **OQ-39**（柱3 を作るか）: 本 Spec でデータが貯まってから再判断する。集計・検索機能を作らない

---

## Architecture

### Existing Architecture Analysis

- リポジトリには `src/prediction_core/`（実装済み・実行時依存ゼロ）のみが存在する。
  上流3パッケージは**仕様化済み・未実装**である
- 既存パターンとして踏襲すべきもの:
  - **公開入口（`__init__.py`）は再エクスポート専用**で、ロジックを持たない
  - 値オブジェクトは `frozen=True, slots=True` の dataclass、フィールド名に単位を含む（`_mm` / `_ms` / `_px`）
  - **無効は値、例外は呼び出し方の誤り**（`prediction_core` の方針。上流2 Spec も踏襲）
  - 判定は「**規則の説明文 ＋ 判定値 ＋ 根拠**」を同じ出力に含める（`OverheadVerdict` / `DetectorDecision`）
  - サードパーティ依存は **extras として宣言し、import する場所を1モジュールに閉じる**
    （`cv2` は `mask_ops.py`、`pyrealsense2` は live アダプタ）
  - 境界テスト（`test_boundaries.py`）で依存方向と禁止 import を**静的に固定する**
- 技術的負債として引き受けるもの: 配布名が `prediction-core` のままである（OQ-40 で解決）

### Architecture Pattern & Boundary Map

**選択したパターン**: **収集側（実機）と評価側（開発PC）を、記録を唯一の境界として分離するパイプライン。**

```mermaid
graph TB
    subgraph Upstream
        SF[sensing foundation]
        FOT[flying object tracking]
        WFC[world frame calibration]
        PC[prediction core]
    end
    subgraph CollectSide
        UG[UpstreamGateway]
        SEAM[Seam]
        RUN[ThrowRunner]
    end
    subgraph Records
        TR[throw records ndjson]
        LOG[structured log ndjson]
        SESS[session recording]
    end
    subgraph EvalSide
        TRUTH[TruthDeriver]
        MET[Metrics]
        ATTR[ErrorAttributor]
        JUDGE[Judgement]
        REP[Reporter]
        PLOT[Plotter]
    end
    SF --> UG
    FOT --> SEAM
    WFC --> SEAM
    PC --> SEAM
    UG --> RUN
    SEAM --> RUN
    RUN --> TR
    RUN --> LOG
    UG --> SESS
    TR --> TRUTH
    LOG --> MET
    TRUTH --> MET
    MET --> ATTR
    ATTR --> JUDGE
    MET --> JUDGE
    JUDGE --> REP
    MET --> PLOT
    ATTR --> PLOT
```

**Architecture Integration**:

- **境界の切れ目は「記録」である。** 収集側は実機で走り、評価側は記録だけを読む。
  評価側は上流3パッケージを import しない。これにより
  「集計・表示を Pi 上で動かさない」（`structure.md`）と「実機なしで再現できる」（要件 12.5）が
  運用規則ではなく**構造**になる
- **上流ごとの接点を1モジュールに固定する。** `sensing_foundation` は `upstream.py`、
  `flying_object_tracking` と `world_frame_calibration` は `seam.py`。
  `prediction_core` は依存ゼロの共通語彙としてどの層からも参照してよい
- **継ぎ目（`seam.py`）は本 Spec の存在理由**であり、他のどこにも変換を書かない
- **新規コンポーネントの根拠**: 継ぎ目・真値・帰属・判断・可視化は、どの上流も持っていない（`research.md` Build vs Adopt）
- **steering 準拠**: 開発標準1（未実測の数値を合否条件にしない）・3（アルゴリズムを2言語に置かない）・
  4（部品を替える前に設定とソフトで詰める）・5（計測が計測対象を歪めない）・6（Record / Replay 前提）

### Dependency Direction

```
errors → types → layout → config
                             ↓
        （収集側）  upstream ─┬→ seam → runner
                             │
        （評価側）  truth → metrics/* → attribution → judgement/* → report → plot
                                                                              ↓
                                                                             cli
```

- 各層は**左の層のみ**を import する。上向きの import は誤りとして扱う
- `prediction_core` は層に属さない共通語彙として、`types` 以降のどの層からも参照してよい
- **評価側から収集側への import は禁止**（`truth` 以降が `upstream` / `seam` / `runner` を import しない）
- **収集側から `plot` への import は禁止**
- `cli.py` は全層を参照してよい唯一のモジュールであり、ロジックを持たない

### Technology Stack

| Layer | Choice / Version | Role in Feature | Notes |
|---|---|---|---|
| 言語 | Python >= 3.11 | 全体 | 既存 `pyproject.toml` の `requires-python` に従う |
| 予測 | `prediction_core`（実装済み） | 逐次予測・Throw Record | 公開18シンボルのみ消費。**実行時依存ゼロを壊さない** |
| 入力・記録・ログ | `sensing_foundation`（extras `sensing`） | フレーム供給・NDJSON ログ・Throw Record 保存・ログ集計 | 接点は `upstream.py` |
| 検出・追跡 | `flying_object_tracking`（extras `tracking`） | カメラ座標系の点列 | 接点は `seam.py` |
| 座標変換 | `world_frame_calibration`（extras `calibration`） | World 変換・検証レポート | 接点は `seam.py`。変換を再実装しない |
| 可視化 | **matplotlib >= 3.8**（extras `m1-viz`） | 落下地点・時系列・収束・帰属の図 | **`plot.py` のみが import する。開発PC 専用** |
| 集計・判断 | 標準ライブラリのみ | 実測算出・帰属・判断 | **本 Spec の評価側は追加依存を持たない** |
| テスト | pytest（既存 `dev` グループ） | 単体・結合・E2E・境界 | 実機不要で全経路を通す |

> **本 Spec 自身が宣言するサードパーティ依存は matplotlib 1つだけである。**
> パイプライン実行に必要な numpy / OpenCV は上流 extras の中で完結しており、本 Spec は再宣言しない。

---

## File Structure Plan

### Directory Structure

```
src/m1_validation/
├── __init__.py            # 公開APIの再エクスポート専用。ロジックを持たない
├── errors.py              # 例外階層と失敗理由の列挙
├── types.py               # 値オブジェクト: SampleProvenance / ThrowSamples / TruthValue /
│                          #   ThrowTruth / ThrowMeasurement / Aggregate / Judgement /
│                          #   M1_EXTRA_VERSION と列挙型
├── layout.py              # ThrowLayout（投擲位置・方向・待機位置・対象物寸法・リリース高さ）
│                          #   OQ-01 / OQ-02 の仮値の置き場。数値をコードへ埋め込まない
├── config.py              # M1Settings（判定規則の閾値・下限・出力先）と解決順序
├── upstream.py            # sensing_foundation との唯一の接点。フレーム入力・セッション時計・
│                          #   構造化ログ・Throw Record 保存・ログ集計の委譲
├── seam.py                # ★ flying_object_tracking / world_frame_calibration との唯一の接点。
│                          #   CameraTrack + CalibrationResult → Sample 列 + provenance。
│                          #   検証ゲートもここに置く
├── runner.py              # 1投擲の実行（取得 → 追跡 → 継ぎ目 → 逐次予測 → 記録）と計測点
├── truth.py               # 真値の受け取り・導出（落下地点=実測 / 落下時刻=内挿 / リリース=外挿）
├── metrics/
│   ├── __init__.py
│   ├── flight.py          # 実測項目 1 / 2 / 6（総飛行時間・リリース〜検出開始・狙い誤差）
│   ├── accuracy.py        # 実測項目 4 / 5（落下地点誤差・落下時刻誤差の系列）
│   ├── convergence.py     # 実測項目 7（有効サンプル数・収束サンプル数と判定規則）
│   ├── latency.py         # 実測項目 3 ＋ §13.1（段階別・end-to-end・資源）の集計
│   └── aggregate.py       # 投擲群への束ね直し（代表値・ばらつき・試行数・暫定印）
├── attribution.py         # ★ 誤差の帰属（共通偏り / ばらつき、World 固定 vs カメラ視線方向）
├── judgement/
│   ├── __init__.py
│   ├── budget.py          # 時間予算表の更新値の算出（§3 更新の入力。ゲート付き）
│   ├── oq27.py            # Pi 4 継続可否の判定規則と適用
│   └── oq05.py            # NFR-7 の目標値・試行回数 N の判断材料
├── overhead.py            # 計測 ON/OFF 比較（上流と同じ判定基準の形）
├── report.py              # 人間可読の要約 ＋ 機械可読 JSON
├── plot.py                # ★ matplotlib を import する唯一の場所。開発PC 専用
└── cli.py                 # run-throw / ingest-truth / measure / attribute / judge-oq27 /
                           #   material-oq05 / budget / bench-overhead / report / plot

tests/m1_validation/
├── conftest.py            # 共通フィクスチャ（一時ディレクトリ・既定設定・既定レイアウト）
├── synthetic.py           # 既知の放物軌道 → CameraTrack / Throw Record の合成器（テストツリーに置く）
├── fakes.py               # 上流公開型を模した最小ダブル（CameraTrack / CalibrationResult / ログ）
├── test_types.py
├── test_layout.py
├── test_config.py
├── test_seam.py           # 変換の正しさ・座標系不一致・形式版・除外理由の計数
├── test_seam_gate.py      # 未検証キャリブレーションの拒否と、許可時の印
├── test_deprojection_contract.py  # ★ クロス Spec 契約: 追跡側とキャリブレーション側の
│                          #   逆投影が同一入力に対して完全一致すること（要件 1.10）
├── test_upstream.py       # 上流ダブルでの委譲（フレーム・ログ・保存）
├── test_runner.py         # 1投擲の実行順序・逐次予測・記録・失敗投擲の扱い
├── test_truth.py          # 内挿・外挿・欠測・不確かさの併記
├── test_metrics_flight.py
├── test_metrics_accuracy.py
├── test_metrics_convergence.py   # 収束判定規則
├── test_metrics_latency.py       # 未知 stage の読み取りと end-to-end の定義
├── test_aggregate.py             # 代表値・ばらつき・試行数下限・暫定印
├── test_attribution.py           # ★ 注入した既知の原因を指すこと
├── test_judgement_oq27.py        # GATE 0/1/2 と4値の分岐
├── test_judgement_oq05.py
├── test_budget.py                # 実測値が揃うまで更新しないゲート
├── test_overhead.py
├── test_report.py
├── test_plot.py                  # 依存が無い環境での縮退を含む
├── test_cli.py
├── test_e2e_synthetic.py         # 合成入力で継ぎ目から判断まで通す
├── test_determinism.py           # 同一入力に同一の集計値と同一の判定
└── test_boundaries.py            # 依存方向・上流 import 集中・評価側の隔離・plot 隔離
```

### Modified Files

- `pyproject.toml` — `[tool.hatch.build.targets.wheel].packages` に `src/m1_validation` を**追記**する。
  `[project.optional-dependencies]` に **`m1-viz = ["matplotlib>=3.8"]`** を**追記**する。
  **`[project].dependencies` は空のまま変更しない**。
  ⚠️ **この追記は、`tests/prediction_core/test_packaging.py` の extras 許可リスト化
  （`sensing-foundation` 所有・`ALLOWED_OPTIONAL_EXTRAS` に `m1-viz` を含む）が landing した後にのみ有効**である
  （「Prerequisites」参照）。**本 Spec は同テストを改変しない**。
  ⚠️ **上流3 Spec が同じファイルを追記する。既存行を書き換えず、衝突時は両方の追記を残す**
- `docs/requirements.md` — **§3 の時間予算表**を実測値の列を持つ形へ更新し、
  区間2 を「検出開始〜**初回予測**」と読み替える。あわせて**表から導出されている NFR-3 の暫定目標**を
  更新後の表と整合させる。**§3 以外の節を書き換えない。導出根拠と ⚠️ 注記を削らない**（要件 11）
- `.kiro/specs/m1-prediction-validation/procedure.md` — **新規**。投擲実験の手順書
  （レイアウトの固定、キャリブレーション検証の先行、真値の測り方、試行数、記録の持ち帰り）
- `.kiro/specs/m1-prediction-validation/measurements.md` — **新規**。実測7項目・帰属・段階別レイテンシ・
  改善適用履歴・**OQ-27 の結論と根拠**・OQ-05 の材料を人が読む形で記録する
  （生データは `var/` 配下で版管理しない）

> `src/prediction_core/**` / `src/sensing_foundation/**` / `src/flying_object_tracking/**` /
> `src/world_frame_calibration/**` は**一切変更しない**。
> `.gitignore` は編集しない（`var/` は `sensing-foundation` が追加する。本 Spec の出力は `var/m1/` に置く）。

---

## System Flows

### 1投擲の実行（収集側・実機）

```mermaid
sequenceDiagram
    participant CLI
    participant Runner
    participant Upstream
    participant Seam
    participant Tracker as ThrowPredictionTracker
    CLI->>Seam: キャリブレーション結果を読み検証状態を確認
    alt 検証未通過かつ許可なし
        Seam-->>CLI: 実行を拒否
    end
    CLI->>Upstream: Logger ハンドルを取得
    CLI->>Seam: 上流の追跡設定を解決（素通し）
    CLI->>Runner: 投擲開始（追跡設定と Logger を同伴）
    Runner->>Seam: 追跡パイプラインを生成（設定と Logger を素通し）
    Runner->>Upstream: フレーム供給を開く
    loop フレームごと
        Upstream-->>Runner: CaptureFrame
        Runner->>Seam: 追跡を1フレーム進め、追加点があれば変換
        alt 有効なサンプルが得られた
            Seam-->>Runner: Sample と provenance
            Runner->>Tracker: サンプルを追加
            Tracker-->>Runner: PredictionOutcome
            Runner->>Upstream: predict stage の計測を送出
        else 除外
            Runner->>Upstream: 除外理由の計数を送出
        end
    end
    Runner->>Upstream: Throw Record を保存
```

**流れ上の決定**:
- **検証ゲートは投擲を始める前に評価する。** 走らせてから拒否すると実験時間を捨てることになる
- 追跡が1点も出さなかった投擲は**失敗として理由付きで記録**し、成功試行の集計から除く（要件 3.8）
- 計測の送出は fire-and-forget（上流のロガーに委譲）。**予測経路の中で集計しない**（`tech.md` 開発標準5）

### 評価と判断（評価側・開発PC）

```mermaid
graph TB
    REC[throw records] --> TRUTH[真値の導出]
    TRUTH --> ITEMS[実測7項目]
    LOG[structured log] --> LAT[段階別レイテンシ]
    ITEMS --> AGG[投擲群への集計]
    LAT --> AGG
    AGG --> ATTR[誤差の帰属]
    AGG --> OQ27[OQ-27 判定]
    LAT --> OQ27
    AGG --> OQ05[OQ-05 材料]
    AGG --> BUDGET[時間予算表の更新値]
    ATTR --> REPORT[レポート]
    OQ27 --> REPORT
    OQ05 --> REPORT
    BUDGET --> REPORT
    REPORT --> DOCS[docs requirements の 3 節を更新]
```

**流れ上の決定**:
- **`BUDGET` は実測値の存在をゲートとする**（要件 11.1）。揃わない場合は更新値を出さずに欠測を返す
- `OQ27` は `BUDGET` に依存しない（時間予算表が未更新でも判定できる）が、
  **GATE 0 / 1 / 2 を通らなければ `deferred` を返す**

### 誤差帰属の判定（要件 6）

```mermaid
graph TB
    E[投擲群の誤差ベクトル] --> DEC[共通偏りとばらつきに分解]
    DEC --> B{共通偏りは有意か}
    B -->|いいえ| S1[偏り成分なし]
    B -->|はい| DIR{向きは何に整合するか}
    DIR -->|World 固定かつ検証レポートの偏りと整合| CAL[キャリブレーション由来]
    DIR -->|カメラ視線方向かつ検証は偏りなし| DET[検出由来の候補]
    DIR -->|どちらとも整合しない、または両者が縮退| UNK[判別不能]
    S1 --> V{ばらつきは観測由来の範囲内か}
    CAL --> V
    DET --> V
    UNK --> V
    V -->|範囲内| NOISE[観測ノイズ由来]
    V -->|超過かつ残差大| MODEL[モデル由来]
    V -->|超過だが残差小| UNK2[判別不能]
```

**流れ上の決定**:
- **判別不能は正常な結果**である（要件 6.10）。無理に一つの原因へ割り当てると、
  OQ-27 や時間予算の判断まで誤らせる
- 偏りとばらつきは**独立に判定する**。両方が同時に存在しうる

---

## Requirements Traceability

| Requirement | Summary | Components | Interfaces | Flows |
|---|---|---|---|---|
| 1.1-1.3, 1.7-1.9 | 継ぎ目の変換と除外 | Seam | `build_samples` | 1投擲の実行 |
| 1.4-1.6 | 座標系・形式版・設定の不一致 | Seam, Errors | `build_samples` / `SeamFailure` | 1投擲の実行 |
| 1.10 | 上流2経路の逆投影一致（クロス Spec 契約）★ | Seam, integration tests | `deproject_pixel`（上流公開入口） | 1投擲の実行 |
| 2.1-2.5 | 検証ゲートと印 | CalibrationGate（Seam 内）, ThrowRunner | `open_calibration` | 1投擲の実行 |
| 3.1-3.2, 3.6-3.8 | 実行と逐次予測 | ThrowRunner, UpstreamGateway | `run_throw` | 1投擲の実行 |
| 3.3-3.5 | Throw Record への記録 | ThrowRunner, M1Types | `M1_EXTRA_VERSION` / `extra["m1"]` | 1投擲の実行 |
| 4.1-4.7 | 真値の定義と不確かさ | TruthDeriver | `derive_truth` / `ThrowTruth` | 評価と判断 |
| 5.1-5.2, 5.6 | 実測項目 1 / 2 / 6 | FlightMetrics | `measure_flight` | 評価と判断 |
| 5.3-5.5 | 実測項目 3 / 4 / 5 | AccuracyMetrics, LatencyAggregator | `measure_accuracy` / `aggregate_latency` | 評価と判断 |
| 5.7-5.8 | 実測項目 7 と収束規則 | ConvergenceAnalyzer | `analyze_convergence` | 評価と判断 |
| 5.9-5.11 | 集計・試行数下限・想定との併置 | ThrowAggregator, Reporter | `aggregate` | 評価と判断 |
| 6.1-6.11 | 誤差の帰属 ★ | ErrorAttributor | `attribute` / `AttributionResult` | 帰属の判定 |
| 7.1-7.4, 7.9 | 段階別・end-to-end 集計 | LatencyAggregator, UpstreamGateway | `aggregate_latency` | 評価と判断 |
| 7.5-7.8 | 計測 ON/OFF 比較 | OverheadBench | `run_overhead` | — |
| 8.1-8.7, 8.10 | 可視化の内容 | Plotter | `plot_*` | 評価と判断 |
| 8.8-8.9 | 可視化依存の隔離と縮退 | Plotter, CLI | `plot` サブコマンド | — |
| 9.1-9.11 | OQ-27 の判断 ★ | Oq27Judge | `judge_oq27` / `Judgement` | 評価と判断 |
| 10.1-10.5 | OQ-05 の材料 | Oq05Material | `oq05_material` | 評価と判断 |
| 11.1-11.8 | 時間予算表の更新 | BudgetUpdater, Reporter | `compute_budget_update` | 評価と判断 |
| 12.1-12.6 | 実機非依存と再現性 | 全コンポーネント, tests | 合成器・最小ダブル | — |
| 13.1-13.9 | 境界・依存・設定 | M1Settings, ThrowLayout, boundaries test | `M1Settings.resolve` / `describe` | — |

---

## Components and Interfaces

| Component | Layer | Intent | Req Coverage | Key Dependencies (P0/P1) | Contracts |
|---|---|---|---|---|---|
| Errors | L0 | 失敗理由の列挙と例外階層 | 1.4, 1.5, 1.6, 2.1 | — | State |
| M1Types | L0 | 値オブジェクトと共通の判定結果の形 | 1.8, 3.3, 4.4, 9.1 | prediction_core (P0) | State |
| ThrowLayout | L1 | 投擲レイアウトの外部化（OQ-01 / OQ-02 の仮値） | 4.3, 5.6, 13.8 | M1Types (P0) | State |
| M1Settings | L1 | 設定の解決と起動時検証 | 5.10, 9.9, 13.5, 13.6, 13.7 | ThrowLayout (P0) | Service, State |
| UpstreamGateway | L2 | `sensing_foundation` との唯一の接点 | 3.1, 3.6, 7.1, 7.3, 7.4 | sensing_foundation (P0) | Service |
| Seam ★ | L2 | `CameraTrack` → `Sample`、検証ゲート | 1.1-1.9, 2.1-2.3 | flying_object_tracking (P0), world_frame_calibration (P0), prediction_core (P0) | Service |
| ThrowRunner | L3 | 1投擲の実行と記録 | 3.1-3.8, 2.3, 7.4 | Seam (P0), UpstreamGateway (P0) | Service, Batch |
| TruthDeriver | L4 | 真値の受け取りと導出 | 4.1-4.7 | prediction_core (P0) | Service |
| FlightMetrics | L5 | 実測項目 1 / 2 / 6 | 5.1, 5.2, 5.6 | TruthDeriver (P0), ThrowLayout (P0) | Service |
| AccuracyMetrics | L5 | 実測項目 4 / 5 | 5.4, 5.5 | TruthDeriver (P0) | Service |
| ConvergenceAnalyzer | L5 | 実測項目 7 と収束規則 | 5.7, 5.8 | M1Settings (P0) | Service |
| LatencyAggregator | L5 | 実測項目 3 ＋ §13.1 | 5.3, 7.1-7.4, 7.9 | UpstreamGateway の集計 (P1) | Service, Batch |
| ThrowAggregator | L5 | 投擲群への束ね直し | 5.9, 5.10, 2.5 | 各 Metrics (P0) | Service |
| ErrorAttributor ★ | L6 | 誤差の帰属 | 6.1-6.11 | ThrowAggregator (P0) | Service |
| BudgetUpdater | L7 | 時間予算表の更新値（ゲート付き） | 11.1-11.8 | ThrowAggregator (P0) | Service |
| Oq27Judge ★ | L7 | Pi 4 継続可否の判定 | 9.1-9.11 | LatencyAggregator (P0), ThrowAggregator (P0) | Service |
| Oq05Material | L7 | NFR-7 の判断材料 | 10.1-10.5 | AccuracyMetrics (P0) | Service |
| OverheadBench | L7 | 計測 ON/OFF 比較 | 7.5-7.8 | ThrowRunner (P0) | Batch |
| Reporter | L8 | 要約と機械可読出力 | 5.11, 6.9, 9.4, 10.4, 11.2 | 上位すべて (P0) | Batch |
| Plotter | L8 | 落下地点ほかの図 | 8.1-8.10 | matplotlib (P1) | Batch |
| CLI | L9 | 実行入口 | 12.6, 13.5 | 全層 (P0) | Batch |

### L0-L1: 型・レイアウト・設定

#### Errors

| Field | Detail |
|---|---|
| Intent | 失敗を識別可能な理由付きで表し、呼び出し方の誤りと観測の不成立を区別する |
| Requirements | 1.4, 1.5, 1.6, 2.1 |

**Responsibilities & Constraints**

- 上流の方針（**無効は値、例外は呼び出し方の誤り**）を踏襲する。ただし
  **「継ぎ目が成立しない」は例外**とする。座標系や形式版が食い違ったまま値が下流へ流れること自体が事故である
- 失敗理由を列挙する: `FRAME_MISMATCH`（座標系不一致）/ `UNKNOWN_HANDOFF_VERSION` /
  `UNKNOWN_CALIBRATION_VERSION` / `PROFILE_MISMATCH` / `CALIBRATION_NOT_VERIFIED` /
  `NO_VALID_SAMPLE` / `TRUTH_MISSING` / `INSUFFICIENT_TRIALS` / `UNKNOWN_RECORD_SCHEMA`
- 例外には**実測値と判定に用いた基準**を載せる文脈情報を持たせる

**Contracts**: State [x]

**Implementation Notes**

- Integration: 基底例外 `M1ValidationError` の下に、設定・入力の誤り（`M1ConfigError`）と
  継ぎ目の不成立（`SeamFailure`）を置く
- Risks: 「拒否」を例外にすると実験中に落ちる。**投擲の前に評価する**設計（System Flows）で緩和する

#### M1Types

| Field | Detail |
|---|---|
| Intent | 値オブジェクトと、すべての判断が載る共通の形を定義する |
| Requirements | 1.8, 3.3, 4.4, 5.9, 6.9, 9.1 |

**Responsibilities & Constraints**

- すべて `frozen=True, slots=True` の dataclass。距離 mm / 時刻 ms / 角度 deg をフィールド名に含める
- **判断はすべて `Judgement` という共通の形に載せる**（`research.md` Generalization）。
  規則の説明文を結果と同じ場所に持たせることで、「どの基準で判断したか」が結果から離れない
- **真値はすべて `TruthValue` という共通の形に載せる**（値・求め方・不確かさ）

**Contracts**: State [x]

##### State Management

```python
M1_EXTRA_VERSION: str = "1.0"     # ThrowRecord.extra["m1"] の形の版

class TruthMethod(StrEnum):
    MEASURED = "measured"          # メジャー等による実測
    INTERPOLATED = "interpolated"  # 観測点の内挿
    EXTRAPOLATED = "extrapolated"  # 推定軌道の外挿
    EXTERNAL_MARK = "external_mark"  # 外部の合図
    MISSING = "missing"

class SampleReject(StrEnum):
    NOT_FINITE = "not_finite"
    BELOW_FLOOR = "below_floor"
    DEPTH_SPREAD_TOO_LARGE = "depth_spread_too_large"
    INSUFFICIENT_VALID_PIXELS = "insufficient_valid_pixels"

class Attribution(StrEnum):
    CALIBRATION = "calibration"
    DETECTION = "detection"
    PREDICTION = "prediction"
    OBSERVATION_NOISE = "observation_noise"
    NONE = "none"
    UNDETERMINED = "undetermined"

class Oq27Verdict(StrEnum):
    CONTINUE = "continue"
    CONTINUE_WITH_CONSTRAINTS = "continue_with_constraints"
    INSUFFICIENT = "insufficient"
    DEFERRED = "deferred"

@dataclass(frozen=True, slots=True)
class TruthValue:
    """真値。値だけでなく求め方と不確かさを必ず伴う（要件 4.4）。"""
    value: float | tuple[float, float, float] | None
    method: TruthMethod
    uncertainty_mm: float | None      # 位置の真値のとき
    uncertainty_ms: float | None      # 時刻の真値のとき
    source: str                       # 測り方の記述。必須（要件 4.1）

@dataclass(frozen=True, slots=True)
class SampleProvenance:
    """Sample に入れられない観測品質。samples と同じ順序・同じ長さで保持する。"""
    frame_index: int
    frame_seq: int
    valid_depth_px: int
    depth_spread_mm: float
    apparent_diameter_px: float
    expected_diameter_px: float
    rivals: int
    gap_before: int
    camera_ray_unit: tuple[float, float, float]   # World 系で表したカメラ視線方向（要件 6.3）

@dataclass(frozen=True, slots=True)
class ThrowSamples:
    """継ぎ目の出力（要件 1.1 / 1.8）。"""
    samples: tuple[Sample, ...]                   # prediction_core.Sample
    provenance: tuple[SampleProvenance, ...]      # len は samples と一致（不変条件）
    rejected: tuple[tuple[SampleReject, int], ...]
    handoff_version: str
    calibration_id: str
    verification_state: str
    verified: bool                                # False なら全生成物に印が付く（要件 2.2）

@dataclass(frozen=True, slots=True)
class Judgement:
    """すべての判断が載る共通の形（要件 5.8 / 6.1 / 9.1）。"""
    question: str                  # 例: "OQ-27" / "convergence" / "attribution"
    criterion: str                 # 実測前に固定した規則の説明文
    verdict: str
    rationale: str
    evidence: Mapping[str, object]
    provisional: bool              # 試行数下限未達・実機以外の入力など
```

- Preconditions: `len(samples) == len(provenance)`
- Invariants: `TruthValue.source` は空文字列を許さない。`Judgement.criterion` は空文字列を許さない

**Implementation Notes**

- Integration: `Sample` / `SourceKind` / `Prediction` 等は `prediction_core` の公開入口から参照し、**再定義しない**
- Validation: 生成時に検証しない（上流3 Spec と同じ方針）。検証は各サービスの境界で行う
- Risks: `provenance` を添字で対応させる設計は壊れやすい。**長さ一致をテストで固定する**

#### ThrowLayout

| Field | Detail |
|---|---|
| Intent | 投擲レイアウトを設定として外部化し、数値をコードへ埋め込まない |
| Requirements | 4.3, 5.6, 13.7, 13.8, 13.9 |

**Contracts**: State [x]

```python
@dataclass(frozen=True, slots=True)
class ThrowLayout:
    layout_id: str
    release_position_world_mm: tuple[float, float, float] | None  # 分かる場合のみ
    release_height_mm: float                # リリース時刻の外挿に使う高さ（要件 4.3）
    throw_direction_deg: float              # World +X からの角度
    standby_position_world_mm: tuple[float, float]   # 待機位置（狙い誤差の基準。要件 5.6）
    object_diameter_mm: float               # 空き缶 φ65。OQ-02 の仮値
    aperture_diameter_mm: float             # 開口 φ200。NFR-5 の窓の算出に使う
    camera_position_world_mm: tuple[float, float, float]  # カメラ視線方向の算出に使う（要件 6.3）
    notes: str

    @property
    def position_tolerance_mm(self) -> float:
        """NFR-5 の暫定許容窓 = 開口半径 − 対象寸法/2。**合否条件ではない**。"""
```

- Invariants: `release_height_mm > 0`、`object_diameter_mm < aperture_diameter_mm`

**Implementation Notes**

- Integration: レイアウトは JSON ファイルとして与える。**投擲位置を2箇所以上にできる形**にしておく
  （`research.md` Decision 4 の縮退対策）
- Risks: `position_tolerance_mm` が合否条件として一人歩きしやすい。
  **docstring と図の注記に「暫定目標値」と明記する**（要件 8.3）

#### M1Settings

| Field | Detail |
|---|---|
| Intent | 判定規則の閾値・下限・出力先を、既定 → ファイル → 環境変数 → 実行時指定の順で解決する |
| Requirements | 5.10, 9.9, 13.5, 13.6, 13.7 |

**Contracts**: Service [x] / State [x]

```python
@dataclass(frozen=True, slots=True)
class SeamConfig:
    require_verified_calibration: bool = True   # 要件 2.1。**既定で拒否**
    min_valid_depth_px: int = 8
    max_depth_spread_mm: float = 200.0
    floor_margin_mm: float = -50.0              # これより下は床面下として除外

@dataclass(frozen=True, slots=True)
class ConvergenceConfig:
    band_mm: float | None = None   # None ならレイアウトの暫定許容窓に揃える（要件 5.8）
    require_monotonic_tail: bool = True

@dataclass(frozen=True, slots=True)
class AttributionConfig:
    bootstrap_iterations: int = 200
    bootstrap_seed: int = 0                     # 決定性のため固定（要件 12.4）
    direction_agreement_deg: float = 30.0       # 向きが整合するとみなす角度差
    bias_significance_ratio: float = 1.0        # 共通偏りの大きさ / ばらつきの比の下限

@dataclass(frozen=True, slots=True)
class TrialLimits:
    min_valid_throws: int = 20        # 暫定。OQ-05 の「最低20回程度」に揃えた出発点
    min_sessions: int = 2
    require_live_source: bool = True  # OQ-27 の GATE 2

@dataclass(frozen=True, slots=True)
class M1Settings:
    layout: ThrowLayout
    seam: SeamConfig
    convergence: ConvergenceConfig
    attribution: AttributionConfig
    trials: TrialLimits
    improvements_applied: tuple[str, ...] = ()   # §13.2 の適用済み項目（要件 9.4）
    output_root: Path = Path("var/m1")

    @classmethod
    def resolve(cls, *, file: Path | None, env: Mapping[str, str],
                overrides: Mapping[str, object]) -> "M1Settings": ...
    def describe(self) -> dict[str, object]: ...   # --print-settings（要件 13.5）
```

- Preconditions: `bootstrap_iterations > 0`、`0 < direction_agreement_deg < 90`、`min_valid_throws > 0`
- Postconditions: 解決後の設定は不変。実行中に変更されない

**Implementation Notes**

- Integration: 環境変数は `STB_M1_` 接頭辞（上流の `STB_SF_` / `STB_FOT_` と衝突しない）
- Validation: 不正は**起動時**に拒否する（要件 13.6）
- Risks: `min_valid_throws=20` / `band_mm` / `direction_agreement_deg` は**暫定の評価候補**であり、
  必須性能ではない。`--help` と docstring に明記する（要件 13.7、`tech.md` 開発標準1）

### L2: 上流との接点

#### UpstreamGateway

| Field | Detail |
|---|---|
| Intent | `sensing_foundation` との唯一の接点。フレーム供給・時計・ログ・保存・集計を委譲する |
| Requirements | 3.1, 3.6, 7.1, 7.3, 7.4, 7.9 |

**Responsibilities & Constraints**

- **`sensing_foundation` を import する唯一のモジュール**である
- 予約 stage（`system` / `capture` / `record`）と衝突しない**独自 stage `predict` と `m1`** を足す
- **集計を実機上で常時実行する前提を持たない**（要件 7.9）。集計はログファイルを読むだけ
- **`Logger` オブジェクトそのものを取り出す入口 `get_logger_handle()` を持つ。**
  上流の `TrackingPipeline.__init__` が `Logger` を要求するため、`emit` のような
  関数形の委譲だけでは足りない。取り出した `Logger` は**本 Spec のどの層も解釈せず**、
  `cli.py` が受け取って `Seam.open_tracking()` へ素通しするだけの**不透明値**として扱う
  （`sensing_foundation` の接点を1モジュールに閉じたまま、上流の要求を満たすための最小の穴）

**Dependencies**

- Inbound: ThrowRunner（P0）, CLI（P0）
- Outbound: —
- External: `sensing_foundation` 公開入口（P0）

**Contracts**: Service [x]

```python
def open_frames(settings, source_spec) -> Iterator["CaptureFrame"]: ...
def session_clock_ms() -> float: ...            # end-to-end の基準（要件 7.2）
def emit(stage: str, event: str, data: Mapping[str, object]) -> None: ...
def get_logger_handle() -> object: ...          # sensing_foundation の Logger を不透明値として渡す
def store_record(record: "ThrowRecord", path: Path) -> None: ...
def load_records(path: Path) -> Iterator["ThrowRecord"]: ...
def summarize_stages(log_path: Path, stages: Sequence[str] | None) -> Mapping[str, object]: ...
```

- Preconditions: `emit` の `stage` は `system` / `capture` / `record` 以外
- Postconditions: `store_record` は `prediction_core` のスキーマをそのまま用い、再定義しない。
  `get_logger_handle` の戻り値は本 Spec の型ではなく、**属性アクセスも型検査も行わない**
- ⚠️ **上流依存**: `Logger` が `sensing_foundation` の公開入口に含まれることを前提とする。
  `sensing-foundation` 側は本バッチで並行して編集され、`Logger` を公開入口に含めることが保証される

**Implementation Notes**

- Integration: ログ集計は上流の集計器へ委譲する。**集計器を二重に持たない**（`research.md` Decision 7）。
  未知の stage も読めるという上流の性質にそのまま乗る（要件 7.3）
- Validation: Throw Record 読み出し時に `schema_version` を `prediction_core.SCHEMA_VERSION` と照合する
- Risks: 上流未実装の期間は、公開型を模した最小ダブルで検証する。**本体にダブルを置かない**

#### Seam ★

| Field | Detail |
|---|---|
| Intent | カメラ座標系の点列を World 座標系の予測入力へ変換する。**本 Spec の存在理由** |
| Requirements | 1.1-1.9, 2.1, 2.2, 2.3 |

**Responsibilities & Constraints**

- **`flying_object_tracking` と `world_frame_calibration` を import する唯一のモジュール**である
- **座標変換を再実装しない。** 上流の `WorldTransform.apply_point` を呼ぶだけ（要件 1.2）
- **時刻を再基準化しない。** `TrackPoint.point.t_ms` をそのまま `Sample.t_ms` にする（要件 1.3）
- **単位変換を挟まない。** 上流・下流ともに mm / ms で一致している
- 変換前に**検証ゲート**を評価する（要件 2.1 / 2.2）
- 生成した `Sample` と同じ順序・同じ長さの `SampleProvenance` を返す（要件 1.8）
- **カメラ固有の型・画素座標・カメラパラメータを `Sample` の側へ出さない**（要件 1.9）
- ★ **上流2経路の逆投影が一致していることを、本 Spec がクロス Spec 契約として検証する**（要件 1.10）。
  `flying_object_tracking`（3次元点の復元）と `world_frame_calibration`（床平面推定）は、
  バッチ決定により**どちらも `sensing_foundation.geometry` の
  `depth_raw_to_mm` / `is_valid_depth` / `deproject_pixel` に乗る**。
  **両方を import する正当な理由を持つのは本 Spec だけ**であるため、
  2経路が同一入力に対して**完全に同一の値**を返すことを確かめられるのもここだけである。
  仮に両者がずれると、そのずれは共通の偏りとして現れ、
  **「予測が悪い」という単一の症状に潰れる**（`docs/requirements.md §6.2` が警告する失敗そのもの）

**Dependencies**

- Inbound: ThrowRunner（P0）, CLI（P0）
- Outbound: M1Types（P0）, M1Settings（P0）, Errors（P0）
- External: `flying_object_tracking` 公開入口（P0）, `world_frame_calibration` 公開入口（P0）,
  `prediction_core` 公開入口（P0）

**Contracts**: Service [x]

##### Service Interface

```python
def open_calibration(path: Path, *, settings: M1Settings,
                     signature: object, intrinsics: object,
                     allow_unverified: bool = False) -> "CalibrationResult":
    """キャリブレーション結果を読み、検証ゲートと整合性検査を通す。

    Raises:
        SeamFailure(UNKNOWN_CALIBRATION_VERSION): 形式版が未知
        SeamFailure(PROFILE_MISMATCH): ストリーム設定・内部パラメータが不一致
        SeamFailure(CALIBRATION_NOT_VERIFIED): 検証未通過かつ allow_unverified が False
    """

def build_samples(track: "CameraTrack", calibration: "CalibrationResult",
                  *, settings: M1Settings) -> ThrowSamples:
    """カメラ座標系の点列を World 座標系のサンプル列へ変換する。

    Raises:
        SeamFailure(FRAME_MISMATCH): track.frame がカメラ座標系でない
        SeamFailure(UNKNOWN_HANDOFF_VERSION): handoff_version が未知
    """

def camera_ray_unit(calibration: "CalibrationResult",
                    point_world_mm: tuple[float, float, float]) -> tuple[float, float, float]:
    """World 系で表した、カメラから当該点へ向かう単位ベクトル（要件 6.3 の材料）。"""

def resolve_tracking_settings(*, config_path: Path | None,
                              env: Mapping[str, str],
                              overrides: Mapping[str, object]) -> "TrackingSettings":
    """上流の `TrackingSettings.resolve()` への素通し。

    上流の署名は `resolve(cls, *, file, env, overrides)` で**3つとも必須**である。
    本関数が `config_path` だけを受け取って `env` / `overrides` を内部で空に
    埋めると、上流側の環境変数と CLI 上書きが**黙って捨てられる**。
    本 Spec の CLI は「CLI 引数 > 環境変数 > 設定ファイル > 既定値」を掲げているため、
    3つとも呼び出し元（`cli.py`）から受け取り、そのまま渡す。

    `cli.py` が `run_throw` へ渡す値を調達するための入口である。**本 Spec は追跡の
    設定値を一切決めない**（既定値も持たない）。この関数が存在するのは、
    `flying_object_tracking` を import するのが `seam.py` だけという境界を保ったまま、
    `cli.py` が上流の解決結果を手に入れられるようにするためだけである。
    """

def open_tracking(settings: M1Settings, tracking_settings: "TrackingSettings",
                  logger: object) -> "TrackingPipeline":
    """上流の追跡パイプラインを生成して返す。

    `ThrowRunner` が追跡を1フレームずつ進めるための入口である。**この関数が存在する
    ことで `runner.py` は `flying_object_tracking` を import せずに済み、検出・追跡
    パッケージとの接点が本モジュールだけに保たれる**（design.md Allowed Dependencies）。

    上流の `TrackingPipeline.__init__(settings: TrackingSettings, logger: Logger)` は
    **本 Spec が生成できない2つの値**を要求する。したがって本関数は両方を**引数として
    受け取り、そのまま渡すだけ**にする。

    - ``tracking_settings``: `flying_object_tracking.TrackingSettings`。
      **これを調達するのは `cli.py` であり、調達手段は上記 `resolve_tracking_settings()` に限る**
      （`cli.py` は全層を参照してよい唯一のモジュールだが、`flying_object_tracking` を
      直接 import しないため継ぎ目経由で受け取る）。
      本 Spec は追跡の設定・方式を決めない（OQ-26 は上流の担当）。
      **設定オブジェクトを素通しすることは方式を決めることではない**
    - ``logger``: `sensing_foundation` の `Logger`。本 Spec の型ではないため
      `object` として受け、**中身を解釈しない**。取得は `UpstreamGateway.get_logger_handle()`
      に限る（`sensing_foundation` の接点は `upstream.py` 1モジュールという制約を守るため）
    """
```

- Preconditions: `track.frame` はカメラ座標系。`handoff_version` は既知の集合に含まれる
- Postconditions: `len(result.samples) == len(result.provenance)`。
  `result.verified is False` のとき、下流のすべての生成物に未検証の印が伝播する
- Invariants: 出力の `t_ms` は入力の `t_ms` と同一値。**変換は回転と平行移動のみ**（上流の不変条件に従う）

**Implementation Notes**

- Integration: 未知の形式版を**推測して読まない**（上流3 Spec 共通の方針）。
  `check_compatibility` を先に通すことで、解像度変更後に古いキャリブレーションを使い回す事故を防ぐ
- Validation: 除外規則は `SeamConfig` に従い、**除外理由ごとの件数を必ず返す**（要件 1.7）。
  除外を静かに行わない
- Risks:
  - **`Sample` に品質情報を入れたくなる誘惑が必ず生じる。** 入れた時点で `prediction_core` の入力契約が壊れる。
    docstring に「`Sample` は4フィールドのままにする」と明記する
  - 検証ゲートを緩めたくなる場面が実験中に必ず来る。**緩めた事実が生成物に残る形**にしておく

### L3: 実行

#### ThrowRunner

| Field | Detail |
|---|---|
| Intent | 1投擲を取得から逐次予測・記録まで通して実行する |
| Requirements | 2.3, 3.1-3.8, 7.4 |

**Responsibilities & Constraints**

- 上流の追跡パイプラインを1フレームずつ進め、点が追加されたら継ぎ目を通し、
  `ThrowPredictionTracker` へサンプルを追加する。
  **追跡パイプラインは `Seam.open_tracking()` から得る**（本モジュールは検出・追跡パッケージを import しない）。
  `open_tracking` が要求する**上流の追跡設定と `Logger` は、呼び出し元（`cli.py`）から
  不透明値として受け取って素通しする**。本モジュールはどちらも解釈しない
- **予測の更新は `prediction_core` に委ねる。** 本 Spec は再フィットの制御を行わない
- 各予測について `predict` stage の計測を送出し、**end-to-end を後から算出できる材料**を残す
- 記録は `ThrowPredictionTracker.to_record()` を用い、**本 Spec 固有の情報は `extra["m1"]` へ退避する**
  （⚠️ **`to_record()` は `extra` を受け取らない。** マージ済みの `prediction_core.tracker` は
  `ThrowRecord(record_id, source, config, samples, predictions)` を組むだけであり、
  `ThrowRecord` は `@dataclass(frozen=True, slots=True)` である。したがって `extra["m1"]` の付与は
  **`dataclasses.replace(record, extra={...})`**、または `ThrowPredictionTracker.samples` /
  `.predictions` からの**公開コンストラクタでの直接構築**のいずれかで行う。
  `trajectory-simulator` は後者を採っており、**両下流の書き手が同じ手段を採ることを意図して
  ここに明記する**。`prediction_core` 側には一切手を入れない）

**Dependencies**

- Inbound: CLI（P0）, OverheadBench（P0）
- Outbound: Seam（P0）, UpstreamGateway（P0）, M1Settings（P0）
- External: `prediction_core.ThrowPredictionTracker`（P0）

**Contracts**: Service [x] / Batch [x]

```python
@dataclass(frozen=True, slots=True)
class ThrowRunResult:
    record_id: str
    record: "ThrowRecord"
    samples_appended: int
    rejected: tuple[tuple[SampleReject, int], ...]
    first_valid_sample_count: int | None    # 初回予測が成立したサンプル数（要件 5.3）
    failed_reason: str | None               # 追跡不成立などの失敗理由（要件 3.8）

def run_throw(*, settings: M1Settings, source_spec: object,
              calibration_path: Path, record_id: str,
              tracking_settings: object, logger: object,
              allow_unverified: bool = False) -> ThrowRunResult: ...
```

- `tracking_settings` は `flying_object_tracking.TrackingSettings`、`logger` は
  `sensing_foundation.Logger` である。**どちらも `cli.py` が用意し、本関数は
  `Seam.open_tracking()` へ素通しするだけ**である（型注釈を `object` に留めるのは、
  本モジュールが上流パッケージを import しないという制約を型注釈の上でも守るため）

##### Batch / Job Contract

- **Trigger**: CLI `run-throw`
- **Input / validation**: 入力元指定（live / recorded / simulated）とキャリブレーション結果。
  **検証ゲートは投擲を始める前に評価する**
- **Output / destination**: `var/m1/throws/<session>.ndjson`（1行1 Throw Record）＋ 構造化ログ
- **Idempotency & recovery**: 同一の記録済み入力に対して同一の記録を生成する（要件 3.7）。
  失敗投擲も**理由付きで記録**し、後から除外できるようにする（要件 3.8）

**Implementation Notes**

- Integration: 計測の送出は fire-and-forget。**予測経路の中で集計・統計処理を行わない**（`tech.md` 開発標準5）
- Validation: 有効サンプルが0件の投擲は `failed_reason` を付けて記録する
- Risks: 実験中に例外で落ちると1試行を失う。**上流の失敗は投擲単位で捕捉し、次の投擲へ進める**

### L4-L5: 真値と実測

#### TruthDeriver

| Field | Detail |
|---|---|
| Intent | 実際の落下地点・落下時刻・リリース時刻を、求め方と不確かさを伴って求める |
| Requirements | 4.1-4.7 |

**Responsibilities & Constraints**

- **落下地点**: 外部から与えられる実測値。**測り方の記述を必須**とする（要件 4.1）
- **落下時刻**: 観測サンプル列のうち床面高さを跨ぐ隣接2点の**内挿**。跨ぐ区間が無ければ欠測（要件 4.2）
- **リリース時刻**: 推定軌道を観測開始より前へ**外挿**し、レイアウトのリリース高さに達する時刻（要件 4.3）
- 外部の合図が記録されていれば、外挿値との**差をレポートに残す**（要件 4.5）
- **真値の入力は投擲の実行と分離**し、実行後に追記できる（要件 4.7）

**Dependencies**

- Inbound: FlightMetrics（P0）, AccuracyMetrics（P0）, Reporter（P1）
- Outbound: M1Types（P0）, ThrowLayout（P0）
- External: `prediction_core`（軌道パラメータの参照。P0）

**Contracts**: Service [x]

```python
@dataclass(frozen=True, slots=True)
class ThrowTruth:
    record_id: str
    impact_point_world_mm: TruthValue     # 落下地点（要件 4.1）
    impact_time_ms: TruthValue            # 落下時刻（要件 4.2）
    release_time_ms: TruthValue           # リリース時刻（要件 4.3）
    external_mark_delta_ms: float | None  # 外部の合図との差（要件 4.5）

def load_truth_file(path: Path) -> Mapping[str, Mapping[str, object]]: ...
def derive_truth(record: "ThrowRecord", entry: Mapping[str, object] | None,
                 *, layout: ThrowLayout) -> ThrowTruth: ...
```

- Preconditions: 落下地点が与えられる場合、`source`（測り方）が非空であること
- Postconditions: 求められなかった真値は `TruthMethod.MISSING` として返り、**例外を投げない**（要件 4.6）

**Implementation Notes**

- Integration: 外挿は最終予測の軌道パラメータを用いる。**新しいフィッティングを実装しない**
- Validation: 内挿は床面高さを**跨ぐ**区間に限る。片側外挿で落下時刻を作らない
- Risks: **外挿の不確かさが区間1 の実測値をそのまま左右する。**
  外挿区間の長さと軌道の残差から不確かさの目安を算出し、必ず併記する（要件 4.4）

#### FlightMetrics

| Field | Detail |
|---|---|
| Intent | 実測項目 1（総飛行時間）/ 2（リリース〜検出開始）/ 6（狙い誤差）を算出する |
| Requirements | 5.1, 5.2, 5.6 |

**Contracts**: Service [x]

```python
@dataclass(frozen=True, slots=True)
class FlightResult:
    total_flight_ms: float | None          # 項目1: 落下時刻 − リリース時刻
    release_to_detect_ms: float | None     # 項目2: 最初の有効サンプル時刻 − リリース時刻 ★未検証区間
    aim_error_mm: float | None             # 項目6: 待機位置 → 実落下地点の水平距離
    uncertainty_ms: float | None           # 項目1 / 2 に伝播した不確かさ
    methods: Mapping[str, TruthMethod]

def measure_flight(record: "ThrowRecord", truth: ThrowTruth,
                   *, layout: ThrowLayout) -> FlightResult: ...
```

**Implementation Notes**

- Integration: 項目2 は §3 区間1 に対応し、**プロジェクトで最も未検証な量**である。
  求め方（外挿）と不確かさを結果に必ず含め、レポートで強調する
- Risks: リリース時刻が欠測なら項目1 / 2 は欠測。**他項目の集計を止めない**（要件 4.6）

#### AccuracyMetrics

| Field | Detail |
|---|---|
| Intent | 実測項目 4（落下地点誤差）/ 5（落下時刻誤差）を、予測更新のたびの系列として算出する |
| Requirements | 5.4, 5.5 |

**Contracts**: Service [x]

```python
@dataclass(frozen=True, slots=True)
class PredictionError:
    sample_count: int
    based_on_time_ms: float
    hit_error_mm: tuple[float, float]      # 予測 − 実測（ベクトル。帰属で向きを使う）
    hit_error_norm_mm: float
    time_error_ms: float | None
    residual_mm: float
    remaining_time_ms: float

@dataclass(frozen=True, slots=True)
class AccuracyResult:
    errors: tuple[PredictionError, ...]
    first_valid: PredictionError | None
    final: PredictionError | None

def measure_accuracy(record: "ThrowRecord", truth: ThrowTruth) -> AccuracyResult: ...
```

**Implementation Notes**

- Integration: 誤差は**スカラーではなくベクトルとして保持する**。帰属（要件 6.3）が向きを使う
- Validation: 無効な予測（`InvalidPrediction`）は系列から除き、理由ごとに数える
- Risks: 真値が欠測なら誤差も欠測。集計側で試行数として数えない

#### ConvergenceAnalyzer

| Field | Detail |
|---|---|
| Intent | 実測項目 7（何サンプル取れたか / 何サンプルで収束するか）を算出する |
| Requirements | 5.7, 5.8 |

**Contracts**: Service [x]

**収束の判定規則**（**実測前に確定させ、結果とともに記録する**。`research.md` Decision 8）:

> **サンプル数 N 以降のすべての予測落下地点が、その投擲の最終予測から `band_mm` 以内に収まり続ける
> 最小の N** を収束サンプル数とする。`band_mm` の既定はレイアウトの暫定許容窓に揃える。
> 収束しない投擲は「未収束」を正常な結果として返す。
> **収束サンプル数と最終誤差を必ず併記する**（最終予測自体がずれている場合、収束は速く見えるため）。

```python
@dataclass(frozen=True, slots=True)
class ConvergenceResult:
    valid_samples: int
    converged_at: int | None
    band_mm: float
    final_error_mm: float | None
    judgement: Judgement          # criterion に上記の規則の説明文を持つ

def analyze_convergence(accuracy: AccuracyResult, *, settings: M1Settings,
                        layout: ThrowLayout) -> ConvergenceResult: ...
```

**Implementation Notes**

- Integration: この結果が **FR-1 の「3」の妥当性**の材料になる。
  収束サンプル数の分布を集計し、`min_samples` の見直し材料としてレポートへ出す
- Risks: 真値と無関係に定義しているため、**最終誤差の併記を欠かすと誤読される**

#### LatencyAggregator

| Field | Detail |
|---|---|
| Intent | 段階別レイテンシと資源使用を集計し、end-to-end を定義して投擲単位へ束ね直す |
| Requirements | 5.3, 7.1, 7.2, 7.3, 7.4, 7.9 |

**Responsibilities & Constraints**

- 集計対象は `development-environment.md §13.1` の10項目
- **end-to-end の定義**（要件 7.2）: **ある観測の `t_capture_ms` から、その観測を含めた予測が
  得られるまでの経過時間**。定義文を出力に含める
- 上流が記録した stage（`capture` / `record` / `detect` / `track` / `calibrate`）を、
  **集計側の改修なしに**読み取る（上流の集計器へ委譲）
- 実測項目3 は「検出開始（最初の有効サンプル）から**初回予測**が成立するまで」とし、
  **単発予測ではなく初回予測を基準としている旨を結果に明示する**（要件 5.3、D-1）

**Contracts**: Service [x] / Batch [x]

```python
@dataclass(frozen=True, slots=True)
class StageLatency:
    stage: str
    count: int
    p50_ms: float
    p95_ms: float
    iqr_ms: float

@dataclass(frozen=True, slots=True)
class LatencyResult:
    definition: str                       # end-to-end の定義文（要件 7.2）
    stages: tuple[StageLatency, ...]
    end_to_end: StageLatency
    detect_to_first_prediction_ms: tuple[float, ...]   # 実測項目3（投擲ごと）
    capture_fps: float | None
    process_fps: float | None
    cpu_percent_mean: float | None
    rss_bytes_max: int | None
    frames_dropped: int
    frames_missing: int
    unknown_stages: tuple[str, ...]       # 読めたが本 Spec が知らない stage

def aggregate_latency(log_path: Path, records: Sequence["ThrowRecord"]) -> LatencyResult: ...
```

**Implementation Notes**

- Integration: 資源値（CPU・メモリ）は上流が Linux の `/proc` から取得する。**取得できない環境では欠測**
- Validation: 未知 stage は捨てずに `unknown_stages` として残す（要件 7.3）
- Risks: 段階の合計と end-to-end は一致しない（待ち時間・スケジューリングを含むため）。
  **一致しないことを定義文に明記する**

#### ThrowAggregator

| Field | Detail |
|---|---|
| Intent | 投擲群へ束ね直し、代表値・ばらつき・試行数・暫定印を付ける |
| Requirements | 2.5, 5.9, 5.10 |

**Contracts**: Service [x]

```python
@dataclass(frozen=True, slots=True)
class Distribution:
    count: int
    median: float | None
    p95: float | None
    iqr: float | None
    minimum: float | None
    maximum: float | None
    missing: int

@dataclass(frozen=True, slots=True)
class ThrowAggregate:
    calibration_id: str                 # 混在させない（要件 2.5）
    session_ids: tuple[str, ...]
    verified: bool
    live_throw_count: int
    valid_throw_count: int
    provisional: bool                   # 試行数下限未達（要件 5.10）
    items: Mapping[str, Distribution]   # 実測7項目の分布
    error_vectors: tuple[tuple[float, float], ...]   # 帰属の入力
    per_throw: tuple[Mapping[str, object], ...]

def aggregate(results: Sequence[Mapping[str, object]], *,
              settings: M1Settings) -> tuple[ThrowAggregate, ...]: ...
```

**Implementation Notes**

- Integration: **キャリブレーション識別子ごとに分けて集計する**（要件 2.5）。
  混ぜて平均すると、座標系の入れ替わりがばらつきとして紛れ込む
- Validation: 未検証キャリブレーションで得た投擲は、検証済みのものと**同じ集計に混ぜない**
- Risks: 試行数下限は暫定値である。**未達でも集計は返し、暫定印を付けるだけ**にする（判断側で使う）

### L6: 帰属 ★

#### ErrorAttributor

| Field | Detail |
|---|---|
| Intent | 誤差をキャリブレーション・検出・予測へ帰属させる。**本 Spec の核** |
| Requirements | 6.1-6.11 |

**Responsibilities & Constraints**

- **判定規則を実測前に固定し、結果と同じ場所に説明文として持たせる**（要件 6.1）
- 誤差ベクトル群を **共通の偏り成分**（平均ベクトル）と **ばらつき成分**（偏り除去後の RMS）に分解する（要件 6.2）
- 共通の偏りについて、**World 座標系に固定された向き**との整合と、
  **カメラ視線方向**との整合を両方評価する（要件 6.3）
- キャリブレーション検証レポートの偏りと突き合わせる（要件 6.4 / 6.5）
- ばらつきについて、**観測サンプルの再抽出（ブートストラップ）による予測ばらつき**と比較する（要件 6.6 / 6.8）
- **判別不能を正常な結果として返す**（要件 6.10）
- 距離帯ごとの誤差を提示する（要件 6.11）

**判定規則**（[System Flows](#誤差帰属の判定要件-6) と同一。実測前に固定する）:

> **偏り成分**
> 1. 共通偏りの大きさ ÷ ばらつきが `bias_significance_ratio` 未満 → **偏り成分なし**
> 2. 有意なとき、その向きが検証レポートの平均オフセット方向と `direction_agreement_deg` 以内で
>    一致する → **キャリブレーション由来**
> 3. 有意なとき、その向きが投擲ごとのカメラ視線方向と一貫して整合し、かつ検証レポートで偏りが
>    認められない → **検出由来の候補**（Depth が対象物のカメラ側表面を測ることによる系統的な寄り）
> 4. どちらとも整合しない、または**両者が縮退して区別できない**（投擲位置が1箇所でカメラ視線方向が
>    World 固定方向と一致してしまう場合を含む）→ **判別不能**
>
> **ばらつき成分**
> 5. ばらつきがブートストラップで見積もった予測ばらつきの範囲内 → **観測ノイズ由来**
> 6. 範囲を超え、かつフィットの残差代表値が投擲群の上位側にある → **モデル由来（予測）**
> 7. 範囲を超えるが残差が小さい → **判別不能**
>
> **絶対値の目標を置かない。** すべて同一測定内の量どうしの相対比較で定義する。

**Dependencies**

- Inbound: Oq27Judge（P1）, Reporter（P0）, Plotter（P0）
- Outbound: ThrowAggregator（P0）, M1Types（P0）, M1Settings（P0）
- External: `prediction_core.predict`（ブートストラップの再予測。P0）

**Contracts**: Service [x]

```python
@dataclass(frozen=True, slots=True)
class BiasComponent:
    vector_mm: tuple[float, float]
    norm_mm: float
    significance_ratio: float
    world_fixed_agreement_deg: float | None      # 検証レポートの偏りとの角度差
    camera_ray_agreement_deg: float | None       # カメラ視線方向との角度差
    degenerate: bool                             # 2方向が縮退して判別できない
    attribution: Attribution

@dataclass(frozen=True, slots=True)
class ScatterComponent:
    rms_mm: float
    bootstrap_rms_mm: float
    residual_median_mm: float
    attribution: Attribution

@dataclass(frozen=True, slots=True)
class RangeBand:
    range_lo_mm: float
    range_hi_mm: float
    throw_count: int
    mean_error_norm_mm: float

@dataclass(frozen=True, slots=True)
class AttributionResult:
    bias: BiasComponent
    scatter: ScatterComponent
    range_bands: tuple[RangeBand, ...]
    calibration_reference: Mapping[str, object]   # 検証レポートから取り込んだ値（要件 2.4）
    judgement: Judgement

def attribute(aggregate: ThrowAggregate, *, settings: M1Settings,
              layout: ThrowLayout) -> AttributionResult: ...
def bootstrap_prediction_spread(record: "ThrowRecord", *,
                                iterations: int, seed: int) -> float: ...
```

- Preconditions: `aggregate.error_vectors` が1件以上。0件なら `INSUFFICIENT_TRIALS` で失敗
- Postconditions: **合計誤差の単一値を返さない。** 常に成分ごとの内訳を返す（要件 6.9）
- Invariants: 同一入力・同一 seed に対して同一結果（要件 12.4）

**Implementation Notes**

- Integration: 検証レポートの値は**記録に埋め込まれた要約**（`extra["m1"]["calibration"]`）から取り込む。
  評価側は `world_frame_calibration` を import しない
- Validation: ブートストラップは観測サンプルを再抽出して `prediction_core.predict` を呼び直すだけであり、
  **新しい推定器を実装しない**
- Risks:
  - **投擲位置が1箇所だと、World 固定方向とカメラ視線方向が縮退する。**
    その場合 `degenerate=True` として `UNDETERMINED` を返す。
    判別可能性を上げるにはレイアウトで投擲位置を増やす（`ThrowLayout` を外部化してある理由）
  - 「検出由来の候補」は候補にとどめる。**断定すると検出側の改善に誤って時間を使う**

### L7: 判断

#### Oq27Judge ★

| Field | Detail |
|---|---|
| Intent | Raspberry Pi 4 を継続するかを、実測前に固定した規則で判断する（OQ-27） |
| Requirements | 9.1-9.11 |

**Responsibilities & Constraints**

- 判定規則は [決着させる未決事項](#決着させる未決事項) と同一。**結果に説明文として埋め込む**
- **GATE 0**: `M1Settings.improvements_applied` が `§13.2` の全項目を覆っていない間、
  `insufficient` を返さない（要件 9.3）
- **GATE 1 / 2**: 試行数下限・セッション数下限・実機由来の投擲の有無（要件 9.9 / 9.10）
- 律速段階を段階別レイテンシの内訳から特定する（要件 9.5）
- **ハードウェアの置き換えを実行しない**（要件 9.11）

**Dependencies**

- Inbound: Reporter（P0）, CLI（P0）
- Outbound: LatencyAggregator（P0）, ThrowAggregator（P0）, M1Settings（P0）
- External: —

**Contracts**: Service [x]

```python
@dataclass(frozen=True, slots=True)
class ImprovementRecord:
    step: str                 # §13.2 の項目名
    applied: bool
    before: Mapping[str, float]
    after: Mapping[str, float]

@dataclass(frozen=True, slots=True)
class Oq27Result:
    verdict: Oq27Verdict
    bottleneck_stage: str | None
    end_to_end_p95_ms: float
    overhead_reference_ms: float       # 同一測定から得た比較対象（要件 9.2 / 9.6）
    resource_saturated: bool
    improvements: tuple[ImprovementRecord, ...]
    judgement: Judgement

def judge_oq27(latency: LatencyResult, aggregate: ThrowAggregate,
               *, settings: M1Settings) -> Oq27Result: ...
```

- Postconditions: `verdict` が `insufficient` のとき、`improvements` の全項目が `applied=True` であること
- Invariants: `judgement.criterion` は空でない。`provisional` は GATE 1 / 2 の未達を反映する

**Implementation Notes**

- Integration: 比較対象（`overhead_reference_ms`）は**同一測定で得た実測オーバーヘッド相当値**であり、
  設計時の想定 0.2〜0.3 s を合否条件として持ち込まない（`tech.md` 開発標準1）
- Validation: `deferred` を正常な結果として扱う。**判断を急いで暫定値でハードを替えない**
- Risks: 「不足」の判定は購入判断に直結する。**GATE 0 の証跡が無い判定を出せない構造**にしておく

#### Oq05Material

| Field | Detail |
|---|---|
| Intent | NFR-7 の目標成功率と試行回数 N を決めるための材料を提示する（**決着させない**） |
| Requirements | 10.1-10.5 |

**Contracts**: Service [x]

```python
@dataclass(frozen=True, slots=True)
class Oq05Result:
    window_mm: float                  # レイアウトから導いた暫定許容窓
    within_window_ratio: float        # 予測が窓に収まる割合（要件 10.1）
    upper_bound_note: str             # 「予測側から見た上限」である旨（要件 10.2）
    required_trials: Mapping[str, int]   # 信頼区間幅ごとの必要試行数（要件 10.3）
    object_scope_note: str            # 対象物の最終スコープが未決である旨（要件 10.5）
    judgement: Judgement              # verdict は常に "material_only"（要件 10.4）

def oq05_material(aggregate: ThrowAggregate, *, layout: ThrowLayout) -> Oq05Result: ...
```

**Implementation Notes**

- Integration: 必要試行数は二項比率の信頼区間幅から算出する。**新しい統計手法を導入しない**
- Risks: `within_window_ratio` が「キャッチ成功率」として読まれると、
  移動体性能を無視した誤った期待値になる。**注記を出力に含める**（要件 10.2）

#### BudgetUpdater

| Field | Detail |
|---|---|
| Intent | `docs/requirements.md §3` の更新値を、実測値の存在をゲートとして算出する |
| Requirements | 11.1-11.8 |

**Contracts**: Service [x]

```python
@dataclass(frozen=True, slots=True)
class BudgetRow:
    segment: str                 # "1" / "2" / "3" / "total"
    label: str                   # 区間2 は「検出開始〜初回予測」（要件 11.3）
    assumed: str                 # 既存の想定値（そのまま残す）
    measured: Distribution | None
    trials: int
    note: str

@dataclass(frozen=True, slots=True)
class BudgetUpdate:
    ready: bool                  # False なら更新しない（要件 11.1）
    missing_items: tuple[str, ...]
    rows: tuple[BudgetRow, ...]
    total_flight_ms: Distribution | None
    remaining_time_ms: Distribution | None      # 移動体に残された時間
    derived_latency_target_ms: float | None     # NFR-3 の更新値（要件 11.4）
    judgement: Judgement

def compute_budget_update(aggregate: ThrowAggregate, latency: LatencyResult,
                          *, settings: M1Settings) -> BudgetUpdate: ...
```

- Preconditions: なし（未成立なら `ready=False` を返す）
- Postconditions: `ready is False` のとき `rows` の `measured` はすべて `None`

**Implementation Notes**

- Integration: 区間3（予測確定〜移動体が動き出す）は **M1 の範囲外**である（移動体が存在しない）。
  行は残し、`note` に「M3 で実測する」と明記する。**勝手に埋めない**
- Validation: `derived_latency_target_ms` は更新後の表から導出する。
  **表と食い違ったまま放置しない**（`docs/requirements.md` NFR-3 の ⚠️）
- Risks: 更新は `docs/` への書き込みを伴う。**本コンポーネントは値を算出するだけ**で、
  文書の書き換えは実装タスクとして人が行う（差分を目で確認するため）

#### OverheadBench

| Field | Detail |
|---|---|
| Intent | 計測 ON / OFF で処理時間が有意に変わらないことを実測で確認する |
| Requirements | 7.5, 7.6, 7.7, 7.8 |

**Contracts**: Batch [x]

- **Trigger**: CLI `bench-overhead`
- **Input / validation**: 同一入力元・同一設定・同一時間で、条件だけを変えて **A/B/A/B** で回す
- **Output / destination**: `var/m1/overhead-<session>.json` ＋ 判定を `measurements.md` へ
- **判定基準**（**実測前に確定させる**）: **ON 条件の1予測あたり総処理時間の中央値と OFF 条件の中央値の差が、
  OFF 条件の四分位範囲以内**であり、かつ**取りこぼしが増えていない**とき「有意に変化しない」と判定する

**Implementation Notes**

- Integration: 判定基準の形は上流（`sensing-foundation` の `LoggingOverheadBench`、
  `flying-object-tracking` の `bench-overhead`）と**同一の形**にする。同じ問いに違う基準を使わない
- Validation: 偽と判定された場合、**当該条件の計測結果を無条件に有効として扱わない旨を出力に含める**（要件 7.8）
- Risks: 本 Spec の計測対象は `predict` 区間と end-to-end であり、上流の区間とは別である。
  出力に対象区間を明示する

### L8-L9: 出力

#### Reporter

| Field | Detail |
|---|---|
| Intent | 実測・帰属・判断を、人が読む要約と機械可読 JSON の両方で出す |
| Requirements | 5.11, 6.9, 9.4, 10.4, 11.2 |

**Contracts**: Batch [x]

- **Trigger**: CLI `report`
- **Output / destination**: `var/m1/report-<session>.json` ＋ 標準出力の要約 ＋
  結論を `.kiro/specs/m1-prediction-validation/measurements.md` へ
- **要約に必ず含めるもの**:
  - 実測7項目を**対応する `docs/requirements.md` の想定値と並べて**表示する（要件 5.11）
  - 帰属の内訳（合計誤差の単一値にしない。要件 6.9）と、上流の読み分け規則
  - OQ-27 の判定値・規則・改善適用履歴（要件 9.4）
  - OQ-05 が**材料であって決着ではない**旨（要件 10.4）
  - 未検証キャリブレーションで得たデータが含まれる場合の**警告**（要件 2.2）

**Implementation Notes**

- Risks: 数値だけを転記して根拠を落とすと、`structure.md` が最悪と呼ぶ状態になる。
  **判定規則の説明文を要約に必ず含める**

#### Plotter

| Field | Detail |
|---|---|
| Intent | 落下地点・時系列・軌道・収束・帰属を図として出す（開発PC 専用） |
| Requirements | 8.1-8.10 |

**Responsibilities & Constraints**

- **matplotlib を import する唯一のモジュール**である（要件 8.8）
- **アルゴリズムを持たない。** 集計済みの値を描画するだけ（要件 8.10、`tech.md` 開発標準3）
- 依存が無い環境では**この機能だけが利用不可**になり、集計・判断は動く（要件 8.9）

**Contracts**: Batch [x]

| 図 | 内容 | 要件 |
|---|---|---|
| 上面図 | 床平面上の予測落下地点系列・実落下地点・待機位置・許容窓（暫定と明記） | 8.1, 8.2, 8.3 |
| 時系列図 | 予測落下時刻の推移・実落下時刻・残差の推移 | 8.4 |
| 軌道図 | World 座標系の観測点列と推定軌道 | 8.5 |
| 収束図 | サンプル数と誤差 | 8.6 |
| 帰属図 | 誤差ベクトルの散布・共通偏り・カメラ視線方向の重ね描き | 8.7 |

- **Trigger**: CLI `plot`
- **Output / destination**: `var/m1/plots/<record_id>-<kind>.png`
- **Idempotency**: 同一入力に同一の図。対話的表示を行わない

**Implementation Notes**

- Integration: 描画は非対話バックエンドで行い、画面を要求しない
- Validation: 依存が無ければ、**利用不可を報告して終了コードで区別する**（例外で全体を落とさない）
- Risks: 図に凡例と単位と「暫定目標値」の注記が無いと誤読される。**注記を図の一部として描く**

#### CLI

| Field | Detail |
|---|---|
| Intent | 設定をコード外から与え、各機能を実行する入口を提供する |
| Requirements | 12.6, 13.5, 13.6, 13.7 |

| サブコマンド | 役割 | 実機 | 主な要件 |
|---|---|---|---|
| `run-throw` | 1投擲を実行し記録する | 要 | 3.1-3.8 |
| `ingest-truth` | 真値ファイルを取り込み、記録へ対応付ける | 不要 | 4.1, 4.7 |
| `measure` | 実測7項目と段階別レイテンシを算出する | 不要 | 5.1-5.11, 7.1-7.4 |
| `attribute` | 誤差の帰属を判定する | 不要 | 6.1-6.11 |
| `judge-oq27` | Pi 4 継続可否を判定する | 不要 | 9.1-9.11 |
| `material-oq05` | NFR-7 の判断材料を出す | 不要 | 10.1-10.5 |
| `budget` | 時間予算表の更新値を算出する | 不要 | 11.1-11.8 |
| `bench-overhead` | 計測 ON/OFF 比較 | 要（推奨） | 7.5-7.8 |
| `report` | 要約と JSON を出す | 不要 | 5.11, 6.9, 9.4 |
| `plot` | 図を出す | 不要 | 8.1-8.10 |

**Implementation Notes**

- Integration: 設定の解決順序は **CLI 引数 > 環境変数 > 設定ファイル > 既定値**。
  `--print-settings` で解決結果を表示できる（要件 13.5）。
  **各サブコマンドのヘルプに実機の要否を明示する**（要件 12.6）
- **上流由来の2値の調達は `run-throw` / `bench-overhead` の入口が担う**（全層を参照してよい唯一のモジュール）:
  - `Logger` を `UpstreamGateway.get_logger_handle()` から得る
  - 上流の追跡設定を `Seam.resolve_tracking_settings()`（= `flying_object_tracking.TrackingSettings.resolve()`
    への素通し）から得る。上流の署名は `file` / `env` / `overrides` の**3つとも必須**なので、
    `cli.py` が3つとも供給する（`env` / `overrides` を継ぎ目側で空に埋めない。
    埋めると上流の環境変数・上書きが黙って捨てられ、本 CLI が掲げる優先順位と食い違う）。
    **`flying_object_tracking` を import するのは `seam.py` だけ**という
    本 Spec の境界を崩さないため、`cli.py` は上流パッケージを直接 import せず継ぎ目経由で調達する。
    **本 Spec は追跡の設定値・方式を決めない**（OQ-26 は上流の担当）。
    上流の解決結果をそのまま `ThrowRunner.run_throw()` へ渡すだけであり、
    本 Spec の `M1Settings` へ写し取らない（写し取ると方式を決めたことになる）
  - どちらも本 Spec のどの層でも解釈しない不透明値として `Seam.open_tracking()` まで素通しする
- Validation: `--allow-unverified` は明示的に与えたときのみ有効（要件 2.2）
- Risks: 既定値が既成事実化しないよう、`--help` に「暫定の評価候補であり必須性能ではない」と明記する

---

## Data Models

### Throw Record の拡張（`extra["m1"]`）

**スキーマは `prediction_core.ThrowRecord`（`schema_version` 1.0）をそのまま使う。再定義しない**（D-8）。
本 Spec 固有の情報は `extra["m1"]` へ退避する（要件 3.4）。

| キー | 型 | 内容 |
|---|---|---|
| `m1_extra_version` | string | `M1_EXTRA_VERSION`（現行 `"1.0"`） |
| `layout` | object | `ThrowLayout` の要約（`layout_id` と主要値） |
| `calibration` | object | `calibration_id` / `verification_state` / `bias_mm` / `scatter_rms_mm` / `verdict` / `verified_at_wall_ms` |
| `tracking` | object | `handoff_version` / `track_id` / `detector_kind` / `started_t_ms` |
| `provenance` | array | サンプルと同順・同数の観測品質（`SampleProvenance` の直列化） |
| `rejected` | array | 除外理由と件数 |
| `truth` | object \| null | `ThrowTruth` の直列化（後から追記される。要件 4.7） |
| `verified` | bool | 検証済みキャリブレーションで得たか（要件 2.2） |
| `failed_reason` | string \| null | 追跡不成立などの失敗理由（要件 3.8） |

- `sensing_foundation` が使う `extra["sensing"]`（セッション対応付け）と**同居させる。上書きしない**
- **付与手段**: `ThrowPredictionTracker.to_record()` は `extra` を受け取らないため、
  `dataclasses.replace(record, extra={...})` か公開コンストラクタでの直接構築を用いる
  （[ThrowRunner](#throwrunner) の Implementation Notes と同一）
- 読み出し時に `schema_version` と `m1_extra_version` の**両方**の既知性を検査する。
  未知の版を**推測して読まない**

### 真値ファイル（`truth.json`）

投擲の実行とは分離して人が記入する（要件 4.7）。

```json
{
  "truth_format_version": "1.0",
  "layout_id": "L1-2026-09",
  "entries": {
    "throw-0007": {
      "impact_point_world_mm": [1240.0, -310.0, 0.0],
      "impact_point_source": "メジャー実測。原点マーカー中心から床上を計測",
      "impact_point_uncertainty_mm": 15.0,
      "external_release_mark_ms": null,
      "notes": "缶が1回バウンドした。初弾接地位置を記録"
    }
  }
}
```

**不変条件**: `impact_point_source` は空文字列を許さない（要件 4.1）。
記録に存在しない `record_id` は取り込み時に警告として報告し、黙って捨てない。

### 集計・判断の出力（`report-<session>.json`）

`ThrowAggregate` / `AttributionResult` / `Oq27Result` / `Oq05Result` / `BudgetUpdate` を素直に JSON 化する。
**すべての判断は `Judgement`（規則の説明文を含む）を伴う。**
`json.dumps(..., allow_nan=False)` を用い、NaN / Infinity は欠測として表す（上流・`prediction_core` と同方針）。

### 構造化ログ（stage = `predict` / `m1`）

上流の NDJSON へ送る。**形式は上流が正**であり、本 Spec は `stage` と `event` と `data` の中身だけを定める。

| stage | event | 主な `data` |
|---|---|---|
| `m1` | `throw_start` | `record_id`, `layout_id`, `calibration_id`, `verification_state`, `verified` |
| `m1` | `seam` | `appended`, `rejected`, `handoff_version`, 所要時間 |
| `m1` | `throw_end` | `samples`, `first_valid_sample_count`, `failed_reason` |
| `predict` | `update` | `sample_count`, `residual_mm`, `remaining_time_ms`, `elapsed_ms`, `end_to_end_ms` |
| `predict` | `invalid` | `sample_count`, `reason` |

> **予約 stage（`system` / `capture` / `record`）および上流が使う `detect` / `track` / `calibrate` と
> 衝突しない。** 下流が自分の stage を足す前提（`sensing-foundation` 要件 8.9）に沿う。

---

## Error Handling

### Error Strategy

上流3 Spec と `prediction_core` の「**無効は値、例外は呼び出し方の誤り**」を踏襲する。
ただし**継ぎ目の不成立は例外**とする。座標系・形式版・設定が食い違ったまま値が下流へ流れることは、
本 Spec が防ごうとしている事故そのものだからである。

### Error Categories and Responses

| 分類 | 例 | 応答 |
|---|---|---|
| 設定の誤り | 不正な閾値、レイアウト未指定、範囲外の値 | `M1ConfigError`。**起動時**に拒否する |
| 継ぎ目の不成立 | 座標系不一致、未知の形式版、設定不一致、検証未通過 | `SeamFailure`。**投擲を始める前**に評価する |
| 観測の不成立 | 有効サンプル0件、追跡不成立 | **値として扱う**。失敗投擲として理由付きで記録し、集計から除く |
| 真値の欠測 | 落下地点未記入、床面を跨ぐ区間が無い | **値として扱う**。当該項目のみ欠測、他項目は継続 |
| 判断の未成立 | 試行数下限未達、実機由来の投擲なし、改善未適用 | **値として扱う**。`deferred` / `provisional` として返す |
| 可視化の不能 | 描画依存が未導入 | **値として扱う**。可視化のみ利用不可を報告し、集計・判断は継続 |

### Monitoring

- 構造化ログ（stage = `predict` / `m1`）に、投擲の開始・継ぎ目の結果・予測更新・終了を残す
- 集計は**後から**行う。実機上で常時集計しない（`tech.md` 開発標準5）

---

## Testing Strategy

### Unit Tests

- **継ぎ目**: 既知の姿勢で合成した `CameraTrack` を変換し、既知の World 座標と一致すること。
  時刻がそのまま引き継がれること。除外理由ごとの件数が合うこと
- **検証ゲート**: `verification_state` の4値それぞれに対する拒否・許可・印の付き方
- **真値**: 床面を跨ぐ区間の内挿、跨がない場合の欠測、外挿によるリリース時刻と不確かさの併記
- **収束**: 既知の収束列・未収束列に対して、固定した規則どおりの結果を返すこと
- **設定**: 解決順序（CLI > 環境変数 > ファイル > 既定）と、不正値の起動時拒否

### Integration Tests

- **1投擲の実行**: 合成フレーム列 → 追跡ダブル → 継ぎ目 → 逐次予測 → Throw Record の往復。
  `extra["m1"]` が `extra["sensing"]` を壊さないこと
- **集計**: 複数投擲・複数キャリブレーション識別子の混在を、識別子ごとに分けて集計すること
- **レイテンシ**: 未知 stage を含むログを読み、`unknown_stages` として残すこと
- **上流2経路の逆投影一致（クロス Spec 契約）★**: 同一の内部パラメータ・画素座標・生の奥行き値に対し、
  `flying_object_tracking` 側の逆投影経路と `world_frame_calibration` 側の逆投影経路が
  **完全に同一の値**を返すこと（浮動小数の厳密比較。許容差を置かない。
  両者が同じ `sensing_foundation.geometry` の基本演算に乗っている限り、厳密一致するのが正しい）。
  無効な生の奥行き値・境界値（`0` / 画素中心規約の効く半画素ずれ）も含めて比較する。
  **両上流を import する正当な理由を持つのは本 Spec だけであり、ここが唯一の検証場所である**旨と、
  ずれた場合の症状（共通偏りとして現れ「予測が悪い」に潰れる。`docs/requirements.md §6.2`）を
  テストの docstring に書く（要件 1.10）。
  本テストは上流3パッケージの**公開入口だけ**を参照する。
  本体側で `sensing_foundation`（`geometry` を含む）に触れてよいのは**接点モジュール `upstream.py` 1つに限る**、という
  境界規則（[Allowed Dependencies](#allowed-dependencies)）は本テストによって緩まない。
  `seam.py` が受け取る `CameraTrack` は既にカメラ座標系の3D点であり、`seam.py` は `WorldTransform` を
  適用するだけなので、逆投影演算を必要としない
- **帰属 ★**: 既知の偏りを注入した投擲群に対し、
  (a) World 固定の偏り → キャリブレーション由来、
  (b) カメラ視線方向の偏り → 検出由来の候補、
  (c) 観測ノイズのみ → 観測ノイズ由来、
  (d) 投擲位置1箇所で両方向が縮退 → 判別不能、
  をそれぞれ返すこと。**この4分岐が本 Spec の存在意義そのもの**である旨をテストの docstring に書く
- **OQ-27**: GATE 0 / 1 / 2 の各未達で `deferred` になること。
  改善未適用のまま `insufficient` を返さないこと。4値それぞれの分岐

### E2E / CLI Tests

- 合成入力に対し `run-throw` → `ingest-truth` → `measure` → `attribute` → `judge-oq27` →
  `budget` → `report` が通り、実測7項目がすべて算出されること
- 実測値が揃わない状態で `budget` が `ready=False` を返し、**時間予算表の更新値を出さない**こと
- 描画依存が無い環境で `plot` のみが利用不可となり、他のサブコマンドが成功すること

### 境界テスト（回帰）

- `sensing_foundation` を import するのが `upstream.py` だけであること
- `flying_object_tracking` / `world_frame_calibration` を import するのが `seam.py` だけであること
- **評価側（`truth` / `metrics` / `attribution` / `judgement` / `report` / `plot`）が
  上流3パッケージを import しないこと**
- **収集側（`upstream` / `seam` / `runner`）が `plot` を import しないこと**、
  および `plot.py` 以外が `matplotlib` を import しないこと
- `cv2` / `pyrealsense2` / `numpy` を直接 import していないこと
- `[project].dependencies` が空のままであること、および本 Spec が追記した extras 名が
  `tests/prediction_core/test_packaging.py` の許可リスト（`ALLOWED_OPTIONAL_EXTRAS`）に含まれること
  （**同テストは改変しない**。本 Spec 側は自テストで追記内容のみを検証する）
- 変更対象が自パッケージ・自テスト・自 Spec ディレクトリ・`pyproject.toml` の追記・
  `docs/requirements.md` §3 に閉じていること

### 実機テスト（ハード到着後・別タスク群）

- 実投擲での7項目の実測、帰属、段階別レイテンシ、計測 ON/OFF 比較、§13.2 の改善適用と再測定
- 記録した実データを WSL 上で再生し、**実機なしで同じ集計と判断が再現されること**（要件 12.5）

---

## Performance & Scalability

- **計測が計測対象を歪めないこと**が本 Spec の前提である（`tech.md` 開発標準5）。
  収集側は記録に徹し、集計・帰属・判断・可視化を実機上で行わない
- 継ぎ目の処理は1点あたり回転と平行移動の適用のみであり、追跡や検出に比べて無視できる。
  ただし**それを主張せず、`m1` stage の計測値で確認する**
- ブートストラップ（既定200回）は評価側でのみ実行する。**実機の実行経路に入らない**
- ログ・記録は行単位でストリーム処理し、全読み込みを避ける（上流の集計器に従う）

---

## Supporting References

- `docs/requirements.md` — §3（時間予算表 / NFR-3 / NFR-5 / NFR-6 / NFR-7）、§6.1 / §6.2、§8 M1、FR-1 / FR-2
- `docs/development-environment.md` — §12（段階検証）、§13.1（実測項目）、§13.2（改善順序）、§13.3、§16
- `docs/decisions.md` — D-1 / D-2（合否条件の規律）、D-8（Throw Record スキーマ）
- `docs/open-questions.md` — OQ-01 / OQ-02 / OQ-05 / OQ-27 / OQ-39 / OQ-40 / OQ-41
- `.kiro/specs/prediction-core/design.md` / `requirements.md`（D-1: 時間予算表の読み替え）
- `.kiro/specs/sensing-foundation/design.md`（Boundary Commitments / Data Models）
- `.kiro/specs/flying-object-tracking/design.md`（Boundary Commitments / Data Models / L1-L2）
- `.kiro/specs/world-frame-calibration/design.md`（Boundary Commitments / L6-L9）
- 本 Spec の `research.md`（Decision 1〜8、Synthesis Outcomes、Risks）
