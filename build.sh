#!/usr/bin/env bash

ALL_PIO_ENVS=()
SUPPORTED_PIO_ENVS=()
declare -A PIO_ENV_PLATFORM_BY_NAME=()
declare -A PIO_ENV_MQTT_BY_NAME=()
declare -A PIO_ENV_OTA_BY_NAME=()
PIO_CONFIG_JSON=""
MENU_CHOICE=""
SELECTED_TARGET=""
SELECTED_COMMAND_ARGS=()
MESHDEBUG_OVERRIDE=""
PACKET_LOGGING_OVERRIDE=""
MQTT_BRIDGE_OVERRIDE=""
MQTT_DEBUG_OVERRIDE=""
FIRMWARE_FILENAME_INFIX=""
RADIO_SETTINGS_API_URL="https://api.meshcore.nz/api/v1/config"
RADIO_SETTING_TITLE=""
RADIO_FREQ_OVERRIDE=""
RADIO_BW_OVERRIDE=""
RADIO_SF_OVERRIDE=""
RADIO_CR_OVERRIDE=""
FIRMWARE_PROFILE_OVERRIDE="${FIRMWARE_PROFILE_OVERRIDE:-}"
BATCH_BUILD_MODE=0
RESOLVED_BUILD_TARGETS=()
RESUME_BUILD_OUTPUT="${RESUME_BUILD_OUTPUT:-0}"
LOGGING_MATRIX_FAILURES=()
RADIO_PRESET_SELECTION=""
KISS_MODE_OVERRIDE=""
PARSED_COMMAND_ARGS=()
FIRMWARE_VERSION_EXPLICIT=0
OUTPUT_POLICY_EXPLICIT=0

ENV_VARIANT_SUFFIX_PATTERN='companion_radio_(wifi_mqtt|serial|wifi|usb|ble)(_ps)?(_fem(on|off))?|companion_radio_ethernet|comp_radio_usb|companion_usb|companion_ble|repeater_bridge_rs232_serial1_lora_ota_no_external_sensors|repeater_bridge_rs232_serial2_lora_ota_no_external_sensors|repeater_bridge_rs232_lora_ota_no_external_sensors|room_server_lora_ota_no_external_sensors|repeater_lora_ota_no_external_sensors|repeater_bridge_rs232_serial1|repeater_bridge_rs232_serial2|repeater_bridge_rs232|repeater_bridge_espnow|repeater_observer_mqtt|repeater_ethernet|room_server_observer_mqtt|room_server_ethernet|terminal_chat|room_server|room_svr|kiss_modem|sensor|repeatr|repeater'
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
OUTPUT_DIR="out"
FALLBACK_VERSION_PREFIX="dev"
FALLBACK_VERSION_DATE_FORMAT='+%Y-%m-%d-%H-%M'

# External programs invoked by this script:
#   bash, cat, cp, date, git, grep, head, mkdir, pio, python3, rm, sed, sort
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
  build-firmwares-logging-matrix: Build all firmwares in three profiles, logging each target under out/build-logs/ and continuing after failures.
  build-matching-firmwares <build-match-spec>: Build all firmwares for build targets containing the string given for <build-match-spec>.
  build-companion-firmwares: Build all companion firmwares for all build targets.
  build-repeater-firmwares: Build all repeater firmwares for all build targets.
  build-room-server-firmwares: Build all chat room server firmwares for all build targets.
  build-sensor-firmwares: Build all sensor firmwares for all build targets.

Options:
  --firmware-version <version>: Firmware version to embed.
  --radio-preset <number>: Use the numbered radio choice from the interactive menu (1 keeps target defaults).
  --profile <default|cascade>: Select the firmware settings profile.
  --skip-kiss|--include-kiss: Exclude or include KISS modem targets in bulk builds.
  --clean|--resume: Clean output or resume existing option 3 artifacts.

Examples:
Build firmware for the "RAK_4631_repeater" device target
$ bash build.sh build-firmware RAK_4631_repeater

Run without arguments to choose an interactive build action/target, debug options, radio settings, firmware profile, and firmware version
$ bash build.sh

