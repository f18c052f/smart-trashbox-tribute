# Research & Design Decisions: simulator-visualization

## Summary

- **Feature**: `simulator-visualization`
- **Discovery Scope**: New Feature（本リポジトリ初の TypeScript 面。既存 TS コードはゼロ）
- **Key Findings**:
  1. **入力の形は既に確定している。** 上流 `trajectory-simulator/design.md` の `ResultSerializer` が
     最上位 8 キーを定義しており、埋め込まれる Throw Record は `src/prediction_core/record.py`
     に**実装済み**である。したがって入力契約は推測ではなく実物から確定できた
  2. **上流の出力には移動体の走行軌跡が含まれない。** brief.md の「移動体の追従が直感的に分かる」を
     素直に実装しようとすると**ブラウザ側で運動モデルを再現することになり、本 Spec の最大の禁止事項に抵触する**。
     この矛盾は「描かない」で解決し、上流への要望として記録する
  3. **SVG を選ぶと、当たり判定・テキスト配置・凡例・保存が「書かなくて済む」**。
     格子点は数十〜数百、アニメーション対象は数十点であり、Canvas / WebGL の利点が働く規模ではない
  4. **依存ゼロを TypeScript 側でも成立させられる。** TypeScript コンパイラのみを開発時依存とし、
     テストは Node.js 組み込みの `node:test`、静的配信は `python -m http.server` で足りる。
     結果として `package.json` の `dependencies` を `{}` に保てる（上流 Python 側の
     `dependencies = []` と同じ形の検査が可能になる）

---

## Research Log

### 上流の出力 JSON の実際の形

- **Context**: 本 Spec の唯一の入力であり、形を誤ると全体が成立しない
- **Sources Consulted**:
  - `.kiro/specs/trajectory-simulator/design.md`（`ResultSerializer` の最上位構造表、`Results`、
    `SweepEngine`、`Params`、`Data Contracts & Integration`）
  - `src/prediction_core/record.py`（実装済み。`to_dict` の実際のキー）
- **Findings**:
  - 最上位キーは `output_schema_version` / `calibration` / `model_exclusions` / `sweep` /
    `parameters` / `parameter_provenance` / `cells` / `throw_records` の 8 つ
  - `cells[]` は `axis_values` / `status` / `success_ratio` / `metrics` / `not_evaluated_reason`
  - `status` は `catchable` / `not_catchable` / `not_evaluated` の 3 値
  - `not_evaluated_reason` は `no_floor_crossing` / `no_samples` / `no_valid_prediction` の 3 値
  - `MODEL_EXCLUSIONS` は 4 段（`throw_physics` / `observation` / `drivetrain` / `catch`）の
    文字列配列で、合計 12 要因
  - `calibration.stage` は `uncalibrated` / `m1_calibrated` / `m2_calibrated`
  - `parameter_provenance` は パス文字列 → `measured` / `assumed`
  - Throw Record の `predictions[]` は `kind` キーで判別する直和。
    値は **`"prediction"` / `"invalid"`**（`record.py` の `_PREDICTION_KIND` / `_INVALID_KIND` で確認）
  - `samples[]` は `t_ms` / `x_mm` / `y_mm` / `z_mm`
- **Implications**: 表示要素と入力キーの対応表を design.md に固定できる。
  形式の推測が必要だったのは `throw_records` が配列かオブジェクトかの 1 点のみで、
  これは**配列と仮定し、異なっていた場合はアニメーション面のみ縮退する**設計とした

### 上流が出力に含めないもの

- **Context**: 「JSON に無い値を描かない」を守るには、何が無いかを先に確定する必要がある
- **Sources Consulted**: 上流 design.md の `ResultSerializer` 最上位構造表、`ScenarioOutcome`
- **Findings**:
  - **移動体の走行軌跡（時系列の位置）は出力されない。** 出力にあるのは
    `metrics` の平均値（位置誤差・持ち時間・必要移動量・予測誤差・残留速度）だけである
  - **真の軌道の連続曲線も出力されない。** `ScenarioOutcome` は真の落下点・落下時刻を持つが、
    `cells` の直列化キー一覧に `representative` は含まれていない
  - **合否を断定するキーは意図的に存在しない**（上流要件 9.5 とその許可リスト検査）
