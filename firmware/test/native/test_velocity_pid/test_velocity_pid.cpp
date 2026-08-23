#include <unity.h>

#include <cmath>
#include <cstdint>

#include "drivetrain_control/config.hpp"
#include "drivetrain_control/types.hpp"
#include "drivetrain_control/velocity_pid.hpp"

// drivetrain-core task 4.2: 各輪の速度PID（VelocityPid）を2段プロトコル
// （compute → commit / holdReset）で検証するホストテスト。
//
// design.md "VelocityPid"（L6）の厳密なシグネチャ・状態機械を対象にする。
// 要件 9.1〜9.6（偏差に基づくP・I、計測値の変化に基づくD、時刻の外部注入、
// 積分の絶対値クランプと条件付き積分によるワインドアップ対策、遮断中の
// holdReset()）。
//
// 以下を検証する:
//   A. P・I が偏差 (target - measured) に、D が「計測値の変化」に掛かること
//      （目標値のみの変更では D がキックしないこと）
//   B. 飽和が続く入力（commit(raw) をそのまま渡し続ける）で積分が
//      integral_limit の絶対値を超えないこと（正・負の両方向）
//   C. compute() の値より小さい値で commit() し続け、かつ偏差が飽和方向を
//      向いているときは、積分が育たないこと（条件付き積分。正・負の両方向）
//   D. holdReset() を挟んだ直後の最初の compute() が、遮断前の残留積分にも
//      遮断中に古いままの計測値からくる微分キックにも起因する過大な出力を
//      返さないこと

using drivetrain_control::DurationMs;
using drivetrain_control::PidParams;
using drivetrain_control::VelocityPid;

namespace {

constexpr float kTight = 1.0e-3f;

PidParams makeParams(float kp, float ki, float kd, float integral_limit) {
  PidParams p{};
  p.kp = kp;
  p.ki = ki;
  p.kd = kd;
  p.integral_limit = integral_limit;
  return p;
}

}  // namespace

void setUp(void) {}
void tearDown(void) {}

// ---------------------------------------------------------------------------
// A-1: 目標値だけを変化させても微分はキックしない（要件 9.1 の「微分は計測値
// の変化に掛ける」の直接検証）
// ---------------------------------------------------------------------------

void test_derivative_does_not_kick_on_target_step_change(void) {
  VelocityPid pid(makeParams(/*kp=*/0.0f, /*ki=*/0.0f, /*kd=*/2.0f, /*integral_limit=*/1000.0f));

  // 微分の基準となる前回計測値を明示的に揃える（holdReset の副作用を使わず、
  // まず measured=50 で1回 compute/commit して基準を確定させる）。
  pid.holdReset(/*measured_mm_s=*/50.0f);
  const float out_baseline = pid.compute(/*target=*/50.0f, /*measured=*/50.0f, /*dt_ms=*/10);
  TEST_ASSERT_FLOAT_WITHIN(kTight, 0.0f, out_baseline);
  pid.commit(out_baseline);

  // target を大きく跳ね上げても measured は不変。kp = ki = 0 なので、
  // D がキックしなければ出力は 0 のままのはず。
  const float out_after_target_jump = pid.compute(/*target=*/9999.0f, /*measured=*/50.0f, /*dt_ms=*/10);
  TEST_ASSERT_FLOAT_WITHIN(kTight, 0.0f, out_after_target_jump);
  pid.commit(out_after_target_jump);
}

// ---------------------------------------------------------------------------
// A-2: 計測値が変化すれば微分は反応する（A-1 が「無反応」の意図的な回帰に
// なっていないことの裏取り）
// ---------------------------------------------------------------------------

