"""Exercise the real L1 display selector and SSD1680 readback on the host."""
from pathlib import Path
import configparser
import os
import subprocess
import tempfile
import unittest

from test_techo_lite_display_detection import ARDUINO as GPIO, PANEL, SPI, SAN
import test_companion_inbox as inbox_fixture
from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "src/helpers/ui"

ARDUINO = GPIO.replace(
    '''#if defined(LILYGO_TECHO_LITE) && defined(TECHO_LITE_DISPLAY_AUTODETECT) && TECHO_LITE_DISPLAY_AUTODETECT == 1
 return true;
#else
 return false;
#endif''', 'return true;').replace("inline void pinMode(", "inline void epdPinMode(").replace(
    "inline void digitalWrite(", "inline void epdDigitalWrite(").replace(
    "inline int digitalRead(", "inline int epdDigitalRead(") + r'''
#define PIN_SPI1_MOSI DISP_MOSI
#define PIN_SPI1_SCK DISP_SCLK
#define DISPLAY_ADDRESS 0x3D
#define PIN_WIRE_SDA 14
#define PIN_WIRE_SCL 15
inline unsigned oled_constructs=0, oled_inits=0, oled_allocs=0, oled_frees=0, oled_draws=0;
inline unsigned eink_constructs=0;
inline bool wire_running=true, i2c_transaction=false;
inline unsigned wire_ends=0, wire_begins=0, i2c_clocks=0, i2c_command=0;
inline bool i2cPin(int pin) { return pin==PIN_WIRE_SDA || pin==PIN_WIRE_SCL; }
inline bool oledAt(unsigned address) {
 return (address==0x3d && (scenario=="oled" || scenario=="oled-fail" || scenario=="both"))
     || (address==0x3c && scenario=="oled-alt");
}
inline void pinMode(int pin,int mode) {
 if(!i2cPin(pin)) { epdPinMode(pin,mode); return; }
 assert(!wire_running && "release Wire before GPIO probe"); ++events;
 assert(mode==INPUT_PULLUP || (mode==OUTPUT && levels[pin]==LOW));
 const int previous=modes[pin]; modes[pin]=mode;
 if(pin==PIN_WIRE_SDA && mode==OUTPUT && modes[PIN_WIRE_SCL]==INPUT_PULLUP) {
   i2c_transaction=true; i2c_clocks=i2c_command=0;
 }
 if(pin==PIN_WIRE_SCL && mode==INPUT_PULLUP && previous==OUTPUT && i2c_transaction) {
   ++i2c_clocks;
   if(i2c_clocks<=8) i2c_command=(i2c_command<<1)|(modes[PIN_WIRE_SDA]==INPUT_PULLUP ? 1u : 0u);
 }
 if(pin==PIN_WIRE_SDA && mode==INPUT_PULLUP && modes[PIN_WIRE_SCL]==INPUT_PULLUP && i2c_clocks>=10)
   i2c_transaction=false;
}
inline void digitalWrite(int pin,int value) {
 if(!i2cPin(pin)) { epdDigitalWrite(pin,value); return; }
 assert(!wire_running && value==LOW && "I2C GPIO must never drive HIGH");
 levels[pin]=value; ++events;
}
inline int digitalRead(int pin) {
 if(!i2cPin(pin)) return epdDigitalRead(pin);
 assert(!wire_running && modes[pin]==INPUT_PULLUP);
 if(pin==PIN_WIRE_SCL) return scenario=="scl-stuck" ? LOW : HIGH;
 if(scenario=="sda-stuck") return LOW;
 if(i2c_transaction && i2c_clocks==9) {
   assert((i2c_command&1)==0 && "OLED probe uses a write address");
   return oledAt(i2c_command>>1) ? LOW : HIGH;
 }
 return HIGH;
}
'''

WIRE = r'''
#pragma once
#include <Arduino.h>
struct TwoWire {
 unsigned probes=0; uint8_t address=0;
 void end() { assert(wire_running); wire_running=false; ++wire_ends; }
 void begin() { assert(!wire_running); wire_running=true; ++wire_begins; }
 void beginTransmission(uint8_t value) { address=value; ++probes; }
 uint8_t endTransmission() {
   assert(wire_running); return oledAt(address) ? 0 : 2;
 }
};
inline TwoWire Wire;
'''

