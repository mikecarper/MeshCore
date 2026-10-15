"""Lab startup trace is opt-in and cannot enter native/dedicated KISS UARTs."""
from pathlib import Path
import os
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "examples/kiss_modem/main.cpp").read_text(encoding="utf-8")
TRACE = "#if" + SOURCE.split("// Lab-only boot diagnostics", 1)[1].split(
    "#if", 1
)[1].split("#if MESH_ESP32_HWCDC_SESSION_GUARD", 1)[0]


class KissBootTraceTests(unittest.TestCase):
    def test_actual_trace_preprocessor_is_uart_only_and_opt_in(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("a host C++ preprocessor is required")
        cases = [
            (["ESP32=1", "MESH_HIL_KISS_BOOT_TRACE=1"], True),
            (["ESP32=1", "MESH_HIL_KISS_BOOT_TRACE=1", "ARDUINO_USB_CDC_ON_BOOT=0"], True),
            (["ESP32=1"], False),
            (["ESP32=1", "MESH_HIL_KISS_BOOT_TRACE=0"], False),
            (["NRF52_PLATFORM=1", "MESH_HIL_KISS_BOOT_TRACE=1"], False),
            (["ESP32=1", "MESH_HIL_KISS_BOOT_TRACE=1", "ARDUINO_USB_MODE=0",
              "ARDUINO_USB_CDC_ON_BOOT=1"], False),
            (["ESP32=1", "MESH_HIL_KISS_BOOT_TRACE=1", "ARDUINO_USB_MODE=1",
              "ARDUINO_USB_CDC_ON_BOOT=1"], False),
            (["ESP32=1", "MESH_HIL_KISS_BOOT_TRACE=1", "KISS_UART_RX=5",
              "KISS_UART_TX=6"], False),
        ]
        for defines, enabled in cases:
            with self.subTest(defines=defines):
                result = subprocess.run(
                    [compiler, "-E", "-P", "-x", "c++", *[f"-D{x}" for x in defines], "-"],
                    input=TRACE + '\nKISS_UART_BOOT_TRACE\nKISS_BOOT_STAGE("probe");\n',
                    capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip().splitlines()[0], "1" if enabled else "0")
                self.assertEqual("Serial.println" in result.stdout, enabled)

    def test_fixed_stages_bracket_startup_before_binary_loop(self):
        setup = SOURCE.split("void setup() {", 1)[1].split("void loop() {", 1)[0]
        self.assertLess(setup.index("Serial.begin(115200)"), setup.index("board.begin()"))
        for operation, before, after in [
            ("board.begin()", "board.before", "board.after"),
            ("radio_driver.begin()", "driver.before", "driver.after"),
            ("loadOrCreateIdentity()", "identity.before", "identity.after"),
            ("sensors.begin()", "sensors.before", "sensors.after"),
            ("modem->begin()", "modem.before", "modem.after"),
        ]:
            self.assertLess(setup.index(f'KISS_BOOT_STAGE("{before}")'), setup.index(operation))
            self.assertLess(setup.index(operation), setup.index(f'KISS_BOOT_STAGE("{after}")'))
        self.assertNotIn("KISS_BOOT_STAGE", SOURCE.split("void loop() {", 1)[1])

    def test_esp32_mount_precedes_radio_and_failed_mount_cannot_generate_key(self):
        identity = SOURCE.split("void loadOrCreateIdentity() {", 1)[1].split(
            "void onSetRadio(", 1
        )[0]
        setup = SOURCE.split("void setup() {", 1)[1].split("void loop() {", 1)[0]
        self.assertNotIn("SPIFFS.begin", identity)
        self.assertEqual(SOURCE.count("SPIFFS.begin(true)"), 1)
        self.assertLess(setup.index("board.begin()"), setup.index("SPIFFS.begin(true)"))
        self.assertLess(setup.index("SPIFFS.begin(true)"), setup.index("while (!radio_init())"))
        mount = setup.split("#if defined(ESP32)", 1)[1].split("#endif", 1)[0]
        self.assertIn("const bool filesystem_ready = SPIFFS.begin(true);", mount)
        failure = mount.split("if (!filesystem_ready) {", 1)[1]
        self.assertIn("board.reboot();", failure)
        self.assertIn("halt();", failure)
        self.assertNotIn("loadOrCreateIdentity", failure)

    def test_identity_stages_never_export_identity_bytes(self):
        for line in SOURCE.splitlines():
            if "KISS_BOOT_STAGE(" in line and not line.lstrip().startswith("#define"):
                self.assertRegex(line.strip(), r'^KISS_BOOT_STAGE\("[a-z_.]+"\);$')
        before = SOURCE.index('KISS_BOOT_STAGE("filesystem.before")')
        after = SOURCE.index('KISS_BOOT_STAGE("filesystem.after")')
        self.assertLess(before, SOURCE.index("SPIFFS.begin(true)"))
        self.assertLess(SOURCE.index("SPIFFS.begin(true)"), after)


if __name__ == "__main__":
    unittest.main()
