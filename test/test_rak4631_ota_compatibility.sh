#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
source build.sh

fail() {
  echo "test_rak4631_ota_compatibility: $*" >&2
  exit 1
}

assert_array_equals() {
  local label=$1
  local actual_name=$2
  shift 2
  local -n actual=$actual_name
  local -a expected=("$@")
  local index

  [ "${#actual[@]}" -eq "${#expected[@]}" ] \
    || fail "$label count was ${#actual[@]}, expected ${#expected[@]}: ${actual[*]}"
  for index in "${!expected[@]}"; do
    [ "${actual[$index]}" = "${expected[$index]}" ] \
      || fail "$label[$index] was ${actual[$index]}, expected ${expected[$index]}"
  done
}

compatibility_targets=(
  RAK_4631_repeater_bridge_rs232_serial1_lora_ota_no_external_sensors
  RAK_4631_repeater_bridge_rs232_serial2_lora_ota_no_external_sensors
)
mapfile -t declared_compatibility_targets < <(
  get_deployed_lora_ota_compatibility_targets
)
assert_array_equals \
  "declared compatibility targets" declared_compatibility_targets \
  "${compatibility_targets[@]}"

for target in "${compatibility_targets[@]}"; do
  is_deployed_lora_ota_compatibility_target "$target" \
    || fail "$target lost its deployed-node compatibility classification"
  if is_redundant_bulk_build_target "$target"; then
    fail "$target was omitted from canonical bulk builds"
  fi
done
if is_deployed_lora_ota_compatibility_target RAK_4631_repeater; then
  fail "canonical RAK4631 repeater was classified as a legacy OTA identity"
fi

[ "$(get_merged_rs232_repeater_replacement "${compatibility_targets[0]}")" \
    = RAK_4631_repeater_lora_ota_no_external_sensors ] \
  || fail "Serial1 compatibility target lost its canonical recommendation"
[ "$(get_merged_rs232_repeater_replacement "${compatibility_targets[1]}")" \
    = RAK_4631_repeater_lora_ota_no_external_sensors ] \
  || fail "Serial2 compatibility target lost its canonical recommendation"

canonical_target=RAK_4631_repeater
canonical_ota_target=RAK_4631_repeater_lora_ota_no_external_sensors
legacy_install_targets=(
  RAK_4631_repeater_bridge_rs232_serial1
  RAK_4631_repeater_bridge_rs232_serial2
)
SUPPORTED_PIO_ENVS=(
  "$canonical_target"
  "$canonical_ota_target"
  "${legacy_install_targets[@]}"
  "${compatibility_targets[@]}"
)
for target in "${SUPPORTED_PIO_ENVS[@]}"; do
  PIO_ENV_PLATFORM_BY_NAME[$target]=NRF52_PLATFORM
done

mapfile -t all_targets < <(resolve_all_firmwares)
assert_array_equals \
  "canonical bulk target list" all_targets \
  "$canonical_target" \
  "$canonical_ota_target" \
  "${compatibility_targets[@]}"

mapfile -t release_targets < <(
  print_release_firmware_targets get-repeater-firmwares-to-build
)
assert_array_equals \
  "repeater release target list" release_targets \
  "$canonical_target" \
  "${compatibility_targets[@]}"

echo "test_rak4631_ota_compatibility: OK"
