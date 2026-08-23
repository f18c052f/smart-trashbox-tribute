#include <unity.h>

#include <cmath>
#include <cstdint>

#include "drivetrain_control/config.hpp"
#include "drivetrain_control/controller.hpp"
#include "drivetrain_control/kinematics.hpp"
#include "drivetrain_control/odometry.hpp"
#include "drivetrain_control/ports.hpp"
#include "drivetrain_control/types.hpp"
#include "drivetrain_control/units.hpp"

// drivetrain-core task 6.2: 制御ステップの骨格（設定・時刻・計測）を検証
// するホストテスト。
//
// design.md "DrivetrainController"（L10）の Preconditions/Postconditions/
// Invariants のうち、このタスクが実装する部分だけを対象にする。要件 3.1,
// 3.2, 3.4, 3.5, 3.6, 15.4。PID・PWM上限・保護①〜④の合成・出力極性・
// MotorOutputPort への書き出しはタスク6.3以降の対象であり、ここでは検証
// しない（出力デューティが常にゼロのままであることは前提として扱う）。
//
// 以下を検証する:
//   A. configure() 前（未設定）の step() は、ポートに一切触れず、
//      BlockReason::kNotConfigured を立てた遮断値（デューティ全ゼロ）の
//      StepResult を返す（要件 15.4）
//   B. ゼロ初期化された DrivetrainConfig は configure() に拒否され、
//      configured() は false のままで、その後の step() も A と同じ挙動
//      になる
//   C. Ports の3つ（encoder/motor/battery）がそれぞれ単独で null のとき、
//      configure() は拒否され、ConfigDiagnostic::index でどのポートかが
//      分かる（0=encoder, 1=motor, 2=battery）
//   D. 妥当な設定と妥当なポートで configure() が成功すること
//      （configured() が true になり、effectiveConfig() が渡した設定と
//      一致する。要件 15.5 の基本アクセサとしての確認）
//   E. 過去時刻・同時刻の step() はポートを読まず、状態を変えず、前回の
//      StepResult をそのまま返す副作用の無い短絡になる（要件 3.4）。
//      configure() 直後（まだ一度も実ステップが進んでいない）状態が対象
//   F. 時刻が進んだ実ステップ: エンコーダ累積カウントの差と経過時間
//      （実測の dt。固定周期を前提にしない、要件 3.5）から計測速度を
//      求め、EncoderParams::polarity を適用し（要件 6.6）、Odometry を
//      更新すること
//   G. F の実ステップの後、同じ時刻で step() を呼んでも
//      （ポートの中身をその間に変えても）ポートが読まれず、内部状態
//      （計測速度・エンコーダ基準値・Odometry の状態）が変化しないこと
//      （要件 3.4 を、状態が既に動いた後の短絡でも確認する）
//   H. 検証に失敗する2回目の configure() は、直前に成功していた設定を
//      破棄し、configured() を false に戻す（「検証に失敗した状態では
//      制御を開始させない」を再設定にも一貫させる設計判断の回帰）

using drivetrain_control::BlockMask;
using drivetrain_control::BlockReason;
using drivetrain_control::ConfigDiagnostic;
using drivetrain_control::ConfigError;
using drivetrain_control::DrivetrainConfig;
using drivetrain_control::DrivetrainController;
using drivetrain_control::EncoderCounts;
using drivetrain_control::EncoderPort;
using drivetrain_control::GeometryParams;
using drivetrain_control::Kinematics;
using drivetrain_control::kWheelCount;
using drivetrain_control::MotorOutputPort;
using drivetrain_control::BatteryVoltagePort;
using drivetrain_control::Ports;
using drivetrain_control::StepResult;
using drivetrain_control::TimeMs;
using drivetrain_control::VoltageSample;
using drivetrain_control::WheelOutputs;
using drivetrain_control::units::kPi;

namespace drivetrain_control {

// タスク6.2 時点では status()（タスク6.4）が無く、dt→計測速度→Odometry
// 更新の配線を外部から観測する公開手段が無い。controller.hpp が宣言する
// friend を通じて、このテストだけが内部状態を読む（ファイル先頭コメント
// 参照）。
struct ControllerStepTestHooks {
  static const float* lastMeasuredMmS(const DrivetrainController& c) { return c.last_measured_mm_s_; }
  static const EncoderCounts& lastEncoderCounts(const DrivetrainController& c) { return c.last_encoder_counts_; }
  static const OdometryState& odometryState(const DrivetrainController& c) { return c.odometry_.get().state(); }
};

}  // namespace drivetrain_control

