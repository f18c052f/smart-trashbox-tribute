#include <unity.h>

#include <cstdint>
#include <string>
#include <type_traits>
#include <vector>

#include "run_recorder/run_recorder.hpp"

// teleop-bringup タスク 6.1: 走行1回を1レコードとする記録の器
// （RunRecorder / RunSample）のホストテスト。
//
// design.md "Components and Interfaces" の RunRecorder 行は Key
// Dependencies を「なし」と定めており、`run_recorder.hpp` は
// `drivetrain_control` を含むどのライブラリにも依存しない。したがって
// 本テストも `drivetrain_control` を include しない（依存の独立性そのものを
// 崩さないため）。
//
// 対象:
//   A. 走行の開始/終了と「走行の外では溜めない」こと（要件 15.1, 15.2）
//   B. `append()` が要件 15.3 のフィールドをすべて時刻とともに保持すること
//   C. 上限到達時に黙って欠落させない（最も古いサンプルを上書きし、
//      `overflowed()` で観測できる）こと（タスク本文の要求）
//   D. `extractCsv()` による取り出し（要件 15.4）。上限到達時の挙動が
//      取り出した記録そのものから判別できること
//   E. `beginRun()` の再呼び出しが前回の記録内容を破棄すること

using run_recorder::kMaxCapacity;
using run_recorder::kWheelCount;
using run_recorder::RunRecorder;
using run_recorder::RunSample;
using run_recorder::TimeMs;

void setUp(void) {}
void tearDown(void) {}

namespace {

RunSample makeSample(TimeMs t_ms, float seed) {
  RunSample s;
  s.t_ms = t_ms;
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    s.command_wheel_mm_s[i] = seed + static_cast<float>(i);
    s.measured_wheel_mm_s[i] = seed + 10.0f + static_cast<float>(i);
  }
  s.battery_milli_volts = static_cast<std::int32_t>(7000 + seed);
  s.battery_valid = true;
  s.global_protection_reasons = 0;
  s.wheel_protection_reasons[0] = 0;
  s.wheel_protection_reasons[1] = 0;
  s.wheel_protection_reasons[2] = 0;
  s.output_enabled = true;
  return s;
}

std::vector<std::string> extractLines(const RunRecorder& rec) {
  std::vector<std::string> lines;
  rec.extractCsv([&lines](const char* line, std::size_t length) {
    lines.emplace_back(line, length);
  });
  return lines;
}

}  // namespace

// ---------------------------------------------------------------------------
// 型（要件 15.3 の主語となるフィールドが揃っていること）
// ---------------------------------------------------------------------------

static_assert(kWheelCount == 3, "kWheelCount は3輪ぶん");
static_assert(std::is_same<TimeMs, std::int64_t>::value, "TimeMs は int64_t");

static_assert(std::is_same<decltype(RunSample::t_ms), TimeMs>::value, "t_ms は TimeMs");
static_assert(std::is_same<decltype(RunSample::battery_milli_volts), std::int32_t>::value,
              "battery_milli_volts は int32_t");
static_assert(std::is_same<decltype(RunSample::battery_valid), bool>::value,
              "battery_valid は bool");
static_assert(std::is_same<decltype(RunSample::output_enabled), bool>::value,
              "output_enabled は bool");
static_assert(
    std::is_same<decltype(RunSample::global_protection_reasons), std::uint16_t>::value,
    "global_protection_reasons は uint16_t");

// ---------------------------------------------------------------------------
// A. 走行の開始/終了と「走行の外では溜めない」こと（要件 15.1, 15.2）
// ---------------------------------------------------------------------------

void test_default_constructed_recorder_is_not_running_and_empty(void) {
  RunRecorder rec(8);
  TEST_ASSERT_FALSE(rec.isRunning());
  TEST_ASSERT_EQUAL_UINT32(0u, static_cast<std::uint32_t>(rec.size()));
  TEST_ASSERT_EQUAL_UINT32(0u, rec.totalAppended());
  TEST_ASSERT_FALSE(rec.overflowed());
}

void test_begin_run_starts_recording(void) {
  RunRecorder rec(8);
  rec.beginRun(1000);
  TEST_ASSERT_TRUE(rec.isRunning());
  TEST_ASSERT_EQUAL_INT64(1000, static_cast<std::int64_t>(rec.startedAtMs()));
}

void test_append_before_begin_run_is_ignored(void) {
  RunRecorder rec(8);
  rec.append(makeSample(1, 1.0f));
  TEST_ASSERT_EQUAL_UINT32(0u, static_cast<std::uint32_t>(rec.size()));
  TEST_ASSERT_EQUAL_UINT32(0u, rec.totalAppended());
}

