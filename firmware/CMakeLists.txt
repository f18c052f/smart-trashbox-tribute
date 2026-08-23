# firmware/CMakeLists.txt — ESP-IDF project root for the teleop/production
# firmware builds (drivetrain-core task 1.1).
#
# EXTRA_COMPONENT_DIRS points DIRECTLY at lib/drivetrain_control, not at
# lib/ as a whole. This is deliberate: it is what keeps lib/test_support/
# (host-test-only, added in a later task) out of every firmware link, since
# a directory not listed here is never discovered by the IDF component
# search. See:
#   - research.md "Decision: 純ロジックを「2マニフェスト同居ディレクトリ」として配置する"
#   - design.md "File Structure Plan" (File Structure Plan の要点, 2nd bullet)
#   - requirements.md 16.3
#
# ⚠️ Use list(APPEND ...), not set(...): PlatformIO's espidf builder already
# injects its own EXTRA_COMPONENT_DIRS cache entry (containing
# PROJECT_SRC_DIR, i.e. firmware/src, which is how it locates the app-layer
# "__idf_src" target). A plain set() here would shadow that cache value
# entirely and PlatformIO would fail with "Couldn't find the main target of
# the project!" because firmware/src would no longer be discovered as a
# component. Appending preserves PlatformIO's entry alongside ours.
cmake_minimum_required(VERSION 3.16)

list(APPEND EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/lib/drivetrain_control")

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(drivetrain_firmware)
