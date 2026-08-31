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

# Reduced RAK profiles all retain the compact INA set, but GPS depends on
# whether the bridge already owns Serial1. Keep those independent contracts so
# a valid Serial1 image cannot fail release qualification for an absent GPS
# marker, and so its manifest never advertises impossible hardware support.
for rak_target in \
  RAK_3401_repeater_lora_ota_no_external_sensors \
  RAK_4631_repeater_lora_ota_no_external_sensors \
  RAK_4631_repeater_bridge_rs232_serial1_lora_ota_no_external_sensors \
  RAK_4631_repeater_bridge_rs232_serial2_lora_ota_no_external_sensors; do
  is_rak_i2c_voltage_monitor_ota_target "$rak_target" \
    || fail "$rak_target lost the retained INA contract"
done
for rak_target in \
  RAK_3401_repeater_lora_ota_no_external_sensors \
  RAK_4631_repeater_lora_ota_no_external_sensors \
  RAK_4631_repeater_bridge_rs232_serial2_lora_ota_no_external_sensors; do
  is_rak_gps_retaining_ota_target "$rak_target" \
    || fail "$rak_target lost its compatible GPS contract"
done
if is_rak_gps_retaining_ota_target \
    RAK_4631_repeater_bridge_rs232_serial1_lora_ota_no_external_sensors; then
  fail "RAK4631 Serial1 RS-232 target incorrectly promises GPS"
fi

# The expanded-profile marker applied below must select a real, nonzero
# boot-local setup window. Keep the build overlay, WebConfig compile-time
# mapping, and pure timing policy tied together.
grep -Fq \
  'static const uint32_t kFullSetupApWindowMs = 30UL * 60UL * 1000UL;' \
  src/helpers/WebConfigBatch.h \
  || fail "Full setup-AP window is no longer exactly 30 minutes"
grep -Fq \
  '#define WEBCONFIG_UNCONFIGURED_SETUP_TIMEOUT_MS WebConfigBatch::kFullSetupApWindowMs' \
  src/helpers/esp32/WebConfigServer.h \
  || fail "expanded Full profile no longer enables the unconfigured-WiFi cutoff"

requires_esp32_arduino3_framework \
  heltec_rc32_repeater heltec_rc32_repeater \
  || fail "RC32 omitted its Arduino-ESP32 3.x package preflight"
if requires_esp32_arduino3_framework \
    Heltec_v3_repeater Heltec_v3_repeater; then
  fail "ordinary ESP32 target unexpectedly selected Arduino-ESP32 3.x"
fi

grep -Fq 'void HeltecRC32Board::onBootComplete()' \
  variants/heltec_rc32/HeltecRC32Board.cpp \
  || fail "RC32 omitted its post-boot clock transition"
grep -Fq 'current_mhz > ESP32_POST_BOOT_CPU_FREQ' \
  variants/heltec_rc32/HeltecRC32Board.cpp \
  || fail "RC32 post-boot clock transition overrides lower power-saving clocks"
grep -Fq 'return ESP32_POST_BOOT_CPU_FREQ;' examples/companion_radio/main.cpp \
  || fail "Companion power saving ignores the RC32 post-boot nominal clock"
grep -Fq 'board.onBootComplete();' examples/simple_sensor/main.cpp \
  || fail "sensor firmware never applies board post-boot policy"

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
require("heltec_v4_expansionkit_tft_companion_radio_full_femon", "platform", "platformio/espressif32@6.11.0")
require("heltec_v4_expansionkit_tft_companion_radio_full_femon", "build_flags", "ARDUINO_USB_MODE=1")
reject("heltec_v4_expansionkit_tft_companion_radio_full_femon", "build_flags", "ARDUINO_USB_MODE=0")
reject("heltec_v4_expansionkit_tft_companion_radio_full_femon", "platform", "55.03.311")
reject("heltec_v4_expansionkit_tft_companion_radio_full_femon", "platform_packages", "esp32-core-3.3.11")

