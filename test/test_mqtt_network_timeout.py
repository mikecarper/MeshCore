#!/usr/bin/env python3
"""Exercise production MQTT configuration for both SDK layouts and RAM profiles.

The real optimizer and adapter setters run on the host. SDK field layouts are
stand-ins; this checks the timeout written to SDK configuration, not a handshake.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_mqtt_transport_results import method
from test_mqtt_publish_ack import (ARDUINO_HEADER, MQTT_HEADER,
                                  PREAMBLE as ADAPTER_PREAMBLE)

ROOT = Path(__file__).resolve().parents[1]

PREAMBLE = r'''
#include <cassert>
#include <cstddef>
#include "helpers/MQTTConnectionAdmission.h"
#include "helpers/MQTTConnectionPolicy.h"
struct esp_mqtt_client_config_t {
#if ESP_IDF_VERSION_MAJOR == 5
  struct { int keepalive = 0, message_retransmit_timeout = 0; } session;
  struct { int timeout_ms = 0; } network;
  struct { int size = 0, out_size = 0; } buffer;
  struct { int stack_size = 0, priority = 0; } task;
#else
  int keepalive = 0, message_retransmit_timeout = 0;
  int network_timeout_ms = 0, buffer_size = 0;
  int task_stack = 0, task_prio = 0;
#endif
};
struct PsychicMqttClient {
  esp_mqtt_client_config_t _mqtt_cfg;
  bool _config_dirty = false;
  size_t _outbox_limit = 0;
  PsychicMqttClient& setKeepAlive(int);
  PsychicMqttClient& setMessageRetransmitTimeout(int);
  PsychicMqttClient& setBufferSize(int);
  PsychicMqttClient& setNetworkTimeout(int);
  PsychicMqttClient& setOutboxLimit(size_t);
  PsychicMqttClient& setTaskStackAndPriority(int, int);
  esp_mqtt_client_config_t* getMqttConfig();
};
struct MQTTBridge {
  void optimizeMqttClientConfig(PsychicMqttClient*, bool);
};
'''

MAIN = r'''
int main() {
  MQTTBridge bridge;
  bridge.optimizeMqttClientConfig(nullptr, true);
  PsychicMqttClient client;
  // Reconfiguration must restore the connection budget after a shorter value,
  // for both small user/password and large JWT CONNECT buffers.
  for (bool needs_large : {false, true, false}) {
    client.setNetworkTimeout(2500);
    client.setTaskStackAndPriority(2048, 1);
    client._config_dirty = false;
    bridge.optimizeMqttClientConfig(&client, needs_large);
    assert(client._config_dirty);
#if ESP_IDF_VERSION_MAJOR == 5
    const int timeout = client._mqtt_cfg.network.timeout_ms;
    const int buffer_size = client._mqtt_cfg.buffer.size;
    const int keepalive = client._mqtt_cfg.session.keepalive;
    const int stack = client._mqtt_cfg.task.stack_size;
    const int priority = client._mqtt_cfg.task.priority;
    assert(client._mqtt_cfg.buffer.out_size == buffer_size);
    assert(client._mqtt_cfg.session.message_retransmit_timeout == 15000);
#else
    const int timeout = client._mqtt_cfg.network_timeout_ms;
    const int buffer_size = client._mqtt_cfg.buffer_size;
    const int keepalive = client._mqtt_cfg.keepalive;
    const int stack = client._mqtt_cfg.task_stack;
    const int priority = client._mqtt_cfg.task_prio;
    assert(client._mqtt_cfg.message_retransmit_timeout == 15000);
#endif
    assert(timeout == 10000);
    assert(timeout == MQTTConnectionPolicy::kNetworkTimeoutMs);
    assert(stack == 6144 && priority == 5);
    assert(stack == MQTTConnectionAdmission::kClientTaskStackBytes);
    assert(priority == MQTTConnectionAdmission::kClientTaskPriority);
#ifdef BOARD_HAS_PSRAM
    assert(buffer_size == 896 && keepalive == 45);
    assert(client._outbox_limit == 16384);
#else
    assert(buffer_size == 896 && keepalive == 75);
    assert(client._outbox_limit == 8192);
#endif
  }
}
'''

RECONFIGURE_PREAMBLE = r'''
#include "helpers/MQTTConnectionAdmission.h"
#include "helpers/MQTTConnectionPolicy.h"
#define MQTT_DEBUG_PRINTLN(...) ((void)0)
#define pdMS_TO_TICKS(value) (value)
constexpr int RUNTIME_MQTT_SLOTS = 2, MQTT_AUTH_JWT = 1;
constexpr uint32_t MALLOC_CAP_INTERNAL = 1, MALLOC_CAP_DMA = 2;
static size_t dma_free = 100000, dma_largest = 20000;
size_t heap_caps_get_free_size(uint32_t caps) {
  assert(caps == (MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA)); return dma_free;
}
size_t heap_caps_get_largest_free_block(uint32_t caps) {
  assert(caps == (MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA)); return dma_largest;
}
struct { size_t getPsramSize() const {
#ifdef BOARD_HAS_PSRAM
  return 2097152;
#else
  return 0;
#endif
} } ESP;
constexpr const char* MQTT_PRESET_NONE = "none";
constexpr const char* MQTT_PRESET_CUSTOM = "custom";
struct MQTTPresetDef { const char* server_url; int auth_type; };
static const MQTTPresetDef plain_preset{"mqtt://broker", 0};
static const MQTTPresetDef jwt_preset{"mqtt://broker", MQTT_AUTH_JWT};
const MQTTPresetDef* findMQTTPreset(const char* name) {
  if (strcmp(name, "plain") == 0) return &plain_preset;
  if (strcmp(name, "jwt") == 0) return &jwt_preset;
  return nullptr;
}
bool mqttPresetNeedsSlotCredentials(const MQTTPresetDef*) { return false; }
bool customEndpointComplete(const char* host, uint16_t port) { return host[0] && port; }
struct MQTTPrefs {
  char mqtt_slot_host[RUNTIME_MQTT_SLOTS][64] = {"custom-broker", ""};
  uint16_t mqtt_slot_port[RUNTIME_MQTT_SLOTS] = {1883, 0};
  char mqtt_slot_username[RUNTIME_MQTT_SLOTS][32] = {"custom-user", ""};
  char mqtt_slot_password[RUNTIME_MQTT_SLOTS][32] = {"custom-password", ""};
  char mqtt_slot_audience[RUNTIME_MQTT_SLOTS][32] = {"custom-audience", ""};
};
struct MQTTBridge {
  struct MQTTSlot {
    PsychicMqttClient* client = nullptr;
    bool enabled = false, connected = false, initial_connect_done = false;
    const MQTTPresetDef* preset = nullptr;
    char host[64] = {}, broker_uri[128] = {}, username[32] = {}, password[32] = {};
    char audience[32] = {}, *auth_token = nullptr;
    uint16_t port = 1883;
    unsigned long token_expires_at = 0, last_token_renewal = 0;
    unsigned long last_reconnect_attempt = 0, last_log_time = 0, last_deferred_log_ms = 0;
    uint8_t reconnect_backoff = 0, max_backoff_failures = 0;
    bool circuit_breaker_tripped = false;
  } _slots[RUNTIME_MQTT_SLOTS];
  std::atomic<bool> _stop_requested{false}, _slot_attempt_pending[RUNTIME_MQTT_SLOTS] = {};
  bool _slot_force_jwt_mint[RUNTIME_MQTT_SLOTS] = {};
  bool _initialized = true;
  int _max_active_slots = 2;
  MQTTPrefs prefs;
  MQTTPrefs* _obs = &prefs;
  bool canActivateSlot(int) const { return true; }
  bool isSlotReady(int, char*, size_t) const { return true; }
  bool hasPendingSlotConnection() const;
  bool canStartSlotConnection(int) const;
  void optimizeMqttClientConfig(PsychicMqttClient*, bool);
  void teardownSlot(int, bool force = false);
  void applySlotPreset(int, const char*);
  bool setupSlot(int index) {
    auto& slot = _slots[index];
    // The setup seam executes the actual optimizer, adapter init/start, and
    // production admission. Token creation and network callbacks are seams.
    if (!canStartSlotConnection(index)) return false;
    if (!slot.client) slot.client = new PsychicMqttClient;
    optimizeMqttClientConfig(slot.client,
        (slot.preset && slot.preset->auth_type == MQTT_AUTH_JWT) || slot.audience[0]);
    slot.client->_mqtt_cfg.uri = "mqtt://broker";
    slot.client->_mqtt_cfg.broker.address.uri = "mqtt://broker";
    if (slot.client->connect() != ESP_OK) return false;
    esp_mqtt_event_t event{MQTT_EVENT_CONNECTED, 0, false};
    sdk_event_handler(sdk_handler_args, "MQTT", event.event_id, &event);
    slot.initial_connect_done = slot.connected = true;
    return true;
  }
};
'''

RECONFIGURE_MAIN = r'''
int main() {
  MQTTBridge bridge;
  auto& slot = bridge._slots[0];
  bridge.applySlotPreset(0, "plain");
  assert(slot.client && slot.initial_connect_done && slot.client->isStarted());
  assert(slot.client->_buffer_capacity == 896);
  char token[8] = "old-jwt";
  slot.auth_token = token;
  bridge.applySlotPreset(0, "jwt");
  assert(token[0] == '\0' && slot.preset == &jwt_preset);
  assert(slot.client->_buffer_capacity == 896);
  bridge.applySlotPreset(0, "plain");
  assert(initialized_buffer_sizes == std::vector<int>{896});
  assert(init_calls == 1 && destroy_calls == 0 && slot.client->_buffer_capacity == 896);
  // A custom broker's audience requests JWT-size buffers through the same
  // explicit-reconfiguration path and synchronizes its saved fields.
  bridge.applySlotPreset(0, "custom");
  assert(slot.preset == nullptr && slot.client->_buffer_capacity == 896);
  assert(strcmp(slot.host, "custom-broker") == 0 && slot.port == 1883);
  assert(strcmp(slot.audience, "custom-audience") == 0);
  assert(strcmp(slot.username, "custom-user") == 0);
  assert(strcmp(slot.password, "custom-password") == 0);
  bridge.applySlotPreset(0, "none");
  assert(!slot.enabled && !slot.initial_connect_done);
  assert(slot.client && slot.client->isInitialized() && !slot.client->isStarted());
  assert(init_calls == 1 && destroy_calls == 0);
  // Reconfiguration retains the initialized client on both board profiles.
  // It needs the stopped-task floor and never silently shrinks its buffers.
  dma_free = 24575; dma_largest = 20000;
  bridge.applySlotPreset(0, "plain");
  assert(slot.client && !slot.client->isStarted() && !slot.initial_connect_done);
  assert(init_calls == 1 && destroy_calls == 0);
  dma_free = 24576;
  assert(bridge.setupSlot(0));
  assert(slot.client->_buffer_capacity == 896 && init_calls == 1);
  const int destructions_before_guard = destroy_calls;
  bridge.applySlotPreset(-1, "jwt");
  bridge.applySlotPreset(RUNTIME_MQTT_SLOTS, "jwt");
  bridge._stop_requested = true;
  bridge.applySlotPreset(0, "jwt");
  assert(destroy_calls == destructions_before_guard && slot.client->isStarted());
  delete slot.client;
  slot.client = nullptr;
}
'''


class MqttNetworkTimeoutTest(unittest.TestCase):
    def fixture(self):
        adapter = "lib/PsychicMqttClient/src/PsychicMqttClient.cpp"
        source = PREAMBLE + "#include <initializer_list>\n"
        source += "\n".join(method(adapter, signature) for signature in (
            "PsychicMqttClient &PsychicMqttClient::setKeepAlive(",
            "PsychicMqttClient &PsychicMqttClient::setMessageRetransmitTimeout(",
            "PsychicMqttClient &PsychicMqttClient::setBufferSize(",
            "PsychicMqttClient &PsychicMqttClient::setNetworkTimeout(",
            "PsychicMqttClient &PsychicMqttClient::setOutboxLimit(",
            "PsychicMqttClient &PsychicMqttClient::setTaskStackAndPriority(",
            "esp_mqtt_client_config_t *PsychicMqttClient::getMqttConfig("))
        source += method("src/helpers/bridges/MQTTBridge.cpp",
                         "void MQTTBridge::optimizeMqttClientConfig(")
        return source + MAIN

    def compile_run(self, source, expect_rejection=False):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a host C++ compiler is required")
        with tempfile.TemporaryDirectory(prefix="meshcore-mqtt-timeout-") as temporary:
            work = Path(temporary)
            path = work / "config.cpp"
            path.write_text(source, encoding="utf-8")
            for major in (4, 5):
                for psram in (False, True):
                    with self.subTest(idf_major=major, psram=psram):
                        binary = work / f"config-{major}-{int(psram)}"
                        command = [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                                   "-Wno-unused-parameter",
                                   f"-DESP_IDF_VERSION_MAJOR={major}",
                                   "-I", str(ROOT / "src"), str(path), "-o", str(binary)]
                        if psram:
                            command.insert(1, "-DBOARD_HAS_PSRAM=1")
                        compiled = subprocess.run(command, capture_output=True,
                                                  text=True, timeout=60)
                        self.assertEqual(compiled.returncode, 0, compiled.stderr)
                        result = subprocess.run([str(binary)], cwd=work, capture_output=True,
                                                text=True, timeout=10)
                        if expect_rejection:
                            self.assertNotEqual(result.returncode, 0,
                                                result.stdout + result.stderr)
                            self.assertIn("Assertion", result.stderr)
                        else:
                            self.assertEqual(result.returncode, 0,
                                             result.stdout + result.stderr)

    def test_production_optimizer_applies_connection_budget_to_every_config(self):
        self.compile_run(self.fixture())

    def test_production_preset_reconfiguration_retains_uniform_sdk_and_reassembly_buffers(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a host C++ compiler is required")
        adapter = "lib/PsychicMqttClient/src/PsychicMqttClient.cpp"
        # Reuse the actual-header SDK seam; this bridge fixture needs the full
        # preset reconfiguration path rather than the publish diagnostic shell.
        preamble = ADAPTER_PREAMBLE.split("constexpr int RUNTIME_MQTT_SLOTS = 2;", 1)[0]
        preamble = preamble.replace(
            'void vTaskDelay(unsigned) { assert(false && "stub disconnect must deliver its event"); }',
            'void vTaskDelay(unsigned ticks) { assert(ticks == 50); }')
        source = "#define ESP_PLATFORM 1\n" + preamble + RECONFIGURE_PREAMBLE
        source += "\n".join(method(adapter, signature) for signature in (
            "PsychicMqttClient::PsychicMqttClient()",
            "PsychicMqttClient::~PsychicMqttClient()",
            "PsychicMqttClient &PsychicMqttClient::setKeepAlive(",
            "PsychicMqttClient &PsychicMqttClient::setMessageRetransmitTimeout(",
            "PsychicMqttClient &PsychicMqttClient::setBufferSize(",
            "PsychicMqttClient &PsychicMqttClient::setNetworkTimeout(",
            "PsychicMqttClient &PsychicMqttClient::setOutboxLimit(",
            "PsychicMqttClient &PsychicMqttClient::setTaskStackAndPriority(",
            "esp_mqtt_client_config_t *PsychicMqttClient::getMqttConfig(",
            "bool PsychicMqttClient::connected()",
            "esp_err_t PsychicMqttClient::applyConfig()",
            "esp_err_t PsychicMqttClient::connect()",
            "void PsychicMqttClient::disconnect()",
            "void PsychicMqttClient::forceStop()",
            "int PsychicMqttClient::subscribe(",
            "void PsychicMqttClient::_onMqttEventStatic(",
            "void PsychicMqttClient::_onMqttEvent(",
            "void PsychicMqttClient::_onConnect(",
            "void PsychicMqttClient::_onDisconnect(",
            "void PsychicMqttClient::_onSubscribe(",
            "void PsychicMqttClient::_onUnsubscribe(",
            "void PsychicMqttClient::_onPublish(",
        ))
        source += "\n".join(method("src/helpers/bridges/MQTTBridge.cpp", signature)
                            for signature in (
            "bool MQTTBridge::hasPendingSlotConnection() const",
            "bool MQTTBridge::canStartSlotConnection(int index) const",
            "void MQTTBridge::optimizeMqttClientConfig(",
            "void MQTTBridge::teardownSlot(",
            "void MQTTBridge::applySlotPreset(",
        )) + RECONFIGURE_MAIN
        with tempfile.TemporaryDirectory(prefix="meshcore-mqtt-reconfigure-") as temporary:
            work = Path(temporary)
            (work / "Arduino.h").write_text(ARDUINO_HEADER, encoding="ascii")
            (work / "mqtt_client.h").write_text(MQTT_HEADER, encoding="ascii")
            (work / "esp_crt_bundle.h").write_text("#pragma once\n", encoding="ascii")
            path = work / "reconfigure.cpp"
            path.write_text(source, encoding="ascii")
            for major in (4, 5):
                for psram in (False, True):
                    with self.subTest(idf_major=major, psram=psram):
                        binary = work / f"reconfigure-{major}-{int(psram)}"
                        command = [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                                   "-Wno-unused-parameter", "-DARDUINO_ARCH_ESP32=1",
                                   f"-DESP_IDF_VERSION_MAJOR={major}",
                                   "-I", str(work),
                                   "-I", str(ROOT / "lib/PsychicMqttClient/src"),
                                   "-I", str(ROOT / "src"), str(path), "-o", str(binary)]
                        if psram:
                            command.insert(1, "-DBOARD_HAS_PSRAM=1")
                        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_previous_publish_timeout_fails_configuration_regression(self):
        source = self.fixture()
        old = source.replace(
            "client->setNetworkTimeout(MQTTConnectionPolicy::kNetworkTimeoutMs);",
            "client->setNetworkTimeout(2500);", 1)
        self.assertNotEqual(old, source)
        self.compile_run(old, expect_rejection=True)

    def test_production_stop_budget_covers_shared_network_timeout_for_every_slot(self):
        bridge = ROOT / "src/helpers/bridges/MQTTBridge.cpp"
        source = bridge.read_text(encoding="utf-8")
        start = source.index("static const uint32_t MQTT_STOP_TIMEOUT_BASE_MS")
        end = source.index("static inline uint32_t mqttStopTimeoutForSlots(int slots)")
        fixture = '#include <cassert>\n#include "helpers/MQTTConnectionPolicy.h"\n'
        fixture += source[start:end]
        fixture += method("src/helpers/bridges/MQTTBridge.cpp",
                          "static inline uint32_t mqttStopTimeoutForSlots(int slots)")
        fixture += r'''
int main() {
  assert(mqttStopTimeoutForSlots(0) == 25000);
  assert(mqttStopTimeoutForSlots(-1) == 25000);
  assert(mqttStopTimeoutForSlots(2) == 35000);
  assert(mqttStopTimeoutForSlots(5) == 65000);
  assert(mqttStopTimeoutForSlots(6) == 75000);
  assert(mqttStopTimeoutForSlots(6) > UINT16_MAX);
  assert(mqttStopTimeoutForSlots(16) == 175000);
}
'''
        self.compile_run(fixture)


if __name__ == "__main__":
    unittest.main()
