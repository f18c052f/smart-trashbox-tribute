#include <unity.h>

#include <cstdint>
#include <limits>

#include "drivetrain_control/config.hpp"
#include "drivetrain_control/errors.hpp"
#include "drivetrain_control/types.hpp"
#include "drivetrain_control/units.hpp"

// drivetrain-core task 2.3: 全パラメータ構造体と設定検証（config.hpp /
// config.cpp）を検証するホストテスト。
//
// design.md "DrivetrainConfig"（L2）の Implementation Notes が挙げる検証
// 項目（有限性・正値性・定義域・順序関係・幾何の非退化・電圧テーブルの
// 単調性と点数・移動平均窓の上限・polarity が ±1・integral_limit が有限）
// を、それぞれ最初に見つかった違反として ConfigDiagnostic が返すことを
// 確認する。要件 6.2, 7.6, 7.7, 9.2, 10.2, 11.6, 12.2, 13.3, 15.1, 15.2,
// 15.3, 15.4, 15.6。
//
// ⚠️ ここで使う数値（機体寸法・ゲイン・閾値等）は「検証ロジックが正しく
// 動くこと」だけを確かめるための架空の値であり、実機の性能や仕様値では
// ない（要件 15.1, 15.2）。

using drivetrain_control::ConfigDiagnostic;
using drivetrain_control::ConfigError;
using drivetrain_control::ConfigField;
using drivetrain_control::DrivetrainConfig;
using drivetrain_control::EncoderParams;
using drivetrain_control::GeometryParams;
using drivetrain_control::LockParams;
using drivetrain_control::LowVoltageParams;
using drivetrain_control::MotionLimits;
using drivetrain_control::OutputParams;
using drivetrain_control::PidParams;
using drivetrain_control::PwmCeilingParams;
using drivetrain_control::VoltageScalerParams;
using drivetrain_control::WatchdogParams;
using drivetrain_control::kMaxVoltagePoints;
using drivetrain_control::kMaxVoltageWindow;
using drivetrain_control::kWheelCount;

void setUp(void) {}
void tearDown(void) {}

namespace {

// 検証ロジックが正しく動くことだけを確かめるための架空の妥当値一式
// （実機の性能値ではない。要件 15.1, 15.2）。
DrivetrainConfig makeValidConfig() {
  DrivetrainConfig config{};

  config.geometry = GeometryParams::equilateral(150.0f, 0.0f);

  config.encoder.counts_per_wheel_rev = 836;
  config.encoder.wheel_diameter_mm = 60.0f;
  config.encoder.raw_modulus = 65536;
  config.encoder.polarity[0] = 1;
  config.encoder.polarity[1] = -1;
  config.encoder.polarity[2] = 1;

  config.output.polarity[0] = 1;
  config.output.polarity[1] = -1;
  config.output.polarity[2] = 1;
  config.output.absolute_max_duty = 1.0f;

  config.limits.max_body_speed_mm_s = 800.0f;
  config.limits.max_body_omega_rad_s = 6.0f;
  config.limits.max_wheel_speed_mm_s = 1000.0f;

  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    config.pid[i].kp = 0.5f;
    config.pid[i].ki = 2.0f;
    config.pid[i].kd = 0.0f;
    config.pid[i].integral_limit = 1.0f;
  }

  config.lock.duty_threshold = 0.3f;
  config.lock.speed_threshold_mm_s = 10.0f;
  config.lock.duration_ms = 300;
  config.lock.clear_duration_ms = 200;
  config.lock.latching = true;

  config.low_voltage.warn_milli_volts = 10800;
  config.low_voltage.stop_milli_volts = 10200;
  config.low_voltage.recover_milli_volts = 10800;
  config.low_voltage.average_window = 8;
  config.low_voltage.stop_duration_ms = 500;
  config.low_voltage.unavailable_duration_ms = 500;
  config.low_voltage.latching = true;

  config.voltage_scaler.table[0] = {0, 0};
  config.voltage_scaler.table[1] = {2048, 6000};
  config.voltage_scaler.table[2] = {4095, 12600};
  config.voltage_scaler.point_count = 3;

