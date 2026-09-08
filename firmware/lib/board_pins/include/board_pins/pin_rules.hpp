#pragma once

// board_pins: ESP32 classic の端子特性表とペリフェラルの本数
// （teleop-bringup タスク 1.3、要件 2.1）。
//
// 要件 2.2〜2.6 が求める成立検査の判定条件は、いずれも「対象マイコンの
// 実際の端子特性」に依拠する。⚠️ **端子割当表は人が実際の配線に使う**ため、
// ここに置く値は記憶からの再導出ではなく出典で固める（research.md
// "ESP32 の端子制約（端子割当の成立条件の根拠）"）。
//
// 成立検査の述語そのもの（`checkPinPlan`）はタスク 1.4 が本ヘッダへ追加する。
// 本タスクの範囲は定数表までである。
//
// ⚠️ `pin_map.hpp` と同じく、ペリフェラルの API を一切参照しない。
// `driver/*.h` / `esp_adc/*.h` を include しない。
//
// ⚠️ 表はすべて狭義単調増加（昇順・重複なし）で保つ。タスク 1.4 の照合を
// 素直に書けるようにするためであり、`src/board_pins.cpp` の
// `static_assert` がこの不変条件をホスト・実機の双方で固定する。

#include <cstdint>

#include "board_pins/pin_map.hpp"

