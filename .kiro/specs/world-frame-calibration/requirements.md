# Requirements Document

## Project Description (Input)

RealSense D435 の Depth から**床平面を推定して World frame を確立し**、カメラ座標系 → World 座標系の変換を提供する。
あわせて、**既知の物理位置との照合によって座標系そのものの正しさを独立に検証する手段**を実装する。
詳細な背景・スコープ・制約は [brief.md](./brief.md) を正とする。

### 誰の問題か

**このプロジェクトで最も時間を奪われる可能性が高い箇所である。**

`docs/requirements.md §6.2` が明示している問題:

> 予測が完璧でも座標系が数 cm ずれていれば必ず外す。
> しかも症状は「予測が悪い」ようにしか見えないため、**原因の切り分けに最も時間を奪われる箇所**になる。

- **M1（`m1-prediction-validation`）**: 落下地点の誤差が出たとき、それが座標系ずれか検出誤差か予測誤差かを分離できないと、
  どこを直せばよいか分からないまま時間だけが溶ける
- **`flying-object-tracking`**: カメラ座標系までを持ち、World への変換は本 Spec に委ねる。
  **この境界が曖昧だと、どちらが誤差の原因か分からなくなる**
- **M3（結合）**: 固定側と移動体側が同じ床基準座標系を共有していなければ FR-3 が成立しない。
  `docs/requirements.md §6.2` は「**この手順が未定義のまま M3 へ進まないこと**」と警告している

### 現状

- World frame の**原点・軸方向ともに未定**（OQ-03）。Z 軸が床面から鉛直上向き正であること、単位が mm / ms であることだけが確定している
- 「床平面は Depth の平面フィッティングで推定する」という方針だけが決まっている
  （D435 が IMU を持たないことの代償。`docs/bom.md §A` の選定根拠）
- **キャリブレーション手順は未定義。コードも存在しない**
- 上流の `sensing-foundation` は Spec が生成済みで、Depth フレームの取得・記録・再生・構造化ロギングの基盤を提供する
- 実機（Raspberry Pi 4 / RealSense D435）は**未セットアップ**

### 何が変わるべきか

- **設置のたびに実行できる手順**が定義され、実際に実行できる
- 床平面が推定でき、そこから World frame の原点・軸が一意に決まる
- カメラ座標の点列を World 座標へ変換できる
- **既知の物理位置に置いた対象の World 座標を算出し、実測値との誤差を定量レポートできる**
- そのレポートが**単体で合否を出せる**ため、検出・予測を一切動かさずに座標系の健全性を判定できる
- キャリブレーション結果を保存・読み込みでき、**設定が変わったまま古い結果を使い続ける事故を検出できる**

---

## 追加入力: 確定済みの方針（A-1〜A-9）

