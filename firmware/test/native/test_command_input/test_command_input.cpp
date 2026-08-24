#include <unity.h>

#include <cmath>
#include <cstdint>

#include "drivetrain_control/command_input.hpp"
#include "drivetrain_control/config.hpp"
#include "drivetrain_control/kinematics.hpp"
#include "drivetrain_control/types.hpp"

// drivetrain-core task 6.1: 3つの指令入口（CommandInput）を検証する
// ホストテスト。
//
// design.md "CommandInput"（L9）の厳密なシグネチャと Postconditions を対象
// にする。要件 5.1〜5.9, 6.5, 17.6。
//
// 以下を検証する:
//   A. 機体速度指令から得た輪目標速度と同じ値を輪単体入口へ直接与えたとき、
//      latched() の内容が一致すること（要件 5.2, 5.9 が構造で成立している
//      ことの振る舞い上の裏取り）
//   B. 範囲外の機体速度指令・輪単体指令それぞれで、クランプ/縮小が起きた
//      ことが CommandAcceptance::clamped / lastCommandClamped() へ報告
//      されること（要件 5.4）。範囲内では報告されないこと
//   C. 過去時刻の指令の拒否（要件 5.4 の一部）: 起動後最初の指令は絶対時刻
//      によらず受理されること、2回目以降は issued_at_ms <= 直前の有効時刻
//      なら拒否され latched() が変化しないこと。指令の型をまたいでも同じ
//      latched().issued_at_ms を基準に判定されること
//   D. setOutputEnabled() が指令の投入と完全に独立していること（要件 5.6,
//      5.7）: submit() が outputEnabled() を変えないこと、
//      setOutputEnabled() が latched() を変えないこと

using drivetrain_control::BodyVelocityCommand;
using drivetrain_control::CommandAcceptance;
using drivetrain_control::CommandInput;
using drivetrain_control::ControlPath;
using drivetrain_control::GeometryParams;
using drivetrain_control::Kinematics;
using drivetrain_control::kWheelCount;
using drivetrain_control::MotionLimits;
using drivetrain_control::TimeMs;
using drivetrain_control::WheelDutyCommand;
using drivetrain_control::WheelTargets;
using drivetrain_control::WheelVelocityCommand;

namespace {

constexpr float kTight = 1.0e-2f;

GeometryParams makeGeometry() {
  return GeometryParams::equilateral(/*base_radius_mm=*/100.0f, /*first_wheel_angle_rad=*/0.0f);
}

MotionLimits makeLimits() {
  MotionLimits limits{};
  limits.max_body_speed_mm_s = 300.0f;
  limits.max_body_omega_rad_s = 5.0f;
  limits.max_wheel_speed_mm_s = 200.0f;
  return limits;
}

}  // namespace

void setUp(void) {}
void tearDown(void) {}

// ---------------------------------------------------------------------------
// A: 機体速度指令 → 輪目標速度と、同じ値を輪単体入口へ直接与えた場合の
//    latched() が一致すること（要件 5.2, 5.9 の構造的成立の振る舞い上の
//    裏取り）
// ---------------------------------------------------------------------------

void test_body_and_wheel_entry_points_converge_to_same_wheel_targets(void) {
  const GeometryParams geometry = makeGeometry();
  const Kinematics kinematics(geometry);
  const MotionLimits limits = makeLimits();

  // 範囲内に収まる指令を選ぶ（クランプ/縮小が起きない条件で、純粋な
  // 「経路の一致」だけを検証するため）。
  const BodyVelocityCommand body{
      /*vx_mm_s=*/50.0f, /*vy_mm_s=*/-30.0f, /*omega_rad_s=*/0.5f, /*issued_at_ms=*/1000};

  CommandInput input_via_body(kinematics, limits);
  const CommandAcceptance acc_body = input_via_body.submit(body);
  TEST_ASSERT_TRUE(acc_body.accepted);
  TEST_ASSERT_FALSE(acc_body.clamped);
  TEST_ASSERT_FALSE(input_via_body.lastCommandClamped());

  const WheelTargets& wt_body = input_via_body.latched();
  TEST_ASSERT_TRUE(wt_body.valid);

  // 同じ3輪の値を、issued_at_ms も揃えて輪単体入口へ直接与える。
  WheelVelocityCommand wheel{};
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    wheel.wheel_mm_s[i] = wt_body.mm_s[i];
  }
  wheel.issued_at_ms = body.issued_at_ms;

  CommandInput input_via_wheel(kinematics, limits);
  const CommandAcceptance acc_wheel = input_via_wheel.submit(wheel);
  TEST_ASSERT_TRUE(acc_wheel.accepted);
  TEST_ASSERT_FALSE(acc_wheel.clamped);

  const WheelTargets& wt_wheel = input_via_wheel.latched();
  TEST_ASSERT_TRUE(wt_wheel.valid);
  TEST_ASSERT_EQUAL_INT64(wt_body.issued_at_ms, wt_wheel.issued_at_ms);
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    TEST_ASSERT_FLOAT_WITHIN(kTight, wt_body.mm_s[i], wt_wheel.mm_s[i]);
  }
}