namespace board_pins {

// ---------------------------------------------------------------------------
// 端子特性表
//
// 出典: research.md "ESP32 の端子制約" が挙げる3件
//   - ESP-IDF Programming Guide — GPIO & RTC GPIO (ESP32)
//   - ESP32 Pinout Reference (Random Nerd Tutorials)
//   - ESP32 Strapping Pins List (espboards.dev)
// ---------------------------------------------------------------------------

// 入力専用の端子（要件 2.3 の判定条件）。
// ⚠️ これらは**内部プルアップ・プルダウンを持たない**。出力用途へ割り当て
// られないことに加え、オープンコレクタ出力のエンコーダをここへ置くと外部
// プルアップが必須になる（要件 4.7 / B-13）。出力形式が未確認である以上、
// 出荷する割当（タスク 1.5）では避けるほうが安全側である。
inline constexpr std::uint8_t kInputOnlyGpioCount = 4;
inline constexpr std::int8_t kInputOnlyGpios[kInputOnlyGpioCount] = {34, 35, 36, 39};

// 起動時の動作モードを決める端子（要件 2.5 の判定条件の前半）。
inline constexpr std::uint8_t kStrappingGpioCount = 5;
inline constexpr std::int8_t kStrappingGpios[kStrappingGpioCount] = {0, 2, 5, 12, 15};

// 内蔵フラッシュとの通信に用いる端子（要件 2.5 の判定条件の後半）。
// 連続した範囲 6〜11 だが、他の表と同じ走査で扱えるよう列挙で持つ。
inline constexpr std::uint8_t kFlashGpioCount = 6;
inline constexpr std::int8_t kFlashGpios[kFlashGpioCount] = {6, 7, 8, 9, 10, 11};

// ADC1 の系統に属する端子（要件 2.4 の判定条件）。連続した範囲 32〜39。
// ⚠️ ADC2 は無線の動作中に使用できない（B-12）。バッテリ電圧監視は本番の
// 無線ビルドでも読めなければならないため、ADC1 であることが成立条件になる。
inline constexpr std::uint8_t kAdc1GpioCount = 8;
inline constexpr std::int8_t kAdc1Gpios[kAdc1GpioCount] = {32, 33, 34, 35, 36, 37, 38, 39};

// ---------------------------------------------------------------------------
// ペリフェラルの本数（要件 2.6 の判定条件）
//
// 出典: pin 済みプラットフォーム pioarduino platform-espressif32 55.03.311 が
// 持つ framework-espidf 5.5.5（package.json version 3.50505）の
// `components/soc/esp32/include/soc/soc_caps.h`。
// ⚠️ 記憶からの再導出ではなく、実際にビルドへ使われる版のヘッダを読んだ値
// である。行番号は当該版のもの。
// ---------------------------------------------------------------------------

// 計数器（PCNT）のユニット数。
// soc_caps.h:273 `SOC_PCNT_GROUPS (1U)` × :274 `SOC_PCNT_UNITS_PER_GROUP (8)`。
// エンコーダ3系統がそれぞれ1ユニットを占める。
inline constexpr std::uint8_t kPcntUnitCount = 8;

// 出力生成器（LEDC）のチャネル数。soc_caps.h:245 `SOC_LEDC_CHANNEL_NUM (8)`。
// ⚠️ ESP32 classic は高速モードと低速モードを持つ（:243
// `SOC_LEDC_SUPPORT_HS_MODE (1)`）ため、両モードを使い分ければ合計 16 まで
// 取れる。ここでは**片モードあたりの本数**を上限として扱う。単一の速度
// モードに収める前提のほうが安全側であり、モードを跨いだ割当を必要とする
// なら、その時点で明示的にこの定数を見直す。
inline constexpr std::uint8_t kLedcChannelCount = 8;

// 変換器（ADC1）のチャネル数。soc_caps.h:126
// `SOC_ADC_CHANNEL_NUM(PERIPH_NUM) ((PERIPH_NUM==0)? 8: 10)` の ADC1 側。
// `kAdc1GpioCount` と一致する（一致は src/board_pins.cpp が固定する）。
inline constexpr std::uint8_t kAdc1ChannelCount = 8;

// ---------------------------------------------------------------------------
// 成立検査（要件 2.2〜2.7、teleop-bringup タスク 1.4）
//
// 出荷する具体的な端子割当（タスク 1.5）はここでは決めない。本節が定める
// のは「端子割当がどう与えられても機械的に判定できる述語」だけである。
// ---------------------------------------------------------------------------

// 個々の割当・全体のいずれかで検出しうる違反種別。design.md board_pins の
// Service Interface が定めるビット割当をそのまま用いる。1本の割当が複数の
// 違反を同時に抱えうるためビットフラグにしてある（例: ストラッピング端子
// かつ内蔵フラッシュ用端子、という重なりは無いが、将来 union する場合に
// 備える）。
enum class PinViolation : std::uint16_t {
  kNone = 0,
  kDuplicate = 1u << 0,          // 2.2 同一端子の二重割当
  kOutputOnInputOnly = 1u << 1,  // 2.3 入力専用端子への出力割当
  kNotAdc1 = 1u << 2,            // 2.4 無線動作中に使えない変換器
  kStrapping = 1u << 3,          // 2.5 起動モードを決める端子
  kFlash = 1u << 4,              // 2.5 内蔵フラッシュ用端子
  kUnitOverflow = 1u << 5,       // 2.6 計数器・出力生成器・変換器の本数超過
  kUnassigned = 1u << 6,         // まだ端子が定まっていない（違反ではないが ok() を偽にする）
};
using PinViolationMask = std::uint16_t;

// 診断結果。`global` は特定の割当1本に帰着しない違反（2.6 の本数超過）を、
// `per_assignment[i]` は `assignments[i]` そのものが抱える違反を持つ。
//
// ⚠️ design.md の Service Interface は `PinPlanDiagnostic` を非テンプレートの
// 固定長として描いているが、その固定長（`kAssignmentCount`）を決める具体的な
// 端子割当は出荷値（タスク 1.5、本タスクの対象外）である。本タスクの時点で
// 特定の本数へ決め打ちしないため、割当本数 `N` をテンプレート引数として
// 受け取る形にしている（`N` は `checkPinPlan` へ渡す配列の要素数から推論
// される）。タスク 1.5 はここに定義した `checkPinPlan` をそのまま出荷値へ
// 適用できる。
template <std::uint8_t N>
struct PinPlanDiagnostic {
  PinViolationMask global = 0;
  PinViolationMask per_assignment[N] = {};