# ESP32 Full uses the board-specific Arduino-ESP32 2.x recipe and one USB TTY. The
# independently qualified RC32 stays on Arduino 3.x, but it must not enable a
# second CDC interface either.
for env_name in (
    "LilyGo_TBeam_1W_companion_radio_full",
    "RAK_3112_companion_radio_full",
    "Station_G2_companion_radio_full",
    "ThinkNode_M2_companion_radio_full",
    "ThinkNode_M5_companion_radio_full",
    "ThinkNode_M7_companion_radio_full",
    "Xiao_S3_WIO_companion_radio_full",
    "heltec_v4_r8_companion_radio_full",
    "heltec_v4_r8_tft_companion_radio_full",
    "meshnology_w12_companion_radio_full",
    "heltec_v4_2_v4_3_companion_radio_full_femon",
    "heltec_v4_3_companion_radio_full_femoff",
    "heltec_v4_expansionkit_tft_companion_radio_full_femon",
    "heltec_v4_tft_companion_radio_full_femon",
    "heltec_v4_3_tft_companion_radio_full_femoff",
    "nibble_zero_connect_companion_radio_full_",
    "nibble_screen_connect_companion_radio_full_",
    "Station_G3_ESP32_companion_radio_full",
    "Heltec_v3_companion_radio_full",
    "heltec_tracker_v2_companion_radio_full_femon",
):
    require(env_name, "platform", "platformio/espressif32@6.11.0")
    reject(env_name, "platform", "55.03.311")
    reject(env_name, "build_flags", "MESH_DUAL_CDC_LOGGING")
    reject(env_name, "build_flags", "COMPANION_FEATURE_DEDICATED_USB_LOGGING")
    reject(env_name, "build_flags", "CFG_TUD_CDC=2")

for env_name in (
    "heltec_rc32_without_display_companion_radio_full",
    "heltec_rc32_companion_radio_full",
):
    require(env_name, "platform", "55.03.311")
    reject(env_name, "build_flags", "MESH_DUAL_CDC_LOGGING")
    reject(env_name, "build_flags", "COMPANION_FEATURE_DEDICATED_USB_LOGGING")
    reject(env_name, "build_flags", "CFG_TUD_CDC=2")

# V3/V4 Full are the one canonical Companion image for each base OLED layout,
# including the former separately published direct-WiFi-MQTT capability.
for env_name in (
    "Heltec_v3_companion_radio_full",
    "heltec_v4_2_v4_3_companion_radio_full_femon",
):
    require(env_name, "build_flags", "WITH_MQTT_BRIDGE=1")
    require(env_name, "build_src_filter", "helpers/bridges/MQTTBridge.cpp")
    require(env_name, "lib_deps", "PsychicMqttClient")

rc32_repeater = "heltec_rc32_repeater"
require(rc32_repeater, "platform", "55.03.311")
require(rc32_repeater, "platform_packages", "esp32-core-3.3.11.tar.xz")
require(rc32_repeater, "build_flags", "ESP32_POST_BOOT_CPU_FREQ=160")
require(rc32_repeater, "build_flags", "RC32_PERIPHERAL_WARMUP_MS=100")
require(rc32_repeater, "build_flags", "SX126X_ALLOW_RECOVERABLE_INIT_STATUS=1")
reject(rc32_repeater, "build_flags", "ESP32_CPU_FREQ=160")

# Environmental telemetry must follow the verified connector bus without
# creating a second Wire instance on the same pins as the board bus.
require("Heltec_t096_companion_radio_usb_femon", "build_flags", "ENV_PIN_SDA=PIN_WIRE1_SDA")
require("Heltec_t096_companion_radio_usb_femon", "build_flags", "ENV_PIN_SCL=PIN_WIRE1_SCL")
require("Heltec_v3_companion_radio_wifi", "build_flags", "ENV_PIN_SDA=33")
require("Heltec_v3_companion_radio_wifi", "build_flags", "ENV_PIN_SCL=34")

