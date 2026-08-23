#pragma once

// drivetrain_control VelocityPid (design.md L6 "VelocityPid", 要件 9.1, 9.2,
// 9.3, 9.4, 9.5, 9.6).
//
// 1輪分の速度追従を、生の出力を返す算出（compute）と、実際に適用された値を
// 受け取って積分を確定させる確定（commit）の2段プロトコルに分ける。1段の
// PID に飽和値を渡す形にすると、3輪まとめての等比縮小（design.md
// "Design Decisions"、PWM 上限はデューティのベクトルを一様にスケールする）
// と、個々の輪のアンチワインドアップ対策が噛み合わない
// （design.md "Critical Issue 3" の是正）。
//
// - `compute(target, measured, dt_ms)`: 偏差 `target - measured` に比例・
//   積分を、**計測値の変化**に微分を掛けて生の出力を返す。上限でクランプ
//   しない。積分は `[-integral_limit, +integral_limit]` へクランプしつつ
//   このステップの増分を暫定的に積む（要件 9.6）。このステップの実際の
//   増分（クランプ後）を内部に保持し、直後の `commit()` が取り消せるように
//   しておく。
// - `commit(applied_duty)`: 等比縮小・遮断を経て実際にポートへ渡された値を
//   渡す。**適用値の大きさが生の出力の大きさより小さく、かつ偏差が飽和方向
//   （生の出力と同符号）を向いているとき**、そのステップで暫定的に積んだ
//   積分増分を取り消す（条件付き積分。要件 9.4）。これにより「縮小によって
//   実現されなかった分」を積分へ積算し直さない。
// - `holdReset(measured_mm_s)`: 出力が遮断されている間、compute/commit の
//   代わりに毎ステップ呼ぶ。積分を 0 に保ち、微分の基準（前回計測値）を
//   現在の計測値へ追従させる。これにより遮断解除直後の最初の compute() が、
//   遮断中に古いままだった計測値との差分からくる過大な微分キックを起こさ
//   ない（要件 9.5）。
//
// Preconditions: `dt_ms > 0`。`compute()` の直後に必ず `commit()` を呼ぶ
// （`holdReset()` を呼んだステップでは compute/commit のどちらも呼ばない。
// 同一の「論理ステップ」内で混在させない）。この事前条件は呼び出し側
// （制御ステップの出力段。タスク 6.3）が保証し、本クラス自体は再検証しない
// （他の L6 コンポーネントと同じ方針。odometry.hpp 参照）。
//
// Invariants: **時刻を自分で取得しない**（要件 9.3）。経過時間は `dt_ms`
// として外部から与えられる。積分項は `float`（binary32、ホストと ESP32 で
// 同一表現）で保持し、発散は型幅ではなく `integral_limit` が封じる
// （`integral_limit` が有限であることは設定検証 `config.cpp` が強制する）。
//
// L6 は L0（units.hpp, errors.hpp）–L2（config.hpp）にのみ依存する
// （design.md "Dependency Direction"）。このファイルはビルド構成マクロ・
// ペリフェラル固有の型・現在時刻を取得する手段のいずれも参照しない。

#include "drivetrain_control/config.hpp"

namespace drivetrain_control {

class VelocityPid {
 public:
  explicit VelocityPid(const PidParams& params) noexcept;

  // 生の出力を返す。上限でクランプしない。群としての等比縮小は呼び出し側
  // （制御ステップ）が行う。
  //
  // Preconditions: dt_ms > 0。直後に必ず commit() を呼ぶこと。
  float compute(float target_mm_s, float measured_mm_s, DurationMs dt_ms) noexcept;

  // 等比縮小と遮断を経て実際に適用された値を渡す。適用値の大きさが直前の
  // compute() の生の出力の大きさより小さく、かつ偏差が飽和方向（生の出力と
  // 同符号）を向いているとき、そのステップの積分増分を取り消す
  // （条件付き積分。要件 9.4）。
  void commit(float applied_duty) noexcept;

  // 出力が遮断されている間、compute()/commit() の代わりに毎ステップ呼ぶ
  // （要件 9.5）。積分を 0 に保ち、微分の基準を現在の計測値へ追従させる。
  void holdReset(float measured_mm_s) noexcept;

  float integral() const noexcept { return integral_; }  // テスト・診断用

 private:
  PidParams params_;

  float integral_ = 0.0f;         // 確定済みの積分値（クランプ済み）
  float last_measured_mm_s_ = 0.0f;  // 微分の基準（前回計測値）

  // compute() と commit() の間だけ有効な、このステップの暫定状態。
  // compute() が書き、直後の commit() だけが読んで消費する（要件 9.4 の
  // 「条件付き積分」を実現するための遷移状態）。
  float pending_integral_increment_ = 0.0f;  // クランプ後、このステップで実際に integral_ へ積んだ量
  float pending_raw_output_ = 0.0f;          // compute() が返した生の出力（大きさ比較の基準）
  float pending_error_ = 0.0f;               // compute() 時点の target - measured（飽和方向の判定用）
};

}  // namespace drivetrain_control
