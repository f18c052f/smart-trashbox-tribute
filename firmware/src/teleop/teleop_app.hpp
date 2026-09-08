#pragma once

// teleop-bringup TeleopApp (design.md "TeleopApp / BenchApp / RunRecorder",
// 要件 9.1-9.7, 14.8, タスク 6.2).
//
// 制御ループ本体。3つのペリフェラルアダプタ（EncoderPcntAdapter /
// MotorLedcAdapter / BatteryAdcAdapter、タスク 3.1-3.3）・操作者入力
// （ControllerLink + teleop_input::mapPad、タスク 4.2/5.2）・核
// （drivetrain_control::DrivetrainController）・走行記録（RunRecorder、
// タスク 6.1）を結線する。
//
// ⚠️ 判断・計算をここへ持ち込まない（design.md TeleopApp Responsibilities
// & Constraints、要件 9.5）。保護の判定・遮断値の決定・入力の変換は
// すべて `drivetrain_control` / `teleop_input` へ委譲する。本クラスが
// 持つのは「どの順で・どの周期で呼ぶか」という結線だけである。
//
// ⚠️ このクラス自体はホストで検証できない（ペリフェラル・BTstack を
// 直接叩く）。だからこそ上記のとおり判断を持たせない
// （EncoderPcntAdapter 等と同じ理由、design.md "Validation"）。
//
// 制御ループ1周期の呼び出し順序（design.md System Flows「制御ループ1周期」
// を正確に踏襲。teleop_app.cpp の runOnce() 参照）:
//   1. ControllerLink::read() でパッド状態と接続状態のスナップショットを得る
//   2. teleop_input::mapPad() で正規化済み軸を指令へ変換する。⚠️ `now` 引数
//      には制御ループ自身の壁時計ではなく、pad_seq の変化から求めた
//      「最後に genuine な BT 入力を観測した時刻」（下記
//      last_pad_change_at_ms_ 参照）を渡す ―― CommandWatchdog を実効化
//      するため（要件 9.6/14.2/B-7）
//   3. DrivetrainController::submit()（body/wheel のどちらかを wheel_scoped
//      で選ぶ）
//   4. DrivetrainController::setOutputEnabled()（3 とは独立した操作、要件9.4）
//   5. DrivetrainController::step()
//   6. DrivetrainController::resetProtections() — ⚠️ step() の直後に、
//      間に他のどんな呼び出しも挟まず呼ぶ（核のヘッダの事前条件）
//   7. DrivetrainController::status()
//   8. ロック保護の立ち上がりエッジで ControllerLink::notifyMotorLockHaptic()
//      を呼ぶ（要件 14.8。resetProtections() の後 — 6 と 6 の間には何も
//      挟まないという制約を破らない）
//   9. RunRecorder へ1周期ぶんのサンプルを追記する
//
// Bluepad32/BTstack のブートストラップ（controller_link.hpp 冒頭コメント
// 「本タスクの外（タスク 6.2 / TeleopApp の担当）」参照）は本クラスの
// run() が担う。制御ループそのものは別の FreeRTOS タスクで走らせる ——
// btstack_run_loop_execute() は呼び出したタスクを BTstack のメインループへ
// 専有し、通常は戻らないため、同じタスクで固定周期の制御ループを回すことは
// できない（design.md "Concurrency": Bluepad32 のコールバックは制御ループ
// とは別コンテキストで呼ばれる）。

#include <cstdint>

#include "drivetrain_control/drivetrain_control.hpp"
#include "run_recorder/run_recorder.hpp"
#include "teleop_input/mapped_command.hpp"

#include "teleop/battery_adc.hpp"
#include "teleop/controller_link.hpp"
#include "teleop/encoder_pcnt.hpp"
#include "teleop/motor_ledc.hpp"

namespace teleop {

class TeleopApp {
 public:
  TeleopApp();

  TeleopApp(const TeleopApp&) = delete;
  TeleopApp& operator=(const TeleopApp&) = delete;
  TeleopApp(TeleopApp&&) = delete;
  TeleopApp& operator=(TeleopApp&&) = delete;

  // 制御ループを起動する。呼び出し元 (main.cpp の app_main) のタスクは
  // Bluepad32/BTstack のブートストラップ後、BTstack のメインループ
  // (btstack_run_loop_execute()) に入り、通常は戻らない。固定周期の制御
  // ループ自体は内部で生成する別タスクが担う（クラス冒頭コメント参照）。
  //
  // 要件 9.7: `DrivetrainController::configure()` が失敗した場合（本来
  // 起こらないはずの設定エラー。核が定める「設定エラー」の判断をそのまま
  // 尊重し、ここで独自の遮断ロジックを追加しない、要件 9.5 と同じ精神）、
  // または制御タスクの生成に失敗した場合は、一度も step() を呼ばずに戻る。
  // `MotorLedcAdapter` は構築時点でデューティ0のまま初期化されている
  // （タスク 3.2、要件 5.5）ため、この経路でモータが駆動されることはない
  // ―― 「制御ループが停止している間はモータへの出力を継続しない」
  // （要件 9.7）を、制御ループの外側から一度も動かさないことで満たす。
  void run();