Build all firmwares for device targets containing the string "RAK_4631"
$ bash build.sh build-matching-firmwares <build-match-spec>

Build all firmwares in three mutually exclusive profiles: standard, USB logging, and MQTT observer (USB logging off):
$ bash build.sh build-firmwares-logging-matrix

Build all companion firmwares
$ bash build.sh build-companion-firmwares

Build all repeater firmwares
$ bash build.sh build-repeater-firmwares

Build all chat room server firmwares
$ bash build.sh build-room-server-firmwares

Build all sensor firmwares
$ bash build.sh build-sensor-firmwares

Environment Variables:
  FIRMWARE_VERSION=vX.Y.Z: Firmware version to embed in the build output.
                           If not set, build.sh derives a default from the latest matching git tag and appends "-dev".
                           In interactive builds, this value is offered as the editable default.
  DISABLE_DEBUG=1: Disables all debug logging flags (MESH_DEBUG, MESH_PACKET_LOGGING, etc.)
                   If not set, debug flags from variant platformio.ini files are used.
  RESUME_BUILD_OUTPUT=1: For build-firmwares-logging-matrix, preserves out/ and skips
                         targets whose expected output artifacts already exist.

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
    while IFS=$'\t' read -r env_name env_platform env_mqtt env_ota; do
      if [ -z "$env_name" ] || [ -z "$env_platform" ]; then
        continue
      fi
      SUPPORTED_PIO_ENVS+=("$env_name")
      PIO_ENV_PLATFORM_BY_NAME["$env_name"]=$env_platform
      PIO_ENV_MQTT_BY_NAME["$env_name"]=$env_mqtt
      PIO_ENV_OTA_BY_NAME["$env_name"]=$env_ota
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
    platform = None
    for key, value in options:
        values = value if isinstance(value, list) else str(value).split()
        if key == "build_src_filter":
            ota_enabled = any("helpers/ota/" in str(item) for item in values)
            continue
        if key != "build_flags":
            continue
        for flag in values:
            if "WITH_MQTT_BRIDGE" in str(flag):
                mqtt_enabled = True
            if "DISABLE_LORA_OTA" in str(flag):
                ota_disabled = True
            match = pattern.search(str(flag))
            if match and platform is None:
                platform = match.group(0)
    if platform:
        print(f"{env_name}\t{platform}\t{1 if mqtt_enabled else 0}\t{1 if ota_enabled and not ota_disabled else 0}")
' "$SUPPORTED_PLATFORM_PATTERN" <<<"$PIO_CONFIG_JSON"
    )
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
    "Build all firmwares in 3 profiles (logging off, logging on, MQTT bridge with logging off)"
    "Build all repeater firmwares"
    "Build all companion firmwares"
    "Build all chat room server firmwares"
    "Build all sensor firmwares"
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
  [ "$1" == "build-firmwares-logging-matrix" ]
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
    "Cascade: path.hash.mode=2 / loop.detect=minimal / rxdelay=2 / agc.reset.interval=8 / advert.interval=0 / flood.advert.interval=83 / multi.acks=1 / companion.manual.add=1 / companion.autoadd=0"
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

  for env in "${ALL_PIO_ENVS[@]}"; do
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

  for env in "${ALL_PIO_ENVS[@]}"; do
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
  for env in "${ALL_PIO_ENVS[@]}"; do
    if [[ "$env" == *${1}* ]] && is_supported_build_env "$env"; then
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

get_pio_envs_for_variant_role() {
  local role=$1
  local env
  local variant_name

  for env in "${ALL_PIO_ENVS[@]}"; do
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
    Tiny_Relay_repeater|RAK_3x72_repeater|wio-e5_repeater|wio-e5-repeater_bridge_rs232|wio-e5-mini_repeater|wio-e5-mini_sensor)
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
  local choice file_count file_label

  normalize_resume_build_output

  if [ "$OUTPUT_POLICY_EXPLICIT" -eq 1 ]; then
    if [ "$RESUME_BUILD_OUTPUT" == "1" ]; then
      echo "Using --resume for existing option 3 output."
    else
      echo "Using --clean for option 3 output."
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
    if [ "$RESUME_BUILD_OUTPUT" == "1" ]; then
      echo "Resuming previous logging matrix output in ${OUTPUT_DIR} (${file_count} ${file_label})."
    fi
    return 0
  fi

  while true; do
    read -r -p "Output directory '${OUTPUT_DIR}' exists with ${file_count} ${file_label}. Resume previous option 3 progress or clean it? [resume/clean] (default: clean): " choice
    choice=${choice,,}
    if [ -z "$choice" ]; then
      choice="clean"
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

