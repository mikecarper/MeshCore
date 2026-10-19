"""Actual Lite SSD1681 readback, GPIO lifecycle and canonical recipe aliases."""
from pathlib import Path
import configparser
import os
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
UI=ROOT/"src/helpers/ui"
SAN=["-fsanitize=address,undefined","-fno-sanitize-recover=all","-fno-pie","-no-pie"] if sys.platform.startswith("linux") else []

ARDUINO=r'''
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
#define DISP_CS 22
#define DISP_DC 21
#define DISP_MOSI 20
#define DISP_SCLK 19
#define DISP_RST 28
#define DISP_BUSY 3
#define DISP_BS1 44
#define PIN_DISPLAY_CS DISP_CS
#define PIN_DISPLAY_DC DISP_DC
#define PIN_DISPLAY_RST DISP_RST
#define PIN_DISPLAY_BUSY DISP_BUSY
inline int modes[64]={},levels[64]={};
inline uint64_t now_us=0,reset_low_at=0,reset_high_at=0,busy_until=0;
inline unsigned events=0,resets=0,commands=0,status_reads=0,spi_begins=0,inits=0,draws=0;
inline unsigned bits=0,clocks=0,data_bits=0,command=0; inline uint8_t response=0;
inline bool transaction=false,initialized=false;
inline std::string scenario;
inline bool automatic() {
#if defined(LILYGO_TECHO_LITE) && defined(TECHO_LITE_DISPLAY_AUTODETECT) && TECHO_LITE_DISPLAY_AUTODETECT == 1
 return true;
#else
 return false;
#endif
}
inline void checkPin(int pin) {
 assert(pin==DISP_CS || pin==DISP_DC || pin==DISP_MOSI || pin==DISP_SCLK || pin==DISP_RST || pin==DISP_BUSY || pin==DISP_BS1);
}
inline void pinMode(int pin,int mode) {
 checkPin(pin); modes[pin]=mode; ++events;
 if(pin==DISP_BS1) assert(mode==OUTPUT && levels[pin]==LOW && "BS1 is a LOW mode strap, not switched power");
 if(pin==DISP_MOSI && (mode==INPUT_PULLUP || mode==INPUT_PULLDOWN)) {
   assert(transaction && bits==8 && command==0x2f);
   ++status_reads; unsigned within=(status_reads-1)%6;
   response=0x01;
   if(scenario=="floating") response=mode==INPUT_PULLUP ? 0xff : 0;
   if(scenario=="high") response=0xff;
   if(scenario=="low" || scenario=="busy-low") response=0;
   if(scenario=="wrong-id") response=0x02;
   if(scenario=="reserved") response=0x09;
   if(scenario=="flags") response=0x31;
   if(scenario=="biased") response=mode==INPUT_PULLUP ? 0x01 : 0x05;
   if(scenario=="noise" && within==2) response=0x05;
   if(scenario=="prime" && within%3==0) response=0xff;
   if(scenario=="recover" && status_reads<=6) response=0;
 }
}
inline void digitalWrite(int pin,int value) {
 checkPin(pin); ++events;
 if(pin==DISP_BS1) assert(value==LOW && "never drive the SPI-mode strap as a power enable");
 if(pin==DISP_RST) {
   if(value==LOW) reset_low_at=now_us;
   if(value==HIGH && levels[pin]==LOW) { assert(now_us-reset_low_at>=2000); reset_high_at=now_us; ++resets; }
 }
 if(pin==DISP_CS && value==LOW && levels[pin]==HIGH && automatic() && spi_begins==0) {
   assert(now_us-reset_high_at>=10000 && "reset settle before commands");
   transaction=true; bits=clocks=data_bits=command=0; ++commands;
 }
 if(pin==DISP_SCLK && value==HIGH && transaction) {
   ++clocks;
   if(bits<8) { assert(modes[DISP_MOSI]==OUTPUT && levels[DISP_DC]==LOW); command=(command<<1)|levels[DISP_MOSI]; ++bits; }
 }
 if(pin==DISP_SCLK && value==LOW && transaction && clocks==8 && command==0x2f)
   assert(modes[DISP_MOSI]!=OUTPUT && "release SDA before controller read falling edge");
 if(pin==DISP_CS && value==HIGH && transaction) {
   assert(bits==8);
   if(command==0x12) { assert(data_bits==0 && clocks==8); busy_until=now_us+3000; }
   else assert(command==0x2f && data_bits==8 && clocks==16);
   transaction=false;
 }
 levels[pin]=value;
}
inline int digitalRead(int pin) {
 checkPin(pin);
 if(pin==DISP_BUSY) {
   assert(modes[pin]==INPUT_PULLUP);
   if(scenario=="busy-high" || scenario=="busy-floating") return HIGH;
   return now_us<busy_until ? HIGH : LOW;
 }
 assert(pin==DISP_MOSI && transaction && command==0x2f && levels[DISP_DC]==HIGH);
 assert(modes[pin]==((status_reads-1)%6<3 ? INPUT_PULLUP : INPUT_PULLDOWN));
 assert(clocks==8+data_bits && data_bits<8 && levels[DISP_SCLK]==LOW && "status has no dummy read clock");
 return (response>>(7-data_bits++))&1;
}
inline void delay(unsigned ms) { now_us+=uint64_t(ms)*1000; }
inline void delayMicroseconds(unsigned us) { now_us+=us; }
'''

