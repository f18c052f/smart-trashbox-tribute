#include "drivetrain_control/kinematics.hpp"

#include <cmath>

// drivetrain_control Kinematics implementation (design.md L5 "Kinematics",
// 要件 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 8.1, 8.4). See kinematics.hpp for the
// full contract, preconditions, and the row-definition rationale.

namespace drivetrain_control {

Kinematics::Kinematics(const GeometryParams& geometry) noexcept : determinant_(0.0f) {
  // sin/cos の評価はここ1回きり（要件 6.1 コメント、design.md Invariants）。
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    matrix_[i][0] = -std::sin(geometry.wheel_angle_rad[i]);
    matrix_[i][1] = std::cos(geometry.wheel_angle_rad[i]);
    matrix_[i][2] = geometry.base_radius_mm;
  }

  const float a = matrix_[0][0];
  const float b = matrix_[0][1];
  const float c = matrix_[0][2];
  const float d = matrix_[1][0];
  const float e = matrix_[1][1];
  const float f = matrix_[1][2];
  const float g = matrix_[2][0];
  const float h = matrix_[2][1];
  const float i = matrix_[2][2];

  determinant_ = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g);

  // 3x3 の厳密な逆行列を余因子（アジュゲート）法で1度だけ組み立てる
  // （Preconditions: |determinant_| > ε は config.cpp の validate() が
  // 既に保証済み。ここでは再検証しない）。
  const float inv_det = 1.0f / determinant_;

  inverse_matrix_[0][0] = (e * i - f * h) * inv_det;
  inverse_matrix_[1][0] = -(d * i - f * g) * inv_det;
  inverse_matrix_[2][0] = (d * h - e * g) * inv_det;

  inverse_matrix_[0][1] = -(b * i - c * h) * inv_det;
  inverse_matrix_[1][1] = (a * i - c * g) * inv_det;
  inverse_matrix_[2][1] = -(a * h - b * g) * inv_det;

  inverse_matrix_[0][2] = (b * f - c * e) * inv_det;
  inverse_matrix_[1][2] = -(a * f - c * d) * inv_det;
  inverse_matrix_[2][2] = (a * e - b * d) * inv_det;
}

void Kinematics::inverse(const BodyVelocity& body, float wheel_mm_s[kWheelCount]) const noexcept {
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    wheel_mm_s[i] = matrix_[i][0] * body.vx_mm_s + matrix_[i][1] * body.vy_mm_s +
                    matrix_[i][2] * body.omega_rad_s;
  }
}

BodyVelocity Kinematics::forward(const float wheel_mm_s[kWheelCount]) const noexcept {
  BodyVelocity body{};
  body.vx_mm_s = inverse_matrix_[0][0] * wheel_mm_s[0] + inverse_matrix_[0][1] * wheel_mm_s[1] +
                 inverse_matrix_[0][2] * wheel_mm_s[2];
  body.vy_mm_s = inverse_matrix_[1][0] * wheel_mm_s[0] + inverse_matrix_[1][1] * wheel_mm_s[1] +
                 inverse_matrix_[1][2] * wheel_mm_s[2];
  body.omega_rad_s = inverse_matrix_[2][0] * wheel_mm_s[0] + inverse_matrix_[2][1] * wheel_mm_s[1] +
                      inverse_matrix_[2][2] * wheel_mm_s[2];
  return body;
}

float Kinematics::determinant() const noexcept { return determinant_; }

float scaleToLimit(float values[kWheelCount], float limit) noexcept {
  float max_abs = 0.0f;
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    const float abs_value = std::fabs(values[i]);
    if (abs_value > max_abs) {
      max_abs = abs_value;
    }
  }

  if (max_abs <= limit || max_abs <= 0.0f) {
    return 1.0f;  // 既に範囲内、または全要素が 0（縮小の余地が無い）
  }

  const float scale = limit / max_abs;
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    values[i] *= scale;
  }
  return scale;
}

}  // namespace drivetrain_control
