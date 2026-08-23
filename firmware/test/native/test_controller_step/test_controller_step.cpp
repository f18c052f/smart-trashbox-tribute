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

// drivetrain-core task 6.2/6.3/6.4: 制御ステップの骨格（設定・時刻・計測、
// 6.2）・出力段（上限・PID・縮小・遮断・極性、6.3）・状態スナップショット
// と下流が使う計測量（status()/DrivetrainStatus、実測周期・指令反映遅れ、
// 6.4）を検証するホストテスト。
//
// design.md "DrivetrainController"（L10）の Preconditions/Postconditions/
// Invariants のうち、これら3タスクが実装する部分を対象にする。要件 3.1,
// 3.2, 3.4, 3.5, 3.6, 6.5, 12.4, 13.5, 13.6, 14.1, 14.2, 14.3, 15.4, 15.5,
// 17.1, 17.2, 17.7。
//
// タスク6.5で `DrivetrainController::submit()` / `setOutputEnabled()` が
// 公開されたため、タスク6.3/6.4由来のテスト（I〜M）は以降これらの公開
// メソッドを直接使う（`controller.submit(...)` / `controller.setOutputEnabled(...)`）。
// タスク6.4で `status()` が内部の計測速度・エンコーダ基準値・オドメトリを
// 公開する読み出し手段になったため、`ControllerStepTestHooks` の friend
// アクセサからはそれらに対応する3メソッド（旧
// lastMeasuredMmS/lastEncoderCounts/odometryState）を既に削除しており、
// テストF/Gは `status()` を使う。friend に残る2メソッド
// （lastCommandedDuty/pidIntegral）が必要な理由は friend struct 定義直前の
// コメント参照。
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
//      更新すること。指令未着・出力未許可の既定状態のまま出力段まで
//      進むため、kNoCommandYet と kOutputDisabled が両方立ち、遮断値が
//      MotorOutputPort へ書き出されること（タスク6.3で挙動が変わった
//      部分）も併せて確認する
//   G. F の実ステップの後、同じ時刻で step() を呼んでも
//      （ポートの中身をその間に変えても）ポートが読まれず、内部状態
//      （計測速度・エンコーダ基準値・Odometry の状態）が変化しないこと
//      （要件 3.4 を、状態が既に動いた後の短絡でも確認する）。バッテリ
//      読み出し・モータ書き出しの回数も短絡で増えないことを含める
//   H. 検証に失敗する2回目の configure() は、直前に成功していた設定を
//      破棄し、configured() を false に戻す（「検証に失敗した状態では
//      制御を開始させない」を再設定にも一貫させる設計判断の回帰）
//   I. PWM 上限が下がっている状態で3輪の生の PID 出力が異なる大きさに
//      なる指令を与えても、全輪へ同一の縮小率が掛かり、指令された比率
//      （方向）が保たれること（要件 6.5, 12.4。輪ごとの個別クリップで
//      ないことの裏取り）
//   J. ウォッチドッグ発火中は、最終出力が遮断されるだけでなく、PID の
//      内部状態（積分）が実際に holdReset() で 0 化され、上限適用後・
//      遮断前の出力指令（applyWheelOutputs() の戻り値）自体が 0 になる
//      こと（要件 13.5。ゲートによる遮断でマスクされていないことの
//      裏取り。compute(0, ...) との違いは積分が保持されるか 0 化される
//      かで判別できる）
//   K. 1輪だけがモータロック条件（出力指令が閾値以上・計測速度が閾値
//      以下）の継続で発火したとき、発火した輪だけが遮断値になり、
//      他の2輪は非ゼロのデューティを保つこと（要件 14.1, 14.2, 14.3）
//   L. status() は未設定状態では合成オブジェクトへ一切触れず既定値
//      （configured=false）を返すこと。設定済み状態では最終出力
//      （wheel_duty・global_reasons・wheel_reasons）・計測速度・エンコーダ
//      累積カウント・PWM上限・平滑後電圧と妥当性・低電圧状態・オドメトリが
//      直近の step() の結果と一致し、かつ status() の呼び出し自体がポート
//      へ一切触れない（読み出し回数が増えない）こと（要件 15.5 の
//      effectiveConfig 以外の全フィールド、17.7）
//   M. 指令反映遅れ（command_apply_latency_ms）が、新しい指令が反映された
//      最初の制御ステップで「現在時刻 - 指令の有効時刻」として算出され、
//      次に反映が切り替わるまで値が保持されること。また
//      step() の呼び出し間隔より短い周期で複数の指令が届いても、最後の
//      遷移の遅れだけが報告されること（要件 17.1。design.md
//      "DrivetrainController" Implementation Notes の回帰シナリオ）。
//      実測の制御周期（last_step_interval_ms）が各実ステップの dt と
//      一致することも併せて確認する（要件 17.1。command_apply_latency_ms
//      と同じく「既存の運動モデルと同一の単位と定義で扱う」対象）

