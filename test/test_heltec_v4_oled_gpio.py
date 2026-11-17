"""Exercise the boot-only OLED GPIO profile and real display pin lifecycle."""
from pathlib import Path
import tempfile
import unittest

import test_heltec_v4_display_detection as display_tests

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "src/helpers/ui"

SETTINGS = r'''
#include <cassert>
#include <cstring>
#include <initializer_list>
#include <HeltecV4DisplaySettings.h>
#include <helpers/ui/HeltecV4GpioPolicy.h>
#include <helpers/RuntimePinClaims.h>
using namespace mesh::ui;
int main() {
  FakeFS fs;HeltecV4Display display;char reply[160];
  const auto command=[&](const char* text){return handleHeltecV4DisplayCommand(text,reply,sizeof(reply),display);};
  loadHeltecV4DisplaySettings(&fs,display);
  assert(!heltecV4OledExpansionActive());
  assert(command("get display.type")&&!strcmp(reply,"display.type auto (active OLED)"));
  for(const char* invalid:{"set display.type", "set display.type TFT", "set display.type oled ", "get display.type oled"})
    assert(command(invalid)&&strstr(reply,"Error:")&&!heltecV4OledExpansionActive()&&fs.files.empty());
  assert(!command("get display.type.extra"));
  assert(command("set display.type oled")&&strstr(reply,"reboot required"));
  assert(!heltecV4OledExpansionActive()&&!heltecV4OledExpansionPinsAvailable());
  assert(display.mode()==HeltecV4DisplayMode::Oled&&fs.files["/display_type"].size()==6);
  assert(command("get display.type")&&!strcmp(reply,"display.type oled (active OLED; reboot required)"));
  loadHeltecV4DisplaySettings(&fs,display);
  assert(heltecV4OledExpansionActive()&&display.mode()==HeltecV4DisplayMode::Oled);
  assert(heltecV4OledExpansionPinsAvailable());
  assert(command("get display.type")&&!strcmp(reply,"display.type oled (active OLED)"));
  const auto oled_record=fs.files["/display_type"];
  for(auto owner:{mesh::pins::Owner::UserGpio,mesh::pins::Owner::Rotary,
                  mesh::pins::Owner::Outputs,mesh::pins::Owner::Notifications}) {
    for(unsigned pin:{33,44,47,48}) {
      assert(mesh::pins::tryClaim(owner,mesh::pins::pinMask(pin)));
      for(const char* choice:{"set display.type auto","set display.type tft","set display.type off"}) {
        assert(command(choice)&&strstr(reply,"release GPIO"));
        assert(heltecV4OledExpansionActive()&&heltecV4OledExpansionPinsAvailable());
        assert(fs.files["/display_type"]==oled_record);
      }
      mesh::pins::release(owner,mesh::pins::pinMask(pin));
    }
  }
  for(const char* choice:{"set display.type auto","set display.type tft","set display.type off"}) {
    const auto before=display.applied;
    assert(command(choice)&&strstr(reply,"reboot required"));
    assert(heltecV4OledExpansionActive()&&!heltecV4OledExpansionPinsAvailable());
    assert(display.applied==before&&display.mode()==HeltecV4DisplayMode::Oled);
    // Canceling a pending change reopens allocation without changing the boot latch.
    assert(command("set display.type oled")&&strstr(reply,"OK"));
    assert(heltecV4OledExpansionPinsAvailable());
  }
  assert(command("set display.type auto")&&strstr(reply,"reboot required"));
  loadHeltecV4DisplaySettings(&fs,display);assert(!heltecV4OledExpansionActive());
  assert(command("set display.type tft")&&strstr(reply,"OK"));
  const auto original=fs.files["/display_type"];
  for(unsigned fault=0;fault<4;++fault) {
    fs.fail_write=fault==0;fs.corrupt_write=fault==1;fs.fail_publish=fault==2;fs.fail_temp_read=fault==3;
    assert(command("set display.type oled")&&strstr(reply,"save failed or unconfirmed"));
    assert(!heltecV4OledExpansionActive()&&fs.files["/display_type"]==original);
    assert(display.mode()==HeltecV4DisplayMode::Tft);
  }
  fs.fail_write=fs.corrupt_write=fs.fail_publish=fs.fail_temp_read=false;
  assert(command("set display.type oled")&&strstr(reply,"OK"));
  const auto oled=fs.files["/display_type"];
  for(unsigned byte=0;byte<6;++byte) {
    fs.files["/display_type"]=oled;fs.files["/display_type"][byte]^=0x80;
    loadHeltecV4DisplaySettings(&fs,display);
    assert(!heltecV4OledExpansionActive()&&display.mode()==HeltecV4DisplayMode::Off);
    assert(command("get display.type")&&strstr(reply,"saved config rejected"));
    const auto applied=display.applied;
    assert(command("set display.type oled")&&strstr(reply,"reboot required"));
    assert(!heltecV4OledExpansionActive()&&display.applied==applied);
  }
  fs.files["/display_type.bak"]=oled;fs.files["/display_type"]={1,2,3};
  loadHeltecV4DisplaySettings(&fs,display);assert(heltecV4OledExpansionActive());
  assert(fs.files["/display_type"]==oled&&!fs.exists("/display_type.bak"));
  // A publish readback failure never unlocks pins during the active boot.
  fs.files.clear();loadHeltecV4DisplaySettings(&fs,display);
  fs.fail_public_read=true;
  assert(command("set display.type oled")&&strstr(reply,"save failed or unconfirmed"));
  assert(!heltecV4OledExpansionActive());
  assert(command("set display.type off")&&strstr(reply,"save failed or unconfirmed"));
  fs.fail_public_read=false;loadHeltecV4DisplaySettings(&fs,display);
  assert(heltecV4OledExpansionActive()); // Saved and active results are distinct.
  loadHeltecV4DisplaySettings(nullptr,display);assert(!heltecV4OledExpansionActive());
  assert(display.mode()==HeltecV4DisplayMode::Off);
  assert(command("set display.type oled")&&strstr(reply,"save failed or unconfirmed"));
}
'''

