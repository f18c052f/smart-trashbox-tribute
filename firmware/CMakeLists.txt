# firmware/CMakeLists.txt — ESP-IDF project root for the teleop/production
# firmware builds (drivetrain-core task 1.1).
#
# EXTRA_COMPONENT_DIRS points DIRECTLY at each component directory under
# lib/, not at lib/ as a whole. This is deliberate: it is what keeps
# lib/test_support/ (host-test-only) out of every firmware link, since a
# directory not listed here is never discovered by the IDF component
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

# teleop-bringup task 1.3 (requirements.md 2.1; design.md "File Structure
# Plan" Modified Files): the pin-assignment source of truth. Listed
# INDIVIDUALLY, for the same reason drivetrain_control is -- naming lib/
# wholesale here would pull lib/test_support/ into the IDF component search.
#
# Being discovered is not the same as being built: for [env:production] the
# COMPONENTS allowlist below still restricts the build seeds to
# src/drivetrain_control/__pio_env, and nothing in that graph requires
# board_pins, so this line does NOT add board_pins to the production image
# and the allowlist does not need to change. firmware/src/CMakeLists.txt
# adds board_pins to the app component's REQUIRES only when
# DRIVETRAIN_BUILD_TELEOP is set.
list(APPEND EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/lib/board_pins")

# teleop-bringup task 4.1 (requirements.md 8.6, 8.7, 8.8, 8.9; design.md
# "File Structure Plan" Modified Files): normalized pad state (PadState) and
# mapping parameters (MappingParams). Listed INDIVIDUALLY for the same
# reason as board_pins above -- naming lib/ wholesale here would pull
# lib/test_support/ into the IDF component search.
#
# Same production-safety argument as board_pins: nothing in the
# [env:production] component graph (src/drivetrain_control/__pio_env) ever
# names teleop_input, so this line does NOT add it to the production image
# and the COMPONENTS allowlist below does not need to change.
# firmware/src/CMakeLists.txt adds teleop_input to the app component's
# REQUIRES only when DRIVETRAIN_BUILD_TELEOP is set.
list(APPEND EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/lib/teleop_input")

# teleop-bringup task 5.1 (requirements.md 7.1; design.md ControllerLink
# "External: Bluepad32 / BTstack (P0)"): Bluepad32 (raw ESP-IDF platform)
# and BTstack, fetched by scripts/fetch_bluepad32.py ([env:teleop]-only
# extra_scripts pre-step; see platformio.ini) into
# firmware/.deps/bluepad32/src/components -- NOT a lib/ component of this
# repo, and deliberately NOT vendored (BTstack's license forbids commercial
# redistribution; see fetch_bluepad32.py's module docstring and
# firmware/.gitignore's .deps/ entry).
#
# ⚠️ Gated on DRIVETRAIN_BUILD_TELEOP, unlike board_pins/teleop_input above:
# those are first-party lib/ directories that always exist in a checkout;
# this one does not exist at all until fetch_bluepad32.py has run, and that
# script only ever runs for [env:teleop]. Appending an EXTRA_COMPONENT_DIRS
# entry that never exists for [env:production]/[env:native] would be a
# needless (if likely harmless) divergence from a directory ESP-IDF can
# actually search -- gating keeps it absent from those environments'
# component search entirely, the same containment principle as the
# COMPONENTS allowlist below.
#
# Being discovered is still not the same as being built (see the
# board_pins/teleop_input comments above): [env:production]'s COMPONENTS
# allowlist a few lines down never names "bluepad32" or "btstack", so even
# though this EXTRA_COMPONENT_DIRS entry is teleop-only already, nothing
# about it could reach the production component graph regardless.
# firmware/src/CMakeLists.txt adds "bluepad32" to the app component's
# REQUIRES only when DRIVETRAIN_BUILD_TELEOP is set; "btstack" (and
# nvs_flash/app_update/esp_timer/cmd_nvs/cmd_system) then resolve
# transitively via Bluepad32's own idf_component_register(REQUIRES ...).
if(DEFINED ENV{DRIVETRAIN_BUILD_TELEOP})
    list(APPEND EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/.deps/bluepad32/src/components")
endif()

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
