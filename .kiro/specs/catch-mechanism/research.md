# Research & Design Decisions

## Summary

- **Feature**: `catch-mechanism`
- **Discovery Scope**: New Feature（機構トラックの1本目。リポジトリに CAD 資産は存在しない）
- **Key Findings**:
  - 形状の正を `.py` に置くか `.FCStd` に置くかは**ツールの好みではなく構造の問題**であり、
    FreeCAD 側には未修正の TNP 関連 issue と git 差分不能という**具体的な故障モード**がある
  - 既存リポジトリには「実行時サードパーティ依存ゼロ」を静的に守る仕組みが2系統あり、
    形状ライブラリの導入は**任意依存（extras）と許可リストへの1行登録**で衝突なく収まる
  - `trajectory_sim` 側は `catch.position_tolerance_mm` を**有効なパラメータパス**として既に持っており、
    還元はコード変更ゼロ・**設定ファイルの値の追記だけ**で成立する
  - FR-7 と FR-12 の二律背反は、**「捕捉面積の増加を開口の外側だけで行う」配置**によって分離できる

## Research Log

### 形状ライブラリ（build123d）の実在性と入出力 API

- **Context**: 形状の正を `.py` に置く方針の実装可能性と、生成物形式の確認
- **Sources Consulted**: build123d の公式ドキュメント（Import/Export）、PyPI / GitHub リリース情報
- **Findings**:
  - 活発に保守され、新しい Python 版への追随が続いている。カーネルは `cadquery-ocp`（OCCT バインディング）
  - STEP は `export_step()`（既定単位 `Unit.MM`、`precision_mode` あり）、STL は `export_stl()`
    （`tolerance` / `angular_tolerance`）、3MF は `Mesher` クラス（メタデータ埋め込み可）
  - いずれもファイルパスまたはストリームへ書け、**ヘッドレスで完結する**
- **Implications**: 「対話操作なしに STEP / STL / 3MF を生成する」という要件はライブラリ標準機能で満たせる。
  単位はミリメートル固定で指定でき、要件 3.4 に直接対応する

### FreeCAD を形状の正にしない根拠の再確認

- **Context**: 「Python API で書けば FreeCAD でもよいのでは」という反論への構造的な回答が要る
- **Sources Consulted**: roadmap「機構トラックの起点」/ brief.md（FreeCAD issue #26084 / #17041 / #31040、
  テキスト化要求 #5585 / #9432）
- **Findings**:
  - TNP は**カーネルが面へ振る生成名の問題**であり、GUI 操作か Python API かに依らない
  - ⚠️ **Spreadsheet 集中管理という定石そのものがバグ経路**（#26084、1.1.0rc1 で再現・open）
  - `.FCStd` の実体は BREP であり、寸法を 1mm 変えるとエントリ番号が総入れ替えになる。
    `textconv` 回避策はマシンごとの再設定が必要でリポジトリに同梱できない
- **Implications**: FreeCAD は「測定器であって編集器ではない」。⚠️ **この位置付けを規律で守らせず、
  `.FCStd` を git 管理から外し、指標照合で違反を可視化する構造にする**

### 既存リポジトリの依存境界の仕組み

- **Context**: 重い CAD 依存を追加したときに、どのテストが落ちるかを事前に特定する
- **Sources Consulted**: `tests/prediction_core/test_packaging.py` / `test_boundaries.py` /
  `test_trajectory_sim_boundaries.py`、`pyproject.toml`、roadmap「着手順序の制約」
- **Findings**:
  - `test_boundaries.py` 系は `src/prediction_core/**` と `src/trajectory_sim/**` の import を
    `ast` で走査するもので、**新パッケージは走査対象外**
  - `test_packaging.py` は `dependencies == []` を固定しつつ、extras は
    `ALLOWED_OPTIONAL_EXTRAS`（`sensing` / `tracking` / `calibration` / `m1-viz`）の**部分集合**であることを要求する。
    ⚠️ **extras 名を追加すると、許可リストへ登録しない限りここが落ちる**
- **Implications**: 触れるのは許可リストの1行のみ。これは `sensing-foundation` が意図した拡張点であり、
  「実行時依存ゼロ」の表明そのものは変更しない

### `trajectory_sim` への還元経路

- **Context**: 「コードに触れずに値だけ渡す」が本当に成立するかの確認
- **Sources Consulted**: `src/trajectory_sim/params.py`（`CatchCriteria` / `ScenarioParams` / `PARAMETER_PATHS` /
  `Provenance`）、`configs/trajectory_sim/sweep-reachability.json`
- **Findings**:
  - `ScenarioParams.catch` は `CatchCriteria` であり、`position_tolerance_mm` は既存フィールド。
    `PARAMETER_PATHS` はデータクラス木の走査で生成されるため `catch.position_tolerance_mm` が含まれる
  - `provenance` のキーは `PARAMETER_PATHS` と一致することが構築時に検証される。
    値集合は `measured` / `assumed` の2値
  - 設定 JSON はあらゆる階層で未知キーを拒否するが、`position_tolerance_mm` は既知キーである
