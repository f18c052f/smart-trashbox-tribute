"""全入力パラメータの不変表現と構築時検証（design.md「Params」/ 要件 1.4,
2.6, 4.1, 4.3, 5.1, 5.4, 7.7, 9.2, 10.1）。

投擲・ばらつき・観測・機体・キャッチ判定・レイアウト・較正段階・出所を、
すべて `frozen=True, slots=True` のデータクラスとして定義する。値等価で
あり、結果へそのまま同梱できる（design.md「Params」Responsibilities）。

**機体性能（最高速度・加速度上限・減速度上限・制御周期・指令反映遅れ）に
既定値を与えない**（要件 4.3）。`DrivetrainParams` のこれらのフィールドは
dataclass の必須フィールドであり、省略した構築は `TypeError` として
失敗する。

予測パラメータは `prediction_core.PredictionConfig` をそのまま保持し、
独自の予測設定型を作らない（design.md「Params」Responsibilities）。

検証は各データクラスの `__post_init__` に置き、違反フィールド名と値を
含むメッセージで `trajectory_sim.errors.ParameterError` を送出する
（`prediction_core.PredictionConfig` と同じ形。`src/prediction_core/config.py`
参照）。

タスク 1.4 の範囲は上記データクラスとフィールド単位の検証、および
`prediction_core.Sample` を構築する `make_sample` までである。
タスク 1.5 では、これに加えて `DrivetrainParams.from_wheel`（ホイール径・
モータ回転数・速度効率からの最高速度導出、要件4.2）、`PARAMETER_PATHS`
（掃引が指定できるパラメータパス表、要件6.1）、`replace_by_path`
（パス指定によるパラメータ置換）、および `ScenarioParams.provenance` の
キー検証（要件9.4）を追加する。

`PARAMETER_PATHS` は `ScenarioParams` のデータクラス木を
`dataclasses.fields()` で走査して構築時に一度だけ生成し、手書きの表を
二重管理しない（design.md「Params」Implementation Notes）。単位は
フィールド名の接尾辞（`_mm_s2` → `_mm_s` → `_mm` 等、詳細は
`_SUFFIX_UNITS` を参照）から導く。`ParameterPath` はこの表のエントリの
形であり、design.md 本文には型の内部構造が明記されていないため、本
モジュールが「パス文字列・単位・所属コンポーネント・フィールド名」を
持つ最小限の frozen dataclass として定義する（設計ギャップの補完。
掃引エンジン（タスク3.2）・結果直列化（タスク4.1）はこの形を前提に
`PARAMETER_PATHS` を消費する）。

`Sample` / `PredictionConfig` は `prediction_core` の公開 API
（`from prediction_core import ...`）からのみ import する。`__all__` は
本モジュールが定義する型・関数のみを含み、これら上流型を再公開しない
（design.md「Params」Implementation Notes: 「これらを `__init__` から
再公開はしない」）。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, is_dataclass, replace
from enum import StrEnum
from types import MappingProxyType
from typing import get_type_hints

from prediction_core import PredictionConfig, Sample

from trajectory_sim.errors import ParameterError, SweepDefinitionError

__all__ = [
    "CalibrationStage",
    "Provenance",
    "CatchPolicy",
    "ThrowParams",
    "ThrowDispersion",
    "ObservationParams",
    "DrivetrainParams",
    "CatchCriteria",
    "LayoutParams",
    "ScenarioParams",
    "ParameterPath",
    "PARAMETER_PATHS",
    "replace_by_path",
    "make_sample",
]


def _require_finite(value: float, name: str) -> None:
    """`value` が有限値であることを検証し、違反時は `ParameterError` を送出する。"""
    if not math.isfinite(value):
        raise ParameterError(f"{name}={value!r} は有限値でなければならない。")


def _require_nonneg_finite(value: float, name: str) -> None:
    """`value` が 0 以上の有限値であることを検証する。"""
    if not (math.isfinite(value) and value >= 0.0):
        raise ParameterError(f"{name}={value!r} は 0 以上の有限値でなければならない。")


def _require_positive_finite(value: float, name: str) -> None:
    """`value` が正の有限値であることを検証する。"""
    if not (math.isfinite(value) and value > 0.0):
        raise ParameterError(f"{name}={value!r} は正の有限値でなければならない。")


class CalibrationStage(StrEnum):
    """較正段階（要件9.1, 9.2, 9.3）。

    既定値は `UNCALIBRATED`（未較正）とする（要件9.2）。未較正の間は、
    結果が感度分析用であり絶対値を信用してはならない旨を出力側
    （`results` / `serialize`、いずれも別タスク）が明示する前提である。
    """

    UNCALIBRATED = "uncalibrated"
    M1_CALIBRATED = "m1_calibrated"
    M2_CALIBRATED = "m2_calibrated"


class Provenance(StrEnum):
    """パラメータ値の出所（実測 / 想定）（要件9.4）。"""

    MEASURED = "measured"
    ASSUMED = "assumed"


class CatchPolicy(StrEnum):
    """キャッチ判定における減速方針（要件5.1, 5.8）。

    方針そのものを確定させず、両方針を比較材料として提示するための
    選択肢である。
    """

    STOP_AND_WAIT = "stop_and_wait"
    PASS_THROUGH = "pass_through"


@dataclass(frozen=True, slots=True)
class ThrowParams:
    """投擲条件（要件1.1, 1.4, 10.1）。

    Attributes:
        release_x_mm: リリース位置の x 座標（mm）。
        release_y_mm: リリース位置の y 座標（mm）。
        release_z_mm: リリース位置の z 座標（mm）。
        speed_mm_s: リリース時の初速の大きさ（mm/s）。正の有限値。
        elevation_deg: 仰角（度）。水平を 0、鉛直上向きを 90、鉛直下向きを
            -90 とする。物理的に意味を持つ範囲として -90 から 90 の間に
            限定する（この範囲を外れる仰角は「上向き」「下向き」の表現と
            して意味を持たない）。
        azimuth_deg: 方位角（度）。周期的な量であるため値域を制限せず、
            有限値であることのみ検証する。
        gravity_mm_s2: 重力加速度の大きさ（mm/s^2）。既定値 9806.65 は
            標準重力加速度 9.80665 m/s^2 を mm/s^2 に換算した値である。
            定数として運動モデルに埋め込まず、ここでパラメータ化する
            （要件1.4: 根拠のない固定値として埋め込まない）。
        object_diameter_mm: 対象物の代表寸法（mm）。既定値 65.0 は
            空き缶（350ml 缶相当）の直径の**仮値**であり、実測値では
            ない（要件1.4, 9.4）。`provenance` で `ASSUMED` として
            明示する運用を想定する。`CatchCriteria.position_tolerance_mm`
            の既定値の導出にもこの仮値を用いる。
    """

    release_x_mm: float
    release_y_mm: float
    release_z_mm: float
    speed_mm_s: float
    elevation_deg: float
    azimuth_deg: float
    gravity_mm_s2: float = 9806.65
    object_diameter_mm: float = 65.0

    def __post_init__(self) -> None:
        """全不変条件を検証し、違反時は違反フィールド名と値を添えて拒否する。"""
        _require_finite(self.release_x_mm, "release_x_mm")
        _require_finite(self.release_y_mm, "release_y_mm")
        _require_finite(self.release_z_mm, "release_z_mm")
        _require_positive_finite(self.speed_mm_s, "speed_mm_s")
        if not (math.isfinite(self.elevation_deg) and -90.0 <= self.elevation_deg <= 90.0):
            raise ParameterError(
                f"elevation_deg={self.elevation_deg!r} は -90 以上 90 以下の"
                "有限値でなければならない（仰角は鉛直下向きと鉛直上向きの"
                "間に限られる）。"
            )
        _require_finite(self.azimuth_deg, "azimuth_deg")
        _require_positive_finite(self.gravity_mm_s2, "gravity_mm_s2")
        _require_positive_finite(self.object_diameter_mm, "object_diameter_mm")


@dataclass(frozen=True, slots=True)
class ThrowDispersion:
    """投擲条件の試行ごとのばらつき（要件1.3）。

    全フィールドの既定値は 0.0（ばらつきなし）であり、既定構成では
    投擲条件は毎回同一になる。
    """

    speed_sigma_mm_s: float = 0.0
    elevation_sigma_deg: float = 0.0
    azimuth_sigma_deg: float = 0.0
    release_sigma_mm: float = 0.0

    def __post_init__(self) -> None:
        _require_nonneg_finite(self.speed_sigma_mm_s, "speed_sigma_mm_s")
        _require_nonneg_finite(self.elevation_sigma_deg, "elevation_sigma_deg")
        _require_nonneg_finite(self.azimuth_sigma_deg, "azimuth_sigma_deg")
        _require_nonneg_finite(self.release_sigma_mm, "release_sigma_mm")


@dataclass(frozen=True, slots=True)
class ObservationParams:
    """観測モデルのパラメータ（要件2.1-2.6, 7.7）。

    Attributes:
        detection_start_delay_ms: リリースから検出開始までの遅れ（ms）。
        sample_period_ms: 標本化の周期（ms）。
        sample_latency_ms: サンプル取得から利用可能になるまでの遅延（ms）。
        prediction_latency_ms: 予測に要する遅延（ms）。
        sigma_x_mm, sigma_y_mm, sigma_z_mm: 軸ごとの観測ノイズの標準偏差
            （mm）。既定値 0.0（ノイズなし）。
        distance_sigma_rel_per_m2: 観測原点からの距離に応じてノイズを
            増加させる係数。既定値 0.0（距離依存ノイズなし）。
        dropout_ratio: サンプルの欠測率。`0 <= dropout_ratio < 1` の
            範囲でなければならない（1 以上は「全サンプルが必ず脱落する」
            という無意味な設定になるため除外する）。既定値 0.0（欠測なし）。
        observer_x_mm, observer_y_mm, observer_z_mm: 観測原点の位置（mm）。
    """

    detection_start_delay_ms: float
    sample_period_ms: float
    sample_latency_ms: float
    prediction_latency_ms: float
    sigma_x_mm: float = 0.0
    sigma_y_mm: float = 0.0
    sigma_z_mm: float = 0.0
    distance_sigma_rel_per_m2: float = 0.0
    dropout_ratio: float = 0.0
    observer_x_mm: float = 0.0
    observer_y_mm: float = 0.0
    observer_z_mm: float = 0.0

    def __post_init__(self) -> None:
        """全不変条件を検証し、違反時は違反フィールド名と値を添えて拒否する。"""
        _require_nonneg_finite(self.detection_start_delay_ms, "detection_start_delay_ms")
        _require_nonneg_finite(self.sample_period_ms, "sample_period_ms")
        _require_nonneg_finite(self.sample_latency_ms, "sample_latency_ms")
        _require_nonneg_finite(self.prediction_latency_ms, "prediction_latency_ms")
        _require_nonneg_finite(self.sigma_x_mm, "sigma_x_mm")
        _require_nonneg_finite(self.sigma_y_mm, "sigma_y_mm")
        _require_nonneg_finite(self.sigma_z_mm, "sigma_z_mm")
        _require_nonneg_finite(self.distance_sigma_rel_per_m2, "distance_sigma_rel_per_m2")
        if not (math.isfinite(self.dropout_ratio) and 0.0 <= self.dropout_ratio < 1.0):
            raise ParameterError(
                f"dropout_ratio={self.dropout_ratio!r} は 0 以上 1 未満の"
                "有限値でなければならない（1 以上は全サンプルが必ず脱落する"
                "設定になり意味を持たない）。"
            )
        _require_finite(self.observer_x_mm, "observer_x_mm")
        _require_finite(self.observer_y_mm, "observer_y_mm")
        _require_finite(self.observer_z_mm, "observer_z_mm")


@dataclass(frozen=True, slots=True)
class DrivetrainParams:
    """移動体の運動性能パラメータ（要件4.1, 4.3, 4.7）。

    **最高速度・加速度上限・減速度上限・制御周期・指令反映遅れには既定値を
    与えない**（要件4.3: 根拠のない固定値としてモデルに埋め込まない）。
    呼び出し側は必ずこれらを明示的に指定しなければならず、省略した構築は
    dataclass の必須フィールドの標準動作により `TypeError` として失敗する。

    Attributes:
        max_speed_mm_s: 最高速度（mm/s）。正の有限値。既定値なし。
        max_accel_mm_s2: 加速度上限（mm/s^2）。正の有限値。既定値なし。
        max_decel_mm_s2: 減速度上限（mm/s^2）。正の有限値。既定値なし。
        control_period_ms: 制御周期（ms）。正の有限値。既定値なし。
        command_latency_ms: 指令反映遅れ（ms）。正の有限値。既定値なし。
        integration_step_ms: 運動の数値積分刻み（ms）。既定値 1.0。
        wheel_diameter_mm, motor_rpm, speed_efficiency: ホイール径・
            モータ回転数・速度効率。`from_wheel`（タスク1.5で追加予定）が
            最高速度を導出する際に使った値を追跡するための任意フィールド
            であり、本タスクでは値が与えられた場合のみ検証・保持する。
    """

    max_speed_mm_s: float
    max_accel_mm_s2: float
    max_decel_mm_s2: float
    control_period_ms: float
    command_latency_ms: float
    integration_step_ms: float = 1.0
    wheel_diameter_mm: float | None = None
    motor_rpm: float | None = None
    speed_efficiency: float | None = None

    def __post_init__(self) -> None:
        """全不変条件を検証し、違反時は違反フィールド名と値を添えて拒否する。"""
        _require_positive_finite(self.max_speed_mm_s, "max_speed_mm_s")
        _require_positive_finite(self.max_accel_mm_s2, "max_accel_mm_s2")
        _require_positive_finite(self.max_decel_mm_s2, "max_decel_mm_s2")
        _require_positive_finite(self.control_period_ms, "control_period_ms")
        _require_positive_finite(self.command_latency_ms, "command_latency_ms")
        _require_positive_finite(self.integration_step_ms, "integration_step_ms")
        if self.wheel_diameter_mm is not None:
            _require_positive_finite(self.wheel_diameter_mm, "wheel_diameter_mm")
        if self.motor_rpm is not None:
            _require_positive_finite(self.motor_rpm, "motor_rpm")
        if self.speed_efficiency is not None:
            _require_positive_finite(self.speed_efficiency, "speed_efficiency")

    @classmethod
    def from_wheel(
        cls,
        *,
        wheel_diameter_mm: float,
        motor_rpm: float,
        speed_efficiency: float,
        max_accel_mm_s2: float,
        max_decel_mm_s2: float,
        control_period_ms: float,
        command_latency_ms: float,
        integration_step_ms: float = 1.0,
    ) -> "DrivetrainParams":
        """ホイール径・モータ回転数・速度効率から最高速度を導出する（要件4.2）。

        最高速度（ホイール外周の周速）は、ホイール円周に毎秒回転数と
        速度効率を乗じて算出する:

            max_speed_mm_s = pi * wheel_diameter_mm * (motor_rpm / 60) * speed_efficiency

        これは `docs/drivetrain-spec.md §3.1` が示す理論周速の算出式と
        同じである（60mm ホイール・無負荷530RPM・効率1.0 のとき
        約1.66 m/s = 1665.9 mm/s となり、同ドキュメントの引用値と一致
        する）。

        導出に用いた `wheel_diameter_mm` / `motor_rpm` / `speed_efficiency`
        は、そのまま構築される `DrivetrainParams` の同名フィールドに保持
        されるため、結果からいつでも導出根拠を追跡できる（要件4.2）。
        検証は通常の構築（`cls(...)`）と同じ `__post_init__` を通るため、
        ここで独自の検証を重複させない。
        """
        max_speed_mm_s = math.pi * wheel_diameter_mm * (motor_rpm / 60.0) * speed_efficiency
        return cls(
            max_speed_mm_s=max_speed_mm_s,
            max_accel_mm_s2=max_accel_mm_s2,
            max_decel_mm_s2=max_decel_mm_s2,
            control_period_ms=control_period_ms,
            command_latency_ms=command_latency_ms,
            integration_step_ms=integration_step_ms,
            wheel_diameter_mm=wheel_diameter_mm,
            motor_rpm=motor_rpm,
            speed_efficiency=speed_efficiency,
        )


@dataclass(frozen=True, slots=True)
class CatchCriteria:
    """キャッチ成立の判定条件（要件5.1, 5.4, 5.5）。

    Attributes:
        policy: 「停止して待つ」か「通過キャッチを許容する」かの方針。
            方針そのものを確定させず、両方を比較材料として選べるように
            する（要件5.1, 5.8）。既定値は `STOP_AND_WAIT`。
        position_tolerance_mm: 水平位置の許容誤差（mm）。既定値 67.5 の
            導出根拠: キャッチ機構の開口半径から、対象物寸法
            （`ThrowParams.object_diameter_mm` の既定値 65.0mm、空き缶の
            仮値）の半分を引く考え方による。すなわち「開口の半径」から
            「対象物の半径」を差し引いた残りを、中心位置のずれとして
            許容できる量とみなす。開口寸法自体がまだ確定していないため、
            この 67.5mm は暫定値であり、実測（M1/M2）により見直す前提の
            仮値である（`tech.md` 開発標準1）。
        residual_speed_tolerance_mm_s: 残留速度の許容値（mm/s）。既定値
            200.0 の根拠: 「停止して待つ」方針において、目標点到達時に
            残ることを許容する、ごくわずかな漂流速度を想定した暫定値
            である。実測に基づく精密な値ではなく、**未実測の仮値**で
            あることを明示する（`tech.md` 開発標準1: 根拠のない精度を
            主張しない）。M1/M2 の実測結果を得たのち見直す前提とする。
    """

    policy: CatchPolicy = CatchPolicy.STOP_AND_WAIT
    position_tolerance_mm: float = 67.5
    residual_speed_tolerance_mm_s: float = 200.0

    def __post_init__(self) -> None:
        _require_nonneg_finite(self.position_tolerance_mm, "position_tolerance_mm")
        _require_nonneg_finite(
            self.residual_speed_tolerance_mm_s, "residual_speed_tolerance_mm_s"
        )


@dataclass(frozen=True, slots=True)
class LayoutParams:
    """投擲レイアウトのうち移動体の待機位置（要件10.1, 10.2）。

    投擲位置・投擲方向・投擲距離は `ThrowParams` の release 座標と
    速度・仰角・方位角から表現されるため、ここでは待機位置のみを持つ。
    """

    home_x_mm: float
    home_y_mm: float

    def __post_init__(self) -> None:
        _require_finite(self.home_x_mm, "home_x_mm")
        _require_finite(self.home_y_mm, "home_y_mm")


@dataclass(frozen=True, slots=True)
class ScenarioParams:
    """1シナリオ分の全入力パラメータ（要件1.4, 4.1, 5.1, 5.4, 9.1, 9.2, 10.1）。

    各ネストしたデータクラスは自身の `__post_init__` で構築時検証済みで
    あるため、`ScenarioParams` 自身はフィールド単位の追加検証を行わない
    （design.md「Params」Postconditions: 検証を通った `ScenarioParams` は
    以降のどの段でも再検証を必要としない）。

    Attributes:
        prediction: `prediction_core.PredictionConfig` をそのまま保持する。
            独自の予測設定型を作らない（design.md「Params」
            Responsibilities）。
        calibration_stage: 較正段階。既定値は `CalibrationStage.
            UNCALIBRATED`（未較正）（要件9.2）。
        provenance: 各パラメータの出所（実測 / 想定）の対応表（要件9.4）。
            キーは `PARAMETER_PATHS` のパス文字列と一致させなければ
            ならない。一致しないキーは `__post_init__` が `ParameterError`
            で拒否する（design.md「Params」Implementation Notes
            Risks: 「出所が黙って無視される事態を防ぐ」）。既定値は空の
            `dict`。
    """

    throw: ThrowParams
    dispersion: ThrowDispersion
    observation: ObservationParams
    drivetrain: DrivetrainParams
    catch: CatchCriteria
    layout: LayoutParams
    prediction: PredictionConfig
    calibration_stage: CalibrationStage = CalibrationStage.UNCALIBRATED
    provenance: Mapping[str, Provenance] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """`provenance` の各キーが `PARAMETER_PATHS` のパスと一致することを検証する（要件9.4）。

        `PARAMETER_PATHS` は本クラス定義の直後にモジュールレベルで構築
        されるため、この検証はどの `ScenarioParams` インスタンス構築時
        にも安全に参照できる（モジュール完全ロード後でなければ
        インスタンスは作られない）。
        """
        for key in self.provenance:
            if key not in PARAMETER_PATHS:
                raise ParameterError(
                    f"provenance のキー {key!r} は PARAMETER_PATHS のパス"
                    "文字列と一致しない。既知のパスのみを出所の対応表の"
                    "キーとして指定できる。"
                )


@dataclass(frozen=True, slots=True)
class ParameterPath:
    """`PARAMETER_PATHS` の1エントリ（要件4.2, 6.1, 9.4）。

    design.md「Params」は `PARAMETER_PATHS: Mapping[str, ParameterPath]`
    という型のみを宣言し、`ParameterPath` 自身の内部構造までは定めて
    いない（設計ギャップ）。ここでは、後続タスクが必要とする2つの機能
    ――(a) 単位文字列の報告（掃引エンジンの `AxisSpec.unit` 構築、
    タスク3.2）、(b) パス文字列からの値の取得・置換（`replace_by_path`）
    ――を満たす最小限の形として、以下の4フィールドを持つ frozen
    dataclass を選んだ。

    Attributes:
        path: パス文字列そのもの（例: `"throw.speed_mm_s"`,
            `"calibration_stage"`）。`PARAMETER_PATHS` の対応するキーと
            常に一致する。
        unit: フィールド名の接尾辞から導いた単位文字列（`_SUFFIX_UNITS`
            参照）。認識できない接尾辞・enum フィールドは `""`
            （無次元）とし、`motor_rpm` のみ例外的に `"rpm"` とする
            （`_FIELD_NAME_UNIT_OVERRIDES` 参照）。
        component: `ScenarioParams` 直下のネストしたデータクラス
            フィールド名（例: `"throw"`）。`ScenarioParams` 自身の
            直接のフィールド（例: `calibration_stage`）の場合は `None`。
        field_name: `component` の中のリーフフィールド名。`component`
            が `None` の場合は `ScenarioParams` 直下のフィールド名
            そのもの。
    """

    path: str
    unit: str
    component: str | None
    field_name: str


# フィールド名の接尾辞から単位を導くための表（design.md「Params」
# Implementation Notes: 「単位はフィールド名の接尾辞…から導く。手書きの
# 表を二重管理しない」）。この表自体はパス表そのものではなく単位の導出
# 規則であり、二重管理を避けるための唯一の情報源である。
#
# より特殊的な（長い）接尾辞を先に判定する。特に `_mm_s2` / `_mm_s` は
# `_mm` の前に判定しないと `_mm` に誤って一致してしまう。
_SUFFIX_UNITS: tuple[tuple[str, str], ...] = (
    ("_mm_s2", "mm/s^2"),
    ("_mm_s", "mm/s"),
    ("_per_m2", "1/m^2"),
    ("_mm", "mm"),
    ("_deg", "deg"),
    ("_ms", "ms"),
    ("_ratio", ""),
)

# 接尾辞では単位を判定できないフィールド名に対する個別の例外表。
# `motor_rpm` は回転数（rpm）であることが明確なので明示する。
# `policy` / `calibration_stage` のような enum フィールドと
# `speed_efficiency`（無次元の比）は、下の既定フォールバック `""` に
# 委ねる。
_FIELD_NAME_UNIT_OVERRIDES: Mapping[str, str] = MappingProxyType({"motor_rpm": "rpm"})


def _derive_unit(field_name: str) -> str:
    """フィールド名の接尾辞（または個別の例外表）から単位文字列を導く。

    どちらにも該当しない場合は `""`（無次元 / enum など単位を持たない
    フィールド）を返す。この既定フォールバックも本関数に一本化し、
    呼び出し側で単位判定ロジックを重複させない。
    """
    if field_name in _FIELD_NAME_UNIT_OVERRIDES:
        return _FIELD_NAME_UNIT_OVERRIDES[field_name]
    for suffix, unit in _SUFFIX_UNITS:
        if field_name.endswith(suffix):
            return unit
    return ""


# `PARAMETER_PATHS` から除外する `ScenarioParams` 直下のフィールド。
# - prediction: `prediction_core.PredictionConfig` であり、本パッケージが
#   所有しない上流型。design.md の依存関係表がこのパッケージの所有範囲を
#   `prediction_core` の内部にまで広げないよう明示的に線引きしている。
# - provenance: `Mapping[str, Provenance]` であり、掃引可能な単一の
#   スカラー/リーフ値ではない。
_EXCLUDED_SCENARIO_FIELDS: frozenset[str] = frozenset({"prediction", "provenance"})


def _build_parameter_paths() -> Mapping[str, ParameterPath]:
    """`ScenarioParams` のデータクラス木を走査し `PARAMETER_PATHS` を構築する。

    `dataclasses.fields()` で `ScenarioParams` 直下のフィールドを列挙し、
    値がさらにデータクラスであるものは1階層だけ再帰してリーフフィールド
    を `"<component>.<field>"` の形でパスにする。データクラスでない
    直接のフィールド（`calibration_stage`）はそのままのフィールド名を
    パスとする。手書きの表は一切持たず、この走査結果だけが唯一の情報源
    である。

    `ScenarioParams` は `from __future__ import annotations` の影響で
    `Field.type` が文字列注釈のままになるため、`typing.get_type_hints`
    で実体の型へ解決してからデータクラス判定を行う。この関数はクラスの
    構造を調べるだけで、いかなる `ScenarioParams` インスタンスも構築
    しない。
    """
    paths: dict[str, ParameterPath] = {}
    scenario_hints = get_type_hints(ScenarioParams)
    for scenario_field in fields(ScenarioParams):
        name = scenario_field.name
        if name in _EXCLUDED_SCENARIO_FIELDS:
            continue
        field_type = scenario_hints[name]
        if is_dataclass(field_type):
            component = name
            for nested_field in fields(field_type):
                leaf_name = nested_field.name
                path = f"{component}.{leaf_name}"
                paths[path] = ParameterPath(
                    path=path,
                    unit=_derive_unit(leaf_name),
                    component=component,
                    field_name=leaf_name,
                )
        else:
            paths[name] = ParameterPath(
                path=name,
                unit=_derive_unit(name),
                component=None,
                field_name=name,
            )
    return MappingProxyType(paths)


PARAMETER_PATHS: Mapping[str, ParameterPath] = _build_parameter_paths()
"""掃引の軸として指定できるパラメータパスの表（要件6.1, design.md
「Params」Invariants: 「`PARAMETER_PATHS` に載っているパスは必ず
`replace_by_path` で置換できる。未知のパスは `SweepDefinitionError` で
拒否する」）。モジュールロード時に一度だけ `_build_parameter_paths()` に
より生成される不変マッピング。
"""


# パス文字列からその値を coerce すべき enum 型への対応表。
# `dataclasses.replace` はフィールドの型への coerce を行わないため、
# 掃引エンジン（タスク3.2）が `AxisSpec.values` から渡す生の `str` を
# enum フィールドへ書き込む前に、ここで明示的に変換する。数値フィールド
# は各データクラスの `__post_init__` 検証にそのまま委ね、ここで重複した
# 検証を行わない。
_ENUM_PATH_TYPES: Mapping[str, type[StrEnum]] = MappingProxyType(
    {
        "catch.policy": CatchPolicy,
        "calibration_stage": CalibrationStage,
    }
)


def replace_by_path(params: ScenarioParams, path: str, value: float | str) -> ScenarioParams:
    """`path` が指すリーフフィールドだけを `value` に置き換えた新しい `ScenarioParams` を返す。

    `path` は `PARAMETER_PATHS` のキーでなければならない。未知のパスは
    掃引定義側の誤りとして `trajectory_sim.errors.SweepDefinitionError`
    を送出する（design.md「Params」Invariants）。

    全データクラスが `frozen=True` であるため、対象がネストした
    コンポーネント（例: `"throw.speed_mm_s"`）の場合は、まずそのコン
    ポーネントを `dataclasses.replace` し、次にそのコンポーネントを
    `ScenarioParams` へ `dataclasses.replace` する、という2段階の置換
    を行う。

    `catch.policy` / `calibration_stage` のような enum 値のパスに文字列
    が渡された場合は、対応する enum 型へ明示的に coerce してから置換
    する（`dataclasses.replace` 自体は型 coerce を行わないため）。それ
    以外の数値フィールドは coerce を行わず、置換後の再構築時に対象の
    データクラスの `__post_init__` がそのまま検証する。
    """
    parameter_path = PARAMETER_PATHS.get(path)
    if parameter_path is None:
        raise SweepDefinitionError(
            f"未知のパラメータパス: path={path!r} は PARAMETER_PATHS に"
            "存在しない。掃引の軸名には PARAMETER_PATHS のキーのみを"
            "指定できる。"
        )

    enum_type = _ENUM_PATH_TYPES.get(path)
    coerced_value: float | str | StrEnum = enum_type(value) if enum_type is not None else value

    if parameter_path.component is None:
        return replace(params, **{parameter_path.field_name: coerced_value})

    component_value = getattr(params, parameter_path.component)
    new_component = replace(component_value, **{parameter_path.field_name: coerced_value})
    return replace(params, **{parameter_path.component: new_component})


def make_sample(t_ms: float, x_mm: float, y_mm: float, z_mm: float) -> Sample:
    """`prediction_core.Sample` を構築する（design.md「Params」Integration）。

    `observation`（タスク2.4）が `prediction_core` を直接 import せずに
    `Sample` を構築できるよう、構築をここに集約する。`Sample` 自身は
    構築時検証を持たない（検証は `prediction_core.predict()` の境界で
    行われる）ため、本関数も値の変換や検証を行わずそのまま渡す。
    """
    return Sample(t_ms=t_ms, x_mm=x_mm, y_mm=y_mm, z_mm=z_mm)
