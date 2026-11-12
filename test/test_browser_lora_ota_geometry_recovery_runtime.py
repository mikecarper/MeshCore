#!/usr/bin/env python3
"""Exercise full-package geometry recovery and replacement in Chrome."""
import os
import unittest

from test_browser_lora_ota_runtime import BROWSER
from test_browser_lora_ota_nrf_runtime import run_harness
from test_browser_lora_ota_block_geometry_runtime import geometry_harness
from test_browser_lora_ota_native_folder_runtime import replace_once


def recovery_harness(mode):
    script = geometry_harness(2048, 2048)
    script = replace_once(script, 'let inbox=[],', "let stagedId='',cancels=0;let inbox=[],")
    old = "(state==='ready'?'ready to install':'fetching')+' id='+mid"
    new = "(state==='failed'?'failed (manifest timeout)':state==='ready'?'ready to install':'fetching')+' id='+stagedId"
    script = replace_once(script, old, new)
    script = replace_once(script,
        "else if(text.startsWith('ota pull ')){state='fetching';reply='OK';}",
        "else if(text==='ota cancel'){cancels++;state='idle';stagedId='';reply='OK';} "
        "else if(text.startsWith('ota pull ')){stagedId=text.split(' ')[2].toLowerCase();state='fetching';reply='OK';}")
    anchor = "el('inspect').click();await wait(()=>!el('inspect').disabled);if(el('transfer').disabled||!el('resume').disabled||!el('install').disabled)throw Error('validated legacy OTA preflight did not enable transfer');"
    setup = r'''
    const previous=await MeshCoreMotaPackage.buildFullMota(expected,{blockSize:1024});
    const previousInfo=await MeshCoreMotaPackage.parseMota(previous);
    const previousId=hex(previousInfo.merkleRoot);
    const previousHash=hex(new Uint8Array(await crypto.subtle.digest('SHA-256',previous)));
    if(previousId===mid)throw Error('test package geometry did not change identity');
    state='failed';stagedId=previousId;
    localStorage.setItem('meshcore-browser-lora-ota',JSON.stringify({source,target:key,id:previousId,packageHash:previousHash,phase:'pull attempted'}));
    const option=el('full-rebuild');
    option.checked=true;
    el('file').dispatchEvent(new Event('change',{bubbles:true}));
    if(option.checked)throw Error('new file selection did not clear the rebuild option');
    if(MODE==='prebuilt'){
      const chosen=new DataTransfer();chosen.items.add(new File([previous],'previous.mota'));
      el('file').files=chosen.files;el('file').dispatchEvent(new Event('change',{bubbles:true}));
    }
    if(MODE!=='preserve'){option.checked=true;option.dispatchEvent(new Event('change',{bubbles:true}));}
    el('inspect').click();await wait(()=>!el('inspect').disabled);
    const selectedAction=el('update').textContent;
    if(el('update').disabled)throw Error('prepare failed: '+el('workflow-status').textContent+' / '+el('status').textContent);
    if(cancels!==0||stagedId!==previousId||cmds.some(c=>c==='ota cancel'||c.startsWith('ota pull ')))
      throw Error('preparation changed the previously staged download');
    if(MODE==='replace'){
      if(selectedAction!=='Replace and install update')throw Error('replacement not explicitly presented: '+selectedAction);
      el('update').click();await wait(()=>el('workflow-status').dataset.state==='complete'||el('workflow-status').dataset.state==='error');
      if(el('workflow-status').dataset.state!=='complete')throw Error(el('workflow-status').textContent);
      if(cancels!==1||stagedId!==mid||!installed)throw Error('explicit replacement did not cancel and install exactly once');
    }else if(selectedAction!=='Restart and install update'){
      throw Error('original package was not selected for restart: '+selectedAction);
    }
    await report({mode:MODE,previousId,selectedId:mid,stagedId,cancels,installed,selectedAction,
      status:el('status').textContent,cmds,fullRebuild:option.checked});return;
    '''.replace('MODE', repr(mode))
    return replace_once(script, anchor, setup)


class BrowserFullGeometryRecoveryRuntime(unittest.TestCase):
    def setUp(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1':
                self.fail('Chromium required')
            self.skipTest('Chromium unavailable')

    def test_old_package_is_kept_until_explicit_replacement(self):
        for mode in ('preserve', 'replace', 'prebuilt'):
            with self.subTest(mode=mode):
                result = run_harness(recovery_harness(mode), timeout=90)
                self.assertNotIn('error', result, result)
                self.assertNotEqual(result['previousId'], result['selectedId'], result)
                if mode == 'replace':
                    self.assertTrue(result['installed'], result)
                    self.assertEqual(result['cancels'], 1, result)
                else:
                    self.assertFalse(result['installed'], result)
                    self.assertEqual(result['cancels'], 0, result)
                    self.assertEqual(result['stagedId'], result['previousId'], result)
                    self.assertEqual(result['selectedAction'], 'Restart and install update', result)


if __name__ == '__main__':
    unittest.main()
