#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
source build.sh

fail() {
  echo "test_build_profiles: $*" >&2
  exit 1
}

# Optional debug settings must remain usable by strict shell callers before
# any PlatformIO flags have been initialized.
(
  unset DISABLE_DEBUG PLATFORMIO_BUILD_FLAGS
  disable_debug_flags
  [ -z "${PLATFORMIO_BUILD_FLAGS+x}" ] \
    || fail "default debug policy unexpectedly set build flags"
  DISABLE_DEBUG=0
  PLATFORMIO_BUILD_FLAGS=-DKEEP_CALLER_FLAG=1
  disable_debug_flags
  [ "$PLATFORMIO_BUILD_FLAGS" = -DKEEP_CALLER_FLAG=1 ] \
    || fail "disabled debug override changed caller flags"
  DISABLE_DEBUG=1
  unset PLATFORMIO_BUILD_FLAGS
  disable_debug_flags
  [[ "$PLATFORMIO_BUILD_FLAGS" == *"-UMESH_DEBUG -UMESH_PACKET_LOGGING"* ]] \
    || fail "explicit debug disable lost the USB debug undefines"
  [[ "$PLATFORMIO_BUILD_FLAGS" == *"-DCFG_DEBUG=0"* ]] \
    || fail "explicit debug disable lost CFG_DEBUG"
  PLATFORMIO_BUILD_FLAGS=-DKEEP_CALLER_FLAG=1
  disable_debug_flags
  [[ "$PLATFORMIO_BUILD_FLAGS" == -DKEEP_CALLER_FLAG=1* ]] \
    || fail "explicit debug disable discarded caller flags"
)

[ "$OPTION3_BUILD_WORKERS" -eq 1 ] \
  || fail "logging matrix permits concurrent PlatformIO target builds"

for utility in profile_switch_t096_sx1262 profile_four_tx_v4_rx xiao_s3_partition_migrator \
    heltec_v4_partition_migrator_test_hold xiao_s3_partition_legacy_seed \
    esp32_4mb_partition_migrator_lora \
    heltec_v4_partition_migrator_lora_repeater \
    heltec_v4_partition_migrator_lora_repeater_lora_ota_no_external_sensors \
    Xiao_S3_WIO_partition_expander \
    Heltec_v3_repeater_observer_mqtt_sim \
    RAK_4631_repeater_rak15002_sd_seeder_only; do
  is_redundant_bulk_build_target "$utility" \
    || fail "release matrix includes development utility $utility"
done

if is_nrf52_sensor_ota_pair_target RAK_4631_repeater_rak15002_sd_seeder_only; then
  fail "source-only SD archive was rewritten into a self-updating receiver pair"
fi

# Reduced RAK profiles all retain the compact INA set, but GPS depends on
# whether the bridge already owns Serial1. Keep those independent contracts so
# a valid Serial1 image cannot fail release qualification for an absent GPS
# marker, and so its manifest never advertises impossible hardware support.
for rak_target in \
  RAK_3401_repeater_lora_ota_no_external_sensors \
  RAK_3401_sensor_lora_ota_no_external_sensors \
  RAK_4631_repeater_lora_ota_no_external_sensors \
  RAK_4631_room_server_ethernet_lora_ota_no_external_sensors \
  RAK_4631_sensor_lora_ota_no_external_sensors \
  RAK_4631_repeater_bridge_rs232_serial1_lora_ota_no_external_sensors \
  RAK_4631_repeater_bridge_rs232_serial2_lora_ota_no_external_sensors; do
  is_rak_i2c_voltage_monitor_ota_target "$rak_target" \
    || fail "$rak_target lost the retained INA contract"
done
for rak_target in \
  RAK_3401_repeater_lora_ota_no_external_sensors \
  RAK_3401_sensor_lora_ota_no_external_sensors \
  RAK_4631_repeater_lora_ota_no_external_sensors \
  RAK_4631_room_server_ethernet_lora_ota_no_external_sensors \
  RAK_4631_sensor_lora_ota_no_external_sensors \
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
import re
import sys

sections = {section: dict(options) for section, options in json.load(sys.stdin)}

def option_text(env_name, option_name):
    section = sections.get(f"env:{env_name}", sections.get(env_name))
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
waveshare_full = "Waveshare_S3_LR1121_companion_radio_full"
require(waveshare_full, "board", "esp32-s3-zero")
require(waveshare_full, "board_build.flash_mode", "dio")
for flag in (
    "BOARD_HAS_PSRAM", "RADIO_CLASS=CustomLR1121", "WRAPPER_CLASS=CustomLR1121Wrapper",
    "LR1121_FORCE_HP_PA", "LR11X0_DIO3_TCXO_VOLTAGE=1.8", "P_LORA_NSS=42",
    "P_LORA_DIO_1=38", "P_LORA_RESET=39", "P_LORA_BUSY=41", "P_LORA_SCLK=40",
    "P_LORA_MISO=46", "P_LORA_MOSI=45", "MAX_CONTACTS=350", "MAX_GROUP_CHANNELS=40",
    "OFFLINE_QUEUE_SIZE=256", "ENABLE_USB_INTERFACE",
):
    require(waveshare_full, "build_flags", flag)
require(waveshare_full, "build_src_filter", "variants/waveshare_esp32s3_lr1121")

wio_l2_full = "Wio_Tracker_L2_companion_radio_full"
require(wio_l2_full, "board", "seeed_wio_tracker_l2")
require(wio_l2_full, "board_build.flash_mode", "dio")
for flag in (
    "SEEED_WIO_TRACKER_L2", "USE_SX1262", "DISPLAY_CLASS=WioTrackerL2Display",
    "HAS_TOUCH=1", "AUTO_OFF_MILLIS=60000", "ENV_INCLUDE_GPS=1", "PIN_GPS_TX=18",
    "PIN_GPS_RX=17", "ENV_SKIP_GPS_DETECT=1", "ENV_INCLUDE_INA219=0",
    "MAX_CONTACTS=350", "MAX_GROUP_CHANNELS=40", "OFFLINE_QUEUE_SIZE=256",
    "ENABLE_USB_INTERFACE",
):
    require(wio_l2_full, "build_flags", flag)
require(wio_l2_full, "build_src_filter", "variants/wio-tracker-l2")
require(wio_l2_full, "build_src_filter", "examples/companion_radio/ui-new")
require(wio_l2_full, "lib_deps", "LovyanGFX @ 1.2.30")
require(wio_l2_full, "lib_deps", "arduino-audio-driver/archive/v0.2.1.zip")
require("heltec_v4_expansionkit_tft_companion_radio_full_femon", "platform", "platformio/espressif32@6.11.0")
require("heltec_v4_expansionkit_tft_companion_radio_full_femon", "build_flags", "ARDUINO_USB_MODE=1")
full_env_names = sorted(
    section_name.removeprefix("env:")
    for section_name in sections
    if section_name.startswith("env:")
    and "companion_radio_full" in section_name.lower()
)
if not full_env_names:
    raise SystemExit("test_build_profiles: no concrete Full environments found")
for env_name in full_env_names:
    require(
        env_name,
        "build_flags",
        "MESHCORE_REQUIRES_COMPANION_RADIO_FULL=1",
    )
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
    "Waveshare_S3_LR1121_companion_radio_full",
    "Wio_Tracker_L2_companion_radio_full",
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
require("Heltec_t096", "build_flags", "LORA_TX_POWER=22")
reject("Heltec_t096", "build_flags", "LORA_TX_POWER=9")
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
    "MKE_s3_repeater",
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

# Full source substitution must preserve the normal repeater UART rather
# than retiring its legacy bridge while compiling an MQTT-only recipe.
for env_name, rx, tx in (
    ("Heltec_v3_repeater_observer_mqtt", 5, 6),
    ("Heltec_WSL3_repeater_observer_mqtt", 5, 6),
    ("RAK_3112_repeater_observer_mqtt", 5, 6),
):
    require(env_name, "build_flags", "WITH_MQTT_BRIDGE=1")
    require(env_name, "build_flags", "WITH_RS232_BRIDGE=Serial2")
    require(env_name, "build_flags", "WITH_RS232_BRIDGE_UART=2")
    require(env_name, "build_flags", f"WITH_RS232_BRIDGE_RX={rx}")
    require(env_name, "build_flags", f"WITH_RS232_BRIDGE_TX={tx}")
    require(env_name, "build_flags", "RS232_BRIDGE_MERGED=1")
    require(env_name, "build_src_filter", "helpers/bridges/RS232Bridge.cpp")
    reject(env_name, "build_flags", "RS232_BRIDGE_DEFAULT_ON")

# The T-LoRa runtime RAM budget cannot fit MQTT, ESP-NOW and UART together.
# Keep its UART in the normal Full image and its MQTT recipe independent.
tlora_observer = "LilyGo_TLora_V2_1_1_6_repeater_observer_mqtt_"
require(tlora_observer, "build_flags", "WITH_MQTT_BRIDGE=1")
reject(tlora_observer, "build_flags", "WITH_RS232_BRIDGE")
reject(tlora_observer, "build_flags", "RS232_BRIDGE_MERGED")
reject(tlora_observer, "build_src_filter", "helpers/bridges/RS232Bridge.cpp")

# MKE keeps its ordinary role and UART wiring while exposing the existing
# persistent bridge controls. The merged marker selects the disabled default;
# the explicit legacy bridge remains enabled for deliberate direct builds.
for env_name in ("MKE_s3_repeater", "MKE_s3_repeater_bridge_rs232"):
    require(env_name, "build_flags", "WITH_RS232_BRIDGE=Serial2")
    require(env_name, "build_flags", "WITH_RS232_BRIDGE_UART=2")
    require(env_name, "build_flags", "WITH_RS232_BRIDGE_RX=16")
    require(env_name, "build_flags", "WITH_RS232_BRIDGE_TX=17")
    require(env_name, "build_src_filter", "helpers/bridges/RS232Bridge.cpp")
