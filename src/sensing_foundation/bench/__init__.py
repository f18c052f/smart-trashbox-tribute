"""解像度・fps 掃引（`modes.py`）と計測 ON/OFF 比較（`logging_overhead.py`、未実装）の集約先。

design.md の File Structure Plan（`bench/` ディレクトリ）に従うパッケージ
マーカー。design.md「PublicApi」節が明記する通り、`bench` 自体は公開 API
（`src/sensing_foundation/__init__.py` の再エクスポート）には出さない
（CLI サブコマンドとして使うのみ）。ロジックは持たない。
"""

from __future__ import annotations
