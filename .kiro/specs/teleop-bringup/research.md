# Research & Design Decisions

## Summary

- **Feature**: `teleop-bringup`
- **Discovery Scope**: Extension（既存ファーム骨格への拡張）＋ 新規外部依存1件（Bluepad32 / BTstack）のため Light Discovery に外部検証を追加した
- **Key Findings**:
  - ⚠️ **roadmap 2026-08-23 の「Arduino-as-component を使う」決定を採らない。** Bluepad32 は raw ESP-IDF プラットフォームを持ち、そちらが **IDF v5.5 以降を推奨**する。本プロジェクトの pin 済みプラットフォーム（pioarduino 55.03.311 = **IDF 5.5.5**）と一致する。一方 Arduino 併用テンプレートは **v5.4.2 を要求**しており、pin 済みバージョンと食い違う
  - ⚠️ **BTstack は素の IDF コンポーネントとして取得できない。** Bluepad32 のドキュメントが `integrate_btstack.py` による**パッチ適用＋コンポーネント設置**を手順として定めている。IDF コンポーネントマネージャ（`idf_component.yml`）だけでは解決しない
  - **`firmware/src/teleop/` は既に予約済み。** `firmware/src/CMakeLists.txt` が `DRIVETRAIN_BUILD_TELEOP` 環境変数のときだけ `teleop/*.cpp` を glob する実装が入っており、現在は空で無害。本 Spec の追加先はここで確定している
  - **`[env:teleop]` は `SDKCONFIG_DEFAULTS` を上書きしていない。** 共有の `sdkconfig.defaults` に暗黙フォールバックしており、無線は無効のまま。`platformio.ini` のコメント自身が「teleop-bringup enables Bluetooth on top of it」と本 Spec の責務として明記している

## Research Log

### Bluepad32 を Arduino 併用で入れるか、raw ESP-IDF で入れるか

