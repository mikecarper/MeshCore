#!/usr/bin/env bash
# Restore only original NVS/SPIFFS from an exact private full-flash backup.
# No app/table/OTA/full erase, identity synthesis, services or guessed reset.
set -euo pipefail
umask 077
restore_die() { printf 'Data restoration refused: %s\n' "$*" >&2; exit 1; }
restore_usage() {
    printf '%s\n' \
      'Usage: mcfirmware_bench_data_restore.sh --port /dev/ttyACM... --usb-path EXACT_BASENAME' \
      '  --usb-id EXACT_BASENAME --device LABEL --bench-dir PRIVATE_DIRECTORY' \
      '  --expect-mac XX:XX:XX:XX:XX:XX --backup /absolute/FULL_FLASH.bin' \
      '  --backup-manifest /absolute/FULL_FLASH.bin.json --backup-sha256 HEX' \
      '  --expect-partition-sha256 HEX --app-address ACTIVE_SLOT_ADDRESS' \
      '  --app-length EXACT_CURRENT_APP_BYTES --expect-app-sha256 HEX --allow-write' \
      '  [--flasher PATH] [--flasher-sha256 HEX]' \
      'Requires an exact 8/16MiB ESP32-S3 backup tied to this MAC, unchanged live table/OTA,' \
      'and a separately pinned current active application. Writes only original nvs/spiffs bytes.' \
      'Verifies both readbacks and unchanged table/OTA/current app before the real runtime finish.'
}
restore_flasher=/home/mikec/meshfirmware/mcfirmware.sh
restore_flasher_hash=4da05ba37c2cb8b4a7e455a4aa3ee24f6d8020912d71874c38afd099e98e75b2
restore_port='' restore_usb_path='' restore_usb_id='' restore_device='' restore_bench='' restore_mac=''
restore_backup='' restore_manifest='' restore_backup_hash='' restore_table_hash=''
restore_app_address='' restore_app_length='' restore_app_hash='' restore_allow=0 restore_active=0
while (($#)); do
    case "$1" in
        --flasher|--flasher-sha256|--port|--usb-path|--usb-id|--device|--bench-dir|--expect-mac|--backup|--backup-manifest|--backup-sha256|--expect-partition-sha256|--app-address|--app-length|--expect-app-sha256)
            (($# >= 2)) || restore_die "Missing value for $1"
            case "$1" in
                --flasher) restore_flasher=$2 ;; --flasher-sha256) restore_flasher_hash=$2 ;;
                --port) restore_port=$2 ;; --usb-path) restore_usb_path=$2 ;; --usb-id) restore_usb_id=$2 ;;
                --device) restore_device=$2 ;; --bench-dir) restore_bench=$2 ;; --expect-mac) restore_mac=$2 ;;
                --backup) restore_backup=$2 ;; --backup-manifest) restore_manifest=$2 ;;
                --backup-sha256) restore_backup_hash=$2 ;; --expect-partition-sha256) restore_table_hash=$2 ;;
                --app-address) restore_app_address=$2 ;; --app-length) restore_app_length=$2 ;;
                --expect-app-sha256) restore_app_hash=$2 ;;
            esac
            shift 2 ;;
        --allow-write) restore_allow=1; shift ;;
        -h|--help) restore_usage; exit 0 ;;
        *) restore_die "Unknown option: $1" ;;
    esac
