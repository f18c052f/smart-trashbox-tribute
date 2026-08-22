# 実機計測記録: world-frame-calibration

> 本書は `design.md` Modified Files が定める記録先である:
>
> > `.kiro/specs/world-frame-calibration/measurements.md` — **新規**。実機での
> > 推定品質・検証誤差・再現性・許容値見直しの**結論**を記録する（生データは
> > `var/` 配下で版管理しない）。
>
> **現時点（タスク 7.4 完了時点）では、実機（Raspberry Pi 4 / RealSense
> D435）はまだセットアップされておらず、`tasks.md` タスク 8.1〜8.3
> （ハードウェア必須）は未着手である。** したがって本書は実測値を記録する
> ための**テンプレート（雛形）**であり、この時点では数値の記入はしない。
> 各節は、対応するタスクが完了した時点で、実施した設置者がその実測結果を
> 追記する場所として用意している。
>
> 記録する数値は、`procedure.md` の手順に従って `calibrate` / `verify` /
> `compare` を実機で実行して得られたものに限る。**未実測の値を、実測値
> であるかのように記入しないこと**（`tech.md` 開発標準1: 未実測の数値を
> 合否条件にしない、との一貫性のため）。生の JSON 出力（`var/calibration/`
> 配下）そのものは版管理対象外であり、本書に記すのは**結論**（数値の要約と
> そこから導いた判断）である。

---

## 1. 床平面推定と World frame 確立（タスク 8.1 対応）

> 記入対象: `procedure.md` 手順3.1（`calibrate`）を実機の live 入力で実行した
> 結果。要件 1.1, 1.2, 1.6, 2.7, 2.8, 8.1。

### 1.1 採用した計画（plan.json）

- 使用した plan ファイル名・設置日時:
- `floor_region` / `origin_anchor` / `x_axis_anchor` の概要（画素範囲・
  高さ帯）:
- 実測した基線長（メジャー実測。`expected_baseline_mm` に対応）:

### 1.2 推定品質

`calibrate` 実行後の `show` 出力、または保存済み `CalibrationResult.plane.quality`
から転記する。

| 項目 | 値 |
|---|---|
| 有効点数（`points_considered`） | |
| 内点数（`inlier_count`） | |
| 内点率（`inlier_ratio`） | |
| 残差 中央値 / 95パーセンタイル / RMS（mm） | |
| 使用フレーム数（`frames_used`） | |
| 入射角（`incidence_angle_deg`） | |

### 1.3 確立結果の幾何

| 項目 | 値 |
|---|---|
| 算出基線長（`geometry.baseline_mm`） | |
| ヨー感度（`yaw_sensitivity_deg_per_mm`） | |
| 距離1000mmでの横方向誤差係数（`lateral_error_mm_per_mm_at_1000mm`） | |

### 1.4 床探索範囲に関する知見

- 壁・天井が範囲に入って誤った平面を掴んだ事象の有無:
- 上記を踏まえた `floor_region` の取り方の見直し（あれば `procedure.md`
  §1.3 へ反映する）:

### 1.5 収集枚数（`--frames`）を変えたときの推定品質の変化

| `--frames` | 内点率 | 残差RMS(mm) | 備考 |
|---|---|---|---|
| | | | |
| | | | |

---

## 2. 独立検証の実施と誤差の記録（タスク 8.2 対応）

> 記入対象: `procedure.md` 手順3.3（`verify`）を実機で実行した結果。
> 検証点は3点以上、うち1点は想定投擲距離の端に配置すること
> （`procedure.md` §1.4）。要件 4.2, 4.4, 4.5, 4.10, 8.5。

### 2.1 検証点の配置

| ラベル | truth_world_mm | truth_source | カメラからの距離目安 |
|---|---|---|---|
| | | | |
| | | | |
| | | | |

### 2.2 検証レポートの要約

