# 投擲実験手順書: m1-prediction-validation

> 本書は `.kiro/specs/m1-prediction-validation/tasks.md` タスク 8.5（要件 12.6）の
> 成果物であり、**投擲実験を実施するたびに従う手順**を定める。
>
> 対象読者は実機での投擲実験の実施者であり、本書だけを読めば、
> キャリブレーション検証の確認から投擲・記録・持ち帰り・集計・レポートまでを
> 実行できる状態を目指す（tasks.md タスク 8.5「観測可能な完了状態」）。
> コマンドの正確な引数一覧は、本書の例に加えて必ず
> `python -m m1_validation.cli <サブコマンド> --help` でも確認すること
> （引数は今後 CLI 側の変更で増減しうるため、`--help` が常に一次情報である）。
>
> 実機を要するのは `run-throw` と `bench-overhead` の2サブコマンドだけである
> （`--help` の実機要否一覧を参照）。真値の取り込み以降（`ingest-truth` /
> `measure` / `attribute` / `judge-oq27` / `material-oq05` / `budget` /
> `report` / `plot`）は記録済みファイルだけで動き、実機を必要としない
> （要件 12.1, 12.5）。

## 0. 全体の流れ

```
1. 前提確認: world-frame-calibration の検証を通す（★ 必須。省略しない）
2. 投擲レイアウトの準備（layout.json、投擲位置を2箇所以上にする）
3. 投擲の実施（run-throw）と落下地点のメジャー実測
4. 記録の持ち帰り（実機 → 開発PC）と真値ファイル（truth.json）の作成
5. 実測7項目の算出（measure）・帰属（attribute）・判断（judge-oq27 等）
6. レポート（report）・可視化（plot）で結果を読む
7. 改善適用（development-environment.md §13.2）とハードウェア判断の順序
8. 再実施が必要な条件
```

**キャリブレーション検証（手順1）を経ていない状態での投擲は実施しない。**
`run-throw` は未検証のキャリブレーションでの実行を既定で拒否する
（手順1参照）。

---

## 1. 前提: キャリブレーション検証を先に通す（★ 必須）

`m1-prediction-validation` は `world-frame-calibration` が確立した World
座標系の**上に**実測を積む。座標系が数 cm ずれていても、症状は
「予測が悪い」としか見えない（`docs/requirements.md §6.2`）。検証を経ずに
投げると、**予測の誤差なのか座標系のずれなのかを一切切り分けられない**
状態で実験を始めることになり、後から取り返しがつかない。

### 1.1 確認手順

キャリブレーション結果ファイル（例: `var/calibration/my_result.json`）が
検証を通過していることを、`world-frame-calibration` の手順書
（`.kiro/specs/world-frame-calibration/procedure.md` 手順3.3「verify」）に
従って確認する:

```
python -m world_frame_calibration.cli verify \
    --plan plan.json \
    --calibration var/calibration/my_result.json \
    --source live \
    --tolerance-horizontal-mm 30.0 \
    --tolerance-vertical-mm 20.0 \
    --tolerance-source "暫定目標値（実測前）"
```

出力先頭の「判定」行が **合格 (pass)** であることを確認する。
「未判定 (not_judged)」「不合格 (fail)」の状態では投擲実験へ進まない。

### 1.2 `--allow-unverified` は実験には使わない

`run-throw` は `--allow-unverified` を明示的に与えない限り、未検証の
キャリブレーションでの実行を拒否する（要件 2.2）。この既定を外して
実験を進めてはならない——`--allow-unverified` を使った結果はレポートに
「誤差の帰属ができない」旨が明示され、実測7項目のうち誤差系の項目は
**実験の判断材料として使えない値**になる（帰属＝要件6が本 Spec の核であり、
座標系の疑いを晴らしていない誤差はどの原因にも割り当てられない）。

`--allow-unverified` を使ってよいのは、キャリブレーション未検証の状態で
配線・引数の疎通だけを確認するデバッグ目的に限る。実験結果として
記録・報告してはならない。

---

## 2. 投擲レイアウトの準備

### 2.1 レイアウトファイル

