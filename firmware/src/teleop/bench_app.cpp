#include "teleop/bench_app.hpp"

// ⚠️ この翻訳単位が触れるペリフェラル/フレームワーク API は ADC1 oneshot
// (esp_adc/adc_oneshot.h、esp_err.h) と FreeRTOS の遅延 (freertos/FreeRTOS.h
// / freertos/task.h) のみである。PCNT・Bluepad32/BTstack のいずれのヘッダも
// include しない（bench_app.hpp 冒頭コメント「計数器も核も一切初期化しない」
// の実体）。

#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

namespace teleop {

namespace {

using board_pins::PinRole;

// ADC 入力レンジをフルスケール（約0〜3.3V）で使うための減衰設定。
// `teleop::BatteryAdcAdapter`（battery_adc.cpp）と同じ値だが、初期化は
// 別のユニットハンドルとして独立に行う（本ファイル冒頭コメント参照）。
constexpr adc_atten_t kAdcAtten = ADC_ATTEN_DB_12;

// `adc_oneshot_read()` の生カウント上限。`ADC_BITWIDTH_DEFAULT` は classic
// ESP32 では 12bit（0〜4095）に解決される（ESP-IDF 5.5.5,
// components/hal/esp32/include/hal/adc_types.h の
// `SOC_ADC_RTC_MAX_BITWIDTH` = 12。classic ESP32 は 12bit 固定で他の幅を
// 選べない）。⚠️ ボード定義を変更する場合は要再確認（`pin_map.hpp` の
// GPIO16/17 可否コメントと同種の注意点）。
constexpr int kAdcRawMax = 4095;

// 開ループ確認は速度PID を経由しない手動操作であり（要件10.6）、
// 要件9.1の制御周期(100Hz)ほどの精度は不要。人が可変抵抗をゆっくり回し
// ながら出力の滑らかさを観察するための表示更新頻度として妥当な値を仮に
// 置く（実測前の仮値。タスク3.2のPWM周波数・分解能と同種の判断）。
constexpr TickType_t kSampleIntervalTicks = pdMS_TO_TICKS(20);

// モータ出力アダプタ側の輪ごと設定は既定値のまま使う（極性反転をここでは
// 行わない。teleop_app.cpp の `kMotorConfigs` と同じ理由）。
constexpr MotorLedcConfig kBenchMotorConfigs[drivetrain_control::kWheelCount] = {};

// GPIO/ADC1割当のような構造的に起こり得ないはずの設定誤りは
// ESP_ERROR_CHECK で落とす（teleop::BatteryAdcAdapter と同じ2段構えの
// エラー処理方針、battery_adc.cpp CheckConfigOk 参照）。
void CheckConfigOk(esp_err_t err) {
  if (err != ESP_OK) {
    ESP_ERROR_CHECK(err);
  }
}

}  // namespace

BenchApp::BenchApp() : motor_(board_pins::kShippedPinPlan, kBenchMotorConfigs) {
  // 端子番号は端子割当の正からのみ取得する。GPIO リテラルを自前で持たない。
  const std::int8_t gpio = board_pins::gpioFor(board_pins::kShippedPinPlan, PinRole::kBenchPot,
                                                board_pins::kNoWheel);
  if (gpio == board_pins::kUnassigned) {
    ESP_ERROR_CHECK(ESP_ERR_INVALID_ARG);
  }

  // GPIO → ADC ユニット・チャネルへの対応づけは ESP-IDF の API に委ねる
  // （自前でチャネル表を持たない）。`board_pins::kShippedPinPlan` の
  // `PinRole::kBenchPot` は GPIO33（ADC1系統）を指すが、ここでも実行時に
  // unit_id を確かめる。
  adc_unit_t unit_id = ADC_UNIT_1;
  CheckConfigOk(adc_oneshot_io_to_channel(gpio, &unit_id, &channel_));
  if (unit_id != ADC_UNIT_1) {
    ESP_ERROR_CHECK(ESP_ERR_INVALID_ARG);
  }

  // ここから先（ペリフェラルハンドルの確保）は bench_app.hpp の
  // Postconditions どおり、失敗してもクラッシュせず、以降 `read()` 相当が
  // 常にデューティ0を返す状態で構築を完了する
  // （teleop::BatteryAdcAdapter と同じグレースフルデグレード）。
  adc_oneshot_unit_init_cfg_t unit_config = {};
  unit_config.unit_id = ADC_UNIT_1;
  if (adc_oneshot_new_unit(&unit_config, &unit_) != ESP_OK) {
    unit_ = nullptr;
    return;
  }

  adc_oneshot_chan_cfg_t chan_config = {};
  chan_config.atten = kAdcAtten;
  chan_config.bitwidth = ADC_BITWIDTH_DEFAULT;
  if (adc_oneshot_config_channel(unit_, channel_, &chan_config) != ESP_OK) {
    adc_oneshot_del_unit(unit_);
    unit_ = nullptr;
    return;
  }

  initialized_ = true;
}

BenchApp::~BenchApp() {
  if (unit_ != nullptr) {
    adc_oneshot_del_unit(unit_);
  }
}

float BenchApp::ReadDutyMagnitude() {
  if (!initialized_) {
    return 0.0f;
  }

  int raw = 0;
  // 読み取り不能はこの回だけの失敗として扱う（恒久的な破損とはみなさず、
  // 次回の呼び出しは独立して再試行する。teleop::BatteryAdcAdapter::read()
  // と同じ方針）。
  if (adc_oneshot_read(unit_, channel_, &raw) != ESP_OK) {
    return 0.0f;
  }

  float duty = static_cast<float>(raw) / static_cast<float>(kAdcRawMax);
  if (duty < 0.0f) {
    duty = 0.0f;
  } else if (duty > 1.0f) {
    duty = 1.0f;
  }
  return duty;
}

void BenchApp::run() {
  for (;;) {
    const float duty = ReadDutyMagnitude();

    // 3輪すべてへ同じデューティを渡す。BenchApp はどの物理モータが台上で
    // 結線されているかを知らない（E-3 の手順、タスク 8.5 が実際にどの
    // 輪を接続するかを決める）ため、可変抵抗の動きに合わせて全チャネルを
    // 一様に駆動する。MotorLedcAdapter::write() は本番・テレオペと同一の
    // 実装であり、独自の制限・補正・判断を加えない（bench_app.hpp 冒頭
    // コメント参照）。
    drivetrain_control::WheelOutputs outputs;
    for (std::uint8_t i = 0; i < drivetrain_control::kWheelCount; ++i) {
      outputs.duty[i] = duty;
    }
    motor_.write(outputs);

    vTaskDelay(kSampleIntervalTicks);
  }
}

}  // namespace teleop
