#include "drivetrain_control/protection/motor_lock.hpp"

#include <cmath>

// drivetrain_control MotorLockDetector implementation (design.md L7-L8
// 保護層 "MotorLockDetector", 要件 10.1〜10.7). See motor_lock.hpp for the
// full contract, the ⚠️ judgement-input note, and the reset() semantics
// decision.

namespace drivetrain_control {

MotorLockDetector::MotorLockDetector(const LockParams& params) noexcept : params_(params) {}

bool MotorLockDetector::update(float commanded_duty, float measured_mm_s, TimeMs now) noexcept {
  // ロック条件（要件 10.1, 10.2）: 出力指令が閾値以上、かつ計測速度が
  // 閾値以下。commanded_duty は呼び出し側が「上限適用の後・遮断の前」の
  // 値を渡す契約（motor_lock.hpp の ⚠️ 注記）であり、このクラスは絶対値の
  // 比較しか行わない。
  const bool lock_condition = std::fabs(commanded_duty) >= params_.duty_threshold &&
                               std::fabs(measured_mm_s) <= params_.speed_threshold_mm_s;

  switch (state_) {
    case State::kIdle:
      if (lock_condition) {
        state_ = State::kSuspect;
        suspect_started_at_ms_ = now;
      }
      break;

    case State::kSuspect:
      if (!lock_condition) {
        // 条件が崩れた: 継続時間の計測をリセットする（要件 10.4）。
        // 一過性の速度低下ではここへ戻るだけで発火しない。
        state_ = State::kIdle;
      } else if (now - suspect_started_at_ms_ > static_cast<TimeMs>(params_.duration_ms)) {
        // 継続時間を厳密に超えたら発火する（design.md の CommandWatchdog
        // と同じ「厳密に超える」規約。motor_lock.hpp 先頭コメント参照）。
        state_ = State::kTripped;
        clear_timer_active_ = false;
      }
      break;

    case State::kTripped:
      if (params_.latching) {
        // 手動リセット設定: reset() が呼ばれるまで維持する（要件 10.5）。
        break;
      }
      // 自動復帰設定: 条件が clear_duration_ms だけ崩れ続けたら Idle へ
      // 戻る（要件 10.5）。
      if (lock_condition) {
        // 条件が復活した: 解除継続時間の計測を打ち切る。
        clear_timer_active_ = false;
      } else if (!clear_timer_active_) {
        clear_timer_active_ = true;
        clear_started_at_ms_ = now;
      } else if (now - clear_started_at_ms_ > static_cast<TimeMs>(params_.clear_duration_ms)) {
        state_ = State::kIdle;
        clear_timer_active_ = false;
      }
      break;
  }

  return state_ == State::kTripped;
}

bool MotorLockDetector::tripped() const noexcept { return state_ == State::kTripped; }

void MotorLockDetector::reset(TimeMs now) noexcept {
  // 無条件に Idle へ戻す（motor_lock.hpp 先頭の reset() 意味論の説明を
  // 参照）。`now` を Suspect の起点として書き戻しておくことで、内部状態が
  // 常に「意味のある時刻」を指すようにする（Idle では実際には参照され
  // ないが、診断のために不定値を残さない）。
  state_ = State::kIdle;
  suspect_started_at_ms_ = now;
  clear_timer_active_ = false;
  clear_started_at_ms_ = now;
}

}  // namespace drivetrain_control
