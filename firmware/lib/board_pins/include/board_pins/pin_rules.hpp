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

}  // namespace board_pins
