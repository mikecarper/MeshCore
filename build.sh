#!/usr/bin/env bash

ALL_PIO_ENVS=()
SUPPORTED_PIO_ENVS=()
declare -A PIO_ENV_PLATFORM_BY_NAME=()
declare -A PIO_ENV_BOARD_BY_NAME=()
declare -A PIO_ENV_MQTT_BY_NAME=()
declare -A PIO_ENV_OTA_BY_NAME=()
declare -A PIO_ENV_SD_OTA_BY_NAME=()
declare -A PIO_ENV_QSPI_OTA_BY_NAME=()
declare -A PIO_ENV_BUILD_BASE_BY_NAME=()
declare -A PIO_ENV_COMPLETE_OTA_BASE_BY_NAME=()
declare -A PIO_ENV_FULL_BUILD_BY_NAME=()
declare -A PIO_ENV_FULL_WIFI_OTA_BY_NAME=()
PIO_CONFIG_JSON=""
MENU_CHOICE=""
SELECTED_TARGET=""
SELECTED_COMMAND_ARGS=()
MESHDEBUG_OVERRIDE="${MESHDEBUG_OVERRIDE-}"
PACKET_LOGGING_OVERRIDE="${PACKET_LOGGING_OVERRIDE-}"
MQTT_BRIDGE_OVERRIDE="${MQTT_BRIDGE_OVERRIDE-}"
MQTT_DEBUG_OVERRIDE="${MQTT_DEBUG_OVERRIDE-}"
FIRMWARE_FILENAME_INFIX=""
ESP32_FULL_BUILD=0
SINGLE_TARGET_FULL_BUILD="${SINGLE_TARGET_FULL_BUILD:-0}"
AUTO_PREFER_FULL_BUILD=0
AUTO_COMPLETE_FIRST_PASS=0
AUTO_REDUCED_FALLBACK_TARGET=""
AUTO_PUBLISH_REDUCED_SECOND_PASS=0
SKIP_DECLARED_REDUCTIONS=0
COMPLETE_OTA_FIRST_PASS=0
FIRMWARE_OUTPUT_ENV_NAME=""
BUILD_PROFILE_OVERRIDE="${BUILD_PROFILE_OVERRIDE:-auto}"
BUILD_PROFILE_EXPLICIT="${BUILD_PROFILE_EXPLICIT:-0}"
BUILD_PROFILE_EFFECTIVE="auto"
RADIO_SETTINGS_API_URL="https://api.meshcore.nz/api/v1/config"
USA_CASCADIA_RADIO_TITLE="USA Cascadia"
USA_CASCADIA_FALLBACK_FREQ="910.525"
USA_CASCADIA_FALLBACK_BW="62.5"
USA_CASCADIA_FALLBACK_SF="7"
USA_CASCADIA_FALLBACK_CR="5"
RADIO_SETTING_TITLE="${RADIO_SETTING_TITLE-$USA_CASCADIA_RADIO_TITLE}"
RADIO_FREQ_OVERRIDE="${RADIO_FREQ_OVERRIDE-$USA_CASCADIA_FALLBACK_FREQ}"
RADIO_BW_OVERRIDE="${RADIO_BW_OVERRIDE-$USA_CASCADIA_FALLBACK_BW}"
RADIO_SF_OVERRIDE="${RADIO_SF_OVERRIDE-$USA_CASCADIA_FALLBACK_SF}"
RADIO_CR_OVERRIDE="${RADIO_CR_OVERRIDE-$USA_CASCADIA_FALLBACK_CR}"
FIRMWARE_PROFILE_OVERRIDE="${FIRMWARE_PROFILE_OVERRIDE-cascade}"
BATCH_BUILD_MODE=0
# PlatformIO shares and cleans .pio/build across environments in this checkout.
# Keep target-level builds strictly single-process; OPTION3_PIO_JOBS only
# controls compiler parallelism inside that one PlatformIO process.
OPTION3_BUILD_WORKERS=1
OPTION3_PIO_JOBS="${OPTION3_PIO_JOBS:-8}"
PROFILE_BUILD_WORKERS=1
PIO_BUILD_JOBS_OVERRIDE=""
PIO_BUILD_DIR_OVERRIDE=""
RESOLVED_BUILD_TARGETS=()
RESUME_BUILD_OUTPUT="${RESUME_BUILD_OUTPUT:-0}"
REQUIRE_OTA_UPDATES="${REQUIRE_OTA_UPDATES:-}"
OTA_EXCLUDED_TARGETS=()
LOGGING_MATRIX_FAILURES=()
LOGGING_MATRIX_DEFERRED_TARGETS=()
RADIO_PRESET_SELECTION=""
KISS_MODE_OVERRIDE="${KISS_MODE_OVERRIDE-}"
PARSED_COMMAND_ARGS=()
FIRMWARE_VERSION_EXPLICIT="${FIRMWARE_VERSION_EXPLICIT:-0}"
OUTPUT_POLICY_EXPLICIT="${OUTPUT_POLICY_EXPLICIT:-0}"
BUILD_BACKGROUND_REQUESTED=0
BUILD_BACKGROUND_EXPLICIT=0
BUILD_BACKGROUND_ACTIVE="${BUILD_BACKGROUND_ACTIVE:-0}"
BUILD_BACKGROUND_CONFIG_RESOLVED="${BUILD_BACKGROUND_CONFIG_RESOLVED:-0}"
BUILD_BACKGROUND_JOB_ID="${BUILD_BACKGROUND_JOB_ID-}"
BUILD_BACKGROUND_DIR="${BUILD_BACKGROUND_DIR-}"
BUILD_BACKGROUND_BACKEND="${BUILD_BACKGROUND_BACKEND-}"
BUILD_BACKGROUND_LOG_FILE=""
BUILD_BACKGROUND_STATUS_FILE=""
BUILD_BACKGROUND_STARTED_AT=""
BUILD_BACKGROUND_HANDOFF_STATE=""
BUILD_SCRIPT_LOCK_FD=""
INTERACTIVE_BUILD_SELECTION=0

ENV_VARIANT_SUFFIX_PATTERN='companion_radio_(wifi_mqtt|serial|wifi|usb|ble|full)(_ps)?(_fem(on|off))?|companion_radio_ethernet|comp_radio_usb|companion_usb|companion_ble|repeater_bridge_rs232_serial1_lora_ota_no_external_sensors|repeater_bridge_rs232_serial2_lora_ota_no_external_sensors|repeater_bridge_rs232_lora_ota_no_external_sensors|repeater_rak13302_w25q16_lora_ota|repeater_rak15001_slot_c_lora_ota|repeater_w25q16_lora_ota|repeater_lora_ota_no_external_sensors|repeater_bridge_rs232_serial1|repeater_bridge_rs232_serial2|repeater_bridge_rs232|repeater_bridge_espnow|repeater_observer_mqtt|repeater_ethernet|room_server_observer_mqtt|room_server_ethernet|terminal_chat|room_server|room_svr|kiss_modem|sensor|repeatr|repeater'
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
#   bash, cat, cp, date, env, find, flock, git, grep, head, mkdir, mv, pgrep,
#   pio, python3, rm, sed, sleep, sort, systemctl, systemd-run, tee, wc
# Keep this list in sync when adding or removing non-builtin command usage.

global_usage() {
  cat - <<EOF
Usage:
bash build.sh <command> [target] [options]

Commands:
  help|usage|-h|--help: Shows this message.
  list|-l: List firmwares available to build.
  build-firmware <target>: Build the firmware for the given build target.
  build-firmwares: Build canonical firmwares for all targets. Runtime-setting aliases and Terminal Chat targets replaced by Full Companion remain available as explicit builds.
  build-firmwares-logging-matrix: Build canonical standard artifacts with merged runtime USB logging plus unified FULL ESP32 USB+WiFi and FULL fallback profiles, logging each target under out/build-logs/ and continuing after failures. MQTT observers and ESP-NOW bridges always use FULL. KISS, BLE-only Companion, and constrained LoRa-OTA repeater contracts do not gain plaintext USB logging.
  build-companion-firmwares-logging-matrix: Build canonical Companion targets with merged runtime USB logging where the transport is safe, plus applicable MQTT and expanded FULL profiles. Full Companion replaces separate USB, BLE, WiFi, Terminal Chat, and USB-logging artifacts where an exact combined recipe exists.
  build-full-esp32-firmwares: Build feature-complete ESP32 profiles with up to 254 neighbors, USB packet logging, WiFi MQTT where supported, LoRa OTA, and expanded dual-OTA partitions.
  build-full-esp32-logging-firmwares: Build only the FULL USB-logging fallback for targets without a matching WiFi MQTT environment.
  build-matching-firmwares <build-match-spec>: Build all firmwares for build targets containing the string given for <build-match-spec>.
  build-companion-firmwares: Build canonical companion firmwares; legacy setting aliases remain available as direct builds.
  build-full-companion-firmwares: Build canonical full Companion firmwares for supported ESP32 and nRF52 targets.
  build-repeater-firmwares: Build all repeater firmwares with 254 neighbors, except DRAM-limited targets that retain 50.
  build-room-server-firmwares: Build all chat room server firmwares for all build targets.
  build-sensor-firmwares: Build all sensor firmwares for all build targets.
  build-kiss-radio-firmwares: Build all KISS radio firmwares for all build targets.
  get-companion-firmwares-to-build: List canonical attached companion targets for release automation; Full Companion replaces separate transport artifacts where qualified.
  get-repeater-firmwares-to-build: List canonical, specialized external-storage, and deployed-target OTA compatibility repeaters for release automation.
  get-room-server-firmwares-to-build: List standard room-server targets for release automation.

Options:
  --firmware-version <version>: Firmware version to embed.
  --radio-preset <name|number>: Override the USA Cascadia radio default. Stable names are usa-cascadia and target; legacy menu numbers remain accepted.
  --profile <default|cascade>: Override runtime settings embedded in the firmware (not its feature set).
  --build-profile <auto|standard|full>: Select feature/partition policy. Auto first builds complete LoRa-OTA-capable firmware. A measured size failure triggers the reduced recipe; internal-flash nRF52 repeaters also publish the reduced image for extra delta-staging headroom. Standard applies declared portable-image reductions immediately; full requires the expanded ESP32 recipe.
  --auto|--standard|--full: Short forms of --build-profile.
  --skip-kiss|--include-kiss: Exclude (default) or include KISS modem targets in bulk builds.
  --clean|--resume: Clean output or resume existing Option 3/FULL-only artifacts.
  --require-ota: Require a verified wireless self-update path for infrastructure.
                 Default for Option 3; Companion USB updates remain supported.
                 Cable-only infrastructure platforms are reported and omitted.
  --allow-no-ota: Include cable-only development targets instead.
  --background: Run the selected build as a persistent detached job. Its log
                and status live outside OUTPUT_DIR so --clean cannot erase them.

Examples:
Build firmware for the "RAK_4631_repeater" device target. Auto first builds
the complete LoRa OTA recipe, then also publishes the reduced
no-external-sensors repeater because this nRF52 repeater stages deltas in
internal flash.
$ bash build.sh build-firmware RAK_4631_repeater --build-profile auto

Run without arguments to choose an interactive build action/target, an optional
FULL-everything profile for supported ESP32 Option 1 targets, debug options,
radio settings, firmware profile, and firmware version
$ bash build.sh

Builds default to the live USA/Canada preset by name and the Cascade firmware
profile. If the preset service is offline, the radio fallback is 910.525 MHz /
BW62.5 / SF7 / CR5. To intentionally use a target's own defaults instead:
$ bash build.sh build-firmware RAK_4631_repeater --radio-preset target --profile default

Build all firmwares for device targets containing the string "RAK_4631"
$ bash build.sh build-matching-firmwares <build-match-spec>

Build all firmwares in standard, USB logging, feature-complete ESP32 MQTT, and feature-complete ESP32 logging profiles:
$ bash build.sh build-firmwares-logging-matrix

Run that matrix persistently in the background:
$ bash build.sh build-firmwares-logging-matrix --background

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
                           In interactive builds, an existing version detected in
                           OUTPUT_DIR is offered in a use-or-edit menu. If no
                           artifact version is found, the derived version is
                           offered directly as the editable default.
                           A single custom version suffix found in existing OUTPUT_DIR
                           artifacts is carried forward after the new numeric version.
  DISABLE_DEBUG=1: Disables all debug logging flags (MESH_DEBUG, MESH_PACKET_LOGGING, etc.)
                   If not set, debug flags from variant platformio.ini files are used.
  RESUME_BUILD_OUTPUT=1: Preserves out/ and skips targets whose expected output
                         artifacts already exist. Option 3 resumes by default.
  OUTPUT_DIR=path: Writes artifacts outside out/ (useful for isolated test builds).
  OPTION3_PIO_JOBS=8: Compiler jobs inside the single active PlatformIO process.
  BUILD_BACKGROUND_DIR=path: Override the detached-job log/status directory.
                             Defaults to OUTPUT_DIR.background-builds.

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
    while IFS=$'\t' read -r env_name env_platform env_mqtt env_ota env_sd_ota env_qspi_ota env_full env_full_wifi env_board; do
      if [ -z "$env_name" ] || [ -z "$env_platform" ]; then
        continue
      fi
      # Native Windows Python writes CRLF even when its output is consumed by
      # Git Bash. `read` removes LF but leaves CR on the final field, which can
      # make exact board checks (for example heltec-rc32) silently fail.
      env_board=${env_board%$'\r'}
      SUPPORTED_PIO_ENVS+=("$env_name")
      PIO_ENV_PLATFORM_BY_NAME["$env_name"]=$env_platform
      PIO_ENV_BOARD_BY_NAME["$env_name"]=$env_board
      PIO_ENV_MQTT_BY_NAME["$env_name"]=$env_mqtt
      PIO_ENV_OTA_BY_NAME["$env_name"]=$env_ota
      PIO_ENV_SD_OTA_BY_NAME["$env_name"]=$env_sd_ota
      PIO_ENV_QSPI_OTA_BY_NAME["$env_name"]=$env_qspi_ota
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
    qspi_ota = False
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
            if "OTA_QSPI_STORE" in str(flag):
                qspi_ota = True
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
            f"\t{1 if qspi_ota else 0}"
            f"\t{1 if full_enabled else 0}\t{1 if full_wifi_ota else 0}"
            f"\t{board_value}"
        )
' "$SUPPORTED_PLATFORM_PATTERN" <<<"$PIO_CONFIG_JSON"
    )

    # Keep each ordinary repeater environment feature-rich (including external
    # sensors), and expose a separately named no-external-sensors OTA build for
    # ESP32/nRF52 repeaters that need a lean staging profile. These two platforms
    # have a complete apply path; RP2040 and STM32 do not yet have the required
    # bootloader/apply path.
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
      # SolarXiao 30S/33S repeaters already use matched external QSPI staging,
      # so their normal full-sensor image is install-capable. A second lean
      # no-external-sensors target provides no additional OTA capability.
      case "${env_name,,}" in
        solarxiao_30s_repeater|solarxiao_33s_repeater) continue ;;
      esac
      ota_env="${env_name%_}_lora_ota_no_external_sensors"
      PIO_ENV_COMPLETE_OTA_BASE_BY_NAME["$ota_env"]="$env_name"
      if [ -n "${PIO_ENV_PLATFORM_BY_NAME[$ota_env]+x}" ]; then
        continue
      fi
      SUPPORTED_PIO_ENVS+=("$ota_env")
      PIO_ENV_PLATFORM_BY_NAME["$ota_env"]="${PIO_ENV_PLATFORM_BY_NAME[$env_name]}"
      PIO_ENV_BOARD_BY_NAME["$ota_env"]="${PIO_ENV_BOARD_BY_NAME[$env_name]}"
      PIO_ENV_MQTT_BY_NAME["$ota_env"]=0
      PIO_ENV_OTA_BY_NAME["$ota_env"]=1
      PIO_ENV_SD_OTA_BY_NAME["$ota_env"]="${PIO_ENV_SD_OTA_BY_NAME[$env_name]:-0}"
      PIO_ENV_QSPI_OTA_BY_NAME["$ota_env"]="${PIO_ENV_QSPI_OTA_BY_NAME[$env_name]:-0}"
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
      # This FEM-enabled image auto-detects both the GC1109 used by Heltec V4.2
      # and the KCT8103L used by V4.3. Keep both revisions in the generated
      # target and artifact name so users do not mistake it for a V4.0-only
      # build. The slash used in the display label is not valid in a filename.
      if [ "$full_env" = "heltec_v4_companion_radio_full_femon" ]; then
        full_env=heltec_v4_2_v4_3_companion_radio_full_femon
      fi
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
      PIO_ENV_QSPI_OTA_BY_NAME["$full_env"]=0
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
      PIO_ENV_QSPI_OTA_BY_NAME["$full_env"]=0
      PIO_ENV_FULL_BUILD_BY_NAME["$full_env"]=0
      PIO_ENV_FULL_WIFI_OTA_BY_NAME["$full_env"]=0
      PIO_ENV_BUILD_BASE_BY_NAME["$full_env"]="$env_name"
    done

    # Some qualified boards historically published only BLE, or BLE plus USB,
    # even though the same recipe has enough flash and RAM for every Companion
    # transport on that platform. Build these measured profiles from the BLE
    # recipe so the radio, display, GPS, and sensor wiring stays exact. ESP32
    # adds USB and WiFi below; nRF52 adds native USB. Legacy transport names
    # remain explicit-build aliases and canonical releases use the Full target.
    local -a qualified_esp32_full_companion_bases=(
      M5Stack_Unit_C6L_companion_radio_ble
      Heltec_Wireless_Tracker_companion_radio_ble
      LilyGo_T3S3_sx1276_companion_radio_ble
      LilyGo_Tlora_C6_companion_radio_ble_
      Heltec_ct62_companion_radio_ble
      Meshadventurer_sx1262_companion_radio_ble
      Meshadventurer_sx1268_companion_radio_ble
      Heltec_Wireless_Paper_companion_radio_ble
      Heltec_E213_companion_radio_ble
      Meshimi_companion_radio_ble_
      WHY2025_badge_companion_radio_ble_
      Xiao_C6_companion_radio_ble_
      Xiao_S3_companion_radio_ble
      LilyGo_TETH_Elite_sx1262_companion_radio_ble
      LilyGo_T3S3_sx1262_companion_radio_ble
      LilyGo_TDeck_companion_radio_ble
      Ebyte_EoRa-S3_companion_radio_ble
      Tbeam_SX1262_companion_radio_ble
      Tbeam_SX1276_companion_radio_ble
      T_Beam_S3_Supreme_SX1262_companion_radio_ble
      heltec_v4_expansionkit_tft_companion_radio_ble_femon
    )
    # A few older targets predate the companion_radio_<transport> naming
    # convention. Keep their established PlatformIO recipe as the build base,
    # but publish a consistently named Full target. SenseCAP Indicator keeps
    # separate ESP-NOW and LoRa images because those are different physical
    # radio layouts, not transport-only variants of one image.
    local -a qualified_esp32_named_full_companion_specs=(
      'Generic_ESPNOW_comp_radio_usb|Generic_ESPNOW_companion_radio_full'
      'Heltec_E290_companion_usb_ble|Heltec_E290_companion_radio_full'
      'Heltec_T190_companion_radio_usb_ble_|Heltec_T190_companion_radio_full_'
      'SenseCapIndicator-ESPNow_comp_radio_usb|SenseCapIndicator-ESPNow_companion_radio_full'
      'SenseCapIndicator-LoRa_comp_radio_usb_wifi|SenseCapIndicator-LoRa_companion_radio_full'
      'SenseCapIndicator-LoRa-N16R2_comp_radio_usb_wifi|SenseCapIndicator-LoRa-N16R2_companion_radio_full'
    )
    local -a qualified_nrf52_full_companion_bases=(
      GAT562_Mesh_Watch13_companion_radio_ble
      LilyGo_T-Echo-Lite_companion_radio_ble
      LilyGo_T_Impulse_Plus_companion_radio_ble
      WioTrackerL1Eink_companion_radio_ble
    )

    for env_name in "${qualified_esp32_full_companion_bases[@]}"; do
      full_env=${env_name/companion_radio_ble/companion_radio_full}
      if [ -n "${PIO_ENV_PLATFORM_BY_NAME[$full_env]+x}" ]; then
        continue
      fi
      if [ "${PIO_ENV_PLATFORM_BY_NAME[$env_name]:-}" != "ESP32_PLATFORM" ]; then
        echo "Qualified Full Companion base is missing or not ESP32: ${env_name}" >&2
        return 1
      fi
      SUPPORTED_PIO_ENVS+=("$full_env")
      PIO_ENV_PLATFORM_BY_NAME["$full_env"]="ESP32_PLATFORM"
      PIO_ENV_BOARD_BY_NAME["$full_env"]="${PIO_ENV_BOARD_BY_NAME[$env_name]}"
      PIO_ENV_MQTT_BY_NAME["$full_env"]=0
      PIO_ENV_OTA_BY_NAME["$full_env"]=1
      PIO_ENV_SD_OTA_BY_NAME["$full_env"]=0
      PIO_ENV_QSPI_OTA_BY_NAME["$full_env"]=0
      PIO_ENV_FULL_BUILD_BY_NAME["$full_env"]=0
      PIO_ENV_FULL_WIFI_OTA_BY_NAME["$full_env"]=0
      PIO_ENV_BUILD_BASE_BY_NAME["$full_env"]="$env_name"
    done

    local full_spec
    for full_spec in "${qualified_esp32_named_full_companion_specs[@]}"; do
      IFS='|' read -r env_name full_env <<<"$full_spec"
      if [ -n "${PIO_ENV_PLATFORM_BY_NAME[$full_env]+x}" ]; then
        continue
      fi
      if [ "${PIO_ENV_PLATFORM_BY_NAME[$env_name]:-}" != "ESP32_PLATFORM" ]; then
        echo "Qualified Full Companion base is missing or not ESP32: ${env_name}" >&2
        return 1
      fi
      SUPPORTED_PIO_ENVS+=("$full_env")
      PIO_ENV_PLATFORM_BY_NAME["$full_env"]="ESP32_PLATFORM"
      PIO_ENV_BOARD_BY_NAME["$full_env"]="${PIO_ENV_BOARD_BY_NAME[$env_name]}"
      PIO_ENV_MQTT_BY_NAME["$full_env"]=0
      PIO_ENV_OTA_BY_NAME["$full_env"]=1
      PIO_ENV_SD_OTA_BY_NAME["$full_env"]=0
      PIO_ENV_QSPI_OTA_BY_NAME["$full_env"]=0
      PIO_ENV_FULL_BUILD_BY_NAME["$full_env"]=0
      PIO_ENV_FULL_WIFI_OTA_BY_NAME["$full_env"]="${PIO_ENV_FULL_WIFI_OTA_BY_NAME[$env_name]:-0}"
      PIO_ENV_BUILD_BASE_BY_NAME["$full_env"]="$env_name"
    done

    for env_name in "${qualified_nrf52_full_companion_bases[@]}"; do
      full_env=${env_name/companion_radio_ble/companion_radio_full}
      if [ -n "${PIO_ENV_PLATFORM_BY_NAME[$full_env]+x}" ]; then
        continue
      fi
      if [ "${PIO_ENV_PLATFORM_BY_NAME[$env_name]:-}" != "NRF52_PLATFORM" ]; then
        echo "Qualified Full Companion base is missing or not nRF52: ${env_name}" >&2
        return 1
      fi
      SUPPORTED_PIO_ENVS+=("$full_env")
      PIO_ENV_PLATFORM_BY_NAME["$full_env"]="NRF52_PLATFORM"
      PIO_ENV_BOARD_BY_NAME["$full_env"]="${PIO_ENV_BOARD_BY_NAME[$env_name]}"
      PIO_ENV_MQTT_BY_NAME["$full_env"]=0
      PIO_ENV_OTA_BY_NAME["$full_env"]=1
      PIO_ENV_SD_OTA_BY_NAME["$full_env"]=0
      PIO_ENV_QSPI_OTA_BY_NAME["$full_env"]=0
      PIO_ENV_FULL_BUILD_BY_NAME["$full_env"]=0
      PIO_ENV_FULL_WIFI_OTA_BY_NAME["$full_env"]=0
      PIO_ENV_BUILD_BASE_BY_NAME["$full_env"]="$env_name"
    done

    # Full Companion may compile the direct WiFi MQTT bridge as a saved,
    # runtime-optional capability. It is still the canonical all-transport
    # image, not an MQTT-only profile which the global MQTT build selector may
    # replace or discard.
    for env_name in "${SUPPORTED_PIO_ENVS[@]}"; do
      if [ "${PIO_ENV_PLATFORM_BY_NAME[$env_name]:-}" = "ESP32_PLATFORM" ] \
          && is_companion_radio_full_target "$env_name"; then
        PIO_ENV_MQTT_BY_NAME["$env_name"]=0
      fi
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
    "Build all canonical firmwares (legacy setting aliases remain direct-build only)"
    "Build the canonical release matrix with unified FULL ESP32 USB + WiFi output (plus logging fallbacks where WiFi is unavailable)"
    "Build all repeater firmwares"
    "Build canonical companion firmwares (Full replaces separate transports; power saving and FEM/RX gain are runtime configurable)"
    "Build all chat room server firmwares"
    "Build all sensor firmwares"
    "Build FULL ESP32 firmwares (all features, USB logging, WiFi MQTT where available, and LoRa OTA)"
    "Build only FULL ESP32 USB-logging fallbacks for targets without WiFi MQTT"
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