void test_append_after_end_run_is_ignored(void) {
  RunRecorder rec(8);
  rec.beginRun(0);
  rec.append(makeSample(1, 1.0f));
  rec.endRun();
  TEST_ASSERT_FALSE(rec.isRunning());
  rec.append(makeSample(2, 2.0f));
  // endRun() 後の append は無視されるため、走行中に溜めた1件のままである。
  TEST_ASSERT_EQUAL_UINT32(1u, static_cast<std::uint32_t>(rec.size()));
  TEST_ASSERT_EQUAL_UINT32(1u, rec.totalAppended());
}

// ---------------------------------------------------------------------------
// B. append() が要件 15.3 のフィールドをすべて時刻とともに保持する
// ---------------------------------------------------------------------------

void test_append_preserves_all_required_fields(void) {
  RunRecorder rec(8);
  rec.beginRun(0);

  RunSample s;
  s.t_ms = 12345;
  s.command_wheel_mm_s[0] = 10.0f;
  s.command_wheel_mm_s[1] = 20.0f;
  s.command_wheel_mm_s[2] = 30.0f;
  s.measured_wheel_mm_s[0] = 9.5f;
  s.measured_wheel_mm_s[1] = 19.5f;
  s.measured_wheel_mm_s[2] = 29.5f;
  s.battery_milli_volts = 7400;
  s.battery_valid = true;
  s.global_protection_reasons = 0x0004;  // kCommandTimeout 相当のビット位置
  s.wheel_protection_reasons[0] = 0x0040;  // kMotorLock 相当のビット位置
  s.wheel_protection_reasons[1] = 0;
  s.wheel_protection_reasons[2] = 0;
  s.output_enabled = false;

  rec.append(s);

  TEST_ASSERT_EQUAL_UINT32(1u, static_cast<std::uint32_t>(rec.size()));
  const RunSample& got = rec.sampleAt(0);
  TEST_ASSERT_EQUAL_INT64(12345, static_cast<std::int64_t>(got.t_ms));
  TEST_ASSERT_EQUAL_FLOAT(10.0f, got.command_wheel_mm_s[0]);
  TEST_ASSERT_EQUAL_FLOAT(20.0f, got.command_wheel_mm_s[1]);
  TEST_ASSERT_EQUAL_FLOAT(30.0f, got.command_wheel_mm_s[2]);
  TEST_ASSERT_EQUAL_FLOAT(9.5f, got.measured_wheel_mm_s[0]);
  TEST_ASSERT_EQUAL_FLOAT(19.5f, got.measured_wheel_mm_s[1]);
  TEST_ASSERT_EQUAL_FLOAT(29.5f, got.measured_wheel_mm_s[2]);
  TEST_ASSERT_EQUAL_INT32(7400, got.battery_milli_volts);
  TEST_ASSERT_TRUE(got.battery_valid);
  TEST_ASSERT_EQUAL_UINT16(0x0004, got.global_protection_reasons);
  TEST_ASSERT_EQUAL_UINT16(0x0040, got.wheel_protection_reasons[0]);
  TEST_ASSERT_FALSE(got.output_enabled);
}

void test_default_run_sample_is_all_zero_or_false(void) {
  const RunSample s;
  TEST_ASSERT_EQUAL_INT64(0, static_cast<std::int64_t>(s.t_ms));
  TEST_ASSERT_EQUAL_FLOAT(0.0f, s.command_wheel_mm_s[0]);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, s.measured_wheel_mm_s[0]);
  TEST_ASSERT_EQUAL_INT32(0, s.battery_milli_volts);
  TEST_ASSERT_FALSE(s.battery_valid);
  TEST_ASSERT_EQUAL_UINT16(0, s.global_protection_reasons);
  TEST_ASSERT_FALSE(s.output_enabled);
}

void test_sample_at_out_of_range_returns_default(void) {
  RunRecorder rec(4);
  rec.beginRun(0);
  rec.append(makeSample(1, 1.0f));
  const RunSample& out_of_range = rec.sampleAt(5);
  TEST_ASSERT_EQUAL_INT64(0, static_cast<std::int64_t>(out_of_range.t_ms));
  TEST_ASSERT_FALSE(out_of_range.battery_valid);
}

// ---------------------------------------------------------------------------
// C. 上限到達時に黙って欠落させない（最も古いサンプルを上書きし、
//    overflowed() で観測できる）
// ---------------------------------------------------------------------------