> 本節は Project Description の続きであり、**入力として与えられた確定方針**である。
> 検証可能な形式の要件は下の [`## Requirements`](#requirements) を正とし、
> `A-n` は各要件の**出典参照**として使用する。

### A-1. 検証ステップを最初に作る

`brief.md` の Approach が最も強く要求している事項。

> **検証ステップ（手順4）を最初に作る。**
> キャリブレーションの実装より先に「合っているかを確かめる手段」を用意する。
> そうしないと、キャリブレーション自体が正しいかを判定できないまま下流を作ることになる。

したがって本 Spec の中心は平面フィッティングではなく、**検証**である。
検証は次の性質を持たなければならない。

- **単体で実行でき、単体で合否が出る。** 物体検出・追跡・予測のいずれも動かさずに実行できる
- **キャリブレーションの構築に使っていない独立な点**で照合する。
  構築に使った点で照合すると、常に一致して見えるため検証にならない
- 誤差を**系統的なずれ（バイアス）とばらつき**に分解して報告する。
  「数 cm ずれている」のか「ばらついている」のかは原因が異なる

### A-2. World frame の定義（OQ-03 の決着）

`docs/requirements.md §6.1` の空欄を、次のとおり埋める。

| 項目 | 決定 |
|---|---|
| Z 軸 | 推定した床平面の法線のうち、**カメラ側を向く向きを正**とする（＝床面から鉛直上向き）。床面が z = 0 |
| 原点 | 床面上に置いた**原点マーカーの位置を床平面へ投影した点**。運用上はゴミ箱の待機位置に置く |
| +X 軸 | 原点マーカーから**方向マーカー**へ向かうベクトルを床平面へ投影し正規化した単位ベクトル。運用上は投擲方向（投擲者 → キャッチ領域）に一致させる |
| +Y 軸 | Z × X（右手系） |
| 単位 | 距離 mm / 時刻 ms（既に確定。`docs/requirements.md §6.1`） |

**マーカーは「床の上に置かれた、周囲の床面から区別できる高さを持つ物体」**であればよく、
特定の柄・色・既知寸法を要求しない。理由は次のとおり。

- **色を使わない。** `sensing-foundation` は Color stream を既定で切っている（`docs/development-environment.md §13.2` の改善順序1）。
  Color に依存すると、その既定を壊すか、キャリブレーションだけ別設定で走らせることになる
- **高さを既知にしない。** マーカーの観測点を床平面へ**法線方向に投影**して使うため、
  マーカーの高さは結果に影響しない。既知寸法を要求すると、運用のたびに同じ物体を用意する制約が増える

> ⚠️ この定義は**カメラの設置位置に依存しない。** マーカーを同じ場所へ置き直せば、
> カメラを動かしても同じ World frame が再現される。これは「設置のたびに実施する」前提（A-3）と、
> M3 で移動体のオドメトリ原点を World frame へ合わせる手順（A-8）の両方に必要な性質である。

### A-3. 設置のたびに実施する前提

`brief.md` Constraints より。**一度きりの作業として設計しない。**

- 手順は文書化され、**同じ手順を繰り返し実行できる**こと
- 結果は保存・読み込みでき、**いつ・どの設定で取得したものか**が分かること
- 再実施した結果どうしを比較でき、**再現性を数値で確認できる**こと

### A-4. 系統誤差とノイズを分離する

`docs/requirements.md §6.2` の「手順4（検証）を先に用意しておけば、系統誤差か予測誤差かを即座に分離できる」を成立させる。
そのために検証レポートは最低限、次を分けて示す。

- **平均オフセット（バイアス）**: 全検証点に共通して乗っているずれ。座標系の原点・軸のずれを示す
- **ばらつき**: 検証点ごとの残差の散らばり。Depth ノイズや観測手順の再現性を示す
- **距離依存性**: カメラからの距離ごとの誤差。Depth 誤差は距離とともに増大するため、
  「遠方だけ大きい」のか「全域で一様にずれている」のかで原因が変わる

### A-5. 上流契約: `sensing-foundation` の公開入口のみを使う

- Depth フレームは `sensing-foundation` から `for frame in source.frames():` の形で受け取る。
  **RealSense SDK を本 Spec が直接叩かない**
- 入力元は **live / recorded / simulated のいずれでもよい**。
  実機が無い期間でも、記録済みセッションと合成入力で全機能を動かせること
- Depth バッファは**読み取り専用として扱い、その場で書き換えない**（Replay の再現性が壊れるため）
- 構造化ログは `sensing-foundation` の仕組みに乗り、**本 Spec は自分の段階名を足すだけ**にする
  （`docs/development-environment.md §13.1` の段階別レイテンシは各 Spec が自区間を計測する方針）
- **ピンホール逆投影の基本演算は `sensing-foundation` が持つ**（同 Spec 要件 3.8 / `geometry.py`）。
  無効 Depth の判定・生カウントから mm への換算・画素からカメラ座標系への逆投影の3つは、
  **本 Spec も `flying-object-tracking` も再実装せず同じ関数を呼ぶ**。
  両者が別々に同等の式を書くと、画素中心規約・mm 換算位置・無効画素の扱いのわずかな差が
  「予測が悪い」としか現れない座標ずれになり（`docs/requirements.md §6.2`）、
  本 Spec が切り分けようとしている当のものと区別できなくなる

### A-6. 資源制約: 毎フレーム巨大な Point Cloud を作らない

`brief.md` Constraints および `docs/development-environment.md §4` より。

- 平面推定・マーカー観測は**キャリブレーション時のみの処理**であり、常時実行しない
- 下流が毎フレーム使うのは**変換だけ**であり、その計算量は点数に比例する軽い処理であること
- 全画素の3次元展開は、キャリブレーション時であっても**必要な範囲に限る**

### A-7. 未実測の数値を合否条件にしない（`tech.md` 開発標準1）との両立

検証は合否を出す必要がある（A-1）が、許容誤差の目標値はまだ実測されていない。
この2つは次の形で両立させる。

- **誤差の実測値そのものは常に無条件で出力する。** これは陳腐化しない
- 合否は「**実行時に与えられた許容値**」に対して判定する。既定値は**暫定目標値**であり、
  レポートには**判定に使った許容値とその出典（暫定か実測由来か）を必ず併記する**
- **暫定値を根拠に方針を変えない。** 実測後に許容値を見直せることを前提とする

> `sensing-foundation` が計測 ON/OFF 比較で採った考え方（判定基準を絶対値ではなく相対量で定義し、
> 基準そのものを結果とともに記録する）と同じ扱いにする。

### A-8. 移動体オドメトリ原点の初期化は「手順の定義」までを持つ

`docs/requirements.md §6.2` の手順3 に相当する。
**移動体が存在しないため実装は M3 側**（`brief.md` Scope: Out）。

本 Spec は「World frame の原点が物理的にどこか」を一意に決め（A-2）、
**その点へ移動体を置けばオドメトリ原点が World 原点と一致する**という関係を手順として文書化する。

### A-9. 縮退条件では結果を出さない

座標系が数 cm ずれても症状が「予測が悪い」にしか見えない以上、
**間違った変換を静かに返すことが最悪の失敗である。**

- 床平面が十分な点で支持されていない場合
- 原点マーカーと方向マーカーが近すぎて、+X 軸の向きが不安定な場合
- 観測点が平面法線方向にほとんど広がっておらず、姿勢が定まらない場合
- 保存された結果と現在のストリーム設定・カメラ内部パラメータが食い違う場合

これらは**警告ではなく失敗として扱い、変換を返さない。**

---

## 決着させる未決事項

| OQ | 本 Spec での扱い |
|---|---|
| **OQ-03** ★ World frame の原点・軸方向とキャリブレーション手順 | **決着させる。** 定義は A-2、手順は要件 7 |

**未決のまま残すもの**（明示）:

- **OQ-06**（設置形態: 壁固定 / 三脚）: 決めない。A-2 の定義は**カメラの設置位置に依存しない**ため、
  どちらでも成立する。ただし設置形態が変われば再キャリブレーションが要る点を手順に明記する
- **OQ-40**（全体のディレクトリ構成）: 本 Spec は自パッケージの位置だけを定める
- **OQ-41**（Python の環境構築・パッケージ管理）: 既存の設定に乗る。新たに決めない
- **OQ-26**（物体検出方式）: `flying-object-tracking` の担当。本 Spec の**マーカー観測は検出方式ではない**
  （キャリブレーション時に静止した対象を範囲指定で観測するだけであり、飛翔物の検出とは要求が別物）

---

## design フェーズで決めるもの（requirements では決めない）

- 平面フィッティングの具体的な手法（RANSAC の反復回数・しきい値、最小二乗の解き方）
- マーカー観測の具体的な集約方法（範囲指定の形式、外れ値の除き方、複数フレームの平均化）
- 変換の内部表現（回転行列 / 四元数 / 同次変換行列）と直列化形式
- クラス・モジュール構成、公開 API のシンボル
- 保存ファイルの形式版番号と整合性検査の実装方法
- NumPy をどこまで使うか、依存をどの層に閉じ込めるか

---

## 制約（brief.md / steering より継承）

- **カメラは固定運用。** エゴモーション補正は対象外（D435 に IMU が無い前提と整合）
- **単位は距離 mm / 時刻 ms**（`docs/requirements.md §6.1`）
- **未実測の数値を合否条件にしない**（`tech.md` 開発標準1）。数値には導出根拠を併記する
- **毎フレーム巨大な Point Cloud を作らない**（`docs/development-environment.md §4`）
- **`prediction_core` の実行時サードパーティ依存ゼロを壊さない。**
  本 Spec の都合で `prediction_core` へ依存を追加しない
- **`flying-object-tracking` と並行実装できること。** 向こうはカメラ座標系までを持ち、
  World への変換は本 Spec が持つ。この境界を曖昧にしない
- 実機は未セットアップ。**ハード不要で進められる作業と、ハード到着後にしかできない作業を分ける**

---

## Introduction

world-frame-calibration は、Depth から**床平面を推定して World frame を確立し**、
カメラ座標系 → World 座標系の変換を提供する部品である。

本 Spec の価値は「変換できること」そのものではなく、**変換が正しいことを独立に確かめられること**にある。
`docs/requirements.md §6.2` が警告するとおり、座標系の数 cm のずれは
「予測が悪い」という症状としてしか現れず、検出誤差・予測誤差と区別がつかない。
検出も予測も動かさずに単体で合否が出る検証を持つことで、**M1 の誤差要因を最初に切り分けられる状態**を作る。

あわせて **OQ-03（World frame の原点・軸方向とキャリブレーション手順）を決着させる。**
これは `docs/requirements.md §6.2` が「未定義のまま M3 へ進まないこと」と名指しした唯一の項目である。

本 Spec は上流 `sensing-foundation` の公開入口からフレームを受け取り、
下流 `flying-object-tracking` と `m1-prediction-validation` へ変換を提供する。
`flying-object-tracking` とは**並行して実装できる**。

## Boundary Context

- **In scope**:
  - Depth からの床平面推定（＝カメラ姿勢の取得）
  - World frame の原点・軸方向の決定（OQ-03 の決着）と、その確立手順
  - カメラ座標系 → World 座標系の変換の提供（`docs/requirements.md` FR-3 のうち固定側の担当分）
  - **既知の物理位置との照合による独立検証と、誤差の定量レポート**
  - 縮退条件・不良条件の検出と、その場合に変換を返さないこと
  - キャリブレーション結果の保存・読み込みと、設定不一致の検出
  - 設置のたびに実行する手順の文書化と、再現性の確認手段
  - 自区間（キャリブレーション処理と変換）の計測値の提供
- **Out of scope**:
  - 飛翔物の検出・追跡・3D位置取得（→ `flying-object-tracking`。カメラ座標系までを持つ）
  - 落下地点・落下時刻の予測（→ `prediction-core`。実装済み）
  - フレーム取得・記録・再生・構造化ロギング基盤（→ `sensing-foundation`）
  - **ピンホール逆投影の基本演算**（無効 Depth 判定・mm 換算・逆投影式）（→ `sensing-foundation` 要件 3.8）。
    本 Spec は呼ぶだけで、式・画素中心規約・無効画素の扱いを自分で決めない
  - M1 の end-to-end 計測と完了判定（→ `m1-prediction-validation`）
  - **移動体オドメトリ原点初期化の実装**（→ M3）。手順としては定義するが実装は持たない
  - 移動体のオドメトリそのもの（`docs/requirements.md` FR-6）
  - カメラを移動させる運用・エゴモーション補正（カメラは固定運用）
  - 設置形態（壁固定 / 三脚）の決定（→ OQ-06）
  - Throw Record スキーマの定義・変更（`prediction-core` が正。`docs/decisions.md` D-8）
- **Adjacent expectations**:
  - `sensing-foundation` は、入力元によらない共通のフレーム表現とカメラ内部パラメータ、
    構造化ログの送出手段、および**ピンホール逆投影の基本演算**（`geometry.py`）を提供する。
    本 Spec はその**公開入口だけ**を使う
  - `flying-object-tracking` は検出結果を**カメラ座標系のまま**引き渡す。
    World への変換は本 Spec が行い、向こう側で座標系を持たない。
    **両 Spec は同じ逆投影の基本演算に乗る**ことが、片方で校正した床平面をもう片方の点に適用してよい根拠になる。
    この一致は `m1-prediction-validation` のクロス Spec 契約テスト
    （同 Spec 要件 1.10 / `tests/m1_validation/test_deprojection_contract.py`）が許容差なしで固定する
  - `m1-prediction-validation` は、本 Spec の検証レポートを**誤差要因の切り分け材料**として使う。
    予測誤差の評価そのものは向こうが持つ
  - `prediction-core` は入力サンプルが**既に World frame へ変換済み**であることを前提としている。
    その前提を満たす責任は本 Spec にある
  - M3 は、World 原点の物理的な位置が一意に決まっていることを前提に、
    移動体のオドメトリ原点をそこへ合わせる

## Requirements

### Requirement 1: 床平面の推定

**Objective:** As a システムの設置者, I want Depth フレームから床平面のパラメータと推定品質が得られること, so that IMU を持たない D435 でもカメラの姿勢を取得できる

_出典: A-2, A-6, A-9_

#### Acceptance Criteria

1. When Depth フレームと床面の探索範囲が与えられた場合, the world-frame-calibration shall 床平面をカメラ座標系における平面パラメータとして推定する
2. The world-frame-calibration shall 平面推定に用いた有効点の数、平面に適合した点の割合、および平面までの距離残差の代表値を推定品質として提供する
3. When 有効な Depth 値を持たない画素が含まれる場合, the world-frame-calibration shall それらを平面推定の入力から除外する
4. The world-frame-calibration shall 平面推定を、探索範囲として指定された画素領域に限定して行い、全画素の3次元展開を必須としない
5. When 平面に適合した点の数または割合が実行時に与えられた下限を下回った場合, the world-frame-calibration shall 平面推定を失敗として扱い、平面パラメータを返さない
6. When 複数の Depth フレームが与えられた場合, the world-frame-calibration shall それらを用いて推定を安定化でき、使用したフレーム数を推定品質に含める
7. The world-frame-calibration shall 与えられた Depth バッファを書き換えない

### Requirement 2: World frame の定義と確立

**Objective:** As a システムの設置者, I want 床平面と2つの基準マーカーから World frame の原点・軸が一意に決まること, so that カメラを設置し直しても同じ床基準座標系を再現できる

_出典: A-2, A-9_

#### Acceptance Criteria

1. The world-frame-calibration shall World frame の Z 軸を、推定した床平面の法線のうちカメラ側を向く向きが正となるように定め、床面を z = 0 とする
2. The world-frame-calibration shall World frame の原点を、原点マーカーの観測点を床平面へ投影した点として定める
3. The world-frame-calibration shall World frame の +X 軸を、原点マーカーから方向マーカーへ向かうベクトルを床平面へ投影して正規化した向きとして定める
4. The world-frame-calibration shall World frame の +Y 軸を、Z 軸と X 軸の外積により右手系となるように定める
5. The world-frame-calibration shall 距離を mm、時刻を ms として扱う
6. The world-frame-calibration shall マーカーに特定の色・柄・既知寸法を要求せず、床面から区別できる高さを持つ物体であれば足りるものとする
7. When 原点マーカーと方向マーカーの床平面上の距離が実行時に与えられた下限を下回った場合, the world-frame-calibration shall World frame の確立を失敗として扱い、変換を返さない
8. The world-frame-calibration shall 原点マーカーと方向マーカーの距離から、マーカー観測のずれが +X 軸の向きに与える感度を算出し、確立結果に含める
9. When 同一の物理配置で確立した World frame があるとき, the world-frame-calibration shall カメラの設置位置が変わっても同一の World frame を再現できる方式でこれを定める

### Requirement 3: カメラ座標系から World 座標系への変換

**Objective:** As a `flying-object-tracking` および `m1-prediction-validation` の実装者, I want カメラ座標の点を World 座標へ変換できること, so that 検出側が座標系を持たずに済み、誤差の責任範囲が分離される

_出典: A-5, A-6_

#### Acceptance Criteria

1. When 確立済みの World frame とカメラ座標系の点が与えられた場合, the world-frame-calibration shall 対応する World 座標の点を返す
2. The world-frame-calibration shall 複数の点をまとめて変換できる手段を提供する
3. The world-frame-calibration shall 変換を回転と平行移動のみで構成し、拡大縮小・せん断を含めない
4. When Depth の画素位置と Depth 値、およびカメラ内部パラメータが与えられた場合, the world-frame-calibration shall 対応するカメラ座標系の3次元点を算出する
5. If カメラ内部パラメータのレンズ歪み係数が本 Spec の扱えない値である場合, then the world-frame-calibration shall 変換を返さず、扱えない理由を示して失敗する
6. The world-frame-calibration shall 変換の適用に、カメラ・SDK・ファイル入出力への接続を要求しない
7. When 床平面上にある点が変換された場合, the world-frame-calibration shall その World 座標の z 成分が推定品質の範囲内で 0 となる結果を返す
8. The world-frame-calibration shall 無効 Depth の判定・生 Depth 値から mm への換算・画素からカメラ座標系への逆投影の基本演算を自身で再実装せず、`sensing-foundation` が公開する共通の演算を用いる

### Requirement 4: 独立検証ステップ ★

**Objective:** As a M1 の実施者, I want 検出も予測も動かさずに、既知の物理位置と算出座標の誤差を単体で判定できること, so that 予測が外れたときに座標系ずれを最初に切り分けられる

_出典: A-1, A-4, A-7_

#### Acceptance Criteria

1. The world-frame-calibration shall 検証を、物体検出・追跡・予測のいずれも実行せずに単体で完了できる手段として提供する
2. When 既知の World 座標に置かれた検証対象の観測が与えられた場合, the world-frame-calibration shall 算出した World 座標と既知の位置との差分を軸ごとに報告する
3. The world-frame-calibration shall 検証点として、World frame の確立に用いた基準マーカー以外の点を用いることを要求し、確立に用いた点のみによる検証を有効な検証として扱わない
4. The world-frame-calibration shall 全検証点に共通して乗っている平均オフセットを、ばらつきと分けて報告する
5. The world-frame-calibration shall 検証点ごとのカメラからの距離と誤差の対応を報告する
6. When 実行時に許容値が与えられた場合, the world-frame-calibration shall 各検証点と全体について合否を判定し、判定に用いた許容値とその出典が暫定か実測由来かを併せて報告する
7. The world-frame-calibration shall 許容値が与えられていない場合でも、誤差の実測値そのものを報告する
8. The world-frame-calibration shall 検証レポートを、実行後に人が読める形と機械が再集計できる形の双方で残す
9. When 床面上の検証点に加えて既知の高さに置かれた検証点が与えられた場合, the world-frame-calibration shall 高さ方向の誤差も同じ形式で報告する
10. The world-frame-calibration shall 検証レポートに、対象となるキャリブレーション結果の識別子と、検証時のストリーム設定を含める

### Requirement 5: 縮退条件の検出と安全な失敗

**Objective:** As a システムの設置者, I want 座標系が定まらない条件では結果が返らないこと, so that 静かに間違った変換が下流へ流れ込む事態を防げる

_出典: A-9_

#### Acceptance Criteria

1. If 平面推定に十分な有効点が得られない場合, then the world-frame-calibration shall 失敗として扱い、原因を識別できる形で報告する
2. If 基準マーカーの観測が指定範囲内に得られない場合, then the world-frame-calibration shall 失敗として扱い、どのマーカーが得られなかったかを報告する
3. If 原点マーカーと方向マーカーの距離が下限を下回る場合, then the world-frame-calibration shall 失敗として扱い、+X 軸が不安定であることを報告する
4. If 推定した床平面の法線がカメラの視線方向に近く、床面を見込む角度が浅すぎる場合, then the world-frame-calibration shall 失敗として扱い、設置角度の見直しを促す情報を報告する
5. The world-frame-calibration shall 縮退条件による失敗を、部分的な結果や既定値で埋めた結果として返さない
6. The world-frame-calibration shall 失敗の理由を、呼び出し側が分岐できる識別可能な値として提供する

### Requirement 6: キャリブレーション結果の永続化と整合性検査

**Objective:** As a システムの設置者, I want キャリブレーション結果を保存・読み込みでき、設定が食い違えば検出されること, so that 設定を変えたまま古い結果を使い続ける事故を防げる

_出典: A-3, A-9_

#### Acceptance Criteria

1. When World frame の確立に成功した場合, the world-frame-calibration shall 結果を永続化でき、後から読み込んで同一の変換を再現できる
2. The world-frame-calibration shall 永続化された結果に、取得日時、入力元の種別、ストリーム設定、カメラ内部パラメータ、推定品質、および用いた基準マーカーの観測値を含める
3. The world-frame-calibration shall 永続化された結果に形式の版を含め、未知の版を読み込んだ場合に失敗として扱う
4. If 読み込んだ結果のストリーム設定またはカメラ内部パラメータが、現在の入力元のそれと一致しない場合, then the world-frame-calibration shall 不一致として報告し、変換をそのまま有効なものとして扱わない
5. The world-frame-calibration shall 永続化された結果に、最後に実施した検証の要約を関連付けられる手段を提供する
6. When 検証が未実施または失敗している結果が読み込まれた場合, the world-frame-calibration shall その状態を呼び出し側が判別できる形で示す

### Requirement 7: 設置のたびに実施する手順と再現性

**Objective:** As a システムの設置者, I want 再設置のたびに同じ手順を実行でき、結果の再現性を数値で確認できること, so that キャリブレーションが一度きりの職人作業にならない

_出典: A-1, A-3, A-8_

#### Acceptance Criteria

1. The world-frame-calibration shall 床平面推定・World frame の確立・検証を、設置のたびに実行できる一連の手順として提供する
2. The world-frame-calibration shall 手順を文書として提供し、必要な物理的準備、実行順序、および各段階で確認すべき出力を記述する
3. The world-frame-calibration shall 手順の中で検証を必須の段階として位置付け、検証を経ていない結果を運用に用いてよい状態として扱わない
4. When 同一の物理配置で複数回キャリブレーションを実施した場合, the world-frame-calibration shall 結果どうしの差分を、原点位置のずれと軸方向のずれとして比較できる手段を提供する
5. The world-frame-calibration shall 移動体のオドメトリ原点を World 原点へ合わせる手順を文書に含め、その実装が本 Spec の範囲外であることを明記する
6. The world-frame-calibration shall 設置形態やストリーム設定を変更した場合に再実施が必要であることを文書に明記する

### Requirement 8: 上流入力の契約と入力元非依存性

**Objective:** As a 開発者, I want live / recorded / simulated のいずれの入力元でも同じキャリブレーションが動くこと, so that 実機が無い期間でも実装と検証を進められる

_出典: A-5, A-6_

#### Acceptance Criteria

1. The world-frame-calibration shall Depth フレームを `sensing-foundation` が提供する共通のフレーム表現として受け取り、カメラ SDK を直接呼び出さない
2. The world-frame-calibration shall `sensing-foundation` の公開入口のみを参照し、その内部構造に依存しない
3. When live / recorded / simulated のいずれの入力元から同一内容のフレーム列が与えられた場合, the world-frame-calibration shall 同一のキャリブレーション結果を返す
4. The world-frame-calibration shall カメラ内部パラメータを入力フレームの経路から取得し、独自に保持した固定値を用いない
5. The world-frame-calibration shall 記録済みセッションに対して、実機とカメラ SDK の無い環境でキャリブレーションと検証を実行できる
6. The world-frame-calibration shall `prediction-core` に対して依存を追加せず、Throw Record スキーマを再定義しない

### Requirement 9: ハードウェア非依存の検証可能性

**Objective:** As a 開発者, I want 既知の姿勢から生成した合成入力で、推定した変換が正解と一致することを確かめられること, so that 実機到着前にアルゴリズムの正しさを確定できる

_出典: A-1, A-5_

#### Acceptance Criteria

1. When 既知のカメラ姿勢と既知のマーカー配置から生成された合成 Depth フレームが与えられた場合, the world-frame-calibration shall 数値計算上の誤差の範囲で既知の姿勢と一致する変換を返す
2. The world-frame-calibration shall 単体テストおよび検証の実行にハードウェアの接続を要求しない
3. When 既知の量のノイズを加えた合成入力が与えられた場合, the world-frame-calibration shall 推定品質と検証レポートがそのノイズを反映した値を示す
4. When 同一の入力が複数回与えられた場合, the world-frame-calibration shall 同一の結果を返す
5. The world-frame-calibration shall 縮退条件の各分岐を、実機を用いずに再現できる手段を提供する

### Requirement 10: 計測とロギング

**Objective:** As a M1 の実施者, I want キャリブレーションと変換の処理時間・品質が構造化ログとして残ること, so that 段階別レイテンシの集計に自区間を寄与できる

_出典: A-5, A-6, A-7_

#### Acceptance Criteria

1. The world-frame-calibration shall 自身の処理に対応する段階名を用いて、`sensing-foundation` の構造化ロギングへ計測値を送出する
2. The world-frame-calibration shall 構造化ロギングの基盤・形式・送出機構を自身で再実装しない
3. The world-frame-calibration shall 平面推定・マーカー観測・World frame 確立・検証の各段階に要した時間を計測値として残す
4. The world-frame-calibration shall 変換の適用に要する時間を、下流が自区間として計測できる形で提供する
5. When ロギングが無効化されている場合, the world-frame-calibration shall 計測値の生成を行わずに処理を完了する
6. The world-frame-calibration shall 計測した数値を、実測前の目標値との比較による合否判定には用いない

### Requirement 11: 責務境界の維持

**Objective:** As a 並行して作業する `flying-object-tracking` の実装者, I want 座標系の責務が本 Spec に閉じていること, so that 誤差が出たときにどちらの責任範囲かを迷わずに切り分けられる

_出典: A-5, A-8_

#### Acceptance Criteria

1. The world-frame-calibration shall 飛翔物の検出・追跡・フレーム間対応付けを自身の責務に含めない
2. The world-frame-calibration shall 落下地点・落下時刻の予測を自身の責務に含めない
3. The world-frame-calibration shall 移動体のオドメトリの実装を自身の責務に含めず、原点を合わせる手順の定義までを持つ
4. The world-frame-calibration shall 検出側から受け取る位置をカメラ座標系のものとして扱い、検出側に World 座標系を要求しない
5. The world-frame-calibration shall 自身が変更を加える範囲を自パッケージとその試験、および構成ファイルへの追記に限る