using drivetrain_control::BlockMask;
using drivetrain_control::BlockReason;
using drivetrain_control::CommandAcceptance;
using drivetrain_control::ConfigDiagnostic;
using drivetrain_control::ConfigError;
using drivetrain_control::DrivetrainConfig;
using drivetrain_control::DrivetrainController;
using drivetrain_control::DrivetrainStatus;
using drivetrain_control::DurationMs;
using drivetrain_control::EncoderCounts;
using drivetrain_control::EncoderPort;
using drivetrain_control::GeometryParams;
using drivetrain_control::Kinematics;
using drivetrain_control::kWheelCount;
using drivetrain_control::LowVoltageState;
using drivetrain_control::MotorOutputPort;
using drivetrain_control::BatteryVoltagePort;
using drivetrain_control::Ports;
using drivetrain_control::StepResult;
using drivetrain_control::TimeMs;
using drivetrain_control::VoltageSample;
using drivetrain_control::WheelOutputs;
using drivetrain_control::WheelVelocityCommand;
using drivetrain_control::units::kPi;

namespace drivetrain_control {

// タスク6.4時点: status() が計測速度・エンコーダ基準値・オドメトリを公開
// する読み出し手段になったため、旧 lastMeasuredMmS/lastEncoderCounts/
// odometryState はこの friend から削除した（呼び出し側は controller.status()
// を使う）。タスク6.5で公開 submit()/setOutputEnabled() が追加された
// ことで、内部の CommandInput へ直接委譲していた submitWheelVelocities/
// setOutputEnabled も不要になり削除した（呼び出し側は controller.submit(...)
// / controller.setOutputEnabled(...) を使う）。残る2メソッド
// （lastCommandedDuty/pidIntegral）は、applyWheelOutputs() の「上限適用後・
// 遮断前」の値（status().wheel_duty とは異なる最終値以外の内部値）や PID
// の内部積分が DrivetrainStatus に含まれないフィールドであるため、
// 引き続き controller.hpp が宣言する friend を通じてこのテストだけが
// 読み取る（ファイル先頭コメント、controller.hpp の friend 宣言直前コメント
// 参照）。
struct ControllerStepTestHooks {
  // タスク6.3: applyWheelOutputs() が返した「上限適用後・遮断前」の出力
  // 指令（極性適用前）。
  static const float* lastCommandedDuty(const DrivetrainController& c) { return c.last_commanded_duty_; }

  // タスク6.3: 輪 wheel の VelocityPid が保持する積分値（テスト・診断用の
  // VelocityPid::integral() をそのまま中継する）。ウォッチドッグ発火時に
  // holdReset() が実際に積分を 0 化したことを、最終出力のゲートでは
  // マスクされない形で確認するために使う。
  static float pidIntegral(const DrivetrainController& c, std::uint8_t wheel) {
    return c.pid_[wheel].get().integral();
  }
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
  // （MockEncoderPort::counts の既定値）。status() 経由で確認する（タスク
  // 6.4。要件 17.7 の裏取りも兼ねる）。
  {
    const DrivetrainStatus baseline_status = controller.status();
    for (std::uint8_t i = 0; i < kWheelCount; ++i) {
      TEST_ASSERT_EQUAL_INT64(0, baseline_status.encoder_count[i]);
    }
  }

  // 輪0: 1回転ぶん正転、輪1: 1回転ぶん逆転（polarity=-1 で吸収されて
  // 輪0と同じ向きの正の速度になるはず）、輪2: 変化無し。dt = 1000ms
  // (=1.0s) を選び、暗算しやすい値にする。
  fx.encoder.counts.count[0] = config.encoder.counts_per_wheel_rev;   // 836
  fx.encoder.counts.count[1] = -config.encoder.counts_per_wheel_rev;  // -836
  fx.encoder.counts.count[2] = 0;

  const StepResult result = controller.step(/*now=*/1000);

  // タスク6.3時点: このテストは CommandInput へ一度も指令を投入せず、
  // 出力許可も与えていない（friend アクセサを使っていない）。そのため
  // 出力段まで進むと kOutputDisabled・kNoCommandYet に加え、
  // CommandWatchdog::update() の Postconditions（has_command が偽なら
  // 無条件に発火中）どおり kCommandTimeout も機体全体の理由として立ち、
  // デューティは遮断値（ゼロ）のまま MotorOutputPort::write() が実際に
  // 呼ばれる（タスク6.2時点の「出力は未配線」という前提から変わった
  // 部分）。
  const BlockMask kExpectedGlobalReasons = static_cast<BlockMask>(BlockReason::kOutputDisabled) |
                                            static_cast<BlockMask>(BlockReason::kNoCommandYet) |
                                            static_cast<BlockMask>(BlockReason::kCommandTimeout);
  TEST_ASSERT_EQUAL_UINT16(kExpectedGlobalReasons, result.global_reasons);
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    TEST_ASSERT_EQUAL_FLOAT(0.0f, result.outputs.duty[i]);
    TEST_ASSERT_EQUAL_UINT16(0, result.wheel_reasons[i]);
  }
  TEST_ASSERT_TRUE(result.outputs_written);
  TEST_ASSERT_EQUAL_INT(1, fx.motor.write_count);   // 出力段が実際に書き出す
  TEST_ASSERT_EQUAL_INT(1, fx.battery.read_count);  // ReadBatt も実行される

  // カウント差と dt=1.0s から求まる計測速度。1回転 = pi * wheel_diameter_mm
  // ぶんの距離。輪1は生の変位が負だが polarity=-1 で符号が反転し、輪0と
  // 同じ正の値になる（A/B 逆結線の吸収、要件 6.6）。status() 経由で確認
  // する（タスク6.4）。
  const float expected_speed = kPi * config.encoder.wheel_diameter_mm;  // ≈188.4956 mm/s (dt=1.0s)
  const DrivetrainStatus status_after_step = controller.status();
  const float* measured = status_after_step.wheel_measured_mm_s;
  TEST_ASSERT_FLOAT_WITHIN(kTight, expected_speed, measured[0]);
  TEST_ASSERT_FLOAT_WITHIN(kTight, expected_speed, measured[1]);
  TEST_ASSERT_FLOAT_WITHIN(kTight, 0.0f, measured[2]);

