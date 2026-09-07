#include <unity.h>

#include <cmath>
#include <cstdint>

#include "drivetrain_control/config.hpp"
#include "drivetrain_control/controller.hpp"
#include "drivetrain_control/kinematics.hpp"
#include "drivetrain_control/ports.hpp"
#include "drivetrain_control/types.hpp"
#include "test_support/fake_ports.hpp"
#include "test_support/plant_coefficients.hpp"
#include "test_support/wheel_plant.hpp"

// drivetrain-core task 7.3: 結合の振る舞いと決定性を検証するホストテスト
// （design.md "Testing Strategy" § Integration Tests
// `firmware/test/native/`）。
//
// design.md が列挙する Integration Tests は7項目あるが、本タスク
// （tasks.md 7.3）が要求するのはそのうち6項目である（3番目の「保護の合成と
// 理由の判別」―― 低電圧と1輪ロックを同時に成立させ理由が正しく分かれる
// こと ―― は task 5.5 の test_protection_supervisor.cpp
// （test_compose_separates_global_low_voltage_from_per_wheel_lock）が
// ProtectionSupervisor 単体レベルで既に検証済みであり、tasks.md 7.3 の
// 6つの箇条書きにも対応する項目が無いため、本ファイルでは扱わない）。
//
// 本ファイルが対象にする6項目は、いずれも task 6.1/6.3/6.4/6.5/5.5 の
// 既存テストと「一見似た」検証をするが、レベルが異なる:
//   - task 6.1 の test_command_input.cpp は CommandInput 単体（構造）。
//   - task 6.3/6.4 の test_controller_step.cpp は REAL DrivetrainController
//     だが、MockEncoderPort/MockMotorPort/MockBatteryPort という軽量な
//     記録専用スタブ（呼び出し回数と最後の値だけを覚える）を挿している。
//   - task 5.5 の test_protection_supervisor.cpp は ProtectionSupervisor
//     単体（DrivetrainController を経由しない）。
//   - 本ファイルは REAL DrivetrainController を REAL
//     test_support::FakeEncoderPort/FakeMotorPort/FakeBatteryPort/
//     WheelPlant（task 7.1）へ配線し、閉ループ（task 7.2 と同じ配線
//     パターン）を回しながら、「振る舞いの一致」「決定性」「呼び出し
//     順序」「反映遅れ」を確認する ―― tasks.md 7.3 の
//     `_Boundary: NativeTestSuite integration_` が要求する、他のどの
//     タスクのテストとも異なる最上位の結合レベルである。
//
// ⚠️ test_controller_closed_loop.cpp と同じ注記: ここで使う
// test_support::PlantCoefficients は「合否条件ではない仮値」であり実測値
// ではない（要件 16.2, 16.4, 16.6）。本ファイルのどのテストも収束時間・
// 具体的なゲイン値そのものを合否条件にしない。

using drivetrain_control::BlockMask;
using drivetrain_control::BlockReason;
using drivetrain_control::BodyVelocity;
using drivetrain_control::BodyVelocityCommand;
using drivetrain_control::ControlPath;
using drivetrain_control::DrivetrainConfig;
using drivetrain_control::DrivetrainController;
using drivetrain_control::DrivetrainStatus;
using drivetrain_control::DurationMs;
using drivetrain_control::GeometryParams;
using drivetrain_control::Kinematics;
using drivetrain_control::kWheelCount;
using drivetrain_control::Ports;
using drivetrain_control::PwmCeiling;
using drivetrain_control::scaleToLimit;
using drivetrain_control::StepResult;
using drivetrain_control::TimeMs;
using drivetrain_control::VoltageSample;
using drivetrain_control::WheelDutyCommand;
using drivetrain_control::WheelVelocityCommand;
using test_support::FakeBatteryPort;
using test_support::FakeEncoderPort;
using test_support::FakeMotorPort;
using test_support::PlantCoefficients;

namespace {

// test_controller_step.cpp / test_controller_closed_loop.cpp の
// makeValidConfig() と同じ流儀（検証ロジックが正しく動くことだけを確かめる
// ための架空の妥当値一式。実機の性能値ではない。要件 15.1, 15.2）。各テスト
// は必要なフィールドだけを上書きする。
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
    config.pid[i].ki = 0.0005f;
    config.pid[i].kd = 0.0f;
    config.pid[i].integral_limit = 1.0f;
  }

  // ロック・ウォッチドッグ・低電圧は既定でこのファイルの各シナリオの関心を
  // 横取りしない値にしておき、各テストが必要なものだけ上書きする
  // （test_controller_closed_loop.cpp と同じ方針）。
  config.lock.duty_threshold = 0.95f;
  config.lock.speed_threshold_mm_s = 10.0f;
  config.lock.duration_ms = 300;
  config.lock.clear_duration_ms = 200;
  config.lock.latching = true;

  config.low_voltage.warn_milli_volts = 9000;
  config.low_voltage.stop_milli_volts = 8000;
  config.low_voltage.recover_milli_volts = 8500;
  config.low_voltage.average_window = 4;
  config.low_voltage.stop_duration_ms = 200;
  config.low_voltage.unavailable_duration_ms = 500;
  config.low_voltage.latching = true;

  config.voltage_scaler.table[0] = {0, 0};
  config.voltage_scaler.table[1] = {2048, 6000};
  config.voltage_scaler.table[2] = {4095, 12600};
  config.voltage_scaler.point_count = 3;

  config.pwm_ceiling.enabled = false;
  config.pwm_ceiling.reference_milli_volts = 12600;
  config.pwm_ceiling.fallback_duty = 0.2f;
  config.pwm_ceiling.override_fn = nullptr;

  config.watchdog.timeout_ms = 100000;

  return config;
}

// このファイル専用のプラント係数（仮値。ファイル先頭の宣言を参照）。
PlantCoefficients makeCoefficients() {
  PlantCoefficients c{};
  c.duty_to_steady_mm_s = 500.0f;
  c.time_constant_ms = 100.0f;
  return c;
}

struct IntegrationFixture {
  FakeEncoderPort encoder;
  FakeMotorPort motor;
  FakeBatteryPort battery;

  IntegrationFixture(const PlantCoefficients& coeffs, const DrivetrainConfig& config)
      : encoder(coeffs, config.encoder.counts_per_wheel_rev, config.encoder.wheel_diameter_mm,
                config.encoder.raw_modulus) {}

  Ports ports() {
    Ports p;
    p.encoder = &encoder;
    p.motor = &motor;
    p.battery = &battery;
    return p;
  }

