# ESP-IDF component registration for the board_pins component
# (teleop-bringup task 1.3, requirements.md 2.1).
#
# Same two-manifest shape as lib/drivetrain_control: this CMakeLists.txt is
# the ESP-IDF component manifest (discovered through firmware/CMakeLists.txt
# EXTRA_COMPONENT_DIRS), and library.json next to it is the PlatformIO
# manifest for the `native` host-test environment. Both describe the same
# src/ + include/ layout, so both build systems compile the identical source
# as a single unit.
#
# ⚠️ Contrast with lib/test_support/, which deliberately has NO
# CMakeLists.txt so ESP-IDF's component search never discovers it.
# board_pins must be visible to BOTH: requirement 2.7 needs the pin-plan
# check to run on the host (firmware/src/ is not part of the native build --
# `test_build_src` defaults to `no`), and the adapters added by tasks 3.1-3.3
# need the same pin numbers on the device.
#
# ⚠️ The SRCS list below must stay in sync with the .cpp files under src/,
# for the same reason as drivetrain_control's (research.md Risk R2: a missed
# update leaves `native` green while the firmware link silently drops a
# source file). tests/firmware/test_firmware_boundaries.py fixes the
# agreement for both components.
idf_component_register(
    SRCS "src/board_pins.cpp"
    INCLUDE_DIRS "include"
)
