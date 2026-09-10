#!/usr/bin/env python3
"""Compile actual UI hooks and CLI branches for touch, legacy and absent UIs."""

from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "examples/companion_radio/ui-new"
MESH = ROOT / "examples/companion_radio/MyMesh.cpp"


class TouchDebugCliTest(unittest.TestCase):
    def test_runtime_default_toggle_redraw_and_unsupported_targets(self):
        abstract = (ROOT / "examples/companion_radio/AbstractUITask.h").read_text()
        header = (UI / "UITask.h").read_text()
        hooks = ("supportsTouchDebug() const", "isTouchDebugEnabled() const",
                 "setTouchDebugEnabled(bool enabled)")
        base = "\n".join(extract_braced(abstract, "virtual bool " + hook) for hook in hooks)
        overrides = "\n".join(extract_braced(header, "bool " + hook) for hook in hooks)
        fields = re.search(r"  bool _touch_debug_enabled[^\n]*\n"
                           r"  int _touch_debug_x[^\n]*\n"
                           r"  UIScreen\* _touch_debug_screen[^\n]*", header).group()
        mesh = MESH.read_text()
        getter = extract_braced(mesh, 'if (strcmp(command, "get display.touch") == 0)')
        setter = extract_braced(mesh, 'if (strncmp(command, "set display.touch", 17) == 0')
        source = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <string>
