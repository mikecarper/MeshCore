"""Verify the default staged radio2 handoff through the real browser UI."""
import unittest
from test_browser_lora_ota_nrf_runtime import run_harness
from test_browser_lora_ota_radio_setup_runtime import radio_setup_harness


def staged_harness():
    text=radio_setup_harness().replace(',128',',368').replace('preamble=128','preamble=368')
    text=text.replace("let sourceTemp='off',targetTemp='off'", "let sourceTuple='',targetTuple='',sourceTemp='off',targetTemp='off'",1)
    target_old="targetTemp==='off'?'> off':'> 909.950,500.000,5,7,rxtx,368; 0d2h0m (120 min) left'"
    target_new="targetTemp==='off'?'> off':'> '+targetTuple+',rxtx,368; 0d2h0m (120 min) left'"
    source_old="sourceTemp==='off'?'> off':'> 909.950,500.000,5,7,rxtx,368; 0d2h0m (120 min) left'"
    source_new="sourceTemp==='off'?'> off':'> '+sourceTuple+',rxtx,368; 0d2h0m (120 min) left'"
    assert target_old in text and source_old in text
    text=text.replace(target_old,target_new,1).replace(source_old,source_new,1)
    old="else if(text==='set tempradio2 909.950,500,5,7,rxtx,120,368'||['tempradio 909.950,500,5,7,2,368','tempradio 909.950,500,5,7,120,368'].includes(text)){\n          if((targetTemp!=='off'&&!text.endsWith(',120,368'))||text.startsWith('set ')!==true)throw Error('Unsafe target setup');\n          targetTemp='active';targetAcknowledgedAt=Date.now();reply='OK - temporary channel applying';\n        }"
    new="else if(text.startsWith('set tempradio2 ')){targetTuple=text.slice(15).split(',').slice(0,4).join(',');targetTemp='active';targetAcknowledgedAt=Date.now();reply='OK - temporary channel applying';}"
    assert old in text;text=text.replace(old,new,1)
    old="else if(text==='set tempradio2 909.950,500,5,7,rxtx,120,368'||['tempradio 909.950,500,5,7,2,368','tempradio 909.950,500,5,7,120,368'].includes(text)){\n          if(!guard||(sourceTemp!=='off'&&!text.endsWith(',120,368'))||(!true&&targetTemp!=='active')||text.startsWith('set ')!==true)throw Error('Unsafe source setup');\n          sourceTemp='pending';value='OK - temporary channel applying';\n        }"
    new="else if(text.startsWith('set tempradio2 ')){if(!guard)throw Error('Missing guard');sourceTuple=text.slice(15).split(',').slice(0,4).join(',');sourceTemp='pending';value='OK - temporary channel applying';}"
    assert old in text;text=text.replace(old,new,1)
    return text

class BrowserStagedHandoffRuntime(unittest.TestCase):
    def test_default_stages_radio2_with_primary_available(self):
        result=run_harness(staged_harness(),timeout=80,radio2_preamble=None)
        self.assertNotIn('error',result,result)
        self.assertTrue(result['prepared']);self.assertEqual(result['profiles'],[1,1])
        self.assertEqual(result['defaults']['radio-frequency'],'910.725')
        writes=[row[:2] for row in result['timeline'] if row[0] in ('remote','local') and row[1].startswith('set tempradio2 ')]
        self.assertEqual(writes,[['remote','set tempradio2 910.525,62.5,7,5,rxtx,120,368'],
          ['local','set tempradio2 910.525,62.5,7,5,rxtx,120,368'],
          ['remote','set tempradio2 910.725,62.5,7,5,rxtx,120,368'],
          ['local','set tempradio2 910.725,62.5,7,5,rxtx,120,368']])
        self.assertFalse(any('set radio2.only on ' in row[1] for row in result['timeline'] if len(row)>1 and isinstance(row[1],str)))

    def test_default_handoff_serves_blocks_without_installing(self):
        harness=staged_harness()
        before='packageBuilds++;return original.apply(this,args);'
        after=("packageBuilds++;return Promise.resolve(original.apply(this,args)).then(async result=>{"
               "if(name==='buildFullForSource'){pkg=await MeshCoreMotaPackage.parseMota(result.blob);mid=hex(pkg.merkleRoot);}return result;});")
        self.assertEqual(harness.count(before),1);harness=harness.replace(before,after,1)
        before='''    if(!false&&!false){
      if(!prepared||inspectStatus.includes('Error:'))throw Error(inspectStatus);
      await wait(()=>!el('radio-check').disabled);el('status').textContent='';el('radio-setup-status').textContent='';
      el('radio-check').click();await wait(()=>el('radio-setup-status').textContent.startsWith('Channel ready:')||el('status').textContent.includes('Error:'));
      if(el('status').textContent.includes('Error:'))throw Error(el('status').textContent);checked=el('radio-setup-status').textContent;
    }'''
        self.assertIn(before,harness);harness=harness.replace(before,"    if(!prepared||inspectStatus.includes('Error:'))throw Error(inspectStatus);",1)
        before="const profiles=[window.testLink.otaRadioProfile,window.testLink.otaTargetProfile];"
        after=before+"el('transfer').click();await wait(()=>!el('install').disabled||el('status').textContent.includes('Error:'));if(el('status').textContent.includes('Error:'))throw Error(el('status').textContent);"
        self.assertEqual(harness.count(before),1);harness=harness.replace(before,after,1)
        harness=harness.replace('inspectStatus,prepared,checked,profiles,defaults,timeline,','installed,otaData,inspectStatus,prepared,checked,profiles,defaults,timeline,',1)
        result=run_harness(harness,timeout=90,radio2_preamble=None)
        self.assertNotIn('error',result,result);self.assertTrue(result['otaData']);self.assertFalse(result['installed'])
        self.assertEqual(result['profiles'],[1,1])
        self.assertFalse(any('ota install ' in str(command) for command in result['cmds']))

    def test_unrecognized_primary_preview_keeps_current_channel(self):
        harness=radio_setup_harness()
        self.assertIn("radio='910.525,62.5,7,5'",harness)
        harness=harness.replace("radio='910.525,62.5,7,5'","radio='910.500,62.5,7,5'",1)
        self.assertIn('v.setUint32(48,910525,true)',harness)
        harness=harness.replace('v.setUint32(48,910525,true)','v.setUint32(48,910500,true)',1)
        needle="    const files=new DataTransfer();files.items.add(new File([expected],'firmware.bin'));el('file').files=files.files;"
        self.assertIn(needle,harness);harness=harness.replace(needle,"    await report({defaults,timeline});return;",1)
        result=run_harness(harness,timeout=60,radio2_preamble=None)
        self.assertNotIn('error',result,result)
        self.assertEqual(result['defaults']['radio-frequency'],'910.500')
        self.assertFalse(any(len(row)>1 and isinstance(row[1],str) and row[1].startswith('set tempradio2 ') for row in result['timeline']))

if __name__=='__main__':unittest.main()
