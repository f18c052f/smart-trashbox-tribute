#pragma once

// drivetrain_control config parameters and validation (design.md L2
// "DrivetrainConfig", 要件 6.2, 7.6, 7.7, 9.2, 10.2, 11.6, 12.2, 13.3,
// 15.1, 15.2, 15.3, 15.4, 15.6).
//
// 機体寸法・エンコーダ分解能とホイール径・出力極性・運動上限・制御ゲイン・
// 保護①〜④の閾値と継続時間・電圧換算テーブルを、それぞれ構造体として定義
// する。本ファイルは値を持たない。
//
// ⚠️ 数値パラメータに既定値を与えない（要件 15.3）。既定値を持つのは
// 振る舞いを選ぶ真偽値・列挙（`latching` / `enabled`）だけであり、これらは
// 必須性能でも達成済み性能でもない。実機の性能値（最高速度・加速度等）を
// このファイルは内にも外にも持たない（要件 15.2）。
//
// L2 は L0（units.hpp, errors.hpp）と L1（types.hpp）にのみ依存する
// （design.md "Dependency Direction"）。
//
// ⚠️ ゼロ初期化（`DrivetrainConfig config{};`）された設定は validate() が
// 必ず拒否する。数値メンバに既定初期化子を与えていないため、`{}` は
// 各数値メンバを 0 へ値初期化し、`base_radius_mm == 0` 等の正値性検証に
// 必ず引っかかる（design.md "DrivetrainConfig" Implementation Notes
// Risks）。「未設定のまま動いてしまう」経路をこれで塞ぐ。

#include <cstdint>

#include "drivetrain_control/errors.hpp"
#include "drivetrain_control/types.hpp"

namespace drivetrain_control {

struct GeometryParams {                // 要件 6.2, 6.6
  float wheel_angle_rad[kWheelCount];  // 機体 +x から反時計回りに測った各輪の取付角
  float base_radius_mm;                // 機体中心から各輪までの距離

  // 120° 等配置を安全に組み立てる補助。sin / cos の手書きを設計上禁止する
  // （要件 15.6）。戻り値そのものが「値」ではなく、呼び出し側が渡した
  // base_radius_mm / first_wheel_angle_rad を組み立てるだけの純関数である
  // ことに注意（このファイルが値を持つわけではない）。
  static GeometryParams equilateral(float base_radius_mm, float first_wheel_angle_rad) noexcept;
};

struct EncoderParams {               // 要件 7.6, 7.7
  std::int32_t counts_per_wheel_rev; // 出力軸1回転あたり。M2a-0 の実測校正値
  float wheel_diameter_mm;
  std::int32_t raw_modulus;          // ハードウェアカウンタの法（例: 65536）
  std::int8_t polarity[kWheelCount]; // +1 / -1。A/B 逆結線の吸収（要件 6.6）
};

struct OutputParams {
  std::int8_t polarity[kWheelCount]; // +1 / -1。モータ極性逆結線の吸収（要件 6.6）
  float absolute_max_duty;           // <= 1.0（要件 12.3）
};

struct MotionLimits {               // 要件 5.4, 6.5, 17.1
  float max_body_speed_mm_s;        // trajectory_sim.DrivetrainParams.max_speed_mm_s と同一定義
  float max_body_omega_rad_s;
  float max_wheel_speed_mm_s;
};

struct PidParams {          // 要件 9.2, 9.4, 9.6
  float kp;
  float ki;
  float kd;
  float integral_limit;     // 積分項の絶対値上限。有限であることを検証で強制する
};

struct LockParams {                // 要件 10.2, 10.5
  float duty_threshold;            // これ以上の出力指令で
  float speed_threshold_mm_s;      // これ以下の計測速度が
  DurationMs duration_ms;          // この時間続いたら発火（→ OQ-15 の運用は latching で選ぶ）
  DurationMs clear_duration_ms;    // 自動復帰時、条件解除がこの時間続けば復帰
  bool latching = true;            // true: 手動リセット / false: 自動復帰。
                                    // 振る舞いの選択であり、必須性能でも
                                    // 達成済み性能でもない（要件 15.3）。
};

struct LowVoltageParams {                // 要件 11.6
  std::int32_t warn_milli_volts;
  std::int32_t stop_milli_volts;
  std::int32_t recover_milli_volts;      // > stop_milli_volts を検証で強制（要件 11.4）
  std::uint8_t average_window;           // 移動平均のサンプル数。1..kMaxVoltageWindow
  DurationMs stop_duration_ms;
  DurationMs unavailable_duration_ms;    // 要件 11.8
  bool latching = true;                  // 振る舞いの選択（要件 15.3）
};

struct VoltageScalerParams {  // 要件 11.7
  struct Point {
    std::int32_t raw;
    std::int32_t milli_volts;
  };
  Point table[kMaxVoltagePoints];
  std::uint8_t point_count;  // 2 以上。2 点なら分圧比のみの線形換算に縮退する
};

struct PwmCeilingParams {              // 要件 12.2, 12.5, 12.6
  bool enabled = true;                 // 振る舞いの選択（要件 15.3）
  std::int32_t reference_milli_volts;  // 上限算出の基準電圧
  float fallback_duty;                 // 読み値が得られないときの安全側既定上限（要件 12.6）
  // 差し替え用。nullptr なら組み込みの式を使う（要件 12.5）。これも
  // 振る舞い（式を差し替えるか否か）の選択であり、性能値ではない。
  float (*override_fn)(std::int32_t measured_milli_volts, const PwmCeilingParams&) = nullptr;
};

struct WatchdogParams {  // 要件 13.3
  DurationMs timeout_ms;
};

struct DrivetrainConfig {
  GeometryParams geometry;
  EncoderParams encoder;
  OutputParams output;
  MotionLimits limits;
  PidParams pid[kWheelCount];
  LockParams lock;
  LowVoltageParams low_voltage;
  VoltageScalerParams voltage_scaler;
  PwmCeilingParams pwm_ceiling;
  WatchdogParams watchdog;
};

// 全パラメータを検証し、最初に見つかった違反を ConfigDiagnostic として返す
// （すべて満たされれば ok() が真、要件 15.4）。例外を投げない。
//
// 判定する項目（design.md "DrivetrainConfig" Implementation Notes）:
// 有限性・正値性・定義域（デューティは [0, 1]）・順序関係（recover > stop、
// warn >= stop 等）・幾何の非退化（|det(M)| > ε）・電圧テーブルの単調性と
// 点数・移動平均窓の上限・polarity が ±1 であること・integral_limit が
// 有限であること。
ConfigDiagnostic validate(const DrivetrainConfig& config) noexcept;

}  // namespace drivetrain_control
