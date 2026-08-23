#include <unity.h>

#include <cmath>
#include <cstdint>

#include "drivetrain_control/config.hpp"
#include "drivetrain_control/kinematics.hpp"
#include "drivetrain_control/types.hpp"

// drivetrain-core task 3.3: 3輪オムニの逆運動学と順運動学（Kinematics）を
// 検証するホストテスト。
//
// design.md "Kinematics"（L5）の厳密なシグネチャ・行定義を対象にする。
// 要件 6.1（並進2成分・回転から3輪の目標速度）、6.2（取付角・距離を外部
// パラメータとして扱う）、6.3（純並進の往復一致）、6.4（純回転で3輪同一
// 符号・同一大きさ）、6.5（範囲超過時の方向を保った等比縮小）、6.6（輪番号
// と符号の対応を一箇所で定義）、8.1（順運動学）、8.4（逆→順の復元一致）。
//
// 以下4系統を検証する:
//   1. 純並進・純回転・複合の各指令で inverse()→forward() の往復一致
//   2. 純回転指令で3輪が同一符号・同一大きさになること
//   3. 行定義 [-sinα_i, cosα_i, R]（要件 6.6）そのものを、120° 等配置の
//      具体的な角度で直接検証する（往復一致だけでは符号が一貫してひっくり
//      返っていても検出できないため）
//   4. scaleToLimit() が既に範囲内なら無変更、超過時は方向（比率・符号）を
//      保ったまま等比縮小すること

using drivetrain_control::BodyVelocity;
using drivetrain_control::GeometryParams;
using drivetrain_control::Kinematics;
using drivetrain_control::kWheelCount;
using drivetrain_control::scaleToLimit;

namespace {

constexpr float kFloatTolerance = 1.0e-2f;

// 120° 等配置。第0輪の取付角を 0 に固定し、行定義を具体的な数値で直接
// 検証できるようにする（equilateral() は task 2.3 実装済みの補助を再利用
// する。三角関数の手書きはここでも行わない）。
GeometryParams makeGeometry() {
  return GeometryParams::equilateral(/*base_radius_mm=*/100.0f, /*first_wheel_angle_rad=*/0.0f);
}

}  // namespace

void setUp(void) {}
void tearDown(void) {}

// ---------------------------------------------------------------------------
// 系統1: 純並進・純回転・複合の各指令で inverse()→forward() の往復一致
// （要件 6.3, 8.4）
// ---------------------------------------------------------------------------

void test_round_trip_pure_translation(void) {
  const Kinematics kinematics(makeGeometry());
  const BodyVelocity in{/*vx=*/120.0f, /*vy=*/-60.0f, /*omega=*/0.0f};

  float wheel_mm_s[kWheelCount];
  kinematics.inverse(in, wheel_mm_s);
  const BodyVelocity out = kinematics.forward(wheel_mm_s);

  TEST_ASSERT_FLOAT_WITHIN(kFloatTolerance, in.vx_mm_s, out.vx_mm_s);
  TEST_ASSERT_FLOAT_WITHIN(kFloatTolerance, in.vy_mm_s, out.vy_mm_s);
  TEST_ASSERT_FLOAT_WITHIN(kFloatTolerance, in.omega_rad_s, out.omega_rad_s);
}

void test_round_trip_pure_rotation(void) {
  const Kinematics kinematics(makeGeometry());
  const BodyVelocity in{/*vx=*/0.0f, /*vy=*/0.0f, /*omega=*/1.75f};

  float wheel_mm_s[kWheelCount];
  kinematics.inverse(in, wheel_mm_s);
  const BodyVelocity out = kinematics.forward(wheel_mm_s);

  TEST_ASSERT_FLOAT_WITHIN(kFloatTolerance, in.vx_mm_s, out.vx_mm_s);
  TEST_ASSERT_FLOAT_WITHIN(kFloatTolerance, in.vy_mm_s, out.vy_mm_s);
  TEST_ASSERT_FLOAT_WITHIN(kFloatTolerance, in.omega_rad_s, out.omega_rad_s);
}

void test_round_trip_combined_translation_and_rotation(void) {
  const Kinematics kinematics(makeGeometry());
  const BodyVelocity in{/*vx=*/80.0f, /*vy=*/40.0f, /*omega=*/-0.9f};

  float wheel_mm_s[kWheelCount];
  kinematics.inverse(in, wheel_mm_s);
  const BodyVelocity out = kinematics.forward(wheel_mm_s);

  TEST_ASSERT_FLOAT_WITHIN(kFloatTolerance, in.vx_mm_s, out.vx_mm_s);
  TEST_ASSERT_FLOAT_WITHIN(kFloatTolerance, in.vy_mm_s, out.vy_mm_s);
  TEST_ASSERT_FLOAT_WITHIN(kFloatTolerance, in.omega_rad_s, out.omega_rad_s);
}

