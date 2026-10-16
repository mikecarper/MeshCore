#!/usr/bin/env python3
"""Run Companion and KISS output over the actual bounded HWCDC facade/SDK.

The USB registers and RTOS ring are synthetic. The frame parser/queue, KISS
encoder/output queue, HWCDC facade, and patched SDK write are production code.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest

import test_hwcdc_write_capacity as capacity
from test_hwcdc_tx_backport import body


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/hwcdc_role_transport.cpp"
SETUP_FIXTURE = ROOT / "test/fixtures/hwcdc_kiss_setup.cpp"


class HwcdcRoleTransportTest(unittest.TestCase):
    def harness(self, raw=False):
        source = capacity.HwcdcWriteCapacityTest().harness()
        source = source[:source.index("int main(int argc, char** argv)")]
        source = source.replace("#include <functional>",
                                "#include <functional>\n#include <deque>")
        source = source.replace("static size_t ring_free = 0;",
                                "static std::deque<uint8_t> host_input;\n"
                                "static size_t ring_free = 0;")
        source = source.replace("int available() override { return 0; }",
                                "int available() override { return host_input.size(); }")
        source = source.replace("int read() override { return -1; }", """
  int read() override {
    if (host_input.empty()) return -1;
    const int byte = host_input.front(); host_input.pop_front(); return byte;
  }""")
        source = source.replace("int peek() override { return -1; }",
                                "int peek() override { return host_input.empty() ? -1 : host_input.front(); }")

        # Compile the production output methods verbatim, with only their
        # unrelated radio/cryptography owners omitted from this host boundary.
        header = (ROOT / "examples/kiss_modem/KissModem.h").read_text()
        kiss = (ROOT / "examples/kiss_modem/KissModem.cpp").read_text()
        constants = "\n".join(line for line in header.splitlines()
                              if line.startswith("#define KISS_")
                              or line.startswith("#define HW_ERR_TX_BUSY")
                              or line.startswith("#define HW_RESP_ERROR"))
        fields = header[header.index("  uint8_t _tx_frame_buf["):
                        header.index("  static uint16_t appendEscapedByte")]
        # The output-only shell retains the exact queue fields and declarations
        # from the production class; its constructor just selects the stream.
        names = ("appendEscapedByte", "encodeFrame", "resetOutputQueue",
                 "popTxFrame", "tryFlushFrames", "queueFrame",
                 "queueHardwareFrame", "queuePendingBusyError")
        declarations = "\n".join(
            line for line in header.splitlines()
            if any(re.search(r"\b" + name + r"\(", line) for name in names))
        # body() starts at the supplied signature; retain each return type.
        methods = "\n".join(
            kiss[kiss.rfind("\n", 0, kiss.index("KissModem::" + name + "(")) + 1:
                 kiss.index("KissModem::" + name + "(")]
            + body(kiss, "KissModem::" + name + "(")
            for name in names)
        main = (ROOT / "examples/companion_radio/main.cpp").read_text()
        connection = body(main[main.index("  // The ESP32 USB-Serial-JTAG peripheral"):],
                          "usb_serial_interface.setConnectedCheck([]()") + ");"
        fixture = FIXTURE.read_text().replace("@KISS_CONSTANTS@", constants)
        fixture = fixture.replace("@KISS_FIELDS@", fields)
        fixture = fixture.replace("@KISS_DECLARATIONS@", declarations)
        fixture = fixture.replace("@KISS_METHODS@", methods)
        fixture = fixture.replace("@COMPANION_CONNECTION@", connection)
        lease_defaults = []
        for name in ("USB_FRAME_REPLY_GRACE_MS", "USB_CLIENT_IDLE_TIMEOUT",
                     "USB_HOST_LOSS_EDGE_MS", "USB_HOST_LOSS_GRACE_MS"):
            definition = re.search(r"^\s*#define " + name + r"\s+(.+)$",
                                   main, re.MULTILINE)
            self.assertIsNotNone(definition, name)
            lease_defaults.append("#define " + name + " " + definition[1])
        fixture = fixture.replace("@COMPANION_LEASE_DEFAULTS@", "\n".join(lease_defaults))
        fixture = fixture.replace("@ROLE_STREAM@", "Serial" if raw else "mesh::guarded")
        return source + fixture

    def compile(self, directory, raw=False):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a host C++17 compiler is required")
        source = Path(directory) / "transport.cpp"
        source.write_text(self.harness(raw), encoding="ascii")
        binary = Path(directory) / "transport.exe"
        sanitizers = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                       "-fno-pie", "-no-pie"] if os.name != "nt" else [])
        result = subprocess.run([
            compiler, "-std=c++17", "-Wall", "-Wextra", "-Wno-unused-parameter",
            "-pthread", *sanitizers, "-DESP32_PLATFORM", "-DESP32=1",
            "-DARDUINO_USB_MODE=1", "-DARDUINO_USB_CDC_ON_BOOT=1",
            "-I" + str(ROOT / "test/mocks"), "-I" + str(ROOT / "src"),
            str(source), str(ROOT / "src/helpers/ArduinoSerialInterface.cpp"),
            "-o", str(binary)
        ], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return binary

    def test_actual_hwcdc_companion_and_kiss_queues(self):
        with tempfile.TemporaryDirectory(prefix="hwcdc-role-transport-") as directory:
            binary = self.compile(directory)
            for case in ("companion_stall", "companion_short", "companion_stale",
                         "companion_session", "companion_activity_lease",
                         "kiss_stall", "kiss_escaped_max", "kiss_stale",
                         "kiss_short", "kiss_busy"):
                with self.subTest(case=case):
                    result = subprocess.run([str(binary), case], capture_output=True,
                                            text=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_raw_hwcdc_negative_control_waits_on_stale_capacity(self):
        with tempfile.TemporaryDirectory(prefix="hwcdc-raw-role-") as directory:
            binary = self.compile(directory, raw=True)
            for case in ("companion_stale", "kiss_stale"):
                with self.subTest(case=case):
                    result = subprocess.run([str(binary), case], capture_output=True,
                                            text=True, timeout=10)
                    self.assertNotEqual(result.returncode, 0)

    def test_actual_kiss_setup_selects_guard_and_prepares_before_driver(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a host C++17 compiler is required")
        main = (ROOT / "examples/kiss_modem/main.cpp").read_text()
        marker = "#if MESH_ESP32_HWCDC_SESSION_GUARD &&"
        start = main.index(marker)
        end = main.index("#endif", start) + len("#endif")
        setup = body(main, "void setup()")
        loop = body(main, "void loop()")
        source = SETUP_FIXTURE.read_text().replace("@TRANSPORT_SELECTION@", main[start:end])
        source = source.replace("@SETUP@", setup)
        state = "\n".join(re.findall(
            r"^#define (?:NOISE_FLOOR_CALIB_INTERVAL_MS|AGC_RESET_INTERVAL_MS).*|"
            r"^static uint32_t next_(?:noise_floor_calib|agc_reset)_ms.*", main, re.MULTILINE))
        reset = re.search(r"^static bool usb_host_reset_pending.*", main, re.MULTILINE)
        self.assertIsNotNone(reset)
        state += "\n#if KISS_HWCDC_TRANSPORT\n" + reset[0] + "\n#endif"
        source = source.replace("@LOOP_STATE@", state).replace("@LOOP@", loop)
        profiles = {
            "hwcdc": ["ARDUINO_USB_MODE=1", "ARDUINO_USB_CDC_ON_BOOT=1", "EXPECT_HWCDC=1"],
            "uart-hwcdc": ["ARDUINO_USB_MODE=1", "ARDUINO_USB_CDC_ON_BOOT=1",
                           "KISS_UART_RX=43", "KISS_UART_TX=44", "EXPECT_UART=1"],
            "tinyusb": ["ARDUINO_USB_MODE=0", "ARDUINO_USB_CDC_ON_BOOT=1"],
            "classic-uart": ["ARDUINO_USB_MODE=1", "ARDUINO_USB_CDC_ON_BOOT=0"],
            "g3-hwcdc": ["STATION_G3_ESP32=1", "ARDUINO_USB_MODE=1",
                         "ARDUINO_USB_CDC_ON_BOOT=1", "EXPECT_HWCDC=1"],
            "g3-uart-hwcdc": ["STATION_G3_ESP32=1", "ARDUINO_USB_MODE=1",
                              "ARDUINO_USB_CDC_ON_BOOT=1", "KISS_UART_RX=16",
                              "KISS_UART_TX=17", "EXPECT_UART=1"],
        }
        sanitizers = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                       "-fno-pie", "-no-pie"] if os.name != "nt" else [])
        with tempfile.TemporaryDirectory(prefix="hwcdc-kiss-setup-") as directory:
            cpp = Path(directory) / "setup.cpp"
            cpp.write_text(source, encoding="ascii")
            for name, flags in profiles.items():
                with self.subTest(profile=name):
                    binary = Path(directory) / name
                    compiled = subprocess.run([
                        compiler, "-std=c++17", *sanitizers, "-DESP32=1", "-DARDUINO=10800",
                        "-DESP32_PLATFORM", *["-D" + flag for flag in flags],
                        "-I" + str(ROOT / "test/mocks"), "-I" + str(ROOT / "src"),
                        str(cpp), "-o", str(binary)
                    ], capture_output=True, text=True, timeout=60)
                    self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
                    executed = subprocess.run([str(binary)], capture_output=True,
                                              text=True, timeout=10)
                    self.assertEqual(executed.returncode, 0, executed.stdout + executed.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
