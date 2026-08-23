#include <unity.h>

#include <cstdint>
#include <type_traits>

#include "drivetrain_control/types.hpp"

// drivetrain-core task 2.2: 指令・出力・状態・遮断理由のコア型
// （types.hpp）を検証するホストテスト。
//
// design.md "Types"（L1）が定めた各型の既定値・フィールド構成・遮断理由の
// ビット定義を対象にする。整数幅そのものの固定は types.hpp 内の
// static_assert（ヘッダを取り込むだけでホスト・組込みの双方の翻訳単位で
// 検査される）が担い、ここでは各型の実際のフィールド挙動（既定構築・
// BlockMask のビット合成/判定・配列添字アクセス）を Unity のランタイム
// テストとして確認する。要件 4.1, 4.2, 4.4, 5.1, 5.3, 14.4。

using drivetrain_control::BlockMask;
using drivetrain_control::BlockReason;
using drivetrain_control::BodyVelocity;
using drivetrain_control::BodyVelocityCommand;
using drivetrain_control::CommandAcceptance;
using drivetrain_control::DurationMs;
using drivetrain_control::EncoderCounts;
using drivetrain_control::Pose2D;
using drivetrain_control::StepResult;
using drivetrain_control::TimeMs;
using drivetrain_control::VoltageSample;
using drivetrain_control::WheelOutputs;
using drivetrain_control::WheelVelocityCommand;
using drivetrain_control::kMaxVoltagePoints;
using drivetrain_control::kMaxVoltageWindow;
using drivetrain_control::kWheelCount;

void setUp(void) {}
void tearDown(void) {}

// ---------------------------------------------------------------------------
// kWheelCount / 容量上限
// ---------------------------------------------------------------------------

void test_wheel_count_is_three(void) {
  TEST_ASSERT_EQUAL_UINT8(3, kWheelCount);
}

void test_capacity_limits_are_positive(void) {
  // 動的確保を避けるための容量上限であり、性能値ではない（要件 15.3）。
  // ここではゼロや負ではないことだけを確認する。
  TEST_ASSERT_TRUE(kMaxVoltagePoints > 0);
  TEST_ASSERT_TRUE(kMaxVoltageWindow > 0);
}

// ---------------------------------------------------------------------------
// TimeMs / DurationMs: 幅固定と経過時間計算での使い方
// ---------------------------------------------------------------------------

void test_time_ms_holds_values_beyond_32bit_range(void) {
  // TimeMs が int64_t であることの実効を、32bit millis() の折り返し点
  // （約 49.7 日 = 4294967296 ms）を跨ぐ値で確認する（要件 4.4）。
  const TimeMs beyond_32bit_wrap = static_cast<TimeMs>(4294967296LL) + 1000;
  TEST_ASSERT_TRUE(beyond_32bit_wrap > 4294967296LL);
}

void test_duration_ms_computed_from_time_ms_difference(void) {
  const TimeMs earlier = 1000;
  const TimeMs later = 1500;
  const DurationMs elapsed = static_cast<DurationMs>(later - earlier);
  TEST_ASSERT_EQUAL_INT32(500, elapsed);
}

// ---------------------------------------------------------------------------
// BodyVelocityCommand / WheelVelocityCommand: 既定構築・指令元非依存
// ---------------------------------------------------------------------------

void test_body_velocity_command_default_constructed_is_zero(void) {
  BodyVelocityCommand cmd;
  TEST_ASSERT_EQUAL_FLOAT(0.0f, cmd.vx_mm_s);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, cmd.vy_mm_s);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, cmd.omega_rad_s);
  TEST_ASSERT_EQUAL_INT64(0, cmd.issued_at_ms);
}

void test_body_velocity_command_holds_assigned_fields(void) {
  BodyVelocityCommand cmd;
  cmd.vx_mm_s = 120.0f;
  cmd.vy_mm_s = -50.0f;
  cmd.omega_rad_s = 1.5f;
  cmd.issued_at_ms = 42;
  TEST_ASSERT_EQUAL_FLOAT(120.0f, cmd.vx_mm_s);
  TEST_ASSERT_EQUAL_FLOAT(-50.0f, cmd.vy_mm_s);
  TEST_ASSERT_EQUAL_FLOAT(1.5f, cmd.omega_rad_s);
  TEST_ASSERT_EQUAL_INT64(42, cmd.issued_at_ms);
}

void test_wheel_velocity_command_default_constructed_is_zero(void) {
  WheelVelocityCommand cmd;
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    TEST_ASSERT_EQUAL_FLOAT(0.0f, cmd.wheel_mm_s[i]);
  }
  TEST_ASSERT_EQUAL_INT64(0, cmd.issued_at_ms);
}

