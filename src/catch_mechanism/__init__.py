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

公開契約の中身（design.md `#### PublicApi`）
-------------------------------------------

- **ゴミ箱の採寸値**: `TrashCanMeasurements`（底の外径・底の平面部径・
  テーパー角・高さ・実測重量ほか）を `load_params()` から取得する。
  ⚠️ `chassis-mechanism` はこれらを**ここから**参照し、同じ値を再定義しない
  （要件 10.1）
- **造形制約と継手方針**: `PrintingConstraints` / `JointPolicy` と、その判定
  （`check_material` / `check_joint` / `check_envelope` /
  `required_segment_count`）。下流が同じ判定を書き直す必要は無い（要件 10.2）
- **出所**: `Provenance` と `MechanismParams.provenance`、単位は
  `PARAMETER_PATHS[path].unit`。⚠️ **仮値と実測値は利用側が区別できる**
  （要件 10.4）。出所表に現れないパスは `ASSUMED` として扱う
- **選定した実物の記録**: `SelectionResult` / `load_selection_result()`
  （何を買い、なぜ選び、壊したときどこで買い直すか。要件 6.8）
- **位置許容誤差の導出記録**: `load_derivation(DEFAULT_DERIVATION_PATH)`
- **形状指標の記録と照合**: `load_baseline()` / `compare_metrics()` と、
  在／不在の符号化（`PRESENCE_FIELD` / `PRESENT` / `ABSENT`）

公開しないもの（要件 10.6 / design.md「Out of Boundary」）
---------------------------------------------------------

⚠️ **駆動ベース・固定アダプタ・トレイ類・整備スタンドは本 Spec の責務ではない。**
それらは `chassis-mechanism` が本基盤を消費して設計する。公開面は**中核層の
7モジュール**（`errors` / `params` / `config` / `selection` / `tolerance` /
`constraints` / `metrics`）に閉じており、下流の部品を名乗るシンボルも、形状を
生成する手段（`shapes.build_parts` 等）も、生成物を書き出す手段
（`export.export_parts` 等）も1つも含まない。形状の生成と書き出しは
`python -m catch_mechanism` の側の道具である。
同じ理由で、記録を**書き換える**操作（`metrics.write_baseline`）とその鮮度検査
（`metrics.verify_baseline_digest`）も公開しない——下流は記録を消費するだけで
再生成しない。

変更したときに何が起きるか（要件 10.5）
---------------------------------------

⚠️ **`__all__` のシンボル追加・削除・意味変更は、下流の再検証を要する変更である。**
これは design.md「Revalidation Triggers」の項目4 が名指しで記録している。
併せて、公開項目の**構造・キー名・単位**の変更は同じ節の項目1（`dimensions.json`
の構造・キー名・単位）に当たる。値そのものの更新（採寸のやり直しなど）は出所が
追随するため再検証を要さない。
この対応付けは `tests/catch_mechanism/test_catch_downstream_contract.py` の
`PUBLIC_CONTRACT` が機械的に固定しており、本ファイルだけを直しても通らない。

依存の制約（design.md「Allowed Dependencies」/「依存境界の扱い（`cad` extra
の導入）」）:
    実行時のサードパーティ依存は宣言しない（`[project].dependencies` は空の
    まま）。本 Spec が宣言する第三者依存は形状ライブラリ `build123d` ただ1つで、
    **任意指定（extras `cad`）**としてのみ宣言し、import するのは `shapes.py`
    と `export.py` の2モジュールに限る。
    ⚠️ **この `__init__` は `build123d` を import しない。** 形状ライブラリを
    導入していない環境でも本パッケージが import でき、寸法パラメータの読み込み・
    導出・下流への提供が成立することが要件 5.2 / 5.7 / 10.3 の要求である。
    そのため CAD 層（`shapes` / `export`）のモジュールも**モジュール直下では
    import しない**——両者は形状ライブラリを関数内で遅延 import するため、
    ここで読み込んでも即座には失敗せず、代わりに入口が黙って重くなる。
    `prediction_core` / `trajectory_sim` も import しない（依存方向が逆になる）。
