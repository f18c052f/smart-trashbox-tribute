# Roadmap

## 現在地（最終更新: 2026-08-23）

> **セッションをまたぐ引き継ぎはこの節を正とする。** 作業が進んだら必ずここを更新する。

| 項目 | 状態 |
|---|---|
| フェーズ | **固定側7Specの生成が完了し、ハード非依存の実装はほぼ枯れた。** 残るのは実機必須タスクと未着手2Spec |
| ドキュメント | `docs/` 7ファイル、steering 4ファイル（本ファイル含む）が整備済み |
| Spec | 下表「Spec 実装状況」を正とする |
| 実機（固定側） | **Raspberry Pi 4 / RealSense D435 ともに未セットアップ**（OS 未導入） |
| 実機（移動体） | **ESP32 DevKit と JGB37-520 モータは手元にある。Nexus 14145 ホイール / 18020 ハブは未着**（→ 機構の現物採寸は部分的にしか進められない） |
| ブランチ | **`spec/*` ブランチはすべて `main` へマージ済み**。現在は `spec/hardware`（`main` と差分なし）。新規作業は `main` から `spec/<feature>` を切る |

### Spec 実装状況

| Spec | 状態 | 残り |
|---|---|---|
| `prediction-core` | **完了** | — |
| `trajectory-simulator` | **完了** | — |
| `sensing-foundation` | セクション1〜8 完了 | **セクション9（実機ブリングアップ）7サブタスク** |
| `world-frame-calibration` | セクション1〜7 完了 | **セクション8（実機での確立と検証）3サブタスク** |
| `flying-object-tracking` | セクション1〜8 完了 | **セクション9（実機実測と OQ-26 確定）5サブタスク** |
| `m1-prediction-validation` | 未着手 | 全47項目 |
| `simulator-visualization` | 未着手 | 全19項目（**急がない**） |

> **重要な帰結**: 固定側で「実機なしに進められる作業」は `simulator-visualization` を除いてほぼ残っていない。
> 上3Specの残タスクはすべて Pi 4 / D435 のセットアップを前提とし、`m1-prediction-validation` は
> その3Specの実機タスク完了を前提とする。**ここが駆動系トラックを並行で立ち上げる理由である。**

### 次のアクション

1. **実機セットアップ（固定側）** — `development-environment.md §16` の手順。
   これが `sensing-foundation` 9 → `world-frame-calibration` 8 → `flying-object-tracking` 9 →
   `m1-prediction-validation` の全体を開錠する唯一の鍵
2. **`/kiro-spec-init drivetrain-core`** — 駆動系トラックの1本目。**ハード不要で今すぐ着手できる。**
   discovery は 2026-08-23 に完了し、3Spec の brief.md を書き出し済み（下記「駆動系トラック」）
3. `simulator-visualization` はハード不要だが**急がない**（下記 Specs 一覧の注記どおり）

### 駆動系トラックの起点（2026-08-23 追加）

固定側がハード待ちに入ったため、**移動体側を本ファイルの Scope へ追加した**（従来は Out）。

着手方針:

- **1本目はハード非依存の駆動コア**とする。`drivetrain-spec.md §10.1.2` の層構造がそのまま境界になる。
  上段（パッド入力 → `(vx, vy, ω)`）は使い捨てだが、下段（逆運動学・速度PID・PWM出力・保護①〜④）は
  M2b・M3 と完全に共有される
- 根拠: `drivetrain-spec.md §10` が **「M2a（初通電走行）より前に ①モータロック保護 ②LiPo低電圧保護
  ③PWM上限 ④指令ウォッチドッグ を実装する」**と明記している。
  **実機に通電する前に書き終えていなければならないコードが、既に仕様として確定している**
- `prediction-core` と同じ作り方ができる。逆運動学も保護の状態機械も純関数・状態機械に落ちるため、
  ペリフェラル（PCNT / LEDC / BT）を薄いアダプタへ隔離すればホスト側で単体テストできる
- **見張るべき seam**: `trajectory_sim.drivetrain` が既に「等方な質点＋加速度上限」の運動モデルを持つ。
  M2b の実測値を `DrivetrainParams` へ戻す口を最初に決めておかないと、
  シミュレータのキャッチ可能領域を実機由来の数字で更新できなくなる。**後付けでは繋がらない**
