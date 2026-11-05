"""Exercise normal and preview-only WiFi join windows in the real portal code."""
import os
from pathlib import Path
import shutil
import subprocess
import unittest

from test_replay_reset_integration import extract_braced
import test_webconfig_station_ownership as ownership_tests
import test_wifi_ota_start as wifi_start

ROOT = Path(__file__).resolve().parents[1]


class WebConfigConnectTimeoutTest(unittest.TestCase):
    compile_and_run = wifi_start.WiFiOtaStartTest.compile_and_run

    def test_default_override_limits_and_rollover(self):
        fixture = r'''
#include <cassert>
#include <cstdint>
#include <initializer_list>
#include <helpers/WebConfigBatch.h>
int main() {
  namespace B = WebConfigBatch;
  static_assert(B::kInitialWiFiConnectTimeoutMs == EXPECT_INITIAL, "initial");
  static_assert(B::kSetupWiFiConnectTimeoutMs == EXPECT_SETUP, "recovery/handoff");
  for (uint32_t duration : {B::kInitialWiFiConnectTimeoutMs, B::kSetupWiFiConnectTimeoutMs}) {
    for (uint32_t start : {0u, 1000u, UINT32_MAX - 30000u}) {
      const uint32_t deadline = B::scheduleAt(start, duration);
      assert(deadline && !B::deadlineReached(start, deadline));
      assert(!B::deadlineReached(deadline - 1u, deadline));
      assert(B::deadlineReached(deadline, deadline));
    }
    // Zero is reserved for an unarmed deadline, including at counter wrap.
    assert(B::scheduleAt(0u - duration, duration) == 1u);
  }
  const uint32_t now = UINT32_MAX - 3000u;
  assert(B::setupWiFiConnectDeadline(now) == B::scheduleAt(now, EXPECT_SETUP));
  // Extending a join must not extend the batch receipt, reboot, or AP window.
  static_assert(B::kRebootFallbackMs == 30000 && B::kRebootConfirmMs == 3000, "reboot");
  static_assert(B::kReauthReceiptMs == 120000, "receipt");
  static_assert(B::kFullSetupApWindowMs == 1800000, "setup window");
}
'''
        for default, override, initial, setup in ((None, None, 15000, 20000),
                                                   (None, 5000, 5000, 5000),
                                                   (30000, None, 30000, 30000),
                                                   (30000, 60000, 60000, 60000),
                                                   (None, 60000, 60000, 60000),
                                                   (None, 120000, 120000, 120000)):
            with self.subTest(default=default, override=override):
                flags = [f"-DEXPECT_INITIAL={initial}UL", f"-DEXPECT_SETUP={setup}UL"]
                if default is not None:
                    flags.append(f"-DWEBCONFIG_WIFI_DEFAULT_CONNECT_TIMEOUT_MS={default}UL")
                if override is not None:
                    flags.append(f"-DWEBCONFIG_WIFI_CONNECT_TIMEOUT_MS={override}UL")
                self.compile_and_run(fixture, *flags)

    def test_invalid_override_is_a_build_error(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "A host C++ compiler is required")
        for value in ("0", "4999", "120001", "-1"):
            with self.subTest(override=value):
                result = subprocess.run([
                    compiler, "-std=c++17", "-x", "c++", "-fsyntax-only",
                    "-I", str(ROOT / "src"), f"-DWEBCONFIG_WIFI_CONNECT_TIMEOUT_MS={value}", "-",
                ], input='#include <helpers/WebConfigBatch.h>\n',
                    text=True, capture_output=True, timeout=30)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("WebConfig WiFi connect timeout must be", result.stderr)

    def test_production_initial_recovery_and_ownership_deadlines(self):
        prefix = ownership_tests.WebConfigStationOwnershipTest().station_fixture().split("int main() {", 1)[0]
        fixture = prefix + r'''
int main() {
  namespace B = WebConfigBatch;
  const uint8_t key[32] = {};
  char reply[160] = {};
  for (uint32_t start : {1000u, UINT32_MAX - 30000u}) {
    WiFi = FakeWiFi{};
    clock_ms = start;
    WebConfigServer portal(nullptr, nullptr, true, key, "test", "test", "repeater", "W12", true);
    portal.updateWiFiOwnership(true);
    assert(portal.startAutoMode(reply));
    const uint32_t first = B::scheduleAt(start, B::kInitialWiFiConnectTimeoutMs);
    assert(portal._connect_deadline == first && WiFi.begins == 1);
    clock_ms = first - 1u;
    portal.tick(clock_ms);
    assert(portal._mode == WebConfigServer::MODE_CONNECTING && !portal.setup_starts);
    clock_ms = first;
    portal.tick(clock_ms);
    assert(portal._mode == WebConfigServer::MODE_SETUP && portal.setup_starts == 1);
    assert(portal._retry_saved_wifi_in_setup && WiFi.begins == 1);

    // Keep the five-minute retry cadence; only the ensuing attempt is longer.
    clock_ms = start + WiFiReconnectPolicy::kRetryIntervalMs - 1u;
    portal.tick(clock_ms);
    assert(!portal._setup_reconnect_in_progress && WiFi.begins == 1);
    ++clock_ms;
    portal.tick(clock_ms);
    assert(portal._setup_reconnect_in_progress && WiFi.begins == 2);
    const uint32_t retry = B::setupWiFiConnectDeadline(clock_ms);
    assert(portal._setup_reconnect_deadline == retry);
    clock_ms = retry - 1u;
    portal.tick(clock_ms);
    assert(portal._setup_reconnect_in_progress);
    clock_ms = retry;
    portal.tick(clock_ms);
    assert(!portal._setup_reconnect_in_progress && !portal._setup_reconnect_deadline);
    assert(portal._mode == WebConfigServer::MODE_SETUP);

    // A failed local acquisition uses the same initial window, including wrap.
    WiFi = FakeWiFi{};
    clock_ms = start;
    WebConfigServer acquiring(nullptr, nullptr, true, key, "test", "test", "repeater", "W12", true);
    acquiring.updateWiFiOwnership(true);
    acquiring._mode = WebConfigServer::MODE_CONNECTING;
    mesh::wifi::fail_protocol = true;
    acquiring.tick(clock_ms);
    mesh::wifi::fail_protocol = false;
    assert(acquiring._connect_deadline == first && !WiFi.begins);

    // A live external station owner gets no competing portal timeout.
    WebConfigServer external(nullptr, nullptr, false, key, "test", "test", "repeater", "W12", true);
    external.updateWiFiOwnership(false);
    assert(external.startAutoMode(reply) && !external._connect_deadline);
    clock_ms = start + 120001u;
    external.tick(clock_ms);
    assert(external._mode == WebConfigServer::MODE_CONNECTING && !external.setup_starts);
    assert(!WiFi.begins);
  }
}
'''
        for flags in ((), ("-DWEBCONFIG_WIFI_CONNECT_TIMEOUT_MS=60000UL",)):
            with self.subTest(flags=flags):
                self.compile_and_run(fixture, "-DWITH_MQTT_BRIDGE=1", *flags)

    def test_credential_save_handoff_uses_the_shared_setup_window(self):
        source = (ROOT / "src/helpers/esp32/WebConfigServer.cpp").read_text()
        handoff = extract_braced(source, "if (WebConfigBatch::shouldStartSetupWiFiHandoff(")
        self.assertRegex(handoff, r"_setup_wifi_handoff_deadline\s*=\s*"
                                  r"WebConfigBatch::setupWiFiConnectDeadline\(now\)")
        service = extract_braced(source, "void WebConfigServer::serviceSetupWiFiHandoff(")
        self.assertIn("WebConfigBatch::deadlineReached(now, _setup_wifi_handoff_deadline)", service)


if __name__ == "__main__":
    unittest.main()
