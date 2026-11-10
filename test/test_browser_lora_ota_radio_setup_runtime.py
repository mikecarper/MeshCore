#!/usr/bin/env python3
"""Real page/Companion protocol regression; fake only the USB radio endpoint."""
import os
import re
import unittest

from test_browser_lora_ota_companion_runtime import HARNESS
from test_browser_lora_ota_nrf_runtime import run_harness
from test_browser_lora_ota_runtime import BROWSER
from test_browser_lora_ota_workflow_runtime import legacy_setup_harness


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise AssertionError('Expected exactly one existing harness anchor: '+old)
    return source.replace(old, new, 1)


def delayed_legacy_target_harness():
    """An older target acknowledges TempRadio before its getter becomes active."""
    harness = legacy_setup_harness(matching=False)
    harness = replace_once(harness, 'timeline=[],trialProof=false;',
        'timeline=[],trialProof=false,targetSettling=false,targetReadbacks=[];')
    harness = replace_once(harness, "else if(text==='get tempradio')reply='> '+targetTemp+',128';", """
        else if(text==='get tempradio'){
          reply=targetSettling?'> off':'> '+targetTemp+',128';
          targetReadbacks.push(reply);targetSettling=false;
        }""")
    harness = replace_once(harness,
        "targetTemp=text.slice(10).split(',').slice(0,4).join(',');reply='OK - temporary channel applying';",
        "targetTemp=text.slice(10).split(',').slice(0,4).join(',');targetSettling=true;reply='OK - temporary channel applying';")
    harness = replace_once(harness, """    if(el('radio-setup-panel').open)throw Error('Manual channel setup was shown');
    el('update').click();await wait(()=>el('workflow-status').dataset.state==='complete'||el('workflow-status').dataset.state==='error');
    if(el('workflow-status').dataset.state!=='complete')throw Error(el('workflow-status').textContent);""", """
    if(el('radio-setup-panel').open)throw Error('Manual channel setup was shown');
    await report({prepared:!el('update').disabled,timeline,targetReadbacks,cmds,frames:port.frames,
      sourceInfo:el('source-info').textContent,status:el('status').textContent});return;""")
    return harness