void test_derivative_responds_to_measurement_change(void) {
  VelocityPid pid(makeParams(/*kp=*/0.0f, /*ki=*/0.0f, /*kd=*/2.0f, /*integral_limit=*/1000.0f));

  pid.holdReset(/*measured_mm_s=*/0.0f);
  const float out_baseline = pid.compute(/*target=*/0.0f, /*measured=*/0.0f, /*dt_ms=*/10);
  TEST_ASSERT_FLOAT_WITHIN(kTight, 0.0f, out_baseline);
  pid.commit(out_baseline);

  // measured が 0 -> 5 (dt=10ms=0.01s) へ変化。kd=2.0 の微分項は
  // -kd * (measured - last_measured) / dt_s = -2.0 * 5.0 / 0.01 = -1000.0
  // （計測値の上昇を打ち消す向き。design.md の「D は計測値の変化に掛ける」
  // という指定自体は符号を定めないため、符号の選び方は本実装の設計判断
  // として固定し、ここでその値を検証する）。
  const float out_after_measurement_change = pid.compute(/*target=*/0.0f, /*measured=*/5.0f, /*dt_ms=*/10);
  TEST_ASSERT_FLOAT_WITHIN(1.0f, -1000.0f, out_after_measurement_change);
  pid.commit(out_after_measurement_change);
}

// ---------------------------------------------------------------------------
// A-3: P・I は偏差に厳密に比例して積み上がる（compute() の戻り値と
// integral() を式どおりに直接検証する）
// ---------------------------------------------------------------------------

void test_proportional_and_integral_terms_follow_error(void) {
  VelocityPid pid(makeParams(/*kp=*/2.0f, /*ki=*/0.5f, /*kd=*/0.0f, /*integral_limit=*/1000.0f));
  pid.holdReset(/*measured_mm_s=*/0.0f);

  // Step 1: target=10, measured=0, dt=100ms=0.1s
  //   error = 10, P = kp*error = 20
  //   積分増分 = ki*error*dt_s = 0.5*10*0.1 = 0.5 -> integral = 0.5 (上限内)
  //   raw = 20 + 0.5 = 20.5
  const float raw1 = pid.compute(/*target=*/10.0f, /*measured=*/0.0f, /*dt_ms=*/100);
  TEST_ASSERT_FLOAT_WITHIN(kTight, 20.5f, raw1);
  pid.commit(raw1);  // 生の出力をそのまま適用 -> 条件付き積分は発動しない
  TEST_ASSERT_FLOAT_WITHIN(kTight, 0.5f, pid.integral());

  // Step 2: target=10, measured=2 (プラントが応答したと仮定), dt=100ms
  //   error = 8, P = 16
  //   積分増分 = 0.5*8*0.1 = 0.4 -> integral = 0.5+0.4 = 0.9
  //   raw = 16 + 0.9 = 16.9
  const float raw2 = pid.compute(/*target=*/10.0f, /*measured=*/2.0f, /*dt_ms=*/100);
  TEST_ASSERT_FLOAT_WITHIN(kTight, 16.9f, raw2);
  pid.commit(raw2);
  TEST_ASSERT_FLOAT_WITHIN(kTight, 0.9f, pid.integral());
}

// ---------------------------------------------------------------------------
// B: 飽和が続く入力で積分が integral_limit の絶対値を超えない
// （commit() には compute() の戻り値をそのまま渡す = 縮小されていない）
// ---------------------------------------------------------------------------

void test_integral_stays_within_limit_under_persistent_positive_saturation(void) {
  constexpr float kIntegralLimit = 5.0f;
  VelocityPid pid(makeParams(/*kp=*/1.0f, /*ki=*/2.0f, /*kd=*/0.0f, kIntegralLimit));
  pid.holdReset(/*measured_mm_s=*/0.0f);

  // target が measured (常に 0) より遥かに高く、決して追いつかない
  // （偏差が縮まない = 飽和が続く）。
  for (int i = 0; i < 500; ++i) {
    const float raw = pid.compute(/*target=*/1000.0f, /*measured=*/0.0f, /*dt_ms=*/10);
    pid.commit(raw);
    TEST_ASSERT_TRUE(pid.integral() <= kIntegralLimit + kTight);
    TEST_ASSERT_TRUE(pid.integral() >= -kIntegralLimit - kTight);
  }
  // 大きな偏差が数百ステップ続けば、実際に上限へ張り付いているはず
  // （クランプが「効いている」ことの裏取り。単に超えていないだけではない）。
  TEST_ASSERT_FLOAT_WITHIN(kTight, kIntegralLimit, pid.integral());
}

