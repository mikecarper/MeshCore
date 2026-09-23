#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source build.sh

fail() { echo "test_build_size_detection: $*" >&2; exit 1; }
test_root=$(mktemp -d "${TMPDIR:-/tmp}/meshcore-size-tests.XXXXXX")
test_root=$(realpath "$test_root")
[[ "$test_root" == /*/meshcore-size-tests.* ]] || fail "unexpected temporary path"
trap 'rm -rf -- "$test_root"' EXIT
export TMPDIR=$test_root
managed_dir=$test_root/build/test
pio() {
  # Model the real SCons cleanup that previously unlinked the tee log.
  [[ "$managed_dir" == "$test_root/"* ]] || return 99
  rm -rf -- "$managed_dir"
  touch "$test_root/pio-called"
  printf '%s\n' "$pio_message"
  return "$pio_result"
}

check_status() {
  local expected=$1 actual=0
  mkdir -p "$managed_dir"
  if run_pio_with_size_detection run -e test > "$test_root/output" 2>&1; then
    actual=0
  else
    actual=$?
  fi
  [ "$actual" -eq "$expected" ] || fail "expected status $expected, got $actual"
  [ ! -d "$managed_dir" ] || fail "fake PlatformIO did not clean its build directory"
  if compgen -G "$test_root/meshcore-build-*.log" > /dev/null; then
    fail "temporary build-output log was not released"
  fi
}

pio_result=1
for pio_message in \
  "firmware.elf section .text will not fit in region FLASH" \
  "region FLASH overflowed by 43080 bytes" \
  "Error: The program size is greater than maximum allowed" \
  "Error: The program size (1314113 bytes) is greater than maximum allowed (1310720 bytes)"; do
  check_status 42
  grep -Fq "$pio_message" "$test_root/output" || fail "compiler output was lost"
done
pio_message='error: missing declaration'
check_status 1
pio_result=2
check_status 2
pio_result=0
pio_message='compilation succeeded'
check_status 0

# An output failure must not look like a successful or merely oversized build.
tee() { cat > /dev/null; return 1; }
check_status 1
unset -f tee
rm -f "$test_root/pio-called"
TMPDIR=$test_root/missing
if run_pio_with_size_detection run -e test > /dev/null 2>&1; then
  fail "missing log directory was accepted"
fi
[ ! -e "$test_root/pio-called" ] || fail "PIO ran without diagnostic storage"

# Keep sensors and protocol features while shrinking nRF52 repeater code.
PIO_ENV_PLATFORM_BY_NAME[ikoka_nano_nrf_22dbm_repeater]=NRF52_PLATFORM
PLATFORMIO_BUILD_FLAGS=''
PLATFORMIO_BUILD_UNFLAGS=''
apply_nrf52_size_profile ikoka_nano_nrf_22dbm_repeater
[[ "$PLATFORMIO_BUILD_FLAGS" == *' -Os'* ]] || fail "full-sensor repeater omitted size optimization"
[[ "$PLATFORMIO_BUILD_UNFLAGS" == *'-Ofast'* ]] || fail "aggressive optimization was not removed"
[[ "$PLATFORMIO_BUILD_FLAGS" != *'-UENV_INCLUDE_'* ]] || fail "sensor support was reduced"

# The legacy RAK4631 Ethernet image uses the same optimizer without inheriting
# Full Companion's transport/source-only OTA overlay or reducing its sensors.
(
  PIO_ENV_PLATFORM_BY_NAME[RAK_4631_companion_radio_ethernet]=NRF52_PLATFORM
  PIO_ENV_OTA_BY_NAME[RAK_4631_companion_radio_ethernet]=1
  expected_flags='-DENABLE_OTA=1 -DOTA_FLASH_STORE=1 -DETHERNET_ENABLED=1 -DENV_INCLUDE_GPS=1 -DENV_INCLUDE_BME680_BSEC=1'
  for profile in auto standard; do
    BUILD_PROFILE_FOR_TARGET=$profile
    PLATFORMIO_BUILD_FLAGS=$expected_flags
    PLATFORMIO_BUILD_UNFLAGS='-D EXTRAFS=1'
    PLATFORMIO_BOARD_UPLOAD_MAXIMUM_SIZE=712704
    PLATFORMIO_BOARD_BUILD_LDSCRIPT=boards/nrf52840_s140_v6.ld
    apply_nrf52_size_profile RAK_4631_companion_radio_ethernet
    [ "$PLATFORMIO_BUILD_FLAGS" = "$expected_flags -Os" ] \
      || fail "Ethernet Companion changed features instead of only optimization ($profile)"
    [ "$PLATFORMIO_BUILD_UNFLAGS" = $'-D EXTRAFS=1\n-Ofast' ] \
      || fail "Ethernet Companion removed flags other than aggressive optimization ($profile)"
    [ "$PLATFORMIO_BOARD_UPLOAD_MAXIMUM_SIZE" = 712704 ] \
      || fail "Ethernet Companion changed its application size limit"
    [ "$PLATFORMIO_BOARD_BUILD_LDSCRIPT" = boards/nrf52840_s140_v6.ld ] \
      || fail "Ethernet Companion changed its flash layout"
  done
  for target in RAK_4631_companion_radio_usb RAK_4631_companion_radio_ble Other_companion_radio_ethernet; do
    PIO_ENV_PLATFORM_BY_NAME[$target]=NRF52_PLATFORM
    PLATFORMIO_BUILD_FLAGS=''
    PLATFORMIO_BUILD_UNFLAGS=''
    apply_nrf52_size_profile "$target"
    [ -z "$PLATFORMIO_BUILD_FLAGS" ] && [ -z "$PLATFORMIO_BUILD_UNFLAGS" ] \
      || fail "RAK Ethernet optimization expanded to unrelated target $target"
  done
)

for target in Heltec_t096_repeater_lora_ota_no_external_sensors Heltec_t1_repeater_lora_ota_no_external_sensors; do
  PIO_ENV_PLATFORM_BY_NAME[$target]=NRF52_PLATFORM
  PLATFORMIO_BUILD_FLAGS=''
  PLATFORMIO_BUILD_UNFLAGS=''
  apply_repeater_neighbor_capacity "$target"
  apply_nrf52_size_profile "$target"
  [[ "$PLATFORMIO_BUILD_FLAGS" == *'-DMAX_NEIGHBOURS=50'* ]] || fail "$target omitted RAM-safe neighbours"
  [[ "$PLATFORMIO_BUILD_FLAGS" == *'-DFLOOD_PACKET_FILTER_SLOTS=16'* ]] || fail "$target omitted RAM-safe rule table"
  [[ "$PLATFORMIO_BUILD_FLAGS" != *'MESH_MIN_RUNTIME_HEAP'* ]] || fail "runtime RAM guard was overridden"
done
for target in Heltec_t096_companion_radio_full_femon RAK_4631_repeater; do
  PIO_ENV_PLATFORM_BY_NAME[$target]=NRF52_PLATFORM
  PLATFORMIO_BUILD_FLAGS=''
  PLATFORMIO_BUILD_UNFLAGS=''
  apply_nrf52_size_profile "$target"
  [[ "$PLATFORMIO_BUILD_FLAGS" != *'FLOOD_PACKET_FILTER_SLOTS'* ]] || fail "$target received unrelated table reductions"
  [[ "$PLATFORMIO_BUILD_FLAGS" != *'MAX_NEIGHBOURS'* ]] || fail "$target received unrelated neighbour reductions"
done
PIO_ENV_PLATFORM_BY_NAME[Heltec_v4_repeater]=ESP32_PLATFORM
PLATFORMIO_BUILD_FLAGS=''
apply_nrf52_size_profile Heltec_v4_repeater
[ -z "$PLATFORMIO_BUILD_FLAGS" ] || fail "nRF52 policy changed an ESP32 target"
echo 'Build-size diagnostics and constrained nRF52 recipes passed.'