投擲レイアウトは `layout.json`（`ThrowLayout` の JSON 直列化。1ファイルに
複数のレイアウトを書き、実行時に `layout_id` で選ぶ）に記述する。
同梱の `.kiro/specs/m1-prediction-validation/layout.example.json` が
形式のサンプルであり、**すでに2箇所の投擲位置（`throw-a` / `throw-b`）を
含んでいる**。ただし全フィールドが**仮値**であり（各レイアウトの `notes`
にその旨が明記されている）、**実験の前に必ず実測値へ置き換えること**
（要件 13.9。レイアウトの確定自体は本 Spec の範囲外）。

書き換える主なフィールド:

| フィールド | 内容 |
|---|---|
| `release_position_world_mm` | 投擲位置（World 座標 mm）。分かる場合のみ |
| `release_height_mm` | リリース高さ（mm）。**正の値が必須**。リリース時刻の外挿の基準になる |
| `throw_direction_deg` | 投擲方向 |
| `standby_position_world_mm` | 移動体（ゴミ箱）の待機位置（World 座標 mm、水平2成分） |
| `object_diameter_mm` / `aperture_diameter_mm` | 対象物寸法・開口寸法。位置許容の**暫定目標値**（開口半径 − 対象寸法/2）の算出に使う |
| `camera_position_world_mm` | カメラの World 座標（分かる場合のみ） |
| `notes` | 仮値である旨、実測していない旨を書いておく（本 Spec のテストが「仮値」の記載を強制する） |

`standby_position_world_mm` は `world-frame-calibration` の原点マーカーの
物理位置（World frame procedure.md 手順6）とも一致させておくと、
待機位置とレイアウトの原点の対応が取りやすい。

### 2.2 投擲位置を2箇所以上にする（帰属の判別可能性）

**誤差の帰属（要件6。本 Spec の核）は、共通の偏りが World 座標系に固定
されているのか、カメラ視線方向に沿っているのかを区別することで原因を
切り分ける。** 投擲位置を1箇所に固定すると、World 固定方向とカメラ視線
方向が**縮退して区別できなくなる**（`research.md` Decision 4）——この状態は
`attribute` サブコマンドが「判別不能」として正直に報告する（要件 6.10。
無理に1つの原因へ割り当てない）が、判別不能では帰属という本 Spec の
存在意義を発揮できない。

**したがって投擲位置は2箇所以上を選択肢として用意し、実際に2箇所以上で
投げることを推奨する。** `layout.example.json` の `throw-a` / `throw-b` は
投擲方向を90度変えた実例であり、そのままレイアウトの骨組みとして使ってよい
（座標値は実測に置き換えること）。

---

## 3. 投擲の実施と落下地点のメジャー実測

### 3.1 run-throw の実行

実機（Raspberry Pi 4 / RealSense D435）上で1投擲ごとに実行する:

```
python -m m1_validation.cli run-throw \
    --layout-file layout.json \
    --layout-id throw-a \
    --calibration var/calibration/my_result.json \
    --record-id throw-0001 \
    --records var/m1/throws.ndjson \
    --output-root var/m1/out \
    --session-id 2026-08-30-session1 \
    --runtime-set source=live \
    --runtime-set logging_path=var/m1/logs
```

- `--record-id` は投擲ごとに一意にする（例: `throw-0001`, `throw-0002`, ...）。
  重複すると記録の対応付けが取れなくなる
- `--records` は追記先（NDJSON）。同一セッション内の投擲はすべて同じ
  ファイルへ追記してよい
- `--session-id` は出力ファイル名とログの対応付けに使われる。1回の実験
  セッション（同一設置・同一設定でまとめて投げる単位）ごとに固定する
- **構造化ログの出力先は `run-throw` 自身の引数ではなく、上流
  （`sensing-foundation`）の設定 `logging_path` として指定する**
  （`--runtime-set logging_path=<ディレクトリ>`）。上流は
  `<logging_path>/<session-id>.ndjson` へ書き出す——手順5以降の
  `--log` にはこのパスをそのまま指定する