prompt_for_background_build_mode() {
  local options=(
    "Run in foreground"
    "Run persistently in background"
  )

  echo "Select how to run this build:"
  print_numbered_menu "${options[@]}"
  prompt_menu_choice "Execution mode" "${#options[@]}"
  case "$MENU_CHOICE" in
    1)
      BUILD_BACKGROUND_REQUESTED=0
      ;;
    2)
      BUILD_BACKGROUND_REQUESTED=1
      ;;
    QUIT)
      echo "Cancelled."
      exit 1
      ;;
  esac
}

prompt_for_single_target_build_profile() {
  SINGLE_TARGET_FULL_BUILD=0
  BUILD_PROFILE_OVERRIDE="auto"
  BUILD_PROFILE_EXPLICIT=1
  if ! supports_esp32_full_build "$SELECTED_TARGET"; then
    echo "Using the smart auto profile in the target's current partition; expanded FULL is unavailable for this target."
    return 0
  fi

  local options=(
    "Auto (keep target capabilities and enforce the current partition)"
    "Standard portable image (allow documented legacy-slot reductions)"
    "FULL everything (all features, 254 neighbors, USB logging, WiFi MQTT where available, LoRa OTA, expanded dual-OTA partitions)"
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
        BUILD_PROFILE_OVERRIDE="auto"
        echo "Using auto: target capabilities are kept and checked against the current partition."
        return 0
        ;;
      2)
        BUILD_PROFILE_OVERRIDE="standard"
        echo "Using standard: documented portable-image reductions may be applied."
        return 0
        ;;
      3)
        BUILD_PROFILE_OVERRIDE="full"
        SINGLE_TARGET_FULL_BUILD=1
        echo "Using FULL everything: all features, 254 neighbors, USB logging, WiFi MQTT where available, LoRa OTA, and expanded dual-OTA partitions."
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
  local preset_output row title description freq bw sf cr
  local -a preset_rows=()

  case "${selection,,}" in
    usa|usa-canada|usa-cascadia|cascadia)
      resolve_usa_cascadia_radio_default
      return 0
      ;;
    default|target|target-defaults)
      clear_radio_overrides
      echo "Using target default radio settings."
      return 0
      ;;
  esac

  if ! [[ "$selection" =~ ^[0-9]+$ ]] || [ "$selection" -lt 1 ]; then
    echo "Invalid --radio-preset value: ${selection} (use usa-cascadia, target, or a legacy menu number)"
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
  echo "Using legacy numbered radio setting ${selection}: ${RADIO_SETTING_TITLE} (${RADIO_FREQ_OVERRIDE}MHz / SF${RADIO_SF_OVERRIDE} / BW${RADIO_BW_OVERRIDE} / CR${RADIO_CR_OVERRIDE})"
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
      --profile|--settings-profile)
        if [ $# -lt 2 ]; then echo "$1 requires a value"; return 1; fi
        case "${2,,}" in
          default) FIRMWARE_PROFILE_OVERRIDE="" ;;
          cascade) FIRMWARE_PROFILE_OVERRIDE="cascade" ;;
          *) echo "Invalid profile: $2 (use default or cascade)"; return 1 ;;
        esac
        shift 2
        ;;
      --build-profile)
        if [ $# -lt 2 ] || [ -z "$2" ]; then echo "$1 requires a value"; return 1; fi
        case "${2,,}" in
          auto|standard|full) BUILD_PROFILE_OVERRIDE="${2,,}" ;;
          *) echo "Invalid build profile: $2 (use auto, standard, or full)"; return 1 ;;
        esac
        BUILD_PROFILE_EXPLICIT=1
        shift 2
        ;;
      --auto)
        BUILD_PROFILE_OVERRIDE="auto"
        BUILD_PROFILE_EXPLICIT=1
        shift
        ;;
      --standard)
        BUILD_PROFILE_OVERRIDE="standard"
        BUILD_PROFILE_EXPLICIT=1
        shift
        ;;
      --full)
        BUILD_PROFILE_OVERRIDE="full"
        BUILD_PROFILE_EXPLICIT=1
        shift
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
      --background)
        BUILD_BACKGROUND_REQUESTED=1
        BUILD_BACKGROUND_EXPLICIT=1
        shift
        ;;
      --require-ota)
        REQUIRE_OTA_UPDATES=1
        shift
        ;;
      --allow-no-ota)
        REQUIRE_OTA_UPDATES=0
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

is_usa_cascadia_radio_title() {
  local title=${1,,}

  [[ "$title" == usa*canada* ]]
}

set_usa_cascadia_radio_fallback() {
  set_radio_overrides \
    "$USA_CASCADIA_RADIO_TITLE" \
    "$USA_CASCADIA_FALLBACK_FREQ" \
    "$USA_CASCADIA_FALLBACK_BW" \
    "$USA_CASCADIA_FALLBACK_SF" \
    "$USA_CASCADIA_FALLBACK_CR"
}

resolve_usa_cascadia_radio_default() {
  local preset_output row title description freq bw sf cr

  set_usa_cascadia_radio_fallback
  if ! preset_output=$(fetch_suggested_radio_settings) || [ -z "$preset_output" ]; then
    echo "USA Cascadia preset lookup unavailable; using offline fallback (${RADIO_FREQ_OVERRIDE}MHz / SF${RADIO_SF_OVERRIDE} / BW${RADIO_BW_OVERRIDE} / CR${RADIO_CR_OVERRIDE})."
    return 0
  fi

  while IFS= read -r row; do
    [ -n "$row" ] || continue
    IFS=$'\t' read -r title description freq bw sf cr <<< "$row"
    if is_usa_cascadia_radio_title "$title"; then
      set_radio_overrides "$USA_CASCADIA_RADIO_TITLE" "$freq" "$bw" "$sf" "$cr"
      echo "Resolved USA Cascadia by preset name '${title}': ${RADIO_FREQ_OVERRIDE}MHz / SF${RADIO_SF_OVERRIDE} / BW${RADIO_BW_OVERRIDE} / CR${RADIO_CR_OVERRIDE}."
      return 0
    fi
  done <<< "$preset_output"

  echo "USA/Canada preset not found; using offline USA Cascadia fallback (${RADIO_FREQ_OVERRIDE}MHz / SF${RADIO_SF_OVERRIDE} / BW${RADIO_BW_OVERRIDE} / CR${RADIO_CR_OVERRIDE})."
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
  local default_title=$RADIO_SETTING_TITLE
  local default_freq=$RADIO_FREQ_OVERRIDE
  local default_bw=$RADIO_BW_OVERRIDE
  local default_sf=$RADIO_SF_OVERRIDE
  local default_cr=$RADIO_CR_OVERRIDE
  local -a options=(
    "USA Cascadia (default): ${default_freq} MHz / SF${default_sf} / BW${default_bw} / CR${default_cr}"
    "Keep target defaults (no radio override)"
  )
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

  if preset_output=$(fetch_suggested_radio_settings); then
    if [ -n "$preset_output" ]; then
      mapfile -t fetched_preset_rows <<< "$preset_output"
    fi
    for row in "${fetched_preset_rows[@]}"; do
      if [ -z "$row" ]; then
        continue
      fi
      IFS=$'\t' read -r title description freq bw sf cr <<< "$row"
      if is_usa_cascadia_radio_title "$title"; then
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
      set_radio_overrides "$default_title" "$default_freq" "$default_bw" "$default_sf" "$default_cr"
      echo "Using radio setting: ${RADIO_SETTING_TITLE} (${RADIO_FREQ_OVERRIDE}MHz / SF${RADIO_SF_OVERRIDE} / BW${RADIO_BW_OVERRIDE} / CR${RADIO_CR_OVERRIDE})"
      return 0
    fi

    if [ "$choice_index" -eq 2 ]; then
      clear_radio_overrides
      echo "Using target default radio settings."
      return 0
    fi

    if [ "$choice_index" -eq "$custom_index" ]; then
      prompt_for_custom_radio_setting
      echo "Using radio setting: ${RADIO_SETTING_TITLE} (${RADIO_FREQ_OVERRIDE}MHz / SF${RADIO_SF_OVERRIDE} / BW${RADIO_BW_OVERRIDE} / CR${RADIO_CR_OVERRIDE})"
      return 0
    fi

    preset_index=$((choice_index - 3))
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
    "Cascade (default): power saving + RXPS on / WiFi power save=min / path.hash.mode=2 / loop.detect=minimal / cad=on / rxdelay=2 / agc.reset.interval=8 / advert.interval=0 / flood.advert.interval=83 / multi.acks=1 / companion.manual.add=1 / companion.autoadd=0"
    "Keep target defaults"
  )

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
        set_firmware_profile_override "cascade"
        echo "Using firmware profile: Cascade"
        return 0
        ;;
      2)
        clear_firmware_profile_overrides
        echo "Using target default firmware profile settings."
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

extract_firmware_version_from_artifact_filename() {
  local filename=${1##*/}
  local stem
  local version
  local i
  local -a filename_parts=()

  case "$filename" in
    *.capabilities.json)
      stem=${filename%.capabilities.json}
      ;;
    *.bin|*.hex|*.uf2|*.zip)
      stem=${filename%.*}
      stem=${stem%-merged}
      ;;
    *)
      return 1
      ;;
  esac

  # Every collected artifact ends in the source commit. Remove it first so a
  # hyphenated prerelease/custom version can be returned intact.
  if ! [[ "$stem" =~ ^(.+)-[[:xdigit:]]{7,40}$ ]]; then
    return 1
  fi
  stem=${BASH_REMATCH[1]}

  IFS='-' read -r -a filename_parts <<< "$stem"
  for ((i = 0; i < ${#filename_parts[@]}; i++)); do
    if [[ "${filename_parts[$i]}" =~ ^v?[0-9]+(\.[0-9]+){2,}$ ]] \
        || { [ "${filename_parts[$i]}" = "$FALLBACK_VERSION_PREFIX" ] \
          && [ $((i + 1)) -lt ${#filename_parts[@]} ] \
          && [[ "${filename_parts[$((i + 1))]}" =~ ^[0-9]{4}$ ]]; }; then
      local IFS='-'
      version="${filename_parts[*]:$i}"
      printf '%s\n' "$version"
      return 0
    fi
  done

  return 1
}

get_latest_output_firmware_version() {
  local output_dir=${1:-$OUTPUT_DIR}
  local artifact_filename
  local _timestamp
  local version

  if ! [ -d "$output_dir" ]; then
    return 1
  fi

  # Prefer the newest artifact when a resumed/partial build left more than one
  # release in OUTPUT_DIR. Duplicate files from one build all resolve to the
  # same version and are harmless.
  while IFS=$'\t' read -r _timestamp artifact_filename; do
    if version=$(extract_firmware_version_from_artifact_filename "$artifact_filename"); then
      printf '%s\n' "$version"
      return 0
    fi
  done < <(
    find "$output_dir" -maxdepth 1 -type f \
      \( -name '*.capabilities.json' -o -name '*.bin' -o -name '*.hex' \
        -o -name '*.uf2' -o -name '*.zip' \) \
      -printf '%T@\t%f\n' | sort -nr -k1,1
  )

  return 1
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

prompt_to_use_or_edit_output_version() {
  local prompt_label=$1
  local result_var=$2
  local detected_version=$3
  local edited_version
  local options=(
    "Use detected version: ${detected_version}"
    "Edit firmware version"
  )

  echo "Detected firmware version in ${OUTPUT_DIR}: ${detected_version}"
  while true; do
    print_numbered_menu "${options[@]}"
    prompt_menu_choice "Firmware version" "${#options[@]}"
    case "$MENU_CHOICE" in
      1)
        printf -v "$result_var" '%s' "$detected_version"
        echo "Using firmware version: ${detected_version}"
        return 0
        ;;
      2)
        prompt_for_firmware_version \
          "$prompt_label" edited_version "$detected_version"
        printf -v "$result_var" '%s' "$edited_version"
        return 0
        ;;
      QUIT)
        echo "Cancelled."
        exit 1
        ;;
    esac
  done
}

prompt_for_resolved_firmware_version() {
  local prompt_label
  local selected_version=${FIRMWARE_VERSION:-}
  local output_version=""

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

  if [ ${#RESOLVED_BUILD_TARGETS[@]} -eq 1 ]; then
    prompt_label="${RESOLVED_BUILD_TARGETS[0]}"
  else
    prompt_label="${#RESOLVED_BUILD_TARGETS[@]} build targets"
  fi

  if [ -z "$selected_version" ] \
      && output_version=$(get_latest_output_firmware_version "$OUTPUT_DIR"); then
    prompt_to_use_or_edit_output_version \
      "$prompt_label" selected_version "$output_version"
  else
    if [ -z "$selected_version" ]; then
      selected_version=$(derive_default_firmware_version_for_targets "${RESOLVED_BUILD_TARGETS[@]}")
      selected_version=$(apply_output_firmware_version_suffix "$selected_version")
    fi
    prompt_for_firmware_version "$prompt_label" selected_version "$selected_version"
  fi
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
    if is_supported_build_env "$env" \
        && ! is_redundant_bulk_build_target "$env" \
        && [[ "$env" == *${suffix} ]]; then
      printf '%s\n' "$env"
    fi
  done
  shopt -u nocasematch
}

get_deployed_lora_ota_compatibility_targets() {
  # These exact target names have distinct OTA IDs embedded in already
  # deployed RAK4631 Serial1/Serial2 bridge images. The merged repeater is the
  # recommended image for new installs, but it cannot address those nodes.
  # Continue publishing an image bearing each legacy ID unless a future,
  # explicitly compatible protocol migrates the installed identity.
  printf '%s\n' \
    RAK_4631_repeater_bridge_rs232_serial1_lora_ota_no_external_sensors \
    RAK_4631_repeater_bridge_rs232_serial2_lora_ota_no_external_sensors
}

is_deployed_lora_ota_compatibility_target() {
  case "${1,,}" in
    rak_4631_repeater_bridge_rs232_serial1_lora_ota_no_external_sensors|\
    rak_4631_repeater_bridge_rs232_serial2_lora_ota_no_external_sensors)
      return 0
      ;;
  esac
  return 1
}

print_release_firmware_targets() {
  case "$1" in
    get-companion-firmwares-to-build)
      get_pio_envs_ending_with_string "_companion_radio_usb"
      get_pio_envs_ending_with_string "_companion_radio_ble"
      # Full Companion supplies every ordinary attached transport and
      # source-only mOTA in one image. Dual-CDC boards use a separate logging
      # port; single-TTY boards switch that port between Binary Companion and
      # an input-capable plaintext logging terminal.
      local env_name
      for env_name in "${SUPPORTED_PIO_ENVS[@]}"; do
        if is_companion_radio_full_target "$env_name" \
            && ! is_redundant_bulk_build_target "$env_name"; then
          printf '%s\n' "$env_name"
        fi
      done
      ;;
    get-repeater-firmwares-to-build)
      get_pio_envs_ending_with_string "_repeater"
      # These full-sensor targets are distinct hardware/bootloader contracts,
      # not generated lean OTA aliases, so tagged repeater releases must ship
      # them explicitly alongside the canonical standard repeaters.
      if is_supported_build_env "RAK_4631_repeater_rak15001_slot_c_lora_ota"; then
        printf '%s\n' "RAK_4631_repeater_rak15001_slot_c_lora_ota"
      fi
      if is_supported_build_env "RAK_4631_repeater_w25q16_lora_ota"; then
        printf '%s\n' "RAK_4631_repeater_w25q16_lora_ota"
      fi
      if is_supported_build_env "RAK_3401_repeater_rak13302_w25q16_lora_ota"; then
        printf '%s\n' "RAK_3401_repeater_rak13302_w25q16_lora_ota"
      fi
      # Functional consolidation does not change an installed image's mOTA
      # target ID. Keep exact Serial1/Serial2 bridge identities publishable as
      # compatibility assets while recommending the merged image for USB/new
      # installs and hiding these legacy profiles in the firmware picker.
      local compatibility_target
      while IFS= read -r compatibility_target; do
        if is_supported_build_env "$compatibility_target"; then
          printf '%s\n' "$compatibility_target"
        fi
      done < <(get_deployed_lora_ota_compatibility_targets)
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

is_lora_ota_only_target() {
  local target_lc=${1,,}
  [[ "$target_lc" == *lora_ota* ]]
}

is_lora_ota_no_external_sensors_target() {
  local target_lc=${1,,}
  [[ "$target_lc" == *lora_ota_no_external_sensors ]]
}

is_rak_i2c_voltage_monitor_ota_target() {
  local target_lc=${1,,}

  case "$target_lc" in
    rak_3401_*lora_ota_no_external_sensors|rak_4631_*lora_ota_no_external_sensors)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

is_rak_gps_retaining_ota_target() {
  local target_lc=${1,,}

  # Serial1 is the RAK12501 UART on RAK4631. The Serial1 RS-232 bridge owns
  # that port instead, so its reduced profile intentionally uses the INA-only
  # recipe and must not promise or require the WisBlock GPS provider.
  case "$target_lc" in
    rak_3401_repeater_lora_ota_no_external_sensors|\
    rak_4631_repeater_lora_ota_no_external_sensors|\
    rak_4631_repeater_bridge_rs232_serial2_lora_ota_no_external_sensors)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

is_logging_size_constrained_target() {
  case "$1" in
    Tiny_Relay_companion_radio_usb|Tiny_Relay_repeater|RAK_3x72_companion_radio_usb|RAK_3x72_repeater|wio-e5_companion_radio_usb|wio-e5_repeater|wio-e5-repeater_bridge_rs232|wio-e5-mini_companion_radio_usb|wio-e5-mini_repeater|wio-e5-mini_sensor)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
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
    filter_out_kiss_modem_targets
    echo "Skipped ${kiss_count} KISS modem target(s) by default; use --include-kiss to build them."
    return 0
  fi

  while true; do
    read -r -p "KISS modem targets found: ${kiss_count}. Build or skip them? [build/skip] (default: skip): " choice
    choice=${choice,,}
    if [ -z "$choice" ]; then
      choice="skip"
    fi

    case "$choice" in
      build)
        KISS_MODE_OVERRIDE="build"
        echo "Including ${kiss_count} KISS modem target(s)."
        return 0
        ;;
      skip)
        KISS_MODE_OVERRIDE="skip"
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
    if is_companion_radio_full_target "$target"; then
      # Full is already the board's one runtime-configurable transport image,
      # so never swap it for a second artifact or compile a different feature
      # set under the same name. Keep it for MQTT=off; for MQTT=on, keep only
      # recipes which were explicitly qualified with direct MQTT support.
      if [ "${MQTT_BRIDGE_OVERRIDE,,}" != "on" ] \
          || pio_env_option_contains "$(get_pio_build_env "$target")" \
            build_flags "WITH_MQTT_BRIDGE"; then
        candidate=$target
      fi
    elif [ "${MQTT_BRIDGE_OVERRIDE,,}" == "on" ]; then
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
  local env_name=${1:-}
  local usb_logging_undefs="-UMESH_DEBUG -UMESH_PACKET_LOGGING"

  # Full Companion always carries diagnostics behind its saved runtime gate.
  # PlatformIO groups -U flags after -D flags, so emitting these undefines here
  # would override apply_companion_radio_full_profile() regardless of the
  # apparent order in PLATFORMIO_BUILD_FLAGS.
  if [ -n "$env_name" ] && is_companion_radio_full_target "$env_name"; then
    usb_logging_undefs=""
  fi

  if [ "$DISABLE_DEBUG" == "1" ]; then
    export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} ${usb_logging_undefs} -UBLE_DEBUG_LOGGING -UWIFI_DEBUG_LOGGING -UBRIDGE_DEBUG -UGPS_NMEA_DEBUG -UCORE_DEBUG_LEVEL -UESPNOW_DEBUG_LOGGING -UDEBUG_RP2040_WIRE -UDEBUG_RP2040_SPI -UDEBUG_RP2040_CORE -UDEBUG_RP2040_PORT -URADIOLIB_DEBUG_SPI -DCFG_DEBUG=0 -URADIOLIB_DEBUG_BASIC -URADIOLIB_DEBUG_PROTOCOL"
  fi
}

