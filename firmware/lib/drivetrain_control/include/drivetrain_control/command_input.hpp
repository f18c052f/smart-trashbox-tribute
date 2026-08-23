#pragma once

// drivetrain_control CommandInput (design.md L9 "CommandInput", 要件 5.1,
// 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.9, 6.5, 17.6).
//
// 3つの指令入口（機体速度指令・輪単体指令・出力許可）を受け、投入時点で
// 輪目標速度（WheelTargets）へ正規化して保持する。
//
// - `submit(BodyVelocityCommand)`: 運動上限（max_body_speed_mm_s /
//   max_body_omega_rad_s、それぞれ独立にクランプ）でクランプしたうえで
//   `Kinematics::inverse()` を通し、いずれかの輪速度が
//   `max_wheel_speed_mm_s` を超える場合は `scaleToLimit()` で方向を保った
//   まま全輪を等比縮小する（要件 6.5）。
// - `submit(WheelVelocityCommand)`: 逆運動学を経由せず、各輪を個別に
//   `[-max_wheel_speed_mm_s, +max_wheel_speed_mm_s]` へ飽和させる。1輪だけ
//   回す用途（M2a-0 の輪単体テスト）には保存すべき運動方向が存在しない
//   ため、`scaleToLimit()` のような等比縮小は掛けない（要件 5.8）。
// - どちらの入口も同じ `WheelTargets` に落ちる。**以降の層（保護①〜④・
//   PID）は指令の形も出自も知らない**。これにより輪単体指令にも保護が
//   同一適用されることが実装の注意深さではなく構造で成立する（要件 5.9）。
// - `setOutputEnabled()` は指令の投入と完全に独立した入口である。指令を
//   受け取ったこと自体は出力許可の条件にならない（要件 5.6, 5.7）。
//
// 過去時刻の拒否（要件 5.4 の一部、design.md "CommandInput" Risks）:
//   起動後まだ一度も有効指令が無ければ（`latched().valid == false`）、
//   最初の指令はその絶対時刻値によらず無条件で受理する（比較対象となる
//   直前指令が存在しないため）。2回目以降は
//   `command.issued_at_ms <= latched().issued_at_ms` のとき拒否する
//   （`<=`。同一時刻での上書きも拒否対象にする。design.md 本文はこの
//   境界を厳密には指定していないため、「同一時刻の指令を黙って上書き
//   しない」方向で `<=` を採用する）。拒否時は `latched()` を一切変更
//   しない（ウォッチドッグを欺けないようにするため）。
//
// L9 は L0（units.hpp, errors.hpp）–L5（kinematics.hpp）にのみ依存する
// （design.md "Dependency Direction"）。このファイルはビルド構成マクロ・
// ペリフェラル固有の型・現在時刻を取得する手段のいずれも参照しない。

#include "drivetrain_control/config.hpp"
#include "drivetrain_control/kinematics.hpp"
#include "drivetrain_control/types.hpp"

namespace drivetrain_control {

struct WheelTargets {
  float  mm_s[kWheelCount] = {0.0f, 0.0f, 0.0f};
  TimeMs issued_at_ms = 0;
  bool   valid = false;  // 起動後まだ一度も有効指令が無ければ false（要件 14.10）
};

class CommandInput {
 public:
  // Preconditions: `kinematics` が構築済みであること（Kinematics のコンス
  // トラクタは常に完全構築で完結するため、参照が有効である限り常に真）。
  // `kinematics` / `limits` の参照・値は呼び出し側が本オブジェクトの寿命の
  // 間保持する（`kinematics` は参照で保持するため、寿命は呼び出し側の
  // 責務）。
  CommandInput(const Kinematics& kinematics, const MotionLimits& limits) noexcept;

  // 機体速度指令（要件 5.1）。運動上限でクランプ → 逆運動学 → 輪速度上限
  // で等比縮小、の順で処理し、同じ WheelTargets へ落とす。
  CommandAcceptance submit(const BodyVelocityCommand& command) noexcept;

  // 輪単体指令（要件 5.8）。逆運動学を経由せず、各輪を個別に飽和させる。
  CommandAcceptance submit(const WheelVelocityCommand& command) noexcept;

  // 出力許可（要件 5.7）。指令の投入とは完全に独立しており、これ自体は
  // `latched()` を変更しない。
  void setOutputEnabled(bool enabled, TimeMs now) noexcept;

  const WheelTargets& latched() const noexcept { return latched_; }  // 要件 5.5
  bool outputEnabled() const noexcept { return output_enabled_; }
  bool lastCommandClamped() const noexcept { return last_command_clamped_; }  // 要件 5.4

 private:
  bool isStale(TimeMs issued_at_ms) const noexcept;

  const Kinematics& kinematics_;
  MotionLimits limits_;

  WheelTargets latched_{};
  bool output_enabled_ = false;
  bool last_command_clamped_ = false;
};

}  // namespace drivetrain_control
