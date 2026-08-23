#include "drivetrain_control/config.hpp"

#include <cmath>

#include "drivetrain_control/units.hpp"

// drivetrain_control config validation (design.md L2 "DrivetrainConfig",
// 要件 6.2, 7.6, 7.7, 9.2, 10.2, 11.6, 12.2, 13.3, 15.1, 15.2, 15.3, 15.4,
// 15.6). See config.hpp for the field-by-field contract; this translation
// unit implements GeometryParams::equilateral() and validate() only.

namespace drivetrain_control {

namespace {

// 逆運動学行列 M の非退化判定に使う無次元の数値許容誤差（design.md
// Kinematics の行定義 [-sinα_i, cosα_i, R] より、det(M) は base_radius_mm
// に比例してスケールする。したがって base_radius_mm を掛けた絶対しきい値と
// 比較することで、機体の寸法に依らない同じ判定基準になる）。
//
// これは実機の性能値でも運用閾値でもなく、浮動小数点比較のための内部の
// 数値許容誤差である。本 Spec の「数値パラメータに既定値を与えない・値を
// 持たない」という方針（要件 15.1, 15.2, 15.3）は、実機の性能や運用条件を
// 表す値に対するものであり、この定数はそれに当たらない。
constexpr float kGeometryDegeneracyRelativeEpsilon = 1.0e-3f;

bool isFiniteF(float value) noexcept { return std::isfinite(value); }

ConfigDiagnostic makeDiag(ConfigError code, ConfigField field, std::uint8_t index = 0) noexcept {
  ConfigDiagnostic diag;
  diag.code = code;
  diag.field = field;
  diag.index = index;
  return diag;
}

// value が有限かつ正であることを確認する。
ConfigDiagnostic checkFinitePositive(float value, ConfigField field, std::uint8_t index = 0) noexcept {
  if (!isFiniteF(value)) {
    return makeDiag(ConfigError::kNotFinite, field, index);
  }
  if (!(value > 0.0f)) {
    return makeDiag(ConfigError::kNotPositive, field, index);
  }
  return ConfigDiagnostic{};
}

// 整数版: 固定幅整数は NaN/inf を持たないため正値性のみを確認する。
ConfigDiagnostic checkPositive(std::int32_t value, ConfigField field, std::uint8_t index = 0) noexcept {
  if (!(value > 0)) {
    return makeDiag(ConfigError::kNotPositive, field, index);
  }
  return ConfigDiagnostic{};
}

ConfigDiagnostic checkFinite(float value, ConfigField field, std::uint8_t index = 0) noexcept {
  if (!isFiniteF(value)) {
    return makeDiag(ConfigError::kNotFinite, field, index);
  }
  return ConfigDiagnostic{};
}

// value が有限かつ非負であることを確認する（0 を許す。例: PID ゲイン,
// PWM 上限の欠測時既定値）。負値は定義域外として扱う。
ConfigDiagnostic checkNonNegativeFinite(float value, ConfigField field, std::uint8_t index = 0) noexcept {
  if (!isFiniteF(value)) {
    return makeDiag(ConfigError::kNotFinite, field, index);
  }
  if (value < 0.0f) {
    return makeDiag(ConfigError::kOutOfRange, field, index);
  }
  return ConfigDiagnostic{};
}

// polarity[kWheelCount] の各要素が厳密に +1 または -1 であることを確認する。
ConfigDiagnostic checkPolarity(const std::int8_t polarity[kWheelCount], ConfigField field) noexcept {
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    if (polarity[i] != 1 && polarity[i] != -1) {
      return makeDiag(ConfigError::kOutOfRange, field, i);
    }
  }
  return ConfigDiagnostic{};
}

}  // namespace

GeometryParams GeometryParams::equilateral(float base_radius_mm, float first_wheel_angle_rad) noexcept {
  GeometryParams params{};
  params.base_radius_mm = base_radius_mm;
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    const float angle = first_wheel_angle_rad + static_cast<float>(i) * (units::kTwoPi / 3.0f);
    params.wheel_angle_rad[i] = units::wrapAngle(angle);
  }
  return params;
}

