#!/usr/bin/env python3
"""Exercise investigation retries through the real Repeater serial protocol."""
import os
import unittest

from test_browser_lora_ota_runtime import BROWSER, HARNESS
from test_browser_lora_ota_nrf_runtime import run_harness


def repeater_harness(fault, route='direct'):
    harness = HARNESS.replace("let folder=false, mid=''", "let counts={}, jobError=false, peerTimeout=60, routePath='off', pathReads=0, folder=false, mid=''", 1)
    marker = "if(cmd==='ota folder')reply='folder='+(folder?'on':'off');"
    timeout = """if(cmd==='ota.peer timeout')reply='OTApeer timeout='+peerTimeout+'s';
      if(cmd.startsWith('ota.peer timeout ')){
        const next=Number(cmd.split(' ').pop()), fault=FAULT;
        if(fault==='restore_failure'&&next===60)reply='Error: timeout setting failed';
        else {peerTimeout=next;reply=(fault==='set_failure'&&next===7)?'Error: timeout setting failed':'OTApeer timeout='+peerTimeout+'s';}
      }
      """.replace('FAULT', repr(fault))
    path = """if(cmd==='ota.peer path'){
        pathReads++;const route=ROUTE;
        reply=route==='unknown'&&pathReads>1?'Unknown command':route==='nonzero'&&pathReads>1?'OTApeer path=A1B2C3':'OTApeer path='+routePath;
      }
      if(cmd.startsWith('ota.peer path ')){routePath=cmd.slice('ota.peer path '.length);reply='OTApeer path='+routePath;}
      """.replace('ROUTE', repr(route))
    harness = harness.replace(marker, timeout+path+marker, 1)
    harness = harness.replace("if(cmd==='ota status') return", "if(cmd==='get bootloader.ver'||cmd==='ota bootloader status'||cmd==='ota key list')return 'Unknown command';\n      if(cmd==='ota status') return", 1)
    original = "job='01020304';jobResult=cmd.startsWith('ota.peer acl ')?'OK':this.remote(cmd.slice(('ota.peer cmd.acl '+target+' ').length));"
    replacement = r'''job='01020304';
        const identity=cmd.startsWith('ota.peer acl '), remote=identity?'identity':cmd.slice(('ota.peer cmd.acl '+target+' ').length);
        counts[remote]=(counts[remote]||0)+1;
        const fault=FAULT;
        jobError=(fault==='identity_exhaust'&&identity)||(fault==='later_exhaust'&&remote==='ota status')
          ||(fault==='ambiguous'&&remote==='ota status')||(fault==='hard'&&remote==='ota status')
          ||(fault==='later_retry'&&remote==='ota status'&&counts[remote]<11);
        jobResult=jobError?(fault==='ambiguous'?'OTA request timed out; its result is unknown':fault==='hard'?'permission denied':'request timed out; result is unknown'):(identity?'OK':this.remote(remote));'''.replace('FAULT', repr(fault))
    assert harness.count(original) == 1
    harness = harness.replace(original, replacement, 1)
    harness = harness.replace('state=ok bytes=${data.length}', "state=${jobError?'error':'ok'} bytes=${data.length}", 1)
    action = "el('connect').click();await wait(()=>!el('inspect').disabled);"
    replacement = r'''el('connect').click();await wait(()=>!el('investigate').disabled);
      const progress=[];new MutationObserver(()=>progress.push(el('investigation-progress').textContent)).observe(el('investigation-progress'),{subtree:true,childList:true,characterData:true});
      const done=new Promise(resolve=>document.querySelector('[data-lora-ota]').addEventListener('lora-ota-investigated',resolve,{once:true}));
      el('investigate').click();const event=await done;
      await report({progress,result:el('investigation').textContent,status:el('status').textContent,complete:event.detail.complete,peerTimeout,counts,cmds,frames:port.frames});return;'''
    assert harness.count(action) == 1
    if route=='routed':replacement="el('acl').checked=true;el('relays').value='cc'.repeat(32);"+replacement
    return harness.replace(action, replacement, 1)