OLED = r'''
#pragma once
#include <Arduino.h>
#include <Wire.h>
#define SH110X_BLACK 0
#define SH110X_WHITE 1
#define SH110X_DISPLAYON 0xAF
#define SH110X_DISPLAYOFF 0xAE
struct Adafruit_I2CDevice {};
struct Adafruit_SH1106G {
 Adafruit_I2CDevice* i2c_dev=nullptr;
 uint8_t* buffer=nullptr;
 Adafruit_SH1106G(int,int,TwoWire*,int) { ++oled_constructs; }
 ~Adafruit_SH1106G() { if(buffer) { delete[] buffer; ++oled_frees; } delete i2c_dev; }
 bool begin(uint8_t address,bool) {
   ++oled_inits;
   assert((scenario=="oled-alt" ? address==0x3c : address==0x3d));
   if(scenario=="oled-fail") return false;
   assert(!buffer); buffer=new uint8_t[1024]; ++oled_allocs;
   i2c_dev=new Adafruit_I2CDevice;
   return true;
 }
 void oled_command(int) { assert(buffer); }
 void clearDisplay() { assert(buffer); ++oled_draws; }
 void display() { assert(buffer); ++oled_draws; }
 void setTextColor(int color) { assert(buffer && (color==0 || color==1)); }
 void setTextSize(int) { assert(buffer); }
 void cp437(bool) { assert(buffer); }
 void setCursor(int,int) { assert(buffer); }
 void print(const char*) { assert(buffer); ++oled_draws; }
 void fillRect(int,int,int,int,int) { assert(buffer); ++oled_draws; }
 void drawRect(int,int,int,int,int) { assert(buffer); ++oled_draws; }
 void drawBitmap(int,int,const uint8_t*,int,int,int) { assert(buffer); ++oled_draws; }
 void getTextBounds(const char* str,int,int,int16_t* x,int16_t* y,uint16_t* w,uint16_t* h) {
   assert(buffer); *x=*y=0; *w=strlen(str)*6; *h=8;
 }
};
'''

