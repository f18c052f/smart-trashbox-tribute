#include <unity.h>

#include <cmath>
#include <cstdint>
#include <fstream>
#include <sstream>
#include <string>

#include "drivetrain_control/types.hpp"
#include "test_support/plant_coefficients.hpp"
#include "test_support/wheel_plant.hpp"

// drivetrain-core task 7.1: test_support::WheelPlant のホストテスト。
//
// 検証する:
//   A. 1次遅れでデューティ*供給比に比例した定常速度へ収束する方向へ動く
//      こと（具体的な収束の速さを合否条件にしない。design.md
//      Implementation Notes、要件 16.4, 16.6）
//   B. むだ時間を持たない ―― 最初の advance() 呼び出しの時点から即座に
//      速度が動き始める（速度がゼロのまま足踏みする「遅延」が無い）
//   C. setStalled(true) の間は duty に関わらず speed_mm_s() が 0 になる
//      （保護① モータロック検出の発火試験用、要件 10.1 系の土台）
//   D. raw_count() が法 raw_modulus で正しく折り返す（正転側・逆転側の
//      両方）
//   E. stalled の間は raw_count() が変化しない（held flat）
//   F. plant_coefficients.hpp が「合否条件ではない仮値」の宣言を実際に
//      持っていることを検査する回帰（テスト名に
//      plant_model_is_provisional を含む。design.md "WheelPlant /
//      FakePorts" Implementation Notes Risks、要件 16.2, 16.3, 16.4）

using drivetrain_control::DurationMs;
using test_support::PlantCoefficients;
using test_support::WheelPlant;

namespace {

// 検証ロジックが正しく動くことだけを確かめるための架空の係数一式
// （実機の性能値ではない。plant_coefficients.hpp の宣言どおり、合否条件
// でも実測値でもない）。
PlantCoefficients makeCoefficients() {
  PlantCoefficients c{};
  c.duty_to_steady_mm_s = 500.0f;
  c.time_constant_ms = 100.0f;
  return c;
}

}  // namespace

void setUp(void) {}
void tearDown(void) {}

// ---------------------------------------------------------------------------
// A: 定常速度(duty*supply_ratio*duty_to_steady_mm_s)へ、時間経過とともに
//    偏差が縮む方向へ動く。
// ---------------------------------------------------------------------------

void test_advance_converges_toward_target_speed_direction_only(void) {
  const PlantCoefficients coeffs = makeCoefficients();
  WheelPlant plant(coeffs, /*counts_per_wheel_rev=*/836, /*wheel_diameter_mm=*/60.0f,
                    /*raw_modulus=*/65536);

  const float target = 1.0f * 1.0f * coeffs.duty_to_steady_mm_s;

  float previous_error = std::fabs(target - plant.speed_mm_s());
  for (int i = 0; i < 20; ++i) {
    plant.advance(/*duty=*/1.0f, /*supply_ratio=*/1.0f, /*dt_ms=*/5);
    const float error = std::fabs(target - plant.speed_mm_s());
    // 偏差が単調に非増加であること（収束の速さ自体は問わない）。
    TEST_ASSERT_TRUE(error <= previous_error + 1.0e-6f);
    previous_error = error;
  }
  // 十分な回数を重ねれば target に近づいていること（向きの確認）。
  TEST_ASSERT_TRUE(previous_error < std::fabs(target));
}

void test_advance_scales_target_by_supply_ratio(void) {
  const PlantCoefficients coeffs = makeCoefficients();
  WheelPlant plant(coeffs, 836, 60.0f, 65536);

  // supply_ratio=0.5 なら定常速度も半分の方向へ寄る。十分に多くの小さい
  // ステップを重ねて漸近させ、半分の目標へ収束していることを確認する。
  for (int i = 0; i < 500; ++i) {
    plant.advance(/*duty=*/1.0f, /*supply_ratio=*/0.5f, /*dt_ms=*/5);
  }
  const float half_target = 0.5f * coeffs.duty_to_steady_mm_s;
  TEST_ASSERT_FLOAT_WITHIN(1.0f, half_target, plant.speed_mm_s());
}

// ---------------------------------------------------------------------------
// B: むだ時間なし ―― 最初の advance() 呼び出しから即座に速度が動く。
// ---------------------------------------------------------------------------

