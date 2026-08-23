#include <unity.h>

#include "drivetrain_control/config.hpp"
#include "drivetrain_control/protection/supervisor.hpp"
#include "drivetrain_control/types.hpp"

// drivetrain-core task 5.5: 保護①②④の合成・遮断値の決定・復帰操作
// （ProtectionSupervisor）を検証するホストテスト。
//
// design.md "ProtectionSupervisor"（L7-L8 保護層）の厳密なシグネチャ・
// 「検出器を1回進める」と「直近の結果を合成する」の分離契約を対象にする。
// 要件 14.1〜14.10。
//
// 以下を検証する:
//   A. 低電圧（機体全体）と1輪ロック（輪ごと）を同時に成立させたとき、
//      compose() が機体全体の理由と輪ごとの理由を正しく分けて立てる
//      （要件 14.1, 14.3, 14.4）
//   B. resetProtections() は、条件が継続している保護（まだロックが続いて
//      いる輪・まだ低電圧が続いている電圧）を解かない（要件 14.6 後半）
//   C. resetProtections() は、解除条件を満たした保護（ロック条件が崩れた
//      輪・電圧が復帰閾値まで戻った低電圧）だけを解く（latching = true、
//      要件 14.6 前半）
//   D. 出力未許可（kOutputDisabled）と指令未着（kNoCommandYet）がそれぞれ
//      別の理由として立ち、いずれも applyGate() で遮断値を生む（要件 14.9,
//      14.10）
//   E. applyGate(): 理由が無い輪はデューティを変更せず、理由がある輪
//      （機体全体の理由を含む）だけをデューティ 0 にする（要件 14.1, 14.2）
//   F. compose() は const であり（コンパイル時に検出器の update() を
//      呼べない）、複数回呼んでも検出器の内部状態（継続時間タイマー）を
//      進めない（要件 14.7 のホスト検証可能性、design.md Preconditions の
//      「compose() を検出器の再更新の代わりに使わない」の裏取り）
//   G. latching = false（自動復帰）構成の保護は resetProtections() の対象
//      外である ―― 条件がまだ完全には崩れきっていない間に呼んでも解除
//      されない（design.md Postconditions「保持設定の保護のうち」の
//      限定を裏取り）

using drivetrain_control::BlockMask;
using drivetrain_control::BlockReason;
using drivetrain_control::DrivetrainConfig;
using drivetrain_control::DurationMs;
using drivetrain_control::GateOutcome;
using drivetrain_control::kWheelCount;
using drivetrain_control::ProtectionSupervisor;
using drivetrain_control::TimeMs;
using drivetrain_control::VoltageSample;
using drivetrain_control::WheelOutputs;

namespace {

constexpr float kLockedDuty = 0.8f;    // >= duty_threshold(0.5)
constexpr float kLockedSpeed = 1.0f;   // <= speed_threshold_mm_s(5.0)
constexpr float kFreeDuty = 0.0f;      // < duty_threshold
constexpr float kFreeSpeed = 200.0f;   // > speed_threshold_mm_s

// 低電圧の閾値（mV）。average_window=1 にして移動平均を実測値へ即座に
// 一致させ、1サンプルで判定できるようにする（人が暗算で確認しやすくする
// ため）。
constexpr std::int32_t kWarnMilliVolts = 11000;
constexpr std::int32_t kStopMilliVolts = 10500;
constexpr std::int32_t kRecoverMilliVolts = 11200;
constexpr std::int32_t kBelowStopMilliVolts = 10000;   // < stop
constexpr std::int32_t kAtRecoverMilliVolts = 11300;   // >= recover

DrivetrainConfig makeConfig(bool lock_latching, bool low_voltage_latching) {
  DrivetrainConfig config{};

  config.lock.duty_threshold = 0.5f;
  config.lock.speed_threshold_mm_s = 5.0f;
  config.lock.duration_ms = static_cast<DurationMs>(100);
  config.lock.clear_duration_ms = static_cast<DurationMs>(50);
  config.lock.latching = lock_latching;

  config.low_voltage.warn_milli_volts = kWarnMilliVolts;
  config.low_voltage.stop_milli_volts = kStopMilliVolts;
  config.low_voltage.recover_milli_volts = kRecoverMilliVolts;
  config.low_voltage.average_window = 1;
  config.low_voltage.stop_duration_ms = static_cast<DurationMs>(100);
  config.low_voltage.unavailable_duration_ms = static_cast<DurationMs>(100);
  config.low_voltage.latching = low_voltage_latching;

  config.watchdog.timeout_ms = static_cast<DurationMs>(100);

  return config;
}

VoltageSample validSample(std::int32_t mv) {
  VoltageSample s;
  s.valid = true;
  s.milli_volts = mv;
  return s;
}

// 各ステップで3輪すべての updateLock() をちょうど1回ずつ呼ぶ（design.md
// Preconditions のとおり）。wheel を指定して拘束し、それ以外は自由回転
// とする。
void stepAllWheels(ProtectionSupervisor& sup, TimeMs now, std::uint8_t locked_wheel,
                    bool lock_the_wheel) {
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    if (i == locked_wheel && lock_the_wheel) {
      sup.updateLock(i, kLockedDuty, kLockedSpeed, now);
    } else {
      sup.updateLock(i, kFreeDuty, kFreeSpeed, now);
    }
  }
}

