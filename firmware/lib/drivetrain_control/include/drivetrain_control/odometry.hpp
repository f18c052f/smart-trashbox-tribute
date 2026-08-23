#pragma once

// drivetrain_control Odometry (design.md L6 "Odometry", 要件 8.2, 8.3, 8.5,
// 8.6, 8.7).
//
// 各輪の周速（Kinematics::forward() が返す機体速度）を、機体座標系の変位
// として時間積分し、世界座標系の位置・姿勢を推定する。積分は**中点法**
// （機体座標系の変位を、積分前後の姿勢角の中点 `theta + dtheta/2` で世界
// 座標系へ回転させる）で行う。前進オイラーだと曲線走行のたびに系統的な
// ドリフトが乗り、それが「逆運動学のバグ」と区別できなくなる（→
// research.md「Decision: オドメトリの積分は中点法」。`teleop-bringup` の
// M2a-2 が手動で正方形を走らせて出発点との誤差を測るため、この区別できな
// さは Spec の存在理由と直接ぶつかる）。
//
// 位置は距離ミリメートル・時刻ミリ秒の単位で外部へ提供する（要件 8.5）。
// 姿勢角は `units::wrapAngle()` で `(-π, +π]` へ正規化して保持する（他の
// どのファイルにも角度正規化を重複実装しない）。
//
// reset() は原点と姿勢を外部からの操作で初期化する（要件 8.3）。原点は
// 「最後に reset() した時点」以上の意味を持たない。**外部座標系（World
// frame）への整合を行う責務はここには無い**（要件 8.7）。整合（例えば
// 実世界の基準点への合わせ込み）は上位（`m2-motion-validation` /
// `teleop-bringup`）の責務である。
//
// traveled_mm（累積走行距離）と since_reset_ms（初期化時点からの経過時間）
// は、ドリフト率（mm/m や mm/s）を上位が算出するための素材である。ここでは
// 素材を提供するだけで、ドリフト率そのものは計算しない（design.md
// Implementation Notes）。
//
// since_reset_ms の求め方: reset(pose, now) が呼ばれた時刻 `now` を
// 内部に保持し、以降の update(..., now) が呼ばれるたびに
// `now - <reset時のnow>` を DurationMs へ収めて since_reset_ms とする
// （dt_ms を毎ステップ積算する方式ではなく、reset 時刻からの経過を都度
// 実時刻差から求め直す方式を採る。丸め誤差が積算で育たず、design.md
// "DrivetrainController" が経過時間を実際の時刻差から求める方針
// （タスク 6.2）と一貫する）。
//
// Preconditions: dt_ms > 0（呼び出し側 `DrivetrainController::step()` が
// 過去時刻・非進行時刻を既に短絡済みで、この関数へは正の dt しか渡さない
// 前提。本関数自体は dt_ms を再検証しない）。
// Postconditions: 姿勢角は `(-π, +π]` に正規化される。積分は中点法。
// Invariants: 外部座標系（World frame）への整合を行わない。原点は「最後に
// reset() した時点」であり、それ以上の意味を持たない。
//
// L6 は L0（units.hpp, errors.hpp）–L5（kinematics.hpp）にのみ依存する
// （design.md "Dependency Direction"）。このファイルはビルド構成マクロ・
// ペリフェラル固有の型・現在時刻を取得する手段のいずれも参照しない
// （時刻は必ず引数として外部から与えられる）。

#include "drivetrain_control/kinematics.hpp"
#include "drivetrain_control/types.hpp"

namespace drivetrain_control {

struct OdometryState {
  Pose2D       pose{};              // 初期化時点の原点・姿勢からの推定値（要件 8.5, mm/rad）
  BodyVelocity body_velocity{};     // 直近ステップの機体速度（mm/s, rad/s）
  float        traveled_mm = 0.0f;  // 累積走行距離（要件 8.6）
  DurationMs   since_reset_ms = 0;  // 初期化時点からの経過時間（要件 8.6）
};

class Odometry {
 public:
  // Kinematics は構築後不変（→ kinematics.hpp Invariants）であり、
  // Odometry はそれを毎 update() で forward() するためだけに参照する。
  // Odometry 自身は Kinematics の所有権を持たない（呼び出し側の寿命が
  // Odometry のそれを包含する前提）。
  explicit Odometry(const Kinematics& kinematics) noexcept;

  // 原点と姿勢を初期化する（要件 8.3）。traveled_mm / since_reset_ms も
  // 0 へ戻す。以降の update() の since_reset_ms は、ここで渡された `now`
  // からの経過として求め直される。
  void reset(const Pose2D& pose, TimeMs now) noexcept;

  // 各輪の周速から機体速度を求め（kinematics.forward()）、中点法で世界
  // 座標系へ積分する。
  //
  // Preconditions: dt_ms > 0
  void update(const float wheel_mm_s[kWheelCount], DurationMs dt_ms, TimeMs now) noexcept;

  const OdometryState& state() const noexcept;

 private:
  const Kinematics& kinematics_;
  OdometryState state_{};
  TimeMs reset_at_ms_ = 0;
};

}  // namespace drivetrain_control