// ---------------------------------------------------------------------------
// B-1: 範囲外の機体速度指令 — 運動上限でクランプしたうえで逆運動学を通し、
//      輪速度上限を超える場合は方向を保ったまま全輪を等比縮小する
//      （要件 6.5）。クランプ/縮小の発生が報告されること
// ---------------------------------------------------------------------------

void test_out_of_range_body_command_is_clamped_and_reported(void) {
  const GeometryParams geometry = makeGeometry();
  const Kinematics kinematics(geometry);
  const MotionLimits limits = makeLimits();

  // vx が運動上限(300)を大きく超える。omega=0, vy=0 なので、クランプ後は
  // (vx=300, vy=0, omega=0) が逆運動学へ渡る。その結果 wheel1/wheel2 が
  // 輪速度上限(200)を超えるため、等比縮小も同時に起きる。
  const BodyVelocityCommand body{
      /*vx_mm_s=*/1000.0f, /*vy_mm_s=*/0.0f, /*omega_rad_s=*/0.0f, /*issued_at_ms=*/2000};

  CommandInput input(kinematics, limits);
  const CommandAcceptance acc = input.submit(body);

  TEST_ASSERT_TRUE(acc.accepted);
  TEST_ASSERT_TRUE(acc.clamped);
  TEST_ASSERT_TRUE(input.lastCommandClamped());

  const WheelTargets& wt = input.latched();
  TEST_ASSERT_TRUE(wt.valid);
  // 等比縮小後、いずれの輪も輪速度上限を許容差内で超えないこと。
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    TEST_ASSERT_TRUE(std::fabs(wt.mm_s[i]) <= limits.max_wheel_speed_mm_s + kTight);
  }
  // 少なくとも1輪は上限近傍まで達している（縮小が実際に効いたことの確認）。
  float max_abs = 0.0f;
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    max_abs = std::fmax(max_abs, std::fabs(wt.mm_s[i]));
  }
  TEST_ASSERT_FLOAT_WITHIN(1.0f, limits.max_wheel_speed_mm_s, max_abs);
}

// ---------------------------------------------------------------------------
// B-2: 範囲内の機体速度指令はクランプされないこと（B-1 との対照）
// ---------------------------------------------------------------------------

void test_in_range_body_command_is_not_clamped(void) {
  const GeometryParams geometry = makeGeometry();
  const Kinematics kinematics(geometry);
  const MotionLimits limits = makeLimits();

  const BodyVelocityCommand body{
      /*vx_mm_s=*/10.0f, /*vy_mm_s=*/5.0f, /*omega_rad_s=*/0.1f, /*issued_at_ms=*/1500};

  CommandInput input(kinematics, limits);
  const CommandAcceptance acc = input.submit(body);

  TEST_ASSERT_TRUE(acc.accepted);
  TEST_ASSERT_FALSE(acc.clamped);
  TEST_ASSERT_FALSE(input.lastCommandClamped());
}

// ---------------------------------------------------------------------------
// B-3: 範囲外の輪単体指令 — 各輪を個別に飽和させる（等比縮小ではない）。
//      クランプの発生が報告されること
// ---------------------------------------------------------------------------

