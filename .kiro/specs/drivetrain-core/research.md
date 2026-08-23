# Research & Design Decisions: drivetrain-core

## Summary

- **Feature**: `drivetrain-core`
- **Discovery Scope**: New Feature（グリーンフィールド。リポジトリに C++ ソースは1行も存在しない）
- **Key Findings**:
  1. **`framework = espidf` では PlatformIO の LDF（`lib/` 私有ライブラリ）が効かない。** ESP-IDF は CMake のコンポーネント探索で構築されるため、`lib/` に置いただけの純ロジックはファームウェアビルドからリンクされない。brief.md が前提にしていた「`lib/control/` に置けば3環境からリンクできる」は **そのままでは成立しない**。→ 1ディレクトリに `CMakeLists.txt`（IDF 用）と `library.json`（native 用）の**2つのマニフェストを同居させる**構成で解決する
  2. **時刻源はポートではなく引数にする。** A-3 の「制御ステップは呼び出し側から駆動され、時刻は引数として渡る」を素直に実装すると `ClockPort` は不要になる。実装が1つしかないインターフェースを作らないという簡素化にも合致する
  3. **決定性の主張範囲を「同一プラットフォーム内の再現性」に限定する。** 固定幅整数を使う理由はオーバーフロー挙動の一致であって、浮動小数点のビット一致ではない。三角関数の実装差はホストと Xtensa で一致しない可能性があるため、「ホストと実機でビット単位に同一の出力が出る」とは主張しない
  4. **`float`（binary32）を採用し `double` を禁止する。** classic ESP32（Xtensa LX6）は単精度 FPU を持つが倍精度はソフトウェアエミュレーションであり、制御ループに乗せられない
  5. **保護①（モータロック）だけが輪ごと、②③④は機体全体**という非対称がある。単一状態機械にまとめると状態数が 2^4 × 3輪 に膨らむため、独立した検出器を合成する構成にする

---

## Research Log

### PlatformIO × ESP-IDF における「純ロジックの共有」の成立条件

