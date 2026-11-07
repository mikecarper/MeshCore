#!/usr/bin/env python3
"""Exercise the actual GPIO encoder driver with instrumented Arduino GPIO."""

from pathlib import Path
import os
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
#define CHANGE 3
void pinMode(int pin, int mode);
int digitalRead(int pin);
uint32_t millis();
void attachInterrupt(uint32_t, void (*)(), int);
void detachInterrupt(uint32_t);
'''

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <algorithm>
#include <helpers/ui/GpioRotaryInput.h>
#include <helpers/ui/RotaryInputGPIO.h>

using Event = RotaryInputEvent;
static int levels[512], modes[512];
static unsigned reads, writes;
static unsigned attaches, detaches;
static bool interrupts[512];
static uint32_t now;
void pinMode(int pin, int mode) {
  assert(pin >= 0 && pin < 512);
  ++writes; modes[pin] = mode;
}
int digitalRead(int pin) {
  assert(pin >= 0 && pin < 512);
  ++reads; return levels[pin];
}
uint32_t millis() { return now; }
void attachInterrupt(uint32_t pin, void (*)(), int mode) {
  assert(pin < 512 && mode == CHANGE); ++attaches; interrupts[pin] = true;
}
void detachInterrupt(uint32_t pin) {
  assert(pin < 512 && interrupts[pin]); ++detaches; interrupts[pin] = false;
}
static void reset() {
  std::fill(levels, levels + 512, HIGH);
  std::fill(modes, modes + 512, -1);
  reads = writes = 0; now = 0;
  attaches = detaches = 0;
  std::fill(interrupts, interrupts + 512, false);
}
static Event state(GpioRotaryInput& input, unsigned ab) {
  levels[10] = (ab & 2) ? HIGH : LOW;
  levels[11] = (ab & 1) ? HIGH : LOW;
  return input.poll();
}
static Event button(GpioRotaryInput& input, uint32_t time, bool pressed) {
  now = time; levels[12] = pressed ? LOW : HIGH;
  return input.poll();
}
static void invalid() {
  const int configs[][4] = {
    {-1, 11, -1, 4}, {10, -1, -1, 4}, {10, 11, -2, 4},
    {10, 10, -1, 4}, {10, 11, 10, 4}, {10, 11, 11, 4},
    {10, 11, -1, 0}, {10, 11, -1, 256}, {10, 11, -1, 65535},
  };
  for (const auto& config : configs) {
    reset();
    GpioRotaryInput input(config[0], config[1], config[2], config[3]);
    assert(!input.isReady());
    assert(input.poll() == Event::None);
    assert(!input.begin() && !input.isReady());
    assert(!input.needsPolling());
    for (int i = 0; i < 10; ++i) assert(input.poll() == Event::None);
    assert(!input.isButtonPressed());
    input.end();
    assert(writes == 0 && reads == 0 && attaches == 0 && detaches == 0);
    assert(!input.configure(config[0], config[1], config[2], config[3]));
    assert(!input.begin());
    assert(writes == 0 && reads == 0);
  }
}
static void lifecycle() {
  reset();
  GpioRotaryInput input(10, 11, 12);
  assert(input.buttonPin() == 12);
  assert(input.poll() == Event::None && writes == 0 && reads == 0);
  assert(input.begin() && input.isReady());
  assert(writes == 3 && modes[10] == INPUT_PULLUP && modes[12] == INPUT_PULLUP);
#if defined(NRF52_PLATFORM) || defined(RP2040_PLATFORM)
  assert(attaches == 3 && interrupts[10] && interrupts[11] && interrupts[12]);
#else
  assert(attaches == 0);
#endif
  assert(input.poll() == Event::None);
  assert(state(input, 1) == Event::None); // A partial detent must not survive.
  input.end();
  assert(!input.isReady() && modes[10] == INPUT && modes[12] == INPUT);
  assert(!input.needsPolling());
#if defined(NRF52_PLATFORM) || defined(RP2040_PLATFORM)
  assert(detaches == 3 && !interrupts[10] && !interrupts[11] && !interrupts[12]);
#endif
  const unsigned stopped_reads = reads, stopped_writes = writes;
  assert(input.poll() == Event::None && !input.isButtonPressed());
  input.end();
  assert(reads == stopped_reads && writes == stopped_writes);
  assert(input.configure(10, 11, -1, 4, false, false));
  assert(!input.isReady() && input.buttonPin() == -1);
  assert(input.begin() && modes[10] == INPUT && modes[11] == INPUT);
  assert(state(input, 0) == Event::None);
  assert(state(input, 2) == Event::None);
  assert(state(input, 3) == Event::None);
  assert(state(input, 1) == Event::Next);
  // Reconfiguration releases the old pull-ups and waits for explicit begin.
  assert(input.configure(20, 21, 22));
  assert(!input.isReady() && modes[10] == INPUT);
  const unsigned configured_writes = writes;
  assert(input.poll() == Event::None && writes == configured_writes);
  assert(input.begin() && modes[20] == INPUT_PULLUP);
  const unsigned accepted_writes = writes;
  assert(!input.configure(20, 20));
  assert(input.isReady() && input.buttonPin() == 22);
  assert(modes[20] == INPUT_PULLUP && writes == accepted_writes);
  assert(input.poll() == Event::None);
  // Generic driver allows a board's valid large numeric pin identifiers.
  GpioRotaryInput large(254, 255, -1);
  assert(large.begin() && modes[254] == INPUT_PULLUP);
  // Existing rotary-only backends retain their optional-button defaults.
  RotaryInputGPIO legacy(30, 31);
  assert(legacy.buttonPin() == -1 && !legacy.isButtonPressed());
  assert(legacy.begin());
}
static void quadrature() {
  const unsigned positive[] = {1, 0, 2, 3};
  for (unsigned steps = 1; steps <= 255; ++steps) {
    for (bool reverse : {false, true}) {
      reset();
      GpioRotaryInput input(10, 11, -1, steps, reverse);
      assert(input.begin());
      for (unsigned i = 0; i < steps * 3; ++i) {
        const Event expected = ((i + 1) % steps == 0)
            ? (reverse ? Event::Prev : Event::Next) : Event::None;
        assert(state(input, positive[i % 4]) == expected);
        assert(input.poll() == Event::None); // Stationary is never movement.
      }
    }
  }
  reset();
  GpioRotaryInput input(10, 11);
  assert(input.begin());
  for (unsigned ab : {1, 3, 1, 0, 1, 0, 2})
    assert(state(input, ab) == Event::None);
  assert(state(input, 3) == Event::Next); // Bounce cancels within a detent.
  assert(state(input, 1) == Event::None);
  assert(state(input, 2) == Event::None); // Invalid two-bit transition resets.
  for (unsigned ab : {0, 1, 3}) assert(state(input, ab) == Event::None);
  assert(state(input, 2) == Event::Prev);
  // Direction reversal cancels a partial detent.
  assert(state(input, 3) == Event::None);
  assert(state(input, 2) == Event::None);
  assert(state(input, 0) == Event::None);
  assert(state(input, 1) == Event::None);
  assert(state(input, 3) == Event::None);
  assert(state(input, 2) == Event::Prev);
}
static void rotation_suspension() {
  reset();
  GpioRotaryInput input(10, 11, 12);
  input.setRotationPolling(false);
  assert(reads == 0 && writes == 0 && attaches == 0);
  assert(input.begin() && !input.needsPolling());
#if defined(NRF52_PLATFORM) || defined(RP2040_PLATFORM)
  assert(!interrupts[10] && !interrupts[11] && interrupts[12]);
#endif
  const unsigned before_reads = reads;
  for (unsigned ab : {1, 0, 2, 3, 1}) assert(state(input, ab) == Event::None);
  assert(reads == before_reads + 5); // Only switch reads while rotation is off.
  assert(button(input, 100, true) == Event::None && input.needsPolling());
  assert(input.isButtonGesturePending());
  assert(button(input, 125, true) == Event::None && input.needsPolling());
  assert(button(input, 200, false) == Event::None);
  assert(button(input, 225, false) == Event::Select);
  assert(!input.needsPolling() && !input.isButtonGesturePending());
  input.setRotationPolling(true); // Current AB is 1; a half detent cannot carry over.
#if defined(NRF52_PLATFORM) || defined(RP2040_PLATFORM)
  assert(interrupts[10] && interrupts[11] && interrupts[12]);
#endif
#if defined(ESP32_PLATFORM)
  assert(input.needsPolling());
#endif
  assert(state(input, 0) == Event::None);
  assert(state(input, 2) == Event::None);
  input.setRotationPolling(false); // Discard this second partial detent.
  assert(state(input, 3) == Event::None);
  input.setRotationPolling(true);
  for (unsigned ab : {1, 0, 2}) assert(state(input, ab) == Event::None);
  assert(state(input, 3) == Event::Next);
  input.setRotationPolling(false);
  input.end();
  assert(!input.needsPolling());
#if defined(NRF52_PLATFORM) || defined(RP2040_PLATFORM)
  assert(!interrupts[10] && !interrupts[11] && !interrupts[12]);
#endif
}
static void buttons() {
  reset(); GpioRotaryInput input(10, 11, 12); assert(input.begin());
  assert(input.needsPolling() ==
#if defined(ESP32_PLATFORM)
      true
#else
      false
#endif
  );
  assert(button(input, 100, true) == Event::None);
  assert(input.needsPolling());
  assert(!input.isButtonPressed());
  assert(button(input, 124, true) == Event::None);
  assert(button(input, 125, true) == Event::None && input.isButtonPressed());
  assert(input.needsPolling());
  assert(button(input, 200, false) == Event::None && !input.isButtonPressed());
  assert(button(input, 224, false) == Event::None);
  assert(button(input, 225, false) == Event::Select);
  assert(input.needsPolling() ==
#if defined(ESP32_PLATFORM)
      true
#else
      false
#endif
  );
  assert(input.poll() == Event::None);
  // Neither a short electrical glitch nor contact bounce creates a click.
  assert(button(input, 300, true) == Event::None);
  assert(button(input, 310, false) == Event::None);
  assert(button(input, 350, false) == Event::None);
  assert(button(input, 400, true) == Event::None);
  assert(button(input, 410, false) == Event::None);
  assert(button(input, 420, true) == Event::None);
  assert(button(input, 445, true) == Event::None);
  assert(button(input, 500, false) == Event::None);
  assert(button(input, 510, true) == Event::None);
  assert(button(input, 520, false) == Event::None);
  assert(button(input, 544, false) == Event::None);
  assert(button(input, 545, false) == Event::Select);
  // Long press fires once at 1200 ms, then release cannot create a click.
  assert(button(input, 600, true) == Event::None);
  assert(button(input, 625, true) == Event::None);
  assert(button(input, 1799, true) == Event::None);
  assert(button(input, 1800, true) == Event::LongPress);
  assert(input.needsPolling() ==
#if defined(ESP32_PLATFORM)
      true
#else
      false
#endif
  );
  assert(button(input, 3000, true) == Event::None);
  assert(button(input, 3010, false) == Event::None);
  assert(button(input, 3035, false) == Event::None);
  // If release was sampled first, its duration still produces a long press.
  assert(button(input, 4000, true) == Event::None);
  assert(button(input, 4025, true) == Event::None);
  assert(button(input, 5200, false) == Event::None);
  assert(button(input, 5225, false) == Event::LongPress);
  assert(input.poll() == Event::None);
}
static void startup_and_wrap() {
  reset(); levels[12] = LOW;
  GpioRotaryInput input(10, 11, 12); assert(input.begin());
  assert(!input.isButtonPressed());
  assert(input.needsPolling() ==
#if defined(ESP32_PLATFORM)
      true
#else
      false
#endif
  );
  assert(button(input, 5000, true) == Event::None && !input.isButtonPressed());
  assert(button(input, 5010, false) == Event::None);
  assert(button(input, 5020, true) == Event::None);
  assert(button(input, 5030, false) == Event::None);
  assert(button(input, 5055, false) == Event::None);
  assert(button(input, 5100, true) == Event::None);
  assert(button(input, 5125, true) == Event::None && input.isButtonPressed());
  assert(button(input, 5200, false) == Event::None);
  assert(button(input, 5225, false) == Event::Select);
  // Both debounce and press duration use unsigned wrap-safe subtraction.
  assert(button(input, UINT32_MAX - 10, true) == Event::None);
  assert(button(input, 13, true) == Event::None);
  assert(!input.isButtonPressed());
  assert(button(input, 14, true) == Event::None && input.isButtonPressed());
  assert(button(input, 1188, true) == Event::None);
  assert(button(input, 1189, true) == Event::LongPress);
  assert(button(input, 1200, false) == Event::None);
  assert(button(input, 1225, false) == Event::None);
}
static void simultaneous() {
  reset(); GpioRotaryInput input(10, 11, 12); assert(input.begin());
  assert(button(input, 100, true) == Event::None);
  assert(button(input, 125, true) == Event::None);
  for (unsigned ab : {1, 0, 2}) assert(state(input, ab) == Event::None);
  assert(button(input, 200, false) == Event::None);
  now = 225;
  assert(state(input, 3) == Event::Select); // Button wins over final edge.
  assert(input.poll() == Event::None); // Discarded rotation cannot replay.
  for (unsigned ab : {1, 0, 2}) assert(state(input, ab) == Event::None);
  assert(state(input, 3) == Event::Next);
}
int main() {
  invalid(); lifecycle(); quadrature(); rotation_suspension(); buttons(); startup_and_wrap(); simultaneous();
}
'''