  config.pwm_ceiling.enabled = true;
  config.pwm_ceiling.reference_milli_volts = 12600;
  config.pwm_ceiling.fallback_duty = 0.2f;
  config.pwm_ceiling.override_fn = nullptr;

  config.watchdog.timeout_ms = 300;

  return config;
}

}  // namespace

// ---------------------------------------------------------------------------
// ゼロ初期化・妥当な設定の両端
// ---------------------------------------------------------------------------

void test_zero_initialized_config_is_rejected(void) {
  // 数値パラメータに既定初期化子を与えていないため、`{}` は各数値メンバを
  // 0 へ値初期化する。counts_per_wheel_rev などの正値性検証に必ず引っかかる
  // （要件 15.3, 15.4）。
  DrivetrainConfig config{};
  const ConfigDiagnostic diag = drivetrain_control::validate(config);
  TEST_ASSERT_FALSE(diag.ok());
}

void test_valid_baseline_config_is_accepted(void) {
  const DrivetrainConfig config = makeValidConfig();
  const ConfigDiagnostic diag = drivetrain_control::validate(config);
  TEST_ASSERT_TRUE(diag.ok());
  TEST_ASSERT_TRUE(ConfigError::kNone == diag.code);
}

// ---------------------------------------------------------------------------
// GeometryParams
// ---------------------------------------------------------------------------

void test_geometry_wheel_angle_non_finite_is_rejected(void) {
  DrivetrainConfig config = makeValidConfig();
  config.geometry.wheel_angle_rad[1] = std::numeric_limits<float>::quiet_NaN();
  const ConfigDiagnostic diag = drivetrain_control::validate(config);
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(ConfigError::kNotFinite == diag.code);
  TEST_ASSERT_TRUE(ConfigField::kGeometryWheelAngle == diag.field);
  TEST_ASSERT_EQUAL_UINT8(1, diag.index);
}

void test_geometry_base_radius_non_positive_is_rejected(void) {
  DrivetrainConfig config = makeValidConfig();
  config.geometry.base_radius_mm = 0.0f;
  const ConfigDiagnostic diag = drivetrain_control::validate(config);
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(ConfigError::kNotPositive == diag.code);
  TEST_ASSERT_TRUE(ConfigField::kGeometryBaseRadius == diag.field);
}

void test_degenerate_geometry_is_rejected_with_specific_error(void) {
  // 3輪すべて同じ取付角 -> 逆運動学行列 M の3行が一致し、|det(M)| == 0。
  DrivetrainConfig config = makeValidConfig();
  config.geometry.wheel_angle_rad[0] = 0.0f;
  config.geometry.wheel_angle_rad[1] = 0.0f;
  config.geometry.wheel_angle_rad[2] = 0.0f;
  const ConfigDiagnostic diag = drivetrain_control::validate(config);
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(ConfigError::kDegenerateGeometry == diag.code);
  TEST_ASSERT_TRUE(ConfigField::kGeometryDegenerate == diag.field);
}

void test_geometry_equilateral_helper_spaces_wheels_120_degrees_apart(void) {
  const GeometryParams geometry = GeometryParams::equilateral(200.0f, 0.0f);
  TEST_ASSERT_FLOAT_WITHIN(1e-4f, 200.0f, geometry.base_radius_mm);
  TEST_ASSERT_FLOAT_WITHIN(1e-4f, 0.0f, geometry.wheel_angle_rad[0]);
  TEST_ASSERT_FLOAT_WITHIN(1e-4f, drivetrain_control::units::kTwoPi / 3.0f, geometry.wheel_angle_rad[1]);
  TEST_ASSERT_FLOAT_WITHIN(1e-4f, -drivetrain_control::units::kTwoPi / 3.0f, geometry.wheel_angle_rad[2]);

  // equilateral() が組み立てた幾何は、それ以外を妥当値で埋めれば validate()
  // を通過する（非退化であることの実効的な裏取り）。
  DrivetrainConfig config = makeValidConfig();
  config.geometry = geometry;
  const ConfigDiagnostic diag = drivetrain_control::validate(config);
  TEST_ASSERT_TRUE(diag.ok());
}

// ---------------------------------------------------------------------------
// EncoderParams
// ---------------------------------------------------------------------------

