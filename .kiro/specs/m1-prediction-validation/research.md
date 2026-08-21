# Research & Design Decisions: m1-prediction-validation

## Summary

- **Feature**: `m1-prediction-validation`
- **Discovery Scope**: **Complex Integration**（上流3 Spec ＋ 実装済み1 Spec を実データで結合する統合 Spec）
- **Key Findings**:
  - **継ぎ目は本当に空いている。** `flying-object-tracking` は `prediction_core` を import しない設計であり、
    `world-frame-calibration` は変換を提供するが `Sample` を作らない。
    `CameraTrack` → `Sample` の変換は**どの上流も持っていない**。本 Spec が唯一の所有者になる
  - **単位・時間基準は既に揃っている。** 上流は距離 mm・時刻 ms、`t_capture_ms` をそのまま引き継ぐ約束であり、
    継ぎ目に単位変換も時刻の再基準化も要らない。**継ぎ目の仕事は座標変換の適用と品質情報の対応付けだけ**
  - **誤差帰属の決め手は「向き」である。** 座標系ずれは World 座標系に固定された偏りとして、
    Depth による対象物表面の観測は**カメラ視線方向**の偏りとして現れる。
    両者は大きさでは区別できないが、**向きで区別できる**
  - **判定規則を実測前に固定する規律が上流に既にある**（`sensing-foundation` の `OverheadVerdict`、
    `flying-object-tracking` の `DetectorDecision`）。OQ-27 も同じ形に揃えるのが自然

---

## Research Log

### 上流3 Spec の受け渡し契約の突き合わせ

- **Context**: 継ぎ目を設計するには、上流が何を保証し何を保証しないかを正確に知る必要がある
- **Sources Consulted**: `.kiro/specs/flying-object-tracking/design.md`（Boundary Commitments / Data Models / L1-L2）、
  `.kiro/specs/world-frame-calibration/design.md`（同 / L6-L9）、
  `.kiro/specs/sensing-foundation/design.md`（同 / Data Models）、`src/prediction_core/`（実装）
- **Findings**:
  - `CameraTrack` は `handoff_version` / `frame`（常に `CAMERA`）/ `track_id` / `started_t_ms` /
    `points` / `state` / `end_reason` / `source` / `detector_kind` を持つ
  - `TrackPoint` は `CameraPoint` に加えて `frame_index` / `frame_seq` / `gap_before` / `rivals` を持つ。
    `CameraPoint` は `valid_depth_px` / `depth_spread_mm` / `apparent_diameter_px` /
    `expected_diameter_px` を持つ。**これらがそのまま検出品質の材料になる**
  - `WorldTransform` は `apply_point` / `apply` / `inverse` / `as_matrix` を持ち、回転と平行移動のみ。
    **`apply` の中でログを取らないと明記されている**（毎フレーム呼ばれる唯一の経路のため）
  - `CalibrationResult` は `verification_state`（`not_verified` / `passed` / `failed` / `not_judged`）を持ち、
    **検証を経ていない結果を機械的に判別できる**
  - `VerificationReport` は `bias_mm` / `scatter_rms_mm` / `range_buckets` / `scale_check` を持ち、
    「バイアスが支配的なら座標系、ばらつきが支配的なら観測、遠方のバケットだけ大きいなら Depth の距離特性」
    という読み分け規則が既に定義されている
  - `prediction_core.Sample` は `(t_ms, x_mm, y_mm, z_mm)` の4フィールドのみ。**拡張の余地はない**
  - `ThrowRecord` は `extra` を「下流の加算的拡張の退避先」として持つ（D-8）
- **Implications**:
  - 継ぎ目は**変換の適用と、`Sample` に入らない品質情報の並行保持**という2つの仕事に分解できる
  - `verification_state` があるおかげで、「検証を通していないキャリブレーションでの実測」を
    **設計上のゲートとして機械的に強制できる**。手順書の注意書きに頼らずに済む
  - 上流の読み分け規則を本 Spec が引き継げば、「同じ問いに違う基準を使わない」を保てる

### `docs/requirements.md §3` と逐次予測の食い違い

- **Context**: roadmap の ⚠️ と `prediction-core/requirements.md` D-1 が、本 Spec に表の更新を割り当てている
- **Findings**:
  - §3 の表は「予測が1回確定 → 送信 → 駆動開始」という単発予測モデル
  - `prediction_core` は3点で初回予測を出し、以降サンプル追加のたびに更新する（A-1 / A-2）
  - NFR-3（≤ 200 ms）は §3 の区間2＋区間3 から**導出された値**であり、
    「表を更新したらこの値も更新する。両者が食い違ったまま放置しない」と本文に明記されている
  - NFR-3 は §3 の小節であるため、**表と NFR-3 の更新は同一節内の整合作業**である
