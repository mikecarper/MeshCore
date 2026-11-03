"""Exercise authenticated owner-link deadlines and independent lost cause."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class OwnerPhoneLinkTest(unittest.TestCase):
    def test_owner_disconnect_state(self):
        compiler = shutil.which("g++") or shutil.which("c++")
        if not compiler:
            self.skipTest("C++ compiler unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "owner-phone-link"
            subprocess.run([
                compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                "-pedantic", "-I", str(ROOT / "src"),
                str(ROOT / "test/fixtures/owner_phone_link/test.cpp"),
                "-o", str(exe),
            ], check=True)
            subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    unittest.main()
