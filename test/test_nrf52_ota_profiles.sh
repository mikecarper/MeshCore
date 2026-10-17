#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
source build.sh
fail() { echo "test_nrf52_ota_profiles: $*" >&2; exit 1; }
test_output=$(mktemp -d)
trap 'rm -rf -- "$test_output"' EXIT
tower_primary=Heltec_tower_v2_sdcard_repeater_lora_ota_no_external_sensors
tower_legacy=Heltec_tower_v2_repeater_lora_ota_no_external_sensors
tower_base=Heltec_tower_v2_repeater

# This suite deliberately has no PlatformIO dependency. A mocked one-profile
# worker exercises the actual single/bulk/matrix dispatch and fail-closed pair
# policy, while the flag/manifest contracts below use production helpers.
SUPPORTED_PIO_ENVS=(
  nrf_repeater nrf_repeater_lora_ota_no_external_sensors
  nrf_room_server nrf_room_server_lora_ota_no_external_sensors
  nrf_sensor nrf_sensor_lora_ota_no_external_sensors
  Xiao_nrf52_repeater "$tower_primary" "$tower_base" "$tower_legacy"
  RAK_3401_repeater_unified_lora_ota RAK_3401_sensor
  RAK_3401_sensor_lora_ota_no_external_sensors nrf_kiss_modem
  nrf_companion_radio_usb nrf_companion_radio_full nrf_terminal_chat
  esp_repeater
)
for target in "${SUPPORTED_PIO_ENVS[@]}"; do
  PIO_ENV_PLATFORM_BY_NAME[$target]=NRF52_PLATFORM
done
PIO_ENV_PLATFORM_BY_NAME[esp_repeater]=ESP32_PLATFORM
for base in nrf_repeater nrf_room_server nrf_sensor RAK_3401_sensor; do
  alias=${base}_lora_ota_no_external_sensors
  PIO_ENV_BUILD_BASE_BY_NAME[$alias]=$base
  PIO_ENV_COMPLETE_OTA_BASE_BY_NAME[$alias]=$base
  PIO_ENV_OTA_BY_NAME[$alias]=1
done
PIO_ENV_QSPI_OTA_BY_NAME[Xiao_nrf52_repeater]=1
PIO_ENV_QSPI_OTA_BY_NAME[RAK_3401_repeater_unified_lora_ota]=1
PIO_ENV_SD_OTA_BY_NAME[Heltec_tower_v2_sdcard_repeater_lora_ota_no_external_sensors]=1
ALL_PIO_ENVS=("$tower_base" "$tower_primary")
PIO_ENV_BUILD_BASE_BY_NAME[$tower_legacy]=$tower_base
PIO_ENV_COMPLETE_OTA_BASE_BY_NAME[$tower_legacy]=$tower_base
PIO_CONFIG_JSON='[]'

for target in nrf_repeater nrf_room_server nrf_sensor Xiao_nrf52_repeater \
  Heltec_tower_v2_sdcard_repeater_lora_ota_no_external_sensors \
  RAK_3401_repeater_unified_lora_ota; do
  is_nrf52_sensor_ota_pair_target "$target" || fail "$target has no pair policy"
done
for target in nrf_kiss_modem nrf_companion_radio_usb nrf_companion_radio_full \
  nrf_terminal_chat esp_repeater; do
  if is_nrf52_sensor_ota_pair_target "$target"; then
    fail "$target incorrectly gained the nRF52 sensor/OTA manager policy"
  fi
done
[ "$(get_reduced_lora_ota_target nrf_sensor)" = nrf_sensor_lora_ota_no_external_sensors ] \
  || fail "sensor reduced alias is not handled"
[ "$(get_nrf52_sensor_ota_pair_target nrf_repeater)" = nrf_repeater_lora_ota_no_external_sensors ] \
  || fail "ordinary internal target lost its historical OTA identity"
[ "$(get_nrf52_sensor_ota_pair_target Xiao_nrf52_repeater)" = Xiao_nrf52_repeater ] \
  || fail "external QSPI target changed its deployed ID"
