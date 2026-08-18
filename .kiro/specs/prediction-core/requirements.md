# Requirements Document

## Project Description (Input)

飛来する物体の3D位置サンプル列から、**床面への落下地点と落下時刻を推定する予測コア**を Python モジュールとして実装する。
詳細な背景・スコープ・制約は [brief.md](./brief.md) を正とする。

### 誰の問題か

落下地点の予測は、このプロジェクトで**最も多くの利用者を持つ部品**である。

- **M1（`m1-prediction-validation`）**: 実データ（RealSense 経由）を流して予測精度を検証する
- **柱1（`trajectory-simulator`）**: 合成データを流して時間予算の成立性を感度分析する
- **M3 以降**: 移動体へ送る目標座標を生成する

### 現状

- 数式は `docs/requirements.md §5-B` で確定している
  （`z(t)=z0+vz·t−½g·t²`、x・y は等速、3点以上で最小二乗、床平面 z=0 との交点）
- **コードは一行も存在しない。** 本 Spec がプロジェクト最初の実装になる
- Throw Record スキーマ（OQ-31）は未定義。Record / Replay の形式（OQ-32）もこれに従う予定
- 実機（Raspberry Pi 4 / RealSense D435）は未セットアップだが、**本 Spec はハード不要**

### 何が変わるべきか

- `(t, x, y, z)` のサンプル列を渡すと落下地点・落下時刻・残差が返る
- **ハードウェア無しで単体テストできる**（既知の放物線を入れれば解析解と一致する）
- 残差を**予測の信頼度**として取り出せる
- **駆動系が早期に動き出せる**よう、初回予測を可能な限り早く出し、以降更新できる
- **Throw Record の最小スキーマ**がここで定義され、下流がそれに従える

---

## 追加入力: 確定済みの方針（A-1〜A-8）

