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

// drivetrain-core task 7.2: 閉ループで保護の発火と速度追従を検証するホスト
// テスト（design.md "Testing Strategy" § 閉ループテスト
// `firmware/test/native/controller_closed_loop/`）。
//
// ⚠️⚠️⚠️ このファイルが得る数値は実機性能の主張ではない ⚠️⚠️⚠️
// ここで使う test_support::PlantCoefficients（duty_to_steady_mm_s /
// time_constant_ms）は test_support/plant_coefficients.hpp が宣言すると
// おり「合否条件ではない仮値」であり、実測値ではない（要件 16.2, 16.4,
// 16.6）。以下のテストが観測する収束の速さ・保護が発火するまでの経過時間・
// PID ゲインの具体値は、すべてこの仮のプラントモデルの産物であり、実機の
// 駆動系がどう振る舞うかを一切主張しない。各テストの合否条件は常に
// 「向き」（定常偏差が縮む方向へ動くか）・「発火の有無」・「遮断/復帰の
// 成立」であり、収束時間・具体的な誤差量・具体的なゲイン値そのものを
// 合否条件にしない（design.md 閉ループテスト冒頭の注記、要件 16.4, 16.6）。
// このファイルの PID ゲイン・保護閾値・電圧の具体値は、いずれもこのテスト
// が仮のプラントモデルを動かすためだけに選んだテストローカルな値であり、
// 実機の推奨設定ではない。
//
// REAL DrivetrainController（L0-L11、タスク 2〜6 で確定済み）を REAL
// test_support::WheelPlant / FakeEncoderPort / FakeMotorPort /
// FakeBatteryPort（タスク 7.1）へ配線し、`controller.step(now)` →
// `FakeMotorPort::lastOutputs()` で読んだデューティを各輪の
// `WheelPlant::advance()` へ渡す → 次の `step()` の `EncoderPort::read()`
// がプラントの更新後状態を反映する、という真の閉ループを回す
// （台本化されたレスポンスではない）。
//
// design.md "Testing Strategy" § 閉ループテストが要求する5系統を、それぞれ
// 独立したテスト関数として実装する:
//   1. 速度追従の収束（要件 9.7） ―― 収束時間を合否条件にしない
//   2. 保護① の発火（要件 16.8, 10.1, 10.4） ―― 継続時間に満たない拘束では
//      発火しないことも含む
//   3. 保護② の発火とヒステリシス（要件 16.8, 11.2, 11.5） ―― 一時的な
//      降下では停止しないこと、復帰は recover 閾値でのみ起きることを含む
//   4. 保護③ の効き（要件 12.1, 12.4, 12.6） ―― 全輪への同一適用と、
//      電圧欠測時の fallback_duty を含む
//   5. オドメトリの整合（要件 8.4） ―― 逆運動学で与えた機体速度どおりに
//      車輪が回った「given」条件を、WheelPlant の1次遅れが単一ステップで
//      目標へ収束しきる大きな dt（alpha が 1 にクランプされる、
//      wheel_plant.cpp 冒頭コメント参照）で作り、PID の収束ノイズを経由
//      せずに順運動学の復元だけを検証する

using drivetrain_control::BlockMask;
using drivetrain_control::BlockReason;
using drivetrain_control::BodyVelocity;
using drivetrain_control::BodyVelocityCommand;
using drivetrain_control::DrivetrainConfig;
using drivetrain_control::DrivetrainController;
using drivetrain_control::DrivetrainStatus;
using drivetrain_control::DurationMs;
using drivetrain_control::GeometryParams;
using drivetrain_control::Kinematics;
using drivetrain_control::kWheelCount;
using drivetrain_control::LowVoltageState;
using drivetrain_control::Ports;
using drivetrain_control::StepResult;
using drivetrain_control::TimeMs;
using drivetrain_control::VoltageSample;
using drivetrain_control::WheelOutputs;
using drivetrain_control::WheelVelocityCommand;
using test_support::FakeBatteryPort;
using test_support::FakeEncoderPort;
using test_support::FakeMotorPort;
using test_support::PlantCoefficients;

