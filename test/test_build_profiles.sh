#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
source build.sh

fail() {
  echo "test_build_profiles: $*" >&2
  exit 1
}

# Full targets are synthesized from an ordinary transport environment. Check
# the resolved PlatformIO configuration so board-specific display, GPS, input,
# and BLE constraints cannot silently disappear through that inheritance.
pio project config --json-output | python3 -c '
import json
import sys

sections = {section: dict(options) for section, options in json.load(sys.stdin)}

def option_text(env_name, option_name):
    section = sections.get(f"env:{env_name}")
    if section is None:
        raise SystemExit(f"test_build_profiles: missing PlatformIO environment {env_name}")
    value = section.get(option_name, [])
    values = value if isinstance(value, list) else [value]
    return "\n".join(str(item) for item in values)

def require(env_name, option_name, needle):
    if needle not in option_text(env_name, option_name):
        raise SystemExit(
            f"test_build_profiles: {env_name} {option_name} lost {needle}"
        )

def reject(env_name, option_name, needle):
    if needle in option_text(env_name, option_name):
        raise SystemExit(
            f"test_build_profiles: {env_name} {option_name} unexpectedly contains {needle}"
        )

xiao_wifi = "Xiao_S3_WIO_companion_radio_wifi"
require(xiao_wifi, "build_flags", "DISPLAY_CLASS=SSD1306Display")
require(xiao_wifi, "build_src_filter", "helpers/ui/SSD1306Display.cpp")
require(xiao_wifi, "build_src_filter", "examples/companion_radio/ui-new")
require(xiao_wifi, "lib_deps", "Adafruit SSD1306")
reject(xiao_wifi, "build_src_filter", "helpers/ui/NullDisplayDriver.cpp")

m8_usb = "ThinkNode_M8_companion_radio_usb"
require(m8_usb, "build_flags", "PIN_BUZZER=33")
reject(m8_usb, "build_flags", "PIN_BUZZER=6")
require(m8_usb, "build_flags", "ENV_INCLUDE_GPS=1")
require(m8_usb, "lib_deps", "GxEPD2 @ 1.6.9")
reject(m8_usb, "lib_deps", "GxEPD2 @ 1.6.2")

require("ThinkNode_M5_companion_radio_wifi", "build_flags", "UI_RECENT_LIST_SIZE=9")

for env_name in (
    "t1000e_companion_radio_usb",
    "MeshTracker_X1_companion_radio_usb",
    "ThinkNode_M3_companion_radio_usb",
):
    require(env_name, "build_flags", "BLE_TX_POWER=0")

for env_name in (
    "WioTrackerL1_companion_radio_usb",
    "WioTrackerL1_companion_radio_ble",
):
    require(env_name, "build_flags", "UI_NO_HIBERNATE")

rak_usb = "RAK_3401_companion_radio_usb"
require(rak_usb, "build_flags", "RAK_BOARD")
require(rak_usb, "build_flags", "FORCE_GPS_ALIVE")
'

# Synthetic inventory: one ESP32 target qualified for expanded Full and one
# nRF52 target which must attempt complete LoRa OTA in its current partition.
SUPPORTED_PIO_ENVS=(
  esp_repeater
  esp_repeater_lora_ota_no_external_sensors
  nrf_repeater
  nrf_repeater_lora_ota_no_external_sensors
  nrf_qspi_repeater
  nrf_qspi_repeater_lora_ota_no_external_sensors
  nrf_sensor
)
PIO_ENV_PLATFORM_BY_NAME[esp_repeater]=ESP32_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[esp_repeater_lora_ota_no_external_sensors]=ESP32_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[nrf_repeater]=NRF52_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[nrf_repeater_lora_ota_no_external_sensors]=NRF52_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[nrf_qspi_repeater]=NRF52_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[nrf_qspi_repeater_lora_ota_no_external_sensors]=NRF52_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[nrf_sensor]=NRF52_PLATFORM
PIO_ENV_QSPI_OTA_BY_NAME[nrf_qspi_repeater_lora_ota_no_external_sensors]=1
PIO_ENV_FULL_BUILD_BY_NAME[esp_repeater]=1