void test_appending_within_capacity_does_not_overflow(void) {
  RunRecorder rec(4);
  rec.beginRun(0);
  for (TimeMs t = 1; t <= 4; ++t) {
    rec.append(makeSample(t, static_cast<float>(t)));
  }
  TEST_ASSERT_FALSE(rec.overflowed());
  TEST_ASSERT_EQUAL_UINT32(4u, static_cast<std::uint32_t>(rec.size()));
  TEST_ASSERT_EQUAL_UINT32(4u, rec.totalAppended());
  // 最古のサンプル（インデックス0）は最初に追記した t=1 のままである。
  TEST_ASSERT_EQUAL_INT64(1, static_cast<std::int64_t>(rec.sampleAt(0).t_ms));
}

void test_exceeding_capacity_sets_overflowed_and_evicts_oldest(void) {
  RunRecorder rec(4);
  rec.beginRun(0);
  for (TimeMs t = 1; t <= 5; ++t) {  // 容量4に対し5件追記
    rec.append(makeSample(t, static_cast<float>(t)));
  }
  TEST_ASSERT_TRUE(rec.overflowed());
  // 容量を超えても size() は容量以下に保たれる（黙って際限なく伸びない）。
  TEST_ASSERT_EQUAL_UINT32(4u, static_cast<std::uint32_t>(rec.size()));
  // append() の受理回数そのものは失われた分も含めて数える。
  TEST_ASSERT_EQUAL_UINT32(5u, rec.totalAppended());
  // 最古の t=1 は上書きされて失われ、残っているのは t=2..5（記録順）。
  TEST_ASSERT_EQUAL_INT64(2, static_cast<std::int64_t>(rec.sampleAt(0).t_ms));
  TEST_ASSERT_EQUAL_INT64(3, static_cast<std::int64_t>(rec.sampleAt(1).t_ms));
  TEST_ASSERT_EQUAL_INT64(4, static_cast<std::int64_t>(rec.sampleAt(2).t_ms));
  TEST_ASSERT_EQUAL_INT64(5, static_cast<std::int64_t>(rec.sampleAt(3).t_ms));
}

void test_capacity_constructor_argument_is_clamped_to_at_least_one(void) {
  RunRecorder rec(0);
  TEST_ASSERT_EQUAL_UINT32(1u, static_cast<std::uint32_t>(rec.capacity()));
}

void test_capacity_constructor_argument_is_clamped_to_max_capacity(void) {
  RunRecorder rec(kMaxCapacity + 1000);
  TEST_ASSERT_EQUAL_UINT32(static_cast<std::uint32_t>(kMaxCapacity),
                            static_cast<std::uint32_t>(rec.capacity()));
}

// ---------------------------------------------------------------------------
// D. forEachSample() が記録順（最古→最新）で全件へ渡す
// ---------------------------------------------------------------------------

void test_for_each_sample_visits_in_recorded_order_after_overflow(void) {
  RunRecorder rec(3);
  rec.beginRun(0);
  for (TimeMs t = 1; t <= 5; ++t) {
    rec.append(makeSample(t, static_cast<float>(t)));
  }
  std::vector<TimeMs> visited;
  rec.forEachSample([&visited](const RunSample& s) { visited.push_back(s.t_ms); });
  TEST_ASSERT_EQUAL_UINT32(3u, static_cast<std::uint32_t>(visited.size()));
  TEST_ASSERT_EQUAL_INT64(3, static_cast<std::int64_t>(visited[0]));
  TEST_ASSERT_EQUAL_INT64(4, static_cast<std::int64_t>(visited[1]));
  TEST_ASSERT_EQUAL_INT64(5, static_cast<std::int64_t>(visited[2]));
}

// ---------------------------------------------------------------------------
// E. extractCsv()（要件 15.4）: 記録を機体の外へ取り出す手段。上限到達時の
//    挙動が取り出した記録そのものから判別できる（タスク本文の観測可能な
//    完了状態）
// ---------------------------------------------------------------------------