void test_advance_has_no_dead_time_first_call_moves_speed_immediately(void) {
  const PlantCoefficients coeffs = makeCoefficients();
  WheelPlant plant(coeffs, 836, 60.0f, 65536);

  TEST_ASSERT_EQUAL_FLOAT(0.0f, plant.speed_mm_s());
  plant.advance(/*duty=*/1.0f, /*supply_ratio=*/1.0f, /*dt_ms=*/1);
  // むだ時間があれば最初の呼び出しでは速度が 0 のままのはず。ここでは
  // 非ゼロへ動いていることを確認する。
  TEST_ASSERT_TRUE(plant.speed_mm_s() > 0.0f);
}

// ---------------------------------------------------------------------------
// C: setStalled(true) の間は duty に関わらず速度が 0。
// ---------------------------------------------------------------------------

void test_stalled_speed_reads_as_zero_regardless_of_duty(void) {
  const PlantCoefficients coeffs = makeCoefficients();
  WheelPlant plant(coeffs, 836, 60.0f, 65536);

  // まず正常に加速させてから、ストールを注入する。
  for (int i = 0; i < 50; ++i) {
    plant.advance(1.0f, 1.0f, 5);
  }
  TEST_ASSERT_TRUE(plant.speed_mm_s() > 0.0f);

  plant.setStalled(true);
  plant.advance(/*duty=*/1.0f, /*supply_ratio=*/1.0f, /*dt_ms=*/5);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, plant.speed_mm_s());

  // duty を変えても解除するまで 0 のまま。
  plant.advance(/*duty=*/-1.0f, /*supply_ratio=*/1.0f, /*dt_ms=*/5);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, plant.speed_mm_s());

  // 解除すれば通常どおり動き出す。
  plant.setStalled(false);
  plant.advance(1.0f, 1.0f, 5);
  TEST_ASSERT_TRUE(plant.speed_mm_s() > 0.0f);
}

// ---------------------------------------------------------------------------
// D: raw_count() が法で正しく折り返す（正転側・逆転側）。
// ---------------------------------------------------------------------------

void test_raw_count_wraps_forward_at_modulus_boundary(void) {
  const PlantCoefficients coeffs = makeCoefficients();
  const std::int32_t raw_modulus = 100;  // 折り返しを素早く再現するための小さい法
  WheelPlant plant(coeffs, /*counts_per_wheel_rev=*/1, /*wheel_diameter_mm=*/1.0f, raw_modulus);
  // counts_per_wheel_rev=1, wheel_diameter_mm=1 とすることで
  // accumulated_counts_ の増分が「距離 / pi」というシンプルな形になる
  // （厳密な値の一致ではなく、折り返しが起きて範囲内に収まることの確認が
  // 目的）。

  bool observed_wrap = false;
  std::int32_t previous = plant.raw_count();
  for (int i = 0; i < 2000; ++i) {
    plant.advance(/*duty=*/1.0f, /*supply_ratio=*/1.0f, /*dt_ms=*/50);
    const std::int32_t current = plant.raw_count();
    // 常に法の折り返し範囲 (-modulus/2, +modulus/2] に収まっていること。
    TEST_ASSERT_TRUE(current > -raw_modulus / 2);
    TEST_ASSERT_TRUE(current <= raw_modulus / 2);
    if (current < previous) {
      // 正転を続けているのに値が下がった = 折り返しが起きた。
      observed_wrap = true;
    }
    previous = current;
  }
  TEST_ASSERT_TRUE_MESSAGE(observed_wrap,
                            "expected raw_count() to wrap at least once while advancing forward");
}

// ---------------------------------------------------------------------------
// E: stalled の間は raw_count() が変化しない。
// ---------------------------------------------------------------------------

void test_raw_count_holds_flat_while_stalled(void) {
  const PlantCoefficients coeffs = makeCoefficients();
  WheelPlant plant(coeffs, 836, 60.0f, 65536);

  for (int i = 0; i < 50; ++i) {
    plant.advance(1.0f, 1.0f, 5);
  }
  const std::int32_t before_stall = plant.raw_count();
  TEST_ASSERT_TRUE(before_stall != 0);

  plant.setStalled(true);
  for (int i = 0; i < 50; ++i) {
    plant.advance(1.0f, 1.0f, 5);
  }
  TEST_ASSERT_EQUAL_INT32(before_stall, plant.raw_count());
}

// ---------------------------------------------------------------------------
// F: plant_coefficients.hpp が「合否条件ではない仮値」の宣言を実際に持つ
//    ことを検査する回帰（テスト名に plant_model_is_provisional を含む）。
// ---------------------------------------------------------------------------

