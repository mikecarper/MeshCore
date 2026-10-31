"""Protect built-in Cardputer buses and Windows cross-drive include paths."""

from pathlib import Path
import ntpath
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
VARIANT = ROOT / "variants/m5stack_cardputer_adv"


class CardputerBoardPolicyTests(unittest.TestCase):
    def test_full_transport_selection_preserves_live_bluetooth_controls(self):
        source = (VARIANT / "companion_radio/ui-cardputer/CardputerUITask.cpp").read_text()
        toggle = extract_braced(source, "void CardputerUITask::toggleBluetooth()")
        transport = extract_braced(source, "void CardputerUITask::toggleTransport()")
        code = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <string>
#include "CompanionBluetooth.h"
#include "CompanionWiFi.h"
static std::string command;
static bool fail = false;
bool handleCompanionBluetoothCommand(const char* text, char* reply, size_t size,
                                    CompanionBluetoothCommandSource) {
  command = text;
  snprintf(reply, size, "%s", fail ? "Error: save failed" : "OK - saved");
  return true;
}
#if defined(COMPANION_EXCLUSIVE_WIFI_BLE)
static CompanionTransportMode selected = CompanionTransportMode::Bluetooth;
CompanionTransportMode getCompanionTransportMode() { return selected; }
bool selectCompanionTransportMode(CompanionTransportMode next) {
  if (fail) return false;
  selected = next;
  return true;
}
#endif
struct CardputerUITask {
  bool enabled = true;
  std::string alert;
  bool isBluetoothEnabled() const { return enabled; }
  void showAlert(const char* text, int) { alert = text; }
  void toggleBluetooth();
#if defined(COMPANION_EXCLUSIVE_WIFI_BLE)
  void toggleTransport();
#endif
};
@METHOD@
#if defined(COMPANION_EXCLUSIVE_WIFI_BLE)
@TRANSPORT@
#endif
int main() {
  CardputerUITask task;
  task.toggleBluetooth();
  assert(command == "set bluetooth off" && task.alert == "saved");
  task.enabled = false; task.toggleBluetooth();
  assert(command == "set bluetooth on" && task.alert == "saved");
  fail = true; task.toggleBluetooth();
  assert(task.alert == "Error: save failed");
  fail = false; command.clear();
#if defined(COMPANION_EXCLUSIVE_WIFI_BLE)
  task.toggleTransport();
  assert(selected == CompanionTransportMode::WiFi && command.empty());
  assert(task.alert == "WiFi selected; reboot required");
  fail = true; task.toggleTransport();
  assert(selected == CompanionTransportMode::WiFi && task.alert == "Transport save failed");
  fail = false; task.toggleTransport();
  assert(selected == CompanionTransportMode::Bluetooth && command.empty());
  assert(task.alert == "Bluetooth selected; reboot required");
#endif
}
'''.replace("@METHOD@", toggle).replace("@TRANSPORT@", transport)
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a native C++ compiler is required")
        with tempfile.TemporaryDirectory(prefix="cardputer-transport-") as directory:
            source = Path(directory) / "transport.cpp"
            binary = Path(directory) / ("transport.exe" if os.name == "nt" else "transport")
            source.write_text(code, encoding="ascii")
            for full in (False, True):
                with self.subTest(full=full):
                    flags = (["-DESP32=1", "-DCOMPANION_EXCLUSIVE_WIFI_BLE=1",
                              '-DWIFI_SSID="test"', "-DBLE_PIN_CODE=123456"] if full else [])
                    sanitizer = [] if os.name == "nt" else ["-fsanitize=address,undefined",
                        "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
                    built = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                        *sanitizer, *flags, "-I", str(ROOT / "examples/companion_radio"),
                        str(source), "-o", str(binary)], capture_output=True, text=True, timeout=30)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    tested = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
                    self.assertEqual(tested.returncode, 0, tested.stdout + tested.stderr)

    def check_gpio(self, remove_board_guard=False):
        method = extract_braced((VARIANT / "CardputerAdvBoard.h").read_text(),
                                "bool isUserGpioAvailable(uint8_t pin) const override")
        if remove_board_guard:
            method = method.replace("if (pin == reserved) return false;",
                                    "if (pin == reserved) break;")
        source = r'''
