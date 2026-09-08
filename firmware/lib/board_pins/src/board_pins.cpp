// board_pins の唯一の翻訳単位（teleop-bringup タスク 1.3、要件 2.1）。
//
// `pin_map.hpp` / `pin_rules.hpp` はいずれも `inline constexpr` のみで構成
// されるため、実行時のコードは持たない。それでも本ファイルを置いているのは
// 次の2点のためである。
//
//   1. ⚠️ **この部品がホスト向けビルドと実機向けビルドの双方で実際に
//      コンパイルされること**（本タスクの観測可能な完了状態）を、
//      「ヘッダが誰かに include されたら」ではなく、ビルド系がこの
//      コンポーネントを翻訳単位として持つことで保証する。ヘッダのみの
//      コンポーネントは IDF では INTERFACE ライブラリになり、利用側が
//      現れるまで一度もコンパイルされない。
//   2. 下の `static_assert` 群が、端子特性表そのものの内部整合（昇順・
//      範囲・表どうしの関係）を**コンパイル時に**固定する。ホスト側は
//      `test/native/test_pin_plan/` が同じ事実を実行時にも確かめるが、
//      実機側にはテストが無いため、実機ビルドで壊れた表を検出できるのは
//      ここだけである。
//
// ⚠️ ペリフェラルの API を一切参照しない（`driver/*.h` / `esp_adc/*.h`）。

#include "board_pins/pin_map.hpp"
#include "board_pins/pin_rules.hpp"

namespace board_pins {
namespace {

// ESP32 classic の端子番号の上限。表の値が現実の端子番号の範囲に収まって
// いることだけを見るための下支えであり、割当の成立検査（タスク 1.4）が
// 用いる判定条件ではない。
constexpr std::int8_t kMaxGpioNumber = 39;

constexpr bool isStrictlyAscending(const std::int8_t *table, std::uint8_t count) {
  for (std::uint8_t i = 1; i < count; ++i) {
    if (!(table[i - 1] < table[i])) {
      return false;
    }
  }
  return true;
}

constexpr bool isWithinGpioRange(const std::int8_t *table, std::uint8_t count) {
  for (std::uint8_t i = 0; i < count; ++i) {
    if (table[i] < 0 || table[i] > kMaxGpioNumber) {
      return false;
    }
  }
  return true;
}

constexpr bool contains(const std::int8_t *table, std::uint8_t count, std::int8_t gpio) {
  for (std::uint8_t i = 0; i < count; ++i) {
    if (table[i] == gpio) {
      return true;
    }
  }
  return false;
}

constexpr bool isDisjoint(const std::int8_t *lhs, std::uint8_t lhs_count, const std::int8_t *rhs,
                          std::uint8_t rhs_count) {
  for (std::uint8_t i = 0; i < lhs_count; ++i) {
    if (contains(rhs, rhs_count, lhs[i])) {
      return false;
    }
  }
  return true;
}

constexpr bool isSubsetOf(const std::int8_t *subset, std::uint8_t subset_count,
                          const std::int8_t *superset, std::uint8_t superset_count) {
  for (std::uint8_t i = 0; i < subset_count; ++i) {
    if (!contains(superset, superset_count, subset[i])) {
      return false;
    }
  }
  return true;
}

// --- 各表が昇順・重複なしであること -----------------------------------------
static_assert(isStrictlyAscending(kInputOnlyGpios, kInputOnlyGpioCount),
              "入力専用端子の表が昇順・重複なしでない");
static_assert(isStrictlyAscending(kStrappingGpios, kStrappingGpioCount),
              "ストラッピング端子の表が昇順・重複なしでない");
static_assert(isStrictlyAscending(kFlashGpios, kFlashGpioCount),
              "内蔵フラッシュ用端子の表が昇順・重複なしでない");
static_assert(isStrictlyAscending(kAdc1Gpios, kAdc1GpioCount),
              "ADC1 端子の表が昇順・重複なしでない");

// --- 各表の値が実在の端子番号の範囲に収まること -------------------------------
static_assert(isWithinGpioRange(kInputOnlyGpios, kInputOnlyGpioCount),
              "入力専用端子の表に範囲外の端子番号がある");
static_assert(isWithinGpioRange(kStrappingGpios, kStrappingGpioCount),
              "ストラッピング端子の表に範囲外の端子番号がある");
static_assert(isWithinGpioRange(kFlashGpios, kFlashGpioCount),
              "内蔵フラッシュ用端子の表に範囲外の端子番号がある");
static_assert(isWithinGpioRange(kAdc1Gpios, kAdc1GpioCount),
              "ADC1 端子の表に範囲外の端子番号がある");

// --- 表どうしの関係 ---------------------------------------------------------
// 入力専用端子 34/35/36/39 はいずれも ADC1 の範囲 32〜39 の内側にある。
// 入力専用であることと ADC1 であることは両立するため、要件 2.3 と 2.4 の
// 判定は互いに独立に効く。
static_assert(isSubsetOf(kInputOnlyGpios, kInputOnlyGpioCount, kAdc1Gpios, kAdc1GpioCount),
              "入力専用端子が ADC1 の系統に含まれていない");
// 内蔵フラッシュ用 6〜11 と ADC1 32〜39 は重ならない。
static_assert(isDisjoint(kFlashGpios, kFlashGpioCount, kAdc1Gpios, kAdc1GpioCount),
              "内蔵フラッシュ用端子と ADC1 端子が重複している");
// ストラッピング端子と内蔵フラッシュ用端子も重ならない（要件 2.5 は両者を
// 同じ「避けるべき端子」として扱うが、由来の異なる別々の表である）。
static_assert(isDisjoint(kStrappingGpios, kStrappingGpioCount, kFlashGpios, kFlashGpioCount),
              "ストラッピング端子と内蔵フラッシュ用端子が重複している");

// --- 変換器のチャネル数と端子表の長さの一致 -----------------------------------
// 食い違うと、要件 2.6 の本数検査が端子表とは別の事実を見ることになる。
static_assert(kAdc1ChannelCount == kAdc1GpioCount,
              "ADC1 のチャネル数と ADC1 端子表の長さが一致しない");

// --- 未割当を表す番兵が実在の端子と衝突しないこと -----------------------------
static_assert(kUnassigned < 0, "kUnassigned が実在の端子番号と衝突しうる");
static_assert(kNoWheel > 0, "kNoWheel が実在の輪の添字と衝突しうる");

// --- ペリフェラルの本数が正であること -----------------------------------------
static_assert(kPcntUnitCount > 0, "計数器の本数が 0 である");
static_assert(kLedcChannelCount > 0, "出力生成器の本数が 0 である");
static_assert(kAdc1ChannelCount > 0, "変換器の本数が 0 である");

}  // namespace
}  // namespace board_pins