  // 直近の step() が書き出したデューティで3輪すべての WheelPlant を進める
  // （test_controller_closed_loop.cpp の advanceAllWheels() と同じ実体）。
  void advanceAllWheels(DurationMs dt_ms) {
    const auto& applied = motor.lastOutputs();
    for (std::uint8_t w = 0; w < kWheelCount; ++w) {
      encoder.plant(w).advance(applied.duty[w], /*supply_ratio=*/1.0f, dt_ms);
    }
  }
};

// StepResult の全フィールドが一致することを確認する（要件 3.3, 16.5）。
void assertStepResultEqual(const StepResult& a, const StepResult& b) {
  TEST_ASSERT_EQUAL_UINT16(a.global_reasons, b.global_reasons);
  for (std::uint8_t w = 0; w < kWheelCount; ++w) {
    TEST_ASSERT_EQUAL_FLOAT(a.outputs.duty[w], b.outputs.duty[w]);
    TEST_ASSERT_EQUAL_UINT16(a.wheel_reasons[w], b.wheel_reasons[w]);
  }
  TEST_ASSERT_TRUE(a.outputs_written == b.outputs_written);
}

// DrivetrainStatus の全フィールドが一致することを確認する（要件 3.3,
// 16.5。effectiveConfig() は DrivetrainStatus のフィールドではないため
// 対象外 ―― 両コントローラへ同一の DrivetrainConfig を configure() 済み
// であることは呼び出し側が別途保証する）。
void assertStatusEqual(const DrivetrainStatus& a, const DrivetrainStatus& b) {
  TEST_ASSERT_EQUAL_INT64(a.now_ms, b.now_ms);
  TEST_ASSERT_TRUE(a.configured == b.configured);
  TEST_ASSERT_TRUE(a.output_enabled == b.output_enabled);
  TEST_ASSERT_TRUE(a.has_valid_command == b.has_valid_command);
  TEST_ASSERT_TRUE(a.last_command_clamped == b.last_command_clamped);
  TEST_ASSERT_EQUAL_INT64(a.last_command_at_ms, b.last_command_at_ms);

  for (std::uint8_t w = 0; w < kWheelCount; ++w) {
    TEST_ASSERT_EQUAL_FLOAT(a.wheel_target_mm_s[w], b.wheel_target_mm_s[w]);
    TEST_ASSERT_EQUAL_FLOAT(a.wheel_measured_mm_s[w], b.wheel_measured_mm_s[w]);
    TEST_ASSERT_EQUAL_FLOAT(a.wheel_duty[w], b.wheel_duty[w]);
    TEST_ASSERT_EQUAL_INT64(a.encoder_count[w], b.encoder_count[w]);
    TEST_ASSERT_EQUAL_UINT16(a.wheel_reasons[w], b.wheel_reasons[w]);
  }

  TEST_ASSERT_EQUAL_FLOAT(a.pwm_ceiling, b.pwm_ceiling);
  TEST_ASSERT_EQUAL_INT32(a.battery_milli_volts, b.battery_milli_volts);
  TEST_ASSERT_TRUE(a.battery_valid == b.battery_valid);
  TEST_ASSERT_TRUE(a.low_voltage_state == b.low_voltage_state);

  TEST_ASSERT_EQUAL_UINT16(a.global_reasons, b.global_reasons);

  TEST_ASSERT_EQUAL_FLOAT(a.odometry.pose.x_mm, b.odometry.pose.x_mm);
  TEST_ASSERT_EQUAL_FLOAT(a.odometry.pose.y_mm, b.odometry.pose.y_mm);
  TEST_ASSERT_EQUAL_FLOAT(a.odometry.pose.theta_rad, b.odometry.pose.theta_rad);
  TEST_ASSERT_EQUAL_FLOAT(a.odometry.body_velocity.vx_mm_s, b.odometry.body_velocity.vx_mm_s);
  TEST_ASSERT_EQUAL_FLOAT(a.odometry.body_velocity.vy_mm_s, b.odometry.body_velocity.vy_mm_s);
  TEST_ASSERT_EQUAL_FLOAT(a.odometry.body_velocity.omega_rad_s, b.odometry.body_velocity.omega_rad_s);
  TEST_ASSERT_EQUAL_FLOAT(a.odometry.traveled_mm, b.odometry.traveled_mm);
  TEST_ASSERT_EQUAL_INT32(a.odometry.since_reset_ms, b.odometry.since_reset_ms);

  TEST_ASSERT_EQUAL_INT32(a.last_step_interval_ms, b.last_step_interval_ms);
  TEST_ASSERT_EQUAL_INT32(a.command_apply_latency_ms, b.command_apply_latency_ms);
}

}  // namespace

void setUp(void) {}
void tearDown(void) {}

// ---------------------------------------------------------------------------
// 1. 輪単体指令と機体速度指令の振る舞い一致（design.md Integration Tests
//    §1、要件 5.9, 13.1, 13.4, 13.5, 13.6, 14.3, 14.4, 14.9, 14.10）。
//
//    task 6.1 の test_body_and_wheel_entry_points_converge_to_same_wheel_targets
//    は CommandInput 単体で WheelTargets が一致することだけを見た（構造の
//    裏取り）。ここでは REAL DrivetrainController・REAL 保護①〜④・REAL
//    VelocityPid・REAL WheelPlant まで通した StepResult/DrivetrainStatus
//    の全フィールドの系列が、複数ステップ・変動する電圧のもとで完全に
//    一致することを確認する（振る舞いの裏取り）。
// ---------------------------------------------------------------------------

