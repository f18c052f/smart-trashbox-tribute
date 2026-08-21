# Requirements Document

## Project Description (Input)

RealSense D435 の Depth フレーム列から**飛来する空き缶だけを検出し、フレームを跨いで同一物体として繋ぎ、
カメラ座標系の3D位置サンプル列にする**処理を Python モジュールとして実装する。
詳細な背景・スコープ・制約は [brief.md](./brief.md) を正とする。

### 誰の問題か

`prediction-core` は「時刻付き3D点の列」を要求するが、**その列を作る手段がプロジェクトに存在しない。**

- **`m1-prediction-validation`**: 実データを `prediction_core` へ流すには、まず点列が必要になる。
  `docs/requirements.md §8 M1` の実測項目のうち、
  「リリース〜検出開始までの時間」と「何サンプル取れたか」は**本 Spec が作る点列がなければ測れない**
- **`world-frame-calibration`**: 変換すべき「カメラ座標系の点」が実際に流れてくる相手として本 Spec を前提にする
  （ただし同 Spec は本 Spec に import 依存せず、点を受け取る結合層は `m1-prediction-validation` が持つ）
- **`prediction-core`**: 実データ系統の唯一の供給元が本 Spec になる

### 現状

- **物体検出方式は未確定である**（→ OQ-26）。確定しているのは
  「**AI モデルを最初から必須にしない**」という方針だけであり、
  軽量方式の候補（Depth 差分 / 背景差分 / フレーム間差分 / Motion detection / ROI / 輪郭抽出）は
  `docs/development-environment.md §10` に列挙されているだけで比較されていない
- **対象物は空き缶（φ65mm 程度の剛体）に固定済み**。M1 の実験条件を確定させるための決定である
  （→ OQ-02 の M1 仮値）
- 上流の `sensing-foundation` が **`FrameSource` ポート・`CaptureFrame`・構造化ロギング・
  セッション記録／再生**を提供する設計になっている（同 Spec design.md）。
  ただし **OpenCV は意図的に導入されておらず**、検出が必要とする道具として本 Spec に割り当てられている
- 時間制約が厳しい。`docs/requirements.md §3` の時間予算表の区間2（検出開始〜予測確定）は
  **0.10〜0.15 s** しかなく、その中で FR-1 の最低3サンプルを取り切る必要がある
- **実機（Raspberry Pi 4 / RealSense D435）は未セットアップである**
- コードは一行も存在しない

### 何が変わるべきか

- 飛来する空き缶を検出し、**カメラ座標系の3D位置サンプル列**として出力できる
- 検出方式を**複数実装して同一データ上で比較でき**、実効サンプル数を根拠に OQ-26 を決着できる
- 検出〜追跡区間の**レイテンシと実効サンプル数を計測値として残せる**
- 人・手・背景などの誤検出を、**下流が残差で棄却できる形**で渡せる
  （棄却の判断そのものは本 Spec が持たない）
- **実機も RealSense SDK も無い環境で**、記録済みセッションと合成入力だけで
  検出・追跡の開発とテストが完結する

---

## 追加入力: 確定済みの方針（A-1〜A-13）

