"""world-frame-calibration が送出する例外階層（design.md「L0-L2: 基盤・入力」
Errors コンポーネント / tasks.md タスク 1.2 / 要件 5.5, 5.6）。

**なぜ「無効は値で返す」ではなく「縮退条件は例外にする」のか。**
`prediction_core` は「予測が構造的に成立しない場合は戻り値
`InvalidPrediction` として返し、例外にしない」という方針を採る
（`prediction_core.errors` 参照）。それは予測が出ないことが正常系の
一部として起こり得るためである。

本パッケージはその方針を踏襲**しない**。本 Spec が扱う縮退条件
（床平面が十分な点で支持されない、基準マーカーの基線長が短すぎる、
姿勢が定まらない、など）は、`docs/requirements.md §6.2` が警告する
とおり「座標系が数 cm ずれていても症状は予測が悪いようにしか見えない」
という性質を持つ。したがって、縮退した変換を戻り値として返し呼び出し側が
その確認を怠れば、**間違った変換が検出も予測も経由せずそのまま下流へ
静かに流れ込む**。これは本 Spec が独立検証（要件 4）まで用意して防ごうと
している当の事故そのものである（A-9: 縮退条件では結果を出さない）。

そのため、座標系が定まらない条件は**必ず例外として送出し、戻り値では
表現しない**。呼び出し側が戻り値の確認を怠っても、変換が下流へ流れる前に
処理そのものが止まる形にする。これにより、部分的な結果や既定値で埋めた
結果を返すことも構造的にできなくなる（要件 5.5）。

失敗は次の3階層で表す。

- `WorldFrameCalibrationError`: 本パッケージ由来の失敗を一括捕捉する基底
- `CalibrationConfigError`: 呼び出し方・`CalibrationPlan` の誤り
  （構築時点で不正な設定など）
- `CalibrationFailure`: **座標系が定まらない条件**。`reason`
  （`FailureReason`）・`detail`・`context` を持ち、`context` には
  内点率・基線長・入射角などの実測値と、判定に用いた下限を必ず含める
  （design.md Errors「Validation」/ 要件 5.1〜5.4 の「報告する」要求）

`FailureReason` は `StrEnum` として定義し、呼び出し側が `except` で
`CalibrationFailure` を捕捉した後、`reason` を文字列比較のみで分岐できる
（要件 5.6: 「失敗の理由を、呼び出し側が分岐できる識別可能な値として提供する」）。

このモジュールは L0 層であり、標準ライブラリ以外（`numpy` を含む）も
パッケージ内の他モジュールも import しない（design.md「Dependency
Direction」の層表）。
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum


class WorldFrameCalibrationError(Exception):
    """world_frame_calibration が送出するすべての例外の基底。

    呼び出し側はこれを捕捉することで、本パッケージ由来の失敗を
    ひとまとめに扱える。`CalibrationConfigError`（呼び出し方の誤り）と
    `CalibrationFailure`（座標系が定まらない条件）のどちらも、
    最終的にはこの例外として捕捉できる。
    """


class CalibrationConfigError(WorldFrameCalibrationError):
    """呼び出し方・`CalibrationPlan` の誤り。

    探索範囲やマーカー範囲の指定が不正、必須の入力が欠けている、
    許容値仕様の出典文字列が欠けている、といった構築時点で成立しない
    設定を検出した際に送出する。座標系そのものは定まる／定まらないの
    判断に至っていない誤りであり、`CalibrationFailure` とは区別する。
    """


class CalibrationFailure(WorldFrameCalibrationError):
    """座標系が定まらない条件（A-9 が「結果を出さない」と定める失敗）。

    `reason` で失敗理由を識別可能な値として提供し（要件 5.6）、
    `context` に実測値と判定に用いた下限を必ず含める
    （design.md Errors「Validation」）。部分的な結果や既定値で埋めた
    変換を返す代わりに、必ずこの例外を送出する（要件 5.5）。

    Attributes:
        reason: 失敗理由。`FailureReason` の値。
        detail: 人が読める失敗の詳細説明。
        context: 実測値と判定に用いた下限などを保持するマッピング
            （例: 内点率・基線長・入射角とそれぞれの下限）。
    """

    def __init__(
        self,
        reason: "FailureReason",
        detail: str,
        context: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason: "FailureReason" = reason
        self.detail = detail
        self.context: Mapping[str, object] = dict(context) if context is not None else {}


class FailureReason(StrEnum):
    """座標系が定まらない条件の識別可能な理由（要件 5.1〜5.6）。

    `StrEnum` を用いることで、呼び出し側は `except CalibrationFailure`
    で捕捉した後、`reason == "plane_not_supported"` のような文字列比較
    のみで分岐できる（要件 5.6）。
    """

    INSUFFICIENT_DEPTH_POINTS = "insufficient_depth_points"
    """平面推定に十分な有効 Depth 点が得られない（要件 5.1）。"""

    PLANE_NOT_SUPPORTED = "plane_not_supported"
    """平面に適合した点の数または割合が下限を下回った（要件 1.5）。"""

    INCIDENCE_ANGLE_TOO_SHALLOW = "incidence_angle_too_shallow"
    """床面を見込む入射角が浅すぎる（要件 5.4）。"""

    ANCHOR_NOT_FOUND = "anchor_not_found"
    """基準マーカーの観測が指定範囲内に得られない（要件 5.2）。"""

    ANCHOR_BASELINE_TOO_SHORT = "anchor_baseline_too_short"
    """原点マーカーと方向マーカーの距離が下限を下回る（要件 2.7, 5.3）。"""

    ANCHOR_DEGENERATE = "anchor_degenerate"
    """マーカー観測が縮退しており姿勢が定まらない（A-9）。"""

    UNSUPPORTED_DISTORTION = "unsupported_distortion"
    """カメラ内部パラメータのレンズ歪み係数が扱えない値である（要件 3.5）。"""

    ROTATION_NOT_ORTHONORMAL = "rotation_not_orthonormal"
    """復元した回転行列が正規直交でない。"""

    UNKNOWN_FORMAT_VERSION = "unknown_format_version"
    """永続化された結果の形式版が未知である（要件 6.3）。"""

    PROFILE_MISMATCH = "profile_mismatch"
    """読み込んだ結果のストリーム設定・カメラ内部パラメータが現在の入力元と食い違う（要件 6.4）。"""

    VERIFICATION_NOT_INDEPENDENT = "verification_not_independent"
    """検証点が World frame の確立に用いた基準マーカーのみである（要件 4.3）。"""