reject("MKE_s3_repeater", "build_flags", "RS232_BRIDGE_DEFAULT_ON")
reject("MKE_s3_repeater_bridge_rs232", "build_flags", "RS232_BRIDGE_MERGED")
require("MKE_s3_repeater", "build_flags", "WITH_ESPNOW_BRIDGE=1")
require("MKE_s3_repeater", "build_flags", "ESPNOW_BRIDGE_MERGED=1")
require("MKE_s3_repeater", "build_src_filter", "helpers/bridges/ESPNowBridge.cpp")
reject("MKE_s3_repeater_bridge_espnow", "build_flags", "ESPNOW_BRIDGE_MERGED")
for env_name in (
    "MKE_s3",
    "MKE_s3_repeater_bridge_espnow",
    "MKE_s3_room_server",
    "MKE_s3_sensor",
    "MKE_s3_terminal_chat",
    "MKE_s3_companion_radio_usb",
    "MKE_s3_companion_radio_ble",
    "MKE_s3_companion_radio_wifi",
):
    reject(env_name, "build_flags", "WITH_RS232_BRIDGE")
    reject(env_name, "build_src_filter", "helpers/bridges/RS232Bridge.cpp")
require("MKE_s3_repeater_bridge_espnow", "build_flags", "WITH_ESPNOW_BRIDGE=1")
require("MKE_s3_repeater_bridge_espnow", "build_src_filter", "helpers/bridges/ESPNowBridge.cpp")

# GPIO5 is the radio reset line. Guard the complete board wiring rather than
# accepting a bridge pin assignment that compiles but disables a peripheral.
mke_pins = {
    name: int(value)
    for name, value in re.findall(
        r"(?:^|\s)-D\s*([A-Z0-9_]+)=([0-9]+)(?=\s|$)",
        option_text("MKE_s3_repeater", "build_flags"),
    )
}
bridge_pins = {mke_pins["WITH_RS232_BRIDGE_RX"], mke_pins["WITH_RS232_BRIDGE_TX"]}
for pin_name in (
    "P_LORA_NSS", "P_LORA_MOSI", "P_LORA_MISO", "P_LORA_SCLK",
    "P_LORA_BUSY", "P_LORA_RESET", "P_LORA_DIO_1",
    "PIN_BOARD_SDA", "PIN_BOARD_SCL", "PIN_USER_BTN",
    "PIN_GPS_RX", "PIN_GPS_TX", "PIN_GPS_EN",
):
    if mke_pins[pin_name] in bridge_pins:
        raise SystemExit(f"test_build_profiles: MKE bridge UART conflicts with {pin_name}")

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
require(rak_usb, "build_flags", "RAK_3401")
require(rak_usb, "build_flags", "PIN_GPS_EN=-1")
reject(rak_usb, "build_flags", "FORCE_GPS_ALIVE")
for env_name in ("RAK_4631_companion_radio_usb", "RAK_4631_repeater"):
    require(env_name, "build_flags", "RAK_4631")
    require(env_name, "build_flags", "PIN_GPS_EN=-1")
    reject(env_name, "build_flags", "FORCE_GPS_ALIVE")
'

# Every qualified ESP32 Companion layout publishes one Full image even when
# its historical recipe had no separately named WiFi target. The transport
# aliases remain directly buildable, but canonical release resolution must
# replace each with that exact board's Full target.
init_project_context >/dev/null

# The interactive board menu must offer the same ordinary Full choices as the
# release, while exact compatibility/development recipes stay directly usable.
# Test real board inheritance: dropping aliases by suffix alone would also
# discard the measured TLora transport split or special nRF52 storage wiring.
(
  mapfile -t menu_targets < <(get_interactive_build_targets)
  menu_text=$(printf '%s\n' "${menu_targets[@]}")
  for expected in Heltec_v3_companion_radio_full Heltec_v3_repeater \
      Waveshare_S3_LR1121_companion_radio_full Wio_Tracker_L2_companion_radio_full \
      LilyGo_TLora_V2_1_1_6_repeater \
      LilyGo_TLora_V2_1_1_6_repeater_observer_mqtt_ \
      RAK_3401_repeater \
      RAK_4631_repeater_rak15001_slot_c_lora_ota \
      RAK_4631_repeater_w25q16_lora_ota \
      RAK_4631_repeater_bridge_rs232_serial1_lora_ota_no_external_sensors \
      RAK_4631_repeater_bridge_rs232_serial2_lora_ota_no_external_sensors; do
    grep -Fxq "$expected" <<<"$menu_text" \
      || fail "ordinary board menu omitted needed choice $expected"
  done
  for hidden in Heltec_v3_companion_radio_usb Heltec_v3_companion_radio_ble \
      Waveshare_S3_LR1121_companion_radio_usb Waveshare_S3_LR1121_companion_radio_ble \
      Wio_Tracker_L2_companion_radio_usb Wio_Tracker_L2_companion_radio_ble \
      Heltec_v3_repeater_observer_mqtt \
      Heltec_v3_repeater_lora_ota_no_external_sensors \
      RAK_3401_repeater_lora_ota_no_external_sensors \
      heltec_v4_partition_migrator; do
    if grep -Fxq "$hidden" <<<"$menu_text"; then
      fail "ordinary board menu still offers redundant/recovery choice $hidden"
    fi
    is_supported_build_env "$hidden" \
      || fail "hiding $hidden from the board menu removed its exact build"
  done
  board=$(get_board_family_for_env Heltec_v3_repeater)
  variants=$(get_variants_for_board "$board")
  grep -Fxq Heltec_v3_companion_radio_full <<<"$variants" \
    || fail "board submenu lost its Full Companion"
  if grep -Eq 'companion_radio_(usb|ble|wifi)|_observer_mqtt|no_external_sensors' <<<"$variants"; then
    fail "board submenu reintroduced transport/profile choices"
  fi
  [ "$(get_nrf52_sensor_ota_pair_target RAK_3401_repeater)" \
      = "$(get_nrf52_sensor_ota_pair_target RAK_3401_repeater_lora_ota_no_external_sensors)" ] \
    || fail "menu folded nRF52 targets with different output identities"
  # Prefer the ordinary name even when a caller supplies the alias first.
  SUPPORTED_PIO_ENVS=(RAK_3401_repeater_lora_ota_no_external_sensors RAK_3401_repeater)
  [ "$(get_interactive_build_targets)" = RAK_3401_repeater ] \
    || fail "nRF52 menu depends on alias input order"
)

# Expanded partitions make Full the ordinary ESP32 node release. Legacy lean
# names still address installed firmware and must remain directly buildable,
# but cannot reappear in either the all-role or a role-specific release list.
for resolver in resolve_all_firmwares resolve_repeater_firmwares \
    resolve_room_server_firmwares resolve_sensor_firmwares; do
  while IFS= read -r release_env; do
    [ "${PIO_ENV_PLATFORM_BY_NAME[$release_env]:-}" = ESP32_PLATFORM ] || continue
    if is_lora_ota_no_external_sensors_target "$release_env"; then
      fail "$resolver republishes reduced ESP32 alias $release_env"
    fi
    is_redundant_bulk_build_target "$release_env" \
      && fail "$resolver republishes utility or legacy alias $release_env"
    if is_kiss_modem_target "$release_env"; then
      if is_esp32_full_release_build_target "$release_env"; then
        fail "$release_env host modem was promoted into the node Full profile"
      fi
    else
      is_esp32_full_release_build_target "$release_env" \
        || fail "$resolver selected ESP32 node without a Full release: $release_env"
    fi
  done < <("$resolver")
done

# Cover the former allowlist gaps as well as migration hardware and a named
# Full Companion. Their ordinary release profile is Full; the older helper
# continues to describe the narrower same-partition OTA identity contract.
for release_env in Station_G3_ESP32_r2_repeater WHY2025_badge_repeater_ \
    GEPRC_Linkflow_900_repeater Generic_ESPNOW_repeatr \
    LilyGo_Tlora_C6_room_server_ Meshadventurer_sx1262_repeater \
    Ebyte_EoRa-S3_Repeater Heltec_ct62_sensor Station_G2_companion_radio_full; do
  is_esp32_full_release_build_target "$release_env" \
    || fail "$release_env retained a standard ordinary ESP32 release slot"
done
if is_esp32_full_only_bulk_target Ebyte_EoRa-S3_Repeater; then
  fail "release consolidation incorrectly advertised same-partition EoRa migration"
fi
if is_esp32_full_release_build_target RAK_3401_repeater; then
  fail "nRF52 role acquired ESP32 Full-only release policy"
fi

for direct_env in Heltec_v3_repeater_lora_ota_no_external_sensors \
    Ebyte_EoRa-S3_Repeater_lora_ota_no_external_sensors \
    Heltec_v3_companion_radio_usb heltec_v4_partition_migrator \
    heltec_v4_partition_migrator_lora_repeater; do
  is_supported_build_env "$direct_env" \
    || fail "ordinary consolidation removed direct compatibility target $direct_env"
  [ -n "$(get_pio_build_env "$direct_env")" ] \
    || fail "$direct_env no longer resolves a direct PlatformIO recipe"
done
(
  BATCH_BUILD_MODE=0
  SINGLE_TARGET_FULL_BUILD=0
  EXACT_IDENTITY_FULL_BUILD=0
  BUILD_PROFILE_OVERRIDE=standard
  BUILD_PROFILE_EXPLICIT=1
  RESOLVED_BUILD_TARGETS=(Ebyte_EoRa-S3_Repeater)
  configure_effective_build_profile build-firmware >/dev/null
  [ "$BUILD_PROFILE_EFFECTIVE" = standard ] \
    || fail "explicit standard recovery profile was promoted to Full"
  [ "${RESOLVED_BUILD_TARGETS[*]}" = Ebyte_EoRa-S3_Repeater ] \
    || fail "explicit recovery profile changed its deployed target identity"
  BUILD_PROFILE_OVERRIDE=auto
  BUILD_PROFILE_EXPLICIT=0
  RESOLVED_BUILD_TARGETS=(Heltec_v3_repeater_lora_ota_no_external_sensors)
  configure_effective_build_profile build-firmware >/dev/null
  [ "$BUILD_PROFILE_EFFECTIVE" = standard ] \
    || fail "direct reduced compatibility request was promoted to Full"
  [ "${RESOLVED_BUILD_TARGETS[*]}" = Heltec_v3_repeater_lora_ota_no_external_sensors ] \
    || fail "direct reduced request changed its deployed target identity"
)

