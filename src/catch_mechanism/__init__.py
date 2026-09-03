"""catch-mechanism: 受け口（ワイドリム）の寸法・形状・生成物を持つ CAD 基盤。

本パッケージは、ゴミ箱の採寸値と造形制約を**プロジェクト内で唯一の正**として
保持し、そこから受け口部品の形状・生成物・形状指標を導く。下流
（`chassis-mechanism` / `trajectory-simulator` / `m1-prediction-validation`）は
本パッケージが公開する寸法と制約を**消費する**側であり、逆向きの依存は無い
（design.md「Boundary Commitments」/「Dependency Direction」）。

本モジュールは公開 API の**再エクスポート専用**であり、ロジックを一切持たない。
下流が参照してよい唯一の入口はこの `__init__` であり、内部モジュール
（`catch_mechanism.params` / `.config` / `.selection` / `.tolerance` /
`.constraints` / `.metrics` / `.shapes` / `.export` 等）へ直接 import しないこと。
この `__all__` に**明示列挙されたものだけ**が公開契約である。

**現時点では何も公開していない。** 公開契約を確定するのは tasks.md のタスク
6.1 であり、`__all__` が空のままであることは**不備ではなく現状の正確な表明**
である（中身が無いのに import できてしまう状態を「完成」と見誤らないため、
空であること自体をテストで固定している）。

依存の制約（design.md「Allowed Dependencies」/「依存境界の扱い（`cad` extra
の導入）」）:
    実行時のサードパーティ依存は宣言しない（`[project].dependencies` は空の
    まま）。本 Spec が宣言する第三者依存は形状ライブラリ `build123d` ただ1つで、
    **任意指定（extras `cad`）**としてのみ宣言し、import するのは `shapes.py`
    と `export.py` の2モジュールに限る。
    ⚠️ **この `__init__` は `build123d` を import しない。** 形状ライブラリを
    導入していない環境でも本パッケージが import でき、寸法パラメータの読み込み・
    導出・下流への提供が成立することが要件 5.2 / 5.7 の要求である。
    `prediction_core` / `trajectory_sim` も import しない（依存方向が逆になる）。
"""

from __future__ import annotations

#: 下流が参照してよい公開シンボル。タスク 6.1 がここへ追記する
#: （空であることは「まだ何も公開していない」という事実であり、
#: 公開する物が無いことを意味しない）。
__all__: list[str] = []