- **部品制約**: ホイール・ハブが未着のため、`drivetrain-spec.md §11` の現物採寸のうち
  #1（付属ブラケット実寸）・#2（取付穴ピッチ）は着手できるが、
  #3（最低地上高）・OQ-07（フレーム外形＝ホイール間隔）・整備スタンドの支持形状は**ホイール到着待ち**

#### ファームウェア構成の決定（2026-08-23 の技術検証で確定）

⚠️ **これらは調査で判明した罠に基づく決定である。根拠ごと残す。**

| 項目 | 決定 | 理由 |
|---|---|---|
| フレームワーク | **`framework = espidf` + Arduino-as-component を両ビルドで使う** | Bluepad32 は `framework = arduino` の `lib_deps` として導入できず、改造コアとして組み込まれる。片方だけ espidf にすると `main/` と `src/` でレイアウトが割れる |
| PlatformIO プラットフォーム | **`pioarduino` フォークをリリース zip で pin** | 公式 `platformio/platform-espressif32` は 7.0.1（2024年5月）で凍結され Arduino 3.x 非対応。⚠️ pioarduino は実質1人規模のため `stable` / `#develop` を使わない |
| 出発点 | `esp-idf-arduino-bluepad32-template` | 公式ルート。IDF 5.4.x 系 |
| パーティション | テレオペビルドは `huge_app`（3MB / OTA 無し） | Arduino core + BTstack BR/EDR HID host で約 1.0〜1.4MB。既定の 4MB スキーム（1.31MB × 2）では溢れる |
| MCU | **classic ESP32 に固定。S3 / C3 / C6 / H2 は不可** | DualSense は BR/EDR のみで BLE 非対応。PCNT ユニット数も classic 8 / S3 2 / C3 0。**将来 S3 へ乗り換える選択肢は無い**（`bom.md` #8 は型番未指定 → 明記が要る） |
| エンコーダ | **`driver/pulse_cnt.h`（IDF 5.x 新 API）で自前アダプタを書く** | `ESP32Encoder` は非推奨 legacy API 上。方向反転時のパルス落ちも報告あり。自前化はアダプタ隔離方針とも一致 |
| ホストテスト | **時刻源を注入する。固定幅整数型（`int32_t`/`int64_t`）を初日から使う** | ホストに `millis()`/`micros()` は無い。ホストは 64-bit で `long` が 8 バイト、ESP32 は 4 バイト。⚠️ **カウンタと PID 積分項のオーバーフロー挙動がホストテストで再現されない** |
| テスト配置 | 純ロジックは `lib/control/`。`test/` は `test_native/` と `test_embedded/` に分け `test_filter` で振り分ける | `test_build_src` は既定 `no` でテストから `src_dir` が見えない。`test/` は全環境向けにビルドされる |

**ライセンス**: Bluepad32 本体は Apache-2.0 だが、依存する **BTstack はオープンソースではない**
（「個人的利益のためのみ。商用目的・金銭的利得のためには使用不可」条項付き。商用は BlueKitchen 社の有償ライセンス）。
趣味プロジェクトとしての使用は問題ないが、**GitHub 公開時にトップレベル LICENSE が
BT 版ファームまで自由に再利用可能だと誤読させてはいけない。**
本番ファームは BT を落とすため、**encumbrance をテレオペビルドに封じ込めれば本番成果物はクリーンに保てる。**
これは「テレオペFWと本番FWを排他にする」既存の決定に、2.4GHz 共存とは**別の根拠を追加する**。

**実装時に踏みやすい罠**（Spec 設計時に必ず拾う）:

- ⚠️ **PCNT のハードウェアカウンタは符号付き 16bit（±32767）。**
  watch point ＋ 64bit ソフトウェアアキュムレータで桁上げを累積しないと、
  **「数秒走るとオドメトリが壊れる」形で現れる**
- ⚠️ **ADC1 を使う**（ADC2 は Wi-Fi 動作中に使用不可 = 本番ビルドで効く）。
  ESP32 の生 ADC は両端で非線形なので**カーブフィッティング補正を入れる** — LiPo カットオフ精度に直結
- DualSense のマルチペアモードで無言のペアリング失敗が既知。レガシー手順（Create/Share + PS 長押し）を使う。
  リンクキーの残留が再接続失敗の頻出原因なので、**「Bluetooth キー消去」操作をファームに用意する**
