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
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <functional>
#include <vector>
#include "helpers/AlertFaultPolicy.h"
#include "helpers/MQTTConnectionAdmission.h"
#include "helpers/MQTTConnectionHealth.h"
#include "helpers/MQTTConnectionPolicy.h"
#include "helpers/WiFiPowerSave.h"
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
constexpr uint32_t MALLOC_CAP_INTERNAL = 1, MALLOC_CAP_DMA = 2;
static uint32_t clock_ms = 1000;
static unsigned long wall_seconds = 1800000000;
static size_t dma_free = 100000, dma_largest = 20000;
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
  assert(caps == (MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA)); return dma_free;
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
};
bool mqttPresetEnforcesTokenExp(const Preset* preset) {
  return preset && preset->enforce_exp;
}
struct PsychicMqttClient {
  bool initialized = false, started = false, live = false, model_task_heap = false;
  int starts = 0, reconnects = 0, bounces = 0, credentials = 0, stops = 0;
  std::function<void()> disconnected;
  bool isInitialized() const { return initialized; }
  bool isStarted() const { return started; }
  bool connected() const { return live; }
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
    if (model_task_heap && started) dma_free += 6536;
    started = live = false;
    if (disconnected) disconnected();
  }
  void softDisconnect() {
    ++bounces; live = false;
    // Releasing the old internal TLS session creates room for its replacement.
    dma_free += 44000; dma_largest = 20000;
  }
  void setCredentials(const char*, const char*) { ++credentials; }
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
  } _slots[RUNTIME_MQTT_SLOTS];
  PsychicMqttClient clients[RUNTIME_MQTT_SLOTS];
  char tokens[RUNTIME_MQTT_SLOTS][16] = {};
  bool setup_succeeds[RUNTIME_MQTT_SLOTS] = {true, true, true, true, true, true};
  bool ready[RUNTIME_MQTT_SLOTS] = {true, true, true, true, true, true};
  std::atomic<bool> _slot_attempt_pending[RUNTIME_MQTT_SLOTS] = {};
  std::atomic<bool> _stop_requested{false}, _ntp_synced{true};
  bool _slot_force_jwt_mint[RUNTIME_MQTT_SLOTS] = {};
  void* _identity = this;
  bool _slots_setup_done = true;
  bool _wifi_status_initialized = true, _manage_wifi = false;
  wl_status_t _last_wifi_status = WL_CONNECTED;
  unsigned long _last_wifi_check = 0, _last_wifi_reconnect_attempt = 0;
  uint8_t _wifi_reconnect_backoff_attempt = 0, _wifi_power_save = 1;
  struct { bool canonical_wifi = false; } _node_info;
  struct Observer { uint8_t wifi_power_save = 1; } observer;
  Observer* _obs = &observer;
  AlertFaultPolicy::OutageSnapshot _wifi_outage{};
  unsigned long _last_slot_reconnect_ms = 0;
  int _max_active_slots = 5, mints = 0;
  AlertFaultPolicy::OutageSnapshot wifiOutage() const { return _wifi_outage; }
  void setWifiOutage(AlertFaultPolicy::OutageSnapshot snapshot) { _wifi_outage = snapshot; }
  void beginWiFiStation() {}
  const char* _jwt_username = "jwt";
  static const unsigned long SLOT_SETUP_RETRY_INTERVAL = 60000;
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
  }
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
            "unsigned long MQTTBridge::slotTokenLifetime(int index) const",
            "bool MQTTBridge::handleWiFiConnection(unsigned long now)",
            "void MQTTBridge::maintainSlotConnections()",
            "void MQTTBridge::maintainSlotConnection("))

    def compile_run(self, main):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a host C++ compiler is required")
        with tempfile.TemporaryDirectory(prefix="meshcore-mqtt-scheduler-") as temporary:
            work = Path(temporary)
            source, binary = work / "scheduler.cpp", work / "scheduler"
            source.write_text(self.fixture() + main, encoding="utf-8")
            result = subprocess.run(
                [compiler, "-std=c++17", "-Wall", "-Wextra", "-I", str(ROOT / "src"),
                 str(source), "-o", str(binary)], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], cwd=work, capture_output=True,
                                    text=True, timeout=10)
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
  clock_ms = 3600000;
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
  bridge.only(0); clock_ms = 1000; dma_free = 32767;
  bridge.maintainSlotConnections();
  assert(bridge.setups.empty() && bridge._slots[0].last_reconnect_attempt == 0);
  assert(bridge._slots[0].enabled && bridge.activatedSlotCount() == 0);
  dma_free = 32768; dma_largest = 8191;
  bridge.maintainSlotConnections();
  assert(bridge.setups.empty() && bridge._slots[0].last_reconnect_attempt == 0);
  assert(!bridge.hasPendingSlotConnection() && bridge.activatedSlotCount() == 0);
  dma_largest = 8192;
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
  dma_free = 16384; dma_largest = 4096;
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
  dma_free = 24576; dma_largest = 8192;
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
  dma_free = 24575;
  bridge.maintainSlotConnections();
  dma_free = 24576; dma_largest = 8191;
  bridge.maintainSlotConnections();
  for (int i = 0; i < 3; ++i) {
    assert(bridge.clients[i].starts == 0 && bridge._slots[i].last_reconnect_attempt == 0);
    assert(bridge._slots[i].reconnect_backoff == 0 && bridge._slots[i].start_failures == 0);
  }

  dma_free = 42376; dma_largest = 22516;
  bridge.maintainSlotConnections();
  assert(bridge.clients[0].starts == 1 && dma_free == 35840);
  assert(bridge.hasPendingSlotConnection());
  clock_ms += 3600000;
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


if __name__ == "__main__":
    unittest.main()
