"""Verify home presence only suppresses automatic lost causes."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class TrackerEscalationTest(unittest.TestCase):
    def test_causes_and_home(self):
        compiler = shutil.which("g++") or shutil.which("c++")
        if not compiler:
            self.skipTest("C++ compiler unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "tracker-escalation"
            subprocess.run([
                compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                "-pedantic", "-I", str(ROOT / "src"),
                str(ROOT / "test/fixtures/tracker_escalation/test.cpp"),
                "-o", str(exe),
            ], check=True)
            subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    unittest.main()