RESOLVED_BUILD_TARGETS=(esp_repeater)
configure_effective_build_profile build-firmware >/dev/null
[ "$BUILD_PROFILE_EFFECTIVE" = full ] || fail "ESP32 auto did not prefer Full"
[ "$AUTO_PREFER_FULL_BUILD" = 1 ] || fail "ESP32 Full pass was not scheduled"
[ "$AUTO_REDUCED_FALLBACK_TARGET" = \
  esp_repeater_lora_ota_no_external_sensors ] \
  || fail "ESP32 reduced fallback was not resolved"

BUILD_PROFILE_OVERRIDE=auto
BUILD_PROFILE_EXPLICIT=0
RESOLVED_BUILD_TARGETS=(nrf_repeater)
configure_effective_build_profile build-firmware >/dev/null
[ "${RESOLVED_BUILD_TARGETS[0]}" = \
  nrf_repeater_lora_ota_no_external_sensors ] \
  || fail "nRF52 complete OTA pass did not select the OTA identity"
[ "$AUTO_COMPLETE_FIRST_PASS" = 1 ] \
  || fail "nRF52 complete first pass was not scheduled"
[ "$AUTO_PUBLISH_REDUCED_SECOND_PASS" = 1 ] \
  || fail "internal-flash nRF52 reduced second artifact was not scheduled"

BUILD_PROFILE_OVERRIDE=auto
BUILD_PROFILE_EXPLICIT=0
RESOLVED_BUILD_TARGETS=(nrf_qspi_repeater)
configure_effective_build_profile build-firmware >/dev/null
[ "$AUTO_COMPLETE_FIRST_PASS" = 1 ] \
  || fail "external-storage nRF52 complete first pass was not scheduled"
[ "$AUTO_PUBLISH_REDUCED_SECOND_PASS" = 0 ] \
  || fail "external-storage nRF52 incorrectly scheduled a redundant artifact"

BUILD_PROFILE_OVERRIDE=auto
BUILD_PROFILE_EXPLICIT=0
RESOLVED_BUILD_TARGETS=(nrf_sensor)
configure_effective_build_profile build-firmware >/dev/null
[ "$BUILD_PROFILE_EFFECTIVE" = auto ] \
  || fail "nRF52 sensor did not retain its ordinary auto profile"
[ "$AUTO_COMPLETE_FIRST_PASS" = 0 ] \
  || fail "non-repeater nRF52 target incorrectly scheduled OTA profile passes"
[ "$AUTO_PUBLISH_REDUCED_SECOND_PASS" = 0 ] \
  || fail "non-repeater nRF52 target incorrectly scheduled two artifacts"

calls=()
build_firmware() {
  calls+=("$1:$BUILD_PROFILE_EFFECTIVE:$SKIP_DECLARED_REDUCTIONS:$FIRMWARE_OUTPUT_ENV_NAME")
  if [ "${#calls[@]}" -eq 1 ]; then
    return 42
  fi
  return 0
}

AUTO_PREFER_FULL_BUILD=0
AUTO_COMPLETE_FIRST_PASS=1
AUTO_REDUCED_FALLBACK_TARGET=nrf_repeater_lora_ota_no_external_sensors
BUILD_PROFILE_EFFECTIVE=auto
FIRMWARE_FILENAME_INFIX=""
SKIP_DECLARED_REDUCTIONS=0
run_auto_two_pass_build nrf_repeater_lora_ota_no_external_sensors >/dev/null

[ "${#calls[@]}" -eq 2 ] || fail "size overflow did not run exactly two passes"
[[ "${calls[0]}" == *:auto:1:* ]] || fail "pass 1 unexpectedly applied reductions"
[[ "${calls[1]}" == *:standard:0:* ]] || fail "pass 2 did not apply standard reductions"