for env_name in (
    "heltec_rc32_without_display_sensor",
    "heltec_rc32_sensor",
):
    require(env_name, "build_flags", "PIN_BOARD_SDA=21")
    require(env_name, "build_flags", "PIN_BOARD_SCL=18")
    reject(env_name, "build_flags", "ENV_PIN_")

require("meshnology_w12_sensor", "build_flags", "PIN_BOARD_SDA=17")
require("meshnology_w12_sensor", "build_flags", "PIN_BOARD_SCL=18")
reject("meshnology_w12_sensor", "build_flags", "ENV_PIN_")

for env_name in (
    "heltec_v4_sensor",
    "heltec_v4_2_v4_3_companion_radio_full_femon",
):
    require(env_name, "build_flags", "PIN_BOARD_SDA=17")
    require(env_name, "build_flags", "PIN_BOARD_SCL=18")
    require(env_name, "build_flags", "ENV_PIN_SDA=4")
    require(env_name, "build_flags", "ENV_PIN_SCL=3")

for env_name in (
    "heltec_v4_tft_sensor",
    "heltec_v4_expansionkit_tft_companion_radio_ble_femon",
    "heltec_v4_expansionkit_tft_companion_radio_full_femon",
    "heltec_v4_tft_companion_radio_full_femon",
):
    require(env_name, "build_flags", "PIN_BOARD_SDA=4")
    require(env_name, "build_flags", "PIN_BOARD_SCL=3")
    reject(env_name, "build_flags", "ENV_PIN_")

for env_name in (
    "RAK_3112_sensor",
    "RAK_3112_companion_radio_full",
):
    require(env_name, "build_flags", "PIN_BOARD_SDA=9")
    require(env_name, "build_flags", "PIN_BOARD_SCL=40")
    require(env_name, "build_flags", "ENV_PIN_SDA=17")
    require(env_name, "build_flags", "ENV_PIN_SCL=18")
    reject(env_name, "build_flags", "ENV_PIN_SDA=33")
    reject(env_name, "build_flags", "ENV_PIN_SCL=34")

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

# Every qualified ESP32 Companion layout publishes one Full image even when
# its historical recipe had no separately named WiFi target. The transport
# aliases remain directly buildable, but canonical release resolution must
# replace each with that exact board's Full target.
init_project_context >/dev/null

# Arduino-ESP32 3.x OTA-only targets that have always declared larger A/B
# slots must be checked against their real partition table, not the legacy
# 1.25 MiB portable ceiling. Keep that exception OTA-only so ordinary RC32
# standard artifacts still retain the portable release contract.
for rc32_ota_env in \
    heltec_rc32_repeater_lora_ota_no_external_sensors \
    heltec_rc32_without_display_repeater_lora_ota_no_external_sensors; do
  if requires_esp32_portable_size_ceiling "$rc32_ota_env"; then
    fail "$rc32_ota_env incorrectly inherited the legacy portable size ceiling"
  fi
done
requires_esp32_portable_size_ceiling heltec_rc32_repeater \
  || fail "ordinary RC32 repeater lost the portable size ceiling"

while IFS='|' read -r source_env full_env build_base; do
  [ "${PIO_ENV_PLATFORM_BY_NAME[$full_env]:-}" = ESP32_PLATFORM ] \
    || fail "$full_env was not registered as ESP32 Full Companion"
  [ "$(get_pio_build_env "$full_env")" = "$build_base" ] \
    || fail "$full_env did not retain its exact board recipe"
  [ "$(get_esp32_full_companion_replacement "$source_env")" = "$full_env" ] \
    || fail "$source_env did not map to $full_env"
  is_redundant_bulk_build_target "$source_env" \
    || fail "$source_env remained beside its Full Companion"
