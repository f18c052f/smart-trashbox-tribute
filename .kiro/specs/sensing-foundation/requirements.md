# Requirements Document

## Project Description (Input)

Raspberry Pi 4 上で RealSense D435 から**安定してフレームを取得し、実データを記録し、
WSL 上で繰り返し再生できる入力基盤**と、各処理段階の時刻・FPS を残す**構造化ロギング基盤**を構築する。
詳細な背景・スコープ・制約は [brief.md](./brief.md) を正とする。

### 誰の問題か

M1 のすべての処理が「Pi 4 上で D435 から安定してフレームが取れる」ことに乗っている。

- **`world-frame-calibration`**: 床平面推定に Depth フレームを必要とする
- **`flying-object-tracking`**: 検出・追跡の入力としてフレーム系列を必要とし、
  アルゴリズム比較のために**同一データの繰り返し再生**を必要とする
- **`m1-prediction-validation`**: 段階別レイテンシ（`development-environment.md §13.1`）を必要とする。
  これが取れなければ **OQ-27（Pi 4 を継続するか）の判断そのものが下せない**

### 現状

- **実機は未セットアップである。** Pi 4 に OS が入っておらず、
  D435 の認識・USB3 接続・給電安定性はいずれも未確認（→ OQ-23 / OQ-24 / OQ-28）
- 解像度・fps の初期評価候補は 640×480 / 30fps だが、
  **必須性能でも達成済み性能でもない**（→ OQ-25）
- Record / Replay は `development-environment.md §6` が「重要な開発方針」と明記しているが、
  **保存形式が未定**（→ OQ-32）
- ログの保存形式も未定（→ OQ-35）
- **Throw Record の最小スキーマは `prediction-core` が確定済み**
  （`prediction_core.ThrowRecord`、`SCHEMA_VERSION` 1.0、→ `docs/decisions.md` D-8）
- `docs/development-environment.md §13.1` は「測る」と決めているだけで、
  **収集手段が長らく未定義**だった（`original-features.md` 柱2a が指摘）

### 何が変わるべきか

- Pi 4 上で D435 から**フレーム落ちを把握しながら**安定取得できる
- 投擲の実データを**記録**でき、WSL へ持ち帰って**繰り返し Replay** できる
- 下流（検出・追跡・予測）が **live / recorded / simulated を区別せず**扱える
- 各段階の時刻・FPS を**構造化ログとして残せ**、後から集計できる
- **計測 ON / OFF で end-to-end latency が有意に変化しない**ことを実測で確認済みである

---

## 追加入力: 確定済みの方針（A-1〜A-12）