WheelOutputs makeNonZeroOutputs() {
  WheelOutputs outputs;
  outputs.duty[0] = 0.5f;
  outputs.duty[1] = -0.3f;
  outputs.duty[2] = 0.7f;
  return outputs;
}

}  // namespace

void setUp(void) {}
void tearDown(void) {}

// ---------------------------------------------------------------------------
// A: 低電圧（機体全体）と1輪ロック（輪ごと）を同時に成立させたとき、
// compose() が理由を正しく分けて立てる。
// ---------------------------------------------------------------------------

void test_compose_separates_global_low_voltage_from_per_wheel_lock(void) {
  ProtectionSupervisor sup(makeConfig(/*lock_latching=*/true, /*low_voltage_latching=*/true));

  // t=0: 低電圧サンプル投入開始、輪0の拘束開始。ウォッチドッグは指令が
  // 直近に届いている体で非発火に保つ。
  sup.updateLowVoltage(validSample(kBelowStopMilliVolts), /*now=*/0);
  stepAllWheels(sup, /*now=*/0, /*locked_wheel=*/0, /*lock_the_wheel=*/true);
  sup.updateWatchdog(/*now=*/0, /*last_command_at_ms=*/0, /*has_command=*/true);

  // t=101: 両保護の継続時間(100ms)を厳密に超える。
  sup.updateLowVoltage(validSample(kBelowStopMilliVolts), /*now=*/101);
  stepAllWheels(sup, /*now=*/101, /*locked_wheel=*/0, /*lock_the_wheel=*/true);
  sup.updateWatchdog(/*now=*/101, /*last_command_at_ms=*/101, /*has_command=*/true);

  const GateOutcome outcome = sup.compose(/*configured=*/true, /*output_enabled=*/true, /*has_command=*/true);

  const BlockMask kLowVoltageBit = static_cast<BlockMask>(BlockReason::kLowVoltage);
  const BlockMask kMotorLockBit = static_cast<BlockMask>(BlockReason::kMotorLock);

  // 機体全体の理由: 低電圧のみ。
  TEST_ASSERT_EQUAL_UINT16(kLowVoltageBit, outcome.global_reasons);
  // 機体全体の理由にモータロックのビットが混ざらない。
  TEST_ASSERT_FALSE(outcome.global_reasons & kMotorLockBit);

  // 輪ごとの理由: 輪0だけがモータロック。他の輪は理由無し。
  TEST_ASSERT_EQUAL_UINT16(kMotorLockBit, outcome.wheel_reasons[0]);
  TEST_ASSERT_EQUAL_UINT16(0, outcome.wheel_reasons[1]);
  TEST_ASSERT_EQUAL_UINT16(0, outcome.wheel_reasons[2]);
  // 輪ごとの理由に低電圧のビットが混ざらない。
  TEST_ASSERT_FALSE(outcome.wheel_reasons[0] & kLowVoltageBit);
}

// ---------------------------------------------------------------------------
// B: resetProtections() は、条件が継続している保護を解かない。
// ---------------------------------------------------------------------------