void test_encoder_counts_per_wheel_rev_non_positive_is_rejected(void) {
  DrivetrainConfig config = makeValidConfig();
  config.encoder.counts_per_wheel_rev = 0;
  const ConfigDiagnostic diag = drivetrain_control::validate(config);
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(ConfigError::kNotPositive == diag.code);
  TEST_ASSERT_TRUE(ConfigField::kEncoderCountsPerWheelRev == diag.field);
}

void test_encoder_wheel_diameter_non_positive_is_rejected(void) {
  DrivetrainConfig config = makeValidConfig();
  config.encoder.wheel_diameter_mm = -1.0f;
  const ConfigDiagnostic diag = drivetrain_control::validate(config);
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(ConfigError::kNotPositive == diag.code);
  TEST_ASSERT_TRUE(ConfigField::kEncoderWheelDiameter == diag.field);
}

void test_encoder_raw_modulus_non_positive_is_rejected(void) {
  DrivetrainConfig config = makeValidConfig();
  config.encoder.raw_modulus = 0;
  const ConfigDiagnostic diag = drivetrain_control::validate(config);
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(ConfigError::kNotPositive == diag.code);
  TEST_ASSERT_TRUE(ConfigField::kEncoderRawModulus == diag.field);
}

void test_encoder_polarity_invalid_value_is_rejected_with_index(void) {
  DrivetrainConfig config = makeValidConfig();
  config.encoder.polarity[2] = 2;  // ±1 以外
  const ConfigDiagnostic diag = drivetrain_control::validate(config);
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(ConfigError::kOutOfRange == diag.code);
  TEST_ASSERT_TRUE(ConfigField::kEncoderPolarity == diag.field);
  TEST_ASSERT_EQUAL_UINT8(2, diag.index);
}

// ---------------------------------------------------------------------------
// OutputParams
// ---------------------------------------------------------------------------

void test_output_polarity_invalid_value_is_rejected(void) {
  DrivetrainConfig config = makeValidConfig();
  config.output.polarity[0] = 0;  // ±1 以外
  const ConfigDiagnostic diag = drivetrain_control::validate(config);
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(ConfigError::kOutOfRange == diag.code);
  TEST_ASSERT_TRUE(ConfigField::kOutputPolarity == diag.field);
  TEST_ASSERT_EQUAL_UINT8(0, diag.index);
}

void test_output_absolute_max_duty_non_positive_is_rejected(void) {
  DrivetrainConfig config = makeValidConfig();
  config.output.absolute_max_duty = 0.0f;
  const ConfigDiagnostic diag = drivetrain_control::validate(config);
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(ConfigError::kNotPositive == diag.code);
  TEST_ASSERT_TRUE(ConfigField::kOutputAbsoluteMaxDuty == diag.field);
}

void test_output_absolute_max_duty_exceeding_one_is_rejected(void) {
  DrivetrainConfig config = makeValidConfig();
  config.output.absolute_max_duty = 1.5f;
  const ConfigDiagnostic diag = drivetrain_control::validate(config);
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(ConfigError::kOutOfRange == diag.code);
  TEST_ASSERT_TRUE(ConfigField::kOutputAbsoluteMaxDuty == diag.field);
}

// ---------------------------------------------------------------------------
// MotionLimits
// ---------------------------------------------------------------------------

void test_limits_max_body_speed_non_positive_is_rejected(void) {
  DrivetrainConfig config = makeValidConfig();
  config.limits.max_body_speed_mm_s = 0.0f;
  const ConfigDiagnostic diag = drivetrain_control::validate(config);
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(ConfigError::kNotPositive == diag.code);
  TEST_ASSERT_TRUE(ConfigField::kLimitsMaxBodySpeed == diag.field);
}

void test_limits_max_body_omega_non_finite_is_rejected(void) {
  DrivetrainConfig config = makeValidConfig();
  config.limits.max_body_omega_rad_s = std::numeric_limits<float>::infinity();
  const ConfigDiagnostic diag = drivetrain_control::validate(config);
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(ConfigError::kNotFinite == diag.code);
  TEST_ASSERT_TRUE(ConfigField::kLimitsMaxBodyOmega == diag.field);
}