is_mqtt_bridge_target() {
  [ "${PIO_ENV_MQTT_BY_NAME[$1]:-0}" == "1" ]
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
      if is_mqtt_bridge_target "$target"; then
        candidate=$target
      elif is_mqtt_bridge_target "${target}_mqtt"; then
        candidate="${target}_mqtt"
      elif is_mqtt_bridge_target "${target}_observer_mqtt"; then
        candidate="${target}_observer_mqtt"
      fi
    else
      if is_mqtt_bridge_target "$target"; then
        if [[ "$target" == *_observer_mqtt ]]; then
          candidate=${target%_observer_mqtt}
        elif [[ "$target" == *_companion_radio_wifi_mqtt ]]; then
          candidate=${target%_mqtt}
        fi
        if ! is_supported_build_env "$candidate" || is_mqtt_bridge_target "$candidate"; then
          candidate=""
        fi
      else
        candidate=$target
      fi
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

  if is_mqtt_bridge_target "$env_name" || [ "${MQTT_BRIDGE_OVERRIDE,,}" == "on" ]; then
    # MQTT observers already export packet traffic through the bridge. Keep the
    # serial console available for the CLI without compiling a second logging path.
    export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -UMESH_DEBUG -UMESH_PACKET_LOGGING -UMQTT_DEBUG -UMQTT_MEMORY_DEBUG"
  fi
}

is_lora_ota_build() {
  local env_name=$1
  local env_name_lc=${env_name,,}

  if [ "${PIO_ENV_OTA_BY_NAME[$env_name]:-0}" != "1" ]; then
    return 1
  fi

  if [[ "$env_name_lc" == *mqtt* ]] \
      || is_mqtt_bridge_target "$env_name" \
      || [ "${MQTT_BRIDGE_OVERRIDE,,}" == "on" ] \
      || [ "${MESHDEBUG_OVERRIDE,,}" == "on" ] \
      || [ "${PACKET_LOGGING_OVERRIDE,,}" == "on" ] \
      || [ "$FIRMWARE_FILENAME_INFIX" == "logging" ]; then
    return 1
  fi

  # LoRa firmware distribution is a repeater-only feature. MQTT observers use
  # their WiFi manifest updater; companion, room-server, and sensor roles do not
  # advertise or accept firmware over the mesh.
  case "$env_name_lc" in
    *repeater*|*repeatr*) return 0 ;;
    *) return 1 ;;
  esac
}