done <<'FULL_COMPANION_SPECS'
LilyGo_Tlora_C6_companion_radio_ble_|LilyGo_Tlora_C6_companion_radio_full_|LilyGo_Tlora_C6_companion_radio_ble_
Meshimi_companion_radio_ble_|Meshimi_companion_radio_full_|Meshimi_companion_radio_ble_
WHY2025_badge_companion_radio_ble_|WHY2025_badge_companion_radio_full_|WHY2025_badge_companion_radio_ble_
Xiao_C6_companion_radio_ble_|Xiao_C6_companion_radio_full_|Xiao_C6_companion_radio_ble_
heltec_v4_3_expansionkit_tft_companion_radio_ble_femoff|heltec_v4_expansionkit_tft_companion_radio_full_femon|heltec_v4_expansionkit_tft_companion_radio_full_femon
Generic_ESPNOW_comp_radio_usb|Generic_ESPNOW_companion_radio_full|Generic_ESPNOW_comp_radio_usb
Heltec_E290_companion_usb_ble|Heltec_E290_companion_radio_full|Heltec_E290_companion_usb_ble
Heltec_T190_companion_radio_usb_ble_|Heltec_T190_companion_radio_full_|Heltec_T190_companion_radio_usb_ble_
SenseCapIndicator-ESPNow_comp_radio_usb|SenseCapIndicator-ESPNow_companion_radio_full|SenseCapIndicator-ESPNow_comp_radio_usb
SenseCapIndicator-LoRa_comp_radio_usb_wifi|SenseCapIndicator-LoRa_companion_radio_full|SenseCapIndicator-LoRa_comp_radio_usb_wifi
FULL_COMPANION_SPECS

# The Full overlay, not the historical transport used as its build base, owns
# the capability contract. Verify every registered ESP32 Full target receives
# an ordinary WiFi Companion transport even when its base was USB- or BLE-only.
for full_env in "${SUPPORTED_PIO_ENVS[@]}"; do
  is_esp32_companion_radio_full_target "$full_env" || continue
  pio_env=$(get_pio_build_env "$full_env")
  PLATFORMIO_BUILD_FLAGS=""
  PLATFORMIO_BUILD_UNFLAGS=""
  PLATFORMIO_BUILD_SRC_FILTER=""
  BUILD_REDUCTIONS=()
  apply_companion_radio_full_profile "$full_env" "$pio_env"
  apply_esp32_full_async_tcp_profile "$full_env"

  if ! pio_env_option_contains "$pio_env" build_flags WIFI_SSID \
      && [[ "$PLATFORMIO_BUILD_FLAGS" != *"WIFI_SSID"* ]]; then
    fail "$full_env Full Companion omitted ordinary WiFi credentials"
  fi
  if ! pio_env_option_contains "$pio_env" build_src_filter "helpers/esp32/*.cpp" \
      && ! pio_env_option_contains "$pio_env" build_src_filter \
          "helpers/esp32/SerialWifiInterface.cpp" \
      && [[ "$PLATFORMIO_BUILD_SRC_FILTER" \
          != *"helpers/esp32/SerialWifiInterface.cpp"* ]]; then
    fail "$full_env Full Companion omitted SerialWifiInterface"
  fi
  [[ "$PLATFORMIO_BUILD_FLAGS" == *"WIFI_OTA_SEEDER=1"* ]] \
    || fail "$full_env Full Companion omitted its WiFi runtime overlay"
  [[ "$PLATFORMIO_BUILD_FLAGS" == *"MESHCORE_EXPANDED_PARTITION_PROFILE=1"* ]] \
    || fail "$full_env Full Companion omitted its expanded runtime profile"
  case "${full_env,,}" in
    sensecapindicator-espnow_companion_radio_full|\
    sensecapindicator-lora_companion_radio_full)
      [ "${MESHCORE_ESP32_FULL_PARTITION_TABLE:-}" \
          = "variants/sensecap_indicator-espnow/dual_ota_2560k_preserve_spiffs.csv" ] \
        || fail "$full_env omitted the Indicator preserve-SPIFFS table"
      [[ "$PLATFORMIO_BUILD_FLAGS" == *"INDICATOR_WIFI_FONT_RECOVERY=1"* ]] \
        || fail "$full_env omitted WiFi font recovery"
      ;;
    *)
      [ -z "${MESHCORE_ESP32_FULL_PARTITION_TABLE:-}" ] \
        || fail "$full_env inherited another board's required partition table"
      ;;
  esac
  [[ "$PLATFORMIO_BUILD_FLAGS" == *"CONFIG_ASYNC_TCP_STACK_SIZE=4096"* ]] \
    || fail "$full_env Full Companion omitted its bounded AsyncTCP stack"
  [[ "$PLATFORMIO_BUILD_FLAGS" == *"CONFIG_ASYNC_TCP_RUNNING_CORE=1"* ]] \
    || fail "$full_env Full Companion omitted its AsyncTCP core isolation"
  [[ "$PLATFORMIO_BUILD_FLAGS" == *"ASYNCWEBSERVER_USE_CHUNK_INFLIGHT=0"* ]] \
    || fail "$full_env Full Companion omitted its slow-client response profile"

  case "${full_env,,}" in
    generic_espnow_companion_radio_full|\
    sensecapindicator-espnow_companion_radio_full)
      pio_env_option_contains "$pio_env" build_flags "MESH_PRIMARY_ESPNOW=1" \
        || fail "$full_env lost its primary ESP-NOW radio marker"
      [[ "$PLATFORMIO_BUILD_UNFLAGS" != *"MESH_PRIMARY_ESPNOW"* ]] \
        || fail "$full_env removed its primary ESP-NOW radio marker"
      [[ "$PLATFORMIO_BUILD_FLAGS" == *"MESH_ESPNOW_RADIO=1"* ]] \
        || fail "$full_env omitted ESP-NOW/WiFi coexistence policy"
      ;;
  esac
