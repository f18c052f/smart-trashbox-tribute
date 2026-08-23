#include <unity.h>

#include <cstdint>
#include <limits>

#include "drivetrain_control/wrap_accumulator.hpp"

// drivetrain-core task 3.1: ハードウェアカウンタの折り返しを跨ぐ累積器
// （WrapAccumulator）を検証するホストテスト。
//
// design.md "WrapAccumulator"（L4）の厳密なシグネチャ・意味論を対象にする。
// 要件 4.4（累積量の表現幅を処理系に依らず同一に）、4.5（累積量が表現範囲
// を超え得る箇所の明示と検証）、7.1（折り返しの検出と桁上げ）、
// 7.2（実機を用いずホストでテストできる形）、7.3（折り返しを含む生カウント
// 列から連続した累積値を返す）、7.4（正転・逆転を跨ぐ折り返しで回転方向を
// 取り違えない）。
//
// 想定するハードウェアカウンタは符号付き16bit（法 65536。要件 7.1 と
// wrap_accumulator.hpp のコメント参照）。以下4系統を検証する:
//   1. 正転で折り返す列
//   2. 逆転で折り返す列
//   3. 正転と逆転を交互に跨ぐ列
//   4. 累積値の表現範囲上限近傍を初期値にした列

using drivetrain_control::WrapAccumulator;

namespace {
constexpr std::int32_t kModulus16 = 65536;  // 符号付き16bit カウンタの法
}  // namespace

void setUp(void) {}
void tearDown(void) {}

// ---------------------------------------------------------------------------
// 基本動作: 構築直後は 0、折り返しの無い小さな変化はそのまま積算される
// ---------------------------------------------------------------------------

void test_initial_value_is_zero(void) {
  WrapAccumulator acc(kModulus16, 1000);
  TEST_ASSERT_EQUAL_INT64(0, acc.value());
}

void test_no_change_yields_zero_delta(void) {
  WrapAccumulator acc(kModulus16, 1000);
  TEST_ASSERT_EQUAL_INT64(0, acc.update(1000));
  TEST_ASSERT_EQUAL_INT64(0, acc.value());
}

void test_small_forward_step_without_wrap(void) {
  WrapAccumulator acc(kModulus16, 1000);
  TEST_ASSERT_EQUAL_INT64(50, acc.update(1050));
  TEST_ASSERT_EQUAL_INT64(90, acc.update(1090));
}

void test_small_reverse_step_without_wrap(void) {
  WrapAccumulator acc(kModulus16, 1000);
  TEST_ASSERT_EQUAL_INT64(-50, acc.update(950));
  TEST_ASSERT_EQUAL_INT64(-90, acc.update(910));
}

// ---------------------------------------------------------------------------
// 系統1: 正転で折り返す列
//
// 16bit カウンタが +32767 側から折り返して -32768 側へ移ると、生値としては
// 大きく「減った」ように見えるが、実際の回転は正転（前進）である。
// 例: last_raw=32750 のとき、実際に+36カウント正転すると
//     32750 + 36 = 32786 は表現範囲(<=32767)を超えて折り返し、
//     32786 - 65536 = -32750 として観測される。
// ---------------------------------------------------------------------------

void test_forward_wrap_single_step(void) {
  WrapAccumulator acc(kModulus16, 32750);
  // 32750 -> -32750: 折り返しを跨いで +36 だけ正転した生値列。
  TEST_ASSERT_EQUAL_INT64(36, acc.update(-32750));
}

void test_forward_wrap_continues_after_crossing(void) {
  WrapAccumulator acc(kModulus16, 32750);
  TEST_ASSERT_EQUAL_INT64(36, acc.update(-32750));
  // 折り返した後も正転を続ける（-32750 -> -32700 は+50、折り返し無し）。
  TEST_ASSERT_EQUAL_INT64(86, acc.update(-32700));
  // さらに正転を続ける。
  TEST_ASSERT_EQUAL_INT64(186, acc.update(-32600));
}

