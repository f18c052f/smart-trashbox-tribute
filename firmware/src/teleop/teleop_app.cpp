#include "teleop/teleop_app.hpp"

// ⚠️ この翻訳単位が Bluepad32/BTstack を直接呼ぶのはブートストラップ関数
// (uni_platform_set_custom / uni_init / btstack_run_loop_execute) の3つに
// 限る。controller_link.hpp 冒頭コメントが「本タスクの外（タスク 6.2 /
// TeleopApp の担当）」として明示的にこの3つを名指している。Bluepad32 の
// "デバイス" 型（uni_hid_device_t / uni_controller_t / uni_gamepad_t 等）
// には一切触れない ―― それらは controller_link.cpp だけが扱う
// （controller_link.hpp 冒頭「本ファイルだけが Bluepad32 の型に触れる」の
// 対象は、そちら側の変換ロジックであり、本ファイルはそれとは独立に
// フレームワーク起動の3関数だけを呼ぶ）。
#include <uni.h>

#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "board_pins/pin_map.hpp"

namespace teleop {

namespace {

using drivetrain_control::BlockMask;
using drivetrain_control::BlockReason;
using drivetrain_control::DrivetrainConfig;
using drivetrain_control::GeometryParams;
using drivetrain_control::kWheelCount;
using drivetrain_control::TimeMs;

// 制御ループの周期（要件 9.1）。run_recorder.hpp 冒頭コメントが引用する
// OQ-22「目安 100〜200Hz程度」の下限側（100Hz = 10ms）を仮に採用。実走で
// 見直す対象（タスク 3.2 の PWM 周波数・分解能と同種の「実測前の仮値」）。
constexpr TimeMs kControlPeriodMs = 10;

// 制御タスクの優先度・スタックサイズ。requirements.md / design.md のどちらも
// 具体値を規定していない実装判断。ESP-IDF の xTaskCreate は
// stack_depth をバイト単位で取る。
constexpr UBaseType_t kControlTaskPriority = 5;
constexpr std::uint32_t kControlTaskStackBytes = 8192;

// アダプタ側の輪ごと設定は既定値のまま使う（EncoderPcntConfig::
// invert_direction / MotorLedcConfig::invert_direction はいずれも極性反転
// をここでは行わない ―― MakeConfig() のコメント参照。極性の吸収は
// EncoderParams::polarity / OutputParams::polarity の1箇所に寄せる）。
// アダプタのコンストラクタは `const Config (&)[kWheelCount]` を取るため、
// 名前を持つ配列として定義する。
constexpr EncoderPcntConfig kEncoderConfigs[kWheelCount] = {};
constexpr MotorLedcConfig kMotorConfigs[kWheelCount] = {};

// TeleopApp の稼働に使う DrivetrainConfig を組み立てる。
//
// ⚠️ ここに置く数値は、docs/drivetrain-spec.md が確定済みの値
// （60mm ホイール・3S LiPo・分圧比 27/(100+27) 等）を根拠にできるものは
// それに従い、それ以外（シャシー半径・エンコーダ実測分解能・PID ゲイン・
// 保護閾値・PWM 上限基準電圧）は同ドキュメントが「実機試験で調整する」
// 「M2a で実測する」と明記している未確定値であり、ここでは実走前の仮値を
// 置く（タスク 3.2 の PWM 周波数・分解能、タスク 6.1 の kMaxCapacity と
// 同種の判断。config.hpp は既定値を持たないため、この関数がその置き場に
// なる）。タスク 10.1（実測にもとづく決定の記録）が確定値へ差し替える。
DrivetrainConfig MakeConfig() noexcept {
  DrivetrainConfig config{};

  // 機体寸法: base_radius_mm はシャシー未確定（docs/drivetrain-spec.md
  // §6.4「モータ到着後、仮組みして実寸を測定してから CAD を完成させる」）
  // のため実測前の仮値。
  config.geometry = GeometryParams::equilateral(/*base_radius_mm=*/150.0f,
                                                 /*first_wheel_angle_rad=*/0.0f);

  // エンコーダ: ホイール径 60mm は docs/drivetrain-spec.md §1 の確定値。
  // counts_per_wheel_rev は同 §3.2 が「固定値をハードコードしないこと」と
  // 明記しており、M2a-0（タスク 9.1）の実測校正までの目安値
  // （11 PPR × 減速比 約19 ≈ 209 pulse/回転）を仮に置く。
  config.encoder.counts_per_wheel_rev = 209;
  config.encoder.wheel_diameter_mm = 60.0f;
  config.encoder.raw_modulus = 65536;  // PCNT 符号付き16bitの法（encoder_pcnt.hpp と同じ値）
  // A/B相の実際の色対応は wiring.md の未確定欄（M2a-0 実測まで確定しない）。
  // 極性反転は EncoderParams::polarity の1箇所でのみ行い（controller.hpp
  // 冒頭コメント「極性の吸収は EncoderParams::polarity /
  // OutputParams::polarity がそれぞれ1箇所で行う」）、
  // EncoderPcntAdapter::EncoderPcntConfig::invert_direction は既定
  // （false）のまま使わない。
  config.encoder.polarity[0] = 1;
  config.encoder.polarity[1] = 1;
  config.encoder.polarity[2] = 1;

  // モータ配線の実際の極性はブリングアップ（E-2〜E-3、タスク 8.4/8.5）で
  // 確定する。同じ理由で MotorLedcAdapter::MotorLedcConfig::invert_direction
  // は使わず、OutputParams::polarity の1箇所へ寄せる。
  config.output.polarity[0] = 1;
  config.output.polarity[1] = 1;
  config.output.polarity[2] = 1;
  config.output.absolute_max_duty = 1.0f;

  // 運動上限: docs/drivetrain-spec.md §3.1 の理論周速（無負荷 1.66 m/s）を
  // 安全側に絞った仮値。実車速度は M2 で実測する（同 §3.1 の注記）。
  config.limits.max_body_speed_mm_s = 600.0f;
  config.limits.max_body_omega_rad_s = 4.0f;
  config.limits.max_wheel_speed_mm_s = 800.0f;

  // 速度PID: docs/drivetrain-spec.md はゲインを規定せず「実走しながら調整
  // できる形」（要件 13.2）だけを求める。保守的に小さいゲインから始める。
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    config.pid[i].kp = 0.4f;
    config.pid[i].ki = 1.0f;
    config.pid[i].kd = 0.0f;
    config.pid[i].integral_limit = 1.0f;
  }

