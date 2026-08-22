"""flying-object-tracking: Depth フレーム列から飛来物体を検出し、
カメラ座標系での3D位置を追跡してカメラ座標点列を下流へ受け渡すパッケージ。

**現時点はタスク 1.1（パッケージ骨組み）であり、公開 API は未確定である。**
このモジュールは最終的に公開 API の再エクスポート専用となる予定だが
（design.md「PublicApi / Responsibilities & Constraints」）、型・設定・
検出・追跡の各モジュールはまだ存在しない。公開シンボルの確定はタスク 8.1
で行う。それまでは `import flying_object_tracking` が成立することだけを
保証する（要件 11.1 / 11.4）。

このパッケージは `sensing_foundation` の公開入口のみに依存し、
`prediction_core` には依存しない（design.md「Allowed Dependencies」）。
サードパーティ依存（`numpy` / GUI 無し OpenCV）は任意依存グループ
`tracking` として宣言し、`[project].dependencies` はゼロのまま維持する。
"""

from __future__ import annotations

__all__: list[str] = []
