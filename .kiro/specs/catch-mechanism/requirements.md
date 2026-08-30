# Requirements Document

## Project Description (Input)

**捕球機構の開口寸法を実物で確定させ、その値を扱うための CAD 基盤（形状の正・寸法パラメータの単一の正・造形制約）を設置する。**
詳細な背景・スコープ・制約は [brief.md](./brief.md) を正とし、
確定済みの技術前提と選定基準は [roadmap.md「機構トラックの起点」](../../steering/roadmap.md) を正とする。

### 誰の問題か

`src/trajectory_sim/params.py` の `CatchCriteria.position_tolerance_mm = 67.5` は、
docstring 自身が「**開口寸法自体がまだ確定していないため暫定値**」と書いている。

```
開口半径 − 対象物半径 → position_tolerance_mm → キャッチ可能領域 → NFR-1 の必要駆動性能
```

この伝播経路は既に稼働しており、**その起点だけが空席**である。
さらに造形手段（Bambu Lab A1 mini）と CAD 手段が確定したにもかかわらず、
**リポジトリには CAD 資産が一切なく、寸法パラメータの置き場所も造形制約の記録先も無い。**

### 現状

- FR-7（受け口）/ FR-12（キャッチ保持）/ NFR-5（位置精度）の記述は `docs/` に散在するが、**所有者となる Spec が無い**
- `teleop-bringup/brief.md` が「ゴミ箱本体・固定アダプタの設計（機構トラック側）」を委譲しており、**宛先の無い参照が既に存在する**
- ゴミ箱本体は未購入（OQ-08 未決）。ただし 2026-08-30 の実売調査により、
  **要求（丸型・φ200 以上・300g 以下・PP・フタなし）は 110 円で成立する**ことが確認済みで、**ハード待ちではない**
- `docs/decisions.md` D-9 により `trajectory_sim` は `bounce_out`（跳ね返り）を**明示的にモデル外**としている。
  したがって FR-12 の判断はシミュレータからは出せない
- 既存の Python パッケージ（`prediction_core` / `trajectory_sim`）は**実行時サードパーティ依存ゼロ**で設計され、
  `tests/prediction_core/test_boundaries.py` / `tests/prediction_core/test_trajectory_sim_boundaries.py` が静的に回帰検証している

### 何が変わるべきか

- **開口径が実物で確定し、`position_tolerance_mm` が「暫定値」でなくなる。** 実物由来の値がシミュレータの設定ファイルへ還元される
- **OQ-08（市販PPゴミ箱の具体機種）が決着する。** 選定基準が判定可能な形で保持され、実物が選定・採寸されている
- **寸法パラメータの単一の正が存在し、現物採寸値を後から流し込める。** git で行単位の差分が読め、ヘッドレスで再生成・検証できる
- **A1 mini の造形制約（φ180 超は分割必須・PETG が実質の上限材料・継手はボルト＋金属インサート）が
  設計の初期前提として明文化され、以降の機構設計がそれを前提に始められる**
- 受け口の深さ・底の扱い・テーパーの釣り合いについて、FR-12 の観点から根拠のある判断が下されている

---

## 追加入力: 確定済みの方針（A-1〜A-9）