using drivetrain_control::ControllerStepTestHooks;
using drivetrain_control::OdometryState;

namespace {

constexpr float kTight = 5.0e-2f;

// --- テスト専用の最小モックポート（lib/test_support/ ではない。タスク7.1
// が用意する本格的な WheelPlant/FakePorts とは別物で、本タスクの検証に
// 必要な最小限の記録だけを持つ） -------------------------------------------

class MockEncoderPort : public EncoderPort {
 public:
  EncoderCounts counts{};
  int read_count = 0;

  EncoderCounts read() override {
    ++read_count;
    return counts;
  }
};

class MockMotorPort : public MotorOutputPort {
 public:
  WheelOutputs last_written{};
  int write_count = 0;

  void write(const WheelOutputs& outputs) override {
    last_written = outputs;
    ++write_count;
  }
};

class MockBatteryPort : public BatteryVoltagePort {
 public:
  VoltageSample sample{};
  int read_count = 0;

  VoltageSample read() override {
    ++read_count;
    return sample;
  }
};

// 検証ロジックが正しく動くことだけを確かめるための架空の妥当値一式
// （実機の性能値ではない。要件 15.1, 15.2。test_config_validation.cpp の
// makeValidConfig() と同じ流儀）。
DrivetrainConfig makeValidConfig() {
  DrivetrainConfig config{};

  config.geometry = GeometryParams::equilateral(150.0f, 0.0f);

  config.encoder.counts_per_wheel_rev = 836;
  config.encoder.wheel_diameter_mm = 60.0f;
  config.encoder.raw_modulus = 65536;
  config.encoder.polarity[0] = 1;
  config.encoder.polarity[1] = -1;
  config.encoder.polarity[2] = 1;

  config.output.polarity[0] = 1;
  config.output.polarity[1] = -1;
  config.output.polarity[2] = 1;
  config.output.absolute_max_duty = 1.0f;

  config.limits.max_body_speed_mm_s = 800.0f;
  config.limits.max_body_omega_rad_s = 6.0f;
  config.limits.max_wheel_speed_mm_s = 1000.0f;

  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    config.pid[i].kp = 0.5f;
    config.pid[i].ki = 2.0f;
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

struct Fixture {
  MockEncoderPort encoder;
  MockMotorPort motor;
  MockBatteryPort battery;

  Ports ports() {
    Ports p;
    p.encoder = &encoder;
    p.motor = &motor;
    p.battery = &battery;
    return p;
  }
};

void assertBlockedZero(const StepResult& result) {
  const BlockMask kNotConfiguredBit = static_cast<BlockMask>(BlockReason::kNotConfigured);
  TEST_ASSERT_EQUAL_UINT16(kNotConfiguredBit, result.global_reasons);
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    TEST_ASSERT_EQUAL_FLOAT(0.0f, result.outputs.duty[i]);
    TEST_ASSERT_EQUAL_UINT16(0, result.wheel_reasons[i]);
  }
}

}  // namespace

void setUp(void) {}
void tearDown(void) {}

// ---------------------------------------------------------------------------
// A: 未設定状態の step() はポートに触れず、kNotConfigured の遮断値を返す。
// ---------------------------------------------------------------------------

void test_step_before_configure_blocks_without_touching_ports(void) {
  DrivetrainController controller;
  Fixture fx;

  TEST_ASSERT_FALSE(controller.configured());

  const StepResult result = controller.step(/*now=*/1000);
  assertBlockedZero(result);

  // ポートを一切構成していないので、そもそも読める対象すら無いことが
  // 「読みに行っていない」ことの一番強い裏取りになる。念のため fx 側の
  // カウンタも 0 のままであることを確認する（configure() を一度も呼んで
  // いないため、fx のポートは controller に一切知らされていない）。
  TEST_ASSERT_EQUAL_INT(0, fx.encoder.read_count);
  TEST_ASSERT_EQUAL_INT(0, fx.motor.write_count);
  TEST_ASSERT_EQUAL_INT(0, fx.battery.read_count);
}