void test_body_and_wheel_command_entry_produce_identical_traces_through_full_stack(void) {
  const DrivetrainConfig config = makeValidConfig();
  const PlantCoefficients coeffs = makeCoefficients();

  // A: 機体速度指令入口。
  DrivetrainController controller_body;
  IntegrationFixture fx_body(coeffs, config);
  TEST_ASSERT_TRUE(controller_body.configure(config, fx_body.ports(), /*now=*/0).ok());
  fx_body.battery.setSample(12600);
  controller_body.setOutputEnabled(true, /*now=*/0);

  BodyVelocityCommand body_command{};
  body_command.vx_mm_s = 180.0f;
  body_command.vy_mm_s = 60.0f;
  body_command.omega_rad_s = 0.4f;
  body_command.issued_at_ms = 0;
  TEST_ASSERT_TRUE(controller_body.submit(body_command).accepted);
  TEST_ASSERT_FALSE(controller_body.status().last_command_clamped);  // クランプが混ざると本テストの
                                                                       // 前提（等価な輪速度）が崩れる

  // 機体速度指令が生む輪目標速度を、独立に構築した Kinematics::inverse()
  // （CommandInput 内部が使うのと全く同じ関数、要件 6.1）で計算する。
  const Kinematics reference_kinematics(config.geometry);
  float equivalent_wheel_mm_s[kWheelCount] = {0.0f, 0.0f, 0.0f};
  reference_kinematics.inverse(BodyVelocity{body_command.vx_mm_s, body_command.vy_mm_s, body_command.omega_rad_s},
                                equivalent_wheel_mm_s);
  for (std::uint8_t w = 0; w < kWheelCount; ++w) {
    TEST_ASSERT_TRUE(std::fabs(equivalent_wheel_mm_s[w]) < config.limits.max_wheel_speed_mm_s);
  }

  // B: 輪単体指令入口 ―― 上で求めた「機体速度指令が生む輪目標速度」と
  //    厳密に同じ値を直接投入する。
  DrivetrainController controller_wheel;
  IntegrationFixture fx_wheel(coeffs, config);
  TEST_ASSERT_TRUE(controller_wheel.configure(config, fx_wheel.ports(), /*now=*/0).ok());
  fx_wheel.battery.setSample(12600);
  controller_wheel.setOutputEnabled(true, /*now=*/0);

  WheelVelocityCommand wheel_command{};
  for (std::uint8_t w = 0; w < kWheelCount; ++w) {
    wheel_command.wheel_mm_s[w] = equivalent_wheel_mm_s[w];
  }
  wheel_command.issued_at_ms = 0;
  TEST_ASSERT_TRUE(controller_wheel.submit(wheel_command).accepted);
  TEST_ASSERT_FALSE(controller_wheel.status().last_command_clamped);

  // 土台の確認: 両者へ渡った輪目標速度そのものが厳密一致する（このテストの
  // 前提が壊れていないことの直接確認。以降のループはこの上に立つ）。
  {
    const DrivetrainStatus sa = controller_body.status();
    const DrivetrainStatus sb = controller_wheel.status();
    for (std::uint8_t w = 0; w < kWheelCount; ++w) {
      TEST_ASSERT_EQUAL_FLOAT(sa.wheel_target_mm_s[w], sb.wheel_target_mm_s[w]);
    }
  }

  // 両方の閉ループを完全に同一の時刻列・電圧列で回し、毎ステップ
  // StepResult と DrivetrainStatus の全フィールドが一致することを確認する
  // ―― 「以降の保護・PID・遮断の振る舞いが完全に一致する」ことを、構造
  // （同じコード経路を通る）ではなく実際の出力系列の一致として裏取りする。
  constexpr int kSteps = 150;
  constexpr DurationMs kDt = 20;
  TimeMs now = 0;

  for (int i = 0; i < kSteps; ++i) {
    now += kDt;

    // 電圧の時系列もわずかに揺らしつつ両方へ同一に注入する（PWM上限相当の
    // 分岐が仮に有効でも両者が同じ分岐を踏む。このテストでは
    // pwm_ceiling.enabled=false のため実際には効かないが、注入経路自体を
    // 分岐点として残さない）。
    const std::int32_t mv = 12600 - (i % 20) * 5;
    fx_body.battery.setSample(mv);
    fx_wheel.battery.setSample(mv);

    const StepResult result_body = controller_body.step(now);
    const StepResult result_wheel = controller_wheel.step(now);

    fx_body.advanceAllWheels(kDt);
    fx_wheel.advanceAllWheels(kDt);

    assertStepResultEqual(result_body, result_wheel);
    assertStatusEqual(controller_body.status(), controller_wheel.status());
  }
}

// ---------------------------------------------------------------------------
// 2. ウォッチドッグのタイムアウト遮断と出力許可の独立性（design.md
//    Integration Tests §2、要件 13.1, 13.4, 13.6）。
//
//    指令を止めて時刻だけを進め、timeout_ms を超えた時点で出力が遮断値に
//    なること。新しい指令でウォッチドッグは解除されるが、出力許可が
//    無ければ出力が出ないこと（ウォッチドッグの解除と出力許可は独立した
//    2つのゲートである）。
// ---------------------------------------------------------------------------

void test_watchdog_timeout_blocks_and_new_command_stays_blocked_without_output_enable(void) {
  DrivetrainController controller;
  DrivetrainConfig config = makeValidConfig();
  config.watchdog.timeout_ms = 200;
  const PlantCoefficients coeffs = makeCoefficients();
  IntegrationFixture fx(coeffs, config);

  TEST_ASSERT_TRUE(controller.configure(config, fx.ports(), /*now=*/0).ok());
  fx.battery.setSample(12600);
  controller.setOutputEnabled(true, /*now=*/0);

  WheelVelocityCommand command{};
  command.wheel_mm_s[0] = 100.0f;
  command.wheel_mm_s[1] = 100.0f;
  command.wheel_mm_s[2] = 100.0f;
  command.issued_at_ms = 0;
  TEST_ASSERT_TRUE(controller.submit(command).accepted);

  const BlockMask kTimeoutBit = static_cast<BlockMask>(BlockReason::kCommandTimeout);
  const BlockMask kOutputDisabledBit = static_cast<BlockMask>(BlockReason::kOutputDisabled);

  // タイムアウト(200ms)以内: 出力が実際に出ていることを先に確認する
  // （このテストが何も検証していない状態を避ける）。
  const StepResult before_timeout = controller.step(/*now=*/100);
  fx.advanceAllWheels(100);
  bool any_nonzero = false;
  for (std::uint8_t w = 0; w < kWheelCount; ++w) {
    if (std::fabs(before_timeout.outputs.duty[w]) > 1.0e-6f) {
      any_nonzero = true;
    }
  }
  TEST_ASSERT_TRUE(any_nonzero);
  TEST_ASSERT_EQUAL_UINT16(0, before_timeout.global_reasons & kTimeoutBit);

  // 指令を止めて（submit() を一切呼ばずに）時刻だけを進める。timeout_ms を
  // 厳密に超えた時点で遮断値になる。
  const StepResult after_timeout = controller.step(/*now=*/300);  // 300 - 0(issued_at_ms) = 300 > 200
  fx.advanceAllWheels(200);
  TEST_ASSERT_TRUE((after_timeout.global_reasons & kTimeoutBit) != 0);
  for (std::uint8_t w = 0; w < kWheelCount; ++w) {
    TEST_ASSERT_EQUAL_FLOAT(0.0f, after_timeout.outputs.duty[w]);
  }

  // 出力許可を明示的に剥奪してから、新しい指令を投入する（ウォッチドッグ
  // だけを解除し、出力許可ゲートは意図的に閉じたままにする）。
  controller.setOutputEnabled(false, /*now=*/300);

  WheelVelocityCommand new_command{};
  new_command.wheel_mm_s[0] = 100.0f;
  new_command.wheel_mm_s[1] = 100.0f;
  new_command.wheel_mm_s[2] = 100.0f;
  new_command.issued_at_ms = 300;
  TEST_ASSERT_TRUE(controller.submit(new_command).accepted);

  const StepResult after_new_command = controller.step(/*now=*/350);  // 350-300=50 < timeout(200): 解除済み

  // ウォッチドッグは解除されている（kCommandTimeout は立たない）が、
  // 出力許可が無いため kOutputDisabled により出力は依然として遮断値
  // ―― 「新しい指令で解除されても出力許可が無ければ出力が出ない」の
  // 直接確認（要件 13.6）。
  TEST_ASSERT_EQUAL_UINT16(0, after_new_command.global_reasons & kTimeoutBit);
  TEST_ASSERT_EQUAL_UINT16(kOutputDisabledBit, after_new_command.global_reasons);
  for (std::uint8_t w = 0; w < kWheelCount; ++w) {
    TEST_ASSERT_EQUAL_FLOAT(0.0f, after_new_command.outputs.duty[w]);
  }
}

