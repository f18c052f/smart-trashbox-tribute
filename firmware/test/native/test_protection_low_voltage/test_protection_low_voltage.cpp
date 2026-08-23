#include <unity.h>

#include "drivetrain_control/config.hpp"
#include "drivetrain_control/protection/low_voltage.hpp"
#include "drivetrain_control/types.hpp"

// drivetrain-core task 5.2: 保護② LiPo 低電圧保護（LowVoltageProtector）を
// 検証するホストテスト。
//
// design.md "LowVoltageProtector"（L7-L8 保護層）と "保護② LiPo 低電圧の
// 状態遷移" の厳密なシグネチャ・4状態機械を対象にする。要件 11.1〜11.5,
// 11.8（固定長移動平均、警告/停止/復帰の3閾値によるヒステリシス、欠測の
// 除外と別理由での停止、mV 整数演算）。
//
// 以下を検証する:
//   A. 移動平均が std::int32_t の整数演算で、0方向への切り捨て
//      （四捨五入ではない）で求まる。スライディング後の再計算も正しい
//   B. 欠測サンプル（valid == false）が移動平均へ取り込まれない
//   C. 警告は移動平均が warn を下回った時点で即座に立つ
//   D. 警告は移動平均が warn 以上へ戻った時点で即座に解除される
//   E. 段階的な電圧低下: 警告 -> （stop 未満が stop_duration_ms 継続）-> 停止
//   F. 一過性の降下（stop_duration_ms に満たない）では停止しない
//   G. 復帰は stop 閾値ではなく recover 閾値に達して初めて起きる
//      （ヒステリシス。自動復帰設定）
//   H. 手動リセット（latching = true）: 発火後は reset() まで維持される
//   I. 欠測が unavailable_duration_ms を超えて継続すると、低電圧とは別の
//      理由（Unavailable）で停止する
//   J. 一過性の欠測（unavailable_duration_ms に満たない）では
//      Unavailable にならず、欠測タイマーはリセットされる
//   K. Unavailable からの復帰は「有効な読み値の復帰」と「移動平均が
//      recover 閾値以上」の両方が必要（latching に関わらず無条件）
//   L. 起動直後（1件も有効なサンプルが無い）は voltage_valid() が偽で
//      averaged_milli_volts() が既定値のままであり、欠測だけでは
//      Normal から誤って警告/停止しない

using drivetrain_control::DurationMs;
using drivetrain_control::LowVoltageParams;
using drivetrain_control::LowVoltageProtector;
using drivetrain_control::LowVoltageState;
using drivetrain_control::TimeMs;
using drivetrain_control::VoltageSample;

namespace {

LowVoltageParams makeParams(std::int32_t warn, std::int32_t stop, std::int32_t recover,
                             std::uint8_t window, DurationMs stop_duration,
                             DurationMs unavailable_duration, bool latching) {
  LowVoltageParams p{};
  p.warn_milli_volts = warn;
  p.stop_milli_volts = stop;
  p.recover_milli_volts = recover;
  p.average_window = window;
  p.stop_duration_ms = stop_duration;
  p.unavailable_duration_ms = unavailable_duration;
  p.latching = latching;
  return p;
}

VoltageSample validSample(std::int32_t mv) {
  VoltageSample s;
  s.valid = true;
  s.milli_volts = mv;
  return s;
}

VoltageSample invalidSample() {
  VoltageSample s;
  s.valid = false;
  s.milli_volts = 0;
  return s;
}

}  // namespace

void setUp(void) {}
void tearDown(void) {}

// ---------------------------------------------------------------------------
// A: 移動平均は int32 の mV 整数演算で、0方向への切り捨て（四捨五入では
// ない）で求まる。ウィンドウが満杯になった後のスライディング再計算も正しい
// （要件 11.3）。warn/stop/recover はここでは無関係な値にして、平均値の
// 検証だけに集中する。
// ---------------------------------------------------------------------------