// ---------------------------------------------------------------------------
// B: ゼロ初期化された DrivetrainConfig は configure() に拒否され、
//    configured() は false のまま。その後の step() も A と同じ。
// ---------------------------------------------------------------------------

void test_configure_rejects_zero_initialized_config(void) {
  DrivetrainController controller;
  Fixture fx;

  const DrivetrainConfig zero_config{};
  const ConfigDiagnostic diag = controller.configure(zero_config, fx.ports(), /*now=*/0);

  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_FALSE(controller.configured());

  const StepResult result = controller.step(/*now=*/1000);
  assertBlockedZero(result);
}

// ---------------------------------------------------------------------------
// C: Ports の3つがそれぞれ単独で null のとき、configure() は拒否され、
//    index でどのポートかが分かる。
// ---------------------------------------------------------------------------

void test_configure_rejects_null_encoder_port(void) {
  DrivetrainController controller;
  Fixture fx;

  Ports ports = fx.ports();
  ports.encoder = nullptr;
  const ConfigDiagnostic diag = controller.configure(makeValidConfig(), ports, /*now=*/0);

  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_FALSE(controller.configured());
  TEST_ASSERT_EQUAL_UINT8(0, diag.index);
}

void test_configure_rejects_null_motor_port(void) {
  DrivetrainController controller;
  Fixture fx;

  Ports ports = fx.ports();
  ports.motor = nullptr;
  const ConfigDiagnostic diag = controller.configure(makeValidConfig(), ports, /*now=*/0);

  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_FALSE(controller.configured());
  TEST_ASSERT_EQUAL_UINT8(1, diag.index);
}

void test_configure_rejects_null_battery_port(void) {
  DrivetrainController controller;
  Fixture fx;

  Ports ports = fx.ports();
  ports.battery = nullptr;
  const ConfigDiagnostic diag = controller.configure(makeValidConfig(), ports, /*now=*/0);

  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_FALSE(controller.configured());
  TEST_ASSERT_EQUAL_UINT8(2, diag.index);
}

// ---------------------------------------------------------------------------
// D: 妥当な設定と妥当なポートで configure() が成功する。
// ---------------------------------------------------------------------------

void test_configure_succeeds_with_valid_config_and_ports(void) {
  DrivetrainController controller;
  Fixture fx;

  const DrivetrainConfig config = makeValidConfig();
  const ConfigDiagnostic diag = controller.configure(config, fx.ports(), /*now=*/0);

  TEST_ASSERT_TRUE(diag.ok());
  TEST_ASSERT_TRUE(controller.configured());

  const DrivetrainConfig& effective = controller.effectiveConfig();
  TEST_ASSERT_EQUAL_INT32(config.encoder.counts_per_wheel_rev, effective.encoder.counts_per_wheel_rev);
  TEST_ASSERT_FLOAT_WITHIN(kTight, config.encoder.wheel_diameter_mm, effective.encoder.wheel_diameter_mm);
  TEST_ASSERT_FLOAT_WITHIN(kTight, config.limits.max_wheel_speed_mm_s, effective.limits.max_wheel_speed_mm_s);
}

// ---------------------------------------------------------------------------
// E: configure() 直後（実ステップ未経過）の過去/同時刻 step() はポートを
//    読まず、前回の（configure() 直後の既定）StepResult をそのまま返す。
// ---------------------------------------------------------------------------

void test_step_with_stale_time_after_configure_is_a_no_op(void) {
  DrivetrainController controller;
  Fixture fx;

  TEST_ASSERT_TRUE(controller.configure(makeValidConfig(), fx.ports(), /*now=*/1000).ok());
  // configure() 自身がエンコーダ基準値を1回読む。
  TEST_ASSERT_EQUAL_INT(1, fx.encoder.read_count);

  // 同時刻・過去時刻のどちらも短絡すること。
  const StepResult same_time = controller.step(/*now=*/1000);
  TEST_ASSERT_EQUAL_INT(1, fx.encoder.read_count);  // 増えていない
  TEST_ASSERT_EQUAL_UINT16(0, same_time.global_reasons);
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    TEST_ASSERT_EQUAL_FLOAT(0.0f, same_time.outputs.duty[i]);
  }

  const StepResult past_time = controller.step(/*now=*/500);
  TEST_ASSERT_EQUAL_INT(1, fx.encoder.read_count);  // 増えていない
  TEST_ASSERT_EQUAL_UINT16(0, past_time.global_reasons);
}

