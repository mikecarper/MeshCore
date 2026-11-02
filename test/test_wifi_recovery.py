"""Exercise bounded station recovery and complete authentication windows."""

from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <string>
#include <helpers/WiFiReconnectPolicy.h>
#include <helpers/MQTTConnectionPolicy.h>

#if defined(MESH_WIFI_RECOVERY) && MESH_WIFI_RECOVERY
constexpr bool recovery = true;
#else
constexpr bool recovery = false;
#endif

void trackerTests() {
  using WiFiReconnectPolicy::Tracker;
  const uint32_t first_interval = recovery ? 60000U : 300000U;
  for (uint32_t start : {1000000U, UINT32_MAX - 1000U}) {
    Tracker tracker;
    assert(!tracker.retryDue(start + 300000U));
    tracker.noteAttempt(start); // Initial association never spends an early retry.
    assert(tracker.isTracking());
    tracker.noteDisconnected(start + first_interval - 1U);
    assert(!tracker.retryDue(start + first_interval - 1U));
    assert(tracker.retryDue(start + first_interval));
    // Observing a due retry while an AP/OTA owner defers it consumes nothing.
    for (unsigned i = 0; i < 100; ++i) {
      assert(tracker.retryDue(start + first_interval + i));
      tracker.noteDisconnected(start + first_interval + i);
    }
    tracker.noteAttempt(start + first_interval + 100U);
    assert(!tracker.retryDue(start + 2U * first_interval + 99U));
    assert(tracker.retryDue(start + 2U * first_interval + 100U));
    tracker.noteAttempt(start + 2U * first_interval + 100U);
    const uint32_t last = start + 2U * first_interval + 100U;
    tracker.noteDisconnected(last + 60000U);
    assert(!tracker.retryDue(last + 299999U));
    assert(tracker.retryDue(last + 300000U)); // The finite burst ends here.
    tracker.noteAttempt(last + 300000U);
    assert(!tracker.retryDue(last + 599999U));
    assert(tracker.retryDue(last + 600000U));
    tracker.noteConnected();
    assert(!tracker.isTracking() && !tracker.retryDue(last + 900000U));
    tracker.noteDisconnected(last + 900000U);
    assert(!tracker.retryDue(last + 900000U + first_interval - 1U));
    assert(tracker.retryDue(last + 900000U + first_interval));
  }
  // Common caller order: observe disconnected, then record the initial join.
  Tracker initial;
  initial.noteDisconnected(1000U);
  initial.noteAttempt(2000U);
  assert(!initial.retryDue(2000U + first_interval - 1U));
  assert(initial.retryDue(2000U + first_interval));
  initial.noteAttempt(2000U + first_interval);
  assert(initial.retryDue(2000U + 2U * first_interval));
  // A manually refreshed attempt must retain a full quiet period.
  Tracker quiet;
  quiet.noteAttempt(1000U);
  quiet.noteAttempt(1000U + first_interval - 1U);
  assert(!quiet.retryDue(1000U + 2U * first_interval - 2U));
  assert(quiet.retryDue(1000U + 2U * first_interval - 1U));
}

void mqttTests() {
  namespace Policy = MQTTConnectionPolicy;
  const uint32_t ordinary[] = {15000U, 30000U, 60000U, 120000U, 300000U};
  for (uint32_t start : {1000000U, UINT32_MAX - 1000U}) {
    for (uint8_t attempt = 0; attempt < 8; ++attempt) {
      const auto backoff = ordinary[attempt < 5 ? attempt : 4];
      assert(Policy::wifiReconnectBackoffMs(attempt) == backoff);
      const uint32_t guard = recovery && backoff < 60000U ? 60000U : backoff;
      const uint32_t old_outage = start - 300000U;
      assert(!Policy::wifiReconnectDue(start + guard - 1U, old_outage, start, attempt));
      assert(Policy::wifiReconnectDue(start + guard, old_outage, start, attempt));
      assert(!Policy::wifiReconnectDue(start + guard, start + 1U, start, attempt));
      // A new actual join gets its own window even late in a long outage.
      const uint32_t next_attempt = start + guard;
      assert(!Policy::wifiReconnectDue(next_attempt + guard - 1U,
                                      old_outage, next_attempt, attempt));
      assert(Policy::wifiReconnectDue(next_attempt + guard,
                                     old_outage, next_attempt, attempt));
    }
  }
}

int main(int argc, char** argv) {
  assert(argc == 2);
  if (std::string(argv[1]) == "tracker") trackerTests();
  else if (std::string(argv[1]) == "mqtt") mqttTests();
  else assert(false);
}
'''


class WiFiRecoveryTests(unittest.TestCase):
    def run_fixture(self, scenario):
        compiler = os.environ.get("CXX") or shutil.which("g++")
        self.assertIsNotNone(compiler)
        for define in (None, 0, 1):
            with self.subTest(recovery=define), tempfile.TemporaryDirectory(prefix="wifi-recovery-") as directory:
                folder = Path(directory)
                (folder / "test.cpp").write_text(HARNESS, encoding="ascii")
                flags = [] if define is None else [f"-DMESH_WIFI_RECOVERY={define}"]
                sanitizer = [] if os.name == "nt" else [
                    "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"
                ]
                built = subprocess.run([
                    compiler, "-std=c++11", "-O1", "-Wall", "-Wextra", "-Werror", *sanitizer,
                    *flags, "-I", str(ROOT / "src"), str(folder / "test.cpp"), "-o", str(folder / "test"),
                ], capture_output=True, text=True, timeout=60)
                self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                ran = subprocess.run([str(folder / "test"), scenario], capture_output=True, text=True, timeout=15)
                self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_two_early_retries_then_normal_cadence_and_initial_quiet_window(self):
        self.run_fixture("tracker")

    def test_mqtt_attempt_and_outage_guards_preserve_full_authentication_time(self):
        self.run_fixture("mqtt")


if __name__ == "__main__":
    unittest.main()
