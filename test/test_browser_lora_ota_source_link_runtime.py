#!/usr/bin/env python3
"""Exercise source-update guidance through the actual browser interface."""
import os
import unittest

from test_browser_lora_ota_runtime import BROWSER, HARNESS as REPEATER
from test_browser_lora_ota_companion_runtime import HARNESS as COMPANION
from test_browser_lora_ota_nrf_runtime import run_harness


def companion_harness(fault):
    action = "if(el('source').value!=='companion')throw Error('Companion is not default');el('connect').click();await wait(()=>!el('inspect').disabled||el('status').textContent.includes('Error:'));"
    assert COMPANION.count(action) == 1
    if fault == 'unsupported':
        inject = "LoraOtaCompanion.CompanionTransport.prototype.open=async()=>{const e=Error('Raw packet sending is unsupported');e.sourceUpdateRequired='companion';throw e;};"
    elif fault == 'timeout':
        inject = "LoraOtaCompanion.CompanionTransport.prototype.open=async()=>{throw Error('Companion response timed out');};"
    else:
        inject = ''
    replacement = inject + r'''
    el('connect').click();await wait(()=>!el('inspect').disabled||el('status').textContent.includes('Error:'));
    await wait(()=>!el('connect').disabled||!el('inspect').disabled);
    const help=el('source-update-help'),link=el('source-update-link');
    await report({visible:!help.hidden,href:link.href,text:help.textContent,status:el('status').textContent});return;'''
    return COMPANION.replace(action, replacement, 1)


def companion_transfer_harness(error_code):
    # Send a real, valid OTA advert. The Companion explicitly accepts or
    # rejects CMD65; do not mistake a generic busy/error reply for missing
    # support. The initial connection performs no RF capability probe.
    branch = "}else if(f[0]===65){const rf=f.slice(2);"
    assert COMPANION.count(branch) == 1
    injected = "}else if(f[0]===65){out=Uint8Array.of(1,"+str(error_code)+");queueMicrotask(()=>this.push(wire(out)));return;}else if(f[0]===65){const rf=f.slice(2);"
    action = "el('transfer').click();await wait(()=>!el('install').disabled||el('status').textContent.includes('Error:'));"
    assert COMPANION.count(action) == 1
    replacement = r'''el('transfer').click();await wait(()=>el('status').textContent.includes('Error:'));
      const help=el('source-update-help'),link=el('source-update-link');
      await report({visible:!help.hidden,href:link.href,text:help.textContent,status:el('status').textContent,frames:port.frames});return;'''
    return COMPANION.replace(branch,injected,1).replace(action,replacement,1)


def repeater_harness(fault):
    action = "el('connect').click();await wait(()=>!el('inspect').disabled);"
    assert REPEATER.count(action) == 1
    if fault == 'unknown':
        harness = REPEATER.replace("'ota.peer status':'OTApeer v=1 session=0 busy=0 peers=1'", "'ota.peer status':'Unknown command'", 1)
    elif fault == 'unsupported_capabilities':
        harness = REPEATER.replace("'ota.peer capabilities':'OTApeer known_repeater=1 routed_path=1'", "'ota.peer capabilities':'ERR unsupported'", 1)
    elif fault == 'busy':
        harness = REPEATER.replace("'ota.peer status':'OTApeer v=1 session=0 busy=0 peers=1'", "'ota.peer status':'OTApeer v=1 session=1 busy=0 peers=1'", 1)
    elif fault == 'malformed':
        harness = REPEATER.replace("'ota.peer status':'OTApeer v=1 session=0 busy=0 peers=1'", "'ota.peer status':'unexpected data'", 1)
    else:
        harness = REPEATER
    replacement = r'''el('connect').click();await wait(()=>!el('inspect').disabled||el('status').textContent.includes('Error:'));
      await wait(()=>!el('connect').disabled||!el('inspect').disabled);
      const help=el('source-update-help'),link=el('source-update-link');
      await report({visible:!help.hidden,href:link.href,text:help.textContent,status:el('status').textContent});return;'''
    return harness.replace(action, replacement, 1)