// ---------------------------------------------------------------------------
// 3. 設定直後の指令未着状態と、指令はあるが出力未許可の状態が、それぞれ
//    別の遮断理由で遮断値になる（design.md Integration Tests §4、要件
//    14.9, 14.10, 5.6）。
// ---------------------------------------------------------------------------

void test_no_command_yet_and_output_disabled_are_separate_reasons_right_after_configure(void) {
  DrivetrainController controller;
  DrivetrainConfig config = makeValidConfig();
  config.watchdog.timeout_ms = 100000;  // ウォッチドッグの発火はこのテストの関心外へ離す
  const PlantCoefficients coeffs = makeCoefficients();
  IntegrationFixture fx(coeffs, config);

  TEST_ASSERT_TRUE(controller.configure(config, fx.ports(), /*now=*/0).ok());
  fx.battery.setSample(12600);
  // 出力許可も指令も一切与えていない。

  const BlockMask kNoCommandYetBit = static_cast<BlockMask>(BlockReason::kNoCommandYet);
  const BlockMask kOutputDisabledBit = static_cast<BlockMask>(BlockReason::kOutputDisabled);

  // 設定直後・指令未着状態: kNoCommandYet が立つ（出力許可も無いため
  // kOutputDisabled も同時に立つ ―― 両方が独立したビットとして共存できる
  // ことは §2/このテスト後半で「片方だけが立つ」状態と対比して確認する）。
  const StepResult before_submit = controller.step(/*now=*/100);
  fx.advanceAllWheels(100);
  TEST_ASSERT_TRUE((before_submit.global_reasons & kNoCommandYetBit) != 0);
  for (std::uint8_t w = 0; w < kWheelCount; ++w) {
    TEST_ASSERT_EQUAL_FLOAT(0.0f, before_submit.outputs.duty[w]);
  }

  // 指令はある（出力許可は与えないまま）: kNoCommandYet はもう立たず、
  // kOutputDisabled だけが理由として残る ―― 「指令未着」と「出力未許可」
  // が構造ではなく実際に区別可能な別ビットであることの直接確認。
  WheelVelocityCommand command{};
  command.wheel_mm_s[0] = 100.0f;
  command.wheel_mm_s[1] = 100.0f;
  command.wheel_mm_s[2] = 100.0f;
  command.issued_at_ms = 150;
  TEST_ASSERT_TRUE(controller.submit(command).accepted);

  const StepResult after_submit = controller.step(/*now=*/200);
  fx.advanceAllWheels(100);
  TEST_ASSERT_EQUAL_UINT16(0, after_submit.global_reasons & kNoCommandYetBit);
  TEST_ASSERT_EQUAL_UINT16(kOutputDisabledBit, after_submit.global_reasons);
  for (std::uint8_t w = 0; w < kWheelCount; ++w) {
    TEST_ASSERT_EQUAL_FLOAT(0.0f, after_submit.outputs.duty[w]);
  }

  TEST_ASSERT_NOT_EQUAL(kNoCommandYetBit, kOutputDisabledBit);
}

// ---------------------------------------------------------------------------
// 4. 決定性（design.md Integration Tests §5、要件 3.3, 16.5）。
//
//    2つの独立した DrivetrainController + 独立した
//    FakeEncoderPort/FakeMotorPort/FakeBatteryPort + 独立した WheelPlant を
//    同一の時刻列・指令列・電圧列（さらに同一のストール注入）で回し、
//    StepResult と DrivetrainStatus の全フィールドが毎ステップ一致することを
//    確認する（「同一プラットフォーム上」の決定性 ―― ホストと実機の
//    ビット一致は主張しない。design.md Open Questions #6, 要件 3.3/16.5 の
//    文言どおりの範囲）。保護①（ロック）・保護②（低電圧）の遷移を実際に
//    踏ませることで、保護の内部状態遷移を含めた決定性を確認する。
// ---------------------------------------------------------------------------

void test_two_independent_controllers_given_identical_script_produce_identical_traces(void) {
  DrivetrainConfig config = makeValidConfig();
  config.lock.duty_threshold = 0.3f;
  config.lock.speed_threshold_mm_s = 10.0f;
  config.lock.duration_ms = 150;
  config.lock.clear_duration_ms = 100;
  config.lock.latching = true;
  config.low_voltage.warn_milli_volts = 10800;
  config.low_voltage.stop_milli_volts = 10200;
  config.low_voltage.recover_milli_volts = 10500;
  config.low_voltage.average_window = 1;
  config.low_voltage.stop_duration_ms = 150;
  config.low_voltage.unavailable_duration_ms = 100000;
  config.low_voltage.latching = false;
  const PlantCoefficients coeffs = makeCoefficients();

  DrivetrainController controller_a;
  DrivetrainController controller_b;
  IntegrationFixture fx_a(coeffs, config);
  IntegrationFixture fx_b(coeffs, config);

  TEST_ASSERT_TRUE(controller_a.configure(config, fx_a.ports(), /*now=*/0).ok());
  TEST_ASSERT_TRUE(controller_b.configure(config, fx_b.ports(), /*now=*/0).ok());

  fx_a.battery.setSample(12600);
  fx_b.battery.setSample(12600);
  controller_a.setOutputEnabled(true, /*now=*/0);
  controller_b.setOutputEnabled(true, /*now=*/0);

  WheelVelocityCommand command{};
  command.wheel_mm_s[0] = 300.0f;
  command.wheel_mm_s[1] = 300.0f;
  command.wheel_mm_s[2] = 300.0f;
  command.issued_at_ms = 0;
  TEST_ASSERT_TRUE(controller_a.submit(command).accepted);
  TEST_ASSERT_TRUE(controller_b.submit(command).accepted);

  // 初期状態の一致から確認する（configure() 直後、まだ1回も step() して
  // いない状態）。
  assertStatusEqual(controller_a.status(), controller_b.status());

  constexpr int kSteps = 60;
  constexpr DurationMs kDt = 50;
  constexpr std::uint8_t kStalledWheel = 1;
  constexpr int kStallStartStep = 10;  // 輪1を拘束し始める（保護①の遷移を踏ませる）
  constexpr int kStallEndStep = 25;    // 拘束を解く
  constexpr int kVoltageDipStartStep = 30;  // 電圧を下げる（保護②の遷移を踏ませる）
  constexpr int kVoltageDipEndStep = 45;    // 電圧を戻す

  TimeMs now = 0;

  for (int i = 1; i <= kSteps; ++i) {
    now += kDt;

    if (i == kStallStartStep) {
      fx_a.encoder.plant(kStalledWheel).setStalled(true);
      fx_b.encoder.plant(kStalledWheel).setStalled(true);
    }
    if (i == kStallEndStep) {
      fx_a.encoder.plant(kStalledWheel).setStalled(false);
      fx_b.encoder.plant(kStalledWheel).setStalled(false);
    }

    const std::int32_t mv = (i >= kVoltageDipStartStep && i < kVoltageDipEndStep) ? 9800 : 12600;
    fx_a.battery.setSample(mv);
    fx_b.battery.setSample(mv);

    const StepResult result_a = controller_a.step(now);
    const StepResult result_b = controller_b.step(now);

    fx_a.advanceAllWheels(kDt);
    fx_b.advanceAllWheels(kDt);

    assertStepResultEqual(result_a, result_b);
    assertStatusEqual(controller_a.status(), controller_b.status());
  }
}

