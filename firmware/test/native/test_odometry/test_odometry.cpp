#include <unity.h>

#include <cmath>
#include <cstdint>

#include "drivetrain_control/config.hpp"
#include "drivetrain_control/kinematics.hpp"
#include "drivetrain_control/odometry.hpp"
#include "drivetrain_control/types.hpp"
#include "drivetrain_control/units.hpp"

// drivetrain-core task 4.1: 順運動学によるオドメトリ（Odometry）を検証する
// ホストテスト。
//
// design.md "Odometry"（L6）の厳密なシグネチャ・積分方式（中点法）を対象に
// する。要件 8.2（機体速度の時間積分で位置・姿勢を推定）、8.3（原点・姿勢
// の外部からの初期化）、8.5（距離mm・時刻msでの提供）、8.6（初期化時点から
// の経過時間と累積走行距離）、8.7（外部座標系への整合を責務に含めない —
// reset() が「最後に初期化した時点」以上の意味を持たないことをテストでも
// 確認する）。
//
// Odometry は Kinematics::forward() を内部で呼ぶ設計（design.md
// "Odometry" コンストラクタ）なので、各テストは Kinematics::inverse() で
// 「機体速度 → 輪速度」を求め、その輪速度を Odometry::update() へ渡す。
// これにより Kinematics への依存注入そのものも実地で検証する。
//
// 以下5系統を検証する:
//   1. 直進: x が進み y/theta が変化しない
//   2. その場旋回: x/y が動かず theta だけ変化し、traveled_mm が増えない
//   3. 一定回転速度の円軌道が1周で閉じる（中点法の往復精度）
//   4. reset() で原点へ戻る（「最後に初期化した時点」以上の意味を持たない）
//   5. traveled_mm と since_reset_ms が単調増加する

using drivetrain_control::BodyVelocity;
using drivetrain_control::DurationMs;
using drivetrain_control::GeometryParams;
using drivetrain_control::Kinematics;
using drivetrain_control::Odometry;
using drivetrain_control::Pose2D;
using drivetrain_control::TimeMs;
using drivetrain_control::kWheelCount;

namespace {

constexpr float kPositionToleranceMm = 1.0f;   // 位置の許容差（mm）
constexpr float kAngleTolerance = 1.0e-3f;     // 角度の許容差（rad）

// 120° 等配置。値そのものはこのテストの合否条件ではない（本 Spec は数値
// パラメータの既定値を持たない、tasks.md ヘッダ注記）。往復・積分の性質を
// 検証するための固定形状として使う。
GeometryParams makeGeometry() {
  return GeometryParams::equilateral(/*base_radius_mm=*/100.0f, /*first_wheel_angle_rad=*/0.0f);
}

// 望む機体速度から、Kinematics::inverse() で輪速度を求めて返す小さな補助。
// Odometry の入力は「各輪の周速」であり、機体速度を直接与えられないため、
// 各テストで往復させる。
void wheelSpeedsFor(const Kinematics& kinematics, const BodyVelocity& body,
                     float wheel_mm_s[kWheelCount]) {
  kinematics.inverse(body, wheel_mm_s);
}

}  // namespace

void setUp(void) {}
void tearDown(void) {}

// ---------------------------------------------------------------------------
// 系統1: 直進（要件 8.2, 8.5）
// ---------------------------------------------------------------------------

void test_straight_line_advances_x_only(void) {
  const Kinematics kinematics(makeGeometry());
  Odometry odometry(kinematics);

  TimeMs now = 0;
  odometry.reset(Pose2D{}, now);

  const BodyVelocity desired{/*vx=*/150.0f, /*vy=*/0.0f, /*omega=*/0.0f};
  float wheel_mm_s[kWheelCount];
  wheelSpeedsFor(kinematics, desired, wheel_mm_s);

  // 10ms 刻みで100ステップ = 1.0s。制御周期の想定域（100〜200Hz、
  // research.md「中点法の Trade-offs」）の下限寄りで、直進なら中点法の
  // 誤差項（曲率に依存、要件 8.4 節参照）は理論上ゼロになるため、粗い刻み
  // でも許容差 1.0mm に収まる。
  constexpr DurationMs kDtMs = 10;
  constexpr int kSteps = 100;
  for (int i = 0; i < kSteps; ++i) {
    now += kDtMs;
    odometry.update(wheel_mm_s, kDtMs, now);
  }

  const auto& state = odometry.state();
  TEST_ASSERT_FLOAT_WITHIN(kPositionToleranceMm, 150.0f, state.pose.x_mm);  // vx * 1.0s
  TEST_ASSERT_FLOAT_WITHIN(kPositionToleranceMm, 0.0f, state.pose.y_mm);
  TEST_ASSERT_FLOAT_WITHIN(kAngleTolerance, 0.0f, state.pose.theta_rad);
  TEST_ASSERT_FLOAT_WITHIN(kPositionToleranceMm, 150.0f, state.traveled_mm);
}

