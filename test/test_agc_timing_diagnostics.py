#!/usr/bin/env python3
"""Exercise the lab AGC timing collector with a host-only C++11 fixture."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class AGCTimingDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            raise RuntimeError("AGC timing diagnostics tests require a host C++ compiler")
        cls.work = tempfile.TemporaryDirectory(prefix="meshcore-agc-timing-")
        cls.addClassCleanup(cls.work.cleanup)
        cls.binary = Path(cls.work.name) / ("timing.exe" if os.name == "nt" else "timing")
        flags = ["-std=c++11", "-O1", "-g", "-Wall", "-Wextra", "-Werror"]
        if sys.platform.startswith("linux"):
            flags += ["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                      "-fno-omit-frame-pointer", "-fno-pie", "-no-pie"]
        result = subprocess.run([
            compiler, *flags, "-I" + str(ROOT / "src"),
            str(ROOT / "test/fixtures/agc_timing_diagnostics/main.cpp"),
            "-o", str(cls.binary),
        ], capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def run_case(self, scenario):
        result = subprocess.run([str(self.binary), scenario], capture_output=True,
                                text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS: " + scenario, result.stdout)

    def test_periodic_scope_records_explicit_and_early_busy_skips_once(self):
        self.run_case("periodic")

    def test_shared_requests_remain_distinct_from_periodic_requests(self):
        self.run_case("shared")

    def test_admission_is_distinct_from_command_and_sequence_success(self):
        self.run_case("errors")

    def test_helper_and_reset_start_to_receive_durations_have_separate_extrema(self):
        self.run_case("timing")

    def test_failed_rearm_keeps_the_original_pending_interval(self):
        self.run_case("pending")

    def test_new_admission_supersedes_only_an_unfinished_receive_interval(self):
        self.run_case("superseded")

    def test_unsigned_clock_wrap_and_zero_and_maximum_samples(self):
        self.run_case("wrap")

    def test_all_cli_pages_keep_complete_maximum_values_within_157_characters(self):
        self.run_case("format")


if __name__ == "__main__":
    unittest.main()
