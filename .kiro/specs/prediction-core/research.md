# Research & Design Decisions: prediction-core

## Summary

- **Feature**: `prediction-core`
- **Discovery Scope**: New Feature（greenfield。リポジトリに実装コードは一行も存在しない）
- **Key Findings**:
  - 6パラメータ `(x0, vx, y0, vy, z0, vz)` の推定は、**3本の独立した2パラメータ線形回帰に完全に分解できる**。
    `g` が既知なら `z' = z + ½g·t²` と置き換えるだけで z 軸も線形になる。
    したがって反復解法も行列ライブラリも不要で、**閉形式で解ける**。
  - 上記の帰結として **NumPy を含む一切のサードパーティ依存が不要**になる。
    これは速度の問題ではなく、要件 9.4（再現性）と 7.1（ハード非依存の検証）に直接効く。
  - 正規方程式の行列式 `Δ = n·Σt̃² − (Σt̃)²` が 0 になる条件は
    「全観測時刻が同一」と厳密に一致し、**要件 6.2 の「時刻の縮退」判定にそのまま使える**。
  - 残差を `sqrt(SSE / (3(n−2)))` と定義すると、分母が 0 になる点が n = 2 と一致し、
    **要件 10.2「最小サンプル数 3 の導出根拠」が式そのものに現れる**。

---

## Research Log

### 実行環境の Python バージョン

- **Context**: 対象は Raspberry Pi 4（実機）と WSL（開発）。OS は未確定（OQ-23）であり、
  どのバージョンを最低ラインに置くかで使用可能な構文が変わる。
