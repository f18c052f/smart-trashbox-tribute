# Research & Design Decisions: flying-object-tracking

## Summary

- **Feature**: `flying-object-tracking`
- **Discovery Scope**: **New Feature（greenfield）＋ 上流 Spec との Complex Integration**
  （`sensing-foundation` の公開契約に乗る初めての下流 Spec であり、リポジトリに OpenCV を初めて持ち込む）
- **Key Findings**:
  1. **3つの軽量検出方式の違いは「前景マスクの作り方」だけである。** マスク以降
     （モルフォロジ → 連結成分 → 候補の絞り込み → 代表3D点）は完全に共通化できる。
     これにより OQ-26 の比較が「同一の後段に対する前段の差」として成立し、比較の交絡が減る
  2. **対象物が空き缶（φ65mm）に固定されたことが、候補の絞り込みを物理量で書けるようにしている。**
     `期待直径_px ≈ fx_px × 直径_mm / 距離_mm` により、
     「画素面積の閾値」ではなく「対象物の寸法」をパラメータにできる
  3. **座標系の境界は「型で」守るのが最も確実である。** 本 Spec は `prediction_core` を
     **一切 import しない**。`Sample` を作れないなら World 変換をここに書きようがない
  4. `sensing-foundation` は `CaptureFrame.depth` を **read-only の numpy 配列**として渡す。
     これは制約ではなく**テスト手段**になる（in-place 変更は書き込み時例外として自動的に露見する）
  5. OpenCV は**連結成分ラベリングのためだけに必要**である。それ以外（差分・閾値・
     モルフォロジ相当の縮退処理）は numpy でも書けるが、連結成分は書きたくない

---

## Research Log

### 上流 `sensing-foundation` の公開契約

- **Context**: 本 Spec の入力はすべて上流経由になる。契約を誤解すると結合時に作り直しになる
- **Sources Consulted**: `.kiro/specs/sensing-foundation/design.md`
  （Boundary Commitments / Dependency Direction / File Structure Plan / CoreTypes / StructuredLogger /
  FrameSource / Data Models）
- **Findings**:
  - 入力経路は **`for frame in source.frames():` の1本**。`live` / `recorded` / `simulated` で
    下流のコードは変わらない（Ports & Adapters）
  - `CaptureFrame` は `index` / `seq` / `t_capture_ms` / `depth`（uint16, read-only）/ `profile` /
    `source` / `dropped_before` / `gap_before` を持つ。**時間基準は `t_capture_ms`**
  - `StreamProfile` に `depth_scale_mm`（Depth 1カウントあたりの mm）と
    `intrinsics: CameraIntrinsics | None` がある。**逆投影に必要なものは全部揃っている**
  - 構造化ログは NDJSON。予約 stage は `system` / `capture` / `record` で、
    **下流は `detect` / `track` を自由に足してよい**（同 design.md 要件 8.9）
  - `Logger.timed(stage, event, **data)` と `Logger.stage(stage)` が下流向けの計測入口
  - 記録セッションは `manifest.json` + `frames.ndjson` + `depth.bin` + `summary.json` で、
    `SessionReader` / `RecordedSource` により **SDK も実機も無い環境で再生できる**
  - **OpenCV は意図的に導入していない**（同 design.md Allowed Dependencies）。
    「検出が必要とする道具であり `flying-object-tracking` の責務」と明記
- **Implications**:
  - 本 Spec は `sensing_foundation` の**公開入口だけ**を import する。内部モジュールへは触れない
  - 逆投影のためにカメラパラメータを自前で持たない。`frame.profile.intrinsics` を使う
  - `depth_scale_mm` を無視して「Depth の値 = mm」と決め打ちしない（D435 の既定は 1mm だが、
    **設定で変わりうる値を固定値として扱わない**）
  - ⚠️ **逆投影の演算そのものも自前で持たない。** 上流の公開入口が
    `depth_raw_to_mm` / `is_valid_depth` / `deproject_pixel` を提供しており、
    生カウント → mm の換算・画素中心の規約・ピンホール式・無効画素の判定を1箇所に固定している。
    `world-frame-calibration` も同じ関数に乗るため、**本 Spec が独自に式を書くと
    2経路が食い違い、`m1-prediction-validation` の誤差切り分けが成立しなくなる**。
    本 Spec が持つのは**候補領域内の画素をどう代表させるか**という集約方針だけである

