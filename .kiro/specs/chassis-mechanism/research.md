# Research & Design Decisions

## Summary

- **Feature**: `chassis-mechanism`
- **Discovery Scope**: **Extension**（上流 `catch-mechanism` が確立した CAD 基盤への統合。
  ただし新規パッケージを1つ立てるため、統合点の調査は網羅的に行った）
- **Key Findings**:
  - 上流 `catch_mechanism` は**公開契約が `__init__.__all__` で機械的に固定**されており、
    造形制約の検査（`check_envelope` / `check_material` / `check_joint`）・形状指標の型と照合
    （`PartMetrics` / `GeometryBaseline` / `compare_metrics` / `load_baseline` / `estimate_mass_g`）・
    ゴミ箱の採寸値・出所の型まで**そのまま再利用できる**。本 Spec が書き直す必要があるのは
    「記録を**書き出す**側」だけである（`write_baseline` / `verify_baseline_digest` は非公開）
  - 上流の分割数導出 `required_segment_count` は**円環専用**（扇形の外接箱で判定）である。
    ゴミ箱固定アダプタ（円環）には**そのまま使えるが**、駆動ベース（中央ハブ＋放射状アーム）は
    円環ではないため使えない。⚠️ **駆動ベースの分割は「輪数から決まる位相」であり、
    `check_envelope` が造形可能性の関門になる**という別の導出になる
  - `docs/bom.md` は 2026-09-03 に**ブラケットと駆動ユニットの実測**を済ませており、
    `R ＝（機体中心 → 取付面の距離）＋ 33.9mm` という式まで到達している。
    ⚠️ ただし **33.9mm が軸方向であることは「確認待ち」**と明記されている。
    したがって本 Spec は**この式を実装しつつ、基準面の定義をパラメータとして持ち、
    確認が済むまで出所を仮値として扱う**必要がある
  - `tests/` に `__init__.py` が無く**テストモジュール名がフラットな名前空間を共有する**ため、
    テストファイルは `test_chassis_` 接頭辞が必須である（上流が `test_catch_` で回避済み）
  - `.gitattributes` は `*.json` を CRLF に固定する一般則を持ち、
    **Python から書き戻す設定ファイルだけ `eol=lf` の例外行を後置**している。
    本 Spec も `configs/chassis_mechanism/*.json` に同じ例外を要する

## Research Log

### 上流 `catch_mechanism` の公開契約で何が再利用できるか

- **Context**: 要件 1.3 が「上流が公開している寸法値・造形制約・継手方針を参照して用い、
  同じ値を自身の設定ファイルへ再定義しない」と要求する。どこまで再利用でき、
  どこから自前で持つ必要があるかを確定させないと、境界が曖昧なまま設計が進む
- **Sources Consulted**: `src/catch_mechanism/__init__.py`（公開契約の docstring と `__all__`）、
  `constraints.py` / `metrics.py` / `params.py` / `config.py`、
  `tests/catch_mechanism/test_catch_downstream_contract.py`、
  `.kiro/specs/catch-mechanism/design.md`「Boundary Commitments」「Revalidation Triggers」
- **Findings**:
  - **再利用できる（公開されている）**: `TrashCanMeasurements`（底の外径・平面部径・テーパー角・
    肉厚・高さ・重量）、`PrintingConstraints`（造形可能寸法・材料・密度・余裕）、
    `JointPolicy`（ボルト呼び・貫通穴径・インサート外径と長さ・ダボ径・支圧面積の下限）、
    `ALLOWED_MATERIALS`、`Provenance`、`Envelope` / `BuildViolation` / `check_envelope` /
    `check_material` / `check_joint` / `required_segment_count`、
    `PartMetrics` / `GeometryBaseline` / `MetricsMismatch` / `compare_metrics` /
    `load_baseline` / `estimate_mass_g` / `PRESENCE_FIELD` / `PRESENT` / `ABSENT`、
    `load_params` / `dump_params`
  - **再利用できない（意図的に非公開）**: `metrics.write_baseline` と
    `metrics.verify_baseline_digest`。上流 docstring は「下流は記録を消費するだけで再生成しない」
    と明記している。⚠️ **本 Spec は自分の部品の記録を持つため、書き出しは自前で実装する**
  - `parameters_digest` は公開されているが、引数が上流の `MechanismParams` であり、
    本 Spec のパラメータには使えない。**識別子の算出は自前で持つ**
  - 上流 `dimensions.json` は**あらゆる階層で未知キーを拒否**する。
    `tests/catch_mechanism/test_catch_config.py` が `document["chassis"] = {...}` を
    拒否することを固定している。⚠️ **本 Spec のパラメータを上流の設定ファイルへ相乗りさせられない**
