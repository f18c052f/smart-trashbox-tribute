#include <unity.h>

#include <cmath>
#include <cstdint>

#include "drivetrain_control/config.hpp"
#include "drivetrain_control/controller.hpp"
#include "drivetrain_control/types.hpp"
#include "test_support/fake_ports.hpp"
#include "test_support/plant_coefficients.hpp"
#include "test_support/wheel_plant.hpp"

// drivetrain-core task 7.1: test_support の偽ポート（FakeEncoderPort /
// FakeMotorPort / FakeBatteryPort）のホストテスト。
//
// 検証する:
//   A. FakeEncoderPort::read() が、内部の WheelPlant::raw_count() を
//      WrapAccumulator::update() へ実際の使われ方で通し、折り返しを跨いで
//      も単調な64bit累積カウントを返すこと（task 7.1 本文「エンコーダ側は
//      モデルと累積器を実際の使われ方で接続する」の裏取り）
//   B. FakeMotorPort::write() が最後に書かれた値をそのまま保持すること
//   C. FakeBatteryPort が setSample()/setMissing() で時系列と欠測の両方を
//      注入できること
//   D. 偽ポート3種を差した実際の DrivetrainController が configure() に
//      成功し、step() を実行できること（task 7.1 観測可能な完了状態
//      「偽ポートを差した制御ステップが実行できる」）

using drivetrain_control::DrivetrainConfig;
using drivetrain_control::DrivetrainController;
using drivetrain_control::EncoderCounts;
using drivetrain_control::GeometryParams;
using drivetrain_control::kWheelCount;
using drivetrain_control::Ports;
using drivetrain_control::StepResult;
using drivetrain_control::VoltageSample;
using drivetrain_control::WheelOutputs;
using drivetrain_control::WheelVelocityCommand;
using test_support::FakeBatteryPort;
using test_support::FakeEncoderPort;
using test_support::FakeMotorPort;
using test_support::PlantCoefficients;

namespace {

PlantCoefficients makeCoefficients() {
  PlantCoefficients c{};
  c.duty_to_steady_mm_s = 500.0f;
  c.time_constant_ms = 100.0f;
  return c;
}

// test_controller_step.cpp の makeValidConfig() と同じ流儀（検証ロジックが
// 正しく動くことだけを確かめるための架空の妥当値一式。実機の性能値では
// ない。要件 15.1, 15.2）。
DrivetrainConfig makeValidConfig() {
  DrivetrainConfig config{};

  config.geometry = GeometryParams::equilateral(150.0f, 0.0f);

  config.encoder.counts_per_wheel_rev = 836;
  config.encoder.wheel_diameter_mm = 60.0f;
  config.encoder.raw_modulus = 65536;
  config.encoder.polarity[0] = 1;
  config.encoder.polarity[1] = 1;
  config.encoder.polarity[2] = 1;

  config.output.polarity[0] = 1;
  config.output.polarity[1] = 1;
  config.output.polarity[2] = 1;
  config.output.absolute_max_duty = 1.0f;

  config.limits.max_body_speed_mm_s = 800.0f;
  config.limits.max_body_omega_rad_s = 6.0f;
  config.limits.max_wheel_speed_mm_s = 1000.0f;

  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    config.pid[i].kp = 0.002f;
    config.pid[i].ki = 0.0f;
    config.pid[i].kd = 0.0f;
    config.pid[i].integral_limit = 1.0f;
  }

  config.lock.duty_threshold = 0.3f;
  config.lock.speed_threshold_mm_s = 10.0f;
  config.lock.duration_ms = 300;
  config.lock.clear_duration_ms = 200;
  config.lock.latching = true;

  config.low_voltage.warn_milli_volts = 10800;
  config.low_voltage.stop_milli_volts = 10200;
  config.low_voltage.recover_milli_volts = 10800;
  config.low_voltage.average_window = 8;
  config.low_voltage.stop_duration_ms = 500;
  config.low_voltage.unavailable_duration_ms = 500;
  config.low_voltage.latching = true;

  config.voltage_scaler.table[0] = {0, 0};
  config.voltage_scaler.table[1] = {2048, 6000};
  config.voltage_scaler.table[2] = {4095, 12600};
  config.voltage_scaler.point_count = 3;

  config.pwm_ceiling.enabled = true;
  config.pwm_ceiling.reference_milli_volts = 12600;
  config.pwm_ceiling.fallback_duty = 0.2f;
  config.pwm_ceiling.override_fn = nullptr;

  config.watchdog.timeout_ms = 300;

  return config;
}

}  // namespace

void setUp(void) {}
void tearDown(void) {}

// ---------------------------------------------------------------------------
// A: FakeEncoderPort::read() は WheelPlant::raw_count() を
//    WrapAccumulator::update() へ実際の使われ方で通し、折り返しを跨いでも
//    単調な累積カウントを返す。
// ---------------------------------------------------------------------------