# Named Full Companion already selects its own combined profile. In a bulk
# pass it must compile the real source recipe, including generated Full names,
# without entering the infrastructure promotion path. Stop at recipe capture,
# before any dependency install, PlatformIO invocation, or artifact collection.
(
  BATCH_BUILD_MODE=1
  BUILD_PROFILE_EXPLICIT=0
  BUILD_PROFILE_EFFECTIVE=standard
  ESP32_FULL_BUILD=0
  FIRMWARE_VERSION=vtest-full-companion-routing
  RESUME_BUILD_OUTPUT=0
  companion_probe_marker=$(mktemp)
  trap 'rm -f "$companion_probe_marker"' EXIT
  compute_build_recipe_digest() {
    printf '%s|%s|%s\n' "$1" "$3" "$ESP32_FULL_BUILD" > "$companion_probe_marker"
    return 77
  }
  for full_companion in Heltec_v3_companion_radio_full Heltec_ct62_companion_radio_full; do
    source_recipe=$(get_pio_build_env "$full_companion")
    [ "$(get_exact_identity_full_pio_env "$full_companion")" = "$source_recipe" ] \
      || fail "$full_companion tried to compile a logical Full name as its source recipe"
    : > "$companion_probe_marker"
    if build_firmware_one_profile "$full_companion" >/dev/null; then
      fail "$full_companion bypassed the pre-PlatformIO recipe probe"
    fi
    [ "$(cat "$companion_probe_marker")" = "$full_companion|$source_recipe|0" ] \
      || fail "$full_companion entered infrastructure promotion or lost its real source recipe"
  done
)

# Exercise actual matrix scheduling while replacing only its final build
# dispatch. This verifies the portable pass cannot return through an old
# allowlist gap, migration target, or later duplicate profile pass.
(
  REQUIRE_OTA_UPDATES=0
  BATCH_BUILD_MODE=1
  BUILD_PROFILE_EXPLICIT=0
  BUILD_PROFILE_EFFECTIVE=standard
  ESP32_FULL_BUILD=0
  matrix_builds=()
  run_logged_build_targets() {
    local target
    for target in "$@"; do
      matrix_builds+=("$target:$ESP32_FULL_BUILD")
      if [ "${PIO_ENV_PLATFORM_BY_NAME[$target]:-}" = ESP32_PLATFORM ] \
          && ! is_kiss_modem_target "$target" \
          && ! is_companion_radio_full_target "$target"; then
        [ "$ESP32_FULL_BUILD" = 1 ] \
          || fail "ordinary matrix scheduled a portable ESP32 node: $target"
      fi
    done
    return 0
  }
  run_logging_matrix_build_targets \
    Station_G3_ESP32_r2_repeater Ebyte_EoRa-S3_Repeater \
    Generic_ESPNOW_repeatr heltec_v4_room_server heltec_v4_sensor \
    Station_G2_companion_radio_full heltec_v4_kiss_modem \
    RAK_3401_repeater_lora_ota_no_external_sensors >/dev/null
  for target in Station_G3_ESP32_r2_repeater Ebyte_EoRa-S3_Repeater \
      Generic_ESPNOW_repeatr heltec_v4_room_server heltec_v4_sensor; do
    matches=0
    for call in "${matrix_builds[@]}"; do
      if [ "$call" = "$target:1" ]; then matches=$((matches + 1)); fi
    done
    [ "$matches" -eq 1 ] \
      || fail "ordinary matrix built $target $matches times instead of one Full image"
  done
  for expected in Station_G2_companion_radio_full:0 heltec_v4_kiss_modem:0 \
      RAK_3401_repeater_lora_ota_no_external_sensors:0; do
    found=0
    for call in "${matrix_builds[@]}"; do
      if [ "$call" = "$expected" ]; then found=$((found + 1)); fi
    done
    [ "$found" -eq 1 ] \
      || fail "matrix changed the distinct Companion/KISS/nRF52 recipe: $expected"
  done
)

# ESP32 room servers and sensors use their expanded Full image for LoRa OTA.
for full_role_env in Heltec_v3_room_server Heltec_v3_sensor; do
  supports_esp32_full_build "$full_role_env" \
    || fail "$full_role_env lacks its ESP32 Full build"
  (
    ESP32_FULL_BUILD=1
    is_lora_ota_build "$full_role_env" \
      || fail "$full_role_env Full image lost LoRa OTA"
  )
done

# Standalone room servers receive a lean LoRa OTA sibling. nRF52 sensors
# receive an explicit lean sibling while their normal image keeps its sensors.
while IFS='|' read -r room_env room_ota_env; do
  is_supported_build_env "$room_ota_env" \
    || fail "$room_ota_env was not registered as a room-server LoRa OTA target"
  [ "$(get_pio_build_env "$room_ota_env")" = "$room_env" ] \
    || fail "$room_ota_env did not retain its room-server PlatformIO recipe"
  [ "$(get_reduced_lora_ota_target "$room_env")" = "$room_ota_env" ] \
    || fail "$room_env did not resolve its reduced room-server OTA sibling"
  (
    BUILD_PROFILE_FOR_TARGET=standard
    ESP32_FULL_BUILD=0
    is_lora_ota_build "$room_ota_env" \
      || fail "$room_ota_env did not enable LoRa OTA"
  )
done <<'ROOM_OTA_SPECS'
Heltec_v3_room_server|Heltec_v3_room_server_lora_ota_no_external_sensors
RAK_3401_room_server|RAK_3401_room_server_lora_ota_no_external_sensors
ROOM_OTA_SPECS
room_release_targets=$(print_release_firmware_targets get-room-server-firmwares-to-build)
printf '%s\n' "$room_release_targets" | grep -Fx \
  RAK_3401_room_server_lora_ota_no_external_sensors >/dev/null \
  || fail "room-server release resolution omitted its portable LoRa OTA target"
printf '%s\n' "$room_release_targets" | grep -Fx \
  solarxiao_30S_room_server_lora_ota >/dev/null \
  || fail "room-server release resolution omitted its QSPI LoRa OTA target"
for room_ota_env in solarxiao_33S_room_server_lora_ota \
    RAK_4631_room_server_ethernet_lora_ota_no_external_sensors; do
  printf '%s\n' "$room_release_targets" | grep -Fx "$room_ota_env" >/dev/null \
    || fail "room-server release resolution omitted $room_ota_env"
done

# SolarXiao Room normally uses the module's onboard QSPI, not an additional
# internal-only choice. Keep direct recovery recipes and their OTA identities
# intact while excluding them from every ordinary selection path.
solar_room_targets=$(resolve_room_server_firmwares)
solar_all_targets=$(resolve_all_firmwares)
solar_logging_targets=$(resolve_logging_matrix_firmwares)
solar_menu_targets=$(get_interactive_build_targets)
for solar_board in solarxiao_30S solarxiao_33S; do
  legacy_room=${solar_board}_room_server
  qspi_room=${legacy_room}_lora_ota
  is_supported_build_env "$legacy_room" \
    || fail "$legacy_room explicit compatibility recipe disappeared"
  validate_build_target "$legacy_room" \
    || fail "$legacy_room explicit compatibility recipe is invalid"
  is_redundant_bulk_build_target "$legacy_room" \
    || fail "$legacy_room internal-only recipe remains an ordinary build"
  is_redundant_bulk_build_target "${legacy_room^^}" \
    || fail "$legacy_room retirement policy is not case insensitive"
  [ "$(get_pio_build_env "$legacy_room")" = "$legacy_room" ] \
    || fail "$legacy_room silently changed its PlatformIO recipe"
  [ "$(get_nrf52_sensor_ota_pair_target "$legacy_room")" = "$legacy_room" ] \
    || fail "$legacy_room silently changed its OTA identity"
  [ "$(get_nrf52_sensor_ota_pair_target "$qspi_room")" = "$qspi_room" ] \
    || fail "$qspi_room lost its distinct QSPI OTA identity"
  for solar_selection in "$room_release_targets" "$solar_room_targets" \
      "$solar_all_targets" "$solar_logging_targets" "$solar_menu_targets"; do
    if printf '%s\n' "$solar_selection" | grep -Fx "$legacy_room" >/dev/null; then
      fail "$legacy_room leaked into an ordinary build selection"
    fi
    printf '%s\n' "$solar_selection" | grep -Fx "$qspi_room" >/dev/null \
      || fail "$qspi_room missing from an ordinary build selection"
  done
  (
    REQUIRE_OTA_UPDATES=0
    resolve_command_targets build-firmware "$legacy_room"
    [ "${#RESOLVED_BUILD_TARGETS[@]}" -eq 1 ] \
      && [ "${RESOLVED_BUILD_TARGETS[0]}" = "$legacy_room" ] \
      || fail "$legacy_room direct recovery build changed its exact target"
  )
done
if is_solarxiao_internal_room_compatibility_target Xiao_nrf52_room_server \
    || is_solarxiao_internal_room_compatibility_target solarxiao_30S_repeater \
    || is_solarxiao_internal_room_compatibility_target solarxiao_33S_room_server_lora_ota; then
  fail "SolarXiao internal Room policy retired an unrelated recipe"
fi

sensor_release_targets=$(resolve_sensor_firmwares)
while IFS='|' read -r sensor_env sensor_ota_env; do
  is_supported_build_env "$sensor_ota_env" \
    || fail "$sensor_ota_env was not registered as a sensor LoRa OTA target"
  [ "$(get_pio_build_env "$sensor_ota_env")" = "$sensor_env" ] \
    || fail "$sensor_ota_env did not retain its sensor PlatformIO recipe"
  printf '%s\n' "$sensor_release_targets" | grep -Fx "$sensor_ota_env" >/dev/null \
    || fail "sensor release resolution omitted $sensor_ota_env"
  (
    BUILD_PROFILE_FOR_TARGET=standard
    ESP32_FULL_BUILD=0
    is_lora_ota_build "$sensor_ota_env" \
      || fail "$sensor_ota_env did not enable LoRa OTA"
  )
done <<'SENSOR_OTA_SPECS'
Heltec_t096_sensor|Heltec_t096_sensor_lora_ota_no_external_sensors
Heltec_t114_sensor|Heltec_t114_sensor_lora_ota_no_external_sensors
ProMicro_sensor|ProMicro_sensor_lora_ota_no_external_sensors
R1Neo_sensor|R1Neo_sensor_lora_ota_no_external_sensors
RAK_3401_sensor|RAK_3401_sensor_lora_ota_no_external_sensors
RAK_4631_sensor|RAK_4631_sensor_lora_ota_no_external_sensors
RAK_WisMesh_Tag_sensor|RAK_WisMesh_Tag_sensor_lora_ota_no_external_sensors
t1000e_sensor|t1000e_sensor_lora_ota_no_external_sensors
SENSOR_OTA_SPECS
for solar_env in solarxiao_30S_room_server_lora_ota solarxiao_33S_room_server_lora_ota; do
  is_supported_build_env "$solar_env" \
    || fail "$solar_env QSPI room-server OTA target was not registered"
  (
    BUILD_PROFILE_FOR_TARGET=standard
    ESP32_FULL_BUILD=0
    is_lora_ota_build "$solar_env" \
      || fail "$solar_env did not enable LoRa OTA"
  )
