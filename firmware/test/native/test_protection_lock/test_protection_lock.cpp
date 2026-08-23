#include <unity.h>

#include "drivetrain_control/config.hpp"
#include "drivetrain_control/protection/motor_lock.hpp"
#include "drivetrain_control/types.hpp"

// drivetrain-core task 5.1: 保護① モータロック検出（MotorLockDetector）を
// 検証するホストテスト。
//
// design.md "MotorLockDetector"（L7-L8 保護層）の厳密なシグネチャ・状態
// 機械を対象にする。要件 10.1〜10.7（出力指令が閾値以上かつ計測速度が
// 閾値以下の継続で発火する状態機械、パラメータの外部化、輪ごとの独立、
// 一過性の速度低下での不発、自動復帰と手動リセットの両対応、
// ハードウェアヒューズを前提にしないこと）。
//
// 以下を検証する:
//   A. 継続時間を超えた拘束で発火する
//   B. 継続時間に満たない一過性の拘束では発火しない
//   C. 条件が崩れると継続時間の計測がリセットされる（B の裏取り: 崩れた
//      直後に再度拘束しても、また最初から数える）
//   D. 輪ごとに独立して判定される（同一パラメータの2インスタンスが
//      互いの状態を参照しない）
//   E. 手動リセット（latching = true）: 発火後は reset() まで維持される
//   F. 自動復帰（latching = false）: 条件が clear_duration_ms 崩れ続けたら
//      自動的に解除される。復帰継続時間の途中で条件が再度成立すると
//      解除がキャンセルされ、発火状態が続く
//   G. 判定は絶対値: 負の出力指令・負の計測速度でも同様に発火する
//   H. duty と speed の両方が条件を満たさないと発火しない（片方だけでは
//      発火しない）

using drivetrain_control::DurationMs;
using drivetrain_control::LockParams;
using drivetrain_control::MotorLockDetector;
using drivetrain_control::TimeMs;

namespace {

// duty_threshold=0.5, speed_threshold_mm_s=5.0, duration_ms=100,
// clear_duration_ms=50。人間が暗算で確認しやすい丸めた値にする。
LockParams makeParams(bool latching) {
  LockParams p{};
  p.duty_threshold = 0.5f;
  p.speed_threshold_mm_s = 5.0f;
  p.duration_ms = static_cast<DurationMs>(100);
  p.clear_duration_ms = static_cast<DurationMs>(50);
  p.latching = latching;
  return p;
}

constexpr float kLockedDuty = 0.8f;      // >= 0.5 (拘束を表す出力指令)
constexpr float kLockedSpeed = 1.0f;     // <= 5.0 (回っていない計測速度)
constexpr float kFreeSpeed = 200.0f;     // > 5.0 (正常に回っている計測速度)
constexpr float kNoCommandDuty = 0.0f;   // < 0.5 (出力していない)

}  // namespace

void setUp(void) {}
void tearDown(void) {}

// ---------------------------------------------------------------------------
// A: 継続時間(100ms)を超えた拘束で発火する
// ---------------------------------------------------------------------------

void test_trips_when_lock_condition_exceeds_duration(void) {
  MotorLockDetector det(makeParams(/*latching=*/true));

  TimeMs now = 0;
  // t=0 で拘束開始 -> Idle -> Suspect
  TEST_ASSERT_FALSE(det.update(kLockedDuty, kLockedSpeed, now));
  TEST_ASSERT_FALSE(det.tripped());

  // t=50, 99: まだ 100ms を超えていない -> 発火しない
  now = 50;
  TEST_ASSERT_FALSE(det.update(kLockedDuty, kLockedSpeed, now));
  now = 99;
  TEST_ASSERT_FALSE(det.update(kLockedDuty, kLockedSpeed, now));
  TEST_ASSERT_FALSE(det.tripped());

  // t=101: 100ms を厳密に超えた -> 発火する
  now = 101;
  TEST_ASSERT_TRUE(det.update(kLockedDuty, kLockedSpeed, now));
  TEST_ASSERT_TRUE(det.tripped());
}

