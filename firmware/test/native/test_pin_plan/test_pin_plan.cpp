#include <unity.h>

#include <cstdint>
#include <type_traits>

#include "board_pins/pin_map.hpp"
#include "board_pins/pin_rules.hpp"

// teleop-bringup タスク 1.3: 端子割当を保持する独立した部品（board_pins）の
// ホストテスト。
//
// 本ファイルは design.md "File Structure Plan" が定めた
// `test/native/test_pin_plan/` に置かれ、要件 2 の全体（2.1〜2.7）を段階的に
// 覆う。タスク 1.3 の時点では次の2点だけを対象にする。
//
//   A. 端子割当を表す型（PinRole / PinAssignment / kUnassigned / kNoWheel）が
//      定義され、未割当を表せること（要件 2.1）
//   B. ESP32 classic の端子特性表（入力専用・ストラッピング・内蔵フラッシュ用・
//      ADC1 の系統）と、ペリフェラルの本数の定数が、ホストビルドから値として
//      参照できること
//
// ⚠️ B が本タスクの「観測可能な完了状態」そのものである。`firmware/src/` は
// ネイティブビルドに含まれない（`test_build_src` 既定 `no`）ため、端子割当を
// アプリ層へ置くとホストから参照できず、要件 2.7（成立検査を実機へ書き込む
// ことなく実行できること）が成り立たなくなる。このテストが緑であることが、
// board_pins が `firmware/lib/` 側にあり、ホストビルドから実際に見えている
// ことの証拠になる。
//
// 成立検査そのもの（checkPinPlan）と出荷する割当の具体値はタスク 1.4 / 1.5 が
// 追加する。本ファイルはその時点で拡張される。

using board_pins::PinAssignment;
using board_pins::PinRole;
using board_pins::PinViolation;
using board_pins::checkPinPlan;
using board_pins::kAdc1ChannelCount;
using board_pins::kAdc1GpioCount;
using board_pins::kAdc1Gpios;
using board_pins::kFlashGpioCount;
using board_pins::kFlashGpios;
using board_pins::kInputOnlyGpioCount;
using board_pins::kInputOnlyGpios;
using board_pins::kLedcChannelCount;
using board_pins::kNoWheel;
using board_pins::kPcntUnitCount;
using board_pins::kStrappingGpioCount;
using board_pins::kStrappingGpios;
using board_pins::kUnassigned;

namespace {
constexpr bool hasViolation(std::uint16_t mask, PinViolation v) {
  return (mask & static_cast<std::uint16_t>(v)) != 0;
}
}  // namespace

void setUp(void) {}
void tearDown(void) {}

namespace {

bool table_contains(const std::int8_t *table, std::uint8_t count, std::int8_t gpio) {
  for (std::uint8_t i = 0; i < count; ++i) {
    if (table[i] == gpio) {
      return true;
    }
  }
  return false;
}

bool table_is_strictly_ascending(const std::int8_t *table, std::uint8_t count) {
  for (std::uint8_t i = 1; i < count; ++i) {
    if (!(table[i - 1] < table[i])) {
      return false;
    }
  }
  return true;
}

}  // namespace

// ---------------------------------------------------------------------------
// A. 端子割当を表す型（要件 2.1）
// ---------------------------------------------------------------------------

// 幅が処理系によって変化しない型を用いる（ホストと ESP32 で `int` の幅が
// 一致することを前提に置かない。drivetrain_control/types.hpp と同じ流儀）。
static_assert(std::is_same<std::underlying_type<PinRole>::type, std::uint8_t>::value,
              "PinRole の基底型は std::uint8_t である");
static_assert(std::is_same<decltype(PinAssignment::wheel_index), std::uint8_t>::value,
              "wheel_index は std::uint8_t である");
static_assert(std::is_same<decltype(PinAssignment::gpio), std::int8_t>::value,
              "gpio は std::int8_t である（未割当を負値で表すため符号付き）");

void test_pin_role_covers_every_use_of_a_pin(void) {
  // design.md board_pins の Service Interface が列挙する6用途。
  // エンコーダA相/B相・モータPWM/方向・バッテリ電圧監視・机上確認用可変抵抗。
  TEST_ASSERT_EQUAL_UINT8(0, static_cast<std::uint8_t>(PinRole::kEncoderA));
  TEST_ASSERT_EQUAL_UINT8(1, static_cast<std::uint8_t>(PinRole::kEncoderB));
  TEST_ASSERT_EQUAL_UINT8(2, static_cast<std::uint8_t>(PinRole::kMotorPwm));
  TEST_ASSERT_EQUAL_UINT8(3, static_cast<std::uint8_t>(PinRole::kMotorDir));
  TEST_ASSERT_EQUAL_UINT8(4, static_cast<std::uint8_t>(PinRole::kBatterySense));
  TEST_ASSERT_EQUAL_UINT8(5, static_cast<std::uint8_t>(PinRole::kBenchPot));
}