void test_moving_average_uses_integer_truncation_toward_zero(void) {
  LowVoltageProtector det(makeParams(/*warn=*/1, /*stop=*/1, /*recover=*/2, /*window=*/3,
                                      /*stop_duration=*/100, /*unavailable_duration=*/100,
                                      /*latching=*/true));

  TimeMs now = 0;
  det.update(validSample(10000), now);
  TEST_ASSERT_EQUAL_INT32(10000, det.averaged_milli_volts());

  now = 1;
  det.update(validSample(10000), now);
  TEST_ASSERT_EQUAL_INT32(10000, det.averaged_milli_volts());

  // (10000 + 10000 + 10002) / 3 = 10000.666... -> 10000 (切り捨て)。
  // 「最近接への丸め」なら 10001 になるはずの境界を突く。
  now = 2;
  det.update(validSample(10002), now);
  TEST_ASSERT_EQUAL_INT32(10000, det.averaged_milli_volts());
  TEST_ASSERT_TRUE(det.voltage_valid());

  // ウィンドウが満杯(3)になった後、最古の 10000 を追い出して 10006 を追加。
  // (10000 + 10002 + 10006) / 3 = 10002.666... -> 10002 (切り捨て)。
  now = 3;
  det.update(validSample(10006), now);
  TEST_ASSERT_EQUAL_INT32(10002, det.averaged_milli_volts());
}

// ---------------------------------------------------------------------------
// B: 欠測サンプル（valid == false）は移動平均へ取り込まれない（要件
// 11.8）。取り込まれていれば平均値がずれるはずの箇所を突く。
// ---------------------------------------------------------------------------

void test_invalid_samples_excluded_from_moving_average(void) {
  LowVoltageProtector det(makeParams(/*warn=*/1, /*stop=*/1, /*recover=*/2, /*window=*/2,
                                      /*stop_duration=*/100, /*unavailable_duration=*/100,
                                      /*latching=*/true));

  TimeMs now = 0;
  det.update(validSample(12000), now);
  TEST_ASSERT_EQUAL_INT32(12000, det.averaged_milli_volts());

  now = 1;
  det.update(invalidSample(), now);
  // 欠測は取り込まれない: 平均値は変化しない。
  TEST_ASSERT_EQUAL_INT32(12000, det.averaged_milli_volts());

  now = 2;
  det.update(validSample(11000), now);
  // (12000 + 11000) / 2 = 11500。
  TEST_ASSERT_EQUAL_INT32(11500, det.averaged_milli_volts());

  now = 3;
  det.update(invalidSample(), now);
  TEST_ASSERT_EQUAL_INT32(11500, det.averaged_milli_volts());

  now = 4;
  det.update(validSample(11002), now);
  // 欠測はリングバッファの位置を消費していないので、ここで追い出される
  // のは最も古い「有効な」サンプルである 12000。
  // (11000 + 11002) / 2 = 11001。
  TEST_ASSERT_EQUAL_INT32(11001, det.averaged_milli_volts());
}

// ---------------------------------------------------------------------------
// C: 警告は移動平均が warn を下回った時点で即座に立つ（要件 11.1）。
// ---------------------------------------------------------------------------

void test_warning_fires_immediately_when_average_drops_below_warn(void) {
  LowVoltageProtector det(makeParams(/*warn=*/11000, /*stop=*/10000, /*recover=*/10500,
                                      /*window=*/1, /*stop_duration=*/100,
                                      /*unavailable_duration=*/100, /*latching=*/true));

  TimeMs now = 0;
  TEST_ASSERT_TRUE(LowVoltageState::kNormal == det.update(validSample(12000), now));

  now = 1;
  TEST_ASSERT_TRUE(LowVoltageState::kWarning == det.update(validSample(10900), now));
  TEST_ASSERT_TRUE(LowVoltageState::kWarning == det.state());
}

// ---------------------------------------------------------------------------
// D: 警告は移動平均が warn 以上へ戻った時点で即座に解除される（design.md
// "Warning --> Normal: 移動平均が警告閾値以上へ復帰"）。
// ---------------------------------------------------------------------------

void test_warning_clears_immediately_when_average_returns_to_warn_threshold(void) {
  LowVoltageProtector det(makeParams(/*warn=*/11000, /*stop=*/10000, /*recover=*/10500,
                                      /*window=*/1, /*stop_duration=*/100,
                                      /*unavailable_duration=*/100, /*latching=*/true));

  TimeMs now = 0;
  TEST_ASSERT_TRUE(LowVoltageState::kWarning == det.update(validSample(10900), now));

  now = 1;
  TEST_ASSERT_TRUE(LowVoltageState::kNormal == det.update(validSample(11500), now));
  TEST_ASSERT_TRUE(LowVoltageState::kNormal == det.state());
}

// ---------------------------------------------------------------------------
// E: 段階的な電圧低下: 警告 -> （stop 未満の継続が stop_duration_ms(100)
// を超える）-> 停止（要件 11.2, 11.5）。
// ---------------------------------------------------------------------------

