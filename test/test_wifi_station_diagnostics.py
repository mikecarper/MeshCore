"""Run real ESP32 event diagnostics and station policy without MQTT or hardware."""

from pathlib import Path
import os
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]

ARDUINO = r'''
#pragma once
#include <cassert>
#include <cstdint>
inline unsigned lock_depth=0,locks_entered=0,locks_exited=0;
inline uint32_t fake_now=1000;
using portMUX_TYPE=int;
#define portMUX_INITIALIZER_UNLOCKED 0
inline void enterCritical(portMUX_TYPE*){assert(lock_depth==0);++lock_depth;++locks_entered;}
inline void exitCritical(portMUX_TYPE*){assert(lock_depth==1);--lock_depth;++locks_exited;}
#define portENTER_CRITICAL(lock) enterCritical(lock)
#define portEXIT_CRITICAL(lock) exitCritical(lock)
inline uint32_t millis(){assert(lock_depth==0);return fake_now;}
'''

WIFI = r'''
#pragma once
#include <Arduino.h>
#include <string>
#include <vector>
enum wl_status_t {WL_IDLE_STATUS,WL_CONNECTED,WL_NO_SSID_AVAIL,WL_CONNECT_FAILED};
enum {ARDUINO_EVENT_WIFI_STA_CONNECTED=1,ARDUINO_EVENT_WIFI_STA_GOT_IP,
 ARDUINO_EVENT_WIFI_STA_DISCONNECTED,ARDUINO_EVENT_WIFI_STA_LOST_IP,ARDUINO_EVENT_WIFI_STA_STOP};
constexpr int WIFI_SCAN_RUNNING=-1;
struct StationConnected {uint8_t ssid[32];uint8_t ssid_len;};
struct StationDisconnected {uint8_t ssid[32];uint8_t ssid_len;uint8_t reason;};
struct arduino_event_t {int event_id;struct {
 StationConnected wifi_sta_connected;
 StationDisconnected wifi_sta_disconnected;
} event_info;};
using wifi_event_id_t=unsigned;
struct FakeWiFi {
 using Callback=void(*)(arduino_event_t*);
 struct Network {std::string ssid;int channel;int rssi;};
 Callback callback=nullptr;
 unsigned listeners=0,begins=0;
 bool autoreconnect=true,emit_connected_on_begin=false;
 int scan_state=-2,join_channel=0,selected_bssid=0;
 wl_status_t current_status=WL_IDLE_STATUS;
 std::vector<Network> networks;
 wifi_event_id_t onEvent(Callback value){assert(lock_depth==0);++listeners;callback=value;return listeners;}
 void setAutoReconnect(bool value){assert(lock_depth==0);autoreconnect=value;}
 wl_status_t begin(const char* ssid,const char*,int channel=0,const uint8_t* bssid=nullptr){
  assert(lock_depth==0&&listeners==1&&callback);++begins;join_channel=channel;
  selected_bssid=bssid?*bssid:0;
  if(emit_connected_on_begin){
   arduino_event_t event{};event.event_id=ARDUINO_EVENT_WIFI_STA_CONNECTED;
   auto& data=event.event_info.wifi_sta_connected;data.ssid_len=std::char_traits<char>::length(ssid);
   std::char_traits<char>::copy(reinterpret_cast<char*>(data.ssid),ssid,data.ssid_len);
   callback(&event);
  }
  return current_status;
 }
 int scanComplete(){assert(lock_depth==0);return scan_state;}
 void scanDelete(){assert(lock_depth==0);}
 int scanNetworks(bool,bool,bool,int,int channel,const char*,const uint8_t*){
  assert(lock_depth==0&&channel==6);return networks.size();
 }
 int channel(int index=-1){assert(lock_depth==0);return index<0?6:networks.at(index).channel;}
 const std::string& SSID(int index){assert(lock_depth==0);return networks.at(index).ssid;}
 int RSSI(int index){assert(lock_depth==0);return networks.at(index).rssi;}
 const uint8_t* BSSID(int index){assert(lock_depth==0);static uint8_t bssid[6];bssid[0]=index+1;return bssid;}
 wl_status_t status(){assert(lock_depth==0);return current_status;}
 void disconnect(bool,bool){assert(lock_depth==0);current_status=WL_IDLE_STATUS;}
};
inline FakeWiFi WiFi;
'''

RADIO_POLICY = r'''
#pragma once
#include <Arduino.h>
constexpr int ESP_OK=0,WIFI_IF_STA=0;
inline bool constrained=false;
inline int protocol_result=0;
namespace mesh {namespace wifi {
inline bool espNowChannelConstrained(){assert(lock_depth==0);return constrained;}
inline int applyProtocolMask(int){assert(lock_depth==0);return protocol_result;}
inline int restoreEspNowChannel(){assert(lock_depth==0);return 0;}
inline uint8_t activeEspNowChannel(){assert(lock_depth==0);return 6;}
}}
'''

WIRELESS_CONTROL = r'''
#pragma once
#include <Arduino.h>
inline bool blocked=false;
namespace mesh {namespace wireless {
enum {WiFi=1};
struct Control {bool blocked(int)const{assert(lock_depth==0);return ::blocked;}};
inline Control& control(){static Control instance;return instance;}
}}
'''


class WiFiStationDiagnosticsIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="wifi-diagnostics-integration-")
        cls.folder = Path(cls.temporary.name)
        (cls.folder / "helpers/esp32").mkdir(parents=True)
        for name, content in {
            "Arduino.h": ARDUINO,
            "WiFi.h": WIFI,
            "helpers/esp32/WiFiRadioPolicy.h": RADIO_POLICY,
            "helpers/WirelessControl.h": WIRELESS_CONTROL,
        }.items():
            (cls.folder / name).write_text(content)
        cls.binary = cls.folder / "test"
        built = subprocess.run([
            os.environ.get("CXX", "g++"), "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
            "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
            "-DESP_PLATFORM=1", "-DWITH_ESPNOW_BRIDGE=1",
            "-I", str(cls.folder), "-I", str(ROOT / "src"),
            str(ROOT / "test/fixtures/wifi_station_diagnostics.cpp"), "-o", str(cls.binary),
        ], capture_output=True, text=True, timeout=60)
        if built.returncode:
            cls.temporary.cleanup()
            raise AssertionError(built.stdout + built.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_scenario(self, scenario):
        result = subprocess.run([str(self.binary), scenario], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_listener_is_permanent_registered_before_join_and_mqtt_independent(self):
        self.run_scenario("registration")

    def test_matching_network_association_and_ip_events(self):
        self.run_scenario("events")

    def test_remote_disassociation_cannot_leave_a_stale_connected_report(self):
        self.run_scenario("remote-disconnect")

    def test_original_auth_failure_survives_local_disconnect_and_ap_recovery(self):
        self.run_scenario("recovery")

    def test_preflight_failures_never_claim_the_driver_joined(self):
        self.run_scenario("preflight")

    def test_timeout_without_driver_event_does_not_invent_a_reason(self):
        self.run_scenario("timeout")

    def test_hints_match_network_and_preserve_complete_recovery_address(self):
        self.run_scenario("hint")


if __name__ == "__main__":
    unittest.main()
