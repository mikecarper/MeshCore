#!/usr/bin/env python3
"""Recover an interrupted first automatic step through the actual browser UI."""
import os
import unittest

from test_browser_lora_ota_runtime import BROWSER
from test_browser_lora_ota_routed_runtime import one_replace
from test_browser_lora_ota_nrf_runtime import run_harness
from test_browser_lora_ota_chain_runtime import automatic_harness


def initial_chain_recovery_harness(receiver_state='idle', *, corrupt_step=False):
    """Fake only a first live-read failure and subsequent receiver state."""
    if receiver_state not in ('idle', 'fetching', 'ready'):
        raise ValueError(receiver_state)
    script = automatic_harness(three_steps=True)
    script = one_replace(script,
        '    async remote(cmd){',
        """    async remote(cmd){
      // The chain record is durable before its first live read. Interrupt
      // that read through the serial endpoint, not by replacing the runner.
      if(cmd==='ver'&&!this.initialReadInterrupted){
        const chainKey=Object.keys(localStorage).find(key=>key.startsWith('meshcore-browser-lora-ota-auto-chain:'));
        const record=chainKey&&JSON.parse(localStorage.getItem(chainKey));
        const stepKey=chainKey&&chainKey.replace('-chain:','-step:');
        if(record?.phase==='step starting'&&record.index===0&&localStorage.getItem(stepKey)===null){
          this.initialReadInterrupted=true;cmds.push('remote '+cmd+' [interrupted first preflight]');
          return 'ERR interrupted first chain preflight';
        }
      }""")
    start = script.index('      let interrupted=false;')
    end = script.index('    } catch(e){await report(', start)
    replacement = r'''
      el('update').click();
      await wait(()=>el('workflow-status').dataset.state==='error'&&!el('inspect').disabled);
      const firstError=el('status').textContent;
      const chainKey=Object.keys(localStorage).find(key=>key.startsWith('meshcore-browser-lora-ota-auto-chain:'));
      if(!chainKey)throw Error('First update did not save its chain record');
      const stepKey=chainKey.replace('-chain:','-step:');
      const interruptedChain=JSON.parse(localStorage.getItem(chainKey));
      const interruptedStep=localStorage.getItem(stepKey);
      // Bare installhash is a read-only capability query, not an install.
      const writesBeforeRecovery=cmds.filter(cmd=>/^remote ota (?:pull |install(?: |$)|installhash )/.test(cmd));
      const applicationsBeforeRecovery=actualApplications;
      if(!port.initialReadInterrupted||interruptedChain.index!==0||interruptedChain.phase!=='step starting'||interruptedStep!==null)
        throw Error('Failure did not land in the absent first-step journal gap');
      const originalChain=localStorage.getItem(chainKey);
      if(CORRUPT_STEP)localStorage.setItem(stepKey,'{unreadable step record');
      state=RECEIVER_STATE;if(state!=='idle')mid='deadbeef';
      // Ordinary Prepare must recover the exact cached route even with no
      // selected base or intermediate files. It must not invent a step record.
      el('base').value='';el('inspect').click();
      await wait(()=>!el('update').disabled||el('workflow-status').dataset.state==='error'&&!el('inspect').disabled);
      const recoveryLog=el('status').textContent,recovered=el('update').textContent;
      if(EXPECT_REJECT){
        await report({status:el('status').textContent,details:el('error-details').textContent,
          notice:el('workflow-status').textContent,disabled:el('update').disabled,firstError,
          interruptedChain,interruptedStep,writesBeforeRecovery,applicationsBeforeRecovery,
          originalChain,finalChain:localStorage.getItem(chainKey),finalStep:localStorage.getItem(stepKey),
          cmds,actualApplications,stageIndex,state,folder,prepared,bridgeReads});return;
      }
      if(el('update').disabled)throw Error('Cached first-step recovery was rejected: '+recoveryLog);
      el('update').click();
      await wait(()=>el('workflow-status').dataset.state==='complete'||el('workflow-status').dataset.state==='error');
      await report({status:el('status').textContent,notice:el('workflow-status').textContent,
        summary:el('update-summary').textContent,prepared,recovered,recoveryLog,firstError,
        interruptedChain,interruptedStep,writesBeforeRecovery,applicationsBeforeRecovery,
        finalChain:JSON.parse(localStorage.getItem(chainKey)),finalStep:JSON.parse(localStorage.getItem(stepKey)),
        cmds,stageIndex,folder,actualApplications,totals,bridgeReads});
'''
    replacement = replacement.replace('CORRUPT_STEP', 'true' if corrupt_step else 'false')
    replacement = replacement.replace('RECEIVER_STATE', repr(receiver_state))
    replacement = replacement.replace('EXPECT_REJECT', 'true' if receiver_state != 'idle' or corrupt_step else 'false')
    return script[:start] + replacement + script[end:]


