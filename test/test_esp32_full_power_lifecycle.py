#!/usr/bin/env python3
"""Execute infrastructure startup, bridge retries, OTA resume, and idle teardown.

The role methods, preference defaults, automatic setup block, and deferred OTA
block are extracted from production. Only hardware/server endpoints are mocked.
"""
from pathlib import Path
import unittest

from test_replay_reset_integration import extract_braced
import test_wifi_ota_start as wifi_start

ROOT = Path(__file__).resolve().parents[1]
FLAGS = ["-DESP32_PLATFORM=1", "-DESP_PLATFORM=1", "-DADMIN_PASSWORD=1",
         "-DWITH_BRIDGE=1", "-DWITH_WEBCONFIG=1", "-DWITH_MQTT_BRIDGE=1",
         "-DWITH_ESPNOW_BRIDGE=1", "-DLIGHTWEIGHT_WIFI_OTA=1",
         "-DOTA_MANIFEST_BASE=1", "-DMESHCORE_EXPANDED_PARTITION_PROFILE=1",
         "-DWEBCONFIG_UNCONFIGURED_SETUP_TIMEOUT_MS=1800000"]

FIXTURE = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <new>
#include <helpers/WebConfigBatch.h>
#include <helpers/PowerManagementUtils.h>
#include <helpers/PowerSavingDiagnostics.h>
#include <helpers/WiFiNetworkPowerPolicy.h>
#include <helpers/WirelessControl.h>
uint32_t now = 0;
uint32_t millis() { return now; }
bool millisHasNowPassed(uint32_t deadline) { return int32_t(now - deadline) >= 0; }
void delay(unsigned) {}
using wifi_mode_t = int;
constexpr int WIFI_OFF = 0, WIFI_MODE_NULL = 0, WIFI_STA = 1, WIFI_AP_STA = 3, WIFI_IF_STA = 0;
constexpr int ESP_OK = 0;
constexpr int ESP_ERR_WIFI_NOT_INIT = -1;
bool sdk_initialized = false, sdk_started = false;
bool fail_sdk_stop = false, fail_sdk_deinit = false;
unsigned espnow_owners = 0;
int sdk_mode = WIFI_OFF, sdk_channel = 1;
unsigned sdk_stops = 0, sdk_deinits = 0;
int esp_wifi_get_mode(wifi_mode_t* mode) {
  if (!sdk_initialized) return -1;
  *mode = sdk_mode; return ESP_OK;
}
int esp_wifi_stop() {
  ++sdk_stops; if (fail_sdk_stop) return -2;
  sdk_started = false; return ESP_OK;
}
int esp_wifi_deinit() {
  ++sdk_deinits; if (fail_sdk_deinit || sdk_started) return -2;
  sdk_initialized = sdk_started = false; sdk_mode = WIFI_OFF;
  return ESP_OK;
}
struct WiFiMock {
  bool initialized_cache = false, started_cache = false, connected = false;
  bool fail_sta = false, fail_off = false, auto_reconnect = false;
  unsigned disconnects = 0, sta_modes = 0, off_modes = 0;
  int getMode() const { return initialized_cache && started_cache ? sdk_mode : WIFI_OFF; }
  int channel() const { return sdk_initialized ? sdk_channel : 0; }
  bool isConnected() const { return connected; }
  void setAutoReconnect(bool enabled) { auto_reconnect = enabled; }
  void disconnect(bool off, bool erase = false) {
    assert(!erase); ++disconnects; connected = false;
    if (off) mode(WIFI_OFF);
  }
  bool mode(int mode) {
    if (mode == WIFI_OFF) ++off_modes;
    if (mode == WIFI_STA) ++sta_modes;
    if ((mode == WIFI_STA && fail_sta) || (mode == WIFI_OFF && fail_off)) return false;
    if (mode == getMode()) return true; // Arduino OFF is a no-op for a raw IDF driver.
    if (mode == WIFI_OFF) {
      initialized_cache = started_cache = false;
      esp_wifi_stop(); esp_wifi_deinit();
    } else {
      initialized_cache = started_cache = true;
      sdk_initialized = sdk_started = true; sdk_mode = mode;
    }
    return true;
  }
  void reset() { *this = {}; sdk_initialized = sdk_started = false; sdk_mode = WIFI_OFF;
                 sdk_channel = 1; sdk_stops = sdk_deinits = 0;
                 fail_sdk_stop = fail_sdk_deinit = false; }
} WiFi;
namespace mesh {
namespace bridge { constexpr int ESPNOW_FORMAT_WRAPPED = 1; }
namespace wifi {
  void applyProtocolMask(int) {} void restoreEspNowChannel() {}
  bool espNowChannelConstrained() { return espnow_owners != 0; }
}
struct LocalIdentity { unsigned char pub_key[32] = {}; };
struct Utils { static void toHex(char* out, const unsigned char*, int) { strcpy(out, "test"); } };
struct Log { template<class... Args> void printf(const char*, Args...) {} };
Log& usbConsolePort() { static Log log; return log; }
Log& usbDebugPort() { return usbConsolePort(); }
}
constexpr int PUB_KEY_SIZE = 32;
struct Prefs {
  int bridge_enabled = 0, espnow_bridge_enabled = 0, rs232_bridge_enabled = 0;
  bool battery_alert_enabled = false;
  const char* node_name = "test";
  float freq = 915, bw = 125; uint8_t sf = 7, cr = 5, disable_fwd = 0;
};
struct ObserverPrefs { char wifi_ssid[33] = {}; };
struct Board {
  bool ota = false, allow_start = true, allow_stop = true;
  unsigned manifest_calls = 0;
  bool isOTAUpdateRunning() const { return ota; }
  const char* getManufacturerName() const { return "test"; }
  bool startOTAUpdate(const char*, char* reply, bool) {
    if (!allow_start) { strcpy(reply, "ERR: OTA WiFi failed"); return false; }
    WiFi.mode(WIFI_AP_STA); ota = true; strcpy(reply, "Started: http://192.168.4.1/update");
    return true;
  }
  bool stopOTAUpdate(char* reply) {
    if (!allow_stop) { strcpy(reply, "ERR: OTA upload active"); return false; }
    ota = false; WiFi.mode(WIFI_STA); strcpy(reply, "OK - OTA stopped"); return true;
  }
  bool otaFromManifest(const char*, const char*, bool, char* reply) {
    ++manifest_calls; strcpy(reply, "ERR: not connected"); return false;
  }
};
struct RoleCLI {
  Board board; ObserverPrefs prefs;
  Board* getBoard() { return &board; }
  ObserverPrefs* getObserverPrefs() { return &prefs; }
  bool hasActiveUserGpioTimer() const { return false; }
};
struct AbstractBridge {
  bool running = false, stopping = false, allow_flash = true;
  unsigned starts = 0, stops = 0;
  bool isRunning() const { return running; }
  bool isStopping() const { return stopping; }
  bool canFlashAfterStop() const { return allow_flash; }
  void begin() { ++starts; running = true; WiFi.mode(WIFI_STA); }
  void end() { ++stops; running = false; } // MQTT and OTA may retain STA.
};
struct ESPNowBridge : AbstractBridge {
  void begin() { if (!running) ++espnow_owners; AbstractBridge::begin(); }
  void end() { if (running) --espnow_owners; AbstractBridge::end(); }
};
struct MQTTNodeInfo {
  const char* node_name; float* freq; float* bw;
  uint8_t* sf; uint8_t* cr; uint8_t* repeat_flag; bool repeat_when_nonzero; bool canonical_wifi = false;
};
struct MQTTBridge : AbstractBridge {
  bool network_suspended = false;
  bool isNetworkSuspended() const { return network_suspended; }
  template<class... Args> explicit MQTTBridge(Args...) {}
  void setDeviceID(const char*) {} void setFirmwareVersion(const char*) {}
  void setBoardModel(const char*) {} void setBuildDate(const char*) {}
  template<class... Args> void setStatsSources(Args...) {}
};
@STOP_OWNED_WIFI@
struct WebConfigServer {
  static bool enabled;
  static bool allow_start;
  bool running = false, stopping = false, owns;
  uint32_t _setup_started_at = 0;
  int _mode = 0;
  static constexpr int MODE_SETUP = 1;
  static constexpr int MODE_LAN = 2;
  uint32_t foreground_until = 0;
  char _wifi_ssid[33] = {};
  template<class... Args> WebConfigServer(void*, void* prefs, bool owner, Args...) : owns(owner) {
    if (prefs) strcpy(_wifi_ssid, static_cast<ObserverPrefs*>(prefs)->wifi_ssid);
  }
  static bool loadEnabled(bool) { return enabled; }
  static bool hasConfiguredWiFi(const void* prefs) { return static_cast<const ObserverPrefs*>(prefs)->wifi_ssid[0] != 0; }
  bool isRunning() const { return running; }
  bool isStopping() const { return stopping; }
  void updateWiFiOwnership(bool owner) { owns = owner; }
  void requestNetworkAccess() {
    foreground_until = millis() + 120000;
    mesh::wifi::networkPower().requestProbe();
  }
  bool isNetworkForegroundActive(uint32_t tick) const {
    return _mode == MODE_SETUP || stopping
        || (foreground_until && int32_t(tick - foreground_until) < 0);
  }
  bool startSetupMode(char* reply) {
    if (!allow_start) {
      if (owns) stopOwnedWiFiRadio();
      strcpy(reply, "Err: failed to start AP"); return false;
    }
    _mode = MODE_SETUP; _setup_started_at = millis(); running = true;
    WiFi.mode(WIFI_AP_STA); strcpy(reply, "WebConfig AP started");
    return true;
  }
  bool startAutoMode(char* reply) { return startSetupMode(reply); }
  void requestStop() { running = false; stopping = true; }
  bool stopForOTA(char*) { running = stopping = false; WiFi.mode(WIFI_STA); return true; }
  void tick(uint32_t now) {
    if (stopping) { stopping = false; if (owns) stopOwnedWiFiRadio(); return; }
    @SETUP_WINDOW@
  }
};
bool WebConfigServer::enabled = false;
bool WebConfigServer::allow_start = true;
constexpr unsigned OTA_TX_DRAIN_TIMEOUT_MS = 1, OTA_MQTT_STOP_SETTLE_MS = 1;
const char* ota_resolve_base(int) { return "test"; }
struct MyMesh {
  Prefs _prefs; RoleCLI _cli;
  MQTTBridge* @WORKER@ = nullptr;
  ESPNowBridge espnow_bridge;
  WebConfigServer* _webconfig = nullptr;
  void* _web_terminal = nullptr;
  bool _unconfigured_setup_espnow_suspended = false;
  uint32_t shared_espnow_retry_at = 0, _ota_update_at = 0;
  int _ota_update_channel = 0;
  bool _wc_batch_active = false, _wc_restart_pending = false;
  mesh::LocalIdentity self_id;
  void* _radio = nullptr; void* _ms = nullptr;
  struct { void setBridge(MQTTBridge*) {} } _alerter;
  struct { bool busy() const { return false; } } _local_cli_output;
  struct { bool pending = false; } deferred_cli_command;
  struct { bool isWatchdogObserving() const { return false; }
           bool isCalibratingNoiseFloor() const { return false; } } radio_driver;
  struct { int getNumClients() const { return 0; } } acl;
  bool pending_self_advert = false, saved_radio_apply_pending = false, temp_radio_applied = false;
  unsigned long next_flood_advert = 0, next_local_advert = 0, dirty_contacts_expiry = 0;
  unsigned long next_recent_repeater_sweep = 0, next_battery_alert_check = 0, next_push = 0;
  unsigned long radio_apply_retry_at = 0, set_radio_at = 0, revert_radio_at = 0;
  bool hasPendingOtaApply() const { return false; } bool hasQueuedWorkDue() const { return false; }
  bool hasRetryWorkDue() const { return false; } bool hasScheduledRadioWorkDue() const { return false; }
  bool isMillisTimerDue(unsigned long) const { return false; }
  bool getNextQueueWakeDelay(uint32_t&) const { return false; }
  bool getNextRetryWakeDelay(uint32_t&) const { return false; }
  bool getNextNoiseFloorWakeDelay(uint32_t&) const { return false; }
  bool isDualRadioActive() const { return false; }
  uint32_t limitSleepToMillisTimer(unsigned long, uint32_t secs) const { return secs; }
  uint32_t limitSleepToScheduledRadioWork(uint32_t secs) const { return secs; }
  const AbstractBridge* activeBridge() const { return @WORKER@; }
  const char* getFirmwareVer() const { return "test"; } const char* getBuildDate() const { return "test"; }
  const char* getRole() const { return "test"; }
  mesh::LocalIdentity getSelfId() { return self_id; } void* getRTCClock() { return nullptr; }
  void configureBridgeFilter(AbstractBridge*) {} void drainOutbound(unsigned) {} void otaAlert(const char*) {}
  void suspendUnconfiguredSetupBridges(); void serviceIdleWiFi();
  bool startWebConfigImpl(bool force_ap, char* reply, bool automatic_setup);
  bool startWebConfig(bool force_ap, char* reply); bool stopWebConfig(char* reply);
  bool hasPendingWork() const; uint32_t getPowerSaveSleepSeconds(uint32_t) const;
  mesh::powerdiag::Mask pendingWorkReason() const;
  @INLINE_METHODS@
  void defaults() { @DEFAULTS@ }
  void startupSetup() { @STARTUP@ }
  void servicePortal() { @PORTAL_LOOP@ }
  void manifestAttempt() { @MANIFEST@ }
  ~MyMesh() { delete _webconfig; delete @WORKER@; }
};
@METHODS@
@INFRA_BACKEND@
struct BrowserCLI {
  Board* _board; Prefs* _prefs; MyMesh* _callbacks;
  @OTA_STATE@
  explicit BrowserCLI(MyMesh& node) : _board(node._cli.getBoard()), _prefs(&node._prefs), _callbacks(&node) {}
  void run(const char* command, char* reply) { if (false) { @OTA_CLI@ } }
};
@CHECKS@
'''

CHECKS = r'''
int main() {
  char reply[160] = {};
  MyMesh node; node.defaults();
  assert(node._prefs.bridge_enabled == 1 && node._prefs.espnow_bridge_enabled == 1);
  assert(node.setBridgeState(true));
  assert(node.isMqttBridgeRunning());
  WiFi.connected = true;
  node.startSharedEspNowBridgeIfReady();
  assert(node.espnow_bridge.isRunning());
  node.startupSetup(); // Execute the actual first-boot setup decision after saved defaults.
  assert(node._unconfigured_setup_espnow_suspended && node.isWebConfigActive());
  assert(!node.isMqttBridgeRunning() && !node.espnow_bridge.isRunning());
  assert(node._prefs.bridge_enabled == 1 && node._prefs.espnow_bridge_enabled == 1);
  const auto starts = node.espnow_bridge.starts;
  for (now = 0; now < 1800000; now += 10000) {
    assert(!node.startSharedEspNowBridgeIfReady());
    node.servicePortal(); node.serviceIdleWiFi();
    assert(sdk_started && node.getPowerSaveSleepSeconds(30) == 0);
  }
  now = 1800000;
  node.servicePortal(); // Absolute window requests a stop, even with activity.
  assert(node.isWebConfigActive());
  node.serviceIdleWiFi(); assert(sdk_started); // Still stopping: do not tear down its task.
  node.servicePortal(); node.serviceIdleWiFi();
  assert(!node.isWebConfigActive() && !sdk_initialized && !sdk_started);
  assert(node.getPowerSaveSleepSeconds(30) == 30);
  now += 10000;
  assert(!node.startSharedEspNowBridgeIfReady() && node.espnow_bridge.starts == starts);
  // Manifest failure must not turn preserved defaults into actual running services.
  node._ota_update_at = now;
  node.manifestAttempt(); node.serviceIdleWiFi();
  assert(node._unconfigured_setup_espnow_suspended && !node.espnow_bridge.isRunning());
  assert(!node.isMqttBridgeRunning() && !sdk_started);
  // Explicit setup restart, stop webconfig, browser OTA, stop OTA: remain suspended.
  assert(node.startWebConfig(true, reply)); assert(node.isWebConfigActive());
  assert(node.stopWebConfig(reply));
  node.servicePortal(); node.serviceIdleWiFi();
  BrowserCLI cli(node);
  cli.run("start ota ap", reply); assert(node._cli.board.ota && sdk_started);
  cli.run("start ota ap", reply); // Repeated start does not invent a bridge snapshot.
  cli.run("stop ota", reply); node.serviceIdleWiFi();
  assert(node._unconfigured_setup_espnow_suspended && !sdk_started);
  assert(!node.isMqttBridgeRunning() && !node.espnow_bridge.isRunning());
  // An explicit ESP-NOW start ends the suspension and owns the shared radio.
  assert(node.setEspNowBridgeState(true));
  assert(!node._unconfigured_setup_espnow_suspended && node.espnow_bridge.isRunning());
  node.serviceIdleWiFi(); assert(sdk_started && node.hasPendingWork());
  // Real active ESP-NOW is restored on failed startup and successful shutdown.
  node._cli.board.allow_start = false;
  cli.run("start ota ap", reply); assert(node.espnow_bridge.isRunning());
  node._cli.board.allow_start = true;
  cli.run("start ota ap", reply); assert(!node.espnow_bridge.isRunning());
  node._cli.board.allow_stop = false;
  cli.run("stop ota", reply); assert(!node.espnow_bridge.isRunning() && node._cli.board.ota);
  node._cli.board.allow_stop = true;
  cli.run("stop ota", reply); assert(node.espnow_bridge.isRunning());
  assert(node.setEspNowBridgeState(false));
  node.serviceIdleWiFi(); assert(!sdk_started);
  // Closing a normal configured WebUI must not create a setup suspension.
  strcpy(node._cli.prefs.wifi_ssid, "saved");
  assert(node.startWebConfig(false, reply));
  assert(!node._unconfigured_setup_espnow_suspended);
  node.stopWebConfig(reply); node.servicePortal(); now += 10000;
  assert(node.startSharedEspNowBridgeIfReady());
  node.serviceIdleWiFi(); assert(sdk_started && node.espnow_bridge.isRunning());
  node.setEspNowBridgeState(false); node.serviceIdleWiFi();
  // A stopping MQTT task still owns the driver and prevents manual light sleep.
  node.@WORKER@->stopping = true;
  assert(!sdk_initialized && node.getPowerSaveSleepSeconds(30) == 0);
  WiFi.mode(WIFI_STA);
  node.serviceIdleWiFi(); assert(sdk_started && node.hasPendingWork());
  node.@WORKER@->stopping = false;
  node.serviceIdleWiFi(); assert(!sdk_started && node.getPowerSaveSleepSeconds(30) == 30);
  // The raw-IDF and Arduino facade states differ. Adopt stale OFF before teardown.
  WiFi.reset(); sdk_initialized = sdk_started = true; sdk_mode = WIFI_STA;
  node.serviceIdleWiFi();
  assert(WiFi.sta_modes == 1 && !WiFi.initialized_cache && !sdk_initialized);
  const auto stops = sdk_stops, deinits = sdk_deinits, disconnects = WiFi.disconnects;
  for (int i = 0; i < 10; ++i) node.serviceIdleWiFi();
  assert(sdk_stops == stops && sdk_deinits == deinits && WiFi.disconnects == disconnects);
  // If channel() is unavailable, the SDK-only fallback still reclaims the driver.
  WiFi.reset(); sdk_initialized = sdk_started = true; sdk_mode = WIFI_STA; sdk_channel = 0;
  node.serviceIdleWiFi(); assert(!sdk_initialized && sdk_deinits == 1);
  // A failed Arduino adoption/shutdown is retried, with no raw deinit under stale caches.
  WiFi.reset(); sdk_initialized = sdk_started = true; sdk_mode = WIFI_STA;
  WiFi.fail_sta = true; node.serviceIdleWiFi(); assert(sdk_started && sdk_deinits == 0);
  assert(node.getPowerSaveSleepSeconds(30) == 0);
  WiFi.fail_sta = false; WiFi.fail_off = true;
  node.serviceIdleWiFi(); assert(sdk_started && WiFi.initialized_cache && sdk_deinits == 0);
  assert(node.getPowerSaveSleepSeconds(30) == 0);
  WiFi.fail_off = false; node.serviceIdleWiFi(); assert(!sdk_initialized);
  assert(node.getPowerSaveSleepSeconds(30) == 30);
  WiFi.reset(); sdk_initialized = sdk_started = true; sdk_mode = WIFI_STA; sdk_channel = 0;
  fail_sdk_stop = true; node.serviceIdleWiFi();
  assert(sdk_started && node.getPowerSaveSleepSeconds(30) == 0);
  fail_sdk_stop = false; fail_sdk_deinit = true; node.serviceIdleWiFi();
  assert(sdk_initialized && !sdk_started && node.getPowerSaveSleepSeconds(30) == 0);
  fail_sdk_deinit = false; node.serviceIdleWiFi();
  assert(!sdk_initialized && node.getPowerSaveSleepSeconds(30) == 30);
  // Every live owner blocks cleanup, including a scheduled manifest operation.
  WiFi.mode(WIFI_STA); node._ota_update_at = now + 1000;
  node.serviceIdleWiFi(); assert(sdk_started);
  node._ota_update_at = 0; node.@WORKER@->running = true;
  node.serviceIdleWiFi(); assert(sdk_started);
  node.@WORKER@->running = false; node._cli.board.ota = true;
  node.serviceIdleWiFi(); assert(sdk_started);
  node._cli.board.ota = false; node.serviceIdleWiFi(); assert(!sdk_started);
}
'''


class ESP32FullPowerLifecycleTest(unittest.TestCase):
    compile_and_run = wifi_start.WiFiOtaStartTest.compile_and_run

    def fixture(self, role, checks=CHECKS, infra_backend=""):
        source = (ROOT / "examples" / role / "MyMesh.cpp").read_text()
        header = (ROOT / "examples" / role / "MyMesh.h").read_text()
        worker = "mqtt_bridge" if role == "simple_repeater" else "bridge"
        inline = "\n".join(extract_braced(header, signature) for signature in (
            "bool startSharedEspNowBridgeIfReady()", "bool setEspNowBridgeState(",
            "bool setMqttBridgeState(", "bool setBridgeState(",
            "bool isEspNowBridgeRunning()", "bool isMqttBridgeRunning()",
            "bool isMqttBridgeStopping()",
            "bool isWebConfigActive() const", "bool isWebConfigStopping() const",
            "bool hasWirelessNetworkClient() const", "bool stopWebConfigForOTA("))
        methods = "\n".join(extract_braced(source, signature) for signature in (
            "void MyMesh::suspendUnconfiguredSetupBridges()", "void MyMesh::serviceIdleWiFi()",
            "bool MyMesh::startWebConfig(", "bool MyMesh::startWebConfigImpl(",
            "bool MyMesh::stopWebConfig(",
            "bool MyMesh::hasPendingWork() const", "mesh::powerdiag::Mask MyMesh::pendingWorkReason() const",
            "uint32_t MyMesh::getPowerSaveSleepSeconds("))
        defaults_start = source.index("  // bridge defaults")
        defaults = source[defaults_start:source.index("  _prefs.bridge_delay", defaults_start)]
        startup = source[source.index("  bool start_webui = WebConfigServer::loadEnabled(false);"):]
        startup = startup[:startup.index("#endif\n\n", startup.index("  if (start_webui)"))]
        tick = source.index("_webconfig->tick(millis());")
        portal_start = source.rfind("  if (_webconfig) {", 0, tick)
        portal_loop = extract_braced(source[portal_start:], "  if (_webconfig) {")
        manifest = extract_braced(source, "  if (_ota_update_at && millisHasNowPassed(_ota_update_at))")
        cli = (ROOT / "src/helpers/CommonCLI.cpp").read_text()
        cli_header = (ROOT / "src/helpers/CommonCLI.h").read_text()
        state_begin = cli_header.index("  bool _wifi_ota_resume_mqtt = false;")
        state = cli_header[state_begin:cli_header.index("#endif", state_begin)]
        begin = cli.index('    } else if (memcmp(command, "start ota", 9)')
        end = cli.index('    } else if (memcmp(command, "clock", 5)', begin)
        web_source = (ROOT / "src/helpers/esp32/WebConfigServer.cpp").read_text()
        window = extract_braced(web_source, "  if (WebConfigBatch::unconfiguredSetupWindowExpired(")
        stop_owned = extract_braced(web_source, "static void stopOwnedWiFiRadio()")
        return (FIXTURE.replace("@INLINE_METHODS@", inline.replace(" override", ""))
                .replace("@METHODS@", methods).replace("@DEFAULTS@", defaults)
                .replace("@STARTUP@", startup).replace("@PORTAL_LOOP@", portal_loop)
                .replace("@MANIFEST@", manifest).replace("@OTA_CLI@", cli[begin:end])
                .replace("@OTA_STATE@", state)
                .replace("@INFRA_BACKEND@", infra_backend)
                .replace("@STOP_OWNED_WIFI@", stop_owned)
                .replace("@SETUP_WINDOW@", window).replace("@CHECKS@", checks)
                .replace("@WORKER@", worker))

    def test_actual_full_startup_timeout_retry_ota_and_teardown(self):
        for role in ("simple_repeater", "simple_room_server"):
            with self.subTest(role=role):
                self.compile_and_run(self.fixture(role), *FLAGS)

    def test_suspended_mqtt_idle_portal_and_foreground_ownership(self):
        checks = r'''
