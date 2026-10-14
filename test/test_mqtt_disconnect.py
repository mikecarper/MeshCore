#!/usr/bin/env python3
"""Run the real MQTT wrapper stop and outage handoff under SDK failures."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_mqtt_publish_ack import ARDUINO_HEADER, MQTT_HEADER
from test_mqtt_transport_results import method
from test_replay_reset_integration import extract_braced


ROOT = Path(__file__).resolve().parents[1]
ADAPTER = "lib/PsychicMqttClient/src/PsychicMqttClient.cpp"
BRIDGE = "src/helpers/bridges/MQTTBridge.cpp"

HARNESS = r'''
#include <atomic>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <helpers/WiFiNetworkPowerPolicy.h>
#include <helpers/MQTTConnectionPolicy.h>
#include <helpers/MQTTTransportHealth.h>
#define private public
#include "PsychicMqttClient.h"
#undef private

template<typename... Args> void log_stub(Args&&...) {}
static const char* TAG = "test";
#define ESP_LOGI(...) log_stub(__VA_ARGS__)
#define ESP_LOGW(...) log_stub(__VA_ARGS__)
#define ESP_ERROR_CHECK_WITHOUT_ABORT(value) ((void)(value))
#define MQTT_DEBUG_PRINTLN(...) ((void)0)
#define pdMS_TO_TICKS(ms) ((ms) / portTICK_PERIOD_MS)
static uint32_t now_ms = 0, elapsed_ms = 0, tick_ms = 1;
static uint32_t callback_after_ms = UINT32_MAX;
static int disconnect_result = ESP_OK, stop_result = ESP_OK;
static int disconnect_calls = 0, stop_calls = 0, sleeps = 0;
static PsychicMqttClient* active_client = nullptr;
static std::function<void()> on_delay;
unsigned long millis() { return now_ms; }
const char* esp_err_to_name(int) { return "mock"; }

static void deliverDisconnect() {
  if (!active_client || elapsed_ms < callback_after_ms) return;
  active_client->_connected = false;
  active_client->_stopMqttClient = true;
  callback_after_ms = UINT32_MAX;
}
void vTaskDelay(unsigned ticks) {
  assert(ticks != 0);
  ++sleeps;
  now_ms += ticks * tick_ms;
  elapsed_ms += ticks * tick_ms;
  deliverDisconnect();
  if (on_delay) on_delay();
  assert(elapsed_ms <= 2000U + tick_ms);
}
int esp_mqtt_client_disconnect(void*) {
  ++disconnect_calls;
  if (disconnect_result == ESP_OK) deliverDisconnect();
  return disconnect_result;
}
int esp_mqtt_client_stop(void*) { ++stop_calls; return stop_result; }
// Destruction is outside this test's stop/handoff seam; failed SDK resources
// remain owned by the surrounding bridge lifecycle in the production code.
PsychicMqttClient::~PsychicMqttClient() {}

@ADAPTER_METHODS@

constexpr int RUNTIME_MQTT_SLOTS = 1, WIFI_MODE_NULL = 0;
constexpr int WIFI_MODE_AP = 2, WIFI_STA = 1, WIFI_OFF = 0;
using wifi_mode_t = int;
static int wifi_mode = WIFI_STA, wifi_stops = 0;
int esp_wifi_get_mode(int* mode) { *mode = wifi_mode; return ESP_OK; }
bool mqttStationWiFiMutationAllowed() { return !(wifi_mode & WIFI_MODE_AP); }
namespace mesh { namespace wifi {
bool espNowChannelConstrained() { return false; }
int restoreEspNowChannel() { assert(false); return ESP_ERR_INVALID_STATE; }
} }
struct {
  void setAutoReconnect(bool enabled) { assert(!enabled); }
  void disconnect(bool stop, bool erase) { assert(!stop && !erase); }
  int getMode() const { return wifi_mode; }
  void mode(int mode) {
    assert(mode == WIFI_OFF && mesh::wifi::networkPower().backgroundSuspended());
    wifi_mode = mode;
    ++wifi_stops;
  }
} WiFi;

struct MQTTBridge {
  struct Slot {
    PsychicMqttClient* client = nullptr;
    bool enabled = true, connected = false, circuit_breaker_tripped = false;
    uint32_t connected_at_ms = 0, last_reconnect_attempt = 0;
    uint32_t current_outage_started_ms = 0;
    uint8_t reconnect_backoff = 0;
    bool initial_connect_done = true;
    char* auth_token = nullptr;
    char broker_uri[32] = "mqtt://original";
    uint32_t token_expires_at = 1, last_token_renewal = 1;
    uint32_t last_log_time = 1, last_deferred_log_ms = 1;
    uint8_t max_backoff_failures = 0;
    MQTTTransportHealth::State transport_health;
  } _slots[RUNTIME_MQTT_SLOTS];
  using MQTTSlot = Slot;
  mesh::wifi::NetworkPowerPolicy _network_power_policy;
  std::atomic<bool> _network_suspended{false}, _fresh_ntp_this_boot{false};
  bool _slot_attempt_pending[RUNTIME_MQTT_SLOTS] = {};
  std::atomic<bool> _slot_transport_fault_pending[RUNTIME_MQTT_SLOTS] = {};
  std::atomic<bool> _slot_health_reset_pending[RUNTIME_MQTT_SLOTS] = {};
  bool _status_publish_pending[RUNTIME_MQTT_SLOTS] = {};
  bool _slot_force_jwt_mint[RUNTIME_MQTT_SLOTS] = {};
  uint32_t _network_publish_acks[RUNTIME_MQTT_SLOTS] = {};
  bool _network_espnow_restore_pending = false;
  uint32_t _network_espnow_restore_at = 0;
  bool _ntp_force_requested = false, _ntp_diag_requested = false;
  bool _ntp_sync_pending = false, _manage_wifi = true, _wifi_status_initialized = true;
  uint32_t _last_wifi_check = 0, _last_slot_reconnect_ms = 0;
  uint32_t _ntp_refresh_retry_at = 0;
  std::atomic<bool> _stop_requested{false}, _stop_acked{false};
  int destroyed = 0, queues = 0, ntp_starts = 0, slot_starts = 0;
  struct Board { bool isOTAUpdateRunning() const { return false; } };
  Board* _board = nullptr;
  void cancelNtpRefresh() {}
  void updateCachedConnectionStatus() {}
  void updateSlotTransportDiag(int, uint32_t) {}
  void initializeWiFiInTask() {}
  void processPacketQueue() { ++queues; }
  void teardownSlot(int index, bool force = false);
  void destroySlotClients() {
    assert(!_stop_acked);
    for (auto& slot : _slots) {
      assert(!slot.client || !slot.client->isStarted());
      slot.client = nullptr;
    }
    ++destroyed;
  }
  bool serviceNetworkPower(uint32_t now);
  void serviceEspNowChannelRestore(uint32_t now);
  void workerStop() {
    while (true) {
@TASK_STOP@
    }
  }
  void workerOutageOnce() {
    for (unsigned iteration = 0; iteration < 1; ++iteration) {
      const uint32_t now = now_ms;
@OUTAGE_GUARD@
      ++ntp_starts;
      ++slot_starts;
    }
  }
};

@BRIDGE_METHOD@

int main(int argc, char** argv) {
  assert(argc == 4);
  const int scenario = std::atoi(argv[1]);
  tick_ms = static_cast<uint32_t>(std::atoi(argv[2]));
  if (std::atoi(argv[3])) now_ms = UINT32_MAX - 511U;
  PsychicMqttClient client;
  active_client = &client;
  client._client = reinterpret_cast<void*>(1);
  client._started = true;
  client._connected = true;
  if (scenario == 0) {
    client.disconnect();  // Lost event: SDK stop is sufficient proof.
    assert(elapsed_ms >= 1000U && elapsed_ms < 1000U + tick_ms);
    assert(stop_calls == 1 && !client.isStarted() && !client.connected());
    const int stopped = stop_calls;
    client.disconnect();
    assert(stop_calls == stopped);  // already stopped is idempotent
  } else if (scenario == 1) {
    stop_result = ESP_ERR_INVALID_STATE;
    auto& control = mesh::wifi::networkPower();
    control.setManaged(true);
    MQTTBridge bridge;
    bridge._slots[0].client = &client;
    const uint32_t start = now_ms;
    assert(bridge.serviceNetworkPower(start));
    now_ms = start + 300000U;
    assert(!bridge.serviceNetworkPower(now_ms));
    assert(elapsed_ms >= 1000U && stop_calls == 1 && client.isStarted());
    assert(!bridge._network_suspended && !control.backgroundSuspended());
    assert(wifi_stops == 0 && wifi_mode == WIFI_STA);
    // An owning-task retry can now complete teardown without another wait if
    // the disconnect request itself fails. Only the successful SDK stop grants
    // the bridge permission to turn the station off.
    disconnect_result = ESP_ERR_INVALID_STATE;
    stop_result = ESP_OK;
    assert(!bridge.serviceNetworkPower(now_ms));
    assert(!client.isStarted() && control.backgroundSuspended() && wifi_stops == 1);
  } else if (scenario == 2) {
    disconnect_result = ESP_ERR_INVALID_STATE;
    client.disconnect();
    assert(sleeps == 0 && disconnect_calls == 1 && stop_calls == 1);
    assert(!client.isStarted() && !client.connected());
  } else if (scenario == 3) {
    callback_after_ms = 0;
    stop_result = ESP_ERR_INVALID_STATE;
    client.disconnect();
    assert(sleeps == 0 && stop_calls == 1 && client.isStarted());
    assert(!client.connected());  // Actual event state survives failed stop.
  } else if (scenario == 4) {
    callback_after_ms = 50U;
    client.disconnect();
    assert(elapsed_ms >= 50U && elapsed_ms < 50U + tick_ms);
    assert(stop_calls == 1 && !client.isStarted() && !client.connected());
  } else if (scenario == 5) {
    client._started = false;
    client.disconnect();
    client._client = nullptr;
    client.disconnect();
    assert(disconnect_calls == 0 && stop_calls == 0 && sleeps == 0);
  } else if (scenario == 6) {
    client._connected = false;
    stop_result = ESP_ERR_INVALID_STATE;
    client.disconnect();
    assert(disconnect_calls == 0 && stop_calls == 1 && sleeps == 0);
    assert(client.isStarted());
  } else if (scenario == 7) {
    disconnect_result = ESP_ERR_INVALID_STATE;
    stop_result = ESP_ERR_INVALID_STATE;
    MQTTBridge bridge;
    bridge._slots[0].client = &client;
    bridge._stop_requested = true;
    on_delay = [&]() {
      assert(!bridge._stop_acked && bridge.destroyed == 0);
      assert(bridge._slots[0].client == &client);
      if (elapsed_ms >= 150U) stop_result = ESP_OK;
    };
    bridge.workerStop();
    assert(stop_calls == 2 && !client.isStarted());
    assert(bridge._stop_acked && bridge.destroyed == 1);
    assert(bridge._slots[0].client == nullptr);
    on_delay = nullptr;
  } else if (scenario == 8) {
    disconnect_result = ESP_ERR_INVALID_STATE;
    stop_result = ESP_ERR_INVALID_STATE;
    MQTTBridge bridge;
    char token[] = "protected-token";
    bridge._slots[0].client = &client;
    bridge._slots[0].auth_token = token;
    bridge.teardownSlot(0);
    assert(client.isStarted() && strcmp(token, "protected-token") == 0);
    assert(bridge._slots[0].initial_connect_done);
    assert(strcmp(bridge._slots[0].broker_uri, "mqtt://original") == 0);
  } else if (scenario == 9) {
    disconnect_result = ESP_ERR_INVALID_STATE;
    stop_result = ESP_ERR_INVALID_STATE;
    auto& control = mesh::wifi::networkPower();
    control.setManaged(true);
    MQTTBridge bridge;
    bridge._slots[0].client = &client;
    const uint32_t start = now_ms;
    assert(bridge.serviceNetworkPower(start));
    now_ms = start + 300000U;
    bridge.workerOutageOnce();
    assert(client.isStarted() && !bridge._network_suspended);
    assert(!control.backgroundSuspended() && wifi_stops == 0);
    assert(bridge.queues == 1 && bridge.ntp_starts == 0 && bridge.slot_starts == 0);
  } else assert(false);
  printf("PASS: %d\n", scenario);
}
'''


class MqttDisconnectTests(unittest.TestCase):
    def test_real_wrapper_and_wifi_handoff_across_sdk_failures_ticks_and_wrap(self):
        compiler = os.environ.get("CXX") or shutil.which("g++")
        self.assertIsNotNone(compiler)
        adapter = "\n".join(method(ADAPTER, signature) for signature in (
            "PsychicMqttClient::PsychicMqttClient()",
            "bool PsychicMqttClient::connected()",
            "void PsychicMqttClient::disconnect()",
            "void PsychicMqttClient::forceStop()",
            "unsigned long PsychicMqttClient::getPublishAck()",
        ))
        task = method(BRIDGE, "void MQTTBridge::mqttTaskLoop()")
        source = (HARNESS.replace("@ADAPTER_METHODS@", adapter)
                  .replace("@TASK_STOP@", extract_braced(task, "    if (_stop_requested)"))
                  .replace("@OUTAGE_GUARD@", extract_braced(task, "    if (!serviceNetworkPower("))
                  .replace("@BRIDGE_METHOD@", "\n".join(method(BRIDGE, signature) for signature in (
                      "bool MQTTBridge::serviceNetworkPower(",
                      "void MQTTBridge::serviceEspNowChannelRestore(",
                      "void MQTTBridge::teardownSlot(",
                  ))))
        with tempfile.TemporaryDirectory(prefix="meshcore-mqtt-disconnect-") as temporary:
            directory = Path(temporary)
            (directory / "Arduino.h").write_text(ARDUINO_HEADER, encoding="ascii")
            (directory / "mqtt_client.h").write_text(MQTT_HEADER, encoding="ascii")
            (directory / "esp_crt_bundle.h").write_text("#pragma once\n", encoding="ascii")
            cpp = directory / "disconnect.cpp"
            cpp.write_text(source, encoding="ascii")
            for idf in (4, 5):
                for tick in (1, 10, 50):
                    with self.subTest(idf=idf, tick=tick):
                        binary = directory / f"disconnect-{idf}-{tick}"
                        arduino = ARDUINO_HEADER.replace(
                            "constexpr unsigned portTICK_PERIOD_MS = 1;",
                            f"constexpr unsigned portTICK_PERIOD_MS = {tick};")
                        (directory / "Arduino.h").write_text(arduino, encoding="ascii")
                        built = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra",
                            "-Werror", "-DARDUINO_ARCH_ESP32=1", "-DESP_PLATFORM=1",
                            f"-DESP_IDF_VERSION_MAJOR={idf}",
                            "-I", str(directory), "-I", str(ROOT / "lib/PsychicMqttClient/src"),
                            "-I", str(ROOT / "src"), str(cpp), "-o", str(binary)],
                            capture_output=True, text=True, timeout=60)
                        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                        for scenario in range(10):
                            for wrap in (0, 1):
                                with self.subTest(scenario=scenario, wrap=wrap):
                                    checked = subprocess.run([str(binary), str(scenario), str(tick), str(wrap)],
                                        capture_output=True, text=True, timeout=5)
                                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)


if __name__ == "__main__":
    unittest.main()
