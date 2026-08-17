#!/usr/bin/env bash

ALL_PIO_ENVS=()
SUPPORTED_PIO_ENVS=()
declare -A PIO_ENV_PLATFORM_BY_NAME=()
declare -A PIO_ENV_BOARD_BY_NAME=()
declare -A PIO_ENV_MQTT_BY_NAME=()
declare -A PIO_ENV_OTA_BY_NAME=()
declare -A PIO_ENV_SD_OTA_BY_NAME=()
declare -A PIO_ENV_BUILD_BASE_BY_NAME=()
declare -A PIO_ENV_FULL_BUILD_BY_NAME=()
declare -A PIO_ENV_FULL_WIFI_OTA_BY_NAME=()
PIO_CONFIG_JSON=""
MENU_CHOICE=""
SELECTED_TARGET=""
SELECTED_COMMAND_ARGS=()
MESHDEBUG_OVERRIDE=""
PACKET_LOGGING_OVERRIDE=""
MQTT_BRIDGE_OVERRIDE=""
MQTT_DEBUG_OVERRIDE=""
FIRMWARE_FILENAME_INFIX=""
ESP32_FULL_BUILD=0
SINGLE_TARGET_FULL_BUILD=0
RADIO_SETTINGS_API_URL="https://api.meshcore.nz/api/v1/config"
RADIO_SETTING_TITLE=""
RADIO_FREQ_OVERRIDE=""
RADIO_BW_OVERRIDE=""
RADIO_SF_OVERRIDE=""
RADIO_CR_OVERRIDE=""
FIRMWARE_PROFILE_OVERRIDE="${FIRMWARE_PROFILE_OVERRIDE:-}"
BATCH_BUILD_MODE=0
OPTION3_BUILD_WORKERS="${OPTION3_BUILD_WORKERS:-2}"
OPTION3_PIO_JOBS="${OPTION3_PIO_JOBS:-8}"
PROFILE_BUILD_WORKERS=1
PIO_BUILD_JOBS_OVERRIDE=""
PIO_BUILD_DIR_OVERRIDE=""
RESOLVED_BUILD_TARGETS=()
RESUME_BUILD_OUTPUT="${RESUME_BUILD_OUTPUT:-0}"
LOGGING_MATRIX_FAILURES=()
RADIO_PRESET_SELECTION=""
KISS_MODE_OVERRIDE=""
PARSED_COMMAND_ARGS=()
FIRMWARE_VERSION_EXPLICIT=0
OUTPUT_POLICY_EXPLICIT=0

ENV_VARIANT_SUFFIX_PATTERN='companion_radio_(wifi_mqtt|serial|wifi|usb|ble|full)(_ps)?(_fem(on|off))?|companion_radio_ethernet|comp_radio_usb|companion_usb|companion_ble|repeater_bridge_rs232_serial1_lora_ota_no_external_sensors|repeater_bridge_rs232_serial2_lora_ota_no_external_sensors|repeater_bridge_rs232_lora_ota_no_external_sensors|repeater_lora_ota_no_external_sensors|repeater_bridge_rs232_serial1|repeater_bridge_rs232_serial2|repeater_bridge_rs232|repeater_bridge_espnow|repeater_observer_mqtt|repeater_ethernet|room_server_observer_mqtt|room_server_ethernet|terminal_chat|room_server|room_svr|kiss_modem|sensor|repeatr|repeater'
BOARD_MODIFIER_WITHOUT_DISPLAY="_without_display"
BOARD_MODIFIER_LOGGING="_logging"
BOARD_MODIFIER_TFT="_tft"
BOARD_MODIFIER_EINK="_eink"
BOARD_MODIFIER_EINK_SUFFIX="Eink"
BOARD_LABEL_WITHOUT_DISPLAY="without_display"
BOARD_LABEL_LOGGING="logging"
BOARD_LABEL_TFT="tft"
BOARD_LABEL_EINK="eink"
IKOKA_HANDHELD_NRF_BOARD_FAMILY="ikoka_handheld_nrf_e22_30dbm"
DEFAULT_VARIANT_LABEL="default"
TAG_PREFIX_ROOM_SERVER="room-server"
TAG_PREFIX_COMPANION="companion"
TAG_PREFIX_REPEATER="repeater"
TAG_PREFIX_SENSOR="sensor"
SUPPORTED_PLATFORM_PATTERN='ESP32_PLATFORM|NRF52_PLATFORM|STM32_PLATFORM|RP2040_PLATFORM'
OUTPUT_DIR="${OUTPUT_DIR:-out}"
ESP32_LORA_OTA_APP_LIMIT=$((0x150000 - 0x10000))
REPEATER_MAX_NEIGHBOURS=254
DRAM_LIMITED_MAX_NEIGHBOURS=50
ESP32_FULL_MAX_NEIGHBOURS=$REPEATER_MAX_NEIGHBOURS
FALLBACK_VERSION_PREFIX="dev"
FALLBACK_VERSION_DATE_FORMAT='+%Y-%m-%d-%H-%M'

# External programs invoked by this script:
#   bash, cat, cp, date, find, git, grep, head, mkdir, mv, pgrep, pio,
#   python3, rm, sed, sleep, sort, wc
# Keep this list in sync when adding or removing non-builtin command usage.

global_usage() {
  cat - <<EOF
Usage:
bash build.sh <command> [target] [options]

Commands:
  help|usage|-h|--help: Shows this message.
  list|-l: List firmwares available to build.
  build-firmware <target>: Build the firmware for the given build target.
  build-firmwares: Build all firmwares for all targets.
  build-firmwares-logging-matrix: Build all firmwares in standard, logging, MQTT, FULL ESP32 MQTT, and FULL ESP32 logging (no MQTT) profiles, logging each target under out/build-logs/ and continuing after failures.
  build-companion-firmwares-logging-matrix: Build every Companion target (including Full Companion and legacy FEM variants) in each applicable standard, logging, MQTT, and expanded FULL profile.
  build-full-esp32-firmwares: Build only feature-complete ESP32 MQTT profiles with up to 254 neighbors, LoRa OTA, and expanded dual-OTA partitions.
  build-full-esp32-logging-firmwares: Build only feature-complete ESP32 profiles with up to 254 neighbors, logging, MQTT disabled, LoRa OTA, and expanded dual-OTA partitions.
  build-matching-firmwares <build-match-spec>: Build all firmwares for build targets containing the string given for <build-match-spec>.
  build-companion-firmwares: Build canonical companion firmwares; legacy _femoff targets remain available as direct builds.
  build-full-companion-firmwares: Build canonical full Companion firmwares for supported ESP32 and nRF52 targets.
  build-repeater-firmwares: Build all repeater firmwares with 254 neighbors, except DRAM-limited targets that retain 50.
  build-room-server-firmwares: Build all chat room server firmwares for all build targets.
  build-sensor-firmwares: Build all sensor firmwares for all build targets.
  build-kiss-radio-firmwares: Build all KISS radio firmwares for all build targets.
  get-companion-firmwares-to-build: List USB and BLE companion targets for release automation.
  get-repeater-firmwares-to-build: List standard repeater targets for release automation.
  get-room-server-firmwares-to-build: List standard room-server targets for release automation.

Options:
  --firmware-version <version>: Firmware version to embed.
  --radio-preset <number>: Use the numbered radio choice from the interactive menu (1 keeps target defaults).
  --profile <default|cascade>: Select the firmware settings profile.
  --skip-kiss|--include-kiss: Exclude or include KISS modem targets in bulk builds.
  --clean|--resume: Clean output or resume existing Option 3/FULL-only artifacts.

Examples:
Build firmware for the "RAK_4631_repeater" device target
$ bash build.sh build-firmware RAK_4631_repeater

Run without arguments to choose an interactive build action/target, an optional
FULL-everything profile for supported ESP32 Option 1 targets, debug options,
radio settings, firmware profile, and firmware version
$ bash build.sh

Build all firmwares for device targets containing the string "RAK_4631"
$ bash build.sh build-matching-firmwares <build-match-spec>

Build all firmwares in standard, USB logging, MQTT observer, feature-complete ESP32, and feature-complete ESP32 logging profiles:
$ bash build.sh build-firmwares-logging-matrix

Build only feature-complete ESP32 firmware:
$ bash build.sh build-full-esp32-firmwares

Build only feature-complete ESP32 firmware with logging:
$ bash build.sh build-full-esp32-logging-firmwares

Build all companion firmwares
$ bash build.sh build-companion-firmwares

Build the complete Companion-only release matrix
$ bash build.sh build-companion-firmwares-logging-matrix

Build all full Companion firmwares
$ bash build.sh build-full-companion-firmwares

Build all repeater firmwares
$ bash build.sh build-repeater-firmwares

Build all chat room server firmwares
$ bash build.sh build-room-server-firmwares

Build all sensor firmwares
$ bash build.sh build-sensor-firmwares

Build all kiss radio firmwares
$ bash build.sh build-kiss-radio-firmwares

Environment Variables:
  FIRMWARE_VERSION=vX.Y.Z: Firmware version to embed in the build output.
                           If not set, build.sh first refreshes tags from upstream
                           when configured (otherwise origin), then derives a
                           default from the latest matching tag and appends "-dev".
                           In interactive builds, this value is offered as the editable default.
                           A single custom version suffix found in existing OUTPUT_DIR
                           artifacts is carried forward after the new numeric version.
  DISABLE_DEBUG=1: Disables all debug logging flags (MESH_DEBUG, MESH_PACKET_LOGGING, etc.)
                   If not set, debug flags from variant platformio.ini files are used.
  RESUME_BUILD_OUTPUT=1: Preserves out/ and skips targets whose expected output
                         artifacts already exist. Option 3 resumes by default.
  OUTPUT_DIR=path: Writes artifacts outside out/ (useful for isolated test builds).
  OPTION3_BUILD_WORKERS=2: Concurrent targets per Option 3 profile pass.
  OPTION3_PIO_JOBS=8: Compiler jobs assigned to each concurrent Option 3 target.

Examples:
Build without debug logging:
$ export FIRMWARE_VERSION=v1.0.0
$ export DISABLE_DEBUG=1
$ bash build.sh build-firmware RAK_4631_repeater

Build with debug logging (default, uses flags from variant files):
$ export FIRMWARE_VERSION=v1.0.0
$ bash build.sh build-firmware RAK_4631_repeater

Build with the derived default version from git tags:
$ unset FIRMWARE_VERSION
$ bash build.sh
EOF
}

init_project_context() {
  if [ ${#ALL_PIO_ENVS[@]} -eq 0 ]; then
    mapfile -t ALL_PIO_ENVS < <(pio project config | grep 'env:' | sed 's/env://')
  fi

  if [ -z "$PIO_CONFIG_JSON" ]; then
    PIO_CONFIG_JSON=$(pio project config --json-output)
  fi

  if [ ${#SUPPORTED_PIO_ENVS[@]} -eq 0 ]; then
    while IFS=$'\t' read -r env_name env_platform env_mqtt env_ota env_sd_ota env_full env_full_wifi env_board; do
      if [ -z "$env_name" ] || [ -z "$env_platform" ]; then
        continue
      fi
      SUPPORTED_PIO_ENVS+=("$env_name")
      PIO_ENV_PLATFORM_BY_NAME["$env_name"]=$env_platform
      PIO_ENV_BOARD_BY_NAME["$env_name"]=$env_board
      PIO_ENV_MQTT_BY_NAME["$env_name"]=$env_mqtt
      PIO_ENV_OTA_BY_NAME["$env_name"]=$env_ota
      PIO_ENV_SD_OTA_BY_NAME["$env_name"]=$env_sd_ota
      PIO_ENV_FULL_BUILD_BY_NAME["$env_name"]=$env_full
      PIO_ENV_FULL_WIFI_OTA_BY_NAME["$env_name"]=$env_full_wifi
    done < <(
      python3 -c '
import json
import re
import sys

pattern = re.compile(sys.argv[1])
data = json.load(sys.stdin)
for section, options in data:
    if not section.startswith("env:"):
        continue
    env_name = section[4:]
    mqtt_enabled = False
    ota_enabled = False
    ota_disabled = False
    sd_ota = False
    admin_enabled = False
    espnow_enabled = "bridge_espnow" in env_name.lower()
    full_wifi_ota = False
    board = None
    platform = None
    for key, value in options:
        values = value if isinstance(value, list) else str(value).split()
        if key == "board":
            board = str(value)
            continue
        if key == "lib_deps":
            full_wifi_ota = any("AsyncElegantOTA" in str(item) for item in values)
            continue
        if key == "build_src_filter":
            ota_enabled = any("helpers/ota/" in str(item) for item in values)
            continue
        if key != "build_flags":
            continue
        for flag in values:
            if "WITH_MQTT_BRIDGE" in str(flag):
                mqtt_enabled = True
            if "ADMIN_PASSWORD" in str(flag):
                admin_enabled = True
            if "DISABLE_LORA_OTA" in str(flag):
                ota_disabled = True
            if "OTA_SD_STORE" in str(flag):
                sd_ota = True
            match = pattern.search(str(flag))
            if match and platform is None:
                platform = match.group(0)
    if platform:
        full_enabled = platform == "ESP32_PLATFORM" and (
            mqtt_enabled or espnow_enabled or admin_enabled
        )
        board_value = board or "-"
        print(
            f"{env_name}\t{platform}\t{1 if mqtt_enabled else 0}"
            f"\t{1 if ota_enabled and not ota_disabled else 0}"
            f"\t{1 if sd_ota else 0}"
            f"\t{1 if full_enabled else 0}\t{1 if full_wifi_ota else 0}"
            f"\t{board_value}"
        )
' "$SUPPORTED_PLATFORM_PATTERN" <<<"$PIO_CONFIG_JSON"
    )

    # Keep each ordinary repeater environment feature-rich (including external
    # sensors), and expose a separately named no-external-sensors OTA build for
    # every ESP32/nRF52 repeater role. These two platforms have a complete apply
    # path; RP2040 and STM32 do not yet have the required bootloader/apply path.
    local env_name ota_env full_env usb_env ble_env
    local -a base_envs=("${SUPPORTED_PIO_ENVS[@]}")
    for env_name in "${base_envs[@]}"; do
      case "${PIO_ENV_PLATFORM_BY_NAME[$env_name]}" in
        ESP32_PLATFORM|NRF52_PLATFORM) ;;
        *) continue ;;
      esac
      # Generate one lean LoRa-OTA image for each board's standalone repeater
      # role. Observer, Ethernet, and bridge profiles are separate roles; any
      # purpose-built OTA versions of those remain explicit PlatformIO targets.
      case "${env_name,,}" in
        *_repeater|*_repeater_|*_repeatr|*_repeatr_) ;;
        *) continue ;;
      esac
      ota_env="${env_name%_}_lora_ota_no_external_sensors"
      if [ -n "${PIO_ENV_PLATFORM_BY_NAME[$ota_env]+x}" ]; then
        continue
      fi
      SUPPORTED_PIO_ENVS+=("$ota_env")
      PIO_ENV_PLATFORM_BY_NAME["$ota_env"]="${PIO_ENV_PLATFORM_BY_NAME[$env_name]}"
      PIO_ENV_BOARD_BY_NAME["$ota_env"]="${PIO_ENV_BOARD_BY_NAME[$env_name]}"
      PIO_ENV_MQTT_BY_NAME["$ota_env"]=0
      PIO_ENV_OTA_BY_NAME["$ota_env"]=1
      PIO_ENV_SD_OTA_BY_NAME["$ota_env"]="${PIO_ENV_SD_OTA_BY_NAME[$env_name]:-0}"
      PIO_ENV_FULL_BUILD_BY_NAME["$ota_env"]=0
      PIO_ENV_FULL_WIFI_OTA_BY_NAME["$ota_env"]=0
      PIO_ENV_BUILD_BASE_BY_NAME["$ota_env"]="$env_name"
    done

    # An ESP32 full companion is built from the board's WiFi companion recipe.
    # Expose it only when the exact same board also has USB and BLE companion
    # recipes, so enabling the three transports cannot silently pull in
    # assumptions from a different hardware variant.
    #
    # The full role is a LoRa mOTA *source*, not a LoRa update destination. Its
    # build overlay below retains the OTA protocol and TCP folder seeder while
    # omitting flash staging/self-install support.
    base_envs=("${SUPPORTED_PIO_ENVS[@]}")
    for env_name in "${base_envs[@]}"; do
      case "$env_name" in
        *companion_radio_wifi_mqtt*) continue ;;
        *companion_radio_wifi*) ;;
        *) continue ;;
      esac
      [ "${PIO_ENV_PLATFORM_BY_NAME[$env_name]:-}" = "ESP32_PLATFORM" ] || continue

      full_env=${env_name/companion_radio_wifi/companion_radio_full}
      usb_env=${env_name/companion_radio_wifi/companion_radio_usb}
      ble_env=${env_name/companion_radio_wifi/companion_radio_ble}
      if [ -n "${PIO_ENV_PLATFORM_BY_NAME[$full_env]+x}" ] \
          || [ "${PIO_ENV_PLATFORM_BY_NAME[$usb_env]:-}" != "ESP32_PLATFORM" ] \
          || [ "${PIO_ENV_PLATFORM_BY_NAME[$ble_env]:-}" != "ESP32_PLATFORM" ] \
          || [ "${PIO_ENV_BOARD_BY_NAME[$usb_env]:-}" != "${PIO_ENV_BOARD_BY_NAME[$env_name]:-}" ] \
          || [ "${PIO_ENV_BOARD_BY_NAME[$ble_env]:-}" != "${PIO_ENV_BOARD_BY_NAME[$env_name]:-}" ]; then
        continue
      fi

      SUPPORTED_PIO_ENVS+=("$full_env")
      PIO_ENV_PLATFORM_BY_NAME["$full_env"]="ESP32_PLATFORM"
      PIO_ENV_BOARD_BY_NAME["$full_env"]="${PIO_ENV_BOARD_BY_NAME[$env_name]}"
      PIO_ENV_MQTT_BY_NAME["$full_env"]=0
      PIO_ENV_OTA_BY_NAME["$full_env"]=1
      PIO_ENV_SD_OTA_BY_NAME["$full_env"]=0
      PIO_ENV_FULL_BUILD_BY_NAME["$full_env"]=0
      PIO_ENV_FULL_WIFI_OTA_BY_NAME["$full_env"]="${PIO_ENV_FULL_WIFI_OTA_BY_NAME[$env_name]:-0}"
      PIO_ENV_BUILD_BASE_BY_NAME["$full_env"]="$env_name"
    done

    # An nRF52 full companion is built from the board's USB recipe and adds
    # BLE plus a source-only serial mOTA mode. nRF52840 has no native WiFi, so
    # this profile deliberately has no TCP/WebConfig surface. Match exact USB
    # and BLE environment names (including display/FEM modifiers) and boards.
    base_envs=("${SUPPORTED_PIO_ENVS[@]}")
    for env_name in "${base_envs[@]}"; do
      case "$env_name" in
        *companion_radio_usb*) ;;
        *) continue ;;
      esac
      [ "${PIO_ENV_PLATFORM_BY_NAME[$env_name]:-}" = "NRF52_PLATFORM" ] || continue

      full_env=${env_name/companion_radio_usb/companion_radio_full}
      ble_env=${env_name/companion_radio_usb/companion_radio_ble}
      if [ -n "${PIO_ENV_PLATFORM_BY_NAME[$full_env]+x}" ] \
          || [ "${PIO_ENV_PLATFORM_BY_NAME[$ble_env]:-}" != "NRF52_PLATFORM" ] \
          || [ "${PIO_ENV_BOARD_BY_NAME[$ble_env]:-}" != "${PIO_ENV_BOARD_BY_NAME[$env_name]:-}" ]; then
        continue
      fi

      SUPPORTED_PIO_ENVS+=("$full_env")
      PIO_ENV_PLATFORM_BY_NAME["$full_env"]="NRF52_PLATFORM"
      PIO_ENV_BOARD_BY_NAME["$full_env"]="${PIO_ENV_BOARD_BY_NAME[$env_name]}"
      PIO_ENV_MQTT_BY_NAME["$full_env"]=0
      PIO_ENV_OTA_BY_NAME["$full_env"]=1
      PIO_ENV_SD_OTA_BY_NAME["$full_env"]=0
      PIO_ENV_FULL_BUILD_BY_NAME["$full_env"]=0
      PIO_ENV_FULL_WIFI_OTA_BY_NAME["$full_env"]=0
      PIO_ENV_BUILD_BASE_BY_NAME["$full_env"]="$env_name"
    done
  fi
}

