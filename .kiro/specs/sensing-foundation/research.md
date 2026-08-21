# Research & Design Decisions: sensing-foundation

## Summary

- **Feature**: `sensing-foundation`
- **Discovery Scope**: **New Feature（greenfield）＋ Complex Integration**。
  リポジトリ内の既存実装は `src/prediction_core/` のみで、本 Spec はそこへ**初めてサードパーティ依存と
  外部デバイスを持ち込む**層になる。同時に、未セットアップの実機（Pi 4 / D435）のブリングアップを含む
- **Key Findings**:
  1. **pyrealsense2 の公式 pip wheel は x86_64 Linux 向けには存在するが、aarch64 向けには存在しない。**
     Pi 4 側は librealsense のソースビルドが必須で、`pyrealsense2` は `.so` として現れる。
     したがって **pyrealsense2 を依存表に書けない**（→ OQ-41 の判断材料）
  2. **RealSense ネイティブの `.bag` 記録には 2 GB のファイルサイズ上限がある**という既知の報告があり、
     Depth 640×480/30fps（≒18 MB/s）では**2分弱で頭打ちになる**。
     さらに SD カードへの連続書き込みは Pi 4 でフレーム落ちの原因として繰り返し報告されている。
     **投擲は 1 秒級の事象**であるため、`.bag` の連続記録よりも
     **RAM のリングバッファ＋事後書き出し**の方が要求に適合する
  3. **フレーム時刻には3つのドメイン**（ハードウェア時計 / システム時刻 / グローバル時刻）があり、
     どれが有効かは**バックエンドとメタデータの可否に依存して実行時に変わる**。
     `-DFORCE_RSUSB_BACKEND=ON` でビルドすると（OQ-23 が前提とする構成）
     メタデータが揃わず `TIME_OF_ARRIVAL` へフォールバックする可能性がある。
     **時刻基準を RealSense 側に委ねる設計は成立しない** → ホスト単調時計を正とする
  4. **フレームキューの既定容量は 1** であり、容量を増やすと安定性と引き換えに**レイテンシが増える**。
     「古いフレームを溜めない」という要件（2.1）は、SDK の設定だけでなく
     **取得側が最新フレームを引き当てる形**でも担保する必要がある

---

## Research Log

### 1. Pi 4 / aarch64 における librealsense と pyrealsense2 の導入形態

- **Context**: OQ-23（OS 選定）と OQ-28（セットアップ成立性）、および OQ-41（環境構築手段）の前提確認。
  「pyrealsense2 を依存として宣言できるか」で設計の形が変わる
