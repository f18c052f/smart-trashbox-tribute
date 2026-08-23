#include <unity.h>

#include "drivetrain_control/config.hpp"
#include "drivetrain_control/protection/pwm_ceiling.hpp"
#include "drivetrain_control/types.hpp"

// drivetrain-core task 5.3: 保護③ バッテリ電圧を考慮した出力上限
// （PwmCeiling）を検証するホストテスト。
//
// design.md "PwmCeiling"（L7-L8 保護層）の厳密なシグネチャ・純関数契約を
// 対象にする。要件 12.1, 12.2, 12.3, 12.5, 12.6。
//
// 以下を検証する:
//   A. 電圧に応じて上限が下がること（満充電相当ほど上限が低い。要件 12.1）
//   B. 上限が absolute_max_duty を超えないこと（要件 12.3。低電圧側では
//      比が 1.0 を超えるがクランプされる）
//   C. 無効化（enabled = false）すると、電圧やサンプルの有効性に関わらず
//      常に absolute_max_duty を返すこと（要件 12.5）
//   D. 差し替えた式（override_fn）の戻り値が定義域外（負値・
//      absolute_max_duty 超過）でも [0, absolute_max_duty] へ収まること
//      （要件 12.5, 12.3）
//   E. override_fn には measured_milli_volts と params 自身が渡され、
//      params の他フィールド（reference_milli_volts）を参照できること
//   F. 欠測（valid == false）時に fallback_duty（安全側の既定上限）が
//      適用されること（要件 12.6）。無制限（absolute_max_duty そのまま）
//      にはならないことを明示的に確認する
//   G. 状態を持たない: 同じサンプルを何度渡しても同じ結果を返す
//      （直前の呼び出し履歴に依存しない）
//   H. 設計判断（pwm_ceiling.hpp 冒頭コメント参照）: valid == true でも
//      measured_milli_volts <= 0（物理的にあり得ない読み値）のときは
//      欠測と同様に fallback_duty を返す

using drivetrain_control::PwmCeiling;
using drivetrain_control::PwmCeilingParams;
using drivetrain_control::VoltageSample;

namespace {

PwmCeilingParams makeParams(bool enabled, std::int32_t reference_mv, float fallback_duty,
                             float (*override_fn)(std::int32_t, const PwmCeilingParams&) = nullptr) {
  PwmCeilingParams p{};
  p.enabled = enabled;
  p.reference_milli_volts = reference_mv;
  p.fallback_duty = fallback_duty;
  p.override_fn = override_fn;
  return p;
}

VoltageSample validSample(std::int32_t mv) {
  VoltageSample s;
  s.valid = true;
  s.milli_volts = mv;
  return s;
}

VoltageSample invalidSample() {
  VoltageSample s;
  s.valid = false;
  s.milli_volts = 0;
  return s;
}

}  // namespace

void setUp(void) {}
void tearDown(void) {}

// ---------------------------------------------------------------------------
// A: 電圧に応じて上限が下がる（要件 12.1）。満充電相当（12600mV）の上限は
// 定格相当（11100mV）の上限より低い。既定式:
// min(absolute_max_duty, reference_milli_volts / measured_milli_volts)。
// ---------------------------------------------------------------------------

void test_ceiling_decreases_as_measured_voltage_rises(void) {
  const float kAbsoluteMaxDuty = 1.0f;
  PwmCeiling ceiling(makeParams(/*enabled=*/true, /*reference_mv=*/12000, /*fallback_duty=*/0.5f),
                      kAbsoluteMaxDuty);

  // reference(12000) / 11100 ≈ 1.081 -> クランプされ absolute_max_duty(1.0)。
  const float at_nominal = ceiling.evaluate(validSample(11100));
  // reference(12000) / 12600 ≈ 0.952 -> クランプなしでそのまま。
  const float at_full_charge = ceiling.evaluate(validSample(12600));

  TEST_ASSERT_FLOAT_WITHIN(1e-6f, 1.0f, at_nominal);
  TEST_ASSERT_FLOAT_WITHIN(1e-4f, 12000.0f / 12600.0f, at_full_charge);
  TEST_ASSERT_TRUE(at_full_charge < at_nominal);
}

// ---------------------------------------------------------------------------
// B: 上限は absolute_max_duty を決して超えない（要件 12.3）。測定電圧が
// 基準電圧を大きく下回り、素の比が absolute_max_duty を超える場合でも
// クランプされる。
// ---------------------------------------------------------------------------