void test_reset_protections_does_not_release_still_active_conditions(void) {
  ProtectionSupervisor sup(makeConfig(/*lock_latching=*/true, /*low_voltage_latching=*/true));

  // 輪0を拘束したまま発火させる。
  stepAllWheels(sup, /*now=*/0, /*locked_wheel=*/0, /*lock_the_wheel=*/true);
  stepAllWheels(sup, /*now=*/101, /*locked_wheel=*/0, /*lock_the_wheel=*/true);

  // 低電圧を発火させる（電圧はまだ回復していない）。
  sup.updateLowVoltage(validSample(kBelowStopMilliVolts), /*now=*/0);
  sup.updateLowVoltage(validSample(kBelowStopMilliVolts), /*now=*/101);

  // ウォッチドッグは指令が直近に届いている体で非発火に保つ（このテストの
  // 対象外の理由がノイズとして混ざらないようにする）。
  sup.updateWatchdog(/*now=*/0, /*last_command_at_ms=*/0, /*has_command=*/true);
  sup.updateWatchdog(/*now=*/101, /*last_command_at_ms=*/101, /*has_command=*/true);

  GateOutcome before = sup.compose(true, true, true);
  TEST_ASSERT_EQUAL_UINT16(static_cast<BlockMask>(BlockReason::kMotorLock), before.wheel_reasons[0]);
  TEST_ASSERT_EQUAL_UINT16(static_cast<BlockMask>(BlockReason::kLowVoltage), before.global_reasons);

  // 直近の updateLock()/updateLowVoltage() が「まだ条件が継続している」
  // ことを反映した状態で resetProtections() を呼ぶ（輪0はまだ拘束中、
  // 電圧はまだ stop 未満のまま ―― どちらもこの直前の呼び出しで観測した
  // 値を使う）。
  sup.resetProtections(/*now=*/102);

  const GateOutcome after = sup.compose(true, true, true);
  // 条件が継続しているため、どちらも解除されていない。
  TEST_ASSERT_EQUAL_UINT16(static_cast<BlockMask>(BlockReason::kMotorLock), after.wheel_reasons[0]);
  TEST_ASSERT_EQUAL_UINT16(static_cast<BlockMask>(BlockReason::kLowVoltage), after.global_reasons);
}

// ---------------------------------------------------------------------------
// C: resetProtections() は、解除条件を満たした保護だけを解く（latching =
// true）。
// ---------------------------------------------------------------------------

void test_reset_protections_releases_conditions_that_have_cleared(void) {
  ProtectionSupervisor sup(makeConfig(/*lock_latching=*/true, /*low_voltage_latching=*/true));

  // 輪0を拘束して発火させる。
  stepAllWheels(sup, /*now=*/0, /*locked_wheel=*/0, /*lock_the_wheel=*/true);
  stepAllWheels(sup, /*now=*/101, /*locked_wheel=*/0, /*lock_the_wheel=*/true);

  // 低電圧を発火させる。
  sup.updateLowVoltage(validSample(kBelowStopMilliVolts), /*now=*/0);
  sup.updateLowVoltage(validSample(kBelowStopMilliVolts), /*now=*/101);

  // ウォッチドッグは指令が直近に届いている体で非発火に保つ（このテストの
  // 対象外の理由がノイズとして混ざらないようにする）。
  sup.updateWatchdog(/*now=*/0, /*last_command_at_ms=*/0, /*has_command=*/true);
  sup.updateWatchdog(/*now=*/101, /*last_command_at_ms=*/101, /*has_command=*/true);

  GateOutcome tripped = sup.compose(true, true, true);
  TEST_ASSERT_TRUE(tripped.wheel_reasons[0] != 0);
  TEST_ASSERT_TRUE(tripped.global_reasons != 0);

  // 条件を実際に解除する: 輪0の速度が回復した状態で updateLock() を進め、
  // 電圧も回復閾値まで戻す。MotorLockDetector 自身は latching=true の
  // Tripped 中この新しい入力を内部状態には反映しない（Idle へは戻らない）
  // が、ProtectionSupervisor はこの呼び出しで「条件がもう成立していない」
  // ことを独立に観測してキャッシュする。
  stepAllWheels(sup, /*now=*/150, /*locked_wheel=*/0, /*lock_the_wheel=*/false);
  sup.updateLowVoltage(validSample(kAtRecoverMilliVolts), /*now=*/150);
  sup.updateWatchdog(/*now=*/150, /*last_command_at_ms=*/150, /*has_command=*/true);

  // 条件が崩れた直後でも、まだ手動解除操作を呼んでいないので保持される。
  GateOutcome still_held = sup.compose(true, true, true);
  TEST_ASSERT_TRUE(still_held.wheel_reasons[0] != 0);
  TEST_ASSERT_TRUE(still_held.global_reasons != 0);

  // 解除操作。条件が崩れている（キャッシュ済み）ので、両方とも解除される。
  sup.resetProtections(/*now=*/151);

  const GateOutcome released = sup.compose(true, true, true);
  TEST_ASSERT_EQUAL_UINT16(0, released.wheel_reasons[0]);
  TEST_ASSERT_EQUAL_UINT16(0, released.global_reasons);
}

