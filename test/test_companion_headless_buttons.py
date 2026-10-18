#!/usr/bin/env python3
"""Run production Companion button routing and helpers with no display."""

from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "examples/companion_radio/ui-new/UITask.cpp"

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <initializer_list>
#include <string>
#define PIN_USER_BTN 7
#define UI_HAS_JOYSTICK 0
#define UI_WIFI_SETUP_HOME_PAGE 0
#define MESH_DEBUG_PRINTLN(...)
constexpr char KEY_ENTER='e', KEY_NEXT='n', KEY_PREV='p', KEY_SELECT='s', KEY_UP='u', KEY_DOWN='d';
constexpr int BUTTON_EVENT_CLICK=1, BUTTON_EVENT_LONG_PRESS=2, BUTTON_EVENT_DOUBLE_CLICK=3;
constexpr int BUTTON_EVENT_TRIPLE_CLICK=4, BUTTON_EVENT_QUADRUPLE_CLICK=5;
uint32_t now_ms=0;
uint32_t millis() { return now_ms; }
bool visual_allowed=false;
unsigned visual_checks=0;
void visual() { assert(visual_allowed && "headless visual path"); ++visual_checks; }
namespace mesh { namespace ui {
enum class DisplayWake { Button };
bool shouldDisplayBluetoothPairingPin(bool enabled, bool connected, uint32_t pin) {
  return enabled && !connected && pin;
}
}}
struct Mesh {
  unsigned notifications=0, rescues=0;
  void notificationButton() { ++notifications; }
  void enterCLIRescue() { ++rescues; }
  uint32_t getBLEPin() const { return 123456; }
} the_mesh;
struct Button {
  int event=0;
  int check() { const int result=event; event=0; return result; }
} user_btn;
struct Display {
  bool on=true;
  unsigned wakes=0, dismissals=0;
  bool isOn() const { visual(); return on; }
  void wake(mesh::ui::DisplayWake) { visual(); ++wakes; on=true; }
  void dismiss() { visual(); ++dismissals; on=false; }
} display;
struct UIScreen {
  unsigned inputs=0;
  char last=0;
  void handleInput(char key) { visual(); ++inputs; last=key; }
};
struct HomeScreen : UIScreen {
  bool radio_page=false;
  bool isRadioPage() const { visual(); return radio_page; }
  void showFirstPage() { visual(); }
};
struct UITask {
  Display* _display=nullptr;
  HomeScreen home_screen;
  UIScreen preview_screen, reader_screen;
  UIScreen *home=&home_screen, *msg_preview=&preview_screen, *reader=&reader_screen, *curr=home;
  bool _pairing_from_button=false, _startup_complete=false;
  uint32_t _pairing_screen_until=0, _next_refresh=0, ui_started_at=0;
  unsigned buzzer_changes=0, reader_opens=0, reader_closes=0;
  bool isPairingScreenActive() const { return false; }
  bool isBluetoothEnabled() const { return false; }
  bool hasBluetoothConnection() const { return false; }
  void showPairingPin(bool) { visual(); }
  bool isReaderActive() const { visual(); return reader && curr==reader; }
  void closeReader() { visual(); ++reader_closes; curr=home; }
  void showReader() { visual(); ++reader_opens; curr=reader; }
  void setCurrScreen(UIScreen* screen) { visual(); curr=screen; }
  void toggleBuzzer() { ++buzzer_changes; }
  char checkDisplayOn(char);
  char handleLongPress(char);
  char handleDoubleClick(char);
  char handleMultiClick(char, bool);
  void pollButtons();
};
@HELPERS@
void UITask::pollButtons() {
@BUTTONS@
}
void event(UITask& ui, int code) { user_btn.event=code; ui.pollButtons(); }
void headless() {
  visual_allowed=false;
  for (int screen=0; screen<3; ++screen) {
    for (int code : {BUTTON_EVENT_CLICK, BUTTON_EVENT_DOUBLE_CLICK,
                     BUTTON_EVENT_TRIPLE_CLICK, BUTTON_EVENT_QUADRUPLE_CLICK}) {
      UITask ui;
      ui.curr=screen==0 ? ui.home : screen==1 ? ui.msg_preview : ui.reader;
      now_ms=10000; visual_checks=0; the_mesh={};
      event(ui, code);
      assert(visual_checks==0 && the_mesh.rescues==0);
      assert(the_mesh.notifications==1 && "physical notification acknowledgement survives");
      assert(ui.buzzer_changes==(code==BUTTON_EVENT_TRIPLE_CLICK || code==BUTTON_EVENT_QUADRUPLE_CLICK));
      assert(ui.home_screen.inputs==0 && ui.preview_screen.inputs==0 && ui.reader_screen.inputs==0);
      assert(ui.reader_opens==0 && ui.reader_closes==0);
    }
    // The early physical rescue remains available without consulting screens.
    for (bool startup_complete : {false, true}) {
      UITask ui;
      ui.curr=screen==0 ? ui.home : screen==1 ? ui.msg_preview : ui.reader;
      ui._startup_complete=startup_complete;
      ui.ui_started_at=UINT32_MAX-4000u;
      now_ms=500; the_mesh={}; visual_checks=0;
      event(ui, BUTTON_EVENT_LONG_PRESS);
      assert(the_mesh.rescues==unsigned(!startup_complete));
      assert(visual_checks==0 && the_mesh.notifications==0);
      now_ms=5000; the_mesh={};
      event(ui, BUTTON_EVENT_LONG_PRESS);
      assert(the_mesh.rescues==0 && visual_checks==0);
    }
  }
}
void visible() {
  visual_allowed=true; now_ms=10000;
  UITask ui; ui._display=&display; ui._startup_complete=true;
  display={}; the_mesh={};
  event(ui, BUTTON_EVENT_CLICK);
  assert(ui.home_screen.inputs==1 && the_mesh.notifications==1);
#ifdef UI_HAS_NAV_INPUT
  assert(ui.home_screen.last==KEY_ENTER);
#else
  assert(ui.home_screen.last==KEY_NEXT);
#endif
  // A single wake gesture remains consumed by the existing display policy.
  display.on=false;
  event(ui, BUTTON_EVENT_CLICK);
  assert(display.on && ui.home_screen.inputs==1);
  ui.curr=ui.msg_preview;
  event(ui, BUTTON_EVENT_DOUBLE_CLICK);
#ifdef UI_HAS_NAV_INPUT
  assert(ui.preview_screen.last==KEY_SELECT);
#else
  assert(ui.preview_screen.last==KEY_PREV);
#endif
  event(ui, BUTTON_EVENT_TRIPLE_CLICK);
  assert(ui.preview_screen.last==KEY_DOWN && ui.buzzer_changes==0);
  event(ui, BUTTON_EVENT_QUADRUPLE_CLICK);
  assert(ui.preview_screen.last==KEY_UP && ui.buzzer_changes==0);
  ui.curr=ui.home;
  event(ui, BUTTON_EVENT_TRIPLE_CLICK);
  assert(ui.buzzer_changes==1);
#if COMPANION_FEATURE_READER
  ui.curr=ui.reader;
  event(ui, BUTTON_EVENT_LONG_PRESS);
  assert(ui.reader_closes==1 && ui.curr==ui.home);
  ui.home_screen.radio_page=true;
  event(ui, BUTTON_EVENT_LONG_PRESS);
  assert(ui.reader_opens==1 && ui.curr==ui.reader);
#endif
}
int main(int argc, char** argv) {
  assert(argc==2);
  if (std::string(argv[1])=="headless") headless(); else visible();
}
'''


def fixture(source):
    loop = extract_braced(source, "void UITask::loop()")
    buttons = loop[loop.index("  char c = 0;"):loop.index("  userLedHandler();")]
    helpers = "\n".join(extract_braced(source, "char UITask::" + name)
                        for name in ("checkDisplayOn(", "handleLongPress(",
                                     "handleDoubleClick(", "handleMultiClick("))
    return HARNESS.replace("@HELPERS@", helpers).replace("@BUTTONS@", buttons)


class CompanionHeadlessButtonTests(unittest.TestCase):
    def build(self, directory, source, *, nav=False, reader=True):
        cpp, binary = directory / "test.cpp", directory / "test"
        directory.mkdir()
        cpp.write_text(fixture(source))
        sanitizers = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
                      if sys.platform.startswith("linux") else [])
        result = subprocess.run([
            os.environ.get("CXX", "g++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
            *sanitizers, "-DCOMPANION_FEATURE_READER=" + str(int(reader)),
            *(["-DUI_HAS_NAV_INPUT=1"] if nav else []), str(cpp), "-o", str(binary)
        ], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return binary

    def run_case(self, binary, scenario, *, negative=False):
        result = subprocess.run([str(binary), scenario], capture_output=True, text=True, timeout=10)
        if negative:
            self.assertNotEqual(result.returncode, 0, "old headless navigation unexpectedly survived")
        else:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_actual_button_mapping_and_helpers_with_and_without_display(self):
        with tempfile.TemporaryDirectory(prefix="companion-headless-") as directory:
            for nav, reader in ((False, False), (False, True), (True, False), (True, True)):
                with self.subTest(nav=nav, reader=reader):
                    binary = self.build(Path(directory) / f"{nav}-{reader}", UI.read_text(),
                                        nav=nav, reader=reader)
                    self.run_case(binary, "headless")
                    self.run_case(binary, "visible")

    def test_original_null_navigation_is_detected(self):
        source = UI.read_text()
        helper = extract_braced(source, "char UITask::checkDisplayOn(")
        old_helper = helper.replace("return _display != NULL ? c : 0;", "return c;")
        self.assertNotEqual(helper, old_helper)
        source = source.replace(helper, old_helper)
        old_dispatch = "if (c != 0 && curr && _display != NULL)"
        self.assertIn(old_dispatch, source)
        source = source.replace(old_dispatch, "if (c != 0 && curr)")
        with tempfile.TemporaryDirectory(prefix="companion-headless-old-") as directory:
            binary = self.build(Path(directory) / "old", source)
            self.run_case(binary, "headless", negative=True)


if __name__ == "__main__":
    unittest.main()