apply_mqtt_bridge_override() {
  local env_name=${1:-}

  # Full Companion is one immutable release artifact whose compiled
  # capabilities are controlled by its qualified board recipe and then
  # enabled or disabled at runtime. A matrix-wide MQTT choice must not mutate
  # that artifact into another binary under the same filename.
  if [ -n "$env_name" ] && is_companion_radio_full_target "$env_name"; then
    return 0
  fi

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
  local env_name=${1:-}
  local preserve_full_companion_logging=0

  if [ -n "$env_name" ] && is_companion_radio_full_target "$env_name"; then
    preserve_full_companion_logging=1
  fi

  case "${MESHDEBUG_OVERRIDE,,}" in
    on)
      export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DMESH_DEBUG=1"
      ;;
    off)
      if [ "$preserve_full_companion_logging" -eq 0 ]; then
        export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -UMESH_DEBUG"
      fi
      ;;
  esac

  case "${PACKET_LOGGING_OVERRIDE,,}" in
    on)
      export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DMESH_PACKET_LOGGING=1"
      ;;
    off)
      if [ "$preserve_full_companion_logging" -eq 0 ]; then
        export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -UMESH_PACKET_LOGGING"
      fi
      ;;
  esac

  case "${MQTT_DEBUG_OVERRIDE,,}" in
    off)
      export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -UMQTT_DEBUG -UMQTT_MEMORY_DEBUG"
      ;;
  esac
}

uses_merged_standard_usb_logging() {
  local env_name=$1

  # These profiles either own Serial for framed traffic, deliberately trade
  # logging for OTA space, or provide their own logging contract. Keep those
  # contracts unchanged.
  if is_kiss_modem_target "$env_name" \
      || is_bluetooth_target "$env_name" \
      || is_lora_ota_only_target "$env_name" \
      || is_mqtt_bridge_target "$env_name" \
      || is_companion_radio_full_target "$env_name"; then
    return 1
  fi

  case "${env_name,,}" in
    *companion*|*comp_radio*|*repeater*|*repeatr*|*room_server*|*room_svr*|\
    *sensor*|*terminal_chat*)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

apply_merged_standard_usb_logging_profile() {
  local env_name=$1

  uses_merged_standard_usb_logging "$env_name" || return 0

  # Explicit diagnostic overrides retain their documented meaning. Canonical
  # builds otherwise compile packet/debug output into the ordinary artifact;
  # get/set usb.logging controls the live Serial stream at runtime.
  if [ "${DISABLE_DEBUG:-0}" = "1" ]; then
    return 0
  fi
  if [ "${PACKET_LOGGING_OVERRIDE,,}" != "off" ]; then
    export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DMESH_PACKET_LOGGING=1 -DMESH_USB_LOGGING_MERGED=1"
  fi
  if [ "${MESHDEBUG_OVERRIDE,,}" != "off" ] \
      && ! is_logging_size_constrained_target "$env_name"; then
    export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DMESH_DEBUG=1"
  fi
}

disable_usb_logging_for_mqtt() {
  local env_name=$1

  # Full Companion always compiles diagnostics behind a saved runtime gate.
  # ESP32 uses one TTY and switches it into an input-capable logging terminal,
  # so plaintext cannot mix with framed traffic. nRF52 retains dedicated CDC 1.
  if is_companion_radio_full_target "$env_name"; then
    return 0
  fi

  # Unified non-companion FULL builds deliberately publish the same radio
  # stream over USB packet logging and direct WiFi MQTT. Full Companion keeps
  # its framed binary serial protocol and must never inherit plaintext logs.
  if [ "$ESP32_FULL_BUILD" = "1" ] \
      && [ "${PACKET_LOGGING_OVERRIDE,,}" = "on" ] \
      && ! is_esp32_companion_build "$env_name"; then
    return 0
  fi

  if is_mqtt_bridge_target "$env_name" || [ "${MQTT_BRIDGE_OVERRIDE,,}" == "on" ]; then
    # Ordinary MQTT observers export packet traffic only through the bridge.
    # Keep their serial console clean unless the explicit unified FULL profile
    # above requested both output paths.
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

requires_esp32_arduino3_framework() {
  local env_name=$1
  local pio_env_name=$2

  # RC32 still uses its board-qualified Arduino 3 package independently of
  # the removed dual-CDC Full profile. ESP32-C6 also inherently uses A3;
  # ordinary ESP32/S3 Full images return to the shared A2 platform.
  case "${env_name,,}:${pio_env_name,,}" in
    heltec_rc32_*:*|*:heltec_rc32_*) return 0 ;;
    *) return 1 ;;
  esac
}

prepare_esp32_arduino3_framework() {
  local env_name=$1
  local pio_env_name=$2
  local arduino3_core_url="https://github.com/espressif/arduino-esp32/releases/download/3.3.11/esp32-core-3.3.11.tar.xz"
  local arduino3_libs_url="https://github.com/espressif/arduino-esp32/releases/download/3.3.11/esp32-core-3.3.11-libs.tar.xz"
  local pio_core_dir="${PLATFORMIO_CORE_DIR:-${HOME}/.platformio}"
  local arduino_manifest="${pio_core_dir}/packages/framework-arduinoespressif32/package.json"

  requires_esp32_arduino3_framework "$env_name" "$pio_env_name" || return 0

  # PlatformIO's standard ESP32 platform and pioarduino publish incompatible
  # Arduino 2.x/3.x cores under the same global package name. PlatformIO may
  # list both versions side by side but pioarduino only resolves the canonical
  # package directory. Replace just that conflicting framework when necessary;
  # do not force-reinstall the platform and all of its toolchains.
  echo "Ensuring Arduino-ESP32 3.3.11 dependencies for ${env_name}..."
  if ! grep -Eq '"version"[[:space:]]*:[[:space:]]*"3\.3\.11"' "$arduino_manifest" 2>/dev/null; then
    pio pkg uninstall --global --tool framework-arduinoespressif32 --no-save || true
    pio pkg install --global --tool "$arduino3_core_url"
  fi
  pio pkg install --global --tool "$arduino3_libs_url"
  pio pkg install -e "$pio_env_name"
}

requires_esp32_companion_full_ota_fallback() {
  # Some non-PSRAM ESP32 companions cannot hold their configured high-capacity contact,
  # channel, and offline-queue tables together with BLE, WiFi, and LoRa OTA in
  # internal DRAM. Keep their ordinary high-capacity image unchanged and emit
  # a separately named FULL OTA image with measured-safe capacities.
  case "${1,,}" in
    heltec_v2_companion_radio_wifi|lilygo_tlora_v2_1_1_6_companion_radio_wifi) return 0 ;;
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

is_room_server_role_target() {
  case "${1,,}" in
    *room_server*|*room_svr*) return 0 ;;
    *) return 1 ;;
  esac
}

is_sensor_role_target() {
  case "${1,,}" in
    *_sensor|*_sensor_) return 0 ;;
    *) return 1 ;;
  esac
}

requires_esp32_field_browser_ota() {
  [ "${PIO_ENV_PLATFORM_BY_NAME[$1]:-}" = "ESP32_PLATFORM" ] \
    && ! is_esp32_companion_build "$1" \
    && { is_repeater_role_target "$1" \
         || is_room_server_role_target "$1" \
         || is_sensor_role_target "$1"; }
}

get_reduced_lora_ota_target() {
  local target=$1
  local candidate

  if is_lora_ota_only_target "$target"; then
    echo "$target"
    return 0
  fi
  if ! is_repeater_role_target "$target"; then
    return 1
  fi

  candidate="${target%_}_lora_ota_no_external_sensors"
  if is_supported_build_env "$candidate"; then
    echo "$candidate"
    return 0
  fi
  return 1
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

  # A single-target auto build promises to preserve the capabilities declared
  # by its resolved PlatformIO environment and fail if they do not fit.  Keep
  # that contract for nRF52 Companion and other non-repeater roles which
  # deliberately inherit the shared OTA recipe.  The standard/release profile
  # continues through the opt-in policy below and may select its documented
  # portable reductions.
  if [ "${BUILD_PROFILE_FOR_TARGET:-$BUILD_PROFILE_EFFECTIVE}" = "auto" ]; then
    return 0
  fi

  # The OTA manager, staging store, and self-install path are deliberately
  # opt-in. Most boards use the constrained no-external-sensors sibling. A
  # purpose-built external-QSPI target may retain the full board feature set.
  if ! is_lora_ota_no_external_sensors_target "$env_name" \
      && [ "${PIO_ENV_QSPI_OTA_BY_NAME[$env_name]:-0}" != "1" ]; then
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

is_companion_build() {
  case "${1,,}" in
    *companion*|*comp_radio*) return 0 ;;
    *) return 1 ;;
  esac
}

is_esp32_companion_build() {
  is_companion_build "$1"
}

requires_esp32_portable_app_slot() {
  [ "${PIO_ENV_PLATFORM_BY_NAME[$1]:-}" = "ESP32_PLATFORM" ] \
    && ! is_esp32_companion_build "$1"
}

requires_esp32_portable_size_ceiling() {
  local env_name=$1

  requires_esp32_portable_app_slot "$env_name" || return 1

  # Arduino 3.x pulls substantially more WiFi runtime into ESP32-C6 and RC32
  # images. These target-specific OTA siblings already use larger A/B slots:
  # C6 boards declared at least 1920 KiB slots, while RC32 has always declared
  # the two 0x640000 slots in default_16MB.csv. Retain those established
  # partition contracts and enforce each image against its actual partition
  # below instead of the unrelated legacy 1.25 MiB ceiling.
  if is_lora_ota_only_target "$env_name"; then
    case "${PIO_ENV_BOARD_BY_NAME[$env_name]:-}" in
      esp32-c6-*|heltec-rc32) return 1 ;;
    esac
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

requires_esp32_full_cli_profile() {
  local env_name=$1

  # MQTT observers and ESP-NOW bridges do not fit their legacy application
  # slots with the complete CLI. Always build those roles with the expanded
  # FULL partition profile so no commands are removed.
  [ "${PIO_ENV_PLATFORM_BY_NAME[$env_name]:-}" = "ESP32_PLATFORM" ] \
    && ! is_esp32_companion_build "$env_name" \
    && { is_mqtt_bridge_target "$env_name" \
         || [[ "${env_name,,}" == *bridge_espnow* ]]; }
}

record_build_capability() {
  BUILD_CAPABILITIES+=("$1")
}

record_build_reduction() {
  BUILD_REDUCTIONS+=("$1")
}

record_build_expectation() {
  BUILD_EXPECTATIONS+=("$1=$2")
}

declare_build_capability_contract() {
  local env_name=$1
  local env_platform=$2
  local env_name_lc=${env_name,,}
  local pio_env_name

  record_build_capability "profile.${BUILD_PROFILE_FOR_TARGET}"

  if [ "$env_platform" = "NRF52_PLATFORM" ] && ! is_kiss_modem_target "$env_name"; then
    # Prove the real DFU service is linked, not merely a generic OTA CLI stub.
    record_build_expectation "ota.update.bluetooth" "_ZN6BLEDfu5beginEv"
  fi

  if [ "$env_platform" = "ESP32_PLATFORM" ]; then
    if is_companion_radio_full_target "$env_name" \
        || { [ "$BUILD_PROFILE_FOR_TARGET" = "standard" ] \
             && requires_esp32_field_browser_ota "$env_name"; }; then
      record_build_expectation "ota.update.wifi" "MeshCore firmware update"
    elif ! is_companion_build "$env_name" \
        && [ "${PIO_ENV_FULL_WIFI_OTA_BY_NAME[$env_name]:-0}" = "1" ] \
        && [ "$BUILD_PROFILE_FOR_TARGET" = "full" ]; then
      record_build_expectation "ota.update.wifi" "Started: http://%s/update"
    fi
    if is_lora_ota_build "$env_name" && ! is_companion_radio_full_target "$env_name"; then
      record_build_expectation "ota.update.lora" "image_hash MISMATCH after decode"
    fi
  fi

  if is_repeater_role_target "$env_name" \
      || is_room_server_role_target "$env_name"; then
    record_build_expectation "cli.retry_preset" "retry.preset"
    if [ "$env_platform" != "STM32_PLATFORM" ]; then
      record_build_expectation "telemetry.history" "telemetry.volt.i2c"
    fi
  fi

  if is_lora_ota_build "$env_name"; then
    record_build_expectation "ota.cli" "OTA: status"
  fi

  # A field-installed ESP32 must never leave the release pipeline with every
  # self-update path compiled out. Standard field/server profiles retain the
  # lightweight browser uploader below; prove that its linked HTML is really
  # present rather than trusting build flags or a generic `start ota` command
  # whose board implementation may still be the unsupported stub.
  if [ "$env_platform" = "ESP32_PLATFORM" ] \
      && [ "$BUILD_PROFILE_FOR_TARGET" = "standard" ] \
      && requires_esp32_field_browser_ota "$env_name"; then
    record_build_expectation \
      "web.lightweight_browser_ota" "MeshCore firmware update"
  fi

  if is_rak_gps_retaining_ota_target "$env_name"; then
    # GPS-compatible reduced RAK OTA profiles retain the WisBlock provider.
    # The Serial1 RS-232 profile is deliberately excluded because that UART
    # cannot simultaneously carry the RAK12501 protocol. This evidence is
    # emitted only by the linked provider; generic CLI text is insufficient.
    record_build_expectation \
      "sensor.gps" "meshcore.capability.rak_wisblock_gps.v1"
  fi

  if is_rak_i2c_voltage_monitor_ota_target "$env_name"; then
    # Every reduced RAK profile retains this compact monitor set, including
    # the Serial1 RS-232 profile that intentionally omits GPS.
    record_build_expectation "sensor.ina219" "INA219"
    record_build_expectation "sensor.ina226" "INA226"
    record_build_expectation "sensor.ina260" "INA260"
    record_build_expectation "sensor.ina3221" "INA3221"
  fi

  if [ "$env_platform" = "ESP32_PLATFORM" ] \
      && [ "$BUILD_PROFILE_FOR_TARGET" = "full" ] \
      && ! is_esp32_companion_build "$env_name"; then
    pio_env_name=$(get_pio_build_env "$env_name")
    if pio_env_option_contains "$pio_env_name" build_flags "ADMIN_PASSWORD"; then
      record_build_expectation "web.webconfig" "start webconfig"
    fi
  fi

  if is_room_server_role_target "$env_name" \
      && [ "$BUILD_PROFILE_FOR_TARGET" = "full" ]; then
    record_build_expectation "room.flood_rule_engine" "flood.rule"
  fi

  if is_companion_radio_full_target "$env_name"; then
    record_build_expectation "companion.temp_radio" "tempradio"
    record_build_expectation "companion.ota_cli" "ota folder"
    record_build_expectation "companion.usb" "+++MESHCORE-TERM-START"
    record_build_expectation "companion.bluetooth" \
      "Companion: starting Bluetooth"
    record_build_expectation "companion.usb_logging" "get usb.logging"
    record_build_expectation "companion.usb_mota_source" "ota folder on"
    record_build_expectation "companion.mota_sender" \
      "_ZN4mesh3ota16SerialMotaSource4read"
    if is_nrf52_companion_radio_full_target "$env_name"; then
      record_build_expectation "companion.dedicated_usb_logging" \
        "get usb.logging"
    fi
    if [ "$env_platform" = "ESP32_PLATFORM" ]; then
      record_build_expectation "companion.network_terminal" \
        "USB currently owns the Full Companion terminal"
      record_build_expectation "companion.wifi_ota_seeder" \
        "OTA seeder listening on :"
      record_build_expectation "web.webconfig" "start webconfig"
      # This branch is constant-folded away when the expanded-profile marker
      # is missing. Make artifact qualification prove that canonical Full
      # Companion images really contain the bounded first-boot WiFi policy.
      record_build_expectation "web.unconfigured_setup_cutoff" \
        "WiFi still unconfigured after"
      pio_env_name=$(get_pio_build_env "$env_name")
      if pio_env_option_contains "$pio_env_name" build_flags \
          "WITH_MQTT_BRIDGE"; then
        record_build_expectation "companion.direct_mqtt" "mqtt.status"
      fi
    else
      record_build_expectation "companion.ble_mota_source" \
        "Bluetooth mOTA source"
    fi
  elif is_companion_build "$env_name" \
      && is_lora_ota_build "$env_name"; then
    record_build_expectation "companion.temp_radio" "ERR usage: tempradio"
    record_build_expectation "companion.ota_cli" "OTA: status"
  fi

  case "$env_name_lc" in
    *sensecapindicator*lora*comp*wifi*)
      record_build_expectation "web.webconfig" "start webconfig"
      ;;
  esac

  case "$env_name_lc" in
    *sensecapindicator*companion_radio_full*)
      # A Full Indicator promises self-repair of the RP2040 font asset. Keep
      # the release manifest from treating an HTTPS downloader without the
      # fresh-time gate as equivalent: these stable messages are emitted on
      # the fail-closed NTP-before-TLS path and survive release builds.
      record_build_expectation "indicator.font_recovery_ntp_gate" \
        "requesting fresh NTP time before download"
      record_build_expectation "indicator.font_recovery_tls" \
        "opening TLS connection to"
      ;;
  esac
}

