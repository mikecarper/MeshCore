#!/usr/bin/env python3
"""Exercise automatic multi-step update recovery after a real browser reload."""
import os
import unittest
from test_browser_lora_ota_chain_runtime import automatic_harness
from test_browser_lora_ota_routed_runtime import one_replace
from test_browser_lora_ota_nrf_runtime import run_harness
from test_browser_lora_ota_runtime import BROWSER


def auto_reload_harness(case='continue'):
    harness=automatic_harness(interrupt=True)
    if case=='wrong-source':
        harness=one_replace(harness,"const source='a'.repeat(64), target='b'.repeat(64), cmds=[];", "const source=(sessionStorage.getItem('auto-resume-reloaded')?'c':'a').repeat(64), target='b'.repeat(64), cmds=[];")
    start=harness.index("      const put=(name,stage)=>")
    end=harness.index("    } catch(e){await report(",start)
    flow=r'''
      const put=(name,stage)=>{const dt=new DataTransfer();dt.items.add(new File([stage.image],name+'.bin'));return dt.files;};
      if(!sessionStorage.getItem('auto-resume-reloaded')){
        el('base').files=put('base',stages[0]);el('file').files=put('goal',stages.at(-1));el('key').value=target;
        el('key').dispatchEvent(new Event('input',{bubbles:true}));
        el('connect').click();await wait(()=>!el('inspect').disabled);
        el('inspect').click();await wait(()=>!el('update').disabled||el('workflow-status').dataset.state==='error');
        if(el('update').disabled)throw Error('Initial preparation failed: '+el('status').textContent);
        let interrupted=false;const remote=port.remote.bind(port);port.remote=async cmd=>{
          if(cmd.startsWith('ota pull ')&&stageIndex===1&&!interrupted){interrupted=true;return 'ERR simulated transfer interruption';}
          return remote(cmd);
        };
        el('update').click();await wait(()=>el('workflow-status').dataset.state==='error');
        if(stageIndex!==1)throw Error('First bridge installation did not complete');
        const original=await LoraOtaResumeStore.load();
        if(!original||original.kind!=='automatic')throw Error('Automatic update was not saved');
        if(CASE==='wrong-image')await LoraOtaResumeStore.save({...original,file:new File([stages[0].image],'goal.bin')});
        sessionStorage.setItem('auto-resume-reloaded','1');location.reload();return;
      }
      // The remote node retained its first verified bridge while the browser restarted.
      stageIndex=1;actualApplications=0;targetTemp=false;state='idle';
      await wait(()=>!el('saved-update').hidden);
      const saved=await LoraOtaResumeStore.peek(),restoredKey=el('key').value;
      el('connect').click();await wait(()=>!el('inspect').disabled||el('workflow-status').dataset.state==='error');
      if(el('workflow-status').dataset.state==='error')throw Error('Reconnect failed: '+el('status').textContent);
      if(CASE==='wrong-target'){el('key').value='c'.repeat(64);el('key').dispatchEvent(new Event('input',{bubbles:true}));}
      const before=cmds.length;
      el('saved-update-resume').click();
      await wait(()=>el('update').textContent.includes('Continue firmware update')||el('workflow-status').dataset.state==='error');
      const firstStatus=el('status').textContent,button=el('update').textContent,disabled=el('update').disabled;
      if(CASE==='continue'&&el('workflow-status').dataset.state!=='error'){
        el('update').click();await wait(()=>el('workflow-status').dataset.state==='complete'||el('workflow-status').dataset.state==='error');
      }
      await report({saved,restoredKey,before,firstStatus,button,disabled,status:el('status').textContent,
        notice:el('workflow-status').textContent,cmds,stageIndex,actualApplications,remaining:await LoraOtaResumeStore.peek()});return;
'''.replace('CASE',repr(case))
    return harness[:start]+flow+harness[end:]


class AutomaticResumeReloadTest(unittest.TestCase):
    def setUp(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER')=='1':self.fail('Chromium is required')
            self.skipTest('Chromium unavailable')

    def test_automatic_path_resumes_after_reload(self):
        result=run_harness(auto_reload_harness(),timeout=240)
        self.assertNotIn('error',result,result)
        self.assertEqual(result['saved']['kind'],'automatic')
        self.assertEqual(result['restoredKey'],'b'*64)
        self.assertEqual(result['button'],'Continue firmware update')
        self.assertFalse(result['disabled'])
        self.assertIn('Update complete',result['notice'])
        self.assertEqual(result['stageIndex'],2)
        self.assertEqual(result['actualApplications'],1)
        self.assertIsNone(result['remaining'])

    def test_wrong_source_target_or_goal_are_rejected_without_recovery_writes(self):
        for case in ['wrong-source','wrong-target','wrong-image']:
            with self.subTest(case=case):
                result=run_harness(auto_reload_harness(case),timeout=240)
                self.assertNotIn('error',result,result)
                self.assertIn('Error:',result['firstStatus'])
                self.assertEqual(result['stageIndex'],1)
                self.assertEqual(result['actualApplications'],0)
                self.assertFalse(any('ota pull ' in cmd or 'ota installhash ' in cmd or cmd.endswith('ota install') for cmd in result['cmds']))


if __name__=='__main__':unittest.main()
