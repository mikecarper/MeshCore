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

echo "test_release_ota_policy: OK"