- **Implications**:
  - 軌跡アニメーションで描けるのは **観測サンプル列と予測系列**である（両方とも Throw Record にある）
  - 移動体の追従アニメーションは**上流の出力形式を変えない限り実現できない**。
    ブラウザ側で運動モデルを解くのは A-1 違反であるため、**先送り事項として記録する**

### OQ-34: Canvas / SVG / WebGL

- **Context**: 本 Spec が決着させるべき唯一の未決事項
- **Sources Consulted**: `docs/open-questions.md` OQ-34、`docs/original-features.md` 柱1、
  `.kiro/steering/tech.md` Core Technologies 表、brief.md「2D で足りる可能性が高い」
- **Findings**:
  - 描画対象の規模: 格子点は上流の性能目安で 80 点（掃引設計次第で数百）。
    アニメーションは 1 投擲あたりのサンプル数が数十点（飛行 0.6〜1.2 s × 30〜60 fps 相当の一部）
  - 要件 2.6（格子点を指し示すと詳細を提示）は、SVG なら `<title>` 子要素だけで満たせる。
    Canvas では座標→格子点の逆引きを自前で書く必要があり、**表示側に算術が増える**
  - 図はレイアウト議論の材料として `docs/` へ持ち込まれる可能性が高い。
    SVG はブラウザからそのまま保存でき、ベクタのまま扱える
  - `tech.md` の「Canvas / WebGL」表記は OQ-34 未決時点の候補列挙であり、決定ではない
- **Implications**: **SVG を採用する。** 詳細は下の Decision を参照

### TypeScript 側の依存と実行手段

- **Context**: 本リポジトリの Python 側は実行時依存ゼロで設計され、`tests/prediction_core/test_packaging.py`
  が `dependencies == []` を固定している。TypeScript 側に同じ規律を持ち込めるかを確認した
- **Sources Consulted**: `pyproject.toml`、`.gitattributes`、`.gitignore`、
  ローカルの `node --version`（v24.18.0）
- **Findings**:
  - Node.js は開発PC に導入済み（v24 系）。`node:test` / `node:assert/strict` は組み込みであり、
    テストフレームワークを追加せずに単体テストが書ける
  - 静的配信は `python -m http.server` で足りる。リポジトリは既に Python 環境（uv / `.venv`）を持つ
  - 境界検査には AST が要るが、**TypeScript コンパイラ自身が `ts.createSourceFile` を公開**しており、
    追加のパーサを入れずに済む。上流 Python 側が `ast` で行っている検査と同じ形になる
  - `.gitattributes` は `*.md` / `*.json` を CRLF、コード類を LF に固定している。
    `*.ts` / `*.html` / `*.css` の指定が無く、`package.json` は `*.json` 規則で CRLF になる。
    npm はインストール時に `package.json` を LF で書き戻すため、**指定を足さないと差分が揺れる**
- **Implications**: 開発時依存は `typescript` 1 個。実行時依存はゼロ。
  `.gitattributes` に 1 ブロック追加する（Modified Files に記載）

### 「誤った安心」を構造で防ぐ手段

- **Context**: `docs/original-features.md` 柱1 の「最大の落とし穴」と、
  それに対する上流の設計（較正段階・出所・除外要因を必須項目にする）を、表示側でどう受けるか
- **Findings**:
  - 上流は**データ側に運用ルールを埋め込む**という解き方をしている
  - 表示側が同じ考え方を採るなら、「注意書きを画面のどこかに置く」ではなく
    **「前提が無いデータは図にしない」**が対応する強度になる
- **Implications**: `calibration` / `model_exclusions` / `parameter_provenance` の
  いずれかが欠けた入力は**図を描かずに拒否する**（fail closed）。要件 1.2 / 3.1〜3.4 の実装方針とした

