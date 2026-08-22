"""前景マスク生成方式のサブパッケージ（design.md「File Structure Plan」）。

design.md「L4-L5: 検出 → MaskBuilder と3方式」は `MaskBuilder` プロトコルと
生成口（`create_mask_builder`）をこのモジュールに置くと定めているが、それは
タスク 2.5（3方式が出そろった後の統合）の責務である。本タスク（2.2）は
`depth_band.py`（距離帯ゲート方式）1つだけを追加するため、ここではサブ
パッケージとして import 可能にするだけに留める。

`MaskBuilder` は本タスクでは明示的な `Protocol` クラスとして定義しない。
`DepthBandMask`（`depth_band.py`）は構造的に `kind` / `ready` / `build` /
`reset` を満たすダックタイピングの実装であり、これは本リポジトリの既存の
慣習（例: `sensing_foundation.source.BaseFrameSource` が `FrameSource` を
継承せず構造的に満たす）に倣っている。タスク 2.5 で `frame_diff.py` /
`background.py` が揃った時点で、この `__init__.py` に `MaskBuilder` /
`create_mask_builder` を追加する。
"""

from __future__ import annotations

__all__: list[str] = []
