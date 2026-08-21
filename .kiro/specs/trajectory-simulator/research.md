# Research & Design Decisions: trajectory-simulator

## Summary

- **Feature**: `trajectory-simulator`
- **Discovery Scope**: **Extension**（既存の `prediction-core` へ接続する新規パッケージ。既存コードの改変は `pyproject.toml` の1行に限る）
- **Key Findings**:
  1. **上流の依存ゼロ制約は「方針」ではなく既存テストで固定されている。** `tests/prediction_core/test_packaging.py::test_no_third_party_runtime_dependencies` が `pyproject.toml` の `[project] dependencies == []` と `optional-dependencies == {}` を検査している。**NumPy を実行時依存として追加すると上流 Spec のテストが落ちる。**
  2. **`prediction_core` の公開 API は 18 シンボルで、`SourceKind.SIMULATED` が既に存在する。** 合成入力のための新しい型・スキーマを本 Spec が作る必要はない。`ThrowPredictionTracker` が逐次予測系列と `ThrowRecord` の生成まで面倒を見るため、本 Spec の接続コードは薄い。
  3. **ホイール径が未確定になった（2026-08-21 の入力）。** `drivetrain-spec.md §1` は Nexus 14145（60mm）を確定としているが、供給遅延により Nexus 14148（48mm）へ代替される可能性がある。**最高速度 1.66 m/s とホイール径 60mm をモデルへ埋め込めない**という設計制約が、ここから直接導かれる。

---

## Research Log

### 上流 `prediction-core` の実装済み契約

- **Context**: 本 Spec は予測を再実装しない。どこまでが既に提供済みかを確定させないと、接続層の責務が決まらない。
- **Sources Consulted**: `src/prediction_core/__init__.py` / `types.py` / `config.py` / `tracker.py` / `record.py`、`.kiro/specs/prediction-core/design.md`、`docs/decisions.md` D-8
- **Findings**:
  - 公開 API は `__init__.__all__` の 18 シンボル。`predict()` は純関数、`ThrowPredictionTracker` が唯一の状態保持者
  - `ThrowPredictionTracker.add_sample()` は**常に** `PredictionOutcome` を返し、`predictions` は `samples` と同数になる。最小サンプル数未満の間は `InvalidPrediction(INSUFFICIENT_SAMPLES)` が積まれる
  - `Prediction` は `based_on_time_ms`（その予測の基準となる最新観測時刻）と `sample_count` を持つ。**「いつの観測に基づく予測か」が結果自体から取れる**ため、シミュレータ側で対応表を持つ必要がない
  - `ThrowRecord` は `record_id` / `source` / `config` / `samples` / `predictions` / `schema_version` / `extra` の7フィールド。`extra` が下流の加算的拡張の退避先として用意されている
  - `PredictionConfig` は `frozen`。`min_samples` は 3 未満を構築時に拒否する
- **Implications**:
  - 接続層（`prediction_link`）は「サンプルを順に流し、`ThrowRecord` を受け取る」だけの薄い層でよい。予測系列の再構築ロジックを持たない
  - シミュレータ固有のメタ情報（掃引の格子点番号・試行番号）は `ThrowRecord.extra` に載せられる。**スキーマの再定義は不要**
  - 予測の**利用可能時刻**は `based_on_time_ms` に遅延を足して求める。これが移動体の目標更新時刻になる

### 実行時依存を追加できるか（NumPy 判断）

- **Context**: 掃引は数値計算のバッチであり、NumPy が第一候補に見える。
- **Sources Consulted**: `pyproject.toml`、`tests/prediction_core/test_packaging.py`、`tests/prediction_core/test_boundaries.py`、`docs/open-questions.md` OQ-41
- **Findings**:
  - リポジトリは**単一の `pyproject.toml`**（`name = "prediction-core"`）で、`[tool.hatch.build.targets.wheel] packages` に `src/prediction_core` のみが列挙されている
  - `test_packaging.py` が `dependencies == []` と `optional-dependencies == {}` を**アサートしている**。実行時依存を1つでも足すと上流のテストが赤くなる
  - `test_boundaries.py` は `src/prediction_core/*.py` のみを走査対象にしており、`src/trajectory_sim/` は検査対象外。**本 Spec 側の境界は本 Spec 側で用意する必要がある**
  - OQ-41（Python の環境構築・パッケージ管理）は未決。`uv.lock` は存在するが、`open-questions.md` は「実機 Pi 上のビルド事情を見てから決める」としている
