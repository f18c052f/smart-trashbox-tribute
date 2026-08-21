"""sensing-foundation: RealSense 由来のセンシング基盤パッケージ。

このパッケージは公開 API の再エクスポート専用となる予定であり、ロジックを
持たない（design.md「PublicApi / Responsibilities & Constraints」）。
`__all__` の明示的な列挙と再エクスポートはタスク 8.2 で行う。現時点は
`import sensing_foundation` が成立することのみを保証するプレースホルダ
である。
"""

from __future__ import annotations
