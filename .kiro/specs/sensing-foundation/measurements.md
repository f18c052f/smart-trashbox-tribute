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

**ステータス: 完了**

### 実施日

2026-08-27

### 実施環境

タスク9.1 の構成（Raspberry Pi 4 Model B Rev 1.2 / Debian 13 trixie / Python 3.13.5）に
RealSense D435 を USB3 ポートへ接続。作業はすべて開発機からの SSH 経由。

### 結論（先に要点）

- ✅ **`docs/development-environment.md §16` の #3〜#6 をすべて充足した。**
  `doctor` の**全9項目が `ok`** となった（タスク9.2 の観測可能な完了状態）
- ✅ **OQ-23（Pi 4 の OS）は Raspberry Pi OS 64-bit で確定できる。Ubuntu 24.04 LTS への退避は不要だった。**
  タスク9.1 で最大リスクとして申し送った **Debian 13 trixie / GCC 14.2 / Python 3.13 という
  「research.md の前提（Bookworm）より新しい環境」でも librealsense v2.58.3 のビルドは完走した**
- ✅ **OQ-28（実機セットアップの成立性）も充足した**（認識・USB3・給電安定性・SDK 導入のすべて）

### 手順1: ハードウェア層の確認（SDK 導入より先に実施）

**ビルドに1時間以上かかるため、SDK 導入の前に Linux 標準ツールだけで
認識・USB3・給電を確認した。**（§16 が「#3〜#6 を #7 より先に」と定める趣旨に沿い、
さらにその中でも費用の安い確認を前倒しした）

```bash
lsusb | grep -i realsense       # 認識
lsusb -t                        # 接続速度
vcgencmd get_throttled          # 給電（電圧低下の有無）
vcgencmd measure_temp           # 温度
dmesg | grep -iE 'usb|xhci'     # 切断・リセットの有無
```

| 確認事項 | 結果 |
|---|---|
| 認識 | `Bus 002 Device 002: ID 8086:0b07 Intel Corp. RealSense D435` |
| USB3 接続 | **SuperSpeed 5000M**（Bus 002）。video インターフェース5本すべて 5000M |
| 給電 | **`throttled=0x0`**（電圧低下なし、過去の低下履歴もなし） |
| 温度 | 49.6 °C |

> ⚠️ **`dmesg` に出る USB 切断イベントを給電問題と誤読しないこと。**
> 本作業中に `1-1.4.x` の切断が4件記録されたが、これは Bus 1 に接続された
> Logitech ワイヤレスマウス受信機のものであり、**RealSense（`2-2`）とは別系統**である。
> RealSense のバス番号で絞って判定すること。

### 手順2: ビルド依存パッケージの導入

```bash
sudo apt update && sudo apt install -y \
  cmake build-essential \
  libssl-dev libusb-1.0-0-dev libudev-dev pkg-config \
  libgtk-3-dev libglfw3-dev libgl1-mesa-dev libglu1-mesa-dev
```

`python3-dev`（`/usr/include/python3.13/Python.h`）は Raspberry Pi OS に既に導入済みであった。
`libgtk-3-dev` / `libglfw3-dev` / mesa 系は `realsense-viewer` のビルドに要る
（タスク9.1 で Desktop 版を選んだ理由に対応する）。

導入後のバージョン: `cmake 3.31.6` / `gcc (Debian 14.2.0-19) 14.2.0`

### 手順3: librealsense のクローンとビルド

**採用バージョン: v2.58.3**（当時の最新安定版）。
新しい GCC・Python への対応が最も進んでいる版を選ぶことで、trixie 環境のリスクを下げる判断。

```bash
cd ~
git clone --depth 1 --branch v2.58.3 https://github.com/IntelRealSense/librealsense.git
cd ~/librealsense && mkdir -p build && cd build

cmake .. \
  -DCMAKE_BUILD_TYPE=Release \
  -DFORCE_RSUSB_BACKEND=ON \
  -DBUILD_PYTHON_BINDINGS=ON \
  -DPYTHON_EXECUTABLE=/home/raspi/repos/smart-trashbox-tribute/.venv/bin/python \
  -DBUILD_EXAMPLES=ON \
  -DBUILD_GRAPHICAL_EXAMPLES=ON \
  -DBUILD_UNIT_TESTS=OFF

make -j3
```

**ビルドオプションの根拠:**

| オプション | 値 | 根拠 |
|---|---|---|
| `FORCE_RSUSB_BACKEND` | `ON` | OQ-23 の前提。**カーネルパッチが不要になる**（`research.md` Research 1） |
| `PYTHON_EXECUTABLE` | venv の python3.13 | **`research.md` が「import できない事例の原因は Python の版取り違えに集中」と指摘**。venv の python を明示的に指す |
| `BUILD_GRAPHICAL_EXAMPLES` | `ON` | `realsense-viewer` を得る（タスク9.1 の Desktop 版選定に対応） |
| `BUILD_EXAMPLES` | `ON` | `rs-enumerate-devices` 等のツール |
| `CMAKE_BUILD_TYPE` | `Release` | — |
| 並列度 | `-j3` | 4コアだが、4GB モデルでのメモリ逼迫を避けて3並列とした |

**ビルド所要時間: 約72分**（20:32:47 → 21:44:57 JST、`make -j3`）。
**コンパイルエラーは発生しなかった。**

⚠️ **ビルド前に `cmake` の設定を検証すること。** 本作業では configure 直後に
`CMakeCache.txt` を確認し、`PYTHON_LIBRARY` が `libpython3.13.so` を指していることを
確かめてからビルドを開始した。ここが誤っていると72分が無駄になる。

```bash
grep -E '^(PYTHON_EXECUTABLE|PYTHON_LIBRARY|PYTHON_INSTALL_DIR):' build/CMakeCache.txt
```

### 手順4: インストール

```bash
cd ~/librealsense
sudo make -C build install
sudo cp config/99-realsense-libusb.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
# 実行後 RealSense を USB から抜き差しする
```

⚠️ **`scripts/setup_udev_rules.sh` を使う場合の落とし穴（本作業で実際に踏んだ）:**
このスクリプトは `/dev/video*` が存在すると
`read -p "Remove all RealSense cameras attached. Hit any key when ready"` で**停止する**。
ここで**カメラを抜いてもキーを押さなければ、後続の `cp` に到達せずルールは導入されない**。
本作業ではこれによりルールが入っておらず、`rs-enumerate-devices` が
`RS2_USB_STATUS_ACCESS` で失敗した。
**Pi 4 では、このスクリプトが実際に行う処理は上記の `cp` と `udevadm` の2行と等価である**
（スクリプト内の Tegra / IPU6 向け分岐は Pi 4 では該当しない）ため、
**上記のように直接コピーする方が確実である。**

### 手順5: `.so` の配置問題の解消 ★詰まりやすい箇所

**`make install` の配置先と venv の探索パスは一致しない。**

| | パス |
|---|---|
| インストール先 | `/usr/local/lib/python3.13/dist-packages/pyrealsense2/` |
| venv の site-packages | `~/repos/smart-trashbox-tribute/.venv/lib/python3.13/site-packages/` |

