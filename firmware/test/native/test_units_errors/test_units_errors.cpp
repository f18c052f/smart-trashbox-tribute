#include <unity.h>

#include <cstdint>

#include "drivetrain_control/errors.hpp"
#include "drivetrain_control/units.hpp"

// drivetrain-core task 2.1: 単位換算の集約（units.hpp）と設定エラーの型
// （errors.hpp）を検証するホストテスト。
//
// design.md "Units" / "Errors"（L0）に定めた厳密なシグネチャ・意味論を対象
// にする。要件 4.2（mm/ms 境界表現）、4.3（換算係数の集約）、
// 7.7（カウント→距離換算をこの1箇所に集約）、15.4（設定検証失敗を例外なし
// で返す型）、15.6（根拠のない固定値を計算式へ直接埋め込まない）。

using drivetrain_control::ConfigDiagnostic;
using drivetrain_control::ConfigError;
using drivetrain_control::ConfigField;

namespace units = drivetrain_control::units;

void setUp(void) {}
void tearDown(void) {}

// ---------------------------------------------------------------------------
// millisToSeconds: 時刻(ms) -> 秒
// ---------------------------------------------------------------------------

void test_millis_to_seconds_zero(void) {
  TEST_ASSERT_FLOAT_WITHIN(1e-6f, 0.0f, units::millisToSeconds(0));
}

void test_millis_to_seconds_one_second(void) {
  TEST_ASSERT_FLOAT_WITHIN(1e-6f, 1.0f, units::millisToSeconds(1000));
}

void test_millis_to_seconds_fractional(void) {
  TEST_ASSERT_FLOAT_WITHIN(1e-6f, 2.5f, units::millisToSeconds(2500));
}

void test_millis_to_seconds_uses_shared_constant(void) {
  // 換算係数はこの1箇所（kMillisecondsPerSecond）にのみ存在する前提を、
  // 定数を直接使った期待値と突き合わせて確認する（要件 4.3, 15.6）。
  float expected = static_cast<float>(3000) / units::kMillisecondsPerSecond;
  TEST_ASSERT_FLOAT_WITHIN(1e-6f, expected, units::millisToSeconds(3000));
}

// ---------------------------------------------------------------------------
// countsToMillimetres: エンコーダ累積カウント差 -> 距離(mm)
// ---------------------------------------------------------------------------

void test_counts_to_millimetres_zero_delta_is_zero(void) {
  TEST_ASSERT_FLOAT_WITHIN(1e-4f, 0.0f, units::countsToMillimetres(0, 1000, 100.0f));
}

void test_counts_to_millimetres_full_revolution_equals_circumference(void) {
  // 1回転ぶんのカウント差は、ホイールの円周（π * 直径）に一致する。
  const std::int32_t counts_per_rev = 1000;
  const float wheel_diameter_mm = 100.0f;
  const float expected = units::kPi * wheel_diameter_mm;
  TEST_ASSERT_FLOAT_WITHIN(
      1e-3f, expected, units::countsToMillimetres(1000, counts_per_rev, wheel_diameter_mm));
}

void test_counts_to_millimetres_is_proportional_to_delta_counts(void) {
  const std::int32_t counts_per_rev = 1000;
  const float wheel_diameter_mm = 60.0f;
  const float half = units::countsToMillimetres(500, counts_per_rev, wheel_diameter_mm);
  const float full = units::countsToMillimetres(1000, counts_per_rev, wheel_diameter_mm);
  TEST_ASSERT_FLOAT_WITHIN(1e-3f, full / 2.0f, half);
}

void test_counts_to_millimetres_preserves_sign_for_reverse_rotation(void) {
  const std::int32_t counts_per_rev = 1000;
  const float wheel_diameter_mm = 60.0f;
  const float forward = units::countsToMillimetres(400, counts_per_rev, wheel_diameter_mm);
  const float backward = units::countsToMillimetres(-400, counts_per_rev, wheel_diameter_mm);
  TEST_ASSERT_TRUE(forward > 0.0f);
  TEST_ASSERT_TRUE(backward < 0.0f);
  TEST_ASSERT_FLOAT_WITHIN(1e-3f, -forward, backward);
}

void test_counts_to_millimetres_scales_with_wheel_diameter(void) {
  const std::int32_t counts_per_rev = 1000;
  const float small = units::countsToMillimetres(1000, counts_per_rev, 50.0f);
  const float large = units::countsToMillimetres(1000, counts_per_rev, 100.0f);
  TEST_ASSERT_FLOAT_WITHIN(1e-3f, large / 2.0f, small);
}

// ---------------------------------------------------------------------------
// wrapAngle: 角度を (-π, +π] へ正規化
// ---------------------------------------------------------------------------

void test_wrap_angle_zero_stays_zero(void) {
  TEST_ASSERT_FLOAT_WITHIN(1e-5f, 0.0f, units::wrapAngle(0.0f));
}

void test_wrap_angle_pi_stays_pi(void) {
  // 上限 +π は含む。
  TEST_ASSERT_FLOAT_WITHIN(1e-5f, units::kPi, units::wrapAngle(units::kPi));
}

