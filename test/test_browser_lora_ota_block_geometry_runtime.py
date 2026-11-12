#!/usr/bin/env python3
"""Exercise source-aware full-package geometry in the actual browser UI."""
import os
import unittest

from test_browser_lora_ota_runtime import BROWSER
from test_browser_lora_ota_nrf_runtime import run_harness
from test_browser_lora_ota_native_folder_runtime import native_folder_harness, replace_once


def geometry_harness(source_max=2048, target_max=2048):
    script = native_folder_harness(False)
    script = replace_once(script,
        "expected=new Uint8Array(100);const v=new DataView(expected.buffer),at=44;",
        "expected=new Uint8Array(1024*1024+56);const v=new DataView(expected.buffer),at=1024*1024;")
    # The emulated receiver and expected package independently constrain the
    # manifest ID. Only the explicit capabilities below allow 2 KiB blocks.
    selected = 2048 if source_max == 2048 and target_max == 2048 else 1024
    script = replace_once(script,
        "buildFullMota(expected));mid=hex(pkg.merkleRoot);",
        "buildFullMota(expected,{blockSize:"+str(selected)+"}));mid=hex(pkg.merkleRoot);")
    script = script.replace('maxblk:1024', 'maxblk:'+str(target_max))
    anchor = "else if(text==='ota stats')value='OTA | fetch idle';"
    source = "OTA seeder | maxblk:"+str(source_max) if source_max else 'Unknown command'
    script = replace_once(script, anchor, anchor+"\n        else if(text==='ota status')value='"+source+"';")
    return script


class BrowserFullGeometryRuntime(unittest.TestCase):
    def setUp(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1': self.fail('Chromium required')
            self.skipTest('Chromium unavailable')

    def test_large_full_image_requires_both_reported_capabilities(self):
        for source, target in ((2048,2048),(1024,2048),(2048,1024),(None,2048)):
            with self.subTest(source=source,target=target):
                result=run_harness(geometry_harness(source,target),timeout=90)
                self.assertNotIn('error',result,result)
                self.assertTrue(result['installed'],result)
                self.assertTrue(result['otaData'],result)
                self.assertEqual(sum(c.startswith('ota pull ') for c in result['cmds']),1,result)
                self.assertFalse(any(c=='ota cancel' for c in result['cmds']),result)

    def test_malformed_catalog_target_limit_falls_back_to_1k(self):
        # The destination cached by investigation must retain the strict
        # parser's result, not the permissive display parser's 2048 prefix.
        # The receiver independently verifies the resulting manifest ID.
        script=geometry_harness(2048,'2048.5')
        anchor="el('investigate').click();const event=await done;"
        script=replace_once(script,anchor,
            "const geometryChecks=[];const strictMax=LoraOtaChecks.explicitMaxBlock;"
            "LoraOtaChecks.explicitMaxBlock=function(text){const value=strictMax(text);"
            "geometryChecks.push({text:String(text),value});return value;};"+anchor)
        script=replace_once(script,"await report({bootstrapLog:","await report({geometryChecks,bootstrapLog:")
        result=run_harness(script,timeout=90)
        self.assertNotIn('error',result,result)
        self.assertTrue(result['installed'],result)
        self.assertTrue(result['otaData'],result)
        checks=[item for item in result['geometryChecks'] if 'maxblk:2048.5' in item['text']]
        self.assertEqual(len(checks),1,result)
        self.assertIsNone(checks[0]['value'],result)
        self.assertEqual(sum(c.startswith('ota pull ') for c in result['cmds']),1,result)
        self.assertFalse(any(c=='ota cancel' for c in result['cmds']),result)


if __name__=='__main__': unittest.main()
