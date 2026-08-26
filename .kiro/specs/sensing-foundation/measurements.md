# 実測記録: sensing-foundation

> 本書は `tasks.md` タスク9（実機ブリングアップと実測）の記録先である。
> 各タスクの**確認項目・実施日・結果・使用したコマンド**をここに残す。
>
> ⚠️ **生データ（セッション記録本体・ログ NDJSON・Depth バイナリ）は版管理しない。**
> 本書に残すのは、結論と、その結論を再現するために必要なコマンドおよび実測値のみである。
>
> ⚠️ **実測前の暫定値を根拠に方針を変えない。** 見直しは実測後に行う
> （`tasks.md` タスク8.3 / `requirements.md` の方針）。

## 記録の書式

各タスクにつき次の5項目を必ず記す。

| 項目 | 内容 |
|---|---|
| 実施日 | 実測を行った日付（YYYY-MM-DD） |
| 実施環境 | 対象機・OS・接続形態など、結果の再現に要る前提 |
| 使用したコマンド | そのまま再実行できる形。省略・要約しない |
| 結果 | 実測値。判定を伴う場合は判定基準を併記する |
| 特記事項 | 想定と違った点、次タスクへの申し送り、リスク |

---

## 実機構成

| 項目 | 値 | 出典 |
|---|---|---|
| 機種 | Raspberry Pi 4 Model B Rev 1.2 | `/proc/device-tree/model` |
| CPU | Cortex-A72 × 4 コア (aarch64) | `lscpu` |
| 搭載 RAM | **3,973,906,432 bytes（約 3.70 GiB ＝ 4GB モデル）** | `doctor` memory 項目 |
| Swap | 2,097,148 kB（約 2.0 GiB） | `/proc/meminfo` |
| ストレージ | microSD 503,317,929,984 bytes（約 469 GiB）| `doctor` disk 項目 |
| ネットワーク | Wi-Fi 接続（有線 LAN は使用不可のため） | — |
| IP アドレス | 192.168.0.11（DHCP） | `hostname -I` |
| ホスト名 | `raspberry-pi` | — |
| 操作方法 | 開発機（Windows）から SSH 公開鍵認証 | — |

> **OQ-24（Pi 4 の RAM 容量の確認）はこれをもって決着する: 4GB モデルである。**

---

## タスク9.1 OS を導入し、RAM 容量と選定結果を記録する

**ステータス: 完了**

### 実施日

2026-08-27

### 実施環境

Raspberry Pi 4 Model B Rev 1.2 に microSD を新規書き込みしてクリーンインストール。
RealSense D435 は本タスクの時点では未接続（接続と導通確認はタスク9.2の責務）。

### 採用した OS と選定理由

| 項目 | 値 |
|---|---|
| OS 名 | Raspberry Pi OS 64-bit（**Desktop 版**、Debian GNU/Linux 13 "trixie" ベース） |
| Debian バージョン | 13.5 |
| カーネル | 6.18.34+rpt-rpi-v8 |
| アーキテクチャ | aarch64 |
| 64bit 判定 | **True** |
| Python | 3.13.5（`/usr/bin/python3`。作業は venv 配下の 3.13.5 を使用） |

**ディストリビューションの選定**は [OQ-23](../../../docs/open-questions.md) の決定
「Pi ハード層の未知数が少ない Raspberry Pi OS 64-bit を先に試し、librealsense の
ビルドが通らなければ Ubuntu 24.04 LTS へ退避」に従った。**現時点で退避は発生していない**
（ビルド成否の判定はタスク9.2）。

**Lite ではなく Desktop 版を選んだ**。理由は次の2点である。

1. `research.md` の記録形式に関する決定が、`.bag` を「**RealSense Viewer で目視確認したいとき**に
   SDK 付属ツールで別途取る手段として残す」と明記している。viewer は GUI アプリであり、
   Lite では実行できない。ブリングアップ時に「カメラが根本的に生きているか」を
   目視で確認できる経路を確保する価値が、Desktop 版の追加 RAM 消費を上回ると判断した
2. librealsense は aarch64 向け公式 wheel が無くソースビルドが必須であり
   （`research.md` Key Finding 1）、`realsense-viewer` は**同じソースツリーから
   `-DBUILD_GRAPHICAL_EXAMPLES=ON` で同時に生成される**。viewer のために
   追加のビルド作業が発生するわけではない

`tasks.md` タスク9.1 が要求する「headless 運用可能」は、
**開発機から SSH でログインして診断コマンドを実行できることをもって満たしている**
（本タスクの実測は全て SSH 経由で実施した）。Desktop 版であることと
headless 運用可能であることは両立する。

> **申し送り**: 実測フェーズ（タスク9.4〜9.6）に入る前に
> `sudo raspi-config` → System Options → Boot / Auto Login → **Console** へ切り替え、
> デスクトップ環境を常駐させない状態で測ること。viewer が要るときだけ `startx` で起動する。
> 本タスク時点の起動ターゲットは `graphical.target` のままである。