void test_out_of_range_wheel_command_is_saturated_per_wheel_and_reported(void) {
  const GeometryParams geometry = makeGeometry();
  const Kinematics kinematics(geometry);
  const MotionLimits limits = makeLimits();

  const WheelVelocityCommand wheel{
      /*wheel_mm_s=*/{250.0f, -250.0f, 50.0f}, /*issued_at_ms=*/3000};

  CommandInput input(kinematics, limits);
  const CommandAcceptance acc = input.submit(wheel);

  TEST_ASSERT_TRUE(acc.accepted);
  TEST_ASSERT_TRUE(acc.clamped);
  TEST_ASSERT_TRUE(input.lastCommandClamped());

  const WheelTargets& wt = input.latched();
  // 個別飽和: 輪2 (50 mm/s, 範囲内) はそのまま。輪0/輪1 は上限へ飽和。
  // 等比縮小であれば輪2 も比率で変化するはずだが、ここでは変化しない
  // ことがまさに「個別飽和 (per-wheel saturation)」であることの証拠。
  TEST_ASSERT_FLOAT_WITHIN(kTight, limits.max_wheel_speed_mm_s, wt.mm_s[0]);
  TEST_ASSERT_FLOAT_WITHIN(kTight, -limits.max_wheel_speed_mm_s, wt.mm_s[1]);
  TEST_ASSERT_FLOAT_WITHIN(kTight, 50.0f, wt.mm_s[2]);
}

// ---------------------------------------------------------------------------
// B-4: 範囲内の輪単体指令はクランプされないこと
// ---------------------------------------------------------------------------

void test_in_range_wheel_command_is_not_clamped(void) {
  const GeometryParams geometry = makeGeometry();
  const Kinematics kinematics(geometry);
  const MotionLimits limits = makeLimits();

  const WheelVelocityCommand wheel{
      /*wheel_mm_s=*/{100.0f, -100.0f, 50.0f}, /*issued_at_ms=*/3000};

  CommandInput input(kinematics, limits);
  const CommandAcceptance acc = input.submit(wheel);

  TEST_ASSERT_TRUE(acc.accepted);
  TEST_ASSERT_FALSE(acc.clamped);
  TEST_ASSERT_FALSE(input.lastCommandClamped());
}

// ---------------------------------------------------------------------------
// C-1: 起動後最初の指令はその絶対時刻値によらず受理される（比較対象となる
//      直前指令が無いため）
// ---------------------------------------------------------------------------

void test_first_ever_command_accepted_regardless_of_absolute_timestamp(void) {
  const GeometryParams geometry = makeGeometry();
  const Kinematics kinematics(geometry);
  const MotionLimits limits = makeLimits();

  CommandInput input(kinematics, limits);
  TEST_ASSERT_FALSE(input.latched().valid);

  // 負の時刻・非常に小さい時刻でも「最初の指令」なら受理される。
  const WheelVelocityCommand wheel{/*wheel_mm_s=*/{1.0f, 2.0f, 3.0f}, /*issued_at_ms=*/-100};
  const CommandAcceptance acc = input.submit(wheel);

  TEST_ASSERT_TRUE(acc.accepted);
  TEST_ASSERT_TRUE(input.latched().valid);
  TEST_ASSERT_EQUAL_INT64(-100, input.latched().issued_at_ms);
}

// ---------------------------------------------------------------------------
// C-2: 2回目以降、issued_at_ms <= 直前の有効時刻の指令は拒否され、
//      latched() は一切変化しない（過去時刻の指令でウォッチドッグを
//      欺けないようにするため）
// ---------------------------------------------------------------------------

void test_stale_command_is_rejected_and_latched_state_is_unchanged(void) {
  const GeometryParams geometry = makeGeometry();
  const Kinematics kinematics(geometry);
  const MotionLimits limits = makeLimits();

  CommandInput input(kinematics, limits);

  const WheelVelocityCommand first{/*wheel_mm_s=*/{10.0f, 20.0f, 30.0f}, /*issued_at_ms=*/1000};
  const CommandAcceptance acc_first = input.submit(first);
  TEST_ASSERT_TRUE(acc_first.accepted);

  const WheelTargets snapshot = input.latched();

  // 同一時刻 (<=) は拒否される。
  const WheelVelocityCommand same_time{
      /*wheel_mm_s=*/{99.0f, 99.0f, 99.0f}, /*issued_at_ms=*/1000};
  const CommandAcceptance acc_same = input.submit(same_time);
  TEST_ASSERT_FALSE(acc_same.accepted);

  // 過去時刻も拒否される。
  const WheelVelocityCommand past{
      /*wheel_mm_s=*/{99.0f, 99.0f, 99.0f}, /*issued_at_ms=*/999};
  const CommandAcceptance acc_past = input.submit(past);
  TEST_ASSERT_FALSE(acc_past.accepted);

  // どちらの拒否後も latched() は最初の指令のまま。
  const WheelTargets& after = input.latched();
  TEST_ASSERT_EQUAL_INT64(snapshot.issued_at_ms, after.issued_at_ms);
  TEST_ASSERT_TRUE(after.valid);
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    TEST_ASSERT_FLOAT_WITHIN(kTight, snapshot.mm_s[i], after.mm_s[i]);
  }

  // 未来時刻は受理され、latched() が更新される。
  const WheelVelocityCommand future{
      /*wheel_mm_s=*/{11.0f, 22.0f, 33.0f}, /*issued_at_ms=*/1001};
  const CommandAcceptance acc_future = input.submit(future);
  TEST_ASSERT_TRUE(acc_future.accepted);
  TEST_ASSERT_EQUAL_INT64(1001, input.latched().issued_at_ms);
}

