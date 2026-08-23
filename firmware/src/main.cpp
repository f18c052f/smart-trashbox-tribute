#include "build_profile.hpp"
#include "drivetrain_control/drivetrain_control.hpp"

// Minimal app-layer entry point.
//
// This intentionally does NOT wire up any ports (EncoderPort /
// MotorOutputPort / BatteryVoltagePort) yet — that is out of scope for
// drivetrain-core (a later Spec, teleop-bringup, owns the real port
// adapters and the app-layer control loop). Its only job is to prove that
// the app layer (this IDF component) links successfully against the real
// drivetrain_control public API (drivetrain-core task 6.5) in both the
// teleop and production build configurations. Before task 6.5 this called
// a placeholder link-check function (task 1.1); now that the real
// DrivetrainController exists, construct one and exercise a real
// out-of-line method (step()) so the linker actually pulls in
// controller.cpp instead of only the header.
extern "C" void app_main(void) {
  static drivetrain_control::DrivetrainController controller;
  const drivetrain_control::StepResult result = controller.step(0);
  (void)result;
}