done
is_supported_build_env RAK_4631_room_server_ethernet_lora_ota_no_external_sensors \
  || fail "RAK4631 Ethernet room server lacked its lean LoRa OTA target"
if is_supported_build_env Heltec_v3_sensor_lora_ota_no_external_sensors; then
  fail "ESP32 sensors should use their Full OTA image"
fi

# KISS inherits board OTA flags, not the application OTA implementation.
for kiss_env in "${SUPPORTED_PIO_ENVS[@]}"; do
  is_kiss_modem_target "$kiss_env" || continue
  for kiss_profile in auto standard full; do
    (
      BUILD_PROFILE_FOR_TARGET=$kiss_profile
      ESP32_FULL_BUILD=1
      if is_lora_ota_build "$kiss_env"; then
        fail "$kiss_env $kiss_profile incorrectly promises LoRa OTA"
      fi
      BUILD_CAPABILITIES=()
      BUILD_EXPECTATIONS=()
      BUILD_REDUCTIONS=()
      declare_build_capability_contract "$kiss_env" "${PIO_ENV_PLATFORM_BY_NAME[$kiss_env]}"
      for expectation in "${BUILD_EXPECTATIONS[@]}"; do
        case "$expectation" in
          ota.update.lora=*|ota.cli=*) fail "$kiss_env $kiss_profile has an impossible OTA contract" ;;
        esac
      done
    )
  done
done

# The portable inflater is shared by every Arduino platform, but it does not
# supply the OTA manager or a supported bootloader/apply path. In particular,
# auto overlays for PicoW and RAK_3x72 must remain buildable without OTA.
for unsupported_ota_env in "${SUPPORTED_PIO_ENVS[@]}"; do
  case "${PIO_ENV_PLATFORM_BY_NAME[$unsupported_ota_env]}" in
    RP2040_PLATFORM|STM32_PLATFORM) ;;
    *) continue ;;
  esac
  [ "${PIO_ENV_OTA_BY_NAME[$unsupported_ota_env]:-0}" = 0 ] \
    || fail "$unsupported_ota_env incorrectly classified its shared inflater as LoRa OTA"
  for unsupported_ota_profile in auto standard; do
    (
      BUILD_PROFILE_FOR_TARGET=$unsupported_ota_profile
      if is_lora_ota_build "$unsupported_ota_env"; then
        fail "$unsupported_ota_env $unsupported_ota_profile enables unsupported LoRa OTA"
      fi
      PLATFORMIO_BUILD_FLAGS=""
      PLATFORMIO_BUILD_UNFLAGS=""
      apply_lora_ota_override "$unsupported_ota_env"
      [[ "$PLATFORMIO_BUILD_FLAGS" == *"-UENABLE_OTA"* ]] \
        || fail "$unsupported_ota_env $unsupported_ota_profile failed to disable unsupported LoRa OTA"
      [[ "$PLATFORMIO_BUILD_FLAGS" != *"-DENABLE_OTA=1"* ]] \
        || fail "$unsupported_ota_env $unsupported_ota_profile emitted missing OTA implementation references"
      [[ "$PLATFORMIO_BUILD_FLAGS" != *"-DCOMPANION_FEATURE_OTA_CLI=1"* ]] \
        || fail "$unsupported_ota_env $unsupported_ota_profile enabled unavailable OTA controls"
    )
  done
done

# Auto means that an explicitly selected target keeps the capabilities in its
# resolved PlatformIO recipe.  T1000-E BLE/USB both inherit nRF52 LoRa OTA; the
# release builder must not silently replace ENABLE_OTA with -UENABLE_OTA merely
# because Companion is not a repeater role.  Standard keeps the existing
# portable-release reduction policy.
for t1000_env in \
    t1000e_companion_radio_ble \
    t1000e_companion_radio_usb; do
  [ "${PIO_ENV_OTA_BY_NAME[$t1000_env]:-0}" = 1 ] \
    || fail "$t1000_env resolved recipe no longer declares LoRa OTA"
  (
    BUILD_PROFILE_FOR_TARGET=auto
    is_lora_ota_build "$t1000_env" \
      || fail "$t1000_env auto profile stripped declared LoRa OTA"

    PLATFORMIO_BUILD_FLAGS=""
    PLATFORMIO_BUILD_UNFLAGS=""
    apply_lora_ota_override "$t1000_env"
    [[ "$PLATFORMIO_BUILD_FLAGS" == *"-DENABLE_OTA=1"* ]] \
      || fail "$t1000_env auto overlay omitted ENABLE_OTA"
    [[ "$PLATFORMIO_BUILD_FLAGS" != *"-UENABLE_OTA"* ]] \
      || fail "$t1000_env auto overlay still disables LoRa OTA"
    [[ "$PLATFORMIO_BUILD_FLAGS" == *"-DCOMPANION_FEATURE_TEMP_RADIO=1"* ]] \
      || fail "$t1000_env auto overlay omitted temporary-radio control"
    [[ "$PLATFORMIO_BUILD_FLAGS" == *"-DCOMPANION_FEATURE_OTA_CLI=1"* ]] \
      || fail "$t1000_env auto overlay omitted OTA CLI control"

    BUILD_CAPABILITIES=()
    BUILD_REDUCTIONS=()
    BUILD_EXPECTATIONS=()
    declare_build_capability_contract "$t1000_env" NRF52_PLATFORM
    expectations=" ${BUILD_EXPECTATIONS[*]} "
    [[ "$expectations" == *"companion.temp_radio=ERR usage: tempradio"* ]] \
      || fail "$t1000_env auto contract omitted temporary-radio verification"
    [[ "$expectations" == *"companion.ota_cli=OTA: status"* ]] \
      || fail "$t1000_env auto contract omitted OTA CLI verification"
  )
  (
    BUILD_PROFILE_FOR_TARGET=standard
    if is_lora_ota_build "$t1000_env"; then
      fail "$t1000_env standard profile bypassed portable OTA policy"
    fi
  )
done

# Every added observer recipe must become the feature base for its exact
# normal-role Full image, without changing that normal target's mOTA identity.
# Only same-partition hardware may advertise an automatic observer successor;
# the four legacy-slot targets must retain their partition-expansion handoff.
for observer_hardware in \
    Ebyte_EoRa-S3 Heltec_E213 Heltec_E290 heltec_rc32 \
    heltec_rc32_without_display Heltec_Wireless_Tracker Heltec_Wireless_Paper \
    LilyGo_T3S3_sx1276 LilyGo_TETH_Elite_sx1262 meshnology_w12 Xiao_S3; do
  for observer_role in repeater room_server; do
    normal_target="${observer_hardware}_${observer_role}"
    observer_target="${normal_target}_observer_mqtt"
    if [ "$normal_target" = Ebyte_EoRa-S3_repeater ]; then
      normal_target=Ebyte_EoRa-S3_Repeater
    fi
    is_mqtt_bridge_target "$observer_target" \
      || fail "$observer_target missing its MQTT feature base"
    [ "${PIO_ENV_OTA_BY_NAME[$observer_target]:-0}" = 1 ] \
      || fail "$observer_target lost LoRa OTA"
    [ "$(get_exact_identity_full_pio_env "$normal_target")" = "$observer_target" ] \
      || fail "$normal_target Full did not use its exact observer recipe"
    case "$observer_hardware" in
      Ebyte_EoRa-S3|LilyGo_T3S3_sx1276)
        is_esp32_partition_migration_full_target "$normal_target" \
          || fail "$normal_target lost its partition-migration boundary"
        if get_exact_identity_full_migration_target "$observer_target" >/dev/null; then
          fail "$observer_target bypassed partition expansion with an OTA alias"
        fi
        ;;
      *)
        is_esp32_full_only_bulk_target "$observer_target" \
          || fail "$observer_target lost same-partition Full policy"
        [ "$(get_exact_identity_full_migration_target "$observer_target")" = "$normal_target" ] \
          || fail "$observer_target lost its exact same-partition successor"
        ;;
    esac
  done
done

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
SenseCapIndicator-LoRa-N16R2_comp_radio_usb_wifi|SenseCapIndicator-LoRa-N16R2_companion_radio_full|SenseCapIndicator-LoRa-N16R2_comp_radio_usb_wifi
Waveshare_S3_LR1121_companion_radio_usb|Waveshare_S3_LR1121_companion_radio_full|Waveshare_S3_LR1121_companion_radio_full
Waveshare_S3_LR1121_companion_radio_ble|Waveshare_S3_LR1121_companion_radio_full|Waveshare_S3_LR1121_companion_radio_full
Wio_Tracker_L2_companion_radio_usb|Wio_Tracker_L2_companion_radio_full|Wio_Tracker_L2_companion_radio_full
Wio_Tracker_L2_companion_radio_ble|Wio_Tracker_L2_companion_radio_full|Wio_Tracker_L2_companion_radio_full
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
  if ! pio_env_option_contains "$pio_env" build_src_filter "helpers/wifi/*.cpp" \
      && ! pio_env_option_contains "$pio_env" build_src_filter \
          "helpers/wifi/SerialWifiInterface.cpp" \
      && [[ "$PLATFORMIO_BUILD_SRC_FILTER" \
          != *"helpers/wifi/SerialWifiInterface.cpp"* ]]; then
    fail "$full_env Full Companion omitted SerialWifiInterface"
  fi
  [[ "$PLATFORMIO_BUILD_FLAGS" == *"WIFI_OTA_SEEDER=1"* ]] \
    || fail "$full_env Full Companion omitted its WiFi runtime overlay"
  [[ "$PLATFORMIO_BUILD_FLAGS" == *"MESHCORE_EXPANDED_PARTITION_PROFILE=1"* ]] \
    || fail "$full_env Full Companion omitted its expanded runtime profile"
  [[ "$PLATFORMIO_BUILD_FLAGS" == *"COMPANION_RADIO_FULL=1"* ]] \
    || fail "$full_env Full Companion omitted its capability identity"
  [[ "$PLATFORMIO_BUILD_FLAGS" == *"BLE_PIN_CODE=123456"* ]] \
    || fail "$full_env Full Companion omitted Bluetooth pairing"
  [[ "$PLATFORMIO_BUILD_FLAGS" == *"COMPANION_FEATURE_USB_MOTA_SOURCE=1"* ]] \
    || fail "$full_env Full Companion left the serial folder source sharing the USB text parser"
  case "${full_env,,}" in
    sensecapindicator-espnow_companion_radio_full|\
    sensecapindicator-lora_companion_radio_full)
      [ "${MESHCORE_ESP32_FULL_PARTITION_TABLE:-}" \
          = "variants/sensecap_indicator-espnow/dual_ota_2560k_preserve_spiffs.csv" ] \
        || fail "$full_env omitted the Indicator preserve-SPIFFS table"
      [[ "$PLATFORMIO_BUILD_FLAGS" == *"INDICATOR_WIFI_FONT_RECOVERY=1"* ]] \
        || fail "$full_env omitted WiFi font recovery"
      ;;
    sensecapindicator-lora-n16r2_companion_radio_full)
      [ "${MESHCORE_ESP32_FULL_PARTITION_TABLE:-}" \
          = "variants/sensecap_indicator-espnow/dual_ota_6400k_preserve_spiffs.csv" ] \
        || fail "$full_env omitted the N16R2 preserve-SPIFFS table"
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