  // エンコーダ基準値が更新されている。
  TEST_ASSERT_EQUAL_INT64(fx.encoder.counts.count[0], status_after_step.encoder_count[0]);
  TEST_ASSERT_EQUAL_INT64(fx.encoder.counts.count[1], status_after_step.encoder_count[1]);
  TEST_ASSERT_EQUAL_INT64(fx.encoder.counts.count[2], status_after_step.encoder_count[2]);

  // Odometry が実際に更新されている: 同じ計測速度配列を独立に構築した
  // Kinematics::forward() へ渡した結果と、controller 内部の Odometry が
  // 保持する body_velocity が一致すること（DrivetrainController が
  // Odometry::update() を正しく呼んでいることの裏取り）。
  const Kinematics reference_kinematics(config.geometry);
  const auto expected_body_velocity = reference_kinematics.forward(measured);
  const OdometryState& odom = status_after_step.odometry;
  TEST_ASSERT_FLOAT_WITHIN(kTight, expected_body_velocity.vx_mm_s, odom.body_velocity.vx_mm_s);
  TEST_ASSERT_FLOAT_WITHIN(kTight, expected_body_velocity.vy_mm_s, odom.body_velocity.vy_mm_s);
  TEST_ASSERT_FLOAT_WITHIN(kTight, expected_body_velocity.omega_rad_s, odom.body_velocity.omega_rad_s);
  TEST_ASSERT_TRUE(odom.traveled_mm > 0.0f);

  // 実測の制御周期（要件 17.1）: dt = now(1000) - configure()時のnow(0) =
  // 1000ms がそのまま status() へ反映されている。
  TEST_ASSERT_EQUAL_INT32(1000, status_after_step.last_step_interval_ms);

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
  const int battery_read_count_after_first = fx.battery.read_count;
  const int motor_write_count_after_first = fx.motor.write_count;
  TEST_ASSERT_EQUAL_INT(1, battery_read_count_after_first);
  TEST_ASSERT_EQUAL_INT(1, motor_write_count_after_first);

  const DrivetrainStatus status_after_first = controller.status();
  const float measured0_after_first = status_after_first.wheel_measured_mm_s[0];
  const EncoderCounts baseline_after_first = [&] {
    EncoderCounts counts{};
    for (std::uint8_t i = 0; i < kWheelCount; ++i) {
      counts.count[i] = status_after_first.encoder_count[i];
    }
    return counts;
  }();

  // ポートの中身を変える（本来なら次の実ステップで反映されるはずの変化）。
  fx.encoder.counts.count[0] = config.encoder.counts_per_wheel_rev * 100;

  // 同じ時刻で再度呼ぶ ―― 短絡し、上記の変化は一切反映されない。
  const StepResult second = controller.step(/*now=*/1000);

  TEST_ASSERT_EQUAL_INT(2, fx.encoder.read_count);  // 増えていない = 読んでいない
  TEST_ASSERT_EQUAL_INT(battery_read_count_after_first, fx.battery.read_count);  // ReadBatt も短絡で増えない
  TEST_ASSERT_EQUAL_INT(motor_write_count_after_first, fx.motor.write_count);    // Write も短絡で増えない
  const DrivetrainStatus status_after_second = controller.status();
  TEST_ASSERT_FLOAT_WITHIN(kTight, measured0_after_first, status_after_second.wheel_measured_mm_s[0]);
  TEST_ASSERT_EQUAL_INT64(baseline_after_first.count[0], status_after_second.encoder_count[0]);
  // status() 自体もポートへ触れていないこと（要件 17.7）。
  TEST_ASSERT_EQUAL_INT(2, fx.encoder.read_count);
  TEST_ASSERT_EQUAL_INT(battery_read_count_after_first, fx.battery.read_count);
  TEST_ASSERT_EQUAL_INT(motor_write_count_after_first, fx.motor.write_count);
  // status() の last_step_interval_ms は短絡では再計算されず、直近の実
  // ステップ（now=1000, configure()時=now=0）の dt=1000ms のまま。
  TEST_ASSERT_EQUAL_INT32(1000, status_after_second.last_step_interval_ms);

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

// ---------------------------------------------------------------------------
// I: PWM 上限が下がっている状態でも、3輪の生の PID 出力の大きさが異なって
//    いても、全輪へ同一の縮小率が掛かり、指令された比率（方向）が保たれる
//    （要件 6.5, 12.4。輪ごとの個別クリップではないことの裏取り）。
// ---------------------------------------------------------------------------

void test_pwm_ceiling_shrinks_all_wheels_by_the_same_ratio(void) {
  DrivetrainController controller;
  Fixture fx;

  DrivetrainConfig config = makeValidConfig();
  // ki=kd=0 にし、raw = kp * target（測定速度は常に0のまま）が指令に厳密に
  // 比例するようにする。等比縮小の前後で比率が保たれることを純粋な数値
  // 比較として検証しやすくするための、このテスト専用の単純化。
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    config.pid[i] = {/*kp=*/0.002f, /*ki=*/0.0f, /*kd=*/0.0f, /*integral_limit=*/1.0f};
  }
  // 極性の符号で比率比較が複雑にならないよう、このテストでは全輪 +1 にする
  // （極性の適用そのものは他のテスト・実装コメントで扱う）。
  config.output.polarity[0] = 1;
  config.output.polarity[1] = 1;
  config.output.polarity[2] = 1;
  // 上限を意図的に下げる（基準電圧を下げることで、電圧はそのままに上限だけ
  // 下がった状況を模す。要件 12.1 の「電圧に応じて上限が下がる」ことその
  // ものは test_protection_pwm_ceiling.cpp が検証済みであり、ここでは
  // 「下がった上限の全輪への同一適用」だけを見る）。
  config.pwm_ceiling.reference_milli_volts = 1000;

