"""Run actual slot identity and status scheduling methods without a broker."""

from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from test_mqtt_transport_results import method


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = "src/helpers/bridges/MQTTBridge.cpp"
PREAMBLE = r'''
#include <atomic>
#include <cassert>
#include <cctype>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <functional>
#include <map>
#include <string>
#include <sys/time.h>
#ifdef _timezone
#undef _timezone
#endif
#include "helpers/MQTTStatusPublishPolicy.h"
#define MQTT_DEBUG_PRINTLN(...) ((void)0)
constexpr int RUNTIME_MQTT_SLOTS = 6, MALLOC_CAP_INTERNAL = 1;
static uint32_t now_ms = 0;
static unsigned builds = 0;
static bool build_succeeds = true;
uint32_t millis() { return now_ms; }
size_t heap_caps_get_free_size(int) { return 100000; }
struct { uint64_t value = 0xAABBCCDDEEFF; uint64_t getEfuseMac() const { return value; } } ESP;
struct MQTTRadio2Info { const char* radio; const char* mode; bool temporary; };
namespace MQTTMessageBuilder {
void formatIsoTimestampForMqtt(long, long, const char*, char* out, size_t size) { snprintf(out, size, "UTC"); }
template<typename... Args>
int buildStatusMessage(int, const char*, const char*, const char*, const char*,
    const char*, const char*, const char*, const char*, char* buffer, int size, Args...) {
  ++builds;
  if (!build_succeeds) return 0;
  assert(size > 2); strcpy(buffer, "{}"); return 2;
}
}
void formatRadioInfo(float, float, int, int, char* out, size_t size) { snprintf(out, size, "radio"); }
struct Client {
  const char* id = nullptr;
  void setClientId(const char* value) { id = value; } // Real adapter retains this pointer.
};
struct Preset { bool allow_retain = false; };
struct MQTTBridge {
  struct MQTTSlot {
    Client* client = nullptr;
    char client_id[32] = {};
    bool enabled = false, connected = false;
    const Preset* preset = nullptr;
    MQTTStatusPublishPolicy::State status_publish;
  } _slots[RUNTIME_MQTT_SLOTS];
  Client clients[RUNTIME_MQTT_SLOTS];
  std::atomic<bool> _status_publish_pending[RUNTIME_MQTT_SLOTS] = {};
  std::atomic<bool> _stop_requested{false};
  struct Observer { bool mqtt_status_enabled = true; } prefs;
  Observer* _obs = &prefs;
  bool accepted[RUNTIME_MQTT_SLOTS] = {true,true,true,true,true,true};
  bool topic_enabled[RUNTIME_MQTT_SLOTS] = {true,true,true,true,true,true};
  unsigned calls[RUNTIME_MQTT_SLOTS] = {};
  std::function<void()> during_enqueue;
  static constexpr int STATUS_JSON_BUFFER_SIZE = 1024, MSG_STATUS = 0;
  static constexpr uint32_t STATUS_RETRY_INTERVAL = 30000;
  uint32_t _status_interval = 300000;
  struct { float* freq = nullptr; float* bw = nullptr; uint8_t* sf = nullptr; uint8_t* cr = nullptr; } _node_info;
  const char *_timezone = "UTC", *_origin = "node", *_device_id = "ABCDEF";
  const char *_board_model = "G3", *_firmware_version = "test";
  int _json_scratch_doc = 0, _queue_count = 0;
  char _json_scratch_buffer[STATUS_JSON_BUFFER_SIZE] = {};
  struct Board { int getBattMilliVolts() const { return 4000; } };
  struct Clock { uint32_t getMillis() const { return now_ms; } };
  struct Dispatcher {
    int getErrFlags() const { return 0; } int getTotalAirTime() const { return 0; }
    int getReceiveAirTime() const { return 0; } int getNumSentFlood() const { return 0; }
    int getNumSentDirect() const { return 0; } int getNumRecvFlood() const { return 0; }
    int getNumRecvDirect() const { return 0; }
  };
  struct Radio { int getNoiseFloor() const { return -120; } int getPacketsRecvErrors() const { return 0; } };
  Board* _board = nullptr; Clock* _ms = nullptr; Dispatcher* _dispatcher = nullptr; Radio* _radio = nullptr;
  void refreshOriginFromPrefs() {}
  void getClientVersion(char* out, size_t size) { snprintf(out, size, "test"); }
  int repeatStatus() const { return 0; } int collectConnHealth() const { return 0; }
  const MQTTRadio2Info* radio2StatusInfo(MQTTRadio2Info&, char*, size_t) const { return nullptr; }
  bool buildTopicForSlot(int i, int type, char* out, size_t size) {
    assert(type == MSG_STATUS); snprintf(out, size, "status%d", i); return topic_enabled[i];
  }
  bool publishToSlot(int i, const char* topic, const char* payload, size_t length, bool retained, uint8_t qos) {
    assert(qos == 1 && length == 2 && !strcmp(payload, "{}") && !strncmp(topic, "status", 6));
    assert(retained == (_slots[i].preset ? _slots[i].preset->allow_retain : false));
    ++calls[i];
    if (during_enqueue) { auto callback = during_enqueue; during_enqueue = nullptr; callback(); }
    return accepted[i];
  }
  void connect(int count) {
    for (int i = 0; i < count; ++i) {
      _slots[i].enabled = _slots[i].connected = true; _slots[i].client = &clients[i];
      _status_publish_pending[i] = true;
    }
  }
  void setSlotClientId(int);
  void maintainStatus(uint32_t);
  bool publishStatus(uint8_t);
  bool publishStatusToSlot(int);
};
'''