- **Sources Consulted**:
  - [How do i install Pyrealsense2-aarch64 into my Raspberry pi 4 (librealsense #12886)](https://github.com/IntelRealSense/librealsense/issues/12886)
  - [Pyrealsense2 for Raspberry Pi (librealsense #12604)](https://github.com/IntelRealSense/librealsense/issues/12604)
  - [realsense_raspberry_pi4 — step by step build instructions](https://github.com/mathklk/realsense_raspberry_pi4)
  - [installation_raspberry_pi_64.md (NobuoTsukamoto/realsense_examples)](https://github.com/NobuoTsukamoto/realsense_examples/blob/master/doc/installation_raspberry_pi_64.md)
  - [librealsense — conda-forge](https://anaconda.org/conda-forge/librealsense)
  - [pyrealsense2-beta — PyPI](https://pypi.org/project/pyrealsense2-beta/)
- **Findings**:
  - aarch64 向けの公式 wheel は無く、`cmake -DBUILD_PYTHON_BINDINGS=true -DPYTHON_EXECUTABLE=$(which python3)`
    によるソースビルドで `pyrealsense2*.so` を得る形になる
  - `-DFORCE_RSUSB_BACKEND=ON` を用いると**カーネルパッチが不要**になる。
    これは OQ-23 が「Ubuntu 有利の最大の論点が消える」と結論した根拠と一致する
  - ビルドが通っても `ModuleNotFoundError: pyrealsense2` になる事例が多い。
    原因は Python のバージョン取り違えと、`.so` の配置先が
    `site-packages` の探索パスに入っていないことに集中している
  - **x86_64 Linux 向けには manylinux wheel が存在する**（cp39〜cp313）。
    つまり WSL 側では `pip install` で pyrealsense2 を入れられる
- **Implications**:
  - **`pyrealsense2` を `pyproject.toml` の依存に書かない。** live 入力の内部で遅延 import し、
    存在しない環境では live 以外がすべて動作する構成にする（要件 4.4 / 12.2）
  - **環境診断ツール（doctor）が必要**。「import できるか」「どの Python を見ているか」
    「デバイスが見えるか」「USB3 か」を切り分けて報告できないと、
    ブリングアップの失敗原因がビルドなのか配置なのか給電なのか分からない（要件 1.10）
  - WSL 側で wheel が入る事実は「Replay を pyrealsense2 に依存させてよい」根拠になりうるが、
    後述の Decision 2 の理由により**採用しない**

### 2. Record / Replay の形式候補（OQ-32）

- **Context**: `development-environment.md §6` が Record / Replay を重要な開発方針としているが形式が未定。
  最有力候補は RealSense ネイティブの `.bag`
- **Sources Consulted**:
  - [Realsense Record and Playback (librealsense2 docs)](http://docs.ros.org/en/iron/p/librealsense2/user_docs/record-and-playback.html)
  - [pyrealsense2.config — enable_record_to_file / enable_device_from_file](https://intelrealsense.github.io/librealsense/python_docs/_generated/pyrealsense2.config.html)
  - [rs-record on RPI4 throws BagIOException after hitting 2 Gb file limit (librealsense #8666)](https://github.com/IntelRealSense/librealsense/issues/8666)
  - [Frame Drops When Recording Bag File (librealsense #2216)](https://github.com/IntelRealSense/librealsense/issues/2216)
  - [Inconsistent playback behavior with pyrealsense2 (librealsense #4660)](https://github.com/realsenseai/librealsense/issues/4660)
- **Findings**:
  - `.bag` の利点は明快で、`config.enable_device_from_file()` により
    **live と同じ pipeline API のまま再生できる**。`playback.set_real_time(False)` で
    1フレームずつ処理できる
  - 一方で、**2 GB のファイル上限**に当たる報告があり、Pi 4 では 30 秒〜数分で頭打ちになる
  - `.bag` 記録は**同期的な書き込み**であり、書き込みが遅いとフレーム落ちを誘発する報告が複数ある
  - 再生の挙動が pause / resume の扱いに敏感で、**同じフレームが繰り返し返る**などの
    再現性に関する不具合報告がある
  - Depth 640×480 16bit の生データ量は 1 フレーム 614,400 B。30 fps で約 18 MB/s、60 fps で約 37 MB/s
- **Implications**:
  - **投擲は 1 秒級の事象**であり、必要なのは「長時間の連続記録」ではなく
    「**投擲の前後だけを確実に残すこと**」である。この観点では `.bag` の連続記録は要求と合っていない
  - 要件 6.2（同一記録の複数回再生で同一系列）に対して、再生挙動の不具合報告があるライブラリ機能へ
    依存するのは弱い。**Replay の決定性は自分で担保できる形にする**
  - → Decision 2 へ

### 3. フレーム時刻とレイテンシ計測の基準

- **Context**: 要件 3.4（単調な単一時間基準）、要件 9.1（capture 区間のレイテンシ）の実現方法
- **Sources Consulted**:
  - [Class frame — get_timestamp / get_frame_timestamp_domain (librealsense2 docs)](https://docs.ros.org/en/ros2_packages/jazzy/api/librealsense2/generated/classrs2_1_1frame.html)
  - [Understanding various timestamps (librealsense #12779)](https://github.com/realsenseai/librealsense/issues/12779)
  - [Global time domain for RS2_FRAME_METADATA_SENSOR_TIMESTAMP (librealsense #8521)](https://github.com/realsenseai/librealsense/issues/8521)
  - [frame metadata backend_timestamp = 0 (librealsense #7972)](https://github.com/IntelRealSense/librealsense/issues/7972)
- **Findings**:
  - SDK は device / host の能力に応じて**実行時に時刻ドメインを選ぶ**。
    メタデータが構成されていない場合は `TIME_OF_ARRIVAL`（ホスト側 EPOCH 時刻）になる
  - どのドメインが有効かは `get_frame_timestamp_domain()` で問い合わせる
  - `RS2_OPTION_GLOBAL_TIME_ENABLED` はハードウェア時計をホスト時計へ写像する機能であり、
    有効時は `get_timestamp()` がホスト EPOCH ミリ秒に近い値を返す
  - Linux ではメタデータの一部（`backend_timestamp` 等）が 0 になる報告が多い。
    **`TIME_OF_ARRIVAL` は環境を問わず得られる**
- **Implications**:
  - **セッション内の正の時刻はホスト単調時計から採る**（`time.perf_counter_ns`）。
    デバイス側時刻とドメインは**記録するが、時間基準には使わない**
  - capture レイテンシは「フレーム到着時のホスト時刻 − デバイス側時刻」で算出できるが、
    **ドメインが GLOBAL_TIME でない場合は意味を持たない。**
    したがって**算出値には常にドメインを添え、意味を持たない場合は欠測として扱う**（要件 3.5）
  - この不確実性があるため、要件 9.1 の「取得に要した時間」の主指標は
    **ホスト側で閉じた区間**（フレーム待機に入ってから下流へ渡すまで）とする

### 4. 古いフレームを溜めない取得方式

- **Context**: 要件 2.1 / A-3。`development-environment.md §4` の「古いフレームを溜めない」
- **Sources Consulted**:
  - [pyrealsense2.pipeline — wait_for_frames / poll_for_frames](https://intelrealsense.github.io/librealsense/python_docs/_generated/pyrealsense2.pipeline.html)
  - [pyrealsense2.frame_queue](https://intelrealsense.github.io/librealsense/python_docs/_generated/pyrealsense2.frame_queue.html)
  - [Delay / Latency issue caused by buffering, queue size too big (librealsense #6448)](https://github.com/realsenseai/librealsense/issues/6448)
  - [RS2_OPTION_FRAMES_QUEUE_SIZE and Frame Syncer on frame management](https://support.intelrealsense.com/hc/en-us/community/posts/16475977062163-RS2-OPTION-FRAMES-QUEUE-SIZE-and-Frame-Syncer-on-frame-management)
  - [Raspberry Pi 4: pyrealsense2 frame buffer overflow (librealsense #7968)](https://github.com/IntelRealSense/librealsense/issues/7968)
- **Findings**:
  - フレームキューの既定容量は 1。容量を増やすと取りこぼしは減るが**レイテンシが増える**
  - `poll_for_frames` は「新しいフレームがあれば取り出す」非ブロッキング動作
  - Pi 4 では下流が重いと frame buffer overflow が発生する報告がある
- **Implications**:
  - キュー容量は**小さく保つ**（既定 1、設定で変更可）。
  - 加えて取得ループ側で「待機して1枚受け取り、**その直後に溜まっている分を捨てて最新に追いつく**」
    ドレイン動作を持たせ、破棄件数を数える（要件 2.1 / 2.2）
  - 欠落は SDK 側のフレーム番号の飛びで検出する（要件 2.3）

### 5. ログ形式の候補（OQ-35）

- **Context**: 柱2a の構造化ロギング。NDJSON / CSV / その他
- **Sources Consulted**: `docs/original-features.md §4.1`、`docs/development-environment.md §13.3`、
  `docs/decisions.md` D-8（NDJSON 化は `sensing-foundation` の判断として明示的に残されている）
- **Findings**:
  - 段階別レイテンシは**各 Spec が自分の区間の計測点を足す**構造になる（roadmap）。
    つまり**行ごとにフィールドの集合が異なる**
  - CSV は列を全 Spec で共有しなければならず、下流が項目を足すたびに列定義が壊れる
  - 行指向であれば、末尾が壊れても先行行は読める。fire-and-forget な追記と相性がよい
- **Implications**:
  - **NDJSON（1行1イベント）を採用する。** 同じ「1行1レコード」の規約を
    Throw Record の保存にも用いることで、読み出し側の道具を1つに揃えられる

### 6. CPU / メモリ使用率の取得手段

- **Context**: 要件 9.3。`development-environment.md §13.1` の実測項目
- **Findings**:
  - `psutil` は aarch64 wheel があり容易だが、依存が1つ増える
  - 対象環境は Pi（Linux）と WSL（Linux）に限定されており、`/proc/stat`・`/proc/self/stat`・
    `/proc/meminfo` の読み取りで必要な値がすべて得られる
- **Implications**:
  - **標準ライブラリのみで `/proc` を読む方式を採る。** Pi 上で追加ビルドを要さないことを優先する。
    Linux 以外では計測値を欠測として返す

---

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | 判断 |
|---|---|---|---|---|
| **Ports & Adapters（採用）** | `FrameSource` という1つのポートに live / recorded / simulated の3アダプタを差す | 要件 4.2 を構造で保証。SDK 非依存の経路が自然に生まれる | アダプタ層の作り込みが必要 | **採用**。`development-environment.md §7` の図そのもの |
| コールバック駆動 | SDK のフレームコールバックに下流処理を登録する | 取得レイテンシが最小 | 下流が SDK のスレッドで走る。recorded / simulated で等価な構造を作りにくい | 不採用。要件 4.2 が壊れる |
| ROS 2 ノード分割 | 取得・記録・検出をノードに分ける | 既製の記録再生資産 | `tech.md` が明示的に不採用（Pi 4 に重い） | 不採用 |
| 単一スクリプト | 取得から記録まで1ファイル | 最短で動く | 下流3 Spec が同じコードを複製する。Replay が成立しない | 不採用 |

---

## Design Decisions

### Decision 1: 記録を「観測フレーム記録」と「Throw Record」の2階層に分ける

- **Context**: OQ-32 は「Record / Replay のデータ形式」だが、D-8 により Throw Record のスキーマは確定済み。
  一方で本 Spec が実機で残したいのは**検出前の生フレーム**であり、粒度がまったく異なる
- **Alternatives Considered**:
  1. フレーム系列を Throw Record の `extra` に押し込む — 1レコードが数十 MB になり、
     `to_json` が現実的でなくなる。Throw Record は「1投擲の観測サンプルと予測」の器であって
     画像の器ではない
  2. フレーム記録を作らず、検出結果（3Dサンプル）だけを残す — 検出方式の比較（OQ-26）が
     やり直せなくなる。`flying-object-tracking` が Replay を必要とする理由そのものが失われる
- **Selected Approach**: 2階層に分ける。フレーム層は本 Spec が定義するセッション記録、
  サンプル層は `prediction_core.ThrowRecord` をそのまま使う。両者は `session_id` / `record_id` で対応付ける
- **Rationale**: D-8 が「保存先・拡張子・NDJSON化はスキーマの範囲外」と明記しており、
  この分割は D-8 の想定と一致する
- **Trade-offs**: 記録が2種類あることを利用側が理解する必要がある。
  ただし責務は「生データ」と「解析結果」で明確に分かれている
- **Follow-up**: 実装完了時に OQ-32 を decisions.md へ移す際、この2階層の関係を明記する

### Decision 2: フレーム記録に `.bag` を採用せず、自前のセッション記録形式を用いる

- **Context**: 要件 5（Record）/ 要件 6（Replay）。`.bag` は最有力候補だった
- **Alternatives Considered**:
  1. **`.bag`（librealsense ネイティブ）** — live と同じ pipeline API で再生できるのが最大の利点
  2. **自前形式**（セッションディレクトリ: メタ情報 JSON ＋ フレーム索引 NDJSON ＋ 生 Depth の連結バイナリ）
- **Selected Approach**: **自前形式を採用**する。`.bag` は「RealSense Viewer で目視確認したいとき」に
  SDK 付属ツールで別途取る手段として残すが、**本 Spec の記録経路には組み込まない**
- **Rationale**:
  - **要件 4.4 / 6.3 / 12.2 が「SDK が無い環境でも動くこと」を求めている。**
    `.bag` 再生は pyrealsense2 を必要とし、**再生経路が SDK に張り付く**
  - **2 GB 上限**の報告があり、Pi 4 での長時間記録に耐えない
  - `.bag` の記録は同期書き込みでフレーム落ちを誘発する報告がある。
    **リングバッファ＋事後書き出し**（要件 5.5）を `.bag` の枠内で実現する手段が無い
  - 再生の反復再現性に関する不具合報告があり、要件 6.2 の担保をライブラリに委ねられない
  - 自前形式なら**フレーム索引に破棄・欠落の情報を同居させられる**（要件 5.3）。
    `.bag` にはその欄が無い
- **Trade-offs**: 記録・再生のコーデックを自分で実装・テストする必要がある。
  ただし Depth は固定サイズの 16bit 生データであり、**符号化の複雑さはほぼ無い**
- **Follow-up**: 記録の読み書き往復テストと、破損時の挙動テストを実装タスクに含める

### Decision 3: 時間基準はホストの単調時計を正とする

- **Context**: 要件 3.4 / 3.5 / 9.1。RealSense 側の時刻ドメインが実行時に変わる（Research 3）
- **Alternatives Considered**:
  1. デバイス側時刻をそのまま `t_ms` にする — ドメインが変わると意味が変わる。単調性も保証されない
  2. 壁時計（EPOCH）を使う — NTP 同期で巻き戻る可能性がある
  3. ホスト単調時計を正とし、デバイス側時刻はメタ情報として併記する
- **Selected Approach**: 3 を採用。セッション開始時に単調時計と壁時計を1度だけ対応付けて記録し、
  以降のすべての `t_ms` は**セッション開始からの単調経過時間**とする
- **Rationale**: `prediction_core` が `elapsed_ms` に `time.perf_counter_ns` を採る判断と揃う。
  Replay 側でも同じ時間基準を再現できるため、要件 6.1 / 6.2 が成立する
- **Trade-offs**: デバイス側の露光時刻とホスト到着時刻の差（＝転送遅延）は
  ドメインが GLOBAL_TIME のときしか分からない。**分からないことを欠測として明示する**
- **Follow-up**: 実機で `get_frame_timestamp_domain()` が何を返すかをブリングアップ時に記録する

### Decision 4: ログ・Throw Record ともに「1行1レコードの追記」に統一する（OQ-35 / OQ-32）

- **Context**: 要件 7.4 / 8.2。形式を決める必要がある
- **Alternatives Considered**:
  1. CSV — 列固定。下流 Spec が計測項目を足すたびに壊れる
  2. 1ファイル1レコードの JSON — ファイル数が膨大になり、追記できない
  3. NDJSON（1行1 JSON オブジェクト）
- **Selected Approach**: NDJSON。構造化ログ・Throw Record・フレーム索引の3つすべてで同じ規約を用いる
- **Rationale**: 行指向は追記と相性がよく、末尾が壊れても先行行が読める（要件 7.5 / 8.6）。
  `ThrowRecord.to_json(indent=None)` の出力が**そのまま1行になる**ため、
  `prediction_core` 側に手を加えずに済む（要件 7.1 / 7.2）
- **Trade-offs**: バイナリ形式に比べて容量効率が悪い。ただしログとサンプル列は
  フレームデータに比べて桁違いに小さく、問題にならない
- **Follow-up**: 行の途中で電源が落ちた場合に備え、**書き込みは行単位でフラッシュする**

### Decision 5: リングバッファ＋トリガ保存を既定の記録方式にする

- **Context**: 要件 5.5 / 5.7。投擲は 1 秒級、SD カードの書き込み帯域は Pi 4 の弱点
- **Selected Approach**: 直近 N 秒分のフレームを RAM に保持し、トリガで前後を書き出す。
  連続記録も選べるが**既定にしない**
- **Rationale**: 「投げる → 記録される」の運用で、書き込みが取得ループの外へ出る。
  結果として要件 5.7（取得ループを妨げない）が構造的に満たされる
- **Trade-offs**: RAM を消費する（640×480 16bit なら 1 秒 30fps で約 18 MB）。
  **OQ-24（RAM 容量）の確認結果がバッファ長の上限を決める**。設定値から必要 RAM を事前に算出して警告する
- **Follow-up**: バッファ長の既定値は実機の RAM 確認後に決める。根拠なく固定値を埋め込まない

### Decision 6: サードパーティ依存を numpy のみに絞る

- **Context**: brief と roadmap は pyrealsense2 / numpy / OpenCV を想定していた
- **Selected Approach**: **宣言する依存は numpy のみ。** pyrealsense2 は宣言せず遅延 import。
  **OpenCV は本 Spec では導入しない**
- **Rationale**:
  - OpenCV が必要になるのは検出（`flying-object-tracking`）であり、本 Spec は
    Depth の生バッファを運ぶだけで足りる。**Pi 上に重いビルドを前倒しで持ち込まない**
    （`tech.md` 開発標準4 の考え方）
  - pyrealsense2 は依存表に書けない（Research 1）
- **Trade-offs**: 記録の目視確認に画像出力が使えない。
  必要になれば `flying-object-tracking` 側の道具として足せる
- **Follow-up**: この判断は OQ-41 の判断材料として報告する

### Decision 7: 物理配置は `src/sensing_foundation/` とし、OQ-40 を閉じない

- **Context**: OQ-40（全体のディレクトリ構成）は未決
- **Selected Approach**: 既存の `src/prediction_core/` と同じ並びに `src/sensing_foundation/` を置く。
  ルートの `pyproject.toml` にパッケージを追加し、**依存は追加の extras として宣言する**
- **Rationale**: 既に `src/` レイアウトの先例があり、これに合わせるのが最小の決定で済む。
  「入力層 / 処理層 / 通信層」といった全体の区画は決めない
- **Trade-offs**: 配布名が `prediction-core` のままになり実態と合わなくなる。
  **これは OQ-40 で解決すべき事項として明示的に残す**
- **Follow-up**: `pyproject.toml` の `[project].dependencies` は空のまま維持する。
  numpy は extras に入れ、`prediction_core` の依存ゼロ性を壊さない

### Decision 8: 実測結果の置き場を spec 配下の1ファイルに定める

- **Context**: 要件 1.3 / 1.8 / 10.2 / 11.5 が「記録として残す」を求めている。置き場が無いと要件が宙に浮く
- **Selected Approach**:
  - **人が読む結論** → `.kiro/specs/sensing-foundation/measurements.md`（版管理する）
  - **機械が吐く生データ** → `var/`（版管理しない。要件 12.6）
  - **決着した OQ の結論** → 実装完了時に `docs/decisions.md` へ移し、`docs/open-questions.md` から行を削除する
- **Rationale**: `structure.md` の「未決事項は open-questions.md にしか書かない」
  「決着したら decisions.md へ移す」という運用を壊さずに、進行中の実測値を置ける
- **Trade-offs**: spec ディレクトリに成果物が1ファイル増える
- **Follow-up**: `docs/` 側の更新は本 Spec の最終タスクとして行う

---

## Risks & Mitigations

| # | リスク | 影響 | 緩和 |
|---|---|---|---|
| R1 | **librealsense のビルドが Pi 4 で通らない / 通っても import できない** | ブリングアップが止まり、以降の全 Spec が止まる | OQ-23 の退避規則（Pi OS → Ubuntu）をタスクに組み込む。doctor が「ビルド」「配置」「Python 版」「デバイス」「USB3」を切り分けて報告する |
| R2 | **給電不足で D435 が断続的に切断する** | 「Pi 4 の性能不足」と誤診する | fps 計測より先に給電安定性を確認する（要件 1.2）。USB2 接続時は fps 計測結果を無効として扱う（要件 1.5） |
| R3 | **Python 側の取得ループが Pi 4 で追いつかない** | dropped frame が増え、実効サンプル数が落ちる | ドレイン方式で最新に追いつき、破棄を数える。Color を切れる設定にする（要件 11.7）。`§13.2` の改善順序に沿って詰める |
| R4 | **リングバッファが RAM を食い潰す** | OOM でセッションが落ちる | 設定値から必要 RAM を事前算出し、上限を超える設定を拒否する。OQ-24 の実測値を上限の根拠にする |
| R5 | **ログ出力が計測対象を歪める** | 計測値そのものが信用できなくなる | fire-and-forget ＋ 有界キュー＋破棄計数。ON/OFF 比較を独立したタスクとして持つ（要件 10） |
| R6 | **実機が無い期間に作った recorded / simulated 経路が、live と食い違う** | 実機到着後に作り直しになる | 3アダプタを**同一の契約テスト**にかけ、live のみ実機タスクで同じテストを再実行する |
| R7 | **フレームのメタデータが取れず capture レイテンシが算出できない** | `§13.1` の項目が1つ欠ける | ホスト側で閉じた区間を主指標に置く。デバイス側との差分は取れたときだけ副指標として記録する |
| R8 | **`prediction_core` へサードパーティ依存が逆流する** | Replay の決定性が崩れる | 依存方向を静的に検証する回帰テストを本 Spec 側にも置く（`prediction-core` の `test_boundaries.py` と同じ手法） |

---

## References

- [Realsense Record and Playback — librealsense2 documentation](http://docs.ros.org/en/iron/p/librealsense2/user_docs/record-and-playback.html) — `.bag` の記録・再生の公式挙動
- [pyrealsense2.config](https://intelrealsense.github.io/librealsense/python_docs/_generated/pyrealsense2.config.html) — `enable_record_to_file` / `enable_device_from_file`
- [pyrealsense2.pipeline](https://intelrealsense.github.io/librealsense/python_docs/_generated/pyrealsense2.pipeline.html) — `wait_for_frames` / `poll_for_frames`
- [pyrealsense2.frame_queue](https://intelrealsense.github.io/librealsense/python_docs/_generated/pyrealsense2.frame_queue.html) — キュー容量と `keep_frames`
- [Class frame — librealsense2](https://docs.ros.org/en/ros2_packages/jazzy/api/librealsense2/generated/classrs2_1_1frame.html) — `get_timestamp` / `get_frame_timestamp_domain` / `get_frame_number`
- [Understanding various timestamps (#12779)](https://github.com/realsenseai/librealsense/issues/12779) — 時刻ドメインの選ばれ方
- [rs-record 2 GB file limit on RPI4 (#8666)](https://github.com/IntelRealSense/librealsense/issues/8666) — `.bag` のサイズ上限
- [Delay / Latency caused by buffering (#6448)](https://github.com/realsenseai/librealsense/issues/6448) — キュー容量とレイテンシのトレードオフ
- [Pi 4 frame buffer overflow (#7968)](https://github.com/IntelRealSense/librealsense/issues/7968) — Pi 4 で下流が重い場合の挙動
- [How do i install Pyrealsense2-aarch64 into RPi 4 (#12886)](https://github.com/IntelRealSense/librealsense/issues/12886) — aarch64 に wheel が無いこと
- [realsense_raspberry_pi4](https://github.com/mathklk/realsense_raspberry_pi4) — Pi 4 向けビルド手順の実例
- [pyrealsense2-beta — PyPI](https://pypi.org/project/pyrealsense2-beta/) — x86_64 Linux 向け wheel の存在確認
- 内部文書: `docs/development-environment.md` §4 / §5 / §6 / §7 / §13 / §16、
  `docs/original-features.md` §2 / §4.1、`docs/open-questions.md` OQ-23〜28 / 32 / 35 / 40 / 41、
  `docs/decisions.md` D-8、`.kiro/specs/prediction-core/design.md`
