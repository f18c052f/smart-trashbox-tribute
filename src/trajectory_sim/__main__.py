"""`python -m trajectory_sim` の入口（design.md「Cli」）。

実処理は一切持たない薄いラッパーであり、全ロジックは `cli.main` に置く
（`cli.main` を直接呼べば in-process にテストできるようにするため）。
"""

import sys

from trajectory_sim.cli import main

if __name__ == "__main__":
    sys.exit(main())