### 使用したコマンド

開発機（Windows / Git Bash）から SSH 経由で実行した。

```bash
# 接続確認
ssh -i ~/.ssh/id_ed25519 raspi@192.168.0.11 "hostname; uname -srmo"

# リポジトリ配置（Pi 上）
mkdir -p ~/repos && cd ~/repos
git clone --depth 1 https://github.com/f18c052f/smart-trashbox-tribute.git

# Python 環境（PEP 668 によりシステムへの pip install は拒否されるため venv 必須）
cd ~/repos/smart-trashbox-tribute
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e ".[sensing]"

# 診断コマンド（本タスクの中心）
.venv/bin/python -m sensing_foundation.cli doctor
```

### 結果: 診断コマンドの出力

`doctor` は9項目すべてを返した。**SDK 関連の4項目のみが `fail` / `skip` となり、
OS・Python・メモリ・ディスクの各項目は `ok` を返した**
（これは `tasks.md` タスク6.2 の「観測可能な完了状態」が予期した通りの挙動であり、
診断ツール自体が実機で正しく機能していることの確認にもなっている）。

| 項目 | status | 要旨 |
|---|---|---|
| `os` | **ok** | Debian GNU/Linux 13 (trixie) / kernel 6.18.34+rpt-rpi-v8 (aarch64), 64bit=True |
| `python` | **ok** | Python 3.13.5（venv 配下） |
| `memory` | **ok** | 搭載 3,973,906,432 bytes / 利用可能 3,579,781,120 bytes |
| `sdk_import` | fail | `pyrealsense2` を import できない（**タスク9.2 で解消する。本タスクの範囲外**） |
| `device` | fail | SDK 不在のため列挙不可 |
| `usb` | skip | SDK 不在のため判定不可 |
| `stream_open` | fail | SDK 不在のため不可 |
| `power_stability` | skip | SDK 不在のため試行不可 |
| `disk` | **ok** | `var/sessions`: 空き 477,069,606,912 / 総容量 503,317,929,984 bytes、書き込み速度目安 **31.9 MB/s** |

`ok` を返した項目の生の値（`doctor` の JSON 出力より抜粋）:

```json
{
  "name": "os",
  "status": "ok",
  "detail": "Debian GNU/Linux 13 (trixie) / kernel 6.18.34+rpt-rpi-v8 (aarch64), 64bit=True",
  "value": {
    "system": "Linux",
    "release": "6.18.34+rpt-rpi-v8",
    "machine": "aarch64",
    "is_64bit": true,
    "os_release": {
      "PRETTY_NAME": "Debian GNU/Linux 13 (trixie)",
      "VERSION_ID": "13",
      "VERSION_CODENAME": "trixie",
      "DEBIAN_VERSION_FULL": "13.5"
    }
  }
}
```

```json
{
  "name": "memory",
  "status": "ok",
  "detail": "搭載 RAM 3973906432 bytes, 利用可能 3579781120 bytes",
  "value": {
    "available": true,
    "system_total_bytes": 3973906432,
    "system_available_bytes": 3579781120
  }
}
```

### リングバッファ上限の既定割合の見直し

`config.py` の既定は「`max_ring_bytes` 未指定のとき**搭載 RAM の 25%**を上限とする」であり、
必要 RAM は `width * height * 2 * fps * ring_seconds` で算出される
（`_required_ring_bytes` / `_resolve_ring_cap_bytes`）。実測した RAM 容量に対する検算:

| 項目 | 値 |
|---|---|
| 搭載 RAM（実測） | 3,973,906,432 bytes |
| 上限（既定 25%） | **993,476,608 bytes**（約 947 MiB） |
| 既定設定の必要量（640×480 / 30fps / ring 3.0s） | **55,296,000 bytes**（約 52.7 MiB） |
| 余裕 | **約 18.0 倍** |
| 60fps に上げた場合の必要量 | 110,592,000 bytes（約 105.5 MiB）、余裕 約 9.0 倍 |
| 上限内に収まる ring_seconds の上限（30fps 時） | 約 53.9 秒 |

**結論: 既定割合 25% は 4GB モデルに対して妥当であり、変更しない。**
既定設定（640×480 / 30fps / ring 3.0s）は上限の 5.6% しか使わず、
タスク9.4 で 60fps を評価する場合も 11.1% にとどまる。
`MemAvailable`（3,579,781,120 bytes）と比較しても上限値そのものが十分小さく、
リスク R4（リングバッファが RAM を食い潰す）は本構成では顕在化しない見込みである。

> なお `ring_seconds` を 50 秒近くまで伸ばせる余地があるが、
> **これは「伸ばしてよい」という結論ではない**。`ring_seconds` の適正値は
> 投擲1回を確実に含む長さとして別途決めるものであり、本タスクの範囲外である。

