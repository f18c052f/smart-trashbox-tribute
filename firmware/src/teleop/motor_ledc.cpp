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

// in_channel_ の添字の意味。motor_ledc.hpp のメンバ宣言のコメントと対。
constexpr std::uint8_t kIn1 = 0;  // PinRole::kMotorPwm の端子 → ドライバ IN1
constexpr std::uint8_t kIn2 = 1;  // PinRole::kMotorDir の端子 → ドライバ IN2

// 輪の添字から LEDC チャネルへの固定対応。案B では輪ごとに2チャネルを
// 占める（IN1 側と IN2 側）。3輪で6本であり、低速モードのチャネル数
// kLedcChannelCount=8 の内側に収まる（pin_rules.hpp 参照）。
// ⚠️ pin_rules.hpp の `isGeneratorRole` は kMotorDir を生成器を占有しない
// 用途として扱ったままである。6本 ≤ 8本なので成立検査は通るが、判定の
// 定義が実態と食い違っている。実機で案Bを確認したうえで是正する。
constexpr ledc_channel_t kChannelForWheel[drivetrain_control::kWheelCount][2] = {
    {LEDC_CHANNEL_0, LEDC_CHANNEL_1},
    {LEDC_CHANNEL_2, LEDC_CHANNEL_3},
    {LEDC_CHANNEL_4, LEDC_CHANNEL_5},
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

// LEDC チャネルを1本、デューティ 0 で確定させた状態で有効化する。
// 要件 5.5 の要。duty=0 を有効化そのものに含める（「まず有効化して後で
// ゼロを書く」のではなく、有効化がゼロデューティで行われる）。IN1 側と
// IN2 側の両方をこれで初期化するため、構築完了時点で (L,L) = ストップが
// 確定している。
void InitChannel(ledc_channel_t channel, std::int8_t gpio) {
  ledc_channel_config_t channel_config = {};
  channel_config.gpio_num = gpio;
  channel_config.speed_mode = LEDC_LOW_SPEED_MODE;
  channel_config.channel = channel;
  channel_config.intr_type = LEDC_INTR_DISABLE;
  channel_config.timer_sel = LEDC_TIMER_0;
  channel_config.duty = 0;
  channel_config.hpoint = 0;
  ESP_ERROR_CHECK(ledc_channel_config(&channel_config));
}

// デューティを書いて反映させる。独自の制限・補正を加えない（要件 5.2）。
// 契約上の範囲を外れた値は ledc_set_duty() 自身の検証と直後の
// ESP_ERROR_CHECK が失敗として露出させる（クランプして黙って通さない）。
void SetDuty(ledc_channel_t channel, std::uint32_t raw_duty) {
  ESP_ERROR_CHECK(ledc_set_duty(LEDC_LOW_SPEED_MODE, channel, raw_duty));
  ESP_ERROR_CHECK(ledc_update_duty(LEDC_LOW_SPEED_MODE, channel));
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
  // ⚠️ 名前は kMotorPwm / kMotorDir のままだが、案B では**両方とも PWM**
  // であり、前者がドライバの IN1、後者が IN2 へ繋がる（motor_ledc.hpp 参照）。
  const std::int8_t gpio_in1 = board_pins::gpioFor(plan, PinRole::kMotorPwm, wheel);
  const std::int8_t gpio_in2 = board_pins::gpioFor(plan, PinRole::kMotorDir, wheel);
  CheckAssigned(gpio_in1);
  CheckAssigned(gpio_in2);

  in_channel_[wheel][kIn1] = kChannelForWheel[wheel][kIn1];
  in_channel_[wheel][kIn2] = kChannelForWheel[wheel][kIn2];

  // 2本ともゼロデューティで確定させてから有効化する。write() はこの構築が
  // 完了した後にしか呼ばれ得ない（teleop::MotorLedcAdapter の生存期間規則）
  // ため、制御ループが最初の write() を呼ぶまでの間、出力は (L,L) =
  // ストップのまま確定している（要件 5.5）。
  InitChannel(in_channel_[wheel][kIn1], gpio_in1);
  InitChannel(in_channel_[wheel][kIn2], gpio_in2);
}

void MotorLedcAdapter::write(const drivetrain_control::WheelOutputs& outputs) {
  for (std::uint8_t wheel = 0; wheel < drivetrain_control::kWheelCount; ++wheel) {
    const float duty = outputs.duty[wheel];  // [-1, +1]。0 が遮断値（要件 5.3）

    // 符号 → どちらのチャネルへデューティを書くか。輪ごとの向き反転
    // （要件 5.4）は、書き込む先を選ぶだけの設定選択であり演算ではない。
    const bool forward = duty >= 0.0f;
    const bool physical_forward = forward != configs_[wheel].invert_direction;  // XOR

    // 大きさ → PWM デューティ。独自の制限・補正を加えない（要件 5.2）。
    // 受け取った大きさをそのまま満スケール値へ写すだけであり、契約上の
    // 範囲 [-1, +1] を外れた値が来た場合は ledc_set_duty() 自身の検証と
    // 直後の ESP_ERROR_CHECK が失敗を露出させる（クランプして黙って
    // 通すことはしない）。
    const float magnitude = forward ? duty : -duty;
    const std::uint32_t raw_duty =
        static_cast<std::uint32_t>(magnitude * static_cast<float>(kLedcMaxDuty) + 0.5f);

    // 案B: 駆動する側へ PWM、休ませる側は L。大きさ 0 のときは駆動側の
    // デューティも 0 になるため、結果として両方 L =(L,L)= ストップになる
    // （要件 5.3 に特別な分岐を要しない）。
    const std::uint8_t active = physical_forward ? kIn1 : kIn2;
    const std::uint8_t idle = physical_forward ? kIn2 : kIn1;

    // ⚠️ **休ませる側を先に 0 にしてから駆動側を書く。** 逆順にすると、
    // 方向が切り替わる瞬間に両チャネルが同時に非ゼロとなる窓ができ、
    // (H,H) = ブレーキが一瞬挟まる。LEDC は次の周期境界で反映されるため
    // この窓は最大1周期（20kHz で 50us）だが、短絡制動が意図せず入るのは
    // 避ける。
    SetDuty(in_channel_[wheel][idle], 0);
    SetDuty(in_channel_[wheel][active], raw_duty);
  }
}

}  // namespace teleop
