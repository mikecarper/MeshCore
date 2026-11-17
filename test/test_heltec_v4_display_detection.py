"""Exercise the unified base-V4 display selector and hardware probes."""

from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_serial_wifi_sessions import SANITIZER_FLAGS
from test_display_power import FS_STUB
from cpp_source import body


ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "src/helpers/ui"

POLICY_TEST = r'''
#include <cassert>
#include <cstring>
#include <initializer_list>
#include <helpers/ui/HeltecV4DisplayPolicy.h>
using namespace mesh::ui;
int main() {
  const HeltecV4DisplayMode modes[] = {HeltecV4DisplayMode::Auto,
      HeltecV4DisplayMode::Oled, HeltecV4DisplayMode::Tft, HeltecV4DisplayMode::Off};
  const char* names[] = {"auto", "oled", "tft", "off"};
  for (unsigned index = 0; index < 4; ++index) {
    assert(strcmp(heltecV4DisplayModeName(modes[index]), names[index]) == 0);
    HeltecV4DisplayMode parsed = HeltecV4DisplayMode::Off;
    assert(parseHeltecV4DisplayMode(names[index], parsed) && parsed == modes[index]);
    for (bool oled : {false, true}) {
      for (bool tft : {false, true}) {
        auto expected = HeltecV4DisplayType::None;
        if (modes[index] == HeltecV4DisplayMode::Oled) expected = HeltecV4DisplayType::Oled;
        if (modes[index] == HeltecV4DisplayMode::Tft) expected = HeltecV4DisplayType::Tft;
        if (modes[index] == HeltecV4DisplayMode::Auto)
          expected = oled ? HeltecV4DisplayType::Oled
              : tft ? HeltecV4DisplayType::Tft : HeltecV4DisplayType::None;
        assert(selectHeltecV4Display(modes[index], oled, tft) == expected);
      }
    }
  }
  for (const char* invalid : {"", "Auto", "OLED", "none", "on", "1", "tft extra", "oled ", " auto"}) {
    auto unchanged = HeltecV4DisplayMode::Tft;
    assert(!parseHeltecV4DisplayMode(invalid, unchanged));
    assert(unchanged == HeltecV4DisplayMode::Tft && "invalid values must not mutate the saved choice");
  }
  assert(strcmp(heltecV4DisplayTypeName(HeltecV4DisplayType::Oled), "oled") == 0);
  assert(strcmp(heltecV4DisplayTypeName(HeltecV4DisplayType::Tft), "tft") == 0);
  assert(strcmp(heltecV4DisplayTypeName(HeltecV4DisplayType::None), "none") == 0);
  auto unchanged = HeltecV4DisplayMode::Off;
  assert(!parseHeltecV4DisplayMode(nullptr, unchanged) && unchanged==HeltecV4DisplayMode::Off);
}
'''