def repeater_preflight_harness(fault):
    harness=REPEATER
    if fault=='source_folder':
        original="if(cmd==='ota folder')reply='folder='+(folder?'on':'off');"
        assert harness.count(original)==1
        harness=harness.replace(original,"if(cmd==='ota folder')reply='Unknown command';",1)
    else:
        original="if(cmd==='ota status') return"
        assert harness.count(original)==1
        harness=harness.replace(original,"if(cmd==='ota status')return 'Unknown OTA command';\n      if(cmd==='unused status') return",1)
    action="el('inspect').click();await wait(()=>!el('transfer').disabled);"
    assert harness.count(action)==1
    replacement=r'''el('inspect').click();await wait(()=>el('status').textContent.includes('Error:'));
      const help=el('source-update-help'),link=el('source-update-link');
      await report({visible:!help.hidden,href:link.href,status:el('status').textContent});return;'''
    return harness.replace(action,replacement,1)


class SourceUpdateLinkBrowserTest(unittest.TestCase):
    def setUp(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1': self.fail('Chromium is required')
            self.skipTest('Chromium unavailable')

    def test_companion_update_link_only_for_confirmed_unsupported_source(self):
        for fault in ('unsupported', 'timeout', 'ok'):
            with self.subTest(fault=fault):
                result=run_harness(companion_harness(fault), timeout=45)
                self.assertNotIn('error',result,result)
                self.assertEqual(result['visible'],fault=='unsupported',result)
                if fault=='unsupported':
                    self.assertIn('mikecarper.github.io/meshfirmware/',result['href'])
                    self.assertIn('role=companionFull',result['href'])
                    self.assertIn('mode=update',result['href'])

    def test_companion_raw_send_error_is_classified_at_transfer(self):
        for error_code in (1, 4, 6):
            with self.subTest(error_code=error_code):
                result=run_harness(companion_transfer_harness(error_code), timeout=90)
                self.assertNotIn('error',result,result)
                self.assertTrue(any(frame[0]==65 and len(frame)>4 for frame in result['frames']),result)
                self.assertEqual(result['visible'],error_code==1,result)
                if error_code==1:
                    self.assertIn('role=companionFull',result['href'])

    def test_guidance_remains_after_disconnect_and_clears_on_source_change(self):
        harness=companion_transfer_harness(1)
        report="await report({visible:!help.hidden,href:link.href,text:help.textContent,status:el('status').textContent,frames:port.frames});return;"
        self.assertEqual(harness.count(report),1)
        steps=r'''const before=!help.hidden;await wait(()=>!el('disconnect').disabled);
          el('disconnect').click();await wait(()=>!el('connect').disabled);
          const afterDisconnect=!help.hidden;
          el('source').value='repeater';el('source').dispatchEvent(new Event('change'));
          await report({before,afterDisconnect,afterChange:!help.hidden});return;'''
        result=run_harness(harness.replace(report,steps,1),timeout=90)
        self.assertNotIn('error',result,result)
        self.assertTrue(result['before'],result)
        self.assertTrue(result['afterDisconnect'],result)
        self.assertFalse(result['afterChange'],result)

    def test_repeater_link_only_for_explicitly_missing_support(self):
        for fault in ('unknown', 'unsupported_capabilities', 'busy', 'malformed', 'ok'):
            with self.subTest(fault=fault):
                result=run_harness(repeater_harness(fault), timeout=45)
                self.assertNotIn('error',result,result)
                self.assertEqual(result['visible'],fault in ('unknown','unsupported_capabilities'),result)
                if result['visible']:
                    self.assertIn('mikecarper.github.io/meshfirmware/',result['href'])
                    self.assertIn('role=repeater',result['href'])
                    self.assertIn('mode=update',result['href'])

    def test_source_folder_support_is_distinguished_from_target_errors(self):
        for fault in ('source_folder','target_status'):
            with self.subTest(fault=fault):
                result=run_harness(repeater_preflight_harness(fault),timeout=45)
                self.assertNotIn('error',result,result)
                self.assertEqual(result['visible'],fault=='source_folder',result)


if __name__ == '__main__': unittest.main()
