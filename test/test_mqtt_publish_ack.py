#!/usr/bin/env python3
"""Exercise the real MQTT publish-event path and bounded slot diagnostics on host.

The adapter uses its production header; SDK calls are controllable stubs. Only
the unrelated receive/error handlers are replaced. No PlatformIO or broker is
required, and both installed ESP-IDF configuration layouts are exercised.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = "lib/PsychicMqttClient/src/PsychicMqttClient.cpp"
BRIDGE = "src/helpers/bridges/MQTTBridge.cpp"


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


ARDUINO_HEADER = r'''
#pragma once
#include <cstddef>
#include <cstdint>
unsigned long millis();
void vTaskDelay(unsigned);
constexpr unsigned portTICK_PERIOD_MS = 1;
'''

MQTT_HEADER = r'''
#pragma once
using esp_err_t = int;
using esp_event_base_t = const char*;
using esp_mqtt_client_handle_t = void*;
constexpr int ESP_OK = 0, ESP_ERR_INVALID_STATE = 1, ESP_ERR_NO_MEM = 2;
enum {
  MQTT_EVENT_ANY = -1, MQTT_EVENT_CONNECTED, MQTT_EVENT_DISCONNECTED,
  MQTT_EVENT_SUBSCRIBED, MQTT_EVENT_UNSUBSCRIBED, MQTT_EVENT_PUBLISHED,
  MQTT_EVENT_DATA, MQTT_EVENT_ERROR
};
struct esp_mqtt_client_config_t {
  const char* uri;
  int buffer_size;
  struct { struct { const char* uri; } address; } broker;
  struct { int size; } buffer;
};
struct esp_mqtt_error_codes_t { int unused; };
struct esp_mqtt_event_t { int event_id, msg_id; bool session_present; };
using esp_mqtt_event_handle_t = esp_mqtt_event_t*;
'''

PREAMBLE = r'''
#include <atomic>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <utility>
// Preinclude the standard headers so this seam only opens adapter visibility.
#define private public
#include "PsychicMqttClient.h"
#undef private
#include "helpers/MQTTReplyFormat.h"
#include "helpers/MQTTConnectionHealth.h"
#include "helpers/MQTTPacketFilter.h"
#include "helpers/bridges/MQTTErrorLabels.h"
template<typename... Args> void test_log(Args&&...) {}
static const char* TAG = "test";
#define ESP_LOGE(...) test_log(__VA_ARGS__)
#define ESP_LOGW(...) test_log(__VA_ARGS__)
#define ESP_LOGI(...) test_log(__VA_ARGS__)
#define ESP_LOGV(...) test_log(__VA_ARGS__)
#define ESP_ERROR_CHECK_WITHOUT_ABORT(value) ((void)(value))
static unsigned long now_ms = 121000;
unsigned long millis() { return now_ms; }
void vTaskDelay(unsigned) { assert(false && "stub disconnect must deliver its event"); }
const char* esp_err_to_name(int) { return "mock"; }
static int publish_result = 0, enqueue_result = 41, outbox_size = 0;
static int publish_calls = 0, enqueue_calls = 0, config_calls = 0;
static int start_calls = 0, reconnect_calls = 0, receive_events = 0, error_events = 0;
using EventHandler = void(*)(void*, esp_event_base_t, int32_t, void*);
static EventHandler sdk_event_handler = nullptr;
static void* sdk_handler_args = nullptr;
void* esp_mqtt_client_init(esp_mqtt_client_config_t*) {
  return reinterpret_cast<void*>(1);
}
int esp_mqtt_client_register_event(void*, int, EventHandler handler, void* args) {
  sdk_event_handler = handler;
  sdk_handler_args = args;
  return ESP_OK;
}
int esp_mqtt_client_destroy(void*) { return ESP_OK; }
int esp_mqtt_set_config(void*, esp_mqtt_client_config_t*) {
  ++config_calls;
  return ESP_OK;
}
int esp_mqtt_client_start(void*) { ++start_calls; return ESP_OK; }
int esp_mqtt_client_reconnect(void*) { ++reconnect_calls; return ESP_OK; }
int esp_mqtt_client_stop(void*) { return ESP_OK; }
int esp_mqtt_client_disconnect(void*) {
  assert(sdk_event_handler != nullptr);
  esp_mqtt_event_t event{MQTT_EVENT_DISCONNECTED, 0, false};
  sdk_event_handler(sdk_handler_args, "MQTT", event.event_id, &event);
  return ESP_OK;
}
int esp_mqtt_client_subscribe(void*, const char*, int) { return 7; }
int esp_mqtt_client_get_outbox_size(void*) { return outbox_size; }
int esp_mqtt_client_publish(void*, const char*, const char*, int, int, bool) {
  ++publish_calls;
  return publish_result;
}
int esp_mqtt_client_enqueue(void*, const char*, const char*, int, int, bool, bool store) {
  assert(store);
  ++enqueue_calls;
  return enqueue_result;
}
void PsychicMqttClient::_onMessage(esp_mqtt_event_handle_t&) { ++receive_events; }
void PsychicMqttClient::_onError(esp_mqtt_event_handle_t&) { ++error_events; }

constexpr int RUNTIME_MQTT_SLOTS = 2;
struct MQTTPrefs {
  uint16_t mqtt_slot_packet_filter[RUNTIME_MQTT_SLOTS] = {
      MQTTPacketFilter::kAllPacketTypes, MQTTPacketFilter::kAllPacketTypes};
};
struct MQTTBridge {
  struct MQTTSlot {
    PsychicMqttClient* client = nullptr;
    bool enabled = true, connected = true, circuit_breaker_tripped = false;
    const void* preset = nullptr;
    char host[8] = "broker";
    uint32_t disconnect_count = 0, connect_failures = 0, start_failures = 0;
    unsigned long first_disconnect_time = 0, last_error_time = 0;
    int32_t last_tls_err = 0, last_tls_stack_err = 0;
    int last_sock_errno = 0;
  };
  MQTTSlot _slots[RUNTIME_MQTT_SLOTS];
  bool _initialized = true;
  bool ready = true;
  MQTTPrefs* _obs = nullptr;
  bool isSlotReady(int) const { return ready; }
  static const char* tlsErrorStr(int32_t);
  static void formatSlotDiagReply(char*, size_t, int);
};
static MQTTBridge* s_mqtt_bridge_instance = nullptr;
'''

MAIN = r'''
static void dispatch(PsychicMqttClient& client, int event_id, int msg_id = 0) {
  esp_mqtt_event_t event{event_id, msg_id, false};
  PsychicMqttClient::_onMqttEventStatic(&client, "MQTT", event_id, &event);
}

int main() {
  PsychicMqttClient client;
  assert(client.getPublishAck() == 0);
  client._mqtt_cfg.uri = "mqtt://broker";
  client._mqtt_cfg.broker.address.uri = "mqtt://broker";
  assert(client.connect() == ESP_OK);
  dispatch(client, MQTT_EVENT_CONNECTED);
  assert(client.connected() && client.getPublishAck() == 0);

  // Acceptance, synchronous success, failure, and outbox drops are distinct
  // from a broker acknowledgment. The real publish method exercises both paths.
  assert(client.publish("topic", 1, false, "body", 4, true) == 41);
  assert(client.publish("topic", 0, false, "body", 4, false) == 0);
  assert(client.getPublishOk() == 2 && client.getPublishAck() == 0);
  enqueue_result = -1;
  publish_result = -1;
  assert(client.publish("topic", 1, false, "body", 4, true) == -1);
  assert(client.publish("topic", 0, false, "body", 4, false) == -1);
  assert(client.getPublishErr() == 2 && client.getPublishAck() == 0);
  client.setOutboxLimit(10);
  outbox_size = 10;
  assert(client.publish("topic", 0, false, "body", 4, true) == -2);
  assert(client.getOutboxDrops() == 1 && client.getPublishAck() == 0);
  assert(publish_calls == 2 && enqueue_calls == 2);

  // A published event counts even without user callbacks. User callbacks see
  // the updated total and additional callbacks do not multiply the counter.
  dispatch(client, MQTT_EVENT_PUBLISHED, 41);
  assert(client.getPublishAck() == 1);
  int callbacks = 0, extra_callbacks = 0;
  assert(&client.onPublish([&](int msg_id) {
    ++callbacks;
    assert(msg_id == 41 + callbacks);
    assert(client.getPublishAck() == static_cast<unsigned long>(1 + callbacks));
  }) == &client);
  dispatch(client, MQTT_EVENT_PUBLISHED, 42);
  assert(client.getPublishAck() == 2 && callbacks == 1);
  client.onPublish([&](int msg_id) {
    ++extra_callbacks;
    assert(msg_id == 43 && client.getPublishAck() == 3);
  });
  dispatch(client, MQTT_EVENT_PUBLISHED, 43);
  assert(client.getPublishAck() == 3 && callbacks == 2 && extra_callbacks == 1);

  // Connection churn and unrelated events cannot clear or advance the total.
  const int unrelated_events[] = {
      MQTT_EVENT_DISCONNECTED, MQTT_EVENT_CONNECTED, MQTT_EVENT_SUBSCRIBED,
      MQTT_EVENT_UNSUBSCRIBED, MQTT_EVENT_DATA, MQTT_EVENT_ERROR, 12345};
  for (int event_id : unrelated_events) {
    dispatch(client, event_id);
    assert(client.getPublishAck() == 3);
  }
  assert(receive_events == 1 && error_events == 1);
  assert(client.publish("topic", 0, false, "body", 4, true) == -1);
  assert(client.getPublishAck() == 3);
  client.forceStop();
  assert(client.getPublishAck() == 3 && !client.isStarted());
  assert(client.connect() == ESP_OK && client.getPublishAck() == 3);
  assert(client.reconnect() == ESP_OK && client.getPublishAck() == 3);
  client._config_dirty = true;
  assert(client.connect() == ESP_OK && client.getPublishAck() == 3);
  assert(start_calls == 3 && reconnect_calls == 1 && config_calls == 1);
  PsychicMqttClient fresh;
  assert(fresh.getPublishAck() == 0);

  char reply[160];
  MQTTBridge::formatSlotDiagReply(reply, sizeof(reply), 0);
  assert(strcmp(reply, "> mqtt1: bridge not running") == 0);
  assert(strstr(reply, "pub_ack") == nullptr);
  MQTTBridge bridge;
  MQTTPrefs prefs;
  bridge._obs = &prefs;
  bridge._slots[0].client = &client;
  s_mqtt_bridge_instance = &bridge;
  MQTTBridge::formatSlotDiagReply(reply, sizeof(reply), 0);
  assert(strcmp(reply, "> mqtt1: ok, no errors, pub_ack:3") == 0);
  bridge._slots[0].client = &fresh;
  MQTTBridge::formatSlotDiagReply(reply, sizeof(reply), 0);
  assert(strcmp(reply, "> mqtt1: ok, no errors, pub_ack:0") == 0);
  bridge._slots[0].client = &client;
  bridge._slots[0].connect_failures = 2;
  bridge._slots[0].start_failures = 3;
  prefs.mqtt_slot_packet_filter[0] = (1U << 2) | (1U << 3);
  MQTTBridge::formatSlotDiagReply(reply, sizeof(reply), 0);
  assert(strcmp(reply, "> mqtt1: ok, no errors, cf:5, pub_ack:3, filter:2,3") == 0);

  // Without an allocated client there is no client-lifetime acknowledgment
  // total. Early-return replies also retain their established wording.
  bridge._slots[1].connected = false;
  MQTTBridge::formatSlotDiagReply(reply, sizeof(reply), 1);
  assert(strcmp(reply, "> mqtt2: no client, no error info") == 0);
  bridge._slots[1].enabled = false;
  bridge._slots[1].host[0] = '\0';
  MQTTBridge::formatSlotDiagReply(reply, sizeof(reply), 1);
  assert(strcmp(reply, "> mqtt2: not configured") == 0);
  MQTTBridge::formatSlotDiagReply(reply, sizeof(reply), RUNTIME_MQTT_SLOTS);
  assert(strcmp(reply, "> invalid slot") == 0);

  bridge._slots[0] = MQTTBridge::MQTTSlot{};
  bridge._slots[0].client = &client;
  prefs.mqtt_slot_packet_filter[0] = MQTTPacketFilter::kAllPacketTypes;
  client._publish_ack.store(4294967295UL, std::memory_order_relaxed);
  MQTTBridge::formatSlotDiagReply(reply, sizeof(reply), 0);
  const char* healthy_prefix = "> mqtt1: ok, no errors";
  const char* ack_field = ", pub_ack:4294967295";
  assert(strcmp(reply, "> mqtt1: ok, no errors, pub_ack:4294967295") == 0);
  const size_t complete_size = strlen(healthy_prefix) + strlen(ack_field) + 1;
  for (size_t capacity = 1; capacity <= complete_size + 1; ++capacity) {
    unsigned char bounded[168];
    memset(bounded, 0xA5, sizeof(bounded));
    char* output = reinterpret_cast<char*>(bounded);
    MQTTBridge::formatSlotDiagReply(output, capacity, 0);
    assert(memchr(output, '\0', capacity) != nullptr);
    for (size_t i = capacity; i < sizeof(bounded); ++i) assert(bounded[i] == 0xA5);
    if (capacity < complete_size) {
      assert(strstr(output, "pub_") == nullptr);
      assert(strncmp(output, healthy_prefix, capacity - 1) == 0);
    } else {
      assert(strcmp(output, "> mqtt1: ok, no errors, pub_ack:4294967295") == 0);
    }
  }
  unsigned char untouched[16];
  memset(untouched, 0xA5, sizeof(untouched));
  MQTTBridge::formatSlotDiagReply(reinterpret_cast<char*>(untouched), 0, 0);
  for (unsigned char byte : untouched) assert(byte == 0xA5);
  MQTTBridge::formatSlotDiagReply(nullptr, 16, 0);

  // Connection diagnostics and failure totals keep priority in the 160-byte
  // LoRa reply. A long error tail must omit the whole ACK field, not its digits.
  auto& slot = bridge._slots[0];
  slot.connected = false;
  slot.disconnect_count = 4294967295U;
  slot.first_disconnect_time = 1;
  slot.last_error_time = 1;
  slot.last_tls_err = 0x8003;
  slot.last_tls_stack_err = -0x7F00;
  slot.last_sock_errno = -2147483647;
  slot.connect_failures = 4294967295U;
  slot.start_failures = 4294967295U;
  now_ms = 4294967295UL;
  unsigned char long_reply[168];
  memset(long_reply, 0xA5, sizeof(long_reply));
  MQTTBridge::formatSlotDiagReply(reinterpret_cast<char*>(long_reply), 160, 0);
  const char* diagnostic = reinterpret_cast<char*>(long_reply);
  assert(strstr(diagnostic, "unsupported protocol family (0x8003)") != nullptr);
  assert(strstr(diagnostic, "mbedtls:-0x7F00") != nullptr);
  assert(strstr(diagnostic, "sock:-2147483647") != nullptr);
  assert(strstr(diagnostic, "cf:2147483647") != nullptr);
  assert(strstr(diagnostic, "pub_") == nullptr);
  assert(memchr(diagnostic, '\0', 160) != nullptr);
  for (size_t i = 160; i < sizeof(long_reply); ++i) assert(long_reply[i] == 0xA5);
}
'''


class MqttPublishAckTests(unittest.TestCase):
    def test_production_publish_events_and_slot_diagnostics(self):
        compiler = shutil.which("g++")
        if not compiler:
            self.skipTest("g++ is unavailable")
        source = PREAMBLE + "\n".join(method(ADAPTER, signature) for signature in (
            "PsychicMqttClient::PsychicMqttClient()",
            "PsychicMqttClient::~PsychicMqttClient()",
            "PsychicMqttClient &PsychicMqttClient::onPublish(",
            "bool PsychicMqttClient::connected()",
            "esp_err_t PsychicMqttClient::applyConfig()",
            "esp_err_t PsychicMqttClient::connect()",
            "esp_err_t PsychicMqttClient::reconnect()",
            "void PsychicMqttClient::disconnect()",
            "void PsychicMqttClient::forceStop()",
            "int PsychicMqttClient::subscribe(",
            "int PsychicMqttClient::publish(",
            "PsychicMqttClient &PsychicMqttClient::setOutboxLimit(",
            "unsigned long PsychicMqttClient::getOutboxDrops()",
            "unsigned long PsychicMqttClient::getPublishOk()",
            "unsigned long PsychicMqttClient::getPublishErr()",
            "unsigned long PsychicMqttClient::getPublishAck()",
            "void PsychicMqttClient::_onMqttEventStatic(",
            "void PsychicMqttClient::_onMqttEvent(",
            "void PsychicMqttClient::_onConnect(",
            "void PsychicMqttClient::_onDisconnect(",
            "void PsychicMqttClient::_onSubscribe(",
            "void PsychicMqttClient::_onUnsubscribe(",
            "void PsychicMqttClient::_onPublish(",
        )) + "\n".join(method(BRIDGE, signature) for signature in (
            "const char* MQTTBridge::tlsErrorStr(",
            "void MQTTBridge::formatSlotDiagReply(",
        )) + MAIN
        with tempfile.TemporaryDirectory(prefix="meshcore-mqtt-publish-ack-") as directory:
            fixture = Path(directory)
            (fixture / "Arduino.h").write_text(ARDUINO_HEADER, encoding="ascii")
            (fixture / "mqtt_client.h").write_text(MQTT_HEADER, encoding="ascii")
            (fixture / "esp_crt_bundle.h").write_text("#pragma once\n", encoding="ascii")
            test_source = fixture / "publish_ack.cpp"
            test_source.write_text(source, encoding="ascii")
            for idf in (4, 5):
                with self.subTest(idf=idf):
                    executable = fixture / f"idf{idf}"
                    # publish() already compares the SDK's signed outbox size
                    # against size_t; this unrelated warning is outside the seam.
                    command = [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                               "-Wno-sign-compare",
                               "-DARDUINO_ARCH_ESP32=1", f"-DESP_IDF_VERSION_MAJOR={idf}",
                               "-I", str(fixture),
                               "-I", str(ROOT / "lib/PsychicMqttClient/src"),
                               "-I", str(ROOT / "src"), str(test_source), "-o", str(executable)]
                    result = subprocess.run(command, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    result = subprocess.run([str(executable)], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