// ---------------------------------------------------------------------------
// D: 出力未許可（kOutputDisabled）と指令未着（kNoCommandYet）はそれぞれ
// 別の理由として立ち、いずれも applyGate() で遮断値を生む。
// ---------------------------------------------------------------------------

void test_output_disabled_and_no_command_yet_are_separate_reasons(void) {
  ProtectionSupervisor sup(makeConfig(/*lock_latching=*/true, /*low_voltage_latching=*/true));

  const BlockMask kOutputDisabledBit = static_cast<BlockMask>(BlockReason::kOutputDisabled);
  const BlockMask kNoCommandYetBit = static_cast<BlockMask>(BlockReason::kNoCommandYet);

  // ウォッチドッグは指令が直近に届いている体で非発火に保つ（このテストの
  // 対象外の理由がノイズとして混ざらないようにする）。低電圧・モータ
  // ロックは一度も update しないため既定の非発火状態のままである。
  sup.updateWatchdog(/*now=*/0, /*last_command_at_ms=*/0, /*has_command=*/true);

  // 出力未許可、指令はある。
  const GateOutcome output_disabled = sup.compose(/*configured=*/true, /*output_enabled=*/false, /*has_command=*/true);
  TEST_ASSERT_EQUAL_UINT16(kOutputDisabledBit, output_disabled.global_reasons);

  WheelOutputs out1 = makeNonZeroOutputs();
  ProtectionSupervisor::applyGate(output_disabled, out1);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, out1.duty[0]);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, out1.duty[1]);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, out1.duty[2]);

  // 出力は許可されているが、指令がまだ届いていない。
  const GateOutcome no_command = sup.compose(/*configured=*/true, /*output_enabled=*/true, /*has_command=*/false);
  TEST_ASSERT_EQUAL_UINT16(kNoCommandYetBit, no_command.global_reasons);

  WheelOutputs out2 = makeNonZeroOutputs();
  ProtectionSupervisor::applyGate(no_command, out2);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, out2.duty[0]);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, out2.duty[1]);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, out2.duty[2]);

  // 両方のビットが互いに異なる（別理由として区別できる）。
  TEST_ASSERT_NOT_EQUAL(kOutputDisabledBit, kNoCommandYetBit);
}

// ---------------------------------------------------------------------------
// E: applyGate(): 理由が無い輪はデューティを変更せず、理由がある輪だけを
// 0 にする。機体全体の理由は全輪を巻き込む。
// ---------------------------------------------------------------------------

void test_apply_gate_zeroes_only_wheels_with_a_reason(void) {
  GateOutcome outcome;  // 理由無し
  WheelOutputs outputs = makeNonZeroOutputs();
  ProtectionSupervisor::applyGate(outcome, outputs);
  TEST_ASSERT_EQUAL_FLOAT(0.5f, outputs.duty[0]);
  TEST_ASSERT_EQUAL_FLOAT(-0.3f, outputs.duty[1]);
  TEST_ASSERT_EQUAL_FLOAT(0.7f, outputs.duty[2]);

  GateOutcome wheel_only;
  wheel_only.wheel_reasons[1] = static_cast<BlockMask>(BlockReason::kMotorLock);
  WheelOutputs outputs2 = makeNonZeroOutputs();
  ProtectionSupervisor::applyGate(wheel_only, outputs2);
  TEST_ASSERT_EQUAL_FLOAT(0.5f, outputs2.duty[0]);   // 変更なし
  TEST_ASSERT_EQUAL_FLOAT(0.0f, outputs2.duty[1]);   // 遮断
  TEST_ASSERT_EQUAL_FLOAT(0.7f, outputs2.duty[2]);   // 変更なし

  GateOutcome global_only;
  global_only.global_reasons = static_cast<BlockMask>(BlockReason::kLowVoltage);
  WheelOutputs outputs3 = makeNonZeroOutputs();
  ProtectionSupervisor::applyGate(global_only, outputs3);
  // 機体全体の理由は全輪を遮断する。
  TEST_ASSERT_EQUAL_FLOAT(0.0f, outputs3.duty[0]);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, outputs3.duty[1]);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, outputs3.duty[2]);
}

