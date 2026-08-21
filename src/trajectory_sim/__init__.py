"""trajectory-simulator: 投擲条件・観測条件・移動体性能からキャッチ可能領域を
算出する Python バッチシミュレータ。

このパッケージは Python 標準ライブラリと `prediction_core` の公開 API のみに
依存し、ハードウェアを接続しない環境で完結して動作する
（design.md「Allowed Dependencies」/ 要件 8.5, 11.3）。

現時点ではパッケージの入口（骨組み）のみを提供する。公開 API の再エクスポート
は各コンポーネントの実装が出そろった後のタスクで行う（design.md
「PublicApi」/ tasks.md タスク 5.5）。それまでこのモジュールはロジックを持たない。
"""

from __future__ import annotations
