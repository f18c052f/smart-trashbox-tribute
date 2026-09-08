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
idf_component_register(
    SRCS "src/mapping.cpp"
    INCLUDE_DIRS "include"
)
