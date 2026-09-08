#pragma once

// teleop-bringup BenchApp (design.md "TeleopApp / BenchApp / RunRecorder",
// 要件 10.6, タスク 7.1).
//
// E-3（`docs/drivetrain-spec.md` §10.2「可変抵抗によるドライバ動作確認」）
// 専用の最小経路。机上確認用の可変抵抗（board_pins::PinRole::kBenchPot、
// バッテリ電圧監視とは別の独立した ADC1 系統）を自前で初期化して読み、
// その読み値を出力の大きさ（[0, 1] のデューティ）へ写して
// `MotorLedcAdapter::write()` へ直接渡す（design.md "BenchApp"
// Responsibilities & Constraints）。
//
// ⚠️ `MotorLedcAdapter` は本番・テレオペと**同一のクラス**をそのまま使う
// （タスク 3.2、research.md "Decision: E-3 の開ループ確認を teleop 系の
// 第3プロファイルとして分離する" の Follow-up: 「E-3 で使うモータ出力
// アダプタは本番と同一のものを使う。ここを別実装にすると E-3 が確認した
// ことが M2a へ引き継がれない」）。
//
// ⚠️ 計数器（PCNT／`EncoderPcntAdapter`）も核
// （`drivetrain_control::DrivetrainController`）も一切初期化しない。
// E-3 の切り分け対象を「ドライバ＋モータ」だけに保つことが目的であり
// （要件 10.6、tasks.md 7.1 の注記「切り分けの対象を『ドライバとモータ』
// だけに保つことが目的であり、載せるものが増えると目的を損なう」）、
// Bluepad32/BTstack にも一切触れない（操作者入力は可変抵抗のみであり、
// 無線コントローラは使わない）。
//
// ⚠️ このクラス自体はホストで検証できない（ペリフェラル API を直接叩く。
// `firmware/src/` は `[env:native]` の対象外）。ここで持つ「読み値 →
// デューティへの線形写像」は BenchApp 自身の責務として design.md が
// 明示的に要求しているものであり（「可変抵抗の読み値を出力の大きさへ
// 写し、モータ出力アダプタへ直接渡す」）、MotorLedcAdapter/
// BatteryAdcAdapter が判断を持たない、というアダプタ層
// （`drivetrain_control::*Port` 実装）に対する規約とは別の話である —
// BenchApp はそのポート実装を呼び出す側のアプリ層であり、TeleopApp が
// 制御ループの「結線」を持つのと同じ位置づけで、ここでは「pot 値 →
// duty」という写像そのものを持つ。

#include <cstdint>

#include "esp_adc/adc_oneshot.h"

#include "board_pins/pin_map.hpp"
#include "drivetrain_control/drivetrain_control.hpp"

#include "teleop/motor_ledc.hpp"

namespace teleop {

class BenchApp {
 public:
  // board_pins::kShippedPinPlan の PinRole::kBenchPot（可変抵抗、独立した
  // ADC1 系統）と PinRole::kMotorPwm/kMotorDir（3輪ぶん）を用いて構築する。
  // GPIO 番号はリテラルで持たず、端子割当の正からのみ取得する（タスク 1.5
  // の観測可能な完了状態と同じ規約）。
  //
  // Preconditions: `board_pins::kShippedPinPlan` が `PinRole::kBenchPot` と
  // 各輪の `PinRole::kMotorPwm`/`kMotorDir` の割当を持つこと。
  // Postconditions: `MotorLedcAdapter` はデューティ0で確定した状態で構築を
  // 終える（要件 5.5、MotorLedcAdapter 自身の契約をそのまま引き継ぐ）。
  // 可変抵抗側の ADC 初期化に失敗した場合は例外を投げず、以降 `run()` が
  // 読み取り不能として常にデューティ0を書き続ける（BatteryAdcAdapter と
  // 同じグレースフルデグレードの流儀。安全側 — モータは回らないまま）。
  BenchApp();

  ~BenchApp();

  BenchApp(const BenchApp&) = delete;
  BenchApp& operator=(const BenchApp&) = delete;
  BenchApp(BenchApp&&) = delete;
  BenchApp& operator=(BenchApp&&) = delete;

  // 開ループの最小経路を無限に回す（要件 10.6: 出力を連続的に変化させる）。
  // 呼び出し元 (main.cpp の app_main) からはこの呼び出しが戻らない ——
  // `TeleopApp::run()` が BTstack のメインループへ入って戻らないのと対称的
  // に、こちらは Bluepad32/BTstack を一切持たないため、自前の固定間隔
  // ポーリングループを持つ。
  [[noreturn]] void run();

 private:
  // 可変抵抗の読み値をデューティの大きさ（[0, 1]、符号なし）へ写す。
  // ADC が初期化できていない、またはその回の読み取りが失敗した場合は
  // 安全側の 0.0f を返す（モータは駆動しない）。
  float ReadDutyMagnitude();

  // 可変抵抗専用の ADC1 oneshot ユニット。⚠️
  // `teleop::BatteryAdcAdapter` とは別の系統として自前で初期化する
  // （バッテリ電圧監視側と初期化を共有しない、design.md "BenchApp"
  // 「変換器はバッテリ電圧監視とは別の系統を自前で初期化する」）。
  adc_oneshot_unit_handle_t unit_ = nullptr;
  adc_channel_t channel_ = ADC_CHANNEL_0;
  bool initialized_ = false;

  // 本番・テレオペと同一のモータ出力アダプタ（コンストラクタ冒頭コメント
  // 参照）。
  MotorLedcAdapter motor_;
};

}  // namespace teleop
