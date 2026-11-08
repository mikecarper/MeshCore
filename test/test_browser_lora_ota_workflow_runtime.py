#!/usr/bin/env python3
"""Exercise the ordinary one-button update with the real UI and serial session."""
import os
import unittest

from test_browser_lora_ota_runtime import BROWSER, HARNESS
from test_browser_lora_ota_nrf_runtime import run_harness
from test_browser_lora_ota_legacy_investigation_runtime import legacy_harness
from test_browser_lora_ota_companion_runtime import HARNESS as COMPANION_HARNESS


def workflow_harness(*, transfer_failure=False, legacy_install=False, catalog=False, staged=False, reboot_channel=False):
    script = HARNESS
    if legacy_install:
        script = script.replace("return 'ERR usage: ota installhash <64-hex-container-sha256>';", "return 'Error: unsupported';")
        script = script.replace("if(/^ota installhash [0-9a-f]{64}$/.test(cmd))", "if(cmd==='ota install')")
    if reboot_channel:
        script = script.replace("const radio='910.525,62.5,7,5,120';", "const radio='910.525,62.5,7,5,120';let sourceActive='910.525,62.5,7,5';")
        script = script.replace("cmds.push('remote '+cmd);", "cmds.push('remote '+cmd);if(sourceActive!==(installed?'907.525,125,8,5':'910.525,62.5,7,5'))throw Error('RF channel mismatch');")
        script = script.replace("if(cmd==='get radio')return radio.split(',').slice(0,4).join(',');", "if(cmd==='get radio')return '907.525,125,8,5';")
        script = script.replace("'get tempradio':radio,'get radio':radio,", "'get tempradio':sourceActive+',128','get radio':'910.525,62.5,7,5',")
        script = script.replace("if(cmd==='ota folder')reply=", "if(cmd.startsWith('tempradio ')){sourceActive=cmd.slice(10).split(',').slice(0,4).join(',');reply='OK';}\n      if(cmd==='ota folder')reply=")
    if transfer_failure:
        script = script.replace("state='ready';", "state='failed';")
        script = script.replace("(state==='ready'?'ready to install':'fetching')", "(state==='failed'?'failed':state==='ready'?'ready to install':'fetching')")
    old = """el('transfer').click();await wait(()=>!el('install').disabled);
      el('install').click();await wait(()=>el('status').textContent.includes('Installed and verified:'));"""
    new = """if(el('update').disabled)throw Error('Ordinary update action not enabled after preparation');
      if(el('diagnostics').open||el('radio-setup-panel').open||el('manual-firmware').open)throw Error('Advanced options opened on the ordinary path');
      el('update').click();el('update').click(); // a double click must not send another pull or install
      await wait(()=>el('workflow-status').dataset.state==='complete'||el('workflow-status').dataset.state==='error');
      if(TRANSFER_FAILURE){
        await report({status:el('status').textContent,notice:el('workflow-status').textContent,summary:el('update-summary').textContent,
          cmds,installed,updateDisabled:el('update').disabled,prepareDisabled:el('inspect').disabled,journal:JSON.parse(localStorage.getItem('meshcore-browser-lora-ota'))});return;
      }
      if(el('workflow-status').dataset.state!=='complete')throw Error(el('workflow-status').textContent);"""
    if script.count(old) != 1:
        raise AssertionError('Expected one update action sequence')
    if staged:
        new = """el('transfer').click();await wait(()=>!el('install').disabled);
      el('inspect').click();await wait(()=>!el('update').disabled);
      if(el('update').textContent!=='Install and verify update')throw Error('Staged package not recognized');
      """ + new
    script = script.replace(old, new).replace('TRANSFER_FAILURE', 'true' if transfer_failure else 'false')
    if catalog:
        # Keep the real discovery, digest verification and package parser. Only
        # provide fixture HTTP responses for the public catalog/mirror.
        script = script.replace("const image=new Uint8Array(100), v=new DataView(image.buffer), at=44;", """const marker=enc.encode('OTA: status\\0get <id> flash | install\\0');
      const at=marker.length,image=new Uint8Array(at+56),v=new DataView(image.buffer);image.set(marker);
      const env='test_board_repeater',targetId=await LoraOtaCatalog.idForEnv(env);
      const name=env+'-ota-v1.18.0.1.bin',url='https://github.com/mikecarper/MeshCore/releases/download/v1.18.0.1/'+name;""")
        script = script.replace("v.setUint32(at+20,0x12345678,true)", "v.setUint32(at+20,targetId,true)")
        script = script.replace("const dt=new DataTransfer();dt.items.add(new File([image],'firmware.bin'));el('file').files=dt.files;", """const hash=hex(new Uint8Array(await crypto.subtle.digest('SHA-256',image)));
      const compressed=new Uint8Array(await new Response(new Blob([image]).stream().pipeThrough(new CompressionStream('gzip'))).arrayBuffer());
      const gzipHash=hex(new Uint8Array(await crypto.subtle.digest('SHA-256',compressed)));
      const item={project:'MeshCore',provider:'Keymind Cascade',role:'repeater',action:'flash-update',hardware:'board',architecture:'esp32',version:'v1.18.0.1',name,url};
      const realFetch=window.fetch.bind(window);window.fetch=async(input,options)=>{
        const address=String(input);
        const payload=address.endsWith('/catalog.json')?{items:[item]}:
          address.endsWith('/firmware_controls.json')?{familyTag:'v1.18.0.1',profiles:{[env]:{platform:'ESP32_PLATFORM',otaRole:'lora-receiver',updateMethods:['lora']}}}:
          address.endsWith('/firmware/index.json')?{schemaVersion:1,entries:[{url,sha256:hash,size:image.length,gzipSha256:gzipHash,gzipSize:compressed.length,path:hash+'.gz'}]}:
          address.includes('/releases/tags/')?{tag_name:'v1.18.0.1',draft:false,prerelease:false,assets:[{name,browser_download_url:url}]}:null;
        if(payload)return new Response(JSON.stringify(payload));
        if(address.endsWith('/firmware/'+hash+'.gz'))return new Response(compressed);
        return realFetch(input,options);
      };
      const remote=port.remote.bind(port);port.remote=cmd=>{
        if(cmd==='ota status')return remote(cmd).replace('target:12345678','target:'+targetId.toString(16).padStart(8,'0'));
        if(cmd==='get bootloader.ver'||cmd==='ota bootloader status')return 'Error: unsupported';
        if(cmd==='ota key list')return 'No keys';
        return remote(cmd);
      };
      const write=port.write.bind(port);port.write=async bytes=>{
        if(dec.decode(bytes).trim()==='ota.peer timeout'){queueMicrotask(()=>port.push(enc.encode('ota.peer timeout\\r\\n-> OTApeer timeout=7s\\r\\n')));return;}
        return write(bytes);
      };""")
        script = script.replace("el('inspect').click();await wait(()=>!el('transfer').disabled);", """el('investigate').click();await wait(()=>!el('firmware-role').disabled && !el('firmware-role-wrap').hidden);
      el('firmware-role').value='repeater';el('firmware-role').dispatchEvent(new Event('change'));
      await wait(()=>!el('inspect').disabled && el('firmware-catalog-status').textContent.startsWith('Downloaded '));
      if(el('file').files.length)throw Error('Manual file was required for catalog download');
      el('inspect').click();await wait(()=>!el('transfer').disabled);""")
    return script