calls=()
build_firmware() {
  calls+=("$1:$BUILD_PROFILE_EFFECTIVE:$SKIP_DECLARED_REDUCTIONS:$FIRMWARE_OUTPUT_ENV_NAME")
  return 0
}

AUTO_PUBLISH_REDUCED_SECOND_PASS=1
BUILD_PROFILE_EFFECTIVE=auto
FIRMWARE_FILENAME_INFIX=""
FIRMWARE_OUTPUT_ENV_NAME=""
SKIP_DECLARED_REDUCTIONS=0
run_auto_two_pass_build nrf_repeater_lora_ota_no_external_sensors >/dev/null

[ "${#calls[@]}" -eq 2 ] \
  || fail "successful internal-flash nRF52 build did not publish both artifacts"
[[ "${calls[0]}" == *:auto:1:nrf_repeater ]] \
  || fail "complete nRF52 artifact did not use the feature-rich output name"
[[ "${calls[1]}" == *:standard:0: ]] \
  || fail "reduced nRF52 artifact did not use the reduced target name"

is_rak_i2c_voltage_monitor_ota_target \
  RAK_3401_repeater_lora_ota_no_external_sensors \
  || fail "RAK3401 reduced OTA target did not retain voltage monitors"
is_rak_i2c_voltage_monitor_ota_target \
  RAK_4631_repeater_bridge_rs232_serial2_lora_ota_no_external_sensors \
  || fail "RAK4631 reduced OTA target did not retain voltage monitors"
if is_rak_i2c_voltage_monitor_ota_target \
    Heltec_t114_repeater_lora_ota_no_external_sensors; then
  fail "non-RAK reduced OTA target incorrectly retained voltage monitors"
fi

verify_reduced_sensor_flags() {
  local env_name=$1
  local expect_ina=$2
  local PLATFORMIO_BUILD_FLAGS=""
  local PLATFORMIO_BUILD_UNFLAGS=""
  local -a BUILD_REDUCTIONS=()

  PIO_ENV_OTA_BY_NAME[$env_name]=1
  apply_lora_ota_no_external_sensors_profile "$env_name"
  [[ "$PLATFORMIO_BUILD_FLAGS" == *-UENV_INCLUDE_BME280* ]] \
    || fail "$env_name did not omit environmental sensors"
  if [ "$expect_ina" = 1 ]; then
    [[ "$PLATFORMIO_BUILD_FLAGS" == *-DENV_INCLUDE_INA219=1* ]] \
      || fail "$env_name did not retain INA monitor support"
    [[ "$PLATFORMIO_BUILD_FLAGS" != *-UENV_INCLUDE_INA219* ]] \
      || fail "$env_name both retained and omitted INA monitor support"
  else
    [[ "$PLATFORMIO_BUILD_FLAGS" == *-UENV_INCLUDE_INA219* ]] \
      || fail "$env_name incorrectly retained INA monitor support"
  fi
}

verify_reduced_sensor_flags \
  RAK_3401_repeater_lora_ota_no_external_sensors 1
verify_reduced_sensor_flags \
  Heltec_t114_repeater_lora_ota_no_external_sensors 0

# A qualified Full Companion is the one canonical release artifact for its
# board. The individual USB/BLE transports remain explicitly buildable, but
# must not be republished beside the Full image.
SUPPORTED_PIO_ENVS=(
  Station_G2_companion_radio_usb
  Station_G2_companion_radio_ble
  Station_G2_companion_radio_wifi
  Station_G2_companion_radio_full
)
for env_name in "${SUPPORTED_PIO_ENVS[@]}"; do
  PIO_ENV_PLATFORM_BY_NAME[$env_name]=ESP32_PLATFORM
  PIO_ENV_BOARD_BY_NAME[$env_name]=station-g2
done
mapfile -t companion_release_targets < <(
  print_release_firmware_targets get-companion-firmwares-to-build
)
[ "${#companion_release_targets[@]}" -eq 1 ] \
  || fail "Station G2 release did not collapse transports to one Full Companion"
