#!/usr/bin/env bash
# Read-only flash backup through the real mcfirmware ESP32 session helpers.
# ROM entry and runtime finish reset the explicitly selected board. No flash
# write/erase command, service stop, owner kill, or automatic recovery is used.
set -euo pipefail
umask 077

backup_die() { printf 'Backup refused: %s\n' "$*" >&2; exit 1; }
backup_usage() {
    printf '%s\n' \
      'Usage: mcfirmware_bench_backup.sh --port /dev/ttyACM... --usb-path EXACT_BASENAME' \
      '  --usb-id EXACT_BASENAME --device LABEL --bench-dir PRIVATE_DIRECTORY' \
      '  --expect-mac XX:XX:XX:XX:XX:XX --flash-bytes 0x1000000' \
      '  --offset 0 --length 0x1000000 --output /absolute/NEW_BACKUP.bin' \
      '  [--flasher /home/mikec/meshfirmware/mcfirmware.sh] [--read-baud 115200]' \
      'A new OUTPUT.json report is created alongside the backup. Both are private.' \
      'Only explicit ranges on a positively identified ESP32-S3 are supported.'
}

backup_flasher=/home/mikec/meshfirmware/mcfirmware.sh
backup_requested_port='' backup_usb_path='' backup_usb_id='' backup_device=''
backup_bench='' backup_expected_mac='' backup_flash_bytes='' backup_offset=''
backup_length='' backup_output='' backup_baud=115200 backup_active=0
while (($#)); do
    case "$1" in
        --flasher|--port|--usb-path|--usb-id|--device|--bench-dir|--expect-mac|--flash-bytes|--offset|--length|--output|--read-baud)
            (($# >= 2)) || backup_die "Missing value for $1"
            case "$1" in
                --flasher) backup_flasher=$2 ;; --port) backup_requested_port=$2 ;;
                --usb-path) backup_usb_path=$2 ;; --usb-id) backup_usb_id=$2 ;;
                --device) backup_device=$2 ;; --bench-dir) backup_bench=$2 ;;
                --expect-mac) backup_expected_mac=$2 ;; --flash-bytes) backup_flash_bytes=$2 ;;
                --offset) backup_offset=$2 ;; --length) backup_length=$2 ;;
                --output) backup_output=$2 ;; --read-baud) backup_baud=$2 ;;
            esac
            shift 2 ;;
        -h|--help) backup_usage; exit 0 ;;
        *) backup_die "Unknown option: $1" ;;
    esac