void test_fake_encoder_port_wires_plant_and_accumulator_across_a_wrap(void) {
  const PlantCoefficients coeffs = makeCoefficients();
  // 折り返しを素早く再現するため小さい法を使う（wheel_plant のテストと
  // 同じ流儀）。
  const std::int32_t raw_modulus = 100;
  FakeEncoderPort encoder(coeffs, /*counts_per_wheel_rev=*/1, /*wheel_diameter_mm=*/1.0f, raw_modulus);

  const EncoderCounts baseline = encoder.read();
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    TEST_ASSERT_EQUAL_INT64(0, baseline.count[i]);
  }

  // 輪0だけを正転させ続け、法(100)を何周も超える距離を進める。
  std::int64_t previous_count0 = baseline.count[0];
  bool observed_monotonic_growth_past_one_modulus = false;
  for (int i = 0; i < 4000; ++i) {
    encoder.plant(0).advance(/*duty=*/1.0f, /*supply_ratio=*/1.0f, /*dt_ms=*/50);
    const EncoderCounts counts = encoder.read();
    // 折り返しを跨いでも累積カウントは単調非減少であること（正転のみ）。
    TEST_ASSERT_TRUE(counts.count[0] >= previous_count0);
    if (counts.count[0] > raw_modulus) {
      observed_monotonic_growth_past_one_modulus = true;
    }
    previous_count0 = counts.count[0];
    // 動かしていない輪1/2は0のまま。
    TEST_ASSERT_EQUAL_INT64(0, counts.count[1]);
    TEST_ASSERT_EQUAL_INT64(0, counts.count[2]);
  }
  TEST_ASSERT_TRUE_MESSAGE(
      observed_monotonic_growth_past_one_modulus,
      "expected the accumulated count to exceed a single modulus, proving the "
      "WrapAccumulator actually carried a wrap rather than just tracking the raw value");
}

void test_fake_encoder_port_accumulates_reverse_rotation_correctly(void) {
  const PlantCoefficients coeffs = makeCoefficients();
  const std::int32_t raw_modulus = 100;
  FakeEncoderPort encoder(coeffs, 1, 1.0f, raw_modulus);

  std::int64_t previous_count0 = encoder.read().count[0];
  bool observed_monotonic_decrease_past_negative_modulus = false;
  for (int i = 0; i < 4000; ++i) {
    encoder.plant(0).advance(/*duty=*/-1.0f, /*supply_ratio=*/1.0f, /*dt_ms=*/50);
    const EncoderCounts counts = encoder.read();
    TEST_ASSERT_TRUE(counts.count[0] <= previous_count0);
    if (counts.count[0] < -raw_modulus) {
      observed_monotonic_decrease_past_negative_modulus = true;
    }
    previous_count0 = counts.count[0];
  }
  TEST_ASSERT_TRUE(observed_monotonic_decrease_past_negative_modulus);
}

// ---------------------------------------------------------------------------
// B: FakeMotorPort::write() は最後に書かれた値をそのまま保持する。
// ---------------------------------------------------------------------------

void test_fake_motor_port_holds_last_written_outputs(void) {
  FakeMotorPort motor;
  TEST_ASSERT_EQUAL_INT(0, motor.writeCount());

  WheelOutputs first{};
  first.duty[0] = 0.1f;
  first.duty[1] = 0.2f;
  first.duty[2] = 0.3f;
  motor.write(first);

  TEST_ASSERT_EQUAL_INT(1, motor.writeCount());
  TEST_ASSERT_EQUAL_FLOAT(0.1f, motor.lastOutputs().duty[0]);
  TEST_ASSERT_EQUAL_FLOAT(0.2f, motor.lastOutputs().duty[1]);
  TEST_ASSERT_EQUAL_FLOAT(0.3f, motor.lastOutputs().duty[2]);

  WheelOutputs second{};
  second.duty[0] = -0.5f;
  second.duty[1] = 0.0f;
  second.duty[2] = 0.9f;
  motor.write(second);

  TEST_ASSERT_EQUAL_INT(2, motor.writeCount());
  TEST_ASSERT_EQUAL_FLOAT(-0.5f, motor.lastOutputs().duty[0]);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, motor.lastOutputs().duty[1]);
  TEST_ASSERT_EQUAL_FLOAT(0.9f, motor.lastOutputs().duty[2]);
}

// ---------------------------------------------------------------------------
// C: FakeBatteryPort は setSample()/setMissing() で時系列と欠測を注入できる。
// ---------------------------------------------------------------------------