void test_limits_max_wheel_speed_non_positive_is_rejected(void) {
  DrivetrainConfig config = makeValidConfig();
  config.limits.max_wheel_speed_mm_s = -5.0f;
  const ConfigDiagnostic diag = drivetrain_control::validate(config);
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(ConfigError::kNotPositive == diag.code);
  TEST_ASSERT_TRUE(ConfigField::kLimitsMaxWheelSpeed == diag.field);
}

// ---------------------------------------------------------------------------
// PidParams（輪ごと。要件 9.2, 9.4, 9.6）
// ---------------------------------------------------------------------------

void test_pid_gain_negative_is_rejected_with_index(void) {
  DrivetrainConfig config = makeValidConfig();
  config.pid[2].kp = -1.0f;
  const ConfigDiagnostic diag = drivetrain_control::validate(config);
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(ConfigError::kOutOfRange == diag.code);
  TEST_ASSERT_TRUE(ConfigField::kPidKp == diag.field);
  TEST_ASSERT_EQUAL_UINT8(2, diag.index);
}

void test_pid_integral_limit_non_finite_is_rejected(void) {
  DrivetrainConfig config = makeValidConfig();
  config.pid[0].integral_limit = std::numeric_limits<float>::infinity();
  const ConfigDiagnostic diag = drivetrain_control::validate(config);
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(ConfigError::kNotFinite == diag.code);
  TEST_ASSERT_TRUE(ConfigField::kPidIntegralLimit == diag.field);
  TEST_ASSERT_EQUAL_UINT8(0, diag.index);
}

void test_pid_integral_limit_non_positive_is_rejected(void) {
  DrivetrainConfig config = makeValidConfig();
  config.pid[1].integral_limit = 0.0f;
  const ConfigDiagnostic diag = drivetrain_control::validate(config);
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(ConfigError::kNotPositive == diag.code);
  TEST_ASSERT_TRUE(ConfigField::kPidIntegralLimit == diag.field);
  TEST_ASSERT_EQUAL_UINT8(1, diag.index);
}

// ---------------------------------------------------------------------------
// LockParams（保護①。要件 10.2, 10.5）
// ---------------------------------------------------------------------------

void test_lock_duty_threshold_exceeding_one_is_rejected(void) {
  DrivetrainConfig config = makeValidConfig();
  config.lock.duty_threshold = 1.2f;
  const ConfigDiagnostic diag = drivetrain_control::validate(config);
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(ConfigError::kOutOfRange == diag.code);
  TEST_ASSERT_TRUE(ConfigField::kLockDutyThreshold == diag.field);
}

void test_lock_duration_non_positive_is_rejected(void) {
  DrivetrainConfig config = makeValidConfig();
  config.lock.duration_ms = 0;
  const ConfigDiagnostic diag = drivetrain_control::validate(config);
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(ConfigError::kNotPositive == diag.code);
  TEST_ASSERT_TRUE(ConfigField::kLockDuration == diag.field);
}

void test_lock_clear_duration_non_positive_is_rejected(void) {
  DrivetrainConfig config = makeValidConfig();
  config.lock.clear_duration_ms = -1;
  const ConfigDiagnostic diag = drivetrain_control::validate(config);
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(ConfigError::kNotPositive == diag.code);
  TEST_ASSERT_TRUE(ConfigField::kLockClearDuration == diag.field);
}

void test_lock_params_default_latching_is_true_and_is_a_behavior_selector(void) {
  // latching は既定値を持ってよい唯一の種別（振る舞いの選択）。要件 15.3。
  LockParams lock{};
  TEST_ASSERT_TRUE(lock.latching);
}

// ---------------------------------------------------------------------------
// LowVoltageParams（保護②。要件 11.6）
// ---------------------------------------------------------------------------

void test_low_voltage_recover_not_greater_than_stop_is_rejected_with_ordering_error(void) {
  DrivetrainConfig config = makeValidConfig();
  config.low_voltage.recover_milli_volts = config.low_voltage.stop_milli_volts;  // recover > stop を満たさない
  const ConfigDiagnostic diag = drivetrain_control::validate(config);
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(ConfigError::kOrderingViolated == diag.code);
  TEST_ASSERT_TRUE(ConfigField::kLowVoltageRecover == diag.field);
}