get_pio_envs() {
  get_supported_pio_envs
}

canonicalize_variant_suffix() {
  local variant_suffix=$1

  case "${variant_suffix,,}" in
    comp_radio_usb|companion_usb|companion_radio_usb)
      echo "companion_radio_usb"
      ;;
    companion_ble|companion_radio_ble)
      echo "companion_radio_ble"
      ;;
    room_svr|room_server)
      echo "room_server"
      ;;
    repeatr|repeater)
      echo "repeater"
      ;;
    *)
      echo "${variant_suffix,,}"
      ;;
  esac
}

trim_trailing_underscores() {
  local value=$1

  while [[ "$value" == *_ ]]; do
    value=${value%_}
  done

  echo "$value"
}

sort_lines_case_insensitive() {
  sort -f
}

print_numbered_menu() {
  local items=("$@")
  local i

  for i in "${!items[@]}"; do
    printf '%d) %s\n' "$((i + 1))" "${items[$i]}"
  done
}

prompt_menu_choice() {
  local prompt_label=$1
  local max_choice=$2
  local allow_back=${3:-0}
  local choice

  while true; do
    if [ "$allow_back" -eq 1 ]; then
      read -r -p "${prompt_label} [1-${max_choice}, B=Back, Q=Quit]: " choice
    else
      read -r -p "${prompt_label} [1-${max_choice}, Q=Quit]: " choice
    fi

    case "${choice^^}" in
      Q)
        MENU_CHOICE="QUIT"
        return 0
        ;;
      B)
        if [ "$allow_back" -eq 1 ]; then
          MENU_CHOICE="BACK"
          return 0
        fi
        echo "Invalid selection."
        ;;
      *)
        if [[ "$choice" =~ ^[0-9]+$ ]] && [ "$choice" -ge 1 ] && [ "$choice" -le "$max_choice" ]; then
          MENU_CHOICE="$choice"
          return 0
        fi
        echo "Invalid selection."
        ;;
    esac
  done
}

prompt_on_off_choice() {
  local prompt_label=$1
  local default_choice=$2
  local choice

  while true; do
    read -r -p "${prompt_label} [on/off] (default: ${default_choice}): " choice
    choice=${choice,,}
    if [ -z "$choice" ]; then
      choice=$default_choice
    fi

    case "$choice" in
      on|off)
        MENU_CHOICE="$choice"
        return 0
        ;;
      *)
        echo "Invalid selection. Choose 'on' or 'off'."
        ;;
    esac
  done
}

prompt_for_build_mode() {
  local options=(
    "Build one firmware target"
    "Build all firmwares"
    "Build all firmwares in 5 profiles (standard, logging, MQTT, full ESP32 MQTT, full ESP32 logging without MQTT)"
    "Build all repeater firmwares"
    "Build canonical companion firmwares (FEM RX gain is runtime configurable)"
    "Build all chat room server firmwares"
    "Build all sensor firmwares"
    "Build only FULL ESP32 MQTT firmwares (all features, MQTT, and LoRa OTA)"
    "Build only FULL ESP32 logging firmwares (all features, logging, no MQTT, and LoRa OTA)"
    "Build canonical full Companion firmwares (runtime FEM control and host-backed LoRa OTA)"
  )

  echo "No command provided. Select a build action:"
  while true; do
    print_numbered_menu "${options[@]}"
    prompt_menu_choice "Build action" "${#options[@]}"
    if [ "$MENU_CHOICE" == "QUIT" ]; then
      echo "Cancelled."
      exit 1
    fi

    case "$MENU_CHOICE" in
      1)
        prompt_for_board_target
        prompt_for_single_target_build_profile
        SELECTED_COMMAND_ARGS=(build-firmware "$SELECTED_TARGET")
        return 0
        ;;
      2)
        SELECTED_COMMAND_ARGS=(build-firmwares)
        return 0
        ;;
      3)
        SELECTED_COMMAND_ARGS=(build-firmwares-logging-matrix)
        return 0
        ;;
      4)
        SELECTED_COMMAND_ARGS=(build-repeater-firmwares)
        return 0
        ;;
      5)
        SELECTED_COMMAND_ARGS=(build-companion-firmwares)
        return 0
        ;;
      6)
        SELECTED_COMMAND_ARGS=(build-room-server-firmwares)
        return 0
        ;;
      7)
        SELECTED_COMMAND_ARGS=(build-sensor-firmwares)
        return 0
        ;;
      8)
        SELECTED_COMMAND_ARGS=(build-full-esp32-firmwares)
        return 0
        ;;
      9)
        SELECTED_COMMAND_ARGS=(build-full-esp32-logging-firmwares)
        return 0
        ;;
      10)
        SELECTED_COMMAND_ARGS=(build-full-companion-firmwares)
        return 0
        ;;
    esac
  done
}

prompt_for_single_target_build_profile() {
  SINGLE_TARGET_FULL_BUILD=0
  if ! supports_esp32_full_build "$SELECTED_TARGET"; then
    echo "Selected target uses its standard build profile; FULL everything is available only for supported ESP32 targets."
    return 0
  fi

  local options=(
    "Standard/custom build"
    "FULL everything (all features, 254 neighbors, logging, MQTT off, LoRa OTA, expanded dual-OTA partitions)"
  )

  echo "Select the Option 1 build profile:"
  while true; do
    print_numbered_menu "${options[@]}"
    prompt_menu_choice "Build profile" "${#options[@]}"
    if [ "$MENU_CHOICE" == "QUIT" ]; then
      echo "Cancelled."
      exit 1
    fi

    case "$MENU_CHOICE" in
      1)
        echo "Using the standard/custom single-target build."
        return 0
        ;;
      2)
        SINGLE_TARGET_FULL_BUILD=1
        echo "Using FULL everything: all features, 254 neighbors, logging, MQTT off, LoRa OTA, and expanded dual-OTA partitions."
        return 0
        ;;
    esac
  done
}

prompt_for_debug_build_settings() {
  echo "Set debug build options:"
  prompt_on_off_choice "Mesh debug (MESH_DEBUG)" "off"
  MESHDEBUG_OVERRIDE="$MENU_CHOICE"

  prompt_on_off_choice "Packet logging (MESH_PACKET_LOGGING)" "off"
  PACKET_LOGGING_OVERRIDE="$MENU_CHOICE"

  echo "Using debug options: meshdebug=${MESHDEBUG_OVERRIDE}, packet_logging=${PACKET_LOGGING_OVERRIDE}"
}

prompt_for_mqtt_bridge_build_setting() {
  if [ -n "$SELECTED_TARGET" ] && [ "$(get_platform_for_env "$SELECTED_TARGET")" == "NRF52_PLATFORM" ]; then
    MQTT_BRIDGE_OVERRIDE="off"
    return 0
  fi

  if [ -n "$SELECTED_TARGET" ] && is_mqtt_bridge_target "$SELECTED_TARGET"; then
    MQTT_BRIDGE_OVERRIDE="on"
    echo "MQTT bridge enabled by selected target: ${SELECTED_TARGET}"
    return 0
  fi

  echo "MQTT bridge sends mesh radio traffic directly to MQTT over WiFi."
  prompt_on_off_choice "MQTT bridge (radio WiFi to MQTT direct)" "off"
  MQTT_BRIDGE_OVERRIDE="$MENU_CHOICE"
  echo "Using MQTT bridge: ${MQTT_BRIDGE_OVERRIDE}"
}

is_logging_matrix_command() {
  case "$1" in
    build-firmwares-logging-matrix|build-companion-firmwares-logging-matrix)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

is_full_esp32_command() {
  [ "$1" == "build-full-esp32-firmwares" ]
}

is_full_esp32_logging_command() {
  [ "$1" == "build-full-esp32-logging-firmwares" ]
}

is_automatic_profile_command() {
  is_logging_matrix_command "$1" \
    || is_full_esp32_command "$1" \
    || is_full_esp32_logging_command "$1"
}

clear_radio_overrides() {
  RADIO_SETTING_TITLE=""
  RADIO_FREQ_OVERRIDE=""
  RADIO_BW_OVERRIDE=""
  RADIO_SF_OVERRIDE=""
  RADIO_CR_OVERRIDE=""
}

clear_firmware_profile_overrides() {
  FIRMWARE_PROFILE_OVERRIDE=""
}

apply_cli_radio_preset() {
  local selection=$1
  local preset_output row
  local -a preset_rows=()

  if ! [[ "$selection" =~ ^[0-9]+$ ]] || [ "$selection" -lt 1 ]; then
    echo "Invalid --radio-preset value: ${selection}"
    return 1
  fi
  clear_radio_overrides
  if [ "$selection" -eq 1 ]; then
    echo "Using target default radio settings."
    return 0
  fi

  if ! preset_output=$(fetch_suggested_radio_settings) || [ -z "$preset_output" ]; then
    echo "Could not fetch radio preset ${selection} from ${RADIO_SETTINGS_API_URL}."
    return 1
  fi
  mapfile -t preset_rows <<< "$preset_output"
  if [ "$selection" -gt $((${#preset_rows[@]} + 1)) ]; then
    echo "Radio preset ${selection} is out of range; available menu choices are 1-$((${#preset_rows[@]} + 1))."
    return 1
  fi

  row=${preset_rows[$((selection - 2))]}
  IFS=$'\t' read -r title description freq bw sf cr <<< "$row"
  set_radio_overrides "$title" "$freq" "$bw" "$sf" "$cr"
  echo "Using radio setting ${selection}: ${RADIO_SETTING_TITLE} (${RADIO_FREQ_OVERRIDE}MHz / SF${RADIO_SF_OVERRIDE} / BW${RADIO_BW_OVERRIDE} / CR${RADIO_CR_OVERRIDE})"
}

parse_cli_options() {
  local -a positional=()

  while [ $# -gt 0 ]; do
    case "$1" in
      --firmware-version|--version)
        if [ $# -lt 2 ] || [ -z "$2" ]; then echo "$1 requires a value"; return 1; fi
        FIRMWARE_VERSION=$2
        FIRMWARE_VERSION_EXPLICIT=1
        export FIRMWARE_VERSION
        shift 2
        ;;
      --radio-preset)
        if [ $# -lt 2 ] || [ -z "$2" ]; then echo "$1 requires a value"; return 1; fi
        RADIO_PRESET_SELECTION=$2
        shift 2
        ;;
      --profile)
        if [ $# -lt 2 ]; then echo "$1 requires a value"; return 1; fi
        case "${2,,}" in
          default) FIRMWARE_PROFILE_OVERRIDE="" ;;
          cascade) FIRMWARE_PROFILE_OVERRIDE="cascade" ;;
          *) echo "Invalid profile: $2 (use default or cascade)"; return 1 ;;
        esac
        shift 2
        ;;
      --skip-kiss)
        KISS_MODE_OVERRIDE="skip"
        shift
        ;;
      --include-kiss)
        KISS_MODE_OVERRIDE="build"
        shift
        ;;
      --clean)
        RESUME_BUILD_OUTPUT=0
        OUTPUT_POLICY_EXPLICIT=1
        shift
        ;;
      --resume)
        RESUME_BUILD_OUTPUT=1
        OUTPUT_POLICY_EXPLICIT=1
        shift
        ;;
      --)
        shift
        positional+=("$@")
        break
        ;;
      help|usage|-h|--help|list|-l)
        positional+=("$1")
        shift
        ;;
      -*)
        echo "Unknown option: $1"
        return 1
        ;;
      *)
        positional+=("$1")
        shift
        ;;
    esac
  done
  PARSED_COMMAND_ARGS=("${positional[@]}")
}

set_radio_overrides() {
  RADIO_SETTING_TITLE=$1
  RADIO_FREQ_OVERRIDE=$2
  RADIO_BW_OVERRIDE=$3
  RADIO_SF_OVERRIDE=$4
  RADIO_CR_OVERRIDE=$5
}

set_firmware_profile_override() {
  FIRMWARE_PROFILE_OVERRIDE=$1
}

fetch_suggested_radio_settings() {
  python3 - "$RADIO_SETTINGS_API_URL" <<'PY'
import json
import sys
import urllib.request

url = sys.argv[1]
request = urllib.request.Request(
    url,
    headers={
        "Accept": "application/json",
        "User-Agent": "MeshCore-build.sh/1.0 (+https://github.com/meshcore-dev/MeshCore)",
    },
)

try:
    with urllib.request.urlopen(request, timeout=8) as response:
        payload = json.load(response)
except Exception as exc:
    print(f"radio preset fetch failed: {exc}", file=sys.stderr)
    raise SystemExit(1)

entries = (
    payload.get("config", {})
    .get("suggested_radio_settings", {})
    .get("entries", [])
)

for entry in entries:
    title = str(entry.get("title", "")).strip()
    description = str(entry.get("description", "")).strip()
    freq = str(entry.get("frequency", "")).strip()
    sf = str(entry.get("spreading_factor", "")).strip()
    bw = str(entry.get("bandwidth", "")).strip()
    cr = str(entry.get("coding_rate", "")).strip()
    if title and freq and sf and bw and cr:
        print("\t".join([title, description, freq, bw, sf, cr]))
PY
}

is_valid_custom_radio_bandwidth() {
  python3 - "$1" <<'PY'
import sys

allowed = [7.8, 10.4, 15.6, 20.8, 31.25, 41.7, 62.5, 125.0, 250.0, 500.0]
try:
    value = float(sys.argv[1])
except Exception:
    raise SystemExit(1)

raise SystemExit(0 if any(abs(value - option) < 1e-6 for option in allowed) else 1)
PY
}

