#!/usr/bin/env python3
"""Exercise stock-Companion investigation through the actual browser UI."""
import os
import unittest

from test_browser_lora_ota_runtime import BROWSER
from test_browser_lora_ota_nrf_runtime import run_harness
from test_browser_lora_ota_companion_runtime import investigation_harness


def legacy_harness(fault=''):
    harness = investigation_harness()
    if fault in ('prelogin', 'mutating'):
        marker = "window.addEventListener('load',async()=>{try{"
        assert harness.count(marker) == 1
        capture = "const Transport=LoraOtaCompanion.CompanionTransport;LoraOtaCompanion.CompanionTransport=class extends Transport{constructor(port,options){super(port,options);window.__testTransport=this;}};"
        harness = harness.replace(marker, marker + capture, 1)
    old = "if(text==='get ota.direct')value=`> ota.direct v=2 active=${guard?1:0} busy=0`;"
    assert harness.count(old) == 1
    harness = harness.replace(old, "if(text==='get ota.direct')value='Unknown command';", 1)
    harness = harness.replace("let expected,pkg,mid=''", "let inbox=[],retryCounts={},firstProofTag='',expected,pkg,mid=''", 1)
    harness = harness.replace("if(!guard||dec.decode(f.slice(33))!=='password')", "if(dec.decode(f.slice(33))!=='password')", 1)
    login_reply = "Uint8Array.of(0x85,1,...unhex(key).slice(0,6),0,0,0,0,3,1)"
    assert harness.count(login_reply) == 1
    if fault in ('guest', 'guest_denied'):
        harness = harness.replace(login_reply, "Uint8Array.of(0x85,0,...unhex(key).slice(0,6),0,0,0,0,0,1)", 1)
    elif fault == 'legacy8':
        harness = harness.replace(login_reply, "Uint8Array.of(0x85,0,...unhex(key).slice(0,6))", 1)
    elif fault == 'login_failure':
        harness = harness.replace(login_reply, "Uint8Array.of(0x86,0,...unhex(key).slice(0,6))", 1)
    harness = harness.replace("if(f[1]!==1||!guard||hex(f.slice(7,13))", "if(f[1]!==1||hex(f.slice(7,13))", 1)
    harness = harness.replace("const command=dec.decode(f.slice(13));if(command.slice(0,3)!==corr+'|')throw Error('bad correlation');const text=command.slice(3);cmds.push(text);", "const command=dec.decode(f.slice(13)),tag=command.slice(0,3);if(tag[2]!=='|')throw Error('missing correlation');const text=command.slice(3);cmds.push(text);", 1)
    if fault == 'unsupported_proof':
        harness = harness.replace("if(text==='get public.key')reply='> '+key;", "if(text==='get public.key')reply='Error: unknown setting: public.key';", 1)
    elif fault == 'guest_denied':
        harness = harness.replace("if(text==='get public.key')reply='> '+key;", "if(text==='get public.key')reply='Error: permission denied';", 1)
    old_end = "else throw Error('unexpected remote '+text);out=Uint8Array.of(6,0,0,0,0,0,0,0,0,0);"
    assert harness.count(old_end) == 1
    new_end = r'''else throw Error('unexpected remote '+text);
        const message=(sender,typ,txt)=>Uint8Array.of(16,0,0,0,...unhex(sender).slice(0,6),255,typ,...f.slice(3,7),...enc.encode(txt));
        if(text==='get public.key') {
          inbox.push(message('cc'.repeat(32),0,'unrelated-message'));
          inbox.push(message(key,1,(tag==='zz|'?'yy|':'zz|')+'stale-reply'));
          inbox.push(message('cc'.repeat(32),1,tag+'> forged'));
          inbox.push(Uint8Array.of(35,1,2,3,4)); // Future queue response type.
        }
        retryCounts[text]=(retryCounts[text]||0)+1;
        if(LEGACY_FAULT==='proof_retry' && text==='get public.key'){
          if(retryCounts[text]===1)firstProofTag=tag;
          if(retryCounts[text]===3)inbox.push(message(key,1,firstProofTag+'> '+'dd'.repeat(32)));
        }
        if(!(LEGACY_FAULT==='proof_retry'&&text==='get public.key'&&retryCounts[text]<3)
          && !(LEGACY_FAULT==='proof_exhausted'&&text==='get public.key')
          && !(LEGACY_FAULT==='timeout'&&text==='get bootloader.ver')
          && !(LEGACY_FAULT==='later_retry'&&text==='ota status'&&retryCounts[text]<3))
          inbox.push(message(key,1,tag+(LEGACY_FAULT==='identity'&&text==='get public.key'?'> '+'dd'.repeat(32):reply)));
        out=Uint8Array.of(6,0,0,0,0,0,0,0,0,0);'''.replace('LEGACY_FAULT', repr(fault))
    if fault in ('timeout', 'proof_retry', 'proof_exhausted', 'later_retry'):
        marker = "window.addEventListener('load',async()=>{try{"
        assert harness.count(marker) == 1
        short = "const Transport=LoraOtaCompanion.CompanionTransport;LoraOtaCompanion.CompanionTransport=class extends Transport{constructor(port,options){super(port,{...options,remoteTimeout:300,investigationTimeout:300,commandTimeout:1500});}};"
        harness = harness.replace(marker, marker + short, 1)
    harness = harness.replace(old_end, new_end, 1)
    marker = "} else if(f[0]===66){const text=dec.decode(f.slice(1));let value;"
    assert harness.count(marker) == 1
    harness = harness.replace(marker, "} else if(f[0]===10){out=inbox.length?inbox.shift():Uint8Array.of(10);"+marker, 1)
    click = "if(el('investigate').disabled)throw Error('investigate button unavailable');el('investigate').click();const event=await done;"
    assert harness.count(click) == 1
    action = r'''if(el('investigate').disabled)throw Error('investigate button unavailable');el('investigate').click();
    if(el('legacy-warning').hidden||port.frames.some(f=>[2,10,26].includes(f[0])))throw Error('legacy investigation started without consent');
    el('legacy-investigate').click();const event=await done;'''
    if fault in ('reserve_failure', 'save_failure', 'save_failure_export'):
        predicate = "rows.some(r=>r.pending)" if fault == 'reserve_failure' else "rows.some(r=>r.frameHex)"
        sabotage = "const originalSet=Storage.prototype.setItem;Storage.prototype.setItem=function(k,v){if(k==='meshcore-lora-ota-preserved-inbox-v1'){const rows=JSON.parse(v);if("+predicate+")throw new DOMException('Storage full','QuotaExceededError');}return originalSet.call(this,k,v);};"
        action = action.replace("el('legacy-investigate').click();", sabotage + "el('legacy-investigate').click();", 1)
    elif fault == 'locked':
        held = "let releaseLock;await new Promise(resolve=>navigator.locks.request('meshcore-lora-ota-preserved-inbox-v1',()=>{resolve();return new Promise(r=>releaseLock=r);}));"
        action = action.replace("el('legacy-investigate').click();", held + "el('legacy-investigate').click();", 1)
        action = action.replace("const event=await done;", "const event=await done;releaseLock();", 1)
    elif fault == 'prelogin':
        attempt = r'''const transport=window.__testTransport;transport.selectTarget(key);await transport.enableStandardInvestigation();
    for(const cmd of ['get public.key','ota status']){let rejected=false;try{await transport.standardReply(cmd);}catch(_){rejected=true;}if(!rejected)throw Error('unauthenticated command was accepted');}
    await report({prelogin:true,frames:port.frames});return;'''
        action = action.replace("el('legacy-investigate').click();const event=await done;", attempt, 1)
    if fault == 'package':
        package_check = r'''const chosen=new DataTransfer();chosen.items.add(new File([expected],'firmware.bin'));el('file').files=chosen.files;el('file').dispatchEvent(new Event('change',{bubbles:true}));
    el('inspect').click();await wait(()=>!el('inspect').disabled);if(!el('transfer').disabled||!el('resume').disabled||!el('install').disabled)throw Error('standard companion enabled OTA transfer');'''
        action = action.replace("const event=await done;", "const event=await done;" + package_check, 1)
    elif fault == 'mutating':
        mutation_check = r'''const before=port.frames.length;let rejected=false;try{await window.__testTransport.standardReply('set tx 9');}catch(_){rejected=true;}if(!rejected||port.frames.length!==before)throw Error('mutating command was accepted');'''
        action = action.replace("const event=await done;", "const event=await done;" + mutation_check, 1)
    elif fault == 'save_failure_export':
        export_check = r'''if(!el('legacy-inbox-clear').disabled)throw Error('clear enabled before export');let exported;
    URL.createObjectURL=blob=>{exported=blob;return 'blob:test';};HTMLAnchorElement.prototype.click=function(){};window.confirm=()=>true;
    el('legacy-inbox-export').click();if(!exported||el('legacy-inbox-clear').disabled)throw Error('export failed to enable clear');window.__testExported=await exported.text();
    el('legacy-inbox-clear').click();await wait(()=>localStorage.getItem('meshcore-lora-ota-preserved-inbox-v1')===null);'''
        action = action.replace("const event=await done;", "const event=await done;" + export_check, 1)
    harness = harness.replace(click, action, 1)
    harness = harness.replace("result:el('investigation').textContent,status:", "exported:window.__testExported||'',packageStatus:el('firmware-catalog-status').textContent,verified:event.detail.verified,searchLocked:el('contact-search').disabled,buttonsDisabled:[...el('contact').querySelectorAll('button')].every(b=>b.disabled),preserved:JSON.parse(localStorage.getItem('meshcore-lora-ota-preserved-inbox-v1')||'[]'),result:el('investigation').textContent,status:", 1)
    return harness


