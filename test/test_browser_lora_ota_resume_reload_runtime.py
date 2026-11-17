#!/usr/bin/env python3
"""Exercise interrupted-update resume after a real page reload in Chromium."""
import os
import unittest
from test_browser_lora_ota_companion_runtime import HARNESS
from test_browser_lora_ota_nrf_runtime import run_harness
from test_browser_lora_ota_runtime import BROWSER
from test_browser_lora_ota_routed_runtime import one_replace


def resume_harness(case):
    start=HARNESS.index("    const dt=new DataTransfer();dt.items.add(new File([expected],'firmware.bin'));")
    end=HARNESS.index("  }catch(e){await report({error:String(e),status:el('status').textContent,cmds,frames:port.frames});}});", start)
    flow=r'''
    const actualHash=hex(new Uint8Array(await crypto.subtle.digest('SHA-256',pkg.blob)));
    if(!sessionStorage.getItem('resume-test-reloaded')){
      const savedSource=CASE==='wrong-source'?'cc'.repeat(32):source;
      await LoraOtaResumeStore.save({source:savedSource,target:key,file:new File([expected],'firmware.bin'),packageHash:actualHash});
      localStorage.setItem('meshcore-browser-lora-ota',JSON.stringify({phase:CASE==='prepared-idle'?'prepared':'pull attempted',source:savedSource,target:key,id:mid,packageHash:actualHash}));
      localStorage.setItem('meshcore-browser-lora-ota-preferences-v1',JSON.stringify({source:'companion',connection:'usb',key}));
      sessionStorage.setItem('resume-test-reloaded','1');location.reload();return;
    }
    state=CASE==='prepared-idle'?'idle':'fetching';el('password').value='password';
    await wait(()=>!el('saved-update').hidden);
    const savedVisible=!el('saved-update').hidden;
    el('connect').click();await wait(()=>!el('inspect').disabled||el('status').textContent.includes('Error:'));
    if(el('status').textContent.includes('Error:'))throw Error(el('status').textContent);
    const restoredKey=el('key').value,selected=[...el('contact').querySelectorAll('button')].find(b=>b.dataset.contactKey===key)?.getAttribute('aria-pressed');
    if(CASE==='wrong-target'){
      el('key').value='33'.repeat(32);el('key').dispatchEvent(new Event('input',{bubbles:true}));
    }
    if(CASE==='missing-journal')localStorage.removeItem('meshcore-browser-lora-ota');
    if(CASE==='changed-journal'){
      const record=JSON.parse(localStorage.getItem('meshcore-browser-lora-ota'));record.packageHash='dd'.repeat(32);
      localStorage.setItem('meshcore-browser-lora-ota',JSON.stringify(record));
    }
    const commandsBefore=cmds.length;
    el('saved-update-resume').click();
    if(CASE==='match'||CASE==='prepared-idle'){
      await wait(()=>(CASE==='match'?el('update').textContent.includes('Resume and install'):!el('update').disabled)||el('status').textContent.includes('Error:'));
    }else{
      await wait(()=>el('status').textContent.includes('Error:'));
    }
    await report({savedVisible,restoredKey,selected,status:el('status').textContent,update:el('update').textContent,
      disabled:el('update').disabled,compression:el('compression').value,commandsBefore,cmds,frames:port.frames});return;
'''.replace('CASE',repr(case))
    return HARNESS[:start]+flow+HARNESS[end:]


