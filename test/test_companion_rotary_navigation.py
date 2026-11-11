#!/usr/bin/env python3
"""Exercise production UI loops with custom/native rotary and button handoff."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced
from test_serial_wifi_sessions import SANITIZER_FLAGS

ROOT = Path(__file__).resolve().parents[1]

ADAPTER = r'''
constexpr char KEY_NEXT='n', KEY_PREV='p', KEY_ENTER='e', KEY_SELECT='s';
constexpr int BUTTON_EVENT_NONE=0, BUTTON_EVENT_CLICK=1, BUTTON_EVENT_LONG_PRESS=2,
    BUTTON_EVENT_DOUBLE_CLICK=3, BUTTON_EVENT_TRIPLE_CLICK=4, BUTTON_EVENT_QUADRUPLE_CLICK=5;
enum class RotaryInputEvent { None, Next, Prev, Select, LongPress };
struct RotaryInput {
  unsigned polls=0; bool rotation_polling=true; RotaryInputEvent pending=RotaryInputEvent::None;
  void setRotationPolling(bool active) { rotation_polling=active; }
  RotaryInputEvent poll() {
    ++polls; auto e=pending; pending=RotaryInputEvent::None;
    if (!rotation_polling && (e==RotaryInputEvent::Next || e==RotaryInputEvent::Prev))
      return RotaryInputEvent::None;
    return e;
  }
} custom_input, rotary_input;
bool custom_enabled=true, shared_button=true;
struct ButtonAdapter {
  unsigned polls=0, resumes=0; int pending=0;
  int check() { ++polls; int e=pending; pending=0; return e; }
  void update() { ++polls; }
  void resume() { ++resumes; pending=0; }
} user_btn;
namespace mesh { namespace ui {
RotaryInput* customRotaryInput() { return custom_enabled ? &custom_input : nullptr; }
bool rotaryOwnsButtonPin(unsigned) { return custom_enabled && shared_button; }
bool shouldDisplayBluetoothPairingPin(bool,bool,unsigned) { return false; }
uint8_t radioProfileDisplayPageCount(bool,uint8_t) { return 4; }
} }
'''

METHODS = r'''
  bool _displayWasOn=true;
  unsigned _radio_profile_display_page=0;
  ButtonAdapter* _userButton=&user_btn;
  bool hasBluetoothConnection() const { return false; }
  void showPairingPin(bool) {}
  void resetRadioProfileDisplayPage() { _radio_profile_display_page=0; }
  void clearMsgPreview() { _origin[0]=_msg[0]=0; }
  char checkDisplayOn(char);
  char handleDoubleClick(char c) { return c; }
  char handleMultiClick(char c,bool) { return c; }
  char handleTripleClick(char c) { return c; }
  void handleButtonAnyPress();
  void handleButtonShortPress();
'''

SCENARIOS = r'''
static uint8_t radioProfileStatusPageCount(Display&,bool) { return 3; }
@LOOP@
@HANDLERS@
int main() {
  now_ms=10000;
  UITask ui;
  ui._startup_complete=true;
  ui.ui_started_at=0;
  // The same physical switch must never be polled by both drivers.
  custom_input.pending=RotaryInputEvent::Next;
  ui.loop();
  assert(user_btn.polls==0);
#if UI_ORIG
  assert(ui._radio_profile_display_page==1);
#else
  assert(ui.screen.last=='n');
#endif
  custom_input.pending=RotaryInputEvent::Prev;
  ui.loop();
#if UI_ORIG
  assert(ui._radio_profile_display_page==0);
#else
  assert(ui.screen.last=='p');
#endif
  custom_input.pending=RotaryInputEvent::Select;
  ui.loop();
  assert(the_mesh.rescues==0 && ui.shutdowns==0);
#if !UI_ORIG
  assert(ui.screen.last=='e');
#endif
  // A sleeping screen consumes the first action, including hold, as wake only.
  for (auto event : {RotaryInputEvent::Next,RotaryInputEvent::Prev,
                    RotaryInputEvent::Select,RotaryInputEvent::LongPress}) {
    ui.display.on=false; ui.screen.last=0;
    custom_input.pending=event; ui.loop();
    assert(ui.display.on && ui.screen.last==0);
#if UI_ORIG
    assert(ui._radio_profile_display_page==0); // Wake returns to main status.
#else

#endif
    assert(the_mesh.rescues==0 && ui.shutdowns==0);
  }
  // Power saving permits sleep with the screen off and retains button wake.
  ui.prefs.powersaving_enabled=true; ui.display.on=false;
  ui.screen.last=0; custom_input.pending=RotaryInputEvent::Next; ui.loop();
  assert(!custom_input.rotation_polling && !ui.display.on && ui.screen.last==0);
  custom_input.pending=RotaryInputEvent::Select; ui.loop();
  assert(ui.display.on && ui.screen.last==0 && custom_input.rotation_polling);
  custom_input.pending=RotaryInputEvent::Next; ui.loop();
  assert(custom_input.rotation_polling);
#if !UI_ORIG
  assert(ui.screen.last=='n');
#endif
  ui.prefs.powersaving_enabled=false;
  // Awake holds retain the original early-boot rescue action.
  ui._startup_complete=false; ui.ui_started_at=now_ms-1000;
  custom_input.pending=RotaryInputEvent::LongPress; ui.loop();
  assert(the_mesh.rescues==1);
  // With no screen, custom events cannot perform destructive actions.
  ui._display=nullptr; custom_input.pending=RotaryInputEvent::LongPress; ui.loop();
  assert(the_mesh.rescues==1 && ui.shutdowns==0);
  ui._display=&ui.display; ui._startup_complete=true;
  // A switch handed back to its native handler is resumed exactly once.
  user_btn.pending=BUTTON_EVENT_LONG_PRESS;
  custom_enabled=false; ui.loop();
  assert(user_btn.resumes==1 && user_btn.polls==1 && user_btn.pending==0);
  assert(the_mesh.rescues==1 && ui.shutdowns==0);
#if UI_NEW
  assert(rotary_input.polls==1); // Native rotary backend was untouched until fallback.
  rotary_input.pending=RotaryInputEvent::Prev; ui.loop();
  assert(ui.screen.last=='p' && rotary_input.polls==2);
#else
  ui.loop();
#endif
  assert(user_btn.resumes==1);
  // A separate native button remains usable alongside a custom encoder.
  custom_enabled=true; shared_button=false;
  const auto before=user_btn.polls;
  ui.loop(); assert(user_btn.polls==before+1);
  std::puts("PASS: rotary navigation, wake, native fallback and switch ownership");
}
'''


class RotaryNavigationTests(unittest.TestCase):
    def test_actual_ui_loops(self):
        compiler = shutil.which('g++') or shutil.which('clang++')
        self.assertIsNotNone(compiler)
        fixture = (ROOT/'test/fixtures/companion_ui_deadlines.cpp').read_text(encoding='ascii')
        prefix = fixture.split('@LOOP@', 1)[0]
        prefix = prefix.replace('namespace UIColor', ADAPTER+'\nnamespace UIColor', 1)
        prefix = prefix.replace('struct Prefs {', 'struct Prefs { bool powersaving_enabled=false;')
        prefix = prefix.replace('void enterCLIRescue()',
            'bool notificationButton() { return false; } unsigned getBLEPin() const { return 123456; } '
            'bool isDualRadioActive() const { return true; } void enterCLIRescue()')
        prefix = prefix.replace('void handleInput(char) {}', 'char last=0; void handleInput(char c) { last=c; }')
        prefix = prefix.replace('  void setCurrScreen(UIScreen* screen);', '  void setCurrScreen(UIScreen* value) { curr=value; }')
        prefix = prefix.replace('  void handleButtonLongPress();', METHODS+'\n  void handleButtonLongPress();', 1)
        with tempfile.TemporaryDirectory(prefix='rotary-ui-') as work:
            for variant in ('ui-new','ui-tiny','ui-orig'):
                with self.subTest(variant=variant):
                    source=(ROOT/f'examples/companion_radio/{variant}/UITask.cpp').read_text(encoding='ascii')
                    signatures=(['void UITask::handleButtonAnyPress()',
                        'void UITask::handleButtonShortPress()', 'void UITask::handleButtonLongPress()']
                        if variant=='ui-orig' else ['char UITask::checkDisplayOn(char c)',
                            'char UITask::handleLongPress(char c)'])
                    generated=prefix+SCENARIOS.replace('@LOOP@',extract_braced(source,'void UITask::loop()')).replace(
                        '@HANDLERS@','\n'.join(extract_braced(source,s) for s in signatures))
                    cpp=Path(work)/f'{variant}.cpp'; binary=Path(work)/variant
                    cpp.write_text(generated,encoding='ascii')
                    command=[compiler,'-std=c++17','-Wall','-Wextra','-Werror','-Wno-unused-function',
                        *SANITIZER_FLAGS,'-DMESH_CUSTOM_PERIPHERALS=1','-DPIN_USER_BTN=0',
                        '-DUI_HAS_JOYSTICK=0','-DCOMPANION_FEATURE_READER=0','-DAUTO_OFF_MILLIS=0',
                        '-DAUTO_SHUTDOWN_MILLIVOLTS=3400','-DUI_ORIG='+str(int(variant=='ui-orig')),
                        '-DUI_NEW='+str(int(variant=='ui-new')),
                        *(['-DUI_HAS_ROTARY_INPUT=1'] if variant=='ui-new' else []),str(cpp),'-o',str(binary)]
                    built=subprocess.run(command,capture_output=True,text=True,timeout=60)
                    self.assertEqual(built.returncode,0,built.stdout+built.stderr)
                    checked=subprocess.run([str(binary)],capture_output=True,text=True,timeout=10)
                    self.assertEqual(checked.returncode,0,checked.stdout+checked.stderr)
                    print(variant,checked.stdout.strip())


if __name__=='__main__': unittest.main()
