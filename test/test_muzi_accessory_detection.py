"""Actual Muzi optional OLED lifecycle, GPS/button integration and recipe aliases."""
from pathlib import Path
import configparser
import os
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "src/helpers/ui"
SAN = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else []

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
#define INPUT_PULLDOWN 3
#define SCREEN_12V_ENABLE 23
inline unsigned now_ms=0, events=0, probes=0, initializations=0, draws=0, allocations=0;
inline int levels[64]={}, modes[64]={};
inline bool initialized=false, allocated=false;
inline std::string scenario;
inline bool automatic() {
#if defined(MUZI_BASE) && defined(MUZI_BASE_ACCESSORY_AUTODETECT) && MUZI_BASE_ACCESSORY_AUTODETECT == 1
 return true;
#else
 return false;
#endif
}
inline void pinMode(int pin,int mode) { assert(pin==23); modes[pin]=mode; ++events; }
inline void digitalWrite(int pin,int value) { assert(pin==23); levels[pin]=value; ++events; }
inline void delay(unsigned ms) { now_ms+=ms; }
'''
WIRE = r'''
#pragma once
#include <Arduino.h>
class TwoWire {
public:
 void beginTransmission(uint8_t addr) { assert(addr==0x3c); if(automatic()) assert(levels[23]==HIGH && now_ms>=250); }
 uint8_t endTransmission() { ++probes; return scenario=="absent" ? 2 : 0; }
};
inline TwoWire Wire;
'''
PANEL = r'''
#pragma once
#include <Wire.h>
#define SH110X_BLACK 0
#define SH110X_WHITE 1
#define SH110X_DISPLAYON 0xaf
#define SH110X_DISPLAYOFF 0xae
class Adafruit_SH1107 {
public:
 template<class... T> Adafruit_SH1107(T...) {}
 bool begin(uint8_t,bool) {
   if(automatic()) assert(probes>0 && "probe before library allocation");
   ++initializations;
   if(!allocated) { allocated=true; ++allocations; }
   initialized=scenario!="init-fail";
   return initialized;
 }
 void setRotation(int) { assert(initialized); ++draws; }
 void oled_command(int command) { assert(initialized); if(automatic()) assert(levels[23]==HIGH); ++draws; }
 void oled_commandList(uint8_t*,int) { assert(initialized); ++draws; }
 void clearDisplay() { assert(initialized); ++draws; }
 void display() { assert(initialized); ++draws; }
 void setContrast(int) { assert(initialized); ++draws; }
 void setTextColor(int) { assert(initialized); ++draws; }
 void setTextSize(int) { assert(initialized); ++draws; }
 void cp437(bool) { assert(initialized); ++draws; }
 void setCursor(int,int) { assert(initialized); ++draws; }
 void print(const char*) { assert(initialized); ++draws; }
 void fillRect(int,int,int,int,int) { assert(initialized); ++draws; }
 void drawRect(int,int,int,int,int) { assert(initialized); ++draws; }
 void drawBitmap(int,int,const uint8_t*,int,int,int) { assert(initialized); ++draws; }
 void getTextBounds(const char* s,int,int,int16_t* x,int16_t* y,uint16_t* w,uint16_t* h) {
   assert(initialized); ++draws; *x=*y=0; *w=strlen(s)*6; *h=8;
 }
};
'''
MAIN = r'''
#include "SH1107Display.h"
void frame(SH1107Display& d) {
 const uint8_t bitmap[]={0x80};
 d.clear(); d.startFrame(); d.setTextSize(1); d.setColor(1); d.setCursor(0,0); d.print("a");
 d.fillRect(0,0,1,1); d.drawRect(0,0,1,1); d.drawXbm(0,0,bitmap,1,1); (void)d.getTextWidth("a"); d.endFrame();
}
int main(int argc,char** argv) {
 assert(argc==2); scenario=argv[1]; SH1107Display d;
 if(!automatic()) { assert(d.begin()); d.turnOn(); frame(d); assert(initializations==1 && probes==1 && events==0); return 0; }
 frame(d); assert(events==0 && draws==0 && initializations==0);
 bool result=d.begin();
 if(scenario=="absent" || scenario=="init-fail") {
   assert(!result && !d.isOn() && levels[23]==LOW);
   assert(initializations==(scenario=="absent" ? 0u : 1u));
   auto before=events, inits=initializations; d.turnOn(); d.turnOff(); frame(d); assert(!d.begin());
   assert(events==before && initializations==inits && draws==0 && now_ms<300);
   return 0;
 }
 assert(result && d.isOn() && levels[23]==HIGH && initializations==1);
 frame(d); auto painted=draws;
 for(unsigned i=0;i<100;++i) {
   d.turnOff(); assert(!d.isOn() && levels[23]==LOW);
   painted=draws; frame(d); assert(draws==painted);
   d.turnOn(); assert(d.isOn() && levels[23]==HIGH);
 }
 assert(initializations==1 && allocations==1 && "wake must not leak repeated GrayOLED adapters");
 frame(d); assert(draws>painted);
}
'''


class MuziAccessoryDetectionTest(unittest.TestCase):
    def compile(self, work, source, flags=(), files=None):
        work.mkdir()
        for name, value in (files or {}).items(): (work / name).write_text(value)
        (work / "main.cpp").write_text(source)
        binary = work / "native"
        command = [os.environ.get("CXX", "g++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
                   "-Wno-unused-parameter", *SAN, *flags, "-I"+str(work), "-I"+str(UI), "-I"+str(ROOT/"src"), str(work/"main.cpp")]
        if files: command.append(str(work/"SH1107Display.cpp"))
        result = subprocess.run(command + ["-o", str(binary)], text=True, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return binary

    def test_real_oled_absence_init_failure_and_power_sleep_cycles(self):
        with tempfile.TemporaryDirectory(prefix="muzi-accessories-") as temp:
            for index, flags in enumerate((["-DMUZI_BASE", "-DMUZI_BASE_ACCESSORY_AUTODETECT=1"],
                                           ["-DMUZI_BASE"], ["-DMUZI_BASE_ACCESSORY_AUTODETECT=1"])):
                files = {name:(UI/name).read_text() for name in ("SH1107Display.h", "SH1107Display.cpp")}
                files.update({"Arduino.h":ARDUINO, "Wire.h":WIRE, "Adafruit_SH110X.h":PANEL,
                              "Adafruit_GFX.h":"#pragma once\n", "Adafruit_GrayOLED.h":"#pragma once\n"})
                binary = self.compile(Path(temp)/str(index), MAIN, flags, files)
                for scenario in (("absent", "init-fail", "valid") if index==0 else ("valid",)):
                    result = subprocess.run([str(binary), scenario], text=True, capture_output=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(b"meshcore.capability.display.autodetect.muzi_superio.v1" in binary.read_bytes(), index==0)

    def test_real_switch_paths_do_not_enable_an_absent_gps_and_keep_power_button(self):
        source = (ROOT/"variants/muzi_base/target.cpp").read_text()
        methods = "\n".join(extract_braced(source, signature) for signature in
                             ("bool MuziBaseSensorManager::begin()", "void MuziBaseSensorManager::loop()"))
        harness = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#define ENV_INCLUDE_GPS 1
#define PIN_GPS_SWITCH 12
#define INPUT_PULLDOWN 3
#define HIGH 1
#define LOW 0
#define BUTTON_EVENT_CLICK 1
#define USER_BTN_POWER_OFF_CLICKS 5
unsigned now=0, starts=0, stops=0, shutdowns=0; int switch_mode=-1, switch_level=0, click=0;
unsigned long millis() { return now; }
void pinMode(int pin,int mode) { assert(pin==12); switch_mode=mode; }
int digitalRead(int pin) { assert(pin==12 && switch_mode==INPUT_PULLDOWN); return switch_level; }
struct Button { void begin() {} int check() { int value=click; click=0; return value; } } pwr_btn;
void muziPowerOff() { ++shutdowns; }
struct EnvironmentSensorManager {
 bool gps_detected=false, requested=false;
 bool begin() { return true; } void loop() {}
 bool setSettingValue(const char* name,const char* value) { assert(strcmp(name,"gps")==0); requested=*value=='1'; return true; }
 void start_gps() { ++starts; } void stop_gps() { ++stops; }
};
struct MuziBaseSensorManager : EnvironmentSensorManager {
 unsigned long _pwr_last_click=0, _next_sw_check=0; unsigned _pwr_clicks=0; int _last_gps_sw=-1;
 bool begin(); void loop();
};
'''
        harness += methods + r'''
int main() {
 MuziBaseSensorManager m; switch_level=HIGH;
 assert(m.begin() && m.requested && starts==0 && "a switch cannot prove GPS presence");
 switch_level=LOW; now=1001; m.loop(); assert(!m.requested && starts==0 && stops==0);
 switch_level=HIGH; now=2002; m.loop(); assert(m.requested && starts==0 && !m.gps_detected);
 m.gps_detected=true; switch_level=LOW; now=3003; m.loop(); assert(stops==1);
 switch_level=HIGH; now=4004; m.loop(); assert(starts==1);
 for(unsigned i=0;i<5;++i) { click=BUTTON_EVENT_CLICK; now+=100; m.loop(); }
 assert(shutdowns==1 && "five-click power-off survives accessory consolidation");
}
'''
        with tempfile.TemporaryDirectory(prefix="muzi-gps-") as temp:
            binary=self.compile(Path(temp)/"native", harness)
            result=subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)

    def test_recipe_aliases_preserve_radio_and_remove_forced_gps_presence(self):
        config=configparser.ConfigParser(interpolation=None, inline_comment_prefixes=(";",))
        config.read(ROOT/"variants/muzi_base/platformio.ini")
        common=config["muzi_companion_accessories"]
        self.assertIn("MUZI_BASE_ACCESSORY_AUTODETECT=1", common["build_flags"])
        self.assertNotIn("ENV_SKIP_GPS_DETECT", (ROOT/"variants/muzi_base/platformio.ini").read_text())
        for radio in ("uno", "duo"):
            for transport in ("usb", "ble"):
                section=config[f"env:muzi_base_{radio}_companion_radio_{transport}"]
                self.assertIn("${muzi_companion_accessories.build_flags}", section["build_flags"])
                self.assertIn("${muzi_companion_accessories.build_src_filter}", section["build_src_filter"])
            self.assertEqual(dict(config[f"env:muzi_base_{radio}_superIO_companion_radio_ble"]),
                             {"extends":f"env:muzi_base_{radio}_companion_radio_ble"})
        self.assertIn("USE_LR1121", config["muzi_base_duo"]["build_flags"])
        self.assertIn("USE_SX1262", config["muzi_base_uno"]["build_flags"])


if __name__ == "__main__": unittest.main()
