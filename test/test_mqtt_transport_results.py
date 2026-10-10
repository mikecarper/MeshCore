#!/usr/bin/env python3
"""Compile the real adapter/bridge attempt methods against controllable SDK calls.

No PlatformIO/network/hardware is used. IDF4 and IDF5 config layouts are covered.
"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def method(path, signature):
    source = (ROOT / path).read_text(encoding="utf-8")
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 1
    pos = opening + 1
    while depth:
        depth += (source[pos] == "{") - (source[pos] == "}")
        pos += 1
    return source[start:pos] + "\n"


PREAMBLE = r'''
#include <atomic>
#include <cstdlib>
#include <cstdint>
#include <cassert>
#include <functional>
#include "helpers/MQTTConnectionHealth.h"
using esp_err_t = int;
using esp_mqtt_client_handle_t = void*;
constexpr int ESP_OK=0, ESP_FAIL=-1, ESP_ERR_INVALID_STATE=2, ESP_ERR_NO_MEM=3;
constexpr int ESP_ERR_INVALID_ARG=4, MQTT_EVENT_ANY=0, RUNTIME_MQTT_SLOTS=2;
#define ESP_LOGE(...) ((void)0)
#define ESP_LOGW(...) ((void)0)
#define ESP_LOGI(...) ((void)0)
#define ESP_ERROR_CHECK_WITHOUT_ABORT(...) ((void)0)
#define MQTT_DEBUG_PRINTLN(...) ((void)0)
const char* esp_err_to_name(int) { return "mock"; }
struct Config {
  struct { struct { const char* uri=nullptr; } address; } broker;
  struct { int size=1024; } buffer;
  const char* uri=nullptr;
  int buffer_size=1024;
};
static bool fail_malloc=false, fail_init=false;
static int register_result=ESP_OK, config_result=ESP_OK;
static int start_result=ESP_OK, reconnect_result=ESP_OK;
static int init_calls=0, register_calls=0, destroy_calls=0, config_calls=0;
static int start_calls=0, reconnect_calls=0;
static std::function<void()> sdk_callback;
void* test_malloc(size_t size) { return fail_malloc ? nullptr : std::malloc(size); }
#define malloc test_malloc
void* esp_mqtt_client_init(Config*) {
  ++init_calls; return fail_init ? nullptr : reinterpret_cast<void*>(1);
}
int esp_mqtt_client_register_event(void*, int, void(*)(void*,int,int,void*), void*) {
  ++register_calls; return register_result;
}
int esp_mqtt_client_destroy(void*) { ++destroy_calls; return ESP_OK; }
int esp_mqtt_set_config(void*, Config*) { ++config_calls; return config_result; }
int esp_mqtt_client_start(void*) {
  ++start_calls; if(sdk_callback) sdk_callback(); return start_result;
}
int esp_mqtt_client_reconnect(void*) {
  ++reconnect_calls; if(sdk_callback) sdk_callback(); return reconnect_result;
}
struct PsychicMqttClient {
  Config _mqtt_cfg;
  void* _client=nullptr;
  bool _config_dirty=true, _started=false;
  char* _buffer=nullptr;
  size_t _buffer_capacity=0;
  ~PsychicMqttClient() { std::free(_buffer); }
  static void _onMqttEventStatic(void*,int,int,void*) {}
  esp_err_t applyConfig();
  esp_err_t connect();
  esp_err_t reconnect();
  bool isStarted() const { return _started; }
  void uri(const char* uri) { _mqtt_cfg.uri=uri; _mqtt_cfg.broker.address.uri=uri; }
};
struct MQTTBridge {
  struct MQTTSlot {
    PsychicMqttClient* client=nullptr;
    uint32_t start_failures=0;
  };
  MQTTSlot _slots[RUNTIME_MQTT_SLOTS];
  std::atomic<bool> _slot_attempt_pending[RUNTIME_MQTT_SLOTS] = {};
  std::atomic<bool> _stop_requested{false};
  bool hasPendingSlotConnection() const;
  bool canStartSlotConnection(int index) const;
  esp_err_t reconnectSlotClient(int index);
};
'''

MAIN = r'''
int main() {
  PsychicMqttClient c;
  assert(c.connect()==ESP_ERR_INVALID_STATE && start_calls==0 && init_calls==0);
  c.uri("mqtts://broker");
  std::free(c._buffer); c._buffer=nullptr;
  fail_malloc=true;
  assert(c.connect()==ESP_ERR_NO_MEM && start_calls==0 && init_calls==0);
  fail_malloc=false;
  fail_init=true;
  assert(c.connect()==ESP_ERR_NO_MEM && register_calls==0 && start_calls==0);
  fail_init=false;
  register_result=ESP_FAIL;
  assert(c.connect()==ESP_FAIL && c._client==nullptr && c._config_dirty);
  assert(destroy_calls==1 && start_calls==0);
  register_result=ESP_OK;
  start_result=ESP_FAIL;
  assert(c.connect()==ESP_FAIL && !c.isStarted());
  start_result=ESP_OK;
  assert(c.connect()==ESP_OK && c.isStarted());
  assert(register_calls==2); // failed registration then one successful registration
  c._config_dirty=true;
  config_result=ESP_FAIL;
  int starts=start_calls;
  assert(c.connect()==ESP_FAIL && start_calls==starts && c._config_dirty);
  assert(c.reconnect()==ESP_FAIL && reconnect_calls==0 && c._config_dirty);
  config_result=ESP_OK;
  reconnect_result=ESP_FAIL;
  assert(c.reconnect()==ESP_FAIL && !c._config_dirty && reconnect_calls==1);
  reconnect_result=ESP_OK;
  assert(c.reconnect()==ESP_OK && reconnect_calls==2);

  MQTTBridge b;
  assert(b.reconnectSlotClient(-1)==ESP_ERR_INVALID_ARG);
  assert(b.reconnectSlotClient(1)==ESP_ERR_INVALID_STATE);
  b._slots[0].client=&c;
  reconnect_result=ESP_FAIL;
  assert(b.reconnectSlotClient(0)==ESP_FAIL);
  assert(!b._slot_attempt_pending[0] && b._slots[0].start_failures==0);
  int attempts = reconnect_calls;
  b._slot_attempt_pending[0]=true;
  assert(b.reconnectSlotClient(0)==ESP_ERR_NO_MEM && reconnect_calls==attempts);
  assert(b._slot_attempt_pending[0] && b._slots[0].start_failures==0);
  b._slot_attempt_pending[0]=false;
  b._slot_attempt_pending[1]=true;
  assert(b.reconnectSlotClient(0)==ESP_ERR_NO_MEM && reconnect_calls==attempts);
  assert(!b._slot_attempt_pending[0] && b._slot_attempt_pending[1]);
  b._slot_attempt_pending[1]=false;
  sdk_callback=[&]() {
    assert(b._slot_attempt_pending[0]);
    b._slot_attempt_pending[0]=false;
  };
  assert(b.reconnectSlotClient(0)==ESP_FAIL && !b._slot_attempt_pending[0]);
  sdk_callback=nullptr;
  c._started=false;
  start_result=ESP_FAIL;
  assert(b.reconnectSlotClient(0)==ESP_FAIL);
  assert(!b._slot_attempt_pending[0] && b._slots[0].start_failures==1);
  start_result=ESP_OK;
  sdk_callback=[&]() {
    assert(b._slot_attempt_pending[0]); // set before synchronous SDK callback
    b._slot_attempt_pending[0]=false;   // onConnect completes the attempt
  };
  assert(b.reconnectSlotClient(0)==ESP_OK && !b._slot_attempt_pending[0]);
  sdk_callback=nullptr;
  reconnect_result=ESP_OK;
  assert(b.reconnectSlotClient(0)==ESP_OK && b._slot_attempt_pending[0]);
  const int stopped_starts = start_calls, stopped_reconnects = reconnect_calls;
  const auto stopped_failures = b._slots[0].start_failures;
  b._stop_requested = true;
  assert(b.reconnectSlotClient(-1)==ESP_ERR_INVALID_ARG);
  for (bool started : {false, true}) {
    c._started = started;
    b._slot_attempt_pending[0] = started;
    assert(b.reconnectSlotClient(0)==ESP_ERR_INVALID_STATE);
    assert(start_calls==stopped_starts && reconnect_calls==stopped_reconnects);
    assert(b._slot_attempt_pending[0]==started && b._slots[0].start_failures==stopped_failures);
  }
  b._stop_requested = false;
  return 0;
}
'''


TASK_PREAMBLE = r'''
#include <algorithm>
#include <atomic>
#include <cassert>
#include <climits>
#include <cstdint>
#include <cstdlib>
#include <string>
#include <vector>
#define PORTABLE_MQTT_OBSERVER 1
#define MQTT_DEBUG_PRINTLN(...) ((void)0)
using TickType_t = uint32_t;
constexpr int RUNTIME_MQTT_SLOTS = 3, WL_CONNECTED = 1;
constexpr uint32_t STATUS_RETRY_INTERVAL = 30000;
static uint32_t tick_ms = 1, first_tick = 0, first_millis = 0;
static uint64_t elapsed_ms = 0, stop_at_ms = UINT64_MAX;
static int scenario = 0;
static std::vector<uint32_t> task_sleeps;
struct MQTTBridge;
static MQTTBridge* active_bridge = nullptr;
#define pdMS_TO_TICKS(ms) static_cast<TickType_t>((ms) / tick_ms)
TickType_t xTaskGetTickCount() {
  return first_tick + static_cast<TickType_t>(elapsed_ms / tick_ms);
}
uint32_t millis() { return first_millis + static_cast<uint32_t>(elapsed_ms); }
void vTaskDelay(TickType_t ticks);
struct { int status() { return WL_CONNECTED; } } WiFi;
bool mqttNtpRefreshDue(unsigned long, unsigned long, unsigned long) { return false; }
struct MQTTPrefs {
  char mqtt_slot_preset[RUNTIME_MQTT_SLOTS][16] = {};
  bool mqtt_status_enabled = true;
};
struct MQTTBridge {
  struct Slot {
    bool enabled = true, initial_connect_done = false, connected = false;
    unsigned long last_reconnect_attempt = 0;
  } _slots[RUNTIME_MQTT_SLOTS];
  struct Latch {
    void noteGotIp() {}
    bool consumeIfConnected(bool) { return false; }
  } _ntp_reconnect_latch;
  MQTTPrefs prefs;
  MQTTPrefs* _obs = &prefs;
  std::atomic<bool> _ntp_synced{true};
  bool _ntp_sync_pending = false, _slots_setup_done = false;
  bool _ntp_force_requested = false, _ntp_force_result = false, _ntp_force_done = false;
  bool _ntp_refresh_pending = false, _cached_has_connected_slots = true;
  std::atomic<bool> _stop_requested{false}, _stop_acked{false};
  bool _slot_reconfigure_pending[RUNTIME_MQTT_SLOTS] = {true, true, true};
  bool _status_publish_pending[RUNTIME_MQTT_SLOTS] = {true, true, true};
  unsigned long _last_ntp_sync = 0, _ntp_refresh_retry_at = 0;
  unsigned long _last_status_retry = 0, _last_status_publish = 0, _status_interval = 300000;
  int _max_active_slots = RUNTIME_MQTT_SLOTS, _queue_count = 0;
  int late_work = 0, reconfigures = 0, publishes = 0, queue_calls = 0;
  std::vector<int> setups;
  std::vector<uint64_t> setup_times;
  std::vector<std::string> teardown;
  void work() {
    if (_stop_requested) ++late_work;
    assert(!_stop_acked);
  }
  bool initializeWiFiInTask() { return true; }
  bool handleWiFiConnection(unsigned long) { work(); return false; }
  bool syncTimeWithNTP(bool = false, bool = false) {
    work();
    if (scenario == 6 || scenario == 7) {
      _ntp_synced = true;
      _stop_requested = true;
    }
    return true;
  }
  bool canActivateSlot(int) { work(); return true; }
  bool isSlotReady(int, char*, size_t) { work(); return true; }
  bool setupSlot(int index) {
    work(); setups.push_back(index); setup_times.push_back(elapsed_ms);
    if (scenario == 5) _stop_requested = true;
    return true;
  }
  void applySlotPreset(int, const char*) {
    work(); ++reconfigures;
    if (scenario == 8) _stop_requested = true;
  }
  void publishStatusToSlot(int) {
    work(); ++publishes;
    if (scenario == 9) _stop_requested = true;
  }
  void maintainSlotConnections() {
    work();
    if (scenario == 5 || scenario == 10) _stop_requested = true;
  }
  void processPacketQueue() {
    work(); ++queue_calls;
    if (scenario == 11) _stop_requested = true;
  }
  void checkConfigurationMismatch() { work(); }
  void pollNtpRefresh(unsigned long) { work(); }
  void refreshNTP() { work(); }
  bool publishStatus() { work(); ++publishes; return true; }
  void updateCachedConnectionStatus() { work(); }
  void cancelNtpRefresh() {
    assert(_stop_requested && !_stop_acked);
    teardown.push_back("ntp");
  }
  void teardownSlot(int index) {
    assert(_stop_requested && !_stop_acked);
    teardown.push_back("slot" + std::to_string(index));
  }
  void destroySlotClients() {
    assert(!_stop_acked);
    assert(teardown == std::vector<std::string>({"ntp", "slot0", "slot1", "slot2"}));
    teardown.push_back("destroy");
  }
  bool waitUnlessStopping(uint32_t delay_ms);
  void mqttTaskLoop();
};
void vTaskDelay(TickType_t ticks) {
  assert(ticks > 0);
  const uint32_t duration = ticks * tick_ms;
  task_sleeps.push_back(duration);
  elapsed_ms += duration;
  if (elapsed_ms >= stop_at_ms) active_bridge->_stop_requested = true;
  // The no-stop scenario exits after one complete worker iteration. Startup
  // only schedules slots; connection maintenance now owns their async starts.
  if (scenario == 3 && active_bridge->queue_calls == 1)
    active_bridge->_stop_requested = true;
  assert(elapsed_ms < 30000); // bound a missing cooperative exit deterministically
}
'''

TASK_MAIN = r'''
int main(int argc, char** argv) {
  assert(argc == 4);
  scenario = std::atoi(argv[1]);
  tick_ms = static_cast<uint32_t>(std::atoi(argv[2]));
  if (std::atoi(argv[3])) {
    first_tick = UINT32_MAX - 511;
    first_millis = UINT32_MAX - 511;
  }
  MQTTBridge bridge;
  active_bridge = &bridge;
  switch (scenario) {
    case 0: stop_at_ms = 187; break;          // stop while WiFi settles
    case 1: stop_at_ms = 1173; break;         // stop during ordinary service
    case 2: stop_at_ms = 6191; break;         // stop during later ordinary service
    case 3: break;                           // preserve every normal delay
    case 4: bridge._stop_requested = true; break;
    case 5: break;                           // maintenance callback requests stop
    case 6: bridge._ntp_force_requested = true; break;
    case 7: bridge._ntp_sync_pending = true; bridge._ntp_synced = false; break;
    case 8: case 9: case 10: case 11: break;  // stop raised during worker callbacks
    default: assert(false);
  }
  bridge.mqttTaskLoop();
  assert(bridge._stop_acked && bridge.late_work == 0);
  assert(bridge.teardown == std::vector<std::string>({
    "ntp", "slot0", "slot1", "slot2", "destroy"}));
  assert(bridge.setups.empty()); // no blocking startup slot loop remains
  if (scenario == 3) {
    assert(bridge._slots_setup_done);
    assert(bridge.reconfigures == RUNTIME_MQTT_SLOTS);
    assert(bridge.publishes >= RUNTIME_MQTT_SLOTS && bridge.queue_calls == 1);
  } else {
    if (scenario >= 8 || scenario == 5) {
      assert(bridge.reconfigures == (scenario == 8 ? 1 : RUNTIME_MQTT_SLOTS));
      assert(bridge.publishes == (scenario == 8 ? 0 : scenario == 9 ? 1 : RUNTIME_MQTT_SLOTS));
      assert(bridge.queue_calls == (scenario == 11 ? 1 : 0));
    } else if (scenario != 1 && scenario != 2) {
      assert(bridge.reconfigures == 0 && bridge.publishes == 0 && bridge.queue_calls == 0);
    }
    const uint32_t poll_bound = std::max(uint32_t{50}, tick_ms);
    assert(std::all_of(task_sleeps.begin(), task_sleeps.end(),
                       [poll_bound](uint32_t duration) { return duration <= poll_bound; }));
    if (scenario == 4) assert(elapsed_ms == 0 && task_sleeps.empty());
    else if (scenario >= 5) assert(elapsed_ms == 1000);
    else assert(elapsed_ms >= stop_at_ms && elapsed_ms - stop_at_ms < poll_bound);
  }
  // A completed zero-duration wait preserves its caller's stop decision.
  const auto sleeps_before = task_sleeps.size();
  bridge._stop_requested = false;
  assert(bridge.waitUnlessStopping(0));
  bridge._stop_requested = true;
  assert(!bridge.waitUnlessStopping(0));
  assert(task_sleeps.size() == sleeps_before);
}
'''

ASYNC_STOP_PREAMBLE = r'''
#include <atomic>
#include <cassert>
#include <cstdint>
#include <limits>
#include "helpers/MQTTLifecycle.h"
#include "helpers/MQTTConnectionPolicy.h"
#define ESP_PLATFORM 1
#define MQTT_DEBUG_PRINTLN(...) ((void)0)
#define pdMS_TO_TICKS(ms) (ms)
constexpr int RUNTIME_MQTT_SLOTS = 3;
static constexpr uint32_t MQTT_STOP_TIMEOUT_BASE_MS = 5000;
static constexpr uint32_t MQTT_STOP_TIMEOUT_PER_SLOT_MS = 8000;
static uint32_t clock_ms = 0, sleeps = 0;
static uint64_t elapsed_ms = 0, ack_after_ms = UINT64_MAX;
uint32_t millis() { return clock_ms; }
struct MQTTBridge;
static MQTTBridge* s_mqtt_bridge_instance = nullptr;
static MQTTBridge* bridge_waiting = nullptr;
void vTaskDelay(uint32_t duration);
struct MQTTBridge {
  struct Slot { bool enabled = true; } _slots[RUNTIME_MQTT_SLOTS];
  struct LifecycleOps : MQTTLifecycle::Ops {
    MQTTBridge* _b;
    explicit LifecycleOps(MQTTBridge* bridge) : _b(bridge) {}
    uint32_t nowMs() override;
    void startTask() override;
    void deliverStop() override;
    void releaseResources() override;
    void onStopComplete(bool clean) override;
  };
  struct RecordingOps : LifecycleOps {
    int signals = 0;
    explicit RecordingOps(MQTTBridge* bridge) : LifecycleOps(bridge) {}
    void deliverStop() override { ++signals; LifecycleOps::deliverStop(); }
  } ops{this};
  MQTTLifecycle::Coordinator _lifecycle{ops, 1};
  std::atomic<bool> _stop_requested{false}, _stop_acked{false};
  bool _initialized = true, resources_owned = true;
  bool _slots_setup_done = true, _staged_raw_valid = true;
  bool _ntp_estimate_requested = true, _ntp_estimate_done = true, _ntp_estimate_ok = true;
  uint32_t _ntp_estimate_epoch = 123;
  int releases = 0, cancels = 0, completions = 0;
  bool clean_completion = false;
  MQTTBridge() {
    assert(_lifecycle.requestStart());
    assert(_lifecycle.onTaskStarted());
    s_mqtt_bridge_instance = this;
  }
  void cancelNtpRefresh() { ++cancels; }
  void requestStop();
  void end();
  void finishStopped();
  void loop();
};
void MQTTBridge::LifecycleOps::releaseResources() {
  assert(_b->_stop_acked.load() || _b->_lifecycle.stopTimedOut());
  assert(_b->resources_owned && _b->_initialized);
  _b->resources_owned = false;
  ++_b->releases;
}
void MQTTBridge::LifecycleOps::onStopComplete(bool clean) {
  ++_b->completions;
  _b->clean_completion = clean;
}
void vTaskDelay(uint32_t duration) {
  assert(duration > 0);
  ++sleeps;
  clock_ms += duration;
  elapsed_ms += duration;
  if (elapsed_ms >= ack_after_ms)
    bridge_waiting->_stop_acked.store(true, std::memory_order_release);
  assert(elapsed_ms < 60000);
}
static void assert_finished(MQTTBridge& bridge, bool clean) {
  assert(!bridge._initialized && !bridge.resources_owned);
  assert(bridge.releases == 1 && bridge.completions == 1);
  assert(bridge.clean_completion == clean);
  assert(!bridge._lifecycle.isStopInProgress() && bridge._lifecycle.mayRestart());
  assert(bridge._lifecycle.mayBeginFlash() == clean);
  assert(!bridge._slots_setup_done && !bridge._staged_raw_valid);
  assert(!bridge._ntp_estimate_requested && !bridge._ntp_estimate_done);
  assert(!bridge._ntp_estimate_ok && bridge._ntp_estimate_epoch == 0);
  assert(bridge.cancels == 1);
}
'''

ASYNC_STOP_MAIN = r'''
int main() {
  {
    MQTTBridge bridge;
    bridge._stop_acked = true; // stale prior-epoch ACK must be cleared exactly once
    bridge.requestStop();
    assert(sleeps == 0 && elapsed_ms == 0 && bridge.ops.signals == 1);
    assert(bridge._stop_requested && !bridge._stop_acked);
    assert(bridge._initialized && bridge.resources_owned && bridge.releases == 0);
    assert(s_mqtt_bridge_instance == nullptr);
    const auto timeout = bridge._lifecycle.stopTimeoutMs();
    assert(timeout > 5000);
    for (int pass = 0; pass < 10; ++pass) {
      clock_ms += timeout; // ordinary loop must never force-free a slow worker
      bridge.requestStop();
      bridge.loop();
      assert(bridge.ops.signals == 1 && sleeps == 0);
      assert(bridge._initialized && bridge.resources_owned && bridge.releases == 0);
      assert(bridge._lifecycle.isStopInProgress() && !bridge._lifecycle.stopTimedOut());
    }
    bridge._stop_acked.store(true, std::memory_order_release);
    bridge.requestStop();
    assert(bridge._stop_acked && bridge.ops.signals == 1); // duplicate cannot revoke ACK
    bridge.loop();
    assert_finished(bridge, true);
    bridge.loop();
    bridge.requestStop();
    bridge.end();
    assert(bridge.releases == 1 && bridge.ops.signals == 1 && bridge.cancels == 1);
  }
  for (int at_deadline = 0; at_deadline < 2; ++at_deadline) {
    MQTTBridge bridge;
    clock_ms = UINT32_MAX - 500; elapsed_ms = 0; sleeps = 0;
    bridge_waiting = &bridge;
    bridge.requestStop();
    assert(bridge._lifecycle.stopTimeoutMs() == 45000);
    // A ten-second blocked publish plus healthy client teardown can exceed
    // the former 29-second budget for three slots without requiring a kill.
    ack_after_ms = at_deadline ? bridge._lifecycle.stopTimeoutMs() : 31000;
    bridge.end(); // synchronous OTA barrier joins the existing request
    assert(elapsed_ms == ack_after_ms && sleeps > 0 && bridge.ops.signals == 1);
    assert(!bridge._lifecycle.stopTimedOut());
    assert_finished(bridge, true);
  }
  {
    MQTTBridge bridge;
    clock_ms = 0; elapsed_ms = 0; sleeps = 0;
    bridge_waiting = &bridge; ack_after_ms = UINT64_MAX;
    bridge.end(); // reviewed timeout remains confined to the explicit barrier
    assert(bridge.ops.signals == 1 && bridge._lifecycle.stopTimedOut());
    assert(elapsed_ms == bridge._lifecycle.stopTimeoutMs());
    assert_finished(bridge, false);
  }
}
'''


class MqttTransportResultsTests(unittest.TestCase):
    def test_actual_async_stop_preserves_resources_until_ack_and_ota_joins(self):
        compiler = shutil.which("g++") or shutil.which("c++")
        self.assertIsNotNone(compiler, "a host C++ compiler is required")
        bridge = "src/helpers/bridges/MQTTBridge.cpp"
        source = ASYNC_STOP_PREAMBLE + "\n".join(method(bridge, signature) for signature in (
            "static inline uint32_t mqttStopTimeoutForSlots(int slots)",
            "void MQTTBridge::requestStop()", "void MQTTBridge::end()",
            "void MQTTBridge::finishStopped()", "void MQTTBridge::loop()",
            "uint32_t MQTTBridge::LifecycleOps::nowMs()",
            "void MQTTBridge::LifecycleOps::startTask()",
            "void MQTTBridge::LifecycleOps::deliverStop()")) + ASYNC_STOP_MAIN
        with tempfile.TemporaryDirectory(prefix="meshcore-mqtt-async-stop-") as temp:
            path = Path(temp) / "stop.cpp"
            path.write_text(source, encoding="utf-8")
            exe = Path(temp) / ("stop.exe" if os.name == "nt" else "stop")
            result = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra",
                                     "-I", str(ROOT / "src"), str(path), "-o", str(exe)],
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(exe)], text=True, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_actual_worker_interrupts_startup_and_service_without_late_work(self):
        compiler = shutil.which("g++") or shutil.which("c++")
        self.assertIsNotNone(compiler, "a host C++ compiler is required")
        bridge = "src/helpers/bridges/MQTTBridge.cpp"
        source = TASK_PREAMBLE + method(bridge,
            "bool MQTTBridge::waitUnlessStopping(uint32_t delay_ms)")
        source += method(bridge, "void MQTTBridge::mqttTaskLoop()") + TASK_MAIN
        with tempfile.TemporaryDirectory(prefix="meshcore-mqtt-stop-") as temp:
            path = Path(temp) / "worker.cpp"
            path.write_text(source, encoding="utf-8")
            exe = Path(temp) / ("worker.exe" if os.name == "nt" else "worker")
            result = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra",
                                     str(path), "-o", str(exe)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            for scenario in range(12):
                for tick_ms in (1, 10, 50):
                    for wrap in (0, 1):
                        with self.subTest(scenario=scenario, tick_ms=tick_ms, wrap=wrap):
                            result = subprocess.run([str(exe), str(scenario), str(tick_ms), str(wrap)],
                                                    text=True, capture_output=True, timeout=5)
                            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_real_adapter_and_bridge_methods_for_both_idf_layouts(self):
        compiler = shutil.which("g++") or shutil.which("c++")
        self.assertIsNotNone(compiler, "a host C++ compiler is required")
        adapter = "lib/PsychicMqttClient/src/PsychicMqttClient.cpp"
        source = PREAMBLE + "\n".join(method(adapter, signature) for signature in (
            "esp_err_t PsychicMqttClient::applyConfig()",
            "esp_err_t PsychicMqttClient::connect()",
            "esp_err_t PsychicMqttClient::reconnect()"))
        source += method("src/helpers/bridges/MQTTBridge.cpp",
                         "bool MQTTBridge::hasPendingSlotConnection() const")
        source += method("src/helpers/bridges/MQTTBridge.cpp",
                         "bool MQTTBridge::canStartSlotConnection(int index) const")
        source += method("src/helpers/bridges/MQTTBridge.cpp",
                         "esp_err_t MQTTBridge::reconnectSlotClient(int index)") + MAIN
        with tempfile.TemporaryDirectory(prefix="meshcore-mqtt-results-") as temp:
            path = Path(temp) / "results.cpp"
            path.write_text(source, encoding="utf-8")
            for major in (4, 5):
                with self.subTest(idf_major=major):
                    exe = Path(temp) / (f"results-{major}" + (".exe" if os.name == "nt" else ""))
                    result = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra",
                                             f"-DESP_IDF_VERSION_MAJOR={major}",
                                             "-I", str(ROOT / "src"), str(path), "-o", str(exe)],
                                            text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    result = subprocess.run([str(exe)], text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_attempt_wiring_covers_stop_bounce_setup_and_both_status_paths(self):
        source = (ROOT / "src/helpers/bridges/MQTTBridge.cpp").read_text(encoding="utf-8")
        self.assertEqual(source.count("result = slot.client->connect();"), 1)
        self.assertEqual(source.count("result = slot.client->reconnect();"), 1)
        self.assertEqual(source.count("repeatStatus(), collectConnHealth()"), 2)
        for signature in ("void MQTTBridge::teardownSlot(", "void MQTTBridge::destroySlotClients("):
            body = method("src/helpers/bridges/MQTTBridge.cpp", signature)
            self.assertIn("_slot_attempt_pending[", body)
            self.assertNotIn("connect_failures =", body)
            self.assertNotIn("start_failures =", body)
        callback = source[source.index("slot.client->onDisconnect("):source.index("slot.client->onError(")]
        self.assertIn("disconnectFailedAttempt", callback)
        self.assertIn("incrementFailures", callback)
        stats = method("src/helpers/bridges/MQTTBridge.cpp", "void MQTTBridge::formatMqttStatsReply(")
        self.assertEqual(stats.count("MQTTConnectionHealth::isOutageSlot("), 2)


if __name__ == "__main__":
    unittest.main()