- **Implications**:
  - 本 Spec は `configs/chassis_mechanism/` に**自分の設定ファイル群を持つ**
  - 再利用する検査（`check_envelope` / `check_material` / `check_joint`）は**書き直さない**。
    要件 2.2 / 2.4 / 2.9 の「上流が公開する検査を用いて確認する」はこの方針の明文化である
  - 形状指標は**型と照合を借り、書き出しだけ自前**という非対称な再利用になる。
    これは境界の破れではなく、上流が「消費専用」として設計した契約に忠実な形である

### 駆動ベースの分割は上流の分割数導出に載るか

- **Context**: 要件 2.1 は「分割数を…造形可能寸法と部品の外形から導出し、手で決めた分割数を
  設定値として持たない」と要求する。上流の `required_segment_count` をそのまま使えるかを確認する
- **Sources Consulted**: `src/catch_mechanism/constraints.py`（`sector_envelope` /
  `required_segment_count` の docstring と実装）
- **Findings**:
  - `required_segment_count` は**円環を n 等分した扇形**を前提とし、
    外接箱を「半径方向 `D/2`」と「接線方向 `D·sin(π/n)`」の2辺で評価する。
    内径を受け取らないため半径方向は**中心を含む扇形という最悪値**を採る
  - 駆動ベースは A-4 により**中央ハブ＋放射状アーム3本**である。これは円環ではなく、
    分割の位相が「輪数」から決まる。円環の等分と同じ式には載らない
  - ゴミ箱固定アダプタは**円環そのもの**（底の外径 φ180 を受ける座）であり、
    `required_segment_count` がそのまま適用できる
- **Implications**:
  - **部品ごとに導出の形が違う**ことを設計で明示する:
    - 円環部品（アダプタ）→ `required_segment_count` で分割数を導出
    - 位相が決まっている部品（駆動ベース）→ 分割数は輪数から決まり、
      **各断片の外接箱を `check_envelope` に通すことが関門**になる
  - どちらの場合も「手で決めた分割数を設定値として持たない」は満たされる。
    後者は「輪数」という**別の設計事実**から従属して決まる

### ブラケット実測値と `R` の導出式の確からしさ

- **Context**: 要件 3.3 が `R ＝（機体中心 → 取付面の距離）＋ 33.9mm` の導出を求めるが、
  この 33.9mm の方向が確定していない
- **Sources Consulted**: `docs/bom.md`「付属ブラケットと駆動ユニットの実測（2026-09-03）」、
  `docs/drivetrain-spec.md §6.3`、`docs/requirements.md` の単位規約
- **Findings**:
  - 実測は3つ: モータ全長 70.6mm / **取付面→接地点（垂直）60.0mm** /
    取付面→ホイール中心 33.9mm
  - bom.md 自身が整合を検算している: 33.9 と 60.0 が**両方とも垂直**だと差 26.1mm となり、
    ホイール半径 30mm と 3.9mm 合わない。よって 60.0 が垂直・33.9 が軸方向と読むと矛盾しない
  - しかし bom.md は同時に「**方向が軸方向であることの確認待ち**」と明記している。
    また「別途 105.6mm という測定値があったが**基準面が不明のため破棄した**」とも記録している
  - ⚠️ **測定値そのものより「どの基準面から測ったか」が失われることが事故の形**である
- **Implications**:
  - 本 Spec は寸法値に**基準面の定義を併記する**設計にする。
    `bracket.mount_face_to_wheel_center_mm` は値だけでなく、
    どの面のどの点からどの方向へ測ったかを人が読める形で持つ
  - 出所は、方向の確認が済むまで**仮値**として扱う（要件 1.9）。
    確認後に実測へ昇格する。⚠️ 値が変わらなくても**出所は変わる**
  - `R` は式で導出し、`layout.json` に**入力と出所を添えた導出記録**として残す
    （上流 `catch-opening.json` と同じ形）

### 最低地上高は何から決まるか