TOUCH_TEST = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <initializer_list>
#include <string>
#include <vector>
#include <helpers/ui/HeltecV4DisplayProbe.h>
using namespace mesh::ui;
struct Io {
  std::string scenario;
  unsigned resets=0, writes=0, reads=0, delay=0, index=0;
  std::vector<unsigned> waits;
  explicit Io(const char* value) : scenario(value) {}
  void reset() { ++resets; index=0; }
  void delayMs(unsigned value) { waits.push_back(value); delay+=value; }
  bool write(const uint8_t* bytes, size_t count) {
    static const uint8_t expected[] = {0x42,0xBD,0x28,0x35,0xC1,0x00,0x35,0xAE};
    assert(count==sizeof(expected) && memcmp(bytes,expected,count)==0);
    ++writes;
    return scenario!="write-failed";
  }
  bool readRegister(uint16_t reg, uint8_t* bytes, size_t count) {
    static const uint16_t registers[] = {0x0001,0x9E6B,0x9E00,0x9E4C};
    static const size_t sizes[] = {1,1,4,4};
    assert(reg==registers[index%4] && count==sizes[index%4]);
    const unsigned sample=index/4;
    ++reads; ++index;
    if(scenario=="read-failed" || (scenario=="recover" && writes==1)) return false;
    memset(bytes,0,count);
    if(reg==0x0001) bytes[0]=scenario=="alternate-address" ? 0x5c : 0x2e;
    if(reg==0x9E6B) bytes[0]=scenario=="zero-chip" ? 0 : 0x20;
    if(reg==0x9E00) { bytes[0]=0x81;bytes[1]=0x52;bytes[2]=0x19;bytes[3]=0x03; }
    if(reg==0x9E4C) {
      const bool landscape=scenario=="landscape";
      const unsigned width=scenario=="independent-touch-resolution" ? 360 : landscape ? 320 : 240;
      const unsigned height=scenario=="independent-touch-resolution" ? 360 : landscape ? 240 : 320;
      bytes[0]=width&255;bytes[1]=width>>8;bytes[2]=height&255;bytes[3]=height>>8;
    }
    if(scenario=="zero") memset(bytes,0,count);
    if(scenario=="high") memset(bytes,255,count);
    if(scenario=="wrong-address" && reg==0x0001) bytes[0]=0x3c;
    if(scenario=="invalid-chip" && reg==0x9E6B) bytes[0]=255;
    if(scenario=="zero-config" && reg==0x9E00) memset(bytes,0,count);
    if(scenario=="high-config" && reg==0x9E00) memset(bytes,255,count);
    if(scenario=="wrong-size" && reg==0x9E4C) bytes[0]=128;
    if(scenario=="noise" && reg==0x9E00 && sample%2) bytes[0]^=1;
    if(scenario=="missing-read" && reg==0x9E4C) return false;
    return true;
  }
};
int main() {
  for(const char* scenario : {"valid","alternate-address","landscape","recover","zero-chip",
      "independent-touch-resolution",
      "write-failed","read-failed","missing-read","zero","high","wrong-address",
      "invalid-chip","zero-config","high-config","wrong-size","noise"}) {
    Io io{scenario};
    HeltecV4TouchIdentity identity{};
    identity.address=0x91;
    const bool valid=io.scenario=="valid" || io.scenario=="alternate-address"
        || io.scenario=="landscape" || io.scenario=="recover" || io.scenario=="zero-chip"
        || io.scenario=="independent-touch-resolution";
    assert(probeHeltecV4Touch(io,&identity)==valid);
    assert(io.resets>=2 && io.resets<=3 && io.writes>=1 && io.writes<=2);
    assert(io.waits.back()>=30 && "DMA access must be exited by reset on every path");
    assert(io.reads<=16 && io.delay<250 && "startup probe must be bounded when absent");
    if(valid) {
      assert(identity.address==(io.scenario=="alternate-address" ? 0x5c : 0x2e));
      assert(identity.width==(io.scenario=="independent-touch-resolution" ? 360
          : io.scenario=="landscape" ? 320 : 240));
      assert(identity.height==(io.scenario=="independent-touch-resolution" ? 360
          : io.scenario=="landscape" ? 240 : 320));
      assert(identity.config==0x03195281u && identity.chip==(io.scenario=="zero-chip" ? 0 : 0x20));
    } else assert(identity.address==0x91 && "failed detection must not publish a partial identity");
  }
  Io no_output{"valid"};
  assert(probeHeltecV4Touch(no_output));
}
'''

SETTINGS_DISPLAY = r'''
#pragma once
#include <helpers/ui/HeltecV4DisplayPolicy.h>
class HeltecV4Display {
  mesh::ui::HeltecV4DisplayMode _mode=mesh::ui::HeltecV4DisplayMode::Auto;
public:
  unsigned applied=0;
  bool oled=true,tft=false;
  auto mode() const { return _mode; }
  void setConfigurationFault(bool) {}
  void setMode(mesh::ui::HeltecV4DisplayMode mode) { _mode=mode; ++applied; }
  const char* status() const {
    if(_mode==mesh::ui::HeltecV4DisplayMode::Off) return "off";
    const auto type=mesh::ui::selectHeltecV4Display(_mode,oled,tft);
    if(type==mesh::ui::HeltecV4DisplayType::Oled) return "OLED";
    if(type==mesh::ui::HeltecV4DisplayType::Tft) return "TFT";
    return "none detected";
  }
};
'''

SETTINGS_TEST = r'''
#include <cassert>
#include <cstdio>
#include <initializer_list>
#include <HeltecV4DisplaySettings.h>
using namespace mesh::ui;
int main() {
  FakeFS fs; HeltecV4Display display; char reply[160];
  const auto command=[&](const char* text) {
    memset(reply,0,sizeof(reply));
    return handleHeltecV4DisplayCommand(text,reply,sizeof(reply),display);
  };
  assert(!loadHeltecV4DisplaySettings(&fs,display));
  assert(display.mode()==HeltecV4DisplayMode::Auto);
  assert(command("get display.type") && strstr(reply,"auto") && strstr(reply,"active OLED"));
  assert(!command("get display.type.extra") && !command("set display.mode off"));
  for(const char* invalid : {"set display.type", "set display.type ", "set display.type none",
      "set display.type OLED", "set display.type tft extra", "set display.type off ",
      "get display.type off"}) {
    assert(command(invalid) && strstr(reply,"Error:"));
    assert(display.mode()==HeltecV4DisplayMode::Auto && fs.files.empty());
  }
  for(const char* mode : {"oled","tft","off","auto"}) {
    char input[80]; snprintf(input,sizeof(input),"set display.type %s",mode);
    assert(command(input) && strstr(reply,"OK"));
    assert(fs.files["/display_type"].size()==6);
    HeltecV4DisplayMode parsed;
    assert(parseHeltecV4DisplayMode(mode,parsed) && display.mode()==parsed);
    HeltecV4Display rebooted;
    assert(loadHeltecV4DisplaySettings(&fs,rebooted) && rebooted.mode()==parsed);
  }
  assert(command("set display.type off") && strstr(reply,"active off"));
  auto original=fs.files["/display_type"];
  const auto before=display.applied;
  fs.fail_write=true;
  assert(command("set display.type tft") && strstr(reply,"save failed"));
  assert(display.mode()==HeltecV4DisplayMode::Off && display.applied==before);
  assert(fs.files["/display_type"]==original);
  fs.fail_write=false; fs.corrupt_write=true;
  assert(command("set display.type tft") && strstr(reply,"save failed"));
  assert(display.mode()==HeltecV4DisplayMode::Off && fs.files["/display_type"]==original);
  fs.corrupt_write=false; fs.fail_publish=true;
  assert(command("set display.type tft") && strstr(reply,"save failed"));
  assert(display.mode()==HeltecV4DisplayMode::Off && fs.files["/display_type"]==original);
  fs.fail_publish=false;
  assert(loadHeltecV4DisplaySettings(&fs,display) && display.mode()==HeltecV4DisplayMode::Off);
  fs.files["/display_type.bak"]=original;
  fs.files["/display_type"][5]^=1;
  assert(loadHeltecV4DisplaySettings(&fs,display) && display.mode()==HeltecV4DisplayMode::Off);
  assert(fs.files["/display_type"]==original && !fs.exists("/display_type.bak"));
  for(unsigned field : {2,3,4,5}) {
    auto invalid=original;
    invalid[field]=field==3 ? 255 : invalid[field]^0x80;
    if(field!=5) { invalid[5]=0; for(unsigned i=0;i<5;++i) invalid[5]^=invalid[i]; }
    fs.files["/display_type"]=invalid;
    assert(!loadHeltecV4DisplaySettings(&fs,display));
    assert(display.mode()==HeltecV4DisplayMode::Off);
  }
  assert(!loadHeltecV4DisplaySettings(nullptr,display));
  const auto no_fs=display.applied;
  assert(command("set display.type off") && strstr(reply,"save failed"));
  assert(display.mode()==HeltecV4DisplayMode::Off && display.applied==no_fs);
  assert(!loadHeltecV4DisplaySettings(&fs,display));
  assert(command("set display.type off") && display.mode()==HeltecV4DisplayMode::Off);
  fs.files.clear(); // Factory reset erases this file with the rest of SPIFFS.
  assert(!loadHeltecV4DisplaySettings(&fs,display) && display.mode()==HeltecV4DisplayMode::Auto);
}
'''

WRAPPER_ARDUINO = r'''
#pragma once
#include <cassert>
#include <cstdint>
#include <cstring>
#include <string>
#define LOW 0
#define HIGH 1
#define INPUT 0
#define OUTPUT 1
#define PIN_TFT_CS 15
#define PIN_TFT_DC 16
#define PIN_TFT_SDA 33
#define PIN_TFT_SCL 17
#define PIN_TFT_RST 18
#define PIN_TFT_LEDA_CTL 21
#define PIN_TFT_LEDA_CTL_ACTIVE HIGH
#define PIN_OLED_RESET 21
#define DISPLAY_ADDRESS 0x3C
inline int modes[64]={},levels[64]={};
inline unsigned gpio_events=0,output_events=0,wait_ms=0,oled_begins=0,tft_begins=0,draws=0,bus_releases=0;
inline unsigned last_color=0,last_background=0;
inline bool oled_present=false,tft_present=false,fail_oled=false,fail_tft=false;
inline std::string scenario;
inline void pinMode(int pin,int mode) {
  assert((pin==15 || pin==16 || pin==33 || pin==17 || pin==18 || pin==21
      || pin==47 || pin==48 || pin==44 || pin==36) && "never drive radio/GNSS/global-I2C pins");
  ++gpio_events;if(mode==OUTPUT) ++output_events; modes[pin]=mode;
}
inline void digitalWrite(int pin,int value) { ++gpio_events; ++output_events; levels[pin]=value; }
inline void delay(unsigned milliseconds) { wait_ms+=milliseconds; }
'''

WRAPPER_WIRE = r'''
#pragma once
#include <Arduino.h>
#include <vector>
#define I2C_BUFFER_LENGTH 128
class TwoWire {
  int id,sda=-1,scl=-1;
  uint8_t address=0;
  uint16_t reg=0;
  std::vector<uint8_t> output,input;
  size_t cursor=0;
public:
  inline static unsigned begins=0,ends=0,global_events=0,touch_reads=0;
  explicit TwoWire(int bus=0) : id(bus) {}
  bool begin(int data,int clock,uint32_t hz) {
    assert(id==1 && "leave the RTC/environment Wire(0) untouched");
    assert((data==17 && clock==18) || (data==47 && clock==48));
    assert(hz==100000); ++begins; sda=data;scl=clock;
    if(scenario=="bus-failed") return false;
    modes[sda]=modes[scl]=OUTPUT;
    return true;
  }
  void end() { assert(id==1); ++ends; modes[sda]=modes[scl]=INPUT; }
  void setTimeOut(unsigned value) { assert(value<=20); }
  void beginTransmission(uint8_t value) { assert(id==1); address=value; output.clear(); }
  size_t write(const uint8_t* bytes,size_t count) {
    output.assign(bytes,bytes+count);
    return scenario=="short-write" ? count-1 : count;
  }
  uint8_t endTransmission(bool stop=true) {
    assert(id==1);
    assert(stop && "CHSC DMA reads require STOP after the register address");
    if(address==0x3c) { assert(sda==17 && scl==18); return oled_present ? 0 : 2; }
    assert(address==0x2e && sda==47 && scl==48);
    if(output.size()==2) reg=unsigned(output[0])<<8 | output[1];
    if(output.size()==8) {
      static const uint8_t dma[]={0x42,0xbd,0x28,0x35,0xc1,0,0x35,0xae};
      assert(memcmp(output.data(),dma,8)==0);
    }
    return tft_present || scenario=="ack-only" ? 0 : 2;
  }
  size_t requestFrom(uint8_t address,uint8_t count) {
    assert(address==0x2e); ++touch_reads; input.assign(count,0);cursor=0;
    if(scenario!="ack-only") {
      if(reg==1) input[0]=0x2e;
      if(reg==0x9e6b) input[0]=0x10;
      if(reg==0x9e00) input[0]=0x81;
      if(reg==0x9e4c) { input[0]=0xf0;input[1]=0;input[2]=0x40;input[3]=1; }
    }
    return scenario=="short-read" ? count-1 : count;
  }
  int available() { return input.size()-cursor; }
  int read() { assert(cursor<input.size()); return input[cursor++]; }
};
inline TwoWire Wire(0);
'''

WRAPPER_BACKEND = r'''
#pragma once
#include <Arduino.h>
#include <Wire.h>
#include <helpers/RefCountedDigitalPin.h>
#include "DisplayDriver.h"
class RecordingDisplay : public DisplayDriver {
protected:
  bool on=false;
  int default_w,default_h;
public:
  RecordingDisplay(int w,int h) : DisplayDriver(w,h),default_w(w),default_h(h) {}
  bool isOn() override { return on; }
  bool supportsRotation() const override { return true; }
  bool setRotationDegrees(uint16_t degrees) override {
    setDimensions(degrees%180 ? default_h : default_w,degrees%180 ? default_w : default_h); return true;
  }
  void turnOn() override { on=true; }
  void turnOff() override { on=false; }
  void clear() override { assert(on); ++draws; }
  void startFrame(ColorVal bkg) override { assert(on); last_background=bkg; ++draws; }
  void setTextSize(int) override {}
  void setColor(ColorVal color) override { last_color=color; }
  void setCursor(int,int) override { assert(on); ++draws; }
  void print(const char*) override { assert(on); ++draws; }
  void fillRect(int,int,int,int) override { assert(on); ++draws; }
  void drawRect(int,int,int,int) override { assert(on); ++draws; }
  void drawXbm(int,int,const uint8_t*,int,int) override { assert(on); ++draws; }
  uint16_t getTextWidth(const char* text) override { return strlen(text)*6; }
  void endFrame() override { assert(on); ++draws; }
};
class SSD1306Display : public RecordingDisplay {
  TwoWire* wire;
public:
  static constexpr size_t FRAMEBUFFER_BYTES=1024;
  SSD1306Display(RefCountedDigitalPin*,TwoWire* value) : RecordingDisplay(128,64),wire(value) {}
  bool begin() { ++oled_begins; wire->beginTransmission(0x3c); on=wire->endTransmission()==0&&!fail_oled; return on; }
};
class ST7789LCDDisplay : public RecordingDisplay {
public:
  explicit ST7789LCDDisplay(RefCountedDigitalPin*) : RecordingDisplay(320,240) {}
  bool begin() { ++tft_begins; on=!fail_tft; return on; }
  void releaseBus() { ++bus_releases; on=false; }
};
'''

WRAPPER_TEST = r'''
#include <cassert>
#include <initializer_list>
#include "HeltecV4Display.h"
using namespace mesh::ui;
void frame(HeltecV4Display& display) {
  static const uint8_t bits[]={128};
  display.clear();display.startFrame();display.setTextSize(1);display.setColor(1);
  display.setCursor(0,0);display.print("hello");display.fillRect(0,0,1,1);
  display.drawRect(0,0,1,1);display.drawXbm(0,0,bits,1,1);display.endFrame();
}
void parked(bool known_tft=false) {
  for(int pin : {15,16,33,17,18,21,47,48,44}) {
    if(known_tft && (pin==18 || pin==21))
      assert(modes[pin]==OUTPUT && levels[pin]==LOW && "known TFT must retain reset/backlight LOW");
    else assert(modes[pin]==INPUT);
  }
}
int main() {
  {
    // A saved Off choice must never probe or enable any display GPIO at boot.
    HeltecV4Display display;
    assert(display.setMode(HeltecV4DisplayMode::Off));
    assert(!display.begin() && strcmp(display.status(),"off")==0);
    assert(gpio_events==0 && TwoWire::begins==0);
    frame(display);display.turnOn();display.turnOff();
    assert(display.setMode(HeltecV4DisplayMode::Off));
    assert(output_events==0 && TwoWire::begins==0 && !display.isOn());
  }
  for(const char* input : {"absent","ack-only","short-read","short-write","bus-failed",
      "oled","both","tft","oled-init-failed","tft-init-failed"}) {
    scenario=input; oled_present=scenario=="oled" || scenario=="both" || scenario=="oled-init-failed";
    tft_present=scenario=="tft" || scenario=="both" || scenario=="short-read"
        || scenario=="short-write" || scenario=="tft-init-failed";
    fail_oled=scenario=="oled-init-failed";fail_tft=scenario=="tft-init-failed";
    RefCountedDigitalPin rail(36);rail.begin();rail.claim();
    HeltecV4Display display(&rail);
    const bool ready=scenario=="oled" || scenario=="both" || scenario=="tft";
    const auto touch_before=TwoWire::touch_reads,oled_before=oled_begins,tft_before=tft_begins;
    assert(display.begin()==ready && display.isOn()==ready);
    if(!ready) {
      assert(display.selectedType()==HeltecV4DisplayType::None);
      assert(strstr(display.status(),"none") || strstr(display.status(),"failed"));
      parked(scenario=="tft-init-failed"); const auto painted=draws;frame(display);display.turnOn();
      assert(draws==painted && !display.isOn());
      assert(levels[36]==HIGH && "failed probe must preserve another owner's rail claim");
    } else {
      assert(display.selectedType()==(oled_present ? HeltecV4DisplayType::Oled : HeltecV4DisplayType::Tft));
      assert(strcmp(display.status(),oled_present ? "OLED" : "TFT")==0);
      if(oled_present) {
        assert(TwoWire::touch_reads==touch_before && tft_begins==tft_before && oled_begins==oled_before+1);
        display.setColor(0xf800);assert(last_color==1);
        display.startFrame(0);assert(last_background==0);
      } else {
        assert(TwoWire::touch_reads>touch_before && tft_begins==tft_before+1 && oled_begins==oled_before);
        display.setColor(0xf800);assert(last_color==0xf800);
        assert(modes[47]==INPUT && modes[48]==INPUT && levels[44]==HIGH);
      }
      assert(display.setRotationDegrees(90));
      assert(display.width()==(oled_present ? 64 : 240) && display.height()==(oled_present ? 128 : 320));
      assert(!display.setRotationDegrees(45));
      frame(display);
      display.turnOff();const auto painted=draws;frame(display);assert(draws==painted);
      parked(!oled_present);
      const auto begins_before_wake=oled_begins+tft_begins;
      display.turnOn();
      assert(display.isOn() && oled_begins+tft_begins==begins_before_wake+1);
      assert(display.width()==(oled_present ? 64 : 240) && display.height()==(oled_present ? 128 : 320));
      frame(display);
      assert(display.setMode(HeltecV4DisplayMode::Off));
      assert(strcmp(display.status(),"off")==0 && !display.isOn());parked(!oled_present);
      assert(display.setMode(HeltecV4DisplayMode::Off));parked(!oled_present);
    }
    rail.release();assert(levels[36]==LOW && "wrapper must balance every temporary power claim");
  }
  // Manual override is explicit, and returning to Auto can find another type.
  scenario="tft";oled_present=false;tft_present=true;fail_oled=fail_tft=false;
  HeltecV4Display display;
  assert(display.setMode(HeltecV4DisplayMode::Tft) && display.begin());
  assert(strstr(display.status(),"TFT") && strstr(display.status(),"forced"));
  const auto before=bus_releases;
  oled_present=true;tft_present=false;
  assert(display.setMode(HeltecV4DisplayMode::Auto));
  assert(display.selectedType()==HeltecV4DisplayType::Oled && strcmp(display.status(),"OLED")==0);
  assert(bus_releases>before && "release TFT SPI before remapping shared OLED pins");
  const auto mode=display.mode();
  assert(!display.setMode(HeltecV4DisplayMode(99)) && display.mode()==mode);
  assert(display.setMode(HeltecV4DisplayMode::Off));parked();
}
'''


class HeltecV4DisplayDetectionTests(unittest.TestCase):
    def compile_run(self, work, main, extra=(), files=None, flags=()):
        for name, contents in (files or {}).items():
            destination = work / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(contents, encoding="ascii")
        source = work / "main.cpp"
        source.write_text(main, encoding="ascii")
        binary = work / "check"
        compiled = subprocess.run([
            os.environ.get("CXX", "g++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
            *SANITIZER_FLAGS, *flags, "-I" + str(work), "-I" + str(ROOT / "src"),
            str(source), *map(str, extra), "-o", str(binary),
        ], capture_output=True, text=True, timeout=60)
        self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
        ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        return binary

    def test_auto_presence_matrix_manual_choices_and_invalid_values(self):
        with tempfile.TemporaryDirectory(prefix="heltec-v4-display-policy-") as directory:
            self.compile_run(Path(directory), POLICY_TEST)

    def test_touch_requires_stable_identity_and_resets_on_every_exit(self):
        with tempfile.TemporaryDirectory(prefix="heltec-v4-touch-probe-") as directory:
            self.compile_run(Path(directory), TOUCH_TEST)

    def test_saved_type_validation_live_apply_and_failed_save_rollback(self):
        with tempfile.TemporaryDirectory(prefix="heltec-v4-display-settings-") as directory:
            work = Path(directory)
            files = {"HeltecV4Display.h": SETTINGS_DISPLAY,
                     "helpers/IdentityStore.h": FS_STUB.replace("/display_prefs", "/display_type")}
            for name in ("HeltecV4DisplaySettings.h", "HeltecV4DisplaySettings.cpp"):
                files[name] = (UI / name).read_text()
            self.compile_run(work, SETTINGS_TEST, [work / "HeltecV4DisplaySettings.cpp"], files,
                             ["-DHELTEC_V4_DISPLAY_UNIFIED=1"])

    def test_board_name_uses_live_display_status_and_detected_fem_revision(self):
        board = (ROOT / "variants/heltec_v4/HeltecV4Board.cpp").read_text()
        method = body(board, "const char* HeltecV4Board::getManufacturerName() const")
        main = r'''