void test_low_voltage_warn_less_than_stop_is_rejected_with_ordering_error(void) {
  DrivetrainConfig config = makeValidConfig();
  config.low_voltage.warn_milli_volts = config.low_voltage.stop_milli_volts - 100;  // warn >= stop を満たさない
  const ConfigDiagnostic diag = drivetrain_control::validate(config);
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(ConfigError::kOrderingViolated == diag.code);
  TEST_ASSERT_TRUE(ConfigField::kLowVoltageWarn == diag.field);
}

void test_low_voltage_warn_equal_to_stop_is_accepted(void) {
  // design.md 明記の順序は warn >= stop（等号を許す）。
  DrivetrainConfig config = makeValidConfig();
  config.low_voltage.warn_milli_volts = config.low_voltage.stop_milli_volts;
  const ConfigDiagnostic diag = drivetrain_control::validate(config);
  TEST_ASSERT_TRUE(diag.ok());
}

void test_low_voltage_average_window_zero_is_rejected(void) {
  DrivetrainConfig config = makeValidConfig();
  config.low_voltage.average_window = 0;
  const ConfigDiagnostic diag = drivetrain_control::validate(config);
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(ConfigError::kNotPositive == diag.code);
  TEST_ASSERT_TRUE(ConfigField::kLowVoltageAverageWindow == diag.field);
}

void test_low_voltage_average_window_exceeding_capacity_is_rejected(void) {
  DrivetrainConfig config = makeValidConfig();
  config.low_voltage.average_window = static_cast<std::uint8_t>(kMaxVoltageWindow + 1);
  const ConfigDiagnostic diag = drivetrain_control::validate(config);
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(ConfigError::kWindowTooLarge == diag.code);
  TEST_ASSERT_TRUE(ConfigField::kLowVoltageAverageWindow == diag.field);
}

void test_low_voltage_average_window_at_capacity_is_accepted(void) {
  DrivetrainConfig config = makeValidConfig();
  config.low_voltage.average_window = kMaxVoltageWindow;
  const ConfigDiagnostic diag = drivetrain_control::validate(config);
  TEST_ASSERT_TRUE(diag.ok());
}

void test_low_voltage_stop_duration_non_positive_is_rejected(void) {
  DrivetrainConfig config = makeValidConfig();
  config.low_voltage.stop_duration_ms = 0;
  const ConfigDiagnostic diag = drivetrain_control::validate(config);
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(ConfigError::kNotPositive == diag.code);
  TEST_ASSERT_TRUE(ConfigField::kLowVoltageStopDuration == diag.field);
}

void test_low_voltage_unavailable_duration_non_positive_is_rejected(void) {
  DrivetrainConfig config = makeValidConfig();
  config.low_voltage.unavailable_duration_ms = 0;
  const ConfigDiagnostic diag = drivetrain_control::validate(config);
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(ConfigError::kNotPositive == diag.code);
  TEST_ASSERT_TRUE(ConfigField::kLowVoltageUnavailableDuration == diag.field);
}

void test_low_voltage_params_default_latching_is_true(void) {
  LowVoltageParams lv{};
  TEST_ASSERT_TRUE(lv.latching);
}

// ---------------------------------------------------------------------------
// VoltageScalerParams（要件 11.7）
// ---------------------------------------------------------------------------

void test_voltage_scaler_point_count_below_two_is_rejected_with_table_too_short(void) {
  DrivetrainConfig config = makeValidConfig();
  config.voltage_scaler.point_count = 1;
  const ConfigDiagnostic diag = drivetrain_control::validate(config);
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(ConfigError::kTableTooShort == diag.code);
  TEST_ASSERT_TRUE(ConfigField::kVoltageScalerPointCount == diag.field);
}

void test_voltage_scaler_point_count_exceeding_capacity_is_rejected_with_table_too_long(void) {
  DrivetrainConfig config = makeValidConfig();
  config.voltage_scaler.point_count = static_cast<std::uint8_t>(kMaxVoltagePoints + 1);
  const ConfigDiagnostic diag = drivetrain_control::validate(config);
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(ConfigError::kTableTooLong == diag.code);
  TEST_ASSERT_TRUE(ConfigField::kVoltageScalerPointCount == diag.field);
}