  // モータロック保護: docs/drivetrain-spec.md §5 の目安値（T = 100〜200ms）
  // を採用。復帰条件（自動/手動）は同 §5・OQ-15 が M2a-1 の挙動を見て
  // 決めるとしており、latching=true（手動リセット）を安全側の既定とする。
  config.lock.duty_threshold = 0.3f;
  config.lock.speed_threshold_mm_s = 10.0f;
  config.lock.duration_ms = 200;
  config.lock.clear_duration_ms = 500;
  config.lock.latching = true;

  // 低電圧保護: docs/drivetrain-spec.md §9.2 は具体閾値を M2 の実測後
  // （OQ-14）に決めるとしているため、3S LiPo の一般的な安全域
  // （3.3V/セル停止・3.5V/セル警告・3.6V/セル復帰）を仮に置く。
  config.low_voltage.warn_milli_volts = 10500;
  config.low_voltage.stop_milli_volts = 9900;
  config.low_voltage.recover_milli_volts = 10800;
  config.low_voltage.average_window = 8;
  config.low_voltage.stop_duration_ms = 500;
  config.low_voltage.unavailable_duration_ms = 500;
  config.low_voltage.latching = true;

  // 電圧換算テーブル: docs/drivetrain-spec.md §9.1 の分圧比
  // （27/(100+27) ≈ 0.2126）と ESP32 の 12bit ADC 既定レンジ（0〜4095 ≈
  // 0〜3.3V）から求めた理論値。同 §9.1「実装後はテスターで実電圧を測定し
  // てキャリブレーションする」対象であり、BatteryAdcAdapter 経由で
  // 実際に使われるのはこのテーブル（config_.voltage_scaler、validate()
  // 済みを BatteryAdcAdapter へそのまま渡す。battery_adc.hpp Preconditions
  // 「config.hpp の validate() を通過済みであること」参照）。
  config.voltage_scaler.table[0] = {0, 0};
  config.voltage_scaler.table[1] = {2048, 6300};
  config.voltage_scaler.table[2] = {4095, 12600};
  config.voltage_scaler.point_count = 3;

  // PWM上限: docs/drivetrain-spec.md §9.3 の式（PWM_MAX = min(1.0, 12.0 /
  // measured_battery_voltage)）を組み込みの式（override_fn=nullptr）へ
  // 委ねる。reference_milli_volts はモータ公称電圧 12V。
  config.pwm_ceiling.enabled = true;
  config.pwm_ceiling.reference_milli_volts = 12000;
  config.pwm_ceiling.fallback_duty = 0.3f;
  config.pwm_ceiling.override_fn = nullptr;

