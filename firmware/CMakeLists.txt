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

# drivetrain-core task 1.2 (requirements.md 1.4): keep esp_wifi's object code
# out of the linked production ELF/BIN.
#
# ⚠️ sdkconfig.defaults.production's CONFIG_BT_ENABLED=n is NOT enough by
# itself. On classic ESP32 (pinned pioarduino platform-espressif32 55.03.311,
# ESP-IDF 5.5.5), `esp_wifi/Kconfig`'s ESP_WIFI_ENABLED symbol has no user
# prompt (`default y if SOC_WIFI_SUPPORTED`, unconditionally true here), so no
# sdkconfig.defaults assignment to it has any effect. And CMAKE_ level
# EXCLUDE_COMPONENTS does NOT work either — verified empirically: with no
# COMPONENTS allowlist, ESP-IDF's default behaviour treats EVERY discovered
# component (the whole $IDF_PATH/components tree, since nothing restricts the
# candidate pool) as a build seed; excluding esp_wifi/bt/esp_phy only removes
# THEM from that seed list, but other still-seeded, unrelated components
# (e.g. wpa_supplicant, whose CMakeLists.txt unconditionally
# `PRIV_REQUIRES ... esp_wifi` regardless of any Kconfig setting) still
# resolve and re-pull esp_wifi's component target back into
# BUILD_COMPONENTS via ESP-IDF's requirement-expansion (component.cmake /
# build.cmake `__build_expand_requirements`), because that lookup walks the
# full global component registry, not the EXCLUDE_COMPONENTS-filtered seed
# list. There is no bt/esp_wifi/esp_phy-only exclusion that survives this.
#
# The mechanism that DOES work, and is the one ESP-IDF's own build-system
# docs actually document (build-system.rst "COMPONENTS"): a positive
# `COMPONENTS` allowlist. "Any component which 'requires' another component
# via REQUIRES/PRIV_REQUIRES will automatically have it added to this list,
# so the COMPONENTS list can be very short." Restricting the seed set to just
# what this app's component graph actually needs means nothing in that graph
# ever names esp_wifi/bt/esp_phy/wpa_supplicant, so they are never
# discovered as build seeds and never re-enter via requirement expansion.
#   - "src": PlatformIO's injected app-layer component (firmware/src).
#   - "drivetrain_control": this project's pure-logic component.
#   - "__pio_env": PlatformIO's own internal dummy component
#     (builder/frameworks/espidf.py generate_default_component() /
#     find_default_component()) that its build wrapper unconditionally
#     requires be present in BUILD_COMPONENTS; omitting it fails with
#     "Failed to find the default IDF component with build information for
#     generic files." before any component compiles.
# Verified: clean `pio run -e production` succeeds, firmware.map has zero
# libesp_wifi.a / libbt.a / libwpa_supplicant.a / libesp_phy.a references
# (down from a real esp_adapter.o/wifi_netif.o presence before this fix),
# and the production flash image shrank from 161985 to 135557 bytes.
# The none-of-the-driver-components-this-Spec-will-add-later
# (esp_driver_pcnt/esp_driver_mcpwm/esp_adc/esp_driver_gpio for the
# encoder/PWM/voltage ports) REQUIRES esp_wifi/bt/esp_phy either, so this
# allowlist should not need to grow when those tasks add real peripheral
# code to src/ or drivetrain_control.
#
# Only gated for [env:production]: [env:teleop] must keep the full default
# component set (no COMPONENTS restriction) because teleop-bringup adds
# Bluetooth/Bluepad32 on top of it later.
if(DEFINED ENV{DRIVETRAIN_BUILD_PRODUCTION})
    list(APPEND COMPONENTS "src" "drivetrain_control" "__pio_env")
endif()

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(drivetrain_firmware)