  TEST_ASSERT_TRUE(controller.configure(config, fx.ports(), /*now=*/0).ok());

  fx.battery.sample = VoltageSample{/*valid=*/true, /*milli_volts=*/12600};

  controller.setOutputEnabled(true, /*now=*/0);

  WheelVelocityCommand command{};
  command.wheel_mm_s[0] = 200.0f;
  command.wheel_mm_s[1] = 400.0f;
  command.wheel_mm_s[2] = 600.0f;
  command.issued_at_ms = 0;
  const CommandAcceptance acceptance = controller.submit(command);
  TEST_ASSERT_TRUE(acceptance.accepted);
  TEST_ASSERT_FALSE(acceptance.clamped);  // max_wheel_speed_mm_s(1000) 以内

  const StepResult result = controller.step(/*now=*/100);

  // 遮断理由が一切立っていないこと（出力許可済み・指令あり・低電圧無し・
  // ロック無し・ウォッチドッグ未発火）。
  TEST_ASSERT_EQUAL_UINT16(0, result.global_reasons);
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    TEST_ASSERT_EQUAL_UINT16(0, result.wheel_reasons[i]);
  }

  // 期待値: raw = kp * target（ki=kd=0, measured=0 のため厳密に比例）。
  // ceiling = reference / measured = 1000 / 12600。scale = ceiling / max(raw)。
  const float kp = config.pid[0].kp;
  const float raw0 = kp * command.wheel_mm_s[0];
  const float raw1 = kp * command.wheel_mm_s[1];
  const float raw2 = kp * command.wheel_mm_s[2];
  const float ceiling = static_cast<float>(config.pwm_ceiling.reference_milli_volts) /
                         static_cast<float>(fx.battery.sample.milli_volts);
  const float scale = ceiling / raw2;  // raw2 が最大（絶対値も最大）
  const float expected0 = raw0 * scale;
  const float expected1 = raw1 * scale;
  const float expected2 = raw2 * scale;

  TEST_ASSERT_FLOAT_WITHIN(kTight, expected0, result.outputs.duty[0]);
  TEST_ASSERT_FLOAT_WITHIN(kTight, expected1, result.outputs.duty[1]);
  TEST_ASSERT_FLOAT_WITHIN(kTight, expected2, result.outputs.duty[2]);

  // 実際に縮小が起きたこと（そうでなければこのテストは何も検証していない
  // ことになる）。
  TEST_ASSERT_TRUE(result.outputs.duty[2] < raw2);

  // 方向（比率）が指令どおりに保たれていること ―― 個別クリップだとここが
  // 崩れる。
  TEST_ASSERT_FLOAT_WITHIN(kTight, 2.0f, result.outputs.duty[1] / result.outputs.duty[0]);
  TEST_ASSERT_FLOAT_WITHIN(kTight, 3.0f, result.outputs.duty[2] / result.outputs.duty[0]);
}

// ---------------------------------------------------------------------------
// J: ウォッチドッグ発火中は、最終出力がゲートで遮断されるだけでなく、PID
//    の内部状態（積分）が実際に holdReset() で 0 化され、上限適用後・
//    遮断前の出力指令自体が 0 になる（要件 13.5）。gating による遮断だけ
//    では compute(0, measured, dt) との違いを検出できない
//    （commit() 済みの積分がそのまま残ってしまうため）ので、内部状態を
//    直接確認する。
// ---------------------------------------------------------------------------