[ "$(get_nrf52_sensor_ota_pair_target Heltec_tower_v2_sdcard_repeater_lora_ota_no_external_sensors)" = \
  Heltec_tower_v2_sdcard_repeater_lora_ota_no_external_sensors ] \
  || fail "SD target lost its exact storage identity"

# The old base and generated internal-only build names now select the exact
# SD primary. A direct alias must warn about its different installed OTA ID,
# and all bulk/tagged lists must retain only one Full/Reduced target pair.
for alias in "$tower_base" "$tower_legacy"; do
  [ "$(get_nrf52_sensor_ota_pair_target "$alias")" = "$tower_primary" ] \
    || fail "$alias did not resolve to the SD primary"
  resolve_command_targets build-firmware "$alias" > /dev/null \
    2> "$test_output/tower-alias-warning"
  [ "${#RESOLVED_BUILD_TARGETS[@]}" -eq 1 ] \
    && [ "${RESOLVED_BUILD_TARGETS[0]}" = "$tower_primary" ] \
    || fail "$alias direct dispatch retained an internal-only identity"
  warning=$(cat "$test_output/tower-alias-warning")
  [[ "$warning" == *"require local USB/BLE DFU or SWD migration"* \
     && "$warning" == *"not a same-target LoRa upgrade"* ]] \
    || fail "$alias omitted its installed-identity migration warning"
  is_redundant_bulk_build_target "$alias" \
    || fail "$alias remained a second canonical artifact"
done
for resolver in resolve_all_firmwares resolve_repeater_firmwares; do
  mapfile -t tower_targets < <("$resolver")
  target_list=" ${tower_targets[*]} "
  [[ "$target_list" == *" $tower_primary "* \
     && "$target_list" != *" $tower_base "* \
     && "$target_list" != *" $tower_legacy "* ]] \
    || fail "$resolver did not publish only the Tower SD primary"
done
mapfile -t tower_targets < <(print_release_firmware_targets get-repeater-firmwares-to-build)
target_list=" ${tower_targets[*]} "
[[ "$target_list" == *" $tower_primary "* \
   && "$target_list" != *" $tower_base "* \
   && "$target_list" != *" $tower_legacy "* ]] \
  || fail "tagged repeater list omitted the SD primary or retained a duplicate"
for unchanged in Heltec_tower_v2_room_server Heltec_tower_v2_companion_radio_full; do
  if get_unified_meshtower_repeater_replacement "$unchanged" >/dev/null; then
    fail "$unchanged was redirected outside the repeater role"
  fi
done

RESOLVED_BUILD_TARGETS=(nrf_repeater nrf_repeater_lora_ota_no_external_sensors \
  nrf_sensor nrf_sensor_lora_ota_no_external_sensors Xiao_nrf52_repeater \
  "$tower_primary" "$tower_base" "$tower_legacy")
normalize_nrf52_sensor_ota_pair_targets 2> "$test_output/tower-alias-warning"
[ "${#RESOLVED_BUILD_TARGETS[@]}" -eq 4 ] \
  || fail "ordinary/reduced aliases did not deduplicate into exactly one pair"

# Do not silently resurrect an incompatible internal recipe if the canonical
# SD target is absent from a narrowed/misconfigured build inventory.
unset 'PIO_ENV_PLATFORM_BY_NAME[Heltec_tower_v2_sdcard_repeater_lora_ota_no_external_sensors]'
RESOLVED_BUILD_TARGETS=("$tower_legacy")
if normalize_nrf52_sensor_ota_pair_targets > /dev/null 2>&1; then
  fail "unavailable SD primary silently fell back to the old internal recipe"
fi
PIO_ENV_PLATFORM_BY_NAME[$tower_primary]=NRF52_PLATFORM

