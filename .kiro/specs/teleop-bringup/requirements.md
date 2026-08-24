# Requirements Document

## Project Description (Input)

組み立てた3輪オムニ移動体に**初めて通電し、人が手で走らせながら足回りの前提を確認する**段階を担う。
詳細な背景・スコープ・制約は [brief.md](./brief.md) を正とする。

### 誰の、どんな問題か

足回りに初めて通電するとき実際に起きるのは性能不足ではなく、**配線・回転方向・
エンコーダ符号のバグ**である（`docs/requirements.md` M2）。そして**それを自動計測
スクリプトでデバッグするのが、最も切り分けの難しい状況になる。** 計測スクリプトは
「逆運動学が正しい」「モータの回転方向とホイール割当が合っている」「エンコーダの
符号が合っている」ことを暗黙の前提にしているが、**その前提を人が目で確認する段階が
これまで存在しなかった。**

もう一つ、この段階にしかできないことがある。**FR-10（通信途絶フェイルセーフ）の
発火試験**である。FR-10 が守るべき状況（固定側が落ちる・無線が切れる）は M3 で意図的に
再現しにくく、しかも**失敗すると機体が暴走する**。テレオペなら**コントローラの電源
ボタンを押すだけ**で同じ経路を発火させられる。`docs/requirements.md` はこれを
**「M2a の隠れた主目的」**と呼んでいる。

### 現在の状況

- 上流の `drivetrain-core` は**実装完了・`main` へマージ済み**（2026-08-24）。
  純ロジック（逆運動学・速度PID・オドメトリ・保護①〜④）と3つの純粋仮想ポート
  （`EncoderPort` / `MotorOutputPort` / `BatteryVoltagePort`）を提供する。下流は公開ヘッダ
  `drivetrain_control/drivetrain_control.hpp` の1本だけを include する
- **ペリフェラルの実装は存在しない。** ESP32 上で実際にエンコーダを数え、PWM を出し、
  電圧を読むコードが要る。これが本 Spec の中心的な仕事である
- `firmware/platformio.ini` に `[env:teleop]` は既に存在するが、**無線は無効のまま**である。
  `sdkconfig.defaults.teleop` は意図的に作られていない。**Bluetooth の有効化は本 Spec の責務**
  （`drivetrain-core` が境界外として明示的に決定済み）
- 部品状況: **ESP32 DevKit / JGB37-520 モータ / モータドライバ AE-TB67H450 は手元にある**
  （AE-TB67H450 は 2026-08-24 に入手確認済み）。**Nexus 14145 ホイール / 18020 ハブは未着**
- **整備スタンド（ホイールを浮かせる台）が未製作**（`docs/bom.md` §E）
- `docs/drivetrain-spec.md` §10.1 にテレオペの実装方針があるが、
  **第一候補（DualSense → BT Classic → ESP32 直結）は実機未検証**（OQ-16）
- ⚠️ **`drivetrain-core` に開ループ（デューティ直接指令）の入口が無い。**
  公開されている指令入口は機体速度指令と輪単体速度指令の2つだけで、**どちらも必ず速度PID を経由する**。
  一方 `docs/requirements.md` の M2a-1 は「接地・オープンループ（PWM 直接指令）で手動走行」を要求する。
  この齟齬の扱いは下記のとおり 2026-08-24 に決定した（B-18）

### 何が変わるべきか

- 人が DualSense で機体を手動走行させられる
- **M2a-0 / M2a-1 / M2a-2 の3段が、危険度の低い順に完了している**（いきなり接地走行させない）
- **安全機能4種すべての発火試験が済んでいる** — デッドマン解放 / コントローラ電源断 /
  ホイール拘束 / バッテリ消耗
- `ENCODER_COUNTS_PER_WHEEL_REV` が**実測校正されている**（未校正のまま M2b へ進むと、
  移動距離の実測値そのものが信用できない）
- 下流の `m2-motion-validation` が「計測だけ」に集中できる状態になる

---

## 追加入力: 確定済みの方針（B-1〜B-19）

