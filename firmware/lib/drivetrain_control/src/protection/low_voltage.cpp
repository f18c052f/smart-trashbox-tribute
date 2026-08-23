#include "drivetrain_control/protection/low_voltage.hpp"

// drivetrain_control LowVoltageProtector implementation (design.md L7-L8
// 保護層 "LowVoltageProtector", 要件 11.1〜11.5, 11.8). See low_voltage.hpp
// for the full contract, the state-diagram-to-code mapping, the
// unavailable-vs-low-voltage priority decision, and the reset() semantics
// decision.

namespace drivetrain_control {

LowVoltageProtector::LowVoltageProtector(const LowVoltageParams& params) noexcept
    : params_(params) {}

void LowVoltageProtector::pushValidSample(std::int32_t milli_volts) noexcept {
  // 固定長リングバッファへの追加。容量は params_.average_window
  // (<= kMaxVoltageWindow、設定検証で保証)。動的メモリ確保は行わない。
  if (count_ < params_.average_window) {
    buffer_[next_index_] = milli_volts;
    sum_ += static_cast<std::int64_t>(milli_volts);
    ++count_;
  } else {
    // 満杯: 最古のサンプル（次の書き込み位置に残っている値）を追い出す。
    sum_ -= static_cast<std::int64_t>(buffer_[next_index_]);
    buffer_[next_index_] = milli_volts;
    sum_ += static_cast<std::int64_t>(milli_volts);
  }
  next_index_ = static_cast<std::uint8_t>((next_index_ + 1) % params_.average_window);
}

LowVoltageState LowVoltageProtector::update(const VoltageSample& sample, TimeMs now) noexcept {
  // 1) 移動平均へ反映する。欠測サンプルは取り込まない（要件 11.8）。
  if (sample.valid) {
    pushValidSample(sample.milli_volts);
  }

  // 2) 欠測（valid == false）の継続時間を追跡する。
  bool unavailable_reached = false;
  if (!sample.valid) {
    if (!unavailable_timer_active_) {
      unavailable_timer_active_ = true;
      unavailable_started_at_ms_ = now;
    } else if (now - unavailable_started_at_ms_ > static_cast<TimeMs>(params_.unavailable_duration_ms)) {
      unavailable_reached = true;
    }
  } else {
    // 有効なサンプルが戻ったら、欠測の継続時間の計測はリセットする。
    unavailable_timer_active_ = false;
  }

  // 3) 移動平均が停止閾値未満の継続時間を追跡する。移動平均が1件も
  //    無い（count_ == 0）間は、既定値 0mV を判定に使わない
  //    （low_voltage.hpp 先頭の設計判断コメント参照）。
  bool stop_reached = false;
  const bool has_average = count_ > 0;
  if (has_average && averaged_milli_volts() < params_.stop_milli_volts) {
    if (!low_voltage_timer_active_) {
      low_voltage_timer_active_ = true;
      low_voltage_started_at_ms_ = now;
    } else if (now - low_voltage_started_at_ms_ > static_cast<TimeMs>(params_.stop_duration_ms)) {
      stop_reached = true;
    }
  } else {
    low_voltage_timer_active_ = false;
  }

  // 4) 状態遷移（design.md "保護② LiPo 低電圧の状態遷移"）。
  switch (state_) {
    case LowVoltageState::kNormal:
      if (unavailable_reached) {
        // 欠測の継続 -> Unavailable。低電圧の継続時間が同時に成立して
        // いても、欠測を優先する（low_voltage.hpp 先頭コメント参照。
        // 要件 11.8「低電圧とは別の理由として停止する」）。
        state_ = LowVoltageState::kUnavailable;
      } else if (stop_reached) {
        state_ = LowVoltageState::kTripped;
      } else if (has_average && averaged_milli_volts() < params_.warn_milli_volts) {
        state_ = LowVoltageState::kWarning;
      }
      break;

    case LowVoltageState::kWarning:
      if (unavailable_reached) {
        state_ = LowVoltageState::kUnavailable;
      } else if (stop_reached) {
        state_ = LowVoltageState::kTripped;
      } else if (has_average && averaged_milli_volts() >= params_.warn_milli_volts) {
        // 警告閾値以上へ即座に復帰する（design.md "Warning --> Normal"）。
        state_ = LowVoltageState::kNormal;
      }
      break;

    case LowVoltageState::kTripped:
      // design.md の state diagram に Tripped --> Unavailable の矢印は
      // 無い。欠測が続いても Tripped のまま（low_voltage.hpp 先頭の
      // ⚠️ 注記を参照）。復帰は latching == false かつ移動平均が
      // recover_milli_volts 以上のときのみ（要件 11.4 のヒステリシス）。
      if (!params_.latching && has_average && averaged_milli_volts() >= params_.recover_milli_volts) {
        state_ = LowVoltageState::kNormal;
      }
      break;

    case LowVoltageState::kUnavailable:
      // 復帰は「有効な読み値の復帰」かつ「移動平均が recover_milli_volts
      // 以上」の両方が必要。design.md の矢印に latching の条件は付いて
      // いないため、latching の値に関わらず無条件で判定する
      // （Tripped の復帰条件との違い。low_voltage.hpp 先頭コメント参照）。
      if (sample.valid && has_average && averaged_milli_volts() >= params_.recover_milli_volts) {
        state_ = LowVoltageState::kNormal;
      }
      break;
  }

  return state_;
}

LowVoltageState LowVoltageProtector::state() const noexcept { return state_; }

std::int32_t LowVoltageProtector::averaged_milli_volts() const noexcept {
  if (count_ == 0) {
    return 0;
  }
  // std::int64_t の合計を std::int32_t の件数で割る整数除算。C++ の
  // 整数除算は 0 方向への切り捨てで処理系非依存（[expr.mul]）。
  // 浮動小数点を経由しないため、丸めがプラットフォームに依存しない
  // （要件 11.3, design.md "LowVoltageProtector" Invariants）。
  return static_cast<std::int32_t>(sum_ / static_cast<std::int64_t>(count_));
}

bool LowVoltageProtector::voltage_valid() const noexcept { return count_ > 0; }

void LowVoltageProtector::reset(TimeMs now) noexcept {
  // 無条件に Normal へ戻す（low_voltage.hpp 先頭の reset() 意味論の
  // 説明を参照。Tripped からも Unavailable からも解除する。ゲーティング
  // は呼び出し側 [ProtectionSupervisor::resetProtections()、タスク 5.5]
  // の責務）。移動平均の蓄積内容（buffer_ / sum_ / count_）はセンサの
  // 実測履歴であり、保護状態ではないためクリアしない。
  state_ = LowVoltageState::kNormal;
  low_voltage_timer_active_ = false;
  low_voltage_started_at_ms_ = now;
  unavailable_timer_active_ = false;
  unavailable_started_at_ms_ = now;
}

}  // namespace drivetrain_control