- **Implications**: 還元は既存2ファイルへの**値の追記**で完結する。
  ⚠️ **本 Spec 側の `Provenance` を2値に揃えることが、翻訳層を作らないための条件**である

### ゴミ箱の選定基準（実売調査の結果を機械可読化できるか）

- **Context**: 「百均で適当に買う」を許さないための判定可能な基準が要る
- **Sources Consulted**: roadmap「ゴミ箱の選定基準（購入前に確定させる）」（2026-08-30 の実売調査で確定）
- **Findings**:
  - 判断ルールは「丸型・5L 以上・フタなし」。φ215〜225 が実質の上限で、φ240 以上は 100 均に存在しない
  - テーパーは全品にある（ネスティング前提）ため「強いテーパー不可」は成立せず「緩いものを選ぶ」へ緩和済み。
    目安は上φ220→底φ158（片側約7°）は可、上φ225→底φ145（約10°）は強すぎる
  - 第一候補は重量が実測で判明している唯一の品であり、**別ルートで単品入手できる**
- **Implications**: しきい値（下限・上限・拒否条件）と「望ましいが必須でない」項目（外向きリム）を
  分けて表現できる。⚠️ **基準は roadmap が正であり、推測で書き換えない**

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| **Core + Optional Adapter（採用）** | 標準ライブラリのみの中核（値・導出・検査）＋ 任意依存の形状層 | 下流とテストの大半が CAD 非導入で動く。依存境界が構造として現れる | 形状に関する検査の一部が CAD 導入環境でしか走らない | `prediction-core` / `drivetrain-core` と同じ「実機なしで検証できる中核」の踏襲 |
| 単層（全部が build123d に依存） | パッケージ全体が形状ライブラリを要求 | 実装が単純 | `chassis-mechanism` が OCCT を強制される。CAD 非導入環境でテストが走らない | 却下 |
| 別プロジェクトへ分離 | `cad/` に独立した `pyproject.toml` | 既存の依存管理に一切触れない | `structure.md` の「単一 `pyproject.toml` へ相乗り」から外れ、同じ判断を下流で繰り返す | 却下 |
| FreeCAD スクリプト | `.FCStd` を Python API で操作 | 組立・図面と同一環境 | ⚠️ TNP・git 差分不能。**形状の正がバイナリになる** | 却下（A-1） |

## Design Decisions

### Decision: 分割数をパラメータではなく導出値にする

- **Context**: φ180 超の部品は分割必須。受け口の外径は採寸値とフランジ幅から決まるため、**後で変わる**
- **Alternatives Considered**:
  1. 分割数を `dimensions.json` のパラメータにする（例: 3分割）
  2. 造形可能寸法から必要な最小分割数を導出する
- **Selected Approach**: 2。外径 D の円環を n 等分した扇形の弦長 `D·sin(π/n)` が
  造形可能寸法から余裕を引いた値以下になる最小の n を返す
- **Rationale**: 手で決めた分割数は、寸法が変わったときに**黙って造形可能寸法を超える**。
  ⚠️ roadmap が指摘するとおり**円は正方形の対角線を使えない**ため、軸並行の外接箱で判定する
- **Trade-offs**: 分割数がパラメータ変更で変わるため、形状指標も変わる（`--update-baseline` を要する）
- **Follow-up**: 現実的な上限（12 分割程度）までに解が無い場合は例外で拒否する

### Decision: 出所（Provenance）は2値のまま、導出値は入力の最弱を継承する

- **Context**: 導出値（位置許容誤差）は「実測 × 仮値」の混合になり得る
- **Alternatives Considered**:
  1. `DERIVED` を third value として追加する
  2. 2値のまま、入力の最弱を継承する
- **Selected Approach**: 2
- **Rationale**: `trajectory_sim.Provenance` は `measured` / `assumed` の2値であり、
  第3の値を作ると**還元時に翻訳層が要る**。⚠️ 翻訳層は「実測でない値が実測として渡る」事故の温床になる。
  さらに「1つでも仮値なら仮値」は `tech.md` 開発標準1（未実測の値を合否条件にしない）と同じ向きに倒れる
- **Trade-offs**: 「導出である」という情報は出所ではなく `formula` フィールドで表現する
- **Follow-up**: 対象物（空き缶）も実測すれば、位置許容誤差は正当に `measured` を名乗れる

### Decision: 形状指標だけをコミットし、生成物（STEP / STL / 3MF）はコミットしない

- **Context**: roadmap は「成果物の正は `.py` と、そこから生成した STEP / STL / 3MF のみ」とし、
  「CI で再生成した STEP の体積とバウンディングボックスを**コミット済みの値**とつき合わせる」としている
- **Alternatives Considered**:
  1. 生成物そのものをコミットする
  2. 形状指標（体積・境界箱・立体数）と `parameters_digest` のみをコミットする
