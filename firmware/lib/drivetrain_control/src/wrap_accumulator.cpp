#include "drivetrain_control/wrap_accumulator.hpp"

// drivetrain_control WrapAccumulator implementation (design.md L4
// "WrapAccumulator", 要件 4.4, 4.5, 7.1, 7.2, 7.3, 7.4). See
// wrap_accumulator.hpp for the full contract, preconditions, and the
// worst-case sampling-interval margin estimate.

namespace drivetrain_control {

WrapAccumulator::WrapAccumulator(std::int32_t modulus, std::int32_t initial_raw) noexcept
    : modulus_(modulus), last_raw_(initial_raw), accumulated_(0) {}

std::int64_t WrapAccumulator::update(std::int32_t raw_now) noexcept {
  const std::int64_t modulus = static_cast<std::int64_t>(modulus_);
  const std::int64_t raw_delta =
      static_cast<std::int64_t>(raw_now) - static_cast<std::int64_t>(last_raw_);

  // 最短経路への折り返し: raw_delta をまず [0, modulus) へ正規化し、
  // modulus/2 を超える側は modulus を引いて (-modulus/2, +modulus/2] へ
  // 写像する。これにより「前回値との生の差」だけから、正転による折り返し
  // （見かけ上は大きな負の差）と逆転による折り返し（見かけ上は大きな正の
  // 差）の両方を、符号を取り違えずに区別できる（要件 7.4）。
  //
  // Precondition（wrap_accumulator.hpp 参照）: サンプリング間隔中の真の
  // 変化量の絶対値が modulus/2 未満であること。この前提が崩れる場合
  // （例えばサンプリングが極端に遅延した場合）、最短経路が実際の回転方向と
  // 逆に解釈され得るが、これは本関数では検出できない事前条件であり、
  // 呼び出し側の制御周期設計で担保する（wrap_accumulator.hpp の余裕の
  // 見積もり参照）。
  std::int64_t wrapped = raw_delta % modulus;
  if (wrapped < 0) {
    wrapped += modulus;
  }
  if (wrapped > modulus / 2) {
    wrapped -= modulus;
  }

  accumulated_ += wrapped;
  last_raw_ = raw_now;
  return accumulated_;
}

std::int64_t WrapAccumulator::value() const noexcept { return accumulated_; }

void WrapAccumulator::reset(std::int32_t raw_now, std::int64_t accumulated) noexcept {
  last_raw_ = raw_now;
  accumulated_ = accumulated;
}

}  // namespace drivetrain_control