namespace {

// test_controller_step.cpp / test_fake_ports.cpp の makeValidConfig() と
// 同じ流儀（検証ロジックが正しく動くことだけを確かめるための架空の妥当値
// 一式。実機の性能値ではない。要件 15.1, 15.2）。各テストはこれを土台に
// 自分の関心に応じたフィールドだけを上書きする。
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

  // ロック・ウォッチドッグ・低電圧は既定でこのテストファイルの各シナリオの
  // 関心を横取りしない値にしておき、各テストが必要なものだけ上書きする。
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

struct ClosedLoopFixture {
  FakeEncoderPort encoder;
  FakeMotorPort motor;
  FakeBatteryPort battery;

  ClosedLoopFixture(const PlantCoefficients& coeffs, const DrivetrainConfig& config)
      : encoder(coeffs, config.encoder.counts_per_wheel_rev, config.encoder.wheel_diameter_mm,
                config.encoder.raw_modulus) {}

  Ports ports() {
    Ports p;
    p.encoder = &encoder;
    p.motor = &motor;
    p.battery = &battery;
    return p;
  }

  // 直近の step() が書き出したデューティで、3輪すべての WheelPlant を
  // dt_ms ぶん進める（供給比 1.0 = 満充電相当のトルク余裕）。これが
  // 「FakeMotorPort::lastOutputs() -> WheelPlant::advance()」の閉ループの
  // 実体である。
  void advanceAllWheels(DurationMs dt_ms) {
    const WheelOutputs& applied = motor.lastOutputs();
    for (std::uint8_t w = 0; w < kWheelCount; ++w) {
      encoder.plant(w).advance(applied.duty[w], /*supply_ratio=*/1.0f, dt_ms);
    }
  }
};

}  // namespace

void setUp(void) {}
void tearDown(void) {}

// ---------------------------------------------------------------------------
// 1. 速度追従の収束（要件 9.7）: 一定の機体速度指令に対して、定常偏差が
//    縮む方向へ動くことを確認する。**収束時間や具体的な誤差量を合否条件に
//    しない** ―― 実行開始直後の窓と実行終盤の窓で「目標との偏差の合計」の
//    平均を比較し、終盤が始めより小さいこと（向き）だけを見る。
// ---------------------------------------------------------------------------

void test_velocity_tracking_deviation_trends_downward_in_closed_loop(void) {
  DrivetrainController controller;
  const DrivetrainConfig config = makeValidConfig();
  const PlantCoefficients coeffs = makeCoefficients();
  ClosedLoopFixture fx(coeffs, config);

  TEST_ASSERT_TRUE(controller.configure(config, fx.ports(), /*now=*/0).ok());

  fx.battery.setSample(12600);
  controller.setOutputEnabled(true, /*now=*/0);

  // 純並進の一定機体速度指令。3輪の目標速度は逆運動学により異なる値になる
  // が、全輪が同じ PI ゲインと同じプラント係数で追従する。
  BodyVelocityCommand command{};
  command.vx_mm_s = 200.0f;
  command.vy_mm_s = 0.0f;
  command.omega_rad_s = 0.0f;
  command.issued_at_ms = 0;
  TEST_ASSERT_TRUE(controller.submit(command).accepted);

  constexpr int kSteps = 300;
  constexpr DurationMs kDt = 20;
  constexpr int kWindow = 10;  // 実行開始直後10ステップ vs 終盤10ステップ

  TimeMs now = 0;
  float early_window_sum = 0.0f;
  float late_window_sum = 0.0f;

  for (int i = 1; i <= kSteps; ++i) {
    now += kDt;
    controller.step(now);
    fx.advanceAllWheels(kDt);

    const DrivetrainStatus status = controller.status();
    float deviation = 0.0f;
    for (std::uint8_t w = 0; w < kWheelCount; ++w) {
      deviation += std::fabs(status.wheel_target_mm_s[w] - status.wheel_measured_mm_s[w]);
    }

    if (i <= kWindow) {
      early_window_sum += deviation;
    }
    if (i > kSteps - kWindow) {
      late_window_sum += deviation;
    }
  }

  const float early_avg = early_window_sum / static_cast<float>(kWindow);
  const float late_avg = late_window_sum / static_cast<float>(kWindow);

  // 「向き」だけを見る: 実行開始直後より終盤のほうが目標との偏差が小さい
  // こと。この不等式1本だけが合否条件であり、具体的な収束時間や許容誤差の
  // 大きさは一切問わない（design.md 閉ループテスト §1、要件 16.4, 16.6）。
  TEST_ASSERT_TRUE_MESSAGE(
      late_avg < early_avg,
      "expected the target/measured deviation to shrink from the start of the run to the end "
      "(direction only -- not a settling-time or absolute-error claim)");
}

