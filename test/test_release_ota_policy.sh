#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source build.sh

fail() { echo "test_release_ota_policy: $*" >&2; exit 1; }

# Exercise release resolution without starting PlatformIO. This inventory
# includes a personal USB Companion and infrastructure on the same platform.
PIO_ENV_PLATFORM_BY_NAME[nrf_repeater]=NRF52_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[esp_companion_radio_full]=ESP32_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[rp_companion_radio_usb]=RP2040_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[rp_repeater]=RP2040_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[stm_sensor]=STM32_PLATFORM
resolve_bulk_command_targets() {
  RESOLVED_BUILD_TARGETS=(nrf_repeater esp_companion_radio_full
                         rp_companion_radio_usb rp_repeater stm_sensor)
}
sort_build_targets_by_platform_and_name() { printf '%s\n' "$@"; }
validate_build_target() { return 0; }

REQUIRE_OTA_UPDATES=""
resolve_command_targets build-firmwares-logging-matrix >/dev/null
[ "$REQUIRE_OTA_UPDATES" = 1 ] || fail "option 3 did not require infrastructure OTA"
[ "${#RESOLVED_BUILD_TARGETS[@]}" = 3 ] || fail "wrong release inventory"
[[ " ${RESOLVED_BUILD_TARGETS[*]} " == *" rp_companion_radio_usb "* ]] \
  || fail "USB-updated Companion was excluded"
[ "${#OTA_EXCLUDED_TARGETS[@]}" = 2 ] || fail "missing exclusion report"

parse_cli_options build-firmwares-logging-matrix --allow-no-ota
resolve_command_targets "${PARSED_COMMAND_ARGS[@]}" >/dev/null
[ "${#RESOLVED_BUILD_TARGETS[@]}" = 5 ] || fail "development override lost targets"

parse_cli_options build-firmware rp_repeater --require-ota
if resolve_command_targets "${PARSED_COMMAND_ARGS[@]}" >/dev/null; then
  fail "explicit cable-only infrastructure build passed OTA requirement"
fi
resolve_command_targets build-firmware rp_companion_radio_usb >/dev/null
[ "${#RESOLVED_BUILD_TARGETS[@]}" = 1 ] || fail "explicit USB Companion was rejected"

# The portable OTA overlay and the final neighbor overlay must agree: an
# earlier build_unflags entry removes even a later definition in PlatformIO.
for target in Generic_E22_sx1262_repeater_lora_ota_no_external_sensors \
              Generic_E22_sx1268_repeater_lora_ota_no_external_sensors \
              Heltec_v2_repeater_lora_ota_no_external_sensors \
              Meshadventurer_sx1262_repeater_lora_ota_no_external_sensors \
              Meshadventurer_sx1268_repeater_lora_ota_no_external_sensors \
              Tbeam_SX1262_repeater_lora_ota_no_external_sensors \
              Tbeam_SX1276_repeater_lora_ota_no_external_sensors; do
  PIO_ENV_PLATFORM_BY_NAME[$target]=ESP32_PLATFORM
  PLATFORMIO_BUILD_FLAGS=""
  PLATFORMIO_BUILD_UNFLAGS=""
  BUILD_PROFILE_FOR_TARGET=standard
  ESP32_FULL_BUILD=0
  apply_esp32_lora_ota_size_profile "$target"
  apply_repeater_neighbor_capacity "$target"
  [[ "$PLATFORMIO_BUILD_FLAGS" == *"-DMAX_NEIGHBOURS=50"* ]] \
    || fail "$target lost its measured-safe neighbor capacity"
  [[ "$PLATFORMIO_BUILD_UNFLAGS" != *"MAX_NEIGHBOURS=50"* ]] \
    || fail "$target removed its selected neighbor capacity"
  [[ "$PLATFORMIO_BUILD_FLAGS" == *"-DLIGHTWEIGHT_WIFI_OTA=1"* ]] \
    || fail "$target lost WiFi OTA while fixing its RAM budget"
done

ESP32_FULL_BUILD=1
BUILD_PROFILE_FOR_TARGET=full
PACKET_LOGGING_OVERRIDE=on
for pair in Tbeam_SX1262_repeater_observer_mqtt:31 \
            Tbeam_SX1276_repeater_observer_mqtt:31 \
            Generic_E22_sx1262_repeater_bridge_espnow:47 \
            LilyGo_TLora_V2_1_1_6_repeater_bridge_espnow:47 \
            LilyGo_TLora_V2_1_1_6_repeater_observer_mqtt_:default; do
  target=${pair%:*}
  rules=${pair##*:}
  PIO_ENV_PLATFORM_BY_NAME[$target]=ESP32_PLATFORM
  PIO_ENV_FULL_BUILD_BY_NAME[$target]=1
  PLATFORMIO_BUILD_FLAGS=""
  PLATFORMIO_BUILD_UNFLAGS=""
  apply_esp32_full_size_profile "$target"
  apply_repeater_neighbor_capacity "$target"
  [[ "$PLATFORMIO_BUILD_FLAGS" == *"-DMAX_NEIGHBOURS=50"* \
     && "$PLATFORMIO_BUILD_UNFLAGS" != *"MAX_NEIGHBOURS=50"* ]] \
    || fail "$target lost its Full profile neighbor capacity"
  if [ "$rules" != default ]; then
    [[ "$PLATFORMIO_BUILD_FLAGS" == *"-DFLOOD_PACKET_FILTER_SLOTS=$rules"* \
       && "$PLATFORMIO_BUILD_UNFLAGS" != *"FLOOD_PACKET_FILTER_SLOTS=$rules"* ]] \
      || fail "$target lost its Full profile rule capacity"
  fi
done

echo "test_release_ota_policy: OK"
