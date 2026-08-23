#pragma once

// drivetrain-core task 1.2: build-configuration exclusivity guard
// (design.md "BuildSkeleton" State Management row "排他の実現（要件 1.3、
// OQ-21 の決着）"; requirements.md 1.3, 16.7).
//
// Exactly one of DRIVETRAIN_BUILD_TELEOP / DRIVETRAIN_BUILD_PRODUCTION must
// be defined by the active PlatformIO environment's build_flags
// (platformio.ini [env:teleop] / [env:production]). If both or neither are
// defined, compilation fails here with a clear #error.
//
// ⚠️ This header lives in the app layer (firmware/src/), NOT in
// lib/drivetrain_control/. The pure drivetrain-core logic is identical
// source across all three build configurations (teleop / production /
// native) and must never branch on these macros (requirements.md 1.2;
// design.md File Structure Plan の要点, 3rd bullet).
//
// Included from src/main.cpp so the check runs for the app-layer build in
// both the teleop and production environments. The native (host-test)
// environment never compiles firmware/src/ (test_filter = native/* and
// PlatformIO's default test_build_src = no keep it out), so this header is
// never evaluated there and never needs to define either macro.

#if defined(DRIVETRAIN_BUILD_TELEOP) && defined(DRIVETRAIN_BUILD_PRODUCTION)
#error \
    "DRIVETRAIN_BUILD_TELEOP and DRIVETRAIN_BUILD_PRODUCTION are both defined. " \
    "The teleop and production build configurations are mutually exclusive " \
    "(requirements.md 1.3) -- check platformio.ini build_flags for " \
    "[env:teleop] and [env:production]."
#endif

#if !defined(DRIVETRAIN_BUILD_TELEOP) && !defined(DRIVETRAIN_BUILD_PRODUCTION)
#error \
    "Neither DRIVETRAIN_BUILD_TELEOP nor DRIVETRAIN_BUILD_PRODUCTION is defined. " \
    "Exactly one build-profile macro must be defined by the active " \
    "PlatformIO environment (requirements.md 1.3) -- check platformio.ini " \
    "build_flags for [env:teleop] and [env:production]."
#endif
