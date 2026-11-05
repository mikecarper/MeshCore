#!/usr/bin/env bash
# Bench-only noninteractive entry point to mcfirmware.sh's real ESP32 flasher.
# The user must authorize resets/erasure of the explicitly selected radio.
# No direct esptool bypasses: preserve flasher USB/MAC binding, safe serial open,
# transport recovery, verification and guarded runtime handoff.
set -euo pipefail

flasher=/home/mikec/meshfirmware/mcfirmware.sh
bench_dir=''
requested_port=''
expected_usb_path=''
expected_usb_id=''
expected_chip_mac=''
device=''
image=''
probe=0
watchdog_recover=0
erase=0
capture_runtime=''
capture_protocol=infrastructure
write_baud=115200
bench_script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

usage() {
    printf '%s\n' \
        'Usage: mcfirmware_bench_flash.sh --port DEVICE --usb-path EXACT_BY_PATH_NAME' \
        '  --device BUILD_BOARD_NAME --bench-dir ABSOLUTE_BENCH_DIR' \
        '  [--usb-id EXACT_BY_ID_NAME] [--flasher PATH]' \
        '  [--expect-mac AA:BB:CC:DD:EE:FF]' \
        '  [--capture-runtime ABSOLUTE_NEW_RAW_FILE]' \
        '  [--capture-protocol infrastructure|companion]' \
        '  [--write-baud 115200|230400|460800|921600]' \
        '  (--probe | --watchdog-recover | --image ABSOLUTE_MERGED_BIN --erase)' \
        '' \
        '--probe resets only the selected radio, reads chip/flash IDs, then restores runtime.' \
        '--watchdog-recover is S3-only: clear volatile forced-ROM bit, reset target MCU by watchdog.' \
        '  No flash erasure/write; runtime startup must be verified separately afterward.' \
        '--image requires explicit --erase and a content-verified merged ESP32 image.' \
        '--capture-runtime is S3 HWCDC only; capture boot bytes and require a completed ver reply.' \
        '  Uses MESHFIRMWARE_CAPTURE_PYTHON (default python3), with pyserial installed.' \
        '  Requires the selected USB by-id and by-path links to remain stable across resets.' \
        '  Infrastructure capture contains raw serial output; review it before sharing.' \
        'Uses a new isolated flasher-cache child under --bench-dir; skips startup menu,' \
        'global USB autosuspend/group changes and Pi reboot/speed prompts.'
}

while (($#)); do
    case "$1" in
        --flasher|--bench-dir|--port|--usb-path|--usb-id|--expect-mac|--device|--image|--capture-runtime|--capture-protocol|--write-baud)
            (($# >= 2)) || { usage >&2; exit 2; }
            case "$1" in
                --flasher) flasher=$2 ;;
                --bench-dir) bench_dir=$2 ;;
                --port) requested_port=$2 ;;
                --usb-path) expected_usb_path=$2 ;;
                --usb-id) expected_usb_id=$2 ;;
                --expect-mac) expected_chip_mac=$2 ;;
                --device) device=$2 ;;
                --image) image=$2 ;;
                --capture-runtime) capture_runtime=$2 ;;
                --capture-protocol) capture_protocol=$2 ;;
                --write-baud) write_baud=$2 ;;
            esac
            shift 2 ;;
        --probe) probe=1; shift ;;
        --watchdog-recover) watchdog_recover=1; shift ;;
        --erase) erase=1; shift ;;
        --help|-h) usage; exit 0 ;;
        *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
done
case "$write_baud" in
    115200|230400|460800|921600) ;;
    *) echo 'Unsupported write baud.' >&2; exit 2 ;;
esac
if [[ -n "$expected_chip_mac" ]]; then
    [[ "$expected_chip_mac" =~ ^([[:xdigit:]]{12}|([[:xdigit:]]{2}:){5}[[:xdigit:]]{2})$ ]] || {
        echo 'Expected chip MAC must be twelve hexadecimal digits, optionally colon-separated.' >&2; exit 2;
    }
    expected_chip_mac=${expected_chip_mac//:/}
    expected_chip_mac=${expected_chip_mac,,}
fi
case "$capture_protocol" in
    infrastructure|companion) ;;
    *) echo 'Unsupported runtime capture protocol.' >&2; exit 2 ;;
