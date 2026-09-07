#include <unity.h>

#include <cstdint>

// drivetrain-core task 6.5 (design.md "PublicApi", L11, 要件 6.7, 17.5):
// 下流 Spec が参照する唯一のヘッダは drivetrain_control/drivetrain_control.hpp
// である。この観測可能な完了状態を「構造として」裏取りするため、本テスト
// ファイルは drivetrain_control/*.hpp のうち以下の1つだけを include する
// （⚠️ 他の drivetrain_control/*.hpp を1つでも追加 include すると、
// 「このヘッダだけで下流の一連の操作が完結する」という主張が崩れるため、
// 追加してはならない）。
#include "drivetrain_control/drivetrain_control.hpp"

// 以下を検証する（task 6.5 の観測可能な完了状態を正確に踏襲）:
//   設定（configure()）→ 指令投入（submit()）→ 制御ステップ（step()）→
//   状態読み出し（status()）の一連が、このヘッダだけを取り込んで実行できる
//   こと。
//
// 併せて、design.md "PublicApi" が明示的に除外する内部構造
// （Odometry / VelocityPid / 保護4部品 / ProtectionSupervisor /
// CommandInput の各個別ヘッダ）を本ファイルが一切 include していないこと
// は、上記の include リストそのものが証拠になる（このファイルは
// odometry.hpp / velocity_pid.hpp / protection/*.hpp / command_input.hpp の
// いずれも参照しない）。

using drivetrain_control::BatteryVoltagePort;
using drivetrain_control::BlockReason;
using drivetrain_control::BodyVelocityCommand;
using drivetrain_control::CommandAcceptance;
using drivetrain_control::ControlPath;
using drivetrain_control::DrivetrainConfig;
using drivetrain_control::DrivetrainController;
using drivetrain_control::DrivetrainStatus;
using drivetrain_control::EncoderCounts;
using drivetrain_control::EncoderPort;
using drivetrain_control::GeometryParams;
using drivetrain_control::kWheelCount;
using drivetrain_control::MotorOutputPort;
using drivetrain_control::Ports;
using drivetrain_control::Pose2D;
using drivetrain_control::StepResult;
using drivetrain_control::VoltageSample;
using drivetrain_control::WheelDutyCommand;
using drivetrain_control::WheelOutputs;
using drivetrain_control::WheelVelocityCommand;

namespace {

// --- ポート実装(下流Specが実際に書くべき最小の偽実装。EncoderPort /
// MotorOutputPort / BatteryVoltagePort は drivetrain_control.hpp が
// 再エクスポートする ports.hpp の抽象そのものであり、ここでの実装が
// コンパイルできること自体が「後続Specがポートを実装するために必要な
// 契約が実装を伴わずに揃っている」（要件17.5）ことの裏取りになる。
// -------------------------------------------------------------------------

class FakeEncoderPort : public EncoderPort {
 public:
  EncoderCounts counts{};
  EncoderCounts read() override { return counts; }
};

class FakeMotorPort : public MotorOutputPort {
 public:
  WheelOutputs last_written{};
  void write(const WheelOutputs& outputs) override { last_written = outputs; }
};

class FakeBatteryPort : public BatteryVoltagePort {
 public:
  VoltageSample sample{true, 12000};
  VoltageSample read() override { return sample; }
};

// 検証ロジックが正しく動くことだけを確かめるための架空の妥当値一式
// （実機の性能値ではない。要件 15.1, 15.2。test_controller_step.cpp の
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

}  // namespace

void setUp(void) {}
void tearDown(void) {}

// ---------------------------------------------------------------------------
// drivetrain_control.hpp だけを取り込んだ状態で、設定 → 指令投入 →
// 制御ステップ → 状態読み出しの一連を実行して緑になること。
// ---------------------------------------------------------------------------
void test_public_api_full_flow_configure_submit_step_status(void) {
  DrivetrainController controller;

  FakeEncoderPort encoder;
  FakeMotorPort motor;
  FakeBatteryPort battery;
  Ports ports;
  ports.encoder = &encoder;
  ports.motor = &motor;
  ports.battery = &battery;

  const DrivetrainConfig config = makeValidConfig();

  // 設定。
  const auto diagnostic = controller.configure(config, ports, /*now=*/0);
  TEST_ASSERT_TRUE(diagnostic.ok());
  TEST_ASSERT_TRUE(controller.configured());

  // 出力許可（submit() とは独立した入口、要件 5.6, 5.7）。
  controller.setOutputEnabled(true, /*now=*/0);

  // 指令投入: 輪単体指令。
  WheelVelocityCommand wheel_command{};
  wheel_command.wheel_mm_s[0] = 100.0f;
  wheel_command.wheel_mm_s[1] = 100.0f;
  wheel_command.wheel_mm_s[2] = 100.0f;
  wheel_command.issued_at_ms = 0;
  const CommandAcceptance wheel_acceptance = controller.submit(wheel_command);
  TEST_ASSERT_TRUE(wheel_acceptance.accepted);

  // 指令投入: 機体速度指令（同じ入口が両方の型を受け付けることの確認、
  // design.md "PublicApi" の再エクスポート型リストに BodyVelocityCommand /
  // WheelVelocityCommand の両方が含まれる理由）。過去時刻拒否（要件 5.4）を
  // 踏まえ、輪単体指令より後の時刻を与える。純回転（vx=vy=0）を選ぶのは、
  // 逆運動学が輪の取付角によらず3輪へ同一方向・同一大きさを与える
  // （要件 6.4）唯一の並進/回転の組み合わせであり、以降のアサーションを
  // 輪番号に依存させずに書けるため。
  BodyVelocityCommand body_command{};
  body_command.vx_mm_s = 0.0f;
  body_command.vy_mm_s = 0.0f;
  body_command.omega_rad_s = 2.0f;
  body_command.issued_at_ms = 5;
  const CommandAcceptance body_acceptance = controller.submit(body_command);
  TEST_ASSERT_TRUE(body_acceptance.accepted);

  // 制御ステップ。
  const StepResult step_result = controller.step(/*now=*/20);
  TEST_ASSERT_TRUE(step_result.outputs_written);

  // 状態読み出し。
  const DrivetrainStatus status = controller.status();
  TEST_ASSERT_TRUE(status.configured);
  TEST_ASSERT_TRUE(status.output_enabled);
  TEST_ASSERT_TRUE(status.has_valid_command);
  TEST_ASSERT_EQUAL_INT64(5, status.last_command_at_ms);
  // 直近の submit() は BodyVelocityCommand（vx=vy=0, omega=2.0 rad/s）で
  // あり、純回転なので3輪とも同じ目標速度になる（要件 6.4）。
  // base_radius_mm=150, omega=2.0 rad/s => 300 mm/s。
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    TEST_ASSERT_FLOAT_WITHIN(1.0e-2f, 300.0f, status.wheel_target_mm_s[i]);
  }

  // 保護解除・オドメトリ初期化も同じ公開面から到達できること（要件 8.3,
  // 14.6。configure() 後は no-op ではなく実際に ProtectionSupervisor /
  // Odometry へ委譲される経路であることを、クラッシュしないこと以上には
  // ここで主張しない ―― 詳細な振る舞いは各内部部品の個別テストの責務）。
  controller.resetProtections(/*now=*/20);
  controller.resetOdometry(Pose2D{}, /*now=*/20);
  const DrivetrainStatus status_after_reset = controller.status();
  TEST_ASSERT_EQUAL_FLOAT(0.0f, status_after_reset.odometry.pose.x_mm);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, status_after_reset.odometry.pose.y_mm);
}

