"""drivetrain-core task 1.2: forward the active build profile to ESP-IDF's
CMake component-requirements pre-pass via an OS environment variable.

Why this exists (do not delete without re-reading): ESP-IDF's
tools/cmake/component.cmake `__component_get_requirements()` evaluates
every component's CMakeLists.txt (including firmware/src/CMakeLists.txt)
inside a SEPARATE `cmake -P` scripting-mode subprocess
(component_get_requirements.cmake), which is only given a fixed whitelist
of serialized "build properties" (see build_properties.temp.cmake). Custom
CMake cache variables passed via platformio.ini's
board_build.cmake_extra_args (-DFOO=1) are NOT in that whitelist and are
invisible inside that subprocess -- verified empirically while implementing
this task (a CMakeLists.txt check that used `if(DRIVETRAIN_BUILD_TELEOP)`
as a plain CMake variable failed with "Neither ... is set" even though
CMakeCache.txt clearly had it).

OS environment variables DO propagate through this subprocess chain
(CMake's execute_process() inherits the parent process environment by
default, and so does every process in between: PlatformIO's SCons process
-> the outer `cmake --build`/configure invocation -> the inner `cmake -P`
scripting-mode subprocess). So instead of a CMake cache variable, this
script sets a plain environment variable before any CMake invocation runs,
and firmware/src/CMakeLists.txt reads it via `DEFINED ENV{...}`.

This is the CMake-configure-time signal used to exclude the (not yet
existing) src/teleop/ source location from the production build
(requirements.md 1.4; design.md BuildSkeleton "本番へ無線を入れない"). It
is intentionally separate from the DRIVETRAIN_BUILD_TELEOP /
DRIVETRAIN_BUILD_PRODUCTION *compiler* macros defined via build_flags for
src/build_profile.hpp: CMake SRCS selection happens at configure time,
before any compiler macro exists, so a distinct CMake-level signal is
required even though the two mechanisms share a name.
"""

import os

Import("env")  # noqa: F821 -- PlatformIO injects this at exec time

_PROFILE_ENV_VARS_BY_PIOENV = {
    "teleop": "DRIVETRAIN_BUILD_TELEOP",
    "production": "DRIVETRAIN_BUILD_PRODUCTION",
}

pioenv = env["PIOENV"]
var_name = _PROFILE_ENV_VARS_BY_PIOENV.get(pioenv)
if var_name is None:
    raise RuntimeError(
        "set_build_profile_cmake_env.py was invoked for unexpected "
        f"PlatformIO environment '{pioenv}'. This script must only be "
        "referenced from [env:teleop] and [env:production] extra_scripts "
        "in platformio.ini."
    )

os.environ[var_name] = "1"
