#!/usr/bin/env python3
"""Exercise reload recovery of a manually planned multi-version update."""
import os
import unittest
from test_browser_lora_ota_chain_runtime import chain_harness
from test_browser_lora_ota_routed_runtime import one_replace
from test_browser_lora_ota_nrf_runtime import run_harness
from test_browser_lora_ota_runtime import BROWSER


def manual_harness(case='load'):
    harness=chain_harness()
    if case=='wrong-source':
        harness=one_replace(harness,"const source='a'.repeat(64), target='b'.repeat(64), cmds=[];", "const source=(sessionStorage.getItem('manual-resume-reloaded')?'c':'a').repeat(64), target='b'.repeat(64), cmds=[];")
    start=harness.index("      const put=(name,stage)=>")
    end=harness.index("    } catch(e){await report(",start)
    flow=r'''
      const put=(name,stage)=>{const dt=new DataTransfer();dt.items.add(new File([stage.image],name+'.bin'));return dt.files;};
      const reloaded=sessionStorage.getItem('manual-resume-reloaded');
      if(!reloaded){
        el('base').files=put('base',stages[0]);el('chain-files').files=put('intermediate',stages[1]);el('file').files=put('goal',stages[2]);el('key').value=target;
        el('key').dispatchEvent(new Event('input',{bubbles:true}));el('chain-attest').checked=true;
        el('connect').click();await wait(()=>!el('chain-plan').disabled);
        el('chain-plan').click();await wait(()=>!el('chain-run').disabled||el('status').textContent.includes('Error:'));
        if(el('status').textContent.includes('Error:'))throw Error(el('status').textContent);
        LoraOtaChain.ChainRunner.prototype.run=async function(){
          const planHash=await this.fingerprint();this.writeChainJournal({version:1,planHash,source:this.sourceKey,target:this.targetKey,phase:'step starting',index:0});
          sessionStorage.setItem('manual-resume-reloaded','1');location.reload();return new Promise(()=>{});
        };
        el('chain-run').click();return;
      }
      // Files are deliberately not reselected after reload.
      await wait(()=>!el('saved-update').hidden);
      const stored=await LoraOtaResumeStore.peek();
      if(stored.kind!=='manual')throw Error('saved path kind is wrong');
      const restoredKey=el('key').value;
      if(CASE==='forget'){
        el('saved-update-forget').click();await wait(()=>el('saved-update').hidden);
        const forgotten=await LoraOtaResumeStore.peek(),plan=await LoraOtaAutoChain.cache('get',stored.planKey);
        await report({status:el('status').textContent,workflow:el('workflow-status').textContent,forgotten,plan:plan??null,cmds});return;
      }
      el('connect').click();await wait(()=>!el('chain-plan').disabled||el('status').textContent.includes('Error:'));
      if(el('status').textContent.includes('Error:'))throw Error(el('status').textContent);
      if(CASE==='wrong-target'){el('key').value='c'.repeat(64);el('key').dispatchEvent(new Event('input',{bubbles:true}));}
      if(CASE==='changed-config'){
        const meta=JSON.parse(localStorage.getItem('meshcore-browser-lora-ota-chain-meta'));meta.preamble++;
        localStorage.setItem('meshcore-browser-lora-ota-chain-meta',JSON.stringify(meta));
      }
      if(CASE==='tampered-plan'){
        const cached=await LoraOtaAutoChain.cache('get',stored.planKey);
        cached.prepared.steps[0].packageHash='f'.repeat(64);
        await LoraOtaAutoChain.cache('put',stored.planKey,cached);
      }
      const beforeResume=cmds.length;
      el('saved-update-resume').click();await wait(()=>!el('chain-resume').disabled||el('status').textContent.includes('Error:'));
      if((CASE==='continue'||CASE==='tampered-plan')&&!el('status').textContent.includes('Error:')){
        el('chain-resume').click();
        await wait(()=>el('status').textContent.includes('Update sequence complete;')||el('status').textContent.includes('Error:'));
      }
      const after=await LoraOtaResumeStore.peek(),afterPlan=await LoraOtaAutoChain.cache('get',stored.planKey);
      await report({status:el('status').textContent,workflow:el('workflow-status').textContent,
        restoredKey,stored,cmds,beforeResume,after,afterPlan:afterPlan??null,disabled:el('chain-resume').disabled,stageIndex,actualApplications});return;
'''
    return harness[:start]+flow.replace('CASE',repr(case))+harness[end:]


class BrowserManualChainResumeTest(unittest.TestCase):
    def setUp(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER')=='1':self.fail('Chromium is required')
            self.skipTest('Chromium unavailable')

    def test_manual_plan_survives_reload_without_starting_transfer(self):
        result=run_harness(manual_harness(),timeout=240)
        self.assertNotIn('error',result,result)
        self.assertIn('Saved path checked',result['workflow'])
        self.assertEqual(result['restoredKey'],'b'*64)
        self.assertFalse(result['disabled'])
        self.assertEqual(result['stageIndex'],0)
        self.assertEqual(result['actualApplications'],0)
        self.assertFalse(any('ota pull ' in cmd or 'ota installhash ' in cmd or cmd.endswith('ota install') for cmd in result['cmds']))


    def test_forget_removes_saved_files_and_unique_manual_plan(self):
        result=run_harness(manual_harness('forget'),timeout=240)
        self.assertNotIn('error',result,result)
        self.assertIsNone(result['forgotten'])
        self.assertIsNone(result['plan'])
        self.assertIn('remote update was not canceled',result['workflow'])
        self.assertFalse(any('ota pull ' in cmd or 'ota installhash ' in cmd or cmd.endswith('ota install') for cmd in result['cmds']))

    def test_changed_config_and_source_or_target_are_rejected_without_writes(self):
        for case in ['changed-config','wrong-source','wrong-target']:
            with self.subTest(case=case):
                result=run_harness(manual_harness(case),timeout=240)
                self.assertNotIn('error',result,result)
                self.assertIn('Error:',result['status'])
                self.assertEqual(result['stageIndex'],0)
                self.assertFalse(any('ota pull ' in cmd or 'ota installhash ' in cmd or cmd.endswith('ota install') for cmd in result['cmds']))

    def test_tampered_plan_is_rejected_before_transfer(self):
        result=run_harness(manual_harness('tampered-plan'),timeout=240)
        self.assertNotIn('error',result,result)
        self.assertIn('No matching chain recovery record',result['status'])
        self.assertEqual(result['stageIndex'],0)
        self.assertFalse(any('ota pull ' in cmd or 'ota installhash ' in cmd or cmd.endswith('ota install') for cmd in result['cmds']))

    def test_explicit_resume_continues_verified_manual_chain(self):
        result=run_harness(manual_harness('continue'),timeout=240)
        self.assertNotIn('error',result,result)
        self.assertIn('Update sequence complete',result['status'])
        self.assertGreater(result['actualApplications'],0)
        self.assertEqual(result['stageIndex'],result['actualApplications'])
        self.assertIsNone(result['after'])
        self.assertIsNone(result['afterPlan'])


if __name__=='__main__':unittest.main()
