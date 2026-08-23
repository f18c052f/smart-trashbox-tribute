#include <unity.h>

#include <cstdint>

#include "drivetrain_control/ports.hpp"
#include "drivetrain_control/types.hpp"

// drivetrain-core task 2.4: 3つのペリフェラルポート宣言（ports.hpp）を検証
// するホストテスト。
//
// ports.hpp は純粋仮想インターフェースのみを宣言し、実装を1つも持たない
// （要件 2.1）。このテストは「実装が正しい」ことではなく「インターフェース
// として実際に派生・オーバーライド・呼び出しが可能であること」を裏取りする
// ための、テストローカルの使い捨てモックである。task 7.1 が用意する
// lib/test_support/fake_ports.hpp（プラント/累積器と実際の使われ方で接続
// した偽実装）とは別物であり、ここでは代替しない。
//
// 併せて、Ports 集約体がデフォルトで全ポインタ null に構築されること
// （configure() が null を設定エラーとして拒否する前提の土台）を確認する。

using drivetrain_control::BatteryVoltagePort;
using drivetrain_control::EncoderCounts;
using drivetrain_control::EncoderPort;
using drivetrain_control::MotorOutputPort;
using drivetrain_control::Ports;
using drivetrain_control::VoltageSample;
using drivetrain_control::WheelOutputs;
using drivetrain_control::kWheelCount;

namespace {

class MockEncoderPort : public EncoderPort {
 public:
  EncoderCounts read() override {
    EncoderCounts counts;
    counts.count[0] = 42;
    counts.count[1] = -7;
    counts.count[2] = 0;
    return counts;
  }
};

class MockMotorOutputPort : public MotorOutputPort {
 public:
  void write(const WheelOutputs& outputs) override {
    last_outputs_ = outputs;
    ++write_count_;
  }

  WheelOutputs last_outputs_{};
  int write_count_ = 0;
};

class MockBatteryVoltagePort : public BatteryVoltagePort {
 public:
  VoltageSample read() override {
    VoltageSample sample;
    sample.valid = true;
    sample.milli_volts = 7400;
    return sample;
  }
};

}  // namespace

void setUp(void) {}
void tearDown(void) {}

// ---------------------------------------------------------------------------
// Ports: 既定構築で全ポインタが null であること
// ---------------------------------------------------------------------------

void test_ports_struct_default_constructed_has_all_null_pointers(void) {
  Ports ports;
  TEST_ASSERT_NULL(ports.encoder);
  TEST_ASSERT_NULL(ports.motor);
  TEST_ASSERT_NULL(ports.battery);
}

void test_ports_struct_holds_assigned_pointers(void) {
  MockEncoderPort encoder_mock;
  MockMotorOutputPort motor_mock;
  MockBatteryVoltagePort battery_mock;

  Ports ports;
  ports.encoder = &encoder_mock;
  ports.motor = &motor_mock;
  ports.battery = &battery_mock;

  TEST_ASSERT_EQUAL_PTR(&encoder_mock, ports.encoder);
  TEST_ASSERT_EQUAL_PTR(&motor_mock, ports.motor);
  TEST_ASSERT_EQUAL_PTR(&battery_mock, ports.battery);
}

// ---------------------------------------------------------------------------
// 各ポートが派生・オーバーライド可能で、基底ポインタ/参照経由で呼び出せる
// こと（実装を持たない抽象として実際に使用可能であることの実効的な裏取り）
// ---------------------------------------------------------------------------

void test_encoder_port_is_overridable_and_invocable_through_base_reference(void) {
  MockEncoderPort mock;
  EncoderPort& port = mock;
  const EncoderCounts counts = port.read();
  TEST_ASSERT_EQUAL_INT64(42, counts.count[0]);
  TEST_ASSERT_EQUAL_INT64(-7, counts.count[1]);
  TEST_ASSERT_EQUAL_INT64(0, counts.count[2]);
}

void test_motor_output_port_is_overridable_and_invocable_through_base_reference(void) {
  MockMotorOutputPort mock;
  MotorOutputPort& port = mock;

  WheelOutputs outputs;
  outputs.duty[0] = 1.0f;
  outputs.duty[1] = -1.0f;
  outputs.duty[2] = 0.0f;
  port.write(outputs);

  TEST_ASSERT_EQUAL_INT(1, mock.write_count_);
  TEST_ASSERT_EQUAL_FLOAT(1.0f, mock.last_outputs_.duty[0]);
  TEST_ASSERT_EQUAL_FLOAT(-1.0f, mock.last_outputs_.duty[1]);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, mock.last_outputs_.duty[2]);
}

void test_battery_voltage_port_is_overridable_and_invocable_through_base_reference(void) {
  MockBatteryVoltagePort mock;
  BatteryVoltagePort& port = mock;
  const VoltageSample sample = port.read();
  TEST_ASSERT_TRUE(sample.valid);
  TEST_ASSERT_EQUAL_INT32(7400, sample.milli_volts);
}

// ---------------------------------------------------------------------------
// ポート越しの往復が3輪分の物理量（累積カウント）を保持すること
// ---------------------------------------------------------------------------

void test_encoder_port_read_covers_all_wheels(void) {
  MockEncoderPort mock;
  EncoderPort& port = mock;
  const EncoderCounts counts = port.read();
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    (void)counts.count[i];  // 3輪分の添字アクセスが有効であることの確認
  }
  TEST_ASSERT_EQUAL_UINT8(3, kWheelCount);
}

int main(int argc, char** argv) {
  (void)argc;
  (void)argv;
  UNITY_BEGIN();

  RUN_TEST(test_ports_struct_default_constructed_has_all_null_pointers);
  RUN_TEST(test_ports_struct_holds_assigned_pointers);

  RUN_TEST(test_encoder_port_is_overridable_and_invocable_through_base_reference);
  RUN_TEST(test_motor_output_port_is_overridable_and_invocable_through_base_reference);
  RUN_TEST(test_battery_voltage_port_is_overridable_and_invocable_through_base_reference);

  RUN_TEST(test_encoder_port_read_covers_all_wheels);

  return UNITY_END();
}