- **Context**: 要件4が4部位の床との隙間の算出を求める。何を入力とすれば再算出が成立するか
- **Sources Consulted**: `docs/drivetrain-spec.md §6.3`、`docs/bom.md §B` の鉛直スタック図
- **Findings**:
  - 鉛直方向の基準は**接地点（床）**であり、そこから上へ「実効転がり半径 → 車軸中心 →
    モータ胴体下面（車軸中心 − モータ半径）」「取付面（＝ベース板下面）」と積み上がる
  - ブラケットを反転してモータを吊り下げる構成により、ベース板下面は床から 60.0mm、
    モータ胴体下面は約 11.5mm となる。⚠️ **当初懸念された「ベース板がモータより下」は解消済み**
  - ⚠️ **すべての高さが実効転がり半径に連動する。** 公称 30mm に対し荷重下で 29.25mm なら、
    モータ胴体下面は 10.75mm へ下がる
- **Implications**:
  - 隙間の算出は**実効転がり半径を入力とする関数**にする。
    実測が入ったら自動で再算出される（要件 4.7）
  - 隙間の違反は**値として返す**（上流 `check_envelope` と同じ流儀）。
    全部位をまとめて返さないと「x を直したら次は y」という往復になる

### テスト・パッケージング・改行コードの既存規約

- **Context**: 新規パッケージを既存の単一 `pyproject.toml` へ相乗りさせる際に壊してはいけない不変条件
- **Sources Consulted**: `pyproject.toml`、`tests/prediction_core/test_packaging.py`、
  `tests/sensing_foundation/test_sensing_boundaries.py`、`tests/catch_mechanism/test_catch_boundaries.py`、
  `.gitattributes`、`.gitignore`
- **Findings**:
  - `[project].dependencies == []` は固定されている。**extras は許可リスト方式**で、
    `ALLOWED_OPTIONAL_EXTRAS` が2箇所（`test_packaging.py` と `test_sensing_boundaries.py`）に
    **複製されている**。⚠️ 片方だけ更新すると落ちる
  - ⚠️ **本 Spec は新しい extra を追加しない。** 形状ライブラリは上流が導入済みの `cad` を使う。
    したがって**許可リストの2ファイルには一切触れない**
  - `[tool.hatch.build.targets.wheel].packages` への追記は必要
  - `tests/` に `__init__.py` が無く、テストモジュール名がフラットな名前空間を共有する。
    `test_boundaries.py` などの一般名は既に使われている → `test_chassis_` 接頭辞が必須
  - `.gitattributes` は `*.json text eol=crlf` の後ろに
    `configs/catch_mechanism/*.json text eol=lf` を置いている（git は最後にマッチした行を採用）
  - `var/` は `.gitignore` 済み。`*.step` / `*.stl` / `*.3mf` は `-text` 済み
- **Implications**:
  - 変更する既存ファイルは `pyproject.toml`（packages 1行）と
    `.gitattributes`（LF 例外1行）の**2つだけ**に収まる
  - `.gitignore` は追加不要（`var/` と `*.FCStd` が既に効く）

### 下流（`teleop-bringup`）が実際に必要とする値

- **Context**: 要件11の「公開」を、絵に描いた餅にしないために下流の受け口を確認する
- **Sources Consulted**: `firmware/lib/drivetrain_control/include/drivetrain_control/config.hpp`、
  `kinematics.hpp`、`units.hpp`、`.kiro/specs/teleop-bringup/brief.md`
- **Findings**:
  - `GeometryParams` は `wheel_angle_rad[3]`（機体 +x から反時計回りに測った各輪の取付角）と
    `base_radius_mm`（機体中心から各輪までの距離）を持ち、
    `equilateral(base_radius_mm, first_wheel_angle_rad)` で 120° 等配置を組み立てる
  - `EncoderParams` は `counts_per_wheel_rev`（M2a-0 の実測校正値）と `wheel_diameter_mm` を持つ
  - ⚠️ **輪番号と符号の対応を定義する唯一の場所は `Kinematics` の行定義**であると
    `kinematics.hpp` が明記している。設計側で定義し直してはならない（要件 3.6 の根拠）
  - `teleop-bringup/brief.md` は整備スタンドについて「**製作は `chassis-mechanism` の所有へ移した**。
    本 Spec は**使う側**である」と明記している