WRAPPER = r'''
#include <cassert>
#include <cstring>
#include <initializer_list>
#include "HeltecV4Display.h"
#include <helpers/ui/HeltecV4GpioPolicy.h>
using namespace mesh::ui;
int main() {
  setHeltecV4OledExpansionAtBoot(true);
  // Existing accessory outputs must remain untouched across the full lifecycle.
  for(int pin:{33,44,47,48}) {modes[pin]=OUTPUT;levels[pin]=HIGH;}
  for(const char* input:{"oled","absent","oled-init-failed","bus-failed"}) {
    scenario=input;oled_present=scenario=="oled"||scenario=="oled-init-failed";
    tft_present=true;fail_oled=scenario=="oled-init-failed";fail_tft=false;
    HeltecV4Display display;
    const auto touched=accessory_events,touch_reads=TwoWire::touch_reads,tfts=tft_begins;
    const bool ready=scenario=="oled";
    assert(display.begin()==ready);
    assert(accessory_events==touched&&TwoWire::touch_reads==touch_reads&&tft_begins==tfts);
    const auto mode=display.mode();
    assert(!display.setMode(HeltecV4DisplayMode::Tft)&&display.mode()==mode);
    display.turnOff();display.turnOn();
    assert(display.setMode(HeltecV4DisplayMode::Auto));
    assert(display.setMode(HeltecV4DisplayMode::Oled));
    assert(display.setMode(HeltecV4DisplayMode::Off));
    assert(accessory_events==touched&&TwoWire::touch_reads==touch_reads&&tft_begins==tfts);
    for(int pin:{33,44,47,48})assert(modes[pin]==OUTPUT&&levels[pin]==HIGH);
    assert(TwoWire::global_events==0);
  }
  setHeltecV4OledExpansionAtBoot(false);
  // A corrupt or unreadable saved preference is headless for the whole boot,
  // even after saving a repair, so unknown accessory wiring is never probed.
  {
    HeltecV4Display damaged;
    damaged.setConfigurationFault(true);
    const auto events=gpio_events,buses=TwoWire::begins,tfts=tft_begins;
    assert(damaged.setMode(HeltecV4DisplayMode::Off));
    assert(!damaged.begin()&&strstr(damaged.status(),"config invalid"));
    assert(!damaged.setMode(HeltecV4DisplayMode::Auto));
    assert(!damaged.setMode(HeltecV4DisplayMode::Tft));
    assert(!damaged.setMode(HeltecV4DisplayMode::Oled));
    damaged.turnOn();damaged.turnOff();
    assert(gpio_events==events&&TwoWire::begins==buses&&tft_begins==tfts);
  }
  scenario="tft";oled_present=false;tft_present=true;fail_oled=false;
  HeltecV4Display shared;
  const auto tfts=tft_begins,touch_reads=TwoWire::touch_reads;
  assert(shared.begin());
  assert(tft_begins==tfts+1&&TwoWire::touch_reads>touch_reads);
}
'''


