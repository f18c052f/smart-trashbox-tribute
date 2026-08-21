# Roadmap

## 現在地（最終更新: 2026-08-21）

> **セッションをまたぐ引き継ぎはこの節を正とする。** 作業が進んだら必ずここを更新する。

| 項目 | 状態 |
|---|---|
| フェーズ | **全7Specの生成が完了した。** `/kiro-spec-batch` で残り6Specを依存ウェーブ順に生成し、クロスSpecレビュー2巡で整合を取った |
| ドキュメント | `docs/` 7ファイル、steering 4ファイル（本ファイル含む）が整備済み |
| Spec | **実装完了は `prediction-core` のみ**（`main` へマージ済み、263テスト全通過）。残り6件は**Spec生成済み・実装未着手** |
| 実機 | **Raspberry Pi 4 / RealSense D435 ともに未セットアップ**（OS 未導入） |
| ブランチ | **`spec/spec-batch` に6Spec分を作成済み**（未マージ）。実装は `main` から `spec/<feature>` を切る |

### 次のアクション

1. **`spec/spec-batch` をレビューして `main` へマージする。**
   6Specは自動承認（`-y` 相当）で生成しているため、`spec.json` の `approved: true` は人間のレビューを経ていない
2. **`/kiro-impl sensing-foundation`** — Wave 0 かつ他3Specの上流。
   ただしタスク群9は実機必須のため、実機セットアップと並行して進める
3. 並行して**実機セットアップ**（`development-environment.md §16` の手順。#3〜#6 を fps 計測より先に）

### 着手順序の制約（Spec生成で判明したもの）

- **`sensing-foundation` タスク1.1 を最初に着地させる。** `tests/prediction_core/test_packaging.py` の
  不変条件を extras 許可リスト方式へ改訂するタスクであり、これが無いまま他Specが
  `[project.optional-dependencies]` を追加すると**マージ済みの prediction-core のテストが赤くなる**。
  これは「`prediction_core` のツリーに触れない」原則に対する**唯一の認可された例外**である
- **`sensing-foundation` タスク1.8（`geometry.py`）が `world-frame-calibration` と
  `flying-object-tracking` の前提になる。** ピンホール逆投影は上流の単独所有とし、
  下流2Specは自前実装を持たない。二重実装は `requirements.md §6.2` が警告する
  「座標系ずれが『予測が悪い』にしか見えない」事態を招くため
- `trajectory-simulator` と `simulator-visualization` は**ハード不要**で今すぐ着手できる

### `prediction-core` 実装完了の要点（引き継ぎ用）

- 公開API（`prediction_core.__init__`）は18シンボルに確定済み: `Sample`/`SourceKind`/`PredictionConfig`/`TrajectoryParameters`/`Prediction`/`InvalidPrediction`/`InvalidReason`/`PredictionOutcome`/`predict`/`ThrowPredictionTracker`/`ThrowRecord`/`SCHEMA_VERSION`/`replay`/`predictions_equivalent`/例外4種。下流Specはこの入口から参照する
- `ThrowRecord`（`schema_version` 1.0）は `to_dict`/`from_dict`/`to_json`/`from_json`/`replay`/`predictions_equivalent` まで実装・テスト済み
- 実行時のサードパーティ依存は引き続きゼロ（`test_boundaries.py` が静的に回帰検証する）

### `prediction-core` の設計上の要点（引き継ぎ用）

- **実行時のサードパーティ依存ゼロ。** 最小二乗は閉形式で解けるため NumPy を採用していない。
  理由は速度ではなく、BLAS 実装差による非決定性が Replay 再現（要件 9.4）を弱めるため
- **Throw Record は `schema_version` 1.0。** 下流はこれに従い、独自スキーマを定義しない
- **依存方向**: units/errors → types → config → fitting・impact → predictor → **record** → tracker → `__init__`。
  `record` を `tracker` より下層に置くのは、スキーマ単体を import できるようにするため
- 未決のまま残しているもの: **OQ-40**（全体のディレクトリ構成。本 Spec は `src/prediction_core/` のみ確定）、
  **OQ-41**（Python の環境構築・パッケージ管理。第一候補は uv だが実機まで確定させない）