### 特記事項

1. ⚠️ **OS が Debian 13 (trixie) ベースであり、`research.md` が前提としていた Bookworm
   （Debian 12）より新しい。** これはタスク9.2（librealsense のソースビルド）にとって
   リスクである。`research.md` が参照しているビルド手順の実例はいずれも
   Bullseye / Bookworm 時代のものであり、trixie では次の差分が影響しうる:
   - **GCC 14.2.0**（Bookworm は GCC 12）— librealsense のビルドが新しい GCC の
     厳格化された警告・エラーで停止する可能性
   - **Python 3.13**（Bookworm は 3.11）— `pyrealsense2` バインディングを 3.13 向けに
     生成する必要がある。`research.md` が「import できない事例の原因は Python の
     バージョン取り違えに集中している」と指摘している通り、ここが最も詰まりやすい

   **タスク9.2 でビルドが通らなかった場合、この OS の新しさを第一の被疑対象とすること。**
   OQ-23 の退避規則（Ubuntu 24.04 LTS arm64 へ退避）を発動する前に、
   「Pi OS だから駄目」なのか「trixie だから駄目」なのかを切り分ける価値がある
   （Ubuntu 24.04 は GCC 13 / Python 3.12 であり、trixie より Bookworm に近い）

2. **Swap が 2.0 GiB 確保されている。** librealsense のビルドは Pi 4 上で
   メモリを大きく使い OOM で停止する事例が知られているため、この容量は
   タスク9.2 にとって有利な条件である（リスク R1 の緩和材料）。
   ビルド時に不足した場合はさらに拡張を検討する

3. **microSD の実容量は約 469 GiB（503 GB）である。** 作業開始時の想定（32GB）と
   異なるが、記録セッションの保存先容量として有利であり問題はない。
   `doctor` の disk 項目が空き 477 GB を報告している

4. **microSD の書き込み速度目安は 31.9 MB/s。** `research.md` は Depth 640×480/30fps を
   約 18 MB/s と見積もっており、**連続記録は帯域上は可能だが余裕は約 1.8 倍しかない**。
   本 Spec が `.bag` の連続記録ではなく **RAM リングバッファ＋事後書き出し**を
   採用した判断（Decision 2）を裏付ける実測値である。
   60fps（約 37 MB/s）では**書き込み速度を上回る**ため、
   タスク9.4 で 60fps を評価する際に連続記録を併用してはならない

5. **Python 環境は venv + pip で構築した**（PEP 668 が有効なため venv は必須）。
   OQ-41（Python の環境構築・パッケージ管理方法）の第一候補は `uv` だが、
   本タスクでは診断コマンドの実行に必要な最小構成を優先し `uv` を導入していない。
   **OQ-41 はタスク9.7 で「決着させない」と定められている**ため、
   この選択は暫定であり決定ではない

### 未実施の項目

なし（タスク9.1 の要求事項はすべて充足）。

---

## タスク9.2 RealSense の導通を確認し、SDK 導入手順を再現可能な形で記録する

**ステータス: 未着手**

前提条件（タスク9.1）は充足済み。次に実施する。
確認順序は `docs/development-environment.md §16` に従い
**認識 → USB3 接続 → 給電安定性 → SDK 導入**の順とし、**fps 計測より先に行う**。

着手時点で不足しているもの: `cmake`（未導入）、librealsense ソース、
ビルド用の開発ライブラリ一式。

---

## タスク9.3 live アダプタを実機で通し、契約テストを再実行する

**ステータス: 未着手**（タスク8.2・9.2 の完了が前提）

---

## タスク9.4 解像度・fps を掃引し、設定を決定する

**ステータス: 未着手**（タスク9.3 の完了が前提）

> 着手前に起動ターゲットを `Console` へ切り替えること（タスク9.1 の申し送り）。
> また 60fps の評価時は連続記録を併用しないこと（タスク9.1 特記事項4）。

---

## タスク9.5 計測 ON / OFF の影響を実機で確認する

**ステータス: 未着手**（タスク9.4 の完了が前提）

> 記録の計測は「記録のみ・ロギングは別測定」として単離されている
> （`tasks.md` タスク7.3 の Implementation Notes）。この単離方針を記録時に明記すること。

---

## タスク9.6 実データを記録し、WSL で再生する往復を確認する

**ステータス: 未着手**（タスク9.4 の完了が前提）

---

## タスク9.7 決着した未決事項をプロジェクト文書へ反映する

**ステータス: 未着手**（タスク9.5・9.6 の完了が前提）

決着させる対象: OQ-23・OQ-24・OQ-25・OQ-28・OQ-32・OQ-35
（**OQ-27・OQ-40・OQ-41 は決着させない**）。

進捗:
- **OQ-24（RAM 容量）**: タスク9.1 で実測済み（4GB モデル）。決着可能
- OQ-23（OS 選定）: タスク9.2 のビルド成否をもって確定する