- **Context**: 要件 1.2 は「駆動中核ロジックを、3つのビルド構成すべてから同一のソースとしてリンクできる形に配置する」ことを要求する。brief.md の技術検証は「`lib/control/` に置き `native` とファーム両環境からリンクする」と結論していたが、この検証は `framework = arduino` を前提にした一般論だった可能性がある
- **Sources Consulted**:
  - [PlatformIO: Shared Code (Unit Testing)](https://docs.platformio.org/en/stable/advanced/unit-testing/structure/shared-code.html)
  - [PlatformIO: Espressif IoT Development Framework](https://docs.platformio.org/en/stable/frameworks/espidf.html)
  - [platformio-docs #281: When using esp-idf, libraries downloaded by PlatformIO are not found](https://github.com/platformio/platformio-docs/issues/281)
  - [PlatformIO Community: ESP-IDF: Libraries vs components](https://community.platformio.org/t/esp-idf-libraries-vs-components/47608)
- **Findings**:
  - テスト側の事実は brief.md のとおり: PlatformIO のテストランナーは既定で `src_dir` をビルドしない。回避策は `test_build_src` だが、公式ドキュメントは**「コードを `lib_dir` の私有ライブラリへ分割する方が推奨」**としている。`test_build_src = true` を使うと `main()` / `app_main()` を `#ifndef PIO_UNIT_TESTING` で囲む必要が生じる
  - しかし `framework = espidf` の場合、ビルドは ESP-IDF の CMake コンポーネントシステムが担う。PlatformIO 公式ドキュメントが示す拡張手段は**「プロジェクトルートの `components` フォルダ」または `EXTRA_COMPONENT_DIRS`** であり、`lib/` の私有ライブラリを IDF ビルドへ引き込む経路は文書化されていない。実際に「`lib/` に置いた自作ライブラリが見つからない」という報告が公式 docs リポジトリの issue として存在する
  - IDF 側の `EXTRA_COMPONENT_DIRS` は、指定したディレクトリ自身が `CMakeLists.txt` を持つ場合はそれを1コンポーネントとして扱い、持たない場合は子ディレクトリを走査する
- **Implications**:
  - 純ロジックのディレクトリに **`CMakeLists.txt`（`idf_component_register`）と `library.json`（PlatformIO ライブラリマニフェスト）を両方置く**。ファーム2環境は IDF のコンポーネントとして、`native` 環境は PlatformIO の LDF が発見する私有ライブラリとして、**同一のソースを2つの経路からリンクする**
  - ルート `CMakeLists.txt` で `EXTRA_COMPONENT_DIRS` に**純ロジックのディレクトリを直接指す**。`lib/` 全体を指さないことで、テスト専用ライブラリ（プラントモデル・偽ポート）がファームウェアへ混入する経路を構造的に断つ（要件 16.3）
  - ファーム2環境では `lib_ldf_mode = off` を設定し、PlatformIO 側の LDF が `lib/` を走査してテスト専用ライブラリを拾う可能性を潰す
  - この構成が成立することは**最初のタスク（ビルド骨組み）の完了条件そのもの**にする。3環境がビルド／テストできない骨組みは、以降の全タスクの前提を壊す

### pioarduino プラットフォームの pin 対象

- **Context**: 要件 1.7「依存する外部プラットフォーム定義を、更新によって内容が変化しない形に固定する」。roadmap は「`stable` や `#develop` ではなくリリース zip を pin する」と決めているが、対象バージョンが未指定
- **Sources Consulted**:
  - [pioarduino/platform-espressif32 Releases](https://github.com/pioarduino/platform-espressif32/releases)
  - [sivar2311/platform-espressif32-versions](https://github.com/sivar2311/platform-espressif32-versions)
- **Findings**:
  - 公式 `platformio/platform-espressif32` は 7.0.1（2024年5月）で凍結（roadmap 既述）。Arduino 3.x / IDF 5.x を使うには pioarduino フォークが必要
  - 2026-08-23 時点の直近リリース: `55.03.311`（Arduino 3.3.11 / ESP-IDF 5.5.5, 2026-07-24）、`55.03.39`（Arduino 3.3.9 / IDF 5.5.4）、`55.03.38-1`、`55.03.38`、`55.03.37`
  - 取得 URL は `https://github.com/pioarduino/platform-espressif32/releases/download/<TAG>/platform-espressif32.zip` の形
- **Implications**:
  - `platform =` に上記のリリース zip URL をタグ込みで直書きする。`stable` / `#develop` / セマンティックレンジは使わない
  - 版の更新は**明示的な変更**としてのみ起きる。静的検査で「`releases/download/<タグ>/` を含む URL であること」を回帰検証する
  - IDF 5.5.x は roadmap が出発点に挙げた `esp-idf-arduino-bluepad32-template`（IDF 5.4.x 系）より新しい。**Bluepad32 の統合可否は `teleop-bringup` の検証範囲**であり、本 Spec は無線を一切持たないためこの差分の影響を受けない。ただし `teleop-bringup` が版を下げる判断をする可能性は残るため、版の変更を再検証トリガに挙げる

### Arduino-as-component を本 Spec で導入するか

- **Context**: roadmap の決定表は「`framework = espidf` + Arduino-as-component を**両ビルドで**使う」としている
- **Findings**:
  - roadmap がその決定に付した理由は「Bluepad32 が `framework = arduino` の `lib_deps` として導入できない」「片方だけ espidf にするとレイアウトが `main/` と `src/` で割れる」の2点。**どちらも `framework = espidf` を両環境で使えば解消する**。Arduino-as-component の有無はプロジェクトレイアウトを変えない
  - drivetrain-core は Arduino API を一切使わない（要件 2.2 が `Arduino.h` の参照を禁じている）
- **Implications**:
  - 本 Spec は**両ファーム環境を `framework = espidf` のみで構成する**。Arduino-as-component（`espressif/arduino-esp32` の managed component 追加）は Bluepad32 が必要とする `teleop` 環境でのみ `teleop-bringup` が導入する
  - roadmap の決定に反していない: レイアウト統一という決定の目的は満たされ、本番ビルドに不要な Arduino コアを載せない分だけ要件 1.4 に近づく

### ESP32（Xtensa LX6）における浮動小数点と決定性

- **Context**: 要件 4 が固定幅整数を要求し、要件 3.3 / 16.5 が決定性を要求する。「すべて整数（固定小数点）で書くべきか」を判断する必要がある
- **Findings**:
  - classic ESP32 は**単精度（binary32）FPU をハードウェアで持つが、倍精度はソフトウェアエミュレーション**。`double` を制御ループに使うと周期を守れない
  - IEEE-754 の四則演算と `sqrt` は正しく丸められるため、同じ演算列であればホスト（x86-64 SSE2）と Xtensa で同じ結果になる。一方 `sin` / `cos` などの超越関数は libm 実装依存であり一致が保証されない
  - 固定幅整数が必要な本当の理由は、roadmap が指摘するとおり **`long` がホスト 8 バイト / ESP32 4 バイトであること**、すなわちエンコーダ累積カウンタと積分項の**オーバーフロー挙動がホストテストで再現されない**ことにある。浮動小数点のビット一致とは別の問題である
- **Implications**:
  - 連続量（速度・位置・デューティ・姿勢角）は `float`、離散量・累積量・時刻・電圧は固定幅整数（`int32_t` / `int64_t`）。`double` は禁止し静的検査で回帰させる
  - 三角関数は**設定検証時に1回だけ**評価して逆運動学行列に畳み込み、制御ループ内では行列積のみを行う。周期あたりの計算量が減るうえ、プラットフォーム差の影響点が「設定時の1回」に閉じる
  - **決定性の主張範囲を明記する**: 「同一プラットフォーム上で、同一の入力列と時刻列に対して同一の出力列を返す」（要件 3.3 / 16.5 の文言そのもの）。ホストと実機のビット一致は主張しない。要件 16.6 の「ホスト上で得た結果を実機性能の主張として扱わない」と同じ性質の但し書きである

### ハードウェアカウンタの折り返しと安全なサンプリング間隔

- **Context**: 要件 7.1〜7.4。roadmap が「PCNT のハードウェアカウンタは符号付き 16bit。桁上げを累積しないと数秒でオドメトリが壊れる」と警告している
- **Findings**:
  - 折り返し検出の標準手法は「前回値との差を法 M（= 65536）で最短経路に折り返す」もの。前提条件は **サンプリング間隔中の真の変化量が M/2 未満**であること
  - 本機の最悪値: 11 PPR × 減速比 約19 × 4逓倍 ≈ 836 counts/出力軸1回転（目安値。実測校正対象）。無負荷 530 RPM で **約 7,385 counts/s**
  - M/2 = 32,768 counts → **約 4.4 秒**まで折り返しを取り違えない。想定制御周期 5〜10 ms（100〜200 Hz）に対して 3 桁近い余裕がある
- **Implications**:
  - 最短経路方式で十分。前提条件（間隔 < M/2 カウント）を累積器の事前条件として明記し、ホストテストで正転・逆転の両方向の折り返しを回帰させる
  - 法 M は**パラメータ**にする。`teleop-bringup` がハードウェアカウンタを自由走行させるか watch point でゼロリセットするかを選べるようにするため。値を本 Spec が持たない方針（要件 15）とも一致する
  - ポートの契約は 64bit 累積カウント（A-11 の決定どおり）。累積器そのものは本 Spec がアダプタへ提供する

### ADC 生値からバッテリ電圧への換算方式

- **Context**: 要件 11.7 は「分圧比と非線形補正を、外部から与えられるパラメータを伴う純ロジックとして提供する」ことを要求する。補正の関数形は design で決める
- **Findings**:
  - `docs/drivetrain-spec.md` §9.1 は分圧比 27/(100+27) ≈ 0.2126 を与え、同時に **「テスターでバッテリー実電圧を測定し ADC 変換係数をキャリブレーションする。ESP32 の ADC は素の値が非線形かつ個体差がある」** と明記している
  - roadmap も「ESP32 の生 ADC は両端で非線形なのでカーブフィッティング補正を入れる。LiPo カットオフ精度に直結」と指摘している
  - 想定される校正作業は「数点で実電圧を測る」ことであり、得られるのは**点列**である
- **Implications**:
  - **単調増加を検証した区分線形テーブル（(生値, 実電圧 mV) の点列）** を採用する。校正作業の出力そのものを設定として受け取れる形であり、点が2点のときは純粋な線形（分圧比のみ）へ自然に縮退する
  - 多項式フィットは採用しない: 係数が校正作業の出力形と一致せず、外挿時の振る舞いが直感に反する（LiPo カットオフ精度に直結する箇所で外挿が暴れるのは避ける）
  - 電圧は **mV の `int32_t`** で扱う。移動平均・ヒステリシス・継続時間判定がすべて整数演算になり、決定性の議論が不要になる。距離 mm / 時刻 ms と同じ「基本単位の 1/1000 を整数で持つ」流儀に揃う

### 保護の構成: 単一状態機械か、独立検出器の合成か

- **Context**: requirements.md「design フェーズで決めるもの」が明示的に挙げている論点
- **Findings**:
  - 4つの保護は入力も復帰方式も適用範囲も異なる。特に**①モータロックだけが輪ごと**（要件 10.1「該当する輪の出力を停止する」10.3「判定を輪ごとに独立して行う」）で、②③④は機体全体に効く
  - 単一の状態機械にまとめると、状態空間は保護4種の直積に輪3本を掛けたものになる
  - 要件 14.5 は保護ごとに保持／自動解除を設定可能にすることを要求しており、これは各保護が独立した状態を持つことを事実上要求している
- **Implications**:
  - **独立した検出器4つ ＋ それらを合成する `ProtectionSupervisor`** を採る。Supervisor は「機体全体の遮断理由ビットマスク」と「輪ごとの遮断理由ビットマスク」の2つを出す
  - ③ PWM 上限は他の3つと性質が違う（遮断ではなく**上限の決定**）。トリップ状態を持たせず、`float` の上限値を返す純関数的な部品として扱う

---

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | 判定 |
|--------|-------------|-----------|---------------------|------|
| **Ports & Adapters（採用）** | 純ロジックが抽象ポートだけに依存し、ペリフェラル実装は下流 Spec が注入する | A-2 の「境界＝フレームワーク固有ヘッダを include するか否か」をそのまま構造にできる。ホストで全ロジックを回せる | ポートの粒度を誤ると判断ロジックがアダプタ側へ沈む | **採用**。`prediction-core` の「実行時依存を最小に保つ中核」の先例と同型 |
| 単一の `DrivetrainController` クラスに全部入れる | 1クラスで逆運動学・PID・保護を持つ | ファイル数が最小 | 保護の状態機械を単体でテストできない。要件 14.7 / 16.1 を満たせない | 不採用 |
| FreeRTOS タスク分割を前提にした設計 | 制御・通信・監視をタスクに割る | 実機での周期安定性 | 本 Spec の成果物がホストで回らなくなる。要件 3.2「自らループを回さない」に反する | 不採用。タスク配置は `teleop-bringup` の裁量 |
| 全て固定小数点で実装 | 浮動小数点を使わない | 完全な整数決定性 | classic ESP32 は単精度 FPU を持つため利得がない。スケーリング設計の誤りという新しいバグ源が増える | 不採用（上記「浮動小数点と決定性」参照） |

---

## Design Decisions

### Decision: 純ロジックを「2マニフェスト同居ディレクトリ」として配置する

- **Context**: 要件 1.2（3構成から同一ソースをリンク）と、`framework = espidf` では PlatformIO の LDF が効かないという発見
- **Alternatives Considered**:
  1. `test_build_src = yes` で `src/` をテストからも見る — 公式が非推奨。`app_main()` を `#ifndef PIO_UNIT_TESTING` で囲む必要が生じ、「本体コードにテスト都合の分岐が入る」
  2. 純ロジックを `components/` に置き、`native` 環境では `lib_extra_dirs = components` で拾う — `lib_extra_dirs` は非推奨で次のメジャーで削除予定
  3. 純ロジックを2箇所へコピーする — `tech.md` 開発標準3 が禁じる二重実装
- **Selected Approach**: `firmware/lib/drivetrain_control/` に `CMakeLists.txt`（`idf_component_register`）と `library.json`（PlatformIO ライブラリ）を同居させ、ルート `CMakeLists.txt` の `EXTRA_COMPONENT_DIRS` でこのディレクトリを**直接**指す。ファーム2環境は `lib_ldf_mode = off`
- **Rationale**: 1ソース・1ディレクトリのまま、IDF と PlatformIO という2つのビルドシステムの発見規則を両方満たす。テスト専用ライブラリ（`lib/test_support/`）は `EXTRA_COMPONENT_DIRS` に含まれないため、ファームウェアへ混入する経路が構造的に存在しない（要件 16.3）
- **Trade-offs**: 1ディレクトリに2つのビルドマニフェストが並ぶ。ファイルを追加したとき **`CMakeLists.txt` の `SRCS` 更新を忘れると `native` だけ通ってファームで落ちる**。→ 静的検査で「`src/*.cpp` の集合と `CMakeLists.txt` の `SRCS` が一致すること」を回帰させる
- **Follow-up**: 最初のタスク（骨組み）の完了条件を「3環境すべてがビルド／テストを完走すること」にする

### Decision: 時刻源はポートではなく `step(TimeMs now)` の引数

- **Context**: A-3、要件 3.1〜3.6
- **Alternatives Considered**:
  1. `ClockPort` インターフェースを定義し注入する — 実装が1つしかない抽象になる。要件 3.2「自らループを回さない」と組み合わせると、結局呼び出し側が時刻を取得して渡すのと等価
  2. `millis()` を直接呼ぶ — ホストに存在しない。この Spec の前提が崩れる
- **Selected Approach**: `TimeMs = int64_t`（単調増加ミリ秒）を、**状態を変更するすべての公開メソッドの引数**にする。ポートは3種（エンコーダ／出力／電圧）のみ
- **Rationale**: 「実装が1つしかないインターフェースは指示の階層を1段増やすだけ」という簡素化の原則。要件 3.6「時刻を伴わない状態更新の入口を持たない」は、引数方式のほうが機械的に検査しやすい
- **Trade-offs**: 呼び出し側（`teleop-bringup` の `app_main`）が時刻取得の責務を負う。`esp_timer_get_time()`（µs, `int64_t`）を 1000 で割るだけなので薄い
- **Follow-up**: `int64_t` ms を採ることで Arduino `millis()` の 32bit 折り返し（約 49.7 日）を経過時間計算の考慮対象から外せる。アダプタの契約として明記する

### Decision: 2つの指令入口を「投入時点で輪目標速度へ正規化」して1本にする

- **Context**: 要件 5.1（機体速度指令）と 5.8（輪ごとの直接指令）という2つの入口があり、5.9 は「保護①〜④を同一に適用する」ことを、5.2 は「指令元の種別によって処理を分岐させない」ことを要求する
- **Alternatives Considered**:
  1. 指令の種別タグを内部に持ち、制御ステップ側で分岐する — 分岐が保護の適用箇所より上流に残り、5.9 の「同一に適用」が実装の注意深さに依存する
- **Selected Approach**: `submitBodyVelocity()` は**投入された時点で逆運動学を通し**、`submitWheelVelocities()` と同じ「輪目標速度 ＋ 有効時刻」へ落として保持する。制御ステップ以降は指令の出自を知らない
- **Rationale**: 5.9 を実装の注意深さではなく**構造**で保証する。要件 6.5（全輪の縮小）も逆運動学の直後に置けるので、輪単体指令には縮小が掛からない（1輪だけ回す用途なので方向保存の概念がない）という違いも自然に表現できる
- **Trade-offs**: 逆運動学の実行が制御ステップではなく指令投入時になるため、設定検証が指令受付より前に完了している必要がある。`configure()` 前の指令は拒否する
- **Follow-up**: 輪単体指令には要件 6.5 の等比縮小を適用せず、各輪を個別に上限で飽和させる。この差を設計文書に明記する

### Decision: 逆運動学を 3×3 行列とその逆行列として設定時に構築する

- **Context**: 要件 6.2（取付角と半径はパラメータ）、6.3 と 8.4（逆運動学 → 順運動学の往復一致）、6.6（輪番号と符号の定義を1箇所に）
- **Selected Approach**: 設定検証時に `M`（行 i = `[-sin α_i, cos α_i, R]`）とその逆行列 `M⁻¹` を構築して保持する。順運動学は `M⁻¹`。`det(M)` が閾値以下の配置は設定エラーとして拒否する
- **Rationale**: 3輪3自由度なので `M` は正方行列であり、擬似逆行列ではなく**厳密な逆行列**が取れる。要件 6.3 / 8.4 の往復一致が浮動小数点誤差の範囲で構造的に成立する。三角関数の評価が設定時の1回に閉じる
- **Trade-offs**: 退化配置（3輪が同一直線上・角度が重複）を弾く検証が必要
- **Follow-up**: 120° 等配置を安全に組み立てる補助関数を用意し、`sin` / `cos` の手書きを設計上禁止する（要件 15.6）

### Decision: オドメトリの積分は中点法（姿勢角の中点で回転させる）

- **Context**: requirements.md「design フェーズで決めるもの」が積分方式を挙げている
- **Alternatives Considered**:
  1. 前進オイラー — 曲線走行時に系統的なドリフトが出る
  2. 厳密な円弧近似 — `sin`/`cos` を毎周期呼ぶ
- **Selected Approach**: 機体座標系の変位を `θ + Δθ/2` で回転させて世界座標系へ積む
- **Rationale**: `teleop-bringup` の M2a-2 は**手動で正方形を走らせて出発点との誤差を測る**。前進オイラーだと旋回のたびに系統誤差が乗り、それが「逆運動学のバグ」と区別できなくなる。**この Spec の存在理由（実機で潰す問題を配線・符号・実測値に絞る）と直接ぶつかる**
- **Trade-offs**: 周期あたり `sin`/`cos` 各1回。100〜200 Hz の単精度 FPU では無視できる
- **Follow-up**: 曲線軌道での往復テスト（一定 ω での円運動を1周させ、閉じることを確認）をホストテストに含める

### Decision: 速度PID は位置形・微分は計測値側・アンチワインドアップは条件付き積分

- **Context**: requirements.md が形式・ワインドアップ対策・微分項の扱いを design 事項として挙げている。要件 9.4 / 9.5 / 9.6
- **Selected Approach**: 位置形 PID を **2段（`compute` → `commit`）** に分ける。`compute()` は上限でクランプしない生の出力を返し、呼び出し側が3輪まとめて等比縮小してから `commit(実際に適用された値)` を呼ぶ。比例・積分は偏差、**微分は計測速度の変化**に掛ける（設定値変更時のキックを避ける）。積分は (a) 上限 `integral_limit` でのクランプ と (b) `commit()` が受け取った適用値が生の出力より小さく偏差が飽和方向を向いているときそのステップの積分増分を取り消す、の二段。出力が遮断されている間は `holdReset()` で積分項をゼロに保ち、直前計測値を現在値で更新し続ける
- **Rationale**: 要件 12.4 の「すべての輪の出力指令に同一の上限を適用し、指令された運動の方向を保つ」は、**輪ごとの個別クリップでは満たせない**（1輪が飽和した瞬間に方向が崩れる）。デューティのベクトルを一様にスケールする必要があり、そうすると個々の PID は「自分の出力がどこまで実現されたか」を知らない。2段に分けることで、群としての縮小と個々のアンチワインドアップが噛み合う。9.5「遮断解除後の挙動を乱さない」は「遮断中は積分を溜めない・微分に段差を作らない」という2つの具体動作へ落とす。デッドマンを離して握り直す運転が常時起きるテレオペでは、ここが最も体感に出る
- **Trade-offs**: フィードフォワード項を持たないため低速域の追従は ki 依存になる。ゲインは M2a-2 で合わせる範囲であり、本 Spec は値を持たない
- **Follow-up**: 積分項は `float`（binary32、ホストと ESP32 で同一表現）で保持し、`i_limit` によるクランプで発散を封じる。要件 9.6 の「表現範囲を超え得ることを考慮」は、型幅ではなく**クランプ値が設定検証で有限であることを強制する**ことで満たす

### Decision: 保護①の発火は当該輪のみ。機体全体への波及は上位の判断に委ねる

- **Context**: 要件 10.1「該当する輪の出力を停止する」と 14.1「いずれかの保護が成立している場合、外部へ渡す出力指令を遮断値とする」の読み合わせ
- **Selected Approach**: ①は輪ごとの遮断理由として表現し、当該輪のデューティのみ遮断値にする。②③④と出力許可・指令未着は機体全体の遮断理由とする。**輪ごとの遮断理由を `status()` で外へ出す**
- **Rationale**: 10.1 と 10.3 の文言に忠実。3輪オムニで1輪だけ止めると機体は旋回するため、「全停止させるべきか」は運転上の判断だが、**その判断材料（どの輪がロックしたか）を核が提供し、停止させる手段（出力許可の解除）も既にある**。核が勝手に方針を決めない
- **Trade-offs**: `teleop-bringup` が輪ごと理由を見て `setOutputEnabled(false)` を呼ばない限り、1輪ロック時に機体は旋回し続ける。**この振る舞いは M2a-1「ホイール拘束」発火試験で必ず観測される**ため、下流の期待として設計文書へ明記する
- **Follow-up**: 新規の未決事項としては登録しない（`open-questions.md` の運用ルール「決め方が書けない項目は課題として成立していない」に照らし、これは観測すれば決まる下流の運転方針である）

### Decision: 電圧換算は単調性を検証した区分線形テーブル

- 上記 Research Log「ADC 生値からバッテリ電圧への換算方式」の結論をそのまま採用する
- **Follow-up**: 点数の上限をコンパイル時定数（動的確保なし）にし、`teleop-bringup` の校正結果をそのまま設定として渡せる形にする

### Decision: 静的境界検査は既存の Python テスト資産へ載せる

- **Context**: 要件 2.4 / 4.6 / 16.3 / 16.7 と、要件 1.3 / 1.4 / 1.6 / 1.7 の「ビルド構成そのものの検査」
- **Alternatives Considered**:
  1. Unity（C++）でソースを読む静的テストを書く — `native` 環境では `<filesystem>` が使えるが、検査対象にはソースだけでなく `platformio.ini` / `CMakeLists.txt` も含まれるため、テキスト処理を C++ で書くことになる
- **Selected Approach**: `tests/firmware/test_firmware_boundaries.py`（pytest）で `firmware/` 配下を静的走査する。振る舞いのテストは Unity（`firmware/test/native/`）が持つ
- **Rationale**: リポジトリには既に `tests/prediction_core/test_packaging.py` / `test_boundaries.py`、`tests/trajectory_sim/test_trajectory_sim_boundaries.py` という**同じ役割の先例**がある。PlatformIO を起動せずに検査できるため、境界違反の検出が最も安価な場所で起きる
- **Trade-offs**: ファームウェアの検証が2つのツールチェーンに分かれる。`tech.md` 開発標準3（本番アルゴリズムの二重実装禁止）には**当たらない** — 静的走査は駆動制御ロジックではない
- **Follow-up**: 何を禁止トークンにするかを設計文書に列挙し、テストと文書がずれない形にする

### Decision: プラントモデルは1次遅れのみ。むだ時間を入れない

- **Context**: 要件 16.2〜16.4。requirements.md が「1次遅れ・むだ時間の有無」を design 事項として挙げている
- **Selected Approach**: デューティ → 車輪速度の1次遅れ ＋ 明示的なストール注入 ＋ 生カウント（法 M で折り返す）の生成。係数は1箇所に集め、「仮値であり合否条件でも実測値でもない」ことをファイル先頭とテスト出力の双方で宣言する
- **Rationale**: 検証したいのは「PID が収束するか」「ロック保護が発火するか」「ウォッチドッグが止めるか」「低電圧ヒステリシスが効くか」であって、プラントの忠実度ではない。むだ時間はゲイン合わせの議論を呼び込むが、ゲインは M2a-2 で実機に対して合わせる
- **Trade-offs**: ここで得た収束特性は実機の収束特性ではない。要件 16.6 の但し書きが効く範囲

---

## Risks & Mitigations

| # | Risk | Mitigation |
|---|---|---|
| R1 | `EXTRA_COMPONENT_DIRS` ＋ `library.json` 同居構成が実際には通らない | 最初のタスクの完了条件を「3環境すべてがビルド／テスト完走」にする。通らない場合の退避は「純ロジックを `components/` へ移し、`native` は `test_build_src` ではなく `build_src_filter` で明示リンク」 |
| R2 | `CMakeLists.txt` の `SRCS` 更新漏れで `native` だけ緑になる | 静的検査で `src/*.cpp` の集合と `SRCS` の一致を回帰させる |
| R3 | 1輪ロック時に機体が旋回し続ける | 輪ごと遮断理由を `status()` へ出し、下流の期待として明記。M2a-1 の発火試験で必ず観測される |
| R4 | `float` の三角関数差でホストと実機の出力が微小に食い違い、「決定的でない」と誤解される | 決定性の主張範囲を「同一プラットフォーム内」と設計文書に明記。三角関数の評価を設定時1回に閉じる |
| R5 | ポートが物理量を返す契約のため、アダプタが換算を自前実装して核の累積器・換算器を使わない | 累積器と電圧換算を**核が提供**し、`teleop-bringup` の期待として「自前実装しない」を明記。ポートの doc に利用先を書く |
| R6 | 保護のパラメータが未実測のまま既定値で埋まり、いつのまにか「仕様」として扱われる | 数値パラメータに既定値を与えない。`configure()` が未設定・範囲外を拒否する。`effectiveConfig()` を起動時ログ用に公開する |
| R7 | pioarduino の pin 版を `teleop-bringup` が Bluepad32 の都合で下げる | 版の変更を再検証トリガに挙げる。核は無線を持たないため影響は骨組みのみ |
| R8 | デューティ 0 が coast か brake かはドライバ配線で決まり、核は指定できない | 遮断値は「デューティ 0」と定義し、coast/brake の対応付けは `teleop-bringup` の責務として境界外に明示する |

---

## References

- [PlatformIO — Shared Code (Unit Testing)](https://docs.platformio.org/en/stable/advanced/unit-testing/structure/shared-code.html) — `test_build_src` が既定 `no` であること、`lib_dir` 私有ライブラリが推奨であること
- [PlatformIO — Espressif IoT Development Framework](https://docs.platformio.org/en/stable/frameworks/espidf.html) — `components` フォルダと `src/CMakeLists.txt` の要求
- [platformio-docs issue #281](https://github.com/platformio/platformio-docs/issues/281) — espidf で `lib/` のライブラリが見つからない事象
- [PlatformIO Community — ESP-IDF: Libraries vs components](https://community.platformio.org/t/esp-idf-libraries-vs-components/47608) — `EXTRA_COMPONENT_DIRS` による回避
- [pioarduino/platform-espressif32 Releases](https://github.com/pioarduino/platform-espressif32/releases) — pin 対象のリリースタグと Arduino / IDF の対応
- `docs/drivetrain-spec.md` §3.2 / §5 / §9.1 / §9.2 / §9.3 / §10 / §10.1 — 駆動系設計の正
- `.kiro/steering/roadmap.md`「ファームウェア構成の決定（2026-08-23 の技術検証で確定）」— 本 Spec が従う前提と、踏みやすい罠の一覧