  // 指令途絶（要件 9.6）: 具体値はタスク 10.1（実測にもとづく決定）が
  // 確定させるまでの仮値。
  config.watchdog.timeout_ms = 500;

  return config;
}

}  // namespace

TeleopApp::TeleopApp()
    : config_(MakeConfig()),
      encoder_(board_pins::kShippedPinPlan, kEncoderConfigs),
      motor_(board_pins::kShippedPinPlan, kMotorConfigs),
      battery_(board_pins::kShippedPinPlan, config_.voltage_scaler) {
  ports_.encoder = &encoder_;
  ports_.motor = &motor_;
  ports_.battery = &battery_;

  mapping_params_.deadzone = 0.05f;
  mapping_params_.curve_exponent = 1.0f;
  // 安全側に絞った実装判断（要件 8.8 の "機体能力に対する割合"）。
  // M2a の実走調整対象（タスク 4.2 と同種の未規定パラメータ選定）。
  mapping_params_.speed_scale = 0.5f;
  // config_ の MotionLimits を単一の数値源として再利用し、入力側の上限
  // （teleop_input）と核側のクランプ（drivetrain_control）が別々の値へ
  // ずれないようにする。
  mapping_params_.max_body_mm_s = config_.limits.max_body_speed_mm_s;
  mapping_params_.max_omega_rad_s = config_.limits.max_body_omega_rad_s;
}

drivetrain_control::TimeMs TeleopApp::nowMs() noexcept {
  // 要件 9.2: 単調増加する現在時刻。ホストに millis() は無いため
  // esp_timer_get_time()（起動からの単調増加マイクロ秒）をミリ秒へ変換
  // する。
  return static_cast<drivetrain_control::TimeMs>(esp_timer_get_time() / 1000);
}

void TeleopApp::ControlTaskTrampoline(void* self) {
  static_cast<TeleopApp*>(self)->controlLoop();
}

void TeleopApp::run() {
  const drivetrain_control::ConfigDiagnostic diag =
      controller_.configure(config_, ports_, nowMs());
  if (!diag.ok()) {
    // クラス冒頭コメント参照: 設定エラーは核が定める判断をそのまま尊重し、
    // 一度も step() を呼ばずに戻る。MotorLedcAdapter は構築時点でデュー
    // ティ0のまま初期化済みのため、モータは駆動されない（要件 9.7）。
    return;
  }

  const BaseType_t created = xTaskCreate(&ControlTaskTrampoline, "teleop_ctrl",
                                          kControlTaskStackBytes, this,
                                          kControlTaskPriority, nullptr);
  if (created != pdPASS) {
    // 制御タスクの生成に失敗（OOM等）。上記と同じ理由でモータは駆動され
    // ない。BTstack のブートストラップも行わずに戻る。
    return;
  }

  // Bluepad32/BTstack のブートストラップ（controller_link.hpp 冒頭コメント
  // 「本タスクの外（タスク 6.2 / TeleopApp の担当）」）。
  // ⚠️ uni_platform_set_custom() は uni_init() より前に呼ぶこと
  // （Bluepad32 自身の契約。呼ばずに CONFIG_BLUEPAD32_PLATFORM_CUSTOM へ
  // 入ると uni_platform_init() が無限ループで停止する）。
  uni_platform_set_custom(ControllerLink::instance().platform());
  uni_init(0, nullptr);

  // BTstack のメインループ。呼び出したこのタスク（app_main）を専有し、
  // 通常は戻らない。固定周期の制御ループは上で生成した別タスクが担う。
  btstack_run_loop_execute();
}

void TeleopApp::controlLoop() {
  TickType_t last_wake = xTaskGetTickCount();
  for (;;) {
    runOnce(nowMs());
    vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(kControlPeriodMs));
  }
}