int main() {
  MyMesh node; char reply[160] = {};
  strcpy(node._cli.prefs.wifi_ssid, "working-network");
  auto& power = mesh::wifi::networkPower();
  power.setManaged(true);
  assert(node.setMqttBridgeState(true));
  assert(node.startWebConfig(false, reply));
  node._webconfig->_mode = WebConfigServer::MODE_LAN;
  now += 120001; // Explicit-start lease has elapsed; listener is idle.
  power.consumeProbeRequest();
  node.@WORKER@->network_suspended = true;
  power.setSuspended(true);
  unsigned off_before = WiFi.off_modes;
  node.serviceIdleWiFi();
  assert(WiFi.off_modes == off_before && sdk_started);
  assert(node.hasPendingWork()); // Cannot sleep before worker shutdown completes.
  WiFi.mode(WIFI_OFF); // Only the Core 0 network owner performs this shutdown.
  node.serviceIdleWiFi();
  assert(!sdk_initialized && !node.hasPendingWork());
  assert(node.getPowerSaveSleepSeconds(30) == 30);
  // An operator can wake the existing listener without creating a second
  // server, taking station ownership, or deinitializing the worker's driver.
  assert(node.startWebConfig(false, reply));
  assert(power.consumeProbeRequest());
  assert(node._webconfig->isNetworkForegroundActive(now));
  assert(node.hasPendingWork());
  now += 120001;
  assert(!node.hasPendingWork());
  power.setHold(mesh::wifi::NetworkPowerHold::OtaSeeder, true);
  assert(node.hasPendingWork());
  power.setHold(mesh::wifi::NetworkPowerHold::OtaSeeder, false);
  assert(!node.hasPendingWork());
  power.setSuspended(false);
  node.@WORKER@->network_suspended = false;
  node.serviceIdleWiFi();
  assert(node.hasPendingWork()); // Resume is owned by the MQTT worker too.
  power.setManaged(false);
}
'''
        for role in ("simple_repeater", "simple_room_server"):
            with self.subTest(role=role):
                self.compile_and_run(self.fixture(role, checks), *FLAGS)

    def test_primary_radios_and_wifi_disabled_images_do_not_reclaim_wifi(self):
        for role in ("simple_repeater", "simple_room_server"):
            source = (ROOT / "examples" / role / "MyMesh.cpp").read_text()
            cleanup = extract_braced(source, "void MyMesh::serviceIdleWiFi()")
            fixture = "struct MyMesh { void serviceIdleWiFi(); };\n" + cleanup
            fixture += "\nint main() { MyMesh node; node.serviceIdleWiFi(); }\n"
            # No WiFi declarations at all: these disabled bodies cannot reference it.
            for flags in ([], ["-DMESH_PRIMARY_ESPNOW=1", "-DWITH_ESPNOW_BRIDGE=1"],
                          ["-DMESH_ESPNOW_RADIO=1", "-DWITH_ESPNOW_BRIDGE=1"]):
                with self.subTest(role=role, flags=flags):
                    self.compile_and_run(fixture, *flags)

    def test_non_full_setup_retains_legacy_bridge_retry_policy(self):
        checks = r'''
