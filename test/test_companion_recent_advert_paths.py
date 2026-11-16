"""Check the bounded recent-advert selection used by Companion displays."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RecentAdvertPathsTest(unittest.TestCase):
    def test_recent_selection(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("a host C++ compiler is required")
        with tempfile.TemporaryDirectory(prefix="meshcore-advert-paths-") as directory:
            binary = Path(directory) / "recent.exe"
            built = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                                    "-I" + str(ROOT / "examples/companion_radio"),
                                    str(ROOT / "test/fixtures/browser_ota_socket/recent_advert_paths.cpp"),
                                    "-o", str(binary)], capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
