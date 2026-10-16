#!/usr/bin/env python3
"""Exercise actual notification LED adapters in separate UI/sink translation units.

The UI boundary is its actual RGB writer and ownership/disable guard, not the
complete boot/flourish/heartbeat state machine. Arduino I/O, board availability,
storage success, and unrelated UI outputs are host adapters, not hardware proof.
"""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).resolve().parent / "fixtures/notification_led_ownership.cpp"
RGB = "defined(PIN_STATUS_LED_R) && defined(PIN_STATUS_LED_G) && defined(PIN_STATUS_LED_B)"

HARNESS = r'''
#pragma once
#include <helpers/CompanionNotificationPolicy.h>
#include <cstdint>
#include <cstring>
inline unsigned levels[64] = {}, analog_writes[64] = {}, digital_writes[64] = {}, mode_writes[64] = {};
inline unsigned ui_admitted = 0;
constexpr unsigned LOW = 0, HIGH = 1, OUTPUT = 1;
inline void analogWrite(uint8_t pin, unsigned value) { levels[pin] = value; ++analog_writes[pin]; }
inline void digitalWrite(uint8_t pin, unsigned value) { levels[pin] = value ? 255 : 0; ++digital_writes[pin]; }
inline void pinMode(uint8_t pin, unsigned) { ++mode_writes[pin]; }
inline void resetIo() {
  std::memset(levels, 0, sizeof(levels));
  std::memset(analog_writes, 0, sizeof(analog_writes));
  std::memset(digital_writes, 0, sizeof(digital_writes));
  std::memset(mode_writes, 0, sizeof(mode_writes));
  ui_admitted = 0;
}
inline unsigned ioWrites() {
  unsigned count = 0;
  for (unsigned pin = 0; pin < 64; ++pin) count += analog_writes[pin] + digital_writes[pin] + mode_writes[pin];
  return count;
}
class UITask {
public:
  uint8_t _notification_outputs = 31;
  bool _notification_led_owned = false;
  uint8_t extra_capabilities = 0;
  @LED_OWNER@
  @OUTPUTS@
  uint8_t notificationCapabilities() const { return extra_capabilities; }
  void notificationVibration(bool) {}
  void notificationMelody(const char*) {}
  void notificationScreen(int8_t) {}
  void userLedHandler();
};
struct Board { bool isUserGpioAvailable(uint8_t) const { return false; } };
inline Board board;
class MyMesh : public mesh::notify::Sink, public mesh::notify::Store {
public:
  UITask ui;
  UITask* _ui = &ui;
  unsigned saves = 0;
  mesh::notify::Controller _notifications{*this, *this};
  uint8_t capabilities() const override;
  bool gpioAvailable(uint8_t) const override;
  void pulse(mesh::notify::Output, bool, int8_t) override;
  void melody(const char*) override;
  void screen(int8_t) override;
  bool save(const mesh::notify::Settings&) override { ++saves; return true; }
  void syncNotifications() { @SYNC@ }
};
void seedUiRgb();
void clearUiRgb();
'''


def production_sources():
    notifications = (ROOT / "examples/companion_radio/Notifications.cpp").read_text(encoding="utf-8")
    ui = (ROOT / "examples/companion_radio/ui-orig/UITask.cpp").read_text(encoding="utf-8")
    abstract = (ROOT / "examples/companion_radio/AbstractUITask.h").read_text(encoding="utf-8")
    mesh = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text(encoding="utf-8")
    sync_start = mesh.index("    const auto& notifications = _notifications.settings();")
    sync = mesh[sync_start:mesh.index("  #endif", sync_start)]
    header = HARNESS.replace("@LED_OWNER@", extract_braced(abstract, "void setNotificationLedOwnership("))
    header = header.replace("@OUTPUTS@", extract_braced(abstract, "void setNotificationOutputs("))
    header = header.replace("@SYNC@", sync)
    sink = '#include "notification_harness.h"\n' + "\n".join(
        extract_braced(notifications, signature) for signature in (
            "uint8_t MyMesh::capabilities(", "bool MyMesh::gpioAvailable(",
            "void MyMesh::pulse(", "void MyMesh::melody(", "void MyMesh::screen("))
    macro_start = ui.index("#if " + RGB)
    macro_end = ui.index("#ifndef USER_BTN_PRESSED", macro_start)
    actual_guard = extract_braced(ui, "void UITask::userLedHandler()")
    guard_end = actual_guard.index("#ifdef STATUS_LED_RGB")
    ui_source = '#include "notification_harness.h"\n' + ui[macro_start:macro_end]
    ui_source += "\n#ifdef STATUS_LED_RGB\n" + extract_braced(ui, "static void statusLedWrite(") + "\n#endif\n"
    ui_source += "void seedUiRgb() {\n#ifdef STATUS_LED_RGB\nstatusLedWrite(200, 120, 90);\n#endif\n}\n"
    ui_source += "void clearUiRgb() {\n#ifdef STATUS_LED_RGB\nstatusLedWrite(0, 0, 0);\n#endif\n}\n"
    ui_source += actual_guard[:guard_end] + "\n++ui_admitted;\n}\n"
    return header, sink, ui_source


class NotificationLedOwnershipTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = shutil.which("g++") or shutil.which("clang++")
        if cls.compiler is None:
            raise unittest.SkipTest("a host C++17 compiler is required")
        cls.header, cls.sink, cls.ui = production_sources()

    def run_variant(self, name, definitions, expected_rgb, expected_mono, on_level=255):
        with tempfile.TemporaryDirectory(prefix="notification-led-ownership-", dir=os.environ.get("TMPDIR")) as directory:
            work = Path(directory)
            for filename, source in (("notification_harness.h", self.header),
                                     ("notification_sink.cpp", self.sink), ("notification_ui.cpp", self.ui)):
                (work / filename).write_text(source, encoding="ascii")
            binary = work / name
            command = [self.compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                       "-Wno-misleading-indentation",
                       *(["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
                         if sys.platform.startswith("linux") else []),
                       *["-D" + value for value in definitions],
                       f"-DEXPECT_RGB={int(expected_rgb)}", f"-DEXPECT_MONO={int(expected_mono)}",
                       f"-DEXPECT_ON_LEVEL={on_level}", "-I" + str(work), "-I" + str(ROOT / "src"),
                       str(work / "notification_sink.cpp"), str(work / "notification_ui.cpp"), str(FIXTURE),
                       str(ROOT / "src/helpers/CompanionNotificationPolicy.cpp"), "-o", str(binary)]
            built = subprocess.run(command, capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
            print(f"\n[{name}]\n" + checked.stdout + checked.stderr, flush=True)
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            expected_scenarios = 10 if expected_rgb or expected_mono else 4
            self.assertEqual(checked.stdout.count("PASS:"), expected_scenarios, checked.stdout)
            self.assertRegex(checked.stdout, rf"{expected_scenarios} scenarios, [0-9]+ checks, 0 failures")

    def test_meshtracker_x1_rgb(self):
        recipe = (ROOT / "variants/meshtracker_x1/platformio.ini").read_text(encoding="utf-8")
        for flag in ("PIN_STATUS_LED=24", "PIN_STATUS_LED_R=3", "PIN_STATUS_LED_G=24", "PIN_STATUS_LED_B=28"):
            self.assertIn("-D " + flag, recipe)
        self.assertIn("-I examples/companion_radio/ui-orig", recipe)
        self.assertIn("[env:MeshTracker_X1_companion_radio_ble]", recipe)
        self.assertNotIn("STATUS_LED_RGB", recipe)
        self.run_variant("meshtracker-x1-rgb", ["PIN_STATUS_LED=24", "PIN_STATUS_LED_R=3",
                         "PIN_STATUS_LED_G=24", "PIN_STATUS_LED_B=28"], True, False)

    def test_complete_rgb_without_mono_alias(self):
        self.run_variant("complete-rgb-only", ["PIN_STATUS_LED_R=3", "PIN_STATUS_LED_G=24",
                         "PIN_STATUS_LED_B=28", "LED_STATE_ON=LOW"], True, False)

    def test_mono_default_active_high(self):
        self.run_variant("mono-default-high", ["PIN_STATUS_LED=24"], False, True)

    def test_mono_explicit_active_high(self):
        self.run_variant("mono-explicit-high", ["PIN_STATUS_LED=24", "LED_STATE_ON=HIGH"], False, True)

    def test_mono_active_low(self):
        self.run_variant("mono-low", ["PIN_STATUS_LED=24", "LED_STATE_ON=LOW"], False, True, 0)

    def test_no_led(self):
        self.run_variant("no-led", [], False, False)

    def test_incomplete_rgb_is_not_led(self):
        self.run_variant("incomplete-rgb-only", ["PIN_STATUS_LED_R=3", "PIN_STATUS_LED_G=24"], False, False)

    def test_incomplete_rgb_preserves_mono(self):
        self.run_variant("incomplete-rgb-mono", ["PIN_STATUS_LED=24", "PIN_STATUS_LED_R=3"], False, True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
