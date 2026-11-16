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
    el('wifi-url').value='192.168.1.96';
    window.LoraOtaWifiPort.create=url=>{if(url!==el('wifi-url').value)throw Error('Wrong IP');port.url='ws://192.168.1.96:5005/ota';return port;};""")
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
        "window.LoraOtaWifiPort.create=url=>{if(url!==el('wifi-url').value)throw Error('Wrong IP');port.url='ws://192.168.1.96:5005/ota';return port;};",
        """window.LoraOtaWifiPort.create=url=>{if(url!==el('wifi-url').value&&url!=='ws://192.168.1.96:5005/ota')throw Error('Wrong IP');
          if(opens++)port=new Port();port.url='ws://192.168.1.96:5005/ota';return port;};""")
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
    el('wifi-url').value='192.168.1.96';
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


def wifi_scan_harness():
    harness=wifi_harness()
    harness=one_replace(harness,"el('wifi-url').value='192.168.1.96';", """
    el('wifi-os').value='windows';el('wifi-os').dispatchEvent(new Event('change'));
    LoraOtaWifiScan.localPrefixes=async()=>['192.168.1'];
    LoraOtaWifiScan.scan=async(network,options)=>{
      if(network!=='192.168.1.0/24')throw Error('wrong network');
      options.onFound('192.168.1.96');options.onProgress(254,254);return ['192.168.1.96'];
    };
    el('wifi-scan').click();await wait(()=>!el('wifi-scan').disabled);
    if(el('wifi-url').value!=='192.168.1.96')throw Error('scan did not select IP');
    if(!el('wifi-os-help').textContent.includes('Windows'))throw Error('wrong IP help');""")
    harness=one_replace(harness,
        "if(el('status').textContent.includes('Error:'))throw Error(el('status').textContent);\n    const buttons=",
        "await report({ip:el('wifi-url').value,network:el('wifi-subnet').value,help:el('wifi-os-help').textContent});return;\n    const buttons=")
    return harness


def contact_path_help_harness():
    harness = one_replace(wifi_harness(),
        "else if(f[0]===42){out=Uint8Array.of(1,2);}",
        "else if(f[0]===42){out=hex(f.slice(2))===key?Uint8Array.of(22,1,0,0,0,2,0xaa,0xbb):Uint8Array.of(1,2);}")
    return one_replace(harness,
        "if(el('status').textContent.includes('Error:'))throw Error(el('status').textContent);\n    const buttons=",
        """if(el('status').textContent.includes('Error:'))throw Error(el('status').textContent);
    await wait(()=>el('advert-path-progress').textContent.includes('complete'));
    const help=el('contact-route-help');
    const helpVisible=!help.hidden, text=help.textContent;
    const labels=[...el('contact').querySelectorAll('button')].map(button=>button.textContent);
    el('contact-search').value='not-a-contact';el('contact-search').dispatchEvent(new Event('input'));
    const hiddenAfterSearch=help.hidden;
    el('contact-search').value='';el('contact-search').dispatchEvent(new Event('input'));
    await report({visible:helpVisible,text,labels,hiddenAfterSearch,visibleAfterClear:!help.hidden});return;
    const buttons=""")


def unsupported_advert_path_harness():
    harness = one_replace(wifi_harness(),
        "else if(f[0]===42){out=Uint8Array.of(1,2);}",
        "else if(f[0]===42){out=Uint8Array.of(1,1);}")
    return one_replace(harness,
        "if(el('status').textContent.includes('Error:'))throw Error(el('status').textContent);\n    const buttons=",
        """await wait(()=>el('advert-path-progress').textContent.includes('unavailable'));
    await report({progress:el('advert-path-progress').textContent,stopHidden:el('advert-path-stop').hidden,
      queries:port.frames.filter(frame=>frame[0]===42).length,connectionReady:!el('inspect').disabled});return;
    const buttons=""")


def stop_advert_path_harness():
    harness = one_replace(wifi_harness(),
        "else if(f[0]===42){out=Uint8Array.of(1,2);}",
        "else if(f[0]===42){setTimeout(()=>this.push(wire(Uint8Array.of(1,2))),100);return;}")
    return one_replace(harness,
        "if(el('status').textContent.includes('Error:'))throw Error(el('status').textContent);\n    const buttons=",
        """await wait(()=>!el('advert-path-stop').hidden);
    el('advert-path-stop').click();await new Promise(resolve=>setTimeout(resolve,250));
    await report({progress:el('advert-path-progress').textContent,queries:port.frames.filter(frame=>frame[0]===42).length,
      stopHidden:el('advert-path-stop').hidden});return;
    const buttons=""")


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

    def test_scan_suggests_network_and_selects_matching_device(self):
        result=run_harness(wifi_scan_harness(),timeout=60)
        self.assertNotIn('error',result,result)
        self.assertEqual(result['ip'],'192.168.1.96')
        self.assertEqual(result['network'],'192.168.1.0/24')
        self.assertIn('Windows',result['help'])

    def test_saved_path_explanation_follows_contact_visibility(self):
        result = run_harness(contact_path_help_harness(), timeout=60)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['visible'])
        self.assertTrue(result['hiddenAfterSearch'])
        self.assertTrue(result['visibleAfterClear'])
        self.assertIn('Neither path confirms', result['text'])
        self.assertIn('zero-hop', result['text'])
        self.assertTrue(any('incoming advert: AA → BB (2 hops)' in label for label in result['labels']))

    def test_old_wifi_companion_stops_after_one_unsupported_lookup(self):
        result = run_harness(unsupported_advert_path_harness(), timeout=60)
        self.assertNotIn('error', result, result)
        self.assertEqual(result['queries'], 1)
        self.assertTrue(result['stopHidden'])
        self.assertTrue(result['connectionReady'])
        self.assertIn('newer Companion firmware', result['progress'])

    def test_stopping_advert_path_checks_does_not_start_more_queries(self):
        result = run_harness(stop_advert_path_harness(), timeout=60)
        self.assertNotIn('error', result, result)
        self.assertEqual(result['queries'], 1)
        self.assertTrue(result['stopHidden'])
        self.assertIn('stopped', result['progress'])

    def test_setup_progress_identifies_wifi_source(self):
        result = run_harness(wifi_setup_harness(), timeout=90)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['prepared'], result)
        self.assertIn('Wi-Fi source ota.hops=0 verified', result['inspectStatus'])
        self.assertNotIn('USB source', result['inspectStatus'])

    def test_error_details_identify_wifi_without_disclosing_address(self):
        result = run_harness(wifi_error_harness(), timeout=60)
        self.assertNotIn('error', result, result)
        self.assertIn('Wi-Fi Companion', result['details'])
        self.assertIn('Wi-Fi source', result['details'])
        self.assertNotIn('USB source', result['details'])
        self.assertNotIn('192.168.1.96', result['details'])


if __name__ == '__main__':
    unittest.main()
