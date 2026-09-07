# Requirements Document

## Project Description (Input)

`brief.md` を参照（2026-08-30 の `/kiro-discovery` により生成済み）。

足回りの部品（60mm ダブルオムニ ×3、6mm ハブ ×3、JGB37-520 ＋付属金属ブラケット ×3、
AE-TB67H450 ×3、3S LiPo、ESP32 DevKit ほか）はすべて手元にあり、制御ロジック
（`drivetrain-core`）も実装完了している。**しかし、それらを組み立てて機体にするための
構造物が一つも存在しない。**

`teleop-bringup`（M2a 初通電走行）は実機必須の Spec だが、その前提となる物理的な機体が
無いため着手できない。とくに**整備スタンド**は、ホイールを床から浮かせた状態でしか安全に
実施できない台上確認（回転方向・エンコーダ符号・デッドマン解放・コントローラ圏外での停止）の
前提であり、**駆動系トラック全体のクリティカルパス上にある単一の造形物**である。

本 Spec は `catch-mechanism` が確立した CAD 基盤（形状の正・寸法パラメータの単一の正・
A1 mini の造形制約）を**消費する側**として、駆動ベース・ゴミ箱固定アダプタ・バッテリ／基板
トレイ・配線ガイド・整備スタンドを設計し、造形し、組み立て、実測する。
**モータは回さない。**

## 追加入力: 確定済みの方針（A-1〜A-10）

要件生成にあたり、`brief.md`・steering・`docs/`・上流 `catch-mechanism` の公開契約から
確定している方針と、本フェーズで利用者に確認した2件を以下に固定する。

| ID | 方針 | 出所 |
|---|---|---|
| **A-1** | **本 Spec の完了は「設計＋造形＋組立＋実測」まで**とする。CAD と生成物だけでなく、実際に造形して組み立て、最低地上高・実測重量・重心位置・荷重下の実効ホイール径までを受入条件に含める | 2026-09-06 利用者確認 / brief.md「Desired Outcome」「Out」 |
| **A-2** | ゴミ箱の**底の平面部径が仮値**であるため、**本 Spec が実測し、上流の設定ファイルの値と出所のみを更新する**（構造・キー名・単位には触れない） | 2026-09-06 利用者確認 / catch-mechanism design.md「Revalidation Triggers」項目1 |
| **A-3** | **CAD 基盤（形状の正・寸法パラメータの単一の正・造形制約・継手方針）は `catch-mechanism` が単独所有する。** 本 Spec は消費する側であり、同じ定義を再実装しない | roadmap.md「CAD 基盤の単独所有」/ catch-mechanism 要件 10 |
| **A-4** | 駆動ベースの分割は**中央部＋放射状の3つのモータ取付部**とする。φ250〜300mm 見込みの円は 180mm 角の造形面へ**斜めでも入らない**ため分割は回避不能 | brief.md「Approach」/ drivetrain-spec.md §6.2 |
| **A-5** | **モータ反力を受ける継手は貫通ボルト＋金属インサート＋広い当たり面で樹脂を圧縮のみで使う。ダボは位置決め専用**。接合面の法線が積層方向と一致する配置は禁忌 | brief.md「Approach」の設計規律 |
| **A-6** | **整備スタンドを他のどの造形物よりも先に出す。** `teleop-bringup` のクリティカルパス上にあり、かつ最も単純である | brief.md「Approach」/ bom.md §E |
| **A-7** | モータは**付属金属ブラケット経由**で固定する。造形部品でモータ本体を直接クランプしない。ブラケットを反転させ**モータをベース板から吊り下げる**構成が実測により成立している | drivetrain-spec.md §6.1 / bom.md §B（2026-09-03 実測） |
| **A-8** | **⚠️ ホイール／ハブは純正品ではない**（Nexus 14145 / 18020 **相当品**）。公称値を CAD にもコードにも入れず、**荷重下の実効転がり径は本 Spec が実測する** | drivetrain-spec.md §11 #4a / roadmap.md |
| **A-9** | 2026-09-03 時点で図面・メーカー資料・現物により確定済みの値（ホイール Ø60／中心穴 Ø12.5／ボルト円 Ø21・Ø28、ハブ内径 Ø6 真円・ボス Ø12×12・M4 止めネジ1本、ブラケット外形 41.3×38.8mm・取付面→接地点 60.0mm・取付面→ホイール中心 33.9mm）を**出発点として引き継ぐ**。本 Spec が新たに測るのは**未確認として残っている項目**である | bom.md §B / drivetrain-spec.md §11 |
| **A-10** | **`docs/` 本体の更新（決着した未決事項の決定記録への移行を含む）は本 Spec の対象外**とする。決着内容と根拠は本 Spec の記録として残し、後から移せる形にする（`catch-mechanism` が OQ-08 で採った扱いと揃える） | catch-mechanism requirements.md「Out of scope」 |