#include <cassert>
#include <cstdio>
#include <string>
#include <initializer_list>
#include "HeltecV4Display.h"
constexpr int KCT8103L_PA=3;
HeltecV4Display display;
class HeltecV4Board {
public:
  struct Fem { int type=0; int getFEMType() const { return type; } } loRaFEMControl;
  const char* getManufacturerName() const;
};
@METHOD@
int main() {
  using namespace mesh::ui;
  HeltecV4Board board;
  for(int fem : {0,KCT8103L_PA}) {
    board.loRaFEMControl.type=fem;
    const std::string prefix=fem==KCT8103L_PA ? "Heltec V4.3 " : "Heltec V4.2 ";
    for(auto mode : {HeltecV4DisplayMode::Auto,HeltecV4DisplayMode::Off,
        HeltecV4DisplayMode::Oled,HeltecV4DisplayMode::Tft}) {
      for(bool oled : {false,true}) for(bool tft : {false,true}) {
        display.oled=oled; display.tft=tft; display.setMode(mode);
        assert(board.getManufacturerName()==prefix+display.status());
      }
    }
    display.oled=display.tft=false;
    display.setMode(HeltecV4DisplayMode::Auto);
    assert(board.getManufacturerName()==prefix+"none detected");
    display.setMode(HeltecV4DisplayMode::Off);
    assert(board.getManufacturerName()==prefix+"off");
  }
}
'''.replace("@METHOD@", method)
        with tempfile.TemporaryDirectory(prefix="heltec-v4-live-board-name-") as directory:
            self.compile_run(Path(directory), main, files={"HeltecV4Display.h": SETTINGS_DISPLAY},
                             flags=["-DHELTEC_V4_DISPLAY_UNIFIED=1", "-DDISPLAY_CLASS=HeltecV4Display"])

    def test_real_wrapper_releases_pins_preserves_other_power_and_switches_live(self):
        with tempfile.TemporaryDirectory(prefix="heltec-v4-display-driver-") as directory:
            work = Path(directory)
            files = {"Arduino.h": WRAPPER_ARDUINO, "Wire.h": WRAPPER_WIRE,
                     "SSD1306Display.h": WRAPPER_BACKEND,
                     "ST7789LCDDisplay.h": '#pragma once\n#include "SSD1306Display.h"\n'}
            for name in ("HeltecV4Display.h", "HeltecV4Display.cpp", "HeltecV4DisplayPolicy.h",
                         "HeltecV4DisplayProbe.h", "DisplayDriver.h", "DisplayPowerPolicy.h",
                         "DisplayBuildFlags.h", "ColorTheme.h", "DisplayNightMode.h"):
                files[name] = (UI / name).read_text()
            self.compile_run(work, WRAPPER_TEST, [work / "HeltecV4Display.cpp"], files,
                             ["-DHELTEC_V4_DISPLAY_UNIFIED=1", "-Wno-unused-parameter"])

    def test_every_role_loads_before_probe_and_base_v4_retains_external_rail_ownership(self):
        for role in ("companion_radio", "simple_repeater", "simple_room_server", "simple_sensor"):
            setup = body((ROOT / "examples" / role / "main.cpp").read_text(), "void setup()")
            with self.subTest(role=role):
                self.assertLess(setup.index("loadHeltecV4DisplaySettings("), setup.index("display.begin()"))
        target = (ROOT / "variants/heltec_v4/target.cpp").read_text()
        # The original V4 screen has never owned the shared external-power
        # rail. Turning a screen off must not cut power to expansion sensors.
        self.assertNotIn("display(&board.periph_power)", target)
        self.assertRegex(target, r"DISPLAY_CLASS\s+display\((?:NULL|nullptr)\)")

    def test_actual_companion_loop_keeps_headless_services_and_live_selection(self):
        fixture = (ROOT / "test/fixtures/companion_ui_deadlines.cpp").read_text()
        prefix = fixture[:fixture.index("@LOOP@")]
        prefix = prefix.replace("namespace ui { enum class DisplayWake { Button }; }",
                                "namespace ui { enum class DisplayWake { Button }; "
                                "enum class HeltecV4DisplayType { None,Oled,Tft }; }")
        prefix = prefix.replace("struct Display {", r'''