// ---------------------------------------------------------------------------
// 2. 保護① の発火（要件 16.8, 10.1, 10.4）: setStalled(true) で1輪を拘束
//    し、duration_ms 経過後に当該輪のみが遮断値になること。duration_ms に
//    満たない一時的な拘束では発火しないことを別関数で確認する。
// ---------------------------------------------------------------------------

void test_motor_lock_trips_only_the_stalled_wheel_after_duration_elapses(void) {
  DrivetrainController controller;
  DrivetrainConfig config = makeValidConfig();
  config.lock.duty_threshold = 0.3f;
  config.lock.speed_threshold_mm_s = 10.0f;
  config.lock.duration_ms = 300;
  config.lock.clear_duration_ms = 200;
  config.lock.latching = true;
  const PlantCoefficients coeffs = makeCoefficients();
  ClosedLoopFixture fx(coeffs, config);

  TEST_ASSERT_TRUE(controller.configure(config, fx.ports(), /*now=*/0).ok());

  fx.battery.setSample(12600);
  controller.setOutputEnabled(true, /*now=*/0);

  // 3輪とも同じ目標速度(300mm/s)を与える。raw = kp(0.002) * 300 = 0.6 >=
  // duty_threshold(0.3)。輪1だけを拘束し、他の2輪は実際にプラントで
  // 加速して速度閾値を上回ることでロック条件から自然に外れる。
  WheelVelocityCommand command{};
  command.wheel_mm_s[0] = 300.0f;
  command.wheel_mm_s[1] = 300.0f;
  command.wheel_mm_s[2] = 300.0f;
  command.issued_at_ms = 0;
  TEST_ASSERT_TRUE(controller.submit(command).accepted);

  constexpr std::uint8_t kStalledWheel = 1;
  fx.encoder.plant(kStalledWheel).setStalled(true);

  const BlockMask kMotorLockBit = static_cast<BlockMask>(BlockReason::kMotorLock);
  constexpr DurationMs kDt = 50;
  TimeMs now = 0;
  bool tripped_wheel_seen = false;
  StepResult result{};

  for (int i = 0; i < 8; ++i) {  // now: 50, 100, ..., 400ms (> duration_ms=300)
    now += kDt;
    result = controller.step(now);
    fx.advanceAllWheels(kDt);

    if ((result.wheel_reasons[kStalledWheel] & kMotorLockBit) != 0) {
      tripped_wheel_seen = true;
    }
    // 拘束されていない輪は、実際にプラントで加速して速度閾値を超えるため、
    // この閉ループ全体を通じてロックが一度も立たないはずである。
    TEST_ASSERT_EQUAL_UINT16(0, result.wheel_reasons[0]);
    TEST_ASSERT_EQUAL_UINT16(0, result.wheel_reasons[2]);
  }

  TEST_ASSERT_TRUE_MESSAGE(tripped_wheel_seen,
                            "expected the stalled wheel to trip MotorLock within the run");
  TEST_ASSERT_EQUAL_UINT16(kMotorLockBit, result.wheel_reasons[kStalledWheel]);
  TEST_ASSERT_EQUAL_UINT16(0, result.wheel_reasons[0]);
  TEST_ASSERT_EQUAL_UINT16(0, result.wheel_reasons[2]);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, result.outputs.duty[kStalledWheel]);
}