## Introduction

chassis-mechanism は、**手元の部品を「機体」にするための構造物一式**を持つ。

本 Spec の価値は造形物を作ること自体ではなく、**駆動系トラック全体を塞いでいる単一の
物理的欠落（機体と整備スタンドが存在しないこと）を解消し、`teleop-bringup` の着手条件を
成立させる**ことにある。同時に、決着期限が「機構設計時」と定められている未決事項
（フレーム外形＝ホイール間隔、締結部品の必要数と長さ、メイン電源スイッチ、電源分岐端子）を
決着させ、**非純正部品の公称値の上に載っている数値を実測へ置き換える**。

本 Spec は**上流の CAD 基盤を消費する側**である（A-3）。寸法パラメータの単一の正・造形制約・
継手方針・ゴミ箱の採寸値は `catch-mechanism` が所有しており、本 Spec はそれらを参照して
自身の部品を設計する。

⚠️ **本 Spec はモータを回さない。** 「組み上がって安全に台上へ載る」までを持ち、通電・走行・
性能値の決定は `teleop-bringup` / `m2-motion-validation` の所有である。

## Boundary Context

- **In scope**:
  - 未確認として残っている現物採寸（付属ブラケットの取付穴・取付面の向き、軸方向スタックの実測確認、
    ホイール取付穴とハブのボルト円の対応、止めネジの当たり面）と、その値の保持
  - 駆動ベース（中央部＋放射状の3つのモータ取付部。造形可能寸法に基づく分割と接合設計）
  - フレーム外形＝ホイール配置半径の確定（OQ-07）
  - 最低地上高の確保と、床に接触しないことの設計上の検査および組立後の実機確認
  - 整備スタンド（M2a-0 の前提。最優先で出す）
  - ゴミ箱固定アダプタ（円錐台を受ける座として設計する）
  - バッテリートレイ（最下部配置）・基板トレイ・配線ガイド
  - ねじ・ナット・熱圧入インサートの種別・長さ・必要数の確定（OQ-09）
  - メイン電源スイッチの要否と取付位置（OQ-11）、電源分岐端子の要否と方式（OQ-12）
  - 造形・組立と組付け手順の確立（止めネジをシャフトの平面部へ当てる手順を含む）
  - 実測重量・重心位置・荷重下の実効ホイール径の測定と記録
  - 実効ホイール径のシミュレータ駆動系設定ファイルへの還元
  - 下流（`teleop-bringup` / `m2-motion-validation`）が消費する寸法・実測値・使用手順の公開
- **Out of scope**:
  - CAD 基盤そのもの（形状の正の枠組み・寸法パラメータの単一の正・造形制約・継手方針）→ `catch-mechanism`
  - ゴミ箱本体の選定と開口寸法、受け口（ワイドリム／漏斗）の設計 → `catch-mechanism`
  - 通電・走行・計測、およびエンコーダのカウント校正 → `teleop-bringup` / `m2-motion-validation`
  - 最高速度・加速度上限・減速度上限といった性能値の決定 → `m2-motion-validation` の実測が決める
  - 制御ロジック（逆運動学・速度PID・オドメトリ・保護）とペリフェラル実装 → `drivetrain-core` / `teleop-bringup`
  - 緩衝ライナーの要否・材質（OQ-10。M3 で実投擲後に判断）
  - 物理的な非常停止手段の決着（OQ-13。判断材料の記録と後付け余地の確保までを持つ）
  - センサー固定治具（壁／三脚マウント）。設置形態が未決であり駆動系トラックと無関係
  - フレーム剛性の実測（走行させないと共振は見えないため `m2-motion-validation` 側）
  - `docs/` 本体の更新（決着した未決事項の決定記録への移行を含む。A-10）
