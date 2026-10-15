"""Execute the actual Companion BLE boot guard and WiFi power-save resolver."""

from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from test_radio_receive_contract import method


ROOT = Path(__file__).resolve().parents[1]


HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <initializer_list>
#include <string>
#include <helpers/BluetoothMac.h>
#include "examples/companion_radio/BluetoothName.h"
#include <helpers/WiFiPowerSave.h>
#include <helpers/esp32/WiFiCompatibility.h>
#include <helpers/CompanionWiFiStatusFormat.h>
#include "examples/companion_radio/CompanionWiFi.h"
#if defined(MESH_TEST_NON_ESP32)
#undef ESP32
#endif

uint32_t now_ms = 100;
uint32_t millis() { return now_ms; }
unsigned identity_calls=0, address_calls=0, begin_calls=0, enable_calls=0;
unsigned register_calls=0, sdk_ps_calls=0;
bool identity_ok=true, register_ok=true, begin_ok=true;
enum class InterfaceType { Bluetooth };
struct CompanionNodePrefs {
  char bluetooth_name[32]={}, node_name[32]="test node";
  uint8_t bluetooth_stealth_mode=0, bluetooth_stealth_peer_type=0;
  uint8_t bluetooth_stealth_peer[6]={};
  uint8_t powersaving_enabled=0, wifi_enabled=0;
};
struct Mesh {
  CompanionNodePrefs prefs;
  bool preference=true;
  CompanionNodePrefs* getNodePrefs() { return &prefs; }
  uint32_t getBLEPin() { return 123456; }
  bool isBluetoothEnabledPreference() { return preference; }
} the_mesh;
struct {
  bool addInterface(InterfaceType, void*) { ++register_calls; return register_ok; }
  bool removeInterface(void*) { return true; }
  bool isEnabled() const { return true; }
} interface_manager;
struct {
  template<typename... T> bool begin(T...) { ++begin_calls; return begin_ok; }
  void enable() { ++enable_calls; }
  const char* beginFailure() const { return "test failure"; }
} bluetooth_interface;
namespace mesh {
struct Logger {
  template<typename... T> void printf(T...) {}
  void println(const char*) {}
} logger;
Logger& usbLoggingPort() { return logger; }
}
bool prepareCompanionBluetoothIdentity(CompanionNodePrefs*) {
  ++identity_calls; return identity_ok;
}
const uint8_t* companionBluetoothAddress(const CompanionNodePrefs*, bool& clear) {
  ++address_calls; clear=false; return nullptr;
}
bool companion_bluetooth_initialized=false;
uint32_t companion_bluetooth_start_at=0;
constexpr uint32_t COMPANION_BLUETOOTH_RETRY_MS=5000;
char companion_bluetooth_start_failure[96]="Bluetooth has not started";
uint8_t companion_bluetooth_session_stealth_mode=0;
#define BLE_NAME_PREFIX "MeshCore "
#define COMPANION_BT_MODEM_SLEEP_AVAILABLE 0

#if defined(COMPANION_EXCLUSIVE_WIFI_BLE)
CompanionTransportMode companion_transport_boot_mode=CompanionTransportMode::Bluetooth;
bool companion_transport_boot_mode_loaded=true;
CompanionTransportMode getCompanionTransportMode() {
  return companion_transport_boot_mode;
}
bool companionTransportWiFiActiveAtBoot() {
  return companion_transport_boot_mode==CompanionTransportMode::WiFi;
}
#endif

enum wifi_ps_type_t { WIFI_PS_NONE, WIFI_PS_MIN_MODEM, WIFI_PS_MAX_MODEM };
constexpr int ESP_OK=0, WIFI_OFF=0, WIFI_IF_STA=0, WIFI_AP=2;
enum wl_status_t { WL_CONNECTED=3, WL_NO_SSID_AVAIL=1, WL_CONNECT_FAILED=4,
                   WL_CONNECTION_LOST=5, WL_DISCONNECTED=6 };
