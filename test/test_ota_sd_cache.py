#!/usr/bin/env python3
"""Exercise the production nRF52 SD archive with no card, flash or PlatformIO."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from test_serial_wifi_sessions import SANITIZER_FLAGS

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/ota_sd_cache"


class OtaSdCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            raise unittest.SkipTest("a host C++17 compiler is required")
        cls.directory = tempfile.TemporaryDirectory(prefix="ota-sd-cache-")
        cls.binary = Path(cls.directory.name) / "sd-cache"
        source_names = ("OtaCacheSdNrf52.cpp", "MotaContainer.cpp", "MerkleTree.cpp",
                        "OtaManager.cpp", "OtaProtocol.cpp")
        built = subprocess.run([
            compiler, "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-parameter", "-Wno-sign-compare", *SANITIZER_FLAGS,
            "-DNRF52_PLATFORM=1", "-DOTA_SD_STORE=1", "-DOTA_SD_CACHE_MAX=16",
            "-I", str(FIXTURE / "mocks"), "-I", str(ROOT / "src"),
            "-I", str(ROOT / "test/mocks"), str(FIXTURE / "test.cpp"),
            *[str(ROOT / "src/helpers/ota" / name) for name in source_names],
            str(ROOT / "src/Utils.cpp"), "-lcrypto", "-o", str(cls.binary),
        ], capture_output=True, text=True, timeout=60)
        if built.returncode:
            cls.directory.cleanup()
            raise AssertionError(built.stdout + built.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def run_case(self, scenario):
        result = subprocess.run([str(self.binary), str(scenario)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS: production SD archive boundary", result.stdout)

    def test_capture_publish_read_and_reboot_enumeration(self): self.run_case(0)
    def test_partial_is_hidden_and_reopens_after_reboot(self): self.run_case(1)
    def test_failed_rename_retains_resumable_partial(self): self.run_case(2)
    def test_file_device_and_post_publish_sync_failures(self): self.run_case(3)
    def test_missing_card_and_invalid_or_insufficient_free_space(self): self.run_case(4)
    def test_fragmented_allocation_fallback_and_cleanup(self): self.run_case(5)
    def test_corrupt_container_and_mid_are_never_advertised(self): self.run_case(6)
    def test_cache_off_survives_reboot_but_completed_files_still_seed(self): self.run_case(7)
    def test_malformed_final_is_quarantined_before_replacement(self): self.run_case(8)
    def test_real_managers_transfer_sd_image_and_verify_merkle_blocks(self): self.run_case(9)
    def test_full_index_refuses_seventeenth_without_losing_resumable_partial(self): self.run_case(10)


if __name__ == "__main__":
    unittest.main()