- **Adjacent expectations**:
  - `catch-mechanism` は**上流**であり、寸法・造形制約・継手方針・ゴミ箱の採寸値を公開している。
    本 Spec はそれを参照するだけで、同じ値を再定義しない。**唯一の例外は仮値の実測による更新**であり、
    その場合も値と出所のみを更新し、構造・キー名・単位には触れない（A-2）
  - `drivetrain-core` は**輪番号と各輪の取付角の規約を既に実装に持っている**。
    本 Spec は設計側で番号や回転方向を定義し直さない
  - `teleop-bringup` は本 Spec の完了を着手条件とし、**整備スタンドを使う側**である。作る側ではない
  - `m2-motion-validation` は本 Spec の実測重量・重心位置を性能評価の文脈として使う
  - 本 Spec が公開する値には出所（実測／仮値）が伴い、その位置付けは利用側の運用ではなく
    **データ自身が保持する**
  - **本 Spec の完了は M2a の達成ではない。** M2a は初通電走行であり `teleop-bringup` の所有である

## Requirements

### Requirement 1: 現物採寸・寸法パラメータの単一の正と形状の再導出

**Objective:** As a 機構を設計する開発者, I want 本 Spec が確定させる寸法値が1箇所に集約され、実測値か仮値かが値ごとに分かり、上流が公開済みの値を再定義しないこと, so that 非純正部品の公称値を信じたまま設計してしまう事態と、同じ値が2箇所で食い違う事態の両方を避けられる

_出典: A-1 / A-3 / A-8 / A-9 / `docs/drivetrain-spec.md §6.4` / `§11 #1 / #2 / #4b / #4c / #4d` / `docs/bom.md §B` / `tech.md` 開発標準1_

#### Acceptance Criteria

1. The chassis-mechanism shall 本 Spec が確定させる寸法値を、実装コードの外にある単一の設定ファイルへ保持する
2. The chassis-mechanism shall 各寸法値について、実測値であるか仮値であるかの出所を値ごとに保持する
3. The chassis-mechanism shall 上流が公開している寸法値・造形制約・継手方針を参照して用い、同じ値を自身の設定ファイルへ再定義しない
4. If 設定ファイルに未知の項目が含まれる場合、必須の項目が欠けている場合、または値が物理的にあり得ない符号・範囲である場合, then the chassis-mechanism shall 読み込みを失敗させ、該当する項目名を示す
5. When 現物採寸値が設定ファイルへ書き込まれた場合, the chassis-mechanism shall 実装コードを変更することなく、その値を用いて以降の導出と形状生成を行う
6. The chassis-mechanism shall 付属金属ブラケットの取付穴の数・寸法・ピッチ・位置、および取付面の向きを実測し、その値を出所とともに保持する
7. The chassis-mechanism shall ハブとホイールの軸方向の積み上がりを実測し、メーカー資料から導出済みの値と照合し、相違がある場合は実測値を正として保持する
8. The chassis-mechanism shall ホイールの取付穴群とハブのボルト円の対応を現物で確認し、確認結果を記録する
9. The chassis-mechanism shall 実測で置き換えられていない非純正部品の公称寸法を、仮値として扱う
10. The chassis-mechanism shall 寸法値を、変更が行単位の差分として読める形式で保持する
11. The chassis-mechanism shall 各部品の形状を、寸法パラメータから決定される手続きとして定義し、対話操作を必要とせずに生成物を得られるようにする
12. When 同一の寸法パラメータから複数回生成した場合, the chassis-mechanism shall 同一の形状指標を持つ生成物を出力する

### Requirement 2: 造形制約への適合と分割・接合設計（OQ-09 の決着）

**Objective:** As a 機構を設計する開発者, I want 分割数が造形可能寸法から導出され、接合部が樹脂を圧縮のみで使う設計になり、必要な締結部品が設計から数え上げられること, so that 造形できない形状や層間剥離で落ちる継手を作り込まず、締結部品の不足で組立が止まることもない

_出典: A-3 / A-4 / A-5 / `brief.md`「Constraints」「Approach」の設計規律 / `docs/requirements.md` CON-1 / CON-2 / `docs/open-questions.md` OQ-09_