apply_lora_ota_override() {
  local env_name=$1

  if is_lora_ota_build "$env_name"; then
    export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DENABLE_OTA=1"
  else
    export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -UENABLE_OTA"
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
      export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DCASCADE_PROFILE=1 -DDEFAULT_PATH_HASH_MODE=2 -DDEFAULT_LOOP_DETECT=1 -DDEFAULT_RX_DELAY_BASE=2.0f -DDEFAULT_AGC_RESET_INTERVAL_SECONDS=8 -DDEFAULT_ADVERT_INTERVAL_MINUTES=0 -DDEFAULT_FLOOD_ADVERT_INTERVAL_HOURS=83 -DDEFAULT_MULTI_ACKS=1 -DDEFAULT_MANUAL_ADD_CONTACTS=1 -DDEFAULT_AUTOADD_CONFIG=0"
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

def print_flags(title, flags):
    print(f"  {title}:")
    if not flags:
        print("    (none)")
        return
    for flag in flags:
        print(f"    {flag}")

print_flags("platformio.ini build_flags", config_flags)
print_flags("PLATFORMIO_BUILD_FLAGS", env_flags)
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
  local firmware_filename=$2

  python3 scripts/check_esp32_app_size.py \
    ".pio/build/${env_name}/firmware.bin" \
    ".pio/build/${env_name}/partitions.bin" || return $?
  pio run -t mergebin -e "$env_name" || return $?
  copy_build_output ".pio/build/${env_name}/firmware.bin" "${OUTPUT_DIR}/${firmware_filename}.bin" || return $?
  copy_build_output ".pio/build/${env_name}/firmware-merged.bin" "${OUTPUT_DIR}/${firmware_filename}-merged.bin" || return $?

  # Emit the partition-table signature for OTA partition-compatibility checks.
  # Keyed by env name so the slim-manifest generator can find it; the firmware
  # computes the same signature at runtime from its flashed table. Best-effort:
  # local builds without the script's deps just skip it.
  if [ -f ".pio/build/${env_name}/partitions.bin" ]; then
    python3 scripts/partition_signature.py ".pio/build/${env_name}/partitions.bin" > "${OUTPUT_DIR}/${env_name}.partsig" 2>/dev/null || true
  fi
}

collect_nrf52_artifacts() {
  local env_name=$1
  local firmware_filename=$2

  python3 bin/uf2conv/uf2conv.py ".pio/build/${env_name}/firmware.hex" -c -o ".pio/build/${env_name}/firmware.uf2" -f 0xADA52840 || return $?
  copy_build_output ".pio/build/${env_name}/firmware.uf2" "${OUTPUT_DIR}/${firmware_filename}.uf2" || return $?
  if [ -f ".pio/build/${env_name}/firmware.zip" ]; then
    copy_build_output ".pio/build/${env_name}/firmware.zip" "${OUTPUT_DIR}/${firmware_filename}.zip" || return $?
  fi
}

collect_stm32_artifacts() {
  local env_name=$1
  local firmware_filename=$2

  copy_build_output ".pio/build/${env_name}/firmware.bin" "${OUTPUT_DIR}/${firmware_filename}.bin" || return $?
  copy_build_output ".pio/build/${env_name}/firmware.hex" "${OUTPUT_DIR}/${firmware_filename}.hex" || return $?
}

collect_rp2040_artifacts() {
  local env_name=$1
  local firmware_filename=$2

  copy_build_output ".pio/build/${env_name}/firmware.bin" "${OUTPUT_DIR}/${firmware_filename}.bin" || return $?
  copy_build_output ".pio/build/${env_name}/firmware.uf2" "${OUTPUT_DIR}/${firmware_filename}.uf2" || return $?
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
  local firmware_filename=$3

  # Post-build outputs differ by platform, so dispatch to the matching
  # collector after the main firmware build succeeds.
  case "$env_platform" in
    ESP32_PLATFORM)
      collect_esp32_artifacts "$env_name" "$firmware_filename"
      ;;
    NRF52_PLATFORM)
      collect_nrf52_artifacts "$env_name" "$firmware_filename"
      ;;
    STM32_PLATFORM)
      collect_stm32_artifacts "$env_name" "$firmware_filename"
      ;;
    RP2040_PLATFORM)
      collect_rp2040_artifacts "$env_name" "$firmware_filename"
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
  # the PlatformIO environment name or the stable MOTA target identity.
  if [ -z "$filename_infix" ] && is_lora_ota_build "$env_name"; then
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
  local had_platformio_build_flags=0
  local build_status

  env_platform=$(get_platform_for_env "$env_name")
  if ! is_supported_platform "$env_platform"; then
    echo "Unsupported or unknown platform for env: $env_name"
    return 1
  fi

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
  # OTA_VARIANT is the env name — it selects the slim per-variant manifest
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

  export PLATFORMIO_BUILD_FLAGS="${original_platformio_build_flags} -DFIRMWARE_BUILD_DATE='\"${firmware_build_date}\"' -DFIRMWARE_BUILD_EPOCH=${firmware_build_epoch} -DFIRMWARE_VERSION='\"${embedded_version_string}\"' -DOTA_VARIANT='\"${env_name}\"'${mota_target_flag}"
  disable_debug_flags
  apply_debug_overrides
  apply_mqtt_bridge_override
  disable_usb_logging_for_mqtt "$env_name"
  apply_lora_ota_override "$env_name"
  apply_radio_overrides
  apply_firmware_profile_overrides

  print_build_flags "$env_name"
  pio run -e "$env_name"
  build_status=$?
  if [ "$build_status" -eq 0 ]; then
    collect_build_artifacts "$env_name" "$env_platform" "$firmware_filename"
    build_status=$?
  fi

  restore_platformio_build_flags "$had_platformio_build_flags" "$original_platformio_build_flags"
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

