# Assertion sections for esp32-release. Called by test_build_profiles.sh.
# Original section bodies stay byte-for-byte intact; see build_profile_sections.json.

build_profile_debug_and_release_utilities() {
# build-profile-body: debug_and_release_utilities
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

# build-profile-end: debug_and_release_utilities
}

build_profile_esp32_runtime_policy() {
# build-profile-body: esp32_runtime_policy
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
grep -Fq 'return ESP32_POST_BOOT_CPU_FREQ;' src/helpers/esp32/CpuClockPolicy.h \
  || fail "Companion power saving ignores the RC32 post-boot nominal clock"
grep -Fq 'board.onBootComplete();' examples/simple_sensor/main.cpp \
  || fail "sensor firmware never applies board post-boot policy"

# build-profile-end: esp32_runtime_policy
}

build_profile_output_versions() {
# build-profile-body: output_versions
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

# build-profile-end: output_versions
}

build_profile_resolved_board_inheritance() {
# build-profile-body: resolved_board_inheritance
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

# Board-base inheritance must preserve the original memory budget and
# code placement of the small migration utilities.
for target in ("Xiao_S3_WIO_partition_expander",
               "Waveshare_S3_LR1121_partition_expander_repeater_observer_mqtt",
               "Waveshare_S3_LR1121_partition_expander_room_server_observer_mqtt"):
    enabled = set(re.findall(r"MC_ESP32_RADIO_(?:FAST|COMMAND|SPI)_CODE=1",
                             option_text(target, "build_flags")))
    removed = set(re.findall(r"MC_ESP32_RADIO_(?:FAST|COMMAND|SPI)_CODE=1",
                             option_text(target, "build_unflags")))
    if enabled - removed:
        raise SystemExit(f"test_build_profiles: {target} changed utility code placement")
    if "-mtext-section-literals" in option_text(target, "build_flags"):
        require(target, "build_unflags", "-mtext-section-literals")

# PSRAM must survive inheritance into every role, not just Full Companion.
# Keep the proven RAK3112 DIO flash header with the octal-PSRAM SDK.
psram_families = ("RAK_3112_", "ThinkNode_M2_", "ThinkNode_M5_")
for section in sections:
    if not section.startswith("env:"):
        continue
    target = section.removeprefix("env:")
    if not target.startswith(psram_families):
        continue
    require(target, "build_flags", "BOARD_HAS_PSRAM")
    require(target, "board_build.arduino.memory_type", "qio_opi")
    require(target, "board_build.psram_type", "opi")
    if target.startswith("RAK_3112_"):
        require(target, "board_build.flash_mode", "dio")

# New family images select their driver at startup. Their resolved recipes
# must use only the universal target source, while legacy names stay exact.
for prefix, board, directory, legacy_dirs in (
    ("Tbeam_", "ttgo-t-beam", "lilygo_tbeam_universal",
     ("lilygo_tbeam_SX1262", "lilygo_tbeam_SX1276")),
    ("LilyGo_T3S3_", "t3_s3_v1_x", "lilygo_t3s3_universal",
     ("lilygo_t3s3", "lilygo_t3s3_sx1276")),
):
    suffixes = ("repeater", "repeater_bridge_espnow", "room_server", "kiss_modem",
                "repeater_observer_mqtt", "room_server_observer_mqtt",
                "companion_radio_ble", "companion_radio_ble_ps")
    if prefix == "LilyGo_T3S3_":
        suffixes += ("companion_radio_usb", "terminal_chat")
    for suffix in suffixes:
        target = prefix + suffix
        require(target, "board", board)
        require(target, "build_src_filter", "+<../variants/" + directory + ">")
        for legacy_dir in legacy_dirs:
            reject(target, "build_src_filter", "+<../variants/" + legacy_dir + ">")
        for flag in ("MESH_RADIO_AUTO_FAMILY=1", "RADIO_CLASS=UniversalLoRaRadio",
                     "WRAPPER_CLASS=UniversalLoRaRadio", "USE_SX1262", "USE_SX1276"):
            require(target, "build_flags", flag)
        for old_class in ("RADIO_CLASS=CustomSX1262", "RADIO_CLASS=CustomSX1276"):
            reject(target, "build_flags", old_class)
        if prefix == "Tbeam_":
            require(target, "build_flags", "P_LORA_BUSY=32")
            require(target, "build_flags", "P_LORA_DIO_0=26")
            require(target, "build_flags", "ENV_INCLUDE_GPS=1")
        else:
            require(target, "build_flags", "P_LORA_BUSY=34")
            require(target, "build_flags", "P_LORA_DIO_0=9")
            require(target, "build_flags", "PIN_OLED_RESET=-1")
            reject(target, "build_flags", "SX127X_RXEN")
            reject(target, "build_flags", "SX127X_TXEN")
for legacy, driver in (("Tbeam_SX1262_repeater", "CustomSX1262"),
                       ("Tbeam_SX1276_repeater", "CustomSX1276"),
                       ("LilyGo_T3S3_sx1262_repeater", "CustomSX1262"),
                       ("LilyGo_T3S3_sx1276_repeater", "CustomSX1276")):
    require(legacy, "build_flags", "RADIO_CLASS=" + driver)
    reject(legacy, "build_flags", "MESH_RADIO_AUTO_FAMILY")

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
cardputer = "M5Stack_Cardputer_Adv_companion_radio_ble"
require(cardputer, "board", "m5stack-stamps3")
require(cardputer, "board_build.partitions", "default_8MB.csv")
for flag in ("CARDPUTER_ADV_UI=1", "MESH_CONTACT_CACHE=1", "MAX_CONTACTS=350",
             "MAX_GROUP_CHANNELS=40", "OFFLINE_QUEUE_SIZE=256",
             "ENABLE_USB_INTERFACE", "BLE_PIN_CODE=123456",
             "RECOVERABLE_EXTERNAL_RADIO", "ENV_INCLUDE_GPS=1"):
    require(cardputer, "build_flags", flag)
for source in ("examples/companion_radio", "variants/m5stack_cardputer_adv/companion_radio",
               "variants/m5stack_cardputer_adv/hw"):
    require(cardputer, "build_src_filter", source)
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

# Unified ordinary V4 keeps the sensor/RTC bus on 4/3. OLED uses a private
# controller on 17/18, so changing display type cannot move the sensor bus.
for env_name in (
    "heltec_v4_repeater",
    "heltec_v4_room_server",
    "heltec_v4_sensor",
    "heltec_v4_2_v4_3_companion_radio_full_femon",
    "heltec_v4_tft_sensor",
    "heltec_v4_expansionkit_tft_companion_radio_ble_femon",
    "heltec_v4_expansionkit_tft_companion_radio_full_femon",
    "heltec_v4_tft_companion_radio_full_femon",
):
    require(env_name, "build_flags", "HELTEC_V4_DISPLAY_UNIFIED=1")
    require(env_name, "build_flags", "DISPLAY_CLASS=HeltecV4Display")
    require(env_name, "build_flags", "PIN_BOARD_SDA=4")
    require(env_name, "build_flags", "PIN_BOARD_SCL=3")
    require(env_name, "build_flags", "ENV_PIN_SDA=4")
    require(env_name, "build_flags", "ENV_PIN_SCL=3")
    for driver in ("HeltecV4Display", "SSD1306Display", "ST7789LCDDisplay"):
        require(env_name, "build_src_filter", "helpers/ui/" + driver + ".cpp")

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

require("WioTrackerL1_companion_radio_usb", "build_flags", "UI_NO_HIBERNATE")
require("WioTrackerL1_companion_radio_ble", "build_flags", "WIO_L1_DISPLAY_UNIFIED=1")
require("WioTrackerL1_companion_radio_ble", "build_flags", "DISPLAY_CLASS=WioL1Display")
reject("WioTrackerL1_companion_radio_ble", "build_flags", "UI_NO_HIBERNATE")

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

# build-profile-end: resolved_board_inheritance
}