venv は `--system-site-packages` なしで作成しているため、このままでは
**`import pyrealsense2` が失敗する**。これは `research.md` が
「`.so` の配置先が `site-packages` の探索パスに入っていない」として
主要な失敗原因に挙げたものそのものである。

`research.md` / OQ-41 は対処として
「(a) `--system-site-packages` で venv を作る」「(b) `.so` を venv へ配置する」の2案を挙げている。
**本作業では (b) を採った**（明示的で、再ビルド時の再現が容易であり、
venv を作り直しても root 所有ファイルが venv 内に残らないため）。

```bash
cp -rL /usr/local/lib/python3.13/dist-packages/pyrealsense2 \
       ~/repos/smart-trashbox-tribute/.venv/lib/python3.13/site-packages/
chown -R raspi:raspi ~/repos/smart-trashbox-tribute/.venv/lib/python3.13/site-packages/pyrealsense2
```

> ⚠️ **これは暫定の選択であり決定ではない。** OQ-41（Python の環境構築・パッケージ管理方法）は
> タスク9.7 で「決着させない」と定められている。
> ⚠️ **librealsense を再ビルドした場合は、この配置をやり直す必要がある。**

### 手順6: 導通の確認

```bash
rs-enumerate-devices
cd ~/repos/smart-trashbox-tribute && .venv/bin/python -m sensing_foundation.cli doctor
```

#### デバイス情報（`rs-enumerate-devices`）

| 項目 | 値 |
|---|---|
| Name | RealSense D435 |
| **Serial Number** | **834412071095** |
| **Firmware Version** | **5.17.3.10** |
| **Usb Type Descriptor** | **3.2** |
| Physical Port | 2-2-5 |
| Product Id | 0B07 |
| Asic Serial Number | 836313020860 |

> ⚠️ **シリアル番号は2種類あり、どちらを見ているかで値が異なる。**
> `dmesg`（USB ディスクリプタ）が露出するのは **Asic Serial Number（836313020860）**であり、
> `rs-enumerate-devices` や `doctor` が報告する **Serial Number（834412071095）**とは別物である。
> **同一のカメラであるにもかかわらず値が食い違うため、突き合わせの際に混乱しやすい。**
> 記録・照合には librealsense 側の Serial Number を用いること。

#### `doctor` の結果: **全9項目 `ok`**

| 項目 | status | 要旨 |
|---|---|---|
| `os` | ok | Debian GNU/Linux 13 (trixie) / kernel 6.18.34+rpt-rpi-v8 (aarch64), 64bit=True |
| `python` | ok | Python 3.13.5（venv 配下） |
| `memory` | ok | 搭載 3,980,185,600 bytes / 利用可能 3,575,754,752 bytes |
| `sdk_import` | **ok** | **pyrealsense2 2.58.3**、venv の site-packages から解決 |
| `device` | **ok** | 1台検出（serial 834412071095 / FW 5.17.3.10 / usb 3.2） |
| `usb` | **ok** | **USB 3.2 接続を検出** |
| `stream_open` | **ok** | 要求モード **640×480@30fps**（color_enabled=False）でストリームを開けた |
| `power_stability` | **ok** | 30枚の短時間取得で **dropped 0 / missing 0 / acquire_errors 0** |
| `disk` | ok | 空き 474,232,352,768 bytes、書き込み速度目安 **27.2 MB/s** |

> `power_stability` の `detail` が自ら述べる通り、これは
> **「この条件下でのみの観測であり、恒久的な安定性の保証ではない」**。
> 長時間・高 fps での安定性はタスク9.4 / 9.5 で改めて確認する。

### 特記事項

1. ✅ **タスク9.1 で申し送った最大のリスク（trixie / GCC 14.2 / Python 3.13）は顕在化しなかった。**
   librealsense **v2.58.3** は Debian 13 環境でエラーなくビルドでき、
   Python バインディングも `pyrealsense2.cpython-313-aarch64-linux-gnu.so` として
   正しく 3.13 向けに生成された。**OQ-23 の Ubuntu 退避規則は発動していない。**
   ただしこれは v2.58.3 での結果であり、古い版（`research.md` が参照した実例の時代の版）でも
   同じとは限らない

2. ⚠️ **ディスク書き込み速度の実測値には無視できないばらつきがある。**
   タスク9.1 では 31.9 MB/s、本タスクでは 27.2 MB/s と、同一の microSD で
   **約 15% の差**が出た。`doctor` の当該値は「目安」であり、
   **この値を判定基準に使う場合は複数回測って幅を見ること。**
   なお 60fps の Depth（約 36.9 MB/s）はどちらの実測値も下回っているため、
   タスク9.1 の結論（60fps 評価時に連続記録を併用しない）は変わらない

3. `MemTotal` はタスク9.1 の 3,973,906,432 bytes に対し本タスクでは 3,980,185,600 bytes と
   約 6 MB 異なる。起動ごとの GPU メモリ割り当て等による変動であり、
   リングバッファ上限の結論（タスク9.1）に影響しない

4. **`realsense-viewer` は未使用のまま。** ビルドは成功し `/usr/local/bin/realsense-viewer` に
   配置済みだが、本タスクでは CLI での確認だけで導通が確定したため実行していない。
   必要になった際は **Pi のデスクトップ上で実行すること**（SSH 経由では GUI を表示できない）

### 決着した未決事項（タスク9.7 で文書へ反映する）

- **OQ-23（Pi 4 の OS）**: Raspberry Pi OS 64-bit（Desktop 版 / Debian 13 trixie）で確定。
  librealsense v2.58.3 のビルドが通ったため Ubuntu 24.04 LTS への退避は行わなかった
- **OQ-28（RealSense 実機セットアップの成立性）**: 成立。認識・USB3・給電安定性・SDK 導入のすべてを確認済み
- **OQ-24（RAM 容量）**: タスク9.1 で決着済み（4GB モデル）

---

## タスク9.3 live アダプタを実機で通し、契約テストを再実行する

**ステータス: 完了**

### 実施日

2026-08-27

### 実施環境

タスク9.2 の構成（pyrealsense2 2.58.3 / D435 が USB 3.2 接続）。
開発機（WSL, SDK 非導入）と実機の**両方**で実行し、
design.md「Technology Stack」の「live 以外は実機・SDK なしで全通過すること」を
壊していないことを併せて確認した。

### 使用したコマンド

```bash
# 実機（Pi）
cd ~/repos/smart-trashbox-tribute
.venv/bin/python -m pytest tests/sensing_foundation/test_source_contract.py -q -p no:cacheprovider
.venv/bin/python -m pytest tests/sensing_foundation/ tests/prediction_core/ -q -p no:cacheprovider

# 開発機（WSL）
.venv/bin/python -m pytest tests/sensing_foundation/ tests/prediction_core/ -q -p no:cacheprovider
```

⚠️ **`tests/` 全体を指定しないこと。** `flying_object_tracking` が `cv2` を要求し
collection が 18 errors で中断する。OpenCV は `research.md` の決定により
Pi へ導入しないため、これは想定内であり不具合ではない。