- **Implications**:
  - 公開すべき値は **`base_radius_mm` / `first_wheel_angle`（と等配置である事実） /
    `wheel_diameter_mm`（実効値） / 実測重量 / 重心** に具体化できる
  - ⚠️ 下流はファームウェアであり Python から読まない。**公開の実体は記録ファイルと公開 API の
    両方**とし、人が転記する経路を前提にする。転記ミスを検出できるのは
    `configs/trajectory_sim/*` 側だけであり、そちらには一致検査を置く

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| **Core + Optional Adapter（採用）** | 標準ライブラリのみの中核（寸法・導出・検査・記録）の上に、形状生成層（`cad` extra）を薄く載せる | 上流 `catch-mechanism` と同形。造形環境なしで大半を検証できる。境界を `ast` で静的検査できる | 中核と CAD 層の境界を守る規律が要る（静的検査で担保） | `prediction-core` / `drivetrain-core` から続く既存パターン |
| 上流パッケージへの相乗り | `catch_mechanism` に chassis 用モジュールを足す | 再利用が最も容易 | ⚠️ **上流の未知キー拒否テストが `chassis` キーを明示的に拒否している。** 責務の単独所有（A-3）も壊れる | 却下 |
| CAD 層のみの薄い実装 | 形状生成だけを持ち、値は上流と `docs/` に置く | 実装量が最小 | ⚠️ OQ-07 / OQ-09 の決着先が無くなる。実測の流し込み経路も持てない | 却下 |
| 形状生成を外部 CAD の手作業に委ねる | FreeCAD で直接モデリング | 立ち上がりが速い | ⚠️ 寸法が変わるたび GUI 作業をやり直す。要件 1.5 / 1.11 を満たせない | 却下（上流と同じ理由） |

## Design Decisions

### Decision: 形状指標は「型と照合を借り、書き出しだけ自前で持つ」

- **Context**: 要件 1.12（同一入力から同一指標）と、要件 1.3（上流の再定義禁止）が同時にかかる
- **Alternatives Considered**:
  1. 上流へ `write_baseline` の公開を依頼する — 上流の公開 API 変更は
     「Revalidation Triggers 項目4」に当たり、`catch-mechanism` の再検証を要求する
  2. 記録の型ごと自前で定義する — `PartMetrics` / `GeometryBaseline` を二重定義することになり要件 1.3 に反する
  3. 型と照合は上流から借り、**書き出しだけ**本 Spec が持つ
- **Selected Approach**: 3。`PartMetrics` / `GeometryBaseline` / `compare_metrics` / `load_baseline` は
  上流の公開 API を使い、`dump_baseline`（原子的書き出し・LF・キー整列）と
  パラメータ識別子の算出だけを `baseline.py` / `config.py` が持つ
- **Rationale**: 上流 docstring が「下流は記録を**消費するだけで再生成しない**」と述べているのは
  **上流自身の記録**についてである。本 Spec が持つのは**自分の部品の記録**であり、
  その書き出しを自分で持つことは契約に反しない。型を借りることで
  `geometry-baseline.json` の形式が上流と揃い、読み手が2種類の形式を覚えずに済む
- **Trade-offs**: 書き出しの実装が上流と似た形で2箇所に存在する。
  ただし**書き出す対象が別のファイル**であり、同じ値の二重管理にはならない
- **Follow-up**: 上流の `GeometryBaseline` の形式が変わったら本 Spec の書き出しも追随する
  （Revalidation Triggers 項目5 が既に名指ししている）

### Decision: 分割の導出を部品の種類で分ける

- **Context**: 要件 2.1 の「手で決めた分割数を持たない」を、円環でない部品にどう適用するか
- **Alternatives Considered**:
  1. すべての部品を円環として近似し `required_segment_count` に通す
  2. 部品の種類ごとに導出の形を変える
- **Selected Approach**: 2。**円環部品**（ゴミ箱固定アダプタ）は `required_segment_count` で
  分割数を導出する。**位相が決まっている部品**（駆動ベース＝中央ハブ＋アーム3本）は
  分割数が輪数から従属し、`check_envelope` が造形可能性の関門になる
- **Rationale**: 1 を採ると、外接箱が円と大きく異なる部品（アーム、トレイ、スタンド）に対して
  ⚠️ **過大な分割数が「正しい導出」の顔をして返る**。上流の docstring 自身が
  「半径方向の広がりは分割数を増やしても縮まない」と警告しており、
  円環でない形へ適用することは想定されていない
- **Trade-offs**: 導出の形が2つになる。設計・レビュー時に「どちらの部品か」を意識する必要がある
- **Follow-up**: 部品表に「円環／非円環」の区別を持たせ、テストで両方の経路を通す

### Decision: 実測値の置き場所を「設計入力」と「組立後の観測」で分ける

- **Context**: 要件1（現物採寸）と要件10（組立後の実測）が、どちらも「実測値を持つ」と述べている
- **Alternatives Considered**:
  1. すべて `dimensions.json` に置く
  2. 設計入力（`dimensions.json`）と組立後の観測（`measurements.json`）に分ける
