#pragma once

// drivetrain_control units (design.md L0 / "Units", 要件 4.2, 4.3, 7.7, 15.6).
//
// 距離ミリメートル・時刻ミリ秒・電圧ミリボルトの換算係数と円周率をここへ
// 1箇所へ集約する。裸の 1000 / π をこのファイル以外へ書かない方針を、
// この Spec のすべての層で維持する。
//
// L0 は標準ヘッダのみに依存する（design.md "Dependency Direction"）。この
// ファイルはビルド構成マクロ・処理系依存の整数幅・ペリフェラル固有の型の
// いずれも参照しない。

#include <cmath>
#include <cstdint>

namespace drivetrain_control::units {

// 換算係数（これ以外の場所に 1000 / π / 60 を書かない）
inline constexpr float kMillisecondsPerSecond = 1000.0f;
inline constexpr float kMilliVoltsPerVolt     = 1000.0f;
inline constexpr float kPi                    = 3.14159265358979323846f;
inline constexpr float kTwoPi                 = 2.0f * kPi;

// 時刻（ms）→ 秒。dt <= 0 の判定は呼び出し側が済ませている前提。
constexpr float millisToSeconds(std::int32_t ms) noexcept {
  return static_cast<float>(ms) / kMillisecondsPerSecond;
}

// エンコーダ累積カウント差 → 距離（mm）。
//   counts_per_wheel_rev: 出力軸1回転あたりのカウント数（実測校正値。要件 7.6）
//   wheel_diameter_mm:    ホイール直径（mm。要件 7.7）
//
// Preconditions: counts_per_wheel_rev > 0, wheel_diameter_mm > 0
//                （設定検証で保証済み。この関数自体は検証しない）
// Postconditions: delta_counts に比例し、符号を保つ
// Invariants: カウント → 距離の換算はこの関数だけが行う（要件 7.7）
constexpr float countsToMillimetres(std::int64_t delta_counts,
                                     std::int32_t counts_per_wheel_rev,
                                     float wheel_diameter_mm) noexcept {
  return (static_cast<float>(delta_counts) / static_cast<float>(counts_per_wheel_rev)) *
         kPi * wheel_diameter_mm;
}

// 角度を (-π, +π] へ正規化する。std::fmod を用いるため constexpr にはしない
// （C++17 では std::fmod が constexpr 関数として規定されていない）。
inline float wrapAngle(float radians) noexcept {
  float wrapped = std::fmod(radians + kPi, kTwoPi);
  if (wrapped <= 0.0f) {
    wrapped += kTwoPi;
  }
  return wrapped - kPi;
}

}  // namespace drivetrain_control::units