---

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|---|---|---|---|---|
| **純粋プラン + 薄い描画層（採用）** | 表示に必要な図形と文字列を純粋関数で「プラン」として組み立て、DOM を触る層はプランを SVG へ写すだけにする | DOM 無しで表示ロジックを単体テストできる。DOM を触るモジュールを 3 つに限定でき、境界検査が単純になる | ファイル数が 1 段増える | 上流 `prediction_core` の「純関数コア + 薄い状態レイヤ」と同じ考え方 |
| 素朴な描画関数群 | 各描画関数が直接 DOM を組み立てる | ファイルが少ない | jsdom 等の DOM 実装を入れないと何も検証できない ＝ **依存ゼロを諦めることになる** | 却下 |
| コンポーネントフレームワーク採用 | React / Svelte 等で状態と DOM を管理する | 記述量が減る | 実行時依存が増え、ビルド基盤も増える。A-4（最小構成）と `tech.md`「使いたい技術に用途を作らない」に反する | 却下 |
| Canvas への即時描画 | 画面全体を毎フレーム再描画する | アニメーションの実装が素直 | 当たり判定・文字配置を自前で書くことになり、表示側の算術が増える。ベクタとして保存できない | 却下（OQ-34 の決着） |

---

## Design Decisions

### Decision: OQ-34 — SVG を採用する

- **Context**: ブラウザ可視化の実装手段を Canvas / SVG / WebGL から決める
- **Alternatives Considered**:
  1. **Canvas 2D** — 即時モード描画
  2. **SVG** — 保持モード（DOM）描画
  3. **WebGL** — GPU 描画
- **Selected Approach**: **SVG。** 格子点・軌跡・凡例をすべて SVG 要素として組み立てる。
  格子点の詳細提示は `<title>` 子要素によるネイティブのツールチップで行う
- **Rationale**:
  - 対象規模（格子点 数十〜数百、軌跡 数十点）は 3 手段のいずれでも成立する。したがって
    **描画性能ではなく「表示側に書かずに済むコードの量」で選ぶのが妥当**である
  - SVG は当たり判定・テキスト配置・スケーラビリティをブラウザ側が持っている。
    これは A-1（ブラウザ側にロジックを増やさない）と方向が一致する
  - 図をベクタのまま保存でき、レイアウト議論（OQ-01）の材料として扱いやすい
  - WebGL は規模が 3 桁足りない。用途に対して手段が過剰であり、
    `tech.md`「使いたい技術に合わせて用途を作らない」に反する
- **Trade-offs**:
  - 格子点が数千を超えると DOM 要素数が問題になりうる。
    その場合は描画層（`view/render.ts`）だけを差し替える。**今は差し替えのための抽象を作らない**
  - `<title>` によるツールチップは表示までに遅延があり、書式を制御できない。
    議論用の図としては許容できると判断した
- **Follow-up**:
  - `tech.md` Core Technologies 表の「Canvas / WebGL」表記と `docs/open-questions.md` の
    OQ-34 行は、**実装完了後に `docs/decisions.md` へ移す別作業**で更新する
    （`prediction-core` の OQ-31 と同じ運用）

### Decision: 入力はファイル選択のみ。`fetch` を一切使わない

- **Context**: 掃引結果 JSON をブラウザへ渡す手段
- **Alternatives Considered**:
  1. `fetch` で決まったパスの JSON を読む（リポジトリ内へ結果ファイルを置く運用）
  2. `<input type="file">` で利用者が選ぶ
- **Selected Approach**: **2。ファイル選択のみ。**
- **Rationale**:
  - 上流の出力先は任意のパスである。リポジトリ内へコピーする運用を強いない方が素直である
  - `fetch` を使わないと決めれば、**通信 API の識別子をソースから全面禁止できる**。
    これは「常駐サーバを持たない」「ライブ表示をしない」（A-5、要件 7.3、8.2）を
    散文ではなく**検査で担保する**手段になる
- **Trade-offs**: 再読み込みのたびにファイルを選び直す必要がある。
  結果ファイルを差し替えながら見る用途では手数が増えるが、許容する
- **Follow-up**: ドラッグ&ドロップは**先送り**（同じ機構の別 UI であり、価値が増えない）

### Decision: 移動体の追従アニメーションを描かない

