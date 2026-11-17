"""Reproduce the reported GAT 9DF841DF package through pinned OTAFIX.

Set MESHCORE_GAT_REPRO_DIR to an offline fixture directory containing the
three content-addressed inputs listed below. Large release binaries are not
checked into the test tree. This validates decoding, not physical startup.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FILES = {
    '1.18.0.1.bin': 'c308f6e097045692d3b9ab7f4f542b8a01ce6ed21954e905734ab1f84fe4706e',
    'first-bridge.mota': '5cdacdbb1b2b3dc4a6c1105da66fd9470427decb0211b28610394dc70aa749ba',
    'd06a56d213ff15b3bd03b87ebf36b31d842d0226a450804f5162d575786fe2a9.bin':
        'd06a56d213ff15b3bd03b87ebf36b31d842d0226a450804f5162d575786fe2a9',
}


class Gat562HybridRepro(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture_dir = os.environ.get('MESHCORE_GAT_REPRO_DIR')
        if not fixture_dir:
            raise unittest.SkipTest('Set MESHCORE_GAT_REPRO_DIR for exact release reproduction')
        cls.inputs = [Path(fixture_dir) / name for name in FILES]
        for path, digest in zip(cls.inputs, FILES.values()):
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise AssertionError(f'Incorrect exact reproduction input: {path.name}')
        compiler = shutil.which('gcc')
        if not compiler:
            raise AssertionError('The exact reproduction requires a C compiler')
        cls.temp = tempfile.TemporaryDirectory(prefix='gat562-hybrid-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.binary = Path(cls.temp.name) / ('probe.exe' if os.name == 'nt' else 'probe')
        src = ROOT / 'tools/lora_ota/otafix_wasm/otafix-2.4.12/src'
        subprocess.run([
            compiler, '-std=c11', '-O2', '-DOTA_DELTA_HOST_TEST',
            '-DDETOOLS_CONFIG_IN_PLACE_RESUME=0', '-DMOTA_INTERNAL_BOOTLOADER_UPDATE',
            '-DMOTA_RAM_ARENA_SIZE=65536', '-DUSB_DESC_VID=0x239A',
            '-DUSB_DESC_UF2_PID=0x0029', '-DDEVICE_NAME="GAT562_DFU"',
            '-I' + str(src), '-I' + str(src / 'detools'),
            str(ROOT / 'test/fixtures/otafix_gat_hybrid.c'),
            str(src / 'detools/detools.c'), str(src / 'sha256.c'),
            str(src / 'usb/uf2/bootloader_image.c'), '-o', str(cls.binary),
        ], check=True, capture_output=True, text=True)

    def run_case(self, mode):
        result = subprocess.run([str(self.binary), *map(str, self.inputs), mode],
                                check=True, capture_output=True, text=True, timeout=30)
        return json.loads(result.stdout)

    def test_exact_reported_first_bridge(self):
        result = self.run_case('good')
        self.assertEqual(result['status'], 0xb8, result)
        self.assertTrue(result['applied'] and result['matches'] and result['crcOk'], result)
        self.assertTrue(result['boundsOk'], result)
        self.assertEqual(result['flashPrefix'], 176128, result)
        self.assertEqual(result['ramSuffix'], 63933, result)

    def test_lost_retained_ram_rejects_before_application_write(self):
        result = self.run_case('corrupt-ram')
        self.assertFalse(result['applied'], result)
        self.assertTrue(result['baseIntact'], result)
        self.assertEqual(result['writes'], 0, result)
        self.assertEqual(result['bank'], 1, result)

    def test_failed_destructive_flash_write_does_not_mark_image_bootable(self):
        result = self.run_case('write-failure')
        self.assertFalse(result['applied'], result)
        self.assertEqual(result['bank'], 0xff, result)
        self.assertTrue(result['boundsOk'], result)


if __name__ == '__main__':
    unittest.main()
