"""Execute the read-only USB backend report with actual compiled flag branches."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
// Minimal concrete types stand in for each SDK's actual primary Serial type.
class HardwareSerial {};
class HWCDC {};
class USBCDC {};
class Adafruit_USBD_CDC {};
class CustomSerial {};
PRIMARY_SERIAL Serial;
#include <helpers/UsbMode.h>
#include <helpers/UsbLoggingClientActivity.h>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <initializer_list>

@DISPATCH@

int main() {
  assert(!strcmp(mesh::usbModeName<decltype(Serial)>(), EXPECTED_MODE));
  const char expected[] = "usb.mode " EXPECTED_MODE;
  char reply[160] = {};
  assert(mesh::handleUsbModeCommand("get usb.mode", reply, sizeof(reply), Serial));
  assert(!strcmp(reply, expected));
  assert(strlen(reply) < 32);
  // This detection query neither pauses capture nor grants a logging-client
  // watchdog lease. Stats polling remains a separate explicit mechanism.
  assert(!mesh::isUsbLoggingStatsCommand("get usb.mode"));
  for (const char* raw : {"get usb.mode", "GET usb.mode", "GeT usb.mode",
       "xy|get usb.mode", "xy| \tget usb.mode", " \txy|GET usb.mode"}) {
    assert(mesh::isUsbModeDetectionCommand(raw));
    assert(!mesh::isUsbLoggingStatsCommand(raw));
  }
  for (const char* raw : std::initializer_list<const char*>{nullptr, "", "x", "xy", "xy|",
       "x|get usb.mode", "xyz|get usb.mode", "get usb.mode ", "get usb.mode extra",
       "xy|set usb.mode tinyusb", "xy|get usb.modex", "get USB.MODE"})
    assert(!mesh::isUsbModeDetectionCommand(raw));
  for (const char* invalid : std::initializer_list<const char*>{nullptr, "", "get usb.modex", "get usb.mode extra",
       "get usb.mode\n", "get usb.mode\t", "get USB.MODE", "usb.mode",
       "set usb.mode", "set usb.mode tinyusb", "set usb.mode hardware-cdc-jtag"}) {
    strcpy(reply, "unchanged");
    assert(!mesh::handleUsbModeCommand(invalid, reply, sizeof(reply), Serial));
    assert(!strcmp(reply, "unchanged"));
  }
  assert(!mesh::handleUsbModeCommand("get usb.mode", nullptr, sizeof(reply), Serial));
  strcpy(reply, "unchanged");
  assert(!mesh::handleUsbModeCommand("get usb.mode", reply, 0, Serial));
  assert(!strcmp(reply, "unchanged"));
  for (size_t capacity = 1; capacity <= sizeof(expected); ++capacity) {
    unsigned char guarded[160];
    memset(guarded, 0xA5, sizeof(guarded));
    assert(mesh::handleUsbModeCommand("get usb.mode",
        reinterpret_cast<char*>(guarded + 1), capacity, Serial));
    assert(guarded[0] == 0xA5 && guarded[capacity + 1] == 0xA5);
    assert(guarded[capacity] == 0);
    assert(!strncmp(reinterpret_cast<char*>(guarded + 1), expected, capacity - 1));
  }
  @DISPATCH_CASES@
}
'''


class UsbModeTests(unittest.TestCase):
    def compile_and_run(self, directory, name, flags, expected, dispatch="", cases="", primary=None):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("a host C++17 compiler is required")
        source = Path(directory) / (name + ".cpp")
        source.write_text(HARNESS.replace("@DISPATCH@", dispatch)
                          .replace("@DISPATCH_CASES@", cases), encoding="ascii")
        binary = source.with_suffix(".exe")
        if primary is None:
            primary = {"hardware-cdc-jtag": "HWCDC", "tinyusb": "USBCDC",
                       "uart": "HardwareSerial"}.get(expected, "CustomSerial")
            if "NRF52_PLATFORM" in flags and expected == "tinyusb":
                primary = "Adafruit_USBD_CDC"
        built = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
            *([] if os.name == "nt" else ["-fsanitize=address,undefined",
                "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]),
            *["-D" + flag for flag in flags], '-DEXPECTED_MODE="' + expected + '"',
            "-DPRIMARY_SERIAL=" + primary,
            "-I" + str(ROOT / "src"), str(source), "-o", str(binary)],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_actual_backend_flags_and_bounded_read_only_command(self):
        cases = (
            (["ESP32", "ARDUINO_USB_MODE=1", "ARDUINO_USB_CDC_ON_BOOT=1"], "hardware-cdc-jtag"),
            (["ESP32", "ARDUINO_USB_MODE=0", "ARDUINO_USB_CDC_ON_BOOT=1"], "tinyusb"),
            (["ESP32", "ARDUINO_USB_MODE=1", "ARDUINO_USB_CDC_ON_BOOT=0"], "uart"),
            (["ESP32", "ARDUINO_USB_MODE=0", "ARDUINO_USB_CDC_ON_BOOT=0"], "uart"),
            (["ESP32", "ARDUINO_USB_MODE=1"], "uart"),
            (["ESP32"], "uart"),
            (["ESP32", "ARDUINO_USB_CDC_ON_BOOT=1"], "unknown"),
            (["ESP32", "ARDUINO_USB_MODE=7", "ARDUINO_USB_CDC_ON_BOOT=1"], "unknown"),
            (["ESP32_PLATFORM", "ARDUINO_USB_MODE=1", "ARDUINO_USB_CDC_ON_BOOT=1"], "hardware-cdc-jtag"),
            (["NRF52_PLATFORM", "USE_TINYUSB", "MESH_DUAL_CDC_LOGGING"], "tinyusb"),
            (["NRF52_PLATFORM"], "unknown"),
            (["RP2040_PLATFORM"], "unknown"),
            ([], "unknown"),
        )
        with tempfile.TemporaryDirectory(prefix="meshcore-usb-mode-") as directory:
            for index, (flags, expected) in enumerate(cases):
                with self.subTest(flags=flags):
                    self.compile_and_run(directory, "mode" + str(index), flags, expected)
            # Exact flags alone are insufficient when a board redirects Serial.
            for index, (flags, primary, expected) in enumerate((
                (["ESP32", "ARDUINO_USB_MODE=1", "ARDUINO_USB_CDC_ON_BOOT=1"], "HardwareSerial", "uart"),
                (["ESP32", "ARDUINO_USB_MODE=1", "ARDUINO_USB_CDC_ON_BOOT=1"], "CustomSerial", "unknown"),
                (["ESP32", "ARDUINO_USB_MODE=0", "ARDUINO_USB_CDC_ON_BOOT=1"], "HWCDC", "unknown"),
                (["NRF52_PLATFORM", "USE_TINYUSB"], "CustomSerial", "unknown"),
            )):
                with self.subTest(primary_override=primary, flags=flags):
                    self.compile_and_run(directory, "alias" + str(index), flags, expected, primary=primary)

    def test_actual_common_and_companion_dispatch_including_v14_request_prefix(self):
        common_source = (ROOT / "src/helpers/CommonCLI.cpp").read_text()
        companion_source = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        common = method(common_source, "void CommonCLI::handleCommand(")
        getter = method(common_source, "void CommonCLI::handleGetCmd(")
        companion = method(companion_source, "bool MyMesh::handleCommand(")
        control = method(companion_source, "bool MyMesh::handleLocalControlCommand(")
        self.assertNotIn("handleUsbModeCommand", common)
        self.assertNotIn("handleUsbModeCommand", companion)
        self.assertIn("handleUsbModeCommand", getter)
        self.assertIn("handleUsbModeCommand", control)
        # Execute each actual entry and getter registration, retaining the V14
        # prefix/capacity handling while omitting unrelated hardware branches.
        common = common.split("#if MESH_ENABLE_HOST_KISS", 1)[0] + method(
            common, 'if (memcmp(command, "get ", 4) == 0)') + "\n}"
        getter = getter[:getter.index("  const char* config")] + "\n}"
        control = control[:control.index("#if defined(ESP32_PLATFORM)")] + "return false;\n}"
        companion = companion[:companion.index("#if defined(ESP32_PLATFORM)")]
        companion += "  return handleLocalControlCommand(command, reply, reply_capacity);\n}"
        common = common.replace("{", "{ (void)sender_timestamp;", 1)
        getter = getter.replace("{", "{ (void)sender_timestamp;", 1)
        companion = companion.replace("{", "{ (void)sender_timestamp;", 1)
        dispatch = r'''
struct PrefsSaveReplyGuard {
  PrefsSaveReplyGuard(unsigned&, char*) {}
};
namespace mesh { namespace cli { void normalizeCommandVerb(char*) {} } }
struct CommonCLI {
  unsigned _prefs_save_failures = 0;
  void handleCommand(uint32_t, char*, char*);
  void handleGetCmd(uint32_t, char*, char*);
};
struct MyMesh {
  bool handleCommand(const char*, uint32_t, char*);
  bool handleLocalControlCommand(const char*, char*, size_t);
};
'''
        cases = r'''
  CommonCLI common;
  MyMesh companion;
  char command[] = "get usb.mode";
  common.handleCommand(0, command, reply);
  assert(!strcmp(reply, expected) && common._prefs_save_failures == 0);
  assert(companion.handleCommand(command, 0, reply));
  assert(!strcmp(reply, expected));
  assert(companion.handleCommand("xy|get usb.mode", 0, reply));
  assert(!strncmp(reply, "xy|", 3) && !strcmp(reply + 3, expected));
  assert(!companion.handleCommand("set usb.mode tinyusb", 0, reply));
'''
        with tempfile.TemporaryDirectory(prefix="meshcore-usb-mode-dispatch-") as directory:
            for index, (flags, expected) in enumerate((
                (["ESP32", "ARDUINO_USB_MODE=1", "ARDUINO_USB_CDC_ON_BOOT=1"], "hardware-cdc-jtag"),
                (["ESP32", "ARDUINO_USB_MODE=0", "ARDUINO_USB_CDC_ON_BOOT=1"], "tinyusb"),
                (["NRF52_PLATFORM", "USE_TINYUSB", "MESH_DUAL_CDC_LOGGING"], "tinyusb"),
                (["STM32_PLATFORM"], "unknown"))):
                self.compile_and_run(directory, "dispatch" + str(index), flags, expected,
                                     dispatch + getter + control + common + companion, cases)
        full = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        frame = full[full.index("mesh::companion::isRunCliFrame"):]
        self.assertIn("handleCommand(text, 0, reply_buf)", frame)
        for role, implementation in (("simple_repeater", "MyMesh.cpp"),
                                     ("simple_room_server", "MyMesh.cpp"),
                                     ("simple_sensor", "SensorMesh.cpp")):
            source = (ROOT / "examples" / role / implementation).read_text()
            self.assertTrue("_cli.handleCommand(sender_timestamp, command, reply)" in source,
                            role + " must route to the shared CommonCLI")


if __name__ == "__main__":
    unittest.main()
