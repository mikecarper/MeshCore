#!/usr/bin/env python3
"""Exercise the real AGC retention helpers against register loss and SPI faults."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest
from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/sx126x_agc_retention"


class SX126xAGCRetentionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix="sx126x-agc-retention-")
        cls.addClassCleanup(cls.folder.cleanup)
        folder = Path(cls.folder.name)
        source = (ROOT / "src/helpers/radiolib/CustomSX1262.h").read_text()
        signature = "int16_t setRxBoostedGainMode(bool enabled, bool persist = true)"
        method = body(source, signature)
        patch = body(source, "int16_t applyAgcLockupFix()")
        wrapper = (ROOT / "src/helpers/radiolib/CustomSX1262Wrapper.h").read_text()
        reset = body(wrapper, "void doResetAGC() override")
        main = ((FIXTURE / "main.cpp").read_text().replace("@GAIN_METHOD@", method)
                .replace("@PATCH_METHOD@", patch).replace("@RESET_METHOD@", reset))
        (folder / "main.cpp").write_text(main)
        cls.binary = folder / "test"
        result = subprocess.run([
            shutil.which("g++") or "g++", "-std=c++11", "-Wall", "-Wextra", "-Werror",
            "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-fno-pie", "-no-pie",
            "-I" + str(FIXTURE), "-I" + str(ROOT / "src"), str(folder / "main.cpp"),
            "-o", str(cls.binary)], capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def check(self, scenario):
        env = dict(os.environ, ASAN_OPTIONS="detect_leaks=1:abort_on_error=1",
                   UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1")
        result = subprocess.run([str(self.binary), scenario], env=env,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), "PASS " + scenario)

    def test_sleep_preserves_patch_gain_and_unrelated_entries(self): self.check("sleep")
    def test_real_gain_setter_preserves_retention_when_toggled(self): self.check("gain")
    def test_base_pointer_agc_reset_keeps_both_registers(self): self.check("reset")
    def test_full_malformed_and_insufficient_lists_never_overwrite_entries(self): self.check("capacity")
    def test_each_spi_failure_and_silent_write_failure_is_reported(self): self.check("failures")
    def test_patch_only_boards_do_not_enable_gain_retention(self): self.check("patch_only")
    def test_other_chip_resets_do_not_apply_sx1262_patch(self): self.check("other_chips")


if __name__ == "__main__":
    unittest.main()