wifi_ps_type_t last_ps=WIFI_PS_MIN_MODEM;
struct TestIP {
  uint32_t value=0;
  operator uint32_t() const { return value; }
  std::string toString() const { return value ? "192.168.11.102" : "0.0.0.0"; }
};
struct {
  int mode=1;
  wl_status_t state=WL_CONNECTED;
  int getMode() const { return mode; }
  wl_status_t status() const { return state; }
  std::string SSID() const { return "test WLAN"; }
  std::string softAPSSID() const { return "MeshCore-Setup"; }
  TestIP localIP() const { return {1}; }
  TestIP softAPIP() const { return {uint32_t(mode & WIFI_AP)}; }
  int RSSI() const { return -74; }
} WiFi;
char configured_wifi_ssid[32]="test WLAN";
bool companion_wifi_requested=true, companion_wifi_active=true;
bool companion_wifi_has_credentials=true, companion_wifi_credential_reload_pending=false;
int esp_wifi_set_ps(wifi_ps_type_t value) {
  ++sdk_ps_calls; last_ps=value; return ESP_OK;
}
int esp_wifi_get_ps(wifi_ps_type_t* value) { *value=last_ps; return ESP_OK; }
int esp_wifi_get_protocol(int, uint8_t* value) { *value=7; return ESP_OK; }
bool companion_wifi_power_save_loaded=false;
uint8_t companion_wifi_power_save=mesh::wifi::kDefaultPowerSave;
namespace mesh { namespace wifi {
constexpr bool kPrimaryEspNowRadio=false;
struct StatusPower {
  bool suspended=false, foreground=false;
  bool backgroundSuspended() const { return suspended; }
  bool foregroundActive() const { return foreground; }
};
StatusPower& networkPower() { static StatusPower value; return value; }
// Protocol persistence has its own real SDK-boundary suite; only the endpoint
// is mocked here so the complete production CLI handler can be executed.
CompatibilitySaveResult saveStationProtocol(StationProtocol value) {
  return compatibility_detail::save("phy", value, compatibility_detail::protocolPreference());
}
} }

@METHODS@

