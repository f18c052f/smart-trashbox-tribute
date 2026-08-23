#include "drivetrain_control/protection/command_watchdog.hpp"

// drivetrain_control CommandWatchdog implementation (design.md L7-L8
// 保護層 "CommandWatchdog", 要件 13.1, 13.2, 13.3, 13.4, 13.7, 13.8). See
// command_watchdog.hpp for the full contract.

namespace drivetrain_control {

CommandWatchdog::CommandWatchdog(const WatchdogParams& params) noexcept : params_(params) {}

bool CommandWatchdog::update(TimeMs now, TimeMs last_command_at_ms, bool has_command) noexcept {
  // design.md Postconditions: 非発火となるのは
  // has_command && (now - last_command_at_ms) <= timeout_ms のときのみ。
  // それ以外（一度も有効な指令を受けていない、またはタイムアウトを
  // 厳密に超えた）は常に発火中。
  const TimeMs elapsed = now - last_command_at_ms;
  tripped_ = !(has_command && elapsed <= static_cast<TimeMs>(params_.timeout_ms));
  return tripped_;
}

bool CommandWatchdog::tripped() const noexcept { return tripped_; }

}  // namespace drivetrain_control
