#include "drivetrain_control/drivetrain_control.hpp"

// Minimal app-layer entry point for drivetrain-core task 1.1
// (build-skeleton proof only).
//
// This intentionally does NOT wire up any ports (EncoderPort /
// MotorOutputPort / BatteryVoltagePort) yet — that is out of scope for
// this task. Its only job is to prove that the app layer (this IDF
// component) links successfully against the drivetrain_control component
// in both the teleop and production build configurations.

extern "C" void app_main(void) {
  volatile int result = drivetrain_control::placeholder_link_check();
  (void)result;
}