### `CaptureFrame.depth` の read-only 契約

- **Context**: 上流が「in-place 変更は Replay 決定性を壊す」と明示している
- **Findings**: numpy 配列の `flags.writeable = False` は、in-place 演算に対して
  `ValueError: assignment destination is read-only` を送出する
- **Implications**:
  - **要件 1.4 の「テストで検出できるようにする」は、追加の仕組みを作らずに満たせる。**
    read-only 配列を流すテストを常設し、in-place 変更が混入した瞬間に落ちるようにする
  - 検出側は「差分結果を新しい配列に書く」スタイルに統一する。
    ただし**毎フレームの `astype` による全画面コピーは避け**、ROI 切り出し後に型変換する

### 軽量検出方式の候補（OQ-26）

- **Context**: `docs/development-environment.md §10` が候補を列挙したまま保留している
- **Sources Consulted**: 同 §10、`brief.md` Approach、`docs/requirements.md` FR-1
- **Findings**: 候補は Depth 差分 / 背景差分 / フレーム間差分 / Motion detection / ROI / 輪郭抽出。
  これらは**排他的な選択肢ではない**。ROI と輪郭抽出はどの方式にも共通する前処理・後処理であり、
  実際に排他なのは「**前景マスクをどう作るか**」の部分だけである
- **Implications**:
  - ROI と輪郭抽出（連結成分）を**共通の後段**として括り出す
  - 排他な選択肢として残るのは次の3つ:
    1. **距離帯ゲート**（Depth レンジ内の画素を前景とする最軽量ベースライン）
    2. **フレーム間差分**（直前フレームとの Depth 差）
    3. **背景差分**（静止背景の Depth モデルとの差）
  - 3方式は「マスク生成器」という同一インターフェースに収まる。**これが比較の前提になる**

### 対象物の寸法から候補を絞る（φ65mm）

- **Context**: 候補の絞り込みを画素面積の魔法数で書くと、解像度・距離を変えた瞬間に破綻する
- **Findings**: ピンホールモデルでは、距離 `z_mm` にある直径 `D_mm` の物体の像の直径は
  `d_px ≈ fx_px × D_mm / z_mm`。640×480 の D435 の `fx_px` は概ね 380〜390 程度であり、
  φ65mm・距離 2000mm なら `d_px ≈ 12.6`、距離 3000mm なら `d_px ≈ 8.4` になる
- **Implications**:
  - **絞り込み条件は「対象物の直径 mm」と「許容倍率」で書ける。** 画素面積の下限・上限は
    候補の実測距離から**その場で導出する**
  - ⚠️ **上の数値はカタログ由来の目安であり、実測値ではない。** 実際の `fx_px` は
    `frame.profile.intrinsics` から取る。設計に数値を焼き付けない
  - φ65mm は OQ-02 の最終決定ではないため、**設定値として分離する**（要件 12.5）

### 検出方式の比較指標（実効サンプル数）

- **Context**: `docs/development-environment.md §5.1` が「fps 単体で評価しない。実効サンプル数で評価する」
  と定めている。`sensing-foundation` も同じ語で解像度・fps を決める
- **Findings**: 上流の `effective_samples_per_window` は**フレーム層**の指標
  （＝取得できたフレーム数）である。本 Spec が必要とするのは
  **検出・追跡を通過して3D点になった数**であり、対象が違う
