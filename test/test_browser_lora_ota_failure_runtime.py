#!/usr/bin/env python3
"""Check target failure details in the actual browser updater UI."""
import os
import unittest

from test_browser_lora_ota_runtime import BROWSER
from test_browser_lora_ota_nrf_runtime import run_harness
from test_browser_lora_ota_workflow_runtime import workflow_harness
from test_browser_lora_ota_companion_runtime import investigation_harness, HARNESS as COMPANION_HARNESS


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

    def test_identity_timeout_reveals_safe_step_command_path_and_can_be_hidden(self):
        script = investigation_harness()
        marker = "window.addEventListener('load',async()=>{try{"
        script = script.replace(marker, marker + "const Base=LoraOtaCompanion.CompanionTransport;LoraOtaCompanion.CompanionTransport=class extends Base{constructor(port,options){super(port,{...options,investigationTimeout:50,remoteTimeout:50});}};", 1)
        mailbox = "value=`> ota.reply req=${corr} state=ready bytes=${b.length} off=${at} data=${hex(part)||'-'}`;"
        self.assertEqual(script.count(mailbox), 1)
        script = script.replace(mailbox, "value=`> ota.reply req=${corr} state=pending bytes=0 off=${at} data=-`;", 1)
        report = "await report({result:el('investigation').textContent,status:el('status').textContent,cmds,frames:port.frames,guard,complete:event.detail.complete});return;"
        self.assertEqual(script.count(report), 1)
        script = script.replace(report, r'''const verbose=el('verbose-errors'), panel=el('verbose-error-panel'), details=el('error-details').textContent;
    const revealed=verbose.checked&&!panel.hidden;
    verbose.checked=false;verbose.dispatchEvent(new Event('change'));const hidden=panel.hidden;
    await report({result:el('investigation').textContent,status:el('status').textContent,notice:el('workflow-status').textContent,details,revealed,hidden,cmds,frames:port.frames,guard,complete:event.detail.complete});return;''', 1)
        result = run_harness(script, timeout=45)
        self.assertNotIn('error', result, result)
        self.assertFalse(result['complete'], result)
        self.assertTrue(result['revealed'], result)
        self.assertTrue(result['hidden'], result)
        self.assertIn('get public.key', result['details'])
        self.assertIn('Verifying target identity', result['details'])
        self.assertIn('0 hops', result['details'])
        self.assertIn('Radio:', result['details'])
        self.assertIn('Attempt: 7 of 7', result['details'])
        self.assertIn('result is unknown', result['notice'])
        self.assertNotIn('Command:', result['notice'])
        self.assertFalse(any(c.startswith('ota pull ') or c.startswith('ota install') for c in result['cmds']))

    def test_prepare_recovers_a_lost_self_reply_without_repeating_pull_or_install(self):
        script = COMPANION_HARNESS
        marker = "window.addEventListener('load',async()=>{try{"
        script = script.replace(marker, marker + "const Base=LoraOtaCompanion.CompanionTransport;LoraOtaCompanion.CompanionTransport=class extends Base{constructor(port,options){super(port,{...options,investigationTimeout:50,remoteTimeout:500});}};", 1)
        mailbox = "value=`> ota.reply req=${corr} state=ready bytes=${b.length} off=${at} data=${hex(part)||'-'}`;"
        self.assertEqual(script.count(mailbox), 1)
        script = script.replace(mailbox, "if(cmds.at(-1)==='ota self'&&cmds.filter(c=>c==='ota self').length===1)value=`> ota.reply req=${corr} state=pending bytes=0 off=${at} data=-`;else " + mailbox, 1)
        result = run_harness(script, timeout=75)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['installed'], result)
        self.assertGreaterEqual(result['cmds'].count('ota self'), 2)
        self.assertEqual(sum(c.startswith('ota pull ') for c in result['cmds']), 1)
        self.assertEqual(sum(c.startswith('ota installhash ') for c in result['cmds']), 1)

    def test_prepare_failure_reports_existing_board_and_reads_only_missing_version(self):
        script = COMPANION_HARNESS
        marker = "window.addEventListener('load',async()=>{try{"
        script = script.replace(marker, marker + "const Base=LoraOtaCompanion.CompanionTransport;LoraOtaCompanion.CompanionTransport=class extends Base{constructor(port,options){super(port,{...options,investigationTimeout:50,remoteTimeout:500});}};", 1)
        mailbox = "value=`> ota.reply req=${corr} state=ready bytes=${b.length} off=${at} data=${hex(part)||'-'}`;"
        script = script.replace(mailbox, "if(cmds.at(-1)==='ota self')value=`> ota.reply req=${corr} state=pending bytes=0 off=${at} data=-`;else " + mailbox, 1)
        action = "if(el('key').value!==key)throw Error('contact selection failed');el('inspect').click();await wait(()=>!el('transfer').disabled||el('status').textContent.includes('Error:'));"
        self.assertEqual(script.count(action), 1)
        script = script.replace(action, r'''if(el('key').value!==key)throw Error('contact selection failed');
    el('inspect').click();await wait(()=>el('error-details').textContent.includes('Target version'));
    await report({details:el('error-details').textContent,notice:el('workflow-status').textContent,cmds,installed});return;''', 1)
        result = run_harness(script, timeout=45)
        self.assertNotIn('error', result, result)
        self.assertFalse(result['installed'], result)
        self.assertEqual(result['cmds'].count('ota self'), 3)
        self.assertEqual(result['cmds'].count('ver'), 1)
        self.assertNotIn('board', result['cmds'], 'reported hardware from ota status must not require another LoRa query')
        self.assertIn('v1.17.1.9', result['details'])
        self.assertIn('hardware ID board', result['details'])
        self.assertIn('Command: ota self', result['details'])
        self.assertIn('Attempt: 3 of 3', result['details'])
        self.assertIn('result is unknown', result['notice'])
        self.assertFalse(any(c.startswith('ota pull ') or c.startswith('ota installhash ') for c in result['cmds']))


if __name__ == '__main__':
    unittest.main()