void test_gradual_decline_transitions_warning_to_tripped_after_stop_duration(void) {
  LowVoltageProtector det(makeParams(/*warn=*/11000, /*stop=*/10000, /*recover=*/10500,
                                      /*window=*/1, /*stop_duration=*/100,
                                      /*unavailable_duration=*/100, /*latching=*/true));

  TimeMs now = 0;
  TEST_ASSERT_TRUE(LowVoltageState::kWarning == det.update(validSample(10900), now));

  // t=1: stop(10000) を下回る -> 継続時間の計測を開始（この tick はまだ発火しない）。
  now = 1;
  TEST_ASSERT_TRUE(LowVoltageState::kWarning == det.update(validSample(9500), now));

  // t=50: 経過49ms、まだ100msを超えていない。
  now = 50;
  TEST_ASSERT_TRUE(LowVoltageState::kWarning == det.update(validSample(9500), now));
  TEST_ASSERT_FALSE(LowVoltageState::kTripped == det.state());

  // t=102: 起点(1)から101ms、厳密に100msを超えた -> 発火する。
  now = 102;
  TEST_ASSERT_TRUE(LowVoltageState::kTripped == det.update(validSample(9500), now));
  TEST_ASSERT_TRUE(LowVoltageState::kTripped == det.state());
}

// ---------------------------------------------------------------------------
// F: 一過性の降下（stop_duration_ms(100) に満たない）では停止しない
// （要件 11.5）。条件が崩れると継続時間の計測がリセットされ、その後
// 長時間経っても停止しないことを確認する。
// ---------------------------------------------------------------------------

void test_transient_dip_below_stop_does_not_trip(void) {
  LowVoltageProtector det(makeParams(/*warn=*/11000, /*stop=*/10000, /*recover=*/10500,
                                      /*window=*/1, /*stop_duration=*/100,
                                      /*unavailable_duration=*/100, /*latching=*/true));

  TimeMs now = 0;
  det.update(validSample(10900), now);  // Warning

  now = 1;
  det.update(validSample(9500), now);  // stop 未満、継続時間の起点

  now = 50;
  det.update(validSample(9500), now);  // まだ 49ms、発火しない

  // t=80: 電圧が回復（急加速の一時的降下を模す）。停止条件が崩れる。
  now = 80;
  TEST_ASSERT_TRUE(LowVoltageState::kNormal == det.update(validSample(11500), now));

  // t=500: 十分に時間が経過しても、既に条件が崩れているので停止しない。
  now = 500;
  TEST_ASSERT_TRUE(LowVoltageState::kNormal == det.update(validSample(11500), now));
  TEST_ASSERT_FALSE(LowVoltageState::kTripped == det.state());
}

// ---------------------------------------------------------------------------
// G: 復帰は stop 閾値ではなく recover 閾値に達して初めて起きる
// （ヒステリシス。要件 11.4）。自動復帰設定（latching = false）。
// ---------------------------------------------------------------------------

void test_recovery_requires_recover_threshold_not_merely_stop_threshold(void) {
  LowVoltageProtector det(makeParams(/*warn=*/11000, /*stop=*/10000, /*recover=*/10500,
                                      /*window=*/1, /*stop_duration=*/100,
                                      /*unavailable_duration=*/100, /*latching=*/false));

  TimeMs now = 0;
  det.update(validSample(9000), now);  // 起動直後から stop 未満。継続時間の起点。

  now = 150;
  TEST_ASSERT_TRUE(LowVoltageState::kTripped == det.update(validSample(9000), now));

  // stop(10000) 以上へは戻ったが、recover(10500) にはまだ届いていない
  // -> 復帰しない。
  now = 151;
  TEST_ASSERT_TRUE(LowVoltageState::kTripped == det.update(validSample(10200), now));
  TEST_ASSERT_TRUE(LowVoltageState::kTripped == det.state());

  // recover(10500) に到達して初めて復帰する。
  now = 152;
  TEST_ASSERT_TRUE(LowVoltageState::kNormal == det.update(validSample(10500), now));
}

// ---------------------------------------------------------------------------
// H: 手動リセット（latching = true）: 発火後は reset() まで維持される。
// 電圧が正常域へ戻っても、reset() を呼ぶまでは Tripped のまま。
// ---------------------------------------------------------------------------

