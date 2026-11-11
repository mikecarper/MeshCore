#!/usr/bin/env python3
"""Check target failure details in the actual browser updater UI."""
import os
import unittest

from test_browser_lora_ota_runtime import BROWSER
from test_browser_lora_ota_nrf_runtime import run_harness
from test_browser_lora_ota_workflow_runtime import workflow_harness


class BrowserLoraOtaFailureRuntime(unittest.TestCase):
    def setUp(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1':
                self.fail('Chromium required')
            self.skipTest('Chromium unavailable')

    def test_target_reason_and_progress_are_shown_for_status_and_legacy_stats(self):
        original = ("if(cmd==='ota status') return 'OTA hw=board | target:12345678 | maxblk:1024 | '+"
                    "(state==='idle'?'no download':'download: '+"
                    "(state==='failed'?'failed':state==='ready'?'ready to install':'fetching')+' id='+mid);")
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                script = workflow_harness(transfer_failure=True)
                self.assertEqual(script.count(original), 1)
                failure = "'legacy status'" if legacy else "'download: failed (manifest timeout) 2/63 id='+mid"
                replacement = ("if(cmd==='ota status') return 'OTA hw=board | target:12345678 | maxblk:1024 | '+"
                               "(state==='idle'?'no download':state==='failed'?" + failure + ":"
                               "'download: '+(state==='ready'?'ready to install':'fetching')+' id='+mid);\n"
                               "      if(cmd==='ota stats')return 'OTA | fetch failed:manifest timeout 2/63 id='+mid;")
                script = script.replace(original, replacement)
                result = run_harness(script, timeout=40)
                self.assertNotIn('error', result, result)
                self.assertIn('manifest timeout', result['notice'])
                self.assertIn('2/63', result['notice'])
                self.assertIn('Target OTA status:', result['status'])
                if legacy:
                    self.assertIn('Target OTA stats: OTA | fetch failed:manifest timeout 2/63', result['status'])
                self.assertFalse(result['installed'])
                self.assertEqual(sum(c.startswith('remote ota pull ') for c in result['cmds']), 1)
                self.assertFalse(any(c == 'remote ota install' or c.startswith('remote ota installhash ')
                                     for c in result['cmds']))


if __name__ == '__main__':
    unittest.main()