class BrowserResumeReloadTest(unittest.TestCase):
    def setUp(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER')=='1':self.fail('Chromium is required')
            self.skipTest('Chromium unavailable')

    def test_reloads_saved_target_and_rechecks_exact_package_without_starting_transfer(self):
        result=run_harness(resume_harness('match'),timeout=90)
        self.assertNotIn('error',result,result)
        self.assertTrue(result['savedVisible'])
        self.assertEqual(result['restoredKey'],'bb'*32)
        self.assertEqual(result['selected'],'true')
        self.assertEqual(result['update'],'Resume and install update')
        self.assertFalse(result['disabled'])
        self.assertFalse(any(cmd.startswith('ota pull ') or cmd.startswith('ota installhash ') or cmd=='ota install' for cmd in result['cmds']))

    def test_prepared_record_and_idle_target_can_start_without_automatic_write(self):
        result=run_harness(resume_harness('prepared-idle'),timeout=90)
        self.assertNotIn('error',result,result)
        self.assertTrue(result['savedVisible'])
        self.assertEqual(result['update'],'Update remote firmware')
        self.assertFalse(result['disabled'])
        self.assertFalse(any(cmd.startswith('ota pull ') or cmd.startswith('ota installhash ') or cmd=='ota install' for cmd in result['cmds']))

    def test_legacy_recovery_restores_native_compression_even_with_new_default(self):
        result=run_harness(resume_harness('match'),timeout=90,compression_mode=None)
        self.assertNotIn('error',result,result)
        self.assertEqual(result['compression'],'native')
        self.assertEqual(result['update'],'Resume and install update')

    def test_mismatched_source_or_target_never_sends_remote_commands(self):
        for case in ['wrong-source','wrong-target']:
            with self.subTest(case=case):
                result=run_harness(resume_harness(case),timeout=90)
                self.assertNotIn('error',result,result)
                self.assertIn('original source',result['status'])
                self.assertEqual(len(result['cmds']),result['commandsBefore'])

    def test_missing_or_changed_journal_never_sends_remote_commands(self):
        for case in ['missing-journal','changed-journal']:
            with self.subTest(case=case):
                result=run_harness(resume_harness(case),timeout=90)
                self.assertNotIn('error',result,result)
                self.assertIn('matching interrupted update record is unavailable',result['status'])
                self.assertEqual(len(result['cmds']),result['commandsBefore'])


class BrowserResumeStorageTest(unittest.TestCase):
    def setUp(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER')=='1':self.fail('Chromium is required')
            self.skipTest('Chromium unavailable')

    def test_storage_failure_warns_and_retains_manual_update_path(self):
        harness=one_replace(HARNESS,
            "    const dt=new DataTransfer();dt.items.add(new File([expected],'firmware.bin'));",
            "    LoraOtaResumeStore.save=async()=>{throw Error('Quota exceeded')};\n    const dt=new DataTransfer();dt.items.add(new File([expected],'firmware.bin'));")
        harness=one_replace(harness,
            "await report({status,cmds,installed,otaData,advertSeen,guard,frames:port.frames});",
            "await report({status,cmds,installed,otaData,advertSeen,guard,frames:port.frames,warning:el('resume-storage-warning').textContent,warningHidden:el('resume-storage-warning').hidden});")
        result=run_harness(harness,timeout=100)
        self.assertNotIn('error',result,result)
        self.assertTrue(result['installed'])
        self.assertFalse(result['warningHidden'])
        self.assertIn('Could not save firmware',result['warning'])

    def test_disconnect_during_snapshot_does_not_start_transfer(self):
        harness=HARNESS
        original="    el('transfer').click();await wait(()=>!el('install').disabled||el('status').textContent.includes('Error:'));"
        replacement="""    const save=LoraOtaResumeStore.save.bind(LoraOtaResumeStore);let release,started=false;
    LoraOtaResumeStore.save=async args=>{started=true;await new Promise(r=>release=r);return save(args);};
    el('transfer').click();await wait(()=>started);el('disconnect').click();release();
    await wait(()=>!el('connect').disabled);
    await report({status:el('status').textContent,cmds,frames:port.frames,connectDisabled:el('connect').disabled,transferDisabled:el('transfer').disabled});return;"""
        harness=one_replace(harness,original,replacement)
        result=run_harness(harness,timeout=90)
        self.assertNotIn('error',result,result)
        self.assertFalse(result['connectDisabled'])
        self.assertTrue(result['transferDisabled'])
        self.assertFalse(any(cmd.startswith('ota pull ') or cmd.startswith('ota installhash ') or cmd=='ota install' for cmd in result['cmds']))
        self.assertNotIn(65,[frame[0] for frame in result['frames']])

    def test_verified_install_removes_matching_saved_firmware(self):
        harness=one_replace(HARNESS,
            "    el('install').click();await wait(()=>el('status').textContent.includes('Installed and verified:')||el('status').textContent.includes('Error:'));",
            "    const savedBefore=await LoraOtaResumeStore.peek();if(!savedBefore)throw Error('started transfer did not save firmware');\n    el('install').click();await wait(()=>el('status').textContent.includes('Installed and verified:')||el('status').textContent.includes('Error:'));\n    await wait(()=>el('saved-update').hidden);const savedAfter=await LoraOtaResumeStore.peek();")
        harness=one_replace(harness,
            "await report({status,cmds,installed,otaData,advertSeen,guard,frames:port.frames});",
            "await report({status,cmds,installed,otaData,advertSeen,guard,frames:port.frames,savedAfter});")
        result=run_harness(harness,timeout=100)
        self.assertNotIn('error',result,result)
        self.assertTrue(result['installed'])
        self.assertIsNone(result['savedAfter'])


if __name__=='__main__':unittest.main()