class LegacyInvestigationBrowserTest(unittest.TestCase):
    def setUp(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1': self.fail('Chromium is required')
            self.skipTest('Chromium unavailable')

    def test_consent_and_read_only_investigation_preserve_other_messages(self):
        result = run_harness(legacy_harness(), timeout=90)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['complete'], result)
        self.assertTrue(result['verified'], result)
        self.assertIn('ota status', result['cmds'])
        self.assertIn('ota self', result['cmds'])
        self.assertIn('ver', result['cmds'])
        self.assertTrue(result['searchLocked'], result)
        self.assertTrue(result['buttonsDisabled'], result)
        saved = [r.get('frameHex', '') for r in result['preserved']]
        self.assertTrue(any('756e72656c617465642d6d657373616765' in r for r in saved), result)
        self.assertTrue(any('7374616c652d7265706c79' in r for r in saved), result)
        self.assertIn('2301020304', saved, result)
        self.assertFalse(any(f[0] in (52, 65) for f in result['frames']), result)
        self.assertFalse(any(c.startswith(('ota pull', 'ota installhash ')) for c in result['cmds']), result)

    def test_empty_password_uses_existing_access_without_login(self):
        harness=legacy_harness()
        self.assertEqual(harness.count("el('password').value='password';"),1)
        result=run_harness(harness.replace("el('password').value='password';","el('password').value='';",1),timeout=90)
        self.assertNotIn('error',result,result)
        self.assertTrue(result['complete'],result)
        self.assertTrue(result['verified'],result)
        self.assertIn('get public.key',result['cmds'],result)
        self.assertIn('ota status',result['cmds'],result)
        self.assertFalse(any(f[0] in (26,52,65) for f in result['frames']),result)
        self.assertFalse(any(c.startswith(('ota pull','ota installhash ')) for c in result['cmds']),result)

    def test_empty_password_without_authorized_full_key_never_sends_ota(self):
        harness=legacy_harness('identity')
        self.assertEqual(harness.count("el('password').value='password';"),1)
        result=run_harness(harness.replace("el('password').value='password';","el('password').value='';",1),timeout=90)
        self.assertNotIn('error',result,result)
        self.assertFalse(result['complete'],result)
        self.assertNotIn('ota status',result['cmds'],result)
        self.assertFalse(any(f[0] in (26,52,65) for f in result['frames']),result)

    def test_wrong_full_identity_is_not_accepted(self):
        result = run_harness(legacy_harness('identity'), timeout=90)
        self.assertNotIn('error', result, result)
        self.assertFalse(result['complete'], result)
        self.assertEqual(result['cmds'].count('get public.key'), 1, result)
        self.assertNotIn('ota status', result['cmds'], result)
        self.assertFalse(any(f[0] in (52, 65) for f in result['frames']), result)

    def test_matching_six_byte_contact_prefix_is_not_trusted(self):
        # Replace an ineligible (Companion) contact instead, preserving the
        # initial eligible contact order asserted by the shared UI harness.
        other = "contact('44'.repeat(32),1,1,0,'Other companion')"
        replacement = "contact('bb'.repeat(31)+'cc',1,1,0,'Other companion')"
        harness = legacy_harness()
        self.assertEqual(harness.count(other), 1)
        result = run_harness(harness.replace(other, replacement, 1), timeout=90)
        self.assertNotIn('error', result, result)
        self.assertFalse(result['complete'], result)
        self.assertFalse(any(f[0] in (2, 10, 26, 52, 65) for f in result['frames']), result)

    def test_storage_reservation_failure_never_dequeues(self):
        result = run_harness(legacy_harness('reserve_failure'), timeout=90)
        self.assertNotIn('error', result, result)
        self.assertFalse(result['complete'], result)
        self.assertFalse(any(f[0] == 10 for f in result['frames']), result)
        self.assertRegex(result['result'], r'(?i)storage|save')

    def test_storage_failure_after_dequeue_stops_further_polls(self):
        result = run_harness(legacy_harness('save_failure'), timeout=90)
        self.assertNotIn('error', result, result)
        self.assertFalse(result['complete'], result)
        self.assertEqual(sum(f[0] == 10 for f in result['frames']), 1, result)
        self.assertRegex(result['result'], r'(?i)save|storage')

    def test_another_tab_holding_inbox_lock_prevents_polling(self):
        result = run_harness(legacy_harness('locked'), timeout=90)
        self.assertNotIn('error', result, result)
        self.assertFalse(result['complete'], result)
        self.assertFalse(any(f[0] == 10 for f in result['frames']), result)
        self.assertRegex(result['result'], r'(?i)another tab|in use')

    def test_guest_and_legacy_login_are_not_reported_as_admin(self):
        for fault, label in (('guest', 'guest'), ('legacy8', 'unknown')):
            with self.subTest(fault=fault):
                result = run_harness(legacy_harness(fault), timeout=90)
                self.assertNotIn('error', result, result)
                self.assertTrue(result['complete'], result)
                self.assertIn('Account access: ' + label, result['result'])
                self.assertFalse(any(f[0] in (52, 65) for f in result['frames']), result)

    def test_failed_login_never_sends_commands_or_drains_inbox(self):
        result = run_harness(legacy_harness('login_failure'), timeout=90)
        self.assertNotIn('error', result, result)
        self.assertFalse(result['complete'], result)
        self.assertFalse(any(f[0] in (2, 10, 52, 65) for f in result['frames']), result)

    def test_unsupported_public_key_only_allows_unverified_diagnostics(self):
        result = run_harness(legacy_harness('unsupported_proof'), timeout=90)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['complete'], result)
        self.assertFalse(result['verified'], result)
        self.assertEqual(result['cmds'].count('get public.key'), 1, result)
        self.assertIn('ota status', result['cmds'], result)
        self.assertRegex(result['result'], r'(?i)not fully verified|unverified')
        self.assertFalse(any(f[0] in (52, 65) for f in result['frames']), result)

    def test_guest_denied_public_key_only_allows_unverified_diagnostics(self):
        result = run_harness(legacy_harness('guest_denied'), timeout=90)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['complete'], result)
        self.assertFalse(result['verified'], result)
        self.assertIn('Account access: guest', result['result'])
        self.assertIn('ota status', result['cmds'], result)
        self.assertRegex(result['result'], r'(?i)not fully verified|unverified')
        self.assertFalse(any(f[0] in (52, 65) for f in result['frames']), result)

    def test_package_check_cannot_enable_transfer_on_old_companion(self):
        result = run_harness(legacy_harness('package'), timeout=90)
        self.assertNotIn('error', result, result)
        self.assertRegex(result['packageStatus'], r'(?i)cannot send|guarded')
        self.assertFalse(any(f[0] in (52, 65) for f in result['frames']), result)

    def test_interrupted_record_requires_export_before_explicit_clear(self):
        result = run_harness(legacy_harness('save_failure_export'), timeout=90)
        self.assertNotIn('error', result, result)
        self.assertFalse(result['complete'], result)
        self.assertIn('interrupted', result['exported'])
        self.assertFalse(result['preserved'], result)

    def test_single_remote_timeout_does_not_prevent_later_checks(self):
        result = run_harness(legacy_harness('timeout'), timeout=90)
        self.assertNotIn('error', result, result)
        self.assertFalse(result['complete'], result)
        self.assertIn('get bootloader.ver', result['cmds'], result)
        self.assertEqual(result['cmds'].count('get bootloader.ver'), 3, result)
        self.assertIn('ota bootloader status', result['cmds'], result)
        self.assertIn('ota config', result['cmds'], result)
        self.assertRegex(result['result'], r'(?i)bootloader version.*no reply after 3 attempts')
        self.assertFalse(any(f[0] in (52, 65) for f in result['frames']), result)

    def test_identity_timeout_retries_with_fresh_tags_and_ignores_stale_reply(self):
        harness = legacy_harness('proof_retry')
        harness = harness.replace("el('password').value='password';", "el('password').value='';", 1)
        result = run_harness(harness, timeout=90)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['complete'], result)
        self.assertTrue(result['verified'], result)
        self.assertEqual(result['cmds'].count('get public.key'), 3, result)
        proofs = [bytes(f[13:]).decode() for f in result['frames']
                  if f[0] == 2 and bytes(f[13:]).decode().endswith('get public.key')]
        self.assertEqual(len({proof[:3] for proof in proofs}), 3, result)
        # The previous attempt's response contains a wrong key. It must be
        # preserved rather than accepted as the third attempt's response.
        self.assertTrue(any('646464646464' in r.get('frameHex', '')
                            for r in result['preserved']), result)
        self.assertIn('ota status', result['cmds'], result)
        self.assertFalse(any(f[0] in (26, 52, 65) for f in result['frames']), result)

    def test_later_read_only_timeout_retries_then_continues_on_success(self):
        result = run_harness(legacy_harness('later_retry'), timeout=90)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['complete'], result)
        self.assertEqual(result['cmds'].count('ota status'), 3, result)
        self.assertEqual(result['cmds'].count('ota self'), 1, result)
        self.assertIn('attempt 2 of 3', result['result'])
        self.assertIn('attempt 3 of 3', result['result'])

    def test_identity_timeout_stops_after_three_attempts(self):
        result = run_harness(legacy_harness('proof_exhausted'), timeout=90)
        self.assertNotIn('error', result, result)
        self.assertFalse(result['complete'], result)
        self.assertFalse(result['verified'], result)
        self.assertEqual(result['cmds'].count('get public.key'), 3, result)
        self.assertNotIn('ota status', result['cmds'], result)
        self.assertFalse(any(f[0] in (52, 65) for f in result['frames']), result)

    def test_standard_commands_are_blocked_before_login(self):
        result = run_harness(legacy_harness('prelogin'), timeout=90)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['prelogin'], result)
        self.assertFalse(any(f[0] in (2, 10, 26, 52, 65) for f in result['frames']), result)

    def test_mutating_standard_commands_are_blocked_after_login(self):
        result = run_harness(legacy_harness('mutating'), timeout=90)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['complete'], result)
        self.assertFalse(any(c.startswith('set ') for c in result['cmds']), result)
        self.assertFalse(any(f[0] in (52, 65) for f in result['frames']), result)


if __name__ == '__main__': unittest.main()
