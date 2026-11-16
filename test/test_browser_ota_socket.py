"""Run the actual on-device browser gateway with bounded mock sockets."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
WIFI = ROOT / "test/fixtures/serial_wifi_sessions"
HERE = ROOT / "test/fixtures/browser_ota_socket"


class BrowserOtaSocketTest(unittest.TestCase):
    def test_gateway_origin_discovery_and_frames(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("a host C++ compiler is required")
        with tempfile.TemporaryDirectory(prefix="meshcore-browser-ota-") as directory:
            binary = Path(directory) / "gateway.exe"
            command = [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-DESP32=1",
                       "-I" + str(HERE / "mocks"), "-I" + str(WIFI / "mocks"), "-I" + str(ROOT / "src"),
                       str(HERE / "test.cpp"), str(ROOT / "src/helpers/wifi/BrowserOtaSocket.cpp"),
                       str(ROOT / "src/helpers/wifi/SerialWifiInterface.cpp"), "-o", str(binary)]
            built = subprocess.run(command, capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("PASS:", result.stdout)


if __name__ == "__main__":
    unittest.main()
