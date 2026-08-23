#pragma once

// test_support fake Ports (drivetrain-core task 7.1, design.md "WheelPlant
// / FakePorts", 要件 16.2, 16.3, 16.4, 16.8).
//
// ⚠️ テスト専用。lib/test_support/ に置き、本体には含めない（要件 16.3、
// wheel_plant.hpp 冒頭コメント参照）。ヘッダのみで完結する部品として、
// 対応する .cpp を作らない（design.md "File Structure Plan の要点" と同じ
// 方針。lib/test_support/src/ には wheel_plant.cpp のみが存在する）。
//
// - FakeEncoderPort: WheelPlant を3輪ぶん内部に保持し、read() は
//   WrapAccumulator::update(plant.raw_count()) を各輪へ呼ぶ ―― これが
//   「エンコーダ側はモデルと累積器を実際の使われ方で接続する」（task 7.1
//   本文）の実体であり、実アダプタ（teleop-bringup）が EncoderPort::read()
//   を実装する際に踏襲すべきパターンをそのまま体現する。閉ループテスト
//   （task 7.2）は plant(wheel) 経由で各 WheelPlant を直接
//   advance()/setStalled() する（FakeMotorPort::lastOutputs() で読んだ
//   直前のデューティを使う）。
// - FakeMotorPort: write() は最後に書かれた値を保持するだけ。判断を持たない
//   （ports.hpp の契約どおり）。
// - FakeBatteryPort: read() は直近に設定された値をそのまま返す。テストが
//   step() の合間に setSample()/setMissing() を呼ぶことで、時系列
//   （複数ステップに渡って値を変える）と欠測（valid=false を注入する）の
//   両方を表現できる。

#include <cstdint>

#include "drivetrain_control/ports.hpp"
#include "drivetrain_control/types.hpp"
#include "drivetrain_control/wrap_accumulator.hpp"
#include "test_support/plant_coefficients.hpp"
#include "test_support/wheel_plant.hpp"

namespace test_support {

class FakeEncoderPort : public drivetrain_control::EncoderPort {
 public:
  // 3輪とも同一のプラント係数・エンコーダパラメータで構築する（極性の
  // 吸収は DrivetrainController::EncoderParams::polarity が担う責務であり、
  // ポート契約は生の折り返しなし累積カウントだけを返せばよい。ports.hpp
  // 参照）。
  FakeEncoderPort(const PlantCoefficients& coefficients, std::int32_t counts_per_wheel_rev,
                  float wheel_diameter_mm, std::int32_t raw_modulus) noexcept
      : plants_{WheelPlant(coefficients, counts_per_wheel_rev, wheel_diameter_mm, raw_modulus),
                WheelPlant(coefficients, counts_per_wheel_rev, wheel_diameter_mm, raw_modulus),
                WheelPlant(coefficients, counts_per_wheel_rev, wheel_diameter_mm, raw_modulus)},
        accumulators_{
            drivetrain_control::WrapAccumulator(raw_modulus, plants_[0].raw_count()),
            drivetrain_control::WrapAccumulator(raw_modulus, plants_[1].raw_count()),
            drivetrain_control::WrapAccumulator(raw_modulus, plants_[2].raw_count())} {}

  drivetrain_control::EncoderCounts read() override {
    drivetrain_control::EncoderCounts counts{};
    for (std::uint8_t i = 0; i < drivetrain_control::kWheelCount; ++i) {
      counts.count[i] = accumulators_[i].update(plants_[i].raw_count());
    }
    return counts;
  }

  // テストが各輪のプラントへ直接触れるための入口（advance()/setStalled()/
  // speed_mm_s() を呼ぶ）。FakeMotorPort::lastOutputs() で読んだデューティを
  // 使って閉ループを回すのは呼び出し側（task 7.2）の責務であり、本クラス
  // 自身は自動で advance() を呼ばない（design.md の Service Interface は
  // FakeEncoderPort に advance() 相当のメソッドを持たせていない）。
  WheelPlant& plant(std::uint8_t wheel) noexcept { return plants_[wheel]; }
  const WheelPlant& plant(std::uint8_t wheel) const noexcept { return plants_[wheel]; }

 private:
  WheelPlant plants_[drivetrain_control::kWheelCount];
  drivetrain_control::WrapAccumulator accumulators_[drivetrain_control::kWheelCount];
};

class FakeMotorPort : public drivetrain_control::MotorOutputPort {
 public:
  // ports.hpp の契約どおり、判断を持たず最後に書かれた値をそのまま保持する。
  void write(const drivetrain_control::WheelOutputs& outputs) override {
    last_outputs_ = outputs;
    ++write_count_;
  }

  const drivetrain_control::WheelOutputs& lastOutputs() const noexcept { return last_outputs_; }
  int writeCount() const noexcept { return write_count_; }

 private:
  drivetrain_control::WheelOutputs last_outputs_{};
  int write_count_ = 0;
};

class FakeBatteryPort : public drivetrain_control::BatteryVoltagePort {
 public:
  drivetrain_control::VoltageSample read() override {
    ++read_count_;
    return sample_;
  }

  // テストが step() の合間に呼ぶことで時系列を注入する（複数回呼べば
  // 段階的な電圧変化になる）。
  void setSample(std::int32_t milli_volts) noexcept { sample_ = {/*valid=*/true, milli_volts}; }

  // 欠測サンプルの注入（要件 11.8 の保護②検証で使う。valid=false）。
  void setMissing() noexcept { sample_ = drivetrain_control::VoltageSample{}; }

  int readCount() const noexcept { return read_count_; }

 private:
  drivetrain_control::VoltageSample sample_{};
  int read_count_ = 0;
};

}  // namespace test_support
