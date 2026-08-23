#include <unity.h>

#include <cstdint>

#include "drivetrain_control/config.hpp"
#include "drivetrain_control/voltage_scaler.hpp"

// drivetrain-core task 3.2: 生の電圧読み値からバッテリ電圧への換算
// （VoltageScaler）を検証するホストテスト。
//
// design.md "VoltageScaler"（L4）の厳密なシグネチャ・意味論を対象にする。
// 要件 11.7（生値 → 電圧の換算を、校正作業の出力形そのままのパラメータを
// 伴う純ロジックとして提供する）、15.6（分圧比・補正係数を実装内に固定値
// として持たない）。
//
// 以下4系統を検証する:
//   1. テーブル点の上で厳密に一致すること
//   2. 点間が線形であること（区分ごとの傾きが個別に適用される）
//   3. 2点構成が分圧比のみの線形換算と一致すること
//      （テスト側で独立に計算した「raw * mv1 / raw1」の式と比較する。
//       VoltageScaler の内部実装を再利用しない）
//   4. テーブル範囲外が両端の区間の傾きで外挿されること（クランプでない）

using drivetrain_control::VoltageScaler;
using drivetrain_control::VoltageScalerParams;

void setUp(void) {}
void tearDown(void) {}

namespace {

// 3点テーブル。区間ごとに傾きを変えて「区分」線形であることを検証する。
//   区間0: raw [0, 2000], mv [0, 6000]      傾き = 3 mV/count
//   区間1: raw [2000, 4000], mv [6000, 10000] 傾き = 2 mV/count
VoltageScalerParams makeThreePointTable() {
  VoltageScalerParams params{};
  params.table[0] = {0, 0};
  params.table[1] = {2000, 6000};
  params.table[2] = {4000, 10000};
  params.point_count = 3;
  return params;
}

}  // namespace

// ---------------------------------------------------------------------------
// 系統1: テーブル点の上で厳密に一致する
// ---------------------------------------------------------------------------

void test_matches_exactly_at_every_table_point(void) {
  const VoltageScalerParams params = makeThreePointTable();
  const VoltageScaler scaler(params);

  TEST_ASSERT_EQUAL_INT32(0, scaler.toMilliVolts(0));
  TEST_ASSERT_EQUAL_INT32(6000, scaler.toMilliVolts(2000));
  TEST_ASSERT_EQUAL_INT32(10000, scaler.toMilliVolts(4000));
}

// ---------------------------------------------------------------------------
// 系統2: 点間が線形である（区間ごとに異なる傾きが正しく適用される）
// ---------------------------------------------------------------------------

void test_interpolates_linearly_within_first_segment(void) {
  const VoltageScalerParams params = makeThreePointTable();
  const VoltageScaler scaler(params);

  // 区間0 の傾きは 3 mV/count。
  TEST_ASSERT_EQUAL_INT32(1500, scaler.toMilliVolts(500));
  TEST_ASSERT_EQUAL_INT32(3000, scaler.toMilliVolts(1000));
  TEST_ASSERT_EQUAL_INT32(5700, scaler.toMilliVolts(1900));
}

void test_interpolates_linearly_within_second_segment(void) {
  const VoltageScalerParams params = makeThreePointTable();
  const VoltageScaler scaler(params);

  // 区間1 の傾きは 2 mV/count（区間0 とは異なる）。
  TEST_ASSERT_EQUAL_INT32(8000, scaler.toMilliVolts(3000));
  TEST_ASSERT_EQUAL_INT32(9000, scaler.toMilliVolts(3500));
  TEST_ASSERT_EQUAL_INT32(9800, scaler.toMilliVolts(3900));
}

// ---------------------------------------------------------------------------
// 系統3: 2点構成が分圧比のみの線形換算に一致する
//
// 生値の最大値 4095（12bit ADC）と満充電相当の 12600 mV を校正2点として
// 与える。基準点が (0, 0) のため、分圧比のみの線形換算は
// mv = raw * 12600 / 4095 という単純な比例式に一致するはずである。
// この式はテスト側で独立に（VoltageScaler を経由せず）計算し、
// VoltageScaler::toMilliVolts の結果と突き合わせる。
// raw に 4095 の約数を選び、割り切れる（丸めが発生しない）値で厳密比較する。
// ---------------------------------------------------------------------------

