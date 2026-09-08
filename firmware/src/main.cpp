#include "build_profile.hpp"

// App-layer entry point.
//
// teleop-bringup task 6.2 (requirements.md 9.1-9.7, 14.8; design.md "File
// Structure Plan" Modified Files: "main.cpp — DRIVETRAIN_BENCH の有無で
// BenchApp / TeleopApp を選ぶ. production 経路は現状のまま"): for the
// [env:teleop] build profile this constructs and runs the real TeleopApp
// control loop.
//
// teleop-bringup task 7.1 (requirements.md 10.6; design.md "アプリ層の入口
// が、プロファイルに応じて制御ループとこの最小経路のどちらを起動するかを
// 決める。入口の分岐はこのタスクが所有する"): the [env:bench] profile
// keeps DRIVETRAIN_BUILD_TELEOP defined (research.md "Decision: E-3 の
// 開ループ確認を teleop 系の第3プロファイルとして分離する" — 第3の排他
// プロファイルにはしない) but additionally defines DRIVETRAIN_BENCH
// (platformio.ini [env:bench] build_flags). This #ifdef is what owns the
// choice between the two paths at compile time; nothing upstream of this
// file branches on DRIVETRAIN_BENCH.
//
// [env:production] keeps its pre-task-6.2 placeholder behaviour unchanged
// (drivetrain-core task 6.5: prove the app layer links against the real
// drivetrain_control public API) — task 6.2 must not alter it, and
// TeleopApp itself depends on Bluepad32/BTstack (bluepad32 is only
// REQUIRES-d into the app component under DRIVETRAIN_BUILD_TELEOP, see
// firmware/src/CMakeLists.txt), so the two branches are also mutually
// exclusive at the component-graph level, not just behaviourally.
#ifdef DRIVETRAIN_BUILD_TELEOP

#ifdef DRIVETRAIN_BENCH

#include "teleop/bench_app.hpp"

extern "C" void app_main(void) {
  // `static`, not a plain local: mirrors the TeleopApp branch below (and
  // the pre-task-6.2 DrivetrainController placeholder before it) -- construct
  // lazily on first reaching this line, once app_main() is already running
  // and the ESP-IDF runtime (heap, GPIO/LEDC/ADC peripheral drivers,
  // FreeRTOS scheduler) is fully up, rather than during the pre-app_main
  // C++ static-init pass a namespace-scope global would use.
  static teleop::BenchApp app;

  // Polls the bench potentiometer and drives the (real, production-shared)
  // MotorLedcAdapter in a fixed-interval loop (see bench_app.cpp). Under
  // normal operation this call does not return -- BenchApp has no
  // Bluepad32/BTstack main loop to hand control to (task 7.1's whole point
  // is that this path never touches the radio stack), so it owns its own
  // infinite polling loop instead.
  app.run();
}

#else  // DRIVETRAIN_BUILD_TELEOP && !DRIVETRAIN_BENCH ([env:teleop])

#include "teleop/teleop_app.hpp"

extern "C" void app_main(void) {
  // `static`, not a plain local: TeleopApp holds a RunRecorder member
  // (~140KiB of fixed-size storage, run_recorder.hpp header comment) that
  // must never live on a FreeRTOS task stack (task 6.1's Implementation
  // Notes). `static` gives it static storage duration (same class as a
  // namespace-scope global) while still constructing it lazily on first
  // reaching this line -- i.e. once app_main() itself is already running,
  // by which point the ESP-IDF runtime (heap, GPIO/LEDC/PCNT/ADC
  // peripheral drivers, FreeRTOS scheduler) is fully up. A namespace-scope
  // global would instead construct during the pre-app_main C++ static-init
  // pass, before that runtime guarantee holds; the pre-task-6.2 code above
  // this branch already used this same function-local `static` pattern for
  // DrivetrainController for the same reason.
  static teleop::TeleopApp app;

  // Runs the Bluepad32/BTstack bootstrap and the fixed-period control loop
  // (spawned as a separate FreeRTOS task, see teleop_app.hpp/.cpp). Under
  // normal operation this call does not return (it becomes the BTstack
  // main loop for the remainder of the app's life).
  app.run();
}

#endif  // DRIVETRAIN_BENCH

#else  // !DRIVETRAIN_BUILD_TELEOP ([env:production])

#include "drivetrain_control/drivetrain_control.hpp"

// This intentionally does NOT wire up any ports (EncoderPort /
// MotorOutputPort / BatteryVoltagePort) — the real port adapters and
// app-layer control loop are teleop-only (see the TeleopApp branch above).
// Its only job is to prove that the app layer (this IDF component) links
// successfully against the real drivetrain_control public API
// (drivetrain-core task 6.5) in the production build configuration.
// Before task 6.5 this called a placeholder link-check function (task
// 1.1); now that the real DrivetrainController exists, construct one and
// exercise a real out-of-line method (step()) so the linker actually
// pulls in controller.cpp instead of only the header.
extern "C" void app_main(void) {
  static drivetrain_control::DrivetrainController controller;
  const drivetrain_control::StepResult result = controller.step(0);
  (void)result;
}

#endif  // DRIVETRAIN_BUILD_TELEOP
