// teleop_input::mapPad の実装（teleop-bringup タスク 4.2、要件 8.1, 8.2,
// 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 11.1, 17.1, 17.2）。
//
// ⚠️ 判定・遮断ロジックを一切持たない。ここにあるのは「軸の値をどう指令へ
// 形作るか」（無効範囲・変換特性・速度上限の適用）だけであり、それが安全か
// どうかの判断（実際にモータを止める・保護を発火する等）は一切行わない
// （design.md teleop_input Responsibilities & Constraints、タスク 4.1 の
// スコープ注記）。

#include "teleop_input/mapped_command.hpp"

#include <cmath>

namespace teleop_input {
namespace {

// スティック軸1本ぶんの無効範囲・変換特性を適用し、[-1, +1] の「単位指令」
// を返す（速度上限の適用は呼び出し側 `ApplyCeiling` が担う。責務を分ける
// ことで、無効範囲・変換特性・速度上限それぞれの効果を独立に検証できる
// ようにしてある = 要件 17.1, 17.2 の前提）。
//
//   1. 入力を [-1, +1] へ防御的にクランプする（`PadState` はこの範囲に
//      正規化済みという前提だが、純関数として入力レンジを自ら保証する）。
//   2. 要件 8.6: `deadzone` 以下の大きさは無効域としてゼロを返す。
//   3. 要件 8.7: 無効域の外側は `(|x| - deadzone) / (1 - deadzone)` で
//      [0, 1] へ再スケールしたうえで `curve_exponent` 乗するサイン付き
//      べき乗カーブ（"expo" カーブ）を適用する。`curve_exponent > 1` では
//      原点付近の傾きが小さくなり（導関数が 0 に近づく）、スティックを
//      中立からわずかに動かした範囲に出力の変化量が集まる = 中立付近の
//      分解能が高まる。`curve_exponent == 1.0`（既定）は等倍（線形）。
//      無効域の境界（|x| == deadzone）で出力 0、フルデフレクション
//      （|x| == 1）で出力 ±1 になるよう再スケールしているため、`deadzone`
//      を変えても最大出力（=速度上限に達するスティック位置）が
//      フルデフレクションのまま保たれる。
//
//   `deadzone` は事前条件により非負（design.md teleop_input
//   Preconditions）。`deadzone >= 1.0` の場合は大きさ（|x|<=1）が常に
//   `deadzone` 以下になるため、除算に至らず常にゼロを返す（0除算・負の
//   再スケール分母を踏まない）。
float ShapeAxis(float raw, float deadzone, float curve_exponent) noexcept {
  float clamped = raw;
  if (clamped > 1.0f) {
    clamped = 1.0f;
  } else if (clamped < -1.0f) {
    clamped = -1.0f;
  }

  const float magnitude = std::fabs(clamped);
  if (magnitude <= deadzone) {
    return 0.0f;
  }

  const float denom = 1.0f - deadzone;  // magnitude <= deadzone で抜けているため denom > 0
  const float rescaled = (magnitude - deadzone) / denom;
  const float shaped = std::pow(rescaled, curve_exponent);
  return std::copysign(shaped, clamped);
}

// 要件 8.8: 単位指令（[-1, +1]）へ「機体能力に対する割合」（`speed_scale`）
// と「機体能力そのもの」（`max_value`、`max_body_mm_s` または
// `max_omega_rad_s`）を掛け、絞った上限を持つ実指令値へ変換する。
// `speed_scale` は事前条件により (0, 1] を前提とする（design.md teleop_input
// Preconditions）。
float ApplyCeiling(float shaped_unit, float speed_scale, float max_value) noexcept {
  return shaped_unit * speed_scale * max_value;
}

}  // namespace

MappedCommand mapPad(const PadState& pad, const MappingParams& params,
                     drivetrain_control::TimeMs now) noexcept {
  MappedCommand out{};  // 既定構築 = output_enabled(false) / wheel_scoped(false) / body・wheel 全ゼロ

  // 要件 8.1, 8.2: 出力許可はデッドマンの押下状態をそのまま返す真偽であり、
  // 遮断の判断はしない。離されている場合はここで抜け、`out` を既定構築の
  // まま（= 指令値すべてゼロ）返すことで要件 8.2 の厳密な事後条件を満たす。
  out.output_enabled = pad.deadman;
  if (!pad.deadman) {
    return out;
  }

  // 要件 8.5, 11.1: `selected_wheel` が機体走行を表す値（`kBodyDriveSelection`
  // = -1）でなければ輪単体選択とみなす。機体走行と輪単体選択は排他
  // （一方の分岐でしか `body` / `wheel` を populate しない。もう一方は
  // `out` の既定構築のゼロのまま）。
  const bool wheel_scoped = (pad.selected_wheel != kBodyDriveSelection);
  out.wheel_scoped = wheel_scoped;

  if (wheel_scoped) {
    // 台上での輪単体テスト（要件 8.5, 11.1）。単一の軸で選択輪の速度を
    // 指令する。機体走行時に前後方向の並進（vx）を担う `left_y`
    // （スロットルに相当する軸）をそのまま流用する ―― design.md は
    // 輪単体テストに使う軸そのものを固定していない（OQ-17 として M2a で
    // 実走しながら決める、design.md teleop_input Implementation Notes
    // "Risks"）ため、既存の軸から最も自然な対応を選んだという実装判断
    // である。
    const float shaped = ShapeAxis(pad.left_y, params.deadzone, params.curve_exponent);
    const float wheel_speed_mm_s = ApplyCeiling(shaped, params.speed_scale, params.max_body_mm_s);

    // `selected_wheel` は [0, kWheelCount) の範囲だけが実在の輪を指す
    // （`pad_state.hpp` "0..2 は輪単体"）。範囲外の値が来た場合は配列境界を
    // 守るため輪指令を populate しない（`wheel_scoped` は真のまま =
    // 機体走行へは戻らない。これは判定・遮断ではなく配列アクセスの安全性
    // のためのガードである）。
    if (pad.selected_wheel >= 0 &&
        pad.selected_wheel < static_cast<std::int8_t>(drivetrain_control::kWheelCount)) {
      out.wheel.wheel_mm_s[static_cast<std::size_t>(pad.selected_wheel)] = wheel_speed_mm_s;
      out.wheel.issued_at_ms = now;
    }
    // `out.body` はゼロのまま（既定構築）。
  } else {
    // 機体走行（要件 8.3, 8.4）。左スティック（left_x, left_y）を並進へ、
    // 右スティックの横方向（right_x）を回転へ対応付ける。`PadState` の
    // コメントが定める軸の向き（前方 +x、左方 +y、反時計回りが +omega）と
    // `drivetrain_control::BodyVelocityCommand` のコメントが定める向きが
    // 一致するように対応付ける。
    const float vx_shaped = ShapeAxis(pad.left_y, params.deadzone, params.curve_exponent);
    const float vy_shaped = ShapeAxis(pad.left_x, params.deadzone, params.curve_exponent);
    const float omega_shaped = ShapeAxis(pad.right_x, params.deadzone, params.curve_exponent);

    out.body.vx_mm_s = ApplyCeiling(vx_shaped, params.speed_scale, params.max_body_mm_s);
    out.body.vy_mm_s = ApplyCeiling(vy_shaped, params.speed_scale, params.max_body_mm_s);
    out.body.omega_rad_s = ApplyCeiling(omega_shaped, params.speed_scale, params.max_omega_rad_s);
    out.body.issued_at_ms = now;
    // `out.wheel` はゼロのまま（既定構築）。
  }

  return out;
}

}  // namespace teleop_input
