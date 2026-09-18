#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
source build.sh

fail() { echo "test_full_infrastructure_selection: $*" >&2; exit 1; }

# Resolve real board inheritance, but never compile or flash firmware.
init_project_context >/dev/null

assert_auto_unified() (
  local plain=$1 expected=$2
  BUILD_PROFILE_OVERRIDE=auto
  BUILD_PROFILE_EXPLICIT=0
  SINGLE_TARGET_FULL_BUILD=0
  RESOLVED_BUILD_TARGETS=("$plain")
  configure_effective_build_profile build-firmware >/dev/null
  [ "${RESOLVED_BUILD_TARGETS[*]}" = "$expected" ] || fail "$plain selected the wrong target"
  [ "$SINGLE_TARGET_FULL_BUILD" = 1 ] || fail "$plain bypassed the unified recipe"
  [ "$AUTO_PREFER_FULL_BUILD" = 0 ] || fail "$plain still schedules a plain Full artifact"

  local -a built=()
  run_logged_build_targets() {
    [ "$FIRMWARE_FILENAME_INFIX" = full-usb-wifi ] || fail "wrong artifact name"
    [ "$ESP32_FULL_BUILD" = 1 ] || fail "lost expanded partitions"
    [ "$PACKET_LOGGING_OVERRIDE" = on ] || fail "lost USB packet logging"
    [ "$MQTT_BRIDGE_OVERRIDE" = on ] || fail "lost MQTT"
    [ "$MESHDEBUG_OVERRIDE" = off ] || fail "enabled verbose debug"
    built+=("$@")
  }
  run_command build-firmware >/dev/null
  [ "${built[*]}" = "$expected" ] || fail "auto emitted duplicate Full artifacts"
  built=()
  run_full_esp32_build_targets all "$plain" "$expected" >/dev/null
  [ "${built[*]}" = "$expected" ] || fail "matrix emitted duplicate Full artifacts"
)

assert_auto_unified heltec_v4_r8_repeater heltec_v4_r8_repeater_observer_mqtt
assert_auto_unified heltec_v4_r8_room_server heltec_v4_r8_room_server_observer_mqtt
assert_auto_unified heltec_v4_r8_tft_repeater heltec_v4_r8_tft_repeater_observer_mqtt
assert_auto_unified Heltec_T190_repeater_ Heltec_T190_repeater_observer_mqtt
assert_auto_unified LilyGo_TLora_V2_1_1_6_room_server LilyGo_TLora_V2_1_1_6_room_server_observer_mqtt_
assert_auto_unified heltec_v4_r8_repeater_observer_mqtt heltec_v4_r8_repeater_observer_mqtt

# Only targets whose exact FULL environment was audited for partition history
# and every capacity limit may omit their redundant portable bulk artifact.
full_only_count=0
for target in "${!PIO_ENV_PLATFORM_BY_NAME[@]}"; do
  if is_esp32_full_only_bulk_target "$target"; then
    full_only_count=$((full_only_count + 1))
  fi
done
[ "$full_only_count" -eq 47 ] || fail "expected 47 audited FULL-only targets, found $full_only_count"

for target in heltec_rc32_repeater Station_G2_repeater_observer_mqtt \
    SenseCapIndicator-LoRa-N16R2_companion_radio_full \
    LilyGo_Tlora_C6_companion_radio_full_ \
    LilyGo_TLora_V2_1_1_6_repeater_bridge_espnow; do
  is_esp32_full_only_bulk_target "$target" || fail "missed audited FULL-only target $target"
done
for target in LilyGo_TLora_V2_1_1_6_repeater_observer_mqtt_ \
    LilyGo_TLora_V2_1_1_6_companion_radio_wifi heltec_v4_r8_repeater; do
  if is_esp32_full_only_bulk_target "$target"; then
    fail "consolidated target with a capacity or partition caveat: $target"
  fi
done

# Portable images and exact reduced OTA identities must not be consolidated.
for target in heltec_v4_r8_repeater heltec_v4_r8_room_server \
    heltec_v4_r8_repeater_lora_ota_no_external_sensors; do
  (
    BUILD_PROFILE_OVERRIDE=standard
    BUILD_PROFILE_EXPLICIT=1
    SINGLE_TARGET_FULL_BUILD=0
    RESOLVED_BUILD_TARGETS=("$target")
    configure_effective_build_profile build-firmware >/dev/null
    [ "${RESOLVED_BUILD_TARGETS[*]}" = "$target" ] || fail "lost portable target $target"
    [ "$BUILD_PROFILE_EFFECTIVE" = standard ] || fail "expanded portable image $target"
    [ "$SINGLE_TARGET_FULL_BUILD" = 0 ] || fail "portable image entered Full pipeline"
  )
done

for target in Tbeam_SX1262_repeater Tbeam_SX1276_repeater \
    Tbeam_SX1262_room_server Tbeam_SX1276_room_server \
    LilyGo_TLora_V2_1_1_6_repeater \
    heltec_v4_r8_repeater_lora_ota_no_external_sensors \
    RAK_4631_repeater_bridge_rs232_serial1_lora_ota_no_external_sensors; do
  if get_unified_full_infrastructure_target "$target" >/dev/null; then
    fail "consolidated a distinct capacity/OTA contract: $target"
  fi
done

(
  PIO_ENV_BOARD_BY_NAME[heltec_v4_r8_repeater_observer_mqtt]=different_board
  if get_unified_full_infrastructure_target heltec_v4_r8_repeater >/dev/null; then
    fail "accepted a different physical board"
  fi
)