// ---------------------------------------------------------------------------
// 系統2: その場旋回（要件 8.2, 8.5）— 位置は動かず姿勢だけ変化する
// ---------------------------------------------------------------------------

void test_in_place_rotation_changes_theta_only(void) {
  const Kinematics kinematics(makeGeometry());
  Odometry odometry(kinematics);

  TimeMs now = 0;
  odometry.reset(Pose2D{}, now);

  const BodyVelocity desired{/*vx=*/0.0f, /*vy=*/0.0f, /*omega=*/1.0f};
  float wheel_mm_s[kWheelCount];
  wheelSpeedsFor(kinematics, desired, wheel_mm_s);

  constexpr DurationMs kDtMs = 10;
  constexpr int kSteps = 100;  // 1.0s -> theta は 1.0 rad 進む（< pi なので正規化の折り返しは起きない）
  for (int i = 0; i < kSteps; ++i) {
    now += kDtMs;
    odometry.update(wheel_mm_s, kDtMs, now);
  }

  const auto& state = odometry.state();
  TEST_ASSERT_FLOAT_WITHIN(kPositionToleranceMm, 0.0f, state.pose.x_mm);
  TEST_ASSERT_FLOAT_WITHIN(kPositionToleranceMm, 0.0f, state.pose.y_mm);
  TEST_ASSERT_FLOAT_WITHIN(1.0e-2f, 1.0f, state.pose.theta_rad);

  // 純回転では機体座標系の変位（dx_body, dy_body）が常にゼロなので、
  // 走行距離は増えない（要件 8.6 の「累積走行距離」は並進のみに反応する）。
  TEST_ASSERT_FLOAT_WITHIN(kPositionToleranceMm, 0.0f, state.traveled_mm);
}

// ---------------------------------------------------------------------------
// 系統3: 一定回転速度の円軌道が1周で閉じる（中点法、要件 8.2）
// ---------------------------------------------------------------------------

void test_constant_curvature_circle_closes_after_one_revolution(void) {
  const Kinematics kinematics(makeGeometry());
  Odometry odometry(kinematics);

  TimeMs now = 1000;  // reset の時刻が0でないことも確認する（要件 8.3 の「外部操作」）
  const Pose2D start{/*x=*/500.0f, /*y=*/-200.0f, /*theta=*/0.5f};
  odometry.reset(start, now);

  // dt を先に固定し、"omega * dt * N == 2*pi" をちょうど満たす omega を
  // 逆算する（N ステップ後に累積 dtheta が浮動小数点誤差の範囲でぴったり
  // 2*pi になるようにするため）。100〜200Hz の制御周期を想定
  // （research.md「中点法の Trade-offs」）し、その下限に近い 200Hz
  // （dt=5ms）を選ぶ。刻みが細かいほど中点法の系統誤差
  // （O((omega*dt)^3) 、テスト作成時に導出）は小さくなるが、5ms 刻みでも
  // 円1周(約1256ステップ)の誤差はサブミリメートルに収まる計算であり、
  // 1.0mm の許容差は十分に余裕がある。
  constexpr DurationMs kDtMs = 5;
  const float dt_s = drivetrain_control::units::millisToSeconds(kDtMs);
  constexpr int kSteps = 1256;
  const float omega_rad_s = drivetrain_control::units::kTwoPi / (static_cast<float>(kSteps) * dt_s);

  // 半径 R = vx / omega。R = 120mm 相当になるよう vx を合わせる。
  constexpr float kRadiusMm = 120.0f;
  const float vx_mm_s = kRadiusMm * omega_rad_s;

  const BodyVelocity desired{/*vx=*/vx_mm_s, /*vy=*/0.0f, /*omega=*/omega_rad_s};
  float wheel_mm_s[kWheelCount];
  wheelSpeedsFor(kinematics, desired, wheel_mm_s);

  float traveled_before_last_step = 0.0f;
  for (int i = 0; i < kSteps; ++i) {
    now += kDtMs;
    if (i == kSteps - 1) {
      traveled_before_last_step = odometry.state().traveled_mm;
    }
    odometry.update(wheel_mm_s, kDtMs, now);
  }

  const auto& state = odometry.state();

  // 姿勢角がちょうど1周して出発時の姿勢へ戻る。
  TEST_ASSERT_FLOAT_WITHIN(1.0e-2f, start.theta_rad, state.pose.theta_rad);

  // 位置が出発点へ戻る（中点法は2次精度であり、有限刻み幅では厳密には
  // 一致しないため、ビット完全一致は期待しない）。
  TEST_ASSERT_FLOAT_WITHIN(kPositionToleranceMm, start.x_mm, state.pose.x_mm);
  TEST_ASSERT_FLOAT_WITHIN(kPositionToleranceMm, start.y_mm, state.pose.y_mm);

  // 走行距離は円周 2*pi*R に等しく、最終ステップでも増え続けている
  // （単調増加、系統5の一部としてもここで裏取りする）。
  const float circumference_mm = drivetrain_control::units::kTwoPi * kRadiusMm;
  TEST_ASSERT_FLOAT_WITHIN(kPositionToleranceMm, circumference_mm, state.traveled_mm);
  TEST_ASSERT_TRUE(state.traveled_mm > traveled_before_last_step);
}