build_profile_interactive_release_menu() {
# build-profile-body: interactive_release_menu
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

# build-profile-end: interactive_release_menu
}

build_profile_esp32_canonical_release() {
# build-profile-body: esp32_canonical_release
# Expanded partitions make Full the ordinary qualified ESP32 node release.
# Legacy lean names remain directly buildable but must not reappear beside
# their qualified Full replacement in all-role or role-specific release lists.
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

# build-profile-end: esp32_canonical_release
}

build_profile_release_matrix_scheduling() {
# build-profile-body: release_matrix_scheduling
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

# build-profile-end: release_matrix_scheduling
}

build_profile_esp32_full_ota_roles() {
# build-profile-body: esp32_full_ota_roles
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

# build-profile-end: esp32_full_ota_roles
}

build_profile_esp32_observer_identity() {
# build-profile-body: esp32_observer_identity
# Every added observer recipe must become the feature base for its exact
# normal-role Full image, without changing that normal target's mOTA identity.
# Only same-partition hardware may advertise an automatic observer successor;
# the four legacy-slot targets must retain their partition-expansion handoff.
for observer_hardware in \
    Ebyte_EoRa-S3 Heltec_E213 Heltec_E290 heltec_rc32 \
    heltec_rc32_without_display Heltec_Wireless_Tracker Heltec_Wireless_Paper \
    LilyGo_T3S3 LilyGo_T3S3_sx1276 LilyGo_TETH_Elite_sx1262 meshnology_w12 Xiao_S3; do
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
      Ebyte_EoRa-S3|LilyGo_T3S3|LilyGo_T3S3_sx1276)
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
# build-profile-end: esp32_observer_identity
}