apply_esp32_lora_ota_size_profile() {
  local env_name=$1

  if [ "$BUILD_PROFILE_FOR_TARGET" != "standard" ] \
      || ! requires_esp32_portable_app_slot "$env_name"; then
    return 0
  fi

  # All non-companion ESP32 artifacts must remain installable into the legacy
  # 0x10000..0x150000 app slot. The WebConfig portal is deliberately omitted.
  # Every field/server role retains the compact browser updater. Publishing a
  # repeater, room server, or sensor with both LoRa OTA and browser OTA absent
  # strands it until someone reaches it with a cable. If the updater pushes a
  # constrained target over its slot ceiling, fail that target instead of
  # silently reducing away its last recovery path. MQTT observers and ESP-NOW
  # bridges are always promoted to the expanded FULL profile so they retain the
  # complete CLI and feature set.
  # Companions retain their target defaults because they are installed over USB.
  # Keep ENV_INCLUDE_GPS for boards with onboard GPS; their target sensor
  # managers require that support.
  export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DWEBCONFIG_DISABLED=1"
  record_build_reduction \
    "web.webconfig omitted to preserve the legacy portable ESP32 app slot"
  if is_lora_ota_only_target "$env_name" \
      || requires_esp32_field_browser_ota "$env_name"; then
    # The no-external-sensors image is also the self-updatable field image. Keep
    # manual browser OTA available on every ESP32 family through the compact
    # uploader, the complete role CLI, and the full one-byte neighbor-index
    # range. MeshCore and this source-built dependency set do not use C++
    # exceptions, so omit their otherwise forced runtime tables instead of
    # dropping WiFi OTA, LoRa OTA, USB seeding, or CLI commands.
    append_platformio_build_unflags "-DDISABLE_WIFI_OTA=1 -DMAX_NEIGHBOURS=50 -DMAX_NEIGHBOURS=8 -fexceptions"
    export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -UDISABLE_WIFI_OTA -DLIGHTWEIGHT_WIFI_OTA=1 -DMAX_NEIGHBOURS=${ESP32_FULL_MAX_NEIGHBOURS} -fno-exceptions"
    record_build_capability "web.lightweight_browser_ota"
  else
    append_platformio_build_unflags "-DLIGHTWEIGHT_WIFI_OTA=1"
    export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -ULIGHTWEIGHT_WIFI_OTA -DDISABLE_WIFI_OTA=1"
    record_build_reduction \
      "web.browser_ota omitted because this portable radio role does not otherwise require WiFi"
  fi

}

apply_esp32_constrained_companion_size_profile() {
  local env_name=$1

  if [ "${PIO_ENV_PLATFORM_BY_NAME[$env_name]:-}" != "ESP32_PLATFORM" ]; then
    return 0
  fi

  # These 1.25 MiB companion images had only 22-66 KiB of measured app-slot
  # headroom. MeshCore and their source-built dependencies do not use C++
  # exceptions, so omit the exception runtime instead of reducing contacts,
  # queues, BLE, WiFi, GPS, display support, or CLI commands. Keep this scoped
  # to measured constrained targets; expanded FULL builds do not need it.
  case "${env_name,,}" in
    station_g2_companion_radio_ble \
      |thinknode_m2_companion_radio_ble \
      |thinknode_m2_companion_radio_wifi \
      |lilygo_t3s3_sx1262_companion_radio_ble \
      |lilygo_t3s3_sx1262_companion_radio_ble_ps \
      |lilygo_t3s3_sx1276_companion_radio_ble \
      |nibble_zero_connect_companion_radio_ble_ \
      |nibble_zero_connect_companion_radio_wifi_ \
      |nibble_screen_connect_companion_radio_ble_ \
      |nibble_screen_connect_companion_radio_wifi_)
      append_platformio_build_unflags "-fexceptions"
      export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -fno-exceptions"
      ;;
  esac
}

apply_esp32_full_size_profile() {
  local env_name=$1
  local max_neighbours=$ESP32_FULL_MAX_NEIGHBOURS

  if [ "$ESP32_FULL_BUILD" != "1" ] || ! supports_esp32_full_build "$env_name"; then
    return 0
  fi

  # The FULL artifact uses expanded dual-OTA slots, so restore features that
  # target or legacy-slot profiles disabled only to save application space.
  append_platformio_build_unflags "-DWEBCONFIG_DISABLED=1"
  export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -UWEBCONFIG_DISABLED -DWIFI_OTA_SEEDER=1 -DMESHCORE_EXPANDED_PARTITION_PROFILE=1"
  if is_room_server_role_target "$env_name"; then
    export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DMESH_ENABLE_ROOM_FLOOD_RULE_ENGINE=1"
  fi

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
    append_platformio_build_unflags "-DMAX_CONTACTS=350 -DMAX_CONTACTS=160 -DMAX_GROUP_CHANNELS=40 -DOFFLINE_QUEUE_SIZE=512 -DOFFLINE_QUEUE_SIZE=256 -DOFFLINE_QUEUE_SIZE=128"
    export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DMAX_CONTACTS=100 -DMAX_GROUP_CHANNELS=8 -DOFFLINE_QUEUE_SIZE=16"
  fi
}

apply_esp32_full_async_tcp_profile() {
  local env_name=$1

  if [ "${PIO_ENV_PLATFORM_BY_NAME[$env_name]:-}" != "ESP32_PLATFORM" ] \
      || { [ "$ESP32_FULL_BUILD" != "1" ] \
           && ! is_esp32_companion_radio_full_target "$env_name"; }; then
    return 0
  fi

  # AsyncTCP 3.x defaults to a 16 KiB task stack. A feature-complete image can
  # have less than that as one contiguous internal-DRAM block by the time its
  # WebConfig server starts, even when total/PSRAM capacity remains plentiful.
  # Use the library's documented 4 KiB reduced configuration. Eight KiB let
  # the task start on the most constrained measured Full Companion, but left
  # too little internal heap to retire successive HTTP responses reliably.
  # Pin its event/callback task to the application core as recommended by the
  # library. Leaving it on the WiFi/Bluetooth core lets a long response to a
  # lossy client starve the shared radio until even ARP recovery fails.
  # Also disable ESPAsyncWebServer's optional 2-MSS in-flight gate. A slow peer
  # can acknowledge one MSS at a time; after a fixed response crosses the TCP
  # window the gate then waits forever for two MSS of space. WebConfig's
  # response fillers are fast flash/memory copies, and the TCP send buffer still
  # provides back-pressure without that gate.
  export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DCONFIG_ASYNC_TCP_STACK_SIZE=4096 -DCONFIG_ASYNC_TCP_RUNNING_CORE=1 -DASYNCWEBSERVER_USE_CHUNK_INFLIGHT=0"
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
    record_build_reduction \
      "mesh.neighbors limited to ${DRAM_LIMITED_MAX_NEIGHBOURS} by measured RAM/flash capacity"
  else
    append_platformio_build_unflags "-DMAX_NEIGHBOURS=8 -DMAX_NEIGHBOURS=50"
  fi
  export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DMAX_NEIGHBOURS=${max_neighbours}"
}

apply_nrf52_size_profile() {
  local env_name=$1

  if [ "${PIO_ENV_PLATFORM_BY_NAME[$env_name]:-}" != "NRF52_PLATFORM" ]; then
    return 0
  fi
  if ! is_nrf52_companion_radio_full_target "$env_name" \
      && { ! is_lora_ota_build "$env_name" \
           || ! is_lora_ota_only_target "$env_name"; }; then
    return 0
  fi

  # The Adafruit nRF52 platform defaults release builds to -Ofast. Once the
  # runtime software Ed25519 fallback is linked alongside CC310, that setting
  # fully expands repeated Curve25519 arithmetic and wastes tens of kilobytes.
  # Keep hardware crypto, RNG mixing, the software fallback, and board features;
  # select the size optimizer for both constrained self-updating images and
  # Full Companion source images, especially their diagnostic profile.
  append_platformio_build_unflags "-Ofast"
  export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -Os"
}

apply_lora_ota_no_external_sensors_profile() {
  local env_name=$1
  local omitted_sensor_flags="ENV_INCLUDE_AHTX0 ENV_INCLUDE_BME280 ENV_INCLUDE_BMP280 ENV_INCLUDE_SHTC3 ENV_INCLUDE_SHT4X ENV_INCLUDE_LPS22HB ENV_INCLUDE_MLX90614 ENV_INCLUDE_VL53L0X ENV_INCLUDE_BME680 ENV_INCLUDE_BMP085 ENV_INCLUDE_RAK12035 ENV_INCLUDE_BME680_BSEC"
  local voltage_monitor_flags="ENV_INCLUDE_INA3221 ENV_INCLUDE_INA219 ENV_INCLUDE_INA226 ENV_INCLUDE_INA260"
  local flag
  local omit_unflags=""
  local omit_overrides=""
  local retain_overrides=""

  if [ "$SKIP_DECLARED_REDUCTIONS" = "1" ]; then
    return 0
  fi
  if ! is_lora_ota_build "$env_name" \
      || ! is_lora_ota_no_external_sensors_target "$env_name"; then
    return 0
  fi

  # The explicit LoRa-OTA sibling drops selected optional environmental and
  # ranging drivers; it does not globally disable I2C or board peripherals.
  # RAK3401/RAK4631 keep the compact INA voltage/current monitor set because it
  # costs less than 5 KiB and covers their most common external telemetry use.
  # The ordinary sibling remains fully sensor-enabled.
  # GPS availability follows the exact target recipe. In particular, the
  # RAK4631 Serial1 RS-232 bridge intentionally omits GPS because both need the
  # same UART; other reduced RAK profiles below retain compatible GPS paths.
  if ! is_rak_i2c_voltage_monitor_ota_target "$env_name"; then
    omitted_sensor_flags+=" ${voltage_monitor_flags}"
  fi
  for flag in $omitted_sensor_flags; do
    omit_unflags+=" -D${flag}=1"
    omit_overrides+=" -U${flag}"
  done
  append_platformio_build_unflags "$omit_unflags"

  if is_rak_i2c_voltage_monitor_ota_target "$env_name"; then
    for flag in $voltage_monitor_flags; do
      retain_overrides+=" -D${flag}=1"
    done
    export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS}${omit_overrides}${retain_overrides}"
    if is_rak_gps_retaining_ota_target "$env_name"; then
      record_build_reduction \
        "selected optional environmental/ranging drivers omitted; INA219/INA226/INA260/INA3221 and board display/RTC/GPS retained; RAK12500 and INA3221 require distinct configured I2C addresses"
    else
      record_build_reduction \
        "selected optional environmental/ranging drivers and GPS omitted; INA219/INA226/INA260/INA3221 and board display/RTC retained; GPS conflicts with RS-232 on Serial1"
    fi
  else
    export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS}${omit_overrides}"
    record_build_reduction \
      "selected optional environmental/ranging drivers omitted; generic I2C and exact-target board peripherals remain unchanged"
  fi
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

apply_lora_ota_flag_order_fix() {
  local env_name=$1
  local pio_env_name=$2

  if ! is_lora_ota_build "$env_name" \
      && ! is_companion_radio_full_target "$env_name"; then
    unset MESHCORE_FORCE_LORA_OTA
    return 0
  fi

  # A few legacy recipes deliberately finish with -UENABLE_OTA. PlatformIO
  # retains that raw undefine after externally supplied definitions, so an OTA
  # overlay can otherwise advertise its controls while compiling out the
  # protocol implementation. Remove the legacy undefine before flags are
  # parsed for every OTA overlay, not just for one platform or role.
  if pio_env_option_contains "$pio_env_name" build_flags "-UENABLE_OTA"; then
    export MESHCORE_FORCE_LORA_OTA=1
    append_platformio_extra_script "pre:scripts/force_lora_ota.py"
  else
    unset MESHCORE_FORCE_LORA_OTA
  fi
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
  if ! pio_env_option_contains "$pio_env_name" build_src_filter "helpers/ota/*.cpp"; then
    append_platformio_build_src_filter "+<helpers/ota/*.cpp>"
  fi
  if ! pio_env_option_contains "$pio_env_name" extra_scripts "tools/mota/pio_endf.py"; then
    append_platformio_extra_script "post:tools/mota/pio_endf.py"
  fi

  if supports_nrf52_internal_bootloader_update "$env_name"; then
    export MESHCORE_NRF52_INTERNAL_BOOTLOADER_UPDATE=1
  else
    unset MESHCORE_NRF52_INTERNAL_BOOTLOADER_UPDATE
  fi
}

# Exact-board OTAFIX manifests currently emitted for shared internal,
# app-preserving bootloader staging. Keep this list narrower than generic nRF52
# OTA support: external QSPI/SD, ExtraFS Companions, Ethernet, and
# unqualified/full-sensor roles must not advertise this privileged path. The
# same inventory is consumed by the nrf52_base pre-script so direct explicit-env
# and build.sh release builds cannot silently differ.
supports_nrf52_internal_bootloader_update() {
  [ "${PIO_ENV_PLATFORM_BY_NAME[$1]:-}" = "NRF52_PLATFORM" ] || return 1
  [ "${PIO_ENV_QSPI_OTA_BY_NAME[$1]:-0}" = "0" ] || return 1
  [ "${PIO_ENV_SD_OTA_BY_NAME[$1]:-0}" = "0" ] || return 1
  is_lora_ota_build "$1" || return 1
  grep -Fqx -- "$1" tools/mota/nrf52_internal_bootloader_targets.txt
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
    if [ "${PIO_ENV_QSPI_OTA_BY_NAME[$env_name]:-0}" = "1" ]; then
      append_platformio_build_unflags "-UENABLE_OTA -DDISABLE_LORA_OTA=1 -DOTA_FLASH_STORE=1 -DOTA_SD_STORE=1"
      export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -UDISABLE_LORA_OTA -DENABLE_OTA=1 -UOTA_FLASH_STORE -UOTA_SD_STORE -DOTA_QSPI_STORE=1 -DOTA_FOLDER_SERIAL"
    elif [ "${PIO_ENV_SD_OTA_BY_NAME[$env_name]:-0}" = "1" ]; then
      append_platformio_build_unflags "-UENABLE_OTA -DDISABLE_LORA_OTA=1 -DOTA_FLASH_STORE=1"
      export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -UDISABLE_LORA_OTA -DENABLE_OTA=1 -UOTA_FLASH_STORE -DOTA_SD_STORE=1 -DOTA_FOLDER_SERIAL"
    else
      append_platformio_build_unflags "-UENABLE_OTA -DDISABLE_LORA_OTA=1"
      export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -UDISABLE_LORA_OTA -DENABLE_OTA=1 -DOTA_FLASH_STORE=1 -DOTA_FOLDER_SERIAL"
    fi
    if is_companion_build "$env_name"; then
      # Do not link an OTA manager into a Companion while compiling out the
      # terminal controls needed to operate it over LoRa.
      export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DCOMPANION_FEATURE_TEMP_RADIO=1 -DCOMPANION_FEATURE_OTA_CLI=1"
    fi
  else
    append_platformio_build_unflags "-DENABLE_OTA=1"
    export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -UENABLE_OTA"
  fi
}

apply_logical_ota_tuning_flags() {
  local logical_env_name=$1
  local pio_env_name=$2
  local ota_flags

  if [ "$logical_env_name" = "$pio_env_name" ]; then
    return 0
  fi

  ota_flags=$(python3 -c '
import json
import sys

logical_env = sys.argv[1]
data = json.load(sys.stdin)
for section, options in data:
    if section != f"env:{logical_env}":
        continue
    for key, value in options:
        if key != "build_flags":
            continue
        values = value if isinstance(value, list) else [str(value)]
        for item in values:
            flag = str(item).strip()
            if flag.startswith("-D OTA_") or flag.startswith("-DOTA_"):
                print(flag)
    break
' "$logical_env_name" <<<"$PIO_CONFIG_JSON")

  if [ -n "$ota_flags" ]; then
    export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} ${ota_flags}"
  fi
}