// ---------------------------------------------------------------------------
// 5. Supervisor 呼び出し順序の回帰（design.md Integration Tests §6、要件
//    11.2, 11.5, 14.7）。
//
//    ⚠️ 設計上のトレードオフ（tasks.md 7.3 のタスクブリーフ参照）:
//    design.md はこの項目を `protection_supervisor/`（ProtectionSupervisor
//    単体、task 5.5 の境界）に位置づけている。`DrivetrainController::
//    step()` は既に task 6.3 で `updateLowVoltage()`/`updateWatchdog()`/
//    `updateLock()` をステップにつきちょうど1回ずつ呼ぶ実装が確定して
//    おり、本タスクの境界（DrivetrainController を直接叩かず、
//    ProtectionSupervisor を直接操作しない）からは、その呼び出し回数を
//    直接注入・改変することはできない。
//
//    さらに `LowVoltageProtector::update()`（low_voltage.cpp）の継続時間
//    判定は「呼ばれた回数」ではなく「渡された絶対時刻 now と、初回に
//    条件が成立した時刻 low_voltage_started_at_ms_ との差」で計算する
//    （タイムスタンプ基準）。そのため「同一の now を渡して2回呼ぶ」だけの
//    誤用は、この実装に対しては元々無害である（2回目の呼び出しは
//    low_voltage_timer_active_ が既に true なので何も変えない）。
//    実際に継続時間を実時間より早く進めうる誤用は、「本来1ステップに
//    つき1回・そのステップの真の now で呼ぶべきところを、真の経過時間と
//    無関係な回数・タイミングで呼ぶ」ことである。
//
//    そこで本テストは、tasks.md 7.3 が提示する選択肢(a)を採用する:
//    既知の `stop_duration_ms` と既知の `dt_ms` を使い、
//    `DrivetrainController::step()` を既知の回数だけ呼んだとき、低電圧の
//    発火が「真の経過時間ちょうどの境界」で起きることを確認する。
//    `now - low_voltage_started_at_ms_ > stop_duration_ms`（厳密に超える、
//    low_voltage.cpp 参照）という境界を、実際の `step()` 呼び出し回数と
//    突き合わせて検証することで、`updateLowVoltage()` が「1ステップに
//    つきちょうど1回・そのステップの真の now で」呼ばれていることの
//    間接証拠とする。もし将来の回帰で1ステップにつき2回（同一 now で）
//    呼ばれるようになっても実害は無い（上記のタイムスタンプ基準の理由に
//    より）が、もし「呼び出し回数を継続時間の単位として扱う」実装
//    （call-count 基準）へ変質した場合は、このテストが要求する「真の
//    経過時間ちょうどの境界」からずれて失敗する。
//
//    task 5.5 の test_protection_supervisor.cpp
//    （test_compose_does_not_advance_detector_timers）は同じ関心を
//    ProtectionSupervisor 単体・compose() の const 性の観点から既に
//    検証済みである（compose() は検出器を進められない、というコンパイル
//    時に保証された不変条件）。本テストはそれを補完する、
//    DrivetrainController を通した「時間ベースの境界」の直接確認である。
// ---------------------------------------------------------------------------

void test_low_voltage_trip_timing_matches_real_elapsed_time_through_full_controller_step(void) {
  DrivetrainController controller;
  DrivetrainConfig config = makeValidConfig();
  config.low_voltage.warn_milli_volts = 10800;
  config.low_voltage.stop_milli_volts = 10200;
  config.low_voltage.recover_milli_volts = 10800;
  config.low_voltage.average_window = 1;  // 平滑化の遅延を無くし、タイミングだけを見る
  config.low_voltage.stop_duration_ms = 250;
  config.low_voltage.unavailable_duration_ms = 100000;  // 欠測はこのテストの対象外
  config.low_voltage.latching = true;
  const PlantCoefficients coeffs = makeCoefficients();
  IntegrationFixture fx(coeffs, config);

  TEST_ASSERT_TRUE(controller.configure(config, fx.ports(), /*now=*/0).ok());

  const BlockMask kLowVoltageBit = static_cast<BlockMask>(BlockReason::kLowVoltage);
  constexpr DurationMs kDt = 50;
  TimeMs now = 0;

  fx.battery.setSample(9800);  // stop_milli_volts(10200) 未満に固定

  // now=50, 100, ..., 300 (elapsed = now - 50 = 0, 50, ..., 250):
  // stop_duration_ms(250) を「厳密に超える」前なので、まだ発火しない。
  // ここで6回 step() を呼ぶ ―― 1ステップにつき2回 updateLowVoltage() が
  // 呼ばれる回帰があれば、call-count 基準の実装に変質した場合に限り
  // このループのどこかで早期に発火してしまう。
  for (int i = 0; i < 6; ++i) {
    now += kDt;
    const StepResult result = controller.step(now);
    fx.advanceAllWheels(kDt);
    TEST_ASSERT_EQUAL_UINT16_MESSAGE(
        0, result.global_reasons & kLowVoltageBit,
        "must not trip before stop_duration_ms has strictly elapsed in real time");
  }
  TEST_ASSERT_EQUAL_INT64(300, now);

  // now=350 (elapsed = 300 > 250): ちょうどこの1ステップで発火する。
  now += kDt;
  const StepResult tripped_result = controller.step(now);
  fx.advanceAllWheels(kDt);
  TEST_ASSERT_EQUAL_INT64(350, now);
  TEST_ASSERT_TRUE_MESSAGE(
      (tripped_result.global_reasons & kLowVoltageBit) != 0,
      "expected LowVoltage to trip at exactly the 7th real step (t=350ms), matching "
      "now(350) - low_voltage_started_at_ms_(50) = 300 > stop_duration_ms(250) -- a call-count-based "
      "(rather than real-elapsed-time-based) duration implementation would trip at a different step "
      "count than this precise boundary predicts");
}

