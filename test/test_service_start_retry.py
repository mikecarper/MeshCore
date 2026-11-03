"""Execute service start intent, bounded retries and explicit cancellation."""
from pathlib import Path
import unittest

from test_replay_reset_integration import extract_braced
import test_wifi_ota_start as wifi_start
import test_mqtt_transport_results as mqtt

ROOT = Path(__file__).resolve().parents[1]

WEB = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <helpers/WebConfigBatch.h>
uint32_t now = 1000;
uint32_t millis() { return now; }
namespace mesh {
namespace wireless {
enum { WiFi = 1 };
struct Control { bool disabled = false; bool blocked(int) { return disabled; } };
Control& control() { static Control c; return c; }
}
namespace wifi {
bool compatible = true;
bool accessPointCompatibleWithLongRange() { return compatible; }
}
struct Log { void println(const char*) {} };
Log& usbDebugPort() { static Log log; return log; }
}
struct Endpoint { unsigned stops = 0; void end() { ++stops; } void stop() { ++stops; } };
struct WebConfigServer {
  enum Mode { MODE_OFF, MODE_SETUP, MODE_CONNECTING, MODE_LAN };
  Mode _mode = MODE_OFF, _start_mode = MODE_OFF;
  uint32_t _start_retry_at = 0, _start_retry_delay = 5000;
  bool _start_retryable = false, _stopping = false, _stop_warned = false;
  uint32_t _stop_warn_at = 0;
  static constexpr unsigned STOP_WARN_MS = 1000;
  Endpoint server, dns; Endpoint* _server = &server; Endpoint* _dns = &dns;
  char _wifi_ssid[32] = {};
  bool fail_start = false, invalid = false;
  unsigned starts = 0; bool last_ap = false; char last_ssid[32] = {};
  bool isRunning() const { return _mode != MODE_OFF; }
  bool isStopping() const { return _stopping; }
  bool isStartPending() const { return _start_mode != MODE_OFF; }
  void detachRoutes() {}
  void refreshNetworkPowerHold(uint32_t) {}
  bool start(bool ap, char* reply) {
    ++starts; last_ap = ap; strcpy(last_ssid, _wifi_ssid);
    _start_retryable = fail_start && !invalid;
    if (fail_start || invalid) { strcpy(reply, "Err: startup failed"); return false; }
    _mode = ap ? MODE_SETUP : MODE_CONNECTING;
    strcpy(reply, "WebConfig started"); return true;
  }
  bool startSetupMode(char* reply) { return start(true, reply); }
  bool startAutoMode(char* reply) { return start(false, reply); }
  bool requestStart(bool, char*);
  void requestRestart(); void requestStop(); void serviceStartRetry(uint32_t);
};
@METHODS@
int main() {
  char reply[160]; WebConfigServer s;
  s.fail_start = true;
  assert(s.requestStart(true, reply) && s.isStartPending() && s.starts == 1);
  assert(strstr(reply, "pending"));
  for (unsigned i = 0; i < 100; ++i) s.serviceStartRetry(now);
  assert(s.starts == 1);
  now = s._start_retry_at - 1; s.serviceStartRetry(now); assert(s.starts == 1);
  ++now; s.serviceStartRetry(now); assert(s.starts == 2);
  assert(s._start_retry_at == now + 10000);
  for (unsigned i = 0; i < 8; ++i) {
    now = s._start_retry_at; s.serviceStartRetry(now);
    assert(s._start_retry_at > now && s._start_retry_at <= now + 60000);
  }
  s.fail_start = false; now = s._start_retry_at; s.serviceStartRetry(now);
  assert(s.isRunning() && !s.isStartPending() && s.last_ap);

  // Off/on during teardown waits for cleanup and never loses desired on.
  s.requestStop(); const auto before = s.starts;
  assert(s.requestStart(false, reply) && s.isStartPending());
  now = s._start_retry_at; s.serviceStartRetry(now); assert(s.starts == before);
  s._stopping = false; s.serviceStartRetry(now);
  assert(s.starts == before + 1 && !s.last_ap && !s.isStartPending());

  // SSID/password writes share one restart with the newest saved tuple.
  strcpy(s._wifi_ssid, "first"); s.requestRestart();
  now += 500; strcpy(s._wifi_ssid, "latest"); s.requestRestart();
  s._stopping = false; now += 999; s.serviceStartRetry(now);
  assert(s.starts == before + 1);
  ++now; s.serviceStartRetry(now);
  assert(s.starts == before + 2 && !strcmp(s.last_ssid, "latest"));

  // Explicit off cancels both a deferred teardown restart and a failed start.
  s.requestRestart(); s.requestStop();
  s.requestRestart(); // A credential save during explicit off must stay off.
  assert(!s.isStartPending()); s._stopping = false;
  now += 120000; s.serviceStartRetry(now);
  assert(!s.isStartPending() && s.starts == before + 2);
  s.fail_start = true; assert(s.requestStart(true, reply)); s.requestStop();
  now += 120000; s.serviceStartRetry(now); assert(!s.isStartPending());

  // Configuration and ownership conflicts remain errors, not accepted jobs.
  s.fail_start = false; s.invalid = true;
  assert(!s.requestStart(false, reply) && !s.isStartPending()); s.invalid = false;
  mesh::wireless::control().disabled = true;
  assert(!s.requestStart(false, reply) && !s.isStartPending());
  mesh::wireless::control().disabled = false; mesh::wifi::compatible = false;
  assert(!s.requestStart(true, reply) && !s.isStartPending());
  mesh::wifi::compatible = true;

  // Retry deadlines remain correct when millis() wraps.
  now = UINT32_MAX - 500; s.fail_start = true; assert(s.requestStart(true, reply));
  const auto attempts = s.starts;
  now += 4999; s.serviceStartRetry(now); assert(s.starts == attempts);
  ++now; s.fail_start = false; s.serviceStartRetry(now);
  assert(s.starts == attempts + 1 && !s.isStartPending());
}
'''


class ServiceStartRetryTests(unittest.TestCase):
    compile_and_run = wifi_start.WiFiOtaStartTest.compile_and_run

    def test_webconfig_retry_coalescing_cancellation_conflicts_and_rollover(self):
        source = (ROOT / 'src/helpers/esp32/WebConfigServer.cpp').read_text()
        methods = '\n'.join(extract_braced(source, signature) for signature in (
            'bool WebConfigServer::requestStart(', 'void WebConfigServer::requestRestart(',
            'void WebConfigServer::requestStop(', 'void WebConfigServer::serviceStartRetry('))
        self.compile_and_run(WEB.replace('@METHODS@', methods))

    def test_pending_starts_are_owned_work_without_claiming_mqtt_running(self):
        for role, worker in (('simple_repeater', 'mqtt_bridge'),
                             ('simple_room_server', 'bridge')):
            with self.subTest(role=role):
                header = (ROOT / 'examples' / role / 'MyMesh.h').read_text()
                methods = '\n'.join(extract_braced(header, signature).replace(' override', '')
                    for signature in ('bool isWebConfigActive() const',
                                      'bool isMqttBridgeRunning()',
                                      'bool isMqttBridgeStartPending()'))
                fixture = r'''
