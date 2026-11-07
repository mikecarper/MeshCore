#!/usr/bin/env python3
"""Exercise stock-Companion investigation through the actual browser UI."""
import os
import json
import unittest

from test_browser_lora_ota_runtime import BROWSER
from test_browser_lora_ota_nrf_runtime import run_harness
from test_browser_lora_ota_companion_runtime import investigation_harness


INVESTIGATE_ACTION = "el('investigate').click();const event=await done;"


def legacy_harness(fault=''):
    harness = investigation_harness()
    if fault == 'clock_behind':
        original = 'clock=Math.floor(Date.now()/1000)'
        assert harness.count(original) == 1
        harness = harness.replace(original, 'clock=Math.floor(Date.now()/1000)-3600', 1)
    if fault in ('prelogin', 'mutating'):
        marker = "window.addEventListener('load',async()=>{try{"
        assert harness.count(marker) == 1
        capture = "const Transport=LoraOtaCompanion.CompanionTransport;LoraOtaCompanion.CompanionTransport=class extends Transport{constructor(port,options){super(port,options);window.__testTransport=this;}};"
        harness = harness.replace(marker, marker + capture, 1)
    old = "if(text==='get ota.direct')value=`> ota.direct v=2 active=${guard?1:0} busy=0`;"
    assert harness.count(old) == 1
    harness = harness.replace(old, "if(text==='get ota.direct')value='Unknown command';", 1)
    harness = harness.replace("let expected,pkg,mid=''", "let inbox=[],retryCounts={},firstProofTag='',expected,pkg,mid=''", 1)
    harness = harness.replace("if(!guard||!['','password'].includes(dec.decode(f.slice(33))))", "if(!['','password'].includes(dec.decode(f.slice(33))))", 1)
    login_reply = "Uint8Array.of(0x85,1,...unhex(key).slice(0,6),0,0,0,0,3,1)"
    assert harness.count(login_reply) == 1
    if fault in ('guest', 'guest_denied'):
        harness = harness.replace(login_reply, "Uint8Array.of(0x85,0,...unhex(key).slice(0,6),0,0,0,0,0,1)", 1)
    elif fault == 'legacy8':
        harness = harness.replace(login_reply, "Uint8Array.of(0x85,0,...unhex(key).slice(0,6))", 1)
    elif fault == 'login_failure':
        harness = harness.replace(login_reply, "Uint8Array.of(0x86,0,...unhex(key).slice(0,6))", 1)
    elif fault == 'empty_login_failed':
        harness = harness.replace(login_reply, "Uint8Array.of(0x86,0,...unhex(key).slice(0,6))", 1)
    elif fault == 'empty_login_timeout':
        login_push = "queueMicrotask(()=>this.push(wire("+login_reply+")));"
        assert harness.count(login_push) == 1
        harness = harness.replace(login_push, "/* no login response */", 1)
    harness = harness.replace("if(f[1]!==1||!guard||hex(f.slice(7,13))", "if(f[1]!==1||hex(f.slice(7,13))", 1)
    harness = harness.replace("const command=dec.decode(f.slice(13));if(command.slice(0,3)!==corr+'|')throw Error('bad correlation');const text=command.slice(3);cmds.push(text);", "const command=dec.decode(f.slice(13)),tag=command.slice(0,3);if(tag[2]!=='|')throw Error('missing correlation');const text=command.slice(3);cmds.push(text);", 1)
    if fault == 'unsupported_proof':
        harness = harness.replace("if(text==='get public.key')reply='> '+key;", "if(text==='get public.key')reply='Error: unknown setting: public.key';", 1)
    elif fault == 'guest_denied':
        harness = harness.replace("if(text==='get public.key')reply='> '+key;", "if(text==='get public.key')reply='Error: permission denied';", 1)
    elif fault in ('full_transfer', 'no_cli_transfer'):
        harness = harness.replace("else if(text==='ota installhash')reply='ERR usage: ota installhash <64-hex-container-sha256>';", "else if(text==='ota installhash')reply='Unknown OTA command';", 1)
        harness = harness.replace("else if(/^ota installhash [0-9a-f]{64}$/.test(text))", "else if(text==='ota install')", 1)
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
          && !(LEGACY_FAULT==='later_retry'&&text==='ota status'&&retryCounts[text]<11))
          inbox.push(message(key,1,tag+(LEGACY_FAULT==='identity'&&text==='get public.key'?'> '+'dd'.repeat(32):reply)));
        out=Uint8Array.of(6,0,0,0,0,0,0,0,0,0);'''.replace('LEGACY_FAULT', repr(fault))
    if fault in ('timeout', 'proof_retry', 'proof_exhausted', 'later_retry', 'empty_login_timeout'):
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
    before_action = r'''if(el('investigate').disabled)throw Error('investigate button unavailable');
    const beforeActionFrames=port.frames.map(f=>f.slice());
    if(beforeActionFrames.some(f=>[2,10,26,52,65].includes(f[0]))||cmds.length)throw Error('investigation sent RF, login or dequeued before the user action');
    if(el('legacy-investigate'))throw Error('a second investigation action is still present');
    if(el('legacy-warning').hidden)throw Error('older Companion queue information was not visible before investigation');
    let investigateClicks=0;el('investigate').addEventListener('click',()=>{investigateClicks++;});
    '''
    action = before_action + INVESTIGATE_ACTION + r'''
    if(investigateClicks!==1)throw Error('investigation required more than one primary click');
    if(el('legacy-warning').hidden)throw Error('older Companion queue information was not shown');'''
    if fault in ('reserve_failure', 'save_failure', 'save_failure_export'):
        predicate = "rows.some(r=>r.pending)" if fault == 'reserve_failure' else "rows.some(r=>r.frameHex)"
        sabotage = "const originalSet=Storage.prototype.setItem;Storage.prototype.setItem=function(k,v){if(k==='meshcore-lora-ota-preserved-inbox-v1'){const rows=JSON.parse(v);if("+predicate+")throw new DOMException('Storage full','QuotaExceededError');}return originalSet.call(this,k,v);};"
        action = action.replace(INVESTIGATE_ACTION, sabotage + INVESTIGATE_ACTION, 1)
    elif fault == 'locked':
        held = "let releaseLock;await new Promise(resolve=>navigator.locks.request('meshcore-lora-ota-preserved-inbox-v1',()=>{resolve();return new Promise(r=>releaseLock=r);}));"
        action = action.replace(INVESTIGATE_ACTION, held + INVESTIGATE_ACTION, 1)
        action = action.replace("const event=await done;", "const event=await done;releaseLock();", 1)
    elif fault == 'prelogin':
        attempt = r'''const transport=window.__testTransport;transport.selectTarget(key);await transport.enableStandardInvestigation();
    for(const cmd of ['get public.key','ota status']){let rejected=false;try{await transport.standardReply(cmd);}catch(_){rejected=true;}if(!rejected)throw Error('unauthenticated command was accepted');}
    await report({prelogin:true,investigateClicks,beforeActionFrames,frames:port.frames});return;'''
        action = before_action + attempt
    if fault in ('package', 'full_transfer', 'no_cli_transfer'):
        package_check = r'''const chosen=new DataTransfer();chosen.items.add(new File([expected],'firmware.bin'));el('file').files=chosen.files;el('file').dispatchEvent(new Event('change',{bubbles:true}));
    el('inspect').click();await wait(()=>!el('inspect').disabled);if(el('transfer').disabled||!el('resume').disabled||!el('install').disabled)throw Error('validated legacy OTA preflight did not enable transfer');'''
        if fault in ('full_transfer', 'no_cli_transfer'):
            package_check += r'''
    el('transfer').click();await wait(()=>!el('install').disabled||el('status').textContent.includes('Error:'));
    if(el('status').textContent.includes('Error:'))throw Error(el('status').textContent);
    el('install').click();await wait(()=>el('status').textContent.includes('Installed and verified:')||el('status').textContent.includes('Error:'));
    if(el('status').textContent.includes('Error:'))throw Error(el('status').textContent);'''
        action = action.replace("const event=await done;", "const event=await done;" + package_check, 1)
        after_investigation = "if(el('investigation').hidden||!el('transfer').disabled||!el('install').disabled)throw Error('investigation did not show results or enabled an update');"
        assert harness.count(after_investigation) == 1
        harness = harness.replace(after_investigation, "if(el('investigation').hidden)throw Error('investigation did not show results');", 1)
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
    harness = harness.replace("result:el('investigation').textContent,status:", "investigateClicks,beforeActionFrames,installed,otaData,advertSeen,sourceInfo:el('source-info').textContent,exported:window.__testExported||'',packageStatus:el('firmware-catalog-status').textContent,verified:event.detail.verified,searchLocked:el('contact-search').disabled,buttonsDisabled:[...el('contact').querySelectorAll('button')].every(b=>b.disabled),preserved:JSON.parse(localStorage.getItem('meshcore-lora-ota-preserved-inbox-v1')||'[]'),result:el('investigation').textContent,status:", 1)
    if fault in ('no_cli_transfer', 'no_cli_probe'):
        # Firmware 1.16.05 handles command 66 as a FEM query and returns RESP_OK,
        # not a local CLI response. Keep all other binary protocol behavior.
        branch = "} else if(f[0]===66){const text=dec.decode(f.slice(1));let value;"
        assert harness.count(branch) == 1
        harness = harness.replace(branch, "} else if(f[0]===66){out=Uint8Array.of(0);" + branch.replace('f[0]===66', 'f[0]===166'), 1)
        version = "out.set(enc.encode('v1.18.0.2-preview'),60)"
        assert harness.count(version) == 1
        harness = harness.replace(version, "out.set(enc.encode('v1.16.05'),60)", 1)
    return harness


def hardware_harness(hw, profiles, nrf_marker=False):
    """Exercise target classification through the real investigation UI."""
    harness = legacy_harness()
    status = "'OTA hw=board | target:12345678 | maxblk:1024 | '"
    assert harness.count(status) == 1
    replacement = "'OTA hw=" + hw + " | target:12345678 | maxblk:1024 | " + ('bl:QSPI | ' if nrf_marker else '') + "'"
    harness = harness.replace(status, replacement, 1)
    marker = "window.addEventListener('load',async()=>{try{"
    assert harness.count(marker) == 1
    controls = json.dumps({'profiles': profiles}, separators=(',', ':'))
    mock = ("const savedFetch=window.fetch.bind(window);window.fetch=(url,opts)=>"
            "String(url).includes('/_data/firmware_controls.json')?Promise.resolve(new Response("
            + json.dumps(controls) + ",{status:200,headers:{'Content-Type':'application/json'}})):savedFetch(url,opts);")
    return harness.replace(marker, marker + mock, 1)


class LegacyInvestigationBrowserTest(unittest.TestCase):
    def setUp(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1': self.fail('Chromium is required')
            self.skipTest('Chromium unavailable')

    def test_single_click_read_only_investigation_preserves_other_messages(self):
        result = run_harness(legacy_harness(), timeout=90)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['complete'], result)
        self.assertTrue(result['verified'], result)
        self.assertEqual(result['investigateClicks'], 1, result)
        self.assertFalse(any(f[0] in (2, 10, 26, 52, 65) for f in result['beforeActionFrames']), result)
        self.assertIn('ota status', result['cmds'])
        self.assertIn('ota self', result['cmds'])
        self.assertIn('ver', result['cmds'])
        self.assertNotIn('get radio', result['cmds'])
        self.assertTrue(result['searchLocked'], result)
        self.assertTrue(result['buttonsDisabled'], result)
        saved = [r.get('frameHex', '') for r in result['preserved']]
        self.assertTrue(any('756e72656c617465642d6d657373616765' in r for r in saved), result)
        self.assertTrue(any('7374616c652d7265706c79' in r for r in saved), result)
        self.assertIn('2301020304', saved, result)
        self.assertFalse(any(f[0] in (52, 65) for f in result['frames']), result)
        self.assertFalse(any(c.startswith(('ota pull', 'ota installhash ')) for c in result['cmds']), result)

    def test_esp32_skips_bootloader_checks(self):
        profiles = {
            'Station_G2_repeater_observer_mqtt-full-usb-wifi': {'platform': 'ESP32_PLATFORM'},
            'Station_G2_room_server_observer_mqtt-full-usb-wifi': {'platform': 'ESP32_PLATFORM'},
        }
        result = run_harness(hardware_harness('Station_G2', profiles), timeout=90)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['verified'], result)
        self.assertNotIn('get bootloader.ver', result['cmds'], result)
        self.assertNotIn('ota bootloader status', result['cmds'], result)
        self.assertNotIn('get radio', result['cmds'], result)
        self.assertRegex(result['result'], r'(?i)bootloader.*skipped.*ESP32')

    def test_unknown_conflicting_or_nrf_identity_keeps_bootloader_checks(self):
        esp = {'Station_G2_repeater_observer_mqtt': {'platform': 'ESP32_PLATFORM'}}
        cases = (
            ('unknown', 'unrecognized_board', {}, False),
            ('conflict', 'Station_G2', {**esp, 'Station_G2_sensor': {'platform': 'NRF52_PLATFORM'}}, False),
            ('nrf_marker', 'Station_G2', esp, True),
            ('nrf', 'RAK_4631', {'RAK_4631_repeater': {'platform': 'NRF52_PLATFORM'}}, True),
        )
        for name, hw, profiles, marker in cases:
            with self.subTest(case=name):
                result = run_harness(hardware_harness(hw, profiles, marker), timeout=90)
                self.assertNotIn('error', result, result)
                self.assertIn('get bootloader.ver', result['cmds'], result)
                self.assertIn('ota bootloader status', result['cmds'], result)
                self.assertNotIn('get radio', result['cmds'], result)

    def test_empty_password_logins_with_acl_then_proves_access(self):
        harness=legacy_harness()
        self.assertEqual(harness.count("el('password').value='password';"),1)
        result=run_harness(harness.replace("el('password').value='password';","el('password').value='';",1),timeout=90)
        self.assertNotIn('error',result,result)
        self.assertTrue(result['complete'],result)
        self.assertTrue(result['verified'],result)
        self.assertIn('get public.key',result['cmds'],result)
        self.assertIn('ota status',result['cmds'],result)
        logins=[f for f in result['frames'] if f[0]==26]
        self.assertEqual(len(logins),1,result)
        self.assertEqual(len(logins[0]),33,result)  # no password bytes
        self.assertFalse(any(f[0] in (52,65) for f in result['frames']),result)
        self.assertFalse(any(c.startswith(('ota pull','ota installhash ')) for c in result['cmds']),result)

    def test_empty_password_without_authorized_full_key_never_sends_ota(self):
        harness=legacy_harness('identity')
        self.assertEqual(harness.count("el('password').value='password';"),1)
        result=run_harness(harness.replace("el('password').value='password';","el('password').value='';",1),timeout=90)
        self.assertNotIn('error',result,result)
        self.assertFalse(result['complete'],result)
        self.assertNotIn('ota status',result['cmds'],result)
        self.assertFalse(any(f[0] in (52,65) for f in result['frames']),result)

    def test_empty_login_failure_and_timeout_fall_back_to_direct_acl_proof(self):
        for fault in ('empty_login_failed', 'empty_login_timeout'):
            with self.subTest(fault=fault):
                harness=legacy_harness(fault).replace("el('password').value='password';","el('password').value='';",1)
                result=run_harness(harness,timeout=90)
                self.assertNotIn('error',result,result)
                self.assertTrue(result['verified'],result)
                self.assertIn('get public.key',result['cmds'],result)
                self.assertIn('ota status',result['cmds'],result)
                self.assertEqual(sum(f[0]==26 for f in result['frames']),1,result)
                self.assertFalse(any(f[0] in (52,65) for f in result['frames']),result)

    def test_older_companion_clock_warning_does_not_block_investigation(self):
        result=run_harness(legacy_harness('clock_behind'),timeout=90)
        self.assertNotIn('error',result,result)
        self.assertTrue(result['verified'],result)
        self.assertIn('ota status',result['cmds'],result)
        self.assertRegex(result['result'],r'(?i)clock.*(?:behind|sync|incorrect)')
        self.assertTrue(any(f[0]==5 for f in result['frames']),result)
        self.assertFalse(any(f[0]==6 for f in result['frames']),result)

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

    def test_package_check_runs_real_preflight_on_old_companion(self):
        result = run_harness(legacy_harness('package'), timeout=90)
        self.assertNotIn('error', result, result)
        self.assertGreaterEqual(result['cmds'].count('ota status'), 2, result)
        self.assertGreaterEqual(result['cmds'].count('ota self'), 2, result)
        self.assertGreaterEqual(result['cmds'].count('get tempradio'), 2, result)
        self.assertFalse(any(f[0] in (52, 65) for f in result['frames']), result)

    def test_full_update_using_old_companion(self):
        result = run_harness(legacy_harness('full_transfer'), timeout=120)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['installed'], result)
        self.assertTrue(result['otaData'], result)
        self.assertTrue(result['advertSeen'], result)
        self.assertIn('Installed and verified:', result['status'])
        self.assertRegex(result['status'], r'(?i)older target|legacy install')
        self.assertIn('ota install', result['cmds'])
        self.assertFalse(any(c.startswith('ota installhash ') for c in result['cmds']), result)
        self.assertGreaterEqual(result['cmds'].count('get public.key'), 2, result)
        self.assertTrue(any(f[0] == 65 for f in result['frames']), result)
        self.assertFalse(any(f[0] == 52 for f in result['frames']), result)

    def test_full_update_using_companion_without_local_cli(self):
        result = run_harness(legacy_harness('no_cli_transfer'), timeout=120)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['installed'], result)
        self.assertTrue(result['otaData'], result)
        self.assertTrue(result['advertSeen'], result)
        self.assertIn('Installed and verified:', result['status'])
        self.assertRegex(result['status'], r'(?i)unverified|cannot.*(?:verif|read)')
        self.assertIn('v1.16.05', result['sourceInfo'])
        self.assertRegex(result['sourceInfo'], r'(?i)raw.packet.*compatibility')
        self.assertIn('ota install', result['cmds'])
        self.assertGreaterEqual(result['cmds'].count('get public.key'), 2, result)
        self.assertTrue(any(f[0] == 65 for f in result['frames']), result)
        self.assertFalse(any(f[0] == 52 for f in result['frames']), result)

    def test_old_companion_version_selects_raw_packet_compatibility(self):
        result = run_harness(legacy_harness('no_cli_probe'), timeout=90)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['verified'], result)
        self.assertIn('v1.16.05', result['sourceInfo'])
        self.assertRegex(result['sourceInfo'], r'(?i)raw.packet.*compatibility.*selected automatically')
        self.assertTrue(any(f[0] == 22 for f in result['frames']), result)
        self.assertFalse(any(f[0] == 52 for f in result['frames']), result)

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
        self.assertEqual(result['cmds'].count('get bootloader.ver'), 11, result)
        self.assertIn('ota bootloader status', result['cmds'], result)
        self.assertIn('ota config', result['cmds'], result)
        self.assertRegex(result['result'], r'(?i)bootloader version.*no reply after 11 attempts')
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
        self.assertFalse(any(f[0] in (52, 65) for f in result['frames']), result)

    def test_later_read_only_timeout_retries_then_continues_on_success(self):
        result = run_harness(legacy_harness('later_retry'), timeout=90)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['complete'], result)
        self.assertEqual(result['cmds'].count('ota status'), 11, result)
        self.assertEqual(result['cmds'].count('ota self'), 1, result)
        self.assertIn('attempt 2 of 11', result['result'])
        self.assertIn('attempt 11 of 11', result['result'])

    def test_identity_timeout_stops_after_seven_attempts(self):
        result = run_harness(legacy_harness('proof_exhausted'), timeout=90)
        self.assertNotIn('error', result, result)
        self.assertFalse(result['complete'], result)
        self.assertFalse(result['verified'], result)
        self.assertEqual(result['cmds'].count('get public.key'), 7, result)
        self.assertNotIn('ota status', result['cmds'], result)
        self.assertFalse(any(f[0] in (52, 65) for f in result['frames']), result)

    def test_standard_commands_are_blocked_before_login(self):
        result = run_harness(legacy_harness('prelogin'), timeout=90)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['prelogin'], result)
        self.assertEqual(result['investigateClicks'], 0, result)
        self.assertFalse(any(f[0] in (2, 10, 26, 52, 65) for f in result['frames']), result)

    def test_mutating_standard_commands_are_blocked_after_login(self):
        result = run_harness(legacy_harness('mutating'), timeout=90)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['complete'], result)
        self.assertFalse(any(c.startswith('set ') for c in result['cmds']), result)
        self.assertFalse(any(f[0] in (52, 65) for f in result['frames']), result)


if __name__ == '__main__': unittest.main()