// ---------------------------------------------------------------------------
// 系統2: 逆転で折り返す列
//
// -32768 側から +32767 側へ折り返す場合。例: last_raw=-32750 から
// 実際に-36カウント逆転すると -32750 - 36 = -32786 は表現範囲を下回って
// 折り返し、-32786 + 65536 = 32750 として観測される。
// ---------------------------------------------------------------------------

void test_reverse_wrap_single_step(void) {
  WrapAccumulator acc(kModulus16, -32750);
  // -32750 -> 32750: 折り返しを跨いで -36 だけ逆転した生値列。
  TEST_ASSERT_EQUAL_INT64(-36, acc.update(32750));
}

void test_reverse_wrap_continues_after_crossing(void) {
  WrapAccumulator acc(kModulus16, -32750);
  TEST_ASSERT_EQUAL_INT64(-36, acc.update(32750));
  // 折り返した後も逆転を続ける（32750 -> 32700 は-50、折り返し無し）。
  TEST_ASSERT_EQUAL_INT64(-86, acc.update(32700));
  TEST_ASSERT_EQUAL_INT64(-186, acc.update(32600));
}

// ---------------------------------------------------------------------------
// 系統3: 正転と逆転を交互に跨ぐ列
//
// 境界から離れた位置を起点に、正転で境界を越え、続けて逆転で同じ境界を
// 越えて元の位置へ戻る。往復後に累積値が正確に0へ戻ることは、
// 「回転方向を取り違えていれば往路・復路が加算方向でずれる」ことの
// 強い裏取りになる（要件 7.4）。
// ---------------------------------------------------------------------------

void test_alternating_forward_then_reverse_wrap_round_trips_to_zero(void) {
  WrapAccumulator acc(kModulus16, 32700);

  // 正転で境界を跨ぐ: 32700 -> -32700 は +136（32700+136=32836 が
  // 65536 で折り返り -32700 になる）。
  TEST_ASSERT_EQUAL_INT64(136, acc.update(-32700));
  // さらに正転を続ける（折り返し無し、+700）。
  TEST_ASSERT_EQUAL_INT64(836, acc.update(-32000));

  // ここで逆転へ転じ、同じ境界を逆向きに跨いで出発点近くまで戻る:
  // -32000 -> 32700 は -836（-32000-836=-32836 が -65536 側で
  // 折り返り 32700 になる）。往復の正転量(836)と逆転量(836)が一致し、
  // 累積値はちょうど0へ戻る。
  TEST_ASSERT_EQUAL_INT64(0, acc.update(32700));
}

void test_alternating_wrap_direction_is_not_confused_with_naive_magnitude(void) {
  // 上のシナリオと同じ生値列を、往路と復路で別々の WrapAccumulator に
  // 与え、それぞれ独立に符号が反転していることを確認する（正転量と
  // 逆転量の絶対値が同じでも、符号を取り違えて両方加算してしまう
  // バグ（例えば modulus/2 判定を絶対値だけで行う実装）を検出する）。
  WrapAccumulator forward_then_hold(kModulus16, 32700);
  TEST_ASSERT_EQUAL_INT64(136, forward_then_hold.update(-32700));

  WrapAccumulator reverse_then_hold(kModulus16, -32000);
  TEST_ASSERT_EQUAL_INT64(-836, reverse_then_hold.update(32700));

  TEST_ASSERT_TRUE(forward_then_hold.value() > 0);
  TEST_ASSERT_TRUE(reverse_then_hold.value() < 0);
}

// ---------------------------------------------------------------------------
// 系統4: 累積値の表現範囲上限近傍を初期値にした列
//
// int64_t の表現範囲上限（約9.2x10^18、本クラスの想定運用では約4000万年
// 分のカウント変化に相当）近傍から出発しても、実際に到達し得る小さな
// 増分では未定義動作を起こさず、単純な整数加算どおりに正確な値を返す
// ことを固定する（要件 4.5）。
// ---------------------------------------------------------------------------

