# App-layer IDF component (drivetrain-core task 1.1: minimal skeleton only,
# no ports wired up here). Depends on the drivetrain_control component to
# prove the app layer can link against the pure-logic library.
#
# drivetrain-core task 1.2: teleop-only source exclusion mechanism
# (requirements.md 1.4; design.md BuildSkeleton state row "本番へ無線を
# 入れない（要件 1.4）" — "build_src_filter が src/teleop/ を除外する").
#
# ⚠️ Deviation from design.md's literal mechanism name: PlatformIO's
# `build_src_filter` / `src_filter` INI option is explicitly unsupported
# under framework=espidf by the pinned pioarduino platform-espressif32
# build script (builder/frameworks/espidf.py prints "Warning: the
# 'src_filter' option cannot be used with ESP-IDF..." and does not apply
# it) -- verified empirically against the pinned platform package during
# this task. Source selection for espidf components is a CMake-configure-
# time decision, so the equivalent exclusion is implemented here instead,
# gated on an ENVIRONMENT variable rather than a CMake cache variable:
# ESP-IDF evaluates this file's `idf_component_register()` requirements
# inside a separate `cmake -P` scripting-mode subprocess
# (component_get_requirements.cmake) that only receives a fixed whitelist
# of serialized build properties -- a custom CMake cache variable set via
# board_build.cmake_extra_args (-DFOO=1) is invisible there (verified
# empirically: `if(DRIVETRAIN_BUILD_TELEOP)` failed even though
# CMakeCache.txt had the entry). Environment variables DO propagate
# through that subprocess chain, so
# scripts/set_build_profile_cmake_env.py (platformio.ini
# [env:teleop]/[env:production] extra_scripts) sets
# DRIVETRAIN_BUILD_TELEOP / DRIVETRAIN_BUILD_PRODUCTION as OS environment
# variables before any CMake invocation runs -- distinct from, but named
# identically to, the compiler macros of the same name that
# src/build_profile.hpp checks via #if defined(...); CMake SRCS selection
# happens before any compiler macro exists, so a separate signal is
# required regardless of which CMake mechanism carries it. The teleop-only
# source location (src/teleop/) is out of this Spec's scope
# (teleop-bringup adds it) -- the glob below is empty and harmless until
# then.
if(NOT DEFINED ENV{DRIVETRAIN_BUILD_TELEOP} AND NOT DEFINED ENV{DRIVETRAIN_BUILD_PRODUCTION})
    message(FATAL_ERROR
        "Neither the DRIVETRAIN_BUILD_TELEOP nor the DRIVETRAIN_BUILD_PRODUCTION "
        "environment variable is set. Check platformio.ini "
        "[env:teleop]/[env:production] extra_scripts = "
        "pre:scripts/set_build_profile_cmake_env.py.")
endif()

set(DRIVETRAIN_APP_SRCS "main.cpp")
if(DEFINED ENV{DRIVETRAIN_BUILD_TELEOP})
    # No CONFIGURE_DEPENDS: this file is evaluated both in the normal
    # configure pass AND inside ESP-IDF's `cmake -P` scripting-mode
    # component-requirements subprocess (see the comment above), and
    # CONFIGURE_DEPENDS is rejected there with "invalid for script and
    # find package modes" -- verified empirically.
    file(GLOB DRIVETRAIN_TELEOP_SRCS "teleop/*.cpp")
    list(APPEND DRIVETRAIN_APP_SRCS ${DRIVETRAIN_TELEOP_SRCS})
endif()

idf_component_register(
    SRCS ${DRIVETRAIN_APP_SRCS}
    INCLUDE_DIRS "."
    REQUIRES drivetrain_control
)