- **Context**: brief.md の Desired Outcome は「投擲・予測・移動体の追従が直感的に分かる」ことを挙げている
- **Alternatives Considered**:
  1. ブラウザ側で運動モデル（加速度上限・最高速度・減速）を解いて位置を描く
  2. 上流に走行軌跡の出力を追加してもらう
  3. 描かない
- **Selected Approach**: **3。描かない。** 代わりに、Throw Record にある
  観測サンプル列と予測系列を再生し、`metrics` の必要移動量・持ち時間を数値として併記する
- **Rationale**:
  - 1 は**本 Spec の最大の禁止事項そのもの**である。「シミュレータでは合っていたのに実機で外れる」
    を生む二重実装であり、選択肢として成立しない
  - 2 は上流の `output_schema_version` を変える変更であり、上流 design の Revalidation Trigger に該当する。
    本 Spec は**先送り可**と位置付けられており、上流に手戻りを起こしてまで急ぐ理由がない
- **Trade-offs**: brief.md の記述の一部を満たさない。**Non-Goals と先送り事項として明記する**
- **Follow-up**: 上流へ「代表シナリオの走行軌跡（時刻・位置の列）を出力に追加する」ことを
  要望として記録する。追加されれば本 Spec は描画層だけで対応できる

### Decision: 境界を検査で担保する（`boundaries.test.ts`）

- **Context**: 「ブラウザ側にアルゴリズムを置かない」を散文の約束にしない
- **Selected Approach**: TypeScript コンパイラ API でソースを AST として走査し、
  10 個の規則（design.md「境界検査」を正とする）を検証するテストを置く。
  さらに**違反を含む架空のソース文字列に対して検査が実際に落ちること**を別テストで示す
- **Rationale**: 上流 2 Spec が `test_boundaries.py` / `test_packaging.py` で同じことをしている。
  同じ形にすることで、レビュー時に「どこを見れば境界が守られているか分かるか」が揃う
- **Trade-offs**: 検査の許可リストを広げれば回避できる。
  ただし**回避が差分として現れる**ことが検査の価値であり、これは要件 7.6 として明文化した
- **Follow-up**: 禁止識別子リストは前方一致・大文字小文字無視で運用する。
  誤検出が出た場合は名前を変える側で対応し、安易にリストを削らない

---

## Risks & Mitigations

- **上流が未実装であるため、出力 JSON の実物がまだ無い** — 入力契約は上流 design と
  実装済みの `prediction_core.ThrowRecord.to_dict` から確定させ、
  テストは**自前の最小フィクスチャ**で行う。上流完成後に実ファイルで突き合わせる手順をタスクに含める
- **`throw_records` が配列でない可能性** — 配列でなければアニメーション面のみ縮退し、
  図の描画は続行する。取り違えを生む推測での吸収はしない
- **可視化に時間をかけすぎる**（`original-features.md` が名指しする失敗） — Non-Goals と
  先送り事項を design.md に列挙し、**タスク数の少なさを設計目標として明示**した
- **改行コードの揺れ** — `.gitattributes` に `*.ts` / `*.html` / `*.css` / `viz/package.json` の
  指定を追加する。追加漏れは `git diff` が毎回全行差分になることで即座に現れる
- **Node.js のバージョン差** — `node:test` と `--test` は Node 20 以降で安定している。
  `package.json` の `engines` に下限を明記し、型ストリップ等の実験的機能に依存しない
  （テストは `tsc` が出力した JavaScript に対して実行する）

## References

- `.kiro/specs/trajectory-simulator/design.md` — 出力 JSON の最上位構造、`MODEL_EXCLUSIONS`、
  `CellStatus` / `NotEvaluatedReason`、下流との契約
- `src/prediction_core/record.py` — Throw Record の `to_dict` の実際のキーと `kind` の値
- `docs/original-features.md` 柱1 — 可視化の価値の順序、実装スタックの方針、Hono を使わない理由
- `docs/open-questions.md` — OQ-34 / OQ-38 / OQ-39 / OQ-40 / OQ-41
- `.kiro/steering/tech.md` 開発標準1 / 3、`.kiro/steering/structure.md` Code Organization Principles
