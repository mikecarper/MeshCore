#!/usr/bin/env python3
"""Exercise the real SH1106 driver with bounded vendor allocation stand-ins.

The stand-in follows pinned GrayOLED allocation order: one reusable framebuffer
and a newly allocated I2C adapter on each vendor begin, including failed probes.
This checks ownership, address selection and rendering, not physical I2C timing.
"""

from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "src/helpers/ui/SH1106Display.cpp"
HEADER = ROOT / "src/helpers/ui/SH1106Display.h"

WIRE = r'''
#pragma once
#include <cassert>
#include <cstddef>
#include <cstdint>
struct TwoWire {
  bool present[128] = {};
  uint8_t probes[16] = {};
  unsigned probe_count = 0, begins = 0;
  uint8_t address = 0;
  void begin() { ++begins; }
  void beginTransmission(uint8_t value) {
    address = value;
    if (probe_count < sizeof(probes)) probes[probe_count++] = value;
  }
  uint8_t endTransmission() { return present[address] ? 0 : 4; }
  void clearProbes() { probe_count = 0; }
};
inline TwoWire Wire;
'''

VENDOR = r'''
#pragma once
#include <Wire.h>
#include <cstdlib>
#include <cstring>
#include <new>
#define SH110X_BLACK 0
#define SH110X_WHITE 1
#define SH110X_DISPLAYON 0xAF
#define SH110X_DISPLAYOFF 0xAE
struct PanelState {
  bool framebuffer_allowed = true, adapter_begin_allowed = true;
  unsigned buffers = 0, buffer_allocations = 0, buffer_frees = 0, peak_buffers = 0;
  unsigned adapters = 0, adapter_allocations = 0, adapter_frees = 0, peak_adapters = 0;
  unsigned begins = 0, clears = 0, frames = 0, on_commands = 0, off_commands = 0;
  unsigned fills = 0, rectangles = 0, bitmaps = 0, prints = 0;
  uint8_t address = 0;
  bool reset = false, cp437_enabled = false;
  int width = 0, height = 0, text_size = 0, cursor_x = 0, cursor_y = 0;
  uint16_t text_color = 0, drawing_color = 0;
  uint8_t* buffer = nullptr;
  char text[64] = {};
};
inline PanelState panel;
class Adafruit_I2CDevice {
  uint8_t address;
  TwoWire* wire;
public:
  Adafruit_I2CDevice(uint8_t value, TwoWire* bus) : address(value), wire(bus) {}
  static void* operator new(std::size_t bytes) {
    void* result = std::malloc(bytes);
    if (!result) throw std::bad_alloc();
    ++panel.adapters;
    ++panel.adapter_allocations;
    if (panel.adapters > panel.peak_adapters) panel.peak_adapters = panel.adapters;
    return result;
  }
  static void operator delete(void* pointer) noexcept {
    assert(panel.adapters > 0);
    --panel.adapters;
    ++panel.adapter_frees;
    std::free(pointer);
  }
  bool begin() {
    wire->begin();
    wire->beginTransmission(address);
    return wire->endTransmission() == 0 && panel.adapter_begin_allowed;
  }
};
class Adafruit_SH1106G {
  uint16_t width, height;
  TwoWire* wire;
protected:
  Adafruit_I2CDevice* i2c_dev = nullptr;
  uint8_t* buffer = nullptr;
public:
  Adafruit_SH1106G(uint16_t w, uint16_t h, TwoWire* bus, int)
      : width(w), height(h), wire(bus) {
    panel.width = w; panel.height = h;
  }
  ~Adafruit_SH1106G() {
    delete i2c_dev;
    if (buffer) {
      assert(panel.buffers > 0);
      --panel.buffers; ++panel.buffer_frees;
      std::free(buffer);
      panel.buffer = nullptr;
    }
  }
  bool begin(uint8_t address, bool reset) {
    ++panel.begins; panel.address = address; panel.reset = reset;
    if (!buffer) {
      if (!panel.framebuffer_allowed) return false;
      buffer = static_cast<uint8_t*>(std::malloc(width * ((height + 7) / 8)));
      assert(buffer);
      ++panel.buffers; ++panel.buffer_allocations;
      if (panel.buffers > panel.peak_buffers) panel.peak_buffers = panel.buffers;
      panel.buffer = buffer;
    }
    // Deliberately match GrayOLED: the wrapper must release the old adapter.
    i2c_dev = new Adafruit_I2CDevice(address, wire);
    if (!i2c_dev->begin()) return false;
    clearDisplay();
    return true;
  }
  void clearDisplay() { ++panel.clears; std::memset(buffer, 0, width * ((height + 7) / 8)); }
  void display() { ++panel.frames; }
  void oled_command(uint8_t command) {
    if (command == SH110X_DISPLAYON) ++panel.on_commands;
    if (command == SH110X_DISPLAYOFF) ++panel.off_commands;
  }
  void setTextColor(uint16_t color) { panel.text_color = color; }
  void setTextSize(int size) { panel.text_size = size; }
  void cp437(bool enabled) { panel.cp437_enabled = enabled; }
  void setCursor(int x, int y) { panel.cursor_x = x; panel.cursor_y = y; }
  void print(const char* text) {
    ++panel.prints;
    std::strncpy(panel.text, text, sizeof(panel.text) - 1);
  }
  void fillRect(int, int, int, int, uint16_t color) { ++panel.fills; panel.drawing_color = color; }
  void drawRect(int, int, int, int, uint16_t color) { ++panel.rectangles; panel.drawing_color = color; }
  void drawBitmap(int, int, const uint8_t* bits, int w, int h, uint16_t color) {
    assert(bits && w > 0 && h > 0); ++panel.bitmaps; panel.drawing_color = color;
  }
  void getTextBounds(const char* text, int, int, int16_t* x, int16_t* y,
                     uint16_t* w, uint16_t* h) {
    *x = *y = 0; *w = std::strlen(text) * 6; *h = 8;
  }
};
'''

