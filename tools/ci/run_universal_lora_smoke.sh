#!/usr/bin/env bash
# Separate CI runners own the shards; calls inside each checkout stay serial.
set -euo pipefail

usage() {
  echo "Usage: $0 {contracts|tbeam|t3s3|meshadventurer|e22-tenstar|muzi}" >&2
}

if [[ $# -ne 1 ]]; then
  usage
  exit 2
fi

full_targets=()
interface_targets=()
require_ota=1
interfaces_first=0
case "$1" in
  contracts)
    ;;
  tbeam)
    full_targets=(Tbeam_companion_radio_full Tbeam_repeater Tbeam_room_server
      Tbeam_repeater_observer_mqtt Tbeam_room_server_observer_mqtt)
    interface_targets=(Tbeam_kiss_modem)
    ;;
  t3s3)
    full_targets=(LilyGo_T3S3_companion_radio_full LilyGo_T3S3_repeater LilyGo_T3S3_room_server)
    interface_targets=(LilyGo_T3S3_kiss_modem LilyGo_T3S3_terminal_chat)
    ;;
  meshadventurer)
    full_targets=(Meshadventurer_repeater Meshadventurer_companion_radio_full Meshadventurer_room_server)
    interface_targets=(Meshadventurer_kiss_modem Meshadventurer_terminal_chat)
    ;;
  e22-tenstar)
    full_targets=(Generic_E22_repeater Tenstar_C3_repeater)
    interface_targets=(Generic_E22_kiss_modem Tenstar_C3_kiss_modem)
    ;;
  muzi)
    interface_targets=(muzi_base_repeater muzi_base_room_server
      muzi_base_companion_radio_usb muzi_base_companion_radio_ble)
    full_targets=(muzi_base_companion_radio_full)
    # Infrastructure remains local qualification until the board-matched OTA
    # loader passes physical qualification. Do not require an OTA receiver here.
    require_ota=0
    interfaces_first=1
    ;;
  *)
    usage
    exit 2
    ;;
esac

ci_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
cd -- "$ci_root"

if [[ "$1" == contracts ]]; then
  python3 -B test/test_universal_lora.py -v
  python3 -B test/test_universal_lora_builds.py -v
  python3 -B test/test_universal_lora_gpio.py -v
  python3 -B test/test_portable_lora_probe.py -v
  python3 -B test/test_universal_lr_policy.py -v
  python3 -B test/test_muzi_universal_builds.py -v
  python3 -B test/test_muzi_universal_startup.py -v
  python3 -B test/test_muzi_qspi_capacity.py -v
  python3 -B test/test_notification_rtc_storage.py -v
  python3 -B test/test_companion_channels_rtc.py -v
  python3 -B test/test_universal_sx126x_builds.py -v
  python3 -B test/test_universal_sx126x_defaults.py -v
  python3 -B test/test_universal_radio_startup.py -v
  python3 -B test/test_universal_radio_boot_policy.py -v
  python3 -B test/test_universal_radio_lto.py -v
  python3 -B test/test_consolidated_profiles.py -v
  node test/test_universal_radio_picker.js
  exit 0
fi

full_flags=(--firmware-version v1.18.0.2-halo-keymind-cascade-dev
  --radio-preset usa-cascade-fixed --profile cascade)

build_full_targets() {
  local target
  for target in "${full_targets[@]}"; do
    local command=(bash build.sh build-firmware "$target" "${full_flags[@]}")
    if [[ "$require_ota" == 1 ]]; then
      command+=(--require-ota)
    fi
    "${command[@]}"
  done
}

build_interface_targets() {
  local target
  local command=(bash tools/ci/pio_retry.sh run)
  for target in "${interface_targets[@]}"; do
    command+=(-e "$target")
  done
  "${command[@]}"
}

if [[ "$interfaces_first" == 1 ]]; then
  build_interface_targets
  build_full_targets
else
  build_full_targets
  build_interface_targets
fi