void test_integral_stays_within_limit_under_persistent_negative_saturation(void) {
  constexpr float kIntegralLimit = 5.0f;
  VelocityPid pid(makeParams(/*kp=*/1.0f, /*ki=*/2.0f, /*kd=*/0.0f, kIntegralLimit));
  pid.holdReset(/*measured_mm_s=*/0.0f);

  // 対称に、target が measured より遥かに低い場合。
  for (int i = 0; i < 500; ++i) {
    const float raw = pid.compute(/*target=*/-1000.0f, /*measured=*/0.0f, /*dt_ms=*/10);
    pid.commit(raw);
    TEST_ASSERT_TRUE(pid.integral() <= kIntegralLimit + kTight);
    TEST_ASSERT_TRUE(pid.integral() >= -kIntegralLimit - kTight);
  }
  TEST_ASSERT_FLOAT_WITHIN(kTight, -kIntegralLimit, pid.integral());
}

// ---------------------------------------------------------------------------
// C: compute() の値より小さい値で commit() し続け、かつ偏差が飽和方向を
// 向いているときは積分が育たない（条件付き積分。3輪等比縮小が生の出力を
// 縮めた状況を模す）
// ---------------------------------------------------------------------------

void test_conditional_integration_prevents_growth_when_scaled_down_positive(void) {
  // integral_limit を十分大きく取り、単純なクランプではなく条件付き積分
  // そのものが効いていることを切り分ける。
  VelocityPid pid(makeParams(/*kp=*/1.0f, /*ki=*/2.0f, /*kd=*/0.0f, /*integral_limit=*/1.0e6f));
  pid.holdReset(/*measured_mm_s=*/0.0f);

  float previous_integral = pid.integral();
  TEST_ASSERT_FLOAT_WITHIN(kTight, 0.0f, previous_integral);

  for (int i = 0; i < 50; ++i) {
    const float raw = pid.compute(/*target=*/1000.0f, /*measured=*/0.0f, /*dt_ms=*/10);
    TEST_ASSERT_TRUE(raw > 0.0f);  // 偏差は正 -> 生の出力も正（飽和方向 = 正）

    // 実際に適用された値は、群としての等比縮小で生の出力より小さくなった
    // と仮定する（半分に縮小）。
    const float applied = raw * 0.5f;
    pid.commit(applied);

    // 積分は育たない: 前回の値からほぼ変化しない（常に取り消されるため）。
    TEST_ASSERT_FLOAT_WITHIN(1.0e-2f, previous_integral, pid.integral());
    previous_integral = pid.integral();
  }
}

void test_conditional_integration_prevents_growth_when_scaled_down_negative(void) {
  VelocityPid pid(makeParams(/*kp=*/1.0f, /*ki=*/2.0f, /*kd=*/0.0f, /*integral_limit=*/1.0e6f));
  pid.holdReset(/*measured_mm_s=*/0.0f);

  float previous_integral = pid.integral();
  TEST_ASSERT_FLOAT_WITHIN(kTight, 0.0f, previous_integral);

  for (int i = 0; i < 50; ++i) {
    const float raw = pid.compute(/*target=*/-1000.0f, /*measured=*/0.0f, /*dt_ms=*/10);
    TEST_ASSERT_TRUE(raw < 0.0f);  // 偏差は負 -> 生の出力も負（飽和方向 = 負）

    const float applied = raw * 0.5f;  // 大きさは半分（符号は同じ）
    pid.commit(applied);

    TEST_ASSERT_FLOAT_WITHIN(1.0e-2f, previous_integral, pid.integral());
    previous_integral = pid.integral();
  }
}

// 対照実験: 適用値の「大きさ」が生の出力以上（縮小されていない）なら、
// 条件付き積分は発動せず、積分は通常どおり育つ（C の否定形を固定し、
// 「常に取り消す」誤実装との回帰を防ぐ）。
void test_conditional_integration_does_not_suppress_growth_when_not_scaled_down(void) {
  VelocityPid pid(makeParams(/*kp=*/1.0f, /*ki=*/2.0f, /*kd=*/0.0f, /*integral_limit=*/1.0e6f));
  pid.holdReset(/*measured_mm_s=*/0.0f);

  for (int i = 0; i < 10; ++i) {
    const float raw = pid.compute(/*target=*/1000.0f, /*measured=*/0.0f, /*dt_ms=*/10);
    pid.commit(raw);  // 縮小なし（等しい）
  }
  // ki=2, error=1000, dt_s=0.01 -> 1ステップあたり積分増分 = 20。10ステップ
  // で 200 育っているはず（integral_limit が 1e6 なのでクランプは効かない）。
  TEST_ASSERT_FLOAT_WITHIN(1.0f, 200.0f, pid.integral());
}