# nRF52 Full targets use the same capability identity and pairing transport,
# plus both host-folder mOTA sources. Check every registered logical target so
# synthetic Full names that share a USB/BLE PlatformIO recipe stay protected.
for full_env in "${SUPPORTED_PIO_ENVS[@]}"; do
  is_nrf52_companion_radio_full_target "$full_env" || continue
  pio_env=$(get_pio_build_env "$full_env")
  PLATFORMIO_BUILD_FLAGS=""
  PLATFORMIO_BUILD_UNFLAGS=""
  PLATFORMIO_BUILD_SRC_FILTER=""
  BUILD_REDUCTIONS=()
  apply_companion_radio_full_profile "$full_env" "$pio_env"

  [[ "$PLATFORMIO_BUILD_FLAGS" == *"COMPANION_RADIO_FULL=1"* ]] \
    || fail "$full_env Full Companion omitted its capability identity"
  [[ "$PLATFORMIO_BUILD_FLAGS" == *"BLE_PIN_CODE=123456"* ]] \
    || fail "$full_env Full Companion omitted Bluetooth pairing"
  [[ "$PLATFORMIO_BUILD_FLAGS" == *"ENABLE_OTA=1"* ]] \
    || fail "$full_env Full Companion omitted LoRa OTA seeding"
  [[ "$PLATFORMIO_BUILD_FLAGS" == *"OTA_FOLDER_SERIAL=1"* ]] \
    || fail "$full_env Full Companion omitted USB folder seeding"
  [[ "$PLATFORMIO_BUILD_FLAGS" == *"CFG_DEBUG=1"* ]] \
    || fail "$full_env Full Companion omitted the validated nRF52 BLE bootstrap"
  [[ "$PLATFORMIO_BUILD_FLAGS" == *"RECOVERABLE_EXTERNAL_RADIO=1"* ]] \
    || fail "$full_env Full Companion must keep Companion transports available while radio recovery runs"
  if [ "$full_env" = "t1000e_companion_radio_full" ]; then
    [[ "$PLATFORMIO_BUILD_FLAGS" == *"COMPANION_FEATURE_BLE_MOTA_SOURCE=1"* ]] \
      || fail "$full_env Full Companion omitted BLE folder seeding"
    [[ "$PLATFORMIO_BUILD_FLAGS" != *"COMPANION_FEATURE_BLE_MOTA_SOURCE=0"* ]] \
      || fail "$full_env must not disable its BLE mOTA extension"
    [[ "$PLATFORMIO_BUILD_FLAGS" != *"COMPANION_FEATURE_BLE_DFU=0"* ]] \
      || fail "$full_env must not disable its BLE DFU extension"
    [[ "$PLATFORMIO_BUILD_FLAGS" != *"COMPANION_FEATURE_DEDICATED_USB_LOGGING=1"* ]] \
      || fail "$full_env must not enable its unstable second USB CDC interface"
    [[ "$PLATFORMIO_BUILD_FLAGS" != *"CFG_TUD_CDC=2"* ]] \
      || fail "$full_env must keep a single USB CDC interface"
    [[ "$PLATFORMIO_BUILD_FLAGS" != *"MESH_DUAL_CDC_LOGGING=1"* ]] \
      || fail "$full_env must keep USB logging on its primary interface"
    [[ "$PLATFORMIO_BUILD_FLAGS" != *"MESH_USB_LOGGING_DISABLED=1"* ]] \
      || fail "$full_env must retain its single-port USB logging terminal"
    [[ "$PLATFORMIO_BUILD_FLAGS" == *"MESH_DEBUG=1"* ]] \
      || fail "$full_env must retain USB diagnostics on its logging terminal"
    [[ "$PLATFORMIO_BUILD_FLAGS" == *"MESH_PACKET_LOGGING=1"* ]] \
      || fail "$full_env must retain packet logging on its logging terminal"
    [[ "$PLATFORMIO_BUILD_FLAGS" != *"COMPANION_BLE_PRPH_MTU="* ]] \
      || fail "$full_env must retain the normal Full-Companion BLE bandwidth"
    [[ "$PLATFORMIO_BUILD_FLAGS" != *"COMPANION_BLUETOOTH_BOOT_DELAY_MS="* ]] \
      || fail "$full_env must start Bluetooth promptly"
  else
    [[ "$PLATFORMIO_BUILD_FLAGS" == *"COMPANION_FEATURE_BLE_MOTA_SOURCE=1"* ]] \
      || fail "$full_env Full Companion omitted BLE folder seeding"
    [[ "$PLATFORMIO_BUILD_FLAGS" == *"COMPANION_FEATURE_DEDICATED_USB_LOGGING=1"* ]] \
      || fail "$full_env Full Companion omitted dedicated USB logging"
    [[ "$PLATFORMIO_BUILD_FLAGS" == *"CFG_TUD_CDC=2"* ]] \
      || fail "$full_env Full Companion omitted its second USB CDC interface"
    [[ "$PLATFORMIO_BUILD_FLAGS" == *"MESH_DUAL_CDC_LOGGING=1"* ]] \
      || fail "$full_env Full Companion omitted dedicated USB logging routing"
    [[ "$PLATFORMIO_BUILD_FLAGS" != *"MESH_USB_LOGGING_DISABLED=1"* ]] \
      || fail "$full_env unexpectedly disabled USB logging"
  fi
  if ! pio_env_option_contains "$pio_env" build_src_filter "helpers/ota/*.cpp" \
      && [[ "$PLATFORMIO_BUILD_SRC_FILTER" != *"helpers/ota/*.cpp"* ]]; then
    fail "$full_env Full Companion omitted the OTA C++ implementation"
  fi
done
unset PLATFORMIO_BUILD_FLAGS PLATFORMIO_BUILD_UNFLAGS \
  PLATFORMIO_BUILD_SRC_FILTER

# An environment that includes only the tiny OTA inflater still needs the C++
# protocol implementation when build.sh overlays LoRa OTA. Room servers use
# the same nRF52 overlay as repeaters.
for reduced_nrf_ota_env in \
    RAK_3401_repeater_lora_ota_no_external_sensors \
    RAK_3401_room_server_lora_ota_no_external_sensors; do
  reduced_nrf_pio_env=$(get_pio_build_env "$reduced_nrf_ota_env")
  PLATFORMIO_BUILD_SRC_FILTER=""
  PLATFORMIO_EXTRA_SCRIPTS=""
  apply_nrf52_lora_ota_build_recipe "$reduced_nrf_ota_env" "$reduced_nrf_pio_env"
  if ! pio_env_option_contains "$reduced_nrf_pio_env" build_src_filter "helpers/ota/*.cpp" \
      && [[ "$PLATFORMIO_BUILD_SRC_FILTER" != *"helpers/ota/*.cpp"* ]]; then
    fail "$reduced_nrf_ota_env omitted the OTA C++ implementation"
  fi
done
unset PLATFORMIO_BUILD_SRC_FILTER PLATFORMIO_EXTRA_SCRIPTS

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

# Full logging is a compiled and packaged contract, including when a legacy
# observer recipe or DISABLE_DEBUG injects an inherited packet-logging -U.
verify_full_logging_contract() {
  local env_name=$1
  local ESP32_FULL_BUILD=$2
  local PACKET_LOGGING_OVERRIDE=$3
  local DISABLE_DEBUG=$4
  local expected=$5
  local combined=$6
  local MESHDEBUG_OVERRIDE=off MQTT_BRIDGE_OVERRIDE="" MQTT_DEBUG_OVERRIDE=""
  local FIRMWARE_FILENAME_INFIX=""
  local BUILD_PROFILE_FOR_TARGET=full
  local -a BUILD_CAPABILITIES=() BUILD_EXPECTATIONS=() BUILD_APPLICATION_EXPECTATIONS=()
  local expectations

  configure_unified_full_infrastructure_output "$env_name"
  if requires_full_usb_packet_logging "$env_name"; then
    [ "$expected" = yes ] || fail "$env_name unexpectedly requires USB logging"
  else
    [ "$expected" = no ] || fail "$env_name omitted its Full USB logging contract"
  fi
  declare_build_capability_contract "$env_name" "${PIO_ENV_PLATFORM_BY_NAME[$env_name]}"
  expectations=" ${BUILD_APPLICATION_EXPECTATIONS[*]} "
  if [ "$expected" = yes ]; then
    [[ "$expectations" == *"logging.usb.packets="* ]] \
      || fail "$env_name does not check packet logging in the packaged application"
    [[ "$expectations" == *"logging.usb.control="* ]] \
      || fail "$env_name does not check working USB logging controls"
  else
    [[ "$expectations" != *"logging.usb."* ]] \
      || fail "$env_name promises logging after it was explicitly omitted"
  fi
  if [ "$combined" = yes ]; then
    [[ "$expectations" == *"logging.usb.output=OK - logging.output %s (saved)"* ]] \
      || fail "$env_name does not verify its combined output selector"
  else
    [[ "$expectations" != *"logging.usb.output="* ]] \
      || fail "$env_name unexpectedly promises a combined output selector"
  fi
}

for logging_target in Station_G2_repeater_observer_mqtt \
    Station_G2_room_server_observer_mqtt heltec_v4_repeater_observer_mqtt \
    heltec_v4_r8_room_server_observer_mqtt; do
  verify_full_logging_contract "$logging_target" 1 "" 0 yes yes
  verify_full_logging_contract "$logging_target" 1 off 1 yes yes
  verify_full_logging_contract "$logging_target" 0 "" 0 no no
done

