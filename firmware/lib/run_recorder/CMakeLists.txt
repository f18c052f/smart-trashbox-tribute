# ESP-IDF component registration for the run_recorder component
# (teleop-bringup task 6.1, requirements.md 15.1-15.5).
#
# Same two-manifest shape as lib/board_pins and lib/teleop_input: this
# CMakeLists.txt is the ESP-IDF component manifest (discovered through
# firmware/CMakeLists.txt EXTRA_COMPONENT_DIRS), and library.json next to it
# is the PlatformIO manifest for the `native` host-test environment. Both
# describe the same src/ + include/ layout, so both build systems compile the
# identical source as a single unit.
#
# ⚠️ Unlike board_pins/teleop_input, this component has NO REQUIRES: design.md
# "Components and Interfaces" lists RunRecorder's Key Dependencies as "なし"
# (none) -- it does not include drivetrain_control or any other library (see
# the header comment in include/run_recorder/run_recorder.hpp for why: it
# keeps the recorded format structurally independent of both
# drivetrain_control's internal types and prediction_core's ThrowRecord,
# which is the concrete backing for requirement 15.5).
#
# ⚠️ Contrast with lib/test_support/, which deliberately has NO CMakeLists.txt
# so ESP-IDF's component search never discovers it. run_recorder must be
# visible to BOTH: this task's own host tests need it on the host
# (firmware/src/ is not part of the native build -- test_build_src defaults
# to no), and task 6.2's TeleopApp will need the same type on the device.
#
# ⚠️ The SRCS list below must stay in sync with the .cpp files under src/, for
# the same reason as board_pins's / teleop_input's (research.md Risk R2: a
# missed update leaves `native` green while the firmware link silently drops
# a source file). tests/firmware/test_firmware_boundaries.py fixes the
# agreement for the component-directory allowlist.
idf_component_register(
    SRCS "src/run_recorder.cpp"
    INCLUDE_DIRS "include"
)