void test_ceiling_never_exceeds_absolute_max_duty(void) {
  const float kAbsoluteMaxDuty = 0.9f;
  PwmCeiling ceiling(makeParams(/*enabled=*/true, /*reference_mv=*/12000, /*fallback_duty=*/0.3f),
                      kAbsoluteMaxDuty);

  // reference(12000) / 6000 = 2.0 -> 素の比は absolute_max_duty(0.9) を
  // 大きく超えるが、戻り値はクランプされる。
  const float result = ceiling.evaluate(validSample(6000));

  TEST_ASSERT_FLOAT_WITHIN(1e-6f, kAbsoluteMaxDuty, result);
  TEST_ASSERT_TRUE(result <= kAbsoluteMaxDuty);
}

// ---------------------------------------------------------------------------
// C: 無効化（enabled = false）すると、電圧やサンプルの有効性に関わらず
// 常に absolute_max_duty を返す（要件 12.5 の無効化）。
// ---------------------------------------------------------------------------

void test_disabled_always_returns_absolute_max_duty(void) {
  const float kAbsoluteMaxDuty = 0.8f;
  PwmCeiling ceiling(makeParams(/*enabled=*/false, /*reference_mv=*/12000, /*fallback_duty=*/0.2f),
                      kAbsoluteMaxDuty);

  // 満充電相当の高電圧でも下がらない。
  TEST_ASSERT_FLOAT_WITHIN(1e-6f, kAbsoluteMaxDuty, ceiling.evaluate(validSample(12600)));
  // 低電圧でも absolute_max_duty を超えて上がりはしない（常に一定）。
  TEST_ASSERT_FLOAT_WITHIN(1e-6f, kAbsoluteMaxDuty, ceiling.evaluate(validSample(9000)));
  // 欠測時でも fallback_duty ではなく absolute_max_duty のまま
  // （無効化が欠測より優先される。pwm_ceiling.hpp 優先順位の説明を参照）。
  TEST_ASSERT_FLOAT_WITHIN(1e-6f, kAbsoluteMaxDuty, ceiling.evaluate(invalidSample()));
}

// ---------------------------------------------------------------------------
// D: 差し替えた式（override_fn）の戻り値が定義域外でも
// [0, absolute_max_duty] へクランプされる（要件 12.5, 12.3）。
// ---------------------------------------------------------------------------

float overrideReturningNegative(std::int32_t measured_mv, const PwmCeilingParams& /*params*/) {
  (void)measured_mv;
  return -0.5f;  // 定義域外（負値）。
}

float overrideReturningOverMax(std::int32_t measured_mv, const PwmCeilingParams& /*params*/) {
  (void)measured_mv;
  return 5.0f;  // 定義域外（absolute_max_duty を大きく超える）。
}

void test_override_fn_result_is_clamped_into_domain(void) {
  const float kAbsoluteMaxDuty = 0.7f;

  PwmCeiling negative_ceiling(
      makeParams(/*enabled=*/true, /*reference_mv=*/12000, /*fallback_duty=*/0.1f,
                 overrideReturningNegative),
      kAbsoluteMaxDuty);
  const float negative_result = negative_ceiling.evaluate(validSample(11000));
  TEST_ASSERT_FLOAT_WITHIN(1e-6f, 0.0f, negative_result);
  TEST_ASSERT_TRUE(negative_result >= 0.0f);

  PwmCeiling over_max_ceiling(
      makeParams(/*enabled=*/true, /*reference_mv=*/12000, /*fallback_duty=*/0.1f,
                 overrideReturningOverMax),
      kAbsoluteMaxDuty);
  const float over_max_result = over_max_ceiling.evaluate(validSample(11000));
  TEST_ASSERT_FLOAT_WITHIN(1e-6f, kAbsoluteMaxDuty, over_max_result);
  TEST_ASSERT_TRUE(over_max_result <= kAbsoluteMaxDuty);
}

// ---------------------------------------------------------------------------
// E: override_fn には measured_milli_volts と params 自身が渡され、
// params の他フィールド（reference_milli_volts）を参照できる
// （PwmCeilingParams::override_fn のシグネチャどおり）。
// ---------------------------------------------------------------------------

float overrideUsingReferenceField(std::int32_t measured_mv, const PwmCeilingParams& params) {
  // reference_milli_volts の2倍を基準にする独自式。params 経由でしか
  // reference_milli_volts を得られないことを確認する。
  return static_cast<float>(2 * params.reference_milli_volts) / static_cast<float>(measured_mv);
}

