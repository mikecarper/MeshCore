#!/usr/bin/env python3
"""Recover staged updates through the real legacy USB Companion/browser flow."""
import os
import unittest

from test_browser_lora_ota_runtime import BROWSER
from test_browser_lora_ota_nrf_runtime import run_harness
from test_browser_lora_ota_workflow_runtime import legacy_setup_harness


def replace_once(script, before, after):
    if script.count(before) != 1:
        raise AssertionError('Expected one staged-recovery harness anchor: ' + before)
    return script.replace(before, after, 1)


def staged_recovery_harness(*, replacement=False, lost_cancel=False, changed_staging=False,
                            staged_ready=False, missing_journal=False):
    script = legacy_setup_harness(matching=True)
    script = replace_once(script, 'let inbox=[],',
        "let failNextPull=true,cancels=0,connects=0,requests=0,stagedId='',installedVersion='v1.18.0.1';let inbox=[],")
    script = replace_once(script, 'requestPort:async()=>port',
        'requestPort:async()=>{requests++;return port;}')
    script = replace_once(script,
        "if(f[0]===1){out=new Uint8Array(58);out[0]=5;out.set(unhex(source),4);",
        "if(f[0]===1){connects++;out=new Uint8Array(58);out[0]=5;out.set(unhex(source),4);")
    script = replace_once(script,
        "(state==='ready'?'ready to install':'fetching')+' id='+mid",
        "(state==='failed'?'failed':state==='ready'?'ready to install':'fetching')+' id='+stagedId")
    script = replace_once(script, "reply=installed?'v1.18.0.1':'v1.17.1.9';",
        "reply=installed?installedVersion:'v1.17.1.9';")
    # The write really reaches the target, but its USB send acknowledgement is
    # lost. No radio requests follow, so the target retains the failed image.
    script = replace_once(script,
        "else if(text.startsWith('ota pull ')){state='fetching';reply='OK';",
        """else if(text==='ota cancel'){
          cancels++;state='idle';stagedId='';
          if(LOST_CANCEL&&cancels===1)return;
          reply='OK';
        }
        else if(text.startsWith('ota pull ')){
          stagedId=text.split(' ')[2].toLowerCase();
          if(stagedId!==mid)throw Error('pull selected the wrong firmware');
          if(failNextPull){failNextPull=false;state='failed';return;}
          state='fetching';reply='OK';""".replace('LOST_CANCEL', 'true' if lost_cancel else 'false'))
    marker = "window.addEventListener('load',async()=>{try{"
    script = replace_once(script, marker, marker + """
    const Transport=LoraOtaCompanion.CompanionTransport;
    LoraOtaCompanion.CompanionTransport=class extends Transport{
      constructor(port,options){super(port,{...options,commandTimeout:120,inboxTimeout:120,inboxPollDelay:0});}
    };
    """)
    original = """    el('update').click();await wait(()=>el('workflow-status').dataset.state==='complete'||el('workflow-status').dataset.state==='error');
    if(el('workflow-status').dataset.state!=='complete')throw Error(el('workflow-status').textContent);"""
    recovery = """    el('update').click();
    await wait(()=>!el('reconnect').hidden&&!el('reconnect').disabled);
    const firstRecord=JSON.parse(localStorage.getItem('meshcore-browser-lora-ota'));
    if(state!=='failed'||firstRecord.phase!=='pull attempted'||firstRecord.id!==stagedId)
      throw Error('Uncertain pull was not retained as the same failed download');
    if(cmds.filter(c=>c.startsWith('ota pull ')).length!==1||cancels!==0)
      throw Error('Uncertain pull was replayed or discarded');
    if(!el('update').disabled||!el('inspect').disabled)throw Error('Poisoned USB still permits update commands');
    const selectedKey=el('key').value,selectedPassword=el('password').value,selectedFile=el('file').files[0];
    const reconnectAt=port.frames.length;el('reconnect').click();await wait(()=>!el('inspect').disabled);
    if(requests!==1||connects!==2)throw Error('Reconnect asked for the USB device again');
    if(el('key').value!==selectedKey||el('password').value!==selectedPassword||el('file').files[0]!==selectedFile)
      throw Error('Reconnect lost the selected target or firmware');
    if(port.frames.slice(reconnectAt).some(f=>[2,10,26,52,65].includes(f[0])))
      throw Error('Reconnect replayed target or OTA operations');
    const oldId=stagedId;
    PRIOR_STATE
    FORGET_JOURNAL
    REPLACE_FIRMWARE
    el('inspect').click();await wait(()=>!el('inspect').disabled);
    if(el('update').disabled||el('update').textContent!==EXPECTED_ACTION)
      throw Error('Recovery action unavailable: '+el('workflow-status').textContent+' / '+el('update').textContent);
    if(cancels!==0||cmds.filter(c=>c.startsWith('ota pull ')).length!==1)
      throw Error('Preparation changed the staged update');
    const recoveryAction=el('update').textContent;
    CHANGE_STAGING
    el('update').click();
    await wait(()=>el('workflow-status').dataset.state==='complete'||el('workflow-status').dataset.state==='error');
    AFTER_UPDATE
    await report({installed,state,oldId,selectedId:mid,stagedId,cancels,connects,requests,recoveryAction,
      journal:JSON.parse(localStorage.getItem('meshcore-browser-lora-ota')),cmds,frames:port.frames,
      status:el('status').textContent,notice:el('workflow-status').textContent});return;"""
    new_firmware = """expected=expected.slice();expected[0]=1;
    const next=new DataView(expected.buffer),end=expected.length-56;
    const nextBody=new Uint8Array(await crypto.subtle.digest('SHA-256',expected.slice(0,end))).slice(0,8);
    expected.set(nextBody,end+8);body=hex(nextBody);next.setUint32(end+16,0x01120002,true);
    pkg=await MeshCoreMotaPackage.parseMota(await MeshCoreMotaPackage.buildFullMota(expected));mid=hex(pkg.merkleRoot);
    installedVersion='v1.18.0.2';if(mid===oldId)throw Error('Replacement did not change package identity');
    const changed=new DataTransfer();changed.items.add(new File([expected],'different-firmware.bin'));
    el('file').files=changed.files;el('file').dispatchEvent(new Event('change',{bubbles:true}));"""
    recovery = recovery.replace('REPLACE_FIRMWARE', new_firmware if replacement else '')
    recovery = recovery.replace('PRIOR_STATE', "state='ready';" if staged_ready else '')
    recovery = recovery.replace('FORGET_JOURNAL', "localStorage.removeItem('meshcore-browser-lora-ota');" if missing_journal else '')
    recovery = recovery.replace('EXPECTED_ACTION', repr('Replace and install update' if replacement or missing_journal else 'Restart and install update'))
    recovery = recovery.replace('CHANGE_STAGING', "stagedId='deadbeef';" if changed_staging else '')
    if changed_staging:
        after_update = """if(cancels!==0||cmds.filter(c=>c.startsWith('ota pull ')).length!==1||installed)
      throw Error('Changed staged package was cancelled or updated');
    if(el('workflow-status').dataset.state!=='error')throw Error('Changed staged identity was accepted');"""
    elif lost_cancel:
        after_update = """await wait(()=>!el('reconnect').hidden&&!el('reconnect').disabled);
    if(cancels!==1||state!=='idle'||installed||cmds.filter(c=>c.startsWith('ota pull ')).length!==1)
      throw Error('Lost cancellation acknowledgement was replayed or followed by a pull');
    el('reconnect').click();await wait(()=>!el('inspect').disabled);
    el('inspect').click();await wait(()=>!el('inspect').disabled);
    if(el('update').disabled||el('update').textContent!=='Update remote firmware')
      throw Error('Already cancelled target was not recognized after fresh readback');
    el('update').click();await wait(()=>el('workflow-status').dataset.state==='complete'||el('workflow-status').dataset.state==='error');
    if(el('workflow-status').dataset.state!=='complete'||cancels!==1)
      throw Error('Replacement did not recover without repeating cancellation: '+el('workflow-status').textContent);"""
    else:
        after_update = """if(el('workflow-status').dataset.state!=='complete')throw Error(el('workflow-status').textContent);"""
    recovery = recovery.replace('AFTER_UPDATE', after_update)
    return replace_once(script, original, recovery)