// ---------------------------------------------------------------------------
// 2b. 保護①: duration_ms に満たない一時的な拘束では発火しない（要件
//     10.4）。拘束を duration_ms より十分短く保ち、その後 unstall して
//     十分長く走らせても、どの輪もロックしないことを確認する。
// ---------------------------------------------------------------------------

void test_motor_lock_does_not_trip_for_stall_shorter_than_duration(void) {
  DrivetrainController controller;
  DrivetrainConfig config = makeValidConfig();
  config.lock.duty_threshold = 0.3f;
  config.lock.speed_threshold_mm_s = 10.0f;
  config.lock.duration_ms = 300;
  config.lock.clear_duration_ms = 200;
  config.lock.latching = true;
  const PlantCoefficients coeffs = makeCoefficients();
  ClosedLoopFixture fx(coeffs, config);

  TEST_ASSERT_TRUE(controller.configure(config, fx.ports(), /*now=*/0).ok());

  fx.battery.setSample(12600);
  controller.setOutputEnabled(true, /*now=*/0);

  WheelVelocityCommand command{};
  command.wheel_mm_s[0] = 300.0f;
  command.wheel_mm_s[1] = 300.0f;
  command.wheel_mm_s[2] = 300.0f;
  command.issued_at_ms = 0;
  TEST_ASSERT_TRUE(controller.submit(command).accepted);

  constexpr std::uint8_t kStalledWheel = 2;
  fx.encoder.plant(kStalledWheel).setStalled(true);

  const BlockMask kMotorLockBit = static_cast<BlockMask>(BlockReason::kMotorLock);
  constexpr DurationMs kDt = 50;
  TimeMs now = 0;

  // 拘束は 100ms（duration_ms=300 に対して十分短い、余裕を持たせた
  // 一時的な速度低下を模す）だけ続ける。
  for (int i = 0; i < 2; ++i) {  // now: 50, 100ms
    now += kDt;
    const StepResult result = controller.step(now);
    fx.advanceAllWheels(kDt);
    for (std::uint8_t w = 0; w < kWheelCount; ++w) {
      TEST_ASSERT_EQUAL_UINT16(0, result.wheel_reasons[w]);
    }
  }

  fx.encoder.plant(kStalledWheel).setStalled(false);

  // 拘束解除後も十分長く走らせ、遅れて発火したり別の輪が誤発火したりし
  // ないことを確認する（now: 150, 200, ..., 900ms）。
  for (int i = 0; i < 16; ++i) {
    now += kDt;
    const StepResult result = controller.step(now);
    fx.advanceAllWheels(kDt);
    for (std::uint8_t w = 0; w < kWheelCount; ++w) {
      TEST_ASSERT_EQUAL_UINT16_MESSAGE(
          0, result.wheel_reasons[w] & kMotorLockBit,
          "a stall shorter than duration_ms must never trip MotorLock, even after it clears");
    }
  }
}

// ---------------------------------------------------------------------------
// 3. 保護② の発火とヒステリシス（要件 16.8, 11.2, 11.5）: 電圧を段階的に
//    下げて警告 -> 停止の順に遷移すること。急加速を模した一時的な降下では
//    停止しないこと。recover_milli_volts まで戻して初めて復帰すること
//    （stop_milli_volts へ戻すだけでは復帰しない）。
// ---------------------------------------------------------------------------

