#pragma once

// test_support::WheelPlant (drivetrain-core task 7.1, design.md "WheelPlant
// / FakePorts", 要件 16.2, 16.3, 16.4, 16.6, 16.8).
//
// ⚠️ テスト専用。lib/test_support/ に置き、本体（lib/drivetrain_control/）
// のコードには一切含めない。lib/test_support/ は CMakeLists.txt を持たず
// firmware/CMakeLists.txt の EXTRA_COMPONENT_DIRS にも含まれないため、
// ファームウェア成果物へリンクされる経路が存在しない（要件 16.3）。
//
// デューティから車輪速度への1次遅れモデル（むだ時間なし）と、ストール
// 注入、法で折り返した生カウントの生成を提供する。使う係数は
// test_support/plant_coefficients.hpp が宣言する「合否条件ではない仮値」
// である（要件 16.2, 16.4, 16.6。そちらのファイル先頭コメントを必ず読む
// こと）。離散化方式・ストール時の挙動・生カウントの折り返し方式の詳細は
// wheel_plant.cpp 冒頭コメントを参照。

#include <cstdint>

#include "drivetrain_control/types.hpp"
#include "test_support/plant_coefficients.hpp"

namespace test_support {

class WheelPlant {
 public:
  // counts_per_wheel_rev / wheel_diameter_mm: DrivetrainConfig::EncoderParams
  // と同じ意味の値をテスト側が渡す（実測校正値ではなく、テストが選んだ
  // 架空の値でよい）。
  // raw_modulus: WrapAccumulator に渡すのと同じ法。raw_count() はこの法で
  // 折り返した値を返す。
  WheelPlant(const PlantCoefficients& coefficients, std::int32_t counts_per_wheel_rev,
             float wheel_diameter_mm, std::int32_t raw_modulus) noexcept;

  // 1ステップぶんプラントを進める。duty は [-1, +1] の指令デューティ、
  // supply_ratio はバッテリ電圧スケールのトルク余裕（1.0 が定格）。
  // dt_ms <= 0 は no-op（状態を変えない）。
  void advance(float duty, float supply_ratio, drivetrain_control::DurationMs dt_ms) noexcept;

  // 直近の advance() が確定した車輪周速 [mm/s]。setStalled(true) の間は
  // 常に 0 を返す。
  float speed_mm_s() const noexcept;

  // 法 raw_modulus で折り返した生カウント。実ハードウェアカウンタの
  // フリーラン挙動を模す（WrapAccumulator と組んで使う前提）。
  std::int32_t raw_count() const noexcept;

  // 保護① モータロック検出の発火試験用。true の間は速度が強制的に 0 相当
  // になり、生カウントも進まない（wheel_plant.cpp 冒頭コメント参照）。
  void setStalled(bool stalled) noexcept;

 private:
  PlantCoefficients coefficients_;
  std::int32_t counts_per_wheel_rev_;
  float wheel_diameter_mm_;
  std::int32_t raw_modulus_;

  float speed_mm_s_ = 0.0f;
  // 折り返す前の連続値（実数のカウント相当）。raw_count() が呼ばれるたびに
  // これを法で折り返した値を返す（wheel_plant.cpp 参照）。
  float accumulated_counts_ = 0.0f;
  bool stalled_ = false;
};

}  // namespace test_support
