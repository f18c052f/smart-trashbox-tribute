#include "drivetrain_control/velocity_pid.hpp"

#include <cmath>

#include "drivetrain_control/units.hpp"

// drivetrain_control VelocityPid implementation (design.md L6
// "VelocityPid", 要件 9.1, 9.2, 9.3, 9.4, 9.5, 9.6). See velocity_pid.hpp
// for the full contract, preconditions, and the compute()/commit() 2段
// プロトコルの設計判断。

namespace drivetrain_control {

VelocityPid::VelocityPid(const PidParams& params) noexcept : params_(params) {}

float VelocityPid::compute(float target_mm_s, float measured_mm_s, DurationMs dt_ms) noexcept {
  const float dt_s = units::millisToSeconds(dt_ms);
  const float error = target_mm_s - measured_mm_s;

  // P・I は偏差に掛かる（要件 9.1）。
  const float p_term = params_.kp * error;

  // 積分は「このステップの増分」をまず [-integral_limit, +integral_limit]
  // へクランプしたうえで暫定的に積む（要件 9.6）。クランプ後に実際へ
  // integral_ へ足された量（naive な ki*error*dt_s とは、上限へ張り付いた
  // ときに異なり得る）を pending_integral_increment_ として覚えておき、
  // 直後の commit() が「このステップぶんだけ」を正確に取り消せるようにする
  // （要件 9.4 の条件付き積分の土台）。
  const float naive_integral = integral_ + params_.ki * error * dt_s;
  const float clamped_integral =
      naive_integral > params_.integral_limit    ? params_.integral_limit
      : naive_integral < -params_.integral_limit ? -params_.integral_limit
                                                   : naive_integral;
  pending_integral_increment_ = clamped_integral - integral_;
  integral_ = clamped_integral;

  // D は目標値ではなく「計測値の変化」に掛かる（設定値変更時のキックを
  // 避けるため。要件 9.1）。負号は、計測値が急に上昇/下降したときに出力を
  // 抑制側へ動かす標準的な「measurement derivative」の符号選択であり、この
  // Spec の性能値ではなく実装上の設計判断である（velocity_pid.hpp 参照）。
  const float d_term = -params_.kd * (measured_mm_s - last_measured_mm_s_) / dt_s;
  last_measured_mm_s_ = measured_mm_s;

  const float raw = p_term + clamped_integral + d_term;

  // commit() が「適用値の大きさ」「生の出力の大きさ」「偏差の飽和方向」を
  // 比較できるよう、このステップの生の出力と偏差を覚えておく。
  pending_raw_output_ = raw;
  pending_error_ = error;

  return raw;
}

void VelocityPid::commit(float applied_duty) noexcept {
  // 条件付き積分（要件 9.4）: 実際に適用された値の大きさが直前の compute()
  // の生の出力の大きさより小さく（= 群としての等比縮小や遮断で「実現され
  // なかった分」がある）、かつ偏差が飽和方向（生の出力と同符号 = 積分を
  // このまま伸ばしても、まだ実現できていない同方向へさらに伸ばすだけ）を
  // 向いているときだけ、このステップで積んだ積分増分を取り消す。
  //
  // 「大きさ」で比較する（符号付きの単純な `applied_duty < pending_raw_`
  // ではない）のは、生の出力が負の場合にも同じロジックが対称に効くように
  // するため（例: raw = -0.9 を縮小して applied = -0.5 にした場合、
  // |applied| < |raw| は真だが、符号付きの `applied < raw` は偽になり、
  // 負方向の飽和を取り逃してしまう）。
  const bool applied_smaller_in_magnitude = std::fabs(applied_duty) < std::fabs(pending_raw_output_);
  const bool error_toward_saturation_direction =
      (pending_error_ > 0.0f && pending_raw_output_ > 0.0f) ||
      (pending_error_ < 0.0f && pending_raw_output_ < 0.0f);

  if (applied_smaller_in_magnitude && error_toward_saturation_direction) {
    integral_ -= pending_integral_increment_;
  }

  pending_integral_increment_ = 0.0f;
}

void VelocityPid::holdReset(float measured_mm_s) noexcept {
  // 積分を 0 に保つ（要件 9.5）。
  integral_ = 0.0f;
  pending_integral_increment_ = 0.0f;
  pending_raw_output_ = 0.0f;
  pending_error_ = 0.0f;

  // 微分の基準を現在の計測値へ追従させる。これにより、遮断解除後の最初の
  // compute() が「遮断中に古いままだった計測値との差分」から過大な微分
  // キックを起こさない（要件 9.5）。
  last_measured_mm_s_ = measured_mm_s;
}

}  // namespace drivetrain_control
