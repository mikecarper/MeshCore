#!/usr/bin/env python3
"""Real page/Companion protocol regression; fake only the USB radio endpoint."""
import os
import re
import unittest

from test_browser_lora_ota_companion_runtime import HARNESS
from test_browser_lora_ota_nrf_runtime import run_harness
from test_browser_lora_ota_runtime import BROWSER


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise AssertionError('Expected exactly one existing harness anchor: '+old)
    return source.replace(old, new, 1)


def radio_setup_harness(*, secondary_supported=True, pending_firmware=False, factory_defaults=False):
    harness=replace_once(HARNESS,"let expected,pkg,mid=''",
        "let sourceTemp='off',targetTemp='off',selectedProfile=0,targetAcknowledgedAt=0,pendingReads=0,timeline=[],fileReads=0,packageBuilds=0;const sourcePolicy={rxps:FACTORY_DEFAULTS?'on':'unsupported',autofetch:'off',autoinstall:'off',hops:FACTORY_DEFAULTS?3:0},targetPolicy={...sourcePolicy,rxps:FACTORY_DEFAULTS?'on':'off'};let expected,pkg,mid=''")
    harness=replace_once(harness,"else if(f[0]===26){if(!guard",
        "else if(f[0]===26){if(targetAcknowledgedAt&&Date.now()-targetAcknowledgedAt<2500)throw Error('Reauthenticated before target activation grace');if(!guard")
    harness=replace_once(harness,"const command=dec.decode(f.slice(13));",
        "const command=dec.decode(f.slice(13));timeline.push(['remote',command.slice(3)]);")
    harness=replace_once(harness,"if(f[1]===3)guard=false;",
        "if(f[1]===3){guard=false;timeline.push(['guard-close']);}")
    harness=replace_once(harness,"else if(text==='get radio.rxps')reply='> off,1000,1000';",r'''
        else if(text==='get radio.rxps')reply='> '+targetPolicy.rxps+',level=6,preamble=16,rx=41704,sleep=11882,mode=continuous-fallback';
        else if(text==='set radio.rxps off'){targetPolicy.rxps='off';reply='OK';}
        else if(/^ota config (autofetch|autoinstall|hops) (off|0)$/.test(text)){
          const [,,field,value]=text.split(' ');targetPolicy[field]=field==='hops'?Number(value):value;reply='OK';
        }''')
    harness=replace_once(harness,"else if(text==='ota config')reply='ota config: autofetch=off autoinstall=off hops=0';",
        "else if(text==='ota config')reply='ota config: autofetch='+targetPolicy.autofetch+' autoinstall='+targetPolicy.autoinstall+' hops='+targetPolicy.hops;")
    harness=replace_once(harness,"else if(text==='get radio.rxps')value='Error: RX power saving is unsupported on this radio';",r'''
        else if(text==='get radio.rxps')value=sourcePolicy.rxps==='unsupported'?'Error: RX power saving is unsupported on this radio':'> '+sourcePolicy.rxps+',level=6,preamble=16,rx=41704,sleep=11882,mode=continuous-fallback';
        else if(text==='set radio.rxps off'){sourcePolicy.rxps='off';value='OK';}
        else if(/^ota config (autofetch|autoinstall|hops) (off|0)$/.test(text)){
          const [,,field,valueText]=text.split(' ');sourcePolicy[field]=field==='hops'?Number(valueText):valueText;value='OK';
        }''')
    harness=replace_once(harness,"else if(text==='ota config')value='ota config: speed=1x packet=1x mode=seeder-only autofetch=off autoinstall=off checkpoint=4 advert=0min hops=0';",
        "else if(text==='ota config')value='ota config: speed=1x packet=1x mode=seeder-only autofetch='+sourcePolicy.autofetch+' autoinstall='+sourcePolicy.autoinstall+' checkpoint=4 advert=0min hops='+sourcePolicy.hops;")
    harness=replace_once(harness,"else if(text==='get tempradio'||text==='get radio')reply='> '+radio;",r'''
        else if(text==='get radio')reply='> '+radio;
        else if(text==='get tempradio')reply=SECONDARY_SUPPORTED||targetTemp==='off'?'> off':'> 909.950,250.000,7,5,128';
        else if(text==='get radio2')reply=SECONDARY_SUPPORTED?'> off (manual)':'Error: unsupported';
        else if(text==='get tempradio2')reply=!SECONDARY_SUPPORTED?'Error: unsupported':targetTemp==='off'?'> off':'> 909.950,250.000,7,5,rxtx,128; 0d2h0m (120 min) left';
        else if(text==='get radio2.cross')reply=SECONDARY_SUPPORTED?'> off':'Error: unsupported';
        else if(text==='get radio2.status')reply='> rxtx; RX=0,0 TX=0,0 switches=8 errors=0 max=200us preamble=32,128; test=4/4,4/4 ready budget=220us';
        else if(text==='get radioat'||text==='get tempradioat')reply='> -none-';
        else if(text==='get radioat2'||text==='get tempradioat2')reply=SECONDARY_SUPPORTED?'> -none-':'Error: unsupported';
        else if(text==='set tempradio2 909.950,250,7,5,rxtx,120,128'||['tempradio 909.950,250,7,5,2,128','tempradio 909.950,250,7,5,120,128'].includes(text)){
          if((targetTemp!=='off'&&!text.endsWith(',120,128'))||text.startsWith('set ')!==SECONDARY_SUPPORTED)throw Error('Unsafe target setup');
          targetTemp='active';targetAcknowledgedAt=Date.now();reply='OK - temporary channel applying';
        }''')
    harness=replace_once(harness,"} else if(f[0]===66){const text=dec.decode(f.slice(1));let value;",
        "} else if(f[0]===66){const text=dec.decode(f.slice(1));timeline.push(['local',text]);let value;")
    harness=replace_once(harness,"else if(text==='get tempradio')value='TempRadio active: '+radio+' 100s left,preamble=32';",r'''
        else if(text==='get ota.direct.radio')value=SECONDARY_SUPPORTED?'> ota.direct.radio v=1 profile='+(selectedProfile?'radio2':'radio'):'Unknown command';
        else if(text==='set ota.direct.radio radio2'||text==='set ota.direct.radio radio'){
          if(!SECONDARY_SUPPORTED||guard||sourceTemp!=='active')throw Error('Unsafe profile selection');
          selectedProfile=text.endsWith('radio2')?1:0;value='OK - ota.direct.radio '+(selectedProfile?'radio2':'radio');
        }
        else if(text==='get tempradio'){
          if(FIRMWARE_PENDING)value='TempRadio pending: 909.950,250.00,7,5,preamble=128';
          else if(SECONDARY_SUPPORTED||sourceTemp==='off')value='TempRadio inactive';
          else if(pendingReads++===0){value='TempRadio pending: 909.950,250.00,7,5,preamble=128';timeline.push(['source-pending']);}
          else{sourceTemp='active';value='TempRadio active: 909.950,250.00,7,5 7200s left,preamble=128';timeline.push(['source-active']);}
        }
        else if(text==='get radio2')value=SECONDARY_SUPPORTED?'> off (manual)':'Error: unsupported';
        else if(text==='get tempradio2')value=!SECONDARY_SUPPORTED?'Error: unsupported':sourceTemp==='off'?'> off':'> 909.950,250.000,7,5,rxtx,128; 0d2h0m (120 min) left';
        else if(text==='get radio2.cross')value=SECONDARY_SUPPORTED?'> off':'Error: unsupported';
        else if(text==='get radio2.status'){
          if(sourceTemp==='pending'&&pendingReads++===0){value='> off; RX=0,0 TX=0,0 switches=0 errors=0 max=0us preamble=32,0';timeline.push(['source-pending']);}
          else{sourceTemp='active';value='> rxtx; RX=0,0 TX=0,0 switches=8 errors=0 max=200us preamble=32,128; test=4/4,4/4 ready budget=220us';timeline.push(['source-active']);}
        }
        else if(text==='get radioat'||text==='get tempradioat')value='Error: unsupported';
        else if(text==='get radioat2'||text==='get tempradioat2')value=SECONDARY_SUPPORTED?'> -none-':'Error: unsupported';
        else if(text==='set tempradio2 909.950,250,7,5,rxtx,120,128'||['tempradio 909.950,250,7,5,2,128','tempradio 909.950,250,7,5,120,128'].includes(text)){
          if(!guard||(sourceTemp!=='off'&&!text.endsWith(',120,128'))||targetTemp!=='active'||text.startsWith('set ')!==SECONDARY_SUPPORTED)throw Error('Unsafe source setup');
          sourceTemp='pending';value='OK - temporary channel applying';
        }''')
    marker="  window.addEventListener('load',async()=>{try{"
    if harness.count(marker)!=1:raise AssertionError('Expected one browser load scenario')
    harness=harness[:harness.index(marker)]+r'''
  window.addEventListener('load',async()=>{try{
    const Transport=LoraOtaCompanion.CompanionTransport;
    LoraOtaCompanion.CompanionTransport=class extends Transport{constructor(...args){super(...args);window.testLink=this;}};
    File.prototype.arrayBuffer=function(){fileReads++;timeline.push(['file-read']);throw Error('TEST_FIRMWARE_READ_AFTER_CHANNEL_READY');};
    for(const name of ['buildFullMota','buildSequentialMota'])if(typeof MeshCoreMotaPackage[name]==='function'){
      const original=MeshCoreMotaPackage[name];MeshCoreMotaPackage[name]=function(...args){packageBuilds++;return original.apply(this,args);};
    }
    el('password').value='password';el('connect').click();
    await wait(()=>!el('inspect').disabled||el('status').textContent.includes('Error:'));
    if(el('status').textContent.includes('Error:'))throw Error(el('status').textContent);
    const target=document.querySelector('button[data-contact-key="'+key+'"]');if(!target)throw Error('Target contact missing');target.click();
    const defaults=Object.fromEntries(['radio-frequency','radio-bandwidth','radio-sf','radio-cr','radio-minutes','radio-preamble'].map(name=>[name,el(name).value]));
    const files=new DataTransfer();files.items.add(new File([new Uint8Array(256)],'firmware.bin'));el('file').files=files.files;
    // A single ordinary firmware-check action must prepare the channel itself.
    // Never press Set up channel in these scenarios.
    el('inspect').click();await wait(()=>el('status').textContent.includes('Error:'));
    const inspectStatus=el('status').textContent;
    let checked='';
    if(!FIRMWARE_PENDING){
      if(!inspectStatus.includes('TEST_FIRMWARE_READ_AFTER_CHANNEL_READY'))throw Error(inspectStatus);
      await wait(()=>!el('radio-check').disabled);el('status').textContent='';el('radio-setup-status').textContent='';
      el('radio-check').click();await wait(()=>el('radio-setup-status').textContent.startsWith('Channel ready:')||el('status').textContent.includes('Error:'));
      if(el('status').textContent.includes('Error:'))throw Error(el('status').textContent);checked=el('radio-setup-status').textContent;
    }
    const profiles=[window.testLink.otaRadioProfile,window.testLink.otaTargetProfile];
    el('disconnect').click();await wait(()=>!el('connect').disabled);
    await report({inspectStatus,checked,profiles,defaults,timeline,cmds,frames:port.frames,sourceSaved:radio,sourceTemp,targetTemp,sourcePolicy,targetPolicy,
      fileReads,packageBuilds,guard,transferDisabled:el('transfer').disabled});
  }catch(e){await report({error:String(e),status:el('status').textContent,channelStatus:el('radio-setup-status')?.textContent,
    timeline,cmds,frames:port.frames,fileReads,packageBuilds});}});
})();
</script>'''
    return harness.replace('SECONDARY_SUPPORTED','true'if secondary_supported else'false').replace('FIRMWARE_PENDING','true'if pending_firmware else'false').replace('FACTORY_DEFAULTS','true'if factory_defaults else'false')