void test_watchdog_trip_zeroes_pid_internal_state_not_just_gated_output(void) {
  DrivetrainController controller;
  Fixture fx;

  DrivetrainConfig config = makeValidConfig();
  config.pwm_ceiling.enabled = false;  // 上限をこのテストの関心から外す
  // 小さいゲインにし、1回目のステップで生の出力が上限(absolute_max_duty=
  // 1.0)以内に収まるようにする。makeValidConfig() の既定ゲイン（kp=0.5,
  // ki=2.0）だと raw が上限を大きく超えて等比縮小され、commit() の条件付き
  // 積分（このステップの増分をまるごと取り消す）が働いてしまい、1回目の
  // ステップ直後の積分がたまたま0になって「holdReset() が積分を0にした」
  // ことの裏取りにならない。ここでは縮小が起きない小さいゲインを使い、
  // 1回目のステップ後に積分が明確に非0で残ることを先に確定させる。
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    config.pid[i] = {/*kp=*/0.001f, /*ki=*/0.001f, /*kd=*/0.0f, /*integral_limit=*/1.0f};
  }

  TEST_ASSERT_TRUE(controller.configure(config, fx.ports(), /*now=*/0).ok());

  fx.battery.sample = VoltageSample{/*valid=*/true, /*milli_volts=*/12600};

  controller.setOutputEnabled(true, /*now=*/0);

  WheelVelocityCommand command{};
  command.wheel_mm_s[0] = 300.0f;
  command.wheel_mm_s[1] = 300.0f;
  command.wheel_mm_s[2] = 300.0f;
  command.issued_at_ms = 0;
  TEST_ASSERT_TRUE(controller.submit(command).accepted);

  // 1回目: ウォッチドッグのタイムアウト(300ms)以内。指令が実際に PID を
  // 駆動していることを確認する（積分が動き、上限適用後の出力指令が
  // 非ゼロになる）。
  const StepResult first = controller.step(/*now=*/100);
  TEST_ASSERT_EQUAL_UINT16(0, first.global_reasons);
  const float* first_applied = ControllerStepTestHooks::lastCommandedDuty(controller);
  bool any_nonzero_after_first = false;
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    if (std::fabs(first_applied[i]) > 1.0e-6f) {
      any_nonzero_after_first = true;
    }
  }
  TEST_ASSERT_TRUE(any_nonzero_after_first);
  bool any_nonzero_integral_after_first = false;
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    if (std::fabs(ControllerStepTestHooks::pidIntegral(controller, i)) > 1.0e-6f) {
      any_nonzero_integral_after_first = true;
    }
  }
  TEST_ASSERT_TRUE(any_nonzero_integral_after_first);

  // 2回目: 新しい指令を一切投入せずに、ウォッチドッグのタイムアウトを
  // 大きく超えた時刻へ進める（issued_at_ms=0 から 500ms 経過、
  // timeout_ms=300）。
  const StepResult second = controller.step(/*now=*/500);

  const BlockMask kCommandTimeoutBit = static_cast<BlockMask>(BlockReason::kCommandTimeout);
  TEST_ASSERT_TRUE((second.global_reasons & kCommandTimeoutBit) != 0);

  // 最終出力（ゲート後）が 0 なのは当然として、ゲートより前の
  // 「上限適用後・遮断前」の出力指令そのものが 0 であることを確認する。
  // compute(0, measured, dt) を呼んでいた場合、measured もほぼ0のままなら
  // raw はほぼ0だが積分は前回値(非0)のまま残ってしまい、次に述べる積分の
  // 0化では検出できてしまう差異が生じる。holdReset() はここを直接 0 に
  // する。
  const float* applied_after_trip = ControllerStepTestHooks::lastCommandedDuty(controller);
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    TEST_ASSERT_EQUAL_FLOAT(0.0f, applied_after_trip[i]);
    TEST_ASSERT_EQUAL_FLOAT(0.0f, second.outputs.duty[i]);
  }

  // 決定的な裏取り: holdReset() は積分を無条件で 0 にする。compute(0, ...)
  // では偏差が 0 の間、積分の増分も 0 になるだけで「前回までに積み上がった
  // 積分」はそのまま残る。したがって「積分が 0 になっている」ことは
  // compute(0,...) 経路では起こらない、holdReset() 経路だけが起こす事実
  // である。
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    TEST_ASSERT_EQUAL_FLOAT(0.0f, ControllerStepTestHooks::pidIntegral(controller, i));
  }
}

// ---------------------------------------------------------------------------
// K: 1輪だけがモータロック条件（出力指令が閾値以上・計測速度が閾値以下）
//    の継続で発火したとき、発火した輪だけが遮断値になり、他の2輪は非ゼロ
//    のデューティを保つ（要件 14.1, 14.2, 14.3）。
// ---------------------------------------------------------------------------