#### Acceptance Criteria

1. The chassis-mechanism shall 部品の分割の要否と分割数を、上流が公開する造形可能寸法と部品の外形から導出し、手で決めた分割数を設定値として持たない
2. The chassis-mechanism shall 分割後の各断片の外接箱が造形可能寸法に収まることを、上流が公開する検査を用いて確認する
3. If 造形可能寸法を超える断片が存在する場合, then the chassis-mechanism shall 生成を失敗させ、超過している軸と超過量を示す
4. The chassis-mechanism shall 材料を上流が公開する許可一覧の範囲から選ぶ
5. The chassis-mechanism shall 常時荷重がかかる部位について、樹脂が時間経過で変形する性質を前提とした材料選定と断面の根拠を記録する
6. The chassis-mechanism shall モータ反力を受ける接合部を、貫通する締結要素と金属インサートと当たり面で構成し、樹脂へ圧縮以外の荷重を主として負わせない
7. The chassis-mechanism shall 荷重を受ける要素と位置決めのみを担う要素を区別して保持し、位置決めのみを担う要素に荷重を負わせない
8. The chassis-mechanism shall 接合面の法線が積層方向と一致する配置を採らず、各接合部の造形姿勢を記録する
9. The chassis-mechanism shall 接合部の当たり面が下限を満たすことを、上流が公開する検査を用いて確認する
10. The chassis-mechanism shall ねじ・ナット・熱圧入インサートの種別・長さ・必要数を設計から数え上げ、調達できる形の一覧として記録する
11. The chassis-mechanism shall 切削加工を前提とする形状を設計に含めない

### Requirement 3: 駆動ベースの構造とフレーム外形の確定（OQ-07 の決着）

**Objective:** As a 機構を設計する開発者, I want 中央部と放射状のモータ取付部からなる駆動ベースが、ホイール配置半径を一意に決める式の上で設計されること, so that 設計変数が1つに減り、ホイール間隔の決定が後から読み直せる根拠とともに残る

_出典: A-4 / A-7 / A-9 / `docs/drivetrain-spec.md §6.1 / §6.2` / `docs/bom.md §B`（2026-09-03 実測）/ `docs/open-questions.md` OQ-07 / `firmware/lib/drivetrain_control` の輪配置規約_

#### Acceptance Criteria

1. The chassis-mechanism shall 駆動ベースを、中央部と3箇所のモータ取付部からなる分割可能な構造として設計する
2. The chassis-mechanism shall 3箇所のモータ取付部を、機体中心まわりに等しい角度間隔で配置する
3. The chassis-mechanism shall ホイール配置半径を、機体中心からモータ取付面までの距離と、取付面からホイール中心までの実測距離との和として導出する
4. The chassis-mechanism shall ホイール配置半径の決定値を、安定性と機体寸法の釣り合いに関する根拠とともに記録する
5. The chassis-mechanism shall 転倒余裕の見積もりを未実測の推定として記録し、合否条件に用いない
6. The chassis-mechanism shall 輪番号と各輪の取付角の規約を上流の制御ロジックが持つ定義から受け取り、設計側で定義し直さない
7. The chassis-mechanism shall モータを付属金属ブラケットを介して取り付け、造形部品でモータ本体を直接クランプしない
8. The chassis-mechanism shall ブラケット取付穴を長穴とし、吸収できる寸法差の量を寸法パラメータとして保持する
9. When 現物採寸値が更新された場合, the chassis-mechanism shall モータ取付部の寸法とホイール配置半径をその値から再導出する
10. The chassis-mechanism shall 中央部と各モータ取付部の接合を、要件2が定める荷重の受け方に従って設計する

### Requirement 4: 最低地上高の確保と床接触の回避

**Objective:** As a 機構を設計する開発者, I want ブラケット・ボルト頭・ベース下面・配線それぞれの床との隙間が設計値として算出され、組立後に実測で確認されること, so that 走行そのものが成立しない床接触を、走らせる前に検出できる

_出典: A-1 / A-7 / `docs/drivetrain-spec.md §6.3` / `docs/bom.md §B`（取付面→接地点 60.0mm）/ `docs/requirements.md` CON-3_

#### Acceptance Criteria