class BrowserRadioSetupRuntime(unittest.TestCase):
    def setUp(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER')=='1':self.fail('Chromium required')
            self.skipTest('Chromium unavailable')

    def assert_automatic_setup(self,result,profile):
        self.assertNotIn('error',result,result)
        self.assertIn('TEST_FIRMWARE_READ_AFTER_CHANNEL_READY',result['inspectStatus'],result)
        self.assertIn('909.950 MHz, BW250, SF7, CR4/5',result['checked'],result)
        self.assertEqual(result['defaults'],{'radio-frequency':'909.950','radio-bandwidth':'250','radio-sf':'7','radio-cr':'5','radio-minutes':'120','radio-preamble':'128'})
        self.assertEqual((result['fileReads'],result['packageBuilds']),(1,0),result)
        self.assertEqual(result['sourceSaved'],'910.525,62.5,7,5');self.assertEqual(result['profiles'],[profile,profile])
        self.assertEqual((result['sourceTemp'],result['targetTemp']),('active','active'))
        timeline=result['timeline'];command='set tempradio2 909.950,250,7,5,rxtx,120,128'if profile else'tempradio 909.950,250,7,5,120,128'
        self.assertEqual(timeline.count(['remote',command]),1);self.assertEqual(timeline.count(['local',command]),1)
        trial=command if profile else command.replace(',120,128',',2,128')
        target_at=timeline.index(['remote',trial]);source_at=timeline.index(['local',trial]);file_at=timeline.index(['file-read'])
        self.assertTrue(any(row==['guard-close']for row in timeline[target_at+1:source_at]),timeline)
        pending_at=timeline.index(['source-pending']);active_at=timeline.index(['source-active'])
        self.assertLess(source_at,pending_at);self.assertLess(pending_at,active_at);self.assertLess(active_at,file_at)
        self.assertIn(['remote','get public.key'],timeline[active_at+1:file_at]);self.assertGreaterEqual(timeline.count(['remote','get public.key']),2)
        self.assertFalse(any(row[0]in ('local','remote')and re.match(r'^(?:set radio(?:2)?\s|tempradio\.ifidle)',row[1])for row in timeline))
        self.assertFalse(any(frame[0]==65 for frame in result['frames']),result);self.assertFalse(result['guard'],result)
        if profile:
            self.assertIn(['local','set ota.direct.radio radio2'],timeline[active_at+1:file_at])
            for endpoint in ['local','remote']:
                self.assertIn([endpoint,'get tempradio2'],timeline)

    def test_firmware_check_automatically_prepares_unused_radio2_before_package(self):
        self.assert_automatic_setup(run_harness(radio_setup_harness(),timeout=60),1)

    def test_older_profile_capabilities_test_short_primary_before_full_window(self):
        self.assert_automatic_setup(run_harness(radio_setup_harness(secondary_supported=False),timeout=60),0)

    def test_normal_defaults_are_checked_and_changed_once_before_temporary_setup(self):
        result=run_harness(radio_setup_harness(factory_defaults=True),timeout=60)
        self.assert_automatic_setup(result,1)
        timeline=result['timeline'];writes=['set radio.rxps off','ota config hops 0']
        first_write=min(i for i,row in enumerate(timeline)if row[0]in('local','remote')and row[1]in writes)
        for endpoint in ['local','remote']:
            self.assertIn([endpoint,'get radio.rxps'],timeline[:first_write]);self.assertIn([endpoint,'ota config'],timeline[:first_write])
            for command in writes:
                self.assertEqual(timeline.count([endpoint,command]),1,timeline)
                self.assertLess(timeline.index([endpoint,command]),timeline.index(['remote','set tempradio2 909.950,250,7,5,rxtx,120,128']))
            for command in ['ota config autofetch off','ota config autoinstall off']:
                self.assertNotIn([endpoint,command],timeline)
            self.assertEqual(result['sourcePolicy'if endpoint=='local'else'targetPolicy'],{'rxps':'off','autofetch':'off','autoinstall':'off','hops':0})
        self.assertIn(['local','ota stats'],timeline[:first_write]);self.assertIn(['local','ota folder'],timeline[:first_write])
        self.assertIn(['remote','ota status'],timeline[:first_write]);self.assertIn(['remote','get public.key'],timeline[:first_write])

    def test_pending_radio_blocks_automatic_writes_and_firmware_processing(self):
        result=run_harness(radio_setup_harness(pending_firmware=True),timeout=60)
        self.assertNotIn('error',result,result);self.assertRegex(result['inspectStatus'],r'pending|active temporary radio window')
        self.assertEqual((result['fileReads'],result['packageBuilds']),(0,0),result);self.assertTrue(result['transferDisabled'],result)
        self.assertFalse(any(row[0]in ('local','remote')and row[1].startswith(('tempradio ','tempradio.ifidle ','set tempradio2','set radio'))for row in result['timeline']))
        self.assertFalse(any(frame[0]==65 for frame in result['frames']),result)


if __name__=='__main__':unittest.main()