HARNESS = r'''
#include "SH1106Display.h"
#include <cassert>
#include <cstdio>
#include <cstring>
static_assert(SH1106Display::DISPLAY_WIDTH == 128, "preserve every display column");
static_assert(SH1106Display::DISPLAY_HEIGHT == 64, "preserve every display row");
static_assert(SH1106Display::FRAMEBUFFER_BYTES == 1024, "preserve the monochrome framebuffer");
void reset() {
  assert(panel.buffers == 0 && panel.adapters == 0);
  panel = PanelState{}; Wire = TwoWire{};
}
void bounded() {
  assert(panel.buffers <= 1 && panel.adapters <= 1);
  assert(panel.peak_buffers <= 1 && panel.peak_adapters <= 1);
}
void destroyed() {
  assert(panel.buffers == 0 && panel.adapters == 0);
  assert(panel.buffer_allocations == panel.buffer_frees);
  assert(panel.adapter_allocations == panel.adapter_frees);
}
int main() {
  reset();
  {
    SH1106Display driver;
    assert(driver.width() == 128 && driver.height() == 64 && !driver.isOn());
    Wire.present[DISPLAY_ADDRESS] = true;
    for (unsigned cycle = 0; cycle < 200; ++cycle) {
      Wire.clearProbes();
      assert(driver.begin());
      assert(panel.address == DISPLAY_ADDRESS && panel.reset);
      assert(Wire.probe_count == 2 && Wire.probes[0] == DISPLAY_ADDRESS);
      assert(panel.buffer_allocations == 1 && panel.adapters == 1);
      bounded();
    }
    uint8_t* framebuffer = panel.buffer;
    driver.turnOn(); assert(driver.isOn() && panel.on_commands == 1);
    driver.turnOff(); assert(!driver.isOn() && panel.off_commands == 1);
    driver.turnOn(); assert(driver.isOn() && panel.on_commands == 2);
    assert(panel.begins == 200 && panel.buffer == framebuffer);
    driver.clear();
    assert(panel.frames == 1);
    driver.startFrame();
    assert(panel.text_color == SH110X_WHITE && panel.text_size == 1 && panel.cp437_enabled);
    driver.setTextSize(2); assert(panel.text_size == 2);
    driver.setCursor(7, 11); assert(panel.cursor_x == 7 && panel.cursor_y == 11);
    driver.print("same pixels"); assert(!std::strcmp(panel.text, "same pixels"));
    driver.setColor(SH110X_BLACK);
    driver.fillRect(1, 2, 3, 4); assert(panel.fills == 1 && panel.drawing_color == SH110X_BLACK);
    driver.drawRect(2, 3, 4, 5); assert(panel.rectangles == 1 && panel.drawing_color == SH110X_BLACK);
    driver.setColor(SH110X_WHITE);
    const uint8_t bitmap[] = {0x80};
    driver.drawXbm(1, 2, bitmap, 1, 1); assert(panel.bitmaps == 1 && panel.drawing_color == SH110X_WHITE);
    assert(driver.getTextWidth("same") == 24);
    driver.endFrame(); assert(panel.frames == 2);
    assert(panel.buffer == framebuffer && panel.buffer_allocations == 1);

    Wire.present[DISPLAY_ADDRESS] = false;
    for (unsigned cycle = 0; cycle < 100; ++cycle) {
      assert(!driver.begin()); bounded();
      assert(panel.buffer == framebuffer && panel.buffer_allocations == 1);
      assert(panel.adapters == 1 && !driver.isOn());
    }
    unsigned commands = panel.on_commands + panel.off_commands;
    driver.turnOn(); driver.turnOff();
    assert(!driver.isOn() && panel.on_commands + panel.off_commands == commands);
    Wire.present[DISPLAY_ADDRESS] = true;
    panel.adapter_begin_allowed = false;
    assert(!driver.begin()); bounded();
    assert(panel.adapters == 1 && panel.buffer == framebuffer);
    panel.adapter_begin_allowed = true;
    panel.framebuffer_allowed = false; // existing buffer must remain reusable
    assert(driver.begin()); bounded();
    driver.turnOn(); assert(driver.isOn());
    assert(panel.buffer_allocations == 1 && panel.buffer == framebuffer);

    Wire.present[DISPLAY_ADDRESS] = false;
#ifdef DISPLAY_ADDRESS_ALT
    Wire.present[DISPLAY_ADDRESS_ALT] = true;
    Wire.present[DISPLAY_ADDRESS ^ 1] = true;
    Wire.clearProbes();
    assert(driver.begin() && panel.address == DISPLAY_ADDRESS_ALT);
    assert(Wire.probes[0] == DISPLAY_ADDRESS && Wire.probes[1] == DISPLAY_ADDRESS_ALT);
    Wire.present[DISPLAY_ADDRESS_ALT] = false;
#endif
    Wire.present[DISPLAY_ADDRESS ^ 1] = true;
    Wire.clearProbes();
    assert(driver.begin() && panel.address == (DISPLAY_ADDRESS ^ 1));
    bounded();
  }
  destroyed();
  reset();
  {
    SH1106Display driver;
    Wire.present[DISPLAY_ADDRESS] = true;
    panel.framebuffer_allowed = false;
    assert(!driver.begin() && panel.buffers == 0 && panel.adapters == 0);
    driver.turnOn(); assert(!driver.isOn());
    panel.framebuffer_allowed = true;
    assert(driver.begin()); bounded();
  }
  destroyed();
  for (unsigned cycle = 0; cycle < 64; ++cycle) {
    reset();
    {
      SH1106Display driver;
      // Destruction must reclaim allocations even after failed initialization.
      Wire.present[DISPLAY_ADDRESS] = (cycle & 1) != 0;
      assert(driver.begin() == ((cycle & 1) != 0));
      bounded();
    }
    destroyed();
  }
  std::puts("PASS: SH1106 bounded allocation, retry, addressing and rendering");
}
'''


