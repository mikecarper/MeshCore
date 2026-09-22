#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
source build.sh

fail() { echo "test_xiao_qspi_combined: $*" >&2; exit 1; }

# Use the resolved PlatformIO recipes, not a board-name allowlist: each
# canonical XIAO repeater must retain the internal bootloader scratch bank,
# raw external-QSPI OTA staging, and packet logging in one application.
init_project_context
BUILD_PROFILE_FOR_TARGET=standard
BUILD_PROFILE_EFFECTIVE=standard
FIRMWARE_FILENAME_INFIX=""
DISABLE_DEBUG=0
PACKET_LOGGING_OVERRIDE=""
MESHDEBUG_OVERRIDE=""
MQTT_BRIDGE_OVERRIDE=""

for target in \
  Xiao_nrf52_repeater \
  ikoka_handheld_nrf_e22_30dbm_repeater \
  ikoka_nano_nrf_22dbm_repeater \
  ikoka_nano_nrf_30dbm_repeater \
  ikoka_nano_nrf_33dbm_repeater \
  ikoka_stick_nrf_22dbm_repeater \
  ikoka_stick_nrf_30dbm_repeater \
  ikoka_stick_nrf_33dbm_repeater \
  solarxiao_30S_repeater \
  solarxiao_33S_repeater; do
  is_supported_build_env "$target" || fail "$target is missing"
  is_xiao_qspi_canonical_repeater_build "$target" \
    || fail "$target lost its combined-image classification"
  if is_supported_build_env "${target}_lora_ota_no_external_sensors"; then
    fail "$target still publishes a redundant lean OTA sibling"
  fi
  if get_reduced_lora_ota_target "$target" >/dev/null; then
    fail "$target still falls back to a reduced OTA sibling"
  fi
  pio_env_option_contains "$target" board_build.ldscript \
    nrf52840_s140_v7_xiao_bootloader_ota.ld \
    || fail "$target lost its 40 KiB internal bootloader scratch reservation"
  pio_env_option_contains "$target" board_upload.maximum_size 757760 \
    || fail "$target lost its internal application size limit"
  pio_env_option_contains "$target" build_flags OTA_QSPI_BOOTLOADER_UPDATE=1 \
    || fail "$target lost its OTAFIX bootloader-update layout"
  pio_env_option_contains "$target" build_flags OTA_QSPI_STORE=1 \
    || fail "$target lost external-QSPI OTA staging"
  pio_env_option_contains "$target" build_flags MESH_PACKET_LOGGING=1 \
    || fail "$target lost packet logging"
  is_lora_ota_build "$target" || fail "$target lost LoRa OTA"

  BUILD_CAPABILITIES=()
  BUILD_REDUCTIONS=()
  BUILD_EXPECTATIONS=()
  BUILD_APPLICATION_EXPECTATIONS=()
  declare_build_capability_contract "$target" NRF52_PLATFORM
  expectations=" ${BUILD_EXPECTATIONS[*]} "
  application=" ${BUILD_APPLICATION_EXPECTATIONS[*]} "
  [[ "$expectations" == *"ota.update.lora=invalid in-place patch geometry"* ]] \
    || fail "$target no longer proves its LoRa OTA receiver"
  [[ "$expectations" == *"ota.cli=OTA: status"* ]] \
    || fail "$target no longer proves its OTA CLI"
  [[ "$application" == *"logging.usb.packets=%s: %s, len=%d (type=%d, route=%s, payload_len=%d)"* ]] \
    || fail "$target no longer verifies packaged packet logging"
  [[ "$application" == *"logging.usb.control=OK - USB logging %s (saved)"* ]] \
    || fail "$target no longer verifies the packaged USB logging control"
done

# This consolidation is specific to the external-QSPI XIAO repeater layout.
is_xiao_qspi_combined_repeater_target Xiao_nrf52_room_server \
  && fail "non-QSPI room server was incorrectly consolidated"
is_xiao_qspi_combined_repeater_target RAK_4631_repeater \
  && fail "unrelated repeater was incorrectly consolidated"

echo "test_xiao_qspi_combined: OK"
