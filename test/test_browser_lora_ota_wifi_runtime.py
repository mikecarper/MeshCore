#!/usr/bin/env python3
"""Exercise the real updater UI with a Wi-Fi Companion transport."""
import os
import unittest

from test_browser_lora_ota_companion_runtime import HARNESS
from test_browser_lora_ota_nrf_runtime import run_harness
from test_browser_lora_ota_routed_runtime import one_replace
from test_browser_lora_ota_runtime import BROWSER
from test_browser_lora_ota_radio_setup_runtime import radio_setup_harness


def wifi_harness(*, guarded=True, wifi_capability=True):
    harness = one_replace(
        HARNESS,
        "el('source').value='companion';el('source').dispatchEvent(new Event('change'));",
        """el('connection').value='wifi';el('connection').dispatchEvent(new Event('change'));
    el('wifi-url').value='ws://127.0.0.1:8765/1234567890abcdef';
    window.LoraOtaWifiPort.create=url=>{if(url!==el('wifi-url').value)throw Error('Wrong bridge URL');return port;};""")
    harness = one_replace(
        harness,
        "if(el('source').value!=='companion')throw Error('Companion is not default');",
        "if(el('wifi-options').hidden||!el('usb-options').hidden)throw Error('Wi-Fi controls not shown');")
    harness = one_replace(harness, "requestPort:async()=>port", "requestPort:async()=>{throw Error('USB chooser used for Wi-Fi')}")
    if wifi_capability:
        harness = one_replace(harness,
            "guarded_cli=1 guarded_radio=1';",
            "guarded_cli=1 guarded_radio=1 guarded_wifi=1';")
    if not guarded or not wifi_capability:
        if not guarded:
            harness = one_replace(harness,
                "if(text==='get ota.direct')value=`> ota.direct v=2 active=${guard?1:0} busy=0`;",
                "if(text==='get ota.direct')value='Error: unsupported';")
        harness = one_replace(harness,
            "if(el('status').textContent.includes('Error:'))throw Error(el('status').textContent);\n    const buttons=",
            """await report({status:el('status').textContent,frames:port.frames,
      inspectDisabled:el('inspect').disabled});return;
    const buttons=""")
    return harness


def reconnect_harness():
    harness = wifi_harness()
    harness = one_replace(harness, 'const port=new Port();', 'let port=new Port(),firstPort=port,opens=0;')
    harness = one_replace(harness,
        "window.LoraOtaWifiPort.create=url=>{if(url!==el('wifi-url').value)throw Error('Wrong bridge URL');return port;};",
        """window.LoraOtaWifiPort.create=url=>{if(url!==el('wifi-url').value)throw Error('Wrong bridge URL');
          if(opens++)port=new Port();port.url=url;return port;};""")
    harness = one_replace(harness,
        "if(el('status').textContent.includes('Error:'))throw Error(el('status').textContent);\n    const buttons=",
        """if(el('status').textContent.includes('Error:'))throw Error(el('status').textContent);
    const pending=port.waiter;if(!pending)throw Error('No pending source read');pending({done:true});
    el('inspect').click();await wait(()=>!el('reconnect').hidden);
    el('reconnect').click();await wait(()=>opens===2&&!el('inspect').disabled);
    await report({samePort:port===firstPort,opens,status:el('workflow-status').textContent});return;
    const buttons=""")
    return harness


def wifi_setup_harness():
    harness = radio_setup_harness(factory_defaults=True)
    harness = one_replace(harness, "guarded_cli=1 guarded_radio=1';",
                          "guarded_cli=1 guarded_radio=1 guarded_wifi=1';")
    harness = one_replace(harness, "el('password').value='password';el('connect').click();", """
    el('connection').value='wifi';el('connection').dispatchEvent(new Event('change'));
    el('wifi-url').value='ws://127.0.0.1:8765/1234567890abcdef';
    LoraOtaWifiPort.create=()=>port;
    el('password').value='password';el('connect').click();""")
    return harness


def wifi_error_harness():
    harness = wifi_harness()
    harness = one_replace(harness,
        "if(text==='get ota.direct')value=`> ota.direct v=2 active=${guard?1:0} busy=0`;",
        "if(text==='get ota.direct')throw Error('USB Companion disconnected; reconnect the USB source');")
    harness = one_replace(harness,
        "if(el('status').textContent.includes('Error:'))throw Error(el('status').textContent);\n    const buttons=",
        "await report({status:el('status').textContent,details:el('error-details').textContent});return;\n    const buttons=")
    return harness


class WifiCompanionBrowserTest(unittest.TestCase):
    def setUp(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1':
                self.fail('Chromium is required')
            self.skipTest('Chromium unavailable')

    def test_guarded_wifi_source_completes_simulated_update(self):
        result = run_harness(wifi_harness(), timeout=90)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['installed'])
        self.assertIn('Installed and verified:', result['status'])
        self.assertTrue(result['otaData'])

    def test_unguarded_wifi_source_is_rejected_without_reading_inbox(self):
        result = run_harness(wifi_harness(guarded=False), timeout=60)
        self.assertNotIn('error', result, result)
        self.assertIn('guarded OTA', result['status'])
        self.assertTrue(result['inspectDisabled'])
        self.assertNotIn(10, [frame[0] for frame in result['frames']])

    def test_usb_only_guard_does_not_authorize_wifi(self):
        result = run_harness(wifi_harness(wifi_capability=False), timeout=60)
        self.assertNotIn('error', result, result)
        self.assertIn('guarded OTA', result['status'])
        self.assertTrue(result['inspectDisabled'])
        self.assertNotIn(10, [frame[0] for frame in result['frames']])

    def test_reconnect_uses_a_new_transport_owner(self):
        result = run_harness(reconnect_harness(), timeout=60)
        self.assertNotIn('error', result, result)
        self.assertFalse(result['samePort'])
        self.assertEqual(result['opens'], 2)
        self.assertIn('Wi-Fi reconnected', result['status'])

    def test_setup_progress_identifies_wifi_source(self):
        result = run_harness(wifi_setup_harness(), timeout=90)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['prepared'], result)
        self.assertIn('Wi-Fi source ota.hops=0 verified', result['inspectStatus'])
        self.assertNotIn('USB source', result['inspectStatus'])

    def test_error_details_identify_wifi_without_disclosing_bridge_url(self):
        result = run_harness(wifi_error_harness(), timeout=60)
        self.assertNotIn('error', result, result)
        self.assertIn('Wi-Fi Companion', result['details'])
        self.assertIn('Wi-Fi source', result['details'])
        self.assertNotIn('USB source', result['details'])
        self.assertNotIn('1234567890abcdef', result['details'])


if __name__ == '__main__':
    unittest.main()