void test_extract_csv_without_overflow_reports_overflowed_zero(void) {
  RunRecorder rec(4);
  rec.beginRun(0);
  rec.append(makeSample(1, 1.0f));
  rec.append(makeSample(2, 2.0f));

  std::vector<std::string> lines = extractLines(rec);
  TEST_ASSERT_TRUE(lines.size() >= 2 + 2);  // meta行 + header行 + データ2行
  TEST_ASSERT_TRUE(lines[0].find("overflowed=0") != std::string::npos);
  TEST_ASSERT_TRUE(lines[0].find("total_appended=2") != std::string::npos);
  TEST_ASSERT_TRUE(lines[0].find("size=2") != std::string::npos);
  TEST_ASSERT_TRUE(lines[0].find("capacity=4") != std::string::npos);
  TEST_ASSERT_TRUE(lines[1].find("t_ms") != std::string::npos);  // CSVヘッダ
  TEST_ASSERT_EQUAL_UINT32(4u, static_cast<std::uint32_t>(lines.size()));
}

void test_extract_csv_after_overflow_reports_overflowed_one(void) {
  RunRecorder rec(3);
  rec.beginRun(0);
  for (TimeMs t = 1; t <= 7; ++t) {
    rec.append(makeSample(t, static_cast<float>(t)));
  }

  std::vector<std::string> lines = extractLines(rec);
  TEST_ASSERT_TRUE(lines[0].find("overflowed=1") != std::string::npos);
  TEST_ASSERT_TRUE(lines[0].find("total_appended=7") != std::string::npos);
  TEST_ASSERT_TRUE(lines[0].find("size=3") != std::string::npos);
  // メタ行 + ヘッダ行 + データ3行(容量ぶん) = 5行。上限を超えて行数が
  // 増え続けないことも、ここで一緒に確かめる。
  TEST_ASSERT_EQUAL_UINT32(5u, static_cast<std::uint32_t>(lines.size()));
}

void test_extract_csv_data_rows_carry_the_recorded_values(void) {
  RunRecorder rec(4);
  rec.beginRun(0);
  RunSample s = makeSample(999, 5.0f);
  s.output_enabled = false;
  rec.append(s);

  std::vector<std::string> lines = extractLines(rec);
  TEST_ASSERT_EQUAL_UINT32(3u, static_cast<std::uint32_t>(lines.size()));  // meta + header + 1行
  const std::string& data_line = lines[2];
  TEST_ASSERT_TRUE(data_line.rfind("999,", 0) == 0);  // t_ms が先頭
}

// ---------------------------------------------------------------------------
// F. beginRun() の再呼び出しが前回の記録内容を破棄する（要件 15.1: 走行1回を
//    1つの記録の単位として扱う）
// ---------------------------------------------------------------------------

void test_begin_run_again_discards_previous_records_state(void) {
  RunRecorder rec(2);
  rec.beginRun(0);
  for (TimeMs t = 1; t <= 5; ++t) {  // 容量2に対し5件 -> overflow させておく
    rec.append(makeSample(t, static_cast<float>(t)));
  }
  TEST_ASSERT_TRUE(rec.overflowed());

  rec.beginRun(500);  // 新しい走行
  TEST_ASSERT_FALSE(rec.overflowed());
  TEST_ASSERT_EQUAL_UINT32(0u, static_cast<std::uint32_t>(rec.size()));
  TEST_ASSERT_EQUAL_UINT32(0u, rec.totalAppended());
  TEST_ASSERT_EQUAL_INT64(500, static_cast<std::int64_t>(rec.startedAtMs()));
}

int main(int argc, char** argv) {
  (void)argc;
  (void)argv;

  UNITY_BEGIN();

  RUN_TEST(test_default_constructed_recorder_is_not_running_and_empty);
  RUN_TEST(test_begin_run_starts_recording);
  RUN_TEST(test_append_before_begin_run_is_ignored);
  RUN_TEST(test_append_after_end_run_is_ignored);

  RUN_TEST(test_append_preserves_all_required_fields);
  RUN_TEST(test_default_run_sample_is_all_zero_or_false);
  RUN_TEST(test_sample_at_out_of_range_returns_default);

  RUN_TEST(test_appending_within_capacity_does_not_overflow);
  RUN_TEST(test_exceeding_capacity_sets_overflowed_and_evicts_oldest);
  RUN_TEST(test_capacity_constructor_argument_is_clamped_to_at_least_one);
  RUN_TEST(test_capacity_constructor_argument_is_clamped_to_max_capacity);

  RUN_TEST(test_for_each_sample_visits_in_recorded_order_after_overflow);

  RUN_TEST(test_extract_csv_without_overflow_reports_overflowed_zero);
  RUN_TEST(test_extract_csv_after_overflow_reports_overflowed_one);
  RUN_TEST(test_extract_csv_data_rows_carry_the_recorded_values);

  RUN_TEST(test_begin_run_again_discards_previous_records_state);

  return UNITY_END();
}
