#include "drivetrain_control/protection/supervisor.hpp"

#include <cmath>

// drivetrain_control ProtectionSupervisor implementation (design.md L7-L8
// 保護層 "ProtectionSupervisor", 要件 14.1〜14.10). See supervisor.hpp for
// the full contract and the resetProtections() safety-gating decision.

namespace drivetrain_control {

namespace {

// MotorLockDetector::update() (motor_lock.cpp) と同一のロック条件判定式。
// resetProtections() が「直近のロック条件が今も成立しているか」を検出器を
// 呼び直さずに判断するために、ProtectionSupervisor 側で独立に評価する
// （supervisor.hpp ファイル先頭の設計判断コメント参照）。
bool lockConditionHolds(const LockParams& params, float commanded_duty, float measured_mm_s) noexcept {
  return std::fabs(commanded_duty) >= params.duty_threshold &&
         std::fabs(measured_mm_s) <= params.speed_threshold_mm_s;
}

}  // namespace

ProtectionSupervisor::ProtectionSupervisor(const DrivetrainConfig& config) noexcept
    : lock_params_(config.lock),
      low_voltage_params_(config.low_voltage),
      motor_lock_{MotorLockDetector(config.lock), MotorLockDetector(config.lock), MotorLockDetector(config.lock)},
      low_voltage_(config.low_voltage),
      watchdog_(config.watchdog) {}

LowVoltageState ProtectionSupervisor::updateLowVoltage(const VoltageSample& battery, TimeMs now) noexcept {
  // 直近のサンプル有効性をキャッシュする（resetProtections() の
  // Unavailable 復帰判定専用。supervisor.hpp 参照）。
  last_voltage_sample_valid_ = battery.valid;
  return low_voltage_.update(battery, now);
}

bool ProtectionSupervisor::updateWatchdog(TimeMs now, TimeMs last_command_at_ms, bool has_command) noexcept {
  return watchdog_.update(now, last_command_at_ms, has_command);
}

bool ProtectionSupervisor::updateLock(std::uint8_t wheel, float commanded_duty, float measured_mm_s,
                                       TimeMs now) noexcept {
  // 直近のロック条件をキャッシュする（resetProtections() 専用。
  // supervisor.hpp 参照）。輪番号は呼び出し側（制御ステップ）が 0..2 の
  // 範囲で渡す契約であり、本クラスは範囲外添字を検査しない
  // （MotorLockDetector と同じくホットパスの純粋なロジックであるため）。
  last_lock_condition_[wheel] = lockConditionHolds(lock_params_, commanded_duty, measured_mm_s);
  return motor_lock_[wheel].update(commanded_duty, measured_mm_s, now);
}

GateOutcome ProtectionSupervisor::compose(bool configured, bool output_enabled, bool has_command) const noexcept {
  GateOutcome outcome;

  // 機体全体の理由（要件 14.9, 14.10 を含む。design.md Postconditions の
  // ビット分類のとおり、kMotorLock 以外はすべて機体全体の理由）。
  if (!configured) {
    outcome.global_reasons |= static_cast<BlockMask>(BlockReason::kNotConfigured);
  }
  if (!output_enabled) {
    outcome.global_reasons |= static_cast<BlockMask>(BlockReason::kOutputDisabled);
  }
  if (!has_command) {
    outcome.global_reasons |= static_cast<BlockMask>(BlockReason::kNoCommandYet);
  }
  // watchdog_.tripped() / low_voltage_.state() は読み取り専用アクセサで
  // あり、直近の updateWatchdog() / updateLowVoltage() 呼び出しの結果を
  // 読むだけである（検出器を呼び直さない。supervisor.hpp Preconditions
  // 参照）。
  if (watchdog_.tripped()) {
    outcome.global_reasons |= static_cast<BlockMask>(BlockReason::kCommandTimeout);
  }
  switch (low_voltage_.state()) {
    case LowVoltageState::kTripped:
      outcome.global_reasons |= static_cast<BlockMask>(BlockReason::kLowVoltage);
      break;
    case LowVoltageState::kUnavailable:
      outcome.global_reasons |= static_cast<BlockMask>(BlockReason::kVoltageUnavailable);
      break;
    case LowVoltageState::kNormal:
    case LowVoltageState::kWarning:
      break;
  }

  // 輪ごとの理由: モータロックのみ（要件 14.4）。同じく tripped() は
  // 読み取り専用アクセサ。
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    if (motor_lock_[i].tripped()) {
      outcome.wheel_reasons[i] |= static_cast<BlockMask>(BlockReason::kMotorLock);
    }
  }