prompt_for_custom_radio_setting() {
  local freq
  local sf
  local bw
  local cr

  echo
  echo "Custom radio settings:"

  while true; do
    read -r -p "Center frequency (MHz, e.g. 915.000): " freq
    if [[ "$freq" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
      break
    fi
    echo "Please enter a numeric MHz value (e.g. 915.000)."
  done

  echo "Spreading factor options: 5, 6, 7, 8, 9, 10, 11, 12"
  while true; do
    read -r -p "SF (5-12): " sf
    if [[ "$sf" =~ ^[0-9]+$ ]] && [ "$sf" -ge 5 ] && [ "$sf" -le 12 ]; then
      break
    fi
    echo "Please enter 5, 6, 7, 8, 9, 10, 11, or 12."
  done

  echo "Bandwidth options (kHz): 7.8 10.4 15.6 20.8 31.25 41.7 62.5 125 250 500"
  while true; do
    read -r -p "BW (kHz): " bw
    if [[ "$bw" =~ ^[0-9]+([.][0-9]+)?$ ]] && is_valid_custom_radio_bandwidth "$bw"; then
      break
    fi
    echo "Please enter one of: 7.8 10.4 15.6 20.8 31.25 41.7 62.5 125 250 500."
  done

  echo "Coding rate options: CR5, CR6, CR7, CR8"
  while true; do
    read -r -p "CR (5-8): " cr
    if [[ "$cr" =~ ^[0-9]+$ ]] && [ "$cr" -ge 5 ] && [ "$cr" -le 8 ]; then
      break
    fi
    echo "Please enter 5, 6, 7, or 8."
  done

  set_radio_overrides "Custom" "$freq" "$bw" "$sf" "$cr"
}

prompt_for_radio_build_settings() {
  local -a preset_rows=()
  local -a fetched_preset_rows=()
  local -a options=("Keep target defaults (no radio override)")
  local row
  local title
  local description
  local freq
  local bw
  local sf
  local cr
  local preset_index
  local choice_index
  local custom_index
  local preset_output

  clear_radio_overrides

  if preset_output=$(fetch_suggested_radio_settings); then
    if [ -n "$preset_output" ]; then
      mapfile -t fetched_preset_rows <<< "$preset_output"
    fi
    for row in "${fetched_preset_rows[@]}"; do
      if [ -z "$row" ]; then
        continue
      fi
      preset_rows+=("$row")
    done
  else
    echo "Could not fetch radio presets from ${RADIO_SETTINGS_API_URL}."
  fi

  for row in "${preset_rows[@]}"; do
    if [ -z "$row" ]; then
      continue
    fi
    IFS=$'\t' read -r title description freq bw sf cr <<< "$row"
    options+=("${title}: ${description}")
  done

  options+=("Custom")
  custom_index=${#options[@]}

  echo "Set radio build options:"
  while true; do
    print_numbered_menu "${options[@]}"
    prompt_menu_choice "Radio setting" "${#options[@]}"
    if [ "$MENU_CHOICE" == "QUIT" ]; then
      echo "Cancelled."
      exit 1
    fi

    choice_index=$MENU_CHOICE
    if [ "$choice_index" -eq 1 ]; then
      echo "Using target default radio settings."
      return 0
    fi

    if [ "$choice_index" -eq "$custom_index" ]; then
      prompt_for_custom_radio_setting
      echo "Using radio setting: ${RADIO_SETTING_TITLE} (${RADIO_FREQ_OVERRIDE}MHz / SF${RADIO_SF_OVERRIDE} / BW${RADIO_BW_OVERRIDE} / CR${RADIO_CR_OVERRIDE})"
      return 0
    fi

    preset_index=$((choice_index - 2))
    if [ "$preset_index" -ge 0 ] && [ "$preset_index" -lt "${#preset_rows[@]}" ]; then
      IFS=$'\t' read -r title description freq bw sf cr <<< "${preset_rows[$preset_index]}"
      set_radio_overrides "$title" "$freq" "$bw" "$sf" "$cr"
      echo "Using radio setting: ${RADIO_SETTING_TITLE} (${RADIO_FREQ_OVERRIDE}MHz / SF${RADIO_SF_OVERRIDE} / BW${RADIO_BW_OVERRIDE} / CR${RADIO_CR_OVERRIDE})"
      return 0
    fi
  done
}

prompt_for_firmware_profile_settings() {
  local -a options=(
    "Keep target defaults"
    "Cascade: path.hash.mode=2 / loop.detect=minimal / cad=on / rxdelay=2 / agc.reset.interval=8 / advert.interval=0 / flood.advert.interval=83 / multi.acks=1 / companion.manual.add=1 / companion.autoadd=0"
  )

  clear_firmware_profile_overrides

  echo "Set firmware profile options:"
  while true; do
    print_numbered_menu "${options[@]}"
    prompt_menu_choice "Firmware profile" "${#options[@]}"
    if [ "$MENU_CHOICE" == "QUIT" ]; then
      echo "Cancelled."
      exit 1
    fi

    case "$MENU_CHOICE" in
      1)
        echo "Using target default firmware profile settings."
        return 0
        ;;
      2)
        set_firmware_profile_override "cascade"
        echo "Using firmware profile: Cascade"
        return 0
        ;;
    esac
  done
}

get_env_metadata() {
  local env_name=$1
  local trimmed_env_name
  local board_part
  local variant_part
  local board_family
  local board_modifier
  local variant_label
  local tag_prefix

  trimmed_env_name=$(trim_trailing_underscores "$env_name")
  board_part=$trimmed_env_name
  variant_part=""

  shopt -s nocasematch
  # Split a raw env name into board and variant pieces using the normalized
  # suffix vocabulary defined near the top of the file.
  if [[ "$trimmed_env_name" =~ ^(.+)[_-](${ENV_VARIANT_SUFFIX_PATTERN})$ ]]; then
    board_part=${BASH_REMATCH[1]}
    variant_part=$(canonicalize_variant_suffix "${BASH_REMATCH[2]}")
  fi

  # Fold display and form-factor suffixes into the variant label so related
  # boards share one first-level menu entry.
  case "$board_part" in
    ikoka_handheld_nrf_e22_30dbm_096_rotated)
      board_family="$IKOKA_HANDHELD_NRF_BOARD_FAMILY"
      board_modifier="096_rotated"
      ;;
    ikoka_handheld_nrf_e22_30dbm_096)
      board_family="$IKOKA_HANDHELD_NRF_BOARD_FAMILY"
      board_modifier="096"
      ;;
    ikoka_handheld_nrf)
      board_family="$IKOKA_HANDHELD_NRF_BOARD_FAMILY"
      board_modifier=""
      ;;
    *"$BOARD_MODIFIER_WITHOUT_DISPLAY")
      board_family=${board_part%"$BOARD_MODIFIER_WITHOUT_DISPLAY"}
      board_modifier="$BOARD_LABEL_WITHOUT_DISPLAY"
      ;;
    *"$BOARD_MODIFIER_LOGGING")
      board_family=${board_part%"$BOARD_MODIFIER_LOGGING"}
      board_modifier="$BOARD_LABEL_LOGGING"
      ;;
    *"$BOARD_MODIFIER_TFT")
      board_family=${board_part%"$BOARD_MODIFIER_TFT"}
      board_modifier="$BOARD_LABEL_TFT"
      ;;
    *"$BOARD_MODIFIER_EINK")
      board_family=${board_part%"$BOARD_MODIFIER_EINK"}
      board_modifier="$BOARD_LABEL_EINK"
      ;;
    *"$BOARD_MODIFIER_EINK_SUFFIX")
      board_family=${board_part%"$BOARD_MODIFIER_EINK_SUFFIX"}
      board_modifier="$BOARD_LABEL_EINK"
      ;;
    *)
      board_family=$board_part
      board_modifier=""
      ;;
  esac
  shopt -u nocasematch

  variant_label="$variant_part"
  if [ -n "$board_modifier" ]; then
    if [ -n "$variant_label" ]; then
      variant_label="${board_modifier}_${variant_label}"
    else
      variant_label="$board_modifier"
    fi
  fi

  if [ -z "$variant_label" ]; then
    variant_label="$DEFAULT_VARIANT_LABEL"
  fi

  case "$variant_part" in
    room_server*)
      tag_prefix="$TAG_PREFIX_ROOM_SERVER"
      ;;
    companion_radio_*)
      tag_prefix="$TAG_PREFIX_COMPANION"
      ;;
    repeater*)
      tag_prefix="$TAG_PREFIX_REPEATER"
      ;;
    sensor)
      tag_prefix="$TAG_PREFIX_SENSOR"
      ;;
    *)
      tag_prefix=""
      ;;
  esac

  printf '%s\t%s\t%s\n' "$board_family" "$variant_label" "$tag_prefix"
}

get_metadata_field() {
  local env_name=$1
  local field_index=$2
  local metadata

  metadata=$(get_env_metadata "$env_name")
  case "$field_index" in
    1)
      echo "${metadata%%$'\t'*}"
      ;;
    2)
      metadata=${metadata#*$'\t'}
      echo "${metadata%%$'\t'*}"
      ;;
    3)
      echo "${metadata##*$'\t'}"
      ;;
  esac
}

get_board_family_for_env() {
  get_metadata_field "$1" 1
}

get_variant_name_for_env() {
  get_metadata_field "$1" 2
}

get_release_tag_prefix_for_env() {
  get_metadata_field "$1" 3
}

get_variants_for_board() {
  local board_family=$1
  local env

  for env in "${SUPPORTED_PIO_ENVS[@]}"; do
    if ! is_supported_build_env "$env"; then
      continue
    fi

    if [ "$(get_board_family_for_env "$env")" == "$board_family" ]; then
      echo "$env"
    fi
  done | sort_lines_case_insensitive
}

prompt_for_variant_for_board() {
  local board=$1
  local -A seen_variant_labels=()
  local variants
  local variant_labels
  local i
  local j

  mapfile -t variants < <(get_variants_for_board "$board")
  if [ ${#variants[@]} -eq 0 ]; then
    echo "No firmware variants were found for ${board}."
    return 1
  fi

  if [ ${#variants[@]} -eq 1 ]; then
    SELECTED_TARGET="${variants[0]}"
    return 0
  fi

  variant_labels=()
  for i in "${!variants[@]}"; do
    variant_labels[i]=$(get_variant_name_for_env "${variants[$i]}")
    seen_variant_labels["${variant_labels[$i]}"]=$(( ${seen_variant_labels["${variant_labels[$i]}"]:-0} + 1 ))
  done

  # Stop early if normalization would present the user with ambiguous labels.
  for i in "${!variant_labels[@]}"; do
    if [ "${seen_variant_labels["${variant_labels[$i]}"]}" -gt 1 ]; then
      echo "Ambiguous firmware variants detected for ${board}: ${variant_labels[$i]}"
      echo "The normalized menu labels are not unique for this board family."
      for j in "${!variants[@]}"; do
        echo "  ${variants[$j]}"
      done
      exit 1
    fi
  done

  echo "Select a firmware variant for ${board}:"
  while true; do
    print_numbered_menu "${variant_labels[@]}"
    prompt_menu_choice "Variant selection" "${#variant_labels[@]}" 1
    if [ "$MENU_CHOICE" == "BACK" ]; then
      return 1
    fi
    if [ "$MENU_CHOICE" == "QUIT" ]; then
      echo "Cancelled."
      exit 1
    fi

    SELECTED_TARGET="${variants[$((MENU_CHOICE - 1))]}"
    return 0
  done
}

prompt_for_board_target() {
  local -A seen_boards=()
  local boards=()
  local board
  local env

  if ! [ -t 0 ]; then
    echo "No command provided and no interactive terminal is available."
    global_usage
    exit 1
  fi

  if [ ${#ALL_PIO_ENVS[@]} -eq 0 ]; then
    echo "No PlatformIO environments were found."
    exit 1
  fi

  for env in "${SUPPORTED_PIO_ENVS[@]}"; do
    if ! is_supported_build_env "$env"; then
      continue
    fi

    board=$(get_board_family_for_env "$env")
    if [ -z "${seen_boards[$board]}" ]; then
      seen_boards["$board"]=1
      boards+=("$board")
    fi
  done

  mapfile -t boards < <(printf '%s\n' "${boards[@]}" | sort_lines_case_insensitive)

  echo "Select a board family:"
  while true; do
    print_numbered_menu "${boards[@]}"
    prompt_menu_choice "Board selection" "${#boards[@]}"
    if [ "$MENU_CHOICE" == "QUIT" ]; then
      echo "Cancelled."
      exit 1
    fi

    board=${boards[$((MENU_CHOICE - 1))]}
    if prompt_for_variant_for_board "$board"; then
      echo "Building firmware for ${SELECTED_TARGET}"
      return 0
    fi
  done
}

get_latest_version_from_tags() {
  local env_name=$1
  local tag_prefix
  local latest_tag
  local fallback_version

  fallback_version="${FALLBACK_VERSION_PREFIX}-$(date "${FALLBACK_VERSION_DATE_FORMAT}")"
  tag_prefix=$(get_release_tag_prefix_for_env "$env_name")
  if [ -z "$tag_prefix" ]; then
    echo "$fallback_version"
    return 0
  fi

  latest_tag=$(git tag --list "${tag_prefix}-v*" --sort=-version:refname | head -n 1)
  if [ -z "$latest_tag" ]; then
    echo "$fallback_version"
    return 0
  fi

  echo "${latest_tag#"${tag_prefix}"-}"
}

refresh_firmware_version_tags() {
  local tag_remote

  if [ "$FIRMWARE_VERSION_EXPLICIT" -eq 1 ] \
      || [ -n "${FIRMWARE_VERSION:-}" ]; then
    return 0
  fi

  if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "WARNING: not in a git worktree; using existing local version tags." >&2
    return 0
  fi
  if git remote get-url upstream >/dev/null 2>&1; then
    tag_remote="upstream"
  elif git remote get-url origin >/dev/null 2>&1; then
    tag_remote="origin"
  else
    echo "WARNING: no upstream or origin git remote is available; using existing local version tags." >&2
    return 0
  fi

  echo "Refreshing firmware version tags from ${tag_remote}..."
  if ! GIT_TERMINAL_PROMPT=0 git fetch --quiet --tags "$tag_remote"; then
    echo "WARNING: unable to refresh tags from ${tag_remote}; using existing local version tags." >&2
  fi
}

derive_default_firmware_version() {
  local env_name=$1
  local base_version

  base_version=$(get_latest_version_from_tags "$env_name")
  case "$base_version" in
    *-dev|dev-*)
      echo "$base_version"
      ;;
    *)
      echo "${base_version}-dev"
      ;;
  esac
}

derive_default_firmware_version_for_targets() {
  local target
  local tag_prefix
  local candidate_version
  local fallback_version
  local -a candidate_versions=()
  local -a sorted_versions=()
  local -A seen_tag_prefixes=()

  fallback_version="${FALLBACK_VERSION_PREFIX}-$(date "${FALLBACK_VERSION_DATE_FORMAT}")"

  for target in "$@"; do
    tag_prefix=$(get_release_tag_prefix_for_env "$target")
    if [ -n "$tag_prefix" ]; then
      if [ -n "${seen_tag_prefixes[$tag_prefix]+x}" ]; then
        continue
      fi
      seen_tag_prefixes["$tag_prefix"]=1
    fi

    candidate_version=$(derive_default_firmware_version "$target")
    candidate_versions+=("$candidate_version")
  done

  if [ ${#candidate_versions[@]} -eq 0 ]; then
    echo "$fallback_version"
    return 0
  fi

  mapfile -t sorted_versions < <(printf '%s\n' "${candidate_versions[@]}" | sort -u -V)
  echo "${sorted_versions[$((${#sorted_versions[@]} - 1))]}"
}

get_output_firmware_version_suffix() {
  local output_dir=${1:-$OUTPUT_DIR}
  local filename
  local candidate_suffix
  local selected_suffix=""
  local found_suffix=0
  local artifact_pattern='-v[0-9]+\.[0-9]+\.[0-9]+(-[[:alnum:]_.-]+)?-[[:xdigit:]]{7,40}(-merged)?\.(bin|hex|uf2|zip)$'

  if ! [ -d "$output_dir" ]; then
    return 1
  fi

  while IFS= read -r filename; do
    if ! [[ "$filename" =~ $artifact_pattern ]]; then
      continue
    fi

    candidate_suffix=${BASH_REMATCH[1]}
    if [ "$found_suffix" -eq 0 ]; then
      selected_suffix=$candidate_suffix
      found_suffix=1
    elif [ "$candidate_suffix" != "$selected_suffix" ]; then
      # Mixed firmware suffixes make the intended default ambiguous.
      return 1
    fi
  done < <(find "$output_dir" -maxdepth 1 -type f -printf '%f\n')

  # Empty and ordinary -dev suffixes add no information beyond the tag-derived
  # suggestion. Only carry a custom label forward.
  if [ "$found_suffix" -eq 0 ] \
      || [ -z "$selected_suffix" ] \
      || [ "$selected_suffix" == "-dev" ]; then
    return 1
  fi

  printf '%s\n' "$selected_suffix"
}

apply_output_firmware_version_suffix() {
  local suggested_version=$1
  local output_suffix

  if ! output_suffix=$(get_output_firmware_version_suffix "$OUTPUT_DIR"); then
    printf '%s\n' "$suggested_version"
    return 0
  fi

  # Preserve the newly tag-derived numeric version while replacing its generic
  # prerelease label with the custom label from the previous output artifacts.
  if [[ "$suggested_version" =~ ^(v[0-9]+\.[0-9]+\.[0-9]+)(-[[:alnum:]_.-]+)?$ ]]; then
    printf '%s%s\n' "${BASH_REMATCH[1]}" "$output_suffix"
  else
    printf '%s\n' "$suggested_version"
  fi
}

prompt_for_firmware_version() {
  local prompt_label=$1
  local result_var=$2
  local suggested_version=${3:-}
  local entered_version

  if [ -z "$suggested_version" ]; then
    suggested_version=$(derive_default_firmware_version "$prompt_label")
  fi

  if ! [ -t 0 ]; then
    printf -v "$result_var" '%s' "$suggested_version"
    return 0
  fi

  echo "Suggested firmware version for ${prompt_label}: ${suggested_version}"
  read -r -e -i "${suggested_version}" -p "Firmware version: " entered_version
  printf -v "$result_var" '%s' "${entered_version:-$suggested_version}"
}

prompt_for_resolved_firmware_version() {
  local prompt_label
  local selected_version=${FIRMWARE_VERSION:-}

  if [ ${#RESOLVED_BUILD_TARGETS[@]} -eq 0 ]; then
    return 0
  fi

  if [ "$FIRMWARE_VERSION_EXPLICIT" -eq 1 ]; then
    echo "Using firmware version from --firmware-version: ${FIRMWARE_VERSION}"
    return 0
  fi

  if ! [ -t 0 ]; then
    return 0
  fi

  if [ -z "$selected_version" ]; then
    selected_version=$(derive_default_firmware_version_for_targets "${RESOLVED_BUILD_TARGETS[@]}")
    selected_version=$(apply_output_firmware_version_suffix "$selected_version")
  fi

  if [ ${#RESOLVED_BUILD_TARGETS[@]} -eq 1 ]; then
    prompt_label="${RESOLVED_BUILD_TARGETS[0]}"
  else
    prompt_label="${#RESOLVED_BUILD_TARGETS[@]} build targets"
  fi

  prompt_for_firmware_version "$prompt_label" selected_version "$selected_version"
  FIRMWARE_VERSION=$selected_version
  export FIRMWARE_VERSION
}

get_pio_envs_containing_string() {
  local env

  shopt -s nocasematch
  # Search the complete supported target set, including generated aliases such
  # as each board's constrained LoRa-OTA repeater build. ALL_PIO_ENVS only
  # contains concrete PlatformIO environments and would silently omit aliases.
  for env in "${SUPPORTED_PIO_ENVS[@]}"; do
    if [[ "$env" == *${1}* ]]; then
      echo "$env"
    fi
  done
  shopt -u nocasematch
}

get_supported_pio_envs() {
  if [ ${#SUPPORTED_PIO_ENVS[@]} -gt 0 ]; then
    printf '%s\n' "${SUPPORTED_PIO_ENVS[@]}"
  fi
}

get_pio_envs_ending_with_string() {
  local suffix=$1
  local env

  shopt -s nocasematch
  for env in "${SUPPORTED_PIO_ENVS[@]}"; do
    if is_supported_build_env "$env" && [[ "$env" == *${suffix} ]]; then
      printf '%s\n' "$env"
    fi
  done
  shopt -u nocasematch
}

print_release_firmware_targets() {
  case "$1" in
    get-companion-firmwares-to-build)
      get_pio_envs_ending_with_string "_companion_radio_usb"
      get_pio_envs_ending_with_string "_companion_radio_ble"
      ;;
    get-repeater-firmwares-to-build)
      get_pio_envs_ending_with_string "_repeater"
      ;;
    get-room-server-firmwares-to-build)
      get_pio_envs_ending_with_string "_room_server"
      ;;
    *)
      return 1
      ;;
  esac
}

get_pio_envs_for_variant_role() {
  local role=$1
  local env
  local variant_name

  for env in "${SUPPORTED_PIO_ENVS[@]}"; do
    if ! is_supported_build_env "$env"; then
      continue
    fi

    variant_name=$(get_variant_name_for_env "$env")
    case "$role:$variant_name" in
      companion:companion_radio_*|companion:*_companion_radio_*)
        echo "$env"
        ;;
      repeater:repeater*|repeater:*_repeater*)
        echo "$env"
        ;;
      room_server:room_server*|room_server:*_room_server*)
        echo "$env"
        ;;
      sensor:sensor|sensor:*_sensor)
        echo "$env"
        ;;
      kiss:kiss_modem|kiss:*_kiss_modem)
        echo "$env"
        ;;
    esac
  done
}

is_kiss_modem_target() {
  case "$(get_variant_name_for_env "$1")" in
    kiss_modem|*_kiss_modem)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

is_bluetooth_target() {
  case "$(get_variant_name_for_env "$1")" in
    companion_radio_ble|*_companion_radio_ble)
      return 0
      ;;
  esac

  case "${1,,}" in
    *companion_radio_ble*|*companion_ble*)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

filter_out_kiss_modem_targets() {
  local target
  local -a filtered_targets=()

  for target in "${RESOLVED_BUILD_TARGETS[@]}"; do
    if ! is_kiss_modem_target "$target"; then
      filtered_targets+=("$target")
    fi
  done

  RESOLVED_BUILD_TARGETS=("${filtered_targets[@]}")
}

filter_out_bluetooth_targets() {
  local target

  for target in "$@"; do
    if ! is_bluetooth_target "$target"; then
      printf '%s\n' "$target"
    fi
  done
}

is_lora_ota_only_target() {
  local target_lc=${1,,}
  [[ "$target_lc" == *lora_ota* ]]
}

filter_out_lora_ota_only_targets() {
  local target

  for target in "$@"; do
    if ! is_lora_ota_only_target "$target"; then
      printf '%s\n' "$target"
    fi
  done
}

is_logging_size_constrained_target() {
  case "$1" in
    Tiny_Relay_repeater|RAK_3x72_repeater|wio-e5_repeater|wio-e5-repeater_bridge_rs232|wio-e5-mini_companion_radio_usb|wio-e5-mini_repeater|wio-e5-mini_sensor)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

filter_out_logging_size_constrained_targets() {
  local target

  for target in "$@"; do
    if ! is_logging_size_constrained_target "$target"; then
      printf '%s\n' "$target"
    fi
  done
}

prompt_for_kiss_modem_build_policy() {
  local kiss_count=0
  local target
  local choice

  for target in "${RESOLVED_BUILD_TARGETS[@]}"; do
    if is_kiss_modem_target "$target"; then
      kiss_count=$((kiss_count + 1))
    fi
  done

  if [ "$kiss_count" -eq 0 ]; then
    return 0
  fi

  case "$KISS_MODE_OVERRIDE" in
    skip)
      filter_out_kiss_modem_targets
      echo "Skipped ${kiss_count} KISS modem target(s)."
      return 0
      ;;
    build)
      echo "Including ${kiss_count} KISS modem target(s)."
      return 0
      ;;
  esac

  if ! [ -t 0 ]; then
    echo "Including ${kiss_count} KISS modem target(s)."
    return 0
  fi

  while true; do
    read -r -p "KISS modem targets found: ${kiss_count}. Build or skip them? [build/skip] (default: build): " choice
    choice=${choice,,}
    if [ -z "$choice" ]; then
      choice="build"
    fi

    case "$choice" in
      build)
        echo "Including ${kiss_count} KISS modem target(s)."
        return 0
        ;;
      skip)
        filter_out_kiss_modem_targets
        echo "Skipped ${kiss_count} KISS modem target(s)."
        return 0
        ;;
      *)
        echo "Invalid selection. Choose 'build' or 'skip'."
        ;;
    esac
  done
}

