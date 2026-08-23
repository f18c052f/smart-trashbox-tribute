#pragma once

// drivetrain_control ProtectionSupervisor (design.md L7-L8 保護層
// "ProtectionSupervisor", 要件 14.1〜14.10).
//
// 保護①（MotorLockDetector×3）・②（LowVoltageProtector）・④
// （CommandWatchdog）の検出器を内部に保持し、「検出器を1回進める」呼び
// 出しと「直近の結果を遮断理由へ合成する」呼び出しを別メソッドへ分離する。
//
// ⚠️ 保護③（PwmCeiling）はここに含めない（design.md "PwmCeiling"
// Implementation Notes）。状態を持たない純関数であり、かつ上限値は PID の
// 算出直後・等比縮小に必要なため、`DrivetrainController` が直接保持して
// `Supervisor::compose()` より前に呼ぶ。
//
// Preconditions（design.md "ProtectionSupervisor" Preconditions）:
//   `now` は単調増加。1ステップにつき `updateLowVoltage()` /
//   `updateWatchdog()` / 輪ごとの `updateLock()` をそれぞれ厳密に1回呼んだ
//   後に `compose()` を呼ぶ。`compose()` はこれら3つの直近の結果を読む
//   だけであり、検出器を呼び直さない（内部の `MotorLockDetector::tripped()`
//   / `LowVoltageProtector::state()` / `CommandWatchdog::tripped()` という
//   読み取り専用アクセサだけを使う。これらは検出器の「1回だけ進める」契約
//   を破らない）。
//
// resetProtections() の設計判断（⚠️ 本 Spec で最も安全上重要な箇所。
// タスク 5.1/5.2 の Implementation Notes、および tasks.md の当該注記を
// 踏まえる）:
//   `MotorLockDetector::reset()` と `LowVoltageProtector::reset()` は
//   いずれも「無条件」実装であり（それぞれの reset() が現在の条件を
//   自身では再評価できないため。各ヘッダの reset() 意味論コメント参照）、
//   「解除条件を満たす保護だけを解く」（要件 14.6）というゲーティングは
//   本クラスの責務として明示的に委譲されている。
//
//   - MotorLockDetector（`config.lock.latching == true` の手動リセット
//     構成のみが対象。自動復帰構成は検出器自身が clear_duration_ms 経過後
//     に自己解除するため、ここで扱う必要が無い ―― design.md
//     Postconditions の「保持設定の保護のうち」という限定はこの区別を
//     指す）: `MotorLockDetector` は `tripped()` しか公開しておらず、
//     `latching == true` の Tripped 状態では `update()` 内部が
//     ロック条件の再評価そのものを行わない
//     （motor_lock.cpp: `if (params_.latching) { break; }`）。そのため
//     検出器の状態から「条件が今クリアされているか」を読み取る手段が
//     構造的に存在しない。そこで本クラスは、直近の `updateLock()` 呼び
//     出しで受け取った `commanded_duty` / `measured_mm_s` を用いて、
//     `MotorLockDetector` 内部と同一の判定式（`config.lock` の
//     `duty_threshold` / `speed_threshold_mm_s`）を独立に評価し、
//     輪ごとにキャッシュしておく（`last_lock_condition_[]`）。
//     `resetProtections()` は、当該輪が `tripped()` かつ直近のロック条件が
//     「もう成立していない」ときだけ `reset()` を呼ぶ。条件が継続してい
//     る輪の `reset()` は呼ばない。
//   - LowVoltageProtector（`config.low_voltage.latching == true` の手動
//     リセット構成のみが対象。同上の理由で自動復帰構成は対象外）:
//     `state()` / `averaged_milli_volts()` / `voltage_valid()` は読み取り
//     専用アクセサとして既に公開されているため、Tripped からの復帰条件
//     （移動平均が `recover_milli_volts` 以上）はこれらだけで判定できる。
//     Unavailable からの復帰条件（design.md の状態遷移図に `latching` の
//     条件は付いていない）は、加えて「直近のサンプルが有効だったこと」も
//     要求する（low_voltage.cpp の Unavailable 節が `sample.valid` を見て
//     いるため）。これは検出器のアクセサからは読み取れないので、直近の
//     `updateLowVoltage()` 呼び出しで受け取った `VoltageSample::valid` を
//     `last_voltage_sample_valid_` としてキャッシュし、判定に使う。
//   - CommandWatchdog は `reset()` を持たない（task 5.4 で確定済み）。
//     `has_command` / `last_command_at_ms` の新しい入力だけで自己解除する
//     ため、`resetProtections()` にはここで扱うべきものが無い。
//
// L8 は L0-L7（units.hpp, errors.hpp, types.hpp, config.hpp,
// 保護①②④の各ヘッダ）にのみ依存する（design.md "Dependency Direction"）。
// このファイルはビルド構成マクロ・ペリフェラル固有の型・現在時刻を取得
// する手段のいずれも参照しない。

