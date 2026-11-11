#!/usr/bin/env python3
"""Run the real route-finder UI in Chrome with a simulated USB repeater."""
import os
import unittest

from test_browser_lora_ota_nrf_runtime import BROWSER, run_harness
from test_browser_lora_ota_routed_runtime import one_replace, routed_harness


def route_finder_harness(kind='good'):
    harness = routed_harness()
    harness = one_replace(harness, "el('key').value=target;el('relays').value=relay;",
                          "el('key').value=target;el('candidates').value=relay;")
    age = '90000' if kind == 'stale' else '5'
    snr = '-24' if kind == 'weak' else '20'
    harness = one_replace(harness,
        "if(cmd==='ota status') return",
        "if(cmd==='neighbors')return peer===relay?'aaaaaaaa:5:36\\nbbbbbbbb:" + age + ':' + snr + "':'cccccccc:5:24';\n"
        "      if(cmd==='ota status') return")
    harness = one_replace(harness,
        "if(cmd==='ota folder')reply='folder='+(folder?'on':'off');",
        "if(cmd==='neighbors')reply='cccccccc:5:40';\n"
        "      if(cmd==='ota folder')reply='folder='+(folder?'on':'off');")
    start = """el('connect').click();await wait(()=>!el('inspect').disabled);
      el('inspect').click();await wait(()=>!el('transfer').disabled);"""
    replacement = """el('connect').click();await wait(()=>!el('inspect').disabled);
      if(el('find-route').disabled)throw Error('route finder disabled after ACL connection');
      const before=cmds.slice();el('find-route').click();
      await wait(()=>el('status').textContent.includes('Selected admin route:')||el('status').textContent.includes('Error:'));
      await wait(()=>!el('inspect').disabled);
      const snapshot={status:el('status').textContent,cmds,before,relay,target,path,folder,
        selected:el('relays').value,findDisabled:el('find-route').disabled,inspectDisabled:el('inspect').disabled};
      await report(snapshot);return;"""
    harness = one_replace(harness, start, replacement)
    return harness


class BrowserLoraOtaRouteFindRuntimeTest(unittest.TestCase):
    def setUp(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1':
                self.fail('A Chromium-family browser is required')
            self.skipTest('Chromium-family browser unavailable')

    def test_selects_acl_proved_admin_path_from_bidirectional_snr(self):
        result = run_harness(route_finder_harness())
        self.assertNotIn('error', result, result)
        self.assertNotIn('Error:', result['status'])
        self.assertIn('Selected admin route: 1 relay(s)', result['status'])
        self.assertIn('weakest measured SNR 5 dB', result['status'])
        self.assertIn('OTA data still floods.', result['status'])
        self.assertIn('do not identify the radio profile', result['status'])
        self.assertEqual(result['selected'], result['relay'])
        self.assertEqual(result['path'], 'off')
        self.assertTrue(result['findDisabled'])
        self.assertFalse(result['inspectDisabled'])
        commands = result['cmds']
        self.assertIn('local neighbors', commands)
        self.assertIn('local ota.peer acl '+result['relay'], commands)
        self.assertIn('local ota.peer acl '+result['target'], commands)
        self.assertIn('local ota.peer path CCCCCC', commands)
        self.assertIn('local ota.peer path off', commands)
        self.assertFalse(any('neighbors' in c or 'ota.peer acl ' in c for c in result['before']))
        self.assertFalse(any('ota pull' in c or 'ota install' in c for c in commands))
        self.assertFalse(result['folder'])

    def test_stale_or_weak_link_is_not_selected(self):
        for kind in ('stale','weak'):
            with self.subTest(kind=kind):
                result = run_harness(route_finder_harness(kind))
                self.assertNotIn('error', result, result)
                self.assertIn('No route has sufficient recent measurements', result['status'])
                self.assertEqual(result['selected'], '')
                self.assertEqual(result['path'], 'off')
                self.assertFalse(result['findDisabled'])
                self.assertNotIn('local ota.peer acl '+result['target'], result['cmds'])
                self.assertFalse(any('ota pull' in c or 'ota install' in c for c in result['cmds']))
                self.assertFalse(result['folder'])


if __name__ == '__main__': unittest.main()
