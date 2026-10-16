#!/usr/bin/env bash
# Identity-bound, application-only adapter for the actual mcfirmware.sh DFU.
# No startup code, erase package, bootloader/SoftDevice update, service stop,
# privilege escalation, unmatched-device override or automatic recovery runs.
# Take any required logical backup separately, before entering the bootloader.
set -euo pipefail
umask 077

nrf_adapter_die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
nrf_adapter_usage() {
    printf '%s\n' \
      'Usage: mercer_nrf_app_flash.sh --flasher PATH --port /dev/tty...' \
      '  --workdir DIR --inventory JSON --serial USB_SERIAL --board LABEL' \
      '  --artifact APPLICATION.zip --sha256 HEX --allow-write' \
      'Only an inventory-matched application-only DFU ZIP is accepted.' \
      'The sibling verified_nrf_flash.py helper and installed dependencies are required.' \
      'Successful DFU still requires a separate application-level boot/version check.'
}

nrf_adapter_flasher='' nrf_adapter_port='' nrf_adapter_workdir=''
nrf_adapter_inventory='' nrf_adapter_serial='' nrf_adapter_board=''
nrf_adapter_artifact='' nrf_adapter_sha256='' nrf_adapter_allow_write=0
nrf_adapter_session_active=0
while (($#)); do
    case "$1" in
        --flasher|--port|--workdir|--inventory|--serial|--board|--artifact|--sha256)
            (($# >= 2)) || nrf_adapter_die "Missing value for $1"
            case "$1" in
                --flasher) nrf_adapter_flasher=$2 ;;
                --port) nrf_adapter_port=$2 ;;
                --workdir) nrf_adapter_workdir=$2 ;;
                --inventory) nrf_adapter_inventory=$2 ;;
                --serial) nrf_adapter_serial=$2 ;;
                --board) nrf_adapter_board=$2 ;;
                --artifact) nrf_adapter_artifact=$2 ;;
                --sha256) nrf_adapter_sha256=$2 ;;
            esac
            shift 2 ;;
        --allow-write) nrf_adapter_allow_write=1; shift ;;
        --help|-h) nrf_adapter_usage; exit 0 ;;
        *) nrf_adapter_die "Unknown option: $1" ;;
    esac
done
((nrf_adapter_allow_write)) || nrf_adapter_die 'An application update requires --allow-write.'
[[ "$nrf_adapter_port" =~ ^/dev/tty(ACM|USB)[0-9]+$ ]] || nrf_adapter_die 'Select a local USB serial tty.'
[[ "$nrf_adapter_serial" =~ ^[[:alnum:]]{6,64}$ ]] || nrf_adapter_die 'Provide the reviewed stable USB chip serial.'
[[ -n "$nrf_adapter_board" && "$nrf_adapter_board" != *$'\n'* ]] || nrf_adapter_die 'Provide the reviewed board label.'
[[ "$nrf_adapter_sha256" =~ ^[[:xdigit:]]{64}$ ]] || nrf_adapter_die 'Expected SHA256 must contain exactly 64 hex digits.'
nrf_adapter_sha256=${nrf_adapter_sha256,,}
[[ -d "$nrf_adapter_workdir" ]] || nrf_adapter_die 'Create a dedicated workdir first.'
nrf_adapter_workdir=$(realpath -e -- "$nrf_adapter_workdir")
[[ "$nrf_adapter_workdir" != / && "$nrf_adapter_workdir" != /home \
    && "$nrf_adapter_workdir" != "${HOME:-}" ]] || nrf_adapter_die 'Use a dedicated run directory.'
for nrf_adapter_command in awk bash python3 pipx timeout udevadm fuser realpath sha256sum stat cp chmod stty socat perl unzip strings; do
    command -v "$nrf_adapter_command" >/dev/null || nrf_adapter_die "Required command is unavailable: $nrf_adapter_command"
