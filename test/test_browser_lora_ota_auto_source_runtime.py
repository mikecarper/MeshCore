#!/usr/bin/env python3
"""The default browser flow detects either USB source and keeps one connection."""
import os
import unittest

from test_browser_lora_ota_runtime import BROWSER, HARNESS as REPEATER_HARNESS
from test_browser_lora_ota_companion_runtime import HARNESS as COMPANION_HARNESS
from test_browser_lora_ota_nrf_runtime import run_harness
from test_browser_lora_ota_routed_runtime import one_replace


def automatic_companion_harness():
    harness = COMPANION_HARNESS
    harness = one_replace(harness,
        "el('source').value='companion';el('source').dispatchEvent(new Event('change'));",
        "if(el('source').value!=='auto')throw Error('Automatic source detection is not the default');")
    harness = one_replace(harness,
        "if(el('source').value!=='companion')throw Error('Companion is not default');",
        "if(el('source').value!=='auto')throw Error('Automatic override changed before connection');")
    harness = one_replace(harness,
        'constructor(){this.q=[];this.waiter=null;this.frames=[];}',
        'constructor(){this.q=[];this.waiter=null;this.frames=[];this.opens=0;}')
    harness = one_replace(harness, 'async open(){this.readable=', 'async open(){this.opens++;this.readable=')
    harness = one_replace(harness,
        "const buttons=()=>[...el('contact').querySelectorAll('button[data-contact-key]')];",
        """if(port.opens!==1)throw Error('Automatic Companion detection reopened USB');
    if(el('source').value!=='auto'||!el('source-info').textContent.includes('Companion firmware:'))throw Error('Detected Companion source was not shown');
    const buttons=()=>[...el('contact').querySelectorAll('button[data-contact-key]')];""")
    harness = one_replace(harness,
        'guard,frames:port.frames});', 'guard,frames:port.frames,opens:port.opens,sourceOverride:el(\'source\').value});')
    return harness


def automatic_repeater_harness():
    harness = REPEATER_HARNESS
    harness = one_replace(harness,
        "el('source').value='repeater';el('source').dispatchEvent(new Event('change'));",
        "if(el('source').value!=='auto')throw Error('Automatic source detection is not the default');")
    harness = one_replace(harness,
        'constructor(){this.q=[];this.pending=null;this.frames=[]}',
        'constructor(){this.q=[];this.pending=null;this.frames=[];this.opens=0;this.probes=[]}')
    harness = one_replace(harness, 'async open(){', 'async open(){this.opens++;')
    harness = one_replace(harness,
        'async write(bytes){',
        "async write(bytes){if(bytes[0]===60){this.probes.push(Array.from(bytes));return;}")
    harness = one_replace(harness,
        "el('key').value=target;",
        "if(el('key').value)throw Error('Automatic Repeater test must connect before selecting a target');")
    harness = one_replace(harness,
        "el('connect').click();await wait(()=>!el('inspect').disabled);",
        """el('connect').click();await wait(()=>!el('inspect').disabled);
      if(port.opens!==1)throw Error('Automatic Repeater detection reopened USB');
      if(el('source').value!=='auto'||!el('source-info').textContent.includes('USB Repeater detected'))throw Error('Detected Repeater source was not shown');
      if(el('target-controls').hidden)throw Error('Repeater target selection is hidden');
      el('key').value=target;el('key').dispatchEvent(new Event('input',{bubbles:true}));""")
    harness = one_replace(harness,
        'frames:port.frames,folder,buttons:',
        'frames:port.frames,folder,opens:port.opens,probes:port.probes,sourceOverride:el(\'source\').value,buttons:')
    return harness


class BrowserAutomaticSourceTest(unittest.TestCase):
    def setUp(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1':
                self.fail('Chromium is required')
            self.skipTest('Chromium unavailable')

    def test_default_auto_detects_companion_and_completes_update_without_source_override(self):
        result = run_harness(automatic_companion_harness(), timeout=90)
        self.assertNotIn('error', result, result)
        self.assertIn('Installed and verified:', result['status'])
        self.assertTrue(result['installed'])
        self.assertEqual(result['opens'], 1)
        self.assertEqual(result['sourceOverride'], 'auto')
        self.assertEqual(result['frames'][0], [22, 4])
        self.assertEqual(sum(frame[0] == 1 for frame in result['frames']), 1)

    def test_default_auto_detects_repeater_then_accepts_target_without_reconnect(self):
        result = run_harness(automatic_repeater_harness(), timeout=45)
        self.assertNotIn('error', result, result)
        self.assertIn('Installed and verified:', result['status'])
        self.assertEqual(result['opens'], 1)
        self.assertEqual(result['sourceOverride'], 'auto')
        self.assertEqual(result['probes'], [[60, 2, 0, 22, 4]])
        self.assertEqual(result['journal']['phase'], 'installed')
        self.assertFalse(result['folder'])
        self.assertEqual(sum(command.startswith('remote ota pull ') for command in result['cmds']), 1)
        self.assertEqual(sum(command.startswith('remote ota installhash ') for command in result['cmds']), 1)


if __name__ == '__main__':
    unittest.main()
