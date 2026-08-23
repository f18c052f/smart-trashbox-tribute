#pragma once

// drivetrain_control config errors (design.md L0 / "Errors", 要件 15.4).
//
// 設定検証の失敗を、例外を使わずに「原因の種別」（ConfigError）・
// 「違反したパラメータ」（ConfigField）・「輪番号やテーブル位置の添字」
// （ConfigDiagnostic::index）の3点で返せる型を定義する。
//
// Invariants: 核は例外を投げない。IDF の既定設定（例外無効）で動くこと。
// L0 は標準ヘッダのみに依存する（design.md "Dependency Direction"）。

#include <cstdint>

namespace drivetrain_control {

enum class ConfigError : std::uint16_t {
  kNone = 0,
  kNotFinite,           // NaN / inf
  kNotPositive,         // <= 0 が許されない箇所
  kOutOfRange,          // 定義域外（デューティが 1 を超える等）
  kOrderingViolated,    // 復帰閾値 <= 停止閾値 等の順序違反
  kDegenerateGeometry,  // 逆運動学行列が特異に近い
  kTableNotMonotonic,   // 電圧校正テーブルが単調でない
  kTableTooShort,       // 校正点が 2 点未満
  kTableTooLong,        // 校正点が上限を超える
  kWindowTooLarge,      // 移動平均窓が上限を超える
};

enum class ConfigField : std::uint16_t {
  kNone = 0,
  // タスク 2.3（config.hpp / DrivetrainConfig）が定義する各パラメータ構造体
  // のフィールドへ、概ね 1:1 で対応する列挙子。ConfigDiagnostic::index が
  // 輪番号やテーブル位置を補う（要件 15.4）。

  // GeometryParams（要件 6.2, 6.6）
  kGeometryWheelAngle,
  kGeometryBaseRadius,
  kGeometryDegenerate,  // 逆運動学行列の非退化判定（個別フィールドではなく幾何全体）

  // EncoderParams（要件 7.6, 7.7）
  kEncoderCountsPerWheelRev,
  kEncoderWheelDiameter,
  kEncoderRawModulus,
  kEncoderPolarity,

  // OutputParams
  kOutputPolarity,
  kOutputAbsoluteMaxDuty,

  // MotionLimits
  kLimitsMaxBodySpeed,
  kLimitsMaxBodyOmega,
  kLimitsMaxWheelSpeed,

  // PidParams（要件 9.2, 9.4, 9.6。輪ごと）
  kPidKp,
  kPidKi,
  kPidKd,
  kPidIntegralLimit,

  // LockParams（保護①。要件 10.2, 10.5）
  kLockDutyThreshold,
  kLockSpeedThreshold,
  kLockDuration,
  kLockClearDuration,

  // LowVoltageParams（保護②。要件 11.6）
  kLowVoltageWarn,
  kLowVoltageStop,
  kLowVoltageRecover,
  kLowVoltageAverageWindow,
  kLowVoltageStopDuration,
  kLowVoltageUnavailableDuration,

  // VoltageScalerParams（要件 11.7）
  kVoltageScalerPointCount,
  kVoltageScalerTable,

  // PwmCeilingParams（保護③。要件 12.2, 12.5, 12.6）
  kPwmCeilingReferenceMilliVolts,
  kPwmCeilingFallbackDuty,

  // WatchdogParams（保護④。要件 13.3）
  kWatchdogTimeout,
};

struct ConfigDiagnostic {
  ConfigError code   = ConfigError::kNone;
  ConfigField field  = ConfigField::kNone;
  std::uint8_t index = 0;  // 輪ごとパラメータのときの輪番号、テーブルのときの要素位置
  constexpr bool ok() const noexcept { return code == ConfigError::kNone; }
};

}  // namespace drivetrain_control