void test_fake_battery_port_injects_time_series_and_missing_samples(void) {
  FakeBatteryPort battery;

  // 既定はゼロ初期化 (valid=false)。
  VoltageSample initial = battery.read();
  TEST_ASSERT_FALSE(initial.valid);

  battery.setSample(12600);
  VoltageSample s1 = battery.read();
  TEST_ASSERT_TRUE(s1.valid);
  TEST_ASSERT_EQUAL_INT32(12600, s1.milli_volts);

  // 時系列: 段階的に電圧を下げていく。
  battery.setSample(11000);
  VoltageSample s2 = battery.read();
  TEST_ASSERT_TRUE(s2.valid);
  TEST_ASSERT_EQUAL_INT32(11000, s2.milli_volts);

  battery.setSample(10000);
  VoltageSample s3 = battery.read();
  TEST_ASSERT_EQUAL_INT32(10000, s3.milli_volts);

  // 欠測の注入。
  battery.setMissing();
  VoltageSample missing = battery.read();
  TEST_ASSERT_FALSE(missing.valid);

  // 欠測から復帰。
  battery.setSample(12000);
  VoltageSample recovered = battery.read();
  TEST_ASSERT_TRUE(recovered.valid);
  TEST_ASSERT_EQUAL_INT32(12000, recovered.milli_volts);

  TEST_ASSERT_EQUAL_INT(6, battery.readCount());
}

// ---------------------------------------------------------------------------
// D: 偽ポート3種を差した実際の DrivetrainController が configure() に成功
//    し、step() を実行できる（task 7.1 の観測可能な完了状態）。
// ---------------------------------------------------------------------------

void test_controller_configured_with_fake_ports_executes_a_step(void) {
  const DrivetrainConfig config = makeValidConfig();
  const PlantCoefficients coeffs = makeCoefficients();

  FakeEncoderPort encoder(coeffs, config.encoder.counts_per_wheel_rev, config.encoder.wheel_diameter_mm,
                           config.encoder.raw_modulus);
  FakeMotorPort motor;
  FakeBatteryPort battery;
  battery.setSample(12600);

  Ports ports;
  ports.encoder = &encoder;
  ports.motor = &motor;
  ports.battery = &battery;

  DrivetrainController controller;
  const auto diag = controller.configure(config, ports, /*now=*/0);
  TEST_ASSERT_TRUE(diag.ok());
  TEST_ASSERT_TRUE(controller.configured());

  controller.setOutputEnabled(true, /*now=*/0);
  WheelVelocityCommand command{};
  command.wheel_mm_s[0] = 100.0f;
  command.wheel_mm_s[1] = 100.0f;
  command.wheel_mm_s[2] = 100.0f;
  command.issued_at_ms = 0;
  TEST_ASSERT_TRUE(controller.submit(command).accepted);

  // 制御ステップを何回か実際に回し、書き出された指令デューティで各輪の
  // WheelPlant を進める ―― これが「偽ポートを差した制御ステップが実行
  // できる」ことの直接的な証明であり、同時に閉ループの配線
  // （FakeMotorPort::lastOutputs() -> WheelPlant::advance()）が動作する
  // ことも示す（本格的な収束検証は task 7.2 の範囲）。
  StepResult result{};
  for (int i = 0; i < 5; ++i) {
    const drivetrain_control::TimeMs now = static_cast<drivetrain_control::TimeMs>(10 * (i + 1));
    result = controller.step(now);
    TEST_ASSERT_TRUE(result.outputs_written);

    const WheelOutputs& applied = motor.lastOutputs();
    for (std::uint8_t w = 0; w < kWheelCount; ++w) {
      encoder.plant(w).advance(applied.duty[w], /*supply_ratio=*/1.0f, /*dt_ms=*/10);
    }
  }

  // クラッシュせず、実際に何かしらの制御結果が返っていること
  // （このテストが何も検証していない状態を避けるための最低限の裏取り）。
  bool any_nonzero_duty = false;
  for (std::uint8_t w = 0; w < kWheelCount; ++w) {
    if (std::fabs(result.outputs.duty[w]) > 1.0e-6f) {
      any_nonzero_duty = true;
    }
  }
  TEST_ASSERT_TRUE(any_nonzero_duty);
  TEST_ASSERT_TRUE(motor.writeCount() >= 5);
  TEST_ASSERT_TRUE(battery.readCount() >= 5);
}

int main(int argc, char** argv) {
  (void)argc;
  (void)argv;
  UNITY_BEGIN();

  RUN_TEST(test_fake_encoder_port_wires_plant_and_accumulator_across_a_wrap);
  RUN_TEST(test_fake_encoder_port_accumulates_reverse_rotation_correctly);
  RUN_TEST(test_fake_motor_port_holds_last_written_outputs);
  RUN_TEST(test_fake_battery_port_injects_time_series_and_missing_samples);
  RUN_TEST(test_controller_configured_with_fake_ports_executes_a_step);

  return UNITY_END();
}