resolve_companion_firmwares() {
  get_pio_envs_for_variant_role companion
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

# Keep bulk build command names mapped to their target resolvers in one place.
get_bulk_build_resolver_name() {
  case "$1" in
    build-firmwares)
      echo "resolve_all_firmwares"
      ;;
    build-firmwares-logging-matrix)
      echo "resolve_all_firmwares"
      ;;
    build-companion-firmwares)
      echo "resolve_companion_firmwares"
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

  if is_bulk_build_command "$1"; then
    prompt_for_kiss_modem_build_policy
    if [ ${#RESOLVED_BUILD_TARGETS[@]} -eq 0 ]; then
      echo "No build targets remain after skipping KISS modem targets."
      return 1
    fi
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

run_logged_build_targets() {
  local targets=("$@")
  local env profile log_dir log_path log_tmp
  local previous_batch_build_mode=$BATCH_BUILD_MODE
  local build_status=0
  local overall_status=0
  local preserved_log=0

  if [ ${#targets[@]} -eq 0 ]; then
    echo "No build targets resolved."
    return 1
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

  for target in "${targets[@]}"; do
    if is_mqtt_bridge_target "$target"; then
      mqtt_targets+=("$target")
    else
      standard_targets+=("$target")
    fi
  done

  echo "Profile 1/3: building ${#standard_targets[@]} standard target(s) with logging off and MQTT bridge off."
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
    echo "Profile 2/3: building ${logging_target_count} standard target(s) with logging on and MQTT bridge off."
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
    echo "Profile 3/3: building ${#mqtt_targets[@]} MQTT bridge target(s) for direct radio-to-MQTT forwarding over WiFi, with logging off."
    MESHDEBUG_OVERRIDE="off"
    PACKET_LOGGING_OVERRIDE="off"
    MQTT_BRIDGE_OVERRIDE="on"
    MQTT_DEBUG_OVERRIDE="off"
    FIRMWARE_FILENAME_INFIX=""
    run_logged_build_targets "${mqtt_targets[@]}"
    pass_status=$?
    if [ "$pass_status" -ne 0 ]; then build_status=1; fi
  else
    echo "No MQTT bridge targets are configured; skipping profile 3/3."
  fi

  MESHDEBUG_OVERRIDE=$original_meshdebug_override
  PACKET_LOGGING_OVERRIDE=$original_packet_logging_override
  MQTT_BRIDGE_OVERRIDE=$original_mqtt_bridge_override
  MQTT_DEBUG_OVERRIDE=$original_mqtt_debug_override
  FIRMWARE_FILENAME_INFIX=$original_firmware_filename_infix

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
  if is_logging_matrix_command "$1"; then
    run_logging_matrix_build_targets "${RESOLVED_BUILD_TARGETS[@]}"
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
    if is_logging_matrix_command "${SELECTED_COMMAND_ARGS[0]}"; then
      echo "Skipping debug and MQTT prompts; this action builds all three profiles automatically."
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

  if ! is_logging_matrix_command "$1" && [ -n "$MQTT_BRIDGE_OVERRIDE" ]; then
    if ! normalize_resolved_targets_for_mqtt "$1"; then
      exit 1
    fi
  fi

  prompt_for_resolved_firmware_version
  if is_logging_matrix_command "$1"; then
    prompt_for_logging_matrix_output_policy
  else
    RESUME_BUILD_OUTPUT=0
  fi
  prepare_output_dir
  run_command "$@"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
