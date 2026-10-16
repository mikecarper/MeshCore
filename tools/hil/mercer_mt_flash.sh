#!/usr/bin/env bash
# Meshtext HIL: identity-bound adapter for the actual mcfirmware.sh functions.
# This does NOT source its startup code, install packages, stop services, or
# recover a busy/failed transport. Logical application backups must be taken
# separately, before entering the ROM. No erase is implicit in write-flash.
set -euo pipefail
umask 077

adapter_die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
adapter_usage() {
    printf '%s\n' \
      'Usage: mercer_mt_flash.sh --flasher PATH --port /dev/tty... --workdir DIR' \
      '  [--board LABEL] [--expect-mac XX:XX:XX:XX:XX:XX --flash-bytes N]' \
      '  [--read-baud 115200|460800] [--allow-write] [--allow-erase] [--allow-reset] -- MODE ARGS' \
      '  [--watchdog-finish] (write-flash only; verified ESP32-S3 session)' \
      'Modes: probe; read-flash OFFSET LENGTH NEW_OUTPUT; write-flash OFFSET FILE ...;' \
      '       erase-flash; erase-region OFFSET LENGTH; reset-runtime' \
      'All non-probe modes require an expected MAC and flash size. Reads must write' \
      'new files inside workdir; writes/erases require their explicit allow flag.' \
      'probe also enters ROM and finishes using the original identity-safe reset.' \
      '--watchdog-finish clears only the volatile S3 forced-ROM bit before watchdog reset.' \
      'reset-runtime requires --allow-reset, an exact MAC and flash size; it never writes flash.'
}

adapter_flasher='' adapter_port='' adapter_workdir='' adapter_board='ESP32-S3'
adapter_expected_mac='' adapter_expected_bytes='' adapter_read_baud=115200
adapter_allow_write=0 adapter_allow_erase=0 adapter_session_active=0 adapter_watchdog_finish=0
adapter_allow_reset=0
while (($#)); do
    case "$1" in
        --flasher|--port|--workdir|--board|--expect-mac|--flash-bytes|--read-baud)
            (($# >= 2)) || adapter_die "Missing value for $1"
            case "$1" in
                --flasher) adapter_flasher=$2 ;;
                --port) adapter_port=$2 ;;
                --workdir) adapter_workdir=$2 ;;
                --board) adapter_board=$2 ;;
                --expect-mac) adapter_expected_mac=$2 ;;
                --flash-bytes) adapter_expected_bytes=$2 ;;
                --read-baud) adapter_read_baud=$2 ;;
            esac
            shift 2 ;;
        --allow-write) adapter_allow_write=1; shift ;;
        --allow-erase) adapter_allow_erase=1; shift ;;
        --allow-reset) adapter_allow_reset=1; shift ;;
        --watchdog-finish) adapter_watchdog_finish=1; shift ;;
        --help|-h) adapter_usage; exit 0 ;;
        --) shift; break ;;
        *) adapter_die "Unknown option: $1" ;;
    esac
