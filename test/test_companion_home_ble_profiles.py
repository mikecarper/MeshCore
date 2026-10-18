#!/usr/bin/env python3
"""Compile the actual home-page enum, BLE rendering/input gates and clock math."""

from pathlib import Path
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced


ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "examples/companion_radio/ui-new/UITask.cpp"


class CompanionHomeBleProfilesTest(unittest.TestCase):
    def test_actual_control_blocks_across_companion_profiles(self):
        source = UI.read_text()
        pages = extract_braced(source, "  enum HomePage") + ";"
        render_start = source.rfind("#ifdef COMPANION_EXCLUSIVE_WIFI_BLE", 0,
            source.index("    } else if (_page == HomePage::TRANSPORT)"))
        render = source[render_start:source.index(
            "    } else if (_page == HomePage::ADVERT)", render_start)]
        input_start = source.rfind("#ifdef COMPANION_EXCLUSIVE_WIFI_BLE", 0,
            source.index("    if (_page == HomePage::TRANSPORT\n"))
        control = source[input_start:source.index(
            "    if (key == KEY_ENTER && _page == HomePage::FIRST)", input_start)]

        harness = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#define PRESS_LABEL "hold button"
#define TOUCH_CENTER_ZONE_PERCENT 70
#define KEY_ENTER 13
#define KEY_UP 0xb5
#define KEY_DOWN 0xb6
enum class UIColor {corp_blue, secondary_txt, primary_txt};
enum class CompanionTransportMode {WiFi, Bluetooth};
bool wifi_active=true, save_ok=true;
CompanionTransportMode selected=CompanionTransportMode::WiFi;
int saves=0, choices=0;
bool isCompanionWiFiEnabled() { return wifi_active; }
CompanionTransportMode getCompanionTransportMode() { return selected; }
bool selectCompanionTransportMode(CompanionTransportMode value) {
  ++saves;
  if (!save_ok) return false;
  selected=value; return true;
}
namespace mesh { namespace ui {
struct Rect { int x=0,y=20,width=60,height=25; };
struct CompanionTransportSelectorLayout {
  bool show_title=false;
  int title_y=14,prompt_y=53;
  Rect wifi,bluetooth;
};
CompanionTransportSelectorLayout makeCompanionTransportSelectorLayout(int,int,int=34) { return {}; }
}}
const uint8_t bluetooth_on[]={1}, bluetooth_off[]={0};
struct Display {
  const uint8_t* icon=nullptr;
  int width() const { return 128; }
  int height() const { return 64; }
  void setColor(UIColor) {} void setTextSize(int) {} void setCompactText(bool) {}
  void drawTextCentered(int,int,const char*) {}
  void drawXbm(int,int,const uint8_t* value,int,int) { icon=value; }
};
void drawCompanionTransportChoice(Display&,int,int,int,int,const char*,bool,bool) { ++choices; }
struct Task {
  bool enabled=false;
  int enables=0,disables=0,reboots=0,alerts=0;
  bool isBluetoothEnabled() const { return enabled; }
  void enableBluetooth() { enabled=true; ++enables; }
  void disableBluetooth() { enabled=false; ++disables; }
  void showAlert(const char*,int) { ++alerts; }
  void shutdown(bool reboot) { assert(reboot); ++reboots; }
};
struct Home {
@PAGES@
  Task* _task;
  int _page=ADVERT, advert_renders=0;
  explicit Home(Task* task):_task(task) {}
  void render(Display& display) {
    const int body_top=20;
    if (false) {
@RENDER@
    } else if (_page==ADVERT) { ++advert_renders; }
  }
  bool input(uint8_t key) {
@CONTROL@
    return false;
  }
};
int main() {
  Task task; Home home(&task); Display display;
  static_assert(Home::FIRST==0 && Home::RECENT==1 && Home::RADIO==2,
                "ordinary and radio pages remain ordered");
  static_assert(Home::RADIO2==3 && Home::RADIO_STATUS==4,
                "custom secondary-radio pages remain present");
#if defined(COMPANION_EXCLUSIVE_WIFI_BLE) || defined(BLE_PIN_CODE)
  static_assert(Home::ADVERT==6 && Home::SHUTDOWN==7 && Home::DISCOVERY==8 && Home::Count==9,
                "only the actual transport control consumes a page");
#else
  static_assert(Home::ADVERT==5 && Home::SHUTDOWN==6 && Home::DISCOVERY==7 && Home::Count==8,
                "non-BLE profiles remove the inert page without losing sleep/discovery");
#endif
  home.render(display); assert(home.advert_renders==1);
#ifdef COMPANION_EXCLUSIVE_WIFI_BLE
  static_assert(Home::TRANSPORT==5, "exclusive selector precedes advert");
  home._page=Home::TRANSPORT;
  home.render(display); assert(choices==2 && display.icon==nullptr);
  assert(home.input(KEY_DOWN));
  assert(selected==CompanionTransportMode::Bluetooth && saves==1 && task.reboots==1);
  wifi_active=false;
  assert(home.input(KEY_UP));
  assert(selected==CompanionTransportMode::WiFi && saves==2 && task.reboots==2);
  // A failed save cannot reboot or change the selected transport.
  save_ok=false;
  assert(home.input(KEY_DOWN));
  assert(task.alerts==1 && task.reboots==2 && selected==CompanionTransportMode::WiFi);
  assert(task.enables==0 && task.disables==0);
#elif defined(BLE_PIN_CODE)
  static_assert(Home::BLUETOOTH==5, "BLE control precedes advert");
  home._page=Home::BLUETOOTH;
  home.render(display); assert(display.icon==bluetooth_off);
  assert(home.input(KEY_ENTER) && task.enabled && task.enables==1);
  home.render(display); assert(display.icon==bluetooth_on);
  assert(home.input(KEY_ENTER) && !task.enabled && task.disables==1);
  assert(choices==0 && saves==0 && task.reboots==0);
#else
  assert(!home.input(KEY_ENTER));
  assert(task.enables==0 && task.disables==0 && choices==0 && saves==0);
#endif
}
'''.replace("@PAGES@", pages).replace("@RENDER@", render).replace("@CONTROL@", control)
        profiles = {
            "usb_without_ble": ["-DENABLE_USB_INTERFACE=1"],
            "ble": ["-DBLE_PIN_CODE=123456"],
            "full_usb_with_ble": ["-DCOMPANION_RADIO_FULL=1", "-DENABLE_USB_INTERFACE=1", "-DBLE_PIN_CODE=123456"],
            "exclusive_full": ["-DCOMPANION_RADIO_FULL=1", "-DENABLE_USB_INTERFACE=1", "-DBLE_PIN_CODE=123456",
                               "-DCOMPANION_EXCLUSIVE_WIFI_BLE=1", "-DHAS_TOUCH=1"],
        }
        with tempfile.TemporaryDirectory(prefix="companion-home-ble-") as directory:
            for profile, flags in profiles.items():
                with self.subTest(profile=profile):
                    actual = subprocess.run(["c++", "-E", "-P", "-x", "c++", *flags, "-"],
                        input="\n".join((pages, render, control)), text=True, capture_output=True)
                    self.assertEqual(actual.returncode, 0, actual.stderr)
                    standalone = profile in ("ble", "full_usb_with_ble")
                    self.assertEqual(actual.stdout.count("HomePage::BLUETOOTH"), 2 if standalone else 0)
                    self.assertEqual("BLUETOOTH," in actual.stdout, standalone)
                    self.assertEqual("TRANSPORT," in actual.stdout, profile == "exclusive_full")
                    executable = Path(directory) / profile
                    built = subprocess.run(["c++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                        "-Wno-unused-parameter", "-Wno-unused-variable", "-fsanitize=address,undefined",
                        "-fno-pie", "-no-pie", *flags, "-x", "c++", "-", "-o", str(executable)],
                        input=harness, text=True, capture_output=True)
                    self.assertEqual(built.returncode, 0, built.stderr)
                    tested = subprocess.run([str(executable)], text=True, capture_output=True)
                    self.assertEqual(tested.returncode, 0, tested.stderr)

    def test_actual_clock_keeps_fractional_hours_and_rejects_invalid_floats(self):
        source = UI.read_text()
        start = source.index("        uint32_t now = _rtc->getCurrentTime();")
        clock = source[start:source.index("        DateTime dt (now);", start)]
        harness = r'''
