#include "teleop/motor_ledc.hpp"

#include "esp_err.h"

namespace teleop {

namespace {

using board_pins::PinRole;

// LEDC タイマ設定。3輪とも同一タイマ（LEDC_TIMER_0）・同一速度モード
// （低速モード。フェード割り込み等は使わず、ISR を必要としないため高速
// モードを選ぶ理由が無い）を共有する。
//
// 周波数はモータドライバの可聴域を避ける目安として 20kHz を選ぶ
// （AE-TB67H450 のような H ブリッジ IC の PWM 入力として一般的な帯域。
// docs/drivetrain-spec.md はモータ PWM の具体的な周波数値を定めていない
// ため、この値は実測校正前の目安であり、9章のブリングアップで見直す
// 余地がある）。
inline constexpr std::uint32_t kLedcFrequencyHz = 20000;

// デューティ分解能 10bit（0〜1023）。80MHz の APB クロックから 20kHz を
// 割り出すのに十分な分解能であり（80,000,000 / 20,000 = 4,000 ≒ 2^11.97、
// 10bit=1024 段階はこの範囲に収まる）、LEDC_AUTO_CLK が自動選択するクロッ
// ク源のもとで ledc_timer_config() が成立する値として選んでいる。
inline constexpr ledc_timer_bit_t kLedcDutyResolution = LEDC_TIMER_10_BIT;

// [0, 1] に正規化した大きさを整数デューティへ写すときの満スケール値。
// 「大きさ → PWM デューティ」という写像そのものは要件 5.1 が求める変換で
// あり、独自の制限・補正（要件 5.2 が禁じるもの）ではない。
inline constexpr std::uint32_t kLedcMaxDuty = (1u << kLedcDutyResolution) - 1u;  // 1023

// 輪の添字から LEDC チャネルへの固定対応。輪ごとに1チャネルを占める
// （kLedcChannelCount=8 に対し3輪ぶんで十分収まる、pin_rules.hpp 参照）。
constexpr ledc_channel_t kChannelForWheel[drivetrain_control::kWheelCount] = {
    LEDC_CHANNEL_0,
    LEDC_CHANNEL_1,
    LEDC_CHANNEL_2,
};

// GPIO が端子割当の正で未割当のまま渡された場合に、実行時に確実に失敗
// させる。⚠️ assert() だけに頼らない（release ビルドで NDEBUG が立つと
// assert() は消え、-1 が黙って周辺機器 API へ渡ってしまう。タスク 3.1 の
// Implementation Notes が指摘した種類の不具合）。ESP_ERROR_CHECK は
// NDEBUG の影響を受けないため、release ビルドでも中断する。
void CheckAssigned(std::int8_t gpio) {
  if (gpio == board_pins::kUnassigned) {
    ESP_ERROR_CHECK(ESP_ERR_INVALID_ARG);
  }
}

}  // namespace

MotorLedcAdapter::MotorLedcAdapter(
    const board_pins::PinAssignment (&plan)[board_pins::kShippedPinPlanCount],
    const MotorLedcConfig (&configs)[drivetrain_control::kWheelCount])
    : configs_{configs[0], configs[1], configs[2]} {
  static_assert(drivetrain_control::kWheelCount == 3,
                "configs_ initializer list has exactly kWheelCount entries");

  ledc_timer_config_t timer_config = {};
  timer_config.speed_mode = LEDC_LOW_SPEED_MODE;
  timer_config.duty_resolution = kLedcDutyResolution;
  timer_config.timer_num = LEDC_TIMER_0;
  timer_config.freq_hz = kLedcFrequencyHz;
  timer_config.clk_cfg = LEDC_AUTO_CLK;
  ESP_ERROR_CHECK(ledc_timer_config(&timer_config));

  for (std::uint8_t wheel = 0; wheel < drivetrain_control::kWheelCount; ++wheel) {
    InitWheel(wheel, plan);
  }
}

void MotorLedcAdapter::InitWheel(
    std::uint8_t wheel,
    const board_pins::PinAssignment (&plan)[board_pins::kShippedPinPlanCount]) {
  // 端子番号は端子割当の正からのみ取得する。GPIO リテラルを自前で持たない。
  const std::int8_t gpio_pwm = board_pins::gpioFor(plan, PinRole::kMotorPwm, wheel);
  const std::int8_t gpio_dir = board_pins::gpioFor(plan, PinRole::kMotorDir, wheel);
  CheckAssigned(gpio_pwm);
  CheckAssigned(gpio_dir);

  dir_gpio_[wheel] = static_cast<gpio_num_t>(gpio_dir);
  pwm_channel_[wheel] = kChannelForWheel[wheel];

  // 方向 GPIO: 出力レジスタへ既定レベル（前進 = 0、要件 5.4 の反転前の
  // 基準）を書いてから出力モードを有効化する。この順序であれば、
  // gpio_config() が出力を有効にした瞬間から既に既定レベルが出ており、
  // 不定レベルが一瞬でも出力される余地が無い。PWM デューティは別途
  // ゼロで確定させる（下記）ため、このレベル自体は要件 5.5 の充足に
  // 直接は効かないが、同じ「確定させてから有効化する」流儀を揃えている。
  ESP_ERROR_CHECK(gpio_set_level(dir_gpio_[wheel], 0));
  gpio_config_t dir_config = {};
  dir_config.pin_bit_mask = 1ULL << static_cast<std::uint8_t>(gpio_dir);
  dir_config.mode = GPIO_MODE_OUTPUT;
  dir_config.pull_up_en = GPIO_PULLUP_DISABLE;
  dir_config.pull_down_en = GPIO_PULLDOWN_DISABLE;
  dir_config.intr_type = GPIO_INTR_DISABLE;
  ESP_ERROR_CHECK(gpio_config(&dir_config));

  // PWM チャネル: 要件 5.5 の要。duty=0 を有効化そのものに含める
  // （「まず有効化して後でゼロを書く」のではなく、有効化がゼロデューティ
  // で行われる）。write() はこの構築が完了した後にしか呼ばれ得ない
  // （teleop::MotorLedcAdapter の生存期間規則）ため、制御ループが最初の
  // write() を呼ぶまでの間、PWM 出力はゼロのまま確定している。
  ledc_channel_config_t channel_config = {};
  channel_config.gpio_num = gpio_pwm;
  channel_config.speed_mode = LEDC_LOW_SPEED_MODE;
  channel_config.channel = pwm_channel_[wheel];
  channel_config.intr_type = LEDC_INTR_DISABLE;
  channel_config.timer_sel = LEDC_TIMER_0;
  channel_config.duty = 0;
  channel_config.hpoint = 0;
  ESP_ERROR_CHECK(ledc_channel_config(&channel_config));
}

void MotorLedcAdapter::write(const drivetrain_control::WheelOutputs& outputs) {
  for (std::uint8_t wheel = 0; wheel < drivetrain_control::kWheelCount; ++wheel) {
    const float duty = outputs.duty[wheel];  // [-1, +1]。0 が遮断値（要件 5.3）

    // 符号 → 方向。輪ごとの向き反転（要件 5.4）は、書き込む論理レベルを
    // 選ぶだけの設定選択であり演算ではない。
    const bool forward = duty >= 0.0f;
    const bool physical_forward = forward != configs_[wheel].invert_direction;  // XOR
    ESP_ERROR_CHECK(gpio_set_level(dir_gpio_[wheel], physical_forward ? 1 : 0));

    // 大きさ → PWM デューティ。独自の制限・補正を加えない（要件 5.2）。
    // 受け取った大きさをそのまま満スケール値へ写すだけであり、契約上の
    // 範囲 [-1, +1] を外れた値が来た場合は ledc_set_duty() 自身の検証と
    // 直後の ESP_ERROR_CHECK が失敗を露出させる（クランプして黙って
    // 通すことはしない）。
    const float magnitude = forward ? duty : -duty;
    const std::uint32_t raw_duty =
        static_cast<std::uint32_t>(magnitude * static_cast<float>(kLedcMaxDuty) + 0.5f);
    ESP_ERROR_CHECK(ledc_set_duty(LEDC_LOW_SPEED_MODE, pwm_channel_[wheel], raw_duty));
    ESP_ERROR_CHECK(ledc_update_duty(LEDC_LOW_SPEED_MODE, pwm_channel_[wheel]));
  }
}

}  // namespace teleop