- DevKit の電波は弱く数 m 程度。**リンク断での停止を前提に置く**（④ が必要な理由がもう一つ増える）
- GPIO 34〜39 は入力専用で内部プルアップが無い

> ✅ 調査が指摘した「Windows に MSYS2 + MinGW が必要」は**本プロジェクトには該当しない**。
> 開発は WSL2 側で行うため gcc は標準で使える。

### 着手順序の制約（Spec生成で判明したもの）

- **`sensing-foundation` タスク1.1 を最初に着地させる。** `tests/prediction_core/test_packaging.py` の
  不変条件を extras 許可リスト方式へ改訂するタスクであり、これが無いまま他Specが
  `[project.optional-dependencies]` を追加すると**マージ済みの prediction-core のテストが赤くなる**。
  これは「`prediction_core` のツリーに触れない」原則に対する**唯一の認可された例外**である
- **`sensing-foundation` タスク1.8（`geometry.py`）が `world-frame-calibration` と
  `flying-object-tracking` の前提になる。** ピンホール逆投影は上流の単独所有とし、
  下流2Specは自前実装を持たない。二重実装は `requirements.md §6.2` が警告する
  「座標系ずれが『予測が悪い』にしか見えない」事態を招くため
- `simulator-visualization` は**ハード不要**で今すぐ着手できる（`trajectory-simulator` 完了により前提が揃った）

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

### `trajectory-simulator` 実装完了の要点（引き継ぎ用）