normalize_resume_build_output() {
  case "${RESUME_BUILD_OUTPUT,,}" in
    1|true|yes|y|on|resume)
      RESUME_BUILD_OUTPUT=1
      ;;
    *)
      RESUME_BUILD_OUTPUT=0
      ;;
  esac
}

prompt_for_logging_matrix_output_policy() {
  local default_policy=${1:-clean}
  local choice file_count file_label

  case "$default_policy" in
    resume|clean) ;;
    *) default_policy="clean" ;;
  esac

  normalize_resume_build_output

  if [ "$OUTPUT_POLICY_EXPLICIT" -eq 1 ]; then
    if [ "$RESUME_BUILD_OUTPUT" == "1" ]; then
      echo "Using --resume for existing profile-build output."
    else
      echo "Using --clean for profile-build output."
    fi
    return 0
  fi

  if ! [ -d "$OUTPUT_DIR" ]; then
    RESUME_BUILD_OUTPUT=0
    return 0
  fi

  file_count=$(find "$OUTPUT_DIR" -type f -printf '.' | wc -c)
  file_count=$((file_count))
  if [ "$file_count" -eq 1 ]; then
    file_label="file"
  else
    file_label="files"
  fi

  if ! [ -t 0 ]; then
    if [ "$default_policy" = "resume" ]; then
      RESUME_BUILD_OUTPUT=1
    fi
    if [ "$RESUME_BUILD_OUTPUT" == "1" ]; then
      echo "Resuming previous profile-build output in ${OUTPUT_DIR} (${file_count} ${file_label})."
    fi
    return 0
  fi

  while true; do
    read -r -p "Output directory '${OUTPUT_DIR}' exists with ${file_count} ${file_label}. Resume previous profile-build progress or clean it? [resume/clean] (default: ${default_policy}): " choice
    choice=${choice,,}
    if [ -z "$choice" ]; then
      choice=$default_policy
    fi

    case "$choice" in
      resume)
        RESUME_BUILD_OUTPUT=1
        return 0
        ;;
      clean)
        RESUME_BUILD_OUTPUT=0
        return 0
        ;;
      *)
        echo "Invalid selection. Choose 'resume' or 'clean'."
        ;;
    esac
  done
}

get_platform_for_env() {
  local env_name=$1

  if [ -n "${PIO_ENV_PLATFORM_BY_NAME[$env_name]+x}" ]; then
    echo "${PIO_ENV_PLATFORM_BY_NAME[$env_name]}"
    return 0
  fi

  # PlatformIO exposes project config as JSON; scan the selected env's
  # build_flags to recover the platform token used for artifact collection.
  # Feed the cached JSON via stdin to avoid shell echo quirks and argv/env size limits.
  python3 -c "
import sys, json, re
data = json.load(sys.stdin)
for section, options in data:
    if section == 'env:$env_name':
        for key, value in options:
            if key == 'build_flags':
                for flag in value:
                    match = re.search(r'($SUPPORTED_PLATFORM_PATTERN)', flag)
                    if match:
                        print(match.group(1))
                        sys.exit(0)
" <<<"$PIO_CONFIG_JSON"
}

is_supported_platform() {
  local env_platform=$1

  [[ "$env_platform" =~ ^(${SUPPORTED_PLATFORM_PATTERN})$ ]]
}

is_known_pio_env() {
  local env_name=$1
  local env

  # Synthetic LoRa-OTA environments build their recorded base PlatformIO
  # environment with a constrained flag profile, so they intentionally do not
  # appear in `pio project config`.
  if [ -n "${PIO_ENV_BUILD_BASE_BY_NAME[$env_name]+x}" ]; then
    return 0
  fi

  for env in "${ALL_PIO_ENVS[@]}"; do
    if [ "$env" == "$env_name" ]; then
      return 0
    fi
  done

  return 1
}

is_supported_build_env() {
  local env_name=$1

  [ -n "${PIO_ENV_PLATFORM_BY_NAME[$env_name]+x}" ]
}

get_pio_build_env() {
  local env_name=$1
  echo "${PIO_ENV_BUILD_BASE_BY_NAME[$env_name]:-$env_name}"
}

is_mqtt_bridge_target() {
  [ "${PIO_ENV_MQTT_BY_NAME[$1]:-0}" == "1" ]
}

get_mqtt_enabled_target() {
  local target=$1

  if is_mqtt_bridge_target "$target"; then
    echo "$target"
  elif is_mqtt_bridge_target "${target}_mqtt"; then
    echo "${target}_mqtt"
  elif is_mqtt_bridge_target "${target}_observer_mqtt"; then
    echo "${target}_observer_mqtt"
  else
    return 1
  fi
}

get_mqtt_disabled_target() {
  local target=$1
  local candidate=$target

  if is_mqtt_bridge_target "$target"; then
    if [[ "$target" == *_observer_mqtt ]]; then
      candidate=${target%_observer_mqtt}
    elif [[ "$target" == *_companion_radio_wifi_mqtt ]]; then
      candidate=${target%_mqtt}
    else
      return 1
    fi
  fi

  if ! is_supported_build_env "$candidate" || is_mqtt_bridge_target "$candidate"; then
    return 1
  fi
  echo "$candidate"
}

normalize_resolved_targets_for_mqtt() {
  local command=$1
  local target
  local candidate
  local skipped_count=0
  local -a candidates=("${RESOLVED_BUILD_TARGETS[@]}")
  local -a normalized_targets=()
  local -A seen_targets=()

  if [ "${MQTT_BRIDGE_OVERRIDE,,}" == "on" ]; then
    case "$command" in
      build-repeater-firmwares)
        for target in "${SUPPORTED_PIO_ENVS[@]}"; do
          if is_mqtt_bridge_target "$target" && [[ "$target" == *_repeater_observer_mqtt ]]; then
            candidates+=("$target")
          fi
        done
        ;;
      build-room-server-firmwares)
        for target in "${SUPPORTED_PIO_ENVS[@]}"; do
          if is_mqtt_bridge_target "$target" && [[ "$target" == *_room_server_observer_mqtt ]]; then
            candidates+=("$target")
          fi
        done
        ;;
    esac
  fi

  for target in "${candidates[@]}"; do
    candidate=""
    if [ "${MQTT_BRIDGE_OVERRIDE,,}" == "on" ]; then
      candidate=$(get_mqtt_enabled_target "$target") || candidate=""
    else
      candidate=$(get_mqtt_disabled_target "$target") || candidate=""
    fi

    if [ -z "$candidate" ]; then
      skipped_count=$((skipped_count + 1))
      continue
    fi
    if [ -z "${seen_targets[$candidate]+x}" ]; then
      normalized_targets+=("$candidate")
      seen_targets["$candidate"]=1
    fi
  done

  RESOLVED_BUILD_TARGETS=("${normalized_targets[@]}")
  if [ ${#RESOLVED_BUILD_TARGETS[@]} -eq 0 ]; then
    echo "No targets support the selected MQTT bridge setting. MQTT bridge is for direct radio-to-MQTT forwarding over WiFi."
    return 1
  fi

  if [ "${MQTT_BRIDGE_OVERRIDE,,}" == "on" ]; then
    echo "MQTT bridge enabled for ${#RESOLVED_BUILD_TARGETS[@]} target(s); ${skipped_count} target(s) without a WiFi MQTT environment were skipped."
  else
    echo "MQTT bridge disabled; using ${#RESOLVED_BUILD_TARGETS[@]} standard target(s)."
  fi
}

disable_debug_flags() {
  if [ "$DISABLE_DEBUG" == "1" ]; then
    export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -UMESH_DEBUG -UMESH_PACKET_LOGGING -UBLE_DEBUG_LOGGING -UWIFI_DEBUG_LOGGING -UBRIDGE_DEBUG -UGPS_NMEA_DEBUG -UCORE_DEBUG_LEVEL -UESPNOW_DEBUG_LOGGING -UDEBUG_RP2040_WIRE -UDEBUG_RP2040_SPI -UDEBUG_RP2040_CORE -UDEBUG_RP2040_PORT -URADIOLIB_DEBUG_SPI -DCFG_DEBUG=0 -URADIOLIB_DEBUG_BASIC -URADIOLIB_DEBUG_PROTOCOL"
  fi
}

apply_mqtt_bridge_override() {
  case "${MQTT_BRIDGE_OVERRIDE,,}" in
    on)
      export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DWITH_MQTT_BRIDGE=1"
      ;;
    off)
      export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -UWITH_MQTT_BRIDGE"
      ;;
  esac
}

apply_debug_overrides() {
  case "${MESHDEBUG_OVERRIDE,,}" in
    on)
      export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DMESH_DEBUG=1"
      ;;
    off)
      export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -UMESH_DEBUG"
      ;;
  esac

  case "${PACKET_LOGGING_OVERRIDE,,}" in
    on)
      export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DMESH_PACKET_LOGGING=1"
      ;;
    off)
      export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -UMESH_PACKET_LOGGING"
      ;;
  esac

  case "${MQTT_DEBUG_OVERRIDE,,}" in
    off)
      export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -UMQTT_DEBUG -UMQTT_MEMORY_DEBUG"
      ;;
  esac
}

disable_usb_logging_for_mqtt() {
  local env_name=$1

  # FULL logging is an explicit diagnostic profile. Keep its requested USB
  # debug and packet logging even when the target also publishes over MQTT.
  if [ "$ESP32_FULL_BUILD" = "1" ] \
      && [ "$FIRMWARE_FILENAME_INFIX" = "full-logging" ]; then
    return 0
  fi

  if is_mqtt_bridge_target "$env_name" || [ "${MQTT_BRIDGE_OVERRIDE,,}" == "on" ]; then
    # MQTT observers already export packet traffic through the bridge. Keep the
    # serial console available for the CLI without compiling a second logging path.
    export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -UMESH_DEBUG -UMESH_PACKET_LOGGING -UMQTT_DEBUG -UMQTT_MEMORY_DEBUG"
  fi
}

is_esp32_usb_wifi_companion_ota_build() {
  local env_name=$1
  local env_name_lc=${env_name,,}

  [ "${PIO_ENV_PLATFORM_BY_NAME[$env_name]:-}" = "ESP32_PLATFORM" ] || return 1
  case "$env_name_lc" in
    *companion_radio_usb*|*companion_radio_wifi*|*comp_radio_usb*|*companion_usb*) return 0 ;;
    *) return 1 ;;
  esac
}

is_companion_radio_full_target() {
  case "${PIO_ENV_PLATFORM_BY_NAME[$1]:-}" in
    ESP32_PLATFORM|NRF52_PLATFORM) ;;
    *) return 1 ;;
  esac
  case "${1,,}" in
    *companion_radio_full*) return 0 ;;
    *) return 1 ;;
  esac
}

is_esp32_companion_radio_full_target() {
  [ "${PIO_ENV_PLATFORM_BY_NAME[$1]:-}" = "ESP32_PLATFORM" ] \
    && is_companion_radio_full_target "$1"
}

is_nrf52_companion_radio_full_target() {
  [ "${PIO_ENV_PLATFORM_BY_NAME[$1]:-}" = "NRF52_PLATFORM" ] \
    && is_companion_radio_full_target "$1"
}

requires_esp32_companion_full_ota_fallback() {
  # Some ESP32 companions cannot hold their configured high-capacity contact,
  # channel, and offline-queue tables together with BLE, WiFi, and LoRa OTA in
  # internal DRAM. Keep their ordinary high-capacity image unchanged and emit
  # a separately named FULL OTA image with the companion default capacities.
  case "${1,,}" in
    heltec_v2_companion_radio_wifi|lilygo_tbeam_1w_companion_radio_wifi|lilygo_tlora_v2_1_1_6_companion_radio_wifi|meshadventurer_sx1262_companion_radio_usb|meshadventurer_sx1268_companion_radio_usb) return 0 ;;
    *) return 1 ;;
  esac
}

requires_dram_limited_neighbors() {
  # These targets cannot hold the protocol-maximum neighbor table. Classic
  # ESP32 MQTT observers exhaust internal DRAM, while the 256 KiB STM32WL
  # targets exhaust their fixed application region because initialized table
  # storage is part of the image.
  #
  # Classic ESP32 bridge targets only need the lower limit when the expanded
  # FULL profile also enables packet logging. Keep their ordinary and
  # non-logging FULL profiles at the protocol maximum.
  if [ "$ESP32_FULL_BUILD" = "1" ] \
      && [ "${PACKET_LOGGING_OVERRIDE,,}" = "on" ]; then
    case "${1,,}" in
      generic_e22_sx1262_repeater_bridge_espnow \
        |generic_e22_sx1268_repeater_bridge_espnow \
        |heltec_v2_repeater_bridge_espnow \
        |lilygo_tlora_v2_1_1_6_repeater_bridge_espnow \
        |lilygo_tlora_v2_1_1_6_repeater_bridge_rs232 \
        |meshadventurer_sx1262_repeater_bridge_espnow \
        |meshadventurer_sx1268_repeater_bridge_espnow \
        |tbeam_sx1262_repeater_bridge_espnow \
        |tbeam_sx1276_repeater_bridge_espnow)
        return 0
        ;;
    esac
  fi
  case "${1,,}" in
    tbeam_sx1262_repeater_observer_mqtt|tbeam_sx1262_room_server_observer_mqtt|tbeam_sx1276_repeater_observer_mqtt|tbeam_sx1276_room_server_observer_mqtt|rak_3x72_repeater|tiny_relay_repeater|wio-e5-mini_repeater|wio-e5-repeater_bridge_rs232|wio-e5_repeater) return 0 ;;
    *) return 1 ;;
  esac
}

is_repeater_role_target() {
  case "${1,,}" in
    *repeater*|*repeatr*) return 0 ;;
    *) return 1 ;;
  esac
}

