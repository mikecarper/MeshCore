#!/usr/bin/env python3
"""Exercise native switch handoff through both production button drivers."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

ARDUINO = r'''
#pragma once
#include <stdint.h>
#define LOW 0
#define HIGH 1
#define INPUT 0
#define INPUT_PULLUP 2
#define INPUT_PULLDOWN 3
#define CHANGE 4
extern uint32_t now;
extern int level, mode, analog_level;
extern unsigned interrupts;
inline uint32_t millis() { return now; }
inline int digitalRead(int) { return level; }
inline int analogRead(int) { return analog_level; }
inline void pinMode(int, int value) { mode = value; }
inline void attachInterrupt(uint32_t, void (*)(), int change) {
  if (change == CHANGE) ++interrupts;
}
'''

HARNESS = r'''
#include <cassert>
#include <helpers/ui/MomentaryButton.h>
#include "examples/companion_radio/ui-orig/Button.h"
uint32_t now = 100;
int level = HIGH, mode = INPUT, analog_level = 100;
unsigned interrupts = 0;
static void native() {
  MomentaryButton button(7, 1000, true, true, true);
  button.begin();
  level = LOW; assert(button.check() == BUTTON_EVENT_NONE);
  now += 25; assert(button.check() == BUTTON_EVENT_NONE);
  level = HIGH; assert(button.check() == BUTTON_EVENT_NONE);
  now += 25; assert(button.check() == BUTTON_EVENT_NONE);
  assert(button.needsPolling());
  const unsigned old_interrupts = interrupts;
  mode = INPUT;
  button.resume();
  assert(mode == INPUT_PULLUP && !button.needsPolling());
#if defined(NRF52_PLATFORM)
  assert(interrupts == old_interrupts + 1);
#else
  assert(interrupts == old_interrupts);
#endif
  now += 500; assert(button.check() == BUTTON_EVENT_NONE);
  level = LOW; button.resume();
  assert(!button.isPressed() && !button.needsPolling());
  now += 15000;
  assert(button.check(true) == BUTTON_EVENT_NONE && !button.isPressed());
  level = HIGH; assert(button.check() == BUTTON_EVENT_NONE && button.needsPolling());
  now += 10; level = LOW; assert(button.check() == BUTTON_EVENT_NONE);
  assert(!button.isPressed()); // A release bounce does not arm the held switch.
  now += 10; level = HIGH; assert(button.check() == BUTTON_EVENT_NONE);
  now += 25; assert(button.check() == BUTTON_EVENT_NONE);
  now += 500; assert(button.check() == BUTTON_EVENT_NONE);
  level = LOW; assert(button.check() == BUTTON_EVENT_NONE);
  now += 25; assert(button.check() == BUTTON_EVENT_NONE && button.isPressed());
  level = HIGH; assert(button.check() == BUTTON_EVENT_NONE);
  now += 25; assert(button.check() == BUTTON_EVENT_NONE);
  now += 280; assert(button.check() == BUTTON_EVENT_CLICK);
  assert(button.check() == BUTTON_EVENT_NONE);
  // Active-high and analog wiring restore their appropriate input mode.
  level = LOW; MomentaryButton high(7, 1000, false, true, true); high.resume();
  assert(mode == INPUT_PULLDOWN && !high.isPressed());
  analog_level = 100; MomentaryButton analog(7, 1000, 50); analog.resume();
  assert(mode == INPUT && !analog.isPressed());
}
static void sample(Button& button, int raw, uint32_t after) {
  level = raw; now += after; button.update();
}
static void original() {
  unsigned short_count = 0, any_count = 0, long_count = 0;
  level = HIGH;
  Button button(7);
  button.onShortPress([&] { ++short_count; });
  button.onAnyPress([&] { ++any_count; });
  button.onLongPress([&] { ++long_count; });
  button.begin();
  sample(button, LOW, 10); sample(button, LOW, 60);
  sample(button, HIGH, 10); sample(button, HIGH, 60);
  assert(any_count == 1 && short_count == 0);
  unsigned old_interrupts = interrupts;
  button.resume();
  assert(mode == INPUT_PULLUP && button.getLastEvent() == Button::NONE);
#if defined(NRF52_PLATFORM)
  assert(interrupts == old_interrupts + 1);
#else
  assert(interrupts == old_interrupts);
#endif
  sample(button, HIGH, 600);
  assert(short_count == 0 && long_count == 0);
  level = LOW; button.resume();
  assert(!button.isPressed());
  sample(button, LOW, 15000);
  assert(!button.isPressed() && any_count == 1 && long_count == 0);
  sample(button, HIGH, 10); sample(button, LOW, 10);
  sample(button, HIGH, 10); sample(button, HIGH, 60);
  sample(button, HIGH, 600);
  assert(any_count == 1 && short_count == 0 && long_count == 0);
  sample(button, LOW, 10); sample(button, LOW, 60);
  assert(button.isPressed() && any_count == 2);
  sample(button, HIGH, 10); sample(button, HIGH, 60); sample(button, HIGH, 600);
  assert(short_count == 1 && long_count == 0);
  level = LOW; Button high(7, HIGH); high.resume();
  assert(mode == INPUT_PULLDOWN && !high.isPressed());
}
int main() { native(); original(); }
'''


class NativeButtonResumeTests(unittest.TestCase):
    def test_handoff_gestures_and_wake_interrupts(self):
        compiler = shutil.which("g++")
        if not compiler:
            self.skipTest("C++ compiler required")
        with tempfile.TemporaryDirectory(prefix="native-button-resume-") as directory:
            folder = Path(directory)
            (folder / "Arduino.h").write_text(ARDUINO, encoding="ascii")
            source = folder / "test.cpp"
            source.write_text(HARNESS, encoding="ascii")
            for platform, flags in {"generic": [], "nrf52": ["-DNRF52_PLATFORM=1"]}.items():
                with self.subTest(platform=platform):
                    binary = folder / platform
                    result = subprocess.run([
                        compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                        "-fsanitize=address,undefined", "-fno-pie", "-no-pie",
                        "-DMOMENTARY_BUTTON_WAKE_FROM_SLEEP=1", *flags,
                        "-I", str(folder), "-I", str(ROOT), "-I", str(ROOT / "src"),
                        str(source), str(ROOT / "src/helpers/ui/MomentaryButton.cpp"),
                        str(ROOT / "examples/companion_radio/ui-orig/Button.cpp"),
                        "-o", str(binary),
                    ], capture_output=True, text=True, timeout=60)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