> ⚠️ **`docs/requirements.md §3` の時間予算表はまだ更新していない。**
> 逐次予測（初回予測 → 駆動開始 → 以降更新）を前提に読み替える必要があるが、
> **M1 の実測値と合わせて `m1-prediction-validation` で更新する**（`prediction-core/requirements.md` D-1）。

### ブランチ運用

- **トランクは `main`。** 新しい作業は main から `spec/<feature>` 形式で切る
- Spec 単位で並列実装する場合は `git worktree` を使う

  ```
  git worktree add ../stb-<feature> -b spec/<feature>
  ```

- ただし**実機を要する Spec はハード待ちで並列化できない**。
  現時点でハード不要なのは `prediction-core` と `trajectory-simulator` のみ
- **Spec 生成（`/kiro-spec-batch`）に worktree は不要**。1ツリー内でサブエージェントが並列化する

### 引き継ぎ時の読み込み順

1. 本ファイルのこの節（現在地）
2. `.kiro/steering/product.md` / `tech.md` / `structure.md`（自動で読み込まれる）
3. 着手する Spec の `.kiro/specs/<name>/brief.md`
4. 必要に応じて `docs/open-questions.md`（未決事項の唯一の正）

**`docs/` 全文を毎回読まない。** 必要になった箇所だけ参照する。

---

## Overview

**M1「予測の可視化」と、柱1「軌道シミュレータ」を2トラック並行で進める。**

M1 は「飛来するゴミを検出・追跡し、落下地点を予測してプロットする」段階であり、移動体は登場しない。
実機（Raspberry Pi 4 / RealSense D435）が未セットアップのため、ハード待ちが発生する。
その間に着手できるハード不要の作業として、柱1 シミュレータを並行トラックに置く。

両トラックは **`prediction-core`（予測アルゴリズム）を共有する。**
これは `original-features.md` の原則「予測は本番と同一コードを使う。ブラウザ側に複製しない」を
構造として担保するためであり、**この共有こそが2トラックを1つの roadmap に置く理由**である。

## Approach Decision

- **Chosen**: 予測コアを最初に独立して切り出し、そこへ「合成データ（シミュレータ）」と
  「実データ（M1）」の2系統を接続する構成
- **Why**:
  - 予測コアはハード不要で**今すぐ着手できる**唯一の中核部品
  - `development-environment.md §7` の「入力元が live / recorded / simulated のどれでも下流は同じ」を
    最初から満たせる
  - M1 がシミュレータ全体の完成を待たずに済む
- **Rejected alternatives**:
  - **M1 を単一 Spec にする**: OS 選定・カメラ導通・キャリブレーション数学・検出・追跡・
    フィッティング・ロギング・可視化を1本にすると 30 タスク超。
    さらに「予測が外れたとき座標系ずれか検出誤差か予測誤差かを分離できない」という
    `requirements.md §6.2` が警告する事態を、Spec 境界の面でも招く
  - **柱1 を後回しにする**: 実機未セットアップの期間が完全な待ち時間になる。
    また予測コアの設計が M1 側の都合だけで決まり、シミュレータ接続時に作り直しになる
  - **予測コアをシミュレータに内包する**（docs の着手順序どおり）: M1 がシミュレータ完成に依存する。
    共有部品であることが構造に現れない

## Scope

- **In**: M1（検出 → 追跡 → 予測 → 可視化 → 時間予算の実測）、柱1（シミュレータ）、柱2a（構造化ロギング）
- **Out**:
  - 移動体側のすべて（M2a / M2b / M3 / M4、テレオペ、ESP32 ファームウェア）
  - 柱2b ライブダッシュボード（→ OQ-38、M3 着手時に再判断）
  - 柱3 投擲アーカイブ／ベンチマーク（→ OQ-39、M1 でデータが貯まってから再判断）

## Constraints

- **対象物は空き缶に固定**（φ65mm 程度の剛体）。M1 の実験条件を確定させるための決定
- 実機は**未セットアップ**。OS 選定・USB3 認識・給電安定性から始める必要がある
- 固定側は Python。可視化のみ TypeScript で、**アルゴリズムを持たせない**
- Raspberry Pi 4 の性能達成を断定しない。**設定・ソフトで詰めてからハード変更を検討する**
- 数値は暫定目標値として扱い、**未実測の値を合否条件にしない**

