#!/usr/bin/env bash
# Application-only 8/16 MiB ESP32-S3 bench role switch on a pinned existing OTA layout. No erase-all,
# partition/OTA/NVS/filesystem write, service stop or direct reset bypass.
# Uses the real mcfirmware session/MAC verification and guarded finish.
set -euo pipefail
umask 077

app_die() { printf 'Application flash refused: %s\n' "$*" >&2; exit 1; }
app_usage() {
    printf '%s\n' \
      'Usage: mcfirmware_bench_app_flash.sh --port /dev/ttyACM... --usb-path EXACT_BASENAME' \
      '  --usb-id EXACT_BASENAME --device LABEL --bench-dir PRIVATE_DIRECTORY' \
      '  --expect-mac XX:XX:XX:XX:XX:XX --image /absolute/APPLICATION.bin --image-sha256 HEX' \
      '  --backup /absolute/FULL_FLASH.bin --backup-manifest /absolute/FULL_FLASH.bin.json' \
      '  --backup-sha256 HEX --expect-partition-sha256 HEX' \
      '  --address ACTIVE_SLOT_ADDRESS --slot-size EXACT_ACTIVE_SLOT_SIZE --allow-write' \
      '  [--flasher PATH] [--flasher-sha256 HEX]' \
      '  [--expect-target-id INTEGER --expect-hardware-id EXACT_ENDF_ID]' \
      'The exact pinned existing 8/16MiB table and its active OTA app0/app1 slot are required.' \
      'The existing complete private backup must match this silicon MAC and full SHA256.' \
      'Writes only the selected active slot, verifies its full readback and unchanged table/otadata,' \
      'then uses the actual flasher runtime finish. Application health is a separate check.' \
      'No original NVS/SPIFFS/otadata restoration is performed by this command.'
}
app_flasher=/home/mikec/meshfirmware/mcfirmware.sh
app_flasher_hash=4da05ba37c2cb8b4a7e455a4aa3ee24f6d8020912d71874c38afd099e98e75b2
app_port='' app_usb_path='' app_usb_id='' app_device='' app_bench='' app_mac=''
app_image='' app_image_hash='' app_backup='' app_backup_manifest='' app_backup_hash=''
app_table_hash='' app_address='' app_slot_size='' app_allow=0 app_active=0
app_target_id='' app_hardware_id=''
while (($#)); do
    case "$1" in
        --flasher|--flasher-sha256|--port|--usb-path|--usb-id|--device|--bench-dir|--expect-mac|--image|--image-sha256|--backup|--backup-manifest|--backup-sha256|--expect-partition-sha256|--address|--slot-size|--expect-target-id|--expect-hardware-id)
            (($# >= 2)) || app_die "Missing value for $1"
            case "$1" in
                --flasher) app_flasher=$2 ;; --flasher-sha256) app_flasher_hash=$2 ;;
                --port) app_port=$2 ;; --usb-path) app_usb_path=$2 ;; --usb-id) app_usb_id=$2 ;;
                --device) app_device=$2 ;; --bench-dir) app_bench=$2 ;; --expect-mac) app_mac=$2 ;;
                --image) app_image=$2 ;; --image-sha256) app_image_hash=$2 ;;
                --backup) app_backup=$2 ;; --backup-manifest) app_backup_manifest=$2 ;;
                --backup-sha256) app_backup_hash=$2 ;; --expect-partition-sha256) app_table_hash=$2 ;;
                --address) app_address=$2 ;; --slot-size) app_slot_size=$2 ;;
                --expect-target-id) app_target_id=$2 ;; --expect-hardware-id) app_hardware_id=$2 ;;
            esac
            shift 2 ;;
        --allow-write) app_allow=1; shift ;;
        -h|--help) app_usage; exit 0 ;;
        *) app_die "Unknown option: $1" ;;
    esac