done
unset PLATFORMIO_BUILD_FLAGS PLATFORMIO_BUILD_UNFLAGS \
  PLATFORMIO_BUILD_SRC_FILTER MESHCORE_ESP32_FULL_PARTITION_TABLE

# The bounded AsyncTCP task stack belongs to every ESP32 Full profile, including
# expanded non-Companion builds, but must not leak into ordinary or nRF52
# recipes.
ESP32_FULL_BUILD=1
PLATFORMIO_BUILD_FLAGS=""
apply_esp32_full_async_tcp_profile Heltec_v3_repeater
[[ "$PLATFORMIO_BUILD_FLAGS" == *"CONFIG_ASYNC_TCP_STACK_SIZE=4096"* ]] \
  || fail "expanded ESP32 Full omitted its bounded AsyncTCP stack"
[[ "$PLATFORMIO_BUILD_FLAGS" == *"CONFIG_ASYNC_TCP_RUNNING_CORE=1"* ]] \
  || fail "expanded ESP32 Full omitted its AsyncTCP core isolation"
[[ "$PLATFORMIO_BUILD_FLAGS" == *"ASYNCWEBSERVER_USE_CHUNK_INFLIGHT=0"* ]] \
  || fail "expanded ESP32 Full omitted its slow-client response profile"

ESP32_FULL_BUILD=0
PLATFORMIO_BUILD_FLAGS=""
apply_esp32_full_async_tcp_profile Heltec_v3_repeater
[[ "$PLATFORMIO_BUILD_FLAGS" != *"CONFIG_ASYNC_TCP_STACK_SIZE"* ]] \
  || fail "ordinary ESP32 inherited the Full AsyncTCP stack override"
[[ "$PLATFORMIO_BUILD_FLAGS" != *"CONFIG_ASYNC_TCP_RUNNING_CORE"* ]] \
  || fail "ordinary ESP32 inherited the Full AsyncTCP core override"