done
((restore_allow)) || restore_die 'Explicit --allow-write is required.'
[[ "$restore_port" =~ ^/dev/tty(ACM|USB)[0-9]+$ ]] || restore_die 'Select one local USB serial tty.'
for restore_id in "$restore_usb_path" "$restore_usb_id"; do
    [[ -n "$restore_id" && "$restore_id" != */* && "$restore_id" != .* ]] \
        || restore_die 'Exact USB by-path and by-id basenames are required.'
done
[[ "$restore_mac" =~ ^([[:xdigit:]]{12}|([[:xdigit:]]{2}:){5}[[:xdigit:]]{2})$ ]] \
    || restore_die 'An explicit silicon MAC is required.'
restore_mac=${restore_mac//:/}; restore_mac=${restore_mac,,}
for restore_hash in "$restore_flasher_hash" "$restore_backup_hash" "$restore_table_hash" "$restore_app_hash"; do
    [[ "$restore_hash" =~ ^[[:xdigit:]]{64}$ ]] || restore_die 'All SHA256 pins must be explicit and complete.'
done
restore_flasher_hash=${restore_flasher_hash,,}; restore_backup_hash=${restore_backup_hash,,}
restore_table_hash=${restore_table_hash,,}; restore_app_hash=${restore_app_hash,,}
restore_number() {
    [[ "$1" =~ ^(0[xX][[:xdigit:]]{1,8}|[0-9]{1,10})$ ]] || restore_die 'Application geometry must be explicit integers.'
    if [[ "$1" == 0[xX]* ]]; then printf '%d\n' "$((16#${1:2}))"; else printf '%d\n' "$((10#$1))"; fi
}
restore_app_address=$(restore_number "$restore_app_address")
restore_app_length=$(restore_number "$restore_app_length")
((restore_app_address >= 0x10000 && restore_app_length > 0 \
  && restore_app_address + restore_app_length <= 0x1000000)) || restore_die 'Application geometry is out of bounds.'
[[ -n "$restore_device" && "$restore_device" != *$'\n'* && "$restore_bench" == /* && -d "$restore_bench" ]] \
    || restore_die 'Supply a device label and existing dedicated absolute private bench directory.'
for restore_command in awk bash python3 pipx timeout udevadm fuser sudo realpath sha256sum stat readlink chmod; do
    command -v "$restore_command" >/dev/null || restore_die "Required command unavailable: $restore_command"
done
restore_bench=$(realpath -e -- "$restore_bench")
[[ "$(stat -Lc '%u' -- "$restore_bench")" == "$(id -u)" ]] || restore_die 'Bench directory must belong to this user.'
restore_permissions=$(stat -Lc '%a' -- "$restore_bench")
(( (8#$restore_permissions & 077) == 0 )) || restore_die 'Bench directory must be private.'
for restore_input in "$restore_flasher" "$restore_backup" "$restore_manifest"; do
    [[ "$restore_input" == /* && -f "$restore_input" && -s "$restore_input" && ! -L "$restore_input" ]] \
        || restore_die 'Inputs must be absolute non-symlink regular files.'
done
restore_flasher=$(realpath -e -- "$restore_flasher")
restore_observed=$(sha256sum -- "$restore_flasher")
[[ "${restore_observed%% *}" == "$restore_flasher_hash" ]] || restore_die 'Flasher source differs from the explicit pin.'
restore_helper="$(dirname -- "$restore_flasher")/tools/meshcore_partition_expansion.py"
restore_helper_hash=$(sed -n 's/^MESHCORE_PARTITION_EXPANSION_TOOL_SHA256="\([0-9a-f]\{64\}\)"$/\1/p' "$restore_flasher")
[[ "$restore_helper_hash" =~ ^[0-9a-f]{64}$ && -f "$restore_helper" && ! -L "$restore_helper" ]] \
    || restore_die 'Current flasher must contain its pinned partition/OTA parser.'
restore_observed=$(sha256sum -- "$restore_helper")
[[ "${restore_observed%% *}" == "$restore_helper_hash" ]] || restore_die 'Partition/OTA parser differs from the flasher pin.'
restore_assert_unowned() {
    local output='' status=0
    if output=$(sudo -n fuser "$1" 2>&1); then restore_die 'Selected serial port has an owner; no process will be killed.'; else status=$?; fi
    [[ "$status" == 1 && -z "$output" ]] || restore_die 'Cannot prove that the selected serial port is unowned.'
}
restore_guard_usb() {
    local physical selected initial
    physical=$(readlink -e -- "/dev/serial/by-path/$restore_usb_path") || restore_die 'Exact physical USB path is absent.'
    selected=$(readlink -e -- "${DEVICE_PORT:-$restore_port}") || restore_die 'Selected USB endpoint is absent.'
    [[ "$selected" == "$physical" && -c "$physical" ]] || restore_die 'Selected port and exact physical path disagree.'
    if [[ -z "${ESP32_FLASH_EXPECTED_MAC:-}" ]]; then
        initial=$(readlink -e -- "/dev/serial/by-id/$restore_usb_id") || restore_die 'Exact initial USB by-id is absent.'
        [[ "$initial" == "$physical" ]] || restore_die 'Initial USB by-id and physical path disagree.'
    fi
    restore_assert_unowned "$selected"
}
# Do not inherit a previous caller's session identity and skip the initial
# by-id comparison. The real flasher establishes its own fresh MAC below.
ESP32_FLASH_EXPECTED_MAC='' DEVICE_PORT=''
restore_guard_usb
restore_state=$(mktemp -d "$restore_bench/.mcfirmware-data-restore.XXXXXX")
# Pure parsing only; the pinned helper receives no command-line main invocation.
cat > "$restore_state/validate.py" <<'PY'
import hashlib, importlib.util, json, os, pathlib, sys
mode, helper_path, state_path, *args = sys.argv[1:]
state = pathlib.Path(state_path)
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
    apps = sorted((p for p in parts if p['type'] == 0), key=lambda p: p['subtype'])
    check([p['subtype'] for p in apps] == [0x10, 0x11] and all(p['flags'] == 0 for p in apps),
          'exactly two unencrypted contiguous OTA application identities are required')
    def one(kind, subtype, name, size=None):
        found = [p for p in parts if p['type'] == kind and p['subtype'] == subtype]
        check(len(found) == 1 and found[0]['name'] == name and found[0]['flags'] == 0
              and (size is None or found[0]['size'] == size),
              'required unique unencrypted data partition geometry is missing or ambiguous')
        return found[0]
    return parts, one(1, 0, 'otadata', 0x2000), [one(1, 2, 'nvs'), one(1, 0x82, 'spiffs')]
def preserved(inputs, suffix):
    table = (state/('table-' + suffix + '.bin')).read_bytes()
    ota = (state/('otadata-' + suffix + '.bin')).read_bytes()
    app = (state/('application-' + suffix + '.bin')).read_bytes()
    check(len(table) == 0x1000 and digest(table) == inputs['partition_sha256']
          and table == (state/'backup-table.bin').read_bytes(), 'live table differs from the pinned original backup')
    parts, ota_part, data_parts = layout(table, inputs['flash_bytes'])
    check(ota_part['offset'] == inputs['otadata_address'] and ota_part['size'] == inputs['otadata_size']
          and ota == (state/'backup-otadata.bin').read_bytes(),
          'live OTA metadata differs from the pinned original backup')
    check(helper.active_ota_offset(parts, ota) == inputs['application_address'],
          'live active application slot changed')
    check(len(app) == inputs['application_bytes'] and digest(app) == inputs['application_sha256'],
          'live active application differs from the explicit current app pin')
try:
    if mode == 'inputs':
        backup_path, manifest_path, expected_backup, expected_table, mac, app_address, app_length, app_hash = args
        for path in (backup_path, manifest_path):
            metadata = os.stat(path, follow_symlinks=False)
            check(metadata.st_uid == os.geteuid() and metadata.st_mode & 0o077 == 0,
                  'backup inputs must be private and owned by this user')
        manifest = helper.safe_json(pathlib.Path(manifest_path).read_bytes())
        check(isinstance(manifest, dict), 'backup manifest must be an object')
        capacity = manifest.get('flash_bytes')
        check(type(capacity) is int and capacity in (0x800000, 0x1000000), 'backup must pin 8/16MiB capacity')
        backup = pathlib.Path(backup_path).read_bytes()
        check(len(backup) == capacity and digest(backup) == expected_backup, 'full backup size/hash mismatch')
        check(manifest.get('complete') is True and manifest.get('operation') == 'read-only-flash-backup'
              and manifest.get('chip') == 'ESP32-S3' and manifest.get('device_mac') == mac
              and manifest.get('offset') == 0 and manifest.get('length') == capacity
              and manifest.get('sha256') == expected_backup
              and manifest.get('artifact') == pathlib.Path(backup_path).name,
              'full backup manifest identity/range/hash mismatch')
        table = backup[0x8000:0x9000]
        check(digest(table) == expected_table, 'backup table differs from its explicit pin')
        parts, ota_part, data_parts = layout(table, capacity)
        ota = backup[ota_part['offset']:ota_part['offset'] + ota_part['size']]
        app_address, app_length = int(app_address), int(app_length)
        active = helper.active_ota_offset(parts, ota)
        app = next((p for p in parts if p['type'] == 0 and p['offset'] == active), None)
        check(app is not None and app_address == active and 0 < app_length <= app['size'],
              'current application geometry does not match the original active slot')
        ranges = []
        for part in data_parts:
            data = backup[part['offset']:part['offset'] + part['size']]
            (state/(part['name'] + '-original.private.bin')).write_bytes(data)
            ranges.append(dict(name=part['name'], offset=part['offset'], length=part['size'], sha256=digest(data)))
        inputs = dict(flash_bytes=capacity, backup_sha256=expected_backup, partition_sha256=expected_table,
                      otadata_address=ota_part['offset'], otadata_size=ota_part['size'],
                      otadata_sha256=digest(ota), application_address=active,
                      application_bytes=app_length, application_sha256=app_hash, restored_ranges=ranges)
        (state/'backup-table.bin').write_bytes(table)
        (state/'backup-otadata.bin').write_bytes(ota)
        (state/'inputs.json').write_text(json.dumps(inputs))
        (state/'ranges.tsv').write_text(''.join(f"{p['name']} {p['offset']} {p['length']} {p['sha256']}\n" for p in ranges))
    elif mode == 'probe':
        inputs = json.loads((state/'inputs.json').read_text())
        capacity, chip = helper.flash_identity((state/'flash-id.log').read_text())
        check(capacity == inputs['flash_bytes'] and chip == 9, 'live chip/capacity differs from the pinned S3 backup')
    elif mode in ('before', 'after'):
        inputs = json.loads((state/'inputs.json').read_text())
        preserved(inputs, mode)
        if mode == 'after':
            for part in inputs['restored_ranges']:
                expected = (state/(part['name'] + '-original.private.bin')).read_bytes()
                observed = (state/(part['name'] + '-readback.private.bin')).read_bytes()
                check(len(observed) == part['length'] and digest(observed) == part['sha256'] and observed == expected,
                      'restored data readback differs from the exact original backup range')
    else:
        raise ValueError('unknown validation mode')
except Exception as error:
    safe = type(error) is ValueError or type(error) is helper.ExpansionError
    print('Data validation failed: ' + (str(error) if safe else type(error).__name__), file=sys.stderr)
    sys.exit(1)
PY
python3 "$restore_state/validate.py" inputs "$restore_helper" "$restore_state" \
    "$restore_backup" "$restore_manifest" "$restore_backup_hash" "$restore_table_hash" \
    "$restore_mac" "$restore_app_address" "$restore_app_length" "$restore_app_hash" \
    2> "$restore_state/validation-inputs.log" \
    || { cat "$restore_state/validation-inputs.log" >&2; restore_die "Offline input validation failed; private evidence: $restore_state"; }
read -r restore_ota_address restore_ota_size < <(python3 - "$restore_state/inputs.json" <<'PY'
import json, sys
v = json.load(open(sys.argv[1]))
print(v['otadata_address'], v['otadata_size'])
PY
)
restore_definitions="$restore_state/functions.sh"
awk '
    { sub(/\r$/, "") }
    /^[A-Za-z_][A-Za-z0-9_]*\(\)[[:space:]]*\{[[:space:]]*$/ { if (capture) exit 2; capture=1; count++ }
    capture { print }
    capture && /^}[[:space:]]*$/ { capture=0 }
    END { if (capture || count < 100) exit 2 }
' "$restore_flasher" > "$restore_definitions"
bash -n "$restore_definitions"
# shellcheck source=/dev/null
source "$restore_definitions"
for restore_function in esptool_set_variables save_selected_serial_port prepare_esp32_flash_session \
    selected_flash_serial_port esp32_record_and_verify_probe_output esp32_verified_destructive_port \
    run_esp32_session_esptool esp32_write_after_mode finish_esp32_flash_session; do
    declare -F "$restore_function" >/dev/null || restore_die "Flasher lacks safety function: $restore_function"
done
PWD_SCRIPT=$(dirname -- "$restore_flasher")
FIRMWARE_ROOT=$restore_state DOWNLOAD_DIR="$restore_state/downloads"
mkdir -- "$DOWNLOAD_DIR"
DEVICE_PORT=$restore_port DEVICE_PORT_FILE="$restore_state/selected-port" DEVICE_PORT_NAME_FILE="$restore_state/selected-by-id"
DEVICE=$restore_device ARCHITECTURE=esp32
ESPTOOL_PIPX_APP='' ESPTOOL_VERSION_OUTPUT='' ESPTOOL_CMD='pipx run --spec esptool esptool'
ESP32_SAFE_BAUD=115200 ESP32_FLASH_EXPECTED_MAC='' ESP32_SESSION_IS_S3=0 ESP32_NATIVE_ROM_READY=0
ESP32_FLASH_SELECTED_BY_ID='' ESP32_FLASH_EXPECTED_SERIAL='' ESP32_FLASH_EXPECTED_PATH_STEM=''
BOOTLOADER_PROBE_PORT='' BOOTLOADER_PROBE_ACTIVE=0 USB_RESET_FAILED=0
USB_RESET_RECOVERED_PORT='' USB_RESET_EXPECTED_IDENTITY=''
LOCKEDSERVICE=None MCFIRMWARE_NO_SUDO=1 MESH_DISABLE_1200_RECOVERY=1
ESP32_PROBE_TIMEOUT_SECONDS=12 NRF52_DFU_REENUMERATE_TIMEOUT_SECONDS=30 NRF52_DFU_REENUMERATE_POLL_SECONDS=0.25
MESHCORE_USB_RESET_TOOL_SHA256=$(sed -n 's/^MESHCORE_USB_RESET_TOOL_SHA256="\([0-9a-f]\{64\}\)"$/\1/p' "$restore_flasher")
MESHCORE_ESP32_BOOTLOADER_TOOL_SHA256=$(sed -n 's/^MESHCORE_ESP32_BOOTLOADER_TOOL_SHA256="\([0-9a-f]\{64\}\)"$/\1/p' "$restore_flasher")
[[ "$MESHCORE_USB_RESET_TOOL_SHA256" =~ ^[0-9a-f]{64}$ && "$MESHCORE_ESP32_BOOTLOADER_TOOL_SHA256" =~ ^[0-9a-f]{64}$ ]] \
    || restore_die 'Current flasher must pin both bundled USB handoff helpers.'
prepare_serial_port_for_flash() { restore_assert_unowned "$1"; }
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
restore_notice() {
    if ((restore_active)); then
        printf 'Data restore session incomplete. No blind reset/recovery attempted; inspect private state %s.\n' "$restore_state" >&2
    fi
}
trap restore_notice EXIT
esptool_set_variables > "$restore_state/esptool-version.log" 2>&1
[[ "$NORESET" == no-reset && "$READFLASH" == read-flash && "$WRITEFLASH" == write-flash ]] \
    || restore_die 'The reviewed native-S3 finish requires esptool5 or newer.'
ESP32_OPERATION_BEFORE=$NORESET
restore_guard_usb
restore_active=1
prepare_esp32_flash_session "$DEVICE_PORT" "$DEVICE" > "$restore_state/prepare.log" 2>&1
[[ "$ESP32_SESSION_IS_S3" == 1 && "$ESP32_FLASH_EXPECTED_MAC" == "$restore_mac" ]] \
    || restore_die 'Prepared silicon is not the explicitly selected ESP32-S3 MAC.'
DEVICE_PORT=$(selected_flash_serial_port "$DEVICE_PORT")
restore_guard_usb
run_esp32_session_esptool "$DEVICE_PORT" --after "$NORESET" --baud 115200 "$FLASHID" > "$restore_state/flash-id.log" 2>&1
restore_probe=$(cat "$restore_state/flash-id.log")
esp32_record_and_verify_probe_output "$restore_probe" > "$restore_state/mac-check.log" 2>&1
[[ "$ESP32_FLASH_EXPECTED_MAC" == "$restore_mac" ]] || restore_die 'Live silicon MAC mismatch.'
python3 "$restore_state/validate.py" probe "$restore_helper" "$restore_state"
restore_read() {
    local address=$1 length=$2 output=$3
    restore_guard_usb
    DEVICE_PORT=$(esp32_verified_destructive_port "$DEVICE_PORT" 'pinned data restoration readback')
    restore_guard_usb
    [[ "$ESP32_FLASH_EXPECTED_MAC" == "$restore_mac" ]] || restore_die 'Immediate pre-read MAC mismatch.'
    run_esp32_session_esptool "$DEVICE_PORT" --after "$NORESET" --baud 115200 \
        "$READFLASH" "$address" "$length" "$output" > "$output.log" 2>&1
}
restore_preserved_snapshot() {
    local suffix=$1
    restore_read 0x8000 0x1000 "$restore_state/table-$suffix.bin"
    restore_read "$restore_ota_address" "$restore_ota_size" "$restore_state/otadata-$suffix.bin"
    restore_read "$restore_app_address" "$restore_app_length" "$restore_state/application-$suffix.bin"
}
restore_preserved_snapshot before
python3 "$restore_state/validate.py" before "$restore_helper" "$restore_state"
# Exactly these two parsed, unencrypted DATA partitions. The app/table/OTA
# addresses never enter WRITEFLASH, and no erase-all command is available here.
while read -r restore_name restore_address restore_length restore_range_hash; do
    [[ "$restore_name" == nvs || "$restore_name" == spiffs ]] || restore_die 'Unexpected restoration range.'
    restore_original="$restore_state/$restore_name-original.private.bin"
    restore_observed=$(sha256sum -- "$restore_original")
    [[ "${restore_observed%% *}" == "$restore_range_hash" \
       && "$(stat -Lc '%s' -- "$restore_original")" == "$restore_length" ]] \
        || restore_die 'Staged original data changed before its write.'
    restore_guard_usb
    DEVICE_PORT=$(esp32_verified_destructive_port "$DEVICE_PORT" 'exact original data-partition restoration')
    restore_guard_usb
    [[ "$ESP32_FLASH_EXPECTED_MAC" == "$restore_mac" ]] || restore_die 'Immediate pre-write MAC mismatch.'
    run_esp32_session_esptool "$DEVICE_PORT" --after "$NORESET" --baud 115200 \
        "$WRITEFLASH" "$restore_address" "$restore_original" > "$restore_state/$restore_name-write.log" 2>&1
    restore_read "$restore_address" "$restore_length" "$restore_state/$restore_name-readback.private.bin"
    restore_observed=$(sha256sum -- "$restore_state/$restore_name-readback.private.bin")
    [[ "${restore_observed%% *}" == "$restore_range_hash" \
       && "$(stat -Lc '%s' -- "$restore_state/$restore_name-readback.private.bin")" == "$restore_length" ]] \
        || restore_die 'Data range readback mismatch; no next range or runtime finish attempted.'
done < "$restore_state/ranges.tsv"
restore_preserved_snapshot after
python3 "$restore_state/validate.py" after "$restore_helper" "$restore_state"
restore_guard_usb
DEVICE_PORT=$(esp32_verified_destructive_port "$DEVICE_PORT" 'verified original-data runtime handoff')
restore_guard_usb
[[ "$ESP32_FLASH_EXPECTED_MAC" == "$restore_mac" ]] || restore_die 'Final runtime handoff MAC mismatch.'
restore_after=$(esp32_write_after_mode "$DEVICE_PORT")
if [[ "$restore_after" != "$NORESET" ]]; then
    run_esp32_session_esptool "$DEVICE_PORT" --after "$restore_after" --baud 115200 "$READMAC" \
        > "$restore_state/runtime-reset.log" 2>&1
fi
finish_esp32_flash_session "$DEVICE_PORT" > "$restore_state/finish.log" 2>&1
BOOTLOADER_PROBE_ACTIVE=0 BOOTLOADER_PROBE_PORT=''
restore_active=0
python3 - "$restore_state" "$restore_mac" "$restore_flasher_hash" <<'PY'
import hashlib, json, pathlib, sys
state, mac, flasher_hash = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
v = json.loads((state/'inputs.json').read_text())
v.update(schema_version=1, operation='original-data-partition-restoration', complete=True,
         device_mac=mac, chip='ESP32-S3', data_readbacks_verified=True,
         partition_table_unchanged=True, otadata_unchanged=True, active_application_unchanged=True,
         other_flash_ranges_written=False, flasher_sha256=flasher_hash,
         definitions_sha256=hashlib.sha256((state/'functions.sh').read_bytes()).hexdigest(),
         session_finish='real flasher completed; application health requires separate verification')
(state/'result.json').write_text(json.dumps(v, indent=2)+'\n')
print(json.dumps(v, sort_keys=True))
PY
printf 'Private data-restoration evidence: %s\n' "$restore_state"