SPI=r'''
#pragma once
#include <Arduino.h>
#define MSBFIRST 1
#define SPI_MODE0 0
struct SPISettings { SPISettings(int,int,int) {} };
struct SPIClass { void begin() { if(automatic()) assert(status_reads>=6); ++spi_begins; } };
inline SPIClass SPI1;
'''

PANEL=r'''
#pragma once
#include <SPI.h>
#define GxEPD_BLACK 0
#define GxEPD_WHITE 1
struct GxEPD2_122_T61 {
 static constexpr int WIDTH=176,HEIGHT=192;
 template<class... T> GxEPD2_122_T61(T...) {}
 void selectSPI(SPIClass&,SPISettings) {}
};
using GxEPD2_150_BN=GxEPD2_122_T61;
template<class T,int Height> class GxEPD2_BW {
 int w=T::WIDTH,h=T::HEIGHT;
public:
 T epd2;
 explicit GxEPD2_BW(T model):epd2(model) {}
 void init(int,bool,int,bool) { assert(spi_begins==1); ++inits; initialized=true; }
 void setRotation(int r) { assert(initialized); w=(r&1)?T::HEIGHT:T::WIDTH; h=(r&1)?T::WIDTH:T::HEIGHT; }
 int width() const { return w; } int height() const { return h; }
 void setTextWrap(bool) { assert(initialized); }
 void setFullWindow() { assert(initialized); }
 void setPartialWindow(int,int,int,int) { assert(initialized); }
 void setFont(const int*) { assert(initialized); }
 void setTextColor(int) { assert(initialized); }
 void setCursor(int,int) { assert(initialized); }
 void print(const char*) { assert(initialized); ++draws; }
 void fillScreen(int) { assert(initialized); ++draws; }
 void display(bool) { assert(initialized); ++draws; }
 void clearScreen(uint8_t) { assert(initialized); ++draws; }
 void writeScreenBuffer(uint8_t) { assert(initialized); ++draws; }
 void hibernate() { assert(initialized); ++draws; }
 void fillRect(int,int,int,int,int) { assert(initialized); ++draws; }
 void drawRect(int,int,int,int,int) { assert(initialized); ++draws; }
 void drawBitmap(int,int,const uint8_t*,int,int,int) { assert(initialized); ++draws; }
 void getTextBounds(const char* s,int,int,int16_t* x,int16_t* y,uint16_t* ww,uint16_t* hh) {
   assert(initialized); *x=0; *y=-8; *ww=strlen(s)*6; *hh=12;
 }
};
'''