ConfigDiagnostic validate(const DrivetrainConfig& config) noexcept {
  ConfigDiagnostic diag;

  // --- GeometryParams（要件 6.2, 6.6）----------------------------------------
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    diag = checkFinite(config.geometry.wheel_angle_rad[i], ConfigField::kGeometryWheelAngle, i);
    if (!diag.ok()) return diag;
  }
  diag = checkFinitePositive(config.geometry.base_radius_mm, ConfigField::kGeometryBaseRadius);
  if (!diag.ok()) return diag;

  {
    // 逆運動学行列 M の行 i = [-sinα_i, cosα_i, R]（design.md Kinematics）。
    // task 3.3 の Kinematics クラスは構築せず、非退化判定だけをここで行う。
    float m[kWheelCount][3];
    for (std::uint8_t i = 0; i < kWheelCount; ++i) {
      m[i][0] = -std::sin(config.geometry.wheel_angle_rad[i]);
      m[i][1] = std::cos(config.geometry.wheel_angle_rad[i]);
      m[i][2] = config.geometry.base_radius_mm;
    }
    const float det = m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) -
                       m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0]) +
                       m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]);
    const float epsilon = kGeometryDegeneracyRelativeEpsilon * config.geometry.base_radius_mm;
    if (!(std::fabs(det) > epsilon)) {
      return makeDiag(ConfigError::kDegenerateGeometry, ConfigField::kGeometryDegenerate);
    }
  }

  // --- EncoderParams（要件 7.6, 7.7）------------------------------------------
  diag = checkPositive(config.encoder.counts_per_wheel_rev, ConfigField::kEncoderCountsPerWheelRev);
  if (!diag.ok()) return diag;
  diag = checkFinitePositive(config.encoder.wheel_diameter_mm, ConfigField::kEncoderWheelDiameter);
  if (!diag.ok()) return diag;
  diag = checkPositive(config.encoder.raw_modulus, ConfigField::kEncoderRawModulus);
  if (!diag.ok()) return diag;
  diag = checkPolarity(config.encoder.polarity, ConfigField::kEncoderPolarity);
  if (!diag.ok()) return diag;

  // --- OutputParams -----------------------------------------------------------
  diag = checkPolarity(config.output.polarity, ConfigField::kOutputPolarity);
  if (!diag.ok()) return diag;
  diag = checkFinitePositive(config.output.absolute_max_duty, ConfigField::kOutputAbsoluteMaxDuty);
  if (!diag.ok()) return diag;
  if (config.output.absolute_max_duty > 1.0f) {
    return makeDiag(ConfigError::kOutOfRange, ConfigField::kOutputAbsoluteMaxDuty);
  }

  // --- MotionLimits（要件 5.4, 6.5, 17.1）-------------------------------------
  diag = checkFinitePositive(config.limits.max_body_speed_mm_s, ConfigField::kLimitsMaxBodySpeed);
  if (!diag.ok()) return diag;
  diag = checkFinitePositive(config.limits.max_body_omega_rad_s, ConfigField::kLimitsMaxBodyOmega);
  if (!diag.ok()) return diag;
  diag = checkFinitePositive(config.limits.max_wheel_speed_mm_s, ConfigField::kLimitsMaxWheelSpeed);
  if (!diag.ok()) return diag;

  // --- PidParams（輪ごと。要件 9.2, 9.4, 9.6）---------------------------------
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    diag = checkNonNegativeFinite(config.pid[i].kp, ConfigField::kPidKp, i);
    if (!diag.ok()) return diag;
    diag = checkNonNegativeFinite(config.pid[i].ki, ConfigField::kPidKi, i);
    if (!diag.ok()) return diag;
    diag = checkNonNegativeFinite(config.pid[i].kd, ConfigField::kPidKd, i);
    if (!diag.ok()) return diag;
    diag = checkFinitePositive(config.pid[i].integral_limit, ConfigField::kPidIntegralLimit, i);
    if (!diag.ok()) return diag;
  }

  // --- LockParams（保護①。要件 10.2, 10.5）-----------------------------------
  diag = checkFinitePositive(config.lock.duty_threshold, ConfigField::kLockDutyThreshold);
  if (!diag.ok()) return diag;
  if (config.lock.duty_threshold > 1.0f) {
    return makeDiag(ConfigError::kOutOfRange, ConfigField::kLockDutyThreshold);
  }
  diag = checkFinitePositive(config.lock.speed_threshold_mm_s, ConfigField::kLockSpeedThreshold);
  if (!diag.ok()) return diag;
  diag = checkPositive(config.lock.duration_ms, ConfigField::kLockDuration);
  if (!diag.ok()) return diag;
  diag = checkPositive(config.lock.clear_duration_ms, ConfigField::kLockClearDuration);
  if (!diag.ok()) return diag;

  // --- LowVoltageParams（保護②。要件 11.6）-----------------------------------
  diag = checkPositive(config.low_voltage.warn_milli_volts, ConfigField::kLowVoltageWarn);
  if (!diag.ok()) return diag;
  diag = checkPositive(config.low_voltage.stop_milli_volts, ConfigField::kLowVoltageStop);
  if (!diag.ok()) return diag;
  diag = checkPositive(config.low_voltage.recover_milli_volts, ConfigField::kLowVoltageRecover);
  if (!diag.ok()) return diag;
  if (!(config.low_voltage.recover_milli_volts > config.low_voltage.stop_milli_volts)) {
    return makeDiag(ConfigError::kOrderingViolated, ConfigField::kLowVoltageRecover);
  }
  if (config.low_voltage.warn_milli_volts < config.low_voltage.stop_milli_volts) {
    return makeDiag(ConfigError::kOrderingViolated, ConfigField::kLowVoltageWarn);
  }
  if (config.low_voltage.average_window < 1) {
    return makeDiag(ConfigError::kNotPositive, ConfigField::kLowVoltageAverageWindow);
  }
  if (config.low_voltage.average_window > kMaxVoltageWindow) {
    return makeDiag(ConfigError::kWindowTooLarge, ConfigField::kLowVoltageAverageWindow);
  }
  diag = checkPositive(config.low_voltage.stop_duration_ms, ConfigField::kLowVoltageStopDuration);
  if (!diag.ok()) return diag;
  diag = checkPositive(config.low_voltage.unavailable_duration_ms, ConfigField::kLowVoltageUnavailableDuration);
  if (!diag.ok()) return diag;

  // --- VoltageScalerParams（要件 11.7）----------------------------------------
  if (config.voltage_scaler.point_count < 2) {
    return makeDiag(ConfigError::kTableTooShort, ConfigField::kVoltageScalerPointCount);
  }
  if (config.voltage_scaler.point_count > kMaxVoltagePoints) {
    return makeDiag(ConfigError::kTableTooLong, ConfigField::kVoltageScalerPointCount);
  }
  for (std::uint8_t i = 1; i < config.voltage_scaler.point_count; ++i) {
    if (config.voltage_scaler.table[i].raw <= config.voltage_scaler.table[i - 1].raw) {
      return makeDiag(ConfigError::kTableNotMonotonic, ConfigField::kVoltageScalerTable, i);
    }
  }

  // --- PwmCeilingParams（保護③。要件 12.2, 12.5, 12.6）-----------------------
  diag = checkPositive(config.pwm_ceiling.reference_milli_volts, ConfigField::kPwmCeilingReferenceMilliVolts);
  if (!diag.ok()) return diag;
  diag = checkNonNegativeFinite(config.pwm_ceiling.fallback_duty, ConfigField::kPwmCeilingFallbackDuty);
  if (!diag.ok()) return diag;
  if (config.pwm_ceiling.fallback_duty > config.output.absolute_max_duty) {
    return makeDiag(ConfigError::kOrderingViolated, ConfigField::kPwmCeilingFallbackDuty);
  }

  // --- WatchdogParams（保護④。要件 13.3）--------------------------------------
  diag = checkPositive(config.watchdog.timeout_ms, ConfigField::kWatchdogTimeout);
  if (!diag.ok()) return diag;

  return ConfigDiagnostic{};  // ok
}

}  // namespace drivetrain_control
