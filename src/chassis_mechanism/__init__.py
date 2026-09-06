"""chassis-mechanism: 手元の駆動部品を「機体」にするための構造物一式を持つパッケージ。

本パッケージは、駆動ベース・ゴミ箱固定アダプタ・バッテリ／基板トレイ・配線ガイド・
整備スタンドの寸法・幾何・接合・観測を持ち、そこから形状と生成物を導く。
これにより `teleop-bringup`（M2a 初通電走行）の着手条件——安全に台上へ載せられる機体が
物理的に存在すること——を成立させる。
⚠️ **本パッケージはモータを回さない。** 通電・走行・性能値の決定は下流の所有である
（design.md「Overview」/「Out of Boundary」）。

本 Spec は上流 `catch-mechanism` が確立した CAD 基盤を**消費する側**である。
造形可能寸法・許可材料一覧・継手方針の定義、ゴミ箱の採寸値の所有、形状指標の型と照合の定義は
上流が単独で持ち、本パッケージはそれらを**参照して用いるだけ**で同じ定義を持たない
（design.md「Boundary Commitments」）。

本モジュールは公開 API の**再エクスポート専用**であり、ロジックを一切持たない。
下流（`teleop-bringup` / `m2-motion-validation`）が参照してよい唯一の入口はこの `__init__`
であり、内部モジュール（`chassis_mechanism.params` / `.config` / `.layout` / `.clearance` /
`.joints` / `.assembly` / `.baseline` / `.shapes` / `.export` 等）へ直接 import しないこと。
この `__all__` に**明示列挙されたものだけ**が公開契約である。

⚠️ **`__all__` のシンボル追加・削除・意味変更は、下流の再検証を要する変更である**
（design.md「Revalidation Triggers」項目4）。

依存の制約（design.md「Allowed Dependencies」/「Dependency Direction」）:
    実行時のサードパーティ依存は宣言しない（`[project].dependencies` は空のまま）。
    形状ライブラリ `build123d` は上流が導入済みの**任意依存（extras `cad`）をそのまま使い**、
    ⚠️ **新しい extras を追加しない**。import してよいのは `shapes.py` と `export.py` の
    2モジュールに限る。
    ⚠️ **この `__init__` は `build123d` を import しない。** 形状ライブラリを導入していない
    環境でも本パッケージが import でき、寸法パラメータの読み込み・導出・下流への提供が
    成立することがタスク 1.1 の観測可能な完了状態である。そのため CAD 層
    （`shapes` / `export`）のモジュールも**モジュール直下では import しない**——両者は
    形状ライブラリを関数内で遅延 import するため、ここで読み込んでも即座には失敗せず、
    代わりに入口が黙って重くなる。
    上流 `catch_mechanism` へは**公開 API（`import catch_mechanism` /
    `from catch_mechanism import X`）経由でのみ**依存してよい。
    `prediction_core` / `trajectory_sim` / `sensing_foundation` その他の兄弟パッケージと
    `firmware/` の資産は import しない（依存方向が逆になる、または無関係）。
    ⚠️ とくに `trajectory_sim` への還元は、設定ファイルの値と一致検査だけで行う。
"""

from __future__ import annotations

#: 下流が参照してよい公開シンボル（design.md `#### PublicApi`）。
#:
#: ⚠️ **並びは design.md「Dependency Direction」の層順とする**
#: （`errors → params → config → layout → {clearance, joints} → {assembly, baseline}`）。
#: 現時点ではパッケージの骨組みのみが存在し、公開するシンボルはまだ1つも無い。
#: 中身の正は下流契約テストが持つ。
#:
#: ⚠️ **ここへの追加・削除・意味変更は下流の再検証を要する変更である**
#: （design.md「Revalidation Triggers」項目4。本モジュール docstring 参照）。
__all__: list[str] = []
