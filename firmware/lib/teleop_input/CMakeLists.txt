# ESP-IDF component registration for the teleop_input component
# (teleop-bringup task 4.1, requirements.md 8.6, 8.7, 8.8, 8.9).
#
# Same two-manifest shape as lib/board_pins (task 1.3): this CMakeLists.txt
# is the ESP-IDF component manifest (discovered through firmware/CMakeLists.txt
# EXTRA_COMPONENT_DIRS), and library.json next to it is the PlatformIO
# manifest for the `native` host-test environment. Both describe the same
# src/ + include/ layout, so both build systems compile the identical source
# as a single unit.
#
# ⚠️ Contrast with lib/test_support/, which deliberately has NO
# CMakeLists.txt so ESP-IDF's component search never discovers it.
# teleop_input must be visible to BOTH: requirements 17.1/17.2 need the
# pad-input types to be usable on the host (firmware/src/ is not part of the
# native build -- test_build_src defaults to no), and task 6.2's TeleopApp
# needs the same types on the device.
#
# ⚠️ The SRCS list below must stay in sync with the .cpp files under src/,
# for the same reason as board_pins's (research.md Risk R2: a missed update
# leaves `native` green while the firmware link silently drops a source
# file). tests/firmware/test_firmware_boundaries.py fixes the agreement.
#
# task 4.2 (requirements.md 8.1-8.5, 11.1, 17.1, 17.2) added
# src/mapped_command.cpp (mapPad() / MappedCommand). This list is NOT
# glob-based -- verified empirically against
# platforms/espressif32's idf_component_register handling -- so the new
# source had to be added here explicitly, not just dropped into src/.
#
# ⚠️ task 4.2 also added REQUIRES drivetrain_control. mapped_command.hpp
# includes drivetrain_control/drivetrain_control.hpp (design.md teleop_input
# Dependencies: "Outbound: drivetrain_control の指令型 (P0)"). Without this,
# ESP-IDF's per-component include-path resolution does not see it: even
# though firmware/src/CMakeLists.txt REQUIRES both drivetrain_control and
# teleop_input for the *app* component, that does not transitively grant
# teleop_input's *own* translation units (compiled as a separate component)
# an include path to drivetrain_control's public header -- verified
# empirically with a clean `pio run -e teleop` (fatal error: drivetrain_
# control/drivetrain_control.hpp: No such file or directory without this
# line). The `native` PlatformIO environment does not need an equivalent
# change: its Library Dependency Finder resolves lib_deps by scanning
# #include statements, not by an explicit REQUIRES list.
idf_component_register(
    SRCS "src/mapping.cpp" "src/mapped_command.cpp"
    INCLUDE_DIRS "include"
    REQUIRES drivetrain_control
)
