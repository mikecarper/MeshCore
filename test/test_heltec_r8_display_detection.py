"""Run the real R8 LCD driver/probe against recording GPIO and panel libraries."""
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
#include <string>
#define LOW 0
#define HIGH 1
#define INPUT 0
#define OUTPUT 1
#define INPUT_PULLUP 2
#define INPUT_PULLDOWN 3
#define HSPI 1
#define PIN_TFT_CS 47
#define PIN_TFT_DC 48
#define PIN_TFT_SCL 16
#define PIN_TFT_SDA 15
#ifndef PIN_TFT_MISO
#define PIN_TFT_MISO 45
#endif
#define PIN_TFT_RST 21
#define PIN_TFT_LEDA_CTL 44
#define PIN_TFT_LEDA_CTL_ACTIVE HIGH
#define SDCARD_CS 3
inline int modes[64] = {}, levels[64] = {};
inline uint64_t now_us=0, reset_low_at=0, reset_high_at=0, reset_low_us=0;
inline unsigned events=0, transactions=0, reads=0, resets=0, spi_begins=0, init_calls=0, draws=0;
inline unsigned spi_ends=0;
inline unsigned rotations=0, last_rotation=0;
inline unsigned command_bits=0, clocks=0, data_bits=0, command=0;
inline uint32_t response=0;
inline bool transaction=false, initialized=false;
inline std::string scenario;
inline bool automatic() {
#if defined(HELTEC_V4_R8_TFT) && defined(HELTEC_TFT_DISPLAY_AUTODETECT) && HELTEC_TFT_DISPLAY_AUTODETECT == 1
  return true;
#else
  return false;
#endif
}
inline void check_pin(int pin) {
  assert(pin==PIN_TFT_CS || pin==PIN_TFT_DC || pin==PIN_TFT_SCL || pin==PIN_TFT_SDA
      || pin==PIN_TFT_MISO || pin==PIN_TFT_RST || pin==PIN_TFT_LEDA_CTL || pin==40 || pin==SDCARD_CS);
}
inline void pinMode(int pin,int mode) {
  check_pin(pin); ++events;
  if (pin==PIN_TFT_MISO) assert(mode!=OUTPUT && "LCD SDO must never be driven");
  if (pin==PIN_TFT_LEDA_CTL && automatic() && !initialized)
    assert(levels[pin]==LOW && "preload inactive backlight before enabling output");
  if (pin==SDCARD_CS && mode==OUTPUT && automatic())
    assert(levels[pin]==HIGH && "preload microSD deselect before enabling output");
  modes[pin]=mode;
}
inline void digitalWrite(int pin,int value) {
  check_pin(pin); ++events;
  if (pin==PIN_TFT_LEDA_CTL && automatic() && value==HIGH)
    assert(initialized && "backlight must remain dark until LCD init completes");
  if (pin==PIN_TFT_RST) {
    if (value==LOW && levels[pin]==HIGH) reset_low_at=now_us;
    if (value==HIGH && levels[pin]==LOW) {
      reset_low_us=now_us-reset_low_at; reset_high_at=now_us; ++resets;
    }
  }
  if (pin==PIN_TFT_CS && value==LOW && levels[pin]==HIGH && automatic() && spi_begins==0) {
    assert(now_us-reset_high_at>=120000 && "wait for reset/NVM before RDDID");
    assert(reset_low_us>=10000 && levels[PIN_TFT_LEDA_CTL]==LOW);
    assert(levels[40]==HIGH && "shared peripheral rail must be claimed");
    transaction=true; ++transactions; command_bits=clocks=data_bits=command=0;
    const unsigned within=(transactions-1)%6;
    response=0x858552;
    if (scenario=="floating") response=modes[PIN_TFT_MISO]==INPUT_PULLUP ? 0xFFFFFF : 0;
    if (scenario=="high") response=0xFFFFFF;
    if (scenario=="low") response=0;
    if (scenario=="biased") response=modes[PIN_TFT_MISO]==INPUT_PULLUP ? 0x858552 : 0x123456;
    if (scenario=="noise") response=within==2 ? 0x858553 : 0x858552;
    if (scenario=="recover" && transactions<=6) response=0;
    if (scenario=="prime" && within%3==0) response=0;
    if (scenario=="programmable") response=0x123456;
  }
  if (pin==PIN_TFT_SCL && value==HIGH && transaction) {
    assert(levels[SDCARD_CS]==HIGH && modes[SDCARD_CS]==OUTPUT && "deselect shared microSD before LCD clocks");
    ++clocks;
    if (clocks<=8) {
      assert(levels[PIN_TFT_DC]==LOW && modes[PIN_TFT_SDA]==OUTPUT);
      command=(command<<1)|levels[PIN_TFT_SDA]; ++command_bits;
    }
  }
  if (pin==PIN_TFT_CS && value==HIGH && transaction) {
    assert(command==0x04 && command_bits==8 && data_bits==24 && clocks==33);
    transaction=false;
  }
  levels[pin]=value;
}
inline int digitalRead(int pin) {
  assert(pin==PIN_TFT_MISO && "read dedicated LCD SDO, never MOSI/touch/I2C");
  assert(transaction && command==0x04 && levels[PIN_TFT_DC]==HIGH);
  assert(modes[pin]==((transactions-1)%6<3 ? INPUT_PULLUP : INPUT_PULLDOWN));
  assert(clocks==9+data_bits && levels[PIN_TFT_SCL]==LOW && data_bits<24);
  ++reads; return (response>>(23-data_bits++))&1;
}
inline void delay(unsigned ms) { now_us+=uint64_t(ms)*1000; }
inline void delayMicroseconds(unsigned us) { now_us+=us; }
'''

SPI = r'''
#pragma once
#include <Arduino.h>
struct SPIClass {
  bool live=false;
  explicit SPIClass(int=0) {}
#ifdef TEST_BOOL_SPI_BEGIN
  bool begin(int clk,int miso,int mosi,int cs) {
#else
  void begin(int clk,int miso,int mosi,int cs) {
#endif
    assert(clk==PIN_TFT_SCL && miso==PIN_TFT_MISO && mosi==PIN_TFT_SDA && cs==PIN_TFT_CS);
    if (automatic()) assert(transactions>=6 && levels[PIN_TFT_LEDA_CTL]==LOW);
    ++spi_begins;
    live=scenario!="spi-fail";
#ifdef TEST_BOOL_SPI_BEGIN
    return live;
#endif
  }
  void* bus() { return live ? this : nullptr; }
  void end() { ++spi_ends; live=false; }
};
inline SPIClass SPI;
'''

PANEL = r'''
#pragma once
#include <SPI.h>
#define ST77XX_BLACK 0
#define ST77XX_WHITE 0xffff
class Adafruit_ST7789 {
  int _w=240, _h=320;
public:
  template<class... T> explicit Adafruit_ST7789(T...) {}
  void init(int w,int h) {
    if (automatic()) assert(spi_begins==1 && transactions>=6 && levels[PIN_TFT_LEDA_CTL]==LOW);
    ++init_calls; initialized=true; _w=w; _h=h;
  }
  void setRotation(int rotation) { assert(initialized); _w=rotation&1 ? 320 : 240; _h=rotation&1 ? 240 : 320; ++draws; ++rotations; last_rotation=rotation; }
  int width() const { return _w; }
  int height() const { return _h; }
  void setSPISpeed(double) { assert(initialized); }
  void fillScreen(uint16_t) { assert(initialized); ++draws; }
  void setTextColor(uint16_t) { assert(initialized); ++draws; }
  void setTextSize(int) { assert(initialized); ++draws; }
  void setTextWrap(bool) { assert(initialized); ++draws; }
  void cp437(bool) { assert(initialized); ++draws; }
  void setCursor(int,int) { assert(initialized); ++draws; }
  void print(const char*) { assert(initialized); ++draws; }
  void fillRect(int,int,int,int,uint16_t) { assert(initialized); ++draws; }
  void drawRect(int,int,int,int,uint16_t) { assert(initialized); ++draws; }
  void drawBitmap(int,int,const uint8_t*,int,int,uint16_t) { assert(initialized); ++draws; }
  void getTextBounds(const char* s,int,int,int16_t* x,int16_t* y,uint16_t* w,uint16_t* h) {
    assert(initialized); ++draws; *x=*y=0; *w=strlen(s)*6; *h=8;
  }
};
'''

MAIN = r'''
#include "ST7789LCDDisplay.h"
void frame(ST7789LCDDisplay& d) {
  const uint8_t bits[]={0x80};
  d.clear(); d.startFrame(); d.setTextSize(2); d.setColor(1); d.setCursor(0,0);
  d.print("text"); d.fillRect(0,0,1,1); d.drawRect(0,0,1,1);
  d.drawXbm(0,0,bits,1,1); d.endFrame(); (void)d.getTextWidth("text");
}
int main(int argc,char** argv) {
  assert(argc==2); scenario=argv[1];
  RefCountedDigitalPin rail(40); rail.begin();
  const bool other_owner=scenario!="unshared";
  if (other_owner) rail.claim();
  ST7789LCDDisplay d(&rail);
  if (!automatic()) {
    assert(d.begin() && d.isOn()); frame(d);
    assert(reads==0 && transactions==0 && init_calls==1);
    return 0;
  }
  const auto before=events; frame(d); d.setFlipped(true);
  assert(events==before && draws==0 && init_calls==0);
  // Simulate a warm reset after a card transaction left DAT3/CS asserted.
  if (scenario=="sd-selected") { modes[SDCARD_CS]=OUTPUT; levels[SDCARD_CS]=LOW; }
  const bool absent=scenario=="floating" || scenario=="high" || scenario=="low"
      || scenario=="noise" || scenario=="biased" || scenario=="unshared";
  if (scenario=="unshared") scenario="floating";
  const bool begun=d.begin();
  if (absent || scenario=="spi-fail") {
    assert(!begun && !d.isOn() && "invalid LCD must remain headless");
    assert(init_calls==0 && spi_begins==(scenario=="spi-fail" ? 1u : 0u) && draws==0);
    assert(spi_ends==(scenario=="spi-fail" ? 1u : 0u));
    assert(levels[PIN_TFT_RST]==HIGH && "shared touch reset must not remain LOW");
    assert(levels[PIN_TFT_LEDA_CTL]==LOW && levels[40]==other_owner);
    for (int pin:{PIN_TFT_CS,PIN_TFT_DC,PIN_TFT_SDA,PIN_TFT_SCL,PIN_TFT_MISO}) assert(modes[pin]==INPUT);
    const auto count=events; frame(d); d.turnOn(); d.turnOff(); d.setFlipped(false);
    assert(!d.begin() && events==count && draws==0 && reads==24*transactions);
    assert(transactions==(scenario=="spi-fail" ? 6u : 12u) && now_us<450000);
    assert(levels[SDCARD_CS]==HIGH && modes[SDCARD_CS]==OUTPUT);
    if (other_owner) { rail.release(); assert(levels[40]==LOW && "probe must not leak its power claim"); }
    return 0;
  }
  assert(begun && d.isOn() && init_calls==1 && spi_begins==1 && initialized);
  assert(last_rotation==((DISPLAY_ROTATION+2)&3));
  assert(transactions==(scenario=="recover" ? 12u : 6u));
  assert(reads==24*transactions && levels[PIN_TFT_LEDA_CTL]==HIGH);
  assert(levels[SDCARD_CS]==HIGH && modes[SDCARD_CS]==OUTPUT);
  frame(d); const auto painted=draws, reset_count=resets, probes=transactions;
  assert(d.begin() && init_calls==1 && transactions==probes);
  d.turnOff(); assert(!d.isOn() && levels[PIN_TFT_RST]==HIGH && levels[PIN_TFT_LEDA_CTL]==LOW);
  assert(levels[40]==HIGH && "display off must retain the other peripheral claim");
  frame(d); d.setFlipped(false); assert(draws==painted);
  for (unsigned i=0;i<100;++i) { d.turnOn(); assert(d.isOn()); d.turnOff(); }
  assert(resets==reset_count && init_calls==1 && spi_begins==1 && transactions==probes);
  assert(rotations==2 && last_rotation==(DISPLAY_ROTATION&3) && "apply deferred flip once on wake");
  d.turnOn(); frame(d); assert(draws>painted);
  d.turnOff(); rail.release(); assert(levels[40]==LOW && "wake/sleep claims must remain balanced");
}
'''


class HeltecR8DisplayDetectionTest(unittest.TestCase):
    def build(self, directory, flags=(), mutation=None, expect_failure=False):
        directory.mkdir()
        for name in ("ST7789LCDDisplay.h", "ST7789LCDDisplay.cpp", "HeltecR8DisplayProbe.h"):
            source = (UI / name).read_text()
            if name == "HeltecR8DisplayProbe.h" and mutation:
                old, new = mutation
                self.assertIn(old, source)
                source = source.replace(old, new)
            (directory / name).write_text(source)
        for name, source in {"Arduino.h": ARDUINO, "SPI.h": SPI,
                             "Wire.h": "#pragma once\nclass TwoWire {};\n",
                             "Adafruit_GFX.h": "#pragma once\n",
                             "Adafruit_ST7789.h": PANEL, "main.cpp": MAIN}.items():
            (directory / name).write_text(source)
        if "-DLILYGO_TDECK" in flags:
            from test_tdeck_display_cooperation import extend_legacy_stubs
            extend_legacy_stubs(directory)
        binary = directory / ("probe.exe" if os.name == "nt" else "probe")
        sanitizer = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
                     if sys.platform.startswith("linux") else [])
        compiled = subprocess.run([
            os.environ.get("CXX", "g++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-parameter", *sanitizer, *flags,
            "-I" + str(directory), "-I" + str(UI), "-I" + str(ROOT / "src"),
            str(directory / "main.cpp"), str(directory / "ST7789LCDDisplay.cpp"), "-o", str(binary)
        ], capture_output=True, text=True, timeout=60)
        if expect_failure:
            self.assertNotEqual(compiled.returncode, 0)
            self.assertIn("R8 display detection requires LCD SDO", compiled.stderr)
        else:
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
        return binary

    def run_case(self, binary, scenario, negative=None):
        ran = subprocess.run([str(binary), scenario], capture_output=True, text=True, timeout=10)
        if negative:
            self.assertNotEqual(ran.returncode, 0)
            self.assertIn(negative, ran.stderr)
        else:
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_absent_floating_stuck_unstable_and_power_ownership(self):
        with tempfile.TemporaryDirectory(prefix="heltec-r8-display-") as temp:
            binary = self.build(Path(temp) / "auto", ["-DHELTEC_V4_R8_TFT", "-DHELTEC_TFT_DISPLAY_AUTODETECT=1"])
            self.assertIn(b"meshcore.capability.display.autodetect.heltec_r8.v1", binary.read_bytes())
            for scenario in ("floating", "high", "low", "noise", "biased", "unshared", "spi-fail"):
                with self.subTest(scenario=scenario): self.run_case(binary, scenario)

    def test_panel_ids_retry_and_shared_touch_reset_sleep_wake(self):
        with tempfile.TemporaryDirectory(prefix="heltec-r8-display-") as temp:
            for api in ("void", "bool"):
                flags = ["-DHELTEC_V4_R8_TFT", "-DHELTEC_TFT_DISPLAY_AUTODETECT=1"]
                if api == "bool": flags.append("-DTEST_BOOL_SPI_BEGIN")
                binary = self.build(Path(temp) / api, flags)
                for scenario in ("valid", "programmable", "prime", "recover", "sd-selected", "spi-fail"):
                    with self.subTest(api=api, scenario=scenario): self.run_case(binary, scenario)

    def test_other_boards_and_explicitly_disabled_r8_keep_existing_behavior(self):
        with tempfile.TemporaryDirectory(prefix="heltec-r8-display-") as temp:
            for index, flags in enumerate(([], ["-DHELTEC_V4_R8_TFT"],
                                          ["-DHELTEC_V4_R8_TFT", "-DHELTEC_TFT_DISPLAY_AUTODETECT=0"],
                                          ["-DHELTEC_LORA_V4_TFT", "-DHELTEC_TFT_DISPLAY_AUTODETECT=1"],
                                          ["-DLILYGO_TDECK", "-DHELTEC_TFT_DISPLAY_AUTODETECT=1"])):
                binary = self.build(Path(temp) / str(index), flags)
                self.run_case(binary, "valid")
                self.assertNotIn(b"meshcore.capability.display.autodetect.heltec_r8.v1", binary.read_bytes())

    def test_negative_controls_detect_bias_settle_and_wrong_read_pin(self):
        flags = ["-DHELTEC_V4_R8_TFT", "-DHELTEC_TFT_DISPLAY_AUTODETECT=1"]
        with tempfile.TemporaryDirectory(prefix="heltec-r8-display-") as temp:
            cases = [("bias", ("ids[0] == ids[1]", "true"), "biased", "invalid LCD must remain headless"),
                     ("settle", ("delay(120);", "delay(0);"), "valid", "wait for reset/NVM before RDDID"),
                     ("pin", ("digitalRead(PIN_TFT_MISO)", "digitalRead(PIN_TFT_SDA)"), "valid", "read dedicated LCD SDO"),
                     ("sd", ("  digitalWrite(SDCARD_CS, HIGH);\n  pinMode(SDCARD_CS, OUTPUT);", ""), "sd-selected", "deselect shared microSD before LCD clocks")]
            for name, mutation, scenario, message in cases:
                binary = self.build(Path(temp) / name, flags, mutation)
                self.run_case(binary, scenario, negative=message)
            self.build(Path(temp) / "no-miso", flags + ["-DPIN_TFT_MISO=-1"], expect_failure=True)


if __name__ == "__main__":
    unittest.main()
