#include "drivetrain_control/odometry.hpp"

#include <cmath>

#include "drivetrain_control/units.hpp"

// drivetrain_control Odometry implementation (design.md L6 "Odometry",
// 要件 8.2, 8.3, 8.5, 8.6, 8.7). See odometry.hpp for the full contract,
// preconditions, and the midpoint-integration rationale.

namespace drivetrain_control {

Odometry::Odometry(const Kinematics& kinematics) noexcept : kinematics_(kinematics) {}

void Odometry::reset(const Pose2D& pose, TimeMs now) noexcept {
  state_.pose = pose;
  state_.body_velocity = BodyVelocity{};
  state_.traveled_mm = 0.0f;
  state_.since_reset_ms = 0;
  reset_at_ms_ = now;
}

void Odometry::update(const float wheel_mm_s[kWheelCount], DurationMs dt_ms, TimeMs now) noexcept {
  // 1. 各輪の周速から機体速度を求める（要件 8.1、Kinematics::forward()）。
  state_.body_velocity = kinematics_.forward(wheel_mm_s);

  // 2. 機体座標系の変位（1ステップぶん）。
  const float dt_s = units::millisToSeconds(dt_ms);
  const float dx_body = state_.body_velocity.vx_mm_s * dt_s;
  const float dy_body = state_.body_velocity.vy_mm_s * dt_s;
  const float dtheta = state_.body_velocity.omega_rad_s * dt_s;

  // 3. 中点法: 積分前後の姿勢角の中点 theta + dtheta/2 で世界座標系へ
  //    回転させる（design.md Postconditions、research.md「Decision:
  //    オドメトリの積分は中点法」）。
  const float theta_mid = state_.pose.theta_rad + dtheta * 0.5f;
  const float cos_mid = std::cos(theta_mid);
  const float sin_mid = std::sin(theta_mid);
  const float dx_world = dx_body * cos_mid - dy_body * sin_mid;
  const float dy_world = dx_body * sin_mid + dy_body * cos_mid;

  // 4. 位置・姿勢を積む。姿勢角は (-π, +π] へ正規化する（重複実装せず
  //    units::wrapAngle() を再利用する）。
  state_.pose.x_mm += dx_world;
  state_.pose.y_mm += dy_world;
  state_.pose.theta_rad = units::wrapAngle(state_.pose.theta_rad + dtheta);

  // 5. 累積走行距離。回転は世界座標系への回転で長さを変えないため、
  //    機体座標系の変位から求めても世界座標系の変位から求めても同じ大きさ
  //    になる（回転は長さを保存する）。ここでは回転前の値を使う。
  state_.traveled_mm += std::sqrt(dx_body * dx_body + dy_body * dy_body);

  // 6. 初期化時点からの経過時間。reset() が記録した時刻からの実時刻差
  //    として求め直す（dt_ms を逐次積算しない。丸め誤差が積算で育たず、
  //    タスク 6.2 の「経過時間を実際の時刻差から求める」方針と一貫する。
  //    → odometry.hpp コメント参照）。
  state_.since_reset_ms = static_cast<DurationMs>(now - reset_at_ms_);
}

const OdometryState& Odometry::state() const noexcept { return state_; }

}  // namespace drivetrain_control