[[ "$PLATFORMIO_BUILD_FLAGS" != *"ASYNCWEBSERVER_USE_CHUNK_INFLIGHT"* ]] \
  || fail "ordinary ESP32 inherited the Full slow-client response override"

ESP32_FULL_BUILD=1
PLATFORMIO_BUILD_FLAGS=""
apply_esp32_full_async_tcp_profile RAK_4631_companion_radio_full
[[ "$PLATFORMIO_BUILD_FLAGS" != *"CONFIG_ASYNC_TCP_STACK_SIZE"* ]] \
  || fail "nRF52 Full inherited the ESP32 AsyncTCP stack override"
[[ "$PLATFORMIO_BUILD_FLAGS" != *"CONFIG_ASYNC_TCP_RUNNING_CORE"* ]] \
  || fail "nRF52 Full inherited the ESP32 AsyncTCP core override"
[[ "$PLATFORMIO_BUILD_FLAGS" != *"ASYNCWEBSERVER_USE_CHUNK_INFLIGHT"* ]] \
  || fail "nRF52 Full inherited the ESP32 slow-client response override"
ESP32_FULL_BUILD=0
unset PLATFORMIO_BUILD_FLAGS

while IFS='|' read -r source_env full_env; do
  [ "$(get_esp32_full_companion_replacement "$source_env")" = "$full_env" ] \
    || fail "$source_env did not collapse into $full_env"
  is_redundant_bulk_build_target "$source_env" \
    || fail "$source_env remained beside its MQTT-capable Full Companion"
  [ "${PIO_ENV_MQTT_BY_NAME[$full_env]:-1}" = 0 ] \
    || fail "$full_env was incorrectly classified as an MQTT-only profile"
done <<'MQTT_FULL_COMPANION_SPECS'
Heltec_v3_companion_radio_wifi_mqtt|Heltec_v3_companion_radio_full
heltec_v4_companion_radio_wifi_mqtt_femon|heltec_v4_2_v4_3_companion_radio_full_femon
heltec_v4_3_companion_radio_wifi_mqtt_femoff|heltec_v4_2_v4_3_companion_radio_full_femon
MQTT_FULL_COMPANION_SPECS

for mqtt_override in on off; do
  MQTT_BRIDGE_OVERRIDE=$mqtt_override
  RESOLVED_BUILD_TARGETS=(Heltec_v3_companion_radio_full)
  normalize_resolved_targets_for_mqtt build-companion-firmwares >/dev/null
  [ "${RESOLVED_BUILD_TARGETS[*]}" = Heltec_v3_companion_radio_full ] \
    || fail "MQTT=${mqtt_override} replaced or discarded canonical Full Companion"
  PLATFORMIO_BUILD_FLAGS="-DKEEP_FULL_RECIPE=1"
  apply_mqtt_bridge_override Heltec_v3_companion_radio_full
  [ "$PLATFORMIO_BUILD_FLAGS" = "-DKEEP_FULL_RECIPE=1" ] \
    || fail "MQTT=${mqtt_override} mutated canonical Full Companion flags"
done

MQTT_BRIDGE_OVERRIDE=on
RESOLVED_BUILD_TARGETS=(Station_G2_companion_radio_full)
if normalize_resolved_targets_for_mqtt build-companion-firmwares >/dev/null; then
  fail "MQTT=on retained a Full recipe without direct MQTT capability"
fi
MQTT_BRIDGE_OVERRIDE=""

while IFS= read -r env_name; do
  if [ "${PIO_ENV_PLATFORM_BY_NAME[$env_name]:-}" = ESP32_PLATFORM ] \
      && ! is_companion_radio_full_target "$env_name"; then
    fail "canonical ESP32 Companion inventory still contains $env_name"
  fi