- **Implications**:
  - **標準ライブラリのみで実装する。** これは性能上の判断ではなく、上流の回帰テストと決定性を守るための判断である
  - 依存を足す必要が生じた場合は、パッケージ分割か上流テストの見直しを伴う**設計変更**として扱う（Revalidation Trigger に記載）

### 掃引規模と純 Python の処理時間

- **Context**: NumPy を使わない判断が、実用的な掃引規模で成立するかを確認する必要がある。
- **Findings**:
  - `predict()` は閉形式の最小二乗であり、サンプル数 n に対して O(n)。行列分解を行わない
  - 想定規模: 格子点 10×8 = 80、1格子点あたり試行 100 回 → 8,000 シナリオ。1シナリオあたりのサンプル数は 0.6〜1.2s ÷ 標本化周期（30fps なら 18〜36 点）
  - `ThrowPredictionTracker` はサンプル追加ごとに全点で再計算するため、1シナリオの `predict()` 呼び出しは n 回、総演算量は O(n²) ≈ 36² ≈ 1,300 点分
  - 総計 8,000 × 1,300 ≈ 1,000万点分の四則演算オーダー。純 Python でも**数十秒の範囲**に収まる
- **Implications**:
  - バッチ用途（対話的でない）であり、この所要時間は許容できる
  - **並列化を初期実装に含めない。** 決定性の担保（要件 8.2 / 8.3）を優先し、単純な逐次実行とする。乱数の種を格子点と試行番号から導出しておけば、必要になった時点で並列化しても結果は変わらない

### 移動体の運動モデルをどこまで作るか（OQ-33 の駆動系側）

- **Context**: brief.md の Out of Boundary は「運動モデルは**性能上限の近似**でよい」としている。どこで線を引くか。
- **Sources Consulted**: `docs/drivetrain-spec.md §1 / §3.1 / §10 / §12`、`docs/requirements.md` NFR-1 / NFR-2 / NFR-6、`docs/open-questions.md` OQ-04 / OQ-22
- **Findings**:
  - `drivetrain-spec.md §3.1` の 1.66 m/s は**無負荷理論値**であり、実車速度を保証しないと明記されている。加速度 2〜3 m/s² も撤回済みの参考値（`decisions.md` D-1）
  - 円形のゴミ箱は**向きの維持が不要**であり、3輪オムニを採用した理由そのものが「回転を考えなくてよい」ことにある
  - NFR-6 の (a) 停止して待つ / (b) 通過キャッチ は未決（OQ-04）で、「**減速距離は加速距離とほぼ同等に必要**」＝停止方式なら実質必要性能が約2倍、という注意が付いている
  - M2b の計測項目には「前後左右および斜め方向の走行性能」があり、**方向依存の性能差は実測してから**扱う対象である
- **Implications**:
  - **並進のみの質点モデル**とする。回転・逆運動学・輪ごとの速度配分を持たない
  - **等方**とする（方向依存の性能差を係数として持たない）。差が実在することは M2b で実測してから反映する
  - 減速方針は**両方を評価できる形**にし、どちらかに決めない（OQ-04 を先取りしない）
  - 到達可否は**時間最適（bang-bang）な運動**として解く。PID の応答遅れは持たない。これは「性能上限の近似」の定義そのものである

### 「誤った安心」を構造で防ぐ方法

- **Context**: `original-features.md` は本 Spec の最大の落とし穴として「楽観的なパラメータを入れれば余裕で間に合うという誤った安心が出力される」ことを挙げ、運用ルールでの対処を提案している。
- **Findings**:
  - 運用ルール（＝人間が覚えておく約束）は、**数ヶ月後に出力ファイルだけが残ったときに機能しない**
  - `structure.md` のドキュメント規則3「数値だけが残って根拠が消える状態が最悪」と同じ失敗形である
- **Implications**:
  - 較正段階・パラメータの出所（実測 / 想定）・**モデルに含めなかった要因の一覧**を、出力 JSON の**必須項目**にする
  - 「合否の断定を出力しない」ことを要件（9.5）として明示し、成立割合は前提付きの算出値として提示する

