#!/usr/bin/env python3
"""Exercise the real Companion manual sleep path with diagnostics on and off."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CompanionPowerDiagnosticsTests(unittest.TestCase):
    def test_native_usb_wait_counts_only_the_sdk_call_and_preserves_fallback(self):
        source = (ROOT / "examples/companion_radio/main.cpp").read_text(encoding="ascii")
        start = source.index("    bool sleep_failed = false;")
        block = source[start:source.index("#elif COMPANION_IDF_PM_AVAILABLE", start)]
        code = r'''
#include <cassert>
#include <initializer_list>
#include <helpers/PowerSavingDiagnostics.h>
using mesh::powerdiag::Reason;
using esp_err_t = int;
constexpr int ESP_OK = 0;
static uint32_t clock_us = 100000;
static uint32_t millis() { return clock_us / 1000; }
static uint32_t micros() { return clock_us; }
static bool ready = true;
static int timer_error = 0, sleep_error = 0;
static unsigned ready_calls = 0, timer_calls = 0, wait_calls = 0, delay_calls = 0;
static bool companionNativeUsbLightSleepReady() { ++ready_calls; return ready; }
static int esp_sleep_enable_timer_wakeup(uint64_t us) {
  assert(us == 10000); ++timer_calls; return timer_error;
}
static int esp_light_sleep_start() { ++wait_calls; clock_us += 500; return sleep_error; }
static unsigned pdMS_TO_TICKS(unsigned ms) { return ms; }
static void vTaskDelay(unsigned ms) { assert(ms == 10); ++delay_calls; clock_us += 10000; }
static void attempt() {
  const uint32_t idle_slice_ms = 10;
@BLOCK@
}
int main() {
  using namespace mesh::powerdiag;
  for (bool diagnostics : {false, true}) {
    for (unsigned mode = 0; mode != 4; ++mode) {
      clock_us = 100000;
      setEnabled(diagnostics, millis()); reset(millis());
      decision(0, millis());
      ready = mode != 1; timer_error = mode == 2 ? -2 : 0; sleep_error = mode == 3 ? -3 : 0;
      ready_calls = timer_calls = wait_calls = delay_calls = 0;
      attempt();
      assert(ready_calls == 1);
      assert(timer_calls == (mode != 1));
      assert(wait_calls == (mode == 0 || mode == 3));
      assert(delay_calls == (mode != 0));
      const auto value = snapshot(millis());
      if (!diagnostics) {
        assert(value.calls == 0 && value.errors == 0 && !value.observed);
      } else {
        assert(value.calls == wait_calls && value.returns == wait_calls);
        assert(value.call_us == wait_calls * 500); // Never include fallback delay.
        assert(value.board_vetoes == (mode == 1));
        assert(value.errors == (mode == 2 || mode == 3));
        assert(value.current == (mode ? mask(Reason::Board) : 0));
        if (mode >= 2) assert(value.detail == -static_cast<int>(mode));
      }
    }
  }
}
'''.replace("@BLOCK@", block)
        with tempfile.TemporaryDirectory(prefix="meshcore-companion-power-") as directory:
            cpp = Path(directory) / "test.cpp"
            binary = Path(directory) / "test"
            cpp.write_text(code, encoding="ascii")
            built = subprocess.run([
                os.environ.get("CXX", "c++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
                "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                "-I", str(ROOT / "src"), str(cpp), "-o", str(binary),
            ], capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_companion_routes_bounded_diagnostics_before_saved_settings(self):
        source = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text(encoding="ascii")
        start = source.index("bool MyMesh::handleLocalControlCommand(")
        body = source[start:source.index('if (strcmp(command, "get powersaving")', start)]
        self.assertIn("mesh::powerdiag::handleCommand(command, reply, reply_size, millis())", body)
        self.assertNotIn("savePrefs()", body[:body.index("mesh::powerdiag::handleCommand")])
        self.assertIn("return pendingWorkReason() != 0;", source)


if __name__ == "__main__":
    unittest.main()
