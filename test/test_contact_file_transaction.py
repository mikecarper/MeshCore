#!/usr/bin/env python3
"""Exercise the real transaction fingerprint without changing persisted CRCs."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/contact_fingerprint/test.cpp"


class ContactFingerprintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            raise unittest.SkipTest("host C++ compiler required")
        cls.work = tempfile.TemporaryDirectory(prefix="mesh-contact-fingerprint-")
        cls.binaries = {}
        source = (ROOT / "src/helpers/ContactFileTransaction.h").read_text(encoding="ascii")
        # Replay the old production accumulators as an exact-negative control.
        legacy = source
        for call in ("updateCRC32C(_crc,", "updateCRC32C(_verify_crc,"):
            if legacy.count(call) != 1:
                raise AssertionError("missing transaction fingerprint call: " + call)
            legacy = legacy.replace(call, call.replace("updateCRC32C", "storage::updateCRC32"))
        for platform in ("ESP32_PLATFORM", "RP2040_PLATFORM"):
            for old_ieee, header in ((False, source), (True, legacy)):
                work = Path(cls.work.name) / (platform + ("-ieee" if old_ieee else "-crc32c"))
                work.mkdir()
                header = header.replace('#include "IdentityStore.h"',
                                        '#include <helpers/IdentityStore.h>').replace(
                    '#include "PersistentStoreFormat.h"', '#include <helpers/PersistentStoreFormat.h>')
                (work / "transaction_under_test.h").write_text(header, encoding="ascii")
                binary = work / "fixture"
                result = subprocess.run([
                    compiler, "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                    "-Wno-sign-compare", "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                    "-fno-sanitize-recover=all", "-fno-pie", "-no-pie", "-D" + platform,
                    "-I", str(work), "-I", str(ROOT / "test/fixtures/radio_profiles/mocks"),
                    "-I", str(ROOT / "src"), str(FIXTURE), "-o", str(binary),
                ], capture_output=True, text=True, timeout=60)
                if result.returncode:
                    cls.work.cleanup()
                    raise AssertionError(result.stdout + result.stderr)
                cls.binaries[(platform, old_ieee)] = binary

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def scenario(self, mode, old_ieee=False, rejected=False):
        for platform in ("ESP32_PLATFORM", "RP2040_PLATFORM"):
            with self.subTest(platform=platform, old_ieee=old_ieee):
                result = subprocess.run([str(self.binaries[(platform, old_ieee)]), mode],
                                        capture_output=True, text=True, timeout=10)
                if rejected:
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertIn("progress == Progress::Failed", result.stderr)
                else:
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_normal_and_chunked_images_keep_publication_cadence(self):
        self.scenario("healthy")

    def test_same_sized_valid_fcs1_stage_is_rejected_before_publication(self):
        self.scenario("stale_fcs1")

    def test_same_sized_valid_dtx1_stage_is_rejected_before_publication(self):
        self.scenario("stale_dtx1")

    def test_known_prepublication_failures_preserve_the_original(self):
        self.scenario("failures")

    def test_old_ieee_control_still_accepts_healthy_images_and_rejects_known_faults(self):
        self.scenario("healthy", old_ieee=True)
        self.scenario("failures", old_ieee=True)

    def test_old_ieee_control_is_caught_by_fcs1_regression(self):
        self.scenario("stale_fcs1", old_ieee=True, rejected=True)

    def test_old_ieee_control_is_caught_by_dtx1_regression(self):
        self.scenario("stale_dtx1", old_ieee=True, rejected=True)


if __name__ == "__main__":
    unittest.main()
