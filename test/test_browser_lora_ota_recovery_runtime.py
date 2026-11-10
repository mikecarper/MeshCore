#!/usr/bin/env python3
"""Recover a saved update channel before the real browser sends any login."""
import os
import unittest

from test_browser_lora_ota_password_runtime import password_harness
from test_browser_lora_ota_runtime import BROWSER
from test_browser_lora_ota_nrf_runtime import run_harness


def recovery_harness(proof_matches=True):
    harness = password_harness('example pass')

    def replace(old, new):
        nonlocal harness
        assert harness.count(old) == 1, old
        harness = harness.replace(old, new, 1)

    replace("let folder=false, mid='', state='idle', installed=false, newHash='', job='', jobResult='';",
            "let sourceTemp=false;let folder=false, mid='', state='idle', installed=false, newHash='', job='', jobResult='';")
    replace("'ota.peer close':'OTApeer closed','get tempradio':radio,'get radio':radio,",
            "'ota.peer close':'OTApeer closed','get tempradio':sourceTemp?'TempRadio active: '+radio.split(',').slice(0,4).join(',')+' 120s left,preamble=128':'TempRadio inactive','get radio':radio.split(',').slice(0,4).join(','),")
    replace("if(cmd==='ota status') return", "if(cmd==='get public.key')return " +
            ("target" if proof_matches else "'e'.repeat(64)") + ";\n      if(cmd==='ota status') return")
    replace("if(cmd==='ota folder')reply='folder='+(folder?'on':'off');", """
      if(cmd==='get radio2'||cmd==='get tempradio2')reply='off';
      if(cmd.startsWith('tempradio ')){
        if(!/^tempradio 910\\.525,62\\.5,7,5,(?:2|120),128$/.test(cmd))throw Error('Unexpected recovery write: '+cmd);
        sourceTemp=true;reply='OK - temporary radio pending';
      }
      if(cmd.startsWith('ota.peer login')&&!sourceTemp)throw Error('Login sent before source recovery');
      if(cmd==='ota folder')reply='folder='+(folder?'on':'off');""")
    replace("el('key').value=target;", """el('key').value=target;
      localStorage.setItem('meshcore-browser-lora-ota',JSON.stringify({
        phase:'pull attempted',source,target,id:'01234567',packageHash:'cc'.repeat(32),bodyHash:'dd'.repeat(8),
        radio:{tuple:[910.525,62.5,7,5],preamble:128,sourceProfile:0,targetProfile:0,
          verifiedAt:Date.now(),expiresAt:Date.now()+120000}}));""")
    replace("el('transfer').click();await wait(()=>!el('install').disabled);",
            "await report({status:el('status').textContent,cmds,sourceTemp,transferDisabled:el('transfer').disabled});return;")
    replace("el('inspect').click();await wait(()=>!el('transfer').disabled);",
            "el('inspect').click();await wait(()=>!el('transfer').disabled||el('status').textContent.includes('Error:'));")
    return harness


class BrowserChannelRecoveryTest(unittest.TestCase):
    def setUp(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1':
                self.fail('Chromium is required')
            self.skipTest('Chromium unavailable')

    def test_source_channel_is_restored_before_login_and_extended_after_proof(self):
        result = run_harness(recovery_harness(), timeout=45)
        self.assertNotIn('error', result, result)
        self.assertFalse(result['transferDisabled'], result)
        commands = result['cmds']
        trial = commands.index('local tempradio 910.525,62.5,7,5,2,128')
        login = commands.index('local ota.peer login.repeater ' + 'b' * 64 + ' example pass')
        extension = commands.index('local tempradio 910.525,62.5,7,5,120,128')
        target_status = commands.index('remote ota status')
        self.assertLess(trial, login)
        self.assertLess(login, extension)
        self.assertLess(extension, target_status)
        proof = commands.index('remote get public.key')
        self.assertLess(login, proof)
        self.assertLess(proof, extension)
        self.assertFalse(any(command.startswith('local set radio ') or command.startswith('remote set ')
                             or command.startswith('remote ota pull ') or command == 'remote ota cancel'
                             for command in commands), result)

    def test_wrong_full_key_does_not_extend_the_trial_or_enable_transfer(self):
        result = run_harness(recovery_harness(proof_matches=False), timeout=45)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['transferDisabled'], result)
        self.assertIn('Target did not prove its full public key on the recovered channel', result['status'])
        self.assertIn('local tempradio 910.525,62.5,7,5,2,128', result['cmds'])
        self.assertNotIn('local tempradio 910.525,62.5,7,5,120,128', result['cmds'])


if __name__ == '__main__':
    unittest.main()
