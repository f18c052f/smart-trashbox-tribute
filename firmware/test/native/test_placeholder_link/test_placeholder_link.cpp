#include <unity.h>

#include "drivetrain_control/drivetrain_control.hpp"

// drivetrain-core task 1.1: host-side proof that [env:native] links against
// lib/drivetrain_control via its library.json manifest, and that it
// actually exercises the placeholder library function (not just compiles
// it). Superseded by real behavioral tests once task 6.5 lands the real
// public API.

void setUp(void) {}
void tearDown(void) {}

void test_placeholder_link_check_returns_expected_value(void) {
  TEST_ASSERT_EQUAL_INT(42, drivetrain_control::placeholder_link_check());
}

int main(int argc, char **argv) {
  (void)argc;
  (void)argv;
  UNITY_BEGIN();
  RUN_TEST(test_placeholder_link_check_returns_expected_value);
  return UNITY_END();
}