# Expanded ESP32 infrastructure Full images keep the MQTT observer recipe and
# add the cooperative ESP-NOW bridge source. This is build-time composition,
# not a second standalone image selected by the filename.
ESP32_FULL_BUILD=1
PLATFORMIO_BUILD_FLAGS=""
PLATFORMIO_BUILD_SRC_FILTER=""
BUILD_CAPABILITIES=()
apply_esp32_full_shared_bridge_profile Station_G2_repeater_observer_mqtt
[[ "$PLATFORMIO_BUILD_FLAGS" == *"-DWITH_ESPNOW_BRIDGE=1"* ]] \
  || fail "Station G2 Full MQTT image did not enable ESP-NOW"
[[ "$PLATFORMIO_BUILD_SRC_FILTER" == *"helpers/bridges/ESPNowBridge.cpp"* ]] \
  || fail "Station G2 Full MQTT image did not include the ESP-NOW bridge"
[[ " ${BUILD_CAPABILITIES[*]} " == *" bridge.espnow "* ]] \
  || fail "Station G2 Full MQTT image did not report ESP-NOW capability"
[[ "$PLATFORMIO_BUILD_FLAGS" != *"ESPNOW_BRIDGE_MERGED"* ]] \
  || fail "Station G2 existing MQTT default changed during ESP-NOW composition"
[[ "$(get_unified_full_infrastructure_target Station_G2_repeater_bridge_espnow)" \
    = "Station_G2_repeater_observer_mqtt" ]] \
  || fail "Station G2 Full ESP-NOW target did not resolve to the combined MQTT recipe"

# Full repeaters without an observer compile the real bridge directly. Both
# historical ESP-NOW defaults and unsupported role boundaries stay explicit.
for plain_target in heltec_v4_tft_repeater Heltec_v2_repeater \
    Heltec_ct62_repeater ThinkNode_M2_Repeater ThinkNode_M5_Repeater \
    Meshadventurer_sx1262_repeater Meshadventurer_sx1268_repeater \
    Generic_E22_sx1262_repeater Generic_E22_sx1268_repeater \
    Tenstar_C3_sx1262_repeater Tenstar_C3_sx1268_repeater \
    nibble_zero_connect_repeater_ nibble_screen_connect_repeater_ \
    Station_G2_logging_repeater; do
  PLATFORMIO_BUILD_FLAGS=""
  PLATFORMIO_BUILD_SRC_FILTER=""
  BUILD_CAPABILITIES=()
  apply_esp32_full_shared_bridge_profile "$plain_target"
  [[ "$PLATFORMIO_BUILD_FLAGS" == *"-DWITH_ESPNOW_BRIDGE=1"* ]] \
    || fail "$plain_target Full image omitted the runtime ESP-NOW bridge"
  [[ "$PLATFORMIO_BUILD_FLAGS" == *"-DESPNOW_BRIDGE_MERGED=1"* ]] \
    || fail "$plain_target Full image omitted safe default-off migration"
  [[ "$PLATFORMIO_BUILD_SRC_FILTER" == *"helpers/bridges/ESPNowBridge.cpp"* ]] \
    || fail "$plain_target Full image omitted the actual ESP-NOW driver source"
  [[ " ${BUILD_CAPABILITIES[*]} " == *" bridge.espnow "* ]] \
    || fail "$plain_target Full image omitted the ESP-NOW capability"
  (
    BUILD_PROFILE_FOR_TARGET=full
    BUILD_EXPECTATIONS=()
    declare_build_capability_contract "$plain_target" ESP32_PLATFORM
    [[ " ${BUILD_EXPECTATIONS[*]} " == *"bridge.espnow=_ZN12ESPNowBridge5beginEv"* ]] \
      || fail "$plain_target Full image does not require a linked ESP-NOW driver"
  )
done
for existing_target in Heltec_v2_repeater_bridge_espnow \
    Station_G2_repeater_observer_mqtt; do
  PLATFORMIO_BUILD_FLAGS=""
  apply_esp32_full_shared_bridge_profile "$existing_target"
  [[ "$PLATFORMIO_BUILD_FLAGS" == *"-DWITH_ESPNOW_BRIDGE=1"* ]] \
    || fail "$existing_target existing bridge source was dropped"
  [[ "$PLATFORMIO_BUILD_FLAGS" != *"ESPNOW_BRIDGE_MERGED"* ]] \
    || fail "$existing_target lost historical bridge-on behavior"
done
for excluded_target in heltec_v4_sensor heltec_v4_tft_room_server \
    heltec_v4_tft_companion_radio_full RAK_4631_repeater; do
  PLATFORMIO_BUILD_FLAGS=""
  PLATFORMIO_BUILD_SRC_FILTER=""
  apply_esp32_full_shared_bridge_profile "$excluded_target"
  [ -z "$PLATFORMIO_BUILD_FLAGS$PLATFORMIO_BUILD_SRC_FILTER" ] \
    || fail "$excluded_target acquired unsupported ESP32 repeater bridge wiring"
done
(
  ESP32_FULL_BUILD=0
  PLATFORMIO_BUILD_FLAGS=""
  PLATFORMIO_BUILD_SRC_FILTER=""
  apply_esp32_full_shared_bridge_profile heltec_v4_tft_repeater
  [ -z "$PLATFORMIO_BUILD_FLAGS$PLATFORMIO_BUILD_SRC_FILTER" ] \
    || fail "standard repeater acquired the expanded Full ESP-NOW overlay"
)
for primary_flag in MESH_PRIMARY_ESPNOW MESH_ESPNOW_RADIO; do
  (
    pio_env_option_contains() { [ "$3" = "$primary_flag" ]; }
    PLATFORMIO_BUILD_FLAGS=""
    PLATFORMIO_BUILD_SRC_FILTER=""
    if supports_esp32_full_shared_espnow heltec_v4_tft_repeater; then
      fail "$primary_flag radio acquired a second conflicting SDK owner"
    fi
    apply_esp32_full_shared_bridge_profile heltec_v4_tft_repeater
    [ -z "$PLATFORMIO_BUILD_FLAGS$PLATFORMIO_BUILD_SRC_FILTER" ] \
      || fail "$primary_flag primary radio acquired the secondary bridge overlay"
  )
done

# The TFT's normal Full image now owns ESP-NOW without an observer recipe.
# Canonical selection must replace the sibling in either input order.
for plain_target in heltec_v4_tft_repeater Meshadventurer_sx1262_repeater; do
  espnow_target=${plain_target}_bridge_espnow
  if get_unified_full_infrastructure_target "$espnow_target" >/dev/null; then
    fail "$espnow_target unexpectedly acquired a combined observer recipe"
  fi
  [ "$(select_ordinary_full_targets "$plain_target" "$espnow_target")" \
      = "$plain_target" ] \
    || fail "$plain_target did not replace its dedicated ESP-NOW image"
  [ "$(select_ordinary_full_targets "$espnow_target" "$plain_target")" \
      = "$plain_target" ] \
    || fail "$plain_target input order changed the combined ESP-NOW selection"
  [ "$(get_merged_espnow_repeater_replacement "$espnow_target")" = "$plain_target" ] \
    || fail "$plain_target ESP-NOW legacy target did not map to its combined Full image"
done
[ "$(get_unified_full_infrastructure_target Heltec_v3_repeater_bridge_espnow)" \
    = Heltec_v3_repeater_observer_mqtt ] \
  || fail "Heltec V3 lost its same-board combined observer recipe"
[ "$(select_ordinary_full_targets Heltec_v3_repeater \
    Heltec_v3_repeater_bridge_espnow Heltec_v3_repeater_observer_mqtt)" \
    = Heltec_v3_repeater ] \
  || fail "Heltec V3 retained duplicate ESP-NOW/observer Full images"
[ "$(select_ordinary_full_targets MKE_s3_repeater MKE_s3_repeater_bridge_espnow)" \
    = MKE_s3_repeater ] \
  || fail "MKE S3 retained a duplicate ESP-NOW image after merging its sources"
[ "$(select_ordinary_full_targets MKE_s3_repeater_bridge_espnow MKE_s3_repeater)" \
    = MKE_s3_repeater ] \
  || fail "MKE S3 bridge input order changed its canonical Full image"
tlora_normal=LilyGo_TLora_V2_1_1_6_repeater
tlora_observer=${tlora_normal}_observer_mqtt_
tlora_espnow=${tlora_normal}_bridge_espnow
[ "$(get_ordinary_full_group_key "$tlora_normal")" \
    != "$(get_ordinary_full_group_key "$tlora_observer")" ] \
  || fail "TLora RAM exception collapsed its independent UART and MQTT Full groups"
for tlora_order in normal-first observer-first bridge-first; do
  case "$tlora_order" in
    normal-first) tlora_candidates=("$tlora_normal" "$tlora_observer" "$tlora_espnow") ;;
    observer-first) tlora_candidates=("$tlora_observer" "$tlora_espnow" "$tlora_normal") ;;
    bridge-first) tlora_candidates=("$tlora_espnow" "$tlora_normal" "$tlora_observer") ;;
  esac
  mapfile -t tlora_selected < <(select_ordinary_full_targets "${tlora_candidates[@]}")
  [ "${#tlora_selected[@]}" = 2 ] \
    || fail "TLora $tlora_order did not retain exactly two capacity-safe Full images"
  [[ " ${tlora_selected[*]} " == *" $tlora_normal "* ]] \
    || fail "TLora $tlora_order discarded its UART Full image"
  [[ " ${tlora_selected[*]} " == *" $tlora_observer "* ]] \
    || fail "TLora $tlora_order discarded its MQTT Full image"
  [[ " ${tlora_selected[*]} " != *" $tlora_espnow "* ]] \
    || fail "TLora $tlora_order retained the replaced dedicated ESP-NOW image"
done
ESP32_FULL_BUILD=0
PLATFORMIO_BUILD_FLAGS=""
PLATFORMIO_BUILD_SRC_FILTER=""

for logging_target in Station_G2_repeater_bridge_espnow heltec_v4_sensor; do
  verify_full_logging_contract "$logging_target" 1 "" 0 yes no
  verify_full_logging_contract "$logging_target" 1 on 1 yes no
  verify_full_logging_contract "$logging_target" 1 off 0 no no
  verify_full_logging_contract "$logging_target" 1 "" 1 no no
done
for logging_target in Station_G2_companion_radio_full RAK_4631_companion_radio_full; do
  verify_full_logging_contract "$logging_target" 0 off 1 yes no
done
verify_full_logging_contract t1000e_companion_radio_full 0 off 1 yes no

