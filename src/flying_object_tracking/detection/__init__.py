"""検出層（L3〜L5）のサブパッケージ（design.md「File Structure Plan」）。

現時点ではタスク 2.1（`mask_ops`）のみが存在する。`MaskBuilder` / 3方式
（`masks/*`）/ `Detector` / `CandidateExtractor` はタスク 2.2〜2.7 で追加される。
公開 API の再エクスポートはタスク 8.1（パッケージ全体の公開入口確定）まで
行わない。
"""

from __future__ import annotations

__all__: list[str] = []