void test_near_int64_max_initial_value_accumulates_exactly(void) {
  WrapAccumulator acc(kModulus16, 1000);
  const std::int64_t near_max = std::numeric_limits<std::int64_t>::max() - 50;
  acc.reset(1000, near_max);
  TEST_ASSERT_EQUAL_INT64(near_max, acc.value());

  // 折り返し無しの小さな正転を2回。オーバーフローへ到達しない範囲に
  // 留める。
  TEST_ASSERT_EQUAL_INT64(near_max + 10, acc.update(1010));
  TEST_ASSERT_EQUAL_INT64(near_max + 30, acc.update(1030));
}

void test_near_int64_max_initial_value_survives_a_wrap_crossing(void) {
  // 上限近傍を初期値にしたまま、折り返しを跨ぐ生値列を与えても
  // （系統1と同じ折り返しパターン）、桁上げの折り返し判定そのものは
  // int32_t の範囲で完結するため正しく動作し続けることを確認する。
  WrapAccumulator acc(kModulus16, 32750);
  const std::int64_t near_max = std::numeric_limits<std::int64_t>::max() - 100;
  acc.reset(32750, near_max);

  TEST_ASSERT_EQUAL_INT64(near_max + 36, acc.update(-32750));
  TEST_ASSERT_EQUAL_INT64(near_max + 86, acc.update(-32700));
}

// ---------------------------------------------------------------------------
// 境界値: delta が法の半分ちょうどのとき ((-modulus/2, +modulus/2] の
// 上限を含む側)
// ---------------------------------------------------------------------------

void test_delta_exactly_half_modulus_folds_to_positive(void) {
  WrapAccumulator acc(kModulus16, 0);
  // raw_delta = +32768 (== modulus/2) はそのまま正側として積算される
  // （(-modulus/2, +modulus/2] の定義。header のコメント参照）。
  TEST_ASSERT_EQUAL_INT64(32768, acc.update(32768));
}

// ---------------------------------------------------------------------------
// reset(): モジュラスを保ったまま生値・累積値だけを置き換える
// ---------------------------------------------------------------------------

void test_reset_replaces_raw_and_accumulated_without_changing_modulus(void) {
  WrapAccumulator acc(kModulus16, 1000);
  TEST_ASSERT_EQUAL_INT64(100, acc.update(1100));

  acc.reset(500, 42);
  TEST_ASSERT_EQUAL_INT64(42, acc.value());

  // reset 後の基準は raw=500 になっているはずなので、そこからの相対変化
  // が積算される。
  TEST_ASSERT_EQUAL_INT64(52, acc.update(510));
}

int main(int argc, char **argv) {
  (void)argc;
  (void)argv;
  UNITY_BEGIN();

  RUN_TEST(test_initial_value_is_zero);
  RUN_TEST(test_no_change_yields_zero_delta);
  RUN_TEST(test_small_forward_step_without_wrap);
  RUN_TEST(test_small_reverse_step_without_wrap);

  RUN_TEST(test_forward_wrap_single_step);
  RUN_TEST(test_forward_wrap_continues_after_crossing);

  RUN_TEST(test_reverse_wrap_single_step);
  RUN_TEST(test_reverse_wrap_continues_after_crossing);

  RUN_TEST(test_alternating_forward_then_reverse_wrap_round_trips_to_zero);
  RUN_TEST(test_alternating_wrap_direction_is_not_confused_with_naive_magnitude);

  RUN_TEST(test_near_int64_max_initial_value_accumulates_exactly);
  RUN_TEST(test_near_int64_max_initial_value_survives_a_wrap_crossing);

  RUN_TEST(test_delta_exactly_half_modulus_folds_to_positive);

  RUN_TEST(test_reset_replaces_raw_and_accumulated_without_changing_modulus);

  return UNITY_END();
}