done < <(resolve_companion_firmwares)

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
# the canonical artifact. LoRa OTA repeaters, KISS, and BLE keep their distinct
# stream/partition contracts. A standard ESP32 artifact still embeds logging
# when the same target also has an expanded FULL profile.
PIO_ENV_PLATFORM_BY_NAME[wio-e5-mini_repeater]=STM32_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[RAK_3x72_companion_radio_usb]=STM32_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[Tiny_Relay_companion_radio_usb]=STM32_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[wio-e5-mini_companion_radio_usb]=STM32_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[wio-e5_companion_radio_usb]=STM32_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[nrf_kiss_modem]=NRF52_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[nrf_companion_radio_ble]=NRF52_PLATFORM
uses_merged_standard_usb_logging nrf_sensor \
  || fail "ordinary nRF52 sensor omitted merged USB logging"
uses_merged_standard_usb_logging wio-e5-mini_repeater \
  || fail "size-constrained STM32 target omitted merged packet logging"
for constrained_companion in \
    RAK_3x72_companion_radio_usb \
    Tiny_Relay_companion_radio_usb \
    wio-e5-mini_companion_radio_usb \
    wio-e5_companion_radio_usb; do
  uses_merged_standard_usb_logging "$constrained_companion" \
    || fail "$constrained_companion omitted merged packet logging"
  is_logging_size_constrained_target "$constrained_companion" \
    || fail "$constrained_companion enabled oversized verbose mesh diagnostics"
done
if uses_merged_standard_usb_logging nrf_repeater_lora_ota_no_external_sensors; then
  fail "LoRa OTA repeater incorrectly merged USB logging"
fi
if uses_merged_standard_usb_logging nrf_kiss_modem; then
  fail "KISS target incorrectly merged plaintext USB logging"
fi
if uses_merged_standard_usb_logging nrf_companion_radio_ble; then
  fail "BLE Companion incorrectly merged USB logging"
fi
uses_merged_standard_usb_logging esp_repeater \
  || fail "standard ESP32 target omitted logging because FULL also exists"

# A standard ESP32 field image may omit LoRa OTA for size, but it must never
# also omit the compact browser updater. This is the exact profile combination
# that previously produced Heltec V4 repeaters where both `ota` and `start ota`
# were dead ends.
verify_esp32_field_browser_ota() {
  local env_name=$1
  local PLATFORMIO_BUILD_FLAGS=""
  local PLATFORMIO_BUILD_UNFLAGS=""
  local BUILD_PROFILE_FOR_TARGET=standard
  local ESP32_FULL_BUILD=0
  local -a BUILD_CAPABILITIES=()
  local -a BUILD_REDUCTIONS=()
  local -a BUILD_EXPECTATIONS=()
  local capabilities reductions expectations

  PIO_ENV_PLATFORM_BY_NAME[$env_name]=ESP32_PLATFORM
  PIO_ENV_OTA_BY_NAME[$env_name]=0
  apply_esp32_lora_ota_size_profile "$env_name"

  [[ "$PLATFORMIO_BUILD_FLAGS" == *-DLIGHTWEIGHT_WIFI_OTA=1* ]] \
    || fail "$env_name omitted its browser OTA fail-safe"
  [[ "$PLATFORMIO_BUILD_FLAGS" == *-UDISABLE_WIFI_OTA* ]] \
    || fail "$env_name did not remove a board-level WiFi OTA disable"
  [[ "$PLATFORMIO_BUILD_FLAGS" != *-DDISABLE_WIFI_OTA=1* ]] \
    || fail "$env_name still explicitly disables browser OTA"
  capabilities=" ${BUILD_CAPABILITIES[*]} "
  [[ "$capabilities" == *" web.lightweight_browser_ota "* ]] \
    || fail "$env_name did not declare browser OTA"
  reductions=" ${BUILD_REDUCTIONS[*]} "
  [[ "$reductions" != *"web.browser_ota omitted"* ]] \
    || fail "$env_name still records browser OTA as omitted"

  declare_build_capability_contract "$env_name" ESP32_PLATFORM
  expectations=" ${BUILD_EXPECTATIONS[*]} "
  [[ "$expectations" == *"web.lightweight_browser_ota=MeshCore firmware update"* ]] \
    || fail "$env_name does not verify browser OTA in the linked image"
}

