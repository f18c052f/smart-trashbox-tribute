#include <unity.h>

#include <cstdint>

#include "drivetrain_control/drivetrain_control.hpp"

// drivetrain-core task 1.1: minimal embedded-lane placeholder proving that
// the real ESP-IDF/Xtensa toolchain can link a test against
// lib/drivetrain_control (design.md test/embedded/build_smoke: "実機
// ツールチェーンでリンクが通ることの最小確認"). This lane targets link
// success under the real toolchain, not execution on physical hardware —
// this task's validation runs `pio run -e teleop` / `pio run -e
// production` (the app-layer link), not `pio test` against a connected
// device.
//
// Updated by task 6.5: the task 1.1 placeholder_link_check() free function
// this test originally called is gone now that the real public API
// (drivetrain_control::DrivetrainController) exists to link against
// instead. This test now constructs a real DrivetrainController and calls
// an out-of-line method (step()) so the real toolchain must actually link
// controller.cpp, not just parse the header.
//
// ⚠️ Entry point is app_main(), not main(): under framework=espidf the
// FreeRTOS startup code (esp-idf/freertos/app_startup.c) calls app_main()
// directly, unlike the native/desktop `int main()` convention used in
// test/native/. A plain `int main()` here links against
// __idf_embedded_test_build_smoke but leaves app_main undefined, which
// fails at link time with "undefined reference to `app_main'" — verified
// empirically while building this task.

void setUp(void) {}
void tearDown(void) {}

void test_controller_links_and_reports_unconfigured(void) {
  drivetrain_control::DrivetrainController controller;
  const drivetrain_control::StepResult result = controller.step(0);
  const auto kNotConfiguredBit = static_cast<std::uint16_t>(drivetrain_control::BlockReason::kNotConfigured);
  TEST_ASSERT_EQUAL_UINT16(kNotConfiguredBit, result.global_reasons);
}

extern "C" void app_main(void) {
  UNITY_BEGIN();
  RUN_TEST(test_controller_links_and_reports_unconfigured);
  UNITY_END();
}
