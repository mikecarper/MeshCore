#!/usr/bin/env python3
"""Resume an acknowledged cancellation without repeating its radio command."""
import os
import unittest

from test_browser_lora_ota_runtime import BROWSER
from test_browser_lora_ota_routed_runtime import one_replace
from test_browser_lora_ota_nrf_runtime import run_harness
from test_browser_lora_ota_chain_runtime import automatic_harness


def cancel_recovery_harness(after_cancel='idle'):
    if after_cancel not in ('idle', 'old-active', 'foreign-ready'):
        raise ValueError(after_cancel)
    script = automatic_harness(three_steps=True)
    script = one_replace(script,
        "el('inspect').click();await wait(()=>!el('update').disabled||el('workflow-status').dataset.state==='error');",
        "state='fetching';mid='deadbeef';el('inspect').click();"
        "await wait(()=>!el('update').disabled||el('workflow-status').dataset.state==='error');")
    script = one_replace(script, '    async remote(cmd){', r'''    async remote(cmd){
      if(cmd==='ota cancel'){
        cmds.push('remote '+cmd);this.cancelDone=true;
        state=AFTER_CANCEL==='idle'?'idle':AFTER_CANCEL==='old-active'?'fetching':'ready';
        if(AFTER_CANCEL==='foreign-ready')mid='feedface';
        return 'OK';
      }
      if(cmd==='ota status'&&this.cancelDone&&!this.cancelReadFailed){
        cmds.push('remote '+cmd+' [post-cancel unreadable]');
        this.cancelReadFailed=true;this.badStats=true;return 'unreadable transient status';
      }
      if(cmd==='ota stats'&&this.badStats){
        cmds.push('remote '+cmd);this.badStats=false;return 'unreadable transient stats';
      }
'''.replace('AFTER_CANCEL', repr(after_cancel)))
    start = script.index('      let interrupted=false;')
    end = script.index('    } catch(e){await report(', start)
    body = r'''
      el('update').click();
      await wait(()=>el('workflow-status').dataset.state==='error'&&!el('inspect').disabled);
      const firstError=el('status').textContent;
      const chainKey=Object.keys(localStorage).find(key=>key.startsWith('meshcore-browser-lora-ota-auto-chain:'));
      if(!chainKey)throw Error('Cancellation did not retain a chain record');
      const stepKey=chainKey.replace('-chain:','-step:');
      const record=JSON.parse(localStorage.getItem(stepKey));
      const originalStep=localStorage.getItem(stepKey),originalChain=localStorage.getItem(chainKey);
      const writesBeforeRecovery=cmds.filter(cmd=>/^remote ota (?:pull |install(?: |$)|installhash )/.test(cmd));
      if(record.phase!=='replacement cancel attempted'||!port.cancelReadFailed)
        throw Error('Failure did not land between acknowledged cancel and prepared journal');
      el('base').value='';el('inspect').click();
      await wait(()=>!el('update').disabled||el('workflow-status').dataset.state==='error'&&!el('inspect').disabled);
      const recovered=el('update').textContent,recoveryLog=el('status').textContent;
      if(!el('update').disabled){
        el('update').click();
        await wait(()=>el('workflow-status').dataset.state==='complete'||el('workflow-status').dataset.state==='error');
      }
      await report({status:el('status').textContent,notice:el('workflow-status').textContent,
        record,originalStep,finalStep:localStorage.getItem(stepKey),originalChain,finalChain:localStorage.getItem(chainKey),
        prepared,recovered,recoveryLog,firstError,writesBeforeRecovery,state,mid,cmds,stageIndex,actualApplications,folder});
'''
    return script[:start] + body + script[end:]


class BrowserLoraOtaCancelRecoveryRuntimeTest(unittest.TestCase):
    def setUp(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1':
                self.fail('A Chromium-family browser is required')
            self.skipTest('Chromium-family browser unavailable')

    def assert_cancel_gap(self, result):
        self.assertNotIn('error', result, result)
        self.assertIn('3 automatic update steps', result['prepared'])
        self.assertEqual(result['record']['phase'], 'replacement cancel attempted')
        self.assertEqual(result['record']['replaced'], {'id': 'deadbeef', 'state': 'active'})
        self.assertEqual(result['writesBeforeRecovery'], [])
        self.assertEqual(result['cmds'].count('remote ota cancel'), 1)
        self.assertIn('Recovered automatic update path: 3 verified differential packages.', result['recoveryLog'])
        self.assertFalse(result['folder'])

    def test_acknowledged_cancel_then_unreadable_status_recovers_all_three_installs(self):
        result = run_harness(cancel_recovery_harness(), timeout=120)
        self.assert_cancel_gap(result)
        self.assertEqual(result['recovered'], 'Continue firmware update')
        self.assertIn('Update complete', result['notice'])
        self.assertEqual(result['actualApplications'], 3)
        self.assertEqual(result['stageIndex'], 3)
        self.assertEqual(len([cmd for cmd in result['cmds'] if cmd.startswith('remote ota pull ')]), 3)
        self.assertEqual(len([cmd for cmd in result['cmds'] if cmd.startswith('remote ota installhash ')]), 3)
        self.assertEqual(result['state'], 'idle')

    def assert_staged_download_preserved(self, after_cancel, state, mid):
        result = run_harness(cancel_recovery_harness(after_cancel), timeout=120)
        self.assert_cancel_gap(result)
        self.assertIn('not positively idle', result['status'])
        self.assertEqual(result['actualApplications'], 0)
        self.assertEqual(result['stageIndex'], 0)
        self.assertEqual(result['state'], state)
        self.assertEqual(result['mid'], mid)
        self.assertEqual(result['finalStep'], result['originalStep'])
        self.assertEqual(result['finalChain'], result['originalChain'])
        self.assertFalse(any(cmd.startswith('remote ota pull ') for cmd in result['cmds']))
        self.assertFalse(any(cmd == 'remote ota install' or cmd.startswith('remote ota installhash ')
                             for cmd in result['cmds']))

    def test_unconfirmed_old_active_download_is_not_cancelled_again(self):
        self.assert_staged_download_preserved('old-active', 'fetching', 'deadbeef')

    def test_foreign_ready_download_is_neither_cancelled_nor_installed(self):
        self.assert_staged_download_preserved('foreign-ready', 'ready', 'feedface')


if __name__ == '__main__':
    unittest.main()