// ---------------------------------------------------------------------------
// B: 継続時間(100ms)に満たない一過性の拘束では発火しない
// ---------------------------------------------------------------------------

void test_does_not_trip_when_lock_condition_is_transient(void) {
  MotorLockDetector det(makeParams(/*latching=*/true));

  TimeMs now = 0;
  TEST_ASSERT_FALSE(det.update(kLockedDuty, kLockedSpeed, now));

  // t=80: まだ拘束中だが 100ms 未満。
  now = 80;
  TEST_ASSERT_FALSE(det.update(kLockedDuty, kLockedSpeed, now));

  // t=90: 速度が回復し、条件が崩れる（要件 10.4: 一過性の速度低下）。
  now = 90;
  TEST_ASSERT_FALSE(det.update(kLockedDuty, kFreeSpeed, now));
  TEST_ASSERT_FALSE(det.tripped());

  // t=300: 十分に時間が経っても、条件がすでに崩れているので発火しない。
  now = 300;
  TEST_ASSERT_FALSE(det.update(kLockedDuty, kFreeSpeed, now));
  TEST_ASSERT_FALSE(det.tripped());
}

// ---------------------------------------------------------------------------
// C: 条件が崩れると継続時間の計測がリセットされる（崩れた直後に再拘束しても
// 最初からやり直しになり、「合計すると 100ms を超えている」だけでは発火しない）
// ---------------------------------------------------------------------------

void test_condition_break_resets_duration_timer(void) {
  MotorLockDetector det(makeParams(/*latching=*/true));

  TimeMs now = 0;
  TEST_ASSERT_FALSE(det.update(kLockedDuty, kLockedSpeed, now));

  // t=90: 90ms 拘束が続いた（100ms 未満）。
  now = 90;
  TEST_ASSERT_FALSE(det.update(kLockedDuty, kLockedSpeed, now));

  // t=95: 条件が崩れる。Suspect -> Idle、タイマーはリセットされる。
  now = 95;
  TEST_ASSERT_FALSE(det.update(kLockedDuty, kFreeSpeed, now));

  // t=100: 再び拘束が始まる（新しい Suspect の起点は t=100）。
  now = 100;
  TEST_ASSERT_FALSE(det.update(kLockedDuty, kLockedSpeed, now));

  // t=190: 新しい起点(100)から 90ms しか経っていない -> まだ発火しない。
  // もしタイマーがリセットされていなければ「合計 90+90=180ms > 100ms」で
  // 誤って発火してしまう箇所。
  now = 190;
  TEST_ASSERT_FALSE(det.update(kLockedDuty, kLockedSpeed, now));
  TEST_ASSERT_FALSE(det.tripped());

  // t=201: 新しい起点(100)から 101ms 経過 -> 発火する。
  now = 201;
  TEST_ASSERT_TRUE(det.update(kLockedDuty, kLockedSpeed, now));
  TEST_ASSERT_TRUE(det.tripped());
}

// ---------------------------------------------------------------------------
// D: 輪ごとに独立して判定される（同一パラメータの2インスタンスが互いの
// 状態を参照しない。要件 10.3）
// ---------------------------------------------------------------------------

void test_wheels_are_judged_independently(void) {
  MotorLockDetector wheel_a(makeParams(/*latching=*/true));
  MotorLockDetector wheel_b(makeParams(/*latching=*/true));

  TimeMs now = 0;
  // wheel_a だけを拘束する。wheel_b は正常に回っている。
  TEST_ASSERT_FALSE(wheel_a.update(kLockedDuty, kLockedSpeed, now));
  TEST_ASSERT_FALSE(wheel_b.update(kLockedDuty, kFreeSpeed, now));

  now = 150;
  TEST_ASSERT_TRUE(wheel_a.update(kLockedDuty, kLockedSpeed, now));
  TEST_ASSERT_FALSE(wheel_b.update(kLockedDuty, kFreeSpeed, now));

  TEST_ASSERT_TRUE(wheel_a.tripped());
  TEST_ASSERT_FALSE(wheel_b.tripped());
}