#include <cstdint>

#include "drivetrain_control/config.hpp"
#include "drivetrain_control/protection/command_watchdog.hpp"
#include "drivetrain_control/protection/low_voltage.hpp"
#include "drivetrain_control/protection/motor_lock.hpp"
#include "drivetrain_control/types.hpp"

namespace drivetrain_control {

struct GateOutcome {
  BlockMask global_reasons = 0;
  BlockMask wheel_reasons[kWheelCount] = {0, 0, 0};
};

class ProtectionSupervisor {
 public:
  explicit ProtectionSupervisor(const DrivetrainConfig& config) noexcept;

  // 保護②。内部の LowVoltageProtector を1回進める（要件 11）。
  LowVoltageState updateLowVoltage(const VoltageSample& battery, TimeMs now) noexcept;

  // 保護④。内部の CommandWatchdog を1回進める（要件 13）。
  bool updateWatchdog(TimeMs now, TimeMs last_command_at_ms, bool has_command) noexcept;

  // 保護①。輪ごとに1回、内部の MotorLockDetector[wheel] を進める（要件 10）。
  // commanded_duty: 上限適用の後・遮断の前の出力指令。
  bool updateLock(std::uint8_t wheel, float commanded_duty, float measured_mm_s, TimeMs now) noexcept;

  // 直近の updateLowVoltage / updateWatchdog / updateLock の結果と、
  // configured / output_enabled / has_command を合成する。検出器は呼び
  // 直さない（読み取り専用アクセサだけを使う）。
  GateOutcome compose(bool configured, bool output_enabled, bool has_command) const noexcept;

  // 保持されている保護状態を解除する。解除条件を満たす保護のみが解ける
  // （要件 14.6。上記ファイル先頭の設計判断を参照）。
  void resetProtections(TimeMs now) noexcept;

  // 遮断値の適用。理由が立っている輪のデューティを 0 にする（要件 14.1,
  // 14.2）。物理的な遮断そのものはここに含まない ―― ポートへ渡す値として
  // 表現するだけである。
  static void applyGate(const GateOutcome& outcome, WheelOutputs& outputs) noexcept;

  // design.md の ProtectionSupervisor Service Interface（タスク 6.4 で
  // 追加・同期済み）に定義された読み取り専用の転送アクセサ。
  // `DrivetrainStatus::battery_milli_volts` / `battery_valid`（要件 17.7）
  // が要求する「平滑後の電圧と妥当性」は内部の `LowVoltageProtector
  // low_voltage_`（private）が保持しており、これを `DrivetrainController
  // ::status()` 用に外部へ転送するためだけに存在する。判定ロジックは
  // 持たず「保護の合成」ではなく「観測値の中継」に属する（design.md
  // Implementation Notes 参照）。検出器を進めない・状態を変えない点は
  // 他の読み取り専用アクセサ（`compose()` 等）と同じ。
  std::int32_t averagedBatteryMilliVolts() const noexcept { return low_voltage_.averaged_milli_volts(); }
  bool batteryVoltageValid() const noexcept { return low_voltage_.voltage_valid(); }

 private:
  // resetProtections() のゲーティングに使う設定値のキャッシュ
  // （config.lock / config.low_voltage の latching と閾値）。
  LockParams lock_params_;
  LowVoltageParams low_voltage_params_;

  MotorLockDetector motor_lock_[kWheelCount];
  LowVoltageProtector low_voltage_;
  CommandWatchdog watchdog_;

  // 直近の updateLock() で渡された引数から独立に評価した「ロック条件が
  // 今成立しているか」の輪ごとのキャッシュ。resetProtections() 専用
  // （ファイル先頭コメント参照）。
  bool last_lock_condition_[kWheelCount] = {false, false, false};

  // 直近の updateLowVoltage() で渡された VoltageSample::valid のキャッシュ。
  // resetProtections() の Unavailable 復帰判定専用（ファイル先頭コメント
  // 参照）。
  bool last_voltage_sample_valid_ = false;
};

}  // namespace drivetrain_control