- 他に上流（`sensing-foundation` / `flying-object-tracking`）の設定を
  変える必要があれば `--runtime-set KEY=VALUE` / `--tracking-set KEY=VALUE`
  を使う。**本 Spec は上流の設定キーを列挙しない**——キー名は上流の
  `--help` / ドキュメントで確認すること
- 終了コード: `0`=成功、`1`=実行時の失敗（継ぎ目の不成立・投擲の不成立）、
  `2`=設定・入力の誤り（実行開始前に拒否される）

投擲が**観測不成立**（追跡が1件もサンプルを得られなかった等）で終わった
場合も、そこまでに得たサンプルは記録に残り、失敗理由とともに集計側で
「除外はするが数える」扱いになる（要件 3.8）。人為的に記録を消さないこと。

### 3.2 落下地点のメジャー実測

真値ファイル（手順4.2）へ書く `impact_point_world_mm` は、**この手順で
実測した値**である。本 Spec は落下地点を算出しない（`truth.py`「求め方が
異なる」の1つ目）。

**測り方**:

1. 基準点は `world-frame-calibration` の原点マーカー（World 原点）とする。
   原点マーカーは設置後もキャリブレーション実施時と同じ位置に残して
   おくこと（`world-frame-calibration/procedure.md` 手順6）
2. メジャー（巻尺・レーザー距離計等）で、原点マーカーから落下地点までの
   World 座標系での位置（x, y。床上の点なら z = 0）を実測する
3. **対象物が1回で静止しなかった場合（跳ねた・転がった）は、初弾の接地
   位置を記録する。** これは `derive_truth()` が落下時刻を「最初の
   降下方向の交差」から求める規約（要件 4.2）と対応を取るためであり、
   対応を崩すと落下地点と落下時刻が別の事象を指すことになる
4. 使った器具・手順を `impact_point_source` にそのまま書けるよう、その場で
   メモしておく（例: 「巻尺実測。原点マーカー中心から床上を計測」）
5. 測定のばらつき目安（mm）を見積もっておく（`impact_point_uncertainty_mm`）。
   メジャーの読み取り精度・対象物の寸法（点ではない）を考慮する

**測り方の記述を必ず残す理由**: 予測誤差を「予測が◯◯mm外れた」と報告
しても、実際の落下地点をどう測ったか（±5mm で測ったのか ±100mm で
測ったのか）が書かれていなければ、その誤差が予測由来なのか測り方由来
なのかを誰も判定できない（`truth.py` モジュール docstring）。`truth.json`
は測り方の記述と不確かさが無い記入を**構造的に拒否する**
（`M1ConfigError`）——後から埋め合わせできないので、投擲直後にその場で
書き取ること。

### 3.3 試行数の下限と、単発の結果で判断しない理由

**投擲はばらつきが大きく再現性が低い**（`tech.md` Testing「実機で毎回
投げ直して比較する方式は成立しない」）。1回の投擲結果だけを見て
「予測が悪い／良い」と判断してはならない。

本 Spec の既定の下限は **有効投擲数 20・セッション数 2**
（`TrialLimits` の既定値。`--min-valid-throws` / `--min-sessions` で
上書きできる）である。下限に満たない集計は**暫定として印が付き**、
判断に用いてよい状態としては扱われない（要件 5.10）。**この下限も暫定
評価候補であり、必須性能ではない**——実測しながら見直してよい。

---

## 4. 記録の持ち帰りと真値ファイルの作成

### 4.1 記録の持ち帰り

`tech.md`「Testing」の運用（WSL で開発 → Git push → Pi で pull → 実機
テスト・実データ記録 → WSL へ持ち帰り解析）に従う。実機（Pi）から
開発 PC（WSL）へ持ち帰るのは次の3種:

- 投擲記録（`--records` で指定した NDJSON。`Throw Record` の系列）
- 構造化ログ（`--log` で指定した NDJSON。段階別レイテンシの出所）
- （記録・再生を使う場合）`sensing-foundation` のセッション記録一式

以降のサブコマンド（`ingest-truth` 以降）はこれらのファイルだけで動く。
**実機や SDK が無い開発 PC 上でも、`--records` / `--truth` / `--log` の
パスを指すだけで同じ集計・判断が再現できる**（要件 12.5）。

