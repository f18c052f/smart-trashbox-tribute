#include <unity.h>

#include <cstdint>
#include <type_traits>

#include "teleop_input/mapping.hpp"
#include "teleop_input/pad_state.hpp"

// teleop-bringup タスク 4.1: 正規化済みパッド状態と変換パラメータの型
// （teleop_input）のホストテスト。
//
// 本ファイルは design.md "File Structure Plan" が定めた
// `test/native/test_pad_mapping/` に置かれ、要件 8 全体（8.1〜8.9）と
// 要件 17.1, 17.2 を段階的に覆う。タスク 4.1 の時点では次の2点だけを
// 対象にする。
//
//   A. `PadState` が正規化済みの軸・デッドマン・輪選択を持ち、
//      コントローラ固有の型を一切含まないこと（要件 8.3, 8.4, 8.5 が
//      指す対応付けの入れ物）
//   B. `MappingParams` が無効範囲・変換特性・速度上限を持ち、独立した
//      フィールドとして実走中に調整できる形で保持されること
//      （要件 8.6, 8.7, 8.8, 8.9）
//
// ⚠️ B が本タスクの「観測可能な完了状態」の一部である。`firmware/src/` は
// ネイティブビルドに含まれない（`test_build_src` 既定 `no`）ため、これらの
// 型をアプリ層へ置くとホストから参照できず、要件 17.1 / 17.2（入力変換を
// 実機を用いずに検証できること）が成り立たなくなる。このテストが緑で
// あることが、`teleop_input` が `firmware/lib/` 側にあり、ホストビルドから
// 実際に見えていることの証拠になる。
//
// 実際の変換（`mapPad`）と、押している間だけ出力を許可する挙動
// （要件 8.1, 8.2）・無効範囲や変換特性が指令値へ及ぼす影響
// （要件 17.2）・輪単体選択の排他性（要件 11.1）は、`MappedCommand` と
// ともにタスク 4.2 が追加する。本ファイルはその時点で拡張される。

using teleop_input::MappingParams;
using teleop_input::PadState;

void setUp(void) {}
void tearDown(void) {}

// ---------------------------------------------------------------------------
// A. PadState（要件 8.3, 8.4, 8.5 が指す軸・輪選択の入れ物）
// ---------------------------------------------------------------------------

// 幅が処理系によって変化しない型を用いる（ホストと ESP32 で `int` の幅が
// 一致することを前提に置かない。board_pins / drivetrain_control/types.hpp
// と同じ流儀）。
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

void test_pad_state_defaults_to_centered_axes_and_body_drive(void) {
  const PadState pad{};
  TEST_ASSERT_EQUAL_FLOAT(0.0f, pad.left_x);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, pad.left_y);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, pad.right_x);
  TEST_ASSERT_FALSE(pad.deadman);
  TEST_ASSERT_EQUAL_INT8(-1, pad.selected_wheel);  // -1 は機体走行
}

void test_pad_state_holds_axes_deadman_and_wheel_selection(void) {
  const PadState pad{0.5f, -0.25f, 0.75f, true, 2};
  TEST_ASSERT_EQUAL_FLOAT(0.5f, pad.left_x);
  TEST_ASSERT_EQUAL_FLOAT(-0.25f, pad.left_y);
  TEST_ASSERT_EQUAL_FLOAT(0.75f, pad.right_x);
  TEST_ASSERT_TRUE(pad.deadman);
  TEST_ASSERT_EQUAL_INT8(2, pad.selected_wheel);
}

void test_pad_state_selected_wheel_distinguishes_body_drive_from_wheel_scoped(void) {
  // 要件 8.5, 11.1: 台上での輪単体テストのため、対象の輪を選べる。-1 は
  // 機体走行、0..2 は輪単体を表す（design.md teleop_input Service
  // Interface のコメント）。実際に「機体走行と輪単体が排他になる」ことの
  // 検証（要件 11.1 の観測可能な完了状態）は `mapPad` を持つタスク 4.2 が
  // 担う。本タスクでは値として区別できることだけを示す。
  const PadState body_drive{};
  TEST_ASSERT_EQUAL_INT8(-1, body_drive.selected_wheel);
  for (std::int8_t wheel = 0; wheel < 3; ++wheel) {
    const PadState wheel_scoped{0.0f, 0.0f, 0.0f, false, wheel};
    TEST_ASSERT_TRUE(wheel_scoped.selected_wheel >= 0);
  }
}

// ---------------------------------------------------------------------------
// B. MappingParams（要件 8.6〜8.9: 無効範囲・変換特性・速度上限が実走中に
//    調整できる設定値として保持される）
// ---------------------------------------------------------------------------

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

void test_mapping_params_defaults_to_identity_mapping(void) {
  const MappingParams params{};
  TEST_ASSERT_EQUAL_FLOAT(0.0f, params.deadzone);        // 要件 8.6: 既定は無効範囲なし
  TEST_ASSERT_EQUAL_FLOAT(1.0f, params.curve_exponent);  // 要件 8.7: 既定は等倍（線形）
  TEST_ASSERT_EQUAL_FLOAT(0.0f, params.speed_scale);     // 要件 8.8: 既定は未設定
  TEST_ASSERT_EQUAL_FLOAT(0.0f, params.max_body_mm_s);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, params.max_omega_rad_s);
}

void test_mapping_params_fields_are_independently_settable(void) {
  // 要件 8.9: 速度上限・無効範囲・変換特性のいずれも実走しながら調整できる
  // 形で保持される。ホスト側では「独立したフィールドとして代入できる」
  // ことがその条件を満たすための最小の証拠になる。実走中に値を書き換える
  // 経路（制御ループからの参照・更新）そのものはタスク 6.2 の責務であり、
  // 本タスクの対象外である。
  MappingParams params{};
  params.deadzone = 0.05f;
  params.curve_exponent = 2.0f;
  params.speed_scale = 0.5f;
  params.max_body_mm_s = 300.0f;
  params.max_omega_rad_s = 3.14f;

  TEST_ASSERT_EQUAL_FLOAT(0.05f, params.deadzone);
  TEST_ASSERT_EQUAL_FLOAT(2.0f, params.curve_exponent);
  TEST_ASSERT_EQUAL_FLOAT(0.5f, params.speed_scale);
  TEST_ASSERT_EQUAL_FLOAT(300.0f, params.max_body_mm_s);
  TEST_ASSERT_EQUAL_FLOAT(3.14f, params.max_omega_rad_s);
}

void test_mapping_params_instances_are_independent(void) {
  // 複数輪・複数走行にまたがって使い回すのではなく、各自が独立した設定を
  // 持てることを確かめる（値型であり、共有状態を持たないことの裏付け）。
  MappingParams a{};
  MappingParams b{};
  a.deadzone = 0.1f;
  TEST_ASSERT_EQUAL_FLOAT(0.0f, b.deadzone);
}

int main(int argc, char **argv) {
  (void)argc;
  (void)argv;

  UNITY_BEGIN();

  RUN_TEST(test_pad_state_defaults_to_centered_axes_and_body_drive);
  RUN_TEST(test_pad_state_holds_axes_deadman_and_wheel_selection);
  RUN_TEST(test_pad_state_selected_wheel_distinguishes_body_drive_from_wheel_scoped);

  RUN_TEST(test_mapping_params_defaults_to_identity_mapping);
  RUN_TEST(test_mapping_params_fields_are_independently_settable);
  RUN_TEST(test_mapping_params_instances_are_independent);

  return UNITY_END();
}