def legacy_setup_harness(rxps_on=False, matching=False, reboot_channel=False):
    """Actual UI and binary transport, with stateful legacy radio endpoints."""
    script = legacy_harness('full_transfer')
    # Shorten only the firmware-transfer polling interval; keep the real
    # temporary-channel settling delays and USB/LoRa request sequence.
    script = script.replace('(()=>{', '(()=>{const timer=window.setTimeout;window.setTimeout=(fn,ms,...args)=>timer(fn,ms===20000?10:ms,...args);', 1)
    script = script.replace("let inbox=[],", """let sourceTemp=MATCHING?'910.525,62.5,7,5':'909.950,250,5,5',targetTemp=sourceTemp,
      sourceRxps=RXPS_ON?'on':'off',targetRxps=sourceRxps,targetHops=RXPS_ON?3:0,
      timeline=[],trialProof=false;let inbox=[],""")
    script = script.replace('MATCHING', 'true' if matching else 'false').replace('RXPS_ON', 'true' if rxps_on else 'false')
    script = script.replace("const f=bytes.slice(3);this.frames.push(Array.from(f));let out;", """const f=bytes.slice(3);this.frames.push(Array.from(f));let out;
      if([2,26,65].includes(f[0])&&sourceTemp!==targetTemp)throw Error('RF attempted on mismatched channels');""")
    script = script.replace("if(text==='get public.key')reply='> '+key;", """if(text==='get public.key'){
          if(sourceTemp==='910.525,62.5,7,5'&&timeline.some(r=>r[1]==='tempradio 910.525,62.5,7,5,2,128'))trialProof=true;
          timeline.push(['remote-proof',sourceTemp]);reply='> '+key;
        }""")
    script = script.replace("else if(text==='get tempradio'||text==='get radio')reply='> '+radio;", """
        else if(text==='get radio')reply='> 910.5250244,62.5,7,5';
        else if(text==='get tempradio')reply='> '+targetTemp+',128';
        else if(['get radio2','get tempradio2','get radio2.cross','get radioat2','get tempradioat2'].includes(text))reply='Error: unsupported';
        else if(['get radioat','get tempradioat'].includes(text))reply='> -none-';
        else if(text.startsWith('tempradio ')){
          timeline.push(['remote',text]);if(text.endsWith(',120,128')&&!trialProof)throw Error('Long lease before trial proof');
          targetTemp=text.slice(10).split(',').slice(0,4).join(',');reply='OK - temporary channel applying';
        }
        else if(text==='set radio.rxps off'){targetRxps='off';timeline.push(['remote',text]);reply='OK';}
        else if(text==='ota config hops 0'){targetHops=0;timeline.push(['remote',text]);reply='OK';}""")
    script = script.replace("else if(text==='get radio.rxps')reply='> off,1000,1000';", "else if(text==='get radio.rxps')reply='> '+targetRxps+',1000,1000';")
    script = script.replace("else if(text==='ota config')reply='ota config: autofetch=off autoinstall=off hops=0';", "else if(text==='ota config')reply='ota config: autofetch=off autoinstall=off hops='+targetHops;")
    script = script.replace("else if(text==='get radio')value='> '+radio;", "else if(text==='get radio')value='> 910.5250244,62.5,7,5';")
    script = script.replace("else if(text==='get tempradio')value='TempRadio active: '+radio+' 100s left,preamble=32';", """
        else if(text==='get tempradio')value='TempRadio active: '+sourceTemp+' 7200s left,preamble=128';
        else if(['get radio2','get tempradio2','get radio2.cross','get radioat','get tempradioat','get radioat2','get tempradioat2'].includes(text))value='Error: unsupported';
        else if(text.startsWith('tempradio ')){
          timeline.push(['local',text]);if(text.endsWith(',120,128')&&!trialProof)throw Error('Long source lease before trial proof');
          sourceTemp=text.slice(10).split(',').slice(0,4).join(',');value='OK - temporary channel applying';
        }
        else if(text==='set radio.rxps off'){sourceRxps='off';timeline.push(['local',text]);value='OK';}""")
    script = script.replace("else if(text==='get radio.rxps')value='Error: RX power saving is unsupported on this radio';",
        "else if(text==='get radio.rxps')value='radio.rxps '+sourceRxps+',level=6,preamble=16,rx=41704,sleep=11882,mode=continuous-fast';")
    script = script.replace("else if(text==='ota config')value='ota config: speed=1x packet=1x mode=seeder-only autofetch=off autoinstall=off checkpoint=4 advert=0min hops=0';",
        "else if(text==='ota config')value='Unknown command';")
    script = script.replace("else if(text==='ota stats')value='OTA | fetch idle';", "else if(text==='ota stats')value='Unknown command';")
    script = script.replace("else if(text==='ota folder')value='folder=off serving=0:';", "else if(text==='ota folder')value='Unknown command';")
    # Use only the ordinary update action; no manual channel controls.
    start=script.index("    el('transfer').click();await wait(()=>!el('install').disabled",script.index("const chosen=new DataTransfer()"))
    end=script.index("if(el('status').textContent.includes('Error:'))throw Error(el('status').textContent);",script.index("el('install').click();",start))
    end += len("if(el('status').textContent.includes('Error:'))throw Error(el('status').textContent);")
    script = script[:start]+"""    if(el('radio-setup-panel').open)throw Error('Manual channel setup was shown');
    el('update').click();await wait(()=>el('workflow-status').dataset.state==='complete'||el('workflow-status').dataset.state==='error');
    if(el('workflow-status').dataset.state!=='complete')throw Error(el('workflow-status').textContent);"""+script[end:]
    script = script.replace("investigateClicks,beforeActionFrames,installed,", "timeline,sourceRxps,targetRxps,targetHops,sourceTemp,targetTemp,frequency:el('radio-frequency').value,investigateClicks,beforeActionFrames,installed,")
    if reboot_channel:
        script = script.replace("else if(text==='get radio')reply='> 910.5250244,62.5,7,5';", "else if(text==='get radio')reply='> 907.525,125,8,5';")
        script = script.replace("installed=true;", "installed=true;targetTemp='907.525,125,8,5';")
    return script


