#pragma once

// drivetrain_control Kinematics (design.md L5 "Kinematics", 要件 6.1, 6.2,
// 6.3, 6.4, 6.5, 6.6, 8.1, 8.4).
//
// 3輪オムニの逆運動学（機体速度 → 各輪の周速）と順運動学（各輪の周速 →
// 機体速度）を、構築時に1度だけ組み立てた3x3行列とその逆行列で表現する。
// 三角関数（sin/cos）の評価はコンストラクタの1回きりで完結し、制御ループが
// 呼ぶ inverse() / forward() は行列積（乗算・加算）だけを行う（要件 6.1,
// 8.1）。
//
// 行 i = [-sinα_i, cosα_i, R]（α_i は GeometryParams::wheel_angle_rad[i]、
// R は GeometryParams::base_radius_mm）。正の輪速度は機体を反時計回りの
// 接線方向へ駆動する。**輪番号と符号の対応を定義する唯一の場所はこの行定義
// である**（要件 6.6）。極性の吸収（A/B 逆結線・モータ逆結線）は
// EncoderParams::polarity / OutputParams::polarity がそれぞれ別の1箇所で
// 行い、ここでは扱わない。
//
// 3輪3自由度なので M は正方行列であり、順運動学は擬似逆行列ではなく厳密な
// 逆行列 M^-1 である。これにより forward(inverse(v)) が浮動小数点誤差の
// 範囲で v に構造的に一致する（要件 6.3, 8.4）。
//
// 上位の位置制御（目標座標から機体速度指令を求める処理）はここに含めない
// （要件 6.7 / タスク境界）。
//
// Preconditions: 構築に渡す GeometryParams は config.hpp の validate() を
// 通過済みであること（|det(M)| > ε）。本クラスはこれを再検証しない
// （config.cpp の非退化判定との二重実装を避けるため）。
// Postconditions: forward(inverse(v)) は v に一致する（浮動小数点許容差の
// 範囲、要件 6.3, 8.4）。純回転指令（vx = vy = 0）では3輪が同一符号・同一
// 大きさになる（要件 6.4）。
// Invariants: 行列は構築後に不変。制御ループで再構築しない。
//
// L5 は L0（units.hpp, errors.hpp）–L2（config.hpp）にのみ依存する
// （design.md "Dependency Direction"）。このファイルはビルド構成マクロ・
// ペリフェラル固有の型・現在時刻を取得する手段のいずれも参照しない。

#include "drivetrain_control/config.hpp"
#include "drivetrain_control/types.hpp"

namespace drivetrain_control {

class Kinematics {
 public:
  // validate() 済みの GeometryParams から構築する。退化配置は configure()
  // （config.cpp の validate()）が先に弾く前提であり、本コンストラクタは
  // 非退化性を再検証しない。sin/cos の評価はここで輪ごとに1回ずつ、合計
  // kWheelCount 回だけ行い、以降は呼ばない。
  explicit Kinematics(const GeometryParams& geometry) noexcept;

  // 逆運動学: 機体速度 → 各輪の周速（要件 6.1）。行列積のみ。
  void inverse(const BodyVelocity& body, float wheel_mm_s[kWheelCount]) const noexcept;

  // 順運動学: 各輪の周速 → 機体速度（要件 8.1）。行列積のみ。
  BodyVelocity forward(const float wheel_mm_s[kWheelCount]) const noexcept;

  // 逆運動学行列 M の行列式。config.cpp の validate() が退化判定に使う値と
  // 同一の定義（構築時に1度だけ計算してキャッシュする）。
  float determinant() const noexcept;

 private:
  float matrix_[kWheelCount][3];     // M。行 i = [-sinα_i, cosα_i, R]
  float inverse_matrix_[kWheelCount][3];  // M^-1（3x3 の厳密な逆行列）
  float determinant_;
};

// 方向を保ったまま複数の値を等比縮小する（要件 6.5, 12.4）。指令の輪速度
// にも出力デューティにも同じ関数を使う。
//   - values のうち絶対値が最大のものが limit を超えるとき、その最大値が
//     ちょうど limit（符号は保ったまま）になるよう全要素へ同一の係数を
//     掛ける（方向・比率を保つ）。
//   - 既に limit 以内なら値を変更しない。
// 戻り値は適用した縮小率（1.0 なら縮小なし）。
float scaleToLimit(float values[kWheelCount], float limit) noexcept;

}  // namespace drivetrain_control
