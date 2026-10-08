#!/usr/bin/env python3
"""Verify streaming proofs and real OTA cold-buffer lifetimes without PlatformIO."""

from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from test_serial_wifi_sessions import SANITIZER_FLAGS

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/ota_cold_buffers/test.cpp"


class OtaColdBufferTests(unittest.TestCase):
    def test_proofs_allocation_failures_and_transfer_lifetimes(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("a host C++17 compiler is required")
        for platform in ((), ("-DNRF52_PLATFORM=1",),
                         ("-DNRF52_PLATFORM=1", "-DOTA_SD_STORE=1"),
                         ("-DESP32_PLATFORM=1",)):
            with self.subTest(platform=platform), tempfile.TemporaryDirectory(prefix="ota-cold-") as directory:
                binary = Path(directory) / "cold-buffers"
                sources = ("OtaManager.cpp", "OtaProtocol.cpp", "MotaContainer.cpp", "MerkleTree.cpp")
                built = subprocess.run([
                    compiler, "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
                    "-Wno-unused-parameter", "-Wno-sign-compare", *SANITIZER_FLAGS, *platform,
                    "-I", str(ROOT / "src"), "-I", str(ROOT / "test/mocks"),
                    str(FIXTURE), *[str(ROOT / "src/helpers/ota" / name) for name in sources],
                    str(ROOT / "src/Utils.cpp"), "-Wl,--wrap=malloc", "-Wl,--wrap=free",
                    "-o", str(binary),
                ], capture_output=True, text=True, timeout=60)
                self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
                self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                self.assertIn("PASS: streaming proofs and OTA cold-buffer lifetimes", checked.stdout)


if __name__ == "__main__":
    unittest.main()
