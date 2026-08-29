"""実測項目の算出を担うサブパッケージ（design.md「File Structure Plan」）。

M1 実測7項目（`docs/requirements.md §8 M1`）を項目ごとのモジュールへ分ける。
現時点では `flight.py`（実測項目 1 / 2 / 6）と `accuracy.py`（項目 4 / 5）が
存在し、`convergence.py`（項目 7）・`latency.py`（項目 3 と §13.1）・
`aggregate.py`（投擲群への束ね直し）はタスク 4.4〜4.6 で追加される。

本ファイル自体はサブパッケージのマーカーであり、ロジックを持たない。
公開 API の再エクスポートは行わない——下流が参照してよい入口は
`m1_validation/__init__.py` ただ1つである（同ファイルの docstring 参照）。
"""

from __future__ import annotations

__all__: list[str] = []