// ---------------------------------------------------------------------------
// E: 手動リセット（latching = true）: 発火後は reset() まで維持される
// ---------------------------------------------------------------------------

void test_manual_reset_required_when_latching(void) {
  MotorLockDetector det(makeParams(/*latching=*/true));

  TimeMs now = 0;
  det.update(kLockedDuty, kLockedSpeed, now);
  now = 150;
  TEST_ASSERT_TRUE(det.update(kLockedDuty, kLockedSpeed, now));
  TEST_ASSERT_TRUE(det.tripped());

  // 条件が解除され、かつ時間が経過しても、latching = true の間は
  // 自動的には解除されない。
  now = 5000;
  TEST_ASSERT_TRUE(det.update(kNoCommandDuty, kFreeSpeed, now));
  TEST_ASSERT_TRUE(det.tripped());

  // reset() で明示的に解除する（要件 10.5 の手動リセット）。
  now = 5001;
  det.reset(now);
  TEST_ASSERT_FALSE(det.tripped());

  // reset() 後は通常どおり Idle から再判定できる（拘束すれば再度発火する）。
  now = 5001;
  det.update(kLockedDuty, kLockedSpeed, now);
  now = 5150;
  TEST_ASSERT_TRUE(det.update(kLockedDuty, kLockedSpeed, now));
}

// ---------------------------------------------------------------------------
// F: 自動復帰（latching = false）: 条件が clear_duration_ms(50) 崩れ続けたら
// 自動的に解除される。途中で条件が再成立すると解除がキャンセルされる
// ---------------------------------------------------------------------------

void test_auto_recovers_after_condition_clears_for_clear_duration(void) {
  MotorLockDetector det(makeParams(/*latching=*/false));

  TimeMs now = 0;
  det.update(kLockedDuty, kLockedSpeed, now);
  now = 150;
  TEST_ASSERT_TRUE(det.update(kLockedDuty, kLockedSpeed, now));
  TEST_ASSERT_TRUE(det.tripped());

  // t=160: 条件が崩れ始める（解除タイマーの起点）。
  now = 160;
  TEST_ASSERT_TRUE(det.update(kNoCommandDuty, kFreeSpeed, now));
  TEST_ASSERT_TRUE(det.tripped());  // まだ 50ms 経っていない

  // t=200: 起点(160)から 40ms -> まだ解除されない。
  now = 200;
  TEST_ASSERT_TRUE(det.update(kNoCommandDuty, kFreeSpeed, now));
  TEST_ASSERT_TRUE(det.tripped());

  // t=212: 起点(160)から 52ms、50ms を厳密に超えた -> 自動的に解除される。
  now = 212;
  TEST_ASSERT_FALSE(det.update(kNoCommandDuty, kFreeSpeed, now));
  TEST_ASSERT_FALSE(det.tripped());
}

