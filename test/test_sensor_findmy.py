"""Sensor Find My startup, durable settings, retry and suspend behavior."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SensorFindMyTest(unittest.TestCase):
    def test_lifecycle(self):
        compiler = shutil.which("g++")
        if not compiler:
            self.skipTest("g++ is unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "sensor_findmy"
            subprocess.run([
                compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                "-I", str(ROOT / "src"),
                str(ROOT / "test/fixtures/findmy_config/test_sensor_findmy.cpp"),
                "-o", str(output),
            ], check=True)
            subprocess.run([str(output)], check=True)


if __name__ == "__main__":
    unittest.main()
