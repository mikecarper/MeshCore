#!/usr/bin/env python3
"""Exercise production MQTT scheduling with delayed callbacks and bounded heaps.

The actual maintenance/admission/reconnect methods run with a deterministic
clock. Hardware, SDK task starts, and token generation are controllable seams.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_mqtt_transport_results import method

ROOT = Path(__file__).resolve().parents[1]

PREAMBLE = r'''
#include <atomic>
#include <algorithm>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <functional>
#include <new>
#include <sys/time.h>
#include <vector>
#include "helpers/AlertFaultPolicy.h"
#include "helpers/MQTTConnectionAdmission.h"
#include "helpers/MQTTPublishAdmission.h"
#include "helpers/MQTTConnectionHealth.h"
#include "helpers/MQTTConnectionPolicy.h"
#include "helpers/MQTTTransportHealth.h"
#include "helpers/MQTTStatusPublishPolicy.h"
#include "helpers/WiFiPowerSave.h"
namespace Admission = MQTTConnectionAdmission;
#define ESP_PLATFORM 1
#define MQTT_DEBUG_PRINTLN(...) ((void)0)
using esp_err_t = int;
constexpr int ESP_OK = 0, ESP_ERR_INVALID_ARG = 1, ESP_ERR_INVALID_STATE = 2;
constexpr int ESP_ERR_NO_MEM = 3, RUNTIME_MQTT_SLOTS = 6, WL_CONNECTED = 1;
constexpr int WL_DISCONNECTED = 0, WIFI_POWER_11dBm = 11;
using wl_status_t = int;
using wifi_ps_type_t = int;
constexpr int WIFI_PS_NONE = 0, WIFI_PS_MAX_MODEM = 1, WIFI_PS_MIN_MODEM = 2;
namespace mesh { namespace wifi {
constexpr bool kPrimaryEspNowRadio = false;
bool enforceStationChannel() { return true; }
} }
bool mqttStationWiFiMutationAllowed() { return true; }
void esp_wifi_set_ps(wifi_ps_type_t) {}
constexpr int MQTT_AUTH_JWT = 1;
constexpr int MQTT_ERROR_TYPE_CONNECTION_REFUSED = 5;
struct esp_mqtt_error_codes {
  int esp_tls_last_esp_err = 0, esp_tls_stack_err = 0;
  int esp_transport_sock_errno = 0, error_type = 0, connect_return_code = 0;
};
constexpr uint32_t MALLOC_CAP_INTERNAL = 1, MALLOC_CAP_DMA = 2;
static uint32_t clock_ms = 1000;
static unsigned long wall_seconds = 1800000000;
static size_t dma_free = 100000, dma_largest = 100000;
static unsigned long s_wifi_connected_at = 0;
uint32_t millis() { return clock_ms; }
unsigned long fake_time(void*) { return wall_seconds; }
#define time fake_time
struct {
  int state = WL_CONNECTED;
  int status() const { return state; }
  void setTxPower(int) {}
  void disconnect() { state = WL_DISCONNECTED; }
} WiFi;
struct { size_t size = 2097152; size_t getPsramSize() const { return size; } } ESP;
size_t heap_caps_get_free_size(uint32_t caps) {
  assert(caps == MALLOC_CAP_INTERNAL || caps == (MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA));
  return dma_free;
}
size_t heap_caps_get_largest_free_block(uint32_t caps) {
  assert(caps == (MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA)); return dma_largest;
}
const char* esp_err_to_name(int) { return "mock"; }
struct Preset {
  const char* server_url = "wss://broker/mqtt";
  int auth_type = 0;
  unsigned long token_lifetime = 3300;
  bool enforce_exp = false;
  bool allow_retain = false;
};
bool mqttPresetEnforcesTokenExp(const Preset* preset) {
  return preset && preset->enforce_exp;
}
struct MQTTRadio2Info { const char* radio; const char* mode; bool temporary; };
namespace MQTTMessageBuilder {
void formatIsoTimestampForMqtt(long, long, const char*, char* buffer, size_t size) {
  snprintf(buffer, size, "2026-10-14T00:00:00+00:00");
}
template<typename... Args>
int buildStatusMessage(int, const char*, const char*, const char*, const char*,
                       const char*, const char*, const char*, const char*,
                       char* buffer, int size, Args...) {
  assert(size > 2);
  strcpy(buffer, "{}");
  return 2;
}
}
void formatRadioInfo(float, float, int, int, char* buffer, size_t size) {
  snprintf(buffer, size, "909.5,500,5,5");
}
struct PsychicMqttClient {
  bool initialized = false, started = false, live = false, model_task_heap = false;
  bool stop_succeeds = true;
  int starts = 0, reconnects = 0, bounces = 0, credentials = 0, stops = 0;
  uint32_t acknowledgments = 0;
  int publish_result = 0, publishes = 0;
  bool immediate_ack = false;
  std::function<void(bool)> connected_callback, disconnected_callback;
  std::function<void(esp_mqtt_error_codes)> error_callback;
  std::function<void()> disconnected;
  bool isInitialized() const { return initialized; }
  bool isStarted() const { return started; }
  bool connected() const { return live; }
  uint32_t getPublishAck() const { return acknowledgments; }
  void setAutoReconnect(bool enabled) { assert(!enabled); }
  void onConnect(std::function<void(bool)> callback) { connected_callback = callback; }
  void onDisconnect(std::function<void(bool)> callback) { disconnected_callback = callback; }
  void onError(std::function<void(esp_mqtt_error_codes)> callback) { error_callback = callback; }
  void errorWithoutDisconnect() {
    live = false; // The real adapter does this before calling onError.
    assert(error_callback);
    error_callback(esp_mqtt_error_codes{});
  }
  void connectEvent() {
    live = true;
    assert(connected_callback);
    connected_callback(false);
  }
  int publish(const char*, int qos, bool, const char*, int, bool async) {
    assert(async == (qos > 0));
    ++publishes;
    if (immediate_ack && qos > 0 && publish_result >= 0) ++acknowledgments;
    return publish_result;
  }
  int connect() {
    ++starts;
    if (model_task_heap) {
      assert(initialized && !started && dma_free >= 6536);
      dma_free -= 6536;
    }
    initialized = started = true;
    return ESP_OK;
  }
  int reconnect() { ++reconnects; return ESP_OK; }
  void disconnect() {
    ++stops;
    if (!stop_succeeds) return;
    if (model_task_heap && started) dma_free += 6536;
    started = live = false;
    if (disconnected) disconnected();
    if (disconnected_callback) disconnected_callback(false);
  }
  void softDisconnect() {
    ++bounces; live = false;
    // Releasing the old internal TLS session creates room for its replacement.
    dma_free += 44000; dma_largest = 50000;
  }
  void setCredentials(const char*, const char*) { ++credentials; }
  void serviceOutboxDiagnostics() {}
};
struct MQTTBridge {
  struct MQTTSlot {
    bool enabled = true, connected = false, initial_connect_done = false;
    const Preset* preset = nullptr;
    PsychicMqttClient* client = nullptr;
    char host[64] = "broker", broker_uri[128] = {}, audience[64] = {};
    uint16_t port = 8883;
    char* auth_token = nullptr;
    unsigned long token_expires_at = 0, last_token_renewal = 0;
    unsigned long last_reconnect_attempt = 0, connected_at_ms = 0;
    uint8_t reconnect_backoff = 0, max_backoff_failures = 0;
    bool circuit_breaker_tripped = false;
    uint32_t start_failures = 0;
    uint32_t connect_failures = 0, disconnect_count = 0;
    unsigned long first_disconnect_time = 0, current_outage_started_ms = 0;
    unsigned long last_log_time = 0;
    int32_t last_tls_err = 0, last_tls_stack_err = 0;
    int last_sock_errno = 0;
    unsigned long last_error_time = 0;
    MQTTTransportHealth::State transport_health;
    MQTTStatusPublishPolicy::State status_publish;
  } _slots[RUNTIME_MQTT_SLOTS];
  PsychicMqttClient clients[RUNTIME_MQTT_SLOTS];
  MQTTPublishAdmission::State _publish_memory;
  char tokens[RUNTIME_MQTT_SLOTS][16] = {};
  bool setup_succeeds[RUNTIME_MQTT_SLOTS] = {true, true, true, true, true, true};
  bool ready[RUNTIME_MQTT_SLOTS] = {true, true, true, true, true, true};
  std::atomic<bool> _slot_attempt_pending[RUNTIME_MQTT_SLOTS] = {};
  std::atomic<bool> _slot_transport_fault_pending[RUNTIME_MQTT_SLOTS] = {};
  std::atomic<bool> _slot_health_reset_pending[RUNTIME_MQTT_SLOTS] = {};
  std::atomic<uint32_t> _slot_health_recoveries[RUNTIME_MQTT_SLOTS] = {};
  std::atomic<uint8_t> _slot_health_reason[RUNTIME_MQTT_SLOTS] = {};
  std::atomic<bool> _slot_health_recovering[RUNTIME_MQTT_SLOTS] = {};
  std::atomic<uint32_t> _slot_health_ack_wait_secs[RUNTIME_MQTT_SLOTS] = {};
  std::atomic<bool> _status_publish_pending[RUNTIME_MQTT_SLOTS] = {};
  std::atomic<bool> _stop_requested{false}, _ntp_synced{true};
  bool _slot_force_jwt_mint[RUNTIME_MQTT_SLOTS] = {};
  void* _identity = this;
  bool _slots_setup_done = true;
  bool _wifi_status_initialized = true, _manage_wifi = false;
  bool _wifi_credentials_changed = false;
  wl_status_t _last_wifi_status = WL_CONNECTED;
  unsigned long _last_wifi_check = 0, _last_wifi_reconnect_attempt = 0;
  uint8_t _wifi_reconnect_backoff_attempt = 0, _wifi_power_save = 1;
  struct {
    bool canonical_wifi = false;
    float *freq = nullptr, *bw = nullptr;
    int *sf = nullptr, *cr = nullptr;
  } _node_info;
  struct Observer { uint8_t wifi_power_save = 1; bool mqtt_status_enabled = true; } observer;
  Observer* _obs = &observer;
  AlertFaultPolicy::OutageSnapshot _wifi_outage{};
  unsigned long _last_slot_reconnect_ms = 0;
  int _max_active_slots = 5, mints = 0;
  AlertFaultPolicy::OutageSnapshot wifiOutage() const { return _wifi_outage; }
  void setWifiOutage(AlertFaultPolicy::OutageSnapshot snapshot) { _wifi_outage = snapshot; }
  void beginWiFiStation() {}
  const char* _jwt_username = "jwt";
  static const unsigned long SLOT_SETUP_RETRY_INTERVAL = 60000;
  static const unsigned long SLOT_LOG_INTERVAL = 60000;
  static const unsigned long STATUS_RETRY_INTERVAL = 30000;
  unsigned long _status_interval = 300000;
  static constexpr int STATUS_JSON_BUFFER_SIZE = 1024, MSG_STATUS = 0;
  const char *_timezone = "UTC", *_origin = "node", *_device_id = "id";
  const char *_board_model = "V4", *_firmware_version = "test";
  int _json_scratch_doc = 0, _queue_count = 0;
  char _json_scratch_buffer[STATUS_JSON_BUFFER_SIZE] = {};
  struct Board { int getBattMilliVolts() const { return 4000; } };
  struct Clock { uint32_t getMillis() const { return clock_ms; } };
  struct Dispatcher {
    int getErrFlags() const { return 0; }
    int getTotalAirTime() const { return 0; }
    int getReceiveAirTime() const { return 0; }
    int getNumSentFlood() const { return 0; }
    int getNumSentDirect() const { return 0; }
    int getNumRecvFlood() const { return 0; }
    int getNumRecvDirect() const { return 0; }
  };
  struct Radio {
    int getNoiseFloor() const { return -120; }
    int getPacketsRecvErrors() const { return 0; }
  };
  Board* _board = nullptr;
  Clock* _ms = nullptr;
  Dispatcher* _dispatcher = nullptr;
  Radio* _radio = nullptr;
  void refreshOriginFromPrefs() {}
  void getClientVersion(char* output, size_t size) { snprintf(output, size, "test"); }
  int repeatStatus() const { return 0; }
  int collectConnHealth() const { return 0; }
  const MQTTRadio2Info* radio2StatusInfo(MQTTRadio2Info&, char*, size_t) const { return nullptr; }
  bool buildTopicForSlot(int, int, char* output, size_t size) {
    snprintf(output, size, "status");
    return true;
  }
  std::vector<int> setups;
  bool isSlotReady(int index) { return ready[index]; }
  bool setupSlot(int index) {
    setups.push_back(index);
    _slots[index].client = &clients[index];
    if (!setup_succeeds[index]) return false;
    if (reconnectSlotClient(index) != ESP_OK) return false;
    _slots[index].initial_connect_done = true;
    return true;
  }
  bool createSlotAuthToken(int index) {
    ++mints;
    strcpy(tokens[index], "fresh");
    _slots[index].auth_token = tokens[index];
    _slots[index].token_expires_at = wall_seconds + 3300;
    return true;
  }
  void complete(int index) {
    _slot_attempt_pending[index].store(false, std::memory_order_release);
    _slots[index].connected = clients[index].live = true;
    if (_slots[index].client) _slots[index].client->live = true;
    _slots[index].connected_at_ms = clock_ms;
    _slot_health_reset_pending[index] = true;
  }
  void updateCachedConnectionStatus() {}
  bool ensureSlotClient(int index);
  bool publishToSlot(int, const char*, const char*, size_t, bool, uint8_t);
  bool hasPublishHeadroom(size_t = 0, size_t = 0, uint8_t = 0) { return true; }
  bool publishStatusToSlot(int);
  bool publishStatus(uint8_t);
  void maintainStatus(uint32_t);
  void only(int index) {
    for (int i = 0; i < RUNTIME_MQTT_SLOTS; ++i) _slots[i].enabled = i == index;
  }
  int activatedSlotCount() const;
  bool canActivateSlot(int index) const;
  bool hasPendingSlotConnection() const;
  bool canStartSlotConnection(int index) const;
  esp_err_t reconnectSlotClient(int index);
  unsigned long slotTokenLifetime(int index) const;
  void maintainSlotConnections();
  bool recoverSlotTransport(int index, uint32_t now);
  void serviceSlotTransportHealth(uint32_t now);
  void updateSlotTransportDiag(int index, uint32_t now);
  bool handleWiFiConnection(unsigned long);
  void maintainSlotConnection(int, unsigned long, unsigned long, bool, bool&, bool&);
};
'''


class MqttHandshakeSchedulerTest(unittest.TestCase):
    def fixture(self):
        bridge = "src/helpers/bridges/MQTTBridge.cpp"
        return PREAMBLE + "\n".join(method(bridge, signature) for signature in (
            "int MQTTBridge::activatedSlotCount() const",
            "bool MQTTBridge::canActivateSlot(int index) const",
            "bool MQTTBridge::hasPendingSlotConnection() const",
            "bool MQTTBridge::canStartSlotConnection(int index) const",
            "esp_err_t MQTTBridge::reconnectSlotClient(int index)",
            "bool MQTTBridge::recoverSlotTransport(",
            "void MQTTBridge::serviceSlotTransportHealth(",
            "void MQTTBridge::updateSlotTransportDiag(",
            "bool MQTTBridge::ensureSlotClient(",
            "bool MQTTBridge::publishToSlot(",
            "bool MQTTBridge::publishStatusToSlot(",
            "bool MQTTBridge::publishStatus(",
            "void MQTTBridge::maintainStatus(",
            "unsigned long MQTTBridge::slotTokenLifetime(int index) const",
            "bool MQTTBridge::handleWiFiConnection(unsigned long now)",
            "void MQTTBridge::maintainSlotConnections()",
            "void MQTTBridge::maintainSlotConnection("))

    def compile_run(self, main, fixture=None, expect_rejection=False):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a host C++ compiler is required")
        with tempfile.TemporaryDirectory(prefix="meshcore-mqtt-scheduler-") as temporary:
            work = Path(temporary)
            source, binary = work / "scheduler.cpp", work / "scheduler"
            source.write_text((self.fixture() if fixture is None else fixture) + main,
                              encoding="utf-8")
            result = subprocess.run(
                [compiler, "-std=c++17", "-Wall", "-Wextra", "-I", str(ROOT / "src"),
                 str(source), "-o", str(binary)], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], cwd=work, capture_output=True,
                                    text=True, timeout=10)
            if expect_rejection:
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("Assertion", result.stderr)
            else:
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_first_start_is_immediate_and_delayed_callback_serializes_following_slots(self):
        self.compile_run(r'''
int main() {
  MQTTBridge bridge;
  clock_ms = 1000;
  bridge.maintainSlotConnections();
  assert(bridge.setups == std::vector<int>{0});
  assert(bridge._slot_attempt_pending[0]);
  // The time guard has expired, but the earlier asynchronous handshake is
  // still pending. Ordinary maintenance must not launch another SDK request.
  clock_ms = 16000;
  bridge.maintainSlotConnections();
  clock_ms = 60000;
  bridge.maintainSlotConnections();
  assert(bridge.setups == std::vector<int>{0});
  assert(bridge.clients[0].starts == 1 && bridge.clients[0].reconnects == 0);
  bridge.complete(0);
  bridge.maintainSlotConnections();
  assert(bridge.setups == std::vector<int>({0, 1}));
  assert(bridge._slot_attempt_pending[1]);
  bridge.complete(1);
  clock_ms += 14999;
  bridge.maintainSlotConnections();
  assert(bridge.setups.size() == 2);
  ++clock_ms;
  bridge.maintainSlotConnections();
  assert(bridge.setups == std::vector<int>({0, 1, 2}));
  bridge._stop_requested = true;
  bridge.complete(2);
  clock_ms += 60000;
  bridge.maintainSlotConnections();
  assert(bridge.setups.size() == 3);
}
''')

    def test_setup_failure_waits_full_minute_even_when_client_was_allocated(self):
        self.compile_run(r'''
int main() {
  MQTTBridge bridge;
  bridge.only(0); bridge.setup_succeeds[0] = false;
  clock_ms = 1000;
  bridge.maintainSlotConnections();
  assert(bridge.setups.size() == 1 && bridge._slots[0].client);
  assert(!bridge._slots[0].initial_connect_done && bridge._slots[0].enabled);
  assert(!bridge.hasPendingSlotConnection());
  clock_ms = 1050;
  bridge.maintainSlotConnections();
  clock_ms = 60999;
  bridge.maintainSlotConnections();
  assert(bridge.setups.size() == 1);
  clock_ms = 61000; bridge.setup_succeeds[0] = true;
  bridge.maintainSlotConnections();
  assert(bridge.setups == std::vector<int>({0, 0}));
  assert(bridge._slots[0].initial_connect_done && bridge.hasPendingSlotConnection());
}
''')

    def test_failed_slot_does_not_starve_healthy_slot_or_consume_active_cap(self):
        self.compile_run(r'''
int main() {
  MQTTBridge bridge;
  bridge._max_active_slots = 1; bridge.setup_succeeds[0] = false;
  clock_ms = 1000;
  bridge.maintainSlotConnections();
  assert(bridge.setups == std::vector<int>{0});
  assert(bridge.activatedSlotCount() == 0);
  clock_ms = 1050;
  bridge.maintainSlotConnections();
  assert(bridge.setups == std::vector<int>({0, 1}));
  assert(bridge.activatedSlotCount() == 1);
  for (int i = 2; i < RUNTIME_MQTT_SLOTS; ++i) assert(!bridge._slots[i].enabled);
  bridge.complete(1); clock_ms += 60000;
  bridge.maintainSlotConnections();
  assert(!bridge._slots[0].enabled && bridge.setups.size() == 2);
}
''')

    def test_low_dma_memory_defers_setup_without_consuming_retry_or_capacity(self):
        self.compile_run(r'''
int main() {
  MQTTBridge bridge;
  bridge.only(0); clock_ms = 1000;
  const size_t cold_free = Admission::kDmaReserveBytes + Admission::kColdStartAllowanceBytes;
  const size_t cold_largest = std::max(Admission::kPsramTlsLargestBytes,
      std::max(Admission::publishLargestBytes(true), Admission::kColdStartLargestBytes));
  dma_free = cold_free - 1;
  bridge.maintainSlotConnections();
  assert(bridge.setups.empty() && bridge._slots[0].last_reconnect_attempt == 0);
  assert(bridge._slots[0].enabled && bridge.activatedSlotCount() == 0);
  dma_free = cold_free; dma_largest = cold_largest - 1;
  bridge.maintainSlotConnections();
  assert(bridge.setups.empty() && bridge._slots[0].last_reconnect_attempt == 0);
  assert(!bridge.hasPendingSlotConnection() && bridge.activatedSlotCount() == 0);
  dma_largest = cold_largest;
  bridge.maintainSlotConnections();
  assert(bridge.setups == std::vector<int>{0} && bridge.activatedSlotCount() == 1);
  assert(bridge.clients[0].starts == 1 && bridge.clients[0].reconnects == 0);
}
''')

    def test_stopped_client_defers_while_retained_task_reconnects_at_warm_budget(self):
        self.compile_run(r'''
int main() {
  MQTTBridge bridge;
  for (int i = 2; i < RUNTIME_MQTT_SLOTS; ++i) bridge._slots[i].enabled = false;
  clock_ms = 70000;
  const size_t floor = std::max(Admission::publishLargestBytes(true), Admission::kPsramTlsLargestBytes);
  dma_free = Admission::kDmaReserveBytes; dma_largest = floor;
  auto& stopped = bridge._slots[0];
  auto& retained = bridge._slots[1];
  stopped.client = &bridge.clients[0];
  retained.client = &bridge.clients[1];
  stopped.initial_connect_done = retained.initial_connect_done = true;
  stopped.client->initialized = retained.client->initialized = true;
  retained.client->started = true;

  // An existing handle with a stopped task needs the task-restart allowance.
  // Its deferral must leave this cycle available for the retained SDK task.
  assert(!bridge.canStartSlotConnection(0));
  assert(bridge.canStartSlotConnection(1));
  bridge.maintainSlotConnections();
  assert(stopped.client->starts == 0 && stopped.client->reconnects == 0);
  assert(stopped.last_reconnect_attempt == 0 && stopped.reconnect_backoff == 0);
  assert(stopped.start_failures == 0 && !bridge._slot_attempt_pending[0]);
  assert(retained.client->starts == 0 && retained.client->reconnects == 1);
  assert(bridge._slot_attempt_pending[1]);

  // Completion and newly available task-restart headroom allow an SDK start,
  // with no extra delay introduced by the earlier memory-only deferral.
  bridge.complete(1);
  clock_ms += 15000;
  dma_free = Admission::kDmaReserveBytes + Admission::kRestartAllowanceBytes;
  dma_largest = floor + Admission::kRestartAllowanceBytes - 1;
  assert(!bridge.canStartSlotConnection(0));
  ++dma_largest;
  bridge.maintainSlotConnections();
  assert(stopped.client->starts == 1 && stopped.client->reconnects == 0);
  assert(stopped.client->isStarted() && bridge._slot_attempt_pending[0]);
  assert(stopped.last_reconnect_attempt == clock_ms);
  assert(retained.client->reconnects == 1 && bridge.setups.empty());
}
''')

    def test_internal_tls_renewal_releases_old_session_before_new_memory_admission(self):
        self.compile_run(r'''
int main() {
  MQTTBridge bridge;
  bridge.only(0); clock_ms = 70000; ESP.size = 0;
  const Preset jwt{"wss://broker/mqtt", MQTT_AUTH_JWT, 3300, true};
  auto& slot = bridge._slots[0];
  slot.preset = &jwt; slot.client = &bridge.clients[0];
  slot.initial_connect_done = slot.connected = true;
  slot.client->initialized = slot.client->started = slot.client->live = true;
  slot.token_expires_at = wall_seconds + 120;
  // Before releasing the current TLS session, the full admission check fails.
  dma_free = 20000; dma_largest = 10000;
  assert(!bridge.canStartSlotConnection(0));
  bridge.maintainSlotConnections();
  assert(bridge.mints == 1 && slot.client->bounces == 1);
  assert(slot.client->reconnects == 1 && bridge._slot_attempt_pending[0]);
  assert(slot.client->credentials == 1 && dma_free == 64000);

  // Another pending slot still prevents renewal from tearing down a live link.
  bridge.complete(0); bridge._slot_attempt_pending[1] = true;
  slot.token_expires_at = wall_seconds + 120; clock_ms += 60000;
  bridge.maintainSlotConnections();
  assert(bridge.mints == 1 && slot.client->bounces == 1);
}
''')

    def test_wifi_loss_restarts_three_retained_clients_without_cold_buffer_double_charge(self):
        self.compile_run(r'''
int main() {
  MQTTBridge bridge;
  clock_ms = 70000;
  // The live V4's three-broker measurement left about 22.7 KiB DMA heap.
  // The SDK seam uses the pinned 6144-byte stack and 344-byte TCB, plus a
  // simulated 48-byte allocator allowance. Transport/config buffers remain
  // owned by initialized handles after stop; SDK heap calls are not executed.
  dma_free = 22768; dma_largest = 22516;
  for (int i = 0; i < 3; ++i) {
    auto& slot = bridge._slots[i];
    slot.client = &bridge.clients[i];
    slot.initial_connect_done = slot.connected = true;
    slot.client->initialized = slot.client->started = slot.client->live = true;
    slot.client->model_task_heap = true;
    slot.client->disconnected = [&bridge, i] { bridge._slots[i].connected = false; };
  }
  bridge._slots[5].enabled = false;
  WiFi.state = WL_DISCONNECTED;
  assert(!bridge.handleWiFiConnection(clock_ms));
  assert(bridge.wifiOutage().down && dma_free == 42376);
  for (int i = 0; i < 3; ++i) {
    assert(bridge.clients[i].isInitialized() && !bridge.clients[i].isStarted());
    assert(bridge.clients[i].stops == 1 && !bridge._slots[i].connected);
    assert(!bridge._slot_attempt_pending[i]);
  }
  bridge.maintainSlotConnections();
  for (int i = 0; i < 3; ++i) assert(bridge.clients[i].starts == 0);

  clock_ms += 11000;
  WiFi.state = WL_CONNECTED;
  assert(bridge.handleWiFiConnection(clock_ms));
  assert(!bridge.wifiOutage().down);
  // Restart admission still fails safely if either aggregate or contiguous
  // headroom is inadequate. Neither failure consumes retry/backoff state.
  const size_t restart_free = Admission::kDmaReserveBytes + Admission::kRestartAllowanceBytes;
  const size_t floor = std::max(Admission::publishLargestBytes(true), Admission::kPsramTlsLargestBytes);
  const size_t bootstrap_largest = std::max(floor, Admission::kRestartLargestBytes);
  dma_free = restart_free - 1;
  bridge.maintainSlotConnections();
  dma_free = restart_free; dma_largest = bootstrap_largest - 1;
  bridge.maintainSlotConnections();
  for (int i = 0; i < 3; ++i) {
    assert(bridge.clients[i].starts == 0 && bridge._slots[i].last_reconnect_attempt == 0);
    assert(bridge._slots[i].reconnect_backoff == 0 && bridge._slots[i].start_failures == 0);
  }

  dma_free = 42376; dma_largest = floor + Admission::kRestartAllowanceBytes;
  bridge.maintainSlotConnections();
  assert(bridge.clients[0].starts == 1 && dma_free == 35840);
  assert(bridge.hasPendingSlotConnection());
  clock_ms += 60000;
  bridge.maintainSlotConnections();
  assert(bridge.clients[1].starts == 0 && bridge.clients[2].starts == 0);
  bridge.complete(0);
  bridge.maintainSlotConnections();
  assert(bridge.clients[1].starts == 1 && dma_free == 29304);
  clock_ms += 15000;
  bridge.maintainSlotConnections();
  assert(bridge.clients[2].starts == 0);  // Callback, not time, ends a pending attempt.
  bridge.complete(1);
  assert(!MQTTConnectionAdmission::canStart(
      true, true, MQTTConnectionAdmission::ClientMemoryState::Uninitialized,
      dma_free, dma_largest));
  assert(bridge.canStartSlotConnection(2));
  bridge.maintainSlotConnections();
  assert(bridge.clients[2].starts == 1 && dma_free == 22768);
  bridge.complete(2);
  clock_ms += 15000;
  bridge.maintainSlotConnections();
  for (int i = 0; i < 3; ++i) {
    assert(bridge._slots[i].connected && bridge.clients[i].isStarted());
    assert(bridge.clients[i].starts == 1 && bridge.clients[i].reconnects == 0);
    assert(bridge._slots[i].start_failures == 0);
  }
  // Additional clients still need the full first-start allowance and wait.
  assert(bridge.setups.empty());
  assert(!bridge.canStartSlotConnection(3) && !bridge.canStartSlotConnection(4));
  assert(!bridge.hasPendingSlotConnection());
}
''')

    def test_missing_handshake_callback_times_out_only_after_sdk_stop(self):
        self.compile_run(r'''
int main() {
  MQTTBridge bridge;
  bridge.only(0);
  clock_ms = 1000;
  bridge.maintainSlotConnections();
  assert(bridge.clients[0].starts == 1 && bridge._slot_attempt_pending[0]);
  clock_ms = 120999;
  bridge.maintainSlotConnections();
  assert(bridge.clients[0].stops == 0 && bridge._slot_attempt_pending[0]);
  clock_ms = 121000;
  bridge.maintainSlotConnections();
  auto& slot = bridge._slots[0];
  assert(bridge.clients[0].stops == 1 && !bridge.clients[0].isStarted());
  assert(!bridge._slot_attempt_pending[0] && !bridge.hasPendingSlotConnection());
  assert(slot.transport_health.recoveries == 1 && slot.connect_failures == 1);
  assert(slot.transport_health.last_recovery_reason ==
      MQTTTransportHealth::RecoveryReason::HandshakeTimeout);
  assert(slot.last_reconnect_attempt == clock_ms);
  // Cleanup doesn't start another SDK task in the same maintenance pass.
  assert(bridge.clients[0].starts == 1);
  clock_ms += 14999;
  bridge.maintainSlotConnections();
  assert(bridge.clients[0].starts == 1);
  ++clock_ms;
  bridge.maintainSlotConnections();
  assert(bridge.clients[0].starts == 2 && bridge._slot_attempt_pending[0]);
  assert(slot.reconnect_backoff == 1);
}
''')

    def test_error_callback_without_disconnect_defers_cleanup_and_preserves_reservation(self):
        self.compile_run(r'''
int main() {
  for (bool established : {false, true}) {
    MQTTBridge bridge;
    bridge.only(0);
    clock_ms = 1000;
    assert(bridge.ensureSlotClient(0));
    auto& slot = bridge._slots[0];
    slot.initial_connect_done = true;
    auto* client = slot.client;
    assert(bridge.reconnectSlotClient(0) == ESP_OK);
    if (established) client->connectEvent();
    client->errorWithoutDisconnect();
    // The actual production callback latches work. It cannot stop its own
    // esp-mqtt task or release a still-running handshake from that callback.
    assert(client->stops == 0 && client->isStarted());
    assert(bridge._slot_transport_fault_pending[0]);
    assert(bridge.hasPendingSlotConnection() && !bridge.canStartSlotConnection(1));
    client->stop_succeeds = false;
    bridge.maintainSlotConnections();
    assert(client->stops == 1 && client->isStarted());
    assert(bridge._slot_transport_fault_pending[0]);
    assert(bridge._slot_attempt_pending[0] == !established);
    assert(bridge.hasPendingSlotConnection() && !bridge.canStartSlotConnection(1));
    assert(slot.transport_health.recoveries == 0 && client->starts == 1);
    // A later successful stop proves ownership ended, then recovery may admit
    // another handshake. Failure accounting remains exactly once.
    client->stop_succeeds = true;
    bridge.maintainSlotConnections();
    assert(client->stops == 2 && !client->isStarted());
    assert(!bridge.hasPendingSlotConnection() && !bridge._slot_transport_fault_pending[0]);
    assert(slot.transport_health.recoveries == 1);
    assert(slot.connect_failures == (established ? 0u : 1u));
    assert(slot.transport_health.last_recovery_reason ==
        MQTTTransportHealth::RecoveryReason::TransportError);
    assert(client->starts == 1); // completed cleanup starts the 15-second gap
    delete client;
    slot.client = nullptr;
  }
}
''')

    def test_failed_stop_after_lost_callback_blocks_every_new_handshake(self):
        self.compile_run(r'''
int main() {
  MQTTBridge bridge;
  clock_ms = 1000;
  bridge.maintainSlotConnections();
  bridge.clients[0].stop_succeeds = false;
  clock_ms += MQTTTransportHealth::kAttemptTimeoutMs;
  for (int pass = 0; pass < 3; ++pass) {
    bridge.maintainSlotConnections();
    assert(bridge.clients[0].stops == pass + 1 && bridge.clients[0].isStarted());
    assert(bridge._slot_attempt_pending[0] && bridge.hasPendingSlotConnection());
    assert(bridge._slots[0].transport_health.recoveries == 0);
    assert(bridge.setups == std::vector<int>{0});
    for (int i = 0; i < RUNTIME_MQTT_SLOTS; ++i)
      assert(!bridge.canStartSlotConnection(i));
    clock_ms += 15000;
  }
  bridge.clients[0].stop_succeeds = true;
  bridge.maintainSlotConnections();
  assert(!bridge.clients[0].isStarted() && !bridge.hasPendingSlotConnection());
  assert(bridge._slots[0].transport_health.recoveries == 1);
  assert(bridge.setups == std::vector<int>{0});
}
''')

    def test_three_brokers_run_beyond_three_hours_and_only_missing_ack_recovers(self):
        self.compile_run(r'''
int main() {
  MQTTBridge bridge;
  clock_ms = 1000;
  for (int i = 0; i < RUNTIME_MQTT_SLOTS; ++i) {
    bridge._slots[i].enabled = i < 3;
    if (i >= 3) continue;
    auto& slot = bridge._slots[i];
    slot.client = &bridge.clients[i];
    slot.initial_connect_done = true;
    slot.client->initialized = slot.client->started = true;
    bridge.complete(i);
    slot.client->immediate_ack = true;
  }
  // Two status publishers get real ACK-counter progress; the third is quiet
  // (for example, its broker does not accept status topics). Neither condition
  // is a transport stall, irrespective of connection age or packet activity.
  for (int minute = 0; minute <= 180; ++minute) {
    assert(bridge.publishToSlot(0, "status", "{}", 2, true, 1));
    assert(bridge.publishToSlot(1, "status", "{}", 2, true, 1));
    bridge.maintainSlotConnections();
    for (int i = 0; i < 3; ++i) assert(bridge.clients[i].stops == 0);
    clock_ms += 60000;
    wall_seconds += 60;
  }
  assert(clock_ms > 3 * 60 * 60 * 1000);
  bridge.clients[0].immediate_ack = false;
  const uint32_t stalled_at = clock_ms;
  assert(bridge.publishToSlot(0, "status", "{}", 2, true, 1));
  for (uint32_t elapsed = 0; elapsed < 120000; elapsed += 30000) {
    clock_ms = stalled_at + elapsed;
    assert(bridge.publishToSlot(0, "status", "{}", 2, true, 1));
    assert(bridge.publishToSlot(1, "status", "{}", 2, true, 1));
    bridge.maintainSlotConnections();
    assert(bridge.clients[0].stops == 0);
    assert(bridge._slots[0].transport_health.ack_started_ms == stalled_at);
  }
  clock_ms = stalled_at + 120000;
  bridge.maintainSlotConnections();
  assert(bridge.clients[0].stops == 1 && !bridge._slots[0].connected);
  assert(bridge._slots[0].transport_health.recoveries == 1);
  assert(bridge._slots[0].transport_health.last_recovery_reason ==
      MQTTTransportHealth::RecoveryReason::AckTimeout);
  for (int i = 1; i < 3; ++i) {
    assert(bridge.clients[i].stops == 0 && bridge._slots[i].connected);
    assert(bridge._slots[i].transport_health.recoveries == 0);
  }
  assert(bridge._slot_health_recoveries[0] == 1);
  assert(!bridge._slot_health_recovering[0]);
}
''')

    def test_on_connect_status_watch_survives_maintenance_and_ack_clears_it(self):
        self.compile_run(r'''
int main() {
  MQTTBridge bridge;
  bridge.only(0); clock_ms = 1000;
  assert(bridge.ensureSlotClient(0));
  auto& slot = bridge._slots[0];
  slot.initial_connect_done = true;
  assert(bridge.reconnectSlotClient(0) == ESP_OK);
  slot.client->connectEvent();
  assert(bridge._slot_health_reset_pending[0] && bridge._status_publish_pending[0]);
  // The real worker consumes an on-connect status before it calls maintenance.
  bridge.publishStatusToSlot(0);
  assert(slot.client->publishes == 1);
  assert(!bridge._slot_health_reset_pending[0] && slot.transport_health.ack_pending);
  bridge.maintainSlotConnections();
  assert(slot.transport_health.ack_pending);
  clock_ms += 119999;
  ++slot.client->acknowledgments;
  bridge.maintainSlotConnections();
  assert(!slot.transport_health.ack_pending && slot.client->stops == 0);
  assert(bridge._slot_health_ack_wait_secs[0] == UINT32_MAX);
  slot.client->immediate_ack = true;
  assert(bridge.publishToSlot(0, "status", "{}", 2, true, 1));
  assert(!slot.transport_health.ack_pending); // ACK arrived inside enqueue
  clock_ms += 3600000;
  bridge.maintainSlotConnections();
  assert(slot.client->stops == 0);
  delete slot.client;
  slot.client = nullptr;
}
''')

    def test_negative_qos0_write_without_disconnect_requests_owner_recovery(self):
        self.compile_run(r'''
int main() {
  MQTTBridge bridge;
  bridge.only(0); clock_ms = 1000;
  auto& slot = bridge._slots[0];
  slot.client = &bridge.clients[0];
  slot.client->initialized = slot.client->started = true;
  slot.initial_connect_done = true;
  bridge.complete(0);
  slot.client->publish_result = -1;
  assert(!bridge.publishToSlot(0, "packets", "{}", 2, false, 0));
  assert(slot.client->stops == 0 && slot.connected && slot.client->connected());
  assert(slot.transport_health.recovery_reason ==
      MQTTTransportHealth::RecoveryReason::PublishFailure);
  assert(bridge.hasPendingSlotConnection());
  assert(!bridge.publishToSlot(0, "packets", "{}", 2, false, 0));
  assert(slot.client->publishes == 1); // don't repeatedly hit the broken socket
  bridge.maintainSlotConnections();
  assert(slot.client->stops == 1 && !slot.connected && !slot.client->isStarted());
  assert(slot.transport_health.recoveries == 1);
  assert(slot.transport_health.last_recovery_reason ==
      MQTTTransportHealth::RecoveryReason::PublishFailure);
}
''')

    def test_handshake_and_ack_recovery_are_millis_wrap_safe(self):
        self.compile_run(r'''
int main() {
  for (bool awaiting_ack : {false, true}) {
    MQTTBridge bridge;
    bridge.only(0); clock_ms = UINT32_MAX - 60000;
    bridge.maintainSlotConnections();
    auto& slot = bridge._slots[0];
    if (awaiting_ack) {
      bridge.complete(0);
      assert(bridge.publishToSlot(0, "status", "{}", 2, true, 1));
    }
    clock_ms += 119999;
    bridge.maintainSlotConnections();
    assert(slot.client->stops == 0);
    ++clock_ms;
    bridge.maintainSlotConnections();
    assert(slot.client->stops == 1 && slot.transport_health.recoveries == 1);
    assert(slot.transport_health.last_recovery_reason == (awaiting_ack
        ? MQTTTransportHealth::RecoveryReason::AckTimeout
        : MQTTTransportHealth::RecoveryReason::HandshakeTimeout));
  }
}
''')

    def test_recovery_preserves_backoff_and_restart_memory_admission(self):
        self.compile_run(r'''
int main() {
  MQTTBridge bridge;
  bridge.only(0); clock_ms = 70000;
  auto& slot = bridge._slots[0];
  slot.client = &bridge.clients[0];
  slot.client->initialized = slot.client->started = true;
  slot.initial_connect_done = true;
  bridge.complete(0);
  slot.reconnect_backoff = 3;
  slot.max_backoff_failures = 1;
  bridge._slot_transport_fault_pending[0] = true;
  bridge.maintainSlotConnections();
  assert(slot.client->stops == 1 && slot.reconnect_backoff == 3);
  assert(slot.max_backoff_failures == 1 && slot.connect_failures == 0);
  clock_ms += 120000;
  const size_t restart_free = Admission::kDmaReserveBytes + Admission::kRestartAllowanceBytes;
  const size_t restart_largest = std::max(Admission::kPsramTlsLargestBytes,
      std::max(Admission::publishLargestBytes(true), Admission::kRestartLargestBytes));
  dma_free = restart_free - 1; dma_largest = 20000;
  bridge.maintainSlotConnections();
  assert(slot.client->starts == 0 && slot.reconnect_backoff == 3);
  assert(slot.last_reconnect_attempt == 70000 && slot.start_failures == 0);
  dma_free = restart_free; dma_largest = restart_largest - 1;
  bridge.maintainSlotConnections();
  assert(slot.client->starts == 0 && slot.reconnect_backoff == 3);
  dma_largest = restart_largest;
  bridge.maintainSlotConnections();
  assert(slot.client->starts == 1 && bridge._slot_attempt_pending[0]);
  assert(slot.reconnect_backoff == 4 && slot.max_backoff_failures == 1);
}
''')

    def test_late_connected_callback_cannot_release_failed_stop_reservation(self):
        self.compile_run(r'''
int main() {
  for (bool publish_before_maintenance : {false, true}) {
    MQTTBridge bridge;
    bridge.only(0); clock_ms = 1000;
    assert(bridge.ensureSlotClient(0));
    auto& slot = bridge._slots[0];
    slot.initial_connect_done = true;
    auto* client = slot.client;
    assert(bridge.reconnectSlotClient(0) == ESP_OK);
    client->connectEvent();
    client->errorWithoutDisconnect();
    client->stop_succeeds = false;
    bridge.maintainSlotConnections();
    assert(client->isStarted() && client->stops == 1);
    assert(slot.transport_health.recovery_reason != MQTTTransportHealth::RecoveryReason::None);
    // An in-flight SDK event arrives after stop failed. CONNACK may clear the
    // attempt bit and set a new-session latch, but it cannot prove task join.
    client->connectEvent();
    assert(!bridge._slot_attempt_pending[0] && bridge._slot_health_reset_pending[0]);
    assert(bridge.hasPendingSlotConnection() && !bridge.canStartSlotConnection(1));
    if (publish_before_maintenance) {
      assert(!bridge.publishToSlot(0, "status", "{}", 2, true, 1));
      assert(client->publishes == 0 && bridge.hasPendingSlotConnection());
    }
    bridge.maintainSlotConnections();
    assert(client->stops == 2 && client->isStarted());
    assert(bridge.hasPendingSlotConnection() && !bridge.canStartSlotConnection(1));
    assert(client->starts == 1 && bridge.setups.empty());
    assert(slot.transport_health.recoveries == 0);
    client->stop_succeeds = true;
    bridge.maintainSlotConnections();
    assert(!client->isStarted() && !bridge.hasPendingSlotConnection());
    assert(slot.transport_health.recoveries == 1 && client->starts == 1);
    delete client;
    slot.client = nullptr;
  }
}
''')

    def test_wifi_loss_failed_stop_and_reconnect_cannot_release_sdk_reservation(self):
        self.compile_run(r'''
int main() {
  for (bool established : {false, true}) {
    MQTTBridge bridge;
    bridge.only(0); clock_ms = 70000; WiFi.state = WL_CONNECTED;
    assert(bridge.ensureSlotClient(0));
    auto& slot = bridge._slots[0];
    slot.initial_connect_done = true;
    auto* client = slot.client;
    assert(bridge.reconnectSlotClient(0) == ESP_OK);
    if (established) client->connectEvent();
    client->stop_succeeds = false;
    WiFi.state = WL_DISCONNECTED;
    assert(!bridge.handleWiFiConnection(clock_ms));
    assert(client->isStarted() && client->stops == 1 && !slot.connected);
    assert(bridge._slot_transport_fault_pending[0] && bridge.hasPendingSlotConnection());
    assert(!bridge.canStartSlotConnection(1));
    clock_ms += 11000;
    WiFi.state = WL_CONNECTED;
    assert(bridge.handleWiFiConnection(clock_ms));
    client->connectEvent(); // late SDK callback after WiFi recovered
    assert(bridge.hasPendingSlotConnection());
    bridge.maintainSlotConnections();
    assert(client->isStarted() && client->stops == 2 && !slot.connected);
    assert(bridge.hasPendingSlotConnection() && !bridge.canStartSlotConnection(1));
    assert(client->starts == 1 && bridge.setups.empty());
    client->stop_succeeds = true;
    bridge.maintainSlotConnections();
    assert(!client->isStarted() && !bridge.hasPendingSlotConnection());
    assert(slot.transport_health.recoveries == 1 && client->starts == 1);
    delete client;
    slot.client = nullptr;
  }
}
''')

    def test_pre_fix_error_callback_without_owner_signal_fails_regression(self):
        source = self.fixture()
        old_callback = source.replace(
            "_slot_transport_fault_pending[index].store(true, std::memory_order_release);",
            "/* pre-fix onError had no owner-task recovery signal */", 1)
        self.assertNotEqual(source, old_callback)
        self.compile_run(r'''
int main() {
  MQTTBridge bridge;
  bridge.only(0); clock_ms = 1000;
  assert(bridge.ensureSlotClient(0));
  auto* client = bridge._slots[0].client;
  assert(bridge.reconnectSlotClient(0) == ESP_OK);
  client->errorWithoutDisconnect();
  assert(bridge._slot_transport_fault_pending[0]);
}
''', fixture=old_callback, expect_rejection=True)


if __name__ == "__main__":
    unittest.main()