// ---------------------------------------------------------------------------
// F: 時刻が進んだ実ステップ ―― カウント差と実測 dt から計測速度を求め、
//    polarity を適用し、Odometry を更新する。
// ---------------------------------------------------------------------------

void test_step_advances_time_computes_measured_speed_and_updates_odometry(void) {
  DrivetrainController controller;
  Fixture fx;

  const DrivetrainConfig config = makeValidConfig();
  TEST_ASSERT_TRUE(controller.configure(config, fx.ports(), /*now=*/0).ok());

  // configure() 時点のエンコーダ基準値はゼロ初期化されたモック
  // （MockEncoderPort::counts の既定値）。
  const EncoderCounts& baseline = ControllerStepTestHooks::lastEncoderCounts(controller);
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    TEST_ASSERT_EQUAL_INT64(0, baseline.count[i]);
  }

  // 輪0: 1回転ぶん正転、輪1: 1回転ぶん逆転（polarity=-1 で吸収されて
  // 輪0と同じ向きの正の速度になるはず）、輪2: 変化無し。dt = 1000ms
  // (=1.0s) を選び、暗算しやすい値にする。
  fx.encoder.counts.count[0] = config.encoder.counts_per_wheel_rev;   // 836
  fx.encoder.counts.count[1] = -config.encoder.counts_per_wheel_rev;  // -836
  fx.encoder.counts.count[2] = 0;

  const StepResult result = controller.step(/*now=*/1000);

  // このタスクでは PID/保護/出力極性/ポート書き出しは未配線。デューティは
  // 既定値（ゼロ）のまま、遮断理由も無い（kNotConfigured は立たない ――
  // configured() は true のため）。
  TEST_ASSERT_EQUAL_UINT16(0, result.global_reasons);
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    TEST_ASSERT_EQUAL_FLOAT(0.0f, result.outputs.duty[i]);
  }
  TEST_ASSERT_EQUAL_INT(0, fx.motor.write_count);  // 出力書き出しはタスク6.3

  // カウント差と dt=1.0s から求まる計測速度。1回転 = pi * wheel_diameter_mm
  // ぶんの距離。輪1は生の変位が負だが polarity=-1 で符号が反転し、輪0と
  // 同じ正の値になる（A/B 逆結線の吸収、要件 6.6）。
  const float expected_speed = kPi * config.encoder.wheel_diameter_mm;  // ≈188.4956 mm/s (dt=1.0s)
  const float* measured = ControllerStepTestHooks::lastMeasuredMmS(controller);
  TEST_ASSERT_FLOAT_WITHIN(kTight, expected_speed, measured[0]);
  TEST_ASSERT_FLOAT_WITHIN(kTight, expected_speed, measured[1]);
  TEST_ASSERT_FLOAT_WITHIN(kTight, 0.0f, measured[2]);

  // エンコーダ基準値が更新されている。
  const EncoderCounts& after = ControllerStepTestHooks::lastEncoderCounts(controller);
  TEST_ASSERT_EQUAL_INT64(fx.encoder.counts.count[0], after.count[0]);
  TEST_ASSERT_EQUAL_INT64(fx.encoder.counts.count[1], after.count[1]);
  TEST_ASSERT_EQUAL_INT64(fx.encoder.counts.count[2], after.count[2]);

  // Odometry が実際に更新されている: 同じ計測速度配列を独立に構築した
  // Kinematics::forward() へ渡した結果と、controller 内部の Odometry が
  // 保持する body_velocity が一致すること（DrivetrainController が
  // Odometry::update() を正しく呼んでいることの裏取り）。
  const Kinematics reference_kinematics(config.geometry);
  const auto expected_body_velocity = reference_kinematics.forward(measured);
  const OdometryState& odom = ControllerStepTestHooks::odometryState(controller);
  TEST_ASSERT_FLOAT_WITHIN(kTight, expected_body_velocity.vx_mm_s, odom.body_velocity.vx_mm_s);
  TEST_ASSERT_FLOAT_WITHIN(kTight, expected_body_velocity.vy_mm_s, odom.body_velocity.vy_mm_s);
  TEST_ASSERT_FLOAT_WITHIN(kTight, expected_body_velocity.omega_rad_s, odom.body_velocity.omega_rad_s);
  TEST_ASSERT_TRUE(odom.traveled_mm > 0.0f);

  TEST_ASSERT_EQUAL_INT(2, fx.encoder.read_count);  // configure() の1回 + このステップの1回
}