done
nrf_adapter_flasher=$(realpath -e -- "$nrf_adapter_flasher")
nrf_adapter_inventory=$(realpath -e -- "$nrf_adapter_inventory")
nrf_adapter_artifact=$(realpath -e -- "$nrf_adapter_artifact")
[[ -f "$nrf_adapter_flasher" && -s "$nrf_adapter_flasher" ]] || nrf_adapter_die 'Provide the complete local mcfirmware.sh.'
[[ -f "$nrf_adapter_inventory" && -s "$nrf_adapter_inventory" ]] || nrf_adapter_die 'Provide the reviewed physical-board inventory.'
[[ -f "$nrf_adapter_artifact" && -s "$nrf_adapter_artifact" \
    && "${nrf_adapter_artifact,,}" == *.zip ]] || nrf_adapter_die 'Provide an application DFU ZIP.'
nrf_adapter_helper="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/verified_nrf_flash.py"
[[ -f "$nrf_adapter_helper" ]] || nrf_adapter_die 'Keep verified_nrf_flash.py beside this adapter.'
python3 -c 'import serial, zipfile' || nrf_adapter_die 'Install the required Python dependencies separately.'

nrf_adapter_check_hash() {
    local observed
    observed=$(sha256sum -- "$1")
    [[ "${observed%% *}" == "$nrf_adapter_sha256" ]] || nrf_adapter_die 'Application ZIP SHA256 mismatch.'
}
nrf_adapter_assert_unowned() {
    local output='' status=0
    # Some Pi psmisc versions do not accept '--'. The port was restricted above.
    [[ "$1" =~ ^/dev/tty(ACM|USB)[0-9]+$ ]] || nrf_adapter_die 'Unsafe serial endpoint.'
    if output=$(fuser "$1" 2>&1); then
        printf '%s\n' "$output" >&2
        nrf_adapter_die "A process owns $1; no owner will be killed."
    else status=$?; fi
    [[ "$status" == 1 && -z "$output" ]] || nrf_adapter_die "Cannot prove $1 is unowned: $output"
}
nrf_adapter_check_hash "$nrf_adapter_artifact"
nrf_adapter_state_dir=$(mktemp -d "$nrf_adapter_workdir/.mcfirmware-nrf-app.XXXXXX")
nrf_adapter_definitions="$nrf_adapter_state_dir/functions.sh"
# Same definitions-only extraction convention as mercer_mt_flash.sh. Syntax
# failure is fatal; no flasher global assignments, traps or startup are sourced.
awk '
    { sub(/\r$/, "") }
    /^[A-Za-z_][A-Za-z0-9_]*\(\)[[:space:]]*\{[[:space:]]*$/ {
        if (capture) exit 2
        capture=1; count++
    }
    capture { print }
    capture && /^}[[:space:]]*$/ { capture=0 }
    END { if (capture || count < 100) exit 2 }
' "$nrf_adapter_flasher" > "$nrf_adapter_definitions"
bash -n "$nrf_adapter_definitions"
# shellcheck source=/dev/null
source "$nrf_adapter_definitions"
for nrf_adapter_function in preferred_flash_serial_port save_selected_serial_port \
    nrf52_validate_rak_board_pair nrf52_selected_by_id_path udev_device_property \
    nrf52_usb_path_stem run_nrf52_dfu_package_buttonless; do
    declare -F "$nrf_adapter_function" >/dev/null || nrf_adapter_die "Flasher lacks $nrf_adapter_function."
done

# Stage a private, hash-verified snapshot, so the build output may not change
# beneath a DFU operation. Keep the original board-qualified artifact basename.
nrf_adapter_staged="$nrf_adapter_state_dir/$(basename -- "$nrf_adapter_artifact")"
cp -- "$nrf_adapter_artifact" "$nrf_adapter_staged"
chmod 0400 -- "$nrf_adapter_staged"
nrf_adapter_check_hash "$nrf_adapter_staged"
cp -- "$nrf_adapter_inventory" "$nrf_adapter_state_dir/inventory.json"
chmod 0400 -- "$nrf_adapter_state_dir/inventory.json"
sha256sum -- "$nrf_adapter_flasher" "$nrf_adapter_definitions" "$nrf_adapter_staged"

