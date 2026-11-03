"""Exercise the Find My ESP32 backend against SDK-shaped host stubs."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test" / "fixtures" / "findmy_esp32"


class FindMyEsp32Tests(unittest.TestCase):
    def test_lifecycle_and_partial_teardown(self):
        compiler = shutil.which("g++")
        if not compiler:
            self.skipTest("g++ is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            shutil.copy(FIXTURE / "mock.h", temp / "mock.h")
            for name in ("sdkconfig.h", "Arduino.h", "esp_bt.h",
                         "esp_bt_main.h", "esp_gap_ble_api.h"):
                (temp / name).write_text('#include "mock.h"\n')
            executable = temp / "findmy_test"
            subprocess.run([
                compiler, "-std=c++11", "-DMESH_SENSOR_FINDMY=1",
                "-DESP32_PLATFORM", "-I", str(temp), "-I", str(ROOT / "src"),
                str(FIXTURE / "test.cpp"), "-o", str(executable),
            ], check=True)
            subprocess.run([str(executable)], check=True)


if __name__ == "__main__":
    unittest.main()
