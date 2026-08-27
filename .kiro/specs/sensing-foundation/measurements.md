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