class BrowserLoraOtaInitialChainRecoveryRuntimeTest(unittest.TestCase):
    def setUp(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1':
                self.fail('A Chromium-family browser is required')
            self.skipTest('Chromium-family browser unavailable')

    def assert_interrupted_before_first_transfer(self, result):
        self.assertNotIn('error', result, result)
        self.assertIn('3 automatic update steps', result['prepared'])
        self.assertEqual(result['interruptedChain']['phase'], 'step starting')
        self.assertEqual(result['interruptedChain']['index'], 0)
        self.assertIsNone(result['interruptedStep'])
        self.assertEqual(result['writesBeforeRecovery'], [])
        self.assertEqual(result['applicationsBeforeRecovery'], 0)
        self.assertEqual(result['bridgeReads'], 2)

    def test_cached_three_step_path_recovers_absent_initial_journal_and_completes(self):
        result = run_harness(initial_chain_recovery_harness(), timeout=120)
        self.assert_interrupted_before_first_transfer(result)
        self.assertIn('Recovered automatic update path: 3 verified differential packages.', result['recoveryLog'])
        self.assertEqual(result['recovered'], 'Continue firmware update')
        self.assertIn('Update complete', result['notice'])
        self.assertEqual(result['actualApplications'], 3)
        self.assertEqual(result['stageIndex'], 3)
        self.assertEqual(result['finalChain']['phase'], 'complete')
        self.assertEqual(result['finalStep']['phase'], 'installed')
        self.assertEqual(len([cmd for cmd in result['cmds'] if cmd.startswith('remote ota pull ')]), 3)
        self.assertEqual(len([cmd for cmd in result['cmds'] if cmd.startswith('remote ota installhash ')]), 3)
        self.assertNotIn('remote ota install', result['cmds'])
        self.assertFalse(result['folder'])

    def assert_nonidle_rejected(self, receiver_state):
        result = run_harness(initial_chain_recovery_harness(receiver_state), timeout=120)
        self.assert_interrupted_before_first_transfer(result)
        self.assertTrue(result['disabled'])
        self.assertIn('not idle', result['status'])
        self.assertEqual(result['originalChain'], result['finalChain'])
        self.assertIsNone(result['finalStep'])
        self.assertEqual(result['state'], receiver_state)
        self.assertEqual(result['actualApplications'], 0)
        self.assertFalse(any(cmd.startswith('remote ota pull ') for cmd in result['cmds']))
        self.assertFalse(any(cmd == 'remote ota install' or cmd.startswith('remote ota installhash ')
                             for cmd in result['cmds']))
        self.assertFalse(result['folder'])

    def test_missing_initial_journal_does_not_replace_active_target_download(self):
        self.assert_nonidle_rejected('fetching')

    def test_missing_initial_journal_does_not_install_or_replace_ready_target_download(self):
        self.assert_nonidle_rejected('ready')

    def test_corrupt_initial_journal_is_not_treated_as_an_absent_record(self):
        result = run_harness(initial_chain_recovery_harness(corrupt_step=True), timeout=120)
        self.assert_interrupted_before_first_transfer(result)
        self.assertTrue(result['disabled'])
        self.assertIn('unreadable', result['status'])
        self.assertEqual(result['originalChain'], result['finalChain'])
        self.assertEqual(result['finalStep'], '{unreadable step record')
        self.assertEqual(result['actualApplications'], 0)
        self.assertFalse(any(cmd.startswith('remote ota pull ') for cmd in result['cmds']))
        self.assertFalse(any(cmd == 'remote ota install' or cmd.startswith('remote ota installhash ')
                             for cmd in result['cmds']))


if __name__ == '__main__':
    unittest.main()