void test_low_voltage_state_machine_warns_trips_and_recovers_only_at_recover_threshold(void) {
  DrivetrainController controller;
  DrivetrainConfig config = makeValidConfig();
  config.low_voltage.warn_milli_volts = 10800;
  config.low_voltage.stop_milli_volts = 10200;
  config.low_voltage.recover_milli_volts = 10500;  // stop より高く、warn より低い独立した閾値
  config.low_voltage.average_window = 1;  // 移動平均そのものではなく状態遷移のタイミングを
                                           // 検証するテストのため、平滑化の遅延を無くす
                                           // （1..kMaxVoltageWindow の範囲内。要件 11.6）
  config.low_voltage.stop_duration_ms = 200;
  config.low_voltage.unavailable_duration_ms = 500;  // 欠測はこのテストの対象外
  config.low_voltage.latching = false;  // 自動復帰。recover 閾値だけで戻ることを確認するため
  const PlantCoefficients coeffs = makeCoefficients();
  ClosedLoopFixture fx(coeffs, config);

  TEST_ASSERT_TRUE(controller.configure(config, fx.ports(), /*now=*/0).ok());
  // このシナリオは低電圧状態機械の遷移だけに関心があるため、指令の投入・
  // 出力許可は行わない（低電圧の判定と global_reasons のビットは、指令・
  // 出力許可の有無に関わらず ReadBatt -> LowV が毎実ステップ実行される
  // ため成立する。design.md System Flows 参照）。

  const BlockMask kLowVoltageBit = static_cast<BlockMask>(BlockReason::kLowVoltage);
  constexpr DurationMs kDt = 50;
  TimeMs now = 0;

  auto step = [&]() -> DrivetrainStatus {
    now += kDt;
    controller.step(now);
    return controller.status();
  };

  // Normal: warn(10800) を大きく上回る。
  fx.battery.setSample(12600);
  {
    const DrivetrainStatus status = step();
    TEST_ASSERT_TRUE(status.low_voltage_state == LowVoltageState::kNormal);
    TEST_ASSERT_EQUAL_UINT16(0, status.global_reasons & kLowVoltageBit);
  }

  // Warning: stop(10200) と warn(10800) の間。
  fx.battery.setSample(10500);
  {
    const DrivetrainStatus status = step();
    TEST_ASSERT_TRUE(status.low_voltage_state == LowVoltageState::kWarning);
    TEST_ASSERT_EQUAL_UINT16(0, status.global_reasons & kLowVoltageBit);
  }

  // 急加速を模した一時的な降下: stop 未満に 100ms だけ触れる
  // （stop_duration_ms=200 より十分短い）。停止しない。
  fx.battery.setSample(9800);
  step();  // stop未満のサンプル1発目（タイマー起動）
  {
    const DrivetrainStatus status = step();  // 2発目、経過50ms(< 200ms)
    TEST_ASSERT_FALSE(status.low_voltage_state == LowVoltageState::kTripped);
    TEST_ASSERT_EQUAL_UINT16(0, status.global_reasons & kLowVoltageBit);
  }

  // stop 以上へ戻す(まだ warn 未満) -> Warning へ戻り、降下タイマーは
  // リセットされる。
  fx.battery.setSample(10500);
  {
    const DrivetrainStatus status = step();
    TEST_ASSERT_TRUE(status.low_voltage_state == LowVoltageState::kWarning);
    TEST_ASSERT_EQUAL_UINT16(0, status.global_reasons & kLowVoltageBit);
  }

  // 継続的な降下: stop 未満を stop_duration_ms(200ms) を超えて持続させる
  // -> 停止（Tripped）。
  fx.battery.setSample(9800);
  DrivetrainStatus tripped_status{};
  for (int i = 0; i < 6; ++i) {  // 300ms 分（200ms を明確に超える）
    tripped_status = step();
  }
  TEST_ASSERT_TRUE(tripped_status.low_voltage_state == LowVoltageState::kTripped);
  TEST_ASSERT_TRUE((tripped_status.global_reasons & kLowVoltageBit) != 0);

  // 復帰の試み(1): stop 閾値ちょうどまでしか戻さない -> まだ Tripped の
  // まま（recover_milli_volts(10500) に達していないため）。
  fx.battery.setSample(10200);
  {
    const DrivetrainStatus status = step();
    TEST_ASSERT_TRUE(status.low_voltage_state == LowVoltageState::kTripped);
    TEST_ASSERT_TRUE((status.global_reasons & kLowVoltageBit) != 0);
  }

  // 復帰の試み(2): stop と recover の間まで戻す -> まだ Tripped のまま。
  fx.battery.setSample(10350);
  {
    const DrivetrainStatus status = step();
    TEST_ASSERT_TRUE(status.low_voltage_state == LowVoltageState::kTripped);
    TEST_ASSERT_TRUE((status.global_reasons & kLowVoltageBit) != 0);
  }

  // recover_milli_volts(10500) まで戻して初めて復帰する。
  fx.battery.setSample(10500);
  {
    const DrivetrainStatus status = step();
    TEST_ASSERT_TRUE(status.low_voltage_state == LowVoltageState::kNormal);
    TEST_ASSERT_EQUAL_UINT16(0, status.global_reasons & kLowVoltageBit);
  }
}