build_profile_esp32_async_tcp() {
# build-profile-body: esp32_async_tcp
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

# build-profile-end: esp32_async_tcp
}

build_profile_esp32_logging_contract() {
# build-profile-body: esp32_logging_contract
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

# build-profile-end: esp32_logging_contract
}

build_profile_esp32_merged_bridges() {
# build-profile-body: esp32_merged_bridges
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
[[ "$PLATFORMIO_BUILD_FLAGS" == *"-DWITH_UDP_BRIDGE=1"* ]] \
  || fail "Station G2 Full repeater did not enable UDP bridge support"
[[ "$PLATFORMIO_BUILD_SRC_FILTER" == *"helpers/bridges/UDPBridge.cpp"* \
    && "$PLATFORMIO_BUILD_SRC_FILTER" == *"helpers/UDPBridgeSettings.cpp"* ]] \
  || fail "Station G2 Full repeater did not include UDP bridge and settings sources"
[[ " ${BUILD_CAPABILITIES[*]} " == *" bridge.udp "* ]] \
  || fail "Station G2 Full repeater omitted the UDP capability"
(
  PLATFORMIO_BUILD_FLAGS=""
  PLATFORMIO_BUILD_SRC_FILTER=""
  BUILD_CAPABILITIES=()
  apply_esp32_full_shared_bridge_profile Station_G2_room_server_observer_mqtt
  [[ "$PLATFORMIO_BUILD_FLAGS" != *"WITH_UDP_BRIDGE"* \
      && "$PLATFORMIO_BUILD_SRC_FILTER" != *"UDPBridge"* ]] \
    || fail "room server acquired unsupported UDP repeater wiring"
)

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
  [[ "$PLATFORMIO_BUILD_FLAGS" == *"-DWITH_UDP_BRIDGE=1"* \
      && "$PLATFORMIO_BUILD_SRC_FILTER" == *"helpers/bridges/UDPBridge.cpp"* ]] \
    || fail "$plain_target Full image omitted default-off UDP support"
  [[ " ${BUILD_CAPABILITIES[*]} " == *" bridge.udp "* ]] \
    || fail "$plain_target Full image omitted UDP capability"

  (
    BUILD_PROFILE_FOR_TARGET=full
    BUILD_EXPECTATIONS=()
    declare_build_capability_contract "$plain_target" ESP32_PLATFORM
    [[ " ${BUILD_EXPECTATIONS[*]} " == *"bridge.espnow=_ZN12ESPNowBridge5beginEv"* ]] \
      || fail "$plain_target Full image does not require a linked ESP-NOW driver"
    [[ " ${BUILD_EXPECTATIONS[*]} " == *"bridge.udp=_ZN9UDPBridge5beginEv"* ]] \
      || fail "$plain_target Full image does not require a linked UDP driver"
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
    pio_env_has_effective_define() { [ "$2" = "$primary_flag" ]; }
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

# build-profile-end: esp32_merged_bridges
}