is_lora_ota_build() {
  local env_name=$1
  local env_name_lc=${env_name,,}

  # ESP32 USB and WiFi companions keep OTA so they can seed a host folder over
  # serial or TCP and can participate in LoRa OTA without using the FULL profile.
  if is_esp32_usb_wifi_companion_ota_build "$env_name"; then
    if requires_esp32_companion_full_ota_fallback "$env_name" && [ "$ESP32_FULL_BUILD" != "1" ]; then
      return 1
    fi
    return 0
  fi

  # FULL ESP32 artifacts use expanded A/B slots and retain every compiled
  # feature, including LoRa OTA, for every supported non-companion role.
  if [ "$ESP32_FULL_BUILD" = "1" ] && supports_esp32_full_build "$env_name"; then
    return 0
  fi

  if [ "${PIO_ENV_OTA_BY_NAME[$env_name]:-0}" != "1" ]; then
    return 1
  fi

  # The OTA manager, staging store, and self-install path are deliberately opt-in. The standard repeater
  # remains a normal, sensor-enabled build, but Mesh transport still relays OTA floods opaquely during
  # TempRadio. Its explicit _lora_ota_no_external_sensors sibling is the constrained self-updatable image.
  if [[ "$env_name_lc" != *lora_ota_no_external_sensors ]]; then
    return 1
  fi

  if is_mqtt_bridge_target "$env_name" \
      || [ "${MQTT_BRIDGE_OVERRIDE,,}" == "on" ] \
      || [ "${MESHDEBUG_OVERRIDE,,}" == "on" ] \
      || [ "${PACKET_LOGGING_OVERRIDE,,}" == "on" ] \
      || [ "$FIRMWARE_FILENAME_INFIX" == "logging" ]; then
    return 1
  fi

  # The constrained portable OTA profile is only emitted for repeaters. FULL
  # ESP32 builds returned above also enable LoRa OTA for room-server, sensor,
  # observer, and bridge roles because their expanded slots have room for every
  # feature.
  case "$env_name_lc" in
    *repeater*|*repeatr*) return 0 ;;
    *) return 1 ;;
  esac
}

is_esp32_companion_build() {
  case "${1,,}" in
    *companion*|*comp_radio*) return 0 ;;
    *) return 1 ;;
  esac
}

requires_esp32_portable_app_slot() {
  [ "${PIO_ENV_PLATFORM_BY_NAME[$1]:-}" = "ESP32_PLATFORM" ] \
    && ! is_esp32_companion_build "$1"
}

is_esp32_c6_target() {
  [[ "${PIO_ENV_BOARD_BY_NAME[$1]:-}" == esp32-c6-* ]]
}

requires_esp32_portable_size_ceiling() {
  local env_name=$1

  requires_esp32_portable_app_slot "$env_name" || return 1

  # Arduino 3.x pulls substantially more WiFi runtime into ESP32-C6 images.
  # These target-specific OTA siblings already used 1920 KiB or larger A/B app
  # slots before the compact browser uploader was enabled, so retain their
  # established partition contract and enforce the actual partition below.
  if is_lora_ota_only_target "$env_name" && is_esp32_c6_target "$env_name"; then
    return 1
  fi

  return 0
}

supports_esp32_full_build() {
  local env_name=$1

  if requires_esp32_companion_full_ota_fallback "$env_name"; then
    return 0
  fi

  [ "${PIO_ENV_FULL_BUILD_BY_NAME[$env_name]:-0}" = "1" ] \
    && ! is_esp32_companion_build "$env_name" \
    && ! is_lora_ota_only_target "$env_name"
}

apply_esp32_lora_ota_size_profile() {
  local env_name=$1

  if [ "$ESP32_FULL_BUILD" = "1" ] || ! requires_esp32_portable_app_slot "$env_name"; then
    return 0
  fi

  # All non-companion ESP32 artifacts must remain installable into the legacy
  # 0x10000..0x150000 app slot. The WebConfig portal is deliberately omitted.
  # WiFi/MQTT observers and lean LoRa-OTA repeaters retain the compact browser
  # updater; other radio-only roles avoid linking WiFi solely for that updater.
  # Companions retain their target defaults because they are installed over USB.
  # Keep ENV_INCLUDE_GPS for boards with onboard GPS; their target sensor
  # managers require that support.
  export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DWEBCONFIG_DISABLED=1"
  if is_mqtt_bridge_target "$env_name"; then
    # Keep the standard ESP-IDF libc. Use its ABI with the chip-ROM formatter,
    # and retain a compact CLI for radio and active-bridge settings.
    export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DLIGHTWEIGHT_WIFI_OTA=1 -DPORTABLE_MQTT_OBSERVER=1 -DPORTABLE_ESP32_RADIO_CLI=1 -UDISPLAY_CLASS"
    if [ "$FIRMWARE_FILENAME_INFIX" != "logging" ] \
        && [ "${MESHDEBUG_OVERRIDE,,}" != "on" ] \
        && [ "${PACKET_LOGGING_OVERRIDE,,}" != "on" ]; then
      export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DPORTABLE_ESP32_ROM_NANO_FORMAT=1"
    fi
    append_platformio_build_unflags "-DWITH_SNMP=1 -DMQTT_DEBUG=1 -DMQTT_MEMORY_DEBUG=1 -DDISPLAY_CLASS=SSD1306Display -DENV_INCLUDE_AHTX0=1 -DENV_INCLUDE_BME280=1 -DENV_INCLUDE_BMP280=1 -DENV_INCLUDE_SHTC3=1 -DENV_INCLUDE_SHT4X=1 -DENV_INCLUDE_LPS22HB=1 -DENV_INCLUDE_INA3221=1 -DENV_INCLUDE_INA219=1 -DENV_INCLUDE_INA226=1 -DENV_INCLUDE_INA260=1 -DENV_INCLUDE_MLX90614=1 -DENV_INCLUDE_VL53L0X=1 -DENV_INCLUDE_BME680=1 -DENV_INCLUDE_BMP085=1 -DENV_INCLUDE_RAK12035=1 -DENV_INCLUDE_BME680_BSEC=1"
  elif [[ "${env_name,,}" == *bridge_espnow* ]]; then
    append_platformio_build_unflags "-DLIGHTWEIGHT_WIFI_OTA=1"
    export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -ULIGHTWEIGHT_WIFI_OTA -DDISABLE_WIFI_OTA=1 -DPORTABLE_ESP32_RADIO_CLI=1 -UDISPLAY_CLASS"
    if [ "$FIRMWARE_FILENAME_INFIX" != "logging" ] \
        && [ "${MESHDEBUG_OVERRIDE,,}" != "on" ] \
        && [ "${PACKET_LOGGING_OVERRIDE,,}" != "on" ]; then
      export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DPORTABLE_ESP32_ROM_NANO_FORMAT=1"
    fi
    append_platformio_build_unflags "-DDISPLAY_CLASS=SSD1306Display -DENV_INCLUDE_AHTX0=1 -DENV_INCLUDE_BME280=1 -DENV_INCLUDE_BMP280=1 -DENV_INCLUDE_SHTC3=1 -DENV_INCLUDE_SHT4X=1 -DENV_INCLUDE_LPS22HB=1 -DENV_INCLUDE_INA3221=1 -DENV_INCLUDE_INA219=1 -DENV_INCLUDE_INA226=1 -DENV_INCLUDE_INA260=1 -DENV_INCLUDE_MLX90614=1 -DENV_INCLUDE_VL53L0X=1 -DENV_INCLUDE_BME680=1 -DENV_INCLUDE_BMP085=1 -DENV_INCLUDE_RAK12035=1 -DENV_INCLUDE_BME680_BSEC=1"
  elif is_lora_ota_only_target "$env_name"; then
    # The no-external-sensors image is also the self-updatable field image. Keep
    # manual browser OTA available on every ESP32 family through the compact
    # uploader, and use the full one-byte neighbor-index range.
    append_platformio_build_unflags "-DDISABLE_WIFI_OTA=1 -DMAX_NEIGHBOURS=50 -DMAX_NEIGHBOURS=8"
    export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -UDISABLE_WIFI_OTA -DLIGHTWEIGHT_WIFI_OTA=1 -DMAX_NEIGHBOURS=${ESP32_FULL_MAX_NEIGHBOURS}"
  else
    append_platformio_build_unflags "-DLIGHTWEIGHT_WIFI_OTA=1"
    export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -ULIGHTWEIGHT_WIFI_OTA -DDISABLE_WIFI_OTA=1"
  fi

}

apply_esp32_full_size_profile() {
  local env_name=$1
  local max_neighbours=$ESP32_FULL_MAX_NEIGHBOURS

  if [ "$ESP32_FULL_BUILD" != "1" ] || ! supports_esp32_full_build "$env_name"; then
    return 0
  fi

  # The FULL artifact uses expanded dual-OTA slots, so restore features that
  # target or portable profiles disabled only to save application space.
  append_platformio_build_unflags "-DWEBCONFIG_DISABLED=1"
  export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -UWEBCONFIG_DISABLED -DWIFI_OTA_SEEDER=1 -DMESHCORE_ESP32_FULL_PROFILE=1"

  # Keep ordinary builds at their board-defined neighbor capacity. FULL builds
  # normally use the largest table supported by the one-byte neighbor discovery
  # indexes. Explicitly DRAM-limited classic ESP32 observers retain 50 entries.
  if requires_dram_limited_neighbors "$env_name"; then
    max_neighbours=$DRAM_LIMITED_MAX_NEIGHBOURS
    append_platformio_build_unflags "-DMAX_NEIGHBOURS=8"
  else
    append_platformio_build_unflags "-DMAX_NEIGHBOURS=50 -DMAX_NEIGHBOURS=8"
  fi
  export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DMAX_NEIGHBOURS=${max_neighbours}"

  # Restore the full ElegantOTA implementation only when the target already
  # declares its dependency. Some ESP32-C6 targets intentionally have no
  # compatible ElegantOTA library and retain their target WiFi-OTA setting.
  if [ "${PIO_ENV_FULL_WIFI_OTA_BY_NAME[$env_name]:-0}" = "1" ]; then
    append_platformio_build_unflags "-DDISABLE_WIFI_OTA=1 -DLIGHTWEIGHT_WIFI_OTA=1"
    export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -UDISABLE_WIFI_OTA -ULIGHTWEIGHT_WIFI_OTA"
  fi

  if requires_esp32_companion_full_ota_fallback "$env_name"; then
    append_platformio_build_unflags "-DMAX_CONTACTS=350 -DMAX_CONTACTS=160 -DMAX_GROUP_CHANNELS=40 -DOFFLINE_QUEUE_SIZE=256 -DOFFLINE_QUEUE_SIZE=128"
    export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DMAX_CONTACTS=100 -DMAX_GROUP_CHANNELS=8 -DOFFLINE_QUEUE_SIZE=16"
  fi
}

apply_repeater_neighbor_capacity() {
  local env_name=$1
  local max_neighbours=$REPEATER_MAX_NEIGHBOURS

  if ! is_repeater_role_target "$env_name"; then
    return 0
  fi

  # Repeater discovery uses one-byte indexes, so 254 is the largest usable
  # table. Keep explicitly constrained targets at their measured safe capacity.
  if requires_dram_limited_neighbors "$env_name"; then
    max_neighbours=$DRAM_LIMITED_MAX_NEIGHBOURS
    append_platformio_build_unflags "-DMAX_NEIGHBOURS=8 -DMAX_NEIGHBOURS=254"
  else
    append_platformio_build_unflags "-DMAX_NEIGHBOURS=8 -DMAX_NEIGHBOURS=50"
  fi
  export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DMAX_NEIGHBOURS=${max_neighbours}"
}

apply_nrf52_lora_ota_size_profile() {
  local env_name=$1

  if [ "${PIO_ENV_PLATFORM_BY_NAME[$env_name]:-}" != "NRF52_PLATFORM" ] \
      || ! is_lora_ota_build "$env_name" \
      || ! is_lora_ota_only_target "$env_name"; then
    return 0
  fi

  # The Adafruit nRF52 platform defaults release builds to -Ofast. Once the
  # runtime software Ed25519 fallback is linked alongside CC310, that setting
  # fully expands repeated Curve25519 arithmetic and wastes tens of kilobytes.
  # Keep hardware crypto, RNG mixing, the software fallback, and board features;
  # only select the size optimizer for the constrained self-updatable image.
  append_platformio_build_unflags "-Ofast"
  export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -Os"
}

apply_lora_ota_no_external_sensors_profile() {
  local env_name=$1

  if ! is_lora_ota_build "$env_name" || ! is_lora_ota_only_target "$env_name"; then
    return 0
  fi

  # The explicit LoRa-OTA sibling additionally drops optional external sensors.
  # Its ordinary sibling remains sensor-enabled.
  # Keep board-integrated GPS support. Several target implementations require
  # their location provider even when optional external I2C sensors are absent.
  append_platformio_build_unflags "-DENV_INCLUDE_AHTX0=1 -DENV_INCLUDE_BME280=1 -DENV_INCLUDE_BMP280=1 -DENV_INCLUDE_SHTC3=1 -DENV_INCLUDE_SHT4X=1 -DENV_INCLUDE_LPS22HB=1 -DENV_INCLUDE_INA3221=1 -DENV_INCLUDE_INA219=1 -DENV_INCLUDE_INA226=1 -DENV_INCLUDE_INA260=1 -DENV_INCLUDE_MLX90614=1 -DENV_INCLUDE_VL53L0X=1 -DENV_INCLUDE_BME680=1 -DENV_INCLUDE_BMP085=1 -DENV_INCLUDE_RAK12035=1 -DENV_INCLUDE_BME680_BSEC=1"
  export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -UENV_INCLUDE_AHTX0 -UENV_INCLUDE_BME280 -UENV_INCLUDE_BMP280 -UENV_INCLUDE_SHTC3 -UENV_INCLUDE_SHT4X -UENV_INCLUDE_LPS22HB -UENV_INCLUDE_INA3221 -UENV_INCLUDE_INA219 -UENV_INCLUDE_INA226 -UENV_INCLUDE_INA260 -UENV_INCLUDE_MLX90614 -UENV_INCLUDE_VL53L0X -UENV_INCLUDE_BME680 -UENV_INCLUDE_BMP085 -UENV_INCLUDE_RAK12035 -UENV_INCLUDE_BME680_BSEC"
}

append_platformio_build_unflags() {
  local flags=$1
  local flag
  local entry

  for flag in $flags; do
    case "$flag" in
      -D*) entry="-D ${flag#-D}" ;;
      *) entry="$flag" ;;
    esac
    if [ -n "${PLATFORMIO_BUILD_UNFLAGS:-}" ]; then
      PLATFORMIO_BUILD_UNFLAGS+=$'\n'
    fi
    PLATFORMIO_BUILD_UNFLAGS+="$entry"
  done
  export PLATFORMIO_BUILD_UNFLAGS
}

pio_env_option_contains() {
  local env_name=$1
  local option_name=$2
  local needle=$3

  python3 -c '
import json
import sys

env_name, option_name, needle = sys.argv[1:]
for section, options in json.load(sys.stdin):
    if section != f"env:{env_name}":
        continue
    for key, value in options:
        if key != option_name:
            continue
        values = value if isinstance(value, list) else str(value).split()
        if any(needle in str(item) for item in values):
            sys.exit(0)
    break
sys.exit(1)
' "$env_name" "$option_name" "$needle" <<<"$PIO_CONFIG_JSON"
}

append_platformio_build_src_filter() {
  local rule=$1

  if [ -n "${PLATFORMIO_BUILD_SRC_FILTER:-}" ]; then
    PLATFORMIO_BUILD_SRC_FILTER+=$'\n'
  fi
  PLATFORMIO_BUILD_SRC_FILTER+="$rule"
  export PLATFORMIO_BUILD_SRC_FILTER
}

append_platformio_extra_script() {
  local script=$1

  if [ -n "${PLATFORMIO_EXTRA_SCRIPTS:-}" ]; then
    PLATFORMIO_EXTRA_SCRIPTS+=$'\n'
  fi
  PLATFORMIO_EXTRA_SCRIPTS+="$script"
  export PLATFORMIO_EXTRA_SCRIPTS
}

apply_nrf52_lora_ota_build_recipe() {
  local env_name=$1
  local pio_env_name=$2

  if [ "${PIO_ENV_PLATFORM_BY_NAME[$env_name]:-}" != "NRF52_PLATFORM" ]; then
    return 0
  fi
  if ! is_lora_ota_build "$env_name" \
      && ! is_nrf52_companion_radio_full_target "$env_name"; then
    return 0
  fi

  # Synthetic OTA aliases and full Companions build an ordinary PlatformIO
  # environment with an OTA overlay. Most nRF52 bases already include this
  # recipe, but a new board may not. Add only the missing pieces so ENABLE_OTA
  # never reaches the linker without its implementation or the EndF
  # trailer/zip post-build step.
  if ! pio_env_option_contains "$pio_env_name" build_src_filter "helpers/ota/"; then
    append_platformio_build_src_filter "+<helpers/ota/*.cpp>"
  fi
  if ! pio_env_option_contains "$pio_env_name" extra_scripts "tools/mota/pio_endf.py"; then
    append_platformio_extra_script "post:tools/mota/pio_endf.py"
  fi
}

apply_lora_ota_override() {
  local env_name=$1

  # The full companion has its own serve-only overlay. Do not run the generic
  # install-capable/disabled switch first: PlatformIO build_unflags would also
  # remove the overlay's later ENABLE_OTA definition.
  if is_companion_radio_full_target "$env_name"; then
    return 0
  fi

  if is_lora_ota_build "$env_name"; then
    if [ "${PIO_ENV_SD_OTA_BY_NAME[$env_name]:-0}" = "1" ]; then
      append_platformio_build_unflags "-UENABLE_OTA -DDISABLE_LORA_OTA=1 -DOTA_FLASH_STORE=1"
      export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -UDISABLE_LORA_OTA -DENABLE_OTA=1 -UOTA_FLASH_STORE -DOTA_SD_STORE=1 -DOTA_FOLDER_SERIAL"
    else
      append_platformio_build_unflags "-UENABLE_OTA -DDISABLE_LORA_OTA=1"
      export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -UDISABLE_LORA_OTA -DENABLE_OTA=1 -DOTA_FLASH_STORE=1 -DOTA_FOLDER_SERIAL"
    fi
  else
    append_platformio_build_unflags "-DENABLE_OTA=1"
    export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -UENABLE_OTA"
  fi
}