// ---------------------------------------------------------------------------
// 4a. 保護③ の効き（要件 12.1, 12.4）: 満充電相当の電圧で上限が下がり、
//     全輪へ同一の上限が掛かって指令方向（比率）が保たれること。
// ---------------------------------------------------------------------------

void test_pwm_ceiling_high_voltage_lowers_ceiling_uniformly_across_wheels(void) {
  DrivetrainController controller;
  DrivetrainConfig config = makeValidConfig();
  config.pwm_ceiling.enabled = true;
  config.pwm_ceiling.reference_milli_volts = 10000;  // < 満充電相当の測定電圧 -> 上限が下がる
  config.pwm_ceiling.fallback_duty = 0.2f;
  config.pwm_ceiling.override_fn = nullptr;
  const PlantCoefficients coeffs = makeCoefficients();
  ClosedLoopFixture fx(coeffs, config);

  TEST_ASSERT_TRUE(controller.configure(config, fx.ports(), /*now=*/0).ok());

  fx.battery.setSample(12600);  // 満充電相当。reference(10000) を上回る
  controller.setOutputEnabled(true, /*now=*/0);

  // ki=kd=0 の kp のみのゲイン(makeValidConfig())なので raw = kp*(target -
  // measured) が測定速度に線形に依存する。3輪の目標速度を 1:2:3 の比率に
  // することで、等比縮小が全輪へ同一に掛かっていることを比率の保存で
  // 確認できる。
  WheelVelocityCommand command{};
  command.wheel_mm_s[0] = 200.0f;
  command.wheel_mm_s[1] = 400.0f;
  command.wheel_mm_s[2] = 600.0f;
  command.issued_at_ms = 0;
  TEST_ASSERT_TRUE(controller.submit(command).accepted);

  const float expected_ceiling =
      static_cast<float>(config.pwm_ceiling.reference_milli_volts) / 12600.0f;  // 10000/12600
  TEST_ASSERT_TRUE(expected_ceiling < config.output.absolute_max_duty);  // 実際に上限が下がっている前提

  constexpr DurationMs kDt = 50;
  TimeMs now = 0;
  constexpr float kTol = 5.0e-2f;

  for (int i = 0; i < 5; ++i) {
    now += kDt;
    const StepResult result = controller.step(now);
    fx.advanceAllWheels(kDt);

    const DrivetrainStatus status = controller.status();

    // 電圧が変わっていないため上限は毎ステップ一定。
    TEST_ASSERT_FLOAT_WITHIN(kTol, expected_ceiling, status.pwm_ceiling);

    // 全輪が同一の上限以内に収まっている(同一上限が掛かっていることの
    // 直接的な裏取り)。
    for (std::uint8_t w = 0; w < kWheelCount; ++w) {
      TEST_ASSERT_TRUE(std::fabs(result.outputs.duty[w]) <= expected_ceiling + kTol);
    }

    // 比率(方向)が指令どおりに保たれている: raw = kp*(target-measured) は
    // 3輪とも同じゲイン・同じ経過時間で計算されるため、等比縮小
    // （個別クリップではない）である限り比率は指令の 1:2:3 のまま保たれる
    // （輪ごとの個別クリップだとここが崩れる。要件 12.4）。この比較は
    // 最初のステップ（i==0）でだけ行う ―― 測定速度がまだ全輪0の時点
    // （raw が測定速度に依存せず厳密に比例する唯一の瞬間）であり、以降の
    // ステップでは輪ごとのエンコーダ量子化誤差（raw_count() の int32
    // 丸め、wheel_plant.cpp 参照）が比率へ累積して本質的でないノイズを
    // 生む。全輪が同一上限以内に収まること自体は上のループ全体で確認済み
    // であり、方向保存の直接証拠は量子化誤差の乗らないこの1点で取る。
    if (i == 0) {
      TEST_ASSERT_FLOAT_WITHIN(kTol, 2.0f, result.outputs.duty[1] / result.outputs.duty[0]);
      TEST_ASSERT_FLOAT_WITHIN(kTol, 3.0f, result.outputs.duty[2] / result.outputs.duty[0]);
    }
  }
}