### 結果: テスト実行

| 環境 | 契約テストのみ | 全体（`sensing_foundation` + `prediction_core`） |
|---|---|---|
| 開発機（WSL・SDK 非導入） | 8 passed, 4 skipped | **822 passed, 4 skipped**（失敗ゼロ） |
| **実機（Pi・SDK 有り）** | **11 passed, 1 skipped** | **813 passed, 12 failed, 1 skipped** |

実機の 12 failed は**本タスク着手前から存在した失敗**であり、内訳・件数とも
着手前のベースラインと完全に一致する（後述「未解決の課題」）。
本タスクによる新規回帰は無い（着手前 809 passed → 813 passed の差 +4 は
本タスクが追加した live テスト4件）。

### 結果: live の実測値（タスク9.3 が記録を要求する項目）

| 記録項目 | 実測値 |
|---|---|
| **グローバル時刻の有効化** | **成功（`global_time_enabled = True`）** |
| **時刻ドメイン** | **`GLOBAL_TIME`**（30枚すべて。要件 3.5） |
| **取得レイテンシの算出可否** | **算出可能（30/30、欠測ゼロ）**。実測 24.60 / 22.38 / 20.56 ms |
| USB2 警告 | `False`（USB3 接続。タスク9.2 の結果と整合） |
| カメラ内部パラメータ（要件 3.6） | `fx = fy = 385.4710`、`ppx = 319.0791`、`ppy = 240.5483` |
| 歪みモデル・係数 | `distortion.brown_conrady`、**係数はすべて `0.0`** |
| Depth スケール | `1.0000000474974513`（**厳密に 1.0 ではない**） |
| Depth 配列 | `uint16` / `(480, 640)` |
| **取得統計（要件 2.8）** | `frames_yielded=30` / `frames_dropped=0` / `frames_missing=0` / `duration_ms=1724.57` / `measured_fps=17.40` / `acquire_errors=0` |
| `seq` の連続性 | 0〜29 連続（飛びなし） |

> **グローバル時刻が有効化できたことは、レイテンシが算出できたことと表裏である。**
> `RealSenseSource` は `timestamp_domain == GLOBAL_TIME` のときに限り
> `capture_latency_ms` を算出する設計（取れないものを偽装しない。要件 3.5）であり、
> 実機で `GLOBAL_TIME` が得られたため 30 枚すべてでレイテンシが算出できた。
> **`research.md` Research 3 が懸念した「`TIME_OF_ARRIVAL` へのフォールバック」は
> 本構成では発生しなかった。**

⚠️ **`measured_fps`（17.4〜20.5、実行ごとに変動）を性能の判断材料にしないこと。**
30枚（約1.7秒）の短い窓にカメラのウォームアップが含まれるための値であり、
これをもって「Pi 4 が 30fps を出せない」と結論するのは
**方針 A-10「未実測の数値を合否条件にしない」および OQ-27 に反する。**
解像度・fps の評価は**タスク9.4 が実効サンプル数で行う**のが正しい手順である。

### 実装: live を3アダプタ共通の契約に載せた方法

変更したファイルは `tests/sensing_foundation/test_source_contract.py` のみ。

**「合成・再生と同じ契約テスト」の解釈**: design.md「Integration Tests」1 は
契約テストを「`CaptureFrame` の系列が等価になること」と定義し「live は実機タスクで
同じテストを再実行する」と続けるが、**同一の合成フレーム列を live へ流すことは
できない**（実カメラの Depth は合成パターンと一致しないため、
`_assert_series_equivalent(合成系列, live系列)` は原理的に成立しない）。

したがって「同じテスト」を**同じ消費関数と同じ等価性判定を live にも適用すること**と
解釈し、既存の合成版と同じ形の往復で検証した:

```
live → 記録 → 再生 ≡ live
```

再生側は有限なので `_consume()` がそのまま使え、
「種別ごとに別々の消費コードを書かない」という要件 4.2 の主旨も保たれる。

追加・変更点:

- `_take(source, count)` を追加。**live は終端しないため `_consume()` が使えない**
  （`list(source.frames())` が戻らない）。枚数で境界を切る
- `_live_hardware_available()` と skip マーカー2種を追加。`doctor` と同じ
  `probe_sdk()` / `probe_devices()` を経由し、**テストファイル自身は
  `pyrealsense2` を直接 import しない**（design.md 境界テスト2 の趣旨を保つ）
- `_record_frames()` に `source` 引数を追加（manifest の記録元種別）
- live 契約テスト4件を追加（要件 4.2 / 3.5 / 3.6 / 2.8 に1件ずつ対応）

### 発見1: 既存テストが実機でハングしていた

`TestLiveAdapterCaseIsDeferredToTask61::test_live_branch_constructs_but_fails_at_start_without_sdk`
は「SDK が無いとき `SourceUnavailableError` が出る」ことを固定するテストだが、
**実機では SDK が存在するため `start()` が成功し、`_consume()` が終端しない live を
読み続けて戻らなくなる**。着手前の全体実行では、このテストを手動で deselect しないと
スイートが完了しなかった。

本タスクで `@requires_no_live_hardware` を付け、実機では skip されるようにした。
これによりスイートは手動 deselect なしで完了する。

### 発見2: 実機で 12 件のテストが失敗する（**本タスクのスコープ外・未解決**）

**着手前から存在し、本タスクの変更とは無関係**である。すべて
「SDK が存在しない環境」を前提に書かれており、実機では前提が成立しない。

| ファイル | 件数 | 性質 |
|---|---|---|
| `test_doctor.py` | 7 | SDK 非存在時の `fail` / `skip` を固定している |
| `test_realsense_source.py::TestSdkNotInstalled` | 3 | `probe_sdk()["available"] is False` 等 |
| `test_sensing_cli.py::TestStartupValidation` | 1 | SDK 非導入時の CLI 失敗メッセージ |
| `test_sensing_boundaries.py` | 1 | **性質が異なる（下記）** |

代表例（実際の出力）:

```
test_probe_sdk_reports_unavailable_without_raising
    assert result["available"] is False
E   assert True is False
```

**`test_sensing_boundaries.py::test_import_sensing_foundation_succeeds_without_sdk`
だけは他の 11 件と性質が異なり、テスト順序依存である。**

```python
importlib.import_module("sensing_foundation")
assert "pyrealsense2" not in sys.modules
```

| 実行方法 | 結果（実証済み） |
|---|---|
| 単独実行 | **PASS** |
| `probe_sdk()` を呼ぶテストの後に実行 | **FAIL** |

⚠️ **ソース側の遅延 import 設計は正しく動いており、欠陥ではない。**
このテストは**インタプリタのグローバル状態（`sys.modules`）**を観察しており、
開発機では `pyrealsense2` が存在し得ないため常に PASS していた。実機では
先行テストが**正当に** import するため汚染される。design.md 境界テスト2 は
この性質を**静的な**依存方向の検証として定義しており（同ファイル内の他の
境界チェックは AST 解析で実装されている）、要件 4.4 / 12.2 の意図自体は正しい。
**意図を保ったまま検証機構をプロセス分離または静的解析へ変える必要がある。**