void test_voltage_scaler_table_not_strictly_increasing_is_rejected_with_index(void) {
  DrivetrainConfig config = makeValidConfig();
  config.voltage_scaler.table[1].raw = config.voltage_scaler.table[0].raw;  // 単調増加でない
  const ConfigDiagnostic diag = drivetrain_control::validate(config);
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(ConfigError::kTableNotMonotonic == diag.code);
  TEST_ASSERT_TRUE(ConfigField::kVoltageScalerTable == diag.field);
  TEST_ASSERT_EQUAL_UINT8(1, diag.index);
}

void test_voltage_scaler_two_point_table_is_accepted(void) {
  // 2点なら分圧比のみの線形換算に縮退する（design.md "VoltageScaler"）。
  DrivetrainConfig config = makeValidConfig();
  config.voltage_scaler.table[0] = {0, 0};
  config.voltage_scaler.table[1] = {4095, 12600};
  config.voltage_scaler.point_count = 2;
  const ConfigDiagnostic diag = drivetrain_control::validate(config);
  TEST_ASSERT_TRUE(diag.ok());
}

// ---------------------------------------------------------------------------
// PwmCeilingParams（保護③。要件 12.2, 12.5, 12.6）
// ---------------------------------------------------------------------------

void test_pwm_ceiling_reference_milli_volts_non_positive_is_rejected(void) {
  DrivetrainConfig config = makeValidConfig();
  config.pwm_ceiling.reference_milli_volts = 0;
  const ConfigDiagnostic diag = drivetrain_control::validate(config);
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(ConfigError::kNotPositive == diag.code);
  TEST_ASSERT_TRUE(ConfigField::kPwmCeilingReferenceMilliVolts == diag.field);
}

void test_pwm_ceiling_fallback_duty_exceeding_absolute_max_duty_is_rejected(void) {
  DrivetrainConfig config = makeValidConfig();
  config.output.absolute_max_duty = 0.5f;
  config.pwm_ceiling.fallback_duty = 0.9f;  // absolute_max_duty を超える
  const ConfigDiagnostic diag = drivetrain_control::validate(config);
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(ConfigError::kOrderingViolated == diag.code);
  TEST_ASSERT_TRUE(ConfigField::kPwmCeilingFallbackDuty == diag.field);
}

void test_pwm_ceiling_fallback_duty_zero_is_accepted(void) {
  // 0（常に出力遮断）は最も安全側の既定上限として許容される。
  DrivetrainConfig config = makeValidConfig();
  config.pwm_ceiling.fallback_duty = 0.0f;
  const ConfigDiagnostic diag = drivetrain_control::validate(config);
  TEST_ASSERT_TRUE(diag.ok());
}

void test_pwm_ceiling_params_default_enabled_is_true_and_override_fn_is_null(void) {
  // enabled と override_fn(nullptr) は振る舞いの選択（要件 15.3, 12.5）。
  PwmCeilingParams pwm{};
  TEST_ASSERT_TRUE(pwm.enabled);
  TEST_ASSERT_NULL(pwm.override_fn);
}

// ---------------------------------------------------------------------------
// WatchdogParams（保護④。要件 13.3）
// ---------------------------------------------------------------------------

void test_watchdog_timeout_non_positive_is_rejected(void) {
  DrivetrainConfig config = makeValidConfig();
  config.watchdog.timeout_ms = 0;
  const ConfigDiagnostic diag = drivetrain_control::validate(config);
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(ConfigError::kNotPositive == diag.code);
  TEST_ASSERT_TRUE(ConfigField::kWatchdogTimeout == diag.field);
}

// ---------------------------------------------------------------------------
// 容量上限は動的確保回避のための定数であり、性能値ではない（要件 15.3）。
// ---------------------------------------------------------------------------

