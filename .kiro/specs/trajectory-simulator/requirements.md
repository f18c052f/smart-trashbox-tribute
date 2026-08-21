# Requirements Document

## Project Description (Input)

投擲条件・観測条件・移動体性能をパラメータとして与えると、**「落下時刻までにキャッチ可能範囲へ到達できるか」の成立境界（キャッチ可能領域）を算出する Python バッチシミュレータ**を実装する。
詳細な背景・スコープ・制約は [brief.md](./brief.md) を正とする。

### 誰の問題か

このプロジェクト最大のリスクは、
**「そもそも時間予算内に間に合うのか」（[NFR-1](../../../docs/requirements.md#nfr-1-短時間移動性能-主評価軸)）が、機体を作り終わるまで分からないこと**である。

移動体の製作には部品調達・CAD・3Dプリント・配線・ファームウェアが必要で、
そこまで進んでから「間に合わない」と分かると**手戻りが最も大きい**。
本 Spec は、その判断を**ハード完成前に机上で行うための唯一の手段**である
（`docs/original-features.md` 柱1、`product.md` の柱一覧で「最優先」と位置付けられている）。

### 現状

- 時間予算（総飛行時間 0.6〜1.2s − オーバーヘッド 0.2〜0.3s ＝ 持ち時間 0.3〜1.0s）は
  **すべて設計上の想定値**であり、実測に基づかない（`docs/requirements.md §3`）
- 投擲レイアウト（OQ-01）は未定。**必要横移動量が決まらないと成立性を判断できない**
- 実機（Raspberry Pi 4 / RealSense D435 / 移動体）はいずれも未セットアップだが、**本 Spec はハード不要**
- 上流の `prediction-core` は**実装完了・`main` へマージ済み**（`src/prediction_core/`）。
  予測アルゴリズムは既に存在し、本 Spec が再実装する対象ではない

### 何が変わるべきか

- 投擲条件と移動体性能を与えると、**「間に合う / 間に合わない」の境界が格子状のデータとして得られる**
- `docs/requirements.md §7` の「投擲レイアウトをどう決めれば成立するか」を**机上で設計できる**（OQ-01 の机上検討）
- [NFR-6](../../../docs/requirements.md#nfr-6-到達時の静定) の「停止して待つ / 通過キャッチを許容する」の比較が、
  **実機を壊さずに**試せる
- `docs/drivetrain-spec.md §12` の改善順序7「投擲レイアウトと必要横移動量の見直し」を、
  勘ではなく数値の上で検討できる
- **シミュレータの物理モデルの詳細度（OQ-33）がここで決着する**

---

## 追加入力: 確定済みの方針（A-1〜A-9）

> 本節は Project Description の続きであり、**入力として与えられた確定方針**である。
> 検証可能な形式の要件は下の [`## Requirements`](#requirements) を正とし、
> `A-n` は各要件の**出典参照**として使用する。

### A-1. 予測は再実装せず、`prediction-core` を呼ぶ

`docs/original-features.md`「実装スタックの方針」の原則
**「予測は本番と同一コードを使う。ブラウザ側に複製しない」**を、本 Spec が構造として担保する。

- 予測は `prediction_core` の**公開 API（18シンボル）経由でのみ**利用する
- `SourceKind.SIMULATED` が既に simulated 入力を表現しており、**新たな入力種別を定義しない**
- **Throw Record スキーマ（`SCHEMA_VERSION` 1.0、`docs/decisions.md` D-8）を再定義しない**

> ⚠️ これを守らないと、シミュレータが検証しているのは「シミュレータ用の予測実装」であって
> **実際に Pi に載る実装ではない**という状態になる。しかも**ズレに気付けない**
> （`tech.md` 開発標準3）。

### A-2. `prediction-core` の依存ゼロ設計を壊さない

`prediction_core` は**実行時のサードパーティ依存ゼロ**で設計されている。
理由は速度ではなく、**BLAS 実装差による非決定性が Replay 再現を弱める**ためである
（roadmap「`prediction-core` の設計上の要点」）。

- 本 Spec は `prediction_core` 側へ依存を押し返さない
- 本 Spec 自身の実装方針は design フェーズで決めるが、
  **Replay の決定性を弱める選択をしない**ことは要件とする

### A-3. 物理モデルは最小限から始める（OQ-33 の決着）

`docs/open-questions.md` OQ-33 は「**最初は最小限**とし、M1・M2 の実測で不足が分かってから足す」としている。
本 Spec はこれを次の形で決着させる。

| 区分 | 初期モデルに**含める** | 初期モデルに**含めない** |
|---|---|---|
| 投擲物理 | 空気抵抗を無視した放物運動、投擲条件のばらつき | 空気抵抗、回転（マグヌス効果）、跳ね返り |
| 観測 | 検出開始遅れ、サンプリング周期、位置ノイズ、欠測 | センサ固有の歪み、視野外判定、遮蔽 |
| 移動体 | 加速度上限・最高速度・減速による**性能上限の近似** | ホイールスリップ、方向依存の性能差、逆運動学、PID |

- **含めなかったことを結果に明示する**。「入れていないものが入っているように見える」状態を作らない
- 追加が必要になる契機は M1・M2 の実測であり、**その時点で改めて判断する**

### A-4. 移動体の性能パラメータをハードコードしない ★

`tech.md` 開発標準1 の「**根拠のない固定値をコードに埋め込まない**」を、本 Spec では特に厳格に適用する。

> ⚠️ **2026-08-21 時点の入力**: `docs/drivetrain-spec.md §1` が確定としている
> Nexus **14145**（60mm オムニホイール）は、**供給遅延により Nexus 14148（48mm）へ代替される可能性がある。
> これは未決であり、どちらになるか決まっていない。**
>
> したがって `docs/drivetrain-spec.md §3.1` の **1.66 m/s（60mm・無負荷 530RPM の理論周速）も、
> ホイール径 60mm も、モデルへ固定値として埋め込んではならない。**
> **最高速度・加速度上限・ホイール径はすべて外部から与える入力**とする。
>
> なお、この供給事情は `docs/` にまだ反映されていない。**`docs/` の更新は本 Spec の責務ではない**
> （`structure.md` の単一情報源ルールに従い、確定した時点で駆動系の正である `drivetrain-spec.md` が更新される）。

### A-5. 出力の本命は「キャッチ可能領域」

`docs/original-features.md` 柱1 は、**軌跡のアニメーションではなくキャッチ可能領域の可視化**が
最も価値のある出力だと明記している。

```
    必要横移動量 [m]
      ^
  0.8 |  ×  ×  ×  ×          × = 間に合わない
      |  ×  ×  ×  ○          ○ = 間に合う
  0.4 |  ×  ○  ○  ○
      |  ○  ○  ○  ○
  0.0 +--------------------> 持ち時間 [s]
        0.3  0.5  0.7  0.9
```

本 Spec は**この図のもとになる数値データまで**を責務とし、**描画は責務としない**
（描画は `simulator-visualization`。ブラウザ側にアルゴリズムを置かない）。

### A-6. ⚠️ 最大の落とし穴 — 誤った安心を出力しない

**シミュレータは前提を入れた分しか返さない。**
楽観的なパラメータを入れれば「余裕で間に合う」という誤った安心が出力される。
これは実機を作った後に発覚すると最も痛い（`docs/original-features.md`「⚠️ 最大の落とし穴」）。

| 段階 | シミュレータの位置付け |
|---|---|
| M1・M2 前 | 感度分析の道具。**絶対値を信用しない** |
| M1 実測後 | 検出遅れ・予測誤差の実測値を反映 |
| M2 実測後 | 加速度・最高速度・スリップの実測値を反映 |
| 較正後 | レイアウト設計・改善検討に使える |

**この運用ルールを注意書きとしてではなく、出力データの必須項目として強制する。**
出力を読んだ人間が段階を知らないまま数値を信用する経路を残さない。

### A-7. 常駐サーバもブラウザも要らない

`docs/original-features.md`「Hono を今は使わない」に従い、
**Python がバッチ計算して JSON を出力し、ブラウザはそれを読むだけ**の構成とする。
HTTP エンドポイントを書く用途が存在しない（`tech.md`「使いたい技術に用途を作らない」）。

### A-8. OQ-01（投擲レイアウト）の机上検討

`docs/open-questions.md` は OQ-01 を★（後続が止まる項目）とし、
「**柱1 シミュレータの入力パラメータでもあるため、着手時に仮値を置く → M1 実測で確定**」としている。

- 本 Spec は**レイアウト候補の成立性を比較できる出力**を生成する
- **レイアウトを確定させない。** 確定は M1 実測後であり、本 Spec は仮値の候補と根拠の提示にとどめる

### A-9. 未実測の数値を合否条件にしない

`tech.md` 開発標準1 に従い、本 Spec が扱う数値は**すべて暫定目標値**として扱う。

- キャッチ成立割合は「与えたパラメータ前提での算出値」であり、**[NFR-7](../../../docs/requirements.md#nfr-7-キャッチ成功率-最終的な合否条件) の達成度ではない**
- シミュレータは**成立性を評価する道具**であって、**合否を宣告する装置ではない**

---

## 決着させる未決事項 / 未決のまま残すもの

| ID | 事項 | 本 Spec での扱い |
|---|---|---|
| **OQ-33** | シミュレータの物理モデルの詳細度 | **決着させる**（A-3）。決着内容の `docs/decisions.md` への移行は、`prediction-core` の OQ-31 と同じく実装完了後の別作業とする |
| **OQ-01** ★ | 投擲レイアウトの定義 | **机上検討の材料を生成する**（A-8）。**確定はしない** |
| OQ-04 | キャッチ時の減速方針（NFR-6） | **決着させない。** 両方針を比較可能にするにとどめる（判断は M3 まで） |
| OQ-02 | 対象とするゴミの種類・寸法 | M1 の仮値「空き缶（φ65mm 程度の剛体）」をパラメータの既定値として用いる。**最終スコープは決めない** |
| OQ-40 | リポジトリのディレクトリ構成 | **決着させない。** 本 Spec が確定させるのは自身のパッケージ配置のみ |
| OQ-41 | Python の環境構築・パッケージ管理 | **決着させない。** 既存リポジトリの構成に相乗りし、実機 Pi 上の事情による判断を先取りしない |

---

## design フェーズで決めるもの（requirements では決めない）

- モジュール分割・クラス構成・公開 API の形
- 掃引の格子定義の表現方法、および評価の並列化の是非
- 乱数の実装手段と、格子点ごとの種の導出方法
- 出力 JSON の具体的なキー名とネスト構造
- 移動体の追従則の具体形（時間最適プロファイルの解き方）

---

## 制約（brief.md / steering より継承）

- **Python のみ。** ブラウザ側にアルゴリズムを置かない（`tech.md` 開発標準3）
- **Hono を使わない。** 常駐サーバ・HTTP API を持たない（A-7）
- **予測を再実装しない。** `prediction_core` の公開 API のみを使う（A-1）
- **Throw Record スキーマを再定義しない**（`docs/decisions.md` D-8）
- 単位は **距離 mm / 時刻 ms**（`structure.md` 命名規約）
- 対象物は**空き缶**（φ65mm 程度の剛体）を仮値とする
- **根拠のない固定値を埋め込まない**（A-4）
- **未実測の数値を合否条件にしない**（A-9）

---

## Introduction

trajectory-simulator は、投擲条件・観測条件・移動体性能をパラメータとして受け取り、
**物理 → 観測 → 予測 → 移動 → 判定**の経路を通して「落下時刻までにキャッチ可能範囲へ到達できるか」を評価し、
その結果をパラメータ掃引の格子として出力する Python バッチシミュレータである。

本 Spec の価値は「シミュレーションできること」そのものよりも、
**機体を作る前に成立性の境界が見えること**にある。
時間予算が足りるかどうかは、現状すべて想定値の上に成り立っており、
実機が完成するまで検証手段が存在しない。本 Spec はその検証手段をハード無しで先に用意する。

同時に本 Spec は、**予測を二重実装しない構造**を実際に成立させる最初の事例でもある。
`prediction-core` は既に `main` へマージ済みであり、本 Spec はそれを**呼ぶ側**として接続する。
`development-environment.md §7` の「入力元が live / recorded / simulated のどれでも下流は同じ」を、
simulated 側から実証する位置付けにある。

本 Spec は**ハードウェアを必要としない**。実機セットアップの完了を待たずに着手・完成でき、
Wave 1 のハード不要トラックとして成立する。

## Boundary Context

- **In scope**:
  - 投擲物理モデル（放物運動による真の軌道・真の落下地点・真の落下時刻の生成、投擲条件のばらつき）
  - 観測モデル（検出開始遅れ、サンプリング周期、位置ノイズ、欠測の模擬）
  - `prediction-core` への接続（合成サンプル列を渡し、逐次予測系列を受け取る）
  - 移動体の運動モデル（加速度上限・最高速度・減速・制御周期・指令反映遅れによる性能上限の近似）
  - キャッチ成立の判定と、停止方針 / 通過方針（NFR-6）の比較
  - パラメータ掃引とキャッチ可能領域の算出
  - 結果の JSON 出力（較正段階と全パラメータ値を含む）
  - 投擲レイアウト候補の成立性比較（OQ-01 の机上検討）
- **Out of scope**:
  - 予測アルゴリズムそのもの（→ `prediction-core` を呼ぶだけ。再実装しない）
  - Throw Record スキーマの定義・改変（→ `prediction-core` が単一定義元）
  - ブラウザ表示・軌跡アニメーション・キャッチ可能領域の描画（→ `simulator-visualization`）
  - HTTP API・常駐サーバ（用途が存在しない）
  - 空気抵抗・回転・ホイールスリップ・逆運動学・PID の精密モデル化（→ OQ-33 の決着により初期は含めない）
  - 移動体の制御アルゴリズムそのもの（運動モデルは性能上限の近似にとどめる）
  - 実機の較正値の取り込み（M1 / M2 実測後に別途反映する）
  - 物体検出・追跡・座標変換・床平面推定（→ `flying-object-tracking` / `world-frame-calibration`）
  - 投擲レイアウト（OQ-01）・減速方針（OQ-04）・NFR-7 目標値（OQ-05）の**確定**
  - `docs/` の更新（決着した OQ の `decisions.md` への移行を含む）
- **Adjacent expectations**:
  - `prediction-core` は実装済みであり、**公開 API と Throw Record スキーマは本 Spec から見て確定済みの契約**である
  - `simulator-visualization` は本 Spec が出力した JSON を**読んで描画するだけ**であり、
    アルゴリズムを持たない。本 Spec は描画側の都合を出力形式に持ち込まない
  - `sensing-foundation` の入力層抽象に対し、本 Spec の出力が simulated data として繋がることを想定するが、
    **今回は接続まで作り込まない**
  - 出力される数値は M1 / M2 の実測で較正されるまで「参考」扱いであり、
    この位置付けは利用側の運用ではなく**出力データ自身が保持する**

## Requirements

### Requirement 1: 投擲物理モデルによる真の軌道の生成

**Objective:** As a 成立性を評価する開発者, I want 投擲条件から真の軌道・真の落下地点・真の落下時刻が得られること, so that 予測誤差と移動性能の評価に必要な「正解」を持てる

_出典: A-3 / brief.md「Boundary Candidates: 投擲物理」_

#### Acceptance Criteria

1. The trajectory-simulator shall リリース位置・初速・仰角・方位角・重力加速度を投擲条件として受け取り、空気抵抗を無視した放物運動として真の軌道を生成する
2. When 投擲条件が与えられた場合, the trajectory-simulator shall 真の軌道と床面 z = 0 との未来側の交点から、真の落下地点と真の落下時刻を算出する
3. Where 投擲条件にばらつきが指定される場合, the trajectory-simulator shall 試行ごとに初速・仰角・方位角・リリース位置を指定された範囲で変動させる
4. The trajectory-simulator shall 重力加速度・対象物の代表寸法を含む物理パラメータを外部から指定可能とし、根拠のない固定値として埋め込まない
5. The trajectory-simulator shall 空気抵抗・回転・跳ね返りを初期の物理モデルに含めず、含めていないことを結果から判別できるようにする
6. If 与えられた投擲条件に対して床面との未来側の交点が存在しない場合, then the trajectory-simulator shall そのシナリオを評価対象外とし、理由を判別できる形で報告する
7. The trajectory-simulator shall 任意の時刻における真の位置を算出できるようにする

### Requirement 2: 観測モデルによる合成サンプル列の生成

**Objective:** As a 成立性を評価する開発者, I want 検出遅れ・サンプリング周期・ノイズ・欠測を模擬した観測サンプル列が得られること, so that 「センサがこの程度の性能なら予測はどこまで当たるか」を実機なしで評価できる

_出典: A-3 / brief.md「Boundary Candidates: 観測モデル」/ `docs/requirements.md §3` 区間1・NFR-4_

#### Acceptance Criteria

1. The trajectory-simulator shall リリースから検出開始までの遅れをパラメータとして受け取り、その時刻以降のみを標本化の対象とする
2. The trajectory-simulator shall 標本化の周期をパラメータとして受け取り、その周期で真の軌道を標本化する
3. The trajectory-simulator shall 各サンプルの位置に、軸ごとに指定された大きさの観測ノイズを付与する
4. Where 距離に依存するノイズ係数が指定される場合, the trajectory-simulator shall 観測原点からの距離に応じてノイズの大きさを増加させる
5. Where 欠測率が指定される場合, the trajectory-simulator shall その割合でサンプルを脱落させ、残ったサンプルのみを予測へ渡す
6. The trajectory-simulator shall 観測サンプルを、距離 mm・時刻 ms の時刻付き3次元位置の列として表す
7. When 観測モデルの誤差要因がすべて無効化された場合, the trajectory-simulator shall 真の軌道と一致するサンプル列を生成する
8. The trajectory-simulator shall センサ固有の歪み・視野外判定・遮蔽を初期の観測モデルに含めず、含めていないことを結果から判別できるようにする

### Requirement 3: prediction-core の再利用と予測の非複製

**Objective:** As a プロジェクトの保守者, I want シミュレータが本番と同一の予測コードを呼ぶこと, so that 「シミュレータでは合っていたのに実機で外れる」という二重実装由来の失敗を構造的に防げる

_出典: A-1 / A-2 / `tech.md` 開発標準3 / `docs/decisions.md` D-8_

#### Acceptance Criteria

1. The trajectory-simulator shall 落下地点・落下時刻の予測を、`prediction-core` の公開 API 経由でのみ行う
2. The trajectory-simulator shall 放物運動フィッティング・床面交点算出・残差算出を自身の内部に再実装しない
3. The trajectory-simulator shall 生成した観測サンプル列を simulated 由来の入力として扱う
4. The trajectory-simulator shall Throw Record のスキーマを再定義・改変せず、`prediction-core` が定義したものをそのまま用いる
5. When `prediction-core` が予測無効と判定した場合, the trajectory-simulator shall その理由を保持し、当該予測を有効な予測として扱わない
6. The trajectory-simulator shall `prediction-core` に対して新たな実行時依存を発生させない
7. The trajectory-simulator shall 予測を逐次更新として扱い、初回予測以降の更新を移動体の目標座標に反映する
8. The trajectory-simulator shall `prediction-core` の内部モジュールへ直接依存せず、公開 API の入口からのみ参照する

### Requirement 4: 移動体の運動モデル

**Objective:** As a 駆動系の設計者, I want 移動体の性能を外部パラメータとして与えて到達性能を評価できること, so that ホイール径やモータが変わっても同じ道具で成立性を再評価できる

_出典: A-4 / `docs/drivetrain-spec.md §3.1` / [NFR-1](../../../docs/requirements.md#nfr-1-短時間移動性能-主評価軸) / [NFR-2](../../../docs/requirements.md#nfr-2-加速安定性)_

#### Acceptance Criteria

1. The trajectory-simulator shall 最高速度・加速度上限・減速度上限・制御周期・指令反映遅れを、外部から指定可能なパラメータとする
2. The trajectory-simulator shall ホイール径とモータ回転数から最高速度を導出する手段を提供し、導出に用いた値を結果から追跡できるようにする
3. The trajectory-simulator shall 最高速度・加速度上限・ホイール径を、根拠のない固定値としてモデルに埋め込まない
4. The trajectory-simulator shall 移動体を並進のみの質点として扱い、加速度上限と最高速度に制限された運動として目標座標へ追従させる
5. When 予測が更新された場合, the trajectory-simulator shall 制御周期と指令反映遅れを経たのちに、その予測を新しい目標座標として反映する
6. The trajectory-simulator shall ホイールスリップ・方向依存の性能差・逆運動学・速度制御の内部構造を初期の運動モデルに含めず、含めていないことを結果から判別できるようにする
7. The trajectory-simulator shall 任意の時刻における移動体の位置と速度を算出できるようにする
8. Where 異なるホイール径の機体パラメータが与えられる場合, the trajectory-simulator shall 同一の掃引をそれぞれのパラメータで実行し、結果を比較できるようにする

### Requirement 5: キャッチ判定と減速方針の比較

**Objective:** As a NFR-6 の判断を控えた設計者, I want 「停止して待つ」と「通過キャッチを許容する」を同じ条件で比較できること, so that 実機を壊さずに方針の損得を評価できる

_出典: brief.md「Desired Outcome」/ [NFR-5](../../../docs/requirements.md#nfr-5-位置精度暫定目標) / [NFR-6](../../../docs/requirements.md#nfr-6-到達時の静定) / OQ-04_

#### Acceptance Criteria

1. The trajectory-simulator shall 目標点で静止して待つ方針と、通過キャッチを許容する方針の双方を選択可能とする
2. When 停止して待つ方針が選択されている場合, the trajectory-simulator shall 目標点で速度が 0 になる減速を含めた運動として評価する
3. When 真の落下時刻に到達した場合, the trajectory-simulator shall 真の落下地点と移動体位置の水平距離、および移動体の残留速度を算出する
4. The trajectory-simulator shall キャッチ成立の判定に用いる水平位置の許容誤差と残留速度の許容値を外部から指定可能なパラメータとし、既定値には導出根拠を併記する
5. When 水平距離が許容誤差以内であり、かつ選択された方針の残留速度条件を満たす場合, the trajectory-simulator shall そのシナリオをキャッチ成立と判定する
6. The trajectory-simulator shall 同一の投擲条件・機体条件に対する両方針の結果を、比較できる形で出力する
7. The trajectory-simulator shall キャッチ後の跳ね返りの有無を評価に含めない
8. The trajectory-simulator shall 減速方針そのものを確定させず、比較材料の提示にとどめる

### Requirement 6: パラメータ掃引とキャッチ可能領域の算出

**Objective:** As a 成立性を評価する開発者, I want 任意のパラメータを軸に掃引して成立境界が格子データとして得られること, so that 「どこまでなら間に合うか」を1枚の図に落とせる材料が手に入る

_出典: A-5 / brief.md「Desired Outcome」/ `docs/original-features.md` 柱1_

#### Acceptance Criteria

1. The trajectory-simulator shall 任意のパラメータを軸とする格子状の掃引を定義できるようにする
2. The trajectory-simulator shall 持ち時間と必要移動量を軸とし、移動体の運動モデルのみで到達可否を判定する掃引を提供する
3. The trajectory-simulator shall 投擲条件・観測条件・機体条件を軸とし、物理・観測・予測・運動の全経路を通した掃引を提供する
4. Where 1格子点あたりの試行回数が 2 以上指定される場合, the trajectory-simulator shall 各格子点でその回数の試行を行い、成立割合を算出する
5. The trajectory-simulator shall 各格子点の結果に、成立か不成立かの別と、その判定に用いた指標値を含める
6. The trajectory-simulator shall 掃引の全格子点の結果を、1つの成果物としてまとめて出力する
7. If 格子点の評価が Requirement 1.6 により評価対象外となった場合, then the trajectory-simulator shall 当該格子点を不成立と混同せず、評価対象外として区別する
8. The trajectory-simulator shall 掃引結果の描画を自身の責務に含めない

### Requirement 7: 結果の出力

**Objective:** As a 下流の利用者（`simulator-visualization` / レイアウトを検討する人間）, I want 掃引結果と前提パラメータが1つのファイルから読めること, so that 常駐サーバなしで表示・再検討ができる

_出典: A-5 / A-7 / brief.md「Approach」_

#### Acceptance Criteria

1. The trajectory-simulator shall 掃引結果を JSON として出力する
2. The trajectory-simulator shall 出力に、その掃引で使用した全パラメータ値を含める
3. The trajectory-simulator shall 出力に自身の出力形式の版を含め、Throw Record のスキーマ版とは異なる名前で表す
4. Where 代表シナリオの記録が要求される場合, the trajectory-simulator shall `prediction-core` が定義する Throw Record の形式で観測サンプル系列と予測系列を出力する
5. The trajectory-simulator shall 出力を、常駐サーバや HTTP エンドポイントを介さずファイルとして得られるようにする
6. The trajectory-simulator shall 出力に、各軸の名前・単位・値の並びを含め、読み手が軸の意味を判別できるようにする
7. The trajectory-simulator shall 出力の距離を mm、時刻を ms として表し、これらを表す項目名に単位を含める

### Requirement 8: 決定性と再現性

**Objective:** As a 結果を検証する開発者, I want 同じ設定なら何度実行しても同じ結果が出ること, so that 掃引結果の差がパラメータの差によるものだと言い切れる

_出典: A-2 / `tech.md` 開発標準6 / `prediction-core` 要件 9.4_

#### Acceptance Criteria

1. The trajectory-simulator shall 乱数の種を外部から指定可能とする
2. When 同一の設定と同一の種で掃引を 2 回実行した場合, the trajectory-simulator shall 同一の出力を返す
3. The trajectory-simulator shall 各試行に用いる乱数列を格子点と試行番号から決定的に導出し、評価の順序に依存しないようにする
4. The trajectory-simulator shall `prediction-core` の Replay 再現性を弱める要素を予測経路に持ち込まない
5. The trajectory-simulator shall すべての機能を、ハードウェアを接続しない環境で実行・検証できるようにする
6. When 出力された Throw Record を `prediction-core` の Replay へ入力した場合, the trajectory-simulator shall 記録された予測系列と一致する結果が再現されるようにする

### Requirement 9: 較正段階の明示と誤った安心の防止

**Objective:** As a 数ヶ月後にこの出力を読む人間, I want その数値がどこまで信用できるかが出力自体に書かれていること, so that 未較正の楽観値を根拠に設計を進めてしまう事態を防げる

_出典: A-6 / A-9 / `docs/original-features.md`「⚠️ 最大の落とし穴」/ `tech.md` 開発標準1_

#### Acceptance Criteria

1. The trajectory-simulator shall 出力に較正段階（未較正 / M1 実測反映済み / M2 実測反映済み）を必須項目として含める
2. The trajectory-simulator shall 較正段階の既定値を未較正とする
3. While 較正段階が未較正である場合, the trajectory-simulator shall 結果が感度分析用であり絶対値を信用してはならない旨を出力に含める
4. The trajectory-simulator shall 各パラメータについて、実測に基づく値か想定値かの別を出力に含める
5. The trajectory-simulator shall NFR-1 や NFR-7 の充足を断定する判定結果を出力に含めない
6. The trajectory-simulator shall キャッチ成立割合を、最終的な合否条件の達成度としてではなく、与えられたパラメータ前提での算出値として提示する
7. The trajectory-simulator shall 初期モデルに含めていない要因（Requirement 1.5 / 2.8 / 4.6）の一覧を出力に含める

### Requirement 10: 投擲レイアウトの机上検討

**Objective:** As a OQ-01 を決める立場の開発者, I want レイアウト候補ごとの成立性を並べて比較できること, so that 投擲位置・待機位置を勘ではなく数値の上で仮決めできる

_出典: A-8 / OQ-01 / `docs/drivetrain-spec.md §12` 改善順序7_

#### Acceptance Criteria

1. The trajectory-simulator shall 投擲位置・投擲方向・投擲距離・移動体の待機位置をレイアウトのパラメータとして受け取る
2. When 待機位置と真の落下地点が定まった場合, the trajectory-simulator shall 両者の水平距離を必要移動量として算出する
3. The trajectory-simulator shall 複数のレイアウト候補に対して掃引を実行し、候補間で成立性を比較できる出力を生成する
4. The trajectory-simulator shall 成立する候補と成立しない候補の境界が読み取れる形で結果を出力する
5. The trajectory-simulator shall 投擲レイアウトを確定させず、仮値の候補とその根拠の提示にとどめる

### Requirement 11: 実行境界と依存の制約

**Objective:** As a プロジェクトの保守者, I want シミュレータが越えてはいけない境界が検証可能な形で固定されていること, so that 「気付かないうちに二重実装や依存が増えていた」という劣化を防げる

_出典: A-1 / A-2 / A-7 / `tech.md` 開発標準3 / `docs/original-features.md`「Hono を今は使わない」_

#### Acceptance Criteria

1. The trajectory-simulator shall 物理・観測・予測・掃引のすべてを Python で実装し、他言語へ複製しない
2. The trajectory-simulator shall 常駐サーバおよび HTTP API を持たない
3. The trajectory-simulator shall 実行時のサードパーティ依存を追加しない
4. The trajectory-simulator shall 実機・カメラ・ネットワークへの接続を必要としない
5. The trajectory-simulator shall 予測アルゴリズムの非複製と依存方向の制約を、静的に検証できるようにする
6. If 上記の境界に反する変更が加えられた場合, then the trajectory-simulator shall その検証が失敗することで違反を検出できるようにする
