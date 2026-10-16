#!/usr/bin/env python3
"""Run real nRF52 shutdown paths with queued Bluefruit LED callbacks on the host.

No radio/current measurements are implied. The Bluefruit mock models the SDK's
NULL-safe autoConnLed(false), deferred timer stop, and unguarded blinky callback.
"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "src/helpers/NRF52Board.cpp"
FIXTURE = ROOT / "test/fixtures/nrf52_shutdown_leds.cpp"

ARDUINO = r'''
#pragma once
#include <cstdint>
#include <vector>
#include <string>
#include <cassert>
#define LOW 0
#define HIGH 1
#define OUTPUT 1
#define INPUT 0
#define PINS_COUNT 64
struct Event { std::string name; int pin; int value; };
extern std::vector<Event> events;
extern int levels[PINS_COUNT];
inline int digitalPinToPinName(int pin) { return pin == 62 ? 0xff : pin; }
inline void digitalWrite(int pin, int value) {
  assert(pin >= 0 && pin < PINS_COUNT && pin != 62);
  levels[pin] = value; events.push_back({"write", pin, value});
}
inline void pinMode(int pin, int mode) {
  assert(pin >= 0 && pin < PINS_COUNT && pin != 62);
  events.push_back({"mode", pin, mode});
}
'''


def compile_source(testcase, source, defines=(), expect_success=True):
    compiler = shutil.which("c++") or shutil.which("g++")
    testcase.assertIsNotNone(compiler, "run this host-only suite where c++ is available")
    with tempfile.TemporaryDirectory(prefix="nrf52-shutdown-leds-") as folder:
        work = Path(folder)
        (work / "Arduino.h").write_text(ARDUINO)
        (work / "case.cpp").write_text(source)
        binary = work / ("case.exe" if os.name == "nt" else "case")
        command = [compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                   "-I", str(work), "-I", str(ROOT / "src"),
                   *["-D" + flag for flag in defines], str(work / "case.cpp"),
                   "-o", str(binary)]
        if os.name != "nt":
            command[1:1] = ["-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                           "-fno-pie", "-no-pie"]
        built = subprocess.run(command, text=True, capture_output=True)
        testcase.assertEqual(built.returncode, 0, built.stderr)
        ran = subprocess.run([str(binary)], text=True, capture_output=True)
        if expect_success:
            testcase.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        else:
            testcase.assertNotEqual(ran.returncode, 0, "negative control did not fail")


def lifecycle_source(mutation=None):
    source = BOARD.read_text()
    if mutation == "automatic-owner-left-running":
        source = source.replace("Bluefruit.autoConnLed(false);", "// negative control")
    elif mutation == "final-latch-left-on":
        source = source.replace("mesh_nrf52::turnOffShutdownLeds();", "// negative control")
    methods = "\n".join(extract_braced(source, signature) for signature in (
        "void NRF52Board::shutdownPeripherals()",
        "void NRF52Board::powerOff()",
        "void NRF52Board::enterDeepSleep(uint32_t secs)",
        "void NRF52Board::enterSystemOff(uint8_t reason)",
    ))
    x1 = (ROOT / "variants/meshtracker_x1/MeshTrackerX1Board.h").read_text()
    hook = extract_braced(x1, "void shutdownPeripherals() override")
    hook = hook.replace("void shutdownPeripherals() override",
                        "void MeshTrackerX1Board::shutdownPeripherals()", 1)
    return FIXTURE.read_text().replace("@METHODS@", methods).replace("@X1_HOOK@", hook)


class Nrf52ShutdownLedsTests(unittest.TestCase):
    def test_real_shutdown_paths_stop_led_owner_then_clear_latches(self):
        compile_source(self, lifecycle_source())

    def test_automatic_led_owner_negative_control(self):
        compile_source(self, lifecycle_source("automatic-owner-left-running"),
                       expect_success=False)

    def test_final_latch_negative_control(self):
        compile_source(self, lifecycle_source("final-latch-left-on"),
                       expect_success=False)

    def test_aliases_polarity_and_absent_led_sentinels(self):
        source = r'''
#include <Arduino.h>
#include <helpers/nrf52/ShutdownLeds.h>
std::vector<Event> events;
int levels[PINS_COUNT];
int main() {
  for (int& level : levels) level = LED_STATE_ON;
  mesh_nrf52::turnOffShutdownLeds();
  for (const Event& e : events) {
    assert(e.pin == EXPECT_PIN);
    if (e.name == "write") assert(e.value == !LED_STATE_ON);
  }
  assert(events.size() == EXPECT_EVENTS);
  for (size_t i = 0; i < events.size(); i += 2) {
    assert(events[i].name == "write" && events[i + 1].name == "mode");
  }
}
'''
        cases = (
            # Preferred LED_BLUE avoids expanding an obsolete broken alias.
            ("LED_STATE_ON=1", "LED_BLUE=36", "LED_CONN=UNUSED_PIN_NAME",
             "LED_BUILTIN=36", "LED_PIN=36", "LED_RED=36", "LED_GREEN=36",
             "EXPECT_PIN=36", "EXPECT_EVENTS=10"),
            ("LED_STATE_ON=0", "LED_BLUE=-1", "LED_BUILTIN=35", "LED_PIN=255",
             "LED_RED=PINS_COUNT", "LED_GREEN=62", "EXPECT_PIN=35", "EXPECT_EVENTS=2"),
            ("LED_STATE_ON=0", "LED_CONN=12", "EXPECT_PIN=12", "EXPECT_EVENTS=2"),
            ("LED_STATE_ON=1", "EXPECT_PIN=-1", "EXPECT_EVENTS=0"),
        )
        for case in cases:
            with self.subTest(defines=case):
                compile_source(self, source, case)

    def test_meshtracker_inherits_base_poweroff_and_keeps_peripheral_hook(self):
        x1 = (ROOT / "variants/meshtracker_x1/MeshTrackerX1Board.h").read_text()
        self.assertNotIn("void powerOff(", x1)
        self.assertIn("NRF52Board::shutdownPeripherals();", x1)
        # Catch an include-only helper that never reaches production shutdown.
        self.assertIn('#include "nrf52/ShutdownLeds.h"', BOARD.read_text())


if __name__ == "__main__":
    unittest.main()
