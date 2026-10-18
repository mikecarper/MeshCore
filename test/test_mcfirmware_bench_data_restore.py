"""Offline guarded-flasher contract tests: no device or PlatformIO process.

The reviewed real flasher and parser are pinned runtime inputs, outside this
repository. This fixture supplies a separately pinned, pure parser and inert
flasher functions; serial commands operate only a temporary bytearray file.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools/hil/mcfirmware_bench_data_restore.sh"
MAC = "441bf669c9c0"
CAPACITY = 0x800000
NVS = (0x9000, 0x5000)
SPIFFS = (0x30000, 0x1000)
APP = (0x10000, 256)

PARSER = r'''
import hashlib,json,re,struct,zlib
class ExpansionError(Exception): pass
def safe_json(data): return json.loads(data)
def parse_table(data,capacity):
    if len(data)!=4096: raise ExpansionError('bad table length')
    result=[]
    for offset in range(0,len(data),32):
        row=data[offset:offset+32]
        if row[:2]==b'\xff\xff': break
        if row[:2]!=b'\xaa\x50': raise ExpansionError('bad table entry')
        kind,subtype,address,size,name,flags=struct.unpack_from('<BBII16sI',row,2)
        if address<0x9000 or address%4096 or size%4096 or not size or address+size>capacity:
            raise ExpansionError('bad table geometry')
        result.append(dict(type=kind,subtype=subtype,offset=address,size=size,name=name.rstrip(b'\0').decode(),flags=flags))
    ordered=sorted(result,key=lambda p:p['offset'])
    if any(a['offset']+a['size']>b['offset'] for a,b in zip(ordered,ordered[1:])):
        raise ExpansionError('overlapping table')
    return result
def active_ota_offset(parts,data):
    if len(data)!=8192: raise ExpansionError('bad OTA size')
    apps=sorted((p for p in parts if p['type']==0),key=lambda p:p['subtype'])
    if data==b'\xff'*8192: return apps[0]['offset']
    sequence=struct.unpack_from('<I',data)[0]
    crc=struct.unpack_from('<I',data,28)[0]
    if crc!=(zlib.crc32(struct.pack('<I',sequence),0xffffffff)&0xffffffff):
        raise ExpansionError('bad OTA CRC')
    return apps[(sequence-1)%len(apps)]['offset']
def flash_identity(text):
    chip=9 if 'ESP32-S3' in text else 5
    return int(re.search(r'flash size:(\d+)MB',text)[1])*1048576,chip
'''

FLASHER = r'''
esptool_set_variables() {
 NORESET=no-reset;HARDRESET=hard-reset;READMAC=read-mac;READFLASH=read-flash;WRITEFLASH=write-flash;FLASHID=flash-id
}
save_selected_serial_port() {
 DEVICE_PORT=$1
}
prepare_esp32_flash_session() {
 printf 'prepare\n' >> "$MOCK_ACTIONS"
 ESP32_FLASH_EXPECTED_MAC=441bf669c9c0;ESP32_SESSION_IS_S3=1
}
selected_flash_serial_port() {
 printf '%s\n' "$1"
}
esp32_record_and_verify_probe_output() {
 [[ "$ESP32_FLASH_EXPECTED_MAC" == 441bf669c9c0 ]]
}
esp32_verified_destructive_port() {
 printf '%s\n' "$1"
}
esp32_write_after_mode() {
 printf '%s\n' "${MOCK_AFTER:-no-reset}"
}
finish_esp32_flash_session() {
 printf 'finish\n' >> "$MOCK_ACTIONS"
}
run_esp32_session_esptool() {
 printf '%s\n' "$*" >> "$MOCK_ACTIONS"
 shift
 [[ "$1" == --after && ( "$2" == no-reset || "$2" == hard-reset ) && "$3" == --baud && "$4" == 115200 ]] || return 97
 shift 4
 case "$1" in
  flash-id) printf 'Chip is %s\nMAC:44:1b:f6:69:c9:c0\nDetected flash size:8MB\n' "${MOCK_CHIP:-ESP32-S3}" ;;
  read-mac) printf 'MAC:44:1b:f6:69:c9:c0\n' ;;
  read-flash) python3 - "$MOCK_FLASH" "$2" "$3" "$4" <<'READ'
import os,sys
with open(sys.argv[1],'rb') as src:
 src.seek(int(sys.argv[2],0)); data=src.read(int(sys.argv[3],0))
if os.environ.get('MOCK_BAD_READBACK') and 'nvs-readback' in sys.argv[4]:
 data=bytes([data[0]^1])+data[1:]
open(sys.argv[4],'wb').write(data)
READ
   ;;
  write-flash) python3 - "$MOCK_FLASH" "$2" "$3" <<'WRITE'
import os,sys
with open(sys.argv[1],'r+b') as dst:
 dst.seek(int(sys.argv[2],0));dst.write(open(sys.argv[3],'rb').read())
 if int(sys.argv[2],0)==0x9000 and os.environ.get('MOCK_MUTATE_AFTER_WRITE'):
  at=int(os.environ['MOCK_MUTATE_AFTER_WRITE'],0)
  dst.seek(at);old=dst.read(1);dst.seek(at);dst.write(bytes([old[0]^1]))
WRITE
   ;;
  *) return 98 ;;
 esac
}
'''


def digest(data):
    return hashlib.sha256(data).hexdigest()


@unittest.skipUnless(os.name == "posix" and all(shutil.which(c) for c in
                     ("bash", "python3", "awk", "stat", "sha256sum", "realpath", "timeout")),
                     "POSIX shell tools required; no physical device required")
class DataRestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mcfirmware-data-offline-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        scripts = {"readlink": "#!/bin/sh\nprintf '%s\\n' /dev/null\n",
                   "fuser": "#!/bin/sh\nexit 1\n", "pipx": "#!/bin/sh\nexit 99\n",
                   "udevadm": "#!/bin/sh\nexit 99\n",
                   "sudo": '#!/bin/sh\n[ "$1" = -n ] && [ "$2" = fuser ] || exit 98\nshift\nexec "$@"\n'}
        for name, text in scripts.items():
            p = self.bin / name
            p.write_text(text)
            p.chmod(0o700)
        self.flasher = self.root / "flasher/mcfirmware.sh"
        helper = self.flasher.parent / "tools/meshcore_partition_expansion.py"
        helper.parent.mkdir(parents=True)
        helper.write_text(PARSER)
        self.flasher.write_text('MESHCORE_PARTITION_EXPANSION_TOOL_SHA256="' + digest(helper.read_bytes()) + '"\n'
            + 'MESHCORE_USB_RESET_TOOL_SHA256="' + '0' * 64 + '"\n'
            + 'MESHCORE_ESP32_BOOTLOADER_TOOL_SHA256="' + '0' * 64 + '"\n' + FLASHER
            + ''.join(f'inert_{i}() {{\n :\n}}\n' for i in range(100)))
        table = b"".join(struct.pack('<2sBBII16sI', b'\xaa\x50', kind, subtype, offset, length, name.encode(), 0)
            for name, kind, subtype, offset, length in [
                ('nvs', 1, 2, *NVS), ('otadata', 1, 0, 0xe000, 0x2000),
                ('app0', 0, 0x10, 0x10000, 0x10000), ('app1', 0, 0x11, 0x20000, 0x10000),
                ('spiffs', 1, 0x82, *SPIFFS)])
        self.table = table.ljust(4096, b'\xff')
        backup = bytearray(b'\xff' * CAPACITY)
        backup[0x8000:0x9000] = self.table
        backup[NVS[0]:sum(NVS)] = b'\x11' * NVS[1]
        backup[SPIFFS[0]:sum(SPIFFS)] = b'\x22' * SPIFFS[1]
        self.backup = self.root / "backup.private.bin"
        self.backup.write_bytes(backup)
        self.backup.chmod(0o600)
        self.manifest = self.root / "backup.private.bin.json"
        self.manifest.write_text(json.dumps(dict(complete=True, operation='read-only-flash-backup', chip='ESP32-S3',
            device_mac=MAC, offset=0, length=CAPACITY, flash_bytes=CAPACITY, sha256=digest(backup), artifact=self.backup.name)))
        self.manifest.chmod(0o600)
        self.app = bytes(range(256))
        live = bytearray(backup)
        live[NVS[0]:sum(NVS)] = b'\x33' * NVS[1]
        live[SPIFFS[0]:sum(SPIFFS)] = b'\x44' * SPIFFS[1]
        live[APP[0]:sum(APP)] = self.app
        self.before = bytes(live)
        self.flash = self.root / "live-flash.bin"
        self.flash.write_bytes(live)
        self.trace = self.root / "actions"
        self.bench = self.root / "bench"
        self.bench.mkdir(mode=0o700)
        self.args = ['bash', str(TOOL), '--port', '/dev/ttyACM99', '--usb-path', 'exact-path', '--usb-id', 'exact-id',
            '--device', 'MOCK', '--bench-dir', str(self.bench), '--expect-mac', MAC,
            '--backup', str(self.backup), '--backup-manifest', str(self.manifest), '--backup-sha256', digest(backup),
            '--expect-partition-sha256', digest(self.table), '--app-address', hex(APP[0]),
            '--app-length', str(APP[1]), '--expect-app-sha256', digest(self.app), '--allow-write',
            '--flasher', str(self.flasher), '--flasher-sha256', digest(self.flasher.read_bytes())]

    def run_tool(self, **env):
        result = subprocess.run(self.args, env=dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
            MOCK_FLASH=str(self.flash), MOCK_ACTIONS=str(self.trace), **env), capture_output=True, text=True, timeout=15)
        self.actions = self.trace.read_text().splitlines() if self.trace.exists() else []
        return result

    def assert_no_writes(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse(any('write-flash' in line for line in self.actions), self.actions)
        self.assertNotIn('finish', self.actions)

    def test_only_original_nvs_and_spiffs_written_and_every_other_byte_preserved(self):
        result = self.run_tool()
        self.assertEqual(result.returncode, 0, result.stderr)
        writes = [line.split() for line in self.actions if 'write-flash' in line]
        self.assertEqual([int(line[6]) for line in writes], [NVS[0], SPIFFS[0]])
        after = self.flash.read_bytes()
        expected = bytearray(self.before)
        original = self.backup.read_bytes()
        for offset, length in (NVS, SPIFFS):
            expected[offset:offset + length] = original[offset:offset + length]
        self.assertEqual(after, expected)
        self.assertEqual(self.actions[-1], 'finish')
        state = next(self.bench.glob('.mcfirmware-data-restore.*'))
        report = json.loads((state / 'result.json').read_text())
        self.assertTrue(report['active_application_unchanged'] and report['data_readbacks_verified'])
        self.assertEqual({p.stat().st_mode & 0o777 for p in state.iterdir() if p.is_file()}, {0o600})

    def test_uart_final_probe_reset_then_real_finish(self):
        result = self.run_tool(MOCK_AFTER='hard-reset')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--after hard-reset --baud 115200 read-mac', self.actions[-2])
        self.assertEqual(self.actions[-1], 'finish')

    def test_changed_live_table_refused_before_data_writes(self):
        data = bytearray(self.flash.read_bytes())
        data[0x800c] ^= 1
        self.flash.write_bytes(data)
        self.assert_no_writes(self.run_tool())

    def test_changed_live_ota_refused_before_data_writes(self):
        data = bytearray(self.flash.read_bytes())
        data[0xe000] = 1
        self.flash.write_bytes(data)
        self.assert_no_writes(self.run_tool())

    def test_changed_live_current_app_refused_before_data_writes(self):
        data = bytearray(self.flash.read_bytes())
        data[APP[0]] ^= 1
        self.flash.write_bytes(data)
        self.assert_no_writes(self.run_tool())

    def test_wrong_chip_refused_before_data_writes(self):
        self.assert_no_writes(self.run_tool(MOCK_CHIP='ESP32-C3'))

    def test_wrong_backup_hash_refused_before_prepare(self):
        self.args[self.args.index('--backup-sha256') + 1] = '0' * 64
        self.assert_no_writes(self.run_tool())
        self.assertNotIn('prepare', self.actions)

    def test_wrong_mac_refused_before_data_writes(self):
        self.args[self.args.index('--expect-mac') + 1] = '112233445566'
        self.assert_no_writes(self.run_tool())

    def test_first_readback_fault_prevents_second_write_and_runtime_finish(self):
        result = self.run_tool(MOCK_BAD_READBACK='1')
        self.assertNotEqual(result.returncode, 0)
        writes = [line.split() for line in self.actions if 'write-flash' in line]
        self.assertEqual([int(line[6]) for line in writes], [NVS[0]])
        self.assertNotIn('finish', self.actions)

    def test_postwrite_table_ota_app_mutations_prevent_runtime_finish(self):
        for address in (0x8000, 0xe000, APP[0]):
            with self.subTest(changed_address=address):
                self.flash.write_bytes(self.before)
                self.trace.unlink(missing_ok=True)
                result = self.run_tool(MOCK_MUTATE_AFTER_WRITE=hex(address))
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('finish', self.actions)

    def test_wrong_active_app_address_refused_before_prepare(self):
        self.args[self.args.index('--app-address') + 1] = '0x20000'
        self.assert_no_writes(self.run_tool())
        self.assertNotIn('prepare', self.actions)

    def test_root_owner_or_sudo_error_refused_before_prepare(self):
        (self.bin / 'sudo').write_text('#!/bin/sh\nprintf "owner inspection unavailable" >&2\nexit 1\n')
        self.assert_no_writes(self.run_tool())
        self.assertNotIn('prepare', self.actions)


if __name__ == '__main__':
    unittest.main()