verify_esp32_field_browser_ota heltec_v4_repeater
verify_esp32_field_browser_ota heltec_v4_room_server
verify_esp32_field_browser_ota heltec_v4_sensor

# Personal/attached roles can retain their established transport policy; the
# fail-safe is deliberately scoped to remotely installed field/server images.
PIO_ENV_PLATFORM_BY_NAME[heltec_v4_terminal_chat]=ESP32_PLATFORM
PLATFORMIO_BUILD_FLAGS=""
PLATFORMIO_BUILD_UNFLAGS=""
BUILD_PROFILE_FOR_TARGET=standard
BUILD_CAPABILITIES=()
BUILD_REDUCTIONS=()
apply_esp32_lora_ota_size_profile heltec_v4_terminal_chat
[[ "$PLATFORMIO_BUILD_FLAGS" == *-DDISABLE_WIFI_OTA=1* ]] \
  || fail "attached terminal unexpectedly changed its WiFi policy"

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
[[ "$expectations" == *"web.unconfigured_setup_cutoff=WiFi still unconfigured after"* ]] \
  || fail "ESP32 Full contract omitted the bounded unconfigured-WiFi window"
[[ "$expectations" != *"Full Companion terminal listening"* ]] \
  || fail "ESP32 Full contract still depends on optional debug logging"

BUILD_CAPABILITIES=()
BUILD_REDUCTIONS=()
BUILD_EXPECTATIONS=()
declare_build_capability_contract \
  SenseCapIndicator-LoRa_companion_radio_full ESP32_PLATFORM
expectations=" ${BUILD_EXPECTATIONS[*]} "
[[ "$expectations" == *"indicator.font_recovery_ntp_gate=requesting fresh NTP time before download"* ]] \
  || fail "Indicator Full contract omitted the fresh-NTP font-download gate"
[[ "$expectations" == *"indicator.font_recovery_tls=opening TLS connection to"* ]] \
  || fail "Indicator Full contract omitted the TLS font-recovery marker"

pio_env_option_contains() {
  [ "$1" = Heltec_v3_companion_radio_full ] \
    && [ "$2" = build_flags ] \
    && [ "$3" = WITH_MQTT_BRIDGE ]
}
BUILD_EXPECTATIONS=()
declare_build_capability_contract \
  Heltec_v3_companion_radio_full ESP32_PLATFORM
expectations=" ${BUILD_EXPECTATIONS[*]} "
[[ "$expectations" == *"companion.direct_mqtt=mqtt.status"* ]] \
  || fail "MQTT-capable ESP32 Full contract omitted direct MQTT"

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

BUILD_PROFILE_FOR_TARGET=standard
for rak_target in \
    RAK_3401_repeater_lora_ota_no_external_sensors \
    RAK_4631_repeater_lora_ota_no_external_sensors; do
  BUILD_CAPABILITIES=()
  BUILD_REDUCTIONS=()
  BUILD_EXPECTATIONS=()
  declare_build_capability_contract "$rak_target" NRF52_PLATFORM
  expectations=" ${BUILD_EXPECTATIONS[*]} "
  [[ "$expectations" == *"sensor.gps=meshcore.capability.rak_wisblock_gps.v1"* ]] \
    || fail "$rak_target reduced OTA contract omitted retained GPS"
  [[ "$expectations" != *"sensor.gps=gps setloc"* ]] \
    || fail "$rak_target reduced OTA contract still accepts generic GPS CLI text"
  for ina in 219 226 260 3221; do
    [[ "$expectations" == *"sensor.ina${ina}=INA${ina}"* ]] \
      || fail "$rak_target reduced OTA contract omitted retained INA${ina}"
  done
done

echo "test_build_profiles: OK"
