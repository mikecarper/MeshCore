import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class UsbKeyboardConsoleTest(unittest.TestCase):
    def test_real_keyboard_service_ignores_obscured_and_unrendered_input(self):
        compiler = shutil.which("g++")
        if not compiler:
            self.skipTest("g++ is not available")
        ui = (ROOT / "examples/companion_radio/ui-new/UITask.cpp").read_text()
        start = ui.index("mesh::usbkeyboard::EventType serviceKeyboard(")
        body = ui.index("{", start)
        depth = 0
        for end in range(body, len(ui)):
            if ui[end] == "{": depth += 1
            elif ui[end] == "}":
                depth -= 1
                if not depth: break
        service = ui[start:end + 1]
        harness = r'''
#include <cassert>
#include <deque>
#include "helpers/esp32/UsbKeyboardHost.h"
#include "helpers/ui/UsbKeyboardConsole.h"
namespace mesh { namespace ui {
enum class DisplayWake { Button };
bool usbKeyboardScreenFits(int w,int h,int line) { return w>=96 && line>0 && h>=line*6; }
} }
std::deque<mesh::usbkeyboard::Event> events;
namespace mesh { namespace usbkeyboard {
bool next(Event& event) { if (events.empty()) return false; event=events.front(); events.pop_front(); return true; }
} }
struct DisplayDriver {
  bool on=true; int font=0, wakes=0;
  void setTextSize(int) { ++font; }
  int width() { return 128; } int height() { return 64; } int textLineHeight() { return 8; }
  bool isOn() { return on; } void wake(mesh::ui::DisplayWake) { on=true; ++wakes; }
};
mesh::ui::UsbKeyboardConsole keyboard_console;
bool keyboard_present=false, keyboard_terminal_owned=false, keyboard_box_drawn=false;
int keyboard_measured_width=-1, keyboard_measured_height=-1;
bool keyboard_screen_fits=false; int submitted=0, keyboard_output=0;
struct Mesh { void setTerminalInputEditing(int&, bool) {} } the_mesh;
void releaseKeyboardTerminalIfIdle() {}
void closeKeyboardTerminal() { keyboard_console.disconnect(); keyboard_box_drawn=false; }
void submitKeyboardCommand() { ++submitted; keyboard_console.clearCommand(); }
@SERVICE@
void report(unsigned char key) {
  mesh::usbkeyboard::Event e{}; e.type=mesh::usbkeyboard::EventType::Report; e.report[2]=key;
  events.push_back(e);
}
int main() {
  DisplayDriver d;
  mesh::usbkeyboard::Event e{}; e.type=mesh::usbkeyboard::EventType::Connected; events.push_back(e);
  serviceKeyboard(&d,true); assert(keyboard_present && !keyboard_console.connected() && d.font==0);
  serviceKeyboard(&d,true); assert(keyboard_console.connected() && !keyboard_box_drawn && d.font==1);
  report(4); serviceKeyboard(&d,true); assert(keyboard_console.length()==0); // not drawn
  keyboard_box_drawn=true;
  report(4); serviceKeyboard(&d,true); assert(keyboard_console.length()==0); // still held
  report(0); serviceKeyboard(&d,true); report(4); serviceKeyboard(&d,true);
  assert(keyboard_console.length()==1);
  report(0); serviceKeyboard(&d,true);
  report(0x28); serviceKeyboard(&d,false); assert(submitted==0); // alert/pairing
  keyboard_box_drawn=true; report(0x28); serviceKeyboard(&d,true); assert(submitted==0);
  report(0); serviceKeyboard(&d,true);
  d.on=false; report(0x28); serviceKeyboard(&d,false); assert(d.on && d.wakes==1 && submitted==0);
  keyboard_box_drawn=true; report(0x28); serviceKeyboard(&d,true); assert(submitted==0);
  report(0); serviceKeyboard(&d,true); report(0x28); serviceKeyboard(&d,true); assert(submitted==1);
  report(0); serviceKeyboard(&d,true); report(0x29); serviceKeyboard(&d,true);
  assert(!keyboard_console.visible()); int font=d.font;
  report(0); serviceKeyboard(&d,true); report(4); serviceKeyboard(&d,true);
  assert(keyboard_console.length()==0 && d.font==font); // hidden leaves normal UI untouched
}
'''.replace("@SERVICE@", service)
        with tempfile.TemporaryDirectory() as temp:
            source = pathlib.Path(temp) / "service.cpp"
            binary = pathlib.Path(temp) / "service"
            source.write_text(harness)
            subprocess.run([compiler, "-std=c++17", "-DMESH_USB_KEYBOARD_HOST=1",
                            "-I", str(ROOT / "src"), str(source), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)

    def test_real_button_dispatch_keeps_actions_and_probes(self):
        compiler = shutil.which("g++")
        if not compiler:
            self.skipTest("g++ is not available")
        ui = (ROOT / "examples/companion_radio/ui-new/UITask.cpp").read_text()
        start = ui.index("  char c = 0;\n#if UI_HAS_JOYSTICK")
        end = ui.index("#if defined(UI_HAS_ROTARY_INPUT)", start)
        dispatch = ui[start:end]
        harness = r'''
#include <cassert>
#include <cstddef>
#define MESH_USB_KEYBOARD_HOST 1
#define PIN_USER_BTN 1
#define COMPANION_FEATURE_READER 0
#define BUTTON_EVENT_NONE 0
#define BUTTON_EVENT_CLICK 1
#define BUTTON_EVENT_LONG_PRESS 2
#define BUTTON_EVENT_DOUBLE_CLICK 3
#define BUTTON_EVENT_TRIPLE_CLICK 4
#define BUTTON_EVENT_QUADRUPLE_CLICK 5
#define KEY_ENTER 10
#define KEY_NEXT 11
#define KEY_PREV 12
#define KEY_SELECT 13
#define KEY_LEFT 14
#define KEY_RIGHT 15
int probes=0, called=0;
namespace mesh { namespace usbkeyboard { bool probe() { ++probes; return false; } } }
struct Button { int event=0; int check() { return event; } } user_btn, joystick_left, joystick_right, back_btn;
struct Display { bool on=true; bool isOn() { return on; } void dismiss() { called=5; } } display, *_display=&display;
char checkDisplayOn(char c) { called=1; return _display->on ? c : 0; }
char handleLongPress(char c) { called=2; return c; }
char handleDoubleClick(char c) { called=3; return c; }
char handleMultiClick(char c, bool) { called=4; return c; }
char dispatch() {
@DISPATCH@
return c;
}
int main() {
  assert(dispatch()==0 && probes==0 && called==0);
  user_btn.event=BUTTON_EVENT_CLICK;
#if UI_HAS_JOYSTICK || defined(UI_HAS_NAV_INPUT)
  assert(dispatch()==KEY_ENTER && called==1 && probes==1);
#else
  assert(dispatch()==KEY_NEXT && called==1 && probes==1);
#endif
  called=0; probes=0; user_btn.event=BUTTON_EVENT_LONG_PRESS;
#if defined(UI_HAS_NAV_INPUT) && !UI_HAS_JOYSTICK
  assert(dispatch()==0 && called==5 && probes==1);
#else
  assert(dispatch()==KEY_ENTER && called==2 && probes==1);
#endif
#if UI_HAS_JOYSTICK
  called=0; probes=0; user_btn.event=0; joystick_left.event=BUTTON_EVENT_CLICK;
  assert(dispatch()==KEY_LEFT && called==1 && probes==1);
  called=0; probes=0; joystick_left.event=0; back_btn.event=BUTTON_EVENT_TRIPLE_CLICK;
  assert(dispatch()==KEY_SELECT && called==4 && probes==1);
#else
  called=0; probes=0; user_btn.event=BUTTON_EVENT_DOUBLE_CLICK; display.on=false;
#ifdef UI_HAS_NAV_INPUT
  assert(dispatch()==KEY_SELECT && called==3 && probes==1);
#else
  assert(dispatch()==0 && called==1 && probes==1);
#endif
  display.on=true; called=0; probes=0; user_btn.event=BUTTON_EVENT_TRIPLE_CLICK;
  assert(dispatch()==KEY_SELECT && called==4 && probes==1);
#endif
}
'''.replace("@DISPATCH@", dispatch)
        with tempfile.TemporaryDirectory() as temp:
            source = pathlib.Path(temp) / "button.cpp"
            source.write_text(harness)
            for flags in (("-DUI_HAS_JOYSTICK=1",),
                          ("-DUI_HAS_JOYSTICK=0",),
                          ("-DUI_HAS_JOYSTICK=0", "-DUI_HAS_NAV_INPUT=1")):
                binary = pathlib.Path(temp) / ("button" + str(len(flags)) + flags[0][-1])
                subprocess.run([compiler, "-std=c++17", *flags, str(source), "-o", str(binary)], check=True)
                subprocess.run([str(binary)], check=True)

    def test_keyboard_and_overlay(self):
        compiler = shutil.which("g++")
        if not compiler:
            self.skipTest("g++ is not available")
        with tempfile.TemporaryDirectory() as temp:
            binary = pathlib.Path(temp) / "keyboard_test"
            subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
                            "-I", str(ROOT / "src"),
                            str(ROOT / "test" / "test_usb_keyboard_console.cpp"),
                            "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)

    def test_host_lifecycle(self):
        compiler = shutil.which("g++")
        if not compiler:
            self.skipTest("g++ is not available")
        with tempfile.TemporaryDirectory() as temp:
            binary = pathlib.Path(temp) / "keyboard_host_test"
            subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                            "-I", str(ROOT / "test" / "fixtures" / "usb_keyboard"),
                            "-I", str(ROOT / "src"),
                            str(ROOT / "test" / "test_usb_keyboard_host.cpp"),
                            "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)

    def test_board_sleep_follows_keyboard_host(self):
        import test_esp32_usb_sleep as existing
        compiler = shutil.which("g++")
        if not compiler:
            self.skipTest("g++ is not available")
        methods = existing.board_sleep_methods() + "\n" + "\n".join(
            existing.board_method(signature) for signature in (
                "bool isUsbDataConnected() override", "bool isUsbHostConnected() override",
                "bool isUsbSleepHeld()"))
        harness = existing.HARNESS.split("int main()", 1)[0].replace(
            "namespace esp32 {",
            "namespace usbkeyboard { bool on = false; bool active() { return on; } }\nnamespace esp32 {",
            1).replace("@METHODS@", methods) + r'''
int main() {
  ESP32Board board;
  Serial.end();
  mesh::usbkeyboard::on = true;
  board.sleep(30);
  require(sleep_calls == 0, "USB scanning must block light sleep");
  mesh::usbkeyboard::on = false;
  board.sleep(30);
  require(sleep_calls == 1, "idle keyboard host must release the sleep veto");
}
'''
        with tempfile.TemporaryDirectory() as temp:
            source = pathlib.Path(temp) / "keyboard_sleep.cpp"
            binary = pathlib.Path(temp) / "keyboard_sleep"
            source.write_text(harness, encoding="ascii")
            subprocess.run([compiler, "-std=c++17", "-DMESH_USB_KEYBOARD_HOST=1",
                            "-DARDUINO_USB_CDC_ON_BOOT=0", "-DARDUINO_USB_MODE=0",
                            "-DMESH_ESP32_USB_CONSOLE_COOPERATIVE=0", "-DMESH_USB_LOGGING_AVAILABLE=0",
                            "-I", str(ROOT / "src"), str(source), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)

    def test_companion_sleep_follows_keyboard_host(self):
        import test_esp32_full_sleep as existing
        compiler = shutil.which("g++")
        if not compiler:
            self.skipTest("g++ is not available")
        main = (ROOT / "examples" / "companion_radio" / "main.cpp").read_text()
        ready = existing.function(main, "static bool companionNativeUsbLightSleepReady()")
        source_text = r'''
#include <stdint.h>
#include <assert.h>
namespace mesh {
namespace usbkeyboard { bool on = false; bool active() { return on; } }
namespace esp32 { bool manualLightSleepReady() { return true; } }
}
struct Wireless { uint8_t enabled() { return 0; } } companion_wireless;
struct Interfaces { bool hasPendingIO() { return false; } } interface_manager;
struct Board { bool isOTAUpdateRunning() { return false; } bool isRadioTestActive() { return false; } } board;
struct Sensors { bool gpsUsesSerialUart(int) { return false; } } sensors;
@READY@
int main() {
  mesh::usbkeyboard::on = true;
  assert(!companionNativeUsbLightSleepReady());
  mesh::usbkeyboard::on = false;
  assert(companionNativeUsbLightSleepReady());
}
'''.replace("@READY@", ready)
        with tempfile.TemporaryDirectory() as temp:
            source = pathlib.Path(temp) / "companion_sleep.cpp"
            binary = pathlib.Path(temp) / "companion_sleep"
            source.write_text(source_text, encoding="ascii")
            subprocess.run([compiler, "-std=c++17", "-DMESH_USB_KEYBOARD_HOST=1",
                            str(source), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()