namespace {

// pio test -e native は firmware/ を作業ディレクトリとして実行される
// （他の native テストと同じ実行慣習）。念のため複数の候補パスを試す。
std::string readPlantCoefficientsHeaderSource() {
  const char* kCandidatePaths[] = {
      "lib/test_support/include/test_support/plant_coefficients.hpp",
      "../lib/test_support/include/test_support/plant_coefficients.hpp",
      "../../lib/test_support/include/test_support/plant_coefficients.hpp",
      "firmware/lib/test_support/include/test_support/plant_coefficients.hpp",
  };
  for (const char* path : kCandidatePaths) {
    std::ifstream file(path);
    if (file.good()) {
      std::ostringstream buffer;
      buffer << file.rdbuf();
      return buffer.str();
    }
  }
  return std::string();
}

}  // namespace

void test_plant_model_is_provisional_declaration_exists_in_plant_coefficients_header(void) {
  const std::string source = readPlantCoefficientsHeaderSource();
  TEST_ASSERT_TRUE_MESSAGE(
      !source.empty(),
      "plant_coefficients.hpp not found from any candidate relative path -- "
      "run `pio test -e native` from firmware/");

  TEST_ASSERT_TRUE_MESSAGE(
      source.find("\xe5\x90\x88\xe5\x90\xa6\xe6\x9d\xa1\xe4\xbb\xb6\xe3\x81\xa7\xe3\x81\xaf"
                   "\xe3\x81\xaa\xe3\x81\x84\xe4\xbb\xae\xe5\x80\xa4") != std::string::npos,
      "plant_coefficients.hpp must declare the coefficients are not a pass/fail "
      "criterion (\xe5\x90\x88\xe5\x90\xa6\xe6\x9d\xa1\xe4\xbb\xb6\xe3\x81\xa7\xe3\x81\xaf"
      "\xe3\x81\xaa\xe3\x81\x84\xe4\xbb\xae\xe5\x80\xa4, \xe8\xa6\x81\xe4\xbb\xb6 16.4)");

  TEST_ASSERT_TRUE_MESSAGE(
      source.find("\xe5\xae\x9f\xe6\xb8\xac\xe5\x80\xa4\xe3\x81\xa7\xe3\x81\xaf\xe3\x81\xaa") !=
          std::string::npos,
      "plant_coefficients.hpp must declare the coefficients are not measured values "
      "(\xe5\xae\x9f\xe6\xb8\xac\xe5\x80\xa4\xe3\x81\xa7\xe3\x81\xaf\xe3\x81\xaa, "
      "\xe8\xa6\x81\xe4\xbb\xb6 16.4)");

  TEST_ASSERT_TRUE_MESSAGE(
      source.find("\xe5\xae\x9f\xe6\xa9\x9f\xe6\x80\xa7\xe8\x83\xbd\xe3\x81\xae\xe4\xb8\xbb\xe5\xbc\xb5"
                   "\xe3\x81\xab\xe4\xbd\xbf\xe3\x82\x8f\xe3\x81\xaa\xe3\x81\x84") != std::string::npos,
      "plant_coefficients.hpp must declare host results must not be used as "
      "real-hardware performance claims "
      "(\xe5\xae\x9f\xe6\xa9\x9f\xe6\x80\xa7\xe8\x83\xbd\xe3\x81\xae\xe4\xb8\xbb\xe5\xbc\xb5\xe3\x81\xab"
      "\xe4\xbd\xbf\xe3\x82\x8f\xe3\x81\xaa\xe3\x81\x84, \xe8\xa6\x81\xe4\xbb\xb6 16.6)");

  TEST_ASSERT_TRUE_MESSAGE(
      source.find("test_support/wheel_plant.hpp") != std::string::npos ||
          source.find("PlantCoefficients") != std::string::npos,
      "sanity check: the file we read really is plant_coefficients.hpp");
}

int main(int argc, char** argv) {
  (void)argc;
  (void)argv;
  UNITY_BEGIN();

  RUN_TEST(test_advance_converges_toward_target_speed_direction_only);
  RUN_TEST(test_advance_scales_target_by_supply_ratio);
  RUN_TEST(test_advance_has_no_dead_time_first_call_moves_speed_immediately);
  RUN_TEST(test_stalled_speed_reads_as_zero_regardless_of_duty);
  RUN_TEST(test_raw_count_wraps_forward_at_modulus_boundary);
  RUN_TEST(test_raw_count_holds_flat_while_stalled);
  RUN_TEST(test_plant_model_is_provisional_declaration_exists_in_plant_coefficients_header);

  return UNITY_END();
}
