#!/usr/bin/env python3
"""Exercise the real browser worker and session before a simulated OTA pull."""
import os
import unittest
from test_browser_lora_ota_companion_runtime import HARNESS
from test_browser_lora_ota_nrf_runtime import run_harness
from test_browser_lora_ota_routed_runtime import one_replace
from test_browser_lora_ota_runtime import BROWSER


class ZopfliTransferTest(unittest.TestCase):
    def test_zopfli_is_default_and_fast_choice_survives_reload(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1': self.fail('Chromium required')
            self.skipTest('Chromium unavailable')
        before="window.addEventListener('load',async()=>{try{"
        harness=one_replace(HARNESS,before,before+"""
    if(!sessionStorage.getItem('compression-reloaded')){
      if(el('compression').value!=='zopfli')throw Error('Zopfli is not the default');
      el('compression').value='native';el('compression').dispatchEvent(new Event('change'));
      sessionStorage.setItem('compression-reloaded','1');location.reload();return;
    }
    await report({mode:el('compression').value,summary:el('compression-summary').textContent});return;
""")
        result=run_harness(harness,timeout=40,compression_mode=None)
        self.assertNotIn('error',result,result)
        self.assertEqual(result['mode'],'native')
        self.assertIn('fast browser compression',result['summary'])

    def test_zopfli_prepares_before_pull_and_finishes_update(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1': self.fail('Chromium required')
            self.skipTest('Chromium unavailable')
        harness=one_replace(HARNESS,
            "if(el('key').value!==key)throw Error('contact selection failed');el('inspect').click();",
            """if(el('key').value!==key)throw Error('contact selection failed');
    el('compression').value='zopfli';el('compression').dispatchEvent(new Event('change'));
    const encode=window.LoraOtaZopfli.compress;let compressionCalls=0;
    window.LoraOtaZopfli.compress=async(...args)=>{if(cmds.some(c=>c.startsWith('ota pull ')))throw Error('compressed after pull');compressionCalls++;return encode(...args);};
    el('inspect').click();""")
        harness=one_replace(harness,"await report({status,cmds,installed,otaData,advertSeen,guard,frames:port.frames});",
            "await report({status,cmds,installed,otaData,advertSeen,guard,frames:port.frames,compressionCalls});")
        result=run_harness(harness,timeout=120)
        self.assertNotIn('error',result,result)
        self.assertGreater(result['compressionCalls'],0,result)
        self.assertTrue(result['installed'],result)
        self.assertTrue(any(c.startswith('ota pull ') for c in result['cmds']),result)


if __name__=='__main__': unittest.main()
