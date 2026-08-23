#include "drivetrain_control/command_input.hpp"

#include <cmath>

// drivetrain_control CommandInput implementation (design.md L9
// "CommandInput", 要件 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.9, 6.5,
// 17.6). See command_input.hpp for the full contract, the past-time
// rejection boundary (`<=`), and why the two submit() overloads converge to
// the same WheelTargets.

namespace drivetrain_control {

CommandInput::CommandInput(const Kinematics& kinematics, const MotionLimits& limits) noexcept
    : kinematics_(kinematics), limits_(limits) {}

bool CommandInput::isStale(TimeMs issued_at_ms) const noexcept {
  // 起動後まだ一度も有効指令が無ければ比較対象が存在しないため、常に
  // 「過去ではない」（= 最初の指令は無条件で受理する）。
  if (!latched_.valid) {
    return false;
  }
  // <=: 同一時刻での上書きも拒否する（command_input.hpp 冒頭のコメント
  // 参照）。過去時刻の指令でウォッチドッグを欺けないようにするため。
  return issued_at_ms <= latched_.issued_at_ms;
}

CommandAcceptance CommandInput::submit(const BodyVelocityCommand& command) noexcept {
  if (isStale(command.issued_at_ms)) {
    return CommandAcceptance{/*accepted=*/false, /*clamped=*/false};
  }

  float vx = command.vx_mm_s;
  float vy = command.vy_mm_s;
  float omega = command.omega_rad_s;
  bool body_clamped = false;

  // 並進2成分の大きさを max_body_speed_mm_s へクランプする（方向は保つ）。
  const float body_speed = std::sqrt(vx * vx + vy * vy);
  if (body_speed > limits_.max_body_speed_mm_s && body_speed > 0.0f) {
    const float scale = limits_.max_body_speed_mm_s / body_speed;
    vx *= scale;
    vy *= scale;
    body_clamped = true;
  }

  // 回転速度を max_body_omega_rad_s へ独立にクランプする。
  if (omega > limits_.max_body_omega_rad_s) {
    omega = limits_.max_body_omega_rad_s;
    body_clamped = true;
  } else if (omega < -limits_.max_body_omega_rad_s) {
    omega = -limits_.max_body_omega_rad_s;
    body_clamped = true;
  }

  // クランプ後の機体速度で逆運動学を通す。
  const BodyVelocity clamped_body{vx, vy, omega};
  float wheel_mm_s[kWheelCount];
  kinematics_.inverse(clamped_body, wheel_mm_s);

  // 輪速度上限を超える場合は方向を保ったまま全輪を等比縮小する（要件 6.5）。
  const float scale = scaleToLimit(wheel_mm_s, limits_.max_wheel_speed_mm_s);
  const bool wheel_scaled = (scale != 1.0f);

  const bool clamped = body_clamped || wheel_scaled;

  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    latched_.mm_s[i] = wheel_mm_s[i];
  }
  latched_.issued_at_ms = command.issued_at_ms;
  latched_.valid = true;
  last_command_clamped_ = clamped;

  return CommandAcceptance{/*accepted=*/true, /*clamped=*/clamped};
}

CommandAcceptance CommandInput::submit(const WheelVelocityCommand& command) noexcept {
  if (isStale(command.issued_at_ms)) {
    return CommandAcceptance{/*accepted=*/false, /*clamped=*/false};
  }

  // 逆運動学を経由しない。各輪を個別に飽和させる（等比縮小ではない）。
  // 1輪だけ回す用途には保存すべき運動方向が存在しないため。
  bool clamped = false;
  float wheel_mm_s[kWheelCount];
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    float v = command.wheel_mm_s[i];
    if (v > limits_.max_wheel_speed_mm_s) {
      v = limits_.max_wheel_speed_mm_s;
      clamped = true;
    } else if (v < -limits_.max_wheel_speed_mm_s) {
      v = -limits_.max_wheel_speed_mm_s;
      clamped = true;
    }
    wheel_mm_s[i] = v;
  }

  // どちらの入口も同じ WheelTargets へ落とす（要件 5.2, 5.9）。
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    latched_.mm_s[i] = wheel_mm_s[i];
  }
  latched_.issued_at_ms = command.issued_at_ms;
  latched_.valid = true;
  last_command_clamped_ = clamped;

  return CommandAcceptance{/*accepted=*/true, /*clamped=*/clamped};
}

void CommandInput::setOutputEnabled(bool enabled, TimeMs now) noexcept {
  // `now` は他の状態変更入口（design.md「状態を変えるすべての公開入口が
  // 時刻を引数に取る」）との一貫性のために受け取るが、CommandInput 自身は
  // 出力許可の時刻を保持・利用しない（要件 5.7 は「指令とは別の入口」を
  // 求めるのみで、許可時刻の記録までは求めていない）。指令の投入とは完全に
  // 独立しており、latched() を一切変更しない（要件 5.6）。
  (void)now;
  output_enabled_ = enabled;
}

}  // namespace drivetrain_control