#include <cassert>
#include <cstdint>
#include <cfloat>
#include <limits>
#include <math.h>
struct RTC { uint32_t value; uint32_t getCurrentTime() const { return value; } };
struct Prefs { float tz_offset; };
uint32_t localTime(float hours) {
  RTC rtc{1700000000}; Prefs prefs{hours};
  auto* _rtc=&rtc; auto* _node_prefs=&prefs;
@CLOCK@
  return now;
}
int main() {
  assert(localTime(0)==1700000000u);
  assert(localTime(5.5f)==1700019800u);
  assert(localTime(-3.5f)==1699987400u);
  assert(localTime(5.75f)==1700020700u);
  assert(localTime(-9.25f)==1699966700u);
  assert(localTime(-12)==1699956800u);
  assert(localTime(14)==1700050400u);
  for (float bad : {std::numeric_limits<float>::quiet_NaN(),
       std::numeric_limits<float>::infinity(), -std::numeric_limits<float>::infinity(),
       FLT_MAX, -FLT_MAX, 14.01f, -12.01f}) assert(localTime(bad)==1700000000u);
}
'''.replace("@CLOCK@", clock)
        with tempfile.TemporaryDirectory(prefix="companion-clock-") as directory:
            executable = Path(directory) / "clock"
            built = subprocess.run(["c++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                "-include", "initializer_list", "-fsanitize=undefined,float-cast-overflow",
                "-fno-sanitize-recover=all", "-x", "c++", "-", "-o", str(executable)],
                input=harness, text=True, capture_output=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            tested = subprocess.run([str(executable)], text=True, capture_output=True)
            self.assertEqual(tested.returncode, 0, tested.stderr)


if __name__ == "__main__": unittest.main()
