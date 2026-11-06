#!/usr/bin/env python3
"""Exercise investigation progress and retry through the real browser UI."""
import os
import unittest

from test_browser_lora_ota_runtime import BROWSER
from test_browser_lora_ota_nrf_runtime import run_harness
from test_browser_lora_ota_legacy_investigation_runtime import legacy_harness


LOGIN_PUSH = "queueMicrotask(()=>this.push(wire(Uint8Array.of(0x85,1,...unhex(key).slice(0,6),0,0,0,0,3,1))));"
PROOF_PUSH = "inbox.push(message(key,1,tag+(''==='identity'&&text==='get public.key'?'> '+'dd'.repeat(32):reply)));"
CONTINUE = "el('legacy-investigate').click();const event=await done;"


class InvestigationProgressBrowserTest(unittest.TestCase):
    def setUp(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1':
                self.fail('Chromium is required')
            self.skipTest('Chromium unavailable')

    def test_progress_changes_while_login_and_identity_reply_are_delayed(self):
        harness = legacy_harness()
        for snippet in (LOGIN_PUSH, PROOF_PUSH, CONTINUE):
            self.assertEqual(harness.count(snippet), 1, snippet)
        harness = harness.replace("el('password').value='password';", "el('password').value='S3cr3t!';", 1)
        harness = harness.replace("['','password'].includes(dec.decode(f.slice(33)))", "['','S3cr3t!'].includes(dec.decode(f.slice(33)))", 1)
        harness = harness.replace(LOGIN_PUSH,
            "window.__releaseLogin=()=>this.push(wire(Uint8Array.of(0x85,1,...unhex(key).slice(0,6),0,0,0,0,3,1)));", 1)
        harness = harness.replace(PROOF_PUSH,
            "if(text==='get public.key')window.__releaseProof=()=>inbox.push(message(key,1,tag+reply));else if(text==='ota status')window.__releaseStatus=()=>inbox.push(message(key,1,tag+reply));else inbox.push(message(key,1,tag+reply));", 1)
        action = r'''el('legacy-investigate').click();
    const progress=()=>el('investigation-progress').textContent;
    await wait(()=>window.__releaseLogin && /log.?in|logging in/i.test(progress()));
    const login=progress();await new Promise(r=>setTimeout(r,1200));const later=progress();
    if(login===later)throw Error('elapsed progress did not change while waiting for login');
    window.__releaseLogin();
    await wait(()=>window.__releaseProof && /get public\.key/i.test(progress()));const identity=progress();
    const proofFrame=port.frames.find(f=>f[0]===2),tag=dec.decode(Uint8Array.from(proofFrame.slice(13))).slice(0,3);
    if(identity.includes(tag)||identity.includes(el('password').value))throw Error('secret or protocol tag visible in progress');
    window.__releaseProof();await wait(()=>window.__releaseStatus && /ota status/i.test(progress()));const statusProgress=progress();
    const statusFrame=port.frames.filter(f=>f[0]===2).at(-1),statusTag=dec.decode(Uint8Array.from(statusFrame.slice(13))).slice(0,3);
    if(statusProgress.includes(statusTag)||statusProgress.includes(el('password').value))throw Error('secret or protocol tag visible in command progress');
    window.__releaseStatus();const event=await done;const history=el('investigation').textContent;
    if(!history.includes('> get public.key')||!history.includes('> ota status'))throw Error('commands missing from history');
    if(history.includes(tag)||history.includes(statusTag)||history.includes(el('password').value))throw Error('secret or protocol tag visible in history');
    await report({login,later,identity,statusProgress,complete:event.detail.complete,verified:event.detail.verified,result:history,cmds,frames:port.frames});return;'''
        harness = harness.replace(CONTINUE, action, 1)
        result = run_harness(harness, timeout=60)
        self.assertNotIn('error', result, result)
        self.assertRegex(result['login'], r'(?i)log.?in|logging in')
        self.assertRegex(result['identity'], r'(?i)get public\.key')
        self.assertIn('attempt 1 of 3', result['identity'])
        self.assertRegex(result['statusProgress'], r'(?i)ota status')
        self.assertIn('attempt 1 of 7', result['statusProgress'])
        self.assertTrue(result['complete'], result)
        self.assertTrue(result['verified'], result)
        self.assertIn('ota status', result['cmds'])
        self.assertFalse(any(f[0] in (52, 65) for f in result['frames']), result)

    def test_login_timeout_is_actionable_and_can_be_retried(self):
        harness = legacy_harness()
        marker = "window.addEventListener('load',async()=>{try{"
        self.assertEqual(harness.count(marker), 1)
        self.assertEqual(harness.count(LOGIN_PUSH), 1)
        self.assertEqual(harness.count(CONTINUE), 1)
        harness = harness.replace(marker, marker + "const Transport=LoraOtaCompanion.CompanionTransport;LoraOtaCompanion.CompanionTransport=class extends Transport{constructor(port,options){super(port,{...options,remoteTimeout:1200,investigationTimeout:1200,commandTimeout:1500});}};", 1)
        harness = harness.replace(LOGIN_PUSH, "if((window.__loginAttempts=(window.__loginAttempts||0)+1)>1)queueMicrotask(()=>this.push(wire(Uint8Array.of(0x85,1,...unhex(key).slice(0,6),0,0,0,0,3,1))));", 1)
        action = r'''el('legacy-investigate').click();const first=await done;
    if(first.detail.complete)throw Error('missing login incorrectly completed');
    const failure=el('investigation').textContent+' '+el('investigation-progress').textContent;
    if(!/timed? out|timeout/i.test(failure))throw Error('timeout not shown: '+failure);
    if(!/try|retry|check|connect/i.test(failure))throw Error('timeout has no next step: '+failure);
    const again=new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('retry did not finish')),45000);document.querySelector('[data-lora-ota]').addEventListener('lora-ota-investigated',e=>{clearTimeout(timer);resolve(e);},{once:true});});
    if(el('investigate').disabled)throw Error('investigate button stayed disabled after timeout');
    el('investigate').click();if(el('legacy-warning').hidden)throw Error('consent missing on retry');
    el('legacy-investigate').click();const second=await again;
    await report({failure,complete:second.detail.complete,verified:second.detail.verified,cmds,frames:port.frames});return;'''
        harness = harness.replace(CONTINUE, action, 1)
        result = run_harness(harness, timeout=60)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['complete'], result)
        self.assertTrue(result['verified'], result)
        self.assertGreaterEqual(sum(f[0] == 26 for f in result['frames']), 2, result)
        self.assertFalse(any(f[0] in (52, 65) for f in result['frames']), result)

    def test_stop_saves_a_message_already_removed_from_the_companion(self):
        harness = legacy_harness()
        dequeue = "} else if(f[0]===10){out=inbox.length?inbox.shift():Uint8Array.of(10);} else if(f[0]===66)"
        self.assertEqual(harness.count(dequeue), 1)
        self.assertEqual(harness.count(CONTINUE), 1)
        delayed = "} else if(f[0]===10){const held=inbox.length?inbox.shift():Uint8Array.of(10);window.__releaseDequeued=()=>this.push(wire(held));return;} else if(f[0]===66)"
        harness = harness.replace(dequeue, delayed, 1)
        action = r'''el('legacy-investigate').click();let finished=false;done.then(()=>{finished=true;});
    await wait(()=>window.__releaseDequeued);
    if(el('investigation-stop').hidden||el('investigation-stop').disabled)throw Error('stop unavailable');
    el('investigation-stop').click();await new Promise(r=>setTimeout(r,200));
    if(finished)throw Error('stop finished before the dequeued message was saved');
    if(!/stopp/i.test(el('investigation-progress').textContent))throw Error('safe stop progress missing');
    window.__releaseDequeued();const event=await done;
    const saved=JSON.parse(localStorage.getItem('meshcore-lora-ota-preserved-inbox-v1')||'[]');
    await report({complete:event.detail.complete,result:el('investigation').textContent,saved,frames:port.frames});return;'''
        harness = harness.replace(CONTINUE, action, 1)
        result = run_harness(harness, timeout=60)
        self.assertNotIn('error', result, result)
        self.assertFalse(result['complete'], result)
        self.assertTrue(any('756e72656c617465642d6d657373616765' in r.get('frameHex', '') for r in result['saved']), result)
        self.assertEqual(sum(f[0] == 10 for f in result['frames']), 1, result)
        self.assertFalse(any(f[0] in (52, 65) for f in result['frames']), result)

    def test_stop_interrupts_login_and_requires_reconnect(self):
        harness = legacy_harness()
        self.assertEqual(harness.count(LOGIN_PUSH), 1)
        self.assertEqual(harness.count(CONTINUE), 1)
        harness = harness.replace(LOGIN_PUSH,
            "window.__releaseLogin=()=>this.push(wire(Uint8Array.of(0x85,1,...unhex(key).slice(0,6),0,0,0,0,3,1)));", 1)
        action = r'''el('legacy-investigate').click();
    await wait(()=>window.__releaseLogin && /log.?in|logging in/i.test(el('investigation-progress').textContent));
    el('investigation-stop').click();const event=await Promise.race([done,new Promise((_,reject)=>setTimeout(()=>reject(Error('login stop did not finish')),3000))]);
    const result=el('investigation').textContent+' '+el('investigation-progress').textContent;
    if(!/disconnect|reconnect/i.test(result))throw Error('missing reconnection guidance: '+result);
    await report({complete:event.detail.complete,result,frames:port.frames});return;'''
        harness = harness.replace(CONTINUE, action, 1)
        result = run_harness(harness, timeout=30)
        self.assertNotIn('error', result, result)
        self.assertFalse(result['complete'], result)
        self.assertTrue(any(f[0] == 26 for f in result['frames']), result)
        self.assertFalse(any(f[0] in (2, 10, 52, 65) for f in result['frames']), result)


if __name__ == '__main__':
    unittest.main()
