"""入力元アダプタのサブパッケージ（design.md「File Structure Plan」）。

`simulated.py`（タスク 3.2）・`recorded.py`（タスク 4.5）・`realsense.py`
（タスク 6.1）を束ねるパッケージマーカー。本 `__init__.py` 自体は再エクスポート
を行わない（`sensing_foundation.__init__` と同様、公開 API の整理は
タスク 8.2 の責務）。
"""

from __future__ import annotations