// ---------------------------------------------------------------------------
// 6. 指令反映遅れ（design.md Integration Tests §7、要件 17.1）。
//
//    ⚠️ 新規テストを追加しない、という判断（tasks.md 7.3 のタスクブリーフが
//    許可する選択）。
//
//    task 6.4 の test_controller_step.cpp
//    （test_command_apply_latency_computed_on_first_step_and_held_until_next_change）
//    は既に REAL DrivetrainController の REAL step()/submit()/status() を
//    使い、「指令反映遅れが、反映された最初の制御ステップで算出され、次の
//    反映まで保持される」ことと「step() の呼び出し間隔より短い周期で複数の
//    指令が届いても最後の遷移の遅れだけが報告される」ことの両方を、本タスク
//    の6つの箇条書きが要求する粒度でそのまま検証している。
//
//    command_apply_latency_ms の算出（controller.hpp のコメント参照）は
//    `CommandInput::latched().issued_at_ms` と `now` の比較だけで完結する
//    ―― エンコーダ・モータ・バッテリのどのポート実装を挿しているかには
//    一切依存しない、DrivetrainController 自身の内部状態だけの計算である。
//    そのため MockEncoderPort/MockMotorPort/MockBatteryPort（記録専用の
//    軽量スタブ）を REAL FakeEncoderPort/FakeMotorPort/FakeBatteryPort +
//    WheelPlant（task 7.1）へ置き換えても、この計算経路もこの計算を検証
//    するテストの意味も一切変わらない ―― 置き換えても閉ループの物理
//    モデルという無関係な複雑さが増えるだけで、この性質についての検証の
//    強さは変わらない。
//
//    したがって本ファイルはこの項目について新しいテストを追加しない
//    （task 6.4 のテストが既に「full integration level」として十分な
//    理由: 検証対象のロジックがポート実装から独立しているため、より重い
//    ポート実装に差し替えても検証の深さが増えない）。
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// 7. タスク 10.4 項目1: 速度PID 経由で最終的に得られたデューティと同じ値を
//    開ループ入口へ直接与えたとき、保護①〜④の発火・遮断理由・遮断値・
//    極性適用が完全に一致する（要件 5.9 に対して上の §1
//    (test_body_and_wheel_command_entry_produce_identical_traces_through_full_stack)
//    が行った裏取りと同じ形を、要件 5.11, 5.12 に対して行う）。
//
//    速度PID は「指令」ではなく「出力」としてデューティを生む経路であり、
//    §1のように「同じ入力を2つの入口へ」比較できない。そこで:
//    (a) このシナリオでは輪プラントを一切進めない（advanceAllWheels() を
//        呼ばない）ため計測速度は常に0であり、かつ ki=kd=0 なので速度PID
//        の生出力 raw=kp*target は時刻に依らず一定になる。`PwmCeiling` /
//        `scaleToLimit()` はどちらも状態を持たない純関数（design.md
//        "PwmCeiling" Implementation Notes）であるため、
//        「速度PID 経由で最終的に得られるはずのデューティ」を
//        DrivetrainController を一切構築せずに独立計算できる。
//    (b) 求めた値を WheelDutyCommand として開ループ入口へ直接投入した別の
//        コントローラを、速度PID 側と完全に同一の時刻列・電圧列で
//        step() させ、StepResult/DrivetrainStatus の全フィールドを毎ステップ
//        比較する（§1と同じ assertStepResultEqual/assertStatusEqual を
//        再利用する）。
//
//    設定はこのシナリオの中で保護①（ロック）・保護②（低電圧）・保護④
//    （ウォッチドッグ）がいずれも発火するように意図的に配置しており（保護③
//    ＝PWM上限は常時効いている）、発火の有無・遮断理由・遮断値（0）・出力
//    極性の適用が全経路で一致することを、単一の比較ループで確認する。
// ---------------------------------------------------------------------------

