#!/usr/bin/env python3
"""A Repeater source respects the password entered in the normal update UI."""
import os
import unittest

from test_browser_lora_ota_runtime import BROWSER, HARNESS
from test_browser_lora_ota_nrf_runtime import run_harness


def password_harness(password):
    harness = HARNESS
    original = "if(cmd.startsWith('ota.peer acl ')||cmd.startsWith('ota.peer cmd.acl ')){"
    replacement = """if(cmd.startsWith('ota.peer acl ')||cmd.startsWith('ota.peer cmd.acl ')
          ||cmd.startsWith('ota.peer login.repeater ')||cmd.startsWith('ota.peer cmd ')){"""
    assert harness.count(original) == 1
    harness = harness.replace(original, replacement, 1)
    original = "job='01020304';jobResult=cmd.startsWith('ota.peer acl ')?'OK':this.remote(cmd.slice(('ota.peer cmd.acl '+target+' ').length));"
    replacement = """job='01020304';
        jobResult=(cmd.startsWith('ota.peer acl ')||cmd.startsWith('ota.peer login.repeater '))?'OK':
          this.remote(cmd.slice((cmd.startsWith('ota.peer cmd.acl ')?'ota.peer cmd.acl ':'ota.peer cmd ').length+target.length+1));"""
    assert harness.count(original) == 1
    harness = harness.replace(original, replacement, 1)
    original = "el('key').value=target;"
    assert harness.count(original) == 1
    harness = harness.replace(original, original + """
      if(el('acl').checked)throw Error('Repeater selection must not enable a hidden password override');
      el('password').value=PASSWORD;""".replace('PASSWORD', repr(password)), 1)
    return harness


class BrowserSourcePasswordTest(unittest.TestCase):
    def setUp(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1':
                self.fail('Chromium is required')
            self.skipTest('Chromium unavailable')

    def test_repeater_uses_entered_password_without_advanced_changes(self):
        result = run_harness(password_harness('example pass'), timeout=45)
        self.assertNotIn('error', result, result)
        self.assertIn('Installed and verified:', result['status'])
        self.assertTrue(any(command == 'local ota.peer login.repeater ' + 'b' * 64 + ' example pass'
                            for command in result['cmds']), result)
        self.assertFalse(any(command.startswith('local ota.peer acl ')
                             for command in result['cmds']), result)

    def test_blank_password_automatically_uses_existing_acl(self):
        result = run_harness(password_harness(''), timeout=45)
        self.assertNotIn('error', result, result)
        self.assertIn('Installed and verified:', result['status'])
        self.assertTrue(any(command == 'local ota.peer acl ' + 'b' * 64
                            for command in result['cmds']), result)
        self.assertFalse(any(command.startswith('local ota.peer login')
                             for command in result['cmds']), result)

    def test_password_entered_after_connecting_is_honored_and_updates_route_gating(self):
        harness = password_harness('')
        original = "el('connect').click();await wait(()=>!el('inspect').disabled);"
        self.assertEqual(harness.count(original), 1)
        harness = harness.replace(original, original + """
      if(el('find-route').disabled)throw Error('A blank password should allow ACL route search');
      el('password').value='example pass';el('password').dispatchEvent(new Event('input'));
      if(!el('find-route').disabled)throw Error('A password should disable ACL-only route search');
      el('password').value='';el('password').dispatchEvent(new Event('input'));
      if(el('find-route').disabled)throw Error('Clearing the password should restore ACL route search');
      el('password').value='example pass';el('password').dispatchEvent(new Event('input'));""", 1)
        result = run_harness(harness, timeout=45)
        self.assertNotIn('error', result, result)
        self.assertIn('Installed and verified:', result['status'])
        self.assertIn('local ota.peer login.repeater ' + 'b' * 64 + ' example pass', result['cmds'])
        self.assertFalse(any(command.startswith('local ota.peer acl ')
                             for command in result['cmds']), result)

    def test_explicit_acl_override_ignores_the_password_and_allows_route_search(self):
        harness = password_harness('example pass')
        original = "el('connect').click();await wait(()=>!el('inspect').disabled);"
        self.assertEqual(harness.count(original), 1)
        harness = harness.replace(original, "el('acl').checked=true;el('acl').dispatchEvent(new Event('change'));" + original + """
      if(el('find-route').disabled)throw Error('The explicit ACL override should allow route search');""", 1)
        result = run_harness(harness, timeout=45)
        self.assertNotIn('error', result, result)
        self.assertIn('Installed and verified:', result['status'])
        self.assertIn('local ota.peer acl ' + 'b' * 64, result['cmds'])
        self.assertFalse(any(command.startswith('local ota.peer login')
                             for command in result['cmds']), result)

    def test_companion_honors_an_explicit_acl_override(self):
        from test_browser_lora_ota_companion_runtime import HARNESS as companion_harness
        script = companion_harness
        original = "el('source').value='companion';el('source').dispatchEvent(new Event('change'));"
        self.assertEqual(script.count(original), 1)
        script = script.replace(original, "el('acl').checked=true;" + original, 1)
        original = "el('transfer').click();await wait(()=>!el('install').disabled||el('status').textContent.includes('Error:'));"
        self.assertEqual(script.count(original), 1)
        script = script.replace(original, "await report({status:el('status').textContent,frames:port.frames,acl:el('acl').checked});return;", 1)
        result = run_harness(script, timeout=45)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['acl'], result)
        self.assertFalse(any(frame[0] == 26 for frame in result['frames']), result)


if __name__ == '__main__':
    unittest.main()
