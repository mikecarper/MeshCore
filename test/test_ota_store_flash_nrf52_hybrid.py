#!/usr/bin/env python3
"""Execute the real nRF52 flash/SRAM hybrid staging store on host mappings."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/ota_store_flash_nrf52_hybrid"


class OtaStoreFlashNrf52HybridTest(unittest.TestCase):
    def test_real_store_hybrid_lifecycle(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a host C++17 compiler is required")
        # Build outside the checkout. On Windows an application-control policy
        # may deny linker output or execution from newly-created repository
        # subdirectories, and a denied executable can leave that directory
        # locked during TemporaryDirectory cleanup.
        with tempfile.TemporaryDirectory(prefix="meshcore-ota-hybrid-") as directory:
            binary = Path(directory) / "ota-store-hybrid.exe"
            compiled = subprocess.run(
                [
                    compiler,
                    "-std=c++17",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-DNRF52_PLATFORM=1",
                    "-DOTA_FLASH_STORE=1",
                    "-DOTA_HYBRID_RAM_STORE=1",
                    "-DMOTA_NRF52_TEST_APP_BASE=0x00026000u",
                    "-DMOTA_NRF52_TEST_LAYOUT_STAGE_CEILING=0x000ED000u",
                    f"-I{FIXTURE / 'mocks'}",
                    f"-I{ROOT / 'src'}",
                    str(FIXTURE / "test_ota_store_flash_nrf52_hybrid.cpp"),
                    "-o",
                    str(binary),
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            checked = subprocess.run(
                [str(binary)], capture_output=True, text=True, timeout=10
            )
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            self.assertIn(
                "4 OtaStoreFlashNrf52 hybrid lifecycle checks passed",
                checked.stdout,
            )
            self.assertEqual(checked.stdout.count("PASS:"), 4)


if __name__ == "__main__":
    unittest.main()