# Synthetic inventory: ESP32 and nRF52 repeater/room-server targets with the
# same lean OTA policy. The sensor deliberately has no lean sibling.
SUPPORTED_PIO_ENVS=(
  esp_repeater
  esp_repeater_lora_ota_no_external_sensors
  esp_room_server
  esp_room_server_lora_ota_no_external_sensors
  nrf_repeater
  nrf_repeater_lora_ota_no_external_sensors
  nrf_room_server
  nrf_room_server_lora_ota_no_external_sensors
  nrf_qspi_repeater
  nrf_qspi_repeater_lora_ota_no_external_sensors
  nrf_qspi_room_server
  nrf_qspi_room_server_lora_ota_no_external_sensors
  nrf_sensor
)
PIO_ENV_PLATFORM_BY_NAME[esp_repeater]=ESP32_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[esp_repeater_lora_ota_no_external_sensors]=ESP32_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[esp_room_server]=ESP32_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[esp_room_server_lora_ota_no_external_sensors]=ESP32_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[nrf_repeater]=NRF52_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[nrf_repeater_lora_ota_no_external_sensors]=NRF52_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[nrf_room_server]=NRF52_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[nrf_room_server_lora_ota_no_external_sensors]=NRF52_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[nrf_qspi_repeater]=NRF52_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[nrf_qspi_repeater_lora_ota_no_external_sensors]=NRF52_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[nrf_qspi_room_server]=NRF52_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[nrf_qspi_room_server_lora_ota_no_external_sensors]=NRF52_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[nrf_sensor]=NRF52_PLATFORM
PIO_ENV_QSPI_OTA_BY_NAME[nrf_qspi_repeater_lora_ota_no_external_sensors]=1
PIO_ENV_QSPI_OTA_BY_NAME[nrf_qspi_room_server_lora_ota_no_external_sensors]=1
PIO_ENV_FULL_BUILD_BY_NAME[esp_repeater]=1
PIO_ENV_FULL_BUILD_BY_NAME[esp_room_server]=1

RESOLVED_BUILD_TARGETS=(esp_repeater)
configure_effective_build_profile build-firmware >/dev/null
[ "$BUILD_PROFILE_EFFECTIVE" = full ] || fail "ESP32 auto did not prefer Full"
[ "$AUTO_PREFER_FULL_BUILD" = 1 ] || fail "ESP32 Full pass was not scheduled"
[ "$AUTO_REDUCED_FALLBACK_TARGET" = \
  esp_repeater_lora_ota_no_external_sensors ] \
  || fail "ESP32 reduced fallback was not resolved"

BUILD_PROFILE_OVERRIDE=auto
BUILD_PROFILE_EXPLICIT=0
RESOLVED_BUILD_TARGETS=(esp_room_server)
configure_effective_build_profile build-firmware >/dev/null
[ "$BUILD_PROFILE_EFFECTIVE" = full ] || fail "ESP32 room server auto did not prefer Full"
[ "$AUTO_PREFER_FULL_BUILD" = 1 ] || fail "ESP32 room-server Full pass was not scheduled"
[ "$AUTO_REDUCED_FALLBACK_TARGET" = \
  esp_room_server_lora_ota_no_external_sensors ] \
  || fail "ESP32 room-server reduced fallback was not resolved"

BUILD_PROFILE_OVERRIDE=auto
BUILD_PROFILE_EXPLICIT=0
RESOLVED_BUILD_TARGETS=(nrf_repeater)
configure_effective_build_profile build-firmware >/dev/null
[ "${RESOLVED_BUILD_TARGETS[0]}" = \
  nrf_repeater ] || fail "nRF52 profile configuration changed the selected target"
is_nrf52_sensor_ota_pair_target nrf_repeater \
  || fail "nRF52 repeater did not enter the required two-profile policy"
[ "$AUTO_COMPLETE_FIRST_PASS" = 0 ] \
  || fail "nRF52 retained the permissive size-fallback policy"

BUILD_PROFILE_OVERRIDE=auto
BUILD_PROFILE_EXPLICIT=0
RESOLVED_BUILD_TARGETS=(nrf_room_server)
configure_effective_build_profile build-firmware >/dev/null
[ "${RESOLVED_BUILD_TARGETS[0]}" = \
  nrf_room_server ] || fail "nRF52 room profile configuration changed the selected target"
is_nrf52_sensor_ota_pair_target nrf_room_server \
  || fail "nRF52 room server did not enter the required two-profile policy"
[ "$AUTO_COMPLETE_FIRST_PASS" = 0 ] \
  || fail "nRF52 room server retained the permissive size-fallback policy"

BUILD_PROFILE_OVERRIDE=auto
BUILD_PROFILE_EXPLICIT=0
RESOLVED_BUILD_TARGETS=(nrf_qspi_repeater)
configure_effective_build_profile build-firmware >/dev/null
is_nrf52_sensor_ota_pair_target nrf_qspi_repeater \
  || fail "external-storage nRF52 repeater did not enter the pair policy"
[ "$AUTO_COMPLETE_FIRST_PASS" = 0 ] \
  || fail "external-storage nRF52 retained the old fallback policy"

BUILD_PROFILE_OVERRIDE=auto
BUILD_PROFILE_EXPLICIT=0
RESOLVED_BUILD_TARGETS=(nrf_qspi_room_server)
configure_effective_build_profile build-firmware >/dev/null
is_nrf52_sensor_ota_pair_target nrf_qspi_room_server \
  || fail "external-storage nRF52 room server did not enter the pair policy"
[ "$AUTO_COMPLETE_FIRST_PASS" = 0 ] \
  || fail "external-storage nRF52 room server retained the old fallback policy"

BUILD_PROFILE_OVERRIDE=auto
BUILD_PROFILE_EXPLICIT=0
RESOLVED_BUILD_TARGETS=(nrf_sensor)
configure_effective_build_profile build-firmware >/dev/null
[ "$BUILD_PROFILE_EFFECTIVE" = auto ] \
  || fail "nRF52 sensor did not retain its ordinary auto profile"
[ "$AUTO_COMPLETE_FIRST_PASS" = 0 ] \
  || fail "nRF52 sensor retained the permissive size-fallback policy"
is_nrf52_sensor_ota_pair_target nrf_sensor \
  || fail "nRF52 sensor was excluded from the required pair policy"

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
  if uses_merged_standard_usb_logging "$constrained_companion"; then
    fail "$constrained_companion exceeded its 256 KiB flash with packet logging"
  fi
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
  local minimal_lora_ota=${2:-0}
  local PLATFORMIO_BUILD_FLAGS=""
  local PLATFORMIO_BUILD_UNFLAGS=""
  local BUILD_PROFILE_FOR_TARGET=standard
  local ESP32_FULL_BUILD=0
  local MESHDEBUG_OVERRIDE=off
  local PACKET_LOGGING_OVERRIDE=off
  local MQTT_BRIDGE_OVERRIDE=off
  local FIRMWARE_FILENAME_INFIX=""
  local PIO_CONFIG_JSON='[]'
  local -a BUILD_CAPABILITIES=()
  local -a BUILD_REDUCTIONS=()
  local -a BUILD_EXPECTATIONS=()
  local capabilities reductions expectations compact_flag

  PIO_ENV_PLATFORM_BY_NAME[$env_name]=ESP32_PLATFORM
  PIO_ENV_OTA_BY_NAME[$env_name]=$minimal_lora_ota
  apply_lora_ota_override "$env_name"
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
  for compact_flag in ED25519_COMPACT_BASE ED25519_COMPACT_SHA512 OTA_TARGET_NAME_FRONT_CODED; do
    if [ "$minimal_lora_ota" = 1 ]; then
      [[ "$PLATFORMIO_BUILD_FLAGS" == *"-D${compact_flag}=1"* ]] \
        || fail "$env_name did not enable its feature-preserving flash trim"
    else
      [[ "$PLATFORMIO_BUILD_FLAGS" != *"${compact_flag}"* ]] \
        || fail "$env_name changed the ordinary standard implementation"
    fi
  done
  if [ "$minimal_lora_ota" = 1 ]; then
    [[ "$PLATFORMIO_BUILD_FLAGS" == *-DENABLE_OTA=1* \
       && "$PLATFORMIO_BUILD_FLAGS" == *-DOTA_FLASH_STORE=1* \
       && "$PLATFORMIO_BUILD_FLAGS" == *-DOTA_FOLDER_SERIAL* ]] \
      || fail "$env_name removed a LoRa OTA or USB seeding feature to fit"
    [[ "$expectations" == *"ota.update.lora=image_hash MISMATCH after decode"* \
       && "$expectations" == *"ota.cli=OTA: status"* ]] \
      || fail "$env_name stopped verifying its LoRa OTA implementation"
  fi
}

verify_esp32_field_browser_ota heltec_v4_repeater
verify_esp32_field_browser_ota heltec_v4_room_server
verify_esp32_field_browser_ota heltec_v4_sensor
verify_esp32_field_browser_ota heltec_v4_repeater_lora_ota_no_external_sensors 1
verify_esp32_field_browser_ota heltec_v4_room_server_lora_ota_no_external_sensors 1

# The new storage representation is confined to portable ESP32 minimal OTA
# artifacts. Full images and other platforms retain their existing policies.
(
  PLATFORMIO_BUILD_FLAGS=""
  PLATFORMIO_BUILD_UNFLAGS=""
  BUILD_PROFILE_FOR_TARGET=full
  ESP32_FULL_BUILD=1
  apply_esp32_lora_ota_size_profile heltec_v4_repeater_lora_ota_no_external_sensors
  [ -z "$PLATFORMIO_BUILD_FLAGS" ] || fail "Full image received portable flash trims"
  BUILD_PROFILE_FOR_TARGET=standard
  ESP32_FULL_BUILD=0
  apply_esp32_lora_ota_size_profile nrf_repeater_lora_ota_no_external_sensors
  [ -z "$PLATFORMIO_BUILD_FLAGS" ] || fail "nRF52 image received ESP32 portable flash trims"
)

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

# Option 3 must not publish an oversized standard ESP32 image, but its expected
# portable-slot status is satisfied by the mandatory expanded FULL pass.
saved_has_esp32_full_profile=$(declare -f has_esp32_full_profile)
saved_build_firmware=$(declare -f build_firmware)
has_esp32_full_profile() { [ "$1" = esp32_portable_overflow ]; }
build_firmware() { return 42; }
saved_output_dir=$OUTPUT_DIR
OUTPUT_DIR=$version_test_dir
LOGGING_MATRIX_FAILURES=()
LOGGING_MATRIX_DEFERRED_TARGETS=()
DEFER_ESP32_PORTABLE_OVERFLOW_TO_FULL=1
FIRMWARE_FILENAME_INFIX=""
MQTT_BRIDGE_OVERRIDE="off"
if ! run_logged_build_targets esp32_portable_overflow >/dev/null; then
  fail "portable ESP32 overflow made the logging pass fail"