struct Display {
  mesh::ui::HeltecV4DisplayType type=mesh::ui::HeltecV4DisplayType::None;
  unsigned epoch=1,rotations=0,angle=0;
  unsigned generation() const { return epoch; }
  auto selectedType() const { return type; }
  bool setRotationDegrees(unsigned value) { ++rotations;angle=value;return true; }
''')
        prefix = prefix.replace('const char* node_name = "test";',
                                'const char* node_name = "test"; unsigned display_rotation_degrees=90;')
        dependencies = r'''
void UITask::setCurrScreen(UIScreen* screen) { curr=screen; }
struct Buzzer {
  unsigned serviced=0;
  bool isPlaying() const { return true; }
  void loop() { ++serviced; }
} buzzer;
struct Vibration { unsigned serviced=0; void loop() { ++serviced; } } vibration;
'''
        loop = body((ROOT / "examples/companion_radio/ui-new/UITask.cpp").read_text(),
                    "void UITask::loop()")
        main = r'''
int main() {
  UITask ui;
  now_ms=10000;ui.voltage=3300;
  ui.loop();
  assert(ui._display==nullptr && ui.display.frames==0);
  assert(ui.shutdowns==1 && ui.battery_reads==1 && "headless mode must preserve battery protection");
  assert(buzzer.serviced==1 && vibration.serviced==1 && ui.screen.polls==1);
  ui.voltage=4000;now_ms=11000;
  ui.display.type=mesh::ui::HeltecV4DisplayType::Oled;++ui.display.epoch;
  ui.loop();
  assert(ui._display==&ui.display && ui.display.frames==1);
  assert(ui.display.angle==90 && ui.display.rotations==2);
  assert(buzzer.serviced==2 && vibration.serviced==2);
  now_ms=12000;ui.display.type=mesh::ui::HeltecV4DisplayType::None;++ui.display.epoch;
  ui.loop();
  assert(ui._display==nullptr && ui.display.frames==1);
  assert(buzzer.serviced==3 && vibration.serviced==3 && ui.screen.polls==3);
}
'''
        with tempfile.TemporaryDirectory(prefix="heltec-v4-headless-ui-services-") as directory:
            self.compile_run(Path(directory), prefix + dependencies + loop + main,
                             flags=["-DHELTEC_V4_DISPLAY_UNIFIED=1", "-DAUTO_SHUTDOWN_MILLIVOLTS=3400",
                                    "-DCOMPANION_FEATURE_READER=0", "-DPIN_BUZZER=1", "-DPIN_VIBRATION=2",
                                    "-DAUTO_OFF_MILLIS=0", "-Wno-unused-function"])

    def test_infrastructure_generation_forces_redraw_across_clock_wrap(self):
        for role in ("simple_repeater", "simple_room_server", "simple_sensor"):
            source = (ROOT / "examples" / role / "UITask.cpp").read_text()
            loop = body(source, "void UITask::loop()")
            prefix = loop[:loop.index("#if defined(HELTEC_V4_R8_TFT)")]
            due = ("assert(uint32_t(now-ui._last_refresh)>=1000u);" if role == "simple_sensor"
                   else "assert(int32_t(now-ui._next_refresh)>=0 && \"live backend change must redraw now\");")
            main = r'''
#include <cassert>
#include <cstdint>
#include <initializer_list>
uint32_t now=0;
uint32_t millis() { return now; }
struct Display { unsigned epoch=1; unsigned generation() const { return epoch; } } display;
class UITask {
public:
  uint32_t _next_refresh=0,_last_refresh=0;
  bool _frame_valid=true,_rows_valid=true;
  void loop();
};
@PREFIX@
}
int main() {
  UITask ui;
  for(uint32_t clock : {10000u,0x90000000u,0xfffffff0u,17u}) {
    now=clock;++display.epoch;
    ui._frame_valid=ui._rows_valid=true;
    ui._next_refresh=now+5000u;ui._last_refresh=now;
    ui.loop();
    assert(!ui._frame_valid && !ui._rows_valid);
    @DUE@
  }
}
'''.replace("@PREFIX@", prefix).replace("@DUE@", due)
            with self.subTest(role=role), tempfile.TemporaryDirectory(prefix="heltec-v4-display-generation-") as directory:
                self.compile_run(Path(directory), main,
                                 flags=["-DHELTEC_V4_DISPLAY_UNIFIED=1", "-DDISPLAY_REDRAW_ON_CHANGE=1",
                                        "-DDISPLAY_ACTIVITY_DASHBOARD=1"])

    def test_unified_environment_uses_primary_i2c_and_preserves_other_board_policy(self):
        main = r'''