class GpioRotaryInputTest(unittest.TestCase):
    def test_driver_with_sanitizers(self):
        compiler = shutil.which(os.environ.get("CXX", "g++"))
        if not compiler:
            self.skipTest("A C++ compiler is required")
        with tempfile.TemporaryDirectory(prefix="gpio-rotary-") as directory:
            temporary = Path(directory)
            (temporary / "Arduino.h").write_text(ARDUINO, encoding="ascii")
            (temporary / "main.cpp").write_text(HARNESS, encoding="ascii")
            for platform, flags in {"generic": [], "nrf52": ["-DNRF52_PLATFORM=1"],
                                    "rp2040": ["-DRP2040_PLATFORM=1"],
                                    "esp32": ["-DESP32_PLATFORM=1"]}.items():
                with self.subTest(platform=platform):
                    binary = temporary / platform
                    result = subprocess.run([
                        compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                        "-Wno-error=reorder",  # Existing RotaryInputGPIO constructor.
                        "-g", "-O1", "-fno-omit-frame-pointer", "-fno-pie", "-no-pie",
                        "-fsanitize=address,undefined", *flags, "-I", str(temporary),
                        "-I", str(ROOT / "src"), str(temporary / "main.cpp"),
                        str(ROOT / "src/helpers/ui/GpioRotaryInput.cpp"),
                        str(ROOT / "src/helpers/ui/RotaryInputGPIO.cpp"),
                        "-o", str(binary),
                    ], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    environment = dict(os.environ, ASAN_OPTIONS="detect_leaks=1:abort_on_error=1",
                                       UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1")
                    result = subprocess.run([str(binary)], env=environment,
                                            capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