#include <initializer_list>
struct UIScreen {};
struct Display {};
struct AbstractUITask {
BASE_HOOKS
};
struct UITask : AbstractUITask {
  Display* _display=nullptr;
  unsigned long _next_refresh=999;
  int wakes=0;
  char checkDisplayOn(char c) { assert(c==0); ++wakes; return 0; }
FIELDS
#ifdef HAS_TOUCH
OVERRIDES
#endif
};
struct MyMesh {
  AbstractUITask* _ui=nullptr;
  bool command(const char* command, char* reply, size_t reply_size) {
    if(!command || !reply || !reply_size) return false;
GETTER
SETTER
    return false;
  }
};
int main() {
  Display display;
  UITask ui;
  MyMesh mesh;
  mesh._ui=&ui;
  auto command=[&](const char* text) {
    char reply[160]={};
    assert(mesh.command(text,reply,sizeof(reply)));
    return std::string(reply);
  };
  assert(!ui.isTouchDebugEnabled() && !ui.supportsTouchDebug());
  assert(command("get display.touch").find("unsupported")!=std::string::npos);
  assert(command("set display.touch on").find("unsupported")!=std::string::npos);
  ui._display=&display;
#ifdef HAS_TOUCH
  assert(ui.supportsTouchDebug());
  assert(command("get display.touch")=="display.touch off (off after reboot)");
  assert(command("set display.touch on")=="OK - display.touch on (off after reboot)");
  assert(ui.isTouchDebugEnabled() && ui.wakes==1 && ui._next_refresh==0);
  assert(command("get display.touch")=="display.touch on (off after reboot)");
  // Invalid inputs must not mutate state or accidentally accept a suffix.
  for(const char* input : {"set display.touch", "set display.touch ",
      "set display.touch toggle", "set display.touch only", "set display.touch OFF",
      "set display.touch 1", "set display.touch off extra", "set display.touch on reboot"}) {
    assert(command(input)=="Error: use set display.touch on|off");
    assert(ui.isTouchDebugEnabled() && ui.wakes==1);
  }
  UIScreen screen;
  ui._touch_debug_x=48; ui._touch_debug_y=145;
  ui._touch_debug_area=4; ui._touch_debug_screen=&screen;
  ui._next_refresh=999;
  assert(command("set display.touch\t off")=="OK - display.touch off (off after reboot)");
  assert(!ui.isTouchDebugEnabled() && ui.wakes==1 && ui._next_refresh==0);
  assert(ui._touch_debug_x==-1 && ui._touch_debug_y==-1 && ui._touch_debug_area==-1);
  assert(ui._touch_debug_screen==nullptr);
  assert(command("set display.touch on").find("OK")==0);
  UITask rebooted;
  rebooted._display=&display;
  assert(!rebooted.isTouchDebugEnabled());
#else
  assert(!ui.supportsTouchDebug());
  assert(command("set display.touch on").find("unsupported")!=std::string::npos);
  assert(!ui.isTouchDebugEnabled() && ui.wakes==0);
#endif
  char reply[160]={};
  for(const char* input : {"get display.touching", "set display.touching on", "get role"})
    assert(!mesh.command(input,reply,sizeof(reply)));
  assert(!mesh.command(nullptr,reply,sizeof(reply)));
  assert(!mesh.command("get display.touch",nullptr,sizeof(reply)));
  assert(!mesh.command("get display.touch",reply,0));
  // No missing/legacy UI may crash or claim it enabled an invisible feature.
  AbstractUITask legacy;
  for(AbstractUITask* target : {&legacy,static_cast<AbstractUITask*>(nullptr)}) {
    mesh._ui=target;
    assert(command("get display.touch").find("unsupported")!=std::string::npos);
    assert(command("set display.touch off").find("unsupported")!=std::string::npos);
  }
  // Short binary reply capacities remain bounded, including a one-byte reply.
  mesh._ui=&ui;
  for(size_t capacity : {size_t(1),size_t(8),size_t(20)}) {
    char tiny[21]; memset(tiny,'!',sizeof(tiny));
    assert(mesh.command("get display.touch",tiny,capacity));
    assert(tiny[capacity-1]==0 && tiny[capacity]=='!');
  }
}
'''.replace("BASE_HOOKS", base).replace("OVERRIDES", overrides).replace(
            "FIELDS", fields).replace("GETTER", getter).replace("SETTER", setter)
        for touch in (False, True):
            with self.subTest(touch=touch), tempfile.TemporaryDirectory() as temp:
                binary = Path(temp) / "touch_debug_cli"
                compiled = subprocess.run(["c++", "-std=c++11", "-O1",
                    "-fsanitize=address,undefined", "-fno-pie", "-no-pie",
                    *(["-DHAS_TOUCH=1"] if touch else []),
                    "-x", "c++", "-", "-o", str(binary)],
                    input=source, text=True, capture_output=True)
                self.assertEqual(compiled.returncode, 0, compiled.stderr)
                tested = subprocess.run([str(binary)], text=True, capture_output=True)
                self.assertEqual(tested.returncode, 0, tested.stderr)

    def test_shared_runtime_feature_is_wired_to_cli_and_rendering(self):
        source = (UI / "UITask.cpp").read_text()
        header = (UI / "UITask.h").read_text()
        mesh = MESH.read_text()
        self.assertNotIn("UI_TOUCH_DEBUG_OUTLINES", source + header)
        self.assertIn('#ifdef HAS_TOUCH\n  #include <helpers/ui/TouchDebugOverlay.h>', source)
        self.assertIn('#ifdef HAS_TOUCH\n  bool supportsTouchDebug()', header)
        self.assertIn("bool _touch_debug_enabled = false;", header)
        loop = extract_braced(source, "void UITask::loop()")
        feedback = extract_braced(loop, "if (_touch_debug_enabled)")
        self.assertIn("touchDebugAreaAt", feedback)
        draw = extract_braced(loop, "if (_touch_debug_enabled && !isPairingScreenActive())")
        self.assertIn("drawTouchDebugAreas", draw)
        local = extract_braced(mesh, "bool MyMesh::handleLocalControlCommand(")
        self.assertIn('"get display.touch"', local)
        handler = extract_braced(mesh, "bool MyMesh::handleCommand(const char* command,")
        self.assertIn("handleLocalControlCommand(command, reply, reply_capacity)", handler)
        terminal = extract_braced(mesh, "void MyMesh::handleTerminalCommand(")
        self.assertIn("handleLocalControlCommand(command, local_reply, sizeof(local_reply))", terminal)
        self.assertIn('get display.touch\\r\\n', terminal)
        # No persistent preference schema or write is involved.
        setter = extract_braced(local, 'if (strncmp(command, "set display.touch", 17) == 0')
        self.assertNotIn("savePrefs", setter)
        self.assertNotIn("_prefs", setter)
        for profile in (ROOT / "variants").rglob("platformio.ini"):
            self.assertNotIn("UI_TOUCH_DEBUG_OUTLINES", profile.read_text())


if __name__ == "__main__":
    unittest.main()