MAIN=r'''
#include "GxEPDDisplay.h"
void frame(GxEPDDisplay& d) {
 const uint8_t bitmap[]={0x80}; d.clear(); d.startFrame(); d.setTextSize(1); d.setColor(1); d.setCursor(0,0); d.print("a");
 d.fillRect(0,0,1,1); d.drawRect(0,0,1,1); d.drawXbm(0,0,bitmap,1,1); (void)d.getTextWidth("a"); d.endFrame();
}
int main(int argc,char** argv) {
 assert(argc==2); scenario=argv[1]; GxEPDDisplay d;
 if(!automatic()) { assert(d.begin()); frame(d); assert(commands==0 && inits==1); return 0; }
 frame(d); assert(events==0 && draws==0 && inits==0);
 bool invalid=scenario=="floating" || scenario=="high" || scenario=="low" || scenario=="wrong-id"
   || scenario=="reserved" || scenario=="biased" || scenario=="noise" || scenario=="busy-high"
   || scenario=="busy-low" || scenario=="busy-floating";
 bool result=d.begin();
 if(invalid) {
   assert(!result && !d.isOn() && "invalid SSD1681 must remain headless");
   assert(inits==0 && spi_begins==0 && draws==0 && levels[DISP_RST]==LOW && levels[DISP_BS1]==LOW);
   for(int pin:{DISP_CS,DISP_DC,DISP_MOSI,DISP_SCLK,DISP_BUSY}) assert(modes[pin]==INPUT);
   auto count=events; d.turnOn(); d.turnOff(); frame(d); assert(!d.begin());
   assert(events==count && inits==0 && draws==0 && now_us<500000);
   return 0;
 }
 assert(result && d.isOn() && inits==1 && spi_begins==1 && initialized);
 assert(status_reads==(scenario=="recover" ? 12u : 6u));
 auto count=commands,reset_count=resets;
 frame(d); d.turnOff(); auto painted=draws; frame(d); assert(draws==painted);
 d.turnOn(); frame(d); assert(draws>painted);
 assert(inits==1 && spi_begins==1 && commands==count && resets==reset_count);
}
'''


