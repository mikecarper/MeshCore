"""Exercise optional Tracker V1 display reads and shared-rail driver lifecycle."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from cpp_source import body
from test_serial_wifi_sessions import SANITIZER_FLAGS

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "src/helpers/ui"

ARDUINO = r'''
#pragma once
#include <cassert>
#include <cstdint>
#include <string>
#include <vector>
#define LOW 0
#define HIGH 1
#define INPUT 0
#define OUTPUT 1
#define INPUT_PULLUP 2
#define INPUT_PULLDOWN 3
#define PIN_TFT_SCL 41
#define PIN_TFT_SDA 42
#define PIN_TFT_RST 39
#define PIN_TFT_LEDA_CTL 21
#define PIN_TFT_CS 38
#define PIN_TFT_DC 40
#ifndef PIN_TFT_LEDA_CTL_ACTIVE
#define PIN_TFT_LEDA_CTL_ACTIVE HIGH
#endif
inline int modes[48] = {}, levels[48] = {};
inline uint64_t now_us = 0, reset_high_at = 0, reset_low_at = 0, reset_low_us = 0;
inline unsigned gpio_events = 0, reads = 0, transactions = 0, spi_begins = 0;
inline unsigned initializations = 0, allocations = 0, objects = 0, draws = 0;
inline unsigned clock_edges = 0, command_bits = 0, data_bits = 0;
inline uint8_t command_value = 0;
inline bool transaction = false, fail_pixels = false, initialized = false;
inline std::string scenario;
inline uint32_t read_value = 0;
inline bool automatic() {
#ifdef MESHCORE_TRACKER_DISPLAY_AUTODETECT
  return true;
#else
  return false;
#endif
}
inline void check_panel_pin(int pin) {
  assert((pin == PIN_TFT_CS || pin == PIN_TFT_DC || pin == PIN_TFT_SDA
      || pin == PIN_TFT_SCL || pin == PIN_TFT_RST || pin == PIN_TFT_LEDA_CTL)
      && "probe must never drive Vext, GNSS, or radio pins");
}
inline void pinMode(int pin, int mode) {
  check_panel_pin(pin); ++gpio_events; modes[pin] = mode;
  if (pin == PIN_TFT_SDA && (mode == INPUT_PULLUP || mode == INPUT_PULLDOWN)) {
    assert(transaction && command_bits == 8 && command_value == 0x04);
    const unsigned within_attempt = (transactions - 1) % 6;
    read_value = 0x7c89f0;
    if (scenario == "floating") read_value = mode == INPUT_PULLUP ? 0xffffff : 0;
    if (scenario == "stuck-high") read_value = 0xffffff;
    if (scenario == "stuck-low") read_value = 0;
    if (scenario == "biased") read_value = mode == INPUT_PULLUP ? 0x7c89f0 : 0x123456;
    if (scenario == "noise") read_value = within_attempt == 2 ? 0x7c89f1 : 0x7c89f0;
    if (scenario == "recover" && transactions <= 6) read_value = 0;
    if (scenario == "prime" && within_attempt % 3 == 0) read_value = 0;
    if (scenario == "programmable") read_value = 0x123456;
  }
}
inline void digitalWrite(int pin, int value) {
  check_panel_pin(pin); ++gpio_events;
  if (automatic() && pin == PIN_TFT_LEDA_CTL && value == PIN_TFT_LEDA_CTL_ACTIVE)
    assert(initialized && "backlight must remain off until initialization");
  if (pin == PIN_TFT_RST && value == HIGH && levels[pin] == LOW) {
    reset_low_us = now_us - reset_low_at; reset_high_at = now_us;
  }
  if (pin == PIN_TFT_RST && value == LOW && levels[pin] == HIGH) reset_low_at = now_us;
  if (pin == PIN_TFT_CS && value == LOW && levels[pin] == HIGH && spi_begins == 0) {
    assert(automatic() && now_us - reset_high_at >= 120000 && reset_low_us >= 10000);
    assert(levels[PIN_TFT_LEDA_CTL] != PIN_TFT_LEDA_CTL_ACTIVE);
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
    assert(command_bits == 8 && data_bits == 24 && clock_edges == 33);
    transaction = false;
  }
  levels[pin] = value;
}
inline int digitalRead(int pin) {
  assert(pin == PIN_TFT_SDA && transaction && levels[PIN_TFT_DC] == HIGH);
  assert(modes[pin] == INPUT_PULLUP || modes[pin] == INPUT_PULLDOWN);
  assert(clock_edges == 9 + data_bits && data_bits < 24 && levels[PIN_TFT_SCL] == LOW);
  ++reads;
  return (read_value >> (23 - data_bits++)) & 1;
}
inline void delay(unsigned ms) { now_us += uint64_t(ms) * 1000; }
inline void delayMicroseconds(unsigned us) { now_us += us; }
'''

HARNESS = r'''
@POLICY@
#include <Arduino.h>
#include "WirelessTrackerDisplayProbe.h"
constexpr int SPI_CLOCK_DIV2=2, ST77XX_NORON=0x13, ST77XX_DISPON=0x29;
constexpr int ST77XX_DISPOFF=0x28, ST77XX_SLPIN=0x10;
constexpr uint16_t ST77XX_BLACK=0, ST77XX_WHITE=0xffff;
constexpr int ST7735_BUFFER_COLOR_DEPTH=16;
struct Spi {
  void begin(int scl, int miso, int sda, int cs) {
    assert(scl==41 && miso==-1 && sda==42 && cs==-1);
    if (automatic()) assert(transactions>=6);
    ++spi_begins;
  }
  void setClockDivider(int) {}
} spi;
static Spi* _spi=&spi;
static int _height, _width, _colstart, _rowstart, lcd;
struct TFT_eSprite {
  bool valid=false;
  explicit TFT_eSprite(int*) { ++objects; }
  void setColorDepth(int) {}
  void* createSprite(int w,int h) {
    assert(w==160 && h==80); ++allocations;
    valid=!fail_pixels; fail_pixels=false;
    return getPointer();
  }
  void* getPointer() { return valid ? this : nullptr; }
  void fillScreen(uint16_t) { assert(valid); ++draws; }
  void setTextColor(uint16_t) { assert(valid); }
};
static TFT_eSprite* sprite=nullptr;
static uint32_t curr_color;
@READY@
namespace mesh {
struct Logger { void println(const char*) {} } logger;
Logger& usbLoggingPort() { return logger; }
}
struct Rail {
  unsigned claims;
  unsigned taken=0,released=0;
  void claim() { ++claims; ++taken; }
  void release() { assert(claims); --claims; ++released; }
};
void sendCommand(int) {}
@MARKER@
class ST7735Display {
public:
  bool _isOn=false;
  Rail* _peripher_power;
#ifdef MESHCORE_TRACKER_DISPLAY_AUTODETECT
  bool _probeComplete=false,_present=false;
#endif
  explicit ST7735Display(Rail* power) : _peripher_power(power) {}
  bool begin();
  void turnOn();
  void turnOff();
  void _resetAndInit() { ++initializations; initialized=true; }
  void endFrame() { if (spriteReady()) { assert(initialized); ++draws; } }
};
@METHODS@
int main(int argc,char** argv) {
  assert(argc==3); scenario=argv[1];
  const unsigned gps_claims=unsigned(std::stoul(argv[2]));
  Rail rail{gps_claims}; ST7735Display display(&rail);
  const bool invalid=scenario=="floating" || scenario=="stuck-high" || scenario=="stuck-low"
      || scenario=="biased" || scenario=="noise";
  if (scenario=="alloc-fail") fail_pixels=true;
  const bool began=display.begin();
  if (automatic() && invalid) {
    assert(!began && !display._isOn && "absent panel must remain headless");
    assert(allocations==0 && objects==0 && spi_begins==0 && initializations==0);
    assert(rail.claims==gps_claims && rail.taken==1 && rail.released==1);
    assert(levels[PIN_TFT_RST]==LOW && levels[PIN_TFT_LEDA_CTL]!=PIN_TFT_LEDA_CTL_ACTIVE);
    for (int pin : {PIN_TFT_CS,PIN_TFT_DC,PIN_TFT_SDA,PIN_TFT_SCL}) assert(modes[pin]==INPUT);
    const auto events= gpio_events;
    assert(!display.begin()); display.turnOn(); display.turnOff(); display.endFrame();
    assert(events==gpio_events && transactions==12 && now_us<400000);
    assert(rail.claims==gps_claims && allocations==0 && draws==0);
    return 0;
  }
  if (scenario=="alloc-fail") {
    assert(!began && sprite==nullptr && allocations==1 && spi_begins==0);
    assert(rail.claims==gps_claims);
    assert(display.begin());
    assert(allocations==2 && rail.claims==gps_claims+1);
  } else {
    assert(began && allocations==1 && rail.claims==gps_claims+1);
  }
  assert(display._isOn && initializations==1);
  const auto probe_count=transactions;
  if (automatic()) assert(probe_count==(scenario=="recover" ? 12u : 6u));
  else assert(probe_count==0 && reads==0);
  assert(levels[PIN_TFT_LEDA_CTL]==PIN_TFT_LEDA_CTL_ACTIVE);
  const auto pixels=sprite; const auto allocated=allocations; const auto events=gpio_events;
  assert(display.begin() && gpio_events==events && rail.claims==gps_claims+1);
  display.turnOff(); assert(rail.claims==gps_claims && !display._isOn);
  display.turnOn();
  assert(display._isOn && rail.claims==gps_claims+1 && initializations==2);
  assert(transactions==probe_count && allocations==allocated && sprite==pixels);
  display.turnOff(); assert(rail.claims==gps_claims);
  delete sprite;
}
'''


class WirelessTrackerDisplayDetectionTests(unittest.TestCase):
    def build(self, work, flags, *, old_bias=False, old_turnaround=False):
        work.mkdir()
        driver = (UI / "ST7735Display.cpp").read_text()
        header = (UI / "ST7735Display.h").read_text()
        policy = header[header.index("#if defined(WIRELESS_TRACKER_DISPLAY_AUTODETECT)"):header.index("class ST7735Display")]
        marker = driver[driver.index("#ifdef MESHCORE_TRACKER_DISPLAY_AUTODETECT"):driver.index("// Most ST7735")]
        methods = "\n".join(body(driver, signature) for signature in (
            "bool ST7735Display::begin()", "void ST7735Display::turnOn()", "void ST7735Display::turnOff()"))
        source = (HARNESS.replace("@POLICY@", policy).replace("@MARKER@", marker)
                  .replace("@READY@", body(driver, "static bool spriteReady()"))
                  .replace("@METHODS@", methods))
        probe = (UI / "WirelessTrackerDisplayProbe.h").read_text()
        if old_bias:
            probe = probe.replace("ids[0] == ids[1]", "true")
        if old_turnaround:
            probe = probe.replace("if (mask == 1) pinMode(PIN_TFT_SDA, bias);", "")
            probe = probe.replace("  digitalWrite(PIN_TFT_DC, HIGH);", "  pinMode(PIN_TFT_SDA, bias);\n  digitalWrite(PIN_TFT_DC, HIGH);", 1)
        (work / "Arduino.h").write_text(ARDUINO)
        (work / "WirelessTrackerDisplayProbe.h").write_text(probe)
        (work / "test.cpp").write_text(source)
        binary = work / "test"
        compiled = subprocess.run([os.environ.get("CXX", "g++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-variable", *SANITIZER_FLAGS, "-DESP_PLATFORM=1", *flags, "-I"+str(work),
            str(work / "test.cpp"), "-o", str(binary)], capture_output=True, text=True, timeout=60)
        self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
        return binary

    def run_case(self, binary, scenario, gps=1, negative=None):
        result = subprocess.run([str(binary), scenario, str(gps)], capture_output=True, text=True, timeout=10)
        if negative:
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(negative, result.stderr)
        else:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_optional_tracker_rejects_absence_noise_and_preserves_gps_power(self):
        with tempfile.TemporaryDirectory(prefix="tracker-detect-") as directory:
            for board in ("HELTEC_LORA_V3", "HELTEC_TRACKER_V1_1"):
                binary = self.build(Path(directory)/board, ["-D"+board, "-DWIRELESS_TRACKER_DISPLAY_AUTODETECT=1"])
                self.assertIn(b"meshcore.capability.display.autodetect.tracker.v1", binary.read_bytes())
                for scenario in ("floating", "stuck-high", "stuck-low", "biased", "noise"):
                    for gps in (0, 1):
                        with self.subTest(board=board, scenario=scenario, gps=gps): self.run_case(binary, scenario, gps)

    def test_present_programmable_id_reset_recovery_and_allocation_retry(self):
        with tempfile.TemporaryDirectory(prefix="tracker-detect-") as directory:
            for active in (0, 1):
                binary = self.build(Path(directory)/str(active), ["-DHELTEC_LORA_V3", "-DWIRELESS_TRACKER_DISPLAY_AUTODETECT=1",
                    f"-DPIN_TFT_LEDA_CTL_ACTIVE={active}"])
                for scenario in ("valid", "programmable", "prime", "recover", "alloc-fail"):
                    for gps in (0, 1):
                        with self.subTest(active=active, scenario=scenario, gps=gps): self.run_case(binary, scenario, gps)

    def test_other_boards_and_disabled_option_keep_existing_initialization(self):
        with tempfile.TemporaryDirectory(prefix="tracker-detect-") as directory:
            variants = (["-DHELTEC_LORA_V3"], ["-DHELTEC_LORA_V3", "-DWIRELESS_TRACKER_DISPLAY_AUTODETECT=0"],
                ["-DWIRELESS_TRACKER_DISPLAY_AUTODETECT=1"],
                *[["-DWIRELESS_TRACKER_DISPLAY_AUTODETECT=1", "-DHELTEC_LORA_V3", "-D"+board]
                    for board in ("HELTEC_TRACKER_V2", "HELTEC_T096", "HELTEC_T1")])
            for index, flags in enumerate(variants):
                binary = self.build(Path(directory)/str(index), flags)
                self.assertNotIn(b"meshcore.capability.display.autodetect.tracker.v1", binary.read_bytes())
                self.run_case(binary, "floating")

    def test_opposite_pull_and_turnaround_negative_controls_fail(self):
        with tempfile.TemporaryDirectory(prefix="tracker-detect-") as directory:
            flags = ["-DHELTEC_LORA_V3", "-DWIRELESS_TRACKER_DISPLAY_AUTODETECT=1"]
            binary = self.build(Path(directory)/"bias", flags, old_bias=True)
            self.run_case(binary, "biased", negative="absent panel must remain headless")
            binary = self.build(Path(directory)/"turnaround", flags, old_turnaround=True)
            self.run_case(binary, "valid", negative="release SDA before command falling edge")


if __name__ == "__main__":
    unittest.main()