// ---------------------------------------------------------------------------
// configure() 前の submit()/setOutputEnabled()/resetProtections()/
// resetOdometry() は、未構築の内部合成オブジェクトへ一切触れず安全に
// 短絡すること（status() が既に同じ方針を持つことと一貫させる、
// controller.hpp 冒頭コメント参照）。
// ---------------------------------------------------------------------------
void test_public_api_entry_points_are_safe_before_configure(void) {
  DrivetrainController controller;
  TEST_ASSERT_FALSE(controller.configured());

  WheelVelocityCommand wheel_command{};
  wheel_command.wheel_mm_s[0] = 10.0f;
  wheel_command.issued_at_ms = 1;
  const CommandAcceptance acceptance = controller.submit(wheel_command);
  TEST_ASSERT_FALSE(acceptance.accepted);

  controller.setOutputEnabled(true, /*now=*/1);
  controller.resetProtections(/*now=*/1);
  controller.resetOdometry(Pose2D{}, /*now=*/1);

  const StepResult step_result = controller.step(/*now=*/1);
  const auto kNotConfiguredBit = static_cast<std::uint16_t>(BlockReason::kNotConfigured);
  TEST_ASSERT_EQUAL_UINT16(kNotConfiguredBit, step_result.global_reasons);
}

// ---------------------------------------------------------------------------
// タスク 10.3（design.md "PublicApi" 再エクスポート対象へ WheelDutyCommand /
// ControlPath を追加、要件 5.10, 5.15）の観測可能な完了状態: このヘッダ
// だけを取り込んだ翻訳単位から開ループ指令（WheelDutyCommand）を構築して
// submit() へ投入し、step() を経て status().control_path が
// ControlPath::kOpenLoop になることを確認できること。
// ---------------------------------------------------------------------------
void test_public_api_open_loop_command_reports_control_path(void) {
  DrivetrainController controller;

  FakeEncoderPort encoder;
  FakeMotorPort motor;
  FakeBatteryPort battery;
  Ports ports;
  ports.encoder = &encoder;
  ports.motor = &motor;
  ports.battery = &battery;

  const DrivetrainConfig config = makeValidConfig();

  const auto diagnostic = controller.configure(config, ports, /*now=*/0);
  TEST_ASSERT_TRUE(diagnostic.ok());

  controller.setOutputEnabled(true, /*now=*/0);

  // 開ループ指令: 輪ごとの目標出力を直接与える（速度PID を経由しない、
  // 要件 5.10）。
  WheelDutyCommand duty_command{};
  duty_command.wheel_duty[0] = 0.1f;
  duty_command.wheel_duty[1] = 0.1f;
  duty_command.wheel_duty[2] = 0.1f;
  duty_command.issued_at_ms = 0;
  const CommandAcceptance acceptance = controller.submit(duty_command);
  TEST_ASSERT_TRUE(acceptance.accepted);

  const StepResult step_result = controller.step(/*now=*/20);
  TEST_ASSERT_TRUE(step_result.outputs_written);

  // 経路の判別（要件 5.15）: 直近に投入したのは開ループ指令なので
  // control_path は kOpenLoop になる。
  const DrivetrainStatus status = controller.status();
  TEST_ASSERT_TRUE(status.has_valid_command);
  const auto kOpenLoopValue = static_cast<std::uint8_t>(ControlPath::kOpenLoop);
  TEST_ASSERT_EQUAL_UINT8(kOpenLoopValue, static_cast<std::uint8_t>(status.control_path));
}

int main(int argc, char** argv) {
  (void)argc;
  (void)argv;
  UNITY_BEGIN();
  RUN_TEST(test_public_api_full_flow_configure_submit_step_status);
  RUN_TEST(test_public_api_entry_points_are_safe_before_configure);
  RUN_TEST(test_public_api_open_loop_command_reports_control_path);
  return UNITY_END();
}