- **Implications**:
  - 更新対象は「表」だけでなく「表から導出された値」を含む。ただし §3 の外へは広げない
  - 更新は**実測値が揃ってからのゲート付きタスク**にする。想定のまま書き換えると根拠が失われる

### 実際のリリース時刻をどう求めるか（区間1 は完全に未検証）

- **Context**: 実測項目2（リリース〜検出開始）は §3 で「まったく未検証」とされ、
  brief が「ここが想定より長ければプロジェクトの成立性そのものが変わる」と書いている最重要項目
- **Findings**:
  - リリースの瞬間は手・腕による遮蔽のため、カメラからは直接観測できない可能性が高い
  - 一方、放物運動モデルは**観測開始より前へも外挿できる**。空気抵抗を無視するモデルの誤差は
    短時間の外挿では小さい
  - 投擲位置・方向・リリース高さを固定する（A-9）ため、**リリース高さは既知の設定値にできる**
- **Implications**:
  - リリース時刻 = 推定軌道を後ろへ外挿し、リリース高さに達する時刻。**外挿であることを必ず併記する**
  - 外挿には不確かさがあるため、**外部の合図（操作者のイベント記録）との突き合わせを任意で受け付ける**
  - 求め方を書かずに数値だけを出すと、後から誤差の出どころを議論できない。**求め方を要件に含める**

---

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|---|---|---|---|---|
| **収集（Pi）と評価（開発PC）の分離パイプライン** | Pi では実行と記録のみ、開発PC で集計・帰属・判断・可視化 | `structure.md` と `original-features.md` 制約2 に一致。計測が計測対象を歪めにくい | 記録形式が両側の契約になる（上流が既に定義済み） | **採用** |
| 単一プロセスで実行と集計と表示を同時に行う | 実行しながら画面に出す | 手数が少ない | Pi に表示レイヤが載る。計測が計測対象を歪める。制約違反 | 不採用 |
| ライブダッシュボード | リアルタイム送信と監視画面 | 直感的 | OQ-38 で保留済み。`§13.3` が「人間がリアルタイムで見るには速すぎる」と明記 | 不採用 |

**採用理由**: 上流3 Spec がいずれも「記録は実機、集計は開発PC」を前提に記録形式を定義しており、
本 Spec がそれに乗ることで新しい契約を増やさずに済む。

---

## Design Decisions

### Decision 1: 継ぎ目を単一モジュールに閉じ、変換を再実装しない

- **Context**: `CameraTrack` → `Sample` の変換は本 Spec が唯一の所有者（要件 1）
- **Alternatives Considered**:
  1. 変換をパイプライン内にインラインで書く — 呼び出し箇所が増え、座標系の取り違えが分散する
  2. `world_frame_calibration` 側に `Sample` 生成を足す — 上流が `prediction_core` へ依存することになり、
     同 Spec の Allowed Dependencies に違反する
  3. **専用モジュールに閉じ、変換は上流の `WorldTransform` を呼ぶだけにする**
- **Selected Approach**: 3。`seam.py` が唯一 `CameraTrack` と `WorldTransform` と `Sample` を同時に知る場所になる
- **Rationale**: 「World 変換を検出側に書けなくする」という上流の構造的保証を、
  下流側でも「変換は1箇所にしか書かれていない」という形で維持できる
- **Trade-offs**: モジュールが1つ増える。ただしこれは本 Spec の存在理由そのものである
- **Follow-up**: 境界テストで、`seam` 以外のモジュールが `WorldTransform` を適用していないことを固定する

### Decision 2: `Sample` に入らない品質情報を並行配列で保持する

- **Context**: `Sample` は4フィールド固定で拡張できない。しかし誤差帰属には観測品質が要る（要件 1.8 / 6.6）
- **Alternatives Considered**:
  1. `Sample` を継承・拡張する — `prediction_core` の入力契約を壊す。禁止
  2. 品質情報を捨てる — 帰属ができなくなる。本 Spec の核が失われる
  3. **`Sample` と同じ順序・同じ長さの provenance 列を別に持ち、添字で対応させる**
- **Selected Approach**: 3。継ぎ目の戻り値を `(samples, provenance)` の組にし、長さの一致を不変条件にする
- **Rationale**: `prediction_core` の入力契約に一切触れずに、必要な情報を下流へ運べる
- **Trade-offs**: 添字による対応付けは壊れやすい。**長さ一致を不変条件としてテストで固定する**
- **Follow-up**: Throw Record の `extra["m1"]` へ provenance を退避し、再現時に復元できるようにする