void test_wheel_velocity_command_supports_per_wheel_indexing(void) {
  WheelVelocityCommand cmd;
  cmd.wheel_mm_s[0] = 100.0f;
  cmd.wheel_mm_s[1] = -100.0f;
  cmd.wheel_mm_s[2] = 0.0f;
  TEST_ASSERT_EQUAL_FLOAT(100.0f, cmd.wheel_mm_s[0]);
  TEST_ASSERT_EQUAL_FLOAT(-100.0f, cmd.wheel_mm_s[1]);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, cmd.wheel_mm_s[2]);
}

// ---------------------------------------------------------------------------
// CommandAcceptance / WheelOutputs / VoltageSample / EncoderCounts
// ---------------------------------------------------------------------------

void test_command_acceptance_default_constructed_is_rejected_unclamped(void) {
  CommandAcceptance acc;
  TEST_ASSERT_FALSE(acc.accepted);
  TEST_ASSERT_FALSE(acc.clamped);
}

void test_wheel_outputs_default_constructed_is_all_zero_duty(void) {
  WheelOutputs outputs;
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    TEST_ASSERT_EQUAL_FLOAT(0.0f, outputs.duty[i]);
  }
}

void test_wheel_outputs_supports_per_wheel_indexing(void) {
  WheelOutputs outputs;
  outputs.duty[0] = 1.0f;
  outputs.duty[1] = -1.0f;
  outputs.duty[2] = 0.5f;
  TEST_ASSERT_EQUAL_FLOAT(1.0f, outputs.duty[0]);
  TEST_ASSERT_EQUAL_FLOAT(-1.0f, outputs.duty[1]);
  TEST_ASSERT_EQUAL_FLOAT(0.5f, outputs.duty[2]);
}

void test_voltage_sample_default_constructed_is_invalid_zero(void) {
  VoltageSample sample;
  TEST_ASSERT_FALSE(sample.valid);
  TEST_ASSERT_EQUAL_INT32(0, sample.milli_volts);
}

void test_voltage_sample_holds_assigned_value(void) {
  VoltageSample sample;
  sample.valid = true;
  sample.milli_volts = 7400;
  TEST_ASSERT_TRUE(sample.valid);
  TEST_ASSERT_EQUAL_INT32(7400, sample.milli_volts);
}

void test_encoder_counts_default_constructed_is_all_zero(void) {
  EncoderCounts counts;
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    TEST_ASSERT_EQUAL_INT64(0, counts.count[i]);
  }
}

void test_encoder_counts_supports_per_wheel_indexing_beyond_32bit(void) {
  // 折り返しを含まない累積値であることの実効を、32bit を超える値で確認
  // する（要件 4.4, 4.5, 7.5）。
  EncoderCounts counts;
  counts.count[0] = static_cast<std::int64_t>(5000000000LL);
  counts.count[1] = -static_cast<std::int64_t>(5000000000LL);
  counts.count[2] = 0;
  TEST_ASSERT_EQUAL_INT64(5000000000LL, counts.count[0]);
  TEST_ASSERT_EQUAL_INT64(-5000000000LL, counts.count[1]);
  TEST_ASSERT_EQUAL_INT64(0, counts.count[2]);
}

// ---------------------------------------------------------------------------
// Pose2D / BodyVelocity
// ---------------------------------------------------------------------------

void test_pose2d_default_constructed_is_origin(void) {
  Pose2D pose;
  TEST_ASSERT_EQUAL_FLOAT(0.0f, pose.x_mm);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, pose.y_mm);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, pose.theta_rad);
}

void test_body_velocity_default_constructed_is_zero(void) {
  BodyVelocity vel;
  TEST_ASSERT_EQUAL_FLOAT(0.0f, vel.vx_mm_s);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, vel.vy_mm_s);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, vel.omega_rad_s);
}

// ---------------------------------------------------------------------------
// BlockReason / BlockMask: ビット合成・判定・機体全体と輪ごとの共存
// ---------------------------------------------------------------------------

void test_block_reason_kNone_is_zero(void) {
  TEST_ASSERT_EQUAL_UINT16(0, static_cast<BlockMask>(BlockReason::kNone));
}

void test_block_reason_values_are_distinct_single_bits(void) {
  const BlockReason reasons[] = {
      BlockReason::kNotConfigured,      BlockReason::kOutputDisabled,
      BlockReason::kNoCommandYet,       BlockReason::kCommandTimeout,
      BlockReason::kLowVoltage,         BlockReason::kVoltageUnavailable,
      BlockReason::kMotorLock,
  };
  BlockMask seen = 0;
  for (BlockReason r : reasons) {
    const BlockMask bit = static_cast<BlockMask>(r);
    // 単一ビットであること（2の冪）。
    TEST_ASSERT_TRUE(bit != 0 && (bit & (bit - 1)) == 0);
    // これまで見たビットと重複しないこと。
    TEST_ASSERT_EQUAL_UINT16(0, seen & bit);
    seen |= bit;
  }
}

