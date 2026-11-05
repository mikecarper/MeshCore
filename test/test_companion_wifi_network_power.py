"""Execute Companion's production WiFi/MQTT/ESP-NOW transitions without hardware."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "examples/companion_radio/main.cpp"


HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#define WIFI_OFF 0
#define WIFI_STA 1
#define WIFI_MODE_NULL 0
#define WIFI_MODE_STA 1
#define ESP_OK 0
#include <helpers/esp32/WiFiCoordinator.h>
#include <helpers/WiFiConnectionDiagnostics.h>
int ps_applied = 0;

namespace mesh { namespace wifi {
void noteStationDiagnosticPhase(ConnectionPhase) {}
bool espnow = false, station_allowed = true;
int protocols = 0, channels = 0, associations = 0, restore_result = 0;
bool espNowChannelConstrained() { return espnow; }
bool stationMutationAllowed() { return station_allowed; }
void applyProtocolMask(int) { ++protocols; }
int restoreEspNowChannel() { ++channels; return restore_result; }
void setStationAutoReconnect(bool);
void beginStation(const char*, const char*) { assert(ps_applied > 0); ++associations; }
}}

constexpr int WIFI_IF_STA = 0;
constexpr int WL_CONNECTED = 3, WL_DISCONNECTED = 6;
uint32_t test_now = 10000;
uint32_t millis() { return test_now; }
struct Board {
  bool ota = false, inhibit = true;
  bool isOTAUpdateRunning() const { return ota; }
  void setInhibitSleep(bool value) { inhibit = value; }
} board;
struct Interface {
  bool connected = false, enabled = true;
  int ends = 0, begins = 0;
  bool isConnected() const { return connected; }
  void end() { enabled = false; ++ends; }
  bool begin(int) { ++begins; return true; }
  void enable() { enabled = true; }
  bool isListening() const { return enabled && begins > ends; }
} wifi_interface;
struct ConsoleClient {
  bool active = false;
  bool connected() const { return active; }
} ota_console_client;
struct Wifi {
  int current_mode = WIFI_STA;
  int current_status = WL_DISCONNECTED;
  bool reconnect = true, last_disconnect_stopped = false;
  int disconnects = 0;
  void setAutoReconnect(bool value) { reconnect = value; }
  void disconnect(bool stop, bool) {
    last_disconnect_stopped = stop;
    ++disconnects;
  }
  void mode(int value) { current_mode = value; }
  int status() const { return current_status; }
} WiFi;
void mesh::wifi::setStationAutoReconnect(bool value) { WiFi.setAutoReconnect(value); }

struct Tracker { void noteDisconnected(uint32_t) {} } wifi_reconnect_tracker;
bool companion_wifi_active = true, companion_wifi_requested = true;
bool companion_wifi_interface_registered = true;
bool companion_wifi_espnow_restore_pending = false;
uint32_t companion_wifi_espnow_restore_at = 0;
bool companion_wifi_webconfig_start_pending = false;
bool companion_wifi_credential_reload_pending = false;
bool companion_wifi_setup_requested = false;
bool companion_wifi_has_credentials = true;
char configured_wifi_ssid[] = "saved-network";
char configured_wifi_password[] = "saved-password";
int ntp_cancels = 0, resets = 0, console_starts = 0, console_stops = 0;
void cancelCompanionWiFiNtp(bool retry) { assert(!retry); ++ntp_cancels; }
void resetCompanionWiFiRecoveryState() { ++resets; }
void applyCompanionWiFiPowerSave() { ++ps_applied; }
void ota_console_start() { ++console_starts; }
void ota_console_stop() { ++console_stops; }
#define WIFI_DEBUG_PRINTLN(...) do {} while (0)
struct WebConfigServer {
  static bool enabled;
  static bool loadEnabled(bool) { return enabled; }
};
bool WebConfigServer::enabled = true;
struct Mesh {
  bool web_active = false, last_force_ap = false;
  int web_starts = 0;
  bool isWebConfigActiveOrStopping() const { return web_active; }
  bool startWebConfig(bool force_ap, char*) {
    ++web_starts; last_force_ap = force_ap; web_active = true; return true;
  }
} the_mesh;

@METHODS@

void reset() {
  auto& coord = mesh::wifi::coordinator();
  coord.reset();
  mesh::wifi::espnow = false;
  mesh::wifi::station_allowed = true;
  mesh::wifi::protocols = mesh::wifi::channels = mesh::wifi::associations = 0;
  mesh::wifi::restore_result = 0;
  test_now = 10000;
  board = {}; wifi_interface = {}; ota_console_client = {}; WiFi = {};
  companion_wifi_active = companion_wifi_requested = true;
  companion_wifi_interface_registered = true;
  companion_wifi_espnow_restore_pending = false;
  companion_wifi_espnow_restore_at = 0;
  companion_wifi_webconfig_start_pending = false;
  companion_wifi_credential_reload_pending = companion_wifi_setup_requested = false;
  companion_wifi_has_credentials = true;
  ntp_cancels = resets = ps_applied = console_starts = console_stops = 0;
  the_mesh = {}; WebConfigServer::enabled = true;
}

int main() {
  // Deferred ESP-NOW channel repair runs after an intentional WiFi disable,
  // without restarting that station or its listeners. A removed ESP-NOW owner
  // cancels it, and millis() wrap is handled safely.
  reset();
  test_now = UINT32_MAX - 500;
  mesh::wifi::espnow = true;
  mesh::wifi::restore_result = 1;
  stopCompanionInfrastructureWiFi();
  companion_wifi_requested = companion_wifi_active = false;
  assert(companion_wifi_espnow_restore_pending && WiFi.disconnects == 1);
  test_now += 999;
  serviceCompanionEspNowChannelRestore();
  assert(mesh::wifi::channels == 1);
  ++test_now;
  mesh::wifi::station_allowed = false; // foreground AP owns the channel
  serviceCompanionEspNowChannelRestore();
  assert(mesh::wifi::channels == 1);
  mesh::wifi::station_allowed = true;
  board.ota = true;
  serviceCompanionEspNowChannelRestore();
  assert(mesh::wifi::channels == 1);
  board.ota = false;
  mesh::wifi::restore_result = ESP_OK;
  serviceCompanionEspNowChannelRestore();
  assert(mesh::wifi::channels == 2 && !companion_wifi_espnow_restore_pending);
  assert(WiFi.disconnects == 1 && mesh::wifi::associations == 0);

  mesh::wifi::restore_result = 1;
  stopCompanionInfrastructureWiFi();
  mesh::wifi::espnow = false;
  test_now += 1000;
  serviceCompanionEspNowChannelRestore();
  assert(!companion_wifi_espnow_restore_pending && mesh::wifi::channels == 3);

  // When MQTTBridge holds the station lease, Companion defers the 15-second
  // AP fallback until WiFi associates.
  reset();
  mesh::wifi::coordinator().setStationLease(mesh::wifi::StationOwner::MqttBridge, true);
  assert(!companionWiFiAutomaticRecoveryAllowed());
  startCompanionWiFiAutoWebConfig();
  assert(companion_wifi_webconfig_start_pending && the_mesh.web_starts == 0);
  for (int i = 0; i < 100; ++i) serviceCompanionWiFiAutoWebConfig();
  assert(the_mesh.web_starts == 0);
  WiFi.current_status = WL_CONNECTED;
  serviceCompanionWiFiAutoWebConfig();
  assert(!companion_wifi_webconfig_start_pending && the_mesh.web_starts == 1);
  assert(!the_mesh.last_force_ap);
  serviceCompanionWiFiAutoWebConfig();
  assert(the_mesh.web_starts == 1);

  // Unconfigured devices still open setup, and standalone WiFi without MQTT
  // retains its ordinary recovery AP.
  reset();
  companion_wifi_has_credentials = false;
  startCompanionWiFiAutoWebConfig();
  assert(the_mesh.web_starts == 1 && the_mesh.last_force_ap);
  assert(!companion_wifi_webconfig_start_pending);
  assert(companionWiFiAutomaticRecoveryAllowed());
}
'''


class CompanionWiFiNetworkPowerTests(unittest.TestCase):
    def test_router_down_boot_starts_the_configured_mqtt_scheduler_once(self):
        source = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text(encoding="ascii")
        actual = extract_braced(source, "static void copyMqttString(")
        actual += extract_braced(source, "void MyMesh::serviceMQTT(")
        fixture = r'''
#define WITH_WEBCONFIG 1
#define WIFI_OFF 0
#define WIFI_STA 1
#define WIFI_MODE_NULL 0
#define WIFI_MODE_STA 1
#define ESP_OK 0
#include <cassert>
#include <cstring>
#include <helpers/esp32/WiFiCoordinator.h>
constexpr int WL_DISCONNECTED = 6;
struct { int status() const { return WL_DISCONNECTED; } } WiFi;
struct { bool ota = false; bool isOTAUpdateRunning() const { return ota; } } board;
namespace mesh { struct Log { void println(const char*) {} };
inline Log& usbLoggingPort() { static Log log; return log; } }
struct Bridge {
  int starts = 0; bool running = false;
  void loop() {}
  bool isStopping() const { return false; }
  bool isStartPending() const { return false; }
  void begin() {
    ++starts;
    running = true;
    mesh::wifi::coordinator().setStationLease(mesh::wifi::StationOwner::MqttBridge, true);
  }
  bool isRunning() const { return running; }
};
struct MyMesh {
  bool _mqtt_enabled = true, _wc_batch_active = false;
  bool _mqtt_started = false, _mqtt_configured = true, setup = false;
  int stops = 0;
  struct { char wifi_ssid[33] = {}, wifi_password[65] = {}; } _mqtt_prefs;
  Bridge* _mqtt_bridge;
  void stopMQTT() { ++stops; }
  bool isWebConfigSetupActive() const { return setup; }
  void serviceMQTT(const char*, const char*);
};
'''
        scenarios = r'''
int main() {
  mesh::wifi::coordinator().reset();
  Bridge bridge; MyMesh node; node._mqtt_bridge = &bridge;
  assert(WiFi.status() == WL_DISCONNECTED);
  board.ota = true;
  node.serviceMQTT("saved-network", "saved-password");
  assert(bridge.starts == 0); board.ota = false;
  node._wc_batch_active = true;
  node.serviceMQTT("saved-network", "saved-password");
  assert(bridge.starts == 0);
  node._wc_batch_active = false; node.setup = true;
  node.serviceMQTT("saved-network", "saved-password");
  assert(bridge.starts == 0);
  node.setup = false; node._mqtt_configured = false;
  node.serviceMQTT("saved-network", "saved-password");
  assert(bridge.starts == 0);
  node._mqtt_configured = true;
  node.serviceMQTT("saved-network", "saved-password");
  assert(bridge.starts == 1 && node._mqtt_started);
  assert(mesh::wifi::coordinator().hasStationLease(mesh::wifi::StationOwner::MqttBridge));
  assert(strcmp(node._mqtt_prefs.wifi_ssid, "saved-network") == 0);
  assert(strcmp(node._mqtt_prefs.wifi_password, "saved-password") == 0);
  node.serviceMQTT("saved-network", "saved-password");
  assert(bridge.starts == 1);
  node._mqtt_enabled = false;
  node.serviceMQTT("saved-network", "saved-password");
  assert(node.stops == 1);
}
'''
        with tempfile.TemporaryDirectory(prefix="companion-router-outage-") as directory:
            path = Path(directory)
            cpp, binary = path / "fixture.cpp", path / "fixture"
            cpp.write_text(fixture + actual + scenarios, encoding="ascii")
            result = subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                "-I", str(ROOT / "src"), str(cpp), "-o", str(binary)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_production_transitions_with_and_without_ota_console(self):
        compiler = os.environ.get("CXX") or shutil.which("g++")
        self.assertIsNotNone(compiler)
        source = MAIN.read_text(encoding="ascii")
        methods = "\n".join(extract_braced(source, signature) for signature in (
            "static bool companionWiFiAutomaticRecoveryAllowed(",
            "static void startCompanionWiFiAutoWebConfig(",
            "static void serviceCompanionWiFiAutoWebConfig(",
            "static void stopCompanionInfrastructureWiFi(",
            "static void serviceCompanionEspNowChannelRestore(",
        ))
        sanitizer = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                      "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])
        with tempfile.TemporaryDirectory(prefix="companion-network-power-") as directory:
            path = Path(directory)
            cpp = path / "fixture.cpp"
            cpp.write_text(HARNESS.replace("@METHODS@", methods), encoding="ascii")
            for ota in (False, True):
                with self.subTest(ota=ota):
                    binary = path / ("with-ota" if ota else "no-ota")
                    built = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra",
                        "-Werror", *sanitizer, *(["-DENABLE_OTA=1"] if ota else []),
                        "-I", str(ROOT / "src"), str(cpp), "-o", str(binary)],
                        capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_auto_webconfig_and_recovery_guards_stay_wired(self):
        source = MAIN.read_text(encoding="ascii")
        self.assertIn("if (!wifi_setup_recovery_mode && companionWiFiAutomaticRecoveryAllowed()", source)
        start = extract_braced(source, "static void startCompanionWiFi(")
        self.assertIn("startCompanionWiFiAutoWebConfig();", start)
        self.assertNotIn("startWebConfig(", start)
        self.assertNotIn("companion_wifi_webconfig_start_pending = false", start)


if __name__ == "__main__":
    unittest.main()