int main() {
  MyMesh node; node.defaults();
  node.setBridgeState(true); WiFi.connected = true;
  node.startSharedEspNowBridgeIfReady(); node.startupSetup();
  assert(node.isWebConfigActive() && !node._unconfigured_setup_espnow_suspended);
  now += 10000;
  assert(node.startSharedEspNowBridgeIfReady());
  assert(node.espnow_bridge.isRunning());
}
'''
        flags = [flag for flag in FLAGS if not flag.startswith("-DMESHCORE_EXPANDED_PARTITION_PROFILE=")]
        for role in ("simple_repeater", "simple_room_server"):
            with self.subTest(role=role):
                self.compile_and_run(self.fixture(role, checks), *flags)

    def test_manual_wifi_and_master_restore_preserve_actual_requested_services(self):
        backend = extract_braced((ROOT / "examples/InfrastructureWireless.h").read_text(),
                                 "class InfrastructureWirelessBackend") + ";"
        backend = "MyMesh the_mesh; Board& board = the_mesh._cli.board;\n" + backend
        checks = r'''
void command(const char* text) {
  char reply[160] = {};
  auto& control = mesh::wireless::control();
  assert(control.handle(text, reply, sizeof(reply), now, mesh::wireless::Independent));
  assert(strncmp(reply, "OK", 2) == 0);
  for (unsigned attempt = 0; attempt < 5 && control.pending(); ++attempt) {
    now += 300; the_mesh.servicePortal(); control.service(now); the_mesh.serviceIdleWiFi();
  }
  assert(!control.pending());
  control.handle("get 2.4ghz", reply, sizeof(reply), now, mesh::wireless::Independent);
  assert(!strstr(reply, "failed"));
}
int main() {
  using namespace mesh::wireless;
  InfrastructureWirelessBackend backend;
  auto& control = mesh::wireless::control(); control.begin(backend);
  the_mesh.defaults(); assert(the_mesh._cli.prefs.wifi_ssid[0] == 0);
  the_mesh.startupSetup();
  assert(the_mesh._unconfigured_setup_espnow_suspended && !the_mesh.isEspNowBridgeRunning());
  assert(the_mesh._prefs.bridge_enabled && the_mesh._prefs.espnow_bridge_enabled);
  char reply[160] = {};
  the_mesh.stopWebConfig(reply); the_mesh.servicePortal(); the_mesh.serviceIdleWiFi();
  assert(backend.enabled() == 0 && !sdk_initialized);
  constexpr uint8_t requested = @REQUESTED@;
  if (requested & EspNow) command("set espnow on");
  const auto espnow_stops = the_mesh.espnow_bridge.stops;
  if (requested & mesh::wireless::WiFi) command("set wifi on");
  // Exact hardware regression: explicit ESP-NOW, then manual WiFi on, no SSID.
  assert(backend.enabled() == requested);
  assert(the_mesh.espnow_bridge.stops == espnow_stops);
  assert(!the_mesh.isMqttBridgeRunning());
  for (unsigned repeat = 0; repeat < 2; ++repeat) {
    command("set 2.4ghz off");
    assert(control.masterOff() && backend.enabled() == 0 && !sdk_initialized);
    command("set 2.4ghz on");
    assert(!control.masterOff() && backend.enabled() == requested);
    assert(the_mesh._prefs.bridge_enabled && the_mesh._prefs.espnow_bridge_enabled);
    // Saved default intent must not add ESP-NOW to a WiFi-only restore.
    if (!(requested & EspNow)) {
      assert(the_mesh._unconfigured_setup_espnow_suspended);
      assert(!the_mesh.startSharedEspNowBridgeIfReady());
    }
  }
  if (requested == (mesh::wireless::WiFi | EspNow)) {
    // The direct forced manual portal route preserves the same live bridge.
    the_mesh.stopWebConfig(reply); the_mesh.servicePortal(); the_mesh.serviceIdleWiFi();
    assert(the_mesh.isEspNowBridgeRunning() && sdk_started);
    const auto stops = the_mesh.espnow_bridge.stops;
    the_mesh.startWebConfig(true, reply);
    assert(the_mesh.isWebConfigActive() && the_mesh.isEspNowBridgeRunning());
    assert(the_mesh.espnow_bridge.stops == stops && !the_mesh._unconfigured_setup_espnow_suspended);
  }
}
'''
        for role in ("simple_repeater", "simple_room_server"):
            for mask in ("EspNow", "mesh::wireless::WiFi", "(mesh::wireless::WiFi | EspNow)"):
                with self.subTest(role=role, requested=mask):
                    body = self.fixture(role, checks.replace("@REQUESTED@", mask), backend)
                    self.compile_and_run(body, *FLAGS, "-DESP32=1")
                    if mask.startswith("("):
                        # Reintroducing the manual suspension must break the
                        # exact command/replay regression, not just a source check.
                        old = body.replace("return startWebConfigImpl(force_ap, reply, false);",
                                           "return startWebConfigImpl(force_ap, reply, true);")
                        self.assertNotEqual(old, body)
                        with self.assertRaises(AssertionError):
                            self.compile_and_run(old, *FLAGS, "-DESP32=1")

    def test_failed_portal_preserves_explicit_bridge_and_prior_setup_suspension(self):
        checks = r'''