// ---------------------------------------------------------------------------
// 系統2: 純回転指令で3輪が同一符号・同一大きさになる（要件 6.4）
// ---------------------------------------------------------------------------

void test_pure_rotation_drives_all_wheels_with_same_sign_and_magnitude(void) {
  const Kinematics kinematics(makeGeometry());
  const BodyVelocity in{/*vx=*/0.0f, /*vy=*/0.0f, /*omega=*/2.0f};

  float wheel_mm_s[kWheelCount];
  kinematics.inverse(in, wheel_mm_s);

  // 行定義の第3列は輪によらず同一の base_radius_mm なので、vx=vy=0 では
  // 全輪が R * omega の同一値になる。
  TEST_ASSERT_FLOAT_WITHIN(kFloatTolerance, wheel_mm_s[0], wheel_mm_s[1]);
  TEST_ASSERT_FLOAT_WITHIN(kFloatTolerance, wheel_mm_s[0], wheel_mm_s[2]);
  TEST_ASSERT_TRUE(wheel_mm_s[0] > 0.0f);  // omega > 0 なので正符号
}

void test_pure_rotation_negative_omega_drives_all_wheels_negative(void) {
  const Kinematics kinematics(makeGeometry());
  const BodyVelocity in{/*vx=*/0.0f, /*vy=*/0.0f, /*omega=*/-3.0f};

  float wheel_mm_s[kWheelCount];
  kinematics.inverse(in, wheel_mm_s);

  TEST_ASSERT_FLOAT_WITHIN(kFloatTolerance, wheel_mm_s[0], wheel_mm_s[1]);
  TEST_ASSERT_FLOAT_WITHIN(kFloatTolerance, wheel_mm_s[0], wheel_mm_s[2]);
  TEST_ASSERT_TRUE(wheel_mm_s[0] < 0.0f);
}

// ---------------------------------------------------------------------------
// 系統3: 行定義 [-sinα_i, cosα_i, R] を 120° 等配置の具体的な角度で直接
// 検証する（要件 6.6）。往復一致だけでは M と M^-1 の両方が同時に符号
// 反転していても検出できないため、片方向の数値を直接固定する。
// ---------------------------------------------------------------------------

void test_row_definition_matches_first_wheel_angle_zero(void) {
  // first_wheel_angle_rad = 0 なので wheel0 の行は
  // [-sin(0), cos(0), R] = [0, 1, R]。vy のみの指令は wheel0 へそのまま
  // vy が乗る（vx とは結合しない）。
  const Kinematics kinematics(makeGeometry());
  const BodyVelocity in{/*vx=*/0.0f, /*vy=*/10.0f, /*omega=*/0.0f};

  float wheel_mm_s[kWheelCount];
  kinematics.inverse(in, wheel_mm_s);

  TEST_ASSERT_FLOAT_WITHIN(kFloatTolerance, 10.0f, wheel_mm_s[0]);
}

void test_row_definition_matches_second_and_third_wheel_angles(void) {
  // wheel1 角度 = 120°: [-sin(120°), cos(120°), R] = [-0.8660, -0.5, R]
  // wheel2 角度 = 240°: [-sin(240°), cos(240°), R] = [+0.8660, -0.5, R]
  // vy のみ 10.0 を与えると、wheel1 = wheel2 = -0.5 * 10.0 = -5.0
  const Kinematics kinematics(makeGeometry());
  const BodyVelocity in{/*vx=*/0.0f, /*vy=*/10.0f, /*omega=*/0.0f};

  float wheel_mm_s[kWheelCount];
  kinematics.inverse(in, wheel_mm_s);

  TEST_ASSERT_FLOAT_WITHIN(kFloatTolerance, -5.0f, wheel_mm_s[1]);
  TEST_ASSERT_FLOAT_WITHIN(kFloatTolerance, -5.0f, wheel_mm_s[2]);
}

void test_row_definition_vx_only_couples_via_negative_sine(void) {
  // wheel0 角度 = 0: [-sin(0), cos(0), R] = [0, 1, R]。vx のみの指令は
  // wheel0 へ結合しない（-sin(0) == 0）。
  const Kinematics kinematics(makeGeometry());
  const BodyVelocity in{/*vx=*/25.0f, /*vy=*/0.0f, /*omega=*/0.0f};

  float wheel_mm_s[kWheelCount];
  kinematics.inverse(in, wheel_mm_s);

  TEST_ASSERT_FLOAT_WITHIN(kFloatTolerance, 0.0f, wheel_mm_s[0]);
}

// ---------------------------------------------------------------------------
// determinant(): 非退化な120°等配置では非ゼロを返す
// ---------------------------------------------------------------------------

void test_determinant_is_nonzero_for_equilateral_geometry(void) {
  const Kinematics kinematics(makeGeometry());
  TEST_ASSERT_TRUE(std::fabs(kinematics.determinant()) > 1.0e-3f);
}

// ---------------------------------------------------------------------------
// 系統4: scaleToLimit() — 範囲内なら無変更、超過時は方向を保った等比縮小
// （要件 6.5, 12.4）
// ---------------------------------------------------------------------------