### Decision 3: 検証を通していないキャリブレーションでの実測を、既定で拒否する

- **Context**: brief の制約「実測前に `world-frame-calibration` の検証ステップを通す」（要件 2.1）
- **Alternatives Considered**:
  1. 手順書に書くだけ — 忘れる。忘れたことが後から分からない
  2. 警告を出して続行 — 警告は無視される
  3. **既定で拒否し、明示的な許可フラグでのみ続行する。続行時は全生成物に印を付ける**
- **Selected Approach**: 3
- **Rationale**: `CalibrationResult.verification_state` があるため機械的に判定できる。
  「未検証の誤差は帰属できない」という事実を、**運用の注意ではなく構造として表す**
- **Trade-offs**: 開発初期に手間が増える。合成入力では検証済みの結果を合成して回避できる
- **Follow-up**: 許可フラグ付きで得たデータが、後から「検証済みデータ」と混ざらないようにする

### Decision 4: 誤差帰属は「大きさ」ではなく「向き」で切り分ける ★

- **Context**: 要件 6。`docs/requirements.md §6.2` が警告する切り分け不能状態を防ぐ
- **Findings**:
  - 座標系のずれは、World 座標系に**固定された**オフセットとして全投擲に同じ向きで現れる
  - Depth は空き缶の**カメラ側表面**を測るため、代表点は真の重心よりカメラ寄りに寄る。
    その偏りは**カメラ視線方向**に沿い、投擲位置が変われば World 上の向きも変わる
  - どちらも「共通の偏り」として現れ、**大きさだけでは区別できない**
- **Selected Approach**: 誤差ベクトル群を (a) 共通の偏り成分 と (b) 投擲ごとのばらつき成分 に分解し、
  (a) について **World 固定方向との整合とカメラ視線方向との整合を両方評価して向きで判別する**。
  (b) は観測サンプルの再抽出による予測ばらつきの見積もりと比較する
- **Rationale**: 上流のキャリブレーション検証レポートが独立に `bias_mm` を出しているため、
  **本 Spec の (a) と突き合わせる形で相互検証できる**。片方だけでは言えないことが言える
- **Trade-offs**: 投擲位置を固定すると、World 固定方向とカメラ視線方向が縮退して判別できない場合がある。
  **判別不能を正常な結果として報告する**（要件 6.10）ことで、誤った断定を避ける
- **Follow-up**: 判別可能性を上げるには投擲位置を2箇所以上にする必要がある。
  レイアウトを設定として外部化しておく（要件 13.8）ことで、後から追加できるようにする

### Decision 5: OQ-27 は3値で判定し、改善適用をゲートにする

- **Context**: 要件 9。`development-environment.md §13.2` と `tech.md` 開発標準4
- **Alternatives Considered**:
  1. 「達成 / 未達成」の2値 — 「取得側が律速で CPU に余裕がある」場合を表現できず、誤ってハード変更を招く
  2. **「継続 / 条件付き継続 / 不足」の3値 ＋ 保留**
- **Selected Approach**: 2。加えて **GATE 0**（§13.2 の未適用項目が残る間は「不足」を出さない）を置く
- **Rationale**: 律速が計算資源なのか取得条件なのかで、次の行動がまったく変わる。
  この区別を判定値そのものに持たせる
- **Trade-offs**: 判定が複雑になる。ただし判定規則を文字列として結果に埋め込むため、後から検証できる
- **Follow-up**: 判定規則は上流（`OverheadVerdict` / `DetectorDecision`）と同じく
  「規則の説明文を結果と同じファイルに含める」形にする

### Decision 6: 可視化の依存を単一モジュールへ隔離する

- **Context**: 要件 8.8 / 8.9。「表示レイヤを Pi 上で動かさない」
- **Selected Approach**: 描画ライブラリを import するモジュールを1つに限定し、
  実機側の実行経路（継ぎ目・実行・記録）からそのモジュールへの import を禁止する。
  依存は任意指定（extras）として宣言し、未導入でも集計・判断は動く
- **Rationale**: `flying-object-tracking` が `cv2` を `mask_ops.py` 1箇所に閉じたのと同じ手法。
  **プロジェクト内で既に有効性が確認されているパターンを再利用する**
- **Follow-up**: 境界テストで静的に固定する

### Decision 7: 集計は上流の道具を再利用する（Build vs Adopt）

- **Context**: 段階別レイテンシの集計（要件 7）
- **Findings**: `sensing-foundation` が NDJSON ログの集計（未知の stage も扱える）を既に提供する
- **Selected Approach**: ログの読み出しと stage×event ごとの集計は上流に委ね、
  本 Spec は **end-to-end の定義と、投擲単位への束ね直し**だけを足す