#include <cassert>
struct Service {
  bool running = false, stopping = false, pending = true;
  bool isRunning() const { return running; }
  bool isStopping() const { return stopping; }
  bool isStartPending() const { return pending; }
} web, mqtt;
struct MyMesh {
  Service* _webconfig = &web;
  Service* @WORKER@ = &mqtt;
@METHODS@
};
int main() {
  MyMesh node;
  assert(node.isWebConfigActive());
  assert(!node.isMqttBridgeRunning() && node.isMqttBridgeStartPending());
  web.pending = mqtt.pending = false;
  assert(!node.isWebConfigActive() && !node.isMqttBridgeStartPending());
}
'''.replace('@METHODS@', methods).replace('@WORKER@', worker)
                self.compile_and_run(fixture, '-DWITH_MQTT_BRIDGE=1')

    def test_companion_credential_reload_finishes_with_a_restarted_lan_listener(self):
        source = (ROOT / 'examples/companion_radio/main.cpp').read_text()
        actual = extract_braced(source, 'static void serviceCompanionWiFiCredentialReload(')
        fixture = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#define WITH_WEBCONFIG 1
#define WIFI_DEBUG_PRINTLN(...) ((void)0)
uint32_t now = 1000;
uint32_t millis() { return now; }
bool companion_wifi_credential_reload_pending = true;
uint32_t companion_wifi_credential_reload_at = 1500;
bool companion_wifi_espnow_restore_pending = true;
bool companion_wifi_requested = true, companion_wifi_active = true;
bool companion_wifi_has_credentials = true;
char configured_wifi_ssid[32] = {}, configured_wifi_password[65] = {};
const char* saved_ssid = "latest", *saved_password = "latest-password";
bool suspended = false, station_allowed = true;
bool companionWiFiBackgroundSuspended() { return suspended; }
void loadCompanionWiFiCredentials() {
  strcpy(configured_wifi_ssid, saved_ssid);
  strcpy(configured_wifi_password, saved_password);
}
void resetCompanionWiFiRecoveryState() {}
struct {
  bool transition = false, active = true;
  bool isWebConfigWiFiTransitionActive() const { return transition; }
  bool isWebConfigActiveOrStopping() const { return active; }
} the_mesh;
constexpr int WIFI_STA = 1, WIFI_IF_STA = 0;
bool station_disconnected = false;
unsigned power_save_applications = 0;
struct {
  void mode(int) {}
  void disconnect(bool stop, bool erase) {
    assert(!stop && !erase); station_disconnected = true;
  }
} WiFi;
namespace mesh { namespace wifi {
unsigned starts = 0;
bool stationMutationAllowed() { return station_allowed; }
void applyProtocolMask(int) {} void setStationAutoReconnect(bool) {}
void beginStation(const char* ssid, const char* password) {
  assert(station_disconnected && power_save_applications == starts + 1);
  ++starts; assert(!strcmp(ssid, "latest")); assert(!strcmp(password, "latest-password"));
}
} }
// The policy implementation has its own tests; observe this association seam.
bool applyCompanionWiFiPowerSave() {
  assert(station_disconnected); ++power_save_applications; return true;
}
struct { void noteAttempt(uint32_t) {} } wifi_reconnect_tracker;
@METHOD@
int main() {
  serviceCompanionWiFiCredentialReload(); assert(companion_wifi_credential_reload_pending);
  assert(!power_save_applications && !station_disconnected);
  now = 1500; the_mesh.transition = true;
  serviceCompanionWiFiCredentialReload(); assert(companion_wifi_credential_reload_pending);
  assert(!power_save_applications && !station_disconnected);
  the_mesh.transition = false; // The LAN listener is active, but does not own association.
  assert(the_mesh.isWebConfigActiveOrStopping());
  serviceCompanionWiFiCredentialReload();
  assert(!companion_wifi_credential_reload_pending && !companion_wifi_espnow_restore_pending);
  assert(mesh::wifi::starts == 1 && power_save_applications == 1);
  serviceCompanionWiFiCredentialReload();
  assert(mesh::wifi::starts == 1 && power_save_applications == 1);
}
'''.replace('@METHOD@', actual)
        self.compile_and_run(fixture)

    def test_mqtt_reenable_waits_for_cleanup_and_failed_start_retries_without_leaks(self):
        source = (ROOT / 'src/helpers/bridges/MQTTBridge.cpp').read_text()
        # Execute the real begin admission and complete queue/task acquisition,
        # rollback and commit. Endpoint parsing between them has its own tests.
        begin = extract_braced(source, 'void MQTTBridge::begin()')
        admission = begin[:begin.index('  cancelNtpRefresh();')]
        resources = begin[begin.index('  // Reset the handshake'):]
        begin = admission + '  ++starts; allocateRuntimeBuffers();\n' + resources
        preamble = mqtt.ASYNC_STOP_PREAMBLE.replace('  void begin() { assert(false); }', r'''
  unsigned starts = 0;
  void* _packet_queue_handle = nullptr; uint8_t* _packet_queue_storage = nullptr;
  int _packet_queue_struct = 0; void* _mqtt_task_handle = nullptr;
  struct QueuedPacket { uint8_t data[8]; };
  static constexpr unsigned MAX_QUEUE_SIZE = 4;
  static void mqttTask(void*) {}
  void allocateRuntimeBuffers() { resources_owned = true; }
  void releaseRuntimeBuffers() { resources_owned = false; }
  void begin(); void requestRestart();
''')
        allocator = r'''
#include <cstdlib>
#define BOARD_HAS_PSRAM 1
using BaseType_t = int;
constexpr int pdPASS = 1;
bool fail_queue = false, fail_task = false;
unsigned storage_live = 0, queues_live = 0;
void* psram_malloc(size_t n) { ++storage_live; return malloc(n); }
void psram_free(void* p) { if (p) { --storage_live; free(p); } }
void* xQueueCreateStatic(unsigned, size_t, uint8_t*, int*) {
  if (fail_queue) return nullptr; ++queues_live; return malloc(1);
}
void* xQueueCreate(unsigned n, size_t size) { return xQueueCreateStatic(n, size, nullptr, nullptr); }
void vQueueDelete(void* p) { assert(p); --queues_live; free(p); }
int xTaskCreatePinnedToCore(void (*)(void*), const char*, unsigned, void*, unsigned, void** task, int) {
  *task = fail_task ? nullptr : reinterpret_cast<void*>(1); return fail_task ? 0 : pdPASS;
}
'''
        # The fake endpoint's release effect owns the same queue/storage that
        # the production begin block acquires; lifecycle ordering stays real.
        preamble = preamble.replace('struct MQTTBridge;', allocator + '\nstruct MQTTBridge;', 1)
        preamble = preamble.replace('  _b->resources_owned = false;', '''  _b->resources_owned = false;
  if (_b->_packet_queue_handle) vQueueDelete(_b->_packet_queue_handle);
  _b->_packet_queue_handle = nullptr;
  psram_free(_b->_packet_queue_storage); _b->_packet_queue_storage = nullptr;''')
        methods = '\n'.join(extract_braced(source, signature) for signature in (
            'static inline uint32_t mqttStopTimeoutForSlots(int slots)',
            'void MQTTBridge::requestStop()', 'void MQTTBridge::requestRestart()',
            'void MQTTBridge::end()', 'void MQTTBridge::finishStopped()',
            'void MQTTBridge::loop()', 'uint32_t MQTTBridge::LifecycleOps::nowMs()',
            'void MQTTBridge::LifecycleOps::startTask()',
            'void MQTTBridge::LifecycleOps::deliverStop()'))
        scenarios = r'''
int main() {
  MQTTBridge b; bridge_waiting = &b;
  b.requestStop(); b.begin(); // A fresh on request cannot cancel an active worker stop.
  assert(b._start_requested && b._lifecycle.isStopInProgress() && !b.starts);
  for (unsigned i = 0; i < 20; ++i) b.loop();
  assert(!b.starts && b.releases == 0);
  b._stop_acked = true; b.loop(); assert(!b._initialized && b.releases == 1);
  fail_queue = true; b.loop();
  assert(b.starts == 1 && !b._initialized && b._start_requested);
  assert(!b.resources_owned && !queues_live && !storage_live);
  for (unsigned i = 0; i < 100; ++i) b.loop(); assert(b.starts == 1);
  clock_ms += 4999; b.loop(); assert(b.starts == 1);
  ++clock_ms; fail_queue = false; fail_task = true; b.loop();
  assert(b.starts == 2 && !b._initialized && !queues_live && !storage_live);
  clock_ms += 5000; fail_task = false; b.loop();
  assert(b.starts == 3 && b._initialized && queues_live == 1 && storage_live == 1);
  b.requestRestart(); b.requestStop(); // Explicit off cancels a queued restart.
  b._stop_acked = true; b.loop(); clock_ms += 10000; b.loop();
  assert(!b._start_requested && !b._initialized && b.starts == 3);
  assert(!queues_live && !storage_live);
  clock_ms = UINT32_MAX - 100; fail_queue = true; b.begin();
  const auto before = b.starts;
  clock_ms += 4999; b.loop(); assert(b.starts == before);
  ++clock_ms; b.loop(); assert(b.starts == before + 1);
  b.end(); assert(!b._start_requested); // Off cancels even an uninitialized failure.
  clock_ms += 10000; b.loop(); assert(b.starts == before + 1);
}
'''
        self.compile_and_run(preamble + begin + methods + scenarios)


if __name__ == '__main__':
    unittest.main()
