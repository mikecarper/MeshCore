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

ROOT = Path(__file__).resolve().parents[1]

PREAMBLE = r'''
#include <cassert>
#include <cstddef>
#include "helpers/MQTTConnectionPolicy.h"
struct esp_mqtt_client_config_t {
#if ESP_IDF_VERSION_MAJOR == 5
  struct { int keepalive = 0, message_retransmit_timeout = 0; } session;
  struct { int timeout_ms = 0; } network;
  struct { int size = 0, out_size = 0; } buffer;
#else
  int keepalive = 0, message_retransmit_timeout = 0;
  int network_timeout_ms = 0, buffer_size = 0;
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
    client._config_dirty = false;
    bridge.optimizeMqttClientConfig(&client, needs_large);
    assert(client._config_dirty);
#if ESP_IDF_VERSION_MAJOR == 5
    const int timeout = client._mqtt_cfg.network.timeout_ms;
    const int buffer_size = client._mqtt_cfg.buffer.size;
    const int keepalive = client._mqtt_cfg.session.keepalive;
    assert(client._mqtt_cfg.buffer.out_size == buffer_size);
    assert(client._mqtt_cfg.session.message_retransmit_timeout == 15000);
#else
    const int timeout = client._mqtt_cfg.network_timeout_ms;
    const int buffer_size = client._mqtt_cfg.buffer_size;
    const int keepalive = client._mqtt_cfg.keepalive;
    assert(client._mqtt_cfg.message_retransmit_timeout == 15000);
#endif
    assert(timeout == 10000);
    assert(timeout == MQTTConnectionPolicy::kNetworkTimeoutMs);
#ifdef BOARD_HAS_PSRAM
    assert(buffer_size == 896 && keepalive == 45);
    assert(client._outbox_limit == 16384);
#else
    assert(buffer_size == (needs_large ? 896 : 512) && keepalive == 75);
    assert(client._outbox_limit == 8192);
#endif
  }
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