"""

from __future__ import annotations

from catch_mechanism.config import (
    SCHEMA_VERSION,
    dump_params,
    load_params,
    parameters_digest,
)
from catch_mechanism.constraints import (
    BuildViolation,
    Envelope,
    check_envelope,
    check_joint,
    check_material,
    required_segment_count,
)
from catch_mechanism.errors import (
    CadUnavailableError,
    CatchMechanismError,
    ConsistencyError,
    GeometryError,
    ParameterError,
    SelectionError,
)
from catch_mechanism.metrics import (
    ABSENT,
    PRESENCE_FIELD,
    PRESENT,
    GeometryBaseline,
    MetricsMismatch,
    PartMetrics,
    compare_metrics,
    estimate_mass_g,
    load_baseline,
)
from catch_mechanism.params import (
    ALLOWED_MATERIALS,
    PARAMETER_PATHS,
    JointPolicy,
    MechanismParams,
    ObjectSpec,
    PrintingConstraints,
    Provenance,
    RetentionParams,
    RimParams,
    TrashCanMeasurements,
)
from catch_mechanism.selection import (
    CRITERIA_ITEMS,
    Candidate,
    CandidateVerdict,
    SelectionCriteria,
    SelectionResult,
    evaluate_candidate,
    load_candidates,
    load_criteria,
    load_selection_result,
)
from catch_mechanism.tolerance import (
    DEFAULT_DERIVATION_PATH,
    ToleranceDerivation,
    ToleranceInput,
    derive_position_tolerance,
    load_derivation,
)

#: 下流が参照してよい公開シンボル（design.md `#### PublicApi`）。
#:
#: ⚠️ **並びは design.md「Dependency Direction」の層順である**
#: （`errors → params → config → {selection, tolerance, constraints, metrics}`）。
#: どの層の契約なのかが一覧のまま読めるようにするためであり、
#: `tests/catch_mechanism/test_catch_downstream_contract.py` の
#: `PUBLIC_CONTRACT` が由来モジュールごとに同じ並びを保持している。
#:
#: ⚠️ **ここへの追加・削除・意味変更は下流の再検証を要する変更である**
#: （design.md「Revalidation Triggers」項目4。本モジュール docstring 参照）。
__all__: list[str] = [
    # errors
    "CatchMechanismError",
    "ParameterError",
    "SelectionError",
    "GeometryError",
    "ConsistencyError",
    "CadUnavailableError",
    # params
    "Provenance",
    "TrashCanMeasurements",
    "ObjectSpec",
    "PrintingConstraints",
    "JointPolicy",
    "RimParams",
    "RetentionParams",
    "MechanismParams",
    "PARAMETER_PATHS",
    "ALLOWED_MATERIALS",
    # config
    "SCHEMA_VERSION",
    "load_params",
    "dump_params",
    "parameters_digest",
    # selection
    "CRITERIA_ITEMS",
    "SelectionCriteria",
    "Candidate",
    "CandidateVerdict",
    "SelectionResult",
    "evaluate_candidate",
    "load_criteria",
    "load_candidates",
    "load_selection_result",
    # tolerance
    "DEFAULT_DERIVATION_PATH",
    "ToleranceInput",
    "ToleranceDerivation",
    "derive_position_tolerance",
    "load_derivation",
    # constraints
    "Envelope",
    "BuildViolation",
    "required_segment_count",
    "check_envelope",
    "check_material",
    "check_joint",
    # metrics
    "PRESENCE_FIELD",
    "PRESENT",
    "ABSENT",
    "PartMetrics",
    "GeometryBaseline",
    "MetricsMismatch",
    "load_baseline",
    "compare_metrics",
    "estimate_mass_g",
]