apply_companion_radio_full_profile() {
  local env_name=$1
  local pio_env_name=$2

  unset MESHCORE_ESP32_FULL_PARTITION_TABLE
  is_companion_radio_full_target "$env_name" || return 0

  # Every full Companion is a LoRa mOTA source, never an update destination.
  # Remove inherited staging/install stores before adding the platform's host
  # folder transport. Full also restores WebConfig when a constrained legacy
  # WiFi sibling disabled it only to fit its smaller application partition.
  append_platformio_build_unflags "-UENABLE_OTA -DOTA_FLASH_STORE=1 -DOTA_SD_STORE=1 -DDISABLE_LORA_OTA=1 -DWEBCONFIG_DISABLED=1"
  export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -UDISABLE_LORA_OTA -DENABLE_OTA=1 -UOTA_FLASH_STORE -UOTA_SD_STORE -UWEBCONFIG_DISABLED -DOTA_SEEDER_ONLY=1 -DMOTA_TARGET_ID=0 -DCOMPANION_RADIO_FULL=1 -DCOMPANION_FEATURE_TEMP_RADIO=1 -DCOMPANION_FEATURE_OTA_CLI=1 -DENABLE_USB_INTERFACE=1 -DBLE_PIN_CODE=123456 -DMESH_DEBUG=1 -DMESH_PACKET_LOGGING=1"

  if is_nrf52_companion_radio_full_target "$env_name"; then
    # CDC 0 starts as an ASCII CLI and automatically hands an incoming framed
    # command to Binary Companion. `motatool serve --serial` switches it into
    # an exclusive host-folder mode with its existing `ota folder on` preamble.
    # CDC 1 is a write-only plaintext packet/debug logging stream; BLE remains
    # an independent Companion link. ESP32 intentionally does not use this
    # dual-CDC capability.
    append_platformio_build_unflags "-UOTA_FOLDER_SERIAL"
    export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DOTA_FOLDER_SERIAL=1 -DCOMPANION_FEATURE_USB_MOTA_SOURCE=1 -DCOMPANION_FEATURE_BLE_MOTA_SOURCE=1 -DCOMPANION_FEATURE_DEDICATED_USB_LOGGING=1 -DCFG_TUD_CDC=2 -DMESH_DUAL_CDC_LOGGING=1 -DMESH_DEBUG=1 -DMESH_PACKET_LOGGING=1"

    if ! pio_env_option_contains "$pio_env_name" build_src_filter "helpers/ota/*.cpp"; then
      append_platformio_build_src_filter "+<helpers/ota/*.cpp>"
    fi
    if ! pio_env_option_contains "$pio_env_name" build_src_filter "helpers/nrf52/*.cpp" \
        && ! pio_env_option_contains "$pio_env_name" build_src_filter "helpers/nrf52/SerialBLEInterface.cpp"; then
      append_platformio_build_src_filter "+<helpers/nrf52/SerialBLEInterface.cpp>"
    fi
    return 0
  fi

  # ESP32 keeps both source transports. TCP remains the normal unattended
  # path; a host may explicitly hold the native USB port in Companion terminal
  # mode when WiFi is unavailable and serve the same folder protocol there.
  # The canonical Full Companion target is already built with an expanded
  # partition table even when it is selected from the ordinary Companion
  # release matrix (ESP32_FULL_BUILD remains 0 in that path). Mark that
  # layout explicitly so Full-only runtime policies, including the bounded
  # first-boot WebConfig window, cannot silently compile with legacy defaults.
  export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DWIFI_OTA_SEEDER=1 -DCOMPANION_FEATURE_USB_MOTA_SOURCE=1 -DCOMPANION_FEATURE_NETWORK_TERMINAL=1 -DCOMPANION_FEATURE_MEMORY_DIAGNOSTICS=1 -DMESHCORE_EXPANDED_PARTITION_PROFILE=1"
  append_platformio_build_unflags "-DDISABLE_WIFI_OTA=1 -fexceptions"
  export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -UDISABLE_WIFI_OTA -DLIGHTWEIGHT_WIFI_OTA=1 -fno-exceptions"

  # Both Indicator radio layouts share the same original NVS/SPIFFS data
  # placement. The LoRa WiFi base names that map directly; the ESP-NOW USB
  # base does not, so pass the physical-board requirement to the partition
  # hook from the canonical target identity rather than guessing from flash
  # size or the generic esp32-s3-devkitc manifest.
  case "${env_name,,}" in
    sensecapindicator-espnow_companion_radio_full|\
    sensecapindicator-lora_companion_radio_full)
      export MESHCORE_ESP32_FULL_PARTITION_TABLE="variants/sensecap_indicator-espnow/dual_ota_2560k_preserve_spiffs.csv"
      # Both physical Indicator radio layouts gain ordinary WiFi in their Full
      # overlay. Keep font recovery tied to that effective capability rather
      # than to whichever historical USB/WiFi environment supplies the recipe.
      export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DINDICATOR_WIFI_FONT_RECOVERY=1"
      ;;
    sensecapindicator-lora-n16r2_companion_radio_full)
      export MESHCORE_ESP32_FULL_PARTITION_TABLE="variants/sensecap_indicator-espnow/dual_ota_6400k_preserve_spiffs.csv"
      # Both physical Indicator radio layouts gain ordinary WiFi in their Full
      # overlay. Keep font recovery tied to that effective capability rather
      # than to whichever historical USB/WiFi environment supplies the recipe.
      export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DINDICATOR_WIFI_FONT_RECOVERY=1"
      ;;
  esac

  # Both Indicator Full layouts select exactly one secondary Companion
  # transport per boot. On the ESP-NOW layout, BLE mode leaves the primary
  # ESP-NOW WiFi radio running while omitting only infrastructure WiFi; WiFi
  # mode can release Bluetooth memory without disturbing ESP-NOW.
  case "${env_name,,}" in
    sensecapindicator-espnow_companion_radio_full|\
    sensecapindicator-lora_companion_radio_full|\
    sensecapindicator-lora-n16r2_companion_radio_full)
      export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DCOMPANION_EXCLUSIVE_WIFI_BLE=1 -DINDICATOR_TRANSPORT_RENDER_PROFILE=1 -DUI_WIFI_SETUP_HOME_PAGE=1 -DWEBCONFIG_AP_PREFIX='\"MC-Set\"'"
      ;;
  esac

  # Allocate the native canvas while internal RAM is least fragmented. After
  # preferences load, ESP-NOW+BLE shrinks it to 320x320 before BLE starts;
  # every other combination retains native 480x480 rendering.
  case "${env_name,,}" in
    sensecapindicator-espnow_companion_radio_full|\
    sensecapindicator-lora_companion_radio_full|\
    sensecapindicator-lora-n16r2_companion_radio_full)
      export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -UUI_ZOOM -DUI_ZOOM=1.0f -UUI_COORD_SCALE -DUI_COORD_SCALE=3"
      ;;
  esac

  # ESP-NOW is the primary mesh radio on these two layouts, so conventional
  # Companion WiFi must share its protocol mask and fixed channel instead of
  # resetting the driver to B/G/N on an arbitrary access-point channel. The
  # runtime policy keeps B/G/N enabled for phones and normal routers while LR
  # remains available for the mesh packets.
  case "${env_name,,}" in
    generic_espnow_companion_radio_full|\
    sensecapindicator-espnow_companion_radio_full)
      export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DMESH_ESPNOW_RADIO=1 -DMESH_ESPNOW_CHANNEL=1"
      ;;
  esac

  # Qualified BLE-based Full recipes did not previously need WiFi credentials.
  # Supply the same first-boot setup placeholders used by ordinary WiFi
  # Companion recipes; saved credentials and WebConfig replace them at runtime.
  if ! pio_env_option_contains "$pio_env_name" build_flags "WIFI_SSID"; then
    export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DWIFI_SSID='\"WIFI_SSID\"' -DWIFI_PWD='\"Password\"'"
  fi

  # BLE + WiFi exhaust internal DRAM on these high-capacity ESP32 recipes. Use
  # measured-safe tables for FULL OTA without changing ordinary USB/BLE/WiFi
  # companion builds.
  case "${env_name,,}" in
    meshadventurer_sx1262_companion_radio_full|\
    meshadventurer_sx1268_companion_radio_full)
      append_platformio_build_unflags "-DMAX_CONTACTS=160 -DMAX_GROUP_CHANNELS=40 -DOFFLINE_QUEUE_SIZE=128"
      export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DMAX_CONTACTS=160 -DMAX_GROUP_CHANNELS=30 -DOFFLINE_QUEUE_SIZE=64"
      record_build_reduction \
        "companion.capacity limited to 160 contacts, 30 channels, and 64 queued frames by measured internal DRAM"
      ;;
    *)
      if requires_esp32_companion_full_ota_fallback "$pio_env_name"; then
        append_platformio_build_unflags "-DMAX_CONTACTS=350 -DMAX_CONTACTS=160 -DMAX_GROUP_CHANNELS=40 -DOFFLINE_QUEUE_SIZE=512 -DOFFLINE_QUEUE_SIZE=256 -DOFFLINE_QUEUE_SIZE=128"
        export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DMAX_CONTACTS=100 -DMAX_GROUP_CHANNELS=8 -DOFFLINE_QUEUE_SIZE=16"
        record_build_reduction \
          "companion.capacity limited to 100 contacts, 8 channels, and 16 queued frames by measured internal DRAM"
      fi
      ;;
  esac

  # A few BLE recipes list only their BLE implementation instead of the full
  # ESP32 helper directory. Add the WiFi transport explicitly in that case.
  if ! pio_env_option_contains "$pio_env_name" build_src_filter "helpers/esp32/*.cpp" \
      && ! pio_env_option_contains "$pio_env_name" build_src_filter "helpers/esp32/SerialWifiInterface.cpp"; then
    append_platformio_build_src_filter "+<helpers/esp32/SerialWifiInterface.cpp>"
  fi

  # WiFi recipes can have the inverse narrow filter. Preserve the existing
  # guard so both kinds of synthesized Full target receive both transports.
  if ! pio_env_option_contains "$pio_env_name" build_src_filter "helpers/esp32/*.cpp" \
      && ! pio_env_option_contains "$pio_env_name" build_src_filter "helpers/esp32/SerialBLEInterface.cpp"; then
    append_platformio_build_src_filter "+<helpers/esp32/SerialBLEInterface.cpp>"
  fi
}

apply_radio_overrides() {
  if [ -n "$RADIO_FREQ_OVERRIDE" ] && [ -n "$RADIO_BW_OVERRIDE" ] && [ -n "$RADIO_SF_OVERRIDE" ] && [ -n "$RADIO_CR_OVERRIDE" ]; then
    export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DLORA_FREQ=${RADIO_FREQ_OVERRIDE} -DLORA_BW=${RADIO_BW_OVERRIDE} -DLORA_SF=${RADIO_SF_OVERRIDE} -DLORA_CR=${RADIO_CR_OVERRIDE}"
    if [ "$RADIO_SETTING_TITLE" = "$USA_CASCADIA_RADIO_TITLE" ] \
        || is_usa_cascadia_radio_title "$RADIO_SETTING_TITLE"; then
      export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DMESHCORE_USA_RADIO_PRESET=1"
    fi
  fi
}

apply_firmware_profile_overrides() {
  case "${FIRMWARE_PROFILE_OVERRIDE,,}" in
    cascade)
      export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DCASCADE_PROFILE=1 -DDEFAULT_PATH_HASH_MODE=2 -DDEFAULT_LOOP_DETECT=1 -DDEFAULT_CAD_ENABLED=1 -DDEFAULT_RX_DELAY_BASE=2.0f -DDEFAULT_AGC_RESET_INTERVAL_SECONDS=8 -DDEFAULT_ADVERT_INTERVAL_MINUTES=0 -DDEFAULT_FLOOD_ADVERT_INTERVAL_HOURS=83 -DDEFAULT_MULTI_ACKS=1 -DDEFAULT_MANUAL_ADD_CONTACTS=1 -DDEFAULT_AUTOADD_CONFIG=0 -DDEFAULT_POWERSAVING_ENABLED=1 -DDEFAULT_RXPS_ENABLED=1 -DDEFAULT_RXPS_LEVEL=8 -DDEFAULT_RXPS_PREAMBLE=16 -DRXPS_FIXED_ENABLED=1 -DRXPS_FIXED_LEVEL=8 -DRXPS_FIXED_PREAMBLE=16 -DDEFAULT_WIFI_POWER_SAVE_MODE=0"
      ;;
  esac
}

print_build_flags() {
  local pio_env_name=$1
  local logical_env_name=${2:-$pio_env_name}

  if [ "$logical_env_name" = "$pio_env_name" ]; then
    echo "Build flags for ${logical_env_name}:"
  else
    echo "Build flags for ${logical_env_name} (PlatformIO base ${pio_env_name}):"
  fi
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
' "$pio_env_name" <<<"$PIO_CONFIG_JSON"
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

write_build_capability_manifest() {
  local env_name=$1
  local env_platform=$2
  local pio_env_name=$3
  local firmware_filename=$4
  local build_output_dir="${PIO_BUILD_DIR_OVERRIDE:-${PLATFORMIO_BUILD_DIR:-.pio/build}}/${pio_env_name}"
  local -a checker_args=(
    --image "${build_output_dir}/firmware.elf"
    --output "${OUTPUT_DIR}/${firmware_filename}.capabilities.json"
    --target "$env_name"
    --artifact-target "${FIRMWARE_OUTPUT_ENV_NAME:-$env_name}"
    --platformio-env "$pio_env_name"
    --platform "$env_platform"
    --build-profile "$BUILD_PROFILE_FOR_TARGET"
  )
  local item

  if [ "${REQUIRE_OTA_UPDATES:-0}" = "1" ] && ! is_companion_build "$env_name"; then
    checker_args+=(--require-ota)
  fi
  if [ "$env_platform" = "ESP32_PLATFORM" ]; then
    checker_args+=(--firmware-bin "${build_output_dir}/firmware.bin"
                   --partitions "${build_output_dir}/partitions.bin")
  elif [ "$env_platform" = "NRF52_PLATFORM" ]; then
    checker_args+=(--dfu-package "${build_output_dir}/firmware.zip")
  fi

  for item in "${BUILD_CAPABILITIES[@]}"; do
    checker_args+=(--capability "$item")
  done
  for item in "${BUILD_REDUCTIONS[@]}"; do
    checker_args+=(--reduction "$item")
  done
  for item in "${BUILD_EXPECTATIONS[@]}"; do
    checker_args+=(--expect "$item")
  done

  python3 scripts/check_firmware_capabilities.py "${checker_args[@]}"
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

  if [ "$BUILD_PROFILE_FOR_TARGET" = "standard" ] \
      && requires_esp32_portable_size_ceiling "$env_name"; then
    size_check_args+=("$ESP32_LORA_OTA_APP_LIMIT")
  fi

  python3 scripts/check_esp32_app_size.py "${size_check_args[@]}"
  local size_status=$?
  if [ "$size_status" -eq 1 ]; then
    return 42
  elif [ "$size_status" -ne 0 ]; then
    return "$size_status"
  fi
  copy_build_output "${build_output_dir}/firmware.bin" "${OUTPUT_DIR}/${firmware_filename}.bin" || return $?
  copy_build_output "${build_output_dir}/firmware-merged.bin" "${OUTPUT_DIR}/${firmware_filename}-merged.bin" || return $?

  # Emit the partition-table signature for OTA partition-compatibility checks.
  # Standard builds keep the env-name key used by the slim-manifest generator;
  # FULL builds use a suffix so their expanded table does not overwrite the
  # legacy-slot signature. The firmware computes the same signature at runtime.
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

  output_artifact_exists "${firmware_filename}.capabilities.json" || return 1
  grep -q '"verified": true' \
    "${OUTPUT_DIR}/${firmware_filename}.capabilities.json" || return 1
  if [ "${REQUIRE_OTA_UPDATES:-0}" = "1" ]; then
    python3 - "${OUTPUT_DIR}/${firmware_filename}.capabilities.json" <<'PY' || return 1
import json, sys
manifest = json.load(open(sys.argv[1]))
target = manifest.get("target", "").lower()
companion = "companion" in target or "comp_radio" in target
sys.exit(0 if manifest.get("schema_version", 0) >= 2 and
         (companion or manifest.get("ota_update_verified")) else 1)
PY
  fi

  case "$env_platform" in
    ESP32_PLATFORM)
      output_artifact_exists "${firmware_filename}.bin" \
        && output_artifact_exists "${firmware_filename}-merged.bin"
      ;;
    NRF52_PLATFORM)
      output_artifact_exists "${firmware_filename}.uf2" \
        && { [ "${REQUIRE_OTA_UPDATES:-0}" != "1" ] \
             || output_artifact_exists "${firmware_filename}.zip"; }
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

  # Qualify the linked image before copying anything into out/. A failed
  # capability contract must not leave an apparently publishable firmware
  # binary behind (in particular, an ESP32 field image with no update path).
  write_build_capability_manifest \
    "$env_name" "$env_platform" "$pio_env_name" "$firmware_filename" \
    || return $?

  # Post-build outputs differ by platform, so dispatch to the matching
  # collector after the main firmware build and capability checks succeed.
  case "$env_platform" in
    ESP32_PLATFORM)
      collect_esp32_artifacts "$env_name" "$pio_env_name" "$firmware_filename" || return $?
      ;;
    NRF52_PLATFORM)
      collect_nrf52_artifacts "$env_name" "$pio_env_name" "$firmware_filename" || return $?
      ;;
    STM32_PLATFORM)
      collect_stm32_artifacts "$env_name" "$pio_env_name" "$firmware_filename" || return $?
      ;;
    RP2040_PLATFORM)
      collect_rp2040_artifacts "$env_name" "$pio_env_name" "$firmware_filename" || return $?
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

  # Make LoRa-OTA artifacts obvious without changing the PlatformIO environment
  # name or the stable MOTA target identity. FULL artifacts retain their
  # profile marker as well as the required OTA marker.
  if [ "$ESP32_FULL_BUILD" = "1" ] && is_lora_ota_build "$env_name"; then
    if [ "$filename_infix" = "full-logging" ]; then
      filename_infix="full-logging-ota"
    elif [ "$filename_infix" = "full-usb-wifi" ]; then
      filename_infix="full-usb-wifi-ota"
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
  local had_platformio_build_flags=0
  local had_platformio_build_unflags=0
  local had_platformio_build_src_filter=0
  local had_platformio_extra_scripts=0
  local build_status
  local platformio_package_lock_fd=""
  local -a pio_run_args=()
  local -a BUILD_CAPABILITIES=()
  local -a BUILD_REDUCTIONS=()
  local -a BUILD_EXPECTATIONS=()
  local BUILD_PROFILE_FOR_TARGET=$BUILD_PROFILE_EFFECTIVE

  # Bash functions use dynamic scoping. These locals let one target be promoted
  # to FULL without changing the profile selected for later targets in the same
  # batch.
  local inherited_esp32_full_build=$ESP32_FULL_BUILD
  local inherited_firmware_filename_infix=$FIRMWARE_FILENAME_INFIX
  local ESP32_FULL_BUILD=$inherited_esp32_full_build
  local FIRMWARE_FILENAME_INFIX=$inherited_firmware_filename_infix

  env_platform=$(get_platform_for_env "$env_name")
  if ! is_supported_platform "$env_platform"; then
    echo "Unsupported or unknown platform for env: $env_name"
    return 1
  fi
  pio_env_name=$(get_pio_build_env "$env_name")
  if [ "$COMPLETE_OTA_FIRST_PASS" = "1" ] \
      && [ -n "${PIO_ENV_COMPLETE_OTA_BASE_BY_NAME[$env_name]+x}" ]; then
    pio_env_name=${PIO_ENV_COMPLETE_OTA_BASE_BY_NAME[$env_name]}
    echo "Complete OTA pass uses feature-rich base environment ${pio_env_name} with stable target identity ${env_name}."
  fi

  if [ "$ESP32_FULL_BUILD" != "1" ] \
      && requires_esp32_full_cli_profile "$env_name"; then
    if [ "$BUILD_PROFILE_EXPLICIT" = "1" ] \
        && [ "${BUILD_PROFILE_OVERRIDE,,}" = "standard" ]; then
      echo "Target ${env_name} requires the expanded FULL profile to retain its complete CLI; refusing an explicit standard build."
      return 1
    fi
    ESP32_FULL_BUILD=1
    if [ "$FIRMWARE_FILENAME_INFIX" = "logging" ]; then
      FIRMWARE_FILENAME_INFIX="full-logging"
    else
      FIRMWARE_FILENAME_INFIX="full"
    fi
    echo "Promoting ${env_name} to FULL so the complete CLI is retained."
  fi

  if [ "$ESP32_FULL_BUILD" = "1" ] \
      || is_companion_radio_full_target "$env_name"; then
    BUILD_PROFILE_FOR_TARGET="full"
  fi
  echo "Effective feature profile for ${env_name}: ${BUILD_PROFILE_FOR_TARGET}"

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
  firmware_filename=$(get_firmware_filename \
    "${FIRMWARE_OUTPUT_ENV_NAME:-$env_name}" "$firmware_version_string")

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
  disable_debug_flags "$env_name"
  apply_debug_overrides "$env_name"
  apply_mqtt_bridge_override "$env_name"
  disable_usb_logging_for_mqtt "$env_name"
  apply_merged_standard_usb_logging_profile "$env_name"
  apply_lora_ota_override "$env_name"
  apply_logical_ota_tuning_flags "$env_name" "$pio_env_name"
  apply_companion_radio_full_profile "$env_name" "$pio_env_name"
  apply_lora_ota_flag_order_fix "$env_name" "$pio_env_name"
  apply_nrf52_lora_ota_build_recipe "$env_name" "$pio_env_name"
  apply_esp32_lora_ota_size_profile "$env_name"
  apply_esp32_constrained_companion_size_profile "$env_name"
  apply_esp32_full_size_profile "$env_name"
  apply_esp32_full_async_tcp_profile "$env_name"
  apply_repeater_neighbor_capacity "$env_name"
  apply_nrf52_size_profile "$env_name"
  apply_lora_ota_no_external_sensors_profile "$env_name"
  apply_radio_overrides
  apply_firmware_profile_overrides
  declare_build_capability_contract "$env_name" "$env_platform"

  if [ "$ESP32_FULL_BUILD" = "1" ] || is_esp32_companion_radio_full_target "$env_name"; then
    export MESHCORE_ESP32_FULL_BUILD=1
    if is_esp32_companion_radio_full_target "$env_name"; then
      export MESHCORE_COMPANION_RADIO_FULL=1
    else
      unset MESHCORE_COMPANION_RADIO_FULL
    fi
    if ! pio_env_option_contains "$pio_env_name" extra_scripts \
        "scripts/esp32_full_partition.py"; then
      append_platformio_extra_script "pre:scripts/esp32_full_partition.py"
    fi
    # PlatformIO appends PLATFORMIO_BUILD_FLAGS while resolving every extends
    # layer. Deep ESP32 Full profiles can therefore exceed Windows' process
    # command-line limit before GCC can launch cc1plus. Compact repeated macro
    # flags and Windows include paths after all profiles are resolved.
    if ! pio_env_option_contains "$pio_env_name" extra_scripts \
        "scripts/deduplicate_full_build_flags.py"; then
      append_platformio_extra_script \
        "pre:scripts/deduplicate_full_build_flags.py"
    fi
  else
    unset MESHCORE_ESP32_FULL_BUILD
    unset MESHCORE_COMPANION_RADIO_FULL
  fi

  if [ "$env_platform" = "ESP32_PLATFORM" ] \
      && ! pio_env_option_contains "$pio_env_name" extra_scripts \
          "scripts/check_esp32_dram.py"; then
    append_platformio_extra_script "post:scripts/check_esp32_dram.py"
  fi

  print_build_flags "$pio_env_name" "$env_name"
  build_status=0
  if [ "$env_platform" = "ESP32_PLATFORM" ]; then
    # Official ESP32 and pioarduino builds share global package names even
    # though they require incompatible Arduino cores. Hold the lock through
    # dependency selection and compilation so parallel profile workers cannot
    # replace a framework while another ESP32 target is using it.
    mkdir -p -- ".pio"
    build_status=$?
    if [ "$build_status" -eq 0 ]; then
      exec {platformio_package_lock_fd}>".pio/esp32-platformio-packages.lock"
      build_status=$?
    fi
    if [ "$build_status" -eq 0 ]; then
      flock "$platformio_package_lock_fd"
      build_status=$?
    fi
  fi
  if [ "$build_status" -eq 0 ]; then
    prepare_esp32_arduino3_framework "$env_name" "$pio_env_name"
    build_status=$?
  fi
  pio_run_args=(run -e "$pio_env_name")
  if [[ "${PIO_BUILD_JOBS_OVERRIDE:-}" =~ ^[1-9][0-9]*$ ]]; then
    pio_run_args+=(-j "$PIO_BUILD_JOBS_OVERRIDE")
  fi
  if [ "$env_platform" = "ESP32_PLATFORM" ]; then
    # The custom mergebin target depends on firmware.bin, so it compiles and
    # merges in one SCons invocation instead of paying startup twice.
    pio_run_args+=(-t mergebin)
  fi
  if [ "$build_status" -eq 0 ]; then
    local build_output_dir="${PIO_BUILD_DIR_OVERRIDE:-${PLATFORMIO_BUILD_DIR:-.pio/build}}/${pio_env_name}"
    local build_output_log="${build_output_dir}/.meshcore-build-output.log"
    mkdir -p -- "$build_output_dir"
    pio "${pio_run_args[@]}" 2>&1 | tee "$build_output_log"
    build_status=${PIPESTATUS[0]}
    if [ "$build_status" -ne 0 ] \
        && grep -Eiq \
          'will not fit in region|region .+ overflowed by|section .+ will not fit|sketch too big|program size is greater than maximum|exceed(s|ing).*(flash|partition|app)' \
          "$build_output_log"; then
      build_status=42
    fi
  fi
  if [ "$build_status" -eq 0 ]; then
    collect_build_artifacts "$env_name" "$env_platform" "$pio_env_name" "$firmware_filename"
    build_status=$?
  fi
  if [ -n "$platformio_package_lock_fd" ]; then
    flock -u "$platformio_package_lock_fd"
    exec {platformio_package_lock_fd}>&-
  fi

  restore_platformio_build_flags "$had_platformio_build_flags" "$original_platformio_build_flags"
  unset MESHCORE_ESP32_FULL_BUILD
  unset MESHCORE_COMPANION_RADIO_FULL
  unset MESHCORE_ESP32_FULL_PARTITION_TABLE
  unset MESHCORE_NRF52_INTERNAL_BOOTLOADER_UPDATE
  unset MESHCORE_FORCE_LORA_OTA
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
  local env_name

  while IFS= read -r env_name; do
    if ! is_redundant_bulk_build_target "$env_name"; then
      printf '%s\n' "$env_name"
    fi
  done < <(get_supported_pio_envs)
}