1. The chassis-mechanism shall 駆動ベースの下面高さを、モータ取付面から接地点までの実測距離から導出する
2. The chassis-mechanism shall ブラケット、ボルト頭およびナット、駆動ベース下面、配線のそれぞれについて、床との隙間を設計上の値として算出する
3. The chassis-mechanism shall 床との隙間の下限値を、使用環境が屋内の平坦床であることに基づく根拠とともに寸法パラメータとして保持する
4. If いずれかの部位の床との隙間が下限値を下回る場合, then the chassis-mechanism shall 検査を失敗させ、該当する部位と不足量を示す
5. When 機体が組み上がった場合, the chassis-mechanism shall 各部位の床との隙間を実測し、設計値との差を記録する
6. The chassis-mechanism shall 配線が床へ垂れ下がらない保持箇所を設ける
7. When 現物採寸値または部品配置が更新された場合, the chassis-mechanism shall 床との隙間を再算出する

### Requirement 5: 整備スタンド（M2a-0 の前提）

**Objective:** As a 初通電を行う開発者, I want ホイールを床から浮かせて機体を安定に保持する台が、他のどの造形物よりも先に完成すること, so that 極性が逆のまま機体が予期せず走り出す事故を起こさずに台上確認を実施できる

_出典: A-1 / A-6 / `docs/bom.md §E`（整備スタンド）/ `docs/drivetrain-spec.md §6.2` / `§11 #15〜#18` / `.kiro/specs/teleop-bringup/brief.md`_

#### Acceptance Criteria

1. The chassis-mechanism shall 整備スタンドを、他の造形物に先立って設計・造形・検証する
2. The chassis-mechanism shall 整備スタンドの設計入力を、ホイール配置半径と現物採寸値に限り、ゴミ箱およびトレイ類の確定を待たない
3. The chassis-mechanism shall 整備スタンドが機体を**駆動ベース端部にぶら下がる駆動ユニット（モータ胴体）の下面**で支持する形状とし、駆動ベース下面での支持を前提としない。⚠️ **ホイール外周より外側および上方に機体の構造は無く**（台上でホイール頂点＝ベース板下面＝80.0mm）、ホイールそのものを受けることは要件 5.4・5.5 が禁じる。ベース板下面のうちホイールより内側の空き帯はハブのフランジ厚ぶん（4.0mm、ギヤボックス端面 118.4mm ↔ ホイール内側面 122.4mm）しかなく、そこにはブラケット・締結の頭・配線が並ぶ
4. While 機体が整備スタンドへ載っている間, the chassis-mechanism shall 3輪すべてが床および台のいずれにも接触しない状態を保つ
5. The chassis-mechanism shall ホイールが自由に回転できる隙間を確保し、その量を寸法パラメータとして保持する
6. The chassis-mechanism shall 台上での保持を、モータ反力の向きに対して機体が外れない拘束として設計し、その根拠を記録する
7. When 台へ載せた機体を手で押した場合、または手でホイールを回した場合, the chassis-mechanism shall 機体が台から外れないことを確認する手順を備える
8. While 機体が整備スタンドへ載っている間, the chassis-mechanism shall 各ホイールの回転方向を目視でき、エンコーダ配線・コネクタ・電源の操作部へ手が届く状態を保つ
9. When 機体を台へ載せる場合、または台から降ろす場合, the chassis-mechanism shall 一人で実施できる手順を備える
10. The chassis-mechanism shall 台上で実施する確認項目そのものの実施を自身の責務に含めない

### Requirement 6: ゴミ箱固定アダプタ（底を抜いた缶を残った縁で掴む）

**Objective:** As a 機構を設計する開発者, I want 底を抜いたゴミ箱を、残った縁と円錐台の側壁で掴むアダプタが設計され、依存する採寸値が実測で裏付けられていること, so that 缶の内側を段積み土台の空間として使えるようにしつつ、円筒前提で作って現物と合わない事態と、仮値の上に受け面が乗る事態を避けられる

_出典: A-2 / A-3 / `brief.md`「Scope」/ roadmap.md「テーパーは設計へ2方向に効く」/ catch-mechanism 要件 10.1 / **catch-mechanism 決定 3（改訂版・`bottom_modification = "bottom_removed"`）**_

