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

| ドキュメント | 内容 |
|---|---|
| [docs/requirements.md](./docs/requirements.md) | 共通要件定義（機能要件・性能要件・マイルストーン） |
| [docs/development-environment.md](./docs/development-environment.md) | 開発環境・実行環境の方針、責務分担、実機 TODO |
| [docs/drivetrain-spec.md](./docs/drivetrain-spec.md) | 駆動系詳細仕様（3輪オムニ・モータ・電源・保護） |
| [docs/bom.md](./docs/bom.md) | 部品表 |

---

## 現時点で確定していない主な事項

数値・方式の多くは**実測してから決める**方針であり、確定値として扱わない。

- Raspberry Pi 4 の OS、RealSense の解像度・fps 設定
- 物体検出方式（AIモデルを最初から必須にしない）
- 固定側 → 移動体の通信方式
- 移動体の短時間移動性能（M2 で実測）

詳細は各ドキュメントの未確定事項セクションを参照。