| 項目 | 値 |
|---|---|
| 判定（`verdict`） | |
| 独立点数 / 全点数 | |
| バイアス（x, y, z mm） | |
| ばらつき（残差RMS, mm） | |
| 最大誤差（全体 / 水平 / 垂直, mm） | |

### 2.3 距離帯ごとの誤差

| 距離帯（mm） | 点数 | 平均誤差(mm) | 最大誤差(mm) |
|---|---|---|---|
| | | | |

### 2.4 スケール確認

| 項目 | 値 |
|---|---|
| 実測基線長（`expected_baseline_mm`） | |
| 算出基線長（`measured_baseline_mm`） | |
| 差分（`difference_mm`） | |

### 2.5 読み分けの所見

`procedure.md` §4.1 の読み分け規則（バイアス支配なら座標系、ばらつき
支配なら観測、遠方だけ大きいなら Depth の距離特性）に照らして、この
実測結果から何が読み取れるかを記す:

-

### 2.6 実機セッションの WSL 再生確認

実機で記録したセッションを、実機なしの環境（WSL 等）で再生して同じ検証
を実行し、主要値が一致することを確認した記録（要件 8.5）。

- 再生に用いたセッション:
- 実機実行時と再生実行時の主要値の一致状況:

---

## 3. 再現性の実測と暫定値の見直し（タスク 8.3 対応）

> 記入対象: `procedure.md` §5（再現性の確認）を複数回実施した結果と、
> それに基づく `PlanLimits` / `ToleranceSpec` の暫定値の見直し。要件 4.6,
> 7.4, 10.6。
>
> ⚠️ 実測前の暫定値を根拠に方針を変えないこと。見直しは実測後にのみ行う
> （`tech.md` 開発標準1）。

### 3.1 再現性の実測（`compare` の結果）

**マーカーを置いたまま複数回 `calibrate` を実施した場合**（観測のばらつき
のみを見る）:

| 試行の組 | 原点のずれ(mm) | 全体回転角(deg) | Z軸のずれ(deg) | ヨーのずれ(deg) |
|---|---|---|---|---|
| | | | | |

**マーカーを置き直して複数回実施した場合**（設置作業そのものの再現性を見る）:

| 試行の組 | 原点のずれ(mm) | 全体回転角(deg) | Z軸のずれ(deg) | ヨーのずれ(deg) |
|---|---|---|---|---|
| | | | | |

### 3.2 暫定値の見直し

実測結果に照らして、以下の暫定値を見直した場合はその結論と実測根拠を記す。
見直さない場合も「見直さない」という判断とその理由を記録する。

| 対象 | 現在の値（暫定） | 見直し後の値 | 実測根拠 |
|---|---|---|---|
| `PlanLimits.min_baseline_mm`（既定 800.0mm） | | | |
| `PlanLimits.min_inlier_points`（既定 2000） | | | |
| `PlanLimits.min_inlier_ratio`（既定 0.5） | | | |
| `PlanLimits.min_incidence_angle_deg`（既定 10.0deg） | | | |
| `ToleranceSpec.horizontal_mm` | | | |
| `ToleranceSpec.vertical_mm` | | | |

見直した値を `provisional=False`（実測由来）として計画ファイルの
`tolerance` に反映した場合は、その旨と反映先のファイルを記す:

-

### 3.3 m1-prediction-validation への通知

`design.md` Revalidation Triggers が定めるとおり、**許容値
（`ToleranceSpec`）の更新は境界を越えた Revalidation Trigger である**。
`m1-prediction-validation` の `seam.open_calibration()` は非 PASS の結果に
対して `SeamFailure(CALIBRATION_NOT_VERIFIED)` を送出し M1 の計測実行を
止めるため、許容値を締める／緩めるいずれの変更であっても、向こうの実行
可否が変わらないことを確認し、通知した記録をここに残す。

- 通知日時・方法:
- 通知内容（変更前後の許容値、変更理由）:
- `m1-prediction-validation` 側の確認結果:

（許容値を変更しなかった場合は本節は「変更なし」とだけ記す。）