// ---------------------------------------------------------------------------
// C-3: 過去時刻の判定は latched().issued_at_ms を基準にし、指令の型を
//      またいでも同じ基準が使われる（機体速度指令の後の輪単体指令、
//      その逆も含む）
// ---------------------------------------------------------------------------

void test_staleness_check_is_shared_across_command_types(void) {
  const GeometryParams geometry = makeGeometry();
  const Kinematics kinematics(geometry);
  const MotionLimits limits = makeLimits();

  CommandInput input(kinematics, limits);

  const BodyVelocityCommand body{
      /*vx_mm_s=*/10.0f, /*vy_mm_s=*/0.0f, /*omega_rad_s=*/0.0f, /*issued_at_ms=*/1000};
  TEST_ASSERT_TRUE(input.submit(body).accepted);
  const WheelTargets after_body = input.latched();

  // 輪単体指令が body より過去時刻なら拒否され、latched() は body 由来の
  // ままであること。
  const WheelVelocityCommand stale_wheel{
      /*wheel_mm_s=*/{5.0f, 5.0f, 5.0f}, /*issued_at_ms=*/999};
  TEST_ASSERT_FALSE(input.submit(stale_wheel).accepted);
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    TEST_ASSERT_FLOAT_WITHIN(kTight, after_body.mm_s[i], input.latched().mm_s[i]);
  }

  // 輪単体指令が body より未来時刻なら受理され、latched() が更新される。
  const WheelVelocityCommand fresh_wheel{
      /*wheel_mm_s=*/{7.0f, 7.0f, 7.0f}, /*issued_at_ms=*/1001};
  TEST_ASSERT_TRUE(input.submit(fresh_wheel).accepted);
  TEST_ASSERT_EQUAL_INT64(1001, input.latched().issued_at_ms);

  // 続いて機体速度指令が直前の輪単体指令より過去時刻なら拒否される。
  const BodyVelocityCommand stale_body{
      /*vx_mm_s=*/1.0f, /*vy_mm_s=*/0.0f, /*omega_rad_s=*/0.0f, /*issued_at_ms=*/1000};
  TEST_ASSERT_FALSE(input.submit(stale_body).accepted);
  TEST_ASSERT_EQUAL_INT64(1001, input.latched().issued_at_ms);
}

// ---------------------------------------------------------------------------
// D: setOutputEnabled() は指令の投入と完全に独立している（要件 5.6, 5.7）
// ---------------------------------------------------------------------------

void test_output_enable_is_independent_of_command_submission(void) {
  const GeometryParams geometry = makeGeometry();
  const Kinematics kinematics(geometry);
  const MotionLimits limits = makeLimits();

  CommandInput input(kinematics, limits);

  // 初期状態: 出力は許可されていない（安全側の既定）。
  TEST_ASSERT_FALSE(input.outputEnabled());

  // 指令を投入しても outputEnabled() は変化しない。
  const WheelVelocityCommand wheel{/*wheel_mm_s=*/{1.0f, 1.0f, 1.0f}, /*issued_at_ms=*/100};
  TEST_ASSERT_TRUE(input.submit(wheel).accepted);
  TEST_ASSERT_FALSE(input.outputEnabled());

  // setOutputEnabled() は latched() を変化させない。
  const WheelTargets before_enable = input.latched();
  input.setOutputEnabled(true, /*now=*/200);
  TEST_ASSERT_TRUE(input.outputEnabled());
  const WheelTargets& after_enable = input.latched();
  TEST_ASSERT_EQUAL_INT64(before_enable.issued_at_ms, after_enable.issued_at_ms);
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    TEST_ASSERT_FLOAT_WITHIN(kTight, before_enable.mm_s[i], after_enable.mm_s[i]);
  }

  // 指令を受け取ったこと自体は出力許可の条件ではない: 指令を投入し続けても
  // outputEnabled() は (許可済みの状態を) 維持し続ける。
  const WheelVelocityCommand wheel2{/*wheel_mm_s=*/{2.0f, 2.0f, 2.0f}, /*issued_at_ms=*/300};
  TEST_ASSERT_TRUE(input.submit(wheel2).accepted);
  TEST_ASSERT_TRUE(input.outputEnabled());

  // 出力不許可へ戻しても latched() は変化しない。
  const WheelTargets before_disable = input.latched();
  input.setOutputEnabled(false, /*now=*/400);
  TEST_ASSERT_FALSE(input.outputEnabled());
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    TEST_ASSERT_FLOAT_WITHIN(kTight, before_disable.mm_s[i], input.latched().mm_s[i]);
  }
}

