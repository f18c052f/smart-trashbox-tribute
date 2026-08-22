"""world-frame-calibration: Depth 由来の床平面と2マーカーから
カメラ座標系→World 座標系の剛体変換を確立し、独立検証するパッケージ。

このパッケージは Python 標準ライブラリと `numpy`（`calibration` extras）、
および `sensing_foundation` の公開入口のみに依存し、ハードウェアを接続しない
環境（合成 Depth・記録済みセッション）で完結して動作する
（design.md「Allowed Dependencies」/ 要件 9.1, 9.2, 11.5）。

本モジュールは公開 API の**再エクスポート専用**であり、ロジックを一切持たない
（design.md「PublicApi / Validation」: 「`__init__` はロジックを持たず
再エクスポートのみ。`prediction_core` を import しない」/ tasks.md
タスク 6.3 / 要件 3.6, 8.6）。下流（`flying-object-tracking` /
`m1-prediction-validation`）が参照してよいシンボルは、この `__all__` に
**明示列挙されたものだけ**である。それ以外はすべて内部実装であり、
バージョン間の互換性は保証されない。

**公開する最小集合**（design.md PublicApi「Integration」/ tasks.md
タスク 6.3「結果の読み込み・結果型・変換型・整合性検査・失敗例外・失敗理由」）:

- 結果の読み込み: `load_calibration`（`result.py`）
- 結果型: `CalibrationResult`（`result.py`）
- 変換型: `WorldTransform`（`transform.py`）
- 整合性検査: `check_compatibility`（`result.py`）
- 失敗例外: `WorldFrameCalibrationError`（基底）・`CalibrationConfigError`
  （呼び出し方・入力ファイルの誤り）・`CalibrationFailure`（座標系が定まらない
  条件）の3階層（`errors.py`）。design.md が名指しする6シンボルは
  `CalibrationFailure` のみだが、`load_calibration` は不正な JSON・欠損キー
  などで `CalibrationConfigError` も送出するため（`result.py` 参照）、
  「失敗例外」というカテゴリを取りこぼしなく満たすには基底の
  `WorldFrameCalibrationError` と合わせた3つが必要になる
- 失敗理由: `FailureReason`（`errors.py`）

**`prediction_core` を import しない**（要件 8.6）。**Throw Record スキーマも
再定義しない**（`prediction_core` の責務であり本 Spec の範囲外）。

**`sensing_foundation` が無い環境でも `import world_frame_calibration` が
成功する**（tasks.md タスク 6.3 の観測可能な完了状態）。`deproject.py` /
`upstream.py` はモジュールレベルで `sensing_foundation` を import するが、
本モジュールはそれらを一切 re-export しない。公開契約の8シンボルは
`errors.py` / `result.py` / `transform.py` からのみ再エクスポートされ、
その依存連鎖（`result` → `frame` → `plan` / `transform` / `types` /
`linalg` / `errors`）のどこにも `sensing_foundation` の import が無い
（`test_world_frame_calibration_public_api.py` が実測で固定する）。
"""

from __future__ import annotations

from world_frame_calibration.errors import (
    CalibrationConfigError,
    CalibrationFailure,
    FailureReason,
    WorldFrameCalibrationError,
)
from world_frame_calibration.result import (
    CalibrationResult,
    check_compatibility,
    load_calibration,
)
from world_frame_calibration.transform import WorldTransform

__all__ = [
    # 失敗例外・失敗理由（errors.py）
    "WorldFrameCalibrationError",
    "CalibrationConfigError",
    "CalibrationFailure",
    "FailureReason",
    # 結果の読み込み・結果型・整合性検査（result.py）
    "CalibrationResult",
    "load_calibration",
    "check_compatibility",
    # 変換型（transform.py）
    "WorldTransform",
]
