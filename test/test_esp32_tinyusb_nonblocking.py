#!/usr/bin/env python3
"""Compile the real USB facade against a stalled, 64-byte ESP32 CDC FIFO."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/esp32_tinyusb_nonblocking"


class Esp32TinyUsbNonblockingTest(unittest.TestCase):
    def test_native_fifo_and_other_role_fallback(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a host C++17 compiler is required")
        with tempfile.TemporaryDirectory(prefix=".tmp-usb-mode0-", dir=ROOT) as temporary:
            for mode, companion, cdc_on_boot in (
                (0, False, 1), (0, True, 1), (1, False, 0)
            ):
                with self.subTest(usb_mode=mode, companion=companion,
                                  cdc_on_boot=cdc_on_boot):
                    binary = Path(temporary) / f"usb-mode-{mode}-{companion}.exe"
                    command = [
                        compiler, "-std=c++17", "-DARDUINO=1", "-DESP32=1",
                        "-DESP32_PLATFORM=1", "-DMESH_DEBUG=1",
                        f"-DARDUINO_USB_CDC_ON_BOOT={cdc_on_boot}",
                        f"-DARDUINO_USB_MODE={mode}",
                        f"-I{FIXTURE / 'mocks'}", f"-I{ROOT / 'src'}",
                        str(FIXTURE / "test_esp32_tinyusb_nonblocking.cpp"),
                        str(ROOT / "src/helpers/UsbLogging.cpp"),
                        "-o", str(binary),
                    ]
                    if companion:
                        command.insert(1, "-DENABLE_USB_INTERFACE=1")
                    built = subprocess.run(command, capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                    self.assertIn("transport checks passed", checked.stdout)


if __name__ == "__main__":
    unittest.main()
