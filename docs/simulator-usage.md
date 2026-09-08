# 軌道シミュレータ 実行手順

> 本書は**軌道シミュレータ（`trajectory_sim`）とその結果ビューア（`viz/`）を実行する手順の正**である。
>
> 対象読者は、実機もハードウェアも持たない状態で「投げたゴミをゴミ箱が受け止められる条件」を
> 机上で検討したい人である。本書だけを読めば、セットアップからサンプル実行、
> 自分の条件での実行、図の読み方までを一通り行える状態を目指す。
>
> **本書はハードウェアを一切必要としない。** RealSense も Raspberry Pi も ESP32 も使わない。
> 開発PC（Windows + WSL2）だけで完結する。
>
> 部品の型番は [bom.md](./bom.md)、要件は [requirements.md](./requirements.md)、
> 未決事項は [open-questions.md](./open-questions.md)、環境の方針は
> [development-environment.md](./development-environment.md) が正である。本書はそれらを複製しない。

---

## 0. 全体の流れ

```
  設定ファイル(JSON) ──▶ trajectory_sim ──▶ 掃引結果(JSON) ──▶ viz(ブラウザ) ──▶ 図
   条件を書く            WSL の Python       結果の数値が並ぶ      開発PC のブラウザ    人が読む
```

2段構えになっている。

1. **掃引実行**（Python / WSL 側）: 条件を総当たりでシミュレーションし、結果を JSON に書き出す
2. **ビューア**（TypeScript / ブラウザ）: その JSON を読み込んで図にする

両者はファイル（JSON）だけで繋がっている。**常駐サーバも通信も無い。**
ビューアは計算を一切行わず、JSON に書かれている値を描くだけである。

---

## 1. 用語

本書と画面に出てくる言葉を先に説明する。

### 掃引（sweep）

パラメータを1点だけ試すのではなく、**格子状に振って総当たりで試すこと**。

たとえば「持ち時間」を 8 通り、「必要移動量」を 9 通り振ると、組み合わせは 8 × 9 = **72 通り**になる。
掃引実行はこの 72 通りすべてをシミュレーションする。

### 軸（axis）と格子点（cell）

- **軸** = 振るパラメータ1つ。上の例では「持ち時間」と「必要移動量」の2軸
- **格子点** = 軸の値の組み合わせ1つ。上の例では 72 個できる

格子点1つにつき1つの結果（受け止められたかどうか）が出る。

### 状態（status）

格子点ごとに、上流のシミュレータが次の3つのいずれかを付ける。

| 値 | 意味 |
|---|---|
| `catchable` | その条件ではゴミ箱が間に合った |
| `not_catchable` | 間に合わなかった |
| `not_evaluated` | 評価できなかった（床に落ちる軌道でない、観測サンプルが足りない等） |

### キャッチ可能領域

格子点を軸に沿って並べ、状態で色分けした図。**ビューアの主たる出力**である。
「どこまでなら間に合うか」の境界が、数値の列ではなく図として見える。

### 代表記録（Throw Record）

掃引の設定で `keep_representative_record: true` にすると、代表的な1投擲について
**観測サンプル列と予測系列**が結果 JSON に残る。ビューアはこれを軌跡アニメーションとして再生する。
`false` の場合は記録が残らず、アニメーション面だけが利用不可になる（図は通常どおり出る）。

---

## 2. 前提環境

| 何を | どこで | 理由 |
|---|---|---|
| 掃引実行（Python） | **WSL2 側** | Python / uv は WSL 側に入っている |
| ビューア（Node / ブラウザ） | **開発PC 側** | ブラウザで見るため |

> **Windows 側のシェルから `python` を叩かないこと。** Microsoft Store のスタブが応答し、
> 実行できない。掃引実行は必ず WSL 側で行う（`wsl -e bash -lc '...'` か WSL のシェル内）。

必要なもの:

- WSL2 + [uv](https://github.com/astral-sh/uv)（Python のパッケージ管理）
- Node.js 20 以上（ビューアのビルド用。開発時依存は TypeScript コンパイラ 1 個だけ）

---

## 3. セットアップ

### 3.1 Python 側（掃引実行）

WSL のシェルでリポジトリのルートに入り、次を実行する。

```bash
uv sync --all-extras
```

> ⚠ **`--all-extras` を省かないこと。** 素の `uv sync` は基本依存しか入れないため、
> `numpy` / `opencv-python-headless` を使う他パッケージのテストが収集エラーになる
> （46 件のエラーになる）。掃引実行そのものは素の `uv sync` でも動くが、
> テストを回すなら `--all-extras` が要る。

### 3.2 ビューア側（表示）

```bash
cd viz
npm install
npm run build
```

`npm run build` は TypeScript を `viz/dist/` へコンパイルする。**ビューアを開く前に必ず実行する。**

---

## 4. サンプルデータで動かす（最短手順）

リポジトリには実行可能な設定ファイルが同梱されている。まずはこれで動かすのが早い。

### 4.1 掃引を実行して結果 JSON を作る

WSL 側で、リポジトリのルートから:

```bash
mkdir -p var/sweep
uv run python -m trajectory_sim \
  --config configs/trajectory_sim/sweep-reachability.json \
  --drivetrain configs/trajectory_sim/drivetrain-wheel60.json \
  --output var/sweep/reachability-wheel60.json
```

成功すると次のように出る。

```
trajectory_sim: 72 格子点を var/sweep/reachability-wheel60.json へ書き出した
```

> `var/` は版管理の対象外（`.gitignore` 済み）なので、結果の置き場として使ってよい。

### 4.2 ビューアで開く

ビューアは静的ファイルの配信だけで動く。WSL 側で `viz/` に入り:

```bash
cd viz
python3 -m http.server 8000
```

開発PC のブラウザで **http://localhost:8000/** を開く。

画面の「ファイルを選択」で、さきほど作った `var/sweep/reachability-wheel60.json` を選ぶ。
較正バナー・キャッチ可能領域の図・前提と限界・パラメータ表が表示される。

> **通信でファイルを取りに行く作りにはなっていない。** 選んだファイルをブラウザが直接読む。
> そのため配信するのは `viz/` だけでよく、結果 JSON を配信下に置く必要はない。

### 4.3 軌跡アニメーションつきで動かす

`sweep-reachability.json` は代表記録を残さない設定なので、アニメーション面は利用不可と表示される。
アニメーションも見たい場合は、記録を残す設定の `sweep-layout.json` を使う。

```bash
uv run python -m trajectory_sim \
  --config configs/trajectory_sim/sweep-layout.json \
  --drivetrain configs/trajectory_sim/drivetrain-wheel60.json \
  --output var/sweep/layout-wheel60.json
```

これを開くと、床面を真上から見た投影（xy）と、水平方向と高さの投影（xz）の2面が表示され、
再生操作が使えるようになる。

### 4.4 同梱されている設定ファイル

| ファイル | 掃引の種類 | 軸 | 格子点数 | 代表記録 |
|---|---|---|---|---|
| `configs/trajectory_sim/sweep-reachability.json` | `reachability` | 持ち時間 × 必要移動量 | 8 × 9 = 72 | 残さない |
| `configs/trajectory_sim/sweep-layout.json` | `throw` | ゴミ箱の待機位置 × 投擲方位 × 受け止め方針 | 4 × 3 × 2 = 24 | 残す |

機体パラメータは別ファイルに分かれている。

| ファイル | 内容 |
|---|---|
| `configs/trajectory_sim/drivetrain-wheel60.json` | オムニホイール径 60mm |
| `configs/trajectory_sim/drivetrain-wheel48.json` | オムニホイール径 48mm |

この2つは**ホイール径だけが違う**。ホイールの調達が未確定なため、両方で結果を出して
比べられるようにしてある（→ [open-questions.md](./open-questions.md)）。
`--drivetrain` を差し替えるだけで比較できる。

---

## 5. 自分の条件で実行する（入力データの用意）

### 5.1 設定ファイルの構造

`--config` に渡す JSON は、最上位に **`parameters` と `sweep` の2キーだけ**を持つ。
それ以外のキーがあるとパラメータ不正として拒否される。

```jsonc
{
  "parameters": {          // 掃引で振らない、固定の条件
    "throw":       { "release_z_mm": 1500.0, "speed_mm_s": 4000.0, "elevation_deg": 45.0, ... },
    "observation": { "sample_period_ms": 33.0, ... },
    "catch":       { "policy": "stop_and_wait" },
    "layout":      { "home_x_mm": 0.0, "home_y_mm": 0.0 },
    "dispersion":  {},
    "prediction":  {},
    "calibration_stage": "uncalibrated",
    "provenance":  { "throw.speed_mm_s": "assumed", ... }
  },
  "sweep": {               // 何を、どう振るか
    "kind": "reachability",
    "axes": [
      { "name": "hold_time_ms", "unit": "ms", "values": [300.0, 500.0, 700.0] }
    ],
    "trials_per_cell": 1,
    "seed": 0,
    "catch_ratio_threshold": null,
    "keep_representative_record": false
  }
}
```

いちから書くより、**同梱の設定ファイルをコピーして値を変える**のが確実である。

```bash
cp configs/trajectory_sim/sweep-reachability.json var/my-sweep.json
# var/my-sweep.json を編集してから実行する
```

### 5.2 よく触る項目

| 項目 | 意味 |
|---|---|
| `sweep.axes[].name` | 振るパラメータ名。`layout.home_x_mm` のようにドット区切りで `parameters` の項目を指せる |
| `sweep.axes[].values` | 振る値の並び。**この個数の積が格子点数になる**（増やすと実行時間も増える） |
| `sweep.trials_per_cell` | 格子点1つあたりの試行回数。ばらつき（`dispersion`）を入れたときに意味を持つ |
| `sweep.catch_ratio_threshold` | 成立と見なす割合の閾値。試行1回の掃引では `null` にする |
| `sweep.keep_representative_record` | `true` で代表記録を残す（軌跡アニメーションが見られるようになる） |
| `parameters.provenance` | 各パラメータの出所（`measured` = 実測 / `assumed` = 想定）。**書かなかった項目は「記載なし」として表示される** |
| `parameters.calibration_stage` | 較正段階（`uncalibrated` / `m1_calibrated` / `m2_calibrated`） |

> `provenance` と `calibration_stage` は**図の信用度を示すために表示される**。
> 省略しても実行はできるが、図を見た人が前提を判断できなくなる。

### 5.3 引数の一次情報

引数は今後変わりうる。正確な一覧は必ず `--help` で確認する。

```bash
uv run python -m trajectory_sim --help
```

---

## 6. ビューアの操作

| 操作 | 何ができるか |
|---|---|
| **ファイルを選択** | 掃引結果 JSON を読み込む。別のファイルを選べば差し替わる |
| **X軸 / Y軸** | 描画に使う2軸を選ぶ。軸が3本以上ある掃引で意味を持つ |
| **固定軸の値** | 描画に使わない残りの軸を、どの値で切り取るか選ぶ |
| **記録** | 再生する代表記録を選ぶ（記録が含まれる掃引でのみ表示される） |
| **再生 / 停止 / 先頭へ** | 軌跡アニメーションの再生操作 |
| **スライダー** | 任意の時刻へ移動する |
| **格子点にカーソルを合わせる** | 軸の値・状態・成立割合・指標が出る |

補足:

- 軸を切り替えても、**較正段階とモデル除外要因の表示は消えない**（前提を見失わないため）
- 操作内容は保存されない。再読み込みすると初期状態に戻る
- パラメータを画面で編集して結果を作り変えることはできない。条件を変えるときは設定ファイルを変えて掃引を実行し直す

---

## 7. 図の読み方（ここが一番重要）

**シミュレータは、入れた前提の分しか返さない。** 楽観的な値を入れれば楽観的な図が出る。
図だけを切り出して見て結論を出すと、最も痛い誤解になる。ビューアは前提を図と同じ画面に出しており、
以下は必ず図とセットで読む。

### 7.1 較正段階（画面上部のバナー）

`uncalibrated`（未較正）と出ている場合、その数値は**感度分析用であって絶対値として信用してはならない**。
注意書きも併せて表示される。

### 7.2 モデル除外要因

シミュレータの物理モデルに**含まれていない**要因が、段ごとに全項目表示される。
現在は 4 段 12 要因ある（空気抵抗・回転・跳ね返り・センサ歪み・視野外・遮蔽・
タイムスタンプ揺らぎ・スリップ・方向依存性能・逆運動学・速度制御動特性・跳ね出し）。
**ここに挙がっている要因は、図に反映されていない。**

### 7.3 パラメータの出所

パラメータ表の右列に、各値が `measured`（実測）か `assumed`（想定）か、あるいは
**「出所の記載なし」**かが出る。「記載なし」を「実測済み」と読み替えないこと。
表示側は記載が無いことを、記載が無いまま表示する。

### 7.4 色分けは合否ではない

格子点の色の濃淡（成立割合の帯）は**表示上の取り決め**であり、合否の条件ではない。
上流の判定閾値（`catch_ratio_threshold`）とは別物で、凡例では両者が区別して示される。
画面のどこにも「合格」「達成」といった断定的な表現は出ない。これは意図的な設計である。

---

## 8. テストの実行

| 対象 | コマンド | 備考 |
|---|---|---|
| Python 側 | `uv run pytest -q` | ルートで実行。`uv sync --all-extras` 済みが前提 |
| ビューア側 | `cd viz && npm test` | `tsc` でのビルドを兼ねる |

ビューア側のテストには、**ブラウザ側にアルゴリズムを持ち込んでいないことの静的検査**（10 規則）が
含まれている。物理・予測の再実装、外部通信、永続化、重力定数の埋め込みなどが混入すると失敗する。

---

## 9. よくあるつまずき

| 症状 | 原因と対処 |
|---|---|
| `python` を叩くと `Python` とだけ出て何も起きない | Windows 側の Store スタブを叩いている。**WSL 側で実行する** |
| `uv run pytest` が 46 件の収集エラーになる | `uv sync --all-extras` を実行していない（§3.1） |
| `parameters: 必須フィールドが不足している ... 'drivetrain'` | `--drivetrain` を渡していない。同梱の機体パラメータを指定する（§4.1） |
| ブラウザで画面が空のまま | `npm run build` を実行していない。`viz/dist/` が無いと何も出ない（§3.2） |
| 軌跡アニメーションが「利用できない」と出る | その掃引が `keep_representative_record: false` で記録を残していない（§4.3） |
| 図が出ず、項目名だけが並ぶ | 必須項目が欠けた JSON を読ませている。表示側は欠けた項目名を列挙して図を描かない |
| 未知の値でエラーになる | 上流が新しい値を出すようになった可能性がある。表示側が黙って丸めない設計のため、上流と表示側の整合を確認する |

---

## 関連ドキュメント

| ドキュメント | 内容 |
|---|---|
| [original-features.md](./original-features.md) | シミュレータを含む独自機能の方針（**なぜ作るのかの正**） |
| [development-environment.md](./development-environment.md) | 開発環境・実行環境の方針 |
| [requirements.md](./requirements.md) | 共通要件（マイルストーン含む） |
| [open-questions.md](./open-questions.md) | 未決事項（ホイール径・投擲レイアウト等） |
| `.kiro/specs/trajectory-simulator/` | 掃引実行側の仕様 |
| `.kiro/specs/simulator-visualization/` | ビューア側の仕様 |