void test_auto_recovery_timer_cancelled_when_condition_reasserts(void) {
  MotorLockDetector det(makeParams(/*latching=*/false));

  TimeMs now = 0;
  det.update(kLockedDuty, kLockedSpeed, now);
  now = 150;
  TEST_ASSERT_TRUE(det.update(kLockedDuty, kLockedSpeed, now));

  // t=160: 解除タイマー開始。
  now = 160;
  det.update(kNoCommandDuty, kFreeSpeed, now);

  // t=190: 30ms 経過した時点で、条件が再び成立してしまう
  // （例えば出力が再び上がり、輪はまだ回っていない）。
  now = 190;
  TEST_ASSERT_TRUE(det.update(kLockedDuty, kLockedSpeed, now));
  TEST_ASSERT_TRUE(det.tripped());  // 解除されずに発火状態が続く

  // t=201: もし解除タイマーがキャンセルされていなければ、元の起点(160)から
  // 41ms しか経っていないため境界に近いが、既に 50ms 超え相当の時間が
  // 経っている状況でも、タイマーが正しくキャンセルされていれば解除
  // されない。ここでは新しい解除起点がまだ発生していないことを確認する。
  now = 201;
  TEST_ASSERT_TRUE(det.update(kLockedDuty, kLockedSpeed, now));
  TEST_ASSERT_TRUE(det.tripped());

  // t=250: あらためて条件を崩し、新しい解除タイマーが t=250 から
  // 数え直しになることを確認する（旧タイマーの残り時間を流用しない）。
  now = 250;
  TEST_ASSERT_TRUE(det.update(kNoCommandDuty, kFreeSpeed, now));
  now = 295;  // 新起点(250)から 45ms -> まだ解除されない
  TEST_ASSERT_TRUE(det.update(kNoCommandDuty, kFreeSpeed, now));
  TEST_ASSERT_TRUE(det.tripped());
  now = 302;  // 新起点(250)から 52ms -> 解除される
  TEST_ASSERT_FALSE(det.update(kNoCommandDuty, kFreeSpeed, now));
  TEST_ASSERT_FALSE(det.tripped());
}

// ---------------------------------------------------------------------------
// G: 判定は絶対値。負の出力指令・負の計測速度でも同様に発火する
// ---------------------------------------------------------------------------

void test_judgement_uses_absolute_value_of_duty_and_speed(void) {
  MotorLockDetector det(makeParams(/*latching=*/true));

  TimeMs now = 0;
  // 負方向へ 0.8 出しているが、輪はわずかに逆回転しているだけ（|-1.0| <= 5.0）。
  TEST_ASSERT_FALSE(det.update(-kLockedDuty, -kLockedSpeed, now));
  now = 150;
  TEST_ASSERT_TRUE(det.update(-kLockedDuty, -kLockedSpeed, now));
  TEST_ASSERT_TRUE(det.tripped());
}

// ---------------------------------------------------------------------------
// H: duty と speed の両方が条件を満たさないと発火しない
// ---------------------------------------------------------------------------

void test_does_not_trip_when_only_duty_condition_holds(void) {
  MotorLockDetector det(makeParams(/*latching=*/true));

  TimeMs now = 0;
  // duty は閾値以上だが、輪はちゃんと回っている（speed が閾値超過）。
  det.update(kLockedDuty, kFreeSpeed, now);
  now = 500;
  TEST_ASSERT_FALSE(det.update(kLockedDuty, kFreeSpeed, now));
  TEST_ASSERT_FALSE(det.tripped());
}

void test_does_not_trip_when_only_speed_condition_holds(void) {
  MotorLockDetector det(makeParams(/*latching=*/true));

  TimeMs now = 0;
  // speed は閾値以下だが、そもそも出力していない（duty が閾値未満）。
  det.update(kNoCommandDuty, kLockedSpeed, now);
  now = 500;
  TEST_ASSERT_FALSE(det.update(kNoCommandDuty, kLockedSpeed, now));
  TEST_ASSERT_FALSE(det.tripped());
}

int main(int argc, char **argv) {
  (void)argc;
  (void)argv;
  UNITY_BEGIN();

  RUN_TEST(test_trips_when_lock_condition_exceeds_duration);
  RUN_TEST(test_does_not_trip_when_lock_condition_is_transient);
  RUN_TEST(test_condition_break_resets_duration_timer);
  RUN_TEST(test_wheels_are_judged_independently);
  RUN_TEST(test_manual_reset_required_when_latching);
  RUN_TEST(test_auto_recovers_after_condition_clears_for_clear_duration);
  RUN_TEST(test_auto_recovery_timer_cancelled_when_condition_reasserts);
  RUN_TEST(test_judgement_uses_absolute_value_of_duty_and_speed);
  RUN_TEST(test_does_not_trip_when_only_duty_condition_holds);
  RUN_TEST(test_does_not_trip_when_only_speed_condition_holds);

  return UNITY_END();
}