MAIN = r'''
#include "WioL1Display.h"
#include "WioL1DisplayLayout.h"
void frame(WioL1Display& display) {
 const uint8_t bitmap[]={0x80};
 display.clear(); display.startFrame(); display.setTextSize(1);
 display.setColor(UIColor::primary_txt); display.setCursor(1,2); display.print("test");
 display.fillRect(1,2,3,4); display.drawRect(1,2,3,4);
 display.drawXbm(1,2,bitmap,1,1); (void)display.getTextWidth("test"); display.endFrame();
}
int main(int argc,char** argv) {
 assert(argc==2); scenario=argv[1];
 assert(mesh::ui::wio_l1::visibleRows(64,18,10,12,6)==4);
 assert(mesh::ui::wio_l1::visibleRows(122,29,22,22,6)==4);
 assert(mesh::ui::wio_l1::visibleRows(64,60,10,12,6)==0);
 for(int height:{40,64,122,250}) for(int top:{0,18,29,60})
   for(int line:{10,12,22}) for(int limit:{0,1,4,6}) {
     const int rows=mesh::ui::wio_l1::visibleRows(height,top,line,line,limit);
     assert(rows>=0 && rows<=limit);
     for(int row=0;row<rows;++row) assert(top+row*line+line<=height);
     if(rows<limit) assert(top+rows*line+line>height);
   }
 const bool oled=scenario=="oled" || scenario=="oled-alt" || scenario=="both";
 const bool invalid=scenario=="floating" || scenario=="high" || scenario=="low"
     || scenario=="wrong-id" || scenario=="reserved" || scenario=="biased"
     || scenario=="noise" || scenario=="busy-high" || scenario=="busy-low"
     || scenario=="busy-floating" || scenario=="oled-fail";
 {
   WioL1Display display;
   frame(display); display.turnOff(); display.forceFullRefresh();
   assert(events==0 && Wire.probes==0 && oled_constructs==0 && eink_constructs==0);
   auto selected=display.detect(); auto events_after_probe=events, probes=Wire.probes;
   assert(wire_running && wire_ends==1 && wire_begins==1);
   assert(display.detect()==selected && events==events_after_probe && Wire.probes==probes);
   assert(oled_constructs==0 && eink_constructs==0 && "probe must not allocate backend storage");
   if(oled) {
     assert(selected==WioL1Display::Type::Oled && !display.isEink());
     assert(display.defaultTimeoutSeconds()==15 && commands==0 && resets==0);
   } else if(!invalid) {
     assert(selected==WioL1Display::Type::Eink && display.isEink());
     assert(display.defaultTimeoutSeconds()==900);
   }
   bool ready=display.begin();
   if(invalid) {
     assert(!ready && !display.isOn() && display.selectedType()==WioL1Display::Type::None);
     assert(inits==0 && spi_begins==0 && draws==0 && oled_allocs==0);
     if(scenario!="oled-fail") {
       assert(oled_constructs==0 && eink_constructs==0 && levels[DISP_RST]==LOW);
       for(int pin:{DISP_CS,DISP_DC,DISP_MOSI,DISP_SCLK,DISP_BUSY}) assert(modes[pin]==INPUT);
     }
     auto count=events; display.turnOn(); display.turnOff(); frame(display);
     assert(!display.begin() && events==count && draws==0 && oled_draws==0 && now_us<500000);
   } else {
     assert(ready);
     display.turnOn(); assert(display.isOn());
     assert(display.width()==(oled ? 128 : 250) && display.height()==(oled ? 64 : 122));
     assert(UIColor::window_bkg==(oled ? 0 : 0xffff));
     assert(UIColor::primary_txt==(oled ? 1 : 0));
     assert(oled_constructs==(oled?1u:0u) && eink_constructs==(oled?0u:1u));
     assert(oled_allocs==(oled?1u:0u));
     auto commands_before=commands, resets_before=resets;
     for(int i=0;i<3;++i) { frame(display); display.turnOff(); }
     auto painted=draws+oled_draws; frame(display); assert(draws+oled_draws==painted);
     display.turnOn(); frame(display); assert(display.isOn() && draws+oled_draws>painted);
     assert(display.begin() && commands==commands_before && resets==resets_before);
     assert(oled_inits==(oled?1u:0u) && inits==(oled?0u:1u));
   }
 }
 assert(oled_frees==oled_allocs);
}
'''