fi
[ ${#LOGGING_MATRIX_FAILURES[@]} -eq 0 ] \
  || fail "portable ESP32 overflow remained a logging-matrix failure"
[ "${LOGGING_MATRIX_DEFERRED_TARGETS[*]}" = esp32_portable_overflow ] \
  || fail "portable ESP32 overflow was not deferred to FULL"
OUTPUT_DIR=$saved_output_dir
unset DEFER_ESP32_PORTABLE_OVERFLOW_TO_FULL
eval "$saved_has_esp32_full_profile"
eval "$saved_build_firmware"

reduced_full_alias=Tbeam_SX1262_repeater_lora_ota_no_external_sensors
[ "$(get_esp32_full_profile_target "$reduced_full_alias")" = Tbeam_SX1262_repeater ] \
  || fail "reduced ESP32 LoRa OTA alias did not resolve to its FULL base target"
has_esp32_full_profile "$reduced_full_alias" \
  || fail "reduced ESP32 LoRa OTA alias lost its expanded FULL replacement"

calls=()
saved_build_firmware_one_profile=$(declare -f build_firmware_one_profile)
build_firmware_one_profile() {
  calls+=("$1:$BUILD_PROFILE_EFFECTIVE:$SKIP_DECLARED_REDUCTIONS:$NRF52_OTA_SENSOR_PROFILE")
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
if run_auto_two_pass_build nrf_repeater_lora_ota_no_external_sensors >/dev/null 2>&1; then
  fail "failed full nRF52 option was hidden by a successful reduced fallback"
fi

[ "${#calls[@]}" -eq 2 ] || fail "size overflow did not run exactly two passes"
[[ "${calls[0]}" == *:auto:1:* ]] || fail "pass 1 unexpectedly applied reductions"
[[ "${calls[1]}" == *:auto:0:reduced ]] || fail "pass 2 did not apply reduced sensor policy"

calls=()
build_firmware_one_profile() {
  calls+=("$1:$BUILD_PROFILE_EFFECTIVE:$SKIP_DECLARED_REDUCTIONS:$NRF52_OTA_SENSOR_PROFILE")
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
[[ "${calls[0]}" == *:auto:1:full ]] \
  || fail "complete nRF52 artifact did not declare the full sensor policy"
[[ "${calls[1]}" == *:auto:0:reduced ]] \
  || fail "reduced nRF52 artifact did not declare the reduced sensor policy"
eval "$saved_build_firmware_one_profile"

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
  RAK_3401_room_server_lora_ota_no_external_sensors 1
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
is_redundant_bulk_build_target heltec_v4_repeater_legacy_partition_test \
  || fail "legacy partition test fixture remained in bulk release builds"

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
[ "$(get_merged_rs232_repeater_replacement MKE_s3_repeater_bridge_rs232)" \
    = MKE_s3_repeater ] \
  || fail "MKE S3 RS232 bridge did not map to its merged repeater"
is_supported_build_env MKE_s3_repeater_bridge_rs232 \
  || fail "MKE S3 legacy RS232 target stopped being directly buildable"
[ "$(get_pio_build_env MKE_s3_repeater_bridge_rs232)" \
    = MKE_s3_repeater_bridge_rs232 ] \
  || fail "MKE S3 direct legacy build lost its bridge-on recipe"
is_redundant_bulk_build_target MKE_s3_repeater_bridge_rs232 \
  || fail "MKE S3 dedicated RS232 image remained in canonical bulk builds"
if is_redundant_bulk_build_target MKE_s3_repeater; then
  fail "MKE S3 canonical repeater was omitted"
fi
is_redundant_bulk_build_target MKE_s3_repeater_bridge_espnow \
  || fail "MKE S3 dedicated ESP-NOW image remained beside its combined repeater"
[ "$(get_merged_espnow_repeater_replacement MKE_s3_repeater_bridge_espnow)" \
    = MKE_s3_repeater ] \
  || fail "MKE S3 ESP-NOW bridge did not map to its merged repeater"
is_supported_build_env MKE_s3_repeater_bridge_espnow \
  || fail "MKE S3 legacy ESP-NOW target stopped being directly buildable"
[ "$(get_pio_build_env MKE_s3_repeater_bridge_espnow)" \
    = MKE_s3_repeater_bridge_espnow ] \
  || fail "MKE S3 direct legacy ESP-NOW build lost its bridge-on recipe"
(
  SUPPORTED_PIO_ENVS=(
    MKE_s3_repeater
    MKE_s3_repeater_bridge_rs232
    MKE_s3_repeater_bridge_espnow
  )
  [ "$(resolve_all_firmwares)" \
      = MKE_s3_repeater ] \
    || fail "MKE S3 bulk inventory did not consolidate both bridge images"
  [ "$(resolve_repeater_firmwares)" \
      = MKE_s3_repeater ] \
    || fail "MKE S3 repeater inventory retained dedicated bridge images"
  [ "$(resolve_full_esp32_firmwares)" \
      = MKE_s3_repeater ] \
    || fail "MKE S3 Full ESP32 inventory retained dedicated bridge images"
)

# A hardcoded merged-role recommendation is insufficient if its normal
# recipe no longer exists, cannot supply Full, or belongs to another board.
# Isolate these metadata faults so a future observer cannot mask the guard.
for replacement_fault in missing full board; do
  (
    normal_target=MKE_s3_repeater
    bridge_target=MKE_s3_repeater_bridge_espnow
    PIO_ENV_PLATFORM_BY_NAME[$normal_target]=ESP32_PLATFORM
    PIO_ENV_FULL_BUILD_BY_NAME[$normal_target]=1
    PIO_ENV_BOARD_BY_NAME[$normal_target]=mke-test-board
    PIO_ENV_PLATFORM_BY_NAME[$bridge_target]=ESP32_PLATFORM
    PIO_ENV_FULL_BUILD_BY_NAME[$bridge_target]=1
    PIO_ENV_BOARD_BY_NAME[$bridge_target]=mke-test-board
    for candidate in "$normal_target" "${normal_target}_mqtt" \
        "${normal_target}_observer_mqtt" "${normal_target}_observer_mqtt_"; do
      PIO_ENV_MQTT_BY_NAME[$candidate]=0
    done
    case "$replacement_fault" in
      missing) unset 'PIO_ENV_PLATFORM_BY_NAME[MKE_s3_repeater]' ;;
      full) PIO_ENV_FULL_BUILD_BY_NAME[$normal_target]=0 ;;
      board) PIO_ENV_BOARD_BY_NAME[$normal_target]=different-board ;;
    esac
    [ "$(get_ordinary_full_group_key "$bridge_target")" \
        = 'mke-test-board|mke_s3_repeater_bridge_espnow' ] \
      || fail "MKE S3 $replacement_fault replacement incorrectly suppressed its bridge"
  )
done
if is_redundant_bulk_build_target wio-e5-repeater_bridge_rs232; then
  fail "capacity-constrained Wio-E5 RS232 bridge was incorrectly merged"
fi

(
  BUILD_PROFILE_FOR_TARGET=full
  for uart_target in Heltec_v3_repeater Heltec_WSL3_repeater RAK_3112_repeater \
      LilyGo_TLora_V2_1_1_6_repeater; do
    BUILD_EXPECTATIONS=()
    declare_build_capability_contract "$uart_target" ESP32_PLATFORM
    [[ " ${BUILD_EXPECTATIONS[*]} " == *"bridge.rs232=_ZN11RS232Bridge5beginEv"* ]] \
      || fail "$uart_target Full contract omitted the linked UART driver"
    [[ " ${BUILD_EXPECTATIONS[*]} " == *"bridge.espnow=_ZN12ESPNowBridge5beginEv"* ]] \
      || fail "$uart_target Full contract omitted the linked ESP-NOW driver"
  done
  BUILD_EXPECTATIONS=()
  declare_build_capability_contract LilyGo_TLora_V2_1_1_6_repeater_observer_mqtt_ ESP32_PLATFORM
  [[ " ${BUILD_EXPECTATIONS[*]} " != *"bridge.rs232="* ]] \
    || fail "TLora MQTT capacity exception falsely promised a linked UART driver"
  [[ " ${BUILD_EXPECTATIONS[*]} " == *"bridge.espnow=_ZN12ESPNowBridge5beginEv"* ]] \
    || fail "TLora MQTT capacity exception omitted its linked ESP-NOW driver"
)

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

# Every ESP32 Full Companion must ship the official app's WiFi controls.
# These checks apply to the packaged binary, not debug strings in the ELF.
for wifi_target in Station_G2_companion_radio_full \
    heltec_v4_2_v4_3_companion_radio_full_femon \
    SenseCapIndicator-LoRa_companion_radio_full; do
  declare_build_capability_contract "$wifi_target" ESP32_PLATFORM
  app_expectations=" ${BUILD_APPLICATION_EXPECTATIONS[*]} "
  for operation in get_enabled set_enabled get_ssid set_ssid get_password set_password get_status get_ip; do
    [[ "$app_expectations" == *"companion.app_wifi.${operation}="* ]] \
      || fail "$wifi_target omitted app WiFi $operation from packaged qualification"
  done
done

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
[[ "$expectations" == *"companion.usb_mota_source=ota folder on"* ]] \
  || fail "ESP32 Full contract omitted exclusive USB folder seeding"

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
[[ " ${BUILD_APPLICATION_EXPECTATIONS[*]} " != *"companion.app_wifi."* ]] \
  || fail "nRF52 Full contract promised ESP32 app WiFi settings"

BUILD_PROFILE_FOR_TARGET=standard
for rak_target in \
    RAK_3401_repeater_lora_ota_no_external_sensors \
    RAK_4631_repeater_lora_ota_no_external_sensors; do
  BUILD_CAPABILITIES=()
  BUILD_REDUCTIONS=()
  BUILD_EXPECTATIONS=()
  declare_build_capability_contract "$rak_target" NRF52_PLATFORM
  expectations=" ${BUILD_EXPECTATIONS[*]} "
  [[ "$expectations" == *"ota.update.lora=invalid in-place patch geometry"* ]] \
    || fail "$rak_target OTA contract omitted LoRa self-update"
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