class BrowserLoraOtaWorkflowRuntime(unittest.TestCase):
    def setUp(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1':
                self.fail('Chromium required')
            self.skipTest('Chromium unavailable')

    def test_legacy_manual_windows_are_aligned_then_transferred_without_manual_setup(self):
        result = run_harness(legacy_setup_harness(), timeout=90)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['installed']); self.assertTrue(result['otaData'])
        self.assertEqual(result['sourceTemp'], '910.525,62.5,7,5')
        self.assertEqual(result['targetTemp'], result['sourceTemp'])
        self.assertEqual(result['frequency'], '910.525')
        writes = [r for r in result['timeline'] if r[0] in ('remote', 'local') and r[1].startswith('tempradio ')]
        self.assertEqual(writes, [
            ['remote', 'tempradio 910.525,62.5,7,5,2,128'], ['local', 'tempradio 910.525,62.5,7,5,2,128'],
            ['remote', 'tempradio 910.525,62.5,7,5,120,128'], ['local', 'tempradio 910.525,62.5,7,5,120,128']])
        self.assertFalse(any(f[0] == 52 for f in result['frames']))

    def test_rxps_on_with_matching_manual_channels_is_disabled_and_update_continues(self):
        result = run_harness(legacy_setup_harness(rxps_on=True, matching=True), timeout=90)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['installed']); self.assertTrue(result['otaData'])
        self.assertEqual((result['sourceRxps'], result['targetRxps'], result['targetHops']), ('off', 'off', 0))
        for endpoint in ('remote', 'local'):
            self.assertEqual(result['timeline'].count([endpoint, 'set radio.rxps off']), 1)
        self.assertFalse(any(r[1].startswith('tempradio ') for r in result['timeline']))

    def test_repeater_source_follows_old_and_new_targets_back_to_saved_channel(self):
        for old_target in (False, True):
            with self.subTest(old_target=old_target):
                result = run_harness(workflow_harness(legacy_install=old_target, reboot_channel=True), timeout=45)
                self.assertNotIn('error', result, result)
                self.assertEqual(result['journal']['phase'], 'installed')
                self.assertEqual(result['cmds'].count('local tempradio 907.525,125,8,5,10,128'), 1)
                installs = [c for c in result['cmds'] if c == 'remote ota install' or c.startswith('remote ota installhash ')]
                self.assertEqual(len(installs), 1)

    def test_legacy_companion_reconnects_after_target_loses_temporary_channel_on_reboot(self):
        result = run_harness(legacy_setup_harness(reboot_channel=True), timeout=90)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['installed'])
        self.assertEqual(result['sourceTemp'], '907.525,125,8,5')
        self.assertEqual(result['targetTemp'], result['sourceTemp'])
        self.assertEqual(result['timeline'].count(['local', 'tempradio 907.525,125,8,5,10,128']), 1)
        self.assertEqual(result['cmds'].count('ota install'), 1)

    def test_guarded_companion_retunes_under_guard_and_reauthenticates_after_reboot(self):
        script = COMPANION_HARNESS.replace("let expected,pkg,", "let sourceActive=radio;let expected,pkg,")
        script = script.replace("const f=bytes.slice(3);this.frames.push(Array.from(f));let out;", """const f=bytes.slice(3);this.frames.push(Array.from(f));let out;
          if([2,26,65].includes(f[0])&&sourceActive!==(installed?'907.525,125,8,5':radio))throw Error('RF channel mismatch');""")
        script = script.replace("else if(text==='get tempradio'||text==='get radio')reply='> '+radio;", "else if(text==='get radio')reply='> 907.525,125,8,5';else if(text==='get tempradio')reply='> '+radio;")
        script = script.replace("else if(text==='get tempradio')value='TempRadio active: '+radio+' 100s left,preamble=32';", """else if(text==='get tempradio')value='TempRadio active: '+sourceActive+' 600s left,preamble=128';
          else if(text.startsWith('tempradio ')){if(!guard)throw Error('Unguarded local retune');sourceActive=text.slice(10).split(',').slice(0,4).join(',');value='OK';}""")
        result = run_harness(script, timeout=90)
        self.assertNotIn('error', result, result)
        self.assertIn('Installed and verified:', result['status'])
        self.assertEqual(sum(c.startswith('ota installhash ') for c in result['cmds']), 1)
        self.assertFalse(result['guard'])

    def test_single_action_transfers_installs_and_verifies_once(self):
        result = run_harness(workflow_harness(), timeout=40)
        self.assertNotIn('error', result, result)
        self.assertEqual(result['journal']['phase'], 'installed')
        self.assertEqual(sum(c.startswith('remote ota pull ') for c in result['cmds']), 1)
        self.assertEqual(sum(c.startswith('remote ota installhash ') for c in result['cmds']), 1)
        self.assertFalse(result['folder'])

    def test_already_transferred_package_installs_without_another_pull(self):
        result = run_harness(workflow_harness(staged=True), timeout=40)
        self.assertNotIn('error', result, result)
        self.assertEqual(result['journal']['phase'], 'installed')
        self.assertEqual(sum(c.startswith('remote ota pull ') for c in result['cmds']), 1)
        self.assertEqual(sum(c.startswith('remote ota installhash ') for c in result['cmds']), 1)

    def test_failed_transfer_never_installs_and_requires_fresh_state_check(self):
        result = run_harness(workflow_harness(transfer_failure=True), timeout=40)
        self.assertNotIn('error', result, result)
        self.assertIn('failed download', result['notice'])
        self.assertIn('Prepare update', result['summary'])
        self.assertTrue(result['updateDisabled'])
        self.assertFalse(result['prepareDisabled'])
        self.assertFalse(result['installed'])
        self.assertFalse(any(c.startswith('remote ota installhash ') for c in result['cmds']))

    def test_published_legacy_receiver_loads_from_catalog_and_updates_without_installhash(self):
        result = run_harness(workflow_harness(catalog=True, legacy_install=True), timeout=50)
        self.assertNotIn('error', result, result)
        self.assertEqual(result['journal']['phase'], 'installed')
        self.assertEqual(result['cmds'].count('remote ota install'), 1)
        self.assertFalse(any(c.startswith('remote ota installhash ') for c in result['cmds']))


if __name__ == '__main__':
    unittest.main()
