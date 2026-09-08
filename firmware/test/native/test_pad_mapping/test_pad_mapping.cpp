#include <unity.h>

#include <cmath>
#include <cstdint>
#include <type_traits>

#include "drivetrain_control/drivetrain_control.hpp"
#include "teleop_input/mapped_command.hpp"
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
// C 以降（タスク 4.2、要件 8.1-8.5, 11.1, 17.1, 17.2）: `teleop_input::mapPad`
// が `PadState` + `MappingParams` から `MappedCommand`（`mapped_command.hpp`,
// タスク 4.1 が予告した2ファイルとは別の新設ファイル）を求める。押している
// 間だけ出力を許可する挙動（8.1, 8.2）・左右スティックの対応付け（8.3,
// 8.4）・輪単体選択の排他性（8.5, 11.1）・無効範囲/変換特性/速度上限が
// 指令値へ及ぼす影響を独立に検証すること（17.1, 17.2）を、それぞれ区別
// できる形でテストする。

using drivetrain_control::TimeMs;
using teleop_input::kBodyDriveSelection;
using teleop_input::MappedCommand;
using teleop_input::mapPad;
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

// ---------------------------------------------------------------------------
// C. mapPad（タスク 4.2、要件 8.1〜8.5, 11.1, 17.1, 17.2）
// ---------------------------------------------------------------------------

namespace {

// フルレンジ（無効範囲なし・線形・上限そのまま）の変換パラメータ。
// 「無効範囲・変換特性・速度上限の効果」を1つずつ切り分けて検証するための
// 基準点として使う（要件 17.1, 17.2）。
constexpr MappingParams kFullRangeParams{
    /*deadzone=*/0.0f,
    /*curve_exponent=*/1.0f,
    /*speed_scale=*/1.0f,
    /*max_body_mm_s=*/1000.0f,
    /*max_omega_rad_s=*/10.0f,
};

}  // namespace

// --- 8.1, 8.2: デッドマンによる出力許可 ------------------------------------

void test_mappad_deadman_held_enables_output(void) {
  // 要件 8.1: 押している間だけ出力を許可する。
  const PadState pad{0.5f, 0.5f, 0.0f, /*deadman=*/true, kBodyDriveSelection};
  const MappedCommand cmd = mapPad(pad, kFullRangeParams, /*now=*/1000);
  TEST_ASSERT_TRUE(cmd.output_enabled);
}

void test_mappad_deadman_released_revokes_output_and_zeroes_all_commands(void) {
  // 要件 8.2: 離された場合、出力許可を取り消す「だけでなく」指令値がすべて
  // ゼロになる（厳密な事後条件）。スティックが振られていても、輪が選択
  // されていても、デッドマンが偽なら全ゼロであることを示す。
  const PadState pad{0.9f, -0.8f, 0.7f, /*deadman=*/false, /*selected_wheel=*/1};
  const MappedCommand cmd = mapPad(pad, kFullRangeParams, /*now=*/12345);

  TEST_ASSERT_FALSE(cmd.output_enabled);
  TEST_ASSERT_FALSE(cmd.wheel_scoped);

  TEST_ASSERT_EQUAL_FLOAT(0.0f, cmd.body.vx_mm_s);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, cmd.body.vy_mm_s);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, cmd.body.omega_rad_s);
  TEST_ASSERT_EQUAL_INT64(0, cmd.body.issued_at_ms);

  for (std::uint8_t i = 0; i < drivetrain_control::kWheelCount; ++i) {
    TEST_ASSERT_EQUAL_FLOAT(0.0f, cmd.wheel.wheel_mm_s[i]);
  }
  TEST_ASSERT_EQUAL_INT64(0, cmd.wheel.issued_at_ms);
}

// --- 8.3, 8.4: 左右スティックの対応付け -------------------------------------

void test_mappad_left_stick_maps_to_body_translation(void) {
  // 要件 8.3: 左スティック -> 並進速度指令（vx, vy）。
  const PadState forward{/*left_x=*/0.0f, /*left_y=*/1.0f, /*right_x=*/0.0f,
                          /*deadman=*/true, kBodyDriveSelection};
  const MappedCommand forward_cmd = mapPad(forward, kFullRangeParams, /*now=*/0);
  TEST_ASSERT_FALSE(forward_cmd.wheel_scoped);
  TEST_ASSERT_EQUAL_FLOAT(1000.0f, forward_cmd.body.vx_mm_s);  // フル前進 = 上限に到達
  TEST_ASSERT_EQUAL_FLOAT(0.0f, forward_cmd.body.vy_mm_s);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, forward_cmd.body.omega_rad_s);

  const PadState lateral{/*left_x=*/1.0f, /*left_y=*/0.0f, /*right_x=*/0.0f,
                          /*deadman=*/true, kBodyDriveSelection};
  const MappedCommand lateral_cmd = mapPad(lateral, kFullRangeParams, /*now=*/0);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, lateral_cmd.body.vx_mm_s);
  TEST_ASSERT_EQUAL_FLOAT(1000.0f, lateral_cmd.body.vy_mm_s);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, lateral_cmd.body.omega_rad_s);
}

