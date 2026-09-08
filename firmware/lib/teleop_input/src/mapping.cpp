// teleop_input の唯一の翻訳単位（teleop-bringup タスク 4.1、要件 8.6, 8.7,
// 8.8, 8.9）。
//
// `pad_state.hpp` / `mapping.hpp` はいずれも本タスクの時点ではデフォルト
// メンバ初期化子つきのプレーンな `struct` のみで構成され、実行時の
// コードを持たない。それでも本ファイルを置いているのは、`board_pins` の
// `src/board_pins.cpp`（タスク 1.3）と同じ理由による。
//
//   1. ⚠️ **この部品がホスト向けビルドと実機向けビルドの双方で実際に
//      コンパイルされること**（本タスクの観測可能な完了状態）を、
//      「誰かに include されたら」ではなく、この翻訳単位そのものが
//      コンパイル対象になることで保証する。IDF の `idf_component_register`
//      は `SRCS` を持たないコンポーネントを INTERFACE ライブラリにし、
//      実体のある `.o` を一度も生成しない。`firmware/src/CMakeLists.txt`
//      が `DRIVETRAIN_BUILD_TELEOP` のときだけ `teleop_input` を
//      `REQUIRES` へ足す（本タスクの変更）が、その参照は「リンク依存
//      として名指す」ことしかせず、`teleop_input` 側に翻訳単位が無ければ
//      コンパイルされる実体は生まれない。
//   2. 下の `static_assert` 群が、`PadState` / `MappingParams` の既定値
//      （design.md teleop_input の Service Interface が既に固定した値）を
//      コンパイル時に固定する。ホスト側は `test/native/test_pad_mapping/`
//      が同じ事実を実行時にも確かめるが、実機側にはテストが無いため、
//      実機ビルドで既定値が壊れた場合に検出できるのはここだけである。
//
// パッド状態から指令への実際の変換（`mapPad()`）と `MappedCommand` は
// タスク 4.2 が本ファイルへ追加する。
//
// ⚠️ Bluepad32 の型・ペリフェラルの API（`driver/*.h` / `esp_adc/*.h` 等）
// のいずれも参照しない。

#include "teleop_input/mapping.hpp"
#include "teleop_input/pad_state.hpp"

#include <cstdint>
#include <type_traits>

namespace teleop_input {
namespace {

// --- 幅が処理系によって変化しない型のみを用いる（board_pins と同じ規約）------
static_assert(std::is_same<decltype(PadState::left_x), float>::value,
              "left_x は float である");
static_assert(std::is_same<decltype(PadState::left_y), float>::value,
              "left_y は float である");
static_assert(std::is_same<decltype(PadState::right_x), float>::value,
              "right_x は float である");
static_assert(std::is_same<decltype(PadState::deadman), bool>::value,
              "deadman は bool である");
static_assert(std::is_same<decltype(PadState::selected_wheel), std::int8_t>::value,
              "selected_wheel は std::int8_t である");

static_assert(std::is_same<decltype(MappingParams::deadzone), float>::value,
              "deadzone は float である");
static_assert(std::is_same<decltype(MappingParams::curve_exponent), float>::value,
              "curve_exponent は float である");
static_assert(std::is_same<decltype(MappingParams::speed_scale), float>::value,
              "speed_scale は float である");
static_assert(std::is_same<decltype(MappingParams::max_body_mm_s), float>::value,
              "max_body_mm_s は float である");
static_assert(std::is_same<decltype(MappingParams::max_omega_rad_s), float>::value,
              "max_omega_rad_s は float である");

// --- PadState の既定値（design.md teleop_input Service Interface）----------
static_assert(PadState{}.left_x == 0.0f, "left_x の既定値は 0.0f");
static_assert(PadState{}.left_y == 0.0f, "left_y の既定値は 0.0f");
static_assert(PadState{}.right_x == 0.0f, "right_x の既定値は 0.0f");
static_assert(PadState{}.deadman == false, "deadman の既定値は false");
static_assert(PadState{}.selected_wheel == -1,
              "selected_wheel の既定値は -1（機体走行）である");

// --- MappingParams の既定値（design.md 同 Service Interface、要件 8.6〜8.9）-
static_assert(MappingParams{}.deadzone == 0.0f, "deadzone の既定値は 0.0f（要件 8.6）");
static_assert(MappingParams{}.curve_exponent == 1.0f,
              "curve_exponent の既定値は 1.0f（線形・要件 8.7）");
static_assert(MappingParams{}.speed_scale == 0.0f, "speed_scale の既定値は 0.0f（要件 8.8）");
static_assert(MappingParams{}.max_body_mm_s == 0.0f, "max_body_mm_s の既定値は 0.0f");
static_assert(MappingParams{}.max_omega_rad_s == 0.0f, "max_omega_rad_s の既定値は 0.0f");

// --- 両型が集成体であること（判断ロジックを持たない純データであることの
//     コンパイル時の裏付け。ユーザ定義コンストラクタを持たせないことは
//     design.md が示した Service Interface の形そのものである）------------
static_assert(std::is_aggregate<PadState>::value, "PadState は集成体である");
static_assert(std::is_aggregate<MappingParams>::value, "MappingParams は集成体である");

}  // namespace
}  // namespace teleop_input