 private:
  // 制御ループ1周期ぶん（クラス冒頭コメントの手順1〜9）。FreeRTOS タスク
  // から一定周期で呼ばれる。
  void runOnce(drivetrain_control::TimeMs now);

  // 単調増加ミリ秒（要件 9.2）。ホストに millis() は無いため
  // esp_timer_get_time()（マイクロ秒、起動からの単調増加）をミリ秒へ変換
  // する。
  static drivetrain_control::TimeMs nowMs() noexcept;

  // FreeRTOS タスクのトランポリン（vtable が非静的メンバ関数を取れない
  // ため、controller_link.hpp の Bluepad32 トランポリンと同じ理由の自由
  // 関数）。
  static void ControlTaskTrampoline(void* self);
  [[noreturn]] void controlLoop();

  // --- 核の設定（アダプタより先に宣言する。BatteryAdcAdapter の校正値に
  // config_.voltage_scaler をそのまま渡すため、宣言順 = 構築順として
  // config_ をアダプタより先に構築する） --------------------------------
  drivetrain_control::DrivetrainConfig config_;

  // --- ペリフェラルアダプタ（board_pins::kShippedPinPlan から構築） -------
  EncoderPcntAdapter encoder_;
  MotorLedcAdapter motor_;
  BatteryAdcAdapter battery_;
  drivetrain_control::Ports ports_{};

  // --- 核の本体 ---------------------------------------------------------
  drivetrain_control::DrivetrainController controller_;

  // --- 入力変換パラメータ（要件 8.9: 実走しながら調整できる形で保持する） --
  teleop_input::MappingParams mapping_params_;

  // --- 走行記録（タスク 6.1）。⚠️ ~140KiB の固定配列を内包するため、この
  // メンバを含む TeleopApp のインスタンス自体を FreeRTOS タスクスタック上
  // に置かないこと（run_recorder.hpp 冒頭コメント参照）。呼び出し側
  // (main.cpp) は static/グローバルとして保持する。
  run_recorder::RunRecorder recorder_;

  // 要件 14.8: ロック保護の立ち上がりエッジ検出用。前回サイクルで
  // いずれかの輪がロック保護中だったか。
  bool motor_lock_active_prev_ = false;

  // 要件 15.1, 15.2 の「走行」の境界を、出力許可（デッドマン押下・解放）の
  // 立ち上がり・立ち下がりに対応付ける実装判断（requirements.md /
  // design.md のどちらも走行の開始・終了の具体的なトリガーを規定して
  // いない。task 6.1 の kMaxCapacity 試算が前提とする「1回の走行が
  // 数十秒程度」に最も自然に合致する境界として採用した。M2a の実走調整
  // 対象 — タスク 4.2/5.2 の未規定パラメータ選定と同種の判断）。
  bool output_enabled_prev_ = false;

  // 要件 9.6/14.2/B-7 (CommandWatchdog 実効化。タスク 6.2 レビュー指摘の
  // 是正): ControllerLink::UpdatePad() が genuine な BT 到着ごとに進める
  // `ControllerSnapshot::pad_seq` を1周期ごとに監視し、実際に変化した周期
  // でだけ「最後に genuine な入力を観測した時刻」を更新する。
  //
  // ⚠️ この2つを teleop_input::mapPad() の `now` 引数として、制御ループ
  // 自身の壁時計 (runOnce() の引数 `now`) の代わりに渡す
  // (teleop_app.cpp runOnce() 参照)。壁時計をそのまま渡すと、BT信号が
  // クリーンな切断なしに途絶えた場合でも mapPad() が返す
  // BodyVelocityCommand/WheelVelocityCommand::issued_at_ms が毎周期
  // 進んでしまい、drivetrain_control::CommandInput の過去時刻拒否
  // (issued_at_ms <= latched().issued_at_ms) が決して発火せず、結果として
  // 核の CommandWatchdog（唯一の「指令途絶」検出手段、design.md Error
  // Handling「接続断（実行時）」行）が無力化される。pad_seq の変化でのみ
  // 更新することで、スティック値そのものの float 等値比較（操作者がスティ
  // ックを完全に一定に保っている場合に誤って「途絶」と判定するリスクが
  // ある）に頼らずに「新しい BT パケットが届いたか」を厳密に判定する。
  std::uint32_t last_pad_seq_ = 0;
  drivetrain_control::TimeMs last_pad_change_at_ms_ = 0;
};

}  // namespace teleop