void test_mappad_right_stick_lateral_maps_to_body_rotation(void) {
  // 要件 8.4: 右スティックの横方向 -> 回転速度指令（omega）。左スティックは
  // 中立に保ち、omega 以外がゼロのままであることも併せて確かめる。
  const PadState rotate{/*left_x=*/0.0f, /*left_y=*/0.0f, /*right_x=*/-1.0f,
                         /*deadman=*/true, kBodyDriveSelection};
  const MappedCommand cmd = mapPad(rotate, kFullRangeParams, /*now=*/0);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, cmd.body.vx_mm_s);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, cmd.body.vy_mm_s);
  TEST_ASSERT_EQUAL_FLOAT(-10.0f, cmd.body.omega_rad_s);  // フル左回転 = 上限に到達
}

void test_mappad_sets_issued_at_ms_to_now_when_output_enabled(void) {
  const PadState pad{0.5f, 0.0f, 0.0f, /*deadman=*/true, kBodyDriveSelection};
  const MappedCommand cmd = mapPad(pad, kFullRangeParams, /*now=*/987654321);
  TEST_ASSERT_EQUAL_INT64(987654321, cmd.body.issued_at_ms);
}

// --- 8.5, 11.1: 輪単体選択と機体走行の排他性 --------------------------------

void test_mappad_wheel_selection_is_mutually_exclusive_with_body_drive(void) {
  // 要件 8.5, 11.1: 輪単体選択と機体走行が排他になる。左右スティックを
  // すべて振った状態でも、輪が選ばれていれば body 指令は一切populateされず
  // （全ゼロ）、選ばれた輪だけが wheel 指令に現れる。
  for (std::int8_t wheel = 0; wheel < 3; ++wheel) {
    const PadState pad{/*left_x=*/0.6f, /*left_y=*/0.8f, /*right_x=*/-0.4f,
                        /*deadman=*/true, wheel};
    const MappedCommand cmd = mapPad(pad, kFullRangeParams, /*now=*/42);

    TEST_ASSERT_TRUE(cmd.output_enabled);
    TEST_ASSERT_TRUE(cmd.wheel_scoped);

    // body は排他により全ゼロ（スティック入力があっても populate されない）。
    TEST_ASSERT_EQUAL_FLOAT(0.0f, cmd.body.vx_mm_s);
    TEST_ASSERT_EQUAL_FLOAT(0.0f, cmd.body.vy_mm_s);
    TEST_ASSERT_EQUAL_FLOAT(0.0f, cmd.body.omega_rad_s);

    // 選択した輪だけが非ゼロ（left_y=0.8 > 0 のフル上限未満の速度）で、
    // 他の輪はゼロのまま。
    for (std::uint8_t i = 0; i < drivetrain_control::kWheelCount; ++i) {
      if (i == static_cast<std::uint8_t>(wheel)) {
        TEST_ASSERT_FLOAT_WITHIN(1e-4f, 800.0f, cmd.wheel.wheel_mm_s[i]);
      } else {
        TEST_ASSERT_EQUAL_FLOAT(0.0f, cmd.wheel.wheel_mm_s[i]);
      }
    }
  }
}

void test_mappad_body_drive_leaves_wheel_command_zero(void) {
  // 逆方向の排他性: selected_wheel == -1（機体走行）のとき、wheel 指令が
  // 全ゼロのままであることを確かめる。
  const PadState pad{0.5f, 0.5f, 0.5f, /*deadman=*/true, kBodyDriveSelection};
  const MappedCommand cmd = mapPad(pad, kFullRangeParams, /*now=*/1);
  TEST_ASSERT_FALSE(cmd.wheel_scoped);
  for (std::uint8_t i = 0; i < drivetrain_control::kWheelCount; ++i) {
    TEST_ASSERT_EQUAL_FLOAT(0.0f, cmd.wheel.wheel_mm_s[i]);
  }
}

