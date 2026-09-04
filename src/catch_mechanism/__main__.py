"""`python -m catch_mechanism` の入口（design.md「Directory Structure」）。

実処理を一切持たない薄いラッパーであり、全ロジックは `cli.main` に置く
（`cli.main(argv)` を直接呼べば in-process で終了コードを観測できるようにする
ため。上流 `src/trajectory_sim/__main__.py` と同じ形である）。

⚠️ 本ファイルは `cli` だけを import する。`cli` は `shapes` / `export` を関数内で
遅延 import するため、`python -m catch_mechanism` の起動そのものは形状ライブラリを
要求しない（要件 5.2, 5.7）。形状を要するサブコマンドだけが、非導入の環境で
終了コード 3 で失敗する（要件 5.3）。
"""

import sys

from catch_mechanism.cli import main

if __name__ == "__main__":
    sys.exit(main())