class WioL1DisplayDetectionTest(unittest.TestCase):
    def build(self, work, mutation=None):
        work.mkdir()
        (work / "Fonts").mkdir()
        for name in ("WioL1Display.h", "WioL1Display.cpp", "WioL1DisplayProbe.h",
                     "SH1106Display.h", "SH1106Display.cpp", "GxEPDDisplay.h", "GxEPDDisplay.cpp"):
            source = (UI / name).read_text()
            if mutation and name == "WioL1DisplayProbe.h":
                old, new = mutation
                self.assertIn(old, source)
                source = source.replace(old, new)
            (work / name).write_text(source)
        panel = PANEL.replace("WIDTH=176,HEIGHT=192", "WIDTH=122,HEIGHT=250")
        panel = panel.replace("GxEPD2_122_T61(T...) {}", "GxEPD2_122_T61(T...) { ++eink_constructs; }")
        panel += "\nusing GxEPD2_213_B74=GxEPD2_122_T61;\n"
        files = {
            "Arduino.h": ARDUINO, "Wire.h": WIRE, "SPI.h": SPI,
            "GxEPD2_BW.h": panel, "Adafruit_SH110X.h": OLED,
            "Adafruit_GrayOLED.h": "#pragma once\n",
            "Adafruit_GFX.h": "#pragma once\n", "main.cpp": MAIN,
            "CRC32.h": "#pragma once\n#include <cstdint>\n#include <cstddef>\nstruct CRC32 { template<class T> void update(T) {} template<class T> void update(const T*,size_t) {} void reset() {} uint32_t finalize() { return 1; } };\n",
        }
        for name in ("GxEPD2_3C.h", "GxEPD2_4C.h", "GxEPD2_7C.h"):
            files[name] = "#pragma once\n"
        for font in ("FreeSans9pt7b", "FreeSansBold12pt7b", "FreeSans18pt7b"):
            files[f"Fonts/{font}.h"] = f"#pragma once\ninline const int {font}=8;\n"
        for name, source in files.items():
            (work / name).write_text(source)
        binary = work / "native"
        result = subprocess.run([
            os.environ.get("CXX", "g++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-parameter", "-Wno-sign-compare", *SAN,
            "-DDISPLAY_CLASS=WioL1Display", "-DWIO_L1_DISPLAY_UNIFIED=1",
            "-DEINK_DISPLAY_MODEL=GxEPD2_213_B74", "-DDISPLAY_ROTATION=1",
            "-DEINK_MAX_PARTIAL_REFRESH=10", "-I" + str(work), "-I" + str(UI),
            "-I" + str(ROOT / "src"), str(work / "main.cpp"),
            *[str(work / name) for name in ("WioL1Display.cpp", "GxEPDDisplay.cpp", "SH1106Display.cpp")],
            "-o", str(binary)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return binary

    def run_case(self, binary, scenario, negative=None):
        result = subprocess.run([str(binary), scenario], capture_output=True, text=True, timeout=10)
        if negative:
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(negative, result.stderr)
        else:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_actual_selector_gpio_readback_and_headless_paths(self):
        with tempfile.TemporaryDirectory(prefix="wio-l1-display-") as temp:
            binary = self.build(Path(temp) / "native")
            for marker in ("wio_l1_unified", "autodetect.wio_l1"):
                self.assertIn(("meshcore.capability.display." + marker + ".v1").encode(), binary.read_bytes())
            for scenario in ("oled", "oled-alt", "oled-fail", "both", "valid", "flags", "prime",
                             "recover", "floating", "high", "low", "wrong-id", "reserved",
                             "biased", "noise", "busy-high", "busy-low", "busy-floating",
                             "scl-stuck", "sda-stuck"):
                with self.subTest(scenario=scenario):
                    self.run_case(binary, scenario)

    def test_negative_controls_reject_wrong_identity_and_turnaround(self):
        with tempfile.TemporaryDirectory(prefix="wio-l1-display-") as temp:
            for name, mutation, scenario, failure in (
                ("id", ("status[0] == 0x01u", "status[0] == 0x02u"), "wrong-id", "!ready"),
                ("settle", ("delay(10);", "delay(0);"), "valid", "reset settle before commands"),
                ("turnaround", ("pinMode(PIN_SPI1_MOSI, bias);", "pinMode(PIN_SPI1_MOSI, OUTPUT);"),
                 "valid", "release SDA before controller read falling edge"),
                ("i2c-drive", ("digitalWrite(pin, LOW);", "digitalWrite(pin, HIGH);"),
                 "oled", "I2C GPIO must never drive HIGH"),
                ("i2c-address", ("(address << 1) & mask", "address & mask"),
                 "oled", "OLED probe uses a write address")):
                binary = self.build(Path(temp) / name, mutation)
                self.run_case(binary, scenario, negative=failure)

    def test_recipe_keeps_capacities_and_legacy_display_specific_targets(self):
        config = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=(";",))
        config.read(ROOT / "variants/wio-tracker-l1/platformio.ini")
        combined = config["env:WioTrackerL1_companion_radio_ble"]
        for flag in ("DISPLAY_CLASS=WioL1Display", "WIO_L1_DISPLAY_UNIFIED=1",
                     "MAX_CONTACTS=350", "MAX_GROUP_CHANNELS=40", "OFFLINE_QUEUE_SIZE=256"):
            self.assertIn(flag, combined["build_flags"])
        for source in ("WioL1Display.cpp", "GxEPDDisplay.cpp", "SerialBLEInterface.cpp"):
            self.assertIn(source, combined["build_src_filter"])
        self.assertIn("DISPLAY_CLASS=SH1106Display", config["env:WioTrackerL1_companion_radio_usb"]["build_flags"])
        config.read(ROOT / "variants/wio-tracker-l1-eink/platformio.ini")
        self.assertIn("DISPLAY_CLASS=GxEPDDisplay", config["env:WioTrackerL1Eink_companion_radio_ble"]["build_flags"])

    def test_real_message_footer_and_hibernate_page_follow_selected_panel(self):
        ui = (ROOT / "examples/companion_radio/ui-new/UITask.cpp").read_text()
        preamble = ("#define WIO_L1_DISPLAY_UNIFIED 1\n#define UI_MESSAGE_CHANNEL_FOOTER 1\n"
                    "#define UI_BUTTON_READER_HINT 0\n" + inbox_fixture.PREAMBLE)
        preamble = preamble.replace("bool on=true, compact=false;", r'''
  bool on=true, compact=false, eink=false;
  bool isEink() override { return eink; }
  void selectEink(bool selected) { eink=selected; setDimensions(selected?250:128,selected?122:64); }
''')
        implementation = extract_braced(ui, "class MsgPreviewScreen :") + ";\n"
        implementation += "Display display;\nstruct HomeScreen { enum HomePage { RADIO2,RADIO_STATUS,SHUTDOWN,FIRST };\n"
        implementation += extract_braced(ui, "bool isPageVisible(uint8_t page) const {") + "\n};\n"
        scenario = r'''
int main() {
 UITask task(display); MsgPreviewScreen messages(&task); HomeScreen home;
 for(bool eink:{false,true}) {
   display.selectEink(eink); display.clear(); messages.render(display);
   assert(display.contains("All channels")==eink);
   assert(home.isPageVisible(HomeScreen::SHUTDOWN)==eink);
   assert(home.isPageVisible(HomeScreen::FIRST));
   assert(!home.isPageVisible(HomeScreen::RADIO2));
   the_mesh.dual_radio_active=true;
   assert(home.isPageVisible(HomeScreen::RADIO2));
   the_mesh.dual_radio_active=false;
 }
}
'''
        inbox_fixture.CompanionInboxTest.compile_and_run(self, preamble + implementation + scenario)

    def test_actual_radio_start_skips_stuck_optional_rtc_bus_only_when_unified(self):
        target = (ROOT / "variants/wio-tracker-l1/target.cpp").read_text()
        implementation = extract_braced(target, "bool radio_init()")
        source = r'''
#include <cassert>
#define PIN_WIRE_SDA 14
#define PIN_WIRE_SCL 15
#define HIGH 1
int sda=1,scl=1,reads=0,Wire=0,SPI=0;
int digitalRead(int pin) { ++reads; return pin==PIN_WIRE_SDA ? sda : scl; }
struct Clock { int probes=0; void begin(int&) { ++probes; } } rtc_clock;
struct Radio { int starts=0; bool result=true; bool std_init(int*) { ++starts; return result; } } radio;
''' + implementation + r'''
int main() {
 for(int data:{0,1}) for(int clock:{0,1}) for(bool success:{false,true}) {
   sda=data; scl=clock; radio.result=success;
   const int before=rtc_clock.probes, starts=radio.starts;
   assert(radio_init()==success && radio.starts==starts+1);
#if defined(WIO_L1_DISPLAY_UNIFIED) && WIO_L1_DISPLAY_UNIFIED
   assert(rtc_clock.probes==before+(data && clock));
#else
   assert(rtc_clock.probes==before+1);
#endif
 }
#if defined(WIO_L1_DISPLAY_UNIFIED) && WIO_L1_DISPLAY_UNIFIED
 assert(reads>0);
#else
 assert(reads==0);
#endif
}
'''
        # The local initializer lists belong to this standalone harness only.
        source = "#include <initializer_list>\n" + source
        with tempfile.TemporaryDirectory(prefix="wio-l1-rtc-") as temp:
            for label, flags in (("unified", ["-DWIO_L1_DISPLAY_UNIFIED=1"]),
                                 ("legacy", []), ("disabled", ["-DWIO_L1_DISPLAY_UNIFIED=0"])):
                binary = Path(temp) / label
                result = subprocess.run([os.environ.get("CXX", "g++"), "-std=c++17",
                    "-Wall", "-Wextra", "-Werror", *SAN, *flags, "-x", "c++", "-",
                    "-o", str(binary)], input=source, capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