> **本タスクではこれらを修正していない。** タスク9.3 の受入基準
> （live 入力で契約テストが通過し取得統計が得られる）を妨げないこと、および
> design.md が「live 以外は**実機・SDK なしで**全通過すること」としか規定して
> おらず、**SDK が存在する環境での扱いは design に未規定**であることによる。
> **この 12 件の扱いは design レベルの判断を要するため、別途タスク化するか
> design.md の改訂で方針を定めること。**

### 発見3: 歪み係数がすべて 0 であることを実機で確認した（下流2 Spec への裏付け）

実測した内部パラメータの `coeffs` は `[0.0, 0.0, 0.0, 0.0, 0.0]` であった。
これは**設計時に文献調査に基づいて置いた仮定を、実機で裏付けるもの**である:

- `src/sensing_foundation/geometry.py`: 「これらの係数が全て `0` で提供されるため、
  現時点では歪み補正の有無が恒等変換となり結果に差が出ない」
- `src/world_frame_calibration/deproject.py` の `ensure_supported_distortion()`:
  歪み係数が**厳密に 0 でなければ** `CalibrationFailure` を送出する

後者は非ゼロ係数を**受理しない**実装であるため、仮定が崩れていれば
`world-frame-calibration` の実機運用が全面的に失敗するところだった。
**実機の D435 Depth ストリームではこの仮定が成立する。**

### 発見4: `stop()` に数百 ms かかる

`measured_fps` の検証を実装する過程で、`with` ブロックを抜けた後まで実時間を
測ると `CaptureMetrics` の計測窓と食い違うことが判明した。差分は約 0.58 秒で、
**RealSense の pipeline 停止に要する時間**である。取得区間の実時間を測る際は
`stop()` を含めないよう区間を揃える必要がある。

### 未実施の項目

なし（タスク9.3 の要求事項はすべて充足）。ただし発見2 は未解決の課題として残る。

---

---

## タスク9.4 解像度・fps を掃引し、設定を決定する

**ステータス: 完了**

### 実施日

2026-08-27

### 実施環境

タスク9.1 の申し送りに従い、**起動ターゲットを `Console`（`multi-user.target`）へ
切り替えてから実施した**。デスクトップ常駐プロセスは 0 件、
メモリ使用量は 355 MiB → 200 MiB（利用可能 3.5 GiB）へ減少。
D435 は USB3（5000M）接続、`throttled=0x0`、温度 37.4 °C。

### 使用したコマンド

```bash
cd ~/repos/smart-trashbox-tribute
.venv/bin/python -m sensing_foundation.cli bench-modes \
    --source live \
    --modes "640x480@30,640x480@60" \
    --duration-s 10 \
    --warmup-s 2 \
    --window-ms 200 \
    --no-recording-enabled \
    --no-logging-enabled
```

**測定条件の選択と根拠:**

| 引数 | 値 | 根拠 |
|---|---|---|
| `--modes` | `640x480@30,640x480@60` | design.md「ModeSweep」が定める候補モード列そのもの（要件 11.2） |
| `--warmup-s` | **2.0** | design.md「ウォームアップ区間を**必ず**設ける」。⚠️ **CLI の既定は 0.0 であり、これは罠である**（後述の発見1） |
| `--duration-s` | 10.0 | CLI 既定の 1.0 では短すぎる（ウォームアップの影響が残る） |
| `--window-ms` | 200.0（既定） | 30fps で1窓 6 枚。比較の分解能として妥当 |
| 記録 | **無効** | タスク9.1 の申し送り: 60fps（約 36.9 MB/s）は microSD 書き込み速度（27〜32 MB/s）を**上回る**ため連続記録を併用しない |
| ロギング | 無効 | 取得性能を単離するため。**ロギングの ON/OFF はタスク9.5 が独立に測る**ため、ここでは両モードで同一条件（無効）に揃えた |

### 結果: 全候補モードの実測値

**2回実施して再現性を確認した。**

| モード | 実測fps | **実効サンプル数/窓** | 取得枚数 | 破棄 | 欠落 | wait p50/p95 (ms) | CPU平均 | RSS最大 | valid |
|---|---|---|---|---|---|---|---|---|---|
| 640×480@30（1回目） | 30.08 | **6.00** | 301 | 0 | 0 | 33.19 / 33.52 | 3.38 % | 235.3 MiB | true |
| 640×480@30（2回目） | 30.00 | **5.98** | 300 | 0 | 0 | — / 33.43 | 3.49 % | 230.6 MiB | true |
| 640×480@60（1回目） | 59.90 | **11.98** | 600 | 0 | 0 | 16.53 / 16.76 | 6.24 % | 410.9 MiB | true |
| 640×480@60（2回目） | 59.85 | **11.96** | 599 | 0 | **1** | — / 16.78 | 4.89 % | 411.5 MiB | true |

- **USB2 は検出されなかった**（`valid=true` / `invalid_reason=null`）。
  したがって要件 1.5 の「USB2 接続時は fps 計測結果を有効なものとして扱わない」に
  該当する回は無い
- 60fps の2回目に `frames_missing=1`（599枚中1件、0.17 %）。1回目は 0 件

### 採用する設定と選定根拠（要件 11.5）

**採用: 640×480 / 60 fps / Color 無効**

**根拠（要件 11.4 の評価軸＝実効サンプル数に基づく）:**

1. **200ms 窓あたりの実効サンプル数が約 6.0 → 約 12.0 とほぼ正確に倍増した。**
   要求 fps に対する達成率も 30fps で 100.2 %、60fps で 99.8 % とほぼ等しく、
   **60fps を要求しても取りこぼしで相殺されない**
2. **破棄ゼロ・欠落ほぼゼロ**（1200枚中1件）。方針 A-9 が警告する
   「高 fps は dropped frame を招けば逆効果」に**該当しなかった**
3. CPU 使用率は 3.4 % → 4.9〜6.2 % に増えるが、**絶対値として十分低い**
4. リングバッファ必要 RAM は 52.7 MiB → 105.5 MiB（3秒保持時）。
   上限（搭載 RAM の 25 % ＝ 947 MiB）の 11.1 % にとどまる（タスク9.1 の検算）

> ⚠️ **これは「60 fps ありき」の選択ではない**（要件 11 Objective）。
> 取りこぼしが出ていれば 30fps を採る判断だった。実際に測って
> 取りこぼしが出なかったから 60fps を採る、という順序である。

**採用しなかった設定**: 640×480/30fps。成立はするが実効サンプル数が半分であり、
上記1〜4より 60fps を選ばない理由が無い。

### 既定値への反映

`src/sensing_foundation/config.py` の `CaptureConfig.fps` を **30 → 60** へ変更した。
docstring に実測の根拠と後述の留保を明記した。