class HeltecV4OledGpioTests(unittest.TestCase):
    compile_run = display_tests.HeltecV4DisplayDetectionTests.compile_run

    def test_saved_oled_selection_is_checked_boot_only_and_gates_new_pin_claims(self):
        with tempfile.TemporaryDirectory(prefix="heltec-v4-oled-gpio-settings-") as directory:
            work = Path(directory)
            files = {"HeltecV4Display.h": display_tests.SETTINGS_DISPLAY,
                     "helpers/IdentityStore.h": display_tests.FS_STUB.replace("/display_prefs", "/display_type")}
            for name in ("HeltecV4DisplaySettings.h", "HeltecV4DisplaySettings.cpp"):
                files[name] = (UI / name).read_text()
            self.compile_run(work, SETTINGS, [work / "HeltecV4DisplaySettings.cpp"], files,
                             ["-DHELTEC_V4_DISPLAY_UNIFIED=1", "-DMESH_CUSTOM_PERIPHERALS=1"])

    def test_oled_gpio_profile_never_touches_tft_or_accessory_pins(self):
        with tempfile.TemporaryDirectory(prefix="heltec-v4-oled-gpio-driver-") as directory:
            work = Path(directory)
            arduino = display_tests.WRAPPER_ARDUINO.replace(
                "inline int modes", "inline unsigned accessory_events=0;\ninline int modes")
            arduino = arduino.replace("++gpio_events;", "if(pin==33||pin==44||pin==47||pin==48){++accessory_events;} ++gpio_events;")
            wire = display_tests.WRAPPER_WIRE.replace("++begins; sda=data;scl=clock;",
                "if(data==47||clock==48){++accessory_events;} ++begins; sda=data;scl=clock;")
            files = {"Arduino.h": arduino, "Wire.h": wire,
                     "SSD1306Display.h": display_tests.WRAPPER_BACKEND,
                     "ST7789LCDDisplay.h": '#pragma once\n#include "SSD1306Display.h"\n'}
            for name in ("HeltecV4Display.h", "HeltecV4Display.cpp", "HeltecV4DisplayPolicy.h",
                         "HeltecV4DisplayProbe.h", "DisplayDriver.h", "DisplayPowerPolicy.h",
                         "DisplayBuildFlags.h", "ColorTheme.h", "DisplayNightMode.h"):
                files[name] = (UI / name).read_text()
            self.compile_run(work, WRAPPER, [work / "HeltecV4Display.cpp"], files,
                             ["-DHELTEC_V4_DISPLAY_UNIFIED=1", "-DMESH_CUSTOM_PERIPHERALS=1", "-Wno-unused-parameter"])


if __name__ == "__main__":
    unittest.main()