done
adapter_mode=${1:-probe}
(($# == 0)) || shift
adapter_operation_args=("$@")
[[ -f "$adapter_flasher" && -s "$adapter_flasher" ]] || adapter_die 'Provide the complete local mcfirmware.sh.'
[[ "$adapter_port" == /dev/ttyACM* || "$adapter_port" == /dev/ttyUSB* ]] || adapter_die 'Select a local USB serial tty.'
[[ -d "$adapter_workdir" ]] || adapter_die 'Create a private workdir first.'
adapter_workdir=$(realpath -e -- "$adapter_workdir")
[[ "$adapter_workdir" != / && "$adapter_workdir" != /home && "$adapter_workdir" != "${HOME:-}" ]] || adapter_die 'Use a dedicated run directory.'
[[ "$adapter_read_baud" == 115200 || "$adapter_read_baud" == 460800 ]] || adapter_die 'Read baud must be 115200 or 460800.'
if [[ -n "$adapter_expected_mac" ]]; then
    [[ "$adapter_expected_mac" =~ ^([[:xdigit:]]{12}|([[:xdigit:]]{2}:){5}[[:xdigit:]]{2})$ ]] || adapter_die 'Expected MAC must be exactly twelve hex digits, optionally colon-separated.'
    adapter_expected_mac=${adapter_expected_mac//:/}
    adapter_expected_mac=${adapter_expected_mac,,}
fi
if [[ -n "$adapter_expected_bytes" ]]; then
    [[ "$adapter_expected_bytes" =~ ^[0-9]+$ ]] || adapter_die 'Flash bytes must be decimal.'
    [[ "$adapter_expected_bytes" == 8388608 || "$adapter_expected_bytes" == 16777216 ]] || adapter_die 'Only confirmed 8 MiB/16 MiB ESP32-S3 devices are in scope.'
fi
case "$adapter_mode" in
    probe) ((${#adapter_operation_args[@]} == 0)) || adapter_die 'probe takes no arguments.' ;;
    read-flash) ((${#adapter_operation_args[@]} == 3)) || adapter_die 'read-flash requires OFFSET LENGTH OUTPUT.' ;;
    write-flash)
        ((adapter_allow_write)) || adapter_die 'write-flash requires --allow-write.'
        ((${#adapter_operation_args[@]} >= 2 && ${#adapter_operation_args[@]} % 2 == 0)) || adapter_die 'write-flash takes OFFSET FILE pairs only.' ;;
    erase-flash)
        ((adapter_allow_erase)) || adapter_die 'erase-flash requires --allow-erase.'
        ((${#adapter_operation_args[@]} == 0)) || adapter_die 'erase-flash takes no arguments.' ;;
    erase-region)
        ((adapter_allow_erase)) || adapter_die 'erase-region requires --allow-erase.'
        ((${#adapter_operation_args[@]} == 2)) || adapter_die 'erase-region requires OFFSET LENGTH.' ;;
    reset-runtime)
        ((adapter_allow_reset)) || adapter_die 'reset-runtime requires --allow-reset.'
        ((${#adapter_operation_args[@]} == 0)) || adapter_die 'reset-runtime takes no arguments.' ;;
    *) adapter_die "Unsupported mode: $adapter_mode" ;;
esac
if ((adapter_watchdog_finish)); then
    [[ "$adapter_mode" == write-flash ]] || adapter_die '--watchdog-finish requires write-flash.'
fi
if ((adapter_allow_reset)); then
    [[ "$adapter_mode" == reset-runtime ]] || adapter_die '--allow-reset requires reset-runtime.'
fi
if [[ "$adapter_mode" != probe ]]; then
    [[ -n "$adapter_expected_mac" && -n "$adapter_expected_bytes" ]] || adapter_die 'Probe first, then supply the verified expected MAC and flash bytes.'
fi

# Validate every range and file before any serial operation. Numeric arguments
# deliberately accept decimal or 0x hex only, never shell arithmetic expressions.
adapter_number() {
    local input=$1
    [[ "$input" =~ ^(0[xX][[:xdigit:]]{1,8}|[0-9]{1,10})$ ]] || adapter_die "Invalid numeric argument: $input"
    if [[ "$input" == 0[xX]* ]]; then printf '%d\n' "$((16#${input:2}))";
    else printf '%d\n' "$((10#$input))"; fi
}
adapter_check_range() {
    local offset=$1 length=$2
    ((length > 0 && offset >= 0 && offset + length <= adapter_expected_bytes)) || adapter_die 'Flash range is outside the confirmed board size.'
}
if [[ "$adapter_mode" == read-flash || "$adapter_mode" == erase-region ]]; then
    adapter_offset=$(adapter_number "${adapter_operation_args[0]}")
    adapter_length=$(adapter_number "${adapter_operation_args[1]}")
    adapter_check_range "$adapter_offset" "$adapter_length"
    if [[ "$adapter_mode" == read-flash ]]; then
        adapter_output=$(realpath -m -- "${adapter_operation_args[2]}")
        [[ "$adapter_output" == "$adapter_workdir"/* && ! -e "$adapter_output" && ! -L "$adapter_output" ]] || adapter_die 'Read output must be a NEW file within the dedicated workdir.'
        [[ -d "$(dirname -- "$adapter_output")" ]] || adapter_die 'Read output parent directory must exist.'
        adapter_operation_args[2]=$adapter_output
    fi
elif [[ "$adapter_mode" == write-flash ]]; then
    for ((adapter_i=0; adapter_i<${#adapter_operation_args[@]}; adapter_i+=2)); do
        adapter_offset=$(adapter_number "${adapter_operation_args[adapter_i]}")
        adapter_image=$(realpath -e -- "${adapter_operation_args[adapter_i+1]}")
        [[ -f "$adapter_image" && -s "$adapter_image" ]] || adapter_die "Missing image: $adapter_image"
        adapter_length=$(stat -Lc '%s' -- "$adapter_image")
        adapter_check_range "$adapter_offset" "$adapter_length"
        adapter_operation_args[adapter_i+1]=$adapter_image
        sha256sum -- "$adapter_image"
    done
fi
for adapter_command in awk bash python3 pipx timeout udevadm fuser realpath sha256sum stat; do
    command -v "$adapter_command" >/dev/null || adapter_die "Required command is unavailable: $adapter_command"
done

adapter_state_dir=$(mktemp -d "$adapter_workdir/.mcfirmware-session.XXXXXX")
adapter_definitions="$adapter_state_dir/functions.sh"
# This is the same unindented-definition convention used by meshfirmware's
# extraction tests. No global assignment, command, trap or startup is included.
# A changed source layout fails bash -n before these definitions are sourced.
awk '
    { sub(/\r$/, "") }
    /^[A-Za-z_][A-Za-z0-9_]*\(\)[[:space:]]*\{[[:space:]]*$/ {
        if (capture) exit 2
        capture=1; count++
    }
    capture { print }
    capture && /^}[[:space:]]*$/ { capture=0 }
    END { if (capture || count < 100) exit 2 }
' "$adapter_flasher" > "$adapter_definitions"
bash -n "$adapter_definitions"
# shellcheck source=/dev/null
source "$adapter_definitions"
sha256sum -- "$adapter_flasher" "$adapter_definitions"

# Explicit private state only. Do not invoke the flasher's startup/cleanup.
PWD_SCRIPT=$(dirname -- "$(realpath -e -- "$adapter_flasher")")
FIRMWARE_ROOT=$adapter_state_dir
DOWNLOAD_DIR="$adapter_state_dir/downloads"
mkdir -p -- "$DOWNLOAD_DIR"
DEVICE_PORT=$adapter_port
DEVICE_PORT_FILE="$adapter_state_dir/selected-port"
DEVICE_PORT_NAME_FILE="$adapter_state_dir/selected-by-id"
ESPTOOL_PIPX_APP=''
ESPTOOL_VERSION_OUTPUT=''
ESPTOOL_CMD='pipx run --spec esptool esptool'
ESP32_SAFE_BAUD=115200
ESP32_FLASH_EXPECTED_MAC=''
ESP32_SESSION_IS_S3=0
ESP32_NATIVE_ROM_READY=0
ESP32_FLASH_SELECTED_BY_ID=''
ESP32_FLASH_EXPECTED_SERIAL=''
ESP32_FLASH_EXPECTED_PATH_STEM=''
BOOTLOADER_PROBE_PORT=''
BOOTLOADER_PROBE_ACTIVE=0
USB_RESET_FAILED=0
USB_RESET_RECOVERED_PORT=''
USB_RESET_EXPECTED_IDENTITY=''
LOCKEDSERVICE='None'
MCFIRMWARE_NO_SUDO=1
MESH_DISABLE_1200_RECOVERY=1
ESP32_PROBE_TIMEOUT_SECONDS=12
NRF52_DFU_REENUMERATE_TIMEOUT_SECONDS=30
NRF52_DFU_REENUMERATE_POLL_SECONDS=0.25
MESHCORE_USB_RESET_TOOL_SHA256='364de4c2e100df3ec2be795fc6079722ce1790416ba5a2d04d0c53e6c87c7083'
MESHCORE_ESP32_BOOTLOADER_TOOL_SHA256='8c48512a650892b9c8d024d926a4c7c85d9e9d30db0fddc990be6c0c108b4ea0'

adapter_assert_unowned() {
    local port=$1 output='' status=0
    # psmisc versions on the Pi do not all accept a '--' separator. The port
    # is already restricted to a /dev/ttyACM* or /dev/ttyUSB* path above.
    if output=$(fuser "$port" 2>&1); then
        printf '%s\n' "$output" >&2
        adapter_die "A process owns $port; no owner will be killed."
    else status=$?; fi
    [[ "$status" == 1 && -z "$output" ]] || adapter_die "Unable to prove $port has no owners: $output"
}
# Scope all privilege/busy/retry boundaries; keep actual chip, USB identity,
# esptool serial bootstrap, verification and watchdog finish implementations.
prepare_serial_port_for_flash() { adapter_assert_unowned "$1"; }
recover_busy_serial_port() { printf 'Busy serial port; refusing recovery.\n' >&2; return 1; }
offer_serial_port_recovery() { return 1; }
offer_identity_safe_1200_touch() { return 1; }
manual_reboot_choice() { return 1; }
esp32_recover_interrupted_transport() { return 1; }
esp32_run_chunked_write_recovery() { return 1; }
resolve_meshcore_usb_reset_tool() {
    local tool="$PWD_SCRIPT/tools/meshcore_usb_reset.py"
    meshcore_usb_reset_tool_hash_matches "$tool" || { printf 'Supply the matching bundled USB helper at %s\n' "$tool" >&2; return 1; }
    printf '%s\n' "$tool"
}
resolve_meshcore_esp32_bootloader_tool() {
    local tool="$PWD_SCRIPT/tools/meshcore_esp32_bootloader.py"
    resolve_meshcore_usb_reset_tool >/dev/null || return 1
    meshcore_esp32_bootloader_tool_hash_matches "$tool" || { printf 'Supply the matching bundled bootloader helper at %s\n' "$tool" >&2; return 1; }
    printf '%s\n' "$tool"
}
adapter_exit_notice() {
    if ((adapter_session_active)); then
        printf 'Session failed or was interrupted. No automatic reset/recovery was attempted; reselect the same radio before continuing. State: %s\n' "$adapter_state_dir" >&2
    fi
}
trap adapter_exit_notice EXIT
esptool_set_variables
[[ "$NORESET" == no-reset ]] || adapter_die 'This adapter requires esptool 5 or newer.'
ESP32_OPERATION_BEFORE=$NORESET
adapter_session_active=1
prepare_esp32_flash_session "$adapter_port" "$adapter_board"
[[ "$ESP32_SESSION_IS_S3" == 1 ]] || adapter_die 'Probe did not identify ESP32-S3.'
if ((adapter_watchdog_finish)) || [[ "$adapter_mode" == reset-runtime ]]; then
    [[ "${WATCHDOGRESET:-}" == watchdog-reset ]] || adapter_die 'A supported S3 watchdog reset is required before writing or resetting.'
fi
if [[ -n "$adapter_expected_mac" && "$ESP32_FLASH_EXPECTED_MAC" != "$adapter_expected_mac" ]]; then
    adapter_die "MAC mismatch: typed $adapter_expected_mac, probe $ESP32_FLASH_EXPECTED_MAC."
fi
adapter_live_port=$(selected_flash_serial_port "$DEVICE_PORT")
adapter_assert_unowned "$adapter_live_port"
adapter_flash_output=$(run_esp32_session_esptool "$adapter_live_port" --after "$NORESET" --baud 115200 flash-id)
printf '%s\n' "$adapter_flash_output"
[[ "$adapter_flash_output" =~ Detected[[:space:]]flash[[:space:]]size:[[:space:]]*([0-9]+)MB ]] || adapter_die 'No unambiguous flash size was reported.'
adapter_detected_bytes=$((BASH_REMATCH[1] * 1024 * 1024))
if [[ -n "$adapter_expected_bytes" && "$adapter_detected_bytes" != "$adapter_expected_bytes" ]]; then
    adapter_die "Flash size mismatch: expected $adapter_expected_bytes, detected $adapter_detected_bytes."
fi
printf 'VERIFIED mac=%s chip=ESP32-S3 flash_bytes=%s port=%s\n' "$ESP32_FLASH_EXPECTED_MAC" "$adapter_detected_bytes" "$adapter_live_port"
if [[ "$adapter_mode" != probe && "$adapter_mode" != reset-runtime ]]; then
    # Re-resolve by-id and re-probe MAC for READS too. Destructive commands also
    # repeat this inside the untouched run_esp32_session_esptool implementation.
    adapter_live_port=$(esp32_verified_destructive_port "$DEVICE_PORT" "$adapter_mode")
    adapter_assert_unowned "$adapter_live_port"
    adapter_baud=115200
    [[ "$adapter_mode" != read-flash ]] || adapter_baud=$adapter_read_baud
    run_esp32_session_esptool "$adapter_live_port" --after "$NORESET" --baud "$adapter_baud" "$adapter_mode" "${adapter_operation_args[@]}"
    if [[ "$adapter_mode" == read-flash ]]; then
        [[ "$(stat -Lc '%s' -- "$adapter_output")" == "$adapter_length" ]] || adapter_die 'Backup read length differs from requested range.'
        sha256sum -- "$adapter_output"
    fi
fi
if ((adapter_watchdog_finish)) || [[ "$adapter_mode" == reset-runtime ]]; then
    # In write-flash mode the write above must already have succeeded, including
    # the real flasher's positive flash-hash verification. reset-runtime instead
    # reaches here without any write/erase operation or corresponding authority.
    # A USB/JTAG bootloader entry
    # can retain FORCE_DOWNLOAD_BOOT, so a watchdog alone would return to ROM.
    # Clear only bit 0 of volatile RTC_CNTL_OPTION1, as in the S3 ROM helper;
    # this is not a flash, eFuse, erase or persistent bootloader modification.
    # Re-prove the bound USB identity and chip MAC before EACH finish phase.
    adapter_live_port=$(esp32_verified_destructive_port "$DEVICE_PORT" 'S3 volatile forced-ROM bit clear')
    adapter_assert_unowned "$adapter_live_port"
    run_esp32_session_esptool "$adapter_live_port" --after "$NORESET" --baud 115200 \
        write-mem 0x6000812c 0 0x1
    adapter_live_port=$(esp32_verified_destructive_port "$DEVICE_PORT" 'S3 watchdog finish')
    adapter_assert_unowned "$adapter_live_port"
    # Once reset may have started the application, no automatic ROM recovery
    # or second reset is authorized. Any lost final reply is an explicit error.
    BOOTLOADER_PROBE_ACTIVE=0
    BOOTLOADER_PROBE_PORT=''
    run_esp32_session_esptool "$adapter_live_port" --after "$WATCHDOGRESET" --baud 115200 "$READMAC"
    ESP32_NATIVE_ROM_READY=0
else
    finish_esp32_flash_session "$DEVICE_PORT"
fi
adapter_session_active=0
printf 'Session finished. Application boot still requires an application-level check. State: %s\n' "$adapter_state_dir"