⚠️ **解像度（640×480）の既定値は変更していない。** design.md が定める候補モード列は
640×480 固定の2モードであり（要件 11.2）、**解像度は他の値と一切比較していない**。
したがって `width_px` / `height_px` は**実測の裏付けを持たない初期評価候補のまま**であり、
docstring もその旨を維持している。タスク名は「解像度・fps を掃引し」だが、
実際に掃引したのは fps だけである。

### 留保: この結論が及ぶ範囲（要件 9.6 の遵守）

⚠️ **Pi 4 を継続するかの判断は行っていない**（要件 9.6 / タスク9.4 の明示指示）。
本節が提供するのは材料のみであり、判断は `m1-prediction-validation` に委ねる。

⚠️ **実効サンプル数には「フレーム層」と「点層」の2つがあり、ここで倍増したのは
フレーム層である。** `bench-modes` の出力自身が
「`flying-object-tracking` が定義する『点層』の実効点数とは別物の、対になる指標」と
注記している。検出処理を含めた点層の実効点数は**未測定**であり、
下流の処理が 1 フレーム 16.6 ms に収まらない場合、ドレインが働いて
点層のサンプル数は倍増しない（この場合でも欠落は生じず、余分な取得コストを
払うだけである）。**`flying-object-tracking` の実測後に既定値を再検討してよい。**

⚠️ **fps は依然として合否条件ではない**（要件 11.6）。

### 下流への波及: 保存済みキャリブレーション結果との整合性

`world-frame-calibration` の `check_compatibility()` は、保存されたキャリブレーション
結果の**解像度・fps・Depth スケール・Color 有無**を現在の入力元のそれと突き合わせ、
不一致なら変換を有効なものとして扱わずに失敗させる（同 Spec タスク4.2）。

したがって **30fps で取得したキャリブレーション結果は、60fps の live 入力に対して
`PROFILE_MISMATCH` として失敗する。** これは「古い結果の使い回しが静かにずれる」ことを
防ぐための**設計どおりの正しい挙動**であり、不具合ではない。

現時点で実害は無い（`world-frame-calibration` のタスク8＝実機でのキャリブレーションは
実機到着待ちでブロックされており、**保存済みの結果がまだ存在しない**）。
ただし**実機でキャリブレーションを実施する際は、本タスクで採用した 60fps で
行うこと**。30fps で取ってしまうと運用時に整合性検査で弾かれる。

### 発見1: `--warmup-s` の既定値 0.0 は罠である

design.md「ModeSweep」の Input / validation は
「**ウォームアップ区間を必ず設ける**」と定めているが、
CLI の `--warmup-s` の既定値は **0.0**（＝ウォームアップ無し）である。

タスク9.3 で観測した `measured_fps` 17.4〜20.5（30fps 要求時）は、
まさにこのウォームアップを含む短窓の測定値であった。本タスクで
`--warmup-s 2 --duration-s 10` として測り直すと **30.08 fps** となり、
**当時の値は性能限界ではなくウォームアップの影響だったことが確定した。**

> **本タスクではこの既定値を変更していない**（タスク9.4 の「既定設定」は
> 解像度・fps の採用値を指しており、CLI 引数の既定は対象外と解釈した）。
> ただし **0.0 のまま `bench-modes` を実行すると design.md の要求を満たさない
> 測定になる**ため、既定値を非ゼロにするか、0.0 を指定した場合に警告を出すことを
> 検討する価値がある。

### 発見2: `world-frame-calibration` の境界テストと衝突する（**未解決・別課題**）

`src/sensing_foundation/config.py` を変更したことで、
`tests/world_frame_calibration/test_world_frame_calibration_boundaries.py::test_actual_working_tree_changes_since_main_stay_within_boundary`
が失敗するようになった。

```
AssertionError: 禁止ディレクトリへの変更を検出した: ['src/sensing_foundation/config.py']
```

当該テストは `main` からのブランチ全変更に対して
`FORBIDDEN_BOUNDARY_PREFIXES = ("src/prediction_core/", "tests/prediction_core/", "src/sensing_foundation/")`
への変更を禁止する。**意図（world-frame-calibration の実装が上流パッケージへ
侵食しないことの検証。同 Spec タスク7.3）は正しい**が、
**ブランチが単一 Spec のものであることを前提としている**ため、
sensing-foundation 自身の作業ブランチで誤検出する。

| ブランチ上の変更 | 抵触 |
|---|---|
| タスク9.1〜9.3（`.kiro/**`, `tests/sensing_foundation/**`） | しない |
| **タスク9.4（`src/sensing_foundation/config.py`）** | **する** |

> **本タスクでは修正していない。** sensing-foundation の作業中に
> world-frame-calibration のテストを書き換えることは、**まさにその境界テストが
> 防ごうとしている越境そのもの**であり、kiro-impl の
> 「root cause が別 Spec に属する場合は下流で回避策を当てず所有元へ差し戻す」
> という指示にも反する。
> **`world-frame-calibration` 側のタスクとして起こし、マージ前に解決すること。**
> 検討しうる方向: 対象ブランチが自 Spec のものかを判定して skip する、
> 比較対象を「ブランチ全体」から「自 Spec が触ったコミット」へ絞る、など。

### 未実施の項目

なし（タスク9.4 の要求事項はすべて充足）。ただし発見1・発見2 は課題として残る。

---

---

## タスク9.5 計測 ON / OFF の影響を実機で確認する

**ステータス: 設計判断は決着済み・実測は未実施**（design.md を A 案で改訂し、実装をタスク 7.4 として起こした。実測には実機が要る）

### 実施日

- 2026-08-27: 着手し、着手時点の構造では実行不能であることを確認した
- 2026-08-28: 3案を比較して A案を採用、design.md を改訂し、実装をタスク 7.4 として起こした

### 結論

**`bench-logging` は着手時点の構造では live に対して実行できなかった。**
3条件ぶんの `FrameSource` を同時に開く構造であり、RealSense は1デバイスにつき
1パイプラインしか開けないためである（以下「症状」「根本原因」を参照）。

**この点は 2026-08-28 に決着した。** 3条件で入力元を1本だけ開いて共有する構造（A案）を採り、
`design.md` の「LoggingOverheadBench」節へ根拠とともに明文化した（下の「決定」節を参照）。
**実装はタスク 7.4、実機での実測はタスク9.5 に残っている。**

### 症状

```bash
.venv/bin/python -m sensing_foundation.cli bench-logging \
    --source live --segment-s 0.2 --cycles 1
```

```
実行エラー: 要求したモード（640x480@60fps, color_enabled=False）で
ストリームを開けなかった。`doctor` の stream_open を確認せよ。
```

### 根本原因（実測で切り分け済み）

エラー文は「モードを開けなかった」だが、**モードの問題ではない**
（タスク9.4 で 640×480@60fps は 600 枚の取得に成功している）。
切り分けのために単独オープンと同時オープンを直接比較した:

| 試行 | 結果 |
|---|---|
| 640×480@60fps を**単独で**オープン | **成功**（`seq=0`, `shape=(480, 640)` を取得） |
| 同じモードを**2本同時に**オープン | 1本目は成功、**2本目が `DeviceNotReadyError`** |