class MqttSlotStatusTests(unittest.TestCase):
    def compile_run(self, main):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a native C++ compiler is required")
        production = "\n".join(method(BRIDGE, signature) for signature in (
            "void MQTTBridge::setSlotClientId(", "void MQTTBridge::maintainStatus(",
            "bool MQTTBridge::publishStatus(", "bool MQTTBridge::publishStatusToSlot("))
        flags = [] if os.name == "nt" else ["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
        with tempfile.TemporaryDirectory(prefix="mqtt-slot-status-") as directory:
            source, binary = Path(directory) / "status.cpp", Path(directory) / "status"
            source.write_text(PREAMBLE + production + main)
            result = subprocess.run([compiler, "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
                *flags, "-I", str(ROOT / "src"), str(source), "-o", str(binary)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_duplicate_broker_slots_have_stable_distinct_persistent_client_ids(self):
        setup = method(BRIDGE, "bool MQTTBridge::setupSlot(")
        self.assertLess(setup.index("setSlotClientId(index)"), setup.index("reconnectSlotClient(index)"))
        self.compile_run(r'''
int main() {
  MQTTBridge bridge; bridge.connect(5);
  std::map<std::string, unsigned> broker_sessions;
  for (int i = 0; i < 5; ++i) {
    bridge.setSlotClientId(i);
    assert(bridge.clients[i].id == bridge._slots[i].client_id);
    assert(strlen(bridge.clients[i].id) <= 23);
    for (const char* p = bridge.clients[i].id; *p; ++p) assert(isalnum(static_cast<unsigned char>(*p)));
    assert(broker_sessions.emplace(bridge.clients[i].id, i).second);
  }
  assert(broker_sessions.size() == 5);
  assert(!strcmp(bridge.clients[0].id, "meshcoreAABBCCDDEEFFs1"));
  char saved[32]; strcpy(saved, bridge.clients[1].id);
  // Reconfigure/reconnect retains identity and storage despite stack churn.
  for (unsigned n = 0; n < 100; ++n) {
    volatile char scratch[128]; for (unsigned i = 0; i < sizeof(scratch); ++i) scratch[i] = char(i + n);
    bridge.setSlotClientId(1);
    assert(!strcmp(bridge.clients[1].id, saved));
  }
  assert(broker_sessions.size() == 5 && !bridge._slots[5].enabled);
  MQTTBridge next_boot; next_boot.connect(1); next_boot.setSlotClientId(0);
  assert(!strcmp(next_boot.clients[0].id, bridge.clients[0].id));
  ESP.value ^= 1; next_boot.setSlotClientId(0);
  assert(strcmp(next_boot.clients[0].id, bridge.clients[0].id));
}
''')

    def test_failed_status_is_retried_per_slot_without_republishing_successful_slots(self):
        self.compile_run(r'''
int main() {
  MQTTBridge bridge; bridge.connect(5);
  bridge.accepted[1] = bridge.accepted[3] = false;
  now_ms = 0; bridge.maintainStatus(now_ms);
  assert(builds == 1);
  for (int i = 0; i < 5; ++i) assert(bridge.calls[i] == 1);
  assert(bridge._slots[0].status_publish.has_accepted && !bridge._slots[0].status_publish.pending);
  assert(bridge._slots[1].status_publish.pending);
  // Every 50 ms bridge tick remains bounded under prolonged memory/outbox rejection.
  for (now_ms = 50; now_ms < 30000; now_ms += 50) bridge.maintainStatus(now_ms);
  assert(builds == 1 && bridge.calls[1] == 1);
  now_ms = 30000; bridge.accepted[1] = true; bridge.maintainStatus(now_ms);
  assert(builds == 2 && bridge.calls[0] == 1 && bridge.calls[1] == 2 && bridge.calls[3] == 2);
  for (now_ms = 30050; now_ms < 60000; now_ms += 50) bridge.maintainStatus(now_ms);
  assert(builds == 2);
  now_ms = 60000; bridge.accepted[3] = true; bridge.maintainStatus(now_ms);
  assert(builds == 3 && bridge.calls[3] == 3 && bridge.calls[1] == 2);
  now_ms = 300000; bridge.maintainStatus(now_ms);
  assert(bridge.calls[0] == 2 && bridge.calls[2] == 2 && bridge.calls[4] == 2);
  assert(bridge.calls[1] == 2 && bridge.calls[3] == 3);
  now_ms = 330000; bridge.maintainStatus(now_ms); assert(bridge.calls[1] == 3);
  now_ms = 360000; bridge.maintainStatus(now_ms); assert(bridge.calls[3] == 4);
}
''')

    def test_json_failure_wraparound_reconnect_latch_and_modes_do_not_flood(self):
        self.compile_run(r'''
int main() {
  MQTTBridge bridge; bridge.connect(2); bridge.topic_enabled[1] = false;
  build_succeeds = false; now_ms = UINT32_MAX - 1000; bridge.maintainStatus(now_ms);
  assert(builds == 1 && bridge.calls[0] == 0 && bridge._slots[0].status_publish.pending);
  for (unsigned elapsed = 1; elapsed < 30000; elapsed += 50) {
    now_ms = uint32_t(UINT32_MAX - 1000 + elapsed); bridge.maintainStatus(now_ms);
  }
  assert(builds == 1);
  build_succeeds = true; now_ms = uint32_t(UINT32_MAX - 1000 + 30000); bridge.maintainStatus(now_ms);
  assert(builds == 2 && bridge.calls[0] == 1 && bridge.calls[1] == 0);
  bridge._slots[0].connected = false; now_ms += 300000; bridge.maintainStatus(now_ms);
  assert(builds == 2);
  bridge._slots[0].connected = true; bridge._status_publish_pending[0] = true;
  bridge.during_enqueue = [&] { bridge._status_publish_pending[0] = true; };
  bridge.maintainStatus(now_ms); assert(bridge.calls[0] == 2);
  assert(bridge._status_publish_pending[0]);
  bridge.maintainStatus(now_ms); assert(bridge.calls[0] == 3);
  assert(!bridge._status_publish_pending[0]);
  bridge.prefs.mqtt_status_enabled = false;
  now_ms += 300000; bridge.maintainStatus(now_ms); assert(bridge.calls[0] == 3);
  // Preserve the existing on-connect status even when periodic status is off.
  bridge._status_publish_pending[0] = true; bridge.maintainStatus(now_ms); assert(bridge.calls[0] == 4);
  bridge._stop_requested = true; bridge._status_publish_pending[0] = true;
  bridge.maintainStatus(now_ms); assert(bridge.calls[0] == 4 && bridge._status_publish_pending[0]);
  MQTTBridge stopping; stopping.connect(5);
  stopping.during_enqueue = [&] { stopping._stop_requested = true; };
  stopping.maintainStatus(now_ms);
  assert(stopping.calls[0] == 1);
  for (int i = 1; i < 5; ++i) assert(stopping.calls[i] == 0);
}
''')


if __name__ == "__main__":
    unittest.main()