---

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| **段パイプライン + 掃引ドライバ**（採用） | 物理 → 観測 → 予測 → 運動 → 判定 を副作用の無い段として並べ、掃引が段を繰り返し呼ぶ | 段ごとに単体テストできる。ノイズを切れば解析解と一致する検証が書ける。決定性を保ちやすい | 段の境界を跨ぐ状態（時刻の基準）を明示的に運ぶ必要がある | `prediction-core` の「純関数コア + 薄い状態レイヤ」と同じ思想 |
| イベント駆動シミュレーション（離散事象） | サンプル到着・予測完了・指令反映をイベントキューで処理 | 遅延の表現が自然 | キューの実装と検証が本質でない複雑さを生む。決定性の担保が難しくなる | 遅延は「時刻の加算」で足りるため過剰 |
| 常駐サーバ + API | 掃引をサーバ側で実行し、ブラウザから叩く | 対話的に試せる | **用途が存在しない**（`original-features.md`「Hono を今は使わない」）。ブラウザ側にロジックが漏れる誘因になる | 明示的に不採用 |
| 予測をシミュレータ内に再実装 | 掃引の都合に合わせて軽量な予測を自前で持つ | 高速化できる | **検証しているのが本番実装でなくなる**（`tech.md` 開発標準3 が名指しで警告） | 明示的に不採用 |

---

## Design Decisions

### Decision: 実行時のサードパーティ依存を持たない（NumPy を採用しない）

- **Context**: 掃引はバッチ数値計算であり、NumPy がまず候補になる。
- **Alternatives Considered**:
  1. NumPy を実行時依存として追加する
  2. NumPy を optional-dependency にして、無ければ純 Python にフォールバックする
  3. 標準ライブラリのみで実装する
- **Selected Approach**: 3。`math` と組み込み型のみで実装する。
- **Rationale**:
  - 1 と 2 は**上流 `prediction-core` の `test_packaging.py` を落とす**（`dependencies == []` / `optional-dependencies == {}` を検査済み）
  - 2 は「NumPy の有無で数値が変わるか」という検証負債を新たに作る。決定性（要件 8.2）が実行環境依存になる
  - 掃引規模の見積もり（上記）から、純 Python で数十秒に収まる
- **Trade-offs**: 大規模掃引では遅い。ただし対話用途ではないため許容する。将来必要になった場合はパッケージ分割を伴う設計変更として扱う。
- **Follow-up**: 掃引の所要時間が実用を損ねた場合、**まず掃引の粒度を見直す**（`tech.md` 開発標準4「部品を替える前に設定とソフトで詰める」と同じ順序）。

### Decision: 掃引を2種類の評価器で構成する

- **Context**: brief.md の本命の図は「持ち時間 × 必要横移動量」を軸にしている。一方で投擲条件・観測ノイズを振った感度分析も必要である。前者の軸は後者の**導出量**であり、同一の掃引軸には乗らない。
- **Alternatives Considered**:
  1. 全経路の掃引だけを持ち、持ち時間の図は結果から後処理で再構成する
  2. 到達可否だけの掃引と、全経路の掃引の2種類を持つ
- **Selected Approach**: 2。**移動体運動モデルを両者の共通部品**とし、評価器を2つ置く。
- **Rationale**:
  - 1 では「持ち時間 0.5s ちょうど」の格子点を作れず、図の軸が投擲条件のばらつきに汚染される。**成立境界を読むための図としては使えない**
  - 到達可否のみの掃引は予測も観測も通さないため**決定論的**であり、境界が線としてはっきり出る。brief.md の図の性質に一致する
  - 共通部品が運動モデル1つに閉じるため、2つの評価器が食い違う余地がない
- **Trade-offs**: 評価器が2つになる。ただし両者は同じ結果型を返すため、出力形式と掃引ドライバは共通化できる。
- **Follow-up**: 全経路掃引の結果に**実現された持ち時間**を必ず含め、到達可否掃引の図と突き合わせられるようにする。

### Decision: 機体性能はホイール径から導出できるが、既定値を持たない

