#pragma once

// teleop_input: 正規化済みパッド状態（teleop-bringup タスク 4.1、要件 8.6-8.9）。
//
// 本ヘッダは「操作者の入力を正規化した形でどう表すか」（`PadState`）だけを
// 持つ。無効範囲・変換特性・速度上限といった変換パラメータは
// `mapping.hpp`（`MappingParams`）が持ち、パッド状態から指令への実際の
// 変換（`mapPad()`）と、その結果を表す `MappedCommand` はタスク 4.2 が
// `mapping.hpp` へ追加する。本タスクは型のみを対象とする。
//
// ⚠️ **Bluepad32 の型を一切参照しない。** DualSense の生値を本型へ正規化
// するのは `ControllerLink`（タスク 5.2）だけであり、それが「唯一
// Bluepad32 の型に触れる場所」になる（design.md teleop_input
// Implementation Notes）。本コンポーネントがコントローラ固有の事情を
// 一切知らないことが、要件 17.1/17.2（実機を用いない検証）を成り立たせる
// 前提である。
//
// ⚠️ ペリフェラルの API（`driver/*.h` 等）も一切参照しない。これにより
// ホスト（`[env:native]`）と実機（`[env:teleop]`）の双方で同一のソースが
// コンパイルできる。`firmware/src/` は `test_build_src` 既定 `no` により
// ホストビルドに含まれないため、この型をアプリ層へ置くとホストから
// 参照できず、要件 17.1/17.2 が原理的に満たせない（`board_pins` を
// `firmware/lib/` へ切り出したのと同じ理由、design.md "Architecture
// Integration" の「ホスト検証境界」）。
//
// ⚠️ 幅が処理系によって変化しない型のみを用いる（`board_pins` /
// `drivetrain_control/types.hpp` と同じ規約）。

#include <cstdint>

namespace teleop_input {

// 正規化済みパッド状態。design.md teleop_input の Service Interface が
// 定めた形そのもの。左スティックが並進速度指令（要件 8.3）、右スティック
// の横方向が回転速度指令（要件 8.4）、`deadman` が出力許可の元
// （要件 8.1, 8.2）、`selected_wheel` が輪単体テストの対象輪選択
// （要件 8.5）に、それぞれタスク 4.2 の `mapPad()` で対応付けられる。
// 本タスクではこの型そのものを定義するに留め、対応付けのロジックは
// 持たない。
struct PadState {
  // 左スティックの X/Y 軸。[-1, +1] に正規化済み。
  float left_x = 0.0f, left_y = 0.0f;
  // 右スティックの横方向（回転速度指令の元）。[-1, +1] に正規化済み。
  float right_x = 0.0f;
  // デッドマンボタンの押下状態。押している間だけ真（要件 8.1, 8.2）。
  bool deadman = false;
  // 輪単体テストの対象輪。-1 は機体走行、0..2 は輪単体（要件 8.5, 11.1）。
  std::int8_t selected_wheel = -1;
};

}  // namespace teleop_input
