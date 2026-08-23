# Smart Trashbox Tribute

投げられたゴミの落下地点を予測し、床を走行するゴミ箱が移動して受け止めるシステム。

元動画: [勝手に入るゴミ箱作った Smart Trashbox](https://www.youtube.com/watch?v=NqDTE6dHpJw)（倉田捻氏 / 文化庁メディア芸術祭受賞）

> **現在のフェーズ: 設計・ドキュメント整理**
> ソフトウェアはまだ実装していない。本リポジトリには現在 `docs/` の設計ドキュメントのみが含まれる。

---

## システム構成

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

「知能（固定側）」と「身体（移動体）」を分離し、両者を**目標座標データ**で繋ぐ。
画像・Depth フレームは移動体へ送らない。

---

## Development Environment

| 環境 | 位置付け | RealSense 接続 |
|---|---|---|
| **Windows + WSL2** | メイン開発環境（Claude Code もここで使用） | 標準フローでは接続しない |
| **Raspberry Pi 4 Model B** | 実機ターゲット環境 | 接続する |

WSL で開発・単体テスト・Replay 検証を行い、Git 経由で Raspberry Pi 4 へ持ち込んで実機テストする。
Pi で記録した実データは WSL へ持ち帰り、繰り返し解析する。

**詳細 → [docs/development-environment.md](./docs/development-environment.md)**

---

## ドキュメント

### 仕様書（現在の状態）

| ドキュメント | 内容 |
|---|---|
| [docs/requirements.md](./docs/requirements.md) | 共通要件定義（機能要件・性能要件・マイルストーン） |
| [docs/drivetrain-spec.md](./docs/drivetrain-spec.md) | 駆動系詳細仕様（3輪オムニ・モータ・電源・保護・手動テレオペ） |
| [docs/bom.md](./docs/bom.md) | 部品表（**型番・数量の正**） |
| [docs/development-environment.md](./docs/development-environment.md) | 開発環境・実行環境の方針、責務分担、実機セットアップ手順 |
| [docs/original-features.md](./docs/original-features.md) | **本プロジェクト独自機能の方針**（シミュレータ / ロギング / 手動テレオペ） |

### 横断ファイル

| ドキュメント | 内容 |
|---|---|
| [docs/open-questions.md](./docs/open-questions.md) | **未決事項の唯一の正**（OQ-01〜OQ-42、欠番あり） |
| [docs/decisions.md](./docs/decisions.md) | **決定と不採用案の記録**（方針転換の経緯・旧案アーカイブ） |

各仕様書は「**現在どうなっているか**」だけを書く。
未決事項は `open-questions.md`、変更の経緯と不採用案は `decisions.md` に集約している。

仕様策定には [cc-sdd](https://github.com/gotalab/cc-sdd)（Kiro 式 Spec-Driven Development）を導入している。
プロジェクト知識は `.kiro/steering/`、個別機能の仕様は `.kiro/specs/` に置く。

---

## 現時点で確定していない主な事項

数値・方式の多くは**実測してから決める**方針であり、確定値として扱わない。
全40件を [docs/open-questions.md](./docs/open-questions.md) に集約している。以下は特に依存の多いもの。

| ID | 事項 |
|---|---|
| [OQ-01](./docs/open-questions.md#a-システム設計成功条件) | **投擲レイアウト**（成功条件を数値化するために必要） |
| [OQ-02](./docs/open-questions.md#a-システム設計成功条件) | **対象とするゴミの種類・寸法**（位置精度の許容値がこれに依存する） |
| [OQ-03](./docs/open-questions.md#a-システム設計成功条件) | **World frame の原点・軸方向とキャリブレーション手順**（未定義のまま M3 に進めない） |
| [OQ-13](./docs/open-questions.md#c-電源安全) | 物理的な非常停止手段 |
| [OQ-27](./docs/open-questions.md#e-固定側検出予測計算機) | Raspberry Pi 4 で必要性能を達成できるか |
| [OQ-29](./docs/open-questions.md#f-通信) | 固定側 → 移動体の通信方式 |

**最も早く着手すべきは OQ-01 と OQ-02。** どちらも軌道シミュレータの入力そのものであり、
**実機を待たずに仮値を置ける。**