### 4.2 真値ファイル（truth.json）の作成

持ち帰った記録の `record_id` と対応付けて、手順3.2 で実測した落下地点を
`truth.json` へ書く:

```json
{
  "truth_format_version": "1.0",
  "layout_id": "throw-a",
  "entries": {
    "throw-0001": {
      "impact_point_world_mm": [1240.0, -310.0, 0.0],
      "impact_point_source": "メジャー実測。原点マーカー中心から床上を計測",
      "impact_point_uncertainty_mm": 15.0,
      "notes": "缶が1回バウンドした。初弾接地位置を記録"
    }
  }
}
```

- `layout_id` は手順2で使ったレイアウトの識別子と一致させる。**別レイアウト
  の真値を混ぜると誤差の帰属が壊れる**（投擲位置ごとに偏りの向きが変わる
  ことを使って原因を切り分けているため）
- 落下地点をまだ測っていない投擲は、記入自体を省略してよい（欠測として
  扱われる。要件 4.7）。**0 で埋めない**——測っていないことと差が0だった
  ことは別である
- 記録に存在しない `record_id`（綴り間違い等）は `ingest-truth` 実行時に
  警告として報告される（黙って捨てない）。実行のたびに `unknown_record_ids`
  を確認すること

```
python -m m1_validation.cli ingest-truth \
    --records var/m1/throws.ndjson \
    --truth truth.json \
    --out var/m1/throws-with-truth.ndjson \
    --layout-file layout.json --layout-id throw-a --output-root var/m1/out
```

---

## 5. 実測7項目の算出・帰属・判断

以降はすべて実機なしで実行できる。`--records` / `--truth` / `--log` /
`--layout-file` / `--layout-id` / `--output-root` は共通で指定する。

```
python -m m1_validation.cli measure \
    --layout-file layout.json --layout-id throw-a \
    --records var/m1/throws.ndjson --truth truth.json \
    --log var/m1/logs/2026-08-30-session1.ndjson \
    --output-root var/m1/out
```

`docs/requirements.md §8 M1` の実測7項目（総飛行時間・リリースから検出
開始・検出開始から初回予測・落下地点誤差・落下時刻誤差・狙い誤差・
有効サンプル数と収束サンプル数）が、代表値・ばらつき・試行数とともに
返る。欠測は `null` のままであり、0 で埋められない。

続けて帰属と判断を実行する:

```
python -m m1_validation.cli attribute   --layout-file layout.json --layout-id throw-a --records var/m1/throws.ndjson --truth truth.json --log var/m1/logs/2026-08-30-session1.ndjson --output-root var/m1/out
python -m m1_validation.cli judge-oq27  --layout-file layout.json --layout-id throw-a --records var/m1/throws.ndjson --truth truth.json --log var/m1/logs/2026-08-30-session1.ndjson --output-root var/m1/out
python -m m1_validation.cli material-oq05 --layout-file layout.json --layout-id throw-a --records var/m1/throws.ndjson --truth truth.json --log var/m1/logs/2026-08-30-session1.ndjson --output-root var/m1/out
python -m m1_validation.cli budget      --layout-file layout.json --layout-id throw-a --records var/m1/throws.ndjson --truth truth.json --log var/m1/logs/2026-08-30-session1.ndjson --output-root var/m1/out
```

- `attribute` は誤差を**成分ごとの内訳**（バイアス・ばらつき・距離帯別・
  判定）として返す。単一の合計誤差は出さない
- `judge-oq27` は OQ-27（Raspberry Pi 4 継続可否）の判定規則を実測前に
  固定した4値（`continue` / `continue_with_constraints` / `insufficient` /
  `deferred`）で返す。GATE 0〜2（改善未適用・試行数不足・実機由来投擲なし）
  のいずれかに該当すれば `insufficient` を出さない（手順7参照）
- `material-oq05` は決着させない。判断材料の提示に留まる
- `budget` は実測7項目が揃っていない限り**時間予算表の更新値を出さない**
  （`ready: false` と不足項目が返る）

---

## 6. レポート・可視化で結果を読む

