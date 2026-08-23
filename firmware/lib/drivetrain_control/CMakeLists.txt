# ESP-IDF component registration for the drivetrain_control pure-logic
# library (drivetrain-core task 1.1).
#
# This file co-exists in the same directory as library.json (the
# PlatformIO manifest for the `native` host-test environment). Both
# manifests describe the same src/ + include/ layout so that the two build
# systems (IDF component search via firmware/CMakeLists.txt
# EXTRA_COMPONENT_DIRS, and PlatformIO's native library discovery) link the
# identical source as a single unit. See research.md "Decision: 純ロジック
# を「2マニフェスト同居ディレクトリ」として配置する".
#
# ⚠️ The SRCS list below must stay in sync with the .cpp files under src/.
# A future task (1.3) adds a static check for this; until then, a missed
# update here is the classic way for `native` to stay green while the
# firmware link silently drops a source file (research.md Risk R2).
idf_component_register(
    SRCS "src/drivetrain_control.cpp" "src/config.cpp" "src/wrap_accumulator.cpp" "src/voltage_scaler.cpp" "src/kinematics.cpp" "src/odometry.cpp" "src/velocity_pid.cpp" "src/protection/motor_lock.cpp"
    INCLUDE_DIRS "include"
)