def radio_setup_harness(*, secondary_supported=True, pending_firmware=False, factory_defaults=False, policy_failure=False):
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
          const [,,field,value]=text.split(' ');if(!POLICY_FAILURE)targetPolicy[field]=field==='hops'?Number(value):value;reply=POLICY_FAILURE?'Error: settings not saved':'OK';
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
          if(!guard||(sourceTemp!=='off'&&!text.endsWith(',120,128'))||(!SECONDARY_SUPPORTED&&targetTemp!=='active')||text.startsWith('set ')!==SECONDARY_SUPPORTED)throw Error('Unsafe source setup');
          sourceTemp='pending';value='OK - temporary channel applying';
        }''')
    marker="  window.addEventListener('load',async()=>{try{"
    if harness.count(marker)!=1:raise AssertionError('Expected one browser load scenario')
    harness=harness[:harness.index(marker)]+r'''
  window.addEventListener('load',async()=>{try{
    const Transport=LoraOtaCompanion.CompanionTransport;
    LoraOtaCompanion.CompanionTransport=class extends Transport{constructor(...args){super(...args);window.testLink=this;}};
    const readFile=File.prototype.arrayBuffer;
    File.prototype.arrayBuffer=function(){fileReads++;timeline.push(['file-read']);return readFile.call(this);};
    expected=new Uint8Array(100);const v=new DataView(expected.buffer),at=44;expected.set(enc.encode('EndF'),at);v.setUint32(at+4,at,true);
    const bh=new Uint8Array(await crypto.subtle.digest('SHA-256',expected.slice(0,at))).slice(0,8);expected.set(bh,at+8);body=hex(bh);v.setUint32(at+16,0x01120001,true);v.setUint32(at+20,0x12345678,true);expected.set(enc.encode('board'),at+24);
    for(const name of ['buildFullMota','buildSequentialMota'])if(typeof MeshCoreMotaPackage[name]==='function'){
      const original=MeshCoreMotaPackage[name];MeshCoreMotaPackage[name]=function(...args){packageBuilds++;return original.apply(this,args);};
    }
    el('password').value='password';el('connect').click();
    await wait(()=>!el('inspect').disabled||el('status').textContent.includes('Error:'));
    if(el('status').textContent.includes('Error:'))throw Error(el('status').textContent);
    const target=document.querySelector('button[data-contact-key="'+key+'"]');if(!target)throw Error('Target contact missing');target.click();
    const defaults=Object.fromEntries(['radio-frequency','radio-bandwidth','radio-sf','radio-cr','radio-minutes','radio-preamble'].map(name=>[name,el(name).value]));
    const files=new DataTransfer();files.items.add(new File([expected],'firmware.bin'));el('file').files=files.files;
    // A single ordinary firmware-check action must prepare the channel itself.
    // Never press Set up channel in these scenarios.
    el('inspect').click();await wait(()=>!el('inspect').disabled);
    const inspectStatus=el('status').textContent,prepared=!el('update').disabled;
    let checked='';
    if(!FIRMWARE_PENDING&&!POLICY_FAILURE){
      if(!prepared||inspectStatus.includes('Error:'))throw Error(inspectStatus);
      await wait(()=>!el('radio-check').disabled);el('status').textContent='';el('radio-setup-status').textContent='';
      el('radio-check').click();await wait(()=>el('radio-setup-status').textContent.startsWith('Channel ready:')||el('status').textContent.includes('Error:'));
      if(el('status').textContent.includes('Error:'))throw Error(el('status').textContent);checked=el('radio-setup-status').textContent;
    }
    const profiles=[window.testLink.otaRadioProfile,window.testLink.otaTargetProfile];
    const diagnosticsOpen=el('diagnostics').open,prepareDisabled=el('inspect').disabled,updateDisabled=el('update').disabled;
    el('disconnect').click();await wait(()=>!el('connect').disabled);
    await report({inspectStatus,prepared,checked,profiles,defaults,timeline,cmds,frames:port.frames,sourceSaved:radio,sourceTemp,targetTemp,sourcePolicy,targetPolicy,
      fileReads,packageBuilds,guard,diagnosticsOpen,prepareDisabled,updateDisabled,transferDisabled:el('transfer').disabled});
  }catch(e){await report({error:String(e),status:el('status').textContent,channelStatus:el('radio-setup-status')?.textContent,
    timeline,cmds,frames:port.frames,fileReads,packageBuilds});}});
})();
</script>'''
    return harness.replace('SECONDARY_SUPPORTED','true'if secondary_supported else'false').replace('FIRMWARE_PENDING','true'if pending_firmware else'false').replace('FACTORY_DEFAULTS','true'if factory_defaults else'false').replace('POLICY_FAILURE','true'if policy_failure else'false')


def missed_switch_harness():
    harness = radio_setup_harness()
    harness = replace_once(harness, 'let sourceTemp=',
        "let ignoredSwitches=1,mailboxPending=false;const profileTrace=[];let sourceTemp=")
    harness = replace_once(harness,
        "const text=command.slice(3);cmds.push(text);",
        "const text=command.slice(3);cmds.push(text);profileTrace.push([selectedProfile,text,Date.now()]);mailboxPending=false;")
    harness = replace_once(harness, "if(text==='get public.key')reply='> '+key;",
        "if(text==='get public.key'&&selectedProfile===1&&targetTemp==='off'){mailboxPending=true;reply='';}else if(text==='get public.key')reply='> '+key;")
    harness = replace_once(harness,
        "value=`> ota.reply req=${corr} state=ready bytes=${b.length} off=${at} data=${hex(part)||'-'}`;",
        "value=mailboxPending?`> ota.reply req=${corr} state=pending bytes=0 off=0 data=-`:`> ota.reply req=${corr} state=ready bytes=${b.length} off=${at} data=${hex(part)||'-'}`;")
    harness = replace_once(harness,
        "targetTemp='active';targetAcknowledgedAt=Date.now();reply='OK - temporary channel applying';",
        "if(ignoredSwitches){ignoredSwitches--;reply='OK - temporary channel applying';}else{targetTemp='active';targetAcknowledgedAt=Date.now();reply='OK - temporary channel applying';}")
    return replace_once(harness, 'sourceSaved:radio,sourceTemp,targetTemp,sourcePolicy,targetPolicy,',
        'profileTrace,sourceSaved:radio,sourceTemp,targetTemp,sourcePolicy,targetPolicy,')


class BrowserRadioSetupRuntime(unittest.TestCase):
    def setUp(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER')=='1':self.fail('Chromium required')
            self.skipTest('Chromium unavailable')

    def assert_automatic_setup(self,result,profile):
        self.assertNotIn('error',result,result)
        self.assertTrue(result['prepared'],result)
        self.assertNotIn('Error:',result['inspectStatus'],result)
        self.assertIn('909.950 MHz, BW250, SF7, CR4/5',result['checked'],result)
        self.assertEqual(result['defaults'],{'radio-frequency':'909.950','radio-bandwidth':'250','radio-sf':'7','radio-cr':'5','radio-minutes':'120','radio-preamble':'128'})
        self.assertEqual((result['fileReads'],result['packageBuilds']),(1,1),result)
        self.assertEqual(result['sourceSaved'],'910.525,62.5,7,5');self.assertEqual(result['profiles'],[profile,profile])
        self.assertEqual((result['sourceTemp'],result['targetTemp']),('active','active'))
        timeline=result['timeline'];command='set tempradio2 909.950,250,7,5,rxtx,120,128'if profile else'tempradio 909.950,250,7,5,120,128'
        self.assertEqual(timeline.count(['remote',command]),1);self.assertEqual(timeline.count(['local',command]),1)
        trial=command if profile else command.replace(',120,128',',2,128')
        target_at=timeline.index(['remote',trial]);source_at=timeline.index(['local',trial]);file_at=timeline.index(['file-read'])
        first, second = (source_at, target_at) if profile else (target_at, source_at)
        self.assertLess(first, second)
        self.assertTrue(any(row==['guard-close']for row in timeline[first+1:second]),timeline)
        pending_at=timeline.index(['source-pending']);active_at=timeline.index(['source-active'])
        self.assertLess(source_at,pending_at);self.assertLess(pending_at,active_at);self.assertLess(file_at,target_at)
        if profile:
            self.assertLess(active_at, target_at, timeline)
        self.assertIn(['remote','get public.key'],timeline[active_at+1:]);self.assertGreaterEqual(timeline.count(['remote','get public.key']),2)
        self.assertFalse(any(row[0]in ('local','remote')and re.match(r'^(?:set radio(?:2)?\s|tempradio\.ifidle)',row[1])for row in timeline))
        self.assertFalse(any(frame[0]==65 for frame in result['frames']),result);self.assertFalse(result['guard'],result)
        if profile:
            self.assertIn(['local','set ota.direct.radio radio2'],timeline[active_at+1:])
            for endpoint in ['local','remote']:
                self.assertIn([endpoint,'get tempradio2'],timeline)

    def test_validated_firmware_automatically_prepares_unused_radio2(self):
        self.assert_automatic_setup(run_harness(radio_setup_harness(),timeout=60),1)

    def test_missed_switch_is_rechecked_on_both_radios_then_requested_on_the_original_channel(self):
        result = run_harness(missed_switch_harness(), timeout=60)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['prepared'], result)
        self.assertNotIn('Error:', result['inspectStatus'], result)
        self.assertEqual(result['sourceSaved'], '910.525,62.5,7,5')
        self.assertEqual(result['profiles'], [1, 1])
        switch = 'set tempradio2 909.950,250,7,5,rxtx,120,128'
        writes = [row for row in result['profileTrace'] if row[1] == switch]
        self.assertEqual(len(writes), 2, result)
        self.assertTrue(all(row[0] == 0 for row in writes), result)
        self.assertGreaterEqual(writes[1][2] - writes[0][2], 5000, result)
        between = [row for row in result['profileTrace'] if writes[0][2] <= row[2] < writes[1][2]]
        self.assertGreaterEqual(sum(row[:2] == [1, 'get public.key'] for row in between), 3, result)
        self.assertIn([0, 'get public.key'], [row[:2] for row in between], result)
        self.assertEqual(result['timeline'].count(['local', switch]), 1, result)
        self.assertFalse(any(row[0] == 'local' and row[1].startswith('tempradio ')
            for row in result['timeline']), result)
        self.assertFalse(any(frame[0] == 65 for frame in result['frames']), result)

    def test_older_profile_capabilities_test_short_primary_before_full_window(self):
        self.assert_automatic_setup(run_harness(radio_setup_harness(secondary_supported=False),timeout=60),0)

    def test_legacy_target_activation_is_read_back_without_repeating_temporary_setters(self):
        result = run_harness(delayed_legacy_target_harness(), timeout=90)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['prepared'], result)
        self.assertNotIn('Error:', result['status'], result)
        # Both the short trial and its full-window extension can remain off
        # briefly after acknowledgement. Only readbacks may be repeated.
        readbacks = result['targetReadbacks']
        self.assertEqual(readbacks.count('> off'), 2, result)
        for index, reply in enumerate(readbacks):
            if reply == '> off':
                self.assertEqual(readbacks[index + 1], '> 910.525,62.5,7,5,128', result)
        writes = [row for row in result['timeline']
            if row[0] in ('remote', 'local') and row[1].startswith('tempradio ')]
        self.assertEqual(writes, [
            ['remote', 'tempradio 910.525,62.5,7,5,2,128'],
            ['local', 'tempradio 910.525,62.5,7,5,2,128'],
            ['remote', 'tempradio 910.525,62.5,7,5,120,128'],
            ['local', 'tempradio 910.525,62.5,7,5,120,128']])
        self.assertFalse(any(frame[0] in (52, 65) for frame in result['frames']), result)
        self.assertFalse(any(command.startswith(('ota pull ', 'ota install'))
            and command != 'ota installhash' for command in result['cmds']), result)

    def test_normal_defaults_are_checked_and_changed_once_before_temporary_setup(self):
        result=run_harness(radio_setup_harness(factory_defaults=True),timeout=60)
        self.assert_automatic_setup(result,1)
        timeline=result['timeline'];writes=['ota config hops 0']
        first_write=min(i for i,row in enumerate(timeline)if row[0]in('local','remote')and row[1]in writes)
        for endpoint in ['local','remote']:
            self.assertIn([endpoint,'ota config'],timeline[:first_write])
            for command in writes:
                self.assertEqual(timeline.count([endpoint,command]),1,timeline)
                self.assertLess(timeline.index([endpoint,command]),timeline.index(['remote','set tempradio2 909.950,250,7,5,rxtx,120,128']))
            for command in ['ota config autofetch off','ota config autoinstall off']:
                self.assertNotIn([endpoint,command],timeline)
            self.assertEqual(result['sourcePolicy'if endpoint=='local'else'targetPolicy'],{'rxps':'on','autofetch':'off','autoinstall':'off','hops':0})
        self.assertFalse(any(row[0] in ('local','remote') and 'radio.rxps' in row[1] for row in timeline))
        self.assertIn(['local','ota stats'],timeline[:first_write]);self.assertIn(['local','ota folder'],timeline[:first_write])
        self.assertIn(['remote','ota status'],timeline[:first_write]);self.assertIn(['remote','get public.key'],timeline[:first_write])

    def test_unverified_policy_shows_next_action_and_log_without_enabling_transfer(self):
        result=run_harness(radio_setup_harness(factory_defaults=True,policy_failure=True),timeout=60)
        self.assertNotIn('error',result,result)
        self.assertIn('Could not verify target ota.hops=0 using ota config',result['inspectStatus'])
        self.assertIn('Select Prepare update to recheck',result['inspectStatus'])
        self.assertNotIn('review the recorded changes before proceeding',result['inspectStatus'])
        self.assertTrue(result['diagnosticsOpen']);self.assertFalse(result['prepareDisabled'])
        self.assertTrue(result['updateDisabled']);self.assertTrue(result['transferDisabled'])
        self.assertEqual((result['fileReads'],result['packageBuilds']),(1,1))
        self.assertEqual(result['timeline'].count(['remote','ota config hops 0']),1)
        self.assertFalse(any(row[0]in ('local','remote')and row[1].startswith(('tempradio ','set tempradio2'))for row in result['timeline']))

    def test_pending_radio_blocks_automatic_writes_and_transfer(self):
        result=run_harness(radio_setup_harness(pending_firmware=True),timeout=60)
        self.assertNotIn('error',result,result);self.assertRegex(result['inspectStatus'],r'pending|active temporary radio window')
        self.assertEqual((result['fileReads'],result['packageBuilds']),(1,1),result);self.assertTrue(result['transferDisabled'],result)
        self.assertFalse(any(row[0]in ('local','remote')and row[1].startswith(('tempradio ','tempradio.ifidle ','set tempradio2','set radio'))for row in result['timeline']))
        self.assertFalse(any(frame[0]==65 for frame in result['frames']),result)


if __name__=='__main__':unittest.main()