void test_open_loop_input_matching_velocity_pid_output_produces_identical_protection_behavior(void) {
  DrivetrainConfig config = makeValidConfig();
  for (std::uint8_t w = 0; w < kWheelCount; ++w) {
    config.pid[w] = {/*kp=*/0.0008f, /*ki=*/0.0f, /*kd=*/0.0f, /*integral_limit=*/1.0f};
  }
  // 極性適用の一致も併せて確認するため、輪1だけ極性を反転させておく
  // （test_controller_step.cpp の makeValidConfig() と同じ流儀）。
  config.output.polarity[0] = 1;
  config.output.polarity[1] = -1;
  config.output.polarity[2] = 1;

  config.lock.duty_threshold = 0.3f;
  config.lock.speed_threshold_mm_s = 10.0f;
  config.lock.duration_ms = 80;
  config.lock.clear_duration_ms = 80;
  config.lock.latching = true;

  config.low_voltage.warn_milli_volts = 10500;
  config.low_voltage.stop_milli_volts = 9500;
  config.low_voltage.recover_milli_volts = 10500;
  config.low_voltage.average_window = 1;
  config.low_voltage.stop_duration_ms = 80;
  config.low_voltage.unavailable_duration_ms = 100000;
  config.low_voltage.latching = true;

  config.pwm_ceiling.enabled = true;
  config.pwm_ceiling.reference_milli_volts = 4500;
  config.pwm_ceiling.fallback_duty = 0.2f;
  config.pwm_ceiling.override_fn = nullptr;

  config.watchdog.timeout_ms = 250;

  const PlantCoefficients coeffs = makeCoefficients();
  constexpr std::int32_t kBatteryMv = 9000;

  // 速度PID 経由で最終的に得られるはずのデューティを、DrivetrainController
  // を一切構築せず独立計算する（本テスト冒頭コメント参照）。
  WheelVelocityCommand velocity_command{};
  velocity_command.wheel_mm_s[0] = 400.0f;
  velocity_command.wheel_mm_s[1] = 700.0f;
  velocity_command.wheel_mm_s[2] = 999.0f;
  velocity_command.issued_at_ms = 0;

  float expected_open_loop_duty[kWheelCount];
  for (std::uint8_t w = 0; w < kWheelCount; ++w) {
    expected_open_loop_duty[w] = config.pid[w].kp * velocity_command.wheel_mm_s[w];
  }
  const PwmCeiling reference_ceiling(config.pwm_ceiling, config.output.absolute_max_duty);
  const float ceiling = reference_ceiling.evaluate(VoltageSample{/*valid=*/true, kBatteryMv});
  scaleToLimit(expected_open_loop_duty, ceiling);

  // 土台の確認: 実際に縮小が起き、輪1・輪2はロック閾値以上、輪0は未満に
  // なる前提を崩していないこと（そうでなければ以降で意図した「保護①が
  // 一部の輪だけで発火する」パターンが成立しない）。
  TEST_ASSERT_TRUE(expected_open_loop_duty[0] < config.lock.duty_threshold);
  TEST_ASSERT_TRUE(expected_open_loop_duty[1] >= config.lock.duty_threshold);
  TEST_ASSERT_TRUE(expected_open_loop_duty[2] >= config.lock.duty_threshold);

  // A: 速度PID 経由。
  DrivetrainController controller_pid;
  IntegrationFixture fx_pid(coeffs, config);
  TEST_ASSERT_TRUE(controller_pid.configure(config, fx_pid.ports(), /*now=*/0).ok());
  fx_pid.battery.setSample(kBatteryMv);
  controller_pid.setOutputEnabled(true, /*now=*/0);
  TEST_ASSERT_TRUE(controller_pid.submit(velocity_command).accepted);

  // B: 開ループ入口。求めた「速度PID が最終的に出すはずの値」をそのまま
  //    WheelDutyCommand として直接投入する。
  DrivetrainController controller_open_loop;
  IntegrationFixture fx_open_loop(coeffs, config);
  TEST_ASSERT_TRUE(controller_open_loop.configure(config, fx_open_loop.ports(), /*now=*/0).ok());
  fx_open_loop.battery.setSample(kBatteryMv);
  controller_open_loop.setOutputEnabled(true, /*now=*/0);

  WheelDutyCommand duty_command{};
  for (std::uint8_t w = 0; w < kWheelCount; ++w) {
    duty_command.wheel_duty[w] = expected_open_loop_duty[w];
  }
  duty_command.issued_at_ms = 0;
  TEST_ASSERT_TRUE(controller_open_loop.submit(duty_command).accepted);
  TEST_ASSERT_TRUE(controller_open_loop.status().control_path == ControlPath::kOpenLoop);

  // 両方を完全に同一の時刻列・電圧列で回す。輪プラントは進めない
  // （advanceAllWheels() を呼ばない ―― measured_mm_s を常に0へ保つことで
  // 「速度PID の raw が時刻に依らず一定」という前提を維持する。§1が閉ループ
  // を回すのとは異なる、本テスト専用の意図的な単純化）。
  constexpr int kSteps = 7;  // now: 50, 100, ..., 350ms
  constexpr DurationMs kDt = 50;
  TimeMs now = 0;

  const BlockMask kMotorLockBit = static_cast<BlockMask>(BlockReason::kMotorLock);
  const BlockMask kLowVoltageBit = static_cast<BlockMask>(BlockReason::kLowVoltage);
  const BlockMask kTimeoutBit = static_cast<BlockMask>(BlockReason::kCommandTimeout);

  bool saw_lock = false;
  bool saw_low_voltage = false;
  bool saw_watchdog = false;

  for (int i = 0; i < kSteps; ++i) {
    now += kDt;

    const StepResult result_pid = controller_pid.step(now);
    const StepResult result_open_loop = controller_open_loop.step(now);

    assertStepResultEqual(result_pid, result_open_loop);

    // status() のうち「保護・遮断・出力」に関わるフィールドだけを比較する
    // ―― assertStatusEqual() は §1（同じ WheelTargets へ落ちる2つの入口の
    // 比較）向けであり、control_path と wheel_target_mm_s はこのテストでは
    // 意図的に異なる（前者は kVelocityPid/kOpenLoop、後者は速度PID 側だけが
    // mm_s を持ち開ループ側は既定の 0 のまま。WheelDutyCommand は duty[] を
    // 使う別のフィールドであり、これは本テストの主張の対象ではない）ため、
    // ここでは流用しない。
    const DrivetrainStatus status_pid = controller_pid.status();
    const DrivetrainStatus status_open_loop = controller_open_loop.status();
    TEST_ASSERT_TRUE(status_pid.output_enabled == status_open_loop.output_enabled);
    TEST_ASSERT_TRUE(status_pid.has_valid_command == status_open_loop.has_valid_command);
    TEST_ASSERT_TRUE(status_pid.last_command_clamped == status_open_loop.last_command_clamped);
    TEST_ASSERT_EQUAL_INT64(status_pid.last_command_at_ms, status_open_loop.last_command_at_ms);
    for (std::uint8_t w = 0; w < kWheelCount; ++w) {
      TEST_ASSERT_EQUAL_FLOAT(status_pid.wheel_duty[w], status_open_loop.wheel_duty[w]);
      TEST_ASSERT_EQUAL_UINT16(status_pid.wheel_reasons[w], status_open_loop.wheel_reasons[w]);
    }
    TEST_ASSERT_EQUAL_FLOAT(status_pid.pwm_ceiling, status_open_loop.pwm_ceiling);
    TEST_ASSERT_EQUAL_INT32(status_pid.battery_milli_volts, status_open_loop.battery_milli_volts);
    TEST_ASSERT_TRUE(status_pid.battery_valid == status_open_loop.battery_valid);
    TEST_ASSERT_TRUE(status_pid.low_voltage_state == status_open_loop.low_voltage_state);
    TEST_ASSERT_EQUAL_UINT16(status_pid.global_reasons, status_open_loop.global_reasons);
    TEST_ASSERT_EQUAL_INT32(status_pid.last_step_interval_ms, status_open_loop.last_step_interval_ms);
    TEST_ASSERT_EQUAL_INT32(status_pid.command_apply_latency_ms, status_open_loop.command_apply_latency_ms);

    if ((result_pid.wheel_reasons[1] & kMotorLockBit) != 0) {
      saw_lock = true;
    }
    if ((result_pid.global_reasons & kLowVoltageBit) != 0) {
      saw_low_voltage = true;
    }
    if ((result_pid.global_reasons & kTimeoutBit) != 0) {
      saw_watchdog = true;
    }
  }

  // このテストが何も検証していない状態を避ける: 保護①・②・④がいずれも
  // このシナリオの中で実際に発火したことを確認する（保護③=PWM上限は
  // ceiling<absolute_max_duty かつ実際に縮小が起きたことを上で既に確認
  // 済み）。
  TEST_ASSERT_TRUE_MESSAGE(saw_lock, "expected MotorLock to trip during this scenario");
  TEST_ASSERT_TRUE_MESSAGE(saw_low_voltage, "expected LowVoltage to trip during this scenario");
  TEST_ASSERT_TRUE_MESSAGE(saw_watchdog, "expected CommandTimeout to trip during this scenario");
}