- **Rationale**: 集計器を二重に持つと、同じログから違う数字が出る。それは最悪の状態
- **Trade-offs**: 上流の集計出力の形に縛られる。許容できる

### Decision 8: 収束の判定規則を実測前に固定する

- **Context**: 要件 5.7 / 5.8。FR-1 の「3」の妥当性を測る
- **Selected Approach**: 「サンプル数 N 以降のすべての予測落下地点が、その投擲の最終予測から
  一定の距離帯に収まり続ける最小の N」を収束サンプル数とする。距離帯は設定値とし、
  **既定は位置精度の暫定許容窓に揃える**（絶対値の合否条件にはしない）
- **Rationale**: 真値との比較で定義すると、真値が欠測した投擲で収束が測れない。
  **最終予測との比較なら投擲内で完結する**。真値との誤差は別項目（要件 5.4）が担う
- **Trade-offs**: 最終予測自体がずれている場合、収束は速く見える。
  そのため**収束サンプル数と最終誤差を必ず併記する**

---

## Synthesis Outcomes

### Generalization

- 実測7項目・帰属・OQ-27・OQ-05 は、いずれも「**投擲群から量を算出し、実測前に固定した規則を当てる**」
  という同じ形をしている。判定規則を「説明文 ＋ 判定値 ＋ 根拠」という共通の形で表し、
  すべての判断をこの形に載せる
- 真値（落下地点・落下時刻・リリース時刻）は「求め方の種別 ＋ 値 ＋ 不確かさ」という共通の形にできる

### Build vs Adopt

| 対象 | 判断 |
|---|---|
| 座標変換 | **Adopt**（`world_frame_calibration.WorldTransform`） |
| 予測・Throw Record | **Adopt**（`prediction_core`。実装済み・18シンボルのみ消費） |
| 入力層・記録・ログ・Throw Record 保存 | **Adopt**（`sensing_foundation`） |
| 検出・追跡 | **Adopt**（`flying_object_tracking`） |
| ログ集計 | **Adopt**（上流の集計。end-to-end の束ね直しのみ追加） |
| 継ぎ目・真値・帰属・判断・可視化 | **Build**（どの上流も持っていない） |

### Simplification

- 入力層の抽象を本 Spec で再定義しない。上流の入力元指定にそのまま委ねる
- 判定器を項目ごとに分けず、**共通の判定結果の形**に載せる
- 可視化を対話的にしない。図はファイルとして出力するだけにする
- `trajectory-simulator` に依存しない。合成入力はテストツリー内の生成器で足りる

---

## Risks & Mitigations

- **上流3 Spec が未実装のまま設計が進む** — 継ぎ目と真値・帰属・判断・可視化は上流の**公開型の形**にしか
  依存しない。上流の型を模した最小ダブルで完成まで到達できる形に分割する
- **投擲位置を固定した結果、座標系ずれと検出バイアスが向きで区別できない** — 判別不能を正常な結果として
  報告する。レイアウトを外部化し、必要なら投擲位置を増やせるようにする
- **真値の測り方が投擲ごとに揺れる** — 測り方の記述を必須項目にし、レポートに出す
- **計測が計測対象を歪める** — 上流と同じ形の ON/OFF 比較を本 Spec の区間でも行い、
  歪んだ場合は結果を無条件に有効としない旨を明示する
- **時間予算表を想定のまま書き換えてしまう** — 更新タスクを実測値の存在でゲートする
- **`pyproject.toml` を3 Spec が同時に編集する** — 追記のみとし、既存行を書き換えない

---

## References

- `docs/requirements.md` §3（時間予算表・NFR-3・NFR-5・NFR-6・NFR-7）/ §6.1 / §6.2 / §8 M1 / FR-1 / FR-2
- `docs/development-environment.md` §12（段階検証）/ §13.1（実測項目）/ §13.2（改善順序）/ §13.3 / §16
- `docs/open-questions.md` OQ-01 / OQ-02 / OQ-05 / OQ-27 / OQ-39 / OQ-40 / OQ-41
- `docs/decisions.md` D-1（合否条件の取り下げ）/ D-2 / D-8（Throw Record スキーマ）
- `.kiro/steering/tech.md` 開発標準1 / 3 / 4 / 5 / 6、`structure.md`、`roadmap.md`
- `.kiro/specs/prediction-core/design.md` / `requirements.md`（D-1）、`src/prediction_core/`
- `.kiro/specs/sensing-foundation/design.md`、`.kiro/specs/flying-object-tracking/design.md`、
  `.kiro/specs/world-frame-calibration/design.md`