- **Selected Approach**: 2
- **Rationale**: 照合対象は「コミット済みの**値**」であり、バイナリ／大きな STEP 本体は照合に不要。
  指標なら**行単位の差分が読める**。生成物は `var/`（既に `.gitignore` 済み）へ出す
- **Trade-offs**: 造形時に生成物を作り直す手間が要る（`build` 一発で再生成できるため許容）
- **Follow-up**: OCCT 版差による下位桁の揺れに備え、許容差と `generator_version` を記録側に持つ

### Decision: `parameters_digest` によって CAD 非導入環境でも不整合を検出する

- **Context**: 形状ライブラリを入れていない環境では、再生成による照合ができない
- **Selected Approach**: 形状指標の記録に、記録時点の `dimensions.json` の識別子（SHA-256）を含める
- **Rationale**: 「パラメータを変えたのに指標を更新し忘れた」という**最も起きやすい不整合**は、
  形状を作らずとも検出できる。⚠️ **CAD 不在を理由に検査が丸ごと消える状態を作らない**
- **Trade-offs**: 形状に影響しないパラメータの変更でも digest が動く（記録更新を促すだけで害はない）

### Decision: FR-7 と FR-12 の二律背反を「配置」で分離する

- **Context**: テーパー／漏斗は FR-7 に有利・FR-12 に不利。D-9 により跳ね返りはシミュレータの対象外
- **Alternatives Considered**:
  1. 内向きの漏斗を付け、跳ね出しはライナーで抑える
  2. 外向きフランジのみとし、開口内径を一切狭めない
  3. 内向きリップ（返し）を付ける
- **Selected Approach**: 2（詳細と根拠は design.md「受け口形状の決定」）
- **Rationale**: 不利側の作用は**開口の内側を絞ること**から生じる。捕捉面積の増加を**外側だけ**で行えば、
  FR-7 の利得だけを取れる。⚠️ 3 は開口を狭めるうえ、跳ね出しが問題になるかが未検証（OQ-10 と同じ構図）
- **Trade-offs**: 跳ね出しに対する機構的な備えを今は持たない → 後付け用の締結座を残すことで補う
- **Follow-up**: M3 の実投擲で跳ね出しが観測された場合に、リップ部品を後付けする

### Decision: シミュレータ設定への反映は自動書き換えではなく「人手＋検査」

- **Context**: `configs/trajectory_sim/` は `trajectory-simulator` の資産である
- **Selected Approach**: 本 Spec は導出値を `catch-opening.json` として出力し、
  設定ファイルへの反映は人手で行い、**一致することをテストで保証する**
- **Rationale**: 本 Spec が他 Spec の設定を自動書き換えする主体になると、所有が曖昧になる。
  ⚠️ 検査があれば「反映し忘れ」は失敗として現れるため、自動化しなくても安全である
- **Trade-offs**: 反映が1手間増える（1ファイル2箇所）

## Risks & Mitigations

- OCCT 版差で体積の下位桁が動く — 許容差を記録側に持ち、版を記録。版更新時の再記録をレビュー対象にする
- 採寸値が公称と乖離する（縁の巻き込み分） — 採寸項目に「外径ではなく内径」を明記し、出所更新で全体が追随する
- `cad` extra が重く環境によって入らない — 中核を標準ライブラリのみで動かし、`--digest-only` を用意。
  ⚠️ CAD 不在は終了コード 3 で明示し、成功として黙って読み飛ばさない
- 「最終微調整が FreeCAD になる」 — `.FCStd` を git 管理せず、指標照合の失敗として可視化する
- 100均の在庫入れ替え — 第一候補は別ルートで単品入手できる品。候補表に次点を保持する

## References

- roadmap `.kiro/steering/roadmap.md`「機構トラックの起点」「ゴミ箱の選定基準」「Boundary Strategy」
- `docs/requirements.md` FR-7 / FR-12 / NFR-5 / NFR-7 / CON-1〜4、`docs/bom.md §E`、
  `docs/drivetrain-spec.md §6.2`、`docs/decisions.md` D-9、`docs/open-questions.md` OQ-02 / 05 / 08 / 10
- `src/trajectory_sim/params.py`（`CatchCriteria` / `PARAMETER_PATHS` / `Provenance`）
- `tests/prediction_core/test_packaging.py`（`ALLOWED_OPTIONAL_EXTRAS`）/ `test_boundaries.py`
- [build123d Import/Export](https://build123d.readthedocs.io/en/latest/import_export.html) — `export_step` / `export_stl` / `Mesher`、既定単位 `Unit.MM`
- [gumyr/build123d](https://github.com/gumyr/build123d) — 保守状況と対応 Python 版
- FreeCAD issues [#26084](https://github.com/FreeCAD/FreeCAD/issues/26084) / [#17041](https://github.com/FreeCAD/FreeCAD/issues/17041) / [#31040](https://github.com/FreeCAD/FreeCAD/issues/31040)
