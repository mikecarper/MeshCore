"""Verify the bundled ARC install instructions publish SCN3's own identity.

This is a deliberately narrow decoder for this eight-instruction leaf routine,
not a general ARC emulator. Unknown operations fail closed. It executes bytes
from the actual compiled image and checks its recorded real disassembly.
"""
from pathlib import Path
import hashlib
import json
import re
import struct
import unittest

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / 'lab/pram_scanner'
BASE = 0x101000
MARKERS = (0x800FF8, 0x800FFC)


def normalized_bytes(path):
    return path.read_bytes().replace(b'\r\n', b'\n')


def install(blob, incoming=0):
    pc = struct.unpack_from('<I', blob, 8)[0]
    registers = [0xABC00000 + i for i in range(32)]
    registers[0] = incoming
    original = registers.copy()
    memory = {MARKERS[0]: 0xA4821C5A, MARKERS[1]: 0x69CE6D7F}
    stores = []
    for _ in range(16):
        offset = pc - BASE
        if offset < 0 or offset + 2 > len(blob):
            raise AssertionError('Install escaped the compiled PRAM image')
        opcode = struct.unpack_from('<H', blob, offset)[0]
        if opcode in (0x200A, 0x210A):  # mov r0/r1, limm
            extension, high, low = struct.unpack_from('<HHH', blob, offset + 2)
            if extension != 0x0F80:
                raise AssertionError('Unexpected ARC immediate encoding')
            registers[(opcode >> 8) & 1] = (high << 16) | low
            pc += 8
        elif opcode in (0x4204, 0x420C):  # ld_s r2,[r0,0/4]
            address = registers[0] + (4 if opcode == 0x420C else 0)
            if address not in (BASE, BASE + 4):
                raise AssertionError('Install read outside its own header')
            registers[2] = struct.unpack_from('<I', blob, address - BASE)[0]
            pc += 2
        elif opcode in (0xA140, 0xA141):  # st_s r2,[r1,0/4], word width
            address = registers[1] + (4 if opcode == 0xA141 else 0)
            if address not in MARKERS:
                raise AssertionError('Install wrote outside retained identity words')
            memory[address] = registers[2]
            stores.append(address)
            pc += 2
        elif opcode == 0x720C:  # mov_s r0,2
            registers[0] = 2
            pc += 2
        elif opcode == 0x78E0:  # nop_s, used by a negative control
            pc += 2
        elif opcode == 0x7EE0:  # j_s [blink]
            return registers, original, memory, stores
        else:
            raise AssertionError(f'Unexpected ARC install opcode {opcode:04x}')
    raise AssertionError('Install did not return within its bounded leaf sequence')


class PRAMInstallTests(unittest.TestCase):
    def assert_identity(self, blob, incoming=0):
        registers, original, memory, stores = install(blob, incoming)
        self.assertEqual(registers[0], 2)
        self.assertEqual(stores, list(MARKERS))
        self.assertEqual([memory[a] for a in MARKERS], list(struct.unpack_from('<II', blob)))
        # No stack, blink, callee-saved registers, ROM JLI base or AUX operations.
        self.assertEqual(registers[3:], original[3:])

    def test_actual_compiled_install_publishes_own_header_on_cold_and_warm_entry(self):
        blob = (LAB / 'scan-safe.bin').read_bytes()
        self.assertLessEqual(len(blob), 4096)
        self.assertEqual(len(blob) % 4, 0)
        self.assertEqual(struct.unpack_from('<II', blob), (0x600DB002, 7))
        for incoming in (0, 1, 0xFFFFFFFF):
            self.assert_identity(blob, incoming)
        # Copy the custom image's actual header, not a hard-coded vendor version.
        changed = bytearray(blob)
        struct.pack_into('<I', changed, 4, 0x00123407)
        self.assert_identity(changed)

    def test_old_noop_or_missing_second_word_fails_identity_contract(self):
        blob = bytearray((LAB / 'scan-safe.bin').read_bytes())
        entry = struct.unpack_from('<I', blob, 8)[0] - BASE
        old = bytearray(blob)
        struct.pack_into('<HH', old, entry, 0x720C, 0x7EE0)
        with self.assertRaises(AssertionError):
            self.assert_identity(old)
        missing = bytearray(blob)
        position = missing.index(struct.pack('<H', 0xA141), entry)
        struct.pack_into('<H', missing, position, 0x78E0)
        with self.assertRaises(AssertionError):
            self.assert_identity(missing)

    def test_bundled_source_blob_header_and_actual_disassembly_provenance(self):
        blob = (LAB / 'scan-safe.bin').read_bytes()
        manifest = json.loads((LAB / 'scan-build.json').read_text())
        self.assertEqual(hashlib.sha256(blob).hexdigest(), manifest['blob_sha256'])
        self.assertEqual(len(blob), manifest['blob_bytes'])
        self.assertEqual(manifest['bss_bytes'], 0)
        for name, expected in manifest['source_sha256'].items():
            self.assertEqual(hashlib.sha256(normalized_bytes(LAB / name)).hexdigest(), expected, name)
        listing = normalized_bytes(LAB / 'scan-install.lst')
        self.assertEqual(hashlib.sha256(listing).hexdigest(), manifest['install_disassembly_sha256'])
        lines = 0
        for line in listing.decode().splitlines():
            match = re.match(r'^\s*([0-9a-f]+):\s+((?:[0-9a-f]{4}\s+)+)', line)
            if not match:
                continue
            address = int(match[1], 16)
            encoded = b''.join(struct.pack('<H', int(word, 16)) for word in match[2].split())
            self.assertEqual(blob[address - BASE:address - BASE + len(encoded)], encoded)
            lines += 1
        self.assertEqual(lines, 8)
        header = (ROOT / 'src/helpers/radiolib/W12PramScannerBlob.h').read_text()
        words = re.findall(r'0x([0-9a-fA-F]{8})u', header)
        self.assertEqual(b''.join(struct.pack('<I', int(word, 16)) for word in words), blob)


if __name__ == '__main__':
    unittest.main()
