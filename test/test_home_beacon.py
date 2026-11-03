"""Exercise authenticated home-beacon wire format and replay bounds."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class HomeBeaconTest(unittest.TestCase):
    def test_vectors_and_freshness(self):
        compiler = shutil.which("g++") or shutil.which("c++")
        if not compiler:
            self.skipTest("C++ compiler unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "home-beacon"
            subprocess.run([
                compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                "-pedantic", "-I", str(ROOT / "src"),
                str(ROOT / "test/fixtures/home_beacon/test.cpp"),
                "-lcrypto", "-o", str(exe),
            ], check=True)
            subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    unittest.main()
