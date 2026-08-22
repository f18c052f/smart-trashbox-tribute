"""trajectory-simulator: 投擲条件・観測条件・移動体性能からキャッチ可能領域を
算出する Python バッチシミュレータ。

このパッケージは Python 標準ライブラリと `prediction_core` の公開 API のみに
依存し、ハードウェアを接続しない環境で完結して動作する
（design.md「Allowed Dependencies」/ 要件 8.5, 11.3）。

このモジュールは公開 API の再エクスポート専用であり、ロジックを持たない
（design.md「PublicApi / Responsibilities & Constraints」/ 要件 11.1）。
`__all__` に列挙されたシンボルのみが公開契約であり、ここに無いものは
内部実装として扱う（`units` / `physics` / `drivetrain` / `observation` /
`prediction_link` / `cli` / `__main__` はすべて非公開）。

公開するのは、パラメータ型・掃引型・結果型・掃引実行（`run_sweep`）・
出力書き出し（`write_sweep_result` / `sweep_result_to_dict`）・
出力形式の版（`OUTPUT_SCHEMA_VERSION`）・評価関数（`evaluate_throw` /
`evaluate_reachability`）・例外階層の8カテゴリである。

**`prediction_core` のシンボルは一切再エクスポートしない**（design.md
「PublicApi / Responsibilities & Constraints」: 「利用側は上流を直接
import する（依存関係を隠さない）」）。`params.PredictionConfig` や
`params` が内部参照用に保持する `Sample` / `SourceKind` も、`results`
が保持する `ThrowRecord` も、ここには載せない。`ScenarioParams.prediction`
に `prediction_core.PredictionConfig` を渡す必要がある呼び出し側は、
`prediction_core` を自分で直接 import しなければならない。これは意図的な
選択であり、上流への依存を隠さないための設計判断である。同じ理由で、
`params.make_sample`（`observation` が内部で `prediction_core` を import
せずに済ませるための構築ヘルパー）も再エクスポートしない。`Sample` を
自ら構築したい呼び出し側は `prediction_core.Sample` を直接使う。
"""

from __future__ import annotations

from trajectory_sim.errors import (
    OutputError,
    ParameterError,
    SweepDefinitionError,
    TrajectorySimError,
)
from trajectory_sim.evaluate import evaluate_reachability, evaluate_throw
from trajectory_sim.params import (
    PARAMETER_PATHS,
    CalibrationStage,
    CatchCriteria,
    CatchPolicy,
    DrivetrainParams,
    LayoutParams,
    ObservationParams,
    ParameterPath,
    Provenance,
    ScenarioParams,
    ThrowDispersion,
    ThrowParams,
    replace_by_path,
)
from trajectory_sim.results import (
    MODEL_EXCLUSIONS,
    CellResult,
    CellStatus,
    NotEvaluatedReason,
    ScenarioOutcome,
    SweepResult,
)
from trajectory_sim.serialize import (
    OUTPUT_SCHEMA_VERSION,
    sweep_result_to_dict,
    write_sweep_result,
)
from trajectory_sim.sweep import AxisSpec, SweepKind, SweepSpec, derive_seed, run_sweep

__all__ = [
    "AxisSpec",
    "CalibrationStage",
    "CatchCriteria",
    "CatchPolicy",
    "CellResult",
    "CellStatus",
    "DrivetrainParams",
    "LayoutParams",
    "MODEL_EXCLUSIONS",
    "NotEvaluatedReason",
    "OUTPUT_SCHEMA_VERSION",
    "ObservationParams",
    "OutputError",
    "PARAMETER_PATHS",
    "ParameterError",
    "ParameterPath",
    "Provenance",
    "ScenarioOutcome",
    "ScenarioParams",
    "SweepDefinitionError",
    "SweepKind",
    "SweepResult",
    "SweepSpec",
    "ThrowDispersion",
    "ThrowParams",
    "TrajectorySimError",
    "derive_seed",
    "evaluate_reachability",
    "evaluate_throw",
    "replace_by_path",
    "run_sweep",
    "sweep_result_to_dict",
    "write_sweep_result",
]
