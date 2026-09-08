#include "teleop/battery_adc.hpp"

#include "esp_err.h"

namespace teleop {

namespace {

using board_pins::PinRole;

// ADC 入力レンジをフルスケール（約0〜3.3V、分圧後の電圧に合わせる）で使う
// ための減衰設定。分圧比そのものは VoltageScaler の校正テーブルが担う
// （このアダプタは分圧比を知らない、要件 6.3）。
constexpr adc_atten_t kAdcAtten = ADC_ATTEN_DB_12;

// GPIO が端子割当の正で未割当のまま渡された場合、または ADC の有効端子
// でない場合に、実行時に確実に失敗させる。これらは端子割当の正
// （board_pins::kShippedPinPlan、タスク 1.5・checkPinPlan、タスク 1.4）が
// すでに保証すべき設定エラーであり、実行時に起こりうる「読み取り不能」
// （要件 6.2）とは別種の不具合である。⚠️ assert() だけに頼らない
// （release ビルドで NDEBUG が立つと無効化される、タスク 3.1/3.2 と同じ
// 注意点）。ESP_ERROR_CHECK は NDEBUG の影響を受けない。
void CheckConfigOk(esp_err_t err) {
  if (err != ESP_OK) {
    ESP_ERROR_CHECK(err);
  }
}

}  // namespace

BatteryAdcAdapter::BatteryAdcAdapter(
    const board_pins::PinAssignment (&plan)[board_pins::kShippedPinPlanCount],
    const drivetrain_control::VoltageScalerParams& calibration)
    : scaler_(calibration) {
  // 端子番号は端子割当の正からのみ取得する。GPIO リテラルを自前で持たない。
  const std::int8_t gpio = board_pins::gpioFor(plan, PinRole::kBatterySense, board_pins::kNoWheel);
  if (gpio == board_pins::kUnassigned) {
    ESP_ERROR_CHECK(ESP_ERR_INVALID_ARG);
  }

  // GPIO → ADC ユニット・チャネルへの対応づけそのものは ESP-IDF の API に
  // 委ねる（自前でチャネル表を持たない）。要件 6.5（ADC1 限定）は
  // board_pins::kShippedPinPlan がすでに GPIO32（ADC1 系統）を割り当てて
  // いるが、ここでも実行時に unit_id を確かめる。無線動作中に使えない
  // ADC2 が万一渡された場合、それは端子割当の正の破損であり、設定エラー
  // として起動時に落とす（read() の valid==false 経路には流さない）。
  adc_unit_t unit_id = ADC_UNIT_1;
  CheckConfigOk(adc_oneshot_io_to_channel(gpio, &unit_id, &channel_));
  if (unit_id != ADC_UNIT_1) {
    ESP_ERROR_CHECK(ESP_ERR_INVALID_ARG);
  }

  // ここから先（ペリフェラルハンドルの確保）は design.md の Precondition
  // どおり「初期化が成功していること。失敗時 read() は valid == false を
  // 返す」（要件 6.2）に従う。設定そのものは正しくても、ペリフェラルの
  // 確保が失敗しうる（例: 資源の競合）ため、ESP_ERROR_CHECK で落とさず
  // initialized_ を false のまま構築を完了させる。
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

BatteryAdcAdapter::~BatteryAdcAdapter() {
  if (unit_ != nullptr) {
    adc_oneshot_del_unit(unit_);
  }
}

drivetrain_control::VoltageSample BatteryAdcAdapter::read() {
  drivetrain_control::VoltageSample sample;  // 既定で valid == false（要件 6.2）

  if (!initialized_) {
    return sample;
  }

  int raw = 0;
  // 読み取り不能（実行時、要件 6.2）はこの呼び出し限りの失敗として扱う。
  // 恒久的な破損とはみなさず、次回の read() は独立して再試行する。
  if (adc_oneshot_read(unit_, channel_, &raw) != ESP_OK) {
    return sample;
  }

  // 生値からミリボルトへの換算は VoltageScaler に委ねる（要件 6.3）。
  // 両端の非線形性補正（要件 6.4）はテーブル境界外の外挿としてこの
  // 呼び出しの内部で行われ、アダプタ側では一切演算しない。
  sample.valid = true;
  sample.milli_volts = scaler_.toMilliVolts(static_cast<std::int32_t>(raw));
  return sample;
}

}  // namespace teleop