void test_override_fn_receives_measured_value_and_full_params(void) {
  const float kAbsoluteMaxDuty = 1.0f;
  PwmCeiling ceiling(
      makeParams(/*enabled=*/true, /*reference_mv=*/6000, /*fallback_duty=*/0.1f,
                 overrideUsingReferenceField),
      kAbsoluteMaxDuty);

  // 2*6000 / 12000 = 1.0 -> クランプなしでそのまま反映される。
  const float result = ceiling.evaluate(validSample(12000));
  TEST_ASSERT_FLOAT_WITHIN(1e-6f, 1.0f, result);
}

// ---------------------------------------------------------------------------
// F: 欠測（valid == false）時は fallback_duty（安全側の既定上限）が
// 適用される。absolute_max_duty（無制限相当）にはならない（要件 12.6）。
// ---------------------------------------------------------------------------

void test_missing_reading_applies_fallback_duty_not_unlimited(void) {
  const float kAbsoluteMaxDuty = 1.0f;
  const float kFallbackDuty = 0.4f;
  PwmCeiling ceiling(
      makeParams(/*enabled=*/true, /*reference_mv=*/12000, /*fallback_duty=*/kFallbackDuty),
      kAbsoluteMaxDuty);

  const float result = ceiling.evaluate(invalidSample());

  TEST_ASSERT_FLOAT_WITHIN(1e-6f, kFallbackDuty, result);
  // 安全側の既定上限は absolute_max_duty（無制限相当）そのものではない
  // ことを明示する（欠測を無制限扱いしていない裏取り）。
  TEST_ASSERT_TRUE(result < kAbsoluteMaxDuty);
}

// ---------------------------------------------------------------------------
// G: 状態を持たない純関数（design.md "PwmCeiling" Invariants）。同一
// サンプルを繰り返し渡しても常に同じ結果を返す。
// ---------------------------------------------------------------------------

void test_evaluate_is_stateless_and_repeatable(void) {
  const float kAbsoluteMaxDuty = 1.0f;
  PwmCeiling ceiling(makeParams(/*enabled=*/true, /*reference_mv=*/12000, /*fallback_duty=*/0.5f),
                      kAbsoluteMaxDuty);

  const float first = ceiling.evaluate(validSample(12600));
  // 間に別の呼び出し（欠測・別電圧）を挟んでも、同じサンプルへ戻せば
  // 同じ結果になる。
  ceiling.evaluate(invalidSample());
  ceiling.evaluate(validSample(9000));
  const float second = ceiling.evaluate(validSample(12600));

  TEST_ASSERT_FLOAT_WITHIN(1e-6f, first, second);
}

// ---------------------------------------------------------------------------
// H: 設計判断（pwm_ceiling.hpp 冒頭のゼロ割りガード説明を参照）:
// valid == true でも measured_milli_volts <= 0（物理的にあり得ない読み値）
// のときは、欠測と同様に fallback_duty を返す（0除算や負のデューティを
// 避けるため）。
// ---------------------------------------------------------------------------

void test_non_positive_measured_value_is_treated_as_missing(void) {
  const float kAbsoluteMaxDuty = 1.0f;
  const float kFallbackDuty = 0.35f;
  PwmCeiling ceiling(
      makeParams(/*enabled=*/true, /*reference_mv=*/12000, /*fallback_duty=*/kFallbackDuty),
      kAbsoluteMaxDuty);

  VoltageSample zero_sample;
  zero_sample.valid = true;
  zero_sample.milli_volts = 0;
  TEST_ASSERT_FLOAT_WITHIN(1e-6f, kFallbackDuty, ceiling.evaluate(zero_sample));

  VoltageSample negative_sample;
  negative_sample.valid = true;
  negative_sample.milli_volts = -100;
  TEST_ASSERT_FLOAT_WITHIN(1e-6f, kFallbackDuty, ceiling.evaluate(negative_sample));
}

int main(int argc, char **argv) {
  (void)argc;
  (void)argv;
  UNITY_BEGIN();

  RUN_TEST(test_ceiling_decreases_as_measured_voltage_rises);
  RUN_TEST(test_ceiling_never_exceeds_absolute_max_duty);
  RUN_TEST(test_disabled_always_returns_absolute_max_duty);
  RUN_TEST(test_override_fn_result_is_clamped_into_domain);
  RUN_TEST(test_override_fn_receives_measured_value_and_full_params);
  RUN_TEST(test_missing_reading_applies_fallback_duty_not_unlimited);
  RUN_TEST(test_evaluate_is_stateless_and_repeatable);
  RUN_TEST(test_non_positive_measured_value_is_treated_as_missing);

  return UNITY_END();
}
