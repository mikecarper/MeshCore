#!/usr/bin/env python3
"""Exercise a Nordic release ZIP through the actual browser updater UI."""
import base64
import hashlib
import os
import re
import unittest
import zipfile

from test_browser_lora_ota_delta_runtime import delta_harness
from test_browser_lora_ota_nrf_runtime import run_harness
from test_browser_lora_ota_routed_runtime import one_replace
from test_browser_lora_ota_runtime import BROWSER
from test_browser_lora_ota_zip_runtime import archive, image


class BrowserLoraOtaZipUiRuntimeTest(unittest.TestCase):
    def test_full_update_from_nordic_dfu_zip(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1':
                self.fail('A Chromium-family browser is required')
            self.skipTest('Chromium-family browser unavailable')
        zip_data = base64.b64encode(archive(zipfile.ZIP_DEFLATED)).decode('ascii')
        target_hash = hashlib.sha256(image()[:-56]).hexdigest()[:16]
        harness = delta_harness()
        old = '''const base=image.slice();base[0]=42;
      const baseDigest=new Uint8Array(await crypto.subtle.digest('SHA-256',base.slice(0,at))).slice(0,8);
      base.set(baseDigest,at+8);baseHash=hex(baseDigest);new DataView(base.buffer).setUint32(at+16,0x01110109,true);
      const dt=new DataTransfer();dt.items.add(new File([image],'firmware.bin'));el('file').files=dt.files;
      const bt=new DataTransfer();bt.items.add(new File([base],'installed.bin'));el('base').files=bt.files;'''
        new = """newHash='%s';baseHash='1122334455667788';
      const zip=Uint8Array.from(atob('%s'), c=>c.charCodeAt(0));
      const dt=new DataTransfer();dt.items.add(new File([zip],'release.zip'));el('file').files=dt.files;""" % (target_hash, zip_data)
        harness = one_replace(harness, old, new)
        result = run_harness(harness)
        self.assertNotIn('error', result, result)
        self.assertNotIn('Error:', result['status'])
        self.assertIn('Transfer complete.', result['status'])
        self.assertIn('Installed and verified: board', result['status'])
        self.assertEqual(result['journal']['phase'], 'installed')
        self.assertEqual(result['journal']['bodyHash'], target_hash)
        self.assertEqual(result['codec'], 0)
        self.assertEqual(result['workerCount'], 0)
        self.assertEqual(len(result['served']), result['total'])
        package = bytes(result['served'])
        block_log = package[27]
        payload_size = int.from_bytes(package[23:27], 'little')
        blocks = (payload_size + (1 << block_log) - 1) >> block_log
        start = 205 + blocks * 4
        self.assertEqual(package[start:start + payload_size], image())
        self.assertEqual(sum(c.startswith('remote ota pull ') for c in result['cmds']), 1)
        self.assertEqual(sum(bool(re.fullmatch(r'remote ota installhash [0-9a-f]{64}', c)) for c in result['cmds']), 1)
        self.assertNotIn('remote ota install', result['cmds'])
        self.assertFalse(result['folder'])


if __name__ == '__main__':
    unittest.main()
