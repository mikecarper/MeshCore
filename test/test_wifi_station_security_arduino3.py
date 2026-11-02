"""Run exact Arduino 3.3.12 station methods against a deferred SDK event model."""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import test_wifi_station_security as older

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/wifi_station_security/arduino3312_sta_begin.inc"
PIN = "0881c617e099b754bd984bfe21a84c2058ea47166dcdac5be3a75cb85f5ed739"

# Reuse the SDK data structures and fault seams, not the Arduino 2 facade.
WIFI = older.WIFI[:older.WIFI.index("class WiFiSTAClass {")]
WIFI = WIFI.replace("#define ESP_ARDUINO_VERSION_MAJOR 2", """#ifdef TEST_LEGACY_CACHED_STATUS
#define ESP_ARDUINO_VERSION_MAJOR 2
#else
#define ESP_ARDUINO_VERSION_MAJOR 3
#endif""").replace("VERSION_MINOR 0", "VERSION_MINOR 3").replace("VERSION_PATCH 17", "VERSION_PATCH 12")
WIFI = WIFI.replace("inline bool sdk_associated=false", "inline bool sdk_connect_pending=false;\ninline bool sdk_associated=false")
WIFI = WIFI.replace("sdk_associated=false;\n", "sdk_associated=false;sdk_connect_pending=false;\n")
WIFI = WIFI.replace("if (fail_connect) return ESP_FAIL;", "if (fail_connect || sdk_connect_pending) return ESP_FAIL;\n sdk_connect_pending=true;")
WIFI += r'''
#define log_w(...) ((void)0)
constexpr int ESP_NETIF_STARTED_BIT=1,ESP_NETIF_HAS_STATIC_IP_BIT=2;
inline unsigned long fake_millis=0;
inline unsigned long millis(){return fake_millis;}
inline void delay(unsigned ms){fake_millis+=ms;}
inline const char* esp_err_to_name(int){return "SDK error";}
class STAClass {
public:
 void* _esp_netif=this;
 wifi_auth_mode_t _minSecurity=WIFI_AUTH_WPA2_PSK;
 wifi_scan_method_t _scanMethod=WIFI_FAST_SCAN;
 wifi_sort_method_t _sortMethod=WIFI_CONNECT_AP_BY_SIGNAL;
 bool begin(bool=false);
 bool connect();
 bool connect(const char*,const char*,int32_t=0,const uint8_t* =nullptr,bool=true);
 bool disconnect(bool=false,unsigned long=0);
 bool reconnect();
 // IDF's CONNECTED bit and the queued Arduino wl_status are separate. The
 // driver clears the former before dispatching the latter's event handler.
 bool connected() const{return sdk_associated;}
 bool started() const{return true;}
 wl_status_t status() const{return sdk_status;}
 unsigned getStatusBits() const{return ESP_NETIF_STARTED_BIT;}
 bool waitStatusBits(int,unsigned) const{return !fail_enable;}
 bool config(){return set_esp_interface_ip(ESP_IF_WIFI_STA)==ESP_OK;}
}inline STA;
class WiFiSTAClass {
public:
 wl_status_t begin(const char*,const char*,int32_t=0,const uint8_t* =nullptr,bool=true);
 wl_status_t begin();
 bool reconnect();
 wl_status_t status() const{return STA.status();}
 bool enableSTA(bool){++enable_calls;return !fail_enable;}
 void setScanMethod(wifi_scan_method_t method){STA._scanMethod=method;}
 void setSortMethod(wifi_sort_method_t method){STA._sortMethod=method;}
}inline WiFi;
'''

