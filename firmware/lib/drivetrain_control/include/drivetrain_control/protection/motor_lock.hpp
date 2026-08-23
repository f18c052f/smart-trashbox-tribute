#pragma once

// drivetrain_control MotorLockDetector (design.md L7-L8 保護層
// "MotorLockDetector", 要件 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7).
//
// 保護①: 出力を出しているのに輪が回っていない状態（モータロック）を、
// 輪1個ぶんについて検出する状態機械。design.md "保護① モータロックの
// 状態遷移（輪ごと・3個独立）" のとおり、3状態を持つ:
//
//   Idle    --[|commanded_duty| >= duty_threshold
//               && |measured_mm_s| <= speed_threshold_mm_s]--> Suspect
//   Suspect --[条件が崩れる]--> Idle（継続時間の計測をリセット。要件 10.4）
//   Suspect --[条件の継続が duration_ms を超える]--> Tripped
//   Tripped --[latching == false かつ 条件が clear_duration_ms だけ
//              崩れ続けた]--> Idle（自動復帰。要件 10.5）
//   Tripped --[latching == true]--> Tripped（reset() が呼ばれるまで維持。
//              手動リセット。要件 10.5）
//
// 「継続時間を超える」の境界は、design.md の CommandWatchdog
// （`(now - last_command_at_ms) <= timeout_ms` のときのみ非発火、すなわち
// 厳密に超えたときに発火）と同じ規約に揃え、`elapsed > duration_ms`
// （厳密に大きい）を発火条件にする。
//
// ⚠️ 判定入力（要件 10.1 の設計上の落とし穴）: `commanded_duty` は
// 「出力上限を適用した後・遮断する前」の出力指令でなければならない
// （design.md "MotorLockDetector" ⚠️ 注記）。
//   - 遮断後の値（常に 0）で判定すると、発火した瞬間に
//     `|commanded_duty| >= duty_threshold` が偽になり、ロック状態が
//     その場で自己解除してしまう。
//   - 上限適用**前**の生の指令（PID の生出力等、群としての等比縮小や
//     PWM 上限より前の値）で判定すると、実際にはポートへ出ていない
//     指令を根拠に誤発火し得る。
// この2点から、判定は「上限適用の後・遮断の前」の1点でなければならない。
// この境界を守る責務は呼び出し側（制御ステップの出力段。タスク 6.3）に
// あり、本クラス自体は渡された値をそのまま絶対値で比較するだけである。
//
// ⚠️ ハードウェアヒューズを前提にしない（要件 10.7）: 基板のヒューズ
// （10A 級）は1モータのストール電流（約 2.3A）では溶断しない
// （design.md "MotorLockDetector" Risks、`docs/drivetrain-spec.md` §5）。
// 3モータが同時にストールしない限りヒューズは無関係であり、単一モータの
// ストール電流域では**この検出器がその領域の唯一の防護**である。
//
// reset() の意味論（設計判断）: `reset(TimeMs now)` は `commanded_duty` /
// `measured_mm_s` を引数に取らないため、呼ばれた時点でロック条件が
// まだ成立しているかどうかをこのクラス自身は再評価できない
// （design.md の Service Interface がそもそもこの2値を渡さない形で
// 宣言されている）。そのため `reset()` は**無条件に** Tripped を解除し
// Idle へ戻す。「解除条件を満たす保護だけを解く」というゲーティング
// （要件 14.6）は `ProtectionSupervisor::resetProtections()`
// （design.md "ProtectionSupervisor" Postconditions、タスク 5.5 の範囲）
// の責務であり、このクラスの責務ではない。呼び出し側が「まだロック条件が
// 続いている輪の reset() を呼ばない」ことを保証する必要がある。
//
// 判定は輪ごとに独立（要件 10.3）: このクラスは他輪の状態を一切
// 参照しない。3輪ぶんの独立は、呼び出し側（`ProtectionSupervisor` の
// `MotorLockDetector[wheel]`。design.md 上は「輪ごとに1個。合計3個を
// Supervisor が持つ」）がこのクラスを3個インスタンス化することで満たす。
//
// L7 は L0（units.hpp, errors.hpp）–L2（config.hpp）にのみ依存する
// （design.md "Dependency Direction"）。このファイルはビルド構成マクロ・
// ペリフェラル固有の型・現在時刻を取得する手段のいずれも参照しない。

#include <cstdint>

#include "drivetrain_control/config.hpp"

namespace drivetrain_control {

class MotorLockDetector {
 public:
  explicit MotorLockDetector(const LockParams& params) noexcept;

  // commanded_duty: 上限適用の後・遮断の前の出力指令（絶対値で判定する。
  //                 上記ファイル先頭の ⚠️ 注記を参照）。
  // measured_mm_s:  計測された輪の周速（絶対値で判定する）。
  // now:            単調増加する現在時刻。
  // 戻り値: 発火中（Tripped）なら true。
  bool update(float commanded_duty, float measured_mm_s, TimeMs now) noexcept;

  bool tripped() const noexcept;

  // 手動リセット（要件 10.5）。Tripped を無条件に解除し Idle へ戻す
  // （上記ファイル先頭の reset() の意味論を参照。ゲーティングは
  // 呼び出し側の責務）。
  void reset(TimeMs now) noexcept;

 private:
  enum class State : std::uint8_t { kIdle, kSuspect, kTripped };

  LockParams params_;
  State state_ = State::kIdle;

  // Suspect に入った時刻。Suspect 状態のときだけ意味を持つ
  // （Idle/Tripped では次に Suspect へ入った時点で上書きされる）。
  TimeMs suspect_started_at_ms_ = 0;

  // 自動復帰（latching == false）用: Tripped 中に条件が崩れ続けている
  // 時間を計るための起点と、その計測が有効かどうか。
  TimeMs clear_started_at_ms_ = 0;
  bool clear_timer_active_ = false;
};

}  // namespace drivetrain_control
