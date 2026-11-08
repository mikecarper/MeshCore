#!/usr/bin/env python3
"""Exercise the ordinary one-button update with the real UI and serial session."""
import os
import unittest

from test_browser_lora_ota_runtime import BROWSER, HARNESS
from test_browser_lora_ota_nrf_runtime import run_harness
from test_browser_lora_ota_legacy_investigation_runtime import legacy_harness


def workflow_harness(*, transfer_failure=False, legacy_install=False, catalog=False, staged=False):
    script = HARNESS
    if legacy_install:
        script = script.replace("return 'ERR usage: ota installhash <64-hex-container-sha256>';", "return 'Error: unsupported';")
        script = script.replace("if(/^ota installhash [0-9a-f]{64}$/.test(cmd))", "if(cmd==='ota install')")
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


class BrowserLoraOtaWorkflowRuntime(unittest.TestCase):
    def setUp(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1':
                self.fail('Chromium required')
            self.skipTest('Chromium unavailable')

    def test_legacy_temporary_channel_mismatch_shows_exact_recovery_without_writes(self):
        script = legacy_harness('package').replace(
            "value='TempRadio active: '+radio+' 100s left,preamble=32'",
            "value='TempRadio active: 909.950,250,7,5 7200s left,preamble=128'")
        old = "if(el('transfer').disabled||!el('resume').disabled||!el('install').disabled)throw Error('validated legacy OTA preflight did not enable transfer');"
        self.assertEqual(script.count(old), 1)
        script = script.replace(old, """await report({notice:el('workflow-status').textContent,manual:el('radio-commands').textContent,
          advancedOpen:el('advanced-tools').open,radioOpen:el('radio-setup-panel').open,
          updateDisabled:el('update').disabled,cmds,frames:port.frames});return;""")
        result = run_harness(script, timeout=90)
        self.assertNotIn('error', result, result)
        self.assertIn('Saved: 910.525,62.5,7,5; temporary: 909.95,250,7,5', result['notice'])
        self.assertIn('tempradio 910.525,62.5,7,5,120,128', result['manual'])
        self.assertTrue(result['advancedOpen'])
        self.assertTrue(result['radioOpen'])
        self.assertTrue(result['updateDisabled'])
        self.assertFalse(any(frame[0] == 65 for frame in result['frames']))
        self.assertFalse(any(command.startswith(('set ', 'tempradio ', 'ota pull ')) for command in result['cmds']))

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