void test_pin_assignment_defaults_to_unassigned_and_no_wheel(void) {
  const PinAssignment assignment{};
  TEST_ASSERT_EQUAL_INT8(kUnassigned, assignment.gpio);
  TEST_ASSERT_EQUAL_UINT8(kNoWheel, assignment.wheel_index);
}

void test_unassigned_is_distinguishable_from_every_real_gpio(void) {
  // 端子番号は 0 以上であるから、負値であれば実在の端子と衝突しない。
  TEST_ASSERT_TRUE(kUnassigned < 0);
}

void test_pin_assignment_holds_role_wheel_and_gpio(void) {
  const PinAssignment assignment{PinRole::kEncoderB, 2, 27};
  TEST_ASSERT_EQUAL_UINT8(static_cast<std::uint8_t>(PinRole::kEncoderB),
                          static_cast<std::uint8_t>(assignment.role));
  TEST_ASSERT_EQUAL_UINT8(2, assignment.wheel_index);
  TEST_ASSERT_EQUAL_INT8(27, assignment.gpio);
}

void test_no_wheel_marks_an_assignment_that_belongs_to_no_wheel(void) {
  // バッテリ電圧監視・机上確認用の可変抵抗は輪に紐づかない。
  const PinAssignment assignment{PinRole::kBatterySense, kNoWheel, 35};
  TEST_ASSERT_EQUAL_UINT8(kNoWheel, assignment.wheel_index);
}

// ---------------------------------------------------------------------------
// B. ESP32 classic の端子特性表（要件 2.3, 2.4, 2.5 の判定条件の原資）
// ---------------------------------------------------------------------------

void test_input_only_gpio_table_matches_the_datasheet(void) {
  // 入力専用かつ内部プルアップ・プルダウンを持たない端子（research.md）。
  TEST_ASSERT_EQUAL_UINT8(4, kInputOnlyGpioCount);
  TEST_ASSERT_EQUAL_INT8(34, kInputOnlyGpios[0]);
  TEST_ASSERT_EQUAL_INT8(35, kInputOnlyGpios[1]);
  TEST_ASSERT_EQUAL_INT8(36, kInputOnlyGpios[2]);
  TEST_ASSERT_EQUAL_INT8(39, kInputOnlyGpios[3]);
}

void test_strapping_gpio_table_matches_the_datasheet(void) {
  // 起動時の動作モードを決める端子（research.md）。
  TEST_ASSERT_EQUAL_UINT8(5, kStrappingGpioCount);
  TEST_ASSERT_EQUAL_INT8(0, kStrappingGpios[0]);
  TEST_ASSERT_EQUAL_INT8(2, kStrappingGpios[1]);
  TEST_ASSERT_EQUAL_INT8(5, kStrappingGpios[2]);
  TEST_ASSERT_EQUAL_INT8(12, kStrappingGpios[3]);
  TEST_ASSERT_EQUAL_INT8(15, kStrappingGpios[4]);
}

void test_flash_gpio_table_covers_the_contiguous_range(void) {
  // 内蔵フラッシュとの通信に用いる端子 6〜11（research.md）。
  TEST_ASSERT_EQUAL_UINT8(6, kFlashGpioCount);
  for (std::uint8_t i = 0; i < kFlashGpioCount; ++i) {
    TEST_ASSERT_EQUAL_INT8(static_cast<std::int8_t>(6 + i), kFlashGpios[i]);
  }
}

void test_adc1_gpio_table_covers_the_contiguous_range(void) {
  // 無線動作中も使用できる変換器の系統 ADC1 は GPIO 32〜39（research.md）。
  TEST_ASSERT_EQUAL_UINT8(8, kAdc1GpioCount);
  for (std::uint8_t i = 0; i < kAdc1GpioCount; ++i) {
    TEST_ASSERT_EQUAL_INT8(static_cast<std::int8_t>(32 + i), kAdc1Gpios[i]);
  }
}

