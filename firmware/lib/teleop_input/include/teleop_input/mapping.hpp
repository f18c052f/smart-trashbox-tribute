#pragma once

// teleop_input: 変換パラメータ（teleop-bringup タスク 4.1、要件 8.6-8.9）。
//
// 本ヘッダは「パッド軸から指令への変換をどう調整できるようにするか」
// （`MappingParams`）だけを持つ。パッド状態そのものは `pad_state.hpp`
// （`PadState`）が持つ。パッド状態から指令への実際の変換（`mapPad()`）と、
// その結果を表す `MappedCommand`（`drivetrain_control` の指令型を包む）は
// **タスク 4.2 が本ファイルへ追加する**。本タスクは設定値の型のみを
// 対象とし、判断・計算のロジックを一切持たない（design.md teleop_input の
// Responsibilities & Constraints 「判定・遮断を持たない」と同じ理由で、
// 本タスク時点では変換そのものも持たない）。
//
// ⚠️ 各フィールドの意味は要件の出典どおり:
//   - deadzone         : スティック中立付近の無効範囲（要件 8.6）
//   - curve_exponent    : 中立付近の分解能を高める変換特性の指数
//                         （要件 8.7）。既定値 1.0 は線形（等倍）を表す
//   - speed_scale       : 機体の能力に対して絞る速度上限の割合
//                         （要件 8.8）。(0, 1] の範囲を前提とする
//   - max_body_mm_s     : 並進速度指令の上限（機体能力に対する絶対値側）
//   - max_omega_rad_s   : 回転速度指令の上限（同上）
//
// ⚠️ **すべて実走しながら調整できる値として保持する（要件 8.9）。**
// 定数ではなく設定可能なフィールドとして持つのはこのためである。実走中に
// 値を書き換える経路（制御ループからの参照・更新）はタスク 6.2 の責務で
// あり、本タスクは「調整可能な形」＝独立したフィールドを持つ構造体を
// 提供するところまでを担う。
//
// ⚠️ 変換式の具体形（カーブの関数形そのもの）は design フェーズでも
// 決めていない（design.md "design フェーズで決めるもの"）。OQ-17 として
// M2a で実走しながら決める。設計時に固定値を埋め込まない
// （design.md teleop_input Implementation Notes "Risks"）。

namespace teleop_input {

// 無効範囲・変換特性・速度上限。すべて実走中に調整できる（要件 8.9）。
struct MappingParams {
  float deadzone = 0.0f;         // 要件 8.6
  float curve_exponent = 1.0f;   // 要件 8.7
  float speed_scale = 0.0f;      // 要件 8.8。機体能力に対する割合
  float max_body_mm_s = 0.0f;
  float max_omega_rad_s = 0.0f;
};

}  // namespace teleop_input
