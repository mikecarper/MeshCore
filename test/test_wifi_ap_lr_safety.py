"""Execute production policies and ESP-NOW starts with IDF 4.4 shared LR state."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest
from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]
BRIDGE_CLASS = r"""
class Bridge {
public:
 struct Prefs {int bridge_format=mesh::bridge::ESPNOW_FORMAT_RAW;uint8_t bridge_channel=1;} prefs;
 Prefs* _prefs=&prefs;bool _initialized=false;
 int _active_format=0,_rx_mux=0,_rx_head=0,_rx_tail=0,_rx_count=0,_rx_dropped=0,_rx_dropped_reported=0;
 int _tx_mux=0,_tx_head=0,_tx_tail=0,_tx_count=0,_tx_waiting=0,_tx_callback_done=0;
 int _tx_callback_status=0,_tx_dropped=0,_tx_dropped_reported=0;
 uint32_t _tx_started_at=0,_tx_retry_at=0;
 bool _sdk_teardown_failed=false;
 mesh::espnow::ESPNowRawReassembler _raw_reassembler;
 struct QueuedTransmit {bool started=false;int packet=0;};
 static constexpr int TX_QUEUE_DEPTH=2;
 QueuedTransmit _tx_queue[TX_QUEUE_DEPTH];
 struct {void clear(const int*){}} _seen_packets;
 static void recv_cb(){}static void send_cb(){}
 void begin();void end();
};
"""
WEB_CLASS = r"""
namespace mesh {
struct DebugPort {template<class... T> void printf(const char*,T...){}void println(const char*){}};
DebugPort& usbDebugPort(){static DebugPort port;return port;}
}
class WebTeardown {
public:
 enum Mode {MODE_SETUP,MODE_LAN};
 struct DNS {void stop(){}};
 DNS* _dns=new DNS;
 Mode _mode=MODE_SETUP;
 bool _was_setup_ap=true,_initial_setup=true,_retry_saved_wifi_in_setup=true,_setup_reconnect_in_progress=true;
 uint32_t _setup_started_at=1,_setup_reconnect_deadline=1,_last_activity=1;
 char _wifi_ssid[5]="mesh";
 struct Tracker {void noteConnected(){}} _wifi_reconnect_tracker;
 ~WebTeardown(){delete _dns;}
 void promote(){uint32_t now=1234;@PROMOTE@}
 void idle(){uint32_t now=1234;@IDLE@}
};
"""
CHECKS = r"""
void reset_policy(){
 failure=0;mode_reads=station_writes=ap_writes=storage_writes=0;
 driver_initialized=true;storage_ram=false;lr=false;nvs_lr=false;
 sdk_mode=WIFI_STA;sta_phy=ap_phy=7;ap_during_station_write=false;
 WiFi.facade_started=true;protocol_order.clear();
}
void check_ap(){uint8_t actual=0;assert(esp_wifi_get_protocol(WIFI_IF_AP,&actual)==ESP_OK&&actual==7);}
int main(int argc,char** argv){
 assert(argc==2);std::string test=argv[1];Backend backend;mesh::wireless::control().begin(backend);
 if(test=="station"){
  sdk_mode=WIFI_AP_STA;WiFi.facade_started=false;
  // Witness the SDK coupling: a direct STA15 write changes AP's live bitmap.
  assert(esp_wifi_set_protocol(WIFI_IF_STA,15)==ESP_OK);
  uint8_t actual=0;esp_wifi_get_protocol(WIFI_IF_AP,&actual);assert(actual==15);
  // Production policy repairs it even if the Arduino facade reports OFF.
  assert(mesh::wifi::applyProtocolMask(WIFI_IF_STA)==ESP_OK);check_ap();
  assert(!lr&&station_writes==2&&ap_writes==1);
  assert(mesh::wifi::beginStation("mesh","password")==WL_CONNECTED);
  assert(WiFi.scan_channel==1&&WiFi.join_channel==1);check_ap();
  // Same unchanged helper is used by setup saved-SSID retry and credential handoff.
  assert(mesh::wifi::beginStation("mesh","password")==WL_CONNECTED);check_ap();
  sdk_mode=WIFI_STA;
  assert(mesh::wifi::applyProtocolMask(WIFI_IF_STA)==ESP_OK&&lr&&storage_ram);
 }else if(test=="bridge"){
  Bridge bridge;sdk_mode=WIFI_AP_STA;WiFi.facade_started=false;
  assert(mesh::wifi::accessPointCompatibleWithLongRange());
  bridge.begin();assert(!bridge._initialized&&!sdk_active&&init_calls==0);
  assert(sdk_mode==WIFI_AP_STA&&station_writes==0&&storage_writes==0);check_ap();
  // Saved raw intent is unchanged; the normal retry can start after AP stops.
  sdk_mode=WIFI_STA;bridge.begin();
  assert(bridge._initialized&&sdk_active&&lr&&storage_ram&&!nvs_lr&&rate_calls==1);
  assert(!mesh::wifi::accessPointCompatibleWithLongRange());
  bridge.begin();assert(mesh::wifi::longRangeOwners().load()==mesh::wifi::kLongRangeBridgeOwner);
  failure=2;mode_reads=0;
  assert(mesh::wifi::applyProtocolMask(WIFI_IF_STA)!=ESP_OK&&lr);
  failure=0;mode_reads=0;
  assert(mesh::wifi::bridgeEspNowChannel().load()==1);
  bridge.end();assert(!sdk_active&&mesh::wifi::bridgeEspNowChannel().load()==0);
  bridge.end();assert(mesh::wifi::accessPointCompatibleWithLongRange());
  sdk_mode=WIFI_AP_STA;mesh::wifi::applyAccessPointProtocolMask();
  bridge.begin();assert(!bridge._initialized);check_ap();
  bridge.prefs.bridge_format=mesh::bridge::ESPNOW_FORMAT_WRAPPED;
  bridge.begin();assert(bridge._initialized&&mesh::wifi::accessPointCompatibleWithLongRange());check_ap();
  bridge.end();assert(mesh::wifi::accessPointCompatibleWithLongRange());
 }else if(test=="radio"){
  ESPNOWRadio radio;sdk_mode=WIFI_AP_STA;WiFi.facade_started=false;
  radio.init();assert(!radio.isEnabled()&&!sdk_active&&init_calls==0);
  assert(sdk_mode==WIFI_AP_STA&&station_writes==0&&storage_writes==0);check_ap();
  sdk_mode=WIFI_STA;radio.init();
  assert(radio.isEnabled()&&sdk_active&&lr&&storage_ram&&!nvs_lr);
  assert(!mesh::wifi::accessPointCompatibleWithLongRange());
  radio.init();assert(mesh::wifi::longRangeOwners().load()==mesh::wifi::kLongRangeRadioOwner);
#if defined(MESH_ESPNOW_RADIO) && MESH_ESPNOW_RADIO
  assert(rate_calls==1);
#endif
  radio.end();assert(!sdk_active&&wake_refs==0);
  radio.end();assert(mesh::wifi::accessPointCompatibleWithLongRange());
  sdk_mode=WIFI_AP_STA;mesh::wifi::applyAccessPointProtocolMask();
  radio.init();assert(!radio.isEnabled());check_ap();
 }else if(test=="failures"){
  for(int stage:{1,2,4,5,6,7}){
   reset_policy();sdk_mode=WIFI_AP_STA;failure=stage;
   assert(mesh::wifi::applyProtocolMask(WIFI_IF_STA)!=ESP_OK);
   assert(!lr); // no failure path writes LR into the live AP
  }
  reset_policy();failure=3;
  assert(mesh::wifi::applyProtocolMask(WIFI_IF_STA)!=ESP_OK);
  assert(station_writes==0&&!lr&&!nvs_lr);
  reset_policy();driver_initialized=false;
  assert(mesh::wifi::checkLongRangeRadioStart()==ESP_OK);
  reset_policy();failure=1;
  assert(mesh::wifi::checkLongRangeRadioStart()!=ESP_OK);
  for(int stage:{1,3,4}){
   reset_policy();failure=stage;Bridge bridge;bridge.begin();
   assert(!bridge._initialized&&!sdk_active&&!nvs_lr);
   assert(mesh::wifi::accessPointCompatibleWithLongRange());
  }
 }else if(test=="race"){
  // A second owner enables AP during the STA write. Post-write repair must
  // preserve standard beacons and reject a claimed strict-LR success.
  ap_during_station_write=true;
  assert(mesh::wifi::applyProtocolMask(WIFI_IF_STA)==ESP_OK);check_ap();
  reset_policy();ap_during_station_write=true;
  assert(mesh::wifi::applyStationProtocolMask(15,true)!=ESP_OK);check_ap();
  assert(!nvs_lr);
  for(int stage:{2,5}){
   reset_policy();ap_during_station_write=true;failure=stage;
   assert(mesh::wifi::applyStationProtocolMask(15,true)!=ESP_OK);
   assert(!lr&&!nvs_lr); // uncertain/failed AP repair cannot leave a new LR flag
  }
 }else if(test=="stop"){
  sdk_mode=WIFI_AP_STA;
  assert(mesh::wifi::applyProtocolMask(WIFI_IF_STA)==ESP_OK);check_ap();
  mesh::wifi::stopTemporaryAccessPointRadio(false);check_ap();assert(!lr);
  sdk_mode=WIFI_STA;
  mesh::wifi::stopTemporaryAccessPointRadio(false);
  assert(lr&&storage_ram&&!nvs_lr);
 }else if(test=="web_teardown"){
  for(bool idle:{false,true}){
   for(int stage:{3,8}){
    reset_policy();sdk_mode=WIFI_AP_STA;WiFi.state=WL_CONNECTED;failure=stage;
    WebTeardown web;
    if(idle)web.idle();else web.promote();
    assert(web._mode==WebTeardown::MODE_SETUP&&web._was_setup_ap);
    // A failed restore keeps setup ownership flags so a later tick can retry.
    failure=0;
    if(idle)web.idle();else web.promote();
    assert(web._mode==WebTeardown::MODE_LAN&&!web._was_setup_ap&&lr&&storage_ram);
   }
  }
 }else return 2;
}
"""


def build_and_run(scenario, *, mutation=None, full=True):
    policy = (ROOT / 'src/helpers/esp32/WiFiRadioPolicy.h').read_text()
    radio_header = (ROOT / 'src/helpers/esp32/ESPNOWRadio.h').read_text()
    radio_header = radio_header[:radio_header.index('#if ESPNOW_DEBUG_LOGGING')]
    radio_header = re.sub(r'^\s*#(?:include|pragma).*$', '', radio_header, flags=re.MULTILINE)
    radio_header = radio_header.replace(' : public mesh::Radio', '').replace(' override', '')
    radio = (ROOT / 'src/helpers/esp32/ESPNOWRadio.cpp').read_text()
    radio = re.sub(r'^\s*#include.*$', '', radio, flags=re.MULTILINE)
    bridge_source = (ROOT / 'src/helpers/bridges/ESPNowBridge.cpp').read_text()
    bridge = method(bridge_source, 'static void stopBridgeWiFiIfUnused(') + '\n'
    bridge += '\n'.join(method(bridge_source, f'void ESPNowBridge::{name}(')
                        .replace('ESPNowBridge::', 'Bridge::') for name in ('begin', 'end'))
    stop = method((ROOT / 'src/helpers/esp32/WiFiAccessPointPolicy.h').read_text(),
                  'inline void stopTemporaryAccessPointRadio(')
    web_source = (ROOT / 'src/helpers/esp32/WebConfigServer.cpp').read_text()
    tick = method(web_source, 'void WebConfigServer::tick(')
    setup = method(tick, 'if (_mode == MODE_SETUP && _retry_saved_wifi_in_setup && _wifi_ssid[0])')
    promotion = method(setup, 'if (WiFi.status() == WL_CONNECTED)')
    idle_outer = method(tick, 'if (_mode == MODE_SETUP && WiFi.softAPgetStationNum() == 0 &&')
    idle = method(idle_outer, 'if (_retry_saved_wifi_in_setup && _wifi_ssid[0])')
    if mutation == 'unguarded_station':
        policy = policy.replace('if (mode & WIFI_MODE_AP) protocols = kAccessPointProtocolMask;', '')
        policy = policy.replace('if (mode & WIFI_MODE_AP) {\n    result = applyAccessPointProtocolMask();',
                                'if (false) {\n    result = applyAccessPointProtocolMask();')
    elif mutation == 'raw_start':
        bridge = bridge.replace('raw_format && mesh::wifi::checkLongRangeRadioStart() != ESP_OK', 'false')
        policy = policy.replace('if ((mode & WIFI_MODE_AP) && require_lr)', 'if (false)')
        policy = policy.replace('if (mode & WIFI_MODE_AP) protocols = kAccessPointProtocolMask;', '')
        policy = policy.replace('if (mode & WIFI_MODE_AP) {\n    result = applyAccessPointProtocolMask();',
                                'if (false) {\n    result = applyAccessPointProtocolMask();')
    elif mutation == 'primary_start':
        radio = radio.replace('mesh::wifi::checkLongRangeRadioStart() != ESP_OK', 'false')
    elif mutation == 'flash_storage':
        policy = policy.replace('result = esp_wifi_set_storage(WIFI_STORAGE_RAM);', 'result = ESP_OK;')
    elif mutation == 'no_restore':
        stop = stop.replace('applyProtocolMask(WIFI_IF_STA);', '')
    elif mutation == 'web_no_restore':
        promotion = promotion.replace('|| mesh::wifi::applyProtocolMask(WIFI_IF_STA) != ESP_OK', '')
        idle = idle.replace('|| mesh::wifi::applyProtocolMask(WIFI_IF_STA) != ESP_OK', '')
    with tempfile.TemporaryDirectory() as temporary:
        folder = Path(temporary)
        (folder / 'helpers/esp32').mkdir(parents=True)
        (folder / 'helpers/esp32/WiFiRadioPolicy.h').write_text(policy)
        for name in ('esp_err.h', 'esp_wifi.h', 'WiFi.h', 'Preferences.h'):
            (folder / name).write_text('#pragma once\n')
        fixture = (ROOT / 'test/fixtures/wifi_ap_shared_lr_sdk.h').read_text()
        source = fixture + '\n#include <helpers/esp32/WiFiRadioPolicy.h>\n'
        source += '#include <helpers/esp32/WiFiStationPolicy.h>\n'
        source += 'namespace mesh {namespace wifi {\n' + stop + '\n}}\n'
        web = WEB_CLASS.replace('@PROMOTE@', promotion).replace('@IDLE@', idle)
        source += radio_header + radio + BRIDGE_CLASS + bridge + web + CHECKS
        (folder / 'test.cpp').write_text(source)
        binary = folder / 'test'
        flags = ['-DMESH_PRIMARY_ESPNOW=1', '-DESP_ARDUINO_VERSION_MAJOR=2', '-DESP_IDF_VERSION=40407']
        if full:
            flags += ['-DMESH_ESPNOW_RADIO=1', '-DWITH_ESPNOW_BRIDGE=1']
        compiled = subprocess.run([os.environ.get('CXX', 'g++'), '-std=c++17',
            '-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-fno-pie', '-no-pie',
            '-I', str(folder), '-I', str(ROOT/'src'), *flags, str(folder/'test.cpp'), '-o', str(binary)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if compiled.returncode:
            raise AssertionError(compiled.stderr)
        return subprocess.run([str(binary), scenario], text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)


class WifiApLongRangeSafetyTests(unittest.TestCase):
    def assert_pass(self, scenario, **kwargs):
        result = build_and_run(scenario, **kwargs)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_constrained_setup_retry_and_handoff_preserve_ap(self):
        self.assert_pass('station')
        source = (ROOT/'src/helpers/esp32/WebConfigServer.cpp').read_text()
        tick = method(source, 'void WebConfigServer::tick(')
        batch = method(source, 'void WebConfigServer::drainBatch(')
        self.assertIn('mesh::wifi::beginStation(_wifi_ssid, _wifi_password);', tick)
        self.assertIn('mesh::wifi::beginStation(_wifi_ssid, _wifi_password);', batch)

    def test_raw_bridge_defers_then_resumes_without_persisting_lr(self):
        self.assert_pass('bridge')

    def test_primary_restart_defers_for_sdk_ap_even_with_stale_facade(self):
        self.assert_pass('radio')
        self.assert_pass('radio', full=False)

    def test_protocol_read_write_and_storage_failures_do_not_claim_success(self):
        self.assert_pass('failures')

    def test_ap_start_during_station_write_is_repaired(self):
        self.assert_pass('race')

    def test_lr_restore_waits_for_actual_ap_stop(self):
        self.assert_pass('stop')

    def test_webconfig_teardown_failure_retains_state_for_retry(self):
        self.assert_pass('web_teardown')

    def test_negative_controls_fail_at_runtime(self):
        for mutation, scenario in [('unguarded_station', 'station'), ('raw_start', 'bridge'),
                                   ('primary_start', 'radio'), ('flash_storage', 'bridge'),
                                   ('no_restore', 'stop'), ('web_no_restore', 'web_teardown')]:
            with self.subTest(mutation=mutation):
                result = build_and_run(scenario, mutation=mutation)
                self.assertNotEqual(result.returncode, 0, 'regression was not detected')
                self.assertIn('Assertion', result.stderr)


if __name__ == '__main__':
    unittest.main()
