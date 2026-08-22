"""world-frame-calibration: Depth 由来の床平面と2マーカーから
カメラ座標系→World 座標系の剛体変換を確立し、独立検証するパッケージ。

このパッケージは Python 標準ライブラリと `numpy`（`calibration` extras）、
および `sensing_foundation` の公開入口のみに依存し、ハードウェアを接続しない
環境（合成 Depth・記録済みセッション）で完結して動作する
（design.md「Allowed Dependencies」/ 要件 9.1, 9.2, 11.5）。

現時点ではパッケージの入口（骨組み）のみを提供する。公開 API の再エクスポート
は各コンポーネントの実装が出そろった後のタスクで行う（design.md
「File Structure Plan」/ tasks.md タスク 6.3）。それまでこのモジュールは
ロジックを持たない。
"""

from __future__ import annotations