# The build overlay must not undefine the exact primary's internal fallback.
# Other SD targets retain their old SD-only contract even if metadata happens
# to contain the Tower macro; no generic SD layout is made dual-storage.
NRF52_OTA_SENSOR_PROFILE=full
PIO_CONFIG_JSON='[["env:Heltec_tower_v2_sdcard_repeater_lora_ota_no_external_sensors", [["build_flags", ["-DOTA_SD_STORE=1", "-DOTA_FLASH_STORE=1", "-DOTA_TOWER_AUTO_STORE=1"]]]]]'
PLATFORMIO_BUILD_FLAGS=""
PLATFORMIO_BUILD_UNFLAGS=""
apply_lora_ota_override "$tower_primary"
[[ "$PLATFORMIO_BUILD_FLAGS" == *"-DOTA_FLASH_STORE=1"* \
   && "$PLATFORMIO_BUILD_FLAGS" == *"-DOTA_SD_STORE=1"* \
   && "$PLATFORMIO_BUILD_FLAGS" == *"-DOTA_TOWER_AUTO_STORE=1"* \
   && "$PLATFORMIO_BUILD_FLAGS" != *"-UOTA_FLASH_STORE"* \
   && "$PLATFORMIO_BUILD_UNFLAGS" != *"OTA_FLASH_STORE"* ]] \
  || fail "SD primary overlay disabled its qualified internal fallback"
other_sd_target=other_sd_repeater_lora_ota_no_external_sensors
PIO_ENV_PLATFORM_BY_NAME[$other_sd_target]=NRF52_PLATFORM
PIO_ENV_SD_OTA_BY_NAME[$other_sd_target]=1
PIO_CONFIG_JSON='[["env:other_sd_repeater_lora_ota_no_external_sensors", [["build_flags", ["-DOTA_SD_STORE=1", "-DOTA_TOWER_AUTO_STORE=1"]]]]]'
PLATFORMIO_BUILD_FLAGS=""
PLATFORMIO_BUILD_UNFLAGS=""
apply_lora_ota_override "$other_sd_target"
[[ "$PLATFORMIO_BUILD_FLAGS" == *"-UOTA_FLASH_STORE"* \
   && "$PLATFORMIO_BUILD_FLAGS" != *"-DOTA_TOWER_AUTO_STORE"* \
   && "$PLATFORMIO_BUILD_UNFLAGS" == *"-D OTA_FLASH_STORE=1"* ]] \
  || fail "unrelated SD target gained the Tower internal fallback"
PIO_CONFIG_JSON='[]'
NRF52_OTA_SENSOR_PROFILE=""

for selected in auto standard full; do
  BUILD_PROFILE_OVERRIDE=$selected
  BUILD_PROFILE_EXPLICIT=1
  RESOLVED_BUILD_TARGETS=(nrf_sensor)
  configure_effective_build_profile build-firmware >/dev/null
  [ "$BUILD_PROFILE_EFFECTIVE" = auto ] || fail "$selected bypassed strict nRF52 pairing"
  [ "$AUTO_COMPLETE_FIRST_PASS" = 0 ] || fail "$selected retained the permissive fallback path"
done
BUILD_PROFILE_EXPLICIT=0

# Use the same explicit source prefix as canonical release packaging, not
# Git's repository-dependent/adaptive abbreviated hash length.
worker_source=$(declare -f build_firmware_one_profile)
[[ "$worker_source" == *'full_source_commit=$(git rev-parse HEAD)'* ]] \
  || fail "builder does not derive publication prefix from full source identity"
[[ "$worker_source" == *'commit_hash=${full_source_commit:0:8}'* ]] \
  || fail "builder publication hash disagrees with canonical release prefix"