- **Selected Approach**: 2
- **Rationale**: 前者は**設計を決める入力**であり、変われば形状が変わる。
  後者は**設計の結果を検証する観測**であり、変わっても形状は変わらない
  （変わるのは合否と下流へ渡す値である）。同じファイルに混ぜると
  ⚠️ 「形状を再生成すべき変更」と「再生成不要な変更」が区別できなくなり、
  パラメータ識別子（digest）が観測のたびに動いてしまう
- **Trade-offs**: ファイルが1つ増える。実効転がり径のように
  「観測だが下流の入力になる」値の所属を明示する必要がある
  （→ **観測側に置く**。設計は公称値で成立し、観測は下流と検証にだけ効く）
- **Follow-up**: `check` が両ファイルの整合（例: 隙間の実測が設計値と同じ部位名を持つ）を検査する

### Decision: 上流の例外を包み直さない

- **Context**: 本 Spec は上流の `load_params` / `check_envelope` / `check_joint` を呼ぶため、
  上流の `CatchMechanismError` 系が本 Spec の呼び出し経路から漏れてくる
- **Alternatives Considered**:
  1. 境界で捕捉して `ChassisMechanismError` へ包み直す
  2. そのまま伝播させ、CLI の終了コード表が**両方の階層**を扱う
- **Selected Approach**: 2
- **Rationale**: 包み直すと ⚠️ **「上流の設定が壊れている」のか「本 Spec の設定が壊れている」のかが
  メッセージから消える**。本 Spec の失敗として報告してしまうと、直す場所を探すのに遠回りする。
  終了コードの意味（入力不正 / 不一致 / 環境不足）は両階層で共通に写せる
- **Trade-offs**: 例外階層が2つ見える。docstring と CLI のヘルプで明示する
- **Follow-up**: `EXIT_CODE_BY_ERROR` に上流の型を含め、テストで両階層の写像を固定する

### Decision: メイン電源スイッチを設け、非常停止とは兼ねないものとして記録する（OQ-11）

- **Context**: OQ-11 の決着期限が「機構設計時」であり、本 Spec が唯一の決着先である。
  OQ-13（物理的な非常停止）と兼ねられるかの判断も求められている
- **Alternatives Considered**:
  1. スイッチを設けず、XT60 の抜き差しを日常の入切とする
  2. スイッチを設け、非常停止も兼ねる
  3. スイッチを設けるが、非常停止としては兼ねない（判断材料のみ記録し、後付け余地を残す）
- **Selected Approach**: 3
- **Rationale**: 1 は ⚠️ **コネクタの抜き差し回数が寿命を超え、抜けかけの接触不良が最も危険な形**で
  現れる。2 は「走行中の機体へ手を伸ばして押す」ことを前提にしてしまい、
  ⚠️ **転倒・巻き込みの危険が停止手段の側に生まれる**。
  `tech.md` 開発標準2 は「無線に依存する停止手段は安全装置ではない」と述べるが、
  **手を伸ばす必要がある物理スイッチも同じ理由で非常停止として不十分**である
- **Trade-offs**: OQ-13 が未決のまま残る。ただし OQ-13 の期限は「M3 着手前」であり、
  本 Spec の期限ではない。後付けできる取付余地を残すことで選択肢を閉じない
- **Follow-up**: 取付余地（ねじ穴と配線の引き出し）を形状として残し、記録に残す

### Decision: 電源分岐はネジ留め端子台とする（OQ-12）

- **Context**: 1本のバッテリ出力を、モータドライバ3台と 5V 生成回路へ分ける必要がある
- **Alternatives Considered**:
  1. はんだ付けによる Y 字分岐
  2. 圧着スリーブによる分岐
  3. ネジ留め端子台
- **Selected Approach**: 3
- **Rationale**: 1 は取り回しが硬くなり、⚠️ **ブリングアップ中に何度も配線を替える段階と相性が悪い**。
  2 は再作業ができず、間違えたときにやり直せない。3 は**保持箇所を形状として設計でき**、
  配線ガイドの一部として扱える。⚠️ ドライバは「交換前提の構造にしてある」と
  `drivetrain-spec.md §11` が述べており、分岐も交換可能である必要がある
- **Trade-offs**: 部品が1点増える。質量と高さは重心の見積もりへ算入する
- **Follow-up**: 主ヒューズはバッテリ直近（端子台より上流）に置く。位置関係を記録に残す

