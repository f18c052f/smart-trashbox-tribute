#pragma once

// teleop-bringup MotorLedcAdapter (design.md "EncoderPcntAdapter /
// MotorLedcAdapter / BatteryAdcAdapter", 要件 5.1-5.5, タスク 3.2).
//
// drivetrain_control::MotorOutputPort の実装。AE-TB67H450 モータドライバ
// （PWM+DIR インタフェース）向けに、ESP-IDF の LEDC ドライバ
// (driver/ledc.h) で PWM の大きさを、素の GPIO 出力で回転方向を生成する。
//
// ⚠️ 判断・計算を持たない（design.md "Responsibilities & Constraints"、
// ports.hpp の契約、要件 5.2）。
//   - write() は受け取った WheelOutputs::duty（[-1, +1]、0 が遮断値）を
//     そのまま「符号 → 方向 GPIO」「大きさ → PWM デューティ」へ写像する
//     だけである。独自の制限（クランプ）・補正を加えない。渡された大きさが
//     ハードウェアの許容範囲を外れた場合は ledc_set_duty 自身の検証と
//     ESP_ERROR_CHECK による起動時/実行時の失敗に委ねる（要件 4.6 でのグリッ
//     チフィルタと同じ流儀。クランプすると判断を持たせたことになり、
//     上流の遮断（duty==0）が効かなくなる、design.md の懸念そのもの）。
//   - 指令がゼロを示した場合（要件 5.3）に特別な分岐は無い。大きさ 0 は
//     そのまま PWM デューティ 0 に写り、駆動が止まる。
//   - 輪ごとの向き反転（要件 5.4）は演算ではなく、方向 GPIO へ書き込む
//     論理レベルを選ぶだけの設定選択でしかない。
//   - 端子番号は board_pins（端子割当の正）からのみ取得する。
//
// ⚠️ 起動直後で制御ループが動く前はモータを駆動しない（要件 5.5）。
// コンストラクタは LEDC チャネルを「初期デューティ 0」で確定させてから
// 有効化する（ledc_channel_config_t::duty を 0 に設定した状態で
// ledc_channel_config() を呼ぶ。これは「まず有効化してから後で 0 を書く」
// のではなく、有効化そのものがゼロデューティで行われる。この呼び出しが
// write() より先に完了することは、write() が呼べるのはこの構築が終わった
// 後という C++ の生存期間規則そのものが保証する）。方向 GPIO も
// gpio_set_level() でレベルを確定させてから gpio_config() で出力を有効化
// する（gpio_config() 呼び出し前に出力レジスタへ既定値を書いておくことで、
// 出力モード有効化の瞬間に不定レベルが出ないようにする）。
//
// ⚠️ このアダプタ自体はホストで検証できない（`firmware/src/` は
// `[env:native]` の対象外、`test_build_src` 既定 `no`）。だからこそ
// 上記のとおり判断を持たせない（design.md "Validation" for
// MotorLedcAdapter と同じ EncoderPcntAdapter 節の注記 — 判断を持たせた
// 瞬間に検証不能な領域が増える。要件 17.3 が静的に検査する）。
//
// BenchApp（タスク 7.1、design.md「MotorLedcAdapter は本番と同一のものを
// 使う」）もこのクラスをそのまま使う。本ファイルは bench 専用の分岐を
// 一切持たない。

#include <cstdint>

#include "driver/gpio.h"
#include "driver/ledc.h"

#include "board_pins/pin_map.hpp"
#include "drivetrain_control/drivetrain_control.hpp"

namespace teleop {

// 輪ごとの設定値。演算を伴わない、そのまま使われる値のみを持つ
// （design.md「輪ごとの向き反転（5.4）は設定値として持ち、演算はしない」）。
struct MotorLedcConfig {
  bool invert_direction = false;  // 要件 5.4。回転方向の向きの反転
};

// drivetrain_control::MotorOutputPort の実装（要件 5.1〜5.5）。
class MotorLedcAdapter final : public drivetrain_control::MotorOutputPort {
 public:
  // plan: 端子割当の正（通常 board_pins::kShippedPinPlan）。
  // configs: 輪ごとの設定（drivetrain_control::kWheelCount 個、輪の添字順）。
  // Preconditions: plan は各輪の PinRole::kMotorPwm / kMotorDir の割当を
  // 持つこと。コンストラクタは ESP-IDF LEDC ペリフェラルと方向 GPIO を
  // 初期化し、デューティ 0（= 駆動なし）で確定させた状態で戻る（要件 5.5）。
  MotorLedcAdapter(
      const board_pins::PinAssignment (&plan)[board_pins::kShippedPinPlanCount],
      const MotorLedcConfig (&configs)[drivetrain_control::kWheelCount]);

  ~MotorLedcAdapter() override = default;

  MotorLedcAdapter(const MotorLedcAdapter&) = delete;
  MotorLedcAdapter& operator=(const MotorLedcAdapter&) = delete;
  MotorLedcAdapter(MotorLedcAdapter&&) = delete;
  MotorLedcAdapter& operator=(MotorLedcAdapter&&) = delete;

  // 受け取った出力指令をそのまま方向 GPIO と PWM デューティへ写す
  // （要件 5.1, 5.2, 5.3）。独自の制限・補正・判断を加えない。
  void write(const drivetrain_control::WheelOutputs& outputs) override;

 private:
  void InitWheel(
      std::uint8_t wheel,
      const board_pins::PinAssignment (&plan)[board_pins::kShippedPinPlanCount]);

  MotorLedcConfig configs_[drivetrain_control::kWheelCount];
  gpio_num_t dir_gpio_[drivetrain_control::kWheelCount] = {};
  ledc_channel_t pwm_channel_[drivetrain_control::kWheelCount] = {};
};

}  // namespace teleop
