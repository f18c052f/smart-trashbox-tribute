#include "drivetrain_control/voltage_scaler.hpp"

// drivetrain_control VoltageScaler implementation (design.md L4
// "VoltageScaler", 要件 11.7, 15.6). See voltage_scaler.hpp for the full
// contract, preconditions, and the integer-rounding rationale.

namespace drivetrain_control {

namespace {

// numerator / denominator を四捨五入（0 から絶対値が大きくなる側）で丸めた
// 商を返す。denominator は本ファイル内の呼び出しではすべて正
// （table[].raw の狭義単調増加により raw_span > 0 が保証される。config.cpp
// の validate() 済み前提、voltage_scaler.hpp Preconditions 参照）。
std::int64_t divRoundNearest(std::int64_t numerator, std::int64_t denominator) {
  if (numerator >= 0) {
    return (numerator + denominator / 2) / denominator;
  }
  return -((-numerator + denominator / 2) / denominator);
}

}  // namespace

VoltageScaler::VoltageScaler(const VoltageScalerParams& params) noexcept : params_(params) {}

std::int32_t VoltageScaler::toMilliVolts(std::int32_t raw) const noexcept {
  const std::uint8_t last = static_cast<std::uint8_t>(params_.point_count - 1);

  // どの2点間（またはどちらの端の外挿区間）を使うかを決める。
  //   - raw がテーブル先頭点以下: 先頭区間（点0→点1）の傾きで外挿
  //   - raw がテーブル末尾点以上: 末尾区間の傾きで外挿
  //   - それ以外: raw を挟む区間で線形補間
  // 2点構成（point_count == 2）では last == 1 となり、上記いずれの分岐でも
  // 同じ唯一の区間（点0→点1）が選ばれる。すなわちテーブル内補間そのものが
  // 分圧比のみの線形換算に自然に縮退する（別の分岐を持たない）。
  std::uint8_t lo = 0;
  std::uint8_t hi = 1;
  if (raw <= params_.table[0].raw) {
    lo = 0;
    hi = 1;
  } else if (raw >= params_.table[last].raw) {
    lo = static_cast<std::uint8_t>(last - 1);
    hi = last;
  } else {
    for (std::uint8_t i = 0; i < last; ++i) {
      if (raw <= params_.table[i + 1].raw) {
        lo = i;
        hi = static_cast<std::uint8_t>(i + 1);
        break;
      }
    }
  }

  const VoltageScalerParams::Point& p0 = params_.table[lo];
  const VoltageScalerParams::Point& p1 = params_.table[hi];

  const std::int64_t raw_span = static_cast<std::int64_t>(p1.raw) - static_cast<std::int64_t>(p0.raw);
  const std::int64_t mv_span =
      static_cast<std::int64_t>(p1.milli_volts) - static_cast<std::int64_t>(p0.milli_volts);
  const std::int64_t raw_offset = static_cast<std::int64_t>(raw) - static_cast<std::int64_t>(p0.raw);

  // raw_offset == 0（raw がちょうど p0）のとき numerator == 0 となり丸めは
  // 発生しない。raw_offset == raw_span（raw がちょうど p1）のときは
  // numerator = raw_span * mv_span がちょうど raw_span で割り切れるため、
  // これも丸めは発生しない。テーブル点の上での厳密一致はここから成り立つ。
  const std::int64_t numerator = raw_offset * mv_span;
  const std::int64_t mv_offset = divRoundNearest(numerator, raw_span);

  return static_cast<std::int32_t>(static_cast<std::int64_t>(p0.milli_volts) + mv_offset);
}

}  // namespace drivetrain_control
