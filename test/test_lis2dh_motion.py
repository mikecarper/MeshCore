"""Test the opt-in LIS2DH latch logic against a fault-injecting I2C bus."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Lis2dhMotionTest(unittest.TestCase):
    def test_motion_latch_and_fail_closed(self):
        compiler = shutil.which("g++") or shutil.which("c++")
        if not compiler:
            self.skipTest("C++ compiler unavailable")
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "motion_test"
            subprocess.run(
                [compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                 "-I", str(ROOT / "src"),
                 str(ROOT / "test/fixtures/tracker_motion/lis2dh_motion.cpp"),
                 "-o", str(output)], check=True)
            subprocess.run([str(output)], check=True)


if __name__ == "__main__":
    unittest.main()