- **Implications**:
  - **同じ語を別の意味で使わない。** 本 Spec の指標名を
    `effective_points_per_window` として上流と区別する
  - 窓長は上流と同じ既定 600 ms を採るが、**固定値にせず設定可能にする**
  - 取りこぼし（候補ゼロのフレーム）と3D位置算出の失敗を**別々に数える**。
    前者は検出方式の問題、後者は Depth の欠測の問題であり、改善の打ち手が違う

### 実機なしでの検証手段

- **Context**: 実機は未セットアップ。ハード待ちで開発を止められない
- **Findings**: `sensing-foundation` の `SimulatedSource` は「合成フレーム供給の差し替え口」であり、
  物理は持たない。`RecordedSource` は SDK 非依存で再生できる
- **Implications**:
  - **既知の3D軌道 → Depth フレーム列**を生成する合成器をテストツリーに置く。
    これは本 Spec の検証用の道具であり、投擲物理（OQ-33 / `trajectory-simulator`）ではない
  - 合成器が逆投影の逆演算になるため、**「入れた軌道が出てくる」ことを直接検証できる**
    （要件 11.2）。これは検出・逆投影・追跡を貫く最も強い回帰テストになる
  - ⚠️ 合成 Depth はノイズ特性が実機と異なる。**合成での計測結果を実機の結論として扱わない**
    旨を出力に明示する（要件 11.5）

### 依存の扱い（OpenCV / NumPy / pyproject）

- **Context**: `[project].dependencies` は `[]` でなければならない
  （`tests/prediction_core/test_packaging.py` が静的に検証している）
- **Findings**:
  - ⚠️ **当該テストの表明は `[project].dependencies == []` だけではない。**
    `[project.optional-dependencies] == {}`（extras が1つも無いこと）も併せて表明している。
    したがって **extras を1つ足した時点で既存テストが赤くなる**。
    この表明を許可リスト方式
    （`ALLOWED_OPTIONAL_EXTRAS = {"sensing", "tracking", "calibration", "m1-viz"}` の部分集合）
    へ緩める改修は **`sensing-foundation`（Wave 0）が所有する**。本 Spec は再改修しない
  - 現在の `pyproject.toml` には `[project.optional-dependencies]` が存在しない。
    `sensing-foundation` が `sensing = ["numpy>=1.24"]` を新設する予定
  - OpenCV の GUI 機能は不要（`docs/development-environment.md §4`「GUI 表示を必須要件にしない」）
- **Implications**:
  - 追加は **`[project.optional-dependencies] tracking`** への追記のみとする。
    `[project].dependencies` には触れない。
    **着手前に上流の許可リスト改修が landing していることを確認する**（順序依存）
  - **GUI 無しの構成を選ぶ。** 依存の重さと headless 運用の両方に効く
  - ⚠️ **Pi 上で OpenCV を apt で入れるか pip で入れるかは実機まで確定させない**（OQ-41）。
    実測結果を OQ-41 の判断材料として `measurements.md` に記録する

---

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | 判定 |
|---|---|---|---|---|
| **戦略パターン（マスク生成器のみ差し替え）** | 前景マスクの生成だけを差し替え、後段を共通化する | OQ-26 の比較が交絡しない。実装量が最小。方式追加が局所的 | 「マスクを作らない方式」（例: 学習済み検出器）は素直に嵌らない | **採用** |
| 検出器まるごとを差し替える | 「フレーム → 候補集合」全体を方式ごとに実装する | 任意の方式（AI 含む）を後から嵌められる | **後段の実装差が比較結果に混入する。** 3方式で同じバグを3回書く | 一部採用（Protocol は「フレーム → 候補集合」で切り、内部で戦略を使う） |
| パイプライン DSL / プラグイン機構 | 処理段を宣言的に組み替える | 実験の自由度が最大 | 現在の要件に対して明らかに過剰。`design-synthesis` の Simplification に反する | 不採用 |
| 逐次処理を全部 OpenCV の高水準 API に寄せる | 背景差分に `cv2.createBackgroundSubtractorMOG2` 等を使う | 実装量が小さい | **Depth 向けに作られていない**（8bit グレースケール前提）。パラメータがブラックボックスで、遅いときに削る場所が分からない | 不採用（`research` Decision 3） |

