#include <unity.h>

#include "drivetrain_control/drivetrain_control.hpp"

// drivetrain-core task 1.1: minimal embedded-lane placeholder proving that
// the real ESP-IDF/Xtensa toolchain can link a test against
// lib/drivetrain_control (design.md test/embedded/build_smoke: "実機
// ツールチェーンでリンクが通ることの最小確認"). This lane targets link
// success under the real toolchain, not execution on physical hardware —
// this task's validation runs `pio run -e teleop` / `pio run -e
// production` (the app-layer link), not `pio test` against a connected
// device. Superseded by real embedded tests as later tasks add ports.
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

void test_placeholder_link_check_returns_expected_value(void) {
  TEST_ASSERT_EQUAL_INT(42, drivetrain_control::placeholder_link_check());
}

extern "C" void app_main(void) {
  UNITY_BEGIN();
  RUN_TEST(test_placeholder_link_check_returns_expected_value);
  UNITY_END();
}
