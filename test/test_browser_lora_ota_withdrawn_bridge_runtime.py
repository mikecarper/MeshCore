"""A reloaded browser must reject withdrawn bridge caches before radio recovery."""
import unittest
from test_browser_lora_ota_auto_resume_reload_runtime import auto_reload_harness
from test_browser_lora_ota_manual_resume_runtime import manual_harness
from test_browser_lora_ota_chain_runtime import automatic_harness, chain_harness
from test_browser_lora_ota_routed_runtime import one_replace
from test_browser_lora_ota_nrf_runtime import run_harness

WITHDRAWN = 'd06a56d213ff15b3bd03b87ebf36b31d842d0226a450804f5162d575786fe2a9'


class WithdrawnBridgeRecoveryRuntime(unittest.TestCase):
    def test_ready_in_memory_paths_show_a_visible_error_without_starting_transfer(self):
        for automatic in (True, False):
            with self.subTest(automatic=automatic):
                harness = automatic_harness() if automatic else chain_harness()
                hook = 'LoraOtaAutoChain' if automatic else 'LoraOtaCandidates'
                marker = "      const put=(name,stage)=>"
                harness = one_replace(harness, marker, '''
      let readyPlan=null;
      const originalPrepare=''' + hook + '''.prepare;
      ''' + hook + '''.prepare=async(...args)=>{readyPlan=await originalPrepare(...args);return readyPlan;};
''' + marker)
                click = "      el('update').click();el('update').click();" if automatic else "      el('chain-run').click();await wait(()=>el('status').textContent.includes('Update sequence complete;')||el('status').textContent.includes('Error:'));"
                replacement = '''
      const destination=readyPlan.steps[0].to;
      readyPlan.nodes.find(node=>node.id===destination).imageHash=''' + repr(WITHDRAWN) + ''';
      const before=cmds.length;
      el(''' + repr('update' if automatic else 'chain-run') + ''').click();
      await wait(()=>el('status').textContent.includes('Error:'));
      await report({status:el('status').textContent,cmds,before,actualApplications,folder});return;
'''
                harness = one_replace(harness, click, replacement)
                result = run_harness(harness, timeout=240)
                self.assertNotIn('error', result, result)
                self.assertIn('withdrawn', result['status'])
                self.assertEqual(result['actualApplications'], 0)
                self.assertFalse(result['folder'])
                self.assert_no_channel_write(result['cmds'][result['before']:])

    def test_automatic_saved_plan_is_preserved_and_blocked_before_channel_recovery(self):
        harness = auto_reload_harness('withdrawn')
        harness = one_replace(harness, '      const before=cmds.length;', '''
      const cached=await LoraOtaAutoChain.cache('get',saved.autoKey);
      const destination=cached.prepared.steps[0].to;
      cached.prepared.nodes.find(node=>node.id===destination).imageHash=''' + repr(WITHDRAWN) + ''';
      await LoraOtaAutoChain.cache('put',saved.autoKey,cached);
      const before=cmds.length;''')
        result = run_harness(harness, timeout=240)
        self.assertNotIn('error', result, result)
        self.assertIn('withdrawn', result['firstStatus'])
        self.assertEqual(result['remaining'], result['saved'])
        self.assertEqual(result['actualApplications'], 0)
        self.assert_no_channel_write(result['cmds'][result['before']:])

    def test_manual_saved_plan_is_preserved_and_blocked_before_channel_recovery(self):
        harness = manual_harness('withdrawn')
        harness = one_replace(harness, '      const beforeResume=cmds.length;', '''
      const blocked=await LoraOtaAutoChain.cache('get',stored.planKey);
      const destination=blocked.prepared.steps[0].to;
      blocked.prepared.nodes.find(node=>node.id===destination).imageHash=''' + repr(WITHDRAWN) + ''';
      await LoraOtaAutoChain.cache('put',stored.planKey,blocked);
      const beforeResume=cmds.length;''')
        result = run_harness(harness, timeout=240)
        self.assertNotIn('error', result, result)
        self.assertIn('withdrawn', result['status'])
        self.assertEqual(result['after'], result['stored'])
        self.assertIsNotNone(result['afterPlan'])
        self.assertEqual(result['actualApplications'], 0)
        self.assert_no_channel_write(result['cmds'][result['beforeResume']:])

    def assert_no_channel_write(self, commands):
        for command in commands:
            self.assertNotRegex(command, r'(?:tempradio |normalradio|set radio|set tempradio|ota pull |ota install)')


if __name__ == '__main__':
    unittest.main()