// ---------------------------------------------------------------------------
// 8. タスク 10.4 項目2: 開ループ指令を投入したのち指令を止めて時刻だけを
//    進めたとき、速度PID 経由の場合と全く同一の時刻(elapsed = timeout_ms
//    をちょうど厳密に超えた瞬間)で途絶（ウォッチドッグ）が遮断する（要件
//    5.12。§2
//    (test_watchdog_timeout_blocks_and_new_command_stays_blocked_without_output_enable)
//    と同一の timeout_ms・同一の相対オフセットを、指令の種類だけ変えて
//    2つのコントローラを並走させて確認する）。
// ---------------------------------------------------------------------------

void test_watchdog_timeout_boundary_is_identical_between_velocity_pid_and_open_loop_paths(void) {
  DrivetrainConfig config = makeValidConfig();
  config.watchdog.timeout_ms = 200;
  const PlantCoefficients coeffs = makeCoefficients();

  DrivetrainController controller_pid;
  IntegrationFixture fx_pid(coeffs, config);
  TEST_ASSERT_TRUE(controller_pid.configure(config, fx_pid.ports(), /*now=*/0).ok());
  fx_pid.battery.setSample(12600);
  controller_pid.setOutputEnabled(true, /*now=*/0);

  WheelVelocityCommand velocity_command{};
  velocity_command.wheel_mm_s[0] = 100.0f;
  velocity_command.wheel_mm_s[1] = 100.0f;
  velocity_command.wheel_mm_s[2] = 100.0f;
  velocity_command.issued_at_ms = 0;
  TEST_ASSERT_TRUE(controller_pid.submit(velocity_command).accepted);

  DrivetrainController controller_open_loop;
  IntegrationFixture fx_open_loop(coeffs, config);
  TEST_ASSERT_TRUE(controller_open_loop.configure(config, fx_open_loop.ports(), /*now=*/0).ok());
  fx_open_loop.battery.setSample(12600);
  controller_open_loop.setOutputEnabled(true, /*now=*/0);

  WheelDutyCommand duty_command{};
  duty_command.wheel_duty[0] = 0.3f;
  duty_command.wheel_duty[1] = 0.3f;
  duty_command.wheel_duty[2] = 0.3f;
  duty_command.issued_at_ms = 0;
  TEST_ASSERT_TRUE(controller_open_loop.submit(duty_command).accepted);
  TEST_ASSERT_TRUE(controller_open_loop.status().control_path == ControlPath::kOpenLoop);

  const BlockMask kTimeoutBit = static_cast<BlockMask>(BlockReason::kCommandTimeout);

  // 境界前(100ms < timeout(200)): まだ発火せず、両経路とも非ゼロの出力が
  // 出ていること（このテストが何も検証していない状態を避ける）。
  {
    const StepResult result_pid = controller_pid.step(/*now=*/100);
    const StepResult result_open_loop = controller_open_loop.step(/*now=*/100);
    TEST_ASSERT_EQUAL_UINT16(0, result_pid.global_reasons & kTimeoutBit);
    TEST_ASSERT_EQUAL_UINT16(0, result_open_loop.global_reasons & kTimeoutBit);

    bool pid_nonzero = false;
    bool open_loop_nonzero = false;
    for (std::uint8_t w = 0; w < kWheelCount; ++w) {
      if (std::fabs(result_pid.outputs.duty[w]) > 1.0e-6f) {
        pid_nonzero = true;
      }
      if (std::fabs(result_open_loop.outputs.duty[w]) > 1.0e-6f) {
        open_loop_nonzero = true;
      }
    }
    TEST_ASSERT_TRUE(pid_nonzero);
    TEST_ASSERT_TRUE(open_loop_nonzero);
  }

  // ちょうど境界(now=200, elapsed=200-0=200。「厳密に超える」の境界ぴったり
  // なのでまだ超えていない): まだ発火しない ―― 速度PID 経路・開ループ経路の
  // 両方で。
  {
    const StepResult result_pid = controller_pid.step(/*now=*/200);
    const StepResult result_open_loop = controller_open_loop.step(/*now=*/200);
    TEST_ASSERT_EQUAL_UINT16(0, result_pid.global_reasons & kTimeoutBit);
    TEST_ASSERT_EQUAL_UINT16(0, result_open_loop.global_reasons & kTimeoutBit);
  }

  // 境界を厳密に超えた直後(now=201, elapsed=201>200): ちょうどこの1ステップ
  // で両経路とも発火する ―― 同一の経過時間ちょうどの境界で揃うことの直接
  // 確認（要件 5.12）。
  {
    const StepResult result_pid = controller_pid.step(/*now=*/201);
    const StepResult result_open_loop = controller_open_loop.step(/*now=*/201);
    TEST_ASSERT_TRUE((result_pid.global_reasons & kTimeoutBit) != 0);
    TEST_ASSERT_TRUE((result_open_loop.global_reasons & kTimeoutBit) != 0);
    for (std::uint8_t w = 0; w < kWheelCount; ++w) {
      TEST_ASSERT_EQUAL_FLOAT(0.0f, result_pid.outputs.duty[w]);
      TEST_ASSERT_EQUAL_FLOAT(0.0f, result_open_loop.outputs.duty[w]);
    }
  }
}

int main(int argc, char** argv) {
  (void)argc;
  (void)argv;
  UNITY_BEGIN();

  RUN_TEST(test_body_and_wheel_command_entry_produce_identical_traces_through_full_stack);
  RUN_TEST(test_watchdog_timeout_blocks_and_new_command_stays_blocked_without_output_enable);
  RUN_TEST(test_no_command_yet_and_output_disabled_are_separate_reasons_right_after_configure);
  RUN_TEST(test_two_independent_controllers_given_identical_script_produce_identical_traces);
  RUN_TEST(test_low_voltage_trip_timing_matches_real_elapsed_time_through_full_controller_step);
  RUN_TEST(test_open_loop_input_matching_velocity_pid_output_produces_identical_protection_behavior);
  RUN_TEST(test_watchdog_timeout_boundary_is_identical_between_velocity_pid_and_open_loop_paths);

  return UNITY_END();
}