`LoggingOverheadBench.run()` は3条件ぶんの `FrameSource` を
**同時に開いたまま**セグメントを回す:

```python
with source_off, source_on, source_rec:
```

**RealSense は1デバイスにつき1パイプラインしか開けない**ため、
live では2本目のオープンで失敗する。

### これは実装の誤りではなく、設計判断の帰結である

`logging_overhead.py` のモジュール docstring は、この同時オープンを
明示的な設計判断として説明している:

> 各条件の `FrameSource` は**最初に1度だけ** `open_source()` で構築し、
> 以後は同じイテレータから `cycles` 回ぶん少しずつ引き出す
> （**毎セグメントで開き直さない**——同一の入力元から連続して取り出すことで
> 「同一入力元」を文字通り満たす）。

これは要件 10.1「同一条件で比較する」を満たすためのタスク7.3 の判断であり、
**simulated / recorded では正しく機能する**（3つの独立した入力元が共存できるため）。
成立しないのは live に対してだけである。

さらに、**仮に3本開けたとしても測定は無意味になる。**
1台のカメラに3本のパイプラインが競合すれば各条件が得るフレームが
互いに干渉し、「同一条件で比較する」という前提自体が崩れる。
したがってこれは「開けるようにすれば済む」問題ではない。

### 決定: A案（1本の入力元を共有する）— 2026-08-28

3案を比較し、**A案（3条件で `FrameSource` を1本だけ開いて共有し、セグメントごとに
ロガーの向き先だけを差し替える）**を採用した。`design.md` の「LoggingOverheadBench」節へ
「3条件が入力元を共有する理由」を追記し、Batch/Job Contract の「同一入力元」が
**1本の共有**を指すことを明文化した。実装はタスク 7.4 として起こし、タスク9.5 は
`_Depends: 9.4, 7.4_` とした。

| 案 | 同一入力元 | A/B/A/B | 測定の妥当性 | 判定 |
|---|---|---|---|---|
| **A** 1本を共有しロガーを差し替え | ✅ 文字通り満たす | ✅ 保てる | ✅ パイプラインを温めたまま回せる | **採用** |
| B セグメントごとに開き直す | △ | ✅ | ❌ 全セグメントがウォームアップ区間になる | 不採用 |
| C 交互実行を諦める | ✅ | ❌ 契約違反 | △ 順序効果・熱ドリフトが残る | 不採用 |

**B を退けた理由は開閉コスト（約 0.5 秒）ではない。** タスク9.4 で、30fps 要求時に
ウォームアップ無しでは 17〜20fps、2秒のウォームアップ後では 30.08fps という差を実測している。
セグメント長は秒未満〜数秒なので、毎セグメントで開き直すと**測っているものが
ロギング負荷ではなくパイプライン起動の過渡応答になる**。

**C を退けた理由**は、`design.md` の Batch/Job Contract が定める A/B/A/B を放棄することになり、
順序効果と（Pi 上での長時間実行では）熱ドリフトを打ち消せなくなるためである。A が使える以上、
契約を緩める理由がない。

#### 本記録の以前の版にあった誤りの訂正

本節には当初、A案について「`CaptureMetrics` は構築時にロガーを束ねるため、ロガーの
差し替え口を作る必要があり `metrics` / `source` へ波及する」と書いていた。**これは誤りである。**

`obslog.py` の `Logger` は `Protocol`（構造的部分型）として定義されており
（`enabled` / `emit` / `stage` / `timed` / `stats` / `close` の6要素）、この形を満たす
切り替え可能なロガーをベンチ側の私有クラスとして置けば、`CaptureMetrics` はそれを
そのまま `Logger` として受け取る。**`metrics.py` も `source.py` も変更不要**であり、
変更は `src/sensing_foundation/bench/logging_overhead.py`（タスク7.3 自身の境界）に閉じる。

波及範囲を過大に見積もったまま案を比較していたため、訂正して記録する。

#### A案が現行実装より design の意図に忠実である点

現行実装は3つの**別々の**入力元インスタンスを開いている。`logging_overhead.py` の
モジュール docstring が述べる意図は「毎セグメントで開き直さない」ことであり、
A案は**文字通り1本のストリーム**から取り出すため、その意図をより厳密に満たす。

#### 許容する副作用

入力元を共有すると、3条件は入力の**互いに素な区間**を処理する。条件ごとに別インスタンスを
開いていた現行では `simulated` / `recorded` で3条件が同一内容を見ていたが、共有ではそうならない。

これは **A/B/A/B の交互実行と複数サイクルが打ち消すべき非定常性**であり、交互実行が
存在する理由そのものである。また live では区間が互いに素になることは不可避であるため、
`simulated` / `recorded` を同じ構造で回すことは、実機実行の予行演習としてはむしろ忠実になる。

### 未実施の項目

タスク9.5 の要求事項すべて。実測値・判定基準の適用・判定結果はいずれも未取得。
**判定基準の文字列自体は実測前に固定済みであり、この決着によって変更しない**
（要件 10.3 / 方針 A-10）:

> ON 条件の total_ms 中央値と OFF 条件の中央値の差が、OFF 条件の
> 四分位範囲（IQR）以内であり、かつ frames_dropped が増えていないとき
> 「有意に変化しない」と判定する。

---

---

## タスク9.6 実データを記録し、WSL で再生する往復を確認する

**ステータス: 完了**

### 実施日

2026-08-28（JST）。セッション識別子は UTC 表記のため `20260827T152644Z` となる。

### 実施環境

| 役割 | 機材 | SDK | 実機接続 |
|---|---|---|---|
| 記録側 | Raspberry Pi 4 Model B Rev 1.2（4GB）/ Raspberry Pi OS 64-bit trixie / Python 3.13.5 | librealsense 2.58.3 あり | D435 接続あり（USB 3.2） |
| 再生側 | WSL2 Ubuntu / Python 3.11.16 | **なし** | **なし** |

再生側に SDK も実機も無いことは実測で確認した（要件 6.3）。

```
$ uv run --extra sensing python -c "from sensing_foundation.sources.realsense import probe_sdk, probe_devices; print(probe_sdk()); print(probe_devices())"
{'available': False, 'version': None, 'location': None, 'error': 'pyrealsense2 を import できない。…'}
{'available': False, 'devices': [], 'error': 'pyrealsense2 を import できない。…'}
```

記録側と再生側で `src/sensing_foundation/**` の 26 ファイルが同一であることを sha256 で確認済み。

### 使用したコマンド

**1. 構図確認（Pi、投擲不要）**

モニタの無い Pi で画角を確認するため、使い捨ての診断ツールを用いた（`sensing_foundation`
の公開経路のみを使い、パッケージ本体には何も足していない）。

```
$ .venv/bin/python /tmp/aim_check.py --background-s 12 --motion-s 0
[stream] 640x480@60 depth_scale=1.0000000474974513 mm/count
[stage1] 背景 716 枚から作成。測距できた画素 96.5%
[stage1] 距離 5%tile=1.55m 中央=2.21m 95%tile=2.60m
```