  constexpr bool ok() const noexcept {
    if (global != 0) {
      return false;
    }
    for (std::uint8_t i = 0; i < N; ++i) {
      if (per_assignment[i] != 0) {
        return false;
      }
    }
    return true;
  }
};

namespace pin_rules_detail {

// `pin_rules.hpp` 内で完結する小さな線形探索。`src/board_pins.cpp` の
// 無名名前空間にも同種の関数があるが、あちらは表そのものの内部整合を
// コンパイル時に固定するための専用ヘルパーであり、こちらは
// `PinAssignment` の実行時評価（`checkPinPlan`）が使う。用途が異なる
// ヘッダ／実装単位をまたいで共有する意味が薄い小関数のため、意図して
// 独立させてある。
constexpr bool contains(const std::int8_t *table, std::uint8_t count, std::int8_t gpio) noexcept {
  for (std::uint8_t i = 0; i < count; ++i) {
    if (table[i] == gpio) {
      return true;
    }
  }
  return false;
}

// 出力を要する用途（2.3 の判定対象）。エンコーダ入力・バッテリ電圧監視・
// 机上確認用可変抵抗はいずれも入力であり対象外。
constexpr bool isOutputRole(PinRole role) noexcept {
  return role == PinRole::kMotorPwm || role == PinRole::kMotorDir;
}

// 無線動作中も使える変換器（ADC1）であることを要求される用途（2.4）。
// 要件 2.4 はバッテリ電圧監視についてのみ述べており、机上確認用可変抵抗
// （無線を用いない BenchApp 経路でのみ読む）には課さない。
constexpr bool requiresAdc1(PinRole role) noexcept {
  return role == PinRole::kBatterySense;
}

// 計数器（PCNT）を占有する用途。1輪のエンコーダはA相・B相の2端子で1ユニット
// を共有するため、本数超過検査（`checkPinPlan` 内）ではユニットの占有元を
// 「輪の添字」の重複を除いて数える。
constexpr bool isCounterRole(PinRole role) noexcept {
  return role == PinRole::kEncoderA || role == PinRole::kEncoderB;
}

// 出力生成器（LEDC）を占有する用途。モータ方向（kMotorDir）は素の GPIO 出力
// であり LEDC を使わないため対象外。
constexpr bool isGeneratorRole(PinRole role) noexcept { return role == PinRole::kMotorPwm; }

// 変換器（ADC1 チャネル）を占有する用途。2.4 の「ADC1 でなければならない」
// 制約とは別軸で、単に「アナログ入力として1チャネルを使う」ことを数える
// （バッテリ電圧監視・机上確認用可変抵抗の両方）。
constexpr bool isConverterRole(PinRole role) noexcept {
  return role == PinRole::kBatterySense || role == PinRole::kBenchPot;
}

}  // namespace pin_rules_detail

// 端子割当の集合を評価し、要件 2.2〜2.6 それぞれの成立条件を機械的に
// 判定する。実機のペリフェラルへ触れない純関数であり、`constexpr` として
// 定義しているためホスト・実機いずれのビルドでもコンパイル時に評価できる
// （要件 2.7 を「ビルド時」まで前倒しで満たす。ホストのユニットテストは
// 実行時評価でも同じ結果になることを確かめる）。
//
// ⚠️ 出荷する端子割当を決めない（タスク 1.5 の責務）。ここで固定するのは
// 判定条件だけである。
template <std::uint8_t N>
constexpr PinPlanDiagnostic<N> checkPinPlan(const PinAssignment (&assignments)[N]) noexcept {
  using pin_rules_detail::contains;
  using pin_rules_detail::isConverterRole;
  using pin_rules_detail::isCounterRole;
  using pin_rules_detail::isGeneratorRole;
  using pin_rules_detail::isOutputRole;
  using pin_rules_detail::requiresAdc1;

  PinPlanDiagnostic<N> diagnostic{};

  // --- 割当1本ごとの違反 (2.2, 2.3, 2.4, 2.5) --------------------------------
  for (std::uint8_t i = 0; i < N; ++i) {
    const PinAssignment &a = assignments[i];

    if (a.gpio == kUnassigned) {
      // 未割当の端子は「まだ決まっていない」ことだけを示す。他の判定条件は
      // 実在する端子番号を前提とするため評価しない（実在しない番地に対して
      // 「入力専用か」等を問うても意味を持たない）。
      diagnostic.per_assignment[i] |= static_cast<PinViolationMask>(PinViolation::kUnassigned);
      continue;
    }

    // 2.2: 同一端子の二重割当。
    for (std::uint8_t j = 0; j < N; ++j) {
      if (j == i) {
        continue;
      }
      if (assignments[j].gpio != kUnassigned && assignments[j].gpio == a.gpio) {
        diagnostic.per_assignment[i] |= static_cast<PinViolationMask>(PinViolation::kDuplicate);
        break;
      }
    }

    // 2.3: 出力を要する用途への入力専用端子の割当。
    if (isOutputRole(a.role) && contains(kInputOnlyGpios, kInputOnlyGpioCount, a.gpio)) {
      diagnostic.per_assignment[i] |= static_cast<PinViolationMask>(PinViolation::kOutputOnInputOnly);
    }

    // 2.4: 無線動作中に使えない変換器（バッテリ電圧監視は ADC1 必須）。
    if (requiresAdc1(a.role) && !contains(kAdc1Gpios, kAdc1GpioCount, a.gpio)) {
      diagnostic.per_assignment[i] |= static_cast<PinViolationMask>(PinViolation::kNotAdc1);
    }

    // 2.5: 起動時の動作モードを決める端子、または内蔵フラッシュ用端子への
    // 割当（用途を問わず不成立）。
    if (contains(kStrappingGpios, kStrappingGpioCount, a.gpio)) {
      diagnostic.per_assignment[i] |= static_cast<PinViolationMask>(PinViolation::kStrapping);
    }
    if (contains(kFlashGpios, kFlashGpioCount, a.gpio)) {
      diagnostic.per_assignment[i] |= static_cast<PinViolationMask>(PinViolation::kFlash);
    }
  }

  // --- ペリフェラルの本数超過 (2.6) ------------------------------------------
  // 特定の1本に帰着しない違反であるため `global` へ記録する。

  // 計数器（PCNT）: 1輪のエンコーダがA相・B相で1ユニットを共有するため、
  // 「輪の添字」の重複を除いて数える。
  std::uint8_t counter_units = 0;
  for (std::uint8_t i = 0; i < N; ++i) {
    const PinAssignment &a = assignments[i];
    if (a.gpio == kUnassigned || !isCounterRole(a.role)) {
      continue;
    }
    bool already_counted = false;
    for (std::uint8_t j = 0; j < i; ++j) {
      const PinAssignment &b = assignments[j];
      if (b.gpio != kUnassigned && isCounterRole(b.role) && b.wheel_index == a.wheel_index) {
        already_counted = true;
        break;
      }
    }
    if (!already_counted) {
      ++counter_units;
    }
  }
  if (counter_units > kPcntUnitCount) {
    diagnostic.global |= static_cast<PinViolationMask>(PinViolation::kUnitOverflow);
  }

  // 出力生成器（LEDC）: モータ出力の大きさ（kMotorPwm）1本につき1チャネル。
  std::uint8_t generator_channels = 0;
  for (std::uint8_t i = 0; i < N; ++i) {
    const PinAssignment &a = assignments[i];
    if (a.gpio != kUnassigned && isGeneratorRole(a.role)) {
      ++generator_channels;
    }
  }
  if (generator_channels > kLedcChannelCount) {
    diagnostic.global |= static_cast<PinViolationMask>(PinViolation::kUnitOverflow);
  }

  // 変換器（ADC1 チャネル）: バッテリ電圧監視・机上確認用可変抵抗の合計。
  std::uint8_t converter_channels = 0;
  for (std::uint8_t i = 0; i < N; ++i) {
    const PinAssignment &a = assignments[i];
    if (a.gpio != kUnassigned && isConverterRole(a.role)) {
      ++converter_channels;
    }
  }
  if (converter_channels > kAdc1ChannelCount) {
    diagnostic.global |= static_cast<PinViolationMask>(PinViolation::kUnitOverflow);
  }

  return diagnostic;
}

}  // namespace board_pins
