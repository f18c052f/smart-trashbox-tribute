# Requirements Document

## Project Description (Input)

`trajectory-simulator` が出力した掃引結果 JSON を、開発PC のブラウザで読み込み、
**キャッチ可能領域の図**と**軌跡アニメーション**として表示するビューアを実装する。
詳細な背景・スコープ・制約は [brief.md](./brief.md) を正とする。

### 誰の問題か

`trajectory-simulator` の成果物は JSON である。
格子点ごとの `status` と `success_ratio` が並んだ数値の列を読んで
**キャッチ可能領域の境界がどこにあるか**を把握するのは現実的でない。

[docs/original-features.md 柱1](../../../docs/original-features.md) は、
シミュレータの出力として最も価値があるのは軌跡アニメーションではなく
**キャッチ可能領域の可視化**であると明記し、その図があれば
[OQ-01 投擲レイアウト](../../../docs/open-questions.md) を机上で設計できるとしている。
本 Spec はその図を作る。

加えてこのプロジェクトは現時点で**外から見て何も動かない**。
可視化は M1〜M4 の中で**唯一デモとして成立する部分**でもある。

### 現状

- 上流 `trajectory-simulator` の Spec は生成済みで、出力 JSON の構造
  （`output_schema_version` / `calibration` / `model_exclusions` / `sweep` / `parameters` /
  `parameter_provenance` / `cells` / `throw_records`）は**確定した契約**として与えられている
- 本 Spec の実装コードは一行も存在しない。**本 Spec がこのリポジトリ初の TypeScript 面**になる
- 実装手段（Canvas / SVG / WebGL）が未確定（[OQ-34](../../../docs/open-questions.md)）
- リポジトリ全体のディレクトリ構成（OQ-40）は未決のまま

### 何が変わるべきか

- 掃引結果を開いた人が、**成立する領域と成立しない領域の境界を図として一目で把握できる**
- 同時に、その図が**どこまで信用してよい数値なのか**（較正段階・パラメータの出所・
  モデルに含まれていない要因）を、図と切り離せない形で受け取れる
- 代表シナリオの観測サンプル列と予測の収束が、時間軸に沿って直感的に見える
- **OQ-34（実装手段）がここで決着する**

---

## 追加入力: 確定済みの方針（A-1〜A-8）