> 本節は Project Description の続きであり、**入力として与えられた確定方針**である。
> 検証可能な形式の要件は下の [`## Requirements`](#requirements) を正とし、
> `A-n` は各要件の**出典参照**として使用する。

### A-1. 上流は `sensing-foundation` の公開入口のみ

入力は `sensing-foundation` が供給するフレーム列であり、`for frame in source.frames():` が唯一の入口になる。

- **公開入口（`sensing_foundation.__init__`）からのみ参照する。** 内部モジュールへ直接 import しない
- 同 Spec の決定を**再定義・分岐させない**（`CaptureFrame` / `StreamProfile` / `CameraIntrinsics` /
  セッション記録形式 / 構造化ログ形式はすべて上流が正）
- **`CaptureFrame.depth` は読み取り専用の `numpy.ndarray`（uint16）である。**
  in-place 変更は Replay の再現性を壊すため行わない（`sensing-foundation` design.md CoreTypes）

### A-2. 座標系の境界 ★最重要

本 Spec は**カメラ座標系までを持ち、World frame への変換を持たない。**

`.kiro/steering/roadmap.md` Boundary Strategy が
「検出はカメラ座標系まで、World への変換はキャリブレーション側が持つ」と明記している。

> ⚠️ **この境界を曖昧にすると誤差の原因を分離できない。**
> `docs/requirements.md §6.2` は「座標系が数 cm ずれていても症状は『予測が悪い』にしか見えない」と
> 警告している。検出誤差と座標系ずれを分離するために、変換は1箇所（`world-frame-calibration`）に置く。

したがって出力は**カメラ座標系の点列**である。
これを受け取って World frame へ変換し `prediction_core.Sample` を構成するのは
**`m1-prediction-validation` の結合層（seam）**である。
`world-frame-calibration` は**変換そのもの（`WorldTransform`）の所有者**であり、
seam がそれを呼ぶ。すなわち本 Spec の出力の直接の消費者は `m1-prediction-validation` である。

また、**画素と Depth からカメラ座標を得る基本演算そのもの（生カウント → mm の換算・
画素中心の規約・ピンホール式・無効画素の判定）は上流 `sensing-foundation` が1箇所で持つ。**
本 Spec と `world-frame-calibration` が別々に実装すると、上記の「数 cm のずれ」が
2経路で食い違う形で現れ、切り分け不能になるためである。
本 Spec は**候補領域内のどの画素をどう代表させるか**という集約方針だけを持つ。

### A-3. 対象物は空き缶に固定

対象は**空き缶（φ65mm 程度の剛体）**であり、任意物体を対象にしない。

- 形状が一定で剛体、Depth に写りやすいという性質を**設計の前提として使ってよい**
- ペットボトル・紙くず等への対応は **OQ-02 の最終決定時に再検討する**（本 Spec では対象外）
- **ゴミの種類判別（分別）は行わない。** `docs/requirements.md §0` でスコープ外と明記されている

> ⚠️ **φ65mm は M1 の実験条件を確定させるための仮値であり、OQ-02 の最終決定ではない。**
> 寸法に依存する判定閾値は**パラメータ化し、固定値として埋め込まない**（`tech.md` 開発標準1）。

### A-4. 軽量方式から実測して選ぶ（OQ-26）

**最初から YOLO 等のニューラルネットを前提にしない**（`docs/development-environment.md §10`）。

初期候補は Depth 差分 / 背景差分 / フレーム間差分 / Motion detection / ROI / 輪郭抽出。
対象が空き缶に固定されたため、Depth 中心の軽量方式で足りる可能性が高い。**まずそこから測る。**

決め方は「実装して同一データで比較し、結果を根拠として記録する」であり、
**議論や印象で選ばない。**

### A-5. 評価軸は実効サンプル数

検出方式の比較は **fps 単体ではなく実効サンプル数**（一定時間窓内に得られた有効な3D位置サンプル数）で行う。

高 fps は取得サンプルを増やす一方、Pi 4 の処理落ち（dropped frame）を招けば逆効果になる
（`docs/development-environment.md §5.1`）。

上流 `sensing-foundation` は**フレーム層の実効サンプル数**（`effective_samples_per_window`）で
解像度・fps を決める。本 Spec が測るのは**その先の、検出・追跡を通過して3D点になった数**である。
**同じ言葉だが対象が違う。**

### A-6. 追跡は最小限から始める

飛翔体が**1個・短時間・単調な軌道**であることから、フレーム間の対応付けは最初は最小限でよい。

- **1投擲 = 1物体**を前提とする。複数同時投擲は対象外
- 多対多のデータアソシエーション・カルマンフィルタ・再識別は初期要件に含めない
- 必要になった場合に拡張を検討する（`tech.md` 開発標準4「まず設定とソフトウェアで詰める」と同じ順序）

### A-7. 誤検出の最終的な棄却判断を持たない

人・手・背景を拾ってしまうことは避けられない。本 Spec は**棄却の最終判断を持たず**、
下流が残差で棄却できる形で渡す。

- 明らかに対象でないもの（サイズ・距離・移動量が想定外）は**検出段で落としてよい**
- しかし「予測が外れたから誤検出だった」の判断は `prediction_core` の残差を使って
  `m1-prediction-validation` が行う
- そのため出力には**判断材料**（候補のサイズ・Depth の有効画素数・信頼度の材料）を添える

### A-8. Pi 4 向けの設計方針を守る

`docs/development-environment.md §4` に従う。

- 低レイテンシ優先 / 必要な **ROI だけ**処理する / 不要な画像コピーを減らす
- **毎フレーム巨大な Point Cloud を作らない**（候補領域の画素だけを逆投影する）
- **RGB が不要なら Depth 中心の構成**にする
- GUI 表示を本番処理の必須要件にしない（headless 運用可能であること）
- **処理時間を段階ごとに測定できる構造にする**

### A-9. 計測は上流のロギング基盤に足す

段階別レイテンシ（`docs/development-environment.md §13.1`）のうち
**Detection latency / Tracking latency は本 Spec が自分で計測する**
（`.kiro/steering/roadmap.md` Shared seams: 「各 Spec が自分の区間を計測する」）。

- `sensing-foundation` の構造化ロガー（NDJSON）を使い、**独自のログ形式を作らない**
- 予約 stage は `system` / `capture` / `record`。本 Spec は **`detect` / `track` を足す**
- 計測は**実行時に無効化でき**、ON/OFF で有意に変化しないこと（`tech.md` 開発標準5）

### A-10. 依存の扱い

- **OpenCV は本 Spec が導入する。** `sensing-foundation` が意図的に導入を見送り、
  「検出が必要とする道具であり `flying-object-tracking` の責務」と明記している
- ⚠️ **`pyproject.toml` の `[project].dependencies` は空のまま維持する。**
  OpenCV / NumPy は **optional extras**（`tracking`）として宣言する
- ⚠️ **前提**: `tests/prediction_core/test_packaging.py` は現状、
  `[project].dependencies` が空であることに**加えて**
  `[project.optional-dependencies]` が**空であること**も表明している。
  この表明を「基本依存は空、かつ extras は許可リスト
  （`sensing` / `tracking` / `calibration` / `m1-viz`）の部分集合」へ緩める改修は
  **`sensing-foundation` が所有する**。**本 Spec は当該テストを変更しない**が、
  本 Spec の extras 追加が成立するのは**その改修が先に landing した後**である
- **`prediction_core` へサードパーティ依存を逆流させない**
- **`prediction_core` の入力契約に RealSense 固有の型を漏らさない**
  （漏らすと simulated 入力が繋がらなくなる。`.kiro/steering/roadmap.md` Shared seams）

### A-11. 実機なしで開発・テストできること

`sensing-foundation` の Replay は **実機も RealSense SDK も無い環境で動く**。
したがって本 Spec の検出・追跡は、**記録済みセッションと合成入力だけで開発・テストが完結する**必要がある。

- 実機到着前に着手できる作業と、実機を要する作業を**明確に分ける**
- 実機を要するのは「実データの記録」「Pi 4 上での実測」であり、
  **アルゴリズムの実装と正しさの検証は実機を要さない**

### A-12. 数値は暫定目標値である

`tech.md` 開発標準1 に従い、**未実測の数値を合否条件にしない。**

- 区間2 の 0.10〜0.15 s、FR-1 の「最低3サンプル」は**いずれも暫定値**である
- FR-1 は「必要サンプル数は M1 の実測で見直す」と明記している
- したがって本 Spec は**閾値を満たすこと**ではなく、**測れること・比較できること**を要件とする

### A-13. 未決のまま残すもの

- **OQ-40**（リポジトリ全体のディレクトリ構成）: 本 Spec は `src/flying_object_tracking/` 1パッケージと
  その配下だけを定める。全体構成は決めない。
  **全 Spec が landing した後も配布名（`[project].name`）は `prediction-core` のままであり、
  wheel には複数パッケージが同居する。この改称は OQ-40 として先送りし、Spec ごとに蒸し返さない**
- **OQ-41**（Python 環境構築・パッケージ管理）: 既存の `pyproject.toml` に乗る。
  **OpenCV を Pi 上で apt / pip のどちらで入れるか**という実測結果を OQ-41 の判断材料として報告する
- **OQ-02**（対象ゴミの最終スコープ）: 判断しない。空き缶固定の前提で進める
- **OQ-27**（Pi 4 継続可否）: 判断しない。detect / track 区間の計測値を材料として提供する

---

## design フェーズで決めるもの（requirements では決めない）

- 検出方式の**具体的なアルゴリズム構成**（差分の取り方・モルフォロジ・輪郭抽出の手順）
- 検出方式を差し替えるための**インターフェース構成**とクラス設計
- ROI の**保持形式**（矩形か、Depth レンジを含む3次元的な絞り込みか）
- 候補領域から代表3D点を求める**代表値の取り方**（重心 / 中央値 / トリム平均）
- 追跡の**対応付け規則**の具体（最近傍か、予測位置ゲートか）
- OpenCV の**どの機能を使うか**、および OpenCV を使わずに済ませられる部分の切り分け
- 検出方式ごとの実装をどこまで共通化するか

---

## 制約（brief.md / steering より継承）

- **Python のみ。** アルゴリズムを TypeScript へ複製しない（`tech.md` 開発標準3）
- 単位は **距離 mm / 時刻 ms**（`docs/requirements.md §6.1`、`structure.md` 命名規約）
- **AI モデルを最初から必須にしない**（OQ-26）
- **fps 単体で評価しない。実効サンプル数で評価する**
- **未実測の数値を合否条件にしない**（`tech.md` 開発標準1）
- **カメラ座標系まで。** World frame への変換を持たない
- **1投擲 = 1物体。** 複数物体の同時追跡は対象外

---

## Introduction

flying-object-tracking は、`sensing-foundation` が供給するフレーム列を入力として、
**飛来する空き缶を検出し、その3D位置をカメラ座標系で求め、フレームを跨いで1投擲の点列にまとめる**部品である。

本 Spec の価値は「検出できること」そのものよりも、
**検出方式を実測で選べる状態を作ること**と、**座標系の境界を1箇所に閉じ込めること**にある。

前者は OQ-26 の決着条件そのものである。`docs/development-environment.md §10` は候補を列挙したまま
「現時点では確定しない」と保留しており、**比較する手段が無いために保留が続いている。**
複数方式を同一データ上で走らせて実効サンプル数で比較できるようにすることが、この保留を解く唯一の道になる。

後者は M1 全体の切り分け能力に関わる。World 変換を本 Spec に持ち込むと、
予測が外れたときに検出誤差なのか座標系ずれなのかを分離できなくなる
（`docs/requirements.md §6.2`）。**したがって本 Spec の出力はカメラ座標系で確定する。**

本 Spec は `world-frame-calibration` と**並行して実装できる**。
両者はいずれも `sensing-foundation` の上に乗り、互いを import しない。

## Boundary Context

- **In scope**:
  - フレーム列からの飛来物体候補の検出（複数方式を差し替え可能な形で実装する）
  - ROI による処理範囲の限定
  - 候補領域と Depth からの **カメラ座標系** 3D位置の算出
  - フレーム間の対応付け（1投擲 = 1物体としての追跡）と、1投擲の点列の確定
  - 検出方式の実測比較（実効サンプル数・取りこぼし・レイテンシ）と、それに基づく OQ-26 の決着
  - detect / track 区間のレイテンシ計測（上流のロギング基盤へ足す形で）
  - 誤検出の判断材料の付与
- **Out of scope**:
  - **カメラ座標系 → World 座標系の変換、床平面推定、World frame の確立**（→ `world-frame-calibration`）
  - 予測・フィッティング・落下地点の算出（→ `prediction-core`）
  - 落下地点の可視化と時間予算の総合評価、M1 完了判定（→ `m1-prediction-validation`）
  - フレーム取得・記録・再生・ロギング基盤そのもの（→ `sensing-foundation`）
  - ゴミの種類判別（分別）
  - 複数物体の同時追跡
  - 誤検出の**最終的な棄却判断**（判断材料の提供までを持つ）
  - 空き缶以外の対象物への対応（→ OQ-02）
  - Pi 4 継続可否の判断（→ OQ-27。材料の提供までを持つ）
- **Adjacent expectations**:
  - `sensing-foundation` は入力元によらないフレーム列と、記録／再生と、構造化ロギングを提供する。
    本 Spec はその**公開入口だけ**を使い、決定を再定義しない
  - `world-frame-calibration` は **World 変換そのもの（`WorldTransform`）と、
    画素 → カメラ座標の基本演算に乗る側**を担当する。**変換の正しさはそちらが担保する**。
    本 Spec が出すカメラ座標系の点を実際に受け取って変換を適用するのは
    `m1-prediction-validation` の結合層である
  - `prediction-core` の入力は `(t, x, y, z)` サンプル列であり、Throw Record スキーマは
    `SCHEMA_VERSION` 1.0 で確定している（`docs/decisions.md` D-8）。**独自スキーマを定義しない**
  - `m1-prediction-validation` は End-to-End の時間予算を評価する。
    本 Spec は detect / track 区間の計測値を提供するにとどまる

## Requirements

### Requirement 1: 入力契約と上流境界

**Objective:** As a 検出・追跡処理の利用者（M1 / キャリブレーション）, I want 入力元（live / recorded / simulated）を区別せずにフレーム列を渡せること, so that 実機の有無に関わらず同一の検出・追跡コードを使い続けられる

_出典: A-1, A-11_

#### Acceptance Criteria

1. The flying-object-tracking shall フレーム列の反復を唯一の入力経路とし、入力元の種別によって処理を分岐させない
2. When live / recorded / simulated のいずれの入力元から同一内容のフレーム列が与えられた場合, the flying-object-tracking shall 同一の検出結果と同一の点列を返す
3. The flying-object-tracking shall 上流が提供する Depth バッファを変更せず、読み取りのみを行う
4. If 上流が提供する Depth バッファを変更しようとする実装が混入した場合, then the flying-object-tracking shall その実装をテストで検出できるようにする
5. The flying-object-tracking shall 上流の公開された入口からのみ上流の機能を参照する
6. The flying-object-tracking shall フレーム取得・記録・再生・ロギング基盤そのものを自身の責務に含めない
7. The flying-object-tracking shall 実機および RealSense SDK が存在しない環境で、検出・3D位置算出・追跡のすべてを実行できる

### Requirement 2: 処理範囲の限定（ROI）

**Objective:** As a Raspberry Pi 4 上で処理を動かす開発者, I want フレーム全体ではなく必要な範囲だけを処理できること, so that 限られた計算資源で低レイテンシを確保できる

_出典: A-8_

#### Acceptance Criteria

1. The flying-object-tracking shall 処理対象範囲を設定として外部から与えられるようにする
2. While 処理対象範囲が指定されている場合, the flying-object-tracking shall その範囲外の画素を検出処理の対象にしない
3. The flying-object-tracking shall 処理対象範囲に距離方向の下限・上限を含められるようにする
4. When 処理対象範囲が指定されていない場合, the flying-object-tracking shall フレーム全体を処理対象として動作する
5. The flying-object-tracking shall 処理対象範囲の指定によって出力される3D位置の座標基準が変化しないようにする
6. The flying-object-tracking shall 毎フレームのフレーム全体に対する点群生成を行わない

### Requirement 3: 飛来物体候補の検出

**Objective:** As a 検出処理の利用者, I want 各フレームから飛来物体の候補領域が得られること, so that その領域から3D位置を求められる

_出典: A-3, A-4, A-7_

#### Acceptance Criteria

1. When フレームが与えられた場合, the flying-object-tracking shall そのフレームにおける飛来物体候補の集合（0個以上）を返す
2. The flying-object-tracking shall 候補ごとに、画像上の位置・大きさ・有効な Depth 画素数を含む判断材料を付与する
3. The flying-object-tracking shall 対象物の想定寸法に基づく候補の絞り込み条件を設定値として与えられるようにし、固定値として埋め込まない
4. If 候補が想定寸法または想定距離の範囲から外れている場合, then the flying-object-tracking shall その候補を除外し、除外した件数を計数する
5. When 有効な候補が1つも得られなかった場合, the flying-object-tracking shall 空の候補集合を返し、例外を送出しない
6. The flying-object-tracking shall 対象物の種類判別（分別）を行わない
7. The flying-object-tracking shall 検出の実行に学習済みモデルを必須としない

### Requirement 4: 検出方式の差し替えと比較

**Objective:** As a OQ-26 を決着させる開発者, I want 複数の検出方式を同一の入力データ上で切り替えて走らせられること, so that 印象ではなく実測結果に基づいて方式を選定できる

_出典: A-4, A-5_

#### Acceptance Criteria

1. The flying-object-tracking shall 複数の検出方式を、同一の入力と同一の出力形式で相互に置き換え可能な形で提供する
2. The flying-object-tracking shall 使用する検出方式を、コードの変更なしに設定として選択できるようにする
3. The flying-object-tracking shall 軽量方式（フレーム間差分・背景差分・Depth に基づく差分を含む）を少なくとも2種類提供する
4. When 同一の記録済みセッションに対して複数の検出方式を実行した場合, the flying-object-tracking shall 方式ごとの結果を同一の指標で比較できる形で出力する
5. The flying-object-tracking shall 比較の主指標を、一定時間窓内に得られた有効な3D位置サンプル数とする
6. The flying-object-tracking shall 比較結果に、取りこぼした（候補を検出できなかった）フレーム数を含める
7. The flying-object-tracking shall 比較結果に、方式ごとの処理時間の代表値とばらつきを含める
8. The flying-object-tracking shall 比較の結論と選定根拠を、後から参照できる形で記録する
9. If 検出方式が実測比較なしに選定された場合, then the flying-object-tracking shall その選定を確定済みとして扱わない

### Requirement 5: カメラ座標系での3D位置の算出

**Objective:** As a キャリブレーション処理の実装者, I want 検出候補に対応する3D位置がカメラ座標系で得られること, so that World frame への変換を1箇所に閉じ込められる

_出典: A-2, A-8_

#### Acceptance Criteria

1. When 候補領域とその領域の Depth が与えられた場合, the flying-object-tracking shall 候補に対応する代表3D位置を算出する
2. The flying-object-tracking shall 算出した3D位置を**カメラ座標系**の値として提供し、距離を mm で表す
3. The flying-object-tracking shall World frame への変換・床平面の推定・カメラ姿勢の推定を自身の責務に含めない
4. The flying-object-tracking shall 3D位置の算出に用いたカメラパラメータの出所を、出力から追跡できるようにする
5. If 候補領域内に有効な Depth 画素が十分に存在しない場合, then the flying-object-tracking shall その候補の3D位置を算出せず、理由とともに無効として扱う
6. The flying-object-tracking shall 3D位置の算出のために候補領域外の画素を逆投影しない
7. The flying-object-tracking shall 各3D位置に、その元となったフレームの時刻を同一の時間基準で付与する
8. The flying-object-tracking shall 画素と距離値からカメラ座標を求める基本演算（無効画素の判定・距離値から mm への換算・画素座標からカメラ座標への逆投影）を自身で実装せず、上流が公開する共通実装を用いる

### Requirement 6: フレーム間追跡と1投擲の点列

**Objective:** As a 予測処理へ点列を渡す利用者, I want フレームを跨いで同一物体として繋がれた点列が得られること, so that 予測に必要な時系列サンプルを構成できる

_出典: A-6, A-7_

#### Acceptance Criteria

1. The flying-object-tracking shall 1投擲につき1つの飛翔体を追跡対象とする
2. When 連続するフレームで候補が得られた場合, the flying-object-tracking shall それらを同一物体として対応付けるか、別物体として扱うかを判定する
3. When 追跡が開始された場合, the flying-object-tracking shall その時点のフレーム時刻を追跡開始時刻として記録する
4. When 新たな3D位置が既存の追跡に対応付けられた場合, the flying-object-tracking shall その点を追跡中の点列へ追加する
5. If 一定フレーム数にわたり対応付け可能な候補が得られない場合, then the flying-object-tracking shall その追跡を終了として扱い、終了理由を付与する
6. If 対応付け候補が複数存在する場合, then the flying-object-tracking shall 決定的な規則で1つを選び、選ばれなかった候補の件数を計数する
7. The flying-object-tracking shall 追跡の途中経過を、追跡が終了する前に逐次取り出せるようにする
8. The flying-object-tracking shall 複数物体の同時追跡を自身の責務に含めない
9. The flying-object-tracking shall 追跡の継続判定に用いる閾値（許容フレーム数・許容移動量）を設定値として与えられるようにする

### Requirement 7: 出力契約と下流への受け渡し

**Objective:** As a 下流 Spec（キャリブレーション / M1）の実装者, I want カメラ座標系の点列を明確な形式で受け取れること, so that World 変換と予測をそれぞれの責務の中で実行できる

_出典: A-2, A-7, A-10_

#### Acceptance Criteria

1. The flying-object-tracking shall 1投擲の追跡結果を、時刻付きカメラ座標3D点の列として提供する
2. The flying-object-tracking shall 出力の各点に、判断材料（候補の大きさ・有効 Depth 画素数・対応付けの確からしさの材料）を添える
3. The flying-object-tracking shall 出力の座標系がカメラ座標系であることを、出力自身から判別できるようにする
4. The flying-object-tracking shall 予測の入力契約に、上流のフレーム表現やカメラ固有の型を含めない
5. The flying-object-tracking shall 予測結果・落下地点・残差を自身の出力に含めない
6. The flying-object-tracking shall 投擲記録のスキーマを新たに定義せず、既に確定しているスキーマに従う
7. The flying-object-tracking shall 出力の各点を、元となったフレームの識別情報から辿れるようにする
8. The flying-object-tracking shall 誤検出であるか否かの最終判断を出力に含めない

### Requirement 8: 区間レイテンシと実効サンプル数の計測

**Objective:** As a 時間予算を実測する開発者, I want 検出と追跡それぞれの処理時間と実効サンプル数を計測値として残せること, so that どの段階が律速かを分離でき、Pi 4 継続可否の判断材料になる

_出典: A-9, A-12_

#### Acceptance Criteria

1. The flying-object-tracking shall 検出区間の処理時間と追跡区間の処理時間を、それぞれ独立した計測値として記録する
2. The flying-object-tracking shall 計測値の記録に上流のロギング基盤を用い、独自のログ形式を定義しない
3. The flying-object-tracking shall 計測値をフレームの時刻および識別情報と突き合わせられる形で記録する
4. The flying-object-tracking shall 一定時間窓内に得られた有効な3D位置サンプル数を計測値として算出する
5. The flying-object-tracking shall 候補を検出できなかったフレーム数と、3D位置の算出に失敗した候補数を区別して計数する
6. The flying-object-tracking shall 計測を実行時に無効化できるようにする
7. While 計測が無効化されている場合, the flying-object-tracking shall 計測値の生成と文字列化を行わない
8. The flying-object-tracking shall 計測の有効・無効による処理時間の差を実測で比較できる手段を提供する
9. The flying-object-tracking shall 計測値の集計・可視化を処理の実行中に行わない
10. The flying-object-tracking shall 未実測の処理時間を合否条件として扱わない

### Requirement 9: 誤検出の扱いと判断材料の提供

**Objective:** As a 予測誤差の原因を切り分ける開発者, I want 誤検出を下流が棄却できる形で受け取れること, so that 検出誤差と予測誤差を分離して評価できる

_出典: A-7_

#### Acceptance Criteria

1. The flying-object-tracking shall 誤検出であるか否かの最終判断を自身の責務に含めない
2. When 想定寸法・想定距離・想定移動量から明らかに外れた候補が得られた場合, the flying-object-tracking shall その候補を検出段で除外し、除外理由を計数する
3. The flying-object-tracking shall 除外した候補の件数と理由の内訳を、計測値として取り出せるようにする
4. The flying-object-tracking shall 出力される点に、下流が信頼度を判断するための材料を付与する
5. If 人・手など対象物以外の動体を追跡してしまった場合, then the flying-object-tracking shall 例外を送出せず、通常の点列として出力する

### Requirement 10: 異常系と縮退動作

**Objective:** As a 実機で処理を走らせる開発者, I want 欠測・遮蔽・設定不備が起きたときの振る舞いが決まっていること, so that 想定外の停止や、誤った値の混入を避けられる

_出典: A-7, A-8_

#### Acceptance Criteria

1. If フレームの Depth に無効値（測距できなかった画素）が含まれる場合, then the flying-object-tracking shall それらを有効画素として扱わない
2. If あるフレームで検出に失敗した場合, then the flying-object-tracking shall 処理を停止せず、次のフレームの処理を継続する
3. If 追跡の途中でフレームが欠落した場合, then the flying-object-tracking shall 欠落を検出し、点列に欠落があったことを記録する
4. If 上流が提供するカメラパラメータが得られない場合, then the flying-object-tracking shall 3D位置の算出を行わず、その旨を明示して失敗する
5. If 設定値が不正である場合, then the flying-object-tracking shall 処理開始前に拒否する
6. The flying-object-tracking shall 欠測を 0 や推定値で埋めず、欠測として扱う

### Requirement 11: 実機なしでの開発・検証可能性

**Objective:** As a 実機到着前に着手する開発者, I want 記録済みデータと合成入力だけで検出・追跡を開発・検証できること, so that ハードウェア待ちで開発が止まらない

_出典: A-11, A-12_

#### Acceptance Criteria

1. The flying-object-tracking shall 実機および RealSense SDK が存在しない環境で、すべての単体テストと結合テストを実行できる
2. The flying-object-tracking shall 既知の3D軌道から生成した合成入力に対して、期待どおりの点列を返すことを検証できる
3. When 同一の記録済みセッションを繰り返し処理した場合, the flying-object-tracking shall 同一の点列を返す
4. The flying-object-tracking shall 実機を必要とする作業と必要としない作業を区別できるようにする
5. The flying-object-tracking shall 実機なしで得られた計測結果を、実機での結論として扱わない旨を出力に明示する

### Requirement 12: 設定と運用

**Objective:** As a Pi 4 と WSL の両方で処理を動かす開発者, I want 設定をコードの外から与えられ、既定値が既成事実化しないこと, so that 環境ごとの調整が実装変更を伴わない

_出典: A-3, A-8, A-12_

#### Acceptance Criteria

1. The flying-object-tracking shall 検出方式・処理対象範囲・絞り込み条件・追跡閾値・計測の有効無効を、コード外から与えられるようにする
2. The flying-object-tracking shall 解決後の設定値を実行開始時に確認できるようにする
3. The flying-object-tracking shall 既定値が必須性能でも達成済み性能でもないことを、設定の説明に明示する
4. The flying-object-tracking shall GUI 表示を処理の実行に必須としない
5. The flying-object-tracking shall 対象物の想定寸法に依存する値を設定として分離し、実装中に固定値として埋め込まない
6. The flying-object-tracking shall 出力・計測結果の保存先をコード外から与えられるようにする

### Requirement 13: 依存関係の維持

**Objective:** As a 既存 Spec の実装を壊したくない開発者, I want 本 Spec の依存追加が既存の依存ゼロ性を壊さないこと, so that 予測コアの決定性とテストの前提が維持される

_出典: A-10_

#### Acceptance Criteria

1. The flying-object-tracking shall 既存の予測コアパッケージへ一切の変更を加えない
2. The flying-object-tracking shall 既存の予測コアパッケージへサードパーティ依存を追加しない
3. The flying-object-tracking shall 追加するサードパーティ依存を、基本の依存一覧ではなく任意の依存グループとして宣言する
4. If 基本の依存一覧が空でなくなった場合, then the flying-object-tracking shall その変更をテストで検出できるようにする
5. The flying-object-tracking shall 上流 Spec のパッケージへ変更を加えない
6. The flying-object-tracking shall 予測の入力契約にカメラ固有の型が漏れていないことをテストで検証する
7. The flying-object-tracking shall 自身が導入するサードパーティ依存の実機導入手段に関する実測結果を、環境構築方針の判断材料として記録する