void test_tables_are_strictly_ascending(void) {
  // 表を昇順に保つのは、タスク 1.4 の照合を素直に書けるようにするためであり、
  // 同時に表そのものに重複が無いことも保証される。
  TEST_ASSERT_TRUE(table_is_strictly_ascending(kInputOnlyGpios, kInputOnlyGpioCount));
  TEST_ASSERT_TRUE(table_is_strictly_ascending(kStrappingGpios, kStrappingGpioCount));
  TEST_ASSERT_TRUE(table_is_strictly_ascending(kFlashGpios, kFlashGpioCount));
  TEST_ASSERT_TRUE(table_is_strictly_ascending(kAdc1Gpios, kAdc1GpioCount));
}

void test_every_input_only_gpio_is_an_adc1_gpio(void) {
  // 34/35/36/39 はいずれも 32〜39 の内側にある。入力専用であることと ADC1 で
  // あることが両立するため、要件 2.3 と 2.4 の判定は互いに独立に効く。
  for (std::uint8_t i = 0; i < kInputOnlyGpioCount; ++i) {
    TEST_ASSERT_TRUE(table_contains(kAdc1Gpios, kAdc1GpioCount, kInputOnlyGpios[i]));
  }
}

void test_flash_and_adc1_tables_do_not_overlap(void) {
  for (std::uint8_t i = 0; i < kFlashGpioCount; ++i) {
    TEST_ASSERT_FALSE(table_contains(kAdc1Gpios, kAdc1GpioCount, kFlashGpios[i]));
  }
}

// ---------------------------------------------------------------------------
// C. ペリフェラルの本数（要件 2.6 の判定条件の原資）
// ---------------------------------------------------------------------------

void test_peripheral_unit_counts_match_the_target_chip(void) {
  // 出典は pin 済みプラットフォームが持つ framework-espidf 5.5.5 の
  // components/soc/esp32/include/soc/soc_caps.h（pin_rules.hpp のコメントに
  // 行番号つきで記載）。
  TEST_ASSERT_EQUAL_UINT8(8, kPcntUnitCount);
  TEST_ASSERT_EQUAL_UINT8(8, kLedcChannelCount);
  TEST_ASSERT_EQUAL_UINT8(8, kAdc1ChannelCount);
}

void test_adc1_channel_count_matches_the_adc1_gpio_table_length(void) {
  // 系統の端子数と変換器のチャネル数が食い違うと、要件 2.6 の本数検査が
  // 端子表とは別の事実を見ることになる。
  TEST_ASSERT_EQUAL_UINT8(kAdc1GpioCount, kAdc1ChannelCount);
}

// ---------------------------------------------------------------------------
// D. 成立検査 checkPinPlan（要件 2.2〜2.7、タスク 1.4）
//
// ⚠️ 「成立する割当が緑になる」テストだけでは検査が素通りするだけの空虚な
// ものになりうる（tasks.md 1.4 の警告）。以下は違反種別ごとに1件ずつ、
// 意図的に壊した入力を用意し、その種別だけが検出されることを確かめる。
// ---------------------------------------------------------------------------

// D-0: 成立する割当（各用途を1回ずつ、互いに独立な端子へ割り当てる）。
void test_valid_plan_has_no_violations(void) {
  constexpr PinAssignment plan[] = {
      {PinRole::kEncoderA, 0, 13},
      {PinRole::kEncoderB, 0, 14},
      {PinRole::kMotorPwm, 0, 16},
      {PinRole::kMotorDir, 0, 17},
      {PinRole::kBatterySense, kNoWheel, 32},
      {PinRole::kBenchPot, kNoWheel, 33},
  };
  const auto diag = checkPinPlan(plan);
  TEST_ASSERT_TRUE(diag.ok());
  TEST_ASSERT_EQUAL_UINT16(0, diag.global);
  for (std::uint8_t i = 0; i < 6; ++i) {
    TEST_ASSERT_EQUAL_UINT16(0, diag.per_assignment[i]);
  }
}

