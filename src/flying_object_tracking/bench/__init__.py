"""検出方式の比較・計測 ON/OFF 比較を置くサブパッケージ（design.md「File Structure
Plan」の `bench/` ディレクトリ、タスク 7.1）。

`compare.py`（`DetectorComparison`。本タスク）と `overhead.py`（`OverheadBench`。
タスク 7.2、本タスクでは実装しない）を置く。本 `__init__.py` は最小のパッケージ
マーカーであり、公開シンボルの再エクスポートは行わない（`compare.py` /
`overhead.py` を直接 import して使う。design.md「公開 API を `__init__` の
1点に集約する」はパッケージ全体（`flying_object_tracking` トップレベル）の
方針であり、`bench` サブパッケージ自身が独自の集約点になる想定ではない
——同様の構造を持つ `detection/masks/__init__.py` はレジストリ関数を持つが、
`bench/__init__.py` にはその種の共有ロジックが無いため空のままにする）。
"""

from __future__ import annotations
