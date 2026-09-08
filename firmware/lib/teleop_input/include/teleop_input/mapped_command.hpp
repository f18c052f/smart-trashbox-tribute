#pragma once

// teleop_input: パッド状態から指令への変換（teleop-bringup タスク 4.2、
// 要件 8.1, 8.2, 8.3, 8.4, 8.5, 11.1, 17.1, 17.2）。
//
// 本ヘッダは `PadState`（`pad_state.hpp`）と `MappingParams`（`mapping.hpp`）
// を受け取り、`drivetrain_control` の指令型を包む `MappedCommand` へ変換する
// 純関数 `mapPad()` を宣言する。design.md teleop_input の Service Interface
// が定めた形そのもの。
//
// ⚠️ **`mapping.hpp` / `pad_state.hpp`（タスク 4.1 の出荷物）を変更しない。**
// `MappedCommand` / `mapPad` は両ファイルのコメントが予告していた場所
// （`mapping.hpp`）ではなく、新設した本ファイルに置く。フィールド定義済みの
// 2ファイルを不変に保つ（親コントローラの境界指定）。
//
// ⚠️ **判定・遮断を持たない。** `output_enabled` は「デッドマンの押下状態を
// そのまま真偽として返すだけ」であり、遮断（実際にモータを止めるか）は
// `drivetrain_control`（下流、タスク 6.2 が結線）が行う（design.md
// teleop_input Responsibilities & Constraints）。
//
// ⚠️ **`board_pins::kNoWheel` を使わない。** それは PinRole→輪添字という
// 別ドメインの `std::uint8_t` 定数であり、型（`PadState::selected_wheel` は
// `std::int8_t`）も意味（機体走行/輪単体の選択）も異なる。加えて
// design.md の teleop_input Dependencies は Outbound を
// `drivetrain_control` の型のみに限定しており（`board_pins` を含まない）、
// 依存すると "teleop_input はペリフェラルを知らない" 境界（design.md
// "Dependency Direction"）を破る。`PadState` 自身が既に文書化している
// 既定値 -1（`pad_state.hpp` "selected_wheel の既定値は -1（機体走行）"）を
// そのままこの名前空間内のローカル定数として使う。

#include "drivetrain_control/drivetrain_control.hpp"
#include "teleop_input/mapping.hpp"
#include "teleop_input/pad_state.hpp"

namespace teleop_input {

// `PadState::selected_wheel` が機体走行を表す値。`pad_state.hpp` が既定値
// として既に定めている -1 と同じ意味を、この名前空間内で参照可能にする
// ためのローカル定数（board_pins には依存しない）。
inline constexpr std::int8_t kBodyDriveSelection = -1;

// `mapPad()` の変換結果。design.md teleop_input Service Interface のとおり:
//   - output_enabled : デッドマンの押下状態をそのまま返す真偽（8.1, 8.2）
//   - wheel_scoped    : 輪単体テストが選ばれているか（8.5, 11.1）
//   - body            : 機体走行の指令（8.3, 8.4）。wheel_scoped のときは
//                        ゼロのまま（排他性）
//   - wheel           : 輪単体の指令（8.5）。body 走行のときはゼロのまま
//                        （排他性）
//
// デフォルト構築ですべてゼロ／偽になる（`output_enabled=false`,
// `wheel_scoped=false`, `body`/`wheel` は `drivetrain_control` 側の既定値
// である全ゼロ）。要件 8.2 の厳密な事後条件
// （デッドマンが離された場合は指令値がすべてゼロ）を、`mapPad()` が
// この既定構築のまま返すことで満たす。
struct MappedCommand {
  bool output_enabled = false;
  bool wheel_scoped = false;
  drivetrain_control::BodyVelocityCommand body{};
  drivetrain_control::WheelVelocityCommand wheel{};
};

// パッド状態 `pad` と変換パラメータ `params` から `MappedCommand` を求める
// 純関数。副作用を持たず、同じ入力には常に同じ出力を返す。
//
// - 8.1, 8.2: `output_enabled` は `pad.deadman` をそのまま返す。`false` の
//   ときは `body` / `wheel` を含めすべてゼロのまま返す（判定・遮断はしない。
//   実際にモータを止める判断は下流の責務）。
// - 8.3, 8.4: 機体走行時（`wheel_scoped == false`）、左スティック
//   （`left_x`, `left_y`）を並進指令（`vy_mm_s`, `vx_mm_s`）へ、右スティック
//   の横方向（`right_x`）を回転指令（`omega_rad_s`）へ対応付ける。
// - 8.5, 11.1: `pad.selected_wheel != kBodyDriveSelection` のとき
//   `wheel_scoped = true` とし、`pad.left_y` を選択輪単体の速度指令へ
//   対応付ける。`body` はゼロのまま（機体走行と輪単体選択は排他）。
// - 8.6: `params.deadzone` の内側は出力ゼロ。
// - 8.7: `params.curve_exponent` によるサイン付きべき乗カーブで、中立付近の
//   分解能を高める（既定値 1.0 は線形）。
// - 8.8: `params.speed_scale`（機体能力に対する割合、(0, 1] を前提）を
//   `params.max_body_mm_s` / `params.max_omega_rad_s` に掛けた値を上限とする。
MappedCommand mapPad(const PadState& pad, const MappingParams& params,
                     drivetrain_control::TimeMs now) noexcept;

}  // namespace teleop_input