> 本節は Project Description の続きであり、**入力として与えられた確定方針**である。
> 検証可能な形式の要件は下の [`## Requirements`](#requirements) を正とし、
> `A-n` は各要件の**出典参照**として使用する。

### A-1. 形状の正は `.py` に置く。FreeCAD を形状の正にしない

形状定義は build123d（Python / OCCT カーネル）で持ち、STEP を経由して FreeCAD で
組立確認・干渉チェック・図面化を行う。**FreeCAD 側の責務は「見る・測る」までであり、形状の正を持たない。**

- 争点は「Python か否か」ではなく**「モデルの正がどこにあるか」**である。FreeCAD の Python API は
  `.FCStd`（バイナリ zip）というドキュメントを操作する形であり、成果物はそのバイナリになる
- ⚠️ **トポロジカルネーミング問題（TNP）はスクリプトで書いても回避されない。**
  未修正 issue: [#26084](https://github.com/FreeCAD/FreeCAD/issues/26084)（Spreadsheet のセル値変更で
  SubShapeBinder の参照面が別の面へ飛ぶ・1.1.0rc1 で再現・open）/
  [#17041](https://github.com/FreeCAD/FreeCAD/issues/17041)（recompute 後に face/edge ID が変わる・Confirmed・open）/
  [#31040](https://github.com/FreeCAD/FreeCAD/issues/31040)（その V2 アルゴリズム本体が 2026-06 起票で未完了）
- ⚠️ **`.FCStd` の git 差分は非圧縮保存でも本質的に解決しない**（形状の実体が BREP のため、
  寸法を 1mm 変えるとエントリ番号が総入れ替えになる）。テキスト化要求は未実装であり、
  `textconv` 回避策はマシンごとの再設定が必要でリポジトリに同梱できない

> ⚠️ 「最終微調整は結局 FreeCAD になるのでは」に対しては**規律ではなく構造で答える**:
> `.FCStd` を git 管理せず、`.py` から再生成した形状指標をコミット済みの値と照合する。
> **正になり得ない場所に置けば、正になりようがない。**

### A-2. 造形前提（Bambu Lab A1 mini）を設計の初期前提として固定する

| 項目 | 前提 |
|---|---|
| 造形可能寸法 | 180 × 180 × 180 mm。⚠️ **φ180 超の部品は分割必須**（円は正方形の対角線を使えないため斜め配置でも入らない） |
| 材料 | **PETG が実質の上限。** ASA / ABS / PC / PA / CF・GF 強化材は公式スペックが not recommended（エンクロージャ無し・ベッド上限 80℃）。⚠️ **後から材料でリカバリできない** |
| 継手 | **貫通ボルト＋金属ヒートインサート＋広い当たり面。ダボは位置決め専用。** FDM は層間強度が弱く、樹脂だけで荷重を渡す継手は層間剥離モードで落ちる |
| PLA | 常時荷重のかかる構造部材に使わない（Tg 以下でもクリープする。PETG も耐クリープ性は高くない） |

**後から気付くと全面やり直しになる種類の制約であるため、形状設計に入る前に固定する。**

### A-3. ゴミ箱の選定基準は roadmap を正とする（実売調査で確定済み）

**買い物の判断ルールは1行: 「丸型・5L 以上・フタなし」を選ぶ。**
3L 以下は φ180 未満で不可。5L → 6.9L → 8L は径がほぼ頭打ちで高さだけ伸びるため、**5〜6.9L がスイートスポット**。

| 項目 | 基準 |
|---|---|
| 形状 | **円形**（角形は有効開口が方向依存になり、スカラーの許容誤差と型が合わない） |
| 開口内径 | **φ215〜225 が実質の上限** / 最低 φ200 / φ180 未満は不可 |
| 価格帯 | **110 円で足りる。** 220 円以上の帯は蓋・スイング・折りたたみ等の不要な機構が付き要求に不利 |
| 重量 | 300g 以下（実測 228g の実例あり） |
| 高さ | 200〜300mm（空き缶 約120mm が寝て収まる深さ） |
| 底 | 脚付き・丸底でないこと。**テーパーは緩いものを選ぶ**（上φ220→底φ158 程度＝片側約7°は可 / 上φ225→底φ145＝約10°は強すぎる） |
| 縁 | 外向きリムがあるものが望ましい（造形物を引っ掛けられる） |
| 付属品 | **フタなし**（蓋・スイングトップ・内枠はいずれも有効開口を狭める） |

**第一候補**: キャンドゥ「ダストボックス丸」＝ 山田化学 No.335（JAN 4965534335027）。
φ220 × H244mm・6.9L・PP再生樹脂・228g・フタなし・110 円。
⚠️ **同一品が別ルートで単品入手できる**ため、壊しても同スペックで再調達できる。
次点: セリア「ブルックリン調ダストボックス」（φ215 × H220mm・5L）。
⚠️ ワッツ「Re.B」は上φ225→底φ145 の強テーパーのため非推奨。

⚠️ **当初の推奨2件は実売調査で訂正済みである**: (1) φ240〜260 は 100 均に存在しない、
(2) テーパーは全品にある（ネスティング前提）ため「強いテーパー不可」は要件として成立せず「緩いものを選ぶ」へ緩和した。
**推測でこの基準を書き直さない。**

### A-4. 開口径 → `position_tolerance_mm` は**設定ファイルの値**で繋ぐ

`許容誤差 = 開口半径 − 対象物半径` として `trajectory_sim.CatchCriteria.position_tolerance_mm` の値になる。

- ⚠️ **`src/trajectory_sim/` のコードには一切触れない。** 本 Spec が行うのは設定値の確定と還元だけである
- 現在の既定値 67.5mm は φ200 開口を仮定した暫定値（`params.py` の docstring が明記）
- `DrivetrainParams` と同じ形の還元経路である

### A-5. CAD 基盤は本 Spec が単独所有する

build123d による形状の正・寸法パラメータの単一の正・A1 mini の造形制約は
**本 Spec が単独で所有し、`chassis-mechanism` は消費する。**
⚠️ **二重に定義しない**（`sensing-foundation` の `geometry.py` と同じ扱い）。

### A-6. 未実測の値を合否条件にしない（`tech.md` 開発標準1）

転倒限界の概算・底径 φ158 の推定値・カタログの公称寸法は**判断材料であって合否ではない。**
**受入基準に据えない。** 値には必ず出所（実測 / 仮値）を併記する。

### A-7. 緩衝ライナー（OQ-10）は M3 まで先送りする

`bom.md §E` の既定方針どおり**先回りして購入せず、M3 で実投擲後に判断する。**
本 Spec は「**後から貼れる形にしておく**」ことのみを担保し、**材質選定を決着させようとしない。**

### A-8. 依存境界を壊さない

`prediction_core` / `trajectory_sim` のツリーは**実行時サードパーティ依存ゼロ**であり、
`tests/prediction_core/test_boundaries.py` / `test_trajectory_sim_boundaries.py` が静的に回帰検証している。
⚠️ **本 Spec が導入する形状ライブラリをそれらの依存へ混入させない。**
既存の依存管理（`pyproject.toml` / `uv.lock`、uv 管理・Python 3.11）に従い、任意依存として扱う。

### A-9. 同じ形状が FR-7 と FR-12 へ逆向きに効く

- **テーパー／漏斗は FR-7（取りこぼさない）には有利**だが、
  **跳ね返った缶を開口へ向けて上外へ弾くため FR-12（保持）には不利**である
- **受け口の深さも両方向に効く**: 浅い＝衝突エネルギーが小さいが跳ね返ると脱出しやすい /
  深い＝衝突エネルギーが大きいが脱出しにくい
- ⚠️ `docs/decisions.md` D-9 により `trajectory_sim` は `bounce_out` をモデル外としているため、
  **この釣り合いはシミュレータからは出せず、本 Spec が机上で決めるしかない**

---

## Introduction

catch-mechanism は**2つの責務を持つ**。

1. **CAD 基盤**: 寸法パラメータの単一の正、造形制約の明文化、`.py` からのヘッドレスな形状生成と
   形状指標による検証。これは本 Spec が単独所有し、下流の `chassis-mechanism` が消費する
2. **受け口（FR-7 / FR-12）**: 市販ゴミ箱の選定・採寸・開口径の確定、ワイドリム／漏斗の設計、
   保持に関する机上判断、および `position_tolerance_mm` の還元

本 Spec の価値は「CAD を導入すること」そのものよりも、
**プロジェクト唯一の最終判定（NFR-7）の手前にある合否条件の入力が、机上の仮定ではなく実物に接地すること**にある。

本 Spec は**ハードウェアを必要としない**（ゴミ箱は百均で調達可能であり、採寸前でも公称値を仮値として
全体の生成と検証が完了できる）。固定側の実機セットアップと完全に並行できる。

## Boundary Context

- **In scope**:
  - 寸法パラメータの単一の正（値ごとの出所つき）と、現物採寸値の流し込み経路
  - 造形制約（造形可能寸法・使用可能材料・継手方針）の明文化と、設計に対する自動検査
  - `.py` からのヘッドレスな形状生成（組立確認用の中間形式と造形用のメッシュ形式）
  - 形状指標（体積・境界箱・立体数）による二重管理の検出、および外部 CAD 作業ファイルのバージョン管理除外
  - 市販ゴミ箱の選定基準・候補判定・実物の選定と採寸（OQ-08 の決着）
  - 開口径の確定と位置許容誤差の導出、シミュレータ設定ファイルへの還元と整合検査
  - 受け口（ワイドリム／漏斗）の設計と、造形制約に従った分割・締結
  - 受け口の深さ・底の扱い・テーパーの釣り合いに関する机上判断の記録
  - 下流（`chassis-mechanism`）が消費する寸法・制約の公開
- **Out of scope**:
  - 駆動ベース・ゴミ箱固定アダプタ・バッテリ／基板トレイ・整備スタンドの設計（→ `chassis-mechanism`）
  - 緩衝ライナーの材質選定と調達（→ OQ-10。M3 で実投擲後に判断）
  - シミュレータの実装コードの変更、およびキャッチ成否の判定ロジック（→ `trajectory-simulator` の所有）
  - 跳ね返りのモデル化（→ D-9 でモデル外と決着済み）
  - NFR-7 の目標値と試行回数 N（→ OQ-05。M1 / M2 実測後）
  - 対象物の最終スコープの確定（→ OQ-02。本 Spec は M1 の実験条件である空き缶を前提とする）
  - `docs/` の更新（決着した OQ の `decisions.md` への移行を含む）
  - 外部 CAD 上での組立確認・図面化の作業手順そのもの（形状の正を持たない領域）
- **Adjacent expectations**:
  - `trajectory-simulator` は**設定ファイルの値を受け取るだけ**であり、本 Spec は実装コードへ変更を要求しない
  - `chassis-mechanism` は CAD 基盤とゴミ箱の底寸法を**消費する側**であり、同じ定義を再実装しない
  - `m1-prediction-validation` は NFR-5 の評価に本 Spec が確定させた許容誤差を用いる
  - 本 Spec が確定させるのは NFR-5 の**許容幅の分母**であり、**NFR-5 の達成そのものではない**
  - 出力される値は出所（実測 / 仮値）を伴い、その位置付けは利用側の運用ではなく**データ自身が保持する**

## Requirements

### Requirement 1: 寸法パラメータの単一の正と出所管理

**Objective:** As a 機構を設計する開発者, I want すべての寸法値が1箇所に集約され、実測値か仮値かが値ごとに分かること, so that 現物採寸の結果をコード変更なしに流し込め、未実測の値を合否条件と取り違えない

_出典: A-1 / A-5 / A-6 / brief.md「Boundary Candidates: CAD 基盤」_

#### Acceptance Criteria

1. The catch-mechanism shall 設計に用いるすべての寸法値を、実装コードの外にある単一の設定ファイルへ保持する
2. The catch-mechanism shall 各寸法値について、実測値であるか仮値であるかの出所を値ごとに保持する
3. If 設定ファイルに未知の項目が含まれる場合, then the catch-mechanism shall 読み込みを失敗させ、該当する項目名を示す
4. If 必須の寸法値が欠けている場合、または値が物理的にあり得ない符号・範囲である場合, then the catch-mechanism shall 読み込みを失敗させ、該当する項目名と値を示す
5. The catch-mechanism shall 他の寸法値から導出される量について、導出に用いた入力がすべて実測である場合に限り実測として扱い、1つでも仮値を含む場合は仮値として扱う
6. When 現物採寸値が設定ファイルへ書き込まれた場合, the catch-mechanism shall 実装コードを変更することなく、その値を用いて以降の導出と形状生成を行う
7. The catch-mechanism shall 寸法値を、変更が行単位の差分として読める形式で保持する
8. The catch-mechanism shall 寸法パラメータの定義を単一の箇所に限り、同じ値を別の場所で再定義しない

### Requirement 2: 造形制約の明文化と自動検査

**Objective:** As a 機構を設計する開発者, I want 造形機の制約が設計の初期前提として明文化され、違反が自動で検出されること, so that 造形できない形状を作り込んでから気付く全面やり直しを避けられる

_出典: A-2 / brief.md「Constraints」/ `docs/requirements.md` CON-1 / CON-2 / `docs/drivetrain-spec.md §6.2`_

#### Acceptance Criteria

1. The catch-mechanism shall 造形可能寸法・使用可能材料の一覧・継手方針を、寸法パラメータと同じ設定基盤の上に保持する
2. When 部品の外形が造形可能寸法を超える場合, the catch-mechanism shall その部品を分割が必要なものとして判定し、判定結果を報告する
3. The catch-mechanism shall 分割後の各断片の外形が造形可能寸法に収まることを検査する
4. If 造形可能寸法を超える断片が存在する場合, then the catch-mechanism shall 生成を失敗させ、超過している軸と超過量を示す
5. The catch-mechanism shall 材料の指定を許可された一覧からのみ受け付け、一覧に無い材料の指定を拒否する
6. The catch-mechanism shall 部品間の継手を、荷重を受ける締結要素と位置決めのみを担う要素とに区別して保持する
7. The catch-mechanism shall 造形制約の検査を形状生成の一部として実行し、検査を通らない形状の生成物を出力しない
8. The catch-mechanism shall 切削加工を前提とする形状を設計に含めない

### Requirement 3: 形状定義とヘッドレスな生成

**Objective:** As a 機構を設計する開発者, I want 形状が寸法パラメータから決定される手続きとしてコードで定義され、対話操作なしに生成物が得られること, so that 寸法変更のたびに GUI 作業をやり直す運用にならない

_出典: A-1 / brief.md「Approach」_

#### Acceptance Criteria

1. The catch-mechanism shall 受け口部品の形状を、寸法パラメータから決定される手続きとして実装コードで定義する
2. When 形状生成が要求された場合, the catch-mechanism shall 画面表示・対話操作・外部 CAD の起動を必要とせずに完了する
3. The catch-mechanism shall 生成した形状を、組立確認と図面化に用いる中間形式と、造形に用いるメッシュ形式の双方で出力する
4. The catch-mechanism shall 出力の単位系をミリメートルに固定する
5. The catch-mechanism shall 生成物の出力先をバージョン管理の対象外とし、同じ入力からいつでも再生成できる状態に保つ
6. If 形状生成が失敗した場合, then the catch-mechanism shall 途中まで書き出したファイルを残さず、失敗した部品名と理由を示す
7. When 同一の寸法パラメータから複数回生成した場合, the catch-mechanism shall 同一の形状指標を持つ生成物を出力する

### Requirement 4: 形状指標による二重管理の検出

**Objective:** As a プロジェクトの保守者, I want 外部 CAD で形状に手を入れた事実が検査の失敗として現れること, so that 「build123d 側が黙って腐る」二重管理を構造で防げる

_出典: A-1 / `tech.md` 開発標準3（二重実装の禁止）/ roadmap「二重管理の検出」_

#### Acceptance Criteria

1. The catch-mechanism shall 生成した各部品について、体積・境界箱・立体数からなる形状指標を算出する
2. The catch-mechanism shall 形状指標をバージョン管理される単一のファイルへ記録し、算出に用いた寸法パラメータの識別子を併せて保持する
3. When 形状指標の照合が要求された場合, the catch-mechanism shall 現在の実装コードとパラメータから形状を再生成し、記録済みの指標と許容差の範囲で一致するかを判定する
4. If 再生成した形状指標が記録済みの指標と一致しない場合, then the catch-mechanism shall 照合を失敗させ、部品名と乖離した指標の双方の値を示す
5. If 寸法パラメータが変更されたにもかかわらず形状指標の記録が更新されていない場合, then the catch-mechanism shall 形状生成の環境が利用できない環境であっても、この不整合を検出して失敗させる
6. The catch-mechanism shall 外部 CAD の作業ファイルを成果物として扱わず、バージョン管理の対象から除外する
7. The catch-mechanism shall 外部 CAD で測定した値を、寸法パラメータの設定ファイルへ書き戻す経路でのみ設計へ反映する

### Requirement 5: 依存境界の維持

**Objective:** As a プロジェクトの保守者, I want 形状ライブラリが既存パッケージの依存へ混入しないこと, so that 実行時サードパーティ依存ゼロという既存の不変条件が壊れない

_出典: A-8 / roadmap「着手順序の制約」/ `tests/prediction_core/test_boundaries.py`_

#### Acceptance Criteria

1. The catch-mechanism shall 形状定義に用いる外部ライブラリを、既定ではインストールされない任意依存として宣言する
2. The catch-mechanism shall 寸法パラメータの読み込み・導出・下流への提供を、その外部ライブラリを必要とせずに実行できるようにする
3. When 形状生成に必要な外部ライブラリが利用できない場合, the catch-mechanism shall 形状生成の要求を明示的な失敗として扱い、寸法パラメータの利用を妨げない
4. The catch-mechanism shall 既存の予測コアおよびシミュレータのパッケージに、新たな実行時依存を発生させない
5. The catch-mechanism shall 自パッケージ内で外部ライブラリを参照する範囲を限定し、その範囲外に参照が現れないことを静的に検査する
6. The catch-mechanism shall 実行時サードパーティ依存ゼロを表明している既存の検査を、その表明が弱まる形で変更しない
7. The catch-mechanism shall 形状生成の環境を持たない実行環境でも、形状生成を除くすべての検査が完了できるようにする

### Requirement 6: ゴミ箱の選定基準・選定・採寸（OQ-08 の決着）

**Objective:** As a 機構を設計する開発者, I want 選定基準が判定可能な形で保持され、実物が根拠付きで選定・採寸されること, so that 「百均で適当に買う」ことなく、開口径が合否条件の入力として接地する

_出典: A-3 / A-6 / `docs/open-questions.md` OQ-08 / `docs/bom.md §E`_

#### Acceptance Criteria

1. The catch-mechanism shall 選定基準を、形状・開口内径・高さ・重量・テーパー・縁・付属品・価格帯の各項目について判定可能なしきい値として保持する
2. When 候補の諸元が与えられた場合, the catch-mechanism shall 各項目の適合・不適合を判定し、不適合であった項目名を示す
3. If 開口内径が下限を下回る候補が与えられた場合, then the catch-mechanism shall その候補を不適合として扱う
4. If テーパー角が上限を超える候補が与えられた場合, then the catch-mechanism shall その候補を不適合として扱う
5. The catch-mechanism shall 採寸すべき項目として、開口内径・上端外径・底の外径・底の平面部径・高さ・実測重量・底の肉厚・テーパー角を明示し、それぞれの記録先を持つ
6. When 実物の採寸値が記録された場合, the catch-mechanism shall 該当する値の出所を実測へ更新する
7. While 実物の採寸が行われていない状態, the catch-mechanism shall 公称値を仮値として扱い、全体の生成と検査を完了できるようにする
8. The catch-mechanism shall 選定した機種を識別できる情報とともに選定結果を記録し、選定基準のどの項目を根拠に選んだかを示す

### Requirement 7: 開口径の確定と位置許容誤差の還元

**Objective:** As a シミュレータの利用者, I want 実物由来の開口径から導出された位置許容誤差が設定ファイルへ反映されること, so that キャッチ可能領域が机上の仮定ではなく実物に接地する

_出典: A-4 / A-6 / `docs/requirements.md` NFR-5 / roadmap「開口径 → position_tolerance_mm」_

#### Acceptance Criteria

1. The catch-mechanism shall 位置許容誤差を「開口内半径 − 対象物の代表寸法の半分」として単一の箇所で導出する
2. The catch-mechanism shall 導出に用いる開口内径として、保持まで成立する内径を用い、外向きに張り出す部分の寸法を算入しない
3. The catch-mechanism shall 導出した位置許容誤差に、導出へ用いた各入力の値と出所を併記する
4. When 開口内径と対象物の代表寸法がいずれも実測である場合, the catch-mechanism shall 導出した位置許容誤差の出所を実測として扱う
5. The catch-mechanism shall 導出した位置許容誤差を、シミュレータの設定ファイルが解釈できる形式で出力する
6. The catch-mechanism shall シミュレータの実行可能な設定ファイルに記録された位置許容誤差が、本 Spec が導出した値と一致することを検査する
7. If 両者が一致しない場合, then the catch-mechanism shall 検査を失敗させ、双方の値と参照元を示す
8. The catch-mechanism shall シミュレータの実装コードを変更せず、設定ファイルの値のみを通じて値を伝える
9. The catch-mechanism shall 対象物の代表寸法として M1 の実験条件である空き缶を用い、その前提を導出の記録に明示する

### Requirement 8: 受け口（ワイドリム／漏斗）の設計

**Objective:** As a 機構を設計する開発者, I want 受け口が採寸値から再導出され、造形制約に従って分割・締結されること, so that ゴミ箱の実寸が変わっても設計をやり直さずに追随できる

_出典: A-2 / A-9 / `docs/requirements.md` FR-7 / `docs/bom.md §E`_

#### Acceptance Criteria

1. The catch-mechanism shall 受け口を、選定したゴミ箱の縁へ取り付けられる部品として設計する
2. The catch-mechanism shall 受け口がゴミ箱本体の開口内径を狭めないことを検査する
3. If 受け口の外形が造形可能寸法を超える場合, then the catch-mechanism shall 複数の断片へ分割した形で設計する
4. The catch-mechanism shall 断片同士の接合を貫通ボルトと金属インサートで行い、位置決めのみを担う要素へ荷重を負わせない
5. When ゴミ箱の採寸値が更新された場合, the catch-mechanism shall 取り付け部の寸法をその値から再導出する
6. The catch-mechanism shall 取り付け部に個体差を吸収する隙間を持たせ、その量を寸法パラメータとして保持する
7. The catch-mechanism shall 受け口の質量の目安を、生成物の体積と材料の密度から算出する

### Requirement 9: 保持（FR-12）の机上判断と緩衝材の後付け余地

**Objective:** As a 機構を設計する開発者, I want 深さ・底の扱い・テーパーの釣り合いが根拠付きで記録されること, so that シミュレータからは出せない判断が、後から読み直せる形で残る

_出典: A-7 / A-9 / `docs/requirements.md` FR-12 / `docs/decisions.md` D-9 / `docs/open-questions.md` OQ-10_

#### Acceptance Criteria

1. The catch-mechanism shall 受け口の深さ・底の扱い・テーパーについての方針を、判断の根拠とともに記録する
2. The catch-mechanism shall 同一の形状が取りこぼし防止と保持に対して逆向きに作用する関係を、判断の記録に明示する
3. The catch-mechanism shall 跳ね返りが評価対象外である旨を判断の記録に明示し、シミュレータの出力を保持の根拠として用いない
4. The catch-mechanism shall 底面へ後から緩衝材を貼れる平面が残ることを、設計上の制約として保持する
5. The catch-mechanism shall 緩衝材の材質選定と調達を本 Spec の決着対象から除外し、その旨を判断の記録に明示する
6. The catch-mechanism shall 未実測の推定に基づく判断材料を、合否条件と区別できる形で記録する
7. Where 跳ね出しを抑える追加部品を後から取り付ける場合, the catch-mechanism shall 既存の受け口を作り直さずに取り付けられる締結箇所を備える

### Requirement 10: 下流仕様への提供と責務の限定

**Objective:** As a `chassis-mechanism` の設計者, I want CAD 基盤とゴミ箱の底寸法を再定義せずに参照できること, so that 同じ値が2箇所で食い違う事態が起きない

_出典: A-5 / roadmap「CAD 基盤の単独所有」/ brief.md「Downstream」_

#### Acceptance Criteria

1. The catch-mechanism shall ゴミ箱の底の外径・底の平面部径・テーパー角・高さ・実測重量を、下流の設計が参照できる形で公開する
2. The catch-mechanism shall 造形制約と継手方針を、下流の設計が同じ定義を再実装せずに参照できる形で公開する
3. The catch-mechanism shall 公開する項目の参照が、形状生成用の外部ライブラリを必要としないようにする
4. The catch-mechanism shall 公開する各項目に出所を併記し、仮値と実測値を利用側が区別できるようにする
5. When 公開している項目の意味・単位・構造が変わる場合, the catch-mechanism shall その変更を下流の再検証が必要な変更として扱う
6. The catch-mechanism shall 駆動ベース・ゴミ箱固定アダプタ・トレイ類・整備スタンドの設計を自身の責務に含めない