**採用パターンの整理**: 公開する契約は `Detector`（フレーム → 候補集合）という1段の Protocol とし、
その**既定実装が「マスク生成器 ＋ 共通後段」**という構造を持つ。
これにより、比較対象の3方式はマスク生成器として差し替わり、
将来 AI 方式を入れる場合は `Detector` の別実装として横に並ぶ。**抽象の階層は2段までに留める。**

---

## Design Decisions

### Decision 1: 座標系の境界を「`prediction_core` を import しない」ことで担保する

- **Context**: `.kiro/steering/roadmap.md` が「検出はカメラ座標系まで、World への変換は
  キャリブレーション側」と定め、`docs/requirements.md §6.2` が
  「座標系ずれは『予測が悪い』にしか見えない」と警告している
- **Alternatives Considered**:
  1. 規約として「World 変換を書かない」と決め、レビューで守る
  2. World 変換を注入可能にし、既定を恒等変換にする
  3. **`prediction_core` を import しない**（`Sample` を作れない状態にする）
- **Selected Approach**: 3。本パッケージは `prediction_core` に一切依存しない。
  出力は自前の `CameraTrack`（`frame == CoordinateFrame.CAMERA` を持つ）であり、
  `world-frame-calibration` がこれを受けて `prediction_core.Sample` を構成する
- **Rationale**: 規約は破れるが、**依存が無ければ書きようがない。**
  静的テスト（`test_boundaries.py`）で回帰検証でき、`prediction-core` /
  `sensing-foundation` が採った「依存方向を静的テストで守る」先例とも一致する
- **Trade-offs**: `world-frame-calibration` が完成するまで、本 Spec の出力を予測へ繋ぐ経路が無い。
  ただし本 Spec の検証は合成軌道の往復で完結するため、開発は止まらない
- **Follow-up**: `test_boundaries.py` に「`prediction_core` を import していない」検査を置く

### Decision 2: 検出方式の差は「前景マスクの作り方」だけに閉じる

- **Context**: OQ-26 を実測で決着させるには、方式間の比較が**後段の実装差で汚れない**必要がある
- **Alternatives Considered**:
  1. 方式ごとに独立した検出器を書く
  2. マスク生成のみ差し替え、モルフォロジ・連結成分・候補絞り込み・代表点算出を共通化する
- **Selected Approach**: 2
- **Rationale**: 候補の絞り込み条件（φ65mm 由来）と代表点の求め方は**方式に依存しない**。
  共通化すると「方式 A だけ絞り込みが緩い」といった交絡が構造的に起きなくなる。
  実装量も 1/3 になり、バグの数も 1/3 になる
- **Trade-offs**: マスクという中間表現に嵌らない方式（学習済み検出器）は
  `Detector` の別実装として書く必要がある。**現時点でその要件は無い**（要件 3.7）
- **Follow-up**: 比較実行時に「後段の設定が全方式で同一である」ことを結果に記録する

### Decision 3: 連結成分ラベリングのために OpenCV を採用する（Build vs Adopt）

- **Context**: マスクから候補領域を切り出すには連結成分ラベリングが要る
- **Alternatives Considered**:
  1. numpy だけで書く（Union-Find の自前実装、または `scipy.ndimage.label`）
  2. `cv2.connectedComponentsWithStats` を使う
  3. 輪郭抽出 `cv2.findContours` を使う