is_legacy_companion_power_saving_target() {
  case "${1,,}" in
    *companion_radio_*_ps|*companion_radio_*_ps_*)
      return 0
      ;;
  esac
  return 1
}

is_legacy_companion_femoff_target() {
  case "${1,,}" in
    *companion_radio_*_femoff)
      return 0
      ;;
  esac
  return 1
}

is_legacy_radio_gain_profile_target() {
  # The Station G2 name only selects the persisted SX126x boosted-RX default;
  # the Station G3 name changes only ADVERT_NAME. The ordinary target supports
  # `set radio.rxgain on|off`, so neither needs a separate release artifact.
  case "${1,,}" in
    station_g2_logging_*|station_g3_esp32_logging_*)
      return 0
      ;;
  esac
  return 1
}

is_exact_companion_recipe_alias_target() {
  # These two Heltec V4 aliases extend the unsuffixed target without changing
  # any effective PlatformIO option. Other _femon names have no unsuffixed
  # target and therefore remain the canonical recipe for that hardware.
  case "${1,,}" in
    heltec_v4_companion_radio_usb_femon|heltec_v4_companion_radio_ble_femon)
      return 0
      ;;
  esac
  return 1
}

get_nrf52_full_companion_replacement() {
  local env_name=$1
  local full_env=""

  [ "${PIO_ENV_PLATFORM_BY_NAME[$env_name]:-}" = "NRF52_PLATFORM" ] || return 1
  case "${env_name,,}" in
    *companion_radio_usb*)
      full_env=${env_name/companion_radio_usb/companion_radio_full}
      ;;
    *companion_radio_ble*)
      full_env=${env_name/companion_radio_ble/companion_radio_full}
      ;;
    *companion_radio_ethernet*)
      full_env=${env_name/companion_radio_ethernet/companion_radio_full}
      ;;
    *)
      return 1
      ;;
  esac

  [ "${PIO_ENV_PLATFORM_BY_NAME[$full_env]:-}" = "NRF52_PLATFORM" ] || return 1
  printf '%s\n' "$full_env"
}

get_esp32_full_companion_replacement() {
  local source_env=$1
  local env_name=${source_env,,}
  local full_env=""

  [ "${PIO_ENV_PLATFORM_BY_NAME[$1]:-}" = "ESP32_PLATFORM" ] || return 1
  case "$env_name" in
    heltec_v3_companion_radio_wifi_mqtt|\
    heltec_v4_companion_radio_wifi_mqtt_femon|\
    heltec_v4_3_companion_radio_wifi_mqtt_femoff) ;;
    *companion_radio_wifi_mqtt*) return 1 ;;
    *companion_radio_usb*|*companion_radio_ble*|*companion_radio_wifi*|\
    *companion_radio_serial*|*companion_radio_ethernet*|*comp_radio_usb*|\
    heltec_e290_companion_ble|heltec_e290_companion_usb|\
    heltec_e290_companion_usb_ble) ;;
    *) return 1 ;;
  esac

  # Full Companion includes USB, BLE, and ordinary WiFi Companion. Map legacy
  # FEM-default aliases to the one runtime-configurable image for each physical
  # V4 display/radio layout. The expansion-kit TFT remains a distinct Full
  # recipe because it has different sensor wiring from both base display
  # layouts.
  case "$env_name" in
    heltec_v3_companion_radio_wifi_mqtt)
      full_env=Heltec_v3_companion_radio_full
      ;;
    generic_espnow_comp_radio_*)
      full_env=Generic_ESPNOW_companion_radio_full
      ;;
    heltec_e290_companion_*)
      full_env=Heltec_E290_companion_radio_full
      ;;
    heltec_t190_companion_radio_*)
      full_env=Heltec_T190_companion_radio_full_
      ;;
    sensecapindicator-espnow_comp_radio_*)
      full_env=SenseCapIndicator-ESPNow_companion_radio_full
      ;;
    sensecapindicator-lora-n16r2_comp_radio_*)
      full_env=SenseCapIndicator-LoRa-N16R2_companion_radio_full
      ;;
    sensecapindicator-lora_comp_radio_*)
      full_env=SenseCapIndicator-LoRa_companion_radio_full
      ;;
    heltec_v4_3_expansionkit_tft_companion_radio_*|\
    heltec_v4_expansionkit_tft_companion_radio_*)
      full_env=heltec_v4_expansionkit_tft_companion_radio_full_femon
      ;;
    heltec_v4_r8_tft_companion_radio_*)
      full_env=heltec_v4_r8_tft_companion_radio_full
      ;;
    heltec_v4_r8_companion_radio_*)
      full_env=heltec_v4_r8_companion_radio_full
      ;;
    heltec_v4_3_tft_companion_radio_*|heltec_v4_tft_companion_radio_*)
      full_env=heltec_v4_tft_companion_radio_full_femon
      ;;
    heltec_v4_3_companion_radio_*|heltec_v4_companion_radio_*)
      full_env=heltec_v4_2_v4_3_companion_radio_full_femon
      ;;
    *)
      case "$env_name" in
        *companion_radio_usb*)
          full_env=${source_env/companion_radio_usb/companion_radio_full}
          ;;
        *companion_radio_ble*)
          full_env=${source_env/companion_radio_ble/companion_radio_full}
          ;;
        *companion_radio_wifi*)
          full_env=${source_env/companion_radio_wifi/companion_radio_full}
          ;;
        *companion_radio_serial*)
          full_env=${source_env/companion_radio_serial/companion_radio_full}
          ;;
        *companion_radio_ethernet*)
          full_env=${source_env/companion_radio_ethernet/companion_radio_full}
          ;;
        *)
          return 1
          ;;
      esac
      ;;
  esac

  is_esp32_companion_radio_full_target "$full_env" || return 1
  printf '%s\n' "$full_env"
}

get_full_companion_replacement() {
  get_nrf52_full_companion_replacement "$1" 2>/dev/null \
    || get_esp32_full_companion_replacement "$1"
}

get_terminal_chat_full_companion_replacement() {
  local source_env=$1
  local env_name=${source_env,,}
  local full_env=""

  case "${PIO_ENV_PLATFORM_BY_NAME[$source_env]:-}" in
    ESP32_PLATFORM|NRF52_PLATFORM) ;;
    *) return 1 ;;
  esac

  # Most Terminal Chat and Full Companion targets share an exact hardware
  # prefix. These exceptions use the canonical runtime-configurable Full name
  # instead of the older revision/FEM-specific spelling.
  case "$env_name" in
    heltec_v4_terminal_chat)
      full_env=heltec_v4_2_v4_3_companion_radio_full_femon
      ;;
    heltec_v4_tft_terminal_chat)
      full_env=heltec_v4_tft_companion_radio_full_femon
      ;;
    heltec_tracker_v2_terminal_chat)
      full_env=heltec_tracker_v2_companion_radio_full_femon
      ;;
    *terminal_chat*)
      full_env=${source_env/terminal_chat/companion_radio_full}
      ;;
    *)
      return 1
      ;;
  esac

  is_companion_radio_full_target "$full_env" || return 1
  printf '%s\n' "$full_env"
}

get_terminal_chat_usb_companion_replacement() {
  local source_env=$1
  local env_name=${source_env,,}
  local usb_env=""

  case "$env_name" in
    generic_espnow_terminal_chat)
      usb_env=Generic_ESPNOW_comp_radio_usb
      ;;
    *terminal_chat*)
      usb_env=${source_env/terminal_chat/companion_radio_usb}
      ;;
    *)
      return 1
      ;;
  esac

  [ -n "${PIO_ENV_PLATFORM_BY_NAME[$usb_env]+x}" ] || return 1
  [ "${PIO_ENV_PLATFORM_BY_NAME[$usb_env]:-}" = "${PIO_ENV_PLATFORM_BY_NAME[$source_env]:-}" ] \
    || return 1
  [ "${PIO_ENV_BOARD_BY_NAME[$usb_env]:-}" = "${PIO_ENV_BOARD_BY_NAME[$source_env]:-}" ] \
    || return 1
  printf '%s\n' "$usb_env"
}

get_terminal_chat_companion_replacement() {
  get_terminal_chat_full_companion_replacement "$1" 2>/dev/null \
    || get_terminal_chat_usb_companion_replacement "$1"
}

get_combined_usb_ble_companion_replacement() {
  case "${1,,}" in
    heltec_e290_companion_ble|heltec_e290_companion_usb)
      printf '%s\n' Heltec_E290_companion_usb_ble
      ;;
    heltec_t190_companion_radio_ble_|heltec_t190_companion_radio_usb_)
      printf '%s\n' Heltec_T190_companion_radio_usb_ble_
      ;;
    *)
      return 1
      ;;
  esac
}

get_merged_rs232_repeater_replacement() {
  case "${1,,}" in
    heltec_t096_repeater_bridge_rs232)
      printf '%s\n' Heltec_t096_repeater
      ;;
    heltec_t096_repeater_bridge_rs232_lora_ota_no_external_sensors)
      printf '%s\n' Heltec_t096_repeater_lora_ota_no_external_sensors
      ;;
    rak_4631_repeater_bridge_rs232_serial1_lora_ota_no_external_sensors|\
    rak_4631_repeater_bridge_rs232_serial2_lora_ota_no_external_sensors)
      printf '%s\n' RAK_4631_repeater_lora_ota_no_external_sensors
      ;;
    rak_4631_repeater_bridge_rs232_serial1|\
    rak_4631_repeater_bridge_rs232_serial2)
      printf '%s\n' RAK_4631_repeater
      ;;
    promicro_repeater_bridge_rs232_serial1)
      printf '%s\n' ProMicro_repeater
      ;;
    heltec_t114_without_display_repeater_bridge_rs232)
      printf '%s\n' Heltec_t114_without_display_repeater
      ;;
    heltec_t114_repeater_bridge_rs232)
      printf '%s\n' Heltec_t114_repeater
      ;;
    rak_3112_repeater_bridge_rs232)
      printf '%s\n' RAK_3112_repeater
      ;;
    rak_11310_repeater_bridge_rs232)
      printf '%s\n' RAK_11310_repeater
      ;;
    waveshare_rp2040_lora_repeater_bridge_rs232)
      printf '%s\n' waveshare_rp2040_lora_repeater
      ;;
    solarxiao_30s_repeater_bridge_rs232)
      printf '%s\n' solarxiao_30S_repeater
      ;;
    solarxiao_33s_repeater_bridge_rs232)
      printf '%s\n' solarxiao_33S_repeater
      ;;
    heltec_v3_repeater_bridge_rs232)
      printf '%s\n' Heltec_v3_repeater
      ;;
    heltec_wsl3_repeater_bridge_rs232)
      printf '%s\n' Heltec_WSL3_repeater
      ;;
    lilygo_tlora_v2_1_1_6_repeater_bridge_rs232)
      printf '%s\n' LilyGo_TLora_V2_1_1_6_repeater
      ;;
    *)
      # Wio-E5 intentionally remains separate: its normal image has only 916
      # bytes free, and the measured combined image exceeds its 240 KiB app
      # partition by 2,192 bytes unless the normal USB/MQTT host CLI is removed.
      return 1
      ;;
  esac
}

is_firmware_role_replaced_by_canonical_artifact() {
  get_full_companion_replacement "$1" >/dev/null 2>&1 \
    || get_terminal_chat_companion_replacement "$1" >/dev/null \
    || get_combined_usb_ble_companion_replacement "$1" >/dev/null \
    || get_merged_rs232_repeater_replacement "$1" >/dev/null
}

is_runtime_setting_alias_target() {
  if is_legacy_companion_power_saving_target "$1" \
      || is_legacy_companion_femoff_target "$1" \
      || is_legacy_radio_gain_profile_target "$1" \
      || is_exact_companion_recipe_alias_target "$1"; then
    return 0
  fi
  case "${1,,}" in
    ikoka_handheld_nrf_e22_30dbm_096_rotated_companion_radio_full)
      return 0
      ;;
  esac
  return 1
}

is_redundant_bulk_build_target() {
  # Keep every legacy name available to `build-firmware` and
  # `build-matching-firmwares`, but do not republish binaries that differ only
  # by a saved/default setting, or roles already supplied by Full Companion.
  # Its text terminal supersedes standalone Terminal Chat on the same exact
  # hardware, in addition to its combined attached transports. Deployed mOTA
  # IDs are wire compatibility contracts, so their exact targets are the one
  # intentional exception to functional artifact consolidation.
  if is_deployed_lora_ota_compatibility_target "$1"; then
    return 1
  fi
  if is_runtime_setting_alias_target "$1" \
      || is_firmware_role_replaced_by_canonical_artifact "$1"; then
    return 0
  fi
  return 1
}

resolve_logging_matrix_firmwares() {
  # Full Companion replaces normal transport and USB-logging artifacts. It
  # either exposes separate framed/logging CDC interfaces or safely switches a
  # single TTY between Binary Companion and the plaintext logging terminal.
  resolve_all_firmwares
}

resolve_companion_firmwares() {
  local env_name

  while IFS= read -r env_name; do
    if ! is_redundant_bulk_build_target "$env_name"; then
      printf '%s\n' "$env_name"
    fi
  done < <(get_pio_envs_for_variant_role companion)
}

resolve_all_companion_firmwares() {
  resolve_companion_firmwares
}

resolve_companion_logging_matrix_firmwares() {
  resolve_all_companion_firmwares
}