struct WirelessBackend : mesh::wireless::Backend {
  uint8_t state = mesh::wireless::WiFi;
  uint8_t available() const override { return mesh::wireless::WiFi; }
  uint8_t enabled() const override { return state; }
  uint8_t clients() const override { return mesh::wireless::Independent; }
  mesh::wireless::Result set(uint8_t service, bool on) override {
    state = on ? state | service : state & ~service; return mesh::wireless::Result::Done;
  }
};
int main() {
  MyMesh node; node.defaults(); char reply[160] = {};
  assert(node.setEspNowBridgeState(true));
  WebConfigServer::allow_start = false;
  for (bool force_ap : {false, true}) {
    assert(node.startWebConfig(force_ap, reply));
    assert(strstr(reply, "failed to start AP"));
    assert(node.espnow_bridge.isRunning() && !node._unconfigured_setup_espnow_suspended);
    assert(node._prefs.espnow_bridge_enabled == 1 && node._prefs.bridge_enabled == 1);
    node.servicePortal(); node.serviceIdleWiFi();
    assert(!node._webconfig && sdk_started && node.espnow_bridge.isRunning());
  }
  WirelessBackend backend;
  mesh::wireless::control().begin(backend);
  assert(mesh::wireless::control().handle("set wifi off force", reply, sizeof(reply), now,
                                       mesh::wireless::Independent));
  now += 300; mesh::wireless::control().service(now);
  assert(mesh::wireless::control().blocked(mesh::wireless::WiFi));
  const auto espnow_stops = node.espnow_bridge.stops;
  node.startWebConfig(true, reply);
  assert(strstr(reply, "WiFi disabled") && !node._webconfig);
  assert(node.espnow_bridge.stops == espnow_stops && node.espnow_bridge.isRunning());
  assert(!node._unconfigured_setup_espnow_suspended);
  assert(mesh::wireless::control().allowService(mesh::wireless::WiFi));
  // Do not interrupt an active MQTT task that cannot be synchronously restarted.
  node.setBridgeState(true); WiFi.connected = true; now += 10000;
  node.startSharedEspNowBridgeIfReady();
  const auto mqtt_stops = node.@WORKER@->stops, second_espnow_stops = node.espnow_bridge.stops;
  node.startWebConfig(false, reply);
  assert(strstr(reply, "MQTT bridge is running") && !node._webconfig);
  assert(node.isMqttBridgeRunning() && node.espnow_bridge.isRunning());
  assert(node.@WORKER@->stops == mqtt_stops && node.espnow_bridge.stops == second_espnow_stops);
  node.@WORKER@->running = false; node.@WORKER@->stopping = true;
  node.startWebConfig(true, reply);
  assert(strstr(reply, "MQTT bridge is stopping") && !node._webconfig);
  assert(node.@WORKER@->stops == mqtt_stops && node.espnow_bridge.stops == second_espnow_stops);
  // A previous successful setup suspension must survive a later AP error.
  MyMesh cold; cold.defaults(); WebConfigServer::allow_start = true;
  cold.startupSetup(); cold.stopWebConfig(reply); cold.servicePortal(); cold.serviceIdleWiFi();
  assert(cold._unconfigured_setup_espnow_suspended && !cold.espnow_bridge.isRunning());
  WebConfigServer::allow_start = false;
  cold.startWebConfig(true, reply); cold.servicePortal();
  assert(cold._unconfigured_setup_espnow_suspended && !cold.espnow_bridge.isRunning());
  assert(!cold.isMqttBridgeRunning() && cold._prefs.espnow_bridge_enabled == 1);
}
'''
        for role in ("simple_repeater", "simple_room_server"):
            with self.subTest(role=role):
                self.compile_and_run("#include <initializer_list>\n" + self.fixture(role, checks), *FLAGS)

    def test_first_boot_lifecycle_rejects_unsuspended_background_retry(self):
        for role in ("simple_repeater", "simple_room_server"):
            with self.subTest(role=role):
                body = self.fixture(role)
                old = body.replace("if (_unconfigured_setup_espnow_suspended) return false;", "")
                self.assertNotEqual(old, body)
                # The pre-fix periodic retry revives ESP-NOW during setup and
                # keeps the radio running after its absolute AP window.
                with self.assertRaises(AssertionError):
                    self.compile_and_run(old, *FLAGS)

    def test_role_loop_calls_cleanup_after_portal_service_and_before_sleep(self):
        for role in ("simple_repeater", "simple_room_server"):
            source = (ROOT / "examples" / role / "MyMesh.cpp").read_text()
            signature = ("void __attribute__((noinline)) MyMesh::servicePostMeshLoop()"
                         if role == "simple_repeater" else "void MyMesh::loop()")
            loop = extract_braced(source, signature)
            self.assertEqual(loop.count("serviceIdleWiFi();"), 1)
            self.assertLess(loop.index("_webconfig->tick(millis());"), loop.index("serviceIdleWiFi();"))


if __name__ == "__main__":
    unittest.main()