calls=()
failed_profile=""
build_firmware_one_profile() {
  calls+=("$1:$NRF52_OTA_SENSOR_PROFILE:$FIRMWARE_FILENAME_INFIX:$SKIP_DECLARED_REDUCTIONS:$COMPLETE_OTA_FIRST_PASS:$REQUIRE_OTA_UPDATES")
  if [ -n "$failed_profile" ] && [ "$NRF52_OTA_SENSOR_PROFILE" = "$failed_profile" ]; then return 42; fi
  return 0
}
ESP32_FULL_BUILD=1
BUILD_PROFILE_EFFECTIVE=standard
REQUIRE_OTA_UPDATES=0
FIRMWARE_FILENAME_INFIX=outer
SKIP_DECLARED_REDUCTIONS=0
NRF52_OTA_SENSOR_PROFILE=""
for target in nrf_repeater nrf_room_server nrf_sensor Xiao_nrf52_repeater \
  "$tower_primary" "$tower_base" "$tower_legacy"; do
  calls=()
  build_firmware "$target" >/dev/null
  [ "${#calls[@]}" -eq 2 ] || fail "$target did not build exactly two options"
  [[ "${calls[0]}" == *:full:full-ota:1:1:1 ]] || fail "$target full flags/OTA requirement are wrong"
  [[ "${calls[1]}" == *:reduced:reduced-ota:0:0:1 ]] || fail "$target reduced flags/OTA requirement are wrong"
  [ "${calls[0]%%:*}" = "${calls[1]%%:*}" ] || fail "$target profiles have different OTA IDs"
  if [[ "$target" == Heltec_tower_v2* ]]; then
    [ "${calls[0]%%:*}" = "$tower_primary" ] \
      || fail "$target profile worker built the obsolete internal identity"
  fi
done
[ "$ESP32_FULL_BUILD:$BUILD_PROFILE_EFFECTIVE:$REQUIRE_OTA_UPDATES:$FIRMWARE_FILENAME_INFIX:$NRF52_OTA_SENSOR_PROFILE" = \
  '1:standard:0:outer:' ] || fail "pair state leaked into other platform/Companion builds"

for failed_profile in full reduced; do
  calls=()
  if build_firmware nrf_repeater >/dev/null 2>&1; then
    fail "$failed_profile failure was reported as a complete pair"
  fi
  [ "${#calls[@]}" -eq 2 ] || fail "$failed_profile failure hid the other option's qualification"
done
failed_profile=""

# Both ordinary bulk and the logged matrix go through the same pair dispatcher.
calls=()
run_resolved_build_targets nrf_repeater nrf_sensor Xiao_nrf52_repeater >/dev/null
[ "${#calls[@]}" -eq 6 ] || fail "bulk builds did not publish every pair"
OUTPUT_DIR=$test_output
FIRMWARE_FILENAME_INFIX=""
calls=()
run_logged_build_targets nrf_room_server Xiao_nrf52_repeater >/dev/null
[ "${#calls[@]}" -eq 4 ] || fail "logged matrix did not publish external/internal pairs"

# KISS and Companion keep the original one-profile worker and do not gain an
# application receiver merely because another target used the paired overlay.
calls=()
build_firmware nrf_kiss_modem >/dev/null
build_firmware nrf_companion_radio_full >/dev/null
[ "${#calls[@]}" -eq 2 ] || fail "excluded roles gained profile pairs"
[[ "${calls[0]}" == nrf_kiss_modem::* ]] || fail "KISS inherited sensor OTA state"

NRF52_OTA_SENSOR_PROFILE=reduced
BUILD_PROFILE_FOR_TARGET=standard
MQTT_BRIDGE_OVERRIDE=off
PACKET_LOGGING_OVERRIDE=on
MESHDEBUG_OVERRIDE=on
ESP32_FULL_BUILD=0
is_lora_ota_build nrf_sensor || fail "logging/standard settings disabled required OTA"
if is_lora_ota_build nrf_kiss_modem; then fail "KISS gained an OTA application"; fi

for profile in full reduced; do
  NRF52_OTA_SENSOR_PROFILE=$profile
  BUILD_CAPABILITIES=()
  BUILD_EXPECTATIONS=()
  BUILD_REDUCTIONS=()
  BUILD_APPLICATION_EXPECTATIONS=()
  declare_build_capability_contract nrf_sensor NRF52_PLATFORM
  [[ " ${BUILD_CAPABILITIES[*]} " == *" sensor.profile.${profile} "* ]] \
    || fail "$profile manifest sensor label is missing"
  [[ " ${BUILD_EXPECTATIONS[*]} " == *" ota.update.lora=invalid in-place patch geometry "* ]] \
    || fail "$profile does not verify actual LoRa apply support"
done