void test_scale_to_limit_is_noop_when_already_within_limit(void) {
  float values[kWheelCount] = {1.0f, -2.0f, 0.5f};
  const float scale = scaleToLimit(values, 5.0f);

  TEST_ASSERT_FLOAT_WITHIN(kFloatTolerance, 1.0f, scale);
  TEST_ASSERT_FLOAT_WITHIN(kFloatTolerance, 1.0f, values[0]);
  TEST_ASSERT_FLOAT_WITHIN(kFloatTolerance, -2.0f, values[1]);
  TEST_ASSERT_FLOAT_WITHIN(kFloatTolerance, 0.5f, values[2]);
}

void test_scale_to_limit_shrinks_all_values_proportionally_when_exceeding(void) {
  float values[kWheelCount] = {3.0f, -6.0f, 1.0f};  // max |.| == 6.0
  const float scale = scaleToLimit(values, 3.0f);

  TEST_ASSERT_FLOAT_WITHIN(kFloatTolerance, 0.5f, scale);
  TEST_ASSERT_FLOAT_WITHIN(kFloatTolerance, 1.5f, values[0]);
  TEST_ASSERT_FLOAT_WITHIN(kFloatTolerance, -3.0f, values[1]);
  TEST_ASSERT_FLOAT_WITHIN(kFloatTolerance, 0.5f, values[2]);

  // 最大値がちょうど limit の大きさになる
  TEST_ASSERT_FLOAT_WITHIN(kFloatTolerance, 3.0f, std::fabs(values[1]));
}

void test_scale_to_limit_preserves_direction_of_commanded_motion(void) {
  // 逆運動学で得た輪速度を等比縮小し、順運動学で復元した機体速度が
  // 元の指令と同じ方向（vx:vy の比率・符号）を保つことを確認する
  // （要件 6.5, タスク3.3の完了条件）。
  const Kinematics kinematics(makeGeometry());
  const BodyVelocity commanded{/*vx=*/200.0f, /*vy=*/100.0f, /*omega=*/0.0f};

  float wheel_mm_s[kWheelCount];
  kinematics.inverse(commanded, wheel_mm_s);

  float max_abs = 0.0f;
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    const float abs_value = std::fabs(wheel_mm_s[i]);
    if (abs_value > max_abs) {
      max_abs = abs_value;
    }
  }
  TEST_ASSERT_TRUE(max_abs > 0.0f);

  // 必ず縮小が起きるよう、実際の最大値の半分を上限にする。
  const float limit = max_abs * 0.5f;
  const float scale = scaleToLimit(wheel_mm_s, limit);

  TEST_ASSERT_FLOAT_WITHIN(kFloatTolerance, 0.5f, scale);

  const BodyVelocity recovered = kinematics.forward(wheel_mm_s);

  // 線形写像なので、縮小後に復元した機体速度は元の指令に scale を掛けた
  // ものと一致する（=同じ方向、大きさだけが scale 倍）。
  TEST_ASSERT_FLOAT_WITHIN(kFloatTolerance, commanded.vx_mm_s * scale, recovered.vx_mm_s);
  TEST_ASSERT_FLOAT_WITHIN(kFloatTolerance, commanded.vy_mm_s * scale, recovered.vy_mm_s);
  TEST_ASSERT_FLOAT_WITHIN(kFloatTolerance, commanded.omega_rad_s * scale, recovered.omega_rad_s);

  // 方向（符号）そのものも保たれている。
  TEST_ASSERT_TRUE((recovered.vx_mm_s > 0.0f) == (commanded.vx_mm_s > 0.0f));
  TEST_ASSERT_TRUE((recovered.vy_mm_s > 0.0f) == (commanded.vy_mm_s > 0.0f));
}

int main(int argc, char **argv) {
  (void)argc;
  (void)argv;
  UNITY_BEGIN();

  RUN_TEST(test_round_trip_pure_translation);
  RUN_TEST(test_round_trip_pure_rotation);
  RUN_TEST(test_round_trip_combined_translation_and_rotation);

  RUN_TEST(test_pure_rotation_drives_all_wheels_with_same_sign_and_magnitude);
  RUN_TEST(test_pure_rotation_negative_omega_drives_all_wheels_negative);

  RUN_TEST(test_row_definition_matches_first_wheel_angle_zero);
  RUN_TEST(test_row_definition_matches_second_and_third_wheel_angles);
  RUN_TEST(test_row_definition_vx_only_couples_via_negative_sine);

  RUN_TEST(test_determinant_is_nonzero_for_equilateral_geometry);

  RUN_TEST(test_scale_to_limit_is_noop_when_already_within_limit);
  RUN_TEST(test_scale_to_limit_shrinks_all_values_proportionally_when_exceeding);
  RUN_TEST(test_scale_to_limit_preserves_direction_of_commanded_motion);

  return UNITY_END();
}
