"""Offline app-only flash contracts; every flash operation is a temporary file.

Reuse the data-restoration test's pinned fake parser/flasher boundary. The real
adapter runs unchanged, including input validation, guards, sole app write,
readbacks and runtime finish. No installed flasher, serial port or PIO is used.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import unittest
import zlib

import test_mcfirmware_bench_data_restore as fixture

TOOL = fixture.ROOT / 'tools/hil/mcfirmware_bench_app_flash.sh'
IMAGE_PARSER = r'''
def image_header(image):
    if len(image)<36 or image[0]!=0xe9: raise ExpansionError('bad image header')
    return struct.unpack_from('<H',image,12)[0],1<<(20+(image[3]>>4))
def endf_identity(image):
    trailer=image[-56:]
    if len(trailer)!=56 or trailer[:4]!=b'EndF' or struct.unpack_from('<I',trailer,4)[0]!=len(image)-56:
        raise ExpansionError('bad EndF length')
    if trailer[8:16]!=hashlib.sha256(image[:-56]).digest()[:8]: raise ExpansionError('bad EndF hash')
    version,target=struct.unpack_from('<II',trailer,16)
    hardware=trailer[24:].rstrip(b'\0').decode('ascii')
    if not version or not target or not hardware: raise ExpansionError('bad EndF identity')
    return version,target,hardware
'''


def image(capacity, *, target=0, hardware='MOCK_BOARD', chip=9, mode=2, length=256):
    body = bytearray(length - 56)
    body[0:4] = bytes([0xe9, 1, mode, (capacity.bit_length() - 21) << 4])
    struct.pack_into('<H', body, 12, chip)
    struct.pack_into('<I', body, 32, 0xabcd5432)
    return (bytes(body) + b'EndF' + struct.pack('<I', len(body)) + hashlib.sha256(body).digest()[:8]
            + struct.pack('<II', 0x01120001, target) + hardware.encode().ljust(32, b'\0'))


def ota(slot):
    data = bytearray(b'\xff' * 8192)
    sequence = slot + 1
    struct.pack_into('<I', data, 0, sequence)
    struct.pack_into('<I', data, 28, zlib.crc32(struct.pack('<I', sequence), 0xffffffff) & 0xffffffff)
    return bytes(data)


@unittest.skipUnless(os.name == 'posix' and all(shutil.which(c) for c in
                     ('bash', 'python3', 'awk', 'stat', 'sha256sum', 'realpath', 'timeout')),
                     'POSIX shell tools required; no physical device required')
class AppFlashTests(unittest.TestCase):
    def setUp(self):
        # This creates only private temporary files and PATH command stubs.
        fixture.DataRestoreTests.setUp(self)
        helper = self.flasher.parent / 'tools/meshcore_partition_expansion.py'
        helper.write_text(fixture.PARSER + IMAGE_PARSER)
        source = self.flasher.read_text().replace(fixture.digest(fixture.PARSER.encode()),
                                                  fixture.digest(helper.read_bytes()))
        source = source.replace('Detected flash size:8MB\\n\' "${MOCK_CHIP:-ESP32-S3}"',
                                'Detected flash size:%sMB\\n\' "${MOCK_CHIP:-ESP32-S3}" "${MOCK_MB:-8}"')
        source = source.replace("'nvs-readback'", "'application-readback'")
        source = source.replace("int(sys.argv[2],0)==0x9000 and os.environ.get('MOCK_MUTATE_AFTER_WRITE')",
                                "os.environ.get('MOCK_MUTATE_AFTER_WRITE')")
        self.flasher.write_text(source)
        self.image_path = self.root / 'application.bin'
        self.configure()

    def configure(self, *, layout='xiao', active=0, relocated=False):
        self.capacity = 0x1000000 if layout == 'v4' else 0x800000
        if layout == 'v4':
            self.apps = [(0x10000, 0x640000), (0x650000, 0x640000)]
            data = [('spiffs', 1, 0x82, 0xc90000, 0x360000), ('coredump', 1, 3, 0xff0000, 0x10000)]
        elif layout == 'indicator':
            self.apps = [(0x10000, 0x280000), (0x400000, 0x280000)]
            data = [('spiffs', 1, 0x82, 0x290000, 0x160000), ('coredump', 1, 3, 0x3f0000, 0x10000)]
        else:
            self.apps = [(0x10000, 0x330000), (0x340000, 0x330000)]
            data = [('spiffs', 1, 0x82, 0x670000, 0x190000)]
        self.active = active
        self.ota_address = 0xc000 if relocated else 0xe000
        entries = [('nvs', 1, 2, 0x9000, 0x3000 if relocated else 0x5000),
                   ('otadata', 1, 0, self.ota_address, 0x2000)]
        entries += [('app' + str(i), 0, 0x10 + i, *part) for i, part in enumerate(self.apps)] + data
        self.table = b''.join(struct.pack('<2sBBII16sI', b'\xaa\x50', kind, sub, offset, length,
                                         name.encode(), 0)
                              for name, kind, sub, offset, length in entries).ljust(4096, b'\xff')
        before = bytearray(b'\xff' * self.capacity)
        before[0x8000:0x9000] = self.table
        before[self.ota_address:self.ota_address + 8192] = ota(active)
        self.before = bytes(before)
        self.backup.write_bytes(before)
        self.flash.write_bytes(before)
        self.trace.unlink(missing_ok=True)
        self.manifest.write_text(json.dumps(dict(complete=True, operation='read-only-flash-backup',
            chip='ESP32-S3', device_mac=fixture.MAC, offset=0, length=self.capacity,
            flash_bytes=self.capacity, sha256=fixture.digest(before), artifact=self.backup.name)))
        self.image = image(self.capacity)
        self.image_path.write_bytes(self.image)
        address, size = self.apps[active]
        self.args = ['bash', str(TOOL), '--port', '/dev/ttyACM99', '--usb-path', 'exact-path',
            '--usb-id', 'exact-id', '--device', 'MOCK', '--bench-dir', str(self.bench),
            '--expect-mac', fixture.MAC, '--backup', str(self.backup), '--backup-manifest', str(self.manifest),
            '--backup-sha256', fixture.digest(before), '--expect-partition-sha256', fixture.digest(self.table),
            '--address', hex(address), '--slot-size', hex(size), '--image', str(self.image_path),
            '--image-sha256', fixture.digest(self.image), '--expect-target-id', '0',
            '--expect-hardware-id', 'MOCK_BOARD', '--allow-write', '--flasher', str(self.flasher),
            '--flasher-sha256', fixture.digest(self.flasher.read_bytes())]

    def argument(self, option, value):
        self.args[self.args.index(option) + 1] = str(value)

    def run_tool(self, **env):
        self.trace.unlink(missing_ok=True)
        return fixture.DataRestoreTests.run_tool(self, MOCK_MB=str(self.capacity // 0x100000), **env)

    def refuse(self, *, before_prepare=False, **env):
        result = self.run_tool(**env)
        fixture.DataRestoreTests.assert_no_writes(self, result)
        self.assertEqual(self.flash.read_bytes(), self.before)
        if before_prepare:
            self.assertNotIn('prepare', self.actions)

    def assert_success(self, **env):
        result = self.run_tool(**env)
        self.assertEqual(result.returncode, 0, result.stderr)
        writes = [line.split() for line in self.actions if 'write-flash' in line]
        self.assertEqual([int(line[6]) for line in writes], [self.apps[self.active][0]])
        expected = bytearray(self.before)
        offset = self.apps[self.active][0]
        expected[offset:offset + len(self.image)] = self.image
        self.assertEqual(self.flash.read_bytes(), expected)
        self.assertEqual(self.actions[-1], 'finish')
        self.assertFalse(any('erase' in line for line in self.actions))
        return result

    def test_only_active_app_written_in_both_slots_of_three_real_layout_shapes(self):
        for layout in ('v4', 'xiao', 'indicator'):
            for active in (0, 1):
                with self.subTest(layout=layout, active=active):
                    self.configure(layout=layout, active=active)
                    self.assert_success()
                    self.assertFalse(any('hard-reset' in action for action in self.actions))

    def test_relocated_ota_read_and_uart_final_probe_reset_then_finish(self):
        self.configure(layout='indicator', active=1, relocated=True)
        self.assert_success(MOCK_AFTER='hard-reset')
        self.assertIn('--after hard-reset --baud 115200 read-mac', self.actions[-2])
        reads = [int(line.split()[6], 0) for line in self.actions if 'read-flash' in line]
        self.assertEqual(reads.count(0xc000), 2)
        self.assertNotIn(0xe000, reads)

    def test_live_chip_or_capacity_mismatch_refused_before_write(self):
        self.refuse(MOCK_CHIP='ESP32-C3')
        # Keep the image/backup capacity at8MiB, change only the live probe.
        self.capacity = 0x1000000
        self.refuse()

    def test_changed_live_table_or_active_ota_refused_before_write(self):
        for address, replacement in ((0x8000, b'\x00'), (self.ota_address, ota(1))):
            with self.subTest(address=address):
                changed = bytearray(self.before)
                changed[address:address + len(replacement)] = replacement
                self.flash.write_bytes(changed)
                # The altered live state itself must also remain untouched.
                self.before = bytes(changed)
                self.refuse()
                self.configure()

    def test_wrong_requested_active_slot_or_size_refused_before_prepare(self):
        self.argument('--address', hex(self.apps[1][0]))
        self.refuse(before_prepare=True)
        self.configure()
        self.argument('--slot-size', self.apps[0][1] - 4096)
        self.refuse(before_prepare=True)

    def test_image_hash_backup_hash_and_mac_refused_before_prepare(self):
        for option, wrong in (('--image-sha256', '0' * 64), ('--backup-sha256', '0' * 64),
                              ('--expect-mac', '112233445566'), ('--expect-partition-sha256', '0' * 64)):
            with self.subTest(option=option):
                self.configure()
                self.argument(option, wrong)
                self.refuse(before_prepare=True)

    def test_full_zero_target_requires_exact_optin_and_hardware(self):
        for option, wrong in (('--expect-target-id', '1234'), ('--expect-hardware-id', 'OTHER_BOARD')):
            with self.subTest(option=option):
                self.configure()
                self.argument(option, wrong)
                self.refuse(before_prepare=True)

    def test_endf_body_corruption_and_oversize_image_refused_before_prepare(self):
        for changed in (self.image[:40] + bytes([self.image[40] ^ 1]) + self.image[41:],
                        image(self.capacity, length=self.apps[0][1] + 1)):
            with self.subTest(length=len(changed)):
                self.image_path.write_bytes(changed)
                self.argument('--image-sha256', fixture.digest(changed))
                self.refuse(before_prepare=True)

    def test_image_chip_capacity_dio_and_descriptor_refused_before_prepare(self):
        images = [image(self.capacity, chip=5), image(0x1000000), image(self.capacity, mode=0)]
        bad_descriptor = bytearray(self.image)
        bad_descriptor[32] ^= 1
        images.append(bytes(bad_descriptor))
        for changed in images:
            with self.subTest(header=changed[:4].hex()):
                self.image_path.write_bytes(changed)
                self.argument('--image-sha256', fixture.digest(changed))
                self.refuse(before_prepare=True)

    def test_app_readback_fault_or_changed_table_ota_prevents_runtime_finish(self):
        for env in ({'MOCK_BAD_READBACK': '1'}, {'MOCK_MUTATE_AFTER_WRITE': '0x8000'},
                    {'MOCK_MUTATE_AFTER_WRITE': hex(self.ota_address)}):
            with self.subTest(fault=env):
                self.configure()
                result = self.run_tool(**env)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(sum('write-flash' in line for line in self.actions), 1)
                self.assertNotIn('finish', self.actions)

    def test_nonzero_infrastructure_target_keeps_existing_interface(self):
        self.image = image(self.capacity, target=1234)
        self.image_path.write_bytes(self.image)
        self.argument('--image-sha256', fixture.digest(self.image))
        for option in ('--expect-target-id', '--expect-hardware-id'):
            index = self.args.index(option)
            del self.args[index:index + 2]
        self.assert_success()


if __name__ == '__main__':
    unittest.main()