apply_companion_radio_full_profile() {
  local env_name=$1
  local pio_env_name=$2

  is_companion_radio_full_target "$env_name" || return 0

  # Every full Companion is a LoRa mOTA source, never an update destination.
  # Remove inherited staging/install stores before adding the platform's host
  # folder transport.
  append_platformio_build_unflags "-UENABLE_OTA -DOTA_FLASH_STORE=1 -DOTA_SD_STORE=1 -DDISABLE_LORA_OTA=1"
  export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -UDISABLE_LORA_OTA -DENABLE_OTA=1 -UOTA_FLASH_STORE -UOTA_SD_STORE -DOTA_SEEDER_ONLY=1 -DMOTA_TARGET_ID=0 -DCOMPANION_RADIO_FULL=1 -DENABLE_USB_INTERFACE=1 -DBLE_PIN_CODE=123456"

  if is_nrf52_companion_radio_full_target "$env_name"; then
    # The USB stream starts as Binary Companion. `motatool serve --serial`
    # switches it into an exclusive host-folder mode with its existing
    # `ota folder on` preamble; BLE remains an independent Companion link.
    append_platformio_build_unflags "-UOTA_FOLDER_SERIAL"
    export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DOTA_FOLDER_SERIAL=1"

    if ! pio_env_option_contains "$pio_env_name" build_src_filter "helpers/ota/"; then
      append_platformio_build_src_filter "+<helpers/ota/*.cpp>"
    fi
    if ! pio_env_option_contains "$pio_env_name" build_src_filter "helpers/nrf52/*.cpp" \
        && ! pio_env_option_contains "$pio_env_name" build_src_filter "helpers/nrf52/SerialBLEInterface.cpp"; then
      append_platformio_build_src_filter "+<helpers/nrf52/SerialBLEInterface.cpp>"
    fi
    return 0
  fi

  # ESP32 inherits the WiFi companion recipe and uses its dedicated TCP folder
  # seeder instead of multiplexing mOTA data onto USB.
  append_platformio_build_unflags "-DOTA_FOLDER_SERIAL"
  export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -UOTA_FOLDER_SERIAL -DWIFI_OTA_SEEDER=1"

  # BLE + WiFi exhaust internal DRAM on these high-capacity ESP32 recipes. Use
  # measured-safe tables for FULL OTA without changing ordinary USB/BLE/WiFi
  # companion builds.
  if requires_esp32_companion_full_ota_fallback "$pio_env_name"; then
    append_platformio_build_unflags "-DMAX_CONTACTS=350 -DMAX_CONTACTS=160 -DMAX_GROUP_CHANNELS=40 -DOFFLINE_QUEUE_SIZE=256 -DOFFLINE_QUEUE_SIZE=128"
    export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DMAX_CONTACTS=100 -DMAX_GROUP_CHANNELS=8 -DOFFLINE_QUEUE_SIZE=16"
  fi

  # A few WiFi recipes list only their WiFi implementation instead of the
  # helpers/esp32 wildcard used by newer boards. Add the BLE implementation
  # explicitly when the inherited source filter does not already include it.
  if ! pio_env_option_contains "$pio_env_name" build_src_filter "helpers/esp32/*.cpp" \
      && ! pio_env_option_contains "$pio_env_name" build_src_filter "helpers/esp32/SerialBLEInterface.cpp"; then
    append_platformio_build_src_filter "+<helpers/esp32/SerialBLEInterface.cpp>"
  fi
}

apply_radio_overrides() {
  if [ -n "$RADIO_FREQ_OVERRIDE" ] && [ -n "$RADIO_BW_OVERRIDE" ] && [ -n "$RADIO_SF_OVERRIDE" ] && [ -n "$RADIO_CR_OVERRIDE" ]; then
    export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DLORA_FREQ=${RADIO_FREQ_OVERRIDE} -DLORA_BW=${RADIO_BW_OVERRIDE} -DLORA_SF=${RADIO_SF_OVERRIDE} -DLORA_CR=${RADIO_CR_OVERRIDE}"
  fi
}

apply_firmware_profile_overrides() {
  case "${FIRMWARE_PROFILE_OVERRIDE,,}" in
    cascade)
      export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DCASCADE_PROFILE=1 -DDEFAULT_PATH_HASH_MODE=2 -DDEFAULT_LOOP_DETECT=1 -DDEFAULT_CAD_ENABLED=1 -DDEFAULT_RX_DELAY_BASE=2.0f -DDEFAULT_AGC_RESET_INTERVAL_SECONDS=8 -DDEFAULT_ADVERT_INTERVAL_MINUTES=0 -DDEFAULT_FLOOD_ADVERT_INTERVAL_HOURS=83 -DDEFAULT_MULTI_ACKS=1 -DDEFAULT_MANUAL_ADD_CONTACTS=1 -DDEFAULT_AUTOADD_CONFIG=0"
      ;;
  esac
}

print_build_flags() {
  local env_name=$1

  echo "Build flags for ${env_name}:"
  python3 -c '
import json
import os
import shlex
import sys

env_name = sys.argv[1]
data = json.load(sys.stdin)
config_flags = []

for section, options in data:
    if section != f"env:{env_name}":
        continue
    for key, value in options:
        if key != "build_flags":
            continue
        if isinstance(value, list):
            config_flags.extend(str(flag) for flag in value)
        elif value:
            config_flags.extend(shlex.split(str(value)))
    break

env_flags = shlex.split(os.environ.get("PLATFORMIO_BUILD_FLAGS", ""))
env_unflags = os.environ.get("PLATFORMIO_BUILD_UNFLAGS", "").splitlines()

def print_flags(title, flags):
    print(f"  {title}:")
    if not flags:
        print("    (none)")
        return
    for flag in flags:
        print(f"    {flag}")

print_flags("platformio.ini build_flags", config_flags)
print_flags("PLATFORMIO_BUILD_FLAGS", env_flags)
print_flags("PLATFORMIO_BUILD_UNFLAGS", env_unflags)
' "$env_name" <<<"$PIO_CONFIG_JSON"
}

copy_build_output() {
  local source_path=$1
  local output_path=$2

  if ! [ -f "$source_path" ]; then
    echo "Expected build output missing: $source_path"
    return 1
  fi

  cp -- "$source_path" "$output_path"
}

collect_esp32_artifacts() {
  local env_name=$1
  local pio_env_name=$2
  local firmware_filename=$3
  local build_output_dir="${PIO_BUILD_DIR_OVERRIDE:-${PLATFORMIO_BUILD_DIR:-.pio/build}}/${pio_env_name}"
  local -a size_check_args=(
    "${build_output_dir}/firmware.bin"
    "${build_output_dir}/partitions.bin"
  )
  local partsig_name=$env_name

  if [ "$ESP32_FULL_BUILD" != "1" ] && requires_esp32_portable_size_ceiling "$env_name"; then
    size_check_args+=("$ESP32_LORA_OTA_APP_LIMIT")
  fi

  python3 scripts/check_esp32_app_size.py "${size_check_args[@]}" || return $?
  copy_build_output "${build_output_dir}/firmware.bin" "${OUTPUT_DIR}/${firmware_filename}.bin" || return $?
  copy_build_output "${build_output_dir}/firmware-merged.bin" "${OUTPUT_DIR}/${firmware_filename}-merged.bin" || return $?

  # Emit the partition-table signature for OTA partition-compatibility checks.
  # Standard builds keep the env-name key used by the slim-manifest generator;
  # FULL builds use a suffix so their expanded table does not overwrite the
  # portable signature. The firmware computes the same signature at runtime.
  # Best-effort: local builds without the script's deps just skip it.
  if [ -f "${build_output_dir}/partitions.bin" ]; then
    if [ "$ESP32_FULL_BUILD" = "1" ]; then
      partsig_name="${env_name}-full"
    fi
    python3 scripts/partition_signature.py "${build_output_dir}/partitions.bin" > "${OUTPUT_DIR}/${partsig_name}.partsig" 2>/dev/null || true
  fi
}

collect_nrf52_artifacts() {
  local env_name=$1
  local pio_env_name=$2
  local firmware_filename=$3
  local build_output_dir="${PIO_BUILD_DIR_OVERRIDE:-${PLATFORMIO_BUILD_DIR:-.pio/build}}/${pio_env_name}"

  python3 bin/uf2conv/uf2conv.py "${build_output_dir}/firmware.hex" -c -o "${build_output_dir}/firmware.uf2" -f 0xADA52840 || return $?
  copy_build_output "${build_output_dir}/firmware.uf2" "${OUTPUT_DIR}/${firmware_filename}.uf2" || return $?
  if [ -f "${build_output_dir}/firmware.zip" ]; then
    copy_build_output "${build_output_dir}/firmware.zip" "${OUTPUT_DIR}/${firmware_filename}.zip" || return $?
  fi
}

collect_stm32_artifacts() {
  local env_name=$1
  local pio_env_name=$2
  local firmware_filename=$3
  local build_output_dir="${PIO_BUILD_DIR_OVERRIDE:-${PLATFORMIO_BUILD_DIR:-.pio/build}}/${pio_env_name}"

  copy_build_output "${build_output_dir}/firmware.bin" "${OUTPUT_DIR}/${firmware_filename}.bin" || return $?
  copy_build_output "${build_output_dir}/firmware.hex" "${OUTPUT_DIR}/${firmware_filename}.hex" || return $?
}

collect_rp2040_artifacts() {
  local env_name=$1
  local pio_env_name=$2
  local firmware_filename=$3
  local build_output_dir="${PIO_BUILD_DIR_OVERRIDE:-${PLATFORMIO_BUILD_DIR:-.pio/build}}/${pio_env_name}"

  copy_build_output "${build_output_dir}/firmware.bin" "${OUTPUT_DIR}/${firmware_filename}.bin" || return $?
  copy_build_output "${build_output_dir}/firmware.uf2" "${OUTPUT_DIR}/${firmware_filename}.uf2" || return $?
}

output_artifact_exists() {
  [ -s "${OUTPUT_DIR}/$1" ]
}

build_artifacts_exist() {
  local env_platform=$1
  local firmware_filename=$2

  case "$env_platform" in
    ESP32_PLATFORM)
      output_artifact_exists "${firmware_filename}.bin" \
        && output_artifact_exists "${firmware_filename}-merged.bin"
      ;;
    NRF52_PLATFORM)
      output_artifact_exists "${firmware_filename}.uf2"
      ;;
    STM32_PLATFORM)
      output_artifact_exists "${firmware_filename}.bin" \
        && output_artifact_exists "${firmware_filename}.hex"
      ;;
    RP2040_PLATFORM)
      output_artifact_exists "${firmware_filename}.bin" \
        && output_artifact_exists "${firmware_filename}.uf2"
      ;;
    *)
      return 1
      ;;
  esac
}

collect_build_artifacts() {
  local env_name=$1
  local env_platform=$2
  local pio_env_name=$3
  local firmware_filename=$4

  # Post-build outputs differ by platform, so dispatch to the matching
  # collector after the main firmware build succeeds.
  case "$env_platform" in
    ESP32_PLATFORM)
      collect_esp32_artifacts "$env_name" "$pio_env_name" "$firmware_filename"
      ;;
    NRF52_PLATFORM)
      collect_nrf52_artifacts "$env_name" "$pio_env_name" "$firmware_filename"
      ;;
    STM32_PLATFORM)
      collect_stm32_artifacts "$env_name" "$pio_env_name" "$firmware_filename"
      ;;
    RP2040_PLATFORM)
      collect_rp2040_artifacts "$env_name" "$pio_env_name" "$firmware_filename"
      ;;
    *)
      echo "Unsupported or unknown platform for env: $env_name"
      return 1
      ;;
  esac
}

get_firmware_filename() {
  local env_name=$1
  local firmware_version_string=$2
  local filename_infix=$FIRMWARE_FILENAME_INFIX

  if [ -z "$filename_infix" ] \
      && [ "${PACKET_LOGGING_OVERRIDE,,}" == "on" ] \
      && [ "${MQTT_BRIDGE_OVERRIDE,,}" != "on" ] \
      && ! is_mqtt_bridge_target "$env_name"; then
    filename_infix="logging"
  fi

  # Make LoRa-OTA artifacts as obvious as logging artifacts without changing
  # the PlatformIO environment name or the stable MOTA target identity. FULL
  # artifacts retain their profile marker as well as the required OTA marker.
  if [ "$ESP32_FULL_BUILD" = "1" ] && is_lora_ota_build "$env_name"; then
    if [ "$filename_infix" = "full-logging" ]; then
      filename_infix="full-logging-ota"
    else
      filename_infix="full-ota"
    fi
  elif [ -z "$filename_infix" ] && is_lora_ota_build "$env_name"; then
    filename_infix="ota"
  fi

  if [ -n "$filename_infix" ]; then
    echo "${env_name}-${filename_infix}-${firmware_version_string}"
  else
    echo "${env_name}-${firmware_version_string}"
  fi
}

restore_platformio_build_flags() {
  local had_platformio_build_flags=$1
  local original_platformio_build_flags=${2:-}

  if [ "$had_platformio_build_flags" -eq 1 ]; then
    export PLATFORMIO_BUILD_FLAGS="$original_platformio_build_flags"
  else
    unset PLATFORMIO_BUILD_FLAGS
  fi
}

build_firmware() {
  local env_name=$1
  local pio_env_name
  local env_platform
  local commit_hash
  local firmware_build_date
  local firmware_build_epoch
  local firmware_version
  local firmware_version_string
  local firmware_filename
  local mota_target_id
  local mota_target_flag=""
  local original_platformio_build_flags
  local original_platformio_build_unflags
  local original_platformio_build_src_filter
  local original_platformio_extra_scripts
  local target_extra_scripts
  local had_platformio_build_flags=0
  local had_platformio_build_unflags=0
  local had_platformio_build_src_filter=0
  local had_platformio_extra_scripts=0
  local build_status
  local -a pio_run_args=()

  env_platform=$(get_platform_for_env "$env_name")
  if ! is_supported_platform "$env_platform"; then
    echo "Unsupported or unknown platform for env: $env_name"
    return 1
  fi
  pio_env_name=$(get_pio_build_env "$env_name")

  commit_hash=$(git rev-parse --short HEAD)
  firmware_build_date=$(date -u '+%d-%b-%Y')
  firmware_build_epoch=$(date -u '+%s')
  firmware_version=${FIRMWARE_VERSION:-}

  if [ -z "$firmware_version" ]; then
    if [ "$BATCH_BUILD_MODE" -eq 1 ]; then
      firmware_version=$(derive_default_firmware_version "$env_name")
    else
      prompt_for_firmware_version "$env_name" firmware_version
    fi
    echo "FIRMWARE_VERSION not set, using derived default for ${env_name}: ${firmware_version}"
  fi

  firmware_version_string="${firmware_version}-${commit_hash}"
  firmware_filename=$(get_firmware_filename "$env_name" "$firmware_version_string")

  # OTA target id = sha2-256:4(env_name) as a little-endian uint32 (matches tools/mota target_id_for_env
  # and the device's MainBoard::getOtaTargetId()). Only OTA-enabled profiles receive this identifier.
  if is_lora_ota_build "$env_name"; then
    mota_target_id=$(python3 -c "import hashlib,sys;print('0x%08x'%int.from_bytes(hashlib.sha256(sys.argv[1].encode()).digest()[:4],'little'))" "$env_name" 2>/dev/null || echo "")
    if [ -n "$mota_target_id" ]; then
      mota_target_flag=" -DMOTA_TARGET_ID=${mota_target_id}"
    fi
  fi

  # Fork CI hooks (consumed by .github/workflows/build-observer*-firmwares.yml).
  # Tag the *embedded* version for observer builds (v1.0.0-observer-abcdef) so
  # `ver`, the MQTT firmware_version, and SNMP identify the fork, and stamp the
  # per-base published-build counter (FIRMWARE_BUILD_NUMBER) as a 4th version
  # component so `ota check` can show how many builds behind a node is. The
  # *filename* stays untagged/un-numbered so assets remain <env>-v<base>-<hash>.
  # OTA_VARIANT is the env name - it selects the slim per-variant manifest
  # (<OTA_MANIFEST_BASE>/<OTA_VARIANT>.json) that the observer pull-OTA fetches.
  local embedded_variant_tag=""
  case "$env_name" in
    *observer*) embedded_variant_tag="-observer" ;;
  esac
  local embedded_build_suffix=""
  if [ -n "${FIRMWARE_BUILD_NUMBER:-}" ]; then
    embedded_build_suffix=".${FIRMWARE_BUILD_NUMBER}"
  fi
  local embedded_version_string="${firmware_version}${embedded_build_suffix}${embedded_variant_tag}-${commit_hash}"

  if [ "$RESUME_BUILD_OUTPUT" == "1" ] && build_artifacts_exist "$env_platform" "$firmware_filename"; then
    echo "Skipping ${env_name}; existing artifacts found for ${firmware_filename}."
    return 0
  fi

  if [ "${PLATFORMIO_BUILD_FLAGS+x}" ]; then
    had_platformio_build_flags=1
    original_platformio_build_flags=$PLATFORMIO_BUILD_FLAGS
  else
    original_platformio_build_flags=""
  fi
  if [ "${PLATFORMIO_BUILD_UNFLAGS+x}" ]; then
    had_platformio_build_unflags=1
    original_platformio_build_unflags=$PLATFORMIO_BUILD_UNFLAGS
  else
    original_platformio_build_unflags=""
  fi
  if [ "${PLATFORMIO_BUILD_SRC_FILTER+x}" ]; then
    had_platformio_build_src_filter=1
    original_platformio_build_src_filter=$PLATFORMIO_BUILD_SRC_FILTER
  else
    original_platformio_build_src_filter=""
  fi
  if [ "${PLATFORMIO_EXTRA_SCRIPTS+x}" ]; then
    had_platformio_extra_scripts=1
    original_platformio_extra_scripts=$PLATFORMIO_EXTRA_SCRIPTS
  else
    original_platformio_extra_scripts=""
  fi

  export PLATFORMIO_BUILD_FLAGS="${original_platformio_build_flags} -DFIRMWARE_BUILD_DATE='\"${firmware_build_date}\"' -DFIRMWARE_BUILD_EPOCH=${firmware_build_epoch} -DFIRMWARE_VERSION='\"${embedded_version_string}\"' -DOTA_VARIANT='\"${env_name}\"'${mota_target_flag}"
  disable_debug_flags
  apply_debug_overrides
  apply_mqtt_bridge_override
  disable_usb_logging_for_mqtt "$env_name"
  apply_lora_ota_override "$env_name"
  apply_companion_radio_full_profile "$env_name" "$pio_env_name"
  apply_nrf52_lora_ota_build_recipe "$env_name" "$pio_env_name"
  apply_esp32_lora_ota_size_profile "$env_name"
  apply_esp32_full_size_profile "$env_name"
  apply_repeater_neighbor_capacity "$env_name"
  apply_nrf52_lora_ota_size_profile "$env_name"
  apply_lora_ota_no_external_sensors_profile "$env_name"
  apply_radio_overrides
  apply_firmware_profile_overrides

  if [ "$ESP32_FULL_BUILD" = "1" ] || is_esp32_companion_radio_full_target "$env_name"; then
    export MESHCORE_ESP32_FULL_BUILD=1
    if is_esp32_companion_radio_full_target "$env_name"; then
      export MESHCORE_COMPANION_RADIO_FULL=1
    else
      unset MESHCORE_COMPANION_RADIO_FULL
    fi
    target_extra_scripts=$original_platformio_extra_scripts
    if [[ "$target_extra_scripts" != *"scripts/esp32_full_partition.py"* ]]; then
      if [ -n "$target_extra_scripts" ]; then
        target_extra_scripts+=$'\n'
      fi
      target_extra_scripts+="pre:scripts/esp32_full_partition.py"
    fi
    export PLATFORMIO_EXTRA_SCRIPTS="$target_extra_scripts"
  else
    unset MESHCORE_ESP32_FULL_BUILD
    unset MESHCORE_COMPANION_RADIO_FULL
  fi

  print_build_flags "$env_name"
  pio_run_args=(run -e "$pio_env_name")
  if [[ "${PIO_BUILD_JOBS_OVERRIDE:-}" =~ ^[1-9][0-9]*$ ]]; then
    pio_run_args+=(-j "$PIO_BUILD_JOBS_OVERRIDE")
  fi
  if [ "$env_platform" = "ESP32_PLATFORM" ]; then
    # The custom mergebin target depends on firmware.bin, so it compiles and
    # merges in one SCons invocation instead of paying startup twice.
    pio_run_args+=(-t mergebin)
  fi
  pio "${pio_run_args[@]}"
  build_status=$?
  if [ "$build_status" -eq 0 ]; then
    collect_build_artifacts "$env_name" "$env_platform" "$pio_env_name" "$firmware_filename"
    build_status=$?
  fi

  restore_platformio_build_flags "$had_platformio_build_flags" "$original_platformio_build_flags"
  unset MESHCORE_ESP32_FULL_BUILD
  unset MESHCORE_COMPANION_RADIO_FULL
  if [ "$had_platformio_build_unflags" -eq 1 ]; then
    export PLATFORMIO_BUILD_UNFLAGS="$original_platformio_build_unflags"
  else
    unset PLATFORMIO_BUILD_UNFLAGS
  fi
  if [ "$had_platformio_build_src_filter" -eq 1 ]; then
    export PLATFORMIO_BUILD_SRC_FILTER="$original_platformio_build_src_filter"
  else
    unset PLATFORMIO_BUILD_SRC_FILTER
  fi
  if [ "$had_platformio_extra_scripts" -eq 1 ]; then
    export PLATFORMIO_EXTRA_SCRIPTS="$original_platformio_extra_scripts"
  else
    unset PLATFORMIO_EXTRA_SCRIPTS
  fi
  return "$build_status"
}