- **Sources Consulted**:
  - [Trixie — the new version of Raspberry Pi OS](https://www.raspberrypi.com/news/trixie-the-new-version-of-raspberry-pi-os/)
  - [The latest Raspberry Pi OS images are now based on Debian 13 Trixie](https://www.cnx-software.com/2025/10/06/raspberry-pi-os-debian-13-trixie/)
  - 本リポジトリの WSL 環境を実測（`python3 --version` → `3.14.4`）
- **Findings**:
  - Raspberry Pi OS Bookworm（Debian 12）系のシステム Python は **3.11**。
  - Raspberry Pi OS Trixie（Debian 13、2025-10 リリース）系は **3.13**。
  - 本リポジトリの WSL 環境は **3.14.4**。
- **Implications**:
  - OS 未確定（OQ-23）である以上、**最低ラインを 3.11 に置く**のが安全。
  - PEP 695 のジェネリクス構文（3.12+）や 3.13+ 専用機能を使わない。
  - `X | Y` 形式の型注釈・`dataclass(frozen=True, slots=True)` は 3.11 で使用可能。

### 数値ライブラリの要否（NumPy を採用するか）

- **Context**: 「3点以上で最小二乗」（`docs/requirements.md §5-B`）を実装するにあたり、
  NumPy（`numpy.linalg.lstsq` / `polyfit`）を採用するのが一般的な選択肢になる。
- **Sources Consulted**:
  - [numpy.linalg.lstsq — NumPy Manual](https://numpy.org/doc/stable/reference/generated/numpy.linalg.lstsq.html)
  - 本リポジトリの WSL 環境で純 Python 実装の実測を行った（下記）
- **Findings**:
  - 本問題は**パラメータについて線形**であり、軸ごとに 2 パラメータの単回帰へ分解できる。
    `lstsq` が内部で行う SVD は、この規模には過剰である。
  - `numpy.linalg.lstsq` は LAPACK/BLAS 実装に依存する。
    **BLAS のビルド差・スレッド数差により、同一入力でも最下位ビットが一致しない可能性がある。**
    WSL（開発）と Raspberry Pi（実機）では BLAS 実装が異なりうる。
  - 本リポジトリの WSL 環境には **NumPy がインストールされていない**（`ModuleNotFoundError`）。
    採用すると venv・依存導入の手順が「プロジェクト最初の Spec」に乗る。
  - 純 Python 閉形式の実測（WSL / Python 3.14.4、3軸分のフィット1回あたり）:

    | サンプル数 n | 3軸フィット1回 |
    |---|---|
    | 3 | 1.28 µs |
    | 5 | 1.84 µs |
    | 10 | 2.20 µs |
    | 20 | 3.54 µs |
    | 40 | 6.55 µs |

    > ⚠️ **これは開発PC（WSL）の実測値であり、Raspberry Pi 4 の値ではない。**
    > Pi 4 での処理時間は未実測であり、合否条件に使わない（`tech.md` 開発標準1）。
    > 実測は `m1-prediction-validation` が行う。本 Spec は計測手段の提供にとどまる（要件 8）。
- **Implications**:
  - **サードパーティ依存を持たない**（標準ライブラリのみ）方針を採用する。
  - 依存ゼロは要件 7.1（ハードウェア非接続で検証可能）と要件 1.5 を最も素直に満たす。
  - 決定性が BLAS 実装に左右されないため、要件 9.4（Replay 再現）を強く担保できる。

### 数値条件（時刻の原点と根の選択）

- **Context**: 時刻 `t` を ms で扱う。絶対時刻（epoch ms ≈ 1.7e12）をそのまま使うと `t²` が 1e24 となり、
  正規方程式で桁落ちが起きる。また床面交点は2次方程式の根であり、根の公式は桁落ちしやすい。
- **Sources Consulted**: 数値計算の標準的な扱い（正規方程式の条件数は元問題の2乗になる／
  2次方程式は `q = −½(b + sign(b)·√D)` 形式で解く）
- **Findings**:
  - 時刻をサンプル列の最小値 `t_ref` で原点シフトすると、飛行時間 0.6〜1.2 s ＝ 約 600〜1200 ms
    の範囲に収まり、`t̃²` は高々 1.4e6。float64 で十分な精度が確保できる。
  - 縮退判定に `Δ == 0` を使うのは脆い。正規方程式は条件数を2乗するため、
    相対閾値には **machine epsilon の平方根**（float64 で約 1.49e-8）を用いるのが慣行。
  - 根の公式 `(−b ± √D) / 2a` は `b² ≫ 4ac` のとき一方の根で桁落ちする。
    `q = −½(b + sign(b)·√D)` を用い `t = q/a` と `t = c/q` の2つを得る形式が安定。
- **Implications**:
  - 内部計算は `t̃ = t − t_ref`（`t_ref = min(t)`）で行い、出力時に `t_ref` を戻す（要件 3.4）。
  - 縮退判定の相対閾値を設定パラメータとして公開し、既定値の導出根拠を併記する（要件 10.5）。
  - 交点算出は安定形の根の公式を使う（要件 7.2 の「解析解と一致」を守るため）。

### Throw Record の直列化方式

- **Context**: 要件 9 は「直列化・復元」を責務とし、ファイル I/O は責務外とする。
  下流は `sensing-foundation`（記録形式 OQ-32）と TypeScript 可視化。
- **Sources Consulted**: 標準ライブラリ `dataclasses` / `json` のドキュメント、
  `docs/original-features.md §2`（Throw Record 想定内容）、`tech.md`（TS 側にアルゴリズムを置かない）
- **Findings**:
  - CPython の `json` は float を `repr()` 相当（最短往復表現）で出力するため、
    **JSON 経由でも float の往復は厳密に一致する**（要件 9.3）。
  - `json` は既定で `NaN` / `Infinity` を出力するが、これは RFC 8259 準拠の JSON ではなく、
    TypeScript 側の `JSON.parse` で読めない。
  - `pydantic` 等の採用は依存を増やし、Pi 上の import コストと venv 管理を持ち込む。
    本 Spec の検証内容（フィールドの型と有限性）は `dataclasses` と明示的な検証で足りる。
- **Implications**:
  - `dataclasses` と明示的な `to_dict` / `from_dict` を採用し、`json` は文字列と dict の変換にのみ使う。
  - JSON 直列化は `allow_nan=False` とし、非有限値を含むレコードは**例外で拒否**する。
  - スキーマ拡張（要件 9.6）は「追加は任意フィールドのみ」とし、
    未知キーを `extra` に保存して往復で失わない形にする。

---

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| **純関数コア + 薄い状態レイヤ**（採用） | 推定・交点算出・予測を純関数、逐次蓄積のみを Tracker が持つ | 決定性が保証しやすく単体テストが容易。Replay とライブが同一コードを通る | 状態を持つ側が薄いため、利用側が蓄積を自前で書く誘惑が残る | 要件 1.3 / 7 / 9.4 に直結 |
| Kalman Filter ベース | 逐次的に状態推定 | 欠測・ノイズに強い | 要件が明示的に除外（まず最小二乗をベースラインとする）。パラメータ調整が必要で M1 前に根拠が無い | 将来拡張として fitting の差し替えで対応 |
| ストラテジパターンで推定器を差し替え可能にする | Estimator 抽象を切る | 将来 Kalman へ差し替えやすい | 実装が1つしかない抽象＝投機的抽象化。design-synthesis の Simplification に反する | 差し替えは関数の差し替えで足りる |
| スライディングウィンドウ推定 | 直近 N 点のみでフィット | モデル不一致（空気抵抗）に追従できる | 空気抵抗を無視する初期方式ではモデルが全区間で正しく、点を捨てるのは純粋な精度損失 | 不採用。M1 でモデル不一致が出たら再検討（OQ-33） |

---

## Design Decisions

### Decision: サードパーティ依存を持たない（標準ライブラリのみ）

- **Context**: 最小二乗フィットに NumPy を使うのが一般的だが、本 Spec は依存ゼロで着手できる唯一の Spec である。
- **Alternatives Considered**:
  1. `numpy.linalg.lstsq` で 6 パラメータを一括推定
  2. `numpy.polyfit` を軸ごとに適用
  3. 純 Python の閉形式（軸ごとの単回帰）
- **Selected Approach**: 3 を採用。`g` 既知により z 軸も線形化できるため、3軸それぞれ 2 パラメータの単回帰に分解する。
- **Rationale**:
  - **要件 9.4（Replay 再現）**: BLAS 実装差に左右されない。演算順序が固定なら CPython の float64 演算は決定的。
  - **要件 7.1 / 1.5**: 依存が無ければ、どの環境でも `python -m pytest` だけで検証できる。
  - **`tech.md` 開発標準4**: まず設定とソフトで詰める。この規模で SVD を呼ぶ必然性が無い。
  - 実測（WSL）で n=40 でも 6.55 µs。時間予算 100〜150 ms に対して問題にならない見込み。
- **Trade-offs**: 将来モデルを非線形化（空気抵抗）する場合は自前実装では足りず、
  その時点で SciPy 等の導入を再検討する必要がある。
- **Follow-up**: Raspberry Pi 4 上の実処理時間は `m1-prediction-validation` で実測する。

### Decision: 全観測点を使う（スライディングウィンドウを導入しない）

- **Context**: requirements.md が「design フェーズで決めるもの」として明示的に挙げた項目。
- **Alternatives Considered**:
  1. 直近 N 点のウィンドウ（N は設定）
  2. 蓄積した全観測点
- **Selected Approach**: 2 を採用。ウィンドウのパラメータ自体を導入しない。
- **Rationale**:
  - 空気抵抗を無視する初期方式（要件 2.2）では、放物運動モデルは**飛行区間全体で正しい**。
    モデルが正しい以上、古い点を捨てることは統計的な情報損失にしかならない。
  - ウィンドウが有効になるのは「モデルが現実とずれている」ときだが、そのずれは未実測であり、
    根拠のないパラメータを追加しないという `tech.md` 開発標準1 に反する。
  - 全点使用は要件 4.1（3点で初回予測）と要件 5.1（追加のたびに再推定）を最も単純に満たす。
- **Trade-offs**: モデル不一致がある場合、古い点が予測を引っ張る。M1 で残差の系統的増大として観測できる。
- **Follow-up**: M1 で残差がサンプル数とともに増大する傾向が出たら、
  OQ-33（物理モデルの詳細度）とあわせて再検討する。

### Decision: 推定器は純関数、蓄積のみ状態を持つ（インクリメンタル和を採らない）

- **Context**: 逐次更新（要件 5.1）は、running sum を保持すれば O(1) で更新できる。
- **Alternatives Considered**:
  1. Tracker が `Σt, Σt², Σx, Σtx, …` を保持し O(1) 更新
  2. Tracker はサンプル列だけを保持し、毎回全点から再計算（O(n)）
- **Selected Approach**: 2 を採用。
- **Rationale**:
  - Tracker は Throw Record 生成のためどのみち**全サンプル列を保持する必要がある**（要件 9.2）。
    running sum は状態の二重管理になる。
  - 1 を採ると「バッチ計算」と「逐次計算」の 2 経路が生まれ、両者がズレたときに気付けない。
    これは `tech.md` 開発標準3 が警告する構図と同型である。
  - n ≤ 40 程度で O(n) の再計算は実測 6.55 µs（WSL）。最適化の必要が無い。
- **Trade-offs**: サンプル数が桁違いに増えると不利になるが、飛行時間 0.6〜1.2 s では起こらない。
- **Follow-up**: なし。

### Decision: 残差を `sqrt(SSE / (3(n−2)))` と定義する

- **Context**: 要件 2.4 は残差の算出、要件 6.8 は残差を返すにとどめること、
  要件 7.3 はサンプル数を変えた予測を相互に比較できることを求める。
- **Alternatives Considered**:
  1. RMS 残差 `sqrt(SSE / n)`
  2. 自由度で正規化した残差 `sqrt(SSE / (3(n−2)))`
  3. SSE そのもの
- **Selected Approach**: 2 を採用。3軸 × n 点の観測に対しパラメータは 3軸 × 2 個なので、残差自由度は `3(n−2)`。
- **Rationale**:
  - **サンプル数が異なる予測どうしを比較できる**（要件 7.3 / 7.4）。1 や 3 は n に対して系統的なバイアスを持ち、
    「3点の予測のほうが残差が小さい＝信頼できる」という誤読を生む。
  - 2 は座標1成分あたりの観測ノイズ標準偏差の不偏推定量にあたり、**mm 単位で直感的に解釈できる**。
  - 分母が 0 になるのが n = 2 のときであり、
    **要件 10.2 が求める「最小サンプル数 3 の導出根拠」が式そのものに現れる**。
- **Trade-offs**: n = 3 では自由度 3 しかなく残差の推定自体が粗い。これは統計的に不可避であり、
  利用側が閾値を決める際の前提として文書化する。
- **Follow-up**: 閾値の決定は利用側（要件 6.8）。M1 で実測分布を見てから決める。

### Decision: `residual` の名称と単位の扱い

- **Context**: 要件 3.3 は出力フィールド名を `residual` と明示している。
  一方、要件 10.4 は距離・時刻・速度を表すフィールド名に単位を含めるとし、
  その例外として `residual` を無次元量と記している。
  上記の残差定義は **mm 単位を持つ**ため、両者に緊張がある。
- **Alternatives Considered**:
  1. `residual_mm` に改名する（要件 3.3 のフィールド名指定に反する）
  2. 無次元になるよう正規化する（解釈性を失い、閾値決定が難しくなる）
  3. `residual` の名称を維持し、単位が mm であることを契約として明記する
- **Selected Approach**: 3 を採用。
- **Rationale**:
  - 要件 3.3 がフィールド名を明示的に列挙しており、そちらを正とする。
  - 要件 10.4 は `residual` を名指しで例外としているため、名称の維持は 10.4 の文言と両立する。
  - 10.4 の意図は「mm と m、ms と s の取り違えをコンパイル前に防ぐ」ことにある。
    `residual` は他の量と四則演算されず、利用側の閾値と比較されるだけなので、取り違えの経路が無い。
- **Trade-offs**: 名前だけでは単位が分からない。契約・スキーマ・docstring に mm を明記して補う。
- **Follow-up**: 下流（`m1-prediction-validation`）が閾値を持つ際、
  単位が mm であることを前提にしていることを確認する。

### Decision: 「予測無効」を例外ではなく値（判別可能な直和型）で表す

- **Context**: 要件 6 は 5 種類の無効理由を区別可能にし（6.6）、正常な予測値を返さないこと（6.7）を求める。
- **Alternatives Considered**:
  1. 例外を送出する
  2. `Prediction` の各フィールドを `None` 許容にし `is_valid` フラグを持たせる
  3. `Prediction` / `InvalidPrediction` の直和型（`PredictionOutcome`）
- **Selected Approach**: 3 を採用。
- **Rationale**:
  - 逐次更新（要件 5.1）では無効は**正常な経過**であり、例外は制御フローとして重い。
  - 2 は「`is_valid` を見ずに `predicted_hit_x_mm` を読む」経路が型として残る。
    3 なら `InvalidPrediction` に落下地点フィールドが**存在しない**ため、要件 6.7 が型で守られる。
  - 設定不正（要件 10.3）は予測ではなく構成の誤りなので、こちらは例外にする。区分が明確になる。
- **Trade-offs**: 利用側は `isinstance` もしくは構造的分岐を書く必要がある。
- **Follow-up**: なし。

### Decision: 入力を時刻昇順に整列してから累積する

- **Context**: 要件 1.3 は「同一内容のサンプル列 → 同一の予測結果」を求める。
- **Selected Approach**: `predict()` は入力を `t_ms` 昇順に安定ソートしてから総和を計算する。
- **Rationale**: 浮動小数点の総和は加算順に依存する。整列を挟むことで、
  **入力の並び順が違っても結果がビット単位で一致する**という、要件 1.3 より強い保証が得られる。
  live / recorded / simulated で到着順が変わりうることを踏まえると、この保証は実利がある。
- **Trade-offs**: n log n のソートコストが増えるが、n ≤ 40 では無視できる。
- **Follow-up**: なし。

### Decision: Tracker はサンプル追加ごとに必ず結果を系列へ追加する

- **Context**: 最小サンプル数未満のときに「何も返さない」か「無効を返す」かの選択。
- **Selected Approach**: `add_sample()` は常に `PredictionOutcome` を返し、常に予測系列へ追加する。
  最小サンプル数未満の間は `InvalidReason.INSUFFICIENT_SAMPLES` が入る。
- **Rationale**:
  - 要件 6.1 の意味論が `predict()` と Tracker で一致する（分岐が1つ減る）。
  - 予測系列がそのまま**投擲のタイムラインになる**ため、
    `original-features.md` 柱3 の評価指標「予測が許容誤差内に収束するまでの時間」の素材として直接使える。
  - 要件 4.1「最小サンプル数に達した時点で初回予測」は、
    系列上で最初の `Prediction` として一意に特定できる。
- **Trade-offs**: 系列の先頭に無効エントリが `min_samples − 1` 個並ぶ。意味のある情報なので許容する。
- **Follow-up**: なし。

### Decision: Replay の一致判定から `elapsed_ms` を除外する

- **Context**: 要件 9.4 は同一パラメータで再入力したら記録された予測結果系列と一致することを求めるが、
  `elapsed_ms`（要件 8.1）は実測の壁時計時間であり、再実行で一致しない。
- **Selected Approach**: `predictions_equivalent()` を提供し、`elapsed_ms` を除く全フィールドで比較する。
  要件 9.4 の「一致」はこの同値性で定義する。
- **Rationale**: 計測値と予測値は性質が異なる。両者を同じ等価性で扱うと要件 9.4 が原理的に満たせない。
- **Trade-offs**: 「一致」の定義が API に現れるため、利用側が定義を知る必要がある。
- **Follow-up**: なし。

### Decision: `record` を `tracker` より下層に置く

- **Context**: 初版の設計では `record` が `tracker` を import する層順（tracker=L5, record=L6）にしていたが、
  `ThrowPredictionTracker.to_record()` が `ThrowRecord` を返す以上、**L5 から L6 への逆方向 import が必要になり循環する**。
  タスク分解の段階でこの矛盾が表面化した。
- **Alternatives Considered**:
  1. `to_record()` を Tracker から外し、`record` 側に `ThrowRecord.from_tracker(tracker)` を置く（層順は維持）
  2. 層順を入れ替え、`record` を L5・`tracker` を L6 とする。`replay()` は Tracker を使わず `predict()` を前置列に適用する
- **Selected Approach**: 2 を採用。
- **Rationale**:
  - `ThrowRecord` は下流 Spec が参照する**単一定義元**（要件 9.7）である。1 を採ると
    `sensing-foundation` がスキーマを import しただけで逐次蓄積器まで引きずり込まれる。
  - `replay()` の意味は「記録順の前置列それぞれに `predict()` を適用する」ことであり、
    Tracker は本来不要だった。Tracker は蓄積の器にすぎず、計算を持たない（[[#Decision-推定器は純関数]] と同じ理由）。
  - 結果として `record` は `predictor` までにしか依存せず、スキーマ層が最も薄くなる。
- **Trade-offs**: `replay()` と `Tracker.add_sample()` が「前置列に predict を適用する」という同じ意味を
  別経路で持つ。両者の一致は要件 9.4 のテスト（`predictions_equivalent`）で継続的に担保する。
- **Follow-up**: 実装時、`record` が `tracker` を import していないことを依存ゼロの回帰テストとあわせて検査する。

### Decision: ディレクトリ構成は `src/prediction_core/` に限定して確定する

- **Context**: OQ-40（リポジトリのディレクトリ構成）は未決。
  `structure.md` は「実装着手時に改めて決める」としている。
  本 Spec がプロジェクト最初の実装であるため、ここで何らかの物理配置を決めざるを得ない。
- **Selected Approach**: 本 Spec は `src/prediction_core/**`・`tests/prediction_core/**`・
  ルートの `pyproject.toml` のみを確定させ、**入力層・通信層・観測基盤の配置は OQ-40 のまま残す**。
- **Rationale**:
  - `src/` レイアウトは、インストールせずにテストが誤ってソースを import する事故を防ぐ標準的な配置。
  - 全体構成まで決めると、実機未着手の `sensing-foundation` の都合を推測で先取りすることになる。
    これは Boundary Readiness に反する。
- **Trade-offs**: 後で全体構成を決める際に `prediction_core` の位置を動かす可能性が残る。
  依存ゼロのパッケージなので移動コストは小さい。
- **Follow-up**: `sensing-foundation` 着手時に OQ-40 を決着させる。

---

## Synthesis Outcomes

### Generalization

- 要件 4（早期予測）と要件 5（逐次更新）は**同一の一般問題の特殊ケース**である。
  「n 点の観測から予測を1つ作る」純関数 `predict()` を用意すれば、
  初回予測は「n = min_samples のときの `predict()`」に、逐次更新は「n が増えたときの `predict()`」に還元される。
  → 専用の「初回予測」経路を作らない。
- 要件 7.3 / 7.4（サンプル数と誤差の関係を評価できる）は、要件 5.2（予測系列）が満たされれば
  **追加の出力を設けずに満たせる**。系列の各要素が `sample_count` と `residual` を持てば十分。
  → 誤差評価用 API を別途作らない。
- 要件 1.3（入力元によらず同一結果）は、`predict()` の引数を
  サンプル列と設定だけに限定すれば**型として保証される**。
  → ソース種別（live / recorded / simulated）は Throw Record のメタ情報にのみ置き、予測経路には一切渡さない。

### Build vs. Adopt

| 対象 | 判断 | 理由 |
|---|---|---|
| 最小二乗フィット | **Build**（純 Python 閉形式） | 問題が線形に分解でき閉形式が数行。NumPy 採用は BLAS 実装差により要件 9.4 を弱める |
| 2次方程式の求解 | **Build**（安定形の根の公式） | 標準ライブラリに安定形の実装が無い。`math.sqrt` のみ使用 |
| データ構造・不変性 | **Adopt**（標準ライブラリ `dataclasses`、`frozen=True`） | 自作の値オブジェクトを書く理由が無い |
| 直列化 | **Adopt**（標準ライブラリ `json`）＋ Build（`to_dict` / `from_dict`） | `json` は float の最短往復表現を保証する。スキーマ検証は自前で足りる |
| 高分解能時間計測 | **Adopt**（`time.perf_counter_ns`） | 単調時計。壁時計の巻き戻しの影響を受けない |
| 決定的な擬似乱数（テスト用） | **Adopt**（標準ライブラリ `random.Random(seed)`） | 要件 7.4 の誤差重畳はテスト側の責務。パッケージ本体には入れない |
| テストランナー | **Adopt**（`pytest`、開発依存のみ） | 実行時依存を増やさない |

### Simplification

- **Estimator 抽象を作らない。** 実装が1つしかない抽象は投機的抽象化。
  将来の Kalman 差し替えは別の関数を書いて `predict()` から呼ぶで足り、今インターフェースを切る理由が無い。
- **ウィンドウサイズのパラメータを作らない**（上記 Decision）。
- **`created_at` を Throw Record に入れない。** 要件 9.2 の列挙に含まれず、
  `original-features.md §9` の「最初から完全なスキーマを設計しない」に従う。
  必要になった下流は要件 9.6 の加算的拡張規則で追加できる。
- **インクリメンタル更新の最適化を入れない**（上記 Decision）。
- **ファイル I/O 用のヘルパを一切置かない**（要件 9.5）。`to_json()` は文字列を返すのみ。

---

## Risks & Mitigations

- **Pi 4 上の処理時間が未実測** — 本 Spec では合否条件にせず、`elapsed_ms` を出力に載せて
  `m1-prediction-validation` が End-to-End で評価できるようにする（要件 8）。
- **`residual` の名称が単位を持たない**（上記 Decision）— 契約・スキーマ・docstring に mm を明記し、
  下流が閾値を持つ際の単位前提を `design.md` に残す。
- **空気抵抗の無視によるモデル不一致** — 全点使用の設計では、モデル不一致は残差の増大として観測される。
  検出そのものは M1 の役割であり、本 Spec は残差を返すところまでを持つ（要件 6.8）。
- **非有限値を含むレコードが JSON 化できない** — `to_json()` は `allow_nan=False` で例外にする。
  `to_dict()` は例外にしないため、メモリ上の忠実性は失われない。
  非有限サンプルを記録として残す必要が生じたら `sensing-foundation` 側で表現方法を決める。
- **`min_samples` を 3 より大きくすると初回予測が遅れる** — パラメータ化してあるため M1 の実測で調整可能。
  3 未満は要件 10.3 により拒否する。
- **OQ-40（全体のディレクトリ構成）が未決のまま `src/prediction_core/` を確定させる** —
  依存ゼロのパッケージであり移動コストが小さいこと、
  および本 Spec が決めるのは自分の部分木だけであることを明記する。

---

## References

- [Trixie — the new version of Raspberry Pi OS](https://www.raspberrypi.com/news/trixie-the-new-version-of-raspberry-pi-os/) — 実機 OS 候補の既定 Python バージョン
- [The latest Raspberry Pi OS images are now based on Debian 13 Trixie](https://www.cnx-software.com/2025/10/06/raspberry-pi-os-debian-13-trixie/) — 同上（リリース時期）
- [numpy.linalg.lstsq — NumPy Manual](https://numpy.org/doc/stable/reference/generated/numpy.linalg.lstsq.html) — 不採用とした一括最小二乗 API
- `docs/requirements.md §5-B` — 予測方式の正（`z(t)=z0+vz·t−½g·t²`、3点以上で最小二乗、z=0 との交点）
- `docs/requirements.md §6.1` — 座標系と単位（距離 mm / 時刻 ms）
- `docs/development-environment.md §7` — 入力層 live / recorded / simulated の分離方針
- `docs/original-features.md §2` — Throw Record を中心に置く全体像と想定内容
- `docs/original-features.md §9` — 最初から完全なスキーマを設計しない
- `docs/open-questions.md` — OQ-31（Throw Record 最小スキーマ）／ OQ-32 ／ OQ-33 ／ OQ-40