esac

[[ -f "$flasher" && -n "$requested_port" && -n "$device" && "$bench_dir" == /* ]] \
    || { usage >&2; exit 2; }
[[ -n "$expected_usb_path" && "$expected_usb_path" != */* && "$expected_usb_path" != .* ]] \
    || { echo 'An exact /dev/serial/by-path basename is required.' >&2; exit 2; }
[[ -z "$expected_usb_id" || ( "$expected_usb_id" != */* && "$expected_usb_id" != .* ) ]] \
    || { echo '--usb-id must be an exact basename.' >&2; exit 2; }
if ((probe + watchdog_recover > 1)); then
    echo '--probe and --watchdog-recover are mutually exclusive.' >&2; exit 2
elif ((probe || watchdog_recover)); then
    [[ -z "$image" && "$erase" == 0 ]] || { echo 'Probe/recovery cannot erase or install.' >&2; exit 2; }
else
    [[ "$erase" == 1 && "$image" == /* && -r "$image" && -s "$image" ]] \
        || { echo 'Installing requires --image ABSOLUTE_MERGED_BIN and explicit --erase.' >&2; exit 2; }
fi
if [[ -n "$capture_runtime" ]]; then
    [[ "$watchdog_recover" == 0 && "$capture_runtime" == /* && -n "$expected_usb_id" ]] || {
        echo '--capture-runtime requires --usb-id and probe/install mode.' >&2; exit 2;
    }
    capture_python=${MESHFIRMWARE_CAPTURE_PYTHON:-python3}
    capture_helper="$bench_script_dir/capture_infrastructure_boot.py"
    command -v "$capture_python" >/dev/null && [[ -r "$capture_helper" ]] || {
        echo 'Runtime capture requires its Python helper and configured interpreter.' >&2; exit 2;
    }
fi

guard_selected_usb() {
    local live_path live_port live_id
    live_path=$(readlink -e -- "/dev/serial/by-path/$expected_usb_path") || {
        echo 'The exact selected USB path is absent; refusing another device.' >&2; return 1;
    }
    live_port=$(readlink -e -- "${DEVICE_PORT:-$requested_port}") || return 1
    [[ "$live_port" == "$live_path" ]] || {
        echo 'Selected serial port no longer matches the required physical USB path.' >&2; return 1;
    }
    # Initial by-id must be exact. After the flasher has bound the silicon MAC,
    # a runtime-to-ROM product-name change is legitimate; the physical path plus
    # its own immediate MAC probe still gate every destructive operation.
    if [[ -n "$expected_usb_id" && -z "${ESP32_FLASH_EXPECTED_MAC:-}" ]]; then
        live_id=$(readlink -e -- "/dev/serial/by-id/$expected_usb_id") || return 1
        [[ "$live_id" == "$live_path" ]] || {
            echo 'The exact selected USB by-id and by-path identities disagree.' >&2; return 1;
        }
    fi
    [[ -c "$live_path" ]] || return 1
}

# Validate before loading any flasher state or installing any cleanup handler.
guard_selected_usb
grep -q '^# MAIN$' "$flasher" || { echo 'Unsupported flasher: MAIN marker absent.' >&2; exit 1; }
# Source functions and their normal global defaults, but not the three startup
# actions or their traps. A plain prefix source would change global host USB
# power settings and could offer a Pi reboot before MAIN even starts.
source <(awk '
    /^# MAIN$/ { exit }
    /^(initialize_privilege_mode|meshfirmware_check_pi_usb_host_speed|configure_usb_autosuspend)$/ { next }
    /^trap / { next }
    { print }
' "$flasher")

PWD_SCRIPT=$(cd -- "$(dirname -- "$flasher")" && pwd)
mkdir -p -- "$bench_dir"
FIRMWARE_ROOT=$(mktemp -d "$bench_dir/mcfirmware-cache.XXXXXX")
DOWNLOAD_DIR="$FIRMWARE_ROOT/downloads"
mkdir -p -- "$DOWNLOAD_DIR"
DEVICE_PORT_FILE="$FIRMWARE_ROOT/09device_port_file.txt"
DEVICE_PORT_NAME_FILE="$FIRMWARE_ROOT/10device_port_name_file.txt"
ESPTOOL_FILE="$FIRMWARE_ROOT/12esptool_file.txt"
DEVICE="$device"
ARCHITECTURE=esp32
MESH_DISABLE_1200_RECOVERY=1
MESHFIRMWARE_PI_USB_CHECK=0
trap cleanup EXIT
trap 'error_handler $LINENO' ERR

for required in save_selected_serial_port prepare_esp32_flash_session \
        run_esp32_session_esptool finish_esp32_flash_session esp32_prepare_merged_ota_mirror; do
    declare -F "$required" >/dev/null || { echo "Flasher lacks required safety function: $required" >&2; exit 1; }
done
ensure_pipx_uv_backend
get_espcmd
DEVICE_PORT=$(readlink -e -- "$requested_port")
guard_selected_usb
save_selected_serial_port "$DEVICE_PORT"
printf 'Bench target: device=%s port=%s usb_path=%s\n' "$DEVICE" "$DEVICE_PORT" "$expected_usb_path"
printf 'Flasher cache: %s\n' "$FIRMWARE_ROOT"

if ((!probe && !watchdog_recover)); then
    [[ "$(esp_firmware_layout "$image")" == merged ]] || {
        echo 'Image is not content-verified merged firmware; refusing erase.' >&2; exit 1;
    }
    esp32_validate_image_for_device "$DEVICE" "$image" merged
    ESP32_MERGED_WRITE_ARGS=(0x0000 "$image")
    ESP32_MERGED_OTA_IMAGE=$(mktemp "$FIRMWARE_ROOT/mirror.XXXXXX.bin")
    mirror_output=$(esp32_prepare_merged_ota_mirror "$image" "$ESP32_MERGED_OTA_IMAGE")
    if [[ -n "$mirror_output" ]]; then
        mapfile -t mirror_offsets <<< "$mirror_output"
        for offset in "${mirror_offsets[@]}"; do
            ESP32_MERGED_WRITE_ARGS+=("$offset" "$ESP32_MERGED_OTA_IMAGE")
        done
    fi
fi

prepare_esp32_flash_session "$DEVICE_PORT" "$DEVICE"
guard_selected_usb
[[ "${ESP32_FLASH_EXPECTED_MAC:-}" =~ ^[0-9a-f]{12}$ ]] || {
    echo 'Guarded session did not bind a valid ESP32 silicon MAC; refusing write.' >&2; exit 1;
}
if [[ -n "$expected_chip_mac" && "$ESP32_FLASH_EXPECTED_MAC" != "$expected_chip_mac" ]]; then
    echo 'Guarded ESP32 silicon MAC differs from the explicitly selected target; refusing write.' >&2
    exit 1
fi
printf 'Verified ESP32 chip MAC: %s\n' "$ESP32_FLASH_EXPECTED_MAC"
if [[ -n "$capture_runtime" ]]; then
    [[ "${ESP32_SESSION_IS_S3:-0}" == 1 && -n "${WATCHDOGRESET:-}" ]] || {
        echo 'Runtime capture requires a positively identified ESP32-S3.' >&2; exit 1;
    }
    capture_args=(--protocol "$capture_protocol" --usb-path "$expected_usb_path" --usb-id "$expected_usb_id"
        --expected-serial "$ESP32_FLASH_EXPECTED_MAC" --output "$capture_runtime")
    # Check imports, output destination and all USB identities before erasing.
    "$capture_python" "$capture_helper" "${capture_args[@]}" --check-only
fi
if [[ "$NORESET" == no_reset ]]; then
    flash_id_command=flash_id
    chip_id_command=chip_id
    write_mem_command=write_mem
else
    flash_id_command=flash-id
    chip_id_command=chip-id
    write_mem_command=write-mem
fi
run_esp32_session_esptool "$DEVICE_PORT" --after "$NORESET" --baud 115200 "$chip_id_command"
probe_after_mode=$NORESET
if ((probe)); then
    # finish_esp32_flash_session() only needs an explicit reset for S3/native
    # USB. Ordinary UART ESP32 probes must request their normal final hard reset
    # here, just as the real flasher's write command would have done.
    probe_after_mode=$(esp32_write_after_mode "$DEVICE_PORT")
fi
run_esp32_session_esptool "$DEVICE_PORT" --after "$probe_after_mode" --baud 115200 "$flash_id_command"
if ((watchdog_recover)); then
    [[ "${ESP32_SESSION_IS_S3:-0}" == 1 && -n "${WATCHDOGRESET:-}" ]] || {
        echo 'Watchdog recovery requires a positively identified ESP32-S3 and supported reset mode.' >&2
        exit 1
    }
    # Native USB's bootloader-entry sequence sets FORCE_DOWNLOAD_BOOT. A WDT
    # alone would retain it. This is the exact masked volatile-register clear
    # used by esptool.targets.esp32s3.ESP32S3ROM.hard_reset(), not a flash write.
    guard_selected_usb
    DEVICE_PORT=$(esp32_verified_destructive_port "$DEVICE_PORT" "S3 volatile forced-ROM bit clear")
    guard_selected_usb
    run_esp32_session_esptool "$DEVICE_PORT" --after "$NORESET" --baud 115200 \
        "$write_mem_command" 0x6000812c 0 0x1
    guard_selected_usb
    DEVICE_PORT=$(esp32_verified_destructive_port "$DEVICE_PORT" "S3 watchdog recovery")
    guard_selected_usb
    run_esp32_session_esptool "$DEVICE_PORT" --after "$WATCHDOGRESET" --baud 115200 "$READMAC"
    BOOTLOADER_PROBE_ACTIVE=0
    BOOTLOADER_PROBE_PORT=''
    echo 'Targeted S3 watchdog recovery requested; flash unchanged. Runtime is not yet verified.'
    exit 0
fi
if ((!probe)); then
    guard_selected_usb
    run_esp32_session_esptool "$DEVICE_PORT" --after "$NORESET" --baud 115200 "$ERASEFLASH"
    sleep 1
    guard_selected_usb
    after_mode=$(esp32_write_after_mode "$DEVICE_PORT")
    run_esp32_session_esptool "$DEVICE_PORT" --after "$after_mode" --baud "$write_baud" "$WRITEFLASH" \
        "${ESP32_MERGED_WRITE_ARGS[@]}"
fi
if [[ -n "$capture_runtime" ]]; then
    # S3 HWCDC applications legitimately retain the ROM's USB product name.
    # Keep every MAC/write guard, but establish runtime from its real reply.
    # This mirrors the guarded --watchdog-recover sequence above: the masked
    # write only clears the volatile forced-download bit; it never changes flash.
    guard_selected_usb
    DEVICE_PORT=$(esp32_verified_destructive_port "$DEVICE_PORT" "S3 capture forced-ROM bit clear")
    guard_selected_usb
    run_esp32_session_esptool "$DEVICE_PORT" --after "$NORESET" --baud 115200 \
        "$write_mem_command" 0x6000812c 0 0x1
    guard_selected_usb
    DEVICE_PORT=$(esp32_verified_destructive_port "$DEVICE_PORT" "S3 capture watchdog reset")
    guard_selected_usb
    run_esp32_session_esptool "$DEVICE_PORT" --after "$WATCHDOGRESET" --baud 115200 "$READMAC"
    # The intended reset is complete. A failed runtime diagnosis must retain
    # its first boot evidence, rather than trigger another cleanup reset.
    BOOTLOADER_PROBE_ACTIVE=0
    BOOTLOADER_PROBE_PORT=''
    "$capture_python" "$capture_helper" "${capture_args[@]}"
else
    finish_esp32_flash_session "$DEVICE_PORT"
fi
# Successful finish already verified the runtime USB handoff. Retain guarded
# cleanup recovery for failures, but don't reset that proven runtime a second time.
BOOTLOADER_PROBE_ACTIVE=0
BOOTLOADER_PROBE_PORT=''
printf 'Completed %s: %s\n' "$([[ "$probe" == 1 ]] && printf probe || printf install)" "$DEVICE_PORT"