> 本節は Project Description の続きであり、**入力として与えられた確定方針**である。
> 検証可能な形式の要件は下の [`## Requirements`](#requirements) を正とし、
> `A-n` は各要件の**出典参照**として使用する。

### A-1. ブラウザ側にアルゴリズムを置かない ★最重要

[docs/original-features.md](../../../docs/original-features.md)「実装スタックの方針」の原則
**「予測は本番と同一コードを使う。ブラウザ側に複製しない」**、
および [tech.md](../../steering/tech.md) 開発標準3 を、本 Spec が**構造として担保する**。

- 物理モデル・予測・掃引・成立判定の**いずれも TypeScript 側に持たない**
- 数値が必要なら Python 側で計算して JSON に載せる。ブラウザは載っている値を描く
- ブラウザが持ってよいのは**描画と、結果を眺めるための軽量な補間だけ**である

> ⚠️ これを破ると「シミュレータでは合っていたのに実機で外れる」の典型的な原因になる。
> 散文の約束では守れないため、**機械的に検査できる形**に落とすことを本 Spec の要件に含める（要件 7）。

### A-2. 入力は上流の出力 JSON のみ

読み込む対象は `trajectory-simulator` が `write_sweep_result` で書き出した JSON 1 ファイルである。
本 Spec は入力形式を**定義しない**。上流の `output_schema_version` に従う側である。

- 独自の中間形式・変換スクリプト・前処理を**挟まない**
- 上流に無い項目を描画側で作り出さない（例: 移動体の走行軌跡は出力 JSON に存在しない）

### A-3. 「誤った安心」を防ぐことが表示の要件である

[docs/original-features.md 柱1 の「最大の落とし穴」](../../../docs/original-features.md)は、
**シミュレータは前提を入れた分しか返さない**こと、
楽観的なパラメータを入れれば「余裕で間に合う」という誤った安心が出力されることを警告している。

上流はこの警告に対して、出力 JSON に
**較正段階・パラメータの出所（実測 / 想定）・モデル除外要因**を必須項目として持たせ、
かつ**合否を断定するキーを出力に含めない**という設計で応えている。

本 Spec はこの意図を**引き継ぐ**。すなわち、

- これらの項目を**隠さず、図と同じ画面に提示する**
- データが述べていない判定（合格 / 不合格 / NFR-7 達成）を**描画側で作らない**

> ⚠️ 図だけを切り出して見た人が前提を知らないまま結論を出す、という事故が
> このプロジェクトで最も痛い失敗である。

### A-4. 最小構成で作る。急がない

ロードマップは本 Spec を**先送り可**とし、着手ウェーブの外に置いて「急がない」と注記している。
`original-features.md` は**「可視化に時間をかけすぎて 1（シミュレータ）の結論が出ない」失敗**を明示的に警告している。

したがって本 Spec は、brief.md の価値
（キャッチ可能領域の図 ＋ 軌跡アニメーション）を満たす**最小の形**に留める。
ダッシュボード・ライブ表示・凝ったビルド基盤を作らない。
**タスク数が少ないことは弱さではなく、この Spec では成功の指標である。**

### A-5. 常駐サーバを持たない。Hono を使わない

[tech.md](../../steering/tech.md) の決定表と `original-features.md`「Hono を今は使わない」に従う。
HTTP API を持つ用途（柱2b ライブダッシュボード）は保留されており、**使う理由が存在しない**。

### A-6. 開発PC 上で動かす。Raspberry Pi 上では動かさない

[structure.md](../../steering/structure.md) Code Organization Principles
「可視化レイヤを Raspberry Pi 上で動かさない」に従う。
実行環境は Windows + WSL2 の開発PC のブラウザである。

### A-7. どのパラメータ集合の結果かを曖昧にしない

上流は、オムニホイール径が未決（60mm Nexus 14145 / 供給遅延による 48mm 代替）であるため、
`drivetrain-wheel60.json` と `drivetrain-wheel48.json` の**2つの機体パラメータ設定を並置**している。

同一形式の JSON が複数存在するため、**表示中の図がどの設定の結果なのか**が
一目で分からないと、2つの掃引結果を見比べたときに取り違える。

### A-8. 未実測の数値を合否条件にしない

[tech.md](../../steering/tech.md) 開発標準1。本 Spec が扱う数値はすべて上流由来の**暫定目標値**であり、
表示上の色分け閾値も**表示の都合**であって合否条件ではない。

---

## Introduction

simulator-visualization は、`trajectory-simulator` が出力した掃引結果 JSON を
開発PC のブラウザで読み込み、**キャッチ可能領域の図**を主たる出力として描画し、
副次的に代表シナリオの**軌跡アニメーション**を再生するビューアである。

本 Spec の価値は「絵が出ること」ではなく、
**成立境界を人が見て議論できる状態にすること**と、
**その図が前提と限界を伴って提示されること**の2点にある。

本 Spec は**表示の終端**である。下流の Spec を持たず、
他の Spec がここに依存することもない。
アルゴリズムを一切持たないため、上流の出力形式が変わらない限り
本 Spec の変更が他へ波及しない。

本 Spec は**ハードウェアを必要としない**。実機セットアップの完了を待たずに着手・完成できる。

## Boundary Context

- **In scope**:
  - `trajectory-simulator` の出力 JSON 1 ファイルの読み込みと、必須項目の検証
  - **キャッチ可能領域（掃引の格子）の描画**（最優先）
  - 較正段階・パラメータ出所・モデル除外要因・判定閾値の提示
  - 表示中の結果がどのパラメータ集合に由来するかの提示
  - 代表 Throw Record に含まれる観測サンプル列と予測系列の時間再生
  - 表示の切り替え・再生操作という範囲の操作 UI
  - 実装手段の決定（OQ-34 の決着）
  - 自身のディレクトリ配置と、ブラウザ側にアルゴリズムを置かないことの機械的検査
- **Out of scope**:
  - **本番にも存在するアルゴリズム**（物理・予測・掃引・成立判定）の実装または移植
  - JSON に存在しない量の算出・推定・補外（移動体の走行軌跡の再現を含む）
  - 上流の出力スキーマの定義・改変、および Throw Record の再定義
  - HTTP API・常駐サーバ・WebSocket 等による実機とのライブ接続（柱2b → OQ-38）
  - 実データのリプレイビューア・投擲アーカイブ（柱3 → OQ-39）
  - 複数結果の同一画面での自動比較・差分表示
  - Raspberry Pi 上での実行
  - リポジトリ全体のディレクトリ構成（OQ-40）と Python の環境構築方法（OQ-41）の確定
  - Python 側パッケージ設定（`pyproject.toml`）への変更
- **Adjacent expectations**:
  - `trajectory-simulator` の出力 JSON は**確定した契約**であり、本 Spec はそれを読む側である。
    出力キーの改名・単位変更・必須項目の増減は、上流 design の Revalidation Trigger に該当し、
    本 Spec の再確認が必要になる
  - 上流は**合否を断定するキーを出力に含めない**。本 Spec はその欠如を欠陥として補わない
  - 上流の代表 Throw Record は掃引設定（`keep_representative_record`）次第で**含まれないことがある**。
    本 Spec は含まれない場合も図の描画を続行できる必要がある
  - 上流の物理モデルには空気抵抗・回転・跳ね返り・センサ歪み・視野外・遮蔽・
    タイムスタンプ揺らぎ・スリップ・方向依存性能・逆運動学・速度制御動特性・跳ね出しが
    **含まれていない**。この事実は出力 JSON 自身が保持しており、表示側はそれを提示する

---

## Requirements

### Requirement 1: 掃引結果 JSON の読み込みと検証

**Objective:** As a 成立性を評価する開発者, I want 手元の掃引結果ファイルを開くと内容が検証された上で表示されること, so that 壊れたデータや前提を欠いたデータを正しい図と誤認しない

_出典: A-2 / A-3 / brief.md「Boundary Candidates: データ読み込み」_

#### Acceptance Criteria

1. The simulator-visualization shall 利用者が選択したローカルの JSON ファイル 1 件を読み込み、その内容だけを表示の入力とする
2. When 読み込んだ JSON に必須項目（出力形式の版・較正段階・モデル除外要因・掃引定義・パラメータ・パラメータ出所・格子点）のいずれかが欠けている場合, the simulator-visualization shall 図を描画せず、欠けている項目名を利用者に提示する
3. If 読み込んだファイルが JSON として解釈できない場合, then the simulator-visualization shall 解釈に失敗した旨を利用者に提示し、直前に表示していた図を残さない
4. When 読み込んだ JSON の出力形式の版が本ビューアが想定する版と一致しない場合, the simulator-visualization shall その不一致を利用者に提示する
5. The simulator-visualization shall 読み込んだ値を表示の目的で保持するにとどめ、入力に存在しない量を算出して補わない
6. The simulator-visualization shall 読み込んだファイルの名前を、表示中の図と併せて提示する
7. Where 掃引結果に代表 Throw Record が含まれない場合, the simulator-visualization shall キャッチ可能領域の図の描画を継続し、軌跡アニメーションが利用できない理由を提示する

### Requirement 2: キャッチ可能領域の表示

**Objective:** As a 投擲レイアウトを検討する開発者, I want 掃引の格子が成立・不成立・評価対象外の区別とともに図として見えること, so that 成立境界がどこにあるかを数値の列を読まずに判断できる

_出典: brief.md「Desired Outcome: キャッチ可能領域の図が表示される（本命の出力）」 / A-4_

#### Acceptance Criteria

1. The simulator-visualization shall 掃引の格子点を、掃引定義が示す軸の値に対応する位置へ配置して描画する
2. The simulator-visualization shall 各格子点の状態（成立 / 不成立 / 評価対象外）を視覚的に区別できる形で描画する
3. The simulator-visualization shall 各軸の名前・単位・値を図の軸ラベルとして提示する
4. When 格子点に成立割合が含まれる場合, the simulator-visualization shall その値を格子点の表現へ反映し、判定に用いられた閾値を凡例に併記する
5. When 格子点が評価対象外である場合, the simulator-visualization shall 不成立とは異なる表現で描画し、その理由を判別できるようにする
6. When 利用者が個々の格子点を指し示した場合, the simulator-visualization shall その格子点の軸の値・状態・成立割合・指標を提示する
7. Where 掃引の軸が 2 本より多い場合, the simulator-visualization shall 描画に用いる 2 軸を利用者が選べるようにし、残りの軸について現在固定している値を提示する
8. Where 掃引の軸が 1 本の場合, the simulator-visualization shall その 1 軸に沿った並びとして描画する
9. The simulator-visualization shall 色分けや記号の割り当てが**表示上の取り決め**であり合否条件ではないことを凡例上で明示する

### Requirement 3: 前提と限界の提示

**Objective:** As a 図を見て判断する開発者, I want その数値がどこまで信用できるかが図と同じ画面に出ること, so that 前提を知らないまま結論を出す事故を避けられる

_出典: A-3 / `docs/original-features.md` 柱1「最大の落とし穴」_

#### Acceptance Criteria

1. The simulator-visualization shall 較正段階を、図が表示されている間は常に読める位置に提示する
2. While 較正段階が未較正である場合, the simulator-visualization shall 出力に含まれる注意書きを併せて提示する
3. The simulator-visualization shall モデル除外要因を段ごとに、省略や折り畳みによって全項目が読めなくなることのない形で提示する
4. The simulator-visualization shall パラメータの出所（実測 / 想定）を、対応するパラメータと対にして提示する
5. The simulator-visualization shall 成立割合を提示する箇所において、判定に用いた閾値と試行回数を併記する
6. The simulator-visualization shall 合否・達成・NFR-7 の充足に相当する断定的な表現を、画面上のいかなる文言にも用いない
7. The simulator-visualization shall 入力 JSON に含まれない判定・評価・要約を新たに生成しない

### Requirement 4: 結果の同一性の提示

**Objective:** As a 2 通りの機体パラメータを比べる開発者, I want 表示中の図がどのパラメータ集合の結果かが曖昧でないこと, so that 2 つの掃引結果を見比べたときに取り違えない

_出典: A-7 / 上流の `drivetrain-wheel60.json` / `drivetrain-wheel48.json` 並置_

#### Acceptance Criteria

1. The simulator-visualization shall 入力に含まれるパラメータの全体を、利用者が確認できる形で提示する
2. The simulator-visualization shall 機体パラメータ（最高速度・加速度上限・減速度上限・制御周期・指令遅延、および指定されている場合はホイール径）を、図と同じ画面から到達できる位置に提示する
3. The simulator-visualization shall 掃引定義（種別・軸・試行回数・乱数種・閾値）を提示する
4. The simulator-visualization shall 読み込んだファイル名と較正段階を、図と同時に読める位置へ配置する
5. The simulator-visualization shall 複数の掃引結果を同一画面で自動比較する機能を持たない

### Requirement 5: 軌跡アニメーション

**Objective:** As a 予測の挙動を直感的に理解したい開発者, I want 代表シナリオの観測と予測が時間に沿って再生されること, so that 検出開始から予測が収束していく様子を数値表ではなく動きとして把握できる

_出典: brief.md「Desired Outcome: 軌跡アニメーション」 / A-1 / A-2_

#### Acceptance Criteria

1. Where 掃引結果に代表 Throw Record が含まれる場合, the simulator-visualization shall その観測サンプル列を時間の順に再生する
2. The simulator-visualization shall 記録に含まれる各予測の予測落下地点を、その予測が何サンプル目に基づくかとともに提示する
3. While 再生中である場合, the simulator-visualization shall 現在の再生時刻を記録の時間基準に沿って提示する
4. When 記録に無効な予測が含まれる場合, the simulator-visualization shall 有効な予測と区別して提示し、その無効理由を提示する
5. The simulator-visualization shall 再生の開始・停止・先頭復帰・任意時刻への移動を利用者が操作できるようにする
6. The simulator-visualization shall 再生において、記録に含まれる観測点および予測点の間を線形に結ぶ以外の補間を行わない
7. The simulator-visualization shall 記録に含まれない量（移動体の走行位置、真の軌道の連続曲線など）を推定して描画しない
8. When 掃引結果に代表 Throw Record が複数含まれる場合, the simulator-visualization shall 再生対象を利用者が選べるようにする

### Requirement 6: 表示操作

**Objective:** As a レイアウトを検討する開発者, I want 図の見え方を切り替えられること, so that 同じ結果を異なる観点から確認できる

_出典: brief.md「Scope: レイアウトパラメータの操作 UI（表示の切り替え程度）」 / A-4_

#### Acceptance Criteria

1. The simulator-visualization shall 表示中の掃引結果を別のファイルに差し替える操作を提供する
2. The simulator-visualization shall キャッチ可能領域の図と軌跡アニメーションの双方を、同一画面から到達できるようにする
3. The simulator-visualization shall 操作によって表示条件を変更した場合も、較正段階とモデル除外要因の提示を維持する
4. The simulator-visualization shall 入力パラメータの値を利用者が編集して結果を作り変える操作を提供しない
5. The simulator-visualization shall 利用者の操作内容を永続化せず、次回起動時に前回の状態を復元しない

### Requirement 7: アルゴリズムを持たない境界の担保

**Objective:** As a このプロジェクトを長く保守する開発者, I want ブラウザ側にアルゴリズムが入り込まないことが検査で分かること, so that 二重実装によるズレに気付けない状態を構造的に避けられる

_出典: A-1 / `tech.md` 開発標準3 / roadmap Constraints_

#### Acceptance Criteria

1. The simulator-visualization shall 物理モデル・予測・パラメータ掃引・キャッチ成立判定の再実装を含まない
2. The simulator-visualization shall 上記の禁止事項に違反していないことを、実装コードに対する自動検査として検証できるようにする
3. The simulator-visualization shall 実行時に外部ネットワーク通信を行わず、外部通信に用いる機構を実装コードに含めない
4. The simulator-visualization shall 実行時のサードパーティ依存を持たない
5. The simulator-visualization shall 上記検査が違反を実際に検出できることを、違反例に対する検査結果として示せるようにする
6. If 検査の許可範囲を広げる変更が必要になった場合, then the simulator-visualization shall その変更が実装コードの差分として現れる形をとる

### Requirement 8: 実行環境と配布形態

**Objective:** As a 開発PC でこの図を開く人, I want 準備なしに近い手間で図が開けること, so that 図を見せるだけのために環境構築が必要にならない

_出典: A-5 / A-6 / brief.md「Approach」_

#### Acceptance Criteria

1. The simulator-visualization shall 開発PC のブラウザ上で動作し、Raspberry Pi 上での実行を前提としない
2. The simulator-visualization shall 常駐サーバ・HTTP API を持たず、静的ファイルの配信のみで動作する
3. The simulator-visualization shall ハードウェアの接続を必要とせずに全機能を確認できるようにする
4. The simulator-visualization shall 実機や上流の Python 実行環境へ接続することなく、既存の出力ファイルだけで動作する
5. The simulator-visualization shall Python 側のパッケージ設定を変更しない

---

## 決着させる未決事項 / 未決のまま残すもの

| ID | 事項 | 本 Spec での扱い |
|---|---|---|
| **OQ-34** | ブラウザ可視化の実装手段（Canvas / SVG / WebGL） | **決着させる**。決着内容の `docs/decisions.md` への移行と `tech.md` の表記更新は、`prediction-core` の OQ-31 と同じく実装完了後の別作業とする |
| OQ-38 | 柱2b ライブダッシュボードを作るか | **決着させない。** 本 Spec は実機ライブ表示を持たない（M3 着手時に再判断） |
| OQ-39 | 柱3 投擲アーカイブ／ベンチマークを作るか | **決着させない。** 本 Spec は実データのリプレイビューアを持たない |
| OQ-40 | リポジトリのディレクトリ構成 | **決着させない。** 本 Spec が確定させるのは自身の配置のみ |
| OQ-41 | Python の環境構築・パッケージ管理 | **決着させない。** 本 Spec は Python 側の設定に触れない |
| OQ-01 | 投擲レイアウトの定義 | **決着させない。** 上流が生成した検討材料を**見える形にする**だけである |

---

## design フェーズで決めるもの（requirements では決めない）

- 描画手段の具体（OQ-34 の決着そのもの）と、その選択理由
- モジュール分割・依存方向・公開する型の形
- 境界検査の実装手段（何をどう静的に検査するか）
- ディレクトリ名・ビルド手段・テスト実行手段
- 図の具体的な配色・レイアウト・凡例の書式
- 入力 JSON のキーと表示要素の対応表

---

## 制約（brief.md / steering より継承）

- **TypeScript 側に本番と重複するアルゴリズムを置かない**（`tech.md` 開発標準3）
- **Hono を使わない。** 常駐サーバ・HTTP API を持たない（A-5）
- **Raspberry Pi 上で動かさない。** 開発PC 上で動かす（A-6）
- **2D で足りる可能性が高い。** WebGL を先に選ばない（OQ-34）
- 上流の**出力 JSON が唯一の入力**であり、その形式を本 Spec は定義しない（A-2）
- 単位は **距離 mm / 時刻 ms**（`structure.md` 命名規約）。上流の単位表記をそのまま用いる
- **未実測の数値を合否条件にしない**（A-8）
- **急がない。最小構成で作る**（A-4）