// D-1: 2.2 同一端子の二重割当。
void test_detects_duplicate_gpio_assignment(void) {
  constexpr PinAssignment plan[] = {
      {PinRole::kEncoderA, 0, 4},
      {PinRole::kMotorPwm, 1, 4},  // 同じ GPIO 4 を別用途へ二重に割り当てる
  };
  const auto diag = checkPinPlan(plan);
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(hasViolation(diag.per_assignment[0], PinViolation::kDuplicate));
  TEST_ASSERT_TRUE(hasViolation(diag.per_assignment[1], PinViolation::kDuplicate));
  // 他の違反種別を巻き込んでいないことも確かめる（誤検出の混入防止）。
  TEST_ASSERT_FALSE(hasViolation(diag.per_assignment[0], PinViolation::kOutputOnInputOnly));
  TEST_ASSERT_FALSE(hasViolation(diag.per_assignment[1], PinViolation::kOutputOnInputOnly));
  TEST_ASSERT_EQUAL_UINT16(0, diag.global);
}

// D-2: 2.3 出力を要する用途への入力専用端子の割当。
void test_detects_output_role_on_input_only_gpio(void) {
  constexpr PinAssignment plan[] = {
      {PinRole::kMotorPwm, 0, 34},  // 34 は入力専用（kInputOnlyGpios）
  };
  const auto diag = checkPinPlan(plan);
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(hasViolation(diag.per_assignment[0], PinViolation::kOutputOnInputOnly));
  TEST_ASSERT_FALSE(hasViolation(diag.per_assignment[0], PinViolation::kDuplicate));
  TEST_ASSERT_FALSE(hasViolation(diag.per_assignment[0], PinViolation::kStrapping));
  TEST_ASSERT_FALSE(hasViolation(diag.per_assignment[0], PinViolation::kFlash));
  TEST_ASSERT_EQUAL_UINT16(0, diag.global);
}

// D-2b: kMotorDir（出力の向き）も同じ判定を受けることを確かめる。
void test_detects_motor_dir_on_input_only_gpio(void) {
  constexpr PinAssignment plan[] = {
      {PinRole::kMotorDir, 0, 39},  // 39 も入力専用
  };
  const auto diag = checkPinPlan(plan);
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(hasViolation(diag.per_assignment[0], PinViolation::kOutputOnInputOnly));
}

// D-2c: 入力専用端子であっても入力用途（エンコーダ）には成立する
// （要件 2.3 は出力を要する用途にのみ課される）。
void test_encoder_on_input_only_gpio_does_not_trigger_output_violation(void) {
  constexpr PinAssignment plan[] = {
      {PinRole::kEncoderA, 0, 35},
  };
  const auto diag = checkPinPlan(plan);
  TEST_ASSERT_FALSE(hasViolation(diag.per_assignment[0], PinViolation::kOutputOnInputOnly));
}

// D-3: 2.4 無線動作中に使えない変換器（バッテリ電圧監視は ADC1 必須）。
void test_detects_battery_sense_not_on_adc1(void) {
  constexpr PinAssignment plan[] = {
      {PinRole::kBatterySense, kNoWheel, 4},  // 4 は ADC1 系統に無い
  };
  const auto diag = checkPinPlan(plan);
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(hasViolation(diag.per_assignment[0], PinViolation::kNotAdc1));
  TEST_ASSERT_EQUAL_UINT16(0, diag.global);
}

// D-3b: 机上確認用可変抵抗は要件 2.4 の対象外（バッテリ電圧監視のみが
// ADC1 を要求される）。
void test_bench_pot_off_adc1_does_not_trigger_not_adc1_violation(void) {
  constexpr PinAssignment plan[] = {
      {PinRole::kBenchPot, kNoWheel, 4},
  };
  const auto diag = checkPinPlan(plan);
  TEST_ASSERT_FALSE(hasViolation(diag.per_assignment[0], PinViolation::kNotAdc1));
}

// D-4: 2.5 起動時の動作モードを決める端子（ストラッピング）への割当。
void test_detects_strapping_gpio_assignment(void) {
  constexpr PinAssignment plan[] = {
      {PinRole::kEncoderA, 0, 0},  // GPIO 0 はストラッピング端子
  };
  const auto diag = checkPinPlan(plan);
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(hasViolation(diag.per_assignment[0], PinViolation::kStrapping));
  TEST_ASSERT_FALSE(hasViolation(diag.per_assignment[0], PinViolation::kFlash));
  TEST_ASSERT_EQUAL_UINT16(0, diag.global);
}

// D-5: 2.5 内蔵フラッシュ用端子への割当。
void test_detects_flash_gpio_assignment(void) {
  constexpr PinAssignment plan[] = {
      {PinRole::kEncoderB, 0, 7},  // GPIO 7 は内蔵フラッシュ用端子
  };
  const auto diag = checkPinPlan(plan);
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(hasViolation(diag.per_assignment[0], PinViolation::kFlash));
  TEST_ASSERT_FALSE(hasViolation(diag.per_assignment[0], PinViolation::kStrapping));
  TEST_ASSERT_EQUAL_UINT16(0, diag.global);
}