namespace {

VoltageScalerParams makeTwoPointDividerTable() {
  VoltageScalerParams params{};
  params.table[0] = {0, 0};
  params.table[1] = {4095, 12600};
  params.point_count = 2;
  return params;
}

// 分圧比のみの線形換算を、VoltageScaler を使わずに独立実装したもの。
std::int32_t linearDividerRatio(std::int32_t raw) {
  const std::int64_t numerator = static_cast<std::int64_t>(raw) * 12600;
  return static_cast<std::int32_t>(numerator / 4095);
}

}  // namespace

void test_two_point_table_matches_divider_ratio_conversion(void) {
  const VoltageScalerParams params = makeTwoPointDividerTable();
  const VoltageScaler scaler(params);

  // 4095 の約数（819 = 4095/5, 1365 = 4095/3, 585 = 4095/7）を選び、
  // raw * 12600 / 4095 が割り切れる（丸めの入り込まない）値で比較する。
  TEST_ASSERT_EQUAL_INT32(linearDividerRatio(819), scaler.toMilliVolts(819));
  TEST_ASSERT_EQUAL_INT32(2520, scaler.toMilliVolts(819));

  TEST_ASSERT_EQUAL_INT32(linearDividerRatio(1365), scaler.toMilliVolts(1365));
  TEST_ASSERT_EQUAL_INT32(4200, scaler.toMilliVolts(1365));

  TEST_ASSERT_EQUAL_INT32(linearDividerRatio(585), scaler.toMilliVolts(585));
  TEST_ASSERT_EQUAL_INT32(1800, scaler.toMilliVolts(585));

  // テーブル点そのものでも一致する。
  TEST_ASSERT_EQUAL_INT32(0, scaler.toMilliVolts(0));
  TEST_ASSERT_EQUAL_INT32(12600, scaler.toMilliVolts(4095));
}

// ---------------------------------------------------------------------------
// 系統4: テーブル範囲外は両端の区間の傾きで外挿される（クランプではない）
// ---------------------------------------------------------------------------

void test_extrapolates_below_range_using_first_segment_slope(void) {
  const VoltageScalerParams params = makeThreePointTable();
  const VoltageScaler scaler(params);

  // 区間0 の傾き（3 mV/count）をそのまま raw < 0 側へ延長する。
  // クランプ（下端値 0 に張り付く）であれば 0 になってしまうが、
  // 外挿では負の値になる。
  TEST_ASSERT_EQUAL_INT32(-1500, scaler.toMilliVolts(-500));
  TEST_ASSERT_EQUAL_INT32(-3000, scaler.toMilliVolts(-1000));
}

void test_extrapolates_above_range_using_last_segment_slope(void) {
  const VoltageScalerParams params = makeThreePointTable();
  const VoltageScaler scaler(params);

  // 区間1 の傾き（2 mV/count）をそのまま raw > 4000 側へ延長する。
  // クランプであれば末尾値 10000 に張り付くはずだが、外挿では超える。
  TEST_ASSERT_EQUAL_INT32(12000, scaler.toMilliVolts(5000));
  TEST_ASSERT_EQUAL_INT32(14000, scaler.toMilliVolts(6000));
}

void test_extrapolates_two_point_table_beyond_range(void) {
  const VoltageScalerParams params = makeTwoPointDividerTable();
  const VoltageScaler scaler(params);

  // 2点構成でも外挿は同じ唯一の区間の傾きを使う。
  TEST_ASSERT_EQUAL_INT32(linearDividerRatio(-4095), scaler.toMilliVolts(-4095));
  TEST_ASSERT_EQUAL_INT32(-12600, scaler.toMilliVolts(-4095));

  TEST_ASSERT_EQUAL_INT32(linearDividerRatio(8190), scaler.toMilliVolts(8190));
  TEST_ASSERT_EQUAL_INT32(25200, scaler.toMilliVolts(8190));
}

int main(int argc, char** argv) {
  (void)argc;
  (void)argv;
  UNITY_BEGIN();

  RUN_TEST(test_matches_exactly_at_every_table_point);

  RUN_TEST(test_interpolates_linearly_within_first_segment);
  RUN_TEST(test_interpolates_linearly_within_second_segment);

  RUN_TEST(test_two_point_table_matches_divider_ratio_conversion);

  RUN_TEST(test_extrapolates_below_range_using_first_segment_slope);
  RUN_TEST(test_extrapolates_above_range_using_last_segment_slope);
  RUN_TEST(test_extrapolates_two_point_table_beyond_range);

  return UNITY_END();
}