# Raw external-storage names have no reduced-alias suffix, but still receive
# the explicit reduced policy; the full profile does not trim these drivers.
NRF52_OTA_SENSOR_PROFILE=reduced
SKIP_DECLARED_REDUCTIONS=0
PLATFORMIO_BUILD_FLAGS=""
PLATFORMIO_BUILD_UNFLAGS=""
BUILD_REDUCTIONS=()
apply_lora_ota_no_external_sensors_profile Xiao_nrf52_repeater
[[ "$PLATFORMIO_BUILD_FLAGS" == *-UENV_INCLUDE_BME280* ]] \
  || fail "external QSPI reduced profile did not trim optional drivers"
NRF52_OTA_SENSOR_PROFILE=full
SKIP_DECLARED_REDUCTIONS=1
PLATFORMIO_BUILD_FLAGS=""
apply_lora_ota_no_external_sensors_profile Xiao_nrf52_repeater
[ -z "$PLATFORMIO_BUILD_FLAGS" ] || fail "full sensor profile was silently trimmed"

NRF52_OTA_SENSOR_PROFILE=reduced
is_rak_i2c_voltage_monitor_ota_target RAK_3401_repeater_unified_lora_ota \
  || fail "adaptive RAK reduced profile dropped the retained INA policy"
is_rak_gps_retaining_ota_target RAK_3401_repeater_unified_lora_ota \
  || fail "adaptive RAK reduced profile dropped compatible GPS"
if is_rak_gps_retaining_ota_target RAK_4631_repeater_bridge_rs232_serial1_lora_ota_no_external_sensors; then
  fail "Serial1 bridge incorrectly promises conflicting GPS"
fi

# Resume must requalify the current sensor profile, not accept a legacy
# Bluetooth-only or unlabeled image merely because its hash/UF2 checks pass.
for profile in full reduced; do
  for corruption in valid missing_sensor opposite_sensor ambiguous_sensor missing_lora_cap \
    no_lora_method unproven_lora unverified_ota unverified_manifest wrong_target wrong_artifact wrong_platform; do
    resume_manifest=$test_output/resume.json
    python3 - "$resume_manifest" "$profile" "$corruption" <<'PY'
import json, pathlib, sys
path, profile, corruption = sys.argv[1:]
manifest = {"platform": "NRF52_PLATFORM", "target": "nrf_sensor",
            "artifact_target": "nrf_sensor-" + profile + "-ota",
            "capabilities": ["sensor.profile." + profile, "ota.update.lora"],
            "verification": [{"capability": "ota.update.lora", "present": True}],
            "verified": True, "ota_update_verified": True, "ota_update_methods": ["lora", "bluetooth"]}
if corruption == "missing_sensor": manifest["capabilities"].pop(0)
elif corruption == "opposite_sensor": manifest["capabilities"][0] = "sensor.profile." + ("reduced" if profile == "full" else "full")
elif corruption == "ambiguous_sensor": manifest["capabilities"].append("sensor.profile." + ("reduced" if profile == "full" else "full"))
elif corruption == "missing_lora_cap": manifest["capabilities"].pop()
elif corruption == "no_lora_method": manifest["ota_update_methods"] = ["bluetooth"]
elif corruption == "unproven_lora": manifest["verification"][0]["present"] = False
elif corruption == "unverified_ota": manifest["ota_update_verified"] = False
elif corruption == "unverified_manifest": manifest["verified"] = False
elif corruption == "wrong_target": manifest["target"] = "other_sensor"
elif corruption == "wrong_artifact": manifest["artifact_target"] = "nrf_sensor"
elif corruption == "wrong_platform": manifest["platform"] = "ESP32_PLATFORM"
pathlib.Path(path).write_text(json.dumps(manifest, indent=2))
PY
    if nrf52_sensor_profile_manifest_matches "$resume_manifest" nrf_sensor "$profile" "nrf_sensor-${profile}-ota"; then
      [ "$corruption" = valid ] || fail "resume accepted $profile/$corruption"
    else
      [ "$corruption" != valid ] || fail "resume rejected valid $profile metadata"
    fi
  done
done

python3 -B test/test_nrf52_paired_logging.py -v

echo "test_nrf52_ota_profiles: OK"