深度マップの下端4行に手前へ向かう勾配が出ており、床が画角に入っていることを確認した
（`world-frame-calibration` 要件 5.4「床面を見込む角度が浅すぎる場合は失敗」を避ける条件）。

**2. 本記録（Pi、実際の投擲を含む）**

```
$ .venv/bin/python -m sensing_foundation.cli record \
      --source live --recording-mode ring \
      --ring-seconds 15 --duration-s 15 --compression zlib
```

**3. WSL への転送と完全性確認**

```
$ scp -r raspi@192.168.0.11:~/repos/smart-trashbox-tribute/var/sessions/20260827T152644Z-4f058354 \
      var/real-sessions/
$ sha256sum manifest.json frames.ndjson depth.bin summary.json   # 両機で一致を確認
```

**4. WSL での再生往復検証**

```
$ SENSING_REAL_SESSION_DIR=var/real-sessions/20260827T152644Z-4f058354 \
      uv run --extra sensing pytest tests/sensing_foundation/test_real_session_roundtrip.py -q
9 passed in 18.08s
```

生データは版管理しない（`var/` は `.gitignore` 済み）。検証テストは環境変数
`SENSING_REAL_SESSION_DIR` が与えられたときのみ実行し、無い環境では skip する
（タスク9.3 が実機の有無で `skipif` した構造と同じ）。

### 結果: 記録

| 項目 | 値 |
|---|---|
| セッション識別子 | `20260827T152644Z-4f058354` |
| 記録方式 | ring（リング長 15 秒 = 取得時間） |
| 取得枚数 / 書き出し枚数 | 900 / 900 |
| 破棄 / 欠落 / 取得失敗 / 書き込み失敗 | 0 / 0 / 0 / 0 |
| ストリーム | 640×480@60fps, Color 無効, `depth_scale_mm`=1.0000000474974513 |
| 圧縮 | zlib（553 MB → 124,566,829 B、約 4.4 倍） |
| 時刻ドメイン | 全 900 行が `global_time` |
| 取得レイテンシ | 算出可能（先頭 16.19 ms / 末尾 12.25 ms） |
| 内部パラメータ | fx=fy=385.4710, ppx=319.0791, ppy=240.5482, coeffs 全 0.0 |

**実効的な取得レートは 59.9 fps。** 索引の先頭 `t_capture_ms`=588.221 と末尾 15596.836 から、
899 間隔で 15008.6 ms、すなわち 16.695 ms/フレーム。**15 秒間 60fps を破棄0・欠落0で維持できた。**

> ⚠️ `summary.json` の `measured_fps` は 10.04 と出るが、これは**採用してはならない**。
> `CaptureMetrics` の計測窓が「構築時刻 → `stats` 読み取り時点」であり、取得後の
> zlib 圧縮と書き出し（約 74 秒）を含んでしまうためである（タスク9.3 発見4・9.4 発見1 と同根）。
> 実効レートは上記のとおり索引の時刻から算出すること。

### 結果: WSL での再生往復検証（9件すべて通過）

| 検証 | 要件 | 内容 |
|---|---|---|
| 記録元が実機であること | 9.6 前提 | `manifest.source == "live"`、内部パラメータが非 `None` かつ焦点距離が正 |
| 2回再生の系列一致 | 6.2 | 900 枚をロックステップで全フィールド比較（`t_capture_ms` を除く） |
| 索引との一致 | 6.1 | 通し番号・フレーム番号・デバイス側時刻・時刻ドメイン・取得レイテンシ・破棄／欠落件数 |
| Depth 内容の一致 | 6.1 | `depth.bin` から素の `zlib` で独立に再構成した配列と全 900 枚が一致 |
| 中身の非空性 | 6.1 | 一様な値で埋まっていない（実際に測距した内容がある） |
| メタ情報の取得経路 | 6.4 | `FrameSource.profile` から解像度・fps・Depth スケール・内部パラメータを取得 |
| Throw Record の対応付け | 7.7 | `link_to_session()` → 保存 → 読み戻し → セッション識別子から記録側のフレーム範囲（225..450 の 226 枚）を引けること |

**検証を「同じ経路」で行わない設計にした。** `RecordedSource` は内部で `SessionReader` を
使うため、再生結果を `SessionReader` で読み直して突き合わせても同じコードが同じ答えを
返しただけになる。期待値は `frames.ndjson` / `depth.bin` / `manifest.json` から素の
`json` と `zlib` で独立に組み立て直している（タスク9.3 の申し送りの適用）。

### 負の対照: 表明が恒真でないことの実証

`SessionReader.read()` に意図的な欠陥（フレーム 300 の Depth の 1 画素だけ書き換え）を
注入して再実行した。結果は予測どおり:

```
FAILED ...::test_replayed_depth_matches_blob_reconstructed_independently
FAILED ...::test_linked_record_round_trips_and_resolves_to_the_frame_range
2 failed, 7 passed
```

- ❌ 独立参照と突き合わせる2件は**検出した**
- ✅ `test_two_replays_produce_equivalent_series` は**通過した**

**最後の1点が独立参照を用意した理由そのものである。** 2回の再生を互いに比べるだけでは、
両方が同じように壊れる欠陥を原理的に検出できない。

あわせて、合成入力で作った記録を与えると「実機由来」ゲートが発火することも確認した
（`test_manifest_records_live_as_the_source` ほか 6 件が失敗）。

### 記録内容の性質（投擲の分離は本 Spec の責務ではない）

記録に実際の動きが含まれることは確認した。背景差分の「熱いブロック数」で見て
ノイズ下限（中央値 14）に対し最大 96 と約 7 倍の信号があり、時系列に明確な山が 3 箇所ある。

ただし**投擲物そのものを分離できたとは主張しない。** 最も強い区間は距離約 1.0m・画面右中央・
継続 1.8 秒および 6.1 秒であり、投擲物の飛行時間（200〜400 ms 程度）よりはるかに長い。
投げている人の腕や体である可能性が高い。

> **下流（`flying-object-tracking`）への申し送り**: 2.4m 先の紙ボール（直径約 7cm）が
> 占める面積は fx≈385px から見積もって**約 8px 四方 = 面積 64px 程度**しかなく、
> 本シーンで実測した背景差分のノイズ下限を下回る。**640×480 で 2m 級の距離では、
> 背景差分だけで投擲物を分離するのは困難である。** 投擲距離を詰めるか、
> 時間方向の情報（フレーム間差分・軌道の連続性）を使う方式が要る。

物体検出は `flying-object-tracking` の責務であり、`sensing-foundation` は
「フレームを取得・記録・再生する」層である（タスク8.1 も投擲検出トリガを明示的に範囲外としている）。
**タスク9.6 の完了条件は「再生の往復」であって「物体の検出」ではない。**

### 発見1: `frame_index_from`/`frame_index_to` の意味が未定義（**タスク 4.7 で決着**）

同じ「フレーム番号」に見える 3 つの量があり、**リングが古いフレームを追い出したときだけ食い違う**。