// ---------------------------------------------------------------------------
// 4b. 保護③: 電圧の読み値が得られない場合、上限を無制限とせず
//     fallback_duty を適用する（要件 12.6）。
// ---------------------------------------------------------------------------

void test_pwm_ceiling_uses_fallback_duty_when_voltage_missing(void) {
  DrivetrainController controller;
  DrivetrainConfig config = makeValidConfig();
  config.pwm_ceiling.enabled = true;
  config.pwm_ceiling.reference_milli_volts = 12600;
  config.pwm_ceiling.fallback_duty = 0.2f;
  config.pwm_ceiling.override_fn = nullptr;
  const PlantCoefficients coeffs = makeCoefficients();
  ClosedLoopFixture fx(coeffs, config);

  TEST_ASSERT_TRUE(controller.configure(config, fx.ports(), /*now=*/0).ok());

  fx.battery.setMissing();

  const DrivetrainStatus status = controller.status();  // configure() 直後は既定値
  (void)status;

  const StepResult result = controller.step(/*now=*/50);
  (void)result;

  const DrivetrainStatus after = controller.status();
  TEST_ASSERT_FLOAT_WITHIN(1.0e-5f, config.pwm_ceiling.fallback_duty, after.pwm_ceiling);
}

// ---------------------------------------------------------------------------
// 5. オドメトリの整合（要件 8.4）: 逆運動学で与えた機体速度どおりに車輪が
//    回った「given」条件で、順運動学（Odometry 経由）が同じ機体速度を
//    復元することを確認する。
//
//    PID の収束ノイズを経由しないよう、WheelPlant::advance() へ1次遅れが
//    単一ステップで目標速度へ「スナップ」する大きな dt（time_constant_ms
//    以上。alpha が [0,1] へクランプされるため、単発の大きな dt は目標へ
//    正確に到達する。wheel_plant.cpp 冒頭コメント参照）を与え、逆運動学が
//    計算した目標輪速度を各輪へ直接実現させる。PID・出力許可・保護は
//    このシナリオの関心ではないため一切使わない（Odometry は
//    EncoderPort::read() の結果だけで更新され、指令・出力許可の状態に
//    左右されない。design.md System Flows の Odom ノードは ReadBatt より
//    前で完結する）。
// ---------------------------------------------------------------------------

