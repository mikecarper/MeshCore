#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
source build.sh

fail() {
  echo "test_build_profiles: $*" >&2
  exit 1
}

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

echo "test_build_profiles: OK"
