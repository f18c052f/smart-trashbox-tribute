# Brief: sensing-foundation

## Problem

M1 のすべての処理は「Raspberry Pi 4 上で RealSense D435 から安定してフレームが取れる」ことに乗っている。
ところが**実機は未セットアップ**であり、OS も決まっていない。

さらに `original-features.md` が指摘するとおり、既存ドキュメントには
**「測る」と書いてあるのに「どう測るか」が定義されていない**箇所が大量にある。
段階別レイテンシ（`development-environment.md §13.1`）が取れなければ、
**Raspberry Pi 4 を継続するかの判断（OQ-27）そのものが下せない。**

## Current State

- Pi 4 / D435 ともに**未セットアップ**（OS 未導入、USB3 認識・給電安定性いずれも未確認）
- OS は Raspberry Pi OS 64-bit と Ubuntu 24.04 LTS arm64 の2候補（OQ-23）
- 解像度・fps の初期評価候補は 640×480 / 30fps だが、**必須性能でも達成済み性能でもない**（OQ-25）
- Record / Replay は「重要な開発方針」と明記されているが**形式未定**（OQ-32）
- ログ形式も未定（OQ-35）

## Desired Outcome

- Pi 4 上で D435 から**フレーム落ちを把握しながら**安定取得できる
- 投擲の実データを**記録**でき、WSL へ持ち帰って**繰り返し Replay** できる
- 下流（検出・追跡・予測）が **live / recorded / simulated を区別せず**扱える
- 各段階の時刻・FPS を**構造化ログとして残せ**、後から集計できる
- **計測 ON / OFF で end-to-end latency が有意に変化しない**ことを実測で確認済み

## Approach

**「入力層の抽象化」と「構造化ロギング」を最初から同時に作る。**

ロギングを後付けにすると、capture 段階のレイテンシが測れなくなり、
OQ-27 の判断材料が永久に欠ける。逆にここで基盤を作れば、以降の Spec は
自分の区間の計測点を足すだけで済む。

実機セットアップは `development-environment.md §16` の手順に従い、
**#3〜#6（認識・USB3・給電安定性・SDK 導入）を fps 計測より先に**行う。
ここが不安定なまま fps を測ると**「Pi 4 の性能不足」と誤診する。**

## Scope

- **In**:
  - Pi 4 の OS 選定と導入（→ OQ-23 / OQ-24）
  - RealSense の導通確認: 認識・USB3・給電安定性・librealsense / pyrealsense2 導入（→ OQ-28）
  - 解像度・fps の実測比較（**実効サンプル数**で評価。→ OQ-25）
  - フレーム取得ループ（**古いフレームを溜めない**）
  - 実データの記録（Record）と WSL 側での再生（Replay）（→ OQ-32）
  - 入力層の抽象化: live / recorded / simulated を差し替え可能にする
  - 構造化ロギング基盤（→ OQ-35）と、capture 区間のレイテンシ計測
  - **計測 ON / OFF でレイテンシが変わらないことの実測確認**
- **Out**:
  - 物体検出（→ `flying-object-tracking`）
  - 床平面推定・座標変換（→ `world-frame-calibration`）
  - 予測（→ `prediction-core`）
  - 可視化・ダッシュボード（柱2b は保留 → OQ-38）

## Boundary Candidates

- **実機セットアップ**（OS・ドライバ・導通）
- **フレーム取得**（RealSense 依存部）
- **入力層抽象**（live / recorded / simulated の共通インターフェース）
- **記録・再生**（Record / Replay）
- **構造化ロギング**（fire-and-forget な記録と、後段の集計）

## Out of Boundary

- **ライブダッシュボード**。M1 / M2 で見たい現象はリアルタイム観測には速すぎる（→ OQ-38）
- 集計・可視化の重い処理を Pi 上で行うこと。**記録するだけにする**
- Pi 4 を別ハードへ置き換える判断。基盤と計測手段を用意するところまでを持ち、
  **判断は `m1-prediction-validation` で行う**（→ OQ-27）

## Upstream / Downstream

- **Upstream**: なし（ただし**実機が必要**。ハード待ちが発生する）
- **Downstream**: `world-frame-calibration` / `flying-object-tracking` /
  `m1-prediction-validation` のすべてが依存する

## Existing Spec Touchpoints

- **Extends**: なし
- **Adjacent**: `prediction-core` が定義する **Throw Record 最小スキーマ**に記録形式を合わせる。
  スキーマを二重定義しない

## Constraints

- **RealSense を WSL へ直結する構成を標準フローにしない。** 実機は Pi 4 側で扱う
- Pi 4 向け設計方針を守る: 低レイテンシ優先 / 古いフレームを溜めない /
  不要な画像コピーを減らす / **毎フレーム巨大な Point Cloud を作らない** /
  **GUI 表示を本番処理の必須要件にしない**（headless 運用可能に）
- ログ送出は **fire-and-forget**。完了を待たない。**実行時に無効化できる**こと
- 開発は WSL、Git 経由で Pi へ。**Pi 上で直接コードを編集して差分が迷子になる運用を避ける**
- `60 fps ありき`で設定を選ばない。30 fps で必要サンプル数が取れるなら 30 fps でよい
