"""Exercise the RC32 optional T108 probe and actual NV3001B driver lifecycle."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "src/helpers/ui"

ARDUINO = r'''
#pragma once
#include <cassert>
#include <cstdint>
#include <cstring>
#include <initializer_list>
#define LOW 0
#define HIGH 1
#define INPUT 0
#define OUTPUT 1
#define INPUT_PULLUP 2
#define INPUT_PULLDOWN 3
#define PROGMEM
#define pgm_read_byte(p) (*(p))
#define PIN_TFT_SCL 17
#define PIN_TFT_SDA 38
#define PIN_TFT_CS 39
#define PIN_TFT_DC 16
#define PIN_TFT_RST 4
#define PIN_TFT_EN 6
#define PIN_TFT_BL 5
#ifndef PIN_TFT_EN_ACTIVE
#define PIN_TFT_EN_ACTIVE LOW
#endif
#ifndef PIN_TFT_BL_ACTIVE
#define PIN_TFT_BL_ACTIVE HIGH
#endif
inline int modes[48] = {}, levels[48] = {};
inline uint64_t now_us = 0, reset_high_at = 0, reset_low_at = 0, reset_low_us = 0;
inline unsigned gpio_events = 0, reads = 0, transactions = 0, spi_begins = 0, spi_ends = 0;
inline unsigned spi_transfers = 0, pixel_bytes = 0, reset_count = 0;
inline unsigned clock_edges = 0, command_bits = 0, data_bits = 0;
inline unsigned per_command[2][4] = {};
inline uint8_t command_value = 0, spi_command = 0;
inline bool transaction = false, spi_active = false, init_done = false, rail_on = false;
inline const char* scenario = "valid";
inline uint32_t read_value = 0;
inline bool is(const char* value) { return strcmp(scenario, value) == 0; }
inline bool automatic() {
#if defined(HELTEC_RC32) && defined(RC32_DISPLAY_AUTODETECT) && RC32_DISPLAY_AUTODETECT
  return true;
#else
  return false;
#endif
}
inline void panelPin(int pin) {
  assert((pin == PIN_TFT_CS || pin == PIN_TFT_DC || pin == PIN_TFT_SDA || pin == PIN_TFT_SCL
      || pin == PIN_TFT_RST || pin == PIN_TFT_EN || pin == PIN_TFT_BL || pin == 30)
      && "touch only panel pins and explicitly shared power");
}
inline int commandIndex() {
  if (command_value == 0x04) return 0;
  if (command_value == 0xDA) return 1;
  if (command_value == 0xDB) return 2;
  assert(command_value == 0xDC); return 3;
}
inline unsigned responseBits() { return command_value == 0x04 ? 24 : 8; }
inline void pinMode(int pin, int mode) {
  panelPin(pin); ++gpio_events; modes[pin] = mode;
  if (pin == PIN_TFT_SDA && (mode == INPUT_PULLUP || mode == INPUT_PULLDOWN)) {
    assert(transaction && command_bits == 8);
    const unsigned occurrence = per_command[mode == INPUT_PULLUP ? 0 : 1][commandIndex()]++;
    uint32_t id = 0x300101;
    if (is("wrong-id")) id = 0x123456;
    if (is("biased") && mode == INPUT_PULLDOWN) id = 0x300102;
    if (is("noise") && occurrence % 3 == 2) id = 0x300102;
    if (is("prime") && occurrence % 3 == 0) id = 0;
    if (is("recover") && reset_count == 1) id = 0;
    if ((is("fallback") || is("bad-fallback")) && command_value == 0x04) id = 0;
    if (is("bad-fallback") && command_value == 0xDC) id = 0x300102;
    if (is("floating")) id = mode == INPUT_PULLUP ? 0xFFFFFF : 0;
    if (is("stuck-high")) id = 0xFFFFFF;
    if (is("stuck-low")) id = 0;
    if (command_value == 0x04) read_value = id;
    else read_value = (id >> ((3-commandIndex())*8)) & 0xFF;
  }
}
inline void digitalWrite(int pin, int value) {
  panelPin(pin); ++gpio_events;
  if (automatic() && pin == PIN_TFT_BL && value == PIN_TFT_BL_ACTIVE)
    assert(init_done && pixel_bytes >= 220u*128u*2u && "backlight only after initialization and clear");
  if (pin == PIN_TFT_EN) {
    if (automatic() && rail_on && value != PIN_TFT_EN_ACTIVE) {
      assert(!spi_active);
      for (int p : {PIN_TFT_CS, PIN_TFT_DC, PIN_TFT_SDA, PIN_TFT_SCL})
        assert(modes[p] == INPUT && "park bus before removing panel power");
    }
    rail_on = value == PIN_TFT_EN_ACTIVE;
  }
  if (pin == PIN_TFT_RST && value == LOW) reset_low_at = now_us;
  if (pin == PIN_TFT_RST && value == HIGH && levels[pin] == LOW) {
    reset_low_us = now_us-reset_low_at; reset_high_at = now_us; ++reset_count;
  }
  if (pin == PIN_TFT_CS && value == LOW && levels[pin] == HIGH && !spi_active) {
    assert(automatic());
    assert(rail_on && levels[PIN_TFT_BL] != PIN_TFT_BL_ACTIVE);
    assert(now_us-reset_high_at >= 120000 && "probe reset settle");
    assert(reset_low_us >= 120000 && "probe reset pulse");
    transaction = true; ++transactions;
    clock_edges = command_bits = data_bits = 0; command_value = 0;
  }
  if (pin == PIN_TFT_SCL && value == HIGH && transaction) {
    ++clock_edges;
    if (modes[PIN_TFT_SDA] == OUTPUT) {
      assert(levels[PIN_TFT_DC] == LOW && command_bits < 8);
      command_value = uint8_t((command_value << 1) | levels[PIN_TFT_SDA]);
      ++command_bits;
    }
  }
  if (pin == PIN_TFT_SCL && value == LOW && transaction && clock_edges == 8)
    assert(modes[PIN_TFT_SDA] != OUTPUT && "release SDA before command falling edge");
  if (pin == PIN_TFT_CS && value == HIGH && transaction) {
    assert(command_bits == 8 && data_bits == responseBits());
    assert(clock_edges == 8+responseBits()+(command_value == 0x04 ? 1 : 0));
    transaction = false;
  }
  levels[pin] = value;
}
inline int digitalRead(int pin) {
  assert(pin == PIN_TFT_SDA && transaction && levels[PIN_TFT_DC] == HIGH);
  assert(modes[pin] == INPUT_PULLUP || modes[pin] == INPUT_PULLDOWN);
  assert(clock_edges == 8+data_bits+(command_value == 0x04 ? 1 : 0));
  assert(data_bits < responseBits() && levels[PIN_TFT_SCL] == LOW && "sample after turnaround falling edge");
  ++reads;
  return (read_value >> (responseBits()-1-data_bits++)) & 1;
}
inline void delay(unsigned ms) { now_us += uint64_t(ms)*1000; }
inline void delayMicroseconds(unsigned us) { now_us += us; }
'''

SPI = r'''
#pragma once
#include <Arduino.h>
#define HSPI 1
#define MSBFIRST 1
#define SPI_MODE0 0
struct SPISettings { SPISettings(int, int, int) {} };
struct SPIClass {
  explicit SPIClass(int) {}
  bool begin(int sck, int miso, int mosi, int cs) {
    assert(sck == PIN_TFT_SCL && miso == -1 && mosi == PIN_TFT_SDA && cs == PIN_TFT_CS);
    ++spi_begins; spi_active = true; init_done = false; pixel_bytes = 0;
    modes[sck] = modes[mosi] = OUTPUT;
    return !is("init-fail") && !(is("wake-fail") && spi_begins == 2);
  }
  void end() { assert(spi_active); ++spi_ends; spi_active = false; }
  void beginTransaction(SPISettings) { assert(spi_active); }
  void endTransaction() { assert(spi_active); }
  uint8_t transfer(uint8_t data) {
    assert(spi_active && rail_on);
    assert(levels[PIN_TFT_CS] == LOW); ++spi_transfers;
    if (levels[PIN_TFT_DC] == LOW) {
      assert(now_us-reset_high_at >= 120000);
      spi_command = data;
      if (data == 0x29) init_done = true;
    } else if (spi_command == 0x2C) {
      assert(init_done); ++pixel_bytes;
    }
    return 0;
  }
};
'''

MAIN = r'''
#include "NV3001BDisplay.h"
void frame(NV3001BDisplay& d) {
  const uint8_t bits[] = {0x80};
  d.clear(); d.startFrame(); d.setTextSize(1); d.setColor(1); d.setCursor(0,0);
  d.print("a"); d.printWordWrap("b",10); d.fillRect(0,0,1,1); d.drawRect(0,0,1,1);
  d.drawXbm(0,0,bits,1,1); d.endFrame(); assert(d.getTextWidth("a") == 6);
}
int main(int argc, char** argv) {
  assert(argc == 2); scenario = argv[1];
  RefCountedDigitalPin power(30, HIGH); power.begin();
  NV3001BDisplay d(&power);
  if (automatic()) {
    const auto events = gpio_events; d.turnOn(); frame(d); d.turnOff();
    assert(gpio_events == events && spi_begins == 0 && spi_transfers == 0);
  }
  const bool absent = is("floating") || is("stuck-high") || is("stuck-low")
      || is("noise") || is("biased") || is("wrong-id") || is("bad-fallback");
  const bool begun = d.begin();
  if (automatic() && is("init-fail")) {
    assert(!begun && !d.isOn() && spi_begins == 1 && spi_transfers == 0 && spi_ends == 1);
    assert(transactions == 6 && levels[30] == LOW && !rail_on);
    assert(levels[PIN_TFT_BL] != PIN_TFT_BL_ACTIVE && levels[PIN_TFT_RST] == LOW);
    const auto events=gpio_events; d.turnOn(); d.turnOff(); frame(d); assert(!d.begin());
    assert(gpio_events == events);
    return 0;
  }
  if (automatic() && absent) {
    assert(!begun && !d.isOn() && "invalid panel must remain headless");
    assert(spi_begins == 0 && spi_transfers == 0 && spi_ends == 0);
    assert(transactions == 48 && reads == 576 && now_us < 800000);
    assert(levels[PIN_TFT_EN] != PIN_TFT_EN_ACTIVE && levels[PIN_TFT_RST] == LOW);
    assert(levels[PIN_TFT_BL] != PIN_TFT_BL_ACTIVE && levels[30] == LOW);
    for (int p : {PIN_TFT_CS, PIN_TFT_DC, PIN_TFT_SDA, PIN_TFT_SCL}) assert(modes[p] == INPUT);
    const auto events=gpio_events; d.turnOn(); d.turnOff(); frame(d); assert(!d.begin());
    assert(events == gpio_events && spi_begins == 0 && transactions == 48);
    return 0;
  }
  assert(begun && d.isOn() && init_done && spi_begins == 1);
  assert(levels[PIN_TFT_BL] == PIN_TFT_BL_ACTIVE && levels[30] == HIGH);
  if (automatic()) assert(transactions == (is("fallback") ? 24u : is("recover") ? 30u : 6u));
  else assert(transactions == 0 && reads == 0);
  frame(d); const auto count=transactions, tx=spi_transfers;
  assert(d.begin() && spi_begins == 1 && spi_transfers == tx);
  d.turnOff(); assert(!d.isOn() && levels[PIN_TFT_EN] != PIN_TFT_EN_ACTIVE && levels[30] == LOW);
  frame(d); assert(spi_transfers == tx);
  if (automatic()) {
    assert(spi_ends == 1 && !spi_active);
    for (int p : {PIN_TFT_CS, PIN_TFT_DC, PIN_TFT_SDA, PIN_TFT_SCL}) assert(modes[p] == INPUT);
  }
  d.turnOn();
  if (automatic() && is("wake-fail")) {
    assert(!d.isOn() && spi_begins == 2 && spi_ends == 2 && transactions == count);
    assert(!rail_on && levels[30] == LOW && levels[PIN_TFT_RST] == LOW);
    const auto events=gpio_events; d.turnOn(); d.turnOff(); frame(d); assert(!d.begin());
    assert(gpio_events == events && spi_transfers == tx);
    return 0;
  }
  assert(d.isOn() && spi_begins == 2 && transactions == count);
  assert(levels[PIN_TFT_BL] == PIN_TFT_BL_ACTIVE && levels[30] == HIGH);
  frame(d); assert(spi_transfers > tx);
  d.turnOff(); d.turnOff(); assert(levels[30] == LOW);
}
'''


class RC32DisplayDetectionTest(unittest.TestCase):
    def build(self, work, flags=(), *, old_bias=False, old_turnaround=False):
        work.mkdir()
        for filename in ("NV3001BDisplay.cpp", "NV3001BDisplay.h", "RC32DisplayProbe.h"):
            source = (UI / filename).read_text()
            if old_bias and filename == "RC32DisplayProbe.h":
                mutated = source.replace("ids[0] == ids[1]", "true")
                self.assertNotEqual(mutated, source)
                source = mutated
            if old_turnaround and filename == "RC32DisplayProbe.h":
                mutated = source.replace("if (mask == 1) pinMode(PIN_TFT_SDA, bias);", "")
                self.assertNotEqual(mutated, source)
                source = mutated.replace("  digitalWrite(PIN_TFT_DC, HIGH);",
                                         "  pinMode(PIN_TFT_SDA, bias);\n  digitalWrite(PIN_TFT_DC, HIGH);", 1)
            (work / filename).write_text(source)
        for filename, source in {"Arduino.h": ARDUINO, "SPI.h": SPI, "main.cpp": MAIN}.items():
            (work / filename).write_text(source)
        sanitizer = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
                     if sys.platform.startswith("linux") else [])
        binary = work / "test"
        compiled = subprocess.run([
            os.environ.get("CXX", "g++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-parameter", "-Wno-type-limits", *sanitizer, *flags,
            "-I"+str(work), "-I"+str(UI), "-I"+str(ROOT / "src"),
            str(work / "main.cpp"), str(work / "NV3001BDisplay.cpp"), "-o", str(binary)
        ], capture_output=True, text=True, timeout=60)
        self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
        return binary

    def run_case(self, binary, scenario, *, negative=None):
        ran = subprocess.run([str(binary), scenario], capture_output=True, text=True, timeout=10)
        if negative:
            self.assertNotEqual(ran.returncode, 0)
            self.assertIn(negative, ran.stderr)
        else:
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_absent_unstable_wrong_controller_stays_headless(self):
        with tempfile.TemporaryDirectory(prefix="rc32-display-") as directory:
            binary = self.build(Path(directory)/"auto", ["-DHELTEC_RC32", "-DRC32_DISPLAY_AUTODETECT=1"])
            self.assertIn(b"meshcore.capability.display.autodetect.rc32.v1", binary.read_bytes())
            for scenario in ("floating", "stuck-high", "stuck-low", "noise", "biased", "wrong-id", "bad-fallback", "init-fail"):
                with self.subTest(scenario=scenario): self.run_case(binary, scenario)

    def test_both_id_protocols_recovery_and_sleep_wake_preserve_screen(self):
        with tempfile.TemporaryDirectory(prefix="rc32-display-") as directory:
            for index, active in enumerate(([], ["-DPIN_TFT_EN_ACTIVE=HIGH", "-DPIN_TFT_BL_ACTIVE=LOW"])):
                flags = ["-DHELTEC_RC32", "-DRC32_DISPLAY_AUTODETECT=1", *active]
                binary = self.build(Path(directory)/str(index), flags)
                for scenario in ("valid", "prime", "fallback", "recover", "wake-fail"):
                    with self.subTest(active=active, scenario=scenario): self.run_case(binary, scenario)

    def test_legacy_and_other_nv3001b_boards_do_not_probe(self):
        with tempfile.TemporaryDirectory(prefix="rc32-display-") as directory:
            for index, flags in enumerate((["-DHELTEC_RC32"], ["-DHELTEC_RC32", "-DRC32_DISPLAY_AUTODETECT=0"],
                                            ["-DRC32_DISPLAY_AUTODETECT=1"])):
                binary = self.build(Path(directory)/str(index), flags)
                self.run_case(binary, "valid")
                self.assertNotIn(b"meshcore.capability.display.autodetect.rc32.v1", binary.read_bytes())

    def test_negative_controls_detect_bias_and_sda_turnaround_regressions(self):
        with tempfile.TemporaryDirectory(prefix="rc32-display-") as directory:
            flags = ["-DHELTEC_RC32", "-DRC32_DISPLAY_AUTODETECT=1"]
            binary = self.build(Path(directory)/"bias", flags, old_bias=True)
            self.run_case(binary, "biased", negative="invalid panel must remain headless")
            binary = self.build(Path(directory)/"turnaround", flags, old_turnaround=True)
            self.run_case(binary, "valid", negative="release SDA before command falling edge")


if __name__ == "__main__":
    unittest.main()