resolve_full_companion_firmwares() {
  local env_name

  for env_name in "${SUPPORTED_PIO_ENVS[@]}"; do
    if is_supported_build_env "$env_name" \
        && is_companion_radio_full_target "$env_name"; then
      if is_redundant_bulk_build_target "$env_name"; then
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
      echo "resolve_logging_matrix_firmwares"
      ;;
    build-companion-firmwares-logging-matrix)
      echo "resolve_companion_logging_matrix_firmwares"
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

  OTA_EXCLUDED_TARGETS=()
  if [ -z "$REQUIRE_OTA_UPDATES" ]; then
    REQUIRE_OTA_UPDATES=0
    if is_logging_matrix_command "$1"; then REQUIRE_OTA_UPDATES=1; fi
  fi
  if [ "$REQUIRE_OTA_UPDATES" = "1" ]; then
    local ota_targets=()
    for target in "${RESOLVED_BUILD_TARGETS[@]}"; do
      if is_companion_build "$target"; then
        ota_targets+=("$target")
        continue
      fi
      case "${PIO_ENV_PLATFORM_BY_NAME[$target]:-}" in
        ESP32_PLATFORM|NRF52_PLATFORM) ota_targets+=("$target") ;;
        *) OTA_EXCLUDED_TARGETS+=("$target") ;;
      esac
    done
    if [ ${#OTA_EXCLUDED_TARGETS[@]} -gt 0 ]; then
      echo "No wireless updater exists for these cable-only targets:"
      printf '  %s\n' "${OTA_EXCLUDED_TARGETS[@]}"
      if [ "$1" = "build-firmware" ]; then return 1; fi
    fi
    RESOLVED_BUILD_TARGETS=("${ota_targets[@]}")
    [ ${#RESOLVED_BUILD_TARGETS[@]} -gt 0 ] || return 1
    echo "OTA required: infrastructure must prove a WiFi, Bluetooth, or LoRa self-update path; Companions may update over USB."
  fi

  # Keep one queue so parallel workers stay saturated. The scheduler may pull
  # a later target forward when a generated alias shares an active PlatformIO
  # base environment, so this is a best-effort start order rather than a phase
  # barrier or completion-order guarantee.
  if [ "$1" != "build-firmware" ]; then
    mapfile -t RESOLVED_BUILD_TARGETS < <(
      sort_build_targets_by_platform_and_name "${RESOLVED_BUILD_TARGETS[@]}"
    )
    echo "Bulk target order: nRF52, ESP32, RP2040, STM32; alphabetical within each platform, one PlatformIO process at a time."
  fi
}

configure_effective_build_profile() {
  local command_name=$1
  local target=""
  local reduced_target=""

  if [ "${#RESOLVED_BUILD_TARGETS[@]}" -eq 1 ]; then
    target=${RESOLVED_BUILD_TARGETS[0]}
  fi

  AUTO_PREFER_FULL_BUILD=0
  AUTO_COMPLETE_FIRST_PASS=0
  AUTO_REDUCED_FALLBACK_TARGET=""
  AUTO_PUBLISH_REDUCED_SECOND_PASS=0

  case "${BUILD_PROFILE_OVERRIDE,,}" in
    auto|standard|full) ;;
    *)
      echo "Invalid build profile: ${BUILD_PROFILE_OVERRIDE} (use auto, standard, or full)"
      return 1
      ;;
  esac

  if [ "$SINGLE_TARGET_FULL_BUILD" = "1" ]; then
    BUILD_PROFILE_EFFECTIVE="full"
  elif is_automatic_profile_command "$command_name"; then
    if [ "$BUILD_PROFILE_EXPLICIT" = "1" ] \
        && [ "${BUILD_PROFILE_OVERRIDE,,}" != "auto" ]; then
      echo "${command_name} manages standard and FULL profiles itself; do not combine it with --build-profile ${BUILD_PROFILE_OVERRIDE}."
      return 1
    fi
    BUILD_PROFILE_EFFECTIVE="standard"
  else
    case "${BUILD_PROFILE_OVERRIDE,,}" in
      auto)
        if [ "$command_name" = "build-firmware" ] \
            && [ "${#RESOLVED_BUILD_TARGETS[@]}" -eq 1 ]; then
          if is_companion_radio_full_target "$target"; then
            BUILD_PROFILE_EFFECTIVE="full"
          elif supports_esp32_full_build "$target"; then
            BUILD_PROFILE_EFFECTIVE="full"
            AUTO_PREFER_FULL_BUILD=1
            AUTO_REDUCED_FALLBACK_TARGET=$(get_reduced_lora_ota_target "$target" || true)
          elif is_lora_ota_no_external_sensors_target "$target"; then
            # An explicitly selected legacy-named reduced target keeps its
            # exact declared driver trim; do not silently put omitted optional
            # environmental/ranging drivers back into it.
            BUILD_PROFILE_EFFECTIVE="standard"
          elif reduced_target=$(get_reduced_lora_ota_target "$target"); then
            RESOLVED_BUILD_TARGETS[0]=$reduced_target
            BUILD_PROFILE_EFFECTIVE="auto"
            AUTO_COMPLETE_FIRST_PASS=1
            AUTO_REDUCED_FALLBACK_TARGET=$reduced_target
            if [ "${PIO_ENV_PLATFORM_BY_NAME[$target]:-}" = "NRF52_PLATFORM" ] \
                && [ "${PIO_ENV_SD_OTA_BY_NAME[$reduced_target]:-0}" = "0" ] \
                && [ "${PIO_ENV_QSPI_OTA_BY_NAME[$reduced_target]:-0}" = "0" ]; then
              AUTO_PUBLISH_REDUCED_SECOND_PASS=1
            fi
            echo "Auto pass 1 selected: complete LoRa OTA for ${reduced_target}; reductions are deferred unless it is too large."
          else
            # Companion and non-repeater targets without an expanded recipe
            # keep every capability declared by their target and are checked
            # against the current partition.
            BUILD_PROFILE_EFFECTIVE="auto"
          fi
        else
          # Canonical and matching builds are release-oriented. Preserve their
          # deployed partition contract unless FULL was explicitly requested.
          BUILD_PROFILE_EFFECTIVE="standard"
        fi
        ;;
      standard)
        BUILD_PROFILE_EFFECTIVE="standard"
        ;;
      full)
        if [ "$command_name" != "build-firmware" ] \
            || [ "${#RESOLVED_BUILD_TARGETS[@]}" -ne 1 ]; then
          echo "--build-profile full requires exactly one build-firmware target; use a build-full-* command for a matrix."
          return 1
        fi
        if is_companion_radio_full_target "$target"; then
          BUILD_PROFILE_EFFECTIVE="full"
        elif supports_esp32_full_build "$target"; then
          BUILD_PROFILE_EFFECTIVE="full"
          SINGLE_TARGET_FULL_BUILD=1
        else
          echo "Target ${target} has no expanded FULL partition profile. Use --build-profile auto or standard."
          return 1
        fi
        ;;
    esac
  fi

  case "$BUILD_PROFILE_EFFECTIVE" in
    auto)
      echo "Build profile: auto (keep target capabilities; enforce its current partition)."
      ;;
    standard)
      echo "Build profile: standard (preserve the portable/deployed partition contract; documented reductions may apply)."
      ;;
    full)
      if [ "${PIO_ENV_PLATFORM_BY_NAME[$target]:-}" = "ESP32_PLATFORM" ]; then
        echo "Build profile: full (expanded partition; a matching merged image is required when the partition table changes)."
      else
        echo "Build profile: full (feature-complete named target in its existing application layout)."
      fi
      ;;
  esac
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
  # Never overlap PlatformIO processes in this checkout: environments share
  # .pio/build and can clean one another's objects.
  local worker_limit=1
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
      if [ "$build_status" -eq 42 ] \
          && [ "${DEFER_ESP32_PORTABLE_OVERFLOW_TO_FULL:-0}" = "1" ] \
          && [ "$profile" = "standard" ] \
          && has_esp32_full_profile "$env"; then
        LOGGING_MATRIX_DEFERRED_TARGETS+=("$env")
        echo "DEFERRED: ${env} (${profile}) exceeds the portable OTA slot; the expanded FULL pass is required."
        echo "DEFERRED: ${env} (${profile}) exceeds the portable OTA slot; the expanded FULL pass is required." >> "$log_path"
      elif [ "$build_status" -ne 0 ]; then
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
    if [ "$build_status" -eq 42 ] \
        && [ "${DEFER_ESP32_PORTABLE_OVERFLOW_TO_FULL:-0}" = "1" ] \
        && [ "$profile" = "standard" ] \
        && has_esp32_full_profile "$env"; then
      LOGGING_MATRIX_DEFERRED_TARGETS+=("$env")
      echo "DEFERRED: ${env} (${profile}) exceeds the portable OTA slot; the expanded FULL pass is required."
      echo "DEFERRED: ${env} (${profile}) exceeds the portable OTA slot; the expanded FULL pass is required." >> "$log_path"
    elif [ "$build_status" -ne 0 ]; then
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

get_esp32_full_profile_target() {
  local target=$1

  # Synthetic reduced LoRa-OTA aliases share an expanded FULL replacement
  # with their complete base repeater. Resolve that base before looking for
  # MQTT/unified siblings so a portable overflow is deferred only when the
  # replacement can actually be built.
  if is_lora_ota_only_target "$target"; then
    local complete_target=${PIO_ENV_COMPLETE_OTA_BASE_BY_NAME[$target]:-${target%_lora_ota_no_external_sensors}}
    if is_supported_build_env "$complete_target"; then
      target=$complete_target
    fi
  fi
  echo "$target"
}

has_esp32_full_profile() {
  local target
  local candidate=""

  target=$(get_esp32_full_profile_target "$1")
  candidate=$(get_mqtt_enabled_target "$target") || candidate=""
  if [ -n "$candidate" ] && supports_esp32_full_build "$candidate"; then
    return 0
  fi
  candidate=$(get_mqtt_disabled_target "$target") || candidate=""
  [ -n "$candidate" ] && supports_esp32_full_build "$candidate"
}

run_full_esp32_profile() {
  local profile_label=$1
  local profile_mode=$2
  shift 2
  local targets=("$@")
  local target
  local full_profile_target
  local full_target
  local mqtt_target
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
    full_profile_target=$(get_esp32_full_profile_target "$target")
    full_target=""
    if [ "$profile_mode" = "fallback" ]; then
      # A matching MQTT environment is emitted once by the unified profile;
      # do not also build its former non-MQTT FULL-logging twin.
      mqtt_target=$(get_mqtt_enabled_target "$full_profile_target") || mqtt_target=""
      if [ -n "$mqtt_target" ] && supports_esp32_full_build "$mqtt_target"; then
        continue
      fi
      full_target=$(get_mqtt_disabled_target "$full_profile_target") || full_target=""
    else
      full_target=$(get_mqtt_enabled_target "$full_profile_target") || full_target=""
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
    if [ "$profile_mode" = "fallback" ]; then
      echo "${profile_label}: every FULL target has a unified WiFi MQTT profile; no logging-only fallback is needed."
    else
      echo "${profile_label}: no WiFi MQTT ESP32 FULL targets resolved; skipping."
    fi
    return 0
  fi

  if [ "$profile_mode" = "fallback" ]; then
    echo "${profile_label}: building ${#full_targets[@]} feature-complete ESP32 fallback target(s) with up to ${ESP32_FULL_MAX_NEIGHBOURS} neighbors (target DRAM limits apply), USB logging on, no available WiFi MQTT sibling, and expanded dual-OTA partitions."
    echo "Fallback artifacts include LoRa OTA and use filename form: name-full-logging-ota-version."
    MESHDEBUG_OVERRIDE="on"
    PACKET_LOGGING_OVERRIDE="on"
    MQTT_BRIDGE_OVERRIDE="off"
    FIRMWARE_FILENAME_INFIX="full-logging"
  else
    echo "${profile_label}: building ${#full_targets[@]} unified feature-complete ESP32 target(s) with up to ${ESP32_FULL_MAX_NEIGHBOURS} neighbors (target DRAM limits apply), USB packet logging, direct WiFi MQTT, and expanded dual-OTA partitions."
    echo "Unified FULL artifacts include LoRa OTA, keep verbose debug off, and use filename form: name-full-usb-wifi-ota-version."
    MESHDEBUG_OVERRIDE="off"
    PACKET_LOGGING_OVERRIDE="on"
    MQTT_BRIDGE_OVERRIDE="on"
    FIRMWARE_FILENAME_INFIX="full-usb-wifi"
  fi
  echo "Flash the matching merged image once to install the expanded partition table."
  MQTT_DEBUG_OVERRIDE="off"
  ESP32_FULL_BUILD=1

  run_logged_build_targets "${full_targets[@]}"
  pass_status=$?
  if [ "$pass_status" -eq 130 ]; then
    build_status=130
  elif [ "$pass_status" -ne 0 ]; then
    build_status=1
  fi

  MESHDEBUG_OVERRIDE=$original_meshdebug_override
  PACKET_LOGGING_OVERRIDE=$original_packet_logging_override
  MQTT_BRIDGE_OVERRIDE=$original_mqtt_bridge_override
  MQTT_DEBUG_OVERRIDE=$original_mqtt_debug_override
  FIRMWARE_FILENAME_INFIX=$original_firmware_filename_infix
  ESP32_FULL_BUILD=$original_esp32_full_build

  return "$build_status"
}