// ---------------------------------------------------------------------------
// F: compose() は検出器を進めない。複数回呼んでも継続時間タイマーが進まず、
// 発火判定は updateLock()/updateLowVoltage()/updateWatchdog() の呼び出し
// 回数だけで決まる。
// ---------------------------------------------------------------------------

void test_compose_does_not_advance_detector_timers(void) {
  ProtectionSupervisor sup(makeConfig(/*lock_latching=*/true, /*low_voltage_latching=*/true));

  // t=0: 輪0を拘束開始（Suspect へ入る。まだ発火しない）。
  stepAllWheels(sup, /*now=*/0, /*locked_wheel=*/0, /*lock_the_wheel=*/true);

  // t=50: まだ duration_ms(100) 未満。
  stepAllWheels(sup, /*now=*/50, /*locked_wheel=*/0, /*lock_the_wheel=*/true);
  TEST_ASSERT_FALSE(sup.compose(true, true, true).wheel_reasons[0] != 0);

  // compose() を何度呼んでも、検出器の内部時刻・状態は進まない
  // （compose() は const であり update() を呼べない ―― コンパイル時に
  // 保証される）。
  for (int i = 0; i < 20; ++i) {
    sup.compose(true, true, true);
  }
  TEST_ASSERT_FALSE(sup.compose(true, true, true).wheel_reasons[0] != 0);

  // t=101: updateLock() をあらためて1回呼んで初めて duration_ms を超える。
  stepAllWheels(sup, /*now=*/101, /*locked_wheel=*/0, /*lock_the_wheel=*/true);
  TEST_ASSERT_TRUE(sup.compose(true, true, true).wheel_reasons[0] != 0);
}

// ---------------------------------------------------------------------------
// G: latching = false（自動復帰）構成は resetProtections() の対象外。
// 条件がまだ崩れきっていない間に呼んでも解除されない。
// ---------------------------------------------------------------------------

void test_reset_protections_skips_auto_recovering_configuration(void) {
  ProtectionSupervisor sup(makeConfig(/*lock_latching=*/false, /*low_voltage_latching=*/false));

  // 輪0を拘束して発火させる。
  stepAllWheels(sup, /*now=*/0, /*locked_wheel=*/0, /*lock_the_wheel=*/true);
  stepAllWheels(sup, /*now=*/101, /*locked_wheel=*/0, /*lock_the_wheel=*/true);
  TEST_ASSERT_TRUE(sup.compose(true, true, true).wheel_reasons[0] != 0);

  // 条件が崩れ始めた直後（clear_duration_ms(50) にまだ満たない）。
  stepAllWheels(sup, /*now=*/110, /*locked_wheel=*/0, /*lock_the_wheel=*/false);
  TEST_ASSERT_TRUE(sup.compose(true, true, true).wheel_reasons[0] != 0);

  // resetProtections() を呼んでも、latching=false 構成には触れないため
  // 状態は変化しない（検出器自身のタイマーがまだ完了していない）。
  sup.resetProtections(/*now=*/111);
  TEST_ASSERT_TRUE(sup.compose(true, true, true).wheel_reasons[0] != 0);
}

int main(int argc, char **argv) {
  (void)argc;
  (void)argv;
  UNITY_BEGIN();

  RUN_TEST(test_compose_separates_global_low_voltage_from_per_wheel_lock);
  RUN_TEST(test_reset_protections_does_not_release_still_active_conditions);
  RUN_TEST(test_reset_protections_releases_conditions_that_have_cleared);
  RUN_TEST(test_output_disabled_and_no_command_yet_are_separate_reasons);
  RUN_TEST(test_apply_gate_zeroes_only_wheels_with_a_reason);
  RUN_TEST(test_compose_does_not_advance_detector_timers);
  RUN_TEST(test_reset_protections_skips_auto_recovering_configuration);

  return UNITY_END();
}