void test_capacity_constants_bound_the_table_and_window_sizes(void) {
  TEST_ASSERT_TRUE(kMaxVoltagePoints > 0);
  TEST_ASSERT_TRUE(kMaxVoltageWindow > 0);
  VoltageScalerParams scaler{};
  TEST_ASSERT_EQUAL_UINT8(kMaxVoltagePoints, sizeof(scaler.table) / sizeof(scaler.table[0]));
}

int main(int argc, char **argv) {
  (void)argc;
  (void)argv;
  UNITY_BEGIN();

  RUN_TEST(test_zero_initialized_config_is_rejected);
  RUN_TEST(test_valid_baseline_config_is_accepted);

  RUN_TEST(test_geometry_wheel_angle_non_finite_is_rejected);
  RUN_TEST(test_geometry_base_radius_non_positive_is_rejected);
  RUN_TEST(test_degenerate_geometry_is_rejected_with_specific_error);
  RUN_TEST(test_geometry_equilateral_helper_spaces_wheels_120_degrees_apart);

  RUN_TEST(test_encoder_counts_per_wheel_rev_non_positive_is_rejected);
  RUN_TEST(test_encoder_wheel_diameter_non_positive_is_rejected);
  RUN_TEST(test_encoder_raw_modulus_non_positive_is_rejected);
  RUN_TEST(test_encoder_polarity_invalid_value_is_rejected_with_index);

  RUN_TEST(test_output_polarity_invalid_value_is_rejected);
  RUN_TEST(test_output_absolute_max_duty_non_positive_is_rejected);
  RUN_TEST(test_output_absolute_max_duty_exceeding_one_is_rejected);

  RUN_TEST(test_limits_max_body_speed_non_positive_is_rejected);
  RUN_TEST(test_limits_max_body_omega_non_finite_is_rejected);
  RUN_TEST(test_limits_max_wheel_speed_non_positive_is_rejected);

  RUN_TEST(test_pid_gain_negative_is_rejected_with_index);
  RUN_TEST(test_pid_integral_limit_non_finite_is_rejected);
  RUN_TEST(test_pid_integral_limit_non_positive_is_rejected);

  RUN_TEST(test_lock_duty_threshold_exceeding_one_is_rejected);
  RUN_TEST(test_lock_duration_non_positive_is_rejected);
  RUN_TEST(test_lock_clear_duration_non_positive_is_rejected);
  RUN_TEST(test_lock_params_default_latching_is_true_and_is_a_behavior_selector);

  RUN_TEST(test_low_voltage_recover_not_greater_than_stop_is_rejected_with_ordering_error);
  RUN_TEST(test_low_voltage_warn_less_than_stop_is_rejected_with_ordering_error);
  RUN_TEST(test_low_voltage_warn_equal_to_stop_is_accepted);
  RUN_TEST(test_low_voltage_average_window_zero_is_rejected);
  RUN_TEST(test_low_voltage_average_window_exceeding_capacity_is_rejected);
  RUN_TEST(test_low_voltage_average_window_at_capacity_is_accepted);
  RUN_TEST(test_low_voltage_stop_duration_non_positive_is_rejected);
  RUN_TEST(test_low_voltage_unavailable_duration_non_positive_is_rejected);
  RUN_TEST(test_low_voltage_params_default_latching_is_true);

  RUN_TEST(test_voltage_scaler_point_count_below_two_is_rejected_with_table_too_short);
  RUN_TEST(test_voltage_scaler_point_count_exceeding_capacity_is_rejected_with_table_too_long);
  RUN_TEST(test_voltage_scaler_table_not_strictly_increasing_is_rejected_with_index);
  RUN_TEST(test_voltage_scaler_two_point_table_is_accepted);

  RUN_TEST(test_pwm_ceiling_reference_milli_volts_non_positive_is_rejected);
  RUN_TEST(test_pwm_ceiling_fallback_duty_exceeding_absolute_max_duty_is_rejected);
  RUN_TEST(test_pwm_ceiling_fallback_duty_zero_is_accepted);
  RUN_TEST(test_pwm_ceiling_params_default_enabled_is_true_and_override_fn_is_null);

  RUN_TEST(test_watchdog_timeout_non_positive_is_rejected);

  RUN_TEST(test_capacity_constants_bound_the_table_and_window_sizes);

  return UNITY_END();
}