run_full_esp32_build_targets() {
  local profile_mode=$1
  shift
  local targets=("$@")
  local profile_name="FULL unified"
  local build_status=0
  local pass_status=0

  if [ "$profile_mode" = "fallback" ]; then
    profile_name="FULL logging fallback"
  fi

  LOGGING_MATRIX_FAILURES=()
  if [ "$profile_mode" = "fallback" ]; then
    run_full_esp32_profile "${profile_name}-only build" "fallback" "${targets[@]}"
    build_status=$?
  else
    run_full_esp32_profile "${profile_name} build" "unified" "${targets[@]}"
    pass_status=$?
    if [ "$pass_status" -eq 130 ]; then return 130; fi
    if [ "$pass_status" -ne 0 ]; then build_status=1; fi

    run_full_esp32_profile "FULL logging fallback build" "fallback" "${targets[@]}"
    pass_status=$?
    if [ "$pass_status" -eq 130 ]; then return 130; fi
    if [ "$pass_status" -ne 0 ]; then build_status=1; fi
  fi

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
  local original_meshdebug_override=$MESHDEBUG_OVERRIDE
  local original_packet_logging_override=$PACKET_LOGGING_OVERRIDE
  local original_mqtt_bridge_override=$MQTT_BRIDGE_OVERRIDE
  local original_mqtt_debug_override=$MQTT_DEBUG_OVERRIDE
  local original_firmware_filename_infix=$FIRMWARE_FILENAME_INFIX
  local original_esp32_full_build=$ESP32_FULL_BUILD
  local original_profile_build_workers=$PROFILE_BUILD_WORKERS
  local full_only_standard_skip_count=0
  local merged_usb_logging_count=0
  local constrained_merged_logging_count=0
  local build_status=0
  local pass_status=0
  local DEFER_ESP32_PORTABLE_OVERFLOW_TO_FULL=0

  if [ ${#targets[@]} -eq 0 ]; then
    echo "No build targets resolved."
    return 1
  fi
  LOGGING_MATRIX_FAILURES=()
  LOGGING_MATRIX_DEFERRED_TARGETS=()
  if [ "${REQUIRE_OTA_UPDATES:-0}" = "1" ]; then
    printf '%s\n' "${OTA_EXCLUDED_TARGETS[@]}" > "${OUTPUT_DIR}/ota-excluded-targets.txt"
  fi
  PROFILE_BUILD_WORKERS=$OPTION3_BUILD_WORKERS
  echo "Option 3 PlatformIO policy: one target build at a time, ${OPTION3_PIO_JOBS} compiler job(s) inside that process."

  for target in "${targets[@]}"; do
    if is_mqtt_bridge_target "$target"; then
      continue
    fi
    if requires_esp32_full_cli_profile "$target"; then
      full_only_standard_skip_count=$((full_only_standard_skip_count + 1))
    else
      standard_targets+=("$target")
      if uses_merged_standard_usb_logging "$target"; then
        merged_usb_logging_count=$((merged_usb_logging_count + 1))
        if is_logging_size_constrained_target "$target"; then
          constrained_merged_logging_count=$((constrained_merged_logging_count + 1))
        fi
      fi
    fi
  done

  echo "Profile 1/2: building ${#standard_targets[@]} standard target(s); ${merged_usb_logging_count} embed runtime-controlled USB logging in the ordinary artifact."
  if [ "$constrained_merged_logging_count" -gt 0 ]; then
    echo "Keeping verbose MESH_DEBUG off for ${constrained_merged_logging_count} size-constrained STM32 target(s); packet logging remains available at runtime."
  fi
  if [ "$full_only_standard_skip_count" -gt 0 ]; then
    echo "Deferring ${full_only_standard_skip_count} ESP32 ESP-NOW target(s) to their FULL logging fallback; its persistent USB gate also provides normal output-off operation."
  fi
  ESP32_FULL_BUILD=0
  MESHDEBUG_OVERRIDE=""
  PACKET_LOGGING_OVERRIDE=""
  MQTT_BRIDGE_OVERRIDE="off"
  FIRMWARE_FILENAME_INFIX=""
  if [ ${#standard_targets[@]} -gt 0 ]; then
    DEFER_ESP32_PORTABLE_OVERFLOW_TO_FULL=1
    run_logged_build_targets "${standard_targets[@]}"
    pass_status=$?
    DEFER_ESP32_PORTABLE_OVERFLOW_TO_FULL=0
    if [ "$pass_status" -eq 130 ]; then return 130; fi
    if [ "$pass_status" -ne 0 ]; then build_status=1; fi
  fi

  run_full_esp32_profile "FULL unified pass" "unified" "${targets[@]}"
  pass_status=$?
  if [ "$pass_status" -eq 130 ]; then return 130; fi
  if [ "$pass_status" -ne 0 ]; then build_status=1; fi

  run_full_esp32_profile "FULL logging fallback pass" "fallback" "${targets[@]}"
  pass_status=$?
  if [ "$pass_status" -eq 130 ]; then return 130; fi
  if [ "$pass_status" -ne 0 ]; then build_status=1; fi

  MESHDEBUG_OVERRIDE=$original_meshdebug_override
  PACKET_LOGGING_OVERRIDE=$original_packet_logging_override
  MQTT_BRIDGE_OVERRIDE=$original_mqtt_bridge_override
  MQTT_DEBUG_OVERRIDE=$original_mqtt_debug_override
  FIRMWARE_FILENAME_INFIX=$original_firmware_filename_infix
  ESP32_FULL_BUILD=$original_esp32_full_build
  PROFILE_BUILD_WORKERS=$original_profile_build_workers

  if [ ${#LOGGING_MATRIX_DEFERRED_TARGETS[@]} -gt 0 ]; then
    echo "${#LOGGING_MATRIX_DEFERRED_TARGETS[@]} standard ESP32 target(s) exceeded the portable OTA slot and were deferred to the expanded FULL pass:"
    printf '  %s\n' "${LOGGING_MATRIX_DEFERRED_TARGETS[@]}"
  fi
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

run_auto_two_pass_build() {
  local target=$1
  local fallback_target=$AUTO_REDUCED_FALLBACK_TARGET
  local original_esp32_full_build=$ESP32_FULL_BUILD
  local original_build_profile_effective=$BUILD_PROFILE_EFFECTIVE
  local original_firmware_filename_infix=$FIRMWARE_FILENAME_INFIX
  local original_skip_declared_reductions=$SKIP_DECLARED_REDUCTIONS
  local original_complete_ota_first_pass=$COMPLETE_OTA_FIRST_PASS
  local original_firmware_output_env_name=$FIRMWARE_OUTPUT_ENV_NAME
  local build_status=0

  if [ "$AUTO_PREFER_FULL_BUILD" = "1" ]; then
    ESP32_FULL_BUILD=1
    BUILD_PROFILE_EFFECTIVE="full"
    FIRMWARE_FILENAME_INFIX="full"
    SKIP_DECLARED_REDUCTIONS=0
    COMPLETE_OTA_FIRST_PASS=0
    FIRMWARE_OUTPUT_ENV_NAME=""
    echo "Auto pass 1/2: building the complete expanded-partition profile for ${target}."
  else
    ESP32_FULL_BUILD=0
    BUILD_PROFILE_EFFECTIVE="auto"
    FIRMWARE_FILENAME_INFIX="full-ota"
    SKIP_DECLARED_REDUCTIONS=1
    COMPLETE_OTA_FIRST_PASS=1
    if [ "$AUTO_PUBLISH_REDUCED_SECOND_PASS" = "1" ]; then
      FIRMWARE_OUTPUT_ENV_NAME=${PIO_ENV_COMPLETE_OTA_BASE_BY_NAME[$target]:-${target%_lora_ota_no_external_sensors}}
    else
      FIRMWARE_OUTPUT_ENV_NAME=""
    fi
    echo "Auto pass 1/2: building complete LoRa OTA for ${target} without declared feature reductions."
  fi

  if build_firmware "$target"; then
    build_status=0
  else
    build_status=$?
  fi

  if [ "$build_status" -eq 42 ]; then
    if [ -z "$fallback_target" ]; then
      echo "The complete image is too large and ${target} has no supported reduced LoRa OTA recipe."
      build_status=1
    else
      echo "Auto pass 1 exceeded measured flash/partition capacity; pass 2 is rebuilding ${fallback_target} with the standard reduced LoRa OTA recipe."
      ESP32_FULL_BUILD=0
      BUILD_PROFILE_EFFECTIVE="standard"
      FIRMWARE_FILENAME_INFIX="reduced-ota"
      SKIP_DECLARED_REDUCTIONS=0
      COMPLETE_OTA_FIRST_PASS=0
      FIRMWARE_OUTPUT_ENV_NAME=""
      if build_firmware "$fallback_target"; then
        build_status=0
      else
        build_status=$?
      fi
      if [ "$build_status" -eq 0 ]; then
        echo "Auto pass 2 succeeded: ${fallback_target} (reduced LoRa OTA)."
      fi
    fi
  elif [ "$build_status" -ne 0 ]; then
    echo "Auto pass 1 failed for a non-size reason; refusing to hide the compiler or capability failure with a reduced build."
  elif [ "$AUTO_PUBLISH_REDUCED_SECOND_PASS" = "1" ] \
      && [ -n "$fallback_target" ]; then
    echo "Auto pass 1 succeeded. Internal-flash nRF52 repeater policy also publishes a reduced LoRa OTA image with more delta-staging headroom."
    ESP32_FULL_BUILD=0
    BUILD_PROFILE_EFFECTIVE="standard"
    FIRMWARE_FILENAME_INFIX="reduced-ota"
    SKIP_DECLARED_REDUCTIONS=0
    COMPLETE_OTA_FIRST_PASS=0
    FIRMWARE_OUTPUT_ENV_NAME=""
    if build_firmware "$fallback_target"; then
      build_status=0
      echo "Auto pass 2 succeeded: ${fallback_target} (reduced LoRa OTA)."
    else
      build_status=$?
    fi
  else
    echo "Auto pass 1 succeeded; no reductions were needed."
  fi

  ESP32_FULL_BUILD=$original_esp32_full_build
  BUILD_PROFILE_EFFECTIVE=$original_build_profile_effective
  FIRMWARE_FILENAME_INFIX=$original_firmware_filename_infix
  SKIP_DECLARED_REDUCTIONS=$original_skip_declared_reductions
  COMPLETE_OTA_FIRST_PASS=$original_complete_ota_first_pass
  FIRMWARE_OUTPUT_ENV_NAME=$original_firmware_output_env_name
  return "$build_status"
}

run_command() {
  # All build commands share execution after validation resolves their target list.
  if [ "$AUTO_PREFER_FULL_BUILD" = "1" ] \
      || [ "$AUTO_COMPLETE_FIRST_PASS" = "1" ]; then
    run_auto_two_pass_build "${RESOLVED_BUILD_TARGETS[0]}"
    return $?
  fi

  if [ "$SINGLE_TARGET_FULL_BUILD" = "1" ]; then
    run_full_esp32_build_targets "all" "${RESOLVED_BUILD_TARGETS[@]}"
    return $?
  fi

  if is_logging_matrix_command "$1"; then
    run_logging_matrix_build_targets "${RESOLVED_BUILD_TARGETS[@]}"
    return $?
  fi

  if is_full_esp32_command "$1"; then
    run_full_esp32_build_targets "all" "${RESOLVED_BUILD_TARGETS[@]}"
    return $?
  fi

  if is_full_esp32_logging_command "$1"; then
    run_full_esp32_build_targets "fallback" "${RESOLVED_BUILD_TARGETS[@]}"
    return $?
  fi

  if is_build_command "$1"; then
    run_resolved_build_targets "${RESOLVED_BUILD_TARGETS[@]}"
    return $?
  fi

  global_usage
  exit 1
}

get_build_project_dir() {
  local script_path=${BASH_SOURCE[0]}
  local script_dir

  if [[ "$script_path" != /* ]]; then
    script_path="${PWD}/${script_path}"
  fi
  script_dir=${script_path%/*}
  (cd -- "$script_dir" && pwd -P)
}

get_background_build_dir() {
  local base_dir=${1:-$PWD}
  local configured_dir=$BUILD_BACKGROUND_DIR

  if [ -z "$configured_dir" ]; then
    configured_dir="${OUTPUT_DIR%/}.background-builds"
  fi
  if [[ "$configured_dir" != /* ]]; then
    configured_dir="${base_dir}/${configured_dir}"
  fi
  printf '%s\n' "$configured_dir"
}

canonicalize_background_build_dir() {
  local configured_dir=$1
  local canonical_dir

  if [ -z "$configured_dir" ] || [ "$configured_dir" = "/" ]; then
    echo "Refusing unsafe background build directory: ${configured_dir}" >&2
    return 1
  fi
  mkdir -p -- "$configured_dir" || return $?
  canonical_dir=$(cd -- "$configured_dir" && pwd -P) || return $?
  if [ -z "$canonical_dir" ] || [ "$canonical_dir" = "/" ]; then
    echo "Refusing unsafe canonical background build directory: ${canonical_dir}" >&2
    return 1
  fi
  printf '%s\n' "$canonical_dir"
}

write_background_build_status() {
  local state=$1
  local exit_code=${2:-}
  local status_tmp="${BUILD_BACKGROUND_STATUS_FILE}.$$"

  {
    printf 'job=%s\n' "$BUILD_BACKGROUND_JOB_ID"
    printf 'state=%s\n' "$state"
    printf 'backend=%s\n' "$BUILD_BACKGROUND_BACKEND"
    printf 'pid=%s\n' "$$"
    printf 'started=%s\n' "$BUILD_BACKGROUND_STARTED_AT"
    printf 'updated=%s\n' "$(date --iso-8601=seconds)"
    if [ -n "$exit_code" ]; then
      printf 'exit_code=%s\n' "$exit_code"
    fi
    printf 'working_directory=%s\n' "$PWD"
    printf 'output_directory=%s\n' "$OUTPUT_DIR"
    printf 'source_commit=%s\n' "$(git rev-parse HEAD)"
    printf 'firmware_version=%s\n' "${FIRMWARE_VERSION:-}"
    printf 'firmware_profile=%s\n' "$FIRMWARE_PROFILE_OVERRIDE"
    printf 'radio_frequency=%s\n' "$RADIO_FREQ_OVERRIDE"
    printf 'radio_bandwidth=%s\n' "$RADIO_BW_OVERRIDE"
    printf 'radio_sf=%s\n' "$RADIO_SF_OVERRIDE"
    printf 'radio_cr=%s\n' "$RADIO_CR_OVERRIDE"
    printf 'log=%s\n' "$BUILD_BACKGROUND_LOG_FILE"
  } > "$status_tmp"
  mv -f -- "$status_tmp" "$BUILD_BACKGROUND_STATUS_FILE"
}

finish_background_build() {
  local exit_code=$1
  local state="failed"

  if [ "$exit_code" -eq 0 ]; then
    state="completed"
  fi
  write_background_build_status "$state" "$exit_code"
  echo "Background build ${BUILD_BACKGROUND_JOB_ID} ${state} (exit ${exit_code})."
  echo "Log: ${BUILD_BACKGROUND_LOG_FILE}"
  return 0
}

setup_background_build_worker() {
  if [ "$BUILD_BACKGROUND_ACTIVE" != "1" ]; then
    return 0
  fi
  if ! [[ "$BUILD_BACKGROUND_JOB_ID" =~ ^meshcore-build-[A-Za-z0-9_.@-]+$ ]]; then
    echo "Invalid background build job id: ${BUILD_BACKGROUND_JOB_ID}" >&2
    return 1
  fi
  BUILD_BACKGROUND_DIR=$(canonicalize_background_build_dir "$BUILD_BACKGROUND_DIR") \
    || return $?
  BUILD_BACKGROUND_LOG_FILE="${BUILD_BACKGROUND_DIR}/${BUILD_BACKGROUND_JOB_ID}.log"
  BUILD_BACKGROUND_STATUS_FILE="${BUILD_BACKGROUND_DIR}/${BUILD_BACKGROUND_JOB_ID}.status"
  BUILD_BACKGROUND_STARTED_AT=$(date --iso-8601=seconds)
  : >> "$BUILD_BACKGROUND_LOG_FILE" || return $?
  exec > >(tee -a -- "$BUILD_BACKGROUND_LOG_FILE") 2>&1
  write_background_build_status "starting"
  echo "Background build: ${BUILD_BACKGROUND_JOB_ID}"
  echo "Working directory: ${PWD}"
  echo "Log: ${BUILD_BACKGROUND_LOG_FILE}"
}

background_systemd_available() {
  command -v systemd-run >/dev/null 2>&1 \
    && command -v systemctl >/dev/null 2>&1 \
    && systemctl --user show-environment >/dev/null 2>&1
}

read_background_build_state() {
  local status_file=$1
  local key value

  if ! [ -s "$status_file" ]; then
    return 1
  fi
  while IFS='=' read -r key value; do
    if [ "$key" = "state" ]; then
      printf '%s\n' "$value"
      return 0
    fi
  done < "$status_file"
  return 1
}

wait_for_background_worker_handoff() {
  local status_file=$1
  local max_attempts=${2:-100}
  local attempt state=""

  BUILD_BACKGROUND_HANDOFF_STATE=""
  for ((attempt = 0; attempt < max_attempts; attempt++)); do
    state=$(read_background_build_state "$status_file" 2>/dev/null) || state=""
    case "$state" in
      running|completed)
        BUILD_BACKGROUND_HANDOFF_STATE=$state
        return 0
        ;;
      failed)
        BUILD_BACKGROUND_HANDOFF_STATE=$state
        return 1
        ;;
    esac
    sleep 0.1
  done
  BUILD_BACKGROUND_HANDOFF_STATE="timeout"
  return 124
}

launch_background_build() {
  local project_dir script_path background_dir command_name timestamp job_id
  local variable_name variable_value handoff_status
  local status_file
  local -a worker_environment=()
  local -a forwarded_environment=(
    PATH HOME USER LOGNAME SHELL LANG LC_ALL LC_CTYPE TMPDIR VIRTUAL_ENV
    XDG_CACHE_HOME XDG_CONFIG_HOME
    PLATFORMIO_CORE_DIR PLATFORMIO_BUILD_DIR PLATFORMIO_BUILD_FLAGS
    PLATFORMIO_BUILD_UNFLAGS PLATFORMIO_BUILD_SRC_FILTER
    PLATFORMIO_EXTRA_SCRIPTS DISABLE_DEBUG OPTION3_PIO_JOBS OUTPUT_DIR
    FIRMWARE_VERSION FIRMWARE_BUILD_NUMBER BUILD_PROFILE_OVERRIDE
    BUILD_PROFILE_EXPLICIT SINGLE_TARGET_FULL_BUILD FIRMWARE_PROFILE_OVERRIDE
    MESHDEBUG_OVERRIDE PACKET_LOGGING_OVERRIDE MQTT_BRIDGE_OVERRIDE
    MQTT_DEBUG_OVERRIDE RADIO_SETTING_TITLE RADIO_FREQ_OVERRIDE
    RADIO_BW_OVERRIDE RADIO_SF_OVERRIDE RADIO_CR_OVERRIDE
    RESUME_BUILD_OUTPUT KISS_MODE_OVERRIDE REQUIRE_OTA_UPDATES
    SSL_CERT_FILE SSL_CERT_DIR REQUESTS_CA_BUNDLE CURL_CA_BUNDLE
    GIT_CONFIG_GLOBAL
  )

  if [ "$BUILD_BACKGROUND_ACTIVE" = "1" ]; then
    echo "Refusing to detach an already detached background worker." >&2
    return 1
  fi
  if ! background_systemd_available; then
    echo "--background requires a running systemd user manager (systemd-run --user)." >&2
    echo "Run this build in the foreground or enable the user systemd service manager." >&2
    return 1
  fi

  project_dir=$(get_build_project_dir) || return $?
  script_path="${project_dir}/${BASH_SOURCE[0]##*/}"
  background_dir=$(get_background_build_dir "$project_dir")
  background_dir=$(canonicalize_background_build_dir "$background_dir") \
    || return $?

  command_name=${1//[^A-Za-z0-9_.@-]/-}
  timestamp=$(date '+%Y%m%d-%H%M%S')
  job_id="meshcore-build-${command_name}-${timestamp}-$$"

  worker_environment+=(
    "BUILD_BACKGROUND_ACTIVE=1"
    "BUILD_BACKGROUND_CONFIG_RESOLVED=1"
    "BUILD_BACKGROUND_JOB_ID=${job_id}"
    "BUILD_BACKGROUND_DIR=${background_dir}"
    "BUILD_BACKGROUND_BACKEND=systemd"
    "OUTPUT_POLICY_EXPLICIT=1"
  )
  # env -i below prevents stale variables in the user service manager (most
  # importantly DISABLE_DEBUG) from changing the detached build. Always give
  # the worker a usable PATH and HOME, then copy only this explicit allowlist.
  # Credentials and proxy URLs are deliberately not placed in the transient
  # unit's command line, where service inspection could expose them.
  worker_environment+=(
    "PATH=${PATH:-/usr/local/bin:/usr/bin:/bin}"
    "HOME=${HOME:?HOME is required for a background build}"
  )
  for variable_name in "${forwarded_environment[@]}"; do
    case "$variable_name" in
      PATH|HOME) continue ;;
    esac
    if [[ -v $variable_name ]]; then
      variable_value=${!variable_name}
      worker_environment+=("${variable_name}=${variable_value}")
    fi
  done

  if ! systemd-run --user --quiet \
      --collect \
      --unit="$job_id" \
      --description="MeshCore firmware build: ${1}" \
      --service-type=exec \
      --working-directory="$project_dir" \
      /usr/bin/env -i "${worker_environment[@]}" \
      /usr/bin/bash "$script_path" "$@"; then
    echo "Failed to start background build ${job_id}." >&2
    return 1
  fi

  status_file="${background_dir}/${job_id}.status"
  if wait_for_background_worker_handoff "$status_file"; then
    handoff_status=0
  else
    handoff_status=$?
  fi
  if [ "$BUILD_BACKGROUND_HANDOFF_STATE" != "timeout" ]; then
    printf '%s\n' "$job_id" > "${background_dir}/latest-job"
  fi
  if [ "$handoff_status" -ne 0 ]; then
    if [ "$BUILD_BACKGROUND_HANDOFF_STATE" = "failed" ]; then
      echo "Background build ${job_id} failed during startup." >&2
      echo "Result status: ${status_file}" >&2
      echo "Build log: ${background_dir}/${job_id}.log" >&2
    else
      echo "Background build ${job_id} was accepted by systemd, but its worker did not acquire the build lock within 10 seconds." >&2
      echo "Inspect: journalctl --user -u ${job_id}.service" >&2
    fi
    return 1
  fi

  echo "Started background build: ${job_id}"
  echo "Status: systemctl --user status ${job_id}.service"
  echo "Live journal: journalctl --user -u ${job_id}.service -f"
  echo "Build log: ${background_dir}/${job_id}.log"
  echo "Result status: ${background_dir}/${job_id}.status"
}

acquire_build_script_lock() {
  local project_dir lock_path owner=""

  if [ -n "$BUILD_SCRIPT_LOCK_FD" ]; then
    return 0
  fi
  project_dir=$(get_build_project_dir) || return $?
  mkdir -p -- "${project_dir}/.pio" || return $?
  lock_path="${project_dir}/.pio/build-sh.lock"
  exec {BUILD_SCRIPT_LOCK_FD}>> "$lock_path" || return $?
  if ! flock -n "$BUILD_SCRIPT_LOCK_FD"; then
    owner=$(<"$lock_path")
    echo "Another build.sh firmware build owns this checkout's PlatformIO lock." >&2
    if [ -n "$owner" ]; then
      echo "Lock owner: ${owner}" >&2
    fi
    exec {BUILD_SCRIPT_LOCK_FD}>&-
    BUILD_SCRIPT_LOCK_FD=""
    return 1
  fi
  printf 'pid=%s job=%s started=%s\n' \
    "$$" "${BUILD_BACKGROUND_JOB_ID:-foreground}" "$(date --iso-8601=seconds)" \
    > "$lock_path"
}

release_build_script_lock() {
  if [ -z "$BUILD_SCRIPT_LOCK_FD" ]; then
    return 0
  fi
  flock -u "$BUILD_SCRIPT_LOCK_FD" || true
  exec {BUILD_SCRIPT_LOCK_FD}>&-
  BUILD_SCRIPT_LOCK_FD=""
}

main() {
  local run_status=0

  if ! parse_cli_options "$@"; then
    exit 1
  fi
  set -- "${PARSED_COMMAND_ARGS[@]}"

  case "${1:-}" in
    help|usage|-h|--help)
      if [ "$BUILD_BACKGROUND_REQUESTED" = "1" ]; then
        echo "--background is valid only with a firmware build command." >&2
        exit 1
      fi
      global_usage
      exit 0
      ;;
    list|-l)
      if [ "$BUILD_BACKGROUND_REQUESTED" = "1" ]; then
        echo "--background is valid only with a firmware build command." >&2
        exit 1
      fi
      init_project_context
      get_pio_envs
      exit 0
      ;;
    get-companion-firmwares-to-build|get-repeater-firmwares-to-build|get-room-server-firmwares-to-build)
      if [ "$BUILD_BACKGROUND_REQUESTED" = "1" ]; then
        echo "--background is valid only with a firmware build command." >&2
        exit 1
      fi
      init_project_context
      print_release_firmware_targets "$1"
      exit $?
      ;;
  esac

  if [ $# -gt 0 ]; then
    validate_command "$@"
  fi

  # Hold one checkout-wide lock from PlatformIO configuration discovery through
  # the final target. Detached and foreground builds must never share or clean
  # the same .pio/build tree concurrently.
  if ! acquire_build_script_lock; then
    exit 1
  fi
  if [ "$BUILD_BACKGROUND_ACTIVE" = "1" ]; then
    write_background_build_status "running"
  fi

  init_project_context

  if [ "$BUILD_BACKGROUND_CONFIG_RESOLVED" = "1" ]; then
    echo "Using build settings resolved by the background launcher."
  elif [ -n "$RADIO_PRESET_SELECTION" ]; then
    if ! apply_cli_radio_preset "$RADIO_PRESET_SELECTION"; then
      exit 1
    fi
  else
    resolve_usa_cascadia_radio_default
  fi

  if [ $# -eq 0 ]; then
    if ! [ -t 0 ]; then
      echo "No command provided and no interactive terminal is available."
      global_usage
      exit 1
    fi

    INTERACTIVE_BUILD_SELECTION=1
    prompt_for_build_mode
    if [ "$SINGLE_TARGET_FULL_BUILD" = "1" ]; then
      echo "Skipping separate debug and MQTT prompts; FULL everything enables USB logging and WiFi MQTT where the hardware supports it."
    elif is_automatic_profile_command "${SELECTED_COMMAND_ARGS[0]}"; then
      if is_logging_matrix_command "${SELECTED_COMMAND_ARGS[0]}"; then
        echo "Skipping debug and MQTT prompts; this action builds standard artifacts with merged runtime USB logging and unified FULL profiles automatically."
      elif is_full_esp32_logging_command "${SELECTED_COMMAND_ARGS[0]}"; then
        echo "Skipping debug and MQTT prompts; this action builds only logging fallbacks for FULL targets without WiFi MQTT."
      else
        echo "Skipping debug and MQTT prompts; this action builds unified FULL USB + WiFi profiles and required logging fallbacks."
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
  if ! configure_effective_build_profile "$1"; then
    exit 1
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

  if [ "$INTERACTIVE_BUILD_SELECTION" = "1" ] \
      && [ "$BUILD_BACKGROUND_EXPLICIT" != "1" ]; then
    prompt_for_background_build_mode
  fi
  if [ "$BUILD_BACKGROUND_REQUESTED" = "1" ]; then
    # The systemd worker reacquires this lock before invoking PlatformIO.
    release_build_script_lock
    launch_background_build "$@"
    exit $?
  fi

  prepare_output_dir
  run_command "$@"
  run_status=$?
  release_build_script_lock
  return "$run_status"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  if [ "$BUILD_BACKGROUND_ACTIVE" = "1" ]; then
    if ! setup_background_build_worker; then
      exit 1
    fi
    trap 'finish_background_build "$?"' EXIT
  fi
  main "$@"
fi
