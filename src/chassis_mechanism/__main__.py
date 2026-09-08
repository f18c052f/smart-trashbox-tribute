"""`python -m chassis_mechanism` の入口（design.md「Directory Structure」）。

⚠️ **ロジックを持たない。** 引数の解析・処理・終了コードの決定はすべて
`cli.main` にあり、本モジュールはその戻り値をプロセスの終了コードにするだけで
ある——2箇所目の入口を作れば、片方だけが終了コードの表から外れる。

⚠️ **形状ライブラリを import しない**（`cli` の遅延 import が保つ性質を、
入口の側で壊さない）。`import chassis_mechanism.__main__` が
`import build123d` へ到達しないことは `test_chassis_boundaries.py` が静的に
検査する。
"""

from __future__ import annotations

import sys

from chassis_mechanism.cli import main

if __name__ == "__main__":
    sys.exit(main())
