"""実測項目の算出を担うサブパッケージ（design.md「File Structure Plan」）。

M1 実測7項目（`docs/requirements.md §8 M1`）を項目ごとのモジュールへ分ける。
現時点では `flight.py`（実測項目 1 / 2 / 6）・`accuracy.py`（項目 4 / 5）・
`convergence.py`（項目 7 と収束の判定規則）・`latency.py`（項目 3 と §13.1 の
段階別レイテンシ・end-to-end・資源使用）・`aggregate.py`（投擲群への束ね直し。
キャリブレーション識別子ごとに分けた代表値・ばらつき・試行数・暫定の印）が
存在する。

本ファイル自体はサブパッケージのマーカーであり、ロジックを持たない。
公開 API の再エクスポートは行わない——下流が参照してよい入口は
`m1_validation/__init__.py` ただ1つである（同ファイルの docstring 参照）。
"""

from __future__ import annotations

__all__: list[str] = []