// ---------------------------------------------------------------------------
// 系統4: reset() で原点へ戻る（要件 8.3, 8.7 — 「最後に初期化した時点」
// 以上の意味を持たない）
// ---------------------------------------------------------------------------

void test_reset_returns_to_the_given_origin(void) {
  const Kinematics kinematics(makeGeometry());
  Odometry odometry(kinematics);

  TimeMs now = 0;
  odometry.reset(Pose2D{}, now);

  // まず走らせて原点から離れさせる。
  const BodyVelocity desired{/*vx=*/80.0f, /*vy=*/40.0f, /*omega=*/0.3f};
  float wheel_mm_s[kWheelCount];
  wheelSpeedsFor(kinematics, desired, wheel_mm_s);
  for (int i = 0; i < 50; ++i) {
    now += 10;
    odometry.update(wheel_mm_s, 10, now);
  }
  TEST_ASSERT_TRUE(std::fabs(odometry.state().pose.x_mm) > 1.0f);  // 実際に動いたことを確認

  // 任意の原点・姿勢で再初期化する。「最後に reset() した時点」が新しい
  // 原点になるだけで、それ以前の走行や外部座標系との関係は一切残らない。
  const Pose2D new_origin{/*x=*/12.5f, /*y=*/-7.5f, /*theta=*/1.2f};
  now += 10;
  odometry.reset(new_origin, now);

  const auto& state = odometry.state();
  TEST_ASSERT_FLOAT_WITHIN(kAngleTolerance, new_origin.x_mm, state.pose.x_mm);
  TEST_ASSERT_FLOAT_WITHIN(kAngleTolerance, new_origin.y_mm, state.pose.y_mm);
  TEST_ASSERT_FLOAT_WITHIN(kAngleTolerance, new_origin.theta_rad, state.pose.theta_rad);
  TEST_ASSERT_FLOAT_WITHIN(kAngleTolerance, 0.0f, state.traveled_mm);
  TEST_ASSERT_EQUAL_INT32(0, state.since_reset_ms);

  // reset 直後に1ステップ進めると、since_reset_ms は「新しい reset 時刻」
  // からの経過として求まる（reset 前の経過は引き継がれない）。
  now += 10;
  odometry.update(wheel_mm_s, 10, now);
  TEST_ASSERT_EQUAL_INT32(10, odometry.state().since_reset_ms);
}

// ---------------------------------------------------------------------------
// 系統5: traveled_mm と since_reset_ms が単調増加する（要件 8.6）
// ---------------------------------------------------------------------------

void test_traveled_distance_and_elapsed_time_increase_monotonically(void) {
  const Kinematics kinematics(makeGeometry());
  Odometry odometry(kinematics);

  TimeMs now = 0;
  odometry.reset(Pose2D{}, now);

  const BodyVelocity desired{/*vx=*/60.0f, /*vy=*/-20.0f, /*omega=*/0.5f};
  float wheel_mm_s[kWheelCount];
  wheelSpeedsFor(kinematics, desired, wheel_mm_s);

  float previous_traveled_mm = odometry.state().traveled_mm;
  DurationMs previous_since_reset_ms = odometry.state().since_reset_ms;

  constexpr DurationMs kDtMs = 7;  // きりの良くない刻みでも単調性は崩れないことを確認する
  for (int i = 0; i < 30; ++i) {
    now += kDtMs;
    odometry.update(wheel_mm_s, kDtMs, now);

    const auto& state = odometry.state();
    TEST_ASSERT_TRUE(state.traveled_mm > previous_traveled_mm);
    TEST_ASSERT_TRUE(state.since_reset_ms > previous_since_reset_ms);

    previous_traveled_mm = state.traveled_mm;
    previous_since_reset_ms = state.since_reset_ms;
  }
}

int main(int argc, char **argv) {
  (void)argc;
  (void)argv;
  UNITY_BEGIN();

  RUN_TEST(test_straight_line_advances_x_only);
  RUN_TEST(test_in_place_rotation_changes_theta_only);
  RUN_TEST(test_constant_curvature_circle_closes_after_one_revolution);
  RUN_TEST(test_reset_returns_to_the_given_origin);
  RUN_TEST(test_traveled_distance_and_elapsed_time_increase_monotonically);

  return UNITY_END();
}