void test_motor_lock_gates_only_the_tripped_wheel(void) {
  DrivetrainController controller;
  Fixture fx;

  DrivetrainConfig config = makeValidConfig();
  // ウォッチドッグと今回のロック判定のタイミングを分離する（このテストの
  // 関心はロックだけであり、ウォッチドッグを同時に発火させたくない）。
  config.watchdog.timeout_ms = 100000;
  // ki=kd=0 にし、raw = kp * target が測定速度に依存せず一定になるように
  // する（測定速度は常に0のまま。3輪とも同一の PID ゲインを使う）。
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    config.pid[i] = {/*kp=*/0.002f, /*ki=*/0.0f, /*kd=*/0.0f, /*integral_limit=*/1.0f};
  }
  config.output.polarity[0] = 1;
  config.output.polarity[1] = 1;
  config.output.polarity[2] = 1;
  // wheel0 の raw(0.6) は lock.duty_threshold(0.3) 以上、wheel1/2 の
  // raw(0.2) は未満に収まるようにする（== duty_threshold ではなく明確に
  // 下回らせる）。measured は常に0（<= speed_threshold_mm_s）。
  TEST_ASSERT_TRUE(config.lock.duty_threshold > 0.2f);
  TEST_ASSERT_TRUE(config.lock.duty_threshold < 0.6f);

  TEST_ASSERT_TRUE(controller.configure(config, fx.ports(), /*now=*/0).ok());

  fx.battery.sample = VoltageSample{/*valid=*/true, /*milli_volts=*/12600};  // config.pwm_ceiling.reference と同じ
                                                                              // ⇒ ceiling は absolute_max_duty(1.0)
                                                                              // に張り付き、縮小が起きない

  controller.setOutputEnabled(true, /*now=*/0);

  WheelVelocityCommand command{};
  command.wheel_mm_s[0] = 300.0f;  // -> raw = 0.6
  command.wheel_mm_s[1] = 100.0f;  // -> raw = 0.2
  command.wheel_mm_s[2] = 100.0f;  // -> raw = 0.2
  command.issued_at_ms = 0;
  TEST_ASSERT_TRUE(controller.submit(command).accepted);

  // 1回目: 条件が初めて成立し Idle -> Suspect (継続時間の計測が始まる)。
  // duration_ms(300ms) にはまだ満たないので、まだ発火しない。
  const StepResult first = controller.step(/*now=*/100);
  TEST_ASSERT_EQUAL_UINT16(0, first.wheel_reasons[0]);
  TEST_ASSERT_EQUAL_UINT16(0, first.wheel_reasons[1]);
  TEST_ASSERT_EQUAL_UINT16(0, first.wheel_reasons[2]);
  TEST_ASSERT_TRUE(first.outputs.duty[0] > 0.0f);  // まだ遮断されていない

  // 2回目: 条件が成立したまま duration_ms(300ms) を厳密に超える時刻まで
  // 進める（Suspect 開始が now=100 なので、now=500 なら 400ms 経過）。
  const StepResult second = controller.step(/*now=*/500);

  const BlockMask kMotorLockBit = static_cast<BlockMask>(BlockReason::kMotorLock);
  TEST_ASSERT_EQUAL_UINT16(kMotorLockBit, second.wheel_reasons[0]);
  TEST_ASSERT_EQUAL_UINT16(0, second.wheel_reasons[1]);
  TEST_ASSERT_EQUAL_UINT16(0, second.wheel_reasons[2]);

  // 発火した輪だけが遮断値(0)になり、他の2輪は非ゼロのデューティを保つ。
  TEST_ASSERT_EQUAL_FLOAT(0.0f, second.outputs.duty[0]);
  TEST_ASSERT_TRUE(second.outputs.duty[1] > 0.0f);
  TEST_ASSERT_TRUE(second.outputs.duty[2] > 0.0f);

  // 機体全体の遮断理由は立っていない（このテストの関心はロックだけ）。
  TEST_ASSERT_EQUAL_UINT16(0, second.global_reasons);
}

// ---------------------------------------------------------------------------
// L: status() は未設定状態では合成オブジェクトへ一切触れず既定値を返し、
//    設定済み状態では直近の step() の結果と一致するスナップショットを、
//    ポートへ一切触れずに返す（要件 15.5 以外の DrivetrainStatus 全フィー
//    ルド、17.7）。
// ---------------------------------------------------------------------------

void test_status_before_configure_returns_defaults(void) {
  DrivetrainController controller;

  // configure() を一度も呼んでいない ―― 合成オブジェクトは未構築。
  // status() がこれらへ触れれば未定義動作になるはずだが、既定値
  // （configured=false）を返すだけで済むことを確認する。
  const DrivetrainStatus status = controller.status();
  TEST_ASSERT_FALSE(status.configured);
  TEST_ASSERT_FALSE(status.output_enabled);
  TEST_ASSERT_FALSE(status.has_valid_command);
  TEST_ASSERT_EQUAL_UINT16(0, status.global_reasons);
}