// ---------------------------------------------------------------------------
// E: 開ループ指令（WheelDutyCommand）— task 10.1
//    - 定義域 [-1, +1] を超える目標デューティは輪ごとに個別飽和され、
//      CommandAcceptance::clamped / lastCommandClamped() へ報告される
//      （要件 5.10, 5.13）
//    - 投入後、保持中の経路（latched().path）が kOpenLoop になる
//      （要件 5.10）
//    - 経路をまたぐ指令の入れ替わりは、過去時刻でない限り通常の指令更新と
//      同じに受理される（issued_at_ms の単調性のみで判定。経路が変わった
//      こと自体は拒否条件にならない）
// ---------------------------------------------------------------------------

void test_out_of_domain_wheel_duty_command_is_saturated_per_wheel_and_reported(void) {
  const GeometryParams geometry = makeGeometry();
  const Kinematics kinematics(geometry);
  const MotionLimits limits = makeLimits();

  const WheelDutyCommand duty_cmd{
      /*wheel_duty=*/{1.5f, -1.5f, 0.4f}, /*issued_at_ms=*/5000};

  CommandInput input(kinematics, limits);
  const CommandAcceptance acc = input.submit(duty_cmd);

  TEST_ASSERT_TRUE(acc.accepted);
  TEST_ASSERT_TRUE(acc.clamped);
  TEST_ASSERT_TRUE(input.lastCommandClamped());

  const WheelTargets& wt = input.latched();
  // 個別飽和: 輪2 (0.4, 範囲内) はそのまま。輪0/輪1 は定義域の端へ飽和。
  TEST_ASSERT_FLOAT_WITHIN(kTight, 1.0f, wt.duty[0]);
  TEST_ASSERT_FLOAT_WITHIN(kTight, -1.0f, wt.duty[1]);
  TEST_ASSERT_FLOAT_WITHIN(kTight, 0.4f, wt.duty[2]);
}

void test_in_domain_wheel_duty_command_is_not_clamped(void) {
  const GeometryParams geometry = makeGeometry();
  const Kinematics kinematics(geometry);
  const MotionLimits limits = makeLimits();

  const WheelDutyCommand duty_cmd{
      /*wheel_duty=*/{0.3f, -0.3f, 0.0f}, /*issued_at_ms=*/5000};

  CommandInput input(kinematics, limits);
  const CommandAcceptance acc = input.submit(duty_cmd);

  TEST_ASSERT_TRUE(acc.accepted);
  TEST_ASSERT_FALSE(acc.clamped);
  TEST_ASSERT_FALSE(input.lastCommandClamped());

  const WheelTargets& wt = input.latched();
  TEST_ASSERT_FLOAT_WITHIN(kTight, 0.3f, wt.duty[0]);
  TEST_ASSERT_FLOAT_WITHIN(kTight, -0.3f, wt.duty[1]);
  TEST_ASSERT_FLOAT_WITHIN(kTight, 0.0f, wt.duty[2]);
}

void test_wheel_duty_command_sets_latched_path_to_open_loop(void) {
  const GeometryParams geometry = makeGeometry();
  const Kinematics kinematics(geometry);
  const MotionLimits limits = makeLimits();

  CommandInput input(kinematics, limits);

  // 投入前、まだ一度も指令が無い状態では既定経路（kVelocityPid）のまま。
  TEST_ASSERT_TRUE(ControlPath::kVelocityPid == input.latched().path);

  const WheelDutyCommand duty_cmd{
      /*wheel_duty=*/{0.5f, -0.5f, 0.1f}, /*issued_at_ms=*/6000};
  TEST_ASSERT_TRUE(input.submit(duty_cmd).accepted);

  TEST_ASSERT_TRUE(ControlPath::kOpenLoop == input.latched().path);
  TEST_ASSERT_TRUE(input.latched().valid);
  TEST_ASSERT_EQUAL_INT64(6000, input.latched().issued_at_ms);
}