class BrowserLoraOtaStagedRecoveryRuntime(unittest.TestCase):
    def setUp(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1':
                self.fail('Chromium required')
            self.skipTest('Chromium unavailable')

    def check_completed(self, result, *, replaced):
        self.assertNotIn('error', result, result)
        self.assertTrue(result['installed'], result)
        self.assertEqual(result['cancels'], int(replaced), result)
        self.assertEqual(result['journal']['phase'], 'installed', result)
        pulls = [cmd for cmd in result['cmds'] if cmd.startswith('ota pull ')]
        self.assertEqual(len(pulls), 2, result)
        self.assertEqual(pulls[-1].split()[2].lower(), result['selectedId'], result)
        if replaced:
            self.assertNotEqual(result['oldId'], result['selectedId'], result)
        else:
            self.assertEqual(result['oldId'], result['selectedId'], result)
        self.assertFalse(any(frame[0] == 52 for frame in result['frames']), result)

    def test_failed_same_package_restarts_after_usb_reconnect(self):
        result = run_harness(staged_recovery_harness(), timeout=90)
        self.check_completed(result, replaced=False)

    def test_an_abandoned_update_can_be_replaced_with_different_firmware(self):
        result = run_harness(staged_recovery_harness(replacement=True), timeout=90)
        self.check_completed(result, replaced=True)

    def test_lost_cancel_reply_requires_readback_and_is_not_repeated(self):
        result = run_harness(staged_recovery_harness(replacement=True, lost_cancel=True), timeout=90)
        self.check_completed(result, replaced=True)
        self.assertEqual(result['connects'], 3, result)

    def test_replacement_stops_if_the_staged_identity_changed(self):
        result = run_harness(staged_recovery_harness(replacement=True, changed_staging=True), timeout=90)
        self.assertNotIn('error', result, result)
        self.assertFalse(result['installed'], result)
        self.assertEqual(result['cancels'], 0, result)
        self.assertEqual(result['stagedId'], 'deadbeef', result)

    def test_ready_prior_package_can_be_explicitly_replaced(self):
        result = run_harness(staged_recovery_harness(replacement=True, staged_ready=True), timeout=90)
        self.check_completed(result, replaced=True)
        self.assertEqual(result['cmds'].count('ota cancel'), 1, result)

    def test_unowned_matching_package_can_be_explicitly_replaced(self):
        result = run_harness(staged_recovery_harness(missing_journal=True), timeout=90)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['installed'], result)
        self.assertEqual(result['oldId'], result['selectedId'], result)
        self.assertEqual(result['recoveryAction'], 'Replace and install update', result)
        self.assertEqual(result['cancels'], 1, result)
        self.assertEqual(result['cmds'].count('ota cancel'), 1, result)
        self.assertEqual(result['journal']['phase'], 'installed', result)


if __name__ == '__main__':
    unittest.main()