  return outcome;
}

void ProtectionSupervisor::resetProtections(TimeMs now) noexcept {
  // --- MotorLockDetector（保護①） ---------------------------------------
  // 「保持設定の保護のうち」（design.md Postconditions）＝
  // latching == true の構成だけが対象。latching == false は検出器自身が
  // clear_duration_ms 経過後に自己解除するため、ここで扱う必要が無い
  // （supervisor.hpp ファイル先頭の設計判断コメント参照）。
  if (lock_params_.latching) {
    for (std::uint8_t i = 0; i < kWheelCount; ++i) {
      // tripped() が偽なら（Idle/Suspect）解除するものが無い。
      // tripped() が真でも、直近の updateLock() で観測したロック条件が
      // まだ成立している輪（last_lock_condition_[i] == true）は「条件が
      // 継続している保護」であり解けない（要件 14.6 後半）。
      if (motor_lock_[i].tripped() && !last_lock_condition_[i]) {
        motor_lock_[i].reset(now);
      }
    }
  }

  // --- LowVoltageProtector（保護②） --------------------------------------
  // 同様に「保持設定の保護のうち」＝ latching == true の構成だけが対象。
  // latching == false の Tripped は update() 自身が復帰条件成立時点で
  // 既に Normal へ自己遷移しているため、ここに到達する時点で state() が
  // kTripped のままなのは latching == true か、条件がまだ継続している
  // 場合に限られる。
  const LowVoltageState lv_state = low_voltage_.state();
  if (lv_state == LowVoltageState::kTripped) {
    if (low_voltage_params_.latching && low_voltage_.voltage_valid() &&
        low_voltage_.averaged_milli_volts() >= low_voltage_params_.recover_milli_volts) {
      low_voltage_.reset(now);
    }
  } else if (lv_state == LowVoltageState::kUnavailable) {
    // Unavailable からの復帰条件（design.md の状態遷移図）に latching の
    // 区別は無い ―― low_voltage.cpp の Unavailable 節は latching を一切
    // 見ずに「有効な読み値の復帰」かつ「移動平均が recover_milli_volts
    // 以上」だけで自己遷移する。そのため通常は resetProtections() が
    // 呼ばれる時点で既に自己解除済みのはずだが、resetProtections() が
    // step() と独立した操作者リセット操作として呼ばれる運用（今回の
    // update 呼び出しを経ずに呼ばれる場合）を考慮し、直近の
    // updateLowVoltage() で渡されたサンプルの有効性もあわせて条件に含め、
    // 「読み値が復帰していない・移動平均がまだ足りない」間は解除しない
    // 安全側の判定にする。
    if (last_voltage_sample_valid_ && low_voltage_.voltage_valid() &&
        low_voltage_.averaged_milli_volts() >= low_voltage_params_.recover_milli_volts) {
      low_voltage_.reset(now);
    }
  }
  // kNormal / kWarning: 保持している遮断理由が無いため何もしない。

  // --- CommandWatchdog（保護④） -------------------------------------------
  // reset() を持たない（task 5.4 で確定済み）。has_command /
  // last_command_at_ms の新しい入力だけで自己解除するため、ここで扱う
  // べきものが無い（supervisor.hpp ファイル先頭コメント参照）。
}

void ProtectionSupervisor::applyGate(const GateOutcome& outcome, WheelOutputs& outputs) noexcept {
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    if (outcome.global_reasons != 0 || outcome.wheel_reasons[i] != 0) {
      outputs.duty[i] = 0.0f;
    }
  }
}

}  // namespace drivetrain_control