// --- 8.6: 無効範囲（deadzone）が指令値へ及ぼす影響を独立に検証 --------------
// 要件 17.1, 17.2: curve_exponent / speed_scale / 上限を固定し、deadzone
// だけを変えたときに結果が変わることを示す。

void test_mappad_deadzone_alone_changes_result_holding_other_params_constant(void) {
  const PadState pad{/*left_x=*/0.0f, /*left_y=*/0.3f, /*right_x=*/0.0f,
                      /*deadman=*/true, kBodyDriveSelection};

  MappingParams no_deadzone = kFullRangeParams;
  no_deadzone.deadzone = 0.0f;
  const MappedCommand no_dz_cmd = mapPad(pad, no_deadzone, /*now=*/0);
  TEST_ASSERT_FLOAT_WITHIN(1e-4f, 300.0f, no_dz_cmd.body.vx_mm_s);  // 0.3 * 1000

  MappingParams wide_deadzone = kFullRangeParams;  // curve/speed_scale/上限は不変
  wide_deadzone.deadzone = 0.5f;                   // 0.3 は無効範囲の内側
  const MappedCommand wide_dz_cmd = mapPad(pad, wide_deadzone, /*now=*/0);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, wide_dz_cmd.body.vx_mm_s);  // 無効化される
}

void test_mappad_deadzone_preserves_full_scale_ceiling_at_full_deflection(void) {
  // deadzone を変えても、フルデフレクション（|入力|==1）では上限に到達する
  // （無効範囲ぶんを再スケールして [0,1] の全域を保つ実装であることの
  // 裏付け）。
  const PadState full{0.0f, 1.0f, 0.0f, /*deadman=*/true, kBodyDriveSelection};

  MappingParams narrow = kFullRangeParams;
  narrow.deadzone = 0.0f;
  MappingParams wide = kFullRangeParams;
  wide.deadzone = 0.6f;

  TEST_ASSERT_EQUAL_FLOAT(1000.0f, mapPad(full, narrow, 0).body.vx_mm_s);
  TEST_ASSERT_EQUAL_FLOAT(1000.0f, mapPad(full, wide, 0).body.vx_mm_s);
}

// --- 8.7: 変換特性（curve_exponent）が指令値へ及ぼす影響を独立に検証 --------
// deadzone / speed_scale / 上限を固定し、curve_exponent だけを変える。

void test_mappad_curve_exponent_alone_reshapes_result_holding_other_params_constant(void) {
  const PadState half{0.0f, 0.5f, 0.0f, /*deadman=*/true, kBodyDriveSelection};

  MappingParams linear = kFullRangeParams;
  linear.curve_exponent = 1.0f;
  const float linear_out = mapPad(half, linear, 0).body.vx_mm_s;
  TEST_ASSERT_FLOAT_WITHIN(1e-3f, 500.0f, linear_out);  // 0.5^1 * 1000

  MappingParams curved = kFullRangeParams;  // deadzone/speed_scale/上限は不変
  curved.curve_exponent = 2.0f;
  const float curved_out = mapPad(half, curved, 0).body.vx_mm_s;
  TEST_ASSERT_FLOAT_WITHIN(1e-3f, 250.0f, curved_out);  // 0.5^2 * 1000

  // 中立付近の分解能を高める特性: 同じ半分の入力に対し、指数を上げると
  // 出力が小さくなる（原点付近の傾きが緩くなる = そのぶん中立付近の
  // 入力変化に対する出力変化が細かくなる）。
  TEST_ASSERT_TRUE(curved_out < linear_out);
}

void test_mappad_curve_exponent_does_not_change_full_deflection_ceiling(void) {
  // 指数を変えても、フルデフレクションでは上限（1000）に一致する
  // （形を変えるだけで最大速度は変えない特性であることの裏付け）。
  const PadState full{0.0f, 1.0f, 0.0f, /*deadman=*/true, kBodyDriveSelection};

  MappingParams linear = kFullRangeParams;
  linear.curve_exponent = 1.0f;
  MappingParams curved = kFullRangeParams;
  curved.curve_exponent = 4.0f;

  TEST_ASSERT_EQUAL_FLOAT(1000.0f, mapPad(full, linear, 0).body.vx_mm_s);
  TEST_ASSERT_EQUAL_FLOAT(1000.0f, mapPad(full, curved, 0).body.vx_mm_s);
}

// --- 8.8: 速度上限（speed_scale・max_*）が指令値へ及ぼす影響を独立に検証 ----
// deadzone / curve_exponent を固定し、speed_scale と上限のそれぞれを変える。