resolve_matching_firmwares() {
  local envs

  mapfile -t envs < <(get_pio_envs_containing_string "$1")
  if [ ${#envs[@]} -gt 0 ]; then
    printf '%s\n' "${envs[@]}"
  fi
}

resolve_all_firmwares() {
  get_supported_pio_envs
}

is_legacy_companion_femoff_target() {
  case "${1,,}" in
    *companion_radio_*_femoff)
      return 0
      ;;
  esac
  return 1
}

resolve_companion_firmwares() {
  local env_name

  while IFS= read -r env_name; do
    if ! is_legacy_companion_femoff_target "$env_name"; then
      printf '%s\n' "$env_name"
    fi
  done < <(get_pio_envs_for_variant_role companion)
}

resolve_all_companion_firmwares() {
  # Corrective/replacement releases must cover every published Companion
  # artifact, including generated Full Companion aliases and the legacy
  # _femoff variants omitted from the canonical day-to-day bulk command.
  get_pio_envs_for_variant_role companion
}

resolve_full_companion_firmwares() {
  local env_name

  for env_name in "${SUPPORTED_PIO_ENVS[@]}"; do
    if is_supported_build_env "$env_name" \
        && is_companion_radio_full_target "$env_name"; then
      if is_legacy_companion_femoff_target "$env_name"; then
        continue
      fi
      printf '%s\n' "$env_name"
    fi
  done
}

resolve_repeater_firmwares() {
  get_pio_envs_for_variant_role repeater
}

resolve_room_server_firmwares() {
  get_pio_envs_for_variant_role room_server
}

resolve_sensor_firmwares() {
  get_pio_envs_for_variant_role sensor
}

resolve_kiss_radio_firmwares() {
  get_pio_envs_for_variant_role kiss
}

resolve_full_esp32_firmwares() {
  local env_name

  for env_name in "${SUPPORTED_PIO_ENVS[@]}"; do
    if supports_esp32_full_build "$env_name"; then
      printf '%s\n' "$env_name"
    fi
  done
}

# Keep bulk build command names mapped to their target resolvers in one place.
get_bulk_build_resolver_name() {
  case "$1" in
    build-firmwares)
      echo "resolve_all_firmwares"
      ;;
    build-firmwares-logging-matrix)
      echo "resolve_all_firmwares"
      ;;
    build-companion-firmwares-logging-matrix)
      echo "resolve_all_companion_firmwares"
      ;;
    build-full-esp32-firmwares)
      echo "resolve_full_esp32_firmwares"
      ;;
    build-full-esp32-logging-firmwares)
      echo "resolve_full_esp32_firmwares"
      ;;
    build-companion-firmwares)
      echo "resolve_companion_firmwares"
      ;;
    build-full-companion-firmwares)
      echo "resolve_full_companion_firmwares"
      ;;
    build-repeater-firmwares)
      echo "resolve_repeater_firmwares"
      ;;
    build-room-server-firmwares)
      echo "resolve_room_server_firmwares"
      ;;
    build-sensor-firmwares)
      echo "resolve_sensor_firmwares"
      ;;
    build-kiss-radio-firmwares)
      echo "resolve_kiss_radio_firmwares"
      ;;
    *)
      return 1
      ;;
  esac
}

is_bulk_build_command() {
  get_bulk_build_resolver_name "$1" >/dev/null
}

is_build_command() {
  case "$1" in
    build-firmware|build-matching-firmwares)
      return 0
      ;;
    *)
      is_bulk_build_command "$1"
      ;;
  esac
}

resolve_bulk_command_targets() {
  local resolver_name

  resolver_name=$(get_bulk_build_resolver_name "$1") || return $?
  mapfile -t RESOLVED_BUILD_TARGETS < <("$resolver_name")
}

get_build_platform_sort_rank() {
  case "$1" in
    NRF52_PLATFORM)
      echo 10
      ;;
    ESP32_PLATFORM)
      echo 20
      ;;
    RP2040_PLATFORM)
      echo 30
      ;;
    STM32_PLATFORM)
      echo 40
      ;;
    *)
      echo 90
      ;;
  esac
}

sort_build_targets_by_platform_and_name() {
  local env_name
  local env_platform
  local platform_rank

  for env_name in "$@"; do
    env_platform=$(get_platform_for_env "$env_name")
    platform_rank=$(get_build_platform_sort_rank "$env_platform")
    printf '%s\t%s\n' "$platform_rank" "$env_name"
  done \
    | LC_ALL=C sort -t $'\t' -k1,1n -k2,2f -k2,2 \
    | while IFS=$'\t' read -r platform_rank env_name; do
        printf '%s\n' "$env_name"
      done
}

validate_build_target() {
  local env_name=$1
  local env_platform

  if ! is_known_pio_env "$env_name"; then
    echo "Unknown build target: $env_name"
    return 1
  fi

  env_platform=$(get_platform_for_env "$env_name")
  if ! is_supported_platform "$env_platform"; then
    echo "Unsupported build target: $env_name"
    return 1
  fi
}