```
python -m m1_validation.cli report --layout-file layout.json --layout-id throw-a --records var/m1/throws.ndjson --truth truth.json --log var/m1/logs/2026-08-30-session1.ndjson --output-root var/m1/out
python -m m1_validation.cli plot   --layout-file layout.json --layout-id throw-a --records var/m1/throws.ndjson --truth truth.json --output-root var/m1/out
```

- `report` は**キャリブレーション識別子×検証状態のグループごとに1ファイル**
  （`report-<session-id>-<calibration-id>-<verified|unverified>.json`）を
  `--output-root` へ書き出す。検証未通過のグループは帰属が使えない旨が
  明示される
- `plot` は描画ライブラリ（`matplotlib`。extras `m1-viz`）が導入されていない
  環境では**可視化だけ**が利用不可として報告され、他のサブコマンドの経路は
  止まらない（要件 8.9）。導入するには
  `uv sync --extra m1-viz`（または相当のインストール手段）を使う

---

## 7. 改善適用の順序と、ハードウェア判断の前に適用し切る理由

**性能不足に見えても、すぐにハードウェア（Pi 4 以外）へ切り替えない。**
`development-environment.md §13.2` が定める8段階を、**この順序で**先に
検討・適用する:

1. Color stream 削減
2. Resolution 調整
3. ROI 縮小
4. FPS 調整
5. 不要な画像処理削減
6. Point Cloud 全生成を避ける
7. 検出アルゴリズム簡略化
8. ソフトウェア最適化

`judge-oq27` はこの順序を**構造として強制する**: 8段階のうち未適用の
ものが残っている間は `insufficient`（Pi 4 では不足という結論）を出さない
（GATE 0。`tech.md` 開発標準4）。適用済み項目と適用前後の計測値を証跡
として `measurements.md` へ記録すること。

**この順序を守る理由**: ハードウェア変更は後戻りしにくく、コストも
大きい。設定・ソフトウェアで詰め切る前に「Pi 4 では無理だ」と結論すると、
実は ROI を絞るだけで足りていた可能性を潰したまま高価な変更へ進んで
しまう。

改善を適用したら、同じレイアウト・同じキャリブレーションで再度投擲実験
（手順3〜6）を行い、適用前後の計測値を比較する。

---

## 8. 再実施が必要な条件

次のいずれかが発生した場合、**当該部分から手順を再実施する**こと。
古い記録・古いキャリブレーションを使い続けると、条件が変わったことに
誰も気付けないまま実験が進む:

- **カメラが移動した**: `world-frame-calibration` の設置手順書に従い
  キャリブレーションを再確立・再検証してから投擲を再開する（手順1から）
- **解像度・fps を変更した**: カメラ内部パラメータが変わるため、既存の
  キャリブレーション結果は現在の入力元との整合性検査
  （`check_compatibility`）で不一致として検出される。再キャリブレーション
  してから投擲を再開する
- **投擲レイアウトを変更した**（投擲位置・待機位置・対象物を変えた）:
  新しい `layout_id` を発行し、**古いレイアウトの真値と混在させない**。
  過去の投擲記録は古い `layout_id` のまま集計対象として残ってよいが、
  新しいレイアウトでの投擲は別の投擲群として扱われる
- **キャリブレーションを入れ替えた**（別の設置・別の結果ファイルに
  切り替えた）: 新しいキャリブレーションが検証を通過していることを
  手順1で確認してから投擲を再開する。`report` は
  キャリブレーション識別子×検証状態ごとに集計を分けるため、入れ替え
  前後の記録が誤って同じ集計に混ざることは無いが、**未検証のまま投げ
  続けない**こと

---

## 参考: 各コマンドの `--help`

いずれのサブコマンドも `--help` に**実機の要否**（要／不要／要（推奨））
を明示する（要件 12.6）。設定の解決順序（実行時指定 > 環境変数 `STB_M1_*`
> 設定ファイル > 既定値）と解決結果は `--print-settings` で確認できる。
既定値はすべて暫定の評価候補であり、必須性能ではない
（`--help` の注記をそのまま参照すること）。
