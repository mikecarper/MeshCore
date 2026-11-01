#!/usr/bin/env python3
"""Exercise the production WiFi reason wrapper against the ESP-IDF enum values."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

# esp_wifi_types.h, wifi_err_reason_t in the ESP-IDF 4.4 SDK used by W12.
# Keep these independent of MQTTErrorLabels so a wrong numeric key is caught.
SDK_REASONS = {
    "AUTH_EXPIRE": 2, "ASSOC_EXPIRE": 4, "ASSOC_LEAVE": 8,
    "BSS_TRANSITION_DISASSOC": 12, "4WAY_HANDSHAKE_TIMEOUT": 15,
    "GROUP_CIPHER_INVALID": 18, "CIPHER_SUITE_REJECTED": 24,
    "MISSING_ACKS": 34, "TIMEOUT": 39, "INVALID_PMKID": 49,
    "BEACON_TIMEOUT": 200, "NO_AP_FOUND": 201, "AUTH_FAIL": 202,
    "ASSOC_FAIL": 203, "HANDSHAKE_TIMEOUT": 204, "CONNECTION_FAIL": 205,
    "AP_TSF_RESET": 206, "ROAMING": 207, "SA_QUERY_TIMEOUT": 209,
}


def production_wrapper(source):
    start = source.index("const char* MQTTBridge::wifiReasonStr(")
    opening = source.index("{", start)
    depth, pos = 1, opening + 1
    while depth:
        depth += (source[pos] == "{") - (source[pos] == "}")
        pos += 1
    return source[start:pos]


class MqttWifiReasonLabelsTests(unittest.TestCase):
    def test_production_wrapper_and_sdk_contract(self):
        compiler = shutil.which("g++")
        if not compiler:
            self.skipTest("g++ is unavailable")
        source = (ROOT / "src/helpers/bridges/MQTTBridge.cpp").read_text()
        sdk_asserts = "\n".join(
            line for line in source.splitlines()
            if line.startswith("static_assert(MQTTErrorLabels::kWifi")
        )
        self.assertTrue(sdk_asserts, "production mapping must check the SDK enum")
        enum = "enum wifi_err_reason_t {\n" + ",\n".join(
            f"WIFI_REASON_{name} = {value}" for name, value in SDK_REASONS.items()
        ) + "\n};\n"
        program = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include "helpers/bridges/MQTTErrorLabels.h"
struct MQTTBridge { static const char* wifiReasonStr(uint8_t); };
'''
        program += enum + sdk_asserts + production_wrapper(source) + r'''
int main() {
  // Check the public production wrapper, rather than the lookup table alone.
  assert(strcmp(MQTTBridge::wifiReasonStr(201), "SSID not found") == 0);
  assert(strcmp(MQTTBridge::wifiReasonStr(202), "auth failed (check password)") == 0);
  assert(strcmp(MQTTBridge::wifiReasonStr(203), "association failed") == 0);
  assert(strcmp(MQTTBridge::wifiReasonStr(204), "handshake timeout") == 0);
  assert(strcmp(MQTTBridge::wifiReasonStr(39), "802.11 timeout") == 0);
  assert(strcmp(MQTTBridge::wifiReasonStr(34), "AP saw no acks") == 0);
  assert(strcmp(MQTTBridge::wifiReasonStr(24), "cipher suite rejected") == 0);
  assert(strcmp(MQTTBridge::wifiReasonStr(209), "SA query timeout (PMF)") == 0);
  const int unknown[] = {0, 40, 61, 63, 88, 168, 255};
  for (int reason : unknown) {
    assert(MQTTBridge::wifiReasonStr(reason) == nullptr);
  }
  for (int reason = 0; reason <= 255; ++reason) {
    const char* label = MQTTBridge::wifiReasonStr(reason);
    if (!label) continue;
    assert(strlen(label) <= 30);
    struct { char text[64]; unsigned char guard[8]; } reply;
    memset(&reply, 0xA5, sizeof(reply));
    int length = snprintf(reply.text, sizeof(reply.text),
                          "> disconnected: %s (reason: %d)", label, reason);
    assert(length > 0 && static_cast<size_t>(length) < sizeof(reply.text));
    for (unsigned char byte : reply.guard) assert(byte == 0xA5);
  }
}
'''
        with tempfile.TemporaryDirectory(prefix="meshcore-wifi-reasons-") as directory:
            test_source = Path(directory) / "wifi_reasons.cpp"
            test_source.write_text(program, encoding="ascii")
            executable = Path(directory) / "wifi_reasons"
            subprocess.run(
                [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                 "-I", str(ROOT / "src"), str(test_source), "-o", str(executable)],
                check=True, capture_output=True, text=True,
            )
            subprocess.run([str(executable)], check=True, capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main()