> 本節は Project Description の続きであり、**入力として与えられた確定方針**である。
> 検証可能な形式の要件は下の [`## Requirements`](#requirements) を正とし、
> `A-n` は各要件の**出典参照**として使用する。

### A-1. 早期予測開始

駆動系には着地点へ移動するための時間が必要なため、
**高精度な予測を待つことより、可能な限り早く最初の予測を出すことを優先する。**

**最低3点の有効な `(t, x, y, z)` 観測が得られた時点で、初回の落下地点・落下時刻を生成できること。**

> **「3」の技術的裏付け**: 各軸の未知数は x が (x0, vx)、y が (y0, vy)、z が (z0, vz) の各2個（g は既知）。
> **3点なら残差の自由度が1残るが、2点では厳密解となり残差が恒等的に 0 になる。**
> したがって3点は「ノイズ耐性のため」だけでなく、
> `docs/requirements.md` FR-1 の根拠 (b)「残差を信頼度として使う」を成立させる下限でもある。
>
> ただし **FR-1 は「必要サンプル数は M1 の実測で見直す」としている。**
> 最小サンプル数は**パラメータ化し、既定値を3とする**。定数として埋め込まない。

### A-2. 逐次予測更新

初回予測後、**新しい有効な観測サンプルが追加されるたびに軌道を再推定し、落下地点・落下時刻を更新できること。**

```
観測3点 → 初回予測 → 駆動開始可能
観測4点 → 再予測
観測5点 → 再予測
...
```

**prediction-core 自体は駆動制御を担当しない。** 駆動側が逐次更新された予測値を利用できることを責務とする。

> **既存ドキュメントとの整合**: これは新規要求というより既存の暗黙前提の明文化にあたる。
> `original-features.md §2` の Throw Record 定義は予測欄を
> **「時刻ごとの予測落下地点・予測落下時刻・残差」**としており、
> 柱3 の評価指標にも「**予測が許容誤差内に収束するまでの時間**」がある。
> **どちらも逐次予測が前提でないと成立しない。**

### A-3. 運動モデル

初期方式として、**空気抵抗を無視した放物運動モデル**を使用する。

```
x(t) = x0 + vx * t
y(t) = y0 + vy * t
z(t) = z0 + vz * t - 1/2 * g * t^2
```

- 重力加速度 `g` は**既知のパラメータ**として扱う（定数として埋め込まない）
- 3点以上の観測値から**最小二乗法**によって軌道パラメータを推定する

### A-4. 床との交点と出力

入力座標は **World frame に変換済み**であり、初期実装では**床面を z = 0 と仮定**する。
推定軌道と z = 0 の**未来側**の交点から、少なくとも以下を算出できること。

**出力フィールド名は単位を含める**（`.kiro/steering/structure.md` の命名規約
「距離 mm / 時刻 ms」に従う。mm と m、ms と s の取り違えをコンパイル前に防ぐため）。

| フィールド | 単位 |
|---|---|
| `predicted_hit_x_mm` | mm |
| `predicted_hit_y_mm` | mm |
| `predicted_hit_time_ms` | ms |
| `remaining_time_ms` | ms |
| `estimated_vx_mm_s` | mm/s |
| `estimated_vy_mm_s` | mm/s |
| `estimated_vz_mm_s` | mm/s |
| `residual` | — （フィット品質） |

> **床座標系の確立自体は `world-frame-calibration` の責務**であり、
> prediction-core に RealSense / camera 固有処理を持ち込まない。

### A-5. 入力契約

入力は**デバイス固有型ではなく、時刻付き3次元位置サンプル列 `(t, x, y, z)`** とする。
**live / recorded / simulated のどの入力元でも同じ prediction-core を使用できること。**

### A-6. 処理時間の計測可能性

「最初の予測をいつ出せるか」が重要だが、**Raspberry Pi 4 上での処理時間は未実測**のため、
**現時点では未実測の数値を合否条件にしない**（`tech.md` 開発標準1）。

代わりに **prediction-core の推定処理時間を計測可能にし**、
後続の `m1-prediction-validation` で End-to-End の時間予算を評価できるようにする。

> ⚠️ **構造化ロギング基盤は `sensing-foundation` の責務**であり、そちらは実機待ちで未着手。
> したがって prediction-core は**計測値を戻り値／Throw Record に含めるにとどめ**、
> 記録・集計・送出は下流に委ねる。**ロギング基盤に依存させない**
> （依存させると Wave 0 で着手できなくなる）。

### A-7. 推定不能・異常系

少なくとも以下の場合、正常な予測値ではなく**「予測無効」と判定できること**。

- 有効な観測が3点未満（＝最小サンプル数未満）
- 時刻情報からフィッティングが成立しない
- **未来側**に有効な床面との交点が存在しない
- NaN / Infinity 等の数値異常
- 明らかに不正な入力

> **境界の明確化**: 「予測無効」と「品質が低い」は別物として扱う。
>
> | 区分 | 判定者 |
> |---|---|
> | **構造的に算出不能**（上記5件） | **prediction-core が「無効」と判定する** |
> | **品質が低い**（残差が大きい、収束していない） | **利用側が閾値で判断する。** prediction-core は残差を返すだけ |
>
> これは brief.md の Out of Boundary
> 「誤検出の棄却ポリシーそのもの（閾値の決定）は利用側が決める」と両立する。

### A-8. Throw Record

roadmap / OQ-31 に従い、**prediction-core で Throw Record の最小スキーマを定義する。**

- 入力された観測系列と、予測結果・評価に必要な情報を**後から再現・検証できる形**にする
- A-2 により**1投擲に複数回の予測が発生する**ため、予測は**系列として保持**する
- **`sensing-foundation` 側で別スキーマを独自定義しない。**
  prediction-core が定義した最小契約に後続 Spec が従う（roadmap「別々に決めない」）

> `original-features.md §9` の方針に従い、**最初から完全なスキーマを設計しない。**
> 柱1 が必要とする最小形から始め、**「1投擲＝1レコード」の粒度だけを守る。**

---

## 既存ドキュメントとの矛盾（記録のみ。本 Spec では変更しない）

### D-1. 時間予算表が単発予測を前提にしている

`docs/requirements.md §3` の時間予算表は現在こうなっている。

| # | 区間 | 想定 |
|---|---|---|
| 2 | 検出開始〜**予測確定** | 0.10〜0.15 s |
| 3 | **予測確定**〜移動体が動き出す | 0.05 s |

これは「予測が1回確定 → 送信 → 駆動開始」という**単発予測モデル**である。
A-1 / A-2 を採用すると「3点で初回予測 → **駆動開始** → 以降更新」となるため、
**区間2 は「検出開始〜初回予測」と読み替える必要がある。**

- これは時間予算を**緩める方向**の変更であり、NFR-3（≤ 200 ms）の達成しやすさが変わる
- **本 Spec では `docs/requirements.md` を書き換えない。**
  未実測のまま数値の意味を変えると、後で「どちらが正か」が分からなくなるため
- **M1 実測後（`m1-prediction-validation`）に、実測値とあわせて §3 を更新する**

---

## design フェーズで決めるもの（requirements では決めない）

- スライディングウィンドウを使うか、全観測点を使うか
- window size の具体値
- クラス・インターフェース構成
- NumPy 等の具体的な実装方法
- カルマンフィルタへの将来的な差し替え構造

> **カルマンフィルタは初期要件に含めない。まず最小二乗フィッティングをベースラインとする。**
> 実測時に予想着地点の振れや欠測耐性が問題になった場合に、将来拡張として再検討する。
> これは「すぐに部品を替えず、まず設定とソフトウェアで詰める」という
> `tech.md` 開発標準4 の考え方と一致する。

---

## 制約（brief.md より継承）

- **Python のみ。** TypeScript へ複製しない（`tech.md` 開発標準3）
- **入力を「時刻付き3D点の列」だけに限定する。** RealSense 固有の型・カメラパラメータ・
  ファイル I/O をこの層に持ち込まない
- 単位は **距離 mm / 時刻 ms**（`docs/requirements.md §6.1`）
- **根拠のない固定値を埋め込まない。** 重力加速度・最小サンプル数はパラメータ化する
- **未実測の数値を合否条件にしない**（`tech.md` 開発標準1）

---

## Introduction

prediction-core は、時刻付き3次元位置サンプル列 `(t, x, y, z)` を唯一の入力として、
床面（z = 0）への**落下地点・落下時刻・残差**を推定する予測部品である。

本 Spec の価値は「予測できること」そのものよりも、**予測が一箇所にしか存在しないこと**にある。
M1（実データ）と柱1（合成データ）が同一のコードを呼ぶことで、
`tech.md` 開発標準3 が警告する「検証している実装と本番に載る実装が別物」という状態を構造的に防ぐ。

あわせて **Throw Record の最小スキーマ**（OQ-31）を本 Spec で定義し、
`sensing-foundation` の Record / Replay 形式（OQ-32）がこれに従う関係を確定させる。

本 Spec は**ハードウェアを必要としない**。実機セットアップの完了を待たずに着手・完成でき、
Wave 0 の並行トラックとして成立する。

## Boundary Context

- **In scope**:
  - 放物運動モデルへの最小二乗フィッティングによる軌道パラメータ推定
  - 床平面 z = 0 との未来側交点による落下地点・落下時刻の算出
  - フィット品質を表す残差の算出と提供
  - 最小サンプル数到達時点での初回予測と、以降の逐次更新
  - 構造的に算出不能な場合の「予測無効」判定と理由の提示
  - 予測処理時間の計測値の提供
  - Throw Record 最小スキーマの定義と、その直列化・復元
- **Out of scope**:
  - 物体検出・追跡・3D位置取得（→ `flying-object-tracking`）
  - 座標変換・床平面推定・World frame の確立（→ `world-frame-calibration`）
  - 投擲物理・ノイズ生成・空気抵抗モデルの詳細化（→ `trajectory-simulator` / OQ-33）
  - 可視化（→ `m1-prediction-validation` / `simulator-visualization`）
  - 移動体の駆動制御、目標座標の送信
  - **残差の閾値による採否判定（棄却ポリシー）** — 判断材料の提供までを持つ
  - ファイル・ストレージへの入出力、構造化ロギング基盤（→ `sensing-foundation`）
- **Adjacent expectations**:
  - 入力サンプルは**すでに World frame へ変換済み**であることを前提とする。
    その変換の正しさは `world-frame-calibration` が担保する
  - `sensing-foundation` の記録形式（OQ-32）は、本 Spec が定義する Throw Record に**従う**。
    独自スキーマを別途定義しない
  - `trajectory-simulator` は合成サンプル列の供給側であり、物理モデルの詳細度（OQ-33）を持つ
  - End-to-End の時間予算評価は `m1-prediction-validation` が行う。
    本 Spec は自区間の計測値を提供するにとどまる

## Requirements

### Requirement 1: 入力契約とデバイス非依存性

**Objective:** As a 予測コアの利用者（M1 / 柱1 シミュレータ / 将来の駆動系）, I want デバイス固有の型に依存せず時刻付き3次元位置サンプル列だけで予測を要求できること, so that live / recorded / simulated のどの入力元でも同一の予測コードを再利用できる

_出典: A-5_

#### Acceptance Criteria

1. The prediction-core shall 予測の入力を、時刻付き3次元位置サンプル `(t, x, y, z)` の列のみに限定する
2. The prediction-core shall 入力サンプルの距離を mm、時刻を ms として解釈する
3. When live / recorded / simulated のいずれの入力元から同一内容のサンプル列が与えられた場合, the prediction-core shall 同一の予測結果を返す
4. The prediction-core shall 入力契約に、デバイス固有の型・カメラパラメータ・ファイル入出力を含めない
5. The prediction-core shall 予測の実行にハードウェアの接続を要求しない

### Requirement 2: 放物運動モデルによる軌道推定

**Objective:** As a 予測コアの利用者, I want 観測サンプル列から放物運動の軌道パラメータと残差が得られること, so that 落下地点の算出と予測の信頼度評価の両方を同じ推定結果から行える

_出典: A-3_

#### Acceptance Criteria

1. When 最小サンプル数以上の有効な観測サンプルが与えられた場合, the prediction-core shall x・y を等速、z を重力加速度による等加速度とする放物運動モデルの軌道パラメータ `(x0, vx, y0, vy, z0, vz)` を最小二乗法で推定する
2. The prediction-core shall 空気抵抗を無視した運動モデルを初期方式として使用する
3. When 誤差を含まない理想的な放物線から生成されたサンプル列が入力された場合, the prediction-core shall 数値計算上の丸め誤差の範囲で解析解と一致する軌道パラメータを返す
4. When 軌道パラメータの推定に成功した場合, the prediction-core shall 推定軌道と各観測サンプルとの乖離を表す残差を算出する
5. The prediction-core shall 推定した速度成分を `estimated_vx_mm_s` / `estimated_vy_mm_s` / `estimated_vz_mm_s` として提供する

### Requirement 3: 落下地点・落下時刻の算出

**Objective:** As a 予測コアの利用者, I want 推定軌道と床面の交点として落下地点・落下時刻・残り時間が得られること, so that 駆動系への目標座標生成と予測精度の検証に直接使える

_出典: A-4_

#### Acceptance Criteria

1. The prediction-core shall 入力座標が World frame に変換済みであることを前提とし、床面を z = 0 と仮定する
2. When 軌道パラメータの推定に成功した場合, the prediction-core shall 推定軌道と平面 z = 0 との交点のうち、**最新の観測時刻より後にある最も早い交点**を落下点として算出する
3. When 落下点が算出された場合, the prediction-core shall `predicted_hit_x_mm` / `predicted_hit_y_mm` / `predicted_hit_time_ms` / `remaining_time_ms` / `estimated_vx_mm_s` / `estimated_vy_mm_s` / `estimated_vz_mm_s` / `residual` を含む予測結果を返す
4. The prediction-core shall `predicted_hit_time_ms` を入力サンプルと同一の時間基準で表す
5. The prediction-core shall `remaining_time_ms` を `predicted_hit_time_ms` から最新の観測時刻を引いた値として算出する
6. The prediction-core shall 床座標系の確立・床平面の推定・キャリブレーションを自身の責務に含めない

### Requirement 4: 早期予測開始

**Objective:** As a 駆動系の実装者, I want 高精度な予測の確定を待たずに最小サンプル数の時点で最初の予測が得られること, so that 移動体が着地点へ移動するための持ち時間を最大化できる

_出典: A-1_

#### Acceptance Criteria

1. When 有効な観測サンプルが最小サンプル数（既定 3）に達した場合, the prediction-core shall その時点で初回の予測結果を生成する
2. The prediction-core shall 初回予測の生成にあたって、最小サンプル数を超える観測の到着を待たない
3. When 初回予測が生成された場合, the prediction-core shall それが何サンプル目の観測に基づく予測かを利用側が識別できる形で返す

### Requirement 5: 逐次予測更新

**Objective:** As a 予測コアの利用者, I want 観測が追加されるたびに予測が更新され、その系列を取り出せること, so that 予測の収束の様子を評価でき、駆動側が最新の目標座標を追従できる

_出典: A-2_

#### Acceptance Criteria

1. When 初回予測の生成後に新しい有効な観測サンプルが追加された場合, the prediction-core shall 軌道を再推定し、更新された落下地点・落下時刻・残差を返す
2. The prediction-core shall 1回の投擲に対して生成した予測結果を、生成順を保持した系列として取り出せるようにする
3. The prediction-core shall 各予測結果に、その予測の基準となる観測時刻を含める
4. The prediction-core shall 移動体の駆動制御および目標座標の送信を自身の責務に含めない

### Requirement 6: 予測無効の判定と品質判断の境界

**Objective:** As a 予測コアの利用者, I want 構造的に算出不能な場合が正常値と区別されて返ること, so that 誤った落下地点を有効な予測として駆動系へ流してしまう事態を防げる

_出典: A-7_

#### Acceptance Criteria

1. If 有効な観測サンプルが最小サンプル数に満たない場合, then the prediction-core shall 予測無効と判定する
2. If 観測時刻が縮退しており軌道パラメータを一意に定められない場合, then the prediction-core shall 予測無効と判定する
3. If 最新の観測時刻より後に平面 z = 0 との交点が存在しない場合, then the prediction-core shall 予測無効と判定する
4. If 入力サンプルまたは算出結果に NaN もしくは Infinity が含まれる場合, then the prediction-core shall 予測無効と判定する
5. If 入力が Requirement 1 の入力契約を満たさない場合, then the prediction-core shall 予測無効と判定する
6. When 予測無効と判定された場合, the prediction-core shall 上記のどの理由によるものかを利用側が区別できる形で返す
7. When 予測無効と判定された場合, the prediction-core shall 落下地点・落下時刻を正常な予測値として返さない
8. The prediction-core shall 残差の大きさや予測の収束度に基づく採否判定を行わず、判断材料として残差を返すにとどめる

### Requirement 7: ハードウェア非依存の検証可能性

**Objective:** As a 開発者, I want 実機を接続せずに予測の正しさと誤差挙動を検証できること, so that 実機セットアップの完了を待たずに予測アルゴリズムを完成・改善できる

_出典: brief.md「Desired Outcome」_

#### Acceptance Criteria

1. The prediction-core shall すべての予測機能を、ハードウェアを接続しない環境で検証できるようにする
2. When 解析的に既知の放物線から生成したサンプル列が入力された場合, the prediction-core shall 数値計算上の丸め誤差の範囲で解析解と一致する落下地点・落下時刻を返す
3. When 同一の投擲に対してサンプル数を変えた予測が行われた場合, the prediction-core shall 各予測の落下地点・落下時刻・残差を相互に比較できる形で返す
4. Where 既知の誤差を重畳したサンプル列が与えられる場合, the prediction-core shall サンプル数と予測誤差の関係を利用側が評価できる出力を返す

### Requirement 8: 処理時間の計測可能性

**Objective:** As a M1 の検証担当, I want 予測1回あたりの処理時間が計測値として取り出せること, so that ロギング基盤の完成を待たずに End-to-End の時間予算を後から評価できる

_出典: A-6_

#### Acceptance Criteria

1. When 予測が実行された場合, the prediction-core shall その予測に要した処理時間を ms 単位で計測し、予測結果および Throw Record から取得できるようにする
2. The prediction-core shall 処理時間の計測を、構造化ロギング基盤に依存せずに提供する
3. The prediction-core shall 処理時間の計測を実行時に無効化できるようにする
4. The prediction-core shall 処理時間の記録・集計・外部への送出を自身の責務に含めない
5. The prediction-core shall 処理時間について目標値の充足を判定せず、計測値の提供にとどめる

### Requirement 9: Throw Record 最小スキーマ

**Objective:** As a 下流 Spec（`sensing-foundation` / `m1-prediction-validation` / `trajectory-simulator`）の実装者, I want 1投擲を表す共通スキーマが単一の場所で定義されていること, so that 記録・再生・評価が入力元によらず同じ形で扱え、スキーマが別々に定義される事態を避けられる

_出典: A-8 / OQ-31 / OQ-32_

#### Acceptance Criteria

1. The prediction-core shall 1回の投擲を1つの Throw Record として表現するスキーマを定義する
2. The prediction-core shall Throw Record に、記録ID・ソース種別（live / recorded / simulated）・観測サンプル系列・予測結果系列・予測に使用したパラメータ・予測処理時間を含める
3. When Throw Record を直列化して再度復元した場合, the prediction-core shall 元の内容と等価な Throw Record を返す
4. When 保存された Throw Record の観測サンプル系列を同一パラメータで再入力した場合, the prediction-core shall 記録された予測結果系列と一致する結果を再現する
5. The prediction-core shall Throw Record の構造定義と直列化・復元を責務として持ち、ファイルやストレージへの読み書きを責務に含めない
6. Where 下流の Spec が追加項目を必要とする場合, the prediction-core shall 「1投擲＝1レコード」の粒度を保ったまま項目を追加できるスキーマを提供する
7. The prediction-core shall Throw Record スキーマの単一の定義元となり、下流 Spec が参照可能な形で提供する

### Requirement 10: パラメータ化と単位規約

**Objective:** As a プロジェクトの保守者, I want 挙動を左右する数値がパラメータとして公開され単位が名前に現れること, so that M1 の実測結果に応じて値を見直せ、単位の取り違えを実行前に防げる

_出典: A-1 / A-3 / A-4 / 制約（`tech.md` 開発標準1、`structure.md` 命名規約）_

#### Acceptance Criteria

1. The prediction-core shall 重力加速度と最小サンプル数を外部から指定可能なパラメータとして公開する
2. The prediction-core shall 最小サンプル数の既定値を 3 とし、その導出根拠を併記する
3. If 最小サンプル数として 3 未満の値が指定された場合, then the prediction-core shall その設定を無効として拒否する
4. The prediction-core shall 距離を mm、時刻を ms、速度を mm/s で表し、**距離・時刻・速度を表す**外部公開フィールド名に単位を含める（`residual` のような無次元量はこの限りではない）
5. The prediction-core shall 挙動に影響する数値を、導出根拠を伴わない固定値として埋め込まない
6. When 予測が実行された場合, the prediction-core shall その予測に使用したパラメータ値を結果から追跡できるようにする