- **Context**: 2026-08-21 時点で Nexus 14145（60mm）が 14148（48mm）へ代替される可能性があり、未決である。
- **Selected Approach**: 最高速度・加速度上限・減速度上限を**必須の入力パラメータ**とし、ホイール径とモータ回転数から最高速度を導出する補助手段を別に用意する。導出に使った値は出力へそのまま残す。
- **Rationale**: `tech.md` 開発標準1「根拠のない固定値をコードに埋め込まない」。1.66 m/s は無負荷理論値であり、実車速度でもなければ確定した構成でもない。
- **Trade-offs**: 実行のたびにパラメータ一式が必要になる。これは設定ファイルの同梱（60mm 版 / 48mm 版）で補う。
- **Follow-up**: M2b の実測後、設定ファイルの値を実測値へ差し替え、較正段階を `m2_calibrated` へ上げる。

### Decision: 較正段階と「モデルに含めなかった要因」を出力の必須項目にする

- **Context**: `original-features.md` の運用ルール（M1/M2 前は絶対値を信用しない）を、人間の記憶に依存させない。
- **Selected Approach**: 出力 JSON に較正段階・パラメータ出所・除外要因一覧を必須で持たせ、未較正時は注意書き文字列を必ず含める。
- **Rationale**: 出力ファイル単体が数ヶ月後に読まれても、位置付けを取り違えない。`structure.md` 規則3 と同じ思想。
- **Trade-offs**: 出力がやや冗長になる。読み手（`simulator-visualization`）はこれを表示する義務を負わないが、**捨てるには意識的な操作が要る**状態になる。

### Decision: 乱数の種を格子点と試行番号から導出する

- **Context**: 決定性（要件 8.2 / 8.3）と、将来の並列化の余地を両立させたい。
- **Selected Approach**: 基準種と「格子点の線形インデックス・試行番号」から各試行の種を決定的に導出し、試行ごとに独立した乱数器を作る。正規乱数は状態キャッシュを持たない生成手段を用いる。
- **Rationale**: 単一の乱数器を掃引全体で共有すると、**評価順序を変えた瞬間に結果が変わる**。並列化も不可能になる。
- **Trade-offs**: 乱数器の生成コストが試行回数分かかる。無視できる。

### Decision: 「評価対象外」を「不成立」と区別する

- **Context**: 床面と交わらない投擲条件や、有効な予測が1つも出ないシナリオを 0 点として集計すると、成立割合が意味を失う。
- **Selected Approach**: 結果を `成立` / `不成立` / `評価対象外（理由付き）` の3値とし、成立割合の分母から評価対象外を除く。分母と除外件数を出力に残す。
- **Rationale**: `prediction-core` が「予測無効」と「品質が低い」を分けたのと同じ構造。集計値が黙って劣化する経路を塞ぐ。

---

## Risks & Mitigations

- **楽観的なパラメータによる誤った安心** — 較正段階・出所・除外要因を出力の必須項目にする（要件 9）。合否の断定を出力しない
- **上流の依存ゼロ設計を壊す** — 標準ライブラリのみで実装し、境界テストで `pyproject.toml` の依存が空のままであることを固定する（要件 11.3 / 11.5）
- **予測の二重実装がじわじわ発生する** — 静的検査で `prediction_core` の内部モジュール直接参照とサードパーティ import を禁止する（要件 3.8 / 11.5）
- **モデル省略が忘れられ、精密なものとして扱われる** — 除外要因を出力へ載せる（要件 9.7）。OQ-33 の決着内容としても記録する
- **持ち時間の定義が曖昧になる** — 全経路掃引の結果に「最初の有効予測が指令へ反映された時刻」と「実現された持ち時間」を必ず含め、到達可否掃引の軸と定義を一致させる
- **OQ-01 を確定させてしまう誘惑** — 出力は候補比較にとどめ、確定を要件から明示的に外す（要件 10.5）

---

## References

- `docs/original-features.md` §3 柱1 — 軌道シミュレータの方針、キャッチ可能領域、実装スタック、Hono 不採用
- `docs/requirements.md` §3 / NFR-1 / NFR-2 / NFR-5 / NFR-6 / NFR-7 / §8 — 時間予算とマイルストーン
- `docs/drivetrain-spec.md` §1 / §3.1 / §10 / §12 — 駆動系構成、理論周速、制御メモ、改善順序
- `docs/open-questions.md` OQ-01 / OQ-02 / OQ-04 / OQ-33 / OQ-40 / OQ-41
- `docs/decisions.md` D-1 / D-8 — 最高速度の合否条件からの取り下げ、Throw Record 最小スキーマ
- `.kiro/specs/prediction-core/design.md` — 公開 API・依存方向・境界テストの前例