// ---------------------------------------------------------------------------
// D: holdReset() を挟んだ直後の最初の compute() が過大な出力を返さない
// （残留積分にも、遮断中に古いままの計測値からくる微分キックにも起因しない）
// ---------------------------------------------------------------------------

void test_first_compute_after_hold_reset_has_no_spike(void) {
  VelocityPid pid(makeParams(/*kp=*/1.0f, /*ki=*/1.0f, /*kd=*/5.0f, /*integral_limit=*/50.0f));
  pid.holdReset(/*measured_mm_s=*/0.0f);

  // Phase A: 出力遮断前に、大きな偏差を与え続けて積分を上限付近まで育てる。
  for (int i = 0; i < 100; ++i) {
    const float raw = pid.compute(/*target=*/1000.0f, /*measured=*/0.0f, /*dt_ms=*/10);
    pid.commit(raw);
  }
  TEST_ASSERT_FLOAT_WITHIN(0.5f, 50.0f, pid.integral());  // 上限へ張り付いていることを確認

  // Phase B: 出力遮断中（ホイールが惰性で減速していく想定で、計測値が
  // ステップごとに変化する）。holdReset() を複数回呼ぶ。
  const float measured_during_hold[] = {80.0f, 60.0f, 40.0f, 20.0f};
  for (float m : measured_during_hold) {
    pid.holdReset(m);
  }
  // 積分は 0 にリセットされている。
  TEST_ASSERT_EQUAL_FLOAT(0.0f, pid.integral());

  // Phase C: 遮断解除直後の最初の compute()。measured は最後の holdReset()
  // と同じ値 (20.0) を使う -> 微分の基準が正しく追従していれば D はキック
  // しない。target は控えめな値にし、期待値を手計算で厳密に検証する。
  //   error = 25 - 20 = 5, dt_s = 0.01
  //   P = kp*error = 5
  //   積分増分 = ki*error*dt_s = 1*5*0.01 = 0.05 -> integral = 0.05 (上限内)
  //   D = -kd*(20-20)/dt_s = 0 (last_measured が holdReset で 20 に追従済みのため)
  //   raw = 5 + 0.05 + 0 = 5.05
  const float raw_after_hold = pid.compute(/*target=*/25.0f, /*measured=*/20.0f, /*dt_ms=*/10);
  TEST_ASSERT_FLOAT_WITHIN(kTight, 5.05f, raw_after_hold);
  pid.commit(raw_after_hold);

  // 参考: もし残留積分(50)が消えていなければ raw は 55 前後に、もし
  // last_measured が遮断前のまま(0)であれば D 項だけで -kd*(20-0)/0.01 =
  // -10000 という桁違いの値になる。どちらも上記の厳密一致で排除される。
}

int main(int argc, char **argv) {
  (void)argc;
  (void)argv;
  UNITY_BEGIN();

  RUN_TEST(test_derivative_does_not_kick_on_target_step_change);
  RUN_TEST(test_derivative_responds_to_measurement_change);
  RUN_TEST(test_proportional_and_integral_terms_follow_error);
  RUN_TEST(test_integral_stays_within_limit_under_persistent_positive_saturation);
  RUN_TEST(test_integral_stays_within_limit_under_persistent_negative_saturation);
  RUN_TEST(test_conditional_integration_prevents_growth_when_scaled_down_positive);
  RUN_TEST(test_conditional_integration_prevents_growth_when_scaled_down_negative);
  RUN_TEST(test_conditional_integration_does_not_suppress_growth_when_not_scaled_down);
  RUN_TEST(test_first_compute_after_hold_reset_has_no_spike);

  return UNITY_END();
}