- **Selected Approach**: 2 を主とし、モルフォロジは `cv2.morphologyEx` を使う
- **Rationale**:
  - 自前の連結成分は**書きたくない類のコード**であり、Pi 4 上で Python ループになると
    レイテンシ要求に対して致命的に遅い
  - `scipy` を入れると依存が1つ増える。**OpenCV は `tech.md` が既に固定側の技術として挙げている**
  - `connectedComponentsWithStats` は面積・外接矩形・重心を**同時に返す**。
    候補の絞り込みに必要な情報がラベリング1回で揃い、`findContours` より無駄が少ない
- **Trade-offs**: OpenCV は依存として大きい。Pi 上の導入方法（apt / pip）が OQ-41 に影響する
- **Follow-up**: GUI 無しの構成を選ぶ。実機導入手段の実測結果を `measurements.md` へ記録する

### Decision 4: 候補の絞り込みを画素ではなく物理量で書く

- **Context**: 要件 3.3 / 12.5 が「対象物の想定寸法に基づく条件を設定値として与える」と要求している
- **Selected Approach**: 設定は `object_diameter_mm`（既定 65.0）と許容倍率
  `min_scale` / `max_scale` で与え、候補ごとに**その候補の実測距離から期待画素直径を導出**して判定する
- **Rationale**: 解像度・距離・カメラを変えても設定が意味を保つ。
  `tech.md` 開発標準1「根拠のない固定値を埋め込まない」に直接対応する
- **Trade-offs**: 距離が求まる前に絞り込めない。したがって
  **絞り込みは2段（距離非依存の粗いふるい → 距離依存の本判定）**になる
- **Follow-up**: 粗いふるいの閾値（最小画素数）も設定値にし、既定値の根拠を docstring に書く

### Decision 5: 追跡は「単一物体・最近傍ゲート」から始める

- **Context**: 要件 6 が 1投擲 = 1物体を前提とし、A-6 が「最初は最小限」と定めている
- **Alternatives Considered**:
  1. カルマンフィルタ ＋ ゲーティング
  2. 直前点からの3D距離ゲート ＋ 最近傍
  3. 予測位置（直近2点の等速外挿）からの距離ゲート ＋ 最近傍
- **Selected Approach**: 3。ただし点が1つしか無い間は 2 に縮退する
- **Rationale**:
  - 空き缶の飛翔は 1 秒級・単調であり、フレーム間の移動量は小さい。
    等速外挿だけで十分にゲートが効く
  - **カルマンフィルタは `prediction-core` が明示的に初期要件から外した選択肢**である
    （同 requirements.md「design フェーズで決めるもの」）。
    追跡側で先回りして導入すると、フィルタが二重になり誤差要因が増える
- **Trade-offs**: 高速で大きく動く物体や長い遮蔽には弱い。**実測して足りなければ拡張する**
- **Follow-up**: ゲート幅（`max_step_mm`）と欠測許容（`max_missing_frames`）を設定値にし、
  ゲートで落とした候補数を計測値として残す（要件 6.6 / 9.3）

### Decision 6: 実効サンプル数の指標名を上流と区別する

- **Context**: `sensing-foundation` が `effective_samples_per_window` を
  **フレーム層**の指標として既に使っている
- **Selected Approach**: 本 Spec の指標は `effective_points_per_window`（3D点の数）とする
- **Rationale**: 同じ語を別の対象に使うと、`m1-prediction-validation` が両者を集計するときに
  取り違える。**`structure.md`「同じ事実を2箇所に書かない」の運用上の系**である
- **Trade-offs**: 名前が長くなる。許容する

### Decision 7: 検出方式の選定を「設計で決めない」

- **Context**: OQ-26 は本 Spec が決着させる未決事項である
- **Selected Approach**: **設計段階では方式を選ばない。** 3方式を実装し、
  比較の実行手段と判定規則を先に確定させ、**実測結果をもって選定する**。
  選定結果と根拠は `measurements.md` に記録する
- **Rationale**: `tech.md` 開発標準1「未実測の数値を合否条件にしない」。
  設計者の予想（Depth 差分で足りるだろう）を決定として書き込むと、
  OQ-26 が「測って決めた」ことにならない
