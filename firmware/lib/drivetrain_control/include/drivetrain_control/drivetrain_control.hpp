#pragma once

// drivetrain_control public entry header (design.md L11 "PublicApi", 要件
// 6.7, 17.5, タスク 6.5).
//
// 下流 Spec（`teleop-bringup` / `m2-motion-validation`）が参照する唯一の
// ヘッダ。下流はこのヘッダだけを include する。以下を再エクスポートする
// （design.md "PublicApi" を正確に踏襲。列挙順もそこに合わせてある）:
//
// - 型: TimeMs / DurationMs / BodyVelocityCommand / WheelVelocityCommand /
//   WheelDutyCommand / ControlPath / CommandAcceptance / WheelOutputs /
//   VoltageSample / EncoderCounts / Pose2D / BodyVelocity / BlockReason /
//   BlockMask / StepResult / DrivetrainStatus / OdometryState /
//   LowVoltageState
// - ポート: EncoderPort / MotorOutputPort / BatteryVoltagePort / Ports
// - 設定: DrivetrainConfig と各 *Params / ConfigError / ConfigField /
//   ConfigDiagnostic / validate
// - 部品: WrapAccumulator / VoltageScaler / Kinematics / DrivetrainController
//
// これらはすべて types.hpp / ports.hpp / config.hpp / errors.hpp /
// wrap_accumulator.hpp / voltage_scaler.hpp / kinematics.hpp /
// controller.hpp が既に定義している。本ヘッダはそれらを #include するだけ
// であり、新しい宣言・別名（namespace エイリアス等）を一切追加しない
// （drivetrain_control:: の中で定義済みのシンボルをそのまま可視にするのが
// 「再エクスポート」の実体である）。
//
// DrivetrainStatus / OdometryState / LowVoltageState の所在について:
//   DrivetrainStatus は controller.hpp（L10）が定義する（odometry.hpp の
//   OdometryState / protection/low_voltage.hpp の LowVoltageState をメンバ
//   に持つため、types.hpp（L1）には置けない ―― design.md "Dependency
//   Direction"）。controller.hpp は DrivetrainStatus の定義に必要な
//   odometry.hpp / protection/low_voltage.hpp を既にそれ自身の実装上の
//   都合で include しているため、OdometryState / LowVoltageState は
//   controller.hpp を include するだけで透過的に可視になる。本ヘッダは
//   odometry.hpp / protection/*.hpp を自分の include リストへ加えない
//   （下記「再エクスポートしないもの」参照）。
//
// submit() 等の可用性について:
//   DrivetrainController::submit() / setOutputEnabled() / resetProtections()
//   / resetOdometry() は「部品」として一覧された DrivetrainController
//   自身のメンバであり、DrivetrainController が再エクスポートされていれば
//   個別に列挙するまでもなく到達可能である（design.md はメソッド単位では
//   なく型単位で再エクスポート対象を列挙している）。
//
// WheelDutyCommand / ControlPath について（要件 5.10, 5.15、タスク 10.3。
// 2026-08-24 追加）:
//   両方とも types.hpp（L1）が定義しており、本ヘッダは types.hpp を元々
//   #include している（上記リスト参照）ため、include リストへの変更は
//   不要だった（すでに直接可視だった）。本タスクの変更は、この2つの型が
//   意図して公開面へ含まれることを上記「型:」一覧へ明記することのみ
//   ―― 未列挙のまま推移的に見えていた状態から、契約として明文化された
//   状態への変更である。DrivetrainController::submit(const
//   WheelDutyCommand&) は上記と同じ理由（DrivetrainController 自身の
//   メンバ）で個別列挙なしに到達可能。DrivetrainStatus::control_path
//   （ControlPath 型）で経路を判別できる（要件 5.15。
//   test/native/test_public_api/test_public_api.cpp の
//   test_public_api_open_loop_command_reports_control_path 参照）。
//
// ⚠️ 再エクスポートしないもの（design.md "PublicApi"）:
//   Odometry / VelocityPid / 保護4部品（MotorLockDetector /
//   LowVoltageProtector / PwmCeiling / CommandWatchdog） /
//   ProtectionSupervisor / CommandInput はいずれも DrivetrainController の
//   内部構造であり、下流が直接組み立てる対象ではない。これらの個別ヘッダ
//   （odometry.hpp / velocity_pid.hpp / protection/*.hpp /
//   command_input.hpp）を本ヘッダは include しない。controller.hpp が
//   自身の実装上の都合でこれらを推移的に include すること自体は避けられ
//   ないが（DrivetrainController が内部にこれらの合成メンバを持つため）、
//   本ヘッダ自身の include リストにこれらを加えることはしない（テストは
//   内部構造を検証する場合、個別ヘッダを直接 include する。
//   test/native/test_odometry, test_velocity_pid,
//   test_protection_lock/low_voltage/pwm_ceiling/watchdog/supervisor,
//   test_command_input 参照）。
//
// ⚠️ 上位の位置制御・翻訳処理・無線関連は公開面に一切含めない（要件 6.7,
// 17.2）。目標座標から機体速度指令を求める処理、実測値から
// trajectory_sim.DrivetrainParams への翻訳、無線・コントローラ・通信は
// いずれも本 Spec の責務外であり、本ヘッダにも一切現れない。
//
// 後続 Spec がポートを実装するために必要な契約（要件 17.5）は、実装を伴わ
// ずにここまでの再エクスポートだけで揃っている: EncoderPort /
// MotorOutputPort / BatteryVoltagePort（純粋仮想の契約）、DrivetrainConfig
// とその検証（validate()）、WrapAccumulator / VoltageScaler（ポート実装が
// 使うべき核ロジック）、そして DrivetrainController（configure() /
// submit() / setOutputEnabled() / step() / status() の一連）。

#include "drivetrain_control/config.hpp"
#include "drivetrain_control/controller.hpp"
#include "drivetrain_control/errors.hpp"
#include "drivetrain_control/kinematics.hpp"
#include "drivetrain_control/ports.hpp"
#include "drivetrain_control/types.hpp"
#include "drivetrain_control/voltage_scaler.hpp"
#include "drivetrain_control/wrap_accumulator.hpp"