## Boundary Strategy

- **Why this split**:
  - `development-environment.md §12` の段階検証（取得 → 記録 → 検出 → 3D位置 → 追跡 → 予測）が
    そのまま責務の切れ目になっている
  - **キャリブレーションを独立させる**ことが最重要。`requirements.md §6.2` は
    「座標系が数 cm ずれていても症状は『予測が悪い』にしか見えない」と警告している。
    検証ステップを持つ独立 Spec にしないと、M1 の誤差要因を分離できない
  - 検出（`flying-object-tracking`）とキャリブレーション（`world-frame-calibration`）は
    **並行可能**。検出はカメラ座標系まで、World への変換はキャリブレーション側が持つ
- **Shared seams to watch**:
  - **`prediction-core` の入力契約**: `(t, x, y, z)` サンプル列。
    ここに RealSense 固有の型を漏らすと simulated 入力が繋がらなくなる
  - **Throw Record スキーマ**（OQ-31）: `prediction-core` が最小形を定義し、
    `sensing-foundation` の記録形式（OQ-32）と `m1-prediction-validation` の計測が従う。
    **別々に決めない**
  - **構造化ロギングの計測点**: `sensing-foundation` が基盤を作るが、
    段階別レイテンシ（`development-environment.md §13.1`）は各 Spec が自分の区間を計測する

## Specs (dependency order)

- [x] prediction-core -- 放物運動フィッティングによる落下地点・時刻・残差の算出。Throw Record 最小スキーマ。ハード不要。Dependencies: none
- [x] sensing-foundation -- Pi 4 / RealSense セットアップ、安定取得、実データ記録、入力層抽象、構造化ロギング基盤。Dependencies: none
- [x] trajectory-simulator -- 投擲物理・ノイズ・遅延・移動体運動モデルとパラメータ掃引によるキャッチ可能領域の算出。Dependencies: prediction-core
- [x] world-frame-calibration -- 床平面推定、World frame の確立、既知位置との照合による検証ステップ。Dependencies: sensing-foundation
- [x] flying-object-tracking -- 飛翔物の検出、3D位置取得、フレーム間追跡。Dependencies: sensing-foundation
- [x] m1-prediction-validation -- 実データを prediction-core へ接続し、落下地点をプロット。時間予算7項目を実測して M1 完了判定。Dependencies: prediction-core, world-frame-calibration, flying-object-tracking
- [x] simulator-visualization -- ブラウザでの軌跡アニメーションとキャッチ可能領域の表示。**先送り可**。Dependencies: trajectory-simulator

> **`[x]` は Spec が生成済み（requirements / design / tasks の3フェーズ完了）であることを示す。実装完了ではない。**
> 実装が完了しているのは `prediction-core` のみ。

### 着手ウェーブ

| Wave | Spec | 備考 |
|---|---|---|
| 0 | `prediction-core` / `sensing-foundation` | **並行可**。前者はハード不要で今すぐ、後者は実機セットアップから |
| 1 | `trajectory-simulator` / `world-frame-calibration` / `flying-object-tracking` | 3本とも並行可 |
| 2 | `m1-prediction-validation` | M1 の完了判定と OQ-27（Pi 4 継続可否）の判断 |
| — | `simulator-visualization` | **急がない。** `original-features.md` が「可視化に時間をかけすぎて結論が出ない失敗」を警告している |

## 決着させる未決事項の対応

| Spec | 決着させる OQ |
|---|---|
| `prediction-core` | OQ-31（Throw Record 最小スキーマ） |
| `sensing-foundation` | OQ-23 / 24 / 25 / 28（OS・RAM・解像度fps・セットアップ成立性）、OQ-32（Record/Replay 形式）、OQ-35（ログ形式） |
| `trajectory-simulator` | OQ-33（物理モデルの詳細度）、OQ-01 の机上検討 |
| `world-frame-calibration` | **OQ-03**（World frame とキャリブレーション手順）★ |
| `flying-object-tracking` | OQ-26（物体検出方式） |
| `m1-prediction-validation` | OQ-27（Pi 4 継続可否）★、OQ-05 の判断材料 |
| `simulator-visualization` | OQ-34（Canvas / SVG / WebGL） |