| 量 | 定義 | 追い出し時の値（例） |
|---|---|---|
| `SessionReader.read(i)` の `i` | 索引ファイルの**行位置** | 0..59 |
| 索引行の `i` フィールド | **記録**セッションの通し番号 | 121..180 |
| `RecordedSource` が返す `CaptureFrame.index` | **再生**セッションの 0 始まり通し番号 | 0..59 |

実測（タスク9.6 のスモーク記録 `20260827T150236Z-04c2bd88`、181 枚取得して直近 60 枚を保存）:

```
$ head -1 frames.ndjson  →  {"i": 121, "seq": 121, ...}
$ tail -1 frames.ndjson  →  {"i": 180, "seq": 180, ...}
$ wc -l frames.ndjson    →  60
```

`link_to_session(record, session_id, frame_index_from, frame_index_to)` の
`frame_index_*` がこのどれを指すのかは、**`design.md` にも `requirements.md` にも定義が無い**
（`design.md` L1178・L1187・L1491 はシグネチャと格納先を書くのみ）。

**影響**: 投擲だけを残すリングバッファ運用（要件 5.5）は**まさに追い出しが起きる使い方**であり、
食い違いが常態化する。下流が「記録側の通し番号」のつもりで値を入れ、利用側が
`SessionReader.read()` へ行位置として渡すと、**静かにずれた範囲を読む**。

**タスク9.6 の時点では是正しなかった。** `SessionReader` はタスク4.4、`link_to_session` は
タスク5 の境界であり、どちらの意味を正とするかは design レベルの判断を要したためである。
当時の検証テストには前提を明示する表明（`index_rows[0]["i"] == 0`）を置き、
追い出しのある記録では前提が崩れることをコメントに残した。

なお `types.py` は `CaptureFrame.index` を「セッション内の 0 始まり通し番号。欠番なく増加する」
と定義しており、**この不変条件を満たすのは `RecordedSource` の側で、`SessionReader.read()`
が返す値（121 始まり）は満たさない**。是正の起点はここになると思われる。

#### 決着（タスク 4.7 / 2026-08-28）

`frame_index_from` / `frame_index_to` は **「記録側の通し番号」（索引行の `i`。記録時の
`CaptureFrame.index`）であり、両端を含む閉区間**と定めた。`design.md`「SessionReader /
『フレーム番号』が指す3つの量」と「ThrowRecordStore」へ明文化した。

**行位置ではなく記録側の通し番号を採った理由**: 要件 7.7 が求めるのは「後から対応付けられる
**識別子**」である。行位置はファイル内の位置であって識別子ではなく、記録を切り詰めれば
同じ値が別のフレームを指す。記録側の通し番号は `seq` / `t_capture_ms` と同じく
**記録に書き込まれた事実**であり、読み出し方に依存しない。

**併せて行ったこと**:

- `SessionReader` に記録側通し番号で引く経路を追加（`recorded_index_range` /
  `position_of()` / `read_recorded()` / `iter_recorded_range()`）。取り違えは
  静かにずれた範囲を返さず `IndexError` になり、メッセージに実在する範囲を含める
- 索引行の `i` の重複を構築時に拒否（同じ識別子が2つのフレームを指す記録は読み出せない）
- `types.CaptureFrame.index` の不変条件が**`FrameSource` が下流へ渡すフレーム**に
  適用されるものであることを明記（`SessionReader.read()` の戻り値は対象外）
- 検証は `tests/sensing_foundation/test_frame_index_contract.py` で、**実際に追い出しを
  起こした記録**（20枚取得・6枚保持 → 記録側通し番号 14..19）に対して行う。
  追い出しが無いと3つの量が一致して検証が空振りするため、空振りしていないことを
  最初のテストで確かめてから本題に入る構成にした

### 発見2: live 記録の manifest にデバイス識別情報が無い（**要件 5.2 未充足・未解決**）

実機で記録した manifest の `device` が `null` である。

```json
"device": null,
"runtime": {"os": "Linux", "os_release": "6.18.34+rpt-rpi-v8",
            "python_version": "3.13.5", "hostname": "raspberry-pi",
            "global_time_enabled": null}
```

要件 5.2 は「記録に、使用した解像度・フレームレート・入力元種別・カメラ内部パラメータ・
記録開始時刻・**デバイス識別情報**を含むメタ情報を伴わせる」と定めている。
`SessionRecorder` は `device` 引数を受け取る用意があり、`sources/realsense.py` の
`probe_devices()` は `serial_number` / `firmware_version` / `usb_type_descriptor` を返せるが、
**`cli.run_record()` が `device=None` を固定で渡している**（`cli.py` の `SessionRecorder(...)` 構築箇所）。

**影響**: どの個体・どのファームウェアで撮った記録なのかが後から追えない。
D435 のシリアル（実測 834412071095）とファームウェア（5.17.3.10）はタスク9.2 で
`doctor` から取得できることを確認済みであり、情報が無いのではなく**渡していない**。

### 発見3: manifest の `global_time_enabled` が常に `null`

`_build_runtime_info()`（`cli.py`）が `"global_time_enabled": None` を固定で返す。
入力元を開く前に組み立てているため、実際の有効化結果を入れる経路が無い。

タスク6.1 は「グローバル時刻の有効化を試み、**有効化できたかどうか**をメタ情報とログに残す」と
定めており、`RealSenseSource` は `_global_time_enabled` を保持しているが manifest へ届かない。

**影響は限定的**である。本記録では索引行の `ts_domain` が全行 `global_time` であることから
有効化に成功したと判断できる。ただし「メタ情報に残す」という要件の字義は満たしていない。

> 発見2・発見3 はいずれも **`cli.py`（タスク8.1）の境界**にあり、本タスク（9.6）の
> 要件（5.1 / 6.1 / 6.2 / 6.3 / 7.7）には含まれない。**実機でしか観測できない欠陥**であり、
> タスク9.7 またはタスク8.1 への差し戻しとして扱うこと。

### 未実施の項目

- 発見2・発見3 の是正（タスク 6.3 / 8.3 として起票済み。実機での確認が要る）
  - 発見1 はタスク 4.7 で決着済み（上記「決着」節）
- リングが追い出す条件下での**実機記録**による再生往復検証。合成入力での
  追い出し検証はタスク 4.7 が `test_frame_index_contract.py` で固定したが、
  実機で撮った追い出しあり記録に対しては未実施
- `replay-session` サブコマンド経由での往復（本タスクは `open_source()` 契約で検証した。
  CLI 経由の系列一致は `run_replay_session()` が枚数一致しか見ないため、
  系列の等価性は検証できない）

---

## タスク9.7 決着した未決事項をプロジェクト文書へ反映する

**ステータス: 未着手**（タスク9.5・9.6 の完了が前提）

決着させる対象: OQ-23・OQ-24・OQ-25・OQ-28・OQ-32・OQ-35
（**OQ-27・OQ-40・OQ-41 は決着させない**）。

進捗:
- **OQ-24（RAM 容量）**: タスク9.1 で実測済み（4GB モデル）。決着可能
- OQ-23（OS 選定）: タスク9.2 のビルド成否をもって確定する