void test_body_velocity_command_sets_latched_path_to_velocity_pid(void) {
  const GeometryParams geometry = makeGeometry();
  const Kinematics kinematics(geometry);
  const MotionLimits limits = makeLimits();

  CommandInput input(kinematics, limits);

  const BodyVelocityCommand body{
      /*vx_mm_s=*/10.0f, /*vy_mm_s=*/0.0f, /*omega_rad_s=*/0.0f, /*issued_at_ms=*/100};
  TEST_ASSERT_TRUE(input.submit(body).accepted);
  TEST_ASSERT_TRUE(ControlPath::kVelocityPid == input.latched().path);
}

void test_cross_path_transition_accepted_purely_by_timestamp_monotonicity(void) {
  const GeometryParams geometry = makeGeometry();
  const Kinematics kinematics(geometry);
  const MotionLimits limits = makeLimits();

  CommandInput input(kinematics, limits);

  // T1: 機体速度指令（kVelocityPid 経路）。
  const BodyVelocityCommand body{
      /*vx_mm_s=*/10.0f, /*vy_mm_s=*/0.0f, /*omega_rad_s=*/0.0f, /*issued_at_ms=*/1000};
  TEST_ASSERT_TRUE(input.submit(body).accepted);
  TEST_ASSERT_TRUE(ControlPath::kVelocityPid == input.latched().path);

  // T2 (> T1): 開ループ指令。経路が変わったこと自体は拒否条件にならない
  // ため受理され、経路が kOpenLoop へ切り替わる。
  const WheelDutyCommand duty_cmd{
      /*wheel_duty=*/{0.2f, 0.2f, 0.2f}, /*issued_at_ms=*/2000};
  const CommandAcceptance acc_duty = input.submit(duty_cmd);
  TEST_ASSERT_TRUE(acc_duty.accepted);
  TEST_ASSERT_TRUE(ControlPath::kOpenLoop == input.latched().path);
  const WheelTargets snapshot = input.latched();

  // T3 (<= T2): 輪速度指令だが、issued_at_ms が直前の有効時刻(T2)以下なの
  // で拒否される。latched() は T2 の開ループ状態のまま変化しない（単一の
  // 入れ物で経路をまたいだ鮮度判定が成立していることの裏取り）。
  const WheelVelocityCommand stale_wheel_velocity{
      /*wheel_mm_s=*/{9.0f, 9.0f, 9.0f}, /*issued_at_ms=*/2000};
  const CommandAcceptance acc_stale = input.submit(stale_wheel_velocity);
  TEST_ASSERT_FALSE(acc_stale.accepted);

  const WheelTargets& after = input.latched();
  TEST_ASSERT_TRUE(ControlPath::kOpenLoop == after.path);
  TEST_ASSERT_EQUAL_INT64(snapshot.issued_at_ms, after.issued_at_ms);
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    TEST_ASSERT_FLOAT_WITHIN(kTight, snapshot.duty[i], after.duty[i]);
  }
}

int main(int argc, char **argv) {
  (void)argc;
  (void)argv;
  UNITY_BEGIN();

  RUN_TEST(test_body_and_wheel_entry_points_converge_to_same_wheel_targets);
  RUN_TEST(test_out_of_range_body_command_is_clamped_and_reported);
  RUN_TEST(test_in_range_body_command_is_not_clamped);
  RUN_TEST(test_out_of_range_wheel_command_is_saturated_per_wheel_and_reported);
  RUN_TEST(test_in_range_wheel_command_is_not_clamped);
  RUN_TEST(test_first_ever_command_accepted_regardless_of_absolute_timestamp);
  RUN_TEST(test_stale_command_is_rejected_and_latched_state_is_unchanged);
  RUN_TEST(test_staleness_check_is_shared_across_command_types);
  RUN_TEST(test_output_enable_is_independent_of_command_submission);

  RUN_TEST(test_out_of_domain_wheel_duty_command_is_saturated_per_wheel_and_reported);
  RUN_TEST(test_in_domain_wheel_duty_command_is_not_clamped);
  RUN_TEST(test_wheel_duty_command_sets_latched_path_to_open_loop);
  RUN_TEST(test_body_velocity_command_sets_latched_path_to_velocity_pid);
  RUN_TEST(test_cross_path_transition_accepted_purely_by_timestamp_monotonicity);

  return UNITY_END();
}