done
[[ -f "$backup_flasher" && -s "$backup_flasher" ]] || backup_die 'The complete local flasher is required.'
[[ "$backup_requested_port" =~ ^/dev/tty(ACM|USB)[0-9]+$ ]] || backup_die 'Select one local USB serial tty.'
for backup_identity in "$backup_usb_path" "$backup_usb_id"; do
    [[ -n "$backup_identity" && "$backup_identity" != */* && "$backup_identity" != .* ]] || backup_die 'Exact USB by-path and by-id basenames are required.'
done
[[ -n "$backup_device" && "$backup_bench" == /* && -d "$backup_bench" ]] || backup_die 'Create a dedicated absolute bench directory and supply the board label.'
backup_bench=$(realpath -e -- "$backup_bench")
[[ "$(stat -Lc '%u' -- "$backup_bench")" == "$(id -u)" ]] || backup_die 'The bench directory must belong to this user.'
backup_permissions=$(stat -Lc '%a' -- "$backup_bench")
(( (8#$backup_permissions & 077) == 0 )) || backup_die 'The bench directory must have no group/other permissions.'
[[ "$backup_baud" == 115200 ]] || backup_die 'This backup wrapper permits only conservative 115200 baud.'
[[ "$backup_expected_mac" =~ ^([[:xdigit:]]{12}|([[:xdigit:]]{2}:){5}[[:xdigit:]]{2})$ ]] || backup_die 'Supply an explicit twelve-digit expected MAC.'
backup_expected_mac=${backup_expected_mac//:/}
backup_expected_mac=${backup_expected_mac,,}
backup_number() {
    [[ "$1" =~ ^(0[xX][[:xdigit:]]{1,8}|[0-9]{1,10})$ ]] || backup_die 'Ranges must be explicit decimal or hexadecimal integers.'
    if [[ "$1" == 0[xX]* ]]; then printf '%d\n' "$((16#${1:2}))"; else printf '%d\n' "$((10#$1))"; fi
}
backup_flash_bytes=$(backup_number "$backup_flash_bytes")
backup_offset=$(backup_number "$backup_offset")
backup_length=$(backup_number "$backup_length")
(( backup_flash_bytes == 8388608 || backup_flash_bytes == 16777216 )) || backup_die 'Only confirmed 8 MiB or 16 MiB boards are supported.'
(( backup_length > 0 && backup_offset + backup_length <= backup_flash_bytes )) || backup_die 'The requested range exceeds the confirmed flash capacity.'
[[ "$backup_output" == /* ]] || backup_die 'Supply an absolute new backup filename.'
backup_output=$(realpath -m -- "$backup_output")
[[ "$backup_output" == "$backup_bench"/* && ! -e "$backup_output" && ! -L "$backup_output" ]] || backup_die 'Backup output must be a new file inside the private bench directory.'
[[ "$(dirname -- "$backup_output")" == "$backup_bench" ]] || backup_die 'Place the backup directly in the private bench directory.'
backup_report="$backup_output.json"
[[ ! -e "$backup_report" && ! -L "$backup_report" ]] || backup_die 'The backup report already exists.'
for backup_command in awk bash python3 pipx timeout udevadm fuser sudo realpath sha256sum stat readlink; do
    command -v "$backup_command" >/dev/null || backup_die "Required command unavailable: $backup_command"
done

backup_assert_unowned() {
    local output='' status=0
    if output=$(sudo -n fuser "$1" 2>&1); then backup_die 'The selected serial port has an owner; no process will be killed.'; else status=$?; fi
    [[ "$status" == 1 && -z "$output" ]] || backup_die 'Unable to prove that the selected serial port is unowned.'
}
backup_guard_usb() {
    local selected physical by_id
    physical=$(readlink -e -- "/dev/serial/by-path/$backup_usb_path") || backup_die 'The exact physical USB path is absent.'
    selected=$(readlink -e -- "${DEVICE_PORT:-$backup_requested_port}") || backup_die 'The selected serial endpoint is absent.'
    [[ "$selected" == "$physical" && -c "$physical" ]] || backup_die 'The selected endpoint no longer matches the physical USB path.'
    # ROM handoff may rename the product/by-id name. Its exact initial link,
    # physical path, and silicon MAC are independently pinned before reading.
    if [[ "${ESP32_FLASH_EXPECTED_MAC:-}" == '' ]]; then
        by_id=$(readlink -e -- "/dev/serial/by-id/$backup_usb_id") || backup_die 'The exact initial USB by-id link is absent.'
        [[ "$by_id" == "$physical" ]] || backup_die 'USB by-id and physical path disagree.'
    fi
    backup_assert_unowned "$selected"
}
backup_guard_usb
backup_state=$(mktemp -d "$backup_bench/.mcfirmware-backup.XXXXXX")
backup_definitions="$backup_state/functions.sh"
# Extract definitions only; never source startup, global host changes, or traps.
awk '
    { sub(/\r$/, "") }
    /^[A-Za-z_][A-Za-z0-9_]*\(\)[[:space:]]*\{[[:space:]]*$/ {
        if (capture) exit 2
        capture=1; count++
    }
    capture { print }
    capture && /^}[[:space:]]*$/ { capture=0 }
    END { if (capture || count < 100) exit 2 }
' "$backup_flasher" > "$backup_definitions"
bash -n "$backup_definitions"
# shellcheck source=/dev/null
source "$backup_definitions"
for backup_function in esptool_set_variables save_selected_serial_port prepare_esp32_flash_session selected_flash_serial_port esp32_record_and_verify_probe_output esp32_verified_destructive_port run_esp32_session_esptool esp32_write_after_mode finish_esp32_flash_session; do
    declare -F "$backup_function" >/dev/null || backup_die "Flasher lacks required function: $backup_function"
done

# Private session state, equivalent to the existing bench-only flasher adapter.
PWD_SCRIPT=$(dirname -- "$(realpath -e -- "$backup_flasher")")
FIRMWARE_ROOT=$backup_state
DOWNLOAD_DIR="$backup_state/downloads"
mkdir -- "$DOWNLOAD_DIR"
DEVICE_PORT=$backup_requested_port
DEVICE_PORT_FILE="$backup_state/selected-port"
DEVICE_PORT_NAME_FILE="$backup_state/selected-by-id"
DEVICE=$backup_device ARCHITECTURE=esp32
ESPTOOL_PIPX_APP='' ESPTOOL_VERSION_OUTPUT='' ESPTOOL_CMD='pipx run --spec esptool esptool'
ESP32_SAFE_BAUD=115200 ESP32_FLASH_EXPECTED_MAC='' ESP32_SESSION_IS_S3=0 ESP32_NATIVE_ROM_READY=0
ESP32_FLASH_SELECTED_BY_ID='' ESP32_FLASH_EXPECTED_SERIAL='' ESP32_FLASH_EXPECTED_PATH_STEM=''
BOOTLOADER_PROBE_PORT='' BOOTLOADER_PROBE_ACTIVE=0 USB_RESET_FAILED=0
USB_RESET_RECOVERED_PORT='' USB_RESET_EXPECTED_IDENTITY=''
LOCKEDSERVICE=None MCFIRMWARE_NO_SUDO=1 MESH_DISABLE_1200_RECOVERY=1
ESP32_PROBE_TIMEOUT_SECONDS=12 NRF52_DFU_REENUMERATE_TIMEOUT_SECONDS=30 NRF52_DFU_REENUMERATE_POLL_SECONDS=0.25
MESHCORE_USB_RESET_TOOL_SHA256=$(sed -n 's/^MESHCORE_USB_RESET_TOOL_SHA256="\([0-9a-f]\{64\}\)"$/\1/p' "$backup_flasher")
[[ "$MESHCORE_USB_RESET_TOOL_SHA256" =~ ^[0-9a-f]{64}$ ]] || backup_die 'The installed flasher must pin its bundled USB helper.'
MESHCORE_ESP32_BOOTLOADER_TOOL_SHA256=$(sed -n 's/^MESHCORE_ESP32_BOOTLOADER_TOOL_SHA256="\([0-9a-f]\{64\}\)"$/\1/p' "$backup_flasher")
# Restrict host-affecting boundaries; preserve actual USB/MAC/bootstrap/finish.
prepare_serial_port_for_flash() { backup_assert_unowned "$1"; }
recover_busy_serial_port() { return 1; }
offer_serial_port_recovery() { return 1; }
offer_identity_safe_1200_touch() { return 1; }
manual_reboot_choice() { return 1; }
esp32_recover_interrupted_transport() { return 1; }
esp32_run_chunked_write_recovery() { return 1; }
resolve_meshcore_usb_reset_tool() {
    local tool="$PWD_SCRIPT/tools/meshcore_usb_reset.py"
    meshcore_usb_reset_tool_hash_matches "$tool" || return 1
    printf '%s\n' "$tool"
}
if declare -F meshcore_esp32_bootloader_tool_hash_matches >/dev/null; then
    [[ "$MESHCORE_ESP32_BOOTLOADER_TOOL_SHA256" =~ ^[0-9a-f]{64}$ ]] || backup_die 'The flasher must pin its bundled ESP32 bootloader helper.'
    resolve_meshcore_esp32_bootloader_tool() {
        local tool="$PWD_SCRIPT/tools/meshcore_esp32_bootloader.py"
        resolve_meshcore_usb_reset_tool >/dev/null || return 1
        meshcore_esp32_bootloader_tool_hash_matches "$tool" || return 1
        printf '%s\n' "$tool"
    }
fi
backup_notice() {
    if ((backup_active)); then
        printf 'Backup/session incomplete. No automatic recovery attempted; inspect private state %s before continuing.\n' "$backup_state" >&2
    fi
}
trap backup_notice EXIT
esptool_set_variables > "$backup_state/esptool-version.log" 2>&1
[[ "$NORESET" == no-reset && "$READFLASH" == read-flash ]] || backup_die 'The backup requires esptool 5 or newer.'
ESP32_OPERATION_BEFORE=$NORESET
backup_guard_usb
backup_active=1
prepare_esp32_flash_session "$DEVICE_PORT" "$DEVICE" > "$backup_state/prepare.log" 2>&1
[[ "$ESP32_SESSION_IS_S3" == 1 && "$ESP32_FLASH_EXPECTED_MAC" == "$backup_expected_mac" ]] || backup_die 'The session chip/MAC differs from the explicit ESP32-S3 target.'
backup_guard_usb
backup_live_port=$(selected_flash_serial_port "$DEVICE_PORT")
DEVICE_PORT=$backup_live_port
backup_guard_usb
run_esp32_session_esptool "$DEVICE_PORT" --after "$NORESET" --baud 115200 "$FLASHID" > "$backup_state/flash-id.log" 2>&1
backup_probe=$(cat "$backup_state/flash-id.log")
esp32_record_and_verify_probe_output "$backup_probe" > "$backup_state/mac-check.log" 2>&1
[[ "$ESP32_FLASH_EXPECTED_MAC" == "$backup_expected_mac" ]] || backup_die 'The flash probe MAC differs from the explicit target.'
[[ "$backup_probe" =~ Detected[[:space:]]flash[[:space:]]size:[[:space:]]*([0-9]+)MB ]] || backup_die 'The flash probe did not report an unambiguous capacity.'
(( BASH_REMATCH[1] * 1024 * 1024 == backup_flash_bytes )) || backup_die 'The detected flash capacity differs from the explicit target.'
backup_live_port=$(esp32_verified_destructive_port "$DEVICE_PORT" 'read-only full/range flash backup')
DEVICE_PORT=$backup_live_port
backup_guard_usb
[[ "$ESP32_FLASH_EXPECTED_MAC" == "$backup_expected_mac" ]] || backup_die 'The pre-read MAC differs from the explicit target.'
# Reserve both outputs exclusively before the read. The directory is private,
# and esptool writes only the newly reserved 0600 backup pathname.
(set -o noclobber; : > "$backup_output"; : > "$backup_report") || backup_die 'An output already exists; refusing to overwrite it.'
chmod 600 -- "$backup_output" "$backup_report"
run_esp32_session_esptool "$DEVICE_PORT" --after "$NORESET" --baud "$backup_baud" "$READFLASH" "$backup_offset" "$backup_length" "$backup_output" > "$backup_state/read.log" 2>&1
[[ -f "$backup_output" && ! -L "$backup_output" && "$(stat -Lc '%s' -- "$backup_output")" == "$backup_length" ]] || backup_die 'The backup length differs from the requested range.'
chmod 600 -- "$backup_output"
backup_guard_usb
# UART ESP32 finish is normally a no-op: its final probe must supply the reset.
backup_after=$(esp32_write_after_mode "$DEVICE_PORT")
if [[ "$backup_after" != "$NORESET" ]]; then
    run_esp32_session_esptool "$DEVICE_PORT" --after "$backup_after" --baud 115200 "$READMAC" > "$backup_state/runtime-reset.log" 2>&1
fi
finish_esp32_flash_session "$DEVICE_PORT" > "$backup_state/finish.log" 2>&1
BOOTLOADER_PROBE_ACTIVE=0 BOOTLOADER_PROBE_PORT=''
backup_active=0
python3 - "$backup_output" "$backup_report" "$backup_state" "$backup_flasher" "$backup_definitions" "$backup_expected_mac" "$backup_flash_bytes" "$backup_offset" "$backup_length" <<'PY'
import hashlib, json, os, pathlib, sys
output, report, state, flasher, definitions, mac, capacity, offset, length = sys.argv[1:]
def digest(path):
    value = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()
result = dict(schema_version=1, operation='read-only-flash-backup', complete=True,
              artifact=pathlib.Path(output).name, sha256=digest(output),
              device_mac=mac, chip='ESP32-S3', flash_bytes=int(capacity),
              offset=int(offset), length=int(length), baud=115200,
              flasher_sha256=digest(flasher), definitions_sha256=digest(definitions),
              flash_probe_sha256=digest(pathlib.Path(state)/'flash-id.log'),
              session_finish='flasher completed; application health requires a separate check')
with open(report, 'w', encoding='utf-8') as stream:
    json.dump(result, stream, indent=2)
    stream.write('\n')
os.chmod(report, 0o600)
print(json.dumps(result, sort_keys=True))
PY