MCFIRMWARE_NO_SUDO=1
MCFIRMWARE_NRF52_ACK_TIMEOUT_SECONDS=10
NRF52_DFU_REENUMERATE_TIMEOUT_SECONDS=30
NRF52_DFU_REENUMERATE_POLL_SECONDS=0.25
NRF52_BOARD_GUARD_PASSED=0
NRF52_SELECTED_BY_ID='' NRF52_RUNTIME_SERIAL='' NRF52_RUNTIME_PATH_STEM=''
NRF52_LAST_DFU_PORT='' NRF52_LAST_DFU_INSTANCE=''
USB_RESET_FAILED=0
DEVICE_PORT_FILE="$nrf_adapter_state_dir/selected-port"
DEVICE_PORT_NAME_FILE="$nrf_adapter_state_dir/selected-by-id"
DEVICE_PORT=$nrf_adapter_port
BAUD=115200
DEFAULT_BAUDS=(115200)
SERIAL_BAUD_CACHE=115200
# These fail-closed boundaries never alter actual identity matching or DFU.
nrf52_confirm_board_override() { printf 'Board override is forbidden.\n' >&2; return 1; }
nrf52_confirm_unmatched_dfu_override() { printf 'Unmatched DFU override is forbidden.\n' >&2; return 1; }
ensure_sudo_session() { printf 'Privilege escalation is forbidden.\n' >&2; return 1; }
install_packages() { printf 'Package installation is forbidden.\n' >&2; return 1; }
nrf52_serial_port_access() {
    nrf_adapter_assert_unowned "$1"
    [[ -r "$1" && -w "$1" ]] || { printf 'Serial access must already be granted.\n' >&2; return 1; }
}

nrf_adapter_assert_unowned "$nrf_adapter_port"
nrf_adapter_port=$(preferred_flash_serial_port "$nrf_adapter_port")
nrf_adapter_assert_unowned "$nrf_adapter_port"
python3 "$nrf_adapter_helper" --inventory "$nrf_adapter_state_dir/inventory.json" \
    --serial "$nrf_adapter_serial" --board "$nrf_adapter_board" \
    --artifact "$nrf_adapter_staged" --application-only --port "$nrf_adapter_port"
save_selected_serial_port "$nrf_adapter_port"
nrf52_validate_rak_board_pair "$nrf_adapter_staged" "$nrf_adapter_port" "$nrf_adapter_board"
NRF52_BOARD_GUARD_PASSED=1
NRF52_SELECTED_BY_ID=$(nrf52_selected_by_id_path)
NRF52_RUNTIME_SERIAL=$(udev_device_property "$nrf_adapter_port" ID_SERIAL_SHORT)
NRF52_RUNTIME_PATH_STEM=$(nrf52_usb_path_stem "$(udev_device_property "$nrf_adapter_port" ID_PATH)")
[[ -n "$NRF52_SELECTED_BY_ID" && -n "$NRF52_RUNTIME_SERIAL" ]] || nrf_adapter_die 'No stable USB identity was captured.'
[[ "${NRF52_RUNTIME_SERIAL^^}" == "${nrf_adapter_serial^^}" ]] || nrf_adapter_die 'USB identity changed after validation.'
nrf_adapter_check_hash "$nrf_adapter_staged"
nrf_adapter_assert_unowned "$nrf_adapter_port"
nrf_adapter_exit_notice() {
    if ((nrf_adapter_session_active)); then
        printf 'DFU interrupted or failed. No recovery/reset was attempted; reselect the same physical radio. State: %s\n' "$nrf_adapter_state_dir" >&2
    fi
}
trap nrf_adapter_exit_notice EXIT
nrf_adapter_session_active=1
run_nrf52_dfu_package_buttonless "$nrf_adapter_staged" "$nrf_adapter_port" 0 0
nrf_adapter_session_active=0
printf 'Application-only DFU completed; boot/version verification is still required. State: %s\n' "$nrf_adapter_state_dir"