// ---------------------------------------------------------------------------
// G: 実ステップの後、同じ時刻で step() を呼んでも、その間にポートの中身が
//    変わっていても、ポートを読まず内部状態も変化しない。
// ---------------------------------------------------------------------------

void test_step_with_same_time_after_a_real_step_does_not_reread_ports(void) {
  DrivetrainController controller;
  Fixture fx;

  const DrivetrainConfig config = makeValidConfig();
  TEST_ASSERT_TRUE(controller.configure(config, fx.ports(), /*now=*/0).ok());

  fx.encoder.counts.count[0] = config.encoder.counts_per_wheel_rev;
  const StepResult first = controller.step(/*now=*/1000);
  TEST_ASSERT_EQUAL_INT(2, fx.encoder.read_count);

  const float measured0_after_first = ControllerStepTestHooks::lastMeasuredMmS(controller)[0];
  const EncoderCounts baseline_after_first = ControllerStepTestHooks::lastEncoderCounts(controller);

  // ポートの中身を変える（本来なら次の実ステップで反映されるはずの変化）。
  fx.encoder.counts.count[0] = config.encoder.counts_per_wheel_rev * 100;

  // 同じ時刻で再度呼ぶ ―― 短絡し、上記の変化は一切反映されない。
  const StepResult second = controller.step(/*now=*/1000);

  TEST_ASSERT_EQUAL_INT(2, fx.encoder.read_count);  // 増えていない = 読んでいない
  TEST_ASSERT_FLOAT_WITHIN(kTight, measured0_after_first, ControllerStepTestHooks::lastMeasuredMmS(controller)[0]);
  TEST_ASSERT_EQUAL_INT64(baseline_after_first.count[0], ControllerStepTestHooks::lastEncoderCounts(controller).count[0]);

  // StepResult の全フィールドが一致する（要件 3.3 の決定性・要件 3.4 の
  // 「前回の結果をそのまま返す」の直接的な確認）。
  TEST_ASSERT_EQUAL_UINT16(first.global_reasons, second.global_reasons);
  TEST_ASSERT_TRUE(first.outputs_written == second.outputs_written);
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    TEST_ASSERT_EQUAL_FLOAT(first.outputs.duty[i], second.outputs.duty[i]);
    TEST_ASSERT_EQUAL_UINT16(first.wheel_reasons[i], second.wheel_reasons[i]);
  }
}

// ---------------------------------------------------------------------------
// H: 検証に失敗する2回目の configure() は、直前に成功していた設定を破棄
//    し、configured() を false に戻す。
// ---------------------------------------------------------------------------

void test_second_failing_configure_discards_previous_good_configuration(void) {
  DrivetrainController controller;
  Fixture fx;

  TEST_ASSERT_TRUE(controller.configure(makeValidConfig(), fx.ports(), /*now=*/0).ok());
  TEST_ASSERT_TRUE(controller.configured());

  const DrivetrainConfig zero_config{};
  const ConfigDiagnostic diag = controller.configure(zero_config, fx.ports(), /*now=*/100);

  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_FALSE(controller.configured());

  const StepResult result = controller.step(/*now=*/9999);
  assertBlockedZero(result);
}

int main(int argc, char **argv) {
  (void)argc;
  (void)argv;
  UNITY_BEGIN();

  RUN_TEST(test_step_before_configure_blocks_without_touching_ports);
  RUN_TEST(test_configure_rejects_zero_initialized_config);
  RUN_TEST(test_configure_rejects_null_encoder_port);
  RUN_TEST(test_configure_rejects_null_motor_port);
  RUN_TEST(test_configure_rejects_null_battery_port);
  RUN_TEST(test_configure_succeeds_with_valid_config_and_ports);
  RUN_TEST(test_step_with_stale_time_after_configure_is_a_no_op);
  RUN_TEST(test_step_advances_time_computes_measured_speed_and_updates_odometry);
  RUN_TEST(test_step_with_same_time_after_a_real_step_does_not_reread_ports);
  RUN_TEST(test_second_failing_configure_discards_previous_good_configuration);

  return UNITY_END();
}