// D-6: 2.6 計数器（PCNT）の本数超過。1輪1ユニットとして、
// kPcntUnitCount(8) を超える輪数のエンコーダを割り当てる。
void test_detects_pcnt_unit_overflow(void) {
  constexpr PinAssignment plan[] = {
      {PinRole::kEncoderA, 0, 4},  {PinRole::kEncoderA, 1, 13}, {PinRole::kEncoderA, 2, 14},
      {PinRole::kEncoderA, 3, 16}, {PinRole::kEncoderA, 4, 17}, {PinRole::kEncoderA, 5, 18},
      {PinRole::kEncoderA, 6, 19}, {PinRole::kEncoderA, 7, 21}, {PinRole::kEncoderA, 8, 22},
  };
  static_assert(sizeof(plan) / sizeof(plan[0]) == 9, "9輪ぶん = kPcntUnitCount(8) を1超える");
  const auto diag = checkPinPlan(plan);
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(hasViolation(diag.global, PinViolation::kUnitOverflow));
  // 個々の割当は他の違反を抱えていない（本数超過はどの1本にも帰着しない）。
  for (std::uint8_t i = 0; i < 9; ++i) {
    TEST_ASSERT_EQUAL_UINT16(0, diag.per_assignment[i]);
  }
}

// D-7: 2.6 出力生成器（LEDC）の本数超過。kLedcChannelCount(8) を超える
// モータ出力チャネルを割り当てる。
void test_detects_ledc_channel_overflow(void) {
  constexpr PinAssignment plan[] = {
      {PinRole::kMotorPwm, 0, 4},  {PinRole::kMotorPwm, 1, 13}, {PinRole::kMotorPwm, 2, 14},
      {PinRole::kMotorPwm, 3, 16}, {PinRole::kMotorPwm, 4, 17}, {PinRole::kMotorPwm, 5, 18},
      {PinRole::kMotorPwm, 6, 19}, {PinRole::kMotorPwm, 7, 21}, {PinRole::kMotorPwm, 8, 22},
  };
  static_assert(sizeof(plan) / sizeof(plan[0]) == 9, "9チャネル = kLedcChannelCount(8) を1超える");
  const auto diag = checkPinPlan(plan);
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(hasViolation(diag.global, PinViolation::kUnitOverflow));
}

// D-8: 2.6 変換器（ADC1 チャネル）の本数超過。バッテリ電圧監視・机上確認用
// 可変抵抗の合計が kAdc1ChannelCount(8) を超える場合を、机上確認用可変抵抗
// の役割で作る（バッテリ電圧監視は ADC1 端子が8本しか無いため、9本を要する
// 過多を作ろうとすると同時に 2.4 違反も混入してしまい検査を分離できない）。
void test_detects_adc1_channel_overflow(void) {
  constexpr PinAssignment plan[] = {
      {PinRole::kBenchPot, kNoWheel, 4},  {PinRole::kBenchPot, kNoWheel, 13},
      {PinRole::kBenchPot, kNoWheel, 14}, {PinRole::kBenchPot, kNoWheel, 16},
      {PinRole::kBenchPot, kNoWheel, 17}, {PinRole::kBenchPot, kNoWheel, 18},
      {PinRole::kBenchPot, kNoWheel, 19}, {PinRole::kBenchPot, kNoWheel, 21},
      {PinRole::kBenchPot, kNoWheel, 22},
  };
  static_assert(sizeof(plan) / sizeof(plan[0]) == 9, "9チャネル = kAdc1ChannelCount(8) を1超える");
  const auto diag = checkPinPlan(plan);
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(hasViolation(diag.global, PinViolation::kUnitOverflow));
  for (std::uint8_t i = 0; i < 9; ++i) {
    TEST_ASSERT_EQUAL_UINT16(0, diag.per_assignment[i]);
  }
}

