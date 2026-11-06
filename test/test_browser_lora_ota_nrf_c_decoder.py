#!/usr/bin/env python3
"""Cross-check two browser nRF deltas using MeshCore's compiled C decoder."""
import hashlib
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def firmware(body, version):
    return (body + b'EndF' + struct.pack('<I', len(body)) + hashlib.sha256(body).digest()[:8]
            + struct.pack('<II', version, 0x12345678) + b'test_board'.ljust(32, b'\0'))


class BrowserNrfCDecoderTest(unittest.TestCase):
    def test_two_successive_in_place_patches_on_flash_model(self):
        node, cc = shutil.which('node'), shutil.which('gcc')
        if not node or not cc:
            if os.environ.get('MESHCORE_REQUIRE_DETOOLS') == '1':
                self.fail('Node and a C compiler are required')
            self.skipTest('Node or a C compiler is unavailable')
        code = ROOT / 'src/helpers/ota/detools'
        fixture = (ROOT / 'lab/pram_scanner/scan-safe.bin').read_bytes() * 6
        script = '''const fs=require('node:fs'),d=require('./docs/_javascript/lora_ota_delta.js');
d.buildInPlaceDelta(fs.readFileSync(process.argv[1]),fs.readFileSync(process.argv[2]),
 {runtimeStageCeiling:0xD4000,runtimeStorage:'internal'})
.then(r=>{fs.writeFileSync(process.argv[3],r.blob);fs.writeFileSync(process.argv[4],String(r.memorySize));})
.catch(e=>{console.error(e);process.exitCode=1});'''
        record = struct.pack('<8sBBHIII', b'mOTALay1', 1, 2, 24, 0x26000, 0xD4000, 0xD4000)
        body = bytearray(fixture)
        images = [firmware(bytes(body) + record, 0x01120001)]
        for version in (0x01120002, 0x01120003):
            for offset in range(73, len(body), max(113, len(body) // 83)):
                body[offset] ^= version & 0xff
            images.append(firmware(bytes(body) + record, version))
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            exe = work / ('apply.exe' if os.name == 'nt' else 'apply')
            subprocess.run([cc, '-std=c99', '-O2', '-I' + str(code),
                            str(ROOT / 'test/fixtures/browser_ota_detools_apply.c'),
                            str(code / 'detools.c'), '-o', str(exe)], check=True, timeout=60)
            paths = []
            for index, value in enumerate(images):
                path = work / f'image{index}.bin'
                path.write_bytes(value)
                paths.append(path)
            for index in range(2):
                package, geometry, patch = work / 'update.mota', work / 'memory.txt', work / 'patch.bin'
                subprocess.run([node, '-e', script, str(paths[index]), str(paths[index + 1]),
                                str(package), str(geometry)], cwd=ROOT, check=True, timeout=120)
                data = package.read_bytes()
                self.assertEqual(data[64], 2)
                self.assertEqual(data[97:105], hashlib.sha256(images[index][:-56]).digest()[:8])
                block_size = 1 << data[27]
                payload_size = int.from_bytes(data[23:27], 'little')
                payload_offset = 205 + ((payload_size + block_size - 1) // block_size) * 4
                patch.write_bytes(data[payload_offset:payload_offset + payload_size])
                capacity = int(geometry.read_text())
                self.assertLessEqual(capacity + ((len(data) + 4095) // 4096) * 4096, 0xD4000 - 0x26000)
                for fill in ('0', '255'):
                    with self.subTest(step=index + 1, flash_fill=fill):
                        subprocess.run([str(exe), str(paths[index]), str(patch), str(paths[index + 1]),
                                        str(capacity), fill], check=True, timeout=60)
                # A valid patch for a different base must never reconstruct the target.
                wrong = paths[(index + 2) % 3]
                with self.subTest(step=index + 1, wrong_base=True):
                    result = subprocess.run([str(exe), str(wrong), str(patch), str(paths[index + 1]),
                                             str(capacity), '255'], capture_output=True, timeout=60)
                    self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
