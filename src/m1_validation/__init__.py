"""m1-prediction-validation: M1（予測の成立性）を実測で判断する検証ハーネス。

本パッケージは、上流4 Spec が用意した部品（取得・追跡・較正・予測）を
**実機で1本につないで投擲を計測し、その結果から「M1 が成立しているか」を
判断する**ためのものである。予測や追跡そのものは実装しない——上流の公開
入口を呼ぶだけであり、本 Spec が持つのは**継ぎ目・真値・実測・帰属・判断**
である（design.md「Overview / Boundary Commitments」）。

本モジュールは公開 API の**再エクスポート専用**であり、ロジックを一切
持たない。下流（M2 / M3 / `simulator-visualization`）が本パッケージを参照
してよい唯一の入口はこの `__init__` であり、内部モジュール
（`m1_validation.types` / `.config` / `.seam` / `.metrics.*` / `.judgement.*`
等）へ直接 import しないこと。この `__all__` に**明示列挙されたものだけ**が
公開契約である。

**現時点では何も公開していない。** 骨組みだけが存在する段階であり
（タスク 1.1）、型・設定・判断の各層が landing するのに合わせて後続タスクが
ここへ再エクスポートを足していく。

依存の制約（design.md「Allowed Dependencies」）:
    実行時のサードパーティ依存は宣言しない。本 Spec が宣言する第三者依存は
    可視化用の `matplotlib` ただ1つで、**任意指定（extras `m1-viz`）**として
    のみ宣言し、import するのは `plot.py` 1モジュールに限る。
    `numpy` / `cv2` / `pyrealsense2` を直接 import しない（前2者は上流の
    道具であり、`numpy` は上流の extras の中で完結させる）。
"""

from __future__ import annotations

#: 下流が参照してよい公開シンボル。後続タスクがここへ追記する
#: （空であることは「まだ何も公開していない」という事実であり、
#: 公開する物が無いことを意味しない）。
__all__: list[str] = []
