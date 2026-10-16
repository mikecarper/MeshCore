#!/usr/bin/env python3
"""Run the stock website console against production CLI and sleep code.

USB/RTOS peripherals and time are simulated; the website's Web Streams client,
repeater command pump, power-saving setters/getters, and ESP32Board methods are
real. Physical browser/HIL testing remains necessary for USB enumeration.
"""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.request

from test_esp32_usb_sleep import HARNESS, board_method
from test_hwcdc_tx_backport import body

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "45a5257bf792b11a83f4c51c18d4f312addfb677"
URL = f"https://raw.githubusercontent.com/meshcore-dev/flasher.meshcore.io/{COMMIT}/lib/console.js"
SHA256 = "107d3ad867915aea8b2454c324327397a0f9e2f0469656046054011bc64475f9"
SIZE = 2455


def official_console():
    explicit = os.environ.get("MESHCORE_OFFICIAL_WEB_CONSOLE")
    path = Path(explicit) if explicit else Path.home() / ".cache/meshcore-tests" / SHA256 / "console.js"
    if not path.exists():
        if explicit:
            raise AssertionError(f"Missing explicit web console source: {path}")
        with urllib.request.urlopen(URL, timeout=45) as response:
            data = response.read(SIZE + 1)
        if len(data) != SIZE or hashlib.sha256(data).hexdigest() != SHA256:
            raise AssertionError("Pinned official console download failed its source gate")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    data = path.read_bytes()
    if len(data) != SIZE or hashlib.sha256(data).hexdigest() != SHA256:
        raise AssertionError(f"Official web console SHA/size mismatch: {path}")
    return path.resolve()


def firmware_harness(*, lose_sleep_guard=False, lose_crlf=False,
                     lose_raw_uart_guard=False, lose_dual_radio_guard=False):
    # Retain the existing host peripheral boundary; use the production methods
    # and a virtual clock to exercise minutes/hours without slowing CI.
    source = HARNESS.split("int main()", 1)[0]
    sleep_held = board_method("bool isUsbSleepHeld()")
    if lose_sleep_guard:
        # Remove the actual native USB veto while retaining host observation.
        # The stock client must lose its reply after the real sleep branch runs.
        guard = ("return host_connected || usb_host_sleep_policy.shouldKeepAwake(\n"
                 "        millis(), MESH_ESP32_USB_HOST_LOSS_SLEEP_GRACE_MS);")
        assert guard in sleep_held, "Missing native USB negative-control boundary"
        sleep_held = sleep_held.replace(guard, "return false;")
    methods = "\n".join(board_method(signature) for signature in (
        "void sleep(uint32_t secs) override", "bool isUsbDataConnected() override",
        "bool isUsbHostConnected() override")) + "\n" + sleep_held
    if lose_raw_uart_guard:
        expression = "keep_awake = keep_awake || static_cast<bool>(Serial);"
        assert expression in methods, "Missing raw UART negative-control boundary"
        methods = methods.replace(expression, "keep_awake = keep_awake || false;")
    source = source.replace("@METHODS@", methods)
    source = source.replace("  uint32_t irq = 48;", "  void loop() {}\n  uint32_t irq = 48;")
    cli = (ROOT / "src/helpers/CommonCLI.cpp").read_text()
    setter = body(cli, 'if (strcmp(config, "powersaving on") == 0')
    getter = body(cli, 'if (strcmp(config, "powersaving") == 0)')
    main = (ROOT / "examples/simple_repeater/main.cpp").read_text()
    pump = body(main, "static void __attribute__((noinline)) serviceCommandInterfaces()")
    if lose_crlf:
        pump = pump.replace('console.printf("  -> %s\\r\\n", reply);',
                            'console.printf("  -> %s\\n", reply);')
    start = main.index("  bool can_power_save =")
    end = main.index("  if (the_mesh.getNodePrefs()->reboot_interval", start)
    morse_declaration = "  const bool morse_timing = mesh::morseTimingActive(&radio_driver);"
    assert morse_declaration in main[:start], "Missing live Morse ownership snapshot"
    power_loop = morse_declaration + "\n" + main[start:end]
    if lose_dual_radio_guard:
        guard = " && !the_mesh.isDualRadioActive()"
        assert power_loop.count(guard) == 1, "Missing radio2 idle-delay negative-control boundary"
        power_loop = power_loop.replace(guard, "")
    morse_helper = body((ROOT / "src/helpers/MorseLoopPolicy.h").read_text(),
                        "inline bool morseTimingActive(const Radio* radio)")
    # Model only RF ownership at the peripheral boundary; use the real helper
    # and loop guards rather than making every invocation permanently idle.
    morse_radio = r'''
namespace mesh {
struct Radio {
  bool tx = false, rx = false, cw = false;
  bool isMorseTxActive() const { return tx; }
  bool isMorseRxActive() const { return rx; }
};
@MORSE_HELPER@
}
static mesh::Radio radio_driver;
'''.replace("@MORSE_HELPER@", morse_helper)
    fixture = (ROOT / "test/fixtures/official_web_console/firmware.cpp").read_text()
    return source + morse_radio + (fixture.replace("@SETTER@", setter).replace("@GETTER@", getter)
                     .replace("@COMMAND_PUMP@", pump).replace("@POWER_LOOP@", power_loop))


class OfficialWebConsoleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")
        cls.compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not cls.node or not cls.compiler:
            raise unittest.SkipTest("Node 18+ and a host C++17 compiler are required")
        cls.console = official_console()

    def run_contract(self, *, lose_sleep_guard=False, lose_crlf=False,
                     raw_uart=False, lose_raw_uart_guard=False):
        with tempfile.TemporaryDirectory(prefix="official-web-console-") as tmp:
            tmp = Path(tmp)
            source = tmp / "firmware.cpp"
            source.write_text(firmware_harness(lose_sleep_guard=lose_sleep_guard,
                                              lose_crlf=lose_crlf,
                                              lose_raw_uart_guard=lose_raw_uart_guard), encoding="ascii")
            binary = tmp / "firmware"
            sanitizers = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                           "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])
            result = subprocess.run([self.compiler, "-std=c++17", "-Wall", "-Wextra",
                "-DARDUINO_USB_MODE=1", "-DARDUINO_USB_CDC_ON_BOOT=" + str(int(not raw_uart)),
                "-DESP32_PLATFORM=1",
                "-DMESH_ESP32_USB_CONSOLE_COOPERATIVE=" + str(int(not raw_uart)),
                "-DMESH_USB_CONSOLE_COOPERATIVE=" + str(int(not raw_uart)),
                "-DMESH_USB_LOGGING_AVAILABLE=1",
                "-DCONFIG_TINYUSB_ENABLED=0", "-I", str(ROOT / "src"),
                *sanitizers, str(source), "-o", str(binary)],
                text=True, capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([self.node,
                str(ROOT / "test/fixtures/official_web_console/contract.mjs"),
                str(self.console), str(binary), str(int(raw_uart))],
                text=True, capture_output=True, timeout=20)
            return result

    def test_stock_console_crlf_idle_power_saving_and_reopen(self):
        result = self.run_contract()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_raw_uart_stock_console_idle_power_saving_and_reopen(self):
        result = self.run_contract(raw_uart=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_missing_raw_uart_sleep_guard_breaks_stock_console(self):
        result = self.run_contract(raw_uart=True, lose_raw_uart_guard=True)
        self.assertNotEqual(result.returncode, 0, "Negative control did not lose the console")
        self.assertIn("idle query", result.stderr)

    def test_missing_usb_sleep_guard_breaks_stock_console(self):
        result = self.run_contract(lose_sleep_guard=True)
        self.assertNotEqual(result.returncode, 0, "Negative control did not lose the console")
        self.assertIn("idle query", result.stderr)

    def test_lf_only_reply_is_not_visible_in_stock_console(self):
        result = self.run_contract(lose_crlf=True)
        self.assertNotEqual(result.returncode, 0, "Negative control did not detect buffered replies")
        self.assertIn("power-saving setter", result.stderr)

    def run_dual_radio_trace(self, *, lose_dual_radio_guard=False):
        source = firmware_harness(lose_dual_radio_guard=lose_dual_radio_guard)
        source = source.split("int main() {", 1)[0] + r'''
int main() {
  attachHost(true); Serial.terminal_open = true;
  prefs.powersaving_enabled = 1; mock_millis = 300000;
  const unsigned before = yield_ms;
  the_mesh.dual_radio_active = true;
  power_loop();
  require(sleep_calls == 0 && yield_ms == before, "Active radio2 inserted an idle delay");
  the_mesh.dual_radio_active = false;
  power_loop();
  require(sleep_calls == 0 && yield_ms == before + 1, "Idle delay did not resume after radio2");
  prefs.powersaving_enabled = 0;
  power_loop();
  require(sleep_calls == 0 && yield_ms == before + 1, "PS-off idle behavior changed");
}
'''
        with tempfile.TemporaryDirectory(prefix="official-console-radio2-ownership-") as temporary:
            cpp = Path(temporary) / "ownership.cpp"
            binary = Path(temporary) / "ownership"
            cpp.write_text(source, encoding="ascii")
            sanitizers = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                           "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])
            result = subprocess.run([self.compiler, "-std=c++17", "-Wall", "-Wextra",
                "-DARDUINO_USB_MODE=1", "-DARDUINO_USB_CDC_ON_BOOT=1", "-DESP32_PLATFORM=1",
                "-DMESH_ESP32_USB_CONSOLE_COOPERATIVE=1", "-DMESH_USB_CONSOLE_COOPERATIVE=1",
                "-DMESH_USB_LOGGING_AVAILABLE=1", "-DCONFIG_TINYUSB_ENABLED=0",
                "-I", str(ROOT / "src"), *sanitizers, str(cpp), "-o", str(binary)],
                text=True, capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return subprocess.run([str(binary)], text=True, capture_output=True, timeout=20)

    def test_actual_radio2_ownership_skips_idle_delay_then_resumes(self):
        result = self.run_dual_radio_trace()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_missing_radio2_idle_guard_is_detected(self):
        result = self.run_dual_radio_trace(lose_dual_radio_guard=True)
        self.assertNotEqual(result.returncode, 0, "Negative control did not detect radio2 idle delay")
        self.assertIn("Active radio2 inserted an idle delay", result.stderr)

    def test_actual_morse_ownership_blocks_sleep_and_idle_delay_then_resumes(self):
        source = firmware_harness().split("int main() {", 1)[0] + r'''
int main() {
  attachHost(false); Serial.terminal_open = false;
  prefs.powersaving_enabled = 1; mock_millis = 300000;
  const unsigned before = yield_ms;
  radio_driver.tx = true;
  require(mesh::morseTimingActive(&radio_driver), "Morse TX ownership was lost");
  power_loop();
  require(sleep_calls == 0 && yield_ms == before, "Morse TX slept or inserted an idle delay");
  radio_driver.tx = false; radio_driver.rx = true;
  require(mesh::morseTimingActive(&radio_driver), "Morse RX ownership was lost");
  power_loop();
  require(sleep_calls == 0 && yield_ms == before, "Morse RX slept or inserted an idle delay");
  radio_driver.rx = false; radio_driver.cw = true;
  require(!mesh::morseTimingActive(&radio_driver), "Unkeyed CW changed the Morse loop policy");
  radio_driver.cw = false;
  power_loop();
  require(sleep_calls == 1, "Power saving did not resume when Morse released RF ownership");
}
'''
        with tempfile.TemporaryDirectory(prefix="official-console-morse-ownership-") as temporary:
            cpp = Path(temporary) / "ownership.cpp"
            binary = Path(temporary) / "ownership"
            cpp.write_text(source, encoding="ascii")
            sanitizers = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                           "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])
            result = subprocess.run([self.compiler, "-std=c++17", "-Wall", "-Wextra",
                "-DARDUINO_USB_MODE=1", "-DARDUINO_USB_CDC_ON_BOOT=1", "-DESP32_PLATFORM=1",
                "-DMESH_ESP32_USB_CONSOLE_COOPERATIVE=1", "-DMESH_USB_CONSOLE_COOPERATIVE=1",
                "-DMESH_USB_LOGGING_AVAILABLE=1", "-DCONFIG_TINYUSB_ENABLED=0",
                "-I", str(ROOT / "src"), *sanitizers, str(cpp), "-o", str(binary)],
                text=True, capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(binary)], text=True, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