HARNESS = r'''
#include <WiFi.h>
#include "actual_arduino3.inc"
#include <helpers/esp32/WiFiStationSecurity.h>
static void reset() {
 sdk_config={};sdk_status=WL_DISCONNECTED;sdk_associated=sdk_connect_pending=false;
 preserve_cached_status=false;get_calls=set_calls=disconnect_calls=0;
 fail_get=fail_set=0;fail_connect=fail_ip=fail_enable=fail_disconnect=false;
 connected_configs.clear();calls.clear();STA._scanMethod=WIFI_FAST_SCAN;
 mesh::wifi::last_phase=mesh::wifi::ConnectionPhase::Connecting;
}
static void check(const std::string& ssid,const std::string& key,const uint8_t* bssid) {
 assert(connected_configs.size()==1);
 const auto& c=connected_configs.front().sta;
 assert(memcmp(c.ssid,ssid.data(),ssid.size())==0);
 assert(memcmp(c.password,key.data(),key.size())==0);
 assert(c.threshold.authmode==(key.empty()?WIFI_AUTH_OPEN:WIFI_AUTH_WPA2_PSK));
 assert(c.threshold.rssi==-127&&c.pmf_cfg.capable&&!c.pmf_cfg.required);
 assert(c.sae_pwe_h2e==WPA3_SAE_PWE_BOTH);
 assert(c.scan_method==WIFI_ALL_CHANNEL_SCAN&&c.sort_method==WIFI_CONNECT_AP_BY_SIGNAL);
 assert(c.failure_retry_cnt==1);
 assert(c.bssid_set==bool(bssid)&&c.channel==(bssid?6:0));
 if(bssid)assert(memcmp(c.bssid,bssid,6)==0);
}
int main(int argc,char** argv) {
 assert(argc==2);const std::string scenario=argv[1];
 const uint8_t mac[]={1,2,3,4,5,6};
 if(scenario=="stage") {
  for(const std::string& key:{std::string("password-key"),std::string(64,'a'),std::string()}) {
   for(bool fixed:{false,true}) {
    reset();const std::string ssid(32,'s');
    mesh::wifi::beginStationWithSecurity(ssid.c_str(),key.c_str(),fixed?6:0,fixed?mac:nullptr);
    check(ssid,key,fixed?mac:nullptr);
   }
  }
 }else if(scenario=="cached") {
  for(wl_status_t status:{WL_CONNECTED,WL_CONNECT_FAILED}) {
   reset();sdk_status=status;preserve_cached_status=true;
   const auto result=mesh::wifi::beginStationWithSecurity("iot","password-key");
   assert(result==(status==WL_CONNECTED?WL_IDLE_STATUS:status));
   assert(sdk_connect_pending);
   assert(mesh::wifi::last_phase!=mesh::wifi::ConnectionPhase::PolicyFailed);
   check("iot","password-key",nullptr);
  }
 }else if(scenario=="healthy") {
  reset();mesh::wifi::beginStationWithSecurity("iot","password-key",6,mac);
  sdk_status=WL_CONNECTED;sdk_associated=true;sdk_connect_pending=false;
  connected_configs.clear();set_calls=disconnect_calls=0;
  assert(mesh::wifi::beginStationWithSecurity("iot","password-key",6,mac)==WL_CONNECTED);
  assert(connected_configs.empty()&&set_calls==0&&disconnect_calls==0);
  mesh::wifi::beginStationWithSecurity("iot","changed-key",6,mac);
  check("iot","changed-key",mac);
  assert(disconnect_calls==1);
 }else if(scenario=="failures") {
  for(int nth=1;nth<=3;++nth) {
   reset();fail_set=nth;
   assert(mesh::wifi::beginStationWithSecurity("iot","password-key")==WL_CONNECT_FAILED);
   assert(connected_configs.empty());
  }
 }else assert(false);
}
'''


class Arduino3StationSecurityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = FIXTURE.read_text(encoding="utf-8")
        if hashlib.sha256(source.encode()).hexdigest() != PIN:
            raise AssertionError("Reviewed Arduino 3.3.12 source witness changed")
        cls.temporary = tempfile.TemporaryDirectory(prefix="wifi-arduino3-security-")
        cls.addClassCleanup(cls.temporary.cleanup)
        root = Path(cls.temporary.name)
        (root / "helpers/esp32").mkdir(parents=True)
        for name, text in {
                "WiFi.h": WIFI, "esp_wifi.h": "#pragma once\n#include <WiFi.h>\n",
                "esp_arduino_version.h": "#pragma once\n#include <WiFi.h>\n",
                "esp_idf_version.h": "#pragma once\n#define ESP_IDF_VERSION_VAL(a,b,c) (((a)<<16)|((b)<<8)|(c))\n#define ESP_IDF_VERSION ESP_IDF_VERSION_VAL(5,5,5)\n",
                "helpers/esp32/WiFiStationDiagnostics.h": older.DIAGNOSTICS,
                "actual_arduino3.inc": source, "test.cpp": HARNESS}.items():
            (root / name).write_text(text, encoding="utf-8")
        cls.binaries = {}
        for legacy in (False, True):
            binary = root / ("legacy" if legacy else "modern")
            command = [os.environ.get("CXX", "g++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
                       "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                       "-DMESH_WIFI_RECOVERY=1", "-DMESH_WIFI_ALL_CHANNEL_SCAN=1",
                       *(["-DTEST_LEGACY_CACHED_STATUS=1"] if legacy else []),
                       "-I", str(root), "-I", str(ROOT / "src"), str(root / "test.cpp"), "-o", str(binary)]
            built = subprocess.run(command, capture_output=True, text=True, timeout=60)
            if built.returncode:
                raise AssertionError(built.stdout + built.stderr)
            cls.binaries[legacy] = binary

    def run_scenario(self, scenario, legacy=False):
        return subprocess.run([str(self.binaries[legacy]), scenario], capture_output=True, text=True, timeout=10)

    def test_staged_security_credentials_and_fixed_ap_survive_real_noarg_connect(self):
        result = self.run_scenario("stage")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_stale_connected_status_never_starts_two_sdk_joins(self):
        result = self.run_scenario("cached")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        stale = self.run_scenario("cached", legacy=True)
        self.assertNotEqual(stale.returncode, 0, "Arduino 2 status workaround must fail on the modern SDK")

    def test_verified_connection_stays_up_and_changed_key_rejoins(self):
        result = self.run_scenario("healthy")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_config_write_failures_never_join_stale_credentials(self):
        result = self.run_scenario("failures")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