> ⚠️ **本要件は改訂されている。** 当初は「底が残っている缶を、円錐台を受ける**座**で下から支える」設計であった。
> 上流 `catch-mechanism` の決定 3 が改訂され底の抜き取りが許可されたため、
> アダプタは**座ではなく側壁のクランプ**になり、缶の内側は要件 7 の段積み土台が使う。
> ⚠️ **切断は不可逆である**（受入基準 11）。

#### Acceptance Criteria

1. The chassis-mechanism shall ゴミ箱固定アダプタの寸法を、上流が公開する底の外径・底の平面部径・テーパー角・底の肉厚から導出する
2. The chassis-mechanism shall アダプタの受け面を円錐台の側面に沿う形状として設計し、円筒を前提としない
3. If アダプタの設計に用いる上流の寸法値の出所が仮値である場合, then the chassis-mechanism shall その値を現物で実測し、上流の設定ファイルの値と出所を更新したうえで設計に用いる
4. The chassis-mechanism shall 上流の設定ファイルに対して、値と出所以外の構造・キー名・単位を変更しない
5. The chassis-mechanism shall ゴミ箱を水平方向および上方向へ拘束する締結を備え、手で加える力で外れないことを確認する手順を備える
6. When ゴミ箱を取り外す場合, the chassis-mechanism shall 駆動ベースを分解せずに着脱できる手順を備える
7. The chassis-mechanism shall アダプタがゴミ箱の開口内径を狭めず、上流が設計した受け口と干渉しないことを検査する
8. The chassis-mechanism shall 締結箇所の配置の根拠を、底を抜いた後に残る縁の幅と側壁の変形しやすさとともに記録する
9. When 上流のゴミ箱の採寸値が更新された場合, the chassis-mechanism shall アダプタの寸法をその値から再導出する
10. The chassis-mechanism shall 底の切り取り径を**本 Spec の寸法パラメータとして保持**し、上流が公開する底の平面部径を上限とする。⚠️ **上限をそのまま採らず、手作業の切断誤差に対する余裕を持たせた値を採る**——切断は工作機械ではなく手で行われる
11. The chassis-mechanism shall 外径と切り取り径の差として残る縁を、⚠️ **缶の重量を受ける座面として用いる**。⚠️ **縁は持ち上げ方向を止めない**——缶は上へ広がる円錐台であり、テーパーは持ち上げでは緩む側である。上方向の拘束は受入基準 5 の締結が担う
12. The chassis-mechanism shall 掴み面を、⚠️ **切り取り径が上限まで振れても縁が載る範囲**に渡って連続させる。⚠️ **切り取り誤差の効き方は片側である**——小さく切れば縁が広くなるだけだが、大きく切れば縁が消える
13. The chassis-mechanism shall 缶の側壁への穴あけを、⚠️ **アダプタの穴をガイドとして行う手順**を備える（手で位置を測って開けない）
14. The chassis-mechanism shall 底の切断と側壁の穴あけを、⚠️ **段の寸法が確定し造形可能性と干渉の検査を通るまで行わない**（いずれも不可逆であり、再調達は一度しか使えない）

### Requirement 7: 搭載物の保持と重心

**Objective:** As a 機構を設計する開発者, I want バッテリ・基板・配線が決められた位置へ確実に保持され、各搭載物の質量と高さが記録されること, so that 低重心化の意図が実物で保たれ、加減速で搭載物が動く事態も起きない

_出典: A-1 / `docs/requirements.md §5` / `docs/drivetrain-spec.md §8`（電源系統）/ `docs/bom.md §D / §E` / roadmap.md「性能の律速要因」_

#### Acceptance Criteria