void test_tripped_latching_requires_manual_reset(void) {
  LowVoltageProtector det(makeParams(/*warn=*/11000, /*stop=*/10000, /*recover=*/10500,
                                      /*window=*/1, /*stop_duration=*/100,
                                      /*unavailable_duration=*/100, /*latching=*/true));

  TimeMs now = 0;
  det.update(validSample(9000), now);
  now = 150;
  TEST_ASSERT_TRUE(LowVoltageState::kTripped == det.update(validSample(9000), now));

  // 電圧が十分に回復しても、latching = true の間は自動的には解除されない。
  now = 1000;
  TEST_ASSERT_TRUE(LowVoltageState::kTripped == det.update(validSample(12000), now));
  TEST_ASSERT_TRUE(LowVoltageState::kTripped == det.state());

  now = 1001;
  det.reset(now);
  TEST_ASSERT_TRUE(LowVoltageState::kNormal == det.state());

  // reset() 後は通常どおり判定できる。
  now = 1002;
  TEST_ASSERT_TRUE(LowVoltageState::kNormal == det.update(validSample(12000), now));
}

// ---------------------------------------------------------------------------
// I: 欠測が unavailable_duration_ms(80) を超えて継続すると、低電圧とは
// 別の理由（Unavailable）で停止する（要件 11.8）。直前の平均値が正常域
// でも、それとは無関係に Unavailable へ遷移することを確認する。
// ---------------------------------------------------------------------------

void test_continuous_missing_readings_trip_unavailable_as_distinct_reason(void) {
  LowVoltageProtector det(makeParams(/*warn=*/11000, /*stop=*/10000, /*recover=*/10500,
                                      /*window=*/1, /*stop_duration=*/100,
                                      /*unavailable_duration=*/80, /*latching=*/true));

  TimeMs now = 0;
  // 正常域の電圧を確立しておく（低電圧が原因ではないことを裏取りする）。
  TEST_ASSERT_TRUE(LowVoltageState::kNormal == det.update(validSample(12000), now));

  now = 1;
  TEST_ASSERT_TRUE(LowVoltageState::kNormal == det.update(invalidSample(), now));

  now = 50;
  TEST_ASSERT_TRUE(LowVoltageState::kNormal == det.update(invalidSample(), now));

  // t=90: 起点(1)から89ms、80msを厳密に超えた -> Unavailable。
  // Tripped ではないことを明示的に確認する（低電圧とは別の理由）。
  now = 90;
  LowVoltageState result = det.update(invalidSample(), now);
  TEST_ASSERT_TRUE(LowVoltageState::kUnavailable == result);
  TEST_ASSERT_FALSE(LowVoltageState::kTripped == result);
}

// ---------------------------------------------------------------------------
// J: 一過性の欠測（unavailable_duration_ms(80) に満たない）では
// Unavailable にならず、有効なサンプルが戻ると欠測タイマーがリセット
// される（合計すると 80ms を超えていても、連続でなければ発火しない）。
// ---------------------------------------------------------------------------

void test_transient_missing_reading_resets_unavailable_timer(void) {
  LowVoltageProtector det(makeParams(/*warn=*/11000, /*stop=*/10000, /*recover=*/10500,
                                      /*window=*/1, /*stop_duration=*/100,
                                      /*unavailable_duration=*/80, /*latching=*/true));

  TimeMs now = 0;
  det.update(validSample(12000), now);

  now = 1;
  det.update(invalidSample(), now);  // 欠測タイマーの起点(1)

  now = 40;
  det.update(invalidSample(), now);  // 経過39ms、まだ発火しない

  // t=41: 有効なサンプルが戻る -> 欠測タイマーはリセットされる。
  now = 41;
  TEST_ASSERT_TRUE(LowVoltageState::kNormal == det.update(validSample(12000), now));

  // 新しい欠測タイマーの起点(42)。
  now = 42;
  det.update(invalidSample(), now);

  // t=100: 新起点(42)から58ms -> まだ80msを超えていない -> 発火しない。
  // もしタイマーがリセットされていなければ、初回欠測(t=1)から99ms
  // 経過しており誤って発火してしまう箇所。
  now = 100;
  TEST_ASSERT_TRUE(LowVoltageState::kNormal == det.update(invalidSample(), now));
  TEST_ASSERT_FALSE(LowVoltageState::kUnavailable == det.state());
}