// D-9: 2.7 の前提 — 未割当（kUnassigned）を含む割当は ok() にならないが、
// 実在しない端子番号に対して他の判定条件（入力専用か等）を問わない。
void test_unassigned_gpio_is_flagged_without_triggering_other_checks(void) {
  constexpr PinAssignment plan[] = {
      {PinRole::kMotorPwm, 0, kUnassigned},
  };
  const auto diag = checkPinPlan(plan);
  TEST_ASSERT_FALSE(diag.ok());
  TEST_ASSERT_TRUE(hasViolation(diag.per_assignment[0], PinViolation::kUnassigned));
  TEST_ASSERT_FALSE(hasViolation(diag.per_assignment[0], PinViolation::kOutputOnInputOnly));
  TEST_ASSERT_FALSE(hasViolation(diag.per_assignment[0], PinViolation::kStrapping));
  TEST_ASSERT_FALSE(hasViolation(diag.per_assignment[0], PinViolation::kFlash));
  TEST_ASSERT_FALSE(hasViolation(diag.per_assignment[0], PinViolation::kNotAdc1));
}

// D-10: 要件 2.7 — 実機へ書き込むことなく評価できる。`checkPinPlan` が
// `constexpr` であることを、ホスト側でもコンパイル時評価として確かめる
// （`src/board_pins.cpp` の `static_assert` が実機側で同じことを固定する）。
constexpr PinAssignment kCompileTimeValidPlan[1] = {
    {PinRole::kBatterySense, kNoWheel, 32},
};
static_assert(checkPinPlan(kCompileTimeValidPlan).ok(),
              "checkPinPlan がホストでコンパイル時に評価できない");

constexpr PinAssignment kCompileTimeInvalidPlan[1] = {
    {PinRole::kBatterySense, kNoWheel, 0},  // ストラッピング端子かつ非 ADC1
};
static_assert(!checkPinPlan(kCompileTimeInvalidPlan).ok(),
              "checkPinPlan が違反をコンパイル時に検出できない");

void test_check_pin_plan_is_constexpr_evaluable(void) {
  // 上の static_assert 群が通っていること自体が証拠だが、Unity のテスト
  // 一覧にも載せて「D. 成立検査」節の一部として可視化する。
  TEST_ASSERT_TRUE(checkPinPlan(kCompileTimeValidPlan).ok());
  TEST_ASSERT_FALSE(checkPinPlan(kCompileTimeInvalidPlan).ok());
}

int main(int argc, char **argv) {
  (void)argc;
  (void)argv;

  UNITY_BEGIN();

  RUN_TEST(test_pin_role_covers_every_use_of_a_pin);
  RUN_TEST(test_pin_assignment_defaults_to_unassigned_and_no_wheel);
  RUN_TEST(test_unassigned_is_distinguishable_from_every_real_gpio);
  RUN_TEST(test_pin_assignment_holds_role_wheel_and_gpio);
  RUN_TEST(test_no_wheel_marks_an_assignment_that_belongs_to_no_wheel);

  RUN_TEST(test_input_only_gpio_table_matches_the_datasheet);
  RUN_TEST(test_strapping_gpio_table_matches_the_datasheet);
  RUN_TEST(test_flash_gpio_table_covers_the_contiguous_range);
  RUN_TEST(test_adc1_gpio_table_covers_the_contiguous_range);
  RUN_TEST(test_tables_are_strictly_ascending);
  RUN_TEST(test_every_input_only_gpio_is_an_adc1_gpio);
  RUN_TEST(test_flash_and_adc1_tables_do_not_overlap);

  RUN_TEST(test_peripheral_unit_counts_match_the_target_chip);
  RUN_TEST(test_adc1_channel_count_matches_the_adc1_gpio_table_length);

  RUN_TEST(test_valid_plan_has_no_violations);
  RUN_TEST(test_detects_duplicate_gpio_assignment);
  RUN_TEST(test_detects_output_role_on_input_only_gpio);
  RUN_TEST(test_detects_motor_dir_on_input_only_gpio);
  RUN_TEST(test_encoder_on_input_only_gpio_does_not_trigger_output_violation);
  RUN_TEST(test_detects_battery_sense_not_on_adc1);
  RUN_TEST(test_bench_pot_off_adc1_does_not_trigger_not_adc1_violation);
  RUN_TEST(test_detects_strapping_gpio_assignment);
  RUN_TEST(test_detects_flash_gpio_assignment);
  RUN_TEST(test_detects_pcnt_unit_overflow);
  RUN_TEST(test_detects_ledc_channel_overflow);
  RUN_TEST(test_detects_adc1_channel_overflow);
  RUN_TEST(test_unassigned_gpio_is_flagged_without_triggering_other_checks);
  RUN_TEST(test_check_pin_plan_is_constexpr_evaluable);

  return UNITY_END();
}
