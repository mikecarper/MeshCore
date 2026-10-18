"""Exercise the T114 probe and real driver lifecycle without a panel or SPI MISO."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "src/helpers/ui"

ARDUINO = r'''
#pragma once
#include <cassert>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>
#define LOW 0
#define HIGH 1
#define INPUT 0
#define OUTPUT 1
#define INPUT_PULLUP 2
#define INPUT_PULLDOWN 3
#define PIN_TFT_SCL 40
#define PIN_TFT_SDA 41
#define PIN_TFT_RST 2
#define PIN_TFT_VDD_CTL 3
#define PIN_TFT_LEDA_CTL 15
#define PIN_TFT_CS 11
#define PIN_TFT_DC 12
#define SPI_CLOCK_DIV2 2
#define pgm_read_byte(p) (*(p))
inline int modes[48] = {}, levels[48] = {};
inline uint64_t now_us = 0, reset_high_at = 0, reset_low_at = 0, reset_low_us = 0;
inline unsigned gpio_events = 0, reads = 0, transactions = 0, spi_begins = 0;
inline unsigned init_calls = 0, allocations = 0, draws = 0, resets = 0;
inline unsigned clock_edges = 0, command_bits = 0, data_bits = 0;
inline uint8_t command_value = 0;
inline bool transaction = false, initialized = false, init_ok = true;
inline std::string scenario;
inline std::vector<int> biases;
inline uint32_t read_value = 0;
inline void check_panel_pin(int pin) {
  assert((pin == PIN_TFT_CS || pin == PIN_TFT_DC || pin == PIN_TFT_SDA || pin == PIN_TFT_SCL
      || pin == PIN_TFT_RST || pin == PIN_TFT_VDD_CTL || pin == PIN_TFT_LEDA_CTL)
      && "probe must only touch panel pins, not MISO or the radio bus");
}
inline bool automatic() {
#if defined(HELTEC_T114) && defined(T114_DISPLAY_AUTODETECT) && T114_DISPLAY_AUTODETECT
  return true;
#else
  return false;
#endif
}
inline int backlight_on() {
#ifdef PIN_TFT_LEDA_CTL_ACTIVE
  return PIN_TFT_LEDA_CTL_ACTIVE;
#else
  return LOW;
#endif
}
inline void pinMode(int pin, int mode) {
  check_panel_pin(pin);
  ++gpio_events; modes[pin] = mode;
  if (pin == PIN_TFT_SDA && (mode == INPUT_PULLUP || mode == INPUT_PULLDOWN)) {
    assert(transaction && command_bits == 8 && command_value == 0x04);
    biases.push_back(mode);
    const unsigned within_attempt = (transactions - 1) % 6;
    read_value = 0x858552;
    if (scenario == "floating") read_value = mode == INPUT_PULLUP ? 0xFFFFFF : 0;
    if (scenario == "stuck-high") read_value = 0xFFFFFF;
    if (scenario == "stuck-low") read_value = 0;
    if (scenario == "biased") read_value = mode == INPUT_PULLUP ? 0x858552 : 0x123456;
    if (scenario == "noise") read_value = within_attempt == 2 ? 0x858553 : 0x858552;
    if (scenario == "recover" && transactions <= 6) read_value = 0;
    // The first read after changing bias can be stale on a real panel.
    if (scenario == "prime" && within_attempt % 3 == 0) read_value = 0;
    if (scenario == "programmable") read_value = 0x123456;
  }
}
inline void digitalWrite(int pin, int value) {
  check_panel_pin(pin);
  ++gpio_events;
  if (automatic() && pin == PIN_TFT_LEDA_CTL && value == backlight_on())
    assert(initialized && "backlight must stay off until initialization succeeds");
  if (pin == PIN_TFT_RST && value == HIGH && levels[pin] == LOW) {
    reset_low_us = now_us - reset_low_at;
    reset_high_at = now_us; ++resets;
  }
  if (pin == PIN_TFT_RST && value == LOW && levels[pin] == HIGH) reset_low_at = now_us;
  if (pin == PIN_TFT_CS && value == LOW && levels[pin] == HIGH && spi_begins == 0) {
    assert(automatic());
    assert(now_us - reset_high_at >= 120000 && "probe reset settle");
    assert(reset_low_us >= 10000 && "probe reset pulse");
    assert(levels[PIN_TFT_VDD_CTL] == LOW);
    assert(levels[PIN_TFT_LEDA_CTL] != backlight_on());
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
  assert(clock_edges == 9 + data_bits && data_bits < 24);
  assert(levels[PIN_TFT_SCL] == LOW && "sample after dummy/falling edge");
  ++reads;
  return (read_value >> (23 - data_bits++)) & 1;
}
inline void delay(unsigned ms) { now_us += uint64_t(ms) * 1000; }
inline void delayMicroseconds(unsigned us) { now_us += us; }
'''

SPI = r'''
#pragma once
#include <Arduino.h>
struct SPIClass {
  void begin() {
    ++spi_begins;
    // The actual SPI1 opener takes SDA/SCK back from bitbang GPIO mode.
    modes[PIN_TFT_SDA] = modes[PIN_TFT_SCL] = OUTPUT;
  }
  void setClockDivider(int) {}
};
inline SPIClass SPI, SPI1;
'''

PANEL = r'''
#pragma once
#include <SPI.h>
enum OLEDDISPLAY_COLOR { BLACK=0, WHITE=1 };
constexpr int GEOMETRY_RAWMODE=0, ST77XX_WHITE=0xffff, ST77XX_BLACK=0;
inline uint8_t ArialMT_Plain_16[1], ArialMT_Plain_24[1];
class ST7789Spi {
  uint8_t _rst, _dc, _cs;
  int _clk=40, _miso=-1, _mosi=41;
  uint16_t displayHeight=135;
  uint8_t _buffheight=0;
  SPIClass* _spi;
public:
  template<class... T> ST7789Spi(SPIClass* spi, uint8_t rst, uint8_t dc, uint8_t cs, T...)
      : _rst(rst), _dc(dc), _cs(cs), _spi(spi) {}
@CONNECT@
  bool init() {
    ++init_calls;
    assert(connect());
    if (automatic()) {
      assert(transactions >= 6);
      assert(now_us - reset_high_at >= 120000 && "initialization reset settle");
      assert(reset_low_us >= 10000);
      assert(levels[PIN_TFT_LEDA_CTL] != backlight_on());
    }
    ++allocations;
    initialized = init_ok;
    return init_ok;
  }
  void landscapeScreen() { assert(initialized); }
  void flipScreenVertically() { assert(initialized); }
  void displayOn() { assert(initialized); }
  void clear() { assert(initialized); ++draws; }
  void display() { assert(initialized); ++draws; }
  void setFont(const uint8_t*) { assert(initialized); ++draws; }
  void setColor(OLEDDISPLAY_COLOR) { assert(initialized); ++draws; }
  void setRGB(uint16_t) { assert(initialized); ++draws; }
  void drawString(int,int,const char*) { assert(initialized); ++draws; }
  void drawStringMaxWidth(int,int,int,const char*) { assert(initialized); ++draws; }
  void fillRect(int,int,int,int) { assert(initialized); ++draws; }
  void drawRect(int,int,int,int) { assert(initialized); ++draws; }
  uint16_t getStringWidth(const char* s) { assert(initialized); ++draws; return strlen(s)*8; }
};
'''

MAIN = r'''
#include "ST7789Display.h"
void frame(ST7789Display& d) {
  const uint8_t bits[] = {0x80};
  d.clear(); d.startFrame(); d.setTextSize(1); d.setColor(1); d.setCursor(0,0);
  d.print("a"); d.printWordWrap("a",10); d.fillRect(0,0,1,1); d.drawRect(0,0,1,1);
  d.drawXbm(0,0,bits,1,1); d.endFrame(); (void)d.getTextWidth("a");
}
int main(int argc, char** argv) {
  assert(argc == 2); scenario = argv[1];
  ST7789Display d;
  if (!automatic()) {
    assert(d.begin() && d.isOn()); frame(d);
    assert(reads == 0 && transactions == 0);
    assert(now_us < 120000 && "legacy/other-board reset timing unchanged");
    return 0;
  }
  d.turnOn(); frame(d);
  assert(gpio_events == 0 && init_calls == 0 && allocations == 0 && draws == 0);
  const bool invalid = scenario == "floating" || scenario == "stuck-high" || scenario == "stuck-low"
      || scenario == "noise" || scenario == "biased";
  if (scenario == "init-fail") init_ok = false;
  const bool begun = d.begin();
  if (invalid || !init_ok) {
    assert(!begun && !d.isOn() && "invalid panel must remain headless");
    if (invalid) assert(init_calls == 0 && allocations == 0 && spi_begins == 0);
    assert(levels[PIN_TFT_VDD_CTL] == HIGH && levels[PIN_TFT_RST] == LOW);
    assert(levels[PIN_TFT_LEDA_CTL] != backlight_on());
    for (int pin : {PIN_TFT_CS,PIN_TFT_DC,PIN_TFT_SDA,PIN_TFT_SCL}) assert(modes[pin] == INPUT);
    const auto events=gpio_events, init=init_calls, allocated=allocations;
    d.turnOn(); d.turnOff(); frame(d); assert(!d.begin());
    assert(gpio_events == events && init_calls == init && allocations == allocated && draws == 0);
    assert(transactions == (invalid ? 12u : 6u));
    assert(now_us < 400000);
    return 0;
  }
  assert(begun && d.isOn() && initialized);
  assert(init_calls == 1 && allocations == 1);
  assert(levels[PIN_TFT_LEDA_CTL] == backlight_on());
  assert(transactions == (scenario == "recover" ? 12u : 6u));
  assert(biases.size() == transactions && reads == 24*transactions);
  for (unsigned i=0; i<transactions; ++i)
    assert(biases[i] == ((i%6)<3 ? INPUT_PULLUP : INPUT_PULLDOWN));
  frame(d); assert(draws > 0);
  const auto count=transactions, drawn=draws;
  d.turnOff(); assert(!d.isOn());
  assert(levels[PIN_TFT_VDD_CTL] == HIGH && levels[PIN_TFT_LEDA_CTL] != backlight_on());
  frame(d); assert(draws == drawn);
  d.turnOn(); assert(d.isOn() && init_calls == 2 && transactions == count);
  assert(levels[PIN_TFT_LEDA_CTL] == backlight_on());
  frame(d); assert(draws > drawn);
  if (scenario == "wake-fail") {
    d.turnOff(); init_ok = false; d.turnOn();
    assert(!d.isOn() && levels[PIN_TFT_VDD_CTL] == HIGH);
    const auto events = gpio_events; frame(d); d.turnOn(); assert(!d.begin());
    assert(events == gpio_events);
  }
}
'''


class T114DisplayDetectionTest(unittest.TestCase):
    def build(self, work, flags=(), *, old_bias=False, old_settle=False, old_turnaround=False):
        work.mkdir()
        for filename in ("ST7789Display.cpp", "ST7789Display.h", "T114DisplayProbe.h"):
            source = (UI / filename).read_text()
            if old_bias and filename == "T114DisplayProbe.h":
                mutated = source.replace("ids[0] == ids[1]", "true")
                self.assertNotEqual(mutated, source)
                source = mutated
            if old_turnaround and filename == "T114DisplayProbe.h":
                mutated = source.replace("if (mask == 1) pinMode(PIN_TFT_SDA, bias);", "")
                self.assertNotEqual(mutated, source)
                source = mutated.replace("  digitalWrite(PIN_TFT_DC, HIGH);",
                                         "  pinMode(PIN_TFT_SDA, bias);\n  digitalWrite(PIN_TFT_DC, HIGH);", 1)
            (work / filename).write_text(source)
        connect = extract_braced((UI / "ST7789Spi.h").read_text(), "bool connect()")
        if old_settle:
            mutated = connect.replace("delay(120);", "delay(0);")
            self.assertNotEqual(mutated, connect)
            connect = mutated
        for filename, source in {
                "Arduino.h": ARDUINO, "SPI.h": SPI, "Wire.h": "#pragma once\nclass TwoWire {};\n",
                "Adafruit_GFX.h": "#pragma once\n", "ST7789Spi.h": PANEL.replace("@CONNECT@", connect),
                "main.cpp": MAIN}.items():
            (work / filename).write_text(source)
        sanitizer = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
                     if sys.platform.startswith("linux") else [])
        binary = work / "test"
        compiled = subprocess.run([
            os.environ.get("CXX", "g++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-parameter", *sanitizer,
            "-DST7789=1", *flags, "-I"+str(work), "-I"+str(UI), "-I"+str(ROOT / "src"),
            str(work / "main.cpp"), str(work / "ST7789Display.cpp"), "-o", str(binary)
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

    def test_real_probe_rejects_floating_stuck_and_unstable_lines_without_allocating(self):
        with tempfile.TemporaryDirectory(prefix="t114-display-") as directory:
            binary = self.build(Path(directory)/"auto", ["-DHELTEC_T114", "-DT114_DISPLAY_AUTODETECT=1"])
            self.assertIn(b"meshcore.capability.display.autodetect.t114.v1", binary.read_bytes())
            for scenario in ("floating", "stuck-high", "stuck-low", "noise", "biased", "init-fail"):
                with self.subTest(scenario=scenario): self.run_case(binary, scenario)

    def test_real_probe_accepts_programmable_ids_recovery_and_preserves_wake(self):
        with tempfile.TemporaryDirectory(prefix="t114-display-") as directory:
            for active in (None, 1):
                flags = ["-DHELTEC_T114", "-DT114_DISPLAY_AUTODETECT=1"]
                if active is not None: flags.append(f"-DPIN_TFT_LEDA_CTL_ACTIVE={active}")
                binary = self.build(Path(directory)/str(active), flags)
                for scenario in ("valid", "prime", "programmable", "recover", "wake-fail"):
                    with self.subTest(active=active, scenario=scenario): self.run_case(binary, scenario)

    def test_legacy_t114_and_other_boards_do_not_probe_or_change_reset_timing(self):
        with tempfile.TemporaryDirectory(prefix="t114-display-") as directory:
            for index, flags in enumerate((["-DHELTEC_T114"], ["-DHELTEC_T114", "-DT114_DISPLAY_AUTODETECT=0"],
                                            ["-DT114_DISPLAY_AUTODETECT=1"])):
                binary = self.build(Path(directory)/str(index), flags)
                self.run_case(binary, "valid")
                self.assertNotIn(b"meshcore.capability.display.autodetect.t114.v1", binary.read_bytes())

    def test_bias_and_reset_settle_negative_controls_are_detected(self):
        with tempfile.TemporaryDirectory(prefix="t114-display-") as directory:
            flags = ["-DHELTEC_T114", "-DT114_DISPLAY_AUTODETECT=1"]
            binary = self.build(Path(directory)/"bias", flags, old_bias=True)
            self.run_case(binary, "biased", negative="invalid panel must remain headless")
            binary = self.build(Path(directory)/"settle", flags, old_settle=True)
            self.run_case(binary, "valid", negative="initialization reset settle")
            binary = self.build(Path(directory)/"turnaround", flags, old_turnaround=True)
            self.run_case(binary, "valid", negative="release SDA before command falling edge")


if __name__ == "__main__":
    unittest.main()