void test_block_mask_combines_multiple_reasons_with_or(void) {
  // 低電圧（機体全体）とモータロック（輪ごと）は同じビット定義のビット
  // マスクへ同時に合成できる（要件 14.4）。
  const BlockMask combined = static_cast<BlockMask>(BlockReason::kLowVoltage) |
                              static_cast<BlockMask>(BlockReason::kMotorLock);
  TEST_ASSERT_TRUE((combined & static_cast<BlockMask>(BlockReason::kLowVoltage)) != 0);
  TEST_ASSERT_TRUE((combined & static_cast<BlockMask>(BlockReason::kMotorLock)) != 0);
  TEST_ASSERT_TRUE((combined & static_cast<BlockMask>(BlockReason::kCommandTimeout)) == 0);
}

void test_block_mask_check_helper_pattern_detects_membership(void) {
  const BlockMask mask = static_cast<BlockMask>(BlockReason::kNoCommandYet);
  const bool has_no_command_yet =
      (mask & static_cast<BlockMask>(BlockReason::kNoCommandYet)) != 0;
  const bool has_command_timeout =
      (mask & static_cast<BlockMask>(BlockReason::kCommandTimeout)) != 0;
  TEST_ASSERT_TRUE(has_no_command_yet);
  TEST_ASSERT_FALSE(has_command_timeout);
}

// ---------------------------------------------------------------------------
// StepResult: 機体全体の理由と輪ごとの理由を別フィールドで保持する
// ---------------------------------------------------------------------------

void test_step_result_default_constructed_has_no_reasons_and_zero_outputs(void) {
  StepResult result;
  TEST_ASSERT_EQUAL_UINT16(0, result.global_reasons);
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    TEST_ASSERT_EQUAL_UINT16(0, result.wheel_reasons[i]);
    TEST_ASSERT_EQUAL_FLOAT(0.0f, result.outputs.duty[i]);
  }
  TEST_ASSERT_FALSE(result.outputs_written);
}

void test_step_result_separates_global_reason_from_per_wheel_reason(void) {
  // 低電圧（機体全体）とモータロック（輪1のみ）が同時に成立した状態を、
  // 機体全体の理由フィールドと輪ごとの理由フィールドへ別々に表現できる
  // ことを確認する（要件 14.4）。
  StepResult result;
  result.global_reasons = static_cast<BlockMask>(BlockReason::kLowVoltage);
  result.wheel_reasons[1] = static_cast<BlockMask>(BlockReason::kMotorLock);

  TEST_ASSERT_EQUAL_UINT16(static_cast<BlockMask>(BlockReason::kLowVoltage), result.global_reasons);
  TEST_ASSERT_EQUAL_UINT16(0, result.wheel_reasons[0]);
  TEST_ASSERT_EQUAL_UINT16(static_cast<BlockMask>(BlockReason::kMotorLock), result.wheel_reasons[1]);
  TEST_ASSERT_EQUAL_UINT16(0, result.wheel_reasons[2]);
}

int main(int argc, char **argv) {
  (void)argc;
  (void)argv;
  UNITY_BEGIN();

  RUN_TEST(test_wheel_count_is_three);
  RUN_TEST(test_capacity_limits_are_positive);

  RUN_TEST(test_time_ms_holds_values_beyond_32bit_range);
  RUN_TEST(test_duration_ms_computed_from_time_ms_difference);

  RUN_TEST(test_body_velocity_command_default_constructed_is_zero);
  RUN_TEST(test_body_velocity_command_holds_assigned_fields);
  RUN_TEST(test_wheel_velocity_command_default_constructed_is_zero);
  RUN_TEST(test_wheel_velocity_command_supports_per_wheel_indexing);

  RUN_TEST(test_command_acceptance_default_constructed_is_rejected_unclamped);
  RUN_TEST(test_wheel_outputs_default_constructed_is_all_zero_duty);
  RUN_TEST(test_wheel_outputs_supports_per_wheel_indexing);
  RUN_TEST(test_voltage_sample_default_constructed_is_invalid_zero);
  RUN_TEST(test_voltage_sample_holds_assigned_value);
  RUN_TEST(test_encoder_counts_default_constructed_is_all_zero);
  RUN_TEST(test_encoder_counts_supports_per_wheel_indexing_beyond_32bit);

  RUN_TEST(test_pose2d_default_constructed_is_origin);
  RUN_TEST(test_body_velocity_default_constructed_is_zero);

  RUN_TEST(test_block_reason_kNone_is_zero);
  RUN_TEST(test_block_reason_values_are_distinct_single_bits);
  RUN_TEST(test_block_mask_combines_multiple_reasons_with_or);
  RUN_TEST(test_block_mask_check_helper_pattern_detects_membership);

  RUN_TEST(test_step_result_default_constructed_has_no_reasons_and_zero_outputs);
  RUN_TEST(test_step_result_separates_global_reason_from_per_wheel_reason);

  return UNITY_END();
}