- **Trade-offs**: 実装完了時点では既定方式が暫定のままになる。
  既定値の docstring に「暫定であり実測で確定する」と明記して扱う
- **Follow-up**: 判定規則（下記）を実測前に確定させる

> **OQ-26 の判定規則（実測前に確定させる）**
>
> 同一の記録済みセッション群に対して3方式を実行し、次の順で比較する。
> 1. **`effective_points_per_window` が最大**の方式を第一候補とする
> 2. 差が各方式の四分位範囲以内なら**区別がついていない**とみなし、
>    `detect` 区間の処理時間 p95 が小さい方を採る
> 3. それでも並ぶ場合は、**設定パラメータが少ない方**を採る（調整の余地が少ない＝壊れにくい）
>
> **絶対値の目標を置かない。** 判定はすべて方式間の相対比較とばらつきで定義する。

---

## Risks & Mitigations

- **上流 `sensing-foundation` が未実装である** — 本 Spec は上流の**公開契約に対して**実装する。
  契約は同 design.md で確定しており、実装順は Wave 1 で解決される。
  結合前に動かすため、テスト用の最小 `FrameSource` 実装をテストツリーに置く
  （**本体には置かない**。上流のアダプタを二重化しないため）
- **区間2（0.10〜0.15 s）に収まらない可能性** — 収まらないこと自体は失敗ではない。
  `docs/development-environment.md §13.2` の改善順序（Color 削減 → 解像度 → **ROI** → fps →
  画像処理削減 → PointCloud 回避 → アルゴリズム簡略化）を先に尽くす。
  本 Spec は ROI と方式選択という2つの打ち手を設定として持つ
- **Depth の欠測（測距できない画素）が多く、3D点が出ない** — 欠測を 0 で埋めない（要件 10.6）。
  有効画素数を候補ごとに記録し、**「検出はできたが3D点にならなかった」を独立に数える**
- **手・腕による遮蔽でリリース直後が取れない** — `docs/requirements.md §3` 区間1 が
  「完全に未検証」と認めている領域。本 Spec は追跡開始時刻を記録し、
  `m1-prediction-validation` が区間1 を実測できる材料を渡す
- **合成データでの良好な結果を実機の結論と取り違える** — 出力に入力元種別を必ず含め、
  合成由来の比較結果には「実機の結論として扱わない」旨を明示する（要件 11.5）
- **OpenCV の導入が Pi 上で難航する** — GUI 無し構成を選び、apt / pip の双方を試す。
  結果を OQ-41 の判断材料として記録する。**依存表に書けない事態になった場合は
  `pyrealsense2` と同じ扱い（遅延 import ＋ 環境診断）へ退避できる**構造にしておく

---

## References

- `.kiro/specs/sensing-foundation/design.md` — 上流の公開契約（`CaptureFrame` / `FrameSource` /
  `StructuredLogger` / セッション記録形式）。**本 Spec が従う正**
- `.kiro/specs/prediction-core/requirements.md` / `design.md` — 下流の入力契約と、
  依存方向を静的テストで守る先例
- `docs/development-environment.md` §4（Pi 向け設計方針）/ §5.1（実効サンプル数）/
  §10（軽量方式の候補）/ §12（段階検証）/ §13.1（計測項目）/ §13.2（改善順序）
- `docs/requirements.md` FR-1（最低3サンプルとその根拠）/ §3（時間予算表）/
  §6.1（座標系と単位）/ §6.2（キャリブレーション手順の警告）/ §8 M1
- `docs/open-questions.md` OQ-02 / OQ-26 / OQ-40 / OQ-41
- `docs/decisions.md` D-8（Throw Record スキーマの確定）
- `.kiro/steering/tech.md` 開発標準 1 / 4 / 5 / 6、`.kiro/steering/structure.md` 命名規約
- `.kiro/steering/roadmap.md` Boundary Strategy（カメラ座標系までという境界の出典）