void test_odometry_recovers_commanded_body_velocity_from_wheel_speeds_matching_inverse_kinematics(void) {
  DrivetrainController controller;
  const DrivetrainConfig config = makeValidConfig();
  const PlantCoefficients coeffs = makeCoefficients();
  ClosedLoopFixture fx(coeffs, config);

  TEST_ASSERT_TRUE(controller.configure(config, fx.ports(), /*now=*/0).ok());
  fx.battery.setSample(12600);

  // 「逆運動学で与えた機体速度」―― コントローラと同じ GeometryParams から
  // 独立に構築した Kinematics で目標輪速度を計算する（design.md 他の
  // ホストテストと同じ流儀。test_controller_step.cpp 参照）。
  const Kinematics reference_kinematics(config.geometry);
  const BodyVelocity target_body{/*vx_mm_s=*/150.0f, /*vy_mm_s=*/60.0f, /*omega_rad_s=*/0.5f};
  float target_wheel_mm_s[kWheelCount] = {0.0f, 0.0f, 0.0f};
  reference_kinematics.inverse(target_body, target_wheel_mm_s);

  // duty_to_steady_mm_s(500) に対して十分小さい目標速度であること
  // （|duty| <= 1 でこの目標へ到達できることの前提。テストローカルな
  // 選択であり、実機の限界を主張しない）。
  for (std::uint8_t w = 0; w < kWheelCount; ++w) {
    TEST_ASSERT_TRUE(std::fabs(target_wheel_mm_s[w]) < coeffs.duty_to_steady_mm_s);
  }

  // 「車輪が回った」―― 各輪の WheelPlant を、目標輪速度ちょうどへ単一
  // ステップでスナップさせる duty で dt_ms(= time_constant_ms 以上) だけ
  // 進める。
  constexpr DurationMs kDt = 2000;  // >= time_constant_ms(100) -> alpha が 1 にクランプされる
  TEST_ASSERT_TRUE(static_cast<float>(kDt) >= coeffs.time_constant_ms);
  for (std::uint8_t w = 0; w < kWheelCount; ++w) {
    const float duty = target_wheel_mm_s[w] / coeffs.duty_to_steady_mm_s;
    fx.encoder.plant(w).advance(duty, /*supply_ratio=*/1.0f, kDt);
    // スナップが実際に厳密一致で起きたことの裏取り(このテストが暗黙の
    // 収束誤差を混入させていないことの確認)。
    TEST_ASSERT_FLOAT_WITHIN(1.0e-2f, target_wheel_mm_s[w], fx.encoder.plant(w).speed_mm_s());
  }

  // 「順運動学が同じ機体速度を復元する」―― この1回の実ステップで
  // EncoderPort::read() が上記の変位を返し、DrivetrainController が
  // Odometry::update() を通じて機体速度を復元する。
  controller.step(/*now=*/kDt);
  const DrivetrainStatus status = controller.status();

  // 量子化誤差（生カウントは int32 へ切り捨てられる。wheel_plant.cpp
  // raw_count() 参照）を考慮した許容差。dt を大きく取ることで速度換算後の
  // 相対誤差を小さく抑えている。
  constexpr float kLinearTol = 1.0f;    // mm/s
  constexpr float kAngularTol = 0.02f;  // rad/s

  TEST_ASSERT_FLOAT_WITHIN(kLinearTol, target_body.vx_mm_s, status.odometry.body_velocity.vx_mm_s);
  TEST_ASSERT_FLOAT_WITHIN(kLinearTol, target_body.vy_mm_s, status.odometry.body_velocity.vy_mm_s);
  TEST_ASSERT_FLOAT_WITHIN(kAngularTol, target_body.omega_rad_s, status.odometry.body_velocity.omega_rad_s);
}

int main(int argc, char** argv) {
  (void)argc;
  (void)argv;
  UNITY_BEGIN();

  RUN_TEST(test_velocity_tracking_deviation_trends_downward_in_closed_loop);
  RUN_TEST(test_motor_lock_trips_only_the_stalled_wheel_after_duration_elapses);
  RUN_TEST(test_motor_lock_does_not_trip_for_stall_shorter_than_duration);
  RUN_TEST(test_low_voltage_state_machine_warns_trips_and_recovers_only_at_recover_threshold);
  RUN_TEST(test_pwm_ceiling_high_voltage_lowers_ceiling_uniformly_across_wheels);
  RUN_TEST(test_pwm_ceiling_uses_fallback_duty_when_voltage_missing);
  RUN_TEST(test_odometry_recovers_commanded_body_velocity_from_wheel_speeds_matching_inverse_kinematics);

  return UNITY_END();
}