#include <helpers/sensors/EnvironmentI2CConfig.h>
static_assert(ENV_HAS_SECONDARY_I2C==EXPECTED_SECONDARY,
    "unified display owns controller 1; same-pin sensors must use primary Wire");
int main() {}
'''
        cases = [(1, 4, 3, 0), (0, 4, 3, 1), (1, 6, 7, 1)]
        for unified, sda, scl, expected in cases:
            with self.subTest(unified=unified, pins=(sda, scl)), tempfile.TemporaryDirectory(
                    prefix="heltec-v4-i2c-ownership-") as directory:
                self.compile_run(Path(directory), main,
                                 flags=[f"-DHELTEC_V4_DISPLAY_UNIFIED={unified}", "-DPIN_BOARD_SDA=4",
                                        "-DPIN_BOARD_SCL=3", f"-DENV_PIN_SDA={sda}", f"-DENV_PIN_SCL={scl}",
                                        f"-DEXPECTED_SECONDARY={expected}"])

    def test_real_tft_rejects_spi_allocation_failure_before_panel_initialization(self):
        import test_heltec_r8_display_detection as fixture
        main = r'''
#include "ST7789LCDDisplay.h"
ColorVal UIColor::window_bkg=0,UIColor::title_bkg=0,UIColor::title_txt=1;
ColorVal UIColor::primary_txt=1,UIColor::secondary_txt=1,UIColor::warning_txt=1;
ColorVal UIColor::popup_bkg=0,UIColor::popup_txt=1,UIColor::corp_blue=1;
int main(int argc,char** argv) {
  assert(argc==2);scenario=argv[1];
  SPIClass radio(HSPI);bus_live[HSPI]=true;
  ST7789LCDDisplay display;
  assert(last_spi_host==FSPI && "unified TFT must use a different SPI controller from the radio");
  const bool fail=scenario=="spi-fail";
  assert(display.begin()==!fail);
  assert(display.isOn()==!fail && spi_begins==1);
  assert(radio.bus() && "TFT startup/failure must preserve the active radio controller");
  if(fail) {
    assert(init_calls==0 && draws==0 && spi_ends==1);
    assert(levels[PIN_TFT_LEDA_CTL]==LOW && levels[PIN_TFT_RST]==LOW);
  } else assert(init_calls==1 && draws>0);
  display.releaseBus();
  assert(radio.bus() && "TFT sleep must not stop the radio SPI controller");
}
'''
        tester = fixture.HeltecR8DisplayDetectionTest()
        spi = fixture.SPI.replace("struct SPIClass {", "inline bool bus_live[2]={};\ninline int last_spi_host=-1;\nstruct SPIClass {")
        spi = spi.replace("explicit SPIClass(int=0) {}", "int host;\n  explicit SPIClass(int value=HSPI) : host(value) { last_spi_host=value; }")
        spi = spi.replace('live=scenario!="spi-fail";', 'live=scenario!="spi-fail"; bus_live[host]=live;')
        spi = spi.replace("return live ? this : nullptr;", "return bus_live[host] ? this : nullptr;")
        spi = spi.replace("++spi_ends; live=false;", "++spi_ends; live=false; bus_live[host]=false;")
        with tempfile.TemporaryDirectory(prefix="heltec-v4-tft-spi-allocation-") as directory:
            with patch.object(fixture, "MAIN", main), patch.object(fixture, "SPI", spi), \
                    patch.object(fixture, "ARDUINO", fixture.ARDUINO + "\n#define FSPI 0\n"):
                binary = tester.build(Path(directory) / "driver",
                                      ["-DHELTEC_V4_DISPLAY_UNIFIED=1", "-DHELTEC_LORA_V4_TFT",
                                       "-DDISPLAY_WIDTH=240", "-DDISPLAY_HEIGHT=320"])
            for scenario in ("valid", "spi-fail"):
                with self.subTest(scenario=scenario):
                    tester.run_case(binary, scenario)
        radio = (ROOT / "variants/heltec_v4/target.cpp").read_text()
        self.assertRegex(radio, r"static\s+SPIClass\s+spi\(HSPI\)")


if __name__ == "__main__":
    unittest.main()