// ---------------------------------------------------------------------------
// K: Unavailable からの復帰は「有効な読み値の復帰」と「移動平均が
// recover 閾値以上」の両方が必要。latching = true でも reset() 無しで
// 復帰できる（design.md の Unavailable --> Normal 矢印に latching の
// 条件が付いていないため。Tripped の復帰条件との違いを裏取りする）。
// ---------------------------------------------------------------------------

void test_unavailable_recovers_only_when_valid_resumes_and_average_reaches_recover(void) {
  LowVoltageProtector det(makeParams(/*warn=*/11000, /*stop=*/10000, /*recover=*/10500,
                                      /*window=*/1, /*stop_duration=*/100,
                                      /*unavailable_duration=*/80, /*latching=*/true));

  TimeMs now = 0;
  det.update(validSample(12000), now);

  now = 1;
  det.update(invalidSample(), now);

  now = 90;
  TEST_ASSERT_TRUE(LowVoltageState::kUnavailable == det.update(invalidSample(), now));

  // 有効なサンプルは戻ったが、平均が recover(10500) に届いていない
  // -> まだ Unavailable のまま。
  now = 91;
  TEST_ASSERT_TRUE(LowVoltageState::kUnavailable == det.update(validSample(10200), now));

  // recover(10500) に到達して初めて復帰する。latching = true だが
  // reset() を呼んでいないことに注意（Unavailable の復帰は無条件）。
  now = 92;
  TEST_ASSERT_TRUE(LowVoltageState::kNormal == det.update(validSample(10500), now));
}

// ---------------------------------------------------------------------------
// L: 起動直後（1件も有効なサンプルが無い）は voltage_valid() が偽で
// averaged_milli_volts() が既定値のまま。欠測だけを与えても、既定値
// 0mV を「危険な低電圧」と誤判定して警告/停止しない。
// ---------------------------------------------------------------------------

void test_no_valid_sample_yet_does_not_misjudge_default_average_as_low_voltage(void) {
  LowVoltageProtector det(makeParams(/*warn=*/11000, /*stop=*/10000, /*recover=*/10500,
                                      /*window=*/2, /*stop_duration=*/100,
                                      /*unavailable_duration=*/100, /*latching=*/true));

  TEST_ASSERT_FALSE(det.voltage_valid());
  TEST_ASSERT_EQUAL_INT32(0, det.averaged_milli_volts());
  TEST_ASSERT_TRUE(LowVoltageState::kNormal == det.state());

  TimeMs now = 0;
  // 欠測だけを与える: 既定値 0mV は warn/stop を大きく下回るが、
  // count_ == 0 の間は平均を判定に使わないので誤って警告/停止しない。
  TEST_ASSERT_TRUE(LowVoltageState::kNormal == det.update(invalidSample(), now));
  TEST_ASSERT_FALSE(det.voltage_valid());

  // warn(11000) 以上の値を与えて、この検証が状態の warn/stop 判定と
  // 混ざらないようにする（このテストの主眼は voltage_valid() /
  // averaged_milli_volts() の初期化挙動である）。
  now = 1;
  TEST_ASSERT_TRUE(LowVoltageState::kNormal == det.update(validSample(12000), now));
  TEST_ASSERT_TRUE(det.voltage_valid());
  TEST_ASSERT_EQUAL_INT32(12000, det.averaged_milli_volts());
}

int main(int argc, char **argv) {
  (void)argc;
  (void)argv;
  UNITY_BEGIN();

  RUN_TEST(test_moving_average_uses_integer_truncation_toward_zero);
  RUN_TEST(test_invalid_samples_excluded_from_moving_average);
  RUN_TEST(test_warning_fires_immediately_when_average_drops_below_warn);
  RUN_TEST(test_warning_clears_immediately_when_average_returns_to_warn_threshold);
  RUN_TEST(test_gradual_decline_transitions_warning_to_tripped_after_stop_duration);
  RUN_TEST(test_transient_dip_below_stop_does_not_trip);
  RUN_TEST(test_recovery_requires_recover_threshold_not_merely_stop_threshold);
  RUN_TEST(test_tripped_latching_requires_manual_reset);
  RUN_TEST(test_continuous_missing_readings_trip_unavailable_as_distinct_reason);
  RUN_TEST(test_transient_missing_reading_resets_unavailable_timer);
  RUN_TEST(test_unavailable_recovers_only_when_valid_resumes_and_average_reaches_recover);
  RUN_TEST(test_no_valid_sample_yet_does_not_misjudge_default_average_as_low_voltage);

  return UNITY_END();
}