void test_status_reflects_final_snapshot_and_does_not_touch_ports(void) {
  DrivetrainController controller;
  Fixture fx;

  DrivetrainConfig config = makeValidConfig();
  // ki=kd=0 にし、raw = kp * (target - measured) が厳密に求まるようにする
  // （test_pwm_ceiling_shrinks_all_wheels_by_the_same_ratio と同じ流儀）。
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    config.pid[i] = {/*kp=*/0.001f, /*ki=*/0.0f, /*kd=*/0.0f, /*integral_limit=*/1.0f};
  }
  config.output.polarity[0] = 1;
  config.output.polarity[1] = 1;
  config.output.polarity[2] = 1;
  // ceiling = min(absolute_max_duty, reference/measured) = min(1.0, 6300/12600)
  // = 0.5 ちょうど。以下の raw の絶対値最大(0.2)より大きいので縮小は起きない
  // （縮小そのものは test_pwm_ceiling_shrinks_all_wheels_by_the_same_ratio が
  // 別途検証済み。ここでは pwm_ceiling フィールドの配線だけを見る）。
  config.pwm_ceiling.reference_milli_volts = 6300;
  // このテストの関心はロック・ウォッチドッグではないため、条件に触れない
  // 値へ離しておく。
  config.watchdog.timeout_ms = 100000;

  TEST_ASSERT_TRUE(controller.configure(config, fx.ports(), /*now=*/0).ok());

  fx.battery.sample = VoltageSample{/*valid=*/true, /*milli_volts=*/12600};
  // 輪0だけ1回転ぶん正転させ、計測速度とオドメトリの配線を同時に確認する
  // （test_step_advances_time_computes_measured_speed_and_updates_odometry
  // と同じ値）。
  fx.encoder.counts.count[0] = config.encoder.counts_per_wheel_rev;  // 836
  fx.encoder.counts.count[1] = 0;
  fx.encoder.counts.count[2] = 0;

  controller.setOutputEnabled(true, /*now=*/0);

  WheelVelocityCommand command{};
  command.wheel_mm_s[0] = 100.0f;
  command.wheel_mm_s[1] = 150.0f;
  command.wheel_mm_s[2] = 200.0f;
  command.issued_at_ms = 0;
  const CommandAcceptance acceptance = controller.submit(command);
  TEST_ASSERT_TRUE(acceptance.accepted);
  TEST_ASSERT_FALSE(acceptance.clamped);

  const StepResult result = controller.step(/*now=*/1000);

  // このシナリオでは理由が一切立たない前提（後続の status() 比較の土台）。
  TEST_ASSERT_EQUAL_UINT16(0, result.global_reasons);
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    TEST_ASSERT_EQUAL_UINT16(0, result.wheel_reasons[i]);
  }

  const int encoder_read_count_after_step = fx.encoder.read_count;
  const int motor_write_count_after_step = fx.motor.write_count;
  const int battery_read_count_after_step = fx.battery.read_count;

  const DrivetrainStatus status = controller.status();

  // status() 自体はポートへ一切触れない（要件 17.7。複数回呼んでも同じ）。
  (void)controller.status();
  (void)controller.status();
  TEST_ASSERT_EQUAL_INT(encoder_read_count_after_step, fx.encoder.read_count);
  TEST_ASSERT_EQUAL_INT(motor_write_count_after_step, fx.motor.write_count);
  TEST_ASSERT_EQUAL_INT(battery_read_count_after_step, fx.battery.read_count);

  TEST_ASSERT_TRUE(status.configured);
  TEST_ASSERT_TRUE(status.output_enabled);
  TEST_ASSERT_TRUE(status.has_valid_command);
  TEST_ASSERT_FALSE(status.last_command_clamped);
  TEST_ASSERT_EQUAL_INT64(0, status.last_command_at_ms);
  TEST_ASSERT_EQUAL_INT64(1000, status.now_ms);

  // 輪ごとの目標速度は指令のまま（クランプ無し）。
  TEST_ASSERT_FLOAT_WITHIN(kTight, 100.0f, status.wheel_target_mm_s[0]);
  TEST_ASSERT_FLOAT_WITHIN(kTight, 150.0f, status.wheel_target_mm_s[1]);
  TEST_ASSERT_FLOAT_WITHIN(kTight, 200.0f, status.wheel_target_mm_s[2]);

  // 計測速度: 輪0だけ1回転ぶん(dt=1.0s)。
  const float expected_speed0 = kPi * config.encoder.wheel_diameter_mm;
  TEST_ASSERT_FLOAT_WITHIN(kTight, expected_speed0, status.wheel_measured_mm_s[0]);
  TEST_ASSERT_FLOAT_WITHIN(kTight, 0.0f, status.wheel_measured_mm_s[1]);
  TEST_ASSERT_FLOAT_WITHIN(kTight, 0.0f, status.wheel_measured_mm_s[2]);

  // デューティ: status().wheel_duty は「ゲート適用後・出力極性適用後」の
  // 最終値であり、StepResult.outputs.duty と一致する（要件 17.7 の裏取り。
  // wheel_duty はここでは result.outputs.duty を正としてそのまま比較する
  // ―― raw = kp*(target-measured) の手計算は他テストで既に検証済み）。
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    TEST_ASSERT_EQUAL_FLOAT(result.outputs.duty[i], status.wheel_duty[i]);
  }
  // 実際に PID が駆動していること（このテストが何も検証していない状態を
  // 避けるための最低限の裏取り）。
  TEST_ASSERT_TRUE(status.wheel_duty[1] > 0.0f);

  // エンコーダ累積カウント。
  TEST_ASSERT_EQUAL_INT64(fx.encoder.counts.count[0], status.encoder_count[0]);
  TEST_ASSERT_EQUAL_INT64(fx.encoder.counts.count[1], status.encoder_count[1]);
  TEST_ASSERT_EQUAL_INT64(fx.encoder.counts.count[2], status.encoder_count[2]);

  // PWM 上限: min(1.0, 6300/12600) = 0.5。
  TEST_ASSERT_FLOAT_WITHIN(kTight, 0.5f, status.pwm_ceiling);

  // 平滑後の電圧と妥当性: 単一サンプルの移動平均はサンプル自身に一致する。
  TEST_ASSERT_EQUAL_INT32(12600, status.battery_milli_volts);
  TEST_ASSERT_TRUE(status.battery_valid);
  TEST_ASSERT_TRUE(status.low_voltage_state == LowVoltageState::kNormal);

  // 遮断理由: StepResult と一致する（このシナリオでは両方ゼロ）。
  TEST_ASSERT_EQUAL_UINT16(result.global_reasons, status.global_reasons);
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    TEST_ASSERT_EQUAL_UINT16(result.wheel_reasons[i], status.wheel_reasons[i]);
  }

  // オドメトリ: 同じ計測速度配列を独立に構築した Kinematics::forward() の
  // 結果と一致する。
  const Kinematics reference_kinematics(config.geometry);
  const auto expected_body_velocity = reference_kinematics.forward(status.wheel_measured_mm_s);
  TEST_ASSERT_FLOAT_WITHIN(kTight, expected_body_velocity.vx_mm_s, status.odometry.body_velocity.vx_mm_s);
  TEST_ASSERT_FLOAT_WITHIN(kTight, expected_body_velocity.vy_mm_s, status.odometry.body_velocity.vy_mm_s);
  TEST_ASSERT_FLOAT_WITHIN(kTight, expected_body_velocity.omega_rad_s, status.odometry.body_velocity.omega_rad_s);
  TEST_ASSERT_TRUE(status.odometry.traveled_mm > 0.0f);

  // 実測の制御周期（要件 17.1）: dt = 1000 - 0。
  TEST_ASSERT_EQUAL_INT32(1000, status.last_step_interval_ms);
}

