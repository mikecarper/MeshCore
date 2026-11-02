"""Execute Arduino credential staging and the shared station policy offline."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "test/fixtures/wifi_station_security"
SDK = Path(os.environ.get("PLATFORMIO_CORE_DIR", Path.home() / ".platformio")) / "packages/framework-arduinoespressif32"

WIFI = r'''
#pragma once
#include <cassert>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>
#define ESP_ARDUINO_VERSION_MAJOR 2
#define ESP_ARDUINO_VERSION_MINOR 0
#define ESP_ARDUINO_VERSION_PATCH 17
#define CONFIG_ESP32_WIFI_ENABLE_WPA3_SAE 1
#define CONFIG_ESP_WIFI_ENABLE_WPA3_SAE 1
using esp_err_t = int;
constexpr int ESP_OK=0, ESP_FAIL=-1, ESP_ERR_INVALID_ARG=0x102;
constexpr int WIFI_MODE_STA=1;
enum wifi_interface_t { WIFI_IF_STA=0 };
constexpr int ESP_IF_WIFI_STA=WIFI_IF_STA;
enum wl_status_t { WL_IDLE_STATUS=0, WL_NO_SSID_AVAIL=1, WL_CONNECTED=3,
 WL_CONNECT_FAILED=4, WL_DISCONNECTED=6 };
enum wifi_auth_mode_t { WIFI_AUTH_OPEN=0, WIFI_AUTH_WPA_PSK=2, WIFI_AUTH_WPA2_PSK=3,
 WIFI_AUTH_WPA2_WPA3_PSK=7, WIFI_AUTH_WPA3_PSK=6 };
enum wifi_scan_method_t { WIFI_FAST_SCAN, WIFI_ALL_CHANNEL_SCAN };
enum wifi_sort_method_t { WIFI_CONNECT_AP_BY_SIGNAL, WIFI_CONNECT_AP_BY_SECURITY };
#ifndef TEST_NO_SAE_TYPES
enum wifi_sae_pwe_method_t { WPA3_SAE_PWE_UNSPECIFIED, WPA3_SAE_PWE_HUNT_AND_PECK,
 WPA3_SAE_PWE_HASH_TO_ELEMENT, WPA3_SAE_PWE_BOTH };
#endif
struct wifi_sta_config_t {
 uint8_t ssid[32]; uint8_t password[64]; wifi_scan_method_t scan_method;
 bool bssid_set; uint8_t bssid[6]; uint8_t channel; uint16_t listen_interval;
 wifi_sort_method_t sort_method;
 struct {int8_t rssi; wifi_auth_mode_t authmode;} threshold;
 struct {bool capable; bool required;} pmf_cfg;
 uint8_t failure_retry_cnt;
#ifndef TEST_NO_SAE_TYPES
 wifi_sae_pwe_method_t sae_pwe_h2e;
#if defined(SAE_H2E_IDENTIFIER_LEN)
 uint8_t sae_h2e_identifier[SAE_H2E_IDENTIFIER_LEN];
#endif
#endif
};
struct wifi_config_t {wifi_sta_config_t sta;};
struct wifi_ap_record_t {uint8_t ssid[33];uint8_t bssid[6];uint8_t primary;};
inline wifi_config_t sdk_config{};
inline std::vector<wifi_config_t> connected_configs;
inline std::vector<std::string> calls;
inline int get_calls=0,set_calls=0,disconnect_calls=0,ip_calls=0,enable_calls=0;
inline int fail_get=0,fail_set=0;
inline int fail_enable_n=0,fail_disconnect_n=0;
inline bool fail_enable=false,fail_disconnect=false,fail_ip=false,fail_connect=false;
inline bool sdk_associated=false,preserve_cached_status=false;
inline bool constrained=false,blocked=false;
inline wl_status_t sdk_status=WL_DISCONNECTED;
inline constexpr int WIFI_SCAN_RUNNING=-1;
inline esp_err_t esp_wifi_get_config(wifi_interface_t, wifi_config_t* result) {
 calls.emplace_back("get");
 if (++get_calls==fail_get) return ESP_FAIL;
 *result=sdk_config;return ESP_OK;
}
inline esp_err_t esp_wifi_set_config(wifi_interface_t, const wifi_config_t* value) {
 calls.emplace_back("set");
 if (++set_calls==fail_set) return ESP_FAIL;
 sdk_config=*value;return ESP_OK;
}
inline esp_err_t esp_wifi_disconnect() {
 calls.emplace_back("disconnect");++disconnect_calls;
 if (fail_disconnect||disconnect_calls==fail_disconnect_n) return ESP_FAIL;
 sdk_associated=false;
 if(!preserve_cached_status)sdk_status=WL_DISCONNECTED;
 return ESP_OK;
}
inline esp_err_t esp_wifi_connect() {
 calls.emplace_back("connect");connected_configs.push_back(sdk_config);
 if (fail_connect) return ESP_FAIL;
 if(!preserve_cached_status)sdk_status=WL_IDLE_STATUS;
 return ESP_OK;
}
inline esp_err_t esp_wifi_sta_get_ap_info(wifi_ap_record_t* record) {
 calls.emplace_back("ap-info");
 if(!sdk_associated)return ESP_FAIL;
 *record={};memcpy(record->ssid,sdk_config.sta.ssid,32);
 memcpy(record->bssid,sdk_config.sta.bssid,6);
 record->primary=sdk_config.sta.channel?sdk_config.sta.channel:6;
 return ESP_OK;
}
inline esp_err_t set_esp_interface_ip(int) {
 calls.emplace_back("ip");++ip_calls;return fail_ip?ESP_FAIL:ESP_OK;
}
#define log_e(...) ((void)0)
class WiFiSTAClass {
 public:
 static bool _useStaticIp;
 static wifi_auth_mode_t _minSecurity;
 static wifi_scan_method_t _scanMethod;
 static wifi_sort_method_t _sortMethod;
 struct Network {std::string ssid;int channel;int rssi;uint8_t bssid[6];};
 std::vector<Network> networks;
 bool autoreconnect=true;
 bool enableSTA(bool) {calls.emplace_back("enable");++enable_calls;return !fail_enable&&enable_calls!=fail_enable_n;}
 wl_status_t status() const {return sdk_status;}
 wl_status_t begin(const char*,const char*,int32_t=0,const uint8_t* = nullptr,bool=true);
 wl_status_t begin();
 bool reconnect();
 int getMode() const {return WIFI_MODE_STA;}
 bool disconnect(bool=false,bool=false) {return esp_wifi_disconnect()==ESP_OK;}
 void setAutoReconnect(bool value) {autoreconnect=value;}
 void setScanMethod(wifi_scan_method_t value) {calls.emplace_back("scan-method");_scanMethod=value;}
 void setSortMethod(wifi_sort_method_t value) {calls.emplace_back("sort-method");_sortMethod=value;}
 int scanComplete() {return -2;}
 void scanDelete() {}
 int scanNetworks(bool,bool,bool,int,int channel,const char*,const uint8_t*) {
  assert(channel==6);return static_cast<int>(networks.size());
 }
 int channel(int index=-1) {return index<0?sdk_config.sta.channel:networks.at(index).channel;}
 const std::string& SSID(int index) {return networks.at(index).ssid;}
 int RSSI(int index) {return networks.at(index).rssi;}
 const uint8_t* BSSID(int index) {return networks.at(index).bssid;}
};
inline bool WiFiSTAClass::_useStaticIp=false;
inline wifi_auth_mode_t WiFiSTAClass::_minSecurity=WIFI_AUTH_WPA2_PSK;
inline wifi_scan_method_t WiFiSTAClass::_scanMethod=WIFI_FAST_SCAN;
inline wifi_sort_method_t WiFiSTAClass::_sortMethod=WIFI_CONNECT_AP_BY_SIGNAL;
inline WiFiSTAClass WiFi;
'''

RADIO = r'''
#pragma once
#include <WiFi.h>
namespace mesh {namespace wifi {
inline bool espNowChannelConstrained() {return constrained;}
inline esp_err_t applyProtocolMask(wifi_interface_t) {return ESP_OK;}
inline esp_err_t restoreEspNowChannel() {return ESP_OK;}
inline uint8_t activeEspNowChannel() {return 6;}
}}
'''

DIAGNOSTICS = r'''
#pragma once
#include <helpers/WiFiConnectionDiagnostics.h>
namespace mesh {namespace wifi {
inline ConnectionPhase last_phase=ConnectionPhase::Idle;
inline void beginStationDiagnostics(const char*) {last_phase=ConnectionPhase::Connecting;}
inline void noteStationDiagnosticPhase(ConnectionPhase phase) {last_phase=phase;}
}}
'''

WIRELESS = r'''
#pragma once
namespace mesh {namespace wireless {
enum {WiFi=1};
struct Control {bool blocked(int) const {return ::blocked;}};
inline Control& control() {static Control value;return value;}
}}
'''

FALLBACK = r'''
#include <WiFi.h>
#include "production_sta_begin.inc"
#include <helpers/esp32/WiFiStationSecurity.h>
int main() {
 const uint8_t bssid[]={1,2,3,4,5,6};
 const std::string keys[]={"test-password",std::string(64,'a')};
 for (const auto& key:keys) {
  connected_configs.clear();sdk_status=WL_DISCONNECTED;
  assert(mesh::wifi::beginStationWithSecurity("iot",key.c_str(),6,bssid)!=WL_CONNECT_FAILED);
  assert(connected_configs.size()==1);
  const auto& config=connected_configs.front().sta;
  assert(memcmp(config.ssid,"iot",4)==0&&memcmp(config.password,key.data(),key.size())==0);
  assert(config.channel==6&&config.bssid_set&&memcmp(config.bssid,bssid,6)==0);
  assert(config.threshold.authmode==WIFI_AUTH_WPA2_PSK);
  assert(config.pmf_cfg.capable&&!config.pmf_cfg.required);
#if defined(MESH_WIFI_ALL_CHANNEL_SCAN) && MESH_WIFI_ALL_CHANNEL_SCAN
  assert(config.scan_method==WIFI_ALL_CHANNEL_SCAN);
#else
  assert(config.scan_method==WIFI_FAST_SCAN);
#endif
  assert(config.sort_method==WIFI_CONNECT_AP_BY_SIGNAL);
 }
}
'''


def installed_sdk_source():
    path = SDK / "libraries/WiFi/src/WiFiSTA.cpp"
    version = SDK / "cores/esp32/esp_arduino_version.h"
    if not path.is_file() or not version.is_file():
        return None
    expected = ("#define ESP_ARDUINO_VERSION_MAJOR   2",
                "#define ESP_ARDUINO_VERSION_MINOR   0",
                "#define ESP_ARDUINO_VERSION_PATCH   17")
    if not all(value in version.read_text() for value in expected):
        return None
    source = path.read_text()
    signatures = (
        "static size_t _wifi_strncpy(",
        "static bool sta_config_equal(const wifi_config_t& lhs, const wifi_config_t& rhs)\n{",
        "static void wifi_sta_config(",
        "wl_status_t WiFiSTAClass::begin(const char* ssid, const char *passphrase, int32_t channel, const uint8_t* bssid, bool connect)",
        "wl_status_t WiFiSTAClass::begin()",
        "bool WiFiSTAClass::reconnect()",
    )
    return "\n\n".join(extract_braced(source, signature) for signature in signatures)


class WiFiStationSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="wifi-station-security-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.folder = Path(cls.temporary.name)
        (cls.folder / "helpers/esp32").mkdir(parents=True)
        for name, content in {
            "WiFi.h": WIFI,
            "esp_wifi.h": "#pragma once\n#include <WiFi.h>\n",
            "Arduino.h": "#pragma once\n#include <WiFi.h>\n",
            "esp_idf_version.h": "#pragma once\n#define ESP_IDF_VERSION_VAL(major,minor,patch) (((major)<<16)|((minor)<<8)|(patch))\n#define ESP_IDF_VERSION ESP_IDF_VERSION_VAL(4,4,8)\n",
            "esp_arduino_version.h": "#pragma once\n#include <WiFi.h>\n",
            "helpers/esp32/WiFiRadioPolicy.h": RADIO,
            "helpers/esp32/WiFiStationDiagnostics.h": DIAGNOSTICS,
            "helpers/WirelessControl.h": WIRELESS,
        }.items():
            (cls.folder / name).write_text(content)
        pinned = (FIXTURES / "arduino2_sta_begin.inc").read_text()
        sources = {"pinned": pinned, "identifier": pinned, "idf443": pinned,
                   "explicit-off": pinned, "recovery": pinned, "recovery-off": pinned}
        installed = installed_sdk_source()
        if installed:
            sources["installed"] = installed
            sources["installed-recovery"] = installed
        cls.binaries = {}
        cls.fallback_binaries = {}
        versions = {"old-idf": (4, 4, 2), "disabled-wpa3": (4, 4, 8),
                    "corrupt-idf445": (4, 4, 5), "corrupt-idf502": (5, 0, 2),
                    "corrupt-idf503": (5, 0, 3), "corrupt-idf510": (5, 1, 0)}
        sources.update({label: pinned for label in versions})
        for label, source in sources.items():
            for all_scan in ((False,) if label == "explicit-off" else (False, True)):
                variant = label + ("-all" if all_scan else "")
                folder = cls.folder / variant
                folder.mkdir()
                (folder / "production_sta_begin.inc").write_text(source)
                major, minor, patch = versions.get(label, (4, 4, 3) if label == "idf443" else (4, 4, 8))
                (folder / "esp_idf_version.h").write_text(
                    "#pragma once\n#define ESP_IDF_VERSION_VAL(major,minor,patch) (((major)<<16)|((minor)<<8)|(patch))\n"
                    f"#define ESP_IDF_VERSION ESP_IDF_VERSION_VAL({major},{minor},{patch})\n")
                flags = []
                if label in ("recovery", "installed-recovery"):
                    flags.append("-DMESH_WIFI_RECOVERY=1")
                elif label == "recovery-off":
                    flags.append("-DMESH_WIFI_RECOVERY=0")
                if all_scan:
                    flags.append("-DMESH_WIFI_ALL_CHANNEL_SCAN=1")
                elif label == "explicit-off":
                    flags.append("-DMESH_WIFI_ALL_CHANNEL_SCAN=0")
                fixture = FIXTURES / "station_security.cpp"
                if label in versions:
                    flags.append("-DTEST_NO_SAE_TYPES=1")
                    fixture = folder / "fallback.cpp"
                    fixture.write_text(FALLBACK)
                if label == "disabled-wpa3":
                    (folder / "WiFi.h").write_text(WIFI.replace(
                        "CONFIG_ESP32_WIFI_ENABLE_WPA3_SAE 1", "CONFIG_ESP32_WIFI_ENABLE_WPA3_SAE 0").replace(
                        "CONFIG_ESP_WIFI_ENABLE_WPA3_SAE 1", "CONFIG_ESP_WIFI_ENABLE_WPA3_SAE 0"))
                if label == "identifier":
                    flags.append("-DSAE_H2E_IDENTIFIER_LEN=32")
                binary = folder / "test"
                built = subprocess.run([
                    os.environ.get("CXX", "g++"), "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
                    "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                    "-DESP_PLATFORM=1", "-DWITH_ESPNOW_BRIDGE=1",
                    *flags,
                    "-I", str(folder), "-I", str(cls.folder), "-I", str(ROOT / "src"),
                    str(fixture), "-o", str(binary),
                ], capture_output=True, text=True, timeout=60)
                if built.returncode:
                    raise AssertionError(built.stdout + built.stderr)
                destination = cls.fallback_binaries if label in versions else cls.binaries
                destination[variant] = binary

    def run_scenario(self, scenario):
        for label, binary in self.binaries.items():
            with self.subTest(sdk=label):
                result = subprocess.run([str(binary), scenario], capture_output=True,
                                        text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_first_connect_uses_h2e_and_retains_wpa2_and_open_networks(self):
        self.run_scenario("security")

    def test_fixed_channel_uses_strongest_matching_bssid(self):
        self.run_scenario("fixed-channel")

    def test_stage_get_set_ip_and_connect_failures_never_join_stale_credentials(self):
        self.run_scenario("failures")

    def test_already_connected_is_idempotent_and_credentials_are_not_confused(self):
        self.run_scenario("idempotent")

    def test_invalid_and_disabled_requests_never_start_a_join(self):
        self.run_scenario("preflight")

    def test_driver_reconnect_retains_h2e_selection(self):
        self.run_scenario("reconnect")

    def test_preview_scan_policy_is_staged_before_join_and_keeps_security_and_routes(self):
        self.run_scenario("scan-policy")

    def test_cached_arduino_status_cannot_skip_join_or_invent_a_policy_failure(self):
        self.run_scenario("cached-status")

    def test_modern_sdk_does_not_reuse_a_password_identifier(self):
        for label in ("identifier", "identifier-all"):
            result = subprocess.run([str(self.binaries[label]), "identifier"],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_old_disabled_and_affected_sdks_compile_without_sae_fields(self):
        for label, binary in self.fallback_binaries.items():
            with self.subTest(sdk=label):
                result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
