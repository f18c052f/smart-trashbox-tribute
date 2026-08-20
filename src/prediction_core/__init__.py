"""prediction-core: 落下地点・落下時刻の予測コア。

このパッケージは Python 標準ライブラリのみに依存し、
ハードウェアを接続しない環境で完結して動作する。

このモジュールは公開 API の再エクスポート専用であり、ロジックを持たない
（design.md「PublicApi / Responsibilities & Constraints」）。`__all__` に
列挙されたシンボルのみが公開契約であり、ここに無いものは内部実装として
扱う。Throw Record スキーマ（`ThrowRecord` / `SCHEMA_VERSION` /
`replay` / `predictions_equivalent`）の単一定義元でもあり、下流 Spec は
本パッケージの入口からのみこれらを参照する（要件 9.7）。
"""

from __future__ import annotations

from prediction_core.config import PredictionConfig
from prediction_core.errors import (
    PredictionConfigError,
    PredictionCoreError,
    RecordSchemaError,
    RecordSerializationError,
)
from prediction_core.predictor import predict
from prediction_core.record import (
    SCHEMA_VERSION,
    ThrowRecord,
    predictions_equivalent,
    replay,
)
from prediction_core.tracker import ThrowPredictionTracker
from prediction_core.types import (
    InvalidPrediction,
    InvalidReason,
    Prediction,
    PredictionOutcome,
    Sample,
    SourceKind,
    TrajectoryParameters,
)

__all__ = [
    "InvalidPrediction",
    "InvalidReason",
    "Prediction",
    "PredictionConfig",
    "PredictionConfigError",
    "PredictionCoreError",
    "PredictionOutcome",
    "RecordSchemaError",
    "RecordSerializationError",
    "SCHEMA_VERSION",
    "Sample",
    "SourceKind",
    "ThrowPredictionTracker",
    "ThrowRecord",
    "TrajectoryParameters",
    "predict",
    "predictions_equivalent",
    "replay",
]