// ---------------------------------------------------------------------------
// M: 指令反映遅れ（command_apply_latency_ms）は、指令が latched() へ反映
//    された最初の制御ステップで算出され、次の反映まで保持される。
//    step() の呼び出し間隔より短い周期で複数の指令が届いても、最後の遷移
//    の遅れだけが報告される（要件 17.1）。実測の制御周期（同じく要件
//    17.1）も併せて確認する。
// ---------------------------------------------------------------------------

void test_command_apply_latency_computed_on_first_step_and_held_until_next_change(void) {
  DrivetrainController controller;
  Fixture fx;

  DrivetrainConfig config = makeValidConfig();
  // ロック・ウォッチドッグがこのテストの関心を横取りしないよう離しておく。
  config.watchdog.timeout_ms = 100000;
  fx.battery.sample = VoltageSample{/*valid=*/true, /*milli_volts=*/12600};

  TEST_ASSERT_TRUE(controller.configure(config, fx.ports(), /*now=*/0).ok());

  // configure() 直後（実ステップ未経過）: まだ一度も指令が反映されていない
  // ため、command_apply_latency_ms は既定値のまま。
  TEST_ASSERT_EQUAL_INT32(0, controller.status().command_apply_latency_ms);

  // 指令Aを issued_at_ms=50 で投入する（起動後最初の指令なので無条件で
  // 受理される）。
  WheelVelocityCommand command_a{};
  command_a.wheel_mm_s[0] = 10.0f;
  command_a.wheel_mm_s[1] = 10.0f;
  command_a.wheel_mm_s[2] = 10.0f;
  command_a.issued_at_ms = 50;
  TEST_ASSERT_TRUE(controller.submit(command_a).accepted);

  // 1回目の実ステップ(now=120): 指令Aがこのステップで初めて反映される。
  // command_apply_latency_ms = now(120) - issued_at_ms(50) = 70。
  controller.step(/*now=*/120);
  DrivetrainStatus status = controller.status();
  TEST_ASSERT_EQUAL_INT32(70, status.command_apply_latency_ms);
  TEST_ASSERT_EQUAL_INT32(120, status.last_step_interval_ms);  // dt = 120 - 0(configure時)

  // 2回目の実ステップ(now=200): 新しい指令を投入していないため、
  // latched().issued_at_ms は変化しない ―― 値が保持される。
  controller.step(/*now=*/200);
  status = controller.status();
  TEST_ASSERT_EQUAL_INT32(70, status.command_apply_latency_ms);  // 保持
  TEST_ASSERT_EQUAL_INT32(80, status.last_step_interval_ms);     // dt = 200 - 120

  // step() の呼び出し間隔(200→300)より短い周期で指令B(issued_at_ms=210)・
  // 指令C(issued_at_ms=220)が届く ―― どちらも step() を経由せずに latched()
  // を上書きする。次の実ステップで latched() が返すのは指令Cだけである。
  WheelVelocityCommand command_b{};
  command_b.wheel_mm_s[0] = 20.0f;
  command_b.wheel_mm_s[1] = 20.0f;
  command_b.wheel_mm_s[2] = 20.0f;
  command_b.issued_at_ms = 210;
  TEST_ASSERT_TRUE(controller.submit(command_b).accepted);

  WheelVelocityCommand command_c{};
  command_c.wheel_mm_s[0] = 30.0f;
  command_c.wheel_mm_s[1] = 30.0f;
  command_c.wheel_mm_s[2] = 30.0f;
  command_c.issued_at_ms = 220;
  TEST_ASSERT_TRUE(controller.submit(command_c).accepted);

  // 3回目の実ステップ(now=300): 反映されるのは指令Cのみ。
  // command_apply_latency_ms = now(300) - issued_at_ms(220) = 80。
  // 指令Bの遅れ(300-210=90)ではないこと、保持されていた70でもないことを
  // 確認する ―― 「最後の遷移の遅れ」だけが報告される（design.md
  // "DrivetrainController" Implementation Notes 3つ目の回帰シナリオ）。
  controller.step(/*now=*/300);
  status = controller.status();
  TEST_ASSERT_EQUAL_INT32(80, status.command_apply_latency_ms);
  TEST_ASSERT_EQUAL_INT32(100, status.last_step_interval_ms);  // dt = 300 - 200
  TEST_ASSERT_EQUAL_INT64(220, status.last_command_at_ms);
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
  RUN_TEST(test_pwm_ceiling_shrinks_all_wheels_by_the_same_ratio);
  RUN_TEST(test_watchdog_trip_zeroes_pid_internal_state_not_just_gated_output);
  RUN_TEST(test_motor_lock_gates_only_the_tripped_wheel);
  RUN_TEST(test_status_before_configure_returns_defaults);
  RUN_TEST(test_status_reflects_final_snapshot_and_does_not_touch_ports);
  RUN_TEST(test_command_apply_latency_computed_on_first_step_and_held_until_next_change);

  return UNITY_END();
}