void rebootCompatibility() {
  mesh::wifi::compatibility_detail::modePreference()={};
  assert(mesh::wifi::loadCompanionModeForBoot());
}
void resetBootCounters() {
  identity_calls=address_calls=begin_calls=enable_calls=register_calls=0;
  companion_bluetooth_initialized=false;
  companion_bluetooth_start_at=0;
}
int main() {
  using namespace mesh::wifi;
  reset_nvs(); rebootCompatibility();
  assert(activeCompanionMode()==CompanionMode::Normal);
#if defined(ESP32) && defined(WIFI_SSID)
  char reply[192]={};
  for (const char* text : {"get wifi.modex", "set wifi.modex wifi-only", "get unknown",
                          "get wifi.compat", "get wifi.status detail", "get wifi.status detailx"})
    assert(!handleCompanionWiFiCompatibilityCommand(text,reply,sizeof(reply)));
  assert(!handleCompanionWiFiCompatibilityCommand(nullptr,reply,sizeof(reply)));
  assert(!handleCompanionWiFiCompatibilityCommand("get wifi.status",nullptr,192));
  assert(!handleCompanionWiFiCompatibilityCommand("get wifi.status",reply,0));
#if !defined(COMPANION_EXCLUSIVE_WIFI_BLE)
  for (const char* text : {"set wifi.mode", "set wifi.mode none", "set wifi.mode wifi-only extra"}) {
    assert(handleCompanionWiFiCompatibilityCommand(text,reply,sizeof(reply)));
    assert(strstr(reply,"Error:") && configuredCompanionMode()==CompanionMode::Normal);
  }
  assert(handleCompanionWiFiCompatibilityCommand("set wifi.mode wifi-only",reply,sizeof(reply)));
  assert(strstr(reply,"wifi.enabled 1") && configuredCompanionMode()==CompanionMode::Normal);
  the_mesh.prefs.wifi_enabled=1;
  assert(handleCompanionWiFiCompatibilityCommand("set wifi.mode\twifi-only",reply,sizeof(reply)));
  assert(strstr(reply,"reboot required") && configuredCompanionMode()==CompanionMode::WiFiOnly);
  assert(handleCompanionWiFiCompatibilityCommand("get wifi.mode",reply,sizeof(reply)));
  assert(strstr(reply,"wifi-only") && strstr(reply,"boot=normal") && strstr(reply,"reboot required"));
  assert(!identity_calls && !begin_calls && !register_calls);
  assert(handleCompanionWiFiCompatibilityCommand("set wifi.mode normal",reply,sizeof(reply)));
  nvs_available=false;
  assert(handleCompanionWiFiCompatibilityCommand("set wifi.mode wifi-only",reply,sizeof(reply)));
  assert(strstr(reply,"could not be saved") && configuredCompanionMode()==CompanionMode::Normal);
  nvs_available=true;
#else
  assert(!handleCompanionWiFiCompatibilityCommand("get wifi.mode",reply,sizeof(reply)));
  assert(!handleCompanionWiFiCompatibilityCommand("set wifi.mode wifi-only",reply,sizeof(reply)));
#endif
  assert(handleCompanionWiFiCompatibilityCommand("get wifi.status",reply,sizeof(reply)));
  assert(strstr(reply,"coex=yes") && strstr(reply,"protocol=")
      && strstr(reply,"mode=") && strstr(reply,"ps=")
      && strstr(reply,"connected") && strstr(reply,"test WLAN")
      && strstr(reply,"192.168.11.102") && strstr(reply,"rssi=-74"));
  assert(strlen(reply)<160);
  // The expanded status remains bounded for short framed/terminal replies.
  for (size_t capacity : {1U, 2U, 16U, 80U, 160U}) {
    char guarded[162]; memset(guarded,'!',sizeof(guarded));
    assert(handleCompanionWiFiCompatibilityCommand("get wifi.status",guarded+1,capacity));
    assert(guarded[0]=='!' && guarded[capacity+1]=='!');
    assert(memchr(guarded+1,0,capacity)!=nullptr);
  }
  // The runtime collector distinguishes disabled, suspended, retry and setup
  // states rather than reporting every stopped WebConfig session as WiFi off.
  WiFi.state=WL_DISCONNECTED; companion_wifi_requested=false;
  formatCompanionWiFiStatus(reply,sizeof(reply)); assert(strstr(reply,"> off "));
  companion_wifi_requested=true; companion_wifi_active=false;
  formatCompanionWiFiStatus(reply,sizeof(reply)); assert(strstr(reply,"inactive"));
  companion_wifi_active=true; companion_wifi_credential_reload_pending=true;
  formatCompanionWiFiStatus(reply,sizeof(reply)); assert(strstr(reply,"reconnect-pending"));
  companion_wifi_credential_reload_pending=false;
  networkPower().suspended=true;
  formatCompanionWiFiStatus(reply,sizeof(reply)); assert(strstr(reply,"suspended(30m)"));
  networkPower().suspended=false; companion_wifi_has_credentials=false;
  formatCompanionWiFiStatus(reply,sizeof(reply)); assert(strstr(reply,"unconfigured"));
  companion_wifi_has_credentials=true;
  for (auto state : {WL_NO_SSID_AVAIL,WL_CONNECT_FAILED,WL_CONNECTION_LOST,WL_DISCONNECTED}) {
    WiFi.state=state; formatCompanionWiFiStatus(reply,sizeof(reply));
    assert(strstr(reply,"protocol=") && !strstr(reply," ip=") && !strstr(reply," rssi="));
  }
  WiFi.mode=3;
  formatCompanionWiFiStatus(reply,sizeof(reply));
  assert(strstr(reply,"setup-ap") && strstr(reply,"MeshCore-Setup") && strstr(reply," ip="));
  WiFi.mode=1; WiFi.state=WL_CONNECTED;
#endif
  assert(saveCompanionMode(CompanionMode::WiFiOnly)==CompatibilitySaveResult::Saved);
  assert(activeCompanionMode()==CompanionMode::Normal); // Next boot only.
  rebootCompatibility();
  assert(activeCompanionMode()==CompanionMode::WiFiOnly);

#if defined(ESP32) && defined(WIFI_SSID) && !defined(COMPANION_EXCLUSIVE_WIFI_BLE)
  // The guard must precede identity writes, registration, SDK init and retry.
  startCompanionBluetooth();
  assert(!companion_bluetooth_initialized && !companion_bluetooth_start_at);
  assert(!identity_calls && !address_calls && !register_calls && !begin_calls && !enable_calls);
  assert(companion_bluetooth_start_failure[0]);
  // Even an old retry deadline cannot resurrect Bluetooth in this boot.
  companion_bluetooth_start_at=now_ms;
  serviceDeferredCompanionBluetooth();
  assert(!companion_bluetooth_start_at && !begin_calls && !identity_calls);

  // Saved normal mode is not permission to init BLE until an actual reboot.
  assert(saveCompanionMode(CompanionMode::Normal)==CompatibilitySaveResult::Saved);
  startCompanionBluetooth();
  assert(!companion_bluetooth_initialized && !begin_calls && !register_calls);
  assert(!companionWiFiBluetoothActive());
  nvs_values["powersave"]={PT_U8, {}, kPowerSaveNone};
  reloadCompanionWiFiPowerSave();
  assert(getCompanionWiFiPowerSave()==kPowerSaveNone && last_ps==WIFI_PS_NONE);
  assert(setCompanionWiFiPowerSave(kPowerSaveNone)==CompanionWiFiPowerSaveResult::Applied);
  assert(last_ps==WIFI_PS_NONE);
  rebootCompatibility();
  assert(activeCompanionMode()==CompanionMode::Normal);
#endif

  // Ordinary startup, existing Indicator exclusive mode, and non-ESP32 BLE
  // startup must retain their previous behavior even if the generic NVS flag
  // is present. This function is only invoked after the Indicator's boot gate.
  resetBootCounters();
  startCompanionBluetooth();
  assert(companion_bluetooth_initialized);
  assert(identity_calls==1 && register_calls==1 && address_calls==1 && begin_calls==1 && enable_calls==1);
  startCompanionBluetooth(); serviceDeferredCompanionBluetooth();
  assert(begin_calls==1); // Already initialized remains idempotent.

#if defined(ESP32) && defined(WIFI_SSID)
  assert(companionWiFiBluetoothActive());
  reloadCompanionWiFiPowerSave();
  assert(getCompanionWiFiPowerSave()==kPowerSaveMin && last_ps==WIFI_PS_MIN_MODEM);
  unsigned before=sdk_ps_calls;
  assert(setCompanionWiFiPowerSave(kPowerSaveNone)==CompanionWiFiPowerSaveResult::BluetoothConflict);
  assert(sdk_ps_calls==before && last_ps==WIFI_PS_MIN_MODEM);
  // A live save for next boot must not unlock PS_NONE under a running BLE stack.
  assert(saveCompanionMode(CompanionMode::WiFiOnly)==CompatibilitySaveResult::Saved);
  assert(companionWiFiBluetoothActive());
#if !defined(COMPANION_EXCLUSIVE_WIFI_BLE)
  before=sdk_ps_calls;
  assert(setCompanionWiFiPowerSave(kPowerSaveNone)==CompanionWiFiPowerSaveResult::SavedForNextBoot);
  assert(sdk_ps_calls==before && last_ps==WIFI_PS_MIN_MODEM);
  assert(getCompanionWiFiPowerSave()==kPowerSaveMin);
  assert(nvs_values.at("powersave").byte==kPowerSaveNone);
#else
  assert(setCompanionWiFiPowerSave(kPowerSaveNone)==CompanionWiFiPowerSaveResult::BluetoothConflict);
#endif
#endif

  // Failed initialization still retries on normal BLE-enabled boots.
  resetBootCounters(); identity_ok=false;
  startCompanionBluetooth();
  assert(!companion_bluetooth_initialized && companion_bluetooth_start_at==now_ms+5000);
  now_ms+=4999; serviceDeferredCompanionBluetooth(); assert(identity_calls==1);
  ++now_ms; identity_ok=true; serviceDeferredCompanionBluetooth();
  assert(companion_bluetooth_initialized && identity_calls==2 && begin_calls==1);
}
'''


class CompanionWiFiCompatibilityTest(unittest.TestCase):
    def compile(self, *defines):
        main = (ROOT / "examples/companion_radio/main.cpp").read_text()
        signatures = [
            "static void scheduleCompanionBluetoothRetry(",
            "static void startCompanionBluetooth(",
            "static void serviceDeferredCompanionBluetooth(",
        ]
        if any(item.startswith("-DWIFI_SSID=") for item in defines):
            signatures += [
                "static bool companionWiFiBluetoothActive(",
                "const char* companionWiFiPowerSaveName(",
                "static wifi_ps_type_t companionWiFiPowerSaveType(",
                "void reloadCompanionWiFiPowerSave(",
                "uint8_t getCompanionWiFiPowerSave(",
                "const char* getCompanionWiFiPowerSaveName(",
                "bool applyCompanionWiFiPowerSave(",
                "CompanionWiFiPowerSaveResult setCompanionWiFiPowerSave(",
                "void formatCompanionWiFiStatus(",
                "bool handleCompanionWiFiCompatibilityCommand(",
            ]
        source = HARNESS.replace("@METHODS@", "\n".join(method(main, item) for item in signatures))
        compiler = os.environ.get("CXX") or shutil.which("g++")
        self.assertIsNotNone(compiler, "A host C++ compiler is required")
        with tempfile.TemporaryDirectory(prefix="companion-wifi-boot-") as directory:
            folder = Path(directory)
            (folder / "Preferences.h").write_text(
                (ROOT / "test/fixtures/wifi_credentials_preferences.h").read_text()
            )
            (folder / "test.cpp").write_text(source)
            binary = folder / ("test.exe" if os.name == "nt" else "test")
            flags = [] if os.name == "nt" else [
                "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"
            ]
            built = subprocess.run([
                compiler, "-std=c++17", "-O1", "-Wall", "-Wextra", *flags,
                "-DBLE_PIN_CODE=123456", *defines,
                "-I", str(folder), "-I", str(ROOT / "src"), "-I", str(ROOT),
                str(folder / "test.cpp"), "-o", str(binary),
            ], capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_generic_wifi_only_boot_and_deferred_retry_stay_ble_free(self):
        self.compile("-DESP32=1", '-DWIFI_SSID="test"')

    def test_indicator_exclusive_mode_keeps_its_existing_selector(self):
        self.compile("-DESP32=1", '-DWIFI_SSID="test"', "-DCOMPANION_EXCLUSIVE_WIFI_BLE=1")

    def test_non_esp32_bluetooth_boot_is_unchanged(self):
        # ESP32 compatibility storage is made available only for the NVS test
        # setup; undefining ESP32 before the actual methods exercises the old
        # non-ESP32 startup branch without an infrastructure-WiFi interface.
        self.compile("-DESP32=1", "-DMESH_TEST_NON_ESP32=1")

    def test_compatibility_is_latched_before_mesh_reads_the_power_policy(self):
        main = (ROOT / "examples/companion_radio/main.cpp").read_text()
        setup = method(main, "void setup()")
        self.assertLess(setup.index("loadCompanionModeForBoot();"), setup.index("the_mesh.begin("))
        self.assertLess(setup.index("loadCompanionModeForBoot();"), setup.index("startCompanionBluetooth();"))
        mesh = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        begin = method(mesh, "void MyMesh::begin(")
        self.assertIn("getCompanionWiFiPowerSave();", begin)


if __name__ == "__main__":
    unittest.main()