done
((app_allow)) || app_die 'Explicit --allow-write is required.'
[[ "$app_port" =~ ^/dev/tty(ACM|USB)[0-9]+$ ]] || app_die 'Select one local USB serial tty.'
for app_identity in "$app_usb_path" "$app_usb_id"; do
    [[ -n "$app_identity" && "$app_identity" != */* && "$app_identity" != .* ]] \
        || app_die 'Exact USB by-path and by-id basenames are required.'
done
[[ "$app_mac" =~ ^([[:xdigit:]]{12}|([[:xdigit:]]{2}:){5}[[:xdigit:]]{2})$ ]] || app_die 'An explicit silicon MAC is required.'
app_mac=${app_mac//:/}; app_mac=${app_mac,,}
for app_hash in "$app_image_hash" "$app_backup_hash" "$app_table_hash" "$app_flasher_hash"; do
    [[ "$app_hash" =~ ^[[:xdigit:]]{64}$ ]] || app_die 'Every expected SHA256 must be explicit and complete.'
done
app_image_hash=${app_image_hash,,}; app_backup_hash=${app_backup_hash,,}
app_table_hash=${app_table_hash,,}; app_flasher_hash=${app_flasher_hash,,}
app_number() {
    [[ "$1" =~ ^(0[xX][[:xdigit:]]{1,8}|[0-9]{1,10})$ ]] || app_die 'Slot address/size must be explicit decimal or hexadecimal integers.'
    if [[ "$1" == 0[xX]* ]]; then printf '%d\n' "$((16#${1:2}))"; else printf '%d\n' "$((10#$1))"; fi
}
app_address=$(app_number "$app_address")
app_slot_size=$(app_number "$app_slot_size")
if [[ -n "$app_target_id" || -n "$app_hardware_id" ]]; then
    [[ -n "$app_target_id" && "$app_hardware_id" =~ ^[A-Za-z0-9_-]{1,32}$ ]] \
        || app_die 'Explicit EndF target and exact hardware identity must be supplied together.'
    app_target_id=$(app_number "$app_target_id")
    ((app_target_id <= 0xffffffff)) || app_die 'EndF target must fit an unsigned 32-bit integer.'
fi
(( app_address >= 0x10000 && app_slot_size > 0 && app_address + app_slot_size <= 0x1000000 )) \
    || app_die 'Requested application slot is outside the supported flash bounds.'
[[ -n "$app_device" && "$app_device" != *$'\n'* && "$app_bench" == /* && -d "$app_bench" ]] \
    || app_die 'Supply a board label and an existing dedicated absolute private bench directory.'
for app_command in awk bash python3 pipx timeout udevadm fuser sudo realpath sha256sum stat readlink cp chmod; do
    command -v "$app_command" >/dev/null || app_die "Required command unavailable: $app_command"
done
app_bench=$(realpath -e -- "$app_bench")
[[ "$(stat -Lc '%u' -- "$app_bench")" == "$(id -u)" ]] || app_die 'Bench directory must belong to this user.'
app_permissions=$(stat -Lc '%a' -- "$app_bench")
(( (8#$app_permissions & 077) == 0 )) || app_die 'Bench directory must have no group/other permissions.'
for app_input in "$app_flasher" "$app_image" "$app_backup" "$app_backup_manifest"; do
    [[ "$app_input" == /* && -f "$app_input" && -s "$app_input" && ! -L "$app_input" ]] \
        || app_die 'All inputs must be absolute non-symlink regular files.'
done
app_flasher=$(realpath -e -- "$app_flasher")
app_observed=$(sha256sum -- "$app_flasher")
[[ "${app_observed%% *}" == "$app_flasher_hash" ]] || app_die 'The complete flasher differs from the pinned source.'
app_helper="$(dirname -- "$app_flasher")/tools/meshcore_partition_expansion.py"
app_helper_hash=$(sed -n 's/^MESHCORE_PARTITION_EXPANSION_TOOL_SHA256="\([0-9a-f]\{64\}\)"$/\1/p' "$app_flasher")
[[ "$app_helper_hash" =~ ^[0-9a-f]{64}$ && -f "$app_helper" && ! -L "$app_helper" ]] \
    || app_die 'The current flasher must contain its pinned partition/OTA parser.'
app_observed=$(sha256sum -- "$app_helper")
[[ "${app_observed%% *}" == "$app_helper_hash" ]] || app_die 'The partition/OTA parser differs from the flasher pin.'

app_assert_unowned() {
    local output='' status=0
    if output=$(sudo -n fuser "$1" 2>&1); then app_die 'Selected serial port has an owner; no process will be killed.'; else status=$?; fi
    [[ "$status" == 1 && -z "$output" ]] || app_die 'Cannot prove that the selected serial port is unowned.'
}
app_guard_usb() {
    local physical selected initial
    physical=$(readlink -e -- "/dev/serial/by-path/$app_usb_path") || app_die 'Exact physical USB path is absent.'
    selected=$(readlink -e -- "${DEVICE_PORT:-$app_port}") || app_die 'Selected serial endpoint is absent.'
    [[ "$selected" == "$physical" && -c "$physical" ]] || app_die 'Selected port and exact physical path disagree.'
    if [[ -z "${ESP32_FLASH_EXPECTED_MAC:-}" ]]; then
        initial=$(readlink -e -- "/dev/serial/by-id/$app_usb_id") || app_die 'Exact initial USB by-id is absent.'
        [[ "$initial" == "$physical" ]] || app_die 'Exact USB by-id and physical path disagree.'
    fi
    app_assert_unowned "$selected"
}
app_guard_usb
app_state=$(mktemp -d "$app_bench/.mcfirmware-app.XXXXXX")
app_copy="$app_state/application.bin"
cp --reflink=auto -- "$app_image" "$app_copy"
chmod 600 -- "$app_copy"
# This validator uses only pinned, pure image/table/OTA parsing functions from
# the actual flasher helper. It cannot operate a serial port or write firmware.
cat > "$app_state/validate.py" <<'PY'
import hashlib, importlib.util, json, os, pathlib, struct, sys
mode, helper_path, state_dir, *args = sys.argv[1:]
state = pathlib.Path(state_dir)
spec = importlib.util.spec_from_file_location('reviewed_partition_parser', helper_path)
helper = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = helper
spec.loader.exec_module(helper)
def check(value, reason):
    if not value:
        raise ValueError(reason)
def digest(data):
    return hashlib.sha256(data).hexdigest()
def layout(table, capacity):
    parts = helper.parse_table(table, capacity)
    apps = sorted((part for part in parts if part['type'] == 0), key=lambda part: part['subtype'])
    check([part['subtype'] for part in apps] == [0x10, 0x11]
          and all(part['flags'] == 0 for part in apps),
          'only two unencrypted contiguous OTA application identities are supported')
    ota = [part for part in parts if part['type'] == 1 and part['subtype'] == 0]
    check(len(ota) == 1 and ota[0]['size'] == 0x2000 and ota[0]['flags'] == 0,
          'a unique unencrypted 8192-byte OTA metadata partition is required')
    return parts, apps, ota[0]
def selected_slot(parts, apps, otadata, address, size):
    active = helper.active_ota_offset(parts, otadata)
    slot = next((part for part in apps if part['offset'] == active), None)
    check(slot is not None and slot['offset'] == address and slot['size'] == size,
          'requested address/size does not match the CRC-verified active OTA slot')
    return slot
try:
    if mode == 'inputs':
        backup_path, manifest_path, expected_backup, expected_image, expected_table, mac, address, slot_size, expected_target, expected_hardware = args
        address, slot_size = int(address), int(slot_size)
        for path in (backup_path, manifest_path):
            metadata = os.stat(path, follow_symlinks=False)
            check(metadata.st_uid == os.geteuid() and metadata.st_mode & 0o077 == 0,
                  'backup inputs must be private and owned by this user')
        manifest = helper.safe_json(pathlib.Path(manifest_path).read_bytes())
        check(isinstance(manifest, dict), 'backup manifest must be an object')
        capacity = manifest.get('flash_bytes')
        check(type(capacity) is int and capacity in (0x800000, 0x1000000),
              'backup must pin a supported 8/16MiB flash capacity')
        backup = pathlib.Path(backup_path).read_bytes()
        check(len(backup) == capacity and digest(backup) == expected_backup,
              'full backup size/hash mismatch')
        check(manifest.get('complete') is True and manifest.get('operation') == 'read-only-flash-backup'
              and manifest.get('chip') == 'ESP32-S3' and manifest.get('device_mac') == mac
              and manifest.get('offset') == 0 and manifest.get('length') == capacity
              and manifest.get('sha256') == expected_backup
              and manifest.get('artifact') == pathlib.Path(backup_path).name,
              'full backup manifest identity/range/hash mismatch')
        table = backup[0x8000:0x9000]
        check(digest(table) == expected_table, 'backup partition table does not match its explicit pin')
        parts, apps, ota = layout(table, capacity)
        backup_ota = backup[ota['offset']:ota['offset'] + ota['size']]
        slot = selected_slot(parts, apps, backup_ota, address, slot_size)
        image = (state/'application.bin').read_bytes()
        check(0 < len(image) <= slot_size and (len(image) + 0xfff) // 0x1000 * 0x1000 <= slot_size
              and digest(image) == expected_image, 'application length/erase extent/hash mismatch')
        chip, image_capacity = helper.image_header(image)
        check(chip == 9 and image_capacity == capacity and image[2] == 2,
              'application must declare ESP32-S3, the exact physical capacity, and DIO')
        check(struct.unpack_from('<I', image, 32)[0] == 0xabcd5432,
              'image has no application descriptor at the first segment')
        if expected_target == '0':
            # Production Full builds intentionally disable LoRa OTA and stamp
            # target zero. The migration parser requires a nonzero target;
            # this explicit opt-in still checks every EndF integrity field.
            trailer = image[-56:]
            check(len(trailer) == 56 and trailer[:4] == b'EndF'
                  and struct.unpack_from('<I', trailer, 4)[0] == len(image) - 56
                  and trailer[8:16] == hashlib.sha256(image[:-56]).digest()[:8],
                  'application EndF length/body hash invalid')
            version, target = struct.unpack_from('<II', trailer, 16)
            hardware_bytes = expected_hardware.encode('ascii')
            check(version != 0 and target == 0
                  and trailer[24:] == hardware_bytes.ljust(32, b'\0'),
                  'application EndF version/explicit zero target/exact hardware mismatch')
            hardware = expected_hardware
        else:
            version, target, hardware = helper.endf_identity(image)
            check(version and target and hardware, 'application EndF identity invalid')
        if expected_target:
            check(target == int(expected_target) and hardware == expected_hardware,
                  'application EndF differs from the explicit target/hardware pins')
        (state/'inputs.json').write_text(json.dumps(dict(
            image_bytes=len(image), image_sha256=expected_image, backup_sha256=expected_backup,
            partition_sha256=expected_table, firmware_body_hash16=image[-48:-40].hex(),
            target_id=target, hardware_id=hardware, firmware_version=version, flash_bytes=capacity,
            application_address=address, slot_size=slot_size, slot_name=slot['name'],
            otadata_address=ota['offset'], otadata_size=ota['size'],
            backup_otadata_sha256=digest(backup_ota))))
        (state/'backup-table.bin').write_bytes(table)
    elif mode == 'probe':
        inputs = json.loads((state/'inputs.json').read_text())
        capacity, chip = helper.flash_identity((state/'flash-id.log').read_text())
        check(capacity == inputs['flash_bytes'] and chip == 9,
              'live chip/capacity differs from the pinned full backup')
    elif mode == 'live':
        expected_table, = args
        inputs = json.loads((state/'inputs.json').read_text())
        table = (state/'table-before.bin').read_bytes()
        check(len(table) == 0x1000 and digest(table) == expected_table
              and table == (state/'backup-table.bin').read_bytes(), 'live table differs from the exact pinned backup')
        parts, apps, ota = layout(table, inputs['flash_bytes'])
        check(ota['offset'] == inputs['otadata_address'] and ota['size'] == inputs['otadata_size'],
              'live OTA metadata geometry differs from the pinned backup')
        selected_slot(parts, apps, (state/'otadata-before.bin').read_bytes(),
                      inputs['application_address'], inputs['slot_size'])
    elif mode == 'readback':
        expected_image, expected_table = args
        original = (state/'application.bin').read_bytes()
        observed = (state/'application-readback.bin').read_bytes()
        check(observed == original and digest(observed) == expected_image, 'hardware app readback hash mismatch')
        check((state/'table-after.bin').read_bytes() == (state/'table-before.bin').read_bytes()
              and digest((state/'table-after.bin').read_bytes()) == expected_table,
              'partition table changed during app-only write')
        check((state/'otadata-after.bin').read_bytes() == (state/'otadata-before.bin').read_bytes(),
              'OTA metadata changed during app-only write')
    else:
        raise ValueError('unknown validation mode')
except Exception as error:
    # Only fixed validator/parser reasons or exception classes; never raw flash,
    # JSON/file contents, paths or credential-bearing operating-system errors.
    safe_reason = type(error) is ValueError or type(error) is helper.ExpansionError
    print('App validation failed: ' + (str(error) if safe_reason else type(error).__name__), file=sys.stderr)
    sys.exit(1)
PY
python3 "$app_state/validate.py" inputs "$app_helper" "$app_state" "$app_backup" "$app_backup_manifest" \
    "$app_backup_hash" "$app_image_hash" "$app_table_hash" "$app_mac" "$app_address" "$app_slot_size" \
    "$app_target_id" "$app_hardware_id" 2> "$app_state/validation-inputs.log" \
    || { cat "$app_state/validation-inputs.log" >&2; app_die "Offline input validation failed; private evidence: $app_state"; }
read -r app_flash_bytes app_ota_address app_ota_size < <(python3 - "$app_state/inputs.json" <<'PY'
import json, sys
value = json.load(open(sys.argv[1]))
print(value["flash_bytes"], value["otadata_address"], value["otadata_size"])
PY
)
app_bytes=$(stat -Lc '%s' -- "$app_copy")
app_definitions="$app_state/functions.sh"
awk '
    { sub(/\r$/, "") }
    /^[A-Za-z_][A-Za-z0-9_]*\(\)[[:space:]]*\{[[:space:]]*$/ { if (capture) exit 2; capture=1; count++ }
    capture { print }
    capture && /^}[[:space:]]*$/ { capture=0 }
    END { if (capture || count < 100) exit 2 }
' "$app_flasher" > "$app_definitions"
bash -n "$app_definitions"
# shellcheck source=/dev/null
source "$app_definitions"
for app_function in esptool_set_variables save_selected_serial_port prepare_esp32_flash_session \
    selected_flash_serial_port esp32_record_and_verify_probe_output esp32_verified_destructive_port \
    run_esp32_session_esptool esp32_write_after_mode finish_esp32_flash_session; do
    declare -F "$app_function" >/dev/null || app_die "Flasher lacks safety function: $app_function"
done
PWD_SCRIPT=$(dirname -- "$app_flasher")
FIRMWARE_ROOT=$app_state DOWNLOAD_DIR="$app_state/downloads"
mkdir -- "$DOWNLOAD_DIR"
DEVICE_PORT=$app_port DEVICE_PORT_FILE="$app_state/selected-port" DEVICE_PORT_NAME_FILE="$app_state/selected-by-id"
DEVICE=$app_device ARCHITECTURE=esp32
ESPTOOL_PIPX_APP='' ESPTOOL_VERSION_OUTPUT='' ESPTOOL_CMD='pipx run --spec esptool esptool'
ESP32_SAFE_BAUD=115200 ESP32_FLASH_EXPECTED_MAC='' ESP32_SESSION_IS_S3=0 ESP32_NATIVE_ROM_READY=0
ESP32_FLASH_SELECTED_BY_ID='' ESP32_FLASH_EXPECTED_SERIAL='' ESP32_FLASH_EXPECTED_PATH_STEM=''
BOOTLOADER_PROBE_PORT='' BOOTLOADER_PROBE_ACTIVE=0 USB_RESET_FAILED=0
USB_RESET_RECOVERED_PORT='' USB_RESET_EXPECTED_IDENTITY=''
LOCKEDSERVICE=None MCFIRMWARE_NO_SUDO=1 MESH_DISABLE_1200_RECOVERY=1
ESP32_PROBE_TIMEOUT_SECONDS=12 NRF52_DFU_REENUMERATE_TIMEOUT_SECONDS=30 NRF52_DFU_REENUMERATE_POLL_SECONDS=0.25
MESHCORE_USB_RESET_TOOL_SHA256=$(sed -n 's/^MESHCORE_USB_RESET_TOOL_SHA256="\([0-9a-f]\{64\}\)"$/\1/p' "$app_flasher")
MESHCORE_ESP32_BOOTLOADER_TOOL_SHA256=$(sed -n 's/^MESHCORE_ESP32_BOOTLOADER_TOOL_SHA256="\([0-9a-f]\{64\}\)"$/\1/p' "$app_flasher")
[[ "$MESHCORE_USB_RESET_TOOL_SHA256" =~ ^[0-9a-f]{64}$ && "$MESHCORE_ESP32_BOOTLOADER_TOOL_SHA256" =~ ^[0-9a-f]{64}$ ]] \
    || app_die 'Current flasher must pin both bundled USB handoff helpers.'
prepare_serial_port_for_flash() { app_assert_unowned "$1"; }
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
resolve_meshcore_esp32_bootloader_tool() {
    local tool="$PWD_SCRIPT/tools/meshcore_esp32_bootloader.py"
    resolve_meshcore_usb_reset_tool >/dev/null || return 1
    meshcore_esp32_bootloader_tool_hash_matches "$tool" || return 1
    printf '%s\n' "$tool"
}
app_notice() {
    if ((app_active)); then
        printf 'App-only session incomplete. No blind reset/recovery attempted; inspect private state %s.\n' "$app_state" >&2
    fi
}
trap app_notice EXIT
esptool_set_variables > "$app_state/esptool-version.log" 2>&1
[[ "$NORESET" == no-reset && "$READFLASH" == read-flash && "$WRITEFLASH" == write-flash ]] \
    || app_die 'The reviewed native-S3 finish requires esptool5 or newer.'
ESP32_OPERATION_BEFORE=$NORESET
app_guard_usb
app_active=1
prepare_esp32_flash_session "$DEVICE_PORT" "$DEVICE" > "$app_state/prepare.log" 2>&1
[[ "$ESP32_SESSION_IS_S3" == 1 && "$ESP32_FLASH_EXPECTED_MAC" == "$app_mac" ]] \
    || app_die 'Prepared silicon is not the explicitly selected ESP32-S3 MAC.'
DEVICE_PORT=$(selected_flash_serial_port "$DEVICE_PORT")
app_guard_usb
run_esp32_session_esptool "$DEVICE_PORT" --after "$NORESET" --baud 115200 "$FLASHID" > "$app_state/flash-id.log" 2>&1
app_probe=$(cat "$app_state/flash-id.log")
esp32_record_and_verify_probe_output "$app_probe" > "$app_state/mac-check.log" 2>&1
[[ "$ESP32_FLASH_EXPECTED_MAC" == "$app_mac" ]] || app_die 'Live chip MAC mismatch.'
python3 "$app_state/validate.py" probe "$app_helper" "$app_state"
app_read() {
    local offset=$1 length=$2 output=$3
    app_guard_usb
    DEVICE_PORT=$(esp32_verified_destructive_port "$DEVICE_PORT" 'app-only pinned readback')
    app_guard_usb
    run_esp32_session_esptool "$DEVICE_PORT" --after "$NORESET" --baud 115200 \
        "$READFLASH" "$offset" "$length" "$output" > "$output.log" 2>&1
}
app_read 0x8000 0x1000 "$app_state/table-before.bin"
app_read "$app_ota_address" "$app_ota_size" "$app_state/otadata-before.bin"
python3 "$app_state/validate.py" live "$app_helper" "$app_state" "$app_table_hash"
app_guard_usb
DEVICE_PORT=$(esp32_verified_destructive_port "$DEVICE_PORT" 'application-only active OTA slot write')
app_guard_usb
[[ "$ESP32_FLASH_EXPECTED_MAC" == "$app_mac" ]] || app_die 'Immediate pre-write silicon identity mismatch.'
app_observed=$(sha256sum -- "$app_copy")
[[ "${app_observed%% *}" == "$app_image_hash" ]] || app_die 'Private application copy changed before write.'
# The sole flash write in this adapter: never erase-all, another slot, table,
# OTA metadata, identity, NVS or filesystem. Keep native S3 in ROM until readback succeeds.
run_esp32_session_esptool "$DEVICE_PORT" --after "$NORESET" --baud 115200 \
    "$WRITEFLASH" "$app_address" "$app_copy" > "$app_state/write.log" 2>&1
app_read "$app_address" "$app_bytes" "$app_state/application-readback.bin"
app_read 0x8000 0x1000 "$app_state/table-after.bin"
app_read "$app_ota_address" "$app_ota_size" "$app_state/otadata-after.bin"
python3 "$app_state/validate.py" readback "$app_helper" "$app_state" "$app_image_hash" "$app_table_hash"
app_guard_usb
# UART adapters can require the final harmless probe to carry their normal
# after-mode reset; native USB keeps no-reset until the real guarded finish.
DEVICE_PORT=$(esp32_verified_destructive_port "$DEVICE_PORT" 'application-only verified runtime handoff')
app_guard_usb
[[ "$ESP32_FLASH_EXPECTED_MAC" == "$app_mac" ]] || app_die 'Final runtime handoff silicon identity mismatch.'
app_after=$(esp32_write_after_mode "$DEVICE_PORT")
if [[ "$app_after" != "$NORESET" ]]; then
    run_esp32_session_esptool "$DEVICE_PORT" --after "$app_after" --baud 115200 "$READMAC" \
        > "$app_state/runtime-reset.log" 2>&1
fi
finish_esp32_flash_session "$DEVICE_PORT" > "$app_state/finish.log" 2>&1
BOOTLOADER_PROBE_ACTIVE=0 BOOTLOADER_PROBE_PORT=''
app_active=0
python3 - "$app_state" "$app_mac" "$app_flasher_hash" <<'PY'
import hashlib, json, pathlib, sys
state, mac, flasher_hash = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
result = json.loads((state/'inputs.json').read_text())
result.update(schema_version=1, operation='application-only-flash', complete=True,
              device_mac=mac, chip='ESP32-S3',
              application_readback_sha256_verified=True, partition_table_unchanged=True,
              otadata_unchanged=True, other_flash_ranges_written=False,
              live_otadata_sha256=hashlib.sha256((state/'otadata-before.bin').read_bytes()).hexdigest(),
              flasher_sha256=flasher_hash,
              definitions_sha256=hashlib.sha256((state/'functions.sh').read_bytes()).hexdigest(),
              session_finish='real flasher completed; application health requires separate verification')
(state/'result.json').write_text(json.dumps(result, indent=2)+'\n')
print(json.dumps(result, sort_keys=True))
PY
printf 'Private app-only evidence: %s\n' "$app_state"