[ "${companion_release_targets[0]}" = Station_G2_companion_radio_full ] \
  || fail "Station G2 release selected a non-Full Companion artifact"

# A WiFi base's final -UENABLE_OTA must not win over the Full Companion's
# source-only OTA overlay. That mismatch compiles out both the TCP terminal and
# seeder while still leaving a superficially valid binary.
pio_env_option_contains() {
  [ "$2" = build_flags ] && [ "$3" = -UENABLE_OTA ]
}
PLATFORMIO_BUILD_FLAGS=""
PLATFORMIO_BUILD_UNFLAGS=""
PLATFORMIO_EXTRA_SCRIPTS=""
unset MESHCORE_FORCE_LORA_OTA
apply_companion_radio_full_profile \
  Station_G2_companion_radio_full Station_G2_companion_radio_wifi
apply_lora_ota_flag_order_fix \
  Station_G2_companion_radio_full Station_G2_companion_radio_wifi
[ "${MESHCORE_FORCE_LORA_OTA:-}" = 1 ] \
  || fail "ESP32 Full Companion did not force its OTA source overlay"
[[ "$PLATFORMIO_EXTRA_SCRIPTS" == *"pre:scripts/force_lora_ota.py"* ]] \
  || fail "ESP32 Full Companion did not install the OTA flag-order fix"

# The same protection applies to explicitly requested standalone OTA
# transports even though the canonical release publishes the Full replacement.
PIO_ENV_OTA_BY_NAME[Station_G2_companion_radio_wifi]=1
PLATFORMIO_EXTRA_SCRIPTS=""
unset MESHCORE_FORCE_LORA_OTA
apply_lora_ota_flag_order_fix \
  Station_G2_companion_radio_wifi Station_G2_companion_radio_wifi
[ "${MESHCORE_FORCE_LORA_OTA:-}" = 1 ] \
  || fail "standalone ESP32 OTA Companion did not force its OTA overlay"
[[ "$PLATFORMIO_EXTRA_SCRIPTS" == *"pre:scripts/force_lora_ota.py"* ]] \
  || fail "standalone ESP32 OTA Companion omitted the OTA flag-order fix"

# Full capability contracts must use functional strings that survive release
# builds with optional debug logging disabled.
BUILD_PROFILE_FOR_TARGET=full
BUILD_CAPABILITIES=()
BUILD_REDUCTIONS=()
BUILD_EXPECTATIONS=()
declare_build_capability_contract \
  Station_G2_companion_radio_full ESP32_PLATFORM
expectations=" ${BUILD_EXPECTATIONS[*]} "
[[ "$expectations" == *"companion.network_terminal=USB currently owns the Full Companion terminal"* ]] \
  || fail "ESP32 Full contract uses no stable network-terminal evidence"
[[ "$expectations" == *"companion.wifi_ota_seeder=OTA seeder listening on :"* ]] \
  || fail "ESP32 Full contract omitted the WiFi seeder"
[[ "$expectations" != *"Full Companion terminal listening"* ]] \
  || fail "ESP32 Full contract still depends on optional debug logging"

PIO_ENV_PLATFORM_BY_NAME[RAK_3401_companion_radio_full]=NRF52_PLATFORM
BUILD_CAPABILITIES=()
BUILD_REDUCTIONS=()
BUILD_EXPECTATIONS=()
declare_build_capability_contract \
  RAK_3401_companion_radio_full NRF52_PLATFORM
expectations=" ${BUILD_EXPECTATIONS[*]} "
[[ "$expectations" == *"companion.usb_mota_source=ota folder on"* ]] \
  || fail "nRF52 Full contract omitted USB folder seeding"
[[ "$expectations" != *"companion.network_terminal="* ]] \
  || fail "nRF52 Full contract promised an ESP32-only network terminal"

echo "test_build_profiles: OK"
