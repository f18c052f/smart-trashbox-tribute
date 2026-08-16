# Brief: prediction-core

## Problem

飛来する物体の3D位置サンプル列から**床面への落下地点と落下時刻**を推定する処理は、
このプロジェクトで**最も多くの利用者を持つ部品**である。

- M1: 実データ（RealSense 経由）を流して予測精度を検証する
- 柱1 シミュレータ: 合成データを流して感度分析する
- M3 以降: 移動体へ送る目標座標を生成する

ここを各所で個別に実装すると、`original-features.md` が警告する
**「シミュレータが検証しているのは本番と別の実装」**という状態に直行する。
両者は必ず少しずつズレていき、しかも**ズレていることに気付けない。**

## Current State

- 数式は `requirements.md §5-B` で確定している: `z(t)=z0+vz·t−½g·t²`、x, y は等速、
  3点以上で最小二乗、床平面 z=0 との交点を解く
- **コードは一行も存在しない**
- Throw Record スキーマ（OQ-31）は未定義。Record/Replay の形式（OQ-32）もこれに従う

## Desired Outcome

- `(t, x, y, z)` のサンプル列を渡すと、`(x_impact, y_impact, t_impact, residual)` が返る
- **ハードウェア無しで単体テストできる**（既知の放物線を入れれば解析解と一致する）
- 残差を**予測の信頼度**として取り出せる（FR-1 の根拠 (b)(c) を満たす）
- サンプル数を変えたときの誤差挙動を評価できる（FR-1 の「3サンプル」の妥当性検証に使える）

## Approach

**入力を「時刻付き3D点の列」だけに限定した、依存の無い Python モジュールとして実装する。**

RealSense 固有の型、カメラのパラメータ、ファイル I/O をこの層に持ち込まない。
そうすることで live / recorded / simulated のどれからでも同じ関数を呼べる
（`development-environment.md §7`）。

あわせて **Throw Record の最小スキーマ**をここで定義する。
柱3 を見送ったため完全な形にはせず、**「1投擲＝1レコード」の粒度だけを守る**。

## Scope

- **In**:
  - 放物運動モデルへの最小二乗フィッティング
  - 床平面 z=0 との交点による落下地点・落下時刻の算出
  - 残差（信頼度）の算出
  - サンプル数不足・発散時の扱い（例外か無効値か）
  - Throw Record 最小スキーマの定義（→ OQ-31）
  - 解析解との突き合わせによる単体テスト
- **Out**:
  - 物体検出・追跡（→ `flying-object-tracking`）
  - 座標変換・キャリブレーション（→ `world-frame-calibration`）
  - ノイズ生成・投擲物理（→ `trajectory-simulator`）
  - 可視化（→ `m1-prediction-validation` / `simulator-visualization`）

## Boundary Candidates

- **フィッティング**（サンプル列 → 軌道パラメータ）
- **交点算出**（軌道パラメータ → 落下地点・時刻）
- **信頼度評価**（残差 → 採否判定の材料）
- **Throw Record スキーマ**（レコードの型定義とシリアライズ）

## Out of Boundary

- 誤検出の**棄却ポリシー**そのもの（閾値の決定）。残差を返すところまでを持ち、
  「いくつ以上を捨てるか」は利用側が決める
- 空気抵抗の考慮。**最初は最小限**とし、M1 実測で不足が分かってから足す（→ OQ-33）
- Depth ノイズのモデル化（→ `trajectory-simulator`）

## Upstream / Downstream

- **Upstream**: なし（ハード不要・依存なしで着手できる唯一の Spec）
- **Downstream**:
  - `trajectory-simulator`（合成データを流す）
  - `m1-prediction-validation`（実データを流す）
  - `sensing-foundation`（記録形式が Throw Record スキーマに従う）
  - 将来 M3 の目標座標生成

## Existing Spec Touchpoints

- **Extends**: なし（新規プロジェクトの最初の Spec）
- **Adjacent**: `sensing-foundation` とは **Throw Record スキーマ**で接する。
  スキーマはこちらが定義し、向こうが従う。**別々に決めない**

## Constraints

- **Python のみ。** TypeScript へ複製しない（`tech.md` 開発標準3）
- **根拠のない固定値を埋め込まない。** 重力加速度以外の定数はパラメータ化する
- 単位は **mm / ms**（`requirements.md §6.1`）
- 最低サンプル数の既定は3だが、**「3」を絶対視しない。**
  実用上足りない可能性があり、M1 の実測で見直す前提の設計にする
