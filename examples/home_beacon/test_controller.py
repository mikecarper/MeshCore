"""Host-only tests for home beacon network/time advertising policy."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


class HomeBeaconControllerTest(unittest.TestCase):
    def compile_and_run(self, source):
        compiler = shutil.which("g++") or shutil.which("c++")
        if not compiler:
            self.skipTest("C++ compiler unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "home-beacon-controller"
            subprocess.run([
                compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                "-pedantic", "-I", str(ROOT / "src"),
                str(HERE / source), "-o", str(exe),
            ], check=True)
            subprocess.run([str(exe)], check=True)

    def test_network_and_clock(self):
        self.compile_and_run("test_controller.cpp")

    def test_serial_provisioning(self):
        self.compile_and_run("test_provisioning.cpp")


if __name__ == "__main__":
    unittest.main()