class RepeaterRetryBrowserTest(unittest.TestCase):
    def setUp(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1': self.fail('Chromium is required')
            self.skipTest('Chromium unavailable')

    def test_completed_timeout_retries_and_stops_on_reply(self):
        result = run_harness(repeater_harness('later_retry'), timeout=90)
        self.assertNotIn('error', result, result)
        self.assertEqual(result['counts']['ota status'], 11, result)
        self.assertEqual(result['counts']['ota self'], 1, result)
        self.assertIn('> Checking target identity (attempt 1 of 7)', result['result'])
        self.assertIn('> ota status (attempt 1 of 11)', result['result'])
        self.assertIn('attempt 11 of 11', result['result'])
        self.assertEqual(result['peerTimeout'], 60, result)
        self.assertIn('local ota.peer timeout 7', result['cmds'], result)
        self.assertIn('local ota.peer timeout 60', result['cmds'], result)

    def test_only_confirmed_zero_hop_uses_seven_second_wait_and_progress(self):
        for route,seconds in (('direct',7),('unknown',20),('nonzero',20),('routed',20)):
            with self.subTest(route=route):
                result=run_harness(repeater_harness('',route),timeout=90)
                self.assertNotIn('error',result,result)
                self.assertTrue(result['complete'],result)
                self.assertIn('local ota.peer timeout '+str(seconds),result['cmds'],result)
                self.assertTrue(any('reply timeout: '+str(seconds)+'s' in text for text in result['progress']),result)
                self.assertEqual(result['peerTimeout'],60,result)
                self.assertFalse(any((command=='remote ota install' or command.startswith(('remote ota pull ','remote ota installhash ')))for command in result['cmds']),result)
                if route=='routed':self.assertIn('local ota.peer path CCCCCC',result['cmds'],result)
                if seconds==20:self.assertNotIn('local ota.peer timeout 7',result['cmds'],result)

    def test_eleven_timeouts_leave_partial_results(self):
        result = run_harness(repeater_harness('later_exhaust'), timeout=90)
        self.assertNotIn('error', result, result)
        self.assertFalse(result['complete'], result)
        self.assertEqual(result['counts']['ota status'], 11, result)
        self.assertEqual(result['counts']['ota self'], 1, result)
        self.assertIn('OTA status: No reply after 11 attempts', result['result'])

    def test_identity_timeout_stops_after_seven_attempts(self):
        result = run_harness(repeater_harness('identity_exhaust'), timeout=90)
        self.assertNotIn('error', result, result)
        self.assertFalse(result['complete'], result)
        self.assertEqual(result['counts']['identity'], 7, result)
        self.assertNotIn('ota status', result['counts'], result)
        self.assertEqual(result['peerTimeout'], 60, result)
        self.assertIn('local ota.peer timeout 60', result['cmds'], result)

    def test_ambiguous_or_permission_errors_are_not_retried(self):
        for fault in ('ambiguous', 'hard'):
            with self.subTest(fault=fault):
                result = run_harness(repeater_harness(fault), timeout=90)
                self.assertNotIn('error', result, result)
                self.assertFalse(result['complete'], result)
                self.assertEqual(result['counts']['ota status'], 1, result)
                self.assertNotIn('ota self', result['counts'], result)

    def test_setting_failure_still_restores_previous_timeout(self):
        result = run_harness(repeater_harness('set_failure'), timeout=90)
        self.assertNotIn('error', result, result)
        self.assertFalse(result['complete'], result)
        self.assertEqual(result['peerTimeout'], 60, result)
        self.assertIn('local ota.peer timeout 60', result['cmds'], result)
        self.assertNotIn('identity', result['counts'], result)

    def test_restore_failure_is_reported(self):
        result = run_harness(repeater_harness('restore_failure'), timeout=90)
        self.assertNotIn('error', result, result)
        self.assertEqual(result['peerTimeout'], 7, result)
        self.assertRegex(result['result']+' '+result['status'], r'(?i)restor')


if __name__ == '__main__': unittest.main()