class Sh1106MemoryTests(unittest.TestCase):
    def compile_run(self, definitions=(), header=None, expect_failure=False):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a host C++17 compiler is required")
        with tempfile.TemporaryDirectory(prefix="meshcore-sh1106-memory-") as directory:
            work = Path(directory)
            for name, contents in {
                "Wire.h": WIRE,
                "Adafruit_GFX.h": "#pragma once\n",
                "Adafruit_SH110X.h": VENDOR,
                "Adafruit_GrayOLED.h": '#pragma once\n#include "Adafruit_SH110X.h"\n',
                "SH1106Display.h": HEADER.read_text() if header is None else header,
                "SH1106Display.cpp": DRIVER.read_text(),
                "test.cpp": HARNESS,
            }.items():
                (work / name).write_text(contents, encoding="ascii")
            binary = work / "display"
            sanitizer = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                          "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])
            built = subprocess.run([
                compiler, "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
                "-Wno-unused-parameter",
                "-DESP32_PLATFORM=1", *sanitizer, *definitions,
                "-I", str(work), "-I", str(ROOT / "src/helpers/ui"),
                str(work / "test.cpp"), str(work / "SH1106Display.cpp"),
                "-o", str(binary),
            ], text=True, capture_output=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            checked = subprocess.run([str(binary)], text=True, capture_output=True, timeout=15)
            if expect_failure:
                self.assertNotEqual(checked.returncode, 0, "unbounded adapter allocation escaped the regression")
                self.assertIn("Assertion", checked.stderr)
            else:
                self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                self.assertIn("PASS: SH1106 bounded allocation, retry, addressing and rendering", checked.stdout)

    def test_real_driver_bounds_success_failure_recovery_and_address_fallback(self):
        for definitions in ((), ("-DDISPLAY_ADDRESS=0x3D",),
                            ("-DDISPLAY_ADDRESS=0x3C", "-DDISPLAY_ADDRESS_ALT=0x3E")):
            with self.subTest(definitions=definitions):
                self.compile_run(definitions)

    def test_missing_i2c_adapter_release_negative_control(self):
        header = HEADER.read_text()
        mutated, count = re.subn(r"delete\s+i2c_dev\s*;", "/* missing adapter release */", header)
        self.assertEqual(count, 1, "production subclass must release its previous adapter")
        self.compile_run(header=mutated, expect_failure=True)


if __name__ == "__main__":
    unittest.main()