> 本節は Project Description の続きであり、**入力として与えられた確定方針**である。
> 検証可能な形式の要件は下の [`## Requirements`](#requirements) を正とし、
> `A-n` は各要件の**出典参照**として使用する。

### A-1. ブリングアップの順序を守る

実機セットアップは `docs/development-environment.md §16` の #1〜#8 の順で行う。

| # | 確認事項 | 関連 OQ |
|---|---|---|
| 1 | Pi 4 の RAM 容量 | OQ-24 |
| 2 | OS の最終選定 | OQ-23 |
| 3 | D435 の認識 | OQ-28 |
| 4 | USB3 接続 | OQ-28 |
| 5 | 給電安定性 | OQ-28 |
| 6 | librealsense / pyrealsense2 導入 | OQ-28 |
| 7 | 解像度・fps の動作確認 | OQ-25 |
| 8 | CPU / メモリ / dropped frame / latency | OQ-27 の材料 |

> ⚠️ **#3〜#6 を #7 より先に行う。** RealSense は USB3 帯域と給電の影響を受けやすく、
> ここが不安定なまま fps を測ると**「Pi 4 の性能不足」と誤診する。**

### A-2. OS 選定の判断規則（OQ-23 / OQ-24）

要求は **64bit**・**headless 運用可能**・**RealSense 利用可能**・**Python / OpenCV 利用可能**。

OQ-23 の調査結果により、**どちらの候補でも librealsense のソースビルドが必要**であり
（pyrealsense2 の公式 pip wheel は x86/x64 のみ。aarch64 版が無い）、
`-DFORCE_RSUSB_BACKEND=ON` によりカーネルパッチが不要になるため、
**Ubuntu 有利の最大の論点だった「Pi OS はカーネルパッチが当たらない」が消えている。**

したがって判断規則は次のとおりとする。

1. **Raspberry Pi OS 64-bit を先に試す**（Pi ハード層の未知数が少ないため）
2. librealsense のビルドまたは動作が成立しない場合に **Ubuntu 24.04 LTS arm64 へ退避する**
3. どちらを採ったかと**その根拠を記録する**

RAM 容量（OQ-24）は実機で確認し、記録する。

### A-3. 古いフレームを溜めない

`docs/development-environment.md §4` の Pi 4 向け設計方針に従う。

- **最新のフレームを優先し、滞留した古いフレームは破棄する**
- ただし**破棄したことを黙って捨てない。** 破棄・欠番は件数として数え、後から確認できるようにする
- 毎フレーム巨大な Point Cloud を作らない。不要な画像コピーを減らす
- **GUI 表示を本番処理の必須要件にしない**（headless 運用可能であること）

### A-4. 入力層を live / recorded / simulated で差し替える

`docs/development-environment.md §7` の「入力元が3種類になっても下流を変えずに済む」を実装する。

- 下流（検出・追跡・予測）は**入力元の種別を意識せずに**フレーム系列を受け取れること
- 入力元の種別は **`prediction_core.SourceKind`（`live` / `recorded` / `simulated`）を使用し、
  同義の列挙型を新たに定義しない**
- `simulated` はこの Spec では**差し替え口を用意するところまで**を持ち、
  投擲物理の生成は `trajectory-simulator` の責務とする

### A-5. 記録は2階層に分ける

**記録対象が2種類あり、粒度が異なる。** これを1つの形式に混ぜない。

| 階層 | 内容 | 誰が定義するか |
|---|---|---|
| **観測フレーム記録** | D435 から取得したフレーム系列とその取得メタ情報 | **本 Spec が定義する**（OQ-32） |
| **Throw Record** | 1投擲の観測サンプル列・予測結果列 | **`prediction-core` が確定済み**（D-8）。**本 Spec は再定義しない** |

観測フレーム記録は「検出前の生データ」であり、Throw Record は「検出後の 3D サンプルと予測」である。
**フレーム記録を Throw Record の一部として押し込まない。**

### A-6. Throw Record の保存形式は `prediction-core` に従う（OQ-32 の残り）

`docs/decisions.md` D-8 により、Throw Record のスキーマは決着済みである。
**残っているのは保存先・拡張子・ファイル上のレイアウトだけ**であり、本 Spec はそこだけを決める。

- 直列化・復元は `prediction_core.ThrowRecord` の `to_json` / `from_json` を使用する
- **`schema_version` を自前で解釈し直したり、独自のスキーマを定義したりしない**
- 追加で残したい項目は、D-8 が用意している**加算的拡張の退避先**を使う

> **`prediction-core` の公開 API は `prediction_core.__init__` が公開する18シンボルのみ**であり、
> 本 Spec はその入口からのみ参照する。内部モジュールへ直接依存しない。

### A-7. 構造化ロギングは「記録するだけ」にする（OQ-35）

`original-features.md` 柱2a の制約をそのまま要件とする。

| # | 制約 |
|---|---|
| 1 | 記録は **fire-and-forget**。**完了を待たない** |
| 2 | 集計・可視化の重い処理を **Pi 上で行わない**（記録するだけ） |
| 3 | 計測は**実行時に無効化できる** |
| 4 | **計測 ON / OFF で end-to-end latency が有意に変化しないこと**を実測で確認する |

制約4 を満たせない場合、計測は**測定対象を歪める**ため計測器としての価値を失う。

### A-8. 計測点は自分の区間だけを持つ

本 Spec は**ロギング基盤**と **capture 区間の計測点**を持つ。
detection / tracking / prediction の各区間は、**各 Spec が自分の区間の計測点を足す**
（roadmap「Shared seams to watch」）。基盤はそれを可能にする形で提供する。

### A-9. 解像度・fps は実効サンプル数で評価する（OQ-25）

**fps 単体で比較しない。** 比較すべきは
**「許容時間内に何サンプル取れて、予測誤差がどこまで収束するか」**である。

- 高 fps は dropped frame を招けば逆効果になる
- **`60 fps ありき`で設定を選ばない。** 30 fps で必要サンプル数が取れるなら 30 fps でよい
- 少なくとも 640×480 / 30 fps と 640×480 / 60 fps を同条件で比較する
- 比較結果と選定した設定、およびその根拠を記録する

### A-10. 未実測の数値を合否条件にしない

`tech.md` 開発標準1 に従う。

- 本 Spec は **fps・レイテンシ・CPU 使用率の目標値を合否条件として置かない**
- 置くのは「**測れること**」「**記録が残ること**」「**設定を切り替えられること**」である
- **OQ-27（Pi 4 を継続するか）の判断は行わない。** 判断材料を揃えるところまでを持ち、
  判断は `m1-prediction-validation` が行う

### A-11. 開発フローを壊さない

```
WSL で開発 → Git push → Pi で pull → 実機テスト・実データ記録 → WSL へ持ち帰り解析
```

- **Pi 上で直接コードを編集する運用にしない**（差分が迷子になる）
- **RealSense を WSL へ直結する構成を標準フローにしない**
- 記録データの解析・Replay は **WSL 側**で行う

### A-12. 未決のまま残すもの（明示）

本 Spec は次の2件を**決着させない**。ただし作業を進めるために**前提を明示する**。

| OQ | 扱い |
|---|---|
| **OQ-40**（リポジトリのディレクトリ構成） | 全体構成は決めない。本 Spec は**自分のパッケージ1つ**と、記録データの置き場だけを定める。既存の `src/prediction_core/` と同じ並びに置く前提で進める |
| **OQ-41**（Python の環境構築・パッケージ管理） | 全体方針は決めない。ただし**リポジトリには既に `pyproject.toml` / `uv.lock` / `.python-version` が存在する**ため、本 Spec はその既存構成に乗る前提で進める。**Pi 側での成立性（pyrealsense2 が `.so` として現れる問題を含む）が確認できた時点で OQ-41 へ結果を報告する** |

---

## 既存ドキュメントとの関係（記録のみ。本 Spec では変更しない）

### E-1. `prediction-core` はサードパーティ依存ゼロで設計されている

`prediction-core` は Replay の決定性を守るために**実行時のサードパーティ依存を持たない**
（BLAS 実装差による非決定性を避けるため）。

本 Spec は **pyrealsense2 / NumPy / OpenCV 等のサードパーティ依存を必要とする。**
これは想定内であり問題ではないが、**それらの依存を `prediction_core` 側へ押し戻さない。**
依存の向きは常に `sensing-foundation → prediction_core` の一方向である。

### E-2. `docs/` 側の更新は本 Spec では行わない

OQ-23 / 24 / 25 / 28 / 32 / 35 の決着内容は本 Spec の成果物として確定するが、
`docs/open-questions.md` からの行削除と `docs/decisions.md` への移行は
**実装完了時にまとめて行う**（`structure.md` の運用ルール2）。要件としてはこれを含めない。

---

## design フェーズで決めるもの（requirements では決めない）

- 観測フレーム記録の**ファイル形式**（RealSense ネイティブ形式 / 独自形式 / サイドカーの有無）
- 構造化ログの**具体的な行形式とフィールド名**
- Throw Record の**拡張子とディレクトリレイアウト**
- フレーム取得ループの実現方式（ポーリング / コールバック / キュー段数）
- ログ送出の非同期化方式（スレッド / キュー / バッファサイズ）
- 抽象化のインターフェース形（Protocol / 抽象基底クラス / ジェネレータ）
- パッケージ名・モジュール構成・公開 API の形

---

## 制約（brief.md より継承）

- **RealSense 実機は Pi 4 側で扱う。** WSL 直結を標準フローにしない
- Pi 4 向け設計方針を守る: 低レイテンシ優先 / 古いフレームを溜めない /
  不要な画像コピーを減らす / **毎フレーム巨大な Point Cloud を作らない** /
  **GUI 表示を本番処理の必須要件にしない**
- ログ送出は **fire-and-forget**。完了を待たない。**実行時に無効化できる**こと
- **`prediction-core` の Throw Record スキーマを二重定義しない**
- 単位は **距離 mm / 時刻 ms**（`docs/requirements.md §6.1`、`structure.md` 命名規約）
- **未実測の数値を合否条件にしない**（`tech.md` 開発標準1）

---

## Introduction

sensing-foundation は、**Raspberry Pi 4 と RealSense D435 を実際に動く状態にし**、
そこから得られるフレームを**下流が入力元を区別せずに扱える形**へ整え、
**記録・再生**と**構造化ロギング**の基盤を提供する Spec である。

本 Spec の価値は「フレームが取れること」そのものよりも、
**その後のすべての判断が実測に基づけるようになること**にある。
`development-environment.md §13.1` の段階別レイテンシが取れなければ
**OQ-27（Pi 4 を継続するか）の判断そのものが下せず**、
Record / Replay が無ければ**アルゴリズム変更の効果を投擲のばらつきと切り分けられない。**

あわせて、実機セットアップの成立性（OQ-23 / OQ-24 / OQ-28）、
解像度・fps 設定（OQ-25）、Record / Replay の保存形式（OQ-32）、
ログ形式（OQ-35）を本 Spec で決着させる。

**本 Spec は実機を必要とする。** ハード待ちが発生するため、
ソフトウェア部分は**実機が無くても検証できる形**で設計・実装できることを要件に含める
（Replay と simulated 入力がその手段になる）。

## Boundary Context

- **In scope**:
  - Pi 4 の OS 選定・導入と、RealSense の導通確認（認識・USB3・給電安定性・SDK 導入）
  - 解像度・fps の実測比較と設定の決定（**実効サンプル数**で評価）
  - 古いフレームを溜めないフレーム取得ループと、破棄・欠番の把握
  - 観測フレーム記録（Record）と、WSL 側での再生（Replay）
  - live / recorded / simulated を差し替え可能にする入力層の抽象化
  - 構造化ロギング基盤と、**capture 区間**のレイテンシ・スループット計測
  - Throw Record の**保存形式**（スキーマではない）の確定
  - **カメラ座標系への1画素の逆投影（ピンホール基本演算）**の一元提供
    （mm 換算・画素中心規約・逆投影式・無効画素判定。下流2 Spec の二重実装を防ぐため）
  - 計測 ON / OFF でレイテンシが有意に変化しないことの実測確認
- **Out of scope**:
  - 物体検出・追跡（→ `flying-object-tracking`）
  - 床平面推定・World frame への座標変換の確立（→ `world-frame-calibration`）
    **ただしカメラ座標系への逆投影の基本演算は本 Spec が持つ**（上記 In scope）
  - 落下地点の予測（→ `prediction-core`。実装済み）
  - 投擲物理・ノイズモデルによる合成データの生成（→ `trajectory-simulator`）
  - ライブダッシュボード・可視化（→ OQ-38。**作らない**）
  - 集計・可視化の重い処理を Pi 上で行うこと（**記録するだけにする**）
  - **Pi 4 を継続するかの判断**（→ OQ-27 / `m1-prediction-validation`）
  - Throw Record **スキーマ**の定義（→ `prediction-core`。確定済み）
  - ESP32 への送信、移動体側のすべて
- **Adjacent expectations**:
  - `prediction-core` が確定させた Throw Record（`SCHEMA_VERSION` 1.0）に**従う**。
    本 Spec は保存先・拡張子・ファイル上のレイアウトのみを決める
  - `flying-object-tracking` / `world-frame-calibration` は、本 Spec が提供する
    **入力層の抽象**を通じてフレームを受け取り、**自分の区間の計測点を**ロギング基盤へ足す。
    さらに両 Spec は**逆投影の基本演算を再実装せず本 Spec のものを呼ぶ**（要件 3.8）
  - `m1-prediction-validation` は、本 Spec が残した計測記録を集計して
    段階別レイテンシと OQ-27 を判断する。本 Spec は判断を行わない
  - `trajectory-simulator` は `simulated` 入力の供給側になりうるが、
    物理モデルの詳細度（OQ-33）は本 Spec の責務ではない

## Requirements

### Requirement 1: 実機ブリングアップと成立性の確認

**Objective:** As a 実機セットアップを行う開発者, I want OS 導入から SDK 導入までを定義された順序で実施し結果を記録できること, so that RealSense の導通が不安定なまま fps を測って「Pi 4 の性能不足」と誤診する事態を避けられる

_出典: A-1 / A-2 / OQ-23 / OQ-24 / OQ-28_

#### Acceptance Criteria

1. The ブリングアップ手順 shall RAM 容量の確認・OS 選定・D435 の認識確認・USB3 接続確認・給電安定性確認・SDK 導入・解像度fps 動作確認・リソース計測をこの順序で実施する
2. The ブリングアップ手順 shall 解像度・fps の動作確認より前に、D435 の認識・USB3 接続・給電安定性・SDK 導入の4項目を完了させる
3. When 各確認項目を実施した場合, the ブリングアップ手順 shall 実施結果（成否・観測値・使用したコマンドまたは操作）を後から参照できる形で記録する
4. The sensing-foundation shall 接続された D435 が USB3 で接続されているかを判定できる手段を提供する
5. If D435 が USB2 で接続されている場合, then the sensing-foundation shall それを警告として明示し、fps 計測の結果を有効なものとして扱わない
6. The ブリングアップ手順 shall 導入する OS を 64bit かつ headless 運用可能なものに限定する
7. The ブリングアップ手順 shall Raspberry Pi OS 64-bit を先に評価し、RealSense SDK の導入または動作が成立しない場合に限り Ubuntu 24.04 LTS arm64 へ退避する
8. When OS が確定した場合, the ブリングアップ手順 shall 採用した OS と選定根拠、および RAM 容量を記録として残す
9. The ブリングアップ手順 shall RealSense SDK の導入手順を、同じ手順で再現できる形で記録する
10. The sensing-foundation shall 実行環境が要求を満たしているか（SDK が利用可能か、デバイスが認識されているか、接続速度が十分か）を確認する手段を、フレーム取得本体とは独立に提供する

### Requirement 2: フレーム取得の安定性と取りこぼしの把握

**Objective:** As a 下流処理（検出・追跡）の実装者, I want 常に最新のフレームを受け取れ、取りこぼしが件数として分かること, so that 遅延の蓄積で予測が古い情報に基づく事態を防ぎ、性能不足の切り分けができる

_出典: A-3 / `docs/development-environment.md` §4_

#### Acceptance Criteria

1. While フレーム取得が継続している間, the sensing-foundation shall 未処理のフレームを蓄積せず、最新のフレームを優先して下流へ渡す
2. When 処理が追いつかず滞留したフレームを破棄した場合, the sensing-foundation shall 破棄した件数を計数し、後から取得できるようにする
3. When 取得したフレームの連番に欠落が生じた場合, the sensing-foundation shall 欠落した件数を計数し、後から取得できるようにする
4. The sensing-foundation shall 取得したフレームごとに、連番・取得時刻・入力元の種別を伴って下流へ渡す
5. The sensing-foundation shall フレーム取得の継続に GUI 表示を必要としない
6. The sensing-foundation shall フレームごとの Point Cloud 生成を行わない
7. If フレームの取得に失敗した、または一定時間フレームが到着しない場合, then the sensing-foundation shall その事象を記録し、取得ループを継続するか停止するかを利用側が制御できるようにする
8. When フレーム取得を終了した場合, the sensing-foundation shall 取得したフレーム総数・破棄件数・欠落件数・実測フレームレートを含む要約を提供する

### Requirement 3: フレームの共通表現と時刻基準

**Objective:** As a 下流 Spec の実装者, I want 入力元によらず同じ形のフレームと、一貫した時刻基準が得られること, so that live で書いたコードをそのまま recorded / simulated に対して動かせる

_出典: A-4 / `docs/development-environment.md §7` / `structure.md` 命名規約_

#### Acceptance Criteria

1. The sensing-foundation shall 取得したフレームを、入力元に依存しない共通表現として下流へ渡す
2. The sensing-foundation shall 共通表現に、Depth データ・連番・取得時刻・入力元種別・その時点で有効な取得設定を含める
3. The sensing-foundation shall 時刻を ms、距離を mm で表し、それらを表すフィールド名に単位を含める
4. The sensing-foundation shall セッション内のフレーム時刻を、単調に増加する単一の時間基準で表す
5. When フレームを取得した場合, the sensing-foundation shall そのフレームの時刻が何を基準とした値か（センサ側の時刻かホスト側の時刻か）を利用側が識別できるようにする
6. The sensing-foundation shall 3次元座標への変換に必要なカメラ内部パラメータを、フレームと同じ入力元から取得できるようにする
7. The sensing-foundation shall World frame への座標変換および床平面の推定を自身の責務に含めない
8. The sensing-foundation shall 生の Depth 値から mm への換算・画素中心の規約・カメラ座標系への逆投影・無効な Depth 値の判定を単一の手段として提供し、利用側が同等の演算を再実装しなくてよいようにする

### Requirement 4: 入力層の抽象化（live / recorded / simulated）

**Objective:** As a 検出・追跡アルゴリズムの開発者, I want 入力元を差し替えても下流のコードを変えずに済むこと, so that 実機が無い WSL 上でも同じコードでアルゴリズムを改善できる

_出典: A-4 / `tech.md` 開発標準6_

#### Acceptance Criteria

1. The sensing-foundation shall live（実機）・recorded（記録の再生）・simulated（合成）の3種の入力元を、共通の取得インターフェースで扱えるようにする
2. When 入力元を差し替えた場合, the sensing-foundation shall 下流のフレーム処理コードの変更を必要としない
3. The sensing-foundation shall 入力元の種別を `prediction_core` が定義する種別（live / recorded / simulated）で表し、同義の種別を新たに定義しない
4. The sensing-foundation shall 実機用の入力元以外を、RealSense の SDK およびハードウェアが利用できない環境でも動作させる
5. Where simulated 入力が使用される場合, the sensing-foundation shall 合成フレームを供給する差し替え口を提供するにとどめ、投擲物理そのものの生成を自身の責務に含めない
6. The sensing-foundation shall 入力元ごとの固有設定を、共通の取得インターフェースの外側で指定できるようにする

### Requirement 5: 観測フレームの記録（Record）

**Objective:** As a 実機で投擲データを取る開発者, I want 投擲時のフレーム系列を欠落情報ごと記録できること, so that WSL へ持ち帰って何度でも同じデータで検証できる

_出典: A-5 / `docs/development-environment.md §6` / OQ-32_

#### Acceptance Criteria

1. When 記録が有効な場合, the sensing-foundation shall 取得したフレーム系列を、その連番・取得時刻とともに永続化する
2. The sensing-foundation shall 記録に、使用した解像度・フレームレート・入力元種別・カメラ内部パラメータ・記録開始時刻・デバイス識別情報を含むメタ情報を伴わせる
3. The sensing-foundation shall 記録中に破棄・欠落したフレームの件数を記録へ含める
4. The sensing-foundation shall 記録の開始と終了を実行時に制御できるようにする
5. Where 投擲の瞬間だけを残したい場合, the sensing-foundation shall 直近一定時間分のフレームのみを保持して保存する方式を選択できるようにする
6. The sensing-foundation shall 記録が有効な場合の追加負荷を、無効な場合と比較して計測できるようにする
7. The sensing-foundation shall 記録処理を、フレーム取得ループの継続を妨げない形で行う
8. If 記録先の容量が不足した、または書き込みに失敗した場合, then the sensing-foundation shall その事象を記録し、フレーム取得自体は継続できるようにする
9. The sensing-foundation shall 観測フレームの記録を Throw Record とは別の記録として扱い、Throw Record のスキーマに押し込まない

### Requirement 6: 記録の再生（Replay）とハード非依存の検証可能性

**Objective:** As a WSL 上でアルゴリズムを改善する開発者, I want 記録したデータを実機無しで繰り返し再生できること, so that アルゴリズム変更の効果を投擲のばらつきと切り分けて評価できる

_出典: A-4 / A-11 / `docs/development-environment.md §6` / OQ-32_

#### Acceptance Criteria

1. When 記録されたデータを再生した場合, the sensing-foundation shall 記録時と同じフレーム系列を同じ順序で下流へ渡す
2. When 同一の記録を複数回再生した場合, the sensing-foundation shall 毎回同一のフレーム系列を返す
3. The sensing-foundation shall 再生を RealSense のハードウェアが接続されていない環境で実行できるようにする
4. The sensing-foundation shall 再生時に、記録に含まれるメタ情報（解像度・フレームレート・カメラ内部パラメータ）を live の場合と同じ経路で取得できるようにする
5. The sensing-foundation shall 実時間に沿った再生と、可能な限り速く進める再生の両方を選択できるようにする
6. When 再生対象の記録が破損している、または想定した形式でない場合, the sensing-foundation shall その旨を利用側が識別できる形で通知し、無効なフレームを正常なフレームとして返さない
7. The sensing-foundation shall 記録の再生において、フレームの取りこぼしを再生側の都合で新たに発生させない

### Requirement 7: Throw Record の保存形式

**Objective:** As a 下流 Spec（`m1-prediction-validation` / `flying-object-tracking`）の実装者, I want 1投擲の記録を決まった場所と形で保存・読み出しできること, so that スキーマを再定義することなく投擲データを蓄積し、後から評価できる

_出典: A-5 / A-6 / OQ-32 / `docs/decisions.md` D-8_

#### Acceptance Criteria

1. The sensing-foundation shall Throw Record のスキーマを自身で定義せず、`prediction-core` が確定させたスキーマをそのまま使用する
2. The sensing-foundation shall Throw Record の直列化・復元を `prediction-core` が提供する手段によって行う
3. When Throw Record を保存して読み戻した場合, the sensing-foundation shall 元の内容と等価な Throw Record を返す
4. The sensing-foundation shall 複数の Throw Record を1つの保存先へ追記でき、後から1件ずつ読み出せるようにする
5. If 保存された記録の一部が破損していた場合, then the sensing-foundation shall 破損した記録を識別可能な形で報告し、他の健全な記録の読み出しを妨げない
6. If 保存された記録のスキーマ版が現在扱える版と異なる場合, then the sensing-foundation shall それを識別可能な形で報告し、内容を推測して読み替えない
7. The sensing-foundation shall 観測フレーム記録と Throw Record を、後から対応付けられる識別子で結び付ける
8. The sensing-foundation shall `prediction-core` の公開入口が提供するシンボルのみを参照し、その内部構成へ依存しない

### Requirement 8: 構造化ロギング基盤

**Objective:** As a M1 の計測を行う開発者, I want 各処理段階の時刻と計測値を機械可読な形で残せること, so that 段階別レイテンシを後から集計でき、OQ-27 の判断材料が揃う

_出典: A-7 / A-8 / OQ-35 / `original-features.md` 柱2a_

#### Acceptance Criteria

1. The sensing-foundation shall 各イベントを、時刻・段階名・イベント名・任意の付随値を持つ構造化された記録として残す
2. The sensing-foundation shall ログを1件ずつ追記でき、後から機械的に読み取って集計できる形式で保存する
3. The sensing-foundation shall ログ送出を fire-and-forget とし、送出の完了を呼び出し側に待たせない
4. The sensing-foundation shall ログ出力を実行時に無効化できるようにする
5. While ログ出力が無効な場合, the sensing-foundation shall ログの生成および書き込みを行わない
6. If ログの書き込みが追いつかない場合, then the sensing-foundation shall フレーム取得を遅延させるのではなくログを破棄し、破棄件数を後から確認できるようにする
7. If ログの書き込みに失敗した場合, then the sensing-foundation shall 本来の処理を中断させない
8. The sensing-foundation shall ログの集計・可視化を自身の責務に含めず、記録するにとどめる
9. The sensing-foundation shall 下流 Spec が自分の段階の計測点を追加できる形でロギング手段を提供する
10. The sensing-foundation shall ログの時刻を、フレームの取得時刻と同一の時間基準で表す

### Requirement 9: capture 区間の計測

**Objective:** As a OQ-27 の判断材料を集める開発者, I want 取得段階のレイテンシとスループットが実測値として残ること, so that どの段階が律速かを分解して判断できる

_出典: A-8 / A-10 / `docs/development-environment.md §13.1`_

#### Acceptance Criteria

1. When フレームを取得した場合, the sensing-foundation shall 取得に要した時間を ms 単位で計測し、構造化ログへ残せるようにする
2. The sensing-foundation shall 実測のフレームレート・破棄件数・欠落件数を計測値として残せるようにする
3. The sensing-foundation shall CPU 使用率とメモリ使用量を計測値として残せるようにする
4. The sensing-foundation shall 計測結果を、区間ごとに分解して集計できる形で残す
5. The sensing-foundation shall 計測値について目標値の充足を判定せず、計測値の提供にとどめる
6. The sensing-foundation shall Pi 4 を継続するかの判断を自身の責務に含めない
7. The sensing-foundation shall 計測値の集計を実機以外の環境で実行できるようにする

### Requirement 10: 計測が計測対象を歪めないことの確認

**Objective:** As a 計測結果を根拠に判断する開発者, I want 計測 ON / OFF で処理時間が有意に変わらないことを実測で確認できること, so that 計測結果そのものを信用してよいと言える

_出典: A-7 制約4 / `tech.md` 開発標準5_

#### Acceptance Criteria

1. The sensing-foundation shall 計測を有効にした場合と無効にした場合の処理時間を、同一条件で比較する手段を提供する
2. When 計測 ON / OFF の比較を実施した場合, the sensing-foundation shall 両条件の実測値とばらつきを含む比較結果を記録として残す
3. The sensing-foundation shall 比較の判定基準（何をもって「有意に変化しない」とするか）を実測前に明示し、結果とともに記録する
4. If 計測 ON / OFF で処理時間に有意な差が観測された場合, then the sensing-foundation shall その事実を記録し、計測結果を無条件に有効なものとして扱わない
5. The sensing-foundation shall この比較を、実機を用いずに実行できる入力元に対しても実施できるようにする

### Requirement 11: 解像度・フレームレート設定の実測比較と決定

**Objective:** As a 固定側の設定を決める開発者, I want 候補設定を同一条件で比較した実測結果に基づいて設定を選べること, so that 「60 fps ありき」ではなく実効サンプル数に基づいて設定を決められる

_出典: A-9 / OQ-25 / `docs/development-environment.md` §5_

#### Acceptance Criteria

1. The sensing-foundation shall 解像度とフレームレートを設定として切り替えられるようにする
2. The sensing-foundation shall 少なくとも 640×480 / 30 fps と 640×480 / 60 fps を同一条件で比較する手段を提供する
3. When 候補設定の比較を実施した場合, the sensing-foundation shall 設定ごとに実測フレームレート・破棄件数・欠落件数・取得レイテンシ・CPU 使用率・メモリ使用量を記録する
4. The sensing-foundation shall 比較の評価軸を、フレームレート単体ではなく一定時間内に得られる有効フレーム数として扱う
5. When 比較が完了した場合, the sensing-foundation shall 採用する設定とその選定根拠を記録として残す
6. The sensing-foundation shall フレームレートの目標値を合否条件として持たない
7. The sensing-foundation shall Color ストリームの有無を設定として切り替えられるようにする

### Requirement 12: 実行環境と運用の制約

**Objective:** As a プロジェクトの保守者, I want 実機の運用制約と開発フローが破られないこと, so that Pi 上の直接編集や WSL 直結によって差分と切り分けが迷子になる事態を防げる

_出典: A-3 / A-11 / A-12 / `tech.md` Development Environment_

#### Acceptance Criteria

1. The sensing-foundation shall Pi 4 上での実行に GUI 環境を必要としない
2. The sensing-foundation shall 実機以外の環境（記録の再生・合成入力）で動作する部分について、RealSense の SDK が存在しない環境でも実行できるようにする
3. The sensing-foundation shall 記録データの解析・集計を実機以外の環境で実行できるようにする
4. The sensing-foundation shall 実行時の設定（入力元・解像度・フレームレート・記録の有無・ログの有無・出力先）を、コードの書き換えを伴わずに指定できるようにする
5. The sensing-foundation shall `prediction-core` へサードパーティ依存を追加させない
6. The sensing-foundation shall 記録データおよびログをリポジトリの版管理対象に含めない
7. Where 実機環境固有の導入手順が必要な場合, the sensing-foundation shall その手順を記録として残し、同じ手順で再現できるようにする
8. The sensing-foundation shall 下流 Spec が参照してよい公開シンボルを公開入口で明示的に列挙し、その集合が変化したことを検出できるようにする