- **Context**: roadmap の「ファームウェア構成の決定（2026-08-23）」が **`framework = espidf` + Arduino-as-component を両ビルドで使う**と決めており、出発点を `esp-idf-arduino-bluepad32-template` としていた。しかし `drivetrain-core` が実装した `platformio.ini` は **Arduino を一切含まない素の espidf** であり、両者が食い違ったまま本 Spec に渡ってきた
- **Sources Consulted**:
  - [Bluepad32 — ESP32 + ESP-IDF (raw API)](https://bluepad32.readthedocs.io/en/latest/plat_esp32/)
  - [esp-idf-arduino-bluepad32-template README](https://github.com/ricardoquesada/esp-idf-arduino-bluepad32-template/blob/main/README.md)
  - [bluepad32 docs/plat_esp32.md](https://raw.githubusercontent.com/ricardoquesada/bluepad32/main/docs/plat_esp32.md)
- **Findings**:
  - raw ESP-IDF プラットフォームは公式に存在し、**「ESP-IDF v5.5 or newer is recommended」**
  - Arduino 併用テンプレートの README は **「Requires ESP-IDF v5.4.2」**（`git clone -b release/v5.4` を案内）
  - 本プロジェクトの pin 済みプラットフォームは pioarduino 55.03.311 = **IDF 5.5.5**（`firmware/sdkconfig.defaults.production` のコメントに実測記録あり）
  - BTstack の設置は `integrate_btstack.py` によるパッチ適用を伴う。「installs BTstack as a component inside the Bluepad32 source code」
- **Implications**:
  - **raw ESP-IDF を採る。** Arduino 併用は pin 済み IDF バージョンと合わず、合わせるにはプラットフォーム pin を動かすことになる。pin を動かすのは `drivetrain-core` が確定させた再現性（リリース zip 固定）を崩す
  - Arduino コアを積まないぶん**フラッシュ消費が roadmap の見積り（Arduino core + BTstack で 1.0〜1.4MB）より小さくなる**。ただしパーティションの決定は見積りではなく実測に従う（`tech.md` 開発標準1）
  - **BTstack の設置手順がスクリプト依存**である事実は、再現手順として記録が要る。`lib_ldf_mode = off` かつ `framework = espidf` のため、PlatformIO の `lib_deps` 経路は最初から使えない

### ESP32 の端子制約（端子割当の成立条件の根拠）

- **Context**: 要件 2 が成立検査を要求しており、その判定条件は ESP32 classic の実際の端子特性に依拠する。割当表を人が配線に使うため、記憶ではなく出典で固める必要がある
- **Sources Consulted**:
  - [ESP-IDF Programming Guide — GPIO & RTC GPIO (ESP32)](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/peripherals/gpio.html)
  - [ESP32 Pinout Reference — Random Nerd Tutorials](https://randomnerdtutorials.com/esp32-pinout-reference-gpios/)
  - [ESP32 Strapping Pins List](https://www.espboards.dev/blog/esp32-strapping-pins/)
- **Findings**:
  - **ストラッピング端子**: GPIO 0 / 2 / 5 / 12 / 15
  - **入力専用**: GPIO 34 / 35 / 36 / 39。**内部プルアップ・プルダウンを持たない**
  - **内蔵フラッシュ用**: GPIO 6〜11
  - ADC1 は GPIO 32〜39 に割り当てられている
- **Implications**:
  - 要件 2.3 / 2.4 / 2.5 の検査条件がそのまま定数表に落ちる
  - ⚠️ **エンコーダ入力を 34〜39 へ置くと外部プルアップが必須**になる（要件 4.7）。オープンコレクタ出力かどうかが未確認である以上、**入力専用端子を避けるほうが安全側**である

### 既存ビルド骨格の拡張点

- **Context**: 無線の有効化とパーティション指定をどこへ書くかを、既存実装の実測記録に合わせる必要がある
- **Sources Consulted**: `firmware/platformio.ini` / `firmware/src/CMakeLists.txt` / `firmware/sdkconfig.defaults*` / `firmware/src/build_profile.hpp`（いずれもリポジトリ内）
- **Findings**:
  - `[env:production]` は `board_build.cmake_extra_args = -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.defaults.production"` で層を重ねている。**この機構は動くことが実証済み**
  - ⚠️ `build_src_filter` は **espidf では効かない**ことが実測で確認済み（`firmware/src/CMakeLists.txt` のコメント）。ソース選択は CMake configure 時の判断であり、**環境変数**でしか伝わらない（CMake キャッシュ変数は `cmake -P` サブプロセスに届かない）
  - ⚠️ `CONFIG_ESP_WIFI_ENABLED=n` は **プロンプトを持たない Kconfig シンボルのため無視される**（実測済み）。本番の無線除外は `firmware/CMakeLists.txt` の `COMPONENTS` 許可リストが担っている
  - `build_profile.hpp` は TELEOP / PRODUCTION の**排他だけ**を検査する。第3のマクロを足しても排他は壊れない
- **Implications**:
  - 無線有効化は **`sdkconfig.defaults.teleop` を新設し、`[env:teleop]` に `-DSDKCONFIG_DEFAULTS` を追加**する形になる（production と同じ実証済み機構）
  - パーティションは **`board_build.partitions` ではなく `CONFIG_PARTITION_TABLE_*` で指定する**。espidf では PlatformIO の INI オプションが効かない前例があるため、Kconfig 側で指定するほうが確実である
  - ⚠️ **本番の `COMPONENTS` 許可リストに触れない。** teleop 側の変更が本番の無線除外を壊さないことが要件 1.2 の実質的な中身である

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| **Ports & Adapters（採用）** | `drivetrain-core` が宣言した3ポートを ESP32 ペリフェラルで実装し、アプリ層が制御ループを回す | 上流が既にこの形。ホストテスト範囲が痩せない | アダプタへ判断が漏れると崩れる → 静的検査で守る | `ports.hpp` の契約がそのまま境界 |
| 制御ループを核へ持たせる | 核が周期実行とペリフェラル取得を持つ | アプリ層が薄くなる | ⚠️ 核がホストで回せなくなり `drivetrain-core` の設計目的を壊す | **不採用** |
| Arduino ライブラリ層を挟む | `analogRead` / `ledcWrite` 等で書く | 記述が短い | ⚠️ IDF 5.5.5 と Arduino テンプレートの版が食い違う。B-9 が禁じる legacy API へ寄る | **不採用** |

## Design Decisions

### Decision: Bluepad32 を raw ESP-IDF プラットフォームで導入する

- **Context**: roadmap 2026-08-23 が Arduino-as-component を決めていたが、`drivetrain-core` の実装は素の espidf で着地した
- **Alternatives Considered**:
  1. Arduino-as-component（roadmap の当初決定）— `esp-idf-arduino-bluepad32-template` を出発点にする
  2. raw ESP-IDF プラットフォーム — Bluepad32 の ESP-IDF 版を直接コンポーネントとして入れる
- **Selected Approach**: 2。Bluepad32 と BTstack を IDF コンポーネントとして設置し、Arduino コアを積まない
- **Rationale**: pin 済みプラットフォームが IDF 5.5.5 であり、raw 版の推奨（5.5+）と一致する。Arduino テンプレートは 5.4.2 要求で、合わせるにはプラットフォーム pin を動かすしかない。pin はリリース zip 固定という再現性の担保であり、外部ライブラリの都合で動かす対象ではない。加えて既存 `platformio.ini` は Arduino を含まず、採れば構成変更が要らない
- **Trade-offs**: Arduino API（`analogRead` 等）が使えない。ただし B-9 / B-12 が IDF ドライバの直接使用を既に求めているため実質の損失は無い。Bluepad32 の Arduino 向けサンプルはそのまま使えず、ESP-IDF 版サンプルを参照する必要がある
- **Follow-up**: ⚠️ **roadmap の当該決定行を是正する**（要件 16.10）。`integrate_btstack.py` を通す手順を再現可能な形で記録する

### Decision: パーティションは INI と Kconfig の両方で指定し、一致を不変条件として固定する

> ⚠️ **2026-09-08 訂正。** 当初この決定は「Kconfig だけで指定する」としていた。
> 根拠は「同リポジトリで `src_filter` が espidf では効かないと実測されている」ことだったが、
> **これは誤った一般化だった**（下記 Findings）。タスク 1.1 の実装中に実測で否定されたため書き直した。

- **Context**: 要件 1.3 が「無線スタックを含んだ成果物が収まる容量」を要求する。roadmap は `huge_app`（3MB / OTA 無し）を挙げていたが、根拠は「Arduino core + BTstack で 1.0〜1.4MB」という見積りだった
- **Findings（実測）**:
  - `~/.platformio/platforms/espressif32/builder/frameworks/espidf.py:2846` が
    `partitions_csv = board.get("build.partitions", "partitions_singleapp.csv")` として
    **`board_build.partitions` だけ**から CSV を決め、`CONFIG_PARTITION_TABLE_FILENAME` を参照しない
  - 直後の 2847 行は `sdk_config.get("PARTITION_TABLE_OFFSET", 0x8000)` を読んでいる。
    つまり**オフセットだけ sdkconfig から読む意図的な部分参照**であり、見落としではない
  - Kconfig だけを設定してビルドした結果、焼かれるテーブルは `factory,app,factory,0x10000,1M` であり
    **1.5MB になっていない**（`gen_esp32part.py` で復号して確認）
  - ⚠️ **`src_filter` が効かないのは事実だが、`board_build.partitions` は効く側のオプションである。**
    「espidf では INI オプションが効かない」という一般化が誤りだった
- **Selected Approach**: **両方に書き、両者が同じ CSV を指すことを境界テストで固定する**
  - `[env:teleop]` の `board_build.partitions` — **実際に焼かれるテーブルを決めるのはこちら**
  - `sdkconfig.defaults.teleop` の `CONFIG_PARTITION_TABLE_SINGLE_APP_LARGE=y` — ESP-IDF 内部の
    アプリサイズ検査が参照するのはこちら
- **Rationale**: 片方だけでは破綻する。INI だけだと IDF のサイズ検査が 1MB 前提のまま通る。
  Kconfig だけだと焼かれるテーブルが 1MB のまま。⚠️ **両者がずれると「サイズ検査は通るのに
  焼き込みか起動で失敗する」という最も切り分けの難しい形で発現する**
- **Trade-offs**: 「正が2つ」になる。だからこそ**一致を不変条件としてテストで固定する**ことが
  この決定と不可分である。片方だけ変更したときにテストが赤くなること
- **Follow-up**: ⚠️ 実際のバイナリサイズを測って記録する。**未実測の見積りを合否条件にしない**（`tech.md` 開発標準1）

### Decision: E-3 の開ループ確認を teleop 系の第3プロファイルとして分離する

- **Context**: `docs/drivetrain-spec.md` §10.2 が E-3 について「この最小ファームは PCNT も PID も使わない。そうすることで E-3 の切り分け対象を『ドライバ＋モータ』だけに保てる」と定めている。一方で上流に開ループ入口（`WheelDutyCommand` / `ControlPath::kOpenLoop`）が既に存在するため、通常の teleop ビルドでも E-3 は形式的には実施できる
- **Alternatives Considered**:
  1. 通常の teleop ビルドで `WheelDutyCommand` を使う
  2. 第3の PlatformIO 環境を足し、PCNT も核も初期化しない最小経路を持つ
- **Selected Approach**: 2。ただし**第3の排他プロファイルにはしない** — `DRIVETRAIN_BUILD_TELEOP` を立てたまま追加マクロで経路だけ切り替える
- **Rationale**: 1 では PCNT の初期化も核も binary に載り、§10.2 が求める「切り分け対象をドライバ＋モータだけに保つ」が成り立たない。E-3 で異常が出たときに疑う対象が増えることは、この手順の目的そのものを損なう。一方 `build_profile.hpp` の排他は TELEOP / PRODUCTION の2択なので、teleop 系に留めれば**排他検査を書き換えずに済む**
- **Trade-offs**: ビルド環境が3つになる。ただし production の `COMPONENTS` 許可リストには一切触れない
- **Follow-up**: E-3 で使うモータ出力アダプタは**本番と同一のものを使う**。ここを別実装にすると E-3 が確認したことが M2a へ引き継がれない

### Decision: 端子割当の正をアプリ層に置き、核へ持ち込まない

- **Context**: `structure.md` は「実機ペリフェラル実装とアプリ層の指令生成は `firmware/src/` へ置く」と定める。一方 `lib/drivetrain_control/` は3つの環境すべてで同一ソースであることが `build_profile.hpp` のコメントで明示されている
- **Selected Approach**: 端子割当を `firmware/src/` 側の単一ヘッダに置き、成立検査をホストテストで回す
- **Rationale**: 端子番号はボード固有の知識であり、核に入れると核が「ホストでも実機でも同一」でなくなる。`ports.hpp` が「PCNT ユニット番号・LEDC 分解能・ADC 生値は、この境界の向こう側に留まる」と明記しているのと同じ線である
- **Trade-offs**: ⚠️ `firmware/src/` はホストテスト（`[env:native]`）でビルドされない（`test_build_src` 既定 `no`）。成立検査をホストで回すには、**端子表を純データとして切り出し、テスト側から参照できる形にする**必要がある。ここは実装時の要注意点である
- **Follow-up**: 検査がホストで実際に走ることを、実装の早い段階で確かめる

### Decision: 回路図との照合は SVG に対する目視レビューとする

- **Context**: 要件 3.8 が端子割当と図の食い違いの検出を求める。作図は開発者が Cirkit Designer で行う
- **Findings**: Cirkit Designer の出力は **PNG / SVG と独自形式 `.ckt`** のみで、**ネットリストのエクスポート機能を持たない**。`.ckt` の内部形式は非公開
- **Selected Approach**: **SVG** をリポジトリへ置き、端子割当表との突き合わせを手順化した目視レビューとする。`.ckt` は照合の入口にしない
- **Rationale**: SVG はテキストであり差分が追える。`.ckt` は独自形式・仕様非公開であり、`chassis-mechanism` が `.FCStd` を形状の正にしなかったのと同じ理由で照合の入口に据えられない
- **Trade-offs**: 端子どうしの接続の機械照合は失われる。ただし要件 3.5〜3.7 が図に求める事項（接地の合流箇所・実装位置・極性）は**そもそもネットリストで表現できない**ため、失うのは重なり部分だけである。端子割当側は要件 2 で機械検査されるので片側の担保は残る

## Risks & Mitigations

- **BTstack の設置がスクリプト依存** — `integrate_btstack.py` の実行結果を再現手順として記録し、生成物を版管理へ含めるか否かを実装時に決める。⚠️ 決めないまま進むと「手元では通るが他の環境で再現しない」状態になる
- **BTstack のライセンス** — 商用不可条項を持つ。teleop 系ビルドにのみ封じ込め、本番成果物へ入らないことを `firmware.map` の参照ゼロで確認する（production で実証済みの検証手法をそのまま使う）
- **端子割当の検査がホストで走らない可能性** — `firmware/src/` はホストテストの対象外。端子表を純データとして切り出す設計にし、早期に実際に走ることを確認する
- **フラッシュ容量** — Arduino を積まない構成の実サイズは未実測。大きい側のパーティションを先に選び、実測後に見直す
- **エンコーダのプルアップ** — オープンコレクタ出力かどうかが未確認。入力専用端子（34〜39）を避けて内部プルアップを使える端子へ割り当て、外部プルアップが必要と判明した場合にも対応できる状態にする
- **A相／B相の未確定** — 仕様書に記載が無く実測でしか定まらない。⚠️ **確定値として書かない**。未確定であることが型で分かる形にし、M2a-0 の実測で埋める

## References

- [Bluepad32 — ESP32 + ESP-IDF (raw API)](https://bluepad32.readthedocs.io/en/latest/plat_esp32/) — raw ESP-IDF プラットフォームの推奨バージョン（v5.5+）
- [bluepad32 docs/plat_esp32.md](https://raw.githubusercontent.com/ricardoquesada/bluepad32/main/docs/plat_esp32.md) — `integrate_btstack.py` によるコンポーネント設置手順
- [esp-idf-arduino-bluepad32-template README](https://github.com/ricardoquesada/esp-idf-arduino-bluepad32-template/blob/main/README.md) — Arduino 併用テンプレートの要求バージョン（v5.4.2）
- [ESP-IDF Programming Guide — GPIO & RTC GPIO (ESP32)](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/peripherals/gpio.html) — 端子特性の一次資料
- [ESP32 Strapping Pins List](https://www.espboards.dev/blog/esp32-strapping-pins/) — ストラッピング端子（0 / 2 / 5 / 12 / 15）
- [ESP32 Pinout Reference](https://randomnerdtutorials.com/esp32-pinout-reference-gpios/) — 入力専用端子とフラッシュ用端子
- [Cirkit Designer](https://www.cirkitdesigner.com/) / [Docs](https://www.cirkitdesigner.com/docs/) — 出力形式の確認（PNG / SVG / `.ckt`、ネットリスト非対応）
