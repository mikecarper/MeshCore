#!/usr/bin/env python3
"""Test the pinned OTAFIX top-level WASM prototype on generated deltas."""
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

from test_browser_lora_ota_nrf_c_decoder import ROOT, firmware

MODULES = ROOT / 'tools/lora_ota/otafix_wasm/build'
NODE = shutil.which('node')


class OtafixWasmTest(unittest.TestCase):
    def test_adaptive_rak_internal_apply_and_rejection(self):
        modules = [MODULES / ('otafix-' + version + '-rak' + board + '-internal.wasm')
                   for version in ('2.4.3', '2.4.6', '2.4.9', '2.4.11', '2.4.12') for board in ('3401', '4631')]
        if not NODE or any(not path.is_file() for path in modules):
            if os.environ.get('MESHCORE_REQUIRE_OTAFIX_WASM') == '1':
                self.fail('Node and compiled OTAFIX WASM prototypes are required')
            self.skipTest('Build the OTAFIX WASM prototypes and install Node first')
        record = struct.pack('<8sBBHIII', b'mOTALay1', 1, 32, 24, 0x26000, 0xED000, 0xED000)
        body = bytearray((ROOT / 'lab/pram_scanner/scan-safe.bin').read_bytes() * 6)
        base = firmware(bytes(body) + record, 0x01120001)
        for offset in range(73, len(body), 701): body[offset] ^= 0x5a
        target = firmware(bytes(body) + record, 0x01120002)
        script = '''const fs=require('node:fs'),d=require('./docs/_javascript/lora_ota_delta.js');
d.buildInPlaceDelta(fs.readFileSync(process.argv[1]),fs.readFileSync(process.argv[2]),
 {runtimeStageCeiling:0xD4000,runtimeStorage:'internal'})
.then(r=>fs.writeFileSync(process.argv[3],r.blob)).catch(e=>{console.error(e);process.exitCode=1});'''
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); old, new, package = root / 'base.bin', root / 'new.bin', root / 'delta.mota'
            old.write_bytes(base); new.write_bytes(target)
            subprocess.run([NODE, '-e', script, str(old), str(new), str(package)], cwd=ROOT, check=True, timeout=120)
            for module in modules:
                for mode in ('good', 'badbase', 'badstage', 'badpatch'):
                    with self.subTest(module=module.name, mode=mode):
                        output = subprocess.check_output([NODE, str(ROOT / 'test/fixtures/otafix_wasm_probe.js'),
                                                          str(module), str(old), str(package), str(new), mode], timeout=30)
                        result = json.loads(output)
                        if mode == 'good':
                            self.assertEqual(result['result'], 1, result)
                            self.assertEqual(result['status'], 0xB8)
                            self.assertEqual(result['size'], len(target))
                            self.assertTrue(result['matches'])
                        else:
                            self.assertNotEqual(result['result'], 1, result)
                            if mode == 'badbase':
                                self.assertEqual(result['status'], 0xB5)
                                self.assertTrue(result['baseIntact'])


if __name__ == '__main__': unittest.main()
