#pragma once

// teleop-bringup BatteryAdcAdapter (design.md "EncoderPcntAdapter /
// MotorLedcAdapter / BatteryAdcAdapter", 要件 6.1-6.6, タスク 3.3).
//
// drivetrain_control::BatteryVoltagePort の実装。ESP-IDF の ADC oneshot
// ドライバ (esp_adc/adc_oneshot.h) で ADC1 の生値（raw カウント）を読み、
// drivetrain_control::VoltageScaler へそのまま渡してミリボルトへ換算する。
//
// ⚠️ 判断・計算を持たない（design.md "Responsibilities & Constraints"、
// ports.hpp の契約、要件 6.3）。
//   - 生値からミリボルトへの換算（分圧比・両端の非線形補正）は
//     drivetrain_control::VoltageScaler に委ねる（要件 6.3, 6.4）。
//     このアダプタは区分線形補間・外挿のいずれも自前で書かない。
//   - 校正値（VoltageScalerParams）はコンストラクタが受け取る設定であり、
//     このファイルに実測前提の定数（テーブル点）を持たない（要件 6.6）。
//     実測にもとづく値は呼び出し側（TeleopApp、タスク 6.2、本タスクの
//     対象外）が設定として渡す。
//
// ⚠️ **VoltageScaler へ渡す `raw` は ADC ドライバの生カウント値である。**
// ESP-IDF は「ADC 自身の非線形応答を補正する」ための別の校正 API
// （esp_adc/adc_cali.h、adc_cali_raw_to_voltage 等）を持つが、本アダプタは
// これを使わない。理由:
//   - config.hpp の `VoltageScalerParams::Point` は `{raw, milli_volts}`
//     という「実測した生値と実電圧の対」をそのまま持つ形であり
//     （voltage_scaler.hpp 冒頭のコメント）、フィールド名も "raw" である。
//     校正手順（実測で複数点を取り、テーブルへ反映する）を素直に読むと、
//     ここでの "raw" は「その場で読める最も生の値」＝ADC ドライバの
//     生カウント（`adc_oneshot_read()` の出力）を指す。
//   - VoltageScaler 自身のコメントが「分圧比・非線形補正のいずれも実装内に
//     固定値として持たない」と明言しており、テーブルの外挿（クランプで
//     はなく両端区間の傾きで延長する、要件 6.4 が求める「両端の非線形性
//     補正」そのもの）は、生カウントに対して行って初めて意味を持つ
//     （esp_adc 自身の校正で mV 化した値に対して行うと、2つの独立した
//     非線形補正機構が縮退・二重適用される恐れがある）。
//   - `adc_cali.h` を用いる版は AC 6.3「上流が提供する換算部品を用いる」
//     を満たすうえで必須ではなく、むしろ VoltageScaler が担うべき換算を
//     ADC ドライバ側の校正と分担してしまい、「校正値を実測にもとづいて
//     設定できる形で保持する」（6.6）先が VoltageScalerParams 一箇所に
//     定まらなくなる。
// ⚠️ この選択は設計上の判断であり、レビュー観点として明示する
//     （タスク 3.3 の Status Report 参照）。
//
// ⚠️ ADC1 限定（要件 6.5）。無線動作中は ADC2 が使えない
//     （B-12 / research.md）。`board_pins::kShippedPinPlan` の
//     `PinRole::kBatterySense` は既に ADC1 系統の GPIO（32）を指すが
//     （タスク 1.5）、本アダプタはさらに `adc_oneshot_io_to_channel()` が
//     返す `adc_unit_t` が `ADC_UNIT_1` であることを実行時にも確かめる。
//
// ⚠️ 読み取り不能時の扱い（要件 6.2）は「設定エラー」ではなく「実行時に
// 起こりうる状態」として扱う（design.md "Error Handling" の該当行:
// 「読み取り不能（実行時）| ADC が読めない | VoltageSample.valid == false
// を返し、核が kVoltageUnavailable として遮断する。アダプタは判断しない」）。
// そのため:
//   - GPIO 未割当・ADC 有効端子でない・ADC1 でない、はいずれも
//     端子割当の正がすでに保証すべき「設定エラー」であり、
//     ESP_ERROR_CHECK で起動時に落とす（他の2アダプタと同じ流儀）。
//   - ADC ユニット・チャネルの初期化そのものが失敗した場合は、
//     design.md の Preconditions（「初期化が成功していること。失敗時
//     read() は valid == false を返す」）に従い、初期化失敗を記録して
//     以降の read() が常に valid == false を返す形にする（クラッシュ
//     させない）。
//   - 初期化後の `adc_oneshot_read()` 呼び出しがその場で失敗した場合も、
//     同じ呼び出しに対してだけ valid == false を返す（恒久的な破損とは
//     扱わない。次回の read() は独立して再試行する）。
//
// ⚠️ このアダプタ自体はホストで検証できない（`firmware/src/` は
// `[env:native]` の対象外、`test_build_src` 既定 `no`）。だからこそ
// 上記のとおり判断を持たせない（design.md "Validation" for
// BatteryAdcAdapter — 判断を持たせた瞬間に検証不能な領域が増える。
// 要件 17.3 が静的に検査する）。

#include <cstdint>

#include "esp_adc/adc_oneshot.h"

#include "board_pins/pin_map.hpp"
#include "drivetrain_control/config.hpp"
#include "drivetrain_control/ports.hpp"
#include "drivetrain_control/types.hpp"
#include "drivetrain_control/voltage_scaler.hpp"

namespace teleop {

// drivetrain_control::BatteryVoltagePort の実装（要件 6.1〜6.6）。
class BatteryAdcAdapter final : public drivetrain_control::BatteryVoltagePort {
 public:
  // plan: 端子割当の正（通常 board_pins::kShippedPinPlan）。
  // calibration: 換算に用いる校正値（実測にもとづいて呼び出し側が設定する。
  //   要件 6.6）。config.hpp の validate() を通過済みであること
  //   （VoltageScaler の事前条件、二重検証しない）。
  // Preconditions: plan は PinRole::kBatterySense（輪に紐づかない）の
  // 割当を持つこと。コンストラクタは ESP-IDF ADC oneshot ユニット（ADC1）
  // とチャネルを初期化する。初期化に失敗した場合は例外を投げず、以降の
  // read() が常に valid == false を返す状態で構築を完了する（6.2）。
  BatteryAdcAdapter(
      const board_pins::PinAssignment (&plan)[board_pins::kShippedPinPlanCount],
      const drivetrain_control::VoltageScalerParams& calibration);

  ~BatteryAdcAdapter() override;

  BatteryAdcAdapter(const BatteryAdcAdapter&) = delete;
  BatteryAdcAdapter& operator=(const BatteryAdcAdapter&) = delete;
  BatteryAdcAdapter(BatteryAdcAdapter&&) = delete;
  BatteryAdcAdapter& operator=(BatteryAdcAdapter&&) = delete;

  // バッテリ電圧をミリボルト単位で返す（要件 6.1）。読み値が得られない
  // 場合は valid == false を返す（要件 6.2）。
  // Postconditions: 核の状態を変えない（ports.hpp の契約）。
  drivetrain_control::VoltageSample read() override;

 private:
  adc_oneshot_unit_handle_t unit_ = nullptr;
  adc_channel_t channel_ = ADC_CHANNEL_0;
  bool initialized_ = false;
  // 生値からミリボルトへの換算そのもの（要件 6.3, 6.4）。read() が唯一
  // 使う「計算」はこのメンバへの委譲であり、アダプタ自身は持たない。
  drivetrain_control::VoltageScaler scaler_;
};

}  // namespace teleop
