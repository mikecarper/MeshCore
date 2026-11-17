#!/usr/bin/env python3
"""Exercise Bluetooth choice and capability gating in the updater UI."""
import os
import unittest
from urllib.parse import parse_qs, urlsplit
from test_browser_lora_ota_companion_runtime import HARNESS
from test_browser_lora_ota_nrf_runtime import run_harness
from test_browser_lora_ota_routed_runtime import one_replace
from test_browser_lora_ota_runtime import BROWSER


def bluetooth_harness(capable, guarded=True):
    harness = one_replace(HARNESS, "guarded_cli=1 guarded_radio=1';", "guarded_cli=1 guarded_radio=1 guarded_wifi=0 guarded_ble=1';")
    if not guarded:
        harness = one_replace(harness, "guarded_cli=1 guarded_radio=1 guarded_wifi=0 guarded_ble=1';", "guarded_cli=1 guarded_radio=1 guarded_wifi=0 guarded_ble=0';")
    harness = one_replace(harness,
        "el('source').value='companion';el('source').dispatchEvent(new Event('change'));",
        """Object.defineProperty(navigator,'bluetooth',{value:{},configurable:true});
    const device={name:'Test Companion'};let choices=0;
    window.LoraOtaBlePort={requestDevice:api=>{if(api!==navigator.bluetooth)throw Error('wrong API');choices++;return Promise.resolve(device);},
      create:d=>{if(d!==device)throw Error('wrong device');port.device=d;port.transportKind='bluetooth';port.bleOtaCapable=CAPABLE;return port;}};
    el('connection').value='bluetooth';el('connection').dispatchEvent(new Event('change'));""".replace('CAPABLE',str(capable).lower()))
    harness = one_replace(harness,
        "if(el('source').value!=='companion')throw Error('Companion is not default');",
        "if(el('bluetooth-options').hidden||!el('usb-options').hidden||!el('wifi-options').hidden)throw Error('Bluetooth controls not shown');")
    if not capable or not guarded:
        harness = one_replace(harness,
            "if(el('status').textContent.includes('Error:'))throw Error(el('status').textContent);\n    const buttons=",
            "await report({status:el('status').textContent,frames:port.frames,choices,inspectDisabled:el('inspect').disabled});return;\n    const buttons=")
    return harness