> 本節は Project Description の続きであり、**入力として与えられた確定方針**である。
> 検証可能な形式の要件は下の [`## Requirements`](#requirements) を正とし、
> `B-n` は各要件の**出典参照**として使用する。

### 接続と実施手順

- **B-1**: 接続方式の第一候補は **DualSense → Bluetooth Classic → 移動体 ESP32 に直結**とする。
  PC も未確定の Pi → ESP32 通信方式（OQ-29）も切り分けの輪に入らず、**足回り単体を裸で検証できる**
  ことが理由である。成立しない場合は PC 経由（Serial / UDP）へ落とす
  （`docs/drivetrain-spec.md` §10.1.1、OQ-16）
- **B-2**: 実施は**危険度の低い順**に M2a-0（台上・輪単体）→ M2a-1（接地・開ループ）
  → M2a-2（接地・速度PID 経由）の3段とする。**いきなり接地走行させない**
  （`docs/requirements.md` M2a）
- **B-19**: モータドライバ **AE-TB67H450 は入手済**（2026-08-24 確認）。
  brief.md が着手前の確認事項として挙げていた項目は解消済みである

### 安全と運転上の制約

- **B-3**: **デッドマンは必須**。指定ボタンを**押している間だけ**モータ出力を許可し、
  離した時点で即停止する（`docs/drivetrain-spec.md` §10.1.3）
- **B-4**: ⚠️ **デッドマンも接続断停止も「安全装置」ではなく「運転操作」である。**
  どちらも無線に依存するため、無線が落ちる状況＝まさに暴走しうる状況では機能しない可能性がある。
  **物理的な非常停止手段（OQ-13）をテレオペで代替しない**
- **B-5**: ⚠️ **M2a は定性確認であり、NFR-1 の合否判定に使わない。**
  「手で動かして速そうだった」は判断材料にならない（`docs/requirements.md` M2a）
- **B-16**: DevKit の電波は弱く**数 m 程度**を想定する。**リンク断での停止を前提に置く**

### 上流との境界

- **B-6**: **アダプタに判断ロジックを置かない。** エンコーダの折り返し桁上げは `WrapAccumulator`、
  生の電圧読み値からミリボルトへの換算は `VoltageScaler` を用いる。いずれも `drivetrain-core` が
  核として提供済みであり、**アダプタ側で再実装しない**（`ports.hpp` の契約）
- **B-7**: **ウォッチドッグの入力元は「最後に有効な指令を受けてからの経過時間」だけを見る形を維持する。**
  M3 ではこの入力元を受信メッセージへ差し替えるだけにする。**FR-10 を新規実装にしない**
  （`docs/drivetrain-spec.md` §10 ④の実装メモ）
- **B-18**: ⚠️ **開ループ（デューティ直接指令）の入口は上流の `drivetrain-core` へ追加する**
  （2026-08-24 決定）。テレオペ側が `MotorOutputPort` へ直接書くと保護①〜④をすべて迂回し、
  「M2a より前に保護①〜④を実装する」という安全要件の趣旨を壊すためである
  （保護一式と `ProtectionSupervisor` は `DrivetrainController` の内部構造として意図的に非公開であり、
  テレオペ側から適用する手段が無い）。**本 Spec は開ループの判定・遮断ロジックを所有しない**

### ハードウェア上の制約（調査で判明した罠）

- **B-8**: **DualSense は BR/EDR（BT Classic）のみで BLE 非対応** → **classic ESP32 必須**
  （S3 / C3 / C6 / H2 は不可）
- **B-9**: **PCNT は `driver/pulse_cnt.h`（IDF 5.x の新 API）で自前アダプタを書く。**
  `ESP32Encoder` ライブラリは非推奨の legacy API 上にあり、方向反転時のパルス落ちも報告されている
- **B-10**: ⚠️ **PCNT のハードウェアカウンタは符号付き 16bit（±32767）。**
  桁上げを累積しないと、**「数秒走るとオドメトリが壊れる」形で現れる**
- **B-11**: **グリッチフィルタの上限は 1023 APB クロック**（80MHz で約 12.8 µs）、ユニット単位設定。
  **これは硬い天井であり、それ以上のフィルタリングを前提にしない**
- **B-12**: **ADC1 を使う**（ADC2 は Wi-Fi 動作中に使用不可であり、本番 Wi-Fi ビルドで効いてくる）。
  ESP32 の生 ADC は両端で非線形なので、**カーブフィッティング補正を入れる**
  — LiPo カットオフ閾値の精度に直結する
- **B-13**: **GPIO 34〜39 は入力専用で内部プルアップが無い。**
  オープンコレクタ出力のエンコーダには外部プルアップが要る
- **B-14**: **パーティションテーブルの自前指定が要る。** Arduino core + BTstack BR/EDR HID host で
  約 1.0〜1.4 MB。4MB の既定スキーム（1.31 MB × 2 OTA）では**ぎりぎりか溢れる**。
  テレオペビルドは `huge_app`（3MB、OTA 無し）を指定する
- **B-15**: **ペアリングの罠**: 新しい DualSense ファームの「マルチペア」モードでは無言で失敗する
  既知問題がある。レガシー手順（Create/Share + PS 長押し）を使う。**リンクキーの残留**が
  再接続失敗の頻出原因であり、**「Bluetooth キーを消去する」操作をファームに用意する**
- **B-17**: **BTstack はオープンソースではない**（「個人的利益のためのみ。商用目的・金銭的利得の
  ためには使用不可」条項付き）。**テレオペビルドへ封じ込め、本番成果物はクリーンに保つ**（OQ-42）

---

## design フェーズで決めるもの（requirements では決めない）

- アダプタの内部構造（PCNT ユニット割当、LEDC チャネル・分解能・周波数、ADC のサンプリング方式）
- Bluepad32 の統合形態（Arduino-as-component の組み込み方、コールバック構造）
- 制御ループのタスク構成（FreeRTOS タスク分割、優先度、周期の実現方法）
- ログの出力経路と保持方法（シリアル / ファイル / 吸い出し手順）
- パッド入力から機体速度指令への変換式の具体形（デッドゾーン・カーブの関数形）
- 開ループ入口を `drivetrain-core` へ追加する際の API 形状 — **上流 Spec の design が決める**

## 制約（brief.md / steering より継承）

- **実機必須。かつ部品が一部未着。** ESP32 単体で先行できるのは Bluepad32 / DualSense の
  導通とペアリング挙動（OQ-16）であり、**これは最大の未検証項目でありながらモータもホイールも
  要らずに単体で潰せる**。モータとドライバがあれば PCNT アダプタとエンコーダ符号の確認まで進む。
  **ホイール・ハブ待ちは整備スタンドの支持形状と M2a-1 以降の接地走行すべて**
- テレオペは**開発用の道具であり、完成品の機能ではない**（`docs/drivetrain-spec.md` §10.1）
- `docs/drivetrain-spec.md` が駆動系の設計の正であり、未決事項の唯一の正は
  `docs/open-questions.md` である

---

## Introduction

teleop-bringup は、`drivetrain-core` が確定させた駆動中核ロジックに**実機の身体を与え、
人が手で走らせながら足回りの前提を確認する**段階である。

範囲は `docs/drivetrain-spec.md` §10.1.2 の層構造の**最上段（パッド入力 → `(vx, vy, ω)`）と、
下段が実機へ届くためのペリフェラルアダプタ**にあたる。最上段は M3 で「目標座標 → `(vx, vy, ω)`」へ
差し替わる**捨てても惜しくない層**だが、アダプタと保護の発火経路は本番へそのまま引き継がれる。

本 Spec の価値は3つある。

**1つめは切り分けである。** 組み立て直後に起きるのは性能不足ではなく配線・回転方向・エンコーダ符号の
バグである。これを自動計測スクリプトでデバッグするのは切り分けが最も難しい。
**人が目で見て潰す段階を先に置く**ことで、下流の `m2-motion-validation` が「計測だけ」に集中できる。

**2つめは FR-10 の発火試験である。** `docs/requirements.md` はこれを**「M2a の隠れた主目的」**と呼ぶ。
FR-10 が守るべき状況は M3 で意図的に再現しにくく、しかも失敗すると機体が暴走する。
テレオペなら**コントローラの電源ボタンを押すだけ**で同じ経路を発火させられる。

**3つめは実測校正である。** `ENCODER_COUNTS_PER_WHEEL_REV` が未校正のまま M2b へ進むと、
**移動距離の実測値そのものが信用できない。**

本 Spec は**実機必須**であり、ホイール・ハブの到着待ちで一部が進められない。
ただし**最大の未検証項目（OQ-16 のペアリング挙動）は ESP32 単体で潰せる**ため、
部品待ちをクリティカルパスに置かない形で着手できる。

## Boundary Context

- **In scope**:
  - ESP32 ペリフェラルアダプタの実装 — パルスカウンタ×3（エンコーダ）/ PWM・方向出力 /
    バッテリ電圧読み出し。いずれも `drivetrain-core` が宣言した3ポートの実装として提供する
  - テレオペ専用ビルド構成における無線の有効化と、パーティション構成の指定
  - コントローラの接続・再接続・ペアリング鍵の消去手段、および接続状態の検出
  - パッド入力から指令への変換 — 並進・回転・デッドマン・輪単体選択、
    デッドゾーン・入力カーブ・速度スケール上限
  - 制御ループの駆動（時刻の供給と周期実行）と、`drivetrain-core` の指令入口への投入
  - M2a-0 / M2a-1 / M2a-2 の実施と、その結果の記録
  - `ENCODER_COUNTS_PER_WHEEL_REV` の実測校正
  - 安全機能4種の発火試験
  - テレオペ走行のログ（走行1回＝1レコード）
  - 整備スタンドの製作
  - OQ-16 / OQ-17 / OQ-18 / OQ-15 / OQ-37 / OQ-42 の決着
- **Out of scope**:
  - 逆運動学・速度PID・オドメトリ・保護①〜④の**判定ロジック本体** → `drivetrain-core`
  - **開ループ（デューティ直接指令）の入口と、そこへの保護①〜④の適用** → `drivetrain-core`（B-18）
  - 定量計測・パラメータの実測・NFR-1 の評価 → `m2-motion-validation`
  - 固定側 → 移動体の通信、⑤ジオフェンス、**物理的な非常停止手段（OQ-13）** → M3
  - ゴミ箱本体・固定アダプタの設計 → 機構トラック
  - 保護閾値の**最終確定値**（OQ-14 低電圧閾値 / OQ-22 制御ループ周期）→ `m2-motion-validation`
- **Adjacent expectations**:
  - **`drivetrain-core` は開ループ指令の入口を提供する。** 輪ごとの目標デューティを速度PID を
    経由せずに受け取り、**保護①〜④と PWM 上限は従来どおり適用したうえで**出力する経路である。
    本 Spec はこの入口へ**マッピングするだけ**であり、判定・遮断ロジックを持たない（B-18）
  - `drivetrain-core` が提供する `WrapAccumulator` と `VoltageScaler` を、アダプタは**そのまま利用する**。
    折り返しの桁上げと電圧換算を**アダプタ側で再実装しない**（B-6）
  - `drivetrain-core` の**本番用ビルドから無線が除外されている状態を壊さない**。
    無線を有効化するのはテレオペ用ビルドに限る
  - `m2-motion-validation` は、**校正済みのエンコーダと動作する足回り**を前提として計測に入る。
    本 Spec が校正値を残さなければ、下流の計測値は距離の意味を持たない
  - M3 の本番ファームウェアは、本 Spec が作るアダプタと保護④の発火経路を**そのまま引き継ぐ**。
    ④の入力元だけが差し替わる（B-7）
  - `docs/open-questions.md` が未決事項の唯一の正であり、本 Spec が決着させた項目はそこから外す

---

## Requirements

### Requirement 1: テレオペ専用ビルド構成と無線の有効化

**Objective:** As a 移動体ファームウェアの開発者, I want テレオペ用ビルドでのみ無線が有効になり、本番用ビルドが無線を含まない状態が保たれること, so that 2.4GHz の競合とライセンス上の混入をビルド構成の段階で封じ込められる

_出典: B-8, B-14, B-17_

#### Acceptance Criteria

1. The teleop-bringup shall テレオペ用ビルド構成でのみ無線スタックを有効化する
2. When テレオペ用ビルドへ無線を追加した場合, the teleop-bringup shall 本番用ビルドの成果物へ無線スタックが含まれない状態を維持する
3. The teleop-bringup shall テレオペ用ビルドに、無線スタックを含んだ成果物が収まる容量のパーティション構成を指定する
4. If テレオペ用ビルドと本番用ビルドが同時に指定された場合, then the teleop-bringup shall ビルドを失敗させる
5. The teleop-bringup shall 対象マイコンを classic ESP32 に固定し、Bluetooth Classic を前提とした構成を持つ
6. The teleop-bringup shall 無線ライブラリのライセンス上の制約を、成果物の配布時に誤読されない形で記載する

### Requirement 2: エンコーダ入力アダプタ

**Objective:** As a 移動体ファームウェアの開発者, I want 3輪ぶんのエンコーダが折り返しを含まない累積カウントとして読めること, so that オドメトリが長時間の走行でも破綻しない

_出典: B-6, B-9, B-10, B-11, B-13_

#### Acceptance Criteria

1. The teleop-bringup shall 3輪ぶんのエンコーダそれぞれについて、折り返しを含まない累積カウントを提供する
2. When ハードウェアカウンタが表現範囲の限界を超えて折り返した場合, the teleop-bringup shall 桁上げを累積し、累積カウントの連続性を保つ
3. The teleop-bringup shall 折り返しの桁上げ処理に `drivetrain-core` が提供する累積器を用い、同等の処理を独自に実装しない
4. The teleop-bringup shall 回転方向に応じて累積カウントが増減する向きを、輪ごとに設定で反転できる
5. When 機械的・電気的なノイズによる短いパルスが入力された場合, the teleop-bringup shall そのパルスを計数から除外する
6. The teleop-bringup shall ノイズ除去の設定可能な範囲を、ハードウェアが許す上限の内側に収める
7. Where エンコーダの出力形式が外部プルアップを必要とする場合, the teleop-bringup shall 内部プルアップを持たない入力専用端子へ割り当てない

### Requirement 3: モータ出力アダプタ

**Objective:** As a 移動体ファームウェアの開発者, I want 各輪の出力指令が回転方向を含めてそのままモータへ届くこと, so that 出力段が判断を持たず、遮断が確実に効く

_出典: B-6_

#### Acceptance Criteria

1. The teleop-bringup shall 各輪について、受け取った出力指令の大きさと符号を、モータの回転速度と回転方向へ変換して出力する
2. The teleop-bringup shall 受け取った出力指令に対して独自の制限・補正・判断を加えない
3. When 出力指令がゼロを示した場合, the teleop-bringup shall そのモータへの駆動を停止する
4. The teleop-bringup shall 各輪の回転方向の向きを、輪ごとに設定で反転できる
5. While ファームウェアの起動直後で制御ループがまだ動作していない間, the teleop-bringup shall モータを駆動しない

### Requirement 4: バッテリ電圧アダプタ

**Objective:** As a 移動体ファームウェアの開発者, I want バッテリ電圧がミリボルト単位で読め、読めない状態が区別できること, so that 低電圧保護と PWM 上限が実測値にもとづいて働く

_出典: B-6, B-12_

#### Acceptance Criteria

1. The teleop-bringup shall バッテリ電圧をミリボルト単位で提供する
2. If 電圧の読み値が得られない場合, then the teleop-bringup shall 値が無効であることを呼び出し側へ伝える
3. The teleop-bringup shall 生の読み値からミリボルトへの換算に `drivetrain-core` が提供する換算部品を用い、同等の換算を独自に実装しない
4. The teleop-bringup shall 変換器の入力範囲の両端における非線形性を補正した読み値を提供する
5. The teleop-bringup shall 無線の動作中も使用できる変換器を用いる
6. The teleop-bringup shall 換算に用いた校正値を、実測にもとづいて設定できる形で保持する

### Requirement 5: コントローラの接続と接続状態の扱い

**Objective:** As a テレオペで機体を走らせる操作者, I want コントローラが確実に接続でき、接続が切れたことを機体が検出できること, so that 接続断が暴走ではなく停止として現れる

_出典: B-1, B-8, B-15, B-16_

#### Acceptance Criteria

1. The teleop-bringup shall Bluetooth Classic 経由でコントローラからの入力を受け取る
2. When コントローラを初めて接続する場合, the teleop-bringup shall 実機で再現可能なペアリング手順を提供する
3. When 一度接続したコントローラが再び電源投入された場合, the teleop-bringup shall 手動操作を追加せずに再接続できる
4. The teleop-bringup shall 保存済みのペアリング鍵を操作者が消去する手段を提供する
5. If コントローラとの接続が切断された場合, then the teleop-bringup shall そのことを制御ループが判別できる形で示す
6. The teleop-bringup shall 接続が確立していない状態と、接続はあるが入力が届いていない状態を区別する

### Requirement 6: 入力マッピングと運転上の制約

**Objective:** As a テレオペで機体を走らせる操作者, I want デッドマンを押している間だけ機体が動き、速度が絞られた状態から始められること, so that 初通電の走行を低い危険度で始められる

_出典: B-2, B-3, B-4, B-5_

#### Acceptance Criteria

1. While デッドマンが押されている間, the teleop-bringup shall モータ出力を許可する
2. When デッドマンが離された場合, the teleop-bringup shall モータ出力の許可を取り消す
3. The teleop-bringup shall 左スティックの入力を機体の並進速度指令へ対応付ける
4. The teleop-bringup shall 右スティックの横方向入力を機体の回転速度指令へ対応付ける
5. The teleop-bringup shall 台上での輪単体テストのために、対象とする輪を操作者が選択して単独で回せる手段を提供する
6. The teleop-bringup shall スティックの中立付近に、入力を無効とみなす範囲を設ける
7. The teleop-bringup shall スティックの入力量から指令値への変換に、中立付近の分解能を高める特性を設ける
8. The teleop-bringup shall 指令できる速度の上限を、機体の能力に対して絞った割合として設定できる
9. The teleop-bringup shall 速度の上限・無効範囲・変換特性のいずれも、実走しながら調整できる形で保持する

### Requirement 7: 制御ループの駆動と指令の投入

**Objective:** As a 移動体ファームウェアの開発者, I want 制御ループが一定周期で回り、パッド入力が指令として投入されること, so that 保護①〜④が設計どおりに発火する

_出典: B-6, B-7_

#### Acceptance Criteria

1. The teleop-bringup shall 制御ループを一定周期で実行する
2. The teleop-bringup shall 制御ループの各回に、単調に増加する現在時刻を供給する
3. When 操作者の入力が更新された場合, the teleop-bringup shall その入力を `drivetrain-core` の指令入口へ投入する
4. The teleop-bringup shall 指令の投入と出力許可を、それぞれ独立した操作として扱う
5. The teleop-bringup shall 保護の判定・遮断値の決定を `drivetrain-core` に委ね、独自に実装しない
6. The teleop-bringup shall 指令の途絶を「最後に有効な指令を受けてからの経過時間」としてのみ扱い、コントローラ固有の事情を判定条件へ持ち込まない
7. While 制御ループが停止している間, the teleop-bringup shall モータへの出力を継続しない

### Requirement 8: M2a-0 — 台上での輪単体検証とエンコーダ実測校正

**Objective:** As a 足回りをブリングアップする開発者, I want ホイールを浮かせた状態で1輪ずつ回して配線と符号を確認できること, so that 接地走行の前に配線・符号のバグを潰せる

_出典: B-2, B-6_

#### Acceptance Criteria

1. While 機体が台に乗りホイールが接地していない間, the teleop-bringup shall 操作者が選択した1輪のみを回転させられる
2. When 1輪を回転させた場合, the teleop-bringup shall その輪の回転方向を操作者が観察できる形で駆動する
3. When 1輪を回転させた場合, the teleop-bringup shall その輪の累積カウントの増減方向を操作者が確認できる形で提示する
4. The teleop-bringup shall 各輪について、モータの回転方向とハーネスの対応、およびエンコーダの符号の確認結果を記録として残す
5. The teleop-bringup shall ホイール1回転あたりの実測カウント数を、実際の回転から求めて記録する
6. The teleop-bringup shall 実測して求めたホイール1回転あたりのカウント数を、以降の走行で用いる設定値として反映する

### Requirement 9: M2a-1 — 接地・開ループでの手動走行検証

**Objective:** As a 足回りをブリングアップする開発者, I want 速度PID を経由せずに接地走行して逆運動学の符号を確認できること, so that エンコーダ符号の誤りが接地状態での暴走として現れない

_出典: B-2, B-5, B-18_

#### Acceptance Criteria

1. While 機体が接地している間, the teleop-bringup shall 速度PID を経由しない経路で操作者の指令を出力へ反映できる
2. While 速度PID を経由しない経路で走行している間, the teleop-bringup shall 保護①〜④および出力上限が適用された状態を維持する
3. The teleop-bringup shall 速度PID を経由しない経路を `drivetrain-core` が提供する入口として利用し、保護の適用を迂回する経路を独自に設けない
4. When 機体を前方向へ指令した場合, the teleop-bringup shall その場で回転せず前進することを操作者が確認できる形で駆動する
5. The teleop-bringup shall 逆運動学の符号、発進時の滑り、モータおよびドライバの発熱、急加速時の電圧降下、重心と転倒しやすさの確認結果を記録として残す

### Requirement 10: M2a-2 — 接地・速度PID 経由の手動走行検証

**Objective:** As a 足回りをブリングアップする開発者, I want 速度PID を経由した手動走行でゲインとオドメトリの妥当性を確認できること, so that 下流の定量計測が意味のある値を出せる状態になる

_出典: B-2, B-5_

#### Acceptance Criteria

1. While 機体が接地している間, the teleop-bringup shall 速度PID を経由した経路で操作者の指令を出力へ反映できる
2. The teleop-bringup shall 速度PID のゲインを、実走しながら調整できる形で保持する
3. When 操作者が閉じた経路を一周させた場合, the teleop-bringup shall 出発点と終了点の位置および姿勢の差を提示する
4. The teleop-bringup shall オドメトリの誤差の測定結果を、走行した経路の形状とともに記録として残す
5. The teleop-bringup shall 速度PID のゲインの調整結果を記録として残す

### Requirement 11: 安全機能の発火試験

**Objective:** As a 足回りをブリングアップする開発者, I want 安全機能4種すべてが実機で発火することを確認できること, so that 通信途絶で機体が走り続けないことを M3 より前に確かめられる

_出典: B-3, B-4, B-7, B-16_

#### Acceptance Criteria

1. When 操作者がデッドマンを解放した場合, the teleop-bringup shall モータの出力を停止する
2. When コントローラの電源が切られた、または通信圏外へ出た場合, the teleop-bringup shall 一定時間の経過をもってモータの出力を停止する
3. When ホイールが外力で拘束された場合, the teleop-bringup shall モータロック保護が発火した状態を提示する
4. When バッテリが消耗し電圧が低下した場合, the teleop-bringup shall 低電圧保護が発火した状態を提示する
5. The teleop-bringup shall 4種それぞれについて、発火したこと・発火までの時間・その時点の状態を記録として残す
6. The teleop-bringup shall 通信途絶からモータ停止までの時間を実測し、その値を記録として残す
7. The teleop-bringup shall 発火試験の記録に、これらが運転操作であって物理的な非常停止手段の代替ではない旨を明記する
8. Where コントローラへの触覚的な通知を行う場合, the teleop-bringup shall モータロック保護の発火を操作者へ振動で通知する

### Requirement 12: テレオペ走行の記録

**Objective:** As a 足回りをブリングアップする開発者, I want 走行1回ぶんの記録が後から読み返せること, so that 発火試験と校正の結果が記憶ではなく記録として残る

_出典: B-5_

#### Acceptance Criteria

1. The teleop-bringup shall 走行1回を1つの記録の単位として扱う
2. When 走行が開始された場合, the teleop-bringup shall その走行の記録を開始する
3. The teleop-bringup shall 記録に、指令値・各輪の実測速度・バッテリ電圧・保護の発火状態・出力許可の状態を時刻とともに含める
4. The teleop-bringup shall 記録を機体の外へ取り出す手段を提供する
5. The teleop-bringup shall 走行の記録を、固定側の投擲記録とは別の形式として扱う

### Requirement 13: 整備スタンド

**Objective:** As a 足回りをブリングアップする開発者, I want ホイールを接地させずに機体を保持できる台があること, so that M2a-0 を安全に実施できる

_出典: B-2_

#### Acceptance Criteria

1. The teleop-bringup shall 機体を保持し、3輪すべてを接地させない状態にできる台を提供する
2. While 機体が台に乗っている間, the teleop-bringup shall 3輪すべてが外部と干渉せず自由に回転できる状態を保つ
3. While 輪が回転している間, the teleop-bringup shall 機体が台から滑落しない保持を提供する

### Requirement 14: 未決事項の決着とドキュメントの是正

**Objective:** As a プロジェクトの引き継ぎを受ける開発者, I want 本 Spec が決着させた事項が唯一の正へ反映されること, so that 決着済みの事項が未決として再浮上しない

_出典: B-1, B-3, B-15, B-17_

#### Acceptance Criteria

1. The teleop-bringup shall 接続方式の成立可否を実機で判定し、その結果を決定として記録する
2. The teleop-bringup shall 入力マッピングと初期パラメータの決定値を記録する
3. The teleop-bringup shall 指令ウォッチドッグのタイムアウト値を実測にもとづいて決定し、その根拠とともに記録する
4. The teleop-bringup shall モータロック保護の復帰条件を、発火試験の挙動にもとづいて決定し記録する
5. The teleop-bringup shall テレオペの記録形式を固定側の投擲記録と同じ形にするか否かを決定として記録する
6. The teleop-bringup shall 無線ライブラリのライセンス上の取り扱いを決定として記録する
7. When 未決事項が決着した場合, the teleop-bringup shall その項目を未決事項の一覧から外す
8. If 決着した内容が既存のドキュメントの記述と食い違う場合, then the teleop-bringup shall そのドキュメントを決着後の内容へ是正する

### Requirement 15: ホストで検証できる範囲の維持

**Objective:** As a 移動体ファームウェアの開発者, I want 実機を要さない部分がホストで検証され続けること, so that アダプタ層へ判断ロジックが少しずつ染み出すことを防げる

_出典: B-6, B-18_

#### Acceptance Criteria

1. The teleop-bringup shall 操作者の入力から指令値への変換を、実機を用いずに検証できる形で提供する
2. When 入力の無効範囲・変換特性・速度上限のいずれかを変更した場合, the teleop-bringup shall 実機を用いずにその影響を検証できる
3. The teleop-bringup shall アダプタ層に判断・計算のロジックが持ち込まれていないことを、回帰的に検査する
4. The teleop-bringup shall `drivetrain-core` の既存のホスト向け検証が引き続き成立する状態を維持する
5. If アダプタ層が `drivetrain-core` の公開されていない内部構造へ依存した場合, then the teleop-bringup shall その依存を検査で検出する