# Direct observer promotion must use the matrix's exact output recipe even if
# interactive flags previously requested MQTT without USB logging. Stop at the
# existing-artifact check to exercise the real build entry point without pio run.
(
  ESP32_FULL_BUILD=0
  BUILD_PROFILE_EFFECTIVE=standard
  BUILD_PROFILE_OVERRIDE=auto
  BUILD_PROFILE_EXPLICIT=0
  FIRMWARE_VERSION=vtest
  RESUME_BUILD_OUTPUT=1
  FIRMWARE_FILENAME_INFIX=""
  MESHDEBUG_OVERRIDE=on
  PACKET_LOGGING_OVERRIDE=off
  MQTT_BRIDGE_OVERRIDE=off
  MQTT_DEBUG_OVERRIDE=on
  observed_filename=""
  observed_output=""
  build_artifacts_exist() {
    observed_filename=$2
    observed_output="$PACKET_LOGGING_OVERRIDE/$MQTT_BRIDGE_OVERRIDE/$MESHDEBUG_OVERRIDE/$MQTT_DEBUG_OVERRIDE"
    return 0
  }
  build_firmware heltec_v4_r8_repeater_observer_mqtt >/dev/null
  [[ "$observed_filename" == heltec_v4_r8_repeater_observer_mqtt-full-usb-wifi-ota-vtest-* ]] \
    || fail "direct observer kept a duplicate name"
  [ "$observed_output" = on/on/off/off ] || fail "direct observer differs from unified recipe"
  [ "$PACKET_LOGGING_OVERRIDE/$MQTT_BRIDGE_OVERRIDE/$MESHDEBUG_OVERRIDE/$MQTT_DEBUG_OVERRIDE" = off/off/on/on ] \
    || fail "observer output settings leaked into subsequent builds"
)

# A canonical bulk build promotes an audited target in-place, retaining its
# environment/mOTA identity. Explicit standard builds remain untouched above.
(
  ESP32_FULL_BUILD=0
  BUILD_PROFILE_EFFECTIVE=standard
  BUILD_PROFILE_OVERRIDE=auto
  BUILD_PROFILE_EXPLICIT=0
  BATCH_BUILD_MODE=1
  FIRMWARE_VERSION=vtest
  RESUME_BUILD_OUTPUT=1
  FIRMWARE_FILENAME_INFIX=""
  observed_filename=""
  observed_full=0
  build_artifacts_exist() {
    observed_filename=$2
    observed_full=$ESP32_FULL_BUILD
    return 0
  }
  build_firmware heltec_rc32_repeater >/dev/null
  [ "$observed_full" = 1 ] || fail "audited bulk target did not promote to FULL"
  [[ "$observed_filename" == heltec_rc32_repeater-full-logging-ota-vtest-* ]] \
    || fail "FULL-only bulk target lost its exact identity"
)
(
  ESP32_FULL_BUILD=0
  BUILD_PROFILE_EFFECTIVE=standard
  BUILD_PROFILE_OVERRIDE=standard
  BUILD_PROFILE_EXPLICIT=1
  BATCH_BUILD_MODE=1
  FIRMWARE_VERSION=vtest
  RESUME_BUILD_OUTPUT=1
  observed_full=1
  build_artifacts_exist() {
    observed_full=$ESP32_FULL_BUILD
    return 0
  }
  build_firmware heltec_rc32_repeater >/dev/null
  [ "$observed_full" = 0 ] || fail "explicit standard recovery build was promoted"
)

(
  standard_built=""
  full_built=""
  REQUIRE_OTA_UPDATES=0
  run_logged_build_targets() {
    if [ "$ESP32_FULL_BUILD" = 0 ]; then standard_built="$*"; else full_built="$*"; fi
  }
  run_logging_matrix_build_targets heltec_v4_r8_repeater \
    heltec_v4_r8_repeater_lora_ota_no_external_sensors \
    heltec_v4_r8_repeater_observer_mqtt >/dev/null
  [ "$standard_built" = 'heltec_v4_r8_repeater heltec_v4_r8_repeater_lora_ota_no_external_sensors' ] \
    || fail "matrix removed a portable build"
  [ "$full_built" = heltec_v4_r8_repeater_observer_mqtt ] || fail "matrix duplicated Full"
  [ "$ESP32_LORA_OTA_APP_LIMIT" = 1310720 ] || fail "changed the 1.25 MiB portable limit"
)

(
  calls=()
  REQUIRE_OTA_UPDATES=0
  run_logged_build_targets() {
    calls+=("$BUILD_PROFILE_EFFECTIVE:$ESP32_FULL_BUILD:$FIRMWARE_FILENAME_INFIX:$*")
  }
  run_logging_matrix_build_targets heltec_rc32_repeater \
    heltec_rc32_repeater_bridge_espnow >/dev/null
  [ "${#calls[@]}" -eq 1 ] || fail "FULL-only targets emitted duplicate matrix artifacts"
  [ "${calls[0]}" = 'full:1:full-logging:heltec_rc32_repeater heltec_rc32_repeater_bridge_espnow' ] \
    || fail "matrix did not build exact FULL-only identities"
)

# The default build settings retain power saving; this does not overwrite
# preferences already stored on a device.
[ "$FIRMWARE_PROFILE_OVERRIDE" = cascade ] || fail "default profile changed"
PLATFORMIO_BUILD_FLAGS=""
apply_firmware_profile_overrides
[[ "$PLATFORMIO_BUILD_FLAGS" == *-DDEFAULT_POWERSAVING_ENABLED=1* ]] || fail "device power saving disabled"
[[ "$PLATFORMIO_BUILD_FLAGS" == *-DDEFAULT_RXPS_ENABLED=1* ]] || fail "RX power saving disabled"

echo "test_full_infrastructure_selection: OK"