class BluetoothBrowserTest(unittest.TestCase):
    def setUp(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1':
                self.fail('Chromium is required')
            self.skipTest('Chromium unavailable')

    def test_capable_bluetooth_source_completes_simulated_update(self):
        result=run_harness(bluetooth_harness(True),timeout=90)
        self.assertNotIn('error',result,result)
        self.assertTrue(result['installed'])
        self.assertTrue(result['otaData'])

    def test_older_bluetooth_source_is_rejected_before_contact_or_inbox(self):
        result=run_harness(bluetooth_harness(False),timeout=60)
        self.assertNotIn('error',result,result)
        self.assertIn('guarded Bluetooth OTA support',result['status'])
        self.assertTrue(result['inspectDisabled'])
        self.assertEqual(result['choices'],1)
        self.assertNotIn(4,[frame[0] for frame in result['frames']])
        self.assertNotIn(10,[frame[0] for frame in result['frames']])


    def test_ble_framing_alone_does_not_authorize_unguarded_ota(self):
        result=run_harness(bluetooth_harness(True,guarded=False),timeout=60)
        self.assertNotIn('error',result,result)
        self.assertIn('guarded Bluetooth OTA support',result['status'])
        self.assertNotIn(4,[frame[0] for frame in result['frames']])
        self.assertNotIn(10,[frame[0] for frame in result['frames']])


def platform_harness(user_agent, platform, touches):
    harness = one_replace(HARNESS,
        "el('source').value='companion';el('source').dispatchEvent(new Event('change'));",
        """Object.defineProperty(navigator,'userAgent',{value:UA,configurable:true});
    Object.defineProperty(navigator,'platform',{value:PLATFORM,configurable:true});
    Object.defineProperty(navigator,'maxTouchPoints',{value:TOUCHES,configurable:true});
    el('connection').value='bluetooth';el('connection').dispatchEvent(new Event('change'));
    await report({bluefyHidden:el('bluefy-help').hidden,text:el('bluefy-help').textContent,
      install:el('bluefy-help').querySelector('a').href,
      open:el('bluefy-help').querySelectorAll('a')[1].getAttribute('href')});return;"""
        .replace('UA',repr(user_agent)).replace('PLATFORM',repr(platform)).replace('TOUCHES',str(touches)))
    return harness


class BluefyVisibilityTest(unittest.TestCase):
    def setUp(self):
        if not BROWSER:
            self.skipTest('Chromium unavailable')

    def test_ios_and_ipados_show_bluefy_android_hides_it(self):
        for ua,platform,touches,hidden in [
            ('Mozilla iPhone','iPhone',5,False),
            ('Mozilla Macintosh','MacIntel',5,False),
            ('Mozilla Android','Linux armv8l',5,True),
        ]:
            with self.subTest(platform=platform):
                result=run_harness(platform_harness(ua,platform,touches),timeout=30)
                self.assertNotIn('error',result,result)
                self.assertEqual(result['bluefyHidden'],hidden)
                self.assertIn('apps.apple.com/us/app/bluefy',result['install'])
                self.assertTrue(result['open'].startswith('bluefy://open?url='))
                target=parse_qs(urlsplit(result['open']).query)['url'][0]
                self.assertEqual(target,'https://mikecarper.github.io/MeshCore/browser_lora_ota/?connection=bluetooth#ota-connect')
                self.assertIn(target,result['text'])

    def test_bluefy_link_selects_bluetooth_without_connecting(self):
        for query, expected in [('', 'usb'), ('?connection=bluetooth', 'bluetooth'), ('?connection=invalid', 'usb')]:
            with self.subTest(query=query):
                harness=one_replace(HARNESS, '<script>\n(()=>{', '''<script>
(()=>{
  history.replaceState(null,'',QUERY+'#ota-connect');
  let chooserCalls=0;
  Object.defineProperty(navigator,'bluetooth',{value:{requestDevice:()=>{chooserCalls++;throw Error('unexpected chooser')}},configurable:true});'''.replace('QUERY',repr(query)))
                harness=one_replace(harness,"window.addEventListener('load',async()=>{try{",'''window.addEventListener('load',async()=>{try{
    await report({connection:el('connection').value,optionsHidden:el('bluetooth-options').hidden,
      button:el('connect').textContent,status:el('contact-status').textContent,
      anchor:document.getElementById('ota-connect')?.id,hash:location.hash,chooserCalls});return;''')
                result=run_harness(harness,timeout=30)
                self.assertNotIn('error',result,result)
                self.assertEqual(result['connection'],expected)
                self.assertEqual(result['optionsHidden'],expected!='bluetooth')
                self.assertEqual(result['anchor'],'ota-connect')
                self.assertEqual(result['hash'],'#ota-connect')
                self.assertEqual(result['chooserCalls'],0)
                if expected=='bluetooth':
                    self.assertEqual(result['button'],'Connect Bluetooth Companion')
                    self.assertIn('Bluetooth Companion',result['status'])



def real_ble_harness(disconnect=False):
    harness = one_replace(HARNESS, "guarded_cli=1 guarded_radio=1';", "guarded_cli=1 guarded_radio=1 guarded_wifi=0 guarded_ble=1';")
    harness = one_replace(harness,
        "const port=new Port();Object.defineProperty(navigator,'serial',{value:{requestPort:async()=>port},configurable:true});",
        """const port=new Port();let disconnects=0,fragments=0,commands=0,hello=0;
    class Emitter {constructor(){this.listeners=new Map()} addEventListener(k,f){this.listeners.set(k,f)} removeEventListener(k,f){if(this.listeners.get(k)===f)this.listeners.delete(k)} emit(k){this.listeners.get(k)?.({target:this})}}
    const device=new Emitter(),tx=new Emitter();
    tx.notify=bytes=>{tx.value=new DataView(Uint8Array.from(bytes).buffer);tx.emit('characteristicvaluechanged')};
    tx.startNotifications=async()=>tx;
    port.push=frame=>{const total=frame[1]+256*frame[2];if(frame[0]!==62||frame.length!==total+3)throw Error('bad backend frame');
      const payload=frame.slice(3);for(let offset=0;offset<payload.length;offset+=17)tx.notify([0x7e,payload.length,offset,...payload.slice(offset,offset+17)]);};
    let incoming=null;
    const rx={async writeValueWithResponse(v){const x=Array.from(v);
      if(x[0]===0x7f){hello++;tx.notify(x);return;}
      if(x[0]!==0x7e||x.length>20||x[1]===0||x[2]!== (incoming?.length||0))throw Error('bad fragment');
      if(!incoming)incoming=[];incoming.push(...x.slice(3));fragments++;
      if(incoming.length>x[1])throw Error('overlong frame');
      const done=incoming.length===x[1], complete=done?Uint8Array.from(incoming):null;if(done)incoming=null;
      tx.notify([0x7e,0,x[2]+x.length-3]);
      if(complete){commands++;await port.write(Uint8Array.of(60,complete.length,0,...complete));}
    }};
    const service={async getCharacteristic(id){return id===LoraOtaBlePort.RX?rx:tx;}};
    device.gatt={connected:false,async connect(){this.connected=true;return {async getPrimaryService(){return service}}},disconnect(){this.connected=false;disconnects++;device.emit('gattserverdisconnected')}};
    Object.defineProperty(navigator,'bluetooth',{value:{requestDevice:async opts=>{if(!opts.filters?.length)throw Error('missing service filter');return device}},configurable:true});
    Object.defineProperty(navigator,'serial',{value:{requestPort:async()=>{throw Error('USB chooser used for Bluetooth')}},configurable:true});""")
    harness = one_replace(harness,
        "el('source').value='companion';el('source').dispatchEvent(new Event('change'));",
        "el('connection').value='bluetooth';el('connection').dispatchEvent(new Event('change'));" )
    harness = one_replace(harness,
        "if(el('source').value!=='companion')throw Error('Companion is not default');",
        "if(el('bluetooth-options').hidden)throw Error('Bluetooth controls not shown');")
    harness = one_replace(harness,
        "status,cmds,installed,otaData,advertSeen,guard,frames:port.frames",
        "status,cmds,installed,otaData,advertSeen,guard,frames:port.frames,hello,fragments,commands,disconnects")
    if disconnect:
        harness = one_replace(harness,
            "if(el('status').textContent.includes('Error:'))throw Error(el('status').textContent);\n    const buttons=",
            """if(el('status').textContent.includes('Error:'))throw Error(el('status').textContent);
    device.gatt.disconnect();el('inspect').click();await wait(()=>!el('reconnect').hidden);
    await report({status:el('workflow-status').textContent,hello,fragments,commands,disconnects});return;
    const buttons=""")
    return harness


class RealBleAdapterBrowserTest(unittest.TestCase):
    def setUp(self):
        if not BROWSER:
            self.skipTest('Chromium unavailable')

    def test_adapter_and_browser_complete_guarded_update(self):
        result=run_harness(real_ble_harness(),timeout=100)
        self.assertNotIn('error',result,result)
        self.assertTrue(result['installed'])
        self.assertTrue(result['otaData'])
        self.assertEqual(result['hello'],1)
        self.assertGreater(result['fragments'],result['commands'])
        self.assertGreaterEqual(result['disconnects'],1)

    def test_disconnected_adapter_exposes_reconnect(self):
        result=run_harness(real_ble_harness(disconnect=True),timeout=60)
        self.assertNotIn('error',result,result)
        self.assertIn('Bluetooth',result['status'])
        self.assertGreaterEqual(result['disconnects'],1)


if __name__=='__main__':
    unittest.main()