void test_wrap_angle_negative_pi_wraps_to_positive_pi(void) {
  // 下限 -π は含まず、+π 側へ折り返す。
  TEST_ASSERT_FLOAT_WITHIN(1e-4f, units::kPi, units::wrapAngle(-units::kPi));
}

void test_wrap_angle_just_past_pi_wraps_to_near_negative_pi(void) {
  const float wrapped = units::wrapAngle(units::kPi + 0.1f);
  TEST_ASSERT_FLOAT_WITHIN(1e-4f, -units::kPi + 0.1f, wrapped);
}

void test_wrap_angle_result_is_always_within_expected_range(void) {
  // (-π, +π] の範囲外へ出ないことを、複数の周回で確認する。
  const float samples[] = {0.3f, -0.3f, 1.5f, -1.5f, 3.0f, -3.0f};
  for (float s : samples) {
    for (int turns = -3; turns <= 3; ++turns) {
      float wrapped = units::wrapAngle(s + static_cast<float>(turns) * units::kTwoPi);
      TEST_ASSERT_TRUE(wrapped > -units::kPi);
      TEST_ASSERT_TRUE(wrapped <= units::kPi + 1e-4f);
    }
  }
}

void test_wrap_angle_round_trip_over_multiple_revolutions(void) {
  // 往復検証: θ を基準に、θ + n*2π を正規化するとすべて同じ値へ戻ること。
  const float base_angles[] = {0.0f, 0.7f, -0.7f, 2.0f, -2.0f, 3.14f, -3.14f};
  for (float base : base_angles) {
    const float reference = units::wrapAngle(base);
    for (int turns = -2; turns <= 2; ++turns) {
      if (turns == 0) continue;
      const float shifted = base + static_cast<float>(turns) * units::kTwoPi;
      TEST_ASSERT_FLOAT_WITHIN(1e-3f, reference, units::wrapAngle(shifted));
    }
  }
}

// ---------------------------------------------------------------------------
// ConfigDiagnostic / ConfigError / ConfigField: 例外を使わない設定検証失敗
// ---------------------------------------------------------------------------

void test_config_diagnostic_default_constructed_is_ok(void) {
  ConfigDiagnostic diag;
  TEST_ASSERT_TRUE(diag.ok());
  TEST_ASSERT_TRUE(ConfigError::kNone == diag.code);
  TEST_ASSERT_TRUE(ConfigField::kNone == diag.field);
  TEST_ASSERT_EQUAL_UINT8(0, diag.index);
}

void test_config_diagnostic_with_error_is_not_ok(void) {
  ConfigDiagnostic diag;
  diag.code = ConfigError::kOrderingViolated;
  TEST_ASSERT_FALSE(diag.ok());
}

void test_config_diagnostic_carries_field_and_index(void) {
  // 「原因の種別」「違反したパラメータ」「輪番号やテーブル位置の添字」の
  // 3点をすべて保持できることを確認する（要件 15.4）。
  ConfigDiagnostic diag;
  diag.code = ConfigError::kTableNotMonotonic;
  diag.field = ConfigField::kNone;  // 実パラメータの列挙子は後続タスクで追加
  diag.index = 5;
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(ConfigError::kTableNotMonotonic == diag.code);
  TEST_ASSERT_EQUAL_UINT8(5, diag.index);
}

void test_config_diagnostic_is_constexpr_constructible(void) {
  // 例外を投げない核であることの一部を、constexpr 構築・constexpr ok() の
  // 成立で裏取りする。
  constexpr ConfigDiagnostic diag{};
  static_assert(diag.ok(), "default ConfigDiagnostic must be ok()");
  TEST_ASSERT_TRUE(diag.ok());
}

int main(int argc, char **argv) {
  (void)argc;
  (void)argv;
  UNITY_BEGIN();

  RUN_TEST(test_millis_to_seconds_zero);
  RUN_TEST(test_millis_to_seconds_one_second);
  RUN_TEST(test_millis_to_seconds_fractional);
  RUN_TEST(test_millis_to_seconds_uses_shared_constant);

  RUN_TEST(test_counts_to_millimetres_zero_delta_is_zero);
  RUN_TEST(test_counts_to_millimetres_full_revolution_equals_circumference);
  RUN_TEST(test_counts_to_millimetres_is_proportional_to_delta_counts);
  RUN_TEST(test_counts_to_millimetres_preserves_sign_for_reverse_rotation);
  RUN_TEST(test_counts_to_millimetres_scales_with_wheel_diameter);

  RUN_TEST(test_wrap_angle_zero_stays_zero);
  RUN_TEST(test_wrap_angle_pi_stays_pi);
  RUN_TEST(test_wrap_angle_negative_pi_wraps_to_positive_pi);
  RUN_TEST(test_wrap_angle_just_past_pi_wraps_to_near_negative_pi);
  RUN_TEST(test_wrap_angle_result_is_always_within_expected_range);
  RUN_TEST(test_wrap_angle_round_trip_over_multiple_revolutions);

  RUN_TEST(test_config_diagnostic_default_constructed_is_ok);
  RUN_TEST(test_config_diagnostic_with_error_is_not_ok);
  RUN_TEST(test_config_diagnostic_carries_field_and_index);
  RUN_TEST(test_config_diagnostic_is_constexpr_constructible);

  return UNITY_END();
}
