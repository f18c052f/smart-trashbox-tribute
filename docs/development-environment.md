# 開発環境・実行環境 方針 v0.1

要件定義: [requirements.md](./requirements.md) / 駆動系詳細: [drivetrain-spec.md](./drivetrain-spec.md) / 部品表: [bom.md](./bom.md)

> **本ドキュメントの位置付け**
> 「**今後どの環境で、どの責務の処理を実行するか**」を整理したもの。
> 記載内容は**方針であり、実装ではない**。本ドキュメント作成時点でソフトウェアは一行も存在しない。
> 数値（解像度・fps・レイテンシ等）は**すべて評価候補であり、達成済み性能でも必須性能でもない**。
> 実測して初めて確定する（→ [§13](#13-raspberry-pi-4-を継続するかの判断)）。

---

## 1. 全体構成

```
Intel RealSense D435
        |
        v
Raspberry Pi 4 Model B        ← 固定側（検出・予測）
        |
        | 落下位置・到達時間等の「小さいデータ」
        v
      ESP32                    ← 移動体側（駆動制御）
        |
        v
  3輪オムニ移動体
```

**原則: RealSense の画像・Depth フレームそのものを ESP32 へ送らない。**
ESP32 へ渡すのは予測結果（目標座標・到達時刻等）だけとする（→ [§9](#9-pi--esp32-の通信内容)）。

この構成は既存要件の責務分離（固定側＝知能 / 移動体＝身体）をそのまま踏襲したものであり、
[requirements.md §1](./requirements.md#1-システム構成サブシステム分解) の骨格を変更しない。

---

## 2. 環境の役割分担

| 環境 | 位置付け | RealSense 接続 |
|---|---|---|
| **Windows + WSL2** | **メイン開発環境** | **標準フローでは接続しない** |
| **Raspberry Pi 4 Model B** | **実機ターゲット環境** | **接続する** |

### 2.1 WSL（主開発環境）

Windows 上の WSL2 を主なソフトウェア開発環境とする。Claude Code も WSL 上で使用する。

将来的に WSL 側で行うこと:

- Claude Code による実装
- Git 操作
- アルゴリズム開発
- 単体テスト
- 軌道予測ロジックの検証
- 座標変換ロジックの検証
- **保存済み RealSense データを使った再生・解析**（→ [§6](#6-record--replay-を将来設計に含める)）
- ログ解析
- パフォーマンス分析

> ⚠️ **WSL から RealSense D435 へ直接接続して開発することを標準フローにしない。**
> WSL2 の USB passthrough を使えば接続できる可能性はあるが、
> **RealSense / USB / WSL 仮想化**の問題が混在するとデバッグが難しくなるため。
> RealSense 実機は Raspberry Pi 4 側で扱う。

### 2.2 Raspberry Pi 4 Model B（実機ターゲット）

RealSense を接続する実機ターゲット環境。**普段のコーディング環境としては使わない。**

将来的に Pi 4 側で行うこと:

- RealSense D435 からのデータ取得
- 飛翔物検出
- Depth を使った3D座標取得
- 追跡
- 軌道予測
- 落下地点予測
- ESP32 への目標情報送信
- **RealSense 実データの記録**
- 実レイテンシ測定
- 実 FPS 測定
- 最終統合試験

---

## 3. ターゲットハードウェア

### 3.1 現在の正式ターゲット

**Raspberry Pi 4 Model B**。手持ちのため、これを現在の正式ターゲットとする。
**Raspberry Pi 5 前提の記述へ変更しない。**

### 3.2 ただし性能達成は断定しない

Smart Trashbox は**低レイテンシが重要**なため、Pi 4 で必要性能を達成できると断定しない。

**判断手順**: まず Pi 4 で実測する → 性能不足が明確になった場合にのみ別ハードウェアを検討する。
改善の試行順序は [§13.2](#132-性能不足だった場合の検討順序) に従う。

### 3.3 将来候補（現時点では購入予定にしない）

性能不足が実測で確認された場合にのみ検討する候補として、以下を記録しておく。

- Raspberry Pi 5
- x86 mini PC
- Jetson 系
- AI accelerator

> いずれも**現時点では購入予定にしない**。BOM の購入対象にも追加しない。

---

## 4. Raspberry Pi 4 向けの設計方針

Pi 4 は計算資源に余裕が大きくないため、今後のソフトウェア設計では以下を意識する。
**（今回は実装しない。実装方針としての記載。）**

- 低レイテンシ優先
- **古いフレームを溜めない**
- 必要以上に高解像度化しない
- 不要な画像コピーを減らす
- 必要な **ROI だけ**処理する
- **毎フレーム巨大な Point Cloud を作らない**
- RGB が不要なら **Depth 中心の構成**も検討する
- **GUI 表示を本番処理の必須要件にしない**（headless 運用を前提にできること）
- **処理時間を段階ごとに測定できる構造にする**（→ [§13.1](#131-実測する項目)）
- 重い AI モデルを最初から必須にしない（→ [§10](#10-物体検出方式は軽量な方式から検討)）

---

## 5. RealSense の初期設定方針

**最初から最大性能で動かす前提にしない。**

### 5.1 初期評価候補

| 項目 | 初期評価候補 |
|---|---|
| Depth 解像度 | 640 × 480 |
| フレームレート | 30 fps |

まずこの程度から実機評価を始める。その後、以下を比較する。

- 640×480 / 30 fps
- 640×480 / 60 fps
- その他必要な設定

> ⚠️ **30 fps / 60 fps のどちらも、現時点で「必須性能」でも「達成済み性能」でもない。**
> Raspberry Pi 4 上で **capture FPS / processing FPS / end-to-end latency / dropped frame** を
> 実測してから決定する。

なお [requirements.md NFR-4](./requirements.md#nfr-4-制御周期) の「≥ 60 fps 相当」は
**要件側の目標値**であり、Pi 4 + D435 で達成可能かどうかは未検証である。両者を混同しない。

---

## 6. Record / Replay を将来設計に含める

**RealSense 実機を毎回使わなくてもアルゴリズムを改善できる構成**を目指す。

```
Raspberry Pi 4 + RealSense D435
        |
        v
   実際の投擲データを記録
        |
        v
      WSL へコピー
        |
        v
   同じデータを何度でも Replay
        |
        v
  検出・追跡・軌道予測を改善
```

これを**重要な開発方針**として位置付ける。同一データに対して繰り返し検証できることで、
アルゴリズム変更の効果を投擲のばらつきと切り分けて評価できる。

> ⚠️ 今回は**録画機能・Replay 機能・データフォーマットを実装しない。**
> データフォーマットは未確定（→ [§16 TODO](#16-実機セットアップ時に確認する-todo)）。

---

## 7. RealSense 依存部分を将来的に分離する

将来的なソフトウェア設計として、**RealSense からの入力**と**その後の処理**を疎結合にする。

```
RealSense input
      |
      v
common frame data     ← ここから下は入力元に依存しない
      |
      v
  detection
      |
      v
   tracking
      |
      v
 3D position
      |
      v
trajectory prediction
```

**狙い**: 同じ検出・追跡・予測ロジックに対して、

- **WSL** では **Recorded Data** を入力にできる
- **Pi** では **RealSense Live Data** を入力にできる

という設計にする。これが [§6](#6-record--replay-を将来設計に含める) の Record / Replay を成立させる前提となる。

> ⚠️ 今回は **class / interface / Python ファイル / ディレクトリを作成しない。**
> 上記は責務分離の**設計思想**であり、具体的な型・API・モジュール名は未確定。

### 7.1 将来のリポジトリ構成（**案・未確定**）

参考として責務の並びだけ示す。**今回ディレクトリは作成しない**。実装着手時に改めて決める。

```
docs/          設計ドキュメント（現在存在するのはここだけ）
（以下は将来案・未作成）
  入力層        RealSense live / recorded data の読み出し
  処理層        detection / tracking / 3D position / prediction
  通信層        ESP32 への送信
  移動体側      ESP32 ファームウェア
```

---

## 8. 固定側と移動体側の責務

既存仕様の責務分離（[requirements.md §1](./requirements.md#1-システム構成サブシステム分解)）を**維持する**。

| 区分 | 構成 | 責務 |
|---|---|---|
| **固定側** | RealSense D435 ＋ **Raspberry Pi 4** | Detection ＋ Prediction |
| **移動体側** | ESP32 ＋ モータドライバ ＋ モータ ＋ エンコーダ | Motor Control |

> ⚠️ **Raspberry Pi をゴミ箱本体へ搭載する構成には変更しない。** Pi は固定側に置く。
> 移動体に載せるのは ESP32 以下のみ（[drivetrain-spec.md](./drivetrain-spec.md) の構成を変更しない）。

---

## 9. Pi → ESP32 の通信内容

**Pi から ESP32 へカメラ画像を送らない。** 送るのは予測結果のみ。

### 9.1 送信内容（将来案）

必須と想定するもの:

- target x
- target y
- predicted impact time

必要であれば将来的に追加を検討するもの:

- timestamp
- sequence number
- confidence
- validity

> [requirements.md §6](./requirements.md#6-インターフェース定義サブシステム間の契約) の
> `{ x_mm, y_mm, t_impact_ms }` が現在の最小形。**今回、通信プロトコルを確定しすぎない。**

### 9.2 通信方式（候補を残す）

以下を候補として残し、**現時点では確定しない**。

- UDP
- Wi-Fi
- Serial
- **固定側 ESP32 を利用した Bridge**（Pi → 固定側 ESP32 → 移動体 ESP32）

> ⚠️ **ESP-NOW を Pi から直接使えることを前提にしない。**
> ESP-NOW は ESP32 間の通信方式であり、Pi から直接利用できるかは未検証。
> このため「固定側に ESP32 を1台置いて Bridge にする」案を候補として残している。

---

## 10. 物体検出方式は軽量な方式から検討

Pi 4 の性能を考慮し、**最初から YOLO 等のニューラルネットを必須にしない。**

初期候補として以下の軽量方式も検討対象とする。

- Depth 差分
- Background subtraction
- Frame difference
- Motion detection
- ROI
- Contour detection

AI モデルを使用する場合も、**Pi 4 上で実測してから決定する。**

> ⚠️ **現時点では物体検出方式を確定しない。**

---

## 11. OS

**Raspberry Pi 4 の OS はまだ確定事項にしない。**

### 11.1 要求

- Raspberry Pi 4 Model B 対応
- **64bit**
- **headless 運用可能**
- RealSense 利用可能
- Python / OpenCV 等を利用可能

### 11.2 候補（どちらも確定扱いにしない）

- Raspberry Pi OS 64-bit
- Ubuntu 24.04 LTS arm64

> **RealSense（librealsense / pyrealsense2）のインストール容易性**等を
> 実機セットアップ時に確認して決める。

---

## 12. 段階的な検証方針

最初から完成版の Smart Trashbox を作るのではなく、以下の順で段階的に検証する。
**これは開発計画であり、今回それらを実装しない。**

| # | 段階 |
|---|---|
| 1 | D435 を Pi 4 で**安定取得** |
| 2 | **実データ記録** |
| 3 | 検出 |
| 4 | 3D位置取得 |
| 5 | 追跡 |
| 6 | 軌道予測 |
| 7 | ESP32 との通信 |
| 8 | 移動体との統合 |

この 1〜6 が [requirements.md M1（予測の可視化）](./requirements.md#8-段階的開発マイルストーン) の中身にあたり、
7〜8 が M3（結合）に対応する。M2（移動体の短時間応答評価）は駆動系側の検証であり、
本ドキュメントの範囲（固定側の計算機環境）とは独立に進められる。

---

## 13. Raspberry Pi 4 を継続するかの判断

**実測で判断する。**

### 13.1 実測する項目

| 分類 | 項目 |
|---|---|
| スループット | RealSense 取得 FPS |
| スループット | 実処理 FPS |
| リソース | CPU 使用率 |
| リソース | メモリ使用量 |
| 取りこぼし | dropped frames |
| レイテンシ | Capture latency |
| レイテンシ | Detection latency |
| レイテンシ | Tracking latency |
| レイテンシ | Trajectory prediction latency |
| レイテンシ | **End-to-end latency** |

> レイテンシを段階ごとに分解して測れる構造にしておくこと（→ [§4](#4-raspberry-pi-4-向けの設計方針)）。
> どの段階が律速かが分からないと、ハードウェア変更の判断ができない。

### 13.2 性能不足だった場合の検討順序

**すぐにハードウェアを変更しない。** 以下を先に検討する。

1. Color stream 削減
2. Resolution 調整
3. ROI 縮小
4. FPS 調整
5. 不要な画像処理削減
6. Point Cloud 全生成を避ける
7. 検出アルゴリズム簡略化
8. ソフトウェア最適化

**それでも必要性能に届かない場合のみ**、Pi 4 以外のハードウェアを検討する（→ [§3.3](#33-将来候補現時点では購入予定にしない)）。

> この「安易に部品を替えず、まず設定とソフトウェアで詰める」という順序は、
> 駆動系の [drivetrain-spec.md §12](./drivetrain-spec.md#12-m2-で性能不足だった場合の調査順序)
> （すぐに高価なモータへ交換しない）と同じ考え方である。

---

## 14. 開発フロー

```
WSL
 ↓  Claude Code で開発
 ↓  Unit Test / Replay Test
 ↓  Git push
Raspberry Pi 4
 ↓  Git pull
 ↓  RealSense 実機テスト
 ↓  実データ記録
 ↓  WSL へ持ち帰る
WSL
 ↓  解析・改善
```

| 環境 | 位置付け |
|---|---|
| **WSL** | メイン開発環境 |
| **Raspberry Pi 4** | 実機ターゲット環境 |

Git を経由して受け渡すため、Pi 上で直接コードを編集して差分が迷子になる運用は避ける。
Pi で得た**実データ**は WSL へ持ち帰り、Replay で繰り返し解析する（→ [§6](#6-record--replay-を将来設計に含める)）。

---

## 15. 現時点で確定していること / していないこと

### 確定

- 主開発環境は **Windows + WSL2**（Claude Code も WSL 上で使用）
- 実機ターゲットは **Raspberry Pi 4 Model B**
- カメラは **Intel RealSense D435**、移動体制御は **ESP32**
- RealSense は **Pi 4 に接続**する。WSL 直結を標準フローにしない
- **画像・Depth フレームを ESP32 へ送らない**
- Raspberry Pi は**固定側**に置く（移動体に搭載しない）
- Record / Replay を開発方針に含める
- RealSense 依存部と処理部を疎結合にする方針

### 未確定

| 項目 | 決め方 |
|---|---|
| Pi 4 の OS（Raspberry Pi OS 64-bit / Ubuntu 24.04 LTS arm64） | 実機セットアップ時、RealSense 導入容易性で判断 |
| RealSense の解像度・fps 設定 | Pi 4 上で実測して決定 |
| 物体検出方式 | Pi 4 上で実測して決定 |
| Pi → ESP32 の通信方式 | 候補（UDP / Wi-Fi / Serial / ESP32 Bridge）から後日決定 |
| 通信メッセージの最終フォーマット | 実装着手時 |
| Record / Replay のデータ形式 | 実装着手時 |
| Pi 4 で必要性能を達成できるか | 実測（§13.1）で判断 |
| リポジトリのディレクトリ構成 | 実装着手時 |

---

## 16. 実機セットアップ時に確認する TODO

**現時点で未確認のものを確定扱いにしない。** 実機セットアップ時に以下を確認する。

| # | 確認事項 |
|---|---|
| 1 | Raspberry Pi 4 の **RAM 容量**確認 |
| 2 | **Raspberry Pi OS / Ubuntu** の最終選定 |
| 3 | RealSense D435 の Pi 4 での**認識** |
| 4 | **USB3 接続**確認 |
| 5 | **pyrealsense2 / librealsense** 導入方法 |
| 6 | **640×480 30fps** 動作確認 |
| 7 | **60fps** 動作確認 |
| 8 | RealSense **電源安定性** |
| 9 | **CPU 使用率** |
| 10 | **メモリ使用率** |
| 11 | **dropped frame** |
| 12 | **end-to-end latency** |
| 13 | **Record / Replay 形式** |
| 14 | **物体検出方式** |
| 15 | **Raspberry Pi → ESP32 通信方式** |

> RealSense は USB3 帯域と給電の影響を受けやすいため、#3 / #4 / #8 は
> fps 計測（#6 / #7）より**先に**確認しておくと切り分けが楽になる。