- 全22サブタスク（5セクション）完了・`/kiro-validate-impl` GO判定済み・**`main` へマージ済み**
- 公開API（`trajectory_sim.__init__`）は33シンボルに確定済み: パラメータ型・掃引型・結果型・`run_sweep`/`write_sweep_result`/`evaluate_throw`/`evaluate_reachability`/`OUTPUT_SCHEMA_VERSION`・例外階層。**`prediction_core` のシンボルは一切再エクスポートしない**（上流依存を隠さないため）
- OQ-33（物理モデルの詳細度）を決着済み → [decisions.md D-9](../../docs/decisions.md#d-9-シミュレータの物理モデルの詳細度を最小限で決着した-oq-33-決着)。`trajectory_sim.MODEL_EXCLUSIONS` が決着内容そのもの
- CLI: `python -m trajectory_sim --config <path> --output <path> [--drivetrain <path>]`。設定JSONの形式（`{"parameters":..., "sweep":...}` の2キー、あらゆるネスト階層で未知キー拒否）は design.md 未記載だったため実装時に確定（`tasks.md` Implementation Notes 参照）
- 実行可能な設定ファイル4本を `configs/trajectory_sim/` に同梱（60mm/48mmホイール×到達可否掃引/レイアウト掃引の全組み合わせで動作確認済み）
- 実行時のサードパーティ依存は引き続きゼロ（`test_trajectory_sim_boundaries.py` が静的に回帰検証する）
- **design.md に2件の記載不備を発見・`tasks.md` Implementation Notes に記録済み（design.md 本体は未修正）**: (1) 依存方向表・Mermaid図が `prediction_link → drivetrain` の辺を欠いている（Service Interface とは矛盾。実装済みコードが正しく、境界検査側の許可リストで実態に合わせた）、(2) CLI設定JSONのスキーマが未記載だった（上記の通り実装時に確定）

### ブランチ運用

- **トランクは `main`。** 新しい作業は main から `spec/<feature>` 形式で切る
- Spec 単位で並列実装する場合は `git worktree` を使う

  ```
  git worktree add ../stb-<feature> -b spec/<feature>
  ```

- ただし**実機を要する Spec はハード待ちで並列化できない**。
  固定側でハード不要なのは `simulator-visualization`（未着手）のみ。
  **駆動系トラックの1本目（ハード非依存の駆動コア）も並列化できる**
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

**2026-08-23 に第3のトラックとして駆動系（M2a / M2b）を追加した。**
固定側のハード非依存な実装が枯れ、残りが Pi 4 / D435 のセットアップ待ちに一本化されたためである。
駆動系トラックも同じ形の共有を持つ: **`trajectory_sim.drivetrain` の運動モデルと実機の駆動制御は、
`DrivetrainParams`（最高速度・加速度上限・減速度上限）を介して繋がる。**
M2b の実測値をここへ戻すことで、シミュレータのキャッチ可能領域が机上値から実機由来の値へ置き換わる。

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

> **2026-08-23 更新**: 固定側のハード非依存な実装がほぼ枯れたため、
> **移動体側（M2a / M2b、テレオペ、ESP32 ファームウェア）を Out から In へ移した。**
> 従来「移動体側のすべて」を Out としていたのは、固定側にハード不要の作業が潤沢に残っていた間の判断である。
> M3 / M4（結合と最終チューニング）は引き続き Out とし、**駆動系が M2b まで到達してから再判断する。**

- **In**:
  - M1（検出 → 追跡 → 予測 → 可視化 → 時間予算の実測）、柱1（シミュレータ）、柱2a（構造化ロギング）
  - **駆動系トラック: M2a（手動テレオペによるブリングアップ）・M2b（短時間応答の自動計測）、柱4（手動テレオペ）**
- **Out**:
  - **M3（予測→通信→走行の結合）／ M4（高速化・復帰動作）** — 通信方式（OQ-29）と
    物理的な非常停止手段（OQ-13）が未決であり、いずれも M2b の実測を待って決める
  - 柱2b ライブダッシュボード（→ OQ-38、M3 着手時に再判断）
  - 柱3 投擲アーカイブ／ベンチマーク（→ OQ-39、M1 でデータが貯まってから再判断）

## Constraints

- **対象物は空き缶に固定**（φ65mm 程度の剛体）。M1 の実験条件を確定させるための決定
- 実機は**未セットアップ**。OS 選定・USB3 認識・給電安定性から始める必要がある
- 固定側は Python。可視化のみ TypeScript で、**アルゴリズムを持たせない**
- Raspberry Pi 4 の性能達成を断定しない。**設定・ソフトで詰めてからハード変更を検討する**
- 数値は暫定目標値として扱い、**未実測の値を合否条件にしない**
- 移動体は ESP32。**駆動コアはホストで単体テストできる形に保ち、ペリフェラル（PCNT / LEDC / BT）は
  薄いアダプタへ隔離する。** `prediction-core` と同じ「実機なしで検証できる中核」の作り方を踏襲する
- **駆動系の部品は全部は揃っていない**（ESP32・モータは手元、ホイール／ハブは未着）。
  機構の現物採寸に依存する作業を、ファーム側の作業のクリティカルパスに置かない
- **M2a（初通電走行）より前に保護①〜④を実装する**（`drivetrain-spec.md §10`）。
  この順序は安全要件であり、スケジュール都合で入れ替えない

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
  - **`DrivetrainParams` の意味論**（駆動系トラック）: `trajectory_sim` の
    最高速度・加速度上限・減速度上限・制御周期・指令反映遅れと、`drivetrain-core` が扱う量の
    **単位と定義を揃える**。`m2-motion-validation` が実測値をここへ翻訳して戻すため、
    ずれると**シミュレータの結論が静かに間違う**。
    ⚠️ ただし**コードは共有しない** — シミュレータの質点モデルと実機の逆運動学は別物であり、
    `tech.md` 標準3 が禁じる「本番アルゴリズムの二重実装」には当たらない
  - **ウォッチドッグの入力元**（駆動系トラック）: ④ は「コントローラ固有の処理」ではなく
    **「最後に有効な指令を受けてからの経過時間」だけを見る形**で実装する。
    FR-10 は M3 でこの入力元を差し替えるだけにする。**新規実装にしない**

## Specs (dependency order)

- [x] prediction-core -- 放物運動フィッティングによる落下地点・時刻・残差の算出。Throw Record 最小スキーマ。ハード不要。Dependencies: none
- [x] sensing-foundation -- Pi 4 / RealSense セットアップ、安定取得、実データ記録、入力層抽象、構造化ロギング基盤。Dependencies: none
- [x] trajectory-simulator -- 投擲物理・ノイズ・遅延・移動体運動モデルとパラメータ掃引によるキャッチ可能領域の算出。Dependencies: prediction-core
- [x] world-frame-calibration -- 床平面推定、World frame の確立、既知位置との照合による検証ステップ。Dependencies: sensing-foundation
- [x] flying-object-tracking -- 飛翔物の検出、3D位置取得、フレーム間追跡。Dependencies: sensing-foundation
- [x] m1-prediction-validation -- 実データを prediction-core へ接続し、落下地点をプロット。時間予算7項目を実測して M1 完了判定。Dependencies: prediction-core, world-frame-calibration, flying-object-tracking
- [x] simulator-visualization -- ブラウザでの軌跡アニメーションとキャッチ可能領域の表示。**先送り可**。Dependencies: trajectory-simulator
- [ ] drivetrain-core -- 3輪オムニ逆運動学・速度PID・オドメトリ・保護①〜④の判定ロジック。ペリフェラルはポート宣言のみ。ホストで単体テストする。**ハード不要**。Dependencies: none
- [ ] teleop-bringup -- ESP32 ペリフェラル実装（PCNT/LEDC/ADC1）、DualSense 直結、M2a-0/1/2 の実施、エンコーダ校正、安全機能4種の発火試験。Dependencies: drivetrain-core
- [ ] m2-motion-validation -- スクリプト化指令による短時間応答の自動計測（M2b 記録項目14件）、NFR-1 の評価、実測値の DrivetrainParams への還元。Dependencies: drivetrain-core, teleop-bringup

> **`[x]` は Spec が生成済み（requirements / design / tasks の3フェーズ完了）であることを示す。実装完了ではない。**
> 実装状況は上部「Spec 実装状況」の表を正とする。
> **`[ ]` の3本は 2026-08-23 の discovery で決めた駆動系トラックであり、brief.md のみ存在する。**

### 着手ウェーブ

| Wave | Spec | 備考 |
|---|---|---|
| 0 | `prediction-core` / `sensing-foundation` | **並行可**。前者はハード不要で今すぐ、後者は実機セットアップから |
| 1 | `trajectory-simulator` / `world-frame-calibration` / `flying-object-tracking` | 3本とも並行可 |
| 2 | `m1-prediction-validation` | M1 の完了判定と OQ-27（Pi 4 継続可否）の判断 |
| — | `simulator-visualization` | **急がない。** `original-features.md` が「可視化に時間をかけすぎて結論が出ない失敗」を警告している |

### 着手ウェーブ（駆動系トラック / 2026-08-23 追加）

固定側とは**独立に進む**。両者を繋ぐのは M3 だが、M3 は現時点で Scope の Out。

| Wave | Spec | 備考 |
|---|---|---|
| 0 | `drivetrain-core` | **ハード不要。今すぐ着手できる**。固定側の実機セットアップと完全に並行可 |
| 1 | `teleop-bringup` | 実機必須。**ホイール／ハブ未着のため部分的にしか進められない**（下記） |
| 2 | `m2-motion-validation` | 実機必須。`teleop-bringup` の完了（特にエンコーダ校正）が前提 |

**`teleop-bringup` の段階的着手**（部品が揃うのを待たずに削れる不確実性）:

| 必要な部品 | できること |
|---|---|
| ESP32 のみ | **Bluepad32 / DualSense の導通・ペアリング挙動（OQ-16）**。⚠️ 最大の未検証項目で、これ単体で潰せる |
| ＋ モータ・ドライバ | PCNT アダプタ、エンコーダ A/B 符号、回転方向の確認 |
| ＋ ホイール・ハブ | 整備スタンドの支持形状、M2a-1 以降の接地走行すべて |

> ⚠️ **モータドライバ AE-TB67H450 が手元にあるかは未確認。** `teleop-bringup` 着手前に確認する。

## 決着させる未決事項の対応

| Spec | 決着させる OQ |
|---|---|
| `prediction-core` | OQ-31（Throw Record 最小スキーマ） |
| `sensing-foundation` | OQ-23 / 24 / 25 / 28（OS・RAM・解像度fps・セットアップ成立性）、OQ-32（Record/Replay 形式）、OQ-35（ログ形式） |
| `trajectory-simulator` | ~~OQ-33~~（物理モデルの詳細度、**決着済み → decisions.md D-9**）、OQ-01 の机上検討 |
| `world-frame-calibration` | **OQ-03**（World frame とキャリブレーション手順）★ |
| `flying-object-tracking` | OQ-26（物体検出方式） |
| `m1-prediction-validation` | OQ-27（Pi 4 継続可否）★、OQ-05 の判断材料 |
| `simulator-visualization` | OQ-34（Canvas / SVG / WebGL） |