void TeleopApp::runOnce(drivetrain_control::TimeMs now) {
  // 1. パッド状態と接続状態のスナップショット。
  const ControllerSnapshot snapshot = ControllerLink::instance().read();

  // 1.5. 要件 9.6/14.2/B-7 (CommandWatchdog 実効化): pad_seq が前回の周期
  // から変化した ―― つまり ControllerLink::UpdatePad() が genuine な BT
  // 到着を1回以上処理した ―― ときにだけ、「最後に genuine な入力を観測
  // した時刻」を今の周期の壁時計 (now) へ進める。BT信号がクリーンな切断
  // なしに途絶えている間は pad_seq が変化しないため、この値は凍結された
  // ままになる（teleop_app.hpp last_pad_change_at_ms_ のコメント参照）。
  if (snapshot.pad_seq != last_pad_seq_) {
    last_pad_seq_ = snapshot.pad_seq;
    last_pad_change_at_ms_ = now;
  }

  // 2. 正規化済み軸 → 指令・出力許可（判定・遮断は持たない純関数）。
  // ⚠️ ループ自身の壁時計 `now` ではなく `last_pad_change_at_ms_` を渡す
  // （直上のコメント、および teleop_app.hpp last_pad_change_at_ms_ の
  // コメント参照）。BT信号が途絶えた場合、これにより mapPad() が返す
  // issued_at_ms も凍結し、drivetrain_control::CommandInput の過去時刻
  // 拒否 (issued_at_ms <= latched().issued_at_ms) が latched_.issued_at_ms
  // を同じく凍結させたまま保つ。核の CommandWatchdog は
  // (制御ループの壁時計 now) - (凍結された latched_.issued_at_ms) の
  // 経過時間を毎周期見ているため、途絶が続けば必ず timeout_ms を超えて
  // 発火する。
  const teleop_input::MappedCommand mapped =
      teleop_input::mapPad(snapshot.pad, mapping_params_, last_pad_change_at_ms_);

  // 3. 指令の投入（要件 9.3）。body/wheel は排他（mapPad の契約）。
  if (mapped.wheel_scoped) {
    controller_.submit(mapped.wheel);
  } else {
    controller_.submit(mapped.body);
  }

  // 4. 出力許可（要件 9.4: 3 とは独立した操作として扱う）。
  controller_.setOutputEnabled(mapped.output_enabled, now);

  // 5. 制御ステップ。保護の判定・遮断値の決定はすべて核へ委ねる（要件9.5）。
  const drivetrain_control::StepResult result = controller_.step(now);

  // 6. ⚠️ resetProtections() は step() の直後に、間に他のどんな呼び出しも
  // 挟まず呼ぶ（核のヘッダの事前条件。controller.hpp resetProtections()
  // コメント参照）。
  controller_.resetProtections(now);

  // 7. 状態スナップショット（resetProtections() 後の最新状態）。
  const drivetrain_control::DrivetrainStatus status = controller_.status();

  // 8. 要件 14.8: ロック保護の立ち上がりエッジでのみ触覚通知を呼ぶ
  // （5.2 のレビューが要求した「立ち上がりエッジのみ」のデバウンス。
  // controller_link.cpp のプール修正と合わせて、通知を毎周期スパムせず、
  // かつバッファ競合を避ける）。resetProtections() の後であり、6 と 6 の
  // 間には何も挟んでいない。
  BlockMask locked_mask = 0;
  for (std::uint8_t w = 0; w < kWheelCount; ++w) {
    locked_mask |= result.wheel_reasons[w];
  }
  const bool locked_now =
      (locked_mask & static_cast<BlockMask>(BlockReason::kMotorLock)) != 0;
  if (locked_now && !motor_lock_active_prev_) {
    ControllerLink::instance().notifyMotorLockHaptic();
  }
  motor_lock_active_prev_ = locked_now;

  // 9. 走行記録。要件 15.1/15.2 の「走行」境界を出力許可の立ち上がり/
  // 立ち下がりへ対応付ける（teleop_app.hpp output_enabled_prev_ 参照）。
  if (mapped.output_enabled && !output_enabled_prev_) {
    recorder_.beginRun(now);
  }

  run_recorder::RunSample sample;
  sample.t_ms = now;
  for (std::uint8_t w = 0; w < kWheelCount; ++w) {
    sample.command_wheel_mm_s[w] = status.wheel_target_mm_s[w];
    sample.measured_wheel_mm_s[w] = status.wheel_measured_mm_s[w];
    sample.wheel_protection_reasons[w] = status.wheel_reasons[w];
  }
  sample.battery_milli_volts = status.battery_milli_volts;
  sample.battery_valid = status.battery_valid;
  sample.global_protection_reasons = status.global_reasons;
  sample.output_enabled = status.output_enabled;
  recorder_.append(sample);  // isRunning()==false の間は無視される（RunRecorder の契約）

  if (!mapped.output_enabled && output_enabled_prev_) {
    recorder_.endRun();
  }
  output_enabled_prev_ = mapped.output_enabled;
}

}  // namespace teleop