1. The chassis-mechanism shall バッテリを機体の最下部へ保持する
2. When バッテリを充電のために取り外す場合, the chassis-mechanism shall 駆動ベースおよび基板トレイを分解せずに着脱できる手順を備える
3. The chassis-mechanism shall バッテリ・基板・配線を、機体を手で傾けても保持位置から動かない拘束で保持する
4. The chassis-mechanism shall 基板トレイに、モータドライバ3台・制御基板・電圧監視回路・5V 生成回路の取付箇所を設ける
5. The chassis-mechanism shall 発熱する部品の周囲に空気が流れる隙間を確保し、その量を寸法パラメータとして保持する
6. The chassis-mechanism shall 配線ガイドを、モータ配線・エンコーダ配線・電源配線が回転部および床へ接触しない経路として設ける
7. The chassis-mechanism shall モータ配線・エンコーダ配線・電源配線を、取り違えが起きないよう識別できる形で分離して通す
8. The chassis-mechanism shall 各搭載物の質量と保持高さを記録し、合成重心の見積もりを導出する
9. The chassis-mechanism shall 合成重心の見積もりを未実測の推定として扱い、合否条件に用いない
10. The chassis-mechanism shall 底を抜いたゴミ箱の内側へ段積み土台を通し、各段の高さと担当する搭載物を記録する
11. The chassis-mechanism shall 各段の外形を、その高さにおける缶の内径から導出する。⚠️ **缶はテーパーで上へ広がるため、段の使える径は高さごとに異なる**
12. The chassis-mechanism shall 最上段を受け止め面とし、⚠️ **上流が定める緩衝材用の平面の最小径（`retention.liner_flat_min_diameter_mm`）以上の平面**を持たせる（底を抜いたことで失われた平面を、ここが肩代わりする）
13. The chassis-mechanism shall 段が造形可能寸法を超える場合、上流の分割数導出に従って分割する
14. The chassis-mechanism shall 組立の順序が**幾何的に成立する**ことを検査する。⚠️ **各部品が、その時点で既に置かれている部品と干渉せずに所定の位置へ到達できる経路を持つ**こと。⚠️ **「組み上がった状態で干渉しない」ことは、組み上げられることを意味しない**——缶は上へ広がる円錐台であり、切り取った開口より大きい段はそこを通れない

### Requirement 8: 電源系の機構的決着（OQ-11 / OQ-12 の決着）

**Objective:** As a 機構を設計する開発者, I want メイン電源スイッチと電源分岐端子の要否・位置・方式が根拠とともに決着すること, so that 決着期限が「機構設計時」と定められた未決事項が、機体が組み上がった後も残り続けることがない

_出典: `docs/open-questions.md` OQ-11 / OQ-12 / OQ-13 / `docs/drivetrain-spec.md §8`（電源系統・メインヒューズ）/ `tech.md` 開発標準2_

#### Acceptance Criteria

1. The chassis-mechanism shall メイン電源スイッチの要否と取付位置を決定し、判断の根拠とともに記録する
2. The chassis-mechanism shall 電源分岐端子の要否と方式を決定し、判断の根拠とともに記録する
3. Where メイン電源スイッチを設ける場合, the chassis-mechanism shall 機体が整備スタンドへ載った状態と床へ接地した状態の双方で操作できる位置へ取り付ける
4. Where 電源分岐端子を設ける場合, the chassis-mechanism shall その保持箇所と配線経路を配線ガイドの一部として設計する
5. The chassis-mechanism shall 主ヒューズをバッテリ直近へ配置できる保持箇所を設ける
6. The chassis-mechanism shall 物理的な非常停止手段の決着を自身の対象から除外し、メイン電源スイッチで兼ねられるかについての判断材料のみを記録する
7. The chassis-mechanism shall 非常停止手段を後から追加できる取付余地を残す
8. The chassis-mechanism shall 無線に依存する停止手段を、機構上の安全装置として扱わない

### Requirement 9: 造形・組立と組付け手順

**Objective:** As a 機体を組み立てる開発者, I want 設計した部品が実際に造形されて組み上がり、滑りや取り違えを防ぐ操作が手順として明示されること, so that 通電してからでは切り分けの難しい不具合を、通電前に潰せる

_出典: A-1 / A-8 / `docs/bom.md §B`（トルク伝達は M4 止めネジ1本）/ `docs/drivetrain-spec.md §11 #4b / #4d` / `brief.md`「Out」_

#### Acceptance Criteria