### Decision: `R` の値を設計で固定せず、決定手順と暫定値を記録する

- **Context**: 要件 3.4 は決定値の記録を求め、要件 3.5 は転倒余裕を合否条件にしないと述べ、
  `tech.md` 開発標準1 は未実測の値を合否条件にしないと述べる
- **Alternatives Considered**:
  1. 設計時に `R` を数値で確定させる
  2. 決定手順（同時に満たすべき制約）を確定させ、現時点の暫定値を仮値として置く
- **Selected Approach**: 2。`R` は次の制約を同時に満たす範囲から選ぶ:
  (a) 各断片が `check_envelope` を通ること、(b) アームが接合部の当たり面を確保できる長さを持つこと、
  (c) ゴミ箱固定アダプタが底の外半径まで届くこと、(d) 転倒余裕の見積もりを記録できること
- **Rationale**: `R` は `bracket.mount_face_to_wheel_center_mm` に直接依存し、
  その値の**基準面が未確認**である（上記の研究ログ）。⚠️ 数値を先に固定すると、
  基準面の確認結果が食い違ったときに「決定」と「実測」のどちらを直すべきかが曖昧になる
- **Trade-offs**: 設計段階では `R` が1つの数値に落ちない。
  代わりに導出記録 `layout.json` が入力・式・出所を持ち、確認後に値が確定する
- **Follow-up**: 基準面の確認（要件 1.6）を実装タスクの早い段階に置く

## Risks & Mitigations

- **リスク: 33.9mm の基準面が確認の結果ずれ、`R` と最低地上高が同時に動く** —
  両者を同一の導出（`layout.py`）から算出し、パラメータ1点の更新で全体が再導出される構造にする。
  ⚠️ 手で写した中間値をどこにも残さない
- **リスク: 実効転がり径が公称より小さく、最低地上高が設計値を下回る** —
  隙間の算出を実効転がり半径の関数にし、`check` が違反を全部位まとめて報告する。
  組立後の実測（要件 4.5）で設計値との差を必ず記録する
- **リスク: ハブの止めネジがシャフトの丸い面に当たり、空転する** —
  ⚠️ **制御側から検出できない**ため、組立手順で「平面へ当てる」操作を明示し、
  手回しでの滑り確認を組立完了の判定項目に含める（要件 9.4 / 9.5）
- **リスク: 上流の仮値（底の平面部径）の上に座面形状が乗る** — A-2 の方針どおり実測して
  上流の値と出所のみを更新する。⚠️ **構造・キー名・単位は変更しない**（要件 6.4）
- **リスク: 造形・組立という物理作業が長期化し、設計側の変更が反映されないまま組み上がる** —
  組立時の差分を `measurements.json` へ記録し、寸法パラメータへ反映する経路を要件 9.7 として持つ。
  記録が空のまま「完了」と呼べないよう、必須項目の充足を `check` の判定に含める
- **リスク: 部品点数が多く、締結部品の不足で組立が止まる** — 締結部品の一覧を接合部の定義から
  導出し、造形の前に出せるようにする（要件 2.10）
- **リスク: `teleop-bringup` が値を手で転記する際の写し間違い** — シミュレータ設定側には
  一致検査（`layout --check`）を置く。⚠️ ファームウェア設定側は本 Spec からは検査できないため、
  公開記録に単位と基準（機体 +x からの反時計回り）を明記する

## References

- `.kiro/specs/catch-mechanism/design.md` — 上流の Boundary Commitments / Revalidation Triggers / CAD 基盤
- `src/catch_mechanism/__init__.py` — 公開契約の正（`__all__` と、公開しないものの理由）
- `src/catch_mechanism/constraints.py` — 造形制約の検査と円環分割数の導出（適用範囲の宣言を含む）
- `docs/bom.md` §B — ホイール／ハブ／ブラケットの確定寸法と実測（2026-09-03）、`R` の導出式
- `docs/drivetrain-spec.md` §6 / §11 — 機構設計方針、CAD 着手手順、実機確認事項
- `docs/open-questions.md` — OQ-07 / OQ-09 / OQ-11 / OQ-12 / OQ-13 の決め方と期限
- `firmware/lib/drivetrain_control/include/drivetrain_control/config.hpp` / `kinematics.hpp` —
  `GeometryParams` / `EncoderParams` と、輪番号・符号の定義が置かれている唯一の場所
- `.gitattributes` / `pyproject.toml` / `tests/prediction_core/test_packaging.py` —
  改行コードとパッケージングの既存不変条件