#include <cassert>
#include <cstdint>
#define PIN_SD_CS 12
#include "globals.h"
class ESP32Board {
public:
  mutable unsigned calls = 0;
  virtual bool isUserGpioAvailable(uint8_t pin) const {
    ++calls;
    // Stand in for shared radio/USB/ADC/strapping exclusions. The variant
    // must retain a base veto, rather than replacing the common policy.
    return pin != 7 && pin < 49;
  }
};
class CardputerAdvBoard : public ESP32Board {
public:
@METHOD@
};
int main() {
  const uint8_t claimed[] = {
    0, 8, 9, 11, 12, 33, 34, 35, 36, 37, 38, 41, 42, 43, 46
  };
  CardputerAdvBoard board;
  for (unsigned pin = 0; pin < 256; ++pin) {
    bool local = false;
    for (uint8_t reserved : claimed) local |= pin == reserved;
    const unsigned before = board.calls;
    const bool available = board.isUserGpioAvailable(static_cast<uint8_t>(pin));
    assert(available == (!local && pin != 7 && pin < 49));
    assert(board.calls == before + unsigned(!local));
  }
}
'''.replace("@METHOD@", method)
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a native C++ compiler is required")
        with tempfile.TemporaryDirectory(prefix="cardputer-gpio-") as directory:
            source_path = Path(directory) / "policy.cpp"
            binary = Path(directory) / ("policy.exe" if os.name == "nt" else "policy")
            source_path.write_text(source, encoding="ascii")
            flags = [] if os.name == "nt" else ["-fsanitize=address,undefined",
                "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
            built = subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                *flags, "-I", str(VARIANT), str(source_path), "-o", str(binary)],
                capture_output=True, text=True, timeout=30)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            if remove_board_guard:
                self.assertNotEqual(checked.returncode, 0, "unprotected board pins passed")
                self.assertIn("Assertion", checked.stderr)
            else:
                self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_all_builtin_bus_pins_are_reserved_and_base_policy_is_preserved(self):
        self.check_gpio()

    def test_missing_board_guard_is_rejected(self):
        self.check_gpio(remove_board_guard=True)

    def test_windows_other_drive_keeps_include_and_shared_root_order(self):
        class Environment(dict):
            def subst(self, value):
                return self["PROJECT_PACKAGES_DIR"] if value == "$PROJECT_PACKAGES_DIR" else value

            def Flatten(self, values):
                return values

        packages = r"C:\Users\Mesh User\.platformio\packages"
        sdk = packages + r"\framework-arduinoespressif32-libs\esp32s3\include"
        project = r"D:\Radio Builds\MeshCore\src"
        paths = [sdk + r"\driver\include", project, sdk + r"\freertos\include",
                 r"C:\Users\Mesh User\.platformio\lib"]
        env = Environment(PROJECT_PACKAGES_DIR=packages, CPPPATH=paths)
        hook_path = VARIANT / "win_long_cmdline.py"
        code = compile(hook_path.read_text(), str(hook_path), "exec")
        hook = {"env": env, "Import": lambda name: None}
        with mock.patch.object(sys, "platform", "win32"), \
                mock.patch.object(os, "path", ntpath), mock.patch.object(os, "sep", "\\"):
            exec(code, hook)
            self.assertIsNone(hook["_prefix_root"](packages, project))
            flags = hook["_short_incflags"](env)
            prefix = ntpath.normcase(sdk).replace("\\", "/") + "/"
            self.assertEqual(flags, ["-iprefix", prefix, "-iwithprefixbefore",
                "driver/include", "-I" + project, "-iwithprefixbefore",
                "freertos/include", "-I" + paths[-1]])
            self.assertIn("_cardputer_short_incflags", env)
            # No root shared with the SDK: preserve every include unchanged.
            env["CPPPATH"] = [project]
            self.assertEqual(hook["_short_incflags"](env), ["-I" + project])

    def test_hook_leaves_other_operating_systems_unchanged(self):
        env = {}
        with mock.patch.object(sys, "platform", "linux"):
            runpy.run_path(str(VARIANT / "win_long_cmdline.py"),
                           init_globals={"env": env, "Import": lambda name: None})
        self.assertEqual(env, {})


if __name__ == "__main__":
    unittest.main()