1. The chassis-mechanism shall 造形に先立ち、部品同士および搭載物との干渉が無いことを組み上がった状態の形状に対して確認する
2. The chassis-mechanism shall 設計した各部品を造形し、造形した部品で機体を組み立てる
3. The chassis-mechanism shall 組立手順を、部品・締結要素・工具・順序が読み取れる形で記録する
4. The chassis-mechanism shall ハブの止めネジをモータシャフトの平面部へ当てる操作を、組立手順の中で明示する
5. When 組立後に手でホイールを回した場合, the chassis-mechanism shall ハブがシャフトに対して滑らないことを確認する手順を備える
6. The chassis-mechanism shall ホイールの取付穴群とハブのボルト円の対応を組立前に確認する操作を、組立手順の中で明示する
7. If 造形した部品が組立時に干渉する場合、または締結できない場合, then the chassis-mechanism shall 該当箇所と設計値との差を記録し、寸法パラメータの更新として反映する
8. The chassis-mechanism shall 組立の完了を、モータへ通電することなく判定する
9. The chassis-mechanism shall 走行・通電・エンコーダの校正・性能値の決定を自身の対象に含めない

### Requirement 10: 実測と下流設定への還元

**Objective:** As a 性能を評価する開発者, I want 実測重量・重心位置・荷重下の実効ホイール径が測定され、実効ホイール径がシミュレータ設定へ還元されること, so that 「予測は合っているのに届かない」という最も切り分けにくい系統誤差を、走らせる前に取り除ける

_出典: A-1 / A-8 / `docs/drivetrain-spec.md §11 #4a / #12 / #13` / roadmap.md「実効ホイール径 → `wheel_diameter_mm`」/ `configs/trajectory_sim/drivetrain-wheel60.json`_

#### Acceptance Criteria

1. When 機体が組み上がった場合, the chassis-mechanism shall 実測重量を測定し、出所を実測として記録する
2. When 機体が組み上がった場合, the chassis-mechanism shall 重心位置を測定し、測定手順とともに記録する
3. The chassis-mechanism shall 機体の重量がかかった状態でのホイールの実効転がり径を測定し、3個それぞれの値と個体差を記録する
4. The chassis-mechanism shall 実効転がり径の測定手順と、公称値との差を記録する
5. If 測定手順が転動を伴わない場合, then the chassis-mechanism shall その測定が捉えられない範囲を記録し、転動を伴う校正が下流で行われることを明示する
6. When 実効転がり径が確定した場合, the chassis-mechanism shall シミュレータの駆動系設定ファイルのホイール径の値を更新する
7. The chassis-mechanism shall 還元を設定ファイルの値のみで行い、シミュレータの実装コードを変更しない
8. The chassis-mechanism shall 還元先の値と本 Spec が保持する値が一致することを検査する
9. The chassis-mechanism shall 実測値と概算値を、利用側が区別できる形で記録する

### Requirement 11: 下流仕様への提供と責務の限定

**Objective:** As a `teleop-bringup` / `m2-motion-validation` の実施者, I want 機体の幾何・実測値・整備スタンドの使用条件を再測定せずに参照できること, so that 同じ値が2箇所で食い違う事態と、前提の取り違えによる事故の両方を避けられる

_出典: A-3 / A-10 / `brief.md`「Downstream」「Out of Boundary」/ roadmap.md「Specs (dependency order)」/ `firmware/lib/drivetrain_control` の設定項目_

#### Acceptance Criteria

1. The chassis-mechanism shall ホイール配置半径と各輪の取付角を、下流が制御設定として参照できる形で公開する
2. The chassis-mechanism shall 実効ホイール径を、下流が制御設定として参照できる形で公開する
3. The chassis-mechanism shall 実測重量と重心位置を、下流が性能評価の文脈として参照できる形で公開する
4. The chassis-mechanism shall 整備スタンドの使用手順と、保持できる条件および保持できない条件を、下流が使う側として参照できる形で公開する
5. The chassis-mechanism shall 公開する各項目に出所を併記し、仮値と実測値を利用側が区別できるようにする
6. When 公開している項目の意味・単位・構造が変わる場合, the chassis-mechanism shall その変更を下流の再検証が必要な変更として扱う
7. The chassis-mechanism shall 決着させた未決事項について、決定内容と根拠を後から決定記録へ移せる形で残す
8. The chassis-mechanism shall 制御ロジックの実装、ペリフェラル実装、性能値の決定、緩衝ライナーの判断、センサー固定治具の設計、およびフレーム剛性の実測を自身の責務に含めない
9. The chassis-mechanism shall 自身の完了を、初通電走行の達成として扱わない
