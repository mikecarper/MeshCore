#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
source build.sh

fail() {
  echo "test_build_profiles: $*" >&2
  exit 1
}

[ "$OPTION3_BUILD_WORKERS" -eq 1 ] \
  || fail "logging matrix permits concurrent PlatformIO target builds"

# Interactive builds can carry the complete version from the newest artifact
# in out/, while an empty directory falls through to the existing tag-derived
# editable prompt.
version_test_dir=$(mktemp -d)
trap 'rm -rf -- "$version_test_dir"' EXIT
touch "$version_test_dir/RAK_4631_repeater-v1.17.1-dev-1234abcd.uf2"
[ "$(get_latest_output_firmware_version "$version_test_dir")" = v1.17.1-dev ] \
  || fail "did not read a semantic firmware version from output"
sleep 0.01
touch "$version_test_dir/RAK_4631_repeater-full-ota-1.17.1.5-halo-keymind-cascade-dev-69ded9d6-merged.bin"
[ "$(get_latest_output_firmware_version "$version_test_dir")" \
    = 1.17.1.5-halo-keymind-cascade-dev ] \
  || fail "did not prefer the newest custom firmware version from output"
rm -f -- "$version_test_dir"/*
if get_latest_output_firmware_version "$version_test_dir" >/dev/null; then
  fail "empty output directory unexpectedly supplied a firmware version"
fi

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
require("ThinkNode_M5_companion_radio_full", "build_flags", "SERIAL_TX=43")
require("ThinkNode_M2_companion_radio_full", "build_flags", "SERIAL_RX=44")
require("Xiao_S3_WIO_companion_radio_full", "build_flags", "SERIAL_TX=D6")
require("ThinkNode_M7_companion_radio_full", "build_flags", "ETHERNET_USE_CH390")
require("ThinkNode_M7_companion_radio_full", "build_src_filter", "helpers/ethernet/ch390")
require("ThinkNode_M7_companion_radio_full", "lib_deps", "ESP32-CH390")
require("RAK_4631_companion_radio_full", "build_flags", "ETHERNET_USE_RAK13800")
require("RAK_4631_companion_radio_full", "build_src_filter", "helpers/ethernet/RAK13800")
require("RAK_4631_companion_radio_full", "lib_deps", "RAK13800-W5100S")
require("Heltec_E290_companion_usb_ble", "build_flags", "ENABLE_USB_INTERFACE")
require("Heltec_E290_companion_usb_ble", "build_flags", "BLE_PIN_CODE=123456")
require("Heltec_T190_companion_radio_usb_ble_", "build_flags", "ENABLE_USB_INTERFACE")
require("Heltec_T190_companion_radio_usb_ble_", "build_flags", "BLE_PIN_CODE=123456")

for env_name in (
    "Heltec_t114_without_display_repeater",
    "Heltec_t114_repeater",
    "Heltec_t114_repeater_lora_ota_no_external_sensors",
    "RAK_3112_repeater",
    "RAK_11310_repeater",
    "ProMicro_repeater",
    "waveshare_rp2040_lora_repeater",
    "solarxiao_30S_repeater",
    "solarxiao_33S_repeater",
    "Heltec_v3_repeater",
    "Heltec_WSL3_repeater",
    "Heltec_t096_repeater",
    "Heltec_t096_repeater_lora_ota_no_external_sensors",
    "RAK_4631_repeater",
    "RAK_4631_repeater_lora_ota_no_external_sensors",
    "LilyGo_TLora_V2_1_1_6_repeater",
):
    require(env_name, "build_flags", "WITH_RS232_BRIDGE=")
    require(env_name, "build_flags", "RS232_BRIDGE_MERGED=1")
    require(env_name, "build_src_filter", "helpers/bridges/RS232Bridge.cpp")

require("RAK_4631_repeater", "build_flags", "WITH_RS232_BRIDGE_ALT=Serial1")
require("RAK_4631_repeater", "build_flags", "WITH_RS232_BRIDGE_UART=2")
reject("wio-e5_repeater", "build_flags", "WITH_RS232_BRIDGE=")

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

# Ordinary USB-loggable roles compile their historical logging profile into
# the canonical artifact. OTA receivers, KISS, and BLE keep their distinct
# stream/partition contracts. A standard ESP32 artifact still embeds logging
# when the same target also has an expanded FULL profile.
PIO_ENV_PLATFORM_BY_NAME[wio-e5-mini_repeater]=STM32_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[nrf_kiss_modem]=NRF52_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[nrf_companion_radio_ble]=NRF52_PLATFORM
uses_merged_standard_usb_logging nrf_sensor \
  || fail "ordinary nRF52 sensor omitted merged USB logging"
uses_merged_standard_usb_logging wio-e5-mini_repeater \
  || fail "size-constrained STM32 target omitted merged packet logging"
if uses_merged_standard_usb_logging nrf_repeater_lora_ota_no_external_sensors; then
  fail "LoRa OTA receiver incorrectly merged USB logging"
fi
if uses_merged_standard_usb_logging nrf_kiss_modem; then
  fail "KISS target incorrectly merged plaintext USB logging"
fi
if uses_merged_standard_usb_logging nrf_companion_radio_ble; then
  fail "BLE Companion incorrectly merged USB logging"
fi
uses_merged_standard_usb_logging esp_repeater \
  || fail "standard ESP32 target omitted logging because FULL also exists"

PLATFORMIO_BUILD_FLAGS=""
MESHDEBUG_OVERRIDE=""
PACKET_LOGGING_OVERRIDE=""
DISABLE_DEBUG=0
apply_merged_standard_usb_logging_profile nrf_sensor
[[ "$PLATFORMIO_BUILD_FLAGS" == *-DMESH_PACKET_LOGGING=1* ]] \
  || fail "merged profile omitted packet logging"
[[ "$PLATFORMIO_BUILD_FLAGS" == *-DMESH_DEBUG=1* ]] \
  || fail "merged profile omitted mesh diagnostics"
PLATFORMIO_BUILD_FLAGS=""
apply_merged_standard_usb_logging_profile wio-e5-mini_repeater
[[ "$PLATFORMIO_BUILD_FLAGS" == *-DMESH_PACKET_LOGGING=1* ]] \
  || fail "constrained merged profile omitted packet logging"
[[ "$PLATFORMIO_BUILD_FLAGS" != *-DMESH_DEBUG=1* ]] \
  || fail "constrained merged profile enabled oversized mesh diagnostics"
if declare -f run_logging_matrix_build_targets \
    | grep -q 'FIRMWARE_FILENAME_INFIX="logging"'; then
  fail "logging matrix still emits a separate standard logging artifact"
fi
if declare -f get_firmware_filename | grep -q 'filename_infix="logging"'; then
  fail "explicit packet logging still renames the ordinary artifact"
fi
FIRMWARE_FILENAME_INFIX=""
ESP32_FULL_BUILD=0
PACKET_LOGGING_OVERRIDE="on"
MQTT_BRIDGE_OVERRIDE="off"
[ "$(get_firmware_filename nrf_sensor vtest)" = "nrf_sensor-vtest" ] \
  || fail "packet logging still creates a separately named artifact"
PACKET_LOGGING_OVERRIDE=""
MQTT_BRIDGE_OVERRIDE=""
if declare -f run_logging_matrix_build_targets | grep -q 'Profile 2'; then
  fail "logging matrix still labels a separate Profile 2"
fi

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
  Station_G2_terminal_chat
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

# Full Companion's text terminal also replaces a separate Terminal Chat image
# in bulk builds. Cover direct ESP32/nRF52 names and the canonical Heltec names
# whose hardware/revision suffixes differ from their Terminal Chat targets.
PIO_ENV_PLATFORM_BY_NAME[RAK_3401_terminal_chat]=NRF52_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[RAK_3401_companion_radio_full]=NRF52_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[heltec_v4_terminal_chat]=ESP32_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[heltec_v4_2_v4_3_companion_radio_full_femon]=ESP32_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[heltec_v4_tft_terminal_chat]=ESP32_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[heltec_v4_tft_companion_radio_full_femon]=ESP32_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[heltec_tracker_v2_terminal_chat]=ESP32_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[heltec_tracker_v2_companion_radio_full_femon]=ESP32_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[PicoW_terminal_chat]=RP2040_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[PicoW_companion_radio_usb]=RP2040_PLATFORM
PIO_ENV_BOARD_BY_NAME[PicoW_terminal_chat]=rpipicow
PIO_ENV_BOARD_BY_NAME[PicoW_companion_radio_usb]=rpipicow

[ "$(get_terminal_chat_full_companion_replacement RAK_3401_terminal_chat)" \
    = RAK_3401_companion_radio_full ] \
  || fail "nRF52 Terminal Chat did not map to Full Companion"
[ "$(get_terminal_chat_full_companion_replacement heltec_v4_terminal_chat)" \
    = heltec_v4_2_v4_3_companion_radio_full_femon ] \
  || fail "Heltec V4 Terminal Chat did not map to its canonical Full Companion"
[ "$(get_terminal_chat_full_companion_replacement heltec_v4_tft_terminal_chat)" \
    = heltec_v4_tft_companion_radio_full_femon ] \
  || fail "Heltec V4 TFT Terminal Chat did not map to Full Companion"
[ "$(get_terminal_chat_full_companion_replacement heltec_tracker_v2_terminal_chat)" \
    = heltec_tracker_v2_companion_radio_full_femon ] \
  || fail "Heltec Tracker V2 Terminal Chat did not map to Full Companion"
is_redundant_bulk_build_target Station_G2_terminal_chat \
  || fail "ESP32 Terminal Chat remained in bulk builds beside Full Companion"
is_redundant_bulk_build_target RAK_3401_terminal_chat \
  || fail "nRF52 Terminal Chat remained in bulk builds beside Full Companion"
[ "$(get_terminal_chat_companion_replacement PicoW_terminal_chat)" \
    = PicoW_companion_radio_usb ] \
  || fail "RP2040 Terminal Chat did not map to USB Companion"
is_redundant_bulk_build_target PicoW_terminal_chat \
  || fail "Terminal Chat remained beside its matching USB Companion"

PIO_ENV_PLATFORM_BY_NAME[RAK_4631_companion_radio_ethernet]=NRF52_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[RAK_4631_companion_radio_full]=NRF52_PLATFORM
[ "$(get_nrf52_full_companion_replacement \
    RAK_4631_companion_radio_ethernet)" = RAK_4631_companion_radio_full ] \
  || fail "RAK4631 Ethernet Companion did not map to Full Companion"
PIO_ENV_PLATFORM_BY_NAME[ThinkNode_M2_companion_radio_serial]=ESP32_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[ThinkNode_M2_companion_radio_full]=ESP32_PLATFORM
[ "$(get_esp32_full_companion_replacement \
    ThinkNode_M2_companion_radio_serial)" = ThinkNode_M2_companion_radio_full ] \
  || fail "serial Companion did not map to Full Companion"

[ "$(get_combined_usb_ble_companion_replacement \
    Heltec_E290_companion_usb)" = Heltec_E290_companion_usb_ble ] \
  || fail "E290 USB Companion did not map to USB+BLE Companion"
[ "$(get_combined_usb_ble_companion_replacement \
    Heltec_T190_companion_radio_ble_)" \
    = Heltec_T190_companion_radio_usb_ble_ ] \
  || fail "T190 BLE Companion did not map to USB+BLE Companion"

if is_redundant_bulk_build_target RAK_4631_repeater_ethernet \
    || is_redundant_bulk_build_target RAK_4631_room_server_ethernet; then
  fail "RAK4631 repeater/room Ethernet artifact was incorrectly merged"
fi

[ "$(get_merged_rs232_repeater_replacement \
    RAK_4631_repeater_bridge_rs232_serial1)" = RAK_4631_repeater ] \
  || fail "RAK4631 Serial1 bridge did not map to merged repeater"
[ "$(get_merged_rs232_repeater_replacement \
    RAK_4631_repeater_bridge_rs232_serial2_lora_ota_no_external_sensors)" \
    = RAK_4631_repeater_lora_ota_no_external_sensors ] \
  || fail "RAK4631 Serial2 OTA bridge did not map to merged repeater"
[ "$(get_merged_rs232_repeater_replacement \
    Heltec_t114_repeater_bridge_rs232)" = Heltec_t114_repeater ] \
  || fail "T114 RS232 bridge did not map to merged repeater"
[ "$(get_merged_rs232_repeater_replacement \
    Heltec_t096_repeater_bridge_rs232)" = Heltec_t096_repeater ] \
  || fail "T096 RS232 bridge did not map to merged repeater"
is_redundant_bulk_build_target Heltec_v3_repeater_bridge_rs232 \
  || fail "merged RS232 bridge remained in canonical bulk builds"
if is_redundant_bulk_build_target wio-e5-repeater_bridge_rs232; then
  fail "capacity-constrained Wio-E5 RS232 bridge was incorrectly merged"
fi

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
