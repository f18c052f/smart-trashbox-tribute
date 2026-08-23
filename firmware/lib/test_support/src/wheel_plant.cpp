// test_support::WheelPlant implementation (drivetrain-core task 7.1).
//
// ⚠️ This is test-only, host-side plant-modeling code. It is not linked
// into any firmware build: lib/test_support/ has no CMakeLists.txt and is
// not listed in firmware/CMakeLists.txt's EXTRA_COMPONENT_DIRS (which
// points directly at lib/drivetrain_control only) -- see design.md "File
// Structure Plan" and requirements.md 16.3. The coefficients this model
// uses are declared PROVISIONAL in plant_coefficients.hpp; read that
// file's header comment before citing anything derived from this model as
// a real-hardware property (要件 16.2, 16.4, 16.6).
//
// Discretization choice ("むだ時間を持たない" 1次遅れのみ, design.md):
//   target_speed_mm_s = duty * supply_ratio * coefficients.duty_to_steady_mm_s
//   alpha = clamp(dt_ms / time_constant_ms, 0, 1)
//   speed_mm_s_ += (target_speed_mm_s - speed_mm_s_) * alpha
// This is a forward-Euler step of the first-order lag dv/dt =
// (target - v) / time_constant, with the fractional step alpha explicitly
// clamped to [0, 1]. A plain (unclamped) forward-Euler step is only
// numerically stable while dt_ms << time_constant_ms; because advance() is
// driven by whatever dt a test chooses rather than a fixed simulation
// step, clamping alpha to 1.0 guarantees a single call can never overshoot
// past the target or oscillate/diverge for a large dt -- at the cost of a
// single very large step "snapping" straight to the target instead of
// asymptotically approaching it across sub-steps. That tradeoff is
// acceptable here because the closed-loop tests that consume this model
// (task 7.2) are only allowed to assert direction of convergence / trip /
// no-trip, and are explicitly forbidden from asserting a specific
// convergence time or gain value (design.md "WheelPlant / FakePorts"
// Implementation Notes: "収束の速さは合否条件ではない", 要件 16.4, 16.6).
//
// Stall behaviour (setStalled(true)): speed_mm_s() reads back as exactly
// 0 for as long as stalled remains true (regardless of duty), and
// raw_count() is held flat (accumulated_counts_ is not advanced while
// stalled) rather than jittering. This is a deliberate simplification:
// MotorLockDetector (保護①) trips on "commanded duty at/above a threshold
// AND measured speed at/below a threshold, sustained for a duration" -- an
// exactly-flat measured speed of 0 satisfies that condition unambiguously
// and keeps a trip test's expected timing exact, with no need to reason
// about jitter magnitude relative to the speed threshold.
//
// raw_count() wrapping: accumulated_counts_ is kept as an UNWRAPPED,
// monotonically-changing running distance-in-counts (never reset in
// place). raw_count() wraps that value into the modulus's natural signed
// range, (-modulus/2, +modulus/2], each time it is called -- the same way
// a real free-running signed hardware counter (e.g. PCNT's int16_t
// register, modulus 65536) presents its value. Wrapping on read (instead
// of wrapping accumulated_counts_ itself in place) keeps advance() from
// ever having to reason about "did I just cross a wrap boundary"; the
// tradeoff is that accumulated_counts_ grows without further bound over a
// very long-running test, which is not a practical concern at float
// precision for the short synthetic time spans the closed-loop tests use.

#include "test_support/wheel_plant.hpp"

#include <cmath>

#include "drivetrain_control/units.hpp"

namespace test_support {

WheelPlant::WheelPlant(const PlantCoefficients& coefficients, std::int32_t counts_per_wheel_rev,
                        float wheel_diameter_mm, std::int32_t raw_modulus) noexcept
    : coefficients_(coefficients),
      counts_per_wheel_rev_(counts_per_wheel_rev),
      wheel_diameter_mm_(wheel_diameter_mm),
      raw_modulus_(raw_modulus) {}

void WheelPlant::advance(float duty, float supply_ratio, drivetrain_control::DurationMs dt_ms) noexcept {
  if (dt_ms <= 0) {
    return;
  }

  if (stalled_) {
    // 保護① の発火試験用: 速度を強制的に 0 にし、生カウントも進めない
    // （held flat。上記コメント参照）。
    speed_mm_s_ = 0.0f;
    return;
  }

  const float target_speed_mm_s = duty * supply_ratio * coefficients_.duty_to_steady_mm_s;

  float alpha = 1.0f;
  if (coefficients_.time_constant_ms > 0.0f) {
    alpha = static_cast<float>(dt_ms) / coefficients_.time_constant_ms;
    if (alpha > 1.0f) {
      alpha = 1.0f;
    }
  }
  speed_mm_s_ += (target_speed_mm_s - speed_mm_s_) * alpha;

  // 距離 → カウントは units::countsToMillimetres の逆変換
  // (counts = distance_mm / (pi * wheel_diameter_mm) * counts_per_wheel_rev)。
  // pi は drivetrain_control::units::kPi を再利用し、裸の定数を書かない。
  const float dt_s = drivetrain_control::units::millisToSeconds(dt_ms);
  const float distance_mm = speed_mm_s_ * dt_s;
  accumulated_counts_ += (distance_mm / (drivetrain_control::units::kPi * wheel_diameter_mm_)) *
                         static_cast<float>(counts_per_wheel_rev_);
}

float WheelPlant::speed_mm_s() const noexcept { return speed_mm_s_; }

std::int32_t WheelPlant::raw_count() const noexcept {
  const float modulus_f = static_cast<float>(raw_modulus_);
  float wrapped = std::fmod(accumulated_counts_, modulus_f);
  if (wrapped < 0.0f) {
    wrapped += modulus_f;
  }
  if (wrapped > modulus_f / 2.0f) {
    wrapped -= modulus_f;
  }
  return static_cast<std::int32_t>(wrapped);
}

void WheelPlant::setStalled(bool stalled) noexcept { stalled_ = stalled; }

}  // namespace test_support
