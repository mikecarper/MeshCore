"""Actual RP2040 UF2/BIN application evidence, without PlatformIO or hardware."""
from pathlib import Path
import json
import os
import shlex
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rp2040_application import FLASH_BASE, FLASH_END, read_application

CAPABILITY = 'companion.picow.combined'
MARKER = b'meshcore.capability.companion.picow.combined.v1'


def uf2(application, padding=255):
    count = (len(application) + 255) // 256
    payload = application + bytes([padding]) * (count * 256 - len(application))
    blocks = []
    for index in range(count):
        block = bytearray(512)
        struct.pack_into('<8I', block, 0, 0x0A324655, 0x9E5D5157, 0x2000,
                         FLASH_BASE + index * 256, 256, index, count, 0xE48BFF56)
        block[32:288] = payload[index * 256:(index + 1) * 256]
        struct.pack_into('<I', block, 508, 0x0AB16F30)
        blocks.append(bytes(block))
    return b''.join(blocks)


class RP2040ApplicationCapabilityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='rp2040-application-')
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name)
        self.bin = self.path / 'firmware.bin'
        self.uf2 = self.path / 'firmware.uf2'
        self.elf = self.path / 'firmware.elf'
        self.output = self.path / 'firmware.capabilities.json'
        self.app = b'program-prefix\0' + MARKER + b'\0' + b'code' * 145
        self.bin.write_bytes(self.app)
        self.uf2.write_bytes(uf2(self.app))
        self.elf.write_bytes(b'ELF metadata without combined capability')

    def checker(self, platform='RP2040_PLATFORM', include_bin=True, include_uf2=True, extra=()):
        command = [sys.executable, '-B', str(ROOT / 'scripts/check_firmware_capabilities.py'),
                   '--image', str(self.elf), '--output', str(self.output), '--target', 'PicoW_companion_radio',
                   '--platform', platform, '--build-profile', 'auto', '--expect-application',
                   CAPABILITY + '=' + MARKER.decode(), *extra]
        if include_bin:
            command.extend(['--firmware-bin', str(self.bin)])
        if include_uf2:
            command.extend(['--uf2-package', str(self.uf2)])
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        return result, json.loads(self.output.read_text())

    def test_actual_uploadable_payload_and_both_supported_padding_conventions(self):
        for padding in (0, 255):
            self.uf2.write_bytes(uf2(self.app, padding))
            self.assertEqual(read_application(self.uf2, self.bin), self.app)
            result, manifest = self.checker()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(manifest['verified'])
            self.assertIn(dict(capability=CAPABILITY, evidence=MARKER.decode(), present=True,
                               source='packaged application'), manifest['verification'])
            self.assertEqual(manifest['ota_update_methods'], [])
            self.assertFalse(manifest['ota_update_verified'])

    def test_elf_metadata_alone_cannot_supply_application_proof(self):
        self.elf.write_bytes(b'ELF metadata\0' + MARKER)
        self.bin.write_bytes(b'program without combined transports')
        self.uf2.write_bytes(uf2(self.bin.read_bytes()))
        result, manifest = self.checker()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(manifest['verified'])

    def test_larger_flash_hardware_is_not_limited_to_the_pico_two_megabyte_size(self):
        application = self.app + b'\0' * (2 * 1024 * 1024 + 17 - len(self.app))
        self.bin.write_bytes(application)
        self.uf2.write_bytes(uf2(application))
        self.assertEqual(read_application(self.uf2, self.bin), application)

    def test_unused_uf2_sector_bytes_are_never_scanned_for_capabilities(self):
        self.bin.write_bytes(b'program without combined transports')
        wire = bytearray(uf2(self.bin.read_bytes()))
        wire[320:320+len(MARKER)] = MARKER
        self.uf2.write_bytes(wire)
        self.elf.write_bytes(MARKER)
        result, manifest = self.checker()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(manifest['verified'])

    def test_uf2_alignment_tail_cannot_supply_application_proof(self):
        self.bin.write_bytes(b'program')
        wire = bytearray(uf2(self.bin.read_bytes()))
        wire[32+len(b'program'):32+len(b'program')+len(MARKER)] = MARKER
        self.uf2.write_bytes(wire)
        result, manifest = self.checker()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(manifest['verified'])
        self.assertIn('padding', result.stderr)

    def test_raw_bin_and_uf2_disagreement_fails_before_marker_admission(self):
        wire = bytearray(uf2(self.app))
        wire[32] ^= 1
        self.uf2.write_bytes(wire)
        result, manifest = self.checker()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(manifest['verified'])
        self.assertIn('does not match', result.stderr)

    def test_invalid_family_headers_layout_and_sector_metadata_are_rejected(self):
        original = uf2(self.app)
        changes = (
            (0, 0), (4, 0), (508, 0), (8, 0), (8, 0x2001), (8, 0x3000),
            (28, 0xADA52840), (16, 252), (20, 1), (24, 0),
            (12, FLASH_BASE-256), (12, FLASH_END),
            (512+12, FLASH_BASE), (512+12, FLASH_BASE+512), (512+20, 0), (512+24, 1),
        )
        for offset, value in changes:
            wire = bytearray(original)
            struct.pack_into('<I', wire, offset, value)
            self.uf2.write_bytes(wire)
            with self.subTest(offset=offset, value=value), self.assertRaises(ValueError):
                read_application(self.uf2, self.bin)
        for wire in (b'', original[:-1], original+b'\0'):
            self.uf2.write_bytes(wire)
            with self.subTest(size=len(wire)), self.assertRaises(ValueError):
                read_application(self.uf2, self.bin)

    def test_empty_oversized_or_unnecessarily_extended_application_is_rejected(self):
        for size in (0, FLASH_END-FLASH_BASE+1):
            with self.bin.open('wb') as stream:
                stream.truncate(size)
            with self.subTest(size=size), self.assertRaises(ValueError):
                read_application(self.uf2, self.bin)
        self.bin.write_bytes(self.app[:256])
        self.uf2.write_bytes(uf2(self.app[:256] + b'\xff'*256))
        with self.assertRaisesRegex(ValueError, 'does not match'):
            read_application(self.uf2, self.bin)

    def test_rp2040_application_proof_requires_both_exact_public_formats(self):
        for include_bin, include_uf2, platform in ((True, False, 'RP2040_PLATFORM'),
                                                  (False, True, 'RP2040_PLATFORM'),
                                                  (True, True, 'ESP32_PLATFORM')):
            result, manifest = self.checker(platform, include_bin, include_uf2)
            with self.subTest(platform=platform, bin=include_bin, uf2=include_uf2):
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertFalse(manifest['verified'])
                self.assertIn('require both', result.stderr)

    def test_combined_transports_never_qualify_as_wireless_self_update(self):
        result, manifest = self.checker(extra=('--require-ota',))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(manifest['ota_update_verified'])
        self.assertEqual(manifest['ota_update_methods'], [])
        self.assertIn('wireless self-update', result.stderr)

    @unittest.skipIf(os.name == 'nt', 'builder is POSIX shell')
    def test_builder_passes_both_real_artifacts_to_the_production_checker(self):
        directory = self.path / 'build/PicoW_companion_radio'
        directory.mkdir(parents=True)
        for source in (self.bin, self.uf2, self.elf):
            (directory / source.name).write_bytes(source.read_bytes())
        output = self.path / 'out'
        output.mkdir()
        program = r'''
set -euo pipefail
source build.sh
pio() { echo 'PlatformIO must not run' >&2; return 99; }
PIO_BUILD_DIR_OVERRIDE=@BUILD@
OUTPUT_DIR=@OUTPUT@
BUILD_PROFILE_FOR_TARGET=auto
BUILD_RECIPE_SHA256=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
BUILD_CAPABILITIES=() BUILD_REDUCTIONS=() BUILD_EXPECTATIONS=()
BUILD_APPLICATION_EXPECTATIONS=('companion.picow.combined=meshcore.capability.companion.picow.combined.v1')
write_build_capability_manifest PicoW_companion_radio RP2040_PLATFORM PicoW_companion_radio fixture
'''
        program = program.replace('@BUILD@', shlex.quote(str(self.path / 'build'))).replace('@OUTPUT@', shlex.quote(str(output)))
        result = subprocess.run(['bash', '-c', program], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        manifest = json.loads((output / 'fixture.capabilities.json').read_text())
        self.assertTrue(manifest['verified'])
        self.assertIn(CAPABILITY, manifest['capabilities'])
        self.assertEqual(manifest['ota_update_methods'], [])


if __name__ == '__main__':
    unittest.main()
