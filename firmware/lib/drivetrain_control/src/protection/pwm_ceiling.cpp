#include "drivetrain_control/protection/pwm_ceiling.hpp"

// drivetrain_control PwmCeiling implementation (design.md L7-L8 保護層
// "PwmCeiling", 要件 12.1, 12.2, 12.3, 12.5, 12.6). See pwm_ceiling.hpp for
// the full contract, the evaluate() priority order, and the default-formula
// units / zero-division-guard design decisions.

namespace drivetrain_control {

namespace {

float clamp(float value, float lo, float hi) noexcept {
  if (value < lo) return lo;
  if (value > hi) return hi;
  return value;
}

}  // namespace

PwmCeiling::PwmCeiling(const PwmCeilingParams& params, float absolute_max_duty) noexcept
    : params_(params), absolute_max_duty_(absolute_max_duty) {}

float PwmCeiling::evaluate(const VoltageSample& sample) const noexcept {
  // 1) 無効化（要件 12.5）: enabled == false のときは欠測の有無に関わらず
  //    常に absolute_max_duty を返す（pwm_ceiling.hpp 優先順位の説明を
  //    参照。要件 12.5 の文言に条件が付いていないことを最優先の根拠と
  //    する）。
  if (!params_.enabled) {
    return absolute_max_duty_;
  }

  // 2) 欠測（要件 12.6）: 読み値が得られない、または電圧センサが物理的に
  //    あり得ない値（0 以下）を返した場合は、上限を無制限とせず外部から
  //    与えられた安全側の既定上限（fallback_duty）を適用する
  //    （pwm_ceiling.hpp のゼロ割りガードの説明を参照）。
  if (!sample.valid || sample.milli_volts <= 0) {
    return params_.fallback_duty;
  }

  // 3) 差し替え（要件 12.5）: override_fn が非 null ならその戻り値を
  //    [0, absolute_max_duty] にクランプして返す。差し替え式の戻り値が
  //    定義域外（負値や absolute_max_duty 超過）であっても、常に定義域内
  //    へ収める（要件 12.3 は差し替え式にも及ぶ）。
  if (params_.override_fn != nullptr) {
    return clamp(params_.override_fn(sample.milli_volts, params_), 0.0f, absolute_max_duty_);
  }

  // 4) 既定の式（要件 12.1, 12.2, 12.3）:
  //    min(absolute_max_duty, reference_milli_volts / measured_milli_volts)
  //    （pwm_ceiling.hpp 単位の説明を参照。比そのものがデューティ上限）。
  //    reference_milli_volts > 0（設定検証）かつここでは
  //    measured_milli_volts > 0 が確定しているため比は常に正であり、
  //    下限クランプは純粋に防御的なものである。
  const float ratio = static_cast<float>(params_.reference_milli_volts) /
                       static_cast<float>(sample.milli_volts);
  return clamp(ratio, 0.0f, absolute_max_duty_);
}

}  // namespace drivetrain_control