void test_mappad_speed_scale_alone_caps_result_holding_other_params_constant(void) {
  const PadState full{0.0f, 1.0f, 0.0f, /*deadman=*/true, kBodyDriveSelection};

  MappingParams quarter = kFullRangeParams;  // deadzone/curve/上限は不変
  quarter.speed_scale = 0.25f;
  TEST_ASSERT_EQUAL_FLOAT(250.0f, mapPad(full, quarter, 0).body.vx_mm_s);

  MappingParams full_scale = kFullRangeParams;
  full_scale.speed_scale = 1.0f;
  TEST_ASSERT_EQUAL_FLOAT(1000.0f, mapPad(full, full_scale, 0).body.vx_mm_s);
}

void test_mappad_max_body_and_max_omega_scale_independently(void) {
  // deadzone/curve/speed_scale を固定し、max_body_mm_s と max_omega_rad_s を
  // 個別に変える。一方を変えても他方に影響しないことを確かめる
  // （要件 17.2 の「変更の影響を独立に検証できる」ことの直接的な裏付け）。
  const PadState full{/*left_x=*/0.0f, /*left_y=*/1.0f, /*right_x=*/1.0f,
                       /*deadman=*/true, kBodyDriveSelection};

  MappingParams higher_body_cap = kFullRangeParams;
  higher_body_cap.max_body_mm_s = 2000.0f;  // max_omega_rad_s は不変（10.0f）
  const MappedCommand cmd_a = mapPad(full, higher_body_cap, 0);
  TEST_ASSERT_EQUAL_FLOAT(2000.0f, cmd_a.body.vx_mm_s);
  TEST_ASSERT_EQUAL_FLOAT(10.0f, cmd_a.body.omega_rad_s);  // 変化なし

  MappingParams higher_omega_cap = kFullRangeParams;
  higher_omega_cap.max_omega_rad_s = 20.0f;  // max_body_mm_s は不変（1000.0f）
  const MappedCommand cmd_b = mapPad(full, higher_omega_cap, 0);
  TEST_ASSERT_EQUAL_FLOAT(1000.0f, cmd_b.body.vx_mm_s);  // 変化なし
  TEST_ASSERT_EQUAL_FLOAT(20.0f, cmd_b.body.omega_rad_s);
}

// --- 純関数であることの裏付け ------------------------------------------------

void test_mappad_is_pure_same_input_yields_same_output(void) {
  const PadState pad{0.3f, -0.4f, 0.2f, /*deadman=*/true, kBodyDriveSelection};
  const MappedCommand first = mapPad(pad, kFullRangeParams, 555);
  const MappedCommand second = mapPad(pad, kFullRangeParams, 555);
  TEST_ASSERT_EQUAL_FLOAT(first.body.vx_mm_s, second.body.vx_mm_s);
  TEST_ASSERT_EQUAL_FLOAT(first.body.vy_mm_s, second.body.vy_mm_s);
  TEST_ASSERT_EQUAL_FLOAT(first.body.omega_rad_s, second.body.omega_rad_s);
  TEST_ASSERT_EQUAL(first.output_enabled, second.output_enabled);
  TEST_ASSERT_EQUAL(first.wheel_scoped, second.wheel_scoped);
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

  RUN_TEST(test_mappad_deadman_held_enables_output);
  RUN_TEST(test_mappad_deadman_released_revokes_output_and_zeroes_all_commands);

  RUN_TEST(test_mappad_left_stick_maps_to_body_translation);
  RUN_TEST(test_mappad_right_stick_lateral_maps_to_body_rotation);
  RUN_TEST(test_mappad_sets_issued_at_ms_to_now_when_output_enabled);

  RUN_TEST(test_mappad_wheel_selection_is_mutually_exclusive_with_body_drive);
  RUN_TEST(test_mappad_body_drive_leaves_wheel_command_zero);

  RUN_TEST(test_mappad_deadzone_alone_changes_result_holding_other_params_constant);
  RUN_TEST(test_mappad_deadzone_preserves_full_scale_ceiling_at_full_deflection);

  RUN_TEST(test_mappad_curve_exponent_alone_reshapes_result_holding_other_params_constant);
  RUN_TEST(test_mappad_curve_exponent_does_not_change_full_deflection_ceiling);

  RUN_TEST(test_mappad_speed_scale_alone_caps_result_holding_other_params_constant);
  RUN_TEST(test_mappad_max_body_and_max_omega_scale_independently);

  RUN_TEST(test_mappad_is_pure_same_input_yields_same_output);

  return UNITY_END();
}