resolve_command_targets() {
  local target

  RESOLVED_BUILD_TARGETS=()
  case "$1" in
    build-firmware)
      for target in "${@:2}"; do
        validate_build_target "$target" || return $?
        RESOLVED_BUILD_TARGETS+=("$target")
      done
      ;;
    build-matching-firmwares)
      mapfile -t RESOLVED_BUILD_TARGETS < <(resolve_matching_firmwares "$2")
      ;;
    *)
      # Bulk command target resolution is centralized so the build-family
      # command list is not repeated in every command handling case.
      resolve_bulk_command_targets "$1" || return $?
      ;;
  esac

  if [ ${#RESOLVED_BUILD_TARGETS[@]} -eq 0 ]; then
    echo "No supported build targets matched: ${*:2}"
    return 1
  fi

  if is_bulk_build_command "$1" && [ "$1" != "build-kiss-radio-firmwares" ]; then
    prompt_for_kiss_modem_build_policy
    if [ ${#RESOLVED_BUILD_TARGETS[@]} -eq 0 ]; then
      echo "No build targets remain after skipping KISS modem targets."
      return 1
    fi
  fi

  # Keep one queue so parallel workers stay saturated. The scheduler may pull
  # a later target forward when a generated alias shares an active PlatformIO
  # base environment, so this is a best-effort start order rather than a phase
  # barrier or completion-order guarantee.
  if [ "$1" != "build-firmware" ]; then
    mapfile -t RESOLVED_BUILD_TARGETS < <(
      sort_build_targets_by_platform_and_name "${RESOLVED_BUILD_TARGETS[@]}"
    )
    echo "Bulk target start order: nRF52, ESP32, RP2040, STM32; alphabetical within each platform (best effort with parallel workers)."
  fi
}

prepare_output_dir() {
  local output_dir="$OUTPUT_DIR"

  if [ -z "$output_dir" ] || [ "$output_dir" == "/" ] || [ "$output_dir" == "." ]; then
    echo "Refusing to clean unsafe output directory: $output_dir"
    exit 1
  fi

  if [ "$RESUME_BUILD_OUTPUT" == "1" ]; then
    mkdir -p -- "$output_dir"
    echo "Resuming build output in ${output_dir}; existing artifacts will be skipped."
    return 0
  fi

  rm -rf -- "$output_dir"
  mkdir -p -- "$output_dir"
}

run_resolved_build_targets() {
  local targets=("$@")
  local env
  local previous_batch_build_mode=$BATCH_BUILD_MODE
  local build_status=0

  if [ ${#targets[@]} -eq 0 ]; then
    echo "No build targets resolved."
    return 1
  fi

  if [ ${#targets[@]} -gt 1 ]; then
    BATCH_BUILD_MODE=1
  fi
  for env in "${targets[@]}"; do
    build_firmware "$env"
    build_status=$?
    if [ "$build_status" -ne 0 ]; then
      break
    fi
  done
  BATCH_BUILD_MODE=$previous_batch_build_mode

  return "$build_status"
}

terminate_process_tree() {
  local parent_pid=$1
  local child_pid
  local -a child_pids=()

  mapfile -t child_pids < <(pgrep -P "$parent_pid" 2>/dev/null || true)
  for child_pid in "${child_pids[@]}"; do
    terminate_process_tree "$child_pid"
  done
  kill -TERM "$parent_pid" 2>/dev/null || true
}

run_logged_build_targets() {
  local targets=("$@")
  local env profile log_dir log_path log_tmp
  local previous_batch_build_mode=$BATCH_BUILD_MODE
  local build_status=0
  local overall_status=0
  local preserved_log=0
  local worker_limit=${PROFILE_BUILD_WORKERS:-1}
  local pio_job_limit=${OPTION3_PIO_JOBS:-8}
  local next_index=0
  local candidate_index
  local candidate_env
  local candidate_key
  local pid pio_env_key
  local completed_pid
  local running_pid
  local interrupted=0
  local interrupt_pid
  local -a running_pids=()
  local -A active_pio_envs=()
  local -A job_env_by_pid=()
  local -A job_key_by_pid=()
  local -A job_log_by_pid=()
  local -A job_tmp_by_pid=()

  if [ ${#targets[@]} -eq 0 ]; then
    echo "No build targets resolved."
    return 1
  fi
  if ! [[ "$worker_limit" =~ ^[1-9][0-9]*$ ]]; then
    worker_limit=1
  fi
  if ! [[ "$pio_job_limit" =~ ^[1-9][0-9]*$ ]]; then
    pio_job_limit=8
  fi

  if [ -n "$FIRMWARE_FILENAME_INFIX" ]; then
    profile=$FIRMWARE_FILENAME_INFIX
  elif [ "${MQTT_BRIDGE_OVERRIDE,,}" == "on" ]; then
    profile="mqtt"
  else
    profile="standard"
  fi
  log_dir="${OUTPUT_DIR}/build-logs"
  mkdir -p -- "$log_dir" || return 1

  if [ ${#targets[@]} -gt 1 ]; then
    BATCH_BUILD_MODE=1
  fi

  if [ "$worker_limit" -le 1 ]; then
    for env in "${targets[@]}"; do
      log_path="${log_dir}/${env}-${profile}.log"
      log_tmp="${log_path}.tmp"
      preserved_log=0
      echo "Building ${env} (${profile}); log: ${log_path}"
      build_firmware "$env" > "$log_tmp" 2>&1
      build_status=$?
      if [ "$build_status" -eq 0 ] \
          && grep -q "^Skipping ${env}; existing artifacts found" "$log_tmp" \
          && [ -s "$log_path" ]; then
        rm -f -- "$log_tmp"
        preserved_log=1
      else
        mv -f -- "$log_tmp" "$log_path"
      fi
      if [ "$build_status" -ne 0 ]; then
        overall_status=1
        LOGGING_MATRIX_FAILURES+=("${env} (${profile}) -> ${log_path}")
        echo "FAILED: ${env} (${profile}), status ${build_status}"
        echo "FAILED: ${env} (${profile}), status ${build_status}" >> "$log_path"
      else
        echo "SUCCEEDED: ${env} (${profile})"
        if [ "$preserved_log" -eq 0 ]; then
          echo "SUCCEEDED: ${env} (${profile})" >> "$log_path"
        fi
      fi
    done
    BATCH_BUILD_MODE=$previous_batch_build_mode
    return "$overall_status"
  fi

  echo "Running up to ${worker_limit} target builds concurrently with ${pio_job_limit} PlatformIO jobs each."
  mkdir -p -- ".pio/build-option3" || return 1
  trap 'interrupted=1; for interrupt_pid in "${running_pids[@]}"; do terminate_process_tree "$interrupt_pid"; done' INT TERM

  while [ "$next_index" -lt "${#targets[@]}" ] || [ ${#running_pids[@]} -gt 0 ]; do
    while [ "$next_index" -lt "${#targets[@]}" ] \
        && [ ${#running_pids[@]} -lt "$worker_limit" ]; do
      candidate_index=$next_index
      candidate_env=""
      candidate_key=""
      while [ "$candidate_index" -lt "${#targets[@]}" ]; do
        candidate_env=${targets[$candidate_index]}
        candidate_key=$(get_pio_build_env "$candidate_env")
        if [ -z "${active_pio_envs[$candidate_key]+x}" ]; then
          break
        fi
        candidate_index=$((candidate_index + 1))
      done
      if [ "$candidate_index" -ge "${#targets[@]}" ]; then
        break
      fi

      # Pull an available environment forward so a generated alias waiting on
      # its base environment does not leave another worker idle.
      if [ "$candidate_index" -ne "$next_index" ]; then
        targets[candidate_index]=${targets[next_index]}
        targets[next_index]=$candidate_env
      fi
      env=$candidate_env
      pio_env_key=$candidate_key
      log_path="${log_dir}/${env}-${profile}.log"
      log_tmp="${log_path}.tmp"
      echo "Building ${env} (${profile}); log: ${log_path}"
      (
        BATCH_BUILD_MODE=1
        PIO_BUILD_JOBS_OVERRIDE=$pio_job_limit
        PIO_BUILD_DIR_OVERRIDE=".pio/build-option3/${pio_env_key}"
        export PLATFORMIO_BUILD_DIR="$PIO_BUILD_DIR_OVERRIDE"
        build_firmware "$env"
      ) > "$log_tmp" 2>&1 &
      pid=$!
      running_pids+=("$pid")
      active_pio_envs["$pio_env_key"]=1
      job_env_by_pid["$pid"]=$env
      job_key_by_pid["$pid"]=$pio_env_key
      job_log_by_pid["$pid"]=$log_path
      job_tmp_by_pid["$pid"]=$log_tmp
      next_index=$((next_index + 1))
    done

    if [ ${#running_pids[@]} -eq 0 ]; then
      echo "Unable to schedule remaining ${profile} targets."
      overall_status=1
      break
    fi

    completed_pid=""
    while [ -z "$completed_pid" ]; do
      for running_pid in "${running_pids[@]}"; do
        if ! kill -0 "$running_pid" 2>/dev/null; then
          completed_pid=$running_pid
          break
        fi
      done
      if [ -z "$completed_pid" ]; then
        sleep 0.1
      fi
    done
    if wait "$completed_pid"; then
      build_status=0
    else
      build_status=$?
    fi
    if [ "$interrupted" -eq 1 ]; then
      for interrupt_pid in "${running_pids[@]}"; do
        wait "$interrupt_pid" 2>/dev/null || true
      done
      BATCH_BUILD_MODE=$previous_batch_build_mode
      trap - INT TERM
      echo "Interrupted ${profile} profile builds."
      return 130
    fi
    pid=$completed_pid
    env=${job_env_by_pid[$pid]}
    pio_env_key=${job_key_by_pid[$pid]}
    log_path=${job_log_by_pid[$pid]}
    log_tmp=${job_tmp_by_pid[$pid]}
    preserved_log=0
    if [ "$build_status" -eq 0 ] \
        && grep -q "^Skipping ${env}; existing artifacts found" "$log_tmp" \
        && [ -s "$log_path" ]; then
      rm -f -- "$log_tmp"
      preserved_log=1
    else
      mv -f -- "$log_tmp" "$log_path"
    fi
    if [ "$build_status" -ne 0 ]; then
      overall_status=1
      LOGGING_MATRIX_FAILURES+=("${env} (${profile}) -> ${log_path}")
      echo "FAILED: ${env} (${profile}), status ${build_status}"
      echo "FAILED: ${env} (${profile}), status ${build_status}" >> "$log_path"
    else
      echo "SUCCEEDED: ${env} (${profile})"
      if [ "$preserved_log" -eq 0 ]; then
        echo "SUCCEEDED: ${env} (${profile})" >> "$log_path"
      fi
    fi

    unset 'active_pio_envs[$pio_env_key]'
    unset 'job_env_by_pid[$pid]' 'job_key_by_pid[$pid]'
    unset 'job_log_by_pid[$pid]' 'job_tmp_by_pid[$pid]'
    local -a remaining_pids=()
    for running_pid in "${running_pids[@]}"; do
      if [ "$running_pid" != "$pid" ]; then
        remaining_pids+=("$running_pid")
      fi
    done
    running_pids=("${remaining_pids[@]}")
  done
  BATCH_BUILD_MODE=$previous_batch_build_mode
  trap - INT TERM

  return "$overall_status"
}

run_full_esp32_profile() {
  local profile_label=$1
  local logging_mode=$2
  shift 2
  local targets=("$@")
  local target
  local full_target
  local full_targets=()
  local -A seen_full_targets=()
  local original_meshdebug_override=$MESHDEBUG_OVERRIDE
  local original_packet_logging_override=$PACKET_LOGGING_OVERRIDE
  local original_mqtt_bridge_override=$MQTT_BRIDGE_OVERRIDE
  local original_mqtt_debug_override=$MQTT_DEBUG_OVERRIDE
  local original_firmware_filename_infix=$FIRMWARE_FILENAME_INFIX
  local original_esp32_full_build=$ESP32_FULL_BUILD
  local build_status=0
  local pass_status=0

  for target in "${targets[@]}"; do
    full_target=""
    if [ "$logging_mode" = "on" ]; then
      full_target=$(get_mqtt_disabled_target "$target") || full_target=""
    else
      full_target=$(get_mqtt_enabled_target "$target") || full_target=""
    fi
    if [ -z "$full_target" ] || ! supports_esp32_full_build "$full_target"; then
      continue
    fi
    if [ -z "${seen_full_targets[$full_target]+x}" ]; then
      full_targets+=("$full_target")
      seen_full_targets["$full_target"]=1
    fi
  done

  if [ ${#full_targets[@]} -eq 0 ]; then
    if [ "$logging_mode" = "on" ]; then
      echo "${profile_label}: no non-MQTT ESP32 FULL targets resolved; skipping."
    else
      echo "${profile_label}: no MQTT ESP32 FULL targets resolved; skipping."
    fi
    return 0
  fi

  if [ "$logging_mode" = "on" ]; then
    echo "${profile_label}: building ${#full_targets[@]} feature-complete ESP32 target(s) with up to ${ESP32_FULL_MAX_NEIGHBOURS} neighbors (target DRAM limits apply), logging on, MQTT off, and expanded dual-OTA partitions."
    echo "FULL logging artifacts exclude MQTT, include LoRa OTA, and use filename form: name-full-logging-ota-version."
    MESHDEBUG_OVERRIDE="on"
    PACKET_LOGGING_OVERRIDE="on"
    MQTT_BRIDGE_OVERRIDE="off"
    FIRMWARE_FILENAME_INFIX="full-logging"
  else
    echo "${profile_label}: building ${#full_targets[@]} feature-complete ESP32 MQTT target(s) with up to ${ESP32_FULL_MAX_NEIGHBOURS} neighbors (target DRAM limits apply), logging off, and expanded dual-OTA partitions."
    echo "FULL artifacts include MQTT and LoRa OTA and use filename form: name-full-ota-version."
    MESHDEBUG_OVERRIDE="off"
    PACKET_LOGGING_OVERRIDE="off"
    MQTT_BRIDGE_OVERRIDE="on"
    FIRMWARE_FILENAME_INFIX="full"
  fi
  echo "Flash the matching merged image once to install the expanded partition table."
  MQTT_DEBUG_OVERRIDE="off"
  ESP32_FULL_BUILD=1

  run_logged_build_targets "${full_targets[@]}"
  pass_status=$?
  if [ "$pass_status" -ne 0 ]; then build_status=1; fi

  MESHDEBUG_OVERRIDE=$original_meshdebug_override
  PACKET_LOGGING_OVERRIDE=$original_packet_logging_override
  MQTT_BRIDGE_OVERRIDE=$original_mqtt_bridge_override
  MQTT_DEBUG_OVERRIDE=$original_mqtt_debug_override
  FIRMWARE_FILENAME_INFIX=$original_firmware_filename_infix
  ESP32_FULL_BUILD=$original_esp32_full_build

  return "$build_status"
}

run_full_esp32_build_targets() {
  local logging_mode=$1
  shift
  local targets=("$@")
  local profile_name="FULL MQTT"
  local build_status=0

  if [ "$logging_mode" = "on" ]; then
    profile_name="FULL logging"
  fi

  LOGGING_MATRIX_FAILURES=()
  run_full_esp32_profile "${profile_name}-only build" "$logging_mode" "${targets[@]}"
  build_status=$?

  if [ ${#LOGGING_MATRIX_FAILURES[@]} -gt 0 ]; then
    echo "${profile_name}-only build completed with ${#LOGGING_MATRIX_FAILURES[@]} failed build(s):"
    printf '  %s\n' "${LOGGING_MATRIX_FAILURES[@]}"
  elif [ "$build_status" -ne 0 ]; then
    echo "${profile_name}-only build completed with an output/logging error; inspect ${OUTPUT_DIR}/build-logs/."
  else
    echo "${profile_name}-only build completed successfully. Per-target logs are in ${OUTPUT_DIR}/build-logs/."
  fi

  return "$build_status"
}

run_logging_matrix_build_targets() {
  local targets=("$@")
  local target
  local standard_targets=()
  local logging_targets=()
  local constrained_logging_targets=()
  local mqtt_targets=()
  local original_meshdebug_override=$MESHDEBUG_OVERRIDE
  local original_packet_logging_override=$PACKET_LOGGING_OVERRIDE
  local original_mqtt_bridge_override=$MQTT_BRIDGE_OVERRIDE
  local original_mqtt_debug_override=$MQTT_DEBUG_OVERRIDE
  local original_firmware_filename_infix=$FIRMWARE_FILENAME_INFIX
  local original_esp32_full_build=$ESP32_FULL_BUILD
  local original_profile_build_workers=$PROFILE_BUILD_WORKERS
  local bluetooth_skip_count=0
  local lora_ota_only_skip_count=0
  local logging_target_count=0
  local build_status=0
  local pass_status=0

  if [ ${#targets[@]} -eq 0 ]; then
    echo "No build targets resolved."
    return 1
  fi
  LOGGING_MATRIX_FAILURES=()
  PROFILE_BUILD_WORKERS=$OPTION3_BUILD_WORKERS
  echo "Option 3 parallelism: ${PROFILE_BUILD_WORKERS} target build(s), ${OPTION3_PIO_JOBS} PlatformIO job(s) per target."

  for target in "${targets[@]}"; do
    if is_mqtt_bridge_target "$target"; then
      mqtt_targets+=("$target")
    else
      standard_targets+=("$target")
    fi
  done

  echo "Profile 1/5: building ${#standard_targets[@]} standard target(s) with logging off and MQTT bridge off."
  ESP32_FULL_BUILD=0
  MESHDEBUG_OVERRIDE="off"
  PACKET_LOGGING_OVERRIDE="off"
  MQTT_BRIDGE_OVERRIDE="off"
  FIRMWARE_FILENAME_INFIX=""
  if [ ${#standard_targets[@]} -gt 0 ]; then
    run_logged_build_targets "${standard_targets[@]}"
    pass_status=$?
    if [ "$pass_status" -ne 0 ]; then build_status=1; fi
  fi

  mapfile -t logging_targets < <(filter_out_bluetooth_targets "${standard_targets[@]}")
  bluetooth_skip_count=$((${#standard_targets[@]} - ${#logging_targets[@]}))

  if [ "$bluetooth_skip_count" -gt 0 ]; then
    echo "Skipping ${bluetooth_skip_count} Bluetooth target(s) for logging-on pass."
  fi

  lora_ota_only_skip_count=0
  for target in "${logging_targets[@]}"; do
    if is_lora_ota_only_target "$target"; then
      lora_ota_only_skip_count=$((lora_ota_only_skip_count + 1))
    fi
  done
  mapfile -t logging_targets < <(filter_out_lora_ota_only_targets "${logging_targets[@]}")

  if [ "$lora_ota_only_skip_count" -gt 0 ]; then
    echo "Skipping ${lora_ota_only_skip_count} LoRa-OTA-only target(s) for logging-on pass because logging disables LoRa OTA."
  fi

  for target in "${logging_targets[@]}"; do
    if is_logging_size_constrained_target "$target"; then
      constrained_logging_targets+=("$target")
    fi
  done
  mapfile -t logging_targets < <(filter_out_logging_size_constrained_targets "${logging_targets[@]}")
  logging_target_count=$((${#logging_targets[@]} + ${#constrained_logging_targets[@]}))

  if [ "$logging_target_count" -gt 0 ]; then
    echo "Profile 2/5: building ${logging_target_count} standard target(s) with logging on and MQTT bridge off."
    echo "Logging-on artifacts use filename form: name-logging-version"
  else
    echo "No non-Bluetooth targets remain for logging-on pass."
  fi

  if [ ${#logging_targets[@]} -gt 0 ]; then
    MESHDEBUG_OVERRIDE="on"
    PACKET_LOGGING_OVERRIDE="on"
    MQTT_BRIDGE_OVERRIDE="off"
    FIRMWARE_FILENAME_INFIX="logging"
    run_logged_build_targets "${logging_targets[@]}"
    pass_status=$?
    if [ "$pass_status" -ne 0 ]; then build_status=1; fi
  fi

  if [ ${#constrained_logging_targets[@]} -gt 0 ]; then
    echo "Building ${#constrained_logging_targets[@]} size-constrained STM32 target(s) with packet logging on and MESH_DEBUG off to fit flash."
    MESHDEBUG_OVERRIDE="off"
    PACKET_LOGGING_OVERRIDE="on"
    MQTT_BRIDGE_OVERRIDE="off"
    FIRMWARE_FILENAME_INFIX="logging"
    run_logged_build_targets "${constrained_logging_targets[@]}"
    pass_status=$?
    if [ "$pass_status" -ne 0 ]; then build_status=1; fi
  fi

  if [ ${#mqtt_targets[@]} -gt 0 ]; then
    echo "Profile 3/5: building ${#mqtt_targets[@]} MQTT bridge target(s) for direct radio-to-MQTT forwarding over WiFi, with logging off."
    MESHDEBUG_OVERRIDE="off"
    PACKET_LOGGING_OVERRIDE="off"
    MQTT_BRIDGE_OVERRIDE="on"
    MQTT_DEBUG_OVERRIDE="off"
    FIRMWARE_FILENAME_INFIX=""
    run_logged_build_targets "${mqtt_targets[@]}"
    pass_status=$?
    if [ "$pass_status" -ne 0 ]; then build_status=1; fi
  else
    echo "No MQTT bridge targets are configured; skipping profile 3/5."
  fi

  run_full_esp32_profile "Profile 4/5" "off" "${targets[@]}"
  pass_status=$?
  if [ "$pass_status" -ne 0 ]; then build_status=1; fi

  run_full_esp32_profile "Profile 5/5" "on" "${targets[@]}"
  pass_status=$?
  if [ "$pass_status" -ne 0 ]; then build_status=1; fi

  MESHDEBUG_OVERRIDE=$original_meshdebug_override
  PACKET_LOGGING_OVERRIDE=$original_packet_logging_override
  MQTT_BRIDGE_OVERRIDE=$original_mqtt_bridge_override
  MQTT_DEBUG_OVERRIDE=$original_mqtt_debug_override
  FIRMWARE_FILENAME_INFIX=$original_firmware_filename_infix
  ESP32_FULL_BUILD=$original_esp32_full_build
  PROFILE_BUILD_WORKERS=$original_profile_build_workers

  if [ ${#LOGGING_MATRIX_FAILURES[@]} -gt 0 ]; then
    echo "Logging matrix completed with ${#LOGGING_MATRIX_FAILURES[@]} failed build(s):"
    printf '  %s\n' "${LOGGING_MATRIX_FAILURES[@]}"
  elif [ "$build_status" -ne 0 ]; then
    echo "Logging matrix completed with an output/logging error; inspect ${OUTPUT_DIR}/build-logs/."
  else
    echo "Logging matrix completed successfully. Per-target logs are in ${OUTPUT_DIR}/build-logs/."
  fi

  return "$build_status"
}

validate_command() {
  case "$1" in
    build-firmware)
      if [ "$#" -lt 2 ]; then
        echo "usage: $0 build-firmware <target>"
        exit 1
      fi
      ;;
    build-matching-firmwares)
      if [ -z "${2:-}" ]; then
        echo "usage: $0 build-matching-firmwares <build-match-spec>"
        exit 1
      fi
      ;;
    *)
      # Bulk commands have no required positional arguments; the helper keeps
      # that command set in sync with target resolution.
      if ! is_bulk_build_command "$1"; then
        global_usage
        exit 1
      fi
      ;;
  esac
}

run_command() {
  # All build commands share execution after validation resolves their target list.
  if [ "$SINGLE_TARGET_FULL_BUILD" = "1" ]; then
    run_full_esp32_build_targets "on" "${RESOLVED_BUILD_TARGETS[@]}"
    return $?
  fi

  if is_logging_matrix_command "$1"; then
    run_logging_matrix_build_targets "${RESOLVED_BUILD_TARGETS[@]}"
    return $?
  fi

  if is_full_esp32_command "$1"; then
    run_full_esp32_build_targets "off" "${RESOLVED_BUILD_TARGETS[@]}"
    return $?
  fi

  if is_full_esp32_logging_command "$1"; then
    run_full_esp32_build_targets "on" "${RESOLVED_BUILD_TARGETS[@]}"
    return $?
  fi

  if is_build_command "$1"; then
    run_resolved_build_targets "${RESOLVED_BUILD_TARGETS[@]}"
    return $?
  fi

  global_usage
  exit 1
}

main() {
  if ! parse_cli_options "$@"; then
    exit 1
  fi
  set -- "${PARSED_COMMAND_ARGS[@]}"

  case "${1:-}" in
    help|usage|-h|--help)
      global_usage
      exit 0
      ;;
    list|-l)
      init_project_context
      get_pio_envs
      exit 0
      ;;
    get-companion-firmwares-to-build|get-repeater-firmwares-to-build|get-room-server-firmwares-to-build)
      init_project_context
      print_release_firmware_targets "$1"
      exit $?
      ;;
  esac

  if [ $# -gt 0 ]; then
    validate_command "$@"
  fi

  init_project_context

  if [ -n "$RADIO_PRESET_SELECTION" ]; then
    if ! apply_cli_radio_preset "$RADIO_PRESET_SELECTION"; then
      exit 1
    fi
  fi

  if [ $# -eq 0 ]; then
    if ! [ -t 0 ]; then
      echo "No command provided and no interactive terminal is available."
      global_usage
      exit 1
    fi

    prompt_for_build_mode
    if [ "$SINGLE_TARGET_FULL_BUILD" = "1" ]; then
      echo "Skipping separate debug and MQTT prompts; FULL everything enables logging and explicitly disables MQTT."
    elif is_automatic_profile_command "${SELECTED_COMMAND_ARGS[0]}"; then
      if is_logging_matrix_command "${SELECTED_COMMAND_ARGS[0]}"; then
        echo "Skipping debug and MQTT prompts; this action builds all five profiles automatically."
      elif is_full_esp32_logging_command "${SELECTED_COMMAND_ARGS[0]}"; then
        echo "Skipping debug and MQTT prompts; this action builds only the FULL ESP32 logging profile with MQTT disabled."
      else
        echo "Skipping debug and MQTT prompts; this action builds only the FULL ESP32 MQTT profile."
      fi
    else
      prompt_for_mqtt_bridge_build_setting
      if [ "${MQTT_BRIDGE_OVERRIDE,,}" == "on" ]; then
        MESHDEBUG_OVERRIDE="off"
        PACKET_LOGGING_OVERRIDE="off"
        MQTT_DEBUG_OVERRIDE="off"
        echo "MQTT bridge selected; USB debug and packet logging are disabled."
      else
        prompt_for_debug_build_settings
      fi
    fi
    prompt_for_radio_build_settings
    prompt_for_firmware_profile_settings
    set -- "${SELECTED_COMMAND_ARGS[@]}"
    validate_command "$@"
  fi

  if ! resolve_command_targets "$@"; then
    exit 1
  fi
  if ! is_automatic_profile_command "$1" && [ -n "$MQTT_BRIDGE_OVERRIDE" ]; then
    if ! normalize_resolved_targets_for_mqtt "$1"; then
      exit 1
    fi
  fi

  refresh_firmware_version_tags
  prompt_for_resolved_firmware_version
  if is_automatic_profile_command "$1" \
      || [ "$SINGLE_TARGET_FULL_BUILD" = "1" ]; then
    if is_logging_matrix_command "$1"; then
      prompt_for_logging_matrix_output_policy "resume"
    else
      prompt_for_logging_matrix_output_policy "clean"
    fi
  else
    RESUME_BUILD_OUTPUT=0
  fi
  prepare_output_dir
  run_command "$@"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
