#!/usr/bin/env python3
"""Test the browser OTA physical-enable and session authorization policy."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BrowserOtaLeaseTest(unittest.TestCase):
    def compile_run(self, fixture):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "lease-test"
            subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                            "-I" + str(ROOT / "src"),
                            "-I" + str(ROOT / "test/fixtures/browser_ota_arduino"),
                            str(ROOT / "test/fixtures" / fixture),
                            "-o", str(output)], check=True)
            subprocess.run([str(output)], check=True)

    def test_lease(self):
        self.compile_run("browser_ota_lease.cpp")

    def test_stream_adapter(self):
        self.compile_run("browser_ota_stream.cpp")

    def test_correlated_seeder_transactions(self):
        self.compile_run("browser_ota_seeder_mailbox.cpp")

    def test_authorized_session_lifecycle(self):
        self.compile_run("browser_ota_session.cpp")


if __name__ == "__main__":
    unittest.main()
