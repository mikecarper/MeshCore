"""Exercise production portal ownership and failed-join recovery without a radio."""
from pathlib import Path
import re
import unittest

from test_replay_reset_integration import extract_braced
import test_wifi_ota_start as wifi_start
import test_webconfig_login_throttle as login_tests

ROOT = Path(__file__).resolve().parents[1]


class WebConfigStationOwnershipTest(unittest.TestCase):
    compile_and_run = wifi_start.WiFiOtaStartTest.compile_and_run

    def test_infrastructure_uses_actual_mqtt_worker_and_refreshes_ownership(self):
        fixture = (ROOT / "test/fixtures/webconfig_station_owner.cpp").read_text()
        for role, worker in (("simple_repeater", "mqtt_bridge"),
                             ("simple_room_server", "bridge")):
            with self.subTest(role=role):
                source = (ROOT / "examples" / role / "MyMesh.cpp").read_text()
                start = extract_braced(source, "bool MyMesh::startWebConfig(")
                start += "\n" + extract_braced(source, "bool MyMesh::startWebConfigImpl(")
                tick = source.index("_webconfig->tick(millis());")
                if role == "simple_repeater":
                    start += "\n" + extract_braced(source, "bool MyMesh::webConfigOwnsWiFi(")
                    refresh_start = source.rfind("_webconfig->updateWiFiOwnership(", 0, tick)
                    refresh = source[refresh_start:tick].strip()
                else:
                    refresh_start = source.rfind("#ifdef WITH_MQTT_BRIDGE", 0, tick)
                    refresh_end = source.index("#endif", refresh_start) + len("#endif")
                    self.assertLess(refresh_end, tick)
                    self.assertFalse(source[refresh_end:tick].strip())
                    refresh = source[refresh_start:refresh_end]
                self.assertIn("_webconfig->updateWiFiOwnership(", refresh)
                body = fixture.replace("@WORKER@", worker).replace("@START@", start)
                body = body.replace("@SUSPEND@", extract_braced(source,
                    "void MyMesh::suspendUnconfiguredSetupBridges()"))
                body = body.replace("@REFRESH@", refresh)
                self.compile_and_run(body, "-DWITH_MQTT_BRIDGE=1")
                # A stopped/unconfigured MQTT implementation used to keep
                # ownership forever merely because its code was compiled in.
                stale = (body.replace("return owns_wifi;", "return false;", 1)
                         if role == "simple_repeater" else re.sub(
                             r"owns_wifi = !.*?;", "owns_wifi = false;", body, count=1, flags=re.DOTALL))
                self.assertNotEqual(body, stale)
                with self.assertRaises(AssertionError):
                    self.compile_and_run(stale, "-DWITH_MQTT_BRIDGE=1")

    def station_fixture(self):
        source = (ROOT / "src/helpers/esp32/WebConfigServer.cpp").read_text()
        header = (ROOT / "src/helpers/esp32/WebConfigServer.h").read_text()
        fixture = (ROOT / "test/fixtures/webconfig_station_recovery.cpp").read_text()
        constructor = extract_braced(source, "WebConfigServer::WebConfigServer(")
        start = extract_braced(source, "bool WebConfigServer::startAutoMode(")
        attempt = extract_braced(source, "bool WebConfigServer::beginSavedStation(")
        setter = extract_braced(header, "void updateWiFiOwnership(")
        prepare = extract_braced(source, "bool WebConfigServer::prepareStationOwner(")
        tick = extract_braced(source, "void WebConfigServer::tick(")
        # Execute the complete production join/fallback/retry/promotion blocks.
        # HTTP batching, display, and settings routes below them are irrelevant.
        begin = tick.index("  if (_mode == MODE_OFF) return;")
        end = tick.index("  if (_dns) _dns->processNextRequest();")
        recovery = "void WebConfigServer::tick(uint32_t now) {\n" + tick[begin:end] + "\n}"
        batch_start = source.index('      if (_standalone_wifi && strcmp(e.key, "wifi.ssid")')
        batch_end = source.index("      _cb->execCommand(e.cmd, e.reply);", batch_start)
        batch = source[batch_start:batch_end]
        capability = re.search(r'  doc\["wifi_psk64"\] = .*?;', source).group(0)
        return (fixture.replace("@CONSTRUCTOR@", constructor).replace("@START@", start)
                .replace("@SETTER@", setter).replace("@RECOVERY@", recovery)
                .replace("@PREPARE@", prepare)
                .replace("@ATTEMPT@", attempt).replace("@CREDENTIAL_BATCH@", batch)
                .replace("@PSK_CAPABILITY@", capability))

    def test_failed_boot_join_retries_saved_credentials_with_no_mqtt_owner(self):
        self.compile_and_run(self.station_fixture(), "-DWITH_MQTT_BRIDGE=1")

    def test_external_owner_excluded_and_runtime_ownership_can_transfer(self):
        self.compile_and_run(self.station_fixture(), "-DWITH_MQTT_BRIDGE=1",
                             "-DTEST_EXTERNAL_OWNER=1")

    def test_udp_claims_connecting_or_idle_recovery_ap_and_preserves_explicit_ap(self):
        self.compile_and_run(self.station_fixture(), "-DWITH_MQTT_BRIDGE=1",
                             "-DTEST_UDP_STATION_OWNER=1")

    def test_canonical_raw_psk_wins_and_legacy_mqtt_credentials_still_load(self):
        fixture = self.station_fixture()
        self.compile_and_run(fixture, "-DWITH_MQTT_BRIDGE=1",
                             "-DTEST_CREDENTIAL_PRIORITY=1")
        for old in (
                fixture.replace('doc["wifi_psk64"] = _standalone_wifi;',
                                'doc["wifi_psk64"] = (_mqtt_prefs == nullptr || !_owns_wifi);'),
                fixture.replace('_standalone_wifi && strcmp(e.key,',
                                '(_mqtt_prefs == nullptr || !_owns_wifi) && strcmp(e.key,')):
            self.assertNotEqual(old, fixture)
            with self.assertRaises(AssertionError):
                self.compile_and_run(old, "-DWITH_MQTT_BRIDGE=1",
                                     "-DTEST_CREDENTIAL_PRIORITY=1")

    def test_non_mqtt_portal_retains_saved_station_recovery(self):
        self.compile_and_run(self.station_fixture())

    def test_saved_power_policy_survives_a_delayed_station_start(self):
        fixture = self.station_fixture().split("int main() {", 1)[0] + r'''
int main() {
  const uint8_t key[32] = {};
  const int expected[] = {WIFI_PS_MIN_MODEM, WIFI_PS_NONE, WIFI_PS_MAX_MODEM};
  for (uint8_t preference = 0; preference < 3; ++preference) {
    WiFi = FakeWiFi{};
    WiFi.current_mode = 0;
    WebConfigServer portal(nullptr, nullptr, true, key, "test", "test", "repeater", "W12", true);
    portal._wifi_power_save = preference;
    assert(portal.beginSavedStation(clock_ms));
    assert(applied_ps == expected[preference]);
    // The SDK setting was correct, but Arduino can deliver STA_START later
    // and reapply its cached MIN_MODEM default over the saved NONE/MAX policy.
    WiFi.deliverStaStart();
    assert(applied_ps == expected[preference]);
    // setSleep returns false for an unchanged cache; another attempt is valid.
    assert(!WiFi.setSleep(expected[preference]));
    assert(portal.beginSavedStation(++clock_ms));
    assert(applied_ps == expected[preference]);
  }
}
'''
        self.compile_and_run(fixture)
        stale = fixture.replace("  (void)WiFi.setSleep(ps_mode);\n", "", 1)
        self.assertNotEqual(stale, fixture)
        with self.assertRaises(AssertionError):
            self.compile_and_run(stale)

    def test_shared_outage_blocks_idle_portal_reconnect_and_allows_foreground(self):
        self.compile_and_run(self.station_fixture(), "-DWITH_MQTT_BRIDGE=1",
                             "-DTEST_BACKGROUND_SUSPEND=1")

    def test_manual_companion_start_preserves_legacy_join_and_15s_fallback(self):
        self.compile_and_run(self.station_fixture(), "-DWITH_MQTT_BRIDGE=1",
                             "-DTEST_LEGACY_COMPANION=1")

    def delayed_login_fixture(self, without_initialization=False):
        source = (ROOT / "src/helpers/esp32/WebConfigServer.cpp").read_text()
        fixture = self.station_fixture().split("int main() {", 1)[0]
        auth = (ROOT / "test/fixtures/webconfig_login_handler.cpp").read_text()
        transport = "\n".join(extract_braced(auth, signature) + ";" for signature in (
            "struct Client", "struct AsyncWebHeader", "struct AsyncWebServerResponse", "struct AsyncResponseStream",
            "struct AsyncWebServerRequest"))
        transport = r'''
#include <map>
#include <ArduinoJson.h>
#include <helpers/WebConfigLoginThrottle.h>
struct NodeSnapshot { char admin_password[16] = {}; };
struct AsyncWebServer {
  bool credential_ready_at_begin = false;
  explicit AsyncWebServer(int port) { assert(port == 80); }
  void begin() {
    mesh::esp32::managementPassword::Snapshot current;
    credential_ready_at_begin = mesh::esp32::managementPassword::snapshot(current);
  }
};
struct DefaultHeaders {
  static DefaultHeaders& Instance() { static DefaultHeaders value; return value; }
  void addHeader(const char*, const char*) {}
};
static uint32_t random_word = 123;
uint32_t esp_random() { return ++random_word; }
''' + transport
        fixture = fixture.replace("class WebConfigServer {", transport + "\nclass WebConfigServer {")
        fixture = fixture.replace("  struct Callbacks { bool supportsRoomService() const { return false; } };", r'''
  struct Callbacks {
    NodeSnapshot node = {"saved-secret"}; unsigned snapshots = 0;
    bool supportsRoomService() const { return false; }
    void getNodeSnapshot(NodeSnapshot& out) { ++snapshots; out = node; }
  };
  static AsyncWebServer* _host;
  AsyncWebServer* _server = nullptr;
  char _session_token[33] = {};
  uint32_t _session_last_seen = 0, _session_generation = 0, _ap_auth_generation = 0;
  WebConfigLoginThrottle::Tracker _login_throttle;
  void initializeManagementPassword();
  void handleLogin(AsyncWebServerRequest*);
  void noteNetworkActivity() {}
  void registerRoutes() {}
''')
        fixture = fixture.replace("  int status() const { return state; }",
                                  "  int status() const { return state; }\n  int softAPIP() const { return 4; }")
        fixture = fixture.replace("  void createServer() { ++server_starts; }", "  void createServer();")
        create = extract_braced(source, "void WebConfigServer::createServer(")
        if without_initialization:
            self.assertIn("  initializeManagementPassword();", create)
            create = create.replace("  initializeManagementPassword();\n", "", 1)
        # A counter observes the complete production server-start body without
        # replacing its credential, route attachment, or HTTP begin ordering.
        create = create.replace("{\n", "{\n  ++server_starts;\n", 1)
        fixture += "\nAsyncWebServer* WebConfigServer::_host = nullptr;\n" + create + "\n"
        fixture += "\n".join(extract_braced(source, signature) for signature in (
            "void WebConfigServer::initializeManagementPassword(",
            "static bool fixedTimeEquals(", "void WebConfigServer::handleLogin("))
        return fixture + r'''
static int browserLogin(WebConfigServer& portal, const char* password) {
  AsyncWebServerRequest request;
  request.socket.remote = 0x0304A8C0;
  request.body = std::string("{\"password\":\"") + password + "\"}";
  request._tempObject = request.body.data();
  portal.handleLogin(&request);
  if (request.status == 503) assert(request.reply == "{\"error\":\"credentials unavailable\"}");
  return request.status;
}
int main() {
  namespace password = mesh::esp32::managementPassword;
  password::Snapshot initial;
  assert(!password::snapshot(initial));
  const uint8_t key[32] = {};
  char reply[160] = {};
  WebConfigServer::Callbacks repeater;
  WebConfigServer portal(&repeater, nullptr, true, key, "test", "test", "repeater", "G3", true);
  portal.updateWiFiOwnership(true);
  assert(portal.startAutoMode(reply));
  assert(portal._mode == WebConfigServer::MODE_CONNECTING && portal.server_starts == 0);
  assert(!password::snapshot(initial)); // No earlier AP/immediate-LAN path initializes it.
  WiFi.state = WL_CONNECTED;
  portal.tick(++clock_ms);
  assert(portal._mode == WebConfigServer::MODE_LAN && portal.server_starts == 1);
  assert(browserLogin(portal, "wrong-secret") == 401);
  assert(browserLogin(portal, "saved-secret") == 200);
  assert(portal._server->credential_ready_at_begin);
  assert(repeater.snapshots == 1); // HTTP authentication never reads mutable prefs.

  // A Room listener may wait for another station owner. Its start must retain
  // a newer committed credential rather than publish a candidate RAM snapshot.
  assert(password::publish("committed-key"));
  password::Snapshot committed; assert(password::snapshot(committed));
  WebConfigServer::Callbacks room;
  strcpy(room.node.admin_password, "temporary-RAM");
  WiFi.state = WL_DISCONNECTED;
  WebConfigServer waiting(&room, nullptr, false, key, "test", "test", "room", "G3", true);
  waiting.updateWiFiOwnership(false);
  assert(waiting.startAutoMode(reply));
  assert(waiting._mode == WebConfigServer::MODE_CONNECTING && waiting.server_starts == 0);
  WiFi.state = WL_CONNECTED;
  waiting.tick(++clock_ms);
  assert(waiting._mode == WebConfigServer::MODE_LAN && waiting.server_starts == 1);
  assert(browserLogin(waiting, "temporary-RAM") == 401);
  assert(browserLogin(waiting, "committed-key") == 200);
  assert(waiting._server->credential_ready_at_begin);
  password::Snapshot after; assert(password::snapshot(after));
  assert(after.generation == committed.generation && !strcmp(after.password, committed.password));
  assert(room.snapshots == 0);
}
'''

    def test_delayed_station_login_initializes_credentials_before_http_and_keeps_committed_value(self):
        json_include = login_tests.WebConfigLoginThrottleTest.json_include(self)
        for mqtt in (False, True):
            with self.subTest(mqtt=mqtt):
                prefix = "#define WITH_MQTT_BRIDGE 1\n" if mqtt else ""
                login_tests.WebConfigLoginThrottleTest.compile_run(
                    self, prefix + self.delayed_login_fixture(), (json_include,))
                # Without the real createServer initializer, the delayed LAN
                # path reproduces the device's 503 credentials-unavailable error.
                login_tests.WebConfigLoginThrottleTest.compile_run(
                    self, prefix + self.delayed_login_fixture(True),
                    (json_include,), expect_rejection=True)


if __name__ == "__main__":
    unittest.main()