class TechoLiteDisplayDetectionTest(unittest.TestCase):
    def build(self, work, flags=(), mutation=None):
        work.mkdir(); (work/"Fonts").mkdir()
        for name in ("GxEPDDisplay.h","GxEPDDisplay.cpp","TechoLiteDisplayProbe.h"):
            source=(UI/name).read_text()
            if mutation and name=="TechoLiteDisplayProbe.h":
                old,new=mutation; self.assertIn(old,source); source=source.replace(old,new)
            (work/name).write_text(source)
        files={"Arduino.h":ARDUINO,"SPI.h":SPI,"Wire.h":"#pragma once\n", "GxEPD2_BW.h":PANEL,
               "CRC32.h":"#pragma once\n#include <cstdint>\n#include <cstddef>\nstruct CRC32 { template<class T> void update(T) {} template<class T> void update(const T*,size_t) {} void reset() {} uint32_t finalize() { return 1; } };\n",
               "main.cpp":MAIN}
        for name in ("GxEPD2_3C.h","GxEPD2_4C.h","GxEPD2_7C.h"): files[name]="#pragma once\n"
        for font in ("FreeSans9pt7b","FreeSansBold12pt7b","FreeSans18pt7b"):
            files[f"Fonts/{font}.h"]=f"#pragma once\ninline const int {font}=8;\n"
        for name,source in files.items(): (work/name).write_text(source)
        binary=work/"native"
        result=subprocess.run([os.environ.get("CXX","g++"),"-std=c++17","-Wall","-Wextra","-Werror",
            "-Wno-unused-parameter","-Wno-sign-compare", *SAN,*flags,"-DEINK_DISPLAY_MODEL=GxEPD2_122_T61","-DDISPLAY_ROTATION=4",
            "-I"+str(work),"-I"+str(UI),"-I"+str(ROOT/"src"),str(work/"main.cpp"),str(work/"GxEPDDisplay.cpp"),"-o",str(binary)],
            capture_output=True,text=True,timeout=60)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        return binary

    def run_case(self,binary,scenario,negative=None):
        result=subprocess.run([str(binary),scenario],capture_output=True,text=True,timeout=10)
        if negative:
            self.assertNotEqual(result.returncode,0); self.assertIn(negative,result.stderr)
        else:self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_actual_status_readback_and_headless_failure_paths(self):
        with tempfile.TemporaryDirectory(prefix="techo-lite-detect-") as temp:
            binary=self.build(Path(temp)/"auto",["-DLILYGO_TECHO_LITE=1","-DTECHO_LITE_DISPLAY_AUTODETECT=1"])
            self.assertIn(b"meshcore.capability.display.autodetect.techo_lite.v1",binary.read_bytes())
            for scenario in ("valid","flags","prime","recover","floating","high","low","wrong-id","reserved","biased","noise","busy-high","busy-low","busy-floating"):
                with self.subTest(scenario=scenario):self.run_case(binary,scenario)

    def test_other_eink_boards_and_disabled_optin_keep_existing_behavior(self):
        with tempfile.TemporaryDirectory(prefix="techo-lite-detect-") as temp:
            for index,flags in enumerate(([],["-DLILYGO_TECHO_LITE=1","-DTECHO_LITE_DISPLAY_AUTODETECT=0"],["-DTECHO_LITE_DISPLAY_AUTODETECT=1"])):
                binary=self.build(Path(temp)/str(index),flags);self.run_case(binary,"valid")
                self.assertNotIn(b"meshcore.capability.display.autodetect.techo_lite.v1",binary.read_bytes())

    def test_negative_controls_catch_turnaround_id_and_reset_errors(self):
        flags=["-DLILYGO_TECHO_LITE=1","-DTECHO_LITE_DISPLAY_AUTODETECT=1"]
        with tempfile.TemporaryDirectory(prefix="techo-lite-detect-") as temp:
            cases=[("id",("status[0] == 0x01u","status[0] == 0x02u"),"wrong-id","invalid SSD1681 must remain headless"),
                   ("settle",("delay(10);","delay(0);"),"valid","reset settle before commands"),
                   ("turnaround",("pinMode(DISP_MOSI, bias);","pinMode(DISP_MOSI, OUTPUT);"),"valid","release SDA before controller read falling edge")]
            for name,mutation,scenario,message in cases:
                binary=self.build(Path(temp)/name,flags,mutation); self.run_case(binary,scenario,negative=message)

    def test_legacy_core_aliases_share_auto_display_and_exact_usb_transport(self):
        config=configparser.ConfigParser(interpolation=None,inline_comment_prefixes=(";",));config.read(ROOT/"variants/lilygo_techo_lite/platformio.ini")
        self.assertIn("TECHO_LITE_DISPLAY_AUTODETECT=1",config["LilyGo_T-Echo-Lite"]["build_flags"])
        self.assertIn("ENABLE_USB_INTERFACE=1",config["env:LilyGo_T-Echo-Lite_companion_radio_usb"]["build_flags"])
        for transport in ("usb","ble"):
            self.assertEqual(dict(config[f"env:LilyGo_T-Echo-Lite_non_shell_companion_radio_{transport}"]),
                             {"extends":f"env:LilyGo_T-Echo-Lite_companion_radio_{transport}"})
        pins=(ROOT/"variants/lilygo_techo_lite/variant.h").read_text()
        self.assertIn("#define DISP_POWER              DISP_BS1",pins)


if __name__=="__main__":unittest.main()
